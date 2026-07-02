"""Tests for the semantic CIEL wiring (kb-ciel) and the SQLite FTS5 fallback.

Covers:
  * ``KbCielClient`` HTTP envelope parsing + filter forwarding.
  * ``CielClient._maybe_build_qdrant_search`` adapter -> (concept_id, score),
    including graceful empty-on-error behaviour.
  * The semantic-first / SQLite-fallback path in ``search_form_seeds`` that the
    report builder also depends on (regression guard).
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service import tool_loop  # noqa: E402
from tena_agent_service.ciel import CielClient  # noqa: E402
from tena_agent_service.config import Settings  # noqa: E402


class _FakeHttpResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


class KbCielClientTests(unittest.TestCase):
    def test_search_parses_hits_and_forwards_filters(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):  # noqa: ANN001
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeHttpResponse(
                {"hits": [{"concept_id": "5089", "score": 0.91, "display_name": "Weight"}]}
            )

        original = tool_loop.urllib.request.urlopen
        tool_loop.urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            client = tool_loop.KbCielClient(base_url="http://kb-ciel.local")
            hits = client.search("weight", k=5, concept_classes=["Finding"], datatypes=["Numeric"])
        finally:
            tool_loop.urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(hits[0]["concept_id"], "5089")
        self.assertEqual(captured["body"]["query"], "weight")
        self.assertEqual(captured["body"]["concept_classes"], ["Finding"])
        self.assertEqual(captured["body"]["datatypes"], ["Numeric"])

    def test_search_raises_on_transport_error(self) -> None:
        def fake_urlopen(req, timeout):  # noqa: ANN001
            raise OSError("connection refused")

        original = tool_loop.urllib.request.urlopen
        tool_loop.urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            client = tool_loop.KbCielClient(base_url="http://kb-ciel.local")
            with self.assertRaises(OSError):
                client.search("weight")
        finally:
            tool_loop.urllib.request.urlopen = original  # type: ignore[assignment]


class _FakeKbCielClient:
    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url

    def search(self, query, k=10, *, concept_classes=None, datatypes=None, include_retired=False):  # noqa: ANN001
        return [{"concept_id": "5089", "score": 0.9}, {"concept_id": "", "score": 0.1}]


class _RaisingKbCielClient:
    def __init__(self, *, base_url: str) -> None:
        pass

    def search(self, *args, **kwargs):  # noqa: ANN001
        raise OSError("kb-ciel down")


class AdapterTests(unittest.TestCase):
    def _filters(self):
        return types.SimpleNamespace(concept_classes=None, datatypes=None, include_retired=False)

    def test_adapter_converts_hits_to_pairs(self) -> None:
        client = CielClient(Settings.from_env())
        original = tool_loop.KbCielClient
        tool_loop.KbCielClient = _FakeKbCielClient  # type: ignore[assignment]
        try:
            adapter = client._maybe_build_qdrant_search()
            self.assertIsNotNone(adapter)
            result = adapter("weight", self._filters(), 5)
        finally:
            tool_loop.KbCielClient = original  # type: ignore[assignment]
        # Blank concept_id dropped; valid one converted to (id, score).
        self.assertEqual(result, [("5089", 0.9)])

    def test_adapter_returns_empty_on_error(self) -> None:
        client = CielClient(Settings.from_env())
        original = tool_loop.KbCielClient
        tool_loop.KbCielClient = _RaisingKbCielClient  # type: ignore[assignment]
        try:
            adapter = client._maybe_build_qdrant_search()
            result = adapter("weight", self._filters(), 5)
        finally:
            tool_loop.KbCielClient = original  # type: ignore[assignment]
        self.assertEqual(result, [])

    def test_adapter_disabled_when_flag_off(self) -> None:
        from dataclasses import replace

        client = CielClient(replace(Settings.from_env(), ciel_semantic_search=False))
        self.assertIsNone(client._maybe_build_qdrant_search())


# ---------------------------------------------------------------------------
# Fallback path used by both the form builder and the report builder.


class _Hit:
    def __init__(self, concept_id: str) -> None:
        self.concept_id = concept_id
        self.display_name = f"concept-{concept_id}"
        self.concept_class = "Finding"
        self.datatype = "Numeric"
        self.retired = False
        self.answer_count = 0
        self.set_member_count = 0
        self.score = 1.0


class _Recommendation:
    def __init__(self, concept_id: str) -> None:
        self.hit = _Hit(concept_id)
        self.rationale = ["sqlite-fallback"]


class _Result:
    def __init__(self, recommended_seeds):  # noqa: ANN001
        self.recommended_seeds = recommended_seeds


class _Service:
    def __init__(self, seeds):  # noqa: ANN001
        self._seeds = seeds
        self.calls = 0

    def search_form_seeds(self, *args, **kwargs):  # noqa: ANN001
        self.calls += 1
        return _Result(self._seeds)


class SemanticFallbackTests(unittest.TestCase):
    def _client(self) -> CielClient:
        client = CielClient(Settings.from_env())
        # Bypass the real ciel_search import for ConceptSearchFilters.
        client._import = lambda pkg, name: (lambda **kw: types.SimpleNamespace(**kw))  # type: ignore[assignment]
        return client

    def test_falls_back_to_sqlite_when_semantic_empty(self) -> None:
        client = self._client()
        semantic = _Service([])  # kb-ciel returned nothing
        sqlite = _Service([_Recommendation("5089")])
        client._service = semantic
        client._sqlite_service = sqlite
        seeds = client.search_form_seeds("weight")
        self.assertEqual([s.concept_id for s in seeds], ["5089"])
        self.assertEqual(sqlite.calls, 1, "SQLite fallback must run when semantic is empty")

    def test_uses_semantic_result_when_present(self) -> None:
        client = self._client()
        semantic = _Service([_Recommendation("5085")])
        sqlite = _Service([_Recommendation("5089")])
        client._service = semantic
        client._sqlite_service = sqlite
        seeds = client.search_form_seeds("systolic")
        self.assertEqual([s.concept_id for s in seeds], ["5085"])
        self.assertEqual(sqlite.calls, 0, "SQLite fallback must NOT run when semantic returns seeds")


if __name__ == "__main__":
    unittest.main()
