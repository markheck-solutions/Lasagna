"""INCA_SRC metadata SQL helpers."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_src_discovery_context import *  # noqa: F403


def required_metadata_queries(
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
) -> dict[str, str]:
    quoted_database = quote_identifier(database)
    literal_schema = sql_literal(schema)
    return {
        "session_context": (
            "SELECT CURRENT_ACCOUNT(), CURRENT_REGION(), CURRENT_ROLE(), "
            "CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA(), "
            "CURRENT_USER(), CURRENT_TIMESTAMP()"
        ),
        "tables": (
            "SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, IS_TRANSIENT, "
            "ROW_COUNT, BYTES, CREATED, LAST_ALTERED "
            f"FROM {quoted_database}.INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = {literal_schema} "
            "ORDER BY TABLE_NAME"
        ),
        "columns": (
            "SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, "
            "DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, "
            "IS_NULLABLE, COLUMN_DEFAULT, COMMENT "
            f"FROM {quoted_database}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = {literal_schema} "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        ),
        "views_available_columns": (
            "SELECT COLUMN_NAME "
            f"FROM {quoted_database}.INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'INFORMATION_SCHEMA' "
            "AND TABLE_NAME = 'VIEWS' "
            "ORDER BY ORDINAL_POSITION"
        ),
        "dependencies": (
            "SELECT REFERENCING_DATABASE, REFERENCING_SCHEMA, REFERENCING_OBJECT_NAME, "
            "REFERENCING_OBJECT_DOMAIN, REFERENCED_DATABASE, REFERENCED_SCHEMA, "
            "REFERENCED_OBJECT_NAME, REFERENCED_OBJECT_DOMAIN "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
            f"WHERE (REFERENCING_DATABASE = {sql_literal(database)} "
            f"AND REFERENCING_SCHEMA = {literal_schema}) "
            f"OR (REFERENCED_DATABASE = {sql_literal(database)} "
            f"AND REFERENCED_SCHEMA = {literal_schema})"
        ),
    }


def build_views_metadata_query(
    database: str,
    schema: str,
    available_columns: Iterable[str],
) -> str:
    normalized = {column.upper() for column in available_columns}
    missing_required = [column for column in VIEW_REQUIRED_COLUMNS if column not in normalized]
    if missing_required:
        joined = ", ".join(missing_required)
        msg = f"INFORMATION_SCHEMA.VIEWS missing required columns: {joined}"
        raise RuntimeError(msg)
    selected = [
        column
        for column in (*VIEW_REQUIRED_COLUMNS, *VIEW_OPTIONAL_COLUMNS)
        if column in normalized
    ]
    return (
        f"SELECT {', '.join(selected)} "
        f"FROM {quote_identifier(database)}.INFORMATION_SCHEMA.VIEWS "
        f"WHERE TABLE_SCHEMA = {sql_literal(schema)} "
        "ORDER BY TABLE_NAME"
    )


def view_metadata_gap_rows(
    available_columns: Iterable[str],
    *,
    discovery_status: str = PASS,
    discovery_error: str = "",
) -> list[dict[str, object]]:
    normalized = {column.upper() for column in available_columns}
    rows: list[dict[str, object]] = []
    if discovery_status != PASS:
        rows.append(
            {
                "metadata_object": "INFORMATION_SCHEMA.VIEWS",
                "gap_scope": "COLUMN_DISCOVERY",
                "missing_column": "",
                "required": False,
                "causes_incomplete": False,
                "reason": discovery_error,
            }
        )
    for column in VIEW_OPTIONAL_COLUMNS:
        if column not in normalized:
            rows.append(
                {
                    "metadata_object": "INFORMATION_SCHEMA.VIEWS",
                    "gap_scope": "OPTIONAL_METADATA_COLUMN",
                    "missing_column": column,
                    "required": False,
                    "causes_incomplete": False,
                    "reason": "optional metadata column unavailable",
                }
            )
    return rows


def quote_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def sql_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_json_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return stable_hash(encoded)
