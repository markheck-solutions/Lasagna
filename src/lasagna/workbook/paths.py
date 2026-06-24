"""Generated workbook output paths."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def default_output_root(home: Path | None = None) -> Path:
    """Return the default generated workbook root outside the repo."""
    base_home = home or Path.home()
    return base_home / "Desktop" / "LasagnaRouteReviews"


def build_run_output_dir(
    root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Return a timestamped output directory path without creating it."""
    output_root = root or default_output_root()
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return output_root / timestamp
