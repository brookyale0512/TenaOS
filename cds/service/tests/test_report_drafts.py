"""Unit tests for ReportDraftStore."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cds_service.report_drafts import (  # noqa: E402
    ReportDraftNotFoundError,
    ReportDraftStore,
)


class ReportDraftStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp(prefix="report-drafts-tests-")
        self.db_path = Path(self.tempdir) / "report_drafts.sqlite3"
        self.store = ReportDraftStore(self.db_path)

    def tearDown(self) -> None:
        if self.db_path.exists():
            os.remove(self.db_path)
        os.rmdir(self.tempdir)

    def test_create_and_get_round_trip(self) -> None:
        draft = self.store.create_draft(name="TB count", owner="alice", report_type="count")
        self.assertEqual(draft.status, "draft")
        self.assertEqual(draft.report_type, "count")
        self.assertEqual(draft.conversation_state, "awaiting_name")
        again = self.store.get_draft(draft.draft_id)
        self.assertEqual(again.name, "TB count")
        # System create_report_draft event was logged.
        events = self.store.list_events(draft.draft_id)
        self.assertTrue(any(e.operation == "create_report_draft" for e in events))

    def test_update_persists_fields(self) -> None:
        draft = self.store.create_draft(name="X", owner=None)
        updated = self.store.update_draft(
            draft.draft_id,
            name="Y",
            report_type="cohort",
            spec={"reportType": "cohort", "filters": [{"filterId": "f1", "conceptId": "1487", "label": "Cough"}]},
            conversation_state="awaiting_question",
        )
        self.assertEqual(updated.name, "Y")
        self.assertEqual(updated.report_type, "cohort")
        self.assertEqual(updated.spec["reportType"], "cohort")
        self.assertEqual(updated.conversation_state, "awaiting_question")
        re_read = self.store.get_draft(draft.draft_id)
        self.assertEqual(re_read.spec["filters"][0]["conceptId"], "1487")

    def test_missing_draft_raises(self) -> None:
        with self.assertRaises(ReportDraftNotFoundError):
            self.store.get_draft("nope")

    def test_event_log_in_order(self) -> None:
        draft = self.store.create_draft(name="X")
        self.store.append_event(draft.draft_id, actor="gemma", operation="op1", detail="first")
        self.store.append_event(draft.draft_id, actor="gemma", operation="op2", detail="second")
        events = self.store.list_events(draft.draft_id)
        ops = [e.operation for e in events]
        self.assertEqual(ops[-2:], ["op1", "op2"])


if __name__ == "__main__":
    unittest.main()
