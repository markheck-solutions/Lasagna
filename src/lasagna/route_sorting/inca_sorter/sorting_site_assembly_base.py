"""Base helpers for within-site route assembly."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import NamedTuple

from .models import InCARow, _DirectionInfo
from .parsers import _cabling_point_int, _ne_group_key
from .sorting_topology import _parse_edge_site_pair

logger = logging.getLogger(__name__)


def _resolve_remote_site_from_tl_name(
    tl_name: str,
    site_code: str,
    known_sites: set[str],
    *,
    service_id: str,
) -> str | None:
    """Resolve the remote site for a TL anchored at a specific site."""
    try:
        left_site, right_site = _parse_edge_site_pair(tl_name, known_sites)
    except ValueError as error:
        logger.warning(
            "Skipping malformed TL edge %r at site %s (service %s): %s",
            tl_name,
            site_code,
            service_id,
            error,
        )
        return None

    if left_site == site_code and right_site == site_code:
        return None
    if left_site == site_code:
        return right_site
    if right_site == site_code:
        return left_site
    return None


def group_rows_by_site(rows: list[InCARow]) -> dict[str, list[InCARow]]:
    """Group rows by Site Code, preserving insertion order.

    Args:
        rows: All INCA rows.

    Returns:
        OrderedDict-like dict mapping site_code -> list of rows at that site.
    """
    groups: dict[str, list[InCARow]] = defaultdict(list)
    for r in rows:
        groups[r.site_code].append(r)
    return groups


def get_trunk_for_site_pair(
    site1: str,
    site2: str,
    edges: list[tuple[str, str, str]],
) -> str | None:
    """Find the trunk route path connecting two sites.

    Args:
        site1: First site code.
        site2: Second site code.
        edges: Trunk edges from parse_trunk_edges().

    Returns:
        Trunk name string, or None if no direct trunk exists.
    """
    for s1, s2, trunk in edges:
        if (s1 == site1 and s2 == site2) or (s1 == site2 and s2 == site1):
            return trunk
    return None


def _ordered_building_rank(
    building_key: str | None,
    ordered_device_bldgs: list[str],
) -> tuple[int, str]:
    """Rank a row by the first matching device-building key in signal order.

    Endpoint self-loop trunks should follow the already-ordered device flow.
    Exact building matches win. Prefix matches (for example, device `C5`
    matching trunk `C5A`) fall back to the longest shared prefix.
    """
    row_bldg = (building_key or "").strip()
    if not row_bldg:
        return (len(ordered_device_bldgs), "")

    best_rank = len(ordered_device_bldgs)
    best_prefix_len = -1
    for idx, dev_bldg in enumerate(ordered_device_bldgs):
        if row_bldg == dev_bldg:
            return (idx, row_bldg)
        if row_bldg.startswith(dev_bldg) or dev_bldg.startswith(row_bldg):
            prefix_len = 0
            for left, right in zip(row_bldg, dev_bldg):
                if left != right:
                    break
                prefix_len += 1
            if prefix_len > best_prefix_len:
                best_prefix_len = prefix_len
                best_rank = idx

    return (best_rank, row_bldg)


def _trunk_cabinet_rank(row: InCARow) -> tuple[int, tuple[str, ...], str]:
    """Return a structured-first cabinet ordering key for trunk-like rows."""
    structured = row.cabinet_sort_key
    if structured is not None:
        return (0, structured, row.cabling_location)
    return (1, tuple(), row.cabling_location)


def _interleave_self_loops_by_route_path(
    sl_arr: list[InCARow],
    sl_dep: list[InCARow],
) -> list[InCARow]:
    """Group self-loop rows by ``route_path`` so each trunk's XS+U pair stays
    contiguous in middle-site assembly.

    At a transition middle site, the second-pass classifier can split a single
    self-loop trunk's rows: XS rows match arrival_type and land in ``arrival_odf``,
    while U rows match departure_type and land in ``departure_odf`` (later
    extracted as ``sl_dep``). Without grouping, the assembly renders all XS
    self-loop rows together, then all U self-loop rows together, breaking
    per-route_path adjacency when two or more self-loop trunks share the site.

    Both inputs must already be sorted via ``_trunk_odf_sort_key`` (which orders
    self-loop rows by fiber pair). This helper preserves that within-list order
    and emits route_paths in the order they are first encountered (arrival rows
    first, then any new paths from departure).
    """
    groups: dict[str, list[InCARow]] = {}
    paths_in_order: list[str] = []
    for r in sl_arr:
        if r.route_path not in groups:
            groups[r.route_path] = []
            paths_in_order.append(r.route_path)
        groups[r.route_path].append(r)
    for r in sl_dep:
        if r.route_path not in groups:
            groups[r.route_path] = []
            paths_in_order.append(r.route_path)
        groups[r.route_path].append(r)
    return [r for p in paths_in_order for r in groups[p]]


def _resolve_direction_buildings(
    site_code: str,
    service_id: str,
    site_order: list[str],
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]],
    device_rows: list[InCARow],
) -> _DirectionInfo:
    """Resolve arrival and departure buildings using TL_DEVICE direction data.

    Iterates ALL transport links at this site from TL_DEVICE data, parses each
    TL name to extract the remote site code, then uses site_order to classify
    the remote site as arrival-side (before current site) or departure-side
    (after current site). The NE_PART(s) from the TL are matched to device rows
    to determine which building and site_type faces each direction. Each TL may
    have multiple NE_PARTs; each is tried until a device row match is found.

    This approach works even when the trunk edge name (from Snowflake hierarchy)
    differs from the TL name (L1/L2 transport link name), because it uses TL
    name parsing rather than exact trunk name matching.

    Args:
        site_code: Current site code.
        service_id: Service ID for TL_DEVICE lookups.
        site_order: Geographic site order (A->B).
        tl_device_map: TL_DEVICE lookup from build_tl_device_map().
        device_rows: Device rows at this site.

    Returns:
        _DirectionInfo with arrival/departure building and site_type.
        Any field may be None if direction cannot be resolved.
    """
    empty = _DirectionInfo(None, None, None, None)
    site_tl_map = tl_device_map.get((service_id, site_code), {})
    if not site_tl_map:
        return empty

    site_idx = site_order.index(site_code) if site_code in site_order else -1
    if site_idx < 0:
        return empty

    known_sites = set(site_order)
    arrival_bldg: str | None = None
    departure_bldg: str | None = None
    arrival_type: str | None = None
    departure_type: str | None = None

    for tl_name, ne_parts in site_tl_map.items():
        remote_site = _resolve_remote_site_from_tl_name(
            tl_name,
            site_code,
            known_sites,
            service_id=service_id,
        )
        if remote_site is None:
            continue

        remote_idx = site_order.index(remote_site) if remote_site in site_order else -1
        if remote_idx < 0:
            continue

        matched_row = next(
            (
                row
                for ne_part in ne_parts
                for row in device_rows
                if row.ne_info and ne_part in row.ne_info and row.building_key
            ),
            None,
        )
        if matched_row is None or not matched_row.building_key:
            continue

        if remote_idx < site_idx:
            arrival_bldg = matched_row.building_key
            arrival_type = matched_row.site_type
        elif remote_idx > site_idx:
            departure_bldg = matched_row.building_key
            departure_type = matched_row.site_type

    return _DirectionInfo(arrival_bldg, departure_bldg, arrival_type, departure_type)


def _classify_self_loop_row_with_direction(
    row: InCARow,
    dir_info: _DirectionInfo,
    arrival_odf: list[InCARow],
    departure_odf: list[InCARow],
) -> None:
    """Place a self-loop row using resolved device-direction cues."""
    row_bldg = row.building_key or None
    if row_bldg and row_bldg == dir_info.arrival_bldg:
        if (
            dir_info.arrival_type
            and row.site_type != dir_info.arrival_type
            and row.site_type in ("XS", "U")
            and dir_info.arrival_type in ("XS", "U")
        ):
            departure_odf.append(row)
        else:
            arrival_odf.append(row)
        return

    if row_bldg and row_bldg == dir_info.departure_bldg:
        if (
            dir_info.departure_type
            and row.site_type != dir_info.departure_type
            and row.site_type in ("XS", "U")
            and dir_info.departure_type in ("XS", "U")
        ):
            arrival_odf.append(row)
        else:
            departure_odf.append(row)
        return

    if (
        dir_info.arrival_type
        and row.site_type == dir_info.arrival_type
        and (not dir_info.departure_type or row.site_type != dir_info.departure_type)
    ):
        arrival_odf.append(row)
        return

    if (
        dir_info.departure_type
        and row.site_type == dir_info.departure_type
        and (not dir_info.arrival_type or row.site_type != dir_info.arrival_type)
    ):
        departure_odf.append(row)
        return

    if dir_info.arrival_bldg and not dir_info.departure_bldg:
        departure_odf.append(row)
        return
    if dir_info.departure_bldg and not dir_info.arrival_bldg:
        arrival_odf.append(row)
        return
    departure_odf.append(row)


def _classify_self_loop_rows(
    self_loop_rows: list[InCARow],
    arrival_odf: list[InCARow],
    departure_odf: list[InCARow],
    *,
    site_code: str,
    site_order: list[str],
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None,
    service_id: str | None,
    device_rows: list[InCARow],
) -> bool:
    """Classify deferred self-loop rows and report self-loop-only topology."""
    if not self_loop_rows:
        return False

    arrival_types = {row.site_type for row in arrival_odf}
    departure_types = {row.site_type for row in departure_odf}

    if tl_device_map and service_id:
        dir_info = _resolve_direction_buildings(
            site_code,
            service_id,
            site_order,
            tl_device_map,
            device_rows,
        )
        if dir_info.arrival_bldg or dir_info.departure_bldg:
            selfloop_only_site = not arrival_types and not departure_types
            for row in self_loop_rows:
                _classify_self_loop_row_with_direction(row, dir_info, arrival_odf, departure_odf)
            return selfloop_only_site

    if not arrival_types and not departure_types:
        departure_odf.extend(self_loop_rows)
        return False

    for row in self_loop_rows:
        if row.site_type in arrival_types and row.site_type not in departure_types:
            arrival_odf.append(row)
        elif row.site_type in departure_types and row.site_type not in arrival_types:
            departure_odf.append(row)
        else:
            departure_odf.append(row)
    return False


def _group_device_rows_by_ne(device_rows: list[InCARow]) -> dict[str, list[InCARow]]:
    """Group device rows by normalized NE identity."""
    ne_groups: dict[str, list[InCARow]] = defaultdict(list)
    for row in device_rows:
        ne_groups[_ne_group_key(row.ne_info)].append(row)
    return ne_groups


def _collect_directional_ne_parts(
    site_tl_map: dict[str, list[str]],
    *,
    site_code: str,
    site_order: list[str],
    site_idx: int,
    service_id: str,
) -> tuple[set[str], set[str]]:
    """Collect TL_DEVICE NE parts facing arrival and departure directions."""
    known_sites = set(site_order)
    arrival_ne_parts: set[str] = set()
    departure_ne_parts: set[str] = set()
    for tl_name, ne_parts in site_tl_map.items():
        remote_site = _resolve_remote_site_from_tl_name(
            tl_name,
            site_code,
            known_sites,
            service_id=service_id,
        )
        if remote_site is None:
            continue
        remote_idx = site_order.index(remote_site) if remote_site in site_order else -1
        if remote_idx < 0:
            continue
        if remote_idx < site_idx:
            arrival_ne_parts.update(ne_parts)
        elif remote_idx > site_idx:
            departure_ne_parts.update(ne_parts)
    return arrival_ne_parts, departure_ne_parts


def _group_ne_parts(group: list[InCARow]) -> set[str]:
    """Extract normalized NE_PART candidates from a grouped device."""
    ne_parts: set[str] = set()
    for row in group:
        if not row.ne_info:
            continue
        normalized = _ne_group_key(row.ne_info)
        ne_parts.add(normalized.split()[-1] if " " in normalized else normalized)
    return ne_parts


def _score_device_groups_from_direction(
    ne_groups: dict[str, list[InCARow]],
    arrival_ne_parts: set[str],
    departure_ne_parts: set[str],
) -> list[tuple[int, tuple[str, list[InCARow]]]] | None:
    """Score grouped devices from TL-derived direction evidence."""
    scored_groups: list[tuple[int, tuple[str, list[InCARow]]]] = []
    any_resolved = False
    for key, group in ne_groups.items():
        group_ne_parts = _group_ne_parts(group)
        faces_arrival = bool(group_ne_parts & arrival_ne_parts)
        faces_departure = bool(group_ne_parts & departure_ne_parts)
        if faces_arrival and not faces_departure:
            scored_groups.append((-1, (key, group)))
            any_resolved = True
        elif faces_departure and not faces_arrival:
            scored_groups.append((1, (key, group)))
            any_resolved = True
        else:
            scored_groups.append((0, (key, group)))
    return scored_groups if any_resolved else None


def _orient_endpoint_device_groups(
    scored_groups: list[tuple[int, tuple[str, list[InCARow]]]],
    *,
    site_idx: int,
    site_count: int,
) -> list[tuple[int, tuple[str, list[InCARow]]]]:
    """Override device-group ordering at endpoints so routers stay outermost."""
    is_a_end = site_idx == 0
    is_b_end = site_idx == site_count - 1
    if not (is_a_end or is_b_end) or len(scored_groups) <= 1:
        return scored_groups

    has_router = any(group[0].is_router for _, (_, group) in scored_groups)
    has_non_router = any(not group[0].is_router for _, (_, group) in scored_groups)
    if not (has_router and has_non_router):
        return scored_groups

    oriented_groups: list[tuple[int, tuple[str, list[InCARow]]]] = []
    for _score, (key, group) in scored_groups:
        group_is_router = group[0].is_router
        if is_a_end:
            oriented_score = -10 if group_is_router else 10
        else:
            oriented_score = 10 if group_is_router else -10
        oriented_groups.append((oriented_score, (key, group)))
    return oriented_groups


def _apply_device_group_cable_fallback(
    scored_groups: list[tuple[int, tuple[str, list[InCARow]]]],
) -> list[tuple[int, tuple[str, list[InCARow]]]]:
    """Fallback to minimum cabling-point ordering when direction scores tie."""
    if len(scored_groups) <= 1 or not all(score == 0 for score, _ in scored_groups):
        return scored_groups
    return [
        (min(_cabling_point_int(row.cabling_points) for row in group), (key, group))
        for _score, (key, group) in scored_groups
    ]


def _flatten_sorted_device_groups(
    scored_groups: list[tuple[int, tuple[str, list[InCARow]]]],
) -> list[InCARow]:
    """Flatten scored device groups into their final stable row order."""
    result: list[InCARow] = []
    for _score, (_key, rows_in_group) in sorted(scored_groups, key=lambda item: item[0]):
        rows_in_group.sort(
            key=lambda row: (
                row.pos,
                row.cabling_location,
                _cabling_point_int(row.cabling_points),
                row.row_index,
            )
        )
        result.extend(rows_in_group)
    return result


class SiteBoundaryContext(NamedTuple):
    site_idx: int
    is_a_end: bool
    is_b_end: bool
    arrival_trunk: str | None
    departure_trunk: str | None
    self_loop_trunk_names: set[str]
    is_icb: bool
    has_bearer: bool
    trunk_route_rank: dict[str, int] | None


class SiteRowBuckets(NamedTuple):
    arrival_odf: list[InCARow]
    device_rows: list[InCARow]
    departure_odf: list[InCARow]
    bearer_odf: list[InCARow]
    other_rows: list[InCARow]
    demarcation_rows: list[InCARow]
    self_loop_rows: list[InCARow]


class SiteAssemblyGroups(NamedTuple):
    arrival_odf: list[InCARow]
    device_rows: list[InCARow]
    departure_odf: list[InCARow]
    bearer_odf: list[InCARow]
    other_rows: list[InCARow]
    demarcation_arelion: list[InCARow]
    demarcation_external: list[InCARow]
    selfloop_only_site: bool


def _order_devices_by_direction(
    device_rows: list[InCARow],
    site_order: list[str],
    site_code: str,
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None = None,
    service_id: str | None = None,
    arrival_trunk: str | None = None,
    departure_trunk: str | None = None,
) -> list[InCARow]:
    """Order device rows by direction (arrival-facing first).

    Uses TL_DEVICE data when available to match device NE_PARTs to transport
    link directions. Falls back to router-vs-DWDM endpoint heuristic (Tier 1)
    and cable-number heuristic (Tier 2) when TL_DEVICE data is absent.

    Args:
        device_rows: Device rows at this site.
        site_order: Geographic site order.
        site_code: Current site code.
        tl_device_map: Optional TL_DEVICE lookup.
        service_id: Optional service ID for TL_DEVICE lookups.
        arrival_trunk: Arrival trunk name at this site.
        departure_trunk: Departure trunk name at this site.

    Returns:
        Reordered device rows.
    """
    if len(device_rows) <= 2:
        return device_rows

    site_idx = site_order.index(site_code) if site_code in site_order else 0
    ne_groups = _group_device_rows_by_ne(device_rows)
    if len(ne_groups) <= 1:
        return device_rows

    scored_groups: list[tuple[int, tuple[str, list[InCARow]]]] | None = None
    if tl_device_map and service_id:
        site_tl_map = tl_device_map.get((service_id, site_code), {})
        if site_tl_map:
            arrival_ne_parts, departure_ne_parts = _collect_directional_ne_parts(
                site_tl_map,
                site_code=site_code,
                site_order=site_order,
                site_idx=site_idx,
                service_id=service_id,
            )
            if arrival_ne_parts or departure_ne_parts:
                scored_groups = _score_device_groups_from_direction(
                    ne_groups,
                    arrival_ne_parts,
                    departure_ne_parts,
                )

    if scored_groups is None:
        scored_groups = [(0, (key, group)) for key, group in ne_groups.items()]

    scored_groups = _orient_endpoint_device_groups(
        scored_groups,
        site_idx=site_idx,
        site_count=len(site_order),
    )
    scored_groups = _apply_device_group_cable_fallback(scored_groups)
    return _flatten_sorted_device_groups(scored_groups)
