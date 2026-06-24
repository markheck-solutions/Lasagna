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
from .parsers import (
    _cabling_point_int,
    _ne_group_key,
    _parse_cabling_point,
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


def _collect_decom_cleanup_keys(decom_rows: list[InCARow] | None) -> set[tuple]:
    """Return cleanup-eligible DECOM tuple keys from the BEFORE section."""
    decom_keys: set[tuple] = set()
    if not decom_rows:
        return decom_keys

    for row in decom_rows:
        if (
            row.classification == "DECOMMISSION"
            and row.site_type == "XS"
            and not row.is_external_demarcation
        ):
            decom_keys.add(row.tuple_key())
    return decom_keys


def _is_cleanup_candidate(
    row: InCARow,
    decom_keys: set[tuple],
    hotcut_sites: set[str],
    unchanged_keys: set[tuple],
) -> bool:
    """Return True when the row should emit a cleanup endpoint."""
    if row.site_type != "XS" or row.is_external_demarcation:
        return False
    key = row.tuple_key()
    return key in decom_keys or (row.site_code in hotcut_sites and key in unchanged_keys)


def _group_cleanup_rows_by_site(
    before_rows: list[InCARow],
) -> tuple[dict[str, list[InCARow]], list[str]]:
    """Collect BEFORE rows by site while preserving first-seen site order."""
    site_rows: dict[str, list[InCARow]] = defaultdict(list)
    site_order_seen: list[str] = []
    seen_sites: set[str] = set()
    for row in before_rows:
        if not row.site_code:
            continue
        site_rows[row.site_code].append(row)
        if row.site_code not in seen_sites:
            site_order_seen.append(row.site_code)
            seen_sites.add(row.site_code)
    return site_rows, site_order_seen


def _split_cleanup_route_groups(rows: list[InCARow]) -> list[list[InCARow]]:
    """Split a site's BEFORE path into contiguous route or role groups."""
    groups: list[list[InCARow]] = []
    current_group: list[InCARow] = []
    for row in rows:
        if not current_group:
            current_group.append(row)
            continue

        prev = current_group[-1]
        same_group = (
            prev.route_path == row.route_path and prev.is_device_row == row.is_device_row
        ) or (
            prev.is_demarcation
            and row.is_demarcation
            and prev.cabling_location == row.cabling_location
        )
        if same_group:
            current_group.append(row)
            continue

        groups.append(current_group)
        current_group = [row]

    if current_group:
        groups.append(current_group)
    return groups


def _bundle_cleanup_group_rows(
    group: list[InCARow],
    decom_keys: set[tuple],
    hotcut_sites: set[str],
    unchanged_keys: set[tuple],
) -> dict[tuple[str, str, bool], list[InCARow]]:
    """Bundle cleanup rows by visible endpoint identity within one group."""
    bundles: dict[tuple[str, str, bool], list[InCARow]] = defaultdict(list)
    for row in group:
        if not _is_cleanup_candidate(row, decom_keys, hotcut_sites, unchanged_keys):
            continue
        route_key = "DEMARCATION" if row.is_demarcation else row.route_path
        bundle_key = (row.cabling_location, route_key, row.is_device_row)
        bundles[bundle_key].append(row)

    for bundle_rows in bundles.values():
        bundle_rows.sort(key=lambda bundled_row: _cabling_point_int(bundled_row.cabling_points))
    return dict(bundles)


def _build_cleanup_group_bundles(
    groups: list[list[InCARow]],
    decom_keys: set[tuple],
    hotcut_sites: set[str],
    unchanged_keys: set[tuple],
) -> list[dict[tuple[str, str, bool], list[InCARow]]]:
    """Build cleanup bundles for every contiguous site group."""
    return [
        _bundle_cleanup_group_rows(group, decom_keys, hotcut_sites, unchanged_keys)
        for group in groups
    ]


def _cleanup_bundle_match_score(left_rows: list[InCARow], right_rows: list[InCARow]) -> int:
    """Score cleanup bundle proximity from cabinet evidence."""
    left_cab = left_rows[0].cabinet_key
    right_cab = right_rows[0].cabinet_key
    shared_prefix = 0
    for left_char, right_char in zip(left_cab, right_cab):
        if left_char != right_char:
            break
        shared_prefix += 1

    left_seg = left_cab.split("/", 1)[0] if left_cab else ""
    right_seg = right_cab.split("/", 1)[0] if right_cab else ""
    if left_cab == right_cab:
        return shared_prefix + 1000
    if (
        left_seg
        and right_seg
        and (left_seg.startswith(right_seg) or right_seg.startswith(left_seg))
    ):
        return shared_prefix + 200
    return shared_prefix


def _select_best_cleanup_bundle_pair(
    left: dict[tuple[str, str, bool], list[InCARow]],
    right: dict[tuple[str, str, bool], list[InCARow]],
    used_left_keys: set[tuple[str, str, bool]],
    used_right_keys: set[tuple[str, str, bool]],
) -> tuple[tuple[str, str, bool], tuple[str, str, bool]] | None:
    """Return the best cleanup bundle pair across adjacent route groups."""
    best_pair: tuple[tuple[str, str, bool], tuple[str, str, bool]] | None = None
    best_rank: (
        tuple[
            int,
            int,
            str,
            str,
            tuple[str, str, bool],
            tuple[str, str, bool],
        ]
        | None
    ) = None

    for left_key, left_rows in left.items():
        for right_key, right_rows in right.items():
            reused = (1 if left_key in used_left_keys else 0) + (
                1 if right_key in used_right_keys else 0
            )
            rank = (
                reused,
                -_cleanup_bundle_match_score(left_rows, right_rows),
                left_rows[0].cabling_location,
                right_rows[0].cabling_location,
                left_key,
                right_key,
            )
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_pair = (left_key, right_key)

    return best_pair


def _extract_cleanup_points(rows: list[InCARow]) -> list[str]:
    """Return distinct parsed cabling points for a cleanup endpoint bundle."""
    points: list[str] = []
    for row in rows:
        point = _parse_cabling_point(row.cabling_points)
        if point and point not in points:
            points.append(point)
    return points


def _build_cleanup_endpoint_lines(
    site_code: str,
    left_rows: list[InCARow],
    right_rows: list[InCARow],
) -> list[TicketLine]:
    """Return the two endpoint lines for one cleanup jumper removal."""
    endpoint_lines: list[TicketLine] = []
    for rows in (left_rows, right_rows):
        text, variant, classification = _format_cleanup_endpoint(
            rows,
            _extract_cleanup_points(rows),
        )
        endpoint_lines.append(
            TicketLine(
                text=text,
                variant=variant,
                site_code=site_code,
                classification=classification,
                hotcut_label="DECOM_ONLY",
                source_key=rows[0].tuple_key(),
            )
        )
    return endpoint_lines


def _build_site_cleanup_lines(
    site_code: str,
    rows: list[InCARow],
    decom_keys: set[tuple],
    hotcut_sites: set[str],
    unchanged_keys: set[tuple],
) -> list[TicketLine]:
    """Return cleanup endpoint lines for one site's BEFORE route."""
    groups = _split_cleanup_route_groups(rows)
    if len(groups) < 2:
        return []

    group_bundles = _build_cleanup_group_bundles(
        groups,
        decom_keys,
        hotcut_sites,
        unchanged_keys,
    )
    used_bundle_keys: list[set[tuple[str, str, bool]]] = [set() for _ in groups]
    cleanup_lines: list[TicketLine] = []

    for group_index in range(len(groups) - 1):
        left = group_bundles[group_index]
        right = group_bundles[group_index + 1]
        if not left or not right:
            continue

        best_pair = _select_best_cleanup_bundle_pair(
            left,
            right,
            used_bundle_keys[group_index],
            used_bundle_keys[group_index + 1],
        )
        if not best_pair:
            continue

        left_key, right_key = best_pair
        used_bundle_keys[group_index].add(left_key)
        used_bundle_keys[group_index + 1].add(right_key)
        cleanup_lines.extend(
            _build_cleanup_endpoint_lines(site_code, left[left_key], right[right_key])
        )

    return cleanup_lines


def _build_cleanup_patch_pair_lines(
    before_rows: list[InCARow],
    decom_rows: list[InCARow] | None,
    hotcut_sites: set[str],
    unchanged_keys: set[tuple],
) -> list[TicketLine]:
    """Build Stage 2 cleanup lines as patch-cable endpoint pairs.

    Cleanup work removes jumpers between adjacent route groups in the
    BEFORE path. Rows inside the same trunk are prewired and are never
    emitted as cleanup tasks.
    """
    decom_keys = _collect_decom_cleanup_keys(decom_rows)
    site_rows, site_order_seen = _group_cleanup_rows_by_site(before_rows)
    cleanup_lines: list[TicketLine] = []
    for site_code in site_order_seen:
        cleanup_lines.extend(
            _build_site_cleanup_lines(
                site_code,
                site_rows[site_code],
                decom_keys,
                hotcut_sites,
                unchanged_keys,
            )
        )
    return cleanup_lines


def _format_cleanup_endpoint(rows: list[InCARow], points: list[str]) -> tuple[str, str, str]:
    """Format a Stage-2 cleanup endpoint preserving the source row's lifecycle.

    Returns ``(text, variant, classification)`` for the representative source
    row of a bundle. Variant-C (NE-Location/device) endpoints route through
    the same formatter as normal ticket lines so the cleanup line carries
    NE name + port + verbatim cabling location instead of bare
    ``NE-location: ...``. Variant A/B endpoints keep the compact
    ``cabling_location [+ joined points]`` form used historically.
    Classification is taken from the row, not hardcoded, so an UNCHANGED LIVE
    device that survives the hot-cut is not mislabeled as DECOMMISSION.
    """
    representative = rows[0]
    variant = _get_ticket_variant(representative)
    classification = representative.classification or "DECOMMISSION"

    if variant == "C":
        text = _format_variant_c(representative)
    else:
        text = representative.cabling_location
        if points:
            text = f"{text} {'+'.join(points)}"

    return text, variant, classification


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


def _build_site_to_cluster_map(clusters: list[list[str]]) -> dict[str, int]:
    """Return the cluster index for each site in the metro grouping."""
    site_to_cluster: dict[str, int] = {}
    for cluster_index, cluster in enumerate(clusters):
        for site in cluster:
            site_to_cluster[site] = cluster_index
    return site_to_cluster


def _group_rows_by_cluster(
    ticket_rows: list[InCARow],
    site_to_cluster: dict[str, int],
) -> dict[int, list[InCARow]]:
    """Group ticket rows by their resolved metro cluster index."""
    cluster_rows: dict[int, list[InCARow]] = defaultdict(list)
    for row in ticket_rows:
        cluster_rows[site_to_cluster.get(row.site_code, 0)].append(row)
    return cluster_rows


def _group_same_site_trunk_names(
    trunk_edges: list[tuple[str, str, str]],
) -> dict[str, set[str]]:
    """Return self-loop trunk names keyed by site code."""
    same_site_trunk_names_by_site: dict[str, set[str]] = defaultdict(set)
    for left_site, right_site, trunk_name in trunk_edges:
        if left_site == right_site:
            same_site_trunk_names_by_site[left_site].add(trunk_name)
    return same_site_trunk_names_by_site


def _create_cluster_ticket(
    cluster: list[str],
    rows_in_cluster: list[InCARow],
    stage: int,
) -> Ticket:
    """Build the ticket shell for one metro cluster."""
    sites_with_rows = {row.site_code for row in rows_in_cluster}
    display_sites = [site for site in cluster if site in sites_with_rows]
    cluster_name = " + ".join(display_sites) if display_sites else " + ".join(cluster)
    return Ticket(cluster_name=cluster_name, sites=display_sites, stage=stage)


def _append_cluster_ticket_lines(
    ticket: Ticket,
    rows_in_cluster: list[InCARow],
    same_site_trunk_names_by_site: dict[str, set[str]],
) -> None:
    """Append formatted ticket lines in cluster route order."""
    rows_by_site: dict[str, list[InCARow]] = defaultdict(list)
    for row in rows_in_cluster:
        rows_by_site[row.site_code].append(row)

    ordered_sites = list(dict.fromkeys(row.site_code for row in rows_in_cluster))
    for site in ordered_sites:
        site_rows = rows_by_site[site]
        same_site_trunk_names = same_site_trunk_names_by_site.get(site)
        if same_site_trunk_names:
            _pair_same_site_trunk_rows(site_rows, ticket, same_site_trunk_names)
        else:
            _pair_standard_rows(site_rows, ticket)


def _pair_standard_rows(
    rows_in_cluster: list[InCARow],
    ticket: Ticket,
) -> None:
    """Standard Tx/Rx pairing for ticket rows."""
    i = 0
    while i < len(rows_in_cluster):
        tx_row = rows_in_cluster[i]
        rx_row = rows_in_cluster[i + 1] if i + 1 < len(rows_in_cluster) else None

        # Tx/Rx pair: must share site_code AND cabling_location
        if (
            rx_row
            and tx_row.site_code == rx_row.site_code
            and tx_row.cabling_location == rx_row.cabling_location
        ):
            line = _format_ticket_line(tx_row, rx_row)
            ticket.lines.append(line)
            i += 2
        else:
            # Single row (odd count, site boundary, or different cabling location)
            line = _format_ticket_line(tx_row, None)
            ticket.lines.append(line)
            i += 1


def _pair_same_site_trunk_rows(
    rows_in_cluster: list[InCARow],
    ticket: Ticket,
    same_site_trunk_names: set[str],
) -> None:
    """PP-211: Pair device rows with nearby trunk ODF rows for same-site trunks.

    When a trunk connects the same site to itself, both trunk endpoints are
    in the same ticket. We must pair each device with its nearest trunk ODF
    (by cabinet proximity) rather than pairing consecutive rows which would
    incorrectly pair the two prewired trunk ends.

    Strategy:
    1. Separate rows into device_rows and trunk_odf_rows (and other_odf_rows)
    2. Group trunk ODF rows by cabinet prefix
    3. Group device rows by cabinet prefix
    4. Match device groups to trunk groups by proximity (startswith matching)
    5. Output: [device_group, matched_trunk_group] per pair
    """
    device_rows, trunk_odf_rows, other_rows = _partition_same_site_ticket_rows(
        rows_in_cluster,
        same_site_trunk_names,
    )

    if not _should_use_same_site_proximity_pairing(device_rows, trunk_odf_rows):
        _pair_standard_rows(rows_in_cluster, ticket)
        return

    trunk_by_cabinet = _group_rows_by_cabinet_key(trunk_odf_rows)
    device_by_cabinet = _group_rows_by_cabinet_key(device_rows)
    matched_pairs, used_device_cabinets, used_trunk_cabinets = _match_same_site_cabinet_groups(
        device_by_cabinet,
        trunk_by_cabinet,
    )

    row_position = {id(row): idx for idx, row in enumerate(rows_in_cluster)}
    _emit_same_site_group_pairs(matched_pairs, row_position, rows_in_cluster, ticket)
    _emit_unmatched_same_site_groups(
        device_by_cabinet,
        trunk_by_cabinet,
        used_device_cabinets,
        used_trunk_cabinets,
        ticket,
    )

    if other_rows:
        _pair_standard_rows(other_rows, ticket)


def _partition_same_site_ticket_rows(
    rows_in_cluster: list[InCARow],
    same_site_trunk_names: set[str],
) -> tuple[list[InCARow], list[InCARow], list[InCARow]]:
    """Split same-site rows into device, self-loop trunk, and other groups."""
    device_rows: list[InCARow] = []
    trunk_odf_rows: list[InCARow] = []
    other_rows: list[InCARow] = []

    for row in rows_in_cluster:
        if row.is_demarcation:
            other_rows.append(row)
        elif row.is_device_row:
            device_rows.append(row)
        elif row.route_path in same_site_trunk_names:
            trunk_odf_rows.append(row)
        else:
            other_rows.append(row)
    return device_rows, trunk_odf_rows, other_rows


def _should_use_same_site_proximity_pairing(
    device_rows: list[InCARow],
    trunk_odf_rows: list[InCARow],
) -> bool:
    """Return True when a site needs cabinet-proximity trunk pairing."""
    if not trunk_odf_rows or not device_rows:
        return False

    device_group_keys = {_ne_group_key(row.ne_info) for row in device_rows}
    return len(device_group_keys) > 1


def _group_rows_by_cabinet_key(rows: list[InCARow]) -> dict[str, list[InCARow]]:
    """Group rows by the cabinet key used for proximity matching."""
    rows_by_cabinet: dict[str, list[InCARow]] = defaultdict(list)
    for row in rows:
        rows_by_cabinet[row.cabinet_key].append(row)
    return dict(rows_by_cabinet)


def _select_best_trunk_cabinet(
    device_group: list[InCARow],
    trunk_by_cabinet: dict[str, list[InCARow]],
    used_trunk_cabinets: set[str],
) -> tuple[str | None, int]:
    """Return the best available trunk cabinet for one device group."""
    best_trunk_cabinet: str | None = None
    best_score = -1
    for trunk_cabinet in sorted(trunk_by_cabinet):
        if trunk_cabinet in used_trunk_cabinets:
            continue
        score = _cabinet_proximity_score(device_group, trunk_by_cabinet[trunk_cabinet])
        if score > best_score:
            best_trunk_cabinet = trunk_cabinet
            best_score = score
    return best_trunk_cabinet, best_score


def _match_same_site_cabinet_groups(
    device_by_cabinet: dict[str, list[InCARow]],
    trunk_by_cabinet: dict[str, list[InCARow]],
) -> tuple[list[tuple[list[InCARow], list[InCARow]]], set[str], set[str]]:
    """Match device and self-loop trunk groups by cabinet proximity."""
    used_trunk_cabinets: set[str] = set()
    used_device_cabinets: set[str] = set()
    matched_pairs: list[tuple[list[InCARow], list[InCARow]]] = []

    for device_cabinet, device_group in sorted(device_by_cabinet.items()):
        best_trunk_cabinet, best_score = _select_best_trunk_cabinet(
            device_group,
            trunk_by_cabinet,
            used_trunk_cabinets,
        )
        if best_trunk_cabinet and best_score >= 0:
            used_trunk_cabinets.add(best_trunk_cabinet)
            used_device_cabinets.add(device_cabinet)
            matched_pairs.append((device_group, trunk_by_cabinet[best_trunk_cabinet]))

    return matched_pairs, used_device_cabinets, used_trunk_cabinets


def _group_start_position(
    group: list[InCARow],
    row_position: dict[int, int],
    fallback_position: int,
) -> int:
    """Return the earliest route-order position for a row group."""
    return min(row_position.get(id(row), fallback_position) for row in group)


def _emit_same_site_group_pairs(
    matched_pairs: list[tuple[list[InCARow], list[InCARow]]],
    row_position: dict[int, int],
    rows_in_cluster: list[InCARow],
    ticket: Ticket,
) -> None:
    """Emit matched device and trunk groups in their existing route order."""
    fallback_position = len(rows_in_cluster)
    for device_group, trunk_group in sorted(
        matched_pairs,
        key=lambda pair: min(
            _group_start_position(pair[0], row_position, fallback_position),
            _group_start_position(pair[1], row_position, fallback_position),
        ),
    ):
        if _group_start_position(
            trunk_group, row_position, fallback_position
        ) < _group_start_position(
            device_group,
            row_position,
            fallback_position,
        ):
            _pair_standard_rows(trunk_group, ticket)
            _pair_standard_rows(device_group, ticket)
        else:
            _pair_standard_rows(device_group, ticket)
            _pair_standard_rows(trunk_group, ticket)


def _emit_unmatched_same_site_groups(
    device_by_cabinet: dict[str, list[InCARow]],
    trunk_by_cabinet: dict[str, list[InCARow]],
    used_device_cabinets: set[str],
    used_trunk_cabinets: set[str],
    ticket: Ticket,
) -> None:
    """Emit any device or trunk groups left unmatched after proximity pairing."""
    for device_cabinet, device_group in sorted(device_by_cabinet.items()):
        if device_cabinet not in used_device_cabinets:
            _pair_standard_rows(device_group, ticket)

    for trunk_cabinet, trunk_group in sorted(trunk_by_cabinet.items()):
        if trunk_cabinet not in used_trunk_cabinets:
            _pair_standard_rows(trunk_group, ticket)


def _extract_cabinet_prefix(cabling_location: str) -> str:
    """Extract cabinet prefix from cabling_location for proximity matching.

    PP-211: Used for same-site trunk ticket pairing to match devices
    with their nearest trunk ODF endpoints.

    Examples:
        '[5TH FL.-STE 524]C2/07/RU43/.' -> 'C2/07'
        'NE-location: [5TH FL.-STE 524]C5/07/RU01/.' -> 'C5/07'
        '[BSMT-TR-I]F2/R04/RU32/F' -> 'F2/R04'
        '[BSMT-TR-I]F2A/R13/RU39/F' -> 'F2A/R13'
    """
    loc = cabling_location.strip()
    # Strip NE-location prefix
    ne_prefix_match = re.match(r"(?i)NE-location:\s*", loc)
    if ne_prefix_match:
        loc = loc[ne_prefix_match.end() :]
    # Strip building prefix in brackets
    bracket_match = re.search(r"\](.+)", loc)
    if bracket_match:
        loc = bracket_match.group(1)
    # Take first two segments (cabinet identifier)
    parts = loc.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if parts else loc


def _cabinet_match_keys(row: InCARow) -> list[str]:
    """Return structured and visible-location cabinet keys for data matching."""
    keys: list[str] = []
    for key in (row.cabinet_key, _extract_cabinet_prefix(row.cabling_location)):
        key = key.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def _cabinet_key_score(left: str, right: str, left_idx: int, right_idx: int) -> int:
    """Score two cabinet keys; higher is better, negative means no match."""
    if left == right:
        return 100 - (left_idx * 5) - (right_idx * 5)

    left_seg = left.split("/")[0]
    right_seg = right.split("/")[0]
    if right_seg.startswith(left_seg) or left_seg.startswith(right_seg):
        prefix_len = 0
        for a, b in zip(left, right):
            if a != b:
                break
            prefix_len += 1
        return 10 + prefix_len - left_idx - right_idx

    return -1


def _cabinet_proximity_score(
    device_group: list[InCARow],
    trunk_group: list[InCARow],
) -> int:
    """Score device/trunk group proximity from structured and visible data."""
    best_score = -1
    for dev_row in device_group:
        for trunk_row in trunk_group:
            for dev_idx, dev_key in enumerate(_cabinet_match_keys(dev_row)):
                for trunk_idx, trunk_key in enumerate(_cabinet_match_keys(trunk_row)):
                    score = _cabinet_key_score(
                        dev_key,
                        trunk_key,
                        dev_idx,
                        trunk_idx,
                    )
                    best_score = max(best_score, score)
    return best_score


def _format_ticket_line(tx_row: InCARow, rx_row: InCARow | None) -> TicketLine:
    """Format a Tx/Rx pair into a ticket line string.

    Variant detection:
    - CablingLocation starts with 'NE-Location' -> Variant C
    - LocationAlias non-empty -> Variant B
    - Else -> Variant A
    """
    variant = _get_ticket_variant(tx_row)

    if variant == "C":
        text = _format_variant_c(tx_row)
    elif variant == "B":
        points = _format_points_pair(tx_row, rx_row)
        text = (
            f"{tx_row.cabling_location}, {points}, {tx_row.conn_type}, "
            f"(alias: {tx_row.location_alias})"
        )
    else:  # Variant A
        points = _format_points_pair(tx_row, rx_row)
        if tx_row.conn_type:
            text = f"{tx_row.cabling_location}, {points}, {tx_row.conn_type}"
        else:
            text = f"{tx_row.cabling_location}, {points}"

    return TicketLine(
        text=text,
        variant=variant,
        site_code=tx_row.site_code,
        classification=tx_row.classification,
        source_key=tx_row.tuple_key(),
    )


def _get_ticket_variant(row: InCARow) -> str:
    """Determine ticket line variant for a row."""
    if row.is_ne_location:
        return "C"
    elif row.location_alias and row.location_alias.strip():
        return "B"
    else:
        return "A"


def _format_points_pair(tx_row: InCARow, rx_row: InCARow | None) -> str:
    """Format cabling points for a Tx/Rx pair.

    Returns 'tx+rx' or just 'tx' if no Rx row.
    DP ODF rows may have pre-paired points like '15+16' in a single row;
    these are preserved as-is when there is no separate Rx row.
    """
    if rx_row:
        tx_pt = _parse_cabling_point(tx_row.cabling_points)
        rx_pt = _parse_cabling_point(rx_row.cabling_points)
        if tx_pt and rx_pt:
            return f"{tx_pt}+{rx_pt}"
        elif tx_pt:
            return tx_pt
        elif rx_pt:
            return rx_pt
    else:
        # Single row: check for pre-paired points (e.g., DP ODF "15+16")
        raw = tx_row.cabling_points.strip()
        m = re.match(r"(\d+)\+(\d+)", raw)
        if m:
            return f"{m.group(1)}+{m.group(2)}"
        tx_pt = _parse_cabling_point(raw)
        if tx_pt:
            return tx_pt
    return "N/A"


def _format_variant_c(row: InCARow) -> str:
    """Format Variant C ticket line (NE-Location direct patch).

    Router: {hostname} {chassis}, port {address}, {cabling_location_verbatim}
    DWDM:   {site} {site_type} {device_type} {nr}, port {address}, {cabling_location_verbatim}

    Cabling location is used verbatim from INCA (PP-051, PP-068).
    NE name is hostname+model only, extracted by splitting on ' -' delimiter (PP-068).
    """
    cabling_loc_verbatim = row.cabling_location.strip()
    # Clean empty NE-location paths (INCA returns "/ / / ::port" when no BO ODF data)
    if cabling_loc_verbatim.startswith("NE-location:"):
        loc_part = cabling_loc_verbatim.split(":", 1)[1].strip()
        if loc_part.startswith("/ / /") or loc_part.startswith(". / /"):
            cabling_loc_verbatim = "NE-location: (no BO ODF location in INCA)"

    if row.is_router:
        ne_name = _ne_group_key(row.ne_info)
        port = _extract_port_address(row)
        return f"{ne_name}, port {port}, {cabling_loc_verbatim}"
    else:
        ne_name = _ne_group_key(row.ne_info)
        port = _extract_port_address(row)
        # Avoid duplicating site_code+site_type when _ne_group_key
        # already starts with that prefix (e.g., 'LAWS XS RLS 02 R4-01')
        prefix = f"{row.site_code} {row.site_type} "
        if ne_name.startswith(prefix):
            return f"{ne_name}, port {port}, {cabling_loc_verbatim}"
        else:
            return f"{prefix}{ne_name}, port {port}, {cabling_loc_verbatim}"


def _assemble_structured_port(row: InCARow) -> str | None:
    """Assemble port address from structured fields when available.

    Phase 2A: Uses slot/subslot/connection_point_nr to build the port
    address deterministically, avoiding regex fragility.

    Returns None to trigger regex fallback for unrecognized patterns
    or when structured fields are not populated.
    """
    if not row.slot:
        return None

    slot = row.slot.rstrip(".")

    # Router class: SLOT/SUBSLOT (NCS-5508, 8201-*, 7280-*)
    # These have subslot populated and cp_nr = "."
    if row.subslot:
        return f"{slot}/{row.subslot}"

    # ASR/DTN/RLS class: SLOT + CONNECTION_POINT_NR
    if row.connection_point_nr and row.connection_point_nr.strip("."):
        cp = row.connection_point_nr.strip(".")
        # Determine separator: / for ASR-style (slot contains /), . for DTN/RLS
        # ASR BUILT IN: slot was "0/0." -> stripped to "0/0", uses /
        # DTN: slot was "05", uses .
        if "/" in slot:
            return f"{slot}/{cp}"
        else:
            return f"{slot}.{cp}"

    return None


def _normalize_port_address(port: str) -> str:
    """Normalize port address per PP-060.

    - Replace backslash with forward slash
    - Strip trailing dots (consecutive dots at end of string)
    - Preserve sub-port decimals (single dot followed by digit, e.g., .3)

    Examples:
        '0/0/0\\13..' -> '0/0/0/13'
        '0/0/0\\30.3' -> '0/0/0/30.3'
        '0/0/0\\9.0' -> '0/0/0/9.0'
        '0/0/0/15' -> '0/0/0/15' (no change)
    """
    # Replace backslash with forward slash
    port = port.replace("\\", "/")
    # Strip trailing dots (but not dots followed by digits, which are sub-ports)
    port = re.sub(r"\.+$", "", port)
    return port


def _extract_structured_port_address(row: InCARow) -> str | None:
    """Extract a port address from structured slot, subslot, and CP fields."""
    return _assemble_structured_port(row)


def _extract_double_dot_port_address(row: InCARow) -> str | None:
    """Extract router ports from the legacy double-dot NE Information format."""
    if not row.ne_info:
        return None

    match = re.search(r"-\((\d+/\d+)\.\.(?:.*?-)?(\d+):", row.ne_info)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _extract_pattern_port_address(raw_text: str | None) -> str | None:
    """Extract and normalize slash or backslash port syntax from raw text."""
    if not raw_text:
        return None

    port_pattern = r"(\d+[/\\]\d+(?:[/\\]\d+)*(?:\.\d+)?\.{0,3})"
    match = re.search(port_pattern, raw_text)
    if not match:
        return None
    return _normalize_port_address(match.group(1))


def _extract_parenthetical_port_address(row: InCARow) -> str | None:
    """Extract the full parenthetical port body from NE Information."""
    if not row.ne_info:
        return None

    match = re.search(r"-\(([^:]+):", row.ne_info)
    if not match:
        return None
    return _normalize_port_address(match.group(1))


def _extract_port_address(row: InCARow) -> str:
    """Extract and normalize port address from row data.

    Phase 2A: tries structured port assembly first (slot/subslot/cp_nr),
    falls back to regex extraction from NE Information and Comment fields.
    Applies PP-060 normalization.
    """
    for extractor in (
        _extract_structured_port_address,
        _extract_double_dot_port_address,
        lambda current_row: _extract_pattern_port_address(current_row.comment),
        lambda current_row: _extract_pattern_port_address(current_row.ne_info),
        _extract_parenthetical_port_address,
    ):
        port = extractor(row)
        if port:
            return port

    # Fallback: use Pos as a crude reference
    return str(row.pos)
