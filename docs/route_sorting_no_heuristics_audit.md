# Route Sorting No-Heuristics Audit

Scope: Lasagna live combined Snowflake export path used by `generate_route_review_from_combined_csv`, `lasagna.cli`, and `live_batch.py`.

Rule: successful workbook route order must come from `STRUCTURED_ROUTE_CONTRACT`; otherwise service status must be `SORT FAILED`.

## Obsolete-Code Classification

| Class | Surface | Decision | Evidence / rule |
|---|---|---|---|
| `KEEP` | `ROUTE_ORDER_METADATA` | Keep as route-order authority. | Source contract: metadata-backed sorting uses exact `ROUTE_PATH` and `EDGE_SEQUENCE`. |
| `KEEP` | `TRANSPORT_DEVICE_ADJACENCY` | Keep as same-site / transport continuity authority only when the row is relation-backed. | Sorter consumes `ENDPOINT_PROOF_SOURCE = EXACT_DEVICE_PORT_MATCH` only with approved `PORT_MATCH_RULE`. |
| `KEEP` | `PORT_MATCH_RULE` | Keep as structured proof discriminator. | The trusted set is exactly `CABLING_POINT_TO_PEER_CABLING_POINT`. |
| `REJECT_AS_PROOF` | `DEVICE_SUBSLOT_EQUALS_CCP_CONNECTION_POINT_NR` | Fail closed. | Device subslot equality to CCP connection point is not an approved Snowflake relation. |
| `REJECT_AS_PROOF` | `T_PORT_TO_CONNECTION_POINT_NR` | Fail closed. | `T<n>` parsing is a port-name convention, not approved relation proof. |
| `REJECT_AS_PROOF` | `CONTENT_POSITION_TO_LINE_ENDPOINT` | Fail closed. | Content-position rows may be candidate evidence only until TM client-line semantics are reviewed. |
| `REJECT_AS_PROOF` | `PLATFORM_FAMILY`, `NE_TYPE`, `NE_PART LIKE` | Annotation only. | Platform labels cannot decide trusted route proof. |
| `REPLACE_WITH_CABLING_PROOF` | Transport adjacency from broad device/content fanout | Replace with cabling-backed Snowflake path only. | SQL emits trusted transport proof only through nonblank `CONNPT_INT_ID`, `CABPT_INT_ID`, cabling join, and peer cabling point. |
| `FAIL_CLOSED_GUARD` | Blank/untrusted proof source | Fail closed. | `DTN_TEXT_MATCH`, blank source, or any source other than `EXACT_DEVICE_PORT_MATCH` is not trusted. |
| `FAIL_CLOSED_GUARD` | Blank/unknown `PORT_MATCH_RULE` | Fail closed. | Sorter trusts neutral proof-rule values, not `PLATFORM_FAMILY` classifier labels. |
| `DELETE` | Legacy sorter package / fallback tests | Delete / keep deleted. | Active combined path does not import `src/lasagna/route_sorting/inca_sorter`. |
| `KEEP` | Parser/display fallbacks | Keep only for workbook text rendering. | Parser/display fallbacks do not establish route order. |

| File | Function / surface | Heuristic type | Current behavior | Replacement data source | Fail-closed condition |
|---|---|---|---|---|---|
| `src/lasagna/route_sorting/combined_results.py` | `sort_combined_csv_to_service_results` | Old sorter fallback | Deleted. Active path uses structured Snowflake facts only. | `ROUTE_ORDER_METADATA`, `TRANSPORT_DEVICE_ADJACENCY`, `DP_ENDPOINT_ROLE`, `SITE_METADATA` | Any `StructuredRouteContractError` becomes `SORT FAILED`. |
| `src/lasagna/route_sorting/combined_results.py` | `_sort_rows_by_structured_contract` | Route-name parsing | Not used. Row route path must exist in Snowflake route contract. | Exact `ROUTE_PATH` and `EDGE_SEQUENCE` facts. | Missing route path contract. |
| `src/lasagna/route_sorting/combined_results.py` | `_sort_rows_by_structured_contract` | Service-prefix branching | Not used. `IC` / `ICB` prefix never changes sort rules. | Same structured contract for every service ID. | Missing or conflicting contract facts. |
| `src/lasagna/route_sorting/combined_results.py` | `_sort_rows_by_structured_contract` | Site-type rank, XS-first, U-last | Not used for route sequence. Site type is identity only for DP role matching. | Edge sequence, endpoint side, transport adjacency, DP endpoint roles. | Missing endpoint role or duplicate row key. |
| `src/lasagna/route_sorting/combined_results.py` | `_transport_site_order` | Cabinet/location/proximity ordering | Not used. Site path comes from graph of proven port-level transport adjacencies. | `TRANSPORT_DEVICE_ADJACENCY.ENDPOINT_PROOF_SOURCE = EXACT_DEVICE_PORT_MATCH`. | Zero or multiple minimum-cost paths covering required sites. |
| `src/lasagna/route_sorting/combined_results.py` | `_sort_rows_by_structured_contract` | Workbook/source row order | Not used as authority. Sort key is structured facts plus exact endpoint port roles. | Edge order, endpoint side, neighbor site rank, row position, direction. | Duplicate unsequenced row fact. |
| `src/lasagna/route_sorting/combined_results.py` / Snowflake SQL | `TL_DEVICE` / `TL_DEVICE_SHARED_HANDOFF` | Shallow device adjacency | Deleted from trusted sorter and removed from current export. | `CABLING_POINT_TO_PEER_CABLING_POINT` with complete `ENDPOINT_*` fields. | Missing, duplicate, ambiguous, unsupported, or untrusted port-level endpoint proof. |
| `src/lasagna/route_sorting/combined_results.py` | `_dp_roles_by_key` / DP handling | DP/SDP side guessing | Not used. DP rows require explicit emitted endpoint-role proof. | `DP_ENDPOINT_ROLE` from exact endpoint identity or transport-backed site-code proof. | Missing or duplicate DP endpoint role. |
| Deleted legacy package | Old Excel, split-CSV, and ticket surfaces | Route-name parsing, service prefix, source order, cabinet proximity | Physically deleted. No compatibility shim remains. | Lasagna structured workbook path. | Import or static reference is a validation failure. |

Prior live Snowflake result before the fail-closed proof cleanup:

- `12` services sorted with `STRUCTURED_ROUTE_CONTRACT`.
- `1` service (`ICB-127392`) fails closed because OSD2/I DP rows have no unique structured endpoint role.
- No old fallback path exists on live workbook path.
- Full-route verifier status was `OK`: each successful service matched the expected full-route row sequence and the failed service had an approved fail-closed message.

These counts are historical evidence for the previous branch state. They are not acceptance proof for Ciena/G30/G40/TM assumptions after this cleanup; current validation must be rerun against the patched branch.

`ICB-127392` Snowflake proof search:

| Proof artifact | Result | Meaning |
|---|---:|---|
| `V_T_INCATNT_DEMARCATION_POINT_CURRENT` rows for `ICB-127392` | `4` | Current DP/SDP rows exist. |
| `V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT` rows for `CONTENT = 'ICB-127392'` | `0` | `SITE_SIDE` cannot prove DP role for this service. |
| DP join to `V_T_INCATNT_CONTENT_CONNECTION_POINT_CURRENT` by `CONTENT_INT_ID = 17088312` | `4` left-join rows, all CCP fields null | No structured content-connection facts for the DP rows. |
| DP join to `V_T_INCATNT_CONNECTION_CABLING_POINT_CURRENT` by `CONN_POINT_INT_ID` | `4` left-join rows, all connection-point fields null | Current DP rows have no connection-point ID to follow. |
| `V_T_INCATNT_CONTENT_POSITION_CURRENT` by `CHILD_INT_ID = 17088312` | `0` | No content-position hierarchy links the DP content to a route edge. |
| `V_T_INCATNT_CABLING_CURRENT` by exact DP locations `101B/02/05` and `102A/01/05` | `0` | Cabling view does not expose exact current DP locations as link endpoints. |
| `V_T_INCATNT_DEMARCATION_POINT_FULL_HISTORY` for OSD2/I DP IDs with non-null `CONN_POINT_INT_ID` | `0` | Historical demarcation rows also do not carry a connection-point ID. |

External proof folder:

`C:\Users\mxj1556\Desktop\LasagnaRouteReviews\icb127392-structured-proof-search-20260626-2310`

Latest live proof:

| Check | Result |
|---|---|
| Work-PC preflight | `RunId=327`, `Ok=True` |
| Work-PC sync hash proof | `RunId=334`, `HASH_VERIFY=PASS`, `HASHED_FILES=15` |
| Work-PC focused tests | `RunId=335`, `42 passed` |
| Work-PC live batch + verifier | `RunId=339`, `VERIFIER_STATUS=OK`, `VERIFIER_FAILURES=0` |
| Formula XML count | `0` |
| Raw CSV cleanup count | `0` |
| Workbook status counts | `OK=12`, `SORT FAILED=1` |
| Route source counts | `STRUCTURED_ROUTE_CONTRACT=12`, blank for the fail-closed service |
| Output folder | `C:\Users\mxj1556\Desktop\LasagnaRouteReviews\thirteen-service-ticket-generator-disabled-20260626-232913` |
| Workbook SHA-256 | `9329EC74873CDCB111E791BFA986FF174BCA1258A07E3E7AA8815311C7ADE756` |
| Combined CSV SHA-256 | `D29B451720F6B2FAFE7678B7162620087BBF3F56BB2B102A14A39BD37BAFCDD1` |
| Verification JSON SHA-256 | `EB40C54448788844432B928256F1137DCBABBBCF1A2944A5189B053D58F2E8B0` |

Remaining data gap:

- Old helper modules were deleted; active code is the combined Snowflake path.
- `ICB-127392` OSD2/I DP endpoint role is not structurally proven in the searched Snowflake views. The governed behavior is `SORT FAILED`, not guessed ordering.

Not touched:

- Workbook writer UI/formatting.
- CLI entry points.
- Package/build workflow.
- Work-PC bridge runtime.
- Snowflake auth, warehouse, connector, VS Code extension, or machine setup.
- Raw Snowflake row exports.
