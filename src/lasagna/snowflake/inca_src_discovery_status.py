"""Status, artifact, ledger, and execution helpers for INCA_SRC discovery."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_src_discovery_classification import classify_structured_id_column
from .inca_src_discovery_context import *  # noqa: F403
from .inca_src_discovery_graph import graph_closure_summary_payload, negative_evidence_allowed
from .inca_src_discovery_metadata import (
    build_views_metadata_query,
    required_metadata_queries,
    stable_json_hash,
    view_metadata_gap_rows,
)
from .inca_src_discovery_models import *  # noqa: F403


def negative_evidence_ledger_entry(
    service_id: str,
    blocker_type: str,
    missing_relation: str,
    endpoint_node_keys: Sequence[str],
    hashes: Mapping[str, str],
    searched_objects: Sequence[str],
    searched_columns: Sequence[str],
    skipped_objects: Sequence[str],
    skipped_columns: Sequence[str],
    result: GraphClosureResult,
    final_status_split: Mapping[str, object],
    accepted_tm_proof_exists: bool,
) -> dict[str, object]:
    return {
        "service_id": service_id,
        "blocker_type": blocker_type,
        "missing_relation": missing_relation,
        "endpoint_node_keys": list(endpoint_node_keys),
        "schema_hash": hashes.get("schema_hash", ""),
        "candidate_filter_hash": hashes.get("candidate_filter_hash", ""),
        "scan_coverage_hash": hashes.get("scan_coverage_hash", ""),
        "structured_id_dictionary_hash": hashes.get("structured_id_dictionary_hash", ""),
        "edge_semantics_registry_hash": hashes.get("edge_semantics_registry_hash", ""),
        "searched_objects": list(searched_objects),
        "searched_columns": list(searched_columns),
        "skipped_objects": list(skipped_objects),
        "skipped_columns": list(skipped_columns),
        "incomplete_areas": [asdict(area) for area in result.incomplete_areas],
        "positive_match_count": result.evidence_row_count,
        "accepted_path_count": result.accepted_path_count,
        "rejected_path_count": result.rejected_path_count,
        "unknown_semantics_path_count": result.unknown_semantics_path_count,
        "fixed_point_reached": result.fixed_point_reached,
        "negative_evidence_allowed": negative_evidence_allowed(result, accepted_tm_proof_exists),
        "final_status_split": dict(final_status_split),
        "invalidation_rule": "invalidate on any hash drift or registry change",
    }


def schema_drift_report(
    previous_hashes: Mapping[str, str],
    current_hashes: Mapping[str, str],
) -> dict[str, object]:
    changed = sorted(
        key for key, value in current_hashes.items() if previous_hashes.get(key) != value
    )
    return {
        "review_required": bool(changed),
        "changed_hashes": changed,
        "previous_hashes": dict(previous_hashes),
        "current_hashes": dict(current_hashes),
        "invalidation_required": bool(changed),
    }


def missing_required_case(
    required_case_type: str,
    searched_sources: Sequence[str],
    unavailable_reason: str,
    regression_impact: str,
) -> GoldenBlockerCase:
    return GoldenBlockerCase(
        case_id=f"MISSING_REQUIRED_CASE:{required_case_type}",
        service_id="",
        blocker_type="",
        expected_status="",
        required_case_type=required_case_type,
        availability_status="MISSING_REQUIRED_CASE",
        searched_sources=tuple(searched_sources),
        unavailable_reason=unavailable_reason,
        regression_impact=regression_impact,
        owner_confirmation_required=True,
        evidence_artifacts=(),
    )


def evaluate_golden_corpus(
    cases: Sequence[GoldenBlockerCase],
) -> tuple[str, list[GoldenBlockerCase]]:
    by_type = {case.required_case_type: case for case in cases}
    normalized = list(cases)
    for required_type in REQUIRED_CASE_TYPES:
        if required_type not in by_type:
            normalized.append(
                missing_required_case(
                    required_type,
                    (),
                    "Required case was not found in configured corpus sources.",
                    "Regression completeness is incomplete.",
                )
            )
    has_missing = any(case.availability_status == "MISSING_REQUIRED_CASE" for case in normalized)
    return (INCOMPLETE if has_missing else PASS), normalized


def regression_status_for_corpus(
    cases: Sequence[GoldenBlockerCase],
    observed_status_by_case_id: Mapping[str, str],
) -> StatusEntry:
    corpus_status, normalized = evaluate_golden_corpus(cases)
    if corpus_status == INCOMPLETE:
        return StatusEntry(INCOMPLETE, "Golden corpus has missing required cases.", ())
    failures = [
        case.case_id
        for case in normalized
        if observed_status_by_case_id.get(case.case_id) != case.expected_status
    ]
    if failures:
        return StatusEntry(
            FAIL, "Available corpus case violated expected outcome.", tuple(failures)
        )
    return StatusEntry(PASS, "Corpus executed and expected outcomes held.", ())


def status_split_template(run_id: str) -> dict[str, object]:
    statuses: dict[str, dict[str, object]] = {}
    for name in STATUS_NAMES:
        if name == "Sorter implementation change":
            statuses[name] = {"status": NOT_STARTED, "reason": "out of scope", "evidence": []}
        else:
            statuses[name] = {"status": NOT_RUN, "reason": "not evaluated", "evidence": []}
    return {"run_id": run_id, "statuses": statuses}


def derive_exact_overlap_status(
    *,
    complete: bool,
    overlap_count: int,
    incomplete_reason: str = "",
) -> StatusEntry:
    if not complete:
        return StatusEntry(INCOMPLETE, incomplete_reason or "Searchable area incomplete.", ())
    if overlap_count == 0:
        return StatusEntry(FAIL, "Complete scan found zero exact ID overlap.", ())
    return StatusEntry(PASS, "All feasible columns scanned with exact ID overlap found.", ())


def derive_graph_closure_status(
    result: GraphClosureResult, blocker_relevant_path_count: int
) -> StatusEntry:
    if not result.fixed_point_reached:
        return StatusEntry(INCOMPLETE, "Evidence graph stopped before fixed point.", ())
    if blocker_relevant_path_count == 0:
        return StatusEntry(
            FAIL, "Fixed point reached with no relevant candidate or accepted path.", ()
        )
    return StatusEntry(PASS, "Fixed point reached.", ())


def derive_tm_relation_status(result: GraphClosureResult, accepted_path_count: int) -> StatusEntry:
    if not result.fixed_point_reached or result.incomplete_areas:
        return StatusEntry(INCOMPLETE, "Graph, scan, or semantics incomplete.", ())
    if accepted_path_count == 0:
        return StatusEntry(FAIL, "Complete fixed point found no accepted TM client-line path.", ())
    return StatusEntry(PASS, "Accepted path exists with approved semantics.", ())


def derive_negative_ledger_status(
    *,
    accepted_tm_proof_exists: bool,
    ledger_written: bool,
    ledger_allowed: bool,
    ledger_malformed: bool,
    incomplete_areas_exist: bool,
) -> StatusEntry:
    if accepted_tm_proof_exists:
        return StatusEntry(NOT_REQUIRED, "Accepted TM proof exists.", ())
    if ledger_malformed or (ledger_written and not ledger_allowed):
        return StatusEntry(FAIL, "Ledger written when not allowed or malformed.", ())
    if incomplete_areas_exist:
        return StatusEntry(INCOMPLETE, "Negative evidence desired but incomplete areas exist.", ())
    if ledger_written and ledger_allowed:
        return StatusEntry(PASS, "Complete negative entry written after fixed point.", ())
    return StatusEntry(INCOMPLETE, "Negative evidence entry not yet written.", ())


def derive_schema_drift_status(report: Mapping[str, object], hashes_available: bool) -> StatusEntry:
    if not hashes_available:
        return StatusEntry(INCOMPLETE, "Missing prior or current hash data blocks evaluation.", ())
    if report.get("review_required") and not report.get("invalidation_required"):
        return StatusEntry(FAIL, "Drift detected but not invalidated.", ())
    return StatusEntry(PASS, "All hashes computed and prior ledger evaluated.", ())


def failure_status_from_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if any(token in text for token in ("timeout", "permission", "truncat", "fanout")):
        return INCOMPLETE
    return FAIL


def write_csv_artifact(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json_artifact(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_required_empty_artifacts(run_dir: Path, run_id: str, service_id: str) -> None:
    write_csv_artifact(run_dir / "exact_match_hits.csv", EXACT_MATCH_HITS_COLUMNS, ())
    write_csv_artifact(run_dir / "evidence_edges.csv", EVIDENCE_EDGES_COLUMNS, ())
    write_csv_artifact(run_dir / "edge_semantics_registry.csv", EDGE_SEMANTICS_REGISTRY_COLUMNS, ())
    write_csv_artifact(run_dir / "coverage_matrix.csv", COVERAGE_MATRIX_COLUMNS, ())
    write_csv_artifact(run_dir / "skipped_objects.csv", SKIPPED_OBJECTS_COLUMNS, ())
    empty_result = GraphClosureResult(False, 0, 0, 0, 0, 0, (), (), 0, (), 0, 0, 0)
    write_json_artifact(
        run_dir / "graph_closure_summary.json",
        graph_closure_summary_payload(run_id, service_id, "", "", empty_result),
    )
    write_json_artifact(run_dir / "status_split.json", status_split_template(run_id))


def execute_metadata_query(cursor: Any, sql_text: str) -> tuple[list[str], list[dict[str, object]]]:
    cursor.execute(sql_text)
    column_names = [column[0] for column in cursor.description]
    rows = [dict(zip(column_names, row, strict=False)) for row in cursor.fetchall()]
    return column_names, rows


def execute_metadata_queries(
    cursor: Any,
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
) -> dict[str, list[dict[str, object]]]:
    outputs: dict[str, list[dict[str, object]]] = {}
    for name, sql_text in required_metadata_queries(database, schema).items():
        try:
            _columns, rows = execute_metadata_query(cursor, sql_text)
        except Exception as exc:
            if name == "views_available_columns":
                outputs["metadata_gaps"] = view_metadata_gap_rows(
                    VIEW_REQUIRED_COLUMNS,
                    discovery_status=INCOMPLETE,
                    discovery_error=str(exc),
                )
                outputs["views_available_columns"] = [
                    {"COLUMN_NAME": column} for column in VIEW_REQUIRED_COLUMNS
                ]
                continue
            if name != "dependencies":
                raise
            outputs[name] = [
                {
                    "dependency_metadata_status": INCOMPLETE,
                    "reason": str(exc),
                }
            ]
        else:
            outputs[name] = rows
    view_columns = [
        str(row["COLUMN_NAME"])
        for row in outputs.get("views_available_columns", [])
        if "COLUMN_NAME" in row
    ]
    outputs.setdefault("metadata_gaps", []).extend(view_metadata_gap_rows(view_columns))
    try:
        _columns, rows = execute_metadata_query(
            cursor,
            build_views_metadata_query(database, schema, view_columns),
        )
    except Exception as exc:
        outputs.setdefault("metadata_gaps", []).append(
            {
                "metadata_object": "INFORMATION_SCHEMA.VIEWS",
                "gap_scope": "VIEW_METADATA_QUERY",
                "missing_column": "",
                "required": True,
                "causes_incomplete": True,
                "reason": str(exc),
            }
        )
        outputs["views"] = []
    else:
        outputs["views"] = rows
    return outputs


def profiles_from_information_schema_columns(
    rows: Iterable[Mapping[str, object]],
    object_type_by_name: Mapping[str, str],
) -> list[ColumnProfile]:
    profiles: list[ColumnProfile] = []
    for row in rows:
        object_name = str(row["TABLE_NAME"])
        profiles.append(
            ColumnProfile(
                database=str(row["TABLE_CATALOG"]),
                schema=str(row["TABLE_SCHEMA"]),
                object_name=object_name,
                object_type=object_type_by_name.get(object_name, ""),
                column_name=str(row["COLUMN_NAME"]),
                ordinal_position=required_int(row["ORDINAL_POSITION"]),
                data_type=str(row["DATA_TYPE"]),
                numeric_scale=optional_int(row.get("NUMERIC_SCALE")),
                is_nullable=str(row["IS_NULLABLE"]),
            )
        )
    return profiles


def required_int(value: object) -> int:
    return int(Decimal(str(value)))


def optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(Decimal(str(value)))


def object_type_map(objects: Iterable[Mapping[str, object]]) -> dict[str, str]:
    return {str(row["TABLE_NAME"]): str(row.get("TABLE_TYPE", "")) for row in objects}


def render_command_log(database: str, schema: str) -> str:
    queries = required_metadata_queries(database, schema)
    return "\n\n".join(f"-- {name}\n{sql_text};" for name, sql_text in queries.items())
