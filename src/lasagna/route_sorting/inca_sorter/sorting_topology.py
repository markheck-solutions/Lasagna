"""Route-topology parsing and site-order construction for sorting."""

from __future__ import annotations

import re
from collections import defaultdict

from .models import InCARow
from .sorting_topology_edges import (
    _parse_edge_site_pair,  # noqa: F401
    _split_trunk_site_pair,
    parse_snowflake_edges,
)
from .sorting_topology_graph import (
    _build_display_site_order,
    _filter_site_order_for_data,  # noqa: F401
    build_section_route_topology,  # noqa: F401
    walk_graph,  # noqa: F401
)
from .sorting_topology_models import (
    AdjacencyGraph,
    RouteEdge,
    RouteEndpoints,
    RouteTopology,
)

_BEARER_PATTERN = re.compile(r".+ (?:BR|SDN|NNI|X) \d+-\S+ (?:BR|SDN|NNI|X) \d+")


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
            lookup[_normalize_trunk_name_key(trunk_name)] = (a_site, b_site)
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
