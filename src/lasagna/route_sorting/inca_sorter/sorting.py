"""Route path sorting, within-site ordering, graph walking, and sort helpers."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import defaultdict, deque
from typing import NamedTuple

from . import sorting_site_assembly as _site_assembly
from . import sorting_topology as _sorting_topology
from .formatting import (
    format_notations,
    format_sorted_route_path,
    format_tickets,
    generate_notations,
    write_output_excel,
)
from .models import (
    InCARow,
    SnowflakeCombinedData,
    SortResult,
    Ticket,
    service_mode,
)
from .parsers import (
    _cabling_point_int,
    build_tl_device_map,
    extract_service_id,
    read_excel,
    read_snowflake_combined_csv,
    read_snowflake_csv,
)
from .sorting_topology import (
    _filter_site_order_for_data,
    build_route_topology,
    build_section_route_topology,
    build_transmission_endpoint_lookup,
    build_trunk_endpoint_lookup,
    build_trunk_media_lookup,
    populate_trunk_media,
    resolve_route_endpoints,
)
from .tickets import (
    build_migration_portion,
    classify_patch_points,
    generate_tickets,
    identify_metro_clusters,
    is_colocation_trunk,
    is_migration_order,
    split_migration_sections,
)

logger = logging.getLogger(__name__)

SiteAssemblyGroups = _site_assembly.SiteAssemblyGroups
SiteBoundaryContext = _site_assembly.SiteBoundaryContext
SiteRowBuckets = _site_assembly.SiteRowBuckets
_append_route_path_row = _site_assembly._append_route_path_row
_apply_device_group_cable_fallback = _site_assembly._apply_device_group_cable_fallback
_apply_endpoint_self_loop_interleave = _site_assembly._apply_endpoint_self_loop_interleave
_assemble_a_end_site_rows = _site_assembly._assemble_a_end_site_rows
_assemble_b_end_site_rows = _site_assembly._assemble_b_end_site_rows
_assemble_default_middle_site_rows = _site_assembly._assemble_default_middle_site_rows
_assemble_departure_only_middle_site_rows = _site_assembly._assemble_departure_only_middle_site_rows
_assemble_middle_site_rows = _site_assembly._assemble_middle_site_rows
_assemble_selfloop_only_middle_site_rows = _site_assembly._assemble_selfloop_only_middle_site_rows
_build_site_boundary_context = _site_assembly._build_site_boundary_context
_classify_inter_site_route_bucket = _site_assembly._classify_inter_site_route_bucket
_classify_route_path_bucket = _site_assembly._classify_route_path_bucket
_classify_self_loop_route_bucket = _site_assembly._classify_self_loop_route_bucket
_classify_self_loop_row_with_direction = _site_assembly._classify_self_loop_row_with_direction
_classify_self_loop_rows = _site_assembly._classify_self_loop_rows
_collect_directional_ne_parts = _site_assembly._collect_directional_ne_parts
_DirectionInfo = _site_assembly._DirectionInfo
_combine_multi_path_self_loops = _site_assembly._combine_multi_path_self_loops
_extract_endpoint_self_loop_rows = _site_assembly._extract_endpoint_self_loop_rows
_flatten_sorted_device_groups = _site_assembly._flatten_sorted_device_groups
_group_device_rows_by_ne = _site_assembly._group_device_rows_by_ne
_group_ne_parts = _site_assembly._group_ne_parts
_group_site_rows = _site_assembly._group_site_rows
_interleave_self_loops_by_route_path = _site_assembly._interleave_self_loops_by_route_path
_ordered_building_rank = _site_assembly._ordered_building_rank
_ordered_non_empty_buildings = _site_assembly._ordered_non_empty_buildings
_order_devices_by_direction = _site_assembly._order_devices_by_direction
_prepare_site_assembly_groups = _site_assembly._prepare_site_assembly_groups
_resolve_direction_buildings = _site_assembly._resolve_direction_buildings
_resolve_remote_site_from_tl_name = _site_assembly._resolve_remote_site_from_tl_name
_score_device_groups_from_direction = _site_assembly._score_device_groups_from_direction
_should_interleave_endpoint_self_loop = _site_assembly._should_interleave_endpoint_self_loop
_site_device_area_rows = _site_assembly._site_device_area_rows
_site_device_buildings = _site_assembly._site_device_buildings
_sort_positional_row_groups = _site_assembly._sort_positional_row_groups
_sort_site_boundary_rows = _site_assembly._sort_site_boundary_rows
_split_demarcation_rows = _site_assembly._split_demarcation_rows
_split_self_loop_rows = _site_assembly._split_self_loop_rows
_trunk_cabinet_rank = _site_assembly._trunk_cabinet_rank
_trunk_odf_sort_key = _site_assembly._trunk_odf_sort_key
get_trunk_for_site_pair = _site_assembly.get_trunk_for_site_pair
group_rows_by_site = _site_assembly.group_rows_by_site
order_within_site = _site_assembly.order_within_site

_infer_demarc_endpoints = _sorting_topology._infer_demarc_endpoints
build_adjacency_graph = _sorting_topology.build_adjacency_graph
parse_bearer_endpoints = _sorting_topology.parse_bearer_endpoints
parse_snowflake_edges = _sorting_topology.parse_snowflake_edges
parse_trunk_edges = _sorting_topology.parse_trunk_edges
walk_graph = _sorting_topology.walk_graph


def _build_site_type_counts(rows: list[InCARow]) -> dict[str, dict[str, int]]:
    """Count site_type occurrences per site."""
    site_type_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = site_type_counts.setdefault(row.site_code, {})
        site_type = row.site_type or ""
        counts[site_type] = counts.get(site_type, 0) + 1
    return site_type_counts


def _dominant_site_type(site: str, site_type_counts: dict[str, dict[str, int]]) -> str:
    """Return the dominant site_type for a site, preferring XS on ties."""
    counts = site_type_counts.get(site, {})
    if not counts:
        return ""
    return max(counts.keys(), key=lambda site_type: (counts[site_type], site_type == "XS"))


def _build_colocation_trunk_graph(
    trunk_edges: list[tuple[str, str, str]],
    trunk_media_lookup: dict[str, str] | None,
) -> dict[str, set[str]]:
    """Build OL-only adjacency used for endpoint sibling ordering."""
    ol_graph: dict[str, set[str]] = defaultdict(set)
    for left_site, right_site, edge_name in trunk_edges:
        if not is_colocation_trunk(edge_name, trunk_media_lookup):
            continue
        ol_graph[left_site].add(right_site)
        ol_graph[right_site].add(left_site)
    return ol_graph


def _order_sites_by_ol_distance(
    cluster: list[str],
    anchor_site: str,
    outer_at_start: bool,
    ol_graph: dict[str, set[str]],
) -> list[str] | None:
    """Order a metro cluster by OL-chain distance from an endpoint anchor."""
    if anchor_site not in cluster:
        return None

    cluster_set = set(cluster)
    distances: dict[str, int] = {anchor_site: 0}
    queue: deque[str] = deque([anchor_site])
    while queue:
        current = queue.popleft()
        for neighbor in ol_graph.get(current, set()):
            if neighbor not in cluster_set or neighbor in distances:
                continue
            distances[neighbor] = distances[current] + 1
            queue.append(neighbor)

    if len(distances) != len(cluster):
        return None

    cluster_position = {site: index for index, site in enumerate(cluster)}
    if outer_at_start:
        return sorted(cluster, key=lambda site: (distances[site], cluster_position[site], site))
    return sorted(cluster, key=lambda site: (-distances[site], cluster_position[site], site))


def _count_interior_directional_links(
    site: str,
    cluster: list[str],
    *,
    cluster_start: int,
    cluster_end: int,
    is_a_end_cluster: bool,
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None,
    service_id: str | None,
    site_idx_map: dict[str, int],
    known_sites: set[str],
) -> int:
    """Count TL links from a clustered site toward the service interior."""
    if not tl_device_map or not service_id:
        return 0

    site_tl_map = tl_device_map.get((service_id, site), {})
    if not site_tl_map:
        return 0

    cluster_set = set(cluster)
    score = 0
    for tl_name in site_tl_map:
        remote_site = _resolve_remote_site_from_tl_name(
            tl_name,
            site,
            known_sites,
            service_id=service_id,
        )
        if remote_site is None or remote_site in cluster_set:
            continue
        remote_index = site_idx_map.get(remote_site)
        if remote_index is None:
            continue
        if is_a_end_cluster and remote_index >= cluster_end:
            score += 1
        elif not is_a_end_cluster and remote_index < cluster_start:
            score += 1
    return score


def _structural_sort_key(
    site: str,
    *,
    is_a_end_cluster: bool,
    sites_with_device: set[str],
    sites_with_demarc: set[str],
) -> tuple[int]:
    """Rank a site using device-vs-demarc structural evidence."""
    has_device = site in sites_with_device
    has_demarc = site in sites_with_demarc
    if is_a_end_cluster:
        if has_device and not has_demarc:
            return (0,)
        if has_demarc and not has_device:
            return (2,)
        return (1,)

    if has_demarc and not has_device:
        return (0,)
    if has_device and not has_demarc:
        return (2,)
    return (1,)


def _sort_icb_endpoint_cluster(
    cluster: list[str],
    *,
    is_a_end_cluster: bool,
    anchor_site: str | None,
    site_idx_map: dict[str, int],
    dominant_type: dict[str, str],
    sites_with_device: set[str],
    sites_with_demarc: set[str],
    ol_graph: dict[str, set[str]],
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None,
    service_id: str | None,
    known_sites: set[str],
    cluster_start: int,
    cluster_end: int,
) -> list[str]:
    """Sort an endpoint metro cluster using OL, structure, and TL fallback cues."""
    outer_at_start = is_a_end_cluster
    if anchor_site and dominant_type.get(anchor_site) == "XS":
        ordered = _order_sites_by_ol_distance(cluster, anchor_site, outer_at_start, ol_graph)
        if ordered is not None:
            return ordered

    structural_scores = {
        site: _structural_sort_key(
            site,
            is_a_end_cluster=is_a_end_cluster,
            sites_with_device=sites_with_device,
            sites_with_demarc=sites_with_demarc,
        )
        for site in cluster
    }
    if len(set(structural_scores.values())) > 1:
        return sorted(cluster, key=lambda site: (structural_scores[site], site))

    interior_scores = {
        site: _count_interior_directional_links(
            site,
            cluster,
            cluster_start=cluster_start,
            cluster_end=cluster_end,
            is_a_end_cluster=is_a_end_cluster,
            tl_device_map=tl_device_map,
            service_id=service_id,
            site_idx_map=site_idx_map,
            known_sites=known_sites,
        )
        for site in cluster
    }
    if len(set(interior_scores.values())) > 1:
        if is_a_end_cluster:
            return sorted(
                cluster,
                key=lambda site: (
                    interior_scores[site],
                    0 if dominant_type.get(site) == "XS" else 1,
                    site_idx_map.get(site, 0),
                ),
            )
        return sorted(
            cluster,
            key=lambda site: (
                -interior_scores[site],
                1 if dominant_type.get(site) == "XS" else 0,
                site_idx_map.get(site, 0),
            ),
        )

    if is_a_end_cluster:
        return sorted(
            cluster,
            key=lambda site: (
                0 if dominant_type.get(site) == "XS" else 1,
                site_idx_map.get(site, 0),
            ),
        )
    return sorted(
        cluster,
        key=lambda site: (1 if dominant_type.get(site) == "XS" else 0, site_idx_map.get(site, 0)),
    )


def _reorder_icb_endpoint_siblings(
    site_order: list[str],
    rows: list[InCARow],
    trunk_edges: list[tuple[str, str, str]],
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None = None,
    service_id: str | None = None,
    a_site: str | None = None,
    b_site: str | None = None,
    site_location_ids: dict[str, str | None] | None = None,
    trunk_media_lookup: dict[str, str] | None = None,
) -> list[str]:
    """Reorder co-located sites at endpoints for ICB services.

    ICB services have device (XS) at outer edge, not customer (U).
    For co-located sites at A-end, the outer site should be first.
    For co-located sites at B-end, the outer site should be last.

    Co-location is determined by topology evidence (OL trunk connections),
    NOT by string prefix matching. This handles cases where different site
    codes (e.g., NYKHD2, NYKHD3, NYKHD4) are in the same physical building.

    For on-net XS-only endpoint clusters, anchor to bearer endpoints first:
    - A-end cluster: A-site should be outermost (first in cluster)
    - B-end cluster: B-site should be outermost (last in cluster)
    Remaining siblings are ordered by OL-chain distance from the endpoint
    anchor so route flow stays continuous within the cluster.

    When endpoint anchoring cannot be resolved, TL_DEVICE direction and
    XS/U dominant row counts are used as fallback signals.

    Args:
        site_order: Current site order.
        rows: All INCA rows (to determine dominant site_type per site).
        trunk_edges: Trunk edges from route path (for metro cluster detection).
        tl_device_map: Optional TL_DEVICE lookup.
        service_id: Optional service ID for TL_DEVICE lookups.
        a_site: Optional bearer A-end site code.
        b_site: Optional bearer B-end site code.

    Returns:
        Reordered site list with ICB-appropriate endpoint orientation.
    """
    if len(site_order) < 2:
        return site_order

    clusters = identify_metro_clusters(
        site_order,
        trunk_edges,
        site_location_ids,
        trunk_media_lookup=trunk_media_lookup,
    )
    if len(clusters) < 1:
        return site_order

    site_type_counts = _build_site_type_counts(rows)
    dominant_type = {
        site: _dominant_site_type(site, site_type_counts)
        for site in {row.site_code for row in rows} | set(site_order)
    }
    site_idx_map = {site: idx for idx, site in enumerate(site_order)}
    known_sites = set(site_order)
    ol_graph = _build_colocation_trunk_graph(trunk_edges, trunk_media_lookup)

    sites_with_device: set[str] = set()
    sites_with_demarc: set[str] = set()
    for row in rows:
        if row.is_device_row and not row.is_demarcation:
            sites_with_device.add(row.site_code)
        if row.is_demarcation:
            sites_with_demarc.add(row.site_code)

    result = list(site_order)

    if (
        len(clusters) == 1
        and len(clusters[0]) >= 3
        and a_site
        and b_site
        and a_site in set(clusters[0])
        and b_site in set(clusters[0])
    ):
        result = result[::-1]
        site_idx_map = {site: idx for idx, site in enumerate(result)}
        clusters = [list(reversed(clusters[0]))]

    a_cluster = clusters[0]
    if len(a_cluster) > 1:
        a_start = result.index(a_cluster[0])
        a_end = a_start + len(a_cluster)
        a_sorted = _sort_icb_endpoint_cluster(
            a_cluster,
            is_a_end_cluster=True,
            anchor_site=a_site,
            site_idx_map=site_idx_map,
            dominant_type=dominant_type,
            sites_with_device=sites_with_device,
            sites_with_demarc=sites_with_demarc,
            ol_graph=ol_graph,
            tl_device_map=tl_device_map,
            service_id=service_id,
            known_sites=known_sites,
            cluster_start=a_start,
            cluster_end=a_end,
        )
        result[a_start:a_end] = a_sorted

    b_cluster = clusters[-1]
    if len(b_cluster) > 1 and clusters[-1] != clusters[0]:
        b_start = result.index(b_cluster[0])
        b_end = b_start + len(b_cluster)
        b_sorted = _sort_icb_endpoint_cluster(
            b_cluster,
            is_a_end_cluster=False,
            anchor_site=b_site,
            site_idx_map=site_idx_map,
            dominant_type=dominant_type,
            sites_with_device=sites_with_device,
            sites_with_demarc=sites_with_demarc,
            ol_graph=ol_graph,
            tl_device_map=tl_device_map,
            service_id=service_id,
            known_sites=known_sites,
            cluster_start=b_start,
            cluster_end=b_end,
        )
        result[b_start:b_end] = b_sorted

    return result


class InterSiteTrunkBlock(NamedTuple):
    start: int
    end: int
    route_path: str
    site: str


class InterSiteTrunkSegment(NamedTuple):
    insert_at: int
    row_indices: frozenset[int]
    rows: list[InCARow]


def _populate_display_points(rows: list[InCARow]) -> None:
    """Populate display_points for NE-Location router rows (PP-062).

    For NE-Location rows where device is a router, extract port from
    NE Information, normalize per PP-060, display in Points column
    instead of N/A.
    """
    from .tickets import _extract_port_address

    for r in rows:
        if r.is_ne_location and r.is_router:
            r.display_points = _extract_port_address(r)


def _collect_inter_site_trunk_blocks(
    sorted_rows: list[InCARow],
    inter_site_trunks: set[str],
) -> list[InterSiteTrunkBlock]:
    """Collect contiguous non-device inter-site trunk blocks."""
    blocks: list[InterSiteTrunkBlock] = []
    row_index = 0
    while row_index < len(sorted_rows):
        row = sorted_rows[row_index]
        if not row.is_device_row and row.route_path in inter_site_trunks:
            block_end = row_index + 1
            while (
                block_end < len(sorted_rows)
                and not sorted_rows[block_end].is_device_row
                and sorted_rows[block_end].route_path == row.route_path
                and sorted_rows[block_end].site_code == row.site_code
            ):
                block_end += 1
            blocks.append(
                InterSiteTrunkBlock(
                    start=row_index,
                    end=block_end,
                    route_path=row.route_path,
                    site=row.site_code,
                )
            )
            row_index = block_end
            continue
        row_index += 1
    return blocks


def _is_inter_site_separator(row: InCARow) -> bool:
    """Return whether a row may separate mergeable inter-site trunk blocks."""
    return row.is_demarcation


def _find_mergeable_inter_site_blocks(
    blocks: list[InterSiteTrunkBlock],
    sorted_rows: list[InCARow],
) -> list[tuple[InterSiteTrunkBlock, InterSiteTrunkBlock]]:
    """Find adjacent cross-site trunk blocks that can be safely interleaved."""
    merge_groups: list[tuple[InterSiteTrunkBlock, InterSiteTrunkBlock]] = []
    for index in range(len(blocks) - 1):
        first_block = blocks[index]
        second_block = blocks[index + 1]
        if (
            first_block.route_path != second_block.route_path
            or first_block.site == second_block.site
        ):
            continue
        if all(
            _is_inter_site_separator(row)
            for row in sorted_rows[first_block.end : second_block.start]
        ):
            merge_groups.append((first_block, second_block))
    return merge_groups


def _build_interleaved_trunk_rows(
    first_rows: list[InCARow],
    second_rows: list[InCARow],
) -> list[InCARow] | None:
    """Interleave paired trunk rows by fiber pair, preserving geographic block order."""
    if not all(row.site_side for row in first_rows) or not all(
        row.site_side for row in second_rows
    ):
        return None

    pairs: dict[int, dict[int, list[InCARow]]] = defaultdict(lambda: {0: [], 1: []})
    for row in first_rows:
        pairs[(row.pos - 1) // 2][0].append(row)
    for row in second_rows:
        pairs[(row.pos - 1) // 2][1].append(row)

    interleaved_rows: list[InCARow] = []
    for fiber_pair in sorted(pairs):
        interleaved_rows.extend(
            sorted(pairs[fiber_pair][0], key=lambda row: (row.pos, row.row_index))
        )
        interleaved_rows.extend(
            sorted(pairs[fiber_pair][1], key=lambda row: (row.pos, row.row_index))
        )
    return interleaved_rows


def _inter_site_trunk_endpoint_lookup(
    trunk_edges: list[tuple[str, str, str]],
) -> dict[str, tuple[str, str]]:
    """Return unambiguous inter-site trunk endpoints by route path."""
    endpoints: dict[str, tuple[str, str]] = {}
    ambiguous: set[str] = set()
    for left_site, right_site, route_path in trunk_edges:
        if left_site == right_site or route_path in ambiguous:
            continue
        endpoint_pair = (left_site, right_site)
        if route_path in endpoints and endpoints[route_path] != endpoint_pair:
            ambiguous.add(route_path)
            del endpoints[route_path]
            continue
        endpoints[route_path] = endpoint_pair
    return endpoints


def _blocks_by_route_path(
    blocks: list[InterSiteTrunkBlock],
) -> dict[str, list[InterSiteTrunkBlock]]:
    """Group collected inter-site blocks by trunk route path."""
    grouped: dict[str, list[InterSiteTrunkBlock]] = defaultdict(list)
    for block in blocks:
        grouped[block.route_path].append(block)
    return grouped


def _has_complete_endpoint_blocks(
    blocks: list[InterSiteTrunkBlock],
    endpoints: tuple[str, str],
) -> bool:
    """Return whether a trunk has exactly one passive block at each endpoint."""
    if len(blocks) != 2:
        return False
    block_sites = {block.site for block in blocks}
    return len(block_sites) == 2 and block_sites == set(endpoints)


def _rows_for_block(
    sorted_rows: list[InCARow],
    block: InterSiteTrunkBlock,
) -> list[InCARow]:
    """Return rows covered by one contiguous trunk endpoint block."""
    return list(sorted_rows[block.start : block.end])


def _build_position_grouped_trunk_rows(
    first_rows: list[InCARow],
    second_rows: list[InCARow],
) -> list[InCARow]:
    """Group endpoint blocks by current site direction and POS fallback."""
    return sorted(first_rows, key=lambda row: (row.pos, row.row_index)) + sorted(
        second_rows,
        key=lambda row: (row.pos, row.row_index),
    )


def _build_trunk_segment_rows(
    sorted_rows: list[InCARow],
    blocks: list[InterSiteTrunkBlock],
) -> list[InCARow]:
    """Build grouped rows for one complete inter-site trunk segment."""
    ordered_blocks = sorted(blocks, key=lambda block: block.start)
    first_rows = _rows_for_block(sorted_rows, ordered_blocks[0])
    second_rows = _rows_for_block(sorted_rows, ordered_blocks[1])
    interleaved_rows = _build_interleaved_trunk_rows(first_rows, second_rows)
    if interleaved_rows is not None:
        return interleaved_rows
    return _build_position_grouped_trunk_rows(first_rows, second_rows)


def _build_inter_site_trunk_segments(
    sorted_rows: list[InCARow],
    blocks: list[InterSiteTrunkBlock],
    endpoint_lookup: dict[str, tuple[str, str]],
) -> list[InterSiteTrunkSegment]:
    """Build safe segment moves for complete crossed inter-site trunks."""
    segments: list[InterSiteTrunkSegment] = []
    for route_path, route_blocks in _blocks_by_route_path(blocks).items():
        endpoints = endpoint_lookup.get(route_path)
        if endpoints is None or not _has_complete_endpoint_blocks(route_blocks, endpoints):
            continue
        ordered_blocks = sorted(route_blocks, key=lambda block: block.start)
        row_indices = frozenset(
            row_index for block in ordered_blocks for row_index in range(block.start, block.end)
        )
        segments.append(
            InterSiteTrunkSegment(
                insert_at=ordered_blocks[0].start,
                row_indices=row_indices,
                rows=_build_trunk_segment_rows(sorted_rows, ordered_blocks),
            )
        )
    return sorted(segments, key=lambda segment: segment.insert_at)


def _apply_inter_site_trunk_segments(
    sorted_rows: list[InCARow],
    segments: list[InterSiteTrunkSegment],
) -> list[InCARow]:
    """Move complete trunk segments to first occurrence and keep other rows stable."""
    segment_by_start = {segment.insert_at: segment for segment in segments}
    consumed_indices = {row_index for segment in segments for row_index in segment.row_indices}
    result: list[InCARow] = []
    for row_index, row in enumerate(sorted_rows):
        segment = segment_by_start.get(row_index)
        if segment is not None:
            result.extend(segment.rows)
        if row_index in consumed_indices:
            continue
        result.append(row)
    return result


def _interleave_inter_site_trunk_pairs(
    sorted_rows: list[InCARow],
    trunk_edges: list[tuple[str, str, str]],
) -> list[InCARow]:
    """Interleave inter-site trunk ODF rows so both endpoints of each position are adjacent.

    For cross-site trunks (e.g., DENV-DENV/3 OL01), the normal sort places all
    rows for site A together, then all rows for site B together.  This function
    reorders them so that matching positions from both sides are paired:
        A pos=236, B pos=236, A pos=237, B pos=237, ...

    Self-loop trunks (site1 == site2) are excluded -- they are already handled
    by _trunk_odf_sort_key().

    Args:
        sorted_rows: Rows already sorted by _sort_section().
        trunk_edges: Trunk edges from parse_trunk_edges().

    Returns:
        New list with inter-site trunk blocks interleaved by position.
    """
    endpoint_lookup = _inter_site_trunk_endpoint_lookup(trunk_edges)
    if not endpoint_lookup:
        return sorted_rows

    blocks = _collect_inter_site_trunk_blocks(sorted_rows, set(endpoint_lookup))
    segments = _build_inter_site_trunk_segments(sorted_rows, blocks, endpoint_lookup)
    if not segments:
        return sorted_rows

    return _apply_inter_site_trunk_segments(sorted_rows, segments)


class PreparedRouteSort(NamedTuple):
    bearer: str
    a_site: str
    b_site: str
    info_lines: list[str]
    trunk_edges: list[tuple[str, str, str]]
    site_order: list[str]
    display_site_order: list[str]
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None
    site_location_ids: dict[str, str | None] | None
    trunk_media_lookup: dict[str, str]
    trunk_route_rank: dict[str, int]
    metadata_canonical_order: bool


class MetadataCompleteness(NamedTuple):
    state: str
    missing_route_paths: list[str]


class CanonicalRouteEdge(NamedTuple):
    route_path: str
    edge_sequence: int
    edge_name: str
    a_site_code: str
    b_site_code: str
    a_site_location_id: str | None
    b_site_location_id: str | None
    a_site_side: str | None
    b_site_side: str | None
    media: str


class CanonicalRouteOrder(NamedTuple):
    edges: list[CanonicalRouteEdge]
    trunk_edges: list[tuple[str, str, str]]
    site_order: list[str]
    site_rank: dict[str, int]
    route_rank: dict[str, int]
    endpoint_lookup: dict[str, tuple[str, str]]
    site_location_ids: dict[str, str | None]
    site_sides: dict[tuple[str, str], str]
    media_lookup: dict[str, str]
    a_site: str
    b_site: str


class RouteOrderFacts(NamedTuple):
    required_route_paths: list[str]
    edges: list[CanonicalRouteEdge]
    endpoint_lookup: dict[str, tuple[str, str]]
    site_location_ids: dict[str, str | None]
    site_sides: dict[tuple[str, str], str]
    media_lookup: dict[str, str]


class SortedRouteArtifacts(NamedTuple):
    items: list[InCARow]
    migration_portion: list[InCARow] | None
    tickets: list[Ticket]


class MetadataRouteSortError(ValueError):
    """Raised when provided TRUNK_METADATA cannot support canonical route sorting."""


def _route_order_service_records(
    route_order_metadata_records: list[dict] | None,
    service_id: str | None,
) -> list[dict]:
    """Return route-order rows scoped to the active service."""
    if route_order_metadata_records is None:
        return []
    if not service_id:
        return list(route_order_metadata_records)
    service_key = service_id.strip().upper()
    return [
        record
        for record in route_order_metadata_records
        if str(record.get("SERVICE_ID", "")).strip().upper() == service_key
    ]


def _record_text(record: dict, field: str) -> str:
    """Return stripped route-order record text."""
    value = record.get(field)
    if value is None:
        return ""
    return str(value).strip()


def _record_int(record: dict, field: str) -> int | None:
    """Return integer route-order record value when present."""
    value = record.get(field)
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _route_order_edge_from_record(record: dict) -> CanonicalRouteEdge | None:
    """Build a canonical edge from one ROUTE_ORDER_METADATA row."""
    route_path = _record_text(record, "ROUTE_PATH") or _record_text(record, "EDGE_NAME")
    edge_sequence = _record_int(record, "EDGE_SEQUENCE")
    a_site = _record_text(record, "A_SITE_CODE")
    b_site = _record_text(record, "B_SITE_CODE")
    if not route_path or edge_sequence is None or not a_site or not b_site:
        return None
    return CanonicalRouteEdge(
        route_path=route_path,
        edge_sequence=edge_sequence,
        edge_name=_record_text(record, "EDGE_NAME") or route_path,
        a_site_code=a_site,
        b_site_code=b_site,
        a_site_location_id=_record_text(record, "A_SITE_LOCATION_ID") or None,
        b_site_location_id=_record_text(record, "B_SITE_LOCATION_ID") or None,
        a_site_side=_record_text(record, "A_SITE_SIDE") or None,
        b_site_side=_record_text(record, "B_SITE_SIDE") or None,
        media=_record_text(record, "MEDIA"),
    )


def _route_order_missing_required_facts(edge: CanonicalRouteEdge) -> list[str]:
    """Return missing PM facts for a required route-path edge."""
    missing: list[str] = []
    if not edge.a_site_location_id:
        missing.append("A_SITE_LOCATION_ID")
    if not edge.b_site_location_id:
        missing.append("B_SITE_LOCATION_ID")
    if not edge.a_site_side:
        missing.append("A_SITE_SIDE")
    if not edge.b_site_side:
        missing.append("B_SITE_SIDE")
    if not edge.media:
        missing.append("MEDIA")
    return missing


def _raise_route_order_error(
    service_id: str | None,
    message: str,
) -> None:
    """Raise owner-readable ROUTE_ORDER_METADATA failure text."""
    service_label = service_id or "unknown service"
    raise MetadataRouteSortError(
        f"ROUTE_ORDER_METADATA completeness partial for {service_label}: {message}. "
        "Data-driven canonical route sorting cannot continue."
    )


def _ordered_route_order_edges(records: list[dict]) -> list[CanonicalRouteEdge]:
    """Return valid canonical edges sorted by Snowflake sequence."""
    edges = [
        edge for record in records if (edge := _route_order_edge_from_record(record)) is not None
    ]
    return sorted(edges, key=lambda edge: edge.edge_sequence)


def _derive_canonical_site_order(edges: list[CanonicalRouteEdge]) -> list[str]:
    """Derive site order from sequenced edge endpoints."""
    ordered: list[str] = []
    seen: set[str] = set()
    for edge in edges:
        for site in (edge.a_site_code, edge.b_site_code):
            if site in seen:
                continue
            ordered.append(site)
            seen.add(site)
    return ordered


def _route_order_lookups(
    edges: list[CanonicalRouteEdge],
) -> tuple[
    dict[str, tuple[str, str]],
    dict[str, str | None],
    dict[tuple[str, str], str],
    dict[str, str],
]:
    """Build canonical endpoint, location, side, and media lookups."""
    endpoint_lookup: dict[str, tuple[str, str]] = {}
    site_location_ids: dict[str, str | None] = {}
    site_sides: dict[tuple[str, str], str] = {}
    media_lookup: dict[str, str] = {}
    for edge in edges:
        endpoint_lookup[edge.route_path] = (edge.a_site_code, edge.b_site_code)
        site_location_ids.setdefault(edge.a_site_code, edge.a_site_location_id)
        site_location_ids.setdefault(edge.b_site_code, edge.b_site_location_id)
        if edge.a_site_side:
            site_sides[(edge.route_path, edge.a_site_code)] = edge.a_site_side
        if edge.b_site_side:
            site_sides[(edge.route_path, edge.b_site_code)] = edge.b_site_side
        if edge.media:
            media_lookup[edge.route_path] = edge.media
    return endpoint_lookup, site_location_ids, site_sides, media_lookup


def _validate_route_order_required_edges(
    edges_by_route: dict[str, CanonicalRouteEdge],
    required_route_paths: list[str],
    service_id: str | None,
) -> None:
    """Fail closed when PM route paths lack required canonical facts."""
    missing_paths = [path for path in required_route_paths if path not in edges_by_route]
    if missing_paths:
        _raise_route_order_error(
            service_id,
            f"missing route_path(s): {', '.join(missing_paths)}",
        )

    missing_facts: list[str] = []
    for route_path in required_route_paths:
        edge = edges_by_route[route_path]
        for fact in _route_order_missing_required_facts(edge):
            missing_facts.append(f"{route_path} {fact}")
    if missing_facts:
        _raise_route_order_error(service_id, f"missing fact(s): {', '.join(missing_facts)}")


def _route_order_covers_row_sites(
    rows: list[InCARow],
    site_order: list[str],
) -> bool:
    """Return whether canonical route order covers every row site."""
    ordered_sites = set(site_order)
    missing_sites = sorted({row.site_code for row in rows if row.site_code} - ordered_sites)
    return not missing_sites


def _build_route_order_facts(
    rows: list[InCARow],
    bearer: str,
    route_order_metadata_records: list[dict] | None,
    service_id: str | None,
) -> RouteOrderFacts | None:
    """Build endpoint facts from ROUTE_ORDER_METADATA when PM records are supplied."""
    if route_order_metadata_records is None:
        return None

    required_route_paths = _metadata_route_paths_requiring_endpoints(rows, bearer)
    if not required_route_paths:
        return None

    records = _route_order_service_records(route_order_metadata_records, service_id)
    if not records:
        _raise_route_order_error(service_id, "no ROUTE_ORDER_METADATA rows for service")

    edges = _ordered_route_order_edges(records)
    edges_by_route = {edge.route_path: edge for edge in edges}
    _validate_route_order_required_edges(edges_by_route, required_route_paths, service_id)

    endpoint_lookup, site_location_ids, site_sides, media_lookup = _route_order_lookups(edges)
    return RouteOrderFacts(
        required_route_paths=required_route_paths,
        edges=edges,
        endpoint_lookup=endpoint_lookup,
        site_location_ids=site_location_ids,
        site_sides=site_sides,
        media_lookup=media_lookup,
    )


def _build_canonical_route_order(
    rows: list[InCARow],
    route_order_facts: RouteOrderFacts | None,
) -> CanonicalRouteOrder | None:
    """Build route order only when ROUTE_ORDER_METADATA covers the display path."""
    if route_order_facts is None:
        return None

    edges = route_order_facts.edges
    site_order = _derive_canonical_site_order(edges)
    if not _route_order_covers_row_sites(rows, site_order):
        return None

    route_rank = {
        route_path: index
        for index, route_path in enumerate(
            edge.route_path
            for edge in edges
            if edge.route_path in route_order_facts.required_route_paths
        )
    }
    site_rank = {site: index for index, site in enumerate(site_order)}
    return CanonicalRouteOrder(
        edges=edges,
        trunk_edges=[(edge.a_site_code, edge.b_site_code, edge.route_path) for edge in edges],
        site_order=site_order,
        site_rank=site_rank,
        route_rank=route_rank,
        endpoint_lookup=route_order_facts.endpoint_lookup,
        site_location_ids=route_order_facts.site_location_ids,
        site_sides=route_order_facts.site_sides,
        media_lookup=route_order_facts.media_lookup,
        a_site=site_order[0],
        b_site=site_order[-1],
    )


def _build_metadata_trunk_route_rank(
    rows: list[InCARow],
    trunk_edges: list[tuple[str, str, str]],
    site_order: list[str],
    metadata_trunk_names: set[str],
) -> dict[str, int]:
    """Rank metadata-resolved trunks by endpoint continuity through site_order."""
    if not metadata_trunk_names:
        return {}

    site_position = {site: index for index, site in enumerate(site_order)}
    candidates: list[tuple[int, int, int, str, str]] = []
    for left_site, right_site, trunk_name in trunk_edges:
        if trunk_name not in metadata_trunk_names:
            continue
        left_position = site_position.get(left_site)
        right_position = site_position.get(right_site)
        if left_position is None or right_position is None:
            continue
        start_position = min(left_position, right_position)
        end_position = max(left_position, right_position)
        direction = 0 if left_position <= right_position else 1
        normalized_trunk_name = trunk_name.strip().upper()
        candidates.append(
            (start_position, end_position, direction, normalized_trunk_name, trunk_name)
        )

    ranked: dict[str, int] = {}
    for *_positions, trunk_name in sorted(candidates):
        if trunk_name not in ranked:
            ranked[trunk_name] = len(ranked)
    return ranked


def _metadata_route_paths_requiring_endpoints(
    rows: list[InCARow],
    bearer: str,
) -> list[str]:
    """Return OL route paths that need metadata endpoint facts."""
    route_paths: list[str] = []
    for row in rows:
        route_path = row.route_path
        if (
            not route_path
            or route_path == bearer
            or row.is_device_row
            or row.is_demarcation
            or not re.search(r"\bOL\d+$", route_path)
            or route_path in route_paths
        ):
            continue
        route_paths.append(route_path)
    return route_paths


def _metadata_completeness(
    rows: list[InCARow],
    bearer: str,
    trunk_endpoint_lookup: dict[str, tuple[str, str]],
    trunk_metadata_records: list[dict] | None,
) -> MetadataCompleteness:
    """Classify whether provided TRUNK_METADATA covers route-sort OL trunks."""
    if trunk_metadata_records is None:
        return MetadataCompleteness("none", [])

    missing_route_paths = [
        route_path
        for route_path in _metadata_route_paths_requiring_endpoints(rows, bearer)
        if route_path not in trunk_endpoint_lookup
    ]
    if missing_route_paths:
        return MetadataCompleteness("partial", missing_route_paths)
    return MetadataCompleteness("complete", [])


def _partial_metadata_error_message(
    service_id: str | None,
    missing_route_paths: list[str],
) -> str:
    """Return owner-readable failure text for partial PM/Snowflake metadata."""
    service_label = service_id or "unknown service"
    missing = ", ".join(missing_route_paths)
    return (
        f"TRUNK_METADATA completeness partial for {service_label}: "
        f"missing endpoint facts for route_path(s): {missing}. "
        "Data-driven canonical route sorting cannot continue because "
        "PM/Snowflake metadata was provided but incomplete."
    )


def _raise_for_partial_metadata(
    service_id: str | None,
    completeness: MetadataCompleteness,
) -> None:
    """Fail closed before canonical and legacy ordering can mix."""
    if completeness.state != "partial":
        return
    raise MetadataRouteSortError(
        _partial_metadata_error_message(service_id, completeness.missing_route_paths)
    )


def _prepare_route_sort(
    rows: list[InCARow],
    service_id: str | None,
    snowflake_edge_records: list[dict] | None,
    tl_device_records: list[dict] | None,
    hub_records: list[dict] | None,
    trunk_metadata_records: list[dict] | None,
    route_order_metadata_records: list[dict] | None,
    transmission_metadata_records: list[dict] | None,
) -> PreparedRouteSort:
    """Build topology, endpoint, and metadata inputs for route sorting."""
    trunk_media_lookup = build_trunk_media_lookup(trunk_metadata_records)
    populate_trunk_media(rows, trunk_media_lookup)

    trunk_endpoint_lookup = build_trunk_endpoint_lookup(trunk_metadata_records)
    metadata_trunk_names = set(trunk_endpoint_lookup)
    transmission_endpoint_lookup = build_transmission_endpoint_lookup(
        transmission_metadata_records,
    )

    endpoints = resolve_route_endpoints(rows)
    route_order_facts = _build_route_order_facts(
        rows,
        endpoints.bearer or "",
        route_order_metadata_records,
        service_id,
    )
    if route_order_facts is not None:
        trunk_endpoint_lookup.update(route_order_facts.endpoint_lookup)
        trunk_media_lookup.update(route_order_facts.media_lookup)

    canonical_route_order = _build_canonical_route_order(rows, route_order_facts)
    if canonical_route_order is not None:
        trunk_endpoint_lookup.update(canonical_route_order.endpoint_lookup)
        trunk_media_lookup.update(canonical_route_order.media_lookup)
        endpoints = _sorting_topology.RouteEndpoints(
            bearer=endpoints.bearer,
            a_site=canonical_route_order.a_site,
            b_site=canonical_route_order.b_site,
            info_lines=[
                f"Bearer: {endpoints.bearer or 'metadata-resolved'}",
                f"A-Loc: {canonical_route_order.a_site}, B-Loc: {canonical_route_order.b_site}",
                "Route order: ROUTE_ORDER_METADATA",
            ],
        )
    metadata_completeness = _metadata_completeness(
        rows,
        endpoints.bearer or "",
        trunk_endpoint_lookup,
        trunk_metadata_records,
    )
    _raise_for_partial_metadata(service_id, metadata_completeness)
    if canonical_route_order is not None:
        route_topology = _sorting_topology.RouteTopology(
            trunk_edges=canonical_route_order.trunk_edges,
            graph=build_adjacency_graph(canonical_route_order.trunk_edges),
            site_order=canonical_route_order.site_order,
        )
    else:
        route_topology = build_route_topology(
            rows,
            endpoints.a_site,
            endpoints.b_site,
            snowflake_edge_records=snowflake_edge_records,
            service_id=service_id,
            trunk_endpoint_lookup=trunk_endpoint_lookup,
            transmission_endpoint_lookup=transmission_endpoint_lookup,
        )

    tl_device_map = None
    if tl_device_records and service_id:
        tl_device_map = build_tl_device_map(tl_device_records, service_id)

    site_location_ids = _merge_site_location_ids(
        _build_site_location_ids(hub_records),
        canonical_route_order,
        route_order_facts,
    )
    site_order = route_topology.site_order
    if canonical_route_order is None and service_mode(service_id) == "ICB":
        site_order = _reorder_icb_endpoint_siblings(
            site_order,
            rows,
            route_topology.trunk_edges,
            tl_device_map=tl_device_map,
            service_id=service_id,
            a_site=endpoints.a_site,
            b_site=endpoints.b_site,
            site_location_ids=site_location_ids,
            trunk_media_lookup=trunk_media_lookup,
        )

    if canonical_route_order is not None:
        trunk_route_rank = canonical_route_order.route_rank
    else:
        trunk_route_rank = _build_metadata_trunk_route_rank(
            rows,
            route_topology.trunk_edges,
            site_order,
            metadata_trunk_names,
        )
    info_lines = list(endpoints.info_lines)

    return PreparedRouteSort(
        bearer=endpoints.bearer or "",
        a_site=endpoints.a_site,
        b_site=endpoints.b_site,
        info_lines=info_lines,
        trunk_edges=route_topology.trunk_edges,
        site_order=site_order,
        display_site_order=list(site_order),
        tl_device_map=tl_device_map,
        site_location_ids=site_location_ids,
        trunk_media_lookup=trunk_media_lookup,
        trunk_route_rank=trunk_route_rank,
        metadata_canonical_order=canonical_route_order is not None,
    )


def _build_site_location_ids(
    hub_records: list[dict] | None,
) -> dict[str, str | None] | None:
    """Build SITE_LOCATION_ID lookup from hub metadata records."""
    if not hub_records:
        return None

    location_ids: dict[str, str | None] = {}
    for record in hub_records:
        site_code = str(record.get("SITE_CODE", "")).strip()
        loc_id = str(record.get("SITE_LOCATION_ID", "")).strip() or None
        if site_code and (loc_id or site_code not in location_ids):
            location_ids[site_code] = loc_id
    return location_ids or None


def _merge_site_location_ids(
    hub_location_ids: dict[str, str | None] | None,
    canonical_route_order: CanonicalRouteOrder | None,
    route_order_facts: RouteOrderFacts | None,
) -> dict[str, str | None] | None:
    """Merge hub and route-order location IDs with PM route order as authority."""
    merged = dict(hub_location_ids or {})
    if route_order_facts is not None:
        merged.update(route_order_facts.site_location_ids)
    if canonical_route_order is not None:
        merged.update(canonical_route_order.site_location_ids)
    return merged or None


def _append_location_id_groups(
    info: list[str],
    site_sequences: list[list[str]],
    site_location_ids: dict[str, str | None] | None,
) -> None:
    """Append co-located building groups for one or more display site sequences."""
    if not site_location_ids:
        return

    loc_to_sites: dict[str, list[str]] = defaultdict(list)
    seen_in_loc: set[tuple[str, str]] = set()
    for sequence in site_sequences:
        for site in sequence:
            loc_id = site_location_ids.get(site)
            if not loc_id or (loc_id, site) in seen_in_loc:
                continue
            loc_to_sites[loc_id].append(site)
            seen_in_loc.add((loc_id, site))

    for loc_id, sites in loc_to_sites.items():
        info.append(f"  {loc_id}: {' + '.join(sites)}")


def _canonical_sorted_rows(
    sorted_rows: list[InCARow],
    prepared: PreparedRouteSort,
) -> list[InCARow]:
    """Return the one row order consumed by display/export and ticket generation."""
    if prepared.metadata_canonical_order:
        return list(sorted_rows)
    return _interleave_inter_site_trunk_pairs(sorted_rows, prepared.trunk_edges)


def _sort_migration_route(
    info: list[str],
    rows: list[InCARow],
    service_id: str | None,
    prepared: PreparedRouteSort,
) -> SortedRouteArtifacts:
    """Sort a migration order using section-specific topology and ticketing."""
    before_rows, after_rows = split_migration_sections(rows)
    endpoint_set = {site for site in (prepared.a_site, prepared.b_site) if site}

    before_topology = build_section_route_topology(
        before_rows,
        prepared.trunk_edges,
        prepared.a_site,
        prepared.b_site,
    )
    after_topology = build_section_route_topology(
        after_rows,
        prepared.trunk_edges,
        prepared.a_site,
        prepared.b_site,
    )
    before_walk_order = before_topology.walk_order
    after_walk_order = after_topology.walk_order
    before_display_order = before_topology.display_order
    after_display_order = after_topology.display_order

    if service_mode(service_id) == "ICB":
        before_walk_order = _reorder_icb_endpoint_siblings(
            before_walk_order,
            before_rows,
            prepared.trunk_edges,
            tl_device_map=prepared.tl_device_map,
            service_id=service_id,
            a_site=prepared.a_site,
            b_site=prepared.b_site,
            site_location_ids=prepared.site_location_ids,
            trunk_media_lookup=prepared.trunk_media_lookup,
        )
        after_walk_order = _reorder_icb_endpoint_siblings(
            after_walk_order,
            after_rows,
            prepared.trunk_edges,
            tl_device_map=prepared.tl_device_map,
            service_id=service_id,
            a_site=prepared.a_site,
            b_site=prepared.b_site,
            site_location_ids=prepared.site_location_ids,
            trunk_media_lookup=prepared.trunk_media_lookup,
        )
        before_display_order = _filter_site_order_for_data(
            before_walk_order,
            {row.site_code for row in before_rows},
            endpoint_set,
        )
        after_display_order = _filter_site_order_for_data(
            after_walk_order,
            {row.site_code for row in after_rows},
            endpoint_set,
        )

    info.append(f"Site order (current): {' -> '.join(before_display_order)}")
    info.append(f"Site order (migrated): {' -> '.join(after_display_order)}")
    _append_location_id_groups(
        info,
        [before_display_order, after_display_order],
        prepared.site_location_ids,
    )

    sorted_before = _sort_section(
        before_rows,
        before_walk_order,
        prepared.trunk_edges,
        prepared.bearer,
        tl_device_map=prepared.tl_device_map,
        service_id=service_id,
        trunk_route_rank=prepared.trunk_route_rank,
    )
    sorted_after = _sort_section(
        after_rows,
        after_walk_order,
        prepared.trunk_edges,
        prepared.bearer,
        tl_device_map=prepared.tl_device_map,
        service_id=service_id,
        trunk_route_rank=prepared.trunk_route_rank,
    )
    _populate_display_points(sorted_before)
    _populate_display_points(sorted_after)

    canonical_before = _canonical_sorted_rows(sorted_before, prepared)
    canonical_after = _canonical_sorted_rows(sorted_after, prepared)
    patch_class = classify_patch_points(canonical_after)
    migration_portion = build_migration_portion(canonical_after, patch_class)
    decom_from_before = [row for row in canonical_before if row.classification == "DECOMMISSION"]
    tickets = generate_tickets(
        canonical_after,
        after_walk_order,
        prepared.trunk_edges,
        is_migration=True,
        patch_classifications=patch_class,
        decom_rows=decom_from_before,
        before_rows=canonical_before,
        site_location_ids=prepared.site_location_ids,
        trunk_media_lookup=prepared.trunk_media_lookup,
    )

    hotcut_sites = {ann.split(" at ")[-1] for ptype, ann in patch_class if ptype == "HOT-CUT"}
    for ticket in tickets:
        if any(site in hotcut_sites for site in ticket.sites):
            ticket.is_hotcut = True

    return SortedRouteArtifacts(
        items=canonical_before,
        migration_portion=migration_portion,
        tickets=tickets,
    )


def _sort_standard_route(
    info: list[str],
    rows: list[InCARow],
    service_id: str | None,
    prepared: PreparedRouteSort,
) -> SortedRouteArtifacts:
    """Sort a standard add order using the prepared full-route topology."""
    info.append(f"Site order: {' -> '.join(prepared.display_site_order)}")
    _append_location_id_groups(
        info,
        [prepared.display_site_order],
        prepared.site_location_ids,
    )

    sorted_rows = _sort_section(
        rows,
        prepared.site_order,
        prepared.trunk_edges,
        prepared.bearer,
        tl_device_map=prepared.tl_device_map,
        service_id=service_id,
        trunk_route_rank=prepared.trunk_route_rank,
    )
    _populate_display_points(sorted_rows)
    canonical_rows = _canonical_sorted_rows(sorted_rows, prepared)
    tickets = generate_tickets(
        canonical_rows,
        prepared.site_order,
        prepared.trunk_edges,
        is_migration=False,
        site_location_ids=prepared.site_location_ids,
        trunk_media_lookup=prepared.trunk_media_lookup,
    )
    return SortedRouteArtifacts(
        items=canonical_rows,
        migration_portion=None,
        tickets=tickets,
    )


def sort_inca_route_path(
    rows: list[InCARow],
    service_type: str | None = None,
    service_id: str | None = None,
    snowflake_edge_records: list[dict] | None = None,
    tl_device_records: list[dict] | None = None,
    hub_records: list[dict] | None = None,
    trunk_metadata_records: list[dict] | None = None,
    route_order_metadata_records: list[dict] | None = None,
    transmission_metadata_records: list[dict] | None = None,
    bo_fibers: list[dict] | None = None,
) -> SortResult:
    """Main sorting and ticket generation pipeline.

    Args:
        rows: INCA rows from read_excel().
        service_type: Optional service type override (e.g., 'Backbone IP').
        service_id: Optional service identifier (e.g., 'IC-136025', 'ICB-820729').
            Service identifier (e.g., 'IC-136025', 'ICB-820729').
        snowflake_edge_records: Optional EDGES records from Snowflake hierarchy walk.
        tl_device_records: Optional TL_DEVICE records for within-site ordering.
        hub_records: Optional HUB_SITE records for consumable hub notations.
        trunk_metadata_records: Optional TRUNK_METADATA records.
        route_order_metadata_records: Optional ROUTE_ORDER_METADATA records.
        transmission_metadata_records: Optional TRANSMISSION_METADATA records
            for transport-level edge endpoint resolution.
        bo_fibers: Optional BO_FIBERS records for INCA BUG notation enrichment.

    Returns:
        SortResult with rows, notations, tickets, info_lines, all_planned, bearer.
    """
    info: list[str] = []

    if not rows:
        return SortResult([], [], [], ["ERROR: No data rows found in input."], False, "")

    prepared = _prepare_route_sort(
        rows,
        service_id=service_id,
        snowflake_edge_records=snowflake_edge_records,
        tl_device_records=tl_device_records,
        hub_records=hub_records,
        trunk_metadata_records=trunk_metadata_records,
        route_order_metadata_records=route_order_metadata_records,
        transmission_metadata_records=transmission_metadata_records,
    )
    info.extend(prepared.info_lines)

    # Detect migration
    migration = is_migration_order(rows)
    if service_type:
        info.append(f"Service type: {service_type}")

    # Check all-planned
    all_planned = all(r.classification == "NEW" for r in rows)

    if migration:
        sorted_artifacts = _sort_migration_route(info, rows, service_id, prepared)
    else:
        sorted_artifacts = _sort_standard_route(info, rows, service_id, prepared)

    # Generate notations (Pure Add suppresses PLANNED; Migration suppresses both)
    is_pure_add = all_planned and not migration
    hub_check_sites = {
        site
        for ticket in sorted_artifacts.tickets
        if ticket.stage != 2
        for site in ticket.sites
        if site
    }
    hub_check_sites.update(
        line.site_code
        for ticket in sorted_artifacts.tickets
        if ticket.stage != 2
        for line in ticket.lines
        if line.site_code
    )
    notations = generate_notations(
        rows,
        is_pure_add=is_pure_add,
        trunk_edges=prepared.trunk_edges,
        is_migration=migration,
        hub_records=hub_records,
        bo_fibers=bo_fibers,
        hub_check_sites=hub_check_sites,
    )

    return SortResult(
        sorted_artifacts.items,
        notations,
        sorted_artifacts.tickets,
        info,
        all_planned,
        prepared.bearer,
        migration_portion=sorted_artifacts.migration_portion,
    )


def _sort_section(
    rows: list[InCARow],
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    bearer: str | None,
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None = None,
    service_id: str | None = None,
    trunk_route_rank: dict[str, int] | None = None,
) -> list[InCARow]:
    """Sort a section of rows (used for both full sort and migration subsections).

    Args:
        rows: Rows to sort.
        site_order: Geographic site order.
        trunk_edges: Trunk edges.
        bearer: Bearer route path name.
        tl_device_map: Optional TL_DEVICE lookup from build_tl_device_map().
        service_id: Optional service ID for TL_DEVICE lookups.

    Returns:
        Sorted list of rows.
    """
    site_groups = group_rows_by_site(rows)

    sorted_rows: list[InCARow] = []
    for site in site_order:
        if site not in site_groups:
            continue
        site_rows = site_groups[site]
        ordered = order_within_site(
            site_rows,
            site,
            site_order,
            trunk_edges,
            bearer,
            tl_device_map=tl_device_map,
            service_id=service_id,
            trunk_route_rank=trunk_route_rank,
        )
        sorted_rows.extend(ordered)

    # Include any rows at sites not in the site_order (shouldn't happen, but safe)
    ordered_sites = set(site_order)
    for site, site_rows in site_groups.items():
        if site not in ordered_sites:
            site_rows.sort(
                key=lambda r: (
                    r.pos,
                    r.cabling_location,
                    _cabling_point_int(r.cabling_points),
                    r.row_index,
                )
            )
            sorted_rows.extend(site_rows)

    return sorted_rows


def _build_sorting_arg_parser() -> argparse.ArgumentParser:
    """Build the sorting CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="INCA Route Path Sorting and Ticket Generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python inca_sorter.py input.xlsx\n"
            '  python inca_sorter.py input.xlsx --service-type "Backbone IP"\n'
            "  python inca_sorter.py input.xlsx --output sorted.xlsx\n"
            "  python inca_sorter.py --snowflake-a trunk.csv --snowflake-b devices.csv\n"
        ),
    )
    parser.add_argument("input", nargs="?", help="Input INCA Excel export (.xlsx)")
    parser.add_argument(
        "--service-type",
        help='Service type (e.g., "Backbone IP", "Backbone DWDM")',
        default=None,
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output Excel file path (.xlsx)",
        default=None,
    )
    parser.add_argument(
        "--snowflake-a",
        help="Snowflake Query A CSV (trunk ODF rows)",
        default=None,
    )
    parser.add_argument(
        "--snowflake-b",
        help="Snowflake Query B CSV (device rows with cable trace)",
        default=None,
    )
    parser.add_argument(
        "--snowflake-c",
        help="Snowflake Query C CSV (ODUC chassis function, optional)",
        default=None,
    )
    parser.add_argument(
        "--snowflake-combined",
        help="Combined Snowflake CSV export (QID,ROW_DATA format from prod_all)",
        default=None,
    )
    return parser


def _validate_sorting_cli_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    """Validate supported CLI input combinations."""
    if args.snowflake_combined:
        return
    if args.snowflake_a and not args.snowflake_b:
        parser.error("--snowflake-b is required when --snowflake-a is specified")
    if not args.snowflake_a and not args.input:
        parser.error(
            "Either input .xlsx, --snowflake-a/--snowflake-b, or --snowflake-combined is required"
        )


def _print_cli_sort_result(result: SortResult, *, service_id: str) -> None:
    """Print the shared owner-facing analysis, route, notation, and tickets."""
    print()
    print("-" * 80)
    print("SERVICE ANALYSIS")
    print("-" * 80)
    for message in result.info_lines:
        print(f"  {message}")
    print()

    print(format_sorted_route_path(result.rows, migration_portion=result.migration_portion))

    notations_str = format_notations(result.notations)
    if notations_str:
        print(notations_str)

    print(format_tickets(result.tickets, all_planned=result.all_planned, service_id=service_id))


def _print_combined_service_inventory(data: SnowflakeCombinedData, csv_path: str) -> None:
    """Print combined-export discovery output before per-service processing."""
    print(f"Reading combined Snowflake CSV: {os.path.basename(csv_path)}")
    print(f"Services found: {len(data.services)}")
    for service_id in sorted(data.services):
        print(f"  {service_id} ({len(data.services[service_id])} rows)")
    print()


def _run_combined_sorting_cli(args: argparse.Namespace) -> None:
    """Run combined CSV processing and print grouped per-service output."""
    combined_data = read_snowflake_combined_csv(args.snowflake_combined)
    if not combined_data.services:
        print("ERROR: No services found in combined CSV.", file=sys.stderr)
        sys.exit(1)

    _print_combined_service_inventory(combined_data, args.snowflake_combined)
    for service_id in sorted(combined_data.services):
        print("=" * 64)
        print(f"SERVICE: {service_id}")
        print("=" * 64)

        result = sort_inca_route_path(
            combined_data.services[service_id],
            service_id=service_id,
            snowflake_edge_records=combined_data.edge_records,
            tl_device_records=combined_data.tl_device_records,
            trunk_metadata_records=combined_data.trunk_metadata,
            route_order_metadata_records=combined_data.route_order_metadata,
            transmission_metadata_records=combined_data.transmission_metadata,
            hub_records=combined_data.hub_records,
            bo_fibers=combined_data.bo_fibers,
        )
        _print_cli_sort_result(result, service_id=service_id)
        print()


def _read_cli_rows(args: argparse.Namespace) -> list[InCARow]:
    """Read Excel or split Snowflake CSV inputs for the standard CLI path."""
    if args.snowflake_a:
        print(
            f"Reading Snowflake CSVs: {os.path.basename(args.snowflake_a)}, {os.path.basename(args.snowflake_b)}"
        )
        if args.snowflake_c:
            print(f"  ODUC context: {os.path.basename(args.snowflake_c)}")
        return read_snowflake_csv(args.snowflake_a, args.snowflake_b, args.snowflake_c)

    print(f"Reading: {os.path.basename(args.input)}")
    return read_excel(args.input)


def _derive_cli_service_id(args: argparse.Namespace, rows: list[InCARow]) -> str:
    """Derive the owner-visible service identifier for standard CLI input."""
    if rows and rows[0].service_id:
        return f"ICB-{rows[0].service_id}"
    if not args.input:
        return ""

    header_id = extract_service_id(args.input)
    if header_id:
        return header_id

    basename = os.path.basename(args.input)
    icb_match = re.search(r"(ICB-\d+)", basename, re.IGNORECASE)
    if icb_match:
        return icb_match.group(1).upper()

    numeric_match = re.search(r"(\d{6})", basename)
    return f"ICB-{numeric_match.group(1)}" if numeric_match else ""


def _write_cli_output_if_requested(
    args: argparse.Namespace,
    result: SortResult,
    service_id: str,
) -> None:
    """Write the optional output workbook for the standard CLI path."""
    if not args.output:
        return

    write_output_excel(
        args.output,
        result.rows,
        result.notations,
        result.tickets,
        migration_portion=result.migration_portion,
        service_id=service_id,
        bearer=result.bearer,
    )
    print(f"Output written to: {os.path.basename(args.output)}")


def _run_standard_sorting_cli(args: argparse.Namespace) -> None:
    """Run the standard single-service CLI path."""
    rows = _read_cli_rows(args)
    service_id = _derive_cli_service_id(args, rows)
    if not rows:
        print("ERROR: No data rows found in input file.", file=sys.stderr)
        sys.exit(1)

    result = sort_inca_route_path(
        rows,
        service_type=args.service_type,
        service_id=service_id,
    )
    _print_cli_sort_result(result, service_id=service_id)
    _write_cli_output_if_requested(args, result, service_id)


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = _build_sorting_arg_parser()
    args = parser.parse_args(argv)

    # Validate arguments
    _validate_sorting_cli_args(parser, args)
    if args.snowflake_combined:
        _run_combined_sorting_cli(args)
        return

    _run_standard_sorting_cli(args)


if __name__ == "__main__":
    main()
