"""Sort combined Snowflake QID/ROW_DATA exports into workbook service results."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lasagna.domain.route_models import ServiceRouteResult
from lasagna.route_sorting.adapter import build_site_location_lookup, route_rows_from_inca
from lasagna.route_sorting.combined_parser import read_snowflake_combined_csv
from lasagna.route_sorting.contract import ROUTE_ORDER_AUTHORITY
from lasagna.route_sorting.port_display import extract_port_address
from lasagna.route_sorting.route_rows import InCARow

TRUSTED_TRANSPORT_DEVICE_PROOF_SOURCE = "EXACT_DEVICE_PORT_MATCH"
TRUSTED_TRANSPORT_PORT_MATCH_RULES = frozenset(
    {
        "DEVICE_SUBSLOT_EQUALS_CCP_CONNECTION_POINT_NR",
        "T_PORT_TO_CONNECTION_POINT_NR",
        "CONTENT_POSITION_TO_LINE_ENDPOINT",
        "CABLING_POINT_TO_PEER_CABLING_POINT",
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


def _route_order_source(info_lines: list[str]) -> str:
    if f"Route order: {ROUTE_ORDER_AUTHORITY}" in info_lines:
        return ROUTE_ORDER_AUTHORITY
    return "SORT_FAILED"


def _bearer_message(info_lines: list[str]) -> str:
    for line in info_lines:
        if line.startswith("Bearer: "):
            return line.removeprefix("Bearer: ").strip()
    return ""


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


def _is_planned_status(value: str | None) -> bool:
    return bool(value and value.strip().lower() == "planned")


def _is_planned_disconnect(rows: list[InCARow]) -> bool:
    return (
        bool(rows)
        and all(_is_planned_status(row.status_t_time) for row in rows)
        and not any(_is_planned_status(row.status_o_time) for row in rows)
    )


def _is_mixed_migration(rows: list[InCARow]) -> bool:
    classes = {row.classification for row in rows}
    return "DECOMMISSION" in classes and bool(classes & {"NEW", "LIVE"})


def _mixed_migration_sections(rows: list[InCARow]) -> tuple[list[InCARow], list[InCARow]]:
    current_route = [row for row in rows if row.classification in {"DECOMMISSION", "LIVE"}]
    migration_route = [row for row in rows if row.classification in {"NEW", "LIVE"}]
    return current_route, migration_route


def _edge_from_record(record: dict[str, Any]) -> StructuredRouteEdge:
    route_path = _text(record.get("ROUTE_PATH")) or _text(record.get("EDGE_NAME"))
    edge_sequence = _int_text(record.get("EDGE_SEQUENCE"))
    a_site_code = _text(record.get("A_SITE_CODE"))
    b_site_code = _text(record.get("B_SITE_CODE"))
    if not route_path or edge_sequence is None or not a_site_code or not b_site_code:
        raise StructuredRouteContractError(
            "missing route_path, edge_sequence, or endpoint site in ROUTE_ORDER_METADATA"
        )
    return StructuredRouteEdge(
        route_path=route_path,
        edge_sequence=edge_sequence,
        a_site_code=a_site_code,
        b_site_code=b_site_code,
        a_site_location_id=_text(record.get("A_SITE_LOCATION_ID")),
        b_site_location_id=_text(record.get("B_SITE_LOCATION_ID")),
        a_site_side=_text(record.get("A_SITE_SIDE")),
        b_site_side=_text(record.get("B_SITE_SIDE")),
        media=_text(record.get("MEDIA")),
    )


def _service_contract_edges(
    route_order_metadata: list[dict[str, Any]] | None,
    service_id: str,
    transport_device_adjacency: list[dict[str, Any]] | None = None,
) -> list[StructuredRouteEdge]:
    service_key = service_id.strip().upper()
    records = [
        record
        for record in route_order_metadata or []
        if _text(record.get("SERVICE_ID")).upper() == service_key
    ]
    if not records:
        raise StructuredRouteContractError(f"{ROUTE_ORDER_AUTHORITY} missing for {service_id}")

    edges = _enrich_edges_with_transport_endpoint_proof(
        [_edge_from_record(record) for record in records],
        _service_transport_adjacencies(transport_device_adjacency, service_id),
    )
    duplicate_paths = _duplicates(edge.route_path for edge in edges)
    duplicate_sequences = _duplicates(str(edge.edge_sequence) for edge in edges)
    if duplicate_paths:
        raise StructuredRouteContractError(
            f"duplicate edge fact route_path(s): {', '.join(duplicate_paths)}"
        )
    if duplicate_sequences:
        raise StructuredRouteContractError(
            f"duplicate edge_sequence fact(s): {', '.join(duplicate_sequences)}"
        )

    missing_node_facts = [edge.route_path for edge in edges if not edge.media]
    if missing_node_facts:
        raise StructuredRouteContractError(
            f"missing node/media fact(s): {', '.join(missing_node_facts)}"
        )
    return sorted(edges, key=lambda edge: edge.edge_sequence)


def _transport_edge_by_name(
    transport_edges: list[TransportDeviceAdjacency],
) -> dict[str, TransportDeviceAdjacency]:
    edges_by_name: dict[str, TransportDeviceAdjacency] = {}
    conflicts: list[str] = []
    for edge in transport_edges:
        name = edge.edge_name.upper()
        if name not in edges_by_name:
            edges_by_name[name] = edge
            continue
        conflicts.append(edge.edge_name)
    if conflicts:
        raise StructuredRouteContractError(
            "duplicate/conflicting TRANSPORT_DEVICE_ADJACENCY endpoint facts for edge(s): "
            + ", ".join(sorted(set(conflicts)))
        )
    return edges_by_name


def _enrich_edges_with_transport_endpoint_proof(
    edges: list[StructuredRouteEdge],
    transport_edges: list[TransportDeviceAdjacency],
) -> list[StructuredRouteEdge]:
    transport_by_name = _transport_edge_by_name(transport_edges)
    enriched_edges: list[StructuredRouteEdge] = []
    for edge in edges:
        transport_edge = transport_by_name.get(edge.route_path.upper())
        if edge.edge_sequence != 1 or transport_edge is None:
            enriched_edges.append(edge)
            continue
        enriched_edges.append(
            StructuredRouteEdge(
                route_path=edge.route_path,
                edge_sequence=edge.edge_sequence,
                a_site_code=transport_edge.endpoint_1_site_code,
                b_site_code=transport_edge.endpoint_2_site_code,
                a_site_location_id=edge.a_site_location_id,
                b_site_location_id=edge.b_site_location_id,
                a_site_side=edge.a_site_side,
                b_site_side=edge.b_site_side,
                media=edge.media,
                endpoint_source="TRANSPORT_DEVICE_ADJACENCY",
            )
        )
    return enriched_edges


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _edge_position_path(record: dict[str, Any]) -> tuple[tuple[int, int], ...]:
    path_text = _text(record.get("EDGE_POSITION_PATH"))
    if path_text:
        path: list[tuple[int, int]] = []
        for segment in path_text.split(">"):
            position_text, separator, position_id_text = segment.partition(":")
            if not separator:
                return ()
            position = _int_text(position_text)
            position_id = _int_text(position_id_text)
            if position is None or position_id is None:
                return ()
            path.append((position, position_id))
        return tuple(path)

    edge_position = _int_text(record.get("EDGE_POSITION"))
    edge_position_id = _int_text(record.get("EDGE_POSITION_ID"))
    if edge_position is None or edge_position_id is None:
        return ()
    return ((edge_position, edge_position_id),)


def _transport_adjacency_from_record(record: dict[str, Any]) -> TransportDeviceAdjacency:
    edge_name = _text(record.get("EDGE_NAME"))
    endpoint_1_site_code = _text(record.get("ENDPOINT_1_SITE_CODE"))
    endpoint_2_site_code = _text(record.get("ENDPOINT_2_SITE_CODE"))
    endpoint_proof_source = _text(record.get("ENDPOINT_PROOF_SOURCE"))
    port_match_rule = _text(record.get("PORT_MATCH_RULE"))
    platform_family = _text(record.get("PLATFORM_FAMILY"))
    if not edge_name or not endpoint_1_site_code or not endpoint_2_site_code:
        raise StructuredRouteContractError(
            "missing edge_name or endpoint site in TRANSPORT_DEVICE_ADJACENCY"
        )
    if endpoint_proof_source != TRUSTED_TRANSPORT_DEVICE_PROOF_SOURCE:
        raise StructuredRouteContractError(
            "untrusted TRANSPORT_DEVICE_ADJACENCY proof source for "
            f"{edge_name}: {endpoint_proof_source or '<blank>'}"
        )
    if port_match_rule not in TRUSTED_TRANSPORT_PORT_MATCH_RULES:
        raise StructuredRouteContractError(
            "untrusted TRANSPORT_DEVICE_ADJACENCY port match rule for "
            f"{edge_name}: {port_match_rule or '<blank>'}"
        )
    endpoint_1_ne = _text(record.get("ENDPOINT_1_NE"))
    endpoint_1_ne_part = _text(record.get("ENDPOINT_1_NE_PART"))
    endpoint_1_connection_point_nr = _text(record.get("ENDPOINT_1_CONNECTION_POINT_NR"))
    endpoint_1_slot = _text(record.get("ENDPOINT_1_SLOT"))
    endpoint_1_subslot = _text(record.get("ENDPOINT_1_SUBSLOT"))
    endpoint_1_device_connection_point_nr = _text(
        record.get("ENDPOINT_1_DEVICE_CONNECTION_POINT_NR")
    )
    endpoint_1_device_slot = _text(record.get("ENDPOINT_1_DEVICE_SLOT")) or endpoint_1_slot
    endpoint_1_device_subslot = (
        _text(record.get("ENDPOINT_1_DEVICE_SUBSLOT")) or endpoint_1_connection_point_nr
    )
    endpoint_1_ccp_connection_point_nr = (
        _text(record.get("ENDPOINT_1_CCP_CONNECTION_POINT_NR")) or endpoint_1_connection_point_nr
    )
    endpoint_1_ccp_slot = _text(record.get("ENDPOINT_1_CCP_SLOT")) or endpoint_1_slot
    endpoint_1_ccp_subslot = _text(record.get("ENDPOINT_1_CCP_SUBSLOT")) or endpoint_1_subslot
    endpoint_2_ne = _text(record.get("ENDPOINT_2_NE"))
    endpoint_2_ne_part = _text(record.get("ENDPOINT_2_NE_PART"))
    endpoint_2_connection_point_nr = _text(record.get("ENDPOINT_2_CONNECTION_POINT_NR"))
    endpoint_2_slot = _text(record.get("ENDPOINT_2_SLOT"))
    endpoint_2_subslot = _text(record.get("ENDPOINT_2_SUBSLOT"))
    endpoint_2_device_connection_point_nr = _text(
        record.get("ENDPOINT_2_DEVICE_CONNECTION_POINT_NR")
    )
    endpoint_2_device_slot = _text(record.get("ENDPOINT_2_DEVICE_SLOT")) or endpoint_2_slot
    endpoint_2_device_subslot = (
        _text(record.get("ENDPOINT_2_DEVICE_SUBSLOT")) or endpoint_2_connection_point_nr
    )
    endpoint_2_ccp_connection_point_nr = (
        _text(record.get("ENDPOINT_2_CCP_CONNECTION_POINT_NR")) or endpoint_2_connection_point_nr
    )
    endpoint_2_ccp_slot = _text(record.get("ENDPOINT_2_CCP_SLOT")) or endpoint_2_slot
    endpoint_2_ccp_subslot = _text(record.get("ENDPOINT_2_CCP_SUBSLOT")) or endpoint_2_subslot
    required_port_fields = (
        endpoint_1_ne,
        endpoint_1_ne_part,
        endpoint_1_device_slot,
        endpoint_1_device_subslot,
        endpoint_1_ccp_connection_point_nr,
        endpoint_1_ccp_slot,
        endpoint_2_ne,
        endpoint_2_ne_part,
        endpoint_2_device_slot,
        endpoint_2_device_subslot,
        endpoint_2_ccp_connection_point_nr,
        endpoint_2_ccp_slot,
    )
    if not all(required_port_fields):
        raise StructuredRouteContractError(
            f"endpoint port proof missing in TRANSPORT_DEVICE_ADJACENCY for {edge_name}"
        )
    return TransportDeviceAdjacency(
        edge_name=edge_name,
        endpoint_1_site_code=endpoint_1_site_code,
        endpoint_2_site_code=endpoint_2_site_code,
        path_text=_text(record.get("PATH_TEXT")),
        endpoint_proof_source=endpoint_proof_source,
        port_match_rule=port_match_rule,
        platform_family=platform_family,
        edge_position_path=_edge_position_path(record),
        endpoint_1_ne=endpoint_1_ne,
        endpoint_1_ne_part=endpoint_1_ne_part,
        endpoint_1_connection_point_nr=endpoint_1_connection_point_nr,
        endpoint_1_slot=endpoint_1_slot,
        endpoint_1_subslot=endpoint_1_subslot,
        endpoint_1_device_connection_point_nr=endpoint_1_device_connection_point_nr,
        endpoint_1_device_slot=endpoint_1_device_slot,
        endpoint_1_device_subslot=endpoint_1_device_subslot,
        endpoint_1_ccp_connection_point_nr=endpoint_1_ccp_connection_point_nr,
        endpoint_1_ccp_slot=endpoint_1_ccp_slot,
        endpoint_1_ccp_subslot=endpoint_1_ccp_subslot,
        endpoint_2_ne=endpoint_2_ne,
        endpoint_2_ne_part=endpoint_2_ne_part,
        endpoint_2_connection_point_nr=endpoint_2_connection_point_nr,
        endpoint_2_slot=endpoint_2_slot,
        endpoint_2_subslot=endpoint_2_subslot,
        endpoint_2_device_connection_point_nr=endpoint_2_device_connection_point_nr,
        endpoint_2_device_slot=endpoint_2_device_slot,
        endpoint_2_device_subslot=endpoint_2_device_subslot,
        endpoint_2_ccp_connection_point_nr=endpoint_2_ccp_connection_point_nr,
        endpoint_2_ccp_slot=endpoint_2_ccp_slot,
        endpoint_2_ccp_subslot=endpoint_2_ccp_subslot,
    )


def _service_transport_adjacencies(
    transport_device_adjacency: list[dict[str, Any]] | None,
    service_id: str,
) -> list[TransportDeviceAdjacency]:
    service_key = service_id.strip().upper()
    return [
        _transport_adjacency_from_record(record)
        for record in transport_device_adjacency or []
        if _text(record.get("SERVICE_ID")).upper() == service_key
    ]


def _dp_endpoint_role_from_record(record: dict[str, Any]) -> DpEndpointRole:
    matched_route_path = _text(record.get("MATCHED_ROUTE_PATH"))
    matched_site_side = _text(record.get("MATCHED_SITE_SIDE")).upper()
    if not matched_route_path or matched_site_side not in {"A", "B"}:
        raise StructuredRouteContractError("missing matched route path or side in DP_ENDPOINT_ROLE")
    return DpEndpointRole(
        dp_route_path=_text(record.get("DP_ROUTE_PATH")),
        site_code=_text(record.get("SITE_CODE")),
        site_type=_text(record.get("SITE_TYPE")),
        site_type_no=_text(record.get("SITE_TYPE_NO")),
        pos=_int_text(record.get("POS")) or 0,
        cabling_points=_text(record.get("CABLING_POINTS")),
        conn_type=_text(record.get("CONN_TYPE")),
        matched_route_path=matched_route_path,
        matched_site_side=matched_site_side,
        endpoint_proof_source=_text(record.get("ENDPOINT_PROOF_SOURCE")),
    )


def _service_dp_endpoint_roles(
    dp_endpoint_roles: list[dict[str, Any]] | None,
    service_id: str,
) -> list[DpEndpointRole]:
    service_key = service_id.strip().upper()
    return [
        _dp_endpoint_role_from_record(record)
        for record in dp_endpoint_roles or []
        if _text(record.get("SERVICE_ID")).upper() == service_key
    ]


def _dp_role_key_from_values(
    route_path: str,
    site_code: str,
    site_type: str,
    site_type_no: str,
    pos: int,
    cabling_points: str,
    conn_type: str,
) -> tuple[object, ...]:
    return (
        route_path,
        site_code,
        site_type,
        site_type_no,
        pos,
        cabling_points,
        conn_type,
    )


def _dp_role_key(row: InCARow) -> tuple[object, ...]:
    return _dp_role_key_from_values(
        row.route_path,
        row.site_code,
        row.site_type,
        row.site_type_no,
        row.pos,
        row.cabling_points,
        row.conn_type,
    )


def _dp_roles_by_key(roles: list[DpEndpointRole]) -> dict[tuple[object, ...], DpEndpointRole]:
    roles_by_key: dict[tuple[object, ...], DpEndpointRole] = {}
    duplicate_keys: list[str] = []
    for role in roles:
        key = _dp_role_key_from_values(
            role.dp_route_path,
            role.site_code,
            role.site_type,
            role.site_type_no,
            role.pos,
            role.cabling_points,
            role.conn_type,
        )
        if key in roles_by_key:
            duplicate_keys.append(f"{role.dp_route_path} at {role.site_code} pos {role.pos}")
            continue
        roles_by_key[key] = role
    if duplicate_keys:
        raise StructuredRouteContractError(
            f"duplicate DP endpoint role fact(s): {', '.join(duplicate_keys)}"
        )
    return roles_by_key


def _row_route_paths(rows: list[InCARow]) -> list[str]:
    route_paths: list[str] = []
    for row in rows:
        route_path = row.route_path.strip()
        if route_path and route_path not in route_paths:
            route_paths.append(route_path)
    return route_paths


def _device_identity(row: InCARow) -> str:
    return _text((row.ne_info or "").split(" -", 1)[0])


def _port_token(value: object) -> str:
    return _text(value).rstrip(".").upper()


def _device_port_identity(row: InCARow) -> tuple[str, str, str]:
    return (_device_identity(row), _port_token(row.slot), _port_token(row.subslot))


def _device_proof_label(row: InCARow) -> str:
    identity, slot, subslot = _device_port_identity(row)
    return f"{identity} slot={slot or '<blank>'} subslot={subslot or '<blank>'}"


def _same_site_handoff_sites(rows: list[InCARow]) -> set[str]:
    identities_by_site: dict[str, set[tuple[str, str, str]]] = {}
    for row in rows:
        if row.is_device_row and not row.is_demarcation and row.site_code:
            identities_by_site.setdefault(row.site_code, set()).add(_device_port_identity(row))
    return {
        site
        for site, identities in identities_by_site.items()
        if len({identity for identity in identities if identity[0]}) > 1
    }


def _has_same_site_device_handoff(rows: list[InCARow]) -> bool:
    return bool(_same_site_handoff_sites(rows))


def _device_endpoint_matches(
    row: InCARow,
    *,
    site_code: str,
    ne_name: str,
    ne_part: str,
    endpoint_device_slot: str,
    endpoint_device_subslot: str,
) -> bool:
    endpoint_identity = _text(f"{ne_name} {ne_part}")
    return bool(
        row.site_code == site_code
        and endpoint_identity
        and _device_identity(row) == endpoint_identity
        and _port_token(row.slot)
        and _port_token(row.subslot)
        and _port_token(endpoint_device_slot)
        and _port_token(endpoint_device_subslot)
        and _port_token(row.slot) == _port_token(endpoint_device_slot)
        and _port_token(row.subslot) == _port_token(endpoint_device_subslot)
    )


def _transport_endpoint_side(row: InCARow, edge: TransportDeviceAdjacency) -> str | None:
    matches: list[str] = []
    if _device_endpoint_matches(
        row,
        site_code=edge.endpoint_1_site_code,
        ne_name=edge.endpoint_1_ne,
        ne_part=edge.endpoint_1_ne_part,
        endpoint_device_slot=edge.endpoint_1_device_slot,
        endpoint_device_subslot=edge.endpoint_1_device_subslot,
    ):
        matches.append("A")
    if _device_endpoint_matches(
        row,
        site_code=edge.endpoint_2_site_code,
        ne_name=edge.endpoint_2_ne,
        ne_part=edge.endpoint_2_ne_part,
        endpoint_device_slot=edge.endpoint_2_device_slot,
        endpoint_device_subslot=edge.endpoint_2_device_subslot,
    ):
        matches.append("B")
    if len(matches) > 1:
        raise StructuredRouteContractError(
            f"device transport endpoint side not uniquely proven for {_device_proof_label(row)}"
        )
    return matches[0] if matches else None


def _edge_side(row: InCARow, edge: StructuredRouteEdge) -> str:
    side = (row.site_side or "").strip().upper()
    if not row.is_device_row:
        if side not in {"A", "B"}:
            raise StructuredRouteContractError(
                f"missing SITE_SIDE for passive row {row.route_path} at {row.site_code}"
            )
        if side == "A" and row.site_code != edge.a_site_code:
            raise StructuredRouteContractError(
                f"SITE_SIDE A conflicts with A_SITE_CODE for {row.route_path} at {row.site_code}"
            )
        if side == "B" and row.site_code != edge.b_site_code:
            raise StructuredRouteContractError(
                f"SITE_SIDE B conflicts with B_SITE_CODE for {row.route_path} at {row.site_code}"
            )
        return side

    if row.site_code == edge.a_site_code and row.site_code != edge.b_site_code:
        return "A"
    if row.site_code == edge.b_site_code and row.site_code != edge.a_site_code:
        return "B"
    if row.site_code == edge.a_site_code == edge.b_site_code:
        raise StructuredRouteContractError(
            f"device row on collapsed edge lacks explicit side proof: {row.route_path} at {row.site_code}"
        )
    raise StructuredRouteContractError(
        f"row site {row.site_code} is not an endpoint for {row.route_path}"
    )


def _device_role_from_transport_edge(
    row: InCARow,
    transport_edge: TransportDeviceAdjacency,
    site_order: dict[str, int],
) -> DeviceTransportRole | None:
    endpoint_side = _transport_endpoint_side(row, transport_edge)
    if endpoint_side is None:
        return None
    neighbor_site = (
        transport_edge.endpoint_2_site_code
        if endpoint_side == "A"
        else transport_edge.endpoint_1_site_code
    )
    neighbor_rank = site_order.get(neighbor_site)
    if neighbor_rank is None:
        return None
    return DeviceTransportRole(
        continuity_rank=neighbor_rank,
        endpoint_side_rank=0 if endpoint_side == "A" else 1,
    )


def _device_transport_roles_by_key(
    rows: list[InCARow],
    site_order: dict[str, int] | None,
    transport_edges: list[TransportDeviceAdjacency],
    required_sites: set[str],
) -> dict[tuple[object, ...], DeviceTransportRole]:
    if site_order is None:
        return {}
    candidates_by_key: dict[tuple[object, ...], DeviceTransportRole] = {}
    for row in rows:
        if not row.is_device_row or row.is_demarcation or row.site_code not in required_sites:
            continue
        roles = [
            role
            for transport_edge in transport_edges
            if (role := _device_role_from_transport_edge(row, transport_edge, site_order))
            is not None
        ]
        if len(roles) > 1:
            raise StructuredRouteContractError(
                f"device transport endpoint not uniquely proven for {_device_proof_label(row)}"
            )
        if roles:
            role = roles[0]
            row_key = row.tuple_key()
            candidates_by_key[row_key] = role
    return candidates_by_key


def _ensure_same_site_device_roles(
    rows: list[InCARow],
    device_roles_by_key: dict[tuple[object, ...], DeviceTransportRole],
) -> None:
    same_site_handoff_sites = _same_site_handoff_sites(rows)
    missing_identities = sorted(
        {
            _device_proof_label(row)
            for row in rows
            if row.site_code in same_site_handoff_sites
            and row.is_device_row
            and not row.is_demarcation
            and row.tuple_key() not in device_roles_by_key
        }
    )
    if missing_identities:
        raise StructuredRouteContractError(
            "device transport endpoint not proven by Snowflake contract for: "
            + ", ".join(missing_identities)
        )


def _opposite_side(side: str) -> str:
    return "B" if side == "A" else "A"


def _site_in_edge(site: str, edge: StructuredRouteEdge) -> bool:
    return site in {edge.a_site_code, edge.b_site_code}


def _same_endpoint_pair(left: StructuredRouteEdge, right: StructuredRouteEdge) -> bool:
    return left.a_site_code == right.a_site_code and left.b_site_code == right.b_site_code


def _reversed_endpoint_pair(left: StructuredRouteEdge, right: StructuredRouteEdge) -> bool:
    return left.a_site_code == right.b_site_code and left.b_site_code == right.a_site_code


def _derive_edge_side_order(
    edge: StructuredRouteEdge, previous_edges: list[StructuredRouteEdge]
) -> tuple[str, str]:
    first_edge = previous_edges[0]
    if edge.a_site_code == edge.b_site_code:
        if edge.a_site_code == first_edge.b_site_code:
            return ("B", "A")
        if edge.a_site_code == first_edge.a_site_code:
            return ("A", "B")
        if edge.a_site_side == "A" and edge.b_site_side == "B":
            return ("A", "B")
        raise StructuredRouteContractError(
            f"same-site edge lacks explicit endpoint continuity proof: {edge.route_path}"
        )

    if _same_endpoint_pair(first_edge, edge):
        return ("A", "B")
    if _reversed_endpoint_pair(first_edge, edge):
        return ("B", "A")

    shared_sides = [
        side
        for side, site in (("A", edge.a_site_code), ("B", edge.b_site_code))
        if any(_site_in_edge(site, previous_edge) for previous_edge in previous_edges)
    ]
    if len(shared_sides) != 1:
        raise StructuredRouteContractError(
            f"edge traversal role not uniquely proven for {edge.route_path}"
        )
    first_side = shared_sides[0]
    return (first_side, _opposite_side(first_side))


def _edge_side_orders(edges: list[StructuredRouteEdge]) -> dict[str, dict[str, int]]:
    orders: dict[str, dict[str, int]] = {}
    previous_edges: list[StructuredRouteEdge] = []
    for edge in edges:
        side_order = (
            ("A", "B") if not previous_edges else _derive_edge_side_order(edge, previous_edges)
        )
        orders[edge.route_path] = {side: index for index, side in enumerate(side_order)}
        previous_edges.append(edge)
    return orders


def _edge_side_orders_from_site_path(
    edges: list[StructuredRouteEdge],
    site_order: dict[str, int],
) -> dict[str, dict[str, int]]:
    orders: dict[str, dict[str, int]] = {}
    previous_edges: list[StructuredRouteEdge] = []
    for edge in edges:
        a_rank = site_order.get(edge.a_site_code)
        b_rank = site_order.get(edge.b_site_code)
        if a_rank is not None and b_rank is not None and a_rank != b_rank:
            side_order = ("A", "B") if a_rank < b_rank else ("B", "A")
        else:
            side_order = (
                ("A", "B") if not previous_edges else _derive_edge_side_order(edge, previous_edges)
            )
        orders[edge.route_path] = {side: index for index, side in enumerate(side_order)}
        previous_edges.append(edge)
    return orders


def _contract_sequence(
    row: InCARow,
    edge: StructuredRouteEdge,
    last_edge_sequence: int,
    dp_role: DpEndpointRole | None = None,
) -> int:
    if not row.is_device_row and dp_role is None:
        return edge.edge_sequence
    side = _matched_site_side(row, edge, dp_role)
    side_rank = 0 if side == "A" else 1
    if edge.edge_sequence == 1 and side_rank == 1:
        return last_edge_sequence + 1
    return edge.edge_sequence


def _matched_site_side(
    row: InCARow,
    edge: StructuredRouteEdge,
    dp_role: DpEndpointRole | None = None,
) -> str:
    if dp_role is not None:
        return dp_role.matched_site_side
    return _edge_side(row, edge)


def _direction_rank(row: InCARow) -> int:
    return {"RX": 0, "TX": 1}.get(_text(row.direction).upper(), 2)


def _contract_sort_key(
    row: InCARow,
    edge: StructuredRouteEdge,
    last_edge_sequence: int,
    edge_side_orders: dict[str, dict[str, int]],
    dp_role: DpEndpointRole | None = None,
) -> tuple[object, ...]:
    side = _matched_site_side(row, edge, dp_role)
    side_rank = edge_side_orders[edge.route_path][side]
    row_type_rank = 0 if (row.is_device_row or row.is_demarcation) and side_rank == 0 else 1
    return (
        _contract_sequence(row, edge, last_edge_sequence, dp_role),
        side_rank,
        row_type_rank,
        row.pos,
        _direction_rank(row),
        row.site_type_no,
        row.conn_type,
    )


def _transport_graph_sort_key(
    row: InCARow,
    edge: StructuredRouteEdge,
    edge_side_orders: dict[str, dict[str, int]],
    site_order: dict[str, int],
    dp_role: DpEndpointRole | None = None,
    device_role: DeviceTransportRole | None = None,
) -> tuple[object, ...]:
    site_rank = site_order.get(row.site_code)
    if site_rank is None:
        if dp_role is None or edge.edge_sequence != 1:
            raise StructuredRouteContractError(
                f"transport adjacency path not proven for row site(s): {row.site_code}"
            )
        site_rank = -1 if dp_role.matched_site_side == "A" else len(site_order)
    try:
        side = _matched_site_side(row, edge, dp_role)
        side_rank = edge_side_orders[edge.route_path][side]
    except StructuredRouteContractError:
        if not row.is_device_row or edge.edge_sequence != 1:
            raise
        side_rank = 0
    row_type_rank = 0 if row.is_device_row or row.is_demarcation else 1
    local_sequence_rank = 0 if row.is_device_row or row.is_demarcation else edge.edge_sequence
    continuity_rank = device_role.continuity_rank if device_role is not None else site_rank
    endpoint_side_rank = device_role.endpoint_side_rank if device_role is not None else 1
    return (
        site_rank,
        local_sequence_rank,
        continuity_rank,
        endpoint_side_rank,
        side_rank,
        row_type_rank,
        row.pos,
        _direction_rank(row),
        row.site_type_no,
        row.conn_type,
    )


def _row_sort_key(
    row: InCARow,
    edge: StructuredRouteEdge,
    last_edge_sequence: int,
    edge_side_orders: dict[str, dict[str, int]],
    site_order: dict[str, int] | None,
    dp_role: DpEndpointRole | None = None,
    device_role: DeviceTransportRole | None = None,
) -> tuple[object, ...]:
    if site_order is not None:
        return _transport_graph_sort_key(
            row, edge, edge_side_orders, site_order, dp_role, device_role
        )
    return _contract_sort_key(row, edge, last_edge_sequence, edge_side_orders, dp_role)


def _matched_route_path(
    row: InCARow,
    dp_role: DpEndpointRole | None = None,
) -> str:
    if dp_role is not None:
        return dp_role.matched_route_path
    return row.route_path


def _ensure_unique_contract_keys(
    rows: list[InCARow],
    edges_by_route: dict[str, StructuredRouteEdge],
    edge_side_orders: dict[str, dict[str, int]],
    site_order: dict[str, int] | None = None,
    dp_roles_by_key: dict[tuple[object, ...], DpEndpointRole] | None = None,
    device_roles_by_key: dict[tuple[object, ...], DeviceTransportRole] | None = None,
) -> None:
    keyed_rows: dict[tuple[object, ...], InCARow] = {}
    duplicates: list[str] = []
    last_edge_sequence = max(edge.edge_sequence for edge in edges_by_route.values())
    for row in rows:
        dp_role = (dp_roles_by_key or {}).get(_dp_role_key(row)) if row.is_demarcation else None
        device_role = (device_roles_by_key or {}).get(row.tuple_key())
        route_path = _matched_route_path(row, dp_role)
        edge = edges_by_route[route_path]
        key = _row_sort_key(
            row,
            edge,
            last_edge_sequence,
            edge_side_orders,
            site_order,
            dp_role,
            device_role,
        )
        existing = keyed_rows.get(key)
        if existing is not None and existing.tuple_key() != row.tuple_key():
            duplicates.append(f"{row.route_path} at {row.site_code} key={key}")
        keyed_rows[key] = row
    if duplicates:
        raise StructuredRouteContractError(
            f"duplicate unsequenced row fact(s): {', '.join(duplicates[:10])}"
        )


def _find_unique_transport_path(
    graph: SiteGraph,
    start_site: str,
    end_site: str,
) -> list[str]:
    if start_site not in graph or end_site not in graph:
        return []

    paths: list[list[str]] = []
    stack: list[tuple[str, list[str]]] = [(start_site, [start_site])]
    max_depth = len(graph)
    while stack:
        site, path = stack.pop()
        if site == end_site:
            paths.append(path)
            if len(paths) > 1:
                return []
            continue
        if len(path) > max_depth:
            continue
        for next_site in sorted(graph[site], reverse=True):
            if next_site in path:
                continue
            stack.append((next_site, [*path, next_site]))
    return paths[0] if len(paths) == 1 else []


def _normalize_site_path(path: list[str]) -> tuple[str, ...]:
    reversed_path = list(reversed(path))
    return tuple(path if tuple(path) <= tuple(reversed_path) else reversed_path)


def _record_covering_path(
    path_costs: PathCostMap,
    required_sites: set[str],
    path: list[str],
    cost: int,
) -> None:
    if not required_sites <= set(path):
        return
    path_key = _normalize_site_path(path)
    path_costs[path_key] = min(cost, path_costs.get(path_key, cost))


def _covering_path_costs_between(
    graph: SiteGraph,
    required_sites: set[str],
    start_site: str,
    end_site: str,
) -> PathCostMap:
    path_costs: PathCostMap = {}
    stack: list[tuple[str, list[str], int]] = [(start_site, [start_site], 0)]
    max_depth = len(graph)
    while stack:
        site, path, cost = stack.pop()
        if site == end_site:
            _record_covering_path(path_costs, required_sites, path, cost)
            continue
        if len(path) > max_depth:
            continue
        for next_site, edge_cost in sorted(graph[site].items(), reverse=True):
            if next_site not in path:
                stack.append((next_site, [*path, next_site], cost + edge_cost))
    return path_costs


def _merge_path_costs(path_costs: PathCostMap, additional_costs: PathCostMap) -> None:
    for path, cost in additional_costs.items():
        path_costs[path] = min(cost, path_costs.get(path, cost))


def _unique_minimum_cost_path(path_costs: PathCostMap) -> list[str]:
    if not path_costs:
        return []
    minimum_cost = min(path_costs.values())
    best_paths = [list(path) for path, cost in path_costs.items() if cost == minimum_cost]
    return best_paths[0] if len(best_paths) == 1 else []


def _find_unique_path_covering_sites(
    graph: SiteGraph,
    required_sites: set[str],
) -> list[str]:
    if len(required_sites) <= 1:
        return sorted(required_sites)

    graph_sites = set(graph)
    if missing_sites := sorted(required_sites - graph_sites):
        raise StructuredRouteContractError(
            f"transport adjacency path not proven for row site(s): {', '.join(missing_sites)}"
        )

    path_costs: PathCostMap = {}
    ordered_sites = sorted(required_sites)
    for index, start_site in enumerate(ordered_sites):
        for end_site in ordered_sites[index + 1 :]:
            _merge_path_costs(
                path_costs,
                _covering_path_costs_between(graph, required_sites, start_site, end_site),
            )
    return _unique_minimum_cost_path(path_costs)


def _add_site_graph_edge(
    graph: SiteGraph,
    a_site: str,
    b_site: str,
    proof_cost: int = 0,
) -> None:
    if not a_site or not b_site or a_site == b_site:
        return
    graph.setdefault(a_site, {})
    graph.setdefault(b_site, {})
    graph[a_site][b_site] = min(proof_cost, graph[a_site].get(b_site, proof_cost))
    graph[b_site][a_site] = min(proof_cost, graph[b_site].get(a_site, proof_cost))


def _transport_adjacency_proof_cost(transport_edge: TransportDeviceAdjacency) -> int:
    if transport_edge.endpoint_proof_source != TRUSTED_TRANSPORT_DEVICE_PROOF_SOURCE:
        raise StructuredRouteContractError(
            "untrusted TRANSPORT_DEVICE_ADJACENCY proof source for "
            f"{transport_edge.edge_name}: {transport_edge.endpoint_proof_source or '<blank>'}"
        )
    return 0


def _has_bearer_transport_adjacency(
    transport_edges: list[TransportDeviceAdjacency],
    bearer: StructuredRouteEdge,
) -> bool:
    bearer_name = bearer.route_path.upper()
    bearer_sites = {bearer.a_site_code, bearer.b_site_code}
    return any(
        transport_edge.edge_name.upper() == bearer_name
        and {
            transport_edge.endpoint_1_site_code,
            transport_edge.endpoint_2_site_code,
        }
        == bearer_sites
        for transport_edge in transport_edges
    )


def _transport_site_order(
    edges: list[StructuredRouteEdge],
    transport_device_adjacency: list[dict[str, Any]] | None,
    service_id: str,
    required_sites: set[str],
) -> dict[str, int] | None:
    transport_edges = _service_transport_adjacencies(transport_device_adjacency, service_id)
    if not transport_edges:
        return None

    bearer = edges[0]
    bearer_name = bearer.route_path.upper()
    graph: SiteGraph = {}
    for edge in edges[1:]:
        _add_site_graph_edge(graph, edge.a_site_code, edge.b_site_code)
    for transport_edge in transport_edges:
        if transport_edge.edge_name.upper() == bearer_name:
            continue
        _add_site_graph_edge(
            graph,
            transport_edge.endpoint_1_site_code,
            transport_edge.endpoint_2_site_code,
            _transport_adjacency_proof_cost(transport_edge),
        )

    missing_required_sites = required_sites - set(graph)
    bearer_sites = {bearer.a_site_code, bearer.b_site_code}
    if (
        missing_required_sites
        and missing_required_sites <= bearer_sites
        and _has_bearer_transport_adjacency(transport_edges, bearer)
    ):
        _add_site_graph_edge(graph, bearer.a_site_code, bearer.b_site_code)

    path = _find_unique_path_covering_sites(graph, required_sites)
    if not path:
        raise StructuredRouteContractError(
            "transport adjacency path not uniquely proven between "
            f"{bearer.a_site_code} and {bearer.b_site_code}"
        )
    return {site: index for index, site in enumerate(path)}


def _ensure_rows_on_transport_path(rows: list[InCARow], site_order: dict[str, int] | None) -> None:
    if site_order is None:
        return
    missing_sites = sorted(
        {
            row.site_code
            for row in rows
            if not row.is_demarcation and row.site_code not in site_order
        }
    )
    if missing_sites:
        raise StructuredRouteContractError(
            f"transport adjacency path not proven for row site(s): {', '.join(missing_sites)}"
        )


def _sort_rows_by_structured_contract(
    rows: list[InCARow],
    route_order_metadata: list[dict[str, Any]] | None,
    service_id: str,
    transport_device_adjacency: list[dict[str, Any]] | None = None,
    dp_endpoint_roles: list[dict[str, Any]] | None = None,
    *,
    allow_decommission: bool = False,
) -> list[InCARow]:
    has_decommission = any(row.classification == "DECOMMISSION" for row in rows)
    if has_decommission and not allow_decommission and not _is_planned_disconnect(rows):
        raise StructuredRouteContractError(
            "migration route contract not proven by Snowflake structured facts"
        )

    edges = _service_contract_edges(route_order_metadata, service_id, transport_device_adjacency)
    edges_by_route = {edge.route_path: edge for edge in edges}
    transport_edges = _service_transport_adjacencies(transport_device_adjacency, service_id)
    dp_roles_by_key = _dp_roles_by_key(_service_dp_endpoint_roles(dp_endpoint_roles, service_id))
    demarc_rows = [row for row in rows if row.is_demarcation]
    missing_dp_roles = sorted(
        {row.route_path for row in demarc_rows if _dp_role_key(row) not in dp_roles_by_key}
    )
    if missing_dp_roles:
        raise StructuredRouteContractError(
            "DP/SDP endpoint role not proven by Snowflake contract for route_path(s): "
            + ", ".join(missing_dp_roles)
        )

    required_sites = {row.site_code for row in rows if not row.is_demarcation}
    site_order = _transport_site_order(
        edges,
        transport_device_adjacency,
        service_id,
        required_sites,
    )
    if site_order is None and _has_same_site_device_handoff(rows):
        raise StructuredRouteContractError(
            "transport adjacency path not proven for same-site device continuity"
        )
    same_site_handoff_sites = _same_site_handoff_sites(rows)
    device_roles_by_key = _device_transport_roles_by_key(
        rows, site_order, transport_edges, same_site_handoff_sites
    )
    _ensure_same_site_device_roles(rows, device_roles_by_key)
    edge_side_orders = (
        _edge_side_orders_from_site_path(edges, site_order)
        if site_order is not None
        else _edge_side_orders(edges)
    )
    row_route_paths = [
        _matched_route_path(
            row,
            dp_roles_by_key.get(_dp_role_key(row)) if row.is_demarcation else None,
        )
        for row in rows
    ]
    missing_paths = [
        path for path in dict.fromkeys(row_route_paths) if path and path not in edges_by_route
    ]
    if missing_paths:
        raise StructuredRouteContractError(
            f"missing route contract for route_path(s): {', '.join(missing_paths)}"
        )

    _ensure_rows_on_transport_path(rows, site_order)
    _ensure_unique_contract_keys(
        rows,
        edges_by_route,
        edge_side_orders,
        site_order,
        dp_roles_by_key,
        device_roles_by_key,
    )
    last_edge_sequence = max(edge.edge_sequence for edge in edges)
    return sorted(
        rows,
        key=lambda row: _row_sort_key(
            row,
            edges_by_route[
                _matched_route_path(
                    row,
                    dp_roles_by_key.get(_dp_role_key(row)) if row.is_demarcation else None,
                )
            ],
            last_edge_sequence,
            edge_side_orders,
            site_order,
            dp_roles_by_key.get(_dp_role_key(row)) if row.is_demarcation else None,
            device_roles_by_key.get(row.tuple_key()),
        ),
    )


def _row_belongs_to_site_path(
    row: InCARow,
    edges_by_route: dict[str, StructuredRouteEdge],
    path_sites: set[str],
) -> bool:
    if row.site_code not in path_sites:
        return False
    edge = edges_by_route.get(row.route_path)
    if edge is None:
        return False
    if row.is_device_row:
        return True
    return edge.a_site_code in path_sites and edge.b_site_code in path_sites


def _migration_rows_on_proven_new_path(
    rows: list[InCARow],
    route_order_metadata: list[dict[str, Any]] | None,
    service_id: str,
    transport_device_adjacency: list[dict[str, Any]] | None,
) -> list[InCARow]:
    new_rows = [row for row in rows if row.classification == "NEW"]
    live_rows = [row for row in rows if row.classification == "LIVE"]
    if not new_rows or not live_rows:
        return []

    edges = _service_contract_edges(route_order_metadata, service_id, transport_device_adjacency)
    edges_by_route = {edge.route_path: edge for edge in edges}
    bearer_route_path = edges[0].route_path
    anchor_rows = [
        *new_rows,
        *[row for row in live_rows if row.route_path == bearer_route_path],
    ]
    anchor_sites = {row.site_code for row in anchor_rows}
    site_order = _transport_site_order(
        edges,
        transport_device_adjacency,
        service_id,
        anchor_sites,
    )
    if site_order is None:
        return []

    path_sites = set(site_order)
    scoped_live_rows = [
        row for row in live_rows if _row_belongs_to_site_path(row, edges_by_route, path_sites)
    ]
    scoped_rows = [*new_rows, *scoped_live_rows]
    if {row.site_code for row in new_rows} - {row.site_code for row in scoped_rows}:
        return []
    if len(scoped_rows) == len([row for row in rows if row.classification in {"NEW", "LIVE"}]):
        return []
    return scoped_rows


def _sort_service_sections_by_structured_contract(
    rows: list[InCARow],
    route_order_metadata: list[dict[str, Any]] | None,
    service_id: str,
    transport_device_adjacency: list[dict[str, Any]] | None = None,
    dp_endpoint_roles: list[dict[str, Any]] | None = None,
) -> tuple[list[InCARow], list[InCARow]]:
    if not _is_mixed_migration(rows):
        return (
            _sort_rows_by_structured_contract(
                rows,
                route_order_metadata,
                service_id,
                transport_device_adjacency,
                dp_endpoint_roles,
            ),
            [],
        )

    current_route, migration_route = _mixed_migration_sections(rows)
    sorted_current_route = _sort_rows_by_structured_contract(
        current_route,
        route_order_metadata,
        service_id,
        transport_device_adjacency,
        dp_endpoint_roles,
        allow_decommission=True,
    )
    try:
        sorted_migration_route = _sort_rows_by_structured_contract(
            migration_route,
            route_order_metadata,
            service_id,
            transport_device_adjacency,
            dp_endpoint_roles,
        )
    except StructuredRouteContractError:
        new_path_rows = _migration_rows_on_proven_new_path(
            rows,
            route_order_metadata,
            service_id,
            transport_device_adjacency,
        )
        if not new_path_rows:
            raise
        sorted_migration_route = _sort_rows_by_structured_contract(
            new_path_rows,
            route_order_metadata,
            service_id,
            transport_device_adjacency,
            dp_endpoint_roles,
        )
    return sorted_current_route, sorted_migration_route


def _populate_device_display_points(rows: list[InCARow]) -> None:
    for row in rows:
        if row.is_ne_location and row.is_router:
            row.display_points = extract_port_address(row)


def sort_combined_csv_to_service_results(
    combined_csv_path: Path,
    service_ids: list[str],
) -> dict[str, ServiceRouteResult]:
    """Sort Snowflake combined export rows for the requested service IDs."""
    combined_data = read_snowflake_combined_csv(str(combined_csv_path))
    site_locations = build_site_location_lookup(
        combined_data.hub_records,
        combined_data.route_order_metadata,
    )
    results: dict[str, ServiceRouteResult] = {}

    for service_id in service_ids:
        rows = combined_data.services.get(service_id, [])
        if not rows:
            results[service_id] = ServiceRouteResult.no_data(service_id)
            continue
        try:
            sorted_rows, migration_rows = _sort_service_sections_by_structured_contract(
                rows,
                combined_data.route_order_metadata,
                service_id,
                combined_data.transport_device_adjacency,
                combined_data.dp_endpoint_roles,
            )
            _populate_device_display_points(sorted_rows)
            _populate_device_display_points(migration_rows)
        except Exception as exc:
            results[service_id] = ServiceRouteResult.sort_failed(service_id, str(exc))
            continue

        results[service_id] = ServiceRouteResult.ok(
            service_id,
            route_rows_from_inca(sorted_rows, site_locations),
            route_rows_from_inca(migration_rows, site_locations),
            route_order_source=ROUTE_ORDER_AUTHORITY,
            message="",
        )

    return results
