"""Command-line entrypoint for local Lasagna generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from lasagna.app import generate_route_review_from_combined_csv


def _read_ids(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.ids_text:
        parts.append(args.ids_text)
    if args.ids_file:
        parts.append(args.ids_file.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Lasagna route review workbooks.")
    parser.add_argument("--ids-text", default="")
    parser.add_argument("--ids-file", type=Path)
    parser.add_argument("--combined-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-service-tabs", type=int, default=100)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = generate_route_review_from_combined_csv(
        _read_ids(args),
        args.combined_csv,
        output_dir=args.output_dir,
        max_service_tabs=args.max_service_tabs,
    )
    print(f"Output folder: {result.output_dir}")
    for workbook in result.workbooks:
        print(f"Workbook: {workbook.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
