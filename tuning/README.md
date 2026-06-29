# PPMT Tuning Directory

This directory contains **runtime tuning patches** for the PPMT live/paper engine,
derived from systematic snapshot analysis. Each tuning round is:

1. **Analyzed** — a snapshot export is parsed and diagnosed.
2. **Documented** — a `vN_snapshot_analysis.md` captures the findings.
3. **Patched** — a `vN_to_vN+1_tuning_patch.yaml` lists explicit before→after params.
4. **Tracked** — `TRACEABILITY.md` records the result of each applied patch.

## Workflow per round

```
[export snapshot vN] → [analyze] → [propose patch] → [apply to runtime]
                                                         │
                                                         ▼
                                              [run 24-48h] → [export vN+1]
                                                         │
                                                         ▼
                                              [compare vN vs vN+1 in TRACEABILITY.md]
```

## Files

| File | Purpose |
|---|---|
| `v9_snapshot_analysis.md` | Diagnostic of snapshot v9 (2026-06-28 18:45:23) |
| `v9_to_v10_tuning_patch.yaml` | Explicit parameter changes to apply for v10 run |
| `TRACEABILITY.md` | Per-change log: what was changed, when, and the resulting metrics |

## Conventions

- **Snapshot versioning**: matches the engine export `_version` field (`ppmt-export-v9`).
- **Patch naming**: `vN_to_vN+1_tuning_patch.yaml` — bump version on every applied patch.
- **Traceability**: every applied patch MUST have an entry in `TRACEABILITY.md` with the resulting snapshot metrics (win_rate, profit_factor, total_pnl, max_drawdown, total_trades).
- **Reversibility**: every patch lists a `rollback_to` field — the previous parameter values, so a regression can be undone in one edit.
