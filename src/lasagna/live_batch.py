"""Live Snowflake batch orchestration for explicit IC/ICB IDs."""

from __future__ import annotations

import argparse
from pathlib import Path

from lasagna.app import generate_route_review_from_combined_csv
from lasagna.domain.service_ids import parse_service_id_text, unique_valid_service_ids
from lasagna.snowflake.export import export_service_ids_to_combined_csv
from lasagna.workbook.paths import build_run_output_dir


def _read_ids(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.service_id:
        parts.extend(args.service_id)
    if args.ids_text:
        parts.append(args.ids_text)
    if args.ids_file:
        parts.append(args.ids_file.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live Lasagna Snowflake batch.")
    parser.add_argument("--service-id", action="append", default=[])
    parser.add_argument("--ids-text", default="")
    parser.add_argument("--ids-file", type=Path)
    parser.add_argument("--connection")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-service-tabs", type=int, default=100)
    parser.add_argument("--keep-combined-csv", action="store_true")
    return parser.parse_args(argv)


def run_live_batch(args: argparse.Namespace) -> Path:
    """Run export, sort, workbook write, and raw export cleanup."""
    pasted_text = _read_ids(args)
    parsed_inputs = parse_service_id_text(pasted_text)
    service_ids = unique_valid_service_ids(parsed_inputs)
    if not service_ids:
        raise ValueError("No valid IC/ICB service IDs found.")

    output_dir = args.output_dir or build_run_output_dir()
    scratch_dir = output_dir / "_scratch"
    combined_csv = scratch_dir / "lasagna_combined_export.csv"
    export_service_ids_to_combined_csv(
        service_ids,
        combined_csv,
        connection=args.connection,
    )
    try:
        generate_route_review_from_combined_csv(
            pasted_text,
            combined_csv,
            output_dir=output_dir,
            max_service_tabs=args.max_service_tabs,
        )
    finally:
        if not args.keep_combined_csv and combined_csv.exists():
            combined_csv.unlink()
        if not args.keep_combined_csv and scratch_dir.exists():
            try:
                scratch_dir.rmdir()
            except OSError:
                pass
    return output_dir


def run_live_batch_from_text(
    ids_text: str,
    *,
    output_dir: Path | None = None,
    connection: str | None = None,
    max_service_tabs: int = 100,
    keep_combined_csv: bool = False,
) -> Path:
    """Run live batch from pasted IDs without exposing argparse to callers."""
    args = argparse.Namespace(
        service_id=[],
        ids_text=ids_text,
        ids_file=None,
        connection=connection,
        output_dir=output_dir,
        max_service_tabs=max_service_tabs,
        keep_combined_csv=keep_combined_csv,
    )
    return run_live_batch(args)


def main(argv: list[str] | None = None) -> int:
    output_dir = run_live_batch(_parse_args(argv))
    print(f"Lasagna live batch output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
