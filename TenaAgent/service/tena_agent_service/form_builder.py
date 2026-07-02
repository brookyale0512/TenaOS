"""Deterministic OpenMRS O3 form schema assembler.

This module is the trust boundary between the Gemma-driven agent loop and
OpenMRS. The agent reasons over a `ConceptBasket` (an ordered list of
sections, each with an ordered list of CIEL concept ids). This module:

    1. Resolves every concept id to a CIEL bundle.
    2. Picks a deterministic rendering for each field from the CIEL datatype
       and answer count.
    3. Emits a full FormSchema JSON conforming to the O3 form shape rendered
       by the existing frontend FormRenderer.
    4. Validates the result before any OpenMRS write.

The agent never produces JSON. It only adds, removes, reorders, or labels
items in the basket. Every schema rebuild starts from the basket, so the
agent cannot drift across turns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .ciel import CielClient, ConceptNotFoundError, RetiredConceptError, openmrs_uuid_for_concept_id


SectionKind = Literal["section_concept", "container"]


@dataclass
class BasketField:
    """A single field reference inside a basket section.

    `concept_id` is the CIEL numeric id. `label_override`, when set, replaces
    the CIEL display name in the rendered form. `required` toggles validation.
    `rendering_override` is reserved for the post-publish-v1 path where the
    user explicitly requests a non-default rendering for a question.
    """

    concept_id: str
    label_override: str | None = None
    required: bool = False
    rendering_override: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conceptId": self.concept_id,
            "labelOverride": self.label_override,
            "required": self.required,
            "renderingOverride": self.rendering_override,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BasketField":
        return cls(
            concept_id=str(data["conceptId"]),
            label_override=data.get("labelOverride") or None,
            required=bool(data.get("required", False)),
            rendering_override=data.get("renderingOverride") or None,
        )


@dataclass
class BasketSection:
    """A section in the concept basket.

    A section can be anchored to a CIEL set concept (`kind="section_concept"`)
    or be a free container (`kind="container"`). Set-anchored sections have
    their label derived from the CIEL display name unless overridden.
    """

    section_id: str
    label: str
    fields: list[BasketField] = field(default_factory=list)
    concept_id: str | None = None
    kind: SectionKind = "container"
    is_expanded: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "sectionId": self.section_id,
            "label": self.label,
            "fields": [field_.to_dict() for field_ in self.fields],
            "conceptId": self.concept_id,
            "kind": self.kind,
            "isExpanded": self.is_expanded,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BasketSection":
        return cls(
            section_id=str(data["sectionId"]),
            label=str(data["label"]),
            fields=[BasketField.from_dict(f) for f in (data.get("fields") or [])],
            concept_id=str(data["conceptId"]) if data.get("conceptId") else None,
            kind=data.get("kind") or ("section_concept" if data.get("conceptId") else "container"),
            is_expanded=bool(data.get("isExpanded", True)),
        )


@dataclass
class ConceptBasket:
    """The complete agent-mutable state for a form draft.

    The basket is normalized JSON: sections in order, fields in order. No
    JSON Schema fragments live here; the schema is always re-derived in
    `basket_to_schema`.
    """

    sections: list[BasketSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"sections": [section.to_dict() for section in self.sections]}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ConceptBasket":
        if not data:
            return cls()
        sections = [BasketSection.from_dict(section) for section in (data.get("sections") or [])]
        return cls(sections=sections)

    @classmethod
    def from_json(cls, payload: str | None) -> "ConceptBasket":
        if not payload:
            return cls()
        return cls.from_dict(json.loads(payload))


@dataclass(frozen=True)
class FormMeta:
    name: str
    version: str
    description: str | None
    encounter_type_uuid: str


@dataclass
class ValidationIssue:
    severity: Literal["error", "warning"]
    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "path": self.path, "message": self.message}


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"issues": [issue.to_dict() for issue in self.issues]}

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


# ---------------------------------------------------------------------------
# Rendering decision: CIEL datatype + answer count -> O3 rendering string.
# This is a stable table; the agent never overrides it without an explicit
# `rendering_override` set by a structured operation.

_RENDERING_BY_DATATYPE: dict[str, str] = {
    "Numeric": "number",
    "Text": "text",
    "Date": "date",
    "Datetime": "datetime",
    "Time": "time",
    "Document": "file",
}

# Canonical CIEL Yes/No concepts. Using their padded OpenMRS UUIDs means the
# publish-time preflight finds them in OpenMRS (after `_seed_concept_for_field`
# has seeded 1065/1066), and the FormFillPage already understands these UUIDs
# (see `FormFillPage.normalizeObsValue`). Using literal "true"/"false" strings
# caused publish to fail with "missing in OpenMRS: Yes, No" because those are
# not real concept references.
_BOOLEAN_ANSWERS = [
    {"concept": openmrs_uuid_for_concept_id("1065"), "label": "Yes"},
    {"concept": openmrs_uuid_for_concept_id("1066"), "label": "No"},
]

# CIEL classes that, with N/A datatype, render as Yes/No form questions
# rather than free text. Keep this aligned with
# `form_builder_tool_loop._NA_CLINICAL_CLASSES`.
_NA_BOOLEAN_CLASSES = {
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


def _is_na_boolean_concept(datatype: str | None, concept_class: str | None) -> bool:
    if datatype not in {None, "", "N/A"}:
        return False
    return (concept_class or "").lower() in _NA_BOOLEAN_CLASSES


def _rendering_for(datatype: str | None, answer_count: int, concept_class: str | None = None) -> str:
    if datatype == "Boolean":
        return "radio"
    if datatype == "Coded":
        return "radio" if 0 < answer_count <= 6 else "select"
    if datatype in _RENDERING_BY_DATATYPE:
        return _RENDERING_BY_DATATYPE[datatype]
    if _is_na_boolean_concept(datatype, concept_class):
        return "radio"
    return "text"


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str, fallback: str) -> str:
    cleaned = _SLUG_RE.sub("_", value.lower()).strip("_")
    return cleaned or fallback


# ---------------------------------------------------------------------------
# Basket -> Schema


def basket_to_schema(
    basket: ConceptBasket,
    meta: FormMeta,
    ciel: CielClient,
    *,
    unresolved_out: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a full O3 FormSchema dict from the basket.

    Every CIEL concept reference is resolved to its bundle so the schema
    always contains: canonical OpenMRS UUID, canonical display label, and
    coded answers (for `Coded` datatype) drawn from the bundle's answer
    relations.

    If ``unresolved_out`` is provided, any basket field whose concept could not
    be resolved in CIEL is appended to it (instead of being silently dropped)
    so the caller can surface it to the clinician as a validation warning.

    Returns a JSON-serialisable dict shaped per
    `frontend/src/types/forms.ts:FormSchema`.
    """
    questions_seen: set[str] = set()
    sections_json: list[dict[str, Any]] = []
    for section_index, section in enumerate(basket.sections):
        section_questions: list[dict[str, Any]] = []
        for field_index, basket_field in enumerate(section.fields):
            question = _field_to_question(
                basket_field, section_index, field_index, ciel, questions_seen, unresolved_out
            )
            if question is not None:
                section_questions.append(question)
        if not section_questions:
            continue
        sections_json.append(
            {
                "id": _slug(section.section_id, f"section_{section_index + 1}"),
                "label": section.label or f"Section {section_index + 1}",
                "isExpanded": bool(section.is_expanded),
                "questions": section_questions,
            }
        )

    schema = {
        "name": meta.name,
        "version": meta.version,
        "description": meta.description or "",
        "encounterType": meta.encounter_type_uuid,
        "processor": "EncounterFormProcessor",
        "published": False,
        "pages": [
            {
                "id": "page-1",
                "label": meta.name,
                "sections": sections_json,
            }
        ],
        "referencedForms": [],
    }
    return schema


def _field_to_question(
    basket_field: BasketField,
    section_index: int,
    field_index: int,
    ciel: CielClient,
    questions_seen: set[str],
    unresolved_out: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    try:
        bundle = ciel.get_concept_bundle(basket_field.concept_id)
    except ConceptNotFoundError:
        if unresolved_out is not None:
            unresolved_out.append(
                {
                    "conceptId": str(basket_field.concept_id),
                    "label": basket_field.label_override or str(basket_field.concept_id),
                    "sectionIndex": section_index,
                    "reason": "Concept not found in CIEL",
                }
            )
        return None
    concept = bundle.get("concept", {}) or {}
    if concept.get("retired"):
        # Skip retired concepts; validate_schema will surface them as errors.
        pass
    display_name = concept.get("display_name") or str(basket_field.concept_id)
    datatype = concept.get("datatype")
    concept_class = concept.get("concept_class")
    answer_relations = bundle.get("answers") or []
    answer_count = len(answer_relations)
    rendering = basket_field.rendering_override or _rendering_for(datatype, answer_count, concept_class)

    question_id = _slug(basket_field.label_override or display_name, f"q_{section_index + 1}_{field_index + 1}")
    # Ensure global uniqueness within the form (O3 requires it).
    deduplicated = question_id
    suffix = 1
    while deduplicated in questions_seen:
        suffix += 1
        deduplicated = f"{question_id}_{suffix}"
    questions_seen.add(deduplicated)

    options: dict[str, Any] = {
        "rendering": rendering,
        "concept": openmrs_uuid_for_concept_id(basket_field.concept_id),
        "datatype": datatype,
    }
    if datatype == "Boolean" or _is_na_boolean_concept(datatype, concept_class):
        # N/A clinical concepts (Diagnosis, Symptom, Finding, Procedure, etc.)
        # render as Yes/No questions: "Does the patient have <concept>?".
        # This is the standard OpenMRS pattern for symptom/diagnosis presence.
        options["answers"] = list(_BOOLEAN_ANSWERS)
    elif datatype == "Coded":
        # Never publish retired answer concepts: they cannot be selected safely
        # in OpenMRS and the apply-time filter already rejects such concepts.
        options["answers"] = [
            {
                "concept": openmrs_uuid_for_concept_id(rel.get("target", {}).get("concept_id", "")),
                "label": rel.get("target", {}).get("display_name") or str(rel.get("target", {}).get("concept_id", "")),
            }
            for rel in answer_relations
            if rel.get("target", {}).get("concept_id") and not rel.get("target", {}).get("retired")
        ]

    # Carry CIEL `extras` (low_absolute, hi_absolute, units, allow_decimal) onto
    # the question. The frontend NumberQuestion reads min/max/unit straight from
    # questionOptions. We never invent these values — they come from CIEL.
    extras = concept.get("extras") or {}
    if datatype == "Numeric":
        low = extras.get("low_absolute")
        high = extras.get("hi_absolute")
        if low is not None:
            options["min"] = low
        if high is not None:
            options["max"] = high
        units = extras.get("units")
        if units:
            options["unit"] = units
        # allow_decimal -> step. False means integer-only.
        if extras.get("allow_decimal") is False:
            options["step"] = 1

    # Use the first English description as tooltip text for any datatype.
    descriptions = concept.get("descriptions") or []
    for entry in descriptions:
        if (entry.get("locale") or "").lower().startswith("en") and entry.get("description"):
            options["tooltip"] = entry["description"]
            break

    question: dict[str, Any] = {
        "id": deduplicated,
        "label": basket_field.label_override or display_name,
        "type": "obs",
        "questionOptions": options,
    }
    if basket_field.required:
        question["required"] = True
        question["validators"] = [{"type": "required", "message": f"{question['label']} is required"}]
    return question


# ---------------------------------------------------------------------------
# Validation


def validate_schema(schema: dict[str, Any], ciel: CielClient, *, allow_retired: bool = False) -> ValidationReport:
    """Deep validation of a basket-derived schema.

    Checks performed:
      * Top-level fields (name, version, encounterType) are present and well typed.
      * All sections contain at least one question.
      * All question ids are unique within the form.
      * Every `questionOptions.concept` resolves in the CIEL store and is not retired
        (unless `allow_retired`).
      * Every coded `answers[].concept` resolves and is not retired.
    """
    issues: list[ValidationIssue] = []
    if not schema.get("name"):
        issues.append(ValidationIssue("error", "name", "Form name is required."))
    if not schema.get("version"):
        issues.append(ValidationIssue("error", "version", "Form version is required."))
    if not schema.get("encounterType"):
        issues.append(ValidationIssue("error", "encounterType", "Encounter type UUID is required."))

    seen_question_ids: set[str] = set()
    referenced_concepts: dict[str, str] = {}
    # concept_id -> (path, label) for question concepts only; answer concepts
    # are excluded because the quality rules apply to the obs concept.
    question_concepts: dict[str, tuple[str, str]] = {}

    pages = schema.get("pages") or []
    if not pages:
        issues.append(ValidationIssue("error", "pages", "Form must have at least one page."))
    for page_index, page in enumerate(pages):
        sections = page.get("sections") or []
        if not sections:
            issues.append(ValidationIssue("error", f"pages[{page_index}].sections", "Page must have at least one section."))
        for section_index, section in enumerate(sections):
            questions = section.get("questions") or []
            if not questions:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"pages[{page_index}].sections[{section_index}]",
                        f"Section '{section.get('label')}' must have at least one question.",
                    )
                )
            for question_index, question in enumerate(questions):
                qid = str(question.get("id") or "")
                path = f"pages[{page_index}].sections[{section_index}].questions[{question_index}]"
                if not qid:
                    issues.append(ValidationIssue("error", path, "Question id is required."))
                elif qid in seen_question_ids:
                    issues.append(ValidationIssue("error", path, f"Duplicate question id '{qid}'."))
                else:
                    seen_question_ids.add(qid)

                options = question.get("questionOptions") or {}
                concept_uuid = options.get("concept")
                if not concept_uuid:
                    issues.append(ValidationIssue("error", f"{path}.questionOptions.concept", "Concept UUID is required for obs questions."))
                    continue
                concept_id = _concept_id_from_uuid(concept_uuid)
                if concept_id:
                    referenced_concepts.setdefault(concept_id, f"{path}.questionOptions.concept")
                    question_concepts.setdefault(
                        concept_id,
                        (f"{path}.questionOptions.concept", str(question.get("label") or "")),
                    )
                for answer_index, answer in enumerate(options.get("answers") or []):
                    answer_uuid = answer.get("concept")
                    if not answer_uuid:
                        issues.append(
                            ValidationIssue(
                                "error",
                                f"{path}.questionOptions.answers[{answer_index}].concept",
                                "Coded answer concept UUID is required.",
                            )
                        )
                        continue
                    answer_id = _concept_id_from_uuid(answer_uuid)
                    if answer_id:
                        referenced_concepts.setdefault(answer_id, f"{path}.questionOptions.answers[{answer_index}].concept")

    # Lazy import avoids an import cycle (form_builder_tool_loop imports this
    # module). These are the SAME rules enforced when the agent applies an
    # add_field op, re-checked here so a frontend-edited basket cannot bypass
    # the safety boundary.
    from .form_builder_tool_loop import (
        _coded_answer_quality_issue,
        _common_measurement_label_mismatch,
        _is_usable_form_bundle,
    )

    for concept_id, path in referenced_concepts.items():
        try:
            bundle = ciel.get_concept_bundle(concept_id)
        except ConceptNotFoundError:
            issues.append(ValidationIssue("error", path, f"Concept '{concept_id}' is not present in the CIEL store."))
            continue
        if bundle.get("concept", {}).get("retired") and not allow_retired:
            issues.append(ValidationIssue("error", path, f"Concept '{concept_id}' is retired and cannot be used."))
        if concept_id in question_concepts:
            q_path, q_label = question_concepts[concept_id]
            usable, reason = _is_usable_form_bundle(bundle)
            if not usable and reason:
                issues.append(ValidationIssue("error", q_path, reason))
            qa_reason = _coded_answer_quality_issue(bundle)
            if qa_reason:
                issues.append(ValidationIssue("error", q_path, qa_reason))
            if q_label:
                mismatch = _common_measurement_label_mismatch(q_label, bundle)
                if mismatch:
                    issues.append(ValidationIssue("error", q_path, mismatch))
    return ValidationReport(issues=issues)


_UUID_PADDING_RE = re.compile(r"^(\d+)A{4,}$")


def _concept_id_from_uuid(uuid: str) -> str | None:
    """Recover the CIEL numeric id from an OpenMRS UUID like '5089AAAA...'.

    Returns None for non-CIEL-shaped UUIDs (those go through OpenMRS-only
    validation rather than CIEL store lookup).
    """
    match = _UUID_PADDING_RE.match(str(uuid))
    if not match:
        return None
    return match.group(1)


__all__ = [
    "BasketField",
    "BasketSection",
    "ConceptBasket",
    "FormMeta",
    "ValidationIssue",
    "ValidationReport",
    "basket_to_schema",
    "validate_schema",
]
