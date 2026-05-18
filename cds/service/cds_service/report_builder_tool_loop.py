"""Allow-listed tool loop for the report-builder agent.

Six tools, no more (the same shape as the form-builder loop):

    1. search_ciel_seeds     -> reused from CielClient
    2. expand_ciel_concept   -> reused from CielClient
    3. get_report_draft      -> returns spec + last_query + last_result
    4. update_report_draft   -> applies structured ops to the spec
    5. build_report_query    -> compiles the spec into a CompiledQuery,
                                resolves natural-language dates here, and
                                stores last_query
    6. run_report            -> executes the compiled query against
                                OpenMRS FHIR2 and stores last_result

Structured ops accepted by ``update_report_draft``::

    {"op": "set_report_type", "reportType": "count" | "cohort" | "indicator" | "pivot"}
    {"op": "set_date_range", "text": "last quarter"}     # NL phrase resolved on build
    {"op": "set_join_mode", "joinMode": "and" | "or"}
    {"op": "add_filter", "conceptId": "1479", "valueBool": true, "label": "Night sweats"}
    {"op": "add_filter", "conceptId": "1063", "valueConceptId": "703", "label": "HIV positive"}
    {"op": "add_filter", "conceptId": "5089", "operator": "gt", "numericThreshold": 60, "label": "Weight over 60"}
    {"op": "remove_filter", "filterId": "..."}
    {"op": "set_denominator", "kind": "encounters_in_range"}
    {"op": "set_denominator", "kind": "ciel_concept", "conceptId": "1063", "valueConceptId": "703"}
    {"op": "clear_denominator"}
    {"op": "add_group_by", "dimension": "sex"}
    {"op": "add_group_by", "dimension": "age_group"}
    {"op": "add_group_by", "dimension": "date_month"}
    {"op": "remove_group_by", "dimension": "sex"}
    {"op": "set_visualization", "template": "filter_bar" | "indicator_rate" | "pivot_grouped_bar" | "pivot_stacked_bar" | "pivot_heatmap" | "time_series_bar" | "time_series_line" | "stacked_time_series" | "rate_over_time", "title": "..."}

The agent never emits FHIR URLs or SQL. All concept ids are passed in the
CIEL numeric form (e.g. ``"5089"``); padded OpenMRS UUIDs are normalised
here too (mirror of the form-builder safety helper).
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, Callable
from uuid import uuid4

from .ciel import CielClient, ConceptNotFoundError
from .openmrs_reader import FilterSpec, OpenmrsReader, ProgressCallback
from .report_builder import (
    CompiledFilter,
    CompiledQuery,
    Denominator,
    GroupBy,
    ReportFilter,
    ReportSpec,
    ReportVisualization,
    filter_mode_for_concept,
    normalize_visualization,
    resolve_date_range,
    spec_to_query,
    validate_spec,
)
from .report_drafts import ReportDraft, ReportDraftStore


REPORT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_ciel_seeds",
        "description": "Search CIEL for candidate concepts (filter concepts, denominator concepts, or group_by concepts). Use this before adding anything to the report spec.",
        "parameters": {"query": "string (clinical phrase)", "limit": "integer (default 10)"},
    },
    {
        "name": "expand_ciel_concept",
        "description": "Inspect a CIEL concept's coded answers / set members before deciding whether it is the right filter target.",
        "parameters": {"conceptId": "string (CIEL numeric id)", "depth": "integer (default 2)"},
    },
    {
        "name": "get_report_draft",
        "description": "Return the current report spec, last compiled query, and last result.",
        "parameters": {"draftId": "string"},
    },
    {
        "name": "update_report_draft",
        "description": (
            "Apply structured operations to the report spec. Ops: set_report_type, "
            "set_date_range (natural language), set_join_mode, add_filter, remove_filter, "
            "set_denominator, clear_denominator, add_group_by, remove_group_by, set_visualization."
        ),
        "parameters": {"draftId": "string", "operations": "object[]"},
    },
    {
        "name": "build_report_query",
        "description": "Resolve dates, validate, and compile the spec into a deterministic FHIR query plan.",
        "parameters": {"draftId": "string"},
    },
    {
        "name": "run_report",
        "description": "Execute the compiled query and store the result snapshot.",
        "parameters": {"draftId": "string"},
    },
]


REPORT_OPENAI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_ciel_seeds",
            "description": "Search CIEL for candidate concepts. Refine until you find a Question/Finding/Test/Boolean/Coded concept that matches the user's filter intent.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expand_ciel_concept",
            "description": "Inspect a CIEL concept's answers / set members.",
            "parameters": {
                "type": "object",
                "properties": {"conceptId": {"type": "string"}, "depth": {"type": "integer", "default": 2}},
                "required": ["conceptId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_report_draft",
            "description": "Read the current spec and the last compiled query / result.",
            "parameters": {
                "type": "object",
                "properties": {"draftId": {"type": "string"}},
                "required": ["draftId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_report_draft",
            "description": "Apply structured spec mutations (set_report_type, set_date_range, add_filter, remove_filter, set_join_mode, set_denominator, clear_denominator, add_group_by, remove_group_by, set_visualization).",
            "parameters": {
                "type": "object",
                "properties": {
                    "draftId": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {"type": "string"},
                                "reportType": {"type": "string"},
                                "text": {"type": "string"},
                                "joinMode": {"type": "string"},
                                "conceptId": {"type": "string"},
                                "valueConceptId": {"type": "string"},
                                "valueBool": {"type": "boolean"},
                                "operator": {"type": "string"},
                                "numericThreshold": {"type": "number"},
                                "label": {"type": "string"},
                                "filterId": {"type": "string"},
                                "kind": {"type": "string"},
                                "dimension": {"type": "string"},
                                "template": {"type": "string"},
                                "title": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["op"],
                            "additionalProperties": True,
                        },
                    },
                },
                "required": ["draftId", "operations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_report_query",
            "description": "Resolve dates, validate the spec, compile to a FHIR query plan.",
            "parameters": {
                "type": "object",
                "properties": {"draftId": {"type": "string"}},
                "required": ["draftId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_report",
            "description": "Execute the compiled query against OpenMRS FHIR2 and store the result.",
            "parameters": {
                "type": "object",
                "properties": {"draftId": {"type": "string"}},
                "required": ["draftId"],
            },
        },
    },
]


_UUID_PADDING_RE = re.compile(r"^(\d+)A{4,}$")


def _normalize_concept_id(value: Any) -> tuple[str, bool]:
    """Coerce a concept identifier to its CIEL numeric form (mirror of form_builder_tool_loop)."""
    raw = str(value or "").strip()
    if not raw:
        return "", False
    match = _UUID_PADDING_RE.match(raw)
    if match:
        return match.group(1), True
    return raw, False


class ReportBuilderToolLoop:
    """Stateless executor for the six report-builder tools."""

    def __init__(
        self,
        *,
        store: ReportDraftStore,
        ciel: CielClient,
        reader_factory: Callable[[ProgressCallback | None], OpenmrsReader],
    ) -> None:
        self.store = store
        self.ciel = ciel
        self.reader_factory = reader_factory

    # ----- tools -----

    def search_ciel_seeds(self, draft_id: str, *, query: str, limit: int = 10) -> dict[str, Any]:
        try:
            hits = self.ciel.search_form_seeds(query, limit=limit, seed_limit=min(limit, 8))
        except Exception:
            hits = []
        payload = {"query": query, "seeds": [hit.to_dict() for hit in hits]}
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="search_ciel_seeds",
            detail=f"Searched CIEL for '{query}' ({len(hits)} returned)",
            payload=payload,
        )
        return payload

    def expand_ciel_concept(self, draft_id: str, *, concept_id: str, depth: int = 2) -> dict[str, Any]:
        try:
            expansion = self.ciel.expand_seed(concept_id, depth=depth)
        except Exception as exc:
            expansion = {"error": f"{type(exc).__name__}: {exc}"}
        payload = {"conceptId": concept_id, "depth": depth, "expansion": expansion}
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="expand_ciel_concept",
            detail=f"Expanded CIEL concept {concept_id} (depth={depth})",
            payload={
                "conceptId": concept_id,
                "depth": depth,
                "answerCount": len(expansion.get("answers", []) or []),
                "setMemberCount": len(expansion.get("setMembers", []) or []),
            },
        )
        return payload

    def get_report_draft(self, draft_id: str) -> dict[str, Any]:
        return self.store.get_draft(draft_id).to_dict()

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "search_ciel_seeds":
            draft_id = _require_str(arguments, "draftId")
            return self.search_ciel_seeds(
                draft_id,
                query=_require_str(arguments, "query"),
                limit=int(arguments.get("limit") or 10),
            )
        if name == "expand_ciel_concept":
            draft_id = _require_str(arguments, "draftId")
            return self.expand_ciel_concept(
                draft_id,
                concept_id=_require_str(arguments, "conceptId"),
                depth=int(arguments.get("depth") or 2),
            )
        if name == "get_report_draft":
            return self.get_report_draft(_require_str(arguments, "draftId"))
        if name == "update_report_draft":
            return self.update_report_draft(
                _require_str(arguments, "draftId"),
                list(arguments.get("operations") or []),
                actor="gemma",
            )
        if name == "build_report_query":
            return self.build_report_query(_require_str(arguments, "draftId"))
        if name == "run_report":
            return self.run_report(_require_str(arguments, "draftId"))
        raise ValueError(f"Unknown report-builder tool '{name}'.")

    # ----- spec mutations -----

    def update_report_draft(
        self,
        draft_id: str,
        operations: list[dict[str, Any]],
        *,
        actor: str = "gemma",
    ) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        spec = ReportSpec.from_dict(draft.spec)
        applied: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        operations, dropped_dupes = _dedupe_operations(list(operations or []))
        if dropped_dupes:
            warnings.append(
                {
                    "operation": None,
                    "reason": f"Dropped {dropped_dupes} duplicate operation(s) from this call.",
                }
            )
        for operation in operations:
            op_name = str(operation.get("op") or "")
            try:
                if op_name == "set_report_type":
                    spec.report_type = _require_str(operation, "reportType")  # type: ignore[assignment]
                    applied.append({"op": op_name, "reportType": spec.report_type})
                elif op_name == "set_date_range":
                    text = str(operation.get("text") or "").strip()
                    spec.date_range_label = text or None
                    spec.date_from = None
                    spec.date_to = None
                    applied.append({"op": op_name, "text": text})
                elif op_name == "set_join_mode":
                    join_mode = _require_str(operation, "joinMode").lower()
                    if join_mode not in ("and", "or"):
                        raise ValueError(f"join_mode must be 'and' or 'or' (got '{join_mode}').")
                    spec.join_mode = join_mode  # type: ignore[assignment]
                    applied.append({"op": op_name, "joinMode": join_mode})
                elif op_name == "add_filter":
                    new_filter = self._build_filter_from_op(operation)
                    spec.filters.append(new_filter)
                    applied.append({"op": op_name, "filterId": new_filter.filter_id, "conceptId": new_filter.concept_id, "filterMode": new_filter.filter_mode})
                elif op_name == "remove_filter":
                    filter_id = _require_str(operation, "filterId")
                    before = len(spec.filters)
                    spec.filters = [f for f in spec.filters if f.filter_id != filter_id]
                    if len(spec.filters) == before:
                        raise ValueError(f"Filter '{filter_id}' not found.")
                    applied.append({"op": op_name, "filterId": filter_id})
                elif op_name == "set_denominator":
                    kind = _require_str(operation, "kind")
                    if kind == "encounters_in_range":
                        spec.denominator = Denominator(kind=kind, label="Encounters in range")
                        applied.append({"op": op_name, "kind": kind})
                    elif kind == "ciel_concept":
                        denominator_filter = self._build_filter_from_op(operation)
                        spec.denominator = Denominator(
                            kind=kind,
                            concept_id=denominator_filter.concept_id,
                            label=denominator_filter.label,
                            value_concept_id=denominator_filter.value_concept_id,
                            value_bool=denominator_filter.value_bool,
                            operator=denominator_filter.operator,
                            numeric_threshold=denominator_filter.numeric_threshold,
                        )
                        applied.append({"op": op_name, "kind": kind, "conceptId": denominator_filter.concept_id})
                    else:
                        raise ValueError(
                            f"Unsupported denominator kind '{kind}'. Use 'ciel_concept' or 'encounters_in_range'."
                        )
                elif op_name == "clear_denominator":
                    spec.denominator = None
                    applied.append({"op": op_name})
                elif op_name == "add_group_by":
                    dimension = _require_str(operation, "dimension")
                    if dimension not in ("sex", "age_group", "date_month", "concept_id"):
                        raise ValueError(f"Unknown group_by dimension '{dimension}'.")
                    concept_id: str | None = None
                    if dimension == "concept_id":
                        raw_id = _require_str(operation, "conceptId")
                        concept_id, _ = _normalize_concept_id(raw_id)
                        if not concept_id:
                            raise ValueError("group_by concept_id requires a valid conceptId.")
                    label = str(operation.get("label") or dimension).strip()
                    group = GroupBy(dimension=dimension, concept_id=concept_id, label=label)  # type: ignore[arg-type]
                    spec.group_by = _upsert_group_by(spec.group_by, group)
                    applied.append({"op": op_name, "dimension": dimension})
                elif op_name == "remove_group_by":
                    dimension = _require_str(operation, "dimension")
                    before = len(spec.group_by)
                    spec.group_by = [g for g in spec.group_by if g.dimension != dimension]
                    if len(spec.group_by) == before:
                        raise ValueError(f"group_by dimension '{dimension}' not found.")
                    applied.append({"op": op_name, "dimension": dimension})
                elif op_name == "set_visualization":
                    template = _require_str(operation, "template")
                    title = str(operation.get("title") or "").strip()
                    reason = str(operation.get("reason") or "").strip()
                    requested = ReportVisualization(template=template, title=title, reason=reason)  # type: ignore[arg-type]
                    normalised = normalize_visualization(spec.report_type, requested, spec.group_by)
                    spec.visualization = normalised
                    applied.append({"op": op_name, **normalised.to_dict()})
                    if normalised.template != template:
                        warnings.append({"operation": operation, "reason": normalised.reason})
                else:
                    warnings.append({"operation": operation, "reason": f"Unknown op '{op_name}'."})
            except (KeyError, ValueError) as exc:
                warnings.append({"operation": operation, "reason": str(exc)})

        self.store.update_draft(draft_id, spec=spec.to_dict(), report_type=spec.report_type)
        self.store.append_event(
            draft_id,
            actor=actor,  # type: ignore[arg-type]
            operation="update_report_draft",
            detail=f"Applied {len(applied)} report op(s); {len(warnings)} warning(s).",
            payload={"applied": applied, "warnings": warnings, "operations": operations},
        )
        return {"applied": applied, "warnings": warnings, "spec": spec.to_dict()}

    def _build_filter_from_op(self, operation: dict[str, Any]) -> ReportFilter:
        raw_id = _require_str(operation, "conceptId")
        concept_id, was_padded = _normalize_concept_id(raw_id)
        if not concept_id:
            raise ValueError("Operation requires a valid conceptId (numeric CIEL id).")
        try:
            bundle = self.ciel.get_concept_bundle(concept_id)
        except ConceptNotFoundError as exc:
            raise ValueError(
                f"Concept '{raw_id}' is not in the CIEL store. Use a CIEL numeric id returned by search_ciel_seeds."
            ) from exc
        mode = filter_mode_for_concept(bundle)
        value_concept_id: str | None = operation.get("valueConceptId")
        if value_concept_id:
            value_concept_id_norm, _ = _normalize_concept_id(value_concept_id)
            value_concept_id = value_concept_id_norm or value_concept_id
        value_bool = operation.get("valueBool")
        operator = operation.get("operator")
        numeric_threshold = operation.get("numericThreshold")
        if numeric_threshold is not None:
            try:
                numeric_threshold = float(numeric_threshold)
            except (TypeError, ValueError):
                raise ValueError("numericThreshold must be a number.")
        label = str(operation.get("label") or "").strip()
        if not label:
            label = bundle.get("concept", {}).get("display_name") or concept_id

        # Datatype-driven defaults so the agent can pass a Boolean concept
        # without thinking about the value field — we infer `value_bool=true`
        # as the obvious default for "X present" semantics.
        if mode == "value_boolean" and value_bool is None:
            value_bool = True

        return ReportFilter(
            filter_id=f"f_{uuid4().hex[:8]}",
            concept_id=concept_id,
            label=label,
            filter_mode=mode,
            value_concept_id=value_concept_id,
            value_bool=value_bool,
            operator=operator,  # type: ignore[arg-type]
            numeric_threshold=numeric_threshold,
        )

    # ----- build + run -----

    def build_report_query(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        spec = ReportSpec.from_dict(draft.spec)
        # Resolve date range if a label is present and we don't already have absolutes.
        if spec.date_range_label and not (spec.date_from and spec.date_to):
            try:
                d1, d2, _label = resolve_date_range(spec.date_range_label)
                if d1 and d2:
                    spec.date_from = d1.isoformat()
                    spec.date_to = d2.isoformat()
            except ValueError as exc:
                self.store.append_event(
                    draft_id,
                    actor="middleware",
                    operation="build_report_query_failed",
                    detail=f"Date range '{spec.date_range_label}' did not resolve: {exc}",
                    payload={"error": str(exc)},
                )
                return {
                    "compiled": None,
                    "validation": {
                        "issues": [
                            {"severity": "error", "path": "dateRangeLabel", "message": str(exc)}
                        ]
                    },
                }
        # Persist resolved dates back so subsequent reruns reuse them.
        self.store.update_draft(draft_id, spec=spec.to_dict())

        report = validate_spec(spec, self.ciel)
        if not report.ok:
            payload = {"compiled": None, "validation": report.to_dict()}
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="build_report_query_invalid",
                detail=f"Spec validation produced {len(report.issues)} issue(s).",
                payload=payload,
            )
            return payload

        compiled = spec_to_query(spec, self.ciel)
        last_query = compiled.to_dict()
        self.store.update_draft(draft_id, last_query=last_query)
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="build_report_query",
            detail=(
                f"Compiled {spec.report_type} report with {len(spec.filters)} filter(s); "
                f"date range {spec.date_from or '?'}..{spec.date_to or '?'} ({spec.date_range_label or 'no label'})."
            ),
            payload={"compiled": last_query, "validation": report.to_dict()},
        )
        return {"compiled": last_query, "validation": report.to_dict()}

    def run_report(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        if not draft.last_query:
            built = self.build_report_query(draft_id)
            if not built.get("compiled"):
                return {"success": False, "error": "Query has validation errors; cannot run.", "validation": built.get("validation")}
            draft = self.store.get_draft(draft_id)

        compiled = draft.last_query or {}
        report_type = compiled.get("reportType") or draft.report_type
        date_from = compiled.get("dateFrom")
        date_to = compiled.get("dateTo")
        join_mode = compiled.get("joinMode") or "and"
        filters_compiled = [
            CompiledFilter(
                filter_id=f["filterId"],
                label=f["label"],
                code_uuid=f["codeUuid"],
                filter_mode=f["filterMode"],
                value_concept_uuid=f.get("valueConceptUuid"),
                value_bool=f.get("valueBool"),
                operator=f.get("operator"),
                numeric_threshold=f.get("numericThreshold"),
            )
            for f in (compiled.get("filters") or [])
        ]
        denominator_compiled: CompiledFilter | None = None
        if compiled.get("denominator"):
            d = compiled["denominator"]
            denominator_compiled = CompiledFilter(
                filter_id=d["filterId"],
                label=d["label"],
                code_uuid=d["codeUuid"],
                filter_mode=d["filterMode"],
                value_concept_uuid=d.get("valueConceptUuid"),
                value_bool=d.get("valueBool"),
                operator=d.get("operator"),
                numeric_threshold=d.get("numericThreshold"),
            )
        visualization = ReportVisualization.from_dict(compiled.get("visualization"))

        progress = self._make_progress_callback(draft_id)
        reader = self.reader_factory(progress)

        self.store.update_draft(draft_id, status="running")
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="run_report_started",
            detail=f"Running {report_type} report with {len(filters_compiled)} filter(s).",
            payload={"reportType": report_type, "dateFrom": date_from, "dateTo": date_to},
        )

        try:
            # Per-filter patient id sets.
            per_filter_ids: list[list[str]] = []
            per_filter_months: list[dict[str, list[str]]] = []
            group_by = compiled.get("groupBy") or []
            needs_date_month = any(g.get("dimension") == "date_month" for g in group_by) or (
                visualization is not None and visualization.template == "rate_over_time"
            )
            for f in filters_compiled:
                self._emit_progress(draft_id, "fetching_filter", {"filterId": f.filter_id, "label": f.label})
                ids = reader.observation_patient_ids(
                    _filter_spec_from_compiled(f), date_from=date_from, date_to=date_to
                )
                per_filter_ids.append(ids)
                if needs_date_month:
                    self._emit_progress(draft_id, "fetching_filter_months", {"filterId": f.filter_id, "label": f.label})
                    per_filter_months.append(
                        reader.observation_patient_months(
                            _filter_spec_from_compiled(f), date_from=date_from, date_to=date_to
                        )
                    )

            joined_ids = _join_patient_ids(per_filter_ids, join_mode)
            patient_months = _join_patient_months(per_filter_months, joined_ids, join_mode) if needs_date_month else {}

            result: dict[str, Any] = {
                "reportType": report_type,
                "dateFrom": date_from,
                "dateTo": date_to,
                "dateRangeLabel": compiled.get("dateRangeLabel"),
                "joinMode": join_mode,
                "filterCounts": [
                    {"filterId": f.filter_id, "label": f.label, "count": len(ids)}
                    for f, ids in zip(filters_compiled, per_filter_ids)
                ],
            }

            if report_type == "count":
                result["total"] = len(joined_ids)
            elif report_type == "cohort":
                # Cap at 500 patients (per the plan).
                capped = joined_ids[:500]
                truncated = len(joined_ids) > 500
                self._emit_progress(draft_id, "fetching_demographics", {"count": len(capped)})
                demographics = reader.patient_demographics(capped) if capped else {}
                patients = [
                    {
                        "uuid": uuid,
                        "displayName": (demographics.get(uuid) or {}).get("display_name") or "(no name)",
                        "gender": (demographics.get(uuid) or {}).get("gender"),
                        "birthdate": (demographics.get(uuid) or {}).get("birthdate"),
                    }
                    for uuid in capped
                ]
                result.update({"total": len(joined_ids), "patients": patients, "truncated": truncated})
            elif report_type == "indicator":
                numerator = len(joined_ids)
                denominator = 0
                denominator_source = compiled.get("denominatorKind")
                if denominator_source == "encounters_in_range":
                    self._emit_progress(draft_id, "fetching_denominator", {"kind": denominator_source})
                    denominator_ids = reader.encounter_patient_ids(date_from=date_from, date_to=date_to)
                    denominator = len(denominator_ids)
                elif denominator_source == "ciel_concept" and denominator_compiled is not None:
                    self._emit_progress(draft_id, "fetching_denominator", {"kind": denominator_source})
                    denominator_ids = reader.observation_patient_ids(
                        _filter_spec_from_compiled(denominator_compiled), date_from=date_from, date_to=date_to
                    )
                    denominator = len(denominator_ids)
                rate = (numerator / denominator * 100.0) if denominator > 0 else None
                result.update(
                    {
                        "numerator": numerator,
                        "denominator": denominator,
                        "rate": rate,
                        "denominatorSource": denominator_source,
                        "denominatorLabel": (denominator_compiled.label if denominator_compiled else "Encounters in range"),
                    }
                )
                if visualization is not None and visualization.template == "rate_over_time":
                    denominator_months: dict[str, list[str]] = {}
                    if denominator_source == "encounters_in_range":
                        self._emit_progress(draft_id, "fetching_denominator_months", {"kind": denominator_source})
                        denominator_months = reader.encounter_patient_months(date_from=date_from, date_to=date_to)
                    elif denominator_source == "ciel_concept" and denominator_compiled is not None:
                        self._emit_progress(draft_id, "fetching_denominator_months", {"kind": denominator_source})
                        denominator_months = reader.observation_patient_months(
                            _filter_spec_from_compiled(denominator_compiled), date_from=date_from, date_to=date_to
                        )
                    result["rateSeries"] = _rate_series(
                        date_from=date_from,
                        date_to=date_to,
                        numerator_months=patient_months,
                        denominator_months=denominator_months,
                    )
            elif report_type == "pivot":
                self._emit_progress(draft_id, "fetching_demographics", {"count": len(joined_ids)})
                demographics = reader.patient_demographics(joined_ids) if joined_ids else {}
                grid = _pivot_grid(
                    joined_ids,
                    demographics,
                    group_by,
                    date_from=date_from,
                    date_to=date_to,
                    patient_months=patient_months,
                )
                result["pivot"] = grid
            else:
                result["error"] = f"Unsupported report_type '{report_type}'."

            result["visualization"] = _visualization_payload(
                report_type=report_type,
                result=result,
                requested=visualization,
            )

            self.store.update_draft(
                draft_id,
                status="ready",
                last_result=result,
                last_run_at=_utc_iso(),
            )
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="run_report_completed",
                detail=_run_summary(result),
                payload={"result": result},
            )
            return {"success": True, "result": result}
        except Exception as exc:
            error_detail = f"{type(exc).__name__}: {exc}"
            self.store.update_draft(draft_id, status="failed")
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="run_report_failed",
                detail=error_detail,
                payload={"error": error_detail},
            )
            return {"success": False, "error": error_detail}

    # ----- progress -----

    def _make_progress_callback(self, draft_id: str) -> ProgressCallback:
        def _cb(stage: str, payload: dict[str, Any]) -> None:
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="run_report_progress",
                detail=f"Progress: {stage}",
                payload={"stage": stage, **payload},
            )

        return _cb

    def _emit_progress(self, draft_id: str, stage: str, payload: dict[str, Any]) -> None:
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="run_report_progress",
            detail=f"Progress: {stage}",
            payload={"stage": stage, **payload},
        )


# ---------------------------------------------------------------------------
# Helpers


def _utc_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _require_str(op: dict[str, Any], key: str) -> str:
    value = op.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Operation '{op.get('op')}' requires '{key}'.")
    return value.strip()


def _dedupe_operations(operations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Collapse repeated ops on the same target (mirror of form-builder helper)."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    dropped = 0
    for operation in operations or []:
        if not isinstance(operation, dict):
            continue
        op = str(operation.get("op") or "")
        key: tuple[Any, ...]
        if op in {"add_filter", "remove_filter"}:
            key = (
                op,
                str(operation.get("conceptId") or operation.get("filterId") or ""),
                str(operation.get("valueConceptId") or ""),
                str(operation.get("valueBool")),
                str(operation.get("operator") or ""),
                str(operation.get("numericThreshold")),
            )
        elif op in {"add_group_by", "remove_group_by"}:
            key = (op, str(operation.get("dimension") or ""), str(operation.get("conceptId") or ""))
        elif op in {"set_report_type", "set_date_range", "set_join_mode", "set_denominator", "clear_denominator", "set_visualization"}:
            key = (op,)
        else:
            out.append(operation)
            continue
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(operation)
    return out, dropped


def _upsert_group_by(existing: list[GroupBy], new_group: GroupBy) -> list[GroupBy]:
    out: list[GroupBy] = []
    seen: set[tuple[str, str | None]] = set()
    for group in [*existing, new_group]:
        key = (group.dimension, group.concept_id if group.dimension == "concept_id" else None)
        if key in seen:
            out = [item for item in out if (item.dimension, item.concept_id if item.dimension == "concept_id" else None) != key]
        seen.add(key)
        out.append(group)
    return out[-2:]


def _filter_spec_from_compiled(f: CompiledFilter) -> FilterSpec:
    return FilterSpec(
        filter_id=f.filter_id,
        label=f.label,
        code_uuid=f.code_uuid,
        filter_mode=f.filter_mode,
        value_concept_uuid=f.value_concept_uuid,
        value_bool=f.value_bool,
        operator=f.operator,
        numeric_threshold=f.numeric_threshold,
    )


def _join_patient_ids(per_filter_ids: list[list[str]], join_mode: str) -> list[str]:
    if not per_filter_ids:
        return []
    if join_mode == "or":
        seen: set[str] = set()
        out: list[str] = []
        for ids in per_filter_ids:
            for uuid in ids:
                if uuid not in seen:
                    seen.add(uuid)
                    out.append(uuid)
        return out
    # default: and
    if len(per_filter_ids) == 1:
        return list(per_filter_ids[0])
    common: set[str] = set(per_filter_ids[0])
    for ids in per_filter_ids[1:]:
        common &= set(ids)
        if not common:
            break
    # preserve order from the first filter
    return [uuid for uuid in per_filter_ids[0] if uuid in common]


def _join_patient_months(
    per_filter_months: list[dict[str, list[str]]],
    joined_ids: list[str],
    join_mode: str,
) -> dict[str, list[str]]:
    if not per_filter_months:
        return {}
    joined = set(joined_ids)
    out: dict[str, list[str]] = {}
    if join_mode == "or":
        for months_by_patient in per_filter_months:
            for patient_id, months in months_by_patient.items():
                if patient_id not in joined:
                    continue
                bucket = out.setdefault(patient_id, [])
                for month in months:
                    if month not in bucket:
                        bucket.append(month)
    else:
        for patient_id in joined_ids:
            month_sets = [set(months_by_patient.get(patient_id) or []) for months_by_patient in per_filter_months]
            common = set.intersection(*month_sets) if month_sets else set()
            out[patient_id] = sorted(common)
    for months in out.values():
        months.sort()
    return out


def _rate_series(
    *,
    date_from: str | None,
    date_to: str | None,
    numerator_months: dict[str, list[str]],
    denominator_months: dict[str, list[str]],
) -> list[dict[str, Any]]:
    months = _month_range_labels(date_from, date_to)
    if not months:
        months = sorted({month for values in [*numerator_months.values(), *denominator_months.values()] for month in values})
    series = []
    for month in months:
        numerator = sum(1 for values in numerator_months.values() if month in values)
        denominator = sum(1 for values in denominator_months.values() if month in values)
        rate = (numerator / denominator * 100.0) if denominator > 0 else None
        series.append({"period": month, "numerator": numerator, "denominator": denominator, "rate": rate})
    return series


_AGE_BUCKETS: list[tuple[str, int, int]] = [
    ("<5", 0, 4),
    ("5-14", 5, 14),
    ("15-24", 15, 24),
    ("25-49", 25, 49),
    ("50+", 50, 200),
]


def _age_bucket(birthdate: str | None, reference_iso: str | None) -> str:
    if not birthdate:
        return "(unknown)"
    try:
        from datetime import date as _date

        bd = _date.fromisoformat(birthdate)
    except Exception:
        return "(unknown)"
    if reference_iso:
        try:
            from datetime import date as _date

            ref = _date.fromisoformat(reference_iso)
        except Exception:
            from datetime import date as _date

            ref = _date.today()
    else:
        from datetime import date as _date

        ref = _date.today()
    age = ref.year - bd.year - ((ref.month, ref.day) < (bd.month, bd.day))
    for label, lo, hi in _AGE_BUCKETS:
        if lo <= age <= hi:
            return label
    return "(unknown)"


def _pivot_grid(
    patient_ids: list[str],
    demographics: dict[str, dict[str, Any]],
    group_by: list[dict[str, Any]],
    *,
    date_from: str | None,
    date_to: str | None = None,
    patient_months: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    if not group_by:
        return {"rowLabels": [], "colLabels": [], "cells": []}
    dimensions = group_by[:2]
    row_keys: list[str] = []
    col_keys: list[str] = []
    counts: dict[tuple[str, str], int] = {}
    patient_months = patient_months or {}
    if dimensions[0].get("dimension") == "date_month":
        row_keys.extend(_month_range_labels(date_from, date_to))
    if len(dimensions) > 1 and dimensions[1].get("dimension") == "date_month":
        col_keys.extend(_month_range_labels(date_from, date_to))
    for uuid in patient_ids:
        demo = demographics.get(uuid) or {}
        rows = _buckets_for_dimension(demo, dimensions[0], date_from, patient_months.get(uuid))
        cols = (
            _buckets_for_dimension(demo, dimensions[1], date_from, patient_months.get(uuid))
            if len(dimensions) > 1
            else ["Count"]
        )
        for row in rows:
            for col in cols:
                if row not in row_keys:
                    row_keys.append(row)
                if col not in col_keys:
                    col_keys.append(col)
                counts[(row, col)] = counts.get((row, col), 0) + 1

    # Stable ordering: age_group uses canonical bucket order, sex prefers female first, months are chronological.
    row_keys = _sort_dimension_labels(row_keys, dimensions[0])
    col_keys = _sort_dimension_labels(col_keys, dimensions[1] if len(dimensions) > 1 else {"dimension": "count"})

    cells = [[counts.get((row, col), 0) for col in col_keys] for row in row_keys]
    return {"rowLabels": row_keys, "colLabels": col_keys, "cells": cells}


def _buckets_for_dimension(
    demo: dict[str, Any],
    dimension: dict[str, Any],
    reference_iso: str | None,
    months: list[str] | None,
) -> list[str]:
    if dimension.get("dimension") == "date_month":
        return list(months or ["(unknown month)"])
    return [_bucket_for_dimension(demo, dimension, reference_iso)]


def _bucket_for_dimension(demo: dict[str, Any], dimension: dict[str, Any], reference_iso: str | None) -> str:
    dim = dimension.get("dimension")
    if dim == "sex":
        gender = (demo.get("gender") or "").lower()
        return {"male": "Male", "female": "Female"}.get(gender, "Other/Unknown")
    if dim == "age_group":
        return _age_bucket(demo.get("birthdate"), reference_iso)
    if dim == "concept_id":
        # Not implemented in v1: would require fetching the per-patient value
        # of that concept inside the same date range. Bucket as "(n/a)".
        return "(concept_id pivot not implemented in v1)"
    if dim == "date_month":
        return "(unknown month)"
    return "Other"


def _sort_dimension_labels(labels: list[str], dimension: dict[str, Any]) -> list[str]:
    dim = dimension.get("dimension")
    if dim == "age_group":
        order = [b[0] for b in _AGE_BUCKETS] + ["(unknown)"]
        return [label for label in order if label in labels] + [label for label in labels if label not in order]
    if dim == "sex":
        order = ["Female", "Male", "Other/Unknown"]
        return [label for label in order if label in labels]
    if dim == "date_month":
        return sorted(labels)
    return labels


def _month_range_labels(date_from: str | None, date_to: str | None) -> list[str]:
    if not date_from or not date_to:
        return []
    try:
        from datetime import date as _date

        start = _date.fromisoformat(date_from[:10]).replace(day=1)
        end = _date.fromisoformat(date_to[:10]).replace(day=1)
    except Exception:
        return []
    if start > end:
        start, end = end, start
    labels: list[str] = []
    year = start.year
    month = start.month
    while (year, month) <= (end.year, end.month):
        labels.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return labels


def _visualization_payload(
    *,
    report_type: str,
    result: dict[str, Any],
    requested: ReportVisualization | None,
) -> dict[str, Any]:
    visualization = normalize_visualization(report_type, requested)  # type: ignore[arg-type]
    payload = visualization.to_dict()
    payload["data"] = _visualization_data(visualization.template, result)
    return payload


def _visualization_data(template: str, result: dict[str, Any]) -> dict[str, Any]:
    if template == "rate_over_time":
        return {
            "xLabel": "Month",
            "yLabel": "Rate (%)",
            "points": list(result.get("rateSeries") or []),
        }
    if template == "indicator_rate":
        numerator = int(result.get("numerator") or 0)
        denominator = int(result.get("denominator") or 0)
        return {
            "xLabel": "Metric",
            "yLabel": "Patients",
            "bars": [
                {"label": "Numerator", "value": numerator},
                {"label": "Denominator", "value": denominator},
            ],
            "rate": result.get("rate"),
        }
    if template in {"pivot_grouped_bar", "pivot_stacked_bar", "pivot_heatmap", "stacked_time_series"}:
        pivot = result.get("pivot") or {}
        row_labels = list(pivot.get("rowLabels") or [])
        col_labels = list(pivot.get("colLabels") or [])
        cells = list(pivot.get("cells") or [])
        rows = []
        for row_index, row_label in enumerate(row_labels):
            cell_row = cells[row_index] if row_index < len(cells) and isinstance(cells[row_index], list) else []
            rows.append(
                {
                    "label": row_label,
                    "values": [
                        {
                            "label": col_label,
                            "value": int(cell_row[col_index] if col_index < len(cell_row) else 0),
                        }
                        for col_index, col_label in enumerate(col_labels)
                    ],
                }
            )
        max_cell = max((value["value"] for row in rows for value in row["values"]), default=0)
        return {
            "xLabel": "Group",
            "yLabel": "Patients",
            "rowLabels": row_labels,
            "colLabels": col_labels,
            "rows": rows,
            "maxCell": max_cell,
        }
    if template in {"time_series_bar", "time_series_line"}:
        pivot = result.get("pivot") or {}
        row_labels = list(pivot.get("rowLabels") or [])
        cells = list(pivot.get("cells") or [])
        points = []
        for row_index, label in enumerate(row_labels):
            cell_row = cells[row_index] if row_index < len(cells) and isinstance(cells[row_index], list) else []
            points.append({"period": label, "value": int(cell_row[0] if cell_row else 0)})
        return {
            "xLabel": "Month",
            "yLabel": "Patients",
            "points": points,
        }
    return {
        "xLabel": "Filter",
        "yLabel": "Patients",
        "bars": [
            {"label": str(item.get("label") or "Filter"), "value": int(item.get("count") or 0)}
            for item in (result.get("filterCounts") or [])
        ],
        "total": result.get("total"),
    }


def _run_summary(result: dict[str, Any]) -> str:
    report_type = result.get("reportType")
    if report_type == "count":
        return f"count: total={result.get('total', 0)}"
    if report_type == "cohort":
        return f"cohort: total={result.get('total', 0)} (showing {len(result.get('patients') or [])})"
    if report_type == "indicator":
        rate = result.get("rate")
        rate_str = f"{rate:.1f}%" if isinstance(rate, (int, float)) else "n/a"
        return f"indicator: {result.get('numerator', 0)}/{result.get('denominator', 0)} = {rate_str}"
    if report_type == "pivot":
        pivot = result.get("pivot") or {}
        rows = pivot.get("rowLabels") or []
        cols = pivot.get("colLabels") or []
        return f"pivot: {len(rows)} rows x {len(cols)} cols"
    return f"report: {report_type}"


__all__ = [
    "REPORT_OPENAI_TOOLS",
    "REPORT_TOOL_SCHEMAS",
    "ReportBuilderToolLoop",
    "_normalize_concept_id",
]
