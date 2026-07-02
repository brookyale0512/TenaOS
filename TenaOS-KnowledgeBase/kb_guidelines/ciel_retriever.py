"""CIEL concept retriever for the kb-ciel daemon mode.

The kb daemon serves two very different collections through the same HTTP
surface:

  * ``who_msf_guidelines`` -> :class:`retrieval_core_v2.KBRetriever`
    (EmbedGemma dense + BM25 with the WHO/MSF clinical reranking pipeline).
  * ``ciel_concepts``      -> :class:`CielConceptRetriever` (this module).

The ``ciel_concepts`` Qdrant collection was indexed with SapBERT dense vectors
(``cambridgeltl/SapBERT-from-PubMedBERT-fulltext``) plus a BM25 sparse vector,
so it cannot be queried with the EmbedGemma retriever. Rather than reimplement
hybrid search, this retriever reuses the proven
``ciel_search.qdrant_index.QdrantHybridSearcher`` (SapBERT + BM25 RRF) and the
``ciel_search.CielSearchService`` SQLite hydration so each hit carries the
metadata the form/report builders need (display name, class, datatype, answer
and set-member counts).

Concept discovery only. Exact bundle/code resolution stays in the caller's
local CIEL SQLite (``CielClient`` / ``CielSearchService._hydrate_hits``); this
service simply answers "which concept ids best match this plain-language
query?".
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("kb_guidelines.ciel_retriever")

DEFAULT_SAPBERT_MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"


def _resolve_sapbert_model() -> str:
    """Prefer a locally mounted SapBERT snapshot, else the HuggingFace id."""
    for env_var in ("TENAOS_SAPBERT_PATH", "TENAOS_SAPBERT_MODEL", "SAPBERT_PATH"):
        value = (os.environ.get(env_var) or "").strip()
        if value:
            return value
    return DEFAULT_SAPBERT_MODEL


def _resolve_sqlite_path() -> str:
    for env_var in ("TENAOS_CIEL_SQLITE", "TENAOS_CIEL_SQLITE_PATH"):
        value = (os.environ.get(env_var) or "").strip()
        if value:
            return value
    # Fall back to the conventional bind-mount location inside the image.
    return "/opt/tenaos/ciel/ciel_search.sqlite3"


class CielConceptRetriever:
    """Hybrid SapBERT + BM25 search over the ``ciel_concepts`` collection."""

    def __init__(
        self,
        *,
        collection: str,
        qdrant_url: str | None = None,
        sqlite_path: str | None = None,
        sapbert_model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.collection = collection
        self.qdrant_url = qdrant_url or os.environ.get(
            "TENAOS_QDRANT_URL", os.environ.get("QDRANT_URL", "http://localhost:6333")
        )
        self.sqlite_path = sqlite_path or _resolve_sqlite_path()
        self.sapbert_model = sapbert_model or _resolve_sapbert_model()
        self.api_key = api_key or (os.environ.get("QDRANT_API_KEY") or None)
        self._service: Any | None = None
        self._searcher: Any | None = None

    # ------------------------------------------------------------------ init
    def initialize(self, enable_vec: bool = True) -> None:
        """Eagerly construct the hybrid searcher + SQLite-hydrating service.

        Raises a clear error if the CIEL package or SQLite store is missing so
        supervisord surfaces the misconfiguration at boot rather than on the
        first query.
        """
        if not Path(self.sqlite_path).exists():
            raise RuntimeError(
                f"CIEL SQLite store not found at {self.sqlite_path}; the kb-ciel "
                "daemon needs it for BM25 corpus stats and hit hydration. Set "
                "TENAOS_CIEL_SQLITE."
            )
        try:
            from ciel_search import CielSearchService, ConceptSearchFilters  # noqa: F401
            from ciel_search.qdrant_index import QdrantHybridSearcher
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "The ciel_search package is required for kb-ciel mode but could "
                f"not be imported: {exc}. Ensure TenaOS-CIEL is on PYTHONPATH."
            ) from exc

        searcher = QdrantHybridSearcher(
            url=self.qdrant_url,
            collection_name=self.collection,
            api_key=self.api_key,
            dense_model_name=self.sapbert_model,
            sqlite_path=self.sqlite_path,
        )
        self._searcher = searcher
        self._service = CielSearchService(self.sqlite_path, qdrant_search=searcher.search)
        log.info(
            "CielConceptRetriever ready: collection=%s qdrant=%s model=%s",
            self.collection, self.qdrant_url, self.sapbert_model,
        )

    def _ensure_service(self) -> Any:
        if self._service is None:
            self.initialize()
        return self._service

    # ---------------------------------------------------------------- search
    def search(
        self,
        query: str,
        *,
        k: int = 8,
        concept_classes: list[str] | None = None,
        datatypes: list[str] | None = None,
        include_retired: bool = False,
        **_ignored: Any,
    ) -> dict[str, Any]:
        """Return a guideline-daemon-shaped envelope with concept hits."""
        from ciel_search import ConceptSearchFilters

        started = time.monotonic()
        service = self._ensure_service()
        filters = ConceptSearchFilters(
            concept_classes=concept_classes or None,
            datatypes=datatypes or None,
            include_retired=bool(include_retired),
        )
        hits = service.search_concepts(query, filters, limit=max(1, min(int(k), 50)))
        serialized = [_serialize_hit(hit) for hit in hits]
        return {
            "query": query,
            "hit": serialized[0] if serialized else None,
            "hits": serialized,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "backend": "ciel-sapbert",
        }

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict[str, Any]:
        info: dict[str, Any] = {"collection": self.collection, "backend": "ciel-sapbert"}
        try:
            searcher = self._searcher
            if searcher is not None and getattr(searcher, "client", None) is not None:
                count = searcher.client.count(collection_name=self.collection, exact=False)
                info["points"] = int(getattr(count, "count", 0) or 0)
        except Exception as exc:  # pragma: no cover - best effort
            info["points_error"] = str(exc)
        return info


def _serialize_hit(hit: Any) -> dict[str, Any]:
    return {
        "concept_id": str(getattr(hit, "concept_id", "")),
        "score": float(getattr(hit, "score", 0.0) or 0.0),
        "display_name": getattr(hit, "display_name", "") or "",
        "concept_class": getattr(hit, "concept_class", None),
        "datatype": getattr(hit, "datatype", None),
        "retired": bool(getattr(hit, "retired", False)),
        "answer_count": int(getattr(hit, "answer_count", 0) or 0),
        "set_member_count": int(getattr(hit, "set_member_count", 0) or 0),
    }


__all__ = ["CielConceptRetriever"]
