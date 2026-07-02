"""OpenMRS FHIR2 read client used by the report builder.

The OpenMRS Reporting REST module is stripped from the runtime image (see
`backend/scripts/run-openmrs.sh`), so we lean on the FHIR2 endpoints that
remain: `Observation`, `Encounter`, and `Patient`. The reader is the only
component that talks directly to OpenMRS; the report builder compiles a
``CompiledQuery`` (see `report_builder.py`) and hands its filters to this
client.

Filter-mode semantics (set by the compiler from the CIEL bundle's datatype):

    * value_concept   -> server-side `value-concept=` filter on Observation.
                          Probed on first use; if the server doesn't accept
                          the parameter we fall back to client-side
                          ``valueCodeableConcept.coding[].code`` filtering.
    * value_boolean   -> fetch all Observation for the code, filter
                          ``valueBoolean`` client-side. OpenMRS FHIR2 maps
                          CIEL Boolean obs to ``valueBoolean`` (not to a
                          coded 1065/1066 answer), so server-side
                          ``value-concept=`` returns 0 hits for these.
    * client_numeric  -> fetch all Observation for the code, filter
                          ``valueQuantity.value`` against the operator and
                          threshold in middleware. ``value-quantity=`` is
                          unreliable across OpenMRS FHIR2 builds.
    * any_value       -> no value predicate; just collect distinct subjects.

The reader exposes a thin progress-callback hook so the report tool loop can
emit `run_report_progress` SSE events as pages and phases tick over.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings


ProgressCallback = Callable[[str, dict[str, Any]], None]


def _basic_auth_from_env() -> str | None:
    """Pull admin creds from env vars.

    Checks both the legacy OPENMRS_USERNAME/OPENMRS_PASSWORD pair and the
    canonical OPENMRS_SERVICE_USER/OPENMRS_SERVICE_PASSWORD pair used by the
    all-in-one container so that whichever is set the reader can authenticate.
    """
    username = (
        os.getenv("OPENMRS_USERNAME")
        or os.getenv("OPENMRS_SERVICE_USER")
    )
    password = (
        os.getenv("OPENMRS_PASSWORD")
        or os.getenv("OPENMRS_SERVICE_PASSWORD")
    )
    if not username or not password:
        return None
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


@dataclass
class FilterSpec:
    """Reader-facing filter, populated from ``CompiledFilter``."""

    filter_id: str
    label: str
    code_uuid: str
    filter_mode: str  # value_concept | value_boolean | client_numeric | condition | any_value
    code_uuids: list[str] | None = None
    value_concept_uuid: str | None = None
    value_bool: bool | None = None
    operator: str | None = None
    numeric_threshold: float | None = None


class OpenmrsReader:
    """Thin FHIR2 read client scoped to the report builder.

    All requests use the operator Basic-auth fallback (`OPENMRS_USERNAME` /
    `OPENMRS_PASSWORD`) so the server-to-server CDS call works without a
    user cookie.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        authorization: str | None = None,
        cookie: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.settings = settings
        self.authorization = authorization or _basic_auth_from_env()
        self.cookie = cookie
        self.progress = progress
        # Per-instance probe cache: None=not yet probed, True/False=result.
        # Instance-scoped so multi-upstream deployments can't cross-contaminate.
        self._value_concept_capability: bool | None = None
        self.page_size = getattr(settings, "fhir_obs_page_size", 200)
        self.max_pages = getattr(settings, "fhir_obs_max_pages", 25)
        self.max_patient_chunks = getattr(settings, "fhir_demographics_chunk", 50)

    # ----- progress -----

    def _emit(self, stage: str, payload: dict[str, Any] | None = None) -> None:
        if self.progress is None:
            return
        try:
            self.progress(stage, payload or {})
        except Exception:
            pass

    # ----- core: observation patient ids -----

    def observation_patient_ids(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        """Return the distinct patient UUIDs matching this filter in the range.

        Internally dispatches by ``filter_spec.filter_mode``.
        """
        if filter_spec.filter_mode == "value_concept":
            return self._patient_ids_value_concept(filter_spec, date_from=date_from, date_to=date_to)
        if filter_spec.filter_mode == "value_boolean":
            return self._patient_ids_value_boolean(filter_spec, date_from=date_from, date_to=date_to)
        if filter_spec.filter_mode == "client_numeric":
            return self._patient_ids_client_numeric(filter_spec, date_from=date_from, date_to=date_to)
        if filter_spec.filter_mode == "condition":
            return self._patient_ids_condition(filter_spec, date_from=date_from, date_to=date_to)
        return self._patient_ids_any_value(filter_spec, date_from=date_from, date_to=date_to)

    def observation_patient_months(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> dict[str, list[str]]:
        """Return distinct observation months per matching patient.

        This powers month-by-month pivots. It deliberately uses the same
        datatype-specific matching rules as ``observation_patient_ids`` but
        preserves each matched Observation's effective month.
        """
        entries = self._matching_observation_entries(filter_spec, date_from=date_from, date_to=date_to)
        months_by_patient: dict[str, list[str]] = {}
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            resource = entry.get("resource") or {}
            patient_id = _patient_id_from_reference((resource.get("subject") or {}).get("reference"))
            month = _month_from_observation(resource)
            if not patient_id or not month:
                continue
            key = (patient_id, month)
            if key in seen:
                continue
            seen.add(key)
            months_by_patient.setdefault(patient_id, []).append(month)
        for months in months_by_patient.values():
            months.sort()
        return months_by_patient

    def _matching_observation_entries(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[dict[str, Any]]:
        if filter_spec.filter_mode == "value_concept":
            if not filter_spec.value_concept_uuid:
                return []
            entries = self._fetch_observation_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)
            target = filter_spec.value_concept_uuid
            return [
                entry
                for entry in entries
                if any(
                    coding.get("code") == target
                    for coding in (((entry.get("resource") or {}).get("valueCodeableConcept") or {}).get("coding") or [])
                )
            ]
        if filter_spec.filter_mode == "value_boolean":
            if filter_spec.value_bool is None:
                return []
            target = bool(filter_spec.value_bool)
            entries = self._fetch_observation_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)
            return [
                entry
                for entry in entries
                if "valueBoolean" in (entry.get("resource") or {})
                and bool((entry.get("resource") or {}).get("valueBoolean")) == target
            ]
        if filter_spec.filter_mode == "client_numeric":
            if filter_spec.numeric_threshold is None or filter_spec.operator is None:
                return []
            op = filter_spec.operator
            threshold = float(filter_spec.numeric_threshold)
            entries = self._fetch_observation_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)
            out: list[dict[str, Any]] = []
            for entry in entries:
                qty = ((entry.get("resource") or {}).get("valueQuantity") or {})
                try:
                    value = float(qty.get("value"))
                except (TypeError, ValueError):
                    continue
                if _compare(value, op, threshold):
                    out.append(entry)
            return out
        if filter_spec.filter_mode == "condition":
            return self._fetch_condition_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)
        return self._fetch_observation_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)

    def _patient_ids_value_concept(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        if not filter_spec.value_concept_uuid:
            return []
        # Try server-side filtering first (one probe per process).
        if self._value_concept_capability is None:
            self._value_concept_capability = self._probe_value_concept(filter_spec.code_uuid)
        if self._value_concept_capability:
            patient_ids: list[str] = []
            seen: set[str] = set()
            for code_uuid in _code_uuids_for_filter(filter_spec):
                params = {
                    "code": code_uuid,
                    "value-concept": filter_spec.value_concept_uuid,
                }
                for patient_id in self._paginate_observation_patient_ids(params, date_from=date_from, date_to=date_to):
                    if patient_id not in seen:
                        seen.add(patient_id)
                        patient_ids.append(patient_id)
            return patient_ids
        # Fallback: pull all obs for the code, filter coding[].code client-side.
        entries = self._fetch_observation_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)
        target = filter_spec.value_concept_uuid
        patient_ids: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            resource = entry.get("resource") or {}
            value = resource.get("valueCodeableConcept") or {}
            codings = value.get("coding") or []
            if not any(coding.get("code") == target for coding in codings):
                continue
            patient_id = _patient_id_from_reference(resource.get("subject", {}).get("reference"))
            if patient_id and patient_id not in seen:
                seen.add(patient_id)
                patient_ids.append(patient_id)
        return patient_ids

    def _patient_ids_value_boolean(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        if filter_spec.value_bool is None:
            return []
        entries = self._fetch_observation_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)
        target = bool(filter_spec.value_bool)
        patient_ids: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            resource = entry.get("resource") or {}
            if "valueBoolean" not in resource:
                continue
            if bool(resource.get("valueBoolean")) != target:
                continue
            patient_id = _patient_id_from_reference(resource.get("subject", {}).get("reference"))
            if patient_id and patient_id not in seen:
                seen.add(patient_id)
                patient_ids.append(patient_id)
        return patient_ids

    def _patient_ids_client_numeric(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        if filter_spec.numeric_threshold is None or filter_spec.operator is None:
            return []
        entries = self._fetch_observation_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)
        op = filter_spec.operator
        threshold = float(filter_spec.numeric_threshold)
        patient_ids: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            resource = entry.get("resource") or {}
            qty = resource.get("valueQuantity") or {}
            if "value" not in qty:
                continue
            try:
                value = float(qty.get("value"))
            except (TypeError, ValueError):
                continue
            if not _compare(value, op, threshold):
                continue
            patient_id = _patient_id_from_reference(resource.get("subject", {}).get("reference"))
            if patient_id and patient_id not in seen:
                seen.add(patient_id)
                patient_ids.append(patient_id)
        return patient_ids

    def _patient_ids_any_value(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        patient_ids: list[str] = []
        seen: set[str] = set()
        for code_uuid in _code_uuids_for_filter(filter_spec):
            params = {"code": code_uuid}
            for patient_id in self._paginate_observation_patient_ids(params, date_from=date_from, date_to=date_to):
                if patient_id not in seen:
                    seen.add(patient_id)
                    patient_ids.append(patient_id)
        return patient_ids

    def _patient_ids_condition(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        entries = self._fetch_condition_entries_for_filter(filter_spec, date_from=date_from, date_to=date_to)
        patient_ids: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            resource = entry.get("resource") or {}
            patient_id = _patient_id_from_reference((resource.get("subject") or {}).get("reference"))
            if patient_id and patient_id not in seen:
                seen.add(patient_id)
                patient_ids.append(patient_id)
        return patient_ids

    def _fetch_observation_entries_for_filter(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        out_without_id: list[dict[str, Any]] = []
        for code_uuid in _code_uuids_for_filter(filter_spec):
            entries = self._fetch_observation_entries({"code": code_uuid}, date_from=date_from, date_to=date_to)
            for entry in entries:
                resource_id = str(((entry.get("resource") or {}).get("id")) or "")
                if resource_id:
                    by_id[resource_id] = entry
                else:
                    out_without_id.append(entry)
        return [*by_id.values(), *out_without_id]

    def _fetch_condition_entries_for_filter(
        self,
        filter_spec: FilterSpec,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        out_without_id: list[dict[str, Any]] = []
        for code_uuid in _code_uuids_for_filter(filter_spec):
            entries = self._fetch_condition_entries(
                code_uuid,
                date_from=date_from,
                date_to=date_to,
                label=None,
            )
            for entry in entries:
                resource_id = str(((entry.get("resource") or {}).get("id")) or "")
                if resource_id:
                    by_id[resource_id] = entry
                else:
                    out_without_id.append(entry)
        return [*by_id.values(), *out_without_id]

    # ----- denominator: encounters_in_range -----

    def encounter_patient_ids(
        self,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        """Distinct subjects across all encounters in the date range.

        Used by the ``encounters_in_range`` indicator denominator. Backed by
        ``Encounter?date=ge&date=le`` which is O(encounters), unlike a naive
        Observation full-table scan.
        """
        params: dict[str, str] = {}
        return self._paginate_resource_subjects("Encounter", params, date_from=date_from, date_to=date_to)

    def encounter_patient_months(
        self,
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> dict[str, list[str]]:
        entries = self._fetch_resource_entries("Encounter", {}, date_from=date_from, date_to=date_to)
        months_by_patient: dict[str, list[str]] = {}
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            resource = entry.get("resource") or {}
            patient_id = _patient_id_from_reference((resource.get("subject") or {}).get("reference"))
            month = _month_from_resource(resource)
            if not patient_id or not month:
                continue
            key = (patient_id, month)
            if key in seen:
                continue
            seen.add(key)
            months_by_patient.setdefault(patient_id, []).append(month)
        for months in months_by_patient.values():
            months.sort()
        return months_by_patient

    # ----- demographics -----

    def patient_demographics(self, patient_uuids: list[str]) -> dict[str, dict[str, Any]]:
        """Batch-fetch demographics for a set of patient UUIDs.

        Returns ``{uuid: {gender, birthdate, display_name}}``.

        OpenMRS FHIR2 has a practical limit on the length of an ``_id`` query
        list, so chunk requests at ``max_patient_chunks`` (50) ids per
        request. This is the constant rediscovered at debugging time in the
        original plan, now documented inline.
        """
        out: dict[str, dict[str, Any]] = {}
        unique_ids = list(dict.fromkeys(patient_uuids))
        if not unique_ids:
            return out
        chunk = self.max_patient_chunks
        for i in range(0, len(unique_ids), chunk):
            slice_ids = unique_ids[i : i + chunk]
            self._emit(
                "demographics_chunk",
                {"offset": i, "count": len(slice_ids), "total": len(unique_ids)},
            )
            params = {"_id": ",".join(slice_ids), "_count": str(chunk)}
            bundle = self._get_fhir("Patient", params)
            for entry in bundle.get("entry") or []:
                resource = entry.get("resource") or {}
                uuid = str(resource.get("id") or "")
                if not uuid:
                    continue
                names = resource.get("name") or []
                display_name = _display_name_from_names(names)
                out[uuid] = {
                    "gender": resource.get("gender"),
                    "birthdate": resource.get("birthDate"),
                    "display_name": display_name,
                }
        return out

    # ----- internals: probes & paging -----

    def _probe_value_concept(self, sample_code: str) -> bool:
        """Single probe: does the FHIR server accept value-concept= on Observation?

        We call ``Observation?code=...&value-concept=dummy-uuid&_summary=count``.
        A clean 200 (even with total=0) means the parameter is accepted.
        A 400 / 500 means the server is rejecting the parameter; we fall
        back to client-side filtering for the rest of the session.
        """
        try:
            self._get_fhir(
                "Observation",
                {
                    "code": sample_code,
                    "value-concept": "00000000-0000-0000-0000-000000000000",
                    "_summary": "count",
                    "_count": "0",
                },
            )
            return True
        except Exception:
            return False

    def _paginate_observation_patient_ids(
        self,
        base_params: dict[str, str],
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        return self._paginate_resource_subjects("Observation", base_params, date_from=date_from, date_to=date_to)

    def _paginate_resource_subjects(
        self,
        resource: str,
        base_params: dict[str, str],
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[str]:
        # Build as a list so we can append two separate `date=` parameters
        # (ge... and le...) without them colliding in a dict.
        params_list = [(k, v) for k, v in base_params.items()]
        if date_from:
            params_list.append(("date", f"ge{date_from}"))
        if date_to:
            params_list.append(("date", f"le{date_to}"))
        params_list.append(("_count", str(self.page_size)))
        seen: set[str] = set()
        patient_ids: list[str] = []
        page_index = 0
        offset = 0
        while page_index < self.max_pages:
            page_params = list(params_list)
            if offset:
                page_params.append(("_getpagesoffset", str(offset)))
            self._emit(
                "fetch_page",
                {"resource": resource, "page": page_index, "offset": offset},
            )
            bundle = self._get_fhir(resource, page_params)
            entries = bundle.get("entry") or []
            if not entries:
                break
            for entry in entries:
                inner = entry.get("resource") or {}
                ref = (inner.get("subject") or {}).get("reference")
                patient_id = _patient_id_from_reference(ref)
                if patient_id and patient_id not in seen:
                    seen.add(patient_id)
                    patient_ids.append(patient_id)
            total = _bundle_total(bundle)
            next_offset = offset + len(entries)
            if total is not None and next_offset >= total:
                break
            if total is None and len(entries) < self.page_size:
                break
            offset = next_offset
            page_index += 1
        return patient_ids

    def _fetch_observation_entries(
        self,
        base_params: dict[str, str],
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[dict[str, Any]]:
        """Fetch raw Observation entries (not just patient ids) for client-side filtering."""
        return self._fetch_resource_entries("Observation", base_params, date_from=date_from, date_to=date_to)

    def _fetch_resource_entries(
        self,
        resource: str,
        base_params: dict[str, str],
        *,
        date_from: str | None,
        date_to: str | None,
    ) -> list[dict[str, Any]]:
        """Fetch raw resource entries for client-side filtering / bucketing."""
        params_list = [(k, v) for k, v in base_params.items()]
        if date_from:
            params_list.append(("date", f"ge{date_from}"))
        if date_to:
            params_list.append(("date", f"le{date_to}"))
        params_list.append(("_count", str(self.page_size)))
        out: list[dict[str, Any]] = []
        page_index = 0
        offset = 0
        while page_index < self.max_pages:
            page_params = list(params_list)
            if offset:
                page_params.append(("_getpagesoffset", str(offset)))
            self._emit(
                "fetch_page",
                {"resource": resource, "page": page_index, "offset": offset, "mode": "raw"},
            )
            bundle = self._get_fhir(resource, page_params)
            entries = bundle.get("entry") or []
            out.extend(entries)
            total = _bundle_total(bundle)
            next_offset = offset + len(entries)
            if total is not None and next_offset >= total:
                break
            if total is None and len(entries) < self.page_size:
                break
            offset = next_offset
            page_index += 1
        return out

    def _fetch_condition_entries(
        self,
        code_uuid: str,
        *,
        date_from: str | None,
        date_to: str | None,
        label: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch FHIR Condition entries for diagnosis/problem-list reports.

        Diagnosis concepts are stored in OpenMRS `conditions`, not `obs`.
        OpenMRS FHIR2 exposes these via `Condition?code=...` and supports
        `onset-date` range filters (clinical onset, not recordedDate).
        """
        params_list: list[tuple[str, str]] = [("code", code_uuid), ("_count", str(self.page_size))]
        if date_from:
            params_list.append(("onset-date", f"ge{date_from}"))
        if date_to:
            params_list.append(("onset-date", f"le{date_to}"))
        out: list[dict[str, Any]] = []
        page_index = 0
        offset = 0
        while page_index < self.max_pages:
            page_params = list(params_list)
            if offset:
                page_params.append(("_getpagesoffset", str(offset)))
            self._emit(
                "fetch_page",
                {"resource": "Condition", "page": page_index, "offset": offset, "mode": "raw"},
            )
            bundle = self._get_fhir("Condition", page_params)
            entries = bundle.get("entry") or []
            out.extend(
                entry
                for entry in entries
                if _resource_in_date_range(entry.get("resource") or {}, date_from=date_from, date_to=date_to)
            )
            if len(entries) < self.page_size:
                break
            offset += self.page_size
            page_index += 1
        return out

    # ----- HTTP -----

    def _get_fhir(self, resource: str, params: list[tuple[str, str]] | dict[str, str]) -> dict[str, Any]:
        base = self.settings.openmrs_fhir_base_url.rstrip("/")
        if isinstance(params, dict):
            encoded = urllib.parse.urlencode(params)
        else:
            encoded = urllib.parse.urlencode(params)
        url = f"{base}/{resource}?{encoded}" if encoded else f"{base}/{resource}"
        request = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/fhir+json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers


def _patient_id_from_reference(ref: str | None) -> str | None:
    if not ref:
        return None
    raw = str(ref).strip()
    if not raw:
        return None
    # FHIR references look like "Patient/<uuid>" or sometimes just "<uuid>".
    if "/" in raw:
        return raw.split("/", 1)[1].strip() or None
    return raw


def _code_uuids_for_filter(filter_spec: FilterSpec) -> list[str]:
    return list(dict.fromkeys([filter_spec.code_uuid, *(filter_spec.code_uuids or [])]))


def _display_name_from_names(names: list[dict[str, Any]]) -> str:
    if not names:
        return ""
    primary = names[0] or {}
    text = primary.get("text")
    if text:
        return str(text)
    family = primary.get("family") or ""
    given = primary.get("given") or []
    if isinstance(given, str):
        given_str = given
    else:
        given_str = " ".join(str(g) for g in given if g)
    if family and given_str:
        return f"{given_str} {family}".strip()
    return family or given_str or ""


def _month_from_observation(resource: dict[str, Any]) -> str | None:
    return _month_from_resource(resource)


def _month_from_resource(resource: dict[str, Any]) -> str | None:
    raw = (
        resource.get("effectiveDateTime")
        or (resource.get("effectivePeriod") or {}).get("start")
        or (resource.get("period") or {}).get("start")
        or resource.get("onsetDateTime")
        or resource.get("recordedDate")
        or resource.get("issued")
    )
    if not raw:
        return None
    value = str(raw)
    if len(value) < 7:
        return None
    return value[:7]


def _date_from_resource(resource: dict[str, Any]) -> str | None:
    raw = (
        resource.get("effectiveDateTime")
        or (resource.get("effectivePeriod") or {}).get("start")
        or (resource.get("period") or {}).get("start")
        or resource.get("onsetDateTime")
        or resource.get("recordedDate")
        or resource.get("issued")
    )
    if not raw:
        return None
    value = str(raw)
    if len(value) < 10:
        return None
    return value[:10]


def _resource_in_date_range(resource: dict[str, Any], *, date_from: str | None, date_to: str | None) -> bool:
    resource_date = _date_from_resource(resource)
    if not resource_date:
        return True
    if date_from and resource_date < date_from[:10]:
        return False
    if date_to and resource_date > date_to[:10]:
        return False
    return True


def _bundle_total(bundle: dict[str, Any]) -> int | None:
    try:
        value = bundle.get("total")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _compare(value: float, op: str, threshold: float) -> bool:
    if op == "eq":
        return value == threshold
    if op == "gt":
        return value > threshold
    if op == "ge":
        return value >= threshold
    if op == "lt":
        return value < threshold
    if op == "le":
        return value <= threshold
    return False


__all__ = ["FilterSpec", "OpenmrsReader", "ProgressCallback"]
