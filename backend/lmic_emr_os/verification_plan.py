from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .config_model import ClinicConfigModel
from .operational_analysis import analyze_operational_policies


@dataclass(slots=True)
class VerificationCheck:
    check_id: str
    product: str
    title: str
    method: str
    target: str
    expected: str
    required: bool = True
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerificationCheck":
        return cls(
            check_id=str(payload["check_id"]),
            product=str(payload["product"]),
            title=str(payload["title"]),
            method=str(payload["method"]),
            target=str(payload["target"]),
            expected=str(payload["expected"]),
            required=bool(payload.get("required", True)),
            notes=[str(value) for value in payload.get("notes", [])],
        )


@dataclass(slots=True)
class VerificationPlan:
    products: list[str]
    summary: dict[str, Any]
    checks: list[VerificationCheck]
    recommended_commands: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "products": self.products,
            "summary": self.summary,
            "checks": [asdict(check) for check in self.checks],
            "recommendedCommands": self.recommended_commands,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerificationPlan":
        return cls(
            products=[str(value) for value in payload.get("products", [])],
            summary=dict(payload.get("summary", {})),
            checks=[VerificationCheck.from_dict(item) for item in payload.get("checks", [])],
            recommended_commands=[str(value) for value in payload.get("recommendedCommands", [])],
        )


def build_verification_plan(config: ClinicConfigModel) -> VerificationPlan:
    analysis = analyze_operational_policies(config)
    checks: list[VerificationCheck] = []

    checks.append(
        VerificationCheck(
            check_id="keycloak.discovery",
            product="Keycloak",
            title="Keycloak discovery document",
            method="http-get",
            target="${KEYCLOAK_PUBLIC_URL}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration",
            expected="HTTP 200",
        )
    )

    checks.append(
        VerificationCheck(
            check_id="openmrs.rest.session",
            product="OpenMRS",
            title="OpenMRS REST session endpoint",
            method="http-get",
            target="${OPENMRS_PUBLIC_URL}/ws/rest/v1/session",
            expected="HTTP 200 or 302 when OIDC login is enabled",
        )
    )
    checks.append(
        VerificationCheck(
            check_id="openmrs.fhir.metadata",
            product="OpenMRS",
            title="OpenMRS FHIR metadata endpoint",
            method="http-get",
            target="${OPENMRS_PUBLIC_URL}/ws/fhir2/R4/metadata",
            expected="HTTP 200 or 302 when OIDC login is enabled",
        )
    )
    checks.append(
        VerificationCheck(
            check_id="openmrs.config.bundle",
            product="OpenMRS",
            title="Generated OpenMRS configuration bundle staged in runtime data directory",
            method="file-check",
            target="/opt/clinicDx/data/emr-os/openmrs-managed-config",
            expected="Generated Initializer domains are present after apply",
            notes=[
                f"Expected queue count: {len(config.queues)}",
                f"Expected form count: {len(config.forms)}",
            ],
        )
    )
    checks.append(
        VerificationCheck(
            check_id="openmrs.runtime.bundle-load",
            product="OpenMRS",
            title="OpenMRS exposes configured metadata after restart",
            method="openmrs-runtime-check",
            target="bundle-runtime-state",
            expected="Locations, encounter types, billing metadata, queue metadata, pricing, and stock rules from the clinic bundle are visible through supported OpenMRS runtime surfaces after apply",
            notes=[
                f"Expected locations: {len(config.locations)}",
                f"Expected encounter types: {len(config.encounter_types)}",
                f"Expected queues: {len(config.queues)}",
                f"Expected queue rooms: {len(config.queue_rooms)}",
                f"Expected pricing rules: {len(config.billing_model.pricing_rules)}",
                f"Expected stock rules: {len(config.stock_pharmacy_model.rules)}",
            ],
        )
    )

    if config.routing_rules:
        checks.append(
            VerificationCheck(
                check_id="openmrs.routing.policy.contract",
                product="OpenMRS",
                title="OpenMRS routing policy contract is installed explicitly",
                method="routing-policy-check",
                target="/opt/clinicDx/data/emr-os/openmrs-extensions/queue-routing.json",
                expected="Adapter-owned routing-policy contract is installed with the expected rule count",
                notes=[
                    "The Queue module still lacks a native route-policy persistence surface.",
                    "This check proves the live adapter-owned contract is present and synchronized.",
                ],
            )
        )

    for role in config.identity_model.roles:
        for role_name in role.keycloak_roles or [role.id]:
            checks.append(
                VerificationCheck(
                    check_id=f"keycloak.role.{role_name}",
                    product="Keycloak",
                    title=f"Realm role exists for clinic role {role_name}",
                    method="admin-api-get",
                    target=f"/admin/realms/${{KEYCLOAK_REALM}}/roles/{role_name}",
                    expected="Role representation exists",
                )
            )

    for user in config.identity_model.users:
        checks.append(
            VerificationCheck(
                check_id=f"keycloak.user.{user.username}",
                product="Keycloak",
                title=f"Provisioned user exists: {user.username}",
                method="admin-api-query",
                target=f"/admin/realms/${{KEYCLOAK_REALM}}/users?username={user.username}&exact=true",
                expected="User representation exists",
            )
        )

    if config.lab_model.enabled:
        checks.append(
            VerificationCheck(
                check_id="openelis.login",
                product="OpenELIS",
                title="OpenELIS login page",
                method="http-get",
                target="${OPENELIS_PUBLIC_URL}/LoginPage",
                expected="HTTP 200 or 302",
            )
        )
        checks.append(
            VerificationCheck(
                check_id="openelis.fhir.metadata",
                product="OpenELIS",
                title="OpenELIS FHIR metadata endpoint",
                method="http-get",
                target="http://localhost:${OPENELIS_FHIR_PORT}/fhir/metadata",
                expected="HTTP 200",
            )
        )
        checks.append(
            VerificationCheck(
                check_id="openelis.facility.id",
                product="OpenELIS",
                title="OpenELIS facility id matches clinic bundle",
                method="file-check",
                target="/var/lib/openelis-global/properties/common.properties",
                expected=f"org.openelisglobal.facility.id={config.facility_profile.name}",
            )
        )
        checks.append(
            VerificationCheck(
                check_id="openelis.auth.properties",
                product="OpenELIS",
                title="OpenELIS auth properties reflect the generated OAuth posture",
                method="file-check",
                target="/run/secrets/extra.properties",
                expected=f"org.itech.login.oauth={'true' if config.lab_model.enabled else 'false'}",
            )
        )
        checks.append(
            VerificationCheck(
                check_id="openelis.workflow.profile",
                product="OpenELIS",
                title="OpenELIS workflow profile artifact is staged in the runtime configuration directory",
                method="file-check",
                target="/var/lib/openelis-global/configuration/backend/lmic-emr-os-plan.json",
                expected=config.lab_model.result_delivery_mode if config.lab_model.enabled else "disabled",
            )
        )

    if config.imaging_model.enabled:
        checks.append(
            VerificationCheck(
                check_id="orthanc.permissions.file",
                product="Orthanc",
                title="Orthanc permissions file contains generated role map",
                method="file-check",
                target="/opt/clinicDx/configs/orthanc-auth/permissions.json",
                expected="Configured imaging roles are present",
            )
        )
        checks.append(
            VerificationCheck(
                check_id="orthanc.dicomweb.studies",
                product="Orthanc",
                title="Orthanc DICOMweb studies endpoint",
                method="http-get-auth",
                target="${ORTHANC_PUBLIC_URL}/dicom-web/studies",
                expected="HTTP 200 for allowed roles; denied for restricted roles",
                notes=[
                    "Use bounded admin and clinical test principals after apply.",
                ],
            )
        )
        checks.append(
            VerificationCheck(
                check_id="orthanc.access.profiles",
                product="Orthanc",
                title="Orthanc access profile artifact matches generated role profiles",
                method="file-check",
                target="/opt/clinicDx/configs/orthanc-auth/access-profiles.json",
                expected="profileId",
            )
        )
        checks.append(
            VerificationCheck(
                check_id="orthanc.auth.overlay",
                product="Orthanc",
                title="Orthanc authorization overlay remains enabled in the live runtime config",
                method="file-check",
                target="/opt/clinicDx/configs/orthanc/orthanc.json",
                expected="Authorization",
            )
        )

    if config.queues:
        checks.append(
            VerificationCheck(
                check_id="workflow.analysis.no-errors",
                product="Workflow",
                title="Operational workflow analysis has no errors",
                method="analysis-check",
                target="operational-analysis.json",
                expected="No severity=error issues",
                notes=[
                    f"Start queues: {', '.join(analysis.start_queue_ids) or 'none'}",
                    f"Terminal queues: {', '.join(analysis.terminal_queue_ids) or 'none'}",
                    f"Route simulations: {len(analysis.route_simulations)}",
                ],
            )
        )

    if config.billing_model.billable_services:
        checks.append(
            VerificationCheck(
                check_id="billing.metadata.seeded",
                product="OpenMRS",
                title="Billing metadata seeded",
                method="metadata-check",
                target="billableservices/paymentmodes/cashpoints",
                expected="Configured services, payment modes, and cash points exist after apply",
            )
        )

    if config.stock_pharmacy_model.stock_locations:
        checks.append(
            VerificationCheck(
                check_id="stock.policy.manifest",
                product="OpenMRS",
                title="Stock/pharmacy operational manifest present",
                method="artifact-check",
                target="openmrs/extensions/stock-pharmacy.json",
                expected="Operational stock structures match clinic bundle",
            )
        )

    products = sorted({check.product for check in checks})
    summary = {
        "facilityName": config.facility_profile.name,
        "archetype": config.archetype,
        "requiredChecks": len([check for check in checks if check.required]),
        "optionalChecks": len([check for check in checks if not check.required]),
        "routeSimulationCount": len(analysis.route_simulations),
    }
    recommended_commands = [
        "./scripts/verify-backend.sh",
        "python -m lmic_emr_os.cli analyze-operations <config-path>",
    ]
    return VerificationPlan(
        products=products,
        summary=summary,
        checks=checks,
        recommended_commands=recommended_commands,
    )
