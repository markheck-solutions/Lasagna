from lasagna.route_sorting.combined_results import _bearer_message


def test_bearer_message_keeps_only_bearer_name() -> None:
    assert (
        _bearer_message(
            [
                "Bearer: ASH/2 X 37-MAI X 133 10G01",
                "A-Loc: ASH, B-Loc: FAIR",
                "Route order: ROUTE_ORDER_METADATA",
                "Site order: ASH -> HVB -> FAIR",
            ]
        )
        == "ASH/2 X 37-MAI X 133 10G01"
    )


def test_bearer_message_is_blank_when_no_bearer_line() -> None:
    assert _bearer_message(["Route order: ROUTE_ORDER_METADATA"]) == ""
