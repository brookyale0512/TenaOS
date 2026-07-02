"""Tests for the robust report-generation pipeline helpers."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service.ciel import SeedHit  # noqa: E402
from tena_agent_service.report_builder_tool_loop import ReportBuilderToolLoop  # noqa: E402
from tena_agent_service.report_drafts import ReportDraftStore  # noqa: E402
from tena_agent_service.report_pipeline.repair import run_report_repair  # noqa: E402
from tena_agent_service.report_pipeline.worklist import PlannedFilter, ReportWorklist, sanitize_worklist  # noqa: E402

from tests.test_report_builder import FakeCielClient, FakeReader, _bundle  # noqa: E402


def _seed(concept_id: str, display: str, *, datatype: str = "Boolean") -> dict:
    return SeedHit(
        concept_id=concept_id,
        display_name=display,
        concept_class="Finding",
        datatype=datatype,
        retired=False,
        answer_count=0,
        set_member_count=0,
        score=1.0,
        rationale=[],
    ).to_dict()


class WorklistTests(unittest.TestCase):
    def test_sanitize_keeps_groupings_out_of_filters(self) -> None:
        worklist = sanitize_worklist(
            {
                "reportType": "pivot",
                "filters": [
                    {"label": "Cough", "searchPhrases": ["cough"]},
                    {"label": "sex"},
                ],
                "groupBy": [{"dimension": "sex"}, {"dimension": "age_group"}],
            },
            request="cough by sex and age group",
        )
        self.assertEqual(worklist.report_type, "pivot")
        self.assertEqual([f.label for f in worklist.filters], ["Cough"])
        self.assertEqual([g.dimension for g in worklist.group_by], ["sex", "age_group"])

    def test_month_over_month_line_graph_with_typo_is_temporal_pivot(self) -> None:
        worklist = sanitize_worklist(
            {
                "reportType": "count",
                "dateRange": "papst 12 months",
                "filters": [
                    {"label": "Malaria diagnosis", "searchPhrases": ["malaria diagnosis"]},
                    {"label": "Papst", "searchPhrases": ["papst"]},
                ],
                "groupBy": [{"dimension": "date_month"}],
                "visualization": "line graph",
            },
            request="malaria diagnosis month over month using line graph for the papst 12 months",
        )
        self.assertEqual(worklist.report_type, "pivot")
        self.assertEqual(worklist.date_range, "last 12 months")
        self.assertEqual(worklist.visualization, "time_series_line")
        self.assertEqual([f.label for f in worklist.filters], ["Malaria diagnosis"])
        self.assertEqual([g.dimension for g in worklist.group_by], ["date_month"])

    def test_grouped_request_forces_pivot_even_if_model_says_count(self) -> None:
        worklist = sanitize_worklist(
            {
                "reportType": "count",
                "filters": [{"label": "Malaria", "searchPhrases": ["malaria diagnosis"]}],
                "groupBy": [{"dimension": "sex"}, {"dimension": "age_group"}],
            },
            request="Show patients diagnosed with malaria by sex and age group in the past 12 months",
        )
        self.assertEqual(worklist.report_type, "pivot")
        self.assertEqual([g.dimension for g in worklist.group_by], ["sex", "age_group"])


class RepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="report-pipeline-")
        self.db_path = Path(self.tempdir) / "reports.sqlite3"
        self.store = ReportDraftStore(self.db_path)
        self.ciel = FakeCielClient({"1487": _bundle("1487", "Cough", datatype="Boolean")})
        self.loop = ReportBuilderToolLoop(
            store=self.store,
            ciel=self.ciel,  # type: ignore[arg-type]
            reader_factory=lambda progress=None: FakeReader(),
        )

    def tearDown(self) -> None:
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def test_repair_commits_reviewed_candidate_when_no_filters(self) -> None:
        draft = self.store.create_draft(name="Cough count", report_type="count")
        self.store.append_event(
            draft.draft_id,
            actor="middleware",
            operation="tool_result",
            detail="Tool result: search_ciel_seeds",
            payload={"toolName": "search_ciel_seeds", "result": {"seeds": [_seed("1487", "Cough")]}},
        )
        build_result = {
            "compiled": None,
            "validation": {"issues": [{"severity": "error", "path": "filters", "code": "missing_filter", "message": "missing"}]},
        }
        worklist = ReportWorklist(filters=[PlannedFilter(label="Cough", search_phrases=["cough"])])
        result = run_report_repair(
            store=self.store,
            loop=self.loop,
            draft=self.store.get_draft(draft.draft_id),
            worklist=worklist,
            build_result=build_result,
        )
        self.assertTrue(result["applied"])
        repaired = self.store.get_draft(draft.draft_id)
        self.assertEqual(repaired.spec["filters"][0]["conceptId"], "1487")


if __name__ == "__main__":
    unittest.main()
