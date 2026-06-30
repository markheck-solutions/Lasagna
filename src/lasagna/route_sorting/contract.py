"""Route sorting contract constants owned by Lasagna."""

from __future__ import annotations

from lasagna.domain.route_models import ROUTE_COLUMNS

SPAGHETTI_SOURCE_COMMIT = "d5871b1e17c8772ae7836b158b1a1ddd9e4566fd"
ROUTE_ORDER_AUTHORITY = "STRUCTURED_ROUTE_CONTRACT"


def route_columns() -> tuple[str, ...]:
    """Return the exact Lasagna route workbook columns."""
    return ROUTE_COLUMNS
