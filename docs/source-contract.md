# Source Contract

Lasagna owns its runtime code. It does not import from `C:\repos\Spaghetti` at runtime.

## Reference

- Source repo: `https://github.com/markheck-solutions/Spaghetti`
- Source commit: `d5871b1e17c8772ae7836b158b1a1ddd9e4566fd`
- Strategy: owned copy, adapted into Lasagna modules.

## Current Runtime Ownership

- Snowflake combined export parsing lives in `src/lasagna/route_sorting/combined_parser.py`.
- Route row data lives in `src/lasagna/route_sorting/route_rows.py`.
- Workbook display-only port extraction lives in `src/lasagna/route_sorting/port_display.py`.
- Active route ordering lives in `src/lasagna/route_sorting/combined_results.py`.

## Preserved Behaviors

- `ROUTE_ORDER_METADATA` is the route-order authority for metadata-backed sorting.
- `TRANSPORT_DEVICE_ADJACENCY` is required when same-site device continuity affects order, and DWDM endpoint matching must use `EXACT_DEVICE_PORT_MATCH` plus an approved structured `PORT_MATCH_RULE`.
- The only approved transport endpoint proof in this PR is `CABLING_POINT_TO_PEER_CABLING_POINT`, sourced through `CONNPT_INT_ID -> CABPT_INT_ID -> V_T_INCATNT_CABLING_CURRENT -> peer CABPT_INT_ID`.
- Platform-family labels, Ciena WS/MOTR/OTN device slot/subslot equality, G30/G40 `T<n>` parsing, and OTM/TM content-position assumptions are not accepted route proof.
- OTM/TM client-to-line relationships fail closed unless a future evidence dossier proves explicit Snowflake relation semantics and the edge semantics registry is reviewed.
- DTN module/card relationships fail closed unless Snowflake emits the approved cabling relation with complete endpoint fields.
- `TL_DEVICE` is not a route-order authority and is not emitted by the current Snowflake export.
- Partial route-order metadata fails closed for a service.
- Migration detection and migration portion behavior come from Spaghetti route tests.
- Workbook route sections repeat the same route columns.
- No Spaghetti PM workspace lanes, Salesforce queue UI, maps, dashboards, release names, heuristic sorting package, or branding are copied into Lasagna.

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
