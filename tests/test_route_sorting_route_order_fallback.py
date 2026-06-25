from lasagna.route_sorting.inca_sorter.models import InCARow
from lasagna.route_sorting.inca_sorter.sorting import sort_inca_route_path


def _row(site_code: str, route_path: str, pos: int, site_type: str = "XS") -> InCARow:
    return InCARow(
        site_code=site_code,
        site_type=site_type,
        ne_info=None,
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
