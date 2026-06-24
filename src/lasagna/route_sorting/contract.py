"""Route sorting contract constants copied into Lasagna ownership."""

from __future__ import annotations

from lasagna.domain.route_models import ROUTE_COLUMNS

SPAGHETTI_SOURCE_COMMIT = "d5871b1e17c8772ae7836b158b1a1ddd9e4566fd"
ROUTE_ORDER_AUTHORITY = "ROUTE_ORDER_METADATA"

SPAGHETTI_CONTRACT_PATHS: tuple[str, ...] = (
    "src/inca_sorter/models.py",
    "src/inca_sorter/parsers.py",
    "src/inca_sorter/sorting.py",
    "src/inca_sorter/sorting_site_assembly.py",
    "src/inca_sorter/sorting_topology.py",
    "src/inca_sorter/tickets.py",
    "src/inca_sorter/formatting.py",
    "tests/test_sorting_characterization.py",
    "tests/test_tickets_characterization.py",
    "tests/test_formatting_characterization.py",
)


def route_columns() -> tuple[str, ...]:
    """Return the exact Lasagna route workbook columns."""
    return ROUTE_COLUMNS
