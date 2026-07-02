"""Orchestrator for the v2 grounded form-builder pipeline.

Public entry point :func:`run_form_pipeline_agent` has the same signature as the
legacy ``form_agent_runner.run_gemma_tool_agent`` so the conversation driver can
switch between them on a flag. It wires the phases together:

    research_phase -> ciel_resolution -> (coverage repair) -> build -> summary

The user-facing summary and final event names match the legacy runner so the
frontend chat renders identically during A/B evaluation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from ..form_agent_runner import build_deterministic_summary
from .ciel_resolution import run_ciel_resolution
from .repair import needs_repair, run_coverage_repair
from .research_phase import run_research_phase
from .worklist import QuestionWorklist

if TYPE_CHECKING:
    from ..config import Settings
    from ..form_builder_tool_loop import FormBuilderToolLoop
    from ..form_drafts import FormDraft, FormDraftStore
    from ..llm_client import LlmClient

_LOGGER = logging.getLogger("tenaos.tena_agent.form_pipeline.runner")


def run_form_pipeline_agent(
    *,
    store: "FormDraftStore",
    loop: "FormBuilderToolLoop",
    llm: "LlmClient",
    draft: "FormDraft",
    request: str,
    mode: Literal["create", "edit"],
    settings: "Settings",
) -> None:
    """Run research -> resolution -> repair -> build -> deterministic summary."""
    from ..form_conversation import OP_FORM_EDIT_APPLIED, OP_FORM_PLAN_APPLIED

    starting_field_count = _field_count(draft)
    target_min_fields = int(getattr(settings, "form_agent_target_min_fields", 6))

    # Phase A: grounded research -> worklist (always runs; no keyword gate).
    worklist = run_research_phase(
        llm=llm, store=store, draft=draft, request=request, mode=mode, settings=settings
    )

    # Phase B: resolve worklist items against CIEL and commit the basket.
    run_ciel_resolution(
        store=store,
        loop=loop,
        llm=llm,
        draft=store.get_draft(draft.draft_id),
        request=request,
        worklist=worklist,
        settings=settings,
    )

    # One bounded, generic coverage-repair pass (create mode only).
    latest = store.get_draft(draft.draft_id)
    if mode == "create" and needs_repair(worklist, latest, target_min_fields=target_min_fields):
        try:
            run_coverage_repair(store=store, loop=loop, llm=llm, draft=latest, worklist=worklist)
        except Exception as exc:  # repair must never crash the turn
            _LOGGER.warning("Coverage repair failed draft=%s: %s", draft.draft_id, exc, exc_info=True)
        latest = store.get_draft(draft.draft_id)

    # Ensure a schema exists when the basket has fields.
    if latest.last_schema is None and _field_count(latest) > 0:
        loop.build_form_schema(draft.draft_id)
        latest = store.get_draft(draft.draft_id)

    store.update_draft(
        draft.draft_id,
        conversation_state="awaiting_question",
        conversation_context={"lastAgentMode": mode},
    )

    final_text = build_deterministic_summary(
        mode=mode,
        latest=latest,
        starting_field_count=starting_field_count,
        target_min_fields=target_min_fields,
        warnings=_collect_warnings(store, draft.draft_id),
    )
    store.append_event(
        draft.draft_id,
        actor="gemma",
        operation=OP_FORM_PLAN_APPLIED if mode == "create" else OP_FORM_EDIT_APPLIED,
        detail=final_text,
        payload={"mode": mode, "finalText": final_text, "pipeline": "v2"},
    )


def _collect_warnings(store: "FormDraftStore", draft_id: str) -> list[str]:
    """Gather distinct apply-time rejection reasons from this run's tool results."""
    from ..form_conversation import OP_TOOL_RESULT

    reasons: list[str] = []
    for event in store.list_events(draft_id, limit=240):
        if event.operation != OP_TOOL_RESULT:
            continue
        result = event.payload.get("result") if isinstance(event.payload, dict) else None
        if not isinstance(result, dict):
            continue
        for warning in result.get("warnings") or []:
            reason = str((warning or {}).get("reason") or "").strip()
            if reason and reason not in reasons:
                reasons.append(reason)
    return reasons


def _field_count(draft: "FormDraft") -> int:
    return sum(len(s.get("fields") or []) for s in (draft.basket.get("sections") or []))


__all__ = ["run_form_pipeline_agent"]
