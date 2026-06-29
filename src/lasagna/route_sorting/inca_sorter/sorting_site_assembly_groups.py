"""Site row grouping and bucket preparation for route assembly."""

from __future__ import annotations

import re

from .models import InCARow, service_mode
from .parsers import _cabling_point_int
from .sorting_site_assembly_base import (
    SiteAssemblyGroups,
    SiteBoundaryContext,
    SiteRowBuckets,
    _classify_self_loop_rows,
    _order_devices_by_direction,
    _ordered_building_rank,
    _trunk_cabinet_rank,
    get_trunk_for_site_pair,
)


def _build_site_boundary_context(
    site_code: str,
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    bearer: str | None,
    service_id: str | None,
    trunk_route_rank: dict[str, int] | None = None,
) -> SiteBoundaryContext:
    """Resolve endpoint position and trunk names for one site."""
    site_idx = site_order.index(site_code) if site_code in site_order else 0
    is_a_end = site_idx == 0
    is_b_end = site_idx == len(site_order) - 1
    arrival_trunk = None
    departure_trunk = None

    if not is_a_end:
        prev_site = site_order[site_idx - 1]
        arrival_trunk = get_trunk_for_site_pair(site_code, prev_site, trunk_edges)
    if not is_b_end and site_idx < len(site_order) - 1:
        next_site = site_order[site_idx + 1]
        departure_trunk = get_trunk_for_site_pair(site_code, next_site, trunk_edges)

    return SiteBoundaryContext(
        site_idx=site_idx,
        is_a_end=is_a_end,
        is_b_end=is_b_end,
        arrival_trunk=arrival_trunk,
        departure_trunk=departure_trunk,
        self_loop_trunk_names={name for left, right, name in trunk_edges if left == right},
        is_icb=service_mode(service_id) == "ICB" if service_id else False,
        has_bearer=bool(bearer),
        trunk_route_rank=trunk_route_rank,
    )


def _classify_self_loop_route_bucket(
    route_path: str,
    row: InCARow,
    site_code: str,
    context: SiteBoundaryContext,
) -> str:
    """Classify a same-site trunk row for endpoint or middle-site assembly."""
    if context.is_a_end:
        return "departure"
    if not context.is_b_end:
        return "self_loop"

    match = re.match(
        re.escape(site_code) + r"-" + re.escape(site_code) + r"\s+([A-Z]+)\s+\d+\s+OL\d+$",
        route_path,
    )
    self_loop_type = match.group(1) if match else None
    return "departure" if self_loop_type and row.site_type == self_loop_type else "arrival"


def _classify_inter_site_route_bucket(
    other_site: str,
    site_order: list[str],
    site_idx: int,
) -> str:
    """Classify an inter-site trunk row by remote-site direction."""
    other_idx = site_order.index(other_site) if other_site in site_order else -1
    return "arrival" if other_idx < site_idx else "departure"


def _classify_route_path_bucket(
    row: InCARow,
    site_code: str,
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    context: SiteBoundaryContext,
) -> str | None:
    """Classify a non-device row using known trunk edges."""
    for left_site, right_site, trunk_name in trunk_edges:
        if row.route_path != trunk_name or site_code not in (left_site, right_site):
            continue
        other_site = right_site if left_site == site_code else left_site
        if other_site == site_code:
            return _classify_self_loop_route_bucket(row.route_path, row, site_code, context)
        return _classify_inter_site_route_bucket(other_site, site_order, context.site_idx)
    return None


def _append_route_path_row(
    row: InCARow,
    buckets: SiteRowBuckets,
    site_code: str,
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    context: SiteBoundaryContext,
) -> None:
    """Append a non-device row to the correct site bucket."""
    bucket_name = _classify_route_path_bucket(row, site_code, site_order, trunk_edges, context)
    if bucket_name == "arrival":
        buckets.arrival_odf.append(row)
    elif bucket_name == "departure":
        buckets.departure_odf.append(row)
    elif bucket_name == "self_loop":
        buckets.self_loop_rows.append(row)
    else:
        buckets.other_rows.append(row)


def _group_site_rows(
    site_rows: list[InCARow],
    site_code: str,
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    bearer: str | None,
    context: SiteBoundaryContext,
) -> SiteRowBuckets:
    """Group site rows into device, trunk, bearer, demarc, and fallback buckets."""
    buckets = SiteRowBuckets([], [], [], [], [], [], [])
    for row in site_rows:
        route_path = row.route_path
        if row.is_demarcation:
            buckets.demarcation_rows.append(row)
        elif row.is_device_row:
            buckets.device_rows.append(row)
        elif route_path == context.arrival_trunk:
            buckets.arrival_odf.append(row)
        elif route_path == context.departure_trunk:
            buckets.departure_odf.append(row)
        elif route_path == bearer:
            buckets.bearer_odf.append(row)
        else:
            _append_route_path_row(
                row,
                buckets,
                site_code,
                site_order,
                trunk_edges,
                context,
            )
    return buckets


def _site_device_buildings(rows: list[InCARow]) -> set[str]:
    """Return non-empty building keys for a device group."""
    return {row.building_key for row in rows} - {""}


def _trunk_odf_sort_key(
    row: InCARow,
    self_loop_trunk_names: set[str],
    device_buildings: set[str],
    trunk_route_rank: dict[str, int] | None,
    *,
    xs_first: bool,
) -> tuple[int | tuple[int, tuple[str, ...], str], ...]:
    """Return the stable sort key for arrival, departure, and fallback ODF rows."""
    self_loop_order = 1 if row.route_path in self_loop_trunk_names else 0
    if trunk_route_rank and row.route_path in trunk_route_rank:
        route_prefix = (0, trunk_route_rank[row.route_path])
    else:
        route_prefix = (1, self_loop_order)
    type_order = 0 if row.site_type == "XS" else 1
    if not xs_first:
        type_order = 1 - type_order
    if not self_loop_order:
        return (
            *route_prefix,
            type_order,
            _trunk_cabinet_rank(row),
            row.pos,
            _cabling_point_int(row.cabling_points),
            row.row_index,
        )

    fiber_pair = (row.pos - 1) // 2
    if row.site_side is not None:
        boundary_order = 0 if row.site_side == "A" else 1
    else:
        boundary_order = 0 if (row.building_key or None) in device_buildings else 1
    return (
        *route_prefix,
        type_order,
        fiber_pair,
        boundary_order,
        _trunk_cabinet_rank(row),
        row.pos,
        _cabling_point_int(row.cabling_points),
        row.row_index,
    )


def _sort_site_boundary_rows(
    arrival_odf: list[InCARow],
    departure_odf: list[InCARow],
    other_rows: list[InCARow],
    device_rows: list[InCARow],
    context: SiteBoundaryContext,
) -> None:
    """Sort arrival, departure, and fallback trunk rows."""
    device_buildings = _site_device_buildings(device_rows)
    arrival_odf.sort(
        key=lambda row: _trunk_odf_sort_key(
            row,
            context.self_loop_trunk_names,
            device_buildings,
            context.trunk_route_rank,
            xs_first=False,
        )
    )
    departure_odf.sort(
        key=lambda row: _trunk_odf_sort_key(
            row,
            context.self_loop_trunk_names,
            device_buildings,
            context.trunk_route_rank,
            xs_first=True,
        )
    )
    other_rows.sort(
        key=lambda row: _trunk_odf_sort_key(
            row,
            context.self_loop_trunk_names,
            device_buildings,
            context.trunk_route_rank,
            xs_first=not context.is_icb if context.is_b_end else True,
        )
    )


def _sort_positional_row_groups(*row_groups: list[InCARow]) -> None:
    """Sort device-adjacent groups by position, cabling location, and source row."""
    for rows in row_groups:
        rows.sort(
            key=lambda row: (
                row.pos,
                row.cabling_location,
                _cabling_point_int(row.cabling_points),
                row.row_index,
            )
        )


def _split_demarcation_rows(
    demarcation_rows: list[InCARow],
) -> tuple[list[InCARow], list[InCARow]]:
    """Split demarcation rows into inner ARELION rows and outer external rows."""
    demarcation_arelion = [
        row for row in demarcation_rows if (row.dp_owner or "").upper() == "ARELION"
    ]
    demarcation_external = [
        row for row in demarcation_rows if (row.dp_owner or "").upper() != "ARELION"
    ]
    return demarcation_arelion, demarcation_external


def _should_interleave_endpoint_self_loop(
    device_rows: list[InCARow],
    arrival_odf: list[InCARow],
    departure_odf: list[InCARow],
    context: SiteBoundaryContext,
) -> bool:
    """Return whether endpoint self-loop ODF rows should be woven into device rows."""
    if not (context.is_a_end or context.is_b_end):
        return False
    if not any(row.is_router for row in device_rows) or not any(
        not row.is_router for row in device_rows
    ):
        return False
    return any(row.route_path in context.self_loop_trunk_names for row in arrival_odf) or any(
        row.route_path in context.self_loop_trunk_names for row in departure_odf
    )


def _extract_endpoint_self_loop_rows(
    arrival_odf: list[InCARow],
    departure_odf: list[InCARow],
    self_loop_trunk_names: set[str],
) -> tuple[list[InCARow], list[InCARow], list[InCARow]]:
    """Return self-loop ODF rows plus updated arrival and departure groups."""
    self_loop_departure = [row for row in departure_odf if row.route_path in self_loop_trunk_names]
    if self_loop_departure:
        remaining_departure = [
            row for row in departure_odf if row.route_path not in self_loop_trunk_names
        ]
        return self_loop_departure, arrival_odf, remaining_departure

    self_loop_arrival = [row for row in arrival_odf if row.route_path in self_loop_trunk_names]
    remaining_arrival = [row for row in arrival_odf if row.route_path not in self_loop_trunk_names]
    return self_loop_arrival, remaining_arrival, departure_odf


def _ordered_non_empty_buildings(rows: list[InCARow]) -> list[str]:
    """Return building keys in first-observed order, skipping blanks."""
    buildings: list[str] = []
    for row in rows:
        building = row.building_key
        if building and building not in buildings:
            buildings.append(building)
    return buildings


def _apply_endpoint_self_loop_interleave(
    arrival_odf: list[InCARow],
    departure_odf: list[InCARow],
    device_rows: list[InCARow],
    context: SiteBoundaryContext,
) -> tuple[list[InCARow], list[InCARow], list[InCARow]]:
    """Interleave endpoint self-loop ODF rows between router and non-router devices."""
    if not _should_interleave_endpoint_self_loop(device_rows, arrival_odf, departure_odf, context):
        return arrival_odf, departure_odf, device_rows

    router_devices = [row for row in device_rows if row.is_router]
    non_router_devices = [row for row in device_rows if not row.is_router]
    self_loop_rows, arrival_odf, departure_odf = _extract_endpoint_self_loop_rows(
        arrival_odf,
        departure_odf,
        context.self_loop_trunk_names,
    )

    ordered_device_buildings = _ordered_non_empty_buildings(device_rows)
    if ordered_device_buildings:
        self_loop_rows.sort(
            key=lambda row: (
                _ordered_building_rank(row.building_key, ordered_device_buildings),
                _trunk_cabinet_rank(row),
                row.pos,
                _cabling_point_int(row.cabling_points),
                row.row_index,
            )
        )

    interleaved_devices = (
        router_devices + self_loop_rows + non_router_devices
        if context.is_a_end
        else non_router_devices + self_loop_rows + router_devices
    )
    return arrival_odf, departure_odf, interleaved_devices


def _prepare_site_assembly_groups(
    site_rows: list[InCARow],
    site_code: str,
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    bearer: str | None,
    context: SiteBoundaryContext,
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None,
    service_id: str | None,
) -> SiteAssemblyGroups:
    """Group, classify, and sort all rows needed for one site's final assembly."""
    buckets = _group_site_rows(site_rows, site_code, site_order, trunk_edges, bearer, context)
    selfloop_only_site = False
    if buckets.self_loop_rows:
        selfloop_only_site = _classify_self_loop_rows(
            buckets.self_loop_rows,
            buckets.arrival_odf,
            buckets.departure_odf,
            site_code=site_code,
            site_order=site_order,
            tl_device_map=tl_device_map,
            service_id=service_id,
            device_rows=buckets.device_rows,
        )

    _sort_site_boundary_rows(
        buckets.arrival_odf,
        buckets.departure_odf,
        buckets.other_rows,
        buckets.device_rows,
        context,
    )
    _sort_positional_row_groups(
        buckets.device_rows,
        buckets.bearer_odf,
        buckets.demarcation_rows,
    )
    ordered_devices = _order_devices_by_direction(
        buckets.device_rows,
        site_order,
        site_code,
        tl_device_map=tl_device_map,
        service_id=service_id,
        arrival_trunk=context.arrival_trunk,
        departure_trunk=context.departure_trunk,
    )
    arrival_odf, departure_odf, ordered_devices = _apply_endpoint_self_loop_interleave(
        buckets.arrival_odf,
        buckets.departure_odf,
        ordered_devices,
        context,
    )
    demarcation_arelion, demarcation_external = _split_demarcation_rows(buckets.demarcation_rows)
    return SiteAssemblyGroups(
        arrival_odf=arrival_odf,
        device_rows=ordered_devices,
        departure_odf=departure_odf,
        bearer_odf=buckets.bearer_odf,
        other_rows=buckets.other_rows,
        demarcation_arelion=demarcation_arelion,
        demarcation_external=demarcation_external,
        selfloop_only_site=selfloop_only_site,
    )
