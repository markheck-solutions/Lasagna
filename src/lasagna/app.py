"""Lasagna generation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lasagna.domain.route_models import ServiceRouteResult
from lasagna.domain.service_ids import parse_service_id_text, unique_valid_service_ids
from lasagna.route_sorting.combined_results import sort_combined_csv_to_service_results
from lasagna.workbook.paths import build_run_output_dir
from lasagna.workbook.writer import WorkbookWriteResult, write_route_workbooks


class ServiceResultProvider(Protocol):
    """Fetch workbook-ready results for normalized service IDs."""

    def __call__(self, service_ids: list[str]) -> dict[str, ServiceRouteResult]: ...


@dataclass(frozen=True)
class GenerationResult:
    """Generated workbook paths and per-service statuses."""

    output_dir: Path
    workbooks: tuple[WorkbookWriteResult, ...]
    service_results: dict[str, ServiceRouteResult]


def generate_route_review(
    pasted_text: str,
    provider: ServiceResultProvider,
    *,
    output_dir: Path | None = None,
    max_service_tabs: int = 100,
) -> GenerationResult:
    """Generate route review workbooks from pasted IC/ICB text."""
    parsed_inputs = parse_service_id_text(pasted_text)
    service_ids = unique_valid_service_ids(parsed_inputs)
    run_output_dir = output_dir or build_run_output_dir()
    service_results = provider(service_ids)
    workbooks = write_route_workbooks(
        parsed_inputs,
        service_results,
        run_output_dir,
        max_service_tabs=max_service_tabs,
    )
    return GenerationResult(
        output_dir=run_output_dir,
        workbooks=tuple(workbooks),
        service_results=service_results,
    )


def generate_route_review_from_combined_csv(
    pasted_text: str,
    combined_csv_path: Path,
    *,
    output_dir: Path | None = None,
    max_service_tabs: int = 100,
) -> GenerationResult:
    """Generate route review workbooks from an existing combined Snowflake CSV."""
    parsed_inputs = parse_service_id_text(pasted_text)
    service_ids = unique_valid_service_ids(parsed_inputs)
    return generate_route_review(
        pasted_text,
        lambda _service_ids: sort_combined_csv_to_service_results(combined_csv_path, service_ids),
        output_dir=output_dir,
        max_service_tabs=max_service_tabs,
    )
