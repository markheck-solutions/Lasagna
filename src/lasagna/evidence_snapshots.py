"""Source-agnostic bounded JSON evidence snapshot helpers."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, Mapping, Sequence

Decision = Literal[
    "SKIP",
    "COUNT_ONLY",
    "SAMPLE_ONLY",
    "DEEP_FETCH_CANDIDATE",
    "OWNER_APPROVAL_REQUIRED",
]

SKIP: Decision = "SKIP"
COUNT_ONLY: Decision = "COUNT_ONLY"
SAMPLE_ONLY: Decision = "SAMPLE_ONLY"
DEEP_FETCH_CANDIDATE: Decision = "DEEP_FETCH_CANDIDATE"
OWNER_APPROVAL_REQUIRED: Decision = "OWNER_APPROVAL_REQUIRED"


@dataclass(frozen=True)
class SnapshotLimits:
    sample_row_limit: int = 5
    deep_fetch_row_limit: int = 2500


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def stable_digest(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def probe_decision(hit_count: int, limits: SnapshotLimits) -> Decision:
    if hit_count < 0:
        return SKIP
    if hit_count == 0:
        return COUNT_ONLY
    if hit_count <= limits.sample_row_limit:
        return SAMPLE_ONLY
    if hit_count <= limits.deep_fetch_row_limit:
        return DEEP_FETCH_CANDIDATE
    return OWNER_APPROVAL_REQUIRED


def fanout_risk(hit_count: int, limits: SnapshotLimits) -> str:
    if hit_count <= limits.sample_row_limit:
        return "LOW"
    if hit_count <= limits.deep_fetch_row_limit:
        return "MEDIUM"
    return "HIGH"


def source_manifest_payload(
    *,
    run_id: str,
    source_kind: str,
    source_name: str,
    auth_context: Mapping[str, object],
    tool_version: str,
    limits: SnapshotLimits,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "snapshot_schema_version": "bounded-json-evidence-snapshot-v1",
        "source_kind": source_kind,
        "source_name": source_name,
        "auth_context": dict(auth_context),
        "tool_version": tool_version,
        "limits": asdict(limits),
        "raw_row_exports": False,
        "started_at": utc_now_iso(),
    }


def profile_snapshot_payload(
    *,
    run_id: str,
    source_namespace: str,
    object_name: str,
    object_type: str,
    fields: Sequence[Mapping[str, object]],
    row_count: int | None = None,
    profile_status: str = "PROFILED",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "source_namespace": source_namespace,
        "object_name": object_name,
        "object_type": object_type,
        "row_count": row_count,
        "field_count": len(fields),
        "fields": [dict(field) for field in fields],
        "profile_status": profile_status,
        "captured_at": utc_now_iso(),
    }


def predicate_probe_snapshot_payload(
    *,
    run_id: str,
    source_namespace: str,
    object_name: str,
    predicate_field: str,
    predicate_domain: str,
    predicate_ref: str,
    exact_hit_count: int,
    sample_rows: Sequence[Mapping[str, object]],
    count_request_id: str,
    sample_request_id: str,
    elapsed_ms: int,
    row_limit_used: int,
    limits: SnapshotLimits,
    error: str = "",
) -> dict[str, object]:
    decision = probe_decision(exact_hit_count, limits) if not error else SKIP
    return {
        "run_id": run_id,
        "source_namespace": source_namespace,
        "object_name": object_name,
        "predicate_field": predicate_field,
        "predicate_domain": predicate_domain,
        "predicate_ref": predicate_ref,
        "exact_hit_count": exact_hit_count,
        "sample_rows": sanitize_sample_rows(sample_rows, row_limit_used),
        "count_request_id": count_request_id,
        "sample_request_id": sample_request_id,
        "elapsed_ms": elapsed_ms,
        "row_limit_used": row_limit_used,
        "fanout_risk": fanout_risk(exact_hit_count, limits) if not error else "UNKNOWN",
        "decision": decision,
        "error": error,
        "captured_at": utc_now_iso(),
    }


def decision_matrix_payload(
    run_id: str, snapshots: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    decision_counts: dict[str, int] = {}
    owner_approval: list[dict[str, object]] = []
    blocked = 0
    for snapshot in snapshots:
        decision = str(snapshot.get("decision", SKIP))
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        if decision == OWNER_APPROVAL_REQUIRED:
            owner_approval.append(
                {
                    "object_name": snapshot.get("object_name", ""),
                    "predicate_field": snapshot.get("predicate_field", ""),
                    "predicate_ref": snapshot.get("predicate_ref", ""),
                    "exact_hit_count": snapshot.get("exact_hit_count", 0),
                }
            )
        if snapshot.get("error"):
            blocked += 1
    return {
        "run_id": run_id,
        "generated_at": utc_now_iso(),
        "decision_counts": decision_counts,
        "total_probe_count": len(snapshots),
        "owner_approval_required": owner_approval,
        "blocked_probe_count": blocked,
        "recommended_next_action": recommended_next_action(decision_counts, blocked),
    }


def recommended_next_action(decision_counts: Mapping[str, int], blocked_probe_count: int) -> str:
    if blocked_probe_count:
        return "STOP_EXACT_BLOCKER"
    if decision_counts.get(OWNER_APPROVAL_REQUIRED, 0):
        return "REQUEST_OWNER_APPROVAL"
    if decision_counts.get(DEEP_FETCH_CANDIDATE, 0):
        return "REVIEW_DEEP_FETCH_CANDIDATES"
    if decision_counts.get(SAMPLE_ONLY, 0):
        return "REVIEW_SAMPLES"
    return "NO_DEEP_FETCH_JUSTIFIED"


def sanitize_sample_rows(
    rows: Sequence[Mapping[str, object]],
    row_limit: int,
) -> list[dict[str, dict[str, object]]]:
    sanitized: list[dict[str, dict[str, object]]] = []
    for row in rows[:row_limit]:
        sanitized.append({str(key): sanitized_cell(value) for key, value in sorted(row.items())})
    return sanitized


def sanitized_cell(value: object) -> dict[str, object]:
    if value is None:
        return {"is_null": True, "type": "NoneType", "value_digest": ""}
    text = str(value)
    return {
        "is_null": False,
        "is_blank": text.strip() == "",
        "type": type(value).__name__,
        "value_digest": stable_digest(text),
    }
