"""Diagnose F7 prediction distributions on opposite-sign rows."""
import json
import pandas as pd
import numpy as np
import lightgbm as lgb

FEATURE_NAMES_V5 = [
    "body_pct", "upper_wick", "lower_wick", "body_abs", "close_pos", "range_pct",
    "ret_1", "ret_3", "ret_5", "ret_10", "log_ret_1",
    "atr_pct", "vol_std_10", "rsi_14",
    "ema_9_20_cross", "ema_20_50_cross", "ema_9_slope", "ema_20_slope", "ema_50_slope",
    "price_vs_ema20", "price_vs_ema50", "vol_ratio", "vol_z",
    "last_3_body_sum", "last_3_range_sum",
    "bullish_engulf_2", "hammer_like", "shooting_star",
    "breakout_up", "breakout_down", "dist_to_high_20", "dist_to_low_20",
    "trend_50", "vol_regime", "trending",
    "hour_sin", "hour_cos", "dow",
]
FEATURE_NAMES_V6_NEW = [
    "btc_ret_1m", "btc_ret_5m", "btc_ret_15m", "btc_vol_z",
    "btc_trend_50", "eth_corr_30", "btc_alt_spread_15m", "btc_volatility_regime",
    "vol_delta_3", "wick_imbalance_3", "body_consistency_5",
    "range_expansion_3", "close_persistence_5", "vol_acceleration",
    "atr_percentile_50", "trend_strength_50", "regime_vol_trend", "hour_quantile",
    "alt_lead_5m", "alt_lag_signal", "momentum_dispersion",
]
FEATURE_NAMES_F4 = [
    "funding_rate", "funding_rate_z",
    "oi_change_1h", "oi_change_4h",
    "sector_blue_chip", "sector_large_cap", "sector_old_meme", "sector_new_meme",
    "sector_idx",
    "day_of_week_sin", "day_of_week_cos", "day_of_week",
]
FEATURE_NAMES = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW + FEATURE_NAMES_F4
LABEL = "fwd_ret_3"

# Load both parquets, union
lf = pd.read_parquet('/home/z/my-project/data/v7_models/long_expert/long_features.parquet',
                     columns=FEATURE_NAMES + ["symbol", "ts", "window", LABEL])
sf = pd.read_parquet('/home/z/my-project/data/v7_models/short_expert/short_features.parquet',
                     columns=FEATURE_NAMES + ["symbol", "ts", "window", LABEL])
df = pd.concat([lf, sf], ignore_index=True)
df["ts_dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)

# Filter to April 2025
apr = df[(df["ts_dt"].dt.year==2025) & (df["ts_dt"].dt.month==4)].copy()
print(f"April 2025: {len(apr)} rows ({(apr[LABEL]>0).sum()} positive, {(apr[LABEL]<0).sum()} negative)")

# Load both April models
long_model  = lgb.Booster(model_file='/home/z/my-project/data/v7_models/long_expert/v7_long_expert_2025-04.txt')
short_model = lgb.Booster(model_file='/home/z/my-project/data/v7_models/short_expert/v7_short_expert_2025-04.txt')

X = apr[FEATURE_NAMES].values.astype(np.float32)
apr["pred_long"]  = long_model.predict(X)
apr["pred_short"] = short_model.predict(X)
apr["pred_short_abs"] = np.abs(apr["pred_short"])

# Distribution of pred_long on positive vs negative rows
pos = apr[apr[LABEL] > 0]
neg = apr[apr[LABEL] < 0]
print()
print("=== pred_long distribution ===")
print(f"  on positive rows (n={len(pos)}): mean={pos['pred_long'].mean():+.4f}  median={pos['pred_long'].median():+.4f}  >0.30={100*(pos['pred_long']>0.30).mean():.1f}%  >0.50={100*(pos['pred_long']>0.50).mean():.1f}%")
print(f"  on negative rows (n={len(neg)}): mean={neg['pred_long'].mean():+.4f}  median={neg['pred_long'].median():+.4f}  >0.30={100*(neg['pred_long']>0.30).mean():.1f}%  >0.50={100*(neg['pred_long']>0.50).mean():.1f}%")

print()
print("=== |pred_short| distribution ===")
print(f"  on positive rows (n={len(pos)}): mean={pos['pred_short_abs'].mean():+.4f}  median={pos['pred_short_abs'].median():+.4f}  >0.40={100*(pos['pred_short_abs']>0.40).mean():.1f}%  >0.60={100*(pos['pred_short_abs']>0.60).mean():.1f}%")
print(f"  on negative rows (n={len(neg)}): mean={neg['pred_short_abs'].mean():+.4f}  median={neg['pred_short_abs'].median():+.4f}  >0.40={100*(neg['pred_short_abs']>0.40).mean():.1f}%  >0.60={100*(neg['pred_short_abs']>0.60).mean():.1f}%")

print()
print("=== Decision breakdown (LONG if pred_long>0.30 AND pred_long>|pred_short|) ===")
apr["action"] = "WAIT"
apr.loc[(apr["pred_long"]>0.30) & (apr["pred_long"]>apr["pred_short_abs"]), "action"] = "LONG"
apr.loc[(apr["pred_short_abs"]>0.40) & (apr["pred_short_abs"]>apr["pred_long"]), "action"] = "SHORT"

for action in ["LONG", "SHORT", "WAIT"]:
    sub = apr[apr["action"]==action]
    if len(sub)==0:
        continue
    pos_pct = 100*(sub[LABEL]>0).mean()
    print(f"  {action}: n={len(sub):,}  positive_actual={pos_pct:.1f}%  avg_fwd_ret={sub[LABEL].mean():+.4f}%")

# Compute PnL
print()
print("=== PnL breakdown ===")
apr["pnl"] = 0.0
apr.loc[apr["action"]=="LONG", "pnl"] = apr[LABEL] - 0.14
apr.loc[apr["action"]=="SHORT", "pnl"] = -apr[LABEL] - 0.14
for action in ["LONG", "SHORT"]:
    sub = apr[apr["action"]==action]
    if len(sub)==0:
        continue
    wins = (sub["pnl"]>0).sum()
    print(f"  {action}: n={len(sub):,}  WR={100*wins/len(sub):.1f}%  avg_pnl={sub['pnl'].mean():+.4f}%  total_pnl={sub['pnl'].sum():+.2f}%")

# Sanity check: what if we ONLY trade when BOTH experts agree?
# i.e., LONG if pred_long>0.30 AND pred_short>-0.05 (SHORT expert says "not much downside")
print()
print("=== Alternative: LONG if pred_long>0.30 AND pred_short_abs<0.10 ===")
alt_mask = (apr["pred_long"]>0.30) & (apr["pred_short_abs"]<0.10)
alt = apr[alt_mask]
print(f"  n={len(alt):,}  positive_actual={100*(alt[LABEL]>0).mean():.1f}%  avg_fwd_ret={alt[LABEL].mean():+.4f}%")
alt_pnl = alt[LABEL] - 0.14
print(f"  WR={100*(alt_pnl>0).mean():.1f}%  avg_pnl={alt_pnl.mean():+.4f}%  total_pnl={alt_pnl.sum():+.2f}%")

print()
print("=== Alternative: SHORT if pred_short_abs>0.40 AND pred_long<0.10 ===")
alt_mask = (apr["pred_short_abs"]>0.40) & (apr["pred_long"]<0.10)
alt = apr[alt_mask]
print(f"  n={len(alt):,}  negative_actual={100*(alt[LABEL]<0).mean():.1f}%  avg_fwd_ret={alt[LABEL].mean():+.4f}%")
alt_pnl = -alt[LABEL] - 0.14
print(f"  WR={100*(alt_pnl>0).mean():.1f}%  avg_pnl={alt_pnl.mean():+.4f}%  total_pnl={alt_pnl.sum():+.2f}%")

# Higher thresholds
print()
print("=== Higher thresholds (more selective) ===")
for tl, ts in [(0.5, 0.6), (0.75, 0.85), (1.0, 1.0)]:
    apr["action2"] = "WAIT"
    apr.loc[(apr["pred_long"]>tl) & (apr["pred_long"]>apr["pred_short_abs"]), "action2"] = "LONG"
    apr.loc[(apr["pred_short_abs"]>ts) & (apr["pred_short_abs"]>apr["pred_long"]), "action2"] = "SHORT"
    apr["pnl2"] = 0.0
    apr.loc[apr["action2"]=="LONG", "pnl2"] = apr[LABEL] - 0.14
    apr.loc[apr["action2"]=="SHORT", "pnl2"] = -apr[LABEL] - 0.14
    for action in ["LONG", "SHORT"]:
        sub = apr[apr["action2"]==action]
        if len(sub)==0:
            continue
        wins = (sub["pnl2"]>0).sum()
        print(f"  thr_L={tl} thr_S={ts}  {action}: n={len(sub):,}  WR={100*wins/len(sub):.1f}%  avg_pnl={sub['pnl2'].mean():+.4f}%  total_pnl={sub['pnl2'].sum():+.2f}%")
