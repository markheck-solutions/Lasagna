"""Shared parser helpers for INCA route rows."""

from __future__ import annotations

import re


def _safe_str(val: object) -> str:
    """Convert cell value to stripped string, handle None."""
    if val is None:
        return ""
    return str(val).strip()


def _safe_int(val: object) -> int:
    """Convert cell value to int, default 0."""
    if val is None:
        return 0
    if not isinstance(val, str | int | float):
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _has_usable_cabling_points(val: str) -> bool:
    """Return True when an export row carries real BO-side patch points."""
    normalized = val.strip().upper()
    return bool(normalized and normalized not in {"NA", "N/A"})


def _csv_optional(val: object) -> str | None:
    """Convert value to str | None (None if empty/None)."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _split_device_location(device_location: str) -> tuple[str, str]:
    """Split DEVICE_LOCATION into (location_prefix, port_address).

    Handles two CCP.LOCATION formats from Snowflake:
    - Double-colon: '[building]rack::port:addr' -> ('[building]rack', 'port:addr')
    - Single-colon: '[building]rack:port' -> ('[building]rack', 'port')
    - No colon after bracket: returns (device_location, '')
    """
    bracket_end = device_location.rfind("]")
    if bracket_end < 0:
        return (device_location, "")
    after_bracket = device_location[bracket_end + 1 :]
    if "::" in after_bracket:
        parts = after_bracket.split("::", 1)
        return (device_location[: bracket_end + 1] + parts[0], parts[1])
    last_colon = after_bracket.rfind(":")
    if last_colon >= 0:
        return (
            device_location[: bracket_end + 1] + after_bracket[:last_colon],
            after_bracket[last_colon + 1 :],
        )
    return (device_location, "")


def _build_ne_information(
    ne: str,
    ne_part: str,
    optic_function: str,
    device_location: str,
    connection_point_nr: str,
    direction: str,
    chassis_function: str | None = None,
) -> str:
    """Construct INCA NE Information composite string from Snowflake fields.

    Formula: {NE} {NE_PART} -{CHASSIS}\\{OPTIC} -({PORT_ADDR}.{CPNR}:{DIR})

    Port address is extracted from device_location after '::' separator,
    with the last ':' replaced by '\\' to match INCA format.

    Args:
        ne: Device name (e.g., 'dls-b23' or 'DAL/C2 XS G40 24').
        ne_part: Device part/chassis (e.g., 'NCS-5508' or 'G42 01').
        optic_function: Optic function (e.g., 'QDD-400G-LR4-S').
        device_location: Full CCP.LOCATION (e.g., '[BLDG]rack::port:addr').
        connection_point_nr: Connection point number (e.g., '01' or '.').
        direction: 'Tx' or 'Rx'.
        chassis_function: Optional chassis function from Query C (e.g., 'CHM6-C8').
            Falls back to ne_part if not provided.

    Returns:
        Composite NE Information string matching INCA export format.
    """
    # Extract port address from device location
    _, port_addr = _split_device_location(device_location)
    # Replace last ':' with '\' to match INCA format (only for :: format)
    if "::" in device_location:
        last_colon = port_addr.rfind(":")
        if last_colon >= 0:
            port_addr = port_addr[:last_colon] + "\\" + port_addr[last_colon + 1 :]

    chassis = chassis_function if chassis_function else ne_part
    return (
        f"{ne} {ne_part} -{chassis}\\{optic_function} "
        f"-({port_addr}.{connection_point_nr}:{direction})"
    )


def _parse_cabling_point(raw: str) -> str | None:
    """Extract the numeric cabling point from INCA format.

    '45 Cable.45' -> '45'
    '05 Cable.05' -> '05' (preserve leading zeros as-is from INCA)
    'N/A' or 'NA' -> None
    """
    raw = raw.strip()
    if not raw or raw.upper() in ("N/A", "NA"):
        return None
    # Take the first token (number before ' Cable.')
    m = re.match(r"(\d+)", raw)
    if m:
        return m.group(1)
    return raw


def _cabling_point_int(raw: str) -> int:
    """Extract numeric cabling point as integer for sort ordering."""
    raw = raw.strip()
    if not raw or raw.upper() in ("N/A", "NA"):
        return 0
    m = re.match(r"(\d+)", raw)
    return int(m.group(1)) if m else 0


def _ne_group_key(ne_info: str | None) -> str:
    """Extract device grouping key from NE Information string.

    Returns '{NE} {NE_PART}' prefix (before first ' -'), which is
    identical between INCA and Snowflake sources regardless of
    chassis/linecard model differences.
    """
    if not ne_info or not ne_info.strip():
        return "unknown"
    return ne_info.strip().split(" -")[0]
