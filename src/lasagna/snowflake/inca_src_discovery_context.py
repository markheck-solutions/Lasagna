"""Deterministic INCA_SRC evidence discovery primitives.

This module intentionally does not change route sorting behavior. It provides
repeatable schema discovery, ID classification, evidence-graph closure, and
artifact status derivation for read-only Snowflake investigations.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

DEFAULT_DATABASE = "PROD_ACCESS_DB"
DEFAULT_SCHEMA = "INCA_SRC"

PASS = "PASS"
FAIL = "FAIL"
INCOMPLETE = "INCOMPLETE"
NOT_REQUIRED = "NOT_REQUIRED"
NOT_RUN = "NOT_RUN"
NOT_STARTED = "NOT_STARTED"

STATUS_NAMES = (
    "INCA_SRC schema discovery",
    "Schema/profile catalog",
    "Manifest-boundary avoidance",
    "Structured ID dictionary",
    "IC-388612 ID extraction",
    "Exact-ID overlap scan",
    "Evidence graph closure",
    "Edge semantics registry",
    "Candidate relation scan",
    "TM client-line relation proof",
    "Negative evidence ledger",
    "Schema drift invalidation",
    "Golden blocker corpus",
    "Golden blocker regression",
    "Sorter implementation change",
    "IC-388612 route order proof",
    "Repo validation",
)

VIEW_REQUIRED_COLUMNS = (
    "TABLE_CATALOG",
    "TABLE_SCHEMA",
    "TABLE_NAME",
)

VIEW_OPTIONAL_COLUMNS = (
    "TABLE_OWNER",
    "VIEW_DEFINITION",
    "CHECK_OPTION",
    "IS_UPDATABLE",
    "INSERTABLE_INTO",
    "IS_SECURE",
    "CREATED",
    "LAST_ALTERED",
    "LAST_DDL",
    "LAST_DDL_BY",
    "COMMENT",
)

STRUCTURED_ID_DICTIONARY_COLUMNS = (
    "run_id",
    "database",
    "schema",
    "object_name",
    "object_type",
    "column_name",
    "ordinal_position",
    "data_type",
    "numeric_scale",
    "is_nullable",
    "id_domain",
    "feasibility_status",
    "inclusion_rule",
    "exclusion_rule",
    "searchable_status",
    "exact_predicate_supported",
    "count_query_status",
    "sample_distinct_count_status",
    "dependency_signal",
    "notes",
)

EXACT_MATCH_HITS_COLUMNS = (
    "run_id",
    "pass_number",
    "object_name",
    "column_name",
    "id_domain",
    "id_value",
    "node_key",
    "match_count",
    "fetched_count",
    "row_hash",
    "matched_columns",
    "context_columns_present",
    "query_id",
    "predicate_sql_hash",
    "page_number",
    "truncated",
    "incomplete_reason",
)

EVIDENCE_EDGES_COLUMNS = (
    "run_id",
    "pass_number",
    "edge_hash",
    "source_object",
    "source_row_hash",
    "connected_node_keys",
    "connected_id_domains",
    "connected_columns",
    "relation_shape",
    "edge_type",
    "cardinality_observed",
    "semantics_registry_key",
    "semantics_status",
    "may_prove_route_continuity",
    "may_prove_tm_relation",
    "query_id",
    "evidence_basis",
    "incomplete_reason",
)

EDGE_SEMANTICS_REGISTRY_COLUMNS = (
    "registry_key",
    "source_object",
    "source_columns",
    "edge_type",
    "connected_id_types",
    "required_columns",
    "allowed_cardinality",
    "semantics_status",
    "may_prove_route_continuity",
    "may_prove_tm_client_line_relation",
    "evidence_basis",
    "evidence_artifact",
    "reviewer",
    "approval_status",
    "approved_at",
    "invalidation_rule",
    "notes",
)

COVERAGE_MATRIX_COLUMNS = (
    "run_id",
    "object_name",
    "column_name",
    "id_domain",
    "feasible",
    "searched",
    "counted",
    "fetched",
    "pass_numbers",
    "predicate_count",
    "rows_matched",
    "rows_fetched",
    "skipped",
    "skip_reason",
    "incomplete",
    "incomplete_reason",
    "query_ids",
    "checkpoint_path",
)

SKIPPED_OBJECTS_COLUMNS = (
    "run_id",
    "object_name",
    "object_type",
    "column_name",
    "skip_scope",
    "skip_reason_code",
    "skip_reason_detail",
    "required_for_full_discovery",
    "causes_incomplete",
    "mitigation_attempted",
    "next_action",
)

CONTEXT_ONLY_COLUMNS = frozenset(
    {
        "NE",
        "NE_PART",
        "NE_PART_NAME",
        "SITE_CODE",
        "SLOT",
        "SUBSLOT",
        "CONNECTION_POINT_NR",
        "CONNECTION_POINT_NAME",
        "PORT_NAME",
        "DISPLAY_PORT_NAME",
        "ROUTE_NAME",
    }
)

EXCLUDED_NAME_TOKENS = (
    "CREATED_BY",
    "UPDATED_BY",
    "LOAD_ID",
    "BATCH_ID",
    "JOB_ID",
    "RUN_ID",
    "ROW_ID",
    "HASH_ID",
    "AUDIT_ID",
)

DATE_STATUS_USER_AUDIT_TOKENS = (
    "DATE",
    "TIME",
    "TIMESTAMP",
    "STATUS",
    "USER",
    "USERNAME",
    "OWNER",
    "AUDIT",
    "COMMENT",
    "DESCRIPTION",
)

RELATION_DOMAIN_TERMS = (
    "CONN",
    "CONTENT",
    "CABPT",
    "TRAIL",
    "FACILITY",
    "WAVELENGTH",
    "WAVE",
    "CHANNEL",
    "LAMBDA",
    "ADAPT",
    "PORT",
    "LINK",
    "PATH",
)

TEXT_TYPES = frozenset({"VARCHAR", "TEXT", "CHAR", "STRING"})
INTEGER_TYPES = frozenset({"INTEGER", "BIGINT", "INT", "NUMBER", "DECIMAL", "NUMERIC"})
REJECTED_TYPES = frozenset({"VARIANT", "OBJECT", "ARRAY", "BINARY", "GEOGRAPHY", "GEOMETRY"})

EXACT_DOMAIN_BY_COLUMN = {
    "CONTENT_INT_ID": "CONTENT_INT_ID",
    "CONNPT_INT_ID": "CONNPT_INT_ID",
    "CONN_POINT_INT_ID": "CONN_POINT_INT_ID",
    "CABPT_INT_ID": "CABPT_INT_ID",
}

DOMAIN_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r".*TRAIL.*(_ID|_INT_ID)$", re.IGNORECASE), "TRAIL_ID", "domain_trail"),
    (
        re.compile(r".*FACILITY.*(_ID|_INT_ID)$", re.IGNORECASE),
        "FACILITY_ID",
        "domain_facility",
    ),
    (
        re.compile(r".*(WAVELENGTH|WAVE|LAMBDA).*(_ID|_INT_ID)$", re.IGNORECASE),
        "WAVELENGTH_ID",
        "domain_wavelength",
    ),
    (re.compile(r".*CHANNEL.*(_ID|_INT_ID)$", re.IGNORECASE), "CHANNEL_ID", "domain_channel"),
    (
        re.compile(r".*(ADAPTATION|ADAPT).*(_ID|_INT_ID)$", re.IGNORECASE),
        "ADAPTATION_ID",
        "domain_adaptation",
    ),
)

PAIR_ROLE_PATTERN = re.compile(
    r"^(PARENT|CHILD|A|Z|FROM|TO|SOURCE|TARGET|SRC|DST|DEST).*(_ID|_INT_ID)$",
    re.IGNORECASE,
)
INT_ID_SUFFIX_PATTERN = re.compile(r".*_INT_ID$", re.IGNORECASE)

APPROVED_SEMANTICS = frozenset({"PROVEN", "OWNER_CONFIRMED"})
BLOCKING_SEMANTICS = frozenset({"UNKNOWN", "REJECTED"})
REQUIRED_CASE_TYPES = (
    "KNOWN_SORTING_ROUTE_FAMILY",
    "KNOWN_CIENA_G30_G40_ACCEPTED",
    "KNOWN_OTM_TM_FAIL_CLOSED",
    "KNOWN_DTN_FAIL_CLOSED",
    "IC_388612",
    "FUTURE_OWNER_CONFIRMED_TM_PASS",
)

# ruff: noqa: F401,I001
