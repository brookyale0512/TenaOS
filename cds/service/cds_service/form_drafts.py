"""SQLite-backed storage for form drafts + an append-only event log.

Two tables:
    form_drafts        -> current state of every draft (basket + last schema)
    form_draft_events  -> append-only audit log; one row per basket mutation
                          or publish attempt, with actor and full payload

The event log is the source of truth for "what happened in this draft" — it
records every tool call, every user follow-up message, every middleware
rebuild, and every publish attempt. The form_drafts row is a denormalised
projection of the latest state for fast reads from the API.

The schema is created on `ensure_schema()` and is idempotent so the same DB
file can be carried across restarts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


DraftStatus = Literal["draft", "publishing", "published", "failed", "archived"]
EventActor = Literal["user", "gemma", "middleware", "system"]
ConversationState = Literal[
    "awaiting_name",
    "awaiting_encounter_type",
    "awaiting_question",
    "awaiting_candidate_pick",
    "awaiting_set_decision",
    "publishing",
    "published",
]


@dataclass
class FormDraftEvent:
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
class FormDraft:
    draft_id: str
    owner: str | None
    status: DraftStatus
    name: str
    version: str
    description: str | None
    encounter_type_uuid: str | None
    basket: dict[str, Any]
    last_schema: dict[str, Any] | None
    last_validation: dict[str, Any] | None
    published_form_uuid: str | None
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
            "version": self.version,
            "description": self.description,
            "encounterTypeUuid": self.encounter_type_uuid,
            "basket": self.basket,
            "lastSchema": self.last_schema,
            "lastValidation": self.last_validation,
            "publishedFormUuid": self.published_form_uuid,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "conversationState": self.conversation_state,
            "conversationContext": self.conversation_context,
        }


class DraftNotFoundError(KeyError):
    pass


# Current schema version. Bump this integer whenever a new migration is added.
_SCHEMA_VERSION = 3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS form_drafts (
    draft_id              TEXT PRIMARY KEY,
    owner                 TEXT,
    status                TEXT NOT NULL DEFAULT 'draft',
    name                  TEXT NOT NULL,
    version               TEXT NOT NULL DEFAULT '1.0.0',
    description           TEXT,
    encounter_type_uuid   TEXT,
    basket_json           TEXT NOT NULL DEFAULT '{}',
    last_schema_json      TEXT,
    last_validation_json  TEXT,
    published_form_uuid   TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    conversation_state    TEXT NOT NULL DEFAULT 'awaiting_name',
    conversation_context  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_form_drafts_status ON form_drafts(status);
CREATE INDEX IF NOT EXISTS idx_form_drafts_owner ON form_drafts(owner);

CREATE TABLE IF NOT EXISTS form_draft_events (
    event_id   TEXT PRIMARY KEY,
    draft_id   TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    actor      TEXT NOT NULL,
    operation  TEXT NOT NULL,
    detail     TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (draft_id) REFERENCES form_drafts(draft_id)
);

CREATE INDEX IF NOT EXISTS idx_form_draft_events_draft ON form_draft_events(draft_id);
CREATE INDEX IF NOT EXISTS idx_form_draft_events_ts ON form_draft_events(draft_id, timestamp);
"""

# Ordered list of (version_number, SQL_statement) migrations.
# Each entry runs exactly once when the DB version is below it.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE form_drafts ADD COLUMN conversation_state TEXT NOT NULL DEFAULT 'awaiting_name'"),
    (2, "ALTER TABLE form_drafts ADD COLUMN conversation_context TEXT NOT NULL DEFAULT '{}'"),
    # v3: schema_version table added (no DDL change needed — created in _SCHEMA_SQL above).
    (3, "SELECT 1"),
]


class FormDraftStore:
    """Thread-safe SQLite store for form drafts and their event log.

    A single store instance is shared across all request handlers; per-call
    connections are opened inside a thread lock to avoid the "SQLite objects
    created in a thread can only be used in that same thread" error under
    the existing `ThreadingHTTPServer`.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            self._run_migrations(conn)
            conn.commit()

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply any pending migrations and advance the version counter."""
        conn.execute("INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0)")
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        current = int(row["version"]) if row else 0
        for version, sql in _MIGRATIONS:
            if version <= current:
                continue
            try:
                conn.execute(sql)
            except Exception:
                pass  # column already exists (pre-migration DB) — safe to skip
            conn.execute("UPDATE schema_version SET version = ? WHERE id = 1", (version,))
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)",
            (_SCHEMA_VERSION,),
        )

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
        owner: str | None,
        description: str | None,
        encounter_type_uuid: str | None,
        version: str = "1.0.0",
        basket: dict[str, Any] | None = None,
    ) -> FormDraft:
        draft_id = str(uuid4())
        now = _utc_now()
        draft = FormDraft(
            draft_id=draft_id,
            owner=owner,
            status="draft",
            name=name,
            version=version,
            description=description,
            encounter_type_uuid=encounter_type_uuid,
            basket=basket or {"sections": []},
            last_schema=None,
            last_validation=None,
            published_form_uuid=None,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO form_drafts (
                    draft_id, owner, status, name, version, description,
                    encounter_type_uuid, basket_json, last_schema_json,
                    last_validation_json, published_form_uuid, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    draft_id,
                    owner,
                    draft.status,
                    name,
                    version,
                    description,
                    encounter_type_uuid,
                    json.dumps(draft.basket),
                    now,
                    now,
                ),
            )
            conn.commit()
        self.append_event(
            draft_id,
            actor="system",
            operation="create_draft",
            detail=f"Created form draft '{name}'",
            payload={
                "owner": owner,
                "encounterTypeUuid": encounter_type_uuid,
                "description": description,
            },
        )
        return draft

    def get_draft(self, draft_id: str) -> FormDraft:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM form_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
        if not row:
            raise DraftNotFoundError(draft_id)
        return _row_to_draft(row)

    def list_drafts(self, *, owner: str | None = None, limit: int = 50) -> list[FormDraft]:
        with self._connect() as conn:
            if owner is None:
                rows = conn.execute(
                    "SELECT * FROM form_drafts ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM form_drafts WHERE owner = ? ORDER BY updated_at DESC LIMIT ?",
                    (owner, limit),
                ).fetchall()
        return [_row_to_draft(row) for row in rows]

    def update_draft(
        self,
        draft_id: str,
        *,
        basket: dict[str, Any] | None = None,
        last_schema: dict[str, Any] | None = None,
        last_validation: dict[str, Any] | None = None,
        name: str | None = None,
        version: str | None = None,
        description: str | None = None,
        encounter_type_uuid: str | None = None,
        status: DraftStatus | None = None,
        published_form_uuid: str | None = None,
        conversation_state: ConversationState | None = None,
        conversation_context: dict[str, Any] | None = None,
    ) -> FormDraft:
        existing = self.get_draft(draft_id)
        updated = FormDraft(
            draft_id=existing.draft_id,
            owner=existing.owner,
            status=status or existing.status,
            name=name or existing.name,
            version=version or existing.version,
            description=description if description is not None else existing.description,
            encounter_type_uuid=encounter_type_uuid if encounter_type_uuid is not None else existing.encounter_type_uuid,
            basket=basket if basket is not None else existing.basket,
            last_schema=last_schema if last_schema is not None else existing.last_schema,
            last_validation=last_validation if last_validation is not None else existing.last_validation,
            published_form_uuid=published_form_uuid if published_form_uuid is not None else existing.published_form_uuid,
            created_at=existing.created_at,
            updated_at=_utc_now(),
            conversation_state=conversation_state or existing.conversation_state,
            conversation_context=conversation_context if conversation_context is not None else existing.conversation_context,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE form_drafts SET
                    status = ?,
                    name = ?,
                    version = ?,
                    description = ?,
                    encounter_type_uuid = ?,
                    basket_json = ?,
                    last_schema_json = ?,
                    last_validation_json = ?,
                    published_form_uuid = ?,
                    updated_at = ?,
                    conversation_state = ?,
                    conversation_context = ?
                WHERE draft_id = ?
                """,
                (
                    updated.status,
                    updated.name,
                    updated.version,
                    updated.description,
                    updated.encounter_type_uuid,
                    json.dumps(updated.basket),
                    json.dumps(updated.last_schema) if updated.last_schema is not None else None,
                    json.dumps(updated.last_validation) if updated.last_validation is not None else None,
                    updated.published_form_uuid,
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
    ) -> FormDraftEvent:
        event = FormDraftEvent(
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
                INSERT INTO form_draft_events (event_id, draft_id, timestamp, actor, operation, detail, payload)
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

    def list_events(self, draft_id: str, *, since: str | None = None, limit: int = 200) -> list[FormDraftEvent]:
        with self._connect() as conn:
            if since is None:
                rows = conn.execute(
                    "SELECT * FROM form_draft_events WHERE draft_id = ? ORDER BY timestamp ASC LIMIT ?",
                    (draft_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM form_draft_events WHERE draft_id = ? AND timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                    (draft_id, since, limit),
                ).fetchall()
        return [_row_to_event(row) for row in rows]

    def latest_event_timestamp(self, draft_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) AS ts FROM form_draft_events WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
        return row["ts"] if row and row["ts"] else None


def _row_to_draft(row: sqlite3.Row) -> FormDraft:
    keys = set(row.keys())
    conversation_state = row["conversation_state"] if "conversation_state" in keys else "awaiting_name"
    raw_context = row["conversation_context"] if "conversation_context" in keys else None
    try:
        context = json.loads(raw_context) if raw_context else {}
    except (TypeError, ValueError):
        context = {}
    return FormDraft(
        draft_id=row["draft_id"],
        owner=row["owner"],
        status=row["status"],
        name=row["name"],
        version=row["version"],
        description=row["description"],
        encounter_type_uuid=row["encounter_type_uuid"],
        basket=json.loads(row["basket_json"] or "{}"),
        last_schema=json.loads(row["last_schema_json"]) if row["last_schema_json"] else None,
        last_validation=json.loads(row["last_validation_json"]) if row["last_validation_json"] else None,
        published_form_uuid=row["published_form_uuid"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        conversation_state=conversation_state,
        conversation_context=context,
    )


def _row_to_event(row: sqlite3.Row) -> FormDraftEvent:
    return FormDraftEvent(
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
    "DraftNotFoundError",
    "EventActor",
    "FormDraft",
    "FormDraftEvent",
    "FormDraftStore",
]
