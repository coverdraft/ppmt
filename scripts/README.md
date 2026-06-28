# PPMT Patch Scripts

This directory contains the patch scripts used to evolve the PPMT Trading Terminal
from v0.60 (initial commit) through v7 (multi-strategy parallel trading).

## Why these scripts exist

Each `fix_ppmt_v*.py` script applies a discrete set of changes to the codebase.
Keeping them in the repo (not just in chat history) gives us:

1. **Reproducibility** — anyone can re-apply a patch on a fresh checkout
2. **Audit trail** — `git log scripts/` shows the chronology of fixes
3. **Rollback granularity** — each patch is independently revertible
4. **Cross-machine sync** — `git pull` brings every fix to any developer's Mac

## Chronological patch list

| Version | Script | What it does |
|---------|--------|--------------|
| v1 | `apply_ppmt_fixes.py` | Initial audit fixes (end-to-end wiring) |
| v2 | `fix_ppmt_v2.py` | 50 active tokens, dynamic pattern buffer |
| v2 charts | `fix_ppmt_v2_charts.py` | Chart rendering fixes |
| v2 labels (a/b/c/d) | `fix_ppmt_v2_labels*.py` | Form control labels (a11y) |
| v3 | `fix_ppmt_v3_cors.py` | Server-side CoinGecko/Kraken proxy |
| v4 | `fix_ppmt_v4_a11y.py` | Final form-control labels |
| v5 | `fix_ppmt_v5_brain_logs.py` | Live pattern buffer + server-side engine log |
| v6 | `fix_ppmt_v6_profitability.py` | Profitability calibration |
| **v7** | **`fix_ppmt_v7_multi_strategy.py`** | **Multi-strategy parallel + ATR SL/TP + 7 bug fixes** |

## v7 source files (in `scripts/terminal/`)

The complete rewrite of the paper trading engine lives at:
- `scripts/terminal/paper-trading-engine-v3.ts` — 109 KB, the v7 engine source

When `fix_ppmt_v7_multi_strategy.py` runs, it copies this file to
`src/lib/paper-trading-engine.ts` and patches the store + hook.

## How to apply the latest patch on a fresh machine

```bash
cd ~/ppmt
git pull origin terminal-web
python3 scripts/fix_ppmt_v7_multi_strategy.py   # re-applies v7 (idempotent)
npm install
npm run dev
```

## Helper scripts

- `verify_braces.py` / `verify_braces_v2.py` — sanity-check TS file structure
- `_ts_validate.js` / `_ts_parse_check.js` — TypeScript parser smoke tests
- `analyze_log.py` — parses engine logs for trade patterns
- `trader_pattern_analysis.py` / `trader_pattern_analysis_v2.py` — offline trader behavior analysis
- `cleanup_page.py` — page-level dead code removal
- `apply_audit_fixes.py` / `apply_b2.py` — mid-audit incremental fixes
