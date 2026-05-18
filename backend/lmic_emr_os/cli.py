from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .change_control import ChangeControlStateStore
from .ciel import CielTerminologyService
from .config_model import ClinicConfigModel
from .department_composition import compose_from_plan_file, list_department_packs
from .live_gates import run_live_acceptance_gates
from .onboarding import OnboardingEngine
from .operational_analysis import analyze_operational_policies
from .openmrs_pack import OpenMRSPackCompiler
from .runtime_apply import BundleApplier
from .runtime_inventory import build_runtime_inventory, write_runtime_inventory
from .validation import validate_clinic_config
from .verification_plan import build_verification_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LMIC EMR OS backend control plane tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory_cmd = subparsers.add_parser("inventory", help="Generate runtime capability inventory.")
    inventory_cmd.add_argument("--repo-root", default=".")
    inventory_cmd.add_argument("--output-dir", default="docs/emr-os")

    validate_cmd = subparsers.add_parser("validate-config", help="Validate a clinic configuration file.")
    validate_cmd.add_argument("config_path")
    validate_cmd.add_argument("--repo-root", default=".")
    validate_cmd.add_argument("--validate-concepts", action="store_true")

    preview_cmd = subparsers.add_parser("preview", help="Preview onboarding output for a clinic configuration file.")
    preview_cmd.add_argument("config_path")
    preview_cmd.add_argument("--repo-root", default=".")
    preview_cmd.add_argument("--skip-concept-validation", action="store_true")

    analyze_cmd = subparsers.add_parser("analyze-operations", help="Analyze workflow topology and operational policies.")
    analyze_cmd.add_argument("config_path")

    verification_cmd = subparsers.add_parser("verification-plan", help="Generate a cross-product verification plan.")
    verification_cmd.add_argument("config_path")

    list_packs_cmd = subparsers.add_parser("list-department-packs", help="List reusable department packs.")
    list_packs_cmd.add_argument("--pack-dir")

    compose_cmd = subparsers.add_parser("compose-hospital", help="Compose a clinic config from department packs.")
    compose_cmd.add_argument("plan_path")
    compose_cmd.add_argument("output_path")
    compose_cmd.add_argument("--pack-dir")

    compile_cmd = subparsers.add_parser("compile-openmrs", help="Compile an OpenMRS configuration pack.")
    compile_cmd.add_argument("config_path")
    compile_cmd.add_argument("output_dir")
    compile_cmd.add_argument("--include-html-forms", action="store_true")
    compile_cmd.add_argument("--repo-root", default=".")
    compile_cmd.add_argument("--skip-concept-validation", action="store_true")

    bundle_cmd = subparsers.add_parser("build-change-bundle", help="Build a full change bundle with adapters.")
    bundle_cmd.add_argument("config_path")
    bundle_cmd.add_argument("output_dir")
    bundle_cmd.add_argument("--repo-root", default=".")
    bundle_cmd.add_argument("--skip-concept-validation", action="store_true")

    ciel_store_cmd = subparsers.add_parser("build-ciel-store", help="Build the local CIEL SQLite store if missing.")
    ciel_store_cmd.add_argument("--repo-root", default=".")
    ciel_store_cmd.add_argument("--sqlite-path")
    ciel_store_cmd.add_argument("--export-path")

    ciel_validate_cmd = subparsers.add_parser("validate-concept", help="Validate a single concept reference.")
    ciel_validate_cmd.add_argument("concept_ref")
    ciel_validate_cmd.add_argument("--repo-root", default=".")
    ciel_validate_cmd.add_argument("--sqlite-path")
    ciel_validate_cmd.add_argument("--export-path")

    list_changes_cmd = subparsers.add_parser("list-changes", help="List tracked change bundles.")
    list_changes_cmd.add_argument("--repo-root", default=".")
    list_changes_cmd.add_argument("--state-dir")

    approve_cmd = subparsers.add_parser("approve-change", help="Approve a generated change bundle.")
    approve_cmd.add_argument("change_ref")
    approve_cmd.add_argument("--repo-root", default=".")
    approve_cmd.add_argument("--state-dir")
    approve_cmd.add_argument("--approver", required=True)
    approve_cmd.add_argument("--note", default="")
    approve_cmd.add_argument("--environment", default="")

    promote_cmd = subparsers.add_parser("promote-change", help="Record promotion of a generated change bundle.")
    promote_cmd.add_argument("change_ref")
    promote_cmd.add_argument("--repo-root", default=".")
    promote_cmd.add_argument("--state-dir")
    promote_cmd.add_argument("--from-environment", required=True)
    promote_cmd.add_argument("--to-environment", required=True)
    promote_cmd.add_argument("--promoted-by", required=True)
    promote_cmd.add_argument("--note", default="")

    apply_cmd = subparsers.add_parser("apply-change", help="Apply a change bundle to runtime surfaces.")
    apply_cmd.add_argument("change_ref")
    apply_cmd.add_argument("--repo-root", default=".")
    apply_cmd.add_argument("--state-dir")
    apply_cmd.add_argument("--dry-run", action="store_true")
    apply_cmd.add_argument("--restart-services", action="store_true")
    apply_cmd.add_argument("--run-verify", action="store_true")
    apply_cmd.add_argument("--product", action="append", dest="products")
    apply_cmd.add_argument("--environment", default="")

    rollback_cmd = subparsers.add_parser("rollback-change", help="Roll back a previously applied change bundle.")
    rollback_cmd.add_argument("change_ref")
    rollback_cmd.add_argument("--repo-root", default=".")
    rollback_cmd.add_argument("--state-dir")
    rollback_cmd.add_argument("--run-id")
    rollback_cmd.add_argument("--restart-services", action="store_true")
    rollback_cmd.add_argument("--run-verify", action="store_true")
    rollback_cmd.add_argument("--product", action="append", dest="products")
    rollback_cmd.add_argument("--environment", default="")

    live_gates_cmd = subparsers.add_parser(
        "run-live-gates",
        help="Run live apply/verify/rollback gates for golden clinic fixtures.",
    )
    live_gates_cmd.add_argument("--repo-root", default=".")
    live_gates_cmd.add_argument("--output-dir", default="data/emr-os-live-gates")
    live_gates_cmd.add_argument("--state-dir")
    live_gates_cmd.add_argument("--approver", default="live-gate")
    live_gates_cmd.add_argument("--environment", default="")
    live_gates_cmd.add_argument("--fixture", action="append", dest="fixtures")
    live_gates_cmd.add_argument("--product", action="append", dest="products")
    live_gates_cmd.add_argument("--keep-applied", action="store_true")
    live_gates_cmd.add_argument("--keep-going", action="store_true")
    live_gates_cmd.add_argument("--skip-concept-validation", action="store_true")
    live_gates_cmd.add_argument("--no-restart-services", action="store_true")
    live_gates_cmd.add_argument("--skip-verify", action="store_true")

    return parser


def _load_config(path: str | Path) -> ClinicConfigModel:
    return ClinicConfigModel.from_json_file(path)


def _build_applier(repo_root: str | Path, state_dir: str | None) -> BundleApplier:
    store = ChangeControlStateStore(state_dir) if state_dir else None
    return BundleApplier(repo_root, state_store=store)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "inventory":
        inventory = write_runtime_inventory(args.repo_root, args.output_dir)
        print(json.dumps(inventory.to_dict(), indent=2))
        return 0

    if args.command == "validate-config":
        config = _load_config(args.config_path)
        concept_resolver = None
        if args.validate_concepts:
            concept_resolver = CielTerminologyService(args.repo_root)
        report = validate_clinic_config(config, concept_resolver=concept_resolver)
        print(json.dumps({"issues": [asdict(issue) for issue in report.issues], "ok": report.ok()}, indent=2))
        return 0 if report.ok() else 1

    if args.command == "preview":
        config = _load_config(args.config_path)
        concept_resolver = None if args.skip_concept_validation else CielTerminologyService(args.repo_root)
        engine = OnboardingEngine(concept_resolver=concept_resolver)
        print(json.dumps(engine.preview(config), indent=2))
        return 0

    if args.command == "analyze-operations":
        config = _load_config(args.config_path)
        analysis = analyze_operational_policies(config)
        print(json.dumps(analysis.to_dict(), indent=2))
        return 0

    if args.command == "verification-plan":
        config = _load_config(args.config_path)
        plan = build_verification_plan(config)
        print(json.dumps(plan.to_dict(), indent=2))
        return 0

    if args.command == "list-department-packs":
        packs = [asdict(pack) for pack in list_department_packs(args.pack_dir)]
        print(json.dumps(packs, indent=2))
        return 0

    if args.command == "compose-hospital":
        result = compose_from_plan_file(args.plan_path, pack_dir=args.pack_dir)
        result.config.write_json(args.output_path)
        print(
            json.dumps(
                {
                    "planId": result.plan_id,
                    "includedPacks": result.included_packs,
                    "namespaces": result.namespaces,
                    "warnings": result.warnings,
                    "outputPath": str(args.output_path),
                },
                indent=2,
            )
        )
        return 0

    if args.command == "compile-openmrs":
        config = _load_config(args.config_path)
        concept_resolver = None if args.skip_concept_validation else CielTerminologyService(args.repo_root)
        compiler = OpenMRSPackCompiler(
            include_html_forms=args.include_html_forms,
            concept_resolver=concept_resolver,
        )
        result = compiler.compile(config, args.output_dir)
        print(
            json.dumps(
                {
                    "output_dir": result.output_dir,
                    "written_files": result.written_files,
                    "deferred_manifests": result.deferred_manifests,
                    "warnings": result.warnings,
                },
                indent=2,
            )
        )
        return 0

    if args.command == "build-change-bundle":
        config = _load_config(args.config_path)
        concept_resolver = None if args.skip_concept_validation else CielTerminologyService(args.repo_root)
        engine = OnboardingEngine(concept_resolver=concept_resolver)
        bundle = engine.build_change_bundle(config, args.output_dir)
        print(json.dumps(asdict(bundle), indent=2))
        return 0

    if args.command == "build-ciel-store":
        service = CielTerminologyService(
            args.repo_root,
            sqlite_path=args.sqlite_path,
            export_path=args.export_path,
        )
        print(str(service.ensure_store()))
        return 0

    if args.command == "validate-concept":
        service = CielTerminologyService(
            args.repo_root,
            sqlite_path=args.sqlite_path,
            export_path=args.export_path,
        )
        result = service.validate_single_concept(args.concept_ref)
        print(json.dumps(asdict(result), indent=2))
        return 0

    if args.command == "list-changes":
        applier = _build_applier(args.repo_root, args.state_dir)
        records = [record.to_dict() for record in applier.state_store.list_changes()]
        print(json.dumps(records, indent=2))
        return 0

    if args.command == "approve-change":
        applier = _build_applier(args.repo_root, args.state_dir)
        record = applier.approve_bundle(args.change_ref, args.approver, args.note, environment=args.environment)
        print(json.dumps(record.to_dict(), indent=2))
        return 0

    if args.command == "promote-change":
        applier = _build_applier(args.repo_root, args.state_dir)
        record = applier.promote_bundle(
            args.change_ref,
            from_environment=args.from_environment,
            to_environment=args.to_environment,
            promoted_by=args.promoted_by,
            note=args.note,
        )
        print(json.dumps(record.to_dict(), indent=2))
        return 0

    if args.command == "apply-change":
        applier = _build_applier(args.repo_root, args.state_dir)
        outcome = applier.apply_change(
            args.change_ref,
            dry_run=args.dry_run,
            products=args.products,
            restart_services=args.restart_services,
            run_verify=args.run_verify,
            environment=args.environment,
        )
        print(json.dumps(asdict(outcome), indent=2))
        return 0 if not outcome.errors else 1

    if args.command == "rollback-change":
        applier = _build_applier(args.repo_root, args.state_dir)
        outcome = applier.rollback_change(
            args.change_ref,
            run_id=args.run_id,
            products=args.products,
            restart_services=args.restart_services,
            run_verify=args.run_verify,
            environment=args.environment,
        )
        print(json.dumps(asdict(outcome), indent=2))
        return 0 if not outcome.errors else 1

    if args.command == "run-live-gates":
        summary = run_live_acceptance_gates(
            args.repo_root,
            fixture_paths=args.fixtures,
            output_dir=args.output_dir,
            state_dir=args.state_dir,
            approver=args.approver,
            environment=args.environment,
            products=args.products,
            restart_services=not args.no_restart_services,
            run_verify=not args.skip_verify,
            keep_applied=args.keep_applied,
            keep_going=args.keep_going,
            skip_concept_validation=args.skip_concept_validation,
        )
        print(json.dumps(summary, indent=2))
        return 0 if summary["ok"] else 1

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
