"""Route row model used by the Snowflake-backed workbook path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

_TRANSMISSION_NE_TYPES: frozenset[str] = frozenset(
    {
        "DTN",
        "G30",
        "G31",
        "G40",
        "OLA",
        "OLR",
        "OLR96D",
        "OTC",
        "OTM32D",
        "OTM40D",
        "OTM96D",
        "OTN",
        "WS",
        "65FLEXI",
        "65ILA",
        "65ROADM",
        "OADM",
        "SAT",
        "TM",
        "B",
    }
)

_ROUTER_FUNCTION_PREFIXES: tuple[str, ...] = (
    "ROUTER",
    "SWITCH",
    "NCS-",
    "8201",
    "8212",
    "7280",
    "ASR-",
    "DCS-",
    "ALU ",
    "C1111",
    "C1121",
    "892",
    "1841",
    "1921",
    "1941",
    "2921",
    "4500",
    "4900",
    "NCS 5",
    "NCS 54",
    "R660",
    "TI-PG",
    "EX3400",
    "EX4600",
)

_ROUTER_NE_TYPE_PREFIXES: tuple[str, ...] = (
    "NCS-",
    "NCS ",
    "ASR-",
    "DCS-",
    "ALU ",
    "C1111",
    "C1121",
    "892",
    "1841",
    "1921",
    "1941",
    "2921",
    "4500",
    "4900",
    "8201",
    "8212",
    "7280",
    "R660",
    "TI-PG",
    "EX3400",
    "EX4600",
)


def _is_planned(value: str | None) -> bool:
    return bool(value and value.strip().lower() == "planned")


def _is_router_function(ne_function: str) -> bool:
    upper = ne_function.upper()
    return any(prefix in upper for prefix in _ROUTER_FUNCTION_PREFIXES)


def _is_router_ne_type(ne_type: str) -> bool:
    upper = ne_type.strip().upper()
    return any(upper.startswith(prefix) for prefix in _ROUTER_NE_TYPE_PREFIXES)


@dataclass
class InCARow:
    """One Snowflake export row normalized to the workbook route-row shape."""

    site_code: str
    site_type: str
    ne_info: str | None
    cabling_location: str
    cabling_points: str
    conn_type: str
    location_alias: str | None
    route_path: str
    pos: int
    status_o_time: str | None
    o_time: str | None
    status_t_time: str | None
    t_time: str | None
    comment: str | None
    classification: str = ""
    row_index: int = 0
    site_type_no: str = ""
    display_points: str | None = None
    service_id: str | None = None
    dp_owner: str | None = None
    site_side: str | None = None
    floor: str = ""
    room: str = ""
    row: str = ""
    rowside: str = ""
    rack: str = ""
    shelf: str = ""
    subrack: str = ""
    connection_point_nr: str = ""
    ne_type: str = ""
    ne_function: str = ""
    slot: str = ""
    subslot: str = ""
    direction: str = ""

    def __post_init__(self) -> None:
        self.classify()

    def classify(self) -> None:
        """Classify row status from explicit planned status fields."""
        if _is_planned(self.status_t_time):
            self.classification = "DECOMMISSION"
        elif _is_planned(self.status_o_time):
            self.classification = "NEW"
        else:
            self.classification = "LIVE"

    @property
    def is_device_row(self) -> bool:
        """True when row represents active equipment rather than passive ODF."""
        return bool(self.ne_info and self.ne_info.strip())

    @property
    def is_ne_location(self) -> bool:
        """True when the row carries a direct NE-location patch point."""
        return bool(
            self.cabling_location
            and self.cabling_location.strip().lower().startswith("ne-location")
        )

    @property
    def is_router(self) -> bool:
        """Structured router/switch classification from Snowflake fields."""
        if self.ne_type:
            if _is_router_ne_type(self.ne_type):
                return True
            if self.ne_type.upper() in _TRANSMISSION_NE_TYPES:
                return False
        if self.ne_function:
            return _is_router_function(self.ne_function)
        return False

    @property
    def is_demarcation(self) -> bool:
        """True when the row is a DP or SDP demarcation point."""
        if not self.ne_info:
            return False
        ne_text = self.ne_info.strip()
        return ne_text.startswith("DP ") or ne_text.startswith("SDP ")

    def tuple_key(self) -> tuple[object, ...]:
        """Return stable row identity fields for duplicate fact detection."""
        return (
            self.site_code,
            self.site_type,
            self.ne_info or "",
            self.cabling_location,
            self.cabling_points,
            self.conn_type,
            self.location_alias or "",
            self.route_path,
            self.pos,
            self.status_o_time or "",
            self.o_time or "",
            self.status_t_time or "",
            self.t_time or "",
            self.comment or "",
            self.dp_owner or "",
            self.slot,
            self.subslot,
            self.direction,
        )


class SnowflakeCombinedData(NamedTuple):
    """Parsed combined Snowflake export grouped for route workbook generation."""

    services: dict[str, list[InCARow]]
    edge_records: list[dict[str, object]]
    hub_records: list[dict[str, object]] = []
    trunk_metadata: list[dict[str, object]] = []
    route_order_metadata: list[dict[str, object]] = []
    transmission_metadata: list[dict[str, object]] = []
    transport_device_adjacency: list[dict[str, object]] = []
    dp_endpoint_roles: list[dict[str, object]] = []
    bo_fibers: list[dict[str, object]] = []
