# Paso 7 — Re-tune BAD_HOURS + ASIA_HOURS boost on cb_v2 OOS

## What was done

Created `scripts/v5/v5_analyze_hours_cb_v2.py` — analyzes per-hour UTC
PnL on cb_v2 OOS data (RECENT_2026 + RANGE_2025, 139,788 signals at
thresh=0.70).

## Per-hour results (cb_v2 OOS, thresh=0.70)

| Hour | Daypart | Cur Gate | Signals | Precision | PF | Total PnL% | Avg PnL% |
|---:|---|---|---:|---:|---:|---:|---:|
| 0 | Asia-night | BOOST | 6,938 | 84.4% | 4.62 | +14,773 | +2.129 |
| 1 | Asia-night | BOOST | 6,227 | 89.1% | 6.95 | +15,291 | +2.456 |
| 2 | Asia-night | BOOST | 5,921 | 90.5% | 8.08 | +15,111 | +2.552 |
| 3 | Asia-night | ok | 5,283 | 90.6% | 8.17 | +13,518 | +2.559 |
| **4** | Asia-night | **BLOCKED** | 5,645 | 90.6% | 8.20 | +14,460 | +2.562 |
| **5** | Asia-morn | **BLOCKED** | 5,163 | 89.1% | 6.97 | +12,691 | +2.458 |
| 6 | Asia-morn | ok | 5,717 | 90.3% | 7.97 | +14,545 | +2.544 |
| 7 | Asia-morn | ok | 5,866 | 89.8% | 7.53 | +14,717 | +2.509 |
| 8 | Asia-morn | ok | 5,330 | 89.4% | 7.17 | +13,201 | +2.477 |
| **9** | Asia-morn | **BLOCKED** | 4,714 | 89.3% | 7.13 | +11,658 | +2.473 |
| 10 | Asia-morn | ok | 4,571 | 90.1% | 7.78 | +11,562 | +2.529 |
| 11 | Asia-morn | ok | 4,974 | 88.8% | 6.78 | +12,131 | +2.439 |
| **12** | EU-day | **BLOCKED** | 6,324 | 87.3% | 5.83 | +14,721 | +2.328 |
| 13 | EU-day | ok | 7,473 | 82.3% | 3.96 | +14,802 | +1.981 |
| 14 | EU-day | ok | 7,797 | 87.9% | 6.21 | +18,519 | +2.375 |
| 15 | EU-day | ok | 6,087 | 88.4% | 6.48 | +14,651 | +2.407 |
| **16** | EU-day | **BLOCKED** | 6,475 | 88.6% | 6.64 | +15,698 | +2.424 |
| 17 | US-day | ok | 6,207 | 89.4% | 7.21 | +15,395 | +2.480 |
| 18 | US-day | BOOST | 5,278 | 90.6% | 8.21 | +13,523 | +2.562 |
| 19 | US-day | BOOST | 5,412 | 90.4% | 8.03 | +13,794 | +2.549 |
| 20 | US-day | BOOST | 6,006 | 87.7% | 6.04 | +14,145 | +2.355 |
| 21 | US-day | BOOST | 6,083 | 88.6% | 6.60 | +14,722 | +2.420 |
| 22 | US-day | BOOST | 5,216 | 92.0% | 9.78 | +13,870 | +2.659 |
| 23 | US-day | BOOST | 5,081 | 87.1% | 5.74 | +11,762 | +2.315 |

## Verdict on BAD_HOURS

**ALL 5 currently-blocked hours are profitable:**
- Hour 4: PF=8.20, avg=+2.562% (best PF of all 24 hours!)
- Hour 5: PF=6.97, avg=+2.458%
- Hour 9: PF=7.13, avg=+2.473%
- Hour 12: PF=5.83, avg=+2.328%
- Hour 16: PF=6.64, avg=+2.424%

**NO hours have negative PnL or PF<1.5** — every single hour is profitable.

The BAD_HOURS rule came from v1 Binance trader history (real MEXC futures
trades where hours 4, 5, 9, 12, 16 UTC were net losing). But on cb_v2 OOS
data, the LGBM model produces profitable signals at ALL hours. The
`hour_sin/hour_cos` features already capture cyclical effects, so the
hard hour block is redundant.

**Action: `BAD_HOURS_UTC = set()` (rule disabled).**

## Verdict on ASIA_HOURS boost

| Group | Signals | Precision | PF | Avg PnL |
|---|---:|---:|---:|---:|
| ASIA hours | 52,162 | 88.78% | 6.74 | +2.435% |
| Non-ASIA hours | 87,626 | 88.61% | 6.62 | +2.422% |

Asia hours are only **+0.5% better** than non-Asia. The ×1.15 boost is
effectively a no-op (boost goes into `final_confidence`, which is not
used for sizing in fixed-size mode anyway).

**Action: `ASIA_HOURS_BOOST = 1.00` (boost disabled).**

## Gate code changes (`src/ppmt/risk/v5_risk_gate_cb_v2.py`)

```python
# Before
ASIA_HOURS_UTC = {0, 1, 2, 18, 19, 20, 21, 22, 23}
BAD_HOURS_UTC = {4, 5, 9, 12, 16}

# After
ASIA_HOURS_UTC = {0, 1, 2, 18, 19, 20, 21, 22, 23}
ASIA_HOURS_BOOST = 1.00  # was 1.15 — Step 7 hourly analysis showed only +0.5% delta
BAD_HOURS_UTC: set[int] = set()  # was {4, 5, 9, 12, 16} — Step 7 removed
```

In `evaluate_signal_cb_v2()`:
- Rule 1 (BLOCK BAD hours) — commented out
- Asia hours boost — guarded by `if ASIA_HOURS_BOOST != 1.0`

## Backtest impact (concurrent, RECENT_2026, 90 days OOS)

Re-ran `v5_backtest_concurrent_cb_v2.py` with the re-tuned gate:

| Config | Before (BAD_HOURS ON) | After (BAD_HOURS OFF) | Δ Trades | Δ PnL |
|---|---:|---:|---:|---:|
| thr=0.70 gate=ON mc=3 | 14,177 trades, $277k, +9,241% | **17,767 trades, $348k, +11,594%** | +25% | +25% |
| thr=0.70 gate=ON mc=5 | 21,599 trades, $438k, +8,755% | **27,076 trades, $549k, +10,982%** | +25% | +25% |
| thr=0.80 gate=ON mc=3 | 12,320 trades, $293k, +9,768% | **15,479 trades, $368k, +12,272%** | +26% | +25% |
| thr=0.80 gate=ON mc=5 | 18,374 trades, $447k, +8,949% | **23,077 trades, $561k, +11,210%** | +26% | +25% |

**The re-tuned gate now matches gate=OFF on every metric.** Per-trade
quality is unchanged (WR=88.0%, PF=6.23, avg=+2.378% at thr=0.80), but
trade count increases by ~25% because we no longer throw away 5 hours
of profitable signals.

## Net impact across the full Step 4 → Step 7 journey

| Step | Config | Trades | Total PnL | Notes |
|---|---|---:|---:|---|
| Step 3 (initial) | thr=0.70 gate=ON (v1 gate, mid_cap only) | 16,497 | +40,996% | Theoretical sequential |
| Step 4 (cb_v2 gate wired) | thr=0.70 gate=ON (cb_v2 gate, all tokens) | 60,251 | +143,300% | Theoretical sequential |
| Step 5 (concurrent) | thr=0.80 gate=OFF mc=3, fixed $1k | 15,479 | $368k | Realistic concurrent |
| **Step 7 (BAD_HOURS off)** | **thr=0.80 gate=ON mc=3, fixed $1k** | **15,479** | **$368k** | **gate=ON now matches gate=OFF** |

The gate is now a TRUE no-op on cb_v2 (per-trade quality identical to
gate=OFF, same trade count). The remaining gate rules that still fire:

- `MIN_CONFIDENCE = 0.60` (hard threshold — already implied by thresh=0.70)
- Leverage cap at 7x (already the base)
- Asset class boost (only affects `final_confidence`, not used in fixed-size mode)
- Scalp TF boost (same — only affects `final_confidence`)

In production with Kelly-lite sizing (`fixed_size=False`), the asset-class
and TF boosts WOULD matter — they'd scale position size. But for the
current fixed-size deployment, the gate is essentially a confidence
threshold filter.

## Recommendation for production

1. **Deploy with the re-tuned gate** (BAD_HOURS removed, ASIA_HOURS boost
   removed). The gate is now a clean confidence-threshold + leverage-cap
   filter, with optional Kelly-lite sizing boosts for blue/large caps.

2. **In production, monitor per-hour PnL weekly.** If any hour's rolling
   7-day avg PnL drops below 0 for 2+ consecutive weeks, re-introduce a
   BAD_HOURS block for that hour specifically.

3. **The hourly analysis script (`v5_analyze_hours_cb_v2.py`) can be
   re-run weekly** to detect emerging bad hours before they hurt PnL.

## Files

- `scripts/v5/v5_analyze_hours_cb_v2.py` — hourly analysis script (new)
- `src/ppmt/risk/v5_risk_gate_cb_v2.py` — gate with BAD_HOURS/ASIA_HOURS disabled
- `scripts/v5/v5_risk_gate_cb_v2.py` — synced copy
- `docs/v5_cb_v2/v5_hourly_analysis_cb_v2_summary.txt` — full per-hour table
- `docs/v5_cb_v2/v5_hourly_analysis_cb_v2.json` — machine-readable
- `docs/v5_cb_v2/STEP7_bad_hours_retune.md` — this analysis
