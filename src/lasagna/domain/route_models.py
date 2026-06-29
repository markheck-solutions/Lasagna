"""Route row and per-service workbook result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ROUTE_COLUMNS: tuple[str, ...] = (
    "Location ID",
    "Site Code",
    "Site Type",
    "Site Type No",
    "NE Information",
    "Cabling Location",
    "Cabling Points",
    "Conn Type",
    "Location Alias",
    "PCG pos NwP Id",
    "Route Path",
    "Pos",
    "Prot",
    "Status o-time",
    "O-time",
    "Status t-time",
    "T-time",
    "Comment",
)

ServiceStatus = Literal["OK", "INVALID ID", "NO DATA", "SORT FAILED", "SNOWFLAKE ERROR"]


@dataclass(frozen=True)
class RouteRow:
    """One row in the Lasagna 18-column route workbook contract."""

    location_id: str = ""
    site_code: str = ""
    site_type: str = ""
    site_type_no: str = ""
    ne_information: str = ""
    cabling_location: str = ""
    cabling_points: str = ""
    conn_type: str = ""
    location_alias: str = ""
    pcg_pos_nwp_id: str = ""
    route_path: str = ""
    pos: str = ""
    prot: str = ""
    status_o_time: str = ""
    o_time: str = ""
    status_t_time: str = ""
    t_time: str = ""
    comment: str = ""

    def values(self) -> tuple[str, ...]:
        """Return row values in exact workbook column order."""
        return (
            self.location_id,
            self.site_code,
            self.site_type,
            self.site_type_no,
            self.ne_information,
            self.cabling_location,
            self.cabling_points,
            self.conn_type,
            self.location_alias,
            self.pcg_pos_nwp_id,
            self.route_path,
            self.pos,
            self.prot,
            self.status_o_time,
            self.o_time,
            self.status_t_time,
            self.t_time,
            self.comment,
        )


@dataclass(frozen=True)
class ServiceRouteResult:
    """Workbook-ready route result for one normalized service ID."""

    service_id: str
    status: ServiceStatus
    sorted_rows: tuple[RouteRow, ...] = field(default_factory=tuple)
    migration_rows: tuple[RouteRow, ...] = field(default_factory=tuple)
    route_order_source: str = ""
    message: str = ""

    @classmethod
    def ok(
        cls,
        service_id: str,
        sorted_rows: tuple[RouteRow, ...],
        migration_rows: tuple[RouteRow, ...] = (),
        route_order_source: str = "SYNTHETIC_FIXTURE",
        message: str = "",
    ) -> ServiceRouteResult:
        return cls(
            service_id=service_id,
            status="OK",
            sorted_rows=sorted_rows,
            migration_rows=migration_rows,
            route_order_source=route_order_source,
            message=message,
        )

    @classmethod
    def no_data(cls, service_id: str, message: str = "No route rows found.") -> ServiceRouteResult:
        return cls(service_id=service_id, status="NO DATA", message=message)

    @classmethod
    def sort_failed(cls, service_id: str, message: str) -> ServiceRouteResult:
        return cls(service_id=service_id, status="SORT FAILED", message=message)

    @classmethod
    def snowflake_error(cls, service_id: str, message: str) -> ServiceRouteResult:
        return cls(service_id=service_id, status="SNOWFLAKE ERROR", message=message)
