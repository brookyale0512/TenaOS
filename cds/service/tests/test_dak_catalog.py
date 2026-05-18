import unittest

from cds_service.dak_catalog import catalog_for_model, select_dak_from_context


class DakCatalogTests(unittest.TestCase):
    def test_catalog_includes_available_repositories(self) -> None:
        ids = {entry["dakId"] for entry in catalog_for_model()}
        self.assertIn("smart-dak-tb", ids)
        self.assertIn("smart-dak-pnc", ids)
        self.assertIn("smart-hiv", ids)
        self.assertIn("smart-anc", ids)

    def test_no_generic_tb_default_without_evidence(self) -> None:
        selected = select_dak_from_context({"workflow": "patient-chart-insight", "demographics": {"ageYears": 35}, "clinicalEvidence": {"snippets": []}})
        self.assertIsNone(selected["dakId"])
        self.assertEqual(selected["status"], "no_applicable_dak")

    def test_tb_evidence_selects_tb_dak(self) -> None:
        selected = select_dak_from_context({"clinicalEvidence": {"snippets": ["chronic cough, TB contact, sputum test"]}})
        self.assertEqual(selected["dakId"], "smart-dak-tb")

    def test_pregnancy_evidence_selects_anc(self) -> None:
        selected = select_dak_from_context({"clinicalEvidence": {"snippets": ["pregnant patient attending antenatal care"]}})
        self.assertEqual(selected["dakId"], "smart-anc")


if __name__ == "__main__":
    unittest.main()
