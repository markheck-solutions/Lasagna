from pathlib import Path

import pytest

from lasagna.snowflake.sql_template import (
    SQL_TEMPLATE_PATH,
    render_explicit_service_route_sql,
    render_service_values,
    snowflake_string_literal,
)


def test_snowflake_string_literal_escapes_quotes_and_nuls() -> None:
    assert snowflake_string_literal("IC-12'3456\x00") == "'IC-12''3456'"


def test_render_service_values_requires_at_least_one_id() -> None:
    with pytest.raises(ValueError):
        render_service_values([])


def test_rendered_sql_uses_explicit_values_not_pm_seed() -> None:
    sql = render_explicit_service_route_sql(["IC-123456", "ICB-654321"])

    assert "USE SCHEMA prod_access_db.inca_src;" in sql
    assert "('IC-123456')" in sql
    assert "('ICB-654321')" in sql
    assert "LASAGNA_SERVICE_VALUES" not in sql
    assert "prod_service_seed_sdm_orders" not in sql
    assert "prod_service_seed_manual_migrations" not in sql
    assert "FROM workpack" not in sql
    assert "FROM geo_diag" not in sql
    assert "pm_visible_sdm_detail_scope" not in sql
    assert "SELECT qid, row_data AS row_data_variant FROM prod_all" in sql


def test_sql_template_contains_route_qids_and_no_pm_seed_tables() -> None:
    template = Path(SQL_TEMPLATE_PATH).read_text(encoding="utf-8")

    for qid in (
        "TRUNK_ODF",
        "DEVICE",
        "DP_SDP",
        "ROUTE_ORDER_METADATA",
        "TRANSMISSION_METADATA",
        "TRANSPORT_DEVICE_ADJACENCY",
        "DP_ENDPOINT_ROLE",
        "BO_FIBERS",
    ):
        assert qid in template
    assert "prod_service_seed_sdm_orders" not in template
    assert "prod_service_seed_manual_migrations" not in template


def test_route_order_metadata_is_scoped_to_exported_route_paths() -> None:
    template = Path(SQL_TEMPLATE_PATH).read_text(encoding="utf-8")

    assert "CREATE OR REPLACE TEMP TABLE prod_route_order_relevant_edges AS" in template
    assert "WHERE qid IN ('TRUNK_ODF', 'DEVICE')" in template
    assert "JOIN prod_route_order_relevant_edges relevant_edges" in template
    assert "relevant_edges.ROUTE_PATH = walk.edge_name" in template
    assert "WHERE walk.edge_name IS NOT NULL" in template
    assert "CREATE OR REPLACE TEMP TABLE prod_site_location_rows AS" in template
    assert "A_SITE_TYPE_NUMBER" in template
    assert "B_SITE_TYPE_NUMBER" in template
    assert "AND a_site.SITE_TYPE = COALESCE(pcg.A_SITE_TYPE, tx.A_SITE_TYPE)" in template
    assert "AND b_site.SITE_TYPE = COALESCE(pcg.B_SITE_TYPE, tx.B_SITE_TYPE)" in template
    assert "SELECT DISTINCT\n    ranked.service_id AS SERVICE_ID" in template


def test_route_order_edge_sequence_uses_full_walk_path_not_local_minima() -> None:
    template = Path(SQL_TEMPLATE_PATH).read_text(encoding="utf-8")
    position_block = template.split(
        "CREATE OR REPLACE TEMP TABLE prod_route_order_position_rows AS", 1
    )[1].split("-- Role: prod_route_order_site_sides", 1)[0]
    ranking_block = template.split(
        "CREATE OR REPLACE TEMP TABLE prod_route_order_metadata_rows AS", 1
    )[1].split("LEFT JOIN prod_trunk_metadata_rows pcg", 1)[0]

    assert "edge_position_sort_path" in position_block
    assert "MIN(walk.edge_position_path) AS edge_position_path" in position_block
    assert "MIN(walk.edge_position_sort_path) AS edge_position_sort_path" in position_block
    assert "MIN(walk.edge_position) AS edge_position" not in position_block
    assert "MIN(walk.edge_position_id) AS edge_position_id" not in position_block
    assert "ORDER BY edge_position_sort_path, edge_position_path, edge_name" in ranking_block
    assert "ORDER BY edge_position, edge_position_id" not in ranking_block


def test_transport_device_adjacency_uses_recursive_ccp_endpoint_facts() -> None:
    template = Path(SQL_TEMPLATE_PATH).read_text(encoding="utf-8")

    assert "CREATE OR REPLACE TEMP TABLE prod_transport_device_endpoint_rows AS" in template
    assert "CREATE OR REPLACE TEMP TABLE prod_transport_device_adjacency_rows AS" in template
    assert "ccp.CONTENT = walk.edge_name" in template
    assert "ccp.CONNPT_INT_ID" in template
    assert "device_row_keys AS" in template
    assert "DEVICE_CONTENT_INT_ID" in template
    assert "AND device_row_keys.device_site_code = endpoint_sites.device_site_code" in template
    assert "AND device_row_keys.device_slot = endpoint_sites.slot" in template
    assert "device_endpoint_candidates AS" not in template
    assert "DEVICE_SUBSLOT_EQUALS_CCP_CONNECTION_POINT_NR" not in template
    assert "T_PORT_TO_CONNECTION_POINT_NR" not in template
    assert "content_position_endpoint_candidates AS" not in template
    assert "CONTENT_POSITION_TO_LINE_ENDPOINT" not in template
    assert "child_parent.CHILD_INT_ID = device_row_keys.device_content_int_id" not in template
    assert "edge_parent.CHILD_INT_ID = child_parent.TRANSMISSION_INTID" not in template
    assert "edge_parent.BFK_TRANSMISSION = walk.edge_name" not in template
    assert "device_row_keys.device_subslot = endpoint_sites.connection_point_nr" not in template
    assert "device_row_keys.device_subslot = endpoint_sites.subslot" not in template
    assert "dwdm_cabling_endpoint_candidates AS" in template
    assert "CABLING_POINT_TO_PEER_CABLING_POINT" in template
    assert (
        "TO_VARCHAR(endpoint_cacp.CONNPT_INT_ID) = TO_VARCHAR(endpoint_sites.connpt_int_id)"
        in template
    )
    assert "endpoint_cacp.CABPT_INT_ID IS NOT NULL" in template
    assert "JOIN prod_access_db.inca_src.V_T_INCATNT_CABLING_CURRENT cab" in template
    assert "peer_cacp.CABPT_INT_ID != endpoint_cacp.CABPT_INT_ID" in template
    assert "EXACT_DEVICE_PORT_MATCH" in template
    assert "TL_DEVICE_SHARED_HANDOFF" not in template
    assert "shared_handoff_unambiguous_edges AS" not in template
    assert "RAW_ENDPOINT_SITE_COUNT" not in template
    assert "endpoint_row_count = 2" in template
    assert "endpoint_site_count IN (1, 2)" in template
    assert "TRANSPORT_DEVICE_ADJACENCY" in template
    assert "ENDPOINT_PROOF_SOURCE" in template
    assert "PORT_MATCH_RULE" in template
    assert "PORT_MATCH_SOURCE_VIEW" in template
    assert "PORT_MATCH_SOURCE_IDS" in template
    assert "PLATFORM_FAMILY" in template
    assert "ENDPOINT_ROW_COUNT" in template
    assert "ENDPOINT_1_DEVICE_SLOT" in template
    assert "ENDPOINT_1_DEVICE_SUBSLOT" in template
    assert "ENDPOINT_1_CCP_CONNECTION_POINT_NR" in template
    assert "ENDPOINT_1_CONNECTION_POINT_NR" in template
    assert "ENDPOINT_1_SLOT" in template
    assert "ENDPOINT_1_SUBSLOT" in template
    assert "PATH_TEXT" in template


def test_dwdm_adjacency_requires_cabling_backed_relation_not_platform_gate() -> None:
    template = Path(SQL_TEMPLATE_PATH).read_text(encoding="utf-8")
    cabling_block = template.split("dwdm_cabling_endpoint_candidates AS", 1)[1].split(
        "candidate_endpoint_sites AS", 1
    )[0]

    assert "dwdm_cabling_endpoint_candidates AS" in template
    assert "device_row_keys.device_platform_family <> 'DTN'" not in template
    assert "AND device_row_keys.device_platform_family = 'DTN'" not in template
    assert "device_row_keys.device_platform_family = 'G30_G40'" not in template
    assert "DEVICE_SUBSLOT_EQUALS_CCP_CONNECTION_POINT_NR" not in template
    assert "T_PORT_TO_CONNECTION_POINT_NR" not in template
    assert "CONTENT_POSITION_TO_LINE_ENDPOINT" not in template
    assert "REGEXP_LIKE(UPPER(device_row_keys.device_subslot), '^T[0-9]+$')" not in template
    assert "WHERE endpoint_sites.connpt_int_id IS NOT NULL" in template
    assert "endpoint_cacp.CABPT_INT_ID IS NOT NULL" in template
    assert "JOIN prod_access_db.inca_src.V_T_INCATNT_CABLING_CURRENT cab" in template
    assert "peer_cacp.CABPT_INT_ID IS NOT NULL" in template
    assert "peer_cacp.CABPT_INT_ID != endpoint_cacp.CABPT_INT_ID" in template
    assert "CABLING_POINT_TO_PEER_CABLING_POINT" in template
    assert "device_row_keys.device_platform_family =" not in cabling_block
    assert "UPPER(COALESCE(ne_type" not in cabling_block
    assert "LIKE" not in cabling_block
    assert "endpoint_row_count = 2" in template
    assert "endpoint_site_count IN (1, 2)" in template


def test_transport_device_adjacency_preserves_same_site_two_endpoint_proof() -> None:
    template = Path(SQL_TEMPLATE_PATH).read_text(encoding="utf-8")
    ranked_block = template.split("ranked_endpoints AS", 1)[1].split("candidate_pairs AS", 1)[0]

    assert "candidate_site_counts.endpoint_site_count IN (1, 2)" in ranked_block
    assert "candidate_site_counts.endpoint_row_count = 2" in ranked_block
    assert "candidate_site_counts.duplicate_endpoint_count = 0" in ranked_block
    assert "candidate_site_counts.null_endpoint_count = 0" in ranked_block
    assert "candidate_site_counts.endpoint_site_count = 2" not in ranked_block


def test_dp_sdp_rows_use_normalized_service_id_key() -> None:
    template = Path(SQL_TEMPLATE_PATH).read_text(encoding="utf-8")

    assert "dp.SERVICE_ID_KEY AS SERVICE_ID" in template
    assert "dp.CONTENT AS SERVICE_ID" not in template


def test_dp_endpoint_roles_require_unique_structured_proof() -> None:
    template = Path(SQL_TEMPLATE_PATH).read_text(encoding="utf-8")

    assert "CREATE OR REPLACE TEMP TABLE prod_dp_endpoint_role_candidates AS" in template
    assert "CREATE OR REPLACE TEMP TABLE prod_dp_endpoint_role_rows AS" in template
    assert "DP_EXACT_SITE_IDENTITY" in template
    assert "DP_SITE_CODE_TRANSPORT_ENDPOINT" in template
    assert "SAME_PRIORITY_CANDIDATE_COUNT = 1" in template
    assert "INSERT INTO prod_all SELECT 'DP_ENDPOINT_ROLE'" in template
