"""Ticket generation, hot-cut detection, migration section splitting."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from .models import (
    InCARow,
    Ticket,
    TicketLine,
    _is_planned,
)
from .tickets_cleanup import _build_cleanup_patch_pair_lines
from .tickets_standard import (  # noqa: F401
    _append_cluster_ticket_lines,
    _build_site_to_cluster_map,
    _create_cluster_ticket,
    _extract_port_address,
    _group_rows_by_cluster,
    _group_same_site_trunk_names,
    _pair_standard_rows,
)

_OL_PATTERN = re.compile(r"\bOL\d+$")


def _normalize_trunk_name_key(trunk_name: str) -> str:
    """Normalize a trunk name for metadata lookups."""
    return trunk_name.strip().upper()


def is_colocation_trunk(
    trunk_name: str,
    trunk_media_lookup: dict[str, str] | None = None,
) -> bool:
    """Return True when a trunk represents same-building co-location.

    MEDIA from TRUNK_METADATA is authoritative when present. Route-path naming
    remains the bounded fallback for legacy paths and rows without metadata.
    """
    if trunk_media_lookup:
        media = trunk_media_lookup.get(_normalize_trunk_name_key(trunk_name))
        if media:
            return media == "OL"
    return bool(_OL_PATTERN.search(trunk_name))


def is_migration_order(rows: list[InCARow]) -> bool:
    """Detect if this is a migration order.

    Migration detected when:
    - Any row has Status t-time = Planned, OR
    - Exact duplicate rows exist in the data.
    """
    if any(_is_planned(r.status_t_time) for r in rows):
        return True

    # Check for exact duplicates
    seen: set[tuple] = set()
    for r in rows:
        key = r.tuple_key()
        if key in seen:
            return True
        seen.add(key)

    return False


def split_migration_sections(
    rows: list[InCARow],
) -> tuple[list[InCARow], list[InCARow]]:
    """Split migration order into Before and After sections.

    Before Migration: DECOMMISSION + LIVE rows (old topology)
    After Migration: NEW + LIVE rows (new topology)
    LIVE rows appear in BOTH sections.

    Args:
        rows: All rows in the service.

    Returns:
        Tuple of (before_rows, after_rows).
    """
    before: list[InCARow] = []
    after: list[InCARow] = []

    # Deduplicate LIVE rows (INCA exports them twice in migrations)
    live_seen: set[tuple] = set()

    for r in rows:
        if r.classification == "DECOMMISSION":
            before.append(r)
        elif r.classification == "NEW":
            after.append(r)
        elif r.classification == "LIVE":
            key = r.tuple_key()
            if key not in live_seen:
                live_seen.add(key)
                before.append(r)
                after.append(r)
            # Duplicate LIVE rows are silently consumed

    return before, after


def classify_patch_points(
    after_rows: list[InCARow],
) -> list[tuple[str, str]]:
    """Walk the After Migration path and classify each intra-site patch point.

    Returns list of (patch_type, annotation) where patch_type is
    PRE-PATCH, HOT-CUT, or NO-TICKET.
    """
    results: list[tuple[str, str]] = []
    data_rows = after_rows

    prev_row: InCARow | None = None
    for row in data_rows:
        if prev_row is None:
            prev_row = row
            continue

        # Check if this is an intra-site transition (same site, different group)
        if prev_row.site_code == row.site_code:
            is_group_boundary = (
                prev_row.route_path != row.route_path or prev_row.is_device_row != row.is_device_row
            )
            if is_group_boundary:
                both_new = prev_row.classification == "NEW" and row.classification == "NEW"
                one_new_one_live = (
                    prev_row.classification == "NEW" and row.classification == "LIVE"
                ) or (prev_row.classification == "LIVE" and row.classification == "NEW")
                both_live = prev_row.classification == "LIVE" and row.classification == "LIVE"

                if both_new:
                    results.append(("PRE-PATCH", f"PRE-PATCH at {row.site_code}"))
                elif one_new_one_live:
                    results.append(("HOT-CUT", f"HOT-CUT at {row.site_code}"))
                elif both_live:
                    results.append(("NO-TICKET", f"NO-TICKET at {row.site_code}"))

        prev_row = row

    return results


def classify_hotcut_rows(
    before_rows: list[InCARow],
    after_rows: list[InCARow],
    hotcut_sites: set[str],
) -> dict[tuple, str]:
    """Classify rows at hot-cut sites using PP-250 set comparison.

    Compares before_rows vs after_rows using tuple_key():
    - UNCHANGED: row in both before and after = HOT-CUT SIDE
    - NEW_ONLY:  row only in after = CONNECT
    - DECOM_ONLY: row only in before = CLEANUP

    Args:
        before_rows: Sorted Before section (DECOM + LIVE).
        after_rows: Sorted After section (NEW + LIVE).
        hotcut_sites: Sites classified as HOT-CUT.

    Returns:
        Dict mapping tuple_key -> label string.
    """
    before_at_hotcut = {r.tuple_key() for r in before_rows if r.site_code in hotcut_sites}
    after_at_hotcut = {r.tuple_key() for r in after_rows if r.site_code in hotcut_sites}

    result: dict[tuple, str] = {}
    for key in before_at_hotcut & after_at_hotcut:
        result[key] = "UNCHANGED"
    for key in after_at_hotcut - before_at_hotcut:
        result[key] = "NEW_ONLY"
    for key in before_at_hotcut - after_at_hotcut:
        result[key] = "DECOM_ONLY"
    return result


def build_migration_portion(
    after_rows: list[InCARow],
    patch_classifications: list[tuple[str, str]],
) -> list[InCARow]:
    """Build the Migration Portion from the After section.

    Includes:
    - All NEW rows (new infrastructure)
    - LIVE rows at sites that have HOT-CUT or PRE-PATCH activity
    Excludes:
    - LIVE rows at sites with only NO-TICKET activity
    - LIVE rows at sites with no patch classification at all
    """
    active_sites: set[str] = set()
    for ptype, ann in patch_classifications:
        if ptype in ("HOT-CUT", "PRE-PATCH"):
            site = ann.split(" at ")[-1]
            active_sites.add(site)

    portion: list[InCARow] = []
    for r in after_rows:
        if r.classification == "NEW":
            portion.append(r)
        elif r.classification == "LIVE" and r.site_code in active_sites:
            portion.append(r)
    return portion


def _find_site_component(parent: dict[str, str], site: str) -> str:
    """Return the current union-find root for a site."""
    while parent[site] != site:
        parent[site] = parent[parent[site]]
        site = parent[site]
    return site


def _union_site_components(parent: dict[str, str], left: str, right: str) -> None:
    """Union two sites inside the cluster component map."""
    left_root = _find_site_component(parent, left)
    right_root = _find_site_component(parent, right)
    if left_root != right_root:
        parent[left_root] = right_root


def _union_sites_with_shared_location_ids(
    site_order: list[str],
    parent: dict[str, str],
    site_location_ids: dict[str, str | None],
) -> None:
    """Group sites that share the same non-empty SITE_LOCATION_ID."""
    loc_to_sites: dict[str, list[str]] = defaultdict(list)
    for site in site_order:
        loc_id = site_location_ids.get(site)
        if loc_id:
            loc_to_sites[loc_id].append(site)

    for sites in loc_to_sites.values():
        for site in sites[1:]:
            _union_site_components(parent, sites[0], site)


def _union_sites_with_colocation_trunks(
    trunk_edges: list[tuple[str, str, str]],
    parent: dict[str, str],
    trunk_media_lookup: dict[str, str] | None,
) -> None:
    """Fallback same-building grouping based on co-location trunk metadata."""
    for left_site, right_site, trunk_name in trunk_edges:
        if not is_colocation_trunk(trunk_name, trunk_media_lookup):
            continue
        if left_site in parent and right_site in parent:
            _union_site_components(parent, left_site, right_site)


def _split_site_order_by_component(
    site_order: list[str],
    parent: dict[str, str],
) -> list[list[str]]:
    """Split ordered sites whenever the union-find component changes."""
    clusters: list[list[str]] = [[site_order[0]]]
    for previous_site, current_site in zip(site_order, site_order[1:]):
        if _find_site_component(parent, current_site) == _find_site_component(
            parent, previous_site
        ):
            clusters[-1].append(current_site)
        else:
            clusters.append([current_site])
    return clusters


def identify_metro_clusters(
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    site_location_ids: dict[str, str | None] | None = None,
    trunk_media_lookup: dict[str, str] | None = None,
) -> list[list[str]]:
    """Split the site order into metro clusters.

    Cluster boundaries occur at inter-building transitions.
    Same-building sites do NOT split clusters.

    When site_location_ids is provided and non-empty, same-building is
    determined by matching SITE_LOCATION_ID values (authoritative source
    from Snowflake site metadata). This replaces the OL-edge heuristic
    which can false-merge distant sites connected by L3+ OL edges.

    Falls back to the OL-edge heuristic when site_location_ids is empty
    or None (backward compat for tests without site metadata).

    Args:
        site_order: Geographic A->B site order.
        trunk_edges: Trunk edges.
        site_location_ids: Optional mapping of site_code -> SITE_LOCATION_ID.

    Returns:
        List of clusters, each a list of site codes.
    """
    if not site_order:
        return []

    parent: dict[str, str] = {s: s for s in site_order}

    if site_location_ids:
        _union_sites_with_shared_location_ids(site_order, parent, site_location_ids)
    else:
        # Fallback: metadata-backed OL edges indicate same-building co-location.
        # Route-path naming remains the bounded fallback when metadata is absent.
        _union_sites_with_colocation_trunks(trunk_edges, parent, trunk_media_lookup)

    return _split_site_order_by_component(site_order, parent)


def generate_tickets(
    sorted_rows: list[InCARow],
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    is_migration: bool = False,
    patch_classifications: Sequence[tuple[str, str]] | None = None,
    decom_rows: list[InCARow] | None = None,
    before_rows: list[InCARow] | None = None,
    site_location_ids: dict[str, str | None] | None = None,
    trunk_media_lookup: dict[str, str] | None = None,
) -> list[Ticket]:
    """Generate field tech tickets from sorted route path.

    Steps:
    1. Filter rows by order type and site type (XS only, PP-049-revised)
    2. Split by metro clusters (using SITE_LOCATION_ID or trunk-based co-location)
    3. Pair consecutive rows as Tx+Rx (matching cabling location, PP-070)
    4. Format each pair as one ticket line (A-to-B order, PP-055)

    For migration orders (WI-2), generates two stages:
    - Stage 1 (Preparation): PRE-PATCH sites, can be done before maintenance window
    - Stage 2 (Hot-cut + Cleanup): HOT-CUT sites, requires maintenance window.
      Includes DECOM rows from decom_rows for removal instructions.

    Args:
        sorted_rows: Sorted route path rows.
        site_order: Geographic site order.
        trunk_edges: Trunk edges.
        is_migration: Whether this is a migration order.
        patch_classifications: Patch point classifications for migration orders.
        decom_rows: DECOM rows from Before section for Stage 2 cleanup tickets.
        site_location_ids: Optional mapping of site_code -> SITE_LOCATION_ID.

    Returns:
        List of Ticket objects.
    """
    if is_migration and patch_classifications:
        return _generate_migration_tickets(
            sorted_rows,
            site_order,
            trunk_edges,
            patch_classifications,
            decom_rows,
            before_rows,
            site_location_ids=site_location_ids,
            trunk_media_lookup=trunk_media_lookup,
        )

    ticket_rows = _select_standard_ticket_rows(sorted_rows)

    if not ticket_rows:
        return []

    return _build_tickets_from_rows(
        ticket_rows,
        site_order,
        trunk_edges,
        stage=0,
        site_location_ids=site_location_ids,
        trunk_media_lookup=trunk_media_lookup,
    )


def _select_standard_ticket_rows(sorted_rows: list[InCARow]) -> list[InCARow]:
    """Return the standard add rows that should generate tickets."""
    all_new = all(row.classification == "NEW" for row in sorted_rows)
    rows_to_ticket = (
        list(sorted_rows)
        if all_new
        else [row for row in sorted_rows if row.classification != "NEW"]
    )
    return [row for row in rows_to_ticket if row.site_type == "XS"]


def _collect_migration_patch_sites(
    patch_classifications: Sequence[tuple[str, str]],
) -> tuple[set[str], set[str]]:
    """Return HOT-CUT and PRE-PATCH site sets from the annotations."""
    hotcut_sites: set[str] = set()
    prepatch_sites: set[str] = set()
    for patch_type, annotation in patch_classifications:
        site = annotation.split(" at ")[-1]
        if patch_type == "HOT-CUT":
            hotcut_sites.add(site)
        elif patch_type == "PRE-PATCH":
            prepatch_sites.add(site)
    return hotcut_sites, prepatch_sites


def _collect_hotcut_row_labels(
    before_rows: list[InCARow] | None,
    sorted_after: list[InCARow],
    hotcut_sites: set[str],
) -> dict[tuple, str]:
    """Classify HOT-CUT site rows as UNCHANGED or NEW_ONLY when possible."""
    if not before_rows:
        return {}
    return classify_hotcut_rows(
        before_rows,
        sorted_after,
        hotcut_sites,
    )


def _select_migration_rows(
    rows: list[InCARow],
    included_sites: set[str],
) -> list[InCARow]:
    """Return NEW and LIVE XS rows for the selected migration sites."""
    return [
        row
        for row in rows
        if row.site_code in included_sites
        and row.classification in ("NEW", "LIVE")
        and row.site_type == "XS"
    ]


def _collect_unchanged_hotcut_keys(
    before_rows: list[InCARow] | None,
    sorted_after: list[InCARow],
    hotcut_sites: set[str],
) -> set[tuple]:
    """Return BEFORE/AFTER tuple keys that survive unchanged at HOT-CUT sites."""
    if not before_rows:
        return set()

    before_at_hotcut = {r.tuple_key() for r in before_rows if r.site_code in hotcut_sites}
    after_at_hotcut = {r.tuple_key() for r in sorted_after if r.site_code in hotcut_sites}
    return before_at_hotcut & after_at_hotcut


def _tag_ticket_lines_with_hotcut_labels(
    lines: list[TicketLine],
    hotcut_row_labels: dict[tuple, str],
) -> None:
    """Apply precomputed HOT-CUT labels to ticket lines in place."""
    for line in lines:
        if line.source_key in hotcut_row_labels:
            line.hotcut_label = hotcut_row_labels[line.source_key]


def _build_stage1_migration_tickets(
    stage1_rows: list[InCARow],
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    hotcut_row_labels: dict[tuple, str],
    site_location_ids: dict[str, str | None] | None,
    trunk_media_lookup: dict[str, str] | None,
) -> list[Ticket]:
    """Build and label the Stage 1 migration tickets."""
    tickets = _build_tickets_from_rows(
        stage1_rows,
        site_order,
        trunk_edges,
        stage=1,
        site_location_ids=site_location_ids,
        trunk_media_lookup=trunk_media_lookup,
    )
    for ticket in tickets:
        _tag_ticket_lines_with_hotcut_labels(ticket.lines, hotcut_row_labels)
    return tickets


def _ordered_stage2_sites(
    stage2_hotcut_rows: list[InCARow],
    stage2_cleanup_lines: list[TicketLine],
    site_order: list[str],
) -> list[str]:
    """Return Stage 2 sites deduplicated and ordered by route position."""
    ordered_sites: list[str] = []
    seen_sites: set[str] = set()
    for site_code in [
        *(row.site_code for row in stage2_hotcut_rows),
        *(line.site_code for line in stage2_cleanup_lines),
    ]:
        if site_code not in seen_sites:
            ordered_sites.append(site_code)
            seen_sites.add(site_code)

    site_positions = {site: index for index, site in enumerate(site_order)}
    ordered_sites.sort(key=lambda site: site_positions.get(site, 999))
    return ordered_sites


def _build_stage2_migration_ticket(
    stage2_hotcut_rows: list[InCARow],
    stage2_cleanup_lines: list[TicketLine],
    site_order: list[str],
    hotcut_row_labels: dict[tuple, str],
) -> Ticket:
    """Build the single multi-site Stage 2 HOT-CUT + CLEANUP ticket."""
    stage2_sites = _ordered_stage2_sites(
        stage2_hotcut_rows,
        stage2_cleanup_lines,
        site_order,
    )
    ticket = Ticket(
        cluster_name=" + ".join(stage2_sites),
        sites=stage2_sites,
        stage=2,
        is_hotcut=True,
    )
    _pair_standard_rows(stage2_hotcut_rows, ticket)
    _tag_ticket_lines_with_hotcut_labels(ticket.lines, hotcut_row_labels)
    ticket.lines.extend(stage2_cleanup_lines)
    return ticket


def _generate_migration_tickets(
    sorted_after: list[InCARow],
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    patch_classifications: Sequence[tuple[str, str]],
    decom_rows: list[InCARow] | None,
    before_rows: list[InCARow] | None = None,
    site_location_ids: dict[str, str | None] | None = None,
    trunk_media_lookup: dict[str, str] | None = None,
) -> list[Ticket]:
    """Generate two-stage migration tickets (WI-2).

    Stage 1 (HOT-CUT PREPARATION): Sites with both unchanged and new rows.
        Includes hotcut sites AND prepatch sites.
    Stage 2 (HOT-CUT + CLEANUP): Single multi-site ticket combining:
        - HOT-CUT section: unchanged + new rows at hotcut sites
        - CLEANUP section: DECOM rows from ALL sites
    """
    hotcut_sites, prepatch_sites = _collect_migration_patch_sites(patch_classifications)
    hotcut_row_labels = _collect_hotcut_row_labels(before_rows, sorted_after, hotcut_sites)
    stage1_rows = _select_migration_rows(sorted_after, hotcut_sites | prepatch_sites)
    stage2_hotcut_rows = _select_migration_rows(sorted_after, hotcut_sites)
    unchanged_keys = _collect_unchanged_hotcut_keys(
        before_rows,
        sorted_after,
        hotcut_sites,
    )
    stage2_cleanup_lines = _build_cleanup_patch_pair_lines(
        before_rows or [],
        decom_rows,
        hotcut_sites,
        unchanged_keys,
    )

    tickets: list[Ticket] = []

    if stage1_rows:
        tickets.extend(
            _build_stage1_migration_tickets(
                stage1_rows,
                site_order,
                trunk_edges,
                hotcut_row_labels,
                site_location_ids,
                trunk_media_lookup,
            )
        )

    if stage2_hotcut_rows or stage2_cleanup_lines:
        tickets.append(
            _build_stage2_migration_ticket(
                stage2_hotcut_rows,
                stage2_cleanup_lines,
                site_order,
                hotcut_row_labels,
            )
        )

    return tickets


def _build_tickets_from_rows(
    ticket_rows: list[InCARow],
    site_order: list[str],
    trunk_edges: list[tuple[str, str, str]],
    stage: int = 0,
    site_location_ids: dict[str, str | None] | None = None,
    trunk_media_lookup: dict[str, str] | None = None,
) -> list[Ticket]:
    """Build tickets from pre-filtered rows. Shared by add and migration paths."""
    clusters = identify_metro_clusters(
        site_order,
        trunk_edges,
        site_location_ids,
        trunk_media_lookup=trunk_media_lookup,
    )
    site_to_cluster = _build_site_to_cluster_map(clusters)
    cluster_rows = _group_rows_by_cluster(ticket_rows, site_to_cluster)
    same_site_trunk_names_by_site = _group_same_site_trunk_names(trunk_edges)

    tickets: list[Ticket] = []
    for ci, cluster in enumerate(clusters):
        rows_in_cluster = cluster_rows.get(ci, [])
        if not rows_in_cluster:
            continue

        ticket = _create_cluster_ticket(cluster, rows_in_cluster, stage)
        _append_cluster_ticket_lines(
            ticket,
            rows_in_cluster,
            same_site_trunk_names_by_site,
        )
        tickets.append(ticket)

    return tickets
