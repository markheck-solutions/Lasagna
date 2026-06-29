"""Data classes, NamedTuples, and constants for the INCA sorter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

INCA_COLUMNS = [
    "Site Code",
    "Site Type",
    "NE Information",
    "Cabling Location",
    "Cabling Points",
    "Conn type",
    "Location Alias",
    "Route Path",
    "Pos",
    "Status o-time",
    "O-time",
    "Status t-time",
    "T-time",
    "Comment",
]

INCA_BUG_MODELS = {"NCS-5504", "NCS-5508", "NCS-5516"}

ROUTER_PATTERNS = [
    "NCS-5504",
    "NCS-5508",
    "NCS-5516",
    "NCS-55A1",
    "NCS-57C3-MOD",
    "ASR",
    "7280",
    "8201",
]
_ROUTER_FUNCTION_PREFIXES = (
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

# Phase 2B: Data-driven device classification sets.
# NE_TYPEs confirmed as transmission (DWDM/WDM/optical transport).
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

# NE_TYPEs that are ambiguous (e.g., RLS/ROADM are optical but not
# classified as transmission by the existing hostname heuristic).
# These return None from structured classification, falling through
# to the hostname heuristic for backward compatibility.


def _is_planned(val: str | None) -> bool:
    """Check if a status field equals 'Planned' (case-insensitive)."""
    return bool(val and val.strip().lower() == "planned")


def service_mode(service_id: str | None) -> str:
    """Determine service mode from service ID prefix.

    Args:
        service_id: Service identifier (e.g., 'IC-136025', 'ICB-820729').

    Returns:
        'ICB' if service_id starts with 'ICB-', otherwise 'IC' (default).

    Note:
        ICB- is checked before IC- to handle the prefix correctly.
        Unknown/blank IDs default to IC for backward compatibility.
    """
    if not service_id:
        return "IC"
    sid = service_id.strip().upper()
    # Check ICB- first (longer prefix) before IC-
    if sid.startswith("ICB-"):
        return "ICB"
    return "IC"


def _strip_location_prefixes(cabling_location: str | None) -> str:
    """Normalize a cabling location for structured/fallback key extraction."""
    if not cabling_location:
        return ""
    loc = cabling_location.strip()
    if not loc:
        return ""
    if loc.lower().startswith("ne-location:"):
        loc = loc.split(":", 1)[1].strip()
    if loc.startswith("["):
        bracket_end = loc.find("]")
        if bracket_end >= 0:
            loc = loc[bracket_end + 1 :]
    return loc.strip()


def _location_segments(cabling_location: str | None) -> tuple[str, ...]:
    """Split a normalized cabling location into non-empty path segments."""
    loc = _strip_location_prefixes(cabling_location)
    if not loc:
        return ()
    return tuple(part for part in loc.split("/") if part)


@dataclass
class InCARow:
    """One row from an INCA route path export."""

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

    # Derived fields (set after construction)
    classification: str = ""  # NEW, DECOMMISSION, LIVE
    row_index: int = 0  # Original row number for stable sorting
    site_type_no: str = (
        ""  # Snowflake SITE_TYPE_NO (e.g., '107' for 'EL2 X 107'); empty for legacy CSV
    )
    display_points: str | None = None  # Normalized port for NE-Location display
    service_id: str | None = None  # Snowflake SERVICE_ID for multi-ICB grouping
    dp_owner: str | None = None  # Snowflake DP_OWNER (ARELION/EXTERNAL) for DP/SDP rows
    site_side: str | None = None  # Snowflake SITE_SIDE ('A' or 'B') for trunk ODF rows

    # Phase 1: Structured location fields from CCP (populated by Snowflake path)
    floor: str = ""  # CCP.FLOOR (e.g., '5TH FL.')
    room: str = ""  # CCP.ROOM (e.g., 'STE 524')
    row: str = ""  # CCP.ROW_ (e.g., '07')
    rowside: str = ""  # CCP.ROWSIDE (e.g., 'F' or 'R')
    rack: str = ""  # CCP.RACK (e.g., 'C2')
    shelf: str = ""  # CCP.SHELF (e.g., 'RU43')
    subrack: str = ""  # CCP.SUBRACK

    # Phase 1: Device/port structured fields (populated by Snowflake path)
    connection_point_nr: str = ""  # CCP.CONNECTION_POINT_NR (e.g., '03')
    ne_type: str = ""  # NE_PART_CURRENT.NE_TYPE (e.g., 'NCS-5508')
    ne_function: str = ""  # NE_CURRENT.FUNCTION (device classification fallback)
    trunk_media: str = ""  # PCG.MEDIA from TRUNK_METADATA (Phase 1: field only)

    # Phase 2A: Structured port assembly fields (populated by Snowflake DEVICE query)
    slot: str = ""  # CCP.SLOT (e.g., '0/0' for routers, '05' for DTN)
    subslot: str = ""  # CCP.SUBSLOT (e.g., '1' for NCS-5508)

    def __post_init__(self) -> None:
        self.classify()

    def classify(self) -> None:
        """Classify row as NEW, DECOMMISSION, or LIVE.

        Termination (t_time) takes priority: if a row has both o_time and
        t_time Planned, it is being decommissioned (e.g., OL10 self-loop
        trunks in migration orders).
        """
        if _is_planned(self.status_t_time):
            self.classification = "DECOMMISSION"
        elif _is_planned(self.status_o_time):
            self.classification = "NEW"
        else:
            self.classification = "LIVE"

    @property
    def is_device_row(self) -> bool:
        """True if this row represents a network device (not passive ODF)."""
        return bool(self.ne_info and self.ne_info.strip())

    @property
    def is_ne_location(self) -> bool:
        """True if Cabling Location indicates NE-Location (direct device patch).

        Case-insensitive check -- real INCA exports use 'NE-location:' (lowercase).
        """
        return bool(
            self.cabling_location
            and self.cabling_location.strip().lower().startswith("ne-location")
        )

    @property
    def has_inca_bug(self) -> bool:
        """True if row triggers the INCA bug warning."""
        if not self.ne_info or not self.is_ne_location:
            return False
        return any(model in self.ne_info for model in INCA_BUG_MODELS)

    @property
    def is_router_structured(self) -> bool | None:
        """Data-driven router detection from NE_TYPE / NE_FUNCTION.

        Returns None if structured fields cannot determine classification,
        allowing the hostname heuristic to decide.
        """
        if self.ne_type:
            if self.ne_type in _TRANSMISSION_NE_TYPES:
                return False
            # Unknown NE_TYPE (e.g., RLS/ROADM) -- can't determine from type alone
            return None
        if self.ne_function:
            upper = self.ne_function.upper()
            if upper.startswith("ROUTER") or upper.startswith("SWITCH"):
                return True
            # Known router model prefixes in NE_FUNCTION
            for prefix in _ROUTER_FUNCTION_PREFIXES:
                if prefix in upper:
                    return True
            return False
        return None  # Can't determine from structured data

    @property
    def is_router(self) -> bool:
        """True if NE Information indicates a router/switch.

        Phase 2B: checks structured fields first (NE_TYPE/NE_FUNCTION),
        falls back to hostname heuristic (PP-065) when structured is None.
        """
        structured = self.is_router_structured
        if structured is not None:
            return structured
        # Hostname heuristic fallback (H5: bounded legacy path)
        # Fires for <0.1% of devices where NE_TYPE and NE_FUNCTION are both empty.
        import logging

        if not self.ne_info:
            return False
        ne = self.ne_info.strip()
        if not ne:
            return False
        # PRIMARY: Lowercase hostname = router/switch
        if ne[0].islower():
            logging.getLogger("inca_sorter").warning(
                "H5 hostname fallback: %s classified as router by lowercase hostname (no NE_TYPE/NE_FUNCTION)",
                ne.split()[0],
            )
            return True
        # SECONDARY: Known router chassis patterns (for display/classification)
        for pattern in ROUTER_PATTERNS:
            if pattern in ne:
                logging.getLogger("inca_sorter").warning(
                    "H5 hostname fallback: %s classified as router by pattern match (no NE_TYPE/NE_FUNCTION)",
                    ne.split()[0],
                )
                return True
        return False

    @property
    def is_demarcation(self) -> bool:
        """True if this row is a demarcation point (DP ODF or SDP ODF)."""
        if not self.ne_info:
            return False
        ne = self.ne_info.strip()
        return ne.startswith("DP ") or ne.startswith("SDP ")

    @property
    def is_external_demarcation(self) -> bool:
        """True if this is an EXTERNAL demarcation (excluded from cleanup)."""
        if not self.is_demarcation:
            return False
        return (self.dp_owner or "").upper() != "ARELION"

    @property
    def building_key(self) -> str:
        """Structured-first first-segment key for site/building heuristics."""
        rack = self.rack.strip()
        if rack:
            return rack
        segments = _location_segments(self.cabling_location)
        return segments[0] if segments else ""

    @property
    def cabinet_key(self) -> str:
        """Structured-first cabinet proximity key used for pairing logic."""
        rack = self.rack.strip()
        row = self.row.strip()
        if rack and row:
            return f"{rack}/{row}"
        if rack:
            return rack
        segments = _location_segments(self.cabling_location)
        if len(segments) >= 2:
            return f"{segments[0]}/{segments[1]}"
        return segments[0] if segments else ""

    @property
    def cabinet_sort_key(self) -> tuple[str, str, str, str, str, str, str] | None:
        """Structured cabinet tuple for Phase 3 ordering.

        Returns None when structured cabinet fields are absent so callers can
        fall back to the legacy text-based ordering path per row.
        """
        rack = self.rack.strip()
        if not rack:
            return None
        return (
            self.floor.strip(),
            self.room.strip(),
            self.row.strip(),
            self.rowside.strip(),
            rack,
            self.shelf.strip(),
            self.subrack.strip(),
        )

    def tuple_key(self) -> tuple:
        """Return a tuple of all column values for duplicate detection."""
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
        )


@dataclass
class TicketLine:
    """One formatted line in a field tech ticket."""

    text: str
    variant: str  # A, B, or C
    site_code: str
    classification: str = ""  # NEW, LIVE, DECOMMISSION (empty for add orders)
    hotcut_label: str = ""  # PP-250: UNCHANGED, NEW_ONLY, DECOM_ONLY
    source_key: tuple = ()  # InCARow.tuple_key() for post-build annotation


@dataclass
class Ticket:
    """A complete field tech ticket for one metro cluster."""

    cluster_name: str
    lines: list[TicketLine] = field(default_factory=list)
    is_hotcut: bool = False
    sites: list[str] = field(default_factory=list)
    stage: int = 0  # 0=add order, 1=Stage 1 (prep), 2=Stage 2 (hot-cut+cleanup)


class SortResult(NamedTuple):
    """Return type of sort_inca_route_path."""

    rows: list[InCARow]
    notations: list[str]
    tickets: list[Ticket]
    info_lines: list[str]
    all_planned: bool
    bearer: str
    migration_portion: list[InCARow] | None = None


class SnowflakeCombinedData(NamedTuple):
    """Return type of read_snowflake_combined_csv."""

    services: dict[str, list[InCARow]]
    edge_records: list[dict]
    tl_device_records: list[dict]
    hub_records: list[dict] = []
    trunk_metadata: list[dict] = []
    route_order_metadata: list[dict] = []
    transmission_metadata: list[dict] = []
    bo_fibers: list[dict] = []


class _DirectionInfo(NamedTuple):
    """Direction resolution result from TL_DEVICE data."""

    arrival_bldg: str | None
    departure_bldg: str | None
    arrival_type: str | None  # site_type of arrival-facing device
    departure_type: str | None  # site_type of departure-facing device
