"""Shared helpers for route mixin modules.

These pure utilities are used by both ``app.py`` and the individual route
mixin files (``scribe_routes``, ``labs_routes``, etc.).  Keeping them here
avoids importing from ``app.py`` into the mixin modules, which would create
circular imports given that ``app.py`` imports the mixin classes.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    # OpenMRS REST date conversion expects ISO8601 long format with timezone
    # offset as +0000, not Python's default +00:00 suffix.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000%z")


def _openmrs_concept_ref(concept_id_or_uuid: str) -> str:
    from ..ciel import openmrs_uuid_for_concept_id

    raw = str(concept_id_or_uuid or "").strip()
    if not raw:
        return raw
    if raw.isdigit():
        return openmrs_uuid_for_concept_id(raw)
    return raw


def _parse_dose_amount(text: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(match.group(1)) if match else 1.0


_TRACE_MAX = 500  # keep last N completed traces in memory


def _evict_old_traces(store: dict[str, Any], max_size: int = _TRACE_MAX) -> None:
    """Drop the oldest completed/failed entries once the store exceeds max_size.

    In-flight traces are never evicted; if all entries are running the store
    is left as-is.
    """
    if len(store) <= max_size:
        return
    done = [k for k, v in store.items() if getattr(v, "status", "") in {"completed", "failed"}]
    for key in done[: len(store) - max_size]:
        store.pop(key, None)
