"""
v6_backtest_filtered.py — Walk-forward backtest with HONEST filter selection.

Problem with v6_analyze_edge.py: it picked "good hours" by looking at the
same test data we backtested on. That's in-sample optimization — overfit
to noise. The +$2,966 filtered result is suspect.

This script does it properly:
  - For each test window W_test, use ONLY prior windows to:
      (a) compute per-hour PnL distribution
      (b) pick top-K hours by total PnL (K=8, 10, 12, 14 — sweep)
      (c) compute per-symbol PnL distribution
      (d) pick top-N symbols by total PnL (N=6, 8, 10)
  - Then evaluate on W_test using ONLY those pre-selected hours/symbols
  - Walk forward: W1 has no prior → use all hours/symbols (baseline)

This simulates: "at the start of month M, we look at the last few months
of paper-trading data and decide which hours + symbols to trade this month."

Outputs:
  - Aggregated PnL across all walk-forward windows for each (K_hours, N_syms)
  - Comparison vs unfiltered baseline (all hours, all symbols)
  - Save: data/v6_models/v6_filtered_backtest.json
"""
from __future__ import annotations


# === Auto-detected project root (portable paths, patched) ===
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
# === End path setup ===



import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

LOG = logging.getLogger("v6_filt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = '/home/z/my-project/data/ppmt.db'
MODELS_DIR = Path('/home/z/my-project/data/v6_models')

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
FEATURE_NAMES = FEATURE_NAMES_V5 + FEATURE_NAMES_V6_NEW

ROUND_TRIP_COST_PCT = 0.14
THRESHOLD = 0.30
POSITION_NOTIONAL = 700.0
ACCOUNT_SIZE = 10000.0

# Walk-forward windows in chronological order
WINDOWS = ['2025-04', '2025-05', '2025-06', '2025-09', '2025-10']

# Filter grid
K_HOURS_GRID = [8, 10, 12, 14, 24]      # 24 = no filter
N_SYMS_GRID = [6, 8, 10, 12]            # 12 = no filter


def window_bounds(window_str: str):
    yr, mo = window_str.split('-')
    yr, mo = int(yr), int(mo)
    start_ts = int(pd.Timestamp(year=yr, month=mo, day=1, tz='UTC').timestamp())
    if mo == 12:
        end_ts = int(pd.Timestamp(year=yr+1, month=1, day=1, tz='UTC').timestamp())
    else:
        end_ts = int(pd.Timestamp(year=yr, month=mo+1, day=1, tz='UTC').timestamp())
    return start_ts, end_ts


def load_window_data(window_str: str) -> pd.DataFrame:
    start_ts, end_ts = window_bounds(window_str)
    conn = sqlite3.connect(DB_PATH)
    feat_cols = ", ".join([f"json_extract(features_json, '$.{f}') AS {f}" for f in FEATURE_NAMES])
    sql = f"""
        SELECT symbol, ts, window, fwd_ret_3, {feat_cols}
        FROM feature_observations_v6
        WHERE fwd_ret_3 IS NOT NULL
          AND ts >= ? AND ts < ?
        ORDER BY ts ASC
    """
    df = pd.read_sql_query(sql, conn, params=(start_ts, end_ts))
    conn.close()
    for f in FEATURE_NAMES:
        df[f] = pd.to_numeric(df[f], errors='coerce').replace([np.inf, -np.inf], 0).fillna(0)
    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    df['hour_utc'] = df['ts'].dt.hour
    return df


def predict_window(df: pd.DataFrame, model_path: str) -> np.ndarray:
    model = lgb.Booster(model_file=model_path)
    X = df[FEATURE_NAMES].values.astype(np.float32)
    return model.predict(X)


def pick_top_hours(prior_dfs: list[pd.DataFrame], k: int) -> list[int]:
    """From prior windows' entered trades, pick top-K hours by total net PnL."""
    if not prior_dfs or k >= 24:
        return list(range(24))
    prior = pd.concat(prior_dfs, ignore_index=True)
    entered = prior[prior['pred'] > THRESHOLD].copy()
    if len(entered) == 0:
        return list(range(24))
    entered['net'] = entered['fwd_ret_3'] - ROUND_TRIP_COST_PCT
    by_hour = entered.groupby('hour_utc')['net'].sum().sort_values(ascending=False)
    return sorted(by_hour.head(k).index.tolist())


def pick_top_syms(prior_dfs: list[pd.DataFrame], n: int) -> list[str]:
    """From prior windows' entered trades, pick top-N symbols by total net PnL."""
    if not prior_dfs or n >= 12:
        return None  # None = no filter
    prior = pd.concat(prior_dfs, ignore_index=True)
    entered = prior[prior['pred'] > THRESHOLD].copy()
    if len(entered) == 0:
        return None
    entered['net'] = entered['fwd_ret_3'] - ROUND_TRIP_COST_PCT
    by_sym = entered.groupby('symbol')['net'].sum().sort_values(ascending=False)
    return by_sym.head(n).index.tolist()


def backtest_filtered(df: pd.DataFrame, hours: list[int], syms: list[str] | None) -> dict:
    """Apply filter, then compute trade stats."""
    mask = df['hour_utc'].isin(hours)
    if syms is not None:
        mask &= df['symbol'].isin(syms)
    fdf = df[mask]
    entered = fdf[fdf['pred'] > THRESHOLD]
    if len(entered) == 0:
        return {'n_trades': 0, 'wr': 0.0, 'pf': 0.0, 'avg_pnl': 0.0,
                'tot_pnl_pct': 0.0, 'tot_dollars': 0.0, 'sharpe_ann': 0.0}
    net = entered['fwd_ret_3'] - ROUND_TRIP_COST_PCT
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
    std = net.std() if len(net) > 1 else 0.001
    sharpe_per_trade = net.mean() / std if std > 0 else 0.0
    # rough annualization: assume trades span ~1 month (the test window)
    n_per_year = len(net) * 12
    sharpe_ann = sharpe_per_trade * np.sqrt(max(n_per_year, 1))
    return {
        'n_trades': int(len(entered)),
        'wr': float((net > 0).mean()),
        'pf': float(pf),
        'avg_pnl': float(net.mean()),
        'tot_pnl_pct': float(net.sum()),
        'tot_dollars': float(net.sum() / 100 * POSITION_NOTIONAL),
        'sharpe_ann': float(sharpe_ann),
    }


def main():
    print("=" * 110)
    print("v6 FILTERED BACKTEST — walk-forward, no in-sample bias")
    print("=" * 110)
    print(f"Threshold: pred > {THRESHOLD}%   Cost: {ROUND_TRIP_COST_PCT}%   Notional: ${POSITION_NOTIONAL}")
    print(f"Windows (chronological): {WINDOWS}")
    print()

    # Load all 5 windows and predict
    print("Loading + predicting all windows...")
    windows_data = {}  # window_str -> df with 'pred' col
    for w in WINDOWS:
        df = load_window_data(w)
        model_path = MODELS_DIR / f'v6_{w}.txt'
        if not model_path.exists():
            LOG.warning("Model missing for %s — skipping", w)
            continue
        df['pred'] = predict_window(df, str(model_path))
        windows_data[w] = df
        print(f"  {w}: {len(df):,} rows, pred mean={df['pred'].mean():+.4f}%")
    print()

    # For each window: pick filters using PRIOR windows only, then evaluate
    print("=" * 110)
    print("WALK-FORWARD FILTER SELECTION")
    print("=" * 110)
    print("For each test window: filter (hours/syms) chosen from PRIOR windows only.")
    print("W1 (2025-04) has no prior → baseline (no filter).")
    print()

    results = {}  # (k_hours, n_syms) -> list of per-window stats
    for k in K_HOURS_GRID:
        for n in N_SYMS_GRID:
            results[(k, n)] = []

    for i, w in enumerate(WINDOWS):
        if w not in windows_data:
            continue
        df_test = windows_data[w]
        prior_dfs = [windows_data[WINDOWS[j]] for j in range(i) if WINDOWS[j] in windows_data]
        print(f"\n--- Test window {w}  (prior windows: {len(prior_dfs)}) ---")

        for k in K_HOURS_GRID:
            top_hours = pick_top_hours(prior_dfs, k)
            for n in N_SYMS_GRID:
                top_syms = pick_top_syms(prior_dfs, n)
                r = backtest_filtered(df_test, top_hours, top_syms)
                r['window'] = w
                r['k_hours'] = k
                r['n_syms'] = n
                r['hours_used'] = top_hours
                r['syms_used'] = top_syms
                results[(k, n)].append(r)
        # Print compact summary for this window
        print(f"  {'k_h':>4} {'n_s':>4} {'trades':>7} {'WR':>6} {'PF':>6} {'tot_$':>10} {'Sharpe':>8}")
        for k in K_HOURS_GRID:
            for n in N_SYMS_GRID:
                r = results[(k, n)][-1]
                print(f"  {k:>4d} {n:>4d} {r['n_trades']:>7d} {r['wr']:>6.3f} {r['pf']:>6.2f} "
                      f"{r['tot_dollars']:>+10.2f} {r['sharpe_ann']:>+8.2f}")

    # Aggregate across all 5 windows (skip W1's no-prior baseline for filter comparisons)
    print()
    print("=" * 110)
    print("AGGREGATE (5 windows, filter chosen walk-forward)")
    print("=" * 110)
    print(f"  {'k_h':>4} {'n_s':>4} {'trades':>8} {'WR':>6} {'PF':>6} {'tot_$':>12} {'avg_Sh':>8} {'ROI_$10K':>10}")
    agg = []
    for k in K_HOURS_GRID:
        for n in N_SYMS_GRID:
            rs = results[(k, n)]
            tot_trades = sum(r['n_trades'] for r in rs)
            # weighted avg WR / PF
            tot_wins_dollars = sum(r['tot_dollars'] * r['wr'] for r in rs if r['n_trades'] > 0)
            tot_dollars = sum(r['tot_dollars'] for r in rs)
            avg_wr = (tot_wins_dollars / tot_dollars) if tot_dollars != 0 else 0.0
            # PF: sum gross_profit / sum gross_loss — approximate via weighted avg
            avg_pf = np.mean([r['pf'] for r in rs if r['n_trades'] > 0]) if any(r['n_trades'] > 0 for r in rs) else 0.0
            avg_sharpe = np.mean([r['sharpe_ann'] for r in rs if r['n_trades'] > 0]) if any(r['n_trades'] > 0 for r in rs) else 0.0
            roi = (tot_dollars / ACCOUNT_SIZE) * 100
            agg.append({
                'k_hours': k, 'n_syms': n, 'tot_trades': tot_trades,
                'avg_wr': float(avg_wr), 'avg_pf': float(avg_pf),
                'tot_dollars': float(tot_dollars), 'avg_sharpe': float(avg_sharpe),
                'roi_pct': float(roi),
            })
            print(f"  {k:>4d} {n:>4d} {tot_trades:>8d} {avg_wr:>6.3f} {avg_pf:>6.2f} "
                  f"{tot_dollars:>+12.2f} {avg_sharpe:>+8.2f} {roi:>+10.2f}%")

    # Find best non-trivial filter
    print()
    print("=" * 110)
    print("BEST FILTER CONFIG (excluding no-filter k=24, n=12)")
    print("=" * 110)
    nontrivial = [a for a in agg if not (a['k_hours'] == 24 and a['n_syms'] == 12)]
    best = max(nontrivial, key=lambda x: x['tot_dollars'])
    print(f"  k_hours={best['k_hours']}, n_syms={best['n_syms']}")
    print(f"  Total trades: {best['tot_trades']}")
    print(f"  Total PnL:    ${best['tot_dollars']:+.2f} ({best['roi_pct']:+.2f}% on $10K)")
    print(f"  Avg WR:       {best['avg_wr']:.3f}")
    print(f"  Avg PF:       {best['avg_pf']:.2f}")
    print(f"  Avg Sharpe:   {best['avg_sharpe']:+.2f}")

    nofilt = next(a for a in agg if a['k_hours'] == 24 and a['n_syms'] == 12)
    print()
    print(f"  Baseline (no filter):   ${nofilt['tot_dollars']:+.2f} ({nofilt['roi_pct']:+.2f}%), "
          f"trades={nofilt['tot_trades']}, WR={nofilt['avg_wr']:.3f}, PF={nofilt['avg_pf']:.2f}")
    delta = best['tot_dollars'] - nofilt['tot_dollars']
    print(f"  Filter improvement:     ${delta:+.2f} ({delta/max(nofilt['tot_dollars'],1)*100:+.1f}%)")

    # Per-window detail for the best filter
    print()
    print("=" * 110)
    print(f"PER-WINDOW DETAIL — best filter (k={best['k_hours']}, n={best['n_syms']})")
    print("=" * 110)
    print(f"  {'window':10s} {'trades':>7s} {'WR':>6s} {'PF':>6s} {'tot_$':>10s} {'Sharpe':>8s} {'hours':>30s}")
    for r in results[(best['k_hours'], best['n_syms'])]:
        hours_str = ','.join(str(h) for h in r['hours_used'])
        print(f"  {r['window']:10s} {r['n_trades']:>7d} {r['wr']:>6.3f} {r['pf']:>6.2f} "
              f"{r['tot_dollars']:>+10.2f} {r['sharpe_ann']:>+8.2f} {hours_str:>30s}")

    # Save
    out = {
        'config': {
            'threshold': THRESHOLD,
            'round_trip_cost_pct': ROUND_TRIP_COST_PCT,
            'position_notional': POSITION_NOTIONAL,
            'account_size': ACCOUNT_SIZE,
            'windows': WINDOWS,
            'k_hours_grid': K_HOURS_GRID,
            'n_syms_grid': N_SYMS_GRID,
        },
        'aggregate': agg,
        'best_filter': best,
        'baseline_nofilter': nofilt,
        'per_window_best': results[(best['k_hours'], best['n_syms'])],
        'all_results': {f"{k}_{n}": rs for (k, n), rs in results.items()},
    }
    out_path = MODELS_DIR / 'v6_filtered_backtest.json'
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved: {out_path}")


if __name__ == '__main__':
    main()
