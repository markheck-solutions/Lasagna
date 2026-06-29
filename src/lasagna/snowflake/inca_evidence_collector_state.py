"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_evidence_collector_context import *  # noqa: F403

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .inca_evidence_collector_artifacts import (
        metadata_has_incomplete_gap,
        registry_csv_rows,
        set_status,
        status_payload,
    )


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
        "build_structured_id_dictionary": (
            "Manifest-boundary avoidance",
            "Structured ID dictionary",
        ),
        "extract_service_seed_ids": ("IC-388612 ID extraction",),
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
