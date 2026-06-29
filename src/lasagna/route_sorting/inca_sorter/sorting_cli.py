"""Command-line interface for route sorting."""

# ruff: noqa: F401,F403,F405,I001
from __future__ import annotations

from .sorting_context import *  # noqa: F403

from .sorting_core import sort_inca_route_path


def _build_sorting_arg_parser() -> argparse.ArgumentParser:
    """Build the sorting CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="INCA Route Path Sorting and Ticket Generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python inca_sorter.py input.xlsx\n"
            '  python inca_sorter.py input.xlsx --service-type "Backbone IP"\n'
            "  python inca_sorter.py input.xlsx --output sorted.xlsx\n"
            "  python inca_sorter.py --snowflake-a trunk.csv --snowflake-b devices.csv\n"
        ),
    )
    parser.add_argument("input", nargs="?", help="Input INCA Excel export (.xlsx)")
    parser.add_argument(
        "--service-type",
        help='Service type (e.g., "Backbone IP", "Backbone DWDM")',
        default=None,
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output Excel file path (.xlsx)",
        default=None,
    )
    parser.add_argument(
        "--snowflake-a",
        help="Snowflake Query A CSV (trunk ODF rows)",
        default=None,
    )
    parser.add_argument(
        "--snowflake-b",
        help="Snowflake Query B CSV (device rows with cable trace)",
        default=None,
    )
    parser.add_argument(
        "--snowflake-c",
        help="Snowflake Query C CSV (ODUC chassis function, optional)",
        default=None,
    )
    parser.add_argument(
        "--snowflake-combined",
        help="Combined Snowflake CSV export (QID,ROW_DATA format from prod_all)",
        default=None,
    )
    return parser


def _validate_sorting_cli_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    """Validate supported CLI input combinations."""
    if args.snowflake_combined:
        return
    if args.snowflake_a and not args.snowflake_b:
        parser.error("--snowflake-b is required when --snowflake-a is specified")
    if not args.snowflake_a and not args.input:
        parser.error(
            "Either input .xlsx, --snowflake-a/--snowflake-b, or --snowflake-combined is required"
        )


def _print_cli_sort_result(result: SortResult, *, service_id: str) -> None:
    """Print the shared owner-facing analysis, route, notation, and tickets."""
    print()
    print("-" * 80)
    print("SERVICE ANALYSIS")
    print("-" * 80)
    for message in result.info_lines:
        print(f"  {message}")
    print()

    print(format_sorted_route_path(result.rows, migration_portion=result.migration_portion))

    notations_str = format_notations(result.notations)
    if notations_str:
        print(notations_str)

    print(format_tickets(result.tickets, all_planned=result.all_planned, service_id=service_id))


def _print_combined_service_inventory(data: SnowflakeCombinedData, csv_path: str) -> None:
    """Print combined-export discovery output before per-service processing."""
    print(f"Reading combined Snowflake CSV: {os.path.basename(csv_path)}")
    print(f"Services found: {len(data.services)}")
    for service_id in sorted(data.services):
        print(f"  {service_id} ({len(data.services[service_id])} rows)")
    print()


def _run_combined_sorting_cli(args: argparse.Namespace) -> None:
    """Run combined CSV processing and print grouped per-service output."""
    combined_data = read_snowflake_combined_csv(args.snowflake_combined)
    if not combined_data.services:
        print("ERROR: No services found in combined CSV.", file=sys.stderr)
        sys.exit(1)

    _print_combined_service_inventory(combined_data, args.snowflake_combined)
    for service_id in sorted(combined_data.services):
        print("=" * 64)
        print(f"SERVICE: {service_id}")
        print("=" * 64)

        result = sort_inca_route_path(
            combined_data.services[service_id],
            service_id=service_id,
            snowflake_edge_records=combined_data.edge_records,
            tl_device_records=combined_data.tl_device_records,
            trunk_metadata_records=combined_data.trunk_metadata,
            route_order_metadata_records=combined_data.route_order_metadata,
            transmission_metadata_records=combined_data.transmission_metadata,
            hub_records=combined_data.hub_records,
            bo_fibers=combined_data.bo_fibers,
        )
        _print_cli_sort_result(result, service_id=service_id)
        print()


def _read_cli_rows(args: argparse.Namespace) -> list[InCARow]:
    """Read Excel or split Snowflake CSV inputs for the standard CLI path."""
    if args.snowflake_a:
        print(
            f"Reading Snowflake CSVs: {os.path.basename(args.snowflake_a)}, {os.path.basename(args.snowflake_b)}"
        )
        if args.snowflake_c:
            print(f"  ODUC context: {os.path.basename(args.snowflake_c)}")
        return read_snowflake_csv(args.snowflake_a, args.snowflake_b, args.snowflake_c)

    print(f"Reading: {os.path.basename(args.input)}")
    return read_excel(args.input)


def _derive_cli_service_id(args: argparse.Namespace, rows: list[InCARow]) -> str:
    """Derive the owner-visible service identifier for standard CLI input."""
    if rows and rows[0].service_id:
        return f"ICB-{rows[0].service_id}"
    if not args.input:
        return ""

    header_id = extract_service_id(args.input)
    if header_id:
        return header_id

    basename = os.path.basename(args.input)
    icb_match = re.search(r"(ICB-\d+)", basename, re.IGNORECASE)
    if icb_match:
        return icb_match.group(1).upper()

    numeric_match = re.search(r"(\d{6})", basename)
    return f"ICB-{numeric_match.group(1)}" if numeric_match else ""


def _write_cli_output_if_requested(
    args: argparse.Namespace,
    result: SortResult,
    service_id: str,
) -> None:
    """Write the optional output workbook for the standard CLI path."""
    if not args.output:
        return

    write_output_excel(
        args.output,
        result.rows,
        result.notations,
        result.tickets,
        migration_portion=result.migration_portion,
        service_id=service_id,
        bearer=result.bearer,
    )
    print(f"Output written to: {os.path.basename(args.output)}")


def _run_standard_sorting_cli(args: argparse.Namespace) -> None:
    """Run the standard single-service CLI path."""
    rows = _read_cli_rows(args)
    service_id = _derive_cli_service_id(args, rows)
    if not rows:
        print("ERROR: No data rows found in input file.", file=sys.stderr)
        sys.exit(1)

    result = sort_inca_route_path(
        rows,
        service_type=args.service_type,
        service_id=service_id,
    )
    _print_cli_sort_result(result, service_id=service_id)
    _write_cli_output_if_requested(args, result, service_id)


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = _build_sorting_arg_parser()
    args = parser.parse_args(argv)

    # Validate arguments
    _validate_sorting_cli_args(parser, args)
    if args.snowflake_combined:
        _run_combined_sorting_cli(args)
        return

    _run_standard_sorting_cli(args)
