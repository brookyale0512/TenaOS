from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path
from typing import Any

from .models import ConceptSearchFilters
from .pipeline import _stable_json, _stream_items, _top_level_metadata
from .qdrant_index import tokenize
from .service import CielSearchService


def validate_store(
    export_path: str | Path,
    sqlite_path: str | Path,
    *,
    sample_size: int = 25,
) -> dict[str, Any]:
    export_path = Path(export_path)
    sqlite_path = Path(sqlite_path)
    source_metadata = _top_level_metadata(export_path)
    source_version = source_metadata.get("version")

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    service = CielSearchService(sqlite_path)

    export_concepts = 0
    export_mappings = 0
    export_qanda = 0
    export_concept_set = 0
    raw_concept_mismatches = 0

    for concept in _stream_items(export_path, "concepts.item"):
        export_concepts += 1
        row = conn.execute(
            "SELECT raw_concept_json FROM concept_bundles WHERE concept_id = ?",
            (str(concept["id"]),),
        ).fetchone()
        if row is None or row["raw_concept_json"] != _stable_json(concept):
            raw_concept_mismatches += 1

    for mapping in _stream_items(export_path, "mappings.item"):
        export_mappings += 1
        export_qanda += int(mapping.get("map_type") == "Q-AND-A")
        export_concept_set += int(mapping.get("map_type") == "CONCEPT-SET")

    db_concepts = conn.execute("SELECT COUNT(*) AS count FROM concept_bundles").fetchone()["count"]
    db_with_bundle = conn.execute("SELECT COUNT(*) AS count FROM concept_bundles WHERE bundle_json IS NOT NULL").fetchone()["count"]
    db_mappings = conn.execute("SELECT COUNT(*) AS count FROM concept_mappings").fetchone()["count"]
    db_qanda = conn.execute("SELECT COUNT(*) AS count FROM concept_mappings WHERE map_type = 'Q-AND-A'").fetchone()["count"]
    db_concept_set = conn.execute(
        "SELECT COUNT(*) AS count FROM concept_mappings WHERE map_type = 'CONCEPT-SET'"
    ).fetchone()["count"]
    db_answer_total = conn.execute("SELECT COALESCE(SUM(answer_count), 0) AS count FROM concept_bundles").fetchone()["count"]
    db_set_total = conn.execute("SELECT COALESCE(SUM(set_member_count), 0) AS count FROM concept_bundles").fetchone()["count"]
    bad_payload_lists = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM concept_bundles
        WHERE json_type(locales_json) != 'array' OR json_type(external_map_sources_json) != 'array'
        """
    ).fetchone()["count"]
    version_mismatches = conn.execute(
        "SELECT COUNT(*) AS count FROM concept_bundles WHERE source_version IS NOT ?",
        (source_version,),
    ).fetchone()["count"]

    sample_rows = conn.execute(
        """
        SELECT concept_id, display_name
        FROM concept_bundles
        WHERE search_text IS NOT NULL AND TRIM(search_text) != ''
        ORDER BY concept_id
        """
    ).fetchall()
    rng = random.Random(42)
    sampled = rng.sample(sample_rows, k=min(sample_size, len(sample_rows))) if sample_rows else []

    search_roundtrip_failures: list[str] = []
    for row in sampled:
        display_name = row["display_name"] or ""
        query_terms = tokenize(display_name)[:3]
        if not display_name and not query_terms:
            continue
        candidate_queries = []
        if display_name:
            candidate_queries.append(f'"{display_name.replace(chr(34), " ")}"')
        if query_terms:
            candidate_queries.append(" ".join(query_terms))

        hit_ids: set[str] = set()
        for query in candidate_queries:
            hits = service.search_concepts(query, ConceptSearchFilters(include_retired=True), limit=50)
            hit_ids.update(hit.concept_id for hit in hits)

        if row["concept_id"] not in hit_ids:
            search_roundtrip_failures.append(row["concept_id"])
            continue
        bundle = service.get_concept_bundle(row["concept_id"])
        if bundle.get("metadata", {}).get("bundle_ref") != row["concept_id"]:
            search_roundtrip_failures.append(row["concept_id"])

    conn.close()

    return {
        "source_version": source_version,
        "export_size_bytes": export_path.stat().st_size,
        "sqlite_size_bytes": sqlite_path.stat().st_size if sqlite_path.exists() else 0,
        "checks": {
            "concept_count_matches": export_concepts == db_concepts,
            "bundle_count_matches": export_concepts == db_with_bundle,
            "mapping_count_matches": export_mappings == db_mappings,
            "qanda_count_matches": export_qanda == db_qanda == db_answer_total,
            "concept_set_count_matches": export_concept_set == db_concept_set == db_set_total,
            "raw_concept_json_preserved": raw_concept_mismatches == 0,
            "payload_list_fields_valid": bad_payload_lists == 0,
            "source_version_stamped": version_mismatches == 0,
            "search_roundtrip_sample_passed": not search_roundtrip_failures,
        },
        "details": {
            "export_concepts": export_concepts,
            "db_concepts": db_concepts,
            "db_with_bundle": db_with_bundle,
            "export_mappings": export_mappings,
            "db_mappings": db_mappings,
            "export_qanda": export_qanda,
            "db_qanda": db_qanda,
            "db_answer_total": db_answer_total,
            "export_concept_set": export_concept_set,
            "db_concept_set": db_concept_set,
            "db_set_total": db_set_total,
            "raw_concept_mismatches": raw_concept_mismatches,
            "bad_payload_lists": bad_payload_lists,
            "version_mismatches": version_mismatches,
            "search_roundtrip_failures": search_roundtrip_failures,
        },
    }
