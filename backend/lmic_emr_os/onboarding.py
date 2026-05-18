from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .adapters import KeycloakAdapter, OpenELISAdapter, OrthancAdapter
from .config_model import ClinicConfigModel
from .operational_analysis import analyze_operational_policies
from .openmrs_pack import OpenMRSPackCompiler
from .validation import ConceptResolver, ValidationIssue, validate_clinic_config
from .verification_plan import build_verification_plan


@dataclass(slots=True)
class InterviewQuestion:
    id: str
    prompt: str
    field_path: str
    question_type: str
    default: Any = None
    required: bool = True
    choices: list[str] = field(default_factory=list)


@dataclass(slots=True)
class InterviewSection:
    id: str
    title: str
    questions: list[InterviewQuestion]
    complete: bool = False


@dataclass(slots=True)
class ChangeBundle:
    change_id: str
    output_dir: str
    preview_path: str
    config_path: str
    openmrs_pack_dir: str
    apply_order_path: str
    rollback_plan_path: str
    operational_analysis_path: str = ""
    workflow_graph_path: str = ""
    verification_plan_path: str = ""


class OnboardingEngine:
    def __init__(
        self,
        *,
        compiler: OpenMRSPackCompiler | None = None,
        openelis_adapter: OpenELISAdapter | None = None,
        orthanc_adapter: OrthancAdapter | None = None,
        keycloak_adapter: KeycloakAdapter | None = None,
        concept_resolver: ConceptResolver | None = None,
    ) -> None:
        self.concept_resolver = concept_resolver
        self.compiler = compiler or OpenMRSPackCompiler(concept_resolver=concept_resolver)
        self.openelis_adapter = openelis_adapter or OpenELISAdapter()
        self.orthanc_adapter = orthanc_adapter or OrthancAdapter()
        self.keycloak_adapter = keycloak_adapter or KeycloakAdapter()

    def build_interview(self, archetype: str = "general-outpatient") -> list[InterviewSection]:
        lab_default = archetype != "community-pharmacy"
        imaging_default = archetype in {"district-hospital", "general-outpatient", "specialty-clinic"}
        sections = [
            InterviewSection(
                id="facility",
                title="Facility Identity",
                questions=[
                    InterviewQuestion("facility-name", "What is the name of the clinic or facility?", "facilityProfile.name", "text"),
                    InterviewQuestion("facility-code", "What short code should identify this clinic bundle?", "facilityProfile.code", "text"),
                    InterviewQuestion(
                        "facility-type",
                        "What type of facility is this?",
                        "facilityProfile.facilityType",
                        "choice",
                        default=archetype,
                        choices=["general-outpatient", "specialty-clinic", "district-hospital", "primary-health-centre"],
                    ),
                    InterviewQuestion("facility-country", "Which country is the facility in?", "facilityProfile.country", "text"),
                    InterviewQuestion("facility-languages", "What languages should the system support?", "facilityProfile.languages", "list"),
                ],
            ),
            InterviewSection(
                id="registration",
                title="Registration And Flow",
                questions=[
                    InterviewQuestion(
                        "identifier-type",
                        "What patient identifier type should be used?",
                        "registrationModel.identifierStrategy.identifierType",
                        "text",
                        default="OpenMRS ID",
                    ),
                    InterviewQuestion(
                        "walk-in-flow",
                        "Will the clinic accept walk-in patients?",
                        "registrationModel.walkInAllowed",
                        "boolean",
                        default=True,
                    ),
                    InterviewQuestion(
                        "appointment-mode",
                        "How are appointments handled?",
                        "registrationModel.appointmentMode",
                        "choice",
                        default="walk-in",
                        choices=["walk-in", "hybrid", "appointments-only"],
                    ),
                ],
            ),
            InterviewSection(
                id="departments",
                title="Departments And Staffing",
                questions=[
                    InterviewQuestion("departments", "Which departments or service areas should be created?", "locations", "structured-list"),
                    InterviewQuestion("roles", "Which staff roles are needed at launch?", "identityModel.roles", "structured-list"),
                    InterviewQuestion("users", "Which initial users should be created?", "identityModel.users", "structured-list"),
                ],
            ),
            InterviewSection(
                id="clinical",
                title="Forms And Clinical Workflows",
                questions=[
                    InterviewQuestion("encounters", "Which encounter types are needed?", "encounterTypes", "structured-list"),
                    InterviewQuestion("forms", "Which intake, triage, consultation, or specialty forms are needed?", "forms", "structured-list"),
                    InterviewQuestion("programs", "Are there longitudinal clinical programs to configure?", "programs", "structured-list", required=False),
                ],
            ),
            InterviewSection(
                id="routing",
                title="Queues And Routing",
                questions=[
                    InterviewQuestion("queues", "Which queues or service points should patients move through?", "queues", "structured-list"),
                    InterviewQuestion("routing-rules", "What routing rules connect those queues?", "routingRules", "structured-list"),
                ],
            ),
            InterviewSection(
                id="billing",
                title="Billing And Pharmacy",
                questions=[
                    InterviewQuestion("billables", "Which billable services are needed?", "billingModel.billableServices", "structured-list", required=False),
                    InterviewQuestion("payment-modes", "Which payment modes should be enabled?", "billingModel.paymentModes", "structured-list", required=False),
                    InterviewQuestion("cash-points", "Which cash points should be created?", "billingModel.cashPoints", "structured-list", required=False),
                    InterviewQuestion("stock", "Should pharmacy stock structures be configured now?", "stockPharmacyModel", "structured-list", required=False),
                ],
            ),
            InterviewSection(
                id="specialty-engines",
                title="Laboratory And Imaging",
                questions=[
                    InterviewQuestion("lab-enabled", "Will the facility use the lab engine now?", "labModel.enabled", "boolean", default=lab_default),
                    InterviewQuestion(
                        "imaging-enabled",
                        "Will the facility use imaging now?",
                        "imagingModel.enabled",
                        "boolean",
                        default=imaging_default,
                    ),
                ],
            ),
            InterviewSection(
                id="governance",
                title="Approvals And Safety",
                questions=[
                    InterviewQuestion(
                        "approval-required",
                        "Should human approval be required before apply?",
                        "governance.approvalRequired",
                        "boolean",
                        default=True,
                    ),
                    InterviewQuestion(
                        "requestor-roles",
                        "Which roles may request changes from the agent?",
                        "governance.allowedRequestorRoleIds",
                        "list",
                        required=False,
                    ),
                ],
            ),
        ]
        return sections

    def preview(self, config: ClinicConfigModel) -> dict[str, Any]:
        report = validate_clinic_config(config, concept_resolver=self.concept_resolver)
        operational_analysis = analyze_operational_policies(config)
        preview = {
            "facility": {
                "name": config.facility_profile.name,
                "type": config.facility_profile.facility_type,
                "archetype": config.archetype,
                "languages": config.facility_profile.languages,
            },
            "counts": {
                "locations": len(config.locations),
                "forms": len(config.forms),
                "queues": len(config.queues),
                "routingRules": len(config.routing_rules),
                "billableServices": len(config.billing_model.billable_services),
                "paymentModes": len(config.billing_model.payment_modes),
                "stockLocations": len(config.stock_pharmacy_model.stock_locations),
                "users": len(config.identity_model.users),
            },
            "assumptions": self._assumptions(config),
            "issues": [asdict(issue) for issue in report.issues],
            "operationalIssues": [asdict(issue) for issue in operational_analysis.issues],
            "workflow": {
                "startQueueIds": operational_analysis.start_queue_ids,
                "terminalQueueIds": operational_analysis.terminal_queue_ids,
                "isolatedQueueIds": operational_analysis.isolated_queue_ids,
                "unreachableQueueIds": operational_analysis.unreachable_queue_ids,
                "cyclePaths": operational_analysis.cycle_paths,
                "routeSimulationCount": len(operational_analysis.route_simulations),
                "sampleRoutes": [
                    asdict(route)
                    for route in operational_analysis.route_simulations[:5]
                ],
            },
            "verificationSummary": build_verification_plan(config).summary,
            "readyToApply": report.ok() and not any(issue.severity == "error" for issue in operational_analysis.issues),
        }
        return preview

    def build_change_bundle(self, config: ClinicConfigModel, output_dir: str | Path) -> ChangeBundle:
        change_id = f"{config.governance.change_ticket_prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        bundle_dir = Path(output_dir) / change_id
        bundle_dir.mkdir(parents=True, exist_ok=True)

        config_path = bundle_dir / "clinic-config.json"
        config.write_json(config_path)

        preview_path = bundle_dir / "preview.json"
        preview_path.write_text(json.dumps(self.preview(config), indent=2) + "\n", encoding="utf-8")

        openmrs_dir = bundle_dir / "openmrs"
        self.compiler.compile(config, openmrs_dir)

        operational_analysis = analyze_operational_policies(config)
        operational_analysis_path = bundle_dir / "operational-analysis.json"
        operational_analysis_path.write_text(
            json.dumps(operational_analysis.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        workflow_graph_path = bundle_dir / "workflow-graph.mmd"
        workflow_graph_path.write_text(operational_analysis.mermaid, encoding="utf-8")
        verification_plan_path = bundle_dir / "verification-plan.json"
        verification_plan_path.write_text(
            json.dumps(build_verification_plan(config).to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

        plans_dir = bundle_dir / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        self.openelis_adapter.build_plan(config).write_json(plans_dir / "openelis-plan.json")
        self.orthanc_adapter.build_plan(config).write_json(plans_dir / "orthanc-plan.json")
        self.keycloak_adapter.build_plan(config).write_json(plans_dir / "keycloak-plan.json")

        artifact_manifest = {
            "changeId": change_id,
            "artifacts": {
                "artifactManifest": str(bundle_dir / "artifact-manifest.json"),
                "clinicConfig": str(config_path),
                "preview": str(preview_path),
                "operationalAnalysis": str(operational_analysis_path),
                "workflowGraph": str(workflow_graph_path),
                "verificationPlan": str(verification_plan_path),
                "openmrsPack": str(openmrs_dir),
                "plansDir": str(plans_dir),
                "applyOrder": str(bundle_dir / "apply-order.json"),
                "rollbackPlan": str(bundle_dir / "rollback.json"),
            },
        }
        artifact_manifest_path = bundle_dir / "artifact-manifest.json"
        artifact_manifest_path.write_text(json.dumps(artifact_manifest, indent=2) + "\n", encoding="utf-8")

        apply_order = {
            "changeId": change_id,
            "approvalRequired": config.governance.approval_required,
            "steps": [
                {"order": 1, "action": "review-preview", "artifact": str(preview_path)},
                {"order": 2, "action": "load-openmrs-pack", "artifact": str(openmrs_dir)},
                {"order": 3, "action": "apply-keycloak-plan", "artifact": str(plans_dir / "keycloak-plan.json")},
                {"order": 4, "action": "apply-openelis-plan", "artifact": str(plans_dir / "openelis-plan.json")},
                {"order": 5, "action": "apply-orthanc-plan", "artifact": str(plans_dir / "orthanc-plan.json")},
                {"order": 6, "action": "run-post-apply-smoke-tests", "artifact": "scripts/verify-backend.sh"},
            ],
        }
        apply_order_path = bundle_dir / "apply-order.json"
        apply_order_path.write_text(json.dumps(apply_order, indent=2) + "\n", encoding="utf-8")

        rollback_plan = {
            "changeId": change_id,
            "strategy": "Restore the previous known-good config bundle and re-run post-apply verification.",
            "artifactsToRevert": [
                str(openmrs_dir),
                str(plans_dir / "keycloak-plan.json"),
                str(plans_dir / "openelis-plan.json"),
                str(plans_dir / "orthanc-plan.json"),
            ],
            "notes": [
                "The runtime agent should never mutate product internals directly; rollback should re-apply the last approved bundle.",
                "Preserve preview.json and clinic-config.json for audit.",
            ],
        }
        rollback_plan_path = bundle_dir / "rollback.json"
        rollback_plan_path.write_text(json.dumps(rollback_plan, indent=2) + "\n", encoding="utf-8")

        return ChangeBundle(
            change_id=change_id,
            output_dir=str(bundle_dir),
            preview_path=str(preview_path),
            config_path=str(config_path),
            openmrs_pack_dir=str(openmrs_dir),
            apply_order_path=str(apply_order_path),
            rollback_plan_path=str(rollback_plan_path),
            operational_analysis_path=str(operational_analysis_path),
            workflow_graph_path=str(workflow_graph_path),
            verification_plan_path=str(verification_plan_path),
        )

    def _assumptions(self, config: ClinicConfigModel) -> list[str]:
        assumptions: list[str] = []
        if not config.lab_model.enabled:
            assumptions.append("OpenELIS is disabled for this bundle and can be enabled later through the same control plane.")
        if not config.imaging_model.enabled:
            assumptions.append("Orthanc is disabled for this bundle and can be enabled later without changing source code.")
        if not config.billing_model.billable_services:
            assumptions.append("Billing services are omitted from this bundle and the cashier flow will remain minimal.")
        if not config.programs:
            assumptions.append("No longitudinal program workflows are configured in this initial bundle.")
        return assumptions


def render_validation_issues(issues: list[ValidationIssue]) -> list[dict[str, str]]:
    return [asdict(issue) for issue in issues]
