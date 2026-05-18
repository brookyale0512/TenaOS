"""Lab Catalog — SQLite-backed store of administrator-configured lab tests.

Admin uses natural language ("add complete blood count") → agent finds the
CIEL concept → stores in catalog with optional reference ranges.

Reference ranges are stored in catalog (not fetched live) because:
- Only ~20% of CIEL concepts have ranges
- Ranges are population/age/sex dependent — admin can set clinical context
- Avoids per-result OpenMRS API calls at display time
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

log = logging.getLogger("tenaos.cds.lab_catalog")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LabCatalogEntry:
    concept_id: str         # CIEL numeric concept ID
    concept_uuid: str       # OpenMRS padded UUID
    display_name: str
    category: str           # "Hematology", "Chemistry", etc.
    units: str | None = None
    low_normal: float | None = None
    hi_normal: float | None = None
    low_critical: float | None = None
    hi_critical: float | None = None
    order_: int = 0
    uuid: str = field(default_factory=lambda: str(uuid4()))
    added_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "conceptId": self.concept_id,
            "conceptUuid": self.concept_uuid,
            "displayName": self.display_name,
            "category": self.category,
            "units": self.units,
            "lowNormal": self.low_normal,
            "hiNormal": self.hi_normal,
            "lowCritical": self.low_critical,
            "hiCritical": self.hi_critical,
            "order": self.order_,
            "addedAt": self.added_at,
        }


# ---------------------------------------------------------------------------
# Category auto-assignment
# ---------------------------------------------------------------------------

_CATEGORY_PATTERNS: list[tuple[str, list[str]]] = [
    ("Hematology", ["blood count", "cbc", "hemoglobin", "haemoglobin", "hematocrit",
                    "haematocrit", "platelet", "wbc", "rbc", "leukocyte", "erythrocyte",
                    "lymphocyte", "neutrophil", "eosinophil", "basophil", "monocyte",
                    "mcv", "mch", "mchc", "rdw", "esr", "sedimentation", "coagulation",
                    "prothrombin", "aptt", "inr", "fibrinogen"]),
    ("HIV/TB", ["cd4", "cd8", "viral load", "hiv", "tb ", "tuberculosis", "genexpert",
                "sputum", "ziehl", "afb", "acid fast", "tpt", "isoniazid preventive"]),
    ("Chemistry", ["glucose", "creatinine", "urea", "bun", "uric acid", "electrolyte",
                   "sodium", "potassium", "chloride", "bicarbonate", "calcium", "magnesium",
                   "phosphate", "albumin", "protein", "bilirubin", "alt", "ast", "alp",
                   "ggt", "ldh", "amylase", "lipase", "cholesterol", "triglyceride",
                   "hdl", "ldl", "lactate", "ammonia", "iron", "ferritin", "transferrin"]),
    ("Urinalysis", ["urine", "urinalysis", "dipstick", "urine culture", "urinary"]),
    ("Microbiology", ["culture", "sensitivity", "gram stain", "malaria", "blood culture",
                      "wound culture", "csf", "cerebrospinal", "stool culture"]),
    ("Hormones", ["tsh", "t3", "t4", "thyroid", "cortisol", "insulin", "hba1c",
                  "beta hcg", "hcg", "prolactin", "testosterone", "estrogen", "progesterone"]),
    ("Serology", ["hepatitis", "hbsag", "hcv", "syphilis", "vdrl", "rpr", "torch",
                  "toxoplasma", "rubella", "cmv", "herpes", "dengue", "malaria antigen",
                  "widal", "brucella", "leptospira", "covid"]),
    ("Imaging", ["x-ray", "xray", "ultrasound", "ct scan", "mri", "ecg", "echo"]),
]

_DEFAULT_CATEGORY = "Other"


def infer_category(display_name: str) -> str:
    name_lower = display_name.lower()
    for category, keywords in _CATEGORY_PATTERNS:
        if any(kw in name_lower for kw in keywords):
            return category
    return _DEFAULT_CATEGORY


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lab_catalog (
    uuid        TEXT PRIMARY KEY,
    concept_id  TEXT NOT NULL,
    concept_uuid TEXT NOT NULL,
    display_name TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'Other',
    units       TEXT,
    low_normal  REAL,
    hi_normal   REAL,
    low_critical REAL,
    hi_critical  REAL,
    order_      INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lab_catalog_concept_id ON lab_catalog(concept_id);
"""


class LabCatalogStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def list_all(self) -> list[LabCatalogEntry]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lab_catalog ORDER BY category, order_, display_name"
            ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def list_grouped(self) -> dict[str, list[dict[str, Any]]]:
        entries = self.list_all()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for e in entries:
            grouped.setdefault(e.category, []).append(e.to_dict())
        return grouped

    def add(self, entry: LabCatalogEntry) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO lab_catalog
                   (uuid, concept_id, concept_uuid, display_name, category,
                    units, low_normal, hi_normal, low_critical, hi_critical,
                    order_, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    entry.uuid, entry.concept_id, entry.concept_uuid,
                    entry.display_name, entry.category,
                    entry.units, entry.low_normal, entry.hi_normal,
                    entry.low_critical, entry.hi_critical,
                    entry.order_, entry.added_at,
                ),
            )

    def remove(self, uuid: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM lab_catalog WHERE uuid = ?", (uuid,))
            return cursor.rowcount > 0

    def exists_by_concept_id(self, concept_id: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM lab_catalog WHERE concept_id = ?", (concept_id,)
            ).fetchone()
            return row is not None


def _row_to_entry(row: sqlite3.Row) -> LabCatalogEntry:
    return LabCatalogEntry(
        uuid=row["uuid"],
        concept_id=row["concept_id"],
        concept_uuid=row["concept_uuid"],
        display_name=row["display_name"],
        category=row["category"],
        units=row["units"],
        low_normal=row["low_normal"],
        hi_normal=row["hi_normal"],
        low_critical=row["low_critical"],
        hi_critical=row["hi_critical"],
        order_=row["order_"],
        added_at=row["added_at"],
    )


# ---------------------------------------------------------------------------
# Agent — natural language → CIEL → catalog entry
# ---------------------------------------------------------------------------

def concept_id_to_openmrs_uuid(concept_id: str) -> str:
    """CIEL padded UUID convention: concept_id + 'A' * (36 - len(concept_id))"""
    raw = str(concept_id).strip()
    return raw + ("A" * max(0, 36 - len(raw)))


def add_lab_test_from_description(
    description: str,
    store: LabCatalogStore,
    ciel_client: Any,
) -> dict[str, Any]:
    """
    Find the best CIEL concept for the description and add it to the catalog.
    Returns:
      {"status": "added", "entry": {...}}
      {"status": "candidates", "candidates": [...]}  if ambiguous
      {"status": "already_exists", "entry": {...}}
      {"status": "not_found"}
    """
    hits = ciel_client.search_concepts(
        description,
        concept_classes=["Test", "LabSet"],
        limit=5,
    )

    if not hits:
        return {"status": "not_found", "description": description}

    # Check top hit confidence — if score is much higher, auto-add
    top = hits[0]
    top_name_lower = top.display_name.lower()
    desc_lower = description.lower()

    # Check if already in catalog
    if store.exists_by_concept_id(top.concept_id):
        existing = [e for e in store.list_all() if e.concept_id == top.concept_id]
        return {"status": "already_exists", "entry": existing[0].to_dict() if existing else {}}

    # Try to get reference ranges from CIEL extras
    low_normal = hi_normal = low_critical = hi_critical = units = None
    try:
        bundle = ciel_client.get_concept_bundle(top.concept_id)
        concept = bundle.get("concept", {})
        extras = concept.get("extras") or {}
        low_normal = _safe_float(extras.get("low_normal"))
        hi_normal = _safe_float(extras.get("hi_normal"))
        low_critical = _safe_float(extras.get("low_critical"))
        hi_critical = _safe_float(extras.get("hi_critical"))
        units = extras.get("units") or None
    except Exception:
        pass

    # If top hit name closely matches description, auto-add
    auto_add = (
        desc_lower in top_name_lower
        or top_name_lower in desc_lower
        or _word_overlap(desc_lower, top_name_lower) >= 0.6
        or len(hits) == 1
    )

    if auto_add:
        concept_uuid = concept_id_to_openmrs_uuid(top.concept_id)
        category = infer_category(top.display_name)
        entry = LabCatalogEntry(
            concept_id=str(top.concept_id),
            concept_uuid=concept_uuid,
            display_name=top.display_name,
            category=category,
            units=units,
            low_normal=low_normal,
            hi_normal=hi_normal,
            low_critical=low_critical,
            hi_critical=hi_critical,
        )
        store.add(entry)
        log.info("Added lab test: %s (category=%s, ranges=%s-%s %s)",
                 entry.display_name, entry.category, low_normal, hi_normal, units)
        return {"status": "added", "entry": entry.to_dict()}

    # Ambiguous — return top-3 candidates for user to pick
    return {
        "status": "candidates",
        "candidates": [
            {
                "conceptId": str(h.concept_id),
                "conceptUuid": concept_id_to_openmrs_uuid(h.concept_id),
                "displayName": h.display_name,
                "conceptClass": h.concept_class,
                "category": infer_category(h.display_name),
            }
            for h in hits[:3]
        ],
    }


def confirm_add_candidate(
    concept_id: str,
    display_name: str,
    store: LabCatalogStore,
    ciel_client: Any,
) -> dict[str, Any]:
    """Add a specific concept after user confirms from candidates list."""
    if store.exists_by_concept_id(concept_id):
        existing = [e for e in store.list_all() if e.concept_id == concept_id]
        return {"status": "already_exists", "entry": existing[0].to_dict() if existing else {}}

    low_normal = hi_normal = low_critical = hi_critical = units = None
    try:
        bundle = ciel_client.get_concept_bundle(concept_id)
        extras = bundle.get("concept", {}).get("extras") or {}
        low_normal = _safe_float(extras.get("low_normal"))
        hi_normal = _safe_float(extras.get("hi_normal"))
        low_critical = _safe_float(extras.get("low_critical"))
        hi_critical = _safe_float(extras.get("hi_critical"))
        units = extras.get("units") or None
    except Exception:
        pass

    entry = LabCatalogEntry(
        concept_id=concept_id,
        concept_uuid=concept_id_to_openmrs_uuid(concept_id),
        display_name=display_name,
        category=infer_category(display_name),
        units=units,
        low_normal=low_normal,
        hi_normal=hi_normal,
        low_critical=low_critical,
        hi_critical=hi_critical,
    )
    store.add(entry)
    return {"status": "added", "entry": entry.to_dict()}


def _safe_float(val: Any) -> float | None:
    try:
        f = float(val)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _word_overlap(a: str, b: str) -> float:
    wa = set(re.sub(r"[^a-z0-9 ]", "", a).split())
    wb = set(re.sub(r"[^a-z0-9 ]", "", b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))
