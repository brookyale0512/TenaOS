from pathlib import Path
import unittest

from cds_service.dak_tb import TbDakExecutor


CDS_ROOT = Path(__file__).resolve().parents[2]
WORKBOOK = CDS_ROOT / "sources" / "smart-dak-tb-downloads" / "TB DAK_decision-support logic.xlsx"


class TbDakExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = TbDakExecutor(WORKBOOK)

    def test_adult_non_plhiv_matches_general_population_row(self) -> None:
        result = self.executor.execute({"age": {"value": 35}, "riskGroup": {"value": "household contact"}})
        self.assertEqual(result.status, "rule_matched")
        self.assertEqual(result.matched_rows[0]["rowId"], "TB.B4.DT.03")

    def test_child_missing_risk_group_stops_before_execution(self) -> None:
        result = self.executor.execute({"age": {"value": 8}})
        self.assertEqual(result.status, "insufficient_data")
        self.assertEqual(result.missing_facts, ["riskGroup"])

    def test_plhiv_adult_matches_plhiv_row(self) -> None:
        result = self.executor.execute({"age": {"value": 20}, "riskGroup": {"value": "PLHIV"}})
        self.assertEqual(result.status, "rule_matched")
        self.assertEqual(result.matched_rows[0]["rowId"], "TB.B4.DT.01")

    def test_not_plhiv_literal_does_not_match_plhiv_row(self) -> None:
        result = self.executor.execute({"age": {"value": 35}, "riskGroup": {"value": "not_PLHIV"}})
        self.assertEqual(result.status, "rule_matched")
        self.assertEqual(result.matched_rows[0]["rowId"], "TB.B4.DT.03")


if __name__ == "__main__":
    unittest.main()
