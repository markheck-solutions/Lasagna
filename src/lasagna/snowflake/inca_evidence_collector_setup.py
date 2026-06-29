"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_evidence_collector_context import *  # noqa: F403

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .inca_evidence_collector_artifacts import write_command_log
    from .inca_evidence_collector_phases import run_collector_phases
    from .inca_evidence_collector_state import (
        empty_graph_scan,
        empty_seed_scan,
        mark_incomplete_after_exception,
        utc_now,
        write_checkpoint,
        write_progress_summary,
    )


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
        "--seed-mode",
        choices=("service-anchor", "route-bag", "service-anchor-plus-route-bag"),
        default="service-anchor",
        help="Choose IC seed source. route-bag uses a route-derived structured ID artifact.",
    )
    parser.add_argument("--route-seed-id-bag", type=Path, default=None)
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
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument(
        "--framework-commit",
        default=os.environ.get("LASAGNA_FRAMEWORK_COMMIT", ""),
    )
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
        seed_mode=args.seed_mode,
        route_seed_id_bag=Path(args.route_seed_id_bag) if args.route_seed_id_bag else None,
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
