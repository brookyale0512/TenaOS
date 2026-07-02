#!/usr/bin/env python3
"""Form-quality scorer + live-model eval CLI for the grounded v2 pipeline.

The scorer (``score_form`` / ``quality_gate``) is deterministic and dependency
-light so it is reused by pytest. The CLI drives the REAL
``FormConversationDriver`` (Gemma + CIEL + WHO/MSF KB + OpenMRS) over the golden
dataset and applies the gate, mirroring ``scripts/optimization/ab_form_eval.py``
but for a single v2 arm.

Quality dimensions:
  * coverage          — fraction of required concept clusters represented
  * sizeOk            — committed field count within [minQuestions, maxQuestions]
  * schemaValid       — a schema built with no error-severity validation issues
  * hallucinatedCodes — committed concept ids that do not resolve in CIEL
  * retiredCodes      — committed concept ids whose CIEL concept is retired

Run inside the TenaOS container (where model/CIEL/KB live):
    python3 -m evals.form_quality --dataset evals/golden_forms.json --out ./eval_out
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Dataset


@dataclass(slots=True)
class FormQualitySpec:
    id: str
    request: str
    encounter_type: str = "Consultation"
    min_questions: int = 1
    max_questions: int = 999
    forbidden_classes: list[str] = field(default_factory=list)
    require_any_of: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FormQualitySpec":
        return cls(
            id=str(raw["id"]),
            request=str(raw["request"]),
            encounter_type=str(raw.get("encounterType") or "Consultation"),
            min_questions=int(raw.get("minQuestions") or 1),
            max_questions=int(raw.get("maxQuestions") or 999),
            forbidden_classes=[str(c) for c in (raw.get("forbiddenClasses") or [])],
            require_any_of=list(raw.get("requireAnyOf") or []),
        )


def load_specs(path: str | Path) -> list[FormQualitySpec]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [FormQualitySpec.from_dict(item) for item in payload.get("specs", [])]


# ---------------------------------------------------------------------------
# Scorer (deterministic, reused by pytest)


def field_concept_ids(draft: Any) -> list[str]:
    basket = getattr(draft, "basket", None)
    if basket is None and isinstance(draft, dict):
        basket = draft.get("basket")
    ids: list[str] = []
    for section in (basket or {}).get("sections") or []:
        for fld in section.get("fields") or []:
            cid = str(fld.get("conceptId") or "").strip()
            if cid:
                ids.append(cid)
    return ids


def _schema_valid(draft: Any) -> bool:
    last_schema = getattr(draft, "last_schema", None)
    last_validation = getattr(draft, "last_validation", None)
    if last_schema is None and isinstance(draft, dict):
        last_schema = draft.get("last_schema")
        last_validation = draft.get("last_validation")
    issues = (last_validation or {}).get("issues") or []
    return bool(last_schema) and not any(i.get("severity") == "error" for i in issues)


def score_form(spec: FormQualitySpec, draft: Any, ciel: Any | None = None) -> dict[str, Any]:
    """Score one committed draft against a golden spec.

    ``ciel`` is optional: when provided, hallucinated/retired code checks run by
    resolving each committed concept id against the CIEL store.
    """
    ids = field_concept_ids(draft)
    id_set = set(ids)

    clusters = spec.require_any_of
    cluster_hits = []
    for cluster in clusters:
        gold = {str(c) for c in cluster.get("conceptIds") or []}
        matched = sorted(id_set & gold)
        cluster_hits.append({"label": cluster.get("label"), "matched": matched, "covered": bool(matched)})
    covered = sum(1 for h in cluster_hits if h["covered"])
    coverage = covered / (len(clusters) or 1)

    hallucinated: list[str] = []
    retired: list[str] = []
    if ciel is not None:
        for cid in id_set:
            try:
                bundle = ciel.get_concept_bundle(cid)
            except Exception:
                hallucinated.append(cid)
                continue
            if (bundle.get("concept") or {}).get("retired"):
                retired.append(cid)

    return {
        "id": spec.id,
        "coverage": round(coverage, 3),
        "clustersCovered": covered,
        "clustersTotal": len(clusters),
        "clusterHits": cluster_hits,
        "conceptIds": sorted(id_set),
        "fieldCount": len(id_set),
        "sizeOk": spec.min_questions <= len(id_set) <= spec.max_questions,
        "schemaValid": _schema_valid(draft),
        "hallucinatedCodes": sorted(hallucinated),
        "retiredCodes": sorted(retired),
    }


def quality_gate(scores: list[dict[str, Any]], *, min_coverage: float = 0.75) -> dict[str, Any]:
    """Aggregate gate: high coverage, valid schemas, no hallucinated/retired codes."""
    n = len(scores) or 1
    mean_coverage = sum(s.get("coverage", 0.0) for s in scores) / n
    schema_valid_rate = sum(1 for s in scores if s.get("schemaValid")) / n
    size_ok_rate = sum(1 for s in scores if s.get("sizeOk")) / n
    any_hallucinated = any(s.get("hallucinatedCodes") for s in scores)
    any_retired = any(s.get("retiredCodes") for s in scores)

    reasons = [
        f"meanCoverage {mean_coverage:.3f} {'>=' if mean_coverage >= min_coverage else '<'} {min_coverage}",
        f"schemaValidRate {schema_valid_rate:.3f} {'==' if schema_valid_rate >= 1.0 else '<'} 1.0",
        f"no hallucinated codes: {'yes' if not any_hallucinated else 'NO'}",
        f"no retired codes: {'yes' if not any_retired else 'NO'}",
    ]
    passed = (
        mean_coverage >= min_coverage
        and schema_valid_rate >= 1.0
        and not any_hallucinated
        and not any_retired
    )
    return {
        "pass": bool(passed),
        "meanCoverage": round(mean_coverage, 3),
        "schemaValidRate": round(schema_valid_rate, 3),
        "sizeOkRate": round(size_ok_rate, 3),
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Live-model CLI (requires the TenaOS runtime: model + CIEL + KB + OpenMRS)


def _run_live(specs: list[FormQualitySpec], out_dir: Path) -> int:
    import time
    from dataclasses import replace
    from tempfile import mkdtemp

    from tena_agent_service.ciel import CielClient
    from tena_agent_service.config import Settings
    from tena_agent_service.form_builder_tool_loop import FormBuilderToolLoop
    from tena_agent_service.form_conversation import ConversationTurn, FormConversationDriver
    from tena_agent_service.form_drafts import FormDraftStore
    from tena_agent_service.llm_backend import make_llm_client
    from tena_agent_service.openmrs_writer import OpenmrsWriter

    settings = replace(Settings.from_env(), form_agent_pipeline_v2=True)
    db_dir = Path(mkdtemp(prefix="form-eval-"))
    ciel = CielClient(settings)
    llm = make_llm_client(settings)

    def writer_factory() -> Any:
        return OpenmrsWriter(settings, authorization=None, cookie=None)

    def _encounter_uuid(display: str) -> str:
        options = writer_factory().list_encounter_types(limit=100) or []
        for option in options:
            if (option.get("display") or "").strip().lower() == display.strip().lower():
                return str(option["uuid"])
        if options:
            return str(options[0]["uuid"])
        raise RuntimeError("No encounter types available from OpenMRS")

    scores: list[dict[str, Any]] = []
    for spec in specs:
        store = FormDraftStore(db_dir / f"{spec.id}.sqlite3")
        loop = FormBuilderToolLoop(store=store, ciel=ciel, llm=llm, writer_factory=writer_factory)
        driver = FormConversationDriver(store=store, ciel=ciel, loop=loop, llm=llm, settings=settings)
        draft = store.create_draft(
            name=spec.id, owner="eval", description=None, encounter_type_uuid=_encounter_uuid(spec.encounter_type)
        )
        store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        started = time.monotonic()
        driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message=spec.request))
        elapsed = round(time.monotonic() - started, 1)
        final = store.get_draft(draft.draft_id)
        score = score_form(spec, final, ciel)
        score["elapsedSeconds"] = elapsed
        print(
            f"[{spec.id}] coverage={score['coverage']} fields={score['fieldCount']} "
            f"schemaValid={score['schemaValid']} hallucinated={score['hallucinatedCodes']} {elapsed}s",
            flush=True,
        )
        scores.append(score)

    gate = quality_gate(scores)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "form_quality.json").write_text(
        json.dumps({"scores": scores, "gate": gate}, indent=2), encoding="utf-8"
    )
    print("\n=== quality gate:", "PASS" if gate["pass"] else "FAIL", "===")
    for reason in gate["reasons"]:
        print(f"  - {reason}")
    return 0 if gate["pass"] else 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parent / "golden_forms.json")
    parser.add_argument("--out", type=Path, default=Path("./eval_out"))
    args = parser.parse_args()
    specs = load_specs(args.dataset)
    return _run_live(specs, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
