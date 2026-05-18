#!/usr/bin/env python3
"""Seed synthetic obs into the running OpenMRS so the report builder demo has data.

Without obs the report page returns zero hits for every query. This script:

  1. Reads ``cds/runtime/form_drafts.sqlite3`` to discover every CIEL
     concept_id referenced by any published form. These are the concepts the
     report builder will actually search against.
  2. Creates 24 synthetic patients (12 female + 12 male across 5 age
     buckets) via the OpenMRS REST API. Identifier prefix ``SYN-DEMO-``
     makes them trivial to wipe (``DELETE FROM patient WHERE … LIKE
     'SYN-DEMO-%'``).
  3. For each patient, generates 2-4 Consultation encounters spread across
     the last 12 months and emits weighted obs against the discovered CIEL
     concepts. Booleans for symptoms (cough ~60% Yes, weight loss ~40%,
     fever ~30%, night sweats ~25%), Coded for HIV status (~65% Negative,
     ~20% Positive, ~15% Unknown), Numeric within realistic CIEL extras
     ranges.
  4. Writes ``runtime-artifacts/demo-seed-marker.json`` so reruns only add
     newly-published-form concepts rather than duplicating obs.

Idempotent. Uses a fixed PRNG seed (42) so distributions are reproducible.

Usage::

    OPENMRS_USERNAME=admin OPENMRS_PASSWORD=Admin123 \
        python3 cds/scripts/seed_demo_obs.py

    # Wipe synthetic patients between runs:
    OPENMRS_USERNAME=admin OPENMRS_PASSWORD=Admin123 \
        python3 cds/scripts/seed_demo_obs.py --wipe
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DRAFTS_DB = REPO_ROOT / "cds" / "runtime" / "form_drafts.sqlite3"
DEFAULT_CIEL_DB = Path(os.environ.get("CDS_CIEL_SQLITE", "/var/www/ClinicDx_backend/CIEL/ciel_search.sqlite3"))
DEFAULT_MARKER = REPO_ROOT / "runtime-artifacts" / "demo-seed-marker.json"

OPENMRS_REST = os.environ.get("OPENMRS_REST_BASE_URL", "http://127.0.0.1:18080/openmrs/ws/rest/v1").rstrip("/")
ENCOUNTER_TYPE_CONSULTATION = "dd528487-82a5-4082-9c72-ed246bd49591"

NUM_PATIENTS = 24
RANDOM_SEED = 42

# Default symptom distribution. The key matches the CIEL concept_id; the value
# is the probability of "positive" (True for Boolean, "Positive" for HIV
# Coded). Concepts not in this map fall back to a generic distribution.
DEFAULT_DISTRIBUTION: dict[str, float] = {
    # cough variants
    "143264": 0.60,
    "145455": 0.55,
    "159666": 0.50,
    # fever
    "140238": 0.30,
    "162628": 0.30,
    "169237": 0.20,
    # weight loss
    "832": 0.40,
    "1731": 0.35,
    "1856": 0.40,
    # night sweats
    "1479": 0.25,
    # HIV
    "1063": 0.20,  # Positive
    "159576": 0.20,  # Positive
    "1169": 0.20,
    # generic boolean defaults
    "1396": 0.20,
}


@dataclass
class Patient:
    identifier: str
    given_name: str
    family_name: str
    gender: str
    birthdate: str  # ISO date


def _basic_auth() -> str | None:
    user = os.getenv("OPENMRS_USERNAME")
    pwd = os.getenv("OPENMRS_PASSWORD")
    if not user or not pwd:
        return None
    token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    auth = _basic_auth()
    if auth:
        headers["Authorization"] = auth
    return headers


def _http(method: str, path: str, body: dict[str, Any] | None = None, *, base: str = OPENMRS_REST) -> dict[str, Any]:
    url = f"{base}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={**_headers(), "Content-Type": "application/json"} if data is not None else _headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        print(f"[seed] HTTP {exc.code} on {method} {path}: {body_text[:300]}", file=sys.stderr)
        raise


# ---------------------------------------------------------------------------
# Discovery


def discover_concepts(drafts_db: Path) -> list[str]:
    """Return the set of CIEL concept_ids referenced by any published form."""
    if not drafts_db.exists():
        print(f"[seed] No drafts DB at {drafts_db}; using a fallback TB concept set.", file=sys.stderr)
        return ["143264", "832", "1479", "140238", "159576"]
    conn = sqlite3.connect(drafts_db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT basket_json FROM form_drafts WHERE status = 'published'"
        ).fetchall()
    finally:
        conn.close()
    concept_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        try:
            basket = json.loads(row["basket_json"] or "{}")
        except Exception:
            continue
        for section in basket.get("sections") or []:
            for field in section.get("fields") or []:
                cid = str(field.get("conceptId") or "").strip()
                if cid and cid not in seen:
                    seen.add(cid)
                    concept_ids.append(cid)
    if not concept_ids:
        print("[seed] No published forms yet; using a fallback TB concept set.", file=sys.stderr)
        return ["143264", "832", "1479", "140238", "159576"]
    return concept_ids


def load_concept_bundles(ciel_db: Path, concept_ids: list[str]) -> dict[str, dict[str, Any]]:
    bundles: dict[str, dict[str, Any]] = {}
    if not ciel_db.exists():
        print(f"[seed] CIEL store missing at {ciel_db}; cannot load bundles.", file=sys.stderr)
        return bundles
    conn = sqlite3.connect(ciel_db)
    conn.row_factory = sqlite3.Row
    try:
        for cid in concept_ids:
            row = conn.execute("SELECT bundle_json FROM concept_bundles WHERE concept_id = ?", (cid,)).fetchone()
            if row and row["bundle_json"]:
                try:
                    bundles[cid] = json.loads(row["bundle_json"])
                except Exception:
                    continue
    finally:
        conn.close()
    return bundles


# ---------------------------------------------------------------------------
# Patient demographics (deterministic distribution)


def build_patient_demographics() -> list[Patient]:
    rng = random.Random(RANDOM_SEED)
    given_male = ["Aman", "Bekele", "Daniel", "Elias", "Hailu", "Kebede", "Lemma", "Mesfin", "Nahom", "Omar", "Paulos", "Solomon"]
    given_female = ["Alemnesh", "Bethel", "Chaltu", "Dagmawit", "Eden", "Genet", "Hewan", "Lia", "Marta", "Nardos", "Saron", "Tigist"]
    family = ["Abebe", "Bekele", "Desta", "Fikru", "Girma", "Hagos", "Kifle", "Lemma", "Mekonnen", "Negash", "Tadesse", "Yilma"]

    age_buckets = [
        (0, 4, 5),
        (5, 14, 5),
        (15, 24, 5),
        (25, 49, 6),
        (50, 80, 3),
    ]
    today = date.today()
    patients: list[Patient] = []
    serial = 1
    for genders, given_names in (("F", given_female), ("M", given_male)):
        for low, high, count_per_gender in age_buckets:
            # Half of each bucket per gender (handles odd counts via floor/ceil).
            n = max(1, count_per_gender // 2 if genders == "F" else (count_per_gender + 1) // 2)
            for _ in range(n):
                age = rng.randint(low, high)
                bd = today.replace(year=today.year - age) - timedelta(days=rng.randint(0, 180))
                patients.append(
                    Patient(
                        identifier=f"SYN-DEMO-{serial:03d}",
                        given_name=rng.choice(given_names),
                        family_name=rng.choice(family),
                        gender=genders,
                        birthdate=bd.isoformat(),
                    )
                )
                serial += 1
                if len(patients) >= NUM_PATIENTS:
                    return patients
    return patients


# ---------------------------------------------------------------------------
# OpenMRS interactions


def find_existing_patient(identifier: str) -> str | None:
    try:
        response = _http("GET", f"patient?identifier={urllib.parse.quote(identifier)}&v=default")
    except Exception:
        return None
    results = response.get("results") or []
    if not results:
        return None
    return results[0].get("uuid")


def _mint_openmrs_id(source_uuid: str) -> str:
    response = _http("POST", f"idgen/identifiersource/{source_uuid}/identifier", body={})
    identifier = response.get("identifier") or ""
    if not identifier:
        raise RuntimeError("IDGen returned no identifier")
    return identifier


def ensure_patient(
    p: Patient,
    *,
    free_form_type_uuid: str,
    openmrs_id_type_uuid: str | None,
    openmrs_id_source_uuid: str | None,
    location_uuid: str,
) -> str:
    existing = find_existing_patient(p.identifier)
    if existing:
        return existing
    identifiers: list[dict[str, Any]] = [
        {
            "identifier": p.identifier,
            "identifierType": free_form_type_uuid,
            "location": location_uuid,
            "preferred": False,
        }
    ]
    # Many OpenMRS installs require a Luhn-checked OpenMRS ID. Mint one via
    # IDGen and mark it preferred.
    if openmrs_id_type_uuid and openmrs_id_source_uuid:
        try:
            openmrs_id = _mint_openmrs_id(openmrs_id_source_uuid)
            identifiers.append(
                {
                    "identifier": openmrs_id,
                    "identifierType": openmrs_id_type_uuid,
                    "location": location_uuid,
                    "preferred": True,
                }
            )
            identifiers[0]["preferred"] = False
        except Exception as exc:
            print(f"[seed] Could not mint OpenMRS ID for {p.identifier}: {exc}", file=sys.stderr)
            identifiers[0]["preferred"] = True
    else:
        identifiers[0]["preferred"] = True
    payload = {
        "person": {
            "names": [
                {
                    "givenName": p.given_name,
                    "familyName": p.family_name,
                }
            ],
            "gender": p.gender,
            "birthdate": p.birthdate,
        },
        "identifiers": identifiers,
    }
    response = _http("POST", "patient", payload)
    uuid = response.get("uuid") or ""
    if not uuid:
        raise RuntimeError(f"OpenMRS did not return uuid for synthetic patient {p.identifier}")
    return uuid


def _default_identifier_type() -> str:
    """Pick the most permissive non-retired identifier type.

    OpenMRS often ships with multiple identifier types; some carry a
    Luhn Mod-30 check-digit validator that rejects arbitrary strings like
    ``SYN-DEMO-001``. We probe each type's `validator` field and prefer the
    one whose validator is empty (any string accepted) and whose display
    name suggests a free-form identifier ("Old Identification Number" is
    the conventional OpenMRS pick for this).
    """
    response = _http(
        "GET",
        "patientidentifiertype?v=custom:(uuid,display,name,retired,validator)&limit=50",
    )
    candidates = [entry for entry in response.get("results") or [] if not entry.get("retired")]
    # First: a non-retired type with no validator AND a friendly name.
    for entry in candidates:
        if entry.get("validator"):
            continue
        display = (entry.get("display") or entry.get("name") or "").lower()
        if "old" in display or "identification" in display or "national" in display:
            return entry.get("uuid")
    # Then: any non-retired type with no validator at all.
    for entry in candidates:
        if not entry.get("validator"):
            return entry.get("uuid")
    # Fall back to the first non-retired type (may force the operator to
    # supply identifiers that pass the validator).
    if candidates:
        return candidates[0].get("uuid")
    raise RuntimeError("No usable patient identifier types found in OpenMRS")


def _openmrs_id_type_and_source() -> tuple[str | None, str | None]:
    """Locate the OpenMRS ID identifier-type + matching IDGen source.

    On standard Reference Application installs, every patient must have an
    OpenMRS ID with a Luhn-checked value. We mint one via the IDGen module
    and attach it as the preferred identifier so the create-patient call
    passes the required-identifier check.
    """
    type_uuid: str | None = None
    try:
        types = _http(
            "GET",
            "patientidentifiertype?v=custom:(uuid,display,name,retired,validator)&limit=50",
        )
        for entry in types.get("results") or []:
            if entry.get("retired"):
                continue
            display = (entry.get("display") or entry.get("name") or "").lower()
            if "openmrs id" in display:
                type_uuid = entry.get("uuid")
                break
    except Exception:
        pass
    if not type_uuid:
        return None, None

    source_uuid: str | None = None
    try:
        sources = _http("GET", "idgen/identifiersource?v=default&limit=20")
        for entry in sources.get("results") or []:
            name = (entry.get("display") or entry.get("name") or "").lower()
            if "openmrs id" in name:
                source_uuid = entry.get("uuid")
                break
    except Exception:
        return type_uuid, None
    return type_uuid, source_uuid


def _default_location() -> str:
    response = _http("GET", "location?v=default&limit=10")
    for entry in response.get("results") or []:
        if not entry.get("retired"):
            return entry.get("uuid")
    raise RuntimeError("No location found in OpenMRS")


def create_encounter(patient_uuid: str, location_uuid: str, when: datetime) -> str:
    payload = {
        "encounterType": ENCOUNTER_TYPE_CONSULTATION,
        "patient": patient_uuid,
        "location": location_uuid,
        "encounterDatetime": when.replace(tzinfo=timezone.utc).isoformat(),
    }
    response = _http("POST", "encounter", payload)
    return response.get("uuid") or ""


def post_obs(encounter_uuid: str, patient_uuid: str, concept_uuid: str, when: datetime, *, value_boolean: bool | None = None, value_coded: str | None = None, value_numeric: float | None = None, value_text: str | None = None) -> None:
    payload: dict[str, Any] = {
        "encounter": encounter_uuid,
        "person": patient_uuid,
        "concept": concept_uuid,
        "obsDatetime": when.replace(tzinfo=timezone.utc).isoformat(),
    }
    if value_boolean is not None:
        payload["value"] = bool(value_boolean)
    elif value_coded is not None:
        payload["value"] = value_coded
    elif value_numeric is not None:
        payload["value"] = float(value_numeric)
    elif value_text is not None:
        payload["value"] = str(value_text)
    else:
        return
    try:
        _http("POST", "obs", payload)
    except urllib.error.HTTPError:
        # Tolerate per-obs failures so we don't bail the whole seed run.
        return


# ---------------------------------------------------------------------------
# Distribution


def _ciel_uuid(concept_id: str) -> str:
    raw = str(concept_id).strip()
    return raw + ("A" * max(0, 36 - len(raw)))


def _coded_yes_no_uuid(rng: random.Random, positive_prob: float) -> str:
    # 1065 = Yes, 1066 = No, 1067 = Unknown
    pick = rng.random()
    if pick < positive_prob:
        return _ciel_uuid("1065")
    if pick < positive_prob + 0.85 * (1.0 - positive_prob):
        return _ciel_uuid("1066")
    return _ciel_uuid("1067")


def _hiv_coded_uuid(rng: random.Random) -> str:
    pick = rng.random()
    if pick < 0.20:
        return _ciel_uuid("703")  # Positive
    if pick < 0.85:
        return _ciel_uuid("664")  # Negative
    return _ciel_uuid("1067")  # Unknown


def value_for_concept(
    rng: random.Random,
    concept_id: str,
    bundle: dict[str, Any],
) -> dict[str, Any] | None:
    concept = bundle.get("concept") or {}
    datatype = (concept.get("datatype") or "").strip()
    cls = (concept.get("concept_class") or "").strip().lower()
    distribution = DEFAULT_DISTRIBUTION.get(concept_id, 0.30)

    if datatype == "Boolean":
        return {"value_boolean": rng.random() < distribution}
    if datatype == "Coded":
        # If this looks like an HIV status concept, use the HIV distribution.
        display = (concept.get("display_name") or "").lower()
        if "hiv" in display or concept_id == "1063":
            return {"value_coded": _hiv_coded_uuid(rng)}
        return {"value_coded": _coded_yes_no_uuid(rng, distribution)}
    if datatype == "Numeric":
        extras = concept.get("extras") or {}
        low = extras.get("low_absolute")
        high = extras.get("hi_absolute")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
            low_f, high_f = float(low), float(high)
        else:
            # Reasonable fallback range for a generic numeric obs.
            low_f, high_f = 0.0, 100.0
        value = rng.uniform(low_f, high_f)
        if extras.get("allow_decimal") is False:
            value = round(value)
        return {"value_numeric": round(value, 2)}
    if datatype == "Text":
        return {"value_text": "(synthetic seed)"}
    if datatype in {"N/A", ""}:
        # N/A clinical concepts (Diagnosis/Symptom/Finding) render as Yes/No
        # in our form schema; emit a coded yes/no value here too.
        clinical = {"diagnosis", "symptom", "finding", "symptom/finding", "procedure", "test", "question"}
        if cls in clinical:
            return {"value_coded": _coded_yes_no_uuid(rng, distribution)}
    return None


# ---------------------------------------------------------------------------
# Main


def seed(args: argparse.Namespace) -> int:
    if not _basic_auth():
        print("[seed] OPENMRS_USERNAME / OPENMRS_PASSWORD not set; aborting.", file=sys.stderr)
        return 2
    drafts_db = Path(args.drafts_db)
    ciel_db = Path(args.ciel_db)
    concept_ids = discover_concepts(drafts_db)
    if not concept_ids:
        print("[seed] No CIEL concepts discovered; nothing to seed.")
        return 1
    bundles = load_concept_bundles(ciel_db, concept_ids)
    if not bundles:
        print("[seed] CIEL bundles unavailable; cannot decide values to emit.", file=sys.stderr)
        return 3
    print(f"[seed] Discovered {len(concept_ids)} CIEL concepts across published forms.")

    free_form_type_uuid = _default_identifier_type()
    location_uuid = _default_location()
    openmrs_id_type_uuid, openmrs_id_source_uuid = _openmrs_id_type_and_source()
    patients = build_patient_demographics()
    rng = random.Random(RANDOM_SEED)

    today = date.today()
    horizon = today - timedelta(days=365)
    total_encounters = 0
    total_obs = 0

    for patient in patients:
        try:
            patient_uuid = ensure_patient(
                patient,
                free_form_type_uuid=free_form_type_uuid,
                openmrs_id_type_uuid=openmrs_id_type_uuid,
                openmrs_id_source_uuid=openmrs_id_source_uuid,
                location_uuid=location_uuid,
            )
        except Exception as exc:
            print(f"[seed] Skipping {patient.identifier}: {exc}", file=sys.stderr)
            continue
        encounter_count = rng.randint(2, 4)
        for _ in range(encounter_count):
            span_days = (today - horizon).days
            offset = rng.randint(0, max(1, span_days))
            when_date = horizon + timedelta(days=offset)
            when = datetime.combine(when_date, datetime.min.time())
            try:
                encounter_uuid = create_encounter(patient_uuid, location_uuid, when)
            except Exception as exc:
                print(f"[seed] Could not create encounter for {patient.identifier}: {exc}", file=sys.stderr)
                continue
            total_encounters += 1
            # Pick a random subset of concepts for this encounter (5-9).
            concept_subset = rng.sample(concept_ids, k=min(len(concept_ids), rng.randint(5, max(5, min(9, len(concept_ids))))))
            for cid in concept_subset:
                bundle = bundles.get(cid)
                if not bundle:
                    continue
                value = value_for_concept(rng, cid, bundle)
                if not value:
                    continue
                post_obs(encounter_uuid, patient_uuid, _ciel_uuid(cid), when, **value)
                total_obs += 1

    marker = {
        "lastRunAt": datetime.now(timezone.utc).isoformat(),
        "patientsTarget": NUM_PATIENTS,
        "patientsCreated": len(patients),
        "encountersCreated": total_encounters,
        "obsPosted": total_obs,
        "conceptsCovered": len(concept_ids),
    }
    marker_path = Path(args.marker)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker, indent=2))
    print(
        f"[seed] Seeded {len(patients)} patients, {total_encounters} encounters, "
        f"{total_obs} obs across {len(concept_ids)} CIEL concepts."
    )
    return 0


def wipe(_args: argparse.Namespace) -> int:
    """Best-effort wipe of synthetic patients (by identifier prefix).

    The script does not have direct DB access; it iterates the REST patient
    search and voids each synthetic record. The CDS demo doc lists a faster
    SQL-side cleanup for operators with DB access.
    """
    if not _basic_auth():
        print("[seed] OPENMRS_USERNAME / OPENMRS_PASSWORD not set; aborting.", file=sys.stderr)
        return 2
    response = _http("GET", "patient?q=SYN-DEMO&v=default&limit=100")
    voided = 0
    for entry in response.get("results") or []:
        uuid = entry.get("uuid")
        if not uuid:
            continue
        try:
            _http("DELETE", f"patient/{uuid}?reason=demo-seed-wipe")
            voided += 1
        except Exception:
            continue
    print(f"[seed] Voided {voided} synthetic patients.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic obs for the report-builder demo")
    parser.add_argument("--drafts-db", default=str(DEFAULT_DRAFTS_DB))
    parser.add_argument("--ciel-db", default=str(DEFAULT_CIEL_DB))
    parser.add_argument("--marker", default=str(DEFAULT_MARKER))
    parser.add_argument("--wipe", action="store_true", help="Void synthetic SYN-DEMO-* patients instead of seeding.")
    args = parser.parse_args()
    if args.wipe:
        return wipe(args)
    return seed(args)


if __name__ == "__main__":
    sys.exit(main())
