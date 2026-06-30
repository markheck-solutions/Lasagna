"""Collect sanitized INCA_SRC evidence artifacts with read-only Snowflake queries."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

import snowflake.connector

from lasagna.evidence_snapshots import (
    SnapshotLimits,
    decision_matrix_payload,
    predicate_probe_snapshot_payload,
    probe_decision,
    profile_snapshot_payload,
    sanitize_sample_rows,
    source_manifest_payload,
    stable_digest,
)
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
    "BOUNDED_JSON_SNAPSHOT_PROTOCOL.md": (
        "docs/runbooks/BOUNDED_JSON_EVIDENCE_SNAPSHOT_PROTOCOL.md"
    ),
    "BOUNDED_JSON_SNAPSHOT_CONTRACT.md": (
        "docs/contracts/BOUNDED_JSON_EVIDENCE_SNAPSHOT_CONTRACT.md"
    ),
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
    "write_probe_snapshots",
    "write_dtn_semantic_probe",
    "run_exact_id_overlap_scan",
    "run_graph_closure",
    "write_final_status",
)
DTN_SEMANTIC_OBJECTS = {
    "service": "V_T_INCATNT_SERVICE_TRANSMISSION_CURRENT",
    "content_position": "V_T_INCATNT_CONTENT_POSITION_CURRENT",
    "content_connection_point": "V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT",
    "connection_cabling_point": "V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT",
    "cabling": "V_T_INCATNT_CABLING_CURRENT",
    "ne_part": "V_T_INCATNT_NE_PART_CURRENT",
}
DEFAULT_DWDM_ADJACENCY_SERVICE_IDS = (
    "IC-388612",
    "IC-386642",
    "IC-386283",
    "IC-324417",
    "IC-392063",
    "IC-339967",
)
DWDM_ADJACENCY_DECISIONS = (
    "PROVEN_DWDM_ADJACENCY",
    "TRANSMISSION_ONLY_FANOUT",
    "INCOMPLETE",
    "OWNER_APPROVAL_REQUIRED",
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
    connection_name: str
    probe_sample_row_limit: int
    semantic_site_code: str
    semantic_device_token: str
    semantic_fetch_row_limit: int
    semantic_service_ids: tuple[str, ...]
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
    semantic_probe: dict[str, object] | None = None
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
        choices=(
            "full",
            "metadata-only",
            "seed-only",
            "probe-only",
            "snapshot-only",
            "semantic-probe",
        ),
        default="full",
        help=(
            "Run full evidence, metadata-only smoke, seed-only smoke, bounded JSON "
            "probe snapshots, or bounded DTN semantic candidate probing."
        ),
    )
    parser.add_argument(
        "--seed-mode",
        choices=("service-anchor", "route-bag", "service-anchor-plus-route-bag"),
        default="service-anchor",
        help="Choose IC seed source. route-bag uses a route-derived structured ID artifact.",
    )
    parser.add_argument("--route-seed-id-bag", type=Path, default=None)
    parser.add_argument("--semantic-site-code", default="ASH/R1")
    parser.add_argument("--semantic-device-token", default="DTN")
    parser.add_argument("--semantic-fetch-row-limit", type=int, default=125)
    parser.add_argument(
        "--semantic-service-ids",
        default="",
        help=(
            "Optional comma/space separated service IDs for one bounded DWDM adjacency "
            "semantic-probe run. Defaults to --service-id."
        ),
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
    parser.add_argument("--probe-sample-row-limit", type=int, default=5)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument(
        "--framework-commit",
        default=os.environ.get("LASAGNA_FRAMEWORK_COMMIT", ""),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--start-fresh", action="store_true")
    return parser.parse_args()


def parse_semantic_service_ids(primary_service_id: str, raw_service_ids: object) -> tuple[str, ...]:
    if isinstance(raw_service_ids, (list, tuple)):
        tokens = [str(token).strip() for token in raw_service_ids if str(token).strip()]
    else:
        tokens = [
            token.strip() for token in re.split(r"[\s,;]+", str(raw_service_ids)) if token.strip()
        ]
    service_ids = tokens or [primary_service_id]
    deduped: list[str] = []
    seen: set[str] = set()
    for service_id in service_ids:
        normalized = service_id.upper()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return tuple(deduped)


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
        seed_mode=args.seed_mode,
        route_seed_id_bag=Path(args.route_seed_id_bag) if args.route_seed_id_bag else None,
        connection_name=args.connection,
        probe_sample_row_limit=args.probe_sample_row_limit,
        semantic_site_code=str(getattr(args, "semantic_site_code", "ASH/R1")),
        semantic_device_token=str(getattr(args, "semantic_device_token", "DTN")),
        semantic_fetch_row_limit=int(getattr(args, "semantic_fetch_row_limit", 125)),
        semantic_service_ids=parse_semantic_service_ids(
            args.service_id, getattr(args, "semantic_service_ids", "")
        ),
        internal_deadline_seconds=args.internal_deadline_seconds,
        statement_timeout_seconds=args.statement_timeout_seconds,
        framework_commit_sha=resolve_framework_commit(args.repo_root, args.framework_commit),
        repo_root=Path(args.repo_root),
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
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "framework_commit_sha": config.framework_commit_sha,
        "runbook_path": str(config.repo_root / DOC_SNAPSHOT_FILES["FRAMEWORK_RUNBOOK_SNAPSHOT.md"]),
        "status_contract_path": str(
            config.repo_root / DOC_SNAPSHOT_FILES["STATUS_CONTRACT_SNAPSHOT.md"]
        ),
        "handoff_path": str(config.repo_root / DOC_SNAPSHOT_FILES["AI_HANDOFF_SNAPSHOT.md"]),
        "documentation_snapshots": list(DOC_SNAPSHOT_FILES),
        "hard_constraints": HARD_CONSTRAINTS,
        "negative_evidence_allowed": False,
        "sorter_changes_allowed": False,
        "port_match_rule_changes_allowed": False,
        "phase_mode": config.phase_mode,
        "seed_mode": config.seed_mode,
        "route_seed_id_bag": ""
        if config.route_seed_id_bag is None
        else str(config.route_seed_id_bag),
        "connection_name": config.connection_name,
        "bounded_json_snapshots": config.phase_mode
        in {"probe-only", "snapshot-only", "semantic-probe"},
        "probe_sample_row_limit": config.probe_sample_row_limit,
        "probe_deep_fetch_row_limit": probe_limits(config).deep_fetch_row_limit,
        "semantic_site_code": config.semantic_site_code,
        "semantic_device_token": config.semantic_device_token,
        "semantic_fetch_row_limit": config.semantic_fetch_row_limit,
        "semantic_service_ids": list(config.semantic_service_ids),
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
        write_progress_summary(state, "initialize_run")
        write_source_manifest(state)
        write_jsonl_artifact(state.run_dir / "profile_snapshots.jsonl", ())
        write_jsonl_artifact(state.run_dir / "predicate_probe_snapshots.jsonl", ())
        write_json_artifact(
            state.run_dir / "probe_decision_matrix.json",
            decision_matrix_payload(state.config.run_id, ()),
        )
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
        write_documentation_snapshots(state)
        write_checkpoint(state, "initialize_run")
    except Exception as exc:
        (state.run_dir / "init_error.txt").write_text(str(exc), encoding="utf-8")
        raise


def write_init_error(output_root: Path, exc: Exception) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "init_error.txt"
    path.write_text(f"{utc_now()}\n{type(exc).__name__}: {exc}\n", encoding="utf-8")


def resolve_framework_commit(repo_root: Path, provided_commit: str) -> str:
    if provided_commit:
        return provided_commit
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def write_documentation_snapshots(state: RunState) -> None:
    snapshot_status: dict[str, str] = {}
    for snapshot_name, relative_path in DOC_SNAPSHOT_FILES.items():
        content = read_doc_snapshot_source(
            state.config.repo_root, state.config.framework_commit_sha, relative_path
        )
        if content:
            snapshot_status[snapshot_name] = "WRITTEN"
            (state.run_dir / snapshot_name).write_text(content, encoding="utf-8")
        else:
            snapshot_status[snapshot_name] = "UNAVAILABLE"
    state.run_manifest["documentation_snapshot_status"] = snapshot_status
    write_json_artifact(state.run_dir / "run_manifest.json", state.run_manifest)


def read_doc_snapshot_source(repo_root: Path, commit_sha: str, relative_path: str) -> str:
    file_path = repo_root / relative_path
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    if not commit_sha:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{commit_sha}:{relative_path}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    return result.stdout


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
            if args.phase in {"seed-only", "probe-only", "snapshot-only", "semantic-probe", "full"}:
                run_phase(
                    state,
                    "extract_service_seed_ids",
                    lambda: phase_extract_service_seed_ids(cursor, state),
                )
            if args.phase in {"probe-only", "snapshot-only", "semantic-probe"}:
                run_phase(
                    state,
                    "write_probe_snapshots",
                    lambda: phase_write_probe_snapshots(cursor, state),
                )
            if args.phase == "semantic-probe":
                run_phase(
                    state,
                    "write_dtn_semantic_probe",
                    lambda: phase_write_dtn_semantic_probe(cursor, state),
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
    write_profile_snapshots(state)
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


def phase_write_probe_snapshots(cursor: object, state: RunState) -> None:
    snapshots = collect_predicate_probe_snapshots(cursor, state)
    write_jsonl_artifact(state.run_dir / "predicate_probe_snapshots.jsonl", snapshots)
    write_json_artifact(
        state.run_dir / "probe_decision_matrix.json",
        decision_matrix_payload(state.config.run_id, snapshots),
    )
    write_progress_summary(
        state,
        "write_probe_snapshots",
        reason=f"predicate_probe_count={len(snapshots)}",
    )


def phase_write_dtn_semantic_probe(cursor: object, state: RunState) -> None:
    probes = collect_dwdm_adjacency_proofs(cursor, state)
    probe = probes[0] if probes else dtn_semantic_probe_payload(state, {}, {}, [], [], [])
    state.semantic_probe = {"services": probes} if len(probes) > 1 else probe
    snapshot = cast("Mapping[str, object]", probe["snapshot"])
    classification = cast("Mapping[str, object]", probe["classification"])
    service_summary = dwdm_adjacency_service_summary(state, probes)
    dwdm_matrix = dwdm_adjacency_decision_matrix(state, service_summary)
    predicate_snapshots = dwdm_predicate_probe_snapshots(state, service_summary)
    write_json_artifact(
        state.run_dir / "dtn_semantic_probe_snapshot.json",
        snapshot,
    )
    write_json_artifact(
        state.run_dir / "dtn_relation_classification.json",
        classification,
    )
    write_json_artifact(state.run_dir / "dwdm_adjacency_service_summary.json", service_summary)
    write_json_artifact(state.run_dir / "dwdm_adjacency_decision_matrix.json", dwdm_matrix)
    write_jsonl_artifact(state.run_dir / "predicate_probe_snapshots.jsonl", predicate_snapshots)
    write_json_artifact(
        state.run_dir / "probe_decision_matrix.json",
        decision_matrix_payload(state.config.run_id, predicate_snapshots),
    )
    counts = classification.get("counts", {})
    write_progress_summary(
        state,
        "write_dtn_semantic_probe",
        reason=f"dtn_candidate_counts={json.dumps(counts, sort_keys=True)}",
    )


def collect_dwdm_adjacency_proofs(cursor: object, state: RunState) -> list[dict[str, object]]:
    probes: list[dict[str, object]] = []
    for service_id in state.config.semantic_service_ids:
        check_deadline(state, "write_dtn_semantic_probe", service_id)
        service_state = replace(state, config=replace(state.config, service_id=service_id))
        probes.append(collect_dtn_semantic_probe(cursor, service_state))
    return probes


def collect_dtn_semantic_probe(cursor: object, state: RunState) -> dict[str, object]:
    columns = semantic_columns_by_object(state.profiles)
    blockers = semantic_schema_blockers(columns)
    query_notes: list[dict[str, object]] = []
    if blockers:
        return dtn_semantic_probe_payload(state, {}, {}, [], blockers, query_notes)

    service_rows = semantic_fetch_service_rows(cursor, state, columns, query_notes)
    seed_values = semantic_seed_values(service_rows, columns[DTN_SEMANTIC_OBJECTS["service"]])
    cp_rows = semantic_fetch_content_position_seed_rows(
        cursor, state, columns, seed_values, query_notes
    )
    content_candidates = semantic_content_candidates(cursor, state, columns, cp_rows, query_notes)
    device_rows = semantic_fetch_device_rows(
        cursor, state, columns, content_candidates, query_notes
    )
    dtn_rows = [row for row in device_rows if semantic_row_matches_target(row, state.config)]
    connpt_ids = sorted({semantic_text(row.get("CCP__CONNPT_INT_ID")) for row in dtn_rows})
    connpt_ids = [value for value in connpt_ids if value]
    cacp_rows = semantic_fetch_cacp_rows(cursor, state, columns, connpt_ids, query_notes)
    cabpt_ids = sorted({semantic_text(row.get("CABPT_INT_ID")) for row in cacp_rows})
    cabpt_ids = [value for value in cabpt_ids if value]
    cabling_rows = semantic_fetch_cabling_rows(cursor, state, columns, cabpt_ids, query_notes)
    peer_ids = semantic_peer_cabpt_ids(cabpt_ids, cabling_rows)
    peer_cacp_rows = semantic_fetch_peer_cacp_rows(cursor, state, columns, peer_ids, query_notes)
    rows = {
        "service_transmission": service_rows,
        "content_position_seed": cp_rows,
        "content_connection_point_devices": device_rows,
        "ashr1_dtn_device_rows": dtn_rows,
        "dtn_device_rows": dtn_rows,
        "dtn_cacp_rows": cacp_rows,
        "dtn_cabling_rows": cabling_rows,
        "cabling_peer_cacp_rows": peer_cacp_rows,
    }
    seed_ids = {"CONNPT_INT_ID": connpt_ids, "CABPT_INT_ID": cabpt_ids}
    return dtn_semantic_probe_payload(
        state, rows, seed_ids, content_candidates, blockers, query_notes
    )


def semantic_columns_by_object(profiles: list[ColumnProfile]) -> dict[str, list[str]]:
    columns: dict[str, list[str]] = defaultdict(list)
    for profile in profiles:
        columns[profile.object_name].append(profile.column_name)
    return columns


def semantic_schema_blockers(columns: dict[str, list[str]]) -> list[dict[str, object]]:
    required = {
        DTN_SEMANTIC_OBJECTS["service"]: ("SERVICE_ID",),
        DTN_SEMANTIC_OBJECTS["content_position"]: (),
        DTN_SEMANTIC_OBJECTS["content_connection_point"]: ("CONTENT", "CONNPT_INT_ID"),
        DTN_SEMANTIC_OBJECTS["connection_cabling_point"]: ("CONNPT_INT_ID", "CABPT_INT_ID"),
        DTN_SEMANTIC_OBJECTS["cabling"]: ("A_CABPT_INT_ID", "B_CABPT_INT_ID"),
    }
    blockers: list[dict[str, object]] = []
    for object_name, required_columns in required.items():
        available = set(columns.get(object_name, []))
        missing = [column for column in required_columns if column not in available]
        if object_name not in columns or missing:
            blockers.append(
                {
                    "label": "semantic_probe_schema",
                    "object_name": object_name,
                    "missing_columns": missing,
                    "reason": "required object or column unavailable",
                }
            )
    return blockers


def semantic_fetch_service_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    object_name = DTN_SEMANTIC_OBJECTS["service"]
    selected = semantic_select_list("", columns[object_name])
    sql = (
        f"SELECT {selected} FROM {qualified_object(state.config.database, state.config.schema, object_name)} "
        f"WHERE {quote_identifier('SERVICE_ID')} = %s ORDER BY {quote_identifier('SERVICE_ID')} "
        "LIMIT %s OFFSET %s"
    )
    result = execute_rows(
        cursor,
        sql,
        (state.config.service_id, state.config.probe_sample_row_limit, 0),
        state,
        "write_dtn_semantic_probe",
        "semantic_service_transmission_fetch",
    )
    query_notes.append({"label": "service_transmission", "query_id": result.query_id})
    return result.rows


def semantic_seed_values(rows: list[dict[str, object]], columns: list[str]) -> list[str]:
    seed_columns = set(semantic_id_columns(columns))
    values = {
        semantic_text(value)
        for row in rows
        for column, value in row.items()
        if column in seed_columns and semantic_text(value)
    }
    return sorted(values)


def semantic_id_columns(columns: list[str]) -> list[str]:
    names = []
    for column in columns:
        if (
            column.endswith("_INT_ID")
            or column.endswith("_IDENTITY")
            or column in {"CONTENT", "TRANSMISSION_INTID", "SERVICE_ID"}
        ):
            names.append(column)
    return names


def semantic_fetch_content_position_seed_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    seed_values: list[str],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    object_name = DTN_SEMANTIC_OBJECTS["content_position"]
    selected = semantic_select_list("", columns[object_name])
    rows: list[dict[str, object]] = []
    seen_hashes: set[str] = set()
    for seed_value in seed_values:
        for column in semantic_id_columns(columns[object_name]):
            fetched = semantic_fetch_exact_text_rows(
                cursor,
                state,
                object_name,
                selected,
                column,
                seed_value,
                state.config.probe_sample_row_limit,
                f"semantic_cp_seed_{column}",
                query_notes,
            )
            for row in fetched:
                row_hash = stable_hash(json.dumps(row, sort_keys=True, default=str))
                if row_hash not in seen_hashes:
                    seen_hashes.add(row_hash)
                    rows.append(row)
    return rows


def semantic_content_candidates(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    cp_rows: list[dict[str, object]],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in cp_rows[: state.config.probe_sample_row_limit]:
        for column, value in sorted(row.items()):
            text = semantic_text(value)
            if not text or text in seen:
                continue
            seen.add(text)
            count, query_id = semantic_count_ccp_content(cursor, state, columns, text)
            query_notes.append({"label": f"ccp_content_candidate_{column}", "query_id": query_id})
            if 0 < count <= state.config.semantic_fetch_row_limit:
                candidates.append(
                    {
                        "_value": text,
                        "source_column": column,
                        "value_digest": stable_digest(text),
                        "device_count": count,
                    }
                )
    return candidates


def semantic_count_ccp_content(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    value: str,
) -> tuple[int, str]:
    object_name = DTN_SEMANTIC_OBJECTS["content_connection_point"]
    extra = semantic_ccp_device_filter(columns[object_name])
    sql = (
        "SELECT COUNT(*) AS MATCH_COUNT "
        f"FROM {qualified_object(state.config.database, state.config.schema, object_name)} "
        f"WHERE TO_VARCHAR({quote_identifier('CONTENT')}) = %s{extra}"
    )
    return execute_count(
        cursor, sql, (value,), state, "write_dtn_semantic_probe", "semantic_ccp_content_count"
    )


def semantic_fetch_device_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    candidates: list[dict[str, object]],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    object_name = DTN_SEMANTIC_OBJECTS["content_connection_point"]
    selected = semantic_device_select(columns)
    for candidate in candidates:
        value = str(candidate.get("_value", ""))
        if not value:
            continue
        extra = semantic_ccp_device_filter(columns[object_name], alias="ccp")
        sql = (
            f"SELECT {selected} FROM {semantic_device_from_clause(state, columns)} "
            f"WHERE TO_VARCHAR(ccp.{quote_identifier('CONTENT')}) = %s{extra} "
            "ORDER BY ccp."
            f"{quote_identifier('CONNPT_INT_ID')} LIMIT %s OFFSET %s"
        )
        result = execute_rows(
            cursor,
            sql,
            (value, state.config.semantic_fetch_row_limit, 0),
            state,
            "write_dtn_semantic_probe",
            "semantic_ccp_device_fetch",
        )
        query_notes.append({"label": "ccp_device_fetch", "query_id": result.query_id})
        rows.extend(result.rows)
    return rows


def semantic_select_list(alias: str, columns: list[str]) -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(f"{prefix}{quote_identifier(column)}" for column in columns)


def semantic_prefixed_select(alias: str, columns: list[str], prefix: str) -> str:
    return ", ".join(
        f"{alias}.{quote_identifier(column)} AS {quote_identifier(f'{prefix}__{column}')}"
        for column in columns
    )


def semantic_fetch_exact_text_rows(
    cursor: object,
    state: RunState,
    object_name: str,
    selected: str,
    column: str,
    value: str,
    row_limit: int,
    logical_name: str,
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    count_sql = (
        "SELECT COUNT(*) AS MATCH_COUNT "
        f"FROM {qualified_object(state.config.database, state.config.schema, object_name)} "
        f"WHERE TO_VARCHAR({quote_identifier(column)}) = %s"
    )
    count, count_query_id = execute_count(
        cursor, count_sql, (value,), state, "write_dtn_semantic_probe", f"{logical_name}_count"
    )
    query_notes.append(
        {"label": f"{logical_name}_count", "query_id": count_query_id, "count": count}
    )
    if count <= 0 or count > row_limit:
        return []
    fetch_sql = (
        f"SELECT {selected} "
        f"FROM {qualified_object(state.config.database, state.config.schema, object_name)} "
        f"WHERE TO_VARCHAR({quote_identifier(column)}) = %s "
        f"ORDER BY {quote_identifier(column)} LIMIT %s OFFSET %s"
    )
    result = execute_rows(
        cursor,
        fetch_sql,
        (value, row_limit, 0),
        state,
        "write_dtn_semantic_probe",
        f"{logical_name}_fetch",
    )
    query_notes.append({"label": f"{logical_name}_fetch", "query_id": result.query_id})
    return result.rows


def semantic_ccp_device_filter(ccp_columns: list[str], alias: str = "") -> str:
    if "NE" in ccp_columns:
        prefix = f"{alias}." if alias else ""
        return f" AND {prefix}{quote_identifier('NE')} IS NOT NULL"
    return ""


def semantic_device_select(columns: dict[str, list[str]]) -> str:
    ccp_columns = [
        column
        for column in columns[DTN_SEMANTIC_OBJECTS["content_connection_point"]]
        if column
        in {
            "CONTENT",
            "CONTENT_INT_ID",
            "CONNPT_INT_ID",
            "NE",
            "NE_PART",
            "SITE_CODE",
            "SLOT",
            "SUBSLOT",
            "CONNECTION_POINT_NR",
            "CONNECTION_POINT_TYPE",
            "PORT_TYPE",
        }
    ]
    selected = [semantic_prefixed_select("ccp", ccp_columns, "CCP")]
    ne_part_columns = columns.get(DTN_SEMANTIC_OBJECTS["ne_part"], [])
    if semantic_ne_part_join_allowed(columns):
        selected.append(
            semantic_prefixed_select(
                "nep",
                [
                    column
                    for column in ne_part_columns
                    if column
                    in {
                        "NE",
                        "NE_PART_NAME",
                        "NEPART_SITE_CODE",
                        "NE_TYPE",
                        "NE_PART_TYPE",
                        "MODEL",
                        "TECHNOLOGY",
                    }
                ],
                "NEP",
            )
        )
    return ", ".join(part for part in selected if part)


def semantic_device_from_clause(state: RunState, columns: dict[str, list[str]]) -> str:
    ccp_object = qualified_object(
        state.config.database, state.config.schema, DTN_SEMANTIC_OBJECTS["content_connection_point"]
    )
    if not semantic_ne_part_join_allowed(columns):
        return f"{ccp_object} ccp"
    nep_object = qualified_object(
        state.config.database, state.config.schema, DTN_SEMANTIC_OBJECTS["ne_part"]
    )
    return (
        f"{ccp_object} ccp LEFT JOIN {nep_object} nep "
        f"ON ccp.{quote_identifier('NE')} = nep.{quote_identifier('NE')} "
        f"AND ccp.{quote_identifier('NE_PART')} = nep.{quote_identifier('NE_PART_NAME')}"
    )


def semantic_ne_part_join_allowed(columns: dict[str, list[str]]) -> bool:
    ccp_columns = set(columns.get(DTN_SEMANTIC_OBJECTS["content_connection_point"], []))
    nep_columns = set(columns.get(DTN_SEMANTIC_OBJECTS["ne_part"], []))
    return {"NE", "NE_PART"} <= ccp_columns and {"NE", "NE_PART_NAME"} <= nep_columns


def semantic_row_matches_target(row: dict[str, object], config: LiveConfig) -> bool:
    site = semantic_text(row.get("NEP__NEPART_SITE_CODE")) or semantic_text(
        row.get("CCP__SITE_CODE")
    )
    haystack = " ".join(semantic_text(value) for value in row.values())
    return semantic_site_matches_target(site, config.semantic_site_code) and (
        config.semantic_device_token.upper() in haystack.upper()
    )


def semantic_site_matches_target(site: str, target_site: str) -> bool:
    normalized = target_site.strip().upper()
    if normalized in {"", "*", "ANY", "ALL"}:
        return True
    return site.upper() == normalized


def semantic_fetch_cacp_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    connpt_ids: list[str],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    object_name = DTN_SEMANTIC_OBJECTS["connection_cabling_point"]
    return semantic_fetch_in_rows(
        cursor,
        state,
        object_name,
        columns[object_name],
        "CONNPT_INT_ID",
        connpt_ids,
        "semantic_dtn_cacp_by_connpt",
        query_notes,
    )


def semantic_fetch_cabling_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    cabpt_ids: list[str],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not cabpt_ids:
        return []
    object_name = DTN_SEMANTIC_OBJECTS["cabling"]
    placeholders = semantic_placeholders(len(cabpt_ids))
    selected = semantic_select_list("", columns[object_name])
    where_sql = (
        f"TO_VARCHAR({quote_identifier('A_CABPT_INT_ID')}) IN ({placeholders}) "
        f"OR TO_VARCHAR({quote_identifier('B_CABPT_INT_ID')}) IN ({placeholders})"
    )
    return semantic_fetch_where_rows(
        cursor,
        state,
        object_name,
        selected,
        where_sql,
        (*cabpt_ids, *cabpt_ids),
        "semantic_dtn_cabling_by_cabpt",
        query_notes,
    )


def semantic_peer_cabpt_ids(
    cabpt_ids: list[str], cabling_rows: list[dict[str, object]]
) -> list[str]:
    ids = set(cabpt_ids)
    for row in cabling_rows:
        for column in ("A_CABPT_INT_ID", "B_CABPT_INT_ID"):
            value = semantic_text(row.get(column))
            if value:
                ids.add(value)
    return sorted(ids)


def semantic_fetch_peer_cacp_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    cabpt_ids: list[str],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    object_name = DTN_SEMANTIC_OBJECTS["connection_cabling_point"]
    return semantic_fetch_in_rows(
        cursor,
        state,
        object_name,
        columns[object_name],
        "CABPT_INT_ID",
        cabpt_ids,
        "semantic_cabling_peer_cacp_by_cabpt",
        query_notes,
    )


def semantic_fetch_in_rows(
    cursor: object,
    state: RunState,
    object_name: str,
    columns: list[str],
    column: str,
    values: list[str],
    logical_name: str,
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not values:
        return []
    selected = semantic_select_list("", columns)
    placeholders = semantic_placeholders(len(values))
    where_sql = f"TO_VARCHAR({quote_identifier(column)}) IN ({placeholders})"
    return semantic_fetch_where_rows(
        cursor, state, object_name, selected, where_sql, tuple(values), logical_name, query_notes
    )


def semantic_fetch_where_rows(
    cursor: object,
    state: RunState,
    object_name: str,
    selected: str,
    where_sql: str,
    params: tuple[object, ...],
    logical_name: str,
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    qualified = qualified_object(state.config.database, state.config.schema, object_name)
    count, count_query_id = execute_count(
        cursor,
        f"SELECT COUNT(*) AS MATCH_COUNT FROM {qualified} WHERE {where_sql}",
        params,
        state,
        "write_dtn_semantic_probe",
        f"{logical_name}_count",
    )
    query_notes.append(
        {"label": f"{logical_name}_count", "query_id": count_query_id, "count": count}
    )
    if count <= 0 or count > state.config.semantic_fetch_row_limit:
        return []
    result = execute_rows(
        cursor,
        f"SELECT {selected} FROM {qualified} WHERE {where_sql} LIMIT %s OFFSET %s",
        (*params, state.config.semantic_fetch_row_limit, 0),
        state,
        "write_dtn_semantic_probe",
        f"{logical_name}_fetch",
    )
    query_notes.append({"label": f"{logical_name}_fetch", "query_id": result.query_id})
    return result.rows


def semantic_placeholders(count: int) -> str:
    return ", ".join("%s" for _ in range(count))


def dtn_semantic_probe_payload(
    state: RunState,
    rows: Mapping[str, list[dict[str, object]]],
    seed_ids: Mapping[str, list[str]],
    content_candidates: list[dict[str, object]],
    blockers: list[dict[str, object]],
    query_notes: list[dict[str, object]],
) -> dict[str, object]:
    counts = semantic_counts(rows, content_candidates, blockers)
    safe_candidates = [
        {key: value for key, value in candidate.items() if key != "_value"}
        for candidate in content_candidates
    ]
    dwdm_context = semantic_dwdm_context(rows, seed_ids, query_notes, blockers, state.config)
    classification = dtn_relation_classification(state, counts, seed_ids, dwdm_context)
    snapshot = {
        "run_id": state.config.run_id,
        "service_id": state.config.service_id,
        "source": f"{state.config.database}.{state.config.schema}",
        "target": {
            "site_code": state.config.semantic_site_code,
            "device_token": state.config.semantic_device_token,
        },
        "raw_unrestricted_values_written": False,
        "content_candidates": safe_candidates,
        "counts": counts,
        "dwdm_adjacency": dwdm_context,
        "seed_ids": dict(seed_ids),
        "blockers": blockers,
        "query_notes": query_notes,
        "samples": {
            name: sanitize_sample_rows(sample_rows, state.config.probe_sample_row_limit)
            for name, sample_rows in rows.items()
        },
    }
    return {"snapshot": snapshot, "classification": classification}


def semantic_counts(
    rows: Mapping[str, list[dict[str, object]]],
    content_candidates: list[dict[str, object]],
    blockers: list[dict[str, object]],
) -> dict[str, int]:
    return {
        "service_transmission_rows": len(rows.get("service_transmission", [])),
        "cp_seed_rows": len(rows.get("content_position_seed", [])),
        "ccp_content_candidate_count": len(content_candidates),
        "device_rows_by_actual_bearer_content": len(
            rows.get("content_connection_point_devices", [])
        ),
        "ashr1_dtn_device_rows": len(rows.get("ashr1_dtn_device_rows", [])),
        "dtn_device_rows": len(rows.get("dtn_device_rows", [])),
        "dtn_cacp_rows": len(rows.get("dtn_cacp_rows", [])),
        "blank_cabpt_rows": semantic_blank_cabpt_count(rows.get("dtn_cacp_rows", [])),
        "nonblank_cabpt_rows": semantic_nonblank_cabpt_count(rows.get("dtn_cacp_rows", [])),
        "dtn_cabling_rows": len(rows.get("dtn_cabling_rows", [])),
        "cabling_peer_cacp_rows": len(rows.get("cabling_peer_cacp_rows", [])),
        "distinct_site_count": len(semantic_site_count_summary(rows.get("dtn_device_rows", []))),
        "blocker_count": len(blockers),
    }


def semantic_blank_cabpt_count(rows: list[dict[str, object]]) -> int:
    return sum(1 for row in rows if not semantic_text(row.get("CABPT_INT_ID")))


def semantic_nonblank_cabpt_count(rows: list[dict[str, object]]) -> int:
    return sum(1 for row in rows if semantic_text(row.get("CABPT_INT_ID")))


def semantic_site_count_summary(rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        site = semantic_text(row.get("NEP__NEPART_SITE_CODE")) or semantic_text(
            row.get("CCP__SITE_CODE")
        )
        key = site or "<blank>"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def semantic_dwdm_context(
    rows: Mapping[str, list[dict[str, object]]],
    seed_ids: Mapping[str, list[str]],
    query_notes: list[dict[str, object]],
    blockers: list[dict[str, object]],
    config: LiveConfig,
) -> dict[str, object]:
    dtn_cabpt_ids = [value for value in seed_ids.get("CABPT_INT_ID", []) if value]
    usable_pair_count = semantic_usable_endpoint_pair_count(
        dtn_cabpt_ids,
        rows.get("dtn_cabling_rows", []),
        rows.get("cabling_peer_cacp_rows", []),
    )
    counts = semantic_counts(rows, [], blockers)
    fanout_limit_exceeded = semantic_fanout_limit_exceeded(query_notes, config)
    decision = semantic_dwdm_decision(counts, usable_pair_count, fanout_limit_exceeded)
    return {
        "decision": decision,
        "usable_endpoint_pair_count": usable_pair_count,
        "fanout_limit_exceeded": fanout_limit_exceeded,
        "site_count_summary": semantic_site_count_summary(rows.get("dtn_device_rows", [])),
        "decision_reason": semantic_dwdm_decision_reason(
            counts, usable_pair_count, fanout_limit_exceeded, decision
        ),
    }


def semantic_usable_endpoint_pair_count(
    dtn_cabpt_ids: list[str],
    cabling_rows: list[dict[str, object]],
    peer_cacp_rows: list[dict[str, object]],
) -> int:
    dtn_ids = set(dtn_cabpt_ids)
    peer_ids = {
        semantic_text(row.get("CABPT_INT_ID"))
        for row in peer_cacp_rows
        if semantic_text(row.get("CABPT_INT_ID"))
    } - dtn_ids
    pairs: set[tuple[str, str]] = set()
    for row in cabling_rows:
        a_id = semantic_text(row.get("A_CABPT_INT_ID"))
        b_id = semantic_text(row.get("B_CABPT_INT_ID"))
        if not a_id or not b_id or a_id == b_id:
            continue
        if (a_id in dtn_ids and b_id in peer_ids) or (b_id in dtn_ids and a_id in peer_ids):
            first_id, second_id = sorted((a_id, b_id))
            pairs.add((first_id, second_id))
    return len(pairs)


def semantic_fanout_limit_exceeded(
    query_notes: list[dict[str, object]], config: LiveConfig
) -> bool:
    return any(
        int_or_zero(note.get("count")) > config.semantic_fetch_row_limit
        for note in query_notes
        if "count" in note
    )


def semantic_dwdm_decision(
    counts: Mapping[str, int], usable_pair_count: int, fanout_limit_exceeded: bool
) -> str:
    if fanout_limit_exceeded:
        return "OWNER_APPROVAL_REQUIRED"
    if counts.get("blocker_count", 0) > 0:
        return "INCOMPLETE"
    if (
        counts.get("dtn_device_rows", 0) > 0
        and counts.get("nonblank_cabpt_rows", 0) > 0
        and counts.get("dtn_cabling_rows", 0) > 0
        and counts.get("cabling_peer_cacp_rows", 0) > 0
        and usable_pair_count > 0
    ):
        return "PROVEN_DWDM_ADJACENCY"
    if counts.get("dtn_device_rows", 0) > 0 and (
        counts.get("nonblank_cabpt_rows", 0) == 0 or counts.get("dtn_cabling_rows", 0) == 0
    ):
        return "TRANSMISSION_ONLY_FANOUT"
    return "INCOMPLETE"


def semantic_dwdm_decision_reason(
    counts: Mapping[str, int],
    usable_pair_count: int,
    fanout_limit_exceeded: bool,
    decision: str,
) -> str:
    if fanout_limit_exceeded:
        return "fanout exceeds bounded semantic fetch limit"
    if decision == "PROVEN_DWDM_ADJACENCY":
        return f"nonblank CABPT + cabling + peer CACP proven; usable_endpoint_pair_count={usable_pair_count}"
    if decision == "TRANSMISSION_ONLY_FANOUT":
        return "DTN rows found but nonblank CABPT/cabling path not proven"
    if counts.get("blocker_count", 0) > 0:
        return "required object or column unavailable"
    return "bounded probe did not prove cabling-backed DTN adjacency"


def dtn_relation_classification(
    state: RunState,
    counts: Mapping[str, int],
    seed_ids: Mapping[str, list[str]],
    dwdm_context: Mapping[str, object],
) -> dict[str, object]:
    found = {
        "service_transmission": counts.get("service_transmission_rows", 0) > 0,
        "content_position_seed": counts.get("cp_seed_rows", 0) > 0,
        "content_connection_point_devices": counts.get("device_rows_by_actual_bearer_content", 0)
        > 0,
        "ashr1_dtn_content_connection_point": counts.get("ashr1_dtn_device_rows", 0) > 0,
        "connection_cabling_point": counts.get("dtn_cacp_rows", 0) > 0,
        "cabling": counts.get("dtn_cabling_rows", 0) > 0,
        "cabling_peer_cacp": counts.get("cabling_peer_cacp_rows", 0) > 0,
    }
    return {
        "run_id": state.config.run_id,
        "service_id": state.config.service_id,
        "seed_ids": dict(seed_ids),
        "counts": dict(counts),
        "dwdm_adjacency": dict(dwdm_context),
        "candidate_relation_sources_found": found,
        "business_proof": {
            "dtn_endpoint_relation_proof": INCOMPLETE,
            "route_order_proof": INCOMPLETE,
            "sorter_change_allowed": False,
            "negative_evidence_allowed": False,
            "reason": (
                "Candidate exact-ID and cabling rows exist only as candidate evidence; "
                "DTN edge semantics remain unapproved."
            ),
        },
        "recommended_next_action": (
            "Review or approve a DTN edge semantics registry entry before sorter changes."
        ),
    }


def semantic_probe_candidate_scan_pass(probe: dict[str, object] | None) -> bool:
    if not probe:
        return False
    if "services" in probe:
        services = probe.get("services", [])
        if not isinstance(services, list):
            return False
        return any(
            semantic_service_decision(service) == "PROVEN_DWDM_ADJACENCY"
            for service in services
            if isinstance(service, dict)
        )
    classification = probe.get("classification", {})
    if not isinstance(classification, dict):
        return False
    sources = classification.get("candidate_relation_sources_found", {})
    return isinstance(sources, dict) and all(bool(value) for value in sources.values())


def semantic_service_decision(probe: dict[str, object]) -> str:
    classification = probe.get("classification", {})
    if not isinstance(classification, dict):
        return "INCOMPLETE"
    dwdm = classification.get("dwdm_adjacency", {})
    if not isinstance(dwdm, dict):
        return "INCOMPLETE"
    decision = str(dwdm.get("decision", "INCOMPLETE"))
    return decision if decision in DWDM_ADJACENCY_DECISIONS else "INCOMPLETE"


def dwdm_adjacency_service_summary(
    state: RunState, probes: list[dict[str, object]]
) -> dict[str, object]:
    services = [dwdm_service_summary_row(probe) for probe in probes]
    return {
        "run_id": state.config.run_id,
        "source": f"{state.config.database}.{state.config.schema}",
        "service_ids": [row["service_id"] for row in services],
        "target": {
            "site_code": state.config.semantic_site_code,
            "device_token": state.config.semantic_device_token,
        },
        "raw_unrestricted_values_written": False,
        "snapshot_only": True,
        "business_proof": {
            "route_order_proof": INCOMPLETE,
            "sorter_change_allowed": False,
            "negative_evidence_allowed": False,
            "reason": "Six-service DWDM snapshot is candidate evidence until semantics review.",
        },
        "services": services,
    }


def dwdm_service_summary_row(probe: dict[str, object]) -> dict[str, object]:
    snapshot = probe.get("snapshot", {})
    classification = probe.get("classification", {})
    if not isinstance(snapshot, dict) or not isinstance(classification, dict):
        return {"service_id": "", "decision": "INCOMPLETE"}
    counts = classification.get("counts", {})
    dwdm = classification.get("dwdm_adjacency", {})
    if not isinstance(counts, dict):
        counts = {}
    if not isinstance(dwdm, dict):
        dwdm = {}
    return {
        "service_id": str(classification.get("service_id", snapshot.get("service_id", ""))),
        "decision": str(dwdm.get("decision", "INCOMPLETE")),
        "decision_reason": str(dwdm.get("decision_reason", "")),
        "service_rows": int_or_zero(counts.get("service_transmission_rows")),
        "content_position_seed_rows": int_or_zero(counts.get("cp_seed_rows")),
        "dtn_device_rows": int_or_zero(counts.get("dtn_device_rows")),
        "dtn_cacp_rows": int_or_zero(counts.get("dtn_cacp_rows")),
        "blank_cabpt_rows": int_or_zero(counts.get("blank_cabpt_rows")),
        "nonblank_cabpt_rows": int_or_zero(counts.get("nonblank_cabpt_rows")),
        "dtn_cabling_rows": int_or_zero(counts.get("dtn_cabling_rows")),
        "peer_cacp_rows": int_or_zero(counts.get("cabling_peer_cacp_rows")),
        "distinct_site_count": int_or_zero(counts.get("distinct_site_count")),
        "site_count_summary": dwdm.get("site_count_summary", {}),
        "usable_endpoint_pair_count": int_or_zero(dwdm.get("usable_endpoint_pair_count")),
        "fanout_limit_exceeded": bool(dwdm.get("fanout_limit_exceeded")),
    }


def dwdm_adjacency_decision_matrix(
    state: RunState, service_summary: Mapping[str, object]
) -> dict[str, object]:
    raw_services = service_summary.get("services", [])
    services = (
        [row for row in raw_services if isinstance(row, dict)]
        if isinstance(raw_services, list)
        else []
    )
    decision_counts: dict[str, int] = {}
    for row in services:
        decision = str(row.get("decision", "INCOMPLETE"))
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
    return {
        "run_id": state.config.run_id,
        "generated_at": utc_now(),
        "decision_counts": decision_counts,
        "total_service_count": len(services),
        "proven_service_ids": [
            row.get("service_id")
            for row in services
            if row.get("decision") == "PROVEN_DWDM_ADJACENCY"
        ],
        "transmission_only_fanout_service_ids": [
            row.get("service_id")
            for row in services
            if row.get("decision") == "TRANSMISSION_ONLY_FANOUT"
        ],
        "owner_approval_required_service_ids": [
            row.get("service_id")
            for row in services
            if row.get("decision") == "OWNER_APPROVAL_REQUIRED"
        ],
        "incomplete_service_ids": [
            row.get("service_id") for row in services if row.get("decision") == "INCOMPLETE"
        ],
        "recommended_next_action": dwdm_recommended_next_action(decision_counts),
        "sorter_change_allowed": False,
        "negative_evidence_allowed": False,
    }


def dwdm_recommended_next_action(decision_counts: Mapping[str, int]) -> str:
    if decision_counts.get("OWNER_APPROVAL_REQUIRED", 0):
        return "REQUEST_OWNER_APPROVAL"
    if decision_counts.get("INCOMPLETE", 0):
        return "STOP_EXACT_BLOCKER"
    if decision_counts.get("PROVEN_DWDM_ADJACENCY", 0):
        return "REVIEW_SEMANTICS_BEFORE_SORTER_CHANGE"
    return "NO_DEEP_FETCH_JUSTIFIED"


def dwdm_predicate_probe_snapshots(
    state: RunState, service_summary: Mapping[str, object]
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    raw_services = service_summary.get("services", [])
    services = raw_services if isinstance(raw_services, list) else []
    for row in services:
        if not isinstance(row, dict):
            continue
        service_id = str(row.get("service_id", ""))
        exact_hit_count = int_or_zero(row.get("dtn_device_rows"))
        snapshots.append(
            predicate_probe_snapshot_payload(
                run_id=state.config.run_id,
                source_namespace=f"{state.config.database}.{state.config.schema}",
                object_name="DWDM_ADJACENCY_SERVICE_SUMMARY",
                predicate_field="SERVICE_ID",
                predicate_domain="DWDM_ADJACENCY",
                predicate_ref=stable_digest(service_id),
                exact_hit_count=exact_hit_count,
                sample_rows=[],
                count_request_id="",
                sample_request_id="",
                elapsed_ms=0,
                row_limit_used=state.config.probe_sample_row_limit,
                limits=probe_limits(state.config),
                error=""
                if row.get("decision") != "INCOMPLETE"
                else str(row.get("decision_reason")),
            )
        )
    return snapshots


def semantic_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        return text[:-2]
    return text


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
    if seed_scan_started(state):
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
    if state.semantic_probe is not None:
        candidate_status = (
            PASS if semantic_probe_candidate_scan_pass(state.semantic_probe) else INCOMPLETE
        )
        set_status(
            statuses,
            "Candidate relation scan",
            candidate_status,
            "bounded DTN semantic candidate probe completed",
            [],
        )
        set_status(
            statuses,
            "Edge semantics registry",
            INCOMPLETE,
            "DTN edge semantics not reviewed or approved",
            [],
        )
    else:
        set_status(
            statuses, "Candidate relation scan", INCOMPLETE, "candidate scan not completed", []
        )
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
    record_exception_incomplete_area(state, phase, f"{reason}: {exc}")
    mark_statuses_incomplete(state, f"{reason}: {exc}")
    state.run_manifest["run_status"] = INCOMPLETE
    state.run_manifest["completed_at"] = utc_now()
    state.run_manifest["incomplete_reason"] = f"{reason}: {exc}"
    state.run_manifest["negative_evidence_allowed"] = False
    write_json_artifact(state.run_dir / "run_manifest.json", state.run_manifest)
    write_json_artifact(state.run_dir / "status_split.json", state.status_split)
    mark_checkpoint_incomplete(state, phase, f"{reason}: {exc}")
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
    write_progress_from_checkpoint(state, phase, f"{reason}: {exc}")


def seed_scan_started(state: RunState) -> bool:
    return bool(
        state.seed_scan.searched_anchor_columns
        or state.seed_scan.seed_nodes
        or state.seed_scan.seed_rows
        or state.seed_scan.incomplete_areas
        or state.seed_scan.skipped_rows
    )


def mark_statuses_incomplete(state: RunState, reason: str) -> None:
    state.status_split = status_payload_for_state(state)
    statuses = state.status_split["statuses"]
    phase = str(state.run_manifest.get("current_phase", "unknown"))
    for name in status_names_for_incomplete_phase(phase):
        set_status(statuses, name, INCOMPLETE, reason, [])
    for name in (
        "Exact-ID overlap scan",
        "Evidence graph closure",
        "Candidate relation scan",
        "Edge semantics registry",
        "TM client-line relation proof",
        "Negative evidence ledger",
        "IC-388612 route order proof",
    ):
        set_status(statuses, name, INCOMPLETE, reason, [])
    set_status(statuses, "Sorter implementation change", "NOT_STARTED", "out of scope", [])


def status_names_for_incomplete_phase(phase: str) -> tuple[str, ...]:
    return {
        "initialize_run": ("INCA_SRC schema discovery",),
        "discover_schema_objects": ("INCA_SRC schema discovery",),
        "discover_schema_columns": ("INCA_SRC schema discovery",),
        "discover_views_metadata": ("INCA_SRC schema discovery",),
        "discover_dependencies_optional": ("Schema/profile catalog",),
        "write_schema_profile": ("Schema/profile catalog",),
        "write_probe_snapshots": ("Bounded JSON evidence snapshots",),
        "build_structured_id_dictionary": (
            "Manifest-boundary avoidance",
            "Structured ID dictionary",
        ),
        "extract_service_seed_ids": ("IC-388612 ID extraction",),
        "write_dtn_semantic_probe": ("Candidate relation scan", "Edge semantics registry"),
        "run_exact_id_overlap_scan": ("Exact-ID overlap scan", "Candidate relation scan"),
        "run_graph_closure": ("Evidence graph closure",),
        "write_final_status": ("Schema drift invalidation",),
    }.get(phase, ())


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
        "write_probe_snapshots": [
            "source_manifest.json",
            "profile_snapshots.jsonl",
            "predicate_probe_snapshots.jsonl",
            "probe_decision_matrix.json",
        ],
        "write_dtn_semantic_probe": [
            "dtn_semantic_probe_snapshot.json",
            "dtn_relation_classification.json",
            "dwdm_adjacency_service_summary.json",
            "dwdm_adjacency_decision_matrix.json",
        ],
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
        msg = f"internal deadline expired after {int(elapsed)} seconds during {phase}: {context}"
        mark_checkpoint_incomplete(state, phase, msg)
        raise InternalDeadlineExceededError(msg)


def write_checkpoint(state: RunState, phase: str, reason: str = "") -> None:
    payload = {
        "run_id": state.config.run_id,
        "phase": phase,
        "reason": reason,
        "updated_at": utc_now(),
        "current_node_key": "",
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
    discovered_node_keys: list[str] | None = None,
) -> None:
    payload = {
        "run_id": state.config.run_id,
        "phase": phase,
        "reason": "",
        "updated_at": utc_now(),
        "current_node_key": "" if node is None else node.key,
        "current_object": profile.object_name,
        "current_column": profile.column_name,
        "current_id_domain": "" if node is None else node.id_domain,
        "current_id_value": "" if node is None else node.value,
        "page_hash_window": "",
        "rows_expected": expected,
        "rows_fetched": fetched,
        "visited_predicate_keys": ["|".join(item) for item in sorted(visited_predicates or set())],
        "discovered_node_keys": sorted(discovered_node_keys or state.seed_scan.seed_nodes),
        "evidence_edge_hashes": [evidence_edge_hash(row) for row in state.graph_scan.evidence_rows],
    }
    write_json_artifact(state.run_dir / "checkpoint.json", payload)


def mark_checkpoint_incomplete(state: RunState, phase: str, reason: str) -> None:
    checkpoint = state.run_dir / "checkpoint.json"
    if not checkpoint.exists():
        write_checkpoint(state, phase, reason=reason)
        return
    payload = json_load_or_empty(checkpoint)
    if not payload:
        write_checkpoint(state, phase, reason=reason)
        return
    payload["phase"] = payload.get("phase") or phase
    payload["reason"] = reason
    payload["updated_at"] = utc_now()
    write_json_artifact(checkpoint, payload)


def json_load_or_empty(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def record_exception_incomplete_area(state: RunState, phase: str, reason: str) -> None:
    checkpoint = json_load_or_empty(state.run_dir / "checkpoint.json")
    area = incomplete_area_from_checkpoint(state, checkpoint, reason)
    if area is None:
        return
    if phase == "extract_service_seed_ids":
        state.seed_scan = SeedScanResult(
            state.seed_scan.seed_nodes,
            state.seed_scan.seed_rows,
            state.seed_scan.searched_anchor_columns,
            state.seed_scan.exact_anchor_hits,
            [*state.seed_scan.incomplete_areas, area],
            state.seed_scan.skipped_rows,
        )
    elif phase == "run_exact_id_overlap_scan":
        state.graph_scan = GraphScanResult(
            state.graph_scan.evidence_rows,
            state.graph_scan.exact_hits,
            state.graph_scan.coverage_rows,
            [*state.graph_scan.incomplete_areas, area],
            state.graph_scan.skipped_rows,
        )


def incomplete_area_from_checkpoint(
    state: RunState, checkpoint: dict[str, object], reason: str
) -> IncompleteArea | None:
    object_name = str(checkpoint.get("current_object", ""))
    column_name = str(checkpoint.get("current_column", ""))
    if not object_name or not column_name:
        return None
    return IncompleteArea(
        object_name=object_name,
        column_name=column_name,
        id_node_key=str(checkpoint.get("current_node_key", "")),
        expected_row_count=int_or_zero(checkpoint.get("rows_expected")),
        fetched_row_count=int_or_zero(checkpoint.get("rows_fetched")),
        page_size=state.config.page_size,
        attempted_mitigations=(
            "count-first exact predicate",
            "pagination checkpoint",
            "internal deadline before bridge timeout",
        ),
        stop_reason=reason,
        resume_checkpoint=str(state.run_dir / "checkpoint.json"),
    )


def int_or_zero(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def refresh_graph_scan_state(
    state: RunState | None,
    evidence_rows: list[EvidenceRow],
    exact_hits: list[dict[str, object]],
    coverage_rows: list[dict[str, object]],
    incomplete_areas: list[IncompleteArea],
    skipped_rows: list[dict[str, object]],
) -> None:
    if state is None:
        return
    state.graph_scan = GraphScanResult(
        evidence_rows, exact_hits, coverage_rows, incomplete_areas, skipped_rows
    )


def write_progress_summary(
    state: RunState,
    phase: str,
    *,
    current_object: str = "",
    current_column: str = "",
    current_node_key: str = "",
    pass_number: int = 0,
    visited_predicate_count: int = 0,
    known_node_count: int | None = None,
    rows_expected: int = 0,
    rows_fetched: int = 0,
    reason: str = "",
) -> None:
    payload = {
        "run_id": state.config.run_id,
        "service_id": state.config.service_id,
        "phase": phase,
        "updated_at": utc_now(),
        "current_object": current_object,
        "current_column": current_column,
        "current_node_key": current_node_key,
        "pass_number": pass_number,
        "visited_predicate_count": visited_predicate_count,
        "seed_node_count": len(state.seed_scan.seed_nodes),
        "known_node_count": known_node_count
        if known_node_count is not None
        else len(state.seed_scan.seed_nodes),
        "exact_hit_count": len(state.graph_scan.exact_hits),
        "coverage_row_count": len(state.graph_scan.coverage_rows),
        "evidence_row_count": len(state.graph_scan.evidence_rows),
        "incomplete_area_count": len(
            [*state.seed_scan.incomplete_areas, *state.graph_scan.incomplete_areas]
        ),
        "rows_expected": rows_expected,
        "rows_fetched": rows_fetched,
        "negative_evidence_allowed": False,
        "reason": reason,
    }
    write_json_artifact(state.run_dir / "progress_summary.json", payload)


def write_progress_from_checkpoint(state: RunState, phase: str, reason: str) -> None:
    checkpoint = json_load_or_empty(state.run_dir / "checkpoint.json")
    write_progress_summary(
        state,
        phase,
        current_object=str(checkpoint.get("current_object", "")),
        current_column=str(checkpoint.get("current_column", "")),
        current_node_key=str(checkpoint.get("current_node_key", "")),
        visited_predicate_count=json_list_count(checkpoint.get("visited_predicate_keys")),
        known_node_count=json_list_count(checkpoint.get("discovered_node_keys")),
        rows_expected=int_or_zero(checkpoint.get("rows_expected")),
        rows_fetched=int_or_zero(checkpoint.get("rows_fetched")),
        reason=reason,
    )


def json_list_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def append_csv_row(path: Path, fieldnames: tuple[str, ...], row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def write_jsonl_artifact(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True))
            handle.write("\n")


def write_source_manifest(state: RunState) -> None:
    write_json_artifact(
        state.run_dir / "source_manifest.json",
        source_manifest_payload(
            run_id=state.config.run_id,
            source_kind="snowflake",
            source_name=f"{state.config.database}.{state.config.schema}",
            auth_context={
                "connection_name": state.config.connection_name,
                "secret_material_written": False,
                "semantic_service_ids": list(state.config.semantic_service_ids),
            },
            tool_version=state.config.framework_commit_sha,
            limits=probe_limits(state.config),
        ),
    )


def write_profile_snapshots(state: RunState) -> None:
    objects = object_rows_by_name(state.metadata.get("tables", []))
    fields_by_object: dict[str, list[dict[str, object]]] = defaultdict(list)
    for profile in state.profiles:
        fields_by_object[profile.object_name].append(
            {
                "name": profile.column_name,
                "data_type": profile.data_type,
                "numeric_scale": profile.numeric_scale,
                "is_nullable": profile.is_nullable,
                "ordinal_position": profile.ordinal_position,
            }
        )
    snapshots = [
        profile_snapshot_payload(
            run_id=state.config.run_id,
            source_namespace=f"{state.config.database}.{state.config.schema}",
            object_name=object_name,
            object_type=str(objects.get(object_name, {}).get("TABLE_TYPE", "")),
            row_count=optional_int(objects.get(object_name, {}).get("ROW_COUNT")),
            fields=fields,
        )
        for object_name, fields in sorted(fields_by_object.items())
    ]
    write_jsonl_artifact(state.run_dir / "profile_snapshots.jsonl", snapshots)


def object_rows_by_name(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(row.get("TABLE_NAME", "")): row for row in rows}


def optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def append_command_log(state: RunState, text: str) -> None:
    with (state.run_dir / "command_log.sql").open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write(text)
        handle.flush()


def empty_seed_scan() -> SeedScanResult:
    return SeedScanResult({}, [], 0, 0, [], [])


def empty_graph_scan() -> GraphScanResult:
    return GraphScanResult([], [], [], [], [])


def load_route_seed_scan(config: LiveConfig) -> SeedScanResult:
    if config.route_seed_id_bag is None:
        area = IncompleteArea(
            object_name="ROUTE_SEED_ID_BAG",
            column_name="",
            id_node_key="",
            expected_row_count=1,
            fetched_row_count=0,
            page_size=config.page_size,
            attempted_mitigations=("provide --route-seed-id-bag",),
            stop_reason="seed_mode route-bag requires --route-seed-id-bag",
            resume_checkpoint="",
        )
        return SeedScanResult({}, [], 0, 0, [area], [])
    payload = json_load_or_empty(config.route_seed_id_bag)
    nodes, skipped_rows = route_seed_nodes_from_payload(config, payload)
    seed_rows = route_seed_rows(config, payload, nodes)
    return SeedScanResult(nodes, seed_rows, len(seed_rows), len(seed_rows), [], skipped_rows)


def route_seed_nodes_from_payload(
    config: LiveConfig, payload: dict[str, object]
) -> tuple[dict[str, IdNode], list[dict[str, object]]]:
    nodes: dict[str, IdNode] = {}
    skipped_rows: list[dict[str, object]] = []
    raw_nodes = payload.get("nodes", [])
    if not isinstance(raw_nodes, list):
        return nodes, skipped_rows
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        column_name = str(raw.get("domain", "")).strip()
        value = str(raw.get("value", "")).strip()
        if not column_name or not value:
            continue
        try:
            node = node_from_value(config.database, config.schema, column_name, value, "NUMBER")
        except ValueError as exc:
            skipped_rows.append(route_seed_skipped_row(config, column_name, str(exc)))
            continue
        nodes.setdefault(node.key, node)
    return nodes, skipped_rows


def route_seed_rows(
    config: LiveConfig, payload: dict[str, object], nodes: dict[str, IdNode]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    raw_nodes = payload.get("nodes", [])
    if not isinstance(raw_nodes, list):
        return rows
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        column_name = str(raw.get("domain", "")).strip()
        value = str(raw.get("value", "")).strip()
        if not column_name or not value:
            continue
        try:
            node = node_from_value(config.database, config.schema, column_name, value, "NUMBER")
        except ValueError:
            continue
        if node.key not in nodes:
            continue
        rows.append(
            {
                "run_id": config.run_id,
                "service_id": config.service_id,
                "object_name": "ROUTE_SEED_ID_BAG",
                "anchor_column": column_name,
                "match_count": 1,
                "row_hash": stable_hash(node.key),
                "query_id": "",
                "page_number": 1,
            }
        )
    return rows


def route_seed_skipped_row(
    config: LiveConfig, column_name: str, reason_detail: str
) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "object_name": "ROUTE_SEED_ID_BAG",
        "object_type": "ROUTE_SEED_ARTIFACT",
        "column_name": column_name,
        "skip_scope": "COLUMN",
        "skip_reason_code": "ROUTE_SEED_FIELD_NOT_PROOF_GRADE",
        "skip_reason_detail": reason_detail,
        "required_for_full_discovery": False,
        "causes_incomplete": False,
        "mitigation_attempted": "field skipped before graph node creation",
        "next_action": "add deterministic ID-domain rule only if this field is approved proof-grade",
    }


def merge_seed_scans(left: SeedScanResult, right: SeedScanResult) -> SeedScanResult:
    nodes = dict(left.seed_nodes)
    nodes.update(right.seed_nodes)
    return SeedScanResult(
        nodes,
        [*left.seed_rows, *right.seed_rows],
        left.searched_anchor_columns + right.searched_anchor_columns,
        left.exact_anchor_hits + right.exact_anchor_hits,
        [*left.incomplete_areas, *right.incomplete_areas],
        [*left.skipped_rows, *right.skipped_rows],
    )


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
