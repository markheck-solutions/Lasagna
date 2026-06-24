from lasagna.route_sorting.adapter import build_site_location_lookup, route_row_from_inca
from lasagna.route_sorting.inca_sorter.models import InCARow


def test_route_row_from_inca_maps_to_lasagna_18_column_contract() -> None:
    row = InCARow(
        site_code="ALPHA",
        site_type="XS",
        ne_info="alpha-router NCS-5508",
        cabling_location="NE-location: [BLDG]A/01/RU01/.",
        cabling_points="01 Cable.01",
        conn_type="LC",
        location_alias="Alpha alias",
        route_path="ALPHA-BETA OL01",
        pos=1,
        status_o_time=None,
        o_time=None,
        status_t_time="Planned",
        t_time="2026-01-01",
        comment="synthetic",
        row_index=1,
        site_type_no="107",
    )

    workbook_row = route_row_from_inca(row, {"ALPHA": "LOC-ALPHA"})

    assert workbook_row.values() == (
        "LOC-ALPHA",
        "ALPHA",
        "XS",
        "107",
        "alpha-router NCS-5508",
        "NE-location: [BLDG]A/01/RU01/.",
        "01 Cable.01",
        "LC",
        "Alpha alias",
        "",
        "ALPHA-BETA OL01",
        "1",
        "",
        "",
        "",
        "Planned",
        "2026-01-01",
        "synthetic",
    )


def test_build_site_location_lookup_prefers_route_order_metadata() -> None:
    lookup = build_site_location_lookup(
        [{"SITE_CODE": "ALPHA", "SITE_LOCATION_ID": "OLD"}],
        [
            {
                "A_SITE_CODE": "ALPHA",
                "A_SITE_LOCATION_ID": "NEW",
                "B_SITE_CODE": "BETA",
                "B_SITE_LOCATION_ID": "BETA-LOC",
            }
        ],
    )

    assert lookup == {"ALPHA": "NEW", "BETA": "BETA-LOC"}
