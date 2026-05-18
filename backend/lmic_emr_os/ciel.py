from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from .validation import ValidationIssue


def _ensure_ciel_path(repo_root: Path) -> None:
    ciel_path = repo_root / "CIEL"
    if str(ciel_path) not in sys.path:
        sys.path.insert(0, str(ciel_path))


def _normalize_concept_ref(ref: str) -> str:
    value = ref.strip()
    if not value:
        return ""
    if ":" in value:
        prefix, raw_value = value.split(":", 1)
        if prefix.upper() == "CIEL":
            return raw_value.strip()
    return value


@dataclass(slots=True)
class ConceptValidationResult:
    requested_ref: str
    concept_id: str
    display_name: str
    retired: bool


def _search_hit_value(payload: dict[str, Any], key: str, default: Any = None) -> Any:
    value = payload.get(key, default)
    if value is None:
        return default
    return value


def _serialize_search_hit(hit: Any) -> dict[str, Any]:
    if is_dataclass(hit):
        payload = asdict(hit)
    elif isinstance(hit, dict):
        payload = dict(hit)
    else:
        payload = {
            "concept_id": getattr(hit, "concept_id", ""),
            "score": getattr(hit, "score", 0.0),
            "bundle_ref": getattr(hit, "bundle_ref", getattr(hit, "concept_id", "")),
            "display_name": getattr(hit, "display_name", ""),
            "concept_class": getattr(hit, "concept_class", None),
            "datatype": getattr(hit, "datatype", None),
            "retired": getattr(hit, "retired", False),
            "locales": getattr(hit, "locales", []),
            "answer_count": getattr(hit, "answer_count", 0),
            "set_member_count": getattr(hit, "set_member_count", 0),
            "external_map_sources": getattr(hit, "external_map_sources", []),
        }
    return {
        "concept_id": str(_search_hit_value(payload, "concept_id", "")),
        "score": float(_search_hit_value(payload, "score", 0.0) or 0.0),
        "bundle_ref": str(_search_hit_value(payload, "bundle_ref", payload.get("concept_id", ""))),
        "display_name": str(_search_hit_value(payload, "display_name", "")),
        "concept_class": (
            str(_search_hit_value(payload, "concept_class", "")) if _search_hit_value(payload, "concept_class", None) else None
        ),
        "datatype": str(_search_hit_value(payload, "datatype", "")) if _search_hit_value(payload, "datatype", None) else None,
        "retired": bool(_search_hit_value(payload, "retired", False)),
        "locales": [str(value) for value in (_search_hit_value(payload, "locales", []) or [])],
        "answer_count": int(_search_hit_value(payload, "answer_count", 0) or 0),
        "set_member_count": int(_search_hit_value(payload, "set_member_count", 0) or 0),
        "external_map_sources": [
            str(value) for value in (_search_hit_value(payload, "external_map_sources", []) or [])
        ],
    }


class CielTerminologyService:
    def __init__(
        self,
        repo_root: str | Path,
        *,
        sqlite_path: str | Path | None = None,
        export_path: str | Path | None = None,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        qdrant_collection: str = "ciel_concepts",
        service: Any | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.sqlite_path = Path(sqlite_path or self.repo_root / "CIEL" / "ciel_search.sqlite3")
        self.export_path = Path(export_path or self.repo_root / "CIEL" / "export.json")
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key
        self.qdrant_collection = qdrant_collection
        self._service = service

    def ensure_store(self) -> Path:
        if self.sqlite_path.exists():
            return self.sqlite_path
        if not self.export_path.exists():
            raise FileNotFoundError(
                f"CIEL export not found at '{self.export_path}'. Cannot build terminology store."
            )
        _ensure_ciel_path(self.repo_root)
        from ciel_search import build_sqlite_store

        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        build_sqlite_store(self.export_path, self.sqlite_path)
        return self.sqlite_path

    def build_service(self) -> Any:
        if self._service is not None:
            return self._service

        _ensure_ciel_path(self.repo_root)
        self.ensure_store()
        from ciel_search import CielSearchService, QdrantHybridSearcher

        qdrant_search = None
        if self.qdrant_url:
            qdrant_searcher = QdrantHybridSearcher(
                self.qdrant_url,
                api_key=self.qdrant_api_key,
                collection_name=self.qdrant_collection,
                sqlite_path=self.sqlite_path,
            )
            qdrant_search = qdrant_searcher.search
        self._service = CielSearchService(self.sqlite_path, qdrant_search=qdrant_search)
        return self._service

    def build_qdrant_index(self, *, recreate: bool = False) -> int:
        _ensure_ciel_path(self.repo_root)
        self.ensure_store()
        from ciel_search import SapBERTEmbedder
        from ciel_search.qdrant_index import (
            BM25SparseEncoder,
            QdrantIndexer,
            build_qdrant_points,
            build_sparse_corpus_stats,
        )

        if not self.qdrant_url:
            raise ValueError("Qdrant URL is required to build the hybrid search index.")

        embedder = SapBERTEmbedder()
        sparse_encoder = BM25SparseEncoder(build_sparse_corpus_stats(self.sqlite_path))
        indexer = QdrantIndexer(self.qdrant_url, api_key=self.qdrant_api_key)
        indexer.ensure_collection(
            embedder.dimension,
            collection_name=self.qdrant_collection,
            recreate=recreate,
        )
        return indexer.upsert_batches(
            self.qdrant_collection,
            build_qdrant_points(
                self.sqlite_path,
                dense_encoder=embedder,
                sparse_encoder=sparse_encoder,
            ),
        )

    def search_concepts(
        self,
        query: str,
        *,
        concept_classes: list[str] | None = None,
        datatypes: list[str] | None = None,
        include_retired: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        _ensure_ciel_path(self.repo_root)
        from ciel_search import ConceptSearchFilters

        filters = ConceptSearchFilters(
            concept_classes=concept_classes,
            datatypes=datatypes,
            include_retired=include_retired,
        )
        return [_serialize_search_hit(hit) for hit in self.build_service().search_concepts(query, filters, limit=limit)]

    def search_form_seeds(self, query: str, *, limit: int = 10, seed_limit: int = 3, expansion_depth: int = 3) -> Any:
        return self.build_service().search_form_seeds(
            query,
            limit=limit,
            seed_limit=seed_limit,
            expansion_depth=expansion_depth,
        )

    def search_form_components(
        self,
        query: str,
        *,
        limit: int = 10,
        seed_limit: int = 3,
        expansion_depth: int = 3,
    ) -> Any:
        return self.build_service().search_form_components(
            query,
            limit=limit,
            seed_limit=seed_limit,
            expansion_depth=expansion_depth,
        )

    def get_form_ready_bundle(self, concept_ref: str, *, allow_retired: bool = False) -> dict[str, Any]:
        concept_id = _normalize_concept_ref(concept_ref)
        if not concept_id:
            raise ValueError("Concept reference must not be empty.")
        return self.build_service().get_form_ready_concept(concept_id, allow_retired=allow_retired)

    def validate_concept_refs(self, refs: Iterable[str], allow_retired: bool = False) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for ref in refs:
            concept_id = _normalize_concept_ref(ref)
            if not concept_id:
                issues.append(ValidationIssue("error", "concepts", "Empty concept reference is not allowed."))
                continue
            try:
                bundle = self.build_service().get_concept_bundle(concept_id)
            except KeyError:
                issues.append(
                    ValidationIssue(
                        "error",
                        "concepts",
                        f"Concept reference '{ref}' is not present in the local CIEL store.",
                    )
                )
                continue
            concept = bundle.get("concept", {})
            if concept.get("retired") and not allow_retired:
                issues.append(
                    ValidationIssue(
                        "error",
                        "concepts",
                        f"Concept reference '{ref}' resolves to retired concept '{concept_id}'.",
                    )
                )
        return issues

    def validate_single_concept(self, ref: str, allow_retired: bool = False) -> ConceptValidationResult:
        concept_id = _normalize_concept_ref(ref)
        bundle = self.build_service().get_concept_bundle(concept_id)
        concept = bundle.get("concept", {})
        retired = bool(concept.get("retired"))
        if retired and not allow_retired:
            raise ValueError(f"Concept '{ref}' is retired and cannot be used for new clinic configuration.")
        return ConceptValidationResult(
            requested_ref=ref,
            concept_id=concept_id,
            display_name=str(concept.get("display_name", concept_id)),
            retired=retired,
        )
