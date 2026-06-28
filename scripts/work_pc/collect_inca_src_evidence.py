"""Collect sanitized INCA_SRC evidence artifacts with read-only Snowflake queries."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from lasagna.snowflake.export import connect_with_connection_name
from lasagna.snowflake.inca_src_discovery import (
    DEFAULT_DATABASE,
    DEFAULT_SCHEMA,
    STRUCTURED_ID_DICTIONARY_COLUMNS,
    build_structured_id_dictionary_rows,
    execute_metadata_queries,
    object_type_map,
    profiles_from_information_schema_columns,
    render_command_log,
    stable_json_hash,
    status_split_template,
    write_csv_artifact,
    write_json_artifact,
    write_required_empty_artifacts,
)

DEFAULT_OUTPUT_ROOT = Path.home() / "Desktop" / "LasagnaRouteReviews" / "inca-src-discovery"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build sanitized deterministic INCA_SRC discovery artifacts."
    )
    parser.add_argument("--service-id", default="IC-388612")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--connection", default="default")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--query-tag", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")
    run_dir = args.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    command_log = render_command_log(args.database, args.schema)
    (run_dir / "command_log.sql").write_text(command_log, encoding="utf-8")
    connection = connect_with_connection_name(args.connection)
    try:
        cursor = connection.cursor()
        try:
            apply_session_controls(cursor, args.query_tag or run_id)
            metadata = execute_metadata_queries(cursor, args.database, args.schema)
        finally:
            cursor.close()
    finally:
        connection.close()
    write_metadata_artifacts(run_dir, run_id, args.service_id, metadata)
    return 0


def apply_session_controls(cursor: object, query_tag: str) -> None:
    escaped_tag = query_tag.replace("'", "''")
    execute = getattr(cursor, "execute")
    execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 300")
    execute(f"ALTER SESSION SET QUERY_TAG = 'LASAGNA_INCA_SRC_DISCOVERY:{escaped_tag}'")


def write_metadata_artifacts(
    run_dir: Path,
    run_id: str,
    service_id: str,
    metadata: dict[str, list[dict[str, object]]],
) -> None:
    objects = metadata.get("tables", [])
    columns = metadata.get("columns", [])
    views = metadata.get("views", [])
    dependencies = metadata.get("dependencies", [])
    write_json_artifact(
        run_dir / "run_manifest.json",
        {
            "run_id": run_id,
            "service_id": service_id,
            "database": DEFAULT_DATABASE,
            "schema": DEFAULT_SCHEMA,
            "sanitized": True,
            "raw_row_exports": False,
        },
    )
    write_csv_artifact(run_dir / "schema_objects.csv", csv_headers(objects), objects)
    write_csv_artifact(run_dir / "schema_columns.csv", csv_headers(columns), columns)
    write_csv_artifact(run_dir / "object_counts.csv", csv_headers(objects), objects)
    write_csv_artifact(run_dir / "dependencies.csv", csv_headers(dependencies), dependencies)
    profiles = profiles_from_information_schema_columns(columns, object_type_map(objects))
    dictionary_rows = build_structured_id_dictionary_rows(run_id, profiles)
    write_csv_artifact(
        run_dir / "structured_id_dictionary.csv",
        STRUCTURED_ID_DICTIONARY_COLUMNS,
        dictionary_rows,
    )
    write_csv_artifact(
        run_dir / "candidate_tables.csv",
        ("run_id", "object_name", "candidate_reason", "id_domains", "feasible_column_count"),
        (),
    )
    write_csv_artifact(
        run_dir / "candidate_column_reasons.csv",
        ("run_id", "object_name", "column_name", "reason"),
        (),
    )
    write_json_artifact(
        run_dir / "ic388612_id_bag.json",
        {"service_id": service_id, "seed_nodes": [], "status": "NOT_RUN"},
    )
    write_json_artifact(run_dir / "join_paths.json", {"paths": []})
    write_json_artifact(
        run_dir / "negative_evidence_ledger_entry.json",
        {"service_id": service_id, "negative_evidence_allowed": False, "status": "NOT_RUN"},
    )
    write_json_artifact(
        run_dir / "schema_drift_invalidation_report.json",
        {
            "schema_hash": stable_json_hash(
                {"objects": objects, "columns": columns, "views": views}
            ),
            "review_required": False,
        },
    )
    write_json_artifact(run_dir / "golden_blocker_corpus.json", {"cases": []})
    write_json_artifact(run_dir / "golden_blocker_results.json", {"status": "NOT_RUN"})
    write_required_empty_artifacts(run_dir, run_id, service_id)
    write_json_artifact(run_dir / "status_split.json", status_split_template(run_id))
    (run_dir / "README.md").write_text(readme_text(service_id), encoding="utf-8")


def readme_text(service_id: str) -> str:
    return (
        "# INCA_SRC Evidence Discovery Run\n\n"
        f"Service: `{service_id}`\n\n"
        "This folder contains sanitized schema and framework artifacts only. "
        "It contains no raw full-table dumps and no sorter implementation changes.\n"
    )


def csv_headers(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return []
    return list(rows[0].keys())


if __name__ == "__main__":
    raise SystemExit(main())
