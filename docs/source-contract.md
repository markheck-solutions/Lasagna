# Source Contract

Lasagna owns its runtime code. It does not import from `C:\repos\Spaghetti` at runtime.

## Reference

- Source repo: `https://github.com/markheck-solutions/Spaghetti`
- Source commit: `d5871b1e17c8772ae7836b158b1a1ddd9e4566fd`
- Strategy: owned copy, adapted into Lasagna modules.

## Contract Paths Inspected

- `src/inca_sorter/models.py`
- `src/inca_sorter/parsers.py`
- `src/inca_sorter/sorting.py`
- `src/inca_sorter/sorting_site_assembly.py`
- `src/inca_sorter/sorting_topology.py`
- `src/inca_sorter/tickets.py`
- `src/inca_sorter/formatting.py`
- `tests/test_sorting_characterization.py`
- `tests/test_tickets_characterization.py`
- `tests/test_formatting_characterization.py`

## Preserved Behaviors

- `ROUTE_ORDER_METADATA` is the route-order authority for metadata-backed sorting.
- Partial route-order metadata fails closed for a service.
- Migration detection and migration portion behavior come from Spaghetti route tests.
- Workbook route sections repeat the same route columns.
- No Spaghetti PM workspace lanes, Salesforce queue UI, maps, dashboards, release names, or branding are copied into Lasagna.

## Lasagna Route Workbook Columns

1. `Location ID`
2. `Site Code`
3. `Site Type`
4. `Site Type No`
5. `NE Information`
6. `Cabling Location`
7. `Cabling Points`
8. `Conn Type`
9. `Location Alias`
10. `PCG pos NwP Id`
11. `Route Path`
12. `Pos`
13. `Prot`
14. `Status o-time`
15. `O-time`
16. `Status t-time`
17. `T-time`
18. `Comment`
