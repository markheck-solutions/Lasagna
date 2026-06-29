"""Exact-match SQL and evidence graph helpers for INCA_SRC discovery."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_src_discovery_context import *  # noqa: F403
from .inca_src_discovery_models import *  # noqa: F403
from .inca_src_discovery_metadata import quote_identifier, stable_hash


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
