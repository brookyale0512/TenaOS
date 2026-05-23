"""Driver-level tests for the report conversation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service.ciel import ConceptNotFoundError  # noqa: E402
from tena_agent_service.openmrs_reader import FilterSpec  # noqa: E402
from tena_agent_service.report_builder_tool_loop import ReportBuilderToolLoop  # noqa: E402
from tena_agent_service.report_conversation import (  # noqa: E402
    ConversationTurn,
    OP_AGENT_REASONING,
    OP_REPORT_EDIT_APPLIED,
    OP_REPORT_PLAN_APPLIED,
    ReportConversationDriver,
    _report_name_from_brainstorm,
    _report_name_from_request,
)
from tena_agent_service.report_drafts import ReportDraftStore  # noqa: E402


# Reuse the fakes / helpers from test_report_builder.
from tests.test_report_builder import FakeCielClient, FakeReader, _bundle, _obs_entry  # noqa: E402


class FakeLlmStatus:
    healthy = True

    def to_dict(self) -> dict[str, Any]:
        return {"healthy": True, "message": "test"}


class FakeUnhealthyLlm:
    def health(self) -> Any:
        class _Status:
            healthy = False

        return _Status()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("LLM should not be called when unhealthy")


class FakeHealthyLlm:
    def __init__(self, contents: list[Any]) -> None:
        self.contents = list(contents)

    def health(self) -> Any:
        return FakeLlmStatus()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        content = self.contents.pop(0) if self.contents else ""
        if isinstance(content, dict):
            return {"choices": [{"message": content}]}
        return {"choices": [{"message": {"content": content}}]}


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


class _DriverTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="report-conv-tests-")
        self.db_path = Path(self.tempdir) / "rdb.sqlite3"
        self.store = ReportDraftStore(self.db_path)
        self.bundles = {
            "1479": _bundle("1479", "Night sweats", datatype="Boolean"),
            "1487": _bundle("1487", "Cough", datatype="Boolean"),
        }
        self.ciel = FakeCielClient(self.bundles)
        self.reader = FakeReader()
        self.loop = ReportBuilderToolLoop(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            reader_factory=lambda progress=None: self.reader,
        )
        self.driver = ReportConversationDriver(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            loop=self.loop,
            llm=FakeUnhealthyLlm(),  # default; tests can swap
        )

    def tearDown(self) -> None:
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def _swap_llm(self, llm: Any) -> None:
        self.driver = ReportConversationDriver(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            loop=self.loop,
            llm=llm,
        )

    def _new_draft(self):
        return self.store.create_draft(name="Untitled report", owner="alice")


class KickoffAndNameTurnTests(_DriverTestBase):
    def test_kickoff_emits_name_prompt(self) -> None:
        draft = self._new_draft()
        self.driver.kickoff(draft.draft_id)
        events = self.store.list_events(draft.draft_id)
        prompts = [e for e in events if e.operation == "agent_prompt"]
        self.assertTrue(prompts)

    def test_name_turn_records_user_message_and_advances(self) -> None:
        # Use a healthy LLM so the auto-run after naming is exercised; we
        # don't care about its tool calls here, just that the name is set.
        self._swap_llm(FakeHealthyLlm(["Plan: Cough count last quarter\nName: Cough Count\nType: count", "stopping"]))
        draft = self._new_draft()
        self.driver.handle_user_turn(
            draft.draft_id,
            ConversationTurn(kind="message", message="how many patients had cough last quarter"),
        )
        d = self.store.get_draft(draft.draft_id)
        # Name should have been derived from the request.
        self.assertNotEqual(d.name, "Untitled report")
        self.assertEqual(d.name, "Cough Count")

    def test_report_name_from_request_is_short_and_clinical(self) -> None:
        self.assertEqual(
            _report_name_from_request("patients with weigh loss month over month past 6 months"),
            "Monthly Weight Loss (Last 6 Months)",
        )
        self.assertEqual(
            _report_name_from_request("Show weight loss cases by sex and age group in the last 6 months as stacked bars."),
            "Weight Loss Stacked Breakdown by Sex and Age Group (Last 6 Months)",
        )
        self.assertEqual(
            _report_name_from_request("Show the monthly weight loss rate among patients seen over the past 6 months."),
            "Weight Loss Rate (Last 6 Months)",
        )

    def test_report_name_prefers_brainstorm_name(self) -> None:
        brainstorm = """Plan: Monthly trend of patients with weight loss over the past 6 months
Name: Monthly Weight Loss
Type: pivot
Date range: last 6 months"""
        self.assertEqual(
            _report_name_from_brainstorm(brainstorm, "ppatients with weight lostt month over motnh ppast 6 months"),
            "Monthly Weight Loss",
        )


class AgentLoopTests(_DriverTestBase):
    def test_count_happy_path_via_agent_loop(self) -> None:
        # The fake LLM returns the tool-call script that produces a count report.
        self.reader.observation_entries = {
            "1487AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA": [
                _obs_entry("p1", valueBoolean=True),
                _obs_entry("p2", valueBoolean=True),
            ]
        }
        canned = [
            "Brainstorm",
            _tool_message(
                "update_report_draft",
                {
                    "draftId": "_",
                    "operations": [
                        {"op": "set_report_type", "reportType": "count"},
                        {"op": "set_date_range", "text": "last quarter"},
                        {"op": "set_visualization", "template": "filter_bar", "title": "Cough matches by filter"},
                        {"op": "add_filter", "conceptId": "1487", "valueBool": True, "label": "Cough"},
                    ],
                },
                "u1",
            ),
            _tool_message("build_report_query", {"draftId": "_"}, "b1"),
            _tool_message("run_report", {"draftId": "_"}, "r1"),
            "All done.",
        ]
        self._swap_llm(FakeHealthyLlm(canned))

        draft = self.store.create_draft(name="TB count", report_type="count")
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="how many had cough last quarter"))

        d = self.store.get_draft(draft.draft_id)
        self.assertEqual(d.spec["reportType"], "count")
        self.assertEqual(len(d.spec["filters"]), 1)
        self.assertEqual(d.spec["visualization"]["template"], "filter_bar")
        self.assertIsNotNone(d.last_result)
        self.assertEqual(d.last_result["total"], 2)
        self.assertEqual(d.last_result["visualization"]["title"], "Cough matches by filter")
        events = self.store.list_events(draft.draft_id)
        self.assertTrue(any(e.operation == OP_REPORT_EDIT_APPLIED or e.operation == OP_REPORT_PLAN_APPLIED for e in events))

    def test_offline_gemma_returns_clear_error(self) -> None:
        draft = self.store.create_draft(name="X")
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        self.driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="how many had cough"))
        events = self.store.list_events(draft.draft_id)
        prompts = [e for e in events if e.operation == "agent_prompt"]
        self.assertTrue(any("Gemma 4 online" in p.detail for p in prompts))


class NestedLogicWarningTests(_DriverTestBase):
    def test_parenthesised_request_surfaces_join_mode_warning(self) -> None:
        self.reader.observation_entries = {
            "1487AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA": [_obs_entry("p1", valueBoolean=True)],
            "1479AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA": [_obs_entry("p2", valueBoolean=True)],
        }
        canned = [
            "Brainstorm",
            _tool_message(
                "update_report_draft",
                {
                    "draftId": "_",
                    "operations": [
                        {"op": "set_report_type", "reportType": "count"},
                        {"op": "set_date_range", "text": "last quarter"},
                        {"op": "add_filter", "conceptId": "1487", "valueBool": True, "label": "Cough"},
                        {"op": "add_filter", "conceptId": "1479", "valueBool": True, "label": "Night sweats"},
                    ],
                },
                "u1",
            ),
            _tool_message("build_report_query", {"draftId": "_"}, "b1"),
            _tool_message("run_report", {"draftId": "_"}, "r1"),
            "done",
        ]
        self._swap_llm(FakeHealthyLlm(canned))

        draft = self.store.create_draft(name="Nested", report_type="count")
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        self.driver.handle_user_turn(
            draft.draft_id,
            ConversationTurn(kind="message", message="(cough AND weight loss) OR fever"),
        )
        events = self.store.list_events(draft.draft_id)
        finals = [e for e in events if e.operation in (OP_REPORT_PLAN_APPLIED, OP_REPORT_EDIT_APPLIED)]
        self.assertTrue(finals)
        self.assertIn("single join mode", finals[-1].detail.lower())


if __name__ == "__main__":
    unittest.main()
