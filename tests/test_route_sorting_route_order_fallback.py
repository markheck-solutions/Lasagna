from lasagna.route_sorting.inca_sorter.models import InCARow
from lasagna.route_sorting.inca_sorter.sorting import (
    _interleave_inter_site_trunk_pairs,
    _prepare_route_sort,
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
