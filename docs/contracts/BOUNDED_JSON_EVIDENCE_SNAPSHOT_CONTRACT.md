# Bounded JSON Evidence Snapshot Contract

This contract defines source-agnostic JSON artifacts for probe-first data
search.

## Decisions

- `SKIP`: object or predicate is not safe or relevant to probe.
- `COUNT_ONLY`: count is enough for current decision; no sample fetched.
- `SAMPLE_ONLY`: tiny sanitized sample is allowed; no deep fetch.
- `DEEP_FETCH_CANDIDATE`: deeper fetch may be proposed after review.
- `OWNER_APPROVAL_REQUIRED`: fanout exceeds configured operational limit.

## Status Rules

- Probe success is not business proof.
- Probe failure is not negative evidence.
- Permission, timeout, truncation, source-health failure, or fanout guard means
  `INCOMPLETE`.
- Raw full-table exports are not allowed in snapshot mode.
- Secret values, credentials, tokens, and private auth material must never be
  written to artifacts.

## Required Files

`source_manifest.json` must be a JSON object.

`profile_snapshots.jsonl` must contain one JSON object per line.

`predicate_probe_snapshots.jsonl` must contain one JSON object per line.

`probe_decision_matrix.json` must be a JSON object with:

- `run_id`
- `generated_at`
- `decision_counts`
- `total_probe_count`
- `owner_approval_required`
- `blocked_probe_count`
- `recommended_next_action`

`status_split.json` remains source-specific but must keep independent statuses.
Do not collapse all probe results into one success label.

## Sanitized Sample Rows

Samples must be small and sanitized. A sample row may include:

- column names
- value type
- blank/null marker
- short stable digest
- source row hash if already available

Samples must not include raw unrestricted values unless the source contract
explicitly allows them.

## Default Limits

Unless a repo-specific contract overrides them:

- sample row limit: `5`
- deep-fetch candidate limit: `page_size * max_pages_per_predicate`
- owner approval required above deep-fetch candidate limit

Changing limits is a behavior change and must be visible in
`source_manifest.json`.
