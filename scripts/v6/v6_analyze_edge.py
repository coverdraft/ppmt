"""
v6_analyze_edge.py — Break down the v6 backtest edge by symbol and hour.

Question: the aggregate backtest shows a tiny directional edge (~51% vs 47-49%
baseline) that's only profitable at threshold 0.30%+. WHERE does this edge
concentrate? If it's all in BTC/ETH and hours 13-16 UTC, we can filter and
salvage a usable strategy. If it's spread evenly across noise, we have to
admit there's no real signal.

Outputs:
  1. Per-symbol breakdown @ thr=0.30% (n_trades, WR, PF, avg_pnl, total_$)
  2. Per-hour breakdown @ thr=0.30%
  3. Per-symbol × per-hour heatmap (top 10 cells by total_$ PnL)
  4. Per-window (already done in v6_backtest.py, but recalc with stratification)
  5. Save: data/v6_models/v6_edge_breakdown.json

This is diagnostic only — no model retraining. Uses the 5 already-trained
walk-forward models and queries the feature_observations_v6 table.
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

LOG = logging.getLogger("v6_edge")
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
THRESHOLD = 0.30  # the threshold where aggregate edge first appears
POSITION_NOTIONAL = 700.0

# Walk-forward windows (same as v6_backtest.py)
WINDOWS = ['2025-04', '2025-05', '2025-06', '2025-09', '2025-10']


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
    df['pred'] = 0.0  # filled below
    return df


def predict_window(df: pd.DataFrame, model_path: str) -> np.ndarray:
    model = lgb.Booster(model_file=model_path)
    X = df[FEATURE_NAMES].values.astype(np.float32)
    return model.predict(X)


def stratify(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """For each group: count trades (pred>thr), WR (net_pnl>0), PF, avg_pnl, total_$."""
    entered = df[df['pred'] > THRESHOLD].copy()
    if len(entered) == 0:
        return pd.DataFrame(columns=group_cols + ['n_trades', 'win_rate', 'pf', 'avg_pnl_pct', 'total_pnl_pct', 'total_dollars'])
    entered['net_pnl'] = entered['fwd_ret_3'] - ROUND_TRIP_COST_PCT
    entered['is_win'] = (entered['net_pnl'] > 0).astype(float)

    g = entered.groupby(group_cols)
    rows = []
    for key, grp in g:
        if not isinstance(key, tuple):
            key = (key,)
        wins = grp[grp['net_pnl'] > 0]['net_pnl'].sum()
        losses = -grp[grp['net_pnl'] < 0]['net_pnl'].sum()
        pf = (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
        n = len(grp)
        wr = grp['is_win'].mean()
        avg_pnl = grp['net_pnl'].mean()
        tot_pnl = grp['net_pnl'].sum()
        tot_dollars = tot_pnl / 100 * POSITION_NOTIONAL
        row = dict(zip(group_cols, key))
        row.update({
            'n_trades': n, 'win_rate': float(wr), 'pf': float(pf),
            'avg_pnl_pct': float(avg_pnl), 'total_pnl_pct': float(tot_pnl),
            'total_dollars': float(tot_dollars),
        })
        rows.append(row)
    return pd.DataFrame(rows).sort_values('total_dollars', ascending=False)


def main():
    print("=" * 110)
    print(f"v6 EDGE BREAKDOWN — where does the +0.30% threshold edge actually live?")
    print("=" * 110)
    print(f"Threshold: pred > {THRESHOLD}% (LONG only)")
    print(f"Round-trip cost: {ROUND_TRIP_COST_PCT}%")
    print(f"Position notional: ${POSITION_NOTIONAL}")
    print()

    # Load all 5 windows and predict
    all_dfs = []
    for w in WINDOWS:
        print(f"  Loading window {w}...", end=' ', flush=True)
        df = load_window_data(w)
        model_path = MODELS_DIR / f'v6_{w}.txt'
        if not model_path.exists():
            print(f"MISSING model — skipping")
            continue
        df['pred'] = predict_window(df, str(model_path))
        all_dfs.append(df)
        print(f"{len(df):,} rows, pred mean={df['pred'].mean():+.4f}%")
    df = pd.concat(all_dfs, ignore_index=True)
    print(f"\nTotal: {len(df):,} rows across {len(WINDOWS)} windows")
    print()

    # ---------- 1. Per-symbol breakdown ----------
    print("=" * 110)
    print("1) PER-SYMBOL BREAKDOWN @ thr=0.30%")
    print("=" * 110)
    print(f"{'symbol':10s} {'trades':>7s} {'WR':>6s} {'PF':>6s} {'avg_pnl%':>9s} {'tot_pnl%':>10s} {'tot_$':>10s}")
    sym_df = stratify(df, ['symbol'])
    for _, r in sym_df.iterrows():
        print(f"{r['symbol']:10s} {int(r['n_trades']):>7d} {r['win_rate']:>6.3f} {r['pf']:>6.2f} "
              f"{r['avg_pnl_pct']:>+9.4f} {r['total_pnl_pct']:>+10.2f} {r['total_dollars']:>+10.2f}")
    print(f"\nProfitable symbols: {(sym_df['total_dollars'] > 0).sum()} / {len(sym_df)}")
    print()

    # ---------- 2. Per-hour breakdown ----------
    print("=" * 110)
    print("2) PER-HOUR (UTC) BREAKDOWN @ thr=0.30%")
    print("=" * 110)
    print(f"{'hour':>5s} {'trades':>7s} {'WR':>6s} {'PF':>6s} {'avg_pnl%':>9s} {'tot_pnl%':>10s} {'tot_$':>10s}")
    hour_df = stratify(df, ['hour_utc']).sort_values('hour_utc')
    for _, r in hour_df.iterrows():
        print(f"{int(r['hour_utc']):>5d} {int(r['n_trades']):>7d} {r['win_rate']:>6.3f} {r['pf']:>6.2f} "
              f"{r['avg_pnl_pct']:>+9.4f} {r['total_pnl_pct']:>+10.2f} {r['total_dollars']:>+10.2f}")
    print(f"\nProfitable hours: {(hour_df['total_dollars'] > 0).sum()} / {len(hour_df)}")
    print()

    # ---------- 3. Symbol × hour heatmap (top 15 + bottom 5) ----------
    print("=" * 110)
    print("3) SYMBOL × HOUR — TOP 15 CELLS by total_$ PnL")
    print("=" * 110)
    print(f"{'symbol':10s} {'hour':>5s} {'trades':>7s} {'WR':>6s} {'PF':>6s} {'tot_$':>10s}")
    sh_df = stratify(df, ['symbol', 'hour_utc'])
    for _, r in sh_df.head(15).iterrows():
        print(f"{r['symbol']:10s} {int(r['hour_utc']):>5d} {int(r['n_trades']):>7d} {r['win_rate']:>6.3f} "
              f"{r['pf']:>6.2f} {r['total_dollars']:>+10.2f}")
    print(f"\n  --- bottom 5 (worst cells) ---")
    for _, r in sh_df.tail(5).iterrows():
        print(f"{r['symbol']:10s} {int(r['hour_utc']):>5d} {int(r['n_trades']):>7d} {r['win_rate']:>6.3f} "
              f"{r['pf']:>6.2f} {r['total_dollars']:>+10.2f}")
    print()

    # ---------- 4. Per-window stratification ----------
    print("=" * 110)
    print("4) PER-WINDOW BREAKDOWN @ thr=0.30%")
    print("=" * 110)
    print(f"{'window':10s} {'trades':>7s} {'WR':>6s} {'PF':>6s} {'tot_$':>10s}")
    win_df = stratify(df, ['window'])
    for _, r in win_df.iterrows():
        print(f"{r['window']:10s} {int(r['n_trades']):>7d} {r['win_rate']:>6.3f} {r['pf']:>6.2f} {r['total_dollars']:>+10.2f}")
    print()

    # ---------- 5. Summary: filter to "good" cells ----------
    print("=" * 110)
    print("5) WHAT IF WE FILTER TO PROFITABLE SYMBOLS ONLY?")
    print("=" * 110)
    good_syms = sym_df[sym_df['total_dollars'] > 0]['symbol'].tolist()
    print(f"Profitable symbols: {good_syms}")
    df_good = df[df['symbol'].isin(good_syms)]
    entered_good = df_good[df_good['pred'] > THRESHOLD]
    if len(entered_good) > 0:
        net = entered_good['fwd_ret_3'] - ROUND_TRIP_COST_PCT
        wins = net[net > 0].sum()
        losses = -net[net < 0].sum()
        pf = wins / losses if losses > 0 else 99.0
        print(f"  Trades: {len(entered_good):,}")
        print(f"  WR:     {(net > 0).mean():.3f}")
        print(f"  PF:     {pf:.2f}")
        print(f"  Avg PnL per trade: {net.mean():+.4f}%")
        print(f"  Total PnL: {net.sum():+.2f}% = ${net.sum()/100*POSITION_NOTIONAL:+.2f}")
    print()

    print("=" * 110)
    print("6) WHAT IF WE FILTER TO PROFITABLE HOURS ONLY?")
    print("=" * 110)
    good_hours = hour_df[hour_df['total_dollars'] > 0]['hour_utc'].tolist()
    print(f"Profitable hours (UTC): {sorted(good_hours)}")
    df_gh = df[df['hour_utc'].isin(good_hours)]
    entered_gh = df_gh[df_gh['pred'] > THRESHOLD]
    if len(entered_gh) > 0:
        net = entered_gh['fwd_ret_3'] - ROUND_TRIP_COST_PCT
        wins = net[net > 0].sum()
        losses = -net[net < 0].sum()
        pf = wins / losses if losses > 0 else 99.0
        print(f"  Trades: {len(entered_gh):,}")
        print(f"  WR:     {(net > 0).mean():.3f}")
        print(f"  PF:     {pf:.2f}")
        print(f"  Avg PnL per trade: {net.mean():+.4f}%")
        print(f"  Total PnL: {net.sum():+.2f}% = ${net.sum()/100*POSITION_NOTIONAL:+.2f}")
    print()

    # ---------- 7. Filter to BOTH ----------
    print("=" * 110)
    print("7) FILTER TO good_syms ∩ good_hours")
    print("=" * 110)
    df_both = df[df['symbol'].isin(good_syms) & df['hour_utc'].isin(good_hours)]
    entered_both = df_both[df_both['pred'] > THRESHOLD]
    if len(entered_both) > 0:
        net = entered_both['fwd_ret_3'] - ROUND_TRIP_COST_PCT
        wins = net[net > 0].sum()
        losses = -net[net < 0].sum()
        pf = wins / losses if losses > 0 else 99.0
        print(f"  Trades: {len(entered_both):,}")
        print(f"  WR:     {(net > 0).mean():.3f}")
        print(f"  PF:     {pf:.2f}")
        print(f"  Avg PnL per trade: {net.mean():+.4f}%")
        print(f"  Total PnL: {net.sum():+.2f}% = ${net.sum()/100*POSITION_NOTIONAL:+.2f}")
        # Per-window
        entered_both = entered_both.copy()
        entered_both['net_pnl'] = net
        print(f"\n  Per-window breakdown of filtered strategy:")
        print(f"  {'window':10s} {'trades':>7s} {'WR':>6s} {'PF':>6s} {'tot_$':>10s}")
        for w, grp in entered_both.groupby('window'):
            w_wins = grp[grp['net_pnl'] > 0]['net_pnl'].sum()
            w_losses = -grp[grp['net_pnl'] < 0]['net_pnl'].sum()
            w_pf = w_wins / w_losses if w_losses > 0 else 99.0
            print(f"  {w:10s} {len(grp):>7d} {(grp['net_pnl']>0).mean():>6.3f} {w_pf:>6.2f} "
                  f"{grp['net_pnl'].sum()/100*POSITION_NOTIONAL:>+10.2f}")
    print()

    # ---------- Save ----------
    out = {
        'threshold': THRESHOLD,
        'round_trip_cost_pct': ROUND_TRIP_COST_PCT,
        'position_notional': POSITION_NOTIONAL,
        'per_symbol': sym_df.to_dict(orient='records'),
        'per_hour': hour_df.to_dict(orient='records'),
        'per_symbol_hour_top15': sh_df.head(15).to_dict(orient='records'),
        'per_symbol_hour_bottom5': sh_df.tail(5).to_dict(orient='records'),
        'per_window': win_df.to_dict(orient='records'),
        'good_symbols': good_syms,
        'good_hours': [int(h) for h in good_hours],
    }
    out_path = MODELS_DIR / 'v6_edge_breakdown.json'
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"Saved: {out_path}")


if __name__ == '__main__':
    main()
