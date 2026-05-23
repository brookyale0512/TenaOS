#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from ciel_search.models import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_SAPBERT_MODEL,
    DEFAULT_SQLITE_PATH,
    ConceptSearchFilters,
)
from ciel_search.pipeline import build_sqlite_store
from ciel_search.qdrant_index import (
    BM25SparseEncoder,
    QdrantIndexer,
    QdrantHybridSearcher,
    SapBERTEmbedder,
    build_qdrant_points,
    build_sparse_corpus_stats,
    qdrant_collection_config,
)
from ciel_search.service import CielSearchService
from ciel_search.validation import validate_store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query the CIEL search index.")
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE_PATH, help="SQLite bundle store path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    build_cmd = subparsers.add_parser("build-store", help="Build the canonical SQLite bundle store")
    build_cmd.add_argument("--export", required=True, help="Path to CIEL export.json")

    validate_cmd = subparsers.add_parser("validate", help="Run validation checks")
    validate_cmd.add_argument("--export", required=True, help="Path to CIEL export.json")
    validate_cmd.add_argument("--sample-size", type=int, default=25)

    config_cmd = subparsers.add_parser("qdrant-config", help="Print Qdrant collection config")
    config_cmd.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    config_cmd.add_argument(
        "--dense-model",
        default=DEFAULT_SAPBERT_MODEL,
        help="SapBERT checkpoint used to determine dense vector size",
    )

    dump_cmd = subparsers.add_parser("dump-points", help="Write Qdrant points as JSONL")
    dump_cmd.add_argument("--output", required=True, help="Destination JSONL file")
    dump_cmd.add_argument(
        "--dense-model",
        default=DEFAULT_SAPBERT_MODEL,
        help="Dense model checkpoint for SapBERT encoding",
    )

    upload_cmd = subparsers.add_parser("upload-points", help="Upload points to Qdrant")
    upload_cmd.add_argument("--url", required=True, help="Qdrant URL")
    upload_cmd.add_argument("--api-key", help="Qdrant API key")
    upload_cmd.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    upload_cmd.add_argument("--recreate", action="store_true", help="Recreate the collection before upload")
    upload_cmd.add_argument(
        "--dense-model",
        default=DEFAULT_SAPBERT_MODEL,
        help="Dense model checkpoint for SapBERT encoding",
    )

    search_cmd = subparsers.add_parser("search", help="Concept search mode: return top matching concepts")
    search_cmd.add_argument("--query", required=True)
    search_cmd.add_argument("--limit", type=int, default=10)
    search_cmd.add_argument("--include-retired", action="store_true")
    search_cmd.add_argument("--qdrant-url", help="Enable hybrid Qdrant search against this URL")
    search_cmd.add_argument("--api-key", help="Qdrant API key for search")
    search_cmd.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    search_cmd.add_argument(
        "--dense-model",
        default=DEFAULT_SAPBERT_MODEL,
        help="SapBERT checkpoint for hybrid query encoding",
    )

    seed_cmd = subparsers.add_parser("search-seeds", help="Seed search mode: return top hits plus recommended seeds")
    seed_cmd.add_argument("--query", required=True)
    seed_cmd.add_argument("--limit", type=int, default=5)
    seed_cmd.add_argument("--seed-limit", type=int, default=3)
    seed_cmd.add_argument("--expansion-depth", type=int, default=3)
    seed_cmd.add_argument("--include-retired", action="store_true")
    seed_cmd.add_argument("--qdrant-url", help="Enable hybrid Qdrant search against this URL")
    seed_cmd.add_argument("--api-key", help="Qdrant API key for search")
    seed_cmd.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    seed_cmd.add_argument(
        "--dense-model",
        default=DEFAULT_SAPBERT_MODEL,
        help="SapBERT checkpoint for hybrid query encoding",
    )

    bundle_cmd = subparsers.add_parser("bundle", help="Fetch a concept bundle")
    bundle_cmd.add_argument("--concept-id", required=True)

    expand_cmd = subparsers.add_parser("expand-seed", help="Expand one exact seed concept into form-ready structure")
    expand_cmd.add_argument("--concept-id", required=True)
    expand_cmd.add_argument("--allow-retired", action="store_true")
    expand_cmd.add_argument("--expansion-depth", type=int, default=3)

    form_cmd = subparsers.add_parser("form-ready", help="Alias for expand-seed")
    form_cmd.add_argument("--concept-id", required=True)
    form_cmd.add_argument("--allow-retired", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sqlite_path = Path(args.sqlite)

    if args.command == "build-store":
        stats = build_sqlite_store(args.export, sqlite_path)
        print(json.dumps(asdict(stats), indent=2))
        return

    if args.command == "validate":
        payload = validate_store(args.export, sqlite_path, sample_size=args.sample_size)
        print(json.dumps(payload, indent=2))
        return

    if args.command == "qdrant-config":
        dense_encoder = SapBERTEmbedder(args.dense_model)
        payload = qdrant_collection_config(dense_encoder.dimension, collection_name=args.collection)
        print(json.dumps(payload, indent=2, default=str))
        return

    if args.command in {"dump-points", "upload-points"}:
        dense_encoder = SapBERTEmbedder(args.dense_model)
        sparse_stats = build_sparse_corpus_stats(sqlite_path)
        sparse_encoder = BM25SparseEncoder(sparse_stats)
        batches = build_qdrant_points(sqlite_path, dense_encoder=dense_encoder, sparse_encoder=sparse_encoder)

        if args.command == "dump-points":
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as handle:
                for batch in batches:
                    for point in batch:
                        handle.write(json.dumps(point, ensure_ascii=False) + "\n")
            print(json.dumps({"output": str(output_path)}, indent=2))
            return

        indexer = QdrantIndexer(args.url, api_key=args.api_key)
        indexer.ensure_collection(
            dense_encoder.dimension,
            collection_name=args.collection,
            recreate=args.recreate,
        )
        total = indexer.upsert_batches(args.collection, batches)
        print(json.dumps({"collection": args.collection, "uploaded_points": total}, indent=2))
        return

    qdrant_search = None
    if args.command in {"search", "search-seeds"} and args.qdrant_url:
        qdrant_search = QdrantHybridSearcher(
            args.qdrant_url,
            api_key=args.api_key,
            collection_name=args.collection,
            dense_model_name=args.dense_model,
            sqlite_path=sqlite_path,
        ).search

    service = CielSearchService(sqlite_path, qdrant_search=qdrant_search)
    if args.command == "search":
        filters = ConceptSearchFilters(include_retired=args.include_retired)
        hits = service.search_concepts(args.query, filters, limit=args.limit)
        print(json.dumps([asdict(hit) for hit in hits], indent=2))
        return

    if args.command == "search-seeds":
        filters = ConceptSearchFilters(include_retired=args.include_retired)
        result = service.search_form_seeds(
            args.query,
            filters,
            limit=args.limit,
            seed_limit=args.seed_limit,
            expansion_depth=args.expansion_depth,
        )
        print(json.dumps(asdict(result), indent=2))
        return

    if args.command == "bundle":
        print(json.dumps(service.get_concept_bundle(args.concept_id), indent=2))
        return

    if args.command == "expand-seed":
        print(
            json.dumps(
                service.expand_seed(
                    args.concept_id,
                    allow_retired=args.allow_retired,
                    expansion_depth=args.expansion_depth,
                ),
                indent=2,
            )
        )
        return

    if args.command == "form-ready":
        print(json.dumps(service.get_form_ready_concept(args.concept_id, allow_retired=args.allow_retired), indent=2))
        return


if __name__ == "__main__":
    main()
