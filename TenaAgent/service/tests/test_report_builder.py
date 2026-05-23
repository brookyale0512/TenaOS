"""Unit tests for report_builder + openmrs_reader + report_builder_tool_loop."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service.ciel import ConceptNotFoundError, openmrs_uuid_for_concept_id  # noqa: E402
from tena_agent_service.openmrs_reader import FilterSpec  # noqa: E402
from tena_agent_service.report_builder import (  # noqa: E402
    Denominator,
    GroupBy,
    ReportFilter,
    ReportSpec,
    ReportVisualization,
    filter_mode_for_concept,
    resolve_date_range,
    spec_to_query,
    validate_spec,
)
from tena_agent_service.report_builder_tool_loop import (  # noqa: E402
    ReportBuilderToolLoop,
    _normalize_concept_id,
)
from tena_agent_service.report_drafts import ReportDraftStore  # noqa: E402


def _bundle(
    concept_id: str,
    display_name: str,
    *,
    datatype: str = "Boolean",
    concept_class: str = "Finding",
    retired: bool = False,
    answers: list[dict[str, Any]] | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "concept": {
            "concept_id": concept_id,
            "display_name": display_name,
            "datatype": datatype,
            "concept_class": concept_class,
            "retired": retired,
            "extras": extras or {},
        },
        "answers": [
            {"target": {**a, "concept_id": str(a.get("concept_id", ""))}}
            for a in (answers or [])
        ],
        "set_members": [],
    }


class FakeCielClient:
    def __init__(self, bundles: dict[str, dict[str, Any]] | None = None) -> None:
        self.bundles = bundles or {}

    def get_concept_bundle(self, concept_id: str) -> dict[str, Any]:
        if concept_id not in self.bundles:
            raise ConceptNotFoundError(concept_id)
        return self.bundles[concept_id]

    def search_form_seeds(self, query: str, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def search_concepts(self, query: str, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def expand_seed(self, concept_id: str, *, depth: int = 2, allow_retired: bool = False) -> dict[str, Any]:
        return self.get_concept_bundle(concept_id)


# ---------------------------------------------------------------------------
# resolve_date_range


class ResolveDateRangeTests(unittest.TestCase):
    REF = date(2026, 5, 15)

    def test_none_returns_no_range(self) -> None:
        d1, d2, label = resolve_date_range(None, reference_date=self.REF)
        self.assertIsNone(d1)
        self.assertIsNone(d2)
        self.assertIsNone(label)

    def test_last_quarter(self) -> None:
        d1, d2, _ = resolve_date_range("last quarter", reference_date=self.REF)
        self.assertEqual(d1, date(2026, 1, 1))
        self.assertEqual(d2, date(2026, 3, 31))

    def test_this_quarter(self) -> None:
        d1, d2, _ = resolve_date_range("this quarter", reference_date=self.REF)
        self.assertEqual(d1, date(2026, 4, 1))
        self.assertEqual(d2, date(2026, 6, 30))

    def test_last_month(self) -> None:
        d1, d2, _ = resolve_date_range("last month", reference_date=self.REF)
        self.assertEqual(d1, date(2026, 4, 1))
        self.assertEqual(d2, date(2026, 4, 30))

    def test_year_to_date(self) -> None:
        d1, d2, _ = resolve_date_range("YTD", reference_date=self.REF)
        self.assertEqual(d1, date(2026, 1, 1))
        self.assertEqual(d2, self.REF)

    def test_last_year(self) -> None:
        d1, d2, _ = resolve_date_range("last year", reference_date=self.REF)
        self.assertEqual(d1, date(2025, 1, 1))
        self.assertEqual(d2, date(2025, 12, 31))

    def test_last_n_months(self) -> None:
        d1, d2, _ = resolve_date_range("last 6 months", reference_date=self.REF)
        self.assertEqual(d2, self.REF)
        self.assertEqual(d1, date(2025, 11, 15))

    def test_yyyy_qn(self) -> None:
        d1, d2, _ = resolve_date_range("2025-Q2", reference_date=self.REF)
        self.assertEqual(d1, date(2025, 4, 1))
        self.assertEqual(d2, date(2025, 6, 30))

    def test_yyyy_mm(self) -> None:
        d1, d2, _ = resolve_date_range("2025-03", reference_date=self.REF)
        self.assertEqual(d1, date(2025, 3, 1))
        self.assertEqual(d2, date(2025, 3, 31))

    def test_explicit_range(self) -> None:
        d1, d2, _ = resolve_date_range("2025-01-01..2025-06-30", reference_date=self.REF)
        self.assertEqual(d1, date(2025, 1, 1))
        self.assertEqual(d2, date(2025, 6, 30))

    def test_unknown_phrase_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_date_range("around christmas time", reference_date=self.REF)


# ---------------------------------------------------------------------------
# filter_mode_for_concept


class FilterModeTests(unittest.TestCase):
    def test_boolean_concept(self) -> None:
        self.assertEqual(filter_mode_for_concept(_bundle("1479", "Night sweats", datatype="Boolean")), "value_boolean")

    def test_coded_concept(self) -> None:
        self.assertEqual(filter_mode_for_concept(_bundle("1063", "HIV", datatype="Coded")), "value_concept")

    def test_numeric_concept(self) -> None:
        self.assertEqual(filter_mode_for_concept(_bundle("5089", "Weight", datatype="Numeric")), "client_numeric")

    def test_na_clinical_concept(self) -> None:
        bundle = _bundle("131602", "Otalgia", datatype="N/A", concept_class="Diagnosis")
        self.assertEqual(filter_mode_for_concept(bundle), "condition")


# ---------------------------------------------------------------------------
# validate_spec + spec_to_query


class SpecValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ciel = FakeCielClient(
            {
                "1479": _bundle("1479", "Night sweats", datatype="Boolean"),
                "1487": _bundle("1487", "Cough", datatype="Boolean"),
                "5089": _bundle("5089", "Weight", datatype="Numeric"),
                "1063": _bundle("1063", "HIV", datatype="Coded", answers=[{"concept_id": "703", "display_name": "Positive"}]),
                "131602": _bundle("131602", "Otalgia", datatype="N/A", concept_class="Diagnosis"),
                "1366": _bundle("1366", "Malaria smear, qualitative", datatype="Coded", concept_class="Test", answers=[{"concept_id": "1065", "display_name": "Yes"}]),
            }
        )

    def test_count_with_boolean_filter(self) -> None:
        spec = ReportSpec(
            report_type="count",
            date_range_label="last quarter",
            filters=[
                ReportFilter(filter_id="f1", concept_id="1479", label="Night sweats", filter_mode="value_boolean", value_bool=True),
            ],
        )
        report = validate_spec(spec, self.ciel)
        self.assertTrue(report.ok, [i.message for i in report.issues])
        compiled = spec_to_query(spec, self.ciel, reference_date=date(2026, 5, 15))
        self.assertEqual(compiled.report_type, "count")
        self.assertEqual(compiled.filters[0].filter_mode, "value_boolean")
        self.assertEqual(compiled.date_from, "2026-01-01")
        self.assertEqual(compiled.date_to, "2026-03-31")

    def test_missing_value_bool_rejected_for_boolean(self) -> None:
        spec = ReportSpec(
            report_type="count",
            filters=[
                ReportFilter(filter_id="f1", concept_id="1479", label="Night sweats", filter_mode="value_boolean"),
            ],
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("valueBool" in i.path for i in report.issues))

    def test_filter_mode_mismatch_rejected(self) -> None:
        spec = ReportSpec(
            report_type="count",
            filters=[
                ReportFilter(filter_id="f1", concept_id="5089", label="Weight", filter_mode="value_concept", value_concept_id="1"),
            ],
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("filterMode" in i.path for i in report.issues))

    def test_coded_without_value_rejected(self) -> None:
        spec = ReportSpec(
            report_type="count",
            filters=[
                ReportFilter(filter_id="f1", concept_id="1063", label="HIV", filter_mode="value_concept"),
            ],
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("valueConceptId" in i.path for i in report.issues))

    def test_diagnosis_condition_filter_does_not_require_value(self) -> None:
        spec = ReportSpec(
            report_type="count",
            filters=[
                ReportFilter(filter_id="f1", concept_id="131602", label="Otalgia", filter_mode="value_concept"),
            ],
        )
        report = validate_spec(spec, self.ciel)
        self.assertTrue(report.ok, [i.message for i in report.issues])
        compiled = spec_to_query(spec, self.ciel, reference_date=date(2026, 5, 15))
        self.assertEqual(compiled.filters[0].filter_mode, "condition")

    def test_malaria_diagnosis_alias_compiles_to_condition_code(self) -> None:
        spec = ReportSpec(
            report_type="count",
            filters=[
                ReportFilter(
                    filter_id="f1",
                    concept_id="1366",
                    label="Malaria diagnosis",
                    filter_mode="value_concept",
                    value_concept_id="1065",
                ),
            ],
        )
        report = validate_spec(spec, self.ciel)
        self.assertTrue(report.ok, [i.message for i in report.issues])
        compiled = spec_to_query(spec, self.ciel, reference_date=date(2026, 5, 15))
        self.assertEqual(compiled.filters[0].filter_mode, "condition")
        self.assertEqual(compiled.filters[0].code_uuid, openmrs_uuid_for_concept_id("116128"))

    def test_numeric_requires_operator_and_threshold(self) -> None:
        spec = ReportSpec(
            report_type="count",
            filters=[ReportFilter(filter_id="f1", concept_id="5089", label="Weight", filter_mode="client_numeric")],
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        paths = {i.path for i in report.issues}
        self.assertIn("filters[0].operator", paths)
        self.assertIn("filters[0].numericThreshold", paths)

    def test_numeric_operators_all_supported(self) -> None:
        for op in ("eq", "gt", "ge", "lt", "le"):
            spec = ReportSpec(
                report_type="count",
                filters=[
                    ReportFilter(
                        filter_id="f1",
                        concept_id="5089",
                        label="Weight",
                        filter_mode="client_numeric",
                        operator=op,  # type: ignore[arg-type]
                        numeric_threshold=60.0,
                    )
                ],
            )
            self.assertTrue(validate_spec(spec, self.ciel).ok, op)

    def test_indicator_requires_denominator(self) -> None:
        spec = ReportSpec(
            report_type="indicator",
            filters=[ReportFilter(filter_id="f1", concept_id="1479", label="Night sweats", filter_mode="value_boolean", value_bool=True)],
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("denominator" in i.path for i in report.issues))

    def test_indicator_rejects_all_patients_in_range(self) -> None:
        spec = ReportSpec(
            report_type="indicator",
            filters=[ReportFilter(filter_id="f1", concept_id="1479", label="Night sweats", filter_mode="value_boolean", value_bool=True)],
            denominator=Denominator(kind="all_patients_in_range"),  # type: ignore[arg-type]
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("denominator.kind" in i.path for i in report.issues))

    def test_pivot_requires_group_by(self) -> None:
        spec = ReportSpec(
            report_type="pivot",
            filters=[ReportFilter(filter_id="f1", concept_id="1479", label="Night sweats", filter_mode="value_boolean", value_bool=True)],
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("groupBy" in i.path for i in report.issues))

    def test_pivot_concept_id_dimension_requires_concept(self) -> None:
        spec = ReportSpec(
            report_type="pivot",
            filters=[ReportFilter(filter_id="f1", concept_id="1479", label="Night sweats", filter_mode="value_boolean", value_bool=True)],
            group_by=[GroupBy(dimension="concept_id")],
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("conceptId" in i.path for i in report.issues))

    def test_duplicate_filter_rejected(self) -> None:
        spec = ReportSpec(
            report_type="count",
            filters=[
                ReportFilter(filter_id="f1", concept_id="1479", label="A", filter_mode="value_boolean", value_bool=True),
                ReportFilter(filter_id="f2", concept_id="1479", label="B", filter_mode="value_boolean", value_bool=True),
            ],
        )
        report = validate_spec(spec, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any(i.message.lower().startswith("duplicate") for i in report.issues))

    def test_visualization_is_compiled_with_default(self) -> None:
        spec = ReportSpec(
            report_type="indicator",
            filters=[ReportFilter(filter_id="f1", concept_id="1479", label="Night sweats", filter_mode="value_boolean", value_bool=True)],
            denominator=Denominator(kind="encounters_in_range"),
        )
        compiled = spec_to_query(spec, self.ciel, reference_date=date(2026, 5, 15))
        self.assertIsNotNone(compiled.visualization)
        self.assertEqual(compiled.visualization.template, "indicator_rate")

    def test_incompatible_visualization_warns_and_falls_back(self) -> None:
        spec = ReportSpec(
            report_type="count",
            filters=[ReportFilter(filter_id="f1", concept_id="1479", label="Night sweats", filter_mode="value_boolean", value_bool=True)],
            visualization=ReportVisualization(template="pivot_heatmap", title="Requested heatmap"),
        )
        report = validate_spec(spec, self.ciel)
        self.assertTrue(report.ok)
        self.assertTrue(any(issue.severity == "warning" and "visualization" in issue.path for issue in report.issues))
        compiled = spec_to_query(spec, self.ciel, reference_date=date(2026, 5, 15))
        self.assertEqual(compiled.visualization.template, "filter_bar")
        self.assertEqual(compiled.visualization.title, "Requested heatmap")


# ---------------------------------------------------------------------------
# OpenmrsReader: Boolean + numeric client-side filtering


class FakeReader:
    """Stand-in for OpenmrsReader used by ReportBuilderToolLoop.run_report."""

    def __init__(
        self,
        *,
        observation_entries: dict[str, list[dict[str, Any]]] | None = None,
        encounter_subjects: list[str] | None = None,
        encounter_months: dict[str, list[str]] | None = None,
        demographics: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.observation_entries = observation_entries or {}
        self.encounter_subjects = encounter_subjects or []
        self.encounter_months = encounter_months or {}
        self.demographics = demographics or {}

    def observation_patient_ids(self, filter_spec: FilterSpec, *, date_from: str | None, date_to: str | None) -> list[str]:
        return list(self.observation_patient_months(filter_spec, date_from=date_from, date_to=date_to).keys())

    def observation_patient_months(self, filter_spec: FilterSpec, *, date_from: str | None, date_to: str | None) -> dict[str, list[str]]:
        entries = self.observation_entries.get(filter_spec.code_uuid, [])
        out: dict[str, list[str]] = {}
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            value = entry.get("resource") or {}
            patient_ref = (value.get("subject") or {}).get("reference")
            patient = patient_ref.split("/")[-1] if patient_ref else None
            if not patient:
                continue
            if filter_spec.filter_mode == "value_boolean":
                if "valueBoolean" not in value:
                    continue
                if bool(value.get("valueBoolean")) != bool(filter_spec.value_bool):
                    continue
            elif filter_spec.filter_mode == "client_numeric":
                qty = value.get("valueQuantity") or {}
                v = qty.get("value")
                if v is None:
                    continue
                op = filter_spec.operator
                threshold = filter_spec.numeric_threshold
                if op == "gt" and not (v > threshold):
                    continue
                if op == "ge" and not (v >= threshold):
                    continue
                if op == "lt" and not (v < threshold):
                    continue
                if op == "le" and not (v <= threshold):
                    continue
                if op == "eq" and not (v == threshold):
                    continue
            elif filter_spec.filter_mode == "value_concept":
                coded = (value.get("valueCodeableConcept") or {}).get("coding") or []
                if not any(c.get("code") == filter_spec.value_concept_uuid for c in coded):
                    continue
            month = str(value.get("effectiveDateTime") or value.get("issued") or "2026-01-01")[:7]
            key = (patient, month)
            if key in seen:
                continue
            seen.add(key)
            out.setdefault(patient, []).append(month)
        for months in out.values():
            months.sort()
        return out

    def encounter_patient_ids(self, *, date_from: str | None, date_to: str | None) -> list[str]:
        return list(self.encounter_subjects)

    def encounter_patient_months(self, *, date_from: str | None, date_to: str | None) -> dict[str, list[str]]:
        return dict(self.encounter_months)

    def patient_demographics(self, patient_uuids: list[str]) -> dict[str, dict[str, Any]]:
        return {uuid: self.demographics.get(uuid, {}) for uuid in patient_uuids}


def _obs_entry(patient_uuid: str, **value: Any) -> dict[str, Any]:
    return {
        "resource": {
            "subject": {"reference": f"Patient/{patient_uuid}"},
            **value,
        }
    }


class ReaderClientSideFilteringTests(unittest.TestCase):
    """Verify the filter-mode dispatch logic on the FakeReader matches the spec."""

    def test_boolean_true_filter(self) -> None:
        reader = FakeReader(
            observation_entries={
                openmrs_uuid_for_concept_id("1479"): [
                    _obs_entry("p1", valueBoolean=True),
                    _obs_entry("p2", valueBoolean=False),
                    _obs_entry("p3", valueBoolean=True),
                ]
            }
        )
        ids = reader.observation_patient_ids(
            FilterSpec(filter_id="f", label="Night sweats", code_uuid=openmrs_uuid_for_concept_id("1479"), filter_mode="value_boolean", value_bool=True),
            date_from=None,
            date_to=None,
        )
        self.assertEqual(ids, ["p1", "p3"])

    def test_boolean_false_filter(self) -> None:
        reader = FakeReader(
            observation_entries={
                openmrs_uuid_for_concept_id("1479"): [
                    _obs_entry("p1", valueBoolean=True),
                    _obs_entry("p2", valueBoolean=False),
                ]
            }
        )
        ids = reader.observation_patient_ids(
            FilterSpec(filter_id="f", label="X", code_uuid=openmrs_uuid_for_concept_id("1479"), filter_mode="value_boolean", value_bool=False),
            date_from=None,
            date_to=None,
        )
        self.assertEqual(ids, ["p2"])

    def test_numeric_all_operators(self) -> None:
        entries = [
            _obs_entry("p1", valueQuantity={"value": 40}),
            _obs_entry("p2", valueQuantity={"value": 60}),
            _obs_entry("p3", valueQuantity={"value": 80}),
        ]
        reader = FakeReader(observation_entries={openmrs_uuid_for_concept_id("5089"): entries})
        results = {}
        for op in ("eq", "gt", "ge", "lt", "le"):
            results[op] = reader.observation_patient_ids(
                FilterSpec(
                    filter_id="f",
                    label="Weight",
                    code_uuid=openmrs_uuid_for_concept_id("5089"),
                    filter_mode="client_numeric",
                    operator=op,  # type: ignore[arg-type]
                    numeric_threshold=60.0,
                ),
                date_from=None,
                date_to=None,
            )
        self.assertEqual(results["eq"], ["p2"])
        self.assertEqual(results["gt"], ["p3"])
        self.assertEqual(results["ge"], ["p2", "p3"])
        self.assertEqual(results["lt"], ["p1"])
        self.assertEqual(results["le"], ["p1", "p2"])


# ---------------------------------------------------------------------------
# Tool loop end-to-end


class ReportToolLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="report-loop-tests-")
        self.db_path = Path(self.tempdir) / "rdb.sqlite3"
        self.store = ReportDraftStore(self.db_path)
        self.bundles = {
            "1479": _bundle("1479", "Night sweats", datatype="Boolean"),
            "1487": _bundle("1487", "Cough", datatype="Boolean"),
            "5089": _bundle("5089", "Weight", datatype="Numeric"),
            "1063": _bundle("1063", "HIV", datatype="Coded", answers=[{"concept_id": "703", "display_name": "Positive"}]),
            "1065": _bundle("1065", "Yes", datatype="N/A", concept_class="Misc"),
            "1066": _bundle("1066", "No", datatype="N/A", concept_class="Misc"),
            "703": _bundle("703", "Positive", datatype="N/A", concept_class="Misc"),
        }
        self.ciel = FakeCielClient(self.bundles)

        # Default fake reader with no obs (counts return 0). Specific tests override.
        self.reader = FakeReader()
        self.loop = ReportBuilderToolLoop(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            reader_factory=lambda progress=None: self.reader,
        )

    def tearDown(self) -> None:
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def _new_draft(self) -> str:
        return self.store.create_draft(name="TB count", report_type="count").draft_id

    def test_count_happy_path(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "count"},
                {"op": "set_date_range", "text": "last quarter"},
                {"op": "add_filter", "conceptId": "1479", "valueBool": True, "label": "Night sweats"},
            ],
        )
        # Reader returns 3 distinct patients for the night-sweats code.
        self.reader.observation_entries = {
            openmrs_uuid_for_concept_id("1479"): [
                _obs_entry("p1", valueBoolean=True),
                _obs_entry("p2", valueBoolean=True),
                _obs_entry("p3", valueBoolean=True),
            ]
        }
        build = self.loop.build_report_query(draft_id)
        self.assertIsNotNone(build.get("compiled"))
        result = self.loop.run_report(draft_id)
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["total"], 3)
        self.assertEqual(result["result"]["visualization"]["template"], "filter_bar")
        self.assertEqual(result["result"]["visualization"]["data"]["bars"][0]["value"], 3)

    def test_cohort_with_demographics(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "cohort"},
                {"op": "set_date_range", "text": "last quarter"},
                {"op": "add_filter", "conceptId": "1487", "valueBool": True, "label": "Cough"},
            ],
        )
        self.reader.observation_entries = {
            openmrs_uuid_for_concept_id("1487"): [
                _obs_entry("p1", valueBoolean=True),
                _obs_entry("p2", valueBoolean=True),
            ]
        }
        self.reader.demographics = {
            "p1": {"gender": "female", "birthdate": "1990-01-01", "display_name": "Alice"},
            "p2": {"gender": "male", "birthdate": "1985-01-01", "display_name": "Bob"},
        }
        self.loop.build_report_query(draft_id)
        result = self.loop.run_report(draft_id)
        self.assertTrue(result["success"])
        patients = result["result"]["patients"]
        names = sorted(p["displayName"] for p in patients)
        self.assertEqual(names, ["Alice", "Bob"])

    def test_indicator_with_encounters_denominator(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "indicator"},
                {"op": "set_date_range", "text": "last quarter"},
                {"op": "add_filter", "conceptId": "1487", "valueBool": True, "label": "Cough"},
                {"op": "set_denominator", "kind": "encounters_in_range"},
            ],
        )
        self.reader.observation_entries = {
            openmrs_uuid_for_concept_id("1487"): [_obs_entry("p1", valueBoolean=True), _obs_entry("p2", valueBoolean=True)],
        }
        self.reader.encounter_subjects = ["p1", "p2", "p3", "p4", "p5"]
        self.loop.build_report_query(draft_id)
        result = self.loop.run_report(draft_id)
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["numerator"], 2)
        self.assertEqual(result["result"]["denominator"], 5)
        self.assertAlmostEqual(result["result"]["rate"], 40.0, places=2)
        chart = result["result"]["visualization"]
        self.assertEqual(chart["template"], "indicator_rate")
        self.assertEqual(chart["data"]["bars"], [{"label": "Numerator", "value": 2}, {"label": "Denominator", "value": 5}])

    def test_pivot_by_sex_and_age(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "pivot"},
                {"op": "set_date_range", "text": "last 12 months"},
                {"op": "add_filter", "conceptId": "1487", "valueBool": True, "label": "Cough"},
                {"op": "add_group_by", "dimension": "sex"},
                {"op": "add_group_by", "dimension": "age_group"},
            ],
        )
        self.reader.observation_entries = {
            openmrs_uuid_for_concept_id("1487"): [
                _obs_entry(f"p{i}", valueBoolean=True) for i in range(1, 6)
            ]
        }
        today = date.today()
        # Choose birthdates by subtracting the desired age from the current year
        # so the test is hermetic against the system clock.
        female_under_5 = date(today.year - 2, 1, 1).isoformat()
        female_5_14 = date(today.year - 10, 1, 1).isoformat()
        male_15_24 = date(today.year - 20, 1, 1).isoformat()
        male_25_49 = date(today.year - 35, 1, 1).isoformat()
        male_50_plus = date(today.year - 60, 1, 1).isoformat()
        self.reader.demographics = {
            "p1": {"gender": "female", "birthdate": female_under_5, "display_name": "A"},
            "p2": {"gender": "female", "birthdate": female_5_14, "display_name": "B"},
            "p3": {"gender": "male", "birthdate": male_15_24, "display_name": "C"},
            "p4": {"gender": "male", "birthdate": male_25_49, "display_name": "D"},
            "p5": {"gender": "male", "birthdate": male_50_plus, "display_name": "E"},
        }
        self.loop.build_report_query(draft_id)
        result = self.loop.run_report(draft_id)
        self.assertTrue(result["success"])
        pivot = result["result"]["pivot"]
        self.assertEqual(pivot["rowLabels"], ["Female", "Male"])
        self.assertEqual(pivot["colLabels"][:4], ["<5", "5-14", "15-24", "25-49"])
        female_row = pivot["cells"][0]
        male_row = pivot["cells"][1]
        # Female has one in <5 and one in 5-14, none in older buckets.
        self.assertEqual(female_row[:2], [1, 1])
        # Male has zero in <5/5-14 and one each in 15-24, 25-49, 50+.
        self.assertEqual(male_row[:2], [0, 0])
        self.assertEqual(male_row[2:5], [1, 1, 1])
        chart = result["result"]["visualization"]
        self.assertEqual(chart["template"], "pivot_grouped_bar")
        self.assertEqual(chart["data"]["rowLabels"], ["Female", "Male"])

    def test_pivot_by_month_for_single_filter(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "pivot"},
                {"op": "set_date_range", "text": "2026-01-01..2026-03-31"},
                {"op": "add_filter", "conceptId": "1487", "valueBool": True, "label": "Weight loss"},
                {"op": "add_group_by", "dimension": "date_month"},
                {"op": "set_visualization", "template": "pivot_grouped_bar", "title": "Weight loss month by month"},
            ],
        )
        self.reader.observation_entries = {
            openmrs_uuid_for_concept_id("1487"): [
                _obs_entry("p1", valueBoolean=True, effectiveDateTime="2026-01-10T09:00:00+00:00"),
                _obs_entry("p2", valueBoolean=True, effectiveDateTime="2026-03-02T09:00:00+00:00"),
                _obs_entry("p2", valueBoolean=True, effectiveDateTime="2026-03-20T09:00:00+00:00"),
            ]
        }
        self.loop.build_report_query(draft_id)
        result = self.loop.run_report(draft_id)
        self.assertTrue(result["success"])
        pivot = result["result"]["pivot"]
        self.assertEqual(pivot["rowLabels"], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(pivot["colLabels"], ["Count"])
        self.assertEqual([row[0] for row in pivot["cells"]], [1, 0, 1])
        chart = result["result"]["visualization"]
        self.assertEqual(chart["data"]["rows"][2]["values"][0]["value"], 1)

    def test_pivot_by_month_defaults_to_time_series_bar(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "pivot"},
                {"op": "set_date_range", "text": "2026-01-01..2026-02-28"},
                {"op": "add_filter", "conceptId": "1487", "valueBool": True, "label": "Weight loss"},
                {"op": "add_group_by", "dimension": "date_month"},
            ],
        )
        self.reader.observation_entries = {
            openmrs_uuid_for_concept_id("1487"): [
                _obs_entry("p1", valueBoolean=True, effectiveDateTime="2026-02-10T09:00:00+00:00"),
            ]
        }
        self.loop.build_report_query(draft_id)
        result = self.loop.run_report(draft_id)
        self.assertTrue(result["success"])
        chart = result["result"]["visualization"]
        self.assertEqual(chart["template"], "time_series_bar")
        self.assertEqual(chart["data"]["points"], [{"period": "2026-01", "value": 0}, {"period": "2026-02", "value": 1}])

    def test_indicator_rate_over_time(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "indicator"},
                {"op": "set_date_range", "text": "2026-01-01..2026-02-28"},
                {"op": "add_filter", "conceptId": "1487", "valueBool": True, "label": "Weight loss"},
                {"op": "set_denominator", "kind": "encounters_in_range"},
                {"op": "add_group_by", "dimension": "date_month"},
                {"op": "set_visualization", "template": "rate_over_time", "title": "Monthly weight loss rate"},
            ],
        )
        self.reader.observation_entries = {
            openmrs_uuid_for_concept_id("1487"): [
                _obs_entry("p1", valueBoolean=True, effectiveDateTime="2026-01-10T09:00:00+00:00"),
                _obs_entry("p2", valueBoolean=True, effectiveDateTime="2026-02-10T09:00:00+00:00"),
            ]
        }
        self.reader.encounter_subjects = ["p1", "p2", "p3"]
        self.reader.encounter_months = {"p1": ["2026-01"], "p2": ["2026-02"], "p3": ["2026-02"]}
        self.loop.build_report_query(draft_id)
        result = self.loop.run_report(draft_id)
        self.assertTrue(result["success"])
        series = result["result"]["rateSeries"]
        self.assertEqual(series[0], {"period": "2026-01", "numerator": 1, "denominator": 1, "rate": 100.0})
        self.assertEqual(series[1], {"period": "2026-02", "numerator": 1, "denominator": 2, "rate": 50.0})
        self.assertEqual(result["result"]["visualization"]["template"], "rate_over_time")

    def test_set_visualization_op_accepts_compatible_template(self) -> None:
        draft_id = self._new_draft()
        result = self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "pivot"},
                {"op": "set_visualization", "template": "pivot_heatmap", "title": "Cases by age and sex"},
            ],
        )
        self.assertFalse(result["warnings"])
        draft = self.store.get_draft(draft_id)
        self.assertEqual(draft.spec["visualization"]["template"], "pivot_heatmap")
        self.assertEqual(draft.spec["visualization"]["title"], "Cases by age and sex")

    def test_set_visualization_op_falls_back_on_incompatible_template(self) -> None:
        draft_id = self._new_draft()
        result = self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "count"},
                {"op": "set_visualization", "template": "pivot_heatmap", "title": "Bad chart"},
            ],
        )
        self.assertTrue(result["warnings"])
        draft = self.store.get_draft(draft_id)
        self.assertEqual(draft.spec["visualization"]["template"], "filter_bar")

    def test_add_group_by_dedupes_and_keeps_latest_two_dimensions(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "pivot"},
                {"op": "add_group_by", "dimension": "date_month"},
                {"op": "add_group_by", "dimension": "date_month"},
                {"op": "add_group_by", "dimension": "sex"},
                {"op": "add_group_by", "dimension": "age_group"},
            ],
        )
        draft = self.store.get_draft(draft_id)
        self.assertEqual([g["dimension"] for g in draft.spec["groupBy"]], ["sex", "age_group"])

    def test_temporal_group_by_dedupes_to_month_and_sex(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "pivot"},
                {"op": "add_group_by", "dimension": "date_month"},
                {"op": "add_group_by", "dimension": "date_month"},
                {"op": "add_group_by", "dimension": "sex"},
            ],
        )
        draft = self.store.get_draft(draft_id)
        self.assertEqual([g["dimension"] for g in draft.spec["groupBy"]], ["date_month", "sex"])

    def test_padded_uuid_normalised_in_add_filter(self) -> None:
        draft_id = self._new_draft()
        result = self.loop.update_report_draft(
            draft_id,
            [{"op": "add_filter", "conceptId": "1479AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "valueBool": True}],
        )
        applied = [a for a in result["applied"] if a["op"] == "add_filter"]
        self.assertEqual(applied[0]["conceptId"], "1479")

    def test_normalize_concept_id_helper(self) -> None:
        self.assertEqual(_normalize_concept_id("5089"), ("5089", False))
        self.assertEqual(_normalize_concept_id("5089AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"), ("5089", True))

    def test_run_report_emits_progress_events(self) -> None:
        draft_id = self._new_draft()
        self.loop.update_report_draft(
            draft_id,
            [
                {"op": "set_report_type", "reportType": "cohort"},
                {"op": "set_date_range", "text": "last quarter"},
                {"op": "add_filter", "conceptId": "1479", "valueBool": True, "label": "Night sweats"},
            ],
        )
        self.reader.observation_entries = {
            openmrs_uuid_for_concept_id("1479"): [_obs_entry("p1", valueBoolean=True)],
        }
        self.reader.demographics = {"p1": {"gender": "female", "birthdate": "1990-01-01", "display_name": "Z"}}
        self.loop.build_report_query(draft_id)
        self.loop.run_report(draft_id)
        events = self.store.list_events(draft_id)
        progress_stages = [
            (e.payload or {}).get("stage")
            for e in events
            if e.operation == "run_report_progress"
        ]
        self.assertIn("fetching_filter", progress_stages)
        self.assertIn("fetching_demographics", progress_stages)


if __name__ == "__main__":
    unittest.main()
