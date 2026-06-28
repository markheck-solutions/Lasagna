"""Collect sanitized INCA_SRC evidence artifacts with read-only Snowflake queries."""

from __future__ import annotations

import argparse
import csv
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
    internal_deadline_seconds: int
    statement_timeout_seconds: int


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build sanitized deterministic INCA_SRC discovery artifacts."
    )
    parser.add_argument("--service-id", default="IC-388612")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--connection", default="sdm_runner")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--query-tag", default="")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages-per-predicate", type=int, default=25)
    parser.add_argument(
        "--phase",
        choices=("full", "metadata-only", "seed-only"),
        default="full",
        help="Run full evidence, metadata-only smoke, or seed-only smoke.",
    )
    parser.add_argument(
        "--internal-deadline-seconds",
        type=int,
        default=DEFAULT_INTERNAL_DEADLINE_SECONDS,
    )
    parser.add_argument(
        "--statement-timeout-seconds",
        type=int,
        default=DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--start-fresh", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        state = initialize_run(args)
    except Exception as exc:
        write_init_error(args.output_root, exc)
        raise
    exit_code = 0
    connection = None
    cursor = None
    try:
        state = run_collector_phases(args, state)
    except InternalDeadlineExceededError as exc:
        mark_incomplete_after_exception(state, "internal deadline exceeded", exc)
        exit_code = 2
    except CollectorIncompleteError as exc:
        mark_incomplete_after_exception(state, "collector incomplete", exc)
        exit_code = 2
    except Exception as exc:
        mark_incomplete_after_exception(state, "collector error", exc)
        exit_code = 1
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
    print(f"ARTIFACT_DIR={state.run_dir}")
    return exit_code


def initialize_run(args: argparse.Namespace) -> RunState:
    run_id = datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")
    run_dir = resolve_run_dir(args, run_id)
    config = LiveConfig(
        run_id=run_dir.name,
        service_id=args.service_id,
        database=args.database,
        schema=args.schema,
        page_size=args.page_size,
        max_pages_per_predicate=args.max_pages_per_predicate,
        phase_mode=args.phase,
        internal_deadline_seconds=args.internal_deadline_seconds,
        statement_timeout_seconds=args.statement_timeout_seconds,
    )
    state = RunState(
        run_dir=run_dir,
        config=config,
        started_at=utc_now(),
        deadline_started_monotonic=time.monotonic(),
        status_split=status_split_template(config.run_id),
        run_manifest=initial_run_manifest(config, run_dir),
        metadata={},
        profiles=[],
        proof_by_object={},
        dictionary_rows=[],
        candidates=[],
        seed_scan=empty_seed_scan(),
        graph_scan=empty_graph_scan(),
    )
    write_baseline_artifacts(state)
    return state


def resolve_run_dir(args: argparse.Namespace, generated_run_id: str) -> Path:
    if args.resume and args.start_fresh:
        msg = "--resume and --start-fresh are mutually exclusive"
        raise ValueError(msg)
    output_root = Path(args.output_root)
    run_dir = Path(args.run_dir) if args.run_dir else output_root / generated_run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        if not args.resume and not args.start_fresh:
            msg = f"Existing run folder requires --resume or --start-fresh: {run_dir}"
            raise RuntimeError(msg)
        if args.start_fresh:
            msg = f"--start-fresh requires a new or empty run folder: {run_dir}"
            raise RuntimeError(msg)
        checkpoint = run_dir / "checkpoint.json"
        if not checkpoint.exists():
            msg = f"--resume requires checkpoint.json: {run_dir}"
            raise RuntimeError(msg)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def initial_run_manifest(config: LiveConfig, run_dir: Path) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "service_id": config.service_id,
        "database": config.database,
        "schema": config.schema,
        "phase_mode": config.phase_mode,
        "run_dir": str(run_dir),
        "started_at": utc_now(),
        "completed_at": "",
        "run_status": INCOMPLETE,
        "current_phase": "initialize_run",
        "phases": {
            phase: {"status": "NOT_RUN", "started_at": "", "completed_at": ""} for phase in PHASES
        },
        "sanitized": True,
        "raw_row_exports": False,
        "total_objects_discovered": 0,
        "total_views_discovered": 0,
        "total_columns_discovered": 0,
        "structured_id_column_count": 0,
        "metadata_gap_count": 0,
        "internal_deadline_seconds": config.internal_deadline_seconds,
        "statement_timeout_seconds": config.statement_timeout_seconds,
        "artifact_first": True,
    }


def write_baseline_artifacts(state: RunState) -> None:
    try:
        write_json_artifact(state.run_dir / "run_manifest.json", state.run_manifest)
        write_json_artifact(state.run_dir / "status_split.json", state.status_split)
        write_csv_artifact(state.run_dir / "metadata_gaps.csv", METADATA_GAPS_COLUMNS, ())
        write_csv_artifact(state.run_dir / "coverage_matrix.csv", COVERAGE_MATRIX_COLUMNS, ())
        write_csv_artifact(state.run_dir / "skipped_objects.csv", SKIPPED_OBJECTS_COLUMNS, ())
        write_csv_artifact(state.run_dir / "query_log.csv", QUERY_LOG_COLUMNS, ())
        write_csv_artifact(state.run_dir / "phase_log.csv", PHASE_LOG_COLUMNS, ())
        write_json_artifact(
            state.run_dir / "graph_closure_summary.json",
            graph_closure_summary_payload(
                state.config.run_id,
                state.config.service_id,
                state.started_at,
                "",
                GraphClosureResult(False, 0, 0, 0, 0, 0, (), (), 0, (), 0, 0, 0),
            ),
        )
        write_command_log(state.run_dir, state.config.database, state.config.schema)
        write_checkpoint(state, "initialize_run")
    except Exception as exc:
        (state.run_dir / "init_error.txt").write_text(str(exc), encoding="utf-8")
        raise


def write_init_error(output_root: Path, exc: Exception) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "init_error.txt"
    path.write_text(f"{utc_now()}\n{type(exc).__name__}: {exc}\n", encoding="utf-8")


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
        write_checkpoint(state, phase)


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
    state.seed_scan = scan_ic_seed_nodes(
        cursor, state.config, state.profiles, state.proof_by_object, state
    )
    write_json_artifact(
        state.run_dir / "ic388612_id_bag.json", id_bag_payload(state.config, state.seed_scan)
    )
    write_csv_artifact(
        state.run_dir / "skipped_objects.csv",
        SKIPPED_OBJECTS_COLUMNS,
        state.seed_scan.skipped_rows,
    )


def phase_run_exact_id_overlap_scan(cursor: object, state: RunState) -> None:
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
    state.run_manifest["run_status"] = PASS if state.config.phase_mode != "full" else INCOMPLETE
    state.run_manifest["completed_at"] = utc_now()
    refresh_run_manifest_counts(state)
    (state.run_dir / "README.md").write_text(readme_text(state.config.service_id), encoding="utf-8")


def status_payload_for_state(state: RunState) -> dict[str, object]:
    if state.config.phase_mode == "full" and state.closure is not None:
        return status_payload(
            state.config,
            state.metadata,
            state.seed_scan,
            state.graph_scan,
            state.closure,
            state.candidates,
            registry_csv_rows(state.graph_scan.evidence_rows),
        )
    split = status_split_template(state.config.run_id)
    statuses = split["statuses"]
    schema_status = INCOMPLETE if metadata_has_incomplete_gap(state.metadata) else PASS
    if state.metadata.get("tables") and state.metadata.get("columns"):
        set_status(
            statuses, "INCA_SRC schema discovery", schema_status, "metadata inventory written", []
        )
        set_status(statuses, "Schema/profile catalog", schema_status, "schema catalog written", [])
        set_status(
            statuses, "Manifest-boundary avoidance", PASS, "full metadata inventory used", []
        )
        set_status(
            statuses,
            "Structured ID dictionary",
            PASS if state.dictionary_rows else INCOMPLETE,
            "deterministic classification written",
            [],
        )
    if state.config.phase_mode == "seed-only":
        seed_status = (
            INCOMPLETE
            if state.seed_scan.incomplete_areas
            else (PASS if state.seed_scan.seed_nodes else FAIL)
        )
        set_status(statuses, "IC-388612 ID extraction", seed_status, "seed scan completed", [])
    for name in (
        "Exact-ID overlap scan",
        "Evidence graph closure",
        "TM client-line relation proof",
        "Negative evidence ledger",
        "IC-388612 route order proof",
    ):
        set_status(statuses, name, INCOMPLETE, "phase not completed in this run", [])
    set_status(statuses, "Candidate relation scan", INCOMPLETE, "candidate scan not completed", [])
    set_status(statuses, "Edge semantics registry", INCOMPLETE, "semantics not reviewed", [])
    set_status(
        statuses, "Schema drift invalidation", PASS, "hashes computed for available artifacts", []
    )
    set_status(statuses, "Golden blocker corpus", INCOMPLETE, "required cases not configured", [])
    set_status(statuses, "Golden blocker regression", INCOMPLETE, "corpus incomplete", [])
    set_status(statuses, "Repo validation", "NOT_RUN", "live collector run only", [])
    return split


def write_negative_ledger(
    state: RunState, hashes: dict[str, str], statuses: dict[str, object]
) -> None:
    closure: GraphClosureResult
    if isinstance(state.closure, GraphClosureResult):
        closure = state.closure
    else:
        closure = GraphClosureResult(False, 0, 0, 0, 0, 0, (), (), 0, (), 0, 0, 0)
    ledger = negative_evidence_ledger_entry(
        state.config.service_id,
        "TM_CLIENT_LINE_ROUTE_BLOCKER",
        "TM client-line relation",
        [],
        hashes,
        sorted({str(row["object_name"]) for row in state.graph_scan.coverage_rows}),
        sorted({str(row["column_name"]) for row in state.graph_scan.coverage_rows}),
        sorted({str(row["object_name"]) for row in state.graph_scan.skipped_rows}),
        sorted({str(row["column_name"]) for row in state.graph_scan.skipped_rows}),
        closure,
        statuses,
        accepted_tm_proof_exists=False,
    )
    if not getattr(closure, "fixed_point_reached", False):
        ledger["negative_evidence_allowed"] = False
    write_json_artifact(state.run_dir / "negative_evidence_ledger_entry.json", ledger)


def should_write_negative_ledger(state: RunState) -> bool:
    if state.config.phase_mode != "full":
        return False
    if not isinstance(state.closure, GraphClosureResult):
        return False
    return state.closure.fixed_point_reached and not state.closure.incomplete_areas


def mark_incomplete_after_exception(state: RunState, reason: str, exc: Exception) -> None:
    phase = str(state.run_manifest.get("current_phase", "unknown"))
    mark_statuses_incomplete(state, f"{reason}: {exc}")
    state.run_manifest["run_status"] = INCOMPLETE
    state.run_manifest["completed_at"] = utc_now()
    state.run_manifest["incomplete_reason"] = f"{reason}: {exc}"
    write_json_artifact(state.run_dir / "run_manifest.json", state.run_manifest)
    write_json_artifact(state.run_dir / "status_split.json", state.status_split)
    write_checkpoint(state, phase, reason=f"{reason}: {exc}")
    write_json_artifact(
        state.run_dir / "graph_closure_summary.json",
        graph_closure_summary_payload(
            state.config.run_id,
            state.config.service_id,
            state.started_at,
            utc_now(),
            GraphClosureResult(
                False,
                0,
                len(state.seed_scan.seed_nodes),
                len(state.seed_scan.seed_nodes),
                len(state.graph_scan.evidence_rows),
                len(state.graph_scan.evidence_rows),
                (),
                (),
                0,
                tuple([*state.seed_scan.incomplete_areas, *state.graph_scan.incomplete_areas]),
                0,
                0,
                len(state.graph_scan.evidence_rows),
            ),
        ),
    )


def mark_statuses_incomplete(state: RunState, reason: str) -> None:
    statuses = state.status_split["statuses"]
    for name in (
        "Exact-ID overlap scan",
        "Evidence graph closure",
        "TM client-line relation proof",
        "Negative evidence ledger",
        "IC-388612 route order proof",
    ):
        set_status(statuses, name, INCOMPLETE, reason, [])
    set_status(statuses, "Sorter implementation change", "NOT_STARTED", "out of scope", [])


def refresh_run_manifest_counts(state: RunState) -> None:
    state.run_manifest["total_objects_discovered"] = len(state.metadata.get("tables", []))
    state.run_manifest["total_views_discovered"] = len(state.metadata.get("views", []))
    state.run_manifest["total_columns_discovered"] = len(state.metadata.get("columns", []))
    state.run_manifest["structured_id_column_count"] = sum(
        1 for row in state.dictionary_rows if row.get("id_domain")
    )
    state.run_manifest["metadata_gap_count"] = len(state.metadata.get("metadata_gaps", []))
    write_json_artifact(state.run_dir / "run_manifest.json", state.run_manifest)


def phase_artifact_paths(state: RunState, phase: str) -> list[str]:
    mapping = {
        "initialize_run": ["run_manifest.json", "status_split.json", "command_log.sql"],
        "discover_schema_objects": ["schema_objects.csv", "object_counts.csv"],
        "discover_schema_columns": ["schema_columns.csv"],
        "discover_views_metadata": ["metadata_gaps.csv"],
        "discover_dependencies_optional": ["dependencies.csv", "metadata_gaps.csv"],
        "write_schema_profile": ["schema_objects.csv", "schema_columns.csv"],
        "build_structured_id_dictionary": [
            "structured_id_dictionary.csv",
            "candidate_tables.csv",
            "candidate_column_reasons.csv",
        ],
        "extract_service_seed_ids": ["ic388612_id_bag.json", "skipped_objects.csv"],
        "run_exact_id_overlap_scan": [
            "exact_match_hits.csv",
            "coverage_matrix.csv",
            "evidence_edges.csv",
            "edge_semantics_registry.csv",
        ],
        "run_graph_closure": ["graph_closure_summary.json"],
        "write_final_status": ["status_split.json", "run_manifest.json"],
    }
    return [str(state.run_dir / item) for item in mapping.get(phase, [])]


def execute_observed_rows(
    state: RunState,
    cursor: object,
    sql_text: str,
    params: tuple[object, ...],
    phase: str,
    logical_query_name: str,
) -> QueryRows:
    check_deadline(state, phase, logical_query_name)
    started_at = utc_now()
    started = time.monotonic()
    sql_hash = stable_hash(sql_text)
    query_id = ""
    row_count = 0
    status = PASS
    error = ""
    timeout_reason = ""
    append_command_log(
        state,
        f"-- query_start phase={phase} logical={logical_query_name} sql_hash={sql_hash}\n{sql_text};\n",
    )
    try:
        execute = getattr(cursor, "execute")
        execute(sql_text, params)
        query_id = str(getattr(cursor, "sfqid", ""))
        description = getattr(cursor, "description", None) or []
        names = [column[0] for column in description]
        fetched = getattr(cursor, "fetchall")() if names else []
        rows = [dict(zip(names, row, strict=False)) for row in fetched]
        row_count = len(rows)
    except Exception as exc:
        error = str(exc)
        status = INCOMPLETE if "timeout" in error.lower() else FAIL
        timeout_reason = error if status == INCOMPLETE else ""
        raise
    finally:
        completed_at = utc_now()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        append_csv_row(
            state.run_dir / "query_log.csv",
            QUERY_LOG_COLUMNS,
            {
                "run_id": state.config.run_id,
                "phase": phase,
                "logical_query_name": logical_query_name,
                "sql_hash": sql_hash,
                "query_id": query_id,
                "started_at": started_at,
                "completed_at": completed_at,
                "elapsed_ms": elapsed_ms,
                "row_count": row_count,
                "status": status,
                "error": error,
                "timeout_reason": timeout_reason,
            },
        )
        append_command_log(
            state,
            f"-- query_end phase={phase} logical={logical_query_name} sql_hash={sql_hash} "
            f"query_id={query_id or '<unavailable>'} status={status} rows={row_count}\n",
        )
        if not query_id:
            state.metadata.setdefault("metadata_gaps", []).append(
                {
                    "metadata_object": "QUERY_LOG",
                    "gap_scope": "SNOWFLAKE_QUERY_ID",
                    "missing_column": "QUERY_ID",
                    "required": False,
                    "causes_incomplete": False,
                    "reason": f"query id unavailable for {phase}:{logical_query_name}",
                }
            )
            write_csv_artifact(
                state.run_dir / "metadata_gaps.csv",
                METADATA_GAPS_COLUMNS,
                state.metadata.get("metadata_gaps", []),
            )
    return QueryRows(rows=rows, query_id=query_id)


def check_deadline(state: RunState, phase: str, context: str) -> None:
    elapsed = time.monotonic() - state.deadline_started_monotonic
    if elapsed >= state.config.internal_deadline_seconds:
        write_checkpoint(state, phase, reason=f"internal deadline before {context}")
        msg = f"internal deadline expired after {int(elapsed)} seconds during {phase}: {context}"
        raise InternalDeadlineExceededError(msg)


def write_checkpoint(state: RunState, phase: str, reason: str = "") -> None:
    payload = {
        "run_id": state.config.run_id,
        "phase": phase,
        "reason": reason,
        "updated_at": utc_now(),
        "current_object": "",
        "current_column": "",
        "current_id_domain": "",
        "current_id_value": "",
        "page_hash_window": "",
        "rows_expected": 0,
        "rows_fetched": 0,
        "visited_predicate_keys": [],
        "discovered_node_keys": sorted(state.seed_scan.seed_nodes),
        "evidence_edge_hashes": [evidence_edge_hash(row) for row in state.graph_scan.evidence_rows],
    }
    write_json_artifact(state.run_dir / "checkpoint.json", payload)


def write_scan_checkpoint(
    state: RunState,
    phase: str,
    profile: ColumnProfile,
    node: IdNode | None,
    expected: int,
    fetched: int,
    visited_predicates: set[tuple[str, str, str]] | None = None,
) -> None:
    payload = {
        "run_id": state.config.run_id,
        "phase": phase,
        "reason": "",
        "updated_at": utc_now(),
        "current_object": profile.object_name,
        "current_column": profile.column_name,
        "current_id_domain": "" if node is None else node.id_domain,
        "current_id_value": "" if node is None else node.value,
        "page_hash_window": "",
        "rows_expected": expected,
        "rows_fetched": fetched,
        "visited_predicate_keys": ["|".join(item) for item in sorted(visited_predicates or set())],
        "discovered_node_keys": sorted(state.seed_scan.seed_nodes),
        "evidence_edge_hashes": [evidence_edge_hash(row) for row in state.graph_scan.evidence_rows],
    }
    write_json_artifact(state.run_dir / "checkpoint.json", payload)


def append_csv_row(path: Path, fieldnames: tuple[str, ...], row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def append_command_log(state: RunState, text: str) -> None:
    with (state.run_dir / "command_log.sql").open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write(text)
        handle.flush()


def empty_seed_scan() -> SeedScanResult:
    return SeedScanResult({}, [], 0, 0, [], [])


def empty_graph_scan() -> GraphScanResult:
    return GraphScanResult([], [], [], [], [])


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
    ]


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
                state,
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
                state,
                visited_predicates,
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
        skipped_rows.append(
            skipped_row(config, predicate_column, "EXACT_COUNT_FAILED", str(exc), True)
        )
        incomplete_areas.append(exact_incomplete_area(config, predicate_column, node, str(exc)))
        return
    pages = pages_to_fetch(count, config.page_size)
    incomplete_reason = ""
    if pages > config.max_pages_per_predicate:
        incomplete_reason = (
            "page count exceeds configured operational max and owner approval needed"
        )
        incomplete_areas.append(exact_fanout_area(config, predicate_column, node, count))
        pages = config.max_pages_per_predicate
    if state is not None:
        write_scan_checkpoint(
            state,
            "run_exact_id_overlap_scan",
            predicate_column,
            node,
            count,
            0,
            visited_predicates,
        )
    fetched_rows = fetch_exact_pages(
        cursor, config, predicate_column, proof_columns, node, pages, state
    )
    coverage_rows.append(
        coverage_row(
            config, predicate_column, node, pass_number, count, len(fetched_rows), incomplete_reason
        )
    )
    for page_number, row in fetched_rows:
        row_hash = row_text(row, "ROW_HASH")
        exact_hits.append(
            exact_hit_row(
                config,
                pass_number,
                predicate_column,
                node,
                count,
                len(fetched_rows),
                row_hash,
                [profile.column_name for profile in proof_columns],
                query_id,
                predicate_sql,
                page_number,
                incomplete_reason,
            )
        )
        row_nodes = nodes_from_row(row, proof_columns)
        for discovered in row_nodes.values():
            known_nodes.setdefault(discovered.key, discovered)
        if row_hash in visited_rows or not row_nodes:
            continue
        visited_rows.add(row_hash)
        evidence_rows.append(
            evidence_row(config, pass_number, predicate_column.object_name, row_hash, row_nodes)
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


def write_artifacts(
    run_dir: Path,
    config: LiveConfig,
    metadata: dict[str, list[dict[str, object]]],
    artifacts: dict[str, object],
) -> None:
    objects = metadata.get("tables", [])
    columns = metadata.get("columns", [])
    profiles = as_profiles(artifacts["profiles"])
    proof_by_object = as_proof_by_object(artifacts["proof_by_object"])
    seed_scan = as_seed_scan(artifacts["seed_scan"])
    graph_scan = as_graph_scan(artifacts["graph_scan"])
    closure = artifacts["closure"]
    dictionary_rows = build_structured_id_dictionary_rows(config.run_id, profiles)
    candidates = candidate_rows(config, proof_by_object)
    registry_rows = registry_csv_rows(graph_scan.evidence_rows)
    statuses = status_payload(
        config,
        metadata,
        seed_scan,
        graph_scan,
        closure,
        candidates,
        registry_rows,
    )
    hashes = run_hashes(objects, columns, dictionary_rows, graph_scan.coverage_rows, registry_rows)
    write_core_artifacts(run_dir, config, metadata, dictionary_rows, candidates, proof_by_object)
    write_graph_artifacts(
        run_dir,
        config,
        seed_scan,
        graph_scan,
        registry_rows,
        closure,
        statuses,
        hashes,
    )
    write_json_artifact(
        run_dir / "schema_drift_invalidation_report.json",
        schema_drift_report({}, hashes),
    )
    write_json_artifact(run_dir / "golden_blocker_corpus.json", {"cases": []})
    write_json_artifact(run_dir / "golden_blocker_results.json", {"status": "INCOMPLETE"})
    write_json_artifact(run_dir / "status_split.json", statuses)
    (run_dir / "README.md").write_text(readme_text(config.service_id), encoding="utf-8")


def write_core_artifacts(
    run_dir: Path,
    config: LiveConfig,
    metadata: dict[str, list[dict[str, object]]],
    dictionary_rows: list[dict[str, object]],
    candidates: list[dict[str, object]],
    proof_by_object: dict[str, list[ColumnProfile]],
) -> None:
    objects = metadata.get("tables", [])
    columns = metadata.get("columns", [])
    dependencies = metadata.get("dependencies", [])
    metadata_gaps = metadata.get("metadata_gaps", [])
    write_json_artifact(
        run_dir / "run_manifest.json", run_manifest(config, metadata, dictionary_rows)
    )
    write_csv_artifact(run_dir / "schema_objects.csv", csv_headers(objects), objects)
    write_csv_artifact(run_dir / "schema_columns.csv", csv_headers(columns), columns)
    write_csv_artifact(run_dir / "object_counts.csv", csv_headers(objects), objects)
    write_csv_artifact(run_dir / "dependencies.csv", csv_headers(dependencies), dependencies)
    write_csv_artifact(
        run_dir / "metadata_gaps.csv",
        csv_headers(metadata_gaps),
        metadata_gaps,
    )
    write_csv_artifact(
        run_dir / "structured_id_dictionary.csv",
        STRUCTURED_ID_DICTIONARY_COLUMNS,
        dictionary_rows,
    )
    write_csv_artifact(
        run_dir / "candidate_tables.csv",
        ("run_id", "object_name", "candidate_reason", "id_domains", "feasible_column_count"),
        candidates,
    )
    write_csv_artifact(
        run_dir / "candidate_column_reasons.csv",
        ("run_id", "object_name", "column_name", "id_domain", "reason"),
        candidate_column_reason_rows(config, proof_by_object),
    )


def write_graph_artifacts(
    run_dir: Path,
    config: LiveConfig,
    seed_scan: SeedScanResult,
    graph_scan: GraphScanResult,
    registry_rows: list[dict[str, object]],
    closure: object,
    statuses: dict[str, object],
    hashes: dict[str, str],
) -> None:
    closure_result = as_closure(closure)
    write_json_artifact(run_dir / "ic388612_id_bag.json", id_bag_payload(config, seed_scan))
    write_csv_artifact(
        run_dir / "exact_match_hits.csv", EXACT_MATCH_HITS_COLUMNS, graph_scan.exact_hits
    )
    write_csv_artifact(
        run_dir / "evidence_edges.csv",
        EVIDENCE_EDGES_COLUMNS,
        evidence_edge_rows(config, graph_scan.evidence_rows),
    )
    write_json_artifact(run_dir / "join_paths.json", join_paths_payload(graph_scan.evidence_rows))
    write_csv_artifact(
        run_dir / "edge_semantics_registry.csv", EDGE_SEMANTICS_REGISTRY_COLUMNS, registry_rows
    )
    write_csv_artifact(
        run_dir / "coverage_matrix.csv", COVERAGE_MATRIX_COLUMNS, graph_scan.coverage_rows
    )
    write_csv_artifact(
        run_dir / "skipped_objects.csv", SKIPPED_OBJECTS_COLUMNS, graph_scan.skipped_rows
    )
    write_json_artifact(
        run_dir / "graph_closure_summary.json",
        graph_closure_summary_payload(config.run_id, config.service_id, "", "", closure_result),
    )
    ledger = negative_evidence_ledger_entry(
        config.service_id,
        "TM_CLIENT_LINE_ROUTE_BLOCKER",
        "TM client-line relation",
        [],
        hashes,
        sorted({str(row["object_name"]) for row in graph_scan.coverage_rows}),
        sorted({str(row["column_name"]) for row in graph_scan.coverage_rows}),
        sorted({str(row["object_name"]) for row in graph_scan.skipped_rows}),
        sorted({str(row["column_name"]) for row in graph_scan.skipped_rows}),
        closure_result,
        statuses,
        accepted_tm_proof_exists=False,
    )
    if closure_result.unknown_semantics_path_count:
        ledger["negative_evidence_allowed"] = False
    write_json_artifact(run_dir / "negative_evidence_ledger_entry.json", ledger)


def run_manifest(
    config: LiveConfig,
    metadata: dict[str, list[dict[str, object]]],
    dictionary_rows: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "service_id": config.service_id,
        "database": config.database,
        "schema": config.schema,
        "sanitized": True,
        "raw_row_exports": False,
        "total_objects_discovered": len(metadata.get("tables", [])),
        "total_views_discovered": len(metadata.get("views", [])),
        "total_columns_discovered": len(metadata.get("columns", [])),
        "structured_id_column_count": sum(1 for row in dictionary_rows if row["id_domain"]),
        "metadata_gap_count": len(metadata.get("metadata_gaps", [])),
    }


def candidate_rows(
    config: LiveConfig,
    proof_by_object: dict[str, list[ColumnProfile]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for object_name, profiles in sorted(proof_by_object.items()):
        domains = sorted(
            {
                classify_structured_id_column(
                    profile, Searchability("SEARCHABLE", True, "PASS")
                ).id_domain
                for profile in profiles
            }
        )
        if len(domains) < 2:
            continue
        rows.append(
            {
                "run_id": config.run_id,
                "object_name": object_name,
                "candidate_reason": "MULTIPLE_PROOF_GRADE_STRUCTURED_ID_DOMAINS",
                "id_domains": "|".join(domains),
                "feasible_column_count": len(profiles),
            }
        )
    return rows


def candidate_column_reason_rows(
    config: LiveConfig,
    proof_by_object: dict[str, list[ColumnProfile]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for object_name, profiles in sorted(proof_by_object.items()):
        for profile in profiles:
            classification = classify_structured_id_column(
                profile, Searchability("SEARCHABLE", True, "PASS")
            )
            rows.append(
                {
                    "run_id": config.run_id,
                    "object_name": object_name,
                    "column_name": profile.column_name,
                    "id_domain": classification.id_domain,
                    "reason": classification.inclusion_rule,
                }
            )
    return rows


def id_bag_payload(config: LiveConfig, seed_scan: SeedScanResult) -> dict[str, object]:
    seed_nodes = sorted(seed_scan.seed_nodes)
    return {
        "service_id": config.service_id,
        "seed_nodes": seed_nodes,
        "seed_node_count": len(seed_nodes),
        "exact_anchor_hits": seed_scan.exact_anchor_hits,
        "searched_anchor_columns": seed_scan.searched_anchor_columns,
        "seed_rows": seed_scan.seed_rows,
        "status": "PASS" if seed_nodes else "FAIL",
    }


def evidence_edge_rows(
    config: LiveConfig,
    evidence_rows: list[EvidenceRow],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in evidence_rows:
        rows.append(
            {
                "run_id": config.run_id,
                "pass_number": row.pass_number,
                "edge_hash": evidence_edge_hash(row),
                "source_object": row.source_object,
                "source_row_hash": row.source_row_hash,
                "connected_node_keys": "|".join(row.node_keys),
                "connected_id_domains": "|".join(
                    sorted(domain_from_node_key(key) for key in row.node_keys)
                ),
                "connected_columns": "|".join(row.connected_columns),
                "relation_shape": "MULTI_ID_ROW",
                "edge_type": row.edge_type,
                "cardinality_observed": str(len(row.node_keys)),
                "semantics_registry_key": row.semantics_registry_key,
                "semantics_status": row.semantics_status,
                "may_prove_route_continuity": False,
                "may_prove_tm_relation": False,
                "query_id": row.query_id,
                "evidence_basis": "exact structured ID overlap in Snowflake row",
                "incomplete_reason": "",
            }
        )
    return rows


def registry_csv_rows(evidence_rows: list[EvidenceRow]) -> list[dict[str, object]]:
    by_key: dict[str, EvidenceRow] = {}
    for row in evidence_rows:
        by_key.setdefault(row.semantics_registry_key, row)
    rows: list[dict[str, object]] = []
    for row in by_key.values():
        registry = initial_semantics_registry_row(
            row.semantics_registry_key,
            row.source_object,
            row.connected_columns,
            sorted(domain_from_node_key(key) for key in row.node_keys),
        )
        payload = asdict(registry)
        rows.append({key: csv_cell(value) for key, value in payload.items()})
    return rows


def join_paths_payload(evidence_rows: list[EvidenceRow]) -> dict[str, object]:
    return {
        "accepted_paths": [],
        "rejected_paths": [],
        "unknown_semantics_paths": [evidence_edge_hash(row) for row in evidence_rows],
    }


def status_payload(
    config: LiveConfig,
    metadata: dict[str, list[dict[str, object]]],
    seed_scan: SeedScanResult,
    graph_scan: GraphScanResult,
    closure: object,
    candidates: list[dict[str, object]],
    registry_rows: list[dict[str, object]],
) -> dict[str, object]:
    closure_result = as_closure(closure)
    split = status_split_template(config.run_id)
    statuses = split["statuses"]
    metadata_incomplete = metadata_has_incomplete_gap(metadata)
    schema_status = "INCOMPLETE" if metadata_incomplete else "PASS"
    set_status(
        statuses, "INCA_SRC schema discovery", schema_status, "metadata queries completed", []
    )
    set_status(
        statuses, "Schema/profile catalog", schema_status, "schema catalog artifacts written", []
    )
    set_status(statuses, "Manifest-boundary avoidance", "PASS", "full metadata inventory used", [])
    set_status(
        statuses,
        "Structured ID dictionary",
        "PASS",
        "all columns classified with deterministic rules",
        [],
    )
    seed_status = (
        "INCOMPLETE" if seed_scan.incomplete_areas else ("PASS" if seed_scan.seed_nodes else "FAIL")
    )
    set_status(
        statuses, "IC-388612 ID extraction", seed_status, "exact service anchor scan completed", []
    )
    overlap_status = exact_overlap_status(graph_scan)
    set_status(
        statuses,
        "Exact-ID overlap scan",
        overlap_status,
        "exact structured-ID predicates scanned",
        [],
    )
    graph_status = "INCOMPLETE" if not closure_result.fixed_point_reached else "PASS"
    set_status(
        statuses, "Evidence graph closure", graph_status, "fixed-point graph closure evaluated", []
    )
    registry_status = "INCOMPLETE" if registry_rows else "PASS"
    set_status(
        statuses, "Edge semantics registry", registry_status, "initial semantics are UNKNOWN", []
    )
    candidate_status = (
        "INCOMPLETE" if graph_scan.incomplete_areas else ("PASS" if candidates else "FAIL")
    )
    set_status(
        statuses,
        "Candidate relation scan",
        candidate_status,
        "candidate relation sources classified",
        [],
    )
    tm_status = tm_status_from_closure(closure_result)
    set_status(
        statuses, "TM client-line relation proof", tm_status, "no self-approved semantics", []
    )
    negative_status = negative_status_from_closure(closure_result)
    set_status(
        statuses,
        "Negative evidence ledger",
        negative_status,
        "ledger guarded by closure and semantics",
        [],
    )
    set_status(
        statuses, "Schema drift invalidation", "PASS", "hashes computed for drift checks", []
    )
    set_status(
        statuses,
        "Golden blocker corpus",
        "INCOMPLETE",
        "required cases not configured in this run",
        [],
    )
    set_status(statuses, "Golden blocker regression", "INCOMPLETE", "corpus incomplete", [])
    set_status(
        statuses,
        "IC-388612 route order proof",
        tm_status,
        "TM relation proof controls route proof",
        [],
    )
    set_status(statuses, "Repo validation", "NOT_RUN", "live collector run only", [])
    return split


def metadata_has_incomplete_gap(metadata: dict[str, list[dict[str, object]]]) -> bool:
    for row in metadata.get("metadata_gaps", []):
        if str(row.get("causes_incomplete", "")).lower() == "true":
            return True
    return False


def exact_overlap_status(graph_scan: GraphScanResult) -> str:
    if graph_scan.incomplete_areas:
        return "INCOMPLETE"
    return "PASS" if graph_scan.exact_hits else "FAIL"


def tm_status_from_closure(closure: object) -> str:
    closure_result = as_closure(closure)
    if not closure_result.fixed_point_reached:
        return "INCOMPLETE"
    if closure_result.unknown_semantics_path_count:
        return "INCOMPLETE"
    return "FAIL" if closure_result.accepted_path_count == 0 else "PASS"


def negative_status_from_closure(closure: object) -> str:
    closure_result = as_closure(closure)
    if not closure_result.fixed_point_reached or closure_result.unknown_semantics_path_count:
        return "INCOMPLETE"
    return "PASS"


def set_status(
    statuses: object,
    name: str,
    status: str,
    reason: str,
    evidence: list[str],
) -> None:
    if not isinstance(statuses, dict):
        msg = "status_split statuses payload is malformed"
        raise TypeError(msg)
    statuses[name] = {"status": status, "reason": reason, "evidence": evidence}


def run_hashes(
    objects: list[dict[str, object]],
    columns: list[dict[str, object]],
    dictionary_rows: list[dict[str, object]],
    coverage_rows: list[dict[str, object]],
    registry_rows: list[dict[str, object]],
) -> dict[str, str]:
    return {
        "schema_hash": stable_json_hash({"objects": objects, "columns": columns}),
        "structured_id_dictionary_hash": stable_json_hash(dictionary_rows),
        "candidate_filter_hash": stable_hash("MULTIPLE_PROOF_GRADE_STRUCTURED_ID_DOMAINS"),
        "scan_coverage_hash": stable_json_hash(coverage_rows),
        "edge_semantics_registry_hash": stable_json_hash(registry_rows),
    }


def domain_from_node_key(node_key: str) -> str:
    parts = node_key.split("|")
    return parts[1] if len(parts) >= 3 else ""


def csv_cell(value: object) -> object:
    if isinstance(value, tuple):
        return "|".join(str(item) for item in value)
    return value


def as_profiles(value: object) -> list[ColumnProfile]:
    if not isinstance(value, list):
        msg = "profiles artifact payload is malformed"
        raise TypeError(msg)
    return value


def as_proof_by_object(value: object) -> dict[str, list[ColumnProfile]]:
    if not isinstance(value, dict):
        msg = "proof column payload is malformed"
        raise TypeError(msg)
    return value


def as_seed_scan(value: object) -> SeedScanResult:
    if not isinstance(value, SeedScanResult):
        msg = "seed scan payload is malformed"
        raise TypeError(msg)
    return value


def as_graph_scan(value: object) -> GraphScanResult:
    if not isinstance(value, GraphScanResult):
        msg = "graph scan payload is malformed"
        raise TypeError(msg)
    return value


def as_closure(value: object) -> Any:
    return value


def write_command_log(run_dir: Path, database: str, schema: str) -> None:
    content = (
        render_command_log(database, schema)
        + "\n\n-- exact service anchor scan template\n"
        + 'SELECT <proof_id_columns>, SHA2(...) AS ROW_HASH FROM "<DB>"."<SCHEMA>"."<OBJECT>" '
        + 'WHERE "<ANCHOR_COLUMN>" = %s ORDER BY ROW_HASH LIMIT %s OFFSET %s;\n\n'
        + "-- exact structured-ID overlap template\n"
        + 'SELECT <proof_id_columns>, SHA2(...) AS ROW_HASH FROM "<DB>"."<SCHEMA>"."<OBJECT>" '
        + 'WHERE "<STRUCTURED_ID_COLUMN>" = %s ORDER BY ROW_HASH LIMIT %s OFFSET %s;\n'
    )
    (run_dir / "command_log.sql").write_text(content, encoding="utf-8")


def readme_text(service_id: str) -> str:
    return (
        "# INCA_SRC Evidence Discovery Run\n\n"
        f"Service: `{service_id}`\n\n"
        "Sanitized artifacts only. No raw full-table dumps. No sorter implementation changes. "
        "UNKNOWN edge semantics cannot prove route continuity or TM client-line relation.\n"
    )


def csv_headers(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return []
    return list(rows[0].keys())


if __name__ == "__main__":
    raise SystemExit(main())
