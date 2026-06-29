"""Plain-text route and ticket formatting for INCA route sorter output."""

from __future__ import annotations

from collections.abc import Callable

from .models import InCARow, Ticket, TicketLine


def _format_route_rows(items: list[InCARow]) -> list[str]:
    """Format InCARow items into display lines."""
    lines: list[str] = []
    for item in items:
        marker = ""
        if item.classification == "NEW":
            marker = " [NEW]"
        elif item.classification == "DECOMMISSION":
            marker = " [DECOM]"

        ne = item.ne_info or "-"
        points_display = item.display_points if item.display_points else item.cabling_points
        lines.append(
            f"  {item.site_code:<12} {item.site_type:<4} "
            f"{ne:<25} "
            f"{item.cabling_location:<40} {points_display:<15} "
            f"Pos:{item.pos}{marker}"
        )
    return lines


def _append_ticket_lines_with_site_breaks(
    lines: list[str],
    ticket_lines: list[TicketLine],
    formatter: Callable[[TicketLine], str] | None = None,
) -> None:
    """Append formatted ticket lines with blank-line separators between cable groups.

    Each fiber cable (jumper) connects two ODF positions, producing a pair of
    consecutive ticket lines.  Blank lines separate cable pairs and site
    transitions for field crew readability.
    """
    prev_site: str | None = None
    for i, ticket_line in enumerate(ticket_lines):
        # Blank line on cable boundary (every 2 lines) or site change
        if i > 0 and (i % 2 == 0 or ticket_line.site_code != prev_site):
            lines.append("")
        if formatter is None:
            lines.append(ticket_line.text)
        else:
            lines.append(formatter(ticket_line))
        prev_site = ticket_line.site_code


def _format_stage1_ticket_line(ticket_line: TicketLine) -> str:
    """Format one Stage 1 ticket line with its inline action label."""
    label = ticket_line.hotcut_label if ticket_line.hotcut_label else ticket_line.classification
    if label in ("UNCHANGED", "LIVE"):
        return f"{ticket_line.text} *** HOT-CUT SIDE - Leave Hanging ***"
    if label in ("NEW_ONLY", "NEW"):
        return f"{ticket_line.text} *** CONNECT ***"
    return ticket_line.text


def format_sorted_route_path(
    items: list[InCARow],
    migration_portion: list[InCARow] | None = None,
) -> str:
    """Format the sorted route path for stdout display.

    For migration orders, emits "Current Route Path" and "Migration Portion"
    section headers. For standard Add orders (migration_portion is None),
    emits the single "SORTED ROUTE PATH" header.

    Args:
        items: Sorted InCARow objects (Current Route Path for migration).
        migration_portion: Migration Portion rows (None for non-migration).

    Returns:
        Formatted string with row data.
    """
    lines: list[str] = []

    if migration_portion is not None:
        # Migration order: two sections with headers
        lines.append("=" * 80)
        lines.append("Current Route Path")
        lines.append("=" * 80)
        lines.append("")
        lines.extend(_format_route_rows(items))
        lines.append("")
        lines.append("")
        lines.append("=" * 80)
        lines.append("Migration Portion")
        lines.append("=" * 80)
        lines.append("")
        lines.extend(_format_route_rows(migration_portion))
        lines.append("")
    else:
        # Standard Add order
        lines.append("=" * 80)
        lines.append("SORTED ROUTE PATH (A -> B)")
        lines.append("=" * 80)
        lines.append("")
        lines.extend(_format_route_rows(items))
        lines.append("")

    return "\n".join(lines)


def format_notations(notations: list[str]) -> str:
    """Format the NOTATIONS section for display."""
    if not notations:
        return ""

    lines = [
        "",
        "=" * 80,
        "NOTATIONS",
        "=" * 80,
        "",
    ]
    for n in notations:
        lines.append(f"  {n}")
    lines.append("")
    return "\n".join(lines)


def _format_patching_lines(
    ticket: Ticket,
    has_stages: bool,
) -> list[str]:
    """Format patching lines for a single ticket.

    Handles standard add-order formatting, Stage 1 (HOT-CUT PREPARATION),
    and Stage 2 (HOT-CUT + CLEANUP) migration formatting.
    """
    lines: list[str] = []

    if has_stages and ticket.stage == 1:
        lines.append("Stage 1: HOT-CUT PREPARATION")
        lines.append("")
        _append_ticket_lines_with_site_breaks(
            lines,
            ticket.lines,
            _format_stage1_ticket_line,
        )

    elif has_stages and ticket.stage == 2:
        lines.append("Stage 2: HOT-CUT + CLEANUP")
        lines.append("")

        hotcut_lines = [
            tl
            for tl in ticket.lines
            if tl.hotcut_label in ("UNCHANGED", "NEW_ONLY")
            or (not tl.hotcut_label and tl.classification in ("LIVE", "NEW"))
        ]
        cleanup_lines = [
            tl
            for tl in ticket.lines
            if tl.hotcut_label == "DECOM_ONLY"
            or (not tl.hotcut_label and tl.classification == "DECOMMISSION")
        ]

        if hotcut_lines:
            lines.append("HOT-CUT (prep has been completed)")
            for tl in hotcut_lines:
                label = tl.hotcut_label if tl.hotcut_label else tl.classification
                if label in ("UNCHANGED", "LIVE"):
                    lines.append(f"{tl.text} *** HOT-CUT SIDE ***")
                else:
                    lines.append(tl.text)

        if cleanup_lines:
            if hotcut_lines:
                lines.append("")
            lines.append("CLEANUP (remove old patching)")
            _append_ticket_lines_with_site_breaks(lines, cleanup_lines)

    else:
        _append_ticket_lines_with_site_breaks(lines, ticket.lines)

    return lines


def _build_hub_details(
    ticket_sites: list[str],
    hub_lookup: dict[str, tuple[str, str]],
) -> str | None:
    """Build KEY IMPORTANT DETAILS content based on hub data.

    Returns None if section should be omitted (all sites are hubs).
    """
    from collections import defaultdict

    hub_to_satellites: dict[str, list[str]] = defaultdict(list)
    ship_lines: list[str] = []

    all_hub = True
    for site in ticket_sites:
        info = hub_lookup.get(site)
        if not info:
            continue  # Unknown sites handled by notations
        consumable, hub = info
        upper = (consumable or "").upper()
        if upper == "HUB":
            continue
        all_hub = False
        if upper == "SATELLITE" and hub:
            hub_to_satellites[hub].append(site)
        elif upper == "REMOTE" and hub:
            ship_lines.append(f"Consumables must be shipped from hub {hub} to {site}")

    if all_hub and not ship_lines:
        return None

    lines: list[str] = []
    for hub, satellites in sorted(hub_to_satellites.items()):
        lines.append(f"Collect cables for {', '.join(satellites)} from hub {hub}")
    lines.extend(ship_lines)

    return "\n".join(lines) if lines else None


def format_tickets(
    tickets: list[Ticket],
    all_planned: bool = False,
    bearer: str = "",
    service_id: str = "",
    hub_records: list[dict] | None = None,
) -> str:
    """Format field tech tickets for display.

    For migration orders with staged tickets:
    - Stage 1 (HOT-CUT PREPARATION): inline labels per line
    - Stage 2 (HOT-CUT + CLEANUP): HOT-CUT + CLEANUP sub-headers

    Args:
        tickets: List of Ticket objects.
        all_planned: True if all rows are Planned (hypothetical tickets).
        bearer: Bearer route path name (unused, kept for API compat).
        service_id: Service identifier (e.g., 'ICB-820729').
        hub_records: Optional hub records (unused, kept for API compat).

    Returns:
        Formatted string.
    """
    if not tickets:
        return "\n  (No active tickets - service not yet built)\n"

    has_stages = any(t.stage > 0 for t in tickets)
    parts: list[str] = []

    parts.append("")
    parts.append("=" * 80)
    parts.append("FIELD TECH TICKETS")
    parts.append("=" * 80)

    current_stage = -1
    for i, ticket in enumerate(tickets, 1):
        # Insert stage header when stage changes
        if has_stages and ticket.stage != current_stage:
            current_stage = ticket.stage
            parts.append("")
            if current_stage == 1:
                parts.append("=== STAGE 1: HOT-CUT PREPARATION ===")
            elif current_stage == 2:
                parts.append("=== STAGE 2: HOT-CUT + CLEANUP ===")

        parts.append("")
        parts.append(f"--- Ticket {i}: ---")
        if service_id:
            parts.append(f"Service: {service_id}")
        parts.append(f"Site: {ticket.cluster_name}")

        patching_lines = _format_patching_lines(ticket, has_stages)
        parts.append("")
        parts.extend(patching_lines)

    parts.append("")
    return "\n".join(parts)


_CONSOLIDATED_POC_AND_CHECKLIST = """\
=============

POC

PM operations contact
Use the approved project email channel
Use the approved project phone/WhatsApp channel

=============

LABELING AND CHECKLIST

Labeling Template:
- 1st row: IC#/ICB#
- 2nd row: Near-Side ODF/Equipment Location
- 3rd row: Far-Side ODF/Equipment Location

---

Checklist (Upload photos):
- Scope Images: Photos of passing fiber scope results for each connector installed
- Port Detail: Close-up of all ports/labels while connected (must be legible)
- Path and Management: Wide-angle shots showing jumpers properly dressed, routed, and separated
- Photos must prove fiber bend radius and strain relief are maintained end-to-end

Final Actions:
- Restore work area to data-center operational standards. If uncertain, ask data-center staff
- Notify POC before leaving site"""


def _build_consolidated_hub_lookup(
    hub_records: list[dict] | None,
) -> dict[str, tuple[str, str]]:
    """Return normalized hub metadata keyed by site code."""
    hub_lookup: dict[str, tuple[str, str]] = {}
    for record in hub_records or []:
        site_code = str(record.get("SITE_CODE", "")).strip()
        consumable = str(record.get("CONSUMABLE", "")).strip()
        hub = str(record.get("HUB", "")).strip()
        if site_code and (consumable or site_code not in hub_lookup):
            hub_lookup[site_code] = (consumable, hub)
    return hub_lookup


def _location_sites_for_hub_details(
    service_ticket_pairs: list[tuple[str, Ticket]],
) -> list[str]:
    """Collect unique non-stage-2 sites for consolidated hub guidance."""
    ordered_sites: list[str] = []
    seen_sites: set[str] = set()
    for _, ticket in service_ticket_pairs:
        if ticket.stage == 2:
            continue
        for site in ticket.sites:
            if site not in seen_sites:
                ordered_sites.append(site)
                seen_sites.add(site)
    return ordered_sites


def _append_consolidated_service_blocks(
    parts: list[str],
    service_ticket_pairs: list[tuple[str, Ticket]],
    has_stages: bool,
) -> None:
    """Append per-service patching blocks for one location."""
    for service_id, ticket in service_ticket_pairs:
        parts.append("")
        parts.append(f"- Service ID: {service_id}")
        parts.append(f"- Site: {ticket.cluster_name}")
        parts.append("")
        parts.append("Patching:")
        parts.extend(_format_patching_lines(ticket, has_stages))


def _append_consolidated_hub_details(
    parts: list[str],
    location_sites: list[str],
    hub_lookup: dict[str, tuple[str, str]],
) -> None:
    """Append the optional hub-details section for one location."""
    hub_details = _build_hub_details(location_sites, hub_lookup)
    if not hub_details:
        return

    parts.append("")
    parts.append("=============")
    parts.append("")
    parts.append("KEY IMPORTANT DETAILS")
    parts.append("")
    parts.append(hub_details)


def _append_consolidated_location_section(
    parts: list[str],
    location_id: str,
    service_ticket_pairs: list[tuple[str, Ticket]],
    hub_lookup: dict[str, tuple[str, str]],
) -> None:
    """Append one full consolidated location block."""
    parts.append("")
    parts.append(f"--- Location: {location_id} ---")

    has_stages = any(ticket.stage > 0 for _, ticket in service_ticket_pairs)
    _append_consolidated_service_blocks(parts, service_ticket_pairs, has_stages)
    _append_consolidated_hub_details(
        parts,
        _location_sites_for_hub_details(service_ticket_pairs),
        hub_lookup,
    )

    parts.append("")
    parts.append(_CONSOLIDATED_POC_AND_CHECKLIST)


def format_consolidated_tickets(
    location_tickets: dict[str, list[tuple[str, Ticket]]],
    hub_records: list[dict] | None = None,
) -> str:
    """Format consolidated field tech tickets grouped by physical location.

    Each location (identified by SITE_LOCATION_ID) gets a single consolidated
    ticket containing patching instructions from all services at that location,
    with shared POC/labeling/checklist and hub details appearing only once.

    Args:
        location_tickets: Mapping of SITE_LOCATION_ID (or fallback site_code)
            to list of (service_id, Ticket) pairs.
        hub_records: Optional HUB_SITE records for consumable hub details.

    Returns:
        Formatted string with consolidated ticket output.
    """
    if not location_tickets:
        return ""

    hub_lookup = _build_consolidated_hub_lookup(hub_records)
    parts: list[str] = []
    parts.append("")
    parts.append("=" * 80)
    parts.append("CONSOLIDATED FIELD TECH TICKETS")
    parts.append("=" * 80)

    for location_id in sorted(location_tickets.keys()):
        service_ticket_pairs = location_tickets[location_id]
        if not service_ticket_pairs:
            continue
        _append_consolidated_location_section(
            parts,
            location_id,
            service_ticket_pairs,
            hub_lookup,
        )

    parts.append("")
    return "\n".join(parts)
