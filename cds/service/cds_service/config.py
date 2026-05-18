from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_cds_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cors_origins_from_env() -> frozenset[str]:
    raw = os.getenv("CDS_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
    return frozenset(o.strip() for o in raw.split(",") if o.strip())


@dataclass(frozen=True)
class Settings:
    cds_root: Path
    host: str
    port: int
    openmrs_rest_base_url: str
    openmrs_fhir_base_url: str
    vllm_base_url: str
    vllm_model: str
    vllm_api_key: str
    vllm_launch_command: str | None
    runtime_dir: Path
    request_timeout_seconds: float
    drafts_db_path: Path
    ciel_repo_root: Path
    ciel_sqlite_path: Path
    qdrant_url: str | None
    qdrant_api_key: str | None
    qdrant_collection: str
    kb_guidelines_url: str
    clinical_note_encounter_type_uuid: str | None
    clinical_note_concept_uuid: str | None
    soap_note_form_uuid: str | None
    soap_subjective_concept_uuid: str | None
    soap_objective_concept_uuid: str | None
    soap_assessment_concept_uuid: str | None
    soap_plan_concept_uuid: str | None
    openmrs_service_user: str = "admin"
    openmrs_service_password: str = "Admin123"

    # CORS — explicit allowlist; never mirror arbitrary origins.
    cors_allowed_origins: frozenset[str] = field(default_factory=frozenset)

    # ---- agent tuning constants (override via env for ops experiments) ----
    # Form agent
    form_agent_max_steps: int = 35
    form_agent_target_min_fields: int = 6
    form_agent_max_nudges: int = 2
    form_agent_brainstorm_max_tokens: int = 1400
    form_agent_tool_max_tokens: int = 900
    # Report agent
    report_agent_max_steps: int = 25
    report_agent_brainstorm_max_tokens: int = 1100
    report_agent_tool_max_tokens: int = 900
    # FHIR reader
    fhir_obs_page_size: int = 200
    fhir_obs_max_pages: int = 25
    fhir_demographics_chunk: int = 50
    # Report result caps
    cohort_max_patients: int = 500

    @classmethod
    def from_env(cls) -> "Settings":
        cds_root = Path(os.getenv("CDS_ROOT", _default_cds_root())).resolve()
        runtime_dir = Path(os.getenv("CDS_RUNTIME_DIR", cds_root / "runtime")).resolve()
        ciel_repo_root = Path(os.getenv("CDS_CIEL_REPO_ROOT", "/var/www/ClinicDx_backend")).resolve()
        ciel_sqlite_default = ciel_repo_root / "CIEL" / "ciel_search.sqlite3"
        return cls(
            cds_root=cds_root,
            host=os.getenv("CDS_SERVICE_HOST", "0.0.0.0"),
            port=int(os.getenv("CDS_SERVICE_PORT", "8095")),
            openmrs_rest_base_url=os.getenv("OPENMRS_REST_BASE_URL", "http://localhost:18080/openmrs/ws/rest/v1").rstrip("/"),
            openmrs_fhir_base_url=os.getenv("OPENMRS_FHIR_BASE_URL", "http://localhost:18080/openmrs/ws/fhir2/R4").rstrip("/"),
            vllm_base_url=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1").rstrip("/"),
            vllm_model=os.getenv("VLLM_MODEL", "gemma-4"),
            vllm_api_key=os.getenv("VLLM_API_KEY", "EMPTY"),
            vllm_launch_command=os.getenv("VLLM_LAUNCH_COMMAND") or None,
            runtime_dir=runtime_dir,
            request_timeout_seconds=float(os.getenv("CDS_REQUEST_TIMEOUT_SECONDS", "20")),
            drafts_db_path=Path(os.getenv("CDS_DRAFTS_DB", runtime_dir / "form_drafts.sqlite3")).resolve(),
            ciel_repo_root=ciel_repo_root,
            ciel_sqlite_path=Path(os.getenv("CDS_CIEL_SQLITE", ciel_sqlite_default)).resolve(),
            qdrant_url=os.getenv("QDRANT_URL") or None,
            qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
            qdrant_collection=os.getenv("QDRANT_COLLECTION", "ciel_concepts"),
            kb_guidelines_url=os.getenv("KB_GUIDELINES_URL", "http://localhost:4276"),
            # Fallbacks match the UUID contract in backend/metadata/required-openmrs-metadata.json
            # so the scribe confirm endpoint works out-of-the-box with the reference OpenMRS image.
            # Override via env vars for non-standard deployments.
            clinical_note_encounter_type_uuid=os.getenv("CLINICAL_NOTE_ENCOUNTER_TYPE_UUID") or "d7151f82-c1f3-4152-a605-2f9ea7414a79",
            clinical_note_concept_uuid=os.getenv("CLINICAL_NOTE_CONCEPT_UUID") or "162169AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            soap_note_form_uuid=os.getenv("SOAP_NOTE_FORM_UUID") or "289417aa-31d5-3a06-bae8-a22d870bcf1d",
            soap_subjective_concept_uuid=os.getenv("SOAP_SUBJECTIVE_CONCEPT_UUID") or "81a60a0dbc0c478caa714d372ac533d5",
            soap_objective_concept_uuid=os.getenv("SOAP_OBJECTIVE_CONCEPT_UUID") or "aeec913c-9a36-4153-9a44-12bc255d7f60",
            soap_assessment_concept_uuid=os.getenv("SOAP_ASSESSMENT_CONCEPT_UUID") or "13f82aece2cd4e3bbb950140e6cbffce",
            soap_plan_concept_uuid=os.getenv("SOAP_PLAN_CONCEPT_UUID") or "2ad20b043cf54dd48e698e1c8e231c99",
            openmrs_service_user=os.getenv("OPENMRS_SERVICE_USER", "admin"),
            openmrs_service_password=os.getenv("OPENMRS_SERVICE_PASSWORD", "Admin123"),
            cors_allowed_origins=_cors_origins_from_env(),
            form_agent_max_steps=int(os.getenv("FORM_AGENT_MAX_STEPS", "35")),
            form_agent_target_min_fields=int(os.getenv("FORM_AGENT_TARGET_MIN_FIELDS", "6")),
            form_agent_max_nudges=int(os.getenv("FORM_AGENT_MAX_NUDGES", "2")),
            form_agent_brainstorm_max_tokens=int(os.getenv("FORM_AGENT_BRAINSTORM_MAX_TOKENS", "1400")),
            form_agent_tool_max_tokens=int(os.getenv("FORM_AGENT_TOOL_MAX_TOKENS", "900")),
            report_agent_max_steps=int(os.getenv("REPORT_AGENT_MAX_STEPS", "25")),
            report_agent_brainstorm_max_tokens=int(os.getenv("REPORT_AGENT_BRAINSTORM_MAX_TOKENS", "1100")),
            report_agent_tool_max_tokens=int(os.getenv("REPORT_AGENT_TOOL_MAX_TOKENS", "900")),
            fhir_obs_page_size=int(os.getenv("FHIR_OBS_PAGE_SIZE", "200")),
            fhir_obs_max_pages=int(os.getenv("FHIR_OBS_MAX_PAGES", "25")),
            fhir_demographics_chunk=int(os.getenv("FHIR_DEMOGRAPHICS_CHUNK", "50")),
            cohort_max_patients=int(os.getenv("COHORT_MAX_PATIENTS", "500")),
        )
