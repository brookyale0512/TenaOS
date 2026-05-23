"""Unit tests for the FormConversationDriver state machine.

Tests use a fake CielClient and fake OpenmrsWriter so they exercise the
state transitions deterministically without touching the live CIEL store or
OpenMRS instance.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service.ciel import ConceptNotFoundError, SeedHit  # noqa: E402
from tena_agent_service.form_builder_tool_loop import FormBuilderToolLoop  # noqa: E402
from tena_agent_service.form_conversation import (  # noqa: E402
    OP_AGENT_PROMPT,
    OP_CANDIDATE_PICKER,
    OP_DUPLICATE_REJECTED,
    OP_ENCOUNTER_TYPE_PICKER,
    OP_ENCOUNTER_TYPE_SET,
    OP_FIELD_ADDED,
    OP_FIELDS_ADDED,
    OP_FORM_PLAN_APPLIED,
    OP_FORM_PLAN_CREATED,
    OP_AGENT_REASONING,
    OP_NAME_SET,
    OP_SET_DECISION,
    ConversationTurn,
    FormConversationDriver,
)
from tena_agent_service.form_drafts import FormDraftStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes


class FakeCielClient:
    def __init__(self, bundles: dict[str, dict[str, Any]], search_hits: dict[str, list[SeedHit]] | None = None) -> None:
        self.bundles = bundles
        self.search_hits = search_hits or {}

    def get_concept_bundle(self, concept_id: str) -> dict[str, Any]:
        if concept_id not in self.bundles:
            raise ConceptNotFoundError(concept_id)
        return self.bundles[concept_id]

    def search_concepts(self, query: str, *args: Any, **kwargs: Any) -> list[SeedHit]:
        return self.search_hits.get(query, self.search_hits.get("*", []))

    def search_form_seeds(self, *args: Any, **kwargs: Any) -> list[SeedHit]:
        return []

    def expand_seed(self, concept_id: str, *, depth: int = 2, allow_retired: bool = False) -> dict[str, Any]:
        return self.get_concept_bundle(concept_id)


class FakeLlmStatus:
    healthy = False

    def to_dict(self) -> dict[str, Any]:
        return {"healthy": False, "message": "test"}


class FakeLlm:
    def health(self) -> Any:
        return FakeLlmStatus()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("LLM should not be called in tests")


class FakeHealthyLlm:
    def __init__(self, content: Any | list[Any]) -> None:
        self.contents = [content] if isinstance(content, str) else list(content)

    def health(self) -> Any:
        class _Status:
            healthy = True

        return _Status()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        content = self.contents.pop(0) if self.contents else ""
        if isinstance(content, dict):
            return {"choices": [{"message": content}]}
        return {"choices": [{"message": {"content": content}}]}


class FakeWriter:
    def __init__(self) -> None:
        # Optional: tests can flip per-uuid responses by mutating this dict
        # before invoking the driver to simulate seeding failures.
        self.unseedable: set[str] = set()

    def list_encounter_types(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            {"uuid": "enc-consult-uuid", "display": "Consultation", "name": "Consultation"},
            {"uuid": "enc-admit-uuid", "display": "Admission", "name": "Admission"},
        ]

    def probe_concept(self, concept_uuid: str) -> dict[str, Any] | None:
        return {"uuid": concept_uuid, "display": "stub", "retired": False}

    def preflight_concepts(self, concept_uuids: list[str]) -> dict[str, list[str]]:
        return {"missing": [], "retired": [], "checked": list({u: True for u in concept_uuids})}

    def _concept_key(self, bundle: dict[str, Any]) -> str:
        concept = bundle.get("concept") or {}
        return str(concept.get("id") or concept.get("concept_id") or "")

    def ensure_concept(self, bundle: dict[str, Any]) -> bool:
        return self._concept_key(bundle) not in self.unseedable

    def ensure_concept_with_answers(self, bundle: dict[str, Any]) -> tuple[bool, list[str]]:
        concept_id = self._concept_key(bundle)
        if concept_id in self.unseedable:
            return False, [concept_id]
        return True, []

    def publish_form(self, schema: dict[str, Any]) -> Any:  # pragma: no cover - not used here
        raise RuntimeError("Publish not used in conversation tests")


def _bundle(
    concept_id: str,
    display_name: str,
    *,
    datatype: str = "Text",
    concept_class: str = "Finding",
    retired: bool = False,
    set_members: list[dict[str, Any]] | None = None,
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
        "set_members": [
            {"target": {**m, "concept_id": str(m.get("concept_id", ""))}}
            for m in (set_members or [])
        ],
    }


def _seed(
    concept_id: str,
    display: str,
    *,
    datatype: str = "Numeric",
    concept_class: str = "Finding",
    set_member_count: int = 0,
    answer_count: int = 0,
) -> SeedHit:
    return SeedHit(
        concept_id=concept_id,
        display_name=display,
        concept_class=concept_class,
        datatype=datatype,
        retired=False,
        answer_count=answer_count,
        set_member_count=set_member_count,
        score=1.0,
        rationale=[],
    )


def _tool_message(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def _agent_build_messages(operations: list[dict[str, Any]], final: str = "Done") -> list[dict[str, Any] | str]:
    return [
        "Brainstorm",
        _tool_message("get_form_draft", {"draftId": "ignored"}, "call_get"),
        _tool_message("update_form_draft", {"draftId": "ignored", "operations": operations}, "call_update"),
        _tool_message("build_form_schema", {"draftId": "ignored"}, "call_build"),
        final,
    ]


# ---------------------------------------------------------------------------
# Test base


class _DriverTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="form-conv-tests-")
        self.db_path = Path(self.tempdir) / "drafts.sqlite3"
        self.store = FormDraftStore(self.db_path)
        self.bundles: dict[str, dict[str, Any]] = {
            "5089": _bundle("5089", "Weight (kg)", datatype="Numeric", extras={"hi_absolute": 250, "low_absolute": 0, "units": "kg", "allow_decimal": True}),
            "5085": _bundle("5085", "Systolic BP", datatype="Numeric"),
            "5086": _bundle("5086", "Diastolic BP", datatype="Numeric"),
            # Vital Signs set with two members for the set-decision tests.
            "vital_set": _bundle(
                "vital_set",
                "Vital Signs",
                datatype="N/A",
                concept_class="ConvSet",
                set_members=[
                    {"concept_id": "5085", "display_name": "Systolic BP", "datatype": "Numeric", "concept_class": "Finding"},
                    {"concept_id": "5086", "display_name": "Diastolic BP", "datatype": "Numeric", "concept_class": "Finding"},
                ],
            ),
            "9999": _bundle("9999", "Retired field", datatype="Text", retired=True),
        }
        self.search_hits: dict[str, list[SeedHit]] = {
            "weight": [_seed("5089", "Weight (kg)")],
            "weight kg": [_seed("5089", "Weight (kg)")],
            "vitals": [_seed("vital_set", "Vital Signs", concept_class="ConvSet", set_member_count=2)],
            "*": [],
        }
        self.ciel = FakeCielClient(self.bundles, self.search_hits)
        self.writer = FakeWriter()
        self.loop = FormBuilderToolLoop(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            llm=FakeLlm(),  # type: ignore[arg-type]
            writer_factory=lambda: self.writer,  # type: ignore[arg-type, return-value]
        )
        self.driver = FormConversationDriver(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            loop=self.loop,
            llm=FakeLlm(),  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def _new_draft(self):
        return self.store.create_draft(
            name="Untitled form",
            owner="alice",
            description=None,
            encounter_type_uuid=None,
        )

    def _operations(self, draft_id: str) -> list[str]:
        return [event.operation for event in self.store.list_events(draft_id)]


# ---------------------------------------------------------------------------
# Tests


class StageOneNameAndEncounterTypeTests(_DriverTestBase):
    def test_kickoff_emits_name_prompt(self) -> None:
        draft = self._new_draft()
        self.driver.kickoff(draft.draft_id)
        ops = self._operations(draft.draft_id)
        self.assertIn(OP_AGENT_PROMPT, ops)

    def test_name_message_advances_to_encounter_type(self) -> None:
        draft = self._new_draft()
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="ANC initial visit"))
        d = self.store.get_draft(draft.draft_id)
        self.assertEqual(d.name, "ANC initial visit")
        self.assertEqual(d.conversation_state, "awaiting_encounter_type")
        ops = self._operations(draft.draft_id)
        self.assertIn(OP_NAME_SET, ops)
        self.assertIn(OP_ENCOUNTER_TYPE_PICKER, ops)

    def test_encounter_type_pick_advances_to_question(self) -> None:
        draft = self._new_draft()
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="ANC initial visit"))
        self.driver.handle_user_turn(
            draft.draft_id,
            ConversationTurn(kind="action", action="pick_encounter_type", payload={"encounterTypeUuid": "enc-consult-uuid", "display": "Consultation"}),
        )
        d = self.store.get_draft(draft.draft_id)
        self.assertEqual(d.encounter_type_uuid, "enc-consult-uuid")
        self.assertEqual(d.conversation_state, "awaiting_question")
        self.assertIn(OP_ENCOUNTER_TYPE_SET, self._operations(draft.draft_id))


class ModelUnavailableTests(_DriverTestBase):
    def _prepare(self) -> str:
        draft = self._new_draft()
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="ANC"))
        self.driver.handle_user_turn(
            draft.draft_id,
            ConversationTurn(kind="action", action="pick_encounter_type", payload={"encounterTypeUuid": "enc-consult-uuid"}),
        )
        return draft.draft_id

    def test_message_requires_gemma_online(self) -> None:
        draft_id = self._prepare()
        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="add weight"))
        d = self.store.get_draft(draft_id)
        self.assertEqual(d.conversation_state, "awaiting_question")
        self.assertEqual(d.basket.get("sections") or [], [])
        last_prompt = next(event for event in reversed(self.store.list_events(draft_id)) if event.operation == OP_AGENT_PROMPT)
        self.assertIn("Gemma 4 online", last_prompt.detail)


class WholeFormPlanTests(_DriverTestBase):
    def _prepare(self) -> str:
        draft = self._new_draft()
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="ANC"))
        self.driver.handle_user_turn(
            draft.draft_id,
            ConversationTurn(kind="action", action="pick_encounter_type", payload={"encounterTypeUuid": "enc-consult-uuid"}),
        )
        return draft.draft_id

    def test_whole_form_request_builds_basket_from_text_plan(self) -> None:
        self.bundles["5090"] = _bundle("5090", "Height (cm)", datatype="Numeric")
        self.bundles["1063"] = _bundle(
            "1063",
            "HIV status",
            datatype="Coded",
            answers=[
                {"concept_id": "703", "display_name": "Positive"},
                {"concept_id": "664", "display_name": "Negative"},
            ],
        )
        self.search_hits["weight"] = [_seed("5089", "Weight (kg)")]
        self.search_hits["height"] = [_seed("5090", "Height (cm)")]
        self.search_hits["hiv status"] = [_seed("1063", "HIV status", datatype="Coded", concept_class="Question", answer_count=2)]
        plan = """Form: ANC intake
Section: Vitals
- Weight
- Height
Section: Screening
- HIV status
"""
        healthy_llm = FakeHealthyLlm(_agent_build_messages([
            {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089", "label": "Weight"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5090", "label": "Height"},
            {"op": "add_section", "sectionId": "screening", "label": "Screening"},
            {"op": "add_field", "sectionId": "screening", "conceptId": "1063", "label": "HIV status"},
        ]))
        self.driver = FormConversationDriver(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            loop=self.loop,
            llm=healthy_llm,  # type: ignore[arg-type]
        )
        draft = self.store.create_draft(
            name="ANC intake",
            owner="alice",
            description=None,
            encounter_type_uuid="enc-consult-uuid",
        )
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        draft_id = draft.draft_id

        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="build me ANC intake form"))

        draft = self.store.get_draft(draft_id)
        self.assertEqual(draft.conversation_state, "awaiting_question")
        sections = draft.basket.get("sections") or []
        self.assertEqual([section["label"] for section in sections], ["Vitals", "Screening"])
        field_ids = [field["conceptId"] for section in sections for field in section["fields"]]
        self.assertEqual(field_ids, ["5089", "5090", "1063"])
        self.assertIsNotNone(draft.last_schema)
        operations = self._operations(draft_id)
        self.assertIn(OP_AGENT_REASONING, operations)
        self.assertIn("model_tool_call", operations)
        self.assertIn("tool_result", operations)
        self.assertIn(OP_FORM_PLAN_APPLIED, operations)

    def test_form_request_given_as_name_runs_after_encounter_pick(self) -> None:
        self.bundles["5090"] = _bundle("5090", "Height (cm)", datatype="Numeric")
        self.search_hits["weight"] = [_seed("5089", "Weight (kg)")]
        self.search_hits["height"] = [_seed("5090", "Height (cm)")]
        plan = """Form: Pediatric intake
Section: Vitals
- Weight
- Height
"""
        healthy_llm = FakeHealthyLlm(_agent_build_messages([
            {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089", "label": "Weight"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5090", "label": "Height"},
        ]))
        self.driver = FormConversationDriver(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            loop=self.loop,
            llm=healthy_llm,  # type: ignore[arg-type]
        )
        draft = self._new_draft()

        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="we need to create pediatric intake form"))
        after_name = self.store.get_draft(draft.draft_id)
        self.assertEqual(after_name.name, "Pediatric intake form")
        self.assertEqual(after_name.conversation_context.get("pendingFormRequest"), "we need to create pediatric intake form")

        self.driver.handle_user_turn(
            draft.draft_id,
            ConversationTurn(kind="action", action="pick_encounter_type", payload={"encounterTypeUuid": "enc-consult-uuid"}),
        )

        after_pick = self.store.get_draft(draft.draft_id)
        field_ids = [field["conceptId"] for section in after_pick.basket["sections"] for field in section["fields"]]
        self.assertEqual(field_ids, ["5089", "5090"])
        operations = self._operations(draft.draft_id)
        self.assertIn(OP_AGENT_REASONING, operations)
        self.assertIn(OP_FORM_PLAN_APPLIED, operations)

    def test_plan_skips_registration_fields_and_unusable_diagnosis_concepts(self) -> None:
        self.bundles["156639"] = _bundle("156639", "History of hypertension", datatype="N/A", concept_class="Diagnosis")
        self.search_hits["patient id"] = [_seed("5325", "Patient ID", datatype="Numeric", concept_class="Misc")]
        self.search_hits["history of hypertension"] = [_seed("156639", "History of hypertension", datatype="N/A", concept_class="Diagnosis")]
        self.search_hits["weight"] = [_seed("5089", "Weight (kg)")]
        plan = """Form: Cardiac intake
Section: Patient Demographics
- Patient ID
Section: Past Medical History
- History of hypertension
Section: Vitals
- Weight
"""
        self.driver = FormConversationDriver(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            loop=self.loop,
            llm=FakeHealthyLlm(_agent_build_messages([
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089", "label": "Weight"},
            ])),  # type: ignore[arg-type]
        )
        draft = self.store.create_draft(
            name="Cardiac intake",
            owner="alice",
            description=None,
            encounter_type_uuid="enc-consult-uuid",
        )
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")

        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="cardiac intake form"))

        after = self.store.get_draft(draft.draft_id)
        field_ids = [field["conceptId"] for section in after.basket["sections"] for field in section["fields"]]
        self.assertEqual(field_ids, ["5089"])
        operations = self._operations(draft.draft_id)
        self.assertIn("model_tool_call", operations)
        self.assertIn("tool_result", operations)

    def test_remove_and_required_change_requests_mutate_existing_basket(self) -> None:
        draft_id = self._prepare()
        self.driver = FormConversationDriver(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            loop=self.loop,
            llm=FakeHealthyLlm([
                "Brainstorm",
                _tool_message("update_form_draft", {"draftId": "ignored", "operations": [{"op": "set_required", "sectionId": "vitals", "conceptId": "5085", "required": True}]}, "call_req"),
                _tool_message("build_form_schema", {"draftId": "ignored"}, "call_build_req"),
                "Required.",
                "Brainstorm",
                _tool_message("update_form_draft", {"draftId": "ignored", "operations": [{"op": "remove_field", "sectionId": "vitals", "conceptId": "5089"}]}, "call_remove"),
                _tool_message("build_form_schema", {"draftId": "ignored"}, "call_build_remove"),
                "Removed.",
            ]),  # type: ignore[arg-type]
        )
        self.loop.update_form_draft(
            draft_id,
            [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089", "label": "Weight"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5085", "label": "Systolic BP"},
            ],
        )
        self.loop.build_form_schema(draft_id)

        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="make systolic required"))
        draft = self.store.get_draft(draft_id)
        field = draft.basket["sections"][0]["fields"][1]
        self.assertTrue(field["required"])

        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="remove weight"))
        draft = self.store.get_draft(draft_id)
        field_ids = [field["conceptId"] for field in draft.basket["sections"][0]["fields"]]
        self.assertEqual(field_ids, ["5085"])


class _PublishingFakeWriter(FakeWriter):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[dict[str, Any]] = []

    def publish_form(self, schema: dict[str, Any]) -> Any:
        from tena_agent_service.openmrs_writer import PublishResult, PublishStep

        form_uuid = "agent-published-form-uuid"
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


class AgentSafetyBoundaryTests(_DriverTestBase):
    """End-to-end agent-loop coverage of safety regressions seen in runtime.

    These tests drive the `_run_gemma_tool_agent` path with mocked tool-call
    responses that mirror real-world failure modes: padded UUIDs, drugs,
    diagnoses, duplicate ops, and a final publish step.
    """

    def _prepared_draft(self) -> str:
        draft = self._new_draft()
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="ANC"))
        self.driver.handle_user_turn(
            draft.draft_id,
            ConversationTurn(kind="action", action="pick_encounter_type", payload={"encounterTypeUuid": "enc-consult-uuid"}),
        )
        return draft.draft_id

    def _swap_driver(self, llm: Any) -> None:
        self.driver = FormConversationDriver(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            loop=self.loop,
            llm=llm,
        )

    def test_padded_uuid_in_agent_add_field_is_normalized(self) -> None:
        """Agent passes 5089AAAA...; middleware normalizes to 5089 and warns."""
        draft_id = self._prepared_draft()
        padded = "5089" + "A" * 32
        self._swap_driver(FakeHealthyLlm(_agent_build_messages([
            {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": padded, "label": "Weight"},
        ])))
        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="add weight"))
        draft = self.store.get_draft(draft_id)
        field_ids = [field["conceptId"] for section in draft.basket["sections"] for field in section["fields"]]
        self.assertEqual(field_ids, ["5089"], "Padded UUID must collapse to CIEL numeric id")

    def test_drug_na_datatype_rejected_by_agent_path(self) -> None:
        """Agent cannot slip a Drug+N/A concept into the basket."""
        self.bundles["907"] = _bundle("907", "Medroxyprogesterone acetate", datatype="N/A", concept_class="Drug")
        draft_id = self._prepared_draft()
        self._swap_driver(FakeHealthyLlm(_agent_build_messages([
            {"op": "add_section", "sectionId": "meds", "label": "Meds"},
            {"op": "add_field", "sectionId": "meds", "conceptId": "907"},
        ])))
        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="add a contraceptive question"))
        draft = self.store.get_draft(draft_id)
        # Section may be empty (no questions) — schema is empty, build_form_schema produced no questions.
        fields = [field for section in draft.basket["sections"] for field in section["fields"]]
        self.assertEqual(fields, [], "Drug+N/A concepts must not enter the basket")

    def test_diagnosis_na_datatype_allowed_as_yes_no_via_agent_path(self) -> None:
        """Diagnosis + N/A (e.g. Amenorrhea, Otalgia) IS a valid Yes/No question.

        CIEL classifies common symptoms as Diagnosis-class N/A; the schema
        builder renders them as Yes/No radios. Blocking them entirely was the
        cause of the 1-field ENT live run at 20:51.
        """
        self.bundles["148989"] = _bundle("148989", "Amenorrhea", datatype="N/A", concept_class="Diagnosis")
        draft_id = self._prepared_draft()
        self._swap_driver(FakeHealthyLlm(_agent_build_messages([
            {"op": "add_section", "sectionId": "hpi", "label": "HPI"},
            {"op": "add_field", "sectionId": "hpi", "conceptId": "148989"},
        ])))
        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="ask about amenorrhea"))
        draft = self.store.get_draft(draft_id)
        field_ids = [f["conceptId"] for section in draft.basket["sections"] for f in section["fields"]]
        self.assertIn("148989", field_ids, "N/A Diagnosis symptoms are valid Yes/No form questions")

    def test_final_message_count_matches_actual_basket(self) -> None:
        """The user-facing summary must come from the basket, not the model.

        The model was observed claiming '10 questions' when the basket had 7,
        and inventing '7 + the one attempted addition (failed) = 9'-style
        arithmetic. The middleware now overrides the model's final prose
        with a deterministic summary derived from the schema.
        """
        draft = self.store.create_draft(
            name="TB",
            owner="alice",
            description=None,
            encounter_type_uuid="enc-consult-uuid",
        )
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        self.bundles["5090"] = _bundle("5090", "Height (cm)", datatype="Numeric")

        # The model claims '10 questions' in its final reply but only commits
        # 3 fields (5089, 5085, 5090). The deterministic footer must override.
        self._swap_driver(FakeHealthyLlm([
            "Brainstorm",
            _tool_message("get_form_draft", {"draftId": "ignored"}, "g"),
            _tool_message(
                "update_form_draft",
                {
                    "draftId": "ignored",
                    "operations": [
                        {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                        {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                        {"op": "add_field", "sectionId": "vitals", "conceptId": "5085"},
                        {"op": "add_field", "sectionId": "vitals", "conceptId": "5090"},
                    ],
                },
                "u",
            ),
            _tool_message("build_form_schema", {"draftId": "ignored"}, "b"),
            "I built a form with 10 wonderful questions across 5 sections.",
        ]))
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="build vitals form"))
        events = self.store.list_events(draft.draft_id)
        final = next(e for e in reversed(events) if e.operation == "form_plan_applied")
        # The model's hallucinated '10 questions' / '5 sections' must NOT
        # appear in the user-facing summary.
        self.assertNotIn("10 wonderful questions", final.detail)
        self.assertNotIn("5 sections", final.detail)
        # The deterministic footer must report 3 questions and 1 section.
        self.assertIn("3 question", final.detail)
        self.assertIn("1 section", final.detail)
        self.assertIn("Vitals", final.detail)

    def test_final_message_surfaces_rejection_warning(self) -> None:
        """When an op was rejected this turn, the summary names the rejection."""
        self.bundles["907"] = _bundle("907", "Medroxyprogesterone", datatype="N/A", concept_class="Drug")
        draft = self.store.create_draft(
            name="TB", owner="alice", description=None, encounter_type_uuid="enc-consult-uuid",
        )
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")

        self._swap_driver(FakeHealthyLlm([
            "Brainstorm",
            _tool_message("get_form_draft", {"draftId": "ignored"}, "g"),
            _tool_message(
                "update_form_draft",
                {
                    "draftId": "ignored",
                    "operations": [
                        {"op": "add_section", "sectionId": "history", "label": "History"},
                        {"op": "add_field", "sectionId": "history", "conceptId": "5089"},
                        {"op": "add_field", "sectionId": "history", "conceptId": "907"},  # Drug, rejected
                    ],
                },
                "u",
            ),
            _tool_message("build_form_schema", {"draftId": "ignored"}, "b"),
            "All done.",
        ]))
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="build form"))
        events = self.store.list_events(draft.draft_id)
        final = next(e for e in reversed(events) if e.operation == "form_plan_applied")
        # The summary must include a Note: line explaining the drug rejection.
        self.assertIn("Note:", final.detail)
        self.assertIn("drug", final.detail.lower())
        # And the basket count must be 1 (only 5089), not 2.
        self.assertIn("1 question", final.detail)

    def test_edit_turn_with_no_commits_emits_honest_message(self) -> None:
        """An edit turn that searches but never commits must say so.

        Mirrors the runtime bug where 'can you add more relevant questions'
        produced 14 searches, 0 update_form_draft calls, but the agent
        replied with the create-mode summary 'I built a CIEL-backed draft
        with 5 questions' — misleading the user into thinking the request
        succeeded.
        """
        # Start with a draft that already has 2 fields (so mode resolves to edit).
        draft = self.store.create_draft(
            name="TB form",
            owner="alice",
            description=None,
            encounter_type_uuid="enc-consult-uuid",
        )
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        self.loop.update_form_draft(
            draft.draft_id,
            [
                {"op": "add_section", "sectionId": "history", "label": "History"},
                {"op": "add_field", "sectionId": "history", "conceptId": "5089"},
                {"op": "add_field", "sectionId": "history", "conceptId": "5085"},
            ],
        )
        self.loop.build_form_schema(draft.draft_id)

        # Canned model output: brainstorm + 5 searches (no commits) + text reply.
        self._swap_driver(FakeHealthyLlm([
            "Brainstorm",
            _tool_message("get_form_draft", {"draftId": "ignored"}, "g0"),
            _tool_message("search_ciel_seeds", {"draftId": "ignored", "query": "cough"}, "s1"),
            _tool_message("search_ciel_seeds", {"draftId": "ignored", "query": "fever"}, "s2"),
            _tool_message("search_ciel_seeds", {"draftId": "ignored", "query": "weight loss"}, "s3"),
            _tool_message("search_ciel_seeds", {"draftId": "ignored", "query": "night sweats"}, "s4"),
            _tool_message("search_ciel_seeds", {"draftId": "ignored", "query": "fatigue"}, "s5"),
        ]))
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="add more relevant questions"))

        events = self.store.list_events(draft.draft_id)
        final = next((e for e in reversed(events) if e.operation == "form_edit_applied"), None)
        self.assertIsNotNone(final, "Edit-mode summary must be emitted")
        detail = final.detail.lower()
        # Critical: must NOT use the misleading create-mode wording.
        self.assertNotIn("i built a ciel-backed draft", detail)
        # Should explicitly tell the user nothing was added.
        self.assertTrue(
            "couldn't" in detail or "could not" in detail or "didn't" in detail,
            f"Expected honest 'no-changes' wording, got: {final.detail!r}",
        )

    def test_repeated_search_phrase_within_turn_is_rejected(self) -> None:
        """The middleware must reject repeat search_ciel_seeds within a turn.

        Mirrors the runtime failure at 21:39 where the model retried
        'close contact TB' 4 times in a row after the small-basket nudge,
        burning budget instead of refining via the vocabulary.
        """
        draft_id = self._prepared_draft()
        self._swap_driver(FakeHealthyLlm([
            "Brainstorm",
            _tool_message("search_ciel_seeds", {"draftId": "ignored", "query": "close contact TB"}, "s1"),
            _tool_message("search_ciel_seeds", {"draftId": "ignored", "query": "close contact TB"}, "s2_dup"),
            "Stopping for now.",
        ]))
        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="build TB form"))

        events = self.store.list_events(draft_id)
        repeated = [e for e in events if e.operation == "search_ciel_seeds_repeated"]
        self.assertEqual(len(repeated), 1, "Repeat search must be flagged exactly once")
        self.assertIn("close contact tb", repeated[0].detail.lower())
        # The corresponding tool_result on the repeat must contain the
        # refinement vocabulary so the model has guidance for the next step.
        repeated_results = [
            e for e in events
            if e.operation == "tool_result" and isinstance(e.payload, dict)
            and isinstance(e.payload.get("result"), dict)
            and (e.payload["result"].get("phrase") == "close contact tb")
        ]
        self.assertEqual(len(repeated_results), 1)
        err = repeated_results[0].payload["result"]["error"]
        self.assertIn("refinement strategy", err.lower())

    def test_diagnosis_boolean_allowed_by_agent_path(self) -> None:
        """Diagnosis class + Boolean datatype IS a valid yes/no form question."""
        self.bundles["123588"] = _bundle("123588", "Tinnitus", datatype="Boolean", concept_class="Diagnosis")
        draft_id = self._prepared_draft()
        self._swap_driver(FakeHealthyLlm(_agent_build_messages([
            {"op": "add_section", "sectionId": "ent", "label": "ENT"},
            {"op": "add_field", "sectionId": "ent", "conceptId": "123588"},
        ])))
        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="ask if patient has tinnitus"))
        draft = self.store.get_draft(draft_id)
        field_ids = [field["conceptId"] for section in draft.basket["sections"] for field in section["fields"]]
        self.assertIn("123588", field_ids, "Boolean diagnoses are valid yes/no form questions")

    def test_duplicate_add_field_in_one_call_deduped_via_agent(self) -> None:
        """Same add_field repeated six times in one tool call -> one apply."""
        draft_id = self._prepared_draft()
        self._swap_driver(FakeHealthyLlm(_agent_build_messages([
            {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
        ])))
        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="add weight repeatedly"))
        draft = self.store.get_draft(draft_id)
        field_ids = [field["conceptId"] for section in draft.basket["sections"] for field in section["fields"]]
        self.assertEqual(field_ids, ["5089"], "Duplicate add_field ops must collapse to one")

    def test_agent_loop_drives_publish_path_end_to_end(self) -> None:
        """The new agent loop builds a draft that can publish to OpenMRS."""
        self.writer = _PublishingFakeWriter()
        self.loop = FormBuilderToolLoop(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            llm=FakeLlm(),  # type: ignore[arg-type]
            writer_factory=lambda: self.writer,  # type: ignore[arg-type, return-value]
        )
        draft_id = self._prepared_draft()
        self._swap_driver(FakeHealthyLlm(_agent_build_messages([
            {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5089", "label": "Weight"},
            {"op": "add_field", "sectionId": "vitals", "conceptId": "5085", "label": "Systolic BP"},
        ])))
        self.driver.handle_user_turn(draft_id, ConversationTurn(kind="message", message="build ANC vitals form"))

        publish_result = self.loop.publish_form(draft_id, mark_published=True)
        self.assertTrue(publish_result["success"], publish_result)
        self.assertEqual(publish_result["formUuid"], "agent-published-form-uuid")
        draft = self.store.get_draft(draft_id)
        self.assertEqual(draft.status, "published")
        self.assertEqual(draft.published_form_uuid, "agent-published-form-uuid")
        self.assertEqual(len(self.writer.published), 1)

    def test_small_basket_triggers_followup_search_on_create(self) -> None:
        """A 2-field basket on a create turn must trigger a nudge for more fields."""
        # Add a third bundle for the follow-up search.
        self.bundles["5090"] = _bundle("5090", "Height (cm)", datatype="Numeric")
        self.search_hits["height"] = [_seed("5090", "Height (cm)")]

        draft = self.store.create_draft(
            name="ANC",
            owner="alice",
            description=None,
            encounter_type_uuid="enc-consult-uuid",
        )
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")

        # Canned tool-call sequence:
        # 1) initial commit with only 2 fields,
        # 2) build_form_schema -> triggers the small-basket nudge,
        # 3) follow-up search,
        # 4) second update_form_draft adding the third field,
        # 5) build_form_schema again,
        # 6) final text summary.
        self._swap_driver(FakeHealthyLlm([
            "Brainstorm",
            _tool_message(
                "update_form_draft",
                {
                    "draftId": "ignored",
                    "operations": [
                        {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                        {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                        {"op": "add_field", "sectionId": "vitals", "conceptId": "5085"},
                    ],
                },
                "call_update1",
            ),
            _tool_message("build_form_schema", {"draftId": "ignored"}, "call_build1"),
            _tool_message(
                "search_ciel_seeds",
                {"draftId": "ignored", "query": "height"},
                "call_search",
            ),
            _tool_message(
                "update_form_draft",
                {
                    "draftId": "ignored",
                    "operations": [
                        {"op": "add_field", "sectionId": "vitals", "conceptId": "5090"},
                    ],
                },
                "call_update2",
            ),
            _tool_message("build_form_schema", {"draftId": "ignored"}, "call_build2"),
            "Done with 3 fields.",
        ]))
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="build me ANC vitals form"))
        d = self.store.get_draft(draft.draft_id)
        field_ids = [field["conceptId"] for section in d.basket["sections"] for field in section["fields"]]
        # Without the nudge the agent would have stopped at 2 fields. The nudge
        # forces it through one more search + add_field cycle.
        self.assertEqual(field_ids, ["5089", "5085", "5090"])

    def test_text_only_replies_terminate_after_two_strikes(self) -> None:
        """A model that returns only text twice in a row must not loop forever."""
        # Build a draft already past Stage 1 so the encounter-type pick does
        # not auto-replay a pending form request through the agent loop.
        draft = self.store.create_draft(
            name="ANC",
            owner="alice",
            description=None,
            encounter_type_uuid="enc-consult-uuid",
        )
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")

        self._swap_driver(FakeHealthyLlm([
            "Brainstorm",
            "I will think about this.",  # text-only turn 1
            "Still thinking.",            # text-only turn 2 -> break
            "Should not be consumed.",   # would only be reached if loop kept going
        ]))
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="vitals"))
        events = self.store.list_events(draft.draft_id)
        tool_call_model_calls = [
            e for e in events
            if e.operation == "model_call" and e.payload.get("phase") == "tool_call"
        ]
        # With a 2-strike text-only break, we should see exactly 2 tool_call rounds.
        self.assertLessEqual(len(tool_call_model_calls), 2, "Text-only loop should stop within 2 retries")
        # And the basket must still be empty — no field was forced in.
        d = self.store.get_draft(draft.draft_id)
        fields = [field for section in (d.basket.get("sections") or []) for field in section.get("fields") or []]
        self.assertEqual(fields, [])


class CompactToolResultTests(unittest.TestCase):
    """The agent must never see padded UUIDs echoed in tool results."""

    def test_schema_in_tool_result_is_collapsed_to_counts(self) -> None:
        from tena_agent_service.form_conversation import _compact_tool_result  # noqa: WPS433

        schema = {
            "name": "ANC",
            "encounterType": "enc-uuid",
            "pages": [
                {
                    "sections": [
                        {
                            "questions": [
                                {
                                    "id": "weight",
                                    "questionOptions": {
                                        "concept": "5089" + "A" * 32,  # padded UUID
                                    },
                                }
                            ]
                        }
                    ]
                }
            ],
        }
        compact = _compact_tool_result({"schema": schema, "validation": {"issues": []}})
        self.assertEqual(compact["schema"], {"name": "ANC", "encounterType": "enc-uuid", "questionCount": 1})
        # The padded UUID body must be gone.
        self.assertNotIn("A" * 32, json.dumps(compact))

    def test_basket_in_tool_result_replaced_with_ciel_id_summary(self) -> None:
        from tena_agent_service.form_conversation import _compact_tool_result  # noqa: WPS433

        basket = {
            "sections": [
                {
                    "sectionId": "vitals",
                    "label": "Vitals",
                    "fields": [
                        {"conceptId": "5089", "labelOverride": "Weight", "required": True},
                        {"conceptId": "5085", "labelOverride": None, "required": False},
                    ],
                }
            ]
        }
        compact = _compact_tool_result({"basket": basket, "applied": [], "warnings": []})
        self.assertNotIn("basket", compact)
        self.assertEqual(compact["basketSummary"][0]["sectionId"], "vitals")
        ids = [field["conceptId"] for field in compact["basketSummary"][0]["fields"]]
        self.assertEqual(ids, ["5089", "5085"])

    def test_get_form_draft_response_strips_last_schema(self) -> None:
        from tena_agent_service.form_conversation import _compact_tool_result  # noqa: WPS433

        draft_dict = {
            "draftId": "abc",
            "name": "ANC",
            "encounterTypeUuid": "enc-uuid",
            "status": "draft",
            "basket": {"sections": []},
            "lastSchema": {"pages": [{"sections": [{"questions": [{"questionOptions": {"concept": "5089" + "A" * 32}}]}]}]},
            "lastValidation": {"issues": []},
        }
        compact = _compact_tool_result(draft_dict)
        self.assertNotIn("lastSchema", compact)
        self.assertNotIn("lastValidation", compact)
        self.assertNotIn("A" * 32, json.dumps(compact))


if __name__ == "__main__":
    unittest.main()
