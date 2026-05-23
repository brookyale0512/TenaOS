"""Qdrant-backed fetch stage for the WHO/MSF guidelines retrieval pipeline.

Replaces the Memvid `mem.find(...)` stage used by `retrieval_core_v2.KBRetriever`.
Returns hits in the exact shape `retrieval_core_v2._normalize_hit` would have
produced from an MV2 index, so every downstream reranker stage (corruption
filter, CDS boost, action pipeline, domain coherence, intent-rerank, …) stays
byte-identical.

Search modes mirror the MV2 daemon:
  lex  — sparse BM25 only
  sem  — dense EmbedGemma only
  rrf  — Qdrant-native Reciprocal Rank Fusion over both prefetches
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import sqlite3
import sys
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger("kb_guidelines.qdrant_retriever")

_REPO_ROOT = Path(__file__).resolve().parents[2]  # .../TenaOS
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kb_common.bm25 import (  # noqa: E402
    BM25SparseEncoder,
    SparseCorpusStats,
    build_sparse_corpus_stats_from_texts,
)

try:
    from .embedder import EmbedGemmaEmbedder
except ImportError:  # pragma: no cover
    from embedder import EmbedGemmaEmbedder  # type: ignore[no-redef]

DEFAULT_COLLECTION = os.environ.get("TENAOS_KB_COLLECTION", "who_msf_guidelines")
DEFAULT_DENSE_VECTOR = "embedgemma"
DEFAULT_SPARSE_VECTOR = "bm25"
DEFAULT_QDRANT_URL = os.environ.get("TENAOS_QDRANT_URL", os.environ.get("QDRANT_URL", "http://localhost:6333"))

# How many candidates to fetch per prefetch (dense OR sparse) before RRF fuses them.
# 15 matches K_INTERNAL in retrieval_core_v2; we let the reranker trim to top-5.
DEFAULT_PREFETCH_LIMIT = 60


def _require_qdrant():
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Missing dependency 'qdrant-client'. "
            "Install TenaOS-KnowledgeBase/requirements.txt first."
        ) from exc
    return QdrantClient, models


@dataclass(slots=True)
class QdrantRetrieverConfig:
    url: str = DEFAULT_QDRANT_URL
    api_key: str | None = None
    collection_name: str = DEFAULT_COLLECTION
    dense_vector_name: str = DEFAULT_DENSE_VECTOR
    sparse_vector_name: str = DEFAULT_SPARSE_VECTOR
    prefetch_limit: int = DEFAULT_PREFETCH_LIMIT


class QdrantHybridRetriever:
    """Thin wrapper around Qdrant's hybrid (dense + sparse) query API.

    Produces hits already normalised for `retrieval_core_v2._normalize_hit`.
    Corpus BM25 stats are built lazily from the collection text payload on
    the first call and cached for the lifetime of the process.
    """

    def __init__(self, config: QdrantRetrieverConfig | None = None) -> None:
        self.cfg = config or QdrantRetrieverConfig()
        self._client = None
        self._embedder: EmbedGemmaEmbedder | None = None
        self._sparse_encoder: BM25SparseEncoder | None = None
        self._lock = threading.Lock()

    # ── Lazy initialization ──────────────────────────────────────────────

    def _get_client(self):
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is not None:
                return self._client
            QdrantClient, _ = _require_qdrant()
            self._client = QdrantClient(url=self.cfg.url, api_key=self.cfg.api_key)
            log.info("Qdrant client ready: %s collection=%s",
                     self.cfg.url, self.cfg.collection_name)
        return self._client

    def _get_embedder(self) -> EmbedGemmaEmbedder:
        if self._embedder is None:
            emb = EmbedGemmaEmbedder()
            self._embedder = emb
        return self._embedder

    def _get_sparse_encoder(self) -> BM25SparseEncoder:
        if self._sparse_encoder is not None:
            return self._sparse_encoder
        with self._lock:
            if self._sparse_encoder is not None:
                return self._sparse_encoder
            stats = self._load_or_build_corpus_stats()
            self._sparse_encoder = BM25SparseEncoder(stats)
        return self._sparse_encoder

    def _load_or_build_corpus_stats(self) -> SparseCorpusStats:
        """Scroll the Qdrant collection's text payload to rebuild BM25 stats.

        For ~100k chunks this scrolls in ~2 s at startup. We cache in-process
        and also persist to a JSON blob next to the daemon so subsequent starts
        are instant. Rebuild happens automatically if the stats file is missing
        or stale relative to the collection's point count.
        """
        import json
        cache = Path(os.environ.get(
            "KB_BM25_CACHE",
            str(Path(__file__).resolve().parent / ".bm25_corpus_stats.json"),
        ))
        client = self._get_client()
        info = client.get_collection(self.cfg.collection_name)
        points_count = int(getattr(info, "points_count", 0) or 0)

        if cache.exists():
            try:
                blob = json.loads(cache.read_text())
                if blob.get("points_count") == points_count and points_count > 0:
                    log.info("BM25 stats: loaded cached (%d docs, vocab=%d)",
                             blob["document_count"], len(blob["document_frequencies"]))
                    return SparseCorpusStats(
                        document_count=int(blob["document_count"]),
                        average_document_length=float(blob["average_document_length"]),
                        document_frequencies={
                            k: int(v) for k, v in blob["document_frequencies"].items()
                        },
                    )
            except Exception as exc:
                log.warning("BM25 cache unreadable, rebuilding: %s", exc)

        log.info("Building BM25 corpus stats from Qdrant collection %s ...",
                 self.cfg.collection_name)

        def _iter_texts() -> Iterable[str]:
            offset = None
            while True:
                batch, offset = client.scroll(
                    collection_name=self.cfg.collection_name,
                    scroll_filter=None,
                    limit=2048,
                    with_payload=["headings", "text"],
                    with_vectors=False,
                    offset=offset,
                )
                if not batch:
                    break
                for p in batch:
                    payload = p.payload or {}
                    headings = payload.get("headings") or []
                    text = payload.get("text", "")
                    yield (" > ".join(headings) + "\n" + text) if headings else text
                if offset is None:
                    break

        stats = build_sparse_corpus_stats_from_texts(_iter_texts())
        log.info("BM25 stats built: N=%d, avg_len=%.1f, vocab=%d",
                 stats.document_count, stats.average_document_length,
                 len(stats.document_frequencies))
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({
                "points_count": points_count,
                "document_count": stats.document_count,
                "average_document_length": stats.average_document_length,
                "document_frequencies": stats.document_frequencies,
            }))
        except Exception as exc:
            log.warning("Failed to persist BM25 cache: %s", exc)
        return stats

    # ── Hit normalisation (mirrors retrieval_core_v2._normalize_hit shape) ─

    @staticmethod
    def _build_hit(point, snippet_chars: int) -> dict[str, Any]:
        payload = point.payload or {}
        chunk_id = payload.get("chunk_id") or str(point.id)
        doc_id = payload.get("doc_id", "") or payload.get("pdf_file", "")
        headings = payload.get("headings") or []
        title = payload.get("title") or (headings[-1] if headings else payload.get("doc_title", ""))
        text = payload.get("text", "") or ""
        uri = f"kb-guidelines://{doc_id}#{chunk_id}" if doc_id else f"kb-guidelines://_/{chunk_id}"

        # The v2 reranker reads metadata via _normalize_hit. We emit the same shape
        # as a memvid hit so _extract_hits + _normalize_hit stay byte-identical.
        metadata: dict[str, Any] = {
            "content_type": payload.get("chunk_type") or payload.get("content_type") or "background",
            "retrieval_priority": payload.get("retrieval_priority", 1.0),
            "is_current": payload.get("is_current", True),
            "headings": headings,
            "doc_type": payload.get("doc_type", ""),
            "recommendation_strength": payload.get("recommendation_strength"),
            "evidence_certainty": payload.get("evidence_certainty"),
            "source_url": payload.get("source_url", ""),
            "pdf_file": payload.get("pdf_file", doc_id),
        }
        return {
            "frame_id": chunk_id,
            "title": title,
            "snippet": text[:snippet_chars] if snippet_chars else text,
            "uri": uri,
            "score": float(point.score or 0.0),
            "metadata": metadata,
        }

    # ── Search ────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        k: int = 15,
        mode: str = "rrf",
        snippet_chars: int = 15_000,
    ) -> list[dict[str, Any]]:
        """Fetch top-k hits from Qdrant, already shaped for the v2 reranker."""
        _, models = _require_qdrant()
        client = self._get_client()
        mode = (mode or "rrf").lower()

        dense_vec = None
        sparse_vec = None
        if mode in ("sem", "rrf"):
            dense_vec = self._get_embedder().embed_query(query)
        if mode in ("lex", "rrf"):
            encoded = self._get_sparse_encoder().encode_one(query)
            sparse_vec = models.SparseVector(
                indices=encoded["indices"], values=encoded["values"],
            )

        prefetch_limit = max(self.cfg.prefetch_limit, k)

        if mode == "sem":
            response = client.query_points(
                collection_name=self.cfg.collection_name,
                query=dense_vec,
                using=self.cfg.dense_vector_name,
                limit=k,
                with_payload=True,
                with_vectors=False,
            )
        elif mode == "lex":
            response = client.query_points(
                collection_name=self.cfg.collection_name,
                query=sparse_vec,
                using=self.cfg.sparse_vector_name,
                limit=k,
                with_payload=True,
                with_vectors=False,
            )
        else:  # rrf — native Qdrant fusion over both prefetches
            response = client.query_points(
                collection_name=self.cfg.collection_name,
                prefetch=[
                    models.Prefetch(
                        query=dense_vec,
                        using=self.cfg.dense_vector_name,
                        limit=prefetch_limit,
                    ),
                    models.Prefetch(
                        query=sparse_vec,
                        using=self.cfg.sparse_vector_name,
                        limit=prefetch_limit,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=k,
                with_payload=True,
                with_vectors=False,
            )

        hits = [self._build_hit(p, snippet_chars) for p in response.points]
        return hits

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        client = self._get_client()
        info = client.get_collection(self.cfg.collection_name)
        out: dict[str, Any] = {
            "collection": self.cfg.collection_name,
            "points_count": getattr(info, "points_count", None),
            "vectors_count": getattr(info, "vectors_count", None),
            "status": str(getattr(info, "status", "")),
        }
        return out
