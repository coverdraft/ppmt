#!/usr/bin/env python3
"""Diagnostic: check label_hit_tp_first distribution among predicted-positive signals."""
import json
import sqlite3
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb

sys.path.insert(0, "/home/z/my-project/ppmt/src")

DB = "/home/z/my-project/data/ppmt.db"
MODEL_PATH = "/home/z/my-project/download/v5_lgbm_model_cb_v2.txt"

FEATURE_NAMES = [
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

conn = sqlite3.connect(DB, timeout=30)
rows = conn.execute("""
    SELECT symbol, timeframe, ts, asset_class, features_json,
           prior_expected_move, label_hit_tp_first, label_pnl
    FROM feature_observations_cb
    WHERE historical_regime = 'RECENT_2026'
      AND label_hit_tp_first IS NOT NULL
""").fetchall()
conn.close()
print(f"Total RECENT_2026 labeled rows: {len(rows)}")

df = pd.DataFrame(rows, columns=["symbol","timeframe","ts","asset_class","features_json",
                                  "prior_expected_move","label_hit_tp_first","label_pnl"])
fe = pd.json_normalize(df["features_json"].apply(json.loads))
for f in FEATURE_NAMES:
    if f not in fe.columns:
        fe[f] = 0.0
df = pd.concat([df.drop(columns=["features_json"]).reset_index(drop=True),
                fe[FEATURE_NAMES].reset_index(drop=True)], axis=1)

# Edge features
df["hour_utc"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.hour
hour = df["hour_utc"]
asia = hour.isin([0, 1, 2, 18, 19, 20, 21, 22, 23])
alt = ~df["asset_class"].isin(["blue_chip"])
scalp = df["timeframe"].isin(["1m", "5m", "15m"])
df["edge_strong"] = (alt & scalp & asia).astype(int)
score = alt.astype(int) + scalp.astype(int) + asia.astype(int)
df["edge_marginal"] = ((score == 2) & ~df["edge_strong"]).astype(int)

feature_cols = FEATURE_NAMES + ["edge_strong", "edge_marginal"]
model = lgb.Booster(model_file=str(MODEL_PATH))
df["proba"] = model.predict(df[feature_cols].values)

print(f"\nProba distribution:")
print(f"  min={df['proba'].min():.4f}  p25={df['proba'].quantile(0.25):.4f}")
print(f"  p50={df['proba'].quantile(0.5):.4f}  p75={df['proba'].quantile(0.75):.4f}")
print(f"  p90={df['proba'].quantile(0.90):.4f}  max={df['proba'].max():.4f}")

print(f"\nLabel balance (overall):")
print(f"  label=1 (TP first): {(df['label_hit_tp_first']==1).sum():>8}  ({(df['label_hit_tp_first']==1).mean()*100:.1f}%)")
print(f"  label=0 (SL first): {(df['label_hit_tp_first']==0).sum():>8}  ({(df['label_hit_tp_first']==0).mean()*100:.1f}%)")

print(f"\n=== Precision by threshold (FULL RECENT_2026) ===")
for t in [0.5, 0.6, 0.7, 0.8]:
    sig = df[df["proba"] >= t]
    if len(sig) == 0: continue
    prec = (sig["label_hit_tp_first"] == 1).mean()
    n = len(sig)
    print(f"  thr>={t}: n={n:>6}  precision={prec*100:.1f}%")

print(f"\n=== Label distribution among proba>=0.7 signals ===")
sig = df[df["proba"] >= 0.7]
print(f"  Total: {len(sig)}")
print(f"  label=1: {(sig['label_hit_tp_first']==1).sum()}  ({(sig['label_hit_tp_first']==1).mean()*100:.1f}%)")
print(f"  label=0: {(sig['label_hit_tp_first']==0).sum()}  ({(sig['label_hit_tp_first']==0).mean()*100:.1f}%)")

print(f"\n=== Check label_pnl among label=1 signals (should be ~+0.6%) ===")
sig_lab1 = sig[sig["label_hit_tp_first"] == 1]
print(f"  label_pnl stats: mean={sig_lab1['label_pnl'].mean():.4f}%  median={sig_lab1['label_pnl'].median():.4f}%")
print(f"  label_pnl range: min={sig_lab1['label_pnl'].min():.4f}%  max={sig_lab1['label_pnl'].max():.4f}%")

print(f"\n=== Check label_pnl among label=0 signals (should be ~-0.4%) ===")
sig_lab0 = sig[sig["label_hit_tp_first"] == 0]
print(f"  label_pnl stats: mean={sig_lab0['label_pnl'].mean():.4f}%  median={sig_lab0['label_pnl'].median():.4f}%")

print(f"\n=== Per-timeframe breakdown at proba>=0.7 ===")
for tf, sub in sig.groupby("timeframe"):
    print(f"  {tf}: n={len(sub):>5}  label=1 frac={ (sub['label_hit_tp_first']==1).mean()*100:.1f}%")

print(f"\n=== Per-symbol breakdown at proba>=0.7 ===")
for sym, sub in sig.groupby("symbol"):
    print(f"  {sym}: n={len(sub):>5}  label=1 frac={(sub['label_hit_tp_first']==1).mean()*100:.1f}%")
