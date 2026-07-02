"""The QuestionWorklist contract between research (Phase A) and CIEL (Phase B).

The worklist is the single hand-off artifact in the v2 pipeline. Phase A
(subject research) decides WHICH questions belong on the form and emits a
structured worklist; Phase B (CIEL resolution) consumes that worklist and
resolves each item to a concrete CIEL concept. Making this an explicit typed
object (rather than free-text injected into a prompt) is what lets the loop
compute coverage deterministically and justify a single, generic repair pass
instead of the hardcoded nudges/recovery the legacy runner used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

# Datatype hints the schema builder understands. Anything else collapses to
# Boolean (the safest renderable default for an unknown clinical question).
ALLOWED_DATATYPES = ("Boolean", "Coded", "Numeric", "Text", "Date", "Datetime", "Time")

WorklistSource = Literal["research", "request", "repair"]

# Labels that describe an action/order rather than a collectable observation.
# Phase A must not propose these as form questions.
_DISALLOWED_LABEL_TOKENS = (
    "treat",
    "treatment",
    "give ",
    "administer",
    "dose",
    "dosage",
    "regimen",
    "refer",
    "referral",
    "counsel",
    "educat",
    "prophylaxis",
    "start medication",
)


@dataclass
class WorklistItem:
    """One planned form question, before CIEL resolution.

    ``search_phrases`` are candidate CIEL query strings ordered most- to
    least-specific. They are hints for Phase B, not commitments; Phase B is
    free to refine. ``concept_id`` is filled in once Phase B resolves the item.
    """

    label: str
    datatype_hint: str = "Boolean"
    section_hint: str | None = None
    rationale: str = ""
    search_phrases: list[str] = field(default_factory=list)
    priority: int = 5
    source: WorklistSource = "research"
    concept_id: str | None = None
    resolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "datatypeHint": self.datatype_hint,
            "sectionHint": self.section_hint,
            "rationale": self.rationale,
            "searchPhrases": list(self.search_phrases),
            "priority": self.priority,
            "source": self.source,
            "conceptId": self.concept_id,
            "resolved": self.resolved,
        }


@dataclass
class QuestionWorklist:
    """An ordered list of planned questions plus subject-matter provenance."""

    items: list[WorklistItem] = field(default_factory=list)
    subject_summary: str = ""
    used_guidelines: bool = False
    searches: list[dict[str, Any]] = field(default_factory=list)

    def labels(self) -> list[str]:
        return [item.label for item in self.items]

    def section_hints(self) -> list[str]:
        seen: list[str] = []
        for item in self.items:
            hint = (item.section_hint or "").strip()
            if hint and hint not in seen:
                seen.append(hint)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "subjectSummary": self.subject_summary,
            "usedGuidelines": self.used_guidelines,
            "questions": [item.to_dict() for item in self.items],
            "searches": list(self.searches),
        }

    def to_prompt_block(self) -> str:
        """Render the worklist as the plan block fed to the CIEL phase."""
        if not self.items:
            return "(no planned questions)"
        lines: list[str] = []
        if self.subject_summary:
            lines.append(f"Subject summary: {self.subject_summary}")
        lines.append("Planned questions (resolve each against CIEL, in order):")
        for index, item in enumerate(self.items, 1):
            phrases = ", ".join(item.search_phrases[:3]) if item.search_phrases else item.label
            section = f" | section: {item.section_hint}" if item.section_hint else ""
            lines.append(
                f"{index}. {item.label} | datatype: {item.datatype_hint} | "
                f"search: {phrases}{section}"
            )
        return "\n".join(lines)


def normalize_label(value: str) -> str:
    """Lowercase + collapse to alphanumerics for label-uniqueness comparisons."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _clean_label(value: Any) -> str:
    label = re.sub(r"\s+", " ", str(value or "")).strip(" .:-")
    return label[:90]


def _label_is_disallowed(label: str) -> bool:
    lowered = label.lower()
    return any(token in lowered for token in _DISALLOWED_LABEL_TOKENS)


def _coerce_phrases(raw: Any, fallback: str) -> list[str]:
    phrases: list[str] = []
    if isinstance(raw, list):
        for entry in raw:
            text = re.sub(r"\s+", " ", str(entry or "")).strip()
            if text and text.lower() not in {p.lower() for p in phrases}:
                phrases.append(text[:120])
    if not phrases and fallback:
        phrases.append(fallback)
    return phrases[:4]


def sanitize_items(raw_items: Any, *, source: WorklistSource = "research", limit: int = 12) -> list[WorklistItem]:
    """Coerce model-emitted question dicts into clean, deduped WorklistItems.

    Drops action/order-style labels, normalizes datatypes, caps the count, and
    sorts by ascending priority (1 = most important). This is generic shaping,
    not domain knowledge: there are no hardcoded clinical concepts here.
    """
    if not isinstance(raw_items, list):
        return []
    items: list[WorklistItem] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        label = _clean_label(raw.get("label") or raw.get("name"))
        if not label or _label_is_disallowed(label):
            continue
        key = normalize_label(label)
        if not key or key in seen:
            continue
        seen.add(key)
        datatype = str(
            raw.get("datatypeHint")
            or raw.get("datatype_hint")
            or raw.get("datatype")
            or "Boolean"
        ).strip()
        if datatype not in ALLOWED_DATATYPES:
            datatype = "Boolean"
        try:
            priority = int(raw.get("priority") or 5)
        except (TypeError, ValueError):
            priority = 5
        section_hint = str(raw.get("sectionHint") or raw.get("section_hint") or "").strip() or None
        items.append(
            WorklistItem(
                label=label,
                datatype_hint=datatype,
                section_hint=section_hint,
                rationale=str(raw.get("rationale") or raw.get("reason") or "").strip()[:500],
                search_phrases=_coerce_phrases(
                    raw.get("searchPhrases") or raw.get("search_phrases"), label
                ),
                priority=max(1, min(priority, 10)),
                source=source,
            )
        )
    items.sort(key=lambda item: item.priority)
    return items[:limit]
