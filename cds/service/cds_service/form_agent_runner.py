"""Gemma 4 tool-calling agent loop for the form builder.

Extracted from form_conversation.py so the state machine (form_conversation)
and the agent execution engine (this module) are independently readable and
testable.

Public entry points:
  run_gemma_tool_agent(driver, draft, request, mode, settings) -> None
  ask_gemma_for_recovery_operations(driver, draft_id, request) -> list[dict]
  build_deterministic_summary(...) -> str
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal

from .agent_prompts import form_brainstorm_system, form_tool_system
from .form_builder_tool_loop import FORM_OPENAI_TOOLS
from .form_drafts import FormDraft, FormDraftStore

if TYPE_CHECKING:
    from .config import Settings
    from .form_builder_tool_loop import FormBuilderToolLoop
    from .vllm import VllmClient


# ---------------------------------------------------------------------------
# Public entry point


def run_gemma_tool_agent(
    *,
    store: FormDraftStore,
    loop: "FormBuilderToolLoop",
    vllm: "VllmClient",
    draft: FormDraft,
    request: str,
    mode: Literal["create", "edit"],
    settings: "Settings",
) -> None:
    """Run the full brainstorm → tool-loop → deterministic-summary cycle."""
    from .form_conversation import (
        OP_AGENT_REASONING,
        OP_FORM_EDIT_APPLIED,
        OP_FORM_PLAN_APPLIED,
        OP_MODEL_TOOL_CALL,
        OP_TOOL_RESULT,
        OP_CIEL_REVIEW,
    )

    brainstorm = _call_gemma_text(
        vllm=vllm,
        store=store,
        draft_id=draft.draft_id,
        system=form_brainstorm_system(),
        user_prompt=_brainstorm_user(draft, request, mode),
        max_tokens=settings.form_agent_brainstorm_max_tokens,
        temperature=0.3,
        phase="brainstorm",
    )
    if brainstorm:
        store.append_event(
            draft.draft_id,
            actor="gemma",
            operation=OP_AGENT_REASONING,
            detail="Gemma brainstormed the form strategy before calling tools.",
            payload={"phase": "brainstorm", "temperature": 0.3, "text": brainstorm, "mode": mode},
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": form_tool_system()},
        {"role": "user", "content": _tool_user(draft, request, mode, brainstorm)},
    ]

    max_steps = settings.form_agent_max_steps
    planned_question_count = _planned_question_count(brainstorm)
    target_min_fields = max(
        settings.form_agent_target_min_fields,
        planned_question_count if mode == "create" else 0,
    )
    max_nudges = settings.form_agent_max_nudges

    starting_field_count = _basket_field_count(draft)
    turn_commit_calls = 0
    turn_fields_added = 0
    turn_search_calls = 0
    small_basket_nudges = 0
    edit_commit_nudged = False
    text_only_strikes = 0
    turn_search_phrases: dict[str, int] = {}
    turn_warnings: list[str] = []

    for step in range(max_steps):
        response = _call_gemma_tool_turn(
            vllm=vllm,
            store=store,
            draft_id=draft.draft_id,
            messages=messages,
            max_tokens=settings.form_agent_tool_max_tokens,
            temperature=0.0,
            phase="tool_call",
        )
        message = response.get("choices", [{}])[0].get("message", {}) if response else {}
        tool_calls = _extract_tool_calls(message)
        content = str(message.get("content") or "").strip()

        if tool_calls:
            messages.append(_assistant_message_for_tool_calls(message, tool_calls))
            did_update_this_turn = False
            for call in tool_calls:
                arguments = dict(call.get("arguments") or {})
                arguments["draftId"] = draft.draft_id
                tool_name = str(call.get("name") or "")
                store.append_event(
                    draft.draft_id,
                    actor="gemma",
                    operation=OP_MODEL_TOOL_CALL,
                    detail=f"Gemma called {tool_name}",
                    payload={
                        "phase": "tool_call",
                        "temperature": 0.0,
                        "toolName": tool_name,
                        "arguments": arguments,
                        "toolCallId": call.get("id"),
                        "step": step,
                    },
                )

                if tool_name == "search_ciel_seeds":
                    query_key = str(arguments.get("query") or "").strip().lower()
                    if query_key and query_key in turn_search_phrases:
                        prior_hits = turn_search_phrases[query_key]
                        result: Any = {
                            "error": (
                                f"You already searched '{query_key}' this turn "
                                f"({prior_hits} hits). Pick a DIFFERENT refinement "
                                "strategy: synonym (e.g. otalgia -> earache), "
                                "generalize (e.g. ear pain -> pain), CIEL prefix "
                                "(weight -> patient weight), unit suffix "
                                "(weight -> weight kg), presence "
                                "(tinnitus -> tinnitus present), or history "
                                "(smoking -> history of smoking). Do not retry "
                                "the same phrase."
                            ),
                            "phrase": query_key,
                            "priorHits": prior_hits,
                        }
                        store.append_event(
                            draft.draft_id,
                            actor="middleware",
                            operation="search_ciel_seeds_repeated",
                            detail=f"Rejected repeat search '{query_key}'.",
                            payload={"query": query_key, "priorHits": prior_hits, "step": step},
                        )
                    else:
                        try:
                            result = loop.execute_tool(tool_name, arguments)
                        except Exception as exc:
                            result = {"error": f"{type(exc).__name__}: {exc}"}
                        if query_key:
                            hits = len((result or {}).get("seeds") or [])
                            turn_search_phrases[query_key] = hits
                else:
                    try:
                        result = loop.execute_tool(tool_name, arguments)
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"}

                if tool_name == "search_ciel_seeds":
                    turn_search_calls += 1
                if tool_name == "update_form_draft" and isinstance(result, dict):
                    did_update_this_turn = True
                    turn_commit_calls += 1
                    turn_fields_added += sum(
                        1 for applied in (result.get("applied") or [])
                        if applied.get("op") == "add_field"
                    )
                    for warning in result.get("warnings") or []:
                        reason = str(warning.get("reason") or "").strip()
                        if reason and reason not in turn_warnings:
                            turn_warnings.append(reason)

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
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or f"call_{step}_{tool_name}"),
                    "name": tool_name,
                    "content": json.dumps(compact_tool_result(result), default=str),
                })

            latest_after_tools = store.get_draft(draft.draft_id)
            field_count = _basket_field_count(latest_after_tools)
            if did_update_this_turn and mode == "create" and field_count == 0 and step < max_steps - 1:
                tried_phrases = sorted(turn_search_phrases.keys())[:14]
                messages.append({
                    "role": "user",
                    "content": (
                        "You created sections but did not add any fields. Do not finish with "
                        "an empty form. Continue the original plan and search unresolved "
                        "questions using exact label-like phrases before committing again. "
                        "For HIV/TB enrollment, use phrases such as date enrolled in HIV care, "
                        "date enrolled in tuberculosis (TB) care, tuberculosis (TB) care, "
                        "new patient identifier, study population type, general patient note, "
                        "tuberculosis treatment number, and treatment number. For vitals, use "
                        "weight, height, temperature, pulse, oxygen saturation, systolic blood "
                        "pressure, and diastolic blood pressure. "
                        f"Already-tried phrases: {tried_phrases}. "
                        "Only call update_form_draft again after you have at least one valid "
                        "field to add, then call build_form_schema."
                    ),
                })
            elif (
                mode == "create"
                and did_update_this_turn
                and 0 < field_count < target_min_fields
                and small_basket_nudges < max_nudges
                and step < max_steps - 1
            ):
                small_basket_nudges += 1
                tried_phrases = sorted(turn_search_phrases.keys())[:14]
                messages.append({
                    "role": "user",
                    "content": (
                        f"You committed only {field_count} field(s), below the target of "
                        f"{target_min_fields}. Continue the original plan before finishing. "
                        "For vitals, retry short canonical CIEL phrases: weight, height, "
                        "temperature, pulse, oxygen saturation, systolic blood pressure, "
                        "diastolic blood pressure. For HIV/TB enrollment, retry exact "
                        "label-like phrases: date enrolled in HIV care, date enrolled in "
                        "tuberculosis (TB) care, new patient identifier, study population "
                        "type, general patient note, tuberculosis treatment number. "
                        f"Do not repeat these already-tried phrases: {tried_phrases}. "
                        "Search unresolved planned questions, then send ONE additional "
                        "update_form_draft with only newly resolved fields and call "
                        "build_form_schema again."
                    ),
                })
            elif field_count == 0 and step in {18, 26}:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have searched CIEL several times but have not added any fields yet. "
                        "Stop searching and commit now: pick the usable candidates you have "
                        "already reviewed (Boolean, Coded with answers, or Question/Finding/Test/Obs "
                        "with Numeric/Text/Date/Datetime/Time/Document datatype), call "
                        "update_form_draft with one add_section per section followed by one "
                        "add_field per accepted concept (same call), then call build_form_schema."
                    ),
                })
            elif (
                mode == "edit"
                and not edit_commit_nudged
                and turn_commit_calls == 0
                and turn_search_calls >= 5
            ):
                edit_commit_nudged = True
                messages.append({
                    "role": "user",
                    "content": (
                        "You have searched CIEL several times but have not added any new fields. "
                        "Pick the most relevant usable candidates from the reviewed searches, call "
                        "update_form_draft NOW with add_field operations targeting an existing "
                        "section from the current basket (or add a new section first), then call "
                        "build_form_schema. Do not narrate between tool calls."
                    ),
                })
            continue

        if content:
            latest = store.get_draft(draft.draft_id)
            field_count = _basket_field_count(latest)
            if field_count == 0 and step < max_steps - 1:
                text_only_strikes += 1
                if text_only_strikes >= 2:
                    break
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "You returned text before adding any fields. Pick the best "
                        "CIEL concepts you already reviewed, call update_form_draft, "
                        "then call build_form_schema. If nothing reviewed is usable, "
                        "search one more plain clinical phrase and then return a "
                        "final summary."
                    ),
                })
                continue
            if (
                mode == "create"
                and 0 < field_count < target_min_fields
                and small_basket_nudges < max_nudges
                and step < max_steps - 1
            ):
                small_basket_nudges += 1
                tried_phrases = sorted(turn_search_phrases.keys())[:12]
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        f"You stopped at {field_count} field(s); the plan had more. "
                        "Resume the plan: pick the numbered questions you haven't committed yet, "
                        "generate FRESH search phrases for each (do not repeat phrases from this turn: "
                        f"{tried_phrases}). Use the refinement vocabulary (synonym, generalize, "
                        "CIEL prefix, unit suffix, presence, history). After 2-3 zero-hit retries "
                        "on a single question, drop it. Send ONE update_form_draft with all newly "
                        "resolved fields, call build_form_schema, return the final summary."
                    ),
                })
                continue
            break

        messages.append({
            "role": "user",
            "content": (
                "Continue. Either call the next tool or, if the draft is complete, "
                "return the final concise user-facing summary."
            ),
        })

    # Recovery: if basket is still empty after the loop, extract reviewed candidates
    # and ask Gemma to commit directly from that list.
    latest = store.get_draft(draft.draft_id)
    if _basket_field_count(latest) == 0:
        recovery = ask_gemma_for_recovery_operations(
            vllm=vllm, store=store, draft_id=draft.draft_id, request=request,
        )
        if recovery:
            result = loop.update_form_draft(draft.draft_id, recovery, actor="gemma")
            store.append_event(
                draft.draft_id,
                actor="gemma",
                operation=OP_MODEL_TOOL_CALL,
                detail="Gemma selected recovery basket operations from reviewed CIEL candidates",
                payload={"phase": "recovery_commit", "temperature": 0.0, "toolName": "update_form_draft", "arguments": {"draftId": draft.draft_id, "operations": recovery}},
            )
            store.append_event(
                draft.draft_id,
                actor="middleware",
                operation=OP_TOOL_RESULT,
                detail="Tool result: update_form_draft",
                payload={"toolName": "update_form_draft", "result": result, "phase": "recovery_commit"},
            )
            loop.build_form_schema(draft.draft_id)
            latest = store.get_draft(draft.draft_id)

    deterministic_recovery = _deterministic_recovery_operations(request, latest)
    if deterministic_recovery:
        result = loop.update_form_draft(draft.draft_id, deterministic_recovery, actor="middleware")
        store.append_event(
            draft.draft_id,
            actor="middleware",
            operation=OP_TOOL_RESULT,
            detail="Tool result: deterministic recovery update_form_draft",
            payload={"toolName": "update_form_draft", "result": result, "phase": "deterministic_recovery"},
        )
        loop.build_form_schema(draft.draft_id)
        latest = store.get_draft(draft.draft_id)

    if latest.last_schema is None and _basket_field_count(latest) > 0:
        loop.build_form_schema(draft.draft_id)
        latest = store.get_draft(draft.draft_id)

    store.update_draft(
        draft.draft_id,
        conversation_state="awaiting_question",
        conversation_context={"lastAgentMode": mode},
    )
    final_text = build_deterministic_summary(
        mode=mode,
        latest=latest,
        starting_field_count=starting_field_count,
        target_min_fields=target_min_fields,
        warnings=turn_warnings,
    )
    store.append_event(
        draft.draft_id,
        actor="gemma",
        operation=OP_FORM_PLAN_APPLIED if mode == "create" else OP_FORM_EDIT_APPLIED,
        detail=final_text,
        payload={"mode": mode, "finalText": final_text},
    )


# ---------------------------------------------------------------------------
# Recovery: Gemma picks from already-reviewed CIEL candidates


def ask_gemma_for_recovery_operations(
    *,
    vllm: "VllmClient",
    store: FormDraftStore,
    draft_id: str,
    request: str,
) -> list[dict[str, Any]]:
    from .form_conversation import OP_TOOL_RESULT

    events = store.list_events(draft_id, limit=200)
    reviewed: list[dict[str, Any]] = []
    for event in events:
        if event.operation != OP_TOOL_RESULT:
            continue
        result = event.payload.get("result") if isinstance(event.payload, dict) else None
        if not isinstance(result, dict):
            continue
        for seed in result.get("seeds") or []:
            if _seed_dict_is_usable(seed):
                reviewed.append(seed)
        expansion = result.get("expansion")
        if isinstance(expansion, dict):
            concept = expansion.get("concept") or {}
            seed = {
                "conceptId": concept.get("concept_id") or concept.get("id"),
                "displayName": concept.get("display_name"),
                "datatype": concept.get("datatype"),
                "conceptClass": concept.get("concept_class"),
                "answerCount": len(expansion.get("answers") or []),
                "setMemberCount": len(expansion.get("set_members") or []),
            }
            if _seed_dict_is_usable(seed):
                reviewed.append(seed)

    deduped: dict[str, dict[str, Any]] = {}
    for seed in reviewed:
        cid = str(seed.get("conceptId") or "")
        if cid:
            deduped.setdefault(cid, seed)
    if not deduped:
        return []

    parsed = _call_gemma_json(
        vllm=vllm,
        system=(
            "You must choose safe form basket operations from CIEL candidates already reviewed by the agent. "
            "Return only operations that use conceptId values from the candidate list. Do not invent concepts. "
            "Prefer Question, Finding, Test, or Obs class concepts; never include Drug or Diagnosis class items."
        ),
        user_prompt=(
            "Return ONLY JSON: {\"operations\":[{\"op\":\"add_section\",\"sectionId\":\"...\",\"label\":\"...\"},{\"op\":\"add_field\",\"sectionId\":\"...\",\"conceptId\":\"...\",\"label\":\"...\"}]}.\n"
            f"Original user request: {request}\n"
            f"Reviewed usable CIEL candidates ({len(deduped)} available): {list(deduped.values())[:18]}\n"
            "Build a clinically useful form with 6-10 high-confidence fields. "
            "Include exactly one add_section operation followed by one add_field operation per chosen concept. "
            "Choose concepts that together cover the intent of the user request (history, symptoms, vitals, "
            "labs as relevant). Do not stop at 2-3 fields when more usable candidates exist in the list."
        ),
        max_tokens=900,
    )
    if not isinstance(parsed, dict):
        return []
    operations = parsed.get("operations")
    if not isinstance(operations, list):
        return []
    allowed_ids = set(deduped)
    safe: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op_name = operation.get("op")
        if op_name == "add_field" and str(operation.get("conceptId") or "") not in allowed_ids:
            continue
        safe.append(operation)
    return safe[:20]


# ---------------------------------------------------------------------------
# Deterministic summary (no model intermediation for user-facing counts)


def build_deterministic_summary(
    *,
    mode: str,
    latest: FormDraft,
    starting_field_count: int,
    target_min_fields: int,
    warnings: list[str],
) -> str:
    ending_field_count = _basket_field_count(latest)
    question_count = _schema_question_count(latest.last_schema)
    net_added = ending_field_count - starting_field_count
    parts: list[str] = []

    if mode == "edit":
        if net_added > 0:
            parts.append(f"I added {net_added} new question{'' if net_added == 1 else 's'} to the draft.")
        elif net_added < 0:
            parts.append(f"I removed {-net_added} question{'' if net_added == -1 else 's'} from the draft.")
        else:
            parts.append("I searched but could not safely add any new questions to the existing draft.")
    else:
        if ending_field_count == 0:
            parts.append(
                "I could not build a safe CIEL-backed draft from that request. "
                "Try a narrower clinical form request."
            )
        elif question_count < target_min_fields:
            parts.append(
                f"I built a CIEL-backed draft with {ending_field_count} question"
                f"{'' if ending_field_count == 1 else 's'}. Reply with 'add more questions' "
                "to extend it, or publish as-is if it's enough."
            )
        else:
            parts.append(
                f"I built a CIEL-backed draft with {ending_field_count} question"
                f"{'' if ending_field_count == 1 else 's'}. Review the preview and basket "
                "before publishing."
            )

    for reason in warnings[:3]:
        parts.append(f"Note: {reason}")

    parts.append(_summarize_basket_for_user(latest))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Shared helpers (also re-exported for test compatibility)


def compact_tool_result(result: Any) -> Any:
    """Trim noise + UUID-shaped identifiers from tool results fed back to Gemma."""
    if not isinstance(result, dict):
        return result
    compact = dict(result)
    if isinstance(compact.get("schema"), dict):
        schema = compact["schema"]
        compact["schema"] = {
            "name": schema.get("name"),
            "encounterType": schema.get("encounterType"),
            "questionCount": _schema_question_count(schema),
        }
    if isinstance(compact.get("basket"), dict):
        compact["basketSummary"] = [
            {
                "sectionId": section.get("sectionId"),
                "label": section.get("label"),
                "fields": [
                    {
                        "conceptId": str(field.get("conceptId") or ""),
                        "label": field.get("labelOverride") or field.get("conceptId"),
                        "required": bool(field.get("required")),
                    }
                    for field in (section.get("fields") or [])
                ],
            }
            for section in compact["basket"].get("sections", [])
        ]
        compact.pop("basket", None)
    compact.pop("lastSchema", None)
    compact.pop("lastValidation", None)
    if compact.get("draftId") and "name" in compact and "encounterTypeUuid" in compact:
        compact = {
            "draftId": compact.get("draftId"),
            "name": compact.get("name"),
            "encounterTypeUuid": compact.get("encounterTypeUuid"),
            "status": compact.get("status"),
            "basketSummary": compact.get("basketSummary") or [],
        }
    return compact


# ---------------------------------------------------------------------------
# Private helpers


def _basket_field_count(draft: FormDraft) -> int:
    return sum(len(s.get("fields") or []) for s in (draft.basket.get("sections") or []))


def _schema_question_count(schema: dict[str, Any] | None) -> int:
    if not schema:
        return 0
    return sum(
        len(section.get("questions") or [])
        for page in schema.get("pages", []) or []
        for section in page.get("sections", []) or []
    )


def _summarize_basket_for_user(draft: FormDraft) -> str:
    sections = draft.basket.get("sections") or []
    field_count = sum(len(s.get("fields") or []) for s in sections)
    section_count = sum(1 for s in sections if (s.get("fields") or []))
    section_labels: list[str] = []
    for section in sections:
        if not (section.get("fields") or []):
            continue
        raw_label = str(section.get("label") or "").strip()
        section_id = str(section.get("sectionId") or "").strip()
        if raw_label and raw_label != section_id:
            section_labels.append(raw_label)
        else:
            section_labels.append(_humanize_section_label(section_id))
    if section_count == 0:
        return f"Current form: {field_count} question{'' if field_count == 1 else 's'}."
    section_list = ", ".join(label for label in section_labels if label) or "—"
    return (
        f"Current form: {field_count} question{'' if field_count == 1 else 's'} across "
        f"{section_count} section{'' if section_count == 1 else 's'} ({section_list})."
    )


def _humanize_section_label(value: str) -> str:
    if not value:
        return ""
    parts = [p for p in re.split(r"[_\-]+|\s+", str(value).strip()) if p]
    if not parts:
        return ""
    return " ".join(p.capitalize() if p.islower() else p for p in parts)


def _seed_dict_is_usable(seed: dict[str, Any]) -> bool:
    datatype = seed.get("datatype") or ""
    cls = str(seed.get("conceptClass") or "").lower()
    if seed.get("retired"):
        return False
    if int(seed.get("setMemberCount") or 0) > 0 or cls in {"convset", "labset", "medset"}:
        return False
    if datatype == "Boolean":
        return True
    if datatype == "Coded":
        return int(seed.get("answerCount") or 0) > 0
    if datatype in {"Numeric", "Text", "Date", "Datetime", "Time", "Document"}:
        return cls not in {"diagnosis", "drug"}
    return False


def _planned_question_count(brainstorm: str) -> int:
    """Count numbered question intents in Gemma's brainstorm plan."""
    if not brainstorm:
        return 0
    return len(re.findall(r"(?m)^\s*\d+\.\s+", brainstorm))


def _deterministic_recovery_operations(request: str, draft: FormDraft) -> list[dict[str, Any]]:
    """Fill high-confidence common clinic fields the model failed to commit."""
    request_lower = request.lower()
    existing = {
        str(field.get("conceptId") or "")
        for section in (draft.basket.get("sections") or [])
        for field in (section.get("fields") or [])
    }
    operations: list[dict[str, Any]] = []

    def target_section(section_id: str, label: str) -> str:
        sections = draft.basket.get("sections") or []
        if sections:
            return str(sections[0].get("sectionId") or section_id)
        operations.append({"op": "add_section", "sectionId": section_id, "label": label})
        return section_id

    def add_missing(section_id: str, concept_id: str, label: str) -> None:
        if concept_id in existing:
            return
        existing.add(concept_id)
        operations.append({"op": "add_field", "sectionId": section_id, "conceptId": concept_id, "label": label})

    if "vital" in request_lower or "triage" in request_lower:
        section_id = target_section("vital_signs", "Vital Signs")
        if "blood pressure" in request_lower or "systolic" in request_lower:
            add_missing(section_id, "5085", "Systolic blood pressure")
        if "blood pressure" in request_lower or "diastolic" in request_lower:
            add_missing(section_id, "5086", "Diastolic blood pressure")
        if "pulse" in request_lower or "heart rate" in request_lower:
            add_missing(section_id, "5087", "Pulse rate")
        if "temperature" in request_lower or "temp" in request_lower:
            add_missing(section_id, "5088", "Temperature")
        if "weight" in request_lower:
            add_missing(section_id, "5089", "Weight")
        if "height" in request_lower:
            add_missing(section_id, "5090", "Height")
        if "oxygen saturation" in request_lower or "spo2" in request_lower or "pulse oximeter" in request_lower:
            add_missing(section_id, "5092", "Oxygen saturation")

    if "hiv care" in request_lower and "enroll" in request_lower:
        section_id = target_section("hiv_care_enrollment", "HIV Care Enrollment")
        if "date" in request_lower or "enroll" in request_lower:
            add_missing(section_id, "160555", "Date enrolled in HIV care")
        if "identifier" in request_lower or "unique" in request_lower or "service id" in request_lower:
            add_missing(section_id, "162576", "Unique service identifier")
        if "population" in request_lower or "category" in request_lower:
            add_missing(section_id, "166432", "Population category")
        if "note" in request_lower:
            add_missing(section_id, "165095", "General patient notes")

    if ("tb care" in request_lower or "tuberculosis" in request_lower) and "enroll" in request_lower:
        section_id = target_section("tb_care_enrollment", "TB Care Enrollment")
        if "program" in request_lower or "type" in request_lower:
            add_missing(section_id, "164411", "Category of tuberculosis patient")
        if "date" in request_lower or "enroll" in request_lower:
            add_missing(section_id, "161552", "Date enrolled in tuberculosis care")
        if "treatment number" in request_lower or "ds tb" in request_lower:
            add_missing(section_id, "161654", "DS TB treatment number")

    return operations


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    native = message.get("tool_calls")
    calls: list[dict[str, Any]] = []
    if isinstance(native, list):
        for index, call in enumerate(native):
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                args = {}
            calls.append({
                "id": call.get("id") or f"call_{index}",
                "name": function.get("name") or call.get("name"),
                "arguments": args,
            })
    content = str(message.get("content") or "")
    for index, match in enumerate(re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.DOTALL)):
        try:
            payload = json.loads(match.group(1))
        except Exception:
            continue
        name = payload.get("name") or payload.get("tool") or payload.get("function")
        args = payload.get("arguments") or payload.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        calls.append({"id": f"text_tool_{index}", "name": name, "arguments": args})
    return [c for c in calls if c.get("name")]


def _assistant_message_for_tool_calls(
    message: dict[str, Any], tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    if message.get("tool_calls"):
        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": message["tool_calls"],
        }
    return {
        "role": "assistant",
        "content": "\n".join(
            f"<tool_call>{json.dumps({'name': c.get('name'), 'arguments': c.get('arguments')})}</tool_call>"
            for c in tool_calls
        ),
    }


def _parse_json_object(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Model call helpers


def _call_gemma_text(
    *,
    vllm: "VllmClient",
    store: FormDraftStore,
    draft_id: str,
    system: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    phase: str,
) -> str:
    try:
        response = vllm.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception:
        return ""
    store.append_event(
        draft_id,
        actor="gemma",
        operation="model_call",
        detail=f"Gemma model call: {phase}",
        payload={"phase": phase, "temperature": temperature, "maxTokens": max_tokens},
    )
    return str(response.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()


def _call_gemma_tool_turn(
    *,
    vllm: "VllmClient",
    store: FormDraftStore,
    draft_id: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    phase: str,
) -> dict[str, Any]:
    response = vllm.chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=FORM_OPENAI_TOOLS,
        tool_choice="auto",
    )
    store.append_event(
        draft_id,
        actor="gemma",
        operation="model_call",
        detail=f"Gemma model call: {phase}",
        payload={
            "phase": phase,
            "temperature": temperature,
            "maxTokens": max_tokens,
            "tools": [t["function"]["name"] for t in FORM_OPENAI_TOOLS],
        },
    )
    return response


def _call_gemma_json(
    *,
    vllm: "VllmClient",
    system: str,
    user_prompt: str,
    max_tokens: int,
) -> dict[str, Any] | None:
    """One-shot JSON call; returns parsed dict or None.

    Returns None for two distinct failure modes:
      - model unreachable / timeout → vllm.chat raises
      - model returned non-JSON or empty → parse fails
    Callers must provide a deterministic fallback for both.
    """
    try:
        response = vllm.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
        )
    except Exception:
        return None
    content = str(response.get("choices", [{}])[0].get("message", {}).get("content", "") or "")
    return _parse_json_object(content)


# ---------------------------------------------------------------------------
# Prompt builders (user-side, dynamic)


def _brainstorm_user(draft: FormDraft, request: str, mode: str) -> str:
    if mode == "edit":
        return (
            f"Mode: edit\n"
            f"Draft name: {draft.name}\n"
            f"User request: {request}\n"
            f"Current basket (already in the form, do NOT repeat these):\n"
            f"{_basket_summary_for_model(draft)}\n\n"
            "Plan only the NEW questions to add (or the changes to make) in response "
            "to the user's edit request. Do not include questions that are already in "
            "the current basket above. Emit the plan in the same structured format "
            "as the system prompt (Plan:, Section:, numbered entries with search "
            "phrases and datatype). If the user asked to remove or mark a question, "
            "name the existing question label exactly and prefix it with 'Remove:' "
            "or 'Require:' / 'Optional:' instead of 'Section:'."
        )
    return (
        f"Mode: create\n"
        f"Draft name: {draft.name}\n"
        f"User request: {request}\n"
        f"Current basket:\n{_basket_summary_for_model(draft)}\n\n"
        "Plan the form now. Emit the structured plan in the exact format from the system prompt."
    )


def _tool_user(draft: FormDraft, request: str, mode: str, brainstorm: str) -> str:
    return (
        f"draftId: {draft.draft_id}\n"
        f"mode: {mode}\n"
        f"user request: {request}\n\n"
        f"=== plan (follow this in order) ===\n{brainstorm or '(none)'}\n\n"
        f"=== current basket ===\n{_basket_summary_for_model(draft)}\n\n"
        "Now execute the algorithm from the system prompt:\n"
        "  1. Call get_form_draft once.\n"
        "  2. For each numbered question in the plan above, run the per-question\n"
        "     search-and-refine sub-loop. Use the candidate search phrases the plan\n"
        "     suggests; if they miss, refine with the vocabulary (synonym /\n"
        "     generalize / CIEL prefix / unit / presence / history) up to 4 times\n"
        "     per question.\n"
        "  3. After all questions are resolved (or dropped), send ONE\n"
        "     update_form_draft with all the add_section + add_field operations.\n"
        "  4. Call build_form_schema.\n"
        "  5. If the basket has fewer than 5 fields, retry dropped questions with\n"
        "     new refinement strategies, then commit again.\n"
        "  6. Return the final summary."
    )


def _basket_summary_for_model(draft: FormDraft) -> str:
    lines: list[str] = []
    for section in draft.basket.get("sections") or []:
        lines.append(f"Section: {section.get('label') or section.get('sectionId')}")
        for f in section.get("fields") or []:
            label = f.get("labelOverride") or f.get("conceptId")
            required = "required" if f.get("required") else "optional"
            lines.append(f"- {label} ({required})")
    return "\n".join(lines) if lines else "No questions yet."
