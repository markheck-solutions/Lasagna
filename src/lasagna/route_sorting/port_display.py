"""Display-only port extraction for workbook NE-location rows."""

from __future__ import annotations

import re

from lasagna.route_sorting.route_rows import InCARow


def _assemble_structured_port(row: InCARow) -> str | None:
    if not row.slot:
        return None

    slot = row.slot.rstrip(".")
    if row.subslot:
        return f"{slot}/{row.subslot}"

    if row.connection_point_nr and row.connection_point_nr.strip("."):
        connection_point = row.connection_point_nr.strip(".")
        separator = "/" if "/" in slot else "."
        return f"{slot}{separator}{connection_point}"

    return None


def _normalize_port_address(port: str) -> str:
    port = port.replace("\\", "/")
    return re.sub(r"\.+$", "", port)


def _extract_structured_port_address(row: InCARow) -> str | None:
    return _assemble_structured_port(row)


def _extract_double_dot_port_address(row: InCARow) -> str | None:
    if not row.ne_info:
        return None

    match = re.search(r"-\((\d+/\d+)\.\.(?:.*?-)?(\d+):", row.ne_info)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _extract_pattern_port_address(raw_text: str | None) -> str | None:
    if not raw_text:
        return None

    port_pattern = r"(\d+[/\\]\d+(?:[/\\]\d+)*(?:\.\d+)?\.{0,3})"
    match = re.search(port_pattern, raw_text)
    if not match:
        return None
    return _normalize_port_address(match.group(1))


def _extract_parenthetical_port_address(row: InCARow) -> str | None:
    if not row.ne_info:
        return None

    match = re.search(r"-\(([^:]+):", row.ne_info)
    if not match:
        return None
    return _normalize_port_address(match.group(1))


def extract_port_address(row: InCARow) -> str:
    """Extract normalized display port text without creating route-order facts."""
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

    return str(row.pos)
