"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_evidence_collector_context import *  # noqa: F403

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .inca_evidence_collector_artifacts import (
        candidate_column_reason_rows,
        candidate_rows,
        csv_headers,
        evidence_edge_rows,
        id_bag_payload,
        join_paths_payload,
        readme_text,
        registry_csv_rows,
        run_hashes,
    )
    from .inca_evidence_collector_live import (
        apply_session_controls,
        connect_with_connection_name,
        proof_columns_by_object,
        scan_evidence_graph,
        scan_ic_seed_nodes,
    )
    from .inca_evidence_collector_state import (
        append_csv_row,
        check_deadline,
        empty_seed_scan,
        execute_observed_rows,
        load_route_seed_scan,
        mark_checkpoint_incomplete,
        merge_seed_scans,
        phase_artifact_paths,
        refresh_run_manifest_counts,
        should_write_negative_ledger,
        status_payload_for_state,
        utc_now,
        write_checkpoint,
        write_negative_ledger,
        write_progress_summary,
    )


def run_collector_phases(args: argparse.Namespace, state: RunState) -> RunState:
    connection = connect_with_connection_name(args.connection)
    try:
        cursor = connection.cursor()
        try:
            run_phase(state, "initialize_run", lambda: phase_initialize_run(cursor, args, state))
            run_phase(
                state,
                "discover_schema_objects",
                lambda: phase_discover_schema_objects(cursor, state),
            )
            run_phase(
                state,
                "discover_schema_columns",
                lambda: phase_discover_schema_columns(cursor, state),
            )
            run_phase(
                state,
                "discover_views_metadata",
                lambda: phase_discover_views_metadata(cursor, state),
            )
            run_phase(
                state,
                "discover_dependencies_optional",
                lambda: phase_discover_dependencies_optional(cursor, state),
            )
            run_phase(state, "write_schema_profile", lambda: phase_write_schema_profile(state))
            run_phase(
                state,
                "build_structured_id_dictionary",
                lambda: phase_build_structured_id_dictionary(state),
            )
            if args.phase in {"seed-only", "full"}:
                run_phase(
                    state,
                    "extract_service_seed_ids",
                    lambda: phase_extract_service_seed_ids(cursor, state),
                )
            if args.phase == "full":
                run_phase(
                    state,
                    "run_exact_id_overlap_scan",
                    lambda: phase_run_exact_id_overlap_scan(cursor, state),
                )
                run_phase(state, "run_graph_closure", lambda: phase_run_graph_closure(state))
            run_phase(state, "write_final_status", lambda: phase_write_final_status(state))
        finally:
            cursor.close()
    finally:
        connection.close()
    return state


def run_phase(state: RunState, phase: str, action: Any) -> None:
    started_at = utc_now()
    state.run_manifest["current_phase"] = phase
    phase_payload = {"status": INCOMPLETE, "started_at": started_at, "completed_at": ""}
    phases = state.run_manifest.setdefault("phases", {})
    if isinstance(phases, dict):
        phases[phase] = phase_payload
    write_checkpoint(state, phase)
    write_json_artifact(state.run_dir / "run_manifest.json", state.run_manifest)
    try:
        check_deadline(state, phase, "phase start")
        action()
        status = PASS
        reason = ""
    except InternalDeadlineExceededError as exc:
        status = INCOMPLETE
        reason = str(exc)
        raise
    except CollectorIncompleteError as exc:
        status = INCOMPLETE
        reason = str(exc)
        raise
    except Exception as exc:
        status = INCOMPLETE
        reason = str(exc)
        raise
    finally:
        completed_at = utc_now()
        if isinstance(phases, dict):
            phases[phase] = {
                "status": locals().get("status", INCOMPLETE),
                "started_at": started_at,
                "completed_at": completed_at,
            }
        append_csv_row(
            state.run_dir / "phase_log.csv",
            PHASE_LOG_COLUMNS,
            {
                "run_id": state.config.run_id,
                "phase": phase,
                "started_at": started_at,
                "completed_at": completed_at,
                "status": locals().get("status", INCOMPLETE),
                "artifact_paths": "|".join(phase_artifact_paths(state, phase)),
                "reason": locals().get("reason", ""),
            },
        )
        write_json_artifact(state.run_dir / "run_manifest.json", state.run_manifest)
        if locals().get("status", INCOMPLETE) == PASS:
            write_checkpoint(state, phase)
        else:
            mark_checkpoint_incomplete(state, phase, locals().get("reason", ""))


def phase_initialize_run(cursor: object, args: argparse.Namespace, state: RunState) -> None:
    apply_session_controls(cursor, state, args.query_tag or state.config.run_id)
    rows = execute_observed_rows(
        state,
        cursor,
        required_metadata_queries(state.config.database, state.config.schema)["session_context"],
        (),
        "initialize_run",
        "session_context",
    )
    state.metadata["session_context"] = rows.rows
    refresh_run_manifest_counts(state)


def phase_discover_schema_objects(cursor: object, state: RunState) -> None:
    rows = execute_observed_rows(
        state,
        cursor,
        required_metadata_queries(state.config.database, state.config.schema)["tables"],
        (),
        "discover_schema_objects",
        "tables",
    )
    state.metadata["tables"] = rows.rows
    write_csv_artifact(state.run_dir / "schema_objects.csv", csv_headers(rows.rows), rows.rows)
    write_csv_artifact(state.run_dir / "object_counts.csv", csv_headers(rows.rows), rows.rows)
    refresh_run_manifest_counts(state)


def phase_discover_schema_columns(cursor: object, state: RunState) -> None:
    rows = execute_observed_rows(
        state,
        cursor,
        required_metadata_queries(state.config.database, state.config.schema)["columns"],
        (),
        "discover_schema_columns",
        "columns",
    )
    state.metadata["columns"] = rows.rows
    write_csv_artifact(state.run_dir / "schema_columns.csv", csv_headers(rows.rows), rows.rows)
    refresh_run_manifest_counts(state)


def phase_discover_views_metadata(cursor: object, state: RunState) -> None:
    state.metadata.setdefault("metadata_gaps", [])
    try:
        available = execute_observed_rows(
            state,
            cursor,
            required_metadata_queries(state.config.database, state.config.schema)[
                "views_available_columns"
            ],
            (),
            "discover_views_metadata",
            "views_available_columns",
        )
    except Exception as exc:
        state.metadata["metadata_gaps"].extend(
            view_metadata_gap_rows(
                ("TABLE_CATALOG", "TABLE_SCHEMA", "TABLE_NAME"),
                discovery_status=INCOMPLETE,
                discovery_error=str(exc),
            )
        )
        available_columns = ["TABLE_CATALOG", "TABLE_SCHEMA", "TABLE_NAME"]
    else:
        state.metadata["views_available_columns"] = available.rows
        available_columns = [
            str(row["COLUMN_NAME"]) for row in available.rows if "COLUMN_NAME" in row
        ]
        state.metadata["metadata_gaps"].extend(view_metadata_gap_rows(available_columns))
    try:
        view_sql = build_views_metadata_query(
            state.config.database, state.config.schema, available_columns
        )
        views = execute_observed_rows(
            state, cursor, view_sql, (), "discover_views_metadata", "views"
        )
    except Exception as exc:
        state.metadata["metadata_gaps"].append(
            {
                "metadata_object": "INFORMATION_SCHEMA.VIEWS",
                "gap_scope": "VIEW_METADATA_QUERY",
                "missing_column": "",
                "required": True,
                "causes_incomplete": True,
                "reason": str(exc),
            }
        )
        state.metadata["views"] = []
    else:
        state.metadata["views"] = views.rows
    write_csv_artifact(
        state.run_dir / "metadata_gaps.csv",
        METADATA_GAPS_COLUMNS,
        state.metadata.get("metadata_gaps", []),
    )
    refresh_run_manifest_counts(state)


def phase_discover_dependencies_optional(cursor: object, state: RunState) -> None:
    try:
        rows = execute_observed_rows(
            state,
            cursor,
            required_metadata_queries(state.config.database, state.config.schema)["dependencies"],
            (),
            "discover_dependencies_optional",
            "dependencies",
        )
    except Exception as exc:
        state.metadata["dependencies"] = [
            {"dependency_metadata_status": INCOMPLETE, "reason": str(exc)}
        ]
        state.metadata.setdefault("metadata_gaps", []).append(
            {
                "metadata_object": "SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES",
                "gap_scope": "OPTIONAL_DEPENDENCY_METADATA",
                "missing_column": "",
                "required": False,
                "causes_incomplete": False,
                "reason": str(exc),
            }
        )
    else:
        state.metadata["dependencies"] = rows.rows
    write_csv_artifact(
        state.run_dir / "dependencies.csv",
        csv_headers(state.metadata.get("dependencies", [])),
        state.metadata.get("dependencies", []),
    )
    write_csv_artifact(
        state.run_dir / "metadata_gaps.csv",
        METADATA_GAPS_COLUMNS,
        state.metadata.get("metadata_gaps", []),
    )


def phase_write_schema_profile(state: RunState) -> None:
    state.profiles = profiles_from_information_schema_columns(
        state.metadata.get("columns", []),
        object_type_map(state.metadata.get("tables", [])),
    )
    write_csv_artifact(
        state.run_dir / "schema_objects.csv",
        csv_headers(state.metadata.get("tables", [])),
        state.metadata.get("tables", []),
    )
    write_csv_artifact(
        state.run_dir / "schema_columns.csv",
        csv_headers(state.metadata.get("columns", [])),
        state.metadata.get("columns", []),
    )
    refresh_run_manifest_counts(state)


def phase_build_structured_id_dictionary(state: RunState) -> None:
    state.proof_by_object = proof_columns_by_object(state.profiles)
    state.dictionary_rows = build_structured_id_dictionary_rows(state.config.run_id, state.profiles)
    state.candidates = candidate_rows(state.config, state.proof_by_object)
    write_csv_artifact(
        state.run_dir / "structured_id_dictionary.csv",
        STRUCTURED_ID_DICTIONARY_COLUMNS,
        state.dictionary_rows,
    )
    write_csv_artifact(
        state.run_dir / "candidate_tables.csv",
        ("run_id", "object_name", "candidate_reason", "id_domains", "feasible_column_count"),
        state.candidates,
    )
    write_csv_artifact(
        state.run_dir / "candidate_column_reasons.csv",
        ("run_id", "object_name", "column_name", "id_domain", "reason"),
        candidate_column_reason_rows(state.config, state.proof_by_object),
    )
    refresh_run_manifest_counts(state)


def phase_extract_service_seed_ids(cursor: object, state: RunState) -> None:
    seed_scan = empty_seed_scan()
    if state.config.seed_mode in {"service-anchor", "service-anchor-plus-route-bag"}:
        seed_scan = scan_ic_seed_nodes(
            cursor, state.config, state.profiles, state.proof_by_object, state
        )
    if state.config.seed_mode in {"route-bag", "service-anchor-plus-route-bag"}:
        route_seed_scan = load_route_seed_scan(state.config)
        seed_scan = merge_seed_scans(seed_scan, route_seed_scan)
    state.seed_scan = seed_scan
    write_json_artifact(
        state.run_dir / "ic388612_id_bag.json", id_bag_payload(state.config, state.seed_scan)
    )
    write_csv_artifact(
        state.run_dir / "skipped_objects.csv",
        SKIPPED_OBJECTS_COLUMNS,
        state.seed_scan.skipped_rows,
    )


def phase_run_exact_id_overlap_scan(cursor: object, state: RunState) -> None:
    write_csv_artifact(state.run_dir / "exact_match_hits.csv", EXACT_MATCH_HITS_COLUMNS, ())
    write_csv_artifact(state.run_dir / "coverage_matrix.csv", COVERAGE_MATRIX_COLUMNS, ())
    write_csv_artifact(state.run_dir / "evidence_edges.csv", EVIDENCE_EDGES_COLUMNS, ())
    write_csv_artifact(
        state.run_dir / "edge_semantics_registry.csv", EDGE_SEMANTICS_REGISTRY_COLUMNS, ()
    )
    write_json_artifact(
        state.run_dir / "join_paths.json",
        {"accepted_paths": [], "rejected_paths": [], "unknown_semantics_paths": []},
    )
    write_progress_summary(state, "run_exact_id_overlap_scan")
    state.graph_scan = scan_evidence_graph(
        cursor, state.config, state.proof_by_object, state.seed_scan.seed_nodes, state
    )
    registry_rows = registry_csv_rows(state.graph_scan.evidence_rows)
    write_csv_artifact(
        state.run_dir / "exact_match_hits.csv",
        EXACT_MATCH_HITS_COLUMNS,
        state.graph_scan.exact_hits,
    )
    write_csv_artifact(
        state.run_dir / "coverage_matrix.csv",
        COVERAGE_MATRIX_COLUMNS,
        state.graph_scan.coverage_rows,
    )
    write_csv_artifact(
        state.run_dir / "skipped_objects.csv",
        SKIPPED_OBJECTS_COLUMNS,
        [*state.seed_scan.skipped_rows, *state.graph_scan.skipped_rows],
    )
    write_csv_artifact(
        state.run_dir / "evidence_edges.csv",
        EVIDENCE_EDGES_COLUMNS,
        evidence_edge_rows(state.config, state.graph_scan.evidence_rows),
    )
    write_csv_artifact(
        state.run_dir / "edge_semantics_registry.csv",
        EDGE_SEMANTICS_REGISTRY_COLUMNS,
        registry_rows,
    )
    write_json_artifact(
        state.run_dir / "join_paths.json", join_paths_payload(state.graph_scan.evidence_rows)
    )


def phase_run_graph_closure(state: RunState) -> None:
    incomplete = [*state.seed_scan.incomplete_areas, *state.graph_scan.incomplete_areas]
    state.closure = close_evidence_graph(
        state.seed_scan.seed_nodes.keys(),
        state.graph_scan.evidence_rows,
        tuple(incomplete),
    )
    write_json_artifact(
        state.run_dir / "graph_closure_summary.json",
        graph_closure_summary_payload(
            state.config.run_id, state.config.service_id, state.started_at, utc_now(), state.closure
        ),
    )


def phase_write_final_status(state: RunState) -> None:
    statuses = status_payload_for_state(state)
    state.status_split = statuses
    hashes = run_hashes(
        state.metadata.get("tables", []),
        state.metadata.get("columns", []),
        state.dictionary_rows,
        state.graph_scan.coverage_rows,
        registry_csv_rows(state.graph_scan.evidence_rows),
    )
    write_json_artifact(state.run_dir / "status_split.json", statuses)
    write_json_artifact(
        state.run_dir / "schema_drift_invalidation_report.json", schema_drift_report({}, hashes)
    )
    write_json_artifact(state.run_dir / "golden_blocker_corpus.json", {"cases": []})
    write_json_artifact(state.run_dir / "golden_blocker_results.json", {"status": INCOMPLETE})
    if should_write_negative_ledger(state):
        write_negative_ledger(state, hashes, statuses)
    state.run_manifest["negative_evidence_allowed"] = should_write_negative_ledger(state)
    state.run_manifest["run_status"] = PASS if state.config.phase_mode != "full" else INCOMPLETE
    state.run_manifest["completed_at"] = utc_now()
    refresh_run_manifest_counts(state)
    (state.run_dir / "README.md").write_text(readme_text(state.config.service_id), encoding="utf-8")
