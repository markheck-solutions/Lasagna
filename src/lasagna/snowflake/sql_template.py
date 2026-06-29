"""Render Lasagna explicit-service-ID Snowflake SQL."""

from __future__ import annotations

from pathlib import Path

SERVICE_VALUES_PLACEHOLDER = "/* LASAGNA_SERVICE_VALUES */"
SQL_TEMPLATE_PATH = Path(__file__).with_name("explicit_service_route_extract.sql")


def snowflake_string_literal(value: str) -> str:
    """Return a Snowflake single-quoted string literal."""
    return "'" + value.replace("\x00", "").replace("'", "''") + "'"


def render_service_values(service_ids: list[str]) -> str:
    """Render Snowflake VALUES rows for normalized service IDs."""
    if not service_ids:
        raise ValueError("At least one valid service ID is required.")
    return ",\n".join(f"    ({snowflake_string_literal(service_id)})" for service_id in service_ids)


def render_explicit_service_route_sql(
    service_ids: list[str],
    template_path: Path | None = None,
) -> str:
    """Render SQL using explicit IC/ICB service IDs only."""
    path = template_path or SQL_TEMPLATE_PATH
    template = path.read_text(encoding="utf-8")
    if SERVICE_VALUES_PLACEHOLDER not in template:
        raise ValueError(f"Missing {SERVICE_VALUES_PLACEHOLDER} in SQL template: {path}")
    return template.replace(SERVICE_VALUES_PLACEHOLDER, render_service_values(service_ids))
