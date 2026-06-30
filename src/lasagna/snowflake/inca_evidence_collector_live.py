"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_evidence_collector_context import *  # noqa: F403

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .inca_evidence_collector_predicates import (
        build_anchor_count_sql,
        execute_count,
        fetch_anchor_pages,
        scan_single_predicate,
    )
    from .inca_evidence_collector_rows import (
        add_nodes_from_fetched_rows,
        fanout_area_for_anchor,
        pages_to_fetch,
        seed_hit_row,
        seed_incomplete_area,
        skipped_row,
    )
    from .inca_evidence_collector_state import (
        check_deadline,
        execute_observed_rows,
        refresh_graph_scan_state,
        write_progress_summary,
        write_scan_checkpoint,
    )


def connect_with_connection_name(connection_name: str) -> Any:
    return snowflake.connector.connect(
        connection_name=connection_name,
        authenticator="externalbrowser",
        client_store_temporary_credential=True,
        session_parameters={"CLIENT_TELEMETRY_ENABLED": False},
    )


def apply_session_controls(cursor: object, state: RunState, query_tag: str) -> None:
    escaped_tag = query_tag.replace("'", "''")
    execute_observed_rows(
        state,
        cursor,
        "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = "
        f"{int(state.config.statement_timeout_seconds)}",
        (),
        "initialize_run",
        "set_statement_timeout",
    )
    execute_observed_rows(
        state,
        cursor,
        f"ALTER SESSION SET QUERY_TAG = 'LASAGNA_INCA_SRC_DISCOVERY:{escaped_tag}'",
        (),
        "initialize_run",
        "set_query_tag",
    )


def collect_live_artifacts(
    cursor: object,
    config: LiveConfig,
    metadata: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    profiles = profiles_from_information_schema_columns(
        metadata.get("columns", []),
        object_type_map(metadata.get("tables", [])),
    )
    proof_by_object = proof_columns_by_object(profiles)
    seed_scan = scan_ic_seed_nodes(cursor, config, profiles, proof_by_object)
    graph_scan = scan_evidence_graph(cursor, config, proof_by_object, seed_scan.seed_nodes)
    incomplete = [*seed_scan.incomplete_areas, *graph_scan.incomplete_areas]
    closure = close_evidence_graph(
        seed_scan.seed_nodes.keys(),
        graph_scan.evidence_rows,
        tuple(incomplete),
    )
    return {
        "profiles": profiles,
        "proof_by_object": proof_by_object,
        "seed_scan": seed_scan,
        "graph_scan": graph_scan,
        "closure": closure,
    }


def proof_columns_by_object(
    profiles: list[ColumnProfile],
) -> dict[str, list[ColumnProfile]]:
    proof: dict[str, list[ColumnProfile]] = defaultdict(list)
    searchable = Searchability("SEARCHABLE", True, "PASS")
    for profile in profiles:
        classification = classify_structured_id_column(profile, searchable)
        if classification.id_domain and classification.feasibility_status == "FEASIBLE":
            proof[profile.object_name].append(profile)
    return dict(proof)


def scan_ic_seed_nodes(
    cursor: object,
    config: LiveConfig,
    profiles: list[ColumnProfile],
    proof_by_object: dict[str, list[ColumnProfile]],
    state: RunState | None = None,
) -> SeedScanResult:
    seed_nodes: dict[str, IdNode] = {}
    seed_rows: list[dict[str, object]] = []
    skipped_rows: list[dict[str, object]] = []
    incomplete_areas: list[IncompleteArea] = []
    searched = 0
    hits = 0
    for anchor in anchor_profiles(profiles):
        if state is not None:
            check_deadline(state, "extract_service_seed_ids", anchor.object_name)
            write_scan_checkpoint(state, "extract_service_seed_ids", anchor, None, 0, 0)
        searched += 1
        object_proof = proof_by_object.get(anchor.object_name, [])
        if not object_proof:
            continue
        try:
            count, query_id = execute_count(
                cursor,
                build_anchor_count_sql(config, anchor),
                (config.service_id,),
                state,
                "extract_service_seed_ids",
                "anchor_count",
            )
        except Exception as exc:
            skipped_rows.append(skipped_row(config, anchor, "ANCHOR_COUNT_FAILED", str(exc), True))
            incomplete_areas.append(seed_incomplete_area(config, anchor, str(exc)))
            continue
        if count == 0:
            continue
        hits += count
        pages = pages_to_fetch(count, config.page_size)
        if pages > config.max_pages_per_predicate:
            incomplete_areas.append(fanout_area_for_anchor(config, anchor, count))
            pages = config.max_pages_per_predicate
        fetched = fetch_anchor_pages(cursor, config, anchor, object_proof, pages, state)
        seed_rows.extend(
            seed_hit_row(config, anchor, row, query_id, count, page_number)
            for page_number, row in fetched
        )
        add_nodes_from_fetched_rows(seed_nodes, fetched, object_proof)
    return SeedScanResult(seed_nodes, seed_rows, searched, hits, incomplete_areas, skipped_rows)


def anchor_profiles(profiles: list[ColumnProfile]) -> list[ColumnProfile]:
    return [
        profile
        for profile in profiles
        if canonical_data_type(profile.data_type) in TEXT_TYPES
        and not data_type_is_rejected(profile.data_type)
        and is_service_anchor_column(profile.column_name)
    ]


def is_service_anchor_column(column_name: str) -> bool:
    upper = column_name.upper()
    exact_names = {
        "SERVICE",
        "SERVICE_ID",
        "SERVICE_NAME",
        "SERVICE_NR",
        "SERVICE_NUMBER",
        "SERVICE_CODE",
        "SERVICE_IDENTIFIER",
        "CIRCUIT",
        "CIRCUIT_ID",
        "CIRCUIT_NAME",
        "CIRCUIT_NR",
        "CIRCUIT_NUMBER",
        "CIRCUIT_CODE",
        "CIRCUIT_IDENTIFIER",
    }
    if upper in exact_names:
        return True
    suffixes = (
        "_SERVICE",
        "_SERVICE_ID",
        "_SERVICE_NAME",
        "_SERVICE_NR",
        "_SERVICE_NUMBER",
        "_SERVICE_CODE",
        "_CIRCUIT",
        "_CIRCUIT_ID",
        "_CIRCUIT_NAME",
        "_CIRCUIT_NR",
        "_CIRCUIT_NUMBER",
        "_CIRCUIT_CODE",
    )
    return upper.endswith(suffixes)


def scan_evidence_graph(
    cursor: object,
    config: LiveConfig,
    proof_by_object: dict[str, list[ColumnProfile]],
    seed_nodes: dict[str, IdNode],
    state: RunState | None = None,
) -> GraphScanResult:
    known_nodes = dict(seed_nodes)
    evidence_rows: list[EvidenceRow] = []
    exact_hits: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    skipped_rows: list[dict[str, object]] = []
    incomplete_areas: list[IncompleteArea] = []
    visited_predicates: set[tuple[str, str, str]] = set()
    visited_rows: set[str] = set()
    registry_keys_written: set[str] = set()
    pass_number = 0
    while True:
        pass_number += 1
        start_nodes = dict(known_nodes)
        before_nodes = len(known_nodes)
        before_rows = len(visited_rows)
        for object_name, proof_columns in sorted(proof_by_object.items()):
            if state is not None:
                check_deadline(state, "run_exact_id_overlap_scan", object_name)
            scan_object_predicates(
                cursor,
                config,
                pass_number,
                proof_columns,
                start_nodes,
                known_nodes,
                visited_predicates,
                visited_rows,
                evidence_rows,
                exact_hits,
                coverage_rows,
                skipped_rows,
                incomplete_areas,
                registry_keys_written,
                state,
            )
            refresh_graph_scan_state(
                state, evidence_rows, exact_hits, coverage_rows, incomplete_areas, skipped_rows
            )
        if len(known_nodes) == before_nodes and len(visited_rows) == before_rows:
            break
    return GraphScanResult(evidence_rows, exact_hits, coverage_rows, incomplete_areas, skipped_rows)


def scan_object_predicates(
    cursor: object,
    config: LiveConfig,
    pass_number: int,
    proof_columns: list[ColumnProfile],
    start_nodes: dict[str, IdNode],
    known_nodes: dict[str, IdNode],
    visited_predicates: set[tuple[str, str, str]],
    visited_rows: set[str],
    evidence_rows: list[EvidenceRow],
    exact_hits: list[dict[str, object]],
    coverage_rows: list[dict[str, object]],
    skipped_rows: list[dict[str, object]],
    incomplete_areas: list[IncompleteArea],
    registry_keys_written: set[str],
    state: RunState | None = None,
) -> None:
    for predicate_column in proof_columns:
        domain = classify_structured_id_column(
            predicate_column, Searchability("SEARCHABLE", True, "PASS")
        ).id_domain
        for node in [item for item in start_nodes.values() if item.id_domain == domain]:
            predicate_key = (predicate_column.object_name, predicate_column.column_name, node.key)
            if predicate_key in visited_predicates:
                continue
            if state is not None:
                check_deadline(
                    state,
                    "run_exact_id_overlap_scan",
                    f"{predicate_column.object_name}.{predicate_column.column_name}",
                )
                write_scan_checkpoint(
                    state,
                    "run_exact_id_overlap_scan",
                    predicate_column,
                    node,
                    0,
                    0,
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
                    visited_predicate_count=len(visited_predicates),
                    known_node_count=len(known_nodes),
                )
            visited_predicates.add(predicate_key)
            scan_single_predicate(
                cursor,
                config,
                pass_number,
                predicate_column,
                proof_columns,
                node,
                known_nodes,
                visited_rows,
                evidence_rows,
                exact_hits,
                coverage_rows,
                skipped_rows,
                incomplete_areas,
                registry_keys_written,
                state,
                visited_predicates,
            )
