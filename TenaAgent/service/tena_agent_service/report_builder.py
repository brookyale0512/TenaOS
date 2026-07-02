"""Deterministic compiler for the CIEL-backed report builder.

This module is the trust boundary between the Gemma-driven agent and OpenMRS
FHIR2. The agent reasons over a `ReportSpec` (a small structured query plan).
This module:

    1. Resolves natural-language date phrases into absolute ISO dates.
    2. Validates that every CIEL concept referenced by the spec resolves in
       the local CIEL store and is usable as a filter target.
    3. Compiles the spec into a `QueryPlan` consisting of one or more FHIR
       Observation search descriptors plus a post-processing recipe
       (intersect / union / divide / groupby_demographic).

The agent never emits FHIR URLs. It only mutates the spec through an
allow-listed op grammar. Every query that hits OpenMRS goes through
`spec_to_query` first.

Filter-mode policy (chosen by CIEL datatype, not by the agent):
    * Boolean datatype       -> filter_mode = "value_boolean"
      Reader fetches all Observation for the code, filters
      `valueBoolean == filter.value_bool` client-side. OpenMRS FHIR2
      maps Boolean datatype obs to `valueBoolean: true|false`, NOT to
      `valueCodeableConcept` 1065/1066 — querying these with
      `value-concept=1065AAAA...` returns zero hits.
    * Coded datatype         -> filter_mode = "value_concept"
      Server-side `value-concept=` filter, with a one-time probe + fallback
      to client-side filtering when the server doesn't accept the parameter.
    * Numeric datatype       -> filter_mode = "client_numeric"
      `value-quantity=` is unreliable on OpenMRS FHIR2 across builds;
      always fetch all Observation for the code and filter
      `valueQuantity.value` against the operator + threshold in middleware.
    * N/A clinical datatype  -> filter_mode = "value_concept"
      Rendered as Yes/No in forms (1065/1066). Same server-side path.
    * Any datatype           -> filter_mode = "any_value" when the spec asks
      for "patient has any value recorded for this concept" (used by the
      indicator denominator path).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from .ciel import CielClient, ConceptNotFoundError, openmrs_uuid_for_concept_id


ReportType = Literal["count", "cohort", "indicator", "pivot"]
JoinMode = Literal["and", "or"]
FilterMode = Literal["value_concept", "value_boolean", "client_numeric", "condition", "any_value"]
NumericOperator = Literal["eq", "gt", "ge", "lt", "le"]
GroupDimension = Literal["sex", "age_group", "concept_id", "date_month"]
DenominatorKind = Literal["ciel_concept", "encounters_in_range"]
VisualizationTemplate = Literal[
    "filter_bar",
    "indicator_rate",
    "pivot_grouped_bar",
    "pivot_stacked_bar",
    "pivot_heatmap",
    "time_series_bar",
    "time_series_line",
    "stacked_time_series",
    "rate_over_time",
]


@dataclass
class ReportFilter:
    """A single filter row in the spec.

    `filter_mode` is set by the compiler based on the CIEL bundle's datatype,
    not by the agent. The agent supplies the bare ingredients
    (`concept_id`, `value_concept_id` / `value_bool` / `operator` +
    `numeric_threshold`) and the compiler decides how the reader should
    evaluate the filter at runtime.
    """

    filter_id: str
    concept_id: str
    label: str
    concept_ids: list[str] = field(default_factory=list)
    filter_mode: FilterMode = "value_concept"
    value_concept_id: str | None = None
    value_bool: bool | None = None
    operator: NumericOperator | None = None
    numeric_threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filterId": self.filter_id,
            "conceptId": self.concept_id,
            "conceptIds": list(dict.fromkeys([self.concept_id, *self.concept_ids])),
            "label": self.label,
            "filterMode": self.filter_mode,
            "valueConceptId": self.value_concept_id,
            "valueBool": self.value_bool,
            "operator": self.operator,
            "numericThreshold": self.numeric_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportFilter":
        return cls(
            filter_id=str(data.get("filterId") or ""),
            concept_id=str(data.get("conceptId") or ""),
            label=str(data.get("label") or ""),
            concept_ids=[
                str(cid)
                for cid in (data.get("conceptIds") or [])
                if str(cid or "").strip()
            ],
            filter_mode=data.get("filterMode") or "value_concept",
            value_concept_id=data.get("valueConceptId"),
            value_bool=data.get("valueBool"),
            operator=data.get("operator"),
            numeric_threshold=data.get("numericThreshold"),
        )


@dataclass
class Denominator:
    """Indicator denominator descriptor.

    Two supported kinds:
      - `"ciel_concept"`: a single CIEL concept (e.g. CIEL 1424
        "Pregnancy diagnosis") plus optional `value_concept_id` /
        `value_bool` / numeric operator. Evaluated as its own filter against
        the same date range as the numerator.
      - `"encounters_in_range"`: distinct subjects across all encounters in
        the report's date range. Backed by `Encounter?date=ge&date=le`. This
        avoids the full-Observation-table scan of the original "all patients
        in range" idea.
    """

    kind: DenominatorKind
    concept_id: str | None = None
    label: str = ""
    value_concept_id: str | None = None
    value_bool: bool | None = None
    operator: NumericOperator | None = None
    numeric_threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "conceptId": self.concept_id,
            "label": self.label,
            "valueConceptId": self.value_concept_id,
            "valueBool": self.value_bool,
            "operator": self.operator,
            "numericThreshold": self.numeric_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Denominator":
        return cls(
            kind=data.get("kind") or "encounters_in_range",
            concept_id=data.get("conceptId"),
            label=str(data.get("label") or ""),
            value_concept_id=data.get("valueConceptId"),
            value_bool=data.get("valueBool"),
            operator=data.get("operator"),
            numeric_threshold=data.get("numericThreshold"),
        )


@dataclass
class GroupBy:
    """One pivot dimension. At most 2 group_by entries are supported in v1."""

    dimension: GroupDimension
    concept_id: str | None = None  # only when dimension == "concept_id"
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "conceptId": self.concept_id,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupBy":
        return cls(
            dimension=data.get("dimension") or "sex",
            concept_id=data.get("conceptId"),
            label=str(data.get("label") or ""),
        )


@dataclass
class ReportVisualization:
    """Validated visualization intent.

    Gemma may request a template, but the compiler normalizes the request
    against the report type before any chart-ready data is produced.
    """

    template: VisualizationTemplate = "filter_bar"
    title: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"template": self.template, "title": self.title, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ReportVisualization | None":
        if not data:
            return None
        return cls(
            template=data.get("template") or "filter_bar",
            title=str(data.get("title") or ""),
            reason=str(data.get("reason") or ""),
        )


@dataclass
class ReportSpec:
    """The agent-mutable query plan."""

    report_type: ReportType = "count"
    date_from: str | None = None
    date_to: str | None = None
    date_range_label: str | None = None  # raw natural-language phrase
    filters: list[ReportFilter] = field(default_factory=list)
    join_mode: JoinMode = "and"
    denominator: Denominator | None = None
    group_by: list[GroupBy] = field(default_factory=list)
    visualization: ReportVisualization | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reportType": self.report_type,
            "dateFrom": self.date_from,
            "dateTo": self.date_to,
            "dateRangeLabel": self.date_range_label,
            "filters": [f.to_dict() for f in self.filters],
            "joinMode": self.join_mode,
            "denominator": self.denominator.to_dict() if self.denominator else None,
            "groupBy": [g.to_dict() for g in self.group_by],
            "visualization": self.visualization.to_dict() if self.visualization else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ReportSpec":
        if not data:
            return cls()
        return cls(
            report_type=data.get("reportType") or "count",
            date_from=data.get("dateFrom"),
            date_to=data.get("dateTo"),
            date_range_label=data.get("dateRangeLabel"),
            filters=[ReportFilter.from_dict(f) for f in (data.get("filters") or [])],
            join_mode=data.get("joinMode") or "and",
            denominator=Denominator.from_dict(data["denominator"]) if data.get("denominator") else None,
            group_by=[GroupBy.from_dict(g) for g in (data.get("groupBy") or [])],
            visualization=ReportVisualization.from_dict(data.get("visualization")),
        )


@dataclass
class ValidationIssue:
    severity: Literal["error", "warning"]
    path: str
    message: str
    code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"severity": self.severity, "path": self.path, "message": self.message}
        if self.code:
            payload["code"] = self.code
        return payload


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"issues": [issue.to_dict() for issue in self.issues]}

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


# ---------------------------------------------------------------------------
# Date range resolution
#
# The agent never computes dates itself; it passes a natural-language string
# via the `set_date_range` op and the compiler resolves it deterministically
# here. The lookup table is small and explicit; anything we don't recognise
# is rejected with a clear error rather than guessed.


_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEAR_QUARTER_RE = re.compile(r"^(\d{4})[ \-]?[qQ]([1-4])$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})[ \-]?(\d{1,2})$")
_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*(?:\.\.|to|—|-)\s*(\d{4}-\d{2}-\d{2})$")


def _quarter_range(year: int, quarter: int) -> tuple[date, date]:
    start_month = 3 * (quarter - 1) + 1
    start = date(year, start_month, 1)
    end_month = start_month + 2
    end = date(year, end_month, calendar.monthrange(year, end_month)[1])
    return start, end


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _shift_months(reference: date, months: int) -> date:
    total = reference.month - 1 + months
    year = reference.year + total // 12
    month = total % 12 + 1
    day = min(reference.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def resolve_date_range(
    text: str | None,
    *,
    reference_date: date | None = None,
) -> tuple[date | None, date | None, str | None]:
    """Resolve a natural-language date phrase to absolute ISO dates.

    Returns ``(date_from, date_to, normalised_label)``. The label is the
    canonical version of the input (e.g. trimmed lowercase) so we can echo
    it back to the user verbatim.

    Recognised forms:
      - ``""`` / ``None``                  -> ``(None, None, None)`` (no range)
      - ``"last quarter"``, ``"this quarter"``, ``"q1"``, ``"YYYY-Qn"``
      - ``"last month"``, ``"this month"``, ``"YYYY-MM"``
      - ``"last N months"``                -> N from 1..36
      - ``"ytd"`` / ``"year to date"``     -> Jan 1 of current year .. today
      - ``"this year"`` / ``"last year"``  -> calendar year bounds
      - ``"YYYY-MM-DD..YYYY-MM-DD"``       -> explicit bounded range
      - single ``"YYYY-MM-DD"``            -> that single day as both bounds

    Raises ``ValueError`` for any other input so the compiler can surface a
    clear validation error rather than silently guessing.
    """
    if not text or not str(text).strip():
        return None, None, None
    raw = str(text).strip()
    label = raw
    lower = raw.lower()
    today = reference_date or date.today()

    # Explicit YYYY-MM-DD..YYYY-MM-DD range
    range_match = _RANGE_RE.match(lower)
    if range_match:
        d1 = date.fromisoformat(range_match.group(1))
        d2 = date.fromisoformat(range_match.group(2))
        if d1 > d2:
            d1, d2 = d2, d1
        return d1, d2, label

    # Single ISO date
    iso_match = _ISO_DATE_RE.match(lower)
    if iso_match:
        d = date.fromisoformat(lower)
        return d, d, label

    # YYYY-Qn / YYYYQn
    yq = _YEAR_QUARTER_RE.match(lower)
    if yq:
        year, quarter = int(yq.group(1)), int(yq.group(2))
        d1, d2 = _quarter_range(year, quarter)
        return d1, d2, label

    # YYYY-MM
    ym = _YEAR_MONTH_RE.match(lower)
    if ym:
        year, month = int(ym.group(1)), int(ym.group(2))
        if 1 <= month <= 12:
            d1, d2 = _month_range(year, month)
            return d1, d2, label

    # Named phrases
    if lower in {"today"}:
        return today, today, label
    if lower in {"yesterday"}:
        d = today - timedelta(days=1)
        return d, d, label
    if lower in {"this month"}:
        return _month_range(today.year, today.month)[0], _month_range(today.year, today.month)[1], label
    if lower in {"last month"}:
        prev = _shift_months(today.replace(day=1), -1)
        d1, d2 = _month_range(prev.year, prev.month)
        return d1, d2, label
    if lower in {"this quarter"}:
        q = (today.month - 1) // 3 + 1
        d1, d2 = _quarter_range(today.year, q)
        return d1, d2, label
    if lower in {"last quarter"}:
        q = (today.month - 1) // 3 + 1
        if q == 1:
            d1, d2 = _quarter_range(today.year - 1, 4)
        else:
            d1, d2 = _quarter_range(today.year, q - 1)
        return d1, d2, label
    if lower in {"this year", "ytd", "year to date"}:
        return date(today.year, 1, 1), today, label
    if lower in {"last year"}:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), label

    rel_months = re.match(r"^last\s+(\d+)\s+months?$", lower)
    if rel_months:
        n = int(rel_months.group(1))
        if 1 <= n <= 36:
            d2 = today
            d1 = _shift_months(today, -n)
            return d1, d2, label

    rel_days = re.match(r"^last\s+(\d+)\s+days?$", lower)
    if rel_days:
        n = int(rel_days.group(1))
        if 1 <= n <= 366:
            return today - timedelta(days=n), today, label

    raise ValueError(
        f"Could not resolve date range '{raw}'. Use a phrase like 'last quarter', "
        "'last 6 months', 'this year', 'YYYY-Qn', 'YYYY-MM', or "
        "'YYYY-MM-DD..YYYY-MM-DD'."
    )


# ---------------------------------------------------------------------------
# Filter-mode helpers


_NA_CLINICAL_CLASSES_FOR_REPORT = {
    "diagnosis",
    "symptom",
    "finding",
    "symptom/finding",
    "procedure",
    "radiology/imaging procedure",
    "test",
    "question",
    "observable entity",
    "program",
    "specimen",
    "misc",
}


def filter_mode_for_concept(bundle: dict[str, Any]) -> FilterMode:
    """Return the runtime filter mode for a CIEL concept bundle.

    Mirrors the form-builder's `_is_usable_form_bundle` decision tree on
    datatype but maps to a `FilterMode` instead of a renderer.
    """
    concept = bundle.get("concept") or {}
    datatype = (concept.get("datatype") or "").strip()
    cls = (concept.get("concept_class") or "").strip().lower()
    if cls == "diagnosis":
        return "condition"
    if datatype == "Boolean":
        return "value_boolean"
    if datatype == "Coded":
        return "value_concept"
    if datatype in {"Numeric", "Text", "Date", "Datetime", "Time", "Document"}:
        if datatype == "Numeric":
            return "client_numeric"
        return "any_value"
    if datatype in {"N/A", "", None}:
        if cls in _NA_CLINICAL_CLASSES_FOR_REPORT:
            return "value_concept"
    return "any_value"


# ---------------------------------------------------------------------------
# Visualization helpers


_VISUALIZATION_TEMPLATES_BY_REPORT_TYPE: dict[ReportType, set[VisualizationTemplate]] = {
    "count": {"filter_bar"},
    "cohort": {"filter_bar"},
    "indicator": {"indicator_rate", "filter_bar", "rate_over_time"},
    "pivot": {
        "pivot_grouped_bar",
        "pivot_stacked_bar",
        "pivot_heatmap",
        "time_series_bar",
        "time_series_line",
        "stacked_time_series",
    },
}

_DEFAULT_VISUALIZATION_BY_REPORT_TYPE: dict[ReportType, VisualizationTemplate] = {
    "count": "filter_bar",
    "cohort": "filter_bar",
    "indicator": "indicator_rate",
    "pivot": "pivot_grouped_bar",
}

_VISUALIZATION_DEFAULT_TITLES: dict[VisualizationTemplate, str] = {
    "filter_bar": "Matched patients by filter",
    "indicator_rate": "Patient proportion",
    "pivot_grouped_bar": "Pivot counts by group",
    "pivot_stacked_bar": "Pivot counts by stacked group",
    "pivot_heatmap": "Pivot count intensity",
    "time_series_bar": "Patients by month",
    "time_series_line": "Patient trend over time",
    "stacked_time_series": "Patients by month and group",
    "rate_over_time": "Indicator rate over time",
}


def compatible_visualization_templates(report_type: ReportType) -> set[VisualizationTemplate]:
    return set(_VISUALIZATION_TEMPLATES_BY_REPORT_TYPE.get(report_type, {"filter_bar"}))


def default_visualization_for_report_type(report_type: ReportType) -> ReportVisualization:
    template = _DEFAULT_VISUALIZATION_BY_REPORT_TYPE.get(report_type, "filter_bar")
    return ReportVisualization(
        template=template,
        title=_VISUALIZATION_DEFAULT_TITLES[template],
        reason="Default visualization for this report type.",
    )


def default_visualization_for_spec(report_type: ReportType, group_by: list[GroupBy] | None = None) -> ReportVisualization:
    group_by = group_by or []
    if report_type == "pivot" and group_by and group_by[0].dimension == "date_month":
        has_second_dimension = len(group_by) > 1
        template: VisualizationTemplate = "stacked_time_series" if has_second_dimension else "time_series_line"
        return ReportVisualization(
            template=template,
            title=_VISUALIZATION_DEFAULT_TITLES[template],
            reason="Default temporal visualization for month-by-month reports.",
        )
    if report_type == "indicator" and any(g.dimension == "date_month" for g in group_by):
        return ReportVisualization(
            template="rate_over_time",
            title=_VISUALIZATION_DEFAULT_TITLES["rate_over_time"],
            reason="Default temporal visualization for monthly indicator reports.",
        )
    return default_visualization_for_report_type(report_type)


def normalize_visualization(
    report_type: ReportType,
    requested: ReportVisualization | None,
    group_by: list[GroupBy] | None = None,
) -> ReportVisualization:
    """Return a report-type-safe visualization, falling back on mismatch."""

    default = default_visualization_for_spec(report_type, group_by)
    if requested is None:
        return default
    if requested.template not in compatible_visualization_templates(report_type):
        return ReportVisualization(
            template=default.template,
            title=requested.title or default.title,
            reason=(
                f"Requested template '{requested.template}' is not compatible with "
                f"{report_type} reports; using '{default.template}'."
            ),
        )
    return ReportVisualization(
        template=requested.template,
        title=requested.title or _VISUALIZATION_DEFAULT_TITLES.get(requested.template, default.title),
        reason=requested.reason or "Requested by the report builder.",
    )


# ---------------------------------------------------------------------------
# Compiler


@dataclass
class CompiledFilter:
    """Reader-facing view of a single filter (output of spec_to_query)."""

    filter_id: str
    label: str
    code_uuid: str
    filter_mode: FilterMode
    code_uuids: list[str] = field(default_factory=list)
    value_concept_uuid: str | None = None
    value_bool: bool | None = None
    operator: NumericOperator | None = None
    numeric_threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filterId": self.filter_id,
            "label": self.label,
            "codeUuid": self.code_uuid,
            "codeUuids": list(dict.fromkeys([self.code_uuid, *self.code_uuids])),
            "filterMode": self.filter_mode,
            "valueConceptUuid": self.value_concept_uuid,
            "valueBool": self.value_bool,
            "operator": self.operator,
            "numericThreshold": self.numeric_threshold,
        }


@dataclass
class CompiledQuery:
    """Reader-facing query plan."""

    report_type: ReportType
    date_from: str | None
    date_to: str | None
    date_range_label: str | None
    join_mode: JoinMode
    filters: list[CompiledFilter] = field(default_factory=list)
    denominator: CompiledFilter | None = None
    denominator_kind: DenominatorKind | None = None
    group_by: list[GroupBy] = field(default_factory=list)
    visualization: ReportVisualization | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reportType": self.report_type,
            "dateFrom": self.date_from,
            "dateTo": self.date_to,
            "dateRangeLabel": self.date_range_label,
            "joinMode": self.join_mode,
            "filters": [f.to_dict() for f in self.filters],
            "denominator": self.denominator.to_dict() if self.denominator else None,
            "denominatorKind": self.denominator_kind,
            "groupBy": [g.to_dict() for g in self.group_by],
            "visualization": self.visualization.to_dict() if self.visualization else None,
        }


def spec_to_query(
    spec: ReportSpec,
    ciel: CielClient,
    *,
    reference_date: date | None = None,
) -> CompiledQuery:
    """Compile a spec into a reader-friendly CompiledQuery.

    Caller is expected to have already called `validate_spec` and gated on
    its result. This function does NOT silently fall back; it raises on
    unresolved CIEL ids so callers can surface a clean error.
    """
    date_from = spec.date_from
    date_to = spec.date_to
    label = spec.date_range_label
    # If only a label is set (no resolved dates), resolve here.
    if spec.date_range_label and (not date_from and not date_to):
        d1, d2, _normalised = resolve_date_range(spec.date_range_label, reference_date=reference_date)
        date_from = d1.isoformat() if d1 else None
        date_to = d2.isoformat() if d2 else None

    compiled_filters: list[CompiledFilter] = []
    for f in spec.filters:
        compiled_filters.append(_compile_filter(f, ciel))

    denominator_compiled: CompiledFilter | None = None
    denominator_kind: DenominatorKind | None = None
    if spec.report_type == "indicator" and spec.denominator is not None:
        denominator_kind = spec.denominator.kind
        if spec.denominator.kind == "ciel_concept" and spec.denominator.concept_id:
            denominator_compiled = _compile_filter(
                ReportFilter(
                    filter_id=f"denom_{spec.denominator.concept_id}",
                    concept_id=spec.denominator.concept_id,
                    label=spec.denominator.label or "Denominator",
                    value_concept_id=spec.denominator.value_concept_id,
                    value_bool=spec.denominator.value_bool,
                    operator=spec.denominator.operator,
                    numeric_threshold=spec.denominator.numeric_threshold,
                ),
                ciel,
            )

    return CompiledQuery(
        report_type=spec.report_type,
        date_from=date_from,
        date_to=date_to,
        date_range_label=label,
        join_mode=spec.join_mode,
        filters=compiled_filters,
        denominator=denominator_compiled,
        denominator_kind=denominator_kind,
        group_by=list(spec.group_by),
        visualization=normalize_visualization(spec.report_type, spec.visualization, spec.group_by),
    )


def _compile_filter(f: ReportFilter, ciel: CielClient) -> CompiledFilter:
    try:
        bundle = ciel.get_concept_bundle(f.concept_id)
    except ConceptNotFoundError as exc:
        raise ValueError(
            f"Concept '{f.concept_id}' is not in the CIEL store; cannot compile filter."
        ) from exc
    # The compiler owns runtime mode selection. Older drafts may persist a
    # now-stale mode; rederive here so Diagnosis concepts use Condition reads.
    derived_mode = filter_mode_for_concept(bundle)
    mode: FilterMode = "any_value" if f.filter_mode == "any_value" and derived_mode != "condition" else derived_mode
    concept_ids = _normalized_filter_concept_ids(f)
    code_uuid = openmrs_uuid_for_concept_id(concept_ids[0])
    code_uuids = [
        openmrs_uuid_for_concept_id(cid)
        for cid in concept_ids
        if cid != concept_ids[0]
    ]
    value_concept_uuid: str | None = None
    if f.value_concept_id:
        value_concept_uuid = openmrs_uuid_for_concept_id(f.value_concept_id)
    return CompiledFilter(
        filter_id=f.filter_id,
        label=f.label or (bundle.get("concept", {}).get("display_name") or f.concept_id),
        code_uuid=code_uuid,
        code_uuids=code_uuids,
        filter_mode=mode,
        value_concept_uuid=value_concept_uuid,
        value_bool=f.value_bool,
        operator=f.operator,
        numeric_threshold=f.numeric_threshold,
    )


def _normalized_filter_concept_ids(f: ReportFilter) -> list[str]:
    ids: list[str] = []
    for cid in [f.concept_id, *list(f.concept_ids or [])]:
        text = str(cid or "").strip()
        if text and text not in ids:
            ids.append(text)
    return ids or [f.concept_id]


# ---------------------------------------------------------------------------
# Validator


def validate_spec(spec: ReportSpec, ciel: CielClient) -> ValidationReport:
    """Deep validation. Mirror of `validate_schema` for the form builder."""
    issues: list[ValidationIssue] = []

    if spec.report_type not in ("count", "cohort", "indicator", "pivot"):
        issues.append(ValidationIssue("error", "reportType", f"Unknown report type '{spec.report_type}'."))

    # Date range must parse if a label was provided.
    if spec.date_range_label and not (spec.date_from and spec.date_to):
        try:
            d1, d2, _ = resolve_date_range(spec.date_range_label)
            if d1 is None or d2 is None:
                issues.append(
                    ValidationIssue(
                        "error",
                        "dateRangeLabel",
                        f"Date range '{spec.date_range_label}' did not resolve to a bounded range.",
                    )
                )
        except ValueError as exc:
            issues.append(ValidationIssue("error", "dateRangeLabel", str(exc)))

    if not spec.filters:
        issues.append(
            ValidationIssue(
                "error",
                "filters",
                "A report must have at least one filter. Add one with set/add_filter.",
            )
        )

    seen_filter_keys: set[tuple[str, str, str | None, str | None, float | None]] = set()
    for index, f in enumerate(spec.filters):
        path = f"filters[{index}]"
        if not f.concept_id:
            issues.append(ValidationIssue("error", f"{path}.conceptId", "Filter is missing conceptId."))
            continue
        try:
            bundle = ciel.get_concept_bundle(f.concept_id)
        except ConceptNotFoundError:
            issues.append(
                ValidationIssue("error", f"{path}.conceptId", f"Concept '{f.concept_id}' is not in the CIEL store.", "concept_not_found")
            )
            continue
        usability = _report_filter_usability_issue(bundle)
        if usability:
            issues.append(ValidationIssue("error", f"{path}.conceptId", usability, "unusable_filter_concept"))
            continue
        derived_mode = filter_mode_for_concept(bundle)
        for related_index, related_id in enumerate(_normalized_filter_concept_ids(f)[1:], 1):
            try:
                related_bundle = ciel.get_concept_bundle(related_id)
            except ConceptNotFoundError:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.conceptIds[{related_index}]",
                        f"Related concept '{related_id}' is not in the CIEL store.",
                        "concept_not_found",
                    )
                )
                continue
            related_issue = _report_filter_usability_issue(related_bundle)
            if related_issue:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.conceptIds[{related_index}]",
                        related_issue,
                        "unusable_filter_concept",
                    )
                )
                continue
            related_mode = filter_mode_for_concept(related_bundle)
            if related_mode != derived_mode and not (derived_mode == "condition" and related_mode == "value_concept"):
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.conceptIds[{related_index}]",
                        (
                            f"Related concept '{related_id}' has filter mode '{related_mode}', "
                            f"which cannot be combined with '{derived_mode}'."
                        ),
                        "filter_mode_mismatch",
                    )
                )
        # The agent may set the mode explicitly; reject mismatches.
        if (
            f.filter_mode
            and f.filter_mode != derived_mode
            and f.filter_mode != "any_value"
            and not (derived_mode == "condition" and f.filter_mode == "value_concept")
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    f"{path}.filterMode",
                    (
                        f"filterMode '{f.filter_mode}' does not match CIEL datatype "
                        f"'{(bundle.get('concept') or {}).get('datatype')}' for "
                        f"concept {f.concept_id}. Expected '{derived_mode}'."
                    ),
                    "filter_mode_mismatch",
                )
            )
            continue
        effective_mode = f.filter_mode or derived_mode

        if derived_mode == "condition":
            effective_mode = "condition"

        if effective_mode == "condition":
            pass
        elif effective_mode == "value_concept":
            # Coded / N-A clinical: must supply a valueConceptId.
            if not f.value_concept_id:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.valueConceptId",
                        (
                            "Coded / N/A-clinical filter must supply valueConceptId "
                            "(e.g. CIEL 1065 for Yes, 1066 for No, or the chosen coded answer)."
                        ),
                        "missing_filter_value",
                    )
                )
            elif (bundle.get("concept") or {}).get("datatype") == "Coded":
                allowed_answers = _answer_concept_ids(bundle)
                if allowed_answers and f.value_concept_id not in allowed_answers:
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"{path}.valueConceptId",
                            (
                                f"valueConceptId '{f.value_concept_id}' is not an answer for "
                                f"concept {f.concept_id}. Expand the concept and choose one of "
                                "its coded answers."
                            ),
                            "coded_value_not_answer",
                        )
                    )
        elif effective_mode == "value_boolean":
            if f.value_bool is None:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.valueBool",
                        "Boolean filter must supply valueBool (true or false).",
                        "missing_filter_value",
                    )
                )
        elif effective_mode == "client_numeric":
            if f.operator not in ("eq", "gt", "ge", "lt", "le"):
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.operator",
                        "Numeric filter must supply operator in {eq, gt, ge, lt, le}.",
                        "numeric_operator_missing",
                    )
                )
            if f.numeric_threshold is None:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{path}.numericThreshold",
                        "Numeric filter must supply numericThreshold (a number).",
                        "numeric_threshold_missing",
                    )
                )

        # Duplicate filter check across this spec.
        key = (
            f.concept_id,
            effective_mode,
            f.value_concept_id,
            f.value_bool if f.value_bool is None else str(f.value_bool),
            f.numeric_threshold,
        )
        if key in seen_filter_keys:
            issues.append(
                ValidationIssue(
                    "error",
                    path,
                    "Duplicate filter (same concept + value + operator combination).",
                    "duplicate_filter",
                )
            )
        seen_filter_keys.add(key)

    if spec.join_mode not in ("and", "or"):
        issues.append(
            ValidationIssue("error", "joinMode", f"Unknown join mode '{spec.join_mode}'.")
        )

    if spec.visualization is not None:
        if spec.visualization.template not in _VISUALIZATION_DEFAULT_TITLES:
            issues.append(
                ValidationIssue(
                    "warning",
                    "visualization.template",
                    (
                        f"Unknown visualization template '{spec.visualization.template}'. "
                        f"Using '{default_visualization_for_report_type(spec.report_type).template}'."
                    ),
                )
            )
        elif spec.visualization.template not in compatible_visualization_templates(spec.report_type):
            issues.append(
                ValidationIssue(
                    "warning",
                    "visualization.template",
                    (
                        f"Visualization template '{spec.visualization.template}' is not compatible "
                        f"with {spec.report_type} reports; using "
                        f"'{default_visualization_for_report_type(spec.report_type).template}'."
                    ),
                )
            )

    if spec.report_type == "indicator":
        if spec.denominator is None:
            issues.append(
                ValidationIssue(
                    "error",
                    "denominator",
                    "Indicator reports require a denominator (kind=ciel_concept or encounters_in_range).",
                    "missing_denominator",
                )
            )
        else:
            if spec.denominator.kind not in ("ciel_concept", "encounters_in_range"):
                # Explicitly reject the unsafe "all_patients_in_range" mode that
                # would force a full Observation table scan.
                issues.append(
                    ValidationIssue(
                        "error",
                        "denominator.kind",
                        (
                            f"Unsupported denominator kind '{spec.denominator.kind}'. "
                            "Use 'ciel_concept' or 'encounters_in_range'."
                        ),
                        "invalid_denominator",
                    )
                )
            elif spec.denominator.kind == "ciel_concept":
                if not spec.denominator.concept_id:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "denominator.conceptId",
                            "ciel_concept denominator must supply conceptId.",
                            "missing_denominator",
                        )
                    )
                else:
                    try:
                        ciel.get_concept_bundle(spec.denominator.concept_id)
                    except ConceptNotFoundError:
                        issues.append(
                            ValidationIssue(
                                "error",
                                "denominator.conceptId",
                                f"Denominator concept '{spec.denominator.concept_id}' is not in the CIEL store.",
                                "concept_not_found",
                            )
                        )

    if spec.report_type == "pivot":
        if not spec.group_by:
            issues.append(
                ValidationIssue(
                    "error",
                    "groupBy",
                    "Pivot reports require at least one group_by dimension (sex, age_group, date_month, or concept_id).",
                    "missing_group_by",
                )
            )
        for index, g in enumerate(spec.group_by[:2]):
            if g.dimension not in ("sex", "age_group", "date_month", "concept_id"):
                issues.append(
                    ValidationIssue(
                        "error",
                        f"groupBy[{index}].dimension",
                        f"Unknown group_by dimension '{g.dimension}'.",
                    )
                )
            if g.dimension == "concept_id" and not g.concept_id:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"groupBy[{index}].conceptId",
                        "group_by dimension=concept_id requires a conceptId.",
                    )
                )
        if len(spec.group_by) > 2:
            issues.append(
                ValidationIssue(
                    "warning",
                    "groupBy",
                    f"Only the first two group_by dimensions are used; ignoring {len(spec.group_by) - 2} extra.",
                )
            )

    return ValidationReport(issues=issues)


_QA_DISPLAY_NAME_TOKENS = (
    "missing",
    "incorrect",
    "invalid",
    "not available",
    "not applicable",
    "review needed",
    "needs review",
    "data quality",
    "data entry",
    "verification needed",
    "rejected",
    "duplicate entry",
    "error in",
)

_QA_ANSWER_TOKENS = (
    "incorrect",
    "missing",
    "invalid",
    "rejected",
    "duplicate",
    "review needed",
    "needs review",
    "data quality",
    "data entry",
    "verification needed",
    "not verified",
    "not available",
    "not applicable",
    "not specified",
    "failed",
    "error",
)


def _answer_concept_ids(bundle: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for rel in bundle.get("answers") or []:
        target = rel.get("target") or {}
        cid = str(target.get("concept_id") or target.get("id") or "").strip()
        if cid and not target.get("retired"):
            out.add(cid)
    return out


def _coded_answer_quality_issue(bundle: dict[str, Any]) -> str | None:
    concept = bundle.get("concept") or {}
    if (concept.get("datatype") or "").strip() != "Coded":
        return None
    labels = [
        str((rel.get("target") or {}).get("display_name") or "").strip().lower()
        for rel in (bundle.get("answers") or [])
    ]
    labels = [label for label in labels if label]
    if not labels:
        return None
    qa_hits = [label for label in labels if any(token in label for token in _QA_ANSWER_TOKENS)]
    informative = [
        label
        for label in labels
        if label not in qa_hits and not any(token in label for token in ("other", "unknown", "none"))
    ]
    if len(qa_hits) >= 2 and len(informative) < max(1, len(qa_hits)):
        return "Coded answer set is dominated by data-quality/workflow values, not clinical report values."
    return None


def _report_filter_usability_issue(bundle: dict[str, Any]) -> str | None:
    concept = bundle.get("concept") or {}
    if concept.get("retired"):
        return "Concept is retired and cannot be used as a report filter."
    cls = (concept.get("concept_class") or "").strip().lower()
    datatype = (concept.get("datatype") or "").strip()
    display = str(concept.get("display_name") or "").lower()
    if any(token in display for token in _QA_DISPLAY_NAME_TOKENS):
        return "Concept display name looks like a data-quality/workflow annotation."
    if bundle.get("set_members") or cls in {"convset", "labset", "medset"}:
        return "Concept is a set. Expand it and use a member concept as the filter."
    if datatype == "Coded":
        if not _answer_concept_ids(bundle):
            return "Coded concept has no non-retired answers."
        return _coded_answer_quality_issue(bundle)
    if datatype in {"Boolean", "Numeric", "Text", "Date", "Datetime", "Time", "Document"}:
        return None
    if datatype in {"N/A", ""} and cls in _NA_CLINICAL_CLASSES_FOR_REPORT:
        return None
    if cls == "diagnosis":
        return None
    return f"Datatype '{datatype or 'N/A'}' with class '{cls or 'unknown'}' is not a supported report filter."


__all__ = [
    "CompiledFilter",
    "CompiledQuery",
    "Denominator",
    "DenominatorKind",
    "FilterMode",
    "GroupBy",
    "GroupDimension",
    "JoinMode",
    "NumericOperator",
    "ReportFilter",
    "ReportSpec",
    "ReportType",
    "ReportVisualization",
    "ValidationIssue",
    "ValidationReport",
    "VisualizationTemplate",
    "compatible_visualization_templates",
    "default_visualization_for_spec",
    "default_visualization_for_report_type",
    "filter_mode_for_concept",
    "normalize_visualization",
    "resolve_date_range",
    "spec_to_query",
    "validate_spec",
]
