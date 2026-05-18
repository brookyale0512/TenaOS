from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cds_service.ciel import ConceptNotFoundError, openmrs_uuid_for_concept_id  # noqa: E402
from cds_service.form_builder import (  # noqa: E402
    BasketField,
    BasketSection,
    ConceptBasket,
    FormMeta,
    basket_to_schema,
    validate_schema,
)
from cds_service.form_builder_tool_loop import FormBuilderToolLoop  # noqa: E402
from cds_service.form_drafts import FormDraftStore  # noqa: E402


# ---------------------------------------------------------------------------
# A minimal fake CielClient that the form_builder/tool_loop can be tested
# against without depending on the real CIEL SQLite store or Qdrant.


class FakeCielClient:
    def __init__(self, bundles: dict[str, dict[str, Any]] | None = None) -> None:
        self.bundles = bundles or {}

    def get_concept_bundle(self, concept_id: str) -> dict[str, Any]:
        if concept_id not in self.bundles:
            raise ConceptNotFoundError(concept_id)
        return self.bundles[concept_id]

    def expand_seed(self, concept_id: str, *, depth: int = 3, allow_retired: bool = False) -> dict[str, Any]:
        bundle = self.get_concept_bundle(concept_id)
        if bundle.get("concept", {}).get("retired") and not allow_retired:
            raise ValueError(f"Concept {concept_id} is retired")
        return {
            "concept": bundle["concept"],
            "answers": [
                {
                    "conceptId": str(rel.get("target", {}).get("concept_id", "")),
                    "displayName": rel.get("target", {}).get("display_name", ""),
                    "retired": bool(rel.get("target", {}).get("retired")),
                }
                for rel in bundle.get("answers", []) or []
            ],
            "setMembers": [
                {
                    "conceptId": str(rel.get("target", {}).get("concept_id", "")),
                    "displayName": rel.get("target", {}).get("display_name", ""),
                    "conceptClass": rel.get("target", {}).get("concept_class"),
                    "datatype": rel.get("target", {}).get("datatype"),
                    "retired": bool(rel.get("target", {}).get("retired")),
                    "answerCount": int(rel.get("target", {}).get("answer_count", 0) or 0),
                    "setMemberCount": int(rel.get("target", {}).get("set_member_count", 0) or 0),
                }
                for rel in bundle.get("set_members", []) or []
            ],
            "depth": depth,
        }

    def search_form_seeds(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def _bundle(
    concept_id: str,
    display_name: str,
    *,
    datatype: str = "Text",
    concept_class: str = "Finding",
    retired: bool = False,
    answers: list[dict[str, Any]] | None = None,
    set_members: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "concept": {
            "concept_id": concept_id,
            "display_name": display_name,
            "datatype": datatype,
            "concept_class": concept_class,
            "retired": retired,
        },
        "answers": [
            {"target": {**answer, "concept_id": str(answer.get("concept_id", ""))}}
            for answer in (answers or [])
        ],
        "set_members": [
            {"target": {**member, "concept_id": str(member.get("concept_id", ""))}}
            for member in (set_members or [])
        ],
    }


# ---------------------------------------------------------------------------
# UUID padding


class OpenmrsUuidPaddingTests(unittest.TestCase):
    def test_short_id_pads_to_36_chars(self) -> None:
        self.assertEqual(openmrs_uuid_for_concept_id("5089"), "5089" + "A" * 32)

    def test_long_id_still_pads_correctly(self) -> None:
        uuid = openmrs_uuid_for_concept_id("162169")
        self.assertEqual(len(uuid), 36)
        self.assertTrue(uuid.startswith("162169"))
        self.assertEqual(uuid[6:], "A" * 30)

    def test_integer_id(self) -> None:
        self.assertEqual(openmrs_uuid_for_concept_id(5089), "5089" + "A" * 32)

    def test_empty_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            openmrs_uuid_for_concept_id("")

    def test_overlong_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            openmrs_uuid_for_concept_id("x" * 37)


# ---------------------------------------------------------------------------
# Schema construction: datatype -> rendering mapping


class SchemaConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ciel = FakeCielClient(
            bundles={
                "5089": _bundle("5089", "Weight (kg)", datatype="Numeric"),
                "5090": _bundle("5090", "Height (cm)", datatype="Numeric"),
                "162169": _bundle("162169", "Clinical note", datatype="Text"),
                "1396": _bundle(
                    "1396",
                    "TB exposure",
                    datatype="Boolean",
                ),
                "1063": _bundle(
                    "1063",
                    "HIV status",
                    datatype="Coded",
                    answers=[
                        {"concept_id": "703", "display_name": "Positive"},
                        {"concept_id": "664", "display_name": "Negative"},
                        {"concept_id": "1067", "display_name": "Unknown"},
                    ],
                ),
                "1234": _bundle("1234", "Symptom set", datatype="N/A", concept_class="ConvSet"),
                "9999": _bundle("9999", "Retired symptom", datatype="Numeric", retired=True),
            },
        )
        self.meta = FormMeta(
            name="ANC initial visit",
            version="1.0.0",
            description="Antenatal first contact form",
            encounter_type_uuid="dd528487-82a5-4082-9c72-ed246bd49591",
        )

    def test_numeric_renders_as_number(self) -> None:
        basket = ConceptBasket(sections=[
            BasketSection(
                section_id="vitals",
                label="Vitals",
                fields=[BasketField(concept_id="5089"), BasketField(concept_id="5090")],
            )
        ])
        schema = basket_to_schema(basket, self.meta, self.ciel)
        questions = schema["pages"][0]["sections"][0]["questions"]
        self.assertEqual(questions[0]["questionOptions"]["rendering"], "number")
        self.assertEqual(questions[0]["questionOptions"]["concept"], "5089" + "A" * 32)
        self.assertEqual(questions[0]["label"], "Weight (kg)")

    def test_boolean_renders_as_radio_with_yes_no(self) -> None:
        basket = ConceptBasket(sections=[
            BasketSection(section_id="exposure", label="Exposure", fields=[BasketField(concept_id="1396")]),
        ])
        schema = basket_to_schema(basket, self.meta, self.ciel)
        question = schema["pages"][0]["sections"][0]["questions"][0]
        self.assertEqual(question["questionOptions"]["rendering"], "radio")
        self.assertEqual(question["questionOptions"]["datatype"], "Boolean")
        # Boolean answers use the canonical CIEL Yes/No concept UUIDs so the
        # publish-time preflight finds them in OpenMRS. The literal
        # "true"/"false" sentinels previously used here caused publish to
        # report "missing in OpenMRS: Yes, No".
        self.assertEqual(
            [ans["concept"] for ans in question["questionOptions"]["answers"]],
            [openmrs_uuid_for_concept_id("1065"), openmrs_uuid_for_concept_id("1066")],
        )
        labels = [ans["label"] for ans in question["questionOptions"]["answers"]]
        self.assertEqual(labels, ["Yes", "No"])

    def test_coded_with_few_answers_renders_radio(self) -> None:
        basket = ConceptBasket(sections=[
            BasketSection(section_id="hiv", label="HIV", fields=[BasketField(concept_id="1063")]),
        ])
        schema = basket_to_schema(basket, self.meta, self.ciel)
        question = schema["pages"][0]["sections"][0]["questions"][0]
        self.assertEqual(question["questionOptions"]["rendering"], "radio")
        answers = question["questionOptions"]["answers"]
        self.assertEqual(len(answers), 3)
        for answer in answers:
            self.assertTrue(answer["concept"].endswith("A" * (36 - len(str(answer["concept"]).rstrip("A")))))

    def test_required_flag_emits_validator(self) -> None:
        basket = ConceptBasket(sections=[
            BasketSection(section_id="vitals", label="Vitals", fields=[BasketField(concept_id="5089", required=True)])
        ])
        schema = basket_to_schema(basket, self.meta, self.ciel)
        question = schema["pages"][0]["sections"][0]["questions"][0]
        self.assertTrue(question["required"])
        self.assertEqual(question["validators"][0]["type"], "required")

    def test_label_override_used(self) -> None:
        basket = ConceptBasket(sections=[
            BasketSection(section_id="vitals", label="Vitals", fields=[BasketField(concept_id="5089", label_override="Body weight (kg)")])
        ])
        schema = basket_to_schema(basket, self.meta, self.ciel)
        question = schema["pages"][0]["sections"][0]["questions"][0]
        self.assertEqual(question["label"], "Body weight (kg)")

    def test_empty_section_omitted(self) -> None:
        basket = ConceptBasket(sections=[
            BasketSection(section_id="empty", label="Empty", fields=[]),
            BasketSection(section_id="vitals", label="Vitals", fields=[BasketField(concept_id="5089")]),
        ])
        schema = basket_to_schema(basket, self.meta, self.ciel)
        self.assertEqual(len(schema["pages"][0]["sections"]), 1)
        self.assertEqual(schema["pages"][0]["sections"][0]["id"], "vitals")

    def test_unknown_concept_silently_skipped_then_validation_catches_it(self) -> None:
        # The schema build is robust: unknown concepts are skipped so the
        # form can still preview. validate_schema catches them as errors via
        # the answers/concept checks because every emitted question references
        # a known concept. Confirm: missing concept -> section becomes empty
        # -> validation reports the empty page/section.
        basket = ConceptBasket(sections=[
            BasketSection(section_id="vitals", label="Vitals", fields=[BasketField(concept_id="not_in_ciel")])
        ])
        schema = basket_to_schema(basket, self.meta, self.ciel)
        self.assertEqual(len(schema["pages"][0]["sections"]), 0)
        report = validate_schema(schema, self.ciel)
        self.assertFalse(report.ok)


# ---------------------------------------------------------------------------
# Validation


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ciel = FakeCielClient(
            bundles={
                "5089": _bundle("5089", "Weight (kg)", datatype="Numeric"),
                "9999": _bundle("9999", "Retired", datatype="Numeric", retired=True),
            },
        )

    def _good_schema(self) -> dict[str, Any]:
        return {
            "name": "Form",
            "version": "1.0.0",
            "encounterType": "encounter-uuid",
            "pages": [
                {
                    "id": "p1",
                    "label": "Page",
                    "sections": [
                        {
                            "id": "s1",
                            "label": "Section",
                            "questions": [
                                {
                                    "id": "weight",
                                    "label": "Weight",
                                    "type": "obs",
                                    "questionOptions": {
                                        "concept": openmrs_uuid_for_concept_id("5089"),
                                        "rendering": "number",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    def test_valid_schema_passes(self) -> None:
        report = validate_schema(self._good_schema(), self.ciel)
        self.assertTrue(report.ok, [issue.to_dict() for issue in report.issues])

    def test_retired_concept_blocked(self) -> None:
        schema = self._good_schema()
        schema["pages"][0]["sections"][0]["questions"][0]["questionOptions"]["concept"] = openmrs_uuid_for_concept_id("9999")
        report = validate_schema(schema, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("retired" in issue.message.lower() for issue in report.issues))

    def test_missing_concept_blocked(self) -> None:
        schema = self._good_schema()
        schema["pages"][0]["sections"][0]["questions"][0]["questionOptions"]["concept"] = openmrs_uuid_for_concept_id("424242")
        report = validate_schema(schema, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("not present" in issue.message.lower() for issue in report.issues))

    def test_duplicate_question_ids_blocked(self) -> None:
        schema = self._good_schema()
        schema["pages"][0]["sections"][0]["questions"].append(dict(schema["pages"][0]["sections"][0]["questions"][0]))
        report = validate_schema(schema, self.ciel)
        self.assertFalse(report.ok)
        self.assertTrue(any("duplicate" in issue.message.lower() for issue in report.issues))

    def test_missing_required_top_level_fields(self) -> None:
        schema = self._good_schema()
        del schema["name"]
        del schema["encounterType"]
        report = validate_schema(schema, self.ciel)
        self.assertFalse(report.ok)
        paths = {issue.path for issue in report.issues}
        self.assertIn("name", paths)
        self.assertIn("encounterType", paths)


# ---------------------------------------------------------------------------
# Draft store + tool loop integration (using temp SQLite + fake CIEL)


class _FakeVllmStatus:
    healthy = False

    def to_dict(self) -> dict[str, Any]:
        return {"healthy": False, "message": "test"}


class _FakeVllm:
    def health(self) -> Any:
        return _FakeVllmStatus()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Not expected in tests")


class _FakeWriter:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def list_encounter_types(self, limit: int = 50) -> list[dict[str, Any]]:
        return [{"uuid": "encounter-uuid", "display": "Consultation", "name": "Consultation"}]

    def probe_concept(self, concept_uuid: str) -> dict[str, Any] | None:
        return {"uuid": concept_uuid, "display": "stub", "retired": False}

    def preflight_concepts(self, concept_uuids: list[str]) -> dict[str, list[str]]:
        return {"missing": [], "retired": [], "checked": list({u: True for u in concept_uuids})}

    def ensure_concept(self, bundle: dict[str, Any]) -> bool:
        return True

    def ensure_concept_with_answers(self, bundle: dict[str, Any]) -> tuple[bool, list[str]]:
        return True, []

    def publish_form(self, schema: dict[str, Any]) -> Any:
        from cds_service.openmrs_writer import PublishResult, PublishStep

        form_uuid = "test-form-uuid-0001"
        self.published.append(schema)
        return PublishResult(
            form_uuid=form_uuid,
            success=True,
            steps=[
                PublishStep("create_form", "ok", "stub"),
                PublishStep("upload_clob", "ok", "stub"),
                PublishStep("attach_resource", "ok", "stub"),
            ],
        )


class ToolLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="form-builder-tests-")
        self.db_path = Path(self.tempdir) / "drafts.sqlite3"
        self.store = FormDraftStore(self.db_path)
        self.ciel = FakeCielClient(
            bundles={
                "5089": _bundle("5089", "Weight (kg)", datatype="Numeric"),
                "5085": _bundle("5085", "Systolic BP", datatype="Numeric"),
                "1063": _bundle(
                    "1063",
                    "HIV status",
                    datatype="Coded",
                    answers=[
                        {"concept_id": "703", "display_name": "Positive"},
                        {"concept_id": "664", "display_name": "Negative"},
                    ],
                ),
                "9999": _bundle("9999", "Retired field", datatype="Text", retired=True),
            },
        )
        self.writer = _FakeWriter()
        self.loop = FormBuilderToolLoop(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            vllm=_FakeVllm(),  # type: ignore[arg-type]
            writer_factory=lambda: self.writer,  # type: ignore[arg-type, return-value]
        )

    def tearDown(self) -> None:
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def _draft(self) -> Any:
        return self.store.create_draft(
            name="Vitals form",
            owner="alice",
            description=None,
            encounter_type_uuid="encounter-uuid",
        )

    def test_add_section_and_field_then_build_schema(self) -> None:
        draft = self._draft()
        update = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5085", "required": True},
            ],
        )
        self.assertEqual(len(update["applied"]), 3)
        self.assertEqual(update["warnings"], [])
        build = self.loop.build_form_schema(draft.draft_id)
        self.assertEqual(len(build["schema"]["pages"][0]["sections"][0]["questions"]), 2)
        events = self.store.list_events(draft.draft_id)
        operations = [event.operation for event in events]
        self.assertIn("update_form_draft", operations)
        self.assertIn("build_form_schema", operations)

    def test_retired_field_rejected_at_add_time(self) -> None:
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "9999"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("retired", result["warnings"][0]["reason"].lower())

    def test_reorder_sections_preserves_unmentioned(self) -> None:
        draft = self._draft()
        self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "a", "label": "A"},
                {"op": "add_section", "sectionId": "b", "label": "B"},
                {"op": "add_section", "sectionId": "c", "label": "C"},
            ],
        )
        self.loop.update_form_draft(
            draft.draft_id,
            [{"op": "reorder_sections", "sectionIds": ["c", "a"]}],
        )
        draft_after = self.store.get_draft(draft.draft_id)
        order = [section["sectionId"] for section in draft_after.basket["sections"]]
        self.assertEqual(order, ["c", "a", "b"])

    def test_add_field_strips_openmrs_padded_uuid_and_warns(self) -> None:
        """Gemma sometimes echoes the padded UUID. Normalize + flag, don't reject."""
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089" + "A" * 32},
            ],
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(result["applied"]), 2)
        add_field_step = result["applied"][1]
        self.assertEqual(add_field_step["conceptId"], "5089")
        self.assertEqual(add_field_step["normalizedFromUuid"], "5089" + "A" * 32)
        self.assertIn("CIEL numeric id", add_field_step["warning"])
        draft_after = self.store.get_draft(draft.draft_id)
        field_ids = [field["conceptId"] for field in draft_after.basket["sections"][0]["fields"]]
        self.assertEqual(field_ids, ["5089"])

    def test_drug_na_datatype_rejected_as_form_question(self) -> None:
        """A Drug like Medroxyprogesterone with N/A datatype is not a valid form question."""
        self.ciel.bundles["907"] = _bundle("907", "Medroxyprogesterone acetate", datatype="N/A", concept_class="Drug")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "907"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        draft_after = self.store.get_draft(draft.draft_id)
        self.assertEqual(draft_after.basket["sections"][0]["fields"], [])

    def test_drug_class_with_numeric_datatype_rejected(self) -> None:
        """Numeric/Text Drug-class concepts are still rejected as form questions."""
        self.ciel.bundles["2271"] = _bundle("2271", "Amount of paracetamol", datatype="Numeric", concept_class="Drug")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "meds", "label": "Meds"},
                {"op": "add_field", "sectionId": "meds", "conceptId": "2271"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("drug", result["warnings"][0]["reason"].lower())

    def test_diagnosis_na_datatype_allowed_as_yes_no_question(self) -> None:
        """Diagnosis + N/A is a valid 'does the patient have X?' question.

        CIEL stores common symptoms (Otalgia, Tinnitus, Hearing loss,
        Amenorrhea, etc.) as Diagnosis-class N/A datatype concepts. They are
        the standard form-question shape: render as Yes/No radio with the
        concept itself as the obs concept.
        """
        self.ciel.bundles["148989"] = _bundle("148989", "Amenorrhea", datatype="N/A", concept_class="Diagnosis")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "hpi", "label": "HPI"},
                {"op": "add_field", "sectionId": "hpi", "conceptId": "148989"},
            ],
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(result["applied"]), 2)
        draft_after = self.store.get_draft(draft.draft_id)
        field_ids = [f["conceptId"] for f in draft_after.basket["sections"][0]["fields"]]
        self.assertEqual(field_ids, ["148989"])

    def test_na_clinical_concept_renders_as_yes_no_radio_in_schema(self) -> None:
        """N/A Diagnosis/Symptom/Finding concepts render as Boolean Yes/No radios."""
        from cds_service.form_builder import BasketField, BasketSection, ConceptBasket, FormMeta, basket_to_schema  # noqa: WPS433

        self.ciel.bundles["131602"] = _bundle("131602", "Otalgia", datatype="N/A", concept_class="Diagnosis")
        basket = ConceptBasket(sections=[
            BasketSection(section_id="ent", label="ENT", fields=[BasketField(concept_id="131602")]),
        ])
        meta = FormMeta(name="ENT", version="1.0.0", description="", encounter_type_uuid="enc-uuid")
        schema = basket_to_schema(basket, meta, self.ciel)
        question = schema["pages"][0]["sections"][0]["questions"][0]
        self.assertEqual(question["questionOptions"]["rendering"], "radio")
        self.assertEqual(
            [a["label"] for a in question["questionOptions"]["answers"]],
            ["Yes", "No"],
        )

    def test_na_drug_class_still_rejected_by_filter(self) -> None:
        """N/A Drug-class concepts are still rejected (drugs aren't questions)."""
        self.ciel.bundles["907"] = _bundle("907", "Medroxyprogesterone acetate", datatype="N/A", concept_class="Drug")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "meds", "label": "Meds"},
                {"op": "add_field", "sectionId": "meds", "conceptId": "907"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        draft_after = self.store.get_draft(draft.draft_id)
        self.assertEqual(draft_after.basket["sections"][0]["fields"], [])

    def test_coded_with_qa_style_answers_rejected(self) -> None:
        """CIEL Coded concepts whose answers are QA flags (incorrect/missing/...) are unusable.

        Mirrors the runtime bug at 21:00 where CIEL 166541 'Reason for failed
        contact tracing' was added to the TB form with QA answers like
        'Contact details missing', 'Contact details incorrect'.
        """
        self.ciel.bundles["166541"] = _bundle(
            "166541",
            "Reason for failed contact tracing",
            datatype="Coded",
            concept_class="Question",
            answers=[
                {"concept_id": "166540", "display_name": "Contact locations details incorrect"},
                {"concept_id": "166538", "display_name": "Contact location details missing"},
                {"concept_id": "166537", "display_name": "Contact details missing"},
                {"concept_id": "166539", "display_name": "Contact details incorrect"},
                {"concept_id": "5622", "display_name": "Other"},
                {"concept_id": "1067", "display_name": "Unknown"},
            ],
        )
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "tb", "label": "TB"},
                {"op": "add_field", "sectionId": "tb", "conceptId": "166541"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("data-quality", result["warnings"][0]["reason"].lower())
        draft_after = self.store.get_draft(draft.draft_id)
        self.assertEqual(draft_after.basket["sections"][0]["fields"], [])

    def test_qa_display_name_concept_rejected(self) -> None:
        """N/A Finding concepts whose display name is a QA flag are rejected.

        CIEL has Finding-class N/A concepts like 'Contact details missing'
        and 'Contact details incorrect' (siblings of CIEL 166541). These
        passed the N/A clinical filter but they're data-quality annotations,
        not clinical observations. The model sometimes picks them and slaps
        a clinical label on top (observed in the TB run at 21:46 where
        166537 'Contact details missing' was labelled 'Presence of
        persistent cough').
        """
        self.ciel.bundles["166537"] = _bundle("166537", "Contact details missing", datatype="N/A", concept_class="Finding")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "s", "label": "S"},
                {"op": "add_field", "sectionId": "s", "conceptId": "166537", "label": "Presence of persistent cough"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("data-quality", result["warnings"][0]["reason"].lower())

    def test_coded_with_clinical_answers_still_allowed(self) -> None:
        """A Coded concept with normal Yes/No/Unknown answers is not flagged as QA."""
        self.ciel.bundles["1063"] = _bundle(
            "1063",
            "HIV status",
            datatype="Coded",
            concept_class="Question",
            answers=[
                {"concept_id": "703", "display_name": "Positive"},
                {"concept_id": "664", "display_name": "Negative"},
                {"concept_id": "1067", "display_name": "Unknown"},
            ],
        )
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "hiv", "label": "HIV"},
                {"op": "add_field", "sectionId": "hiv", "conceptId": "1063"},
            ],
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(result["applied"]), 2)

    def _track_seeding(self) -> tuple[list[str], list[str]]:
        """Replace the fake writer's seeding methods with trackers.

        Returns (seeded_via_ensure_concept, seeded_via_ensure_concept_with_answers).
        """
        seeded_solo: list[str] = []
        seeded_with_answers: list[str] = []
        original_ensure = self.writer.ensure_concept
        original_with_answers = self.writer.ensure_concept_with_answers

        def _track_solo(bundle: dict[str, Any]) -> bool:
            seeded_solo.append(str((bundle.get("concept") or {}).get("concept_id") or ""))
            return original_ensure(bundle)

        def _track_with_answers(bundle: dict[str, Any]) -> tuple[bool, list[str]]:
            concept_id = str((bundle.get("concept") or {}).get("concept_id") or "")
            seeded_with_answers.append(concept_id)
            return original_with_answers(bundle)

        self.writer.ensure_concept = _track_solo  # type: ignore[assignment]
        self.writer.ensure_concept_with_answers = _track_with_answers  # type: ignore[assignment]
        return seeded_solo, seeded_with_answers

    def test_add_field_auto_seeds_concept_into_openmrs(self) -> None:
        """Every add_field must seed the concept + its answers into OpenMRS.

        Previously the agent path bypassed seeding, so publish failed with
        'Concepts missing or retired in target OpenMRS'.
        """
        draft = self._draft()
        _seeded_solo, seeded_with_answers = self._track_seeding()

        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "1063"},  # Coded HIV status
            ],
        )
        self.assertEqual(result["warnings"], [])
        # The question concept itself goes through ensure_concept_with_answers,
        # which the real OpenmrsWriter then expands to ensure_concept for each
        # answer (covered by test_form_builder integration tests).
        self.assertIn("1063", seeded_with_answers)
        # The basket op recorded that seeding happened.
        applied = [a for a in result["applied"] if a.get("op") == "add_field"]
        self.assertTrue(applied[0].get("seededInOpenmrs"))

    def test_add_field_auto_seeds_yes_no_for_boolean_concept(self) -> None:
        """Boolean datatype questions must also seed CIEL 1065/1066 (Yes/No)."""
        self.ciel.bundles["1065"] = _bundle("1065", "Yes", datatype="N/A", concept_class="Misc")
        self.ciel.bundles["1066"] = _bundle("1066", "No", datatype="N/A", concept_class="Misc")
        self.ciel.bundles.setdefault("1396", _bundle("1396", "TB exposure", datatype="Boolean"))
        seeded_solo, seeded_with_answers = self._track_seeding()

        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "exposure", "label": "Exposure"},
                {"op": "add_field", "sectionId": "exposure", "conceptId": "1396"},
            ],
        )
        self.assertEqual(result["warnings"], [])
        self.assertIn("1396", seeded_with_answers)
        # Yes/No concepts must be seeded so publish preflight finds them.
        self.assertIn("1065", seeded_solo)
        self.assertIn("1066", seeded_solo)

    def test_add_field_auto_seeds_yes_no_for_na_clinical_concept(self) -> None:
        """N/A Diagnosis/Symptom concepts render as Yes/No and need 1065/1066 seeded."""
        self.ciel.bundles["131602"] = _bundle("131602", "Otalgia", datatype="N/A", concept_class="Diagnosis")
        self.ciel.bundles["1065"] = _bundle("1065", "Yes", datatype="N/A", concept_class="Misc")
        self.ciel.bundles["1066"] = _bundle("1066", "No", datatype="N/A", concept_class="Misc")
        seeded_solo, seeded_with_answers = self._track_seeding()

        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "ent", "label": "ENT"},
                {"op": "add_field", "sectionId": "ent", "conceptId": "131602"},
            ],
        )
        self.assertEqual(result["warnings"], [])
        self.assertIn("131602", seeded_with_answers)
        self.assertIn("1065", seeded_solo)
        self.assertIn("1066", seeded_solo)

    def test_publish_autoseeds_missing_concepts_before_failing(self) -> None:
        """When preflight finds CIEL-shaped UUIDs missing, autoseed them and re-check."""
        # Simulate an OpenMRS that initially does not have CIEL 5089 (Weight)
        # and only seeds it when ensure_concept is called.
        seeded_uuids: set[str] = set()

        class _SelectiveWriter(_FakeWriter):
            def preflight_concepts(inner_self, concept_uuids: list[str]) -> dict[str, list[str]]:
                missing = [u for u in concept_uuids if u not in seeded_uuids]
                return {"missing": missing, "retired": [], "checked": list({u: True for u in concept_uuids})}

            def ensure_concept_with_answers(inner_self, bundle: dict[str, Any]) -> tuple[bool, list[str]]:
                concept_id = str((bundle.get("concept") or {}).get("concept_id") or "")
                if concept_id:
                    seeded_uuids.add(openmrs_uuid_for_concept_id(concept_id))
                return True, []

            def ensure_concept(inner_self, bundle: dict[str, Any]) -> bool:
                concept_id = str((bundle.get("concept") or {}).get("concept_id") or "")
                if concept_id:
                    seeded_uuids.add(openmrs_uuid_for_concept_id(concept_id))
                return True

        selective_writer = _SelectiveWriter()
        self.loop = FormBuilderToolLoop(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            vllm=_FakeVllm(),  # type: ignore[arg-type]
            writer_factory=lambda: selective_writer,  # type: ignore[arg-type, return-value]
        )
        draft = self._draft()
        # Build a 1-question form. The add_field path seeds 5089 itself.
        self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            ],
        )
        self.loop.build_form_schema(draft.draft_id)
        # Now simulate the missing-concept scenario: discard the seeded set so
        # preflight reports 5089 missing again.
        seeded_uuids.clear()
        result = self.loop.publish_form(draft.draft_id, mark_published=True)
        # Publish must succeed because the autoseeder pulled 5089 back from
        # CIEL and POSTed it before failing.
        self.assertTrue(result["success"], result)

    def test_add_section_without_label_derives_humanized_label_from_id(self) -> None:
        """When the agent forgets label=, the basket shows a readable title.

        Previously the basket displayed 'patient_history_and_risk_factors' as
        the section title because add_section fell back to the sectionId when
        label was missing. Now it derives 'Patient History And Risk Factors'.
        """
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "patient_history_and_risk_factors"},
                {"op": "add_field", "sectionId": "patient_history_and_risk_factors", "conceptId": "5089"},
            ],
        )
        self.assertEqual(result["warnings"], [])
        draft_after = self.store.get_draft(draft.draft_id)
        self.assertEqual(
            draft_after.basket["sections"][0]["label"],
            "Patient History And Risk Factors",
        )

    def test_add_section_with_label_keeps_label_verbatim(self) -> None:
        """An explicit label is never overwritten by the humanizer."""
        draft = self._draft()
        self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "history", "label": "Patient History & Risk Factors"},
            ],
        )
        draft_after = self.store.get_draft(draft.draft_id)
        self.assertEqual(draft_after.basket["sections"][0]["label"], "Patient History & Risk Factors")

    def test_duplicate_labelOverride_in_section_rejected(self) -> None:
        """Two different CIEL concepts cannot share the same labelOverride in one section.

        Mirrors runtime bug at 21:44 where the agent added CIEL 159576
        (HIV Status) and CIEL 119481 (Diabetes mellitus) both with
        labelOverride='History of immunosuppressive conditions', producing
        two indistinguishable form questions.
        """
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "history", "label": "History"},
                {"op": "add_field", "sectionId": "history", "conceptId": "5089", "label": "History of immunosuppression"},
                {"op": "add_field", "sectionId": "history", "conceptId": "5085", "label": "History of immunosuppression"},
            ],
        )
        self.assertEqual(len(result["applied"]), 2)  # add_section + first add_field
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("already has a field labelled", result["warnings"][0]["reason"].lower())
        draft_after = self.store.get_draft(draft.draft_id)
        field_ids = [f["conceptId"] for f in draft_after.basket["sections"][0]["fields"]]
        self.assertEqual(field_ids, ["5089"], "Second duplicate-labelled field must not enter the basket")

    def test_cross_section_concept_dedupe(self) -> None:
        """A CIEL concept cannot appear in two different sections of one form.

        Mirrors the runtime bug at 21:00 where CIEL 159576 'HIV Status' was
        added twice with two different labels ('History of immunosuppressive
        conditions' and 'Presence of unexplained weight loss') in two
        different sections.
        """
        draft = self._draft()
        self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "history", "label": "History"},
                {"op": "add_field", "sectionId": "history", "conceptId": "5089"},
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
            ],
        )
        result = self.loop.update_form_draft(
            draft.draft_id,
            [{"op": "add_field", "sectionId": "vitals", "conceptId": "5089"}],
        )
        self.assertEqual(len(result["applied"]), 0)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("already used", result["warnings"][0]["reason"].lower())
        draft_after = self.store.get_draft(draft.draft_id)
        all_concept_ids = [
            f["conceptId"]
            for section in draft_after.basket["sections"]
            for f in section["fields"]
        ]
        self.assertEqual(all_concept_ids, ["5089"], "Same concept must not appear in two sections")

    def test_na_anatomy_class_rejected_by_filter(self) -> None:
        """N/A datatype with a non-clinical class (Anatomy) is rejected."""
        self.ciel.bundles["123"] = _bundle("123", "Left ear", datatype="N/A", concept_class="Anatomy")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "anatomy", "label": "Anatomy"},
                {"op": "add_field", "sectionId": "anatomy", "conceptId": "123"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("not a", result["warnings"][0]["reason"].lower())

    def test_diagnosis_with_boolean_datatype_allowed_as_yes_no_question(self) -> None:
        """A Diagnosis-class concept with Boolean datatype IS a valid yes/no question.

        E.g. CIEL classifies "Tinnitus" as Diagnosis but it renders as a
        legitimate 'does the patient have tinnitus?' Boolean form question.
        Blocking these caused the live ENT run to end with only 1 field.
        """
        self.ciel.bundles["123588"] = _bundle("123588", "Tinnitus", datatype="Boolean", concept_class="Diagnosis")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "ent", "label": "ENT"},
                {"op": "add_field", "sectionId": "ent", "conceptId": "123588"},
            ],
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(result["applied"]), 2)
        draft_after = self.store.get_draft(draft.draft_id)
        field_ids = [f["conceptId"] for f in draft_after.basket["sections"][0]["fields"]]
        self.assertEqual(field_ids, ["123588"])

    def test_diagnosis_with_coded_answers_allowed(self) -> None:
        """A Diagnosis-class Coded concept with answers IS a valid multi-choice question."""
        self.ciel.bundles["1063"] = _bundle(
            "1063",
            "HIV status",
            datatype="Coded",
            concept_class="Diagnosis",
            answers=[
                {"concept_id": "703", "display_name": "Positive"},
                {"concept_id": "664", "display_name": "Negative"},
            ],
        )
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "hiv", "label": "HIV"},
                {"op": "add_field", "sectionId": "hiv", "conceptId": "1063"},
            ],
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(result["applied"]), 2)

    def test_set_concept_rejected_directly_expand_first(self) -> None:
        """A ConvSet shouldn't be added as a question — expand_ciel_concept first."""
        self.ciel.bundles["1234"] = _bundle(
            "1234",
            "Vital Signs",
            datatype="N/A",
            concept_class="ConvSet",
            set_members=[
                {"concept_id": "5089", "display_name": "Weight", "datatype": "Numeric"},
                {"concept_id": "5085", "display_name": "Systolic BP", "datatype": "Numeric"},
            ],
        )
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "1234"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("set", result["warnings"][0]["reason"].lower())

    def test_coded_with_no_answers_rejected(self) -> None:
        self.ciel.bundles["8001"] = _bundle("8001", "Empty coded", datatype="Coded")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "s1", "label": "S1"},
                {"op": "add_field", "sectionId": "s1", "conceptId": "8001"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("no answers", result["warnings"][0]["reason"].lower())

    def test_coded_with_retired_answer_rejected(self) -> None:
        self.ciel.bundles["8002"] = _bundle(
            "8002",
            "Coded with retired answer",
            datatype="Coded",
            concept_class="Question",
            answers=[
                {"concept_id": "1111", "display_name": "Active answer"},
                {"concept_id": "2222", "display_name": "Retired answer", "retired": True},
            ],
        )
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "s1", "label": "S1"},
                {"op": "add_field", "sectionId": "s1", "conceptId": "8002"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("retired answer", result["warnings"][0]["reason"].lower())

    def test_common_vital_label_must_match_concept_display(self) -> None:
        self.ciel.bundles["5090"] = _bundle("5090", "Height (cm)", datatype="Numeric")
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5090", "labelOverride": "Oxygen saturation"},
            ],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("does not match concept", result["warnings"][0]["reason"])

    def test_duplicate_add_field_in_one_call_deduped(self) -> None:
        """Six identical add_field ops collapse to one apply + one warning."""
        draft = self._draft()
        result = self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            ],
        )
        self.assertEqual(len(result["applied"]), 2)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("duplicate", result["warnings"][0]["reason"].lower())

    def test_remove_field_accepts_padded_uuid_after_normalization(self) -> None:
        draft = self._draft()
        self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            ],
        )
        result = self.loop.update_form_draft(
            draft.draft_id,
            [{"op": "remove_field", "sectionId": "vitals", "conceptId": "5089" + "A" * 32}],
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(result["warnings"], [])
        draft_after = self.store.get_draft(draft.draft_id)
        self.assertEqual(draft_after.basket["sections"][0]["fields"], [])

    def test_publish_records_audit_trail(self) -> None:
        draft = self._draft()
        self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            ],
        )
        self.loop.build_form_schema(draft.draft_id)
        publish_result = self.loop.publish_form(draft.draft_id, mark_published=True)
        self.assertTrue(publish_result["success"])
        self.assertEqual(publish_result["formUuid"], "test-form-uuid-0001")
        self.assertEqual(len(self.writer.published), 1)
        draft_after = self.store.get_draft(draft.draft_id)
        self.assertEqual(draft_after.status, "published")
        self.assertEqual(draft_after.published_form_uuid, "test-form-uuid-0001")
        operations = [event.operation for event in self.store.list_events(draft.draft_id)]
        self.assertIn("publish_form", operations)


if __name__ == "__main__":
    unittest.main()
