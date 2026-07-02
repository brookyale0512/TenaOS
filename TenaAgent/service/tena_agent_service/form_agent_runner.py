"""Form-builder agent entry point and shared deterministic helpers.

Historically this module contained a large scripted tool loop with
condition-specific search phrases, hardcoded CIEL concept-id recovery tables,
and a dozen middleware "nudge" branches. That machinery has been removed: the
grounded v2 pipeline (``form_pipeline``) — subject-matter research over the
WHO/MSF guideline KB, semantic CIEL discovery, exact SQLite resolution, and a
single generic coverage-repair pass — is now the one and only path.

``run_gemma_tool_agent`` is kept as a thin compatibility shim that delegates to
``form_pipeline.run_form_pipeline_agent`` so the conversation driver and the
``FORM_AGENT_PIPELINE_V2`` env toggle keep working without a behavioural fork.

Public entry points:
  run_gemma_tool_agent(store, loop, llm, draft, request, mode, settings) -> None
  build_deterministic_summary(...) -> str
  compact_tool_result(result) -> Any
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from .form_drafts import FormDraft, FormDraftStore  # noqa: F401 (FormDraftStore re-exported for tests)

if TYPE_CHECKING:
    from .config import Settings
    from .form_builder_tool_loop import FormBuilderToolLoop
    from .llm_client import LlmClient


def run_gemma_tool_agent(
    *,
    store: "FormDraftStore",
    loop: "FormBuilderToolLoop",
    llm: "LlmClient",
    draft: "FormDraft",
    request: str,
    mode: Literal["create", "edit"],
    settings: "Settings",
) -> None:
    """Delegate to the grounded v2 form pipeline.

    Imported lazily to avoid an import cycle (``form_pipeline`` imports
    ``build_deterministic_summary``/``compact_tool_result`` from this module).
    """
    from .form_pipeline import run_form_pipeline_agent

    run_form_pipeline_agent(
        store=store,
        loop=loop,
        llm=llm,
        draft=draft,
        request=request,
        mode=mode,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Deterministic, model-free summary used by both the v2 pipeline and the
# conversation driver.


def build_deterministic_summary(
    *,
    mode: str,
    latest: FormDraft,
    starting_field_count: int,
    target_min_fields: int,
    warnings: list[str],
) -> str:
    ending_field_count = _basket_field_count(latest)
    question_count = _schema_question_count(latest.last_schema)
    net_added = ending_field_count - starting_field_count
    parts: list[str] = []

    if mode == "edit":
        if net_added > 0:
            parts.append(f"I added {net_added} new question{'' if net_added == 1 else 's'} to the draft.")
        elif net_added < 0:
            parts.append(f"I removed {-net_added} question{'' if net_added == -1 else 's'} from the draft.")
        else:
            parts.append("I searched but could not safely add any new questions to the existing draft.")
    else:
        if ending_field_count == 0:
            parts.append(
                "I could not build a safe CIEL-backed draft from that request. "
                "Try a narrower clinical form request."
            )
        elif question_count < target_min_fields:
            parts.append(
                f"I built a CIEL-backed draft with {ending_field_count} question"
                f"{'' if ending_field_count == 1 else 's'}. Reply with 'add more questions' "
                "to extend it, or publish as-is if it's enough."
            )
        else:
            parts.append(
                f"I built a CIEL-backed draft with {ending_field_count} question"
                f"{'' if ending_field_count == 1 else 's'}. Review the preview and basket "
                "before publishing."
            )

    for reason in warnings[:3]:
        parts.append(f"Note: {reason}")

    parts.append(_summarize_basket_for_user(latest))
    return "\n\n".join(parts)


def compact_tool_result(result: Any) -> Any:
    """Trim noise + UUID-shaped identifiers from tool results fed back to Gemma."""
    if not isinstance(result, dict):
        return result
    compact = dict(result)
    if isinstance(compact.get("schema"), dict):
        schema = compact["schema"]
        compact["schema"] = {
            "name": schema.get("name"),
            "encounterType": schema.get("encounterType"),
            "questionCount": _schema_question_count(schema),
        }
    if isinstance(compact.get("basket"), dict):
        compact["basketSummary"] = [
            {
                "sectionId": section.get("sectionId"),
                "label": section.get("label"),
                "fields": [
                    {
                        "conceptId": str(field.get("conceptId") or ""),
                        "label": field.get("labelOverride") or field.get("conceptId"),
                        "required": bool(field.get("required")),
                    }
                    for field in (section.get("fields") or [])
                ],
            }
            for section in compact["basket"].get("sections", [])
        ]
        compact.pop("basket", None)
    compact.pop("lastSchema", None)
    compact.pop("lastValidation", None)
    if compact.get("draftId") and "name" in compact and "encounterTypeUuid" in compact:
        compact = {
            "draftId": compact.get("draftId"),
            "name": compact.get("name"),
            "encounterTypeUuid": compact.get("encounterTypeUuid"),
            "status": compact.get("status"),
            "basketSummary": compact.get("basketSummary") or [],
        }
    return compact


# ---------------------------------------------------------------------------
# Private helpers


def _basket_field_count(draft: FormDraft) -> int:
    return sum(len(s.get("fields") or []) for s in (draft.basket.get("sections") or []))


def _schema_question_count(schema: dict[str, Any] | None) -> int:
    if not schema:
        return 0
    return sum(
        len(section.get("questions") or [])
        for page in schema.get("pages", []) or []
        for section in page.get("sections", []) or []
    )


def _summarize_basket_for_user(draft: FormDraft) -> str:
    sections = draft.basket.get("sections") or []
    field_count = sum(len(s.get("fields") or []) for s in sections)
    section_count = sum(1 for s in sections if (s.get("fields") or []))
    section_labels: list[str] = []
    for section in sections:
        if not (section.get("fields") or []):
            continue
        raw_label = str(section.get("label") or "").strip()
        section_id = str(section.get("sectionId") or "").strip()
        if raw_label and raw_label != section_id:
            section_labels.append(raw_label)
        else:
            section_labels.append(_humanize_section_label(section_id))
    if section_count == 0:
        return f"Current form: {field_count} question{'' if field_count == 1 else 's'}."
    section_list = ", ".join(label for label in section_labels if label) or "—"
    return (
        f"Current form: {field_count} question{'' if field_count == 1 else 's'} across "
        f"{section_count} section{'' if section_count == 1 else 's'} ({section_list})."
    )


def _humanize_section_label(value: str) -> str:
    if not value:
        return ""
    parts = [p for p in re.split(r"[_\-]+|\s+", str(value).strip()) if p]
    if not parts:
        return ""
    return " ".join(p.capitalize() if p.islower() else p for p in parts)


__all__ = [
    "run_gemma_tool_agent",
    "build_deterministic_summary",
    "compact_tool_result",
]
