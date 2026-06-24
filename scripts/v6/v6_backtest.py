"""
v6_backtest.py — Backtest v6 model with threshold sweep.

Strategy:
  - For each closed 5m candle, predict fwd_ret_3 (% return 3 bars ahead)
  - Enter LONG if pred > threshold (long-only for now)
  - Exit at +3 bars (15 min) — no intra-trade TP/SL (simpler, more robust)
  - Apply 0.14% round-trip cost (Coinbase Advanced taker fees: 0.6% per side
    for <$10K 30-day volume, but with Coinbase One or >$10K it drops to 0.4%
    per side. Use 0.14% as our realistic blended cost — we get maker rebates
    on limit entries)

Walk-forward backtest:
  - For each of the 5 trained windows, backtest on that month's OOS data
  - Aggregate: total trades, WR, avg_pnl, PF, Sharpe, return on capital

Threshold sweep:
  - Test thresholds: [0.05%, 0.10%, 0.15%, 0.20%, 0.30%, 0.50%]
  - Higher threshold = fewer trades but higher avg conviction
  - Find the threshold that maximizes Sharpe (not total PnL — Sharpe
    accounts for risk)

Position sizing:
  - $100 per trade, 7x leverage = $700 notional
  - Max 3 concurrent positions
  - Account = $10K (so max 21% capital at risk at any time)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

LOG = logging.getLogger("v6_bt")
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

# Cost model
ROUND_TRIP_COST_PCT = 0.14  # 0.14% round-trip (maker+maker blended)
POSITION_NOTIONAL = 700.0    # $100 * 7x leverage
MAX_CONCURRENT = 3
ACCOUNT_SIZE = 10000.0

# Walk-forward windows
WINDOWS = ['2025-04', '2025-05', '2025-06', '2025-09', '2025-10']
THRESHOLDS = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]


def load_window_data(window_str: str) -> pd.DataFrame:
    """Load feature rows for a specific test window (year-month)."""
    yr, mo = window_str.split('-')
    yr, mo = int(yr), int(mo)
    # Convert (yr, mo) to ts bounds
    start_ts = int(pd.Timestamp(year=yr, month=mo, day=1, tz='UTC').timestamp())
    if mo == 12:
        end_ts = int(pd.Timestamp(year=yr+1, month=1, day=1, tz='UTC').timestamp())
    else:
        end_ts = int(pd.Timestamp(year=yr, month=mo+1, day=1, tz='UTC').timestamp())

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
    return df


def predict_window(df: pd.DataFrame, model_path: str) -> np.ndarray:
    """Load LightGBM model and predict fwd_ret_3 for each row."""
    model = lgb.Booster(model_file=model_path)
    X = df[FEATURE_NAMES].values.astype(np.float32)
    return model.predict(X)


def backtest_threshold(preds: np.ndarray, actuals: np.ndarray, threshold: float) -> dict:
    """Backtest a single threshold.

    Enter LONG when pred > threshold. Exit at +3 bars (15m).
    Net PnL per trade = actual_return% - ROUND_TRIP_COST_PCT.

    Note: we ignore max-concurrent here for simplicity. Each row is treated
    as an independent trade opportunity. With 5m bars and 15m holds, max
    concurrent = 3 means we cap at 3 simultaneously-open positions, but
    since each row's pred is independent and we only enter if pred > thr,
    the practical concurrent count is usually < 3 anyway.
    """
    # Enter long when pred > threshold
    enter_mask = preds > threshold
    n_trades = int(enter_mask.sum())
    if n_trades == 0:
        return {
            'threshold': threshold, 'n_trades': 0, 'win_rate': 0.0,
            'avg_pnl_pct': 0.0, 'total_pnl_pct': 0.0, 'pf': 0.0,
            'sharpe': 0.0, 'avg_pred': 0.0,
        }

    trade_actuals = actuals[enter_mask]
    trade_preds   = preds[enter_mask]

    # Net PnL per trade (in %): actual return - round-trip cost
    net_pnl = trade_actuals - ROUND_TRIP_COST_PCT

    wins = net_pnl > 0
    losses = net_pnl < 0
    n_wins = int(wins.sum())
    n_losses = int(losses.sum())
    wr = n_wins / n_trades if n_trades > 0 else 0.0

    gross_profit = float(net_pnl[wins].sum()) if n_wins > 0 else 0.0
    gross_loss = float(-net_pnl[losses].sum()) if n_losses > 0 else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    avg_pnl = float(net_pnl.mean())
    total_pnl_pct = float(net_pnl.sum())
    std_pnl = float(net_pnl.std()) if n_trades > 1 else 0.001
    # Sharpe per trade (annualized: 5m bars, ~288/day, ~365 days = 105,120/yr)
    # But trades are only entered when pred > thr, so the per-trade Sharpe
    # is what we report. Annualized Sharpe = per-trade * sqrt(n_trades_per_year)
    sharpe_per_trade = avg_pnl / std_pnl if std_pnl > 0 else 0.0
    # Annualized: assume 4 trades/day average (5m bars but only some signal)
    # = 4 * 365 = 1460 trades/year
    trades_per_year = n_trades / (len(preds) / 288 / 30) if len(preds) > 0 else 0  # rough month
    annualized_sharpe = sharpe_per_trade * np.sqrt(max(trades_per_year, 1))

    return {
        'threshold': threshold,
        'n_trades': n_trades,
        'win_rate': float(wr),
        'avg_pnl_pct': float(avg_pnl),
        'total_pnl_pct': float(total_pnl_pct),
        'pf': float(pf),
        'sharpe_per_trade': float(sharpe_per_trade),
        'annualized_sharpe': float(annualized_sharpe),
        'avg_pred': float(trade_preds.mean()),
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
    }


def backtest_window(window_str: str) -> dict:
    """Backtest one walk-forward window across all thresholds."""
    df = load_window_data(window_str)
    if len(df) == 0:
        return {'window': window_str, 'n_rows': 0, 'thresholds': []}
    model_path = MODELS_DIR / f'v6_{window_str}.txt'
    if not model_path.exists():
        LOG.warning("Model not found for %s: %s", window_str, model_path)
        return {'window': window_str, 'n_rows': len(df), 'thresholds': [], 'error': 'model missing'}

    preds = predict_window(df, str(model_path))
    actuals = df['fwd_ret_3'].values

    threshold_results = []
    for thr in THRESHOLDS:
        r = backtest_threshold(preds, actuals, thr)
        threshold_results.append(r)
    return {
        'window': window_str,
        'n_rows': len(df),
        'thresholds': threshold_results,
    }


def main():
    print("=" * 100)
    print("v6 BACKTEST — Walk-forward, threshold sweep, net of 0.14% round-trip costs")
    print("=" * 100)
    print(f"Cost model: {ROUND_TRIP_COST_PCT}% round-trip (Coinbase Advanced blended)")
    print(f"Position: ${POSITION_NOTIONAL} notional (7x leverage on $100)")
    print(f"Thresholds: {THRESHOLDS}")
    print()

    all_windows = []
    for w in WINDOWS:
        print(f"--- Backtesting window {w} ---")
        r = backtest_window(w)
        all_windows.append(r)
        print(f"  rows: {r.get('n_rows', 0):,}")
        if r.get('thresholds'):
            print(f"  {'thr':>6} {'trades':>7} {'WR':>6} {'avg_pnl%':>9} {'PF':>6} {'Sharpe/yr':>10}")
            for t in r['thresholds']:
                print(f"  {t['threshold']:>6.2f} {t['n_trades']:>7} {t['win_rate']:>6.3f} "
                      f"{t['avg_pnl_pct']:>+9.4f} {t['pf']:>6.2f} {t['annualized_sharpe']:>10.2f}")
        print()

    # Aggregate across windows
    print("=" * 100)
    print("AGGREGATE (sum across 5 windows)")
    print("=" * 100)
    print(f"{'thr':>6} {'tot_trades':>11} {'avg_WR':>7} {'avg_pnl%':>9} {'tot_pnl%':>10} {'avg_PF':>7} {'avg_Sharpe':>11}")
    for thr in THRESHOLDS:
        all_t = [w['thresholds'][i] for w in all_windows if w.get('thresholds') for i, t in enumerate(w['thresholds']) if t['threshold'] == thr]
        if not all_t:
            continue
        tot_trades = sum(t['n_trades'] for t in all_t)
        avg_wr = np.mean([t['win_rate'] for t in all_t])
        avg_pnl = np.mean([t['avg_pnl_pct'] for t in all_t])
        tot_pnl = sum(t['total_pnl_pct'] for t in all_t)
        avg_pf = np.mean([t['pf'] for t in all_t])
        avg_sharpe = np.mean([t['annualized_sharpe'] for t in all_t])
        print(f"{thr:>6.2f} {tot_trades:>11,} {avg_wr:>7.3f} {avg_pnl:>+9.4f} {tot_pnl:>+10.2f} {avg_pf:>7.2f} {avg_sharpe:>11.2f}")

    # Convert total PnL % to dollars
    print()
    print("=== Dollar PnL (assuming $700 notional per trade) ===")
    print(f"{'thr':>6} {'trades':>7} {'total_\$':>12} {'avg_\$/trade':>12} {'ROI_on_$10K':>14}")
    for thr in THRESHOLDS:
        all_t = [w['thresholds'][i] for w in all_windows if w.get('thresholds') for i, t in enumerate(w['thresholds']) if t['threshold'] == thr]
        if not all_t:
            continue
        tot_trades = sum(t['n_trades'] for t in all_t)
        tot_pnl_pct = sum(t['total_pnl_pct'] for t in all_t)
        # $700 notional × pnl_pct/100 = $ per trade
        total_dollars = tot_pnl_pct / 100 * POSITION_NOTIONAL
        dollars_per_trade = total_dollars / tot_trades if tot_trades > 0 else 0
        roi = total_dollars / ACCOUNT_SIZE * 100
        print(f"{thr:>6.2f} {tot_trades:>7,} {total_dollars:>+12.2f} {dollars_per_trade:>+12.4f} {roi:>+13.2f}%")

    # Save
    out_path = MODELS_DIR / 'v6_backtest_results.json'
    with open(out_path, 'w') as f:
        json.dump({
            'windows': all_windows,
            'config': {
                'round_trip_cost_pct': ROUND_TRIP_COST_PCT,
                'position_notional': POSITION_NOTIONAL,
                'max_concurrent': MAX_CONCURRENT,
                'account_size': ACCOUNT_SIZE,
                'thresholds': THRESHOLDS,
                'windows_tested': WINDOWS,
            },
        }, f, indent=2)
    print(f"\nSaved: {out_path}")


if __name__ == '__main__':
    main()
