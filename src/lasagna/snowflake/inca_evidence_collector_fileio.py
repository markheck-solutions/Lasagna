"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from typing import TYPE_CHECKING

from .inca_evidence_collector_context import *  # noqa: F403

if TYPE_CHECKING:
    from .inca_evidence_collector_probe_snapshots import probe_limits
    from .inca_evidence_collector_state import json_load_or_empty


def write_jsonl_artifact(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True))
            handle.write("\n")


def write_source_manifest(state: RunState) -> None:
    write_json_artifact(
        state.run_dir / "source_manifest.json",
        source_manifest_payload(
            run_id=state.config.run_id,
            source_kind="snowflake",
            source_name=f"{state.config.database}.{state.config.schema}",
            auth_context={
                "connection_name": state.config.connection_name,
                "secret_material_written": False,
                "semantic_service_ids": list(state.config.semantic_service_ids),
            },
            tool_version=state.config.framework_commit_sha,
            limits=probe_limits(state.config),
        ),
    )


def write_profile_snapshots(state: RunState) -> None:
    objects = object_rows_by_name(state.metadata.get("tables", []))
    fields_by_object: dict[str, list[dict[str, object]]] = defaultdict(list)
    for profile in state.profiles:
        fields_by_object[profile.object_name].append(
            {
                "name": profile.column_name,
                "data_type": profile.data_type,
                "numeric_scale": profile.numeric_scale,
                "is_nullable": profile.is_nullable,
                "ordinal_position": profile.ordinal_position,
            }
        )
    snapshots = [
        profile_snapshot_payload(
            run_id=state.config.run_id,
            source_namespace=f"{state.config.database}.{state.config.schema}",
            object_name=object_name,
            object_type=str(objects.get(object_name, {}).get("TABLE_TYPE", "")),
            row_count=optional_int(objects.get(object_name, {}).get("ROW_COUNT")),
            fields=fields,
        )
        for object_name, fields in sorted(fields_by_object.items())
    ]
    write_jsonl_artifact(state.run_dir / "profile_snapshots.jsonl", snapshots)


def object_rows_by_name(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(row.get("TABLE_NAME", "")): row for row in rows}


def optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def append_command_log(state: RunState, text: str) -> None:
    with (state.run_dir / "command_log.sql").open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write(text)
        handle.flush()


def empty_seed_scan() -> SeedScanResult:
    return SeedScanResult({}, [], 0, 0, [], [])


def empty_graph_scan() -> GraphScanResult:
    return GraphScanResult([], [], [], [], [])


def load_route_seed_scan(config: LiveConfig) -> SeedScanResult:
    if config.route_seed_id_bag is None:
        area = IncompleteArea(
            object_name="ROUTE_SEED_ID_BAG",
            column_name="",
            id_node_key="",
            expected_row_count=1,
            fetched_row_count=0,
            page_size=config.page_size,
            attempted_mitigations=("provide --route-seed-id-bag",),
            stop_reason="seed_mode route-bag requires --route-seed-id-bag",
            resume_checkpoint="",
        )
        return SeedScanResult({}, [], 0, 0, [area], [])
    payload = json_load_or_empty(config.route_seed_id_bag)
    nodes, skipped_rows = route_seed_nodes_from_payload(config, payload)
    seed_rows = route_seed_rows(config, payload, nodes)
    return SeedScanResult(nodes, seed_rows, len(seed_rows), len(seed_rows), [], skipped_rows)


def route_seed_nodes_from_payload(
    config: LiveConfig, payload: dict[str, object]
) -> tuple[dict[str, IdNode], list[dict[str, object]]]:
    nodes: dict[str, IdNode] = {}
    skipped_rows: list[dict[str, object]] = []
    raw_nodes = payload.get("nodes", [])
    if not isinstance(raw_nodes, list):
        return nodes, skipped_rows
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        column_name = str(raw.get("domain", "")).strip()
        value = str(raw.get("value", "")).strip()
        if not column_name or not value:
            continue
        try:
            node = node_from_value(config.database, config.schema, column_name, value, "NUMBER")
        except ValueError as exc:
            skipped_rows.append(route_seed_skipped_row(config, column_name, str(exc)))
            continue
        nodes.setdefault(node.key, node)
    return nodes, skipped_rows


def route_seed_rows(
    config: LiveConfig, payload: dict[str, object], nodes: dict[str, IdNode]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    raw_nodes = payload.get("nodes", [])
    if not isinstance(raw_nodes, list):
        return rows
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        column_name = str(raw.get("domain", "")).strip()
        value = str(raw.get("value", "")).strip()
        if not column_name or not value:
            continue
        try:
            node = node_from_value(config.database, config.schema, column_name, value, "NUMBER")
        except ValueError:
            continue
        if node.key not in nodes:
            continue
        rows.append(
            {
                "run_id": config.run_id,
                "service_id": config.service_id,
                "object_name": "ROUTE_SEED_ID_BAG",
                "anchor_column": column_name,
                "match_count": 1,
                "row_hash": stable_hash(node.key),
                "query_id": "",
                "page_number": 1,
            }
        )
    return rows


def route_seed_skipped_row(
    config: LiveConfig, column_name: str, reason_detail: str
) -> dict[str, object]:
    return {
        "run_id": config.run_id,
        "object_name": "ROUTE_SEED_ID_BAG",
        "object_type": "ROUTE_SEED_ARTIFACT",
        "column_name": column_name,
        "skip_scope": "COLUMN",
        "skip_reason_code": "ROUTE_SEED_FIELD_NOT_PROOF_GRADE",
        "skip_reason_detail": reason_detail,
        "required_for_full_discovery": False,
        "causes_incomplete": False,
        "mitigation_attempted": "field skipped before graph node creation",
        "next_action": "add deterministic ID-domain rule only if this field is approved proof-grade",
    }


def merge_seed_scans(left: SeedScanResult, right: SeedScanResult) -> SeedScanResult:
    nodes = dict(left.seed_nodes)
    nodes.update(right.seed_nodes)
    return SeedScanResult(
        nodes,
        [*left.seed_rows, *right.seed_rows],
        left.searched_anchor_columns + right.searched_anchor_columns,
        left.exact_anchor_hits + right.exact_anchor_hits,
        [*left.incomplete_areas, *right.incomplete_areas],
        [*left.skipped_rows, *right.skipped_rows],
    )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
