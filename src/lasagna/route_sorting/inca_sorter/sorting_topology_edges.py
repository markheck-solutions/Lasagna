"""Snowflake edge parsing for route topology."""

from __future__ import annotations

import re
from collections import defaultdict, deque

from .sorting_topology_models import ParsedSnowflakeEdge, RouteEdge


def _normalize_trunk_name_key(trunk_name: str) -> str:
    """Normalize a trunk name for metadata lookups."""
    return trunk_name.strip().upper()


def parse_snowflake_edges(
    edge_records: list[dict],
    service_id: str,
    known_sites: set[str],
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None = None,
    transmission_endpoint_lookup: dict[str, tuple[str, str]] | None = None,
) -> list[RouteEdge]:
    """Parse Snowflake hierarchy edge names into site-pair tuples."""
    parsed = _collect_snowflake_edges(
        edge_records,
        service_id,
        known_sites,
        trunk_endpoint_lookup,
        transmission_endpoint_lookup,
    )
    filtered = _filter_service_level_shortcuts(parsed)
    return [(edge.site1, edge.site2, edge.edge_name) for edge in filtered]


def _collect_snowflake_edges(
    edge_records: list[dict],
    service_id: str,
    known_sites: set[str],
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None,
    transmission_endpoint_lookup: dict[str, tuple[str, str]] | None,
) -> list[ParsedSnowflakeEdge]:
    """Collect unique, site-resolved edges for a single service."""
    parsed: list[ParsedSnowflakeEdge] = []
    seen_edge_names: set[str] = set()
    for record in edge_records:
        parsed_edge = _parse_snowflake_edge_record(
            record,
            service_id,
            known_sites,
            seen_edge_names,
            trunk_endpoint_lookup,
            transmission_endpoint_lookup,
        )
        if parsed_edge is not None:
            parsed.append(parsed_edge)
    return parsed


def _parse_snowflake_edge_record(
    record: dict,
    service_id: str,
    known_sites: set[str],
    seen_edge_names: set[str],
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None,
    transmission_endpoint_lookup: dict[str, tuple[str, str]] | None,
) -> ParsedSnowflakeEdge | None:
    """Resolve one raw Snowflake hierarchy row into a parsed edge."""
    if str(record.get("SERVICE_ID", "")).strip() != service_id:
        return None

    edge_name = str(record.get("EDGE_NAME", "")).strip()
    if not edge_name or edge_name in seen_edge_names:
        return None
    seen_edge_names.add(edge_name)

    site1, site2 = _resolve_edge_sites(
        edge_name,
        known_sites,
        trunk_endpoint_lookup,
        transmission_endpoint_lookup,
    )
    if not site1 or not site2:
        return None
    if site1 not in known_sites and site2 not in known_sites:
        return None

    return ParsedSnowflakeEdge(
        level=_parse_edge_level(str(record.get("LEVEL", "")).strip().upper()),
        site1=site1,
        site2=site2,
        edge_name=edge_name,
    )


def _resolve_edge_sites(
    edge_name: str,
    known_sites: set[str],
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None,
    transmission_endpoint_lookup: dict[str, tuple[str, str]] | None,
) -> tuple[str, str]:
    """Resolve edge endpoints from metadata lookups or a name parser."""
    if trunk_endpoint_lookup and edge_name in trunk_endpoint_lookup:
        return trunk_endpoint_lookup[edge_name]
    normalized_edge_name = _normalize_trunk_name_key(edge_name)
    if trunk_endpoint_lookup and normalized_edge_name in trunk_endpoint_lookup:
        return trunk_endpoint_lookup[normalized_edge_name]
    if transmission_endpoint_lookup and edge_name in transmission_endpoint_lookup:
        return transmission_endpoint_lookup[edge_name]
    return _parse_edge_site_pair(edge_name, known_sites)


def _parse_edge_level(level_raw: str) -> int:
    """Convert a level label like L2 into its numeric value."""
    match = re.match(r"^L(\d+)$", level_raw)
    if not match:
        return 0
    return int(match.group(1))


def _filter_service_level_shortcuts(
    parsed_edges: list[ParsedSnowflakeEdge],
) -> list[ParsedSnowflakeEdge]:
    """Drop L1 edges only when non-L1 edges already connect the same sites."""
    non_l1_graph = _build_non_l1_graph(parsed_edges)
    return [
        edge
        for edge in parsed_edges
        if edge.level != 1 or not _graph_has_path(edge.site1, edge.site2, non_l1_graph)
    ]


def _build_non_l1_graph(parsed_edges: list[ParsedSnowflakeEdge]) -> dict[str, set[str]]:
    """Build a graph containing only L2+ adjacency edges."""
    graph: dict[str, set[str]] = defaultdict(set)
    for edge in parsed_edges:
        if edge.level <= 1:
            continue
        graph[edge.site1].add(edge.site2)
        graph[edge.site2].add(edge.site1)
    return graph


def _graph_has_path(
    start: str,
    end: str,
    graph: dict[str, set[str]],
) -> bool:
    """Return True when a graph already connects two sites."""
    if start == end:
        return True
    if start not in graph or end not in graph:
        return False

    visited: set[str] = {start}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in graph.get(current, set()):
            if neighbor == end:
                return True
            if neighbor in visited:
                continue
            visited.add(neighbor)
            queue.append(neighbor)
    return False


def _parse_edge_site_pair(edge_name: str, known_sites: set[str]) -> tuple[str, str]:
    """Extract two site codes from a hierarchy edge name."""
    cleaned = _strip_edge_suffix(edge_name)
    return _split_trunk_site_pair(cleaned, known_sites)


def _strip_edge_suffix(edge_name: str) -> str:
    """Remove transport-type and speed suffixes from an edge name."""
    cleaned = re.sub(r"\s+(?:OL|OCGX|O[DT]UC?|WDM|WT|MCH|O[\d.]+[GT]|S\d+G)\S*$", "", edge_name)
    if not cleaned:
        cleaned = edge_name
    stripped_speed = re.sub(r"\s+\d+(?:G(?:E)?|ZR)\d*$", "", cleaned)
    return stripped_speed or cleaned


def _split_trunk_site_pair(pair_str: str, known_sites: set[str]) -> tuple[str, str]:
    """Split a hyphenated site-pair string into two site codes."""
    hyphen_positions = [index for index, char in enumerate(pair_str) if char == "-"]
    best_match: tuple[str, str] | None = None

    for position in hyphen_positions:
        left = pair_str[:position].strip()
        right = pair_str[position + 1 :].strip()
        left_site = _extract_site_from_trunk_half(left, known_sites)
        right_site = _extract_site_from_trunk_half(right, known_sites)
        if not left_site or not right_site:
            continue
        if left_site in known_sites and right_site in known_sites:
            return (left_site, right_site)
        if best_match is None:
            best_match = (left_site, right_site)

    if best_match is not None:
        return best_match
    if not hyphen_positions:
        return ("", "")

    split_at = hyphen_positions[0]
    return (pair_str[:split_at].strip(), pair_str[split_at + 1 :].strip())


def _extract_site_from_trunk_half(half: str, known_sites: set[str]) -> str | None:
    """Extract a site code from one side of a trunk-style name."""
    half = half.strip()
    if half in known_sites:
        return half

    for site in sorted(known_sites, key=len, reverse=True):
        if not half.startswith(site):
            continue
        remainder = half[len(site) :].strip()
        if not remainder or re.match(r"^[A-Z]+(\s+\d+)?$", remainder):
            return site

    match = re.match(r"^(.+?)\s+(BR|SDN|NNI|X|XS|U|UX|CC|G|MG)\s+\d+$", half)
    if match:
        return match.group(1)
    return None
