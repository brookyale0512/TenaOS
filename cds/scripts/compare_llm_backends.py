#!/usr/bin/env python3
"""Run the form-builder and report-builder agent loops against BOTH
local Gemma (vLLM) and DeepSeek-R1 (Vertex Garden), capturing the full
event trace for each combination so the two backends can be compared.

Usage:
    python3 cds/scripts/compare_llm_backends.py \
        --backends gemma deepseek \
        --scenarios form_tb form_anc report_count report_cohort \
        --output cds/runtime/llm-comparison

What it does
============
For each (backend, scenario) it instantiates the same FormConversationDriver
or ReportConversationDriver used by the live service, drives the scripted
user turns end-to-end, and persists:

- ``trace.json``    — every event row from the SQLite draft store.
- ``summary.json``  — metric counts: turns, tool calls, durations.
- ``draft.json``    — the final ReportDraft / FormDraft (basket / spec).

The script is intentionally self-contained: it does NOT require the
cds-service HTTP daemon to be running; it pulls in the same modules
in-process and writes to a temporary SQLite DB to keep the live drafts
DB clean.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "cds" / "service"
sys.path.insert(0, str(SERVICE_DIR))

from cds_service.ciel import CielClient  # noqa: E402
from cds_service.config import Settings  # noqa: E402
from cds_service.form_builder_tool_loop import FormBuilderToolLoop  # noqa: E402
from cds_service.form_conversation import (  # noqa: E402
    ConversationTurn as FormTurn,
    FormConversationDriver,
)
from cds_service.form_drafts import FormDraftStore  # noqa: E402
from cds_service.llm_backend import DeepSeekVertexClient  # noqa: E402
from cds_service.openmrs_reader import OpenmrsReader  # noqa: E402
from cds_service.openmrs_writer import OpenmrsWriter  # noqa: E402
from cds_service.report_builder_tool_loop import ReportBuilderToolLoop  # noqa: E402
from cds_service.report_conversation import (  # noqa: E402
    ConversationTurn as ReportTurn,
    ReportConversationDriver,
)
from cds_service.report_drafts import ReportDraftStore  # noqa: E402
from cds_service.vllm import VllmClient  # noqa: E402

LOGGER = logging.getLogger("compare")

# Encounter type UUID for the form-builder scenarios. "Consultation" exists in
# the local OpenMRS demo dataset; if you swap demos, replace with another UUID
# from /openmrs/ws/rest/v1/encountertype.
CONSULTATION_UUID = "dd528487-82a5-4082-9c72-ed246bd49591"


# =========================================================================
# Scenarios
# =========================================================================


@dataclass
class FormScenario:
    key: str
    request_message: str            # First user message (name + intent)
    encounter_type_uuid: str = CONSULTATION_UUID
    encounter_label: str = "Consultation"
    follow_ups: list[str] = field(default_factory=list)


@dataclass
class ReportScenario:
    key: str
    request_message: str
    follow_ups: list[str] = field(default_factory=list)


FORM_SCENARIOS = {
    "form_tb": FormScenario(
        key="form_tb",
        request_message=(
            "Build a TB screening form. Capture patient history and risk factors "
            "(close TB contact, prior TB, HIV status), screening symptoms "
            "(cough lasting 2 weeks or more, fever, night sweats, weight loss), "
            "and a sputum collection check. Aim for 7 to 10 questions."
        ),
    ),
    "form_anc": FormScenario(
        key="form_anc",
        request_message=(
            "Build an antenatal first-visit form. Capture gestational age in weeks, "
            "gravidity, parity, last menstrual period, key danger signs in pregnancy "
            "(severe headache, vaginal bleeding, decreased fetal movements), and a "
            "history of hypertension. Aim for 7 to 10 questions."
        ),
    ),
}


REPORT_SCENARIOS = {
    "report_count": ReportScenario(
        key="report_count",
        request_message=(
            "How many patients had cough lasting 2 weeks or more in the last 6 months? "
            "Filter on patients where the cough screening question was answered Yes."
        ),
    ),
    "report_cohort": ReportScenario(
        key="report_cohort",
        request_message=(
            "Show me the list of patients who were screened TB positive (cough plus "
            "fever) in the last 12 months. I want their names, gender and age."
        ),
    ),
}


# =========================================================================
# Backend factory
# =========================================================================


def build_llm_client(backend: str, settings: Settings):
    if backend == "gemma":
        return VllmClient(settings)
    if backend == "deepseek":
        return DeepSeekVertexClient(settings)
    raise ValueError(f"unknown backend: {backend}")


# =========================================================================
# Scenario runners
# =========================================================================


def _make_stores(settings: Settings, db_path: Path) -> tuple[FormDraftStore, ReportDraftStore]:
    # Both stores accept a path; using the same file gives us a single DB.
    form_store = FormDraftStore(db_path)
    report_store = ReportDraftStore(db_path)
    return form_store, report_store


def _run_form_scenario(
    *,
    backend: str,
    scenario: FormScenario,
    settings: Settings,
    db_path: Path,
) -> dict[str, Any]:
    llm = build_llm_client(backend, settings)
    form_store, _ = _make_stores(settings, db_path)
    ciel = CielClient(settings)

    def writer_factory() -> OpenmrsWriter:
        return OpenmrsWriter(settings)

    loop = FormBuilderToolLoop(store=form_store, ciel=ciel, vllm=llm, writer_factory=writer_factory)
    driver = FormConversationDriver(store=form_store, ciel=ciel, loop=loop, vllm=llm)

    draft = form_store.create_draft(
        name=f"[{backend}] {scenario.key}",
        owner=None,
        description=f"LLM comparison run — backend={backend}",
        encounter_type_uuid=None,
    )
    started = time.monotonic()
    LOGGER.info("[%s][%s] kickoff", backend, scenario.key)
    driver.kickoff(draft.draft_id)

    LOGGER.info("[%s][%s] turn=name", backend, scenario.key)
    driver.handle_user_turn(
        draft.draft_id,
        FormTurn(kind="message", message=scenario.request_message),
    )

    LOGGER.info("[%s][%s] turn=encounter_type", backend, scenario.key)
    driver.handle_user_turn(
        draft.draft_id,
        FormTurn(
            kind="action",
            action="pick_encounter_type",
            payload={
                "encounterTypeUuid": scenario.encounter_type_uuid,
                "display": scenario.encounter_label,
            },
        ),
    )

    for idx, follow_up in enumerate(scenario.follow_ups, start=1):
        LOGGER.info("[%s][%s] follow_up=%d", backend, scenario.key, idx)
        driver.handle_user_turn(
            draft.draft_id,
            FormTurn(kind="message", message=follow_up),
        )

    elapsed = time.monotonic() - started
    final = form_store.get_draft(draft.draft_id)
    events = form_store.list_events(draft.draft_id, limit=10_000)
    LOGGER.info(
        "[%s][%s] DONE elapsed=%.1fs events=%d basket_sections=%d",
        backend,
        scenario.key,
        elapsed,
        len(events),
        len(final.basket.get("sections", [])),
    )
    return {
        "draftId": draft.draft_id,
        "elapsedSeconds": round(elapsed, 2),
        "events": [_event_to_dict(e) for e in events],
        "draft": _form_draft_to_dict(final),
    }


def _run_report_scenario(
    *,
    backend: str,
    scenario: ReportScenario,
    settings: Settings,
    db_path: Path,
) -> dict[str, Any]:
    llm = build_llm_client(backend, settings)
    _, report_store = _make_stores(settings, db_path)
    ciel = CielClient(settings)

    def reader_factory(progress=None) -> OpenmrsReader:
        return OpenmrsReader(settings, progress=progress)

    loop = ReportBuilderToolLoop(store=report_store, ciel=ciel, reader_factory=reader_factory)
    driver = ReportConversationDriver(store=report_store, ciel=ciel, loop=loop, vllm=llm)

    draft = report_store.create_draft(
        name=f"[{backend}] {scenario.key}",
        owner=None,
        description=f"LLM comparison run — backend={backend}",
    )
    started = time.monotonic()
    LOGGER.info("[%s][%s] kickoff", backend, scenario.key)
    driver.kickoff(draft.draft_id)

    LOGGER.info("[%s][%s] turn=request", backend, scenario.key)
    driver.handle_user_turn(
        draft.draft_id,
        ReportTurn(kind="message", message=scenario.request_message),
    )
    for idx, follow_up in enumerate(scenario.follow_ups, start=1):
        LOGGER.info("[%s][%s] follow_up=%d", backend, scenario.key, idx)
        driver.handle_user_turn(
            draft.draft_id,
            ReportTurn(kind="message", message=follow_up),
        )

    elapsed = time.monotonic() - started
    final = report_store.get_draft(draft.draft_id)
    events = report_store.list_events(draft.draft_id, limit=10_000)
    LOGGER.info(
        "[%s][%s] DONE elapsed=%.1fs events=%d filters=%d",
        backend,
        scenario.key,
        elapsed,
        len(events),
        len(final.spec.get("filters", [])),
    )
    return {
        "draftId": draft.draft_id,
        "elapsedSeconds": round(elapsed, 2),
        "events": [_event_to_dict(e) for e in events],
        "draft": _report_draft_to_dict(final),
    }


# =========================================================================
# Metrics
# =========================================================================


def _compute_metrics(events: list[dict[str, Any]], kind: str, draft: dict[str, Any]) -> dict[str, Any]:
    tool_calls: list[str] = []
    duplicate_rejects = 0
    concept_errors = 0
    search_phrases: list[str] = []
    model_call_phases: list[str] = []
    middleware_errors = 0
    agent_summary_present = False

    for ev in events:
        op = ev.get("operation") or ""
        payload = ev.get("payload") or {}
        if op == "model_tool_call":
            name = (payload.get("name") or payload.get("toolName") or "").strip()
            if name:
                tool_calls.append(name)
                if name == "search_ciel_seeds":
                    args = payload.get("arguments") or payload.get("args") or {}
                    if isinstance(args, dict):
                        phrase = args.get("query") or args.get("phrase") or ""
                        if phrase:
                            search_phrases.append(str(phrase))
        elif op == "duplicate_rejected":
            duplicate_rejects += 1
        elif op == "concept_error":
            concept_errors += 1
        elif op in {"conversation_error", "agent_loop_error", "kickoff_failed", "conversation_turn_failed"}:
            middleware_errors += 1
        elif op == "model_call":
            model_call_phases.append((payload.get("phase") or "")[:40])
        elif op in {"form_plan_applied", "form_edit_applied", "agent_summary", "report_run_complete"}:
            agent_summary_present = True

    metrics: dict[str, Any] = {
        "kind": kind,
        "totalEvents": len(events),
        "toolCalls": len(tool_calls),
        "toolCallBreakdown": _frequency(tool_calls),
        "searchPhraseCount": len(search_phrases),
        "uniqueSearchPhrases": len(set(p.strip().lower() for p in search_phrases if p.strip())),
        "duplicatePhrases": len(search_phrases) - len(set(p.strip().lower() for p in search_phrases if p.strip())),
        "duplicateRejects": duplicate_rejects,
        "conceptErrors": concept_errors,
        "middlewareErrors": middleware_errors,
        "modelCallPhases": _frequency(model_call_phases),
        "agentTerminatedCleanly": agent_summary_present or middleware_errors == 0,
    }

    if kind == "form":
        sections = draft.get("basket", {}).get("sections", [])
        field_total = sum(len(s.get("fields", []) or []) for s in sections)
        metrics.update(
            {
                "sections": len(sections),
                "fields": field_total,
                "minQuestionsAchieved": field_total >= 5,
            }
        )
    elif kind == "report":
        spec = draft.get("spec") or {}
        metrics.update(
            {
                "reportType": spec.get("reportType"),
                "filters": len(spec.get("filters") or []),
                "groupBy": len(spec.get("groupBy") or []),
                "hasDenominator": bool(spec.get("denominator")),
                "hasDateRange": bool(spec.get("dateRangeLabel") or spec.get("dateFrom")),
                "hasResult": bool(draft.get("lastResult")),
            }
        )
    return metrics


def _frequency(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# =========================================================================
# Dump helpers
# =========================================================================


def _event_to_dict(event: Any) -> dict[str, Any]:
    raw = event.__dict__ if hasattr(event, "__dict__") else dict(event)
    out = {
        "eventId": raw.get("event_id") or raw.get("eventId"),
        "createdAt": raw.get("created_at") or raw.get("createdAt"),
        "actor": raw.get("actor"),
        "operation": raw.get("operation"),
        "detail": raw.get("detail"),
        "payload": raw.get("payload"),
    }
    return out


def _form_draft_to_dict(draft: Any) -> dict[str, Any]:
    return {
        "draftId": draft.draft_id,
        "name": draft.name,
        "status": draft.status,
        "encounterTypeUuid": draft.encounter_type_uuid,
        "basket": draft.basket,
        "lastValidation": draft.last_validation,
    }


def _report_draft_to_dict(draft: Any) -> dict[str, Any]:
    return {
        "draftId": draft.draft_id,
        "name": draft.name,
        "status": draft.status,
        "reportType": draft.report_type,
        "spec": draft.spec,
        "lastResult": draft.last_result,
    }


# =========================================================================
# Main
# =========================================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backends", nargs="+", default=["gemma", "deepseek"])
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=list(FORM_SCENARIOS.keys()) + list(REPORT_SCENARIOS.keys()),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/var/www/tenaos/cds/runtime/llm-comparison"),
    )
    parser.add_argument("--db", type=Path, default=None,
                        help="SQLite DB path (defaults to temp file per run).")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    LOGGER.info("scenarios=%s backends=%s", args.scenarios, args.backends)

    settings = Settings.from_env()
    args.output.mkdir(parents=True, exist_ok=True)

    db_path = args.db or Path(tempfile.mktemp(prefix="llm-compare-", suffix=".sqlite3"))
    LOGGER.info("DB=%s OUT=%s", db_path, args.output)

    summary_index: dict[str, Any] = {"runs": []}

    for backend in args.backends:
        for scenario_key in args.scenarios:
            run_dir = args.output / backend / scenario_key
            run_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.monotonic()
            try:
                if scenario_key in FORM_SCENARIOS:
                    result = _run_form_scenario(
                        backend=backend,
                        scenario=FORM_SCENARIOS[scenario_key],
                        settings=settings,
                        db_path=db_path,
                    )
                    kind = "form"
                elif scenario_key in REPORT_SCENARIOS:
                    result = _run_report_scenario(
                        backend=backend,
                        scenario=REPORT_SCENARIOS[scenario_key],
                        settings=settings,
                        db_path=db_path,
                    )
                    kind = "report"
                else:
                    LOGGER.warning("unknown scenario: %s", scenario_key)
                    continue
                metrics = _compute_metrics(result["events"], kind, result["draft"])
                metrics["wallSeconds"] = result["elapsedSeconds"]
                metrics["backend"] = backend
                metrics["scenario"] = scenario_key
                metrics["draftId"] = result["draftId"]
                (run_dir / "trace.json").write_text(
                    json.dumps(result, indent=2, default=str), encoding="utf-8"
                )
                (run_dir / "draft.json").write_text(
                    json.dumps(result["draft"], indent=2, default=str), encoding="utf-8"
                )
                (run_dir / "summary.json").write_text(
                    json.dumps(metrics, indent=2), encoding="utf-8"
                )
                summary_index["runs"].append(metrics)
            except Exception as exc:
                LOGGER.exception(
                    "scenario %s on backend %s FAILED after %.1fs",
                    scenario_key,
                    backend,
                    time.monotonic() - t0,
                )
                err = {
                    "backend": backend,
                    "scenario": scenario_key,
                    "wallSeconds": round(time.monotonic() - t0, 2),
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
                (run_dir / "error.json").write_text(json.dumps(err, indent=2), encoding="utf-8")
                summary_index["runs"].append(err)

    (args.output / "index.json").write_text(json.dumps(summary_index, indent=2), encoding="utf-8")
    LOGGER.info("done — %d run(s) recorded", len(summary_index["runs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
