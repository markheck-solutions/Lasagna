"""Shared data contracts for combined route sorting."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

TRUSTED_TRANSPORT_DEVICE_PROOF_SOURCE = "EXACT_DEVICE_PORT_MATCH"
TRUSTED_TRANSPORT_PORT_MATCH_RULES = frozenset(
    {
        "CABLING_POINT_TO_PEER_CABLING_POINT",
    }
)
TRUSTED_DP_ENDPOINT_ROLE_PROOF_SOURCES = frozenset(
    {
        "DP_EXACT_SITE_IDENTITY",
        "DP_SITE_CODE_TRANSPORT_ENDPOINT",
    }
)


class StructuredRouteContractError(ValueError):
    """Raised when Snowflake route facts cannot prove a data-only row order."""


@dataclass(frozen=True)
class StructuredRouteEdge:
    route_path: str
    edge_sequence: int
    a_site_code: str
    b_site_code: str
    a_site_location_id: str
    b_site_location_id: str
    a_site_side: str
    b_site_side: str
    media: str
    endpoint_source: str = "ROUTE_ORDER_METADATA"


@dataclass(frozen=True)
class TransportDeviceAdjacency:
    edge_name: str
    endpoint_1_site_code: str
    endpoint_2_site_code: str
    path_text: str
    endpoint_proof_source: str = ""
    port_match_rule: str = ""
    platform_family: str = ""
    edge_position_path: tuple[tuple[int, int], ...] = ()
    endpoint_1_ne: str = ""
    endpoint_1_ne_part: str = ""
    endpoint_1_connection_point_nr: str = ""
    endpoint_1_slot: str = ""
    endpoint_1_subslot: str = ""
    endpoint_1_device_connection_point_nr: str = ""
    endpoint_1_device_slot: str = ""
    endpoint_1_device_subslot: str = ""
    endpoint_1_ccp_connection_point_nr: str = ""
    endpoint_1_ccp_slot: str = ""
    endpoint_1_ccp_subslot: str = ""
    endpoint_2_ne: str = ""
    endpoint_2_ne_part: str = ""
    endpoint_2_connection_point_nr: str = ""
    endpoint_2_slot: str = ""
    endpoint_2_subslot: str = ""
    endpoint_2_device_connection_point_nr: str = ""
    endpoint_2_device_slot: str = ""
    endpoint_2_device_subslot: str = ""
    endpoint_2_ccp_connection_point_nr: str = ""
    endpoint_2_ccp_slot: str = ""
    endpoint_2_ccp_subslot: str = ""


@dataclass(frozen=True)
class DpEndpointRole:
    dp_route_path: str
    site_code: str
    site_type: str
    site_type_no: str
    pos: int
    cabling_points: str
    conn_type: str
    matched_route_path: str
    matched_site_side: str
    endpoint_proof_source: str


@dataclass(frozen=True)
class DeviceTransportRole:
    continuity_rank: int
    endpoint_side_rank: int


SiteGraph = dict[str, dict[str, int]]
PathCostMap = dict[tuple[str, ...], int]


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _int_text(value: object) -> int | None:
    text = _text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)
