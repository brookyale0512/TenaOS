"""Phase B: resolve a typed report worklist against CIEL with report tools."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from ..agent_prompts import apply_tool_description_overlay
from ..form_pipeline._llm_utils import (
    assistant_message_for_tool_calls,
    emit_thinking,
    extract_tool_calls,
    finish_reason_from_response,
    message_from_response,
)
from ..report_builder_tool_loop import REPORT_OPENAI_TOOLS
from .worklist import ReportWorklist

if TYPE_CHECKING:
    from ..config import Settings
    from ..report_builder_tool_loop import ReportBuilderToolLoop
    from ..report_drafts import ReportDraft, ReportDraftStore
    from ..llm_client import LlmClient

_LOGGER = logging.getLogger("tenaos.tena_agent.report_pipeline.ciel")

_REPEAT_SEARCH_HINT = (
    "You already searched this phrase in this run. Use the returned candidates now, "
    "or search a different synonym. Do not repeat the same query."
)


def run_ciel_resolution(
    *,
    store: "ReportDraftStore",
    loop: "ReportBuilderToolLoop",
    llm: "LlmClient",
    draft: "ReportDraft",
    request: str,
    worklist: ReportWorklist,
    settings: "Settings",
) -> list[str]:
    """Run the report tool ReAct loop. Returns distinct tool warning reasons."""
    from ..report_conversation import OP_MODEL_TOOL_CALL, OP_TOOL_RESULT

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _resolve_system()},
        {"role": "user", "content": _resolve_user(draft, request, worklist)},
    ]
    max_steps = int(getattr(settings, "report_agent_max_steps", 25))
    max_tokens = int(getattr(settings, "report_agent_tool_max_tokens", 900))
    search_cache: dict[str, Any] = {}
    warnings: list[str] = []
    truncation_retries = 0

    for step in range(max_steps):
        response = _call_tool_turn(llm, store, draft.draft_id, messages, max_tokens=max_tokens)
        message = message_from_response(response)
        content = str(message.get("content") or "").strip()
        tool_calls = extract_tool_calls(message)
        emit_thinking(store, draft.draft_id, content, phase="report_resolve")

        if not tool_calls:
            if finish_reason_from_response(response) == "length" and truncation_retries < 2:
                truncation_retries += 1
                max_tokens = min(max_tokens * 2, 1800)
                messages.append({"role": "user", "content": "Your prior turn was cut off. Continue with the next report tool call."})
                continue
            break

        messages.append(assistant_message_for_tool_calls(message, tool_calls))
        for call in tool_calls:
            arguments = dict(call.get("arguments") or {})
            arguments["draftId"] = draft.draft_id
            tool_name = str(call.get("name") or "")
            recovery_note = _recover_missing_arguments(tool_name, arguments, worklist, search_cache)
            store.append_event(
                draft.draft_id,
                actor="gemma",
                operation=OP_MODEL_TOOL_CALL,
                detail=f"Gemma called {tool_name}",
                payload={
                    "phase": "report_resolve",
                    "toolName": tool_name,
                    "arguments": arguments,
                    "argumentRecovery": recovery_note,
                    "toolCallId": call.get("id"),
                    "step": step,
                },
            )
            result = _execute_with_repeat_guard(loop, tool_name, arguments, search_cache)
            if tool_name == "update_report_draft" and isinstance(result, dict):
                for warning in result.get("warnings") or []:
                    reason = str((warning or {}).get("reason") or "").strip()
                    if reason and reason not in warnings:
                        warnings.append(reason)
            store.append_event(
                draft.draft_id,
                actor="middleware",
                operation=OP_TOOL_RESULT,
                detail=f"Tool result: {tool_name}",
                payload={
                    "toolName": tool_name,
                    "result": _compact_tool_result(result),
                    "toolCallId": call.get("id"),
                    "step": step,
                    "phase": "report_resolve",
                },
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or f"call_{step}_{tool_name}"),
                    "name": tool_name,
                    "content": json.dumps(_compact_tool_result(result), default=str),
                }
            )
    return warnings


def _resolve_system() -> str:
    return (
        "You are Gemma 4 running a CIEL-backed OpenMRS report-builder tool loop. "
        "Follow the typed plan exactly. First call get_report_draft. Set report type, date range, "
        "join mode, visualization, denominator/group_by. For each clinical filter, search CIEL, "
        "expand coded concepts when a value is needed, and add_filter with numeric CIEL ids only. "
        "For broad diagnosis requests like 'malaria diagnosis' or 'patients diagnosed with X', "
        "you MUST call search_related_ciel_concepts after the initial CIEL search to identify "
        "clinically related/narrower CIEL Diagnosis concepts. If the user's intent is broad, "
        "pass the relevant returned concepts together in "
        "add_filter as conceptIds so the filter is one logical OR over those CIEL concepts. "
        "Do not stop at the generic parent concept when search results include relevant subtype "
        "diagnoses with the same disease name, such as severe, uncomplicated, falciparum, cerebral, "
        "vivax, or complicated variants; include all clearly relevant non-retired Diagnosis-class "
        "CIEL concepts returned by the search. "
        "Coded filters need valueConceptId from the answers. Boolean/presence filters default true. "
        "Numeric filters need operator and numericThreshold. Build once, fix validation errors, then run once."
    )


def _resolve_user(draft: "ReportDraft", request: str, worklist: ReportWorklist) -> str:
    return (
        f"draftId: {draft.draft_id}\n"
        f"user request: {request}\n\n"
        f"=== typed report plan ===\n{worklist.to_prompt_block()}\n\n"
        f"=== current spec ===\n{json.dumps(draft.spec or {}, indent=2)}\n\n"
        "Use only report tools. Do not invent CIEL ids or result numbers."
    )


def _call_tool_turn(
    llm: "LlmClient",
    store: "ReportDraftStore",
    draft_id: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        response = llm.chat(
            messages,
            temperature=0.0,
            max_tokens=max_tokens,
            tools=apply_tool_description_overlay("report_builder", REPORT_OPENAI_TOOLS),
            tool_choice="auto",
        )
    except Exception as exc:
        _LOGGER.warning("Report resolution tool turn failed draft=%s: %s", draft_id, exc, exc_info=True)
        return {}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    message = message_from_response(response)
    store.append_event(
        draft_id,
        actor="gemma",
        operation="model_call",
        detail="Gemma model call: report_resolve",
        payload={
            "phase": "report_resolve",
            "elapsedMs": elapsed_ms,
            "finishReason": finish_reason_from_response(response),
            "toolCallCount": len(extract_tool_calls(message)),
            "contentChars": len(str(message.get("content") or "")),
        },
    )
    return response


def _execute_with_repeat_guard(
    loop: "ReportBuilderToolLoop",
    tool_name: str,
    arguments: dict[str, Any],
    search_cache: dict[str, Any],
) -> Any:
    if tool_name == "search_ciel_seeds":
        key = str(arguments.get("query") or "").strip().lower()
        if key and key in search_cache:
            cached = search_cache[key]
            return {**cached, "note": _REPEAT_SEARCH_HINT} if isinstance(cached, dict) else {"result": cached, "note": _REPEAT_SEARCH_HINT}
        result = _safe_execute(loop, tool_name, arguments)
        if key:
            search_cache[key] = result
        return result
    return _safe_execute(loop, tool_name, arguments)


def _recover_missing_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    worklist: ReportWorklist,
    search_cache: dict[str, Any],
) -> str | None:
    if tool_name != "search_ciel_seeds" or str(arguments.get("query") or "").strip():
        return None
    query = _next_unsearched_query(worklist, search_cache)
    if not query:
        return None
    arguments["query"] = query
    return f"filled missing query from typed report plan: {query}"


def _next_unsearched_query(worklist: ReportWorklist, search_cache: dict[str, Any]) -> str:
    seen = {str(key).lower() for key in search_cache}
    for item in worklist.filters:
        for phrase in item.search_phrases or [item.label]:
            if phrase and phrase.lower() not in seen:
                return phrase
    if worklist.denominator:
        for phrase in worklist.denominator.search_phrases:
            if phrase and phrase.lower() not in seen:
                return phrase
    return ""


def _safe_execute(loop: "ReportBuilderToolLoop", tool_name: str, arguments: dict[str, Any]) -> Any:
    try:
        return loop.execute_tool(tool_name, arguments)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _compact_tool_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    compact = dict(result)
    if "result" in compact and isinstance(compact["result"], dict):
        inner = dict(compact["result"])
        patients = inner.get("patients")
        if isinstance(patients, list) and len(patients) > 5:
            inner["patients"] = patients[:5]
            inner["patientsTruncated"] = True
        compact["result"] = inner
    if "concepts" in compact and isinstance(compact["concepts"], list) and len(compact["concepts"]) > 20:
        compact["concepts"] = compact["concepts"][:20]
    if "expansion" in compact and isinstance(compact["expansion"], dict):
        expansion = dict(compact["expansion"])
        for key in ("answers", "set_members", "setMembers"):
            value = expansion.get(key)
            if isinstance(value, list) and len(value) > 8:
                expansion[key] = value[:8]
        compact["expansion"] = expansion
    return compact


__all__ = ["run_ciel_resolution"]
