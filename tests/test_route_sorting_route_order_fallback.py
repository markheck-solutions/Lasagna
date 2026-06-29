from lasagna.route_sorting.inca_sorter.models import InCARow
from lasagna.route_sorting.inca_sorter.sorting import (
    _filter_site_order_for_data,
    _interleave_inter_site_trunk_pairs,
    _prepare_route_sort,
    build_trunk_endpoint_lookup,
    parse_snowflake_edges,
    sort_inca_route_path,
)


def _row(
    site_code: str,
    route_path: str,
    pos: int,
    site_type: str = "XS",
    *,
    ne_info: str | None = None,
    site_side: str | None = None,
) -> InCARow:
    row = InCARow(
        site_code=site_code,
        site_type=site_type,
        ne_info=ne_info,
        cabling_location=f"[{site_code}]01/R01/RU01/.",
        cabling_points=str(pos),
        conn_type="LC",
        location_alias=None,
        route_path=route_path,
        pos=pos,
        status_o_time=None,
        o_time=None,
        status_t_time=None,
        t_time=None,
        comment=None,
    )
    row.site_side = site_side
    return row


def _passive_positions(rows: list[InCARow], route_path: str) -> list[int]:
    return [
        index
        for index, row in enumerate(rows)
        if row.route_path == route_path and not row.is_device_row and not row.is_demarcation
    ]


def _is_contiguous(positions: list[int]) -> bool:
    return positions == list(range(min(positions), max(positions) + 1))


def test_route_order_site_gap_falls_back_to_hierarchy_topology() -> None:
    service_id = "IC-123456"
    bearer = "AAA X 1-CCC X 1 10G01"
    route_path = "AAA-BBB OL01"
    rows = [
        _row("AAA", bearer, 1),
        _row("AAA", route_path, 2),
        _row("BBB", route_path, 3),
        _row("CCC", bearer, 4),
    ]
    route_order_metadata = [
        {
            "SERVICE_ID": service_id,
            "ROUTE_PATH": route_path,
            "EDGE_SEQUENCE": 1,
            "EDGE_NAME": route_path,
            "A_SITE_CODE": "AAA",
            "B_SITE_CODE": "BBB",
            "A_SITE_LOCATION_ID": "LOC-A",
            "B_SITE_LOCATION_ID": "LOC-B",
            "A_SITE_SIDE": "A",
            "B_SITE_SIDE": "B",
            "MEDIA": "OL",
        }
    ]
    snowflake_edges = [
        {
            "SERVICE_ID": service_id,
            "EDGE_NAME": "BBB-CCC OL02",
            "LEVEL": "L2",
        }
    ]

    result = sort_inca_route_path(
        rows,
        service_id=service_id,
        snowflake_edge_records=snowflake_edges,
        route_order_metadata_records=route_order_metadata,
    )

    assert "Site order: AAA -> BBB -> CCC" in result.info_lines
    assert [row.site_code for row in result.rows] == ["AAA", "AAA", "BBB", "CCC"]


def test_site_variant_filter_preserves_ascending_numeric_suffixes() -> None:
    sites = {"ASH/2", "ASH/3"}

    assert _filter_site_order_for_data(["ASH/2", "ASH/3"], sites, set()) == ["ASH/2", "ASH/3"]
    assert _filter_site_order_for_data(["ASH/3", "ASH/2"], sites, set()) == ["ASH/2", "ASH/3"]


def test_trunk_endpoint_lookup_matches_normalized_edge_names() -> None:
    service_id = "IC-123456"
    trunk = "ASH/2-ASH/3 OL02"
    lookup = build_trunk_endpoint_lookup(
        [
            {
                "BPK_PCG": trunk,
                "A_SITE_CODE": "ASH/2",
                "B_SITE_CODE": "ASH/3",
            }
        ]
    )

    edges = parse_snowflake_edges(
        [{"SERVICE_ID": service_id, "EDGE_NAME": " ash/2-ash/3 ol02 ", "LEVEL": "L1"}],
        service_id,
        {"ASH/2", "ASH/3"},
        lookup,
    )

    assert edges == [("ASH/2", "ASH/3", "ash/2-ash/3 ol02")]


def test_segment_assembly_keeps_crossed_inter_site_trunks_contiguous() -> None:
    bearer = "REST X 90-SCR/CS X 28 100G01"
    rest_trunk = "REST U 1-REST/2 OL06"
    ash_trunk = "ASH/2-ASH/3 OL02"
    sorted_rows = [
        _row("REST", rest_trunk, 41, "U", site_side="A"),
        _row("REST", rest_trunk, 42, "U", site_side="A"),
        _row("REST", bearer, 1),
        _row("ASH/2", ash_trunk, 47, site_side="A"),
        _row("ASH/2", ash_trunk, 48, site_side="A"),
        _row("REST/2", rest_trunk, 41, site_side="B"),
        _row("REST/2", rest_trunk, 42, site_side="B"),
        _row("ASH/2", ash_trunk, 92, ne_info="ASH/2 device"),
        _row("ASH/3", ash_trunk, 47, site_side="B"),
        _row("ASH/3", ash_trunk, 48, site_side="B"),
        _row("SCR/CS", bearer, 1),
    ]
    trunk_edges = [
        ("REST", "REST/2", rest_trunk),
        ("ASH/2", "ASH/3", ash_trunk),
    ]

    result = _interleave_inter_site_trunk_pairs(sorted_rows, trunk_edges)

    sorted_route_paths = [row.route_path for row in result]
    assert _is_contiguous(_passive_positions(result, rest_trunk)), sorted_route_paths
    assert _is_contiguous(_passive_positions(result, ash_trunk)), sorted_route_paths
    passive_route_paths = [
        row.route_path for row in result if not row.is_device_row and not row.is_demarcation
    ]
    assert passive_route_paths[:4] == [rest_trunk] * 4
    assert passive_route_paths[5:9] == [ash_trunk] * 4
    assert result[4].route_path == bearer
    assert next(row for row in result if row.is_device_row).route_path == ash_trunk


def test_fallback_keeps_same_location_endpoint_device_with_trunk_handoff() -> None:
    service_id = "IC-394531"
    bearer = "REST X 90-SCR/CS X 28 100G01"
    rest_trunk = "REST U 1-REST/2 OL06"
    ash_trunk = "ASH/2-ASH/3 OL02"
    rest2_to_ash3 = "ASH/3-REST/2 ODU411"
    rest_to_ash2 = "REST-ASH/2 ODU000"
    rows = [
        _row("REST", rest_trunk, 41, "U", site_side="A"),
        _row("REST", rest_trunk, 42, "U", site_side="A"),
        _row("REST/2", rest_trunk, 41, site_side="B"),
        _row("REST/2", rest_trunk, 42, site_side="B"),
        _row("REST/2", bearer, 1, ne_info="REST/2 XS WS 03 XT-03"),
        _row("REST/2", bearer, 2, ne_info="REST/2 XS WS 03 XT-03"),
        _row("ASH/3", bearer, 1, ne_info="ASH/3 XS WS 09 XT-09"),
        _row("ASH/3", bearer, 2, ne_info="ASH/3 XS WS 09 XT-09"),
        _row("ASH/2", bearer, 1, ne_info="ASH/2 XS DTN 01 XT-01"),
        _row("ASH/2", bearer, 2, ne_info="ASH/2 XS DTN 01 XT-01"),
        _row("ASH/2", ash_trunk, 47, site_side="A"),
        _row("ASH/2", ash_trunk, 48, site_side="A"),
        _row("ASH/3", ash_trunk, 47, site_side="B"),
        _row("ASH/3", ash_trunk, 48, site_side="B"),
        _row("SCR/CS", bearer, 99, "X"),
    ]
    trunk_metadata = [
        {
            "BPK_PCG": rest_trunk,
            "A_SITE_CODE": "REST",
            "B_SITE_CODE": "REST/2",
            "MEDIA": "OL",
        },
        {
            "BPK_PCG": ash_trunk,
            "A_SITE_CODE": "ASH/2",
            "B_SITE_CODE": "ASH/3",
            "MEDIA": "OL",
        },
    ]
    transmission_metadata = [
        {
            "BPK_TRANSMISSION": rest2_to_ash3,
            "A_SITE_CODE": "ASH/3",
            "B_SITE_CODE": "REST/2",
        },
        {
            "BPK_TRANSMISSION": rest_to_ash2,
            "A_SITE_CODE": "REST",
            "B_SITE_CODE": "ASH/2",
        },
    ]
    snowflake_edges = [
        {"SERVICE_ID": service_id, "EDGE_NAME": rest_to_ash2, "LEVEL": "L1"},
        {"SERVICE_ID": service_id, "EDGE_NAME": rest2_to_ash3, "LEVEL": "L2"},
    ]
    hub_records = [
        {"SITE_CODE": "REST", "SITE_LOCATION_ID": "RES00002"},
        {"SITE_CODE": "REST/2", "SITE_LOCATION_ID": "RES00002"},
        {"SITE_CODE": "ASH/2", "SITE_LOCATION_ID": "WDC00001"},
        {"SITE_CODE": "ASH/3", "SITE_LOCATION_ID": "WDC00011"},
    ]
    tl_device_records = [
        {
            "SERVICE_ID": service_id,
            "SITE_CODE": "REST/2",
            "TL_NAME": rest2_to_ash3,
            "NE_PART": "XT-03",
        },
        {
            "SERVICE_ID": service_id,
            "SITE_CODE": "ASH/3",
            "TL_NAME": rest2_to_ash3,
            "NE_PART": "XT-09",
        },
        {
            "SERVICE_ID": service_id,
            "SITE_CODE": "ASH/2",
            "TL_NAME": rest_to_ash2,
            "NE_PART": "XT-01",
        },
    ]

    result = sort_inca_route_path(
        rows,
        service_id=service_id,
        snowflake_edge_records=snowflake_edges,
        tl_device_records=tl_device_records,
        hub_records=hub_records,
        trunk_metadata_records=trunk_metadata,
        transmission_metadata_records=transmission_metadata,
    )

    assert "Site order: REST -> ASH/2 -> REST/2 -> ASH/3 -> SCR/CS" in result.info_lines
    labels = [(row.site_code, row.ne_info, row.route_path) for row in result.rows]
    assert labels[:8] == [
        ("REST", None, rest_trunk),
        ("REST", None, rest_trunk),
        ("REST/2", None, rest_trunk),
        ("REST/2", None, rest_trunk),
        ("REST/2", "REST/2 XS WS 03 XT-03", bearer),
        ("REST/2", "REST/2 XS WS 03 XT-03", bearer),
        ("ASH/3", "ASH/3 XS WS 09 XT-09", bearer),
        ("ASH/3", "ASH/3 XS WS 09 XT-09", bearer),
    ]
    assert _is_contiguous(_passive_positions(result.rows, rest_trunk)), labels
    assert _is_contiguous(_passive_positions(result.rows, ash_trunk)), labels


def test_trunk_metadata_rank_does_not_mark_fallback_rows_canonical() -> None:
    service_id = "IC-394531"
    bearer = "REST X 90-SCR/CS X 28 100G01"
    rest_trunk = "REST U 1-REST/2 OL06"
    rows = [
        _row("REST", bearer, 1),
        _row("REST", rest_trunk, 41, "U", site_side="A"),
        _row("REST/2", rest_trunk, 41, site_side="B"),
        _row("SCR/CS", bearer, 2),
    ]
    trunk_metadata = [
        {
            "BPK_PCG": rest_trunk,
            "A_SITE_CODE": "REST",
            "B_SITE_CODE": "REST/2",
            "MEDIA": "OL",
        }
    ]

    prepared = _prepare_route_sort(
        rows,
        service_id=service_id,
        snowflake_edge_records=None,
        tl_device_records=None,
        hub_records=None,
        trunk_metadata_records=trunk_metadata,
        route_order_metadata_records=None,
        transmission_metadata_records=None,
    )

    assert prepared.trunk_route_rank == {rest_trunk: 0}
    assert not prepared.metadata_canonical_order
