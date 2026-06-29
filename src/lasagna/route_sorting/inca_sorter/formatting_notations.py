"""Notation formatting for INCA route sorter output."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from .models import InCARow, _is_planned
from .parsers import _safe_str

_NO_HUB_ASSIGNMENT_SITE_TYPES = {"X", "U"}


def _format_date(raw: str | None) -> str:
    """Format INCA date (YYYYMMDD or other) to YYYY-MM-DD."""
    if not raw:
        return "unknown"
    raw = _safe_str(raw)
    if re.match(r"^\d{8}$", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def _site_requires_hub_assignment(site_types: set[str]) -> bool:
    """Only field-patching site types need consumable hub assignments."""
    normalized = {
        _safe_str(site_type).strip().upper()
        for site_type in site_types
        if _safe_str(site_type).strip()
    }
    if not normalized:
        return True
    return any(site_type not in _NO_HUB_ASSIGNMENT_SITE_TYPES for site_type in normalized)


def _days_until(raw: str | None) -> str:
    """Calculate days from today until the given date."""
    if not raw:
        return "?"
    raw_str = _safe_str(raw)
    try:
        if re.match(r"^\d{8}$", raw_str):
            target = date(int(raw_str[:4]), int(raw_str[4:6]), int(raw_str[6:8]))
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", raw_str):
            target = date(int(raw_str[:4]), int(raw_str[5:7]), int(raw_str[8:10]))
        else:
            return "?"
        delta = (target - date.today()).days
        return str(delta)
    except (ValueError, TypeError):
        return "?"


def _append_planned_notations(
    notations: list[str],
    rows: list[InCARow],
    *,
    include_planned: bool,
) -> None:
    """Append PLANNED notation lines using legacy device and dedupe rules."""
    if not include_planned:
        return

    seen_planned: set[str] = set()
    for row in rows:
        if not _is_planned(row.status_o_time):
            continue
        device = re.sub(r":(?:Tx|Rx)\)$", "", row.ne_info or "") or "ODF"
        key = f"{device}@{row.site_code}"
        if key in seen_planned:
            continue
        seen_planned.add(key)
        date_str = _format_date(row.o_time)
        days = _days_until(row.o_time)
        notations.append(
            f"PLANNED: {device} at {row.site_code} scheduled for {date_str} "
            f"({days} days). Not yet physically patched."
        )


def _build_bo_fiber_lookup(bo_fibers: list[dict] | None) -> dict[str, list[dict]]:
    """Return BO_FIBERS keyed by lowercased A-end NE name."""
    fiber_lookup: dict[str, list[dict]] = defaultdict(list)
    for fiber in bo_fibers or []:
        ne_name = str(fiber.get("A_NE_NAME", "")).strip()
        if not ne_name:
            continue
        ne_key = ne_name.split(",")[0].strip().lower()
        fiber_lookup[ne_key].append(fiber)
    return dict(fiber_lookup)


def _extract_bug_device_name(ne_info: str | None) -> str:
    """Extract the legacy device label used in INCA bug notations."""
    if not ne_info:
        return ""
    return ne_info.split(" -")[0].strip() if " -" in ne_info else ne_info


def _extract_bo_fiber_port_target(ne_info: str | None) -> tuple[str | None, str | None]:
    """Extract the slot and sub-port used for BO_FIBERS correlation."""
    port_match = re.search(r"-\(([^)]+)\)", ne_info or "")
    if not port_match:
        return None, None

    port_str = re.sub(r"\.\d*:(?:Tx|Rx)$", "", port_match.group(1))
    if "\\" not in port_str:
        return port_str, None

    slot, sub_port = port_str.split("\\", 1)
    return slot, sub_port.split(".")[0] if sub_port else None


def _matched_bo_fiber_rows(
    fiber_records: list[dict],
    port_slot: str | None,
    port_sub: str | None,
) -> list[dict]:
    """Return exact BO_FIBERS matches for the legacy slot or sub-port rules."""
    if not port_slot or not port_sub:
        return []

    target = f"{port_slot}:{port_sub}"
    matched: list[dict] = []
    for fiber in fiber_records:
        a_location = str(fiber.get("A_LOCATION", "")).strip()
        colon_pos = a_location.find("::")
        if colon_pos < 0:
            continue
        port_section = a_location[colon_pos + 2 :]
        if port_section == target or port_section.startswith(target + ":"):
            matched.append(fiber)
    return matched


def _build_converged_bo_fiber_hint(
    fiber_records: list[dict],
    port_slot: str | None,
    port_sub: str | None,
) -> str | None:
    """Return the optional BO_FIBERS diagnostic hint when matches converge."""
    matched = _matched_bo_fiber_rows(fiber_records, port_slot, port_sub)
    if not matched or not port_slot or not port_sub:
        return None

    b_locations = {
        str(match.get("B_LOCATION", "")).strip() for match in matched if match.get("B_LOCATION")
    }
    if len(b_locations) != 1:
        return None

    b_location = next(iter(b_locations))
    b_ports = sorted(
        {
            str(match.get("B_CONNECTION_POINT", "")).strip().split()[0]
            for match in matched
            if str(match.get("B_CONNECTION_POINT", "")).strip()
        }
    )
    full_port = f"{port_slot}/{port_sub}"
    hint = f"*Previous {full_port} was on {b_location}"
    if len(b_ports) == 1:
        return f"{hint} port {b_ports[0]}"
    if len(b_ports) == 2:
        return f"{hint} ports {b_ports[0]}+{b_ports[1]}"
    if len(b_ports) > 2:
        return f"{hint} ports {b_ports[0]}+{b_ports[-1]}"
    return hint


def _append_inca_bug_notations(
    notations: list[str],
    rows: list[InCARow],
    bo_fibers: list[dict] | None,
) -> None:
    """Append VERIFY notations for NE-location and optional BO_FIBERS hints."""
    bo_fiber_lookup = _build_bo_fiber_lookup(bo_fibers)
    seen_bugs: set[str] = set()
    for row in rows:
        if not row.has_inca_bug:
            continue

        device = _extract_bug_device_name(row.ne_info)
        key = f"{device}@{row.site_code}"
        if key in seen_bugs:
            continue
        seen_bugs.add(key)

        notation = f"VERIFY: {device} has NE-Location, not BO ODF."
        ne_name = (row.ne_info or "").strip().split(" ")[0].lower()
        port_slot, port_sub = _extract_bo_fiber_port_target(row.ne_info)
        hint = _build_converged_bo_fiber_hint(
            bo_fiber_lookup.get(ne_name, []),
            port_slot,
            port_sub,
        )
        if hint:
            notation += f"\n    {hint}"
        notations.append(notation)


def _append_decommission_notations(
    notations: list[str],
    rows: list[InCARow],
    *,
    include_decommission: bool,
) -> None:
    """Append DECOMMISSION notation lines using legacy dedupe rules."""
    if not include_decommission:
        return

    seen_decom: set[str] = set()
    for row in rows:
        if not _is_planned(row.status_t_time):
            continue
        device = re.sub(r":(?:Tx|Rx)\)$", "", row.ne_info or "") or "ODF"
        key = f"{device}@{row.site_code}"
        if key in seen_decom:
            continue
        seen_decom.add(key)
        date_str = _format_date(row.t_time)
        notations.append(
            f"DECOMMISSION: {device} at {row.site_code} scheduled for removal on {date_str}."
        )


def _build_hub_assignment_lookup(
    hub_records: list[dict] | None,
) -> dict[str, tuple[str, str]]:
    """Return normalized hub assignment metadata keyed by site code."""
    hub_lookup: dict[str, tuple[str, str]] = {}
    for record in hub_records or []:
        site_code = str(record.get("SITE_CODE", "")).strip()
        consumable = str(record.get("CONSUMABLE", "")).strip()
        hub = str(record.get("HUB", "")).strip()
        if site_code and (consumable or site_code not in hub_lookup):
            hub_lookup[site_code] = (consumable, hub)
    return hub_lookup


def _site_types_by_site(
    rows: list[InCARow],
    hub_check_sites: set[str] | None,
) -> dict[str, set[str]]:
    """Collect site types for sites that should be checked for hub assignments."""
    site_types: dict[str, set[str]] = {}
    for row in rows:
        site = _safe_str(row.site_code).strip()
        if not site:
            continue
        if hub_check_sites is not None and site not in hub_check_sites:
            continue
        site_types.setdefault(site, set()).add(row.site_type)
    return site_types


def _append_missing_hub_assignment_notations(
    notations: list[str],
    rows: list[InCARow],
    hub_records: list[dict] | None,
    hub_check_sites: set[str] | None,
) -> None:
    """Append VERIFY notations for sites missing a usable hub assignment."""
    if not hub_records:
        return

    hub_lookup = _build_hub_assignment_lookup(hub_records)
    site_types_by_site = _site_types_by_site(rows, hub_check_sites)
    for site, site_types in sorted(site_types_by_site.items()):
        if not _site_requires_hub_assignment(site_types):
            continue
        consumable, _hub = hub_lookup.get(site, ("", ""))
        if consumable and consumable.upper() != "N/A":
            continue
        notations.append(f"VERIFY: {site} has no hub assignment. Check consumables sourcing.")


def generate_notations(
    rows: list[InCARow],
    is_pure_add: bool = False,
    trunk_edges: Sequence[tuple[str, str, str | None]] | None = None,
    is_migration: bool = False,
    hub_records: list[dict] | None = None,
    bo_fibers: list[dict] | None = None,
    hub_check_sites: set[str] | None = None,
) -> list[str]:
    """Generate NOTATIONS section entries.

    Includes:
    - PLANNED alerts for Status o-time = Planned (Mixed Add only, omitted for Pure Add
      and Migration)
    - INCA BUG alerts for NCS + NE-Location (all order types), with an optional
      exact-match BO_FIBERS diagnostic line when evidence converges on one ODF
    - DECOMMISSION alerts for Status t-time = Planned (omitted for Migration)
    - VERIFY alerts for sites without hub assignments

    For migration orders, PLANNED and DECOMMISSION are expected states and
    alerting about them is noise. VERIFY (INCA bug) remains actionable.

    Args:
        rows: All rows (sorted or unsorted).
        is_pure_add: True if all rows are Planned (Pure Add). Suppresses PLANNED alerts.
        trunk_edges: Trunk edges from parse_trunk_edges(). If provided, enables
            PP-184 suspicious trunk detection.
        is_migration: True for migration orders. Suppresses PLANNED and DECOMMISSION.
        hub_records: Optional HUB_SITE records for consumable hub notations.
        bo_fibers: Optional BO_FIBERS records for tightly gated INCA BUG
            diagnostic enrichment.
        hub_check_sites: Optional site codes that actually receive field-tech
            patching work. When provided, missing-hub VERIFY notations are
            limited to this set.

    Returns:
        List of notation strings.
    """
    notations: list[str] = []

    _append_planned_notations(
        notations,
        rows,
        include_planned=not is_pure_add and not is_migration,
    )
    _append_inca_bug_notations(notations, rows, bo_fibers)
    _append_decommission_notations(
        notations,
        rows,
        include_decommission=not is_migration,
    )
    _append_missing_hub_assignment_notations(
        notations,
        rows,
        hub_records,
        hub_check_sites,
    )

    return notations
