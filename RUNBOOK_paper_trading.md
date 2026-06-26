# RUNBOOK — PPMT v7 Paper Trading Harness

**Status:** OPERATIONAL · **Last updated:** 2026-06-26 (v7 binary classification)
**Code:** `scripts/v7/paper_trader/` (Python package)
**Outputs:** `data/paper_trading/{models,logs,state}/`

---

## 1. Purpose

Validate the v7 binary classification model (58 features, LightGBM, quantile-based
trading) under live market conditions before risking real capital.

The model predicts P(UP in 24h) and uses rolling quantile thresholds to generate
LONG/SHORT signals. This architecture was validated through:
- 7-token comprehensive sweep (90d, 504 configs)
- Deep optimization (180d, 5040 configs across 4 tokens)
- 3/4 core tokens have 4/4 window consistency

Ship criteria for going live with real capital:

| Metric        | Threshold   | Source           |
|---------------|-------------|------------------|
| Sharpe (2-4w) | > 0.3       | paper-trade log  |
| MaxDD         | > -15%      | paper-trade log  |
| Win rate      | > 55%       | paper-trade log  |
| N trades      | >= 20       | paper-trade log  |

Note: thresholds are more conservative than backtest because OOS performance
is typically lower. We expect ~10-12 trades per token per 45-day window.

---

## 2. Architecture

```
                ┌─────────────────────────────────────────┐
                │           BYBIT PUBLIC API              │
                │   (no key needed for OHLCV spot)        │
                └────────────────┬────────────────────────┘
                                 │  fetch_ohlcv 5m
                                 ▼
┌──────────────────────────────────────────────────────────┐
│  Feed (scripts/v7/paper_trader/feed.py)                  │
│  - wait_for_next_close(): polls every 30s                │
│  - fetch_recent_window(): pulls 400 bars per cycle       │
└────────────────┬─────────────────────────────────────────┘
                 │  ohlcv_df, btc_df, eth_df
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Features (features.py)                                  │
│  - 37 v5 indicators (RSI, EMA, ATR, body/wick, ...)      │
│  - 21 v6 indicators (BTC ret, ETH corr, microstructure)  │
│  - = 58 features, all backward-looking, no leakage       │
└────────────────┬─────────────────────────────────────────┘
                 │  latest_feature_row()
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Model (model.py) — v7 LightGBM binary classification    │
│  - P(UP in 24h) = model.predict(features)                │
│  - 1 model per symbol with per-symbol HP config           │
│  - Per-symbol config: Q, window, cost, HP from deep opt  │
│  - Quantile-based decision: LONG if pred > Q_LONG%       │
│    SHORT if pred < Q_SHORT%, WAIT otherwise               │
└────────────────┬─────────────────────────────────────────┘
                 │  (pred, decision)
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Engine (engine.py) — position management                │
│  - Rolling quantile window for signal generation          │
│  - Open position when quantile signal fires               │
│  - Close after HORIZON=288 bars (24h) OR on reverse      │
│  - Per-symbol cost: maker 0.04% (limit orders)           │
│  - Persists state to JSON, logs every signal to CSV      │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Logs (data/paper_trading/logs/)                         │
│  - signals_<SYM>.csv: 1 row per 5m candle                │
│  - equity_<SYM>.csv: 1 row per 5m candle (cumulative)    │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Per-Symbol Config (from Deep Optimization 180d)

| Token | Q Long | Q Short | Window | Cost | HP | PnL 180d | Consistency |
|-------|--------|---------|--------|------|-----|----------|-------------|
| DOGE/USDT | 95 | 5 | 400 | 0.04% | default | +41.55% | **4/4** |
| AVAX/USDT | 82 | 18 | 200 | 0.04% | more_reg | +44.76% | **4/4** |
| SOL/USDT | 85 | 15 | 200 | 0.04% | very_reg | +41.46% | **4/4** |
| ETH/USDT | 87 | 13 | 400 | 0.04% | default | +36.56% | 3/4 |
| LINK/USDT | 90 | 10 | 200 | 0.14% | default | +13.0%* | 3/4* |
| XRP/USDT | 80 | 20 | 200 | 0.14% | default | +21.7%* | 3/4* |

*90d sweep results (pending deep optimization)

**IMPORTANT**: All 4 core tokens use maker fees (0.04%). This requires placing
LIMIT ORDERS, not market orders. Taker fees (0.14%) reduce profitability by ~5pp.

---

## 4. Quick start

### 4.1 Train models for the tokens you want to paper-trade

```bash
cd ~/ppmt && source .venv/bin/activate

# Train all core tokens (DOGE, AVAX, SOL, ETH)
python -m scripts.v7.paper_trader.runner --train --all

# Or train specific tokens
python -m scripts.v7.paper_trader.runner --train --symbol DOGE/USDT
python -m scripts.v7.paper_trader.runner --train --symbols "DOGE/USDT,AVAX/USDT"
```

Training uses ~180d of data (52000 bars) with per-symbol HP config.
Each token takes ~2-3 minutes. Models are saved to
`data/paper_trading/models/v7_clf_<SYM>.txt` with companion `_meta.json`.

### 4.2 Run paper trading

```bash
# Single token (foreground)
python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT

# All core tokens (multiprocessing)
python -m scripts.v7.paper_trader.runner --all

# Single token (background via nohup — survives terminal close)
nohup python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT \
    > /tmp/pt_DOGE.log 2>&1 &

# Or via screen (if available)
screen -dmS pt_DOGE python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT
```

Each cycle:
1. Waits for next 5m candle close on Bybit.
2. Fetches 400 most recent closed candles for symbol + BTC/USDT + ETH/USDT.
3. Computes 58 features on the latest closed candle.
4. Predicts P(UP in 24h) and applies quantile decision rule.
5. Manages any open position (close on reverse signal or after 24h).
6. Appends a row to signal/equity CSVs.
7. Updates state JSON (including rolling prediction window).

### 4.3 Single-cycle mode (for cron or smoke test)

```bash
python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT --once
```

Cron example (every 5 minutes, 30s after candle close):
```cron
*/5 * * * * sleep 30 && cd ~/ppmt && source .venv/bin/activate && \
    python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT --once \
    >> /tmp/pt_DOGE.cron.log 2>&1
```

### 4.4 Check status

```bash
python -m scripts.v7.paper_trader.runner --status --symbol DOGE/USDT
python -m scripts.v7.paper_trader.runner --status --all
```

---

## 5. Logs schema

### 5.1 `signals_<SYM>.csv`

| Column              | Description                                                   |
|---------------------|---------------------------------------------------------------|
| ts_utc              | Candle close timestamp (ms since epoch)                       |
| ts_iso              | ISO-8601 UTC timestamp                                        |
| symbol              | Trading pair, e.g. `DOGE/USDT`                                |
| close               | Candle close price                                            |
| pred                | Model prediction P(UP in 24h) [0-1]                          |
| decision            | `LONG`, `SHORT`, or `WAIT` (quantile-based)                   |
| q_high              | Rolling Q_LONG percentile threshold                           |
| q_low               | Rolling Q_SHORT percentile threshold                          |
| action              | `OPEN_LONG`, `OPEN_SHORT`, `CLOSE_LONG`, `CLOSE_SHORT`, `REVERSE_TO_*`, `HOLD`, `NO_ACTION` |
| position_side       | `LONG`, `SHORT`, or empty                                     |
| position_bars_held  | How many 5m bars the current position has been held           |
| entry_price         | Filled entry price (only set on CLOSE/REVERSE)                |
| exit_price          | Filled exit price (only set on CLOSE/REVERSE)                 |
| pnl_pct             | Gross PnL of the closed trade in %                            |
| cost_pct            | Round-trip cost in % (0.04% maker / 0.14% taker)             |
| pnl_net_pct         | `pnl_pct - cost_pct`                                          |
| equity_pct          | Cumulative net PnL since engine start                         |

### 5.2 `equity_<SYM>.csv`

| Column     | Description                                  |
|------------|----------------------------------------------|
| ts_utc     | Candle close timestamp                       |
| ts_iso     | ISO timestamp                                |
| equity_pct | Cumulative net PnL                           |
| n_trades   | Total closed trades since start              |
| n_wins     | Trades with pnl_net_pct > 0                  |
| win_rate   | n_wins / n_trades                            |

### 5.3 `state/engine_<SYM>.json`

Persists engine state between runs. Contains:
- `last_closed_candle_ts`: ts of the most recent candle processed
- `position`: `{side, entry_ts, entry_price, bars_held}` or null
- `recent_preds`: list of recent predictions for rolling quantile computation
- `equity_pct`, `n_trades`, `n_wins`: cumulative stats

---

## 6. Decision logic

### 6.1 Quantile-based trading (matches backtest)

```python
# Each 5m candle:
pred = model.predict(features)  # P(UP in 24h)

# Update rolling window
recent_preds.append(pred)
if len(recent_preds) > window_size:
    recent_preds = recent_preds[-window_size:]

# Compute thresholds
q_high = np.percentile(recent_preds, Q_LONG)   # e.g. 95th percentile
q_low = np.percentile(recent_preds, Q_SHORT)    # e.g. 5th percentile

# Decision
if pred > q_high:
    decision = "LONG"
elif pred < q_low:
    decision = "SHORT"
else:
    decision = "WAIT"
```

### 6.2 Position management

- Hold period = HORIZON = 288 bars (24h) — matches training label.
- If a reverse signal fires while a position is open, close current and open opposite.
- If position has been held for HORIZON bars, force-close at next candle close.
- One position per symbol at a time (no pyramiding).

### 6.3 Cost model

- Maker fees: 0.04% round-trip (Bybit maker 0.02% × 2 sides).
  **Requires limit orders.** This is what the deep optimization assumes.
- Taker fees: 0.14% round-trip (0.055% × 2 + 0.03% slippage).
  This would reduce PnL by ~5pp. Avoid market orders if possible.

---

## 7. Operating procedures

### 7.1 Launch paper trading (recommended: 3 tokens with 4/4 consistency)

```bash
cd ~/ppmt && source .venv/bin/activate

# Train all core tokens first
python -m scripts.v7.paper_trader.runner --train --all

# Launch 3 most robust tokens in background (nohup)
nohup python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT > /tmp/pt_DOGE.log 2>&1 &
nohup python -m scripts.v7.paper_trader.runner --symbol AVAX/USDT > /tmp/pt_AVAX.log 2>&1 &
nohup python -m scripts.v7.paper_trader.runner --symbol SOL/USDT  > /tmp/pt_SOL.log 2>&1 &

# Or use --all for all 4 core tokens
nohup python -m scripts.v7.paper_trader.runner --all > /tmp/pt_all.log 2>&1 &
```

### 7.2 Daily health check

```bash
# 1. Are processes alive?
ps -ef | grep paper_trader | grep -v grep

# 2. Latest signals
tail -3 ~/ppmt/data/paper_trading/logs/signals_DOGE_USDT.csv

# 3. Equity curve
tail -10 ~/ppmt/data/paper_trading/logs/equity_DOGE_USDT.csv

# 4. Status report
python -m scripts.v7.paper_trader.runner --status --all
```

### 7.3 Weekly performance review

```bash
cd ~/ppmt && source .venv/bin/activate
python3 -c "
import pandas as pd, numpy as np, glob

for sym in ['DOGE_USDT', 'AVAX_USDT', 'SOL_USDT', 'ETH_USDT']:
    try:
        eq = pd.read_csv(f'data/paper_trading/logs/equity_{sym}.csv')
        sig = pd.read_csv(f'data/paper_trading/logs/signals_{sym}.csv')
        closed = sig[sig['action'].str.startswith('CLOSE') | sig['action'].str.startswith('REVERSE')]
        print(f'\n=== {sym} ===')
        print(f'Total trades:    {len(closed)}')
        if len(closed) > 0:
            print(f'Win rate:        {(closed[\"pnl_net_pct\"]>0).mean():.1%}')
            print(f'Total PnL:       {eq[\"equity_pct\"].iloc[-1]:.2f}%')
            print(f'Avg trade PnL:   {closed[\"pnl_net_pct\"].mean():.3f}%')
            returns = closed['pnl_net_pct'].values / 100
            if len(returns) > 1 and returns.std() > 0:
                sharpe = np.sqrt(252*288) * returns.mean() / returns.std()
                print(f'Sharpe (ann.):   {sharpe:.2f}')
        equity = eq['equity_pct'].values / 100
        peak = np.maximum.accumulate(equity)
        dd = equity - peak
        print(f'Max drawdown:    {dd.min()*100:.2f}%')
    except FileNotFoundError:
        print(f'\n=== {sym} === (no data yet)')
"
```

### 7.4 Retrain (Layer 2 rolling retrain)

```bash
cd ~/ppmt && source .venv/bin/activate

# Retrain on 90d of data with per-symbol HP
python scripts/v7/v7_layer2_rolling_retrain.py --symbol DOGE/USDT --days 90

# Retrain all core tokens
python scripts/v7/v7_layer2_rolling_retrain.py --symbols "DOGE/USDT,AVAX/USDT,SOL/USDT,ETH/USDT" --days 90
```

The engine will pick up the new model on the next cycle (no restart needed
if using `--once` cron mode; for foreground, restart the process).

### 7.5 Stop / restart cleanly

```bash
# Stop specific token
kill $(pgrep -f "paper_trader.runner.*DOGE")

# Stop all
kill $(pgrep -f paper_trader)

# Engine persists state, so restart picks up where it left off
python -m scripts.v7.paper_trader.runner --symbol DOGE/USDT
```

### 7.6 Emergency stop (kill switch)

If the model starts losing money rapidly:

```bash
# 1. Kill the engine
kill $(pgrep -f paper_trader)

# 2. Clear position state
python3 -c "
import json
for sym in ['DOGE_USDT', 'AVAX_USDT', 'SOL_USDT', 'ETH_USDT']:
    p = f'data/paper_trading/state/engine_{sym}.json'
    try:
        s = json.load(open(p))
        s['position'] = None
        json.dump(s, open(p, 'w'), indent=2)
        print(f'{sym}: position cleared')
    except: pass
"

# 3. Investigate before restarting
```

---

## 8. Known limitations

1. **Paper trading only.** No real order execution. Simulated fills at candle close.

2. **No short-selling on spot.** SHORT trades are simulated as
   `-(24h return) - 0.04%`. Real SHORT would require futures/perps.

3. **No position sizing.** Each trade is simulated as 100% notional. Real
   deployment needs fixed-fractional sizing (e.g. 10% of equity per trade).

4. **Quantile window warmup.** The engine needs ~20 predictions before quantile
   trading activates. During warmup, it falls back to fixed thresholds
   (PROB_LONG=0.55, PROB_SHORT=0.42), which are less optimal.

5. **BTC excluded.** BTC/USDT is a dead end — confirmed across all configs.

6. **Bybit only.** Code supports other exchanges but only Bybit is tested.

7. **No funding rate / OI features.** The 58 features are all price/volume-based.
   Adding funding rate and open interest could improve edge.

---

## 9. Troubleshooting

| Symptom                                      | Fix                                                            |
|----------------------------------------------|----------------------------------------------------------------|
| `ModuleNotFoundError: ccxt`                  | `pip install ccxt` (or in venv: `python3 -m pip install ccxt`) |
| `DDoSProtection: binance 418`                | Use `--exchange bybit` (default)                               |
| Model not loading after retrain              | Check `models/v7_clf_<SYM>_meta.json` exists alongside `.txt` |
| Engine keeps timing out waiting for candle   | Check Bybit API status; bump `--warmup-bars` to 500            |
| Equity going negative quickly                | Stop engine, inspect last 20 signals, retrain if drift detected |
| All decisions are WAIT                       | Quantile window not warmed up yet. Wait ~2h for 20+ preds.     |
| Too many trades (churning)                   | Q config may be wrong. Check SYMBOL_CONFIG for the symbol.     |

---

## 10. Next steps after paper trading graduates

1. **Futures mode.** Implement real SHORT via Bybit USDT perpetual contracts.

2. **Position sizing.** Fixed-fractional (e.g. 10% equity per trade) with
   correlation limits across tokens.

3. **Live order execution.** Replace simulated fills with real ccxt order
   placement. Start with Bybit testnet, then small real size.

4. **Funding rate / OI features.** Add these to potentially improve edge.

5. **Monitoring dashboard.** Stream `signals_*.csv` and `equity_*.csv`
   into Grafana / Streamlit for real-time visibility.
