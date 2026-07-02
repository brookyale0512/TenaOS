"""Allow-listed tool loop for the report-builder agent.

Six tools, no more (the same shape as the form-builder loop):

    1. search_ciel_seeds     -> reused from CielClient
    2. search_related_ciel_concepts -> finds related/narrower diagnosis concepts
    3. expand_ciel_concept   -> reused from CielClient
    4. get_report_draft      -> returns spec + last_query + last_result
    5. update_report_draft   -> applies structured ops to the spec
    6. build_report_query    -> compiles the spec into a CompiledQuery,
                                resolves natural-language dates here, and
                                stores last_query
    6. run_report            -> executes the compiled query against
                                OpenMRS FHIR2 and stores last_result

Structured ops accepted by ``update_report_draft``::

    {"op": "set_report_type", "reportType": "count" | "cohort" | "indicator" | "pivot"}
    {"op": "set_date_range", "text": "last quarter"}     # NL phrase resolved on build
    {"op": "set_join_mode", "joinMode": "and" | "or"}
    {"op": "add_filter", "conceptId": "1479", "conceptIds": ["1479", "..."], "valueBool": true, "label": "Night sweats"}
    {"op": "add_filter", "conceptId": "1063", "valueConceptId": "703", "label": "HIV positive"}
    {"op": "add_filter", "conceptId": "5089", "operator": "gt", "numericThreshold": 60, "label": "Weight over 60"}
    {"op": "remove_filter", "filterId": "..."}
    {"op": "set_filter_value", "filterId": "...", "valueBool": true}
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
        "name": "search_related_ciel_concepts",
        "description": "Find related or narrower CIEL diagnosis concepts for a broad clinical diagnosis phrase. Use before add_filter for broad diagnosis requests.",
        "parameters": {"query": "string (clinical diagnosis phrase)", "limit": "integer (default 20)"},
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
                "set_date_range (natural language), set_join_mode, add_filter, remove_filter, set_filter_value, "
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
            "name": "search_related_ciel_concepts",
            "description": "Find related/narrower non-retired CIEL Diagnosis concepts for a broad diagnosis report filter. Use for requests like 'patients diagnosed with malaria' before add_filter; put selected conceptIds into one add_filter operation.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 20}},
                "required": ["query"],
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
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_report_draft",
            "description": "Apply structured spec mutations (set_report_type, set_date_range, add_filter, remove_filter, set_filter_value, set_join_mode, set_denominator, clear_denominator, add_group_by, remove_group_by, set_visualization). For broad diagnosis filters, add_filter may include conceptIds: [primary, related...] to express one logical OR filter over related CIEL concepts selected by the agent.",
            "parameters": {
                "type": "object",
                "properties": {
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
                                "conceptIds": {"type": "array", "items": {"type": "string"}},
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
                "required": ["operations"],
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
                "properties": {},
                "required": [],
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
                "properties": {},
                "required": [],
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


def _normalize_concept_id_list(value: Any, *, primary: str) -> list[str]:
    ids: list[str] = []
    for item in [primary, *(value if isinstance(value, list) else [])]:
        normalized, _ = _normalize_concept_id(item)
        if normalized and normalized not in ids:
            ids.append(normalized)
    return ids or [primary]


def _looks_like_broad_diagnosis_query(query: str) -> bool:
    lowered = str(query or "").lower()
    return "diagnos" in lowered or "diagnosed" in lowered or "cases" in lowered


def _date_range_text_from_operation(operation: dict[str, Any]) -> str:
    return str(
        operation.get("text")
        or operation.get("dateRangeLabel")
        or operation.get("dateRange")
        or operation.get("date_range")
        or ""
    ).strip()


def _first_concept_id(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            normalized, _ = _normalize_concept_id(item)
            if normalized:
                return normalized
    return ""


_NA_CLINICAL_CLASSES = {
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


def _dedupe_seed_hits(hits: list[Any]) -> list[Any]:
    by_id: dict[str, Any] = {}
    for hit in hits or []:
        cid = str(getattr(hit, "concept_id", "") or "")
        if not cid:
            continue
        existing = by_id.get(cid)
        if existing is None or float(getattr(hit, "score", 0.0) or 0.0) > float(
            getattr(existing, "score", 0.0) or 0.0
        ):
            by_id[cid] = hit
    return list(by_id.values())


def _related_diagnosis_candidates(ciel: CielClient, query: str, *, limit: int) -> list[dict[str, Any]]:
    query_text = str(query or "").strip()
    variants = _related_query_variants(query_text)
    by_id: dict[str, dict[str, Any]] = {}
    for variant in variants:
        try:
            hits = ciel.search_concepts(variant, concept_classes=["Diagnosis"], limit=max(limit, 20))
        except Exception:
            hits = []
        for hit in hits or []:
            display = str(getattr(hit, "display_name", "") or "")
            cid = str(getattr(hit, "concept_id", "") or "")
            if not cid or cid in by_id:
                continue
            if not _related_display_matches(query_text, display):
                continue
            by_id[cid] = {
                "conceptId": cid,
                "displayName": display,
                "conceptClass": getattr(hit, "concept_class", None),
                "datatype": getattr(hit, "datatype", None),
                "score": float(getattr(hit, "score", 0.0) or 0.0),
                "sourceQuery": variant,
            }
    ranked = sorted(
        by_id.values(),
        key=lambda item: _related_candidate_rank(query_text, item),
        reverse=True,
    )
    return ranked[:limit]


def _related_query_variants(query: str) -> list[str]:
    base = _diagnosis_base_phrase(query)
    variants = [query, base, f"{base} diagnosis", f"{base} complications", f"severe {base}", f"uncomplicated {base}"]
    out: list[str] = []
    for variant in variants:
        cleaned = " ".join(str(variant or "").split())
        if cleaned and cleaned.lower() not in {item.lower() for item in out}:
            out.append(cleaned)
    return out


def _diagnosis_base_phrase(query: str) -> str:
    import re

    text = re.sub(r"\b(?:patients?|cases?|diagnosed|diagnosis|with|of|report|count|cohort)\b", " ", str(query or ""), flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" .,;:")
    return text or str(query or "").strip()


def _related_display_matches(query: str, display: str) -> bool:
    base_tokens = {
        token
        for token in _diagnosis_base_phrase(query).lower().split()
        if len(token) > 2
    }
    display_lower = display.lower()
    if not base_tokens:
        return True
    return any(token in display_lower for token in base_tokens)


_LESS_SPECIFIC_DIAGNOSIS_TOKENS = (
    "suspected",
    "h/o:",
    "history of",
    "maternal",
    "pregnancy",
    "pregnant",
    "baby",
    "hiv",
    "human immunodeficiency",
    "world health organization",
)

_SPECIFIC_DISEASE_SUBTYPE_TOKENS = (
    "falciparum",
    "cerebral",
    "vivax",
    "severe",
    "uncomplicated",
    "complicated",
    "non-falciparum",
    "quartan",
    "mixed",
)


def _related_candidate_rank(query: str, item: dict[str, Any]) -> tuple[float, float]:
    """Rank CIEL diagnosis candidates before Gemma sees them.

    This is terminology shaping, not clinical hardcoding: generic uncertainty,
    history, pregnancy, and comorbidity qualifiers are demoted unless requested;
    disease subtypes are promoted for broad disease-family diagnosis requests.
    Gemma still chooses the final conceptIds.
    """
    score = float(item.get("score") or 0.0)
    display = str(item.get("displayName") or "").lower()
    query_lower = query.lower()
    base_tokens = [token for token in _diagnosis_base_phrase(query_lower).split() if len(token) > 2]

    rank = score
    if base_tokens and any(token in display for token in base_tokens):
        rank += 0.35
    if any(token in display for token in _SPECIFIC_DISEASE_SUBTYPE_TOKENS):
        rank += 0.25
    for token in _LESS_SPECIFIC_DIAGNOSIS_TOKENS:
        if token in display and token not in query_lower:
            rank -= 0.55
    # Prefer concise disease concepts over administrative/comorbidity labels.
    word_count = len(display.split())
    if word_count <= 4:
        rank += 0.1
    elif word_count >= 8:
        rank -= 0.15
    return rank, score


def _answer_concept_ids(bundle: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for rel in bundle.get("answers") or []:
        target = rel.get("target") or {}
        cid = str(target.get("concept_id") or target.get("id") or "").strip()
        if cid and not target.get("retired"):
            out.add(cid)
    return out


def _is_na_clinical_bundle(bundle: dict[str, Any]) -> bool:
    concept = bundle.get("concept") or {}
    datatype = (concept.get("datatype") or "").strip()
    cls = (concept.get("concept_class") or "").strip().lower()
    return datatype in {"N/A", ""} and cls in _NA_CLINICAL_CLASSES


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


def _is_usable_report_filter_bundle(bundle: dict[str, Any]) -> tuple[bool, str | None]:
    concept = bundle.get("concept") or {}
    if concept.get("retired"):
        return False, "Concept is retired."
    cls = (concept.get("concept_class") or "").strip().lower()
    datatype = (concept.get("datatype") or "").strip()
    display = str(concept.get("display_name") or "").lower()
    if any(token in display for token in _QA_DISPLAY_NAME_TOKENS):
        return False, "Concept display name looks like a data-quality/workflow annotation."
    if bundle.get("set_members") or cls in {"convset", "labset", "medset"}:
        return False, "Concept is a set. Expand it and use a member concept as the filter."
    if datatype == "Coded":
        if not _answer_concept_ids(bundle):
            return False, "Coded concept has no non-retired answers."
        qa_reason = _coded_answer_quality_issue(bundle)
        if qa_reason:
            return False, qa_reason
        return True, None
    if datatype in {"Boolean", "Numeric", "Text", "Date", "Datetime", "Time", "Document"}:
        return True, None
    if _is_na_clinical_bundle(bundle):
        return True, None
    if cls == "diagnosis":
        return True, None
    return False, f"Datatype '{datatype or 'N/A'}' with class '{cls or 'unknown'}' is not a supported report filter."


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
        requested_limit = max(int(limit), 20 if _looks_like_broad_diagnosis_query(query) else int(limit))
        pool_limit = max(requested_limit, 25)
        pool_seed_limit = max(min(requested_limit, 20), 10)
        try:
            primary = self.ciel.search_form_seeds(query, limit=pool_limit, seed_limit=pool_seed_limit)
        except Exception as exc:
            import logging
            logging.getLogger("tenaos.tena_agent.report").warning(
                "CIEL search_form_seeds failed for query=%r: %s", query, exc, exc_info=True
            )
            primary = []
        semantic_score: dict[str, float] = {}
        try:
            for hit in self.ciel.search_concepts(query, limit=pool_limit):
                cid = str(getattr(hit, "concept_id", "") or "")
                if cid:
                    semantic_score[cid] = float(getattr(hit, "score", 0.0) or 0.0)
        except Exception:
            semantic_score = {}
        hits = sorted(
            _dedupe_seed_hits(primary),
            key=lambda s: (
                semantic_score.get(str(getattr(s, "concept_id", "") or ""), -1.0),
                float(getattr(s, "score", 0.0) or 0.0),
            ),
            reverse=True,
        )[:requested_limit]
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

    def search_related_ciel_concepts(self, draft_id: str, *, query: str, limit: int = 20) -> dict[str, Any]:
        candidates = _related_diagnosis_candidates(self.ciel, query, limit=max(int(limit), 20))
        payload = {"query": query, "concepts": candidates}
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="search_related_ciel_concepts",
            detail=f"Searched related CIEL diagnosis concepts for '{query}' ({len(candidates)} returned)",
            payload=payload,
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
        if name == "search_related_ciel_concepts":
            draft_id = _require_str(arguments, "draftId")
            return self.search_related_ciel_concepts(
                draft_id,
                query=_require_str(arguments, "query"),
                limit=int(arguments.get("limit") or 20),
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
        operations, normalized_ops = _normalize_report_operations(list(operations or []))
        if normalized_ops:
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="operation_normalized",
                detail=f"Normalized {len(normalized_ops)} report operation(s) with missing op names.",
                payload={"normalizedOperations": normalized_ops},
            )
        operations, dropped_dupes = _dedupe_operations(operations)
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
                    text = _date_range_text_from_operation(operation)
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
                elif op_name == "set_filter_value":
                    filter_id = _require_str(operation, "filterId")
                    updated = self._apply_set_filter_value(spec, filter_id, operation)
                    applied.append({"op": op_name, "filterId": filter_id, **updated})
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
                    template = _visualization_template_from_operation(operation)
                    if not template:
                        raise ValueError("Operation 'set_visualization' requires 'template'.")
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

        _normalize_report_type_for_grouping(spec)
        self.store.update_draft(
            draft_id,
            spec=spec.to_dict(),
            report_type=spec.report_type,
            clear_last_query=bool(applied),
            clear_last_result=bool(applied),
            clear_last_run_at=bool(applied),
            status="draft" if applied else None,
        )
        self.store.append_event(
            draft_id,
            actor=actor,  # type: ignore[arg-type]
            operation="update_report_draft",
            detail=f"Applied {len(applied)} report op(s); {len(warnings)} warning(s).",
            payload={"applied": applied, "warnings": warnings, "operations": operations},
        )
        return {"applied": applied, "warnings": warnings, "spec": spec.to_dict()}

    def _apply_set_filter_value(
        self,
        spec: ReportSpec,
        filter_id: str,
        operation: dict[str, Any],
    ) -> dict[str, Any]:
        target = next((f for f in spec.filters if f.filter_id == filter_id), None)
        if target is None:
            raise ValueError(f"Filter '{filter_id}' not found.")
        bundle = self.ciel.get_concept_bundle(target.concept_id)
        mode = filter_mode_for_concept(bundle)
        if mode == "condition":
            target.value_concept_id = None
            target.value_bool = None
            target.operator = None
            target.numeric_threshold = None
            target.filter_mode = mode
            return {"filterMode": mode}
        if "valueConceptId" in operation:
            value_concept_id, _ = _normalize_concept_id(operation.get("valueConceptId"))
            allowed = _answer_concept_ids(bundle)
            if allowed and value_concept_id not in allowed:
                raise ValueError(
                    f"valueConceptId '{value_concept_id}' is not an answer for concept '{target.concept_id}'."
                )
            target.value_concept_id = value_concept_id
        elif mode == "value_concept" and target.value_concept_id is None and _is_na_clinical_bundle(bundle):
            target.value_concept_id = "1065"
        if "valueBool" in operation:
            target.value_bool = bool(operation.get("valueBool"))
        elif mode == "value_boolean" and target.value_bool is None:
            target.value_bool = True
        if "operator" in operation:
            op = str(operation.get("operator") or "").strip()
            if op not in {"eq", "gt", "ge", "lt", "le"}:
                raise ValueError("operator must be one of eq, gt, ge, lt, le.")
            target.operator = op  # type: ignore[assignment]
        if "numericThreshold" in operation:
            try:
                target.numeric_threshold = float(operation.get("numericThreshold"))
            except (TypeError, ValueError):
                raise ValueError("numericThreshold must be a number.")
        if operation.get("label"):
            target.label = str(operation.get("label")).strip()
        target.filter_mode = mode
        return {
            "filterMode": target.filter_mode,
            "valueConceptId": target.value_concept_id,
            "valueBool": target.value_bool,
            "operator": target.operator,
            "numericThreshold": target.numeric_threshold,
        }

    def _build_filter_from_op(self, operation: dict[str, Any]) -> ReportFilter:
        raw_id = str(operation.get("conceptId") or _first_concept_id(operation.get("conceptIds")) or "").strip()
        if not raw_id:
            raise ValueError("Operation 'add_filter' requires 'conceptId' or a non-empty 'conceptIds' array.")
        concept_id, was_padded = _normalize_concept_id(raw_id)
        if not concept_id:
            raise ValueError("Operation requires a valid conceptId (numeric CIEL id).")
        try:
            bundle = self.ciel.get_concept_bundle(concept_id)
        except ConceptNotFoundError as exc:
            raise ValueError(
                f"Concept '{raw_id}' is not in the CIEL store. Use a CIEL numeric id returned by search_ciel_seeds."
            ) from exc
        usable, reason = _is_usable_report_filter_bundle(bundle)
        if not usable:
            display = (bundle.get("concept") or {}).get("display_name") or concept_id
            raise ValueError(f"Concept '{concept_id}' ({display}) cannot be used as a report filter: {reason}")
        mode = filter_mode_for_concept(bundle)
        concept_ids = _normalize_concept_id_list(operation.get("conceptIds"), primary=concept_id)
        for extra_id in concept_ids[1:]:
            try:
                extra_bundle = self.ciel.get_concept_bundle(extra_id)
            except ConceptNotFoundError as exc:
                raise ValueError(
                    f"Related concept '{extra_id}' is not in the CIEL store. Use only CIEL ids returned by search/expand."
                ) from exc
            extra_usable, extra_reason = _is_usable_report_filter_bundle(extra_bundle)
            if not extra_usable:
                display = (extra_bundle.get("concept") or {}).get("display_name") or extra_id
                raise ValueError(f"Related concept '{extra_id}' ({display}) cannot be used as a report filter: {extra_reason}")
            extra_mode = filter_mode_for_concept(extra_bundle)
            if extra_mode != mode and not (mode == "condition" and extra_mode == "value_concept"):
                raise ValueError(
                    f"Related concept '{extra_id}' has filter mode '{extra_mode}', which cannot be combined with '{mode}'."
                )
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
        if mode == "value_concept" and value_concept_id is None and _is_na_clinical_bundle(bundle):
            value_concept_id = "1065"
        if mode == "value_concept" and value_concept_id is not None:
            allowed = _answer_concept_ids(bundle)
            if allowed and value_concept_id not in allowed:
                display = (bundle.get("concept") or {}).get("display_name") or concept_id
                raise ValueError(
                    f"valueConceptId '{value_concept_id}' is not an answer for concept "
                    f"'{concept_id}' ({display}). Expand the concept and choose one of its coded answers."
                )

        return ReportFilter(
            filter_id=f"f_{uuid4().hex[:8]}",
            concept_id=concept_id,
            label=label,
            concept_ids=concept_ids,
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
                self.store.update_draft(
                    draft_id,
                    spec=spec.to_dict(),
                    clear_last_query=True,
                    clear_last_result=True,
                    clear_last_run_at=True,
                    status="draft",
                )
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
        _normalize_report_type_for_grouping(spec)
        self.store.update_draft(draft_id, spec=spec.to_dict())

        report = validate_spec(spec, self.ciel)
        if not report.ok:
            payload = {"compiled": None, "validation": report.to_dict()}
            self.store.update_draft(
                draft_id,
                spec=spec.to_dict(),
                clear_last_query=True,
                clear_last_result=True,
                clear_last_run_at=True,
                status="draft",
            )
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
                code_uuids=list(f.get("codeUuids") or []),
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
                code_uuids=list(d.get("codeUuids") or []),
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
        if op in {"add_filter", "remove_filter", "set_filter_value"}:
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


def _normalize_report_operations(
    operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Recover common Gemma tool-grammar omissions without weakening validation.

    The report operation grammar requires an explicit ``op`` value, but live
    Gemma runs sometimes emit structurally clear objects like
    ``{"reportType": "pivot"}`` or ``{"conceptId": "1479"}``. We infer only
    unambiguous missing op names and leave the normal validator responsible for
    concept safety, supported dimensions, date parsing, and report semantics.
    """
    normalized: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for operation in operations or []:
        if not isinstance(operation, dict):
            continue
        op = dict(operation)
        if str(op.get("op") or "").strip():
            normalized.append(op)
            continue

        inferred: str | None = None
        if op.get("reportType"):
            inferred = "set_report_type"
        elif op.get("text") or op.get("dateRangeLabel") or op.get("dateRange") or op.get("date_range"):
            inferred = "set_date_range"
        elif op.get("joinMode"):
            inferred = "set_join_mode"
        elif op.get("template") or op.get("visualization") or _visualization_template_from_operation(op):
            inferred = "set_visualization"
        elif op.get("dimension"):
            inferred = "add_group_by"
        elif op.get("kind"):
            inferred = "set_denominator"
        elif op.get("filterId") and any(k in op for k in ("valueConceptId", "valueBool", "operator", "numericThreshold")):
            inferred = "set_filter_value"
        elif op.get("conceptId") or op.get("valueConceptId"):
            inferred = "add_filter"

        if inferred:
            op["op"] = inferred
            notes.append({"before": operation, "after": op, "reason": "missing op inferred from fields"})
        normalized.append(op)
    return normalized, notes


def _visualization_template_from_operation(operation: dict[str, Any]) -> str:
    raw = (
        operation.get("template")
        or operation.get("visualization")
        or operation.get("chartType")
        or operation.get("chart")
        or operation.get("title")
        or operation.get("reason")
    )
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "line": "time_series_line",
        "line_graph": "time_series_line",
        "line_chart": "time_series_line",
        "trend": "time_series_line",
        "bar": "time_series_bar",
        "bar_graph": "time_series_bar",
        "bar_chart": "time_series_bar",
        "heatmap": "pivot_heatmap",
        "stacked_bar": "pivot_stacked_bar",
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
    return text if text in allowed else ""


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


def _normalize_report_type_for_grouping(spec: ReportSpec) -> None:
    """Grouped count/cohort specs are semantically pivot reports.

    This is a structural invariant rather than a clinical decision. If the
    agent asks for `groupBy` but leaves reportType=count, the grouped dimensions
    would otherwise be silently ignored by the result renderer.
    """
    if spec.group_by and spec.report_type in {"count", "cohort"}:
        spec.report_type = "pivot"
        spec.visualization = normalize_visualization(spec.report_type, spec.visualization, spec.group_by)


def _filter_spec_from_compiled(f: CompiledFilter) -> FilterSpec:
    return FilterSpec(
        filter_id=f.filter_id,
        label=f.label,
        code_uuid=f.code_uuid,
        filter_mode=f.filter_mode,
        code_uuids=f.code_uuids,
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
        numerator_label = _indicator_numerator_label(result)
        denominator_label = str(result.get("denominatorLabel") or result.get("denominatorSource") or "Denominator")
        remainder = max(0, denominator - numerator)
        return {
            "xLabel": "Metric",
            "yLabel": "Patients",
            "bars": [
                {"label": numerator_label, "value": numerator},
                {"label": f"Other patients ({denominator_label})", "value": remainder},
            ],
            "numeratorLabel": numerator_label,
            "denominatorLabel": denominator_label,
            "numerator": numerator,
            "denominator": denominator,
            "remainder": remainder,
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


def _indicator_numerator_label(result: dict[str, Any]) -> str:
    labels = [
        str(item.get("label") or "").strip()
        for item in (result.get("filterCounts") or [])
        if str(item.get("label") or "").strip()
    ]
    if not labels:
        return "Matching patients"
    joiner = f" {(result.get('joinMode') or 'and').upper()} "
    return joiner.join(labels)


__all__ = [
    "REPORT_OPENAI_TOOLS",
    "REPORT_TOOL_SCHEMAS",
    "ReportBuilderToolLoop",
    "_normalize_concept_id",
]
