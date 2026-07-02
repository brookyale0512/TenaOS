"""Bounded validation-driven repair for report generation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..report_builder import ReportSpec
from .worklist import ReportWorklist

if TYPE_CHECKING:
    from ..report_builder_tool_loop import ReportBuilderToolLoop
    from ..report_drafts import ReportDraft, ReportDraftStore


def needs_repair(build_result: dict[str, Any]) -> bool:
    validation = build_result.get("validation") if isinstance(build_result, dict) else None
    issues = validation.get("issues") if isinstance(validation, dict) else []
    return any((issue or {}).get("severity") == "error" for issue in issues or [])


def run_report_repair(
    *,
    store: "ReportDraftStore",
    loop: "ReportBuilderToolLoop",
    draft: "ReportDraft",
    worklist: ReportWorklist,
    build_result: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    from ..report_conversation import OP_MODEL_TOOL_CALL, OP_TOOL_RESULT

    issues = ((build_result.get("validation") or {}).get("issues") or []) if isinstance(build_result, dict) else []
    operations = _ops_for_issues(draft, worklist, issues, _reviewed_candidates(store, draft.draft_id))
    if not operations:
        return {"applied": [], "warnings": [], "skipped": True}
    store.append_event(
        draft.draft_id,
        actor="middleware",
        operation="report_repair_started",
        detail=f"Repairing report spec with {len(operations)} operation(s).",
        payload={"operations": operations, "issues": issues, "warnings": warnings or []},
    )
    store.append_event(
        draft.draft_id,
        actor="gemma",
        operation=OP_MODEL_TOOL_CALL,
        detail="Report repair selected deterministic report operations",
        payload={"phase": "report_repair", "toolName": "update_report_draft", "arguments": {"operations": operations}},
    )
    result = loop.update_report_draft(draft.draft_id, operations, actor="middleware")
    store.append_event(
        draft.draft_id,
        actor="middleware",
        operation=OP_TOOL_RESULT,
        detail="Tool result: report repair update_report_draft",
        payload={"phase": "report_repair", "toolName": "update_report_draft", "result": result},
    )
    return result


def _ops_for_issues(
    draft: "ReportDraft",
    worklist: ReportWorklist,
    issues: list[dict[str, Any]],
    reviewed: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    spec = ReportSpec.from_dict(draft.spec)
    ops: list[dict[str, Any]] = []
    codes = {str(issue.get("code") or "") for issue in issues if isinstance(issue, dict)}

    if "missing_denominator" in codes and spec.report_type == "indicator":
        if worklist.denominator and worklist.denominator.kind == "ciel_concept":
            candidate = _best_reviewed_candidate(reviewed, worklist.denominator.search_phrases or [worklist.denominator.label])
            if candidate:
                ops.append({"op": "set_denominator", "kind": "ciel_concept", "conceptId": candidate["conceptId"], "label": candidate.get("displayName") or worklist.denominator.label})
        if not any(op.get("op") == "set_denominator" for op in ops):
            ops.append({"op": "set_denominator", "kind": "encounters_in_range"})

    if "missing_group_by" in codes and spec.report_type == "pivot":
        groups = worklist.group_by or []
        if not groups:
            groups = []
        for group in groups[:2]:
            ops.append({"op": "add_group_by", "dimension": group.dimension, "label": group.label or group.dimension})
        if not groups:
            ops.append({"op": "add_group_by", "dimension": "sex", "label": "Sex"})

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        path = str(issue.get("path") or "")
        code = str(issue.get("code") or "")
        index = _filter_index_from_path(path)
        if index is None or index >= len(spec.filters):
            continue
        filter_ = spec.filters[index]
        if code == "missing_filter_value":
            if filter_.filter_mode == "value_boolean":
                ops.append({"op": "set_filter_value", "filterId": filter_.filter_id, "valueBool": True})
            elif filter_.filter_mode == "value_concept":
                ops.append({"op": "set_filter_value", "filterId": filter_.filter_id, "valueConceptId": "1065"})
        elif code in {"numeric_operator_missing", "numeric_threshold_missing"}:
            planned = _planned_filter_for_label(worklist, filter_.label)
            if planned and planned.operator and planned.numeric_threshold is not None:
                ops.append(
                    {
                        "op": "set_filter_value",
                        "filterId": filter_.filter_id,
                        "operator": planned.operator,
                        "numericThreshold": planned.numeric_threshold,
                    }
                )

    if not spec.filters and worklist.filters:
        candidate_ops = _ops_from_reviewed_candidates(worklist, reviewed)
        ops.extend(candidate_ops)
    return _dedupe_ops(ops)


def _ops_from_reviewed_candidates(worklist: ReportWorklist, reviewed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for item in worklist.filters[:4]:
        candidate = _best_reviewed_candidate(reviewed, item.search_phrases or [item.label])
        if not candidate:
            continue
        op: dict[str, Any] = {"op": "add_filter", "conceptId": candidate["conceptId"], "label": item.label}
        if item.value_kind == "numeric" and item.operator and item.numeric_threshold is not None:
            op.update({"operator": item.operator, "numericThreshold": item.numeric_threshold})
        elif item.value_label:
            answer = _answer_candidate(candidate, item.value_label)
            if answer:
                op["valueConceptId"] = answer
        ops.append(op)
    return ops


def _reviewed_candidates(store: "ReportDraftStore", draft_id: str) -> dict[str, dict[str, Any]]:
    from ..report_conversation import OP_TOOL_RESULT

    out: dict[str, dict[str, Any]] = {}
    for event in store.list_events(draft_id, limit=300):
        if event.operation != OP_TOOL_RESULT:
            continue
        result = event.payload.get("result") if isinstance(event.payload, dict) else None
        if not isinstance(result, dict):
            continue
        for seed in result.get("seeds") or []:
            cid = str(seed.get("conceptId") or "")
            if cid:
                out.setdefault(cid, seed)
        expansion = result.get("expansion")
        if isinstance(expansion, dict):
            concept = expansion.get("concept") or {}
            cid = str(concept.get("concept_id") or concept.get("id") or "")
            if cid:
                out.setdefault(
                    cid,
                    {
                        "conceptId": cid,
                        "displayName": concept.get("display_name"),
                        "datatype": concept.get("datatype"),
                        "conceptClass": concept.get("concept_class"),
                        "answers": expansion.get("answers") or [],
                    },
                )
    return out


def _best_reviewed_candidate(reviewed: dict[str, dict[str, Any]], phrases: list[str]) -> dict[str, Any] | None:
    phrase_tokens = {token for phrase in phrases for token in _tokens(phrase)}
    best: tuple[int, dict[str, Any]] | None = None
    for candidate in reviewed.values():
        tokens = _tokens(str(candidate.get("displayName") or ""))
        score = len(tokens & phrase_tokens)
        if score <= 0:
            score = int(float(candidate.get("score") or 0.0) * 10)
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best and best[0] >= 0 else None


def _answer_candidate(candidate: dict[str, Any], value_label: str) -> str | None:
    target = set(_tokens(value_label))
    for rel in candidate.get("answers") or []:
        answer = rel.get("target") if isinstance(rel, dict) else {}
        label = str(answer.get("display_name") or "")
        if _tokens(label) & target:
            cid = str(answer.get("concept_id") or answer.get("id") or "")
            if cid:
                return cid
    return None


def _planned_filter_for_label(worklist: ReportWorklist, label: str):
    target = _tokens(label)
    for item in worklist.filters:
        if _tokens(item.label) & target:
            return item
    return None


def _filter_index_from_path(path: str) -> int | None:
    import re

    match = re.search(r"filters\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _tokens(value: str) -> set[str]:
    import re

    return {token for token in re.sub(r"[^a-z0-9]+", " ", value.lower()).split() if len(token) > 2}


def _dedupe_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for op in ops:
        key = (op.get("op"), op.get("filterId"), op.get("conceptId"), op.get("dimension"), op.get("kind"))
        if key in seen:
            continue
        seen.add(key)
        out.append(op)
    return out


__all__ = ["needs_repair", "run_report_repair"]
