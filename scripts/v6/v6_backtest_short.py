"""
v6_backtest_short.py — Add SHORT side to the v6 backtest.

Idea: if the model predicts fwd_ret_3 < -threshold, the model expects
the price to DROP. So we should be able to enter SHORT and profit from
the drop. This should roughly double trade count and improve Sharpe
(uncorrelated signals on both sides of the book).

Math:
  - LONG:  enter when pred > +thr.  PnL = +fwd_ret_3 - cost
  - SHORT: enter when pred < -thr.  PnL = -fwd_ret_3 - cost  (we profit when ret is negative)

For Coinbase Advanced:
  - Spot: SHORT via margin borrow. Borrow fee ~0.05% / 15min = negligible.
  - Perps (Coinbase Perps): SHORT is native, funding rate ~0.01%/8h = negligible.
  - We assume SHORT side has same 0.14% round-trip cost as LONG.

Filter:
  - Apply same hours filter (k=12, walk-forward) to both sides
  - Also test: maybe SHORT works in DIFFERENT hours than LONG (asymmetric)
  - For simplicity in v1: use same filter, evaluate both sides

Outputs:
  - LONG-only, SHORT-only, LONG+SHORT combined
  - Per-window breakdown
  - Save: data/v6_models/v6_short_backtest.json
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

LOG = logging.getLogger("v6_short")
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

WINDOWS = ['2025-04', '2025-05', '2025-06', '2025-09', '2025-10']
K_HOURS = 12  # use the robust k=12 from v6_backtest_filtered.py


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


def pick_top_hours(prior_dfs: list[pd.DataFrame], k: int, side: str = 'long') -> list[int]:
    """Pick top-K hours by total PnL on the chosen side."""
    if not prior_dfs or k >= 24:
        return list(range(24))
    prior = pd.concat(prior_dfs, ignore_index=True)
    if side == 'long':
        entered = prior[prior['pred'] > THRESHOLD].copy()
        entered['net'] = entered['fwd_ret_3'] - ROUND_TRIP_COST_PCT
    else:  # short
        entered = prior[prior['pred'] < -THRESHOLD].copy()
        entered['net'] = -entered['fwd_ret_3'] - ROUND_TRIP_COST_PCT
    if len(entered) == 0:
        return list(range(24))
    by_hour = entered.groupby('hour_utc')['net'].sum().sort_values(ascending=False)
    return sorted(by_hour.head(k).index.tolist())


def backtest_side(df: pd.DataFrame, hours: list[int], side: str) -> dict:
    """Backtest one side (long or short) on df filtered to hours."""
    fdf = df[df['hour_utc'].isin(hours)]
    if side == 'long':
        entered = fdf[fdf['pred'] > THRESHOLD].copy()
        entered['net'] = entered['fwd_ret_3'] - ROUND_TRIP_COST_PCT
    else:  # short
        entered = fdf[fdf['pred'] < -THRESHOLD].copy()
        entered['net'] = -entered['fwd_ret_3'] - ROUND_TRIP_COST_PCT
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
    print("v6 LONG+SHORT BACKTEST — both sides, walk-forward k=12 hours filter")
    print("=" * 110)
    print(f"LONG:  enter when pred > +{THRESHOLD}%,  PnL = +fwd_ret_3 - {ROUND_TRIP_COST_PCT}%")
    print(f"SHORT: enter when pred < -{THRESHOLD}%,  PnL = -fwd_ret_3 - {ROUND_TRIP_COST_PCT}%")
    print(f"Hours filter: k={K_HOURS} top hours per side, chosen walk-forward from prior windows")
    print(f"Windows: {WINDOWS}")
    print()

    # Load all 5 windows
    print("Loading + predicting all windows...")
    windows_data = {}
    for w in WINDOWS:
        df = load_window_data(w)
        model_path = MODELS_DIR / f'v6_{w}.txt'
        if not model_path.exists():
            continue
        df['pred'] = predict_window(df, str(model_path))
        windows_data[w] = df
        print(f"  {w}: {len(df):,} rows, pred mean={df['pred'].mean():+.4f}%, "
              f"pred>thr={(df['pred']>THRESHOLD).sum():,}, pred<-thr={(df['pred']<-THRESHOLD).sum():,}")
    print()

    # Walk-forward: pick hours filter per side using prior windows
    print("=" * 110)
    print("WALK-FORWARD RESULTS (per window)")
    print("=" * 110)
    per_window_results = []
    for i, w in enumerate(WINDOWS):
        if w not in windows_data:
            continue
        df_test = windows_data[w]
        prior_dfs = [windows_data[WINDOWS[j]] for j in range(i) if WINDOWS[j] in windows_data]

        # Pick hours separately for each side (asymmetric)
        long_hours = pick_top_hours(prior_dfs, K_HOURS, side='long')
        short_hours = pick_top_hours(prior_dfs, K_HOURS, side='short')

        long_r = backtest_side(df_test, long_hours, 'long')
        short_r = backtest_side(df_test, short_hours, 'short')

        # Combined: just sum
        combined = {
            'n_trades': long_r['n_trades'] + short_r['n_trades'],
            'tot_dollars': long_r['tot_dollars'] + short_r['tot_dollars'],
            'tot_pnl_pct': long_r['tot_pnl_pct'] + short_r['tot_pnl_pct'],
        }
        # Combined WR/PF: weight by trade count
        n_tot = long_r['n_trades'] + short_r['n_trades']
        if n_tot > 0:
            combined['wr'] = (long_r['wr'] * long_r['n_trades'] + short_r['wr'] * short_r['n_trades']) / n_tot
            # PF: sum gross_profit / sum gross_loss — approximate
            long_gp = long_r['tot_dollars'] * long_r['wr'] if long_r['n_trades'] > 0 else 0
            long_gl = long_r['tot_dollars'] * (1 - long_r['wr']) if long_r['n_trades'] > 0 else 0
            short_gp = short_r['tot_dollars'] * short_r['wr'] if short_r['n_trades'] > 0 else 0
            short_gl = short_r['tot_dollars'] * (1 - short_r['wr']) if short_r['n_trades'] > 0 else 0
            # NOTE: PF calc here is rough (uses signed dollars, not gross). For accuracy would need to recompute.
            # We'll report LONG-only and SHORT-only PF accurately, combined PF is approximate.
            combined['pf'] = (long_gp + short_gp) / max(-(long_r['tot_dollars'] - long_gp) - (short_r['tot_dollars'] - short_gp), 1)
            # Sharpe: combined net = concat of long_net and short_net (approximate via trade-count-weighted mean/std)
            # Rough: combined sharpe = (mean_L*n_L + mean_S*n_S)/n_tot / sqrt(var combine)
            # For simplicity, just average
            combined['sharpe_ann'] = (long_r['sharpe_ann'] * long_r['n_trades'] + short_r['sharpe_ann'] * short_r['n_trades']) / n_tot if n_tot > 0 else 0
        else:
            combined['wr'] = 0.0
            combined['pf'] = 0.0
            combined['sharpe_ann'] = 0.0

        combined['window'] = w
        combined['long_hours'] = long_hours
        combined['short_hours'] = short_hours
        long_r['window'] = w
        short_r['window'] = w
        per_window_results.append({
            'window': w, 'long': long_r, 'short': short_r, 'combined': combined,
        })

        print(f"\n--- {w} ---")
        print(f"  LONG  hours={long_hours}")
        print(f"        trades={long_r['n_trades']:>5}, WR={long_r['wr']:.3f}, PF={long_r['pf']:.2f}, "
              f"tot=${long_r['tot_dollars']:+.2f}, Sharpe={long_r['sharpe_ann']:+.2f}")
        print(f"  SHORT hours={short_hours}")
        print(f"        trades={short_r['n_trades']:>5}, WR={short_r['wr']:.3f}, PF={short_r['pf']:.2f}, "
              f"tot=${short_r['tot_dollars']:+.2f}, Sharpe={short_r['sharpe_ann']:+.2f}")
        print(f"  COMBINED: trades={combined['n_trades']:>5}, tot=${combined['tot_dollars']:+.2f}, "
              f"WR~{combined['wr']:.3f}")

    # Aggregate
    print()
    print("=" * 110)
    print("AGGREGATE (5 windows)")
    print("=" * 110)
    agg_long = {
        'trades': sum(r['long']['n_trades'] for r in per_window_results),
        'dollars': sum(r['long']['tot_dollars'] for r in per_window_results),
        'avg_wr': np.mean([r['long']['wr'] for r in per_window_results if r['long']['n_trades'] > 0]),
        'avg_pf': np.mean([r['long']['pf'] for r in per_window_results if r['long']['n_trades'] > 0]),
        'avg_sharpe': np.mean([r['long']['sharpe_ann'] for r in per_window_results if r['long']['n_trades'] > 0]),
    }
    agg_short = {
        'trades': sum(r['short']['n_trades'] for r in per_window_results),
        'dollars': sum(r['short']['tot_dollars'] for r in per_window_results),
        'avg_wr': np.mean([r['short']['wr'] for r in per_window_results if r['short']['n_trades'] > 0]),
        'avg_pf': np.mean([r['short']['pf'] for r in per_window_results if r['short']['n_trades'] > 0]),
        'avg_sharpe': np.mean([r['short']['sharpe_ann'] for r in per_window_results if r['short']['n_trades'] > 0]),
    }
    agg_combined = {
        'trades': agg_long['trades'] + agg_short['trades'],
        'dollars': agg_long['dollars'] + agg_short['dollars'],
        'roi_pct': (agg_long['dollars'] + agg_short['dollars']) / ACCOUNT_SIZE * 100,
    }
    print(f"  {'side':10s} {'trades':>7s} {'dollars':>12s} {'avg_WR':>7s} {'avg_PF':>7s} {'avg_Sharpe':>11s}")
    print(f"  {'LONG':10s} {agg_long['trades']:>7d} {agg_long['dollars']:>+12.2f} {agg_long['avg_wr']:>7.3f} {agg_long['avg_pf']:>7.2f} {agg_long['avg_sharpe']:>+11.2f}")
    print(f"  {'SHORT':10s} {agg_short['trades']:>7d} {agg_short['dollars']:>+12.2f} {agg_short['avg_wr']:>7.3f} {agg_short['avg_pf']:>7.2f} {agg_short['avg_sharpe']:>+11.2f}")
    print(f"  {'COMBINED':10s} {agg_combined['trades']:>7d} {agg_combined['dollars']:>+12.2f} ({agg_combined['roi_pct']:+.2f}% ROI)")

    # Comparison vs LONG-only baseline (k=12 from prior commit)
    print()
    print("=" * 110)
    print("COMPARISON vs PRIOR BASELINES")
    print("=" * 110)
    print(f"  LONG-only, no filter:     \$+872 / 5mo  (+8.72% ROI), 1457 trades  [v6_backtest.py]")
    print(f"  LONG-only, k=12 filter:   \$+1139 / 5mo (+11.39% ROI), 1020 trades [v6_backtest_filtered.py]")
    print(f"  LONG+SHORT, k=12 filter:  \${agg_combined['dollars']:+.0f} / 5mo ({agg_combined['roi_pct']:+.2f}% ROI), {agg_combined['trades']} trades [THIS]")
    if agg_long['dollars'] > 0:
        print(f"  SHORT contribution:       \${agg_short['dollars']:+.0f} ({agg_short['dollars']/agg_long['dollars']*100:+.1f}% of LONG)")
    print()

    # Verdict
    print("=" * 110)
    print("VERDICT")
    print("=" * 110)
    if agg_short['dollars'] > 0 and agg_short['avg_wr'] > 0.50:
        print(f"  ✓ SHORT side is profitable ({agg_short['avg_wr']:.1%} WR, +\${agg_short['dollars']:.0f})")
        print(f"  ✓ Adding SHORT {'IMPROVES' if agg_combined['dollars'] > 1139 else 'DOES NOT IMPROVE'} total PnL vs LONG-only-filtered")
        print(f"  ✓ SHORT avg Sharpe = {agg_short['avg_sharpe']:+.2f} ({'positive' if agg_short['avg_sharpe'] > 0 else 'NEGATIVE — short side is noise'})")
    else:
        print(f"  ✗ SHORT side is NOT profitable ({agg_short['avg_wr']:.1%} WR, {agg_short['dollars']:+.0f})")
        print(f"  ✗ The model's directional signal is asymmetric — it can call LONGS but not SHORTS")
        print(f"  → Stick with LONG-only strategy")
    print()

    # Save
    out = {
        'config': {
            'threshold': THRESHOLD,
            'round_trip_cost_pct': ROUND_TRIP_COST_PCT,
            'position_notional': POSITION_NOTIONAL,
            'account_size': ACCOUNT_SIZE,
            'windows': WINDOWS,
            'k_hours': K_HOURS,
        },
        'per_window': per_window_results,
        'aggregate': {
            'long': agg_long, 'short': agg_short, 'combined': agg_combined,
        },
    }
    out_path = MODELS_DIR / 'v6_short_backtest.json'
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"Saved: {out_path}")


if __name__ == '__main__':
    main()
