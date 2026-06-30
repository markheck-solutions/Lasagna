# INCA_SRC Evidence Status Contract

This contract defines `status_split.json` for deterministic
`PROD_ACCESS_DB.INCA_SRC` evidence runs.

## Status Values

`PASS`: completed, and evidence supports that status.

`FAIL`: completed, and evidence does not support proof.

`INCOMPLETE`: work could not complete because of timeout, permission failure,
truncation, fanout guard, skipped searchable object, unavailable required
metadata, incomplete graph closure, or pending/unknown edge semantics.

`NOT_RUN`: intentionally not run in this mode.

`NOT_STARTED`: intentionally not started and out of scope.

`NOT_REQUIRED`: not required for this run because a higher-priority completed
condition makes it unnecessary.

`INCOMPLETE` is not negative evidence. Metadata-only success is not route proof.

## Status Derivation

### INCA_SRC schema discovery

- `PASS`: core tables/views and columns inventory completed.
- `FAIL`: inventory query completed but did not target `INCA_SRC` or omitted
  required core fields.
- `INCOMPLETE`: permission, timeout, or required metadata failure blocked full
  inventory.
- `NOT_RUN`: discovery intentionally not run.

### Schema/profile catalog

- `PASS`: schema objects, columns, counts, metadata gaps, and hashes written.
- `FAIL`: artifacts are malformed or inconsistent with collected metadata.
- `INCOMPLETE`: count/profile/catalog write incomplete.
- `NOT_RUN`: catalog intentionally not run.

### Manifest-boundary avoidance

- `PASS`: full-schema feasible ID columns drove scan coverage.
- `FAIL`: discovery was limited to manifests, current SQL, known candidates, or
  route exports.
- `INCOMPLETE`: coverage cannot be proven.
- `NOT_RUN`: coverage not evaluated.

### Structured ID dictionary

- `PASS`: every discovered column classified with deterministic rules.
- `FAIL`: feasible column exists with no classification.
- `INCOMPLETE`: metadata gap blocks classification.
- `NOT_RUN`: dictionary not built.

### IC-388612 ID extraction

- `PASS`: proof-grade seed IDs extracted with provenance.
- `FAIL`: complete extraction found no proof-grade seed IDs.
- `INCOMPLETE`: extraction stopped because of timeout, permission, truncation,
  fanout guard, or skipped searchable object.
- `NOT_RUN`: seed extraction intentionally not run, such as metadata-only mode.

### Exact-ID overlap scan

- `PASS`: all feasible structured-ID columns were scanned and overlap found.
- `FAIL`: complete scan found zero overlap.
- `INCOMPLETE`: any searchable area incomplete.
- `NOT_RUN`: exact scan intentionally not run.

### Evidence graph closure

- `PASS`: fixed point reached.
- `FAIL`: fixed point reached with no blocker-relevant candidate or accepted
  path.
- `INCOMPLETE`: closure stopped before fixed point or any scan area incomplete.
- `NOT_RUN`: graph closure intentionally not run.

### Edge semantics registry

- `PASS`: all proof-used edge sources have approved semantics.
- `FAIL`: registry malformed or required edge rejected.
- `INCOMPLETE`: any needed edge semantics is `UNKNOWN` or pending review.
- `NOT_RUN`: registry not evaluated.

### Candidate relation scan

- `PASS`: candidates classified and scanned completely.
- `FAIL`: complete scan found no candidate relation source.
- `INCOMPLETE`: candidate scan incomplete or semantics unknown.
- `NOT_RUN`: candidate scan not run.

### TM client-line relation proof

- `PASS`: accepted path exists with approved semantics proving TM relation.
- `FAIL`: complete fixed-point closure found no accepted path.
- `INCOMPLETE`: graph, scan, metadata, fanout, or semantics incomplete.
- `NOT_RUN`: proof phase not run.

### Negative evidence ledger

- `PASS`: complete negative entry written after full fixed point with no
  incomplete areas and no accepted TM proof.
- `FAIL`: ledger written when not allowed or malformed.
- `INCOMPLETE`: negative evidence would require complete proof, but run is
  incomplete.
- `NOT_REQUIRED`: accepted TM proof exists.
- `NOT_RUN`: ledger intentionally not evaluated.

### Schema drift invalidation

- `PASS`: hashes computed and prior ledger evaluated.
- `FAIL`: drift detected but affected negative evidence not invalidated.
- `INCOMPLETE`: hash data or prior ledger data unavailable.
- `NOT_RUN`: drift check intentionally not run.

### Golden blocker corpus

- `PASS`: all required cases present or accepted future placeholder is explicit.
- `FAIL`: required case silently absent.
- `INCOMPLETE`: any `MISSING_REQUIRED_CASE` exists.
- `NOT_RUN`: corpus not evaluated.

### Golden blocker regression

- `PASS`: corpus executed and expected outcomes held.
- `FAIL`: available case violated expected outcome.
- `INCOMPLETE`: corpus incomplete.
- `NOT_RUN`: regression not executed.

### Sorter implementation change

- `NOT_STARTED`: required value for evidence-only phases.
- Any other value in evidence-only runs is invalid.

### IC-388612 route order proof

- `PASS`: accepted TM path plus complete route-continuity proof exists.
- `FAIL`: complete evidence disproves accepted route proof.
- `INCOMPLETE`: evidence incomplete or semantics unapproved.
- `NOT_RUN`: route proof not evaluated.

### Repo validation

- `PASS`: repo validation command completed successfully after implementation.
- `FAIL`: repo validation command failed.
- `NOT_RUN`: validation not run in live-only artifact collection.

## Global Rules

- Metadata-only mode must not run seed extraction, exact-ID overlap, or graph
  closure.
- Metadata-only mode must not write `negative_evidence_ledger_entry.json`.
- Seed-only mode must not run graph closure.
- Probe-only and snapshot-only modes must not run graph closure.
- Probe-only and snapshot-only modes must not write
  `negative_evidence_ledger_entry.json`.
- Probe-only and snapshot-only modes may write bounded JSON count/sample
  artifacts, but those artifacts are candidate evidence only.
- Unknown semantics paths cannot change sorter behavior.
- Exact-ID overlap alone cannot prove route continuity.
- Context-only fields cannot create graph nodes.
- `tx_waveinfo` proof use defaults to `SKIPPED_SEMANTICS_UNPROVEN` unless
  approved semantics exist.
- `IC-388612` remains `SORT FAILED` unless accepted Snowflake semantics prove the
  TM relation and route continuity.
