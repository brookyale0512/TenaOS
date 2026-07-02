"""Allow-listed tool loop for the agent-driven form builder.

Six tools, no more:

    1. search_ciel_seeds         -> finds candidate seed concepts
    2. expand_ciel_concept       -> returns set_members / answers for a concept
    3. get_form_draft            -> returns the current basket + meta
    4. update_form_draft         -> applies structured ops to the basket
    5. build_form_schema         -> deterministically rebuilds the schema
    6. publish_form              -> publishes to OpenMRS (3 REST calls)

`update_form_draft` accepts a small structured-operation grammar so Gemma
never emits schema JSON fragments. The set of operations is:

    {"op": "add_section", "sectionId": "...", "label": "...", "conceptId": "..."}
    {"op": "remove_section", "sectionId": "..."}
    {"op": "rename_section", "sectionId": "...", "label": "..."}
    {"op": "add_field", "sectionId": "...", "conceptId": "...", "label": "...", "required": false}
    {"op": "remove_field", "sectionId": "...", "conceptId": "..."}
    {"op": "set_required", "sectionId": "...", "conceptId": "...", "required": true}
    {"op": "set_label", "sectionId": "...", "conceptId": "...", "label": "..."}
    {"op": "reorder_sections", "sectionIds": ["...", "..."]}
    {"op": "reorder_fields", "sectionId": "...", "conceptIds": ["...", "..."]}

Any unknown op is recorded as a warning and ignored. The middleware always
re-runs `build_form_schema` after a successful basket mutation so the
preview reflects the latest deterministic schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any, Callable, Iterable

from .ciel import CielClient, ConceptNotFoundError, RetiredConceptError
from .form_builder import (
    BasketField,
    BasketSection,
    ConceptBasket,
    FormMeta,
    basket_to_schema,
    validate_schema,
)
from .form_drafts import FormDraftStore, DraftNotFoundError
from .openmrs_writer import OpenmrsWriter, PublishResult
from .llm_client import LlmClient


FORM_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_ciel_seeds",
        "description": "Search CIEL for candidate seed concepts (sections or fields). Use this before adding anything to a basket.",
        "parameters": {
            "query": "string (natural-language clinical phrase)",
            "conceptClasses": "string[] | null",
            "datatypes": "string[] | null",
            "limit": "integer (default 10)",
            "seedLimit": "integer (default 5)",
        },
    },
    {
        "name": "expand_ciel_concept",
        "description": "Return the set members or coded answers under a CIEL concept so you can pick fields.",
        "parameters": {
            "conceptId": "string (CIEL numeric id)",
            "depth": "integer (default 2)",
        },
    },
    {
        "name": "get_form_draft",
        "description": "Return the current basket and last-built schema for the active draft.",
        "parameters": {"draftId": "string"},
    },
    {
        "name": "update_form_draft",
        "description": (
            "Apply structured operations to the basket. Operations are: add_section, "
            "remove_section, rename_section, add_field, remove_field, set_required, "
            "set_label, reorder_sections, reorder_fields."
        ),
        "parameters": {"draftId": "string", "operations": "object[]"},
    },
    {
        "name": "build_form_schema",
        "description": "Deterministically rebuild the schema from the basket and validate it.",
        "parameters": {"draftId": "string"},
    },
    {
        "name": "publish_form",
        "description": "Publish the draft to OpenMRS (3 REST writes). Only call after the user confirms.",
        "parameters": {"draftId": "string", "markPublished": "boolean (default false)"},
    },
]


FORM_OPENAI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_ciel_seeds",
            "description": "Search CIEL for candidate concepts for a proposed form question. Gemma should call this repeatedly with refined phrases until it finds an answerable concept or decides to drop the question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "conceptClasses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional. Usually leave EMPTY: the search already ranks by clinical meaning and a wrong guess (e.g. class=Symptom for 'night sweats', whose concept is class Finding) hides the correct concept. Only set it when you must restrict.",
                    },
                    "datatypes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional. Usually leave EMPTY. Only set when the question is specifically numeric/coded and you must exclude other datatypes.",
                    },
                    "limit": {"type": "integer", "default": 10},
                    "seedLimit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expand_ciel_concept",
            "description": "Inspect a CIEL concept's coded answers or set members before deciding whether it is appropriate for the form.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conceptId": {"type": "string"},
                    "depth": {"type": "integer", "default": 2},
                },
                "required": ["conceptId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_form_draft",
            "description": "Read the current draft basket and schema.",
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
            "name": "update_form_draft",
            "description": "Apply safe structured operations to the draft basket after CIEL concepts have been checked.",
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
                                "sectionId": {"type": "string"},
                                "label": {
                                    "type": "string",
                                    "description": (
                                        "Section title for add_section/rename_section. "
                                        "For add_field, OMIT this: the form uses the CIEL "
                                        "concept's display name. Only set it to rephrase into "
                                        "other human words, never a snake_case id like "
                                        "'cough_field' or the concept id."
                                    ),
                                },
                                "conceptId": {"type": "string"},
                                "required": {"type": "boolean"},
                                "sectionIds": {"type": "array", "items": {"type": "string"}},
                                "conceptIds": {"type": "array", "items": {"type": "string"}},
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
            "name": "build_form_schema",
            "description": "Rebuild and validate the OpenMRS O3 schema from the basket after updates.",
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
            "name": "publish_form",
            "description": "Publish the draft to OpenMRS. Only use after the user explicitly confirms publish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draftId": {"type": "string"},
                    "markPublished": {"type": "boolean", "default": False},
                },
                "required": ["draftId"],
            },
        },
    },
]


_SECTION_OP_NAMES = {
    "add_section",
    "remove_section",
    "rename_section",
    "reorder_sections",
}
_FIELD_OP_NAMES = {
    "add_field",
    "remove_field",
    "set_required",
    "set_label",
    "reorder_fields",
}


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_UUID_PADDING_RE = re.compile(r"^(\d+)A{4,}$")


def _slug(value: str, fallback: str) -> str:
    cleaned = _SLUG_RE.sub("_", (value or "").lower()).strip("_")
    return cleaned or fallback


def _humanize_slug(value: str) -> str:
    """Turn 'patient_history_and_risk_factors' into 'Patient History And Risk Factors'.

    Used as a fallback when the agent omits the `label` on add_section.
    Acronyms passed in upper-case (e.g. 'HIV') are preserved.
    """
    if not value:
        return ""
    parts = [part for part in re.split(r"[_\-]+|\s+", str(value).strip()) if part]
    if not parts:
        return ""
    return " ".join(part.capitalize() if part.islower() else part for part in parts)


def _normalize_label(value: str) -> str:
    """Lowercase + collapse whitespace for label-uniqueness comparison."""
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


# Machine-identifier shape: no spaces, lowercase tokens joined by underscores
# (e.g. ``cough_field``, ``history_tb``, ``bmi_field``). The model is prompted
# to keep field ids unique and sometimes echoes that id into the `label`, which
# would render verbatim in the form. We treat such strings as NOT a real human
# label and fall back to the CIEL display name instead.
_IDENTIFIER_LABEL_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def _looks_like_identifier_label(value: str) -> bool:
    text = str(value or "").strip()
    if not text or " " in text:
        return False
    if text.endswith("_field"):
        return True
    # Pure snake_case / single lowercase token with no spaces is an identifier,
    # never a clinical display label ("Cough", "Body Mass Index" have spaces or
    # capitals). A single capitalized word like "Cough" is a valid label.
    return bool(_IDENTIFIER_LABEL_RE.match(text)) and ("_" in text or text.islower())


def _clean_label_override(raw_label: Any) -> str | None:
    """Normalize an agent-supplied label, dropping machine-identifier strings.

    Returns the cleaned human label, or ``None`` to inherit the CIEL display
    name. A single lowercase word (``cough``) or snake_case id (``cough_field``)
    is discarded so the form shows the proper concept name.
    """
    if raw_label is None:
        return None
    text = re.sub(r"\s+", " ", str(raw_label)).strip()
    if not text:
        return None
    if _looks_like_identifier_label(text):
        return None
    return text


def _bundle_display_name(ciel: CielClient, concept_id: str) -> str:
    try:
        bundle = ciel.get_concept_bundle(concept_id)
    except ConceptNotFoundError:
        return ""
    return str(bundle.get("concept", {}).get("display_name") or "")


def _concept_id_from_padded_uuid(uuid: str) -> str | None:
    """Inverse of `openmrs_uuid_for_concept_id`: return the CIEL numeric id.

    Returns ``None`` for any UUID that does not have the CIEL padding shape,
    so the publish-time autoseeder can skip non-CIEL UUIDs without trying to
    look them up in the CIEL store.
    """
    if not uuid:
        return None
    match = _UUID_PADDING_RE.match(str(uuid).strip())
    if not match:
        return None
    return match.group(1)


def _normalize_concept_id(value: Any) -> tuple[str, bool]:
    """Coerce a concept identifier to its CIEL numeric form.

    Returns ``(normalized, was_padded_uuid)``. The agent loop emits the
    OpenMRS-padded UUID form (e.g. ``5089AAAAAA...``) often enough that
    rejecting those calls outright wastes turns; instead we strip the
    padding and surface a one-shot warning back to the model.
    """
    raw = str(value or "").strip()
    if not raw:
        return "", False
    match = _UUID_PADDING_RE.match(raw)
    if match:
        return match.group(1), True
    return raw, False


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


def _is_usable_form_bundle(bundle: dict[str, Any]) -> tuple[bool, str | None]:
    """Mirror of `_is_usable_form_seed` for a full CIEL bundle.

    Returns ``(usable, reason)``. The agent loop bypasses the search-time
    filter because it picks concepts via expand_ciel_concept or by
    re-reading the basket, so the apply-time check is the only safety
    backstop against unanswerable concepts being added as form questions.

    Filter rules:

    * Retired concepts are always rejected.
    * Sets (ConvSet/LabSet/MedSet, or any concept whose bundle has
      ``set_members``) are rejected — the agent should expand the set
      and add the individual members.
    * Boolean datatype is always usable, regardless of concept class.
    * Coded datatype is usable iff the concept has at least one non-retired
      answer (so the form renderer can produce a select/radio without
      publishing retired answer concepts).
    * Numeric/Text/Date/Datetime/Time/Document datatypes are usable
      unless the concept class is Drug or Diagnosis — those are
      typically labels, not collectable observations of that datatype.
    * N/A datatype with a clinical concept class (Diagnosis, Symptom,
      Finding, Procedure, Test, Question, Observable Entity, Program,
      Specimen, or Misc) IS usable. This is the standard OpenMRS pattern
      for "does the patient have X?" yes/no questions: CIEL stores
      Otalgia / Tinnitus / Hearing loss as Diagnosis-class N/A, and the
      form renders them as Boolean Yes/No radios with the concept itself
      as the obs concept. ``basket_to_schema`` handles the rendering.
    * Any other datatype/class combination is rejected.
    """
    concept = bundle.get("concept") or {}
    if concept.get("retired"):
        return False, "Concept is retired and cannot be added as a form question."
    cls = (concept.get("concept_class") or "").lower()
    datatype = concept.get("datatype") or ""
    answer_count = len(bundle.get("answers") or [])
    set_member_count = len(bundle.get("set_members") or [])
    display_name_lower = str(concept.get("display_name") or "").lower()
    if any(token in display_name_lower for token in _QA_DISPLAY_NAME_TOKENS):
        return False, (
            f"Concept display name '{concept.get('display_name')}' looks like a data-quality "
            "annotation (contains tokens like 'missing', 'incorrect', 'not available', 'review "
            "needed'), not a clinical observation. Pick a clinical concept that asks for the "
            "presence or value of the observation rather than the QA status of a data entry."
        )
    if set_member_count > 0 or cls in {"convset", "labset", "medset"}:
        return False, (
            f"Concept is a set ({cls or 'set'}) — expand it and add the individual members "
            "rather than adding the set itself as a question."
        )
    if datatype == "Boolean":
        return True, None
    if datatype == "Coded":
        retired_answers = [
            str((rel.get("target") or {}).get("display_name") or (rel.get("target") or {}).get("concept_id") or "")
            for rel in (bundle.get("answers") or [])
            if (rel.get("target") or {}).get("retired")
        ]
        if retired_answers:
            preview = ", ".join(label for label in retired_answers[:3] if label)
            return False, (
                "Coded concept has retired answer concepts and cannot render safely"
                + (f" ({preview})" if preview else ".")
            )
        if answer_count > 0:
            return True, None
        return False, "Coded concept has no answers and cannot render as a question."
    if datatype in {"Numeric", "Text", "Date", "Datetime", "Time", "Document"}:
        if cls in {"diagnosis", "drug"}:
            return False, (
                f"Concept class '{cls}' with datatype '{datatype}' is not a collectable "
                "observation. Use a Question, Finding, Test, or Obs concept instead, "
                "or pick a Boolean / Coded variant that asks whether the condition is present."
            )
        return True, None
    if datatype in {"N/A", "", None}:
        if cls in _NA_CLINICAL_CLASSES:
            # Renders as a Boolean Yes/No question in basket_to_schema.
            return True, None
        return False, (
            f"Concept datatype 'N/A' with class '{cls or 'unknown'}' is not a "
            "collectable observation. Pick a concept whose class is Diagnosis, "
            "Symptom, Finding, Procedure, Test, or Question (any of these is "
            "valid as a yes/no question), or use a Boolean/Coded variant."
        )
    return False, (
        f"Concept datatype '{datatype}' is not a collectable observation. "
        "Pick a concept whose datatype is Boolean, Coded with answers, Numeric, "
        "Text, Date, Datetime, Time, or Document."
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

# Tokens in a concept's own display name that signal it is a data-quality
# annotation rather than a clinical observation. The model sometimes picks
# these (e.g. CIEL 166537 "Contact details missing") and slaps a clinical
# label on top, producing a publishable form whose obs concepts are
# semantically wrong.
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
_QA_NEUTRAL_TOKENS = ("other", "unknown", "indeterminate", "not done", "none")


def _coded_answer_quality_issue(bundle: dict[str, Any]) -> str | None:
    """Detect Coded concepts whose answer set is dominated by QA-style flags.

    CIEL contains concepts like 166541 ("Reason for failed contact tracing")
    that are technically Coded with answers but whose answer labels are data
    -quality / workflow flags (e.g. "Contact details missing", "Contact
    location details incorrect"). The agent sometimes picks these because
    the display name fuzzily matches a clinical intent ("TB contact"), but
    the resulting form question is unusable.

    Returns a human-readable reason when the concept should be rejected, or
    ``None`` when the answer set looks clinically usable.
    """
    concept = bundle.get("concept") or {}
    if (concept.get("datatype") or "").lower() != "coded":
        return None
    answers = bundle.get("answers") or []
    answer_labels = [
        str((rel.get("target") or {}).get("display_name") or "").strip().lower()
        for rel in answers
    ]
    if not answer_labels:
        return None
    qa_hits = [label for label in answer_labels if any(token in label for token in _QA_ANSWER_TOKENS)]
    informative = [
        label
        for label in answer_labels
        if label and not any(token in label for token in _QA_NEUTRAL_TOKENS) and label not in qa_hits
    ]
    if len(qa_hits) >= 2 and len(informative) < max(1, len(qa_hits)):
        return (
            f"This Coded concept's answer set is dominated by data-quality flags "
            f"({len(qa_hits)} of {len(answer_labels)} answers look like 'missing', "
            "'incorrect', 'invalid', etc.). It is a workflow/QA concept, not a "
            "clinical question."
        )
    return None


_COMMON_MEASUREMENT_LABEL_TOKENS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("height", "stature"), ("height", "stature")),
    (("weight",), ("weight",)),
    (("temperature", "temp"), ("temperature", "temp")),
    (("oxygen saturation", "spo2", "pulse oximetry", "pulse oximeter"), ("oxygen saturation", "spo2", "oximeter")),
    (("systolic",), ("systolic",)),
    (("diastolic",), ("diastolic",)),
    (("pulse rate", "heart rate"), ("pulse", "heart rate")),
)


def _common_measurement_label_mismatch(label: str, bundle: dict[str, Any]) -> str | None:
    """Reject obvious vital-sign label/concept mismatches.

    The agent sometimes reuses a numeric candidate from a prior search with an
    unrelated label (for example, CIEL 5090 Height labelled as Oxygen
    saturation). These concepts are individually valid, so the basket safety
    check needs a lightweight semantic guard for common vitals.
    """
    label_lower = label.lower()
    display = str((bundle.get("concept") or {}).get("display_name") or "")
    display_lower = display.lower()
    for label_tokens, display_tokens in _COMMON_MEASUREMENT_LABEL_TOKENS:
        if any(token in label_lower for token in label_tokens):
            if not any(token in display_lower for token in display_tokens):
                concept_id = (bundle.get("concept") or {}).get("concept_id") or (bundle.get("concept") or {}).get("id")
                return (
                    f"Label '{label}' does not match concept '{concept_id}' ({display}). "
                    "Search the specific measurement again and use a concept whose display name "
                    "matches the planned question."
                )
    return None


_FIELD_OPS_NEEDING_CONCEPT = {"add_field", "remove_field", "set_required", "set_label"}


def _first_str(value: Any) -> str:
    """Return value as a string, or the first element if it is a non-empty list."""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


def _normalize_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce the agent's loose operation shapes into the canonical singular form.

    Gemma frequently emits ``add_field`` with the PLURAL ``sectionIds`` /
    ``conceptIds`` arrays (the keys that actually belong to the reorder ops)
    instead of the singular ``sectionId`` / ``conceptId`` that the apply
    handlers require. Left as-is, every such ``add_field`` fails with
    "requires 'sectionId'" and the form ends up empty. We normalize here:

    - ``sectionIds: [x]``        -> ``sectionId: x``
    - ``conceptIds: [a]``        -> ``conceptId: a``
    - ``conceptIds: [a, b, c]``  -> three ops, one ``conceptId`` each
      (the model batched several concepts into one add_field).

    Reorder ops keep their plural arrays untouched.
    """
    normalized: list[dict[str, Any]] = []
    for operation in operations or []:
        if not isinstance(operation, dict):
            continue
        op_name = str(operation.get("op") or "")
        if op_name in {"reorder_sections", "reorder_fields"}:
            normalized.append(operation)
            continue

        op = dict(operation)
        if not op.get("sectionId") and op.get("sectionIds"):
            op["sectionId"] = _first_str(op.get("sectionIds"))
        op.pop("sectionIds", None)

        if op_name in _FIELD_OPS_NEEDING_CONCEPT and not op.get("conceptId") and op.get("conceptIds"):
            concept_ids = op.get("conceptIds")
            if isinstance(concept_ids, list) and len(concept_ids) > 1:
                for cid in concept_ids:
                    expanded = dict(op)
                    expanded.pop("conceptIds", None)
                    expanded["conceptId"] = str(cid)
                    normalized.append(expanded)
                continue
            op["conceptId"] = _first_str(concept_ids)
        op.pop("conceptIds", None)
        normalized.append(op)
    return normalized


def _merge_seeds_by_score(filtered: list, unfiltered: list, seed_limit: int) -> list:
    """Union filtered + unfiltered seed hits, dedupe by conceptId, rank by score.

    Filtered hits (which match the model's requested class/datatype intent) and
    unfiltered hits (the raw semantic best) are combined; for a concept present
    in both we keep the higher-scoring copy. The result is sorted by descending
    semantic score so the correct concept wins even when a narrow filter would
    otherwise have buried or excluded it.
    """
    by_id: dict[str, Any] = {}
    for seed in list(filtered) + list(unfiltered):
        cid = str(getattr(seed, "concept_id", "") or "")
        if not cid:
            continue
        existing = by_id.get(cid)
        if existing is None or float(getattr(seed, "score", 0.0) or 0.0) > float(
            getattr(existing, "score", 0.0) or 0.0
        ):
            by_id[cid] = seed
    ranked = sorted(by_id.values(), key=lambda s: float(getattr(s, "score", 0.0) or 0.0), reverse=True)
    return ranked[:seed_limit]


def _dedupe_operations(operations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Collapse repeated basket operations from a single tool call.

    The agent sometimes returns the same ``add_field`` six times in one
    call (observed in runtime traces). We keep the first occurrence and
    drop the rest; the caller surfaces the suppressed count back to the
    model so it does not retry the same mistake on the next turn.
    """
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    dropped = 0
    for operation in operations or []:
        if not isinstance(operation, dict):
            continue
        op_name = str(operation.get("op") or "")
        key_parts: tuple[Any, ...]
        if op_name in {"add_field", "remove_field", "set_required", "set_label"}:
            key_parts = (
                op_name,
                str(operation.get("sectionId") or ""),
                str(operation.get("conceptId") or ""),
            )
        elif op_name in {"add_section", "remove_section", "rename_section"}:
            key_parts = (op_name, str(operation.get("sectionId") or operation.get("label") or ""))
        else:
            # Reorder ops and unknown ops pass through; ordering matters for them.
            out.append(operation)
            continue
        if key_parts in seen:
            dropped += 1
            continue
        seen.add(key_parts)
        out.append(operation)
    return out, dropped


class FormBuilderToolLoop:
    """Stateless executor for the six form-builder tools.

    All mutable state lives in the SQLite store. The tool loop reads + writes
    the basket via deterministic helpers and journals every operation through
    `FormDraftStore.append_event` so the front-end SSE stream is always the
    authoritative source of progress.
    """

    def __init__(
        self,
        *,
        store: FormDraftStore,
        ciel: CielClient,
        llm: LlmClient,
        writer_factory: Callable[[], OpenmrsWriter],
    ) -> None:
        self.store = store
        self.ciel = ciel
        self.llm = llm
        self.writer_factory = writer_factory

    # ------------------------------------------------------------------ tools

    def search_ciel_seeds(
        self,
        draft_id: str,
        *,
        query: str,
        concept_classes: list[str] | None = None,
        datatypes: list[str] | None = None,
        limit: int = 10,
        seed_limit: int = 5,
    ) -> dict[str, Any]:
        has_filters = bool(concept_classes or datatypes)
        # Fetch a slightly larger pool internally so the correct concept is in
        # play even when filters or the recommender bury it; cap to seed_limit
        # only after re-ranking.
        pool_seed_limit = max(int(seed_limit), 10)
        pool_limit = max(int(limit), 15)
        filtered = (
            self.ciel.search_form_seeds(
                query,
                concept_classes=concept_classes,
                datatypes=datatypes,
                limit=pool_limit,
                seed_limit=pool_seed_limit,
            )
            if has_filters
            else []
        )
        # ALWAYS run the unfiltered semantic search too. An over-narrow
        # class/datatype filter (e.g. forcing class=Symptom for "night sweats",
        # whose CIEL concept is class Finding) silently excludes the correct
        # concept and leaves only low-scoring junk ("Vision difficulties").
        unfiltered = self.ciel.search_form_seeds(
            query,
            concept_classes=None,
            datatypes=None,
            limit=pool_limit,
            seed_limit=pool_seed_limit,
        )
        pool = _merge_seeds_by_score(filtered, unfiltered, pool_seed_limit)

        # The seed recommender re-scores by form-suitability, which can rank an
        # unrelated-but-tidy concept (e.g. "Swallowing difficulties") above the
        # true match for "shortness of breath". The plain concept search keeps
        # the authoritative semantic ordering, so we re-rank the pool by it:
        # concepts that appear in the semantic results sort first by their true
        # similarity, the rest fall back to the recommender score.
        semantic_score: dict[str, float] = {}
        try:
            for hit in self.ciel.search_concepts(query, limit=pool_limit):
                cid = str(getattr(hit, "concept_id", "") or "")
                if cid:
                    semantic_score[cid] = float(getattr(hit, "score", 0.0) or 0.0)
        except Exception:  # semantic concept search is best-effort re-ranking only
            semantic_score = {}
        seeds = sorted(
            pool,
            key=lambda s: (
                semantic_score.get(str(s.concept_id), -1.0),
                float(getattr(s, "score", 0.0) or 0.0),
            ),
            reverse=True,
        )[: int(seed_limit)]
        relaxed = has_filters and bool(unfiltered)
        payload = {
            "query": query,
            "conceptClasses": concept_classes,
            "datatypes": datatypes,
            "relaxedFilters": relaxed,
            "seeds": [seed.to_dict() for seed in seeds],
        }
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="search_ciel_seeds",
            detail=f"Searched CIEL for seed candidates matching '{query}' ({len(seeds)} returned)",
            payload=payload,
        )
        return payload

    def expand_ciel_concept(self, draft_id: str, *, concept_id: str, depth: int = 2) -> dict[str, Any]:
        try:
            expanded = self.ciel.expand_seed(concept_id, depth=depth)
        except RetiredConceptError as exc:
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="expand_ciel_concept",
                detail=f"Concept {concept_id} is retired; not expandable for new forms",
                payload={"conceptId": concept_id, "depth": depth, "retired": True, "error": str(exc)},
            )
            return {"conceptId": concept_id, "depth": depth, "retired": True, "error": str(exc)}
        except ConceptNotFoundError as exc:
            return {"conceptId": concept_id, "depth": depth, "error": f"Concept not found: {exc}"}
        payload = {"conceptId": concept_id, "depth": depth, "expansion": expanded}
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="expand_ciel_concept",
            detail=f"Expanded CIEL concept {concept_id} (depth={depth})",
            payload={"conceptId": concept_id, "depth": depth, "answerCount": len(expanded.get("answers", [])), "setMemberCount": len(expanded.get("setMembers", []))},
        )
        return payload

    def get_form_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        return draft.to_dict()

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one allow-listed Gemma tool call.

        The model is allowed to request tools, but this method owns execution,
        argument normalization, draft scoping, and the final safety boundary.
        """
        if name == "search_ciel_seeds":
            draft_id = _require_str(arguments, "draftId")
            return self.search_ciel_seeds(
                draft_id,
                query=_require_str(arguments, "query"),
                concept_classes=_optional_str_list(arguments.get("conceptClasses")),
                datatypes=_optional_str_list(arguments.get("datatypes")),
                limit=int(arguments.get("limit") or 10),
                seed_limit=int(arguments.get("seedLimit") or 5),
            )
        if name == "expand_ciel_concept":
            draft_id = _require_str(arguments, "draftId")
            return self.expand_ciel_concept(
                draft_id,
                concept_id=_require_str(arguments, "conceptId"),
                depth=int(arguments.get("depth") or 2),
            )
        if name == "get_form_draft":
            return self.get_form_draft(_require_str(arguments, "draftId"))
        if name == "update_form_draft":
            return self.update_form_draft(
                _require_str(arguments, "draftId"),
                list(arguments.get("operations") or []),
                actor="gemma",
            )
        if name == "build_form_schema":
            return self.build_form_schema(_require_str(arguments, "draftId"))
        if name == "publish_form":
            return self.publish_form(
                _require_str(arguments, "draftId"),
                mark_published=bool(arguments.get("markPublished", False)),
            )
        raise ValueError(f"Unknown form-builder tool '{name}'.")

    def update_form_draft(
        self,
        draft_id: str,
        operations: list[dict[str, Any]],
        *,
        actor: str = "gemma",
    ) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        basket = ConceptBasket.from_dict(draft.basket)
        applied: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        operations = _normalize_operations(list(operations or []))
        operations, dropped_duplicates = _dedupe_operations(operations)
        if dropped_duplicates:
            warnings.append(
                {
                    "operation": None,
                    "reason": (
                        f"Dropped {dropped_duplicates} duplicate operation(s) from this call. "
                        "Send each (op, sectionId, conceptId) tuple only once per update_form_draft."
                    ),
                }
            )
        for operation in operations or []:
            op_name = str(operation.get("op") or "")
            try:
                if op_name == "add_section":
                    applied.append(self._apply_add_section(basket, operation))
                elif op_name == "remove_section":
                    applied.append(self._apply_remove_section(basket, operation))
                elif op_name == "rename_section":
                    applied.append(self._apply_rename_section(basket, operation))
                elif op_name == "add_field":
                    applied.append(self._apply_add_field(basket, operation))
                elif op_name == "remove_field":
                    applied.append(self._apply_remove_field(basket, operation))
                elif op_name == "set_required":
                    applied.append(self._apply_set_required(basket, operation))
                elif op_name == "set_label":
                    applied.append(self._apply_set_label(basket, operation))
                elif op_name == "reorder_sections":
                    applied.append(self._apply_reorder_sections(basket, operation))
                elif op_name == "reorder_fields":
                    applied.append(self._apply_reorder_fields(basket, operation))
                else:
                    warnings.append({"operation": operation, "reason": f"Unknown op '{op_name}'"})
            except KeyError as exc:
                warnings.append({"operation": operation, "reason": str(exc)})
            except ValueError as exc:
                warnings.append({"operation": operation, "reason": str(exc)})

        self.store.update_draft(draft_id, basket=basket.to_dict())
        self.store.append_event(
            draft_id,
            actor=actor,  # type: ignore[arg-type]
            operation="update_form_draft",
            detail=f"Applied {len(applied)} basket operation(s); {len(warnings)} warning(s)",
            payload={"applied": applied, "warnings": warnings, "operations": operations},
        )
        return {"applied": applied, "warnings": warnings, "basket": basket.to_dict()}

    def build_form_schema(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        basket = ConceptBasket.from_dict(draft.basket)
        if not draft.encounter_type_uuid:
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="build_form_schema_skipped",
                detail="Encounter type is not selected yet; cannot build schema.",
                payload={},
            )
            return {"schema": None, "validation": {"issues": [{"severity": "error", "path": "encounterType", "message": "Encounter type UUID is required."}]}}
        meta = FormMeta(
            name=draft.name,
            version=draft.version,
            description=draft.description,
            encounter_type_uuid=draft.encounter_type_uuid,
        )
        unresolved: list[dict[str, Any]] = []
        schema = basket_to_schema(basket, meta, self.ciel, unresolved_out=unresolved)
        report = validate_schema(schema, self.ciel)
        validation = report.to_dict()
        if unresolved:
            # Don't silently drop fields the model committed but CIEL couldn't
            # resolve: surface them as warnings so the clinician sees the gap.
            issues = validation.setdefault("issues", [])
            for item in unresolved:
                issues.append(
                    {
                        "severity": "warning",
                        "path": f"field:{item['conceptId']}",
                        "message": (
                            f"Question '{item['label']}' was dropped: {item['reason']}. "
                            "Re-search CIEL for an equivalent concept."
                        ),
                    }
                )
            validation["unresolvedFields"] = unresolved
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="build_form_schema_unresolved",
                detail=f"{len(unresolved)} basket field(s) could not be resolved in CIEL and were dropped.",
                payload={"unresolvedFields": unresolved},
            )
        self.store.update_draft(draft_id, last_schema=schema, last_validation=validation)
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="build_form_schema",
            detail=f"Built schema with {sum(len((page.get('sections') or [])) for page in schema.get('pages', []))} section(s) and {_count_questions(schema)} question(s); validation ok={report.ok}",
            payload={"validation": validation, "questionCount": _count_questions(schema)},
        )
        return {"schema": schema, "validation": validation}

    def publish_form(self, draft_id: str, *, mark_published: bool = False) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        if not draft.last_schema:
            built = self.build_form_schema(draft_id)
            schema = built.get("schema")
        else:
            schema = draft.last_schema
        if not schema:
            self._emit_publish_block(
                draft_id,
                "I can't publish yet — the form has no preview schema. Add at least one question first.",
                {},
            )
            return {"success": False, "error": "Schema unavailable; resolve validation errors first.", "steps": []}

        report_dict = draft.last_validation or {"issues": []}
        if any(issue.get("severity") == "error" for issue in report_dict.get("issues", [])):
            self._emit_publish_block(
                draft_id,
                (
                    "I can't publish — the schema has validation errors. "
                    "Look at the preview's validation report and fix them, then try again."
                ),
                {"validation": report_dict},
            )
            return {"success": False, "error": "Schema has validation errors.", "validation": report_dict, "steps": []}

        # Concept-seeding preflight.
        #
        # With auto-seeding wired into _apply_add_field this should be empty in
        # practice. As defense in depth, if anything is missing we try to seed
        # it from CIEL right here before failing the publish. The schema may
        # reference answer concepts (e.g. CIEL 1065/1066 for Yes/No) that the
        # per-field seeding didn't catch in older drafts.
        writer = self.writer_factory()
        concept_uuids = _collect_concept_uuids(schema)
        preflight = writer.preflight_concepts(concept_uuids)
        if preflight["missing"]:
            still_missing, seeded_uuids = self._try_autoseed_missing(writer, preflight["missing"])
            if seeded_uuids:
                self.store.append_event(
                    draft_id,
                    actor="middleware",
                    operation="publish_autoseed",
                    detail=f"Auto-seeded {len(seeded_uuids)} concept(s) from CIEL before publish.",
                    payload={"seededUuids": seeded_uuids},
                )
                preflight = {"missing": still_missing, "retired": preflight.get("retired", []), "checked": preflight.get("checked", [])}
        if preflight["missing"] or preflight["retired"]:
            missing_names = _resolve_concept_names(schema, set(preflight["missing"]))
            retired_names = _resolve_concept_names(schema, set(preflight["retired"]))
            details: list[str] = []
            if missing_names:
                details.append(
                    f"missing in OpenMRS: {', '.join(missing_names)}"
                )
            if retired_names:
                details.append(
                    f"retired in OpenMRS: {', '.join(retired_names)}"
                )
            message = (
                "I can't publish yet — "
                + "; ".join(details)
                + ". Remove or replace those questions and try again."
            )
            self._emit_publish_block(draft_id, message, preflight)
            return {"success": False, "error": "Concepts missing or retired in target OpenMRS.", "preflight": preflight, "steps": []}

        schema_to_publish = dict(schema)
        schema_to_publish["published"] = bool(mark_published)
        self.store.update_draft(draft_id, status="publishing")
        result: PublishResult = writer.publish_form(schema_to_publish)
        if result.success and result.form_uuid:
            self.store.update_draft(
                draft_id,
                status="published",
                published_form_uuid=result.form_uuid,
                last_schema={**schema_to_publish, "uuid": result.form_uuid},
            )
            # Friendly user-facing announcement.
            self.store.append_event(
                draft_id,
                actor="gemma",
                operation="agent_prompt",
                detail=(
                    "Form published to OpenMRS. You can open it from the forms list "
                    "or use 'Open published form' in the header to fill it."
                ),
                payload={
                    "text": "Form published to OpenMRS.",
                    "formUuid": result.form_uuid,
                },
            )
            # Diagnostic audit row (hidden from chat).
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="publish_form",
                detail=f"Published form to OpenMRS (uuid={result.form_uuid})",
                payload=result.to_dict(),
            )
        else:
            self.store.update_draft(draft_id, status="failed")
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="publish_form_failed",
                detail=f"OpenMRS publish failed: {result.error}",
                payload=result.to_dict(),
            )
        return result.to_dict()

    # ------------------------------------------------------ publish helpers

    def _emit_publish_block(self, draft_id: str, gemma_message: str, payload: dict[str, Any]) -> None:
        """Twin events for a publish failure.

        - actor=gemma agent_prompt: the user-friendly chat message.
        - actor=middleware publish_form_blocked: diagnostic audit row.
        The frontend chat dispatcher hides publish_form_blocked from view,
        so only the assistant's voice is visible.
        """
        self.store.append_event(
            draft_id,
            actor="gemma",
            operation="agent_prompt",
            detail=gemma_message,
            payload={"text": gemma_message, "publishBlocked": True, **payload},
        )
        self.store.append_event(
            draft_id,
            actor="middleware",
            operation="publish_form_blocked",
            detail=gemma_message,
            payload=payload,
        )

    # ------------------------------------------------------ basket operations

    def _apply_add_section(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        raw_concept_id = str(op.get("conceptId") or "").strip()
        concept_id: str | None
        if raw_concept_id:
            normalized, _ = _normalize_concept_id(raw_concept_id)
            concept_id = normalized or None
        else:
            concept_id = None
        label = op.get("label")
        section_id_hint = op.get("sectionId") or label or concept_id
        if not section_id_hint:
            raise ValueError("add_section requires sectionId, label, or conceptId.")
        if concept_id and not label:
            bundle = self.ciel.get_concept_bundle(concept_id)
            label = bundle.get("concept", {}).get("display_name") or concept_id
        section_id = _unique_section_id(basket, _slug(str(section_id_hint), "section"))
        # If the agent forgot to pass a `label`, derive a human-readable one
        # from the sectionId slug instead of letting the basket show
        # 'patient_history_and_risk_factors' verbatim. Snake_case slugs are
        # turned into 'Patient History And Risk Factors'; everything else is
        # passed through.
        display_label = str(label).strip() if label else ""
        if not display_label:
            display_label = _humanize_slug(section_id)
        section = BasketSection(
            section_id=section_id,
            label=display_label or section_id,
            concept_id=concept_id,
            kind="section_concept" if concept_id else "container",
        )
        basket.sections.append(section)
        return {"op": "add_section", "sectionId": section_id, "label": section.label, "conceptId": concept_id}

    def _apply_remove_section(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        section_id = _require_str(op, "sectionId")
        index = _find_section_index(basket, section_id)
        removed = basket.sections.pop(index)
        return {"op": "remove_section", "sectionId": section_id, "removedFieldCount": len(removed.fields)}

    def _apply_rename_section(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        section_id = _require_str(op, "sectionId")
        new_label = _require_str(op, "label")
        section = basket.sections[_find_section_index(basket, section_id)]
        section.label = new_label
        return {"op": "rename_section", "sectionId": section_id, "label": new_label}

    def _apply_add_field(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        section_id = _require_str(op, "sectionId")
        raw_concept_id = _require_str(op, "conceptId")
        concept_id, was_padded_uuid = _normalize_concept_id(raw_concept_id)
        if not concept_id:
            raise ValueError(f"Operation '{op.get('op')}' requires 'conceptId'.")
        try:
            bundle = self.ciel.get_concept_bundle(concept_id)
        except ConceptNotFoundError as exc:
            raise ValueError(
                f"Concept '{raw_concept_id}' is not present in the CIEL store. "
                "Only pass CIEL numeric conceptId values returned by search_ciel_seeds."
            ) from exc
        usable, reason = _is_usable_form_bundle(bundle)
        if not usable:
            display = bundle.get("concept", {}).get("display_name") or concept_id
            raise ValueError(
                f"Concept '{concept_id}' ({display}) was rejected as a form question: {reason}"
            )
        # Reject Coded concepts whose answer set is dominated by data-quality
        # flags ("incorrect", "missing", "invalid", "review needed", etc.).
        qa_reason = _coded_answer_quality_issue(bundle)
        if qa_reason:
            display = bundle.get("concept", {}).get("display_name") or concept_id
            raise ValueError(
                f"Concept '{concept_id}' ({display}) was rejected as a form question: "
                f"{qa_reason} Pick a different Coded concept whose answers are clinical "
                "values (e.g. Positive/Negative, Yes/No, Severity grades)."
            )
        # Cross-section duplicate: the same CIEL conceptId must not appear in
        # two different sections of the form.
        for existing_section in basket.sections:
            if existing_section.section_id == section_id:
                if any(field.concept_id == concept_id for field in existing_section.fields):
                    raise ValueError(
                        f"Concept '{concept_id}' is already in section '{section_id}'."
                    )
                continue
            if any(field.concept_id == concept_id for field in existing_section.fields):
                raise ValueError(
                    f"Concept '{concept_id}' is already used as a question in section "
                    f"'{existing_section.section_id}'. A CIEL concept can appear in only "
                    "one section per form; pick a different concept or move it."
                )
        section = basket.sections[_find_section_index(basket, section_id)]

        # Label-uniqueness within a section. The agent occasionally adds two
        # different CIEL concepts with the same labelOverride (e.g. CIEL
        # 159576 and 119481 both labelled "History of immunosuppressive
        # conditions"), which renders as two indistinguishable questions in
        # the form. Reject the second one and surface a warning that names
        # the conflicting existing label.
        new_label_override = _clean_label_override(op.get("label") or op.get("labelOverride"))
        if new_label_override:
            mismatch = _common_measurement_label_mismatch(str(new_label_override), bundle)
            if mismatch:
                raise ValueError(mismatch)
            normalized_new = _normalize_label(new_label_override)
            for existing_field in section.fields:
                existing_label = existing_field.label_override or _bundle_display_name(
                    self.ciel, existing_field.concept_id
                )
                if existing_label and _normalize_label(existing_label) == normalized_new:
                    raise ValueError(
                        f"Section '{section_id}' already has a field labelled "
                        f"'{existing_label}' (concept {existing_field.concept_id}). "
                        "Pick a different concept or give this one a distinct labelOverride."
                    )

        # Auto-seed the concept (and its Coded answers / Yes-No answers for
        # Boolean & N/A clinical concepts) into the running OpenMRS instance
        # BEFORE appending to the basket. The previous candidate-picker
        # workflow did this in form_conversation._add_field; the new
        # tool-calling agent path was bypassing seeding, which caused publish
        # preflight to reject every form because the concepts existed in
        # CIEL but not in OpenMRS.
        seeded_now, missing_answers = self._seed_concept_for_field(bundle)
        if not seeded_now:
            display = bundle.get("concept", {}).get("display_name") or concept_id
            raise ValueError(
                f"Concept '{concept_id}' ({display}) could not be seeded into OpenMRS. "
                "Try a different concept."
            )

        section.fields.append(
            BasketField(
                concept_id=concept_id,
                label_override=new_label_override,
                required=bool(op.get("required", False)),
                rendering_override=op.get("renderingOverride") or None,
            )
        )
        result: dict[str, Any] = {
            "op": "add_field",
            "sectionId": section_id,
            "conceptId": concept_id,
            "displayName": bundle.get("concept", {}).get("display_name"),
            "seededInOpenmrs": True,
        }
        if missing_answers:
            result["partialAnswerSeeding"] = missing_answers
        if was_padded_uuid:
            result["normalizedFromUuid"] = raw_concept_id
            result["warning"] = (
                f"You passed an OpenMRS-padded UUID '{raw_concept_id}'. "
                f"Use the CIEL numeric id '{concept_id}' returned by search_ciel_seeds."
            )
        return result

    def _try_autoseed_missing(self, writer: OpenmrsWriter, missing_uuids: list[str]) -> tuple[list[str], list[str]]:
        """Best-effort seed any CIEL-shaped UUIDs missing from OpenMRS.

        Used at publish time as a last-mile defense: even if a basket
        operation slipped through without auto-seeding (older drafts, manual
        operations from the frontend basket editor, etc.), the publish step
        will pull each missing concept from CIEL and POST it to OpenMRS
        before declining.

        Returns ``(still_missing, seeded)`` UUID lists.
        """
        still_missing: list[str] = []
        seeded: list[str] = []
        for uuid in missing_uuids:
            concept_id = _concept_id_from_padded_uuid(uuid)
            if not concept_id:
                still_missing.append(uuid)
                continue
            try:
                bundle = self.ciel.get_concept_bundle(concept_id)
            except ConceptNotFoundError:
                still_missing.append(uuid)
                continue
            try:
                ok, _missing_answers = writer.ensure_concept_with_answers(bundle)
            except Exception:
                ok = False
            if ok:
                seeded.append(uuid)
            else:
                still_missing.append(uuid)
        return still_missing, seeded

    def _seed_concept_for_field(self, bundle: dict[str, Any]) -> tuple[bool, list[str]]:
        """Seed the question concept + every answer concept it can render with.

        For Boolean datatype or N/A clinical concepts (rendered as Yes/No
        radios), also seed CIEL 1065 (Yes) and 1066 (No) so the preflight
        check passes when the schema is published.

        Returns ``(seeded_question, missing_answer_display_names)``. The
        question must seed for the field to be added; missing answer concepts
        are surfaced as a partial warning but do not block.
        """
        writer = self.writer_factory()
        try:
            seeded, missing_answers = writer.ensure_concept_with_answers(bundle)
        except Exception:
            return False, []
        if not seeded:
            return False, list(missing_answers or [])

        concept = bundle.get("concept") or {}
        datatype = (concept.get("datatype") or "").strip()
        concept_class = (concept.get("concept_class") or "").strip().lower()
        needs_yes_no = datatype == "Boolean" or (
            datatype in {"N/A", ""} and concept_class in _NA_CLINICAL_CLASSES
        )
        if needs_yes_no:
            for yn_id, yn_label in (("1065", "Yes"), ("1066", "No")):
                try:
                    yn_bundle = self.ciel.get_concept_bundle(yn_id)
                except ConceptNotFoundError:
                    missing_answers.append(yn_label)
                    continue
                try:
                    yn_ok = writer.ensure_concept(yn_bundle)
                except Exception:
                    yn_ok = False
                if not yn_ok:
                    missing_answers.append(yn_label)
        return True, list(missing_answers or [])

    def _apply_remove_field(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        section_id = _require_str(op, "sectionId")
        concept_id, _ = _normalize_concept_id(_require_str(op, "conceptId"))
        section = basket.sections[_find_section_index(basket, section_id)]
        before = len(section.fields)
        section.fields = [f for f in section.fields if f.concept_id != concept_id]
        if len(section.fields) == before:
            raise ValueError(f"Concept '{concept_id}' is not in section '{section_id}'.")
        return {"op": "remove_field", "sectionId": section_id, "conceptId": concept_id}

    def _apply_set_required(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        section_id = _require_str(op, "sectionId")
        concept_id, _ = _normalize_concept_id(_require_str(op, "conceptId"))
        required = bool(op.get("required", False))
        section = basket.sections[_find_section_index(basket, section_id)]
        for field in section.fields:
            if field.concept_id == concept_id:
                field.required = required
                return {"op": "set_required", "sectionId": section_id, "conceptId": concept_id, "required": required}
        raise ValueError(f"Concept '{concept_id}' is not in section '{section_id}'.")

    def _apply_set_label(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        section_id = _require_str(op, "sectionId")
        concept_id, _ = _normalize_concept_id(_require_str(op, "conceptId"))
        # Drop machine-identifier labels so a rename to "cough_field" reverts to
        # the CIEL display name rather than showing the id in the form.
        label = _clean_label_override(_require_str(op, "label"))
        section = basket.sections[_find_section_index(basket, section_id)]
        for field in section.fields:
            if field.concept_id == concept_id:
                field.label_override = label
                effective = label or _bundle_display_name(self.ciel, concept_id)
                return {"op": "set_label", "sectionId": section_id, "conceptId": concept_id, "label": effective}
        raise ValueError(f"Concept '{concept_id}' is not in section '{section_id}'.")

    def _apply_reorder_sections(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        ids = list(op.get("sectionIds") or [])
        index_by_id = {section.section_id: section for section in basket.sections}
        for sid in ids:
            if sid not in index_by_id:
                raise ValueError(f"Unknown sectionId '{sid}' in reorder_sections.")
        # Preserve any sections not mentioned in the new order, appended after.
        reordered: list[BasketSection] = [index_by_id[sid] for sid in ids]
        for section in basket.sections:
            if section.section_id not in ids:
                reordered.append(section)
        basket.sections = reordered
        return {"op": "reorder_sections", "sectionIds": [section.section_id for section in basket.sections]}

    def _apply_reorder_fields(self, basket: ConceptBasket, op: dict[str, Any]) -> dict[str, Any]:
        section_id = _require_str(op, "sectionId")
        concept_ids = [_normalize_concept_id(cid)[0] for cid in (op.get("conceptIds") or [])]
        concept_ids = [cid for cid in concept_ids if cid]
        section = basket.sections[_find_section_index(basket, section_id)]
        by_id = {field.concept_id: field for field in section.fields}
        for cid in concept_ids:
            if cid not in by_id:
                raise ValueError(f"Concept '{cid}' not in section '{section_id}'.")
        reordered = [by_id[cid] for cid in concept_ids]
        for field in section.fields:
            if field.concept_id not in concept_ids:
                reordered.append(field)
        section.fields = reordered
        return {"op": "reorder_fields", "sectionId": section_id, "conceptIds": [field.concept_id for field in section.fields]}


def _require_str(op: dict[str, Any], key: str) -> str:
    value = op.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Operation '{op.get('op')}' requires '{key}'.")
    return value.strip()


def _optional_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned or None


def _find_section_index(basket: ConceptBasket, section_id: str) -> int:
    for index, section in enumerate(basket.sections):
        if section.section_id == section_id:
            return index
    raise KeyError(f"Section '{section_id}' not found in basket.")


def _unique_section_id(basket: ConceptBasket, base: str) -> str:
    used = {section.section_id for section in basket.sections}
    if base not in used:
        return base
    suffix = 2
    while f"{base}_{suffix}" in used:
        suffix += 1
    return f"{base}_{suffix}"


def _collect_concept_uuids(schema: dict[str, Any]) -> list[str]:
    uuids: list[str] = []
    for page in schema.get("pages", []) or []:
        for section in page.get("sections", []) or []:
            for question in section.get("questions", []) or []:
                options = question.get("questionOptions") or {}
                if options.get("concept"):
                    uuids.append(options["concept"])
                for answer in options.get("answers") or []:
                    if answer.get("concept"):
                        uuids.append(answer["concept"])
    return uuids


def _resolve_concept_names(schema: dict[str, Any], uuids: set[str]) -> list[str]:
    """Map a set of OpenMRS UUIDs back to their human-friendly question labels.

    Used to turn an opaque "2 missing concept(s)" error into something the
    user can act on (e.g. "missing in OpenMRS: Age at diagnosis (years)").
    Falls back to the UUID prefix when no label is found.
    """
    by_uuid: dict[str, str] = {}
    for page in schema.get("pages", []) or []:
        for section in page.get("sections", []) or []:
            for question in section.get("questions", []) or []:
                options = question.get("questionOptions") or {}
                if options.get("concept"):
                    by_uuid[options["concept"]] = question.get("label") or options["concept"]
                for answer in options.get("answers") or []:
                    if answer.get("concept"):
                        by_uuid[answer["concept"]] = answer.get("label") or answer["concept"]
    return [by_uuid.get(u, u[:8] + "...") for u in uuids]


def _count_questions(schema: dict[str, Any]) -> int:
    return sum(
        len(section.get("questions") or [])
        for page in schema.get("pages", []) or []
        for section in page.get("sections", []) or []
    )


__all__ = [
    "FORM_OPENAI_TOOLS",
    "FORM_TOOL_SCHEMAS",
    "FormBuilderToolLoop",
]
