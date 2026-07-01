"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from typing import TYPE_CHECKING

from .inca_evidence_collector_context import *  # noqa: F403

if TYPE_CHECKING:
    from .inca_evidence_collector_predicate_sql import (
        build_exact_fetch_sql,
        execute_count,
        execute_rows,
        predicate_value,
    )
    from .inca_evidence_collector_state import check_deadline


def collect_predicate_probe_snapshots(cursor: object, state: RunState) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for object_name, proof_columns in sorted(state.proof_by_object.items()):
        check_deadline(state, "write_probe_snapshots", object_name)
        snapshots.extend(probe_object_predicates(cursor, state, proof_columns))
    return snapshots


def probe_object_predicates(
    cursor: object,
    state: RunState,
    proof_columns: list[ColumnProfile],
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for predicate_column in proof_columns:
        domain = classify_structured_id_column(
            predicate_column, Searchability("SEARCHABLE", True, "PASS")
        ).id_domain
        nodes = [node for node in state.seed_scan.seed_nodes.values() if node.id_domain == domain]
        for node in nodes:
            snapshots.append(
                probe_single_predicate(cursor, state, predicate_column, proof_columns, node)
            )
    return snapshots


def probe_single_predicate(
    cursor: object,
    state: RunState,
    predicate_column: ColumnProfile,
    proof_columns: list[ColumnProfile],
    node: IdNode,
) -> dict[str, object]:
    started = time.monotonic()
    try:
        count, count_query_id = execute_probe_count(cursor, state, predicate_column, node)
        sample_rows, sample_query_id = sample_probe_rows(
            cursor, state, predicate_column, proof_columns, node, count
        )
        return predicate_probe_snapshot_payload(
            run_id=state.config.run_id,
            source_namespace=f"{state.config.database}.{state.config.schema}",
            object_name=predicate_column.object_name,
            predicate_field=predicate_column.column_name,
            predicate_domain=node.id_domain,
            predicate_ref=node.key,
            exact_hit_count=count,
            sample_rows=sample_rows,
            count_request_id=count_query_id,
            sample_request_id=sample_query_id,
            elapsed_ms=elapsed_ms(started),
            row_limit_used=state.config.probe_sample_row_limit,
            limits=probe_limits(state.config),
        )
    except Exception as exc:
        return predicate_probe_snapshot_payload(
            run_id=state.config.run_id,
            source_namespace=f"{state.config.database}.{state.config.schema}",
            object_name=predicate_column.object_name,
            predicate_field=predicate_column.column_name,
            predicate_domain=node.id_domain,
            predicate_ref=node.key,
            exact_hit_count=-1,
            sample_rows=[],
            count_request_id="",
            sample_request_id="",
            elapsed_ms=elapsed_ms(started),
            row_limit_used=state.config.probe_sample_row_limit,
            limits=probe_limits(state.config),
            error=str(exc),
        )


def execute_probe_count(
    cursor: object,
    state: RunState,
    predicate_column: ColumnProfile,
    node: IdNode,
) -> tuple[int, str]:
    check_deadline(state, "write_probe_snapshots", predicate_column.object_name)
    predicate_sql = build_count_sql(
        state.config.database,
        state.config.schema,
        predicate_column.object_name,
        predicate_column.column_name,
        1,
    )
    return execute_count(
        cursor,
        predicate_sql,
        (predicate_value(node, predicate_column),),
        state,
        "write_probe_snapshots",
        "probe_exact_count",
    )


def sample_probe_rows(
    cursor: object,
    state: RunState,
    predicate_column: ColumnProfile,
    proof_columns: list[ColumnProfile],
    node: IdNode,
    count: int,
) -> tuple[list[dict[str, object]], str]:
    decision = probe_decision(count, probe_limits(state.config))
    if decision in {"COUNT_ONLY", "OWNER_APPROVAL_REQUIRED", "SKIP"}:
        return [], ""
    check_deadline(state, "write_probe_snapshots", predicate_column.object_name)
    result = execute_rows(
        cursor,
        build_exact_fetch_sql(state.config, predicate_column, proof_columns),
        (
            predicate_value(node, predicate_column),
            state.config.probe_sample_row_limit,
            0,
        ),
        state,
        "write_probe_snapshots",
        "probe_exact_sample",
    )
    return result.rows[: state.config.probe_sample_row_limit], result.query_id


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def probe_limits(config: LiveConfig) -> SnapshotLimits:
    return SnapshotLimits(
        sample_row_limit=config.probe_sample_row_limit,
        deep_fetch_row_limit=config.page_size * config.max_pages_per_predicate,
    )
