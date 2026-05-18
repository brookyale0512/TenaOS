#!/usr/bin/env python3
"""Form-builder eval runner.

Drives the live CDS service end-to-end for each prompt in ``prompts.json``,
records every event the agent produces, and scores the final basket against
the SME-graded gold concepts.

Usage::

    python cds/evals/form_builder/run_eval.py \
        --cds-base-url http://127.0.0.1:8095 \
        --prompts cds/evals/form_builder/prompts.json \
        --out cds/evals/form_builder/runs

A run produces three files per prompt under ``runs/<timestamp>/``:

    * ``<id>.events.json``   — full draft event log
    * ``<id>.basket.json``   — final basket + schema metadata
    * ``<id>.score.json``    — per-prompt scoring (recall, latency, …)

…plus a top-level ``summary.json`` with aggregate numbers suitable to paste
into the challenge submission.

The runner is deliberately dependency-free (urllib only) so it runs anywhere
the CDS service is reachable.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _http_json(method: str, url: str, body: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"} if data is not None else {"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8") or "{}"
        return json.loads(raw)


def _wait_for_state(
    cds_base_url: str,
    draft_id: str,
    *,
    expected_states: set[str],
    expect_basket_non_empty: bool = False,
    timeout: float = 90.0,
    poll: float = 1.0,
) -> dict[str, Any]:
    """Poll the draft until it reaches one of ``expected_states`` (or basket is non-empty)."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _http_json("GET", f"{cds_base_url}/forms/drafts/{draft_id}")
        state = last.get("conversationState")
        basket_sections = last.get("basket", {}).get("sections") or []
        basket_fields = sum(len(section.get("fields") or []) for section in basket_sections)
        if expect_basket_non_empty and basket_fields > 0:
            return last
        if state in expected_states:
            if not expect_basket_non_empty or basket_fields > 0:
                return last
        time.sleep(poll)
    return last


def _wait_for_agent_completion(
    cds_base_url: str,
    draft_id: str,
    *,
    timeout: float = 300.0,
    poll: float = 2.0,
) -> dict[str, Any]:
    """Wait until the agent has emitted ``form_plan_applied`` or ``form_edit_applied``.

    The previous loop stopped as soon as the basket had any fields, which
    caught the agent mid-build and produced misleadingly small baskets in
    the eval. The agent's final-message events are the canonical "I am
    done" signal — wait for one of those (or the timeout).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events_payload = _http_json("GET", f"{cds_base_url}/forms/drafts/{draft_id}/events")
        events = events_payload.get("events") or []
        for event in reversed(events):
            if event.get("operation") in {"form_plan_applied", "form_edit_applied"}:
                return _http_json("GET", f"{cds_base_url}/forms/drafts/{draft_id}")
        time.sleep(poll)
    return _http_json("GET", f"{cds_base_url}/forms/drafts/{draft_id}")


def _resolve_encounter_type_uuid(cds_base_url: str, display: str) -> str:
    payload = _http_json("GET", f"{cds_base_url}/forms/encounter-types")
    options = payload.get("encounterTypes") or []
    for option in options:
        if (option.get("display") or "").strip().lower() == display.strip().lower():
            return str(option["uuid"])
    if options:
        return str(options[0]["uuid"])
    raise RuntimeError("No encounter types available from the CDS service")


def _score_basket(prompt: dict[str, Any], basket: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute per-prompt metrics.

    Recall  = fraction of ``requireAnyOf`` clusters that the basket covers
              (a cluster is covered when any of its conceptIds is in the basket).
    Drug/Diagnosis pollution is reported but does not affect recall.
    """
    sections = basket.get("sections") or []
    field_concept_ids: set[str] = set()
    for section in sections:
        for field in section.get("fields") or []:
            cid = str(field.get("conceptId") or "")
            if cid:
                field_concept_ids.add(cid)

    clusters = prompt.get("requireAnyOf") or []
    cluster_hits: list[dict[str, Any]] = []
    for cluster in clusters:
        gold_ids = {str(cid) for cid in cluster.get("conceptIds") or []}
        matched = sorted(field_concept_ids & gold_ids)
        cluster_hits.append(
            {
                "label": cluster.get("label"),
                "expectedAnyOf": sorted(gold_ids),
                "matched": matched,
                "covered": bool(matched),
            }
        )
    covered = sum(1 for hit in cluster_hits if hit["covered"])
    total = len(clusters) or 1
    recall = covered / total

    min_q = prompt.get("minQuestions") or 0
    max_q = prompt.get("maxQuestions") or 999
    size_ok = min_q <= len(field_concept_ids) <= max_q

    # Drug/Diagnosis pollution: scan basket for warnings or rejected ops in
    # the event log. The new middleware rejects them, so a "pollution" score
    # > 0 here means the model still tried to add them.
    rejected_classes: list[str] = []
    for event in events:
        if event.get("operation") != "update_form_draft":
            continue
        payload = event.get("payload") or {}
        for warning in payload.get("warnings") or []:
            reason = (warning.get("reason") or "").lower()
            for cls in prompt.get("forbiddenClasses") or []:
                if cls.lower() in reason:
                    rejected_classes.append(cls)
                    break

    tool_call_events = [e for e in events if e.get("operation") == "model_tool_call"]
    ciel_search_events = [e for e in events if e.get("operation") == "search_ciel_seeds"]

    return {
        "recall": round(recall, 3),
        "clustersCovered": covered,
        "clustersTotal": total,
        "clusterHits": cluster_hits,
        "fieldCount": len(field_concept_ids),
        "sizeOk": size_ok,
        "drugDiagnosisRejectAttempts": rejected_classes,
        "toolCallCount": len(tool_call_events),
        "cielSearchCount": len(ciel_search_events),
    }


def run_one(cds_base_url: str, prompt: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    started = time.monotonic()
    draft = _http_json("POST", f"{cds_base_url}/forms/drafts", body={})
    draft_id = draft["draftId"]

    # Stage 1: name turn.
    _http_json("POST", f"{cds_base_url}/forms/drafts/{draft_id}/messages", body={"message": prompt["request"]})
    _wait_for_state(cds_base_url, draft_id, expected_states={"awaiting_encounter_type"}, timeout=30.0)

    # Stage 2: encounter type pick.
    encounter_uuid = _resolve_encounter_type_uuid(cds_base_url, prompt.get("encounterType") or "Consultation")
    _http_json(
        "POST",
        f"{cds_base_url}/forms/drafts/{draft_id}/actions",
        body={"action": "pick_encounter_type", "payload": {"encounterTypeUuid": encounter_uuid}},
    )

    # Stage 3: wait for the agent to emit form_plan_applied / form_edit_applied
    # rather than just for any basket activity. The previous "basket non-empty"
    # heuristic caught the agent mid-build at 3 fields when the final plan
    # produced 7.
    final = _wait_for_agent_completion(
        cds_base_url,
        draft_id,
        timeout=300.0,
        poll=2.0,
    )

    events_payload = _http_json("GET", f"{cds_base_url}/forms/drafts/{draft_id}/events")
    events = events_payload.get("events") or []
    schema_payload = _http_json("GET", f"{cds_base_url}/forms/drafts/{draft_id}/schema")

    score = _score_basket(prompt, final.get("basket") or {}, events)
    score["elapsedSeconds"] = round(time.monotonic() - started, 2)
    score["draftId"] = draft_id
    score["schemaValid"] = bool(
        schema_payload.get("validation")
        and not any(issue.get("severity") == "error" for issue in (schema_payload["validation"].get("issues") or []))
    )

    (out_dir / f"{prompt['id']}.events.json").write_text(json.dumps(events, indent=2))
    (out_dir / f"{prompt['id']}.basket.json").write_text(
        json.dumps(
            {
                "basket": final.get("basket"),
                "lastSchemaSummary": {
                    "name": (schema_payload.get("schema") or {}).get("name"),
                    "encounterType": (schema_payload.get("schema") or {}).get("encounterType"),
                    "questionCount": sum(
                        len(section.get("questions") or [])
                        for page in (schema_payload.get("schema") or {}).get("pages") or []
                        for section in page.get("sections") or []
                    ),
                },
                "validation": schema_payload.get("validation"),
            },
            indent=2,
        ),
    )
    (out_dir / f"{prompt['id']}.score.json").write_text(json.dumps(score, indent=2))
    return score


def main() -> int:
    parser = argparse.ArgumentParser(description="Form-builder eval runner")
    parser.add_argument("--cds-base-url", default="http://127.0.0.1:8095")
    parser.add_argument("--prompts", default=str(Path(__file__).parent / "prompts.json"))
    parser.add_argument("--out", default=str(Path(__file__).parent / "runs"))
    parser.add_argument("--filter", default=None, help="Only run prompts whose id matches this substring.")
    args = parser.parse_args()

    prompts = json.loads(Path(args.prompts).read_text())["prompts"]
    if args.filter:
        prompts = [prompt for prompt in prompts if args.filter in prompt["id"]]
    if not prompts:
        print("No prompts to run.")
        return 1

    run_id = _now_iso()
    out_dir = Path(args.out) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    scores: list[dict[str, Any]] = []
    for prompt in prompts:
        print(f"[{prompt['id']}] running…", flush=True)
        try:
            score = run_one(args.cds_base_url, prompt, out_dir)
        except urllib.error.URLError as exc:
            score = {"promptId": prompt["id"], "error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:
            score = {"promptId": prompt["id"], "error": f"{type(exc).__name__}: {exc}"}
        score = {"promptId": prompt["id"], "request": prompt["request"], **score}
        scores.append(score)
        if "error" in score:
            print(f"  -> ERROR: {score['error']}")
        else:
            print(
                f"  -> recall={score['recall']} ({score['clustersCovered']}/{score['clustersTotal']})"
                f"  fields={score['fieldCount']}  schemaValid={score.get('schemaValid')}"
                f"  tools={score['toolCallCount']}  elapsed={score['elapsedSeconds']}s"
            )

    valid_scores = [score for score in scores if "recall" in score]
    summary: dict[str, Any] = {
        "runId": run_id,
        "cdsBaseUrl": args.cds_base_url,
        "promptCount": len(prompts),
        "completed": len(valid_scores),
        "failed": len(scores) - len(valid_scores),
        "meanRecall": round(sum(score["recall"] for score in valid_scores) / max(1, len(valid_scores)), 3),
        "schemaValidRate": round(sum(1 for score in valid_scores if score.get("schemaValid")) / max(1, len(valid_scores)), 3),
        "medianElapsedSeconds": _median([score["elapsedSeconds"] for score in valid_scores]),
        "medianToolCalls": _median([score["toolCallCount"] for score in valid_scores]),
        "perPrompt": scores,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(
        {key: summary[key] for key in ("runId", "completed", "failed", "meanRecall", "schemaValidRate", "medianElapsedSeconds", "medianToolCalls")},
        indent=2,
    ))
    return 0


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2 == 0:
        return round((sorted_values[mid - 1] + sorted_values[mid]) / 2, 2)
    return round(sorted_values[mid], 2)


if __name__ == "__main__":
    sys.exit(main())
