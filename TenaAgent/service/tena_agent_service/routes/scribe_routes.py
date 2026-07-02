"""Scribe HTTP handler methods.

Pulled out of `app.py` so the route-handler class stays under ~1k lines.
The methods rely on attributes provided by `TenaAgentRequestHandler`
(self.settings, self._send_json, self._read_json_body, etc.) which are
resolved at runtime via mixin MRO.

Module-level state owned here:
- SCRIBE_TRACES  — in-memory trace store for async text-scribe SSE flow
- _get_scribe_trace_store — persistent InsightTraceStore accessor
- _run_scribe_text_trace  — background thread worker for the trace flow
"""
from __future__ import annotations

import base64
import cgi
import email.parser
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import traceback
from http import HTTPStatus
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from ..ciel import CielClient, openmrs_uuid_for_concept_id
from ..config import Settings
from ..insight_traces import InsightTraceStore
from ..llm_backend import make_llm_client
from ..models import ScribeTrace
from ..openmrs import OpenMrsClient
from ..scribe import (
    _SYSTEM_PROMPT as SCRIBE_SYSTEM_PROMPT,
    build_scribe_prompt,
    build_translation_prompt,
    parse_scribe_response,
    soap_to_note_text,
)
from ..scribe_tool_loop import SoapScribeToolLoop, resolve_scribe_result
from .deps import (
    _evict_old_traces,
    _openmrs_concept_ref,
    _parse_dose_amount,
    _utc_now_iso,
)

# ---------------------------------------------------------------------------
# Module-level scribe state
# ---------------------------------------------------------------------------

SCRIBE_TRACES: dict[str, ScribeTrace] = {}
_MAX_VOICE_BODY_BYTES = 100 * 1024 * 1024

_SCRIBE_TRACE_STORE: InsightTraceStore | None = None


def _get_scribe_trace_store(settings: Settings) -> InsightTraceStore:
    global _SCRIBE_TRACE_STORE
    if _SCRIBE_TRACE_STORE is None:
        _SCRIBE_TRACE_STORE = InsightTraceStore(settings.scribe_traces_db_path, "scribe")
    return _SCRIBE_TRACE_STORE


def _dump_scribe_run_failure(trace_id: str, patient_uuid: str, exc: Exception, tb: str) -> None:
    logging.getLogger("tenaos.tena_agent.scribe").error(
        "text_scribe run failed trace_id=%s patient=%s error=%s",
        trace_id, patient_uuid, exc,
        exc_info=False,
    )


def _run_scribe_text_trace(
    settings: Settings,
    trace_id: str,
    authorization: str | None,
    cookie: str | None,
    body: dict[str, Any],
) -> None:
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
        _dump_scribe_run_failure(trace_id, patient_uuid, exc, tb)
        trace.fail(f"{type(exc).__name__}: {exc}", {"request": body, "traceback": tb.splitlines()[-12:]})


class ScribeRoutesMixin:
    def _handle_scribe_events(self, trace_id: str) -> None:
        trace = SCRIBE_TRACES.get(trace_id)
        if not trace:
            self._send_json({"error": "Scribe trace not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._add_cors_headers()
        self.end_headers()
        last_payload = ""
        while True:
            payload = json.dumps(trace.to_dict(), default=str)
            if payload != last_payload:
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_payload = payload
            if trace.status in {"completed", "failed"}:
                break
            time.sleep(0.25)
    def _handle_scribe_process_voice(self) -> None:
        """Accept multipart/form-data with 'audio' (webm/opus blob) + 'patient_uuid',
        convert to 16kHz mono WAV with ffmpeg, base64-encode, and send to Gemma 4
        as an audio_url content block.  Response parsed identically to process_text."""

        # language is passed as a form field (optional, defaults to "english")
        _voice_language_hint = "english"  # resolved after multipart parse
        content_type = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in content_type:
            self._send_json({"error": "multipart/form-data required"}, HTTPStatus.BAD_REQUEST)
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            self._send_json({"error": "empty body"}, HTTPStatus.BAD_REQUEST)
            return
        if length > _MAX_VOICE_BODY_BYTES:
            self._send_json({"error": "Request body too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return

        raw_body = self.rfile.read(length)

        # Parse multipart manually using email library
        msg_bytes = f"Content-Type: {content_type}\r\n\r\n".encode() + raw_body
        msg = email.parser.BytesParser().parsebytes(msg_bytes)

        audio_data: bytes | None = None
        patient_uuid: str = ""
        voice_language: str = "english"

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = part.get("Content-Disposition", "")
            # Extract field name
            name_match = __import__("re").search(r'name="([^"]+)"', disposition)
            field_name = name_match.group(1) if name_match else ""
            if field_name == "audio":
                audio_data = part.get_payload(decode=True)
            elif field_name == "patient_uuid":
                patient_uuid = (part.get_payload(decode=False) or "").strip()
            elif field_name == "language":
                voice_language = (part.get_payload(decode=False) or "english").strip().lower()

        if not audio_data:
            self._send_json({"error": "audio field is required"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            # Write incoming audio (webm/opus from MediaRecorder) to a temp file
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f_in:
                f_in.write(audio_data)
                in_path = f_in.name
            out_path = in_path.replace(".webm", ".wav")

            # Convert to 16kHz mono PCM WAV (required by Gemma's conformer encoder)
            ffmpeg_result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", in_path,
                    "-ar", "16000",
                    "-ac", "1",
                    "-f", "wav",
                    out_path,
                ],
                capture_output=True,
                timeout=30,
            )
            os.unlink(in_path)

            if ffmpeg_result.returncode != 0:
                err = ffmpeg_result.stderr.decode("utf-8", errors="replace")[:400]
                self._send_json({"error": f"ffmpeg conversion failed: {err}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            with open(out_path, "rb") as f_wav:
                wav_bytes = f_wav.read()
            os.unlink(out_path)

            if len(wav_bytes) < 100:
                self._send_json({"error": "Audio too short after conversion"}, HTTPStatus.BAD_REQUEST)
                return

            audio_b64 = base64.b64encode(wav_bytes).decode("ascii")

            # Optional patient context
            patient_summary: str | None = None
            if patient_uuid:
                try:
                    openmrs = OpenMrsClient(
                        self.settings,
                        authorization=self.headers.get("Authorization"),
                        cookie=self.headers.get("Cookie"),
                    )
                    ctx = openmrs.build_patient_context(patient_uuid)
                    patient_summary = ctx.to_kb_query()
                except Exception:
                    pass

            # Build scribe system prompt (same as text scribe)

            background_block = (
                f"[BACKGROUND — DO NOT EXTRACT FROM THIS — FOR CONTEXT ONLY]:\n{patient_summary}\n\n"
                if patient_summary else ""
            )
            if voice_language == "amharic":
                lang_instruction = (
                    "The audio recording is in Amharic (አማርኛ). "
                    "Listen to the Amharic speech, translate it to English internally, "
                    "then extract the SOAP note and clinical data from the translation. "
                    "Output English JSON only. "
                )
            else:
                lang_instruction = (
                    "Listen carefully. Extract ONLY what is explicitly stated in the audio. "
                )
            user_text = (
                background_block +
                "[AUDIO TO SCRIBE — EXTRACT ONLY FROM THIS RECORDING]:\n" +
                lang_instruction +
                "If only one measurement is mentioned, all other arrays must be empty. "
                "Do NOT use the background context as content to extract. "
                "Return JSON only."
            )

            messages = [
                {"role": "system", "content": SCRIBE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}},
                        {"type": "text", "text": user_text},
                    ],
                },
            ]

            llm = make_llm_client(self.settings)
            # Audio processing can take up to 60s for longer recordings
            response = llm.chat(messages, temperature=0.1, max_tokens=1600, timeout=90.0)
            raw = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            result = parse_scribe_response(raw)

            ciel = CielClient(self.settings)
            resolved = resolve_scribe_result(result, ciel)
            resolved["generationTrace"] = [
                {
                    "type": "model_reasoning",
                    "title": "Voice model response",
                    "detail": raw[:1800],
                    "payload": {"source": "voice"},
                    "timestamp": _utc_now_iso(),
                },
                {
                    "type": "model_summary",
                    "title": "Voice SOAP parsed",
                    "detail": "Parsed voice SOAP response and resolved extracted items against CIEL.",
                    "payload": {
                        "diagnoses": len(resolved.get("concepts") or []),
                        "observations": len(resolved.get("observations") or []),
                        "medications": len(resolved.get("medications") or []),
                    },
                    "timestamp": _utc_now_iso(),
                },
            ]

            self._send_json({
                "soap": resolved["soap"],
                "concepts": resolved["concepts"],
                "observations": resolved["observations"],
                "medications": resolved["medications"],
                "generationTrace": resolved["generationTrace"],
                "soapText": soap_to_note_text(resolved["soap"]),
            })

        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
    def _handle_scribe_process_text(self) -> None:
        body = self._read_json()
        note_text = (body.get("noteText") or body.get("note_text") or "").strip()
        patient_uuid = (body.get("patientUuid") or body.get("patient_uuid") or "").strip()
        language = (body.get("language") or "english").strip().lower()
        if not note_text:
            self._send_json({"error": "noteText is required"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            llm = make_llm_client(self.settings)

            # If Amharic: translate to English first
            original_note = note_text
            if language == "amharic":
                trans_msgs = build_translation_prompt(note_text)
                trans_resp = llm.chat(trans_msgs, temperature=0.1, max_tokens=800)
                translation = trans_resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if translation:
                    note_text = translation

            # Optional patient context
            patient_summary: str | None = None
            if patient_uuid:
                try:
                    openmrs = OpenMrsClient(
                        self.settings,
                        authorization=self.headers.get("Authorization"),
                        cookie=self.headers.get("Cookie"),
                    )
                    ctx = openmrs.build_patient_context(patient_uuid)
                    patient_summary = ctx.to_kb_query()
                except Exception:
                    pass

            ciel = CielClient(self.settings)
            try:
                resolved = SoapScribeToolLoop(llm, ciel, trace_store=_get_scribe_trace_store(self.settings)).run(note_text, patient_summary)
            except Exception as exc:
                logging.getLogger("tenaos.tena_agent.scribe").warning(
                    "SOAP tool loop failed; falling back to one-shot scribe: %s", exc
                )

                messages = build_scribe_prompt(note_text, patient_summary)
                response = llm.chat(messages, temperature=0.1, max_tokens=1600)
                raw = response.get("choices", [{}])[0].get("message", {}).get("content", "")
                result = parse_scribe_response(raw)
                resolved = resolve_scribe_result(result, ciel)
                resolved["generationTrace"] = [
                    {
                        "type": "model_reasoning",
                        "title": "One-shot fallback response",
                        "detail": raw[:1800],
                        "payload": {"fallback": True},
                        "timestamp": _utc_now_iso(),
                    },
                    {
                        "type": "model_summary",
                        "title": "Fallback SOAP parsed",
                        "detail": "The ReAct tool loop failed, so TenaAgent parsed the legacy one-shot SOAP JSON response.",
                        "payload": {"fallback": True},
                        "timestamp": _utc_now_iso(),
                    },
                ]

            self._send_json({
                "soap": resolved["soap"],
                "concepts": resolved["concepts"],
                "observations": resolved["observations"],
                "medications": resolved["medications"],
                "generationTrace": resolved.get("generationTrace") or [],
                "soapText": soap_to_note_text(resolved["soap"]),
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
    def _handle_scribe_process_text_trace(self) -> None:
        body = self._read_json()
        note_text = (body.get("noteText") or body.get("note_text") or "").strip()
        patient_uuid = (body.get("patientUuid") or body.get("patient_uuid") or "").strip()
        if not note_text:
            self._send_json({"error": "noteText is required"}, HTTPStatus.BAD_REQUEST)
            return
        _evict_old_traces(SCRIBE_TRACES)  # type: ignore[arg-type]
        trace = ScribeTrace(patient_uuid=patient_uuid)
        SCRIBE_TRACES[trace.trace_id] = trace
        trace.add("queued", "SOAP scribe started", "Preparing text for Gemma 4 SOAP scribe.", {"request": body})
        threading.Thread(
            target=_run_scribe_text_trace,
            args=(self.settings, trace.trace_id, self.headers.get("Authorization"), self.headers.get("Cookie"), body),
            daemon=True,
        ).start()
        self._send_json(trace.to_dict(), HTTPStatus.ACCEPTED)
    def _handle_scribe_confirm_text(self) -> None:
        body = self._read_json()
        patient_uuid = (body.get("patientUuid") or "").strip()
        visit_uuid = (body.get("visitUuid") or "").strip()
        location_uuid = (body.get("locationUuid") or "").strip()
        soap_text = (body.get("soapText") or "").strip()
        soap_sections = body.get("soap") if isinstance(body.get("soap"), dict) else {}
        concept_uuids: list[str] = [str(u) for u in (body.get("conceptUuids") or []) if u]
        # observations: [{uuid, value}]
        observations: list[dict[str, str]] = [
            {"uuid": str(o.get("uuid") or ""), "value": str(o.get("value") or "")}
            for o in (body.get("observations") or [])
            if o.get("uuid") and o.get("value") is not None
        ]
        # medications: [{uuid, doseString}] — saved as Drug-class obs, value = dosage text
        medications: list[dict[str, str]] = [
            {
                "uuid": str(m.get("uuid") or ""),
                "value": str(m.get("doseString") or m.get("label") or ""),
                "label": str(m.get("label") or ""),
                "dose": str(m.get("dose") or ""),
                "frequency": str(m.get("frequency") or ""),
                "route": str(m.get("route") or ""),
            }
            for m in (body.get("medications") or [])
            if m.get("uuid")
        ]

        encounter_datetime_override = (body.get("encounterDatetime") or "").strip()

        if not patient_uuid or not visit_uuid or not soap_text:
            self._send_json({"error": "patientUuid, visitUuid, and soapText are required"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            openmrs = OpenMrsClient(
                self.settings,
                authorization=self.headers.get("Authorization"),
                cookie=self.headers.get("Cookie"),
            )
            encounter_type = self.settings.clinical_note_encounter_type_uuid
            note_concept_uuid = self.settings.clinical_note_concept_uuid
            soap_form_uuid = self.settings.soap_note_form_uuid
            if not encounter_type or not note_concept_uuid:
                self._send_json({"error": "clinicalNoteEncounterTypeUuid or clinicalNoteConceptUuid not configured"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            obs_list: list[dict[str, Any]] = []
            using_soap_form = False
            if soap_form_uuid and soap_sections:
                soap_concepts = [
                    (self.settings.soap_subjective_concept_uuid, soap_sections.get("subjective")),
                    (self.settings.soap_objective_concept_uuid, soap_sections.get("objective")),
                    (self.settings.soap_assessment_concept_uuid, soap_sections.get("assessment")),
                    (self.settings.soap_plan_concept_uuid, soap_sections.get("plan")),
                ]
                for concept_uuid, value in soap_concepts:
                    text_value = str(value or "").strip()
                    if concept_uuid and text_value and text_value.lower() != "not documented":
                        obs_list.append({"concept": concept_uuid, "value": text_value})
                using_soap_form = bool(obs_list)
            if not using_soap_form:
                obs_list.append({"concept": note_concept_uuid, "value": soap_text})
            # Valued observation obs (vitals / labs)
            for obs_item in observations:
                obs_list.append({"concept": _openmrs_concept_ref(obs_item["uuid"]), "value": obs_item["value"]})

            enc_payload: dict[str, Any] = {
                "patient": patient_uuid,
                "visit": visit_uuid,
                "encounterType": encounter_type,
                "encounterDatetime": encounter_datetime_override or _utc_now_iso(),
                "obs": obs_list,
            }
            # Scribed SOAP notes should appear as submissions of the published
            # SOAP Note Template form. Plain notes are saved by the frontend's
            # quick note path and intentionally do not attach this form UUID.
            if using_soap_form and soap_form_uuid:
                enc_payload["form"] = soap_form_uuid
            if location_uuid:
                enc_payload["location"] = location_uuid

            enc_result = openmrs._post_rest("encounter", enc_payload)
            saved_conditions = 0
            for cuuid in concept_uuids:
                try:
                    openmrs._post_rest("condition", {
                        "patient": patient_uuid,
                        "condition": {"coded": _openmrs_concept_ref(cuuid)},
                        "clinicalStatus": "ACTIVE",
                        "verificationStatus": "CONFIRMED",
                    })
                    saved_conditions += 1
                except Exception as exc:
                    logging.getLogger("tenaos.tena_agent.scribe").warning(
                        "Could not save extracted diagnosis condition concept=%s error=%s", cuuid, exc
                    )
            saved_medications = 0
            for med_item in medications:
                try:
                    drug_hits = openmrs.get_rest(
                        "drug",
                        {
                            "q": med_item.get("label") or med_item["uuid"],
                            "v": "custom:(uuid,name,display,concept:(uuid,display),strength)",
                            "limit": 5,
                        },
                    ).get("results", [])
                    concept_ref = _openmrs_concept_ref(med_item["uuid"])
                    drug = next(
                        (d for d in drug_hits if (d.get("concept") or {}).get("uuid") == concept_ref),
                        drug_hits[0] if drug_hits else None,
                    )
                    if not drug:
                        raise ValueError(f"No OpenMRS drug found for {med_item.get('label') or med_item['uuid']}")
                    existing_orders = openmrs.get_rest(
                        "order",
                        {
                            "patient": patient_uuid,
                            "type": "drugorder",
                            "v": "custom:(uuid,drug:(uuid,display),dateStopped,voided)",
                            "limit": 50,
                        },
                    ).get("results", [])
                    if any(
                        (order.get("drug") or {}).get("uuid") == drug.get("uuid")
                        and not order.get("dateStopped")
                        and not order.get("voided")
                        for order in existing_orders
                    ):
                        saved_medications += 1
                        continue
                    openmrs._post_rest("order", {
                        "type": "drugorder",
                        "patient": patient_uuid,
                        "encounter": enc_result.get("uuid"),
                        "drug": drug.get("uuid"),
                        "dose": _parse_dose_amount(med_item.get("dose") or med_item.get("value") or ""),
                        "doseUnits": {"uuid": self.settings.drug_order_dose_units_uuid},
                        "frequency": {"uuid": self.settings.drug_order_frequency_uuid},
                        "route": {"uuid": self.settings.drug_order_route_uuid},
                        "quantity": 30,
                        "quantityUnits": {"uuid": self.settings.drug_order_quantity_units_uuid},
                        "numRefills": 0,
                        "orderer": self.settings.drug_order_orderer_uuid,
                        "careSetting": {"uuid": self.settings.drug_order_care_setting_uuid},
                        "orderType": {"uuid": self.settings.drug_order_type_uuid},
                        "urgency": "ROUTINE",
                        "instructions": med_item.get("value") or med_item.get("label") or None,
                    })
                    saved_medications += 1
                except Exception as exc:
                    logging.getLogger("tenaos.tena_agent.scribe").warning(
                        "Could not save extracted medication order concept=%s label=%s error=%s",
                        med_item.get("uuid"), med_item.get("label"), exc,
                    )
            self._send_json({
                "encounterUuid": enc_result.get("uuid"),
                "formUuid": soap_form_uuid,
                "saved": True,
                "obsCount": len(obs_list),
                "diagnosesCount": saved_conditions,
                "observationsCount": len(observations),
                "medicationsCount": saved_medications,
                "medicationsInPlanCount": len(medications),
            })
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logging.getLogger("tenaos.tena_agent.scribe").warning(
                "OpenMRS rejected SOAP scribe save: status=%s detail=%s", exc.code, detail[:1000]
            )
            self._send_json({"error": detail or str(exc)}, HTTPStatus.BAD_GATEWAY)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
