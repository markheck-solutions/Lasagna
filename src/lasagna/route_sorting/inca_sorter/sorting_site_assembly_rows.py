"""Final site row assembly for route sorting."""

from __future__ import annotations

from .models import InCARow
from .sorting_site_assembly_base import (
    SiteAssemblyGroups,
    SiteBoundaryContext,
    _interleave_self_loops_by_route_path,
)
from .sorting_site_assembly_groups import (
    _build_site_boundary_context,
    _prepare_site_assembly_groups,
    _site_device_buildings,
    _trunk_odf_sort_key,
)


def _split_self_loop_rows(
    rows: list[InCARow],
    self_loop_trunk_names: set[str],
) -> tuple[list[InCARow], list[InCARow]]:
    """Split a trunk-row block into self-loop and non-self-loop subsets."""
    self_loop_rows = [row for row in rows if row.route_path in self_loop_trunk_names]
    non_self_loop_rows = [row for row in rows if row.route_path not in self_loop_trunk_names]
    return self_loop_rows, non_self_loop_rows


def _combine_multi_path_self_loops(
    arrival_rows: list[InCARow],
    departure_rows: list[InCARow],
) -> list[InCARow] | None:
    """Regroup split self-loop rows when several same-site trunks share a site."""
    route_paths = {row.route_path for row in arrival_rows} | {
        row.route_path for row in departure_rows
    }
    if len(route_paths) < 2 or not arrival_rows or not departure_rows:
        return None
    return _interleave_self_loops_by_route_path(arrival_rows, departure_rows)


def _assemble_a_end_site_rows(
    groups: SiteAssemblyGroups,
    context: SiteBoundaryContext,
) -> list[InCARow]:
    """Assemble A-end rows in preserved signal-flow order."""
    self_loop_dep, non_self_loop_dep = _split_self_loop_rows(
        groups.departure_odf,
        context.self_loop_trunk_names,
    )
    if not context.is_icb and groups.demarcation_external:
        if self_loop_dep and non_self_loop_dep:
            self_loop_dep.sort(
                key=lambda row: _trunk_odf_sort_key(
                    row,
                    context.self_loop_trunk_names,
                    _site_device_buildings(groups.device_rows),
                    context.trunk_route_rank,
                    xs_first=False,
                )
            )
            return (
                groups.demarcation_external
                + self_loop_dep
                + groups.device_rows
                + groups.bearer_odf
                + groups.demarcation_arelion
                + groups.other_rows
                + non_self_loop_dep
            )
        return (
            groups.demarcation_external
            + groups.device_rows
            + groups.bearer_odf
            + groups.demarcation_arelion
            + groups.other_rows
            + groups.departure_odf
        )

    if not context.is_icb and not groups.demarcation_external and self_loop_dep:
        self_loop_dep.sort(
            key=lambda row: _trunk_odf_sort_key(
                row,
                context.self_loop_trunk_names,
                _site_device_buildings(groups.device_rows),
                context.trunk_route_rank,
                xs_first=False,
            )
        )
        return (
            self_loop_dep
            + groups.device_rows
            + groups.bearer_odf
            + groups.demarcation_arelion
            + groups.other_rows
            + non_self_loop_dep
        )

    if not context.has_bearer and groups.demarcation_external:
        return (
            groups.demarcation_external
            + groups.device_rows
            + groups.bearer_odf
            + groups.demarcation_arelion
            + groups.other_rows
            + groups.departure_odf
        )

    return (
        groups.device_rows
        + groups.bearer_odf
        + groups.demarcation_arelion
        + groups.other_rows
        + groups.departure_odf
        + groups.demarcation_external
    )


def _assemble_b_end_site_rows(
    groups: SiteAssemblyGroups,
    context: SiteBoundaryContext,
) -> list[InCARow]:
    """Assemble B-end rows in preserved signal-flow order."""
    if context.is_icb and groups.departure_odf:
        arrival_has_inter_site = any(
            row.route_path not in context.self_loop_trunk_names for row in groups.arrival_odf
        )
        departure_all_self_loop = all(
            row.route_path in context.self_loop_trunk_names for row in groups.departure_odf
        )
        if arrival_has_inter_site and departure_all_self_loop:
            return (
                groups.demarcation_external
                + groups.demarcation_arelion
                + groups.arrival_odf
                + groups.departure_odf
                + groups.other_rows
                + groups.bearer_odf
                + groups.device_rows
            )
        return (
            groups.demarcation_external
            + groups.demarcation_arelion
            + groups.departure_odf
            + groups.other_rows
            + groups.arrival_odf
            + groups.bearer_odf
            + groups.device_rows
        )

    if context.is_icb:
        return (
            groups.demarcation_external
            + groups.demarcation_arelion
            + groups.arrival_odf
            + groups.other_rows
            + groups.bearer_odf
            + groups.device_rows
        )

    if groups.departure_odf:
        return (
            groups.demarcation_arelion
            + groups.device_rows
            + groups.bearer_odf
            + groups.arrival_odf
            + groups.other_rows
            + groups.departure_odf
            + groups.demarcation_external
        )
    return (
        groups.arrival_odf
        + groups.demarcation_arelion
        + groups.device_rows
        + groups.bearer_odf
        + groups.other_rows
        + groups.demarcation_external
    )


def _site_device_area_rows(groups: SiteAssemblyGroups) -> list[InCARow]:
    """Return device rows plus bearer ODF rows for middle-site assembly."""
    return list(groups.device_rows + groups.bearer_odf)


def _assemble_selfloop_only_middle_site_rows(groups: SiteAssemblyGroups) -> list[InCARow]:
    """Assemble a middle site with only same-site physical trunks."""
    arrival_buildings = {row.building_key for row in groups.arrival_odf} - {""}
    departure_buildings = {row.building_key for row in groups.departure_odf} - {""}
    arrival_types = {row.site_type for row in groups.arrival_odf}
    departure_types = {row.site_type for row in groups.departure_odf}
    device_area_rows = _site_device_area_rows(groups)

    if arrival_buildings & departure_buildings:
        arrival_devices: list[InCARow] = []
        departure_devices: list[InCARow] = []
        remaining_devices = list(device_area_rows)
    else:
        arrival_devices = [row for row in device_area_rows if row.building_key in arrival_buildings]
        departure_devices = [
            row for row in device_area_rows if row.building_key in departure_buildings
        ]
        remaining_devices = [
            row
            for row in device_area_rows
            if row not in arrival_devices and row not in departure_devices
        ]

    if remaining_devices and (arrival_types or departure_types):
        still_remaining: list[InCARow] = []
        for row in remaining_devices:
            if row.site_type in arrival_types and row.site_type not in departure_types:
                arrival_devices.append(row)
            elif row.site_type in departure_types and row.site_type not in arrival_types:
                departure_devices.append(row)
            else:
                still_remaining.append(row)
        remaining_devices = still_remaining

    return (
        groups.demarcation_external
        + arrival_devices
        + groups.arrival_odf
        + groups.departure_odf
        + departure_devices
        + groups.demarcation_arelion
        + remaining_devices
        + groups.other_rows
    )


def _assemble_departure_only_middle_site_rows(
    groups: SiteAssemblyGroups,
    context: SiteBoundaryContext,
) -> list[InCARow]:
    """Assemble a middle site that only has departure trunk rows."""
    self_loop_arrival, non_self_loop_arrival = _split_self_loop_rows(
        groups.arrival_odf,
        context.self_loop_trunk_names,
    )
    self_loop_departure, non_self_loop_departure = _split_self_loop_rows(
        groups.departure_odf,
        context.self_loop_trunk_names,
    )
    combined_self_loops = _combine_multi_path_self_loops(
        self_loop_arrival,
        self_loop_departure,
    )
    if combined_self_loops is not None:
        return (
            groups.demarcation_external
            + non_self_loop_arrival
            + groups.demarcation_arelion
            + groups.device_rows
            + groups.bearer_odf
            + groups.other_rows
            + combined_self_loops
            + non_self_loop_departure
        )
    if self_loop_departure and non_self_loop_departure:
        return (
            groups.demarcation_external
            + groups.arrival_odf
            + groups.demarcation_arelion
            + groups.device_rows
            + groups.bearer_odf
            + groups.other_rows
            + self_loop_departure
            + non_self_loop_departure
        )
    return (
        groups.demarcation_external
        + groups.arrival_odf
        + groups.demarcation_arelion
        + groups.device_rows
        + groups.bearer_odf
        + groups.other_rows
        + groups.departure_odf
    )


def _assemble_default_middle_site_rows(
    groups: SiteAssemblyGroups,
    context: SiteBoundaryContext,
) -> list[InCARow]:
    """Assemble a middle site with both arrival and departure boundaries."""
    self_loop_arrival, non_self_loop_arrival = _split_self_loop_rows(
        groups.arrival_odf,
        context.self_loop_trunk_names,
    )
    self_loop_departure, non_self_loop_departure = _split_self_loop_rows(
        groups.departure_odf,
        context.self_loop_trunk_names,
    )
    combined_self_loops = _combine_multi_path_self_loops(
        self_loop_arrival,
        self_loop_departure,
    )
    if combined_self_loops is not None:
        return (
            non_self_loop_arrival
            + groups.device_rows
            + groups.bearer_odf
            + groups.demarcation_arelion
            + groups.other_rows
            + combined_self_loops
            + non_self_loop_departure
            + groups.demarcation_external
        )
    return (
        groups.arrival_odf
        + groups.device_rows
        + groups.bearer_odf
        + groups.demarcation_arelion
        + groups.other_rows
        + self_loop_departure
        + non_self_loop_departure
        + groups.demarcation_external
    )


def _assemble_middle_site_rows(
    groups: SiteAssemblyGroups,
    context: SiteBoundaryContext,
) -> list[InCARow]:
    """Assemble middle-site rows in preserved signal-flow order."""
    if groups.selfloop_only_site:
        return _assemble_selfloop_only_middle_site_rows(groups)
    if context.arrival_trunk is None and context.departure_trunk is None:
        return (
            groups.demarcation_external
            + groups.arrival_odf
            + groups.departure_odf
            + groups.demarcation_arelion
            + groups.device_rows
            + groups.bearer_odf
            + groups.other_rows
        )
    if context.arrival_trunk is None:
        return _assemble_departure_only_middle_site_rows(groups, context)
    if not groups.arrival_odf and groups.demarcation_external:
        self_loop_departure, non_self_loop_departure = _split_self_loop_rows(
            groups.departure_odf,
            context.self_loop_trunk_names,
        )
        return (
            groups.demarcation_external
            + groups.demarcation_arelion
            + groups.device_rows
            + groups.bearer_odf
            + groups.other_rows
            + self_loop_departure
            + non_self_loop_departure
        )
    return _assemble_default_middle_site_rows(groups, context)


def order_within_site(
    site_rows: list[InCARow],
    site_code: str,
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    bearer: str | None,
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None = None,
    service_id: str | None = None,
    trunk_route_rank: dict[str, int] | None = None,
) -> list[InCARow]:
    """Order rows within a single site according to signal flow.

    Order: [Arrival trunk ODF] -> [Device rows] -> [Departure trunk ODF]
    A-end: [Device] -> [Departure ODF]
    B-end: [Arrival ODF] -> [Device]

    When tl_device_map is provided, uses TL_DEVICE data to determine which
    building a transport link connects to, enabling data-driven within-site
    ordering for self-loop sites and device direction.

    Args:
        site_rows: All rows at this site.
        site_code: The site code.
        site_order: Geographic site order (A->B).
        trunk_edges: All trunk edges.
        bearer: Bearer route path name.
        tl_device_map: Optional TL_DEVICE lookup from build_tl_device_map().
        service_id: Optional service ID for TL_DEVICE lookups.

    Returns:
        Sorted list of rows within this site.
    """
    context = _build_site_boundary_context(
        site_code,
        site_order,
        trunk_edges,
        bearer,
        service_id,
        trunk_route_rank,
    )
    groups = _prepare_site_assembly_groups(
        site_rows,
        site_code,
        site_order,
        trunk_edges,
        bearer,
        context,
        tl_device_map,
        service_id,
    )
    if context.is_a_end:
        return _assemble_a_end_site_rows(groups, context)
    if context.is_b_end:
        return _assemble_b_end_site_rows(groups, context)
    return _assemble_middle_site_rows(groups, context)
