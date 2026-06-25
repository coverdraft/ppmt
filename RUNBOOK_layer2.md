# RUNBOOK — F9 Layer 2 Rolling Retrain

**Status:** OPERATIONAL · **First deployed:** 2026-06-25
**Code:** `scripts/v7/v7_layer2_rolling_retrain.py` (standalone script)
**Outputs:** `data/paper_trading/{models,logs}/`

---

## 1. Purpose

Re-train the v6-LONG LightGBM model on a rolling 30-day window every 6 hours,
so the paper trader picks up new market regime information without manual
intervention. The acceptance gate prevents deploying models that regress
significantly vs the previous deploy.

Per `PPMT_v7_MASTER_PLAN.md` §6.2:

| Aspect        | Spec                                                    |
|---------------|---------------------------------------------------------|
| Cadence       | Every 6h (00:30, 06:30, 12:30, 18:30 UTC via cron)     |
| Window        | 30 days of 5m candles (8640 bars per symbol)           |
| Split         | Train 83% / Val 10% / Test 7% (proportional to window) |
| Algorithm     | v6-LONG LightGBM regression, single regression on ALL labels |
| Features      | 59 v6 features (no F4 extras yet — future F10 work)    |
| Accept gate   | New model deployed iff val_dir_acc >= old - 2pp         |
| Reject gate   | Reject iff new val_dir_acc < old - 5pp                  |
| Swap          | Atomic (.tmp + fsync + rename) — no half-written models |

---

## 2. Quick start

### 2.1 Single-symbol retrain (manual)

```bash
cd /home/z/my-project
python3 scripts/v7/v7_layer2_rolling_retrain.py --symbol SOL/USDT --days 30
```

Exit codes:
- `0` = accepted (new model deployed) or first deploy
- `1` = rejected (kept old model — significant regression)
- `2` = error (data fetch / training failure)

### 2.2 Multi-symbol batch

```bash
python3 scripts/v7/v7_layer2_rolling_retrain.py \
    --symbols "BTC/USDT,ETH/USDT,SOL/USDT" --days 30
```

### 2.3 Dry-run (train + evaluate, no deploy)

```bash
python3 scripts/v7/v7_layer2_rolling_retrain.py --symbol SOL/USDT --days 30 --dry-run
```

Useful for debugging or pre-deploy validation.

### 2.4 Smoke test (smaller window, faster iteration)

```bash
python3 scripts/v7/v7_layer2_rolling_retrain.py --symbol SOL/USDT --days 7 --dry-run
```

---

## 3. Cron setup (production)

Add to crontab (`crontab -e`):

```cron
# F9 Layer 2 rolling retrain — every 6h at :30 (UTC)
30 */6 * * * cd /home/z/my-project && \
    /home/z/.venv/bin/python3 scripts/v7/v7_layer2_rolling_retrain.py \
        --symbols "BTC/USDT,ETH/USDT,SOL/USDT" --days 30 \
        >> /tmp/pt_layer2.cron.log 2>&1
```

**Why `:30` past the hour?** Bybit's 5m candle closes at :00/:05/:10/.../:55.
Scheduling at :30 gives the exchange 30 minutes to publish the candle that
closed at :25, ensuring we always include the most recent data.

**Time zone:** Cron runs in the system timezone. To force UTC:
```bash
CRON_TZ=UTC
30 */6 * * * ...
```

---

## 4. Acceptance gate logic

```python
delta = new_val_dir_acc - old_val_dir_acc

if no prior model:
    decision = "FIRST_DEPLOY"     # always accept
elif delta >= -0.02:               # within 2pp tolerance
    decision = "ACCEPT"            # deploy new
elif delta < -0.05:                # beyond 5pp regression
    decision = "REJECT"            # keep old
else:                              # between -2pp and -5pp
    decision = "ACCEPT_WITH_WARNING"  # deploy but flag for review
```

**Rationale:**
- ±2pp = within noise band (LightGBM stochastic + small val set)
- -5pp = significant degradation (regime shift or training failure)
- Between = grey zone; deploy but log warning for human review

**Consecutive rejection alert (TODO — F9.1):**
If a symbol gets 3 REJECT in a row, send alert. This indicates either:
- Persistent regime shift (need to expand training window)
- Feature distribution drift (need Layer 3 early)
- Bug in data pipeline (need investigation)

---

## 5. Logs schema

### 5.1 `data/paper_trading/logs/retrain_<SYM>.csv`

One row per retrain cycle.

| Column              | Description                                              |
|---------------------|----------------------------------------------------------|
| ts_utc              | Retrain completion timestamp (s)                         |
| ts_iso              | ISO-8601 UTC                                             |
| symbol              | Trading pair                                             |
| window_days         | Training window in days (default 30)                     |
| n_train             | Rows in train split                                      |
| n_val               | Rows in val split                                        |
| n_test              | Rows in test split                                       |
| new_val_dir_acc     | New model's direction accuracy on val (0-1)              |
| new_val_rmse        | New model's RMSE on val                                  |
| new_val_corr        | New model's pred-vs-actual correlation on val            |
| old_val_dir_acc     | Previous model's val_dir_acc (0 if first deploy)         |
| old_val_rmse        | Previous model's val_rmse                                |
| decision            | `FIRST_DEPLOY` / `ACCEPT` / `ACCEPT_WITH_WARNING` / `REJECT` / `ERROR` |
| delta_dir_acc       | `new - old` (positive = improvement)                     |
| model_path          | Path to deployed model (empty if REJECT)                 |
| trained_at          | Deploy timestamp (empty if REJECT)                       |

### 5.2 `data/paper_trading/models/v6_long_<SYM>_meta.json`

Updated on every successful deploy. Includes:
- `trained_at`, `training_window_days`
- `n_train`, `n_val`, `n_test`
- `rmse_val`, `corr_val`, `dir_acc_val`
- `rmse_test`, `corr_test`, `dir_acc_test`
- `n_trades_test`, `pnl_long_test`, `pnl_short_test`, `pnl_total_test`
- `acceptance`: full decision record (decision, delta, old/new dir_acc, thresholds)
- `training_rows_time_range`: timestamps of first/last train + test rows
- `feature_names`: 59 feature names (canonical order)

---

## 6. Operating procedures

### 6.1 Daily check

```bash
# Latest retrain decisions for all symbols
for sym in BTC ETH SOL; do
    echo "=== $sym ==="
    tail -3 /home/z/my-project/data/paper_trading/logs/retrain_${sym}_USDT.csv
done

# Check for any REJECT in last 24h
for sym in BTC ETH SOL; do
    tail -10 /home/z/my-project/data/paper_trading/logs/retrain_${sym}_USDT.csv | \
        awk -F, 'NR>1 && $13=="REJECT" {print "REJECT:", $1, $3, "delta="$14}'
done
```

### 6.2 Weekly review

```python
import pandas as pd
import json
from pathlib import Path

for sym in ['BTC', 'ETH', 'SOL']:
    f = Path(f'/home/z/my-project/data/paper_trading/logs/retrain_{sym}_USDT.csv')
    if not f.exists():
        continue
    df = pd.read_csv(f)
    print(f"\n=== {sym}/USDT ===")
    print(f"Total retrains:    {len(df)}")
    print(f"Accepts:           {(df['decision']=='ACCEPT').sum()}")
    print(f"Accepts w/ warn:   {(df['decision']=='ACCEPT_WITH_WARNING').sum()}")
    print(f"Rejects:           {(df['decision']=='REJECT').sum()}")
    print(f"First deploy:      {(df['decision']=='FIRST_DEPLOY').sum()}")
    print(f"Dir acc trend:     {df['new_val_dir_acc'].iloc[0]:.3f} → {df['new_val_dir_acc'].iloc[-1]:.3f}")
    # Latest deployed model
    meta_path = Path(f'/home/z/my-project/data/paper_trading/models/v6_long_{sym}_USDT_meta.json')
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        print(f"Current model:     dir_acc_val={meta['dir_acc_val']:.3f} dir_acc_test={meta.get('dir_acc_test', 0):.3f}")
        print(f"  test_pnl:        {meta.get('pnl_total_test', 0):.3f}% ({meta.get('n_trades_test', 0)} trades)")
```

### 6.3 Force retrain (manual)

If you suspect the model is stale (e.g., major market event):

```bash
python3 scripts/v7/v7_layer2_rolling_retrain.py --symbols "BTC/USDT,ETH/USDT,SOL/USDT"
```

### 6.4 Rollback to previous model

The acceptance gate prevents bad models from deploying, but if you need to
manually roll back:

```bash
# 1. Find a known-good model in git history (if you committed it)
cd /home/z/my-project
git log --oneline -- 'data/paper_trading/models/v6_long_SOL_USDT.txt' 2>/dev/null

# 2. Or — re-run retrain with a wider window (more data often helps)
python3 scripts/v7/v7_layer2_rolling_retrain.py --symbol SOL/USDT --days 45

# 3. If still bad — restore from initial bootstrap train
python3 -m scripts.v7.paper_trader.runner --train --symbol SOL/USDT --bootstrap-bars 4000
```

### 6.5 Stop the rolling retrain

```bash
# Remove cron entry
crontab -l | grep -v v7_layer2_rolling_retrain | crontab -

# Or just disable temporarily by commenting out the line in crontab -e
```

---

## 7. Known limitations

1. **Bybit-only.** Same as the paper trader. Can be extended via `--exchange`.

2. **30-day window may be too short for rare regimes.** If the market enters
   a regime not seen in the last 30 days (e.g., flash crash, halving event),
   the model will have no training data for it. Mitigation: monitor
   `dir_acc_test` — if it drops below 50%, consider expanding the window.

3. **No drift-based early trigger.** Master plan §12.2 specifies drift
   escalation: if `|pred_avg_24h - outcome_avg_24h| > 0.5%`, force retrain
   early. Not yet implemented — scheduled for F9.1.

4. **Single model per symbol.** No ensemble, no model averaging. The
   acceptance gate's "REJECT" path keeps the OLD model until the next cycle,
   so consecutive rejections mean the deployed model gets staler. Consecutive
   reject alerting (3 in a row → notify) is on the F9.1 roadmap.

5. **No F4 features yet.** Only the 59 v6 features. Funding rate, OI change,
   sector one-hot are NOT included — would need a separate data feed (Bybit
   derivatives API). Scheduled for F10.

---

## 8. Integration with paper trader

The paper trader (`scripts/v7/paper_trader/`) loads the model file from
`data/paper_trading/models/v6_long_<SYM>.txt` on EVERY cycle. This means:

- **`--once` mode (cron-driven):** New model picked up automatically on next cycle.
- **Foreground `run_forever` mode:** The LightGBM Booster is loaded once at
  startup. To pick up a new model, restart the process. A future enhancement
  (F9.2) could check the model file's mtime each cycle and reload if newer.

**Recommended deployment pattern:**
```bash
# Cron: paper trader every 5m, layer2 retrain every 6h
*/5 * * * * sleep 30 && cd /home/z/my-project && \
    python3 -m scripts.v7.paper_trader.runner --symbol SOL/USDT --once \
    >> /tmp/pt_SOL.cron.log 2>&1

30 */6 * * * cd /home/z/my-project && \
    python3 scripts/v7/v7_layer2_rolling_retrain.py \
        --symbols "BTC/USDT,ETH/USDT,SOL/USDT" --days 30 \
    >> /tmp/pt_layer2.cron.log 2>&1
```

---

## 9. Smoke test results (2026-06-25 17:01 UTC)

Initial validation with 7-day window:

| Symbol    | Decision             | New dir_acc | Old dir_acc | Delta   |
|-----------|----------------------|-------------|-------------|---------|
| BTC/USDT  | ACCEPT               | 0.591       | 0.549       | +0.042  |
| ETH/USDT  | REJECT               | 0.465       | 0.518       | -0.053  |
| SOL/USDT  | ACCEPT_WITH_WARNING  | 0.551       | 0.580       | -0.030  |

All three branches of the acceptance gate exercised correctly:
- BTC: significant improvement → deployed
- ETH: significant regression (-5.3pp beyond REJECT_THRESHOLD) → kept old model
- SOL: small regression (within 2pp tolerance, but rounded to -3pp on first run,
  then ACCEPT on second because comparison was vs the just-deployed model)

End-to-end pipeline verified: Bybit fetch → 59 features → walk-forward split →
LightGBM train → val evaluate → test evaluate → acceptance gate → atomic deploy
→ CSV log.

---

## 10. Next steps (F9.x roadmap)

| Sub-phase | Description                                              | Effort |
|-----------|----------------------------------------------------------|--------|
| F9.1      | Drift-based early trigger (master plan §12.2)           | 2-3h   |
| F9.2      | Hot-reload in paper trader (mtime check each cycle)     | 1h     |
| F9.3      | Consecutive reject alerting (3 in a row → email/log)    | 1h     |
| F9.4      | Add F4 features (funding_rate, oi_change, sector)       | 4-6h   |
| F9.5      | Multi-symbol portfolio retrain (correlation-aware)      | 1-2d   |

After F9 is stable for 1-2 weeks of paper trading, move to F10 (adaptive
SL/TP manager per master plan §7).
