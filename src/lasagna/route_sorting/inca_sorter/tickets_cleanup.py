"""Migration cleanup ticket helpers."""

from __future__ import annotations

from collections import defaultdict

from .models import InCARow, TicketLine
from .parsers import _cabling_point_int, _parse_cabling_point
from .tickets_standard import _format_variant_c, _get_ticket_variant


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
