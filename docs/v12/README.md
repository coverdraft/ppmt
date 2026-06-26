# V12 — Low-TF Microstructure Pipeline

**Version**: V12 (extends V11)
**Status**: Walk-forward validated, paper trading in progress
**Date**: 2026-06-27

## Overview

V12 extends the V11 low-timeframe model (1m base data, 80 microstructure features,
H=12 horizon) with optimized trading parameters. The key insight: the V11 model
already had predictive edge, but the trading parameters (quantile thresholds,
direction mode, trend filter) were suboptimal. V12 improves WR from 0.39 (V10)
to 0.65-0.69 through parameter optimization, not model architecture changes.

## Architecture

```
1m OHLCV candles
       |
       v
  Aggregate to 5m bars
       |
       v
  Compute 80 features:
    - Base indicators (RSI, EMA, ATR, Bollinger, etc.)
    - Microstructure (CVD_5m, vol_delta, price_impact, trade_imbalance)
    - MTF alignment (5m/15m/1h trend consensus)
    - BTC/ETH correlation (btc_corr_30, eth_corr_30)
       |
       v
  LightGBM Binary Classifier:
    P(UP in 1h) = model.predict(features)
    Label = 1 if fwd_ret_h12 > 0
       |
       v
  Quantile-Based Signal Generation:
    Rolling window = 200 bars
    LONG if pred > Q_long percentile (e.g. 95th)
    SHORT if pred < Q_short percentile (e.g. 5th)
    WAIT otherwise
       |
       v
  Direction & Trend Filters:
    - direction_mode: both / long_only / short_only
    - trend_filter: none / aligned (long only when trend_1h >= 0)
       |
       v
  Position Management:
    Hold for H=12 bars (1h)
    Can re-enter after hold period
    One position per symbol at a time
```

## Feature Set (80 features)

### Base Indicators (~30)
RSI, EMA (5/10/20/50), ATR, Bollinger bands, body/wick ratios,
volume ratios, returns (1/3/6/12/24 bars), high-low range

### Microstructure (~15)
CVD_5m (cumulative volume delta), vol_delta (buy vs sell volume),
price_impact (price change per unit volume), trade_imbalance,
vwap_deviation, orderflow_imbalance

### Multi-Timeframe (~20)
trend_5m, trend_15m, trend_1h, RSI on 15m/1h,
MTF alignment score, trend_strength across timeframes

### Cross-Asset (~10)
btc_corr_30, eth_corr_30, btc_ret_1h, btc_ret_24h,
eth_ret_1h, relative_strength vs BTC

### Temporal (~5)
hour_sin, hour_cos, day_of_week, is_weekend

## V12 Optimization Process

### Step 1: V12 Analyze (`v12_analyze.py`)
Analyzed what separates winning from losing trades:
- Signal strength (prediction value) is the most powerful discriminator
- RSI filters help marginally
- Hour-of-day has some effect but not consistent
- MTF alignment helps for conservative configs

### Step 2: V12 Optimize (`v12_optimize.py`)
Exhaustive search over 108 configs:
- 9 quantile configs (Q80/20 through Q98/2)
- 2 direction modes (both, long_only)
- 2 trend filters (none, aligned)
- 3 symbols (SOL, DOGE, AVAX)

Key finding: Higher quantile selectivity = better WR but fewer trades.

### Step 3: V12 Validate (`v12_validate.py`)
Walk-forward validation with 6 windows:
- Each window: retrain model on expanding training set
- Test on next 7% of data (chronological)
- Verified that best configs are robust across all windows

### Step 4: Cost-Aware Labels (ABANDONED)
Tested labels where label=1 only if fwd_ret > 2x maker fee (0.08%).
Result: WORSE performance. The model already learns profitability implicitly.
Standard binary labels (UP/DOWN) are better.

### Step 5: Adaptive Exits (`v12_adaptive_exit.py`)
Tested trailing stops and momentum-based exits.
Result: Fixed hold period (H=12) remains best for most configs.

## Best Configurations

### SOL — Balanced Profile (RECOMMENDED)
```python
{
    "q_long": 95, "q_short": 5,
    "direction": "both",
    "trend_filter": "none",
}
```
- Walk-forward WR: 0.693 (4/4 windows profitable)
- PF: 3.35, Sharpe: +0.385

### DOGE — Balanced Profile (RECOMMENDED)
```python
{
    "q_long": 95, "q_short": 5,
    "direction": "both",
    "trend_filter": "none",
}
```
- Walk-forward WR: 0.649 (6/6 windows profitable)
- PF: 2.40, Sharpe: +0.277

### DOGE — Conservative Profile
```python
{
    "q_long": 98, "q_short": 2,
    "direction": "both",
    "trend_filter": "none",
}
```
- Walk-forward WR: 0.681 (6/6 windows profitable)
- PF: 3.03, Sharpe: +0.343

### AVAX — Conservative Profile
```python
{
    "q_long": 97, "q_short": 3,
    "direction": "long_only",
    "trend_filter": "aligned",
}
```
- Walk-forward WR: 0.625 (6/6 windows profitable)
- PF: 3.35, Sharpe: +0.383

### AVAX — Balanced Profile
```python
{
    "q_long": 95, "q_short": 5,
    "direction": "both",
    "trend_filter": "aligned",
}
```
- Walk-forward WR: 0.622 (6/6 windows profitable)
- PF: 2.62, Sharpe: +0.301

## Improvement Over Previous Versions

| Metric | V10 | V11 | V12 | Change V10->V12 |
|--------|-----|-----|-----|------------------|
| WR (best) | 0.39 | 0.61 | 0.693 | +77% |
| PnL | Negative | Marginal | Positive all windows | Confirmed |
| Consistency | 0/4 | 2/4 | 4-6/6 | Robust |
| Timeframe | 1m base | 1m base | 1m base | Same |
| Horizon | H=12 | H=12 | H=12 | Same |
| Features | 80 | 80 | 80 | Same |
| Trading params | Default | Default | Optimized | KEY CHANGE |

The improvement from V10 to V12 is entirely from trading parameter optimization,
not model architecture. This validates that the V11 model had edge all along —
it just needed better signal extraction via quantile selectivity.

## Files

| File | Purpose |
|------|---------|
| `scripts/v11/v11_build_dataset.py` | Build 80-feature dataset from 1m data |
| `scripts/v11/v11_train.py` | Train binary classifier per symbol x horizon |
| `scripts/v11/v11_backtest.py` | Fixed + adaptive exit backtesting |
| `scripts/v12/v12_analyze.py` | Feature/filter analysis |
| `scripts/v12/v12_optimize.py` | Exhaustive parameter optimization |
| `scripts/v12/v12_validate.py` | Walk-forward validation of best configs |
| `scripts/v12/v12_adaptive_exit.py` | Trailing stop testing |
| `scripts/v12/v12_build_dataset.py` | Cost-aware labels + extra features (abandoned) |
| `scripts/v12/v12_train.py` | Signal gating experiments |
| `scripts/v12/v12_train_final.py` | Cost-aware label training (abandoned) |
| `data/v12/v12_optimization_results.csv` | All 108 optimization results |
| `data/v12/v12_validation_results.json` | Walk-forward validation results |
| `data/v12/V12_SUMMARY.md` | Quick summary of results |

## Failed Experiments

1. **Cost-aware labels** (threshold 0.08%): WORSE WR and PnL
2. **Signal gating** (RSI filter, MTF gate): Marginal improvement, not worth complexity
3. **Trailing stop exits**: Worse than fixed hold period
4. **V12 new features** (CVD divergence, vol surge, momentum accel): Not tested in final validation

These were explored but did not improve over the simple quantile-selective approach.
