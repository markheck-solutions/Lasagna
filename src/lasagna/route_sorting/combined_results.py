"""Sort combined Snowflake QID/ROW_DATA exports into workbook service results."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lasagna.domain.route_models import ServiceRouteResult
from lasagna.route_sorting.adapter import build_site_location_lookup, route_rows_from_inca
from lasagna.route_sorting.combined_parser import read_snowflake_combined_csv
from lasagna.route_sorting.combined_results_models import (
    StructuredRouteContractError,
    StructuredRouteEdge,
)
from lasagna.route_sorting.combined_results_records import (
    _dp_role_key,
    _dp_roles_by_key,
    _service_contract_edges,
    _service_dp_endpoint_roles,
    _service_transport_adjacencies,
)
from lasagna.route_sorting.combined_results_transport import (
    _device_transport_roles_by_key,
    _edge_side_orders,
    _edge_side_orders_from_site_path,
    _ensure_rows_on_transport_path,
    _ensure_same_site_device_roles,
    _ensure_unique_contract_keys,
    _has_same_site_device_handoff,
    _matched_route_path,
    _row_sort_key,
    _same_site_handoff_sites,
    _transport_site_order,
)
from lasagna.route_sorting.contract import ROUTE_ORDER_AUTHORITY
from lasagna.route_sorting.port_display import extract_port_address
from lasagna.route_sorting.route_rows import InCARow

FAILED_SOURCE_ROWS_NOT_PROOF = "SOURCE_ROWS_NOT_ROUTE_PROOF"


def _route_order_source(info_lines: list[str]) -> str:
    if f"Route order: {ROUTE_ORDER_AUTHORITY}" in info_lines:
        return ROUTE_ORDER_AUTHORITY
    return "SORT_FAILED"


def _bearer_message(info_lines: list[str]) -> str:
    for line in info_lines:
        if line.startswith("Bearer: "):
            return line.removeprefix("Bearer: ").strip()
    return ""


def _is_planned_status(value: str | None) -> bool:
    return bool(value and value.strip().lower() == "planned")


def _is_planned_disconnect(rows: list[InCARow]) -> bool:
    return (
        bool(rows)
        and all(_is_planned_status(row.status_t_time) for row in rows)
        and not any(_is_planned_status(row.status_o_time) for row in rows)
    )


def _is_mixed_migration(rows: list[InCARow]) -> bool:
    classes = {row.classification for row in rows}
    return "DECOMMISSION" in classes and bool(classes & {"NEW", "LIVE"})


def _mixed_migration_sections(rows: list[InCARow]) -> tuple[list[InCARow], list[InCARow]]:
    current_route = [row for row in rows if row.classification in {"DECOMMISSION", "LIVE"}]
    migration_route = [row for row in rows if row.classification in {"NEW", "LIVE"}]
    return current_route, migration_route


def _sort_rows_by_structured_contract(
    rows: list[InCARow],
    route_order_metadata: list[dict[str, Any]] | None,
    service_id: str,
    transport_device_adjacency: list[dict[str, Any]] | None = None,
    dp_endpoint_roles: list[dict[str, Any]] | None = None,
    *,
    allow_decommission: bool = False,
) -> list[InCARow]:
    has_decommission = any(row.classification == "DECOMMISSION" for row in rows)
    if has_decommission and not allow_decommission and not _is_planned_disconnect(rows):
        raise StructuredRouteContractError(
            "migration route contract not proven by Snowflake structured facts"
        )

    edges = _service_contract_edges(route_order_metadata, service_id, transport_device_adjacency)
    edges_by_route = {edge.route_path: edge for edge in edges}
    transport_edges = _service_transport_adjacencies(transport_device_adjacency, service_id)
    dp_roles_by_key = _dp_roles_by_key(_service_dp_endpoint_roles(dp_endpoint_roles, service_id))
    demarc_rows = [row for row in rows if row.is_demarcation]
    missing_dp_roles = sorted(
        {row.route_path for row in demarc_rows if _dp_role_key(row) not in dp_roles_by_key}
    )
    if missing_dp_roles:
        raise StructuredRouteContractError(
            "DP/SDP endpoint role not proven by Snowflake contract for route_path(s): "
            + ", ".join(missing_dp_roles)
        )

    required_sites = {row.site_code for row in rows if not row.is_demarcation}
    site_order = _transport_site_order(
        edges,
        transport_device_adjacency,
        service_id,
        required_sites,
    )
    if site_order is None and _has_same_site_device_handoff(rows):
        raise StructuredRouteContractError(
            "transport adjacency path not proven for same-site device continuity"
        )
    same_site_handoff_sites = _same_site_handoff_sites(rows)
    device_roles_by_key = _device_transport_roles_by_key(
        rows, site_order, transport_edges, same_site_handoff_sites
    )
    _ensure_same_site_device_roles(rows, device_roles_by_key)
    edge_side_orders = (
        _edge_side_orders_from_site_path(edges, site_order)
        if site_order is not None
        else _edge_side_orders(edges)
    )
    row_route_paths = [
        _matched_route_path(
            row,
            dp_roles_by_key.get(_dp_role_key(row)) if row.is_demarcation else None,
        )
        for row in rows
    ]
    missing_paths = [
        path for path in dict.fromkeys(row_route_paths) if path and path not in edges_by_route
    ]
    if missing_paths:
        raise StructuredRouteContractError(
            f"missing route contract for route_path(s): {', '.join(missing_paths)}"
        )

    _ensure_rows_on_transport_path(rows, site_order)
    _ensure_unique_contract_keys(
        rows,
        edges_by_route,
        edge_side_orders,
        site_order,
        dp_roles_by_key,
        device_roles_by_key,
    )
    last_edge_sequence = max(edge.edge_sequence for edge in edges)
    return sorted(
        rows,
        key=lambda row: _row_sort_key(
            row,
            edges_by_route[
                _matched_route_path(
                    row,
                    dp_roles_by_key.get(_dp_role_key(row)) if row.is_demarcation else None,
                )
            ],
            last_edge_sequence,
            edge_side_orders,
            site_order,
            dp_roles_by_key.get(_dp_role_key(row)) if row.is_demarcation else None,
            device_roles_by_key.get(row.tuple_key()),
        ),
    )


def _row_belongs_to_site_path(
    row: InCARow,
    edges_by_route: dict[str, StructuredRouteEdge],
    path_sites: set[str],
) -> bool:
    if row.site_code not in path_sites:
        return False
    edge = edges_by_route.get(row.route_path)
    if edge is None:
        return False
    if row.is_device_row:
        return True
    return edge.a_site_code in path_sites and edge.b_site_code in path_sites


def _migration_rows_on_proven_new_path(
    rows: list[InCARow],
    route_order_metadata: list[dict[str, Any]] | None,
    service_id: str,
    transport_device_adjacency: list[dict[str, Any]] | None,
) -> list[InCARow]:
    new_rows = [row for row in rows if row.classification == "NEW"]
    live_rows = [row for row in rows if row.classification == "LIVE"]
    if not new_rows or not live_rows:
        return []

    edges = _service_contract_edges(route_order_metadata, service_id, transport_device_adjacency)
    edges_by_route = {edge.route_path: edge for edge in edges}
    bearer_route_path = edges[0].route_path
    anchor_rows = [
        *new_rows,
        *[row for row in live_rows if row.route_path == bearer_route_path],
    ]
    anchor_sites = {row.site_code for row in anchor_rows}
    site_order = _transport_site_order(
        edges,
        transport_device_adjacency,
        service_id,
        anchor_sites,
    )
    if site_order is None:
        return []

    path_sites = set(site_order)
    scoped_live_rows = [
        row for row in live_rows if _row_belongs_to_site_path(row, edges_by_route, path_sites)
    ]
    scoped_rows = [*new_rows, *scoped_live_rows]
    if {row.site_code for row in new_rows} - {row.site_code for row in scoped_rows}:
        return []
    if len(scoped_rows) == len([row for row in rows if row.classification in {"NEW", "LIVE"}]):
        return []
    return scoped_rows


def _sort_service_sections_by_structured_contract(
    rows: list[InCARow],
    route_order_metadata: list[dict[str, Any]] | None,
    service_id: str,
    transport_device_adjacency: list[dict[str, Any]] | None = None,
    dp_endpoint_roles: list[dict[str, Any]] | None = None,
) -> tuple[list[InCARow], list[InCARow]]:
    if not _is_mixed_migration(rows):
        return (
            _sort_rows_by_structured_contract(
                rows,
                route_order_metadata,
                service_id,
                transport_device_adjacency,
                dp_endpoint_roles,
            ),
            [],
        )

    current_route, migration_route = _mixed_migration_sections(rows)
    sorted_current_route = _sort_rows_by_structured_contract(
        current_route,
        route_order_metadata,
        service_id,
        transport_device_adjacency,
        dp_endpoint_roles,
        allow_decommission=True,
    )
    try:
        sorted_migration_route = _sort_rows_by_structured_contract(
            migration_route,
            route_order_metadata,
            service_id,
            transport_device_adjacency,
            dp_endpoint_roles,
        )
    except StructuredRouteContractError:
        new_path_rows = _migration_rows_on_proven_new_path(
            rows,
            route_order_metadata,
            service_id,
            transport_device_adjacency,
        )
        if not new_path_rows:
            raise
        sorted_migration_route = _sort_rows_by_structured_contract(
            new_path_rows,
            route_order_metadata,
            service_id,
            transport_device_adjacency,
            dp_endpoint_roles,
        )
    return sorted_current_route, sorted_migration_route


def _populate_device_display_points(rows: list[InCARow]) -> None:
    for row in rows:
        if row.is_ne_location and row.is_router:
            row.display_points = extract_port_address(row)


def _failed_sort_display_rows(
    rows: list[InCARow],
    route_order_metadata: list[dict[str, Any]] | None,
    service_id: str,
) -> list[InCARow]:
    """Return source rows in best available metadata order without proving route order."""
    try:
        edges = _service_contract_edges(route_order_metadata, service_id)
    except StructuredRouteContractError:
        return list(rows)

    edge_by_route = {edge.route_path: edge for edge in edges}

    def sort_key(row: InCARow) -> tuple[object, ...]:
        edge = edge_by_route.get(row.route_path)
        sequence = edge.edge_sequence if edge is not None else 999_999
        side = (row.site_side or "").strip().upper()
        side_rank = {"A": 0, "B": 1}.get(side, 2)
        return (sequence, side_rank, row.pos, row.site_code)

    return sorted(rows, key=sort_key)


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
            sorted_rows, migration_rows = _sort_service_sections_by_structured_contract(
                rows,
                combined_data.route_order_metadata,
                service_id,
                combined_data.transport_device_adjacency,
                combined_data.dp_endpoint_roles,
            )
            _populate_device_display_points(sorted_rows)
            _populate_device_display_points(migration_rows)
        except Exception as exc:
            display_rows = _failed_sort_display_rows(
                rows,
                combined_data.route_order_metadata,
                service_id,
            )
            _populate_device_display_points(display_rows)
            results[service_id] = ServiceRouteResult.sort_failed(
                service_id,
                (f"{exc} Source rows shown for troubleshooting only; not route proof."),
                route_rows_from_inca(display_rows, site_locations),
                route_order_source=FAILED_SOURCE_ROWS_NOT_PROOF,
            )
            continue

        results[service_id] = ServiceRouteResult.ok(
            service_id,
            route_rows_from_inca(sorted_rows, site_locations),
            route_rows_from_inca(migration_rows, site_locations),
            route_order_source=ROUTE_ORDER_AUTHORITY,
            message="",
        )

    return results
