"""Graph walk helpers for route topology."""

from __future__ import annotations

import re
from collections import defaultdict, deque

from .models import InCARow
from .sorting_topology_models import AdjacencyGraph, RouteEdge, SectionRouteTopology


def build_adjacency_graph(edges: list[RouteEdge]) -> AdjacencyGraph:
    """Build a bidirectional adjacency graph from route edges."""
    graph: AdjacencyGraph = defaultdict(list)
    for site1, site2, trunk_name in edges:
        graph[site1].append((site2, trunk_name))
        graph[site2].append((site1, trunk_name))
    return dict(graph)


def _collect_all_site_codes(rows: list[InCARow], a_site: str, b_site: str) -> set[str]:
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
        return left_num > right_num
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
