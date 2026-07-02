"""Typed planning contract for report generation.

The report agent uses this as the hand-off between planning and CIEL
resolution. It keeps demographic/time groupings separate from clinical filters
so the resolver only searches CIEL for concepts that actually need terminology.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..report_builder import ReportType, VisualizationTemplate

FilterValueKind = Literal["presence", "coded", "numeric", "any"]
GroupDimension = Literal["sex", "age_group", "date_month", "concept_id"]
DenominatorKind = Literal["encounters_in_range", "ciel_concept", "none"]


@dataclass
class PlannedFilter:
    label: str
    search_phrases: list[str] = field(default_factory=list)
    value_kind: FilterValueKind = "presence"
    value_label: str | None = None
    operator: str | None = None
    numeric_threshold: float | None = None
    priority: int = 5
    concept_id: str | None = None
    resolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "searchPhrases": list(self.search_phrases),
            "valueKind": self.value_kind,
            "valueLabel": self.value_label,
            "operator": self.operator,
            "numericThreshold": self.numeric_threshold,
            "priority": self.priority,
            "conceptId": self.concept_id,
            "resolved": self.resolved,
        }


@dataclass
class PlannedDenominator:
    kind: DenominatorKind = "none"
    label: str = ""
    search_phrases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "searchPhrases": list(self.search_phrases)}


@dataclass
class PlannedGroupBy:
    dimension: GroupDimension
    label: str = ""
    search_phrases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "label": self.label, "searchPhrases": list(self.search_phrases)}


@dataclass
class ReportWorklist:
    summary: str = ""
    report_type: ReportType = "count"
    date_range: str | None = None
    join_mode: Literal["and", "or"] = "and"
    filters: list[PlannedFilter] = field(default_factory=list)
    denominator: PlannedDenominator | None = None
    group_by: list[PlannedGroupBy] = field(default_factory=list)
    visualization: VisualizationTemplate | None = None
    title: str = ""
    needs_clarification: bool = False
    clarification_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "reportType": self.report_type,
            "dateRange": self.date_range,
            "joinMode": self.join_mode,
            "filters": [item.to_dict() for item in self.filters],
            "denominator": self.denominator.to_dict() if self.denominator else None,
            "groupBy": [item.to_dict() for item in self.group_by],
            "visualization": self.visualization,
            "title": self.title,
            "needsClarification": self.needs_clarification,
            "clarificationQuestion": self.clarification_question,
        }

    def to_prompt_block(self) -> str:
        lines = [
            f"Summary: {self.summary or '(none)'}",
            f"Type: {self.report_type}",
            f"Date range: {self.date_range or '(none)'}",
            f"Join: {self.join_mode}",
            f"Visualization: {self.visualization or '(default)'}",
        ]
        lines.append("Filters:")
        if not self.filters:
            lines.append("  (none)")
        for index, item in enumerate(self.filters, 1):
            phrases = ", ".join(item.search_phrases[:4]) or item.label
            value = item.value_label or item.value_kind
            numeric = ""
            if item.operator and item.numeric_threshold is not None:
                numeric = f" | numeric: {item.operator} {item.numeric_threshold}"
            lines.append(f"  {index}. {item.label} | search: {phrases} | value: {value}{numeric}")
        if self.denominator:
            phrases = ", ".join(self.denominator.search_phrases[:3]) or self.denominator.label
            lines.append(f"Denominator: {self.denominator.kind} | {phrases}")
        if self.group_by:
            lines.append("Group by: " + ", ".join(g.dimension for g in self.group_by))
        return "\n".join(lines)


def sanitize_worklist(payload: dict[str, Any] | None, *, request: str = "") -> ReportWorklist:
    payload = payload or {}
    report_type = _coerce_report_type(payload.get("reportType") or payload.get("type"))
    group_by = _sanitize_group_by(payload.get("groupBy") or payload.get("group_by"))
    request_lower = _normalize_request_typos(request.lower())
    if group_by and _is_disaggregation_request(request_lower):
        report_type = "pivot"
    if _is_temporal_request(request_lower):
        report_type = "pivot"
        if not any(g.dimension == "date_month" for g in group_by):
            group_by.insert(0, PlannedGroupBy(dimension="date_month", label="Month"))
    if report_type == "pivot" and not group_by:
        group_by = [PlannedGroupBy(dimension="sex", label="Sex")]
    denominator = _sanitize_denominator(payload.get("denominator"), report_type)
    filters = _sanitize_filters(payload.get("filters"), request=request)
    if not filters:
        filters = _sanitize_filters(_filters_from_request(request), request=request)
    date_range = _coerce_date_range(payload.get("dateRange") or payload.get("date_range"), request_lower)
    visualization = _coerce_visualization(payload.get("visualization")) or _visualization_from_request(request_lower, report_type, group_by)
    return ReportWorklist(
        summary=_clean(payload.get("summary") or payload.get("plan") or request, 240),
        report_type=report_type,
        date_range=date_range or None,
        join_mode="or" if str(payload.get("joinMode") or payload.get("join") or "").lower() == "or" else "and",
        filters=filters,
        denominator=denominator,
        group_by=group_by[:2],
        visualization=visualization,
        title=_clean(payload.get("title") or payload.get("name"), 80),
        needs_clarification=bool(payload.get("needsClarification") or payload.get("needs_clarification")),
        clarification_question=_clean(payload.get("clarificationQuestion") or payload.get("clarification_question"), 240),
    )


def _sanitize_filters(raw: Any, *, request: str) -> list[PlannedFilter]:
    if not isinstance(raw, list):
        raw = _filters_from_request(request)
    out: list[PlannedFilter] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get("label") or item.get("intent") or item.get("name"), 90)
        if not label or _is_demographic_grouping(label) or _is_nonclinical_filter_label(label):
            continue
        key = normalize_label(label)
        if not key or key in seen:
            continue
        seen.add(key)
        value_kind = str(item.get("valueKind") or item.get("value_kind") or "presence").lower()
        if value_kind not in {"presence", "coded", "numeric", "any"}:
            value_kind = "presence"
        try:
            threshold = item.get("numericThreshold") if item.get("numericThreshold") is not None else item.get("numeric_threshold")
            numeric_threshold = float(threshold) if threshold is not None else None
        except (TypeError, ValueError):
            numeric_threshold = None
        phrases = _coerce_phrases(item.get("searchPhrases") or item.get("search_phrases"), label)
        phrases = _preserve_diagnosis_search_phrases(phrases, label=label, request=request)
        out.append(
            PlannedFilter(
                label=label,
                search_phrases=phrases,
                value_kind=value_kind,  # type: ignore[arg-type]
                value_label=_clean(item.get("valueLabel") or item.get("value_label"), 80) or None,
                operator=_coerce_operator(item.get("operator")),
                numeric_threshold=numeric_threshold,
                priority=_coerce_int(item.get("priority"), 5),
            )
        )
    out.sort(key=lambda item: item.priority)
    return out[:6]


def _sanitize_denominator(raw: Any, report_type: str) -> PlannedDenominator | None:
    if report_type != "indicator":
        return None
    if isinstance(raw, dict):
        kind = str(raw.get("kind") or "").strip()
        label = _clean(raw.get("label") or raw.get("phrase"), 90)
        phrases = _coerce_phrases(raw.get("searchPhrases") or raw.get("search_phrases"), label)
    else:
        text = _clean(raw, 90)
        kind = text
        label = text
        phrases = [text] if text and text not in {"encounters_in_range", "none"} else []
    if kind == "ciel_concept" or (label and "encounter" not in label.lower()):
        return PlannedDenominator(kind="ciel_concept", label=label, search_phrases=phrases)
    return PlannedDenominator(kind="encounters_in_range", label="Encounters in range")


def _sanitize_group_by(raw: Any) -> list[PlannedGroupBy]:
    if not isinstance(raw, list):
        raw = [raw] if raw else []
    out: list[PlannedGroupBy] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            dim = item
            label = item
            phrases: list[str] = []
        elif isinstance(item, dict):
            dim = str(item.get("dimension") or "")
            label = _clean(item.get("label") or dim, 80)
            phrases = _coerce_phrases(item.get("searchPhrases") or item.get("search_phrases"), label)
        else:
            continue
        dim = dim.strip().lower()
        aliases = {"gender": "sex", "month": "date_month", "monthly": "date_month"}
        dim = aliases.get(dim, dim)
        if dim not in {"sex", "age_group", "date_month", "concept_id"} or dim in seen:
            continue
        seen.add(dim)
        out.append(PlannedGroupBy(dimension=dim, label=label, search_phrases=phrases))  # type: ignore[arg-type]
    return out[:2]


def _filters_from_request(request: str) -> list[dict[str, Any]]:
    text = re.sub(r"\s+", " ", _normalize_request_typos(str(request or ""))).strip()
    if not text:
        return []
    candidate = re.sub(
        r"\b(?:show|create|build|run|count|how many|report|patients?|cases?|with|had|have|by|grouped|monthly|month|months?|over|line|graph|chart|trend|rate|percentage|last|past|this|quarter|year|days?|using|for|the|\d+)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"\s+", " ", candidate).strip(" .,;:")
    phrase = candidate or text
    return [{"label": phrase, "searchPhrases": _preserve_diagnosis_search_phrases([phrase], label=phrase, request=request), "valueKind": "presence", "priority": 1}]


def _coerce_report_type(value: Any) -> ReportType:
    text = str(value or "").strip().lower()
    return text if text in {"count", "cohort", "indicator", "pivot"} else "count"  # type: ignore[return-value]


def _coerce_visualization(value: Any) -> VisualizationTemplate | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "line": "time_series_line",
        "line_graph": "time_series_line",
        "line_chart": "time_series_line",
        "trend": "time_series_line",
        "bar": "time_series_bar",
        "bar_graph": "time_series_bar",
        "bar_chart": "time_series_bar",
    }
    text = aliases.get(text, text)
    allowed = {
        "filter_bar",
        "indicator_rate",
        "pivot_grouped_bar",
        "pivot_stacked_bar",
        "pivot_heatmap",
        "time_series_bar",
        "time_series_line",
        "stacked_time_series",
        "rate_over_time",
    }
    return text if text in allowed else None  # type: ignore[return-value]


def _coerce_phrases(raw: Any, fallback: str) -> list[str]:
    phrases: list[str] = []
    if isinstance(raw, list):
        for entry in raw:
            phrase = _clean(entry, 120)
            if phrase and phrase.lower() not in {p.lower() for p in phrases}:
                phrases.append(phrase)
    if not phrases and fallback:
        phrases.append(fallback)
    return phrases[:4]


def _preserve_diagnosis_search_phrases(phrases: list[str], *, label: str, request: str) -> list[str]:
    """Keep diagnosis wording in CIEL searches without choosing concepts.

    The planner often shortens "patients diagnosed with malaria" to just
    "Malaria". That loses the key CIEL retrieval hint that this is a diagnosis
    family, not a lab/test/observation. This function preserves the user's
    diagnosis wording while leaving concept selection to Gemma and the CIEL
    tools.
    """
    lowered_request = request.lower()
    if "diagnos" not in lowered_request and "diagnosed" not in lowered_request:
        return phrases
    disease = _diagnosis_target_from_request(request) or label
    additions = [f"{disease} diagnosis", f"diagnosed with {disease}"]
    out = list(phrases)
    existing = {p.lower() for p in out}
    for phrase in additions:
        cleaned = _clean(phrase, 120)
        if cleaned and cleaned.lower() not in existing:
            out.insert(0, cleaned)
            existing.add(cleaned.lower())
    return out[:4]


def _diagnosis_target_from_request(request: str) -> str:
    text = re.sub(r"\s+", " ", str(request or "")).strip()
    match = re.search(r"\bdiagnosed\s+with\s+(.+?)(?:\s+(?:in|over|during|for|the|last|past|this)\b|$)", text, re.IGNORECASE)
    if match:
        return _clean(match.group(1), 80)
    match = re.search(r"\b(.+?)\s+diagnos(?:is|ed)\b", text, re.IGNORECASE)
    if match:
        return _clean(match.group(1), 80)
    return ""


def _coerce_operator(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in {"eq", "gt", "ge", "lt", "le"} else None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return max(1, min(int(value), 10))
    except (TypeError, ValueError):
        return default


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" .,;:'\"")[:limit]


def normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _is_demographic_grouping(label: str) -> bool:
    lowered = label.lower().strip()
    return lowered in {"sex", "gender", "age", "age group", "month", "date", "date month", "monthly"}


def _is_nonclinical_filter_label(label: str) -> bool:
    lowered = normalize_label(_normalize_request_typos(label))
    if not lowered:
        return True
    temporal_only = {
        "past",
        "last",
        "this",
        "month",
        "months",
        "monthly",
        "date",
        "time",
        "line",
        "graph",
        "chart",
        "trend",
        "month over month",
        "past 12 months",
        "last 12 months",
    }
    if lowered in temporal_only:
        return True
    tokens = set(lowered.split())
    nonclinical = {"past", "last", "this", "month", "months", "monthly", "line", "graph", "chart", "trend", "using", "over"}
    return bool(tokens) and tokens.issubset(nonclinical | {str(i) for i in range(0, 100)})


def _normalize_request_typos(value: str) -> str:
    return re.sub(r"\bpapst\b", "past", value, flags=re.IGNORECASE)


def _is_temporal_request(lower: str) -> bool:
    return any(phrase in lower for phrase in ("month over month", "month by month", "monthly", "by month", "over time"))


def _is_disaggregation_request(lower: str) -> bool:
    return bool(
        re.search(r"\bby\s+(?:sex|gender|age|age group|age groups|month)\b", lower)
        or "grouped by" in lower
        or "breakdown" in lower
    )


def _extract_date_range_from_request(lower: str) -> str | None:
    for phrase in ("last quarter", "this quarter", "last month", "this month", "this year", "last year", "ytd"):
        if phrase in lower:
            return phrase
    match = re.search(r"(last|past)\s+(\d+)\s+(days?|months?|years?)", lower)
    if match:
        # resolve_date_range accepts "last N months", not "past N months".
        return f"last {match.group(2)} {match.group(3)}"
    return None


def _coerce_date_range(raw: Any, request_lower: str) -> str | None:
    value = _clean(_normalize_request_typos(str(raw or "")), 80)
    if not value:
        return _extract_date_range_from_request(request_lower)
    extracted = _extract_date_range_from_request(value.lower())
    return extracted or value


def _visualization_from_request(
    lower: str,
    report_type: str,
    group_by: list[PlannedGroupBy],
) -> VisualizationTemplate | None:
    if "line" in lower or "trend" in lower:
        return "time_series_line" if report_type == "pivot" else "rate_over_time"
    if any(phrase in lower for phrase in ("month over month", "month by month", "monthly", "by month")) and report_type == "pivot":
        return "time_series_line"
    if "bar" in lower:
        return "time_series_bar" if any(g.dimension == "date_month" for g in group_by) else "pivot_grouped_bar"
    return None


__all__ = [
    "PlannedDenominator",
    "PlannedFilter",
    "PlannedGroupBy",
    "ReportWorklist",
    "sanitize_worklist",
    "normalize_label",
]
