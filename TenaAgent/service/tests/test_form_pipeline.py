"""Unit + end-to-end tests for the v2 grounded form-builder pipeline.

These exercise the worklist contract, the research phase (Phase A), and the
full research -> resolve -> repair -> build flow through the conversation driver
with the ``form_agent_pipeline_v2`` flag enabled. The legacy runner keeps its
own coverage in ``test_form_conversation.py``; this file validates the parallel
path only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service.ciel import ConceptNotFoundError, SeedHit  # noqa: E402
from tena_agent_service.config import Settings  # noqa: E402
from tena_agent_service.form_builder_tool_loop import FormBuilderToolLoop  # noqa: E402
from tena_agent_service.form_conversation import (  # noqa: E402
    OP_AGENT_REASONING,
    OP_FORM_PLAN_APPLIED,
    ConversationTurn,
    FormConversationDriver,
)
from tena_agent_service.form_drafts import FormDraftStore  # noqa: E402
from tena_agent_service.form_pipeline import research_phase  # noqa: E402
from tena_agent_service.form_pipeline.worklist import QuestionWorklist, sanitize_items  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes


class FakeCielClient:
    def __init__(self, bundles: dict[str, dict[str, Any]], search_hits: dict[str, list[SeedHit]]) -> None:
        self.bundles = bundles
        self.search_hits = search_hits

    def get_concept_bundle(self, concept_id: str) -> dict[str, Any]:
        if concept_id not in self.bundles:
            raise ConceptNotFoundError(concept_id)
        return self.bundles[concept_id]

    def search_concepts(self, query: str, *args: Any, **kwargs: Any) -> list[SeedHit]:
        return self.search_hits.get(query, self.search_hits.get("*", []))

    def search_form_seeds(self, *args: Any, **kwargs: Any) -> list[SeedHit]:
        query = str(args[0] if args else kwargs.get("query", ""))
        return self.search_hits.get(query, self.search_hits.get("*", []))

    def expand_seed(self, concept_id: str, *, depth: int = 2, allow_retired: bool = False) -> dict[str, Any]:
        return self.get_concept_bundle(concept_id)


class FakeWriter:
    def list_encounter_types(self, limit: int = 50) -> list[dict[str, Any]]:
        return [{"uuid": "enc-consult-uuid", "display": "Consultation", "name": "Consultation"}]

    def ensure_concept_with_answers(self, bundle: dict[str, Any]) -> tuple[bool, list[str]]:
        return True, []

    def ensure_concept(self, bundle: dict[str, Any]) -> bool:
        return True


class FakeHealthyLlm:
    """Pops scripted responses; supports text, single dict, and tool messages."""

    def __init__(self, scripted: list[Any]) -> None:
        self.scripted = list(scripted)
        self.calls = 0

    def health(self) -> Any:
        class _Status:
            healthy = True

        return _Status()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        content = self.scripted.pop(0) if self.scripted else ""
        if isinstance(content, dict):
            return {"choices": [{"message": content}]}
        return {"choices": [{"message": {"content": content}}]}


def _bundle(concept_id: str, display: str, *, datatype: str = "Numeric", concept_class: str = "Finding") -> dict[str, Any]:
    return {
        "concept": {
            "concept_id": concept_id,
            "display_name": display,
            "datatype": datatype,
            "concept_class": concept_class,
            "retired": False,
            "extras": {},
        },
        "answers": [],
        "set_members": [],
    }


def _seed(concept_id: str, display: str, *, datatype: str = "Numeric", concept_class: str = "Finding", answer_count: int = 0) -> SeedHit:
    return SeedHit(
        concept_id=concept_id,
        display_name=display,
        concept_class=concept_class,
        datatype=datatype,
        retired=False,
        answer_count=answer_count,
        set_member_count=0,
        score=1.0,
        rationale=[],
    )


def _finalize_worklist_msg(questions: list[dict[str, Any]], summary: str = "subject") -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [
            {
                "id": "call_finalize",
                "type": "function",
                "function": {
                    "name": "finalize_worklist",
                    "arguments": json.dumps({"summary": summary, "questions": questions}),
                },
            }
        ],
    }


def _tool_msg(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


# ---------------------------------------------------------------------------
# Worklist contract unit tests


class ToolCallExtractionTests(unittest.TestCase):
    def test_tagged_call_with_nested_json_is_parsed(self) -> None:
        from tena_agent_service.form_pipeline._llm_utils import extract_tool_calls

        content = (
            '<tena_call>{"name": "update_form_draft", "arguments": {"draftId": "d1", '
            '"operations": [{"op": "add_section", "sectionId": "s", "label": "S"}, '
            '{"op": "add_field", "sectionId": "s", "conceptId": "5959"}]}}</tena_call>'
        )
        calls = extract_tool_calls({"content": content})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "update_form_draft")
        self.assertEqual(len(calls[0]["arguments"]["operations"]), 2)
        self.assertEqual(calls[0]["arguments"]["operations"][1]["conceptId"], "5959")

    def test_bare_json_tool_call_without_tags(self) -> None:
        from tena_agent_service.form_pipeline._llm_utils import extract_tool_calls

        content = '{"name": "search_ciel_seeds", "arguments": {"query": "cough"}}'
        calls = extract_tool_calls({"content": content})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "search_ciel_seeds")
        self.assertEqual(calls[0]["arguments"]["query"], "cough")

    def test_native_tool_calls_preferred(self) -> None:
        from tena_agent_service.form_pipeline._llm_utils import extract_tool_calls

        msg = {
            "tool_calls": [
                {"id": "c1", "function": {"name": "search_ciel_seeds", "arguments": '{"query": "fever"}'}}
            ],
            "content": "",
        }
        calls = extract_tool_calls(msg)
        self.assertEqual(calls[0]["name"], "search_ciel_seeds")
        self.assertEqual(calls[0]["arguments"]["query"], "fever")


class WorklistContractTests(unittest.TestCase):
    def test_drops_action_labels_and_dedups_and_sorts(self) -> None:
        items = sanitize_items(
            [
                {"label": "Start TB treatment", "priority": 1},  # disallowed (treatment)
                {"label": "Cough duration", "datatypeHint": "Numeric", "priority": 3},
                {"label": "cough duration", "priority": 2},  # dup of above by normalized label
                {"label": "Fever present", "datatypeHint": "Boolean", "priority": 1},
                {"label": "Refer to clinic"},  # disallowed (refer)
            ]
        )
        labels = [item.label for item in items]
        self.assertEqual(labels, ["Fever present", "Cough duration"])
        self.assertEqual(items[0].priority, 1)
        self.assertEqual(items[1].datatype_hint, "Numeric")

    def test_unknown_datatype_collapses_to_boolean(self) -> None:
        items = sanitize_items([{"label": "Has rash", "datatypeHint": "Weird"}])
        self.assertEqual(items[0].datatype_hint, "Boolean")

    def test_search_phrases_default_to_label(self) -> None:
        items = sanitize_items([{"label": "Weight"}])
        self.assertEqual(items[0].search_phrases, ["Weight"])

    def test_prompt_block_lists_questions(self) -> None:
        wl = QuestionWorklist(items=sanitize_items([{"label": "Weight", "datatypeHint": "Numeric"}]), subject_summary="S")
        block = wl.to_prompt_block()
        self.assertIn("Weight", block)
        self.assertIn("Numeric", block)


# ---------------------------------------------------------------------------
# Research phase unit tests (Phase A)


class ResearchPhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="form-pipeline-")
        self.db_path = Path(self.tempdir) / "drafts.sqlite3"
        self.store = FormDraftStore(self.db_path)
        self.settings = replace(Settings.from_env(), form_agent_research_max_searches=2)
        self._orig_kb = research_phase._make_kb_client

    def tearDown(self) -> None:
        research_phase._make_kb_client = self._orig_kb  # type: ignore[assignment]
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def _draft(self):
        return self.store.create_draft(name="TB form", owner="alice", description=None, encounter_type_uuid="enc-consult-uuid")

    def test_research_without_kb_finalizes_directly(self) -> None:
        research_phase._make_kb_client = lambda settings: None  # type: ignore[assignment]
        draft = self._draft()
        llm = FakeHealthyLlm([
            _finalize_worklist_msg([
                {"label": "Cough duration", "datatypeHint": "Numeric", "priority": 1},
                {"label": "Fever", "datatypeHint": "Boolean", "priority": 2},
            ])
        ])
        worklist = research_phase.run_research_phase(
            llm=llm, store=self.store, draft=draft, request="build a TB intake form", mode="create", settings=self.settings
        )
        self.assertEqual(worklist.labels(), ["Cough duration", "Fever"])
        self.assertFalse(worklist.used_guidelines)
        ops = [e.operation for e in self.store.list_events(draft.draft_id)]
        self.assertIn(OP_AGENT_REASONING, ops)

    def test_research_with_kb_searches_then_finalizes(self) -> None:
        class FakeKb:
            def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
                return [{"title": "TB assessment", "text": "Assess cough, fever, weight loss.", "source": "WHO"}]

        research_phase._make_kb_client = lambda settings: FakeKb()  # type: ignore[assignment]
        draft = self._draft()
        llm = FakeHealthyLlm([
            _tool_msg("search_guidelines", {"query": "tuberculosis assessment symptoms"}, "s1"),
            _finalize_worklist_msg([{"label": "Cough duration", "datatypeHint": "Numeric", "priority": 1}]),
        ])
        worklist = research_phase.run_research_phase(
            llm=llm, store=self.store, draft=draft, request="TB form", mode="create", settings=self.settings
        )
        self.assertEqual(worklist.labels(), ["Cough duration"])
        self.assertTrue(worklist.used_guidelines)
        events = self.store.list_events(draft.draft_id)
        # Guideline searches are journaled with the same operations as CIEL tool
        # calls so the UI renders them identically as visible tool steps.
        tool_calls = [e for e in events if e.operation == "model_tool_call"]
        tool_results = [e for e in events if e.operation == "tool_result"]
        self.assertTrue(any(e.payload.get("toolName") == "search_guidelines" for e in tool_calls))
        self.assertTrue(any(e.payload.get("toolName") == "search_guidelines" for e in tool_results))


# ---------------------------------------------------------------------------
# End-to-end pipeline tests (driver with flag on)


class PipelineE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="form-pipeline-e2e-")
        self.db_path = Path(self.tempdir) / "drafts.sqlite3"
        self.store = FormDraftStore(self.db_path)
        self.bundles = {
            "5089": _bundle("5089", "Weight (kg)", datatype="Numeric"),
            "5085": _bundle("5085", "Systolic BP", datatype="Numeric"),
        }
        self.search_hits = {
            "weight": [_seed("5089", "Weight (kg)")],
            "systolic": [_seed("5085", "Systolic BP")],
            "*": [],
        }
        self.ciel = FakeCielClient(self.bundles, self.search_hits)
        self.writer = FakeWriter()
        self.loop = FormBuilderToolLoop(
            store=self.store, ciel=self.ciel, llm=None, writer_factory=lambda: self.writer  # type: ignore[arg-type]
        )
        self.settings = replace(
            Settings.from_env(),
            form_agent_pipeline_v2=True,
            form_agent_target_min_fields=2,
            form_agent_research_max_searches=2,
        )
        self._orig_kb = research_phase._make_kb_client
        research_phase._make_kb_client = lambda settings: None  # type: ignore[assignment]

    def tearDown(self) -> None:
        research_phase._make_kb_client = self._orig_kb  # type: ignore[assignment]
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def _driver(self, llm: Any) -> FormConversationDriver:
        return FormConversationDriver(
            store=self.store, ciel=self.ciel, loop=self.loop, llm=llm, settings=self.settings  # type: ignore[arg-type]
        )

    def _create_ready_draft(self) -> str:
        draft = self.store.create_draft(name="Vitals", owner="alice", description=None, encounter_type_uuid="enc-consult-uuid")
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        return draft.draft_id

    def test_full_pipeline_builds_form(self) -> None:
        draft_id = self._create_ready_draft()
        llm = FakeHealthyLlm([
            _finalize_worklist_msg([
                {"label": "Weight", "datatypeHint": "Numeric", "searchPhrases": ["weight"], "priority": 1},
                {"label": "Systolic BP", "datatypeHint": "Numeric", "searchPhrases": ["systolic"], "priority": 2},
            ]),
            _tool_msg(
                "update_form_draft",
                {"operations": [
                    {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                    {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                    {"op": "add_field", "sectionId": "vitals", "conceptId": "5085"},
                ]},
                "u1",
            ),
            _tool_msg("build_form_schema", {}, "b1"),
            "Built the vitals form.",
        ])
        self._driver(llm).handle_user_turn(draft_id, ConversationTurn(kind="message", message="build a vitals form"))

        draft = self.store.get_draft(draft_id)
        field_ids = [f["conceptId"] for s in draft.basket["sections"] for f in s["fields"]]
        self.assertEqual(field_ids, ["5089", "5085"])
        self.assertIsNotNone(draft.last_schema)
        ops = [e.operation for e in self.store.list_events(draft_id)]
        self.assertIn(OP_FORM_PLAN_APPLIED, ops)
        self.assertIn("model_tool_call", ops)
        self.assertIn("tool_result", ops)

    def test_coverage_repair_commits_when_model_never_does(self) -> None:
        draft_id = self._create_ready_draft()
        llm = FakeHealthyLlm([
            _finalize_worklist_msg([{"label": "Weight", "datatypeHint": "Numeric", "searchPhrases": ["weight"], "priority": 1}]),
            _tool_msg("search_ciel_seeds", {"query": "weight"}, "s1"),
            "I found weight but will not commit.",
            json.dumps({"operations": [
                {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                {"op": "add_field", "sectionId": "vitals", "conceptId": "5089", "label": "Weight"},
            ]}),
        ])
        self._driver(llm).handle_user_turn(draft_id, ConversationTurn(kind="message", message="build weight form"))

        draft = self.store.get_draft(draft_id)
        field_ids = [f["conceptId"] for s in draft.basket["sections"] for f in s["fields"]]
        self.assertIn("5089", field_ids)
        ops = [e.operation for e in self.store.list_events(draft_id)]
        self.assertIn("recovery_commit_started", ops)


if __name__ == "__main__":
    unittest.main()
