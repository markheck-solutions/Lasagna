"""Topology data types for INCA route sorting."""

from __future__ import annotations

from typing import NamedTuple

RouteEdge = tuple[str, str, str]
AdjacencyGraph = dict[str, list[tuple[str, str]]]


class ParsedSnowflakeEdge(NamedTuple):
    level: int
    site1: str
    site2: str
    edge_name: str


class RouteEndpoints(NamedTuple):
    bearer: str | None
    a_site: str
    b_site: str
    info_lines: list[str]


class RouteTopology(NamedTuple):
    trunk_edges: list[RouteEdge]
    graph: AdjacencyGraph
    site_order: list[str]


class SectionRouteTopology(NamedTuple):
    walk_order: list[str]
    display_order: list[str]
