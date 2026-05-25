from __future__ import annotations

import base64
import cgi
import email.parser
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from .ciel import CielClient, openmrs_uuid_for_concept_id
from .config import Settings
from .form_builder_tool_loop import FormBuilderToolLoop
from .scribe import (
    _SYSTEM_PROMPT as SCRIBE_SYSTEM_PROMPT,
    build_scribe_prompt,
    build_translation_prompt,
    parse_scribe_response,
    soap_to_note_text,
)
from .scribe_tool_loop import SoapScribeToolLoop, resolve_scribe_result
from .form_conversation import ConversationTurn, FormConversationDriver
from .form_drafts import DraftNotFoundError, FormDraftStore
from .insight_traces import InsightTraceStore
from .models import InsightTrace, MaterialTrace, ScribeTrace
from .openmrs import OpenMrsClient, PatientInsightContext
from .openmrs_reader import OpenmrsReader, ProgressCallback
from .openmrs_writer import OpenmrsWriter
from .report_builder_tool_loop import ReportBuilderToolLoop
from .report_conversation import (
    ConversationTurn as ReportConversationTurn,
    ReportConversationDriver,
)
from .report_drafts import ReportDraft, ReportDraftNotFoundError, ReportDraftStore
from .tool_loop import KbAgentLoop, KbGuidelinesClient
from .material_loop import PatientMaterialLoop
from .lab_catalog import (
    LabCatalogStore,
    add_lab_test_from_description,
    confirm_add_candidate,
)
from .llm_client import LlmClient
from .llm_backend import make_llm_client


def _utc_now_iso() -> str:
    # OpenMRS REST date conversion expects ISO8601 long format with timezone
    # offset as +0000, not Python's default +00:00 suffix.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000%z")


def _openmrs_concept_ref(concept_id_or_uuid: str) -> str:
    raw = str(concept_id_or_uuid or "").strip()
    if not raw:
        return raw
    if raw.isdigit():
        return openmrs_uuid_for_concept_id(raw)
    return raw


def _parse_dose_amount(text: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(match.group(1)) if match else 1.0


TRACES: dict[str, InsightTrace] = {}
MATERIALS: dict[str, MaterialTrace] = {}
SCRIBE_TRACES: dict[str, ScribeTrace] = {}
_TRACE_MAX = 500  # keep last N completed insight traces in memory
_MAX_BODY_BYTES = 4 * 1024 * 1024  # 4 MB request body limit
_LAB_CATALOG: LabCatalogStore | None = None
DEFAULT_FORM_ENCOUNTER_TYPE_UUID = os.getenv("FORM_DEFAULT_ENCOUNTER_TYPE_UUID") or "0e8230ce-bd1d-43f5-a863-cf44344fa4b0"


def _evict_old_traces(store: dict[str, Any], max_size: int = _TRACE_MAX) -> None:
    """Drop the oldest entries once the store exceeds max_size.

    Only completed/failed traces are candidates so in-flight work is never
    evicted; if all entries are running we leave the store as-is.
    """
    if len(store) <= max_size:
        return
    done = [k for k, v in store.items() if getattr(v, "status", "") in {"completed", "failed"}]
    for key in done[: len(store) - max_size]:
        store.pop(key, None)


def _get_lab_catalog(settings: Settings) -> LabCatalogStore:
    global _LAB_CATALOG
    if _LAB_CATALOG is None:
        from pathlib import Path as _Path
        db_path = settings.runtime_dir / "lab_catalog.sqlite3"
        _LAB_CATALOG = LabCatalogStore(db_path)
    return _LAB_CATALOG
_DRAFT_STORE: FormDraftStore | None = None
_CDS_TRACE_STORE: InsightTraceStore | None = None
_MATERIAL_TRACE_STORE: InsightTraceStore | None = None
_SCRIBE_TRACE_STORE: InsightTraceStore | None = None


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line for structured log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra_keys = set(record.__dict__) - logging.LogRecord.__dict__.keys() - {
            "message", "asctime", "args", "exc_info", "exc_text", "stack_info",
        }
        for key in extra_keys:
            payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def _configure_service_logger(settings: Settings) -> logging.Logger:
    """Structured JSON logger writing to ``runtime/tena-agent.log`` + stderr.

    Idempotent — safe to call multiple times (e.g. during test imports).
    """
    logger = logging.getLogger("tenaos.tena_agent")
    if getattr(logger, "_tenaos_configured", False):  # type: ignore[attr-defined]
        return logger
    logger.setLevel(logging.INFO)
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.runtime_dir / "tena-agent.log"
    fmt = _JsonFormatter()
    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        logger.addHandler(stream_handler)
    logger._tenaos_configured = True  # type: ignore[attr-defined]
    logger.propagate = False
    return logger


# Service logger exists at import time as a plain `logging.getLogger`. Handlers
# (rotating file + stderr) are attached by `_configure_service_logger` inside
# run(); at import time the logger has no handlers and is therefore safe for
# tests to import without touching the filesystem.
_SERVICE_LOGGER: logging.Logger = logging.getLogger("tenaos.tena_agent")


def _get_draft_store(settings: Settings) -> FormDraftStore:
    global _DRAFT_STORE
    if _DRAFT_STORE is None:
        _DRAFT_STORE = FormDraftStore(settings.drafts_db_path)
    return _DRAFT_STORE



def _get_cds_trace_store(settings: Settings) -> InsightTraceStore:
    global _CDS_TRACE_STORE
    if _CDS_TRACE_STORE is None:
        _CDS_TRACE_STORE = InsightTraceStore(settings.cds_traces_db_path, "cds")
    return _CDS_TRACE_STORE


def _get_material_trace_store(settings: Settings) -> InsightTraceStore:
    global _MATERIAL_TRACE_STORE
    if _MATERIAL_TRACE_STORE is None:
        _MATERIAL_TRACE_STORE = InsightTraceStore(settings.material_traces_db_path, "material")
    return _MATERIAL_TRACE_STORE


def _get_scribe_trace_store(settings: Settings) -> InsightTraceStore:
    global _SCRIBE_TRACE_STORE
    if _SCRIBE_TRACE_STORE is None:
        _SCRIBE_TRACE_STORE = InsightTraceStore(settings.scribe_traces_db_path, "scribe")
    return _SCRIBE_TRACE_STORE


from .routes.scribe_routes import ScribeRoutesMixin
from .routes.report_routes import (
    ReportRoutesMixin,
    _get_report_store,
    _report_draft_payload,
    _kickoff_report_conversation,
    _run_report_conversation_turn,
    _build_report_driver,
)
from .routes.labs_routes import LabsRoutesMixin


class TenaAgentRequestHandler(
    ScribeRoutesMixin,
    LabsRoutesMixin,
    ReportRoutesMixin,
    BaseHTTPRequestHandler,
):
    # Populated by run() before the HTTP server starts accepting connections.
    # Never accessed at import time so tests can monkeypatch Settings.from_env
    # without polluting module-level state.
    settings: Settings = None  # type: ignore[assignment]

    def do_OPTIONS(self) -> None:
        self._send_json({}, HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._handle_health()
            return
        if path.startswith("/insights/") and path.endswith("/events"):
            trace_id = path.split("/")[2]
            self._handle_insight_events(trace_id)
            return
        if path.startswith("/material/") and path.endswith("/events"):
            trace_id = path.split("/")[2]
            self._handle_material_events(trace_id)
            return
        if path.startswith("/scribe/") and path.endswith("/events"):
            trace_id = path.split("/")[2]
            self._handle_scribe_events(trace_id)
            return
        if path == "/labs/catalog":
            self._handle_get_lab_catalog()
            return
        if path == "/forms/drafts":
            self._handle_list_drafts(parsed)
            return
        if path == "/forms/encounter-types":
            self._handle_list_encounter_types()
            return
        if path == "/forms/ciel/health":
            self._handle_ciel_health()
            return
        if path.startswith("/forms/drafts/") and path.endswith("/events"):
            draft_id = path.split("/")[3]
            self._handle_draft_events(draft_id, parsed)
            return
        if path.startswith("/forms/drafts/") and path.endswith("/schema"):
            draft_id = path.split("/")[3]
            self._handle_draft_schema(draft_id)
            return
        if path.startswith("/forms/drafts/"):
            draft_id = path.split("/")[3]
            if draft_id:
                self._handle_get_draft(draft_id)
                return
        # ----- reports -----
        if path == "/reports/drafts":
            self._handle_list_report_drafts(parsed)
            return
        if path.startswith("/reports/drafts/") and path.endswith("/events"):
            draft_id = path.split("/")[3]
            self._handle_report_draft_events(draft_id, parsed)
            return
        if path.startswith("/reports/drafts/") and path.endswith("/result"):
            draft_id = path.split("/")[3]
            self._handle_get_report_result(draft_id)
            return
        if path.startswith("/reports/drafts/"):
            draft_id = path.split("/")[3]
            if draft_id:
                self._handle_get_report_draft(draft_id)
                return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/insights/patient/"):
            patient_uuid = path.rsplit("/", 1)[-1]
            self._handle_post_insight(patient_uuid)
            return
        if path.startswith("/material/patient/"):
            patient_uuid = path.rsplit("/", 1)[-1]
            self._handle_post_material(patient_uuid)
            return
        if path == "/translate":
            self._handle_translate()
            return
        if path == "/labs/catalog/add":
            self._handle_labs_catalog_add()
            return
        if path == "/labs/catalog/confirm":
            self._handle_labs_catalog_confirm()
            return
        if path.startswith("/labs/catalog/") and path.endswith("/remove"):
            entry_uuid = path.split("/")[3]
            self._handle_labs_catalog_delete(entry_uuid)
            return
        if path == "/scribe/process_text":
            self._handle_scribe_process_text()
            return
        if path == "/scribe/process_text_trace":
            self._handle_scribe_process_text_trace()
            return
        if path == "/scribe/process_voice":
            self._handle_scribe_process_voice()
            return
        if path == "/scribe/confirm_text":
            self._handle_scribe_confirm_text()
            return
        if path == "/forms/drafts":
            self._handle_create_draft()
            return
        if path.startswith("/forms/drafts/") and path.endswith("/messages"):
            draft_id = path.split("/")[3]
            self._handle_post_draft_message(draft_id)
            return
        if path.startswith("/forms/drafts/") and path.endswith("/actions"):
            draft_id = path.split("/")[3]
            self._handle_post_draft_action(draft_id)
            return
        if path.startswith("/forms/drafts/") and path.endswith("/publish"):
            draft_id = path.split("/")[3]
            self._handle_publish_draft(draft_id)
            return
        if path.startswith("/forms/drafts/") and path.endswith("/operations"):
            draft_id = path.split("/")[3]
            self._handle_apply_operations(draft_id)
            return
        # ----- reports -----
        if path == "/reports/drafts":
            self._handle_create_report_draft()
            return
        if path.startswith("/reports/drafts/") and path.endswith("/messages"):
            draft_id = path.split("/")[3]
            self._handle_post_report_message(draft_id)
            return
        if path.startswith("/reports/drafts/") and path.endswith("/actions"):
            draft_id = path.split("/")[3]
            self._handle_post_report_action(draft_id)
            return
        if path.startswith("/reports/drafts/") and path.endswith("/operations"):
            draft_id = path.split("/")[3]
            self._handle_apply_report_operations(draft_id)
            return
        if path.startswith("/reports/drafts/") and path.endswith("/run"):
            draft_id = path.split("/")[3]
            self._handle_run_report(draft_id)
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/reports/drafts/"):
            draft_id = path.split("/")[3]
            if draft_id:
                self._handle_delete_report_draft(draft_id)
                return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    # -- /health, /insights ----

    def _handle_health(self) -> None:
        llm_status = make_llm_client(self.settings).health()
        ciel_status = CielClient(self.settings).availability_detail()
        self._send_json(
            {
                "ok": True,
                "service": "tena-agent",
                "llm": llm_status.to_dict(),
                "ciel": ciel_status,
                "kb": {
                    "url": self.settings.kb_guidelines_url,
                    "collection": "who_msf_guidelines",
                },
            },
        )

    def _handle_insight_events(self, trace_id: str) -> None:
        trace = TRACES.get(trace_id)
        if not trace:
            self._send_json({"error": "Trace not found"}, HTTPStatus.NOT_FOUND)
            return
        if "text/event-stream" in (self.headers.get("Accept") or ""):
            self._send_insight_event_stream(trace)
            return
        self._send_json(trace.to_dict())

    def _handle_translate(self) -> None:
        body = self._read_json()
        content = (body.get("content") or "").strip()
        language = (body.get("language") or "Amharic").strip()
        if not content:
            self._send_json({"error": "content is required"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            llm = make_llm_client(self.settings)
            prompt = (
                f"Translate the following clinical decision support report to {language}. "
                "Rules:\n"
                "1. Preserve ALL markdown structure exactly: ## headings, - bullet points, **bold**, *(citations)*\n"
                "2. Translate ONLY the text content — do NOT translate or modify *(WHO: ...)* or *(MSF: ...)* citation tags\n"
                "3. Use accurate medical terminology in the target language\n"
                "4. Do NOT add explanations, notes, or any extra text — output ONLY the translated report\n\n"
                f"Report to translate:\n\n{content}"
            )
            # stream=True keeps the socket alive during generation so the
            # per-read timeout (request_timeout_seconds=20) is never triggered
            # for what is a ~25-35 s generation job at 114 tok/s.
            # max_tokens=6144 matches the care-guide format budget so the
            # Amharic output is never truncated mid-section.
            response = llm.chat(
                [
                    {"role": "system", "content": f"You are a precise medical translator. Translate to {language} accurately."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=6144,
                stream=True,
            )
            translated = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            self._send_json({"translatedContent": translated.strip()})
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logging.getLogger("tenaos.tena_agent.translate").warning(
                "Translate upstream error: status=%s detail=%s", exc.code, detail[:1000]
            )
            self._send_json({"error": detail or str(exc)}, HTTPStatus.BAD_GATEWAY)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_material_events(self, trace_id: str) -> None:
        trace = MATERIALS.get(trace_id)
        if not trace:
            self._send_json({"error": "Material trace not found"}, HTTPStatus.NOT_FOUND)
            return
        if "text/event-stream" in (self.headers.get("Accept") or ""):
            self._send_material_event_stream(trace)
            return
        self._send_json(trace.to_dict())

    def _handle_post_material(self, patient_uuid: str) -> None:
        body = self._read_json()
        _evict_old_traces(MATERIALS)  # type: ignore[arg-type]
        trace = MaterialTrace(patient_uuid=patient_uuid)
        MATERIALS[trace.trace_id] = trace
        trace.add("queued", "Patient material started", "Preparing patient context for WHO/MSF KB search.", {"request": body})
        threading.Thread(
            target=_run_material,
            args=(self.settings, patient_uuid, trace.trace_id, self.headers.get("Authorization"), self.headers.get("Cookie"), body),
            daemon=True,
        ).start()
        self._send_json(trace.to_dict(), HTTPStatus.ACCEPTED)

    def _send_material_event_stream(self, trace: MaterialTrace) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._add_cors_headers()
        self.end_headers()
        last_payload = ""
        while True:
            payload = json.dumps(trace.to_dict())
            if payload != last_payload:
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_payload = payload
            if trace.status in {"completed", "failed"}:
                break
            time.sleep(0.4)

    def _handle_post_insight(self, patient_uuid: str) -> None:
        body = self._read_json()
        _evict_old_traces(TRACES)  # type: ignore[arg-type]
        trace = InsightTrace(patient_uuid=patient_uuid)
        TRACES[trace.trace_id] = trace
        trace.add("queued", "AI insight started", "Preparing patient context for WHO/MSF KB search.", {"request": body})
        threading.Thread(
            target=_run_insight,
            args=(self.settings, patient_uuid, trace.trace_id, self.headers.get("Authorization"), self.headers.get("Cookie"), body),
            daemon=True,
        ).start()
        self._send_json(trace.to_dict(), HTTPStatus.ACCEPTED)

    # -- /forms ----

    def _handle_ciel_health(self) -> None:
        self._send_json(CielClient(self.settings).availability_detail())

    def _handle_list_encounter_types(self) -> None:
        try:
            writer = self._build_writer()
            encounter_types = writer.list_encounter_types(limit=100)
            self._send_json({"encounterTypes": encounter_types})
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)

    def _handle_list_drafts(self, parsed: Any) -> None:
        store = _get_draft_store(self.settings)
        params = parse_qs(parsed.query)
        owner = (params.get("owner") or [None])[0]
        drafts = store.list_drafts(owner=owner)
        self._send_json({"drafts": [draft.to_dict() for draft in drafts]})

    def _handle_get_draft(self, draft_id: str) -> None:
        store = _get_draft_store(self.settings)
        try:
            draft = store.get_draft(draft_id)
        except DraftNotFoundError:
            self._send_json({"error": "Draft not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(draft.to_dict())

    def _handle_draft_schema(self, draft_id: str) -> None:
        store = _get_draft_store(self.settings)
        try:
            draft = store.get_draft(draft_id)
        except DraftNotFoundError:
            self._send_json({"error": "Draft not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json({"schema": draft.last_schema, "validation": draft.last_validation})

    def _handle_create_draft(self) -> None:
        body = self._read_json()
        store = _get_draft_store(self.settings)
        # Draft starts unnamed and unsourced; the conversation collects both.
        # Callers may still pre-seed (name, encounterTypeUuid) to skip Stage 1.
        prefill_name = body.get("name") if isinstance(body.get("name"), str) else ""
        prefill_encounter = body.get("encounterTypeUuid") if isinstance(body.get("encounterTypeUuid"), str) else None

        # importFormSchema: an existing O3 form JSON forwarded by the frontend
        # when the user clicks "Edit" on a published form. We parse the schema
        # into an initial basket so the draft starts fully populated and the
        # agent can immediately edit rather than rebuild from scratch.
        import_schema = body.get("importFormSchema") if isinstance(body.get("importFormSchema"), dict) else None
        initial_basket: dict | None = None
        imported_field_count = 0
        if import_schema:
            initial_basket, imported_field_count = _o3_schema_to_basket(import_schema)
            # Prefer the schema's encounter type if not explicitly provided.
            if not prefill_encounter:
                raw_et = import_schema.get("encounterType")
                if isinstance(raw_et, str) and raw_et.strip():
                    prefill_encounter = raw_et.strip()
                elif isinstance(raw_et, dict) and isinstance(raw_et.get("uuid"), str):
                    prefill_encounter = raw_et["uuid"].strip()

        if not prefill_encounter:
            prefill_encounter = DEFAULT_FORM_ENCOUNTER_TYPE_UUID

        draft = store.create_draft(
            name=str(prefill_name or "Untitled form"),
            owner=body.get("owner") if isinstance(body.get("owner"), str) else None,
            description=body.get("description") if isinstance(body.get("description"), str) else None,
            encounter_type_uuid=prefill_encounter,
            version=str(body.get("version") or "1.0.0"),
            basket=initial_basket,
        )
        # Skip ahead through whichever stages the caller already satisfied so
        # the first agent prompt matches the missing state.
        initial_state = "awaiting_name"
        if prefill_name and prefill_encounter:
            initial_state = "awaiting_question"
        elif prefill_name:
            initial_state = "awaiting_encounter_type"
        elif prefill_encounter:
            initial_state = "awaiting_name"
        if initial_state != "awaiting_name":
            store.update_draft(draft.draft_id, conversation_state=initial_state)
        threading.Thread(
            target=_kickoff_conversation,
            args=(self.settings, draft.draft_id, initial_state, self.headers.get("Authorization"), self.headers.get("Cookie"), imported_field_count),
            daemon=True,
        ).start()
        # Re-read so the response reflects any conversation_state bump.
        self._send_json(store.get_draft(draft.draft_id).to_dict(), HTTPStatus.ACCEPTED)

    def _handle_post_draft_message(self, draft_id: str) -> None:
        body = self._read_json()
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            self._send_json({"error": "message is required"}, HTTPStatus.BAD_REQUEST)
            return
        store = _get_draft_store(self.settings)
        try:
            store.get_draft(draft_id)
        except DraftNotFoundError:
            self._send_json({"error": "Draft not found"}, HTTPStatus.NOT_FOUND)
            return
        turn = ConversationTurn(kind="message", message=message.strip())
        threading.Thread(
            target=_run_conversation_turn,
            args=(self.settings, draft_id, turn, self.headers.get("Authorization"), self.headers.get("Cookie")),
            daemon=True,
        ).start()
        self._send_json({"draftId": draft_id, "accepted": True}, HTTPStatus.ACCEPTED)

    def _handle_post_draft_action(self, draft_id: str) -> None:
        body = self._read_json()
        action = body.get("action")
        if not isinstance(action, str) or not action.strip():
            self._send_json({"error": "action is required"}, HTTPStatus.BAD_REQUEST)
            return
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        store = _get_draft_store(self.settings)
        try:
            store.get_draft(draft_id)
        except DraftNotFoundError:
            self._send_json({"error": "Draft not found"}, HTTPStatus.NOT_FOUND)
            return
        turn = ConversationTurn(kind="action", action=action.strip(), payload=payload)
        threading.Thread(
            target=_run_conversation_turn,
            args=(self.settings, draft_id, turn, self.headers.get("Authorization"), self.headers.get("Cookie")),
            daemon=True,
        ).start()
        self._send_json({"draftId": draft_id, "accepted": True}, HTTPStatus.ACCEPTED)

    def _handle_apply_operations(self, draft_id: str) -> None:
        body = self._read_json()
        operations = body.get("operations")
        if not isinstance(operations, list):
            self._send_json({"error": "operations must be an array"}, HTTPStatus.BAD_REQUEST)
            return
        store = _get_draft_store(self.settings)
        try:
            store.get_draft(draft_id)
        except DraftNotFoundError:
            self._send_json({"error": "Draft not found"}, HTTPStatus.NOT_FOUND)
            return
        loop = self._build_form_tool_loop()
        update_result = loop.update_form_draft(draft_id, operations, actor="user")
        build_result = loop.build_form_schema(draft_id)
        self._send_json({"update": update_result, "build": build_result})

    def _handle_publish_draft(self, draft_id: str) -> None:
        body = self._read_json()
        store = _get_draft_store(self.settings)
        try:
            draft = store.get_draft(draft_id)
        except DraftNotFoundError:
            self._send_json({"error": "Draft not found"}, HTTPStatus.NOT_FOUND)
            return
        # Allow caller to supply final metadata adjustments before publish.
        updates: dict[str, Any] = {}
        for field in ("name", "version", "description", "encounterTypeUuid"):
            if field in body and isinstance(body[field], str) and body[field].strip():
                key_map = {"encounterTypeUuid": "encounter_type_uuid"}.get(field, field)
                updates[key_map] = body[field].strip()
        if updates:
            store.update_draft(draft_id, **updates)
            store.append_event(draft_id, actor="user", operation="update_metadata", detail="User finalised draft metadata before publish.", payload=updates)
        loop = self._build_form_tool_loop()
        # Always rebuild before publish so the schema reflects the latest metadata.
        build_result = loop.build_form_schema(draft_id)
        publish_result = loop.publish_form(draft_id, mark_published=bool(body.get("markPublished", True)))
        self._send_json({"build": build_result, "publish": publish_result})

    def _handle_draft_events(self, draft_id: str, parsed: Any) -> None:
        store = _get_draft_store(self.settings)
        try:
            store.get_draft(draft_id)
        except DraftNotFoundError:
            self._send_json({"error": "Draft not found"}, HTTPStatus.NOT_FOUND)
            return
        if "text/event-stream" in (self.headers.get("Accept") or ""):
            self._send_draft_event_stream(draft_id, store)
            return
        params = parse_qs(parsed.query)
        since = (params.get("since") or [None])[0]
        events = store.list_events(draft_id, since=since)
        self._send_json({"draftId": draft_id, "events": [event.to_dict() for event in events]})

    # -- /reports ----

    # ---- shared plumbing ----

    def _build_writer(self) -> OpenmrsWriter:
        return OpenmrsWriter(
            self.settings,
            authorization=self.headers.get("Authorization"),
            cookie=self.headers.get("Cookie"),
        )

    def _build_form_tool_loop(self) -> FormBuilderToolLoop:
        store = _get_draft_store(self.settings)
        ciel = CielClient(self.settings)
        llm = make_llm_client(self.settings)
        authorization = self.headers.get("Authorization")
        cookie = self.headers.get("Cookie")
        settings = self.settings
        return FormBuilderToolLoop(
            store=store,
            ciel=ciel,
            llm=llm,
            writer_factory=lambda: OpenmrsWriter(settings, authorization=authorization, cookie=cookie),
        )

    def _build_report_tool_loop(self) -> ReportBuilderToolLoop:
        store = _get_report_store(self.settings)
        ciel = CielClient(self.settings)
        authorization = self.headers.get("Authorization")
        cookie = self.headers.get("Cookie")
        settings = self.settings

        def reader_factory(progress: ProgressCallback | None = None) -> OpenmrsReader:
            return OpenmrsReader(
                settings,
                authorization=authorization,
                cookie=cookie,
                progress=progress,
            )

        return ReportBuilderToolLoop(store=store, ciel=ciel, reader_factory=reader_factory)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        if length > _MAX_BODY_BYTES:
            self._send_json({"error": "Request body too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _cors_origin_header(self) -> str | None:
        """Return the validated Origin to echo, or None if not in the allowlist."""
        origin = self.headers.get("Origin", "")
        if not origin:
            return None
        if origin in self.settings.cors_allowed_origins:
            return origin
        return None

    def _add_cors_headers(self) -> None:
        origin = self._cors_origin_header()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        if status != HTTPStatus.NO_CONTENT:
            self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if status != HTTPStatus.NO_CONTENT:
            self.wfile.write(encoded)

    def _send_insight_event_stream(self, trace: InsightTrace) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._add_cors_headers()
        self.end_headers()
        last_payload = ""
        while True:
            payload = json.dumps(trace.to_dict())
            if payload != last_payload:
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_payload = payload
            if trace.status in {"completed", "failed"}:
                break
            time.sleep(0.4)

    def _send_report_event_stream(self, draft_id: str, store: ReportDraftStore) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._add_cors_headers()
        self.end_headers()
        last_seen: str | None = None
        last_status: str | None = None
        idle_iterations = 0
        max_idle_iterations = 600
        while True:
            events = store.list_events(draft_id, since=last_seen, limit=100)
            for event in events:
                payload = json.dumps({"type": "event", "event": event.to_dict()})
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_seen = event.timestamp
                idle_iterations = 0
            try:
                draft = store.get_draft(draft_id)
            except ReportDraftNotFoundError:
                break
            if draft.status != last_status:
                payload = json.dumps({"type": "status", "status": draft.status, "lastRunAt": draft.last_run_at})
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_status = draft.status
            if draft.status in {"failed", "archived"}:
                break
            idle_iterations += 1
            if idle_iterations > max_idle_iterations:
                break
            time.sleep(0.4)

    def _send_draft_event_stream(self, draft_id: str, store: FormDraftStore) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._add_cors_headers()
        self.end_headers()
        last_seen: str | None = None
        last_status: str | None = None
        idle_iterations = 0
        max_idle_iterations = 600  # ~4 minutes at 0.4s poll
        while True:
            events = store.list_events(draft_id, since=last_seen, limit=100)
            for event in events:
                payload = json.dumps({"type": "event", "event": event.to_dict()})
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_seen = event.timestamp
                idle_iterations = 0
            try:
                draft = store.get_draft(draft_id)
            except DraftNotFoundError:
                break
            if draft.status != last_status:
                payload = json.dumps({"type": "status", "status": draft.status, "publishedFormUuid": draft.published_form_uuid})
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_status = draft.status
            if draft.status in {"published", "failed", "archived"}:
                break
            idle_iterations += 1
            if idle_iterations > max_idle_iterations:
                break
            time.sleep(0.4)

    def log_message(self, format: str, *args: Any) -> None:
        _SERVICE_LOGGER.info("%s %s", self.address_string(), format % args)


import base64 as _base64


def _service_auth(settings: Settings) -> str:
    """Basic auth header value using the configured OpenMRS service account."""
    raw = f"{settings.openmrs_service_user}:{settings.openmrs_service_password}"
    return "Basic " + _base64.b64encode(raw.encode()).decode()


def _run_insight(settings: Settings, patient_uuid: str, trace_id: str, authorization: str | None, cookie: str | None, body: dict[str, Any]) -> None:
    trace = TRACES[trace_id]
    try:
        try:
            openmrs = OpenMrsClient(settings, authorization=authorization, cookie=cookie)
            context = openmrs.build_patient_context(patient_uuid)
        except Exception:
            # Session expired — retry with service account credentials.
            openmrs = OpenMrsClient(settings, authorization=_service_auth(settings), cookie=None)
            context = openmrs.build_patient_context(patient_uuid)
        kb = KbGuidelinesClient(base_url=settings.kb_guidelines_url)
        loop = KbAgentLoop(llm=make_llm_client(settings), kb=kb, trace_store=_get_cds_trace_store(settings))
        structured = loop.run(trace, context)
        trace.complete(structured)
    except Exception as exc:
        tb = traceback.format_exc()
        _dump_run_failure("ai_insight", trace_id, patient_uuid, exc, tb)
        trace.fail(f"{type(exc).__name__}: {exc}", {"request": body, "traceback": tb.splitlines()[-12:]})


def _run_material(settings: Settings, patient_uuid: str, trace_id: str, authorization: str | None, cookie: str | None, body: dict[str, Any]) -> None:
    trace = MATERIALS[trace_id]
    try:
        try:
            openmrs = OpenMrsClient(settings, authorization=authorization, cookie=cookie)
            context = openmrs.build_patient_context(patient_uuid)
        except Exception:
            openmrs = OpenMrsClient(settings, authorization=_service_auth(settings), cookie=None)
            context = openmrs.build_patient_context(patient_uuid)
        kb = KbGuidelinesClient(base_url=settings.kb_guidelines_url)
        loop = PatientMaterialLoop(llm=make_llm_client(settings), kb=kb, trace_store=_get_material_trace_store(settings))
        material = loop.run(trace, context)
        trace.complete(material)
    except Exception as exc:
        tb = traceback.format_exc()
        _dump_run_failure("patient_material", trace_id, patient_uuid, exc, tb)
        trace.fail(f"{type(exc).__name__}: {exc}", {"request": body, "traceback": tb.splitlines()[-12:]})


def _run_scribe_text_trace(settings: Settings, trace_id: str, authorization: str | None, cookie: str | None, body: dict[str, Any]) -> None:
    trace = SCRIBE_TRACES[trace_id]
    note_text = (body.get("noteText") or body.get("note_text") or "").strip()
    patient_uuid = (body.get("patientUuid") or body.get("patient_uuid") or "").strip()
    language = (body.get("language") or "english").strip().lower()
    try:
        llm = make_llm_client(settings)
        original_note = note_text
        if language == "amharic":
            trace.add("model_tool_call", "translate_note", "Translating Amharic note to English before SOAP extraction.")
            trans_resp = llm.chat(build_translation_prompt(note_text), temperature=0.1, max_tokens=800)
            translation = trans_resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if translation:
                note_text = translation
            trace.add("middleware_result", "Translation complete", "Amharic note translated for SOAP extraction.", {"translatedText": note_text})

        patient_summary: str | None = None
        if patient_uuid:
            try:
                openmrs = OpenMrsClient(settings, authorization=authorization, cookie=cookie)
                ctx = openmrs.build_patient_context(patient_uuid)
                patient_summary = ctx.to_kb_query()
                trace.add("context", "Built patient context", "Patient background prepared for scribe context.", {"summary": patient_summary})
            except Exception as exc:
                trace.add("context", "Patient context unavailable", str(exc))

        ciel = CielClient(settings)

        def sink(event: dict[str, Any]) -> None:
            trace.add(str(event.get("type") or "event"), str(event.get("title") or ""), str(event.get("detail") or ""), event.get("payload") or {})

        try:
            resolved = SoapScribeToolLoop(llm, ciel, event_sink=sink, trace_store=_get_scribe_trace_store(settings)).run(note_text, patient_summary)
        except Exception as exc:
            trace.add("error", "SOAP tool loop failed", f"{type(exc).__name__}: {exc}")
            messages = build_scribe_prompt(note_text, patient_summary)
            response = llm.chat(messages, temperature=0.1, max_tokens=1600)
            raw = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            result = parse_scribe_response(raw)
            resolved = resolve_scribe_result(result, ciel)
            trace.add("model_reasoning", "One-shot fallback response", raw[:1800], {"fallback": True})
            trace.add("model_summary", "Fallback SOAP parsed", "Parsed legacy one-shot SOAP JSON response.", {"fallback": True})

        payload = {
            "soap": resolved["soap"],
            "concepts": resolved["concepts"],
            "observations": resolved["observations"],
            "medications": resolved["medications"],
            "generationTrace": [event.to_dict() for event in trace.events],
            "soapText": soap_to_note_text(resolved["soap"]),
        }
        if language == "amharic" and note_text != original_note:
            payload["translatedText"] = note_text
        trace.complete(payload)
    except Exception as exc:
        tb = traceback.format_exc()
        _dump_run_failure("text_scribe", trace_id, patient_uuid, exc, tb)
        trace.fail(f"{type(exc).__name__}: {exc}", {"request": body, "traceback": tb.splitlines()[-12:]})


def _dump_run_failure(kind: str, trace_id: str, patient_uuid: str, exc: BaseException, tb: str) -> None:
    """Persist the full traceback to a plain-text file for diagnosis.

    The structured JSON logger sometimes drops ``%`` args when records are
    emitted from background threads under load; route failure tracebacks
    to a dedicated sink so they are always recoverable.
    """
    try:
        msg_line = f"{kind} run failed trace_id={trace_id} patient={patient_uuid} exc={type(exc).__name__}: {exc}"
        # 1) Best-effort structured log (single line, pre-rendered).
        try:
            _SERVICE_LOGGER.error(msg_line)
        except Exception:
            pass
        # 2) Plain-text traceback sink. Use the handler's bound settings so
        # tests and re-entrant calls don't re-read env every invocation.
        runtime_dir = (
            TenaAgentRequestHandler.settings.runtime_dir
            if TenaAgentRequestHandler.settings is not None
            else Settings.from_env().runtime_dir
        )
        sink = runtime_dir / "tena-agent-failures.log"
        with open(sink, "a", encoding="utf-8") as fh:
            fh.write("=" * 80 + "\n")
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {msg_line}\n")
            fh.write(tb)
            fh.write("\n")
    except Exception:
        pass


def _build_driver(
    settings: Settings, authorization: str | None, cookie: str | None
) -> FormConversationDriver:
    store = _get_draft_store(settings)
    ciel = CielClient(settings)
    llm = make_llm_client(settings)

    def writer_factory() -> OpenmrsWriter:
        return OpenmrsWriter(settings, authorization=authorization, cookie=cookie)

    loop = FormBuilderToolLoop(store=store, ciel=ciel, llm=llm, writer_factory=writer_factory)
    return FormConversationDriver(store=store, ciel=ciel, loop=loop, llm=llm)


def _concept_id_from_openmrs_uuid(uuid: str) -> str | None:
    """Reverse-engineer a CIEL concept id from its OpenMRS UUID.

    CIEL concept UUIDs use the pattern ``<numeric_id>`` + ``'A' * (36 - len(numeric_id))``.
    Returns None when the UUID does not match this pattern (e.g. real random UUIDs).
    """
    if not uuid or len(uuid) != 36:
        return None
    stripped = uuid.rstrip("A")
    if not stripped or not stripped.isdigit():
        return None
    return stripped


def _o3_schema_to_basket(schema: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Convert an OpenMRS O3 form schema into an initial concept basket.

    Iterates ``pages[*].sections[*].questions`` and extracts each question's
    ``questionOptions.concept`` (an OpenMRS UUID). CIEL concept UUIDs follow
    the pattern ``<numeric_id><A*>(to 36 chars)``, so stripping trailing A's
    gives the CIEL numeric id that the basket and downstream schema builder use.

    Sections without any resolvable CIEL concept are included as empty
    containers so the structure is preserved. Questions with non-CIEL UUIDs
    (e.g. real random UUIDs) are silently skipped.

    Returns (basket_dict, total_field_count).
    """

    sections: list[dict[str, Any]] = []
    total_fields = 0

    for page in schema.get("pages") or []:
        for o3_section in page.get("sections") or []:
            section_label = str(o3_section.get("label") or "Section").strip()
            section_id = str(uuid.uuid4())
            fields: list[dict[str, Any]] = []

            for question in o3_section.get("questions") or []:
                opts = question.get("questionOptions") or {}
                concept_uuid = str(opts.get("concept") or "").strip()
                if not concept_uuid:
                    continue
                concept_id = _concept_id_from_openmrs_uuid(concept_uuid)
                if not concept_id:
                    continue
                label_override = str(question.get("label") or "").strip() or None
                required = bool(question.get("required", False))
                rendering_override = str(opts.get("rendering") or "").strip() or None
                fields.append({
                    "conceptId": concept_id,
                    "labelOverride": label_override,
                    "required": required,
                    "renderingOverride": rendering_override,
                })
                total_fields += 1

            sections.append({
                "sectionId": section_id,
                "label": section_label,
                "fields": fields,
                "conceptId": None,
                "kind": "container",
                "isExpanded": True,
            })

    return {"sections": sections}, total_fields


def _kickoff_conversation(
    settings: Settings,
    draft_id: str,
    initial_state: str,
    authorization: str | None,
    cookie: str | None,
    imported_field_count: int = 0,
) -> None:
    driver = _build_driver(settings, authorization, cookie)
    store = _get_draft_store(settings)
    try:
        if initial_state == "awaiting_name":
            driver.kickoff(draft_id)
        elif initial_state == "awaiting_encounter_type":
            driver._emit_encounter_type_picker(draft_id)  # type: ignore[attr-defined]
        elif initial_state == "awaiting_question":
            if imported_field_count > 0:
                driver._emit_prompt(  # type: ignore[attr-defined]
                    draft_id,
                    f"I've loaded your existing form — it has {imported_field_count} question{'s' if imported_field_count != 1 else ''} across the sections shown in the preview. "
                    "You can ask me to add new questions, remove or rename existing ones, reorder sections, or change any field. "
                    "What would you like to change?",
                )
            else:
                driver._emit_prompt(  # type: ignore[attr-defined]
                    draft_id,
                    "What should be the first question on the form? Describe it in your own words.",
                )
    except Exception as exc:
        _SERVICE_LOGGER.exception("Conversation kickoff failed for draft %s", draft_id)
        store.append_event(
            draft_id,
            actor="middleware",
            operation="kickoff_failed",
            detail=f"Conversation kickoff failed: {type(exc).__name__}: {exc}",
            payload={"error": str(exc), "traceback": traceback.format_exc()},
        )


def _run_conversation_turn(
    settings: Settings,
    draft_id: str,
    turn: ConversationTurn,
    authorization: str | None,
    cookie: str | None,
) -> None:
    driver = _build_driver(settings, authorization, cookie)
    store = _get_draft_store(settings)
    started = time.monotonic()
    _SERVICE_LOGGER.info(
        "draft=%s turn=%s start", draft_id, turn.kind if turn else "?",
    )
    try:
        driver.handle_user_turn(draft_id, turn)
        _SERVICE_LOGGER.info(
            "draft=%s turn=%s ok elapsed=%.2fs", draft_id, turn.kind, time.monotonic() - started,
        )
    except Exception as exc:
        _SERVICE_LOGGER.exception(
            "draft=%s turn=%s failed after %.2fs", draft_id, turn.kind, time.monotonic() - started,
        )
        store.append_event(
            draft_id,
            actor="middleware",
            operation="conversation_turn_failed",
            detail=f"Conversation turn failed: {type(exc).__name__}: {exc}",
            payload={"error": str(exc), "traceback": traceback.format_exc()},
        )



def run() -> None:
    settings = Settings.from_env()
    TenaAgentRequestHandler.settings = settings
    _configure_service_logger(settings)
    _SERVICE_LOGGER.info(
        "TenaAgent service starting host=%s port=%s llm=%s ciel_sqlite=%s drafts_db=%s",
        settings.host,
        settings.port,
        settings.llm_base_url,
        settings.ciel_sqlite_path,
        settings.drafts_db_path,
    )
    _get_draft_store(settings)  # eager init so the schema exists before first request
    _get_report_store(settings)  # eager init the report store
    server = ThreadingHTTPServer((settings.host, settings.port), TenaAgentRequestHandler)
    print(f"TenaAgent service listening on {settings.host}:{settings.port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
