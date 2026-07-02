"""Single bounded coverage-repair pass.

Replaces the legacy runner's six nudge branches and three hardcoded-concept-id
recovery paths with ONE generic step: if the committed basket does not cover the
worklist, ask the model to map the still-missing questions onto CIEL candidates
it has ALREADY reviewed this run, then apply + rebuild. There are no hardcoded
clinical concepts or phrases here; the only inputs are the worklist labels and
the candidate seeds journaled during Phase B.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ._llm_utils import message_from_response, parse_json_object
from .worklist import QuestionWorklist, normalize_label

if TYPE_CHECKING:
    from ..form_builder_tool_loop import FormBuilderToolLoop
    from ..form_drafts import FormDraft, FormDraftStore
    from ..llm_client import LlmClient

_LOGGER = logging.getLogger("tenaos.tena_agent.form_pipeline.repair")


def missing_labels(worklist: QuestionWorklist, draft: "FormDraft") -> list[str]:
    """Worklist labels not yet represented by a committed field.

    Heuristic but generic: a planned question is considered covered if a
    committed field's label shares a meaningful token with it. This avoids
    needing a perfect item->concept mapping while still detecting real gaps.
    """
    committed_tokens: set[str] = set()
    for section in draft.basket.get("sections") or []:
        for field in section.get("fields") or []:
            label = str(field.get("labelOverride") or field.get("conceptId") or "")
            committed_tokens.update(_tokens(label))
    missing: list[str] = []
    for item in worklist.items:
        item_tokens = _tokens(item.label)
        if not item_tokens:
            continue
        if not (item_tokens & committed_tokens):
            missing.append(item.label)
    return missing


def needs_repair(worklist: QuestionWorklist, draft: "FormDraft", *, target_min_fields: int) -> bool:
    field_count = _field_count(draft)
    target = min(len(worklist.items), target_min_fields) if worklist.items else 0
    if field_count >= target and field_count > 0:
        return False
    if not worklist.items:
        return field_count == 0  # nothing planned and nothing built -> let caller report honestly
    return field_count < target


def run_coverage_repair(
    *,
    store: "FormDraftStore",
    loop: "FormBuilderToolLoop",
    llm: "LlmClient",
    draft: "FormDraft",
    worklist: QuestionWorklist,
) -> int:
    """Attempt one repair commit from already-reviewed CIEL candidates.

    Returns the number of applied operations (0 if nothing safe was found).
    """
    from ..form_conversation import OP_MODEL_TOOL_CALL, OP_TOOL_RESULT

    reviewed = _reviewed_candidates(store, draft.draft_id)
    if not reviewed:
        return 0
    gaps = missing_labels(worklist, draft) or worklist.labels()

    section_id = _existing_section_id(draft)
    operations = _ask_model_for_ops(llm, worklist, gaps, reviewed, section_id)
    allowed_ids = set(reviewed)
    safe_ops: list[dict[str, Any]] = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        if op.get("op") == "add_field" and str(op.get("conceptId") or "") not in allowed_ids:
            continue
        safe_ops.append(op)
    if not safe_ops:
        return 0

    store.append_event(
        draft.draft_id,
        actor="middleware",
        operation="recovery_commit_started",
        detail=f"Coverage repair applying {len(safe_ops)} operation(s).",
        payload={"phase": "coverage_repair", "operationCount": len(safe_ops), "missing": gaps},
    )
    store.append_event(
        draft.draft_id,
        actor="gemma",
        operation=OP_MODEL_TOOL_CALL,
        detail="Coverage repair selected basket operations from reviewed CIEL candidates",
        payload={"phase": "coverage_repair", "toolName": "update_form_draft", "arguments": {"operations": safe_ops}},
    )
    result = loop.update_form_draft(draft.draft_id, safe_ops, actor="middleware")
    store.append_event(
        draft.draft_id,
        actor="middleware",
        operation=OP_TOOL_RESULT,
        detail="Tool result: coverage repair update_form_draft",
        payload={"toolName": "update_form_draft", "result": result, "phase": "coverage_repair"},
    )
    loop.build_form_schema(draft.draft_id)
    applied = len((result or {}).get("applied") or [])
    store.append_event(
        draft.draft_id,
        actor="middleware",
        operation="recovery_commit_applied",
        detail=f"Coverage repair applied {applied} operation(s).",
        payload={"phase": "coverage_repair", "appliedCount": applied},
    )
    return applied


# ---------------------------------------------------------------------------
# Internals


def _ask_model_for_ops(
    llm: "LlmClient",
    worklist: QuestionWorklist,
    gaps: list[str],
    reviewed: dict[str, dict[str, Any]],
    section_id: str | None,
) -> list[dict[str, Any]]:
    system = (
        "You map planned form questions onto CIEL concepts that were ALREADY reviewed. "
        "Return ONLY JSON: {\"operations\":[{\"op\":\"add_section\",\"sectionId\":\"...\",\"label\":\"...\"},"
        "{\"op\":\"add_field\",\"sectionId\":\"...\",\"conceptId\":\"...\",\"label\":\"...\"}]}. "
        "Use ONLY conceptId values from the candidate list. Never invent concepts. "
        "Prefer Question/Finding/Test/Obs concepts; never Drug or Diagnosis-as-label."
    )
    section_line = (
        f"Add fields to existing sectionId '{section_id}'."
        if section_id
        else "Include one add_section first, then add_field operations."
    )
    user = (
        f"Subject: {worklist.subject_summary or '(n/a)'}\n"
        f"Still-missing questions: {gaps[:12]}\n"
        f"{section_line}\n"
        f"Reviewed usable CIEL candidates ({len(reviewed)}): {list(reviewed.values())[:18]}\n"
        "Choose the best concept for each missing question. Output 5-10 add_field ops."
    )
    try:
        response = llm.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=900,
        )
    except Exception as exc:
        _LOGGER.warning("Coverage repair model call failed: %s", exc)
        return []
    parsed = parse_json_object(str(message_from_response(response).get("content") or ""))
    if not isinstance(parsed, dict):
        return []
    operations = parsed.get("operations")
    return operations if isinstance(operations, list) else []


def _reviewed_candidates(store: "FormDraftStore", draft_id: str) -> dict[str, dict[str, Any]]:
    """Collect usable CIEL seeds the model reviewed this run, deduped by conceptId."""
    from ..form_conversation import OP_TOOL_RESULT

    out: dict[str, dict[str, Any]] = {}
    for event in store.list_events(draft_id, limit=240):
        if event.operation != OP_TOOL_RESULT:
            continue
        result = event.payload.get("result") if isinstance(event.payload, dict) else None
        if not isinstance(result, dict):
            continue
        for seed in result.get("seeds") or []:
            if _seed_is_usable(seed):
                cid = str(seed.get("conceptId") or "")
                if cid:
                    out.setdefault(cid, seed)
        expansion = result.get("expansion")
        if isinstance(expansion, dict):
            concept = expansion.get("concept") or {}
            seed = {
                "conceptId": concept.get("concept_id") or concept.get("id"),
                "displayName": concept.get("display_name"),
                "datatype": concept.get("datatype"),
                "conceptClass": concept.get("concept_class"),
                "retired": bool(concept.get("retired")),
                "answerCount": len(expansion.get("answers") or []),
                "setMemberCount": len(expansion.get("setMembers") or []),
            }
            if _seed_is_usable(seed):
                cid = str(seed.get("conceptId") or "")
                if cid:
                    out.setdefault(cid, seed)
            # Set members are themselves usable candidates the model reviewed.
            for member in expansion.get("setMembers") or []:
                if _seed_is_usable(member):
                    cid = str(member.get("conceptId") or "")
                    if cid:
                        out.setdefault(cid, member)
    return out


def _seed_is_usable(seed: dict[str, Any]) -> bool:
    if not isinstance(seed, dict) or seed.get("retired"):
        return False
    datatype = seed.get("datatype") or ""
    cls = str(seed.get("conceptClass") or "").lower()
    if int(seed.get("setMemberCount") or 0) > 0 or cls in {"convset", "labset", "medset"}:
        return False
    if datatype == "Boolean":
        return True
    if datatype == "Coded":
        return int(seed.get("answerCount") or 0) > 0
    if datatype in {"Numeric", "Text", "Date", "Datetime", "Time", "Document"}:
        return cls not in {"diagnosis", "drug"}
    if datatype in {"N/A", ""}:
        return cls in {"diagnosis", "symptom", "finding", "procedure", "test", "question", "observable entity"}
    return False


def _existing_section_id(draft: "FormDraft") -> str | None:
    sections = draft.basket.get("sections") or []
    if sections:
        return str(sections[0].get("sectionId") or "") or None
    return None


def _field_count(draft: "FormDraft") -> int:
    return sum(len(s.get("fields") or []) for s in (draft.basket.get("sections") or []))


def _tokens(value: str) -> set[str]:
    return {t for t in normalize_label(value).split() if len(t) > 2}
