"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_evidence_collector_context import *  # noqa: F403


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
