import csv
import json
from pathlib import Path

from lasagna.route_sorting.combined_parser import read_snowflake_combined_csv


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
