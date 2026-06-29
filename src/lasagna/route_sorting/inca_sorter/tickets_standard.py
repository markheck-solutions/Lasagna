"""Standard ticket grouping and line formatting helpers."""

from __future__ import annotations

import re
from collections import defaultdict

from .models import InCARow, Ticket, TicketLine
from .parsers import _ne_group_key, _parse_cabling_point


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
