"""Route-topology parsing and site-order construction for sorting."""

from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from typing import NamedTuple

from .models import InCARow

logger = logging.getLogger(__name__)

RouteEdge = tuple[str, str, str]
AdjacencyGraph = dict[str, list[tuple[str, str]]]

_BEARER_PATTERN = re.compile(r".+ (?:BR|SDN|NNI|X) \d+-\S+ (?:BR|SDN|NNI|X) \d+")


class ParsedSnowflakeEdge(NamedTuple):
    level: int
    site1: str
    site2: str
    edge_name: str


class RouteEndpoints(NamedTuple):
    bearer: str | None
    a_site: str
    b_site: str
    info_lines: list[str]


class RouteTopology(NamedTuple):
    trunk_edges: list[RouteEdge]
    graph: AdjacencyGraph
    site_order: list[str]


class SectionRouteTopology(NamedTuple):
    walk_order: list[str]
    display_order: list[str]


def find_bearer_route_path(rows: list[InCARow]) -> str | None:
    """Find the bearer route path among all route paths."""
    for route_path in _collect_route_paths_in_order(rows):
        if _BEARER_PATTERN.match(route_path):
            return route_path
    return None


def _collect_route_paths_in_order(rows: list[InCARow]) -> list[str]:
    """Return non-empty route paths in first observed row order."""
    route_paths: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not row.route_path or row.route_path in seen:
            continue
        route_paths.append(row.route_path)
        seen.add(row.route_path)
    return route_paths


def resolve_route_endpoints(rows: list[InCARow]) -> RouteEndpoints:
    """Resolve sorter endpoints from bearer data or demarc fallback rows."""
    bearer = find_bearer_route_path(rows)
    if bearer:
        a_site, b_site = parse_bearer_endpoints(bearer)
        return RouteEndpoints(
            bearer=bearer,
            a_site=a_site,
            b_site=b_site,
            info_lines=[f"Bearer: {bearer}", f"A-Loc: {a_site}, B-Loc: {b_site}"],
        )

    inferred_a, inferred_b = _infer_demarc_endpoints(rows)
    if inferred_a and inferred_b:
        return RouteEndpoints(
            bearer=None,
            a_site=inferred_a,
            b_site=inferred_b,
            info_lines=[
                "No bearer route path found; endpoints inferred from "
                f"external demarcation rows: A={inferred_a}, B={inferred_b}."
            ],
        )

    return RouteEndpoints(
        bearer=None,
        a_site="",
        b_site="",
        info_lines=[
            "WARNING: No bearer route path found and external demarcation "
            "rows did not resolve to exactly two endpoint sites; endpoints "
            "left unresolved."
        ],
    )


def _infer_demarc_endpoints(
    rows: list[InCARow],
) -> tuple[str, str] | tuple[None, None]:
    """Infer A/B endpoints from external demarcation rows when no bearer exists."""
    sites, first_pos = _collect_external_demarc_sites(rows)
    if len(sites) != 2:
        return (None, None)
    return _order_demarc_endpoint_pair(sites, first_pos)


def _collect_external_demarc_sites(rows: list[InCARow]) -> tuple[list[str], dict[str, int]]:
    """Collect unique external demarc sites and their first numeric POS."""
    sites: list[str] = []
    first_pos: dict[str, int] = {}
    seen: set[str] = set()
    for row in rows:
        if not _is_endpoint_demarcation_candidate(row):
            continue
        if row.site_code not in seen:
            seen.add(row.site_code)
            sites.append(row.site_code)
        if row.site_code not in first_pos and row.pos > 0:
            first_pos[row.site_code] = row.pos
    return sites, first_pos


def _is_endpoint_demarcation_candidate(row: InCARow) -> bool:
    """Return True when a row can anchor the no-bearer endpoint fallback."""
    return bool(
        row.is_external_demarcation and row.classification != "DECOMMISSION" and row.site_code
    )


def _order_demarc_endpoint_pair(
    sites: list[str],
    first_pos: dict[str, int],
) -> tuple[str, str]:
    """Order two demarc sites by numeric POS when both positions are known."""
    left, right = sites
    left_pos = first_pos.get(left)
    right_pos = first_pos.get(right)
    if left_pos is None or right_pos is None or left_pos == right_pos:
        return (left, right)
    if right_pos < left_pos:
        return (right, left)
    return (left, right)


def parse_bearer_endpoints(bearer: str) -> tuple[str, str]:
    """Extract A-site and B-site from bearer route path."""
    marker_hits: list[tuple[int, int]] = []
    for marker in (" BR ", " SDN ", " NNI ", " X "):
        marker_hits.extend(
            (match.start(), len(marker)) for match in re.finditer(re.escape(marker), bearer)
        )

    marker_hits.sort(key=lambda hit: hit[0])
    if len(marker_hits) < 2:
        raise ValueError(f"Cannot parse bearer endpoints from: {bearer}")

    first_pos, first_len = marker_hits[0]
    a_site = bearer[:first_pos].strip()
    after_first = bearer[first_pos + first_len :]

    second_pos, _second_len = marker_hits[1]
    second_rel = second_pos - (first_pos + first_len)
    segment = after_first[:second_rel]
    hyphen_index = segment.index("-")
    b_site = segment[hyphen_index + 1 :].strip()
    return a_site, b_site


def parse_trunk_edges(
    rows: list[InCARow],
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None = None,
    transmission_endpoint_lookup: dict[str, tuple[str, str]] | None = None,
) -> list[RouteEdge]:
    """Extract site-pair edges from trunk route paths."""
    del transmission_endpoint_lookup

    route_paths = _collect_route_paths_in_order(rows)
    bearer = find_bearer_route_path(rows)
    known_sites = {row.site_code for row in rows if row.site_code}

    edges: list[RouteEdge] = []
    seen_trunks: set[str] = set()
    for route_path in route_paths:
        edge = _parse_trunk_edge(
            route_path, bearer, known_sites, seen_trunks, trunk_endpoint_lookup
        )
        if edge is not None:
            edges.append(edge)
    return edges


def _parse_trunk_edge(
    route_path: str,
    bearer: str | None,
    known_sites: set[str],
    seen_trunks: set[str],
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None,
) -> RouteEdge | None:
    """Parse one route path into a trunk edge when it is OL-addressable."""
    if route_path == bearer or route_path in seen_trunks:
        return None

    match = re.search(r"^(.+?)\s+OL(\d+)$", route_path)
    if not match:
        return None

    seen_trunks.add(route_path)
    site1, site2 = _resolve_trunk_sites(
        route_path, match.group(1).strip(), known_sites, trunk_endpoint_lookup
    )
    if not site1 or not site2:
        return None
    return (site1, site2, route_path)


def _resolve_trunk_sites(
    route_path: str,
    pair_text: str,
    known_sites: set[str],
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None,
) -> tuple[str, str]:
    """Resolve trunk endpoints from metadata first, then by name parsing."""
    if trunk_endpoint_lookup and route_path in trunk_endpoint_lookup:
        return trunk_endpoint_lookup[route_path]
    return _split_trunk_site_pair(pair_text, known_sites)


def _normalize_trunk_name_key(trunk_name: str) -> str:
    """Normalize a trunk name for metadata lookups."""
    return trunk_name.strip().upper()


def build_trunk_media_lookup(
    trunk_metadata_records: list[dict] | None,
) -> dict[str, str]:
    """Map TRUNK_METADATA.BPK_PCG to normalized MEDIA."""
    lookup: dict[str, str] = {}
    for record in trunk_metadata_records or []:
        trunk_name = str(record.get("BPK_PCG", "")).strip()
        media = str(record.get("MEDIA", "")).strip().upper()
        if trunk_name and media:
            lookup[_normalize_trunk_name_key(trunk_name)] = media
    return lookup


def populate_trunk_media(rows: list[InCARow], trunk_media_lookup: dict[str, str]) -> None:
    """Stamp row.trunk_media when TRUNK_METADATA matches the route path."""
    if not trunk_media_lookup:
        return
    for row in rows:
        if row.trunk_media or not row.route_path:
            continue
        media = trunk_media_lookup.get(_normalize_trunk_name_key(row.route_path))
        if media:
            row.trunk_media = media


def build_trunk_endpoint_lookup(
    trunk_metadata_records: list[dict] | None,
) -> dict[str, tuple[str, str]]:
    """Map BPK_PCG to endpoint site codes from TRUNK_METADATA."""
    lookup: dict[str, tuple[str, str]] = {}
    for record in trunk_metadata_records or []:
        trunk_name = str(record.get("BPK_PCG", "")).strip()
        a_site = str(record.get("A_SITE_CODE", "")).strip()
        b_site = str(record.get("B_SITE_CODE", "")).strip()
        if trunk_name and a_site and b_site:
            lookup[trunk_name] = (a_site, b_site)
    return lookup


def build_transmission_endpoint_lookup(
    transmission_metadata_records: list[dict] | None,
) -> dict[str, tuple[str, str]]:
    """Map BPK_TRANSMISSION to endpoint site codes from TRANSMISSION_METADATA."""
    lookup: dict[str, tuple[str, str]] = {}
    for record in transmission_metadata_records or []:
        transmission_name = str(record.get("BPK_TRANSMISSION", "")).strip()
        a_site = str(record.get("A_SITE_CODE", "")).strip()
        b_site = str(record.get("B_SITE_CODE", "")).strip()
        if transmission_name and a_site and b_site:
            lookup[transmission_name] = (a_site, b_site)
    return lookup


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


def build_adjacency_graph(edges: list[RouteEdge]) -> AdjacencyGraph:
    """Build a bidirectional adjacency graph from route edges."""
    graph: AdjacencyGraph = defaultdict(list)
    for site1, site2, trunk_name in edges:
        graph[site1].append((site2, trunk_name))
        graph[site2].append((site1, trunk_name))
    for neighbors in graph.values():
        neighbors.sort(key=lambda edge: (edge[0], edge[1]))
    return graph


def build_route_topology(
    rows: list[InCARow],
    a_site: str,
    b_site: str,
    snowflake_edge_records: list[dict] | None = None,
    service_id: str | None = None,
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None = None,
    transmission_endpoint_lookup: dict[str, tuple[str, str]] | None = None,
) -> RouteTopology:
    """Build merged route edges, adjacency graph, and display site order."""
    trunk_edges = parse_trunk_edges(rows, trunk_endpoint_lookup, transmission_endpoint_lookup)
    merged_edges = _merge_snowflake_edges(
        trunk_edges,
        rows,
        a_site,
        b_site,
        snowflake_edge_records,
        service_id,
        trunk_endpoint_lookup,
        transmission_endpoint_lookup,
    )
    graph = build_adjacency_graph(merged_edges)
    site_order = _build_display_site_order(rows, a_site, b_site, graph)
    return RouteTopology(merged_edges, graph, site_order)


def _merge_snowflake_edges(
    trunk_edges: list[RouteEdge],
    rows: list[InCARow],
    a_site: str,
    b_site: str,
    snowflake_edge_records: list[dict] | None,
    service_id: str | None,
    trunk_endpoint_lookup: dict[str, tuple[str, str]] | None,
    transmission_endpoint_lookup: dict[str, tuple[str, str]] | None,
) -> list[RouteEdge]:
    """Merge route-path edges with supplemental Snowflake hierarchy edges."""
    if not snowflake_edge_records:
        return list(trunk_edges)

    service_key = service_id or (rows[0].service_id if rows and rows[0].service_id else "")
    known_sites = _collect_all_site_codes(rows, a_site, b_site)
    snowflake_edges = parse_snowflake_edges(
        snowflake_edge_records,
        service_key,
        known_sites,
        trunk_endpoint_lookup,
        transmission_endpoint_lookup,
    )
    return _append_distinct_edges(trunk_edges, snowflake_edges, find_bearer_route_path(rows))


def _append_distinct_edges(
    trunk_edges: list[RouteEdge],
    snowflake_edges: list[RouteEdge],
    bearer: str | None,
) -> list[RouteEdge]:
    """Append Snowflake edges whose endpoint pair is not already present."""
    merged = list(trunk_edges)
    existing = {(min(site1, site2), max(site1, site2)) for site1, site2, _ in merged}
    for site1, site2, edge_name in snowflake_edges:
        if bearer and edge_name == bearer:
            continue
        canonical = (min(site1, site2), max(site1, site2))
        if canonical in existing:
            continue
        merged.append((site1, site2, edge_name))
        existing.add(canonical)
    return merged


def _collect_all_site_codes(
    rows: list[InCARow],
    a_site: str,
    b_site: str,
) -> set[str]:
    """Collect row sites plus any resolved endpoint sites."""
    all_site_codes = {row.site_code for row in rows if row.site_code}
    if a_site:
        all_site_codes.add(a_site)
    if b_site:
        all_site_codes.add(b_site)
    return all_site_codes


def _build_display_site_order(
    rows: list[InCARow],
    a_site: str,
    b_site: str,
    graph: AdjacencyGraph,
) -> list[str]:
    """Build the sorter display site order from graph topology and row sites."""
    all_site_codes = _collect_all_site_codes(rows, a_site, b_site)
    walked = walk_graph(a_site, b_site, graph, all_site_codes)
    return _filter_site_order_for_data(
        walked,
        {row.site_code for row in rows},
        {site for site in (a_site, b_site) if site},
    )


def walk_graph(
    a_site: str,
    b_site: str,
    graph: AdjacencyGraph,
    all_site_codes: set[str],
) -> list[str]:
    """Walk the adjacency graph from A-site to B-site."""
    path = _bfs_path(a_site, b_site, graph)
    if not path:
        path = _bridge_disconnected_graph(a_site, b_site, graph, all_site_codes)

    missing_sites = all_site_codes - set(path)
    if missing_sites:
        path = _insert_missing_sites(path, missing_sites, graph)
    return path


def _bridge_disconnected_graph(
    a_site: str,
    b_site: str,
    graph: AdjacencyGraph,
    all_site_codes: set[str],
) -> list[str]:
    """Build a stable A-to-B path when the graph is disconnected."""
    a_chain = _bfs_chain(a_site, graph)
    b_chain = list(reversed(_bfs_chain(b_site, graph)))
    remaining = sorted(all_site_codes - set(a_chain) - set(b_chain))
    combined = _dedupe_sites_in_order(a_chain + remaining + b_chain)
    return _force_endpoint_positions(combined, a_site, b_site)


def _dedupe_sites_in_order(path: list[str]) -> list[str]:
    """Remove duplicate sites while preserving first appearance order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for site in path:
        if site in seen:
            continue
        seen.add(site)
        deduped.append(site)
    return deduped


def _force_endpoint_positions(path: list[str], a_site: str, b_site: str) -> list[str]:
    """Force A-site to the start and B-site to the end of a site path."""
    result = list(path)
    if result and result[0] != a_site:
        if a_site in result:
            result.remove(a_site)
        result.insert(0, a_site)
    if result and result[-1] != b_site:
        if b_site in result:
            result.remove(b_site)
        result.append(b_site)
    return result


def _bfs_path(
    start: str,
    end: str,
    graph: AdjacencyGraph,
) -> list[str] | None:
    """Find a site path between two endpoints using BFS."""
    if start == end:
        return [start]

    visited: set[str] = {start}
    queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
    while queue:
        current, current_path = queue.popleft()
        for neighbor, _trunk_name in graph.get(current, []):
            if neighbor == end:
                return current_path + [neighbor]
            if neighbor in visited:
                continue
            visited.add(neighbor)
            queue.append((neighbor, current_path + [neighbor]))
    return None


def _bfs_chain(start: str, graph: AdjacencyGraph) -> list[str]:
    """Walk the connected component from a start site in BFS order."""
    visited: set[str] = {start}
    chain: list[str] = [start]
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor, _trunk_name in graph.get(current, []):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            chain.append(neighbor)
            queue.append(neighbor)
    return chain


def _insert_missing_sites(
    path: list[str],
    missing_sites: set[str],
    graph: AdjacencyGraph,
) -> list[str]:
    """Insert missing row-only sites into the nearest inferred position."""
    result = list(path)
    for site in sorted(missing_sites):
        _insert_missing_site(result, site, graph)
    return result


def _insert_missing_site(path: list[str], site: str, graph: AdjacencyGraph) -> None:
    """Insert one missing site using base-code similarity, then graph hints."""
    base_key = _site_base_key(site)
    for index, path_site in enumerate(path):
        if site == path_site:
            continue
        if base_key and base_key == _site_base_key(path_site):
            path.insert(index + 1, site)
            return

    for neighbor, _trunk_name in graph.get(site, []):
        if neighbor not in path:
            continue
        path.insert(path.index(neighbor) + 1, site)
        return

    path.insert(-1, site)


def _site_base_key(site: str) -> str:
    """Normalize a site code into the base token used for nearby insertion."""
    return site.split("/")[0].rstrip("0123456789")


def _site_variant_info(site: str) -> tuple[str, int | None]:
    """Extract (base, numeric_suffix) for site codes like 'ASH/3'."""
    match = re.match(r"^(.+)/(\d+)$", site)
    if not match:
        return (site, None)
    return (match.group(1), int(match.group(2)))


def _normalize_adjacent_site_variants(site_order: list[str]) -> list[str]:
    """Normalize adjacent sibling site variants for deterministic traversal."""
    result = list(site_order)
    changed = True
    while changed:
        changed = False
        for index in range(len(result) - 1):
            left = result[index]
            right = result[index + 1]
            if not _should_swap_site_variants(left, right):
                continue
            result[index], result[index + 1] = result[index + 1], result[index]
            changed = True
    return result


def _should_swap_site_variants(left: str, right: str) -> bool:
    """Return True when adjacent sibling variants should flip order."""
    left_base, left_num = _site_variant_info(left)
    right_base, right_num = _site_variant_info(right)
    if left_num is not None and right_num is not None and left_base == right_base:
        return left_num < right_num
    return left_num is not None and right_num is None and left_base == right


def _filter_site_order_for_data(
    site_order: list[str],
    sites_with_data: set[str],
    endpoint_sites: set[str],
) -> list[str]:
    """Keep only data or endpoint sites in walk order, then normalize variants."""
    filtered: list[str] = []
    seen: set[str] = set()
    for site in site_order:
        if site not in sites_with_data and site not in endpoint_sites:
            continue
        if site in seen:
            continue
        seen.add(site)
        filtered.append(site)
    return _normalize_adjacent_site_variants(filtered)


def build_section_route_topology(
    section_rows: list[InCARow],
    trunk_edges: list[RouteEdge],
    a_site: str,
    b_site: str,
) -> SectionRouteTopology:
    """Build walk and display site orders for one migration section."""
    endpoint_sites = {site for site in (a_site, b_site) if site}
    section_universe = _build_section_universe(section_rows, trunk_edges, endpoint_sites)
    section_edges = _filter_section_trunk_edges(trunk_edges, section_universe)
    section_graph = build_adjacency_graph(section_edges)
    walk_order = walk_graph(a_site, b_site, section_graph, section_universe)
    display_order = _filter_site_order_for_data(
        walk_order,
        {row.site_code for row in section_rows},
        endpoint_sites,
    )
    return SectionRouteTopology(walk_order=walk_order, display_order=display_order)


def _build_section_universe(
    section_rows: list[InCARow],
    trunk_edges: list[RouteEdge],
    endpoint_sites: set[str],
) -> set[str]:
    """Collect section sites plus sites referenced by trunk rows in that section."""
    section_paths = {row.route_path for row in section_rows if row.route_path}
    referenced = {
        site
        for site1, site2, edge_name in trunk_edges
        if edge_name in section_paths
        for site in (site1, site2)
    }
    return {row.site_code for row in section_rows} | endpoint_sites | referenced


def _filter_section_trunk_edges(
    trunk_edges: list[RouteEdge],
    section_universe: set[str],
) -> list[RouteEdge]:
    """Keep only edges whose endpoints both belong to a section universe."""
    return [
        (site1, site2, edge_name)
        for site1, site2, edge_name in trunk_edges
        if site1 in section_universe and site2 in section_universe
    ]
