# RUNBOOK — PPMT V12 Paper Trading (1h Microstructure)

**Status:** OPERATIONAL · **Last updated:** 2026-06-27
**Code:** `scripts/v12/paper_trader/` (Python package)
**Outputs:** `data/paper_trading/v12_logs/`, `data/paper_trading/v12_state/`

---

## 1. Purpose

Validate the V12 low-timeframe model (80 microstructure features, LightGBM,
quantile-based trading with optimized thresholds) under live market conditions.

The V12 pipeline:
- Uses 1m OHLCV data aggregated to 5m bars
- Predicts P(UP in 1h) with LightGBM binary classifier
- Applies V12-validated quantile thresholds (Q95/5, Q97/3, Q98/2)
- Supports direction mode (both/long_only) and trend alignment filters
- Holds positions for H=12 bars (1h), then can re-enter

Ship criteria for going live:

| Metric        | Threshold   | Source            |
|---------------|-------------|-------------------|
| Sharpe (2-4w) | > 0.3       | paper-trade log   |
| MaxDD         | > -15%      | paper-trade log   |
| Win rate      | > 55%       | paper-trade log   |
| N trades      | >= 30       | paper-trade log   |

Note: V12 targets 1h trades, so we expect ~3-5 trades per day per symbol.
With 3 symbols, we should have 30+ trades in 2-3 days.

---

## 2. Architecture

```
                ┌─────────────────────────────────────────┐
                │           BYBIT PUBLIC API               │
                │   (no key needed for OHLCV spot)         │
                └────────────────┬────────────────────────┘
                                 │  fetch_ohlcv 1m
                                 ▼
┌──────────────────────────────────────────────────────────┐
│  Feed (scripts/v12/paper_trader/feed.py)                 │
│  - fetch_5m_window(): fetches 1m, aggregates to 5m      │
│  - wait_for_next_5m_close(): polls every 15s             │
└────────────────────────┬─────────────────────────────────┘
                         │  sym_5m, btc_5m, eth_5m DataFrames
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Features (features.py)                                  │
│  - 13 microstructure (CVD, vol_delta, price_impact)      │
│  - 37 5m base (RSI, EMA, ATR, body/wick, ...)           │
│  - 21 BTC/ETH cross-asset (corr, spread, BTC trend)     │
│  - 4 15m features (trend, RSI, vol regime, BTC trend)   │
│  - 5 1h features (trend, RSI, vol regime, BTC, MTF)     │
│  - = 80 features total                                   │
└────────────────────────┬─────────────────────────────────┘
                         │  latest_feature_row()
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Model (model.py) — V11 LightGBM binary classification   │
│  - P(UP in 1h) = model.predict(features)                 │
│  - Models in data/v11/models/v11_clf_{SYM}_h12.txt      │
│  - V12 SYMBOL_CONFIG: per-symbol Q, direction, trend     │
└────────────────────────┬─────────────────────────────────┘
                         │  (pred, decision)
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Engine (engine.py) — position management                │
│  - Rolling quantile window for signal generation          │
│  - V12 filters: direction_mode + trend_filter            │
│  - Hold H=12 bars (1h), then can re-enter               │
│  - Close on reverse signal or after H=12 bars            │
│  - Maker cost: 0.04% (limit orders)                     │
│  - Persists state to JSON, logs to CSV                   │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Logs (data/paper_trading/v12_logs/)                     │
│  - signals_v12_{SYM}.csv: 1 row per 5m candle            │
│  - equity_v12_{SYM}.csv: 1 row per 5m candle             │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Per-Symbol Config (from V12 Walk-Forward Validation)

| Symbol | Profile | Q Long | Q Short | Direction | Trend | WR (WF) | Consistency |
|--------|---------|--------|---------|-----------|-------|---------|-------------|
| SOL | Balanced | 95 | 5 | both | none | 0.693 | 4/4 |
| SOL | Conservative | 95 | 5 | long_only | aligned | 0.738 | 4/4 |
| DOGE | Balanced | 95 | 5 | both | none | 0.649 | 6/6 |
| DOGE | Conservative | 98 | 2 | both | none | 0.681 | 6/6 |
| AVAX | Balanced | 95 | 5 | both | aligned | 0.622 | 6/6 |
| AVAX | Conservative | 97 | 3 | long_only | aligned | 0.625 | 6/6 |

All configs use maker fees (0.04%), requiring LIMIT ORDERS.

---

## 4. Quick start

### 4.1 Verify models exist

```bash
cd ~/ppmt && source .venv/bin/activate

# Check model files
ls -la data/v11/models/v11_clf_*_h12.txt

# Check status
python -m scripts.v12.paper_trader --status --symbol SOL
```

### 4.2 If models don't exist, train V11 first

```bash
# Build dataset
python scripts/v11/v11_build_dataset.py

# Train models for H=12
python scripts/v11/v11_train.py --horizon 12
```

### 4.3 Smoke test (single cycle)

```bash
python -m scripts.v12.paper_trader --symbol SOL --once
```

### 4.4 Run paper trading

```bash
# Single symbol (foreground)
python -m scripts.v12.paper_trader --symbol SOL

# Single symbol (background)
nohup python -m scripts.v12.paper_trader --symbol SOL > /tmp/v12_SOL.log 2>&1 &

# Conservative profile
python -m scripts.v12.paper_trader --symbol DOGE --profile conservative

# All V12 symbols (SOL, DOGE, AVAX)
nohup python -m scripts.v12.paper_trader --all > /tmp/v12_all.log 2>&1 &
```

### 4.5 Cron mode (every 5 minutes)

```cron
*/5 * * * * sleep 30 && cd ~/ppmt && source .venv/bin/activate && \
    python -m scripts.v12.paper_trader --symbol SOL --once \
    >> /tmp/v12_SOL.cron.log 2>&1
```

---

## 5. Logs schema

### 5.1 signals_v12_{SYM}.csv

| Column              | Description                                                   |
|---------------------|---------------------------------------------------------------|
| ts_utc              | Candle close timestamp (ms since epoch)                       |
| ts_iso              | ISO-8601 UTC timestamp                                        |
| symbol              | Token symbol (SOL, DOGE, AVAX)                                |
| close               | Candle close price                                            |
| pred                | Model prediction P(UP in 1h) [0-1]                           |
| decision            | LONG, SHORT, or WAIT (after V12 filters)                     |
| q_high              | Rolling Q_LONG percentile threshold                           |
| q_low               | Rolling Q_SHORT percentile threshold                          |
| direction_mode      | both / long_only / short_only                                 |
| trend_filter        | none / aligned                                                |
| action              | OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT, REVERSE_TO_*, HOLD, NO_ACTION |
| position_side       | LONG, SHORT, or empty                                         |
| position_bars_held  | How many 5m bars the current position has been held           |
| entry_price         | Filled entry price                                            |
| exit_price          | Filled exit price                                             |
| pnl_pct             | Gross PnL of the closed trade in %                            |
| cost_pct            | Round-trip cost in % (0.04% maker)                            |
| pnl_net_pct         | pnl_pct - cost_pct                                            |
| equity_pct          | Cumulative net PnL since engine start                         |

### 5.2 equity_v12_{SYM}.csv

| Column     | Description                                  |
|------------|----------------------------------------------|
| ts_utc     | Candle close timestamp                       |
| ts_iso     | ISO timestamp                                |
| equity_pct | Cumulative net PnL                           |
| n_trades   | Total closed trades since start              |
| n_wins     | Trades with pnl_net_pct > 0                  |
| win_rate   | n_wins / n_trades                            |

---

## 6. Decision logic

### 6.1 V12 Signal generation

```python
# Each 5m candle:
pred = model.predict(features)  # P(UP in 1h)

# Update rolling window
recent_preds.append(pred)
if len(recent_preds) > window_size:
    recent_preds = recent_preds[-window_size:]

# Compute quantile thresholds
q_high = np.percentile(recent_preds, Q_LONG)   # e.g. 95th
q_low = np.percentile(recent_preds, Q_SHORT)    # e.g. 5th

# Base decision
if pred > q_high:
    decision = "LONG"
elif pred < q_low:
    decision = "SHORT"
else:
    decision = "WAIT"

# Direction mode filter
if decision == "SHORT" and direction_mode == "long_only":
    decision = "WAIT"

# Trend alignment filter
if trend_filter == "aligned":
    if decision == "LONG" and trend_1h < 0:
        decision = "WAIT"  # counter-trend long blocked
    if decision == "SHORT" and trend_1h > 0:
        decision = "WAIT"  # counter-trend short blocked
```

### 6.2 Position management

- Hold period = H=12 bars (1h) — matches training label
- If reverse signal fires while position open, close + open opposite
- If held for H=12 bars, force-close at next candle close
- One position per symbol at a time

### 6.3 Cost model

- Maker fees: 0.04% round-trip (Bybit maker 0.02% x 2 sides)
- Requires LIMIT ORDERS
- Taker fees (0.14%) would reduce profitability significantly

---

## 7. Operating procedures

### 7.1 Launch paper trading

```bash
cd ~/ppmt && source .venv/bin/activate

# Launch 3 symbols in background (balanced profile)
nohup python -m scripts.v12.paper_trader --symbol SOL > /tmp/v12_SOL.log 2>&1 &
nohup python -m scripts.v12.paper_trader --symbol DOGE > /tmp/v12_DOGE.log 2>&1 &
nohup python -m scripts.v12.paper_trader --symbol AVAX --profile conservative > /tmp/v12_AVAX.log 2>&1 &
```

### 7.2 Daily health check

```bash
# 1. Are processes alive?
ps -ef | grep v12.paper_trader | grep -v grep

# 2. Latest signals
tail -3 ~/ppmt/data/paper_trading/v12_logs/signals_v12_SOL.csv

# 3. Equity curve
tail -10 ~/ppmt/data/paper_trading/v12_logs/equity_v12_SOL.csv

# 4. Status report
python -m scripts.v12.paper_trader --status --symbol SOL
```

### 7.3 Weekly performance review

```bash
cd ~/ppmt && source .venv/bin/activate
python3 -c "
import pandas as pd, numpy as np

for sym in ['SOL', 'DOGE', 'AVAX']:
    try:
        eq = pd.read_csv(f'data/paper_trading/v12_logs/equity_v12_{sym}.csv')
        sig = pd.read_csv(f'data/paper_trading/v12_logs/signals_v12_{sym}.csv')
        closed = sig[sig['action'].str.startswith('CLOSE') | sig['action'].str.startswith('REVERSE')]
        print(f'\n=== V12 {sym} ===')
        print(f'Total trades:    {len(closed)}')
        if len(closed) > 0:
            print(f'Win rate:        {(closed[\"pnl_net_pct\"]>0).mean():.1%}')
            print(f'Total PnL:       {eq[\"equity_pct\"].iloc[-1]:.2f}%')
            print(f'Avg trade PnL:   {closed[\"pnl_net_pct\"].mean():.3f}%')
            returns = closed['pnl_net_pct'].values / 100
            if len(returns) > 1 and returns.std() > 0:
                sharpe = np.sqrt(252 * 12) * returns.mean() / returns.std()
                print(f'Sharpe (ann.):   {sharpe:.2f}')
        equity = eq['equity_pct'].values / 100
        peak = np.maximum.accumulate(equity)
        dd = equity - peak
        print(f'Max drawdown:    {dd.min()*100:.2f}%')
    except FileNotFoundError:
        print(f'\n=== V12 {sym} === (no data yet)')
"
```

### 7.4 Stop / restart

```bash
# Stop specific symbol
kill $(pgrep -f "v12.paper_trader.*SOL")

# Stop all V12 engines
kill $(pgrep -f "v12.paper_trader")

# Restart (engine persists state, picks up where it left off)
python -m scripts.v12.paper_trader --symbol SOL
```

---

## 8. Known limitations

1. **Paper trading only.** No real order execution. Simulated fills at candle close.
2. **No short-selling on spot.** SHORT trades simulated as -(1h return) - 0.04%.
   Real SHORT requires futures/perps.
3. **No position sizing.** Each trade simulated as 100% notional. Real deployment
   needs fixed-fractional sizing.
4. **1m data approximated for microstructure.** Paper trader fetches 5m bars
   directly and computes microstructure features from 5m bars (approximation).
   Training used 1m→5m aggregation. This may cause minor prediction drift.
5. **Quantile window warmup.** Engine needs ~20 predictions before quantile
   trading activates (~2h). Falls back to fixed thresholds during warmup.
6. **Bybit only.** Other exchanges not tested.
7. **BTC excluded.** Dead end — confirmed across all configs.
8. **V12 only validates SOL, DOGE, AVAX.** ETH and others not yet tested in V12.

---

## 9. Troubleshooting

| Symptom                                      | Fix                                                            |
|----------------------------------------------|----------------------------------------------------------------|
| `FileNotFoundError: no V11 model`            | Train first: `python scripts/v11/v11_train.py --horizon 12`   |
| `ModuleNotFoundError: ccxt`                  | `pip install ccxt`                                            |
| `DDoSProtection: binance 418`                | Use `--exchange bybit` (default)                              |
| All decisions are WAIT                       | Quantile window not warmed up. Wait ~2h for 20+ predictions.  |
| Feature computation returns None             | Need more warm-up bars. Try `--warmup-bars 500`               |
| Equity going negative quickly                | Stop engine, inspect last 20 signals                          |
| Wrong direction_mode being applied            | Check profile: --profile balanced vs conservative             |

---

## 10. Comparison: V7 vs V12 Paper Trading

| | V7 Paper Trading | V12 Paper Trading |
|---|---|---|
| Code location | `scripts/v7/paper_trader/` | `scripts/v12/paper_trader/` |
| Data feed | 5m native | 1m → 5m aggregated |
| Features | 58 | 80 (incl. microstructure) |
| Horizon | 24h (H=288) | 1h (H=12) |
| Trading freq | ~1 trade/2d per symbol | ~3-5 trades/d per symbol |
| Q thresholds | Q82-95 | Q95-98 |
| Direction | both | both / long_only per config |
| Trend filter | none | none / aligned per config |
| Logs dir | `data/paper_trading/logs/` | `data/paper_trading/v12_logs/` |
| State dir | `data/paper_trading/state/` | `data/paper_trading/v12_state/` |
| Runbook | `RUNBOOK_paper_trading.md` | `RUNBOOK_v12_paper_trading.md` |
