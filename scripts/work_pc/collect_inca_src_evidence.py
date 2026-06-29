"""Collect sanitized INCA_SRC evidence artifacts with read-only Snowflake queries."""

# ruff: noqa: F401,F403,F405
from __future__ import annotations

from lasagna.snowflake.inca_evidence_collector import *  # noqa: F403
from lasagna.snowflake.inca_evidence_collector_setup import main

if __name__ == "__main__":
    raise SystemExit(main())
