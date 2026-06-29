"""Output formatting compatibility facade."""

from __future__ import annotations

from .formatting_excel import write_output_excel
from .formatting_notations import generate_notations
from .formatting_text import (
    format_consolidated_tickets,
    format_notations,
    format_sorted_route_path,
    format_tickets,
)

__all__ = [
    "format_consolidated_tickets",
    "format_notations",
    "format_sorted_route_path",
    "format_tickets",
    "generate_notations",
    "write_output_excel",
]
