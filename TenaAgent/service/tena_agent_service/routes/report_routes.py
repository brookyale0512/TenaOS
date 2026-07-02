"""Report-builder HTTP handler methods.

Extracted from `app.py`; relies on attrs supplied by `TenaAgentRequestHandler`
(self.settings, self._send_json, self._read_json_body, etc.).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..ciel import CielClient
from ..config import Settings
from ..llm_backend import make_llm_client
from ..openmrs_reader import OpenmrsReader, ProgressCallback
from ..report_builder_tool_loop import ReportBuilderToolLoop
from ..report_conversation import (
    ConversationTurn as ReportConversationTurn,
    ReportConversationDriver,
)
from ..report_drafts import ReportDraft, ReportDraftNotFoundError, ReportDraftStore

_LOG = logging.getLogger("tenaos.tena_agent.reports")

_REPORT_STORE: ReportDraftStore | None = None
_REPORT_LOCKS: dict[str, threading.Lock] = {}
_REPORT_LOCKS_GUARD = threading.Lock()


def _draft_lock(draft_id: str) -> threading.Lock:
    with _REPORT_LOCKS_GUARD:
        lock = _REPORT_LOCKS.get(draft_id)
        if lock is None:
            lock = threading.Lock()
            _REPORT_LOCKS[draft_id] = lock
        return lock


def _get_report_store(settings: Settings) -> ReportDraftStore:
    global _REPORT_STORE
    if _REPORT_STORE is None:
        report_db = settings.runtime_dir / "report_drafts.sqlite3"
        _REPORT_STORE = ReportDraftStore(report_db)
    return _REPORT_STORE


def _report_draft_payload(draft: ReportDraft) -> dict[str, Any]:
    payload = draft.to_dict()
    payload["published"] = bool((draft.conversation_context or {}).get("published"))
    return payload


def _build_report_driver(
    settings: Settings, authorization: str | None, cookie: str | None
) -> ReportConversationDriver:
    store = _get_report_store(settings)
    ciel = CielClient(settings)
    llm = make_llm_client(settings)

    def reader_factory(progress: ProgressCallback | None = None) -> OpenmrsReader:
        from ..openmrs_reader import OpenmrsReader as _R
        return _R(settings, authorization=authorization, cookie=cookie, progress=progress)

    loop = ReportBuilderToolLoop(store=store, ciel=ciel, reader_factory=reader_factory)
    return ReportConversationDriver(store=store, ciel=ciel, loop=loop, llm=llm)


def _kickoff_report_conversation(
    settings: Settings, draft_id: str, authorization: str | None, cookie: str | None
) -> None:
    driver = _build_report_driver(settings, authorization, cookie)
    store = _get_report_store(settings)
    try:
        driver.kickoff(draft_id)
    except Exception as exc:
        _LOG.exception("Report kickoff failed for draft %s", draft_id)
        store.append_event(
            draft_id,
            actor="middleware",
            operation="kickoff_failed",
            detail=f"Report kickoff failed: {type(exc).__name__}: {exc}",
            payload={"error": str(exc), "traceback": traceback.format_exc()},
        )


def _run_report_conversation_turn(
    settings: Settings,
    draft_id: str,
    turn: ReportConversationTurn,
    authorization: str | None,
    cookie: str | None,
) -> None:
    driver = _build_report_driver(settings, authorization, cookie)
    store = _get_report_store(settings)
    started = time.monotonic()
    _LOG.info("report-draft=%s turn=%s start", draft_id, turn.kind if turn else "?")
    try:
        with _draft_lock(draft_id):
            driver.handle_user_turn(draft_id, turn)
        _LOG.info(
            "report-draft=%s turn=%s ok elapsed=%.2fs",
            draft_id, turn.kind, time.monotonic() - started,
        )
    except Exception as exc:
        _LOG.exception(
            "report-draft=%s turn=%s failed after %.2fs",
            draft_id, turn.kind, time.monotonic() - started,
        )
        store.append_event(
            draft_id,
            actor="middleware",
            operation="conversation_turn_failed",
            detail=f"Report turn failed: {type(exc).__name__}: {exc}",
            payload={"error": str(exc), "traceback": traceback.format_exc()},
        )


class ReportRoutesMixin:
    def _handle_list_report_drafts(self, parsed: Any) -> None:
        store = _get_report_store(self.settings)
        params = parse_qs(parsed.query)
        owner = (params.get("owner") or [None])[0]
        published_filter = (params.get("published") or [None])[0]
        drafts = store.list_drafts(owner=owner)
        out = []
        for draft in drafts:
            if draft.status == "archived":
                continue
            is_published = bool((draft.conversation_context or {}).get("published"))
            if published_filter == "true" and not is_published:
                continue
            if published_filter == "false" and is_published:
                continue
            out.append(_report_draft_payload(draft))
        self._send_json({"drafts": out})
    def _handle_get_report_draft(self, draft_id: str) -> None:
        store = _get_report_store(self.settings)
        try:
            draft = store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            self._send_json({"error": "Report draft not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(_report_draft_payload(draft))
    def _handle_get_report_result(self, draft_id: str) -> None:
        store = _get_report_store(self.settings)
        try:
            draft = store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            self._send_json({"error": "Report draft not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json({"result": draft.last_result, "lastRunAt": draft.last_run_at, "status": draft.status})
    def _handle_report_draft_events(self, draft_id: str, parsed: Any) -> None:
        store = _get_report_store(self.settings)
        try:
            store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            self._send_json({"error": "Report draft not found"}, HTTPStatus.NOT_FOUND)
            return
        if "text/event-stream" in (self.headers.get("Accept") or ""):
            self._send_report_event_stream(draft_id, store)
            return
        params = parse_qs(parsed.query)
        since = (params.get("since") or [None])[0]
        events = store.list_events(draft_id, since=since)
        self._send_json({"draftId": draft_id, "events": [event.to_dict() for event in events]})
    def _handle_create_report_draft(self) -> None:
        body = self._read_json()
        store = _get_report_store(self.settings)
        prefill_name = body.get("name") if isinstance(body.get("name"), str) else ""
        report_type = body.get("reportType") if isinstance(body.get("reportType"), str) else "count"
        if report_type not in ("count", "cohort", "indicator", "pivot"):
            report_type = "count"
        draft = store.create_draft(
            name=str(prefill_name or "Untitled report"),
            owner=body.get("owner") if isinstance(body.get("owner"), str) else None,
            description=body.get("description") if isinstance(body.get("description"), str) else None,
            report_type=report_type,  # type: ignore[arg-type]
        )
        threading.Thread(
            target=_kickoff_report_conversation,
            args=(self.settings, draft.draft_id, self.headers.get("Authorization"), self.headers.get("Cookie")),
            daemon=True,
        ).start()
        self._send_json(_report_draft_payload(store.get_draft(draft.draft_id)), HTTPStatus.ACCEPTED)
    def _handle_post_report_message(self, draft_id: str) -> None:
        body = self._read_json()
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            self._send_json({"error": "message is required"}, HTTPStatus.BAD_REQUEST)
            return
        store = _get_report_store(self.settings)
        try:
            store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            self._send_json({"error": "Report draft not found"}, HTTPStatus.NOT_FOUND)
            return
        turn = ReportConversationTurn(kind="message", message=message.strip())
        threading.Thread(
            target=_run_report_conversation_turn,
            args=(self.settings, draft_id, turn, self.headers.get("Authorization"), self.headers.get("Cookie")),
            daemon=True,
        ).start()
        self._send_json({"draftId": draft_id, "accepted": True}, HTTPStatus.ACCEPTED)
    def _handle_post_report_action(self, draft_id: str) -> None:
        body = self._read_json()
        action = body.get("action")
        if not isinstance(action, str) or not action.strip():
            self._send_json({"error": "action is required"}, HTTPStatus.BAD_REQUEST)
            return
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        store = _get_report_store(self.settings)
        try:
            draft = store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            self._send_json({"error": "Report draft not found"}, HTTPStatus.NOT_FOUND)
            return
        if action.strip() in {"publish", "unpublish"}:
            context = dict(draft.conversation_context or {})
            context["published"] = action.strip() == "publish"
            updated = store.update_draft(draft_id, conversation_context=context)
            store.append_event(
                draft_id,
                actor="user",
                operation=f"report_{action.strip()}",
                detail="Report published." if context["published"] else "Report unpublished.",
                payload={"published": context["published"]},
            )
            self._send_json(_report_draft_payload(updated))
            return
        turn = ReportConversationTurn(kind="action", action=action.strip(), payload=payload)
        threading.Thread(
            target=_run_report_conversation_turn,
            args=(self.settings, draft_id, turn, self.headers.get("Authorization"), self.headers.get("Cookie")),
            daemon=True,
        ).start()
        self._send_json({"draftId": draft_id, "accepted": True}, HTTPStatus.ACCEPTED)
    def _handle_apply_report_operations(self, draft_id: str) -> None:
        body = self._read_json()
        operations = body.get("operations")
        if not isinstance(operations, list):
            self._send_json({"error": "operations must be an array"}, HTTPStatus.BAD_REQUEST)
            return
        store = _get_report_store(self.settings)
        try:
            store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            self._send_json({"error": "Report draft not found"}, HTTPStatus.NOT_FOUND)
            return
        loop = self._build_report_tool_loop()
        with _draft_lock(draft_id):
            update_result = loop.update_report_draft(draft_id, operations, actor="user")
            build_result = loop.build_report_query(draft_id)
        self._send_json({"update": update_result, "build": build_result})
    def _handle_run_report(self, draft_id: str) -> None:
        store = _get_report_store(self.settings)
        try:
            store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            self._send_json({"error": "Report draft not found"}, HTTPStatus.NOT_FOUND)
            return
        loop = self._build_report_tool_loop()
        with _draft_lock(draft_id):
            # Compile first for a user-visible validation payload, then run
            # through run_report's own fresh compile guard.
            build_result = loop.build_report_query(draft_id)
            run_result = loop.run_report(draft_id) if build_result.get("compiled") else {
                "success": False,
                "error": "Query has validation errors; cannot run.",
                "validation": build_result.get("validation"),
            }
        self._send_json({"build": build_result, "run": run_result})
    def _handle_delete_report_draft(self, draft_id: str) -> None:
        store = _get_report_store(self.settings)
        try:
            store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            self._send_json({"error": "Report draft not found"}, HTTPStatus.NOT_FOUND)
            return
        updated = store.update_draft(draft_id, status="archived")
        store.append_event(
            draft_id,
            actor="user",
            operation="delete_report_draft",
            detail="Report archived.",
            payload={"status": "archived"},
        )
        self._send_json(_report_draft_payload(updated))
