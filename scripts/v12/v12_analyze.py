"""
v12_analyze.py — Deep analysis of V11 model predictions to find WR improvement opportunities.

GOALS:
  1. Load V11 dataset + model predictions
  2. Analyze what separates winning vs losing trades
  3. Find optimal filters (MTF alignment, vol regime, time-of-day, signal strength)
  4. Quantify WR improvement per filter
  5. Output recommendations for V12

USAGE:
    python scripts/v12/v12_analyze.py
    python scripts/v12/v12_analyze.py --symbol DOGE --horizon 12
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
V11_DIR = DATA_DIR / "v11"
MODELS_DIR = V11_DIR / "models"
OUTPUT_DIR = DATA_DIR / "v12"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v12_analyze")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
from v11_build_dataset import ALL_FEATURE_NAMES, DEFAULT_SYMBOLS, DEFAULT_HORIZONS

MAKER_COST_PCT = 0.04


def load_model(symbol: str, horizon: int):
    """Load a trained V11 model."""
    model_path = MODELS_DIR / f"v11_clf_{symbol}_h{horizon}.txt"
    if not model_path.exists():
        LOG.error("Model not found: %s", model_path)
        return None
    return lgb.Booster(model_file=str(model_path))


def compute_predictions(df: pd.DataFrame, model, feature_names: list) -> np.ndarray:
    """Compute model predictions."""
    X = df[feature_names].values.astype(np.float32)
    # Handle NaN
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return model.predict(X)


def analyze_signal_strength(preds: np.ndarray, fwd_ret: np.ndarray, n_bins: int = 10):
    """Analyze WR by prediction strength bins."""
    results = []
    pred_nonan = ~np.isnan(preds) & ~np.isnan(fwd_ret)
    p = preds[pred_nonan]
    r = fwd_ret[pred_nonan]
    
    if len(p) < 100:
        return results
    
    # Bin by prediction strength
    bins = np.percentile(p, np.linspace(0, 100, n_bins + 1))
    bins[-1] += 1e-6  # Ensure last bin includes max
    
    for i in range(n_bins):
        mask = (p >= bins[i]) & (p < bins[i + 1])
        if mask.sum() < 10:
            continue
        bin_ret = r[mask]
        wr = (bin_ret > 0).mean()
        avg_ret = bin_ret.mean()
        results.append({
            "bin": i,
            "pred_range": f"{bins[i]:.3f}-{bins[i+1]:.3f}",
            "count": int(mask.sum()),
            "win_rate": float(wr),
            "avg_ret": float(avg_ret * 100),
        })
    
    return results


def analyze_by_filter(df: pd.DataFrame, preds: np.ndarray, fwd_col: str, 
                      filter_col: str, n_bins: int = 5):
    """Analyze WR by a feature filter."""
    results = []
    fwd = df[fwd_col].values
    
    valid = ~np.isnan(preds) & ~np.isnan(fwd)
    if filter_col not in df.columns:
        return results
    
    feat = df[filter_col].values
    p = preds[valid]
    r = fwd[valid]
    f = feat[valid]
    
    if len(f) < 100:
        return results
    
    # Check if binary/categorical
    unique_vals = np.unique(f[~np.isnan(f)])
    if len(unique_vals) <= 5:
        for v in unique_vals:
            mask = f == v
            if mask.sum() < 10:
                continue
            wr = (r[mask] > 0).mean()
            avg_ret = r[mask].mean()
            results.append({
                "filter": filter_col,
                "value": float(v),
                "count": int(mask.sum()),
                "win_rate": float(wr),
                "avg_ret": float(avg_ret * 100),
            })
    else:
        # Quantile bins
        bins = np.percentile(f[~np.isnan(f)], np.linspace(0, 100, n_bins + 1))
        bins[-1] += 1e-6
        for i in range(n_bins):
            mask = (f >= bins[i]) & (f < bins[i + 1])
            if mask.sum() < 10:
                continue
            wr = (r[mask] > 0).mean()
            avg_ret = r[mask].mean()
            results.append({
                "filter": filter_col,
                "value_range": f"{bins[i]:.3f}-{bins[i+1]:.3f}",
                "count": int(mask.sum()),
                "win_rate": float(wr),
                "avg_ret": float(avg_ret * 100),
            })
    
    return results


def backtest_with_filters(
    df: pd.DataFrame,
    preds: np.ndarray,
    fwd_col: str,
    q_long: int = 85,
    q_short: int = 15,
    hold_bars: int = 12,
    cost_pct: float = MAKER_COST_PCT,
    filters: dict = None,
    window_size: int = 200,
) -> dict:
    """Sequential backtest with optional signal filters."""
    fwd = df[fwd_col].values
    
    # Apply filters to mask
    trade_mask = np.ones(len(df), dtype=bool)
    if filters:
        for col, (op, val) in filters.items():
            if col not in df.columns:
                continue
            feat = df[col].values
            if op == ">":
                trade_mask &= feat > val
            elif op == ">=":
                trade_mask &= feat >= val
            elif op == "<":
                trade_mask &= feat < val
            elif op == "<=":
                trade_mask &= feat <= val
            elif op == "==":
                trade_mask &= feat == val
            elif op == "abs<":
                trade_mask &= np.abs(feat) < val
    
    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    recent_preds = []
    trade_returns = []
    n_long = 0
    n_short = 0
    n_filtered = 0
    
    for i in range(len(preds)):
        if not trade_mask[i]:
            n_filtered += 1
            recent_preds.append(preds[i])
            if len(recent_preds) > window_size:
                recent_preds.pop(0)
            continue
            
        p_val = float(preds[i])
        recent_preds.append(p_val)
        if len(recent_preds) > window_size:
            recent_preds.pop(0)
        
        if in_trade:
            if i >= exit_bar:
                in_trade = False
            else:
                continue
        
        if len(recent_preds) < 20:
            continue
        
        q_high = np.percentile(recent_preds, q_long)
        q_low = np.percentile(recent_preds, q_short)
        
        sig = 0
        if p_val > q_high:
            sig = 1
            n_long += 1
        elif p_val < q_low:
            sig = -1
            n_short += 1
        
        if sig != 0 and not np.isnan(fwd[i]):
            n_trades += 1
            trade_ret = sig * fwd[i] - cost_pct / 100
            pnl += trade_ret
            trade_returns.append(trade_ret)
            in_trade = True
            exit_bar = i + hold_bars
            if trade_ret > 0:
                n_win += 1
    
    win_rate = n_win / n_trades if n_trades > 0 else 0
    avg_ret = pnl / n_trades if n_trades > 0 else 0
    sharpe = (np.mean(trade_returns) / np.std(trade_returns)) if len(trade_returns) > 1 else 0
    
    gains = sum(r for r in trade_returns if r > 0)
    losses = abs(sum(r for r in trade_returns if r < 0))
    pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0)
    
    return {
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "n_filtered": n_filtered,
        "win_rate": round(win_rate, 4),
        "avg_ret_pct": round(avg_ret * 100, 4),
        "pnl_pct": round(pnl * 100, 4),
        "sharpe": round(sharpe, 4),
        "profit_factor": round(pf, 4),
    }


def main():
    parser = argparse.ArgumentParser(description="V12 Analysis")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--horizon", type=int, default=12)
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else DEFAULT_SYMBOLS
    horizon = args.horizon
    fwd_col = f"fwd_ret_h{horizon}"
    label_col = f"label_h{horizon}"
    
    print("=" * 90)
    print("V12 ANALYSIS — Finding WR Improvement Opportunities")
    print(f"  Symbols: {symbols}")
    print(f"  Horizon: {horizon} ({horizon * 5 / 60:.0f}h)")
    print("=" * 90)
    
    # Load dataset
    dataset_path = V11_DIR / "v11_dataset.parquet"
    LOG.info("Loading dataset: %s", dataset_path)
    df = pd.read_parquet(dataset_path)
    LOG.info("  loaded %d rows", len(df))
    
    all_findings = []
    
    for symbol in symbols:
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        if len(sym_df) < 1000:
            continue
        
        # Load model
        model = load_model(symbol, horizon)
        if model is None:
            continue
        
        LOG.info("Analyzing %s H=%d (%d rows)", symbol, horizon, len(sym_df))
        
        # Compute predictions
        preds = compute_predictions(sym_df, model, ALL_FEATURE_NAMES)
        
        # Get forward returns
        fwd = sym_df[fwd_col].values
        
        # 1. Signal Strength Analysis
        print(f"\n{'='*70}")
        print(f"SIGNAL STRENGTH ANALYSIS — {symbol}")
        print(f"{'='*70}")
        strength = analyze_signal_strength(preds, fwd)
        for s in strength:
            marker = " ***" if s["win_rate"] > 0.6 else ""
            print(f"  Bin {s['bin']}: pred={s['pred_range']:>20s}  "
                  f"n={s['count']:>5d}  WR={s['win_rate']:.3f}  "
                  f"avg_ret={s['avg_ret']:+.3f}%{marker}")
        
        # 2. MTF Alignment Filter
        print(f"\n{'='*70}")
        print(f"MTF ALIGNMENT FILTER — {symbol}")
        print(f"{'='*70}")
        
        # Test different MTF filter approaches
        mtf_configs = [
            ("No filter", None),
            ("mtf_alignment != 0", {"mtf_alignment": ("==", 0)}),  # neutral = skip
            ("|mtf_alignment| >= 1", {"mtf_alignment": ("abs<", 1)}),  # skip if no alignment
        ]
        
        # Check mtf_alignment values
        if "mtf_alignment" in sym_df.columns:
            mtf_vals = sym_df["mtf_alignment"].value_counts().sort_index()
            print(f"  mtf_alignment distribution: {dict(mtf_vals)}")
        
        # 3. Volatility Regime Filter
        print(f"\n{'='*70}")
        print(f"VOLATILITY REGIME FILTER — {symbol}")
        print(f"{'='*70}")
        
        vol_filters = ["vol_regime", "vol_regime_15m", "vol_regime_1h", "atr_pct"]
        for vf in vol_filters:
            if vf not in sym_df.columns:
                continue
            analysis = analyze_by_filter(sym_df, preds, fwd_col, vf)
            if analysis:
                print(f"\n  {vf}:")
                for a in analysis:
                    vr = a.get("value_range", str(a.get("value", "")))
                    print(f"    {vr:>20s}  n={a['count']:>5d}  "
                          f"WR={a['win_rate']:.3f}  avg_ret={a['avg_ret']:+.3f}%")
        
        # 4. RSI Filter (top feature)
        print(f"\n{'='*70}")
        print(f"RSI FILTER — {symbol}")
        print(f"{'='*70}")
        
        for rsi_col in ["rsi_14", "rsi_15m", "rsi_1h"]:
            if rsi_col not in sym_df.columns:
                continue
            analysis = analyze_by_filter(sym_df, preds, fwd_col, rsi_col)
            if analysis:
                print(f"\n  {rsi_col}:")
                for a in analysis:
                    vr = a.get("value_range", str(a.get("value", "")))
                    marker = " ***" if a["win_rate"] > 0.6 else ""
                    print(f"    {vr:>20s}  n={a['count']:>5d}  "
                          f"WR={a['win_rate']:.3f}  avg_ret={a['avg_ret']:+.3f}%{marker}")
        
        # 5. Trend Strength Filter
        print(f"\n{'='*70}")
        print(f"TREND FILTER — {symbol}")
        print(f"{'='*70}")
        
        for trend_col in ["trend_1h", "trend_15m", "trend_50", "trending"]:
            if trend_col not in sym_df.columns:
                continue
            analysis = analyze_by_filter(sym_df, preds, fwd_col, trend_col)
            if analysis:
                print(f"\n  {trend_col}:")
                for a in analysis:
                    vr = a.get("value_range", str(a.get("value", "")))
                    print(f"    {vr:>20s}  n={a['count']:>5d}  "
                          f"WR={a['win_rate']:.3f}  avg_ret={a['avg_ret']:+.3f}%")
        
        # 6. Hour-of-day filter
        print(f"\n{'='*70}")
        print(f"HOUR-OF-DAY FILTER — {symbol}")
        print(f"{'='*70}")
        
        if "timestamp" in sym_df.columns:
            hours = pd.to_datetime(sym_df["timestamp"], unit="ms").dt.hour
            sym_df["hour"] = hours
            analysis = analyze_by_filter(sym_df, preds, fwd_col, "hour")
            best_hours = []
            for a in analysis:
                marker = " ***" if a["win_rate"] > 0.58 else ""
                val = a.get("value", a.get("value_range", "?"))
                val_str = f"{int(val):>2d}" if isinstance(val, (int, float)) and not isinstance(val, bool) else str(val)
                print(f"    Hour {val_str}  n={a['count']:>5d}  "
                      f"WR={a['win_rate']:.3f}  avg_ret={a['avg_ret']:+.3f}%{marker}")
                if a["win_rate"] > 0.55 and isinstance(val, (int, float)):
                    best_hours.append(int(val))
            all_findings.append({
                "symbol": symbol,
                "best_hours": best_hours,
            })
        
        # 7. Comprehensive filter combination backtest
        print(f"\n{'='*70}")
        print(f"COMBINED FILTER BACKTEST — {symbol} H={horizon}")
        print(f"{'='*70}")
        
        # Define filter combinations to test
        filter_configs = [
            ("Baseline (no filter)", None),
            ("Trend aligned (trend_1h != 0)", {"trend_1h": ("==", 0)}),  # skip neutral trend
            ("MTF aligned (|mtf|>=1)", {"mtf_alignment": ("abs<", 1)}),  # skip if no MTF alignment
        ]
        
        # Add RSI filter if available
        if "rsi_1h" in sym_df.columns:
            rsi_q30 = sym_df["rsi_1h"].quantile(0.30)
            rsi_q70 = sym_df["rsi_1h"].quantile(0.70)
            filter_configs.append(
                (f"RSI not extreme (>{rsi_q30:.0f} <{rsi_q70:.0f})", 
                 {"rsi_1h": ("<", rsi_q30)})  # skip low RSI
            )
        
        # Add vol_regime_1h filter
        if "vol_regime_1h" in sym_df.columns:
            # Find best vol regime
            vr_analysis = analyze_by_filter(sym_df, preds, fwd_col, "vol_regime_1h")
            if vr_analysis:
                best_vr = max(vr_analysis, key=lambda x: x["win_rate"])
                worst_vr = min(vr_analysis, key=lambda x: x["win_rate"])
                if best_vr["win_rate"] > worst_vr["win_rate"] + 0.05:
                    # Skip worst vol regime
                    worst_val = worst_vr.get("value", 0)
                    filter_configs.append(
                        (f"Skip vol_regime_1h={worst_val}", 
                         {"vol_regime_1h": ("==", worst_val)})
                    )
        
        for name, filters in filter_configs:
            # Note: filters are EXCLUSION filters (skip when condition is TRUE)
            # We need to invert the logic
            bt = backtest_with_filters(sym_df, preds, fwd_col, 
                                        q_long=85, q_short=15,
                                        hold_bars=horizon,
                                        filters=filters)
            marker = " ***" if bt["win_rate"] > 0.6 else ""
            print(f"  {name:<45s}  trades={bt['n_trades']:>4d}  "
                  f"WR={bt['win_rate']:.3f}  PnL={bt['pnl_pct']:+.2f}%  "
                  f"PF={bt['profit_factor']:.2f}  Sharpe={bt['sharpe']:.3f}{marker}")
    
    # Save findings
    findings_path = OUTPUT_DIR / "v12_analysis_findings.json"
    with open(findings_path, "w") as f:
        json.dump(all_findings, f, indent=2, default=str)
    
    print(f"\n{'='*90}")
    print("ANALYSIS COMPLETE")
    print(f"Findings saved to: {findings_path}")
    print(f"{'='*90}")


if __name__ == "__main__":
    main()
