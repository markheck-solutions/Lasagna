"""ICB endpoint and structural site-order helpers."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .sorting_context import *  # noqa: F403


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


def _endpoint_site_role_sets(rows: list[InCARow]) -> tuple[set[str], set[str]]:
    """Return sites carrying endpoint devices and demarcation rows."""
    sites_with_device: set[str] = set()
    sites_with_demarc: set[str] = set()
    for row in rows:
        if row.is_device_row and not row.is_demarcation:
            sites_with_device.add(row.site_code)
        if row.is_demarcation:
            sites_with_demarc.add(row.site_code)
    return sites_with_device, sites_with_demarc


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
    sites_with_device, sites_with_demarc = _endpoint_site_role_sets(rows)

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


class SameLocationHandoffAnchor(NamedTuple):
    trunk_route_path: str
    outer_site: str
    handoff_site: str


class DeviceHandoffSegment(NamedTuple):
    insert_after: int
    row_indices: frozenset[int]
    rows: list[InCARow]
