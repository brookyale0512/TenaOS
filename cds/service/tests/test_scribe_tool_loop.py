from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cds_service.scribe_tool_loop import (  # noqa: E402
    resolve_concept_item,
    resolve_scribe_result,
)


def _bundle(
    concept_id: str,
    display_name: str,
    *,
    concept_class: str,
    datatype: str = "N/A",
) -> dict[str, Any]:
    return {
        "concept": {
            "concept_id": concept_id,
            "display_name": display_name,
            "concept_class": concept_class,
            "datatype": datatype,
            "retired": False,
        },
        "answers": [],
        "set_members": [],
    }


class FakeCielClient:
    def __init__(self) -> None:
        self.bundles = {
            "117399": _bundle("117399", "Urinary tract infection", concept_class="Diagnosis"),
            "5088": _bundle("5088", "Temperature (C)", concept_class="Question", datatype="Numeric"),
            "1231": _bundle("1231", "Trimethoprim + Sulfamethoxazole", concept_class="Drug"),
            "999001": _bundle(
                "999001",
                "4-hydroxyphenylpyruvate dioxygenase deficiency",
                concept_class="Diagnosis",
            ),
        }

    def get_concept_bundle(self, concept_id: str) -> dict[str, Any]:
        if concept_id not in self.bundles:
            raise KeyError(concept_id)
        return self.bundles[concept_id]

    def expand_seed(self, concept_id: str, *, depth: int = 3, allow_retired: bool = False) -> dict[str, Any]:
        return self.get_concept_bundle(concept_id)

    def search_concepts(
        self,
        query: str,
        *,
        concept_classes: list[str] | None = None,
        datatypes: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        q = query.lower()
        if "urinary tract infection" in q or q == "uti":
            return [_hit("117399", "Urinary tract infection", "Diagnosis")]
        if "trimethoprim" in q or "cotrimoxazole" in q or "co-trimoxazole" in q:
            return [_hit("1231", "Trimethoprim + Sulfamethoxazole", "Drug")]
        if "burning urination" in q:
            return [_hit("999001", "4-hydroxyphenylpyruvate dioxygenase deficiency", "Diagnosis")]
        return []


def _hit(concept_id: str, display_name: str, concept_class: str, datatype: str = "N/A") -> dict[str, Any]:
    return {
        "conceptId": concept_id,
        "displayName": display_name,
        "conceptClass": concept_class,
        "datatype": datatype,
        "retired": False,
        "score": 0.99,
    }


class ScribeResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ciel = FakeCielClient()

    def test_uti_assessment_is_saved_as_diagnosis_not_subjective_symptoms(self) -> None:
        result = resolve_scribe_result(
            {
                "soap": {
                    "subjective": "Burning urination for 4 days, frequent urge to urinate.",
                    "objective": "Temperature 38.1. Urine is cloudy.",
                    "assessment": "UTI",
                    "plan": "Start cotrimoxazole.",
                },
                "concepts": [
                    {"label": "Burning urination", "ciel_hint": "burning urination"},
                    {"label": "Frequent urge to urinate", "ciel_hint": "urge to urinate"},
                ],
                "observations": [
                    {"label": "Temperature", "ciel_hint": "temperature", "value": "38.1", "unit": "C"},
                ],
                "medications": [
                    {"label": "cotrimoxazole", "ciel_hint": "cotrimoxazole"},
                ],
            },
            self.ciel,
        )

        self.assertEqual(len(result["concepts"]), 1)
        self.assertEqual(result["concepts"][0]["uuid"], "117399")
        self.assertEqual(result["concepts"][0]["display"], "Urinary tract infection")
        self.assertEqual(result["observations"][0]["uuid"], "5088")
        self.assertEqual(result["medications"][0]["uuid"], "1231")
        self.assertEqual(result["medications"][0]["display"], "Trimethoprim + Sulfamethoxazole")

    def test_unrelated_top_hit_is_rejected(self) -> None:
        resolved = resolve_concept_item(
            {"label": "Burning urination", "ciel_hint": "burning urination"},
            self.ciel,
            kind="diagnosis",
        )

        self.assertIsNone(resolved["uuid"])
        self.assertEqual(resolved["resolutionStatus"], "unresolved")
        self.assertEqual(resolved["display"], "Burning urination")

    def test_cotrimoxazole_synonym_resolves_to_combo_drug(self) -> None:
        resolved = resolve_concept_item(
            {"label": "cotrimoxazole", "ciel_hint": "cotrimoxazole"},
            self.ciel,
            kind="medication",
        )

        self.assertEqual(resolved["uuid"], "1231")
        self.assertEqual(resolved["display"], "Trimethoprim + Sulfamethoxazole")


if __name__ == "__main__":
    unittest.main()
