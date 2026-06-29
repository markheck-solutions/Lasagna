from lasagna.domain.route_models import ROUTE_COLUMNS
from lasagna.route_sorting.contract import (
    ROUTE_ORDER_AUTHORITY,
    SPAGHETTI_CONTRACT_PATHS,
    SPAGHETTI_SOURCE_COMMIT,
    route_columns,
)


def test_route_contract_matches_lasagna_18_columns() -> None:
    assert route_columns() == ROUTE_COLUMNS
    assert route_columns() == (
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


def test_source_contract_pins_spaghetti_commit_and_metadata_authority() -> None:
    assert SPAGHETTI_SOURCE_COMMIT == "d5871b1e17c8772ae7836b158b1a1ddd9e4566fd"
    assert ROUTE_ORDER_AUTHORITY == "ROUTE_ORDER_METADATA"
    assert "src/inca_sorter/sorting.py" in SPAGHETTI_CONTRACT_PATHS
    assert "tests/test_sorting_characterization.py" in SPAGHETTI_CONTRACT_PATHS
