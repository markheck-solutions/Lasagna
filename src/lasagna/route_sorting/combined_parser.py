"""Combined Snowflake QID/ROW_DATA parser for route workbook inputs."""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from lasagna.route_sorting.route_rows import InCARow, SnowflakeCombinedData

SNOWFLAKE_ROUTE_QIDS = {
    "TRUNK_ODF",
    "DEVICE",
    "ODUC",
    "DP_SDP",
    "EDGES",
    "HUB_SITE",
    "SITE_METADATA",
    "TRUNK_METADATA",
    "ROUTE_ORDER_METADATA",
    "TRANSMISSION_METADATA",
    "TRANSPORT_DEVICE_ADJACENCY",
    "DP_ENDPOINT_ROLE",
    "BO_FIBERS",
}

SDM_WORKSPACE_QIDS = {
    "00_COHORT_COUNT",
    "00_RUN_METADATA",
    "01_LEGACY_QUEUE",
    "02_ENRICHED_QUEUE",
    "04_SUMMARY",
    "05_ALL_WOS",
    "06_ALL_ASSIGNMENTS",
    "07_ALL_TPS",
    "08_FS_WO_DETAIL",
    "09B_SERVICE_FAMILY_DISCOVERY",
    "09_FS_COLUMN_DISCOVERY",
    "10_TPS_STATUS_HISTORY",
    "10_ASSIGNMENT_STATUS_HISTORY",
    "10_WO_STATUS_HISTORY",
    "10B_ALL_HISTORY_TABLES",
    "10_HISTORY_COLUMNS",
    "10_HISTORY_TABLE_DISCOVERY",
}
SDM_WORKSPACE_QID_PREFIXES = ("COMBINED_", "GEO-", "ASCOPE_", "ITEM")


def _is_sdm_workspace_qid(qid: str) -> bool:
    return qid in SDM_WORKSPACE_QIDS or qid.startswith(SDM_WORKSPACE_QID_PREFIXES)


def _safe_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: object) -> int:
    if value is None or not isinstance(value, str | int | float):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _csv_optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _has_usable_cabling_points(value: str) -> bool:
    normalized = value.strip().upper()
    return bool(normalized and normalized not in {"NA", "N/A"})


def _split_device_location(device_location: str) -> tuple[str, str]:
    bracket_end = device_location.rfind("]")
    if bracket_end < 0:
        return (device_location, "")
    after_bracket = device_location[bracket_end + 1 :]
    if "::" in after_bracket:
        location_prefix, port_address = after_bracket.split("::", 1)
        return (device_location[: bracket_end + 1] + location_prefix, port_address)
    last_colon = after_bracket.rfind(":")
    if last_colon >= 0:
        return (
            device_location[: bracket_end + 1] + after_bracket[:last_colon],
            after_bracket[last_colon + 1 :],
        )
    return (device_location, "")


def _build_ne_information(
    ne_name: str,
    ne_part: str,
    optic_function: str,
    device_location: str,
    connection_point_nr: str,
    direction: str,
    chassis_function: str | None = None,
) -> str:
    _, port_address = _split_device_location(device_location)
    if "::" in device_location:
        last_colon = port_address.rfind(":")
        if last_colon >= 0:
            port_address = port_address[:last_colon] + "\\" + port_address[last_colon + 1 :]

    chassis = chassis_function if chassis_function else ne_part
    return (
        f"{ne_name} {ne_part} -{chassis}\\{optic_function} "
        f"-({port_address}.{connection_point_nr}:{direction})"
    )


_PLANNED_DEVICE_LOCATION = "NE-location: (planned device, not yet installed)"
_MISSING_DEVICE_LOCATION = "NE-location: (no BO ODF location in source data)"
_MISSING_BO_ODF_LOCATION = "BO ODF location missing from Snowflake export"


def _make_snowflake_trunk_row(
    record: dict[str, Any], row_index: int, service_id: str | None = None
) -> InCARow:
    row = InCARow(
        site_code=_safe_str(record.get("SITE_CODE")),
        site_type=_safe_str(record.get("SITE_TYPE")),
        ne_info=None,
        cabling_location=_safe_str(record.get("CABLING_LOCATION")),
        cabling_points=_safe_str(record.get("CABLING_POINTS")),
        conn_type=_safe_str(record.get("CONN_TYPE")),
        location_alias=_csv_optional(record.get("LOCATION_ALIAS")),
        route_path=_safe_str(record.get("ROUTE_PATH")),
        pos=_safe_int(record.get("POS", 0)),
        status_o_time=_csv_optional(record.get("STATUS_O_TIME")),
        o_time=_csv_optional(record.get("O_TIME")),
        status_t_time=_csv_optional(record.get("STATUS_T_TIME")),
        t_time=_csv_optional(record.get("T_TIME")),
        comment=_csv_optional(record.get("COMMENT")),
        row_index=row_index,
        service_id=service_id,
        site_type_no=_safe_str(record.get("SITE_TYPE_NO")),
        floor=_safe_str(record.get("FLOOR")),
        room=_safe_str(record.get("ROOM")),
        row=_safe_str(record.get("ROW_")),
        rowside=_safe_str(record.get("ROWSIDE")),
        rack=_safe_str(record.get("RACK")),
        shelf=_safe_str(record.get("SHELF")),
        subrack=_safe_str(record.get("SUBRACK")),
        connection_point_nr=_safe_str(record.get("CONNECTION_POINT_NR")),
    )
    row.site_side = _csv_optional(record.get("SITE_SIDE"))
    return row


def _location_has_meaningful_building_data(location_prefix: str) -> bool:
    cleaned = location_prefix
    for char in "/.:\\- 0123456789":
        cleaned = cleaned.replace(char, "")
    return bool(cleaned.strip())


def _resolve_device_ne_location_label(location: str) -> str | None:
    location_prefix, _ = _split_device_location(location)
    if not _location_has_meaningful_building_data(location_prefix):
        return None
    return f"NE-location: {location_prefix}"


def _fallback_device_ne_location(record: dict[str, Any], device_location: str) -> str:
    for location in (device_location, _safe_str(record.get("NEP_LOCATION"))):
        label = _resolve_device_ne_location_label(location)
        if label:
            return label

    status = _safe_str(record.get("STATUS_O_TIME"))
    if status.lower() == "planned":
        return _PLANNED_DEVICE_LOCATION
    return _MISSING_DEVICE_LOCATION


def _resolve_device_cabling_fields(
    record: dict[str, Any], device_location: str
) -> tuple[str, str, str]:
    cabling_location = _safe_str(record.get("CABLING_LOCATION"))
    if cabling_location:
        return (
            cabling_location,
            _safe_str(record.get("CABLING_POINTS")),
            _safe_str(record.get("CONN_TYPE")),
        )

    cabling_points = _safe_str(record.get("CABLING_POINTS"))
    conn_type = _safe_str(record.get("CONN_TYPE"))
    if _has_usable_cabling_points(cabling_points) and conn_type:
        return (_MISSING_BO_ODF_LOCATION, cabling_points, conn_type)

    return (_fallback_device_ne_location(record, device_location), "NA", "")


def _make_snowflake_device_row(
    record: dict[str, Any],
    row_index: int,
    chassis_lookup: dict[tuple[str, str], str],
    service_id: str | None = None,
) -> InCARow:
    site_code = _safe_str(record.get("SITE_CODE"))
    ne_name = _safe_str(record.get("NE"))
    ne_part = _safe_str(record.get("NE_PART"))
    optic_function = _safe_str(record.get("OPTIC_FUNCTION"))
    device_location = _safe_str(record.get("DEVICE_LOCATION"))
    connection_point_nr = _safe_str(record.get("CONNECTION_POINT_NR"))
    direction = _safe_str(record.get("DIRECTION"))
    cabling_location, cabling_points, conn_type = _resolve_device_cabling_fields(
        record, device_location
    )

    return InCARow(
        site_code=site_code,
        site_type=_safe_str(record.get("SITE_TYPE")),
        ne_info=_build_ne_information(
            ne_name,
            ne_part,
            optic_function,
            device_location,
            connection_point_nr,
            direction,
            chassis_lookup.get((site_code, ne_name)),
        ),
        cabling_location=cabling_location,
        cabling_points=cabling_points,
        conn_type=conn_type,
        location_alias=_csv_optional(record.get("LOCATION_ALIAS")),
        route_path=_safe_str(record.get("ROUTE_PATH")),
        pos=_safe_int(record.get("POS", 0)),
        status_o_time=_csv_optional(record.get("STATUS_O_TIME")),
        o_time=_csv_optional(record.get("O_TIME")),
        status_t_time=_csv_optional(record.get("STATUS_T_TIME")),
        t_time=_csv_optional(record.get("T_TIME")),
        comment=_csv_optional(record.get("COMMENT")),
        row_index=row_index,
        service_id=service_id,
        site_type_no=_safe_str(record.get("SITE_TYPE_NO")),
        floor=_safe_str(record.get("BO_FLOOR", record.get("FLOOR", ""))),
        room=_safe_str(record.get("BO_ROOM", record.get("ROOM", ""))),
        row=_safe_str(record.get("BO_ROW", record.get("ROW_", ""))),
        rowside=_safe_str(record.get("BO_ROWSIDE", record.get("ROWSIDE", ""))),
        rack=_safe_str(record.get("BO_RACK", record.get("RACK", ""))),
        shelf=_safe_str(record.get("BO_SHELF", record.get("SHELF", ""))),
        subrack=_safe_str(record.get("BO_SUBRACK", record.get("SUBRACK", ""))),
        connection_point_nr=connection_point_nr,
        ne_type=_safe_str(record.get("NE_TYPE")),
        ne_function=_safe_str(record.get("NE_FUNCTION")),
        slot=_safe_str(record.get("SLOT")),
        subslot=_safe_str(record.get("SUBSLOT")),
        direction=direction,
    )


_DEMARC_POS_SUFFIX = re.compile(r"\s+pos\s+\d+$", re.IGNORECASE)


def _dp_sdp_ne_information(record: dict[str, Any]) -> str | None:
    raw = _safe_str(record.get("NE_INFORMATION"))
    function = _safe_str(record.get("FUNCTION"))
    if not raw or not function:
        return raw or None
    parts = raw.split()
    if len(parts) >= 2 and parts[0] in {"DP", "SDP"}:
        return f"{parts[0]} {function}"
    return raw


def _make_snowflake_dp_sdp_row(
    record: dict[str, Any], row_index: int, service_id: str | None = None
) -> InCARow:
    route_path = _DEMARC_POS_SUFFIX.sub("", _safe_str(record.get("ROUTE_PATH")))
    return InCARow(
        site_code=_safe_str(record.get("SITE_CODE")),
        site_type=_safe_str(record.get("SITE_TYPE")),
        ne_info=_dp_sdp_ne_information(record),
        cabling_location=_safe_str(record.get("CABLING_LOCATION")),
        cabling_points=_safe_str(record.get("CABLING_POINTS")),
        conn_type=_safe_str(record.get("CONN_TYPE")),
        location_alias=_csv_optional(record.get("LOCATION_ALIAS")),
        route_path=route_path,
        pos=_safe_int(record.get("POS", 0)),
        status_o_time=_csv_optional(record.get("STATUS_O_TIME")),
        o_time=_csv_optional(record.get("O_TIME")),
        status_t_time=_csv_optional(record.get("STATUS_T_TIME")),
        t_time=_csv_optional(record.get("T_TIME")),
        comment=_csv_optional(record.get("COMMENT")),
        row_index=row_index,
        service_id=service_id,
        dp_owner=_csv_optional(record.get("DP_OWNER")),
        site_type_no=_safe_str(record.get("SITE_TYPE_NO")),
    )


def _normalize_bearer_endpoint_device_site_types(
    services: dict[str, list[InCARow]],
) -> None:
    for rows in services.values():
        xs_sites = {row.site_code for row in rows if row.site_type == "XS"}
        if not xs_sites:
            continue
        for row in rows:
            if row.site_type == "BR" and row.site_code in xs_sites and row.is_device_row:
                row.site_type = "XS"
                row.site_type_no = ""


def _warn_unexpected_combined_header(header: list[str]) -> None:
    header_upper = [item.strip().upper() for item in header]
    if header_upper == ["QID", "ROW_DATA"]:
        return
    print(
        f"WARNING: Unexpected CSV header {header}, expected ['QID', 'ROW_DATA']",
        file=sys.stderr,
    )


def _parse_combined_row_data(raw_json: str, qid: str, line_num: int) -> dict[str, Any] | None:
    if not raw_json:
        print(f"WARNING: Empty ROW_DATA at line {line_num} (QID={qid}), skipping", file=sys.stderr)
        return None
    try:
        record = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        print(f"WARNING: Invalid JSON at line {line_num} (QID={qid}): {exc}", file=sys.stderr)
        return None
    if not isinstance(record, dict):
        print(
            f"WARNING: ROW_DATA at line {line_num} (QID={qid}) is not an object, skipping",
            file=sys.stderr,
        )
        return None
    return record


def _group_combined_record(
    by_qid: dict[str, list[dict[str, Any]]],
    skipped_qids: set[str],
    qid: str,
    record: dict[str, Any],
) -> None:
    if qid in SNOWFLAKE_ROUTE_QIDS:
        by_qid[qid].append(record)
    elif not _is_sdm_workspace_qid(qid):
        skipped_qids.add(qid)


def _read_combined_qid_groups(
    filepath: str,
) -> tuple[dict[str, list[dict[str, Any]]], set[str]] | None:
    by_qid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_qids: set[str] = set()

    with open(filepath, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            print("WARNING: Empty combined CSV file", file=sys.stderr)
            return None

        _warn_unexpected_combined_header(header)

        for line_num, csv_row in enumerate(reader, start=2):
            if len(csv_row) < 2:
                continue
            qid = csv_row[0].strip()
            record = _parse_combined_row_data(csv_row[1].strip(), qid, line_num)
            if record is not None:
                _group_combined_record(by_qid, skipped_qids, qid, record)

    return by_qid, skipped_qids


def _build_combined_chassis_lookup(
    oduc_records: list[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    chassis_lookup: dict[tuple[str, str], str] = {}
    for record in oduc_records:
        site_code = _safe_str(record.get("SITE_CODE"))
        ne_name = _safe_str(record.get("NE"))
        chassis_function = _safe_str(record.get("CHASSIS_FUNCTION"))
        if site_code and ne_name and chassis_function:
            chassis_lookup[(site_code, ne_name)] = chassis_function
    return chassis_lookup


def _append_combined_service_rows(
    services: dict[str, list[InCARow]],
    records: list[dict[str, Any]],
    row_index: int,
    row_factory: Callable[[dict[str, Any], int, str], InCARow],
) -> int:
    for record in records:
        service_id = _safe_str(record.get("SERVICE_ID"))
        if service_id:
            services[service_id].append(row_factory(record, row_index, service_id))
            row_index += 1
    return row_index


def _build_combined_services(
    by_qid: dict[str, list[dict[str, Any]]],
    chassis_lookup: dict[tuple[str, str], str],
) -> dict[str, list[InCARow]]:
    services: dict[str, list[InCARow]] = defaultdict(list)
    row_index = 1
    row_index = _append_combined_service_rows(
        services,
        by_qid.get("TRUNK_ODF", []),
        row_index,
        _make_snowflake_trunk_row,
    )
    row_index = _append_combined_service_rows(
        services,
        by_qid.get("DEVICE", []),
        row_index,
        lambda record, index, service_id: _make_snowflake_device_row(
            record,
            index,
            chassis_lookup,
            service_id=service_id,
        ),
    )
    _append_combined_service_rows(
        services,
        by_qid.get("DP_SDP", []),
        row_index,
        _make_snowflake_dp_sdp_row,
    )
    return dict(services)


def read_snowflake_combined_csv(filepath: str) -> SnowflakeCombinedData:
    """Read combined Snowflake export in QID,ROW_DATA format."""
    grouped_rows = _read_combined_qid_groups(filepath)
    if grouped_rows is None:
        return SnowflakeCombinedData(services={}, edge_records=[])
    by_qid, skipped_qids = grouped_rows

    if skipped_qids:
        print(f"WARNING: Skipped unknown QID values: {sorted(skipped_qids)}", file=sys.stderr)

    chassis_lookup = _build_combined_chassis_lookup(by_qid.get("ODUC", []))
    services = _build_combined_services(by_qid, chassis_lookup)
    _normalize_bearer_endpoint_device_site_types(services)

    return SnowflakeCombinedData(
        services=services,
        edge_records=by_qid.get("EDGES", []),
        hub_records=by_qid.get("SITE_METADATA", []) or by_qid.get("HUB_SITE", []),
        trunk_metadata=by_qid.get("TRUNK_METADATA", []),
        route_order_metadata=by_qid.get("ROUTE_ORDER_METADATA", []),
        transmission_metadata=by_qid.get("TRANSMISSION_METADATA", []),
        transport_device_adjacency=by_qid.get("TRANSPORT_DEVICE_ADJACENCY", []),
        dp_endpoint_roles=by_qid.get("DP_ENDPOINT_ROLE", []),
        bo_fibers=by_qid.get("BO_FIBERS", []),
    )
