"""Thin client over the existing CIEL search package.

The form builder needs three CIEL operations:
    1. search_form_seeds(query) -> ranked seed concepts suitable as section roots
    2. expand_seed(concept_id) -> bundle (concept + answers + set_members)
    3. get_concept_bundle(concept_id) -> raw bundle

The canonical implementation lives in
`/var/www/TenaOS/TenaOS-CIEL/ciel_search/`. This client imports it via
`sys.path` injection (configurable through `TENAOS_CIEL_ROOT`) and falls
back to a thin direct-SQLite reader if the package is unavailable so the
TenaAgent service can boot in restricted environments.

Concept identity:
    CIEL stores concepts by their numeric id (`5089`). OpenMRS seeds them
    with the UUID padding convention `<id>` + `A` * (36 - len(id)).
    `openmrs_uuid_for_concept_id` is the single source of truth for that
    mapping and is used by both the schema builder and the publisher.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import Settings

_LOGGER = logging.getLogger("tenaos.tena_agent.ciel")


def openmrs_uuid_for_concept_id(concept_id: str | int) -> str:
    """Return the canonical OpenMRS UUID for a CIEL concept id.

    OpenMRS seeds CIEL concepts as `<id><A repeated to 36 chars>` so a
    numeric id like 5089 becomes `5089` + `A` * 32.
    """
    raw = str(concept_id).strip()
    if not raw:
        raise ValueError("concept_id must not be empty")
    if len(raw) > 36:
        raise ValueError(f"concept_id '{raw}' exceeds 36 characters")
    return raw + ("A" * (36 - len(raw)))


@dataclass(frozen=True)
class SeedHit:
    concept_id: str
    display_name: str
    concept_class: str | None
    datatype: str | None
    retired: bool
    answer_count: int
    set_member_count: int
    score: float
    role_hint: str | None = None
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conceptId": self.concept_id,
            "displayName": self.display_name,
            "conceptClass": self.concept_class,
            "datatype": self.datatype,
            "retired": self.retired,
            "answerCount": self.answer_count,
            "setMemberCount": self.set_member_count,
            "score": self.score,
            "roleHint": self.role_hint,
            "rationale": self.rationale,
        }


class CielClient:
    """Adapter around the canonical CIEL search package.

    Initialisation is lazy: the upstream package is imported on first use so
    the TenaAgent service can construct a `CielClient` even when the CIEL repo is
    not yet mounted. All public methods raise `CielUnavailableError` if no
    backend is reachable.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._service: Any | None = None
        self._sqlite_service: Any | None = None
        self._sqlite_path = settings.ciel_sqlite_path
        self._available_error: str | None = None

    def is_available(self) -> bool:
        try:
            self._ensure_service()
            return True
        except CielUnavailableError as exc:
            self._available_error = str(exc)
            return False

    def availability_detail(self) -> dict[str, Any]:
        available = self.is_available()
        return {
            "available": available,
            "sqlitePath": str(self._sqlite_path),
            "kbCielUrl": self.settings.kb_ciel_url,
            "error": None if available else self._available_error,
        }

    def search_form_seeds(
        self,
        query: str,
        *,
        concept_classes: list[str] | None = None,
        datatypes: list[str] | None = None,
        limit: int = 10,
        seed_limit: int = 5,
    ) -> list[SeedHit]:
        service = self._ensure_service()
        if service is _DirectSqliteSentinel:
            return _direct_sqlite_search_seeds(self._sqlite_path, query, concept_classes, datatypes, limit, seed_limit)
        ConceptSearchFilters = self._import("ciel_search", "ConceptSearchFilters")
        filters = ConceptSearchFilters(
            concept_classes=concept_classes,
            datatypes=datatypes,
            include_retired=False,
        )
        result = service.search_form_seeds(query, filters, limit=limit, seed_limit=seed_limit, expansion_depth=2)
        if not result.recommended_seeds and service is not self._sqlite_only_service():
            # Semantic discovery returned nothing (kb-ciel down/empty) -> FTS5.
            result = self._sqlite_only_service().search_form_seeds(
                query, filters, limit=limit, seed_limit=seed_limit, expansion_depth=2
            )
        seeds: list[SeedHit] = []
        for recommendation in result.recommended_seeds:
            hit = recommendation.hit
            seeds.append(
                SeedHit(
                    concept_id=str(hit.concept_id),
                    display_name=hit.display_name,
                    concept_class=hit.concept_class,
                    datatype=hit.datatype,
                    retired=bool(hit.retired),
                    answer_count=int(hit.answer_count or 0),
                    set_member_count=int(hit.set_member_count or 0),
                    score=float(hit.score or 0.0),
                    role_hint="section" if hit.set_member_count else "field",
                    rationale=list(recommendation.rationale),
                )
            )
        return seeds

    def search_concepts(
        self,
        query: str,
        *,
        concept_classes: list[str] | None = None,
        datatypes: list[str] | None = None,
        limit: int = 10,
    ) -> list[SeedHit]:
        service = self._ensure_service()
        if service is _DirectSqliteSentinel:
            return _direct_sqlite_search_concepts(self._sqlite_path, query, concept_classes, datatypes, limit)
        ConceptSearchFilters = self._import("ciel_search", "ConceptSearchFilters")
        filters = ConceptSearchFilters(
            concept_classes=concept_classes,
            datatypes=datatypes,
            include_retired=False,
        )
        hits = service.search_concepts(query, filters, limit=limit)
        if not hits and service is not self._sqlite_only_service():
            hits = self._sqlite_only_service().search_concepts(query, filters, limit=limit)
        return [
            SeedHit(
                concept_id=str(hit.concept_id),
                display_name=hit.display_name,
                concept_class=hit.concept_class,
                datatype=hit.datatype,
                retired=bool(hit.retired),
                answer_count=int(hit.answer_count or 0),
                set_member_count=int(hit.set_member_count or 0),
                score=float(hit.score or 0.0),
                role_hint=None,
                rationale=[],
            )
            for hit in hits
        ]

    def get_concept_bundle(self, concept_id: str) -> dict[str, Any]:
        service = self._ensure_service()
        if service is _DirectSqliteSentinel:
            return _direct_sqlite_get_bundle(self._sqlite_path, str(concept_id))
        try:
            return service.get_concept_bundle(str(concept_id))
        except KeyError as exc:
            raise ConceptNotFoundError(str(exc)) from exc

    def expand_seed(self, concept_id: str, *, depth: int = 3, allow_retired: bool = False) -> dict[str, Any]:
        """Expand a concept into its answers, set members, and BFS form candidates.

        Honors ``depth`` by delegating to ``CielSearchService.expand_seed`` (which
        walks Q-AND-A / CONCEPT-SET edges breadth-first up to ``expansion_depth``)
        rather than returning only the directly-attached relations. Falls back to
        a direct depth-1 read when only the raw SQLite store is available.
        """
        service = self._ensure_service()
        if service is _DirectSqliteSentinel:
            return self._expand_seed_direct(concept_id, depth=depth, allow_retired=allow_retired)
        try:
            expanded = service.expand_seed(
                str(concept_id), allow_retired=allow_retired, expansion_depth=max(1, int(depth))
            )
        except KeyError as exc:
            raise ConceptNotFoundError(str(concept_id)) from exc
        except ValueError as exc:
            # Service signals a retired concept via ValueError; normalize the type.
            raise RetiredConceptError(str(exc)) from exc
        return self._shape_expansion(expanded, depth=depth)

    def _shape_expansion(self, expanded: dict[str, Any], *, depth: int) -> dict[str, Any]:
        concept = expanded.get("concept", {})
        answers = expanded.get("answers", []) or []
        set_members = expanded.get("set_members", []) or []
        candidates = expanded.get("component_candidates", []) or []
        return {
            "concept": concept,
            "answers": [
                {
                    "conceptId": str(rel.get("target", {}).get("concept_id", "")),
                    "displayName": rel.get("target", {}).get("display_name", ""),
                    "retired": bool(rel.get("target", {}).get("retired")),
                }
                for rel in answers
            ],
            "setMembers": [
                {
                    "conceptId": str(rel.get("target", {}).get("concept_id", "")),
                    "displayName": rel.get("target", {}).get("display_name", ""),
                    "conceptClass": rel.get("target", {}).get("concept_class"),
                    "datatype": rel.get("target", {}).get("datatype"),
                    "answerCount": int(rel.get("target", {}).get("answer_count", 0) or 0),
                    "setMemberCount": int(rel.get("target", {}).get("set_member_count", 0) or 0),
                    "retired": bool(rel.get("target", {}).get("retired")),
                }
                for rel in set_members
            ],
            "componentCandidates": [
                {
                    "conceptId": str(c.get("concept_id", "")),
                    "displayName": c.get("display_name", ""),
                    "conceptClass": c.get("concept_class"),
                    "datatype": c.get("datatype"),
                    "answerCount": int(c.get("answer_count", 0) or 0),
                    "setMemberCount": int(c.get("set_member_count", 0) or 0),
                    "retired": bool(c.get("retired")),
                    "role": c.get("role"),
                    "path": c.get("path") or [],
                }
                for c in candidates
            ],
            "depth": depth,
        }

    def _expand_seed_direct(self, concept_id: str, *, depth: int, allow_retired: bool) -> dict[str, Any]:
        bundle = self.get_concept_bundle(concept_id)
        concept = bundle.get("concept", {})
        if concept.get("retired") and not allow_retired:
            raise RetiredConceptError(f"Concept {concept_id} is retired and is not allowed for new form construction.")
        return self._shape_expansion(
            {
                "concept": concept,
                "answers": bundle.get("answers", []),
                "set_members": bundle.get("set_members", []),
                "component_candidates": [],
            },
            depth=depth,
        )

    def _ensure_service(self) -> Any:
        if self._service is not None:
            return self._service
        # TenaOS-CIEL/ contains ciel_search/ directly. Inject the parent so the
        # package is importable as `ciel_search`.
        repo_root = self.settings.ciel_repo_root
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        try:
            from ciel_search import CielSearchService  # type: ignore
        except Exception as exc:
            if not self._sqlite_path.exists():
                raise CielUnavailableError(
                    f"Neither the ciel_search package nor a SQLite store at {self._sqlite_path} is available: {exc}"
                ) from exc
            self._service = _DirectSqliteSentinel
            return self._service
        if not self._sqlite_path.exists():
            raise CielUnavailableError(
                f"CIEL sqlite store not found at {self._sqlite_path}; build it via ciel_search build_sqlite_store."
            )
        qdrant_search = self._maybe_build_qdrant_search()
        self._service = CielSearchService(self._sqlite_path, qdrant_search=qdrant_search)
        if qdrant_search is None:
            # No semantic layer: the primary service already is SQLite-only, so
            # reuse it as the fallback to avoid a redundant second search.
            self._sqlite_service = self._service
        return self._service

    def _maybe_build_qdrant_search(self) -> Callable[..., list[tuple[str, float]]] | None:
        """Build a semantic-discovery callable backed by the kb-ciel HTTP service.

        Returns ``(query, filters, limit) -> [(concept_id, score)]`` that the
        ``CielSearchService`` routes plain-language queries through; the service
        then hydrates exact bundles from the local SQLite store. This realizes
        the intended two-stage flow: semantic discovery (kb-ciel) -> exact code
        resolution (SQLite). When the service is disabled or unreachable the
        callable returns an empty list so the caller transparently falls back to
        SQLite FTS5 search.
        """
        if not getattr(self.settings, "ciel_semantic_search", True):
            return None
        base_url = (self.settings.kb_ciel_url or "").strip()
        if not base_url:
            return None
        try:
            from .tool_loop import KbCielClient
        except Exception as exc:  # pragma: no cover - import guard
            _LOGGER.info("kb-ciel client unavailable; using SQLite FTS5: %s", exc)
            return None
        client = KbCielClient(base_url=base_url)

        def _search(query: str, filters: Any, limit: int) -> list[tuple[str, float]]:
            try:
                hits = client.search(
                    query,
                    k=int(limit),
                    concept_classes=getattr(filters, "concept_classes", None),
                    datatypes=getattr(filters, "datatypes", None),
                    include_retired=bool(getattr(filters, "include_retired", False)),
                )
            except Exception as exc:
                _LOGGER.warning("kb-ciel semantic search failed (query=%r); falling back: %s", query, exc)
                return []
            out: list[tuple[str, float]] = []
            for hit in hits or []:
                cid = str(hit.get("concept_id") or "").strip()
                if cid:
                    out.append((cid, float(hit.get("score") or 0.0)))
            return out

        return _search

    def _sqlite_only_service(self) -> Any:
        """A CIEL service that always uses SQLite FTS5 (semantic fallback path)."""
        cached = getattr(self, "_sqlite_service", None)
        if cached is not None:
            return cached
        from ciel_search import CielSearchService  # type: ignore

        service = CielSearchService(self._sqlite_path, qdrant_search=None)
        self._sqlite_service = service
        return service

    def _import(self, module_name: str, attribute: str) -> Any:
        module = __import__(module_name)
        return getattr(module, attribute)


class CielUnavailableError(RuntimeError):
    pass


class ConceptNotFoundError(KeyError):
    pass


class RetiredConceptError(ValueError):
    pass


_DirectSqliteSentinel = object()


# ---------------------------------------------------------------------------
# Direct SQLite fallback: used when the canonical ciel_search package cannot
# be imported but the SQLite store is present. Implements just enough to keep
# the form builder functional with FTS5 + LIKE search and bundle lookup.


def _direct_sqlite_get_bundle(sqlite_path: Path, concept_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT bundle_json FROM concept_bundles WHERE concept_id = ?",
            (concept_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["bundle_json"]:
        raise ConceptNotFoundError(f"Concept {concept_id} not found in {sqlite_path}")
    return json.loads(row["bundle_json"])


def _direct_sqlite_search_concepts(
    sqlite_path: Path,
    query: str,
    concept_classes: list[str] | None,
    datatypes: list[str] | None,
    limit: int,
) -> list[SeedHit]:
    rows = _direct_sqlite_search_rows(sqlite_path, query, concept_classes, datatypes, limit)
    return [_row_to_seed(row) for row in rows]


def _direct_sqlite_search_seeds(
    sqlite_path: Path,
    query: str,
    concept_classes: list[str] | None,
    datatypes: list[str] | None,
    limit: int,
    seed_limit: int,
) -> list[SeedHit]:
    candidates = _direct_sqlite_search_concepts(sqlite_path, query, concept_classes, datatypes, limit)
    ranked = sorted(candidates, key=_seed_priority, reverse=True)
    seen: set[str] = set()
    seeds: list[SeedHit] = []
    for hit in ranked:
        if hit.concept_id in seen or hit.retired:
            continue
        seen.add(hit.concept_id)
        role_hint = "section" if hit.set_member_count else ("field" if hit.answer_count or hit.datatype else None)
        seeds.append(
            SeedHit(
                concept_id=hit.concept_id,
                display_name=hit.display_name,
                concept_class=hit.concept_class,
                datatype=hit.datatype,
                retired=hit.retired,
                answer_count=hit.answer_count,
                set_member_count=hit.set_member_count,
                score=hit.score,
                role_hint=role_hint,
                rationale=_seed_rationale(hit),
            )
        )
        if len(seeds) >= seed_limit:
            break
    return seeds


def _direct_sqlite_search_rows(
    sqlite_path: Path,
    query: str,
    concept_classes: list[str] | None,
    datatypes: list[str] | None,
    limit: int,
) -> list[sqlite3.Row]:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        conditions: list[str] = ["cb.retired = 0"]
        params: list[Any] = []
        if concept_classes:
            placeholders = ",".join("?" for _ in concept_classes)
            conditions.append(f"cb.concept_class IN ({placeholders})")
            params.extend(concept_classes)
        if datatypes:
            placeholders = ",".join("?" for _ in datatypes)
            conditions.append(f"cb.datatype IN ({placeholders})")
            params.extend(datatypes)
        select_columns = (
            "cb.concept_id, cb.display_name, cb.concept_class, cb.datatype, cb.retired, "
            "cb.answer_count, cb.set_member_count"
        )
        rows: list[sqlite3.Row] = []
        try:
            fts_conditions = conditions + ["fts.search_text MATCH ?"]
            rows = conn.execute(
                f"""
                SELECT {select_columns}, bm25(fts) AS score
                FROM concept_search_fts AS fts
                JOIN concept_bundles AS cb ON cb.concept_id = fts.concept_id
                WHERE {' AND '.join(fts_conditions)}
                ORDER BY score
                LIMIT ?
                """,
                (*params, query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if len(rows) < limit:
            like_conditions = conditions + ["LOWER(cb.search_text) LIKE ?"]
            like_query = f"%{query.lower()}%"
            like_rows = conn.execute(
                f"""
                SELECT {select_columns}, 0.0 AS score
                FROM concept_bundles AS cb
                WHERE {' AND '.join(like_conditions)}
                ORDER BY cb.display_name
                LIMIT ?
                """,
                (*params, like_query, limit),
            ).fetchall()
            existing = {row["concept_id"] for row in rows}
            for row in like_rows:
                if row["concept_id"] in existing:
                    continue
                rows.append(row)
                if len(rows) >= limit:
                    break
        return rows
    finally:
        conn.close()


def _row_to_seed(row: sqlite3.Row) -> SeedHit:
    return SeedHit(
        concept_id=str(row["concept_id"]),
        display_name=row["display_name"] or "",
        concept_class=row["concept_class"],
        datatype=row["datatype"],
        retired=bool(row["retired"]),
        answer_count=int(row["answer_count"] or 0),
        set_member_count=int(row["set_member_count"] or 0),
        score=float(row["score"] or 0.0),
    )


def _seed_priority(hit: SeedHit) -> int:
    priority = 0
    if hit.set_member_count > 0:
        priority += 6
    if hit.answer_count > 0:
        priority += 5
    if hit.concept_class in {"ConvSet", "LabSet", "MedSet"}:
        priority += 4
    if hit.concept_class in {"Question", "Finding", "Test", "Obs"}:
        priority += 2
    if hit.datatype in {"Coded", "Numeric", "Text", "Boolean", "Date", "Datetime"}:
        priority += 2
    if hit.concept_class == "Diagnosis":
        priority -= 5
    if hit.concept_class == "Drug":
        priority -= 4
    if hit.retired:
        priority -= 10
    return priority


def _seed_rationale(hit: SeedHit) -> list[str]:
    rationale: list[str] = []
    if hit.set_member_count:
        rationale.append(f"set with {hit.set_member_count} members")
    if hit.answer_count:
        rationale.append(f"coded concept with {hit.answer_count} answers")
    if hit.concept_class:
        rationale.append(f"class={hit.concept_class}")
    if hit.datatype:
        rationale.append(f"datatype={hit.datatype}")
    return rationale


__all__ = [
    "CielClient",
    "CielUnavailableError",
    "ConceptNotFoundError",
    "RetiredConceptError",
    "SeedHit",
    "openmrs_uuid_for_concept_id",
]
