from datetime import datetime
from pathlib import Path

from lasagna.workbook.paths import build_run_output_dir, default_output_root


def test_default_output_root_is_desktop_folder_outside_repo() -> None:
    root = default_output_root(Path("C:/Users/mheck"))

    assert root == Path("C:/Users/mheck/Desktop/LasagnaRouteReviews")
    assert "repos" not in {part.lower() for part in root.parts}


def test_build_run_output_dir_adds_timestamp() -> None:
    path = build_run_output_dir(
        Path("C:/Users/mheck/Desktop/LasagnaRouteReviews"),
        datetime(2026, 6, 24, 16, 30, 5),
    )

    assert path == Path("C:/Users/mheck/Desktop/LasagnaRouteReviews/20260624_163005")
