"""Snowflake record conversion for combined route sorting."""

from __future__ import annotations

from typing import Any

from lasagna.route_sorting.combined_results_models import (
    TRUSTED_TRANSPORT_DEVICE_PROOF_SOURCE,
    TRUSTED_TRANSPORT_PORT_MATCH_RULES,
    DpEndpointRole,
    StructuredRouteContractError,
    StructuredRouteEdge,
    TransportDeviceAdjacency,
    _duplicates,
    _int_text,
    _text,
)
from lasagna.route_sorting.contract import ROUTE_ORDER_AUTHORITY
from lasagna.route_sorting.route_rows import InCARow


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
                a_site_code=edge.a_site_code,
                b_site_code=edge.b_site_code,
                a_site_location_id=edge.a_site_location_id,
                b_site_location_id=edge.b_site_location_id,
                a_site_side=edge.a_site_side,
                b_site_side=edge.b_site_side,
                media=edge.media,
                endpoint_source="TRANSPORT_DEVICE_ADJACENCY",
            )
        )
    return enriched_edges


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
