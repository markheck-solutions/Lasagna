# INCA_SRC Evidence Framework

## Purpose

This framework discovers, profiles, and tests possible `PROD_ACCESS_DB.INCA_SRC`
relationships for route blockers such as `IC-388612`. It must prove,
fail-close, or report `INCOMPLETE` from authoritative Snowflake data only.

The framework is evidence collection. It is not sorter implementation. A live
run cannot change route order, `PORT_MATCH_RULE`, or edge semantics approval.
`IC-388612` remains `SORT FAILED` unless an accepted Snowflake relation proves
the TM client-line relation and the route continuity contract.

## Non-Proof Sources

RAG, embedding similarity, semantic ranking, display text proximity, and route
name parsing are analyst aids at most. They cannot determine route continuity,
TM client-line relationships, or sorter status.

`max_join_depth` and arbitrary depth caps are banned. Depth caps create false
negative evidence. The required traversal is fixed-point graph closure over the
scoped proof-grade evidence graph.

Manifests, current route SQL, route-export temp tables, known candidate lists,
and prior investigations are seed sources only. They are never discovery
boundaries. Full `PROD_ACCESS_DB.INCA_SRC` object and column discovery comes
first.

## Discovery Order

1. Capture Snowflake account, role, user, warehouse, database, schema, and time.
2. Enumerate all `PROD_ACCESS_DB.INCA_SRC` tables/views.
3. Enumerate all `PROD_ACCESS_DB.INCA_SRC` columns.
4. Discover `INFORMATION_SCHEMA.VIEWS` columns dynamically.
5. Capture optional object dependencies if privileges allow.
6. Build a structured-ID dictionary across the full schema.
7. Extract proof-grade IDs from the blocker route.
8. Run exact-ID overlap against all feasible structured-ID columns.
9. Build evidence edges from rows connecting proof-grade IDs.
10. Traverse until fixed point: no new IDs, no new rows, no new edges.
11. Derive status split. Negative evidence is allowed only after complete fixed
    point with no incomplete areas.

## Feasible Structured-ID Columns

Column inclusion requires deterministic name and data-type rules.

Included names:

- Exact: `CONTENT_INT_ID`, `CONNPT_INT_ID`, `CONN_POINT_INT_ID`, `CABPT_INT_ID`
- Suffix: `.*_INT_ID`
- Pair roles: `(PARENT|CHILD|A|Z|FROM|TO|SOURCE|TARGET|SRC|DST|DEST).*(_ID|_INT_ID)$`
- Domains: `.*(TRAIL|FACILITY|WAVELENGTH|WAVE|CHANNEL|LAMBDA|ADAPTATION|ADAPT).*(_ID|_INT_ID)$`

Allowed types:

- Integer-like numeric: `NUMBER` scale `0`, `INTEGER`, `BIGINT`, `INT`,
  `DECIMAL`, `NUMERIC`
- Text ID-like: `VARCHAR`, `TEXT`, `CHAR`, `STRING`

Excluded:

- Context-only fields: `NE`, `NE_PART`, `NE_PART_NAME`, `SITE_CODE`, `SLOT`,
  `SUBSLOT`, `CONNECTION_POINT_NR`, display names, route names, port names
- `VARIANT`, `OBJECT`, `ARRAY`, `BINARY`, geospatial, date/time/status/user/audit
  fields
- Generated-only metadata such as `LOAD_ID`, `BATCH_ID`, `JOB_ID`, `RUN_ID`,
  `ROW_ID`, `HASH_ID`, `AUDIT_ID` unless the name also contains an INCA
  relation-domain term
- Columns where exact predicates fail, time out, or are permission-blocked

Every excluded or unsearchable column must be represented in artifacts as a
classification, metadata gap, skipped object, coverage row, or incomplete area.

## ID Classes

Proof-grade structured IDs may become graph nodes:

- `*_INT_ID`
- `CONTENT_INT_ID`
- `CONNPT_INT_ID`
- `CONN_POINT_INT_ID`
- `CABPT_INT_ID`
- explicit trail IDs
- explicit facility IDs
- explicit wavelength/channel IDs
- explicit adaptation IDs
- explicit parent/child or A/Z/from/to/source/target structured ID pairs

Context-only fields may annotate evidence but cannot prove route continuity:

- `NE_PART_NAME`
- site code
- `SLOT`
- `SUBSLOT`
- `CONNECTION_POINT_NR`
- display port names
- route names
- C/L labels or client/line display words
- row order or cabling order

Rejected heuristic fields may not be hidden proof paths:

- route-name parsing
- port-name inference
- `C = client`
- `L = line`
- embedding similarity
- semantic retrieval ranking
- display text proximity

## ID Node Model

Node key:

```text
<database>.<schema>|<id_domain>|<normalized_value>
```

`id_domain` comes from deterministic column classification, not value shape.
Same value in different domains stays distinct. Same domain/value from multiple
columns merges only through the same node key with separate provenance.

Numeric IDs normalize to decimal strings with no leading zeros. Text IDs are
trimmed, case preserved, and compared by source column equality.

## Exact-ID Overlap Rule

Exact ID overlap is candidate evidence only. It is not route proof.

A table containing endpoint IDs, both endpoint IDs, or nearby rows is not enough.
Route proof requires an explicit path through authoritative Snowflake relation
rows where every required edge has accepted semantics.

## Evidence Graph

Node = proof-grade structured ID.

Edge = Snowflake row connecting two or more proof-grade structured IDs.

Edge metadata:

- source database/schema/object
- source columns
- row hash
- relation shape/type
- observed cardinality
- semantics registry key/status
- route-continuity eligibility
- TM client-line eligibility
- evidence basis

Closure repeats until:

```text
no new proof-grade IDs
no new evidence rows
no new graph edges
no incomplete scan areas
```

Timeout, permission failure, truncation, fanout guard, skipped searchable object,
or unavailable required metadata means `INCOMPLETE`, not `FAIL`.

## Fanout Handling

Fanout controls are safety guards, not proof.

Required process:

1. Count before fetch.
2. Batch exact IDs.
3. Paginate deterministic result windows.
4. Checkpoint object, column, ID, page/hash window, expected rows, fetched rows,
   visited predicates, discovered nodes, and evidence edges.
5. Resume from checkpoint or explicitly start fresh.
6. Mark `INCOMPLETE` only after concrete continuation failure.

Concrete continuation failures include statement timeout after smaller batches,
permission denial, result truncation despite pagination, operational page limit
requiring owner approval, warehouse/session failure, or unstable object rows.

## Semantics Approval

Initial edge semantics status is `UNKNOWN`.

Allowed transitions:

- `UNKNOWN -> PROVEN`: authoritative docs, reproducible ID joins, and reviewed
  registry entry
- `UNKNOWN -> OWNER_CONFIRMED`: owner confirms after evidence review
- `UNKNOWN -> REJECTED`: data or owner review rejects the relation

Codex may propose `PROVEN`. Codex may not self-approve. Any path with
`UNKNOWN` or `REJECTED` edge semantics cannot prove route continuity or a TM
client-line relation.

## tx_waveinfo

`tx_waveinfo` may be enumerated and profiled if present. It may not prove route
continuity or TM relation unless first approved as an authoritative structured
source with documented semantics and reproducible ID joins.

Default proof status:

```text
SKIPPED_SEMANTICS_UNPROVEN
```

## Negative Evidence

No negative evidence from metadata-only runs.

No negative evidence from incomplete traversal.

Negative evidence is allowed only after full fixed-point graph closure completes
with no incomplete areas and no accepted TM proof. The ledger must include schema
hash, structured-ID dictionary hash, candidate-filter hash, scan coverage hash,
edge semantics registry hash, searched/skipped objects and columns, path counts,
status split, and invalidation rule.

## Schema Drift

Hash and compare:

- sorted object/column metadata as `schema_hash`
- structured-ID classification as `structured_id_dictionary_hash`
- candidate rules as `candidate_filter_hash`
- searched object/column matrix as `scan_coverage_hash`
- edge semantics registry as `edge_semantics_registry_hash`

New/removed objects or columns, changed ID-like columns, dependency changes,
registry changes, dictionary changes, or scan coverage changes require review
and invalidate affected negative evidence. Row-count drift may trigger
reprofiling but does not prove or disprove route continuity.

## Golden Blocker Corpus

Required cases:

- known route family that sorts correctly
- known Ciena/G30/G40 accepted case if evidence exists
- known OTM/TM fail-closed case
- known DTN fail-closed case
- `IC-388612`
- future owner-confirmed TM pass case

Unavailable required cases must become `MISSING_REQUIRED_CASE` entries with
searched sources, unavailable reason, and regression impact. A fix for
`IC-388612` may not weaken existing fail-closed behavior.

## Bridge Recovery

Use Work-PC backend bridge only for Work-PC/Snowflake-owned proof. Do not kill
the bridge blindly. Do not use top-level `exit` in bridge commands. Use `throw`
and bounded polling.

Before Snowflake-dependent work, run from Personal PC:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\mheck\.codex\pikvm\Test-WorkPcBackendAccess.ps1"
```

If bridge/Snowflake/preflight fails, stop with the exact failed check. PiKVM/OCR
is visual verification only, not a workaround for an unhealthy bridge.

If a job is stale, identify process name, PID, command line, owner, impact, and
recovery path before stopping anything. Prefer collector internal deadlines over
bridge-side termination.

## Commands

Metadata-only smoke:

```powershell
python scripts\work_pc\collect_inca_src_evidence.py --service-id IC-388612 --connection bk03716.eu-central-1 --phase metadata-only
```

Full evidence run:

```powershell
python scripts\work_pc\collect_inca_src_evidence.py --service-id IC-388612 --connection bk03716.eu-central-1 --phase full
```

Expected artifact root:

```text
C:\Users\mxj1556\Desktop\LasagnaRouteReviews\inca-src-discovery\
```

## Required Artifacts

Each run folder must be self-describing and include baseline artifacts before
blocking Snowflake queries:

- `run_manifest.json`
- `status_split.json`
- `metadata_gaps.csv`
- `coverage_matrix.csv`
- `skipped_objects.csv`
- `graph_closure_summary.json`
- `command_log.sql`
- `query_log.csv`
- `phase_log.csv`
- `checkpoint.json`
- `FRAMEWORK_RUNBOOK_SNAPSHOT.md`
- `STATUS_CONTRACT_SNAPSHOT.md`
- `AI_HANDOFF_SNAPSHOT.md`

Full runs also write schema/profile, ID dictionary, candidates, seed ID bag,
exact hits, evidence edges, join paths, edge semantics registry, drift report,
golden corpus artifacts, and only when allowed, a negative evidence ledger.

## Reading status_split.json

Read each status independently. Do not collapse statuses into one overall result.

`PASS` means completed and evidence supports that status.
`FAIL` means completed and evidence does not support proof.
`INCOMPLETE` means blocked by timeout, permission, truncation, fanout guard,
skipped searchable object, missing required metadata, unknown semantics, or
incomplete closure.
`NOT_RUN` means intentionally not run in this mode.
`NOT_STARTED` means intentionally out of scope, such as sorter implementation
change.

Metadata-only success proves artifact durability and schema discovery only. It
is not route proof.

## Report After Each Run

Report:

- artifact folder path
- manifest summary
- docs snapshot/ref presence
- metadata gaps
- object/column/structured-ID counts
- seed IDs if extraction ran
- exact-ID overlap count if scan ran
- evidence edge count if graph ran
- fixed-point status, pass count, path counts, incomplete areas
- `status_split.json` summary
- whether negative evidence was allowed and whether ledger exists
- confirmation that sorter logic and `PORT_MATCH_RULE` were not changed
- confirmation that `IC-388612` remains `SORT FAILED` unless accepted TM
  semantics proved otherwise
