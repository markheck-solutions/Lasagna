"""INCA_SRC discovery data models."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_src_discovery_context import *  # noqa: F403


@dataclass(frozen=True)
class ColumnProfile:
    database: str
    schema: str
    object_name: str
    object_type: str
    column_name: str
    ordinal_position: int
    data_type: str
    numeric_scale: int | None
    is_nullable: str
    dependency_signal: str = ""


@dataclass(frozen=True)
class Searchability:
    searchable_status: str
    exact_predicate_supported: bool
    count_query_status: str
    sample_distinct_count_status: str = "NOT_RUN"
    notes: str = ""


@dataclass(frozen=True)
class StructuredIdClassification:
    id_domain: str
    feasibility_status: str
    inclusion_rule: str
    exclusion_rule: str
    searchable_status: str
    exact_predicate_supported: bool
    count_query_status: str
    sample_distinct_count_status: str
    notes: str


@dataclass(frozen=True)
class IdNode:
    database: str
    schema: str
    id_domain: str
    value: str

    @property
    def key(self) -> str:
        return f"{self.database}.{self.schema}|{self.id_domain}|{self.value}"


@dataclass(frozen=True)
class EvidenceRow:
    source_object: str
    source_row_hash: str
    node_keys: tuple[str, ...]
    connected_columns: tuple[str, ...]
    semantics_registry_key: str = ""
    semantics_status: str = "UNKNOWN"
    edge_type: str = "UNKNOWN"
    query_id: str = ""
    pass_number: int = 0


@dataclass(frozen=True)
class EvidenceEdge:
    edge_hash: str
    source_object: str
    source_row_hash: str
    connected_node_keys: tuple[str, ...]
    connected_columns: tuple[str, ...]
    semantics_registry_key: str
    semantics_status: str
    edge_type: str


@dataclass(frozen=True)
class IncompleteArea:
    object_name: str
    column_name: str
    id_node_key: str
    expected_row_count: int
    fetched_row_count: int
    page_size: int
    attempted_mitigations: tuple[str, ...]
    stop_reason: str
    resume_checkpoint: str


@dataclass(frozen=True)
class GraphClosureResult:
    fixed_point_reached: bool
    pass_count: int
    seed_node_count: int
    final_node_count: int
    evidence_row_count: int
    edge_count: int
    new_nodes_by_pass: tuple[int, ...]
    new_edges_by_pass: tuple[int, ...]
    visited_predicate_count: int
    incomplete_areas: tuple[IncompleteArea, ...]
    accepted_path_count: int
    rejected_path_count: int
    unknown_semantics_path_count: int


@dataclass(frozen=True)
class SemanticRegistryRow:
    registry_key: str
    source_object: str
    source_columns: tuple[str, ...]
    edge_type: str
    connected_id_types: tuple[str, ...]
    required_columns: tuple[str, ...]
    allowed_cardinality: str
    semantics_status: str
    may_prove_route_continuity: bool
    may_prove_tm_client_line_relation: bool
    evidence_basis: str
    evidence_artifact: str
    reviewer: str
    approval_status: str
    approved_at: str
    invalidation_rule: str
    notes: str


@dataclass(frozen=True)
class GoldenBlockerCase:
    case_id: str
    service_id: str
    blocker_type: str
    expected_status: str
    required_case_type: str
    availability_status: str
    searched_sources: tuple[str, ...]
    unavailable_reason: str
    regression_impact: str
    owner_confirmation_required: bool
    evidence_artifacts: tuple[str, ...]


@dataclass(frozen=True)
class StatusEntry:
    status: str
    reason: str
    evidence: tuple[str, ...]
