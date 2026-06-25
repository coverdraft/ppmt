# RUNBOOK — PPMT v7.5 Paper Trading Harness

**Status:** OPERATIONAL · **First deployed:** 2026-06-25
**Code:** `scripts/v7/paper_trader/` (Python package)
**Outputs:** `data/paper_trading/{models,logs,state}/`

---

## 1. Purpose

Validate the v6-LONG regression model (59 features, single LightGBM, no sign
filter — the architecture that scored Sharpe 1.22 / +124.57% PnL in walk-forward
backtest F7b) under live market conditions before risking real capital.

Ship criteria for going live with real money (per `PPMT_v7_MASTER_PLAN.md` §12.1):

| Metric        | Threshold   | Source           |
|---------------|-------------|------------------|
| Sharpe (2-4w) | > 1.0       | paper-trade log  |
| MaxDD         | > -15%      | paper-trade log  |
| Win rate      | > 52%       | paper-trade log  |
| N trades      | >= 50       | paper-trade log  |

If 2-4 weeks of paper trading pass all 4 criteria, escalate to real capital.
If any fails, investigate root cause before re-running.

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
│  - fetch_recent_window(): pulls 200 bars per cycle       │
└────────────────┬─────────────────────────────────────────┘
                 │  ohlcv_df, btc_df, eth_df
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Features (features.py)                                  │
│  - 38 v5 indicators (RSI, EMA, ATR, body/wick, ...)      │
│  - 21 v6 indicators (BTC ret, ETH corr, microstructure)  │
│  - = 59 features, all backward-looking, no leakage       │
└────────────────┬─────────────────────────────────────────┘
                 │  latest_feature_row()
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Model (model.py) — v6-LONG LightGBM regression          │
│  - Loaded from disk if exists, else bootstrap-trains     │
│  - 1 model per symbol (BTC/USDT, ETH/USDT, SOL/USDT...)  │
│  - Predicts fwd_ret_3 (15m forward return %)             │
│  - Decision: LONG if pred>0.20, SHORT if pred<-0.50      │
└────────────────┬─────────────────────────────────────────┘
                 │  (pred, decision)
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Engine (engine.py) — position management                │
│  - Open position when signal fires                       │
│  - Close after HORIZON=3 bars (15m) OR on reverse signal │
│  - Cost: 0.14% per round-trip                            │
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

## 3. Quick start

### 3.1 Train (or re-train) models for the symbols you want to paper-trade

```bash
cd /home/z/my-project
python3 -m scripts.v7.paper_trader.runner --train \
    --symbols "BTC/USDT,ETH/USDT,SOL/USDT" \
    --bootstrap-bars 4000
```

`--bootstrap-bars 4000` pulls ~14 days of 5m candles from Bybit and trains
one LightGBM model per symbol. Models are saved to
`data/paper_trading/models/v6_long_<SYM>.txt` with companion `_meta.json`.

### 3.2 Run paper trading in continuous mode

```bash
# Foreground (Ctrl+C to stop)
python3 -m scripts.v7.paper_trader.runner --symbol SOL/USDT

# Background via nohup
nohup python3 -m scripts.v7.paper_trader.runner --symbol SOL/USDT \
    > /tmp/pt_SOL.log 2>&1 &

# Or via screen / tmux for resilience
tmux new -s pt_SOL 'python3 -m scripts.v7.paper_trader.runner --symbol SOL/USDT'
```

Each cycle:
1. Waits for next 5m candle close on Bybit.
2. Fetches 200 most recent closed candles for SOL/USDT + BTC/USDT + ETH/USDT.
3. Computes 59 features on the latest closed candle.
4. Predicts fwd_ret_3 and applies decision rule.
5. Manages any open position (close on reverse signal or after 15m).
6. Appends a row to `signals_SOL_USDT.csv` and `equity_SOL_USDT.csv`.
7. Updates `state/engine_SOL_USDT.json`.

### 3.3 Single-cycle mode (for cron or smoke test)

```bash
python3 -m scripts.v7.paper_trader.runner --symbol SOL/USDT --once
```

This processes exactly one candle close and exits. Use this if you want to
drive the engine from cron / systemd timer instead of a long-running process.

Cron example (every 5 minutes, 30s after candle close):
```cron
*/5 * * * * sleep 30 && cd /home/z/my-project && \
    python3 -m scripts.v7.paper_trader.runner --symbol SOL/USDT --once \
    >> /tmp/pt_SOL.cron.log 2>&1
```

### 3.4 Check status

```bash
python3 -m scripts.v7.paper_trader.runner --status --symbol SOL/USDT
```

---

## 4. Logs schema

### 4.1 `signals_<SYM>.csv`

| Column              | Description                                                   |
|---------------------|---------------------------------------------------------------|
| ts_utc              | Candle close timestamp (ms since epoch)                       |
| ts_iso              | ISO-8601 UTC timestamp                                        |
| symbol              | Trading pair, e.g. `SOL/USDT`                                 |
| close               | Candle close price                                            |
| pred                | Model prediction (expected fwd_ret_3 in %)                    |
| decision            | `LONG`, `SHORT`, or `WAIT` (raw signal)                       |
| thr_long            | LONG threshold (0.20)                                         |
| thr_short           | SHORT threshold (0.50, asymmetric per v7.5 tuning)            |
| action              | `OPEN_LONG`, `OPEN_SHORT`, `CLOSE_LONG`, `CLOSE_SHORT`, `REVERSE_TO_*`, `HOLD`, `NO_ACTION` |
| position_side       | `LONG`, `SHORT`, or empty                                     |
| position_bars_held  | How many 5m bars the current position has been held           |
| entry_price         | Filled entry price (only set on CLOSE/REVERSE)                |
| exit_price          | Filled exit price (only set on CLOSE/REVERSE)                 |
| pnl_pct             | Gross PnL of the closed trade in %                            |
| cost_pct            | Round-trip cost in % (0.14)                                   |
| pnl_net_pct         | `pnl_pct - cost_pct`                                          |
| equity_pct          | Cumulative net PnL since engine start                         |

### 4.2 `equity_<SYM>.csv`

| Column     | Description                                  |
|------------|----------------------------------------------|
| ts_utc     | Candle close timestamp                       |
| ts_iso     | ISO timestamp                                |
| equity_pct | Cumulative net PnL                           |
| n_trades   | Total closed trades since start              |
| n_wins     | Trades with pnl_net_pct > 0                  |
| win_rate   | n_wins / n_trades                            |

### 4.3 `state/engine_<SYM>.json`

Persists engine state between runs. Contains:
- `last_closed_candle_ts`: ts of the most recent candle processed (avoids duplicate processing)
- `position`: `{side, entry_ts, entry_price, bars_held}` or null
- `equity_pct`, `n_trades`, `n_wins`: cumulative stats

---

## 5. Decision rule & thresholds

Per `PPMT_v7_MASTER_PLAN.md` §16 (v7.5 walk-forward backtest results):

```python
pred = model.predict(features)

if pred > 0.20:
    decision = "LONG"    # expected 15m return > +0.20%
elif pred < -0.50:
    decision = "SHORT"   # expected 15m return < -0.50%
else:
    decision = "WAIT"    # no edge
```

**Why asymmetric?** The v7.5 Optuna tuning found that LONG signals are reliable
at lower conviction (0.20%) but SHORT signals need much higher conviction (0.50%)
to be profitable — consistent with the crypto structural upward drift.

**Position management:**
- Hold period = HORIZON = 3 bars (15m) — matches training label.
- If a reverse signal fires while a position is open, close current and open opposite.
- If position has been held for HORIZON bars, force-close at next candle close.
- One position per symbol at a time (no pyramiding).

**Cost model:**
- Round-trip cost = 0.14% (Bybit taker fee 0.055% × 2 sides + 0.03% slippage buffer).
- Deducted from `pnl_net_pct` on every CLOSE.

---

## 6. Operating procedures

### 6.1 Daily health check

```bash
# 1. Is the process alive?
ps -ef | grep paper_trader | grep -v grep

# 2. Latest signal
tail -3 /home/z/my-project/data/paper_trading/logs/signals_SOL_USDT.csv

# 3. Equity curve
tail -10 /home/z/my-project/data/paper_trading/logs/equity_SOL_USDT.csv

# 4. Current position
python3 -c "import json; print(json.dumps(json.load(open('/home/z/my-project/data/paper_trading/state/engine_SOL_USDT.json')), indent=2))"
```

### 6.2 Weekly performance review

```python
import pandas as pd
df = pd.read_csv('/home/z/my-project/data/paper_trading/logs/equity_SOL_USDT.csv')
trades = pd.read_csv('/home/z/my-project/data/paper_trading/logs/signals_SOL_USDT.csv')
closed = trades[trades['action'].str.startswith('CLOSE')]
print(f"Total trades:    {len(closed)}")
print(f"Win rate:        {(closed['pnl_net_pct']>0).mean():.1%}")
print(f"Total PnL:       {df['equity_pct'].iloc[-1]:.2f}%")
print(f"Avg trade PnL:   {closed['pnl_net_pct'].mean():.3f}%")
print(f"Best trade:      {closed['pnl_net_pct'].max():.3f}%")
print(f"Worst trade:     {closed['pnl_net_pct'].min():.3f}%")

# Sharpe (per-trade, annualized to 5m bars)
import numpy as np
returns = closed['pnl_net_pct'].values / 100
sharpe = np.sqrt(252*288) * returns.mean() / returns.std() if returns.std() > 0 else 0
print(f"Sharpe (annualized): {sharpe:.2f}")

# Max drawdown
equity = df['equity_pct'].values / 100
peak = np.maximum.accumulate(equity)
dd = equity - peak
print(f"Max drawdown:    {dd.min()*100:.2f}%")
```

### 6.3 Retrain (Layer 2 rolling retrain — future F9 work)

```bash
# Retrain on the most recent 14 days of data
python3 -m scripts.v7.paper_trader.runner --train \
    --symbol SOL/USDT \
    --bootstrap-bars 4000
```

The engine will pick up the new model on next cycle (no restart needed if you
run in `--once` mode from cron; if running in foreground, restart the process).

### 6.4 Stop / restart cleanly

```bash
# Stop
kill $(pgrep -f "paper_trader.runner.*SOL/USDT")

# The engine persists state to JSON, so restart picks up where it left off
# (open positions are remembered, equity continues accumulating).
python3 -m scripts.v7.paper_trader.runner --symbol SOL/USDT
```

### 6.5 Emergency stop (kill switch)

If the model starts losing money rapidly:

```bash
# 1. Kill the engine (stops new signals)
kill $(pgrep -f paper_trader)

# 2. Manually close any open position in the state file
python3 -c "
import json
p = '/home/z/my-project/data/paper_trading/state/engine_SOL_USDT.json'
s = json.load(open(p))
s['position'] = None
json.dump(s, open(p, 'w'), indent=2)
print('Position cleared. Manual PnL reconciliation needed.')
"

# 3. Investigate root cause before restarting
#    - Check latest signals: tail signals_SOL_USDT.csv
#    - Compare prediction distribution to training: is there drift?
#    - Check Bybit status page for exchange-side issues
```

---

## 7. Known limitations (as of v7.5)

1. **Single-symbol engine.** Running multiple symbols requires launching
   separate processes. A future version could multiplex inside one process.

2. **Bootstrap training uses only 14 days.** v6 walk-forward trained on 60+
   days per window. The smaller training set means dir_acc on validation
   is currently 51-58% (vs 56.7% in v6 walk-forward). This is expected to
   improve as we accumulate more historical data via Layer 2 rolling retrain.

3. **No live order book / funding rate features.** The 6 F4 features
   (funding_rate, oi_change, sector, day_of_week) from v7 master plan §3
   are NOT yet implemented in the paper trader. Only the 59 v6 features
   are used. Adding F4 is a future enhancement (F8 / F10).

4. **Bybit only.** Binance IP-banned us in development. The code supports
   `--exchange okx|kraken|coinbase` but only Bybit has been smoke-tested.

5. **No short-selling cost modeling.** SHORT trades are simulated as
   `-fwd_ret_3 - 0.14%`, ignoring borrow fees. Real SHORT on Bybit spot
   isn't possible — would need futures mode (future work).

6. **No position sizing.** Each trade is simulated as 100% notional. Real
   deployment needs fixed-fractional sizing (e.g. 7% of equity per trade
   to match v6 production sizing).

---

## 8. Troubleshooting

| Symptom                                      | Fix                                                            |
|----------------------------------------------|----------------------------------------------------------------|
| `ModuleNotFoundError: ccxt`                  | `pip install ccxt` (or in the venv: `python3 -m pip install ccxt`) |
| `DDoSProtection: binance 418`                | Use `--exchange bybit` (default)                               |
| `OutOfBoundsDatetime: 58442-09-05`           | Already fixed in features.py (auto-detects ms vs s timestamps) |
| Model not loading after retrain              | Check `models/v6_long_<SYM>_meta.json` exists alongside `.txt` |
| Engine keeps timing out waiting for candle   | Check Bybit API status; bump `--warmup-bars` to 250            |
| Equity going negative quickly                | Stop engine, inspect last 20 signals, retrain if drift detected |
| Cron mode missing candles                    | Ensure cron runs `*/5 * * * *` with `sleep 30` offset          |

---

## 9. Glossary

| Term          | Definition                                                   |
|---------------|--------------------------------------------------------------|
| fwd_ret_3     | Forward 15-minute return: `(close[T+3] - close[T]) / close[T] * 100` on 5m TF |
| HORIZON       | Number of 5m bars to hold a position (= 3, i.e. 15 minutes)  |
| thr_long      | LONG threshold: pred must exceed 0.20 to fire LONG signal    |
| thr_short     | SHORT threshold: pred must be below -0.50 to fire SHORT      |
| cost_pct      | Round-trip trading cost = 0.14% (fees + slippage)            |
| warmup_bars   | Number of historical bars fetched each cycle for feature computation (default 200) |
| bootstrap_bars| Number of historical bars used for one-shot training (default 4000) |

---

## 10. Next steps after paper trading graduates

Once 2-4 weeks of paper trading pass all 4 ship criteria:

1. **F9 — Layer 2 rolling retrain.** Replace one-shot bootstrap with rolling
   30-day window retrained every 6h. Code path:
   `scripts/v7/v7_layer2_rolling_retrain.py` (TODO).

2. **F10 — Live order execution.** Replace the simulated fills in
   `engine.py:_act_on_decision` with real ccxt order placement. Start with
   Bybit testnet, then small real size (e.g. $10/trade).

3. **F11 — Multi-symbol portfolio.** Aggregate signals across BTC/ETH/SOL/...
   with position sizing and correlation limits.

4. **F12 — Monitoring dashboard.** Stream `signals_*.csv` and `equity_*.csv`
   into Grafana / Streamlit for real-time visibility.
