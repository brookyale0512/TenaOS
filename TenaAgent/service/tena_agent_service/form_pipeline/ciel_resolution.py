"""Phase B: resolve each worklist item to a CIEL concept and commit the basket.

This is the model-driven search/refine loop. It is deliberately thin: the model
works the worklist in order, searches CIEL, inspects candidates, and commits via
``update_form_draft``; the deterministic apply-time safety in
``FormBuilderToolLoop._apply_add_field`` (usability, QA-token rejection, label
mismatch, dedup, auto-seed) remains the only safety boundary and is reused
unchanged.

What this loop does NOT contain (by design, vs. the legacy runner):
- No hardcoded HIV/TB/vitals search phrases injected as nudges.
- No hardcoded CIEL concept-id recovery tables.
- No magic per-step thresholds.

The only control-flow guards are generic and domain-agnostic: reject a verbatim
repeated search within the run, and recover from a truncated tool turn.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from ..agent_prompts import apply_tool_description_overlay, load_prompt
from ..form_agent_runner import compact_tool_result
from ..form_builder_tool_loop import FORM_OPENAI_TOOLS
from ._llm_utils import (
    assistant_message_for_tool_calls,
    emit_thinking,
    extract_tool_calls,
    finish_reason_from_response,
    message_from_response,
)
from .worklist import QuestionWorklist

if TYPE_CHECKING:
    from ..config import Settings
    from ..form_builder_tool_loop import FormBuilderToolLoop
    from ..form_drafts import FormDraft, FormDraftStore
    from ..llm_client import LlmClient

_LOGGER = logging.getLogger("tenaos.tena_agent.form_pipeline.ciel")

# Resolution may search/expand/read/update/build but never publishes.
_RESOLVE_TOOLS = [
    tool for tool in FORM_OPENAI_TOOLS if (tool.get("function") or {}).get("name") != "publish_form"
]

_REPEAT_SEARCH_HINT = (
    "You already ran this exact search this run; these are the same candidates. "
    "Pick a usable conceptId from them now, or refine with a DIFFERENT phrase "
    "(synonym, more general term, unit suffix, or presence/history phrasing). "
    "Do not repeat the identical phrase again."
)


def _resolve_system() -> str:
    return load_prompt("form_resolve_system.txt")


def run_ciel_resolution(
    *,
    store: "FormDraftStore",
    loop: "FormBuilderToolLoop",
    llm: "LlmClient",
    draft: "FormDraft",
    request: str,
    worklist: QuestionWorklist,
    settings: "Settings",
) -> None:
    """Run the CIEL search/commit loop until the model finishes or steps run out."""
    from ..form_conversation import OP_CIEL_REVIEW, OP_MODEL_TOOL_CALL, OP_TOOL_RESULT

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _resolve_system()},
        {"role": "user", "content": _resolve_user(draft, request, worklist)},
    ]
    max_steps = int(getattr(settings, "form_agent_max_steps", 35))
    max_tokens = int(getattr(settings, "form_agent_resolve_max_tokens", 1300))
    search_cache: dict[str, Any] = {}
    truncation_retries = 0

    for step in range(max_steps):
        response = _call_tool_turn(llm, store, draft.draft_id, messages, max_tokens=max_tokens)
        message = message_from_response(response)
        tool_calls = extract_tool_calls(message)
        content = str(message.get("content") or "").strip()

        # Surface the model's pre-action reasoning as a visible step.
        emit_thinking(store, draft.draft_id, content, phase="resolve")

        if not tool_calls:
            # A truncated text turn is not a real "final answer"; nudge once.
            if (
                finish_reason_from_response(response) == "length"
                and truncation_retries < 2
                and step < max_steps - 1
            ):
                truncation_retries += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous turn was cut off. Continue with the next single "
                            "tool call (search, update_form_draft, or build_form_schema)."
                        ),
                    }
                )
                continue
            # Genuine text turn -> the model considers itself done.
            break

        messages.append(assistant_message_for_tool_calls(message, tool_calls))
        committed_this_turn = False
        abort_resolution = False
        for call in tool_calls:
            arguments = dict(call.get("arguments") or {})
            arguments["draftId"] = draft.draft_id
            tool_name = str(call.get("name") or "")
            recovery_note = _recover_missing_arguments(tool_name, arguments, worklist, search_cache)
            if tool_name in {"update_form_draft", "build_form_schema"}:
                committed_this_turn = True
            store.append_event(
                draft.draft_id,
                actor="gemma",
                operation=OP_MODEL_TOOL_CALL,
                detail=f"Gemma called {tool_name}",
                payload={
                    "phase": "resolve",
                    "toolName": tool_name,
                    "arguments": arguments,
                    "argumentRecovery": recovery_note,
                    "toolCallId": call.get("id"),
                    "step": step,
                },
            )

            if tool_name == "search_ciel_seeds" and not str(arguments.get("query") or "").strip():
                result = {
                    "error": (
                        "Missing required query and no unused primary worklist search phrase remains; "
                        "ending resolution so coverage repair can use reviewed candidates."
                    )
                }
                abort_resolution = True
            else:
                result = _execute_with_repeat_guard(
                    loop, store, draft.draft_id, tool_name, arguments, search_cache, step
                )

            store.append_event(
                draft.draft_id,
                actor="middleware",
                operation=OP_TOOL_RESULT,
                detail=f"Tool result: {tool_name}",
                payload={"toolName": tool_name, "result": result, "toolCallId": call.get("id"), "step": step},
            )
            if tool_name in {"search_ciel_seeds", "expand_ciel_concept"}:
                store.append_event(
                    draft.draft_id,
                    actor="gemma",
                    operation=OP_CIEL_REVIEW,
                    detail=f"Gemma reviewed CIEL result for {tool_name}",
                    payload={"toolName": tool_name, "arguments": arguments, "result": result, "step": step},
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or f"call_{step}_{tool_name}"),
                    "name": tool_name,
                    "content": json.dumps(compact_tool_result(result), default=str),
                }
            )
            if abort_resolution:
                store.append_event(
                    draft.draft_id,
                    actor="middleware",
                    operation="resolution_unrecoverable_tool_args",
                    detail="Stopped resolution after unrecoverable missing query.",
                    payload={"toolName": tool_name, "step": step},
                )
                break

        if abort_resolution:
            break

        if (
            not committed_this_turn
            and worklist.items
            and _searched_all_primary_worklist_queries(worklist, search_cache)
        ):
            store.append_event(
                draft.draft_id,
                actor="middleware",
                operation="resolution_primary_searches_exhausted",
                detail=(
                    "All primary worklist CIEL searches have completed without a draft commit; "
                    "ending resolution loop so coverage repair can use reviewed candidates."
                ),
                payload={"searchedQueries": sorted(search_cache)},
            )
            break


def _execute_with_repeat_guard(
    loop: "FormBuilderToolLoop",
    store: "FormDraftStore",
    draft_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    search_cache: dict[str, Any],
    step: int,
) -> Any:
    """Execute one tool call, short-circuiting verbatim repeated CIEL searches.

    On a repeat we DO NOT just reject: we return the previously fetched
    candidates again (with a note) so the model can pick a concept and move on
    instead of looping. This keeps the guard generic while cutting the wasted
    search/refine cycles that show up as "Rejected repeat search". All concept
    validation stays in the tool loop.
    """
    if tool_name == "search_ciel_seeds":
        query_key = str(arguments.get("query") or "").strip().lower()
        if query_key and query_key in search_cache:
            store.append_event(
                draft_id,
                actor="middleware",
                operation="search_ciel_seeds_repeated",
                detail=f"Returned cached candidates for repeat search '{query_key}'.",
                payload={"query": query_key, "step": step},
            )
            cached = search_cache[query_key]
            if isinstance(cached, dict):
                return {**cached, "note": _REPEAT_SEARCH_HINT}
            return {"results": cached, "note": _REPEAT_SEARCH_HINT}
        result = _safe_execute(loop, tool_name, arguments)
        if query_key:
            search_cache[query_key] = result
        return result
    return _safe_execute(loop, tool_name, arguments)


def _recover_missing_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    worklist: QuestionWorklist,
    search_cache: dict[str, Any],
) -> str | None:
    """Recover generic, required tool args from the typed worklist when possible.

    Some llama.cpp/Gemma combinations emit the right tool name but omit the
    required ``query`` field. The worklist is already the source of truth for
    CIEL search phrases, so using its next unsearched phrase is safer than
    feeding the model repeated validation errors until max_steps is exhausted.
    """
    if tool_name != "search_ciel_seeds":
        return None
    if str(arguments.get("query") or "").strip():
        return None
    query = _next_worklist_query(worklist, search_cache)
    if not query:
        return None
    arguments["query"] = query
    return f"filled missing query from worklist search phrase: {query}"


def _next_worklist_query(worklist: QuestionWorklist, search_cache: dict[str, Any]) -> str:
    seen = {str(key).strip().lower() for key in search_cache}
    for item in worklist.items:
        query = str((item.search_phrases or [item.label])[0] or "").strip()
        if query and query.lower() not in seen:
            return query
    return ""


def _searched_all_primary_worklist_queries(worklist: QuestionWorklist, search_cache: dict[str, Any]) -> bool:
    required = {
        str((item.search_phrases or [item.label])[0] or "").strip().lower()
        for item in worklist.items
    }
    required.discard("")
    if not required:
        return False
    seen = {str(key).strip().lower() for key in search_cache}
    return required.issubset(seen)


def _safe_execute(loop: "FormBuilderToolLoop", tool_name: str, arguments: dict[str, Any]) -> Any:
    try:
        return loop.execute_tool(tool_name, arguments)
    except Exception as exc:  # surface tool errors to the model instead of crashing
        return {"error": f"{type(exc).__name__}: {exc}"}


def _call_tool_turn(
    llm: "LlmClient",
    store: "FormDraftStore",
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
            tools=apply_tool_description_overlay("form_builder", _RESOLVE_TOOLS),
            tool_choice="auto",
        )
    except Exception as exc:
        _LOGGER.warning("Resolution tool turn failed draft=%s: %s", draft_id, exc)
        return {}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    message = message_from_response(response)
    store.append_event(
        draft_id,
        actor="gemma",
        operation="model_call",
        detail="Gemma model call: resolve",
        payload={
            "phase": "resolve",
            "elapsedMs": elapsed_ms,
            "finishReason": finish_reason_from_response(response),
            "toolCallCount": len(extract_tool_calls(message)),
            "contentChars": len(str(message.get("content") or "")),
        },
    )
    return response


def _resolve_user(draft: "FormDraft", request: str, worklist: QuestionWorklist) -> str:
    return (
        f"draftId: {draft.draft_id}\n"
        f"user request: {request}\n\n"
        f"=== plan (resolve each question in order) ===\n{worklist.to_prompt_block()}\n\n"
        f"=== current basket ===\n{_basket_summary(draft)}\n\n"
        "Resolve each planned question against CIEL, then send ONE update_form_draft "
        "with all add_section + add_field operations and call build_form_schema. "
        "When the basket reflects the plan, return a short final summary."
    )


def _basket_summary(draft: "FormDraft") -> str:
    lines: list[str] = []
    for section in draft.basket.get("sections") or []:
        lines.append(f"Section: {section.get('label') or section.get('sectionId')}")
        for field in section.get("fields") or []:
            label = field.get("labelOverride") or field.get("conceptId")
            lines.append(f"- {label}")
    return "\n".join(lines) if lines else "No questions yet."
