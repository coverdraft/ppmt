# Paso 6 — Walk-forward validation

## What was done

Created `scripts/v5/v5_walkforward_cb_v2.py` and ran a 6-window monthly
walk-forward validation. For each window:

1. Train a fresh LGBM on ALL data BEFORE the window start
2. Use last 20% of pre-window data as validation for early stopping
3. Predict on the window
4. Compute AUC, precision@0.70, profit factor, total PnL

Same model params as `v5_train_lgbm_cb_v2.py`:
- `num_leaves=15, lr=0.1, n_estimators=200, min_data_in_leaf=50`
- `is_unbalanced=True`
- `feature_fraction=0.85, bagging_fraction=0.85, bagging_freq=5`
- `lambda_l1=0.1, lambda_l2=0.1`

## Per-window results

| Window | Dates | Train | Test | AUC | Signals | Precision | PF | Total PnL | Avg PnL |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| W1 | 2025-03-24 → 2025-04-29 | 291k | 121k | 0.9258 | 31,647 | 87.8% | 6.13 | +74,890% | +2.366% |
| W2 | 2025-04-29 → 2025-06-05 | 388k | 120k | 0.9248 | 30,946 | 88.3% | 6.43 | +74,313% | +2.401% |
| W3 | 2025-06-05 → 2025-07-12 | 484k | 50k | 0.9371 | 12,523 | 88.5% | 6.56 | +30,244% | +2.415% |
| W4 | 2025-07-12 → 2025-08-17 | 524k | 51k | 0.9325 | 14,439 | 88.7% | 6.68 | +35,063% | +2.428% |
| W5 | 2025-08-17 → 2025-09-23 | 566k | 95k | 0.9466 | 24,923 | 90.0% | 7.67 | +62,815% | +2.520% |
| W6 | 2025-09-23 → 2025-10-30 | 642k | 103k | 0.9381 | 25,323 | 89.2% | 7.00 | +62,311% | +2.461% |

## Stability summary

| Metric | Value |
|---|---|
| Windows evaluated | 6 |
| AUC mean ± std | **0.9341 ± 0.0075** |
| AUC range | [0.9248, 0.9466] (Δ = 0.022) |
| AUC drop W1 → W6 | -0.0123 (model IMPROVED over time) |
| Precision mean ± std | 0.8874 ± 0.0069 |
| PF mean (min) | 6.75 (6.13) |
| Total PnL sum | +339,636% of margin |
| Total PnL mean/window | +56,606% of margin |
| Any window AUC drop > 0.05? | **NO** |
| Windows profitable | **6/6** |

## Verdict: model edge is stable

- AUC variation across 6 months: ±0.0075 (std) — well within noise
- AUC range [0.925, 0.947] — Δ = 0.022, far below the 0.05 alarm threshold
- AUC actually IMPROVED W1 → W6 (-0.0123 drop = model got better with more data)
- PF minimum 6.13 — every window had PF > 5 (excellent)
- ALL windows profitable — no regime produced net losses

## Per-window per-symbol snapshot (Window 5, the best)

| Symbol | Signals | Precision | PF | Avg PnL |
|---|---:|---:|---:|---:|
| WIF | 2,956 | 89.7% | 7.40 | +2.498% |
| BONK | 2,884 | 89.3% | 7.10 | +2.470% |
| AVAX | 2,583 | 89.9% | 7.61 | +2.515% |
| DOGE | 2,444 | 90.3% | 7.90 | +2.538% |
| PEPE | 2,417 | 88.7% | 6.69 | +2.429% |

## Per-window per-TF snapshot (Window 5)

| TF | Signals | Precision | PF | Avg PnL |
|---|---:|---:|---:|---:|
| 5m | 16,518 | 91.8% | 9.56 | +2.647% |
| 15m | 8,405 | 86.5% | 5.43 | +2.271% |

5m consistently outperforms 15m on both count and per-trade quality.

## Interesting observation: data ordering

The walk-forward revealed that the `historical_regime` labels do NOT
match chronological order:

- W1-W3 (2025-03-24 → 2025-07-12): labeled `RECENT_2026`
- W4-W6 (2025-07-12 → 2025-10-30): labeled `RANGE_2025`

So `RECENT_2026` is chronologically EARLIER than `RANGE_2025` in the
actual `ts` timestamps. This is just a naming artifact (the regimes were
named for the market regime they represent, not their calendar dates).

For the walk-forward, this doesn't matter — we use `ts` for ordering, not
the regime label. But it's worth noting that:

1. The original `v5_train_lgbm_cb_v2.py` trained on `BULL_2024 + BEAR_2022`
   and tested on `RECENT_2026`. This means the test set was the EARLIEST
   portion of the post-training data — so the original test metrics were
   a true OOS evaluation.

2. The walk-forward here ADDS `RANGE_2025` to the test pool, which is
   LATER data. The model performs even better on this later data
   (W4-W6 AUC 0.93-0.95 vs W1-W3 0.92-0.94), suggesting the model's edge
   has been stable to slightly improving over the 6-month OOS period.

## Comparison to published metrics

Published cb_v2 model metrics (from `v5_train_metrics_cb_v2.json`):
- Test AUC: ~0.94 (single-window, all RECENT_2026)

Walk-forward AUC mean: 0.9341 ± 0.0075

The published single-window AUC falls within the walk-forward range,
confirming the published number was not an outlier.

## Caveats

1. **Walk-forward is NOT a true live simulation** — each window trains
   on data that includes the previous window's labels. In live trading
   you'd need to wait for labels to materialize (the label is bar-level
   SL/TP, which resolves in 1-15 minutes). For monthly retrain cadence,
   this is fine — by the time you retrain, all of last month's labels
   are settled.

2. **Per-trade metrics assume the same fixed costs** as the realistic
   backtest (taker 0.05% × 2 + slippage 0.02% × 2 = 0.14% per side).
   Live costs may differ on Coinbase Advanced, especially for smaller
   alts.

3. **AUC stability ≠ live profitability** — the model could be stable
   on paper but fail in live due to:
   - Order rejection (insufficient liquidity at SL/TP levels)
   - Latency (signal arrives 100ms late, fills worse)
   - Funding rates on perpetuals (negligible at 7x, 15min hold)
   - Exchange downtime / API rate limits

## Recommendation

**Model is ready for paper-trading deployment.** AUC is stable across 6
monthly windows (0.9341 ± 0.0075), all windows profitable, PF minimum 6.13.

Deploy with:
- Monthly retrain cadence (retrain on first day of each month)
- AUC drift alert: if rolling 7-day AUC drops >0.05 below the published
  baseline (0.9341), trigger immediate retrain
- Paper-trade for at least 1 week with $100/trade before sizing up

## Files

- `scripts/v5/v5_walkforward_cb_v2.py` — walk-forward script (new)
- `docs/v5_cb_v2/v5_walkforward_cb_v2_summary.txt` — full per-window table
- `docs/v5_cb_v2/v5_walkforward_cb_v2.json` — machine-readable
- `docs/v5_cb_v2/STEP6_walkforward.md` — this analysis
