"""Diagnóstico: stats del label + correlaciones feature-target + drift train/val.
Uso: python scripts/v7/diagnose_signal.py BTC/USDT 30
"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from scripts.v7.v7_layer2_rolling_retrain import fetch_30d_data, HORIZON
from scripts.v7.paper_trader.features import FEATURE_NAMES, extract_features
from scripts.v7.paper_trader.feed import Feed

def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC/USDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    print(f"=== DIAGNÓSTICO {symbol} {days}d (HORIZON={HORIZON}) ===\n")

    feed = Feed(exchange_id="bybit")
    sym_df, btc_df, eth_df = fetch_30d_data(feed, symbol, days)
    feat_df = extract_features(sym_df, btc_df, eth_df)
    # Label en porcentaje (igual que el entrenamiento: * 100)
    c = feat_df["close"].values
    n = len(feat_df)
    fwd = np.full(n, np.nan)
    for i in range(n - HORIZON):
        fwd[i] = (c[i + HORIZON] - c[i]) / c[i] * 100
    feat_df["fwd_ret_3"] = fwd

    valid = feat_df[FEATURE_NAMES + ["fwd_ret_3"]].dropna().reset_index(drop=True)
    print(f"Clean rows: {len(valid)}")

    y = valid["fwd_ret_3"]
    print(f"\n=== LABEL STATS (fwd_ret_3) ===")
    print(f"  mean:      {y.mean():+.6f}")
    print(f"  std:       {y.std():.6f}")
    print(f"  |mean|/std (signal/noise): {abs(y.mean())/y.std():.4f}")
    print(f"  skew:      {y.skew():+.3f}")
    print(f"  kurtosis:  {y.kurtosis():+.3f}")
    print(f"  quantiles 5/25/50/75/95:")
    for q in [0.05, 0.25, 0.5, 0.75, 0.95]:
        print(f"    {q:.2f}: {y.quantile(q):+.4f}")

    print(f"\n=== TOP 20 |corr| FEATURE vs LABEL ===")
    corrs = []
    for col in FEATURE_NAMES:
        c = valid[col].corr(valid["fwd_ret_3"])
        if not np.isnan(c):
            corrs.append((col, c))
    corrs.sort(key=lambda x: abs(x[1]), reverse=True)
    for col, c in corrs[:20]:
        print(f"  {col:30s} {c:+.4f}")
    n_02 = sum(1 for _, c in corrs if abs(c) > 0.02)
    n_05 = sum(1 for _, c in corrs if abs(c) > 0.05)
    n_10 = sum(1 for _, c in corrs if abs(c) > 0.10)
    print(f"\n  |corr| > 0.02: {n_02}/{len(corrs)}")
    print(f"  |corr| > 0.05: {n_05}/{len(corrs)}")
    print(f"  |corr| > 0.10: {n_10}/{len(corrs)}")

    n = len(valid)
    n_val = int(n * 0.2)
    train = valid.iloc[:-n_val]
    val = valid.iloc[-n_val:]
    print(f"\n=== TRAIN/VAL DRIFT (train={len(train)} val={len(val)}) ===")
    print(f"  {'feature':30s} {'train_mean':>12s} {'val_mean':>12s} {'drift_σ':>10s}")
    drifts = []
    for col in FEATURE_NAMES:
        tm, vm = train[col].mean(), val[col].mean()
        ts = train[col].std()
        d = (vm - tm) / ts if ts > 1e-10 else 0
        drifts.append((col, tm, vm, d))
    drifts.sort(key=lambda x: abs(x[3]), reverse=True)
    for col, tm, vm, d in drifts[:15]:
        print(f"  {col:30s} {tm:>12.4f} {vm:>12.4f} {d:>+10.3f}")

    tm, vm = train["fwd_ret_3"].mean(), val["fwd_ret_3"].mean()
    ts, vs = train["fwd_ret_3"].std(), val["fwd_ret_3"].std()
    print(f"\n  LABEL fwd_ret_3:")
    print(f"    train mean/std: {tm:+.6f} / {ts:.6f}")
    print(f"    val   mean/std: {vm:+.6f} / {vs:.6f}")
    print(f"    mean drift (σ): {(vm-tm)/ts:+.3f}   std ratio (v/t): {vs/ts:.3f}")

if __name__ == "__main__":
    main()
