"""SQLite-backed storage for report drafts + an append-only event log.

Mirrors `form_drafts.py` but on the READ side. Each report draft holds:
    - a `report_type` (count | cohort | indicator | pivot)
    - a `spec` dict — the agent-mutable query plan
    - a `last_query` dict — the deterministic compiled FHIR query plan
    - a `last_result` dict — the most recent execution snapshot
    - a `conversation_state` machine: awaiting_name -> awaiting_question -> ready
      (we deliberately collapsed `awaiting_report_type` into the
      brainstorm-driven plan; the report type is determined by the agent's
      plan rather than a separate picker turn.)

The event log captures every tool call, every user turn, every compile and
every run, so the SSE stream is the single source of truth for the chat
reasoning toggle.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


ReportStatus = Literal["draft", "running", "ready", "failed", "archived"]
ReportType = Literal["count", "cohort", "indicator", "pivot"]
EventActor = Literal["user", "gemma", "middleware", "system"]
ConversationState = Literal[
    "awaiting_name",
    "awaiting_question",
    "ready",
]


@dataclass
class ReportDraftEvent:
    event_id: str
    draft_id: str
    timestamp: str
    actor: EventActor
    operation: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "draftId": self.draft_id,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "operation": self.operation,
            "detail": self.detail,
            "payload": self.payload,
        }


@dataclass
class ReportDraft:
    draft_id: str
    owner: str | None
    status: ReportStatus
    name: str
    description: str | None
    report_type: ReportType
    spec: dict[str, Any]
    last_query: dict[str, Any] | None
    last_result: dict[str, Any] | None
    last_run_at: str | None
    created_at: str
    updated_at: str
    conversation_state: ConversationState = "awaiting_name"
    conversation_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "draftId": self.draft_id,
            "owner": self.owner,
            "status": self.status,
            "name": self.name,
            "description": self.description,
            "reportType": self.report_type,
            "spec": self.spec,
            "lastQuery": self.last_query,
            "lastResult": self.last_result,
            "lastRunAt": self.last_run_at,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "conversationState": self.conversation_state,
            "conversationContext": self.conversation_context,
        }


class ReportDraftNotFoundError(KeyError):
    pass


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS report_drafts (
    draft_id              TEXT PRIMARY KEY,
    owner                 TEXT,
    status                TEXT NOT NULL DEFAULT 'draft',
    name                  TEXT NOT NULL,
    description           TEXT,
    report_type           TEXT NOT NULL DEFAULT 'count',
    spec_json             TEXT NOT NULL DEFAULT '{}',
    last_query_json       TEXT,
    last_result_json      TEXT,
    last_run_at           TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    conversation_state    TEXT NOT NULL DEFAULT 'awaiting_name',
    conversation_context  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_report_drafts_status ON report_drafts(status);
CREATE INDEX IF NOT EXISTS idx_report_drafts_owner ON report_drafts(owner);

CREATE TABLE IF NOT EXISTS report_draft_events (
    event_id   TEXT PRIMARY KEY,
    draft_id   TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    actor      TEXT NOT NULL,
    operation  TEXT NOT NULL,
    detail     TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (draft_id) REFERENCES report_drafts(draft_id)
);

CREATE INDEX IF NOT EXISTS idx_report_draft_events_draft ON report_draft_events(draft_id);
CREATE INDEX IF NOT EXISTS idx_report_draft_events_ts ON report_draft_events(draft_id, timestamp);
"""


_DEFAULT_SPEC: dict[str, Any] = {
    "reportType": "count",
    "dateFrom": None,
    "dateTo": None,
    "dateRangeLabel": None,
    "filters": [],
    "joinMode": "and",
    "denominator": None,
    "groupBy": [],
}


class ReportDraftStore:
    """Thread-safe SQLite store for report drafts and their event log."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    # ---- Draft CRUD ----

    def create_draft(
        self,
        *,
        name: str,
        owner: str | None = None,
        description: str | None = None,
        report_type: ReportType = "count",
        spec: dict[str, Any] | None = None,
    ) -> ReportDraft:
        draft_id = str(uuid4())
        now = _utc_now()
        merged_spec = {**_DEFAULT_SPEC, "reportType": report_type, **(spec or {})}
        draft = ReportDraft(
            draft_id=draft_id,
            owner=owner,
            status="draft",
            name=name,
            description=description,
            report_type=report_type,
            spec=merged_spec,
            last_query=None,
            last_result=None,
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO report_drafts (
                    draft_id, owner, status, name, description, report_type,
                    spec_json, last_query_json, last_result_json, last_run_at,
                    created_at, updated_at, conversation_state, conversation_context
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    owner,
                    draft.status,
                    name,
                    description,
                    report_type,
                    json.dumps(merged_spec),
                    now,
                    now,
                    draft.conversation_state,
                    json.dumps(draft.conversation_context),
                ),
            )
            conn.commit()
        self.append_event(
            draft_id,
            actor="system",
            operation="create_report_draft",
            detail=f"Created report draft '{name}'",
            payload={"owner": owner, "reportType": report_type, "description": description},
        )
        return draft

    def get_draft(self, draft_id: str) -> ReportDraft:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM report_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
        if not row:
            raise ReportDraftNotFoundError(draft_id)
        return _row_to_draft(row)

    def list_drafts(self, *, owner: str | None = None, limit: int = 50) -> list[ReportDraft]:
        with self._connect() as conn:
            if owner is None:
                rows = conn.execute(
                    "SELECT * FROM report_drafts ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM report_drafts WHERE owner = ? ORDER BY updated_at DESC LIMIT ?",
                    (owner, limit),
                ).fetchall()
        return [_row_to_draft(row) for row in rows]

    def update_draft(
        self,
        draft_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        report_type: ReportType | None = None,
        spec: dict[str, Any] | None = None,
        last_query: dict[str, Any] | None = None,
        last_result: dict[str, Any] | None = None,
        last_run_at: str | None = None,
        status: ReportStatus | None = None,
        conversation_state: ConversationState | None = None,
        conversation_context: dict[str, Any] | None = None,
    ) -> ReportDraft:
        existing = self.get_draft(draft_id)
        updated = ReportDraft(
            draft_id=existing.draft_id,
            owner=existing.owner,
            status=status or existing.status,
            name=name if name is not None else existing.name,
            description=description if description is not None else existing.description,
            report_type=report_type if report_type is not None else existing.report_type,
            spec=spec if spec is not None else existing.spec,
            last_query=last_query if last_query is not None else existing.last_query,
            last_result=last_result if last_result is not None else existing.last_result,
            last_run_at=last_run_at if last_run_at is not None else existing.last_run_at,
            created_at=existing.created_at,
            updated_at=_utc_now(),
            conversation_state=conversation_state or existing.conversation_state,
            conversation_context=conversation_context if conversation_context is not None else existing.conversation_context,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE report_drafts SET
                    status = ?,
                    name = ?,
                    description = ?,
                    report_type = ?,
                    spec_json = ?,
                    last_query_json = ?,
                    last_result_json = ?,
                    last_run_at = ?,
                    updated_at = ?,
                    conversation_state = ?,
                    conversation_context = ?
                WHERE draft_id = ?
                """,
                (
                    updated.status,
                    updated.name,
                    updated.description,
                    updated.report_type,
                    json.dumps(updated.spec),
                    json.dumps(updated.last_query) if updated.last_query is not None else None,
                    json.dumps(updated.last_result) if updated.last_result is not None else None,
                    updated.last_run_at,
                    updated.updated_at,
                    updated.conversation_state,
                    json.dumps(updated.conversation_context),
                    draft_id,
                ),
            )
            conn.commit()
        return updated

    # ---- Event log ----

    def append_event(
        self,
        draft_id: str,
        *,
        actor: EventActor,
        operation: str,
        detail: str,
        payload: dict[str, Any] | None = None,
    ) -> ReportDraftEvent:
        event = ReportDraftEvent(
            event_id=str(uuid4()),
            draft_id=draft_id,
            timestamp=_utc_now(),
            actor=actor,
            operation=operation,
            detail=detail,
            payload=payload or {},
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO report_draft_events (event_id, draft_id, timestamp, actor, operation, detail, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    draft_id,
                    event.timestamp,
                    actor,
                    operation,
                    detail,
                    json.dumps(event.payload),
                ),
            )
            conn.commit()
        return event

    def list_events(self, draft_id: str, *, since: str | None = None, limit: int = 500) -> list[ReportDraftEvent]:
        with self._connect() as conn:
            if since is None:
                rows = conn.execute(
                    "SELECT * FROM report_draft_events WHERE draft_id = ? ORDER BY timestamp ASC LIMIT ?",
                    (draft_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM report_draft_events WHERE draft_id = ? AND timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                    (draft_id, since, limit),
                ).fetchall()
        return [_row_to_event(row) for row in rows]


def _row_to_draft(row: sqlite3.Row) -> ReportDraft:
    keys = set(row.keys())
    raw_context = row["conversation_context"] if "conversation_context" in keys else None
    try:
        context = json.loads(raw_context) if raw_context else {}
    except (TypeError, ValueError):
        context = {}
    return ReportDraft(
        draft_id=row["draft_id"],
        owner=row["owner"],
        status=row["status"],
        name=row["name"],
        description=row["description"],
        report_type=row["report_type"],
        spec=json.loads(row["spec_json"] or "{}"),
        last_query=json.loads(row["last_query_json"]) if row["last_query_json"] else None,
        last_result=json.loads(row["last_result_json"]) if row["last_result_json"] else None,
        last_run_at=row["last_run_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        conversation_state=row["conversation_state"] if "conversation_state" in keys else "awaiting_name",
        conversation_context=context,
    )


def _row_to_event(row: sqlite3.Row) -> ReportDraftEvent:
    return ReportDraftEvent(
        event_id=row["event_id"],
        draft_id=row["draft_id"],
        timestamp=row["timestamp"],
        actor=row["actor"],
        operation=row["operation"],
        detail=row["detail"],
        payload=json.loads(row["payload"] or "{}"),
    )


__all__ = [
    "ConversationState",
    "EventActor",
    "ReportDraft",
    "ReportDraftEvent",
    "ReportDraftNotFoundError",
    "ReportDraftStore",
    "ReportStatus",
    "ReportType",
]
