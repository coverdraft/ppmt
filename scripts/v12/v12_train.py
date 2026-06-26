"""
v12_train.py — Enhanced V11 with signal gating filters for higher WR.

KEY IMPROVEMENTS vs V11:
  1. Signal confidence filter: only trade when pred > Q85 (long) or < Q15 (short)
  2. RSI_1h filter: skip entries when RSI_1h is extreme (<35 or >65)
  3. MTF alignment gate: require |mtf_alignment| >= 0.33
  4. Direction-trend alignment: long only when trend_1h >= 0, short only when trend_1h <= 0
  5. Stricter quantile configs (Q90/10, Q92/8, Q95/5) for higher selectivity
  6. Cost-aware labels: only label as "win" if return > 2x fees

USAGE:
    python scripts/v12/v12_train.py
    python scripts/v12/v12_train.py --symbol DOGE --horizon 12
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
V11_DIR = DATA_DIR / "v11"
OUTPUT_DIR = DATA_DIR / "v12"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR = OUTPUT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

LOG = logging.getLogger("v12_train")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "v11"))
from v11_build_dataset import ALL_FEATURE_NAMES, DEFAULT_SYMBOLS, DEFAULT_HORIZONS

MAKER_COST_PCT = 0.04
TAKER_COST_PCT = 0.14

# ============================================================================
# SIGNAL GATING FILTERS
# ============================================================================

class SignalGate:
    """Filters that gate whether a signal should be traded."""
    
    def __init__(self, config: dict = None):
        self.config = config or {}
    
    def should_trade(self, row: dict, pred: float, direction: int) -> bool:
        """Check all gate conditions. direction: 1=long, -1=short."""
        # RSI_1h filter: skip extreme RSI
        rsi_1h_min = self.config.get("rsi_1h_min", 35)
        rsi_1h_max = self.config.get("rsi_1h_max", 65)
        rsi_1h = row.get("rsi_1h", 50)
        if not np.isnan(rsi_1h):
            if rsi_1h < rsi_1h_min or rsi_1h > rsi_1h_max:
                return False
        
        # Direction-trend alignment
        if self.config.get("trend_align", False):
            trend_1h = row.get("trend_1h", 0)
            if direction == 1 and trend_1h < 0:
                return False
            if direction == -1 and trend_1h > 0:
                return False
        
        # MTF alignment gate
        if self.config.get("mtf_gate", False):
            mtf = row.get("mtf_alignment", 0)
            if abs(mtf) < 0.33:
                return False
        
        # Volatility regime filter
        vol_regime_max = self.config.get("vol_regime_max", 99)
        vol_regime = row.get("vol_regime_1h", 0)
        if vol_regime > vol_regime_max:
            return False
        
        return True


# ============================================================================
# GATE CONFIGURATIONS TO TEST
# ============================================================================

GATE_CONFIGS = {
    "baseline": {},  # No gates
    "rsi_only": {
        "rsi_1h_min": 35,
        "rsi_1h_max": 65,
    },
    "trend_rsi": {
        "trend_align": True,
        "rsi_1h_min": 35,
        "rsi_1h_max": 65,
    },
    "mtf_rsi": {
        "mtf_gate": True,
        "rsi_1h_min": 35,
        "rsi_1h_max": 65,
    },
    "full_gate": {
        "trend_align": True,
        "mtf_gate": True,
        "rsi_1h_min": 35,
        "rsi_1h_max": 65,
        "vol_regime_max": 2,
    },
}

# ============================================================================
# HP PRESETS (same as V11)
# ============================================================================

HP_PRESETS = {
    "default": {
        "num_leaves": 31,
        "learning_rate": 0.01,
        "min_data_in_leaf": 30,
        "lambda_l1": 0.3,
        "lambda_l2": 3.0,
    },
    "ltf_ultra_reg": {
        "num_leaves": 7,
        "learning_rate": 0.003,
        "min_data_in_leaf": 150,
        "lambda_l1": 5.0,
        "lambda_l2": 15.0,
    },
    "ltf_reg": {
        "num_leaves": 15,
        "learning_rate": 0.005,
        "min_data_in_leaf": 80,
        "lambda_l1": 2.0,
        "lambda_l2": 8.0,
    },
    "more_reg": {
        "num_leaves": 15,
        "learning_rate": 0.01,
        "min_data_in_leaf": 100,
        "lambda_l1": 1.0,
        "lambda_l2": 10.0,
    },
}

SYMBOL_HP = {
    12: "ltf_ultra_reg",
    36: "ltf_reg",
    72: "more_reg",
    288: "default",
}

BASE_PARAMS = {
    "objective": "binary",
    "metric": ["binary_logloss", "auc"],
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "bagging_freq": 3,
    "verbosity": -1,
    "seed": 42,
}

NUM_BOOST_ROUND = 500


def _auc(y_true, y_pred):
    order = np.argsort(-y_pred)
    y_sorted = y_true[order]
    n_pos = y_sorted.sum()
    n_neg = len(y_sorted) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tp = 0.0
    auc = 0.0
    for y in y_sorted:
        if y == 1:
            tp += 1
        else:
            auc += tp
    return auc / (n_pos * n_neg)


def walk_forward_splits(df, n_windows=4, test_frac=0.07):
    """Create walk-forward splits."""
    ts = df["timestamp"].values
    ts_first, ts_last = ts[0], ts[-1]
    span_ms = ts_last - ts_first
    span_days = span_ms / (1000 * 86400)
    test_days = max(span_days * test_frac, 0.5)
    
    splits = []
    for w in range(n_windows):
        offset_ms = int(w * test_days * 86400 * 1000)
        test_end_ts = ts_last - offset_ms
        test_start_ts = test_end_ts - int(test_days * 86400 * 1000)
        
        if test_start_ts <= ts_first:
            break
        
        train_df = df[df["timestamp"] < test_start_ts].reset_index(drop=True)
        test_df = df[(df["timestamp"] >= test_start_ts) & (df["timestamp"] < test_end_ts)].reset_index(drop=True)
        
        if len(train_df) < 500 or len(test_df) < 50:
            continue
        
        splits.append((f"w{w+1}", train_df, test_df))
    
    return splits


def sequential_backtest_gated(
    test_df: pd.DataFrame,
    preds: np.ndarray,
    fwd_col: str,
    q_long: int,
    q_short: int,
    hold_bars: int,
    gate: SignalGate,
    cost_pct: float = MAKER_COST_PCT,
    window_size: int = 200,
) -> dict:
    """Sequential backtest with signal gating."""
    fwd = test_df[fwd_col].values
    
    n_trades = 0
    n_win = 0
    pnl = 0.0
    in_trade = False
    exit_bar = 0
    recent_preds = []
    trade_returns = []
    n_long = 0
    n_short = 0
    n_gated = 0  # trades blocked by gate
    n_long_gated = 0
    n_short_gated = 0
    
    for i in range(len(preds)):
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
        
        direction = 0
        if p_val > q_high:
            direction = 1
        elif p_val < q_low:
            direction = -1
        
        if direction == 0:
            continue
        
        # Apply signal gate
        row = test_df.iloc[i].to_dict()
        if not gate.should_trade(row, p_val, direction):
            n_gated += 1
            if direction == 1:
                n_long_gated += 1
            else:
                n_short_gated += 1
            continue
        
        # Take the trade
        if direction == 1:
            n_long += 1
        else:
            n_short += 1
        
        if not np.isnan(fwd[i]):
            n_trades += 1
            trade_ret = direction * fwd[i] - cost_pct / 100
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
        "n_gated": n_gated,
        "n_long_gated": n_long_gated,
        "n_short_gated": n_short_gated,
        "win_rate": round(win_rate, 4),
        "avg_ret_pct": round(avg_ret * 100, 4),
        "pnl_pct": round(pnl * 100, 4),
        "sharpe": round(sharpe, 4),
        "profit_factor": round(pf, 4),
    }


def train_and_evaluate(
    symbol: str,
    horizon: int,
    gate_name: str,
    gate: SignalGate,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    hp_preset: str = "default",
):
    """Train model and evaluate with signal gate."""
    t0 = time.time()
    
    label_col = f"label_h{horizon}"
    fwd_col = f"fwd_ret_h{horizon}"
    
    train_clean = train_df[train_df[label_col].notna()].reset_index(drop=True)
    test_clean = test_df[test_df[label_col].notna()].reset_index(drop=True)
    
    X_tr = train_clean[ALL_FEATURE_NAMES].values.astype(np.float32)
    y_tr = train_clean[label_col].values.astype(np.float32)
    
    if len(X_tr) < 200:
        return None
    
    # Train
    hp = HP_PRESETS.get(hp_preset, HP_PRESETS["default"])
    params = dict(BASE_PARAMS)
    params.update(hp)
    
    d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=ALL_FEATURE_NAMES, free_raw_data=False)
    bst = lgb.train(params, d_tr, num_boost_round=NUM_BOOST_ROUND,
                     callbacks=[lgb.log_evaluation(period=0)])
    
    # Predict on test
    X_test = test_clean[ALL_FEATURE_NAMES].values.astype(np.float32)
    preds = bst.predict(X_test)
    y_test = test_clean[label_col].values.astype(np.float32)
    
    auc_test = float(_auc(y_test, preds))
    
    # Backtest with different Q configs
    q_configs = [
        (90, 10), (92, 8), (95, 5),  # Stricter for higher WR
        (85, 15), (80, 20),           # Standard
    ]
    backtest_results = {}
    
    for q_long, q_short in q_configs:
        bt = sequential_backtest_gated(
            test_clean, preds, fwd_col,
            q_long, q_short,
            hold_bars=horizon,
            gate=gate,
            cost_pct=MAKER_COST_PCT,
        )
        backtest_results[f"q{q_long}_{q_short}"] = bt
    
    result = {
        "symbol": symbol,
        "horizon": horizon,
        "gate": gate_name,
        "hp_preset": hp_preset,
        "auc_test": auc_test,
        "n_train": len(X_tr),
        "n_test": len(X_test),
        "backtest": backtest_results,
        "train_time_s": float(time.time() - t0),
    }
    
    # Save model
    model_name = f"v12_clf_{symbol}_h{horizon}_{gate_name}"
    model_path = MODELS_DIR / f"{model_name}.txt"
    bst.save_model(str(model_path))
    result["model_path"] = str(model_path)
    
    # Log summary
    best_pnl = max(bt["pnl_pct"] for bt in backtest_results.values())
    best_wr = max(bt["win_rate"] for bt in backtest_results.values() if bt["n_trades"] >= 3)
    LOG.info("  %s H=%d gate=%s: auc=%.3f best_pnl=%+.2f%% best_wr=%.3f",
             symbol, horizon, gate_name, auc_test, best_pnl, best_wr)
    
    return result


def main():
    parser = argparse.ArgumentParser(description="V12 Training with Signal Gates")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--horizon", type=int, default=12)
    args = parser.parse_args()
    
    symbols = [args.symbol] if args.symbol else DEFAULT_SYMBOLS
    horizon = args.horizon
    
    print("=" * 100)
    print("V12 TRAIN — Signal-Gated Low Timeframe Classifier")
    print(f"  Symbols: {symbols}")
    print(f"  Horizon: {horizon} ({horizon * 5 / 60:.0f}h)")
    print(f"  Gate configs: {list(GATE_CONFIGS.keys())}")
    print(f"  Features: {len(ALL_FEATURE_NAMES)}")
    print("=" * 100)
    
    # Load dataset
    dataset_path = V11_DIR / "v11_dataset.parquet"
    LOG.info("Loading dataset: %s", dataset_path)
    df = pd.read_parquet(dataset_path)
    LOG.info("  loaded %d rows", len(df))
    
    all_results = []
    
    for symbol in symbols:
        sym_df = df[df["symbol"] == symbol].copy().reset_index(drop=True)
        if len(sym_df) < 1000:
            continue
        
        hp = SYMBOL_HP.get(horizon, "default")
        label_col = f"label_h{horizon}"
        
        if label_col not in sym_df.columns:
            continue
        
        valid_df = sym_df[sym_df[label_col].notna()].reset_index(drop=True)
        if len(valid_df) < 500:
            continue
        
        splits = walk_forward_splits(valid_df, n_windows=4)
        if len(splits) < 2:
            continue
        
        LOG.info("Training %s H=%d — %d splits, %d rows", symbol, horizon, len(splits), len(valid_df))
        
        # Train once per split, evaluate with all gate configs
        for split_name, train_df, test_df in splits:
            # Train baseline model (same for all gates)
            train_clean = train_df[train_df[label_col].notna()].reset_index(drop=True)
            test_clean = test_df[test_df[label_col].notna()].reset_index(drop=True)
            
            X_tr = train_clean[ALL_FEATURE_NAMES].values.astype(np.float32)
            y_tr = train_clean[label_col].values.astype(np.float32)
            
            if len(X_tr) < 200:
                continue
            
            hp_dict = HP_PRESETS.get(hp, HP_PRESETS["default"])
            params = dict(BASE_PARAMS)
            params.update(hp_dict)
            
            d_tr = lgb.Dataset(X_tr, label=y_tr, feature_name=ALL_FEATURE_NAMES, free_raw_data=False)
            bst = lgb.train(params, d_tr, num_boost_round=NUM_BOOST_ROUND,
                             callbacks=[lgb.log_evaluation(period=0)])
            
            X_test = test_clean[ALL_FEATURE_NAMES].values.astype(np.float32)
            preds = bst.predict(X_test)
            y_test = test_clean[label_col].values.astype(np.float32)
            auc_test = float(_auc(y_test, preds))
            
            fwd_col = f"fwd_ret_h{horizon}"
            
            # Evaluate each gate config
            for gate_name, gate_config in GATE_CONFIGS.items():
                gate = SignalGate(gate_config)
                
                q_configs = [(90, 10), (92, 8), (95, 5), (85, 15), (80, 20)]
                backtest_results = {}
                
                for q_long, q_short in q_configs:
                    bt = sequential_backtest_gated(
                        test_clean, preds, fwd_col,
                        q_long, q_short,
                        hold_bars=horizon,
                        gate=gate,
                        cost_pct=MAKER_COST_PCT,
                    )
                    backtest_results[f"q{q_long}_{q_short}"] = bt
                
                result = {
                    "symbol": symbol,
                    "horizon": horizon,
                    "split": split_name,
                    "gate": gate_name,
                    "hp_preset": hp,
                    "auc_test": auc_test,
                    "n_train": len(X_tr),
                    "n_test": len(X_test),
                    "backtest": backtest_results,
                }
                all_results.append(result)
    
    if not all_results:
        LOG.error("No results generated")
        sys.exit(1)
    
    # Save results
    results_path = OUTPUT_DIR / "v12_results.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    
    # Print summary
    print(f"\n{'='*120}")
    print("V12 TRAINING RESULTS — SIGNAL GATE COMPARISON")
    print(f"{'='*120}")
    
    # Group by (symbol, gate)
    grouped = defaultdict(list)
    for r in all_results:
        grouped[(r["symbol"], r["gate"])].append(r)
    
    print(f"\n{'Symbol':<8} {'Gate':<15} {'AUC':>8} {'Best Q':>8} {'PnL%':>10} {'WR':>8} {'PF':>8} {'Sharpe':>8} {'Trades':>8} {'Gated':>8}")
    print("-" * 120)
    
    for (sym, gate_name), results in sorted(grouped.items()):
        auc_tests = [r["auc_test"] for r in results]
        
        best_pnl = -999
        best_q = ""
        best_wr = 0
        best_pf = 0
        best_sharpe = 0
        best_trades = 0
        best_gated = 0
        
        for r in results:
            for q_name, bt in r["backtest"].items():
                if bt["pnl_pct"] > best_pnl and bt["n_trades"] >= 3:
                    best_pnl = bt["pnl_pct"]
                    best_q = q_name
                    best_wr = bt["win_rate"]
                    best_pf = bt["profit_factor"]
                    best_sharpe = bt["sharpe"]
                    best_trades = bt["n_trades"]
                    best_gated = bt["n_gated"]
        
        print(f"{sym:<8} {gate_name:<15} {np.mean(auc_tests):>8.3f} {best_q:>8} "
              f"{best_pnl:>+9.2f}% {best_wr:>8.3f} {best_pf:>8.2f} "
              f"{best_sharpe:>8.3f} {best_trades:>8d} {best_gated:>8d}")
    
    # Gate comparison across all symbols
    print(f"\n{'='*120}")
    print("GATE EFFECTIVENESS (aggregated across symbols and splits)")
    print(f"{'='*120}")
    
    gate_stats = defaultdict(lambda: {"pnls": [], "wrs": [], "pfs": [], "trades": [], "gated": []})
    for r in all_results:
        for q_name, bt in r["backtest"].items():
            if bt["n_trades"] >= 3:
                gate_stats[r["gate"]]["pnls"].append(bt["pnl_pct"])
                gate_stats[r["gate"]]["wrs"].append(bt["win_rate"])
                gate_stats[r["gate"]]["pfs"].append(bt["profit_factor"])
                gate_stats[r["gate"]]["trades"].append(bt["n_trades"])
                gate_stats[r["gate"]]["gated"].append(bt["n_gated"])
    
    print(f"\n{'Gate':<15} {'Avg PnL%':>10} {'Avg WR':>10} {'Avg PF':>10} {'Avg Trades':>12} {'Avg Gated':>12} {'WR Improvement':>15}")
    print("-" * 90)
    
    baseline_wr = np.mean(gate_stats.get("baseline", {}).get("wrs", [0]))
    for gate_name in ["baseline", "rsi_only", "trend_rsi", "mtf_rsi", "full_gate"]:
        if gate_name not in gate_stats:
            continue
        stats = gate_stats[gate_name]
        avg_wr = np.mean(stats["wrs"])
        wr_imp = avg_wr - baseline_wr
        print(f"{gate_name:<15} {np.mean(stats['pnls']):>+10.2f} {avg_wr:>10.3f} "
              f"{np.mean(stats['pfs']):>10.2f} {np.mean(stats['trades']):>12.1f} "
              f"{np.mean(stats['gated']):>12.1f} {wr_imp:>+15.3f}")
    
    LOG.info("Results saved to %s", results_path)


if __name__ == "__main__":
    main()
