import csv
import json
from pathlib import Path

from lasagna.route_sorting.combined_parser import read_snowflake_combined_csv
from lasagna.route_sorting.combined_results import _sort_rows_by_structured_contract


def _write_combined_csv(path: Path, rows: list[tuple[str, dict[str, object]]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["QID", "ROW_DATA"])
        for qid, row_data in rows:
            writer.writerow([qid, json.dumps(row_data)])
    return path


def test_combined_parser_builds_rows_and_metadata_buckets(tmp_path: Path) -> None:
    service_id = "IC-123456"
    combined_csv = _write_combined_csv(
        tmp_path / "combined.csv",
        [
            (
                "TRUNK_ODF",
                {
                    "SERVICE_ID": service_id,
                    "SITE_CODE": "AAA",
                    "SITE_TYPE": "XS",
                    "SITE_TYPE_NO": "1",
                    "CABLING_LOCATION": "[AAA]01/R01/RU01/.",
                    "CABLING_POINTS": "01 Cable.01",
                    "CONN_TYPE": "LC",
                    "ROUTE_PATH": "AAA-BBB OL01",
                    "POS": 1,
                    "SITE_SIDE": "A",
                },
            ),
            (
                "ODUC",
                {
                    "SITE_CODE": "AAA",
                    "NE": "aaa-router",
                    "CHASSIS_FUNCTION": "NCS-5508",
                },
            ),
            (
                "DEVICE",
                {
                    "SERVICE_ID": service_id,
                    "SITE_CODE": "AAA",
                    "SITE_TYPE": "XS",
                    "SITE_TYPE_NO": "1",
                    "NE": "aaa-router",
                    "NE_PART": "NCS-5508",
                    "OPTIC_FUNCTION": "QDD-400G-LR4-S",
                    "DEVICE_LOCATION": "[AAA]01/R01::0/0/0:13",
                    "CONNECTION_POINT_NR": "01",
                    "DIRECTION": "Tx",
                    "ROUTE_PATH": "AAA-BBB 400G01",
                    "POS": 2,
                    "NE_TYPE": "NCS-5508",
                    "NE_FUNCTION": "ROUTER",
                    "SLOT": "0/0/0",
                    "SUBSLOT": "13",
                },
            ),
            (
                "DP_SDP",
                {
                    "SERVICE_ID": service_id,
                    "SITE_CODE": "BBB",
                    "SITE_TYPE": "XS",
                    "SITE_TYPE_NO": "1",
                    "NE_INFORMATION": "DP old",
                    "FUNCTION": "ODF",
                    "CABLING_LOCATION": "[BBB]01/R01/RU01/.",
                    "CABLING_POINTS": "02 Cable.02",
                    "CONN_TYPE": "LC",
                    "ROUTE_PATH": "Demarcation point: BBB XS pos 2",
                    "POS": 2,
                    "DP_OWNER": "ARELION",
                },
            ),
            (
                "ROUTE_ORDER_METADATA",
                {
                    "SERVICE_ID": service_id,
                    "ROUTE_PATH": "AAA-BBB OL01",
                    "EDGE_SEQUENCE": 1,
                    "A_SITE_CODE": "AAA",
                    "B_SITE_CODE": "BBB",
                    "MEDIA": "OL",
                },
            ),
            (
                "TRANSPORT_DEVICE_ADJACENCY",
                {
                    "SERVICE_ID": service_id,
                    "EDGE_NAME": "AAA-BBB OL01",
                    "ENDPOINT_1_SITE_CODE": "AAA",
                    "ENDPOINT_2_SITE_CODE": "BBB",
                    "ENDPOINT_PROOF_SOURCE": "EXACT_DEVICE_PORT_MATCH",
                },
            ),
            (
                "DP_ENDPOINT_ROLE",
                {
                    "SERVICE_ID": service_id,
                    "DP_ROUTE_PATH": "Demarcation point: BBB XS",
                    "SITE_CODE": "BBB",
                    "SITE_TYPE": "XS",
                    "SITE_TYPE_NO": "1",
                    "POS": 2,
                    "CABLING_POINTS": "02 Cable.02",
                    "CONN_TYPE": "LC",
                    "MATCHED_ROUTE_PATH": "AAA-BBB OL01",
                    "MATCHED_SITE_SIDE": "B",
                },
            ),
        ],
    )

    parsed = read_snowflake_combined_csv(str(combined_csv))

    assert len(parsed.services[service_id]) == 3
    assert parsed.services[service_id][0].site_side == "A"
    assert parsed.services[service_id][1].ne_info == (
        "aaa-router NCS-5508 -NCS-5508\\QDD-400G-LR4-S -(0/0/0\\13.01:Tx)"
    )
    assert parsed.services[service_id][1].direction == "Tx"
    assert parsed.services[service_id][2].route_path == "Demarcation point: BBB XS"
    assert parsed.route_order_metadata[0]["ROUTE_PATH"] == "AAA-BBB OL01"
    assert parsed.transport_device_adjacency[0]["ENDPOINT_PROOF_SOURCE"] == (
        "EXACT_DEVICE_PORT_MATCH"
    )
    assert parsed.dp_endpoint_roles[0]["MATCHED_SITE_SIDE"] == "B"


def test_combined_parser_does_not_rewrite_br_dp_role_at_xs_device_site(
    tmp_path: Path,
) -> None:
    service_id = "ICB-823999"
    bearer = "AAA BR 1-BBB BR 2 100G01"
    dp_route = "Demarcation point: BBB BR"
    combined_csv = _write_combined_csv(
        tmp_path / "combined.csv",
        [
            (
                "DEVICE",
                {
                    "SERVICE_ID": service_id,
                    "SITE_CODE": "AAA",
                    "SITE_TYPE": "BR",
                    "SITE_TYPE_NO": "1",
                    "NE": "aaa-br",
                    "NE_PART": "1",
                    "OPTIC_FUNCTION": "QSFP-100G-LR4",
                    "DEVICE_LOCATION": "[AAA]01/R01::1/1/1:1",
                    "CONNECTION_POINT_NR": "01",
                    "DIRECTION": "Tx",
                    "ROUTE_PATH": bearer,
                    "POS": 1,
                    "NE_TYPE": "G30",
                    "NE_FUNCTION": "TRANSPORT",
                    "SLOT": "1",
                    "SUBSLOT": "1",
                },
            ),
            (
                "DEVICE",
                {
                    "SERVICE_ID": service_id,
                    "SITE_CODE": "BBB",
                    "SITE_TYPE": "XS",
                    "SITE_TYPE_NO": "7",
                    "NE": "bbb-xs",
                    "NE_PART": "1",
                    "OPTIC_FUNCTION": "QSFP-100G-LR4",
                    "DEVICE_LOCATION": "[BBB]01/R01::1/1/2:1",
                    "CONNECTION_POINT_NR": "01",
                    "DIRECTION": "Rx",
                    "ROUTE_PATH": bearer,
                    "POS": 2,
                    "NE_TYPE": "G30",
                    "NE_FUNCTION": "TRANSPORT",
                    "SLOT": "1",
                    "SUBSLOT": "2",
                },
            ),
            (
                "DP_SDP",
                {
                    "SERVICE_ID": service_id,
                    "SITE_CODE": "BBB",
                    "SITE_TYPE": "BR",
                    "SITE_TYPE_NO": "2",
                    "NE_INFORMATION": "DP old",
                    "FUNCTION": "ODF",
                    "CABLING_LOCATION": "[BBB]01/R01/RU01/.",
                    "CABLING_POINTS": "09 Cable",
                    "CONN_TYPE": "LC/UPC",
                    "ROUTE_PATH": f"{dp_route} pos 9",
                    "POS": 9,
                },
            ),
            (
                "ROUTE_ORDER_METADATA",
                {
                    "SERVICE_ID": service_id,
                    "ROUTE_PATH": bearer,
                    "EDGE_SEQUENCE": 1,
                    "EDGE_NAME": bearer,
                    "A_SITE_CODE": "AAA",
                    "B_SITE_CODE": "BBB",
                    "A_SITE_LOCATION_ID": "LOC-A",
                    "B_SITE_LOCATION_ID": "LOC-B",
                    "A_SITE_SIDE": "A",
                    "B_SITE_SIDE": "B",
                    "MEDIA": "ET",
                },
            ),
            (
                "DP_ENDPOINT_ROLE",
                {
                    "SERVICE_ID": service_id,
                    "DP_ROUTE_PATH": dp_route,
                    "SITE_CODE": "BBB",
                    "SITE_TYPE": "BR",
                    "SITE_TYPE_NO": "2",
                    "POS": 9,
                    "CABLING_POINTS": "09 Cable",
                    "CONN_TYPE": "LC/UPC",
                    "MATCHED_ROUTE_PATH": bearer,
                    "MATCHED_SITE_SIDE": "B",
                    "ENDPOINT_PROOF_SOURCE": "DP_EXACT_SITE_IDENTITY",
                },
            ),
        ],
    )

    parsed = read_snowflake_combined_csv(str(combined_csv))
    demarcation = next(row for row in parsed.services[service_id] if row.is_demarcation)

    assert (demarcation.site_type, demarcation.site_type_no) == ("BR", "2")
    sorted_rows = _sort_rows_by_structured_contract(
        parsed.services[service_id],
        parsed.route_order_metadata,
        service_id,
        dp_endpoint_roles=parsed.dp_endpoint_roles,
    )
    assert sorted_rows[-1].route_path == dp_route
