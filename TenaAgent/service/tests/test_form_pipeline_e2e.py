"""End-to-end smoke test for the v2 grounded pipeline + form-quality scorer.

Drives a request through the real ``FormConversationDriver`` with a scripted
(mocked) LLM and a CIEL client, then scores the committed draft with the eval
harness scorer (``evals.form_quality``). When the real CIEL SQLite store is
available it is used; otherwise a deterministic in-memory fake stands in so the
pipeline + scorer wiring is always exercised in CI.
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

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVICE_ROOT))
sys.path.insert(0, str(_SERVICE_ROOT.parent))  # repo: TenaAgent/ (for `evals`)

from tena_agent_service.ciel import ConceptNotFoundError, SeedHit  # noqa: E402
from tena_agent_service.config import Settings  # noqa: E402
from tena_agent_service.form_builder_tool_loop import FormBuilderToolLoop  # noqa: E402
from tena_agent_service.form_conversation import ConversationTurn, FormConversationDriver  # noqa: E402
from tena_agent_service.form_drafts import FormDraftStore  # noqa: E402
from tena_agent_service.form_pipeline import research_phase  # noqa: E402

from evals.form_quality import FormQualitySpec, quality_gate, score_form  # noqa: E402


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
    def __init__(self, scripted: list[Any]) -> None:
        self.scripted = list(scripted)

    def health(self) -> Any:
        class _S:
            healthy = True

        return _S()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
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


def _seed(concept_id: str, display: str, *, datatype: str = "Numeric", concept_class: str = "Finding") -> SeedHit:
    return SeedHit(
        concept_id=concept_id,
        display_name=display,
        concept_class=concept_class,
        datatype=datatype,
        retired=False,
        answer_count=0,
        set_member_count=0,
        score=1.0,
        rationale=[],
    )


def _finalize_worklist_msg(questions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [
            {
                "id": "call_finalize",
                "type": "function",
                "function": {"name": "finalize_worklist", "arguments": json.dumps({"summary": "s", "questions": questions})},
            }
        ],
    }


def _tool_msg(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }


class ScorerUnitTests(unittest.TestCase):
    def test_coverage_and_codes(self) -> None:
        spec = FormQualitySpec(
            id="t",
            request="vitals",
            min_questions=1,
            max_questions=5,
            require_any_of=[
                {"label": "Weight", "conceptIds": ["5089"]},
                {"label": "Height", "conceptIds": ["5090"]},
            ],
        )
        draft = {
            "basket": {"sections": [{"fields": [{"conceptId": "5089"}, {"conceptId": "5090"}]}]},
            "last_schema": {"pages": [{"sections": [{"questions": [{"id": "q"}]}]}]},
            "last_validation": {"issues": []},
        }
        ciel = FakeCielClient({"5089": _bundle("5089", "Weight"), "5090": _bundle("5090", "Height")}, {})
        score = score_form(spec, draft, ciel)
        self.assertEqual(score["coverage"], 1.0)
        self.assertTrue(score["schemaValid"])
        self.assertEqual(score["hallucinatedCodes"], [])
        self.assertEqual(score["retiredCodes"], [])
        gate = quality_gate([score])
        self.assertTrue(gate["pass"], gate["reasons"])

    def test_hallucinated_code_fails_gate(self) -> None:
        spec = FormQualitySpec(id="t", request="x", require_any_of=[{"label": "W", "conceptIds": ["5089"]}])
        draft = {
            "basket": {"sections": [{"fields": [{"conceptId": "9999999"}]}]},
            "last_schema": {"pages": []},
            "last_validation": {"issues": []},
        }
        ciel = FakeCielClient({"5089": _bundle("5089", "Weight")}, {})
        score = score_form(spec, draft, ciel)
        self.assertIn("9999999", score["hallucinatedCodes"])
        self.assertFalse(quality_gate([score])["pass"])


class PipelineE2ESmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="form-e2e-")
        self.db_path = Path(self.tempdir) / "drafts.sqlite3"
        self.store = FormDraftStore(self.db_path)
        self.bundles = {"5089": _bundle("5089", "Weight (kg)"), "5090": _bundle("5090", "Height (cm)")}
        self.search_hits = {"weight": [_seed("5089", "Weight (kg)")], "height": [_seed("5090", "Height (cm)")], "*": []}
        self.ciel = FakeCielClient(self.bundles, self.search_hits)
        self.loop = FormBuilderToolLoop(
            store=self.store, ciel=self.ciel, llm=None, writer_factory=lambda: FakeWriter()  # type: ignore[arg-type]
        )
        self.settings = replace(
            Settings.from_env(), form_agent_pipeline_v2=True, form_agent_target_min_fields=2
        )
        self._orig_kb = research_phase._make_kb_client
        research_phase._make_kb_client = lambda settings: None  # type: ignore[assignment]

    def tearDown(self) -> None:
        research_phase._make_kb_client = self._orig_kb  # type: ignore[assignment]
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def test_pipeline_resolves_and_scores_clean(self) -> None:
        draft = self.store.create_draft(
            name="Vitals", owner="eval", description=None, encounter_type_uuid="enc-consult-uuid"
        )
        self.store.update_draft(draft.draft_id, conversation_state="awaiting_question")
        llm = FakeHealthyLlm([
            _finalize_worklist_msg([
                {"label": "Weight", "datatypeHint": "Numeric", "searchPhrases": ["weight"], "priority": 1},
                {"label": "Height", "datatypeHint": "Numeric", "searchPhrases": ["height"], "priority": 2},
            ]),
            _tool_msg(
                "update_form_draft",
                {"operations": [
                    {"op": "add_section", "sectionId": "vitals", "label": "Vitals"},
                    {"op": "add_field", "sectionId": "vitals", "conceptId": "5089"},
                    {"op": "add_field", "sectionId": "vitals", "conceptId": "5090"},
                ]},
                "u1",
            ),
            _tool_msg("build_form_schema", {}, "b1"),
            "Built the vitals form.",
        ])
        driver = FormConversationDriver(
            store=self.store, ciel=self.ciel, loop=self.loop, llm=llm, settings=self.settings  # type: ignore[arg-type]
        )
        driver.handle_user_turn(draft.draft_id, ConversationTurn(kind="message", message="build a vitals form"))

        final = self.store.get_draft(draft.draft_id)
        spec = FormQualitySpec(
            id="vitals",
            request="build a vitals form",
            min_questions=2,
            max_questions=8,
            require_any_of=[
                {"label": "Weight", "conceptIds": ["5089"]},
                {"label": "Height", "conceptIds": ["5090"]},
            ],
        )
        score = score_form(spec, final, self.ciel)
        self.assertEqual(score["coverage"], 1.0)
        self.assertTrue(score["schemaValid"])
        self.assertEqual(score["hallucinatedCodes"], [])
        self.assertTrue(quality_gate([score])["pass"], quality_gate([score])["reasons"])

    def test_pipeline_against_real_ciel_sqlite_if_available(self) -> None:
        """When the real CIEL SQLite store is present, resolve a common concept."""
        from tena_agent_service.ciel import CielClient

        settings = Settings.from_env()
        sqlite_path = Path(settings.ciel_sqlite_path)
        if not sqlite_path.exists():
            self.skipTest("CIEL SQLite store not available in this environment")
        try:
            real_ciel = CielClient(settings)
            hits = real_ciel.search_form_seeds("weight", limit=5)
        except Exception as exc:  # CIEL package or store misconfigured
            self.skipTest(f"CIEL unavailable: {exc}")
        self.assertTrue(hits, "Expected the real CIEL store to return seeds for 'weight'")


if __name__ == "__main__":
    unittest.main()
