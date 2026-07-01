"""INCA evidence collector implementation slice."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from typing import TYPE_CHECKING

from .inca_evidence_collector_context import *  # noqa: F403

if TYPE_CHECKING:
    from .inca_evidence_collector_fileio import utc_now
    from .inca_evidence_collector_probe_snapshots import probe_limits
    from .inca_evidence_collector_state import int_or_zero


def dtn_semantic_probe_payload(
    state: RunState,
    rows: Mapping[str, list[dict[str, object]]],
    seed_ids: Mapping[str, list[str]],
    content_candidates: list[dict[str, object]],
    blockers: list[dict[str, object]],
    query_notes: list[dict[str, object]],
) -> dict[str, object]:
    counts = semantic_counts(rows, content_candidates, blockers)
    safe_candidates = [
        {key: value for key, value in candidate.items() if key != "_value"}
        for candidate in content_candidates
    ]
    dwdm_context = semantic_dwdm_context(rows, seed_ids, query_notes, blockers, state.config)
    classification = dtn_relation_classification(state, counts, seed_ids, dwdm_context)
    snapshot = {
        "run_id": state.config.run_id,
        "service_id": state.config.service_id,
        "source": f"{state.config.database}.{state.config.schema}",
        "target": {
            "site_code": state.config.semantic_site_code,
            "device_token": state.config.semantic_device_token,
        },
        "raw_unrestricted_values_written": False,
        "content_candidates": safe_candidates,
        "counts": counts,
        "dwdm_adjacency": dwdm_context,
        "seed_ids": dict(seed_ids),
        "blockers": blockers,
        "query_notes": query_notes,
        "samples": {
            name: sanitize_sample_rows(sample_rows, state.config.probe_sample_row_limit)
            for name, sample_rows in rows.items()
        },
    }
    return {"snapshot": snapshot, "classification": classification}


def semantic_counts(
    rows: Mapping[str, list[dict[str, object]]],
    content_candidates: list[dict[str, object]],
    blockers: list[dict[str, object]],
) -> dict[str, int]:
    return {
        "service_transmission_rows": len(rows.get("service_transmission", [])),
        "cp_seed_rows": len(rows.get("content_position_seed", [])),
        "ccp_content_candidate_count": len(content_candidates),
        "device_rows_by_actual_bearer_content": len(
            rows.get("content_connection_point_devices", [])
        ),
        "ashr1_dtn_device_rows": len(rows.get("ashr1_dtn_device_rows", [])),
        "dtn_device_rows": len(rows.get("dtn_device_rows", [])),
        "dtn_cacp_rows": len(rows.get("dtn_cacp_rows", [])),
        "blank_cabpt_rows": semantic_blank_cabpt_count(rows.get("dtn_cacp_rows", [])),
        "nonblank_cabpt_rows": semantic_nonblank_cabpt_count(rows.get("dtn_cacp_rows", [])),
        "dtn_cabling_rows": len(rows.get("dtn_cabling_rows", [])),
        "cabling_peer_cacp_rows": len(rows.get("cabling_peer_cacp_rows", [])),
        "distinct_site_count": len(semantic_site_count_summary(rows.get("dtn_device_rows", []))),
        "blocker_count": len(blockers),
    }


def semantic_blank_cabpt_count(rows: list[dict[str, object]]) -> int:
    return sum(1 for row in rows if not semantic_text(row.get("CABPT_INT_ID")))


def semantic_nonblank_cabpt_count(rows: list[dict[str, object]]) -> int:
    return sum(1 for row in rows if semantic_text(row.get("CABPT_INT_ID")))


def semantic_site_count_summary(rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        site = semantic_text(row.get("NEP__NEPART_SITE_CODE")) or semantic_text(
            row.get("CCP__SITE_CODE")
        )
        key = site or "<blank>"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def semantic_dwdm_context(
    rows: Mapping[str, list[dict[str, object]]],
    seed_ids: Mapping[str, list[str]],
    query_notes: list[dict[str, object]],
    blockers: list[dict[str, object]],
    config: LiveConfig,
) -> dict[str, object]:
    dtn_cabpt_ids = [value for value in seed_ids.get("CABPT_INT_ID", []) if value]
    usable_pair_count = semantic_usable_endpoint_pair_count(
        dtn_cabpt_ids,
        rows.get("dtn_cabling_rows", []),
        rows.get("cabling_peer_cacp_rows", []),
    )
    counts = semantic_counts(rows, [], blockers)
    fanout_limit_exceeded = semantic_fanout_limit_exceeded(query_notes, config)
    decision = semantic_dwdm_decision(counts, usable_pair_count, fanout_limit_exceeded)
    return {
        "decision": decision,
        "usable_endpoint_pair_count": usable_pair_count,
        "fanout_limit_exceeded": fanout_limit_exceeded,
        "site_count_summary": semantic_site_count_summary(rows.get("dtn_device_rows", [])),
        "decision_reason": semantic_dwdm_decision_reason(
            counts, usable_pair_count, fanout_limit_exceeded, decision
        ),
    }


def semantic_usable_endpoint_pair_count(
    dtn_cabpt_ids: list[str],
    cabling_rows: list[dict[str, object]],
    peer_cacp_rows: list[dict[str, object]],
) -> int:
    dtn_ids = set(dtn_cabpt_ids)
    peer_ids = {
        semantic_text(row.get("CABPT_INT_ID"))
        for row in peer_cacp_rows
        if semantic_text(row.get("CABPT_INT_ID"))
    } - dtn_ids
    pairs: set[tuple[str, str]] = set()
    for row in cabling_rows:
        a_id = semantic_text(row.get("A_CABPT_INT_ID"))
        b_id = semantic_text(row.get("B_CABPT_INT_ID"))
        if not a_id or not b_id or a_id == b_id:
            continue
        if (a_id in dtn_ids and b_id in peer_ids) or (b_id in dtn_ids and a_id in peer_ids):
            first_id, second_id = sorted((a_id, b_id))
            pairs.add((first_id, second_id))
    return len(pairs)


def semantic_fanout_limit_exceeded(
    query_notes: list[dict[str, object]], config: LiveConfig
) -> bool:
    return any(
        int_or_zero(note.get("count")) > config.semantic_fetch_row_limit
        for note in query_notes
        if "count" in note
    )


def semantic_dwdm_decision(
    counts: Mapping[str, int], usable_pair_count: int, fanout_limit_exceeded: bool
) -> str:
    if fanout_limit_exceeded:
        return "OWNER_APPROVAL_REQUIRED"
    if counts.get("blocker_count", 0) > 0:
        return "INCOMPLETE"
    if (
        counts.get("dtn_device_rows", 0) > 0
        and counts.get("nonblank_cabpt_rows", 0) > 0
        and counts.get("dtn_cabling_rows", 0) > 0
        and counts.get("cabling_peer_cacp_rows", 0) > 0
        and usable_pair_count > 0
    ):
        return "PROVEN_DWDM_ADJACENCY"
    if counts.get("dtn_device_rows", 0) > 0 and (
        counts.get("nonblank_cabpt_rows", 0) == 0 or counts.get("dtn_cabling_rows", 0) == 0
    ):
        return "TRANSMISSION_ONLY_FANOUT"
    return "INCOMPLETE"


def semantic_dwdm_decision_reason(
    counts: Mapping[str, int],
    usable_pair_count: int,
    fanout_limit_exceeded: bool,
    decision: str,
) -> str:
    if fanout_limit_exceeded:
        return "fanout exceeds bounded semantic fetch limit"
    if decision == "PROVEN_DWDM_ADJACENCY":
        return f"nonblank CABPT + cabling + peer CACP proven; usable_endpoint_pair_count={usable_pair_count}"
    if decision == "TRANSMISSION_ONLY_FANOUT":
        return "DTN rows found but nonblank CABPT/cabling path not proven"
    if counts.get("blocker_count", 0) > 0:
        return "required object or column unavailable"
    return "bounded probe did not prove cabling-backed DTN adjacency"


def dtn_relation_classification(
    state: RunState,
    counts: Mapping[str, int],
    seed_ids: Mapping[str, list[str]],
    dwdm_context: Mapping[str, object],
) -> dict[str, object]:
    found = {
        "service_transmission": counts.get("service_transmission_rows", 0) > 0,
        "content_position_seed": counts.get("cp_seed_rows", 0) > 0,
        "content_connection_point_devices": counts.get("device_rows_by_actual_bearer_content", 0)
        > 0,
        "ashr1_dtn_content_connection_point": counts.get("ashr1_dtn_device_rows", 0) > 0,
        "connection_cabling_point": counts.get("dtn_cacp_rows", 0) > 0,
        "cabling": counts.get("dtn_cabling_rows", 0) > 0,
        "cabling_peer_cacp": counts.get("cabling_peer_cacp_rows", 0) > 0,
    }
    return {
        "run_id": state.config.run_id,
        "service_id": state.config.service_id,
        "seed_ids": dict(seed_ids),
        "counts": dict(counts),
        "dwdm_adjacency": dict(dwdm_context),
        "candidate_relation_sources_found": found,
        "business_proof": {
            "dtn_endpoint_relation_proof": INCOMPLETE,
            "route_order_proof": INCOMPLETE,
            "sorter_change_allowed": False,
            "negative_evidence_allowed": False,
            "reason": (
                "Candidate exact-ID and cabling rows exist only as candidate evidence; "
                "DTN edge semantics remain unapproved."
            ),
        },
        "recommended_next_action": (
            "Review or approve a DTN edge semantics registry entry before sorter changes."
        ),
    }


def semantic_probe_candidate_scan_pass(probe: dict[str, object] | None) -> bool:
    if not probe:
        return False
    if "services" in probe:
        services = probe.get("services", [])
        if not isinstance(services, list):
            return False
        return any(
            semantic_service_decision(service) == "PROVEN_DWDM_ADJACENCY"
            for service in services
            if isinstance(service, dict)
        )
    classification = probe.get("classification", {})
    if not isinstance(classification, dict):
        return False
    sources = classification.get("candidate_relation_sources_found", {})
    return isinstance(sources, dict) and all(bool(value) for value in sources.values())


def semantic_service_decision(probe: dict[str, object]) -> str:
    classification = probe.get("classification", {})
    if not isinstance(classification, dict):
        return "INCOMPLETE"
    dwdm = classification.get("dwdm_adjacency", {})
    if not isinstance(dwdm, dict):
        return "INCOMPLETE"
    decision = str(dwdm.get("decision", "INCOMPLETE"))
    return decision if decision in DWDM_ADJACENCY_DECISIONS else "INCOMPLETE"


def dwdm_adjacency_service_summary(
    state: RunState, probes: list[dict[str, object]]
) -> dict[str, object]:
    services = [dwdm_service_summary_row(probe) for probe in probes]
    return {
        "run_id": state.config.run_id,
        "source": f"{state.config.database}.{state.config.schema}",
        "service_ids": [row["service_id"] for row in services],
        "target": {
            "site_code": state.config.semantic_site_code,
            "device_token": state.config.semantic_device_token,
        },
        "raw_unrestricted_values_written": False,
        "snapshot_only": True,
        "business_proof": {
            "route_order_proof": INCOMPLETE,
            "sorter_change_allowed": False,
            "negative_evidence_allowed": False,
            "reason": "Six-service DWDM snapshot is candidate evidence until semantics review.",
        },
        "services": services,
    }


def dwdm_service_summary_row(probe: dict[str, object]) -> dict[str, object]:
    snapshot = probe.get("snapshot", {})
    classification = probe.get("classification", {})
    if not isinstance(snapshot, dict) or not isinstance(classification, dict):
        return {"service_id": "", "decision": "INCOMPLETE"}
    counts = classification.get("counts", {})
    dwdm = classification.get("dwdm_adjacency", {})
    if not isinstance(counts, dict):
        counts = {}
    if not isinstance(dwdm, dict):
        dwdm = {}
    return {
        "service_id": str(classification.get("service_id", snapshot.get("service_id", ""))),
        "decision": str(dwdm.get("decision", "INCOMPLETE")),
        "decision_reason": str(dwdm.get("decision_reason", "")),
        "service_rows": int_or_zero(counts.get("service_transmission_rows")),
        "content_position_seed_rows": int_or_zero(counts.get("cp_seed_rows")),
        "dtn_device_rows": int_or_zero(counts.get("dtn_device_rows")),
        "dtn_cacp_rows": int_or_zero(counts.get("dtn_cacp_rows")),
        "blank_cabpt_rows": int_or_zero(counts.get("blank_cabpt_rows")),
        "nonblank_cabpt_rows": int_or_zero(counts.get("nonblank_cabpt_rows")),
        "dtn_cabling_rows": int_or_zero(counts.get("dtn_cabling_rows")),
        "peer_cacp_rows": int_or_zero(counts.get("cabling_peer_cacp_rows")),
        "distinct_site_count": int_or_zero(counts.get("distinct_site_count")),
        "site_count_summary": dwdm.get("site_count_summary", {}),
        "usable_endpoint_pair_count": int_or_zero(dwdm.get("usable_endpoint_pair_count")),
        "fanout_limit_exceeded": bool(dwdm.get("fanout_limit_exceeded")),
    }


def dwdm_adjacency_decision_matrix(
    state: RunState, service_summary: Mapping[str, object]
) -> dict[str, object]:
    raw_services = service_summary.get("services", [])
    services = (
        [row for row in raw_services if isinstance(row, dict)]
        if isinstance(raw_services, list)
        else []
    )
    decision_counts: dict[str, int] = {}
    for row in services:
        decision = str(row.get("decision", "INCOMPLETE"))
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
    return {
        "run_id": state.config.run_id,
        "generated_at": utc_now(),
        "decision_counts": decision_counts,
        "total_service_count": len(services),
        "proven_service_ids": [
            row.get("service_id")
            for row in services
            if row.get("decision") == "PROVEN_DWDM_ADJACENCY"
        ],
        "transmission_only_fanout_service_ids": [
            row.get("service_id")
            for row in services
            if row.get("decision") == "TRANSMISSION_ONLY_FANOUT"
        ],
        "owner_approval_required_service_ids": [
            row.get("service_id")
            for row in services
            if row.get("decision") == "OWNER_APPROVAL_REQUIRED"
        ],
        "incomplete_service_ids": [
            row.get("service_id") for row in services if row.get("decision") == "INCOMPLETE"
        ],
        "recommended_next_action": dwdm_recommended_next_action(decision_counts),
        "sorter_change_allowed": False,
        "negative_evidence_allowed": False,
    }


def dwdm_recommended_next_action(decision_counts: Mapping[str, int]) -> str:
    if decision_counts.get("OWNER_APPROVAL_REQUIRED", 0):
        return "REQUEST_OWNER_APPROVAL"
    if decision_counts.get("INCOMPLETE", 0):
        return "STOP_EXACT_BLOCKER"
    if decision_counts.get("PROVEN_DWDM_ADJACENCY", 0):
        return "REVIEW_SEMANTICS_BEFORE_SORTER_CHANGE"
    return "NO_DEEP_FETCH_JUSTIFIED"


def dwdm_predicate_probe_snapshots(
    state: RunState, service_summary: Mapping[str, object]
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    raw_services = service_summary.get("services", [])
    services = raw_services if isinstance(raw_services, list) else []
    for row in services:
        if not isinstance(row, dict):
            continue
        service_id = str(row.get("service_id", ""))
        exact_hit_count = int_or_zero(row.get("dtn_device_rows"))
        snapshots.append(
            predicate_probe_snapshot_payload(
                run_id=state.config.run_id,
                source_namespace=f"{state.config.database}.{state.config.schema}",
                object_name="DWDM_ADJACENCY_SERVICE_SUMMARY",
                predicate_field="SERVICE_ID",
                predicate_domain="DWDM_ADJACENCY",
                predicate_ref=stable_digest(service_id),
                exact_hit_count=exact_hit_count,
                sample_rows=[],
                count_request_id="",
                sample_request_id="",
                elapsed_ms=0,
                row_limit_used=state.config.probe_sample_row_limit,
                limits=probe_limits(state.config),
                error=""
                if row.get("decision") != "INCOMPLETE"
                else str(row.get("decision_reason")),
            )
        )
    return snapshots


def semantic_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        return text[:-2]
    return text
