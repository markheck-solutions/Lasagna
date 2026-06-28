from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lasagna.snowflake.inca_src_discovery import (
    APPROVED_SEMANTICS,
    FAIL,
    INCOMPLETE,
    PASS,
    ColumnProfile,
    EvidenceRow,
    GoldenBlockerCase,
    GraphClosureResult,
    Searchability,
    SemanticRegistryRow,
    assert_full_inventory_before_candidate_classification,
    build_structured_id_dictionary_rows,
    build_views_metadata_query,
    classify_candidate_relation_tables,
    classify_structured_id_column,
    close_evidence_graph,
    derive_exact_overlap_status,
    derive_graph_closure_status,
    derive_negative_ledger_status,
    derive_tm_relation_status,
    evaluate_golden_corpus,
    failure_status_from_exception,
    fanout_action,
    fanout_incomplete_area,
    initial_semantics_registry_row,
    negative_evidence_allowed,
    node_from_value,
    path_may_prove_tm_relation,
    regression_status_for_corpus,
    schema_drift_report,
    status_split_template,
    tx_waveinfo_registry_row,
    view_metadata_gap_rows,
)

_COLLECTOR_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "work_pc" / "collect_inca_src_evidence.py"
)
_COLLECTOR_SPEC = importlib.util.spec_from_file_location(
    "inca_src_collector_under_test", _COLLECTOR_PATH
)
assert _COLLECTOR_SPEC is not None
assert _COLLECTOR_SPEC.loader is not None
collector: Any = importlib.util.module_from_spec(_COLLECTOR_SPEC)
sys.modules[_COLLECTOR_SPEC.name] = collector
_COLLECTOR_SPEC.loader.exec_module(collector)

SEARCHABLE = Searchability(
    searchable_status="SEARCHABLE",
    exact_predicate_supported=True,
    count_query_status=PASS,
)


class FakeCursor:
    def __init__(self) -> None:
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[object, ...]] = []
        self.sfqid = ""
        self.calls: list[str] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append(sql)
        self.sfqid = f"Q{len(self.calls)}"
        upper = sql.upper()
        if "CURRENT_ACCOUNT" in upper:
            self.description = [("CURRENT_ACCOUNT()",)]
            self._rows = [("ACCT",)]
        elif "COUNT(*) AS MATCH_COUNT" in upper:
            self.description = [("MATCH_COUNT",)]
            self._rows = [(0,)]
        elif "INFORMATION_SCHEMA.TABLES" in upper:
            self.description = [
                ("TABLE_CATALOG",),
                ("TABLE_SCHEMA",),
                ("TABLE_NAME",),
                ("TABLE_TYPE",),
            ]
            self._rows = [("PROD_ACCESS_DB", "INCA_SRC", "T_REL", "BASE TABLE")]
        elif "INFORMATION_SCHEMA.COLUMNS" in upper and "TABLE_NAME = 'VIEWS'" in upper:
            self.description = [("COLUMN_NAME",)]
            self._rows = [("TABLE_CATALOG",), ("TABLE_SCHEMA",), ("TABLE_NAME",), ("IS_SECURE",)]
        elif "INFORMATION_SCHEMA.COLUMNS" in upper:
            self.description = [
                ("TABLE_CATALOG",),
                ("TABLE_SCHEMA",),
                ("TABLE_NAME",),
                ("COLUMN_NAME",),
                ("ORDINAL_POSITION",),
                ("DATA_TYPE",),
                ("NUMERIC_SCALE",),
                ("IS_NULLABLE",),
            ]
            self._rows = [
                ("PROD_ACCESS_DB", "INCA_SRC", "T_REL", "CONTENT_INT_ID", 1, "NUMBER", 0, "YES")
            ]
        elif "INFORMATION_SCHEMA.VIEWS" in upper:
            self.description = [
                ("TABLE_CATALOG",),
                ("TABLE_SCHEMA",),
                ("TABLE_NAME",),
                ("IS_SECURE",),
            ]
            self._rows = []
        elif "OBJECT_DEPENDENCIES" in upper:
            self.description = [("REFERENCING_DATABASE",)]
            self._rows = []
        elif "SELECT 1" in upper:
            self.description = [("ONE",)]
            self._rows = [(1,)]
        else:
            self.description = []
            self._rows = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


def collector_args(tmp_path: Path, phase: str = "full", deadline: int = 1500) -> SimpleNamespace:
    return SimpleNamespace(
        service_id="IC-388612",
        database="PROD_ACCESS_DB",
        schema="INCA_SRC",
        connection="fake",
        output_root=tmp_path,
        query_tag="test",
        page_size=100,
        max_pages_per_predicate=25,
        phase=phase,
        internal_deadline_seconds=deadline,
        statement_timeout_seconds=120,
        run_dir=tmp_path / "run-test",
        resume=False,
        start_fresh=False,
    )


def column(name: str, data_type: str = "NUMBER", scale: int | None = 0) -> ColumnProfile:
    return ColumnProfile(
        database="PROD_ACCESS_DB",
        schema="INCA_SRC",
        object_name="RELATION_TABLE",
        object_type="BASE TABLE",
        column_name=name,
        ordinal_position=1,
        data_type=data_type,
        numeric_scale=scale,
        is_nullable="YES",
    )


def test_full_schema_discovery_runs_before_candidate_classification() -> None:
    with pytest.raises(RuntimeError, match="complete object and column inventory"):
        assert_full_inventory_before_candidate_classification(False, True)
    with pytest.raises(RuntimeError, match="complete object and column inventory"):
        classify_candidate_relation_tables(
            [],
            object_inventory_complete=True,
            column_inventory_complete=False,
        )


def test_views_metadata_query_uses_is_secure_not_invalid_secure() -> None:
    sql = build_views_metadata_query(
        "PROD_ACCESS_DB",
        "INCA_SRC",
        [
            "TABLE_CATALOG",
            "TABLE_SCHEMA",
            "TABLE_NAME",
            "IS_SECURE",
            "CREATED",
        ],
    )

    assert "IS_SECURE" in sql
    assert " SECURE" not in sql.replace("IS_SECURE", "")


def test_optional_view_metadata_column_absence_records_gap_not_route_evidence() -> None:
    gaps = view_metadata_gap_rows(["TABLE_CATALOG", "TABLE_SCHEMA", "TABLE_NAME"])
    is_secure_gap = [gap for gap in gaps if gap["missing_column"] == "IS_SECURE"]

    assert is_secure_gap
    assert is_secure_gap[0]["gap_scope"] == "OPTIONAL_METADATA_COLUMN"
    assert is_secure_gap[0]["causes_incomplete"] is False
    assert "node_key" not in is_secure_gap[0]
    assert "edge_hash" not in is_secure_gap[0]


def test_run_manifest_json_is_written_before_first_snowflake_query(tmp_path: Path) -> None:
    args = collector_args(tmp_path)
    state = collector.initialize_run(args)

    collector.phase_initialize_run(FakeCursor(), args, state)

    assert (state.run_dir / "run_manifest.json").exists()
    assert json.loads((state.run_dir / "run_manifest.json").read_text())["artifact_first"] is True


def test_status_split_json_is_written_before_first_snowflake_query(tmp_path: Path) -> None:
    args = collector_args(tmp_path)
    state = collector.initialize_run(args)

    collector.phase_initialize_run(FakeCursor(), args, state)

    assert (state.run_dir / "status_split.json").exists()
    assert (
        "Sorter implementation change"
        in json.loads((state.run_dir / "status_split.json").read_text())["statuses"]
    )


def test_metadata_gaps_csv_is_written_even_when_empty(tmp_path: Path) -> None:
    state = collector.initialize_run(collector_args(tmp_path))

    rows = list(csv.DictReader((state.run_dir / "metadata_gaps.csv").open()))

    assert rows == []
    assert "metadata_object" in (state.run_dir / "metadata_gaps.csv").read_text()


def test_graph_closure_summary_initialized_false(tmp_path: Path) -> None:
    state = collector.initialize_run(collector_args(tmp_path))
    payload = json.loads((state.run_dir / "graph_closure_summary.json").read_text())

    assert payload["fixed_point_reached"] is False
    assert payload["incomplete_area_count"] == 0


def test_timeout_during_schema_discovery_leaves_valid_partial_artifacts(
    tmp_path: Path,
) -> None:
    args = collector_args(tmp_path, deadline=0)
    state = collector.initialize_run(args)

    with pytest.raises(collector.InternalDeadlineExceededError):
        collector.run_phase(
            state,
            "discover_schema_objects",
            lambda: collector.phase_discover_schema_objects(FakeCursor(), state),
        )
    collector.mark_incomplete_after_exception(state, "timeout", TimeoutError("deadline"))

    assert (state.run_dir / "run_manifest.json").exists()
    assert (state.run_dir / "status_split.json").exists()
    assert (
        json.loads((state.run_dir / "status_split.json").read_text())["statuses"][
            "TM client-line relation proof"
        ]["status"]
        == INCOMPLETE
    )


def test_timeout_during_exact_id_scan_leaves_coverage_and_marks_incomplete(
    tmp_path: Path,
) -> None:
    args = collector_args(tmp_path, deadline=0)
    state = collector.initialize_run(args)

    with pytest.raises(collector.InternalDeadlineExceededError):
        collector.run_phase(
            state,
            "run_exact_id_overlap_scan",
            lambda: collector.check_deadline(state, "run_exact_id_overlap_scan", "test"),
        )
    collector.mark_incomplete_after_exception(state, "timeout", TimeoutError("deadline"))

    assert (state.run_dir / "coverage_matrix.csv").exists()
    statuses = json.loads((state.run_dir / "status_split.json").read_text())["statuses"]
    assert statuses["Exact-ID overlap scan"]["status"] == INCOMPLETE
    assert statuses["Evidence graph closure"]["status"] == INCOMPLETE


def test_external_interruption_after_initialization_does_not_leave_only_command_log(
    tmp_path: Path,
) -> None:
    state = collector.initialize_run(collector_args(tmp_path))

    names = {path.name for path in state.run_dir.iterdir()}

    assert "command_log.sql" in names
    assert {"run_manifest.json", "status_split.json", "coverage_matrix.csv"} <= names
    assert names != {"command_log.sql"}


def test_internal_deadline_expires_before_bridge_timeout_and_writes_incomplete(
    tmp_path: Path,
) -> None:
    args = collector_args(tmp_path, deadline=0)
    state = collector.initialize_run(args)

    with pytest.raises(collector.InternalDeadlineExceededError):
        collector.check_deadline(state, "run_exact_id_overlap_scan", "before bridge timeout")

    collector.mark_incomplete_after_exception(state, "deadline", TimeoutError("internal"))
    assert json.loads((state.run_dir / "run_manifest.json").read_text())["run_status"] == INCOMPLETE


def test_query_log_records_phase_logical_sql_hash_timestamps_and_status(
    tmp_path: Path,
) -> None:
    state = collector.initialize_run(collector_args(tmp_path))

    collector.execute_observed_rows(
        state, FakeCursor(), "SELECT 1", (), "initialize_run", "smoke_select"
    )

    rows = list(csv.DictReader((state.run_dir / "query_log.csv").open()))
    assert rows[-1]["phase"] == "initialize_run"
    assert rows[-1]["logical_query_name"] == "smoke_select"
    assert rows[-1]["sql_hash"]
    assert rows[-1]["started_at"]
    assert rows[-1]["completed_at"]
    assert rows[-1]["status"] == PASS


def test_metadata_only_mode_does_not_run_exact_id_scan_or_graph_closure(
    tmp_path: Path,
) -> None:
    args = collector_args(tmp_path, phase="metadata-only")
    state = collector.initialize_run(args)
    cursor = FakeCursor()

    collector.phase_initialize_run(cursor, args, state)
    collector.phase_discover_schema_objects(cursor, state)
    collector.phase_discover_schema_columns(cursor, state)
    collector.phase_discover_views_metadata(cursor, state)
    collector.phase_discover_dependencies_optional(cursor, state)
    collector.phase_write_schema_profile(state)
    collector.phase_build_structured_id_dictionary(state)
    collector.phase_write_final_status(state)

    query_log = (state.run_dir / "query_log.csv").read_text()
    assert "exact_count" not in query_log
    assert (
        json.loads((state.run_dir / "graph_closure_summary.json").read_text())[
            "fixed_point_reached"
        ]
        is False
    )


def test_seed_only_mode_does_not_run_graph_closure(tmp_path: Path) -> None:
    args = collector_args(tmp_path, phase="seed-only")
    state = collector.initialize_run(args)
    state.profiles = [column("CONTENT_INT_ID")]
    state.proof_by_object = {"RELATION_TABLE": [column("CONTENT_INT_ID")]}

    collector.phase_extract_service_seed_ids(FakeCursor(), state)
    collector.phase_write_final_status(state)

    query_log = (state.run_dir / "query_log.csv").read_text()
    assert "run_graph_closure" not in query_log
    assert (
        json.loads((state.run_dir / "graph_closure_summary.json").read_text())[
            "fixed_point_reached"
        ]
        is False
    )


def test_resume_mode_refuses_unsafe_resume_without_checkpoint(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-test"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text("{}", encoding="utf-8")
    args = collector_args(tmp_path)
    args.resume = True

    with pytest.raises(RuntimeError, match="checkpoint.json"):
        collector.initialize_run(args)


def test_negative_evidence_not_allowed_after_timeout_incomplete(tmp_path: Path) -> None:
    state = collector.initialize_run(collector_args(tmp_path))
    collector.mark_incomplete_after_exception(state, "timeout", TimeoutError("internal"))

    payload = json.loads((state.run_dir / "graph_closure_summary.json").read_text())
    assert payload["fixed_point_reached"] is False
    statuses = json.loads((state.run_dir / "status_split.json").read_text())["statuses"]
    assert statuses["Negative evidence ledger"]["status"] == INCOMPLETE


def test_manifest_and_current_sql_are_seed_sources_not_boundaries() -> None:
    known_manifest = column("CONTENT_INT_ID")
    synthetic = ColumnProfile(
        **{
            **known_manifest.__dict__,
            "object_name": "SYNTHETIC_NOT_IN_MANIFEST",
            "column_name": "CONNPT_INT_ID",
        }
    )
    rows = build_structured_id_dictionary_rows(
        "run",
        [known_manifest, synthetic],
        {
            ("RELATION_TABLE", "CONTENT_INT_ID"): SEARCHABLE,
            ("SYNTHETIC_NOT_IN_MANIFEST", "CONNPT_INT_ID"): SEARCHABLE,
        },
    )

    objects = {row["object_name"] for row in rows if row["feasibility_status"] == "FEASIBLE"}

    assert "SYNTHETIC_NOT_IN_MANIFEST" in objects


def test_feasible_structured_id_column_rules_are_deterministic() -> None:
    cases = {
        "CONTENT_INT_ID": "FEASIBLE",
        "CONNPT_INT_ID": "FEASIBLE",
        "CONN_POINT_INT_ID": "FEASIBLE",
        "CABPT_INT_ID": "FEASIBLE",
        "PARENT_TRAIL_ID": "FEASIBLE",
        "WAVELENGTH_CHANNEL_ID": "FEASIBLE",
        "SLOT": "EXCLUDED",
        "NE_PART_NAME": "EXCLUDED",
        "ROUTE_NAME": "EXCLUDED",
        "CREATED_BY": "EXCLUDED",
    }
    observed = {
        name: classify_structured_id_column(column(name), SEARCHABLE).feasibility_status
        for name in cases
    }

    assert observed == cases


def test_node_key_namespaces_same_value_by_id_domain() -> None:
    content = node_from_value("PROD_ACCESS_DB", "INCA_SRC", "CONTENT_INT_ID", 123, "NUMBER")
    connpt = node_from_value("PROD_ACCESS_DB", "INCA_SRC", "CONNPT_INT_ID", 123, "NUMBER")

    assert content.key != connpt.key
    assert content.key.endswith("|CONTENT_INT_ID|123")
    assert connpt.key.endswith("|CONNPT_INT_ID|123")


def test_context_only_fields_do_not_create_graph_nodes() -> None:
    assert classify_structured_id_column(column("CONNECTION_POINT_NR"), SEARCHABLE).id_domain == ""
    with pytest.raises(ValueError, match="not a proof-grade ID column"):
        node_from_value("PROD_ACCESS_DB", "INCA_SRC", "SLOT", "7", "VARCHAR")


def test_exact_id_overlap_creates_candidate_not_proof() -> None:
    registry = initial_semantics_registry_row(
        "RELATION_TABLE:CONTENT_CONNPT",
        "RELATION_TABLE",
        ["CONTENT_INT_ID", "CONNPT_INT_ID"],
        ["CONTENT_INT_ID", "CONNPT_INT_ID"],
    )

    assert registry.semantics_status == "UNKNOWN"
    assert not path_may_prove_tm_relation([registry])


def test_fixed_point_closure_runs_until_no_new_nodes_rows_edges() -> None:
    result = close_evidence_graph(
        ["A"],
        [
            EvidenceRow("T1", "r1", ("A", "B"), ("CONTENT_INT_ID", "CONNPT_INT_ID")),
            EvidenceRow("T2", "r2", ("B", "C"), ("CONNPT_INT_ID", "CABPT_INT_ID")),
        ],
    )

    assert result.fixed_point_reached
    assert result.pass_count == 3
    assert result.final_node_count == 3
    assert result.new_nodes_by_pass == (1, 1, 0)


def test_graph_loop_terminates_with_visited_predicates_and_row_hashes() -> None:
    result = close_evidence_graph(
        ["A"],
        [
            EvidenceRow("T1", "r1", ("A", "B"), ("A_ID", "B_ID")),
            EvidenceRow("T2", "r2", ("B", "A"), ("B_ID", "A_ID")),
        ],
    )

    assert result.fixed_point_reached
    assert result.pass_count <= 3
    assert result.edge_count == 2


def test_fanout_count_paginates_checkpoints_and_resumes() -> None:
    assert fanout_action(5, 10) == "FETCH_SINGLE_PAGE"
    assert fanout_action(11, 10) == "PAGINATE"


def test_fanout_marks_incomplete_only_after_failed_mitigation() -> None:
    area = fanout_incomplete_area(
        "RELATION_TABLE",
        "CONNPT_INT_ID",
        "PROD_ACCESS_DB.INCA_SRC|CONNPT_INT_ID|123",
        10_000,
        5_000,
        500,
        ["split batch smaller", "paginate by row hash"],
        "page count exceeds configured operational max and owner approval needed",
        "checkpoints/relation_table_connpt.json",
    )

    assert area.stop_reason.startswith("page count exceeds")
    assert area.attempted_mitigations == ("split batch smaller", "paginate by row hash")


def test_timeout_permission_truncation_become_incomplete_not_fail() -> None:
    assert failure_status_from_exception(TimeoutError("statement timeout")) == INCOMPLETE
    assert failure_status_from_exception(PermissionError("permission denied")) == INCOMPLETE
    assert failure_status_from_exception(RuntimeError("result truncation")) == INCOMPLETE


def test_tx_waveinfo_defaults_to_skipped_semantics_unproven() -> None:
    row = tx_waveinfo_registry_row()

    assert row.approval_status == "SKIPPED_SEMANTICS_UNPROVEN"
    assert not row.may_prove_route_continuity
    assert not row.may_prove_tm_client_line_relation


def test_unknown_semantics_path_cannot_change_sort_outcome() -> None:
    result = close_evidence_graph(
        ["A"],
        [
            EvidenceRow(
                "RELATION_TABLE",
                "r1",
                ("A", "B"),
                ("CONTENT_INT_ID", "CONNPT_INT_ID"),
                semantics_status="UNKNOWN",
            )
        ],
    )
    status = derive_tm_relation_status(result, result.accepted_path_count)

    assert result.unknown_semantics_path_count == 1
    assert result.accepted_path_count == 0
    assert status.status == FAIL


def test_negative_evidence_requires_fixed_point_and_no_incomplete_areas() -> None:
    complete = GraphClosureResult(True, 1, 1, 1, 0, 0, (0,), (0,), 0, (), 0, 0, 0)
    incomplete = GraphClosureResult(
        False,
        1,
        1,
        1,
        0,
        0,
        (0,),
        (0,),
        0,
        (fanout_incomplete_area("T", "C", "N", 2, 1, 1, ["paginate"], "timeout", "cp"),),
        0,
        0,
        0,
    )

    assert negative_evidence_allowed(complete, accepted_tm_proof_exists=False)
    assert not negative_evidence_allowed(incomplete, accepted_tm_proof_exists=False)


def test_negative_evidence_invalidated_by_schema_hash_drift() -> None:
    report = schema_drift_report({"schema_hash": "old"}, {"schema_hash": "new"})

    assert report["review_required"] is True
    assert report["changed_hashes"] == ["schema_hash"]


def test_negative_evidence_invalidated_by_structured_id_dictionary_hash_drift() -> None:
    report = schema_drift_report(
        {"structured_id_dictionary_hash": "old"},
        {"structured_id_dictionary_hash": "new"},
    )

    assert report["invalidation_required"] is True
    assert report["changed_hashes"] == ["structured_id_dictionary_hash"]


def test_negative_evidence_invalidated_by_edge_semantics_registry_hash_drift() -> None:
    report = schema_drift_report(
        {"edge_semantics_registry_hash": "old"},
        {"edge_semantics_registry_hash": "new"},
    )

    assert report["review_required"] is True
    assert report["changed_hashes"] == ["edge_semantics_registry_hash"]


def test_no_sorter_behavior_change_symbols_touched() -> None:
    planned_files = {
        "src/lasagna/snowflake/inca_src_discovery.py",
        "scripts/work_pc/collect_inca_src_evidence.py",
        "tests/test_inca_src_discovery.py",
    }

    assert not any("/route_sorting/" in path for path in planned_files)


def test_no_port_match_rule_change_symbols_touched() -> None:
    planned_files = {
        "src/lasagna/snowflake/inca_src_discovery.py",
        "scripts/work_pc/collect_inca_src_evidence.py",
        "tests/test_inca_src_discovery.py",
    }

    assert not any("PORT_MATCH_RULE" in path for path in planned_files)


def test_status_split_keeps_independent_statuses() -> None:
    split = status_split_template("run")
    statuses = split["statuses"]

    assert isinstance(statuses, dict)
    assert statuses["Sorter implementation change"]["status"] == "NOT_STARTED"
    assert "status" not in split
    assert len(statuses) > 10


def test_golden_corpus_missing_required_case_is_explicit() -> None:
    status, cases = evaluate_golden_corpus([])

    assert status == INCOMPLETE
    assert any(case.availability_status == "MISSING_REQUIRED_CASE" for case in cases)


def test_golden_corpus_fail_closed_cases_remain_fail_closed() -> None:
    cases = [
        golden_case("known_otm", "KNOWN_OTM_TM_FAIL_CLOSED", FAIL),
        golden_case("known_dtn", "KNOWN_DTN_FAIL_CLOSED", FAIL),
        golden_case("route_family", "KNOWN_SORTING_ROUTE_FAMILY", PASS),
        golden_case("ciena", "KNOWN_CIENA_G30_G40_ACCEPTED", PASS),
        golden_case("ic388612", "IC_388612", FAIL),
        golden_case("future_tm", "FUTURE_OWNER_CONFIRMED_TM_PASS", PASS),
    ]
    observed = {case.case_id: case.expected_status for case in cases}
    status = regression_status_for_corpus(cases, observed)

    assert status.status == PASS


def test_status_derivation_exact_overlap_and_graph_are_separate() -> None:
    overlap = derive_exact_overlap_status(complete=True, overlap_count=0)
    graph = derive_graph_closure_status(
        GraphClosureResult(True, 1, 1, 1, 0, 0, (0,), (0,), 0, (), 0, 0, 0),
        blocker_relevant_path_count=0,
    )
    ledger = derive_negative_ledger_status(
        accepted_tm_proof_exists=False,
        ledger_written=False,
        ledger_allowed=False,
        ledger_malformed=False,
        incomplete_areas_exist=True,
    )

    assert overlap.status == FAIL
    assert graph.status == FAIL
    assert ledger.status == INCOMPLETE


def test_approved_semantics_still_require_reviewer_approval() -> None:
    unapproved = SemanticRegistryRow(
        registry_key="key",
        source_object="RELATION_TABLE",
        source_columns=("CONTENT_INT_ID", "CONNPT_INT_ID"),
        edge_type="TM_CLIENT_LINE",
        connected_id_types=("CONTENT_INT_ID", "CONNPT_INT_ID"),
        required_columns=("CONTENT_INT_ID", "CONNPT_INT_ID"),
        allowed_cardinality="ONE_TO_ONE",
        semantics_status=next(iter(APPROVED_SEMANTICS)),
        may_prove_route_continuity=True,
        may_prove_tm_client_line_relation=True,
        evidence_basis="docs and joins",
        evidence_artifact="edge_semantics_registry.csv",
        reviewer="",
        approval_status="PENDING_REVIEW",
        approved_at="",
        invalidation_rule="registry change",
        notes="",
    )

    assert not path_may_prove_tm_relation([unapproved])


def golden_case(case_id: str, required_case_type: str, expected_status: str) -> GoldenBlockerCase:
    return GoldenBlockerCase(
        case_id=case_id,
        service_id=case_id,
        blocker_type="route_sort",
        expected_status=expected_status,
        required_case_type=required_case_type,
        availability_status="AVAILABLE",
        searched_sources=("local corpus",),
        unavailable_reason="",
        regression_impact="",
        owner_confirmation_required=False,
        evidence_artifacts=("artifact.json",),
    )
