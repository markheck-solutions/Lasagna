"""Execute Lasagna Snowflake route export."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from lasagna.domain.service_ids import parse_service_id_text, unique_valid_service_ids
from lasagna.snowflake.sql_template import render_explicit_service_route_sql


def _import_snowflake_connector() -> Any:
    import snowflake.connector

    return snowflake.connector


def connect_with_connection_name(connection: str) -> Any:
    """Connect through a named Snowflake connector profile."""
    connector = _import_snowflake_connector()
    return connector.connect(
        connection_name=connection,
        authenticator="externalbrowser",
        client_store_temporary_credential=True,
        session_parameters={"CLIENT_TELEMETRY_ENABLED": False},
    )


def _cursor_description_names(cursor: Any) -> list[str]:
    names: list[str] = []
    for meta in getattr(cursor, "description", []) or []:
        name = getattr(meta, "name", None)
        if name is None:
            name = meta[0]
        names.append(str(name).upper())
    return names


def _final_export_rows(cursor: Any) -> list[tuple[Any, Any]] | None:
    if _cursor_description_names(cursor)[:2] != ["QID", "ROW_DATA"]:
        return None
    return [(row[0], row[1]) for row in cursor.fetchall()]


def execute_combined_export(conn: Any, sql_text: str) -> list[tuple[Any, Any]]:
    """Execute rendered SQL and return final QID/ROW_DATA rows."""
    cursors = list(conn.execute_string(sql_text))
    final_rows: list[tuple[Any, Any]] | None = None
    try:
        for cursor in cursors:
            rows = _final_export_rows(cursor)
            if rows is not None:
                final_rows = rows
    finally:
        for cursor in cursors:
            cursor.close()
    if final_rows is None:
        raise RuntimeError("Lasagna Snowflake export did not return QID,ROW_DATA.")
    return final_rows


def write_combined_csv(path: Path, rows: Iterable[tuple[Any, Any]]) -> int:
    """Write combined export rows to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["QID", "ROW_DATA"])
        for qid, row_data in rows:
            writer.writerow([qid, row_data])
            count += 1
    return count


def export_service_ids_to_combined_csv(
    service_ids: list[str],
    output_path: Path,
    *,
    connection: str = "sdm_runner",
    generated_sql_path: Path | None = None,
) -> int:
    """Run Snowflake export for explicit service IDs and write combined CSV."""
    sql_text = render_explicit_service_route_sql(service_ids)
    if generated_sql_path is not None:
        generated_sql_path.parent.mkdir(parents=True, exist_ok=True)
        generated_sql_path.write_text(sql_text, encoding="utf-8")
    conn = connect_with_connection_name(connection)
    try:
        rows = execute_combined_export(conn, sql_text)
    finally:
        conn.close()
    return write_combined_csv(output_path, rows)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Lasagna route rows from Snowflake.")
    parser.add_argument("--service-id", action="append", default=[])
    parser.add_argument("--ids-text", default="")
    parser.add_argument("--connection", default="sdm_runner")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generated-sql", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    parsed_inputs = parse_service_id_text(" ".join([*args.service_id, args.ids_text]))
    service_ids = unique_valid_service_ids(parsed_inputs)
    row_count = export_service_ids_to_combined_csv(
        service_ids,
        args.output,
        connection=args.connection,
        generated_sql_path=args.generated_sql,
    )
    print(f"Lasagna Snowflake export wrote {row_count} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
