"""Adapt owned INCA sorter rows into Lasagna workbook rows."""

from __future__ import annotations

from typing import Any

from lasagna.domain.route_models import RouteRow
from lasagna.route_sorting.inca_sorter.models import InCARow


def _text(value: object) -> str:
    return "" if value is None else str(value)


def build_site_location_lookup(
    hub_records: list[dict[str, Any]] | None,
    route_order_metadata: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Build site code to location ID lookup from metadata records."""
    lookup: dict[str, str] = {}
    for record in hub_records or []:
        site_code = _text(record.get("SITE_CODE")).strip()
        location_id = _text(record.get("SITE_LOCATION_ID")).strip()
        if site_code and location_id:
            lookup.setdefault(site_code, location_id)
    for record in route_order_metadata or []:
        for site_key, location_key in (
            ("A_SITE_CODE", "A_SITE_LOCATION_ID"),
            ("B_SITE_CODE", "B_SITE_LOCATION_ID"),
        ):
            site_code = _text(record.get(site_key)).strip()
            location_id = _text(record.get(location_key)).strip()
            if site_code and location_id:
                lookup[site_code] = location_id
    return lookup


def route_row_from_inca(row: InCARow, site_locations: dict[str, str] | None = None) -> RouteRow:
    """Convert one owned sorter row into exact Lasagna workbook columns."""
    return RouteRow(
        location_id=(site_locations or {}).get(row.site_code, ""),
        site_code=row.site_code,
        site_type=row.site_type,
        site_type_no=row.site_type_no,
        ne_information=row.ne_info or "",
        cabling_location=row.cabling_location,
        cabling_points=row.display_points or row.cabling_points,
        conn_type=row.conn_type,
        location_alias=row.location_alias or "",
        pcg_pos_nwp_id="",
        route_path=row.route_path,
        pos="" if row.pos == 0 else str(row.pos),
        prot="",
        status_o_time=row.status_o_time or "",
        o_time=row.o_time or "",
        status_t_time=row.status_t_time or "",
        t_time=row.t_time or "",
        comment=row.comment or "",
    )


def route_rows_from_inca(
    rows: list[InCARow],
    site_locations: dict[str, str] | None = None,
) -> tuple[RouteRow, ...]:
    """Convert sorter rows into workbook rows."""
    return tuple(route_row_from_inca(row, site_locations) for row in rows)
