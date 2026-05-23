"""SQLite-backed persistence for CDS, patient material, and scribe traces.

The CDS, material, and scribe workflows are single-shot (one request -> one
response) rather than multi-turn drafts. Each run produces an event timeline
that today only lives in memory (CDS, material) or in the response payload
(scribe). Phase 0 of the SOTA roadmap needs these timelines persisted so that
Phase 1 can:

  * query historical failure modes for GEPA reflection signals,
  * build retrieval-quality audits across many runs,
  * (eventually) extract supervised training traces for LoRA.

This module is intentionally minimal: a single ``InsightTraceStore`` class
parameterised by ``workflow`` and DB path. The schema is the same shape as the
event log in [form_drafts.py](form_drafts.py) so consumers can reuse the same
queries with table-name substitution.

Three tables per DB file:

    runs                -> one row per workflow run (metadata + final status)
    events              -> append-only event log keyed by run_id
    schema_version      -> migration counter

Persistence is best-effort and never raises into the caller. A failed write is
logged and swallowed; clinical request flow takes precedence over telemetry.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import uuid4


log = logging.getLogger("tenaos.insight_traces")


Workflow = Literal["cds", "material", "scribe"]
RunStatus = Literal["in_progress", "completed", "failed", "fallback"]
EventActor = Literal["user", "gemma", "middleware", "system"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraceRun:
    run_id: str
    workflow: str
    status: RunStatus
    started_at: str
    finished_at: str | None
    summary: str | None
    context_json: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "workflow": self.workflow,
            "status": self.status,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "summary": self.summary,
            "context": self.context_json,
        }


@dataclass
class TraceEvent:
    event_id: str
    run_id: str
    timestamp: str
    actor: EventActor
    operation: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "runId": self.run_id,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "operation": self.operation,
            "detail": self.detail,
            "payload": self.payload,
        }


_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    workflow      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'in_progress',
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    summary       TEXT,
    context_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_runs_workflow ON runs(workflow);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    event_id  TEXT PRIMARY KEY,
    run_id    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    actor     TEXT NOT NULL,
    operation TEXT NOT NULL,
    detail    TEXT NOT NULL,
    payload   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(run_id, timestamp);
"""


class InsightTraceStore:
    """Thread-safe SQLite store for one workflow's trace runs.

    Instantiate one per workflow (cds, material, scribe), each pointing at its
    own DB file so backups / retention policies can be tuned independently.
    """

    def __init__(self, db_path: Path, workflow: Workflow) -> None:
        self.db_path = Path(db_path)
        self.workflow = workflow
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._enabled = True
        try:
            self._ensure_schema()
        except Exception as exc:  # pragma: no cover - defensive boot path
            log.warning("InsightTraceStore disabled (%s): %s", db_path, exc)
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)",
                (_SCHEMA_VERSION,),
            )
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

    # ---- runs ----

    def start_run(self, *, summary: str | None = None, context: dict[str, Any] | None = None) -> str:
        """Create a new run row and return its run_id. Never raises."""
        run_id = str(uuid4())
        if not self._enabled:
            return run_id
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO runs (run_id, workflow, status, started_at, summary, context_json)
                    VALUES (?, ?, 'in_progress', ?, ?, ?)
                    """,
                    (run_id, self.workflow, _utc_now(), summary, json.dumps(context or {})),
                )
                conn.commit()
        except Exception as exc:  # pragma: no cover - telemetry never blocks
            log.debug("start_run failed: %s", exc)
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus = "completed",
        summary: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return
        try:
            with self._connect() as conn:
                fields = ["status = ?", "finished_at = ?"]
                params: list[Any] = [status, _utc_now()]
                if summary is not None:
                    fields.append("summary = ?")
                    params.append(summary)
                if context is not None:
                    fields.append("context_json = ?")
                    params.append(json.dumps(context))
                params.append(run_id)
                conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE run_id = ?", params)
                conn.commit()
        except Exception as exc:  # pragma: no cover
            log.debug("finish_run failed: %s", exc)

    def get_run(self, run_id: str) -> TraceRun | None:
        if not self._enabled:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return _row_to_run(row)

    def list_runs(self, *, limit: int = 50) -> list[TraceRun]:
        if not self._enabled:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE workflow = ? ORDER BY started_at DESC LIMIT ?",
                (self.workflow, limit),
            ).fetchall()
        return [_row_to_run(row) for row in rows]

    # ---- events ----

    def append_event(
        self,
        run_id: str,
        *,
        actor: EventActor,
        operation: str,
        detail: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append one event. Best-effort: failures are logged, never raised."""
        if not self._enabled:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO events (event_id, run_id, timestamp, actor, operation, detail, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        run_id,
                        _utc_now(),
                        actor,
                        operation,
                        detail,
                        json.dumps(payload or {}, default=str),
                    ),
                )
                conn.commit()
        except Exception as exc:  # pragma: no cover
            log.debug("append_event failed (%s): %s", operation, exc)

    def list_events(self, run_id: str, *, limit: int = 500) -> list[TraceEvent]:
        if not self._enabled:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY timestamp ASC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return [_row_to_event(row) for row in rows]


def _row_to_run(row: sqlite3.Row) -> TraceRun:
    try:
        ctx = json.loads(row["context_json"] or "{}")
    except (TypeError, ValueError):
        ctx = {}
    return TraceRun(
        run_id=row["run_id"],
        workflow=row["workflow"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        summary=row["summary"],
        context_json=ctx,
    )


def _row_to_event(row: sqlite3.Row) -> TraceEvent:
    try:
        payload = json.loads(row["payload"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    return TraceEvent(
        event_id=row["event_id"],
        run_id=row["run_id"],
        timestamp=row["timestamp"],
        actor=row["actor"],
        operation=row["operation"],
        detail=row["detail"],
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Wrapper helper
#
# The existing in-memory trace objects (InsightTrace, MaterialTrace,
# ScribeTrace in models.py) expose .add(type, title, detail, payload),
# .complete(result), and .fail(message, payload). Phase 0 needs every call to
# also persist to SQLite without touching the loops' call sites.
#
# `attach_store(trace, store)` mutates the trace instance to wrap those three
# methods. The wrapper is best-effort and never raises into the loop.

_TYPE_TO_ACTOR: dict[str, EventActor] = {
    "context": "system",
    "model_reasoning": "gemma",
    "model_tool_call": "gemma",
    "model_summary": "gemma",
    "model_fallback": "system",
    "model_streaming": "gemma",
    "middleware_result": "middleware",
    "llm_error": "middleware",
    "loop_exhausted": "middleware",
    "error": "middleware",
}


def attach_store(trace: Any, store: InsightTraceStore, *, summary: str | None = None, context: dict[str, Any] | None = None) -> str:
    """Wrap an in-memory trace so every .add/complete/fail also writes to SQLite.

    Returns the new ``run_id``. Safe to call when the store is disabled (becomes a no-op).
    """
    run_id = store.start_run(summary=summary, context=context)

    original_add = getattr(trace, "add", None)
    original_complete = getattr(trace, "complete", None)
    original_fail = getattr(trace, "fail", None)

    if callable(original_add):
        def _add_with_store(type_: str, title: str, detail: str, payload: dict[str, Any] | None = None) -> None:
            original_add(type_, title, detail, payload)
            actor = _TYPE_TO_ACTOR.get(type_, "system")
            try:
                store.append_event(
                    run_id,
                    actor=actor,
                    operation=type_,
                    detail=f"{title}: {detail[:300]}" if title else (detail[:300] if detail else ""),
                    payload=payload or {},
                )
            except Exception:  # pragma: no cover
                pass
        trace.add = _add_with_store  # type: ignore[attr-defined]

    if callable(original_complete):
        def _complete_with_store(result: dict[str, Any]) -> None:
            original_complete(result)
            store.finish_run(run_id, status="completed", summary=_summarise_result(result))
        trace.complete = _complete_with_store  # type: ignore[attr-defined]

    if callable(original_fail):
        def _fail_with_store(message: str, payload: dict[str, Any] | None = None) -> None:
            original_fail(message, payload)
            store.finish_run(run_id, status="failed", summary=message[:300])
        trace.fail = _fail_with_store  # type: ignore[attr-defined]

    return run_id


def _summarise_result(result: dict[str, Any] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    for key in ("summary", "title", "status"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value[:300]
    return None


__all__ = [
    "EventActor",
    "InsightTraceStore",
    "RunStatus",
    "TraceEvent",
    "TraceRun",
    "Workflow",
    "attach_store",
]
