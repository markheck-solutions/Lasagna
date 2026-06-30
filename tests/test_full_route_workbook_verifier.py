import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from lasagna.domain.route_models import RouteRow, ServiceRouteResult
from lasagna.domain.service_ids import parse_service_id_text
from lasagna.route_sorting.contract import ROUTE_ORDER_AUTHORITY
from lasagna.workbook.writer import write_route_workbooks


def _load_verifier() -> ModuleType:
    verifier_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "quality_gates"
        / "verify_full_route_workbook.py"
    )
    spec = importlib.util.spec_from_file_location(
        "full_route_workbook_verifier_under_test", verifier_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load verifier from {verifier_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_verifier = cast(Any, _load_verifier())
capture_expectations = _verifier.capture_expectations
verify_workbook = _verifier.verify_workbook


def _route_row(site_code: str, route_path: str, pos: str) -> RouteRow:
    return RouteRow(
        location_id=f"LOC-{site_code}",
        site_code=site_code,
        site_type="XS",
        site_type_no="1",
        ne_information=f"{site_code} device",
        cabling_location=f"[{site_code}]01/R01/RU01/.",
        cabling_points=pos,
        conn_type="LC",
        route_path=route_path,
        pos=pos,
    )


def _combined_csv(path: Path) -> Path:
    path.write_text(
        'QID,ROW_DATA\nCOMBINED_00_RUN_METADATA,"{""REPORT_TYPE"":""test""}"\n',
        encoding="utf-8",
    )
    return path


def test_full_route_verifier_captures_and_verifies_structured_order(tmp_path: Path) -> None:
    parsed_inputs = parse_service_id_text("IC-123456")
    service_results = {
        "IC-123456": ServiceRouteResult.ok(
            "IC-123456",
            (
                _route_row("AAA", "AAA-BBB OL01", "1"),
                _route_row("BBB", "AAA-BBB OL01", "2"),
            ),
            route_order_source=ROUTE_ORDER_AUTHORITY,
        )
    }
    [write_result] = write_route_workbooks(parsed_inputs, service_results, tmp_path)
    expected = capture_expectations(write_result.path)
    expected_path = tmp_path / "expected.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")

    result = verify_workbook(
        write_result.path, _combined_csv(tmp_path / "combined.csv"), expected_path
    )

    assert result["status"] == "OK"
    assert result["formula_xml_count"] == 0
    assert result["raw_csv_cleanup_count"] == 0
    assert result["verifier_failures"] == []
    assert result["services"][0]["route_order_source"] == ROUTE_ORDER_AUTHORITY


def test_full_route_verifier_allows_explicit_fail_closed_service(tmp_path: Path) -> None:
    parsed_inputs = parse_service_id_text("ICB-127392")
    service_results = {
        "ICB-127392": ServiceRouteResult.sort_failed(
            "ICB-127392",
            "DP/SDP endpoint role not proven by Snowflake contract for route_path(s): test",
        )
    }
    [write_result] = write_route_workbooks(parsed_inputs, service_results, tmp_path)
    expected = capture_expectations(write_result.path)
    expected_path = tmp_path / "expected.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")

    result = verify_workbook(
        write_result.path, _combined_csv(tmp_path / "combined.csv"), expected_path
    )

    assert result["status"] == "OK"
    assert result["services"][0]["workbook_status"] == "SORT FAILED"
    assert result["services"][0]["actual_row_count"] == 0
    assert result["verifier_failures"] == []
