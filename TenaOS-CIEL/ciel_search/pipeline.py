from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .models import (
    BuildStats,
    ConceptBundle,
    DEFAULT_SQLITE_PATH,
    INTERNAL_RELATIONSHIP_TYPES,
    MAX_RELATIONSHIP_LABELS,
    MAX_SEARCH_DESCRIPTIONS,
    MAX_SEARCH_SYNONYMS,
)


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS source_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS concept_bundles (
        concept_id TEXT PRIMARY KEY,
        uuid TEXT NOT NULL,
        display_name TEXT NOT NULL,
        concept_class TEXT,
        datatype TEXT,
        retired INTEGER NOT NULL,
        is_set INTEGER NOT NULL,
        locales_json TEXT NOT NULL,
        name_count INTEGER NOT NULL,
        description_count INTEGER NOT NULL,
        answer_count INTEGER NOT NULL DEFAULT 0,
        set_member_count INTEGER NOT NULL DEFAULT 0,
        external_map_sources_json TEXT NOT NULL DEFAULT '[]',
        source_version TEXT,
        raw_concept_json TEXT NOT NULL,
        bundle_json TEXT,
        search_text TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS concept_mappings (
        mapping_id TEXT PRIMARY KEY,
        map_type TEXT NOT NULL,
        retired INTEGER NOT NULL,
        sort_weight REAL,
        from_concept_code TEXT,
        to_concept_code TEXT,
        to_source_name TEXT,
        resolved_to_concept_id TEXT,
        from_source_name TEXT,
        is_internal_form_link INTEGER NOT NULL,
        mapping_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_concept_mappings_from ON concept_mappings(from_concept_code)",
    "CREATE INDEX IF NOT EXISTS idx_concept_mappings_to ON concept_mappings(to_concept_code)",
    "CREATE INDEX IF NOT EXISTS idx_concept_mappings_type ON concept_mappings(map_type)",
)


def _require_ijson():
    try:
        import ijson
    except ImportError as exc:  # pragma: no cover - import guard
        raise SystemExit("Missing dependency 'ijson'. Install CIEL/requirements.txt first.") from exc
    return ijson


def _normalize_json_value(payload: Any) -> Any:
    if isinstance(payload, Decimal):
        if payload == payload.to_integral_value():
            return int(payload)
        return float(payload)
    if isinstance(payload, dict):
        return {str(key): _normalize_json_value(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_normalize_json_value(value) for value in payload]
    return payload


def _stable_json(payload: Any) -> str:
    return json.dumps(_normalize_json_value(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_list(payload: Iterable[str]) -> str:
    return _stable_json(list(payload))


def _connect_sqlite(sqlite_path: Path) -> sqlite3.Connection:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    if sqlite_path.exists():
        sqlite_path.unlink()

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=OFF")
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS concept_search_fts
            USING fts5(concept_id UNINDEXED, search_text)
            """
        )
    except sqlite3.OperationalError:
        pass
    return conn


def _top_level_metadata(export_path: Path) -> dict[str, str]:
    ijson = _require_ijson()
    metadata: dict[str, str] = {}
    current_key: str | None = None
    scalar_events = {"string", "number", "boolean", "null"}
    wanted = {"version", "version_url", "previous_version_url", "created_on", "updated_on", "description", "id"}

    with export_path.open("rb") as handle:
        for prefix, event, value in ijson.parse(handle):
            if prefix == "" and event == "map_key":
                current_key = value
                if value == "concepts" and metadata:
                    break
                continue
            if current_key and prefix == current_key and event in scalar_events and current_key in wanted:
                metadata[current_key] = "" if value is None else str(value)
                current_key = None
    return metadata


def _stream_items(export_path: Path, prefix: str):
    ijson = _require_ijson()
    with export_path.open("rb") as handle:
        for item in ijson.items(handle, prefix):
            yield _normalize_json_value(item)


def _locale_priority(locale: str | None) -> tuple[int, str]:
    if locale == "en":
        return (0, "en")
    if locale is None:
        return (2, "")
    return (1, locale)


def _name_sort_key(name: dict[str, Any]) -> tuple[Any, ...]:
    name_type_order = {"FULLY_SPECIFIED": 0, None: 1, "SHORT": 2, "INDEX_TERM": 3}
    return (
        _locale_priority(name.get("locale")),
        0 if name.get("locale_preferred") else 1,
        name_type_order.get(name.get("name_type"), 4),
        len(name.get("name", "")),
        name.get("name", "").lower(),
    )


def _description_sort_key(description: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _locale_priority(description.get("locale")),
        len(description.get("description", "")),
        description.get("description", "").lower(),
    )


def _normalized_text_key(value: str) -> str:
    return " ".join(value.lower().split())


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not value:
            continue
        key = _normalized_text_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(value.strip())
    return unique


def _preferred_display_name(concept: dict[str, Any]) -> str:
    names = sorted(concept.get("names", []), key=_name_sort_key)
    for name in names:
        if name.get("locale") == "en" and name.get("locale_preferred") and name.get("name"):
            return name["name"]
    for name in names:
        if name.get("locale_preferred") and name.get("name"):
            return name["name"]
    return concept.get("display_name") or concept.get("id") or "unknown-concept"


def _relationship_display_name(mapping: dict[str, Any], summary: dict[str, Any] | None, direction: str) -> str | None:
    if summary:
        return summary.get("display_name")
    if direction == "incoming":
        return mapping.get("from_concept_name_resolved") or mapping.get("from_concept_name")
    return mapping.get("to_concept_name_resolved") or mapping.get("to_concept_name")


def _concept_summary_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "concept_id": row["concept_id"],
        "uuid": row["uuid"],
        "display_name": row["display_name"],
        "concept_class": row["concept_class"],
        "datatype": row["datatype"],
        "retired": bool(row["retired"]),
        "locales": json.loads(row["locales_json"]),
        "is_set": bool(row["is_set"]),
    }


def _mapping_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    sort_weight = item.get("sort_weight")
    fallback = item.get("target", {}).get("concept_id") or item.get("source", {}).get("concept_id") or ""
    return (
        1 if sort_weight is None else 0,
        float(sort_weight or 0.0),
        fallback,
    )


def _relationship_record(
    mapping: dict[str, Any],
    summary: dict[str, Any] | None,
    *,
    direction: str,
) -> dict[str, Any]:
    target_key = "source" if direction == "incoming" else "target"
    concept_code_key = "from_concept_code" if direction == "incoming" else "to_concept_code"
    source_name_key = "from_source_name" if direction == "incoming" else "to_source_name"
    unresolved = summary is None
    relation_target = summary or {
        "concept_id": mapping.get(concept_code_key),
        "display_name": _relationship_display_name(mapping, None, direction),
        "source_name": mapping.get(source_name_key),
    }

    return {
        "map_type": mapping.get("map_type"),
        "direction": direction,
        "retired": bool(mapping.get("retired")),
        "resolved": not unresolved,
        "sort_weight": mapping.get("sort_weight"),
        target_key: relation_target,
        "mapping": mapping,
    }


def _build_search_text(
    concept: dict[str, Any],
    answers: list[dict[str, Any]],
    set_members: list[dict[str, Any]],
    external_mappings: list[dict[str, Any]],
) -> str:
    names = sorted(concept.get("names", []), key=_name_sort_key)
    descriptions = sorted(concept.get("descriptions", []), key=_description_sort_key)
    preferred_name = _preferred_display_name(concept)

    synonym_candidates = [name.get("name", "") for name in names if name.get("name")]
    synonyms = _dedupe_preserve_order(synonym_candidates)
    if preferred_name in synonyms:
        synonyms.remove(preferred_name)
    synonyms = synonyms[:MAX_SEARCH_SYNONYMS]

    description_values = _dedupe_preserve_order(
        description.get("description", "")
        for description in descriptions
        if description.get("description")
    )[:MAX_SEARCH_DESCRIPTIONS]

    relationship_labels = _dedupe_preserve_order(
        [
            _relationship_display_name(item.get("mapping", {}), item.get("target"), "outgoing") or ""
            for item in answers
        ]
        + [
            _relationship_display_name(item.get("mapping", {}), item.get("target"), "outgoing") or ""
            for item in set_members
        ]
    )[:MAX_RELATIONSHIP_LABELS]

    external_codes = _dedupe_preserve_order(
        f"{item['mapping'].get('to_source_name')}:{item['mapping'].get('to_concept_code')}"
        for item in external_mappings
        if item["mapping"].get("to_source_name") and item["mapping"].get("to_concept_code")
    )

    blocks = [
        f"display_name: {preferred_name}",
        f"concept_class: {concept.get('concept_class') or ''}",
        f"datatype: {concept.get('datatype') or ''}",
    ]
    if synonyms:
        blocks.append("synonyms: " + " | ".join(synonyms))
    if description_values:
        blocks.append("descriptions: " + " | ".join(description_values))
    if relationship_labels:
        blocks.append("linked_labels: " + " | ".join(relationship_labels))
    if external_codes:
        blocks.append("external_codes: " + " | ".join(external_codes))
    return "\n".join(blocks).strip()


def _iter_mapping_rows(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    column: str,
) -> Iterable[dict[str, Any]]:
    cursor = conn.execute(
        f"""
        SELECT mapping_json
        FROM concept_mappings
        WHERE {column} = ?
        ORDER BY
            CASE WHEN sort_weight IS NULL THEN 1 ELSE 0 END,
            sort_weight ASC,
            COALESCE(to_concept_code, from_concept_code, '') ASC
        """,
        (concept_id,),
    )
    for row in cursor:
        yield json.loads(row["mapping_json"])


def build_sqlite_store(export_path: str | Path, sqlite_path: str | Path | None = None) -> BuildStats:
    export_path = Path(export_path)
    sqlite_path = Path(sqlite_path or export_path.parent / DEFAULT_SQLITE_PATH)
    source_metadata = _top_level_metadata(export_path)
    conn = _connect_sqlite(sqlite_path)

    for key, value in source_metadata.items():
        conn.execute("INSERT OR REPLACE INTO source_metadata(key, value) VALUES(?, ?)", (key, value))

    concept_ids: set[str] = set()
    concept_count = 0
    retired_concept_count = 0

    for concept in _stream_items(export_path, "concepts.item"):
        concept_id = str(concept["id"])
        concept_ids.add(concept_id)
        concept_count += 1
        retired = bool(concept.get("retired"))
        retired_concept_count += int(retired)
        locales = sorted(
            {
                name.get("locale")
                for name in concept.get("names", [])
                if isinstance(name, dict) and name.get("locale")
            },
            key=_locale_priority,
        )
        conn.execute(
            """
            INSERT INTO concept_bundles(
                concept_id, uuid, display_name, concept_class, datatype, retired, is_set,
                locales_json, name_count, description_count, source_version, raw_concept_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                concept_id,
                str(concept.get("uuid", "")),
                concept.get("display_name") or concept_id,
                concept.get("concept_class"),
                concept.get("datatype"),
                int(retired),
                int(bool((concept.get("extras") or {}).get("is_set"))),
                _json_list(locales),
                len(concept.get("names", [])),
                len(concept.get("descriptions", [])),
                source_metadata.get("version"),
                _stable_json(concept),
            ),
        )

    mapping_count = 0
    qanda_edges = 0
    concept_set_edges = 0

    for mapping in _stream_items(export_path, "mappings.item"):
        mapping_count += 1
        map_type = mapping.get("map_type")
        qanda_edges += int(map_type == "Q-AND-A")
        concept_set_edges += int(map_type == "CONCEPT-SET")
        to_code = mapping.get("to_concept_code")
        to_source_name = mapping.get("to_source_name")
        resolved_to_concept_id = None
        if to_code and to_source_name == "CIEL" and str(to_code) in concept_ids:
            resolved_to_concept_id = str(to_code)
        conn.execute(
            """
            INSERT INTO concept_mappings(
                mapping_id, map_type, retired, sort_weight, from_concept_code, to_concept_code,
                to_source_name, resolved_to_concept_id, from_source_name, is_internal_form_link, mapping_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(mapping.get("id")),
                map_type,
                int(bool(mapping.get("retired"))),
                mapping.get("sort_weight"),
                str(mapping.get("from_concept_code") or ""),
                str(to_code or "") if to_code is not None else None,
                to_source_name,
                resolved_to_concept_id,
                mapping.get("from_source_name"),
                int(map_type in INTERNAL_RELATIONSHIP_TYPES and to_source_name == "CIEL"),
                _stable_json(mapping),
            ),
        )

    conn.commit()

    @lru_cache(maxsize=16384)
    def summary_for(concept_id: str | None) -> dict[str, Any] | None:
        if not concept_id:
            return None
        row = conn.execute(
            """
            SELECT concept_id, uuid, display_name, concept_class, datatype, retired, locales_json, is_set
            FROM concept_bundles
            WHERE concept_id = ?
            """,
            (concept_id,),
        ).fetchone()
        return _concept_summary_from_row(row)

    concept_rows = conn.execute(
        """
        SELECT concept_id, raw_concept_json
        FROM concept_bundles
        ORDER BY concept_id
        """
    ).fetchall()

    for row in concept_rows:
        concept_id = row["concept_id"]
        concept = json.loads(row["raw_concept_json"])
        outgoing_rows = list(_iter_mapping_rows(conn, concept_id, column="from_concept_code"))
        incoming_rows = list(_iter_mapping_rows(conn, concept_id, column="to_concept_code"))

        answers: list[dict[str, Any]] = []
        set_members: list[dict[str, Any]] = []
        external_mappings: list[dict[str, Any]] = []
        incoming_relationships: list[dict[str, Any]] = []
        unresolved_count = 0

        for mapping in outgoing_rows:
            target_summary = None
            if mapping.get("to_source_name") == "CIEL":
                target_summary = summary_for(str(mapping.get("to_concept_code") or ""))
            relation = _relationship_record(mapping, target_summary, direction="outgoing")
            unresolved_count += int(not relation["resolved"])
            if relation["map_type"] == "Q-AND-A":
                answers.append(relation)
            elif relation["map_type"] == "CONCEPT-SET":
                set_members.append(relation)
            else:
                external_mappings.append(relation)

        for mapping in incoming_rows:
            source_summary = summary_for(str(mapping.get("from_concept_code") or ""))
            relation = _relationship_record(mapping, source_summary, direction="incoming")
            unresolved_count += int(not relation["resolved"])
            incoming_relationships.append(relation)

        answers.sort(key=_mapping_sort_key)
        set_members.sort(key=_mapping_sort_key)
        incoming_relationships.sort(key=_mapping_sort_key)
        external_mappings.sort(key=lambda item: (item["mapping"].get("map_type") or "",) + _mapping_sort_key(item))

        search_text = _build_search_text(concept, answers, set_members, external_mappings)
        external_sources = sorted(
            {
                item["mapping"].get("to_source_name")
                for item in external_mappings
                if item["mapping"].get("to_source_name")
            }
        )
        bundle = ConceptBundle(
            concept=concept,
            names=concept.get("names", []),
            descriptions=concept.get("descriptions", []),
            answers=answers,
            set_members=set_members,
            external_mappings=external_mappings,
            incoming_relationships=incoming_relationships,
            search_text=search_text,
            source_version=source_metadata.get("version"),
            unresolved_relationship_count=unresolved_count,
            metadata={
                "bundle_ref": concept_id,
                "bundle_backend": "sqlite",
                "retired": bool(concept.get("retired")),
                "is_set": bool((concept.get("extras") or {}).get("is_set")),
                "external_map_sources": external_sources,
            },
        ).to_dict()

        conn.execute(
            """
            UPDATE concept_bundles
            SET answer_count = ?, set_member_count = ?, external_map_sources_json = ?, bundle_json = ?, search_text = ?
            WHERE concept_id = ?
            """,
            (
                len(answers),
                len(set_members),
                _json_list(external_sources),
                _stable_json(bundle),
                search_text,
                concept_id,
            ),
        )
        try:
            conn.execute("INSERT INTO concept_search_fts(concept_id, search_text) VALUES (?, ?)", (concept_id, search_text))
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

    return BuildStats(
        export_path=str(export_path),
        sqlite_path=str(sqlite_path),
        source_version=source_metadata.get("version"),
        concept_count=concept_count,
        retired_concept_count=retired_concept_count,
        mapping_count=mapping_count,
        qanda_edges=qanda_edges,
        concept_set_edges=concept_set_edges,
    )
