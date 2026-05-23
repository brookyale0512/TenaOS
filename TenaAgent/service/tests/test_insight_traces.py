"""Unit tests for the InsightTraceStore and the attach_store wrapper."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service.insight_traces import InsightTraceStore, attach_store  # noqa: E402
from tena_agent_service.models import InsightTrace, MaterialTrace, ScribeTrace  # noqa: E402


class InsightTraceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="insight-trace-tests-")

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _make_store(self, workflow: str = "cds") -> InsightTraceStore:
        return InsightTraceStore(Path(self.tempdir) / f"{workflow}.sqlite3", workflow)  # type: ignore[arg-type]

    def test_run_lifecycle_persists(self) -> None:
        store = self._make_store("cds")
        run_id = store.start_run(summary="test", context={"patient": "x"})
        self.assertIsInstance(run_id, str)

        store.append_event(run_id, actor="middleware", operation="search_guidelines", detail="query=test", payload={"hits": 4})
        store.append_event(run_id, actor="gemma", operation="model_tool_call", detail="format", payload={"tokens": 1024})
        store.finish_run(run_id, status="completed", summary="ok")

        run = store.get_run(run_id)
        assert run is not None
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.workflow, "cds")
        self.assertEqual(run.context_json, {"patient": "x"})

        events = store.list_events(run_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].operation, "search_guidelines")
        self.assertEqual(events[0].payload, {"hits": 4})
        self.assertEqual(events[1].operation, "model_tool_call")

    def test_list_runs_orders_recent_first(self) -> None:
        store = self._make_store("material")
        ids = [store.start_run(summary=f"run-{i}") for i in range(3)]
        runs = store.list_runs(limit=10)
        self.assertEqual(len(runs), 3)
        self.assertEqual({r.run_id for r in runs}, set(ids))

    def test_append_event_is_noop_when_store_disabled(self) -> None:
        store = self._make_store("scribe")
        store._enabled = False
        run_id = store.start_run(summary="x")
        store.append_event(run_id, actor="gemma", operation="noop", detail="noop")
        # Disabled stores never raise.
        self.assertIsInstance(run_id, str)


class AttachStoreWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="attach-store-tests-")
        self.cds_store = InsightTraceStore(Path(self.tempdir) / "cds.sqlite3", "cds")
        self.mat_store = InsightTraceStore(Path(self.tempdir) / "material.sqlite3", "material")
        self.scribe_store = InsightTraceStore(Path(self.tempdir) / "scribe.sqlite3", "scribe")

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_attach_persists_add_complete(self) -> None:
        trace = InsightTrace(patient_uuid="patient-1")
        run_id = attach_store(trace, self.cds_store, summary="cds smoke")

        trace.add("context", "Built patient context", "summary text", {"summary": "s"})
        trace.add("model_tool_call", "search_guidelines", "query=hypertension", {"q": "hypertension"})
        trace.add("middleware_result", "search_guidelines: hypertension", "KB returned 5 hits", {"hits": 5})
        trace.add("model_summary", "Final CDS", "Generated 5-section report")
        trace.complete({"status": "recommendation", "summary": "ok"})

        run = self.cds_store.get_run(run_id)
        assert run is not None
        self.assertEqual(run.status, "completed")

        events = self.cds_store.list_events(run_id)
        operations = [e.operation for e in events]
        self.assertIn("context", operations)
        self.assertIn("model_tool_call", operations)
        self.assertIn("middleware_result", operations)
        self.assertIn("model_summary", operations)
        # In-memory trace still works.
        self.assertEqual(len(trace.events), 4)
        self.assertEqual(trace.status, "completed")

    def test_attach_persists_fail(self) -> None:
        trace = MaterialTrace(patient_uuid="patient-2")
        run_id = attach_store(trace, self.mat_store, summary="material smoke")

        trace.add("model_reasoning", "Phase A", "Thinking about TB material")
        trace.fail("TenaOS-LLM unavailable", {"reason": "timeout"})

        run = self.mat_store.get_run(run_id)
        assert run is not None
        self.assertEqual(run.status, "failed")

        events = self.mat_store.list_events(run_id)
        self.assertTrue(any(e.operation == "error" for e in events))
        self.assertEqual(trace.status, "failed")

    def test_attach_to_scribe_trace_is_safe(self) -> None:
        trace = ScribeTrace(patient_uuid="patient-3")
        run_id = attach_store(trace, self.scribe_store)

        trace.add("model_reasoning", "Scribe reasoning", "Reading note")
        trace.complete({"soap": {}, "concepts": [], "observations": [], "medications": []})

        events = self.scribe_store.list_events(run_id)
        self.assertEqual(len(events), 1)
        run = self.scribe_store.get_run(run_id)
        assert run is not None
        self.assertEqual(run.workflow, "scribe")


class ScribeToolLoopTraceStoreTests(unittest.TestCase):
    """Verify the scribe loop emits events into its trace store when wired.

    Uses a fake LLM that drives the loop through one search then a finalize.
    No real CIEL DB needed — the scribe loop tolerates a stub ciel implementation
    for the smoke path.
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="scribe-trace-loop-")
        self.store = InsightTraceStore(Path(self.tempdir) / "scribe.sqlite3", "scribe")

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_scribe_loop_persists_events(self) -> None:
        from tena_agent_service.scribe_tool_loop import SoapScribeToolLoop  # noqa: E402

        class StubCiel:
            def search_concepts(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

            def get_concept_bundle(self, concept_id: str) -> dict[str, Any]:
                return {"concept": {"concept_id": concept_id, "display_name": "stub"}, "answers": [], "set_members": []}

            def expand_seed(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                return {"concept": {"display_name": "stub"}, "answers": [], "set_members": []}

        @dataclass
        class FakeLlm:
            calls: list[dict[str, Any]] = field(default_factory=list)

            def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
                self.calls.append({"messages": messages, "kwargs": kwargs})
                # Turn 1: emit a search_ciel_concepts tool call.
                if len(self.calls) == 1:
                    return {
                        "choices": [{
                            "message": {
                                "content": "",
                                "tool_calls": [{
                                    "id": "call_1",
                                    "function": {
                                        "name": "search_ciel_concepts",
                                        "arguments": '{"query": "uti", "kind": "diagnosis"}',
                                    },
                                }],
                            }
                        }]
                    }
                # Turn 2+: finalize.
                return {
                    "choices": [{
                        "message": {
                            "content": "",
                            "tool_calls": [{
                                "id": "call_final",
                                "function": {
                                    "name": "finalize_soap_note",
                                    "arguments": '{"soap": {"subjective": "Patient reports dysuria.", "objective": "T 37.5", "assessment": "UTI", "plan": "Cotrimoxazole"}, "concepts": [], "observations": [], "medications": []}',
                                },
                            }],
                        }
                    }]
                }

        loop = SoapScribeToolLoop(FakeLlm(), StubCiel(), trace_store=self.store)
        result = loop.run("Patient with dysuria, assessed as UTI. Plan: cotrimoxazole.")
        self.assertIn("soap", result)

        runs = self.store.list_runs(limit=5)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].workflow, "scribe")
        self.assertEqual(runs[0].status, "completed")

        events = self.store.list_events(runs[0].run_id)
        self.assertGreater(len(events), 0)
        operations = [e.operation for e in events]
        # Expect context (start), search tool call, middleware result, finalize summary.
        self.assertIn("context", operations)
        self.assertIn("model_tool_call", operations)
        self.assertIn("model_summary", operations)


if __name__ == "__main__":
    unittest.main()
