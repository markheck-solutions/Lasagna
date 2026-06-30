"""Transport graph ordering for combined route sorting."""

from __future__ import annotations

from typing import Any

from lasagna.route_sorting.combined_results_models import (
    TRUSTED_TRANSPORT_DEVICE_PROOF_SOURCE,
    DeviceTransportRole,
    DpEndpointRole,
    PathCostMap,
    SiteGraph,
    StructuredRouteContractError,
    StructuredRouteEdge,
    TransportDeviceAdjacency,
    _text,
)
from lasagna.route_sorting.combined_results_records import (
    _dp_role_key,
    _service_transport_adjacencies,
)
from lasagna.route_sorting.route_rows import InCARow


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
    *,
    preserve_direction: bool = False,
) -> None:
    if not required_sites <= set(path):
        return
    path_key = tuple(path) if preserve_direction else _normalize_site_path(path)
    path_costs[path_key] = min(cost, path_costs.get(path_key, cost))


def _covering_path_costs_between(
    graph: SiteGraph,
    required_sites: set[str],
    start_site: str,
    end_site: str,
    *,
    preserve_direction: bool = False,
) -> PathCostMap:
    path_costs: PathCostMap = {}
    stack: list[tuple[str, list[str], int]] = [(start_site, [start_site], 0)]
    max_depth = len(graph)
    while stack:
        site, path, cost = stack.pop()
        if site == end_site:
            _record_covering_path(
                path_costs,
                required_sites,
                path,
                cost,
                preserve_direction=preserve_direction,
            )
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
    start_site: str = "",
    end_site: str = "",
) -> list[str]:
    graph_sites = set(graph)
    if missing_sites := sorted(required_sites - graph_sites):
        raise StructuredRouteContractError(
            f"transport adjacency path not proven for row site(s): {', '.join(missing_sites)}"
        )

    if len(required_sites) <= 1:
        return sorted(required_sites)

    if start_site and end_site and start_site in graph and end_site in graph:
        directed_path = _unique_minimum_cost_path(
            _covering_path_costs_between(
                graph,
                required_sites,
                start_site,
                end_site,
                preserve_direction=True,
            )
        )
        if directed_path:
            return directed_path

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

    path = _find_unique_path_covering_sites(
        graph, required_sites, bearer.a_site_code, bearer.b_site_code
    )
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
