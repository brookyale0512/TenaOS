"""Phase A for report generation: direct typed planning from the request."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Literal

from ..form_pipeline._llm_utils import (
    assistant_message_for_tool_calls,
    emit_thinking,
    extract_tool_calls,
    message_from_response,
    parse_json_object,
)
from .worklist import ReportWorklist, sanitize_worklist

if TYPE_CHECKING:
    from ..config import Settings
    from ..report_drafts import ReportDraft, ReportDraftStore
    from ..llm_client import LlmClient

_LOGGER = logging.getLogger("tenaos.tena_agent.report_pipeline.planning")


PLAN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "finalize_report_plan",
            "description": "Commit the structured report plan. Use clinical filters only; demographics/time go in groupBy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "title": {"type": "string"},
                    "reportType": {"type": "string", "enum": ["count", "cohort", "indicator", "pivot"]},
                    "dateRange": {"type": "string"},
                    "joinMode": {"type": "string", "enum": ["and", "or"]},
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "searchPhrases": {"type": "array", "items": {"type": "string"}},
                                "valueKind": {"type": "string", "enum": ["presence", "coded", "numeric", "any"]},
                                "valueLabel": {"type": "string"},
                                "operator": {"type": "string", "enum": ["eq", "gt", "ge", "lt", "le"]},
                                "numericThreshold": {"type": "number"},
                                "priority": {"type": "integer"},
                            },
                            "required": ["label"],
                        },
                    },
                    "denominator": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["encounters_in_range", "ciel_concept", "none"]},
                            "label": {"type": "string"},
                            "searchPhrases": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "groupBy": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "dimension": {"type": "string", "enum": ["sex", "age_group", "date_month", "concept_id"]},
                                "label": {"type": "string"},
                                "searchPhrases": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["dimension"],
                        },
                    },
                    "visualization": {"type": "string"},
                    "needsClarification": {"type": "boolean"},
                    "clarificationQuestion": {"type": "string"},
                },
                "required": ["reportType", "filters"],
            },
        },
    }
]


def run_planning_phase(
    *,
    llm: "LlmClient",
    store: "ReportDraftStore",
    draft: "ReportDraft",
    request: str,
    mode: Literal["create", "edit"],
    settings: "Settings",
) -> ReportWorklist:
    _emit_planning_started(store, draft.draft_id, mode)
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_prompt(draft, request, mode)},
    ]
    max_tokens = int(getattr(settings, "report_agent_brainstorm_max_tokens", 1100))
    for attempt in range(2):
        try:
            response = llm.chat(messages, temperature=0.0, max_tokens=max_tokens, tools=PLAN_TOOLS, tool_choice="auto")
        except Exception as exc:
            _LOGGER.warning("Report planning call failed draft=%s: %s", draft.draft_id, exc, exc_info=True)
            break
        message = message_from_response(response)
        content = str(message.get("content") or "")
        emit_thinking(store, draft.draft_id, content, phase="report_planning")
        payload = _finalize_from_calls(extract_tool_calls(message)) or _payload_from_text(content)
        if payload:
            worklist = sanitize_worklist(payload, request=request)
            _emit_plan_event(store, draft.draft_id, worklist)
            return worklist
        messages.append(assistant_message_for_tool_calls(message, extract_tool_calls(message)))
        messages.append({"role": "user", "content": "Return the final report plan now by calling finalize_report_plan."})
    worklist = sanitize_worklist(_fallback_plan_from_request(request), request=request)
    _emit_plan_event(store, draft.draft_id, worklist)
    return worklist


def _system_prompt() -> str:
    return (
        "You are a clinical report planner for OpenMRS. Convert the clinician request into a structured "
        "CIEL-backed report plan. Filters must be clinical concepts only. Sex, gender, age group, month, "
        "date, and time are groupBy dimensions, never filters. For coded requests like 'HIV positive', "
        "set valueKind='coded' and valueLabel='Positive'. For symptoms/findings, use valueKind='presence'. "
        "For numeric thresholds, set valueKind='numeric', operator, and numericThreshold. Indicators need "
        "a denominator; prefer encounters_in_range unless the user names a clinical denominator. Output by "
        "calling finalize_report_plan only. Treat common typos in date phrases, e.g. 'papst 12 months' "
        "means 'past 12 months'. Never create filters for words like past, last, month, line graph, trend, "
        "or chart. For 'month over month' or 'monthly' reports, use reportType='pivot', groupBy date_month, "
        "and time_series_line when the user asks for a line graph."
    )


def _user_prompt(draft: "ReportDraft", request: str, mode: str) -> str:
    return (
        f"mode: {mode}\n"
        f"report name: {draft.name}\n"
        f"user request: {request}\n\n"
        f"current spec:\n{json.dumps(draft.spec or {}, indent=2)}\n\n"
        "Plan only the requested report/change. Do not invent CIEL ids."
    )


def _finalize_from_calls(tool_calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    for call in tool_calls:
        if str(call.get("name") or "") == "finalize_report_plan" and isinstance(call.get("arguments"), dict):
            return dict(call["arguments"])
    return None


def _payload_from_text(content: str) -> dict[str, Any] | None:
    parsed = parse_json_object(content)
    if isinstance(parsed, dict) and (parsed.get("filters") or parsed.get("reportType")):
        return parsed
    match = re.search(r"\{.*\}", content or "", re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _fallback_plan_from_request(request: str) -> dict[str, Any]:
    lower = request.lower()
    report_type = "count"
    group_by = []
    visualization = "filter_bar"
    if any(token in lower for token in ("rate", "percentage", "percent", "among patients seen")):
        report_type = "indicator"
        group_by = [{"dimension": "date_month"}] if any(token in lower for token in ("monthly", "month", "over time")) else []
        visualization = "rate_over_time" if group_by else "indicator_rate"
    elif any(token in lower for token in (" by sex", " by gender", " by age", " by month", "monthly", "pivot", "heatmap", "stacked")):
        report_type = "pivot"
        if "month" in lower or "monthly" in lower:
            group_by.append({"dimension": "date_month"})
            visualization = "time_series_line"
        if "sex" in lower or "gender" in lower:
            group_by.append({"dimension": "sex"})
        if "age" in lower:
            group_by.append({"dimension": "age_group"})
        if not group_by:
            group_by = [{"dimension": "sex"}]
    date_range = _extract_date_range(lower)
    return {
        "summary": request,
        "reportType": report_type,
        "dateRange": date_range,
        "joinMode": "or" if " or " in lower else "and",
        "filters": [],
        "denominator": {"kind": "encounters_in_range"} if report_type == "indicator" else {"kind": "none"},
        "groupBy": group_by[:2],
        "visualization": visualization,
    }


def _extract_date_range(lower: str) -> str | None:
    for phrase in ("last quarter", "this quarter", "last month", "this month", "this year", "last year", "ytd"):
        if phrase in lower:
            return phrase
    match = re.search(r"(last|past)\s+\d+\s+(days?|months?|years?)", lower)
    return match.group(0) if match else None


def _emit_planning_started(store: "ReportDraftStore", draft_id: str, mode: str) -> None:
    from ..report_conversation import OP_AGENT_REASONING

    text = "Planning the report structure and separating clinical filters from groupings..."
    store.append_event(
        draft_id,
        actor="gemma",
        operation=OP_AGENT_REASONING,
        detail=text,
        payload={"phase": "report_planning_started", "mode": mode, "text": text},
    )


def _emit_plan_event(store: "ReportDraftStore", draft_id: str, worklist: ReportWorklist) -> None:
    from ..report_conversation import OP_AGENT_REASONING

    detail = f"Planned a {worklist.report_type} report with {len(worklist.filters)} clinical filter(s)."
    store.append_event(
        draft_id,
        actor="gemma",
        operation=OP_AGENT_REASONING,
        detail=detail,
        payload={"phase": "report_plan", "text": detail, "plan": worklist.to_dict()},
    )


__all__ = ["run_planning_phase"]
