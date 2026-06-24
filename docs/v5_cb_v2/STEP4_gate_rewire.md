# Paso 4 — Wire cb_v2 risk gate into backtest + validate

## Problem found

The cb_v2 risk gate (`src/ppmt/risk/v5_risk_gate_cb_v2.py`) was created in
Step 3 but was NEVER actually used by the realistic backtest. The backtest
was still importing the old v1 Binance gate (`v5_risk_gate.py`), which has
three problems for cb_v2:

1. **`direction` was set to `"SHORT"` for all cb_v2 signals.** The line
   `df["direction"] = np.where(df["prior_expected_move"] > 0, "LONG", "SHORT")`
   returned `"SHORT"` for every signal because `prior_expected_move` is 0.0
   in cb_v2 (trie priors were never computed).

2. **The v1 gate blocks SHORTs on `blue_chip | large_cap | meme`.** Rule 1
   in `v5_risk_gate.py`:
   ```python
   if sig.direction == "SHORT" and sig.asset_class in NO_SHORT_CLASSES:
       return BLOCKED
   ```
   This killed BTC, ETH, SOL, XRP, DOGE, SHIB, PEPE, WIF, BONK signals —
   leaving only ADA/AVAX/LINK (mid_cap SHORTs are not blocked by v1).

3. **The v1 gate dampens blue_chip confidence ×0.80.** Even if direction
   were fixed, BTC/ETH would still be dampened, contradicting cb_v2 OOS
   where BTC precision = 92.5% and ETH = 91.1%.

## Fix

`scripts/v5/v5_backtest_realistic_cb_v2.py` now:

- Imports `SignalV5Cb, evaluate_signal_cb_v2` from the cb_v2 gate
- Forces `direction="LONG"` everywhere (cb_v2 label is LONG-directional by
  construction: `label_hit_tp_first = 1` means price hit +0.6% TP before
  -0.4% SL on a LONG)
- Passes `expected_move_pct=0.0` and `win_rate=0.0` (cb_v2 gate doesn't
  use them anyway)

Also committed the old v1 gate (`src/ppmt/risk/v5_risk_gate.py`) to git
— it was untracked, even though other code references it.

## Results — RECENT_2026 (out-of-sample)

| Metric | Step 3 (gate v1) | Step 4 (gate cb_v2) | Δ |
|---|---:|---:|---:|
| Trades @ thresh=0.70 gate=ON | 16,497 | **60,251** | **+3.65×** |
| Win rate | 89.5% | 88.0% | -1.5 pp |
| Profit factor | 7.26 | 6.23 | -14% |
| Avg net PnL/trade | +2.49% | +2.38% | -4% |
| Total net PnL | ~41,000% | **143,300%** | **+3.5×** |

The slight per-trade degradation (-4%) is more than offset by the +3.65×
trade count. Total PnL triples.

### Per-symbol @ thresh=0.70 gate=ON

| Symbol | Asset Class | Trades | WR | PF | Avg PnL |
|---|---|---:|---:|---:|---:|
| AVAX | mid_cap | 5,838 | 89.1% | 6.93 | +2.45% |
| LINK | mid_cap | 5,480 | 89.6% | 7.32 | +2.49% |
| BONK | meme | 6,256 | 84.0% | 4.49 | +2.10% |
| WIF | meme | 6,390 | 83.2% | 4.21 | +2.04% |
| ADA | mid_cap | 5,179 | 89.9% | 7.60 | +2.51% |
| PEPE | meme | 5,957 | 85.1% | 4.85 | +2.17% |
| SHIB | meme | 5,143 | 89.6% | 7.35 | +2.49% |
| DOGE | meme | 5,306 | 88.5% | 6.56 | +2.42% |
| SOL | large_cap | 4,836 | 90.2% | 7.88 | +2.54% |
| ETH | blue_chip | 4,074 | 91.1% | 8.71 | +2.60% |
| XRP | large_cap | 3,955 | 89.1% | 6.95 | +2.46% |
| **BTC** | blue_chip | 1,837 | **92.5%** | **10.49** | **+2.69%** |

**Key insight:** BTC has the BEST profit factor (10.49) and win rate
(92.5%) of all 12 tokens — exactly what the cb_v2 OOS precision analysis
predicted. The v1 gate was throwing these away.

### Per-TF @ thresh=0.70 gate=ON

| TF | Trades | WR | PF | Avg PnL |
|---|---:|---:|---:|---:|
| 5m | 43,777 | 89.4% | 7.19 | +2.48% |
| 15m | 16,474 | 84.2% | 4.53 | +2.11% |

5m timeframe is clearly superior — more trades AND higher PF.

## Compounded growth (sequential, full margin redeployed)

At avg net PnL = +2.378% per trade over 90-day test window:

| Trades | Trades/day | Final account |
|---:|---:|---:|
| 10 | 0.11 | 1.27× (+27%) |
| 50 | 0.56 | 3.24× (+224%) |
| 100 | 1.11 | 10.49× (+949%) |
| 200 | 2.22 | 110.07× (+10,907%) |
| 500 | 5.56 | 127,099× (+12,709,805%) |

Note: these assume one-at-a-time sequential trades. Real-world capacity is
limited by:
- Overlapping signals (multiple signals fire on the same bar)
- Capital allocation per position (not full margin redeploy)
- Slippage growth with size

## Files changed in this commit

- `scripts/v5/v5_backtest_realistic_cb_v2.py` — re-wired to use cb_v2 gate
- `src/ppmt/risk/v5_risk_gate.py` — old v1 gate committed (was untracked)
- `docs/v5_cb_v2/v5_realistic_backtest_cb_v2_summary.txt` — updated backtest output
- `docs/v5_cb_v2/v5_realistic_backtest_cb_v2.json` — updated backtest JSON

## Next steps

1. **Backtest with real concurrency** — N simultaneous positions with
   capital allocation (the existing `v5_backtest_concurrent_cb_v2.py`
   from the previous commit can be re-run with the new gate).
2. **Walk-forward validation** — re-train on rolling windows to detect
   regime drift.
3. **Live paper-trading** — deploy on Coinbase Advanced paper.
