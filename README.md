# Lasagna

Admin-free desktop route workbook tool for pasted `IC-######` and `ICB-######` service IDs.

## Current Slice

Implemented:

- preserves exact 18-column route contract
- parses and de-dupes pasted service IDs
- writes split `Lasagna_Batch_###.xlsx` workbooks outside the repo
- records every pasted input in Summary rows
- keeps per-service failures isolated
- includes fail-closed local validation entrypoint
- runs explicit-ID Snowflake extraction through a Work-PC-owned live batch command
- packages an admin-free per-user installer with a Lasagna desktop shortcut

## Local Validation

Run from `C:\repos\Lasagna`:

```powershell
python scripts/quality_gates/run_local_validation.py
```

Default generated workbook root:

```text
C:\Users\<you>\Desktop\LasagnaRouteReviews
```

## Live Batch

Run on the machine that owns Snowflake access:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\work_pc\run_lasagna_live_batch.ps1 -IdsText "IC-123456 ICB-123456"
```

The raw combined CSV is temporary and is deleted by default after workbook generation.
