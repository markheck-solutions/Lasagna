"""Core route sorting orchestration."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .sorting_context import *  # noqa: F403

from .sorting_handoffs import (
    _apply_same_location_device_handoffs,
    _interleave_inter_site_trunk_pairs,
    _populate_display_points,
)
from .sorting_metadata import (
    CanonicalRouteOrder,
    MetadataCompleteness,
    MetadataRouteSortError,
    PreparedRouteSort,
    RouteOrderFacts,
    SortedRouteArtifacts,
    _build_canonical_route_order,
    _build_metadata_trunk_route_rank,
    _build_route_order_facts,
    _metadata_completeness,
    _metadata_route_paths_requiring_endpoints,
    _partial_metadata_error_message,
    _raise_for_partial_metadata,
)
from .sorting_site_order import _reorder_icb_endpoint_siblings


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
    service_id: str | None,
) -> list[InCARow]:
    """Return the one row order consumed by display/export and ticket generation."""
    if prepared.metadata_canonical_order:
        return list(sorted_rows)
    trunk_grouped_rows = _interleave_inter_site_trunk_pairs(sorted_rows, prepared.trunk_edges)
    return _apply_same_location_device_handoffs(
        trunk_grouped_rows,
        prepared.trunk_edges,
        prepared.site_location_ids,
        prepared.tl_device_map,
        service_id,
    )


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

    canonical_before = _canonical_sorted_rows(sorted_before, prepared, service_id)
    canonical_after = _canonical_sorted_rows(sorted_after, prepared, service_id)
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
    canonical_rows = _canonical_sorted_rows(sorted_rows, prepared, service_id)
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
