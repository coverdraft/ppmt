# PPMT v6 — Design (post v5_cb_v2 leakage)

**Date:** 2026-06-24
**Status:** Design (implementation pending)
**Context:** v5_cb_v2 was discarded due to data-leakage in training. AUC 0.94 was illusory; real AUC = 0.54. See `v5_leakage_postmortem.md`.

---

## Goals

1. A model with **real edge** on 5m Coinbase crypto (AUC > 0.60, WR > 55%, PF > 1.3 net of costs)
2. Strict anti-leakage guards baked into the pipeline
3. Tradeable in production via paper trader → live

---

## What changed vs v5_cb_v2

### Label

| v5_cb_v2 | v6 |
|----------|----|
| `label_hit_tp_first` (binary: +0.6% TP before -0.4% SL in 6 bars 5m) | `fwd_ret_15m` (regression: % return 3 bars ahead) |
| Classification (binary) | **Regression (L2)** |
| Fixed TP/SL baked into label | Threshold tuned post-training on prediction |

**Why:** Predicting a continuous return gives the model more signal than a binary outcome. Entry threshold becomes a tunable hyperparameter instead of being baked into the label, so we can adjust for changing market regimes without retraining.

### Features

Keep all 38 v5 features (verified not leakage). Add 21 new features:

#### Multi-timeframe (8 features, NEW)
- `btc_ret_1m`, `btc_ret_5m`, `btc_ret_15m` — BTC returns in short windows (BTC leads altcoins)
- `btc_vol_z` — z-score of BTC volume
- `btc_trend_50` — sign of (EMA9 − EMA50) on BTC
- `eth_corr_30` — rolling 30-bar correlation between symbol and ETH
- `btc_alt_spread_15m` — (alt_pct_change − btc_pct_change) over 15m
- `btc_volatility_regime` — vol_regime of BTC (0–3)

#### Microstructure approximations (6 features, NEW)
- `vol_delta_3` — change of volume over last 3 bars (proxy for order flow)
- `wick_imbalance_3` — sum of (lower_wick − upper_wick) over last 3 bars
- `body_consistency_5` — fraction of last 5 bars with body > 0
- `range_expansion_3` — ratio (avg range_pct last 3) / (avg range_pct last 20)
- `close_persistence_5` — fraction of last 5 closes above EMA20
- `vol_acceleration` — (vol_ratio − vol_ratio.shift(3))

#### Improved regime (4 features, NEW)
- `atr_percentile_50` — rolling 50-bar percentile of atr_pct (0–1)
- `trend_strength_50` — |EMA9 − EMA50| / atr_pct
- `regime_vol_trend` — interaction: vol_regime × trend_50
- `hour_quantile` — 0=asia (00–08 UTC), 1=europe (08–14), 2=us (14–22), 3=overlap

#### Cross-asset timing (3 features, NEW)
- `alt_lead_5m` — symbol return minus BTC return over last 5m
- `alt_lag_signal` — 1 if BTC moved >0.2% in last 1m but symbol didn't
- `momentum_dispersion` — std of 1m returns over last 10 bars

**Total v6:** 38 (v5) + 21 (new) = **59 features**

### Data

- Same windows: BEAR_2022, BULL_2024, RANGE_2025, RECENT_2026
- Same 12 tokens (BTC/ETH/SOL/XRP/ADA/AVAX/LINK/DOGE/SHIB/PEPE/WIF/BONK)
- Timeframe: 5m only (cleaner than mixing 1m/5m/15m)
- Expected ~104K rows (12 symbols × ~8.7K bars × 4 regimes in 5m)

### Model

- LightGBM regression (`objective=regression`, `metric=rmse` + `l1`)
- Hyperparameters: `num_leaves=31, lr=0.05, n_estimators=500, early_stopping=50`
- Remove `is_unbalanced` (regression, not classification)
- Walk-forward: 6 monthly windows over RANGE_2025 + RECENT_2026

### Entry/exit (backtest & paper trader)

- Predict `fwd_ret_15m` for each closed 5m candle
- Enter LONG if `pred > 0.15%` (covers 0.14% round-trip costs)
- Exit at +15m (3 bars) — no intra-trade TP/SL (simpler, more robust)
- Position size: $100, leverage 7x, max concurrent 3, account $10K
- Metrics: WR (pred>0 → fwd_ret>0), avg_pnl, PF, Sharpe

---

## Anti-leakage guards (mandatory)

These run automatically as part of `v6_extract_features.py`:

1. **Name collision check:** All forward-looking columns MUST start with `fwd_`. The script asserts no feature column name starts with `fwd_`.
2. **Feature range validation:** Each feature has an expected range (e.g., `body_pct ∈ [-1, 1]`, `ret_3 ∈ [-0.05, 0.05]`). If observed range >2× expected, abort.
3. **Feature importance sanity check (post-train):** No single feature may account for >30% of total gain. If it does, flag as suspicious — likely leakage.
4. **AUC ceiling:** If train AUC > 0.85 on 5m crypto, abort with "suspicious — possible leakage".
5. **Walk-forward variance check:** If AUC std across 6 windows > 0.05, flag as unstable.

---

## Implementation plan (when we resume)

1. ✅ Fix `v5_extract_features_cb.py` (rename `ret_{H}` → `fwd_ret_{H}`) — DONE 2026-06-24
2. ⏳ Wipe `feature_observations_cb` in production DB
3. ⏳ Re-extract v5 features (clean, no leakage) — ~30 min on user's Mac
4. Build `v6_extract_features.py` with 59 features
5. Build `v6_train.py` (regression, walk-forward)
6. Build `v6_backtest.py` (with threshold sweep)
7. Compare v6 vs v5-baseline (real AUC, no leakage)
8. If v6 has edge → port paper trader to v6
9. If v6 no edge → iterate: more features, different label, different TF

---

## Open questions

- Should we include `fwd_ret_3m` and `fwd_ret_30m` as additional regression targets (multi-task)?
- Should we add funding rate as a feature for perp-listed tokens?
- Should we use order-book L2 data from Coinbase Advanced if available? (out of scope for v6.0)
