"""Orchestrator for the robust v2 report-generation pipeline."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Literal

from .ciel_resolution import run_ciel_resolution
from .planning_phase import run_planning_phase
from .repair import needs_repair, run_report_repair

if TYPE_CHECKING:
    from ..config import Settings
    from ..report_builder_tool_loop import ReportBuilderToolLoop
    from ..report_drafts import ReportDraft, ReportDraftStore
    from ..llm_client import LlmClient

_LOGGER = logging.getLogger("tenaos.tena_agent.report_pipeline.runner")


def run_report_pipeline_agent(
    *,
    store: "ReportDraftStore",
    loop: "ReportBuilderToolLoop",
    llm: "LlmClient",
    draft: "ReportDraft",
    request: str,
    mode: Literal["create", "edit"],
    settings: "Settings",
) -> None:
    from ..report_conversation import OP_AGENT_PROMPT, OP_REPORT_EDIT_APPLIED, OP_REPORT_PLAN_APPLIED
    from ..report_conversation import _build_deterministic_summary

    starting_filter_count = _filter_count(draft)
    worklist = run_planning_phase(llm=llm, store=store, draft=draft, request=request, mode=mode, settings=settings)

    if worklist.needs_clarification:
        question = worklist.clarification_question or "Can you clarify the report criteria?"
        store.append_event(
            draft.draft_id,
            actor="gemma",
            operation=OP_AGENT_PROMPT,
            detail=question,
            payload={"text": question, "phase": "report_clarification"},
        )
        store.update_draft(draft.draft_id, conversation_state="awaiting_question", conversation_context={"lastAgentMode": mode, "pendingReportPlan": worklist.to_dict()})
        return

    professional_title = _professional_report_title(request, worklist)
    if mode == "create" and professional_title and professional_title != draft.name:
        store.update_draft(draft.draft_id, name=professional_title)
        draft = store.get_draft(draft.draft_id)

    warnings = run_ciel_resolution(
        store=store,
        loop=loop,
        llm=llm,
        draft=store.get_draft(draft.draft_id),
        request=request,
        worklist=worklist,
        settings=settings,
    )

    build = loop.build_report_query(draft.draft_id)
    if needs_repair(build):
        try:
            repair_result = run_report_repair(
                store=store,
                loop=loop,
                draft=store.get_draft(draft.draft_id),
                worklist=worklist,
                build_result=build,
                warnings=warnings,
            )
            if repair_result.get("applied"):
                build = loop.build_report_query(draft.draft_id)
        except Exception as exc:
            _LOGGER.warning("Report repair failed draft=%s: %s", draft.draft_id, exc, exc_info=True)

    ran_report = False
    if build.get("compiled"):
        run = loop.run_report(draft.draft_id)
        ran_report = bool(run.get("success"))

    latest = store.get_draft(draft.draft_id)
    ending_filter_count = _filter_count(latest)
    final_text = _build_deterministic_summary(
        mode=mode,
        draft=latest,
        starting_filter_count=starting_filter_count,
        ending_filter_count=ending_filter_count,
        warnings=warnings,
        ran_report=ran_report,
        request_text=request,
    )
    store.update_draft(
        draft.draft_id,
        conversation_state="ready" if latest.last_result else "awaiting_question",
        conversation_context={"lastAgentMode": mode, "pipeline": "v2"},
    )
    store.append_event(
        draft.draft_id,
        actor="gemma",
        operation=OP_REPORT_PLAN_APPLIED if mode == "create" else OP_REPORT_EDIT_APPLIED,
        detail=final_text,
        payload={"mode": mode, "finalText": final_text, "pipeline": "v2"},
    )


def _filter_count(draft: "ReportDraft") -> int:
    return len((draft.spec or {}).get("filters") or [])


def _professional_report_title(request: str, worklist: object) -> str:
    """Return a concise clinician-facing report title.

    Prefer a decent model title, but replace weak fragment titles produced by
    request-name heuristics. This does not choose clinical concepts; it only
    improves presentation.
    """
    title = str(getattr(worklist, "title", "") or "").strip()
    if title and not _title_is_weak(title):
        return title[:80]

    filters = list(getattr(worklist, "filters", []) or [])
    first_label = str(getattr(filters[0], "label", "") if filters else "").strip()
    disease = _disease_from_request(request) or first_label or "Clinical"
    disease = _title_case(disease)

    report_type = str(getattr(worklist, "report_type", "") or "count")
    group_by = list(getattr(worklist, "group_by", []) or [])
    has_month = any(str(getattr(group, "dimension", "")) == "date_month" for group in group_by)

    if "diagnos" in request.lower() or "diagnosed" in request.lower():
        base = f"{disease} Diagnoses"
    elif report_type == "cohort":
        base = f"{disease} Cohort"
    elif report_type == "indicator":
        base = f"{disease} Indicator"
    elif report_type == "count":
        base = f"{disease} Count"
    else:
        base = f"{disease} Report"
    if has_month and "by Month" not in base:
        base = f"{base} by Month"

    date_label = _date_label_from_request(request) or str(getattr(worklist, "date_range", "") or "").strip()
    if date_label:
        return f"{base} ({_title_case(date_label)})"[:80]
    return base[:80]


def _title_is_weak(title: str) -> bool:
    lowered = title.strip().lower()
    if not lowered or lowered == "untitled report":
        return True
    return bool(re.match(r"^(of|for|with|by|using|the|a|an)\b", lowered))


def _disease_from_request(request: str) -> str:
    text = re.sub(r"\s+", " ", str(request or "")).strip()
    match = re.search(r"\bdiagnosed\s+with\s+(.+?)(?:\s+(?:in|over|during|for|the|last|past|this)\b|$)", text, re.I)
    if match:
        return match.group(1).strip()
    match = re.search(r"\b(.+?)\s+diagnos(?:is|es|ed)\b", text, re.I)
    if match:
        return match.group(1).strip()
    return ""


def _date_label_from_request(request: str) -> str:
    match = re.search(r"\b(?:past|last)\s+(\d+)\s+(days?|weeks?|months?|years?)\b", request, re.I)
    if match:
        return f"last {match.group(1)} {match.group(2)}"
    for phrase in ("last quarter", "this quarter", "last month", "this month", "this year", "last year"):
        if phrase in request.lower():
            return phrase
    return ""


def _title_case(value: str) -> str:
    keep_upper = {"HIV", "TB", "ANC"}
    words = re.sub(r"[^A-Za-z0-9]+", " ", value).strip().split()
    out = []
    for word in words:
        upper = word.upper()
        out.append(upper if upper in keep_upper else word[:1].upper() + word[1:].lower())
    return " ".join(out)


__all__ = ["run_report_pipeline_agent"]
