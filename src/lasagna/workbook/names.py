"""Workbook and worksheet naming."""

from __future__ import annotations

import re

INVALID_SHEET_TITLE_CHARS = re.compile(r"[\[\]:*?/\\]")
MAX_EXCEL_SHEET_TITLE_LENGTH = 31


def workbook_filename(batch_number: int) -> str:
    """Return the Lasagna batch workbook filename for a 1-based batch number."""
    if batch_number < 1:
        raise ValueError("batch_number must be 1 or greater")
    return f"Lasagna_Batch_{batch_number:03d}.xlsx"


def sanitize_sheet_name(raw_name: str, existing_names: set[str] | None = None) -> str:
    """Return an Excel-safe, workbook-unique sheet name."""
    existing = {name.lower() for name in existing_names or set()}
    base = INVALID_SHEET_TITLE_CHARS.sub("-", raw_name.strip()).strip("' -")
    if not base:
        base = "Sheet"
    base = base[:MAX_EXCEL_SHEET_TITLE_LENGTH]
    candidate = base
    suffix_number = 2

    while candidate.lower() in existing:
        suffix = f"~{suffix_number}"
        candidate = f"{base[: MAX_EXCEL_SHEET_TITLE_LENGTH - len(suffix)]}{suffix}"
        suffix_number += 1

    return candidate
