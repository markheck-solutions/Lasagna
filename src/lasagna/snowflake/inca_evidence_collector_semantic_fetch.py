"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from typing import TYPE_CHECKING

from .inca_evidence_collector_context import *  # noqa: F403

if TYPE_CHECKING:
    from .inca_evidence_collector_predicate_sql import execute_count, execute_rows
    from .inca_evidence_collector_semantic_results import (
        dtn_semantic_probe_payload,
        semantic_text,
    )
    from .inca_evidence_collector_state import check_deadline


def collect_dwdm_adjacency_proofs(cursor: object, state: RunState) -> list[dict[str, object]]:
    probes: list[dict[str, object]] = []
    for service_id in state.config.semantic_service_ids:
        check_deadline(state, "write_dtn_semantic_probe", service_id)
        service_state = replace(state, config=replace(state.config, service_id=service_id))
        probes.append(collect_dtn_semantic_probe(cursor, service_state))
    return probes


def collect_dtn_semantic_probe(cursor: object, state: RunState) -> dict[str, object]:
    columns = semantic_columns_by_object(state.profiles)
    blockers = semantic_schema_blockers(columns)
    query_notes: list[dict[str, object]] = []
    if blockers:
        return dtn_semantic_probe_payload(state, {}, {}, [], blockers, query_notes)

    service_rows = semantic_fetch_service_rows(cursor, state, columns, query_notes)
    seed_values = semantic_seed_values(service_rows, columns[DTN_SEMANTIC_OBJECTS["service"]])
    cp_rows = semantic_fetch_content_position_seed_rows(
        cursor, state, columns, seed_values, query_notes
    )
    content_candidates = semantic_content_candidates(cursor, state, columns, cp_rows, query_notes)
    device_rows = semantic_fetch_device_rows(
        cursor, state, columns, content_candidates, query_notes
    )
    dtn_rows = [row for row in device_rows if semantic_row_matches_target(row, state.config)]
    connpt_ids = sorted({semantic_text(row.get("CCP__CONNPT_INT_ID")) for row in dtn_rows})
    connpt_ids = [value for value in connpt_ids if value]
    cacp_rows = semantic_fetch_cacp_rows(cursor, state, columns, connpt_ids, query_notes)
    cabpt_ids = sorted({semantic_text(row.get("CABPT_INT_ID")) for row in cacp_rows})
    cabpt_ids = [value for value in cabpt_ids if value]
    cabling_rows = semantic_fetch_cabling_rows(cursor, state, columns, cabpt_ids, query_notes)
    peer_ids = semantic_peer_cabpt_ids(cabpt_ids, cabling_rows)
    peer_cacp_rows = semantic_fetch_peer_cacp_rows(cursor, state, columns, peer_ids, query_notes)
    rows = {
        "service_transmission": service_rows,
        "content_position_seed": cp_rows,
        "content_connection_point_devices": device_rows,
        "ashr1_dtn_device_rows": dtn_rows,
        "dtn_device_rows": dtn_rows,
        "dtn_cacp_rows": cacp_rows,
        "dtn_cabling_rows": cabling_rows,
        "cabling_peer_cacp_rows": peer_cacp_rows,
    }
    seed_ids = {"CONNPT_INT_ID": connpt_ids, "CABPT_INT_ID": cabpt_ids}
    return dtn_semantic_probe_payload(
        state, rows, seed_ids, content_candidates, blockers, query_notes
    )


def semantic_columns_by_object(profiles: list[ColumnProfile]) -> dict[str, list[str]]:
    columns: dict[str, list[str]] = defaultdict(list)
    for profile in profiles:
        columns[profile.object_name].append(profile.column_name)
    return columns


def semantic_schema_blockers(columns: dict[str, list[str]]) -> list[dict[str, object]]:
    required = {
        DTN_SEMANTIC_OBJECTS["service"]: ("SERVICE_ID",),
        DTN_SEMANTIC_OBJECTS["content_position"]: (),
        DTN_SEMANTIC_OBJECTS["content_connection_point"]: ("CONTENT", "CONNPT_INT_ID"),
        DTN_SEMANTIC_OBJECTS["connection_cabling_point"]: ("CONNPT_INT_ID", "CABPT_INT_ID"),
        DTN_SEMANTIC_OBJECTS["cabling"]: ("A_CABPT_INT_ID", "B_CABPT_INT_ID"),
    }
    blockers: list[dict[str, object]] = []
    for object_name, required_columns in required.items():
        available = set(columns.get(object_name, []))
        missing = [column for column in required_columns if column not in available]
        if object_name not in columns or missing:
            blockers.append(
                {
                    "label": "semantic_probe_schema",
                    "object_name": object_name,
                    "missing_columns": missing,
                    "reason": "required object or column unavailable",
                }
            )
    return blockers


def semantic_fetch_service_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    object_name = DTN_SEMANTIC_OBJECTS["service"]
    selected = semantic_select_list("", columns[object_name])
    sql = (
        f"SELECT {selected} FROM {qualified_object(state.config.database, state.config.schema, object_name)} "
        f"WHERE {quote_identifier('SERVICE_ID')} = %s ORDER BY {quote_identifier('SERVICE_ID')} "
        "LIMIT %s OFFSET %s"
    )
    result = execute_rows(
        cursor,
        sql,
        (state.config.service_id, state.config.probe_sample_row_limit, 0),
        state,
        "write_dtn_semantic_probe",
        "semantic_service_transmission_fetch",
    )
    query_notes.append({"label": "service_transmission", "query_id": result.query_id})
    return result.rows


def semantic_seed_values(rows: list[dict[str, object]], columns: list[str]) -> list[str]:
    seed_columns = set(semantic_id_columns(columns))
    values = {
        semantic_text(value)
        for row in rows
        for column, value in row.items()
        if column in seed_columns and semantic_text(value)
    }
    return sorted(values)


def semantic_id_columns(columns: list[str]) -> list[str]:
    names = []
    for column in columns:
        if (
            column.endswith("_INT_ID")
            or column.endswith("_IDENTITY")
            or column in {"CONTENT", "TRANSMISSION_INTID", "SERVICE_ID"}
        ):
            names.append(column)
    return names


def semantic_fetch_content_position_seed_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    seed_values: list[str],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    object_name = DTN_SEMANTIC_OBJECTS["content_position"]
    selected = semantic_select_list("", columns[object_name])
    rows: list[dict[str, object]] = []
    seen_hashes: set[str] = set()
    for seed_value in seed_values:
        for column in semantic_id_columns(columns[object_name]):
            fetched = semantic_fetch_exact_text_rows(
                cursor,
                state,
                object_name,
                selected,
                column,
                seed_value,
                state.config.probe_sample_row_limit,
                f"semantic_cp_seed_{column}",
                query_notes,
            )
            for row in fetched:
                row_hash = stable_hash(json.dumps(row, sort_keys=True, default=str))
                if row_hash not in seen_hashes:
                    seen_hashes.add(row_hash)
                    rows.append(row)
    return rows


def semantic_content_candidates(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    cp_rows: list[dict[str, object]],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in cp_rows[: state.config.probe_sample_row_limit]:
        for column, value in sorted(row.items()):
            text = semantic_text(value)
            if not text or text in seen:
                continue
            seen.add(text)
            count, query_id = semantic_count_ccp_content(cursor, state, columns, text)
            query_notes.append({"label": f"ccp_content_candidate_{column}", "query_id": query_id})
            if 0 < count <= state.config.semantic_fetch_row_limit:
                candidates.append(
                    {
                        "_value": text,
                        "source_column": column,
                        "value_digest": stable_digest(text),
                        "device_count": count,
                    }
                )
    return candidates


def semantic_count_ccp_content(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    value: str,
) -> tuple[int, str]:
    object_name = DTN_SEMANTIC_OBJECTS["content_connection_point"]
    extra = semantic_ccp_device_filter(columns[object_name])
    sql = (
        "SELECT COUNT(*) AS MATCH_COUNT "
        f"FROM {qualified_object(state.config.database, state.config.schema, object_name)} "
        f"WHERE TO_VARCHAR({quote_identifier('CONTENT')}) = %s{extra}"
    )
    return execute_count(
        cursor, sql, (value,), state, "write_dtn_semantic_probe", "semantic_ccp_content_count"
    )


def semantic_fetch_device_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    candidates: list[dict[str, object]],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    object_name = DTN_SEMANTIC_OBJECTS["content_connection_point"]
    selected = semantic_device_select(columns)
    for candidate in candidates:
        value = str(candidate.get("_value", ""))
        if not value:
            continue
        extra = semantic_ccp_device_filter(columns[object_name], alias="ccp")
        sql = (
            f"SELECT {selected} FROM {semantic_device_from_clause(state, columns)} "
            f"WHERE TO_VARCHAR(ccp.{quote_identifier('CONTENT')}) = %s{extra} "
            "ORDER BY ccp."
            f"{quote_identifier('CONNPT_INT_ID')} LIMIT %s OFFSET %s"
        )
        result = execute_rows(
            cursor,
            sql,
            (value, state.config.semantic_fetch_row_limit, 0),
            state,
            "write_dtn_semantic_probe",
            "semantic_ccp_device_fetch",
        )
        query_notes.append({"label": "ccp_device_fetch", "query_id": result.query_id})
        rows.extend(result.rows)
    return rows


def semantic_select_list(alias: str, columns: list[str]) -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(f"{prefix}{quote_identifier(column)}" for column in columns)


def semantic_prefixed_select(alias: str, columns: list[str], prefix: str) -> str:
    return ", ".join(
        f"{alias}.{quote_identifier(column)} AS {quote_identifier(f'{prefix}__{column}')}"
        for column in columns
    )


def semantic_fetch_exact_text_rows(
    cursor: object,
    state: RunState,
    object_name: str,
    selected: str,
    column: str,
    value: str,
    row_limit: int,
    logical_name: str,
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    count_sql = (
        "SELECT COUNT(*) AS MATCH_COUNT "
        f"FROM {qualified_object(state.config.database, state.config.schema, object_name)} "
        f"WHERE TO_VARCHAR({quote_identifier(column)}) = %s"
    )
    count, count_query_id = execute_count(
        cursor, count_sql, (value,), state, "write_dtn_semantic_probe", f"{logical_name}_count"
    )
    query_notes.append(
        {"label": f"{logical_name}_count", "query_id": count_query_id, "count": count}
    )
    if count <= 0 or count > row_limit:
        return []
    fetch_sql = (
        f"SELECT {selected} "
        f"FROM {qualified_object(state.config.database, state.config.schema, object_name)} "
        f"WHERE TO_VARCHAR({quote_identifier(column)}) = %s "
        f"ORDER BY {quote_identifier(column)} LIMIT %s OFFSET %s"
    )
    result = execute_rows(
        cursor,
        fetch_sql,
        (value, row_limit, 0),
        state,
        "write_dtn_semantic_probe",
        f"{logical_name}_fetch",
    )
    query_notes.append({"label": f"{logical_name}_fetch", "query_id": result.query_id})
    return result.rows


def semantic_ccp_device_filter(ccp_columns: list[str], alias: str = "") -> str:
    if "NE" in ccp_columns:
        prefix = f"{alias}." if alias else ""
        return f" AND {prefix}{quote_identifier('NE')} IS NOT NULL"
    return ""


def semantic_device_select(columns: dict[str, list[str]]) -> str:
    ccp_columns = [
        column
        for column in columns[DTN_SEMANTIC_OBJECTS["content_connection_point"]]
        if column
        in {
            "CONTENT",
            "CONTENT_INT_ID",
            "CONNPT_INT_ID",
            "NE",
            "NE_PART",
            "SITE_CODE",
            "SLOT",
            "SUBSLOT",
            "CONNECTION_POINT_NR",
            "CONNECTION_POINT_TYPE",
            "PORT_TYPE",
        }
    ]
    selected = [semantic_prefixed_select("ccp", ccp_columns, "CCP")]
    ne_part_columns = columns.get(DTN_SEMANTIC_OBJECTS["ne_part"], [])
    if semantic_ne_part_join_allowed(columns):
        selected.append(
            semantic_prefixed_select(
                "nep",
                [
                    column
                    for column in ne_part_columns
                    if column
                    in {
                        "NE",
                        "NE_PART_NAME",
                        "NEPART_SITE_CODE",
                        "NE_TYPE",
                        "NE_PART_TYPE",
                        "MODEL",
                        "TECHNOLOGY",
                    }
                ],
                "NEP",
            )
        )
    return ", ".join(part for part in selected if part)


def semantic_device_from_clause(state: RunState, columns: dict[str, list[str]]) -> str:
    ccp_object = qualified_object(
        state.config.database, state.config.schema, DTN_SEMANTIC_OBJECTS["content_connection_point"]
    )
    if not semantic_ne_part_join_allowed(columns):
        return f"{ccp_object} ccp"
    nep_object = qualified_object(
        state.config.database, state.config.schema, DTN_SEMANTIC_OBJECTS["ne_part"]
    )
    return (
        f"{ccp_object} ccp LEFT JOIN {nep_object} nep "
        f"ON ccp.{quote_identifier('NE')} = nep.{quote_identifier('NE')} "
        f"AND ccp.{quote_identifier('NE_PART')} = nep.{quote_identifier('NE_PART_NAME')}"
    )


def semantic_ne_part_join_allowed(columns: dict[str, list[str]]) -> bool:
    ccp_columns = set(columns.get(DTN_SEMANTIC_OBJECTS["content_connection_point"], []))
    nep_columns = set(columns.get(DTN_SEMANTIC_OBJECTS["ne_part"], []))
    return {"NE", "NE_PART"} <= ccp_columns and {"NE", "NE_PART_NAME"} <= nep_columns


def semantic_row_matches_target(row: dict[str, object], config: LiveConfig) -> bool:
    site = semantic_text(row.get("NEP__NEPART_SITE_CODE")) or semantic_text(
        row.get("CCP__SITE_CODE")
    )
    haystack = " ".join(semantic_text(value) for value in row.values())
    return semantic_site_matches_target(site, config.semantic_site_code) and (
        config.semantic_device_token.upper() in haystack.upper()
    )


def semantic_site_matches_target(site: str, target_site: str) -> bool:
    normalized = target_site.strip().upper()
    if normalized in {"", "*", "ANY", "ALL"}:
        return True
    return site.upper() == normalized


def semantic_fetch_cacp_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    connpt_ids: list[str],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    object_name = DTN_SEMANTIC_OBJECTS["connection_cabling_point"]
    return semantic_fetch_in_rows(
        cursor,
        state,
        object_name,
        columns[object_name],
        "CONNPT_INT_ID",
        connpt_ids,
        "semantic_dtn_cacp_by_connpt",
        query_notes,
    )


def semantic_fetch_cabling_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    cabpt_ids: list[str],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not cabpt_ids:
        return []
    object_name = DTN_SEMANTIC_OBJECTS["cabling"]
    placeholders = semantic_placeholders(len(cabpt_ids))
    selected = semantic_select_list("", columns[object_name])
    where_sql = (
        f"TO_VARCHAR({quote_identifier('A_CABPT_INT_ID')}) IN ({placeholders}) "
        f"OR TO_VARCHAR({quote_identifier('B_CABPT_INT_ID')}) IN ({placeholders})"
    )
    return semantic_fetch_where_rows(
        cursor,
        state,
        object_name,
        selected,
        where_sql,
        (*cabpt_ids, *cabpt_ids),
        "semantic_dtn_cabling_by_cabpt",
        query_notes,
    )


def semantic_peer_cabpt_ids(
    cabpt_ids: list[str], cabling_rows: list[dict[str, object]]
) -> list[str]:
    ids = set(cabpt_ids)
    for row in cabling_rows:
        for column in ("A_CABPT_INT_ID", "B_CABPT_INT_ID"):
            value = semantic_text(row.get(column))
            if value:
                ids.add(value)
    return sorted(ids)


def semantic_fetch_peer_cacp_rows(
    cursor: object,
    state: RunState,
    columns: dict[str, list[str]],
    cabpt_ids: list[str],
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    object_name = DTN_SEMANTIC_OBJECTS["connection_cabling_point"]
    return semantic_fetch_in_rows(
        cursor,
        state,
        object_name,
        columns[object_name],
        "CABPT_INT_ID",
        cabpt_ids,
        "semantic_cabling_peer_cacp_by_cabpt",
        query_notes,
    )


def semantic_fetch_in_rows(
    cursor: object,
    state: RunState,
    object_name: str,
    columns: list[str],
    column: str,
    values: list[str],
    logical_name: str,
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not values:
        return []
    selected = semantic_select_list("", columns)
    placeholders = semantic_placeholders(len(values))
    where_sql = f"TO_VARCHAR({quote_identifier(column)}) IN ({placeholders})"
    return semantic_fetch_where_rows(
        cursor, state, object_name, selected, where_sql, tuple(values), logical_name, query_notes
    )


def semantic_fetch_where_rows(
    cursor: object,
    state: RunState,
    object_name: str,
    selected: str,
    where_sql: str,
    params: tuple[object, ...],
    logical_name: str,
    query_notes: list[dict[str, object]],
) -> list[dict[str, object]]:
    qualified = qualified_object(state.config.database, state.config.schema, object_name)
    count, count_query_id = execute_count(
        cursor,
        f"SELECT COUNT(*) AS MATCH_COUNT FROM {qualified} WHERE {where_sql}",
        params,
        state,
        "write_dtn_semantic_probe",
        f"{logical_name}_count",
    )
    query_notes.append(
        {"label": f"{logical_name}_count", "query_id": count_query_id, "count": count}
    )
    if count <= 0 or count > state.config.semantic_fetch_row_limit:
        return []
    result = execute_rows(
        cursor,
        f"SELECT {selected} FROM {qualified} WHERE {where_sql} LIMIT %s OFFSET %s",
        (*params, state.config.semantic_fetch_row_limit, 0),
        state,
        "write_dtn_semantic_probe",
        f"{logical_name}_fetch",
    )
    query_notes.append({"label": f"{logical_name}_fetch", "query_id": result.query_id})
    return result.rows


def semantic_placeholders(count: int) -> str:
    return ", ".join("%s" for _ in range(count))
