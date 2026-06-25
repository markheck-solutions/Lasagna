"""Sort combined Snowflake QID/ROW_DATA exports into workbook service results."""

from __future__ import annotations

from pathlib import Path

from lasagna.domain.route_models import ServiceRouteResult
from lasagna.route_sorting.adapter import build_site_location_lookup, route_rows_from_inca
from lasagna.route_sorting.inca_sorter.parsers import read_snowflake_combined_csv
from lasagna.route_sorting.inca_sorter.sorting import sort_inca_route_path


def _route_order_source(info_lines: list[str]) -> str:
    if "Route order: ROUTE_ORDER_METADATA" in info_lines:
        return "ROUTE_ORDER_METADATA"
    return "LEGACY_FALLBACK"


def _bearer_message(info_lines: list[str]) -> str:
    for line in info_lines:
        if line.startswith("Bearer: "):
            return line.removeprefix("Bearer: ").strip()
    return ""


def sort_combined_csv_to_service_results(
    combined_csv_path: Path,
    service_ids: list[str],
) -> dict[str, ServiceRouteResult]:
    """Sort Snowflake combined export rows for the requested service IDs."""
    combined_data = read_snowflake_combined_csv(str(combined_csv_path))
    site_locations = build_site_location_lookup(
        combined_data.hub_records,
        combined_data.route_order_metadata,
    )
    results: dict[str, ServiceRouteResult] = {}

    for service_id in service_ids:
        rows = combined_data.services.get(service_id, [])
        if not rows:
            results[service_id] = ServiceRouteResult.no_data(service_id)
            continue
        try:
            sorted_result = sort_inca_route_path(
                rows,
                service_id=service_id,
                snowflake_edge_records=combined_data.edge_records,
                tl_device_records=combined_data.tl_device_records,
                trunk_metadata_records=combined_data.trunk_metadata,
                route_order_metadata_records=combined_data.route_order_metadata,
                transmission_metadata_records=combined_data.transmission_metadata,
                hub_records=combined_data.hub_records,
                bo_fibers=combined_data.bo_fibers,
            )
        except Exception as exc:
            results[service_id] = ServiceRouteResult.sort_failed(service_id, str(exc))
            continue

        results[service_id] = ServiceRouteResult.ok(
            service_id,
            route_rows_from_inca(sorted_result.rows, site_locations),
            route_rows_from_inca(sorted_result.migration_portion or [], site_locations),
            route_order_source=_route_order_source(sorted_result.info_lines),
            message=_bearer_message(sorted_result.info_lines),
        )

    return results
