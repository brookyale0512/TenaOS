from __future__ import annotations

import json
import sqlite3
from collections import deque
from pathlib import Path
from typing import Callable, Iterable

from .models import (
    ConceptSearchFilters,
    FormComponentCandidate,
    FormSearchResult,
    SearchHit,
    SeedRecommendation,
    SeedSearchResult,
)


class CielSearchService:
    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        qdrant_search: Callable[[str, ConceptSearchFilters, int], list[tuple[str, float]]] | None = None,
    ) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.qdrant_search = qdrant_search

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def search_concepts(
        self,
        query: str,
        filters: ConceptSearchFilters | None = None,
        *,
        limit: int = 10,
    ) -> list[SearchHit]:
        filters = filters or ConceptSearchFilters()
        if self.qdrant_search is not None:
            results = self.qdrant_search(query, filters, limit)
            return self._hydrate_hits(results)
        return self._sqlite_search(query, filters, limit)

    def search_form_seeds(
        self,
        query: str,
        filters: ConceptSearchFilters | None = None,
        *,
        limit: int = 10,
        seed_limit: int = 3,
        expansion_depth: int = 3,
    ) -> SeedSearchResult:
        hits = self.search_concepts(query, filters, limit=limit)
        selected_hits = self._select_form_seeds(hits, seed_limit)
        recommendations = [
            SeedRecommendation(
                hit=hit,
                heuristic_score=self._form_seed_priority(hit),
                rationale=self._form_seed_rationale(hit),
                preview_component_count=len(
                    self._expand_form_candidates([hit], expansion_depth=expansion_depth, include_root=False)
                ),
            )
            for hit in selected_hits
        ]
        return SeedSearchResult(
            query=query,
            hits=hits,
            recommended_seeds=recommendations,
        )

    def search_form_components(
        self,
        query: str,
        filters: ConceptSearchFilters | None = None,
        *,
        limit: int = 10,
        seed_limit: int = 3,
        expansion_depth: int = 3,
    ) -> FormSearchResult:
        seed_result = self.search_form_seeds(
            query,
            filters,
            limit=limit,
            seed_limit=seed_limit,
            expansion_depth=expansion_depth,
        )
        selected_hits = [recommendation.hit for recommendation in seed_result.recommended_seeds]
        components = self._expand_form_candidates(selected_hits, expansion_depth=expansion_depth)
        return FormSearchResult(
            query=query,
            hits=seed_result.hits,
            selected_seed_ids=[hit.concept_id for hit in selected_hits],
            components=components,
        )

    def get_concept_bundle(self, concept_id: str) -> dict:
        conn = self._connect()
        row = conn.execute(
            "SELECT bundle_json FROM concept_bundles WHERE concept_id = ?",
            (concept_id,),
        ).fetchone()
        conn.close()
        if row is None or row["bundle_json"] is None:
            raise KeyError(f"Concept bundle not found for concept_id={concept_id}")
        return json.loads(row["bundle_json"])

    def expand_seed(self, concept_id: str, *, allow_retired: bool = False, expansion_depth: int = 3) -> dict:
        bundle = self.get_concept_bundle(concept_id)
        concept = bundle["concept"]
        if concept.get("retired") and not allow_retired:
            raise ValueError(
                f"Concept {concept_id} is retired and cannot be used for new form/workflow generation by default."
            )
        return {
            "concept": concept,
            "answers": bundle.get("answers", []),
            "set_members": bundle.get("set_members", []),
            "incoming_relationships": bundle.get("incoming_relationships", []),
            "component_candidates": [
                {
                    "concept_id": candidate.concept_id,
                    "bundle_ref": candidate.bundle_ref,
                    "display_name": candidate.display_name,
                    "concept_class": candidate.concept_class,
                    "datatype": candidate.datatype,
                    "retired": candidate.retired,
                    "locales": candidate.locales,
                    "answer_count": candidate.answer_count,
                    "set_member_count": candidate.set_member_count,
                    "role": candidate.role,
                    "source_seed_id": candidate.source_seed_id,
                    "path": candidate.path,
                    "option_preview": candidate.option_preview,
                }
                for candidate in self._expand_form_candidates(
                    [self._search_hit_from_bundle(bundle)],
                    expansion_depth=expansion_depth,
                    include_root=False,
                )
            ],
            "metadata": {
                **bundle.get("metadata", {}),
                "retired": bool(concept.get("retired")),
                "allow_retired": allow_retired,
                "expansion_depth": expansion_depth,
            },
        }

    def get_form_ready_concept(self, concept_id: str, *, allow_retired: bool = False) -> dict:
        return self.expand_seed(concept_id, allow_retired=allow_retired, expansion_depth=3)

    def _hydrate_hits(self, results: Iterable[tuple[str, float]]) -> list[SearchHit]:
        ordered_results = list(results)
        hit_rows = {concept_id: score for concept_id, score in ordered_results}
        if not ordered_results:
            return []
        conn = self._connect()
        placeholders = ",".join("?" for _ in hit_rows)
        rows = conn.execute(
            f"""
            SELECT concept_id, display_name, concept_class, datatype, retired, locales_json,
                   answer_count, set_member_count, external_map_sources_json
            FROM concept_bundles
            WHERE concept_id IN ({placeholders})
            """,
            tuple(hit_rows),
        ).fetchall()
        conn.close()
        row_map = {row["concept_id"]: row for row in rows}
        hydrated: list[SearchHit] = []
        for concept_id, score in ordered_results:
            row = row_map.get(concept_id)
            if row is None:
                continue
            hydrated.append(
                SearchHit(
                    concept_id=row["concept_id"],
                    score=score,
                    bundle_ref=row["concept_id"],
                    display_name=row["display_name"],
                    concept_class=row["concept_class"],
                    datatype=row["datatype"],
                    retired=bool(row["retired"]),
                    locales=json.loads(row["locales_json"]),
                    answer_count=row["answer_count"],
                    set_member_count=row["set_member_count"],
                    external_map_sources=json.loads(row["external_map_sources_json"] or "[]"),
                )
            )
        return hydrated

    def _search_hit_from_bundle(self, bundle: dict) -> SearchHit:
        concept = bundle["concept"]
        concept_id = str(concept.get("concept_id") or concept.get("id"))
        return SearchHit(
            concept_id=concept_id,
            score=1.0,
            bundle_ref=concept_id,
            display_name=concept.get("display_name", ""),
            concept_class=concept.get("concept_class"),
            datatype=concept.get("datatype"),
            retired=bool(concept.get("retired")),
            locales=self._bundle_locales(bundle),
            answer_count=len(bundle.get("answers", [])),
            set_member_count=len(bundle.get("set_members", [])),
            external_map_sources=list(bundle.get("metadata", {}).get("external_map_sources", [])),
        )

    def _select_form_seeds(self, hits: list[SearchHit], seed_limit: int) -> list[SearchHit]:
        ranked = sorted(
            enumerate(hits),
            key=lambda item: (
                self._form_seed_priority(item[1]),
                item[1].answer_count + item[1].set_member_count,
                -item[0],
            ),
            reverse=True,
        )
        selected: list[SearchHit] = []
        seen: set[str] = set()
        for _, hit in ranked:
            if hit.concept_id in seen:
                continue
            priority = self._form_seed_priority(hit)
            if priority < 0 and selected:
                continue
            seen.add(hit.concept_id)
            selected.append(hit)
            if len(selected) >= seed_limit:
                break
        return selected or hits[:seed_limit]

    def _form_seed_priority(self, hit: SearchHit) -> int:
        priority = 0
        if hit.set_member_count > 0:
            priority += 6
        if hit.answer_count > 0:
            priority += 5
        if hit.concept_class in {"ConvSet", "LabSet"}:
            priority += 4
        if hit.concept_class in {"Question", "Finding", "Test", "Obs"}:
            priority += 2
        if hit.datatype in {"Coded", "Numeric", "Text", "Boolean", "Date", "Datetime"}:
            priority += 2
        if hit.concept_class == "Diagnosis":
            # CIEL classifies many plain-language symptoms (e.g. Otalgia,
            # Tinnitus, Amenorrhea) as Diagnosis-class with an N/A or Boolean
            # datatype. These render as valid Yes/No presence questions, so they
            # should NOT be penalized; only Diagnosis concepts with a datatype
            # that cannot collect an observation are demoted.
            if hit.datatype in {"N/A", "Boolean", "", None}:
                priority += 1
            else:
                priority -= 5
        if hit.concept_class == "Drug":
            priority -= 4
        if hit.concept_class == "Procedure":
            priority -= 2
        if hit.retired:
            priority -= 10
        return priority

    def _form_seed_rationale(self, hit: SearchHit) -> list[str]:
        rationale: list[str] = []
        if hit.set_member_count > 0:
            rationale.append(f"expandable set with {hit.set_member_count} set members")
        if hit.answer_count > 0:
            rationale.append(f"coded/question-like concept with {hit.answer_count} answers")
        if hit.concept_class in {"ConvSet", "LabSet", "MedSet"}:
            rationale.append(f"{hit.concept_class} concept is suitable as a section seed")
        elif hit.concept_class in {"Question", "Finding", "Test", "Obs"}:
            rationale.append(f"{hit.concept_class} concept is suitable as a field seed")
        if hit.datatype in {"Coded", "Numeric", "Text", "Boolean", "Date", "Datetime"}:
            rationale.append(f"{hit.datatype} datatype is useful for form fields")
        if not rationale:
            rationale.append("selected as the least weak structural candidate among top hits")
        return rationale

    def _expand_form_candidates(
        self,
        seed_hits: list[SearchHit],
        *,
        expansion_depth: int,
        include_root: bool = True,
    ) -> list[FormComponentCandidate]:
        candidates: dict[str, FormComponentCandidate] = {}
        queue = deque(
            (seed_hit.concept_id, seed_hit.concept_id, [], 0)
            for seed_hit in seed_hits
        )
        seen: set[tuple[str, str, int]] = set()

        while queue:
            concept_id, seed_id, path, depth = queue.popleft()
            state = (concept_id, seed_id, depth)
            if state in seen or depth > expansion_depth:
                continue
            seen.add(state)

            bundle = self.get_concept_bundle(concept_id)
            concept = bundle["concept"]
            answers = bundle.get("answers", [])
            set_members = bundle.get("set_members", [])
            display_name = concept.get("display_name") or concept_id
            next_path = [*path, display_name]

            role = self._infer_form_role(bundle)
            should_include = (
                role is not None
                and (include_root or depth > 0)
                and not (depth == 0 and role == "answer_set")
            )
            if should_include:
                candidate = FormComponentCandidate(
                    concept_id=str(concept_id),
                    bundle_ref=str(concept_id),
                    display_name=display_name,
                    concept_class=concept.get("concept_class"),
                    datatype=concept.get("datatype"),
                    retired=bool(concept.get("retired")),
                    locales=self._bundle_locales(bundle),
                    answer_count=len(answers),
                    set_member_count=len(set_members),
                    role=role,
                    source_seed_id=seed_id,
                    path=next_path,
                    option_preview=[
                        rel.get("target", {}).get("display_name")
                        for rel in answers[:5]
                        if rel.get("target", {}).get("display_name")
                    ],
                )
                existing = candidates.get(candidate.concept_id)
                if existing is None or len(candidate.path) < len(existing.path):
                    candidates[candidate.concept_id] = candidate

            if depth >= expansion_depth:
                continue

            for relation in set_members:
                target = relation.get("target", {})
                target_id = target.get("concept_id")
                if target_id:
                    queue.append((str(target_id), seed_id, next_path, depth + 1))

        return sorted(
            candidates.values(),
            key=lambda candidate: (
                0 if candidate.role == "section" else 1,
                len(candidate.path),
                candidate.display_name.lower(),
            ),
        )

    def _infer_form_role(self, bundle: dict) -> str | None:
        concept = bundle["concept"]
        concept_class = concept.get("concept_class")
        datatype = concept.get("datatype")
        answers = bundle.get("answers", [])
        set_members = bundle.get("set_members", [])

        if set_members:
            return "section"
        if answers and concept_class in {"Question", "Finding", "Test", "Obs"}:
            return "field"
        if datatype in {"Numeric", "Text", "Boolean", "Date", "Datetime", "Document", "Coded"}:
            return "field"
        if concept_class in {"Question", "Finding", "Test", "Obs"}:
            return "field"
        if answers:
            return "answer_set"
        return None

    def _bundle_locales(self, bundle: dict) -> list[str]:
        metadata_locales = bundle.get("metadata", {}).get("locales", [])
        if metadata_locales:
            return list(metadata_locales)
        locales = {
            name.get("locale")
            for name in bundle.get("names", [])
            if name.get("locale")
        }
        return sorted(locales)

    def _sqlite_search(
        self,
        query: str,
        filters: ConceptSearchFilters,
        limit: int,
    ) -> list[SearchHit]:
        conn = self._connect()
        conditions: list[str] = []
        params: list[object] = []

        if not filters.include_retired:
            conditions.append("cb.retired = 0")
        if filters.concept_classes:
            placeholders = ",".join("?" for _ in filters.concept_classes)
            conditions.append(f"cb.concept_class IN ({placeholders})")
            params.extend(filters.concept_classes)
        if filters.datatypes:
            placeholders = ",".join("?" for _ in filters.datatypes)
            conditions.append(f"cb.datatype IN ({placeholders})")
            params.extend(filters.datatypes)
        if filters.is_set is not None:
            conditions.append("cb.is_set = ?")
            params.append(int(filters.is_set))
        if filters.locales:
            locale_checks = []
            for locale in filters.locales:
                locale_checks.append("cb.locales_json LIKE ?")
                params.append(f'%"{locale}"%')
            conditions.append("(" + " OR ".join(locale_checks) + ")")

        fts_conditions = list(conditions)
        fts_conditions.append("fts.search_text MATCH ?")
        fts_where_clause = "WHERE " + " AND ".join(fts_conditions)
        like_conditions = list(conditions)
        like_conditions.append("LOWER(cb.search_text) LIKE ?")
        like_where_clause = "WHERE " + " AND ".join(like_conditions)

        rows: list[sqlite3.Row] = []
        try:
            rows = conn.execute(
                f"""
                SELECT cb.concept_id, cb.display_name, cb.concept_class, cb.datatype, cb.retired, cb.locales_json,
                       cb.answer_count, cb.set_member_count, cb.external_map_sources_json,
                       bm25(fts) AS score
                FROM concept_search_fts AS fts
                JOIN concept_bundles AS cb ON cb.concept_id = fts.concept_id
                {fts_where_clause}
                ORDER BY score
                LIMIT ?
                """,
                (*params, query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        seen_ids = {row["concept_id"] for row in rows}
        normalized_like_query = query.replace('"', "").strip().lower()
        if len(rows) < limit and normalized_like_query:
            like_query = f"%{normalized_like_query}%"
            like_rows = conn.execute(
                f"""
                SELECT cb.concept_id, cb.display_name, cb.concept_class, cb.datatype, cb.retired, cb.locales_json,
                       cb.answer_count, cb.set_member_count, cb.external_map_sources_json,
                       0.0 AS score
                FROM concept_bundles AS cb
                {like_where_clause}
                ORDER BY
                    CASE
                        WHEN LOWER(cb.display_name) = ? THEN 0
                        WHEN LOWER(cb.display_name) LIKE ? THEN 1
                        ELSE 2
                    END,
                    cb.display_name
                LIMIT ?
                """,
                (*params, like_query, normalized_like_query, f"{normalized_like_query}%", limit),
            ).fetchall()
            for row in like_rows:
                if row["concept_id"] in seen_ids:
                    continue
                rows.append(row)
                seen_ids.add(row["concept_id"])
                if len(rows) >= limit:
                    break

        conn.close()
        return [
            SearchHit(
                concept_id=row["concept_id"],
                score=float(row["score"]),
                bundle_ref=row["concept_id"],
                display_name=row["display_name"],
                concept_class=row["concept_class"],
                datatype=row["datatype"],
                retired=bool(row["retired"]),
                locales=json.loads(row["locales_json"]),
                answer_count=row["answer_count"],
                set_member_count=row["set_member_count"],
                external_map_sources=json.loads(row["external_map_sources_json"] or "[]"),
            )
            for row in rows
        ]
