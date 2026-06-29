"""Inter-site trunk and same-location handoff helpers."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .sorting_context import *  # noqa: F403
from .sorting_site_order import (
    DeviceHandoffSegment,
    InterSiteTrunkBlock,
    InterSiteTrunkSegment,
    SameLocationHandoffAnchor,
)


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


def _site_types_for_trunk_endpoint(
    rows: list[InCARow],
    route_path: str,
    site: str,
) -> set[str]:
    """Return passive endpoint site types for one trunk route path."""
    return {
        row.site_type
        for row in rows
        if row.route_path == route_path
        and row.site_code == site
        and not row.is_device_row
        and not row.is_demarcation
    }


def _same_location_handoff_site(
    left_site: str,
    right_site: str,
    route_path: str,
    rows: list[InCARow],
    site_location_ids: dict[str, str | None],
) -> tuple[str, str] | None:
    """Return outer/handoff sites for same-building U-to-XS trunk handoff."""
    left_location = site_location_ids.get(left_site)
    right_location = site_location_ids.get(right_site)
    if not left_location or left_location != right_location:
        return None

    left_types = _site_types_for_trunk_endpoint(rows, route_path, left_site)
    right_types = _site_types_for_trunk_endpoint(rows, route_path, right_site)
    if "U" in left_types and "XS" in right_types:
        return left_site, right_site
    if "U" in right_types and "XS" in left_types:
        return right_site, left_site
    return None


def _same_location_handoff_anchors(
    rows: list[InCARow],
    trunk_edges: list[tuple[str, str, str]],
    site_location_ids: dict[str, str | None] | None,
) -> list[SameLocationHandoffAnchor]:
    """Find same-location U/XS trunk endpoints that can anchor a device hop."""
    if not site_location_ids:
        return []

    anchors: list[SameLocationHandoffAnchor] = []
    for left_site, right_site, route_path in trunk_edges:
        if left_site == right_site:
            continue
        handoff_pair = _same_location_handoff_site(
            left_site,
            right_site,
            route_path,
            rows,
            site_location_ids,
        )
        if handoff_pair is None:
            continue
        outer_site, handoff_site = handoff_pair
        anchors.append(
            SameLocationHandoffAnchor(
                trunk_route_path=route_path,
                outer_site=outer_site,
                handoff_site=handoff_site,
            )
        )
    return anchors


def _site_tl_map(
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]],
    service_id: str | None,
    site: str,
) -> dict[str, list[str]]:
    """Return TL_DEVICE map for one site, scoped by service when known."""
    if service_id:
        return tl_device_map.get((service_id, site), {})

    matches = [
        site_map for (_sid, site_code), site_map in tl_device_map.items() if site_code == site
    ]
    return matches[0] if len(matches) == 1 else {}


def _matching_device_row_indices(
    rows: list[InCARow],
    site: str,
    ne_parts: list[str],
) -> list[int]:
    """Return device row positions whose NE Information contains TL_DEVICE parts."""
    return [
        index
        for index, row in enumerate(rows)
        if row.site_code == site
        and row.is_device_row
        and not row.is_demarcation
        and row.ne_info
        and any(ne_part in row.ne_info for ne_part in ne_parts)
    ]


def _remote_site_for_tl(
    tl_name: str,
    handoff_site: str,
    known_sites: set[str],
    service_id: str | None,
) -> str | None:
    """Resolve TL remote site from structured TL endpoint/name data."""
    if not service_id:
        service_id = ""
    return _resolve_remote_site_from_tl_name(
        tl_name,
        handoff_site,
        known_sites,
        service_id=service_id,
    )


def _trunk_segment_end_index(
    rows: list[InCARow],
    route_path: str,
) -> int | None:
    """Return final passive row index for a grouped trunk segment."""
    positions = [
        index
        for index, row in enumerate(rows)
        if row.route_path == route_path and not row.is_device_row and not row.is_demarcation
    ]
    return max(positions) if positions else None


def _handoff_device_segment(
    rows: list[InCARow],
    anchor: SameLocationHandoffAnchor,
    local_ne_parts: list[str],
    remote_site: str,
    remote_ne_parts: list[str],
) -> DeviceHandoffSegment | None:
    """Build one TL_DEVICE-proven device handoff segment."""
    insert_after = _trunk_segment_end_index(rows, anchor.trunk_route_path)
    if insert_after is None:
        return None

    local_indices = _matching_device_row_indices(rows, anchor.handoff_site, local_ne_parts)
    remote_indices = _matching_device_row_indices(rows, remote_site, remote_ne_parts)
    if not local_indices or not remote_indices:
        return None

    ordered_indices = local_indices + remote_indices
    return DeviceHandoffSegment(
        insert_after=insert_after,
        row_indices=frozenset(ordered_indices),
        rows=[rows[index] for index in ordered_indices],
    )


def _handoff_segments_for_anchor(
    rows: list[InCARow],
    anchor: SameLocationHandoffAnchor,
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]],
    service_id: str | None,
    known_sites: set[str],
    claimed_indices: set[int],
) -> list[DeviceHandoffSegment]:
    """Build device handoff segments for one same-location trunk endpoint."""
    segments: list[DeviceHandoffSegment] = []
    local_tl_map = _site_tl_map(tl_device_map, service_id, anchor.handoff_site)
    for tl_name, local_ne_parts in local_tl_map.items():
        remote_site = _remote_site_for_tl(tl_name, anchor.handoff_site, known_sites, service_id)
        remote_ne_parts = _site_tl_map(tl_device_map, service_id, remote_site or "").get(
            tl_name,
            [],
        )
        if not remote_site or not remote_ne_parts:
            continue
        segment = _handoff_device_segment(
            rows,
            anchor,
            local_ne_parts,
            remote_site,
            remote_ne_parts,
        )
        if segment is None or segment.row_indices & claimed_indices:
            continue
        claimed_indices.update(segment.row_indices)
        segments.append(segment)
    return segments


def _build_same_location_device_handoff_segments(
    rows: list[InCARow],
    anchors: list[SameLocationHandoffAnchor],
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]],
    service_id: str | None,
) -> list[DeviceHandoffSegment]:
    """Build all TL_DEVICE-proven same-location handoff moves."""
    known_sites = {row.site_code for row in rows if row.site_code}
    claimed_indices: set[int] = set()
    segments: list[DeviceHandoffSegment] = []
    for anchor in anchors:
        segments.extend(
            _handoff_segments_for_anchor(
                rows,
                anchor,
                tl_device_map,
                service_id,
                known_sites,
                claimed_indices,
            )
        )
    return sorted(segments, key=lambda segment: (segment.insert_after, min(segment.row_indices)))


def _apply_device_handoff_segments(
    rows: list[InCARow],
    segments: list[DeviceHandoffSegment],
) -> list[InCARow]:
    """Move TL_DEVICE-proven device hops after their local ODF handoff."""
    if not segments:
        return rows

    segments_by_insert_after: dict[int, list[DeviceHandoffSegment]] = defaultdict(list)
    consumed_indices = {index for segment in segments for index in segment.row_indices}
    for segment in segments:
        segments_by_insert_after[segment.insert_after].append(segment)

    result: list[InCARow] = []
    for index, row in enumerate(rows):
        if index not in consumed_indices:
            result.append(row)
        for segment in segments_by_insert_after.get(index, []):
            result.extend(segment.rows)
    return result


def _apply_same_location_device_handoffs(
    rows: list[InCARow],
    trunk_edges: list[tuple[str, str, str]],
    site_location_ids: dict[str, str | None] | None,
    tl_device_map: dict[tuple[str, str], dict[str, list[str]]] | None,
    service_id: str | None,
) -> list[InCARow]:
    """Keep same-building U/XS handoff device rows with their trunk endpoint."""
    if not tl_device_map:
        return rows

    anchors = _same_location_handoff_anchors(rows, trunk_edges, site_location_ids)
    if not anchors:
        return rows

    segments = _build_same_location_device_handoff_segments(
        rows,
        anchors,
        tl_device_map,
        service_id,
    )
    return _apply_device_handoff_segments(rows, segments)
