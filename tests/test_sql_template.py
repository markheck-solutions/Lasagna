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
        "BO_FIBERS",
    ):
        assert qid in template
    assert "prod_service_seed_sdm_orders" not in template
    assert "prod_service_seed_manual_migrations" not in template
