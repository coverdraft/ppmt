"""
v6_backtest_filtered_15m.py — Walk-forward backtest on 15m TF for SHORT+LONG.

Mirrors v6_backtest_filtered.py (5m LONG) but:
  - Reads from feature_observations_v6_15m table (label=fwd_ret_1)
  - Loads SHORT-expert v2 15m models (data/v6_models/short_expert_v2_15m/)
  - Tests BOTH sides: LONG (pred > thr) and SHORT (pred < -thr)
  - Walk-forward filter selection per-side: pick top-K hours using prior windows'
    LONG trades for LONG filter, SHORT trades for SHORT filter
  - Two thresholds tested: 0.30% (apples-to-apples vs 5m) and 0.50% (tighter,
    scaled to 15m noise level)

Output: data/v6_models/v6_filtered_backtest_15m.json
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

LOG = logging.getLogger("v6_filt_15m")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = '/home/z/my-project/data/ppmt.db'
MODELS_DIR = Path('/home/z/my-project/data/v6_models/short_expert_v2_15m')
OUT_DIR = Path('/home/z/my-project/data/v6_models')

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
POSITION_NOTIONAL = 700.0
ACCOUNT_SIZE = 10000.0
TABLE = "feature_observations_v6_15m"
LABEL = "fwd_ret_1"

WINDOWS = ['2025-04', '2025-05', '2025-06', '2025-09', '2025-10']

# Test multiple threshold combinations
# LONG_THRESHOLD and SHORT_THRESHOLD are independent
THRESHOLD_CONFIGS = [
    {'name': 'sym_030', 'long_thr': 0.30, 'short_thr': 0.30},
    {'name': 'sym_050', 'long_thr': 0.50, 'short_thr': 0.50},
    {'name': 'asym_long_tight', 'long_thr': 0.30, 'short_thr': 0.50},  # SHORT tighter (15m noise)
]

K_HOURS_GRID = [8, 10, 12, 14, 24]


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
        SELECT symbol, ts, window, {LABEL}, {feat_cols}
        FROM {TABLE}
        WHERE {LABEL} IS NOT NULL
          AND ts >= ? AND ts < ?
        ORDER BY ts ASC
    """
    df = pd.read_sql_query(sql, conn, params=(start_ts, end_ts))
    conn.close()
    for f in FEATURE_NAMES:
        df[f] = pd.to_numeric(df[f], errors='coerce').replace([np.inf, -np.inf], 0).fillna(0)
    df[LABEL] = pd.to_numeric(df[LABEL], errors='coerce').fillna(0)
    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    df['hour_utc'] = df['ts'].dt.hour
    return df


def predict_window(df: pd.DataFrame, model_path: str) -> np.ndarray:
    model = lgb.Booster(model_file=model_path)
    X = df[FEATURE_NAMES].values.astype(np.float32)
    return model.predict(X)


def pick_top_hours_for_side(prior_dfs: list[pd.DataFrame], k: int, side: str,
                            long_thr: float, short_thr: float) -> list[int]:
    """Pick top-K hours by PnL on the given side (LONG or SHORT) using prior windows."""
    if not prior_dfs or k >= 24:
        return list(range(24))
    prior = pd.concat(prior_dfs, ignore_index=True)
    if side == 'LONG':
        entered = prior[prior['pred'] > long_thr].copy()
        entered['net'] = entered[LABEL] - ROUND_TRIP_COST_PCT
    else:  # SHORT
        entered = prior[prior['pred'] < -short_thr].copy()
        entered['net'] = -entered[LABEL] - ROUND_TRIP_COST_PCT
    if len(entered) == 0:
        return list(range(24))
    by_hour = entered.groupby('hour_utc')['net'].sum().sort_values(ascending=False)
    return sorted(by_hour.head(k).index.tolist())


def backtest_side_filtered(df: pd.DataFrame, side: str, hours: list[int],
                           long_thr: float, short_thr: float) -> dict:
    """Apply hour filter, compute trade stats for the given side."""
    mask = df['hour_utc'].isin(hours)
    fdf = df[mask]
    if side == 'LONG':
        entered = fdf[fdf['pred'] > long_thr].copy()
        entered['net'] = entered[LABEL] - ROUND_TRIP_COST_PCT
    else:  # SHORT
        entered = fdf[fdf['pred'] < -short_thr].copy()
        entered['net'] = -entered[LABEL] - ROUND_TRIP_COST_PCT
    if len(entered) == 0:
        return {'n_trades': 0, 'wr': 0.0, 'pf': 0.0, 'avg_pnl': 0.0,
                'tot_pnl_pct': 0.0, 'tot_dollars': 0.0, 'sharpe_ann': 0.0}
    net = entered['net']
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
    std = net.std() if len(net) > 1 else 0.001
    sharpe_per_trade = net.mean() / std if std > 0 else 0.0
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
    print("v6 15m FILTERED BACKTEST — walk-forward, SHORT + LONG, no in-sample bias")
    print("=" * 110)
    print(f"Label: {LABEL}  Table: {TABLE}")
    print(f"Cost: {ROUND_TRIP_COST_PCT}%   Notional: ${POSITION_NOTIONAL}   Account: ${ACCOUNT_SIZE}")
    print(f"Windows: {WINDOWS}")
    print(f"Threshold configs: {[(c['name'], c['long_thr'], c['short_thr']) for c in THRESHOLD_CONFIGS]}")
    print(f"K_HOURS_GRID: {K_HOURS_GRID}")
    print()

    # Load + predict all windows
    print("Loading + predicting all windows...")
    windows_data = {}
    for w in WINDOWS:
        df = load_window_data(w)
        model_path = MODELS_DIR / f'v6_short_expert_v2_15m_{w}.txt'
        if not model_path.exists():
            LOG.warning("Model missing for %s — skipping", w)
            continue
        df['pred'] = predict_window(df, str(model_path))
        windows_data[w] = df
        print(f"  {w}: {len(df):,} rows, pred mean={df['pred'].mean():+.4f}% std={df['pred'].std():.4f}%")
    print()

    # For each threshold config + K_hours: walk-forward filter selection per side
    all_results = {}
    for cfg in THRESHOLD_CONFIGS:
        print("=" * 110)
        print(f"CONFIG: {cfg['name']}  LONG_thr={cfg['long_thr']}%  SHORT_thr={cfg['short_thr']}%")
        print("=" * 110)
        cfg_results = {}  # k -> {'long': [...], 'short': [...], 'combined': [...]}

        for k in K_HOURS_GRID:
            long_per_w = []
            short_per_w = []
            combined_per_w = []
            for i, w in enumerate(WINDOWS):
                if w not in windows_data:
                    continue
                df_test = windows_data[w]
                prior_dfs = [windows_data[WINDOWS[j]] for j in range(i) if WINDOWS[j] in windows_data]
                long_hours = pick_top_hours_for_side(prior_dfs, k, 'LONG', cfg['long_thr'], cfg['short_thr'])
                short_hours = pick_top_hours_for_side(prior_dfs, k, 'SHORT', cfg['long_thr'], cfg['short_thr'])
                long_r = backtest_side_filtered(df_test, 'LONG', long_hours, cfg['long_thr'], cfg['short_thr'])
                short_r = backtest_side_filtered(df_test, 'SHORT', short_hours, cfg['long_thr'], cfg['short_thr'])
                long_r['window'] = w; long_r['k_hours'] = k; long_r['hours_used'] = long_hours
                short_r['window'] = w; short_r['k_hours'] = k; short_r['hours_used'] = short_hours
                combined_r = {
                    'window': w, 'k_hours': k,
                    'n_trades': long_r['n_trades'] + short_r['n_trades'],
                    'tot_dollars': long_r['tot_dollars'] + short_r['tot_dollars'],
                    'long_dollars': long_r['tot_dollars'],
                    'short_dollars': short_r['tot_dollars'],
                    'long_trades': long_r['n_trades'],
                    'short_trades': short_r['n_trades'],
                }
                long_per_w.append(long_r)
                short_per_w.append(short_r)
                combined_per_w.append(combined_r)
            cfg_results[k] = {'long': long_per_w, 'short': short_per_w, 'combined': combined_per_w}
        all_results[cfg['name']] = cfg_results

        # Aggregate print
        print(f"\nAGGREGATE across {len(WINDOWS)} windows:")
        print(f"  {'k_h':>4} {'L_trades':>8} {'L_$':>10} {'S_trades':>8} {'S_$':>10} {'C_trades':>8} {'C_$':>12} {'ROI_$10K':>10}")
        for k in K_HOURS_GRID:
            cr = cfg_results[k]['combined']
            lt = sum(r['long_trades'] for r in cr)
            ld = sum(r['long_dollars'] for r in cr)
            st = sum(r['short_trades'] for r in cr)
            sd = sum(r['short_dollars'] for r in cr)
            ct = lt + st
            cd = ld + sd
            roi = (cd / ACCOUNT_SIZE) * 100
            print(f"  {k:>4d} {lt:>8d} {ld:>+10.2f} {st:>8d} {sd:>+10.2f} {ct:>8d} {cd:>+12.2f} {roi:>+10.2f}%")
        print()

    # Find best config overall
    print("=" * 110)
    print("BEST CONFIGS PER THRESHOLD SET")
    print("=" * 110)
    best_per_cfg = {}
    for cfg_name, cfg_res in all_results.items():
        best_k = None
        best_dollars = -float('inf')
        for k, kr in cfg_res.items():
            tot = sum(r['tot_dollars'] for r in kr['combined'])
            if tot > best_dollars:
                best_dollars = tot
                best_k = k
        best_per_cfg[cfg_name] = {'k_hours': best_k, 'tot_dollars': best_dollars}
        print(f"  {cfg_name}: best k_hours={best_k}  combined=${best_dollars:+.2f}")

    # Detailed per-window for the overall best
    overall_best_cfg = max(best_per_cfg.keys(), key=lambda c: best_per_cfg[c]['tot_dollars'])
    overall_best_k = best_per_cfg[overall_best_cfg]['k_hours']
    print()
    print("=" * 110)
    print(f"OVERALL BEST: cfg={overall_best_cfg}  k_hours={overall_best_k}")
    print("=" * 110)
    cr = all_results[overall_best_cfg][overall_best_k]['combined']
    print(f"  {'window':10s} {'L_trades':>8} {'L_$':>10} {'S_trades':>8} {'S_$':>10} {'C_trades':>8} {'C_$':>12}")
    for r in cr:
        print(f"  {r['window']:10s} {r['long_trades']:>8d} {r['long_dollars']:>+10.2f} "
              f"{r['short_trades']:>8d} {r['short_dollars']:>+10.2f} "
              f"{r['n_trades']:>8d} {r['tot_dollars']:>+12.2f}")
    tot = sum(r['tot_dollars'] for r in cr)
    lt = sum(r['long_trades'] for r in cr)
    st = sum(r['short_trades'] for r in cr)
    ld = sum(r['long_dollars'] for r in cr)
    sd = sum(r['short_dollars'] for r in cr)
    print(f"  {'TOTAL':10s} {lt:>8d} {ld:>+10.2f} {st:>8d} {sd:>+10.2f} {lt+st:>8d} {tot:>+12.2f}")
    print(f"\n  ROI on $10K: {tot/ACCOUNT_SIZE*100:+.2f}% over 5 months ({tot/ACCOUNT_SIZE*100*12/5:+.2f}% annualized)")

    # Save
    out = {
        'config': {
            'timeframe': '15m',
            'label': LABEL,
            'table': TABLE,
            'round_trip_cost_pct': ROUND_TRIP_COST_PCT,
            'position_notional': POSITION_NOTIONAL,
            'account_size': ACCOUNT_SIZE,
            'windows': WINDOWS,
            'threshold_configs': THRESHOLD_CONFIGS,
            'k_hours_grid': K_HOURS_GRID,
        },
        'all_results': all_results,
        'best_per_cfg': best_per_cfg,
        'overall_best': {'cfg': overall_best_cfg, 'k_hours': overall_best_k,
                         'tot_dollars': best_per_cfg[overall_best_cfg]['tot_dollars']},
    }
    out_path = OUT_DIR / 'v6_filtered_backtest_15m.json'
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved: {out_path}")


if __name__ == '__main__':
    main()
