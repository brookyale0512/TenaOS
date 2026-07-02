"""Grounded two-phase form-builder pipeline (v2).

This package is the simple, production-oriented replacement for the heavily
scripted loop in ``form_agent_runner``. It is structured as two model-driven
phases joined by an explicit data contract, then a deterministic compile and a
single bounded repair:

    Phase A  research_phase   -> QuestionWorklist   (grounded on WHO/MSF KB)
    Phase B  ciel_resolution  -> basket operations  (grounded on CIEL)
    Phase C  build_form_schema (deterministic, unchanged)
    repair   one bounded coverage repair pass (generic, no hardcoded concepts)

The orchestrator is :func:`runner.run_form_pipeline_agent`. It is wired behind
the ``form_agent_pipeline_v2`` settings flag so it can run in parallel with the
legacy runner during A/B evaluation. All SSE event operation names mirror the
legacy runner so the frontend needs no changes.
"""

from __future__ import annotations

from .worklist import QuestionWorklist, WorklistItem

__all__ = ["QuestionWorklist", "WorklistItem", "run_form_pipeline_agent"]


def run_form_pipeline_agent(*args, **kwargs):  # noqa: ANN002, ANN003 - thin lazy shim
    """Lazy import shim so importing the package does not pull the LLM stack."""
    from .runner import run_form_pipeline_agent as _impl

    return _impl(*args, **kwargs)
