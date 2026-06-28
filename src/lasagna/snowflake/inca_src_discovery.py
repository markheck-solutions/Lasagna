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


def required_metadata_queries(
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
) -> dict[str, str]:
    quoted_database = quote_identifier(database)
    literal_schema = sql_literal(schema)
    return {
        "session_context": (
            "SELECT CURRENT_ACCOUNT(), CURRENT_REGION(), CURRENT_ROLE(), "
            "CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA(), "
            "CURRENT_USER(), CURRENT_TIMESTAMP()"
        ),
        "tables": (
            "SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, IS_TRANSIENT, "
            "ROW_COUNT, BYTES, CREATED, LAST_ALTERED "
            f"FROM {quoted_database}.INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = {literal_schema} "
            "ORDER BY TABLE_NAME"
        ),
        "columns": (
            "SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, "
            "DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, "
            "IS_NULLABLE, COLUMN_DEFAULT, COMMENT "
            f"FROM {quoted_database}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = {literal_schema} "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        ),
        "views_available_columns": (
            "SELECT COLUMN_NAME "
            f"FROM {quoted_database}.INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'INFORMATION_SCHEMA' "
            "AND TABLE_NAME = 'VIEWS' "
            "ORDER BY ORDINAL_POSITION"
        ),
        "dependencies": (
            "SELECT REFERENCING_DATABASE, REFERENCING_SCHEMA, REFERENCING_OBJECT_NAME, "
            "REFERENCING_OBJECT_DOMAIN, REFERENCED_DATABASE, REFERENCED_SCHEMA, "
            "REFERENCED_OBJECT_NAME, REFERENCED_OBJECT_DOMAIN "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
            f"WHERE (REFERENCING_DATABASE = {sql_literal(database)} "
            f"AND REFERENCING_SCHEMA = {literal_schema}) "
            f"OR (REFERENCED_DATABASE = {sql_literal(database)} "
            f"AND REFERENCED_SCHEMA = {literal_schema})"
        ),
    }


def build_views_metadata_query(
    database: str,
    schema: str,
    available_columns: Iterable[str],
) -> str:
    normalized = {column.upper() for column in available_columns}
    missing_required = [column for column in VIEW_REQUIRED_COLUMNS if column not in normalized]
    if missing_required:
        joined = ", ".join(missing_required)
        msg = f"INFORMATION_SCHEMA.VIEWS missing required columns: {joined}"
        raise RuntimeError(msg)
    selected = [
        column
        for column in (*VIEW_REQUIRED_COLUMNS, *VIEW_OPTIONAL_COLUMNS)
        if column in normalized
    ]
    return (
        f"SELECT {', '.join(selected)} "
        f"FROM {quote_identifier(database)}.INFORMATION_SCHEMA.VIEWS "
        f"WHERE TABLE_SCHEMA = {sql_literal(schema)} "
        "ORDER BY TABLE_NAME"
    )


def view_metadata_gap_rows(
    available_columns: Iterable[str],
    *,
    discovery_status: str = PASS,
    discovery_error: str = "",
) -> list[dict[str, object]]:
    normalized = {column.upper() for column in available_columns}
    rows: list[dict[str, object]] = []
    if discovery_status != PASS:
        rows.append(
            {
                "metadata_object": "INFORMATION_SCHEMA.VIEWS",
                "gap_scope": "COLUMN_DISCOVERY",
                "missing_column": "",
                "required": False,
                "causes_incomplete": False,
                "reason": discovery_error,
            }
        )
    for column in VIEW_OPTIONAL_COLUMNS:
        if column not in normalized:
            rows.append(
                {
                    "metadata_object": "INFORMATION_SCHEMA.VIEWS",
                    "gap_scope": "OPTIONAL_METADATA_COLUMN",
                    "missing_column": column,
                    "required": False,
                    "causes_incomplete": False,
                    "reason": "optional metadata column unavailable",
                }
            )
    return rows


def quote_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def sql_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_json_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return stable_hash(encoded)


def normalize_id_value(value: object, source_data_type: str) -> str:
    data_type = canonical_data_type(source_data_type)
    if value is None:
        msg = "ID node value cannot be null"
        raise ValueError(msg)
    if data_type in INTEGER_TYPES:
        return normalize_numeric_id(value)
    return str(value).strip()


def normalize_numeric_id(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("1")))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+(\.0+)?", text):
        msg = f"Numeric ID is not integer-like: {text}"
        raise ValueError(msg)
    return str(int(Decimal(text)))


def node_from_value(
    database: str,
    schema: str,
    column_name: str,
    value: object,
    source_data_type: str,
) -> IdNode:
    domain, _rule = domain_for_column(column_name)
    if domain == "":
        msg = f"Column is not a proof-grade ID column: {column_name}"
        raise ValueError(msg)
    return IdNode(
        database=database,
        schema=schema,
        id_domain=domain,
        value=normalize_id_value(value, source_data_type),
    )


def classify_structured_id_column(
    profile: ColumnProfile,
    searchability: Searchability | None = None,
) -> StructuredIdClassification:
    domain, inclusion_rule = domain_for_column(profile.column_name)
    exclusion = exclusion_rule(profile)
    searchable = searchability or Searchability("UNKNOWN", False, "NOT_RUN")
    if exclusion:
        return StructuredIdClassification(
            id_domain="",
            feasibility_status="EXCLUDED",
            inclusion_rule=inclusion_rule,
            exclusion_rule=exclusion,
            searchable_status=searchable.searchable_status,
            exact_predicate_supported=searchable.exact_predicate_supported,
            count_query_status=searchable.count_query_status,
            sample_distinct_count_status=searchable.sample_distinct_count_status,
            notes=searchable.notes,
        )
    if domain == "":
        return StructuredIdClassification(
            id_domain="",
            feasibility_status="EXCLUDED",
            inclusion_rule="NO_MATCH",
            exclusion_rule="NO_STRUCTURED_ID_NAME_MATCH",
            searchable_status=searchable.searchable_status,
            exact_predicate_supported=searchable.exact_predicate_supported,
            count_query_status=searchable.count_query_status,
            sample_distinct_count_status=searchable.sample_distinct_count_status,
            notes=searchable.notes,
        )
    return searchable_classification(domain, inclusion_rule, searchable)


def searchable_classification(
    domain: str,
    inclusion_rule: str,
    searchability: Searchability,
) -> StructuredIdClassification:
    if searchability.searchable_status == "SEARCHABLE":
        feasible = (
            searchability.exact_predicate_supported and searchability.count_query_status == PASS
        )
        status = "FEASIBLE" if feasible else "EXCLUDED"
        exclusion = "" if feasible else "NOT_SEARCHABLE"
    elif searchability.searchable_status == "UNKNOWN":
        status = "INCOMPLETE"
        exclusion = "SEARCHABILITY_NOT_PROVEN"
    else:
        status = "EXCLUDED"
        exclusion = "NOT_SEARCHABLE"
    return StructuredIdClassification(
        id_domain=domain,
        feasibility_status=status,
        inclusion_rule=inclusion_rule,
        exclusion_rule=exclusion,
        searchable_status=searchability.searchable_status,
        exact_predicate_supported=searchability.exact_predicate_supported,
        count_query_status=searchability.count_query_status,
        sample_distinct_count_status=searchability.sample_distinct_count_status,
        notes=searchability.notes,
    )


def exclusion_rule(profile: ColumnProfile) -> str:
    name = profile.column_name.upper()
    if is_context_only_name(name):
        return "CONTEXT_ONLY_FIELD"
    if data_type_is_rejected(profile.data_type):
        return "REJECTED_DATA_TYPE"
    if data_type_is_incompatible(profile.data_type, profile.numeric_scale):
        return "INCOMPATIBLE_DATA_TYPE"
    if is_date_status_user_audit_name(name):
        return "DATE_STATUS_USER_AUDIT_FIELD"
    if is_generated_metadata_name(name, profile.object_name):
        return "GENERATED_ONLY_METADATA_FIELD"
    return ""


def domain_for_column(column_name: str) -> tuple[str, str]:
    name = column_name.upper()
    if name in EXACT_DOMAIN_BY_COLUMN:
        return EXACT_DOMAIN_BY_COLUMN[name], f"EXACT:{name}"
    for pattern, domain, rule in DOMAIN_PATTERNS:
        if pattern.fullmatch(name):
            return domain, rule
    if PAIR_ROLE_PATTERN.fullmatch(name):
        return f"GENERIC_INT_ID:{name}", "PAIR_ROLE"
    if INT_ID_SUFFIX_PATTERN.fullmatch(name):
        return f"GENERIC_INT_ID:{name}", "SUFFIX_INT_ID"
    return "", "NO_MATCH"


def is_context_only_name(name: str) -> bool:
    upper = name.upper()
    if upper in CONTEXT_ONLY_COLUMNS:
        return True
    return any(token in upper for token in ("DISPLAY", "ROUTE_NAME", "PORT_NAME"))


def data_type_is_rejected(data_type: str) -> bool:
    return canonical_data_type(data_type) in REJECTED_TYPES


def data_type_is_incompatible(data_type: str, numeric_scale: int | None) -> bool:
    canonical = canonical_data_type(data_type)
    if canonical in TEXT_TYPES:
        return False
    if canonical not in INTEGER_TYPES:
        return True
    return numeric_scale not in (None, 0)


def canonical_data_type(data_type: str) -> str:
    return data_type.upper().split("(", maxsplit=1)[0].strip()


def is_generated_metadata_name(column_name: str, object_name: str) -> bool:
    upper_column = column_name.upper()
    if upper_column not in EXCLUDED_NAME_TOKENS:
        return False
    combined = f"{object_name}_{upper_column}".upper()
    return not any(term in combined for term in RELATION_DOMAIN_TERMS)


def is_date_status_user_audit_name(column_name: str) -> bool:
    upper_column = column_name.upper()
    if upper_column.endswith("_INT_ID") or upper_column in EXACT_DOMAIN_BY_COLUMN:
        return False
    return any(token in upper_column for token in DATE_STATUS_USER_AUDIT_TOKENS)


def build_structured_id_dictionary_rows(
    run_id: str,
    profiles: Iterable[ColumnProfile],
    searchability: Mapping[tuple[str, str], Searchability] | None = None,
) -> list[dict[str, object]]:
    lookup = searchability or {}
    rows: list[dict[str, object]] = []
    for profile in profiles:
        status = lookup.get((profile.object_name, profile.column_name))
        classification = classify_structured_id_column(profile, status)
        rows.append(
            {
                "run_id": run_id,
                "database": profile.database,
                "schema": profile.schema,
                "object_name": profile.object_name,
                "object_type": profile.object_type,
                "column_name": profile.column_name,
                "ordinal_position": profile.ordinal_position,
                "data_type": profile.data_type,
                "numeric_scale": profile.numeric_scale,
                "is_nullable": profile.is_nullable,
                "id_domain": classification.id_domain,
                "feasibility_status": classification.feasibility_status,
                "inclusion_rule": classification.inclusion_rule,
                "exclusion_rule": classification.exclusion_rule,
                "searchable_status": classification.searchable_status,
                "exact_predicate_supported": classification.exact_predicate_supported,
                "count_query_status": classification.count_query_status,
                "sample_distinct_count_status": classification.sample_distinct_count_status,
                "dependency_signal": profile.dependency_signal,
                "notes": classification.notes,
            }
        )
    return rows


def feasible_dictionary_rows(rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [row for row in rows if row.get("feasibility_status") == "FEASIBLE"]


def assert_full_inventory_before_candidate_classification(
    object_inventory_complete: bool,
    column_inventory_complete: bool,
) -> None:
    if not object_inventory_complete or not column_inventory_complete:
        msg = "Candidate classification requires complete object and column inventory"
        raise RuntimeError(msg)


def classify_candidate_relation_tables(
    dictionary_rows: Sequence[Mapping[str, object]],
    *,
    object_inventory_complete: bool,
    column_inventory_complete: bool,
) -> list[dict[str, object]]:
    assert_full_inventory_before_candidate_classification(
        object_inventory_complete,
        column_inventory_complete,
    )
    by_object: dict[str, list[Mapping[str, object]]] = {}
    for row in feasible_dictionary_rows(dictionary_rows):
        by_object.setdefault(str(row["object_name"]), []).append(row)
    candidates: list[dict[str, object]] = []
    for object_name, rows in sorted(by_object.items()):
        domains = sorted({str(row["id_domain"]) for row in rows})
        if len(domains) >= 2:
            candidates.append(
                {
                    "object_name": object_name,
                    "candidate_reason": "MULTIPLE_FEASIBLE_STRUCTURED_ID_DOMAINS",
                    "id_domains": "|".join(domains),
                    "feasible_column_count": len(rows),
                }
            )
    return candidates


def build_count_sql(
    database: str,
    schema: str,
    object_name: str,
    column_name: str,
    placeholders: int,
) -> str:
    return (
        "SELECT COUNT(*) AS MATCH_COUNT "
        f"FROM {qualified_object(database, schema, object_name)} "
        f"WHERE {quote_identifier(column_name)} IN ({placeholder_list(placeholders)})"
    )


def build_fetch_sql(
    database: str,
    schema: str,
    object_name: str,
    id_columns: Sequence[str],
    predicate_column: str,
    placeholders: int,
    page_size: int,
) -> str:
    selected_columns = ", ".join(quote_identifier(column) for column in sorted(set(id_columns)))
    row_hash_sql = row_hash_expression(id_columns)
    return (
        f"SELECT {selected_columns}, {row_hash_sql} AS ROW_HASH "
        f"FROM {qualified_object(database, schema, object_name)} "
        f"WHERE {quote_identifier(predicate_column)} IN ({placeholder_list(placeholders)}) "
        "ORDER BY ROW_HASH "
        f"LIMIT {int(page_size)}"
    )


def qualified_object(database: str, schema: str, object_name: str) -> str:
    return (
        f"{quote_identifier(database)}.{quote_identifier(schema)}.{quote_identifier(object_name)}"
    )


def placeholder_list(count: int) -> str:
    if count < 1:
        msg = "At least one exact ID predicate placeholder is required"
        raise ValueError(msg)
    return ", ".join(["%s"] * count)


def row_hash_expression(columns: Sequence[str]) -> str:
    if not columns:
        msg = "Stable row hash requires selected ID columns"
        raise ValueError(msg)
    parts = ", ".join(
        f"COALESCE(TO_VARCHAR({quote_identifier(column)}), '<NULL>')" for column in columns
    )
    return f"SHA2(CONCAT_WS('|', {parts}), 256)"


def exact_match_hit_row(
    run_id: str,
    pass_number: int,
    object_name: str,
    column_name: str,
    node: IdNode,
    match_count: int,
    fetched_count: int,
    row_hash: str,
    matched_columns: Sequence[str],
    query_id: str,
    predicate_sql: str,
    page_number: int,
    incomplete_reason: str = "",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "pass_number": pass_number,
        "object_name": object_name,
        "column_name": column_name,
        "id_domain": node.id_domain,
        "id_value": node.value,
        "node_key": node.key,
        "match_count": match_count,
        "fetched_count": fetched_count,
        "row_hash": row_hash,
        "matched_columns": "|".join(matched_columns),
        "context_columns_present": "",
        "query_id": query_id,
        "predicate_sql_hash": stable_hash(predicate_sql),
        "page_number": page_number,
        "truncated": bool(incomplete_reason),
        "incomplete_reason": incomplete_reason,
    }


def close_evidence_graph(
    seed_node_keys: Iterable[str],
    evidence_rows: Sequence[EvidenceRow],
    incomplete_areas: Sequence[IncompleteArea] = (),
) -> GraphClosureResult:
    known_nodes = set(seed_node_keys)
    seed_node_count = len(known_nodes)
    visited_rows: set[str] = set()
    edge_hashes: set[str] = set()
    new_nodes_by_pass: list[int] = []
    new_edges_by_pass: list[int] = []
    pass_count = 0
    while True:
        pass_count += 1
        new_nodes, new_edges = graph_closure_pass(
            evidence_rows, known_nodes, visited_rows, edge_hashes
        )
        new_nodes_by_pass.append(new_nodes)
        new_edges_by_pass.append(new_edges)
        if new_nodes == 0 and new_edges == 0:
            break
    accepted, rejected, unknown = count_path_semantics(evidence_rows, visited_rows)
    fixed_point = len(incomplete_areas) == 0
    return GraphClosureResult(
        fixed_point_reached=fixed_point,
        pass_count=pass_count,
        seed_node_count=seed_node_count,
        final_node_count=len(known_nodes),
        evidence_row_count=len(visited_rows),
        edge_count=len(edge_hashes),
        new_nodes_by_pass=tuple(new_nodes_by_pass),
        new_edges_by_pass=tuple(new_edges_by_pass),
        visited_predicate_count=len(visited_rows),
        incomplete_areas=tuple(incomplete_areas),
        accepted_path_count=accepted,
        rejected_path_count=rejected,
        unknown_semantics_path_count=unknown,
    )


def graph_closure_pass(
    evidence_rows: Sequence[EvidenceRow],
    known_nodes: set[str],
    visited_rows: set[str],
    edge_hashes: set[str],
) -> tuple[int, int]:
    new_nodes = 0
    new_edges = 0
    pass_start_nodes = set(known_nodes)
    for row in evidence_rows:
        row_nodes = set(row.node_keys)
        if row.source_row_hash in visited_rows or not row_nodes.intersection(pass_start_nodes):
            continue
        visited_rows.add(row.source_row_hash)
        before_nodes = len(known_nodes)
        known_nodes.update(row_nodes)
        new_nodes += len(known_nodes) - before_nodes
        edge_hash = evidence_edge_hash(row)
        if edge_hash not in edge_hashes:
            edge_hashes.add(edge_hash)
            new_edges += 1
    return new_nodes, new_edges


def evidence_edge_hash(row: EvidenceRow) -> str:
    basis = "|".join(
        [
            row.source_object,
            row.source_row_hash,
            "|".join(sorted(row.node_keys)),
            "|".join(sorted(row.connected_columns)),
        ]
    )
    return stable_hash(basis)


def count_path_semantics(
    evidence_rows: Sequence[EvidenceRow],
    visited_rows: set[str],
) -> tuple[int, int, int]:
    accepted = 0
    rejected = 0
    unknown = 0
    for row in evidence_rows:
        if row.source_row_hash not in visited_rows:
            continue
        if row.semantics_status in APPROVED_SEMANTICS:
            accepted += 1
        elif row.semantics_status == "REJECTED":
            rejected += 1
        else:
            unknown += 1
    return accepted, rejected, unknown


def graph_closure_summary_payload(
    run_id: str,
    service_id: str,
    started_at: str,
    completed_at: str,
    result: GraphClosureResult,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "service_id": service_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "fixed_point_reached": result.fixed_point_reached,
        "pass_count": result.pass_count,
        "seed_node_count": result.seed_node_count,
        "final_node_count": result.final_node_count,
        "evidence_row_count": result.evidence_row_count,
        "edge_count": result.edge_count,
        "new_nodes_by_pass": list(result.new_nodes_by_pass),
        "new_edges_by_pass": list(result.new_edges_by_pass),
        "visited_predicate_count": result.visited_predicate_count,
        "incomplete_area_count": len(result.incomplete_areas),
        "incomplete_areas": [asdict(area) for area in result.incomplete_areas],
        "accepted_path_count": result.accepted_path_count,
        "rejected_path_count": result.rejected_path_count,
        "unknown_semantics_path_count": result.unknown_semantics_path_count,
    }


def path_may_prove_tm_relation(rows: Sequence[SemanticRegistryRow]) -> bool:
    return all(
        row.semantics_status in APPROVED_SEMANTICS
        and row.approval_status == "APPROVED"
        and row.may_prove_tm_client_line_relation
        for row in rows
    )


def path_may_prove_route_continuity(rows: Sequence[SemanticRegistryRow]) -> bool:
    return all(
        row.semantics_status in APPROVED_SEMANTICS
        and row.approval_status == "APPROVED"
        and row.may_prove_route_continuity
        for row in rows
    )


def initial_semantics_registry_row(
    registry_key: str,
    source_object: str,
    source_columns: Sequence[str],
    connected_id_types: Sequence[str],
) -> SemanticRegistryRow:
    return SemanticRegistryRow(
        registry_key=registry_key,
        source_object=source_object,
        source_columns=tuple(source_columns),
        edge_type="UNKNOWN",
        connected_id_types=tuple(connected_id_types),
        required_columns=tuple(source_columns),
        allowed_cardinality="UNKNOWN",
        semantics_status="UNKNOWN",
        may_prove_route_continuity=False,
        may_prove_tm_client_line_relation=False,
        evidence_basis="",
        evidence_artifact="",
        reviewer="",
        approval_status="PENDING_REVIEW",
        approved_at="",
        invalidation_rule="invalidate on registry row change",
        notes="Initial status is UNKNOWN until reviewed.",
    )


def tx_waveinfo_registry_row(source_object: str = "TX_WAVEINFO") -> SemanticRegistryRow:
    row = initial_semantics_registry_row(
        "TX_WAVEINFO:SKIPPED_SEMANTICS_UNPROVEN",
        source_object,
        (),
        (),
    )
    return SemanticRegistryRow(
        **{
            **asdict(row),
            "semantics_status": "UNKNOWN",
            "approval_status": "SKIPPED_SEMANTICS_UNPROVEN",
            "notes": "May be profiled only; cannot prove route without approved semantics.",
        }
    )


def fanout_action(match_count: int, page_size: int) -> Literal["FETCH_SINGLE_PAGE", "PAGINATE"]:
    if page_size < 1:
        msg = "page_size must be positive"
        raise ValueError(msg)
    return "FETCH_SINGLE_PAGE" if match_count <= page_size else "PAGINATE"


def fanout_incomplete_area(
    object_name: str,
    column_name: str,
    id_node_key: str,
    expected_row_count: int,
    fetched_row_count: int,
    page_size: int,
    attempted_mitigations: Sequence[str],
    stop_reason: str,
    resume_checkpoint: str,
) -> IncompleteArea:
    if not attempted_mitigations:
        msg = "Fanout INCOMPLETE requires at least one attempted mitigation"
        raise ValueError(msg)
    return IncompleteArea(
        object_name=object_name,
        column_name=column_name,
        id_node_key=id_node_key,
        expected_row_count=expected_row_count,
        fetched_row_count=fetched_row_count,
        page_size=page_size,
        attempted_mitigations=tuple(attempted_mitigations),
        stop_reason=stop_reason,
        resume_checkpoint=resume_checkpoint,
    )


def negative_evidence_allowed(result: GraphClosureResult, accepted_tm_proof_exists: bool) -> bool:
    return (
        result.fixed_point_reached
        and len(result.incomplete_areas) == 0
        and not accepted_tm_proof_exists
    )


def negative_evidence_ledger_entry(
    service_id: str,
    blocker_type: str,
    missing_relation: str,
    endpoint_node_keys: Sequence[str],
    hashes: Mapping[str, str],
    searched_objects: Sequence[str],
    searched_columns: Sequence[str],
    skipped_objects: Sequence[str],
    skipped_columns: Sequence[str],
    result: GraphClosureResult,
    final_status_split: Mapping[str, object],
    accepted_tm_proof_exists: bool,
) -> dict[str, object]:
    return {
        "service_id": service_id,
        "blocker_type": blocker_type,
        "missing_relation": missing_relation,
        "endpoint_node_keys": list(endpoint_node_keys),
        "schema_hash": hashes.get("schema_hash", ""),
        "candidate_filter_hash": hashes.get("candidate_filter_hash", ""),
        "scan_coverage_hash": hashes.get("scan_coverage_hash", ""),
        "structured_id_dictionary_hash": hashes.get("structured_id_dictionary_hash", ""),
        "edge_semantics_registry_hash": hashes.get("edge_semantics_registry_hash", ""),
        "searched_objects": list(searched_objects),
        "searched_columns": list(searched_columns),
        "skipped_objects": list(skipped_objects),
        "skipped_columns": list(skipped_columns),
        "incomplete_areas": [asdict(area) for area in result.incomplete_areas],
        "positive_match_count": result.evidence_row_count,
        "accepted_path_count": result.accepted_path_count,
        "rejected_path_count": result.rejected_path_count,
        "unknown_semantics_path_count": result.unknown_semantics_path_count,
        "fixed_point_reached": result.fixed_point_reached,
        "negative_evidence_allowed": negative_evidence_allowed(result, accepted_tm_proof_exists),
        "final_status_split": dict(final_status_split),
        "invalidation_rule": "invalidate on any hash drift or registry change",
    }


def schema_drift_report(
    previous_hashes: Mapping[str, str],
    current_hashes: Mapping[str, str],
) -> dict[str, object]:
    changed = sorted(
        key for key, value in current_hashes.items() if previous_hashes.get(key) != value
    )
    return {
        "review_required": bool(changed),
        "changed_hashes": changed,
        "previous_hashes": dict(previous_hashes),
        "current_hashes": dict(current_hashes),
        "invalidation_required": bool(changed),
    }


def missing_required_case(
    required_case_type: str,
    searched_sources: Sequence[str],
    unavailable_reason: str,
    regression_impact: str,
) -> GoldenBlockerCase:
    return GoldenBlockerCase(
        case_id=f"MISSING_REQUIRED_CASE:{required_case_type}",
        service_id="",
        blocker_type="",
        expected_status="",
        required_case_type=required_case_type,
        availability_status="MISSING_REQUIRED_CASE",
        searched_sources=tuple(searched_sources),
        unavailable_reason=unavailable_reason,
        regression_impact=regression_impact,
        owner_confirmation_required=True,
        evidence_artifacts=(),
    )


def evaluate_golden_corpus(
    cases: Sequence[GoldenBlockerCase],
) -> tuple[str, list[GoldenBlockerCase]]:
    by_type = {case.required_case_type: case for case in cases}
    normalized = list(cases)
    for required_type in REQUIRED_CASE_TYPES:
        if required_type not in by_type:
            normalized.append(
                missing_required_case(
                    required_type,
                    (),
                    "Required case was not found in configured corpus sources.",
                    "Regression completeness is incomplete.",
                )
            )
    has_missing = any(case.availability_status == "MISSING_REQUIRED_CASE" for case in normalized)
    return (INCOMPLETE if has_missing else PASS), normalized


def regression_status_for_corpus(
    cases: Sequence[GoldenBlockerCase],
    observed_status_by_case_id: Mapping[str, str],
) -> StatusEntry:
    corpus_status, normalized = evaluate_golden_corpus(cases)
    if corpus_status == INCOMPLETE:
        return StatusEntry(INCOMPLETE, "Golden corpus has missing required cases.", ())
    failures = [
        case.case_id
        for case in normalized
        if observed_status_by_case_id.get(case.case_id) != case.expected_status
    ]
    if failures:
        return StatusEntry(
            FAIL, "Available corpus case violated expected outcome.", tuple(failures)
        )
    return StatusEntry(PASS, "Corpus executed and expected outcomes held.", ())


def status_split_template(run_id: str) -> dict[str, object]:
    statuses: dict[str, dict[str, object]] = {}
    for name in STATUS_NAMES:
        if name == "Sorter implementation change":
            statuses[name] = {"status": NOT_STARTED, "reason": "out of scope", "evidence": []}
        else:
            statuses[name] = {"status": NOT_RUN, "reason": "not evaluated", "evidence": []}
    return {"run_id": run_id, "statuses": statuses}


def derive_exact_overlap_status(
    *,
    complete: bool,
    overlap_count: int,
    incomplete_reason: str = "",
) -> StatusEntry:
    if not complete:
        return StatusEntry(INCOMPLETE, incomplete_reason or "Searchable area incomplete.", ())
    if overlap_count == 0:
        return StatusEntry(FAIL, "Complete scan found zero exact ID overlap.", ())
    return StatusEntry(PASS, "All feasible columns scanned with exact ID overlap found.", ())


def derive_graph_closure_status(
    result: GraphClosureResult, blocker_relevant_path_count: int
) -> StatusEntry:
    if not result.fixed_point_reached:
        return StatusEntry(INCOMPLETE, "Evidence graph stopped before fixed point.", ())
    if blocker_relevant_path_count == 0:
        return StatusEntry(
            FAIL, "Fixed point reached with no relevant candidate or accepted path.", ()
        )
    return StatusEntry(PASS, "Fixed point reached.", ())


def derive_tm_relation_status(result: GraphClosureResult, accepted_path_count: int) -> StatusEntry:
    if not result.fixed_point_reached or result.incomplete_areas:
        return StatusEntry(INCOMPLETE, "Graph, scan, or semantics incomplete.", ())
    if accepted_path_count == 0:
        return StatusEntry(FAIL, "Complete fixed point found no accepted TM client-line path.", ())
    return StatusEntry(PASS, "Accepted path exists with approved semantics.", ())


def derive_negative_ledger_status(
    *,
    accepted_tm_proof_exists: bool,
    ledger_written: bool,
    ledger_allowed: bool,
    ledger_malformed: bool,
    incomplete_areas_exist: bool,
) -> StatusEntry:
    if accepted_tm_proof_exists:
        return StatusEntry(NOT_REQUIRED, "Accepted TM proof exists.", ())
    if ledger_malformed or (ledger_written and not ledger_allowed):
        return StatusEntry(FAIL, "Ledger written when not allowed or malformed.", ())
    if incomplete_areas_exist:
        return StatusEntry(INCOMPLETE, "Negative evidence desired but incomplete areas exist.", ())
    if ledger_written and ledger_allowed:
        return StatusEntry(PASS, "Complete negative entry written after fixed point.", ())
    return StatusEntry(INCOMPLETE, "Negative evidence entry not yet written.", ())


def derive_schema_drift_status(report: Mapping[str, object], hashes_available: bool) -> StatusEntry:
    if not hashes_available:
        return StatusEntry(INCOMPLETE, "Missing prior or current hash data blocks evaluation.", ())
    if report.get("review_required") and not report.get("invalidation_required"):
        return StatusEntry(FAIL, "Drift detected but not invalidated.", ())
    return StatusEntry(PASS, "All hashes computed and prior ledger evaluated.", ())


def failure_status_from_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if any(token in text for token in ("timeout", "permission", "truncat", "fanout")):
        return INCOMPLETE
    return FAIL


def write_csv_artifact(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json_artifact(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_required_empty_artifacts(run_dir: Path, run_id: str, service_id: str) -> None:
    write_csv_artifact(run_dir / "exact_match_hits.csv", EXACT_MATCH_HITS_COLUMNS, ())
    write_csv_artifact(run_dir / "evidence_edges.csv", EVIDENCE_EDGES_COLUMNS, ())
    write_csv_artifact(run_dir / "edge_semantics_registry.csv", EDGE_SEMANTICS_REGISTRY_COLUMNS, ())
    write_csv_artifact(run_dir / "coverage_matrix.csv", COVERAGE_MATRIX_COLUMNS, ())
    write_csv_artifact(run_dir / "skipped_objects.csv", SKIPPED_OBJECTS_COLUMNS, ())
    empty_result = GraphClosureResult(False, 0, 0, 0, 0, 0, (), (), 0, (), 0, 0, 0)
    write_json_artifact(
        run_dir / "graph_closure_summary.json",
        graph_closure_summary_payload(run_id, service_id, "", "", empty_result),
    )
    write_json_artifact(run_dir / "status_split.json", status_split_template(run_id))


def execute_metadata_query(cursor: Any, sql_text: str) -> tuple[list[str], list[dict[str, object]]]:
    cursor.execute(sql_text)
    column_names = [column[0] for column in cursor.description]
    rows = [dict(zip(column_names, row, strict=False)) for row in cursor.fetchall()]
    return column_names, rows


def execute_metadata_queries(
    cursor: Any,
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
) -> dict[str, list[dict[str, object]]]:
    outputs: dict[str, list[dict[str, object]]] = {}
    for name, sql_text in required_metadata_queries(database, schema).items():
        try:
            _columns, rows = execute_metadata_query(cursor, sql_text)
        except Exception as exc:
            if name == "views_available_columns":
                outputs["metadata_gaps"] = view_metadata_gap_rows(
                    VIEW_REQUIRED_COLUMNS,
                    discovery_status=INCOMPLETE,
                    discovery_error=str(exc),
                )
                outputs["views_available_columns"] = [
                    {"COLUMN_NAME": column} for column in VIEW_REQUIRED_COLUMNS
                ]
                continue
            if name != "dependencies":
                raise
            outputs[name] = [
                {
                    "dependency_metadata_status": INCOMPLETE,
                    "reason": str(exc),
                }
            ]
        else:
            outputs[name] = rows
    view_columns = [
        str(row["COLUMN_NAME"])
        for row in outputs.get("views_available_columns", [])
        if "COLUMN_NAME" in row
    ]
    outputs.setdefault("metadata_gaps", []).extend(view_metadata_gap_rows(view_columns))
    try:
        _columns, rows = execute_metadata_query(
            cursor,
            build_views_metadata_query(database, schema, view_columns),
        )
    except Exception as exc:
        outputs.setdefault("metadata_gaps", []).append(
            {
                "metadata_object": "INFORMATION_SCHEMA.VIEWS",
                "gap_scope": "VIEW_METADATA_QUERY",
                "missing_column": "",
                "required": True,
                "causes_incomplete": True,
                "reason": str(exc),
            }
        )
        outputs["views"] = []
    else:
        outputs["views"] = rows
    return outputs


def profiles_from_information_schema_columns(
    rows: Iterable[Mapping[str, object]],
    object_type_by_name: Mapping[str, str],
) -> list[ColumnProfile]:
    profiles: list[ColumnProfile] = []
    for row in rows:
        object_name = str(row["TABLE_NAME"])
        profiles.append(
            ColumnProfile(
                database=str(row["TABLE_CATALOG"]),
                schema=str(row["TABLE_SCHEMA"]),
                object_name=object_name,
                object_type=object_type_by_name.get(object_name, ""),
                column_name=str(row["COLUMN_NAME"]),
                ordinal_position=required_int(row["ORDINAL_POSITION"]),
                data_type=str(row["DATA_TYPE"]),
                numeric_scale=optional_int(row.get("NUMERIC_SCALE")),
                is_nullable=str(row["IS_NULLABLE"]),
            )
        )
    return profiles


def required_int(value: object) -> int:
    return int(Decimal(str(value)))


def optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(Decimal(str(value)))


def object_type_map(objects: Iterable[Mapping[str, object]]) -> dict[str, str]:
    return {str(row["TABLE_NAME"]): str(row.get("TABLE_TYPE", "")) for row in objects}


def render_command_log(database: str, schema: str) -> str:
    queries = required_metadata_queries(database, schema)
    return "\n\n".join(f"-- {name}\n{sql_text};" for name, sql_text in queries.items())
