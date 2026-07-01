"""Work-PC entrypoint for sanitized INCA_SRC evidence collection."""

# ruff: noqa: F401,F403,I001
from __future__ import annotations

from lasagna.snowflake.inca_evidence_collector import *  # noqa: F401,F403
from lasagna.snowflake.inca_evidence_collector import main


if __name__ == "__main__":
    raise SystemExit(main())
