"""BM25 sparse encoder shared by CIEL and kb_guidelines.

This is lifted out of `CIEL/ciel_search/qdrant_index.py` verbatim with only one
extension: a corpus-stats builder that takes an arbitrary iterable of strings
(so kb_guidelines can build stats from chunk text, not just SQLite rows).
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


@dataclass(slots=True)
class SparseCorpusStats:
    document_count: int
    average_document_length: float
    document_frequencies: dict[str, int]


def build_sparse_corpus_stats_from_texts(texts: Iterable[str]) -> SparseCorpusStats:
    """Build BM25 corpus statistics from any iterable of document strings."""
    document_frequencies: Counter[str] = Counter()
    document_count = 0
    total_length = 0
    for text in texts:
        terms = tokenize(text)
        document_count += 1
        total_length += len(terms)
        document_frequencies.update(set(terms))
    average_document_length = (total_length / document_count) if document_count else 0.0
    return SparseCorpusStats(
        document_count=document_count,
        average_document_length=average_document_length,
        document_frequencies=dict(document_frequencies),
    )


def build_sparse_corpus_stats_from_sqlite(
    sqlite_path: str | Path,
    *,
    table: str = "concept_bundles",
    column: str = "search_text",
) -> SparseCorpusStats:
    """Backwards-compatible helper that mirrors the CIEL SQLite path."""
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            f"SELECT {column} AS text FROM {table} WHERE {column} IS NOT NULL"
        )

        def _iter():
            for row in cursor:
                yield row["text"]

        return build_sparse_corpus_stats_from_texts(_iter())
    finally:
        conn.close()


class BM25SparseEncoder:
    """Classic BM25 sparse encoder producing (indices, values) sparse vectors."""

    def __init__(self, stats: SparseCorpusStats, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.stats = stats
        self.k1 = k1
        self.b = b

    @staticmethod
    def _term_index(term: str) -> int:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=4).digest()
        return int.from_bytes(digest, "big", signed=False)

    def encode_one(self, text: str) -> dict[str, list[float] | list[int]]:
        terms = tokenize(text)
        term_counts = Counter(terms)
        doc_len = len(terms)
        if not term_counts or not self.stats.document_count:
            return {"indices": [], "values": []}

        indices: list[int] = []
        values: list[float] = []
        avgdl = self.stats.average_document_length or 1.0

        for term in sorted(term_counts):
            tf = term_counts[term]
            df = self.stats.document_frequencies.get(term, 0)
            idf = math.log(
                1.0 + ((self.stats.document_count - df + 0.5) / (df + 0.5))
            )
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / avgdl))
            weight = idf * (numerator / denominator)
            indices.append(self._term_index(term))
            values.append(weight)

        return {"indices": indices, "values": values}

    def encode_many(self, texts: list[str]) -> list[dict[str, list[float] | list[int]]]:
        return [self.encode_one(text) for text in texts]
