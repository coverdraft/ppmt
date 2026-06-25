"""
v7_online_simulate.py — F8 Layer 1 simulation: backtest with trie online.

WHAT THIS DOES
--------------
Re-runs the v7.5 walk-forward backtest, but this time simulating the
LIVE online learning loop described in master plan §6.1:

  For each candle (sorted by ts):
    1. If there's a pending prediction from 15m ago → commit_outcome
    2. Lookup_pattern(features) → get trie feedback (mean_outcome, n_obs)
    3. LightGBM predicts pred_long
    4. Ensemble: final_pred = 0.8 * lgb_pred + 0.2 * trie_mean (if n_obs >= 5)
    5. Apply decision rule (LONG / SHORT / WAIT)
    6. predict_and_record(features, ts, symbol) → buffer for commit in 15m
    7. Compute PnL on this candle

The walk-forward setup is identical to v7_backtest_v75.py — model for
window W is trained on data BEFORE W, so there's no train/test leakage.
The trie is reset at the start of each window (since training data is
already in the LightGBM model — trie should learn only from OOS outcomes
during the window itself).

OUTPUTS
-------
  - data/v7_models/v75/v75_online_summary.json
  - data/v7_models/v75/v75_online_trades_{window}.parquet
  - data/v7_models/v75/v75_online_equity_curve.parquet
  - Compares metrics vs static v7.5 backtest

USAGE
-----
    python scripts/v7/v7_online_simulate.py
    python scripts/v7/v7_online_simulate.py --thr-long 0.20 --thr-short 0.50 --model-suffix best
    python scripts/v7/v7_online_simulate.py --trie-weight 0.30 --trie-min-obs 10
"""
from __future__ import annotations

# === Auto-detected project root (portable paths) ===
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
# === End path setup ===

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import lightgbm as lgb

# Import the OnlineTrie and ensemble helper
sys.path.insert(0, _PROJECT_ROOT_STR + "/scripts/v7")
from v7_trie_online import (
    OnlineTrie,
    ensemble_prediction,
    PRUNE_EVERY_N_INSERTS,
)
from v7_backtest_v75 import (
    FEATURE_NAMES,
    LABEL,
    ROUND_TRIP_COST_PCT,
    POSITION_NOTIONAL,
    ACCOUNT_SIZE,
    WF_WINDOWS,
    MODELS_DIR,
    PARQUET_PATH,
    load_window_data,
    compute_metrics,
)

LOG = logging.getLogger("v75_online")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

OUTPUT_DIR = MODELS_DIR

# Forward window for trie outcome commit (must match LABEL = fwd_ret_3)
# fwd_ret_3 = forward return 3 candles ahead = 15 minutes (3 × 5m)
OUTCOME_DELAY_SECONDS = 15 * 60  # 15 minutes


def commit_pending(
    trie: OnlineTrie,
    outcome_lookup: Dict[Tuple[int, str], float],
    current_ts: float,
) -> int:
    """Commit all pending predictions whose outcome is now known.

    A prediction made at ts_p is committable at current_ts >= ts_p + 15m,
    because fwd_ret_3 (15m forward return) is known by then.

    outcome_lookup must be a pre-built dict {(ts_int, symbol): fwd_ret_3}
    covering the entire simulation period.

    Returns number of entries committed.
    """
    if not trie.pending:
        return 0
    cutoff = current_ts - OUTCOME_DELAY_SECONDS
    pending_ts_to_commit = [ts for ts in trie.pending.keys() if ts <= cutoff]
    if not pending_ts_to_commit:
        return 0

    n_committed = 0
    for ts_p in pending_ts_to_commit:
        # Get all entries pending at this ts (could be multiple symbols)
        entries = trie.pending.get(ts_p, [])
        # Commit per-symbol (each has its own outcome)
        for _key, sym, _feat in entries:
            outcome = outcome_lookup.get((int(ts_p), sym))
            if outcome is None or (isinstance(outcome, float) and np.isnan(outcome)):
                continue
            trie.commit_outcome(ts_p, outcome, symbol=sym, current_ts=current_ts)
            n_committed += 1
    return n_committed


def simulate_window(
    df_w: pd.DataFrame,
    outcome_lookup: Dict[Tuple[int, str], float],
    model_path: str,
    thr_long: float,
    thr_short: float,
    trie_weight: float = 0.2,
    trie_min_obs: int = 5,
    n_bins: int = 5,
    half_life_hours: float = 24.0,
    max_nodes: int = 2_000_000,
    prefit_bins: Optional[np.ndarray] = None,
) -> Tuple[pd.DataFrame, OnlineTrie, Dict]:
    """Simulate online learning on one walk-forward window.

    Steps (in chronological order per candle):
      1. Commit pending outcomes (those whose fwd_ret_3 is now known)
      2. Lookup pattern in trie
      3. LightGBM predict
      4. Ensemble: 0.8*lgb + 0.2*trie_mean (if n_obs >= trie_min_obs)
      5. Decision: LONG / SHORT / WAIT
      6. predict_and_record → buffer for future commit
      7. Compute PnL
    """
    # Sort by ts (and symbol for stable order)
    df_w = df_w.sort_values(["ts", "symbol"]).reset_index(drop=True)

    # Load model once
    model = lgb.Booster(model_file=model_path)

    # Initialize trie with high max_nodes to avoid LRU eviction during sim
    trie = OnlineTrie(
        n_bins=n_bins,
        half_life_hours=half_life_hours,
        max_nodes=max_nodes,
    )
    if prefit_bins is not None:
        trie.bin_edges = prefit_bins
        trie.n_features = prefit_bins.shape[0]
    else:
        # Fit bins on the first 5K rows of THIS window (acceptable: bin fitting
        # is unsupervised quantization, not supervised leakage).
        n_fit = min(len(df_w), 5000)
        X_fit = df_w[FEATURE_NAMES].iloc[:n_fit].values.astype(np.float64)
        X_fit = np.nan_to_num(X_fit, nan=0.0, posinf=0.0, neginf=0.0)
        trie.fit_bins(X_fit)

    # Pre-compute predictions for ALL candles in the window.
    # Note: LightGBM prediction is stateless (the model itself doesn't update
    # during the window — only the trie does), so the lgb_pred is the same
    # whether we predict all-at-once or one-by-one. The trie feedback is what
    # we apply online.
    X_all = df_w[FEATURE_NAMES].values.astype(np.float32)
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=0.0, neginf=0.0)
    LOG.info("  predicting %d rows with LightGBM...", len(X_all))
    lgb_preds_all = model.predict(X_all)

    # Loop in chronological order
    final_preds = np.zeros(len(df_w), dtype=np.float64)
    n_trie_hits = 0
    n_committed = 0

    # ts is now datetime (after main()'s conversion). We need int unix seconds
    # for outcome_lookup keys (which were built with int keys).
    ts_int_arr = df_w["ts"].astype("int64").values // 10**9  # nanoseconds → seconds
    # Actually, pd datetime values when cast to int64 give nanoseconds since epoch.
    # Convert to seconds (the unit our outcome_lookup uses).
    # The .astype("int64") on a datetime64[ns, UTC] series gives ns since epoch.
    ts_int_arr = ts_int_arr.astype(np.int64)
    sym_arr = df_w["symbol"].values

    LOG.info("  starting online loop (%d iterations)...", len(df_w))
    progress_step = max(len(df_w) // 10, 1)

    for i in range(len(df_w)):
        ts_i = float(ts_int_arr[i])
        sym_i = str(sym_arr[i])
        feat_i = X_all[i]

        # 1. Commit any pending outcomes now knowable (uses pre-built dict)
        if trie.pending:
            committed = commit_pending(trie, outcome_lookup, ts_i)
            n_committed += committed

        # 2. Lookup pattern in trie
        trie_mean, _trie_std, trie_n, _eff = trie.lookup_pattern(feat_i)

        # 3. LightGBM pred (already computed)
        lgb_pred = float(lgb_preds_all[i])

        # 4. Ensemble
        if trie_n >= trie_min_obs:
            final_pred = ensemble_prediction(
                lgb_pred, trie_mean, trie_n,
                trie_min_obs=trie_min_obs,
                trie_weight=trie_weight,
            )
            n_trie_hits += 1
        else:
            final_pred = lgb_pred
        final_preds[i] = final_pred

        # 5. predict_and_record → buffer
        trie.predict_and_record(feat_i, ts_i, sym_i)

        if (i + 1) % progress_step == 0:
            LOG.info("    %d/%d (%.0f%%) — trie_hits=%d, committed=%d, nodes=%d",
                     i + 1, len(df_w), 100 * (i + 1) / len(df_w),
                     n_trie_hits, n_committed, len(trie.nodes))

    # Build trades DataFrame using final_preds
    df_w = df_w.copy()
    df_w["pred"] = final_preds
    df_w["lgb_pred"] = lgb_preds_all
    df_w["side"] = "WAIT"
    df_w.loc[df_w["pred"] > thr_long, "side"] = "LONG"
    df_w.loc[df_w["pred"] < -thr_short, "side"] = "SHORT"

    df_w["pnl_pct"] = 0.0
    long_mask = df_w["side"] == "LONG"
    short_mask = df_w["side"] == "SHORT"
    df_w.loc[long_mask, "pnl_pct"] = df_w.loc[long_mask, LABEL] - ROUND_TRIP_COST_PCT
    df_w.loc[short_mask, "pnl_pct"] = -df_w.loc[short_mask, LABEL] - ROUND_TRIP_COST_PCT

    trades = df_w[df_w["side"] != "WAIT"].copy()
    trades["pnl_dollars"] = trades["pnl_pct"] / 100 * POSITION_NOTIONAL

    meta = {
        "n_trie_hits": int(n_trie_hits),
        "n_committed": int(n_committed),
        "trie_stats": trie.stats(),
        "trie_weight": trie_weight,
        "trie_min_obs": trie_min_obs,
        "n_bins": n_bins,
        "half_life_hours": half_life_hours,
    }
    return trades, trie, meta


def main():
    from typing import Optional  # local import to avoid top-level clutter

    parser = argparse.ArgumentParser()
    parser.add_argument("--thr-long", type=float, default=0.20,
                        help="LONG threshold (pred > thr_long → LONG). Default 0.20%")
    parser.add_argument("--thr-short", type=float, default=0.50,
                        help="SHORT threshold (pred < -thr_short → SHORT). Default 0.50%")
    parser.add_argument("--model-suffix", type=str, default="best",
                        help="Model file suffix. 'best'=tuned (v75_best_{window}.txt)")
    parser.add_argument("--trie-weight", type=float, default=0.20,
                        help="Weight on trie mean in ensemble (1-weight goes to LGB). Default 0.20")
    parser.add_argument("--trie-min-obs", type=int, default=5,
                        help="Min observations in trie to use ensemble (else pure LGB). Default 5")
    parser.add_argument("--n-bins", type=int, default=5,
                        help="Number of quantile bins per feature for trie keys. Default 5")
    parser.add_argument("--half-life-hours", type=float, default=24.0,
                        help="Trie time-decay half-life in hours. Default 24")
    args = parser.parse_args()

    print("=" * 110)
    print("v7.5 ONLINE SIMULATION — F8 Layer 1 (trie insert-after-predict)")
    suffix_str = f" [models: v75_{args.model_suffix}_*]"
    print(f"  features: {len(FEATURE_NAMES)}   decision: pred>+{args.thr_long}% → LONG, pred<-{args.thr_short}% → SHORT")
    print(f"  ensemble: 0.8*lgb + 0.2*trie_mean (min_obs={args.trie_min_obs}, weight={args.trie_weight})")
    print(f"  trie: n_bins={args.n_bins}, half_life={args.half_life_hours}h")
    print(f"  cost: {ROUND_TRIP_COST_PCT}% round-trip   position: ${POSITION_NOTIONAL}   account: ${ACCOUNT_SIZE}")
    print(f"  walk-forward windows: {WF_WINDOWS}{suffix_str}")
    print("=" * 110)

    if not PARQUET_PATH.exists():
        LOG.error("Parquet missing: %s", PARQUET_PATH)
        LOG.error("Run: python scripts/v7/v7_materialize_v75_features.py")
        sys.exit(1)

    LOG.info("Loading parquet: %s", PARQUET_PATH)
    df_all = pd.read_parquet(PARQUET_PATH)
    LOG.info("  loaded %d rows", len(df_all))

    # Convert ts (int64 unix seconds) to datetime ONCE.
    # This is needed because compute_metrics() calls pd.to_datetime(trades["ts"])
    # without unit="s" — if ts stays as int64, it gets interpreted as nanoseconds
    # and Sharpe / span_seconds calculations explode.
    LOG.info("Converting ts int64 → datetime ...")
    ts_dt = pd.to_datetime(df_all["ts"], unit="s", utc=True)
    df_all["_ts_year"] = ts_dt.dt.year
    df_all["_ts_month"] = ts_dt.dt.month
    # Replace ts with datetime so downstream code (compute_metrics) works correctly.
    # We keep int64 version separately for outcome_lookup keys.
    ts_int_list = df_all["ts"].astype(np.int64).tolist()
    df_all["ts"] = ts_dt

    # Pre-compute outcome_lookup dict ONCE for the entire dataset.
    # This maps (ts_int, symbol) → fwd_ret_3, used to commit pending predictions.
    # Uses zip() instead of iterrows() — 100x faster on 1.4M rows.
    LOG.info("Building outcome_lookup dict (ts, symbol) → fwd_ret_3 ...")
    t_lookup = time.time()
    sym_list = df_all["symbol"].astype(str).tolist()
    label_list = df_all[LABEL].astype(np.float64).tolist()
    outcome_lookup = dict(zip(zip(ts_int_list, sym_list), label_list))
    LOG.info("  built %d entries in %.1fs", len(outcome_lookup), time.time() - t_lookup)

    all_trades = []
    all_meta = []
    equity_curve_pts = []

    for w in WF_WINDOWS:
        if args.model_suffix:
            model_path = MODELS_DIR / f"v75_{args.model_suffix}_{w}.txt"
        else:
            model_path = MODELS_DIR / f"v75_{w}.txt"
        if not model_path.exists():
            LOG.warning("Model missing for %s — skipping (looked for %s)", w, model_path.name)
            continue
        # Slice window manually (ts is int64 unix seconds in the parquet)
        yr, mo = w.split("-")
        yr, mo = int(yr), int(mo)
        mask = (df_all["_ts_year"] == yr) & (df_all["_ts_month"] == mo)
        df_w = df_all[mask].copy()

        if len(df_w) == 0:
            LOG.warning("Window %s: no data", w)
            continue

        t0 = time.time()
        trades, trie, meta = simulate_window(
            df_w=df_w,
            outcome_lookup=outcome_lookup,
            model_path=str(model_path),
            thr_long=args.thr_long,
            thr_short=args.thr_short,
            trie_weight=args.trie_weight,
            trie_min_obs=args.trie_min_obs,
            n_bins=args.n_bins,
            half_life_hours=args.half_life_hours,
        )
        elapsed = time.time() - t0
        trades["window"] = w
        trades["trie_hit"] = meta["n_trie_hits"] > 0  # placeholder; per-row trie hit tracking would be expensive

        m = compute_metrics(trades)
        m["window"] = w
        m["elapsed_seconds"] = round(elapsed, 1)
        m["trie_hits"] = meta["n_trie_hits"]
        m["trie_committed"] = meta["n_committed"]
        m["trie_nodes"] = meta["trie_stats"]["n_nodes"]
        m["trie_inserts_total"] = meta["trie_stats"]["n_inserts_total"]

        print(
            f"\n[{w}] n={m['n_trades']:4d} L={m['n_long']:4d} S={m['n_short']:3d} "
            f"WR={m['wr']:.3f} PF={m['pf']:.2f} PnL={m['total_pnl_pct']:+.2f}% "
            f"${m['total_pnl_dollars']:+.0f} Sharpe={m['sharpe_ann']:.2f} MaxDD={m['max_dd_pct']:.2f}% "
            f"trie_hits={meta['n_trie_hits']} nodes={meta['trie_stats']['n_nodes']} ({elapsed:.1f}s)"
        )

        all_trades.append(trades)
        all_meta.append(m)

        # Save per-window trades
        trades_path = OUTPUT_DIR / f"v75_online_trades_{w}.parquet"
        trades.to_parquet(trades_path, index=False)
        LOG.info("  saved %s (%d trades)", trades_path.name, len(trades))

        # Save trie state
        trie_path = OUTPUT_DIR / f"v75_online_trie_{w}.pkl"
        trie.save(str(trie_path))

    if not all_trades:
        LOG.error("No trades produced. Exiting.")
        sys.exit(1)

    # Aggregate
    all_trades_df = pd.concat(all_trades, ignore_index=True)
    agg = compute_metrics(all_trades_df)
    agg["window"] = "TOTAL"
    agg["config"] = {
        "thr_long": args.thr_long,
        "thr_short": args.thr_short,
        "model_suffix": args.model_suffix,
        "trie_weight": args.trie_weight,
        "trie_min_obs": args.trie_min_obs,
        "n_bins": args.n_bins,
        "half_life_hours": args.half_life_hours,
    }

    print("\n" + "=" * 110)
    print("AGGREGATE (online simulation)")
    print(
        f"  Trades={agg['n_trades']} (L={agg['n_long']}, S={agg['n_short']}) "
        f"WR={agg['wr']:.3f} PF={agg['pf']:.2f} PnL={agg['total_pnl_pct']:+.2f}% "
        f"${agg['total_pnl_dollars']:+.0f} Sharpe={agg['sharpe_ann']:.2f} MaxDD={agg['max_dd_pct']:.2f}%"
    )
    print("=" * 110)

    # Ship criteria
    ship_sharpe = agg["sharpe_ann"] > 1.0
    ship_maxdd = agg["max_dd_pct"] > -15.0
    ship_wr = agg["wr"] > 0.52
    print(f"  SHIP: Sharpe>1.0={'✅' if ship_sharpe else '❌'}  MaxDD>-15%={'✅' if ship_maxdd else '❌'}  WR>52%={'✅' if ship_wr else '❌'}  "
          f"ALL PASS={'YES' if (ship_sharpe and ship_maxdd and ship_wr) else 'NO'}")

    # Save equity curve
    cum_dollars = np.cumsum(all_trades_df.sort_values("ts")["pnl_dollars"].values)
    equity = ACCOUNT_SIZE + cum_dollars
    eq_df = pd.DataFrame({
        "ts": all_trades_df.sort_values("ts")["ts"].values,
        "equity_dollars": equity,
        "equity_pct": equity / ACCOUNT_SIZE * 100,
    })
    eq_path = OUTPUT_DIR / "v75_online_equity_curve.parquet"
    eq_df.to_parquet(eq_path, index=False)
    LOG.info("Saved equity curve: %s", eq_path)

    # Save summary
    summary = {
        "results": all_meta,
        "aggregate": agg,
        "ship_criteria": {
            "sharpe_gt_1": ship_sharpe,
            "maxdd_gt_neg15": ship_maxdd,
            "wr_gt_52": ship_wr,
            "all_pass": ship_sharpe and ship_maxdd and ship_wr,
        },
    }
    summary_path = OUTPUT_DIR / "v75_online_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    LOG.info("Saved summary: %s", summary_path)

    # Compare vs static (if static summary exists)
    static_summary_path = OUTPUT_DIR / "v75_backtest_summary.json"
    if static_summary_path.exists():
        with open(static_summary_path) as f:
            static = json.load(f)
        # Static summary structure: { "results": [ {window, n_trades, wr, ...}, ... ],
        #                              "aggregate": {...},
        #                              "config": {...} }
        # Try aggregate first, then fall back to first per-window result.
        static_agg = static.get("aggregate", {})
        if not static_agg or "n_trades" not in static_agg:
            static_agg = static.get("results", [{}])[0] if static.get("results") else {}
        static_config = static.get("config", {})
        print("\n" + "-" * 110)
        print(f"COMPARISON: Online (F8 trie) vs Static (v7.5 baseline)")
        print(f"  Static config: thr_long={static_config.get('thr_long', '?')}, "
              f"thr_short={static_config.get('thr_short', '?')}, "
              f"model_suffix={static_config.get('model_suffix', '?')!r}")
        print(f"  Online config: thr_long={args.thr_long}, thr_short={args.thr_short}, "
              f"model_suffix={args.model_suffix!r}")
        if static_agg:
            print(f"  Metric        |  Static        |  Online        |  Δ")
            print(f"  n_trades      |  {static_agg.get('n_trades', '?'):>10}    |  {agg['n_trades']:>10}    |  {agg['n_trades'] - static_agg.get('n_trades', 0):+d}")
            print(f"  WR            |  {static_agg.get('wr', 0):>10.4f}    |  {agg['wr']:>10.4f}    |  {agg['wr'] - static_agg.get('wr', 0):+.4f}")
            print(f"  PF            |  {static_agg.get('pf', 0):>10.2f}    |  {agg['pf']:>10.2f}    |  {agg['pf'] - static_agg.get('pf', 0):+.2f}")
            print(f"  PnL%          |  {static_agg.get('total_pnl_pct', 0):>10.2f}    |  {agg['total_pnl_pct']:>10.2f}    |  {agg['total_pnl_pct'] - static_agg.get('total_pnl_pct', 0):+.2f}")
            print(f"  Sharpe        |  {static_agg.get('sharpe_ann', 0):>10.2f}    |  {agg['sharpe_ann']:>10.2f}    |  {agg['sharpe_ann'] - static_agg.get('sharpe_ann', 0):+.2f}")
            print(f"  MaxDD%        |  {static_agg.get('max_dd_pct', 0):>10.2f}    |  {agg['max_dd_pct']:>10.2f}    |  {agg['max_dd_pct'] - static_agg.get('max_dd_pct', 0):+.2f}")
        else:
            print(f"  (No static aggregate found in {static_summary_path.name})")
        print("-" * 110)


if __name__ == "__main__":
    main()
