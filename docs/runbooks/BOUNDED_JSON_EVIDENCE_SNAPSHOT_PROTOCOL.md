# Bounded JSON Evidence Snapshot Protocol

## Purpose

Use bounded JSON evidence snapshots before any deep data search. The protocol
creates a small, durable map of a data source before an agent fetches large row
sets, walks graph relationships, or makes business-proof claims.

This protocol is source-agnostic. It applies to Snowflake, SQL databases, APIs,
CSV files, spreadsheets, file systems, logs, and any future source where broad
reads can overload the agent or the system.

## Core Rule

No bulk scan before bounded JSON snapshots justify it.

Probe evidence is candidate evidence only. It is not route proof, negative
evidence, sorter proof, business truth, or owner approval.

## Phases

1. `source_inventory`: identify source kind, auth context, object list, safe
   access method, and hard limits.
2. `profile_snapshot`: record object shape, fields, data types, row count when
   cheap, null/distinct hints when cheap, and source metadata.
3. `predicate_probe`: for exact predicates only, record count, tiny sanitized
   sample, query or request ID, elapsed time, row limit, and fanout risk.
4. `decision_matrix`: classify each probe as `SKIP`, `COUNT_ONLY`,
   `SAMPLE_ONLY`, `DEEP_FETCH_CANDIDATE`, or `OWNER_APPROVAL_REQUIRED`.
5. `approved_deep_fetch`: fetch more only when the matrix justifies it and
   source owner rules allow it.

## Required JSON Artifacts

Every snapshot run must write:

- `source_manifest.json`
- `profile_snapshots.jsonl`
- `predicate_probe_snapshots.jsonl`
- `probe_decision_matrix.json`
- `status_split.json`

These files must be written before any risky fetch when possible. If access,
permission, timeout, truncation, or fanout blocks the run, artifacts must remain
parseable and the status must be `INCOMPLETE`.

## Artifact Contract

`source_manifest.json`:

- run ID
- source kind
- source name
- auth context summary with no secrets
- tool version or commit
- hard limits
- raw export allowed flag
- started timestamp

`profile_snapshots.jsonl`:

- object name
- object type
- source namespace
- row count when available
- field count
- field names and types
- null/distinct hints when available
- profile status

`predicate_probe_snapshots.jsonl`:

- object name
- predicate field
- predicate domain
- predicate value hash or node key
- exact hit count
- tiny sanitized sample
- count query/request ID
- sample query/request ID
- elapsed milliseconds
- row limit used
- fanout risk
- decision

`probe_decision_matrix.json`:

- decision counts
- per-object summaries
- probes requiring owner approval
- probes blocked by permission, timeout, truncation, or fanout
- recommended next action

## Fanout Rules

Count before sample. Sample before deep fetch.

Default behavior:

- `0` hits: `COUNT_ONLY`
- hits within sample limit: `SAMPLE_ONLY`
- hits within operational deep-fetch limit: `DEEP_FETCH_CANDIDATE`
- hits above operational limit: `OWNER_APPROVAL_REQUIRED`

Fanout controls are safety guards, not proof. Hitting a guard means
`INCOMPLETE` or owner approval required. It never proves absence of a
relationship.

## Snowflake Rule

Before Snowflake-dependent work, run the machine-owned backend preflight for
that environment. If preflight, auth, warehouse, permission, timeout, or bridge
health fails, stop with the exact failed check.

For Lasagna Work-PC Snowflake work, run from Personal PC:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\mheck\.codex\pikvm\Test-WorkPcBackendAccess.ps1"
```

## Lasagna INCA Rule

For `PROD_ACCESS_DB.INCA_SRC`, bounded JSON snapshots come before any full graph
closure rerun when a prior run stopped on fanout. Probe-only output may guide
the next query, but it cannot change route order, `PORT_MATCH_RULE`, edge
semantics approval, or `IC-388612` route status.
