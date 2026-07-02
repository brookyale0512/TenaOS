"""TenaAgent runtime configuration.

All environment variables follow one of two namespaces:

- ``TENAOS_*``      shared with the rest of TenaOS (LLM endpoint, KB URLs, CIEL paths)
- ``TENA_AGENT_*``  TenaAgent-internal service settings (port, CORS, tuning constants)

Legacy CDS_*, older LLM-provider aliases, and KB_GUIDELINES_URL aliases have
been removed in the public release. See CHANGELOG for the rename map if you
are upgrading.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_tena_agent_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_tenaos_root() -> Path:
    return _default_tena_agent_root().parent


def _cors_origins_from_env() -> frozenset[str]:
    raw = os.getenv("TENA_AGENT_CORS_ORIGINS") or "http://localhost:3000,http://localhost:5173"
    return frozenset(o.strip() for o in raw.split(",") if o.strip())


@dataclass(frozen=True)
class Settings:
    agent_root: Path
    host: str
    port: int
    openmrs_rest_base_url: str
    openmrs_fhir_base_url: str
    # TenaOS-LLM (llama.cpp serving Gemma 4 E4B BF16 GGUF)
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    # TenaAgent core
    runtime_dir: Path
    request_timeout_seconds: float
    # SQLite stores
    drafts_db_path: Path
    cds_traces_db_path: Path
    material_traces_db_path: Path
    scribe_traces_db_path: Path
    # CIEL terminology store (raw SQLite + FTS5 — TenaOS-CIEL service)
    ciel_repo_root: Path
    ciel_sqlite_path: Path
    # Knowledge base services (Qdrant-backed semantic search, EmbedGemma)
    kb_guidelines_url: str
    kb_ciel_url: str
    # OpenMRS metadata UUIDs (override per deployment if you use non-reference seeds)
    clinical_note_encounter_type_uuid: str | None
    clinical_note_concept_uuid: str | None
    soap_note_form_uuid: str | None
    soap_subjective_concept_uuid: str | None
    soap_objective_concept_uuid: str | None
    soap_assessment_concept_uuid: str | None
    soap_plan_concept_uuid: str | None
    openmrs_service_user: str = "admin"
    # No default password. Caller must set OPENMRS_SERVICE_PASSWORD in env;
    # an empty value means OpenMRS auth will fail loudly rather than silently
    # using a known weak credential.
    openmrs_service_password: str = ""

    # OpenMRS drug-order UUIDs — reference application defaults. Override
    # per deployment if your OpenMRS distribution uses non-standard concept
    # dictionary seeds. Each value is a CIEL/OpenMRS concept UUID:
    #   dose_units            = "mg"           (CIEL 161553)
    #   route                 = "Oral"         (CIEL 160240)
    #   quantity_units        = "Tablet"       (CIEL 1513)
    #   frequency_once_daily  = OpenMRS ref-app default order frequency
    #   default_orderer       = OpenMRS ref-app demo clinician user
    #   care_setting_outpatient = OpenMRS Outpatient care setting
    #   drug_order_type       = OpenMRS Drug Order type
    drug_order_dose_units_uuid: str = "161553AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    drug_order_route_uuid: str = "160240AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    drug_order_quantity_units_uuid: str = "1513AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    drug_order_frequency_uuid: str = "136ebdb7-e989-47cf-8ec2-4e8b2ffe0ab3"
    drug_order_orderer_uuid: str = "09fcfd7e-36e7-455f-8fc7-6b47d4fe8c5d"
    drug_order_care_setting_uuid: str = "6f0c9a92-6f24-11e3-af88-005056821db0"
    drug_order_type_uuid: str = "131168f4-15f5-102d-96e4-000c29c2a5d7"

    cors_allowed_origins: frozenset[str] = field(default_factory=frozenset)

    # ---- agent tuning constants (override via env for ops experiments) ----
    form_agent_max_steps: int = 35
    form_agent_target_min_fields: int = 6
    form_agent_max_nudges: int = 2
    form_agent_recovery_min_searches: int = 6
    form_agent_brainstorm_max_tokens: int = 1400
    form_agent_tool_max_tokens: int = 900
    form_agent_subject_assessment: bool = False
    form_agent_subject_max_searches: int = 5
    form_agent_subject_max_tokens: int = 900
    # When True, CIEL concept discovery queries the kb-ciel semantic service
    # first (plain-language SapBERT search) and hydrates exact codes from the
    # local SQLite store, falling back to SQLite FTS5 if kb-ciel is unreachable.
    ciel_semantic_search: bool = True
    # v2 grounded pipeline (research -> CIEL resolution -> repair). Now the
    # default path: subject-matter research over WHO/MSF, then semantic CIEL
    # discovery + exact SQLite resolution. Set FORM_AGENT_PIPELINE_V2=0 to fall
    # back to the legacy runner.
    form_agent_pipeline_v2: bool = True
    form_agent_research_max_searches: int = 5
    form_agent_research_max_tokens: int = 1100
    # Resolution tool turns get a larger budget than the legacy 900 so a single
    # multi-field update_form_draft call is not silently truncated.
    form_agent_resolve_max_tokens: int = 1300
    report_agent_max_steps: int = 25
    report_agent_brainstorm_max_tokens: int = 1100
    report_agent_tool_max_tokens: int = 900
    fhir_obs_page_size: int = 200
    fhir_obs_max_pages: int = 25
    fhir_demographics_chunk: int = 50
    cohort_max_patients: int = 500
    # Public all-in-one deployments should require the browser's OpenMRS
    # session before serving expensive agent endpoints. Kept opt-in for
    # backend-only local development where callers may hit TenaAgent directly.
    require_openmrs_session: bool = False
    # When True, agent_prompts.load_prompt() and tool_descriptions_registry()
    # prefer optimized/ overlay files when present. Production stays False
    # unless explicitly opted-in.
    use_optimized_prompts: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        agent_root = Path(os.getenv("TENA_AGENT_ROOT") or _default_tena_agent_root()).resolve()
        runtime_dir = Path(os.getenv("TENA_AGENT_RUNTIME_DIR") or agent_root / "runtime").resolve()
        ciel_repo_root = Path(
            os.getenv("TENAOS_CIEL_ROOT") or _default_tenaos_root() / "TenaOS-CIEL"
        ).resolve()
        ciel_sqlite_default = ciel_repo_root / "ciel_search.sqlite3"
        return cls(
            agent_root=agent_root,
            host=os.getenv("TENA_AGENT_SERVICE_HOST", "0.0.0.0"),
            port=int(os.getenv("TENA_AGENT_SERVICE_PORT", "8095")),
            openmrs_rest_base_url=os.getenv(
                "OPENMRS_REST_BASE_URL", "http://localhost:18080/openmrs/ws/rest/v1"
            ).rstrip("/"),
            openmrs_fhir_base_url=os.getenv(
                "OPENMRS_FHIR_BASE_URL", "http://localhost:18080/openmrs/ws/fhir2/R4"
            ).rstrip("/"),
            llm_base_url=os.getenv("TENAOS_LLM_URL", "http://localhost:8001/v1").rstrip("/"),
            llm_model=os.getenv("TENAOS_LLM_MODEL", "gemma-4"),
            llm_api_key=os.getenv("TENAOS_LLM_API_KEY", "EMPTY"),
            runtime_dir=runtime_dir,
            request_timeout_seconds=float(os.getenv("TENA_AGENT_REQUEST_TIMEOUT_SECONDS", "20")),
            drafts_db_path=Path(
                os.getenv("TENA_AGENT_DRAFTS_DB") or runtime_dir / "form_drafts.sqlite3"
            ).resolve(),
            cds_traces_db_path=Path(
                os.getenv("TENA_AGENT_CDS_TRACES_DB") or runtime_dir / "cds_traces.sqlite3"
            ).resolve(),
            material_traces_db_path=Path(
                os.getenv("TENA_AGENT_MATERIAL_TRACES_DB") or runtime_dir / "material_traces.sqlite3"
            ).resolve(),
            scribe_traces_db_path=Path(
                os.getenv("TENA_AGENT_SCRIBE_TRACES_DB") or runtime_dir / "scribe_traces.sqlite3"
            ).resolve(),
            ciel_repo_root=ciel_repo_root,
            ciel_sqlite_path=Path(
                os.getenv("TENAOS_CIEL_SQLITE") or ciel_sqlite_default
            ).resolve(),
            kb_guidelines_url=os.getenv("TENAOS_KB_GUIDELINES_URL", "http://localhost:4276"),
            kb_ciel_url=os.getenv("TENAOS_KB_CIEL_URL", "http://localhost:4277"),
            ciel_semantic_search=(
                os.getenv("TENAOS_CIEL_SEMANTIC_SEARCH", "1").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
            # OpenMRS metadata UUIDs match the contract in
            # TenaOS-Backend/metadata/required-openmrs-metadata.json so the
            # scribe confirm endpoint works out-of-the-box with the reference
            # OpenMRS image. Override via env vars for non-standard deployments.
            clinical_note_encounter_type_uuid=os.getenv("CLINICAL_NOTE_ENCOUNTER_TYPE_UUID")
            or "d7151f82-c1f3-4152-a605-2f9ea7414a79",
            clinical_note_concept_uuid=os.getenv("CLINICAL_NOTE_CONCEPT_UUID")
            or "162169AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            soap_note_form_uuid=os.getenv("SOAP_NOTE_FORM_UUID")
            or "289417aa-31d5-3a06-bae8-a22d870bcf1d",
            soap_subjective_concept_uuid=os.getenv("SOAP_SUBJECTIVE_CONCEPT_UUID")
            or "81a60a0dbc0c478caa714d372ac533d5",
            soap_objective_concept_uuid=os.getenv("SOAP_OBJECTIVE_CONCEPT_UUID")
            or "aeec913c-9a36-4153-9a44-12bc255d7f60",
            soap_assessment_concept_uuid=os.getenv("SOAP_ASSESSMENT_CONCEPT_UUID")
            or "13f82aece2cd4e3bbb950140e6cbffce",
            soap_plan_concept_uuid=os.getenv("SOAP_PLAN_CONCEPT_UUID")
            or "2ad20b043cf54dd48e698e1c8e231c99",
            openmrs_service_user=os.getenv("OPENMRS_SERVICE_USER", "admin"),
            openmrs_service_password=os.getenv("OPENMRS_SERVICE_PASSWORD", ""),
            drug_order_dose_units_uuid=os.getenv(
                "DRUG_ORDER_DOSE_UNITS_UUID", "161553AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            ),
            drug_order_route_uuid=os.getenv(
                "DRUG_ORDER_ROUTE_UUID", "160240AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            ),
            drug_order_quantity_units_uuid=os.getenv(
                "DRUG_ORDER_QUANTITY_UNITS_UUID", "1513AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            ),
            drug_order_frequency_uuid=os.getenv(
                "DRUG_ORDER_FREQUENCY_UUID", "136ebdb7-e989-47cf-8ec2-4e8b2ffe0ab3"
            ),
            drug_order_orderer_uuid=os.getenv(
                "DRUG_ORDER_ORDERER_UUID", "09fcfd7e-36e7-455f-8fc7-6b47d4fe8c5d"
            ),
            drug_order_care_setting_uuid=os.getenv(
                "DRUG_ORDER_CARE_SETTING_UUID", "6f0c9a92-6f24-11e3-af88-005056821db0"
            ),
            drug_order_type_uuid=os.getenv(
                "DRUG_ORDER_TYPE_UUID", "131168f4-15f5-102d-96e4-000c29c2a5d7"
            ),
            cors_allowed_origins=_cors_origins_from_env(),
            form_agent_max_steps=int(os.getenv("FORM_AGENT_MAX_STEPS", "35")),
            form_agent_target_min_fields=int(os.getenv("FORM_AGENT_TARGET_MIN_FIELDS", "6")),
            form_agent_max_nudges=int(os.getenv("FORM_AGENT_MAX_NUDGES", "2")),
            form_agent_recovery_min_searches=int(os.getenv("FORM_AGENT_RECOVERY_MIN_SEARCHES", "6")),
            form_agent_brainstorm_max_tokens=int(os.getenv("FORM_AGENT_BRAINSTORM_MAX_TOKENS", "1400")),
            form_agent_tool_max_tokens=int(os.getenv("FORM_AGENT_TOOL_MAX_TOKENS", "900")),
            form_agent_subject_assessment=(
                os.getenv("FORM_AGENT_SUBJECT_ASSESSMENT", "").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
            form_agent_subject_max_searches=int(os.getenv("FORM_AGENT_SUBJECT_MAX_SEARCHES", "5")),
            form_agent_subject_max_tokens=int(os.getenv("FORM_AGENT_SUBJECT_MAX_TOKENS", "900")),
            form_agent_pipeline_v2=(
                os.getenv("FORM_AGENT_PIPELINE_V2", "1").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
            form_agent_research_max_searches=int(os.getenv("FORM_AGENT_RESEARCH_MAX_SEARCHES", "5")),
            form_agent_research_max_tokens=int(os.getenv("FORM_AGENT_RESEARCH_MAX_TOKENS", "1100")),
            form_agent_resolve_max_tokens=int(os.getenv("FORM_AGENT_RESOLVE_MAX_TOKENS", "1300")),
            report_agent_max_steps=int(os.getenv("REPORT_AGENT_MAX_STEPS", "25")),
            report_agent_brainstorm_max_tokens=int(os.getenv("REPORT_AGENT_BRAINSTORM_MAX_TOKENS", "1100")),
            report_agent_tool_max_tokens=int(os.getenv("REPORT_AGENT_TOOL_MAX_TOKENS", "900")),
            fhir_obs_page_size=int(os.getenv("FHIR_OBS_PAGE_SIZE", "200")),
            fhir_obs_max_pages=int(os.getenv("FHIR_OBS_MAX_PAGES", "25")),
            fhir_demographics_chunk=int(os.getenv("FHIR_DEMOGRAPHICS_CHUNK", "50")),
            cohort_max_patients=int(os.getenv("COHORT_MAX_PATIENTS", "500")),
            require_openmrs_session=(
                os.getenv("TENA_AGENT_REQUIRE_OPENMRS_SESSION", "").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
            use_optimized_prompts=(
                os.getenv("TENAOS_USE_OPTIMIZED_PROMPTS", "").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
        )
