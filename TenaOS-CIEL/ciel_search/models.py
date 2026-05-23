from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


INTERNAL_RELATIONSHIP_TYPES = {"Q-AND-A", "CONCEPT-SET"}
DEFAULT_SQLITE_PATH = "ciel_search.sqlite3"
DEFAULT_COLLECTION_NAME = "ciel_concepts"
DEFAULT_DENSE_VECTOR_NAME = "sapbert"
DEFAULT_SPARSE_VECTOR_NAME = "bm25"
DEFAULT_SAPBERT_MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
MAX_SEARCH_SYNONYMS = 8
MAX_SEARCH_DESCRIPTIONS = 3
MAX_RELATIONSHIP_LABELS = 12


@dataclass(slots=True)
class BuildStats:
    export_path: str
    sqlite_path: str
    source_version: str | None
    concept_count: int
    retired_concept_count: int
    mapping_count: int
    qanda_edges: int
    concept_set_edges: int


@dataclass(slots=True)
class ConceptSearchFilters:
    concept_classes: list[str] | None = None
    datatypes: list[str] | None = None
    locales: list[str] | None = None
    include_retired: bool = False
    is_set: bool | None = None


@dataclass(slots=True)
class SearchHit:
    concept_id: str
    score: float
    bundle_ref: str
    display_name: str
    concept_class: str | None
    datatype: str | None
    retired: bool
    locales: list[str]
    answer_count: int
    set_member_count: int
    external_map_sources: list[str]


@dataclass(slots=True)
class FormComponentCandidate:
    concept_id: str
    bundle_ref: str
    display_name: str
    concept_class: str | None
    datatype: str | None
    retired: bool
    locales: list[str]
    answer_count: int
    set_member_count: int
    role: str
    source_seed_id: str
    path: list[str]
    option_preview: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SeedRecommendation:
    hit: SearchHit
    heuristic_score: int
    rationale: list[str]
    preview_component_count: int


@dataclass(slots=True)
class SeedSearchResult:
    query: str
    hits: list[SearchHit]
    recommended_seeds: list[SeedRecommendation]


@dataclass(slots=True)
class FormSearchResult:
    query: str
    hits: list[SearchHit]
    selected_seed_ids: list[str]
    components: list[FormComponentCandidate]


@dataclass(slots=True)
class ConceptBundle:
    concept: JsonDict
    names: list[JsonDict]
    descriptions: list[JsonDict]
    answers: list[JsonDict]
    set_members: list[JsonDict]
    external_mappings: list[JsonDict]
    incoming_relationships: list[JsonDict]
    search_text: str
    source_version: str | None = None
    unresolved_relationship_count: int = 0
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "source_version": self.source_version,
            "metadata": self.metadata,
            "concept": self.concept,
            "names": self.names,
            "descriptions": self.descriptions,
            "answers": self.answers,
            "set_members": self.set_members,
            "external_mappings": self.external_mappings,
            "incoming_relationships": self.incoming_relationships,
            "search_text": self.search_text,
            "unresolved_relationship_count": self.unresolved_relationship_count,
        }
