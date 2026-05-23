from __future__ import annotations

import hashlib
import importlib.machinery
import json
import math
import re
import sqlite3
import sys
import types
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .models import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_DENSE_VECTOR_NAME,
    DEFAULT_SAPBERT_MODEL,
    DEFAULT_SPARSE_VECTOR_NAME,
    ConceptSearchFilters,
)

# Shared BM25 implementation (single source of truth across CIEL and kb_guidelines).
# We append the repo root to sys.path so the top-level `kb_common` package is importable
# when CIEL is invoked as its own module (which it is, from CIEL/).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from kb_common.bm25 import (  # noqa: E402
    BM25SparseEncoder,
    SparseCorpusStats,
    tokenize,
)
from kb_common.bm25 import TOKEN_RE  # noqa: E402 — re-export for any external callers


def _require_qdrant():
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models
    except ImportError as exc:  # pragma: no cover - import guard
        raise SystemExit("Missing dependency 'qdrant-client'. Install CIEL/requirements.txt first.") from exc
    return QdrantClient, models


def _qdrant_point_id(raw_id: str) -> int | str:
    if raw_id.isdigit():
        return int(raw_id)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ciel-concept:{raw_id}"))


def _install_torchvision_stub() -> None:
    if "torchvision" in sys.modules and "torchvision.transforms" in sys.modules:
        return

    torchvision_module = types.ModuleType("torchvision")
    transforms_module = types.ModuleType("torchvision.transforms")
    io_module = types.ModuleType("torchvision.io")
    torchvision_module.__spec__ = importlib.machinery.ModuleSpec("torchvision", loader=None)
    transforms_module.__spec__ = importlib.machinery.ModuleSpec("torchvision.transforms", loader=None)
    io_module.__spec__ = importlib.machinery.ModuleSpec("torchvision.io", loader=None)

    class InterpolationMode:
        NEAREST = "nearest"
        NEAREST_EXACT = "nearest_exact"
        BILINEAR = "bilinear"
        BICUBIC = "bicubic"
        BOX = "box"
        HAMMING = "hamming"
        LANCZOS = "lanczos"

    transforms_module.InterpolationMode = InterpolationMode
    torchvision_module.transforms = transforms_module
    torchvision_module.io = io_module
    sys.modules["torchvision"] = torchvision_module
    sys.modules["torchvision.transforms"] = transforms_module
    sys.modules["torchvision.io"] = io_module


class SapBERTEmbedder:
    """Dense encoder backed by a SapBERT transformer with mean pooling."""

    def __init__(
        self,
        model_name: str = DEFAULT_SAPBERT_MODEL,
        *,
        max_seq_length: int = 256,
        device: str | None = None,
    ) -> None:
        try:
            _install_torchvision_stub()
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SystemExit(
                "SapBERT embedding requires 'transformers' and 'torch'."
            ) from exc

        self._torch = torch
        self._device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModel.from_pretrained(model_name)
        self._model.to(self._device)
        self._model.eval()
        self._max_seq_length = max_seq_length
        self.dimension = int(getattr(self._model.config, "hidden_size"))

    def encode(self, texts: list[str]) -> list[list[float]]:
        with self._torch.no_grad():
            encoded = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=self._max_seq_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(self._device) for key, value in encoded.items()}
            model_output = self._model(**encoded)
            token_embeddings = model_output.last_hidden_state
            attention_mask = encoded["attention_mask"].unsqueeze(-1).expand(token_embeddings.size()).float()
            pooled = (token_embeddings * attention_mask).sum(dim=1) / attention_mask.sum(dim=1).clamp(min=1e-9)
            normalized = self._torch.nn.functional.normalize(pooled, p=2, dim=1)
            return normalized.cpu().tolist()


def build_sparse_corpus_stats(sqlite_path: str | Path) -> SparseCorpusStats:
    """CIEL-specific helper: build BM25 stats from the concept_bundles SQLite store.

    Thin wrapper around :func:`kb_common.bm25.build_sparse_corpus_stats_from_texts`
    so existing CIEL callers continue to use the CIEL-flavored signature.
    """
    from kb_common.bm25 import build_sparse_corpus_stats_from_sqlite

    return build_sparse_corpus_stats_from_sqlite(
        sqlite_path, table="concept_bundles", column="search_text"
    )


def qdrant_collection_config(
    dense_vector_size: int,
    *,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    sparse_vector_name: str = DEFAULT_SPARSE_VECTOR_NAME,
    dense_vector_name: str = DEFAULT_DENSE_VECTOR_NAME,
) -> dict:
    _, models = _require_qdrant()
    return {
        "collection_name": collection_name,
        "vectors_config": {
            dense_vector_name: models.VectorParams(size=dense_vector_size, distance=models.Distance.COSINE)
        },
        "sparse_vectors_config": {
            sparse_vector_name: models.SparseVectorParams()
        },
    }


def iter_qdrant_point_payloads(sqlite_path: str | Path) -> Iterator[dict]:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        SELECT concept_id, uuid, display_name, concept_class, datatype, retired, locales_json,
               name_count, description_count, answer_count, set_member_count,
               external_map_sources_json, is_set, search_text, source_version
        FROM concept_bundles
        ORDER BY concept_id
        """
    )
    for row in cursor:
        yield {
            "id": _qdrant_point_id(str(row["concept_id"])),
            "search_text": row["search_text"] or "",
            "payload": {
                "concept_id": row["concept_id"],
                "uuid": row["uuid"],
                "display_name": row["display_name"],
                "concept_class": row["concept_class"],
                "datatype": row["datatype"],
                "retired": bool(row["retired"]),
                "locales": json.loads(row["locales_json"]),
                "name_count": row["name_count"],
                "description_count": row["description_count"],
                "answer_count": row["answer_count"],
                "set_member_count": row["set_member_count"],
                "external_map_sources": json.loads(row["external_map_sources_json"] or "[]"),
                "is_set": bool(row["is_set"]),
                "bundle_ref": row["concept_id"],
                "source_version": row["source_version"],
            },
        }
    conn.close()


def build_qdrant_points(
    sqlite_path: str | Path,
    *,
    dense_encoder: SapBERTEmbedder,
    sparse_encoder: BM25SparseEncoder,
    batch_size: int = 64,
    dense_vector_name: str = DEFAULT_DENSE_VECTOR_NAME,
    sparse_vector_name: str = DEFAULT_SPARSE_VECTOR_NAME,
) -> Iterator[list[dict]]:
    batch: list[dict] = []
    for item in iter_qdrant_point_payloads(sqlite_path):
        batch.append(item)
        if len(batch) >= batch_size:
            yield _encode_point_batch(
                batch,
                dense_encoder=dense_encoder,
                sparse_encoder=sparse_encoder,
                dense_vector_name=dense_vector_name,
                sparse_vector_name=sparse_vector_name,
            )
            batch = []
    if batch:
        yield _encode_point_batch(
            batch,
            dense_encoder=dense_encoder,
            sparse_encoder=sparse_encoder,
            dense_vector_name=dense_vector_name,
            sparse_vector_name=sparse_vector_name,
        )


def _encode_point_batch(
    batch: list[dict],
    *,
    dense_encoder: SapBERTEmbedder,
    sparse_encoder: BM25SparseEncoder,
    dense_vector_name: str,
    sparse_vector_name: str,
) -> list[dict]:
    texts = [item["search_text"] for item in batch]
    dense_vectors = dense_encoder.encode(texts)
    sparse_vectors = sparse_encoder.encode_many(texts)
    encoded: list[dict] = []
    for item, dense_vector, sparse_vector in zip(batch, dense_vectors, sparse_vectors, strict=True):
        vectors: dict[str, object] = {
            dense_vector_name: dense_vector,
            sparse_vector_name: sparse_vector,
        }
        encoded.append({"id": item["id"], "vector": vectors, "payload": item["payload"]})
    return encoded


class QdrantIndexer:
    def __init__(self, url: str, api_key: str | None = None) -> None:
        QdrantClient, _ = _require_qdrant()
        self.client = QdrantClient(url=url, api_key=api_key)

    def ensure_collection(
        self,
        dense_vector_size: int,
        *,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        sparse_vector_name: str = DEFAULT_SPARSE_VECTOR_NAME,
        dense_vector_name: str = DEFAULT_DENSE_VECTOR_NAME,
        recreate: bool = True,
    ) -> None:
        _, models = _require_qdrant()
        config = dict(
            collection_name=collection_name,
            vectors_config={
                dense_vector_name: models.VectorParams(size=dense_vector_size, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={sparse_vector_name: models.SparseVectorParams()},
        )
        if recreate:
            self.client.recreate_collection(**config)
        elif not self.client.collection_exists(collection_name):
            self.client.create_collection(**config)
        self._ensure_payload_indexes(collection_name)

    def _ensure_payload_indexes(self, collection_name: str) -> None:
        _, models = _require_qdrant()
        index_specs = [
            ("concept_id", models.PayloadSchemaType.KEYWORD),
            ("uuid", models.PayloadSchemaType.KEYWORD),
            ("display_name", models.PayloadSchemaType.KEYWORD),
            ("concept_class", models.PayloadSchemaType.KEYWORD),
            ("datatype", models.PayloadSchemaType.KEYWORD),
            ("retired", models.PayloadSchemaType.BOOL),
            ("locales", models.PayloadSchemaType.KEYWORD),
            ("external_map_sources", models.PayloadSchemaType.KEYWORD),
            ("is_set", models.PayloadSchemaType.BOOL),
            ("source_version", models.PayloadSchemaType.KEYWORD),
        ]
        for field_name, field_schema in index_specs:
            self.client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_schema,
            )

    def upsert_batches(self, collection_name: str, batches: Iterable[list[dict]]) -> int:
        _, models = _require_qdrant()
        total = 0
        for batch in batches:
            points = [
                models.PointStruct(
                    id=point["id"],
                    vector={
                        name: (
                            models.SparseVector(indices=value["indices"], values=value["values"])
                            if isinstance(value, dict) and {"indices", "values"} <= set(value.keys())
                            else value
                        )
                        for name, value in point["vector"].items()
                    },
                    payload=point["payload"],
                )
                for point in batch
            ]
            self.client.upsert(collection_name=collection_name, points=points)
            total += len(batch)
        return total


class QdrantHybridSearcher:
    def __init__(
        self,
        url: str,
        *,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        api_key: str | None = None,
        dense_model_name: str = DEFAULT_SAPBERT_MODEL,
        sqlite_path: str | Path,
        dense_vector_name: str = DEFAULT_DENSE_VECTOR_NAME,
        sparse_vector_name: str = DEFAULT_SPARSE_VECTOR_NAME,
    ) -> None:
        QdrantClient, _ = _require_qdrant()
        self.client = QdrantClient(url=url, api_key=api_key)
        self.collection_name = collection_name
        self.dense_vector_name = dense_vector_name
        self.sparse_vector_name = sparse_vector_name
        self.embedder = SapBERTEmbedder(dense_model_name)
        self.sparse_encoder = BM25SparseEncoder(build_sparse_corpus_stats(sqlite_path))

    def search(self, query: str, filters: ConceptSearchFilters, limit: int) -> list[tuple[str, float]]:
        _, models = _require_qdrant()
        dense_query = self.embedder.encode([query])[0]
        sparse_query = self.sparse_encoder.encode_one(query)
        query_filter = self._build_filter(filters)
        prefetch_limit = max(limit * 5, 50)
        response = self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                models.Prefetch(
                    query=dense_query,
                    using=self.dense_vector_name,
                    filter=query_filter,
                    limit=prefetch_limit,
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse_query["indices"],
                        values=sparse_query["values"],
                    ),
                    using=self.sparse_vector_name,
                    filter=query_filter,
                    limit=prefetch_limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter,
            limit=limit,
            with_payload=False,
            with_vectors=False,
        )
        return [(str(point.id), float(point.score or 0.0)) for point in response.points]

    @staticmethod
    def _build_filter(filters: ConceptSearchFilters):
        _, models = _require_qdrant()
        must = []
        if not filters.include_retired:
            must.append(
                models.FieldCondition(key="retired", match=models.MatchValue(value=False))
            )
        if filters.concept_classes:
            must.append(
                models.FieldCondition(
                    key="concept_class",
                    match=models.MatchAny(any=filters.concept_classes),
                )
            )
        if filters.datatypes:
            must.append(
                models.FieldCondition(
                    key="datatype",
                    match=models.MatchAny(any=filters.datatypes),
                )
            )
        if filters.locales:
            must.append(
                models.FieldCondition(
                    key="locales",
                    match=models.MatchAny(any=filters.locales),
                )
            )
        if filters.is_set is not None:
            must.append(
                models.FieldCondition(key="is_set", match=models.MatchValue(value=filters.is_set))
            )
        if not must:
            return None
        return models.Filter(must=must)
