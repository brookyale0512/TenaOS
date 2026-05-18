from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


DEFAULT_OPENMRS_CACHE = Path(
    "backups/default-port-validation/openmrs-data/.openmrs-lib-cache"
)


@dataclass(slots=True)
class CapabilitySurface:
    product: str
    domain: str
    status: str
    supported_surfaces: list[str]
    supported_operations: list[str]
    evidence: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RuntimeInventory:
    repo_root: str
    openmrs_modules: list[str]
    capability_matrix: list[CapabilitySurface]

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "openmrs_modules": self.openmrs_modules,
            "capability_matrix": [asdict(item) for item in self.capability_matrix],
        }

    def write_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    def write_markdown(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        lines.append("# Runtime Capability Matrix")
        lines.append("")
        lines.append(
            "This matrix is generated from the repository runtime artifacts and start scripts so the "
            "agent control plane targets supported configuration surfaces instead of source-code patches."
        )
        lines.append("")
        lines.append("## OpenMRS Module Inventory")
        lines.append("")
        for module in self.openmrs_modules:
            lines.append(f"- `{module}`")
        lines.append("")
        lines.append("## Supported Surfaces")
        lines.append("")
        lines.append("| Product | Domain | Status | Supported surfaces | Supported operations | Evidence | Limitations |")
        lines.append("|---|---|---|---|---|---|---|")
        for surface in self.capability_matrix:
            supported_surfaces = "<br>".join(surface.supported_surfaces)
            operations = "<br>".join(surface.supported_operations)
            evidence = "<br>".join(surface.evidence)
            limitations = "<br>".join(surface.limitations) or "-"
            lines.append(
                f"| {surface.product} | {surface.domain} | {surface.status} | "
                f"{supported_surfaces} | {operations} | {evidence} | {limitations} |"
            )
        lines.append("")
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _path_if_exists(repo_root: Path, relative_path: str) -> str | None:
    path = repo_root / relative_path
    return relative_path if path.exists() else None


def discover_openmrs_modules(repo_root: Path, cache_path: Path | None = None) -> list[str]:
    base_path = cache_path or (repo_root / DEFAULT_OPENMRS_CACHE)
    if not base_path.exists():
        return []
    modules = [path.name for path in base_path.iterdir() if path.is_dir()]
    return sorted(modules)


def _openmrs_surfaces(repo_root: Path, modules: list[str]) -> list[CapabilitySurface]:
    module_set = set(modules)
    surfaces: list[CapabilitySurface] = []

    def present(module: str) -> bool:
        return module in module_set

    if present("initializer"):
        surfaces.append(
            CapabilitySurface(
                product="OpenMRS",
                domain="metadata-packs",
                status="supported",
                supported_surfaces=[
                    "Initializer configuration directory",
                    "CSV/XML/JSON domains in app data",
                ],
                supported_operations=[
                    "load locations",
                    "load encounter types",
                    "load forms",
                    "load id generators",
                    "load programs and workflows",
                    "load billing CSV domains when billing module is present",
                ],
                evidence=[
                    "backups/default-port-validation/openmrs-data/.openmrs-lib-cache/initializer/moduleApplicationContext.xml",
                    "scripts/run-openmrs.sh",
                ],
                limitations=[
                    "not every module exposes an Initializer domain",
                    "custom prices and deep workflow logic still require bounded adapters",
                ],
            )
        )

    if present("o3forms"):
        surfaces.append(
            CapabilitySurface(
                product="OpenMRS",
                domain="forms",
                status="supported",
                supported_surfaces=["O3/AMPATH JSON forms", "HTML Form Entry XML"],
                supported_operations=["create intake forms", "create consultation forms", "seed specialty forms"],
                evidence=[
                    "backups/default-port-validation/openmrs-data/.openmrs-lib-cache/o3forms/webModuleApplicationContext.xml"
                ],
                limitations=["requires concept validation before publishing"],
            )
        )

    if present("queue"):
        surfaces.append(
            CapabilitySurface(
                product="OpenMRS",
                domain="operational-routing",
                status="supported",
                supported_surfaces=["Queue module REST/API surfaces", "queue configuration tables"],
                supported_operations=[
                    "define queues and rooms",
                    "assign providers",
                    "apply status and priority concept sets",
                    "model patient movement between service points",
                ],
                evidence=[
                    "backups/default-port-validation/openmrs-data/.openmrs-lib-cache/queue/liquibase.xml"
                ],
                limitations=["not a full BPMN or rules engine"],
            )
        )

    if present("billing"):
        surfaces.append(
            CapabilitySurface(
                product="OpenMRS",
                domain="billing",
                status="supported-with-constraints",
                supported_surfaces=["billing module repositories", "Initializer billableservices/paymentmodes/cashpoints"],
                supported_operations=[
                    "define billable services",
                    "define payment modes",
                    "define cash points",
                    "set billing global properties",
                ],
                evidence=[
                    "backups/default-port-validation/openmrs-data/.openmrs-lib-cache/billing/moduleApplicationContext.xml",
                    "backups/default-port-validation/openmrs-data/.openmrs-lib-cache/billing/liquibase.xml",
                ],
                limitations=[
                    "pricing schedules need a bounded adapter or admin-surface automation",
                    "clinic-specific exemptions need explicit policy validation",
                ],
            )
        )

    if present("stockmanagement"):
        surfaces.append(
            CapabilitySurface(
                product="OpenMRS",
                domain="stock-pharmacy",
                status="supported-with-constraints",
                supported_surfaces=["stockmanagement service layer", "stock metadata tables"],
                supported_operations=[
                    "define dispensing structures",
                    "scope stock operations by location",
                    "persist operational stock rules",
                ],
                evidence=[
                    "backups/default-port-validation/openmrs-data/.openmrs-lib-cache/stockmanagement/moduleApplicationContext.xml",
                    "backups/default-port-validation/openmrs-data/.openmrs-lib-cache/stockmanagement/liquibase.xml",
                ],
                limitations=["initializer coverage is weaker than core metadata domains"],
            )
        )

    if present("idgen"):
        surfaces.append(
            CapabilitySurface(
                product="OpenMRS",
                domain="patient-identifiers",
                status="supported",
                supported_surfaces=["Initializer idgen domain", "idgen runtime properties"],
                supported_operations=["configure sequential generators", "configure pools", "enable auto-generation"],
                evidence=["scripts/run-openmrs.sh"],
                limitations=[],
            )
        )

    if present("fhir2"):
        surfaces.append(
            CapabilitySurface(
                product="OpenMRS",
                domain="interoperability",
                status="supported",
                supported_surfaces=["FHIR2 module REST endpoints"],
                supported_operations=["patient/search APIs", "order/result integration", "downstream backend verification"],
                evidence=["backups/default-port-validation/openmrs-data/.openmrs-lib-cache/fhir2"],
                limitations=[],
            )
        )

    return surfaces


def _openelis_surfaces(repo_root: Path) -> list[CapabilitySurface]:
    evidence = [
        path
        for path in (
            _path_if_exists(repo_root, "scripts/render-runtime-config.py"),
            _path_if_exists(repo_root, "scripts/run-openelis-webapp.sh"),
            _path_if_exists(repo_root, "scripts/run-openelis-fhir.sh"),
        )
        if path
    ]
    return [
        CapabilitySurface(
            product="OpenELIS",
            domain="runtime-config",
            status="supported-with-constraints",
            supported_surfaces=["generated properties files", "boot scripts", "FHIR integration settings"],
            supported_operations=[
                "site profile configuration",
                "authentication integration",
                "FHIR endpoint configuration",
                "service-user integration setup",
            ],
            evidence=evidence,
            limitations=[
                "not the primary metadata substrate for clinic-to-clinic customization",
                "deep LIS workflow customization should stay within supported OpenELIS admin surfaces",
            ],
        )
    ]


def _orthanc_surfaces(repo_root: Path) -> list[CapabilitySurface]:
    evidence = [
        path
        for path in (
            _path_if_exists(repo_root, "scripts/render-runtime-config.py"),
            _path_if_exists(repo_root, "scripts/run-orthanc-auth.sh"),
            _path_if_exists(repo_root, "configs/orthanc-auth/permissions.json"),
        )
        if path
    ]
    return [
        CapabilitySurface(
            product="Orthanc",
            domain="authorization-and-policy",
            status="supported-with-constraints",
            supported_surfaces=["generated orthanc.json", "orthanc-auth-service env/config", "permissions.json role map"],
            supported_operations=[
                "enable imaging service",
                "bind Keycloak-backed roles to Orthanc permissions",
                "set share-policy defaults",
            ],
            evidence=evidence,
            limitations=["specialized PACS behavior should remain policy-driven, not source-code driven"],
        )
    ]


def _keycloak_surfaces(repo_root: Path) -> list[CapabilitySurface]:
    evidence = [
        path
        for path in (
            _path_if_exists(repo_root, "scripts/render-runtime-config.py"),
            _path_if_exists(repo_root, "scripts/run-keycloak.sh"),
            _path_if_exists(repo_root, "docs/keycloak-e2e-audit.md"),
        )
        if path
    ]
    return [
        CapabilitySurface(
            product="Keycloak",
            domain="identity-and-role-control-plane",
            status="supported-with-constraints",
            supported_surfaces=["rendered realm JSON", "OIDC clients", "service accounts", "admin API"],
            supported_operations=[
                "provision bounded clinic users",
                "map clinic roles to product-native roles",
                "maintain SSO client configuration",
            ],
            evidence=evidence,
            limitations=[
                "runtime clinic agent should not hold broad realm-management privileges",
                "sensitive admin actions should remain in a privileged control plane",
            ],
        )
    ]


def build_runtime_inventory(repo_root: str | Path) -> RuntimeInventory:
    root = Path(repo_root)
    modules = discover_openmrs_modules(root)
    capability_matrix: list[CapabilitySurface] = []
    capability_matrix.extend(_openmrs_surfaces(root, modules))
    capability_matrix.extend(_openelis_surfaces(root))
    capability_matrix.extend(_orthanc_surfaces(root))
    capability_matrix.extend(_keycloak_surfaces(root))
    return RuntimeInventory(
        repo_root=str(root),
        openmrs_modules=modules,
        capability_matrix=capability_matrix,
    )


def write_runtime_inventory(repo_root: str | Path, output_dir: str | Path) -> RuntimeInventory:
    inventory = build_runtime_inventory(repo_root)
    target_dir = Path(output_dir)
    inventory.write_json(target_dir / "runtime-capability-matrix.json")
    inventory.write_markdown(target_dir / "runtime-capability-matrix.md")
    return inventory
