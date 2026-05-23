from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraceEvent:
    type: str
    title: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InsightTrace:
    patient_uuid: str
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    status: Literal["running", "completed", "failed"] = "running"
    events: list[TraceEvent] = field(default_factory=list)
    structured_cds: dict[str, Any] | None = None
    created_at: str = field(default_factory=utc_now)
    completed_at: str | None = None

    def add(self, type_: str, title: str, detail: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append(TraceEvent(type=type_, title=title, detail=detail, payload=payload or {}))

    def complete(self, structured_cds: dict[str, Any]) -> None:
        self.status = "completed"
        self.structured_cds = structured_cds
        self.completed_at = utc_now()

    def fail(self, message: str, payload: dict[str, Any] | None = None) -> None:
        self.status = "failed"
        self.completed_at = utc_now()
        self.add("error", "Insight failed", message, payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "patientUuid": self.patient_uuid,
            "status": self.status,
            "createdAt": self.created_at,
            "completedAt": self.completed_at,
            "events": [event.to_dict() for event in self.events],
            "structuredCds": self.structured_cds,
        }


@dataclass
class MaterialTrace:
    """Trace for patient education material generation — mirrors InsightTrace."""
    patient_uuid: str
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    status: Literal["running", "completed", "failed"] = "running"
    events: list[TraceEvent] = field(default_factory=list)
    material: dict[str, Any] | None = None  # {title, content, kbHits}
    created_at: str = field(default_factory=utc_now)
    completed_at: str | None = None

    def add(self, type_: str, title: str, detail: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append(TraceEvent(type=type_, title=title, detail=detail, payload=payload or {}))

    def complete(self, material: dict[str, Any]) -> None:
        self.status = "completed"
        self.material = material
        self.completed_at = utc_now()

    def fail(self, message: str, payload: dict[str, Any] | None = None) -> None:
        self.status = "failed"
        self.completed_at = utc_now()
        self.add("error", "Material generation failed", message, payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "patientUuid": self.patient_uuid,
            "status": self.status,
            "createdAt": self.created_at,
            "completedAt": self.completed_at,
            "events": [event.to_dict() for event in self.events],
            "material": self.material,
        }


@dataclass
class ScribeTrace:
    """Trace for SOAP Text Scribe generation."""
    patient_uuid: str
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    status: Literal["running", "completed", "failed"] = "running"
    events: list[TraceEvent] = field(default_factory=list)
    result: dict[str, Any] | None = None
    created_at: str = field(default_factory=utc_now)
    completed_at: str | None = None

    def add(self, type_: str, title: str, detail: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append(TraceEvent(type=type_, title=title, detail=detail, payload=payload or {}))

    def complete(self, result: dict[str, Any]) -> None:
        self.status = "completed"
        self.result = result
        self.completed_at = utc_now()

    def fail(self, message: str, payload: dict[str, Any] | None = None) -> None:
        self.status = "failed"
        self.completed_at = utc_now()
        self.add("error", "Scribe failed", message, payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "patientUuid": self.patient_uuid,
            "status": self.status,
            "createdAt": self.created_at,
            "completedAt": self.completed_at,
            "events": [event.to_dict() for event in self.events],
            "result": self.result,
        }
