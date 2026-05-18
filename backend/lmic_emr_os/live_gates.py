from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .change_control import ChangeControlStateStore
from .ciel import CielTerminologyService
from .config_model import ClinicConfigModel
from .onboarding import OnboardingEngine
from .runtime_apply import BundleApplier

DEFAULT_LIVE_GATE_FIXTURES = [
    "examples/emr-os/archetypes/general-outpatient-clinic.json",
    "examples/emr-os/archetypes/specialty-clinic.json",
    "examples/emr-os/archetypes/hospital-composition.json",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_live_gate_fixtures(repo_root: str | Path, fixture_paths: list[str] | None = None) -> list[Path]:
    root = Path(repo_root)
    configured_paths = fixture_paths or DEFAULT_LIVE_GATE_FIXTURES
    resolved: list[Path] = []
    for fixture in configured_paths:
        path = Path(fixture)
        if not path.is_absolute():
            path = root / path
        resolved.append(path)
    return resolved


def run_live_acceptance_gates(
    repo_root: str | Path,
    *,
    fixture_paths: list[str] | None = None,
    output_dir: str | Path | None = None,
    state_dir: str | Path | None = None,
    approver: str = "live-gate",
    environment: str = "",
    products: list[str] | None = None,
    restart_services: bool = True,
    run_verify: bool = True,
    keep_applied: bool = False,
    keep_going: bool = False,
    skip_concept_validation: bool = False,
    engine: OnboardingEngine | None = None,
    applier: BundleApplier | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    output_root = Path(output_dir) if output_dir else root / "data" / "emr-os-live-gates"
    bundle_root = output_root / "bundles"
    summary_path = output_root / "live-gate-summary.json"
    fixtures = resolve_live_gate_fixtures(root, fixture_paths)

    if engine is None:
        concept_resolver = None if skip_concept_validation else CielTerminologyService(root)
        engine = OnboardingEngine(concept_resolver=concept_resolver)
    if applier is None:
        gate_state_dir = Path(state_dir) if state_dir else output_root / "state"
        applier = BundleApplier(root, state_store=ChangeControlStateStore(gate_state_dir))

    output_root.mkdir(parents=True, exist_ok=True)
    bundle_root.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "startedAt": _utc_now(),
        "repoRoot": str(root),
        "outputDir": str(output_root),
        "summaryPath": str(summary_path),
        "stateDir": str(applier.state_store.root_dir),
        "approver": approver,
        "environment": environment,
        "products": list(products or []),
        "restartServices": restart_services,
        "runVerify": run_verify,
        "skipConceptValidation": skip_concept_validation,
        "keepApplied": keep_applied,
        "keepGoing": keep_going,
        "fixtures": [],
        "ok": True,
    }

    for fixture_path in fixtures:
        config = ClinicConfigModel.from_json_file(fixture_path)
        bundle = engine.build_change_bundle(config, bundle_root / fixture_path.stem)
        applier.register_bundle(bundle.output_dir)
        if config.governance.approval_required:
            applier.approve_bundle(
                bundle.output_dir,
                approver,
                note=f"live gate approval for {fixture_path.name}",
                environment=environment,
            )

        fixture_result: dict[str, Any] = {
            "fixture": fixture_path.stem,
            "configPath": str(fixture_path),
            "changeId": bundle.change_id,
            "bundleDir": bundle.output_dir,
        }

        apply_outcome = applier.apply_change(
            bundle.output_dir,
            products=products,
            restart_services=restart_services,
            run_verify=run_verify,
            environment=environment,
        )
        fixture_result["apply"] = asdict(apply_outcome)
        apply_ok = apply_outcome.status == "applied"

        if apply_ok and not keep_applied:
            rollback_outcome = applier.rollback_change(
                bundle.output_dir,
                products=products,
                restart_services=restart_services,
                run_verify=run_verify,
                environment=environment,
            )
            fixture_result["rollback"] = asdict(rollback_outcome)
            apply_ok = rollback_outcome.status == "rolled-back"

        fixture_result["ok"] = apply_ok
        summary["fixtures"].append(fixture_result)
        if not apply_ok:
            summary["ok"] = False
            if not keep_going:
                break

    summary["completedAt"] = _utc_now()
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
