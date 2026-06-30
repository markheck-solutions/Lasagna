"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_evidence_collector_context import *  # noqa: F403

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .inca_evidence_collector_artifacts import evidence_edge_rows, registry_csv_rows
    from .inca_evidence_collector_rows import (
        coverage_row,
        exact_fanout_area,
        exact_hit_row,
        exact_incomplete_area,
        evidence_row,
        nodes_from_row,
        pages_to_fetch,
        predicate_value,
        quoted_select_list,
        row_text,
        skipped_row,
    )
    from .inca_evidence_collector_state import (
        append_csv_row,
        check_deadline,
        execute_observed_rows,
        refresh_graph_scan_state,
        write_progress_summary,
        write_scan_checkpoint,
    )


def scan_single_predicate(
    cursor: object,
    config: LiveConfig,
    pass_number: int,
    predicate_column: ColumnProfile,
    proof_columns: list[ColumnProfile],
    node: IdNode,
    known_nodes: dict[str, IdNode],
    visited_rows: set[str],
    evidence_rows: list[EvidenceRow],
    exact_hits: list[dict[str, object]],
    coverage_rows: list[dict[str, object]],
    skipped_rows: list[dict[str, object]],
    incomplete_areas: list[IncompleteArea],
    registry_keys_written: set[str],
    state: RunState | None = None,
    visited_predicates: set[tuple[str, str, str]] | None = None,
) -> None:
    predicate_sql = build_count_sql(
        config.database,
        config.schema,
        predicate_column.object_name,
        predicate_column.column_name,
        1,
    )
    try:
        count, query_id = execute_count(
            cursor,
            predicate_sql,
            (predicate_value(node, predicate_column),),
            state,
            "run_exact_id_overlap_scan",
            "exact_count",
        )
    except Exception as exc:
        record_exact_count_failure(
            config,
            predicate_column,
            node,
            str(exc),
            skipped_rows,
            incomplete_areas,
            state,
            evidence_rows,
            exact_hits,
            coverage_rows,
        )
        return
    pages, incomplete_reason = limit_exact_pages(
        config, predicate_column, node, count, incomplete_areas
    )
    record_scan_progress(
        state, predicate_column, node, pass_number, visited_predicates, known_nodes, count, 0
    )
    fetched_rows = fetch_exact_pages(
        cursor, config, predicate_column, proof_columns, node, pages, state
    )
    record_exact_coverage(
        state,
        config,
        predicate_column,
        node,
        pass_number,
        count,
        fetched_rows,
        incomplete_reason,
        coverage_rows,
    )
    record_fetched_exact_rows(
        state,
        config,
        pass_number,
        predicate_column,
        proof_columns,
        node,
        count,
        fetched_rows,
        query_id,
        predicate_sql,
        incomplete_reason,
        known_nodes,
        visited_rows,
        evidence_rows,
        exact_hits,
        registry_keys_written,
    )
    refresh_graph_scan_state(
        state, evidence_rows, exact_hits, coverage_rows, incomplete_areas, skipped_rows
    )
    record_scan_progress(
        state,
        predicate_column,
        node,
        pass_number,
        visited_predicates,
        known_nodes,
        count,
        len(fetched_rows),
        incomplete_reason,
    )


def record_exact_count_failure(
    config: LiveConfig,
    predicate_column: ColumnProfile,
    node: IdNode,
    reason: str,
    skipped_rows: list[dict[str, object]],
    incomplete_areas: list[IncompleteArea],
    state: RunState | None,
    evidence_rows: list[EvidenceRow],
    exact_hits: list[dict[str, object]],
    coverage_rows: list[dict[str, object]],
) -> None:
    skipped_rows.append(skipped_row(config, predicate_column, "EXACT_COUNT_FAILED", reason, True))
    incomplete_areas.append(exact_incomplete_area(config, predicate_column, node, reason))
    refresh_graph_scan_state(
        state, evidence_rows, exact_hits, coverage_rows, incomplete_areas, skipped_rows
    )


def limit_exact_pages(
    config: LiveConfig,
    predicate_column: ColumnProfile,
    node: IdNode,
    count: int,
    incomplete_areas: list[IncompleteArea],
) -> tuple[int, str]:
    pages = pages_to_fetch(count, config.page_size)
    if pages <= config.max_pages_per_predicate:
        return pages, ""
    reason = "page count exceeds configured operational max and owner approval needed"
    incomplete_areas.append(exact_fanout_area(config, predicate_column, node, count))
    return config.max_pages_per_predicate, reason


def record_exact_coverage(
    state: RunState | None,
    config: LiveConfig,
    predicate_column: ColumnProfile,
    node: IdNode,
    pass_number: int,
    count: int,
    fetched_rows: list[tuple[int, dict[str, object]]],
    incomplete_reason: str,
    coverage_rows: list[dict[str, object]],
) -> None:
    coverage = coverage_row(
        config, predicate_column, node, pass_number, count, len(fetched_rows), incomplete_reason
    )
    coverage_rows.append(coverage)
    if state is not None:
        append_csv_row(state.run_dir / "coverage_matrix.csv", COVERAGE_MATRIX_COLUMNS, coverage)


def record_fetched_exact_rows(
    state: RunState | None,
    config: LiveConfig,
    pass_number: int,
    predicate_column: ColumnProfile,
    proof_columns: list[ColumnProfile],
    node: IdNode,
    count: int,
    fetched_rows: list[tuple[int, dict[str, object]]],
    query_id: str,
    predicate_sql: str,
    incomplete_reason: str,
    known_nodes: dict[str, IdNode],
    visited_rows: set[str],
    evidence_rows: list[EvidenceRow],
    exact_hits: list[dict[str, object]],
    registry_keys_written: set[str],
) -> None:
    for page_number, row in fetched_rows:
        row_hash = row_text(row, "ROW_HASH")
        exact_hit = exact_hit_for_fetched_row(
            config,
            pass_number,
            predicate_column,
            proof_columns,
            node,
            count,
            len(fetched_rows),
            row_hash,
            query_id,
            predicate_sql,
            page_number,
            incomplete_reason,
        )
        exact_hits.append(exact_hit)
        append_exact_hit_if_live(state, exact_hit)
        row_nodes = nodes_from_row(row, proof_columns)
        for discovered in row_nodes.values():
            known_nodes.setdefault(discovered.key, discovered)
        if row_hash in visited_rows or not row_nodes:
            continue
        visited_rows.add(row_hash)
        evidence = evidence_row(
            config, pass_number, predicate_column.object_name, row_hash, row_nodes
        )
        evidence_rows.append(evidence)
        append_evidence_if_live(state, config, evidence, registry_keys_written)


def exact_hit_for_fetched_row(
    config: LiveConfig,
    pass_number: int,
    predicate_column: ColumnProfile,
    proof_columns: list[ColumnProfile],
    node: IdNode,
    count: int,
    fetched_count: int,
    row_hash: str,
    query_id: str,
    predicate_sql: str,
    page_number: int,
    incomplete_reason: str,
) -> dict[str, object]:
    return exact_hit_row(
        config,
        pass_number,
        predicate_column,
        node,
        count,
        fetched_count,
        row_hash,
        [profile.column_name for profile in proof_columns],
        query_id,
        predicate_sql,
        page_number,
        incomplete_reason,
    )


def append_exact_hit_if_live(state: RunState | None, exact_hit: dict[str, object]) -> None:
    if state is None:
        return
    append_csv_row(state.run_dir / "exact_match_hits.csv", EXACT_MATCH_HITS_COLUMNS, exact_hit)


def append_evidence_if_live(
    state: RunState | None,
    config: LiveConfig,
    evidence: EvidenceRow,
    registry_keys_written: set[str],
) -> None:
    if state is None:
        return
    append_csv_row(
        state.run_dir / "evidence_edges.csv",
        EVIDENCE_EDGES_COLUMNS,
        evidence_edge_rows(config, [evidence])[0],
    )
    if evidence.semantics_registry_key in registry_keys_written:
        return
    append_csv_row(
        state.run_dir / "edge_semantics_registry.csv",
        EDGE_SEMANTICS_REGISTRY_COLUMNS,
        registry_csv_rows([evidence])[0],
    )
    registry_keys_written.add(evidence.semantics_registry_key)


def record_scan_progress(
    state: RunState | None,
    predicate_column: ColumnProfile,
    node: IdNode,
    pass_number: int,
    visited_predicates: set[tuple[str, str, str]] | None,
    known_nodes: dict[str, IdNode],
    rows_expected: int,
    rows_fetched: int,
    reason: str = "",
) -> None:
    if state is None:
        return
    write_scan_checkpoint(
        state,
        "run_exact_id_overlap_scan",
        predicate_column,
        node,
        rows_expected,
        rows_fetched,
        visited_predicates,
        sorted(known_nodes),
    )
    write_progress_summary(
        state,
        "run_exact_id_overlap_scan",
        current_object=predicate_column.object_name,
        current_column=predicate_column.column_name,
        current_node_key=node.key,
        pass_number=pass_number,
        visited_predicate_count=len(visited_predicates or set()),
        known_node_count=len(known_nodes),
        rows_expected=rows_expected,
        rows_fetched=rows_fetched,
        reason=reason,
    )


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
