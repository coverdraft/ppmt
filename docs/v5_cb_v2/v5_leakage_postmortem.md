# v5_cb_v2 Leakage Postmortem

**Date discovered:** 2026-06-24
**Severity:** Critical — model discarded entirely
**Impact:** All v5_cb_v2 metrics (AUC 0.94, WR 80%, PF 3.0) were illusory. Paper trader deployed for ~2 hours produced 0 trades (correctly, because live inference couldn't reproduce the leaked feature).

---

## Summary

The v5_cb_v2 LightGBM model was trained on features that contained the label itself, disguised under a colliding column name. The model learned to read the label as a feature, achieving AUC 0.94. In production (live paper trading), the colliding column couldn't be reproduced (it would require seeing the future), so all predictions collapsed to 0.10–0.19 and no trades opened.

**Real AUC of the v5_cb_v2 approach (without leakage): 0.54 — essentially random.**

---

## Root cause

In `scripts/v5/v5_extract_features_cb.py`:

```python
def compute_indicators(df):
    ...
    df["ret_3"] = df["close"].pct_change(3)   # backward-looking, fraction (±0.005)
    df["ret_5"] = df["close"].pct_change(5)
    df["ret_10"] = df["close"].pct_change(10)
    ...

def make_labels(df, horizons=(3, 6, 12)):
    ...
    for H in horizons:
        ret = ...  # forward-looking % return at H bars
        df[f"ret_{H}"] = ret   # ← COLLISION: overwrites df["ret_3"] for H=3
        ...
```

The label function `make_labels` creates columns `ret_3`, `ret_6`, `ret_12` for forward-looking returns. The H=3 case **silently overwrites** the `ret_3` feature column with the **3-bar forward % return** — which is essentially the label itself (range ±27%, vs the original backward-looking ±0.5%).

The model's root split was `ret_3 > 0.1027` (10.27% forward return) — i.e., "if the next 3 bars go up >10%, predict TP=1". Trivially correct, totally unusable in production.

---

## Discovery timeline

1. **Live paper trader deployed** (Step 8). After 30 min, 0 trades opened despite 34 signals seen.
2. **Diagnostic:** all live probabilities were 0.10–0.19, threshold was 0.80 → 0 approved.
3. **Hypothesis:** feature pipeline in paper trader doesn't match training.
4. **Investigation:** Compared `compute_features()` in paper trader vs `compute_indicators()` in original extractor — identical, so the bug wasn't in the math.
5. **Direct comparison:** Pulled a historical feature row from `feature_observations_cb` (what the model trained on) and compared feature-by-feature to a freshly-recomputed row from `compute_features()` on the same candle.
6. **Found:** 33/38 features diverged wildly. On closer inspection, the divergence was because my diagnostic fetched the WRONG 60-candle window (off-by-window bug in my diagnostic, not in production code).
7. **Re-ran diagnostic correctly:** 31/38 features EXACT match. The 7 diverging features told the story:
   - `ret_3`: stored=`-0.41` (range ±27%), recomputed=`-0.0013` (range ±0.005) — **the smoking gun**
   - 6 EMA-50 related features slightly off — buffer too short for EMA convergence
8. **Verified hypothesis:** Wrote `sanity_check_ret3_leakage.py` that:
   - Loaded stored features
   - Patched JUST `ret_3` to use backward-looking fraction
   - Retrained with identical hyperparameters
9. **Result:** AUC dropped from 0.94 → 0.54. Confirmed.

---

## The smoking gun

Top feature importance comparison:

**With leakage (baseline):**
| Feature | Gain |
|---------|------|
| **ret_3** | **829,801** |
| atr_pct | 60,133 |
| last_3_range_sum | 34,959 |
| range_pct | 6,539 |
| vol_regime | 3,894 |

`ret_3` dominated with 13× more gain than the second feature. It was literally the label.

**Without leakage (patched):**
| Feature | Gain |
|---------|------|
| atr_pct | 5,772 |
| last_3_range_sum | 2,389 |
| ema_20_50_cross | 1,747 |
| hour_cos | 1,735 |
| range_pct | 1,433 |

No feature dominates. Gains are small — the real signal in 5m crypto with classical indicators is **marginal**.

---

## Why walk-forward didn't catch it

The walk-forward validation (`v5_walkforward_cb_v2.py`) showed AUC 0.9341 ± 0.0075 across 6 windows. We interpreted this as "model is stable". In reality:

- The leakage was present in every window equally
- Walk-forward tests temporal generalization, not feature-label independence
- High AUC + low variance across windows **should have been a red flag** (real signal in 5m crypto is rarely this stable)

---

## Lessons

1. **Never let label column names collide with feature column names.** Use a prefix like `fwd_` for all forward-looking quantities.
2. **Walk-forward doesn't catch leakage.** Need explicit feature-label independence checks.
3. **AUC > 0.85 on 5m crypto = almost always leakage.** Real alpha at this timeframe lives in microstructure, not classical indicators. Be suspicious.
4. **Feature importance must be inspected.** If one feature accounts for >30% of total gain, investigate why.
5. **Test against a baseline.** A model trained on shuffled labels should give AUC ≈ 0.50. We never ran this check.
6. **Live deployment is the ultimate sanity check.** The paper trader correctly refused to trade — that was the system telling us something was wrong. We almost rationalized it as "market regime is bad" instead of investigating.

---

## Fix applied

In `scripts/v5/v5_extract_features_cb.py` (commit pending):

```python
# Before (buggy):
df[f"ret_{H}"] = ret          # collides with feature ret_3 when H=3
df[f"mfe_{H}"] = mfe
df[f"mae_{H}"] = mae
df[f"tp_first_{H}"] = tp_first

# After (fixed):
df[f"fwd_ret_{H}"] = ret      # forward prefix prevents collision
df[f"fwd_mfe_{H}"] = mfe
df[f"fwd_mae_{H}"] = mae
df[f"fwd_tp_first_{H}"] = tp_first
```

Also updated `extract_one()` to reference the new column names:
```python
label_col_win = f"fwd_tp_first_{H_PRIMARY}"
label_col_pnl = f"fwd_ret_{H_PRIMARY}"
label_col_fav = f"fwd_mfe_{H_PRIMARY}"
label_col_adv = f"fwd_mae_{H_PRIMARY}"
```

## Required follow-up

1. **Wipe `feature_observations_cb` table** — all 536K rows are contaminated.
2. **Re-extract features** with the fixed extractor (~30 min on Mac).
3. **Retrain v5_cb_v2** to confirm AUC drops to ~0.54 (sanity check).
4. **Proceed to v6** — see `v6_design.md`. The v5 approach (5m + binary TP + classical indicators) has no real edge; v6 changes the label to regression and adds 21 new features (multi-TF, microstructure, cross-asset).
