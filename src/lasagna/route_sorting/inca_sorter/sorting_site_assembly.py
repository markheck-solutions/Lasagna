"""Within-site row grouping and ordering helpers."""

# ruff: noqa: F401,I001
from __future__ import annotations

from .sorting_site_assembly_base import (
    SiteAssemblyGroups,
    SiteBoundaryContext,
    SiteRowBuckets,
    _apply_device_group_cable_fallback,
    _classify_self_loop_row_with_direction,
    _classify_self_loop_rows,
    _collect_directional_ne_parts,
    _flatten_sorted_device_groups,
    _group_device_rows_by_ne,
    _group_ne_parts,
    _interleave_self_loops_by_route_path,
    _order_devices_by_direction,
    _ordered_building_rank,
    _orient_endpoint_device_groups,
    _resolve_direction_buildings,
    _resolve_remote_site_from_tl_name,
    _score_device_groups_from_direction,
    _trunk_cabinet_rank,
    get_trunk_for_site_pair,
    group_rows_by_site,
)

from .sorting_site_assembly_groups import (
    _append_route_path_row,
    _apply_endpoint_self_loop_interleave,
    _build_site_boundary_context,
    _classify_inter_site_route_bucket,
    _classify_route_path_bucket,
    _classify_self_loop_route_bucket,
    _extract_endpoint_self_loop_rows,
    _group_site_rows,
    _ordered_non_empty_buildings,
    _prepare_site_assembly_groups,
    _should_interleave_endpoint_self_loop,
    _site_device_buildings,
    _sort_positional_row_groups,
    _sort_site_boundary_rows,
    _split_demarcation_rows,
    _trunk_odf_sort_key,
)

from .sorting_site_assembly_rows import (
    _assemble_a_end_site_rows,
    _assemble_b_end_site_rows,
    _assemble_default_middle_site_rows,
    _assemble_departure_only_middle_site_rows,
    _assemble_middle_site_rows,
    _assemble_selfloop_only_middle_site_rows,
    _combine_multi_path_self_loops,
    _site_device_area_rows,
    _split_self_loop_rows,
    order_within_site,
)
