"""Structured ID classification helpers for INCA_SRC discovery."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .inca_src_discovery_context import *  # noqa: F403
from .inca_src_discovery_models import *  # noqa: F403
from .inca_src_discovery_metadata import stable_hash


def normalize_id_value(value: object, source_data_type: str) -> str:
    data_type = canonical_data_type(source_data_type)
    if value is None:
        msg = "ID node value cannot be null"
        raise ValueError(msg)
    if data_type in INTEGER_TYPES:
        return normalize_numeric_id(value)
    return str(value).strip()


def normalize_numeric_id(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("1")))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+(\.0+)?", text):
        msg = f"Numeric ID is not integer-like: {text}"
        raise ValueError(msg)
    return str(int(Decimal(text)))


def node_from_value(
    database: str,
    schema: str,
    column_name: str,
    value: object,
    source_data_type: str,
) -> IdNode:
    domain, _rule = domain_for_column(column_name)
    if domain == "":
        msg = f"Column is not a proof-grade ID column: {column_name}"
        raise ValueError(msg)
    return IdNode(
        database=database,
        schema=schema,
        id_domain=domain,
        value=normalize_id_value(value, source_data_type),
    )


def classify_structured_id_column(
    profile: ColumnProfile,
    searchability: Searchability | None = None,
) -> StructuredIdClassification:
    domain, inclusion_rule = domain_for_column(profile.column_name)
    exclusion = exclusion_rule(profile)
    searchable = searchability or Searchability("UNKNOWN", False, "NOT_RUN")
    if exclusion:
        return StructuredIdClassification(
            id_domain="",
            feasibility_status="EXCLUDED",
            inclusion_rule=inclusion_rule,
            exclusion_rule=exclusion,
            searchable_status=searchable.searchable_status,
            exact_predicate_supported=searchable.exact_predicate_supported,
            count_query_status=searchable.count_query_status,
            sample_distinct_count_status=searchable.sample_distinct_count_status,
            notes=searchable.notes,
        )
    if domain == "":
        return StructuredIdClassification(
            id_domain="",
            feasibility_status="EXCLUDED",
            inclusion_rule="NO_MATCH",
            exclusion_rule="NO_STRUCTURED_ID_NAME_MATCH",
            searchable_status=searchable.searchable_status,
            exact_predicate_supported=searchable.exact_predicate_supported,
            count_query_status=searchable.count_query_status,
            sample_distinct_count_status=searchable.sample_distinct_count_status,
            notes=searchable.notes,
        )
    return searchable_classification(domain, inclusion_rule, searchable)


def searchable_classification(
    domain: str,
    inclusion_rule: str,
    searchability: Searchability,
) -> StructuredIdClassification:
    if searchability.searchable_status == "SEARCHABLE":
        feasible = (
            searchability.exact_predicate_supported and searchability.count_query_status == PASS
        )
        status = "FEASIBLE" if feasible else "EXCLUDED"
        exclusion = "" if feasible else "NOT_SEARCHABLE"
    elif searchability.searchable_status == "UNKNOWN":
        status = "INCOMPLETE"
        exclusion = "SEARCHABILITY_NOT_PROVEN"
    else:
        status = "EXCLUDED"
        exclusion = "NOT_SEARCHABLE"
    return StructuredIdClassification(
        id_domain=domain,
        feasibility_status=status,
        inclusion_rule=inclusion_rule,
        exclusion_rule=exclusion,
        searchable_status=searchability.searchable_status,
        exact_predicate_supported=searchability.exact_predicate_supported,
        count_query_status=searchability.count_query_status,
        sample_distinct_count_status=searchability.sample_distinct_count_status,
        notes=searchability.notes,
    )


def exclusion_rule(profile: ColumnProfile) -> str:
    name = profile.column_name.upper()
    if is_context_only_name(name):
        return "CONTEXT_ONLY_FIELD"
    if data_type_is_rejected(profile.data_type):
        return "REJECTED_DATA_TYPE"
    if data_type_is_incompatible(profile.data_type, profile.numeric_scale):
        return "INCOMPATIBLE_DATA_TYPE"
    if is_date_status_user_audit_name(name):
        return "DATE_STATUS_USER_AUDIT_FIELD"
    if is_generated_metadata_name(name, profile.object_name):
        return "GENERATED_ONLY_METADATA_FIELD"
    return ""


def domain_for_column(column_name: str) -> tuple[str, str]:
    name = column_name.upper()
    if name in EXACT_DOMAIN_BY_COLUMN:
        return EXACT_DOMAIN_BY_COLUMN[name], f"EXACT:{name}"
    for pattern, domain, rule in DOMAIN_PATTERNS:
        if pattern.fullmatch(name):
            return domain, rule
    if PAIR_ROLE_PATTERN.fullmatch(name):
        return f"GENERIC_INT_ID:{name}", "PAIR_ROLE"
    if INT_ID_SUFFIX_PATTERN.fullmatch(name):
        return f"GENERIC_INT_ID:{name}", "SUFFIX_INT_ID"
    return "", "NO_MATCH"


def is_context_only_name(name: str) -> bool:
    upper = name.upper()
    if upper in CONTEXT_ONLY_COLUMNS:
        return True
    return any(token in upper for token in ("DISPLAY", "ROUTE_NAME", "PORT_NAME"))


def data_type_is_rejected(data_type: str) -> bool:
    return canonical_data_type(data_type) in REJECTED_TYPES


def data_type_is_incompatible(data_type: str, numeric_scale: int | None) -> bool:
    canonical = canonical_data_type(data_type)
    if canonical in TEXT_TYPES:
        return False
    if canonical not in INTEGER_TYPES:
        return True
    return numeric_scale not in (None, 0)


def canonical_data_type(data_type: str) -> str:
    return data_type.upper().split("(", maxsplit=1)[0].strip()


def is_generated_metadata_name(column_name: str, object_name: str) -> bool:
    upper_column = column_name.upper()
    if upper_column not in EXCLUDED_NAME_TOKENS:
        return False
    combined = f"{object_name}_{upper_column}".upper()
    return not any(term in combined for term in RELATION_DOMAIN_TERMS)


def is_date_status_user_audit_name(column_name: str) -> bool:
    upper_column = column_name.upper()
    if upper_column.endswith("_INT_ID") or upper_column in EXACT_DOMAIN_BY_COLUMN:
        return False
    return any(token in upper_column for token in DATE_STATUS_USER_AUDIT_TOKENS)


def build_structured_id_dictionary_rows(
    run_id: str,
    profiles: Iterable[ColumnProfile],
    searchability: Mapping[tuple[str, str], Searchability] | None = None,
) -> list[dict[str, object]]:
    lookup = searchability or {}
    rows: list[dict[str, object]] = []
    for profile in profiles:
        status = lookup.get((profile.object_name, profile.column_name))
        classification = classify_structured_id_column(profile, status)
        rows.append(
            {
                "run_id": run_id,
                "database": profile.database,
                "schema": profile.schema,
                "object_name": profile.object_name,
                "object_type": profile.object_type,
                "column_name": profile.column_name,
                "ordinal_position": profile.ordinal_position,
                "data_type": profile.data_type,
                "numeric_scale": profile.numeric_scale,
                "is_nullable": profile.is_nullable,
                "id_domain": classification.id_domain,
                "feasibility_status": classification.feasibility_status,
                "inclusion_rule": classification.inclusion_rule,
                "exclusion_rule": classification.exclusion_rule,
                "searchable_status": classification.searchable_status,
                "exact_predicate_supported": classification.exact_predicate_supported,
                "count_query_status": classification.count_query_status,
                "sample_distinct_count_status": classification.sample_distinct_count_status,
                "dependency_signal": profile.dependency_signal,
                "notes": classification.notes,
            }
        )
    return rows


def feasible_dictionary_rows(rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [row for row in rows if row.get("feasibility_status") == "FEASIBLE"]


def assert_full_inventory_before_candidate_classification(
    object_inventory_complete: bool,
    column_inventory_complete: bool,
) -> None:
    if not object_inventory_complete or not column_inventory_complete:
        msg = "Candidate classification requires complete object and column inventory"
        raise RuntimeError(msg)


def classify_candidate_relation_tables(
    dictionary_rows: Sequence[Mapping[str, object]],
    *,
    object_inventory_complete: bool,
    column_inventory_complete: bool,
) -> list[dict[str, object]]:
    assert_full_inventory_before_candidate_classification(
        object_inventory_complete,
        column_inventory_complete,
    )
    by_object: dict[str, list[Mapping[str, object]]] = {}
    for row in feasible_dictionary_rows(dictionary_rows):
        by_object.setdefault(str(row["object_name"]), []).append(row)
    candidates: list[dict[str, object]] = []
    for object_name, rows in sorted(by_object.items()):
        domains = sorted({str(row["id_domain"]) for row in rows})
        if len(domains) >= 2:
            candidates.append(
                {
                    "object_name": object_name,
                    "candidate_reason": "MULTIPLE_FEASIBLE_STRUCTURED_ID_DOMAINS",
                    "id_domains": "|".join(domains),
                    "feasible_column_count": len(rows),
                }
            )
    return candidates
