import pytest

from lasagna.workbook.names import sanitize_sheet_name, workbook_filename


def test_workbook_filename_uses_lasagna_batch_number() -> None:
    assert workbook_filename(1) == "Lasagna_Batch_001.xlsx"
    assert workbook_filename(25) == "Lasagna_Batch_025.xlsx"


def test_workbook_filename_rejects_zero() -> None:
    with pytest.raises(ValueError):
        workbook_filename(0)


def test_sanitize_sheet_name_respects_excel_rules_and_duplicates() -> None:
    first = sanitize_sheet_name("ICB/123456:bad*name?with[chars]", {"Summary"})
    second = sanitize_sheet_name(first, {"Summary", first})

    assert first == "ICB-123456-bad-name-with-chars"
    assert second == "ICB-123456-bad-name-with-char~2"
    assert len(second) <= 31
