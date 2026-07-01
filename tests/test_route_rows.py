from lasagna.route_sorting.route_rows import InCARow


def _row(*, ne_type: str = "", ne_function: str = "") -> InCARow:
    return InCARow(
        site_code="AAA",
        site_type="XS",
        ne_info="AAA device",
        cabling_location="ne-location",
        cabling_points="source-points",
        conn_type="LC",
        location_alias=None,
        route_path="AAA-BBB 100G01",
        pos=1,
        status_o_time=None,
        o_time=None,
        status_t_time=None,
        t_time=None,
        comment=None,
        ne_type=ne_type,
        ne_function=ne_function,
    )


def test_known_router_ne_type_is_router() -> None:
    assert _row(ne_type="NCS-5508").is_router is True


def test_unknown_ne_type_without_router_function_is_not_router() -> None:
    assert _row(ne_type="RLS").is_router is False


def test_unknown_ne_type_can_use_explicit_router_function() -> None:
    assert _row(ne_type="RLS", ne_function="ROUTER").is_router is True


def test_known_transport_ne_type_is_not_router_even_with_unknown_function() -> None:
    assert _row(ne_type="G30", ne_function="TRANSPORT").is_router is False
