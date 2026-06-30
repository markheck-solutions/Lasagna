# INCA_SRC Evidence Handoff

Read first:

1. `docs/runbooks/INCA_SRC_EVIDENCE_FRAMEWORK.md`
2. `docs/contracts/INCA_SRC_EVIDENCE_STATUS_CONTRACT.md`
3. `docs/runbooks/BOUNDED_JSON_EVIDENCE_SNAPSHOT_PROTOCOL.md`
4. `docs/contracts/BOUNDED_JSON_EVIDENCE_SNAPSHOT_CONTRACT.md`
5. This file

Operational rules:

- Inspect `git status --short` before changes.
- Preserve dirty worktree. Do not reset or revert unrelated files.
- Do not modify sorter logic.
- Do not modify `PORT_MATCH_RULE`.
- Do not use RAG, embeddings, or semantic ranking as proof.
- Do not use context-only fields as proof.
- Do not approve edge semantics yourself.
- Do not write negative evidence from metadata-only or incomplete runs.
- Before any full rerun after fanout, use bounded JSON evidence snapshots.
- Run `python -m pytest tests/test_inca_src_discovery.py -q` before live
  evidence.
- Run `python scripts/quality_gates/run_local_validation.py` before claiming
  repo validation.

Current state:

- Metadata-only live run passed from commit
  `d734607eca99691b57c32e36163e8f9f46786dd1`.
- Artifact path:
  `C:\Users\mxj1556\Desktop\LasagnaRouteReviews\inca-src-discovery\run-20260628T060519Z`.
- `INCA_SRC` objects discovered: `144`.
- `INCA_SRC` columns discovered: `4574`.
- Views discovered: `144`.
- Structured-ID columns discovered: `285`.
- `IC-388612` seed extraction: `NOT_RUN`.
- Exact-ID overlap scan: `NOT_RUN`.
- Graph closure: `NOT_RUN`.
- Negative evidence: `NOT WRITTEN`.
- `IC-388612` remains `SORT FAILED`.

Next step:

Diagnose the current fanout with bounded JSON evidence snapshots before any new
full live `IC-388612` evidence collection. Do not patch sorter logic. Do not
create or change `PORT_MATCH_RULE`.

Required preflight from Personal PC:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\mheck\.codex\pikvm\Test-WorkPcBackendAccess.ps1"
```

Probe command on Work PC:

```powershell
python scripts\work_pc\collect_inca_src_evidence.py --service-id IC-388612 --connection bk03716.eu-central-1 --phase probe-only
```

Full run command on Work PC, only after probe review:

```powershell
python scripts\work_pc\collect_inca_src_evidence.py --service-id IC-388612 --connection bk03716.eu-central-1 --phase full
```

Stop condition:

If bridge, Snowflake, permissions, timeout, truncation, fanout, or required
metadata blocks completion, report exact failed check and mark relevant statuses
`INCOMPLETE`. Do not infer route proof.
