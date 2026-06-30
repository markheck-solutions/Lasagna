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
- `TRANSPORT_DEVICE_ADJACENCY` is required when same-site device continuity affects order, and DWDM endpoint matching must use `EXACT_DEVICE_PORT_MATCH` plus a structured `PORT_MATCH_RULE`.
- DWDM endpoint proof compares workbook route rows to the emitted `ENDPOINT_*_DEVICE_SLOT` / `ENDPOINT_*_DEVICE_SUBSLOT` identity, not a global CCP-port assumption.
- Ciena WS/MOTR/OTN accepts exact device slot/subslot to CCP slot/connection point proof; G30/G40 accepts only proven `T<n>` device subslot to CCP connection point `<n>` proof.
- OTM/TM client-to-line relationships are accepted only when Snowflake emits exact
  `V_T_INCATNT_CONTENT_POSITION_CURRENT` child/parent IDs linking the route device
  content to the transport line endpoint; DTN module/card relationships still fail
  closed unless Snowflake emits an explicit structured relation.
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
