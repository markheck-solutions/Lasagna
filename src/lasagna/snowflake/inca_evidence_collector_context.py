"""Collect sanitized INCA_SRC evidence artifacts with read-only Snowflake queries."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import snowflake.connector

from lasagna.snowflake.inca_src_discovery import (
    COVERAGE_MATRIX_COLUMNS,
    DEFAULT_DATABASE,
    DEFAULT_SCHEMA,
    EDGE_SEMANTICS_REGISTRY_COLUMNS,
    EVIDENCE_EDGES_COLUMNS,
    EXACT_MATCH_HITS_COLUMNS,
    FAIL,
    INCOMPLETE,
    PASS,
    SKIPPED_OBJECTS_COLUMNS,
    STRUCTURED_ID_DICTIONARY_COLUMNS,
    TEXT_TYPES,
    ColumnProfile,
    EvidenceRow,
    GraphClosureResult,
    IdNode,
    IncompleteArea,
    Searchability,
    build_count_sql,
    build_structured_id_dictionary_rows,
    build_views_metadata_query,
    canonical_data_type,
    classify_structured_id_column,
    close_evidence_graph,
    data_type_is_rejected,
    evidence_edge_hash,
    fanout_incomplete_area,
    graph_closure_summary_payload,
    initial_semantics_registry_row,
    negative_evidence_ledger_entry,
    node_from_value,
    object_type_map,
    profiles_from_information_schema_columns,
    qualified_object,
    quote_identifier,
    render_command_log,
    required_metadata_queries,
    row_hash_expression,
    schema_drift_report,
    stable_hash,
    stable_json_hash,
    status_split_template,
    view_metadata_gap_rows,
    write_csv_artifact,
    write_json_artifact,
)

DEFAULT_OUTPUT_ROOT = Path.home() / "Desktop" / "LasagnaRouteReviews" / "inca-src-discovery"
DEFAULT_INTERNAL_DEADLINE_SECONDS = 1500
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 120
ARTIFACT_SCHEMA_VERSION = "inca-src-evidence-v1"
DEFAULT_REPO_ROOT = Path(r"C:\repos\Lasagna")
DOC_SNAPSHOT_FILES = {
    "FRAMEWORK_RUNBOOK_SNAPSHOT.md": "docs/runbooks/INCA_SRC_EVIDENCE_FRAMEWORK.md",
    "STATUS_CONTRACT_SNAPSHOT.md": "docs/contracts/INCA_SRC_EVIDENCE_STATUS_CONTRACT.md",
    "AI_HANDOFF_SNAPSHOT.md": "docs/ai_handoffs/INCA_SRC_EVIDENCE_HANDOFF.md",
}
HARD_CONSTRAINTS = {
    "rag_proof_allowed": False,
    "embedding_ranked_proof_allowed": False,
    "context_only_field_proof_allowed": False,
    "sorter_changes_allowed": False,
    "port_match_rule_changes_allowed": False,
    "edge_semantics_self_approval_allowed": False,
    "negative_evidence_requires_full_fixed_point": True,
}
PHASES = (
    "initialize_run",
    "discover_schema_objects",
    "discover_schema_columns",
    "discover_views_metadata",
    "discover_dependencies_optional",
    "write_schema_profile",
    "build_structured_id_dictionary",
    "extract_service_seed_ids",
    "run_exact_id_overlap_scan",
    "run_graph_closure",
    "write_final_status",
)
METADATA_GAPS_COLUMNS = (
    "metadata_object",
    "gap_scope",
    "missing_column",
    "required",
    "causes_incomplete",
    "reason",
)
QUERY_LOG_COLUMNS = (
    "run_id",
    "phase",
    "logical_query_name",
    "sql_hash",
    "query_id",
    "started_at",
    "completed_at",
    "elapsed_ms",
    "row_count",
    "status",
    "error",
    "timeout_reason",
)
PHASE_LOG_COLUMNS = (
    "run_id",
    "phase",
    "started_at",
    "completed_at",
    "status",
    "artifact_paths",
    "reason",
)


@dataclass(frozen=True)
class LiveConfig:
    run_id: str
    service_id: str
    database: str
    schema: str
    page_size: int
    max_pages_per_predicate: int
    phase_mode: str
    seed_mode: str
    route_seed_id_bag: Path | None
    internal_deadline_seconds: int
    statement_timeout_seconds: int
    framework_commit_sha: str
    repo_root: Path


@dataclass(frozen=True)
class QueryRows:
    rows: list[dict[str, object]]
    query_id: str


@dataclass(frozen=True)
class SeedScanResult:
    seed_nodes: dict[str, IdNode]
    seed_rows: list[dict[str, object]]
    searched_anchor_columns: int
    exact_anchor_hits: int
    incomplete_areas: list[IncompleteArea]
    skipped_rows: list[dict[str, object]]


@dataclass(frozen=True)
class GraphScanResult:
    evidence_rows: list[EvidenceRow]
    exact_hits: list[dict[str, object]]
    coverage_rows: list[dict[str, object]]
    incomplete_areas: list[IncompleteArea]
    skipped_rows: list[dict[str, object]]


@dataclass
class RunState:
    run_dir: Path
    config: LiveConfig
    started_at: str
    deadline_started_monotonic: float
    status_split: dict[str, object]
    run_manifest: dict[str, object]
    metadata: dict[str, list[dict[str, object]]]
    profiles: list[ColumnProfile]
    proof_by_object: dict[str, list[ColumnProfile]]
    dictionary_rows: list[dict[str, object]]
    candidates: list[dict[str, object]]
    seed_scan: SeedScanResult
    graph_scan: GraphScanResult
    closure: object | None = None


class CollectorIncompleteError(RuntimeError):
    """Raised when the collector must stop with durable INCOMPLETE artifacts."""


class InternalDeadlineExceededError(CollectorIncompleteError):
    """Raised before bridge timeout so artifacts can be finalized."""
