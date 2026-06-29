"""Route path sorting compatibility facade."""

# ruff: noqa: F401,F403,I001
from __future__ import annotations

from .sorting_cli import *  # noqa: F403
from .sorting_cli import main
from .sorting_core import *  # noqa: F403
from .sorting_core import _prepare_route_sort
from .sorting_handoffs import *  # noqa: F403
from .sorting_handoffs import _interleave_inter_site_trunk_pairs
from .sorting_metadata import *  # noqa: F403
from .sorting_site_order import *  # noqa: F403
from .sorting_topology import (
    _filter_site_order_for_data,
    build_trunk_endpoint_lookup,
    parse_snowflake_edges,
)

if __name__ == "__main__":
    main()
