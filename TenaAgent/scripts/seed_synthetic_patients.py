#!/usr/bin/env python3
"""
seed_synthetic_patients.py
──────────────────────────
Two-phase synthetic patient data pipeline for TenaOS demo.

PHASE 1 — generate (default / --generate):
  For each patient:
    1. DeepSeek on Vertex AI  → rich clinical note per visit
    2. TenaOS scribe pipeline → SOAP + CIEL concept IDs + numeric obs + medications
  Saves all output to a JSON file.  No OpenMRS writes.

PHASE 2 — import (--import FILE):
  Reads the generated JSON and writes to OpenMRS:
    1. Mint OpenMRS ID via IDGen API
    2. Create patient via REST
    3. Create backdated visits + scribe encounters (confirm_text with encounterDatetime)
    4. POST conditions

Usage
-----
  # Step 1 — generate data (safe, no DB writes):
  python seed_synthetic_patients.py --generate --out synthetic_data.json

  # Step 1 test mode — 1 patient, prints note + SOAP, no writes:
  python seed_synthetic_patients.py --test

  # Step 2 — import into OpenMRS:
  python seed_synthetic_patients.py --import synthetic_data.json

  # Resume interrupted generation:
  python seed_synthetic_patients.py --generate --out synthetic_data.json --resume

Environment variables
---------------------
  OPENMRS_REST_BASE_URL       default: http://127.0.0.1:8080/openmrs/ws/rest/v1
  OPENMRS_SERVICE_USER        default: admin
  OPENMRS_SERVICE_PASSWORD    required for import phase
  TENAOS_AGENT_BASE_URL       default: http://127.0.0.1:8095
  VERTEX_PROJECT_ID           default: gen-lang-client-0662339493
  VERTEX_LOCATION             default: us-central1
  VERTEX_MODEL                default: deepseek-ai/deepseek-r1-0528-maas
  VERTEX_ACCESS_TOKEN         gcloud bearer token (auto-resolved via gcloud CLI if absent)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Runtime configuration

OPENMRS_REST = os.getenv(
    "OPENMRS_REST_BASE_URL", "http://127.0.0.1:8080/openmrs/ws/rest/v1"
).rstrip("/")
OPENMRS_USER = (
    os.getenv("OPENMRS_SERVICE_USER")
    or os.getenv("OPENMRS_VERIFY_USERNAME")
    or "admin"
)
OPENMRS_PASS = (
    os.getenv("OPENMRS_SERVICE_PASSWORD")
    or os.getenv("OPENMRS_VERIFY_PASSWORD")
    or os.getenv("OPENMRS_ADMIN_PASSWORD")
    or ""
)

AGENT_BASE = os.getenv("TENAOS_AGENT_BASE_URL", "http://127.0.0.1:8095").rstrip("/")

VERTEX_PROJECT = os.getenv("VERTEX_PROJECT_ID", "gen-lang-client-0662339493")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "deepseek-ai/deepseek-r1-0528-maas")
VERTEX_API_BASE = os.getenv(
    "VERTEX_API_BASE",
    f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/"
    f"projects/{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}/endpoints/openapi",
)

DEFAULT_OUT = "synthetic_data.json"


# ---------------------------------------------------------------------------
# LMIC-representative name pools

GIVEN_NAMES_F = [
    "Amina", "Fatima", "Halima", "Zeinab", "Mekdes", "Tigist", "Hana", "Liya",
    "Selam", "Grace", "Priya", "Anita", "Sunita", "Aisha", "Mariam", "Rahel",
    "Kadija", "Yeshi", "Abeba", "Miriam", "Nadia", "Asha", "Bintu", "Saron",
]
GIVEN_NAMES_M = [
    "Abebe", "Mulugeta", "Yohannes", "Samuel", "Dawit", "Girma", "Tesfaye",
    "Ravi", "Suresh", "Mohamed", "Kofi", "Kwame", "Daniel", "Elias", "Joseph",
    "Tadesse", "Bekele", "Kifle", "Haile", "Ibrahim", "Oumar", "Sekou", "Lamin",
]
FAMILY_NAMES = [
    "Bekele", "Tadesse", "Girma", "Haile", "Kebede", "Mengistu", "Mensah",
    "Kamau", "Okafor", "Hassan", "Dube", "Ayele", "Mwangi", "Sharma", "Patel",
    "Ndlovu", "Diallo", "Traore", "Coulibaly", "Ndiaye", "Waweru", "Otieno",
]


# ---------------------------------------------------------------------------
# Clinical scenario definitions

@dataclass
class Scenario:
    key: str
    label: str
    sex: str           # "F", "M", or "any"
    age_min: int
    age_max: int
    num_patients: int
    visit_min: int
    visit_max: int
    system_context: str
    visit_progressions: list[str]


SCENARIOS: list[Scenario] = [
    Scenario(
        key="malaria",
        label="Plasmodium falciparum malaria",
        sex="any", age_min=2, age_max=55,
        num_patients=12, visit_min=2, visit_max=4,
        system_context=(
            "Patient presenting with malaria in a sub-Saharan African district hospital. "
            "Include Temperature ≥38.5°C, RDT result (positive for Pf antigen), pulse, weight. "
            "Assessment MUST say 'Plasmodium falciparum malaria'. "
            "Treatment: Artemether-lumefantrine 80/480mg twice daily for 3 days (uncomplicated); "
            "IV Artesunate if severe."
        ),
        visit_progressions=[
            "First presentation: acute fever (3–5 days), chills, headache, myalgia. "
            "RDT positive. Start artemether-lumefantrine. Include temperature, pulse, weight, SpO2.",
            "Follow-up 3–5 days after treatment: fever clearance, appetite returning. "
            "Temperature now normal or near-normal.",
            "Final review 7–10 days: full recovery or note complication. Discharge if recovered.",
            "Extra visit only if complicated: repeat blood film, transfusion if Hgb <7g/dL.",
        ],
    ),
    Scenario(
        key="tb",
        label="Pulmonary tuberculosis",
        sex="any", age_min=15, age_max=65,
        num_patients=10, visit_min=4, visit_max=8,
        system_context=(
            "Patient with suspected or confirmed pulmonary TB at an LMIC health facility. "
            "Include cough duration (weeks), night sweats, weight in kg (declining then rising on treatment), "
            "sputum AFB smear result. "
            "Assessment MUST say 'Pulmonary tuberculosis'. "
            "Treatment: 2RHZE intensive (isoniazid 300mg + rifampicin 600mg + pyrazinamide 1500mg + "
            "ethambutol 1200mg daily), then 4RH continuation."
        ),
        visit_progressions=[
            "Initial visit: cough >2 weeks, night sweats, weight loss, sputum AFB sent. "
            "Start RHZE if AFB positive. Include weight, temp, SpO2.",
            "2-week: sputum AFB result confirmed, adherence, weight, side effects review.",
            "2-month end of intensive phase: smear conversion, weight gain, switch to RH.",
            "4-month review on continuation: adherence, weight rising, no adverse effects.",
            "End of treatment (6 months): declare outcome, weight at maximum.",
            "Extra monitoring if drug resistance suspected or side effects noted.",
            "7-month follow-up if extended treatment: check resistance, repeat cultures.",
            "Post-treatment: check for relapse, weight stable.",
        ],
    ),
    Scenario(
        key="hiv",
        label="HIV disease",
        sex="any", age_min=18, age_max=55,
        num_patients=10, visit_min=4, visit_max=8,
        system_context=(
            "Patient with HIV at an LMIC ART clinic. "
            "Include CD4 count (cells/μL, starts low, rises over visits) and weight. "
            "Assessment MUST say 'HIV disease'. "
            "Treatment: TDF/3TC/DTG (Tenofovir 300mg/Lamivudine 300mg/Dolutegravir 50mg) once daily. "
            "Show CD4 rising (e.g. 180→350→520→780) and viral load declining to undetectable."
        ),
        visit_progressions=[
            "Enrolment: newly diagnosed HIV, WHO stage II–III, baseline CD4, weight, start TDF/3TC/DTG.",
            "1-month: adherence, side effects, weight, CD4 trend.",
            "3-month: weight gaining, CD4 improving, viral load pending.",
            "6-month: viral load result (ideally <50 copies/mL), CD4 count.",
            "12-month: viral suppression confirmed, CD4 normalising.",
            "18-month: stable on ART, CD4>500, annual review.",
            "24-month: long-term stable, differentiated service delivery.",
            "Extra visit if opportunistic infection episode.",
        ],
    ),
    Scenario(
        key="hypertension",
        label="Essential hypertension",
        sex="any", age_min=35, age_max=75,
        num_patients=15, visit_min=3, visit_max=6,
        system_context=(
            "Patient with hypertension at a district chronic disease clinic. "
            "Include BP (systolic 140–180, diastolic 90–110 at first, improving on treatment), "
            "pulse, weight. "
            "Assessment MUST say 'Essential hypertension'. "
            "Treatment: Amlodipine 5–10mg once daily; add Hydrochlorothiazide 25mg if uncontrolled."
        ),
        visit_progressions=[
            "New diagnosis: BP ≥160/100, headache or dizziness, initiate Amlodipine 5mg daily.",
            "1-month review: BP still elevated, increase Amlodipine or add Hydrochlorothiazide 25mg.",
            "3-month: BP improving (140–155/88–95), reinforce adherence.",
            "6-month: BP controlled (<140/90), medication stable.",
            "Annual review: BP stable, screen for end-organ damage.",
            "Extra visit if hypertensive urgency: BP >180/110.",
        ],
    ),
    Scenario(
        key="anc",
        label="Antenatal care",
        sex="F", age_min=16, age_max=40,
        num_patients=12, visit_min=4, visit_max=8,
        system_context=(
            "Pregnant woman attending antenatal care at an LMIC health centre. "
            "Include gestational age (weeks), fundal height (cm), Hgb (g/dL), "
            "fetal heart rate (bpm), weight, BP. "
            "Assessment MUST say 'Antenatal care' (or 'Pregnancy with anaemia' if Hgb <10). "
            "Medications: Folic acid 5mg daily, Ferrous sulphate 200mg daily, "
            "Sulfadoxine-pyrimethamine for malaria prophylaxis."
        ),
        visit_progressions=[
            "1st ANC (8–12 weeks): confirm pregnancy, baseline Hgb, start folic acid + iron.",
            "2nd ANC (16–20 weeks): fundal height, fetal heart rate, Hgb check, SP dose 1.",
            "3rd ANC (24–28 weeks): glucose screen, Hgb, SP dose 2, fetal movement.",
            "4th ANC (32 weeks): fetal presentation, Hgb, discuss delivery plan.",
            "5th ANC (36 weeks): pre-labour assessment, birth preparedness.",
            "6th ANC (38 weeks): final pre-delivery visit, fetal position.",
            "Complication visit: anaemia, hypertension in pregnancy, or reduced fetal movement.",
            "Extra high-risk visit if multiple concerns.",
        ],
    ),
    Scenario(
        key="sam",
        label="Severe acute malnutrition",
        sex="any", age_min=0, age_max=5,
        num_patients=8, visit_min=3, visit_max=5,
        system_context=(
            "Child under 5 with severe acute malnutrition at a nutrition rehabilitation unit. "
            "Include MUAC in mm (<115mm at admission), weight in kg, "
            "bilateral pitting oedema (present/absent/resolving). "
            "Assessment MUST say 'Severe acute malnutrition'. "
            "Treatment: RUTF 200 kcal/kg/day, Amoxicillin 80mg/kg/day for 7 days, "
            "Vitamin A 200,000 IU once."
        ),
        visit_progressions=[
            "Admission: MUAC <115mm, bilateral oedema ++, poor appetite, start RUTF + amoxicillin.",
            "Week 1: oedema resolving, appetite returning (RUTF appetite test passed).",
            "Week 2: weight gain beginning, MUAC improving, complete amoxicillin.",
            "Month 1: graduated SAM→MAM (MUAC 115–125mm), weight gaining.",
            "Discharge: MUAC >125mm, weight stable, graduated to supplementary feeding.",
        ],
    ),
    Scenario(
        key="diabetes",
        label="Type 2 diabetes mellitus",
        sex="any", age_min=30, age_max=70,
        num_patients=10, visit_min=3, visit_max=6,
        system_context=(
            "Patient with type 2 diabetes at a district chronic disease clinic. "
            "Include fasting glucose (mmol/L, starts 11–18, improves on treatment) "
            "and HbA1c (%) where available. Weight, BP. "
            "Assessment MUST say 'Type 2 diabetes mellitus'. "
            "Treatment: Metformin 500–1000mg twice daily; "
            "add Glibenclamide 5mg daily if HbA1c >8% at 3 months."
        ),
        visit_progressions=[
            "New diagnosis: fasting glucose ≥11 mmol/L, polyuria, polydipsia. "
            "Start Metformin 500mg twice daily, lifestyle advice.",
            "1-month: glucose still elevated (8–11), increase Metformin to 1000mg BD.",
            "3-month: HbA1c result, fasting glucose trend, add Glibenclamide if HbA1c >8%.",
            "6-month: glycaemic control improving, screen for complications.",
            "Annual: stable control, complication screening.",
            "Extra visit if hypoglycaemia or acute decompensation.",
        ],
    ),
    Scenario(
        key="typhoid",
        label="Typhoid fever",
        sex="any", age_min=5, age_max=35,
        num_patients=8, visit_min=2, visit_max=4,
        system_context=(
            "Patient with typhoid fever at an LMIC district hospital. "
            "Include temperature (38.5–40°C), relative bradycardia, Widal test result (positive). "
            "Assessment MUST say 'Typhoid fever'. "
            "Treatment: Ciprofloxacin 500mg twice daily for 10–14 days."
        ),
        visit_progressions=[
            "Presentation: persistent fever 7–14 days, headache, abdominal pain, "
            "Widal positive. Start Ciprofloxacin.",
            "Day 5–7: fever defervescence, improving, continue antibiotic course.",
            "Day 14: full recovery, complete course, hygiene counselling.",
            "Complication if intestinal perforation: acute abdomen, surgical referral.",
        ],
    ),
    Scenario(
        key="sickle_cell",
        label="Sickle cell disease",
        sex="any", age_min=5, age_max=30,
        num_patients=8, visit_min=3, visit_max=6,
        system_context=(
            "Patient with sickle cell disease at a haematology clinic. "
            "Include Hgb (g/dL, typically 6–9 at baseline), "
            "pain crisis locations (joints, chest, abdomen), temperature. "
            "Assessment MUST say 'Sickle cell disease'. "
            "Treatment: Hydroxyurea 15mg/kg/day, Folic acid 5mg daily, "
            "analgesia (Ibuprofen 400mg + Tramadol 50mg) for crisis."
        ),
        visit_progressions=[
            "Acute crisis: severe bone/joint pain, pallor, Hgb low, IV fluids + analgesia.",
            "Day 3–5: pain improving, Hgb stable or post-transfusion, oral analgesia.",
            "Outpatient 2 weeks: pain-free, Hgb at chronic baseline, hydroxyurea review.",
            "Routine 3-month: Hgb, hydroxyurea adherence, organ damage screen.",
            "6-month: Hgb trend on hydroxyurea (MCV rising = good response).",
            "Annual: comprehensive review, growth, organ damage screen.",
        ],
    ),
    Scenario(
        key="child_ari",
        label="Acute respiratory infection",
        sex="any", age_min=0, age_max=10,
        num_patients=7, visit_min=2, visit_max=4,
        system_context=(
            "Child under 10 with acute respiratory infection / pneumonia at an outpatient clinic. "
            "Include respiratory rate (fast breathing: >50/min for <1yr, >40/min for 1–5yr), "
            "temperature, SpO2, weight, chest indrawing (present/absent). "
            "Assessment MUST say 'Pneumonia' or 'Acute upper respiratory tract infection'. "
            "Treatment: Amoxicillin 40mg/kg/day in 2 doses for 5 days."
        ),
        visit_progressions=[
            "Presentation: fever, cough, fast breathing, assess severity. "
            "Start Amoxicillin if pneumonia. Include RR, temperature, SpO2, weight.",
            "Day 3 review: improving (RR normalising, fever resolving) or deteriorating.",
            "Day 7: recovery confirmed, nutritional assessment, immunisations.",
            "Return if severe: SpO2 dropping, refer for hospital admission.",
        ],
    ),
]

# Total: 12+10+10+15+12+8+10+8+8+7 = 100


# ---------------------------------------------------------------------------
# Utility

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def iso_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def openmrs_auth() -> str:
    if not OPENMRS_PASS:
        raise RuntimeError(
            "OpenMRS password not set. Set OPENMRS_SERVICE_PASSWORD or OPENMRS_ADMIN_PASSWORD."
        )
    tok = base64.b64encode(f"{OPENMRS_USER}:{OPENMRS_PASS}".encode()).decode()
    return f"Basic {tok}"


def http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    payload = json.dumps(body).encode() if body is not None else None
    h: dict[str, str] = {"Accept": "application/json"}
    if payload:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=payload, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        body_err = exc.read().decode(errors="replace")[:800]
        raise RuntimeError(f"HTTP {exc.code} {method} {url}: {body_err}") from exc


# ---------------------------------------------------------------------------
# OpenMRS REST helpers (import phase only)

def omrs(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return http_json(
        method,
        f"{OPENMRS_REST}/{path.lstrip('/')}",
        body,
        headers={"Authorization": openmrs_auth()},
    )


def omrs_first(path: str) -> dict[str, Any] | None:
    r = omrs("GET", path)
    results = r.get("results") or []
    return results[0] if results else None


def resolve_location() -> str:
    for name in ("Outpatient", "Outpatient Clinic", "Reception", "Unknown Location"):
        hit = omrs_first(f"location?q={urllib.parse.quote(name)}&v=default&limit=5")
        if hit and not hit.get("retired"):
            return str(hit["uuid"])
    hit = omrs_first("location?v=default&limit=1")
    if not hit:
        raise RuntimeError("No OpenMRS location found")
    return str(hit["uuid"])


def resolve_visit_type() -> str:
    hit = omrs_first("visittype?v=default&limit=1")
    if not hit:
        raise RuntimeError("No OpenMRS visit type found")
    return str(hit["uuid"])


def resolve_openmrs_id_type_and_source() -> tuple[str, str]:
    """Return (identifierType UUID, idgen source UUID) for OpenMRS ID."""
    type_uuid: str | None = None
    type_data = omrs("GET", "patientidentifiertype?v=default&limit=100")
    for e in type_data.get("results", []):
        if not e.get("retired") and "openmrs id" in (e.get("display") or e.get("name") or "").lower():
            type_uuid = str(e["uuid"])
            break
    if not type_uuid:
        raise RuntimeError("OpenMRS ID identifier type not found")
    source_data = omrs("GET", "idgen/identifiersource?v=full&limit=50")
    for e in source_data.get("results", []):
        itype = e.get("identifierType") or {}
        if itype.get("uuid") == type_uuid or "openmrs id" in (e.get("display") or e.get("name") or "").lower():
            return type_uuid, str(e["uuid"])
    raise RuntimeError("OpenMRS ID generator source not found")


def mint_openmrs_id(source_uuid: str) -> str:
    data = omrs("POST", f"idgen/identifiersource/{source_uuid}/identifier", {})
    identifier = data.get("identifier")
    if not identifier:
        raise RuntimeError("IDGen returned no identifier")
    return str(identifier)


def patient_exists(identifier: str) -> str | None:
    hit = omrs_first(f"patient?identifier={urllib.parse.quote(identifier)}&v=default")
    return str(hit["uuid"]) if hit and hit.get("uuid") else None


def create_patient_omrs(
    rec: dict[str, Any],
    omrs_id_type_uuid: str,
    omrs_id_source_uuid: str,
    location_uuid: str,
) -> str:
    existing = patient_exists(rec["identifier"])
    if existing:
        log(f"    Patient {rec['identifier']} already exists: {existing}")
        return existing
    openmrs_id = mint_openmrs_id(omrs_id_source_uuid)
    data = omrs("POST", "patient", {
        "person": {
            "names": [{"givenName": rec["given_name"], "familyName": rec["family_name"], "preferred": True}],
            "gender": rec["gender"],
            "birthdate": rec["birthdate"],
            "birthdateEstimated": False,
        },
        "identifiers": [
            {
                "identifier": openmrs_id,
                "identifierType": omrs_id_type_uuid,
                "location": location_uuid,
                "preferred": True,
            },
            {
                "identifier": rec["identifier"],
                "identifierType": _free_id_type_uuid(location_uuid),
                "location": location_uuid,
                "preferred": False,
            },
        ],
    })
    if not data.get("uuid"):
        raise RuntimeError(f"Patient creation failed: {data}")
    return str(data["uuid"])


_free_id_type_cache: dict[str, str] = {}


def _free_id_type_uuid(location_uuid: str) -> str:
    if "free" not in _free_id_type_cache:
        data = omrs("GET", "patientidentifiertype?v=default&limit=100")
        candidates = [e for e in data.get("results", []) if not e.get("retired") and not e.get("validator")]
        for e in candidates:
            display = (e.get("display") or e.get("name") or "").lower()
            if any(t in display for t in ("old", "national", "identification")):
                _free_id_type_cache["free"] = str(e["uuid"])
                break
        if "free" not in _free_id_type_cache and candidates:
            _free_id_type_cache["free"] = str(candidates[-1]["uuid"])
        if "free" not in _free_id_type_cache:
            raise RuntimeError("No unvalidated patient identifier type found")
    return _free_id_type_cache["free"]


CLINICAL_NOTE_ENCOUNTER_TYPE = os.getenv(
    "CLINICAL_NOTE_ENCOUNTER_TYPE_UUID", "d7151f82-c1f3-4152-a605-2f9ea7414a79"
)
CLINICAL_NOTE_CONCEPT = os.getenv(
    "CLINICAL_NOTE_CONCEPT_UUID", "162169AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)

# Numeric CIEL concept IDs that are guaranteed to exist in the OpenMRS CIEL dictionary
# and have a Numeric datatype — safe to write as obs values.
SAFE_NUMERIC_CIEL_IDS: set[str] = {
    "5088",   # Temperature (°C)
    "5085",   # Systolic blood pressure
    "5086",   # Diastolic blood pressure
    "5087",   # Pulse
    "5242",   # Respiratory rate
    "5092",   # Oxygen saturation (SpO2)
    "5089",   # Weight (kg)
    "5090",   # Height (cm)
    "857",    # Haemoglobin (g/dL)
    "887",    # Blood glucose (random)
    "160912", # Fasting blood glucose
    "5497",   # CD4 count (cells/μL)
    "856",    # HIV viral load
    "1015",   # MUAC (mm)
    "165395", # HbA1c (%)
    "5088",   # Temperature (duplicate guard)
    "163590", # Fundal height (cm)
    "1396",   # Fetal heart rate (bpm)
}


def _ciel_to_uuid(ciel_id: str) -> str:
    """Pad a CIEL numeric ID to a 36-char OpenMRS concept UUID."""
    s = str(ciel_id).strip()
    return s.ljust(36, "A") if len(s) < 36 else s


def get_or_create_visit(
    patient_uuid: str,
    visit_type_uuid: str,
    location_uuid: str,
    start_iso: str,
    stop_iso: str,
) -> str:
    """Return existing visit near the target date, or create a new one."""
    # Fetch all visits for this patient
    resp = omrs("GET", f"visit?patient={patient_uuid}&v=default&limit=100&includeInactive=true")
    existing = resp.get("results") or []

    # Parse target start date for comparison (date only)
    try:
        target_date = start_iso[:10]  # "YYYY-MM-DD"
    except Exception:
        target_date = ""

    for v in existing:
        v_start = str(v.get("startDatetime") or "")[:10]
        if v_start == target_date:
            return str(v["uuid"])

    # No match — create new visit
    data = omrs("POST", "visit", {
        "patient": patient_uuid,
        "visitType": visit_type_uuid,
        "location": location_uuid,
        "startDatetime": start_iso,
        "stopDatetime": stop_iso,
    })
    if not data.get("uuid"):
        raise RuntimeError(f"Visit creation returned no uuid: {data}")
    return str(data["uuid"])


def import_encounter_direct(
    patient_uuid: str,
    visit_uuid: str,
    location_uuid: str,
    enc: dict[str, Any],
) -> str:
    """Write one generated encounter directly to OpenMRS REST (bypasses confirm_text).

    Uses the encounter_datetime from the JSON but shifts it 30 min inside the
    visit window to satisfy OpenMRS date-range validation.
    """
    raw_dt = enc.get("encounter_datetime") or ""
    try:
        enc_dt = datetime.fromisoformat(raw_dt.replace("+0000", "+00:00"))
    except ValueError:
        enc_dt = datetime.now(timezone.utc)

    # Shift 30 min past visit start so encounter is strictly inside [start, stop]
    enc_dt_shifted = enc_dt + timedelta(minutes=30)
    enc_dt_iso = iso_dt(enc_dt_shifted)

    # Build obs list: SOAP text note + numeric vitals/labs
    obs_list: list[dict[str, Any]] = []
    soap_text = enc.get("soap_text") or ""
    if soap_text:
        obs_list.append({"concept": CLINICAL_NOTE_CONCEPT, "value": soap_text})

    for o in enc.get("coded_obs") or []:
        ciel_id = str(o.get("ciel_id") or "").strip()
        value = o.get("value")
        if not ciel_id or value is None:
            continue
        # Only write obs for known safe numeric CIEL concepts
        if ciel_id not in SAFE_NUMERIC_CIEL_IDS:
            continue
        # Value must be numeric
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        concept_uuid = _ciel_to_uuid(ciel_id)
        obs_list.append({"concept": concept_uuid, "value": value})

    enc_payload: dict[str, Any] = {
        "patient": patient_uuid,
        "visit": visit_uuid,
        "encounterType": CLINICAL_NOTE_ENCOUNTER_TYPE,
        "encounterDatetime": enc_dt_iso,
        "location": location_uuid,
        "obs": obs_list,
    }
    enc_result = omrs("POST", "encounter", enc_payload)
    if not enc_result.get("uuid"):
        raise RuntimeError(f"Encounter creation returned no uuid: {str(enc_result)[:300]}")
    encounter_uuid = str(enc_result["uuid"])

    # POST a Condition for each coded diagnosis
    onset_date = enc_dt.strftime("%Y-%m-%d")
    for dx in enc.get("coded_diagnoses") or []:
        ciel_id = str(dx.get("ciel_id") or "").strip()
        if not ciel_id:
            continue
        concept_uuid = _ciel_to_uuid(ciel_id)
        try:
            omrs("POST", "condition", {
                "patient": patient_uuid,
                "condition": {"coded": concept_uuid},
                "clinicalStatus": "ACTIVE",
                "onsetDate": onset_date,
            })
        except Exception as cond_exc:
            log(f"      Condition warning ({dx.get('label')}): {cond_exc}")

    return encounter_uuid


# ---------------------------------------------------------------------------
# Vertex AI / DeepSeek client

_vertex_token_cache: dict[str, Any] = {}


def _gcloud_token() -> str | None:
    try:
        out = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def get_vertex_token() -> str:
    env_token = os.getenv("VERTEX_ACCESS_TOKEN", "").strip()
    if env_token:
        return env_token
    cached = _vertex_token_cache.get("token")
    cached_at = float(_vertex_token_cache.get("at", 0))
    if cached and (time.time() - cached_at) < 1800:
        return str(cached)
    token = _gcloud_token()
    if not token:
        raise RuntimeError(
            "No Vertex AI access token.\n"
            "  export VERTEX_ACCESS_TOKEN=$(gcloud auth print-access-token)"
        )
    _vertex_token_cache["token"] = token
    _vertex_token_cache["at"] = time.time()
    return token


DEEPSEEK_SYSTEM_PROMPT = (
    "You are an experienced clinical officer documenting patient encounters at a busy "
    "district hospital in sub-Saharan Africa or South Asia.\n\n"
    "RULES FOR EVERY NOTE:\n"
    "1. Include explicit numeric vitals: Temperature X.X°C, BP XXX/XX mmHg, Pulse XX bpm, "
    "RR XX/min, SpO2 XX%, Weight X.X kg (all that are clinically relevant).\n"
    "2. For lab results, state value and units: Hgb X.X g/dL, Fasting glucose X.X mmol/L, "
    "CD4 count XXXX cells/μL, MUAC XXX mm, etc.\n"
    "3. Assessment MUST use one of these exact diagnosis names: "
    "'Plasmodium falciparum malaria', 'Pulmonary tuberculosis', 'HIV disease', "
    "'Essential hypertension', 'Antenatal care', 'Severe acute malnutrition', "
    "'Type 2 diabetes mellitus', 'Typhoid fever', 'Sickle cell disease', "
    "'Pneumonia', 'Acute upper respiratory tract infection'.\n"
    "4. Plan MUST name the specific drug with dose, frequency, and duration "
    "(e.g. 'Artemether-lumefantrine 80/480mg twice daily for 3 days', "
    "'Metformin 500mg twice daily', 'Amoxicillin 250mg three times daily for 5 days').\n"
    "5. Write as a flowing clinical narrative (NOT with SOAP headers).\n"
    "6. Be realistic for a resource-limited LMIC setting.\n"
    "7. Vary numeric values naturally between visits to show disease progression and treatment response.\n"
    "8. Return ONLY the clinical note text. No preamble, no explanation, no markdown."
)


def deepseek_generate(messages: list[dict[str, str]]) -> str:
    url = f"{VERTEX_API_BASE}/chat/completions"
    body: dict[str, Any] = {
        "model": VERTEX_MODEL,
        "messages": messages,
        "max_tokens": 2000,
        "temperature": 0.85,
    }
    # Retry with exponential backoff for 429 rate-limit and transient errors
    for attempt in range(6):
        try:
            token = get_vertex_token()
            resp = http_json("POST", url, body, headers={"Authorization": f"Bearer {token}"}, timeout=120)
            content = (
                (resp.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not content:
                raise RuntimeError(f"DeepSeek returned empty content: {str(resp)[:400]}")
            # Strip DeepSeek-R1 chain-of-thought blocks
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            # Hard cap: scribe works best on focused notes under 2000 chars
            if len(content) > 2500:
                content = content[:2500].rsplit(".", 1)[0] + "."
            return content
        except RuntimeError as exc:
            msg = str(exc)
            is_rate_limit = "429" in msg or "RESOURCE_EXHAUSTED" in msg
            is_transient = any(c in msg for c in ("500", "502", "503", "504"))
            if (is_rate_limit or is_transient) and attempt < 5:
                wait = 30 * (2 ** attempt)  # 30s, 60s, 120s, 240s, 480s
                log(f"    DeepSeek {'rate-limited' if is_rate_limit else 'transient error'} (attempt {attempt+1}/6). Waiting {wait}s...")
                time.sleep(wait)
                # Force token refresh on next attempt
                _vertex_token_cache.clear()
                continue
            raise
    raise RuntimeError("DeepSeek failed after 6 attempts")


def build_note_messages(
    scenario: Scenario,
    visit_num: int,
    patient_label: str,
    age: int,
    sex_label: str,
    prior_summaries: list[dict[str, Any]],
) -> list[dict[str, str]]:
    progression = ""
    if visit_num - 1 < len(scenario.visit_progressions):
        progression = scenario.visit_progressions[visit_num - 1]

    prior_ctx = ""
    if prior_summaries:
        prior_ctx = "\n\nPrevious visit summaries (maintain clinical continuity):\n"
        for ps in prior_summaries:
            prior_ctx += f"  Visit {ps['visit_num']} ({ps['date']}): {str(ps.get('note', ''))[:500]}\n"

    user_msg = (
        f"Patient: {patient_label}, {age} years old, {sex_label}.\n"
        f"Clinical scenario: {scenario.label}.\n"
        f"Clinical context: {scenario.system_context}\n"
        f"This is visit {visit_num}.\n"
        f"Visit focus: {progression}"
        + prior_ctx
        + "\n\nWrite the complete clinical encounter note for this visit."
    )
    return [
        {"role": "system", "content": DEEPSEEK_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


# ---------------------------------------------------------------------------
# TenaAgent scribe (generate phase)

def scribe_process_text(note_text: str, patient_uuid: str = "") -> dict[str, Any]:
    url = f"{AGENT_BASE}/scribe/process_text"
    body: dict[str, Any] = {"noteText": note_text}
    if patient_uuid:
        body["patientUuid"] = patient_uuid
    headers: dict[str, str] = {}
    if OPENMRS_PASS:
        headers["Authorization"] = openmrs_auth()
    resp = http_json("POST", url, body, headers=headers or None, timeout=300)
    if "error" in resp:
        raise RuntimeError(f"Scribe process_text error: {resp['error']}")
    return resp


# ---------------------------------------------------------------------------
# Data file I/O

def load_data_file(path: str) -> dict[str, Any]:
    if Path(path).exists():
        with open(path) as f:
            return json.load(f)
    return {"patients": [], "meta": {"generated_at": None, "total": 0}}


def save_data_file(path: str, data: dict[str, Any]) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Patient spec generation

@dataclass
class PatientSpec:
    identifier: str
    given_name: str
    family_name: str
    gender: str
    age: int
    birthdate: str
    scenario: Scenario
    num_visits: int
    visit_dates: list[str]   # ISO strings


def build_patient_list(count: int, rng: random.Random) -> list[PatientSpec]:
    patients: list[PatientSpec] = []
    idx = 1
    for scenario in SCENARIOS:
        for _ in range(scenario.num_patients):
            if len(patients) >= count:
                break
            gender = (
                "F" if scenario.sex == "F"
                else "M" if scenario.sex == "M"
                else ("F" if idx % 2 == 0 else "M")
            )
            given_pool = GIVEN_NAMES_F if gender == "F" else GIVEN_NAMES_M
            given = given_pool[idx % len(given_pool)]
            family = FAMILY_NAMES[(idx * 3) % len(FAMILY_NAMES)]
            age = rng.randint(max(1, scenario.age_min), scenario.age_max)

            today = datetime.now(timezone.utc).date()
            born = today.replace(year=today.year - age) - timedelta(days=rng.randint(0, 300))

            num_visits = rng.randint(scenario.visit_min, scenario.visit_max)
            max_months_ago = min(24, max(3, num_visits * 3))
            first_ago = timedelta(days=rng.randint(30 * (max_months_ago // 2), 30 * max_months_ago))
            first_visit = datetime.now(timezone.utc) - first_ago

            visit_dates: list[str] = [iso_dt(first_visit)]
            for _ in range(1, num_visits):
                gap = timedelta(weeks=rng.randint(2, 8))
                prev = datetime.fromisoformat(visit_dates[-1].replace("+0000", "+00:00"))
                nxt = prev + gap
                if nxt > datetime.now(timezone.utc) - timedelta(days=1):
                    nxt = datetime.now(timezone.utc) - timedelta(days=rng.randint(1, 7))
                visit_dates.append(iso_dt(nxt))

            patients.append(PatientSpec(
                identifier=f"TENAOS-SYNTH-{idx:03d}",
                given_name=given,
                family_name=family,
                gender=gender,
                age=age,
                birthdate=born.isoformat(),
                scenario=scenario,
                num_visits=num_visits,
                visit_dates=visit_dates,
            ))
            idx += 1
        if len(patients) >= count:
            break
    return patients[:count]


# ---------------------------------------------------------------------------
# PHASE 1 — Generate

def generate_patient_record(
    spec: PatientSpec,
    rng: random.Random,
    verbose: bool = False,
) -> dict[str, Any]:
    sex_label = "Female" if spec.gender == "F" else "Male"
    patient_label = f"{spec.given_name} {spec.family_name}"
    log(f"  {spec.identifier} | {patient_label} | {spec.age}y {sex_label} | {spec.scenario.label}")

    prior_summaries: list[dict[str, Any]] = []
    visits: list[dict[str, Any]] = []

    for v_idx, visit_dt_iso in enumerate(spec.visit_dates):
        visit_num = v_idx + 1
        visit_dt = datetime.fromisoformat(visit_dt_iso.replace("+0000", "+00:00"))
        stop_dt = visit_dt + timedelta(hours=rng.randint(1, 3))

        log(f"    Visit {visit_num}/{spec.num_visits} ({visit_dt.strftime('%Y-%m-%d')}): DeepSeek...")
        messages = build_note_messages(
            spec.scenario, visit_num, patient_label, spec.age, sex_label, prior_summaries
        )
        note = deepseek_generate(messages)
        log(f"    Note: {len(note)} chars. Scribe...")

        if verbose:
            print(f"\n  --- NOTE (visit {visit_num}) ---\n{note}\n  ---\n")

        scribe = scribe_process_text(note)

        concepts = scribe.get("concepts") or []
        obs = scribe.get("observations") or []
        meds = scribe.get("medications") or []

        log(f"    Concepts: {[c.get('label') for c in concepts]}")
        log(f"    Obs: {[(o.get('label'), o.get('value')) for o in obs]}")
        log(f"    Meds: {[m.get('label') for m in meds]}")

        if verbose:
            soap = scribe.get("soap") or {}
            print("  SOAP:")
            for s in ("subjective", "objective", "assessment", "plan"):
                print(f"    {s.upper()}: {str(soap.get(s, ''))[:300]}")
            print()

        visit_record: dict[str, Any] = {
            "visit_start": iso_dt(visit_dt),
            "visit_stop": iso_dt(stop_dt),
            "encounters": [
                {
                    "encounter_datetime": iso_dt(visit_dt),
                    "soap_text": scribe.get("soapText") or "",
                    "soap": scribe.get("soap") or {},
                    "concept_uuids": [
                        str(c.get("uuid") or "") for c in concepts if c.get("uuid")
                    ],
                    "observations": obs,
                    "medications": meds,
                    "raw_note": note,
                    # Diagnosis labels for review / reporting
                    "coded_diagnoses": [
                        {"ciel_id": c.get("uuid"), "label": c.get("label")} for c in concepts
                    ],
                    "coded_obs": [
                        {"ciel_id": o.get("uuid"), "label": o.get("label"), "value": o.get("value")}
                        for o in obs
                    ],
                }
            ],
        }
        visits.append(visit_record)
        prior_summaries.append({
            "visit_num": visit_num,
            "date": visit_dt.strftime("%Y-%m-%d"),
            "note": note[:400],
        })

    return {
        "identifier": spec.identifier,
        "given_name": spec.given_name,
        "family_name": spec.family_name,
        "gender": spec.gender,
        "birthdate": spec.birthdate,
        "age": spec.age,
        "scenario": spec.scenario.key,
        "scenario_label": spec.scenario.label,
        "visits": visits,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "openmrs_uuid": None,   # filled during import phase
        "imported": False,
    }


def run_generate(args: argparse.Namespace) -> int:
    count = 1 if args.test else args.count
    rng = random.Random(args.seed)

    log("Resolving Vertex AI token...")
    try:
        tok = get_vertex_token()
        log(f"  Token OK ({tok[:20]}...)")
    except RuntimeError as exc:
        log(f"ERROR: {exc}")
        return 1

    log(f"Building patient list ({count} patients)...")
    patients = build_patient_list(count, rng)

    if args.test:
        spec = patients[0]
        spec.num_visits = 1
        spec.visit_dates = spec.visit_dates[:1]
        log(f"\n=== TEST MODE: 1 patient, 1 visit ===")
        rec = generate_patient_record(spec, rng, verbose=True)
        log("\n=== Generation complete. Review SOAP + coded obs above. ===")
        log(f"To save: re-run with --generate (removes --test)")
        return 0

    data = load_data_file(args.out)
    done_ids = {p["identifier"] for p in data.get("patients", [])}

    for i, spec in enumerate(patients):
        if spec.identifier in done_ids:
            log(f"[{i+1}/{count}] Skipping {spec.identifier} (already generated)")
            continue
        log(f"\n[{i+1}/{count}]")
        try:
            rec = generate_patient_record(spec, rng, verbose=args.verbose)
            data.setdefault("patients", []).append(rec)
            data["meta"] = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total": len(data["patients"]),
                "count_requested": count,
            }
            save_data_file(args.out, data)
            log(f"  Saved to {args.out} ({len(data['patients'])} total)")
        except Exception as exc:
            log(f"  ERROR: {exc}")
            continue

    log(f"\nGeneration complete. {len(data['patients'])} patients in {args.out}")
    log(f"Next step: python seed_synthetic_patients.py --import {args.out}")
    return 0


# ---------------------------------------------------------------------------
# PHASE 2 — Import

def run_import(args: argparse.Namespace) -> int:
    import_file = args.import_file
    if not Path(import_file).exists():
        log(f"ERROR: File not found: {import_file}")
        return 1

    with open(import_file) as f:
        data = json.load(f)

    patients = data.get("patients") or []
    log(f"Loading {len(patients)} patients from {import_file}")

    if not OPENMRS_PASS:
        log("ERROR: OPENMRS password not configured.")
        log("  Set OPENMRS_SERVICE_PASSWORD or OPENMRS_ADMIN_PASSWORD.")
        return 1

    log("Resolving OpenMRS metadata...")
    visit_type_uuid = resolve_visit_type()
    location_uuid = resolve_location()
    omrs_id_type_uuid, omrs_id_source_uuid = resolve_openmrs_id_type_and_source()
    log(f"  visit_type={visit_type_uuid}")
    log(f"  location={location_uuid}")
    log(f"  omrs_id_type={omrs_id_type_uuid}")

    done = 0
    failed = 0

    for i, rec in enumerate(patients):
        if rec.get("imported"):
            log(f"[{i+1}/{len(patients)}] {rec['identifier']} already imported, skipping")
            done += 1
            continue

        log(f"\n[{i+1}/{len(patients)}] {rec['identifier']} — {rec.get('scenario_label')} — {len(rec.get('visits', []))} visits")
        try:
            patient_uuid = create_patient_omrs(
                rec, omrs_id_type_uuid, omrs_id_source_uuid, location_uuid
            )
            log(f"  patient_uuid={patient_uuid}")

            for v_idx, visit in enumerate(rec.get("visits") or []):
                # Re-use visit UUID from a previous (partial) run if available
                visit_uuid = visit.get("omrs_visit_uuid") or ""
                if not visit_uuid:
                    visit_uuid = get_or_create_visit(
                        patient_uuid, visit_type_uuid, location_uuid,
                        visit["visit_start"], visit["visit_stop"],
                    )
                    visit["omrs_visit_uuid"] = visit_uuid
                    save_data_file(import_file, data)
                log(f"  visit {v_idx+1}: {visit_uuid}")

                for e_idx, enc in enumerate(visit.get("encounters") or []):
                    # Skip if already imported in a previous run
                    if enc.get("omrs_encounter_uuid"):
                        log(f"    encounter {e_idx+1}: already imported, skipping")
                        continue
                    enc_uuid = import_encounter_direct(
                        patient_uuid, visit_uuid, location_uuid, enc
                    )
                    enc["omrs_encounter_uuid"] = enc_uuid
                    save_data_file(import_file, data)
                    log(f"    encounter {e_idx+1}: {enc_uuid} ({enc.get('encounter_datetime', '')[:10]})")

            rec["openmrs_uuid"] = patient_uuid
            rec["imported"] = True
            save_data_file(import_file, data)
            done += 1

        except Exception as exc:
            log(f"  ERROR: {exc}")
            failed += 1

    log(f"\nImport complete. Done: {done}, Failed: {failed}")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# Main

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Two-phase synthetic LMIC patient seeder: generate JSON, then import to OpenMRS"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate", action="store_true",
                      help="Generate patient data to JSON file (default mode)")
    mode.add_argument("--import", dest="import_file", metavar="FILE",
                      help="Import previously generated JSON file into OpenMRS")
    mode.add_argument("--test", action="store_true",
                      help="Generate 1 patient dry-run, print note + SOAP, no file write")

    parser.add_argument("--out", default=DEFAULT_OUT,
                        help=f"Output JSON file for generate phase (default: {DEFAULT_OUT})")
    parser.add_argument("--count", type=int, default=100,
                        help="Number of patients to generate (default 100)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip patients already in the output file")
    parser.add_argument("--verbose", action="store_true",
                        help="Print note text and SOAP for each visit")
    parser.add_argument("--seed", type=int, default=20260604,
                        help="Random seed (default 20260604)")

    args = parser.parse_args()

    log("TenaOS Synthetic Patient Seeder")
    log(f"  TenaAgent: {AGENT_BASE}")
    log(f"  Vertex AI: {VERTEX_API_BASE}")
    log(f"  Model:     {VERTEX_MODEL}")

    if args.import_file:
        return run_import(args)
    else:
        return run_generate(args)


if __name__ == "__main__":
    sys.exit(main())
