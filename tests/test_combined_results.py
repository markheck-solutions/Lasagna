from pathlib import Path

import pytest

from lasagna.route_sorting.combined_results import (
    StructuredRouteContractError,
    _bearer_message,
    _sort_rows_by_structured_contract,
    _sort_service_sections_by_structured_contract,
)
from lasagna.route_sorting.route_rows import InCARow


def _row(
    site_code: str,
    route_path: str,
    pos: int,
    *,
    site_side: str | None = None,
    ne_info: str | None = None,
    status_o_time: str | None = None,
    status_t_time: str | None = None,
    slot: str = "",
    subslot: str = "",
    direction: str = "",
) -> InCARow:
    row = InCARow(
        site_code=site_code,
        site_type="XS",
        ne_info=ne_info,
        cabling_location=f"[{site_code}]01/R01/RU01/.",
        cabling_points=str(pos),
        conn_type="LC",
        location_alias=None,
        route_path=route_path,
        pos=pos,
        status_o_time=status_o_time,
        o_time=None,
        status_t_time=status_t_time,
        t_time=None,
        comment=None,
    )
    row.site_side = site_side
    row.connection_point_nr = str(pos)
    row.slot = slot
    row.subslot = subslot
    row.direction = direction
    return row


def _metadata(service_id: str, route_path: str, sequence: int = 1) -> dict[str, object]:
    return {
        "SERVICE_ID": service_id,
        "ROUTE_PATH": route_path,
        "EDGE_SEQUENCE": sequence,
        "EDGE_NAME": route_path,
        "A_SITE_CODE": "AAA",
        "B_SITE_CODE": "BBB",
        "A_SITE_LOCATION_ID": "LOC-A",
        "B_SITE_LOCATION_ID": "LOC-B",
        "A_SITE_SIDE": "A",
        "B_SITE_SIDE": "B",
        "MEDIA": "OL",
    }


def _metadata_between(
    service_id: str,
    route_path: str,
    sequence: int,
    a_site_code: str,
    b_site_code: str,
) -> dict[str, object]:
    return _metadata(service_id, route_path, sequence) | {
        "A_SITE_CODE": a_site_code,
        "B_SITE_CODE": b_site_code,
        "A_SITE_LOCATION_ID": f"LOC-{a_site_code}",
        "B_SITE_LOCATION_ID": f"LOC-{b_site_code}",
    }


def _transport_adjacency(
    service_id: str,
    edge_name: str,
    endpoint_1_site_code: str,
    endpoint_2_site_code: str,
    *,
    endpoint_proof_source: str = "EXACT_DEVICE_PORT_MATCH",
    endpoint_1_ne: str = "",
    endpoint_1_ne_part: str = "",
    endpoint_1_connection_point_nr: str = "01",
    endpoint_1_slot: str = "01",
    endpoint_1_subslot: str = "",
    endpoint_1_device_slot: str = "",
    endpoint_1_device_subslot: str = "",
    endpoint_1_ccp_connection_point_nr: str = "",
    endpoint_2_ne: str = "",
    endpoint_2_ne_part: str = "",
    endpoint_2_connection_point_nr: str = "01",
    endpoint_2_slot: str = "01",
    endpoint_2_subslot: str = "",
    endpoint_2_device_slot: str = "",
    endpoint_2_device_subslot: str = "",
    endpoint_2_ccp_connection_point_nr: str = "",
    port_match_rule: str = "DEVICE_SUBSLOT_EQUALS_CCP_CONNECTION_POINT_NR",
    platform_family: str = "",
    edge_position_path: str = "",
) -> dict[str, object]:
    endpoint_1_ne = endpoint_1_ne or f"{endpoint_1_site_code} XS TRANSPORT"
    endpoint_1_ne_part = endpoint_1_ne_part or "01"
    endpoint_1_subslot = endpoint_1_subslot or endpoint_1_connection_point_nr
    endpoint_1_device_slot = endpoint_1_device_slot or endpoint_1_slot
    endpoint_1_device_subslot = endpoint_1_device_subslot or endpoint_1_connection_point_nr
    endpoint_1_ccp_connection_point_nr = (
        endpoint_1_ccp_connection_point_nr or endpoint_1_connection_point_nr
    )
    endpoint_2_ne = endpoint_2_ne or f"{endpoint_2_site_code} XS TRANSPORT"
    endpoint_2_ne_part = endpoint_2_ne_part or "01"
    endpoint_2_subslot = endpoint_2_subslot or endpoint_2_connection_point_nr
    endpoint_2_device_slot = endpoint_2_device_slot or endpoint_2_slot
    endpoint_2_device_subslot = endpoint_2_device_subslot or endpoint_2_connection_point_nr
    endpoint_2_ccp_connection_point_nr = (
        endpoint_2_ccp_connection_point_nr or endpoint_2_connection_point_nr
    )
    return {
        "SERVICE_ID": service_id,
        "EDGE_NAME": edge_name,
        "ENDPOINT_1_SITE_CODE": endpoint_1_site_code,
        "ENDPOINT_2_SITE_CODE": endpoint_2_site_code,
        "PATH_TEXT": edge_name,
        "ENDPOINT_PROOF_SOURCE": endpoint_proof_source,
        "PORT_MATCH_RULE": port_match_rule,
        "PLATFORM_FAMILY": platform_family,
        "EDGE_POSITION_PATH": edge_position_path,
        "ENDPOINT_1_NE": endpoint_1_ne,
        "ENDPOINT_1_NE_PART": endpoint_1_ne_part,
        "ENDPOINT_1_DEVICE_SLOT": endpoint_1_device_slot,
        "ENDPOINT_1_DEVICE_SUBSLOT": endpoint_1_device_subslot,
        "ENDPOINT_1_CCP_CONNECTION_POINT_NR": endpoint_1_ccp_connection_point_nr,
        "ENDPOINT_1_CCP_SLOT": endpoint_1_slot,
        "ENDPOINT_1_CCP_SUBSLOT": endpoint_1_subslot,
        "ENDPOINT_1_CONNECTION_POINT_NR": endpoint_1_connection_point_nr,
        "ENDPOINT_1_SLOT": endpoint_1_slot,
        "ENDPOINT_1_SUBSLOT": endpoint_1_subslot,
        "ENDPOINT_2_NE": endpoint_2_ne,
        "ENDPOINT_2_NE_PART": endpoint_2_ne_part,
        "ENDPOINT_2_DEVICE_SLOT": endpoint_2_device_slot,
        "ENDPOINT_2_DEVICE_SUBSLOT": endpoint_2_device_subslot,
        "ENDPOINT_2_CCP_CONNECTION_POINT_NR": endpoint_2_ccp_connection_point_nr,
        "ENDPOINT_2_CCP_SLOT": endpoint_2_slot,
        "ENDPOINT_2_CCP_SUBSLOT": endpoint_2_subslot,
        "ENDPOINT_2_CONNECTION_POINT_NR": endpoint_2_connection_point_nr,
        "ENDPOINT_2_SLOT": endpoint_2_slot,
        "ENDPOINT_2_SUBSLOT": endpoint_2_subslot,
    }


def _dp_endpoint_role(
    service_id: str,
    dp_route_path: str,
    site_code: str,
    site_type: str,
    site_type_no: str,
    pos: int,
    cabling_points: str,
    conn_type: str,
    matched_route_path: str,
    matched_site_side: str,
    *,
    endpoint_proof_source: str = "DP_EXACT_SITE_IDENTITY",
) -> dict[str, object]:
    return {
        "SERVICE_ID": service_id,
        "DP_ROUTE_PATH": dp_route_path,
        "SITE_CODE": site_code,
        "SITE_TYPE": site_type,
        "SITE_TYPE_NO": site_type_no,
        "POS": pos,
        "CABLING_POINTS": cabling_points,
        "CONN_TYPE": conn_type,
        "MATCHED_ROUTE_PATH": matched_route_path,
        "MATCHED_SITE_SIDE": matched_site_side,
        "ENDPOINT_PROOF_SOURCE": endpoint_proof_source,
    }


def test_bearer_message_keeps_only_bearer_name() -> None:
    assert (
        _bearer_message(
            [
                "Bearer: ASH/2 X 37-MAI X 133 10G01",
                "A-Loc: ASH, B-Loc: FAIR",
                "Route order: ROUTE_ORDER_METADATA",
                "Site order: ASH -> HVB -> FAIR",
            ]
        )
        == "ASH/2 X 37-MAI X 133 10G01"
    )


def test_bearer_message_is_blank_when_no_bearer_line() -> None:
    assert _bearer_message(["Route order: ROUTE_ORDER_METADATA"]) == ""


def test_structured_contract_sorts_by_edge_sequence_and_site_side() -> None:
    service_id = "IC-123456"
    first = "AAA-BBB OL01"
    second = "BBB-CCC OL02"
    metadata = [
        _metadata(service_id, second, 2)
        | {
            "A_SITE_CODE": "BBB",
            "B_SITE_CODE": "CCC",
            "A_SITE_LOCATION_ID": "LOC-B",
            "B_SITE_LOCATION_ID": "LOC-C",
        },
        _metadata(service_id, first, 1),
    ]
    rows = [
        _row("CCC", second, 1, site_side="B"),
        _row("BBB", first, 1, site_side="B"),
        _row("AAA", first, 1, site_side="A"),
        _row("BBB", second, 1, site_side="A"),
    ]

    result = _sort_rows_by_structured_contract(rows, metadata, service_id)

    assert [(row.site_code, row.route_path) for row in result] == [
        ("AAA", first),
        ("BBB", first),
        ("BBB", second),
        ("CCC", second),
    ]


def test_structured_contract_places_l1_b_end_device_after_interior_edges() -> None:
    service_id = "IC-123456"
    bearer = "AAA-BBB 100G01"
    interior = "AAA-BBB OL01"
    metadata = [_metadata(service_id, bearer, 1), _metadata(service_id, interior, 2)]
    rows = [
        _row("BBB", bearer, 1, ne_info="bbb-router NCS-5508 -(0/1/0\\0..:Rx)"),
        _row("AAA", bearer, 1, ne_info="aaa-router NCS-5508 -(0/1/0\\0..:Rx)"),
        _row("BBB", interior, 21, site_side="B"),
        _row("AAA", interior, 21, site_side="A"),
    ]

    result = _sort_rows_by_structured_contract(rows, metadata, service_id)

    assert [(row.site_code, row.route_path) for row in result] == [
        ("AAA", bearer),
        ("AAA", interior),
        ("BBB", interior),
        ("BBB", bearer),
    ]


def test_structured_contract_uses_shared_endpoint_continuity_for_icb_823402() -> None:
    service_id = "ICB-823402"
    bearer = "MEI/IX BR 11-SNG/G BR 1 100G10"
    interior = "MEI U 1-MEI/IX OL05"
    metadata = [
        _metadata(service_id, bearer, 1) | {"A_SITE_CODE": "MEI/IX", "B_SITE_CODE": "SNG/G"},
        _metadata(service_id, interior, 2)
        | {
            "A_SITE_CODE": "MEI",
            "B_SITE_CODE": "MEI/IX",
            "A_SITE_LOCATION_ID": "MRS00001",
            "B_SITE_LOCATION_ID": "MRS00034",
        },
    ]
    rows = [
        _row("MEI", interior, 15, site_side="A"),
        _row("MEI/IX", interior, 15, site_side="B"),
    ]

    result = _sort_rows_by_structured_contract(rows, metadata, service_id)

    assert [(row.site_code, row.site_side) for row in result] == [("MEI/IX", "B"), ("MEI", "A")]


def test_structured_contract_uses_b_end_orientation_for_icb_822771_same_site_loop() -> None:
    service_id = "ICB-822771"
    bearer = "HEX BR 1-NEO/T2 BR 2 400G09"
    hex_rbi = "HEX-RBI U 2 OL01"
    neo_loop = "NEO/T2-NEO/T2 U 1 OL02"
    metadata = [
        _metadata(service_id, bearer, 1) | {"A_SITE_CODE": "HEX", "B_SITE_CODE": "NEO/T2"},
        _metadata(service_id, hex_rbi, 2)
        | {
            "A_SITE_CODE": "HEX",
            "B_SITE_CODE": "RBI",
            "A_SITE_LOCATION_ID": "LON00001",
            "B_SITE_LOCATION_ID": "LON00008",
        },
        _metadata(service_id, neo_loop, 3)
        | {
            "A_SITE_CODE": "NEO/T2",
            "B_SITE_CODE": "NEO/T2",
            "A_SITE_LOCATION_ID": "NWA00001",
            "B_SITE_LOCATION_ID": "NWA00001",
        },
    ]
    rows = [
        _row("NEO/T2", neo_loop, 157, site_side="A"),
        _row("NEO/T2", neo_loop, 157, site_side="B"),
    ]

    result = _sort_rows_by_structured_contract(rows, metadata, service_id)

    assert [(row.site_type, row.site_side) for row in result] == [("XS", "B"), ("XS", "A")]


def test_structured_contract_fails_closed_for_migration_rows() -> None:
    with pytest.raises(StructuredRouteContractError, match="migration route contract not proven"):
        _sort_rows_by_structured_contract(
            [
                _row("AAA", "AAA-BBB OL01", 1, site_side="A", status_t_time="Planned"),
                _row("BBB", "AAA-BBB OL01", 1, site_side="B"),
            ],
            [_metadata("IC-123456", "AAA-BBB OL01")],
            "IC-123456",
        )


def test_structured_contract_sorts_planned_disco_from_transport_adjacency_path() -> None:
    service_id = "IC-388612"
    bearer = "ASH/R1 X 21-SCR/CS X 28 100G01"
    site_path = [
        "ASH/R1",
        "ASH/3",
        "ASH/4",
        "NEO/T2",
        "NEO",
        "NEO/2",
        "CHC/3",
        "DEN/F",
        "SLC/F",
        "SANF/2",
        "SANF",
        "PALO",
        "SCR/CS",
    ]
    route_edges = [
        bearer,
        "ASH/R1-ASH/3 OTUC01",
        "ASH/3-ASH/4 OL01",
        "ASH/4-NEO/T2 ODU407",
        "NEO-NEO/T2 OL01",
        "NEO-NEO/2 OL07",
        "NEO/2-CHC/3 ODU402",
        "CHC/3-DEN/F ODU412",
        "DEN/F-SLC/F ODU415",
        "SLC/F-SANF/2 ODU401",
        "SANF/2-SANF OL01",
        "SANF-PALO ODU411",
        "PALO-SCR/CS OTUC205",
    ]
    metadata = [
        _metadata_between(service_id, bearer, 1, "ASH/R1", "SCR/CS"),
        *[
            _metadata_between(service_id, edge, index + 2, site_path[index], site_path[index + 1])
            for index, edge in enumerate(route_edges[1:])
        ],
    ]
    transport_adjacency = [
        _transport_adjacency(service_id, bearer, "ASH/R1", "SCR/CS"),
        *[
            _transport_adjacency(service_id, edge, site_path[index], site_path[index + 1])
            for index, edge in enumerate(route_edges[1:])
            if edge
            not in {
                "ASH/3-ASH/4 OL01",
                "NEO-NEO/T2 OL01",
                "NEO-NEO/2 OL07",
                "SANF/2-SANF OL01",
            }
        ],
    ]
    device_site_path = [site for site in site_path if site != "NEO"]
    rows = [
        _row(
            site,
            bearer,
            index + 1,
            ne_info=f"{site} XS TM 01",
            status_t_time="Planned",
        )
        for index, site in enumerate(device_site_path)
    ]

    result = _sort_rows_by_structured_contract(
        list(reversed(rows)),
        metadata,
        service_id,
        transport_adjacency,
    )

    assert [row.site_code for row in result] == device_site_path


def test_structured_contract_preserves_bearer_a_to_b_transport_direction() -> None:
    service_id = "IC-123456"
    bearer = "ZZZ-AAA 100G01"
    left = "ZZZ-MID OTUC01"
    right = "MID-AAA OTUC02"
    metadata = [
        _metadata_between(service_id, bearer, 1, "ZZZ", "AAA"),
        _metadata_between(service_id, left, 2, "ZZZ", "MID"),
        _metadata_between(service_id, right, 3, "MID", "AAA"),
    ]
    transport_adjacency = [
        _transport_adjacency(service_id, bearer, "AAA", "ZZZ"),
        _transport_adjacency(service_id, left, "ZZZ", "MID"),
        _transport_adjacency(service_id, right, "MID", "AAA"),
    ]
    rows = [
        _row("AAA", bearer, 1, ne_info="AAA XS TM 01", status_t_time="Planned"),
        _row("MID", bearer, 1, ne_info="MID XS TM 01", status_t_time="Planned"),
        _row("ZZZ", bearer, 1, ne_info="ZZZ XS TM 01", status_t_time="Planned"),
    ]

    result = _sort_rows_by_structured_contract(
        rows,
        metadata,
        service_id,
        transport_adjacency,
    )

    assert [row.site_code for row in result] == ["ZZZ", "MID", "AAA"]


def test_structured_contract_orders_ic_388612_palo_handoff_from_transport_adjacency() -> None:
    service_id = "IC-388612"
    bearer = "ASH/R1 X 21-SCR/CS X 28 100G01"
    sanf_palo = "PALO-SANF ODU411"
    palo_scr = "PALO-SCR/CS OTUC205"
    metadata = [
        _metadata_between(service_id, bearer, 1, "ASH/R1", "SCR/CS"),
        _metadata_between(service_id, sanf_palo, 12, "SANF", "PALO"),
        _metadata_between(service_id, palo_scr, 13, "PALO", "SCR/CS"),
    ]
    transport_adjacency = [
        _transport_adjacency(
            service_id,
            sanf_palo,
            "PALO",
            "SANF",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
            endpoint_1_ne="PALO XS G40",
            endpoint_1_ne_part="04",
            endpoint_1_connection_point_nr="15",
            endpoint_1_slot="SLED6",
            endpoint_1_device_slot="SLED6",
            endpoint_1_device_subslot="T15",
            endpoint_2_ne="SANF XS G40",
            endpoint_2_ne_part="02",
            endpoint_2_connection_point_nr="15",
            endpoint_2_slot="SLED7",
            endpoint_2_device_slot="SLED7",
            endpoint_2_device_subslot="T15",
            port_match_rule="T_PORT_TO_CONNECTION_POINT_NR",
            platform_family="G30_G40",
            edge_position_path="1:19369493>1:19281419>7:19280605",
        ),
        _transport_adjacency(
            service_id,
            palo_scr,
            "PALO",
            "SCR/CS",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
            endpoint_1_ne="PALO XS TM 03",
            endpoint_1_ne_part="TM-3000I-01",
            endpoint_1_connection_point_nr="01",
            endpoint_1_slot="10",
            endpoint_1_subslot="C05-06",
            endpoint_1_device_slot="10",
            endpoint_1_device_subslot="C05-06",
            endpoint_1_ccp_connection_point_nr="01",
            endpoint_2_ne="SCR/CS XS TM 01",
            endpoint_2_ne_part="TM-3000I-02",
            endpoint_2_connection_point_nr="01",
            endpoint_2_slot="10",
            endpoint_2_subslot="L03-04",
            endpoint_2_device_slot="10",
            endpoint_2_device_subslot="L03-04",
            endpoint_2_ccp_connection_point_nr="01",
            port_match_rule="CONTENT_POSITION_TO_LINE_ENDPOINT",
            platform_family="OTM_TM",
            edge_position_path="1:19369492>1:18434734>2:18434647",
        ),
    ]
    palo_g40 = _row(
        "PALO",
        sanf_palo,
        1,
        ne_info="PALO XS G40 04",
        status_t_time="Planned",
        slot="SLED6",
        subslot="T15",
    )
    palo_tm = _row(
        "PALO",
        palo_scr,
        1,
        ne_info="PALO XS TM 03 TM-3000I-01",
        status_t_time="Planned",
        slot="10",
        subslot="C05-06",
    )
    rows = [
        _row(
            "SCR/CS",
            palo_scr,
            1,
            ne_info="SCR/CS XS TM 01 TM-3000I-02",
            status_t_time="Planned",
            slot="10",
            subslot="L03-04",
        ),
        palo_tm,
        palo_g40,
        _row("SANF", sanf_palo, 1, ne_info="SANF XS G40 02", status_t_time="Planned"),
    ]

    result = _sort_rows_by_structured_contract(
        rows,
        metadata,
        service_id,
        transport_adjacency,
    )

    palo_order = [row.ne_info for row in result if row.site_code == "PALO"]
    assert palo_order == ["PALO XS G40 04", "PALO XS TM 03 TM-3000I-01"]


def test_structured_contract_orders_ic_388612_chc_den_slc_from_endpoint_ports() -> None:
    service_id = "IC-388612"
    bearer = "NEO/2 X 1-SANF/2 X 1 100G01"
    metadata = [_metadata_between(service_id, bearer, 1, "NEO/2", "SANF/2") | {"MEDIA": "ET"}]
    transport_adjacency = [
        _transport_adjacency(
            service_id,
            "CHC/3-NEO/2 ODU402",
            "CHC/3",
            "NEO/2",
            endpoint_1_ne="CHC/3 XS WS 01",
            endpoint_1_ne_part="WS5",
            endpoint_1_connection_point_nr="05",
            endpoint_1_slot="01",
            endpoint_2_ne="NEO/2 XS WS 02",
            endpoint_2_ne_part="WS5",
            endpoint_2_connection_point_nr="05",
            endpoint_2_slot="05",
        ),
        _transport_adjacency(
            service_id,
            "CHC/3-DEN/F ODU412",
            "CHC/3",
            "DEN/F",
            endpoint_1_ne="CHC/3 XS WS 03",
            endpoint_1_ne_part="WS5",
            endpoint_1_connection_point_nr="06",
            endpoint_1_slot="07",
            endpoint_2_ne="DEN/F XS WS 08",
            endpoint_2_ne_part="WS5",
            endpoint_2_connection_point_nr="14",
            endpoint_2_slot="05",
        ),
        _transport_adjacency(
            service_id,
            "DEN/F-SLC/F ODU415",
            "DEN/F",
            "SLC/F",
            endpoint_1_ne="DEN/F XS WS 09",
            endpoint_1_ne_part="WS5",
            endpoint_1_connection_point_nr="16",
            endpoint_1_slot="05",
            endpoint_2_ne="SLC/F XS WS 05",
            endpoint_2_ne_part="WS5",
            endpoint_2_connection_point_nr="08",
            endpoint_2_slot="05",
        ),
        _transport_adjacency(
            service_id,
            "SANF/2-SLC/F ODU401",
            "SANF/2",
            "SLC/F",
            endpoint_1_ne="SANF/2 XS WS 02",
            endpoint_1_ne_part="WS5",
            endpoint_1_connection_point_nr="16",
            endpoint_1_slot="05",
            endpoint_2_ne="SLC/F XS WS 05",
            endpoint_2_ne_part="WS5",
            endpoint_2_connection_point_nr="16",
            endpoint_2_slot="05",
        ),
    ]
    rows = [
        _row("SANF/2", bearer, 1, ne_info="SANF/2 XS WS 02 WS5"),
        _row("SLC/F", bearer, 1, ne_info="SLC/F XS WS 05 WS5", slot="05", subslot="16"),
        _row("SLC/F", bearer, 1, ne_info="SLC/F XS WS 05 WS5", slot="05", subslot="08"),
        _row("DEN/F", bearer, 1, ne_info="DEN/F XS WS 09 WS5", slot="05", subslot="16"),
        _row("DEN/F", bearer, 1, ne_info="DEN/F XS WS 08 WS5", slot="05", subslot="14"),
        _row("CHC/3", bearer, 1, ne_info="CHC/3 XS WS 03 WS5", slot="07", subslot="06"),
        _row("CHC/3", bearer, 1, ne_info="CHC/3 XS WS 01 WS5", slot="01", subslot="05"),
        _row("NEO/2", bearer, 1, ne_info="NEO/2 XS WS 02 WS5"),
    ]

    result = _sort_rows_by_structured_contract(rows, metadata, service_id, transport_adjacency)

    assert [row.ne_info for row in result] == [
        "NEO/2 XS WS 02 WS5",
        "CHC/3 XS WS 01 WS5",
        "CHC/3 XS WS 03 WS5",
        "DEN/F XS WS 08 WS5",
        "DEN/F XS WS 09 WS5",
        "SLC/F XS WS 05 WS5",
        "SLC/F XS WS 05 WS5",
        "SANF/2 XS WS 02 WS5",
    ]
    assert [row.subslot for row in result if row.site_code == "SLC/F"] == ["08", "16"]


def test_structured_contract_fails_tm_client_line_without_explicit_mapping() -> None:
    service_id = "IC-388612"
    sanf_palo = "PALO-SANF ODU411"
    palo_scr = "PALO-SCR/CS OTUC205"
    rows = [
        _row(
            "PALO",
            sanf_palo,
            1,
            ne_info="PALO XS G40 04 G42 01",
            slot="SLED6",
            subslot="T15",
        ),
        _row(
            "PALO",
            palo_scr,
            1,
            ne_info="PALO XS TM 03 TM-3000I-01",
            slot="10",
            subslot="C05-06",
        ),
    ]

    with pytest.raises(StructuredRouteContractError, match="device transport endpoint not proven"):
        _sort_rows_by_structured_contract(
            rows,
            [
                _metadata_between(service_id, sanf_palo, 1, "SANF", "PALO"),
                _metadata_between(service_id, palo_scr, 2, "PALO", "SCR/CS"),
            ],
            service_id,
            [
                _transport_adjacency(
                    service_id,
                    sanf_palo,
                    "PALO",
                    "SANF",
                    endpoint_1_ne="PALO XS G40 04",
                    endpoint_1_ne_part="G42 01",
                    endpoint_1_connection_point_nr="15",
                    endpoint_1_slot="SLED6",
                    endpoint_1_device_slot="SLED6",
                    endpoint_1_device_subslot="T15",
                    endpoint_2_ne="SANF XS G40 02",
                    endpoint_2_ne_part="G42 01",
                    endpoint_2_connection_point_nr="15",
                    endpoint_2_slot="SLED7",
                    endpoint_2_device_slot="SLED7",
                    endpoint_2_device_subslot="T15",
                    port_match_rule="T_PORT_TO_CONNECTION_POINT_NR",
                    platform_family="G30_G40",
                ),
            ],
        )


def test_structured_contract_orders_ic_394531_same_site_handoffs_from_port_adjacency() -> None:
    service_id = "IC-394531"
    bearer = "REST X 90-SCR/CS X 28 100G01"
    metadata = [_metadata_between(service_id, bearer, 1, "REST", "SCR/CS") | {"MEDIA": "ET"}]
    transport_adjacency = [
        _transport_adjacency(
            service_id,
            "DENV-SLC/SH S900G04",
            "DENV/3",
            "SLC/SH",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
            endpoint_2_ne="SLC/SH XS DTN 01",
            endpoint_2_ne_part="XTC-03",
            endpoint_2_connection_point_nr="01",
            endpoint_2_slot="11",
            edge_position_path="1:19715331>369:18207886",
        ),
        _transport_adjacency(
            service_id,
            "SANF/2-SLC/SH ODU403",
            "SANF/2",
            "SLC/SH",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
            endpoint_1_ne="SANF/2 XS G40 05",
            endpoint_1_ne_part="G42 01",
            endpoint_1_connection_point_nr="05",
            endpoint_1_slot="12",
            endpoint_2_ne="SLC/SH XS G40 02",
            endpoint_2_ne_part="G42 01",
            endpoint_2_connection_point_nr="02",
            endpoint_2_slot="12",
            edge_position_path="1:19715331>1:19587597>7:19587427",
        ),
        _transport_adjacency(
            service_id,
            "PALO-SANF/2 ODU401",
            "PALO",
            "SANF/2",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
            endpoint_1_ne="PALO XS G40 07",
            endpoint_1_ne_part="G42 01",
            endpoint_1_connection_point_nr="07",
            endpoint_1_slot="13",
            endpoint_2_ne="SANF/2 XS G40 04",
            endpoint_2_ne_part="G42 01",
            endpoint_2_connection_point_nr="04",
            endpoint_2_slot="13",
            edge_position_path="1:19715331>1:19485285>5:19485274",
        ),
        _transport_adjacency(
            service_id,
            "PALO-SCR/CS ODU421",
            "PALO",
            "SCR/CS",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
            endpoint_1_ne="PALO XS G31 08",
            endpoint_1_ne_part="G31",
            endpoint_1_connection_point_nr="08",
            endpoint_1_slot="14",
            endpoint_2_ne="SCR/CS XS G31 04",
            endpoint_2_ne_part="G31",
            edge_position_path="1:19715331>1:19505109>3:19535937",
        ),
    ]
    rows = [
        _row("DENV/3", bearer, 1, ne_info="DENV/3 XS DTN 01 XT-09"),
        _row("SLC/SH", bearer, 1, ne_info="SLC/SH XS G40 02 G42 01", slot="12", subslot="02"),
        _row("SANF/2", bearer, 1, ne_info="SANF/2 XS G40 05 G42 01", slot="12", subslot="05"),
        _row("PALO", bearer, 1, ne_info="PALO XS G40 07 G42 01", slot="13", subslot="07"),
        _row("PALO", bearer, 1, ne_info="PALO XS G31 08 G31", slot="14", subslot="08"),
        _row("SANF/2", bearer, 1, ne_info="SANF/2 XS G40 04 G42 01", slot="13", subslot="04"),
        _row("SLC/SH", bearer, 1, ne_info="SLC/SH XS DTN 01 XTC-03", slot="11", subslot="01"),
        _row("SCR/CS", bearer, 1, ne_info="SCR/CS XS G31 04 G31"),
    ]

    result = _sort_rows_by_structured_contract(rows, metadata, service_id, transport_adjacency)

    assert [row.ne_info for row in result if row.site_code == "SLC/SH"] == [
        "SLC/SH XS DTN 01 XTC-03",
        "SLC/SH XS G40 02 G42 01",
    ]
    assert [row.ne_info for row in result if row.site_code == "SANF/2"] == [
        "SANF/2 XS G40 05 G42 01",
        "SANF/2 XS G40 04 G42 01",
    ]
    assert [row.ne_info for row in result if row.site_code == "PALO"] == [
        "PALO XS G40 07 G42 01",
        "PALO XS G31 08 G31",
    ]


def test_structured_contract_requires_same_site_device_continuity_proof() -> None:
    service_id = "IC-388612"
    sanf_palo = "PALO-SANF ODU411"
    palo_scr = "PALO-SCR/CS OTUC205"
    rows = [
        _row("PALO", palo_scr, 1, ne_info="PALO XS TM 03", status_t_time="Planned"),
        _row("PALO", sanf_palo, 1, ne_info="PALO XS G40 04", status_t_time="Planned"),
    ]

    with pytest.raises(StructuredRouteContractError, match="device transport endpoint not proven"):
        _sort_rows_by_structured_contract(
            rows,
            [
                _metadata_between(service_id, sanf_palo, 1, "SANF", "PALO"),
                _metadata_between(service_id, palo_scr, 2, "PALO", "SCR/CS"),
            ],
            service_id,
            [
                _transport_adjacency(service_id, sanf_palo, "SANF", "PALO"),
                _transport_adjacency(service_id, palo_scr, "PALO", "SCR/CS"),
            ],
        )


def test_structured_contract_allows_single_identity_through_device_on_two_edges() -> None:
    service_id = "IC-123456"
    bearer = "AAA X 1-BBB X 2 100G01"
    rows = [
        _row("BBB", bearer, 1, ne_info="BBB XS G40 01 G42 01"),
        _row("MID", bearer, 1, ne_info="MID XS WS 05 WS5"),
        _row("AAA", bearer, 1, ne_info="AAA XS G40 01 G42 01"),
    ]

    result = _sort_rows_by_structured_contract(
        rows,
        [_metadata_between(service_id, bearer, 1, "AAA", "BBB") | {"MEDIA": "ET"}],
        service_id,
        [
            _transport_adjacency(
                service_id,
                "AAA-MID ODU401",
                "AAA",
                "MID",
                endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
                endpoint_1_ne="AAA XS G40 01",
                endpoint_1_ne_part="G42 01",
                endpoint_2_ne="MID XS WS 05",
                endpoint_2_ne_part="WS5",
            ),
            _transport_adjacency(
                service_id,
                "MID-BBB ODU401",
                "MID",
                "BBB",
                endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
                endpoint_1_ne="MID XS WS 05",
                endpoint_1_ne_part="WS5",
                endpoint_2_ne="BBB XS G40 01",
                endpoint_2_ne_part="G42 01",
            ),
        ],
    )

    assert [row.site_code for row in result] == ["AAA", "MID", "BBB"]


def test_structured_contract_fails_single_unproven_transport_site() -> None:
    service_id = "IC-123456"
    bearer = "AAA X 1-BBB X 2 100G01"

    with pytest.raises(
        StructuredRouteContractError,
        match="transport adjacency path not proven for row site\\(s\\): MID",
    ):
        _sort_rows_by_structured_contract(
            [_row("MID", bearer, 1, ne_info="MID XS WS 05 WS5")],
            [_metadata_between(service_id, bearer, 1, "AAA", "BBB") | {"MEDIA": "ET"}],
            service_id,
            [_transport_adjacency(service_id, bearer, "AAA", "BBB")],
        )


def test_structured_contract_keeps_bearer_rows_before_same_site_local_edges() -> None:
    service_id = "IC-123456"
    bearer = "AAA X 1-BBB X 2 100G01"
    local_edge = "MID-MID OL01"
    mid_in = _row("MID", bearer, 1, ne_info="MID XS G40 01 G42 01", slot="01", subslot="11")
    mid_out = _row(
        "MID",
        bearer,
        2,
        ne_info="MID XS TM 03 TM-3000I-01",
        slot="02",
        subslot="22",
    )
    local_a = _row("MID", local_edge, 7, site_side="A")
    local_b = _row("MID", local_edge, 7, site_side="B")

    result = _sort_rows_by_structured_contract(
        [
            local_a,
            mid_out,
            _row("BBB", bearer, 1, ne_info="BBB XS G40 01 G42 01"),
            local_b,
            _row("AAA", bearer, 1, ne_info="AAA XS G40 01 G42 01"),
            mid_in,
        ],
        [
            _metadata_between(service_id, bearer, 1, "AAA", "BBB") | {"MEDIA": "ET"},
            _metadata_between(service_id, local_edge, 2, "MID", "MID"),
        ],
        service_id,
        [
            _transport_adjacency(service_id, "AAA-ASH ODU401", "AAA", "ASH"),
            _transport_adjacency(service_id, "ASH-NEO ODU401", "ASH", "NEO"),
            _transport_adjacency(
                service_id,
                "NEO-MID ODU401",
                "NEO",
                "MID",
                endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
                endpoint_2_ne="MID XS G40 01",
                endpoint_2_ne_part="G42 01",
                endpoint_2_connection_point_nr="11",
                endpoint_2_slot="01",
                edge_position_path="1:100>3:300",
            ),
            _transport_adjacency(
                service_id,
                "MID-SLC ODU401",
                "MID",
                "SLC",
                endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
                endpoint_1_ne="MID XS TM 03",
                endpoint_1_ne_part="TM-3000I-01",
                endpoint_1_connection_point_nr="22",
                endpoint_1_slot="02",
                edge_position_path="1:100>2:200",
            ),
            _transport_adjacency(service_id, "SLC-BBB ODU401", "SLC", "BBB"),
        ],
    )

    mid_route_paths = [row.route_path for row in result if row.site_code == "MID"]
    mid_devices = [row.ne_info for row in result if row.site_code == "MID" and row.is_device_row]
    assert mid_devices == [mid_in.ne_info, mid_out.ne_info]
    assert mid_route_paths == [bearer, bearer, local_edge, local_edge]


def test_structured_contract_orders_same_site_devices_by_neighbor_rank_not_position_path() -> None:
    service_id = "IC-123456"
    bearer = "AAA X 1-BBB X 2 100G01"
    mid_left = _row("MID", bearer, 1, ne_info="MID XS LEFT P1", slot="01", subslot="01")
    mid_right = _row("MID", bearer, 2, ne_info="MID XS RIGHT P1", slot="02", subslot="02")

    result = _sort_rows_by_structured_contract(
        [
            mid_left,
            _row("BBB", bearer, 1, ne_info="BBB XS G40 01 G42 01"),
            mid_right,
            _row("AAA", bearer, 1, ne_info="AAA XS G40 01 G42 01"),
        ],
        [_metadata_between(service_id, bearer, 1, "AAA", "BBB") | {"MEDIA": "ET"}],
        service_id,
        [
            _transport_adjacency(
                service_id,
                "AAA-MID ODU401",
                "AAA",
                "MID",
                endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
                endpoint_2_ne="MID XS LEFT",
                endpoint_2_ne_part="P1",
                endpoint_2_connection_point_nr="01",
                endpoint_2_slot="01",
                edge_position_path="1:100>9:900",
            ),
            _transport_adjacency(
                service_id,
                "BBB-MID ODU401",
                "BBB",
                "MID",
                endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
                endpoint_2_ne="MID XS RIGHT",
                endpoint_2_ne_part="P1",
                endpoint_2_connection_point_nr="02",
                endpoint_2_slot="02",
                edge_position_path="1:100>1:100",
            ),
        ],
    )

    mid_devices = [row.ne_info for row in result if row.site_code == "MID"]
    assert mid_devices == [mid_left.ne_info, mid_right.ne_info]


def test_structured_contract_fails_same_site_device_when_endpoint_port_missing() -> None:
    service_id = "IC-123456"
    bearer = "AAA X 1-BBB X 2 100G01"
    mid_left = _row("MID", bearer, 1, ne_info="MID XS LEFT P1", slot="01", subslot="01")
    mid_right = _row("MID", bearer, 2, ne_info="MID XS RIGHT P1", slot="02", subslot="02")

    with pytest.raises(StructuredRouteContractError, match="endpoint port proof missing"):
        _sort_rows_by_structured_contract(
            [
                mid_right,
                _row("BBB", bearer, 1, ne_info="BBB XS G40 01 G42 01"),
                mid_left,
                _row("AAA", bearer, 1, ne_info="AAA XS G40 01 G42 01"),
            ],
            [_metadata_between(service_id, bearer, 1, "AAA", "BBB") | {"MEDIA": "ET"}],
            service_id,
            [
                _transport_adjacency(
                    service_id,
                    "AAA-MID ODU401",
                    "AAA",
                    "MID",
                    endpoint_2_ne="MID XS LEFT",
                    endpoint_2_ne_part="P1",
                    endpoint_2_connection_point_nr="",
                    endpoint_2_slot="",
                ),
                _transport_adjacency(
                    service_id,
                    "BBB-MID ODU401",
                    "BBB",
                    "MID",
                    endpoint_2_ne="MID XS RIGHT",
                    endpoint_2_ne_part="P1",
                    endpoint_2_connection_point_nr="02",
                    endpoint_2_slot="02",
                ),
            ],
        )


def test_structured_contract_fails_when_one_device_port_matches_two_edges() -> None:
    service_id = "IC-123456"
    bearer = "AAA X 1-BBB X 2 100G01"
    mid_left = _row("MID", bearer, 1, ne_info="MID XS LEFT P1", slot="01", subslot="01")
    mid_right = _row("MID", bearer, 2, ne_info="MID XS RIGHT P1", slot="02", subslot="02")

    with pytest.raises(StructuredRouteContractError, match="not uniquely proven"):
        _sort_rows_by_structured_contract(
            [
                mid_left,
                mid_right,
                _row("AAA", bearer, 1, ne_info="AAA XS G40 01 G42 01"),
                _row("BBB", bearer, 1, ne_info="BBB XS G40 01 G42 01"),
            ],
            [_metadata_between(service_id, bearer, 1, "AAA", "BBB") | {"MEDIA": "ET"}],
            service_id,
            [
                _transport_adjacency(
                    service_id,
                    "AAA-MID ODU401",
                    "AAA",
                    "MID",
                    endpoint_2_ne="MID XS LEFT",
                    endpoint_2_ne_part="P1",
                    endpoint_2_connection_point_nr="01",
                    endpoint_2_slot="01",
                ),
                _transport_adjacency(
                    service_id,
                    "BBB-MID ODU401",
                    "BBB",
                    "MID",
                    endpoint_2_ne="MID XS RIGHT",
                    endpoint_2_ne_part="P1",
                    endpoint_2_connection_point_nr="02",
                    endpoint_2_slot="02",
                ),
                _transport_adjacency(
                    service_id,
                    "AAA-MID ALT ODU401",
                    "AAA",
                    "MID",
                    endpoint_2_ne="MID XS LEFT",
                    endpoint_2_ne_part="P1",
                    endpoint_2_connection_point_nr="01",
                    endpoint_2_slot="01",
                ),
            ],
        )


def test_structured_contract_fails_when_transport_adjacency_path_is_ambiguous() -> None:
    service_id = "IC-388612"
    bearer = "ASH/R1 X 21-SCR/CS X 28 100G01"
    first_hop = "ASH/R1-PALO OTUC01"
    second_hop = "PALO-SCR/CS OTUC205"
    metadata = [
        _metadata_between(service_id, bearer, 1, "ASH/R1", "SCR/CS"),
        _metadata_between(service_id, first_hop, 2, "ASH/R1", "PALO"),
        _metadata_between(service_id, second_hop, 3, "PALO", "SCR/CS"),
    ]
    transport_adjacency = [
        _transport_adjacency(service_id, first_hop, "ASH/R1", "PALO"),
        _transport_adjacency(service_id, second_hop, "PALO", "SCR/CS"),
        _transport_adjacency(
            service_id,
            "ASH/R1-SCR/CS ALT01",
            "ASH/R1",
            "SCR/CS",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
        ),
    ]

    with pytest.raises(StructuredRouteContractError, match="transport adjacency path not uniquely"):
        _sort_rows_by_structured_contract(
            [
                _row("ASH/R1", bearer, 1, ne_info="ASH/R1 Router", status_t_time="Planned"),
                _row("SCR/CS", bearer, 2, ne_info="SCR/CS Router", status_t_time="Planned"),
            ],
            metadata,
            service_id,
            transport_adjacency,
        )


def test_structured_contract_fails_on_conflicting_transport_endpoint_facts() -> None:
    service_id = "IC-123456"
    bearer = "AAA-BBB 100G01"

    with pytest.raises(
        StructuredRouteContractError,
        match="duplicate/conflicting TRANSPORT_DEVICE_ADJACENCY endpoint facts",
    ):
        _sort_rows_by_structured_contract(
            [
                _row("AAA", bearer, 1, ne_info="aaa-router NCS-5508"),
                _row("BBB", bearer, 1, ne_info="bbb-router NCS-5508"),
            ],
            [_metadata_between(service_id, bearer, 1, "AAA", "BBB")],
            service_id,
            [
                _transport_adjacency(service_id, bearer, "AAA", "BBB"),
                _transport_adjacency(service_id, bearer, "AAA", "CCC"),
            ],
        )


def test_structured_contract_rejects_untrusted_dwdm_transport_adjacency_source() -> None:
    service_id = "IC-123456"
    bearer = "AAA-BBB 100G01"

    with pytest.raises(
        StructuredRouteContractError,
        match="untrusted TRANSPORT_DEVICE_ADJACENCY proof source",
    ):
        _sort_rows_by_structured_contract(
            [
                _row("AAA", bearer, 1, ne_info="AAA XS DTN 01"),
                _row("BBB", bearer, 1, ne_info="BBB XS DTN 02"),
            ],
            [_metadata_between(service_id, bearer, 1, "AAA", "BBB")],
            service_id,
            [
                _transport_adjacency(
                    service_id,
                    bearer,
                    "AAA",
                    "BBB",
                    endpoint_proof_source="DTN_TEXT_MATCH",
                ),
            ],
        )


def test_structured_contract_rejects_blank_transport_adjacency_source() -> None:
    service_id = "IC-123456"
    bearer = "AAA-BBB 100G01"

    with pytest.raises(
        StructuredRouteContractError,
        match="untrusted TRANSPORT_DEVICE_ADJACENCY proof source",
    ):
        _sort_rows_by_structured_contract(
            [
                _row("AAA", bearer, 1, ne_info="AAA XS DTN 01"),
                _row("BBB", bearer, 1, ne_info="BBB XS DTN 02"),
            ],
            [_metadata_between(service_id, bearer, 1, "AAA", "BBB")],
            service_id,
            [
                _transport_adjacency(
                    service_id,
                    bearer,
                    "AAA",
                    "BBB",
                    endpoint_proof_source="",
                ),
            ],
        )


@pytest.mark.parametrize(
    "port_match_rule",
    ("", "UNKNOWN_PORT_MATCH_RULE"),
)
def test_structured_contract_rejects_unknown_or_blank_port_match_rule(
    port_match_rule: str,
) -> None:
    service_id = "IC-123456"
    bearer = "AAA-BBB 100G01"

    with pytest.raises(
        StructuredRouteContractError,
        match="untrusted TRANSPORT_DEVICE_ADJACENCY port match rule",
    ):
        _sort_rows_by_structured_contract(
            [
                _row("AAA", bearer, 1, ne_info="AAA XS DTN 01"),
                _row("BBB", bearer, 1, ne_info="BBB XS DTN 02"),
            ],
            [_metadata_between(service_id, bearer, 1, "AAA", "BBB")],
            service_id,
            [
                _transport_adjacency(
                    service_id,
                    bearer,
                    "AAA",
                    "BBB",
                    port_match_rule=port_match_rule,
                    platform_family="G30_G40",
                ),
            ],
        )


def test_structured_contract_accepts_neutral_cabling_port_match_rule() -> None:
    service_id = "IC-123456"
    bearer = "AAA-BBB 100G01"

    rows = _sort_rows_by_structured_contract(
        [
            _row("BBB", bearer, 1, ne_info="BBB XS DTN 02 -port", slot="01", subslot="01"),
            _row("AAA", bearer, 1, ne_info="AAA XS DTN 01 -port", slot="01", subslot="01"),
        ],
        [_metadata_between(service_id, bearer, 1, "AAA", "BBB")],
        service_id,
        [
            _transport_adjacency(
                service_id,
                bearer,
                "AAA",
                "BBB",
                endpoint_1_ne="AAA XS DTN",
                endpoint_1_ne_part="01",
                endpoint_2_ne="BBB XS DTN",
                endpoint_2_ne_part="02",
                port_match_rule="CABLING_POINT_TO_PEER_CABLING_POINT",
                platform_family="DTN",
            ),
        ],
    )

    assert [row.site_code for row in rows] == ["AAA", "BBB"]


def test_structured_contract_splits_ic_371205_migration_from_live_status_facts() -> None:
    service_id = "IC-371205"
    bearer = "OSD/I X 23-OSD2/I BR 17 1G01"
    current_ol = "OSD/I-OSD2/I OL02"
    migration_ol = "OSD2/I-OSD2/Y U 1 OL01"
    metadata = [
        _metadata_between(service_id, bearer, 1, "OSD/I", "OSD2/I")
        | {
            "A_SITE_LOCATION_ID": "COP00002",
            "B_SITE_LOCATION_ID": "COP00031",
            "MEDIA": ".",
        },
        _metadata_between(service_id, current_ol, 2, "OSD/I", "OSD2/I")
        | {
            "A_SITE_LOCATION_ID": "COP00002",
            "B_SITE_LOCATION_ID": "COP00031",
        },
        _metadata_between(service_id, migration_ol, 3, "OSD2/I", "OSD2/Y")
        | {
            "A_SITE_LOCATION_ID": "COP00031",
            "B_SITE_LOCATION_ID": "COP00002",
        },
    ]
    rows = [
        _row("OSD2/I", bearer, 1, ne_info="kbn-b4-sat1"),
        _row("OSD2/I", bearer, 1, ne_info="kbn-b4-sat1"),
        _row("OSD2/I", migration_ol, 35, site_side="A", status_o_time="Planned"),
        _row("OSD2/I", migration_ol, 36, site_side="A", status_o_time="Planned"),
        _row("OSD2/Y", migration_ol, 35, site_side="B", status_o_time="Planned"),
        _row("OSD2/Y", migration_ol, 36, site_side="B", status_o_time="Planned"),
        _row("OSD/I", current_ol, 9, site_side="A", status_t_time="Planned"),
        _row("OSD/I", current_ol, 10, site_side="A", status_t_time="Planned"),
        _row("OSD2/I", current_ol, 9, site_side="B", status_t_time="Planned"),
        _row("OSD2/I", current_ol, 10, site_side="B", status_t_time="Planned"),
    ]

    current_route, migration_route = _sort_service_sections_by_structured_contract(
        rows,
        metadata,
        service_id,
    )

    assert len(current_route) == 6
    assert len(migration_route) == 6
    assert [row.route_path for row in current_route].count(current_ol) == 4
    assert [row.route_path for row in current_route].count(bearer) == 2
    assert [row.route_path for row in migration_route].count(migration_ol) == 4
    assert [row.route_path for row in migration_route].count(bearer) == 2
    assert {row.classification for row in current_route} == {"DECOMMISSION", "LIVE"}
    assert {row.classification for row in migration_route} == {"NEW", "LIVE"}


def test_structured_contract_uses_required_row_sites_for_ic_364797_migration_branch() -> None:
    service_id = "IC-364797"
    bearer = "HY/I X 11-OSD/I X 22 100G01"
    current_ol = "OSD/I-OSD2/I OL51"
    migration_ol = "OSD2/I-OSD2/Y U 1 OL01"
    hy_loop = "HY/I-HY/I U 1 OL06"
    metadata = [
        _metadata_between(service_id, bearer, 1, "HY/I", "OSD/I") | {"MEDIA": "ET"},
        _metadata_between(service_id, current_ol, 2, "OSD/I", "OSD2/I"),
        _metadata_between(service_id, migration_ol, 3, "OSD2/I", "OSD2/Y"),
        _metadata_between(service_id, hy_loop, 4, "HY/I", "HY/I"),
    ]
    transport_adjacency = [
        _transport_adjacency(service_id, bearer, "HY/I", "OSD2/I"),
        _transport_adjacency(service_id, "HY/I-OSD2/I ODU407", "HY/I", "OSD2/I"),
        _transport_adjacency(service_id, "HY/I-OSD2/I OTUC204", "HY/I", "OSD2/I"),
    ]
    rows = [
        _row("HY/I", bearer, 1, ne_info="HY/I XS G30 14"),
        _row("OSD2/I", bearer, 1, ne_info="OSD2/I XS G30 06"),
        _row("OSD/I", current_ol, 27, site_side="A", status_t_time="Planned"),
        _row("OSD/I", current_ol, 28, site_side="A", status_t_time="Planned"),
        _row("OSD2/I", current_ol, 27, site_side="B", status_t_time="Planned"),
        _row("OSD2/I", current_ol, 28, site_side="B", status_t_time="Planned"),
        _row("OSD2/I", migration_ol, 33, site_side="A", status_o_time="Planned"),
        _row("OSD2/I", migration_ol, 34, site_side="A", status_o_time="Planned"),
        _row("OSD2/Y", migration_ol, 33, site_side="B", status_o_time="Planned"),
        _row("OSD2/Y", migration_ol, 34, site_side="B", status_o_time="Planned"),
        _row("HY/I", hy_loop, 87, site_side="A"),
        _row("HY/I", hy_loop, 88, site_side="B"),
    ]

    current_route, migration_route = _sort_service_sections_by_structured_contract(
        rows,
        metadata,
        service_id,
        transport_adjacency,
    )

    assert "OSD/I" in {row.site_code for row in current_route}
    assert "OSD2/Y" not in {row.site_code for row in current_route}
    assert "OSD2/Y" in {row.site_code for row in migration_route}
    assert "OSD/I" not in {row.site_code for row in migration_route}
    assert list(dict.fromkeys(row.site_code for row in migration_route)) == [
        "HY/I",
        "OSD2/I",
        "OSD2/Y",
    ]


def test_structured_contract_scopes_ic_370987_migration_to_proven_new_path() -> None:
    service_id = "IC-370987"
    bearer = "MM/B X 6-OSD/I X 22 10G01"
    current_ol = "OSD/I-OSD2/I OL52"
    migration_ol = "OSD2/I-OSD2/X U 1 OL50"
    mm_loop = "MM/B-MM/B OL01"
    mm_sp = "MM/B-MM/SP OL01"
    metadata = [
        _metadata_between(service_id, bearer, 1, "MM/B", "OSD/I") | {"MEDIA": "ET"},
        _metadata_between(service_id, current_ol, 2, "OSD/I", "OSD2/I"),
        _metadata_between(service_id, migration_ol, 3, "OSD2/I", "OSD2/X"),
        _metadata_between(service_id, mm_loop, 4, "MM/B", "MM/B"),
        _metadata_between(service_id, mm_sp, 5, "MM/B", "MM/SP"),
    ]
    transport_adjacency = [
        _transport_adjacency(
            service_id,
            "MM/SP-OSD2/I ODU219",
            "MM/SP",
            "OSD2/I",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
        ),
        _transport_adjacency(service_id, "MM/SP2-OSD2/I ODU410", "MM/SP2", "OSD2/I"),
    ]
    rows = [
        _row("MM/SP", bearer, 1, ne_info="MM/SP XS G30 02", status_t_time="Planned"),
        _row("OSD2/I", bearer, 1, ne_info="OSD2/I XS G30 05", status_t_time="Planned"),
        _row("OSD/I", current_ol, 11, site_side="A", status_t_time="Planned"),
        _row("OSD2/I", current_ol, 11, site_side="B", status_t_time="Planned"),
        _row("MM/SP2", bearer, 1, ne_info="MM/SP2 XS G31 01", status_o_time="Planned"),
        _row("OSD2/I", bearer, 1, ne_info="OSD2/I XS G31 23", status_o_time="Planned"),
        _row("OSD2/I", migration_ol, 13, site_side="A", status_o_time="Planned"),
        _row("OSD2/X", migration_ol, 13, site_side="B", status_o_time="Planned"),
        _row("MM/B", mm_loop, 21, site_side="A"),
        _row("MM/B", mm_loop, 21, site_side="B"),
        _row("MM/B", mm_sp, 177, site_side="A"),
        _row("MM/SP", mm_sp, 177, site_side="B"),
    ]

    current_route, migration_route = _sort_service_sections_by_structured_contract(
        rows,
        metadata,
        service_id,
        transport_adjacency,
    )

    assert list(dict.fromkeys(row.site_code for row in current_route)) == [
        "MM/B",
        "MM/SP",
        "OSD2/I",
        "OSD/I",
    ]
    assert list(dict.fromkeys(row.site_code for row in migration_route)) == [
        "MM/SP2",
        "OSD2/I",
        "OSD2/X",
    ]
    assert "MM/SP" not in {row.site_code for row in migration_route}


def test_structured_contract_sorts_ic_381405_when_endpoint_location_is_blank() -> None:
    service_id = "IC-381405"
    bearer = "DK/1 X 11-OSD2/I BR 17 1G01"
    current_ol = "OSD/I-OSD2/I OL51"
    migration_ol = "OSD2/I-OSD2/X U 1 OL50"
    metadata = [
        _metadata_between(service_id, bearer, 1, "DK/1", "OSD2/I")
        | {
            "A_SITE_LOCATION_ID": "COP00392",
            "B_SITE_LOCATION_ID": "",
            "MEDIA": ".",
            "A_SITE_TYPE": "X",
            "A_SITE_TYPE_NO": "11",
            "B_SITE_TYPE": "BR",
            "B_SITE_TYPE_NO": "17",
        },
        _metadata_between(service_id, current_ol, 2, "OSD/I", "OSD2/I"),
        _metadata_between(service_id, migration_ol, 3, "OSD2/I", "OSD2/X"),
    ]
    rows = [
        _row("OSD2/I", bearer, 1, ne_info="kbn-b4-sat1 DCS-7280SR-48C6"),
        _row("OSD/I", current_ol, 21, site_side="A", status_t_time="Planned"),
        _row("OSD2/I", current_ol, 21, site_side="B", status_t_time="Planned"),
        _row("OSD2/I", migration_ol, 23, site_side="A", status_o_time="Planned"),
        _row("OSD2/X", migration_ol, 23, site_side="B", status_o_time="Planned"),
    ]

    current_route, migration_route = _sort_service_sections_by_structured_contract(
        rows,
        metadata,
        service_id,
    )

    assert list(dict.fromkeys(row.site_code for row in current_route)) == [
        "OSD2/I",
        "OSD/I",
    ]
    assert list(dict.fromkeys(row.site_code for row in migration_route)) == [
        "OSD2/I",
        "OSD2/X",
    ]


def test_structured_contract_sorts_ic_394531_from_device_matched_transport_handoffs() -> None:
    service_id = "IC-394531"
    bearer = "REST X 90-SCR/CS X 28 100G01"
    rest_loop = "REST U 1-REST/2 OL06"
    scr_loop = "SCR/CS-SCR/CS U 1 OL05"
    metadata = [
        _metadata_between(service_id, bearer, 1, "REST", "SCR/CS") | {"MEDIA": "ET"},
        _metadata_between(service_id, "ATM/2-ATM/2 OL02", 2, "ATM/2", "ATM/2"),
        _metadata_between(service_id, rest_loop, 3, "REST", "REST/2"),
        _metadata_between(service_id, "ASH/2-ASH/3 OL02", 4, "ASH/2", "ASH/3"),
        _metadata_between(service_id, scr_loop, 5, "SCR/CS", "SCR/CS"),
        _metadata_between(service_id, "DENV-DENV OL01", 6, "DENV", "DENV"),
        _metadata_between(service_id, "DENV-DENV/3 OL01", 7, "DENV", "DENV/3"),
        _metadata_between(service_id, "CHC/2-CHC/3 OL02", 8, "CHC/2", "CHC/3"),
    ]
    transport_adjacency = [
        _transport_adjacency(service_id, "ASH/2-ATM/2 OCGX05", "ASH/2", "ATM/2"),
        _transport_adjacency(service_id, "ASH/3-REST/2 ODU411", "ASH/3", "REST/2"),
        _transport_adjacency(
            service_id,
            "ATM/2-IPLS O600G03",
            "ATM/2",
            "IPLS",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
        ),
        _transport_adjacency(
            service_id,
            "CHC-IPLS O900G02",
            "CHC/3",
            "IPLS",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
        ),
        _transport_adjacency(
            service_id,
            "CHC-KANC OCGX11",
            "CHC/2",
            "KANC/2",
            endpoint_proof_source="EXACT_DEVICE_PORT_MATCH",
        ),
        _transport_adjacency(service_id, "DENV-KANC OCGX16", "DENV", "KANC/2"),
        _transport_adjacency(service_id, "DENV-SLC/SH S900G04", "DENV/3", "SLC/SH"),
        _transport_adjacency(service_id, "PALO-SANF/2 ODU401", "PALO", "SANF/2"),
        _transport_adjacency(service_id, "PALO-SCR/CS ODU421", "PALO", "SCR/CS"),
        _transport_adjacency(service_id, "SANF/2-SLC/SH ODU403", "SANF/2", "SLC/SH"),
    ]
    expected_sites = [
        "REST",
        "REST/2",
        "ASH/3",
        "ASH/2",
        "ATM/2",
        "IPLS",
        "CHC/3",
        "CHC/2",
        "DENV",
        "DENV/3",
        "SLC/SH",
        "SANF/2",
        "PALO",
        "SCR/CS",
    ]
    rows = [
        *[
            _row(site, bearer, index + 1, ne_info=f"{site} XS transport")
            for index, site in enumerate(expected_sites)
        ],
        _row("REST", rest_loop, 1, site_side="A"),
        _row("REST/2", rest_loop, 1, site_side="B"),
        _row("SCR/CS", scr_loop, 1, site_side="A"),
        _row("SCR/CS", scr_loop, 2, site_side="B"),
    ]

    result = _sort_rows_by_structured_contract(
        list(reversed(rows)),
        metadata,
        service_id,
        transport_adjacency,
    )

    assert list(dict.fromkeys(row.site_code for row in result)) == expected_sites
    assert rest_loop in {row.route_path for row in result}
    assert scr_loop in {row.route_path for row in result}


def test_structured_contract_uses_icb_823402_l1_bearer_adjacency_for_b_endpoint() -> None:
    service_id = "ICB-823402"
    bearer = "MEI/IX BR 11-SNG/G BR 1 100G10"
    mei_trunk = "MEI U 1-MEI/IX OL05"
    metadata = [
        _metadata_between(service_id, bearer, 1, "MEI/IX", "SNG/G")
        | {
            "A_SITE_LOCATION_ID": "MRS00034",
            "B_SITE_LOCATION_ID": "SIN00002",
            "MEDIA": "ET",
        },
        _metadata_between(service_id, mei_trunk, 2, "MEI", "MEI/IX")
        | {
            "A_SITE_LOCATION_ID": "MRS00001",
            "B_SITE_LOCATION_ID": "MRS00034",
        },
    ]
    transport_adjacency = [
        _transport_adjacency(service_id, bearer, "MEI/IX", "SNG/G"),
    ]
    rows = [
        _row("MEI/IX", bearer, 1, ne_info="mei-b6", status_o_time="Planned"),
        _row("SNG/G", bearer, 1, ne_info="sng-b6", status_o_time="Planned"),
        _row("MEI/IX", mei_trunk, 15, site_side="B", status_o_time="Planned"),
        _row("MEI/IX", mei_trunk, 16, site_side="B", status_o_time="Planned"),
        _row("MEI", mei_trunk, 15, site_side="A", status_o_time="Planned"),
        _row("MEI", mei_trunk, 16, site_side="A", status_o_time="Planned"),
    ]

    sorted_rows, migration_rows = _sort_service_sections_by_structured_contract(
        rows,
        metadata,
        service_id,
        transport_adjacency,
    )

    assert migration_rows == []
    assert list(dict.fromkeys(row.site_code for row in sorted_rows)) == [
        "MEI",
        "MEI/IX",
        "SNG/G",
    ]


def test_structured_contract_uses_transport_endpoint_proof_for_ic_335493_bearer() -> None:
    service_id = "IC-335493"
    bearer = "KOG X 6-STHLM X 459 10G01"
    current_ol = "OSD-OSD2/I OL50"
    migration_ol = "OSD2/I-OSD2/Y U 1 OL01"
    hy_loop = "HY/I-HY/I U 1 OL02"
    metadata = [
        _metadata_between(service_id, bearer, 1, "KOG", "STHLM") | {"MEDIA": "ET"},
        _metadata_between(service_id, current_ol, 2, "OSD", "OSD2/I"),
        _metadata_between(service_id, migration_ol, 3, "OSD2/I", "OSD2/Y"),
        _metadata_between(service_id, hy_loop, 4, "HY/I", "HY/I"),
    ]
    transport_adjacency = [
        _transport_adjacency(service_id, bearer, "HY/I", "OSD2/I"),
    ]
    rows = [
        _row("HY/I", bearer, 1, ne_info="HY/I XS DTN 01"),
        _row("OSD2/I", bearer, 1, ne_info="OSD2/I XS DTN 03"),
        _row("OSD", current_ol, 27, site_side="A", status_t_time="Planned"),
        _row("OSD", current_ol, 28, site_side="A", status_t_time="Planned"),
        _row("OSD2/I", current_ol, 27, site_side="B", status_t_time="Planned"),
        _row("OSD2/I", current_ol, 28, site_side="B", status_t_time="Planned"),
        _row("OSD2/I", migration_ol, 33, site_side="A", status_o_time="Planned"),
        _row("OSD2/I", migration_ol, 34, site_side="A", status_o_time="Planned"),
        _row("OSD2/Y", migration_ol, 33, site_side="B", status_o_time="Planned"),
        _row("OSD2/Y", migration_ol, 34, site_side="B", status_o_time="Planned"),
        _row("HY/I", hy_loop, 87, site_side="A"),
        _row("HY/I", hy_loop, 88, site_side="B"),
    ]

    current_route, migration_route = _sort_service_sections_by_structured_contract(
        rows,
        metadata,
        service_id,
        transport_adjacency,
    )

    assert list(dict.fromkeys(row.site_code for row in current_route)) == [
        "HY/I",
        "OSD2/I",
        "OSD",
    ]
    assert list(dict.fromkeys(row.site_code for row in migration_route)) == [
        "HY/I",
        "OSD2/I",
        "OSD2/Y",
    ]


def test_structured_contract_fails_when_route_path_is_missing() -> None:
    with pytest.raises(StructuredRouteContractError, match="missing route contract"):
        _sort_rows_by_structured_contract(
            [_row("AAA", "AAA-BBB OL01", 1, site_side="A")],
            [_metadata("IC-123456", "CCC-DDD OL99")],
            "IC-123456",
        )


def test_structured_contract_sorts_ic_320550_dp_exact_endpoint_roles() -> None:
    service_id = "IC-320550"
    bearer = "DDF/I X 56-GLOS/I X 37 OTU401"
    ddf_dp = "Demarcation point: DDF/I X 56 1298-913194 VODAFONE PROCUREMENT COMPANY SARL"
    glos_dp = "Demarcation point: GLOS/I X 37 VODAFONE ENTERPRISE EQUIPMENT LTD"
    ddf_row = _row("DDF/I", ddf_dp, 1, ne_info="SDP ODF")
    ddf_row.site_type = "X"
    ddf_row.site_type_no = "56"
    ddf_row.cabling_points = "port 2"
    ddf_row.conn_type = ""
    glos_row = _row("GLOS/I", glos_dp, 3, ne_info="SDP ODF")
    glos_row.site_type = "X"
    glos_row.site_type_no = "37"
    glos_row.cabling_points = "port 5+6"
    glos_row.conn_type = ""

    result = _sort_rows_by_structured_contract(
        [glos_row, ddf_row],
        [
            _metadata_between(service_id, bearer, 1, "DDF/I", "GLOS/I")
            | {
                "A_SITE_TYPE": "X",
                "A_SITE_TYPE_NO": "56",
                "B_SITE_TYPE": "X",
                "B_SITE_TYPE_NO": "37",
            }
        ],
        service_id,
        dp_endpoint_roles=[
            _dp_endpoint_role(
                service_id,
                ddf_dp,
                "DDF/I",
                "X",
                "56",
                1,
                "port 2",
                "",
                bearer,
                "A",
            ),
            _dp_endpoint_role(
                service_id,
                glos_dp,
                "GLOS/I",
                "X",
                "37",
                3,
                "port 5+6",
                "",
                bearer,
                "B",
            ),
        ],
    )

    assert [row.route_path for row in result] == [ddf_dp, glos_dp]


def test_structured_contract_sorts_icb_823422_transport_backed_dp_role() -> None:
    service_id = "ICB-823422"
    bearer = "MOTL BR 5-MOTL/4 BR 2 100G02"
    dp_route = "Demarcation point: MOTL/4 XS"
    motl_dp = _row(
        "MOTL/4",
        dp_route,
        9,
        ne_info="DP ODF",
        status_o_time="Planned",
    )
    motl_dp.cabling_points = "09 Cable"
    motl_dp.conn_type = "LC/UPC"
    rows = [
        _row("MOTL", bearer, 1, ne_info="motl-b4", status_o_time="Planned"),
        _row("MOTL/4", bearer, 1, ne_info="motl-4deg-s2", status_o_time="Planned"),
        motl_dp,
    ]

    result = _sort_rows_by_structured_contract(
        rows,
        [_metadata_between(service_id, bearer, 1, "MOTL", "MOTL/4") | {"MEDIA": "ET"}],
        service_id,
        [_transport_adjacency(service_id, bearer, "MOTL", "MOTL/4")],
        [
            _dp_endpoint_role(
                service_id,
                dp_route,
                "MOTL/4",
                "XS",
                "",
                9,
                "09 Cable",
                "LC/UPC",
                bearer,
                "B",
                endpoint_proof_source="DP_SITE_CODE_TRANSPORT_ENDPOINT",
            )
        ],
    )

    assert [row.site_code for row in result] == ["MOTL", "MOTL/4", "MOTL/4"]
    assert result[-1].route_path == dp_route


def test_structured_contract_fails_for_icb_127392_ambiguous_dp_role() -> None:
    service_id = "ICB-127392"
    bearer = "KB X 98-OSD2/I BR 20 10G01"
    osd2_dp = "Demarcation point: OSD2/I XS FB-315944 TELIA MOBIL DANMARK A/S"
    row = _row("OSD2/I", osd2_dp, 1, ne_info="DP ODF")
    row.cabling_points = "3/4"
    row.conn_type = ""

    with pytest.raises(StructuredRouteContractError, match="DP/SDP endpoint role not proven"):
        _sort_rows_by_structured_contract(
            [row],
            [
                _metadata_between(service_id, bearer, 1, "KB", "OSD2/I"),
                _metadata_between(service_id, "OSD/I-OSD2/I OL51", 2, "OSD/I", "OSD2/I"),
                _metadata_between(
                    service_id,
                    "OSD2/I-OSD2/X U 1 OL50",
                    3,
                    "OSD2/I",
                    "OSD2/X",
                ),
            ],
            service_id,
        )


def test_structured_contract_fails_for_demarcation_without_role_proof() -> None:
    row = _row("AAA", "Demarcation point: AAA XS", 1, ne_info="DP ODF")

    with pytest.raises(StructuredRouteContractError, match="DP/SDP endpoint role not proven"):
        _sort_rows_by_structured_contract(
            [row], [_metadata("IC-123456", row.route_path)], "IC-123456"
        )


def test_combined_results_does_not_import_legacy_sorter() -> None:
    source = Path("src/lasagna/route_sorting/combined_results.py").read_text(encoding="utf-8")
    deleted_package = Path("src/lasagna/route_sorting") / ("inca" + "_sorter")

    assert not deleted_package.exists()
    assert ("sort_" + "inca_route_path") not in source
    assert ("LEGACY_" + "FALLBACK") not in source
    assert "TL_DEVICE_SHARED_HANDOFF" not in source
    assert "EXACT_DEVICE_ROW_MATCH" not in source
    assert "_site_uses_reverse_position_path" not in source
    assert "row.connection_point_nr" not in source
    assert "_parse_edge_site_pair" not in source
    assert "service_mode(" not in source
    assert "row.row_index" not in source
    assert "cabling_location" not in source
    assert "cabinet" not in source.lower()
    assert "proximity" not in source.lower()
    assert "dominant" not in source.lower()
    assert "site_type ==" not in source
    assert "site_type in " not in source
