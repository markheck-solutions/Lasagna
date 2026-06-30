"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from typing import TYPE_CHECKING

from .inca_evidence_collector_context import *  # noqa: F403

if TYPE_CHECKING:
    from .inca_evidence_collector_state import check_deadline, execute_observed_rows


def execute_rows(
    cursor: object,
    sql_text: str,
    params: tuple[object, ...],
    state: RunState | None = None,
    phase: str = "",
    logical_query_name: str = "",
) -> QueryRows:
    if state is not None:
        return execute_observed_rows(
            state,
            cursor,
            sql_text,
            params,
            phase or "unknown",
            logical_query_name or "query",
        )
    execute = getattr(cursor, "execute")
    execute(sql_text, params)
    description = getattr(cursor, "description")
    names = [column[0] for column in description]
    fetched = getattr(cursor, "fetchall")()
    rows = [dict(zip(names, row, strict=False)) for row in fetched]
    return QueryRows(rows=rows, query_id=str(getattr(cursor, "sfqid", "")))


def execute_count(
    cursor: object,
    sql_text: str,
    params: tuple[object, ...],
    state: RunState | None = None,
    phase: str = "",
    logical_query_name: str = "",
) -> tuple[int, str]:
    result = execute_rows(cursor, sql_text, params, state, phase, logical_query_name)
    if not result.rows:
        return 0, result.query_id
    first = result.rows[0]
    return int(str(first.get("MATCH_COUNT", 0))), result.query_id


def build_anchor_count_sql(config: LiveConfig, anchor: ColumnProfile) -> str:
    return (
        "SELECT COUNT(*) AS MATCH_COUNT "
        f"FROM {qualified_object(config.database, config.schema, anchor.object_name)} "
        f"WHERE {quote_identifier(anchor.column_name)} = %s"
    )


def build_anchor_fetch_sql(
    config: LiveConfig,
    anchor: ColumnProfile,
    proof_columns: list[ColumnProfile],
) -> str:
    selected = selected_columns([anchor, *proof_columns])
    row_hash = row_hash_expression(selected)
    return (
        f"SELECT {quoted_select_list(selected)}, {row_hash} AS ROW_HASH "
        f"FROM {qualified_object(config.database, config.schema, anchor.object_name)} "
        f"WHERE {quote_identifier(anchor.column_name)} = %s "
        "ORDER BY ROW_HASH "
        "LIMIT %s OFFSET %s"
    )


def build_exact_fetch_sql(
    config: LiveConfig,
    predicate_column: ColumnProfile,
    proof_columns: list[ColumnProfile],
) -> str:
    selected = selected_columns(proof_columns)
    row_hash = row_hash_expression(selected)
    return (
        f"SELECT {quoted_select_list(selected)}, {row_hash} AS ROW_HASH "
        f"FROM {qualified_object(config.database, config.schema, predicate_column.object_name)} "
        f"WHERE {quote_identifier(predicate_column.column_name)} = %s "
        "ORDER BY ROW_HASH "
        "LIMIT %s OFFSET %s"
    )


def fetch_anchor_pages(
    cursor: object,
    config: LiveConfig,
    anchor: ColumnProfile,
    proof_columns: list[ColumnProfile],
    pages: int,
    state: RunState | None = None,
) -> list[tuple[int, dict[str, object]]]:
    sql_text = build_anchor_fetch_sql(config, anchor, proof_columns)
    rows: list[tuple[int, dict[str, object]]] = []
    for page_number in range(1, pages + 1):
        if state is not None:
            check_deadline(state, "extract_service_seed_ids", f"anchor page {page_number}")
        offset = (page_number - 1) * config.page_size
        result = execute_rows(
            cursor,
            sql_text,
            (config.service_id, config.page_size, offset),
            state,
            "extract_service_seed_ids",
            "anchor_fetch_page",
        )
        rows.extend((page_number, row) for row in result.rows)
    return rows


def fetch_exact_pages(
    cursor: object,
    config: LiveConfig,
    predicate_column: ColumnProfile,
    proof_columns: list[ColumnProfile],
    node: IdNode,
    pages: int,
    state: RunState | None = None,
) -> list[tuple[int, dict[str, object]]]:
    sql_text = build_exact_fetch_sql(config, predicate_column, proof_columns)
    rows: list[tuple[int, dict[str, object]]] = []
    for page_number in range(1, pages + 1):
        if state is not None:
            check_deadline(state, "run_exact_id_overlap_scan", f"exact page {page_number}")
        offset = (page_number - 1) * config.page_size
        params = (predicate_value(node, predicate_column), config.page_size, offset)
        result = execute_rows(
            cursor,
            sql_text,
            params,
            state,
            "run_exact_id_overlap_scan",
            "exact_fetch_page",
        )
        rows.extend((page_number, row) for row in result.rows)
    return rows


def selected_columns(profiles: list[ColumnProfile]) -> list[str]:
    names = sorted({profile.column_name for profile in profiles})
    if not names:
        msg = "At least one selected column is required"
        raise ValueError(msg)
    return names


def quoted_select_list(column_names: list[str]) -> str:
    return ", ".join(quote_identifier(column) for column in column_names)


def pages_to_fetch(count: int, page_size: int) -> int:
    if count == 0:
        return 0
    return ((count - 1) // page_size) + 1


def add_nodes_from_fetched_rows(
    seed_nodes: dict[str, IdNode],
    fetched: list[tuple[int, dict[str, object]]],
    proof_columns: list[ColumnProfile],
) -> None:
    for _page, row in fetched:
        for node in nodes_from_row(row, proof_columns).values():
            seed_nodes.setdefault(node.key, node)


def nodes_from_row(
    row: dict[str, object],
    proof_columns: list[ColumnProfile],
) -> dict[str, IdNode]:
    nodes: dict[str, IdNode] = {}
    for profile in proof_columns:
        value = row_value(row, profile.column_name)
        if value is None or str(value).strip() == "":
            continue
        node = node_from_value(
            profile.database,
            profile.schema,
            profile.column_name,
            value,
            profile.data_type,
        )
        nodes[node.key] = node
    return nodes


def row_value(row: dict[str, object], column_name: str) -> object | None:
    if column_name in row:
        return row[column_name]
    return row.get(column_name.upper())


def row_text(row: dict[str, object], column_name: str) -> str:
    value = row_value(row, column_name)
    return "" if value is None else str(value)


def predicate_value(node: IdNode, profile: ColumnProfile) -> object:
    if canonical_data_type(profile.data_type) in TEXT_TYPES:
        return node.value
    return int(node.value)


def seed_hit_row(
    config: LiveConfig,
    anchor: ColumnProfile,
    row: dict[str, object],
    query_id: str,
    count: int,
    page_number: int,
) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "service_id": config.service_id,
        "object_name": anchor.object_name,
        "anchor_column": anchor.column_name,
        "match_count": count,
        "row_hash": row_text(row, "ROW_HASH"),
        "query_id": query_id,
        "page_number": page_number,
    }


def exact_hit_row(
    config: LiveConfig,
    pass_number: int,
    predicate_column: ColumnProfile,
    node: IdNode,
    match_count: int,
    fetched_count: int,
    row_hash: str,
    matched_columns: list[str],
    query_id: str,
    predicate_sql: str,
    page_number: int,
    incomplete_reason: str,
) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "pass_number": pass_number,
        "object_name": predicate_column.object_name,
        "column_name": predicate_column.column_name,
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


def evidence_row(
    config: LiveConfig,
    pass_number: int,
    object_name: str,
    row_hash: str,
    nodes: dict[str, IdNode],
) -> EvidenceRow:
    registry_key = (
        f"{object_name}:{stable_hash('|'.join(sorted(node.id_domain for node in nodes.values())))}"
    )
    return EvidenceRow(
        source_object=object_name,
        source_row_hash=row_hash,
        node_keys=tuple(sorted(nodes)),
        connected_columns=tuple(sorted({node.id_domain for node in nodes.values()})),
        semantics_registry_key=registry_key,
        semantics_status="UNKNOWN",
        edge_type="UNKNOWN",
        pass_number=pass_number,
    )


def coverage_row(
    config: LiveConfig,
    predicate_column: ColumnProfile,
    node: IdNode,
    pass_number: int,
    matched: int,
    fetched: int,
    incomplete_reason: str,
) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "object_name": predicate_column.object_name,
        "column_name": predicate_column.column_name,
        "id_domain": node.id_domain,
        "feasible": True,
        "searched": True,
        "counted": True,
        "fetched": fetched > 0,
        "pass_numbers": str(pass_number),
        "predicate_count": 1,
        "rows_matched": matched,
        "rows_fetched": fetched,
        "skipped": False,
        "skip_reason": "",
        "incomplete": bool(incomplete_reason),
        "incomplete_reason": incomplete_reason,
        "query_ids": "",
        "checkpoint_path": "",
    }


def skipped_row(
    config: LiveConfig,
    profile: ColumnProfile,
    reason_code: str,
    reason_detail: str,
    causes_incomplete: bool,
) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "object_name": profile.object_name,
        "object_type": profile.object_type,
        "column_name": profile.column_name,
        "skip_scope": "COLUMN",
        "skip_reason_code": reason_code,
        "skip_reason_detail": reason_detail,
        "required_for_full_discovery": True,
        "causes_incomplete": causes_incomplete,
        "mitigation_attempted": "count query guarded by statement timeout",
        "next_action": "review permission, timeout, or data type failure",
    }


def seed_incomplete_area(
    config: LiveConfig,
    anchor: ColumnProfile,
    reason: str,
) -> IncompleteArea:
    return fanout_incomplete_area(
        anchor.object_name,
        anchor.column_name,
        f"SERVICE_ID|{config.service_id}",
        0,
        0,
        config.page_size,
        ("exact anchor count attempted",),
        reason,
        f"seed/{anchor.object_name}/{anchor.column_name}",
    )


def exact_incomplete_area(
    config: LiveConfig,
    predicate_column: ColumnProfile,
    node: IdNode,
    reason: str,
) -> IncompleteArea:
    return fanout_incomplete_area(
        predicate_column.object_name,
        predicate_column.column_name,
        node.key,
        0,
        0,
        config.page_size,
        ("exact structured-ID count attempted",),
        reason,
        f"exact/{predicate_column.object_name}/{predicate_column.column_name}/{stable_hash(node.key)}",
    )


def fanout_area_for_anchor(
    config: LiveConfig,
    anchor: ColumnProfile,
    count: int,
) -> IncompleteArea:
    return fanout_incomplete_area(
        anchor.object_name,
        anchor.column_name,
        f"SERVICE_ID|{config.service_id}",
        count,
        config.page_size * config.max_pages_per_predicate,
        config.page_size,
        ("count first", "paginate by stable row hash"),
        "page count exceeds configured operational max and owner approval needed",
        f"seed/{anchor.object_name}/{anchor.column_name}",
    )


def exact_fanout_area(
    config: LiveConfig,
    predicate_column: ColumnProfile,
    node: IdNode,
    count: int,
) -> IncompleteArea:
    return fanout_incomplete_area(
        predicate_column.object_name,
        predicate_column.column_name,
        node.key,
        count,
        config.page_size * config.max_pages_per_predicate,
        config.page_size,
        ("count first", "split per ID", "paginate by stable row hash"),
        "page count exceeds configured operational max and owner approval needed",
        f"exact/{predicate_column.object_name}/{predicate_column.column_name}/{stable_hash(node.key)}",
    )
