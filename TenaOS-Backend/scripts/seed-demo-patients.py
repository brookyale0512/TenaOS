#!/usr/bin/env python3
"""Seed deterministic, synthetic TenaOS demo patients via OpenMRS REST.

The upstream reference-demo-data module is intentionally disabled because its
startup activator can monopolize OpenMRS first boot. This script provides a
small, repo-owned demo dataset instead:

* 50 synthetic patients, no PHI
* OpenMRS-generated preferred IDs plus a TENAOS-DEMO-* secondary identifier
* recent visits, vitals, and clinical notes
* optional queue entries when queue metadata is available

The script is idempotent. It writes a marker under the OpenMRS data directory
and also skips any patient whose TENAOS-DEMO-* identifier already exists.
"""

from __future__ import annotations

import base64
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REST_BASE = os.getenv("OPENMRS_REST_BASE_URL", "http://127.0.0.1:8080/openmrs/ws/rest/v1").rstrip("/")
USERNAME = os.getenv("OPENMRS_VERIFY_USERNAME") or os.getenv("OPENMRS_SERVICE_USER") or "admin"
PASSWORD = os.getenv("OPENMRS_VERIFY_PASSWORD") or os.getenv("OPENMRS_SERVICE_PASSWORD") or os.getenv("OPENMRS_ADMIN_PASSWORD") or ""
MARKER = Path(os.getenv("TENAOS_DEMO_PATIENT_MARKER", "/opt/openmrs/data/.tenaos-demo-patients-seeded"))
COUNT = int(os.getenv("TENAOS_DEMO_PATIENT_COUNT", "50"))
SEED = int(os.getenv("TENAOS_DEMO_PATIENT_RANDOM_SEED", "20260523"))

VITALS_ENCOUNTER_TYPE = os.getenv("VITALS_ENCOUNTER_TYPE_UUID", "67a71486-1a54-468f-ac3e-7091a9a79584")
NOTE_ENCOUNTER_TYPE = os.getenv("CLINICAL_NOTE_ENCOUNTER_TYPE_UUID", "d7151f82-c1f3-4152-a605-2f9ea7414a79")
NOTE_CONCEPT = os.getenv("CLINICAL_NOTE_CONCEPT_UUID", "162169AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
VITAL_CONCEPTS = {
    "temperature": "5088AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "systolic": "5085AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "diastolic": "5086AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "pulse": "5087AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "spo2": "5092AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "resp": "5242AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "height": "5090AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "weight": "5089AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
}


@dataclass(frozen=True)
class DemoPatient:
    identifier: str
    given_name: str
    family_name: str
    gender: str
    age_years: int
    scenario: str


def log(message: str) -> None:
    print(f"[seed-demo-patients] {message}", file=sys.stderr)


def fail(message: str, code: int = 1) -> int:
    print(f"[seed-demo-patients] ERROR: {message}", file=sys.stderr)
    return code


def auth_header() -> str:
    if not PASSWORD:
        raise RuntimeError("OPENMRS password is not configured")
    token = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def request(method: str, path: str, body: dict[str, Any] | None = None, *, retries: int = 4) -> dict[str, Any]:
    url = f"{REST_BASE}/{path.lstrip('/')}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "Authorization": auth_header()}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    last_error: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body_text}") from exc
        except Exception as exc:  # transient post-boot races
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{method} {path} failed after {retries} attempts: {last_error}")


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def first_result(path: str) -> dict[str, Any] | None:
    data = request("GET", path)
    results = data.get("results") or []
    return results[0] if results else None


def existing_patient_uuid(identifier: str) -> str | None:
    hit = first_result(f"patient?identifier={quote(identifier)}&v=default")
    return str(hit.get("uuid")) if hit and hit.get("uuid") else None


def resolve_location_uuid() -> str:
    preferred = ["Outpatient", "Outpatient Clinic", "Reception", "Inpatient"]
    for name in preferred:
        hit = first_result(f"location?q={quote(name)}&v=default&limit=10")
        if hit and not hit.get("retired"):
            return str(hit["uuid"])
    hit = first_result("location?v=default&limit=1")
    if not hit:
        raise RuntimeError("No OpenMRS location available")
    return str(hit["uuid"])


def resolve_visit_type_uuid() -> str:
    hit = first_result("visittype?v=default&limit=1")
    if not hit:
        raise RuntimeError("No OpenMRS visit type available")
    return str(hit["uuid"])


def resolve_identifier_type() -> str:
    data = request("GET", "patientidentifiertype?v=custom:(uuid,display,name,retired,validator)&limit=100")
    candidates = [entry for entry in data.get("results", []) if not entry.get("retired")]
    for entry in candidates:
        if entry.get("validator"):
            continue
        display = (entry.get("display") or entry.get("name") or "").lower()
        if any(term in display for term in ("old", "identification", "national")):
            return str(entry["uuid"])
    for entry in candidates:
        if not entry.get("validator"):
            return str(entry["uuid"])
    if not candidates:
        raise RuntimeError("No patient identifier type available")
    return str(candidates[0]["uuid"])


def resolve_openmrs_id_source() -> tuple[str, str] | tuple[None, None]:
    type_uuid: str | None = None
    type_data = request("GET", "patientidentifiertype?v=custom:(uuid,display,name,retired,validator)&limit=100")
    for entry in type_data.get("results", []):
        display = (entry.get("display") or entry.get("name") or "").lower()
        if not entry.get("retired") and "openmrs id" in display:
            type_uuid = str(entry["uuid"])
            break
    if not type_uuid:
        return None, None
    source_data = request("GET", "idgen/identifiersource?v=full&limit=50")
    for entry in source_data.get("results", []):
        name = (entry.get("display") or entry.get("name") or "").lower()
        identifier_type = entry.get("identifierType") or {}
        if identifier_type.get("uuid") == type_uuid or "openmrs id" in name:
            return type_uuid, str(entry["uuid"])
    return type_uuid, None


def mint_identifier(source_uuid: str) -> str:
    data = request("POST", f"idgen/identifiersource/{source_uuid}/identifier", {})
    identifier = data.get("identifier")
    if not identifier:
        raise RuntimeError("IDGen returned no identifier")
    return str(identifier)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000%z")


def birthdate_for_age(age_years: int, rng: random.Random) -> str:
    today = datetime.now(timezone.utc).date()
    day_offset = rng.randint(0, 330)
    born = today.replace(year=today.year - age_years) - timedelta(days=day_offset)
    return born.isoformat()


def build_patients() -> list[DemoPatient]:
    given_f = ["Amina", "Liya", "Marta", "Selam", "Nia", "Grace", "Ruth", "Hana", "Zara", "Miriam"]
    given_m = ["Samuel", "Daniel", "Brian", "Jonah", "Noah", "Elias", "Kofi", "Adam", "David", "Joseph"]
    family = ["Lopez", "Bekele", "Mensah", "Kamau", "Okafor", "Hassan", "Dube", "Ndlovu", "Ayele", "Mwangi"]
    scenarios = [
        "hypertension follow-up",
        "antenatal care review",
        "child fever triage",
        "diabetes monitoring",
        "cough and respiratory symptoms",
        "postnatal check",
        "routine immunization visit",
        "malaria follow-up",
        "medication refill",
        "wellness screening",
    ]
    ages = [2, 4, 8, 13, 19, 24, 29, 34, 41, 52, 61, 73]
    patients: list[DemoPatient] = []
    for i in range(1, COUNT + 1):
        gender = "F" if i % 2 else "M"
        given = given_f[i % len(given_f)] if gender == "F" else given_m[i % len(given_m)]
        patients.append(
            DemoPatient(
                identifier=f"TENAOS-DEMO-{i:03d}",
                given_name=given,
                family_name=family[(i * 3) % len(family)],
                gender=gender,
                age_years=ages[(i * 5) % len(ages)],
                scenario=scenarios[(i - 1) % len(scenarios)],
            )
        )
    return patients


def create_patient(patient: DemoPatient, *, location_uuid: str, free_id_type_uuid: str, omrs_id_type_uuid: str | None, omrs_id_source_uuid: str | None, rng: random.Random) -> tuple[str, bool]:
    existing = existing_patient_uuid(patient.identifier)
    if existing:
        return existing, False
    identifiers: list[dict[str, Any]] = [{
        "identifier": patient.identifier,
        "identifierType": free_id_type_uuid,
        "location": location_uuid,
        "preferred": False,
    }]
    if omrs_id_type_uuid and omrs_id_source_uuid:
        identifiers.append({
            "identifier": mint_identifier(omrs_id_source_uuid),
            "identifierType": omrs_id_type_uuid,
            "location": location_uuid,
            "preferred": True,
        })
    else:
        identifiers[0]["preferred"] = True
    data = request("POST", "patient", {
        "person": {
            "names": [{"givenName": patient.given_name, "familyName": patient.family_name, "preferred": True}],
            "gender": patient.gender,
            "birthdate": birthdate_for_age(patient.age_years, rng),
            "birthdateEstimated": False,
        },
        "identifiers": identifiers,
    })
    if not data.get("uuid"):
        raise RuntimeError(f"Patient creation returned no uuid for {patient.identifier}")
    return str(data["uuid"]), True


def create_visit(patient_uuid: str, visit_type_uuid: str, location_uuid: str, start: datetime, stop: datetime | None = None) -> str:
    body: dict[str, Any] = {
        "patient": patient_uuid,
        "visitType": visit_type_uuid,
        "location": location_uuid,
        "startDatetime": iso(start),
    }
    if stop:
        body["stopDatetime"] = iso(stop)
    data = request("POST", "visit", body)
    if not data.get("uuid"):
        raise RuntimeError("Visit creation returned no uuid")
    return str(data["uuid"])


def create_vitals(patient_uuid: str, visit_uuid: str, location_uuid: str, when: datetime, rng: random.Random, age_years: int) -> None:
    systolic = rng.randint(98, 158 if age_years >= 18 else 122)
    diastolic = rng.randint(62, 96 if age_years >= 18 else 82)
    pulse = rng.randint(64, 112)
    weight = round(rng.uniform(12, 28), 1) if age_years < 10 else round(rng.uniform(42, 91), 1)
    height = round(rng.uniform(80, 138), 1) if age_years < 10 else round(rng.uniform(150, 184), 1)
    obs = [
        {"concept": VITAL_CONCEPTS["temperature"], "value": round(rng.uniform(36.2, 38.3), 1)},
        {"concept": VITAL_CONCEPTS["systolic"], "value": systolic},
        {"concept": VITAL_CONCEPTS["diastolic"], "value": diastolic},
        {"concept": VITAL_CONCEPTS["pulse"], "value": pulse},
        {"concept": VITAL_CONCEPTS["spo2"], "value": rng.randint(94, 100)},
        {"concept": VITAL_CONCEPTS["resp"], "value": rng.randint(14, 28)},
        {"concept": VITAL_CONCEPTS["height"], "value": height},
        {"concept": VITAL_CONCEPTS["weight"], "value": weight},
    ]
    request("POST", "encounter", {
        "patient": patient_uuid,
        "visit": visit_uuid,
        "encounterType": VITALS_ENCOUNTER_TYPE,
        "encounterDatetime": iso(when),
        "location": location_uuid,
        "obs": obs,
    })


def create_note(patient: DemoPatient, patient_uuid: str, visit_uuid: str, location_uuid: str, when: datetime) -> None:
    note = (
        f"Synthetic demo encounter for {patient.scenario}. "
        f"Patient is clinically stable, reviewed by nurse, and advised on follow-up. "
        f"No emergency danger signs documented in this demo record."
    )
    request("POST", "encounter", {
        "patient": patient_uuid,
        "visit": visit_uuid,
        "encounterType": NOTE_ENCOUNTER_TYPE,
        "encounterDatetime": iso(when),
        "location": location_uuid,
        "obs": [{"concept": NOTE_CONCEPT, "value": note}],
    })


def maybe_add_queue_entries(patient_uuids: list[str]) -> int:
    queues = request("GET", "queue?v=full&limit=10").get("results") or []
    if not queues:
        log("No queue metadata found; skipping queue-entry demo data.")
        return 0
    queue = queues[0]
    statuses = queue.get("allowedStatuses") or []
    priorities = queue.get("allowedPriorities") or []
    if not statuses or not priorities:
        log("Queue metadata has no allowed statuses/priorities; skipping queue-entry demo data.")
        return 0
    count = 0
    for patient_uuid in patient_uuids[: min(12, len(patient_uuids))]:
        request("POST", "queue-entry", {
            "patient": patient_uuid,
            "queue": queue["uuid"],
            "status": statuses[-1]["uuid"],  # usually Waiting
            "priority": priorities[count % len(priorities)]["uuid"],
            "priorityComment": "Synthetic TenaOS demo queue entry",
            "startedAt": iso(datetime.now(timezone.utc) - timedelta(minutes=count * 7)),
        }, retries=2)
        count += 1
    return count


def main() -> int:
    if os.getenv("TENAOS_SEED_DEMO_PATIENTS", "true").strip().lower() in {"0", "false", "no", "off"}:
        log("TENAOS_SEED_DEMO_PATIENTS=false; skipping.")
        return 0
    if MARKER.exists():
        log(f"Marker exists at {MARKER}; skipping.")
        return 0
    rng = random.Random(SEED)
    location_uuid = resolve_location_uuid()
    visit_type_uuid = resolve_visit_type_uuid()
    free_id_type_uuid = resolve_identifier_type()
    omrs_id_type_uuid, omrs_id_source_uuid = resolve_openmrs_id_source()

    created_patient_uuids: list[str] = []
    total_visits = 0
    total_encounters = 0
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for idx, patient in enumerate(build_patients(), start=1):
        patient_uuid, created = create_patient(
            patient,
            location_uuid=location_uuid,
            free_id_type_uuid=free_id_type_uuid,
            omrs_id_type_uuid=omrs_id_type_uuid,
            omrs_id_source_uuid=omrs_id_source_uuid,
            rng=rng,
        )
        created_patient_uuids.append(patient_uuid)
        if not created:
            log(f"{patient.identifier} already exists; skipping encounter reseed.")
            continue
        # Every patient gets one closed historical visit; every fifth patient
        # also gets an active visit so clinical workflows can be exercised.
        historical_start = now - timedelta(days=idx * 3, hours=idx % 6)
        historical_visit = create_visit(
            patient_uuid,
            visit_type_uuid,
            location_uuid,
            historical_start,
            historical_start + timedelta(hours=2),
        )
        total_visits += 1
        create_vitals(patient_uuid, historical_visit, location_uuid, historical_start + timedelta(minutes=10), rng, patient.age_years)
        create_note(patient, patient_uuid, historical_visit, location_uuid, historical_start + timedelta(minutes=35))
        total_encounters += 2
        if idx % 5 == 0:
            active_start = now - timedelta(hours=(idx % 8) + 1)
            active_visit = create_visit(patient_uuid, visit_type_uuid, location_uuid, active_start)
            total_visits += 1
            create_vitals(patient_uuid, active_visit, location_uuid, active_start + timedelta(minutes=8), rng, patient.age_years)
            total_encounters += 1

    queue_entries = 0
    try:
        queue_entries = maybe_add_queue_entries(created_patient_uuids)
    except Exception as exc:
        log(f"Queue-entry seeding skipped after error: {exc}")

    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps({
        "seededAt": datetime.now(timezone.utc).isoformat(),
        "patients": len(created_patient_uuids),
        "visits": total_visits,
        "encounters": total_encounters,
        "queueEntries": queue_entries,
    }, indent=2) + "\n")
    log(f"Seeded {len(created_patient_uuids)} demo patients, {total_visits} visits, {total_encounters} encounters, {queue_entries} queue entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
