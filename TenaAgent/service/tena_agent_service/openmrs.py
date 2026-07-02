from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any

from .config import Settings


@dataclass
class PatientInsightContext:
    patient_uuid: str
    demographics: dict[str, Any]
    active_visit: dict[str, Any] | None
    summary_counts: dict[str, int]
    clinical_evidence: dict[str, Any]
    medications: list[dict[str, Any]] = None  # type: ignore[assignment]
    lab_orders: list[dict[str, Any]] = None   # type: ignore[assignment]
    workflow: str = "patient-chart-insight"

    def __post_init__(self) -> None:
        if self.medications is None:
            self.medications = []
        if self.lab_orders is None:
            self.lab_orders = []

    def to_model_context(self) -> dict[str, Any]:
        return {
            "patientUuid": self.patient_uuid,
            "workflow": self.workflow,
            "demographics": self.demographics,
            "activeVisit": self.active_visit,
            "summaryCounts": self.summary_counts,
            "clinicalEvidence": self.clinical_evidence,
        }

    def to_kb_query(self) -> str:
        """Return a concise plain-text summary of key clinical terms.

        Designed to help Gemma 4 identify what to search for in Phase 1.
        Short, specific terms work best in the WHO/MSF guidelines KB.
        """
        d = self.demographics
        age = d.get("ageYears")
        gender = d.get("gender") or ""
        age_label = ""
        if age is not None:
            if age < 5:
                age_label = "child under 5"
            elif age < 15:
                age_label = "child"
            elif age > 60:
                age_label = "elderly adult"
            else:
                age_label = "adult"

        signals = (self.clinical_evidence.get("signals") or {})
        signal_terms: list[str] = []
        if signals.get("tb"):
            signal_terms.append("TB")
        if signals.get("hiv"):
            signal_terms.append("HIV")
        if signals.get("pregnancy"):
            signal_terms.append("pregnancy")
        if signals.get("postnatal"):
            signal_terms.append("postnatal")

        # Pull first 6 snippets as clinical context
        snippets = (self.clinical_evidence.get("snippets") or [])[:6]

        parts: list[str] = []
        if age_label or gender:
            parts.append(f"{gender} {age_label}".strip())
        if signal_terms:
            parts.append(", ".join(signal_terms))
        if snippets:
            parts.append("; ".join(snippets))

        visit = self.active_visit
        if visit:
            vtype = visit.get("visitType") or ""
            if vtype:
                parts.append(vtype)

        return " | ".join(p for p in parts if p)


class OpenMrsClient:
    def __init__(self, settings: Settings, authorization: str | None = None, cookie: str | None = None):
        self.settings = settings
        self.authorization = authorization or _basic_auth_from_env()
        self.cookie = cookie

    def get_rest(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get(f"{self.settings.openmrs_rest_base_url}/{path.lstrip('/')}", params)

    def get_fhir(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get(f"{self.settings.openmrs_fhir_base_url}/{path.lstrip('/')}", params)

    def _post_rest(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.openmrs_rest_base_url}/{path.lstrip('/')}"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.settings.request_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Accept": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.settings.request_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def build_patient_context(self, patient_uuid: str) -> PatientInsightContext:
        patient = self.get_rest(f"/patient/{patient_uuid}", {"v": "full"})
        visits = self.get_rest("/visit", {"patient": patient_uuid, "includeInactive": "false", "v": "full"})
        encounters = self.get_rest("/encounter", {"patient": patient_uuid, "v": "default", "limit": 50})
        active_visit = _first_active_visit(visits.get("results", []))
        demographics = _demographics(patient)
        clinical_evidence = self._clinical_evidence(patient_uuid)
        medications = self._fetch_medications(patient_uuid)
        lab_orders = self._fetch_lab_orders(patient_uuid)
        return PatientInsightContext(
            patient_uuid=patient_uuid,
            demographics=demographics,
            active_visit=active_visit,
            clinical_evidence=clinical_evidence,
            medications=medications,
            lab_orders=lab_orders,
            summary_counts={
                "recentEncounters": len(encounters.get("results", [])),
                "activeConditions": _safe_count(lambda: self.get_rest("/condition", {"patientUuid": patient_uuid, "v": "default"})),
                "allergies": _safe_count(lambda: self.get_rest(f"/patient/{patient_uuid}/allergy", {"v": "default"})),
            },
        )

    def _fetch_medications(self, patient_uuid: str) -> list[dict[str, Any]]:
        """Fetch active medication obs (conceptClass=Drug) for the patient.

        In this OpenMRS configuration drugs are recorded as obs whose concept
        belongs to the 'Drug' concept class rather than as formal DrugOrders.
        We also fall back to scanning DrugOrder-type orders in case the
        deployment records them that way.
        """
        meds: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        # Strategy 1: obs where conceptClass == "Drug"
        try:
            obs_resp = self.get_rest(
                "/obs",
                {
                    "patient": patient_uuid,
                    "v": "custom:(concept:(display,uuid,conceptClass:(display)),value,obsDatetime)",
                    "limit": 100,
                },
            )
            for obs in obs_resp.get("results", []):
                concept = obs.get("concept") or {}
                cls = (concept.get("conceptClass") or {}).get("display", "")
                if cls != "Drug":
                    continue
                name = (concept.get("display") or "").strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                raw_val = obs.get("value")
                note = ""
                if isinstance(raw_val, dict):
                    note = raw_val.get("display") or raw_val.get("name") or ""
                elif raw_val:
                    note = str(raw_val)
                meds.append({
                    "name": name,
                    "note": note[:300],
                    "obsDatetime": obs.get("obsDatetime", ""),
                    "source": "obs",
                })
        except Exception:
            pass

        # Strategy 2: formal DrugOrder entries (dose + frequency available)
        try:
            order_resp = self.get_rest(
                "/order",
                {
                    "patient": patient_uuid,
                    "v": "full",
                    "limit": 20,
                },
            )
            for order in order_resp.get("results", []):
                if order.get("type") != "drugorder":
                    continue
                if order.get("voided"):
                    continue
                drug = order.get("drug") or {}
                concept = order.get("concept") or {}
                name = (drug.get("display") or concept.get("display") or "").strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                dose = order.get("dose")
                dose_units = (order.get("doseUnits") or {}).get("display", "")
                freq = (order.get("frequency") or {}).get("display", "")
                route = (order.get("route") or {}).get("display", "")
                dose_str = ""
                if dose:
                    dose_str = f"{dose} {dose_units}".strip()
                    if freq:
                        dose_str += f" {freq}"
                    if route:
                        dose_str += f" ({route})"
                meds.append({
                    "name": name,
                    "dose": dose_str,
                    "note": "",
                    "source": "drugorder",
                })
        except Exception:
            pass

        return meds

    def _fetch_lab_orders(self, patient_uuid: str) -> list[dict[str, Any]]:
        """Fetch recent lab test orders and their most recent result obs."""
        lab: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Fetch test orders
        try:
            order_resp = self.get_rest(
                "/order",
                {
                    "patient": patient_uuid,
                    "v": "full",
                    "limit": 20,
                },
            )
            for order in order_resp.get("results", []):
                if order.get("type") != "testorder":
                    continue
                if order.get("voided"):
                    continue
                concept = order.get("concept") or {}
                name = (concept.get("display") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                lab.append({
                    "test": name,
                    "status": order.get("action", ""),
                    "orderedDate": (order.get("dateActivated") or "")[:10],
                })
        except Exception:
            pass

        return lab[:15]

    def _clinical_evidence(self, patient_uuid: str) -> dict[str, Any]:
        conditions = _safe_results(lambda: self.get_rest("/condition", {"patientUuid": patient_uuid, "includeInactive": "true", "v": "full"}))
        encounters = _safe_results(
            lambda: self.get_rest(
                "/encounter",
                {
                    "patient": patient_uuid,
                    "v": "custom:(uuid,encounterDatetime,encounterType:(display),obs:(uuid,concept:(display),value))",
                    "limit": 20,
                },
            ),
        )
        snippets: list[str] = []
        for condition in conditions[:10]:
            snippets.append(str(condition.get("display") or condition.get("concept", {}).get("display") or ""))
        for encounter in encounters[:10]:
            for obs in (encounter.get("obs") or [])[:8]:
                concept = obs.get("concept", {}).get("display")
                value = obs.get("value")
                snippets.append(f"{concept}: {_compact_value(value)}")
        text = " ".join(snippets).lower()
        return {
            "snippets": [snippet for snippet in snippets if snippet][:30],
            "signals": {
                "tb": any(term in text for term in ("tb", "tuberculosis", "sputum", "rifampicin", "cxr")),
                "hiv": any(term in text for term in ("hiv", "plhiv", "antiretroviral", "viral load", "cd4")),
                "pregnancy": any(term in text for term in ("pregnant", "pregnancy", "antenatal", "gestational", "gravida")),
                "postnatal": any(term in text for term in ("postpartum", "postnatal", "delivery", "newborn", "neonate", "breastfeeding")),
            },
        }

    def request_patient_facts(self, patient_uuid: str, facts: list[str]) -> dict[str, Any]:
        context = self.build_patient_context(patient_uuid)
        resolved: dict[str, Any] = {}
        missing: list[str] = []
        for fact in facts:
            if fact == "age":
                age = context.demographics.get("ageYears")
                if age is None:
                    missing.append(fact)
                else:
                    resolved["age"] = {"value": age, "unit": "years", "source": "Patient.birthDate"}
            elif fact == "riskGroup":
                risk_group = self._find_tb_risk_group(patient_uuid)
                if risk_group:
                    resolved["riskGroup"] = risk_group
                else:
                    missing.append(fact)
            else:
                missing.append(fact)
        return {"facts": resolved, "missingFacts": missing}

    def _find_tb_risk_group(self, patient_uuid: str) -> dict[str, Any] | None:
        bundle = self.get_fhir("Observation", {"patient": patient_uuid, "_count": 50})
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            haystack = json.dumps(resource).lower()
            if "risk" not in haystack or "tb" not in haystack:
                continue
            value = resource.get("valueCodeableConcept") or {}
            text = value.get("text") or _first_coding_display(value) or resource.get("valueString")
            if text:
                return {"value": text, "display": text, "source": f"Observation/{resource.get('id', 'unknown')}"}
        if self._has_hiv_evidence(patient_uuid, bundle):
            return {
                "value": "PLHIV",
                "display": "People living with HIV",
                "source": "OpenMRS condition/observation text containing HIV evidence",
            }
        return {
            "value": "not_PLHIV",
            "display": "General population / high-risk group not including people living with HIV",
            "source": "CDS default for TB.B4.DT when no PLHIV evidence is present in OpenMRS context",
            "assumption": True,
        }

    def _has_hiv_evidence(self, patient_uuid: str, observation_bundle: dict[str, Any]) -> bool:
        try:
            conditions = self.get_rest("/condition", {"patientUuid": patient_uuid, "includeInactive": "true", "v": "full"}).get("results", [])
        except Exception:
            conditions = []
        condition_text = json.dumps(conditions).lower()
        if "hiv" in condition_text or "plhiv" in condition_text:
            return True
        observation_text = json.dumps(observation_bundle).lower()
        return "hiv" in observation_text or "plhiv" in observation_text


def _basic_auth_from_env() -> str | None:
    username = os.getenv("OPENMRS_USERNAME") or os.getenv("OPENMRS_SERVICE_USER")
    password = os.getenv("OPENMRS_PASSWORD") or os.getenv("OPENMRS_SERVICE_PASSWORD")
    if not username or not password:
        return None
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _demographics(patient: dict[str, Any]) -> dict[str, Any]:
    person = patient.get("person", {})
    birthdate = person.get("birthdate")
    return {
        "ageYears": _age_years(birthdate),
        "gender": person.get("gender"),
        "birthdateEstimated": bool(person.get("birthdateEstimated")),
        "deceased": bool(person.get("dead")),
    }


def _age_years(birthdate: str | None) -> int | None:
    if not birthdate:
        return None
    try:
        born = date.fromisoformat(birthdate[:10])
    except ValueError:
        return None
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _first_active_visit(visits: list[dict[str, Any]]) -> dict[str, Any] | None:
    for visit in visits:
        if visit.get("stopDatetime"):
            continue
        return {
            "uuid": visit.get("uuid"),
            "visitType": (visit.get("visitType") or {}).get("display"),
            "location": (visit.get("location") or {}).get("display"),
            "startDatetime": visit.get("startDatetime"),
        }
    return None


def _safe_count(fetcher) -> int:
    try:
        return len(fetcher().get("results", []))
    except Exception:
        return 0


def _safe_results(fetcher) -> list[dict[str, Any]]:
    try:
        return list(fetcher().get("results", []))
    except Exception:
        return []


def _compact_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("display") or value.get("name") or value.get("uuid") or "")
    return str(value or "")


def _first_coding_display(value: dict[str, Any]) -> str | None:
    coding = value.get("coding") or []
    if coding and isinstance(coding[0], dict):
        return coding[0].get("display") or coding[0].get("code")
    return None
