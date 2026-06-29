"""Route-order metadata validation and ranking helpers."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .sorting_context import *  # noqa: F403


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
