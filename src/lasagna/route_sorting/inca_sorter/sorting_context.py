"""Shared imports and compatibility aliases for route sorting modules."""

# ruff: noqa: F401,I001

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
    _DirectionInfo,
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

__all__ = [
    "InCARow",
    "NamedTuple",
    "SiteAssemblyGroups",
    "SiteBoundaryContext",
    "SiteRowBuckets",
    "SnowflakeCombinedData",
    "SortResult",
    "Ticket",
    "_DirectionInfo",
    "_append_route_path_row",
    "_apply_device_group_cable_fallback",
    "_apply_endpoint_self_loop_interleave",
    "_assemble_a_end_site_rows",
    "_assemble_b_end_site_rows",
    "_assemble_default_middle_site_rows",
    "_assemble_departure_only_middle_site_rows",
    "_assemble_middle_site_rows",
    "_assemble_selfloop_only_middle_site_rows",
    "_build_site_boundary_context",
    "_cabling_point_int",
    "_classify_inter_site_route_bucket",
    "_classify_route_path_bucket",
    "_classify_self_loop_route_bucket",
    "_classify_self_loop_row_with_direction",
    "_classify_self_loop_rows",
    "_collect_directional_ne_parts",
    "_combine_multi_path_self_loops",
    "_extract_endpoint_self_loop_rows",
    "_filter_site_order_for_data",
    "_flatten_sorted_device_groups",
    "_group_device_rows_by_ne",
    "_group_ne_parts",
    "_group_site_rows",
    "_infer_demarc_endpoints",
    "_interleave_self_loops_by_route_path",
    "_order_devices_by_direction",
    "_ordered_building_rank",
    "_ordered_non_empty_buildings",
    "_prepare_site_assembly_groups",
    "_resolve_direction_buildings",
    "_resolve_remote_site_from_tl_name",
    "_score_device_groups_from_direction",
    "_should_interleave_endpoint_self_loop",
    "_site_assembly",
    "_site_device_area_rows",
    "_site_device_buildings",
    "_sort_positional_row_groups",
    "_sort_site_boundary_rows",
    "_sorting_topology",
    "_split_demarcation_rows",
    "_split_self_loop_rows",
    "_trunk_cabinet_rank",
    "_trunk_odf_sort_key",
    "argparse",
    "build_adjacency_graph",
    "build_migration_portion",
    "build_route_topology",
    "build_section_route_topology",
    "build_tl_device_map",
    "build_transmission_endpoint_lookup",
    "build_trunk_endpoint_lookup",
    "build_trunk_media_lookup",
    "classify_patch_points",
    "defaultdict",
    "deque",
    "extract_service_id",
    "format_notations",
    "format_sorted_route_path",
    "format_tickets",
    "generate_notations",
    "generate_tickets",
    "get_trunk_for_site_pair",
    "group_rows_by_site",
    "identify_metro_clusters",
    "is_colocation_trunk",
    "is_migration_order",
    "logger",
    "logging",
    "order_within_site",
    "os",
    "parse_bearer_endpoints",
    "parse_snowflake_edges",
    "parse_trunk_edges",
    "populate_trunk_media",
    "re",
    "read_excel",
    "read_snowflake_combined_csv",
    "read_snowflake_csv",
    "resolve_route_endpoints",
    "service_mode",
    "split_migration_sections",
    "sys",
    "walk_graph",
    "write_output_excel",
]
