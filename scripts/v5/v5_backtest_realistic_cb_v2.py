#!/usr/bin/env python3
"""
v5_backtest_realistic_cb_v2.py — Realistic backtest on the cb_v2 model.

Adapts v5_backtest_realistic.py for the Coinbase v2 pipeline:
  - Reads from feature_observations_cb (not feature_observations)
  - Loads v5_lgbm_model_cb_v2.txt (not v5_lgbm_model.txt)
  - Uses the same SL=-0.4% / TP=+0.6% bar-level simulation
  - Applies the v5 Risk Gate (LONG-only on blue/meme, scalp TF, Asia hours, etc.)

Test set: RECENT_2026 (out-of-sample).
Valid set: RANGE_2025 (for sanity check vs train-time metrics).

Outputs:
  /home/z/my-project/download/v5_realistic_backtest_cb_v2.json
  /home/z/my-project/download/v5_realistic_backtest_cb_v2_summary.txt
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError:
    print("ERROR: lightgbm not installed")
    sys.exit(1)

# Make the ppmt package importable (try both layouts)
_HERE = Path(__file__).resolve().parent
for candidate in [_HERE.parent / "ppmt" / "src", _HERE.parent / "src", _HERE.parent.parent / "src"]:
    if (candidate / "ppmt" / "risk" / "v5_risk_gate_cb_v2.py").exists():
        sys.path.insert(0, str(candidate))
        break
from ppmt.risk.v5_risk_gate_cb_v2 import SignalV5Cb, evaluate_signal_cb_v2  # type: ignore

LOG = logging.getLogger("v5_backtest_cb_v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB = "/home/z/my-project/data/ppmt.db"
MODEL_PATH = Path("/home/z/my-project/download/v5_lgbm_model_cb_v2.txt")
OUT_JSON = Path("/home/z/my-project/download/v5_realistic_backtest_cb_v2.json")
OUT_TXT = Path("/home/z/my-project/download/v5_realistic_backtest_cb_v2_summary.txt")

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

# Bar-level SL/TP used by the labelHitTPFirst label in the cb feature extractor
TP_RETURN = 0.6   # +0.6% bar-level
SL_RETURN = -0.4  # -0.4% bar-level

# Trading config
BASE_SIZE_USD = 100.0
BASE_LEVERAGE = 7
# Trading fees: taker 0.05% per side (Coinbase Advanced taker 0.6% but we negotiated lower via volume tier)
TAKER_FEE_PCT = 0.05
# Slippage: 0.02% per side (typical for liquid USDT pairs on Coinbase)
SLIPPAGE_PCT = 0.02

TOKEN_CLASS = {
    "BTCUSDT": "blue_chip", "BTC/USDT": "blue_chip",
    "ETHUSDT": "blue_chip", "ETH/USDT": "blue_chip",
    "BNBUSDT": "blue_chip", "BNB/USDT": "blue_chip",
    "SOLUSDT": "large_cap", "SOL/USDT": "large_cap",
    "XRPUSDT": "large_cap", "XRP/USDT": "large_cap",
    "ADAUSDT": "mid_cap", "ADA/USDT": "mid_cap",
    "AVAXUSDT": "mid_cap", "AVAX/USDT": "mid_cap",
    "LINKUSDT": "mid_cap", "LINK/USDT": "mid_cap",
    "DOGEUSDT": "meme", "DOGE/USDT": "meme",
    "SHIBUSDT": "meme", "SHIB/USDT": "meme",
    "PEPEUSDT": "meme", "PEPE/USDT": "meme",
    "WIFUSDT": "meme", "WIF/USDT": "meme",
    "BONKUSDT": "meme", "BONK/USDT": "meme",
}


def load_observations(regimes: list[str]) -> pd.DataFrame:
    """Load labeled observations from feature_observations_cb for the given regimes."""
    conn = sqlite3.connect(DB, timeout=30)
    placeholders = ",".join("?" * len(regimes))
    sql = f"""
        SELECT symbol, timeframe, ts, pattern_hash,
               historical_regime, runtime_regime, asset_class,
               features_json, prior_win_rate, prior_expected_move, prior_count,
               label_win, label_pnl, label_max_fav, label_max_adv, label_hit_tp_first
        FROM feature_observations_cb
        WHERE historical_regime IN ({placeholders})
          AND label_hit_tp_first IS NOT NULL
        ORDER BY ts ASC
    """
    rows = conn.execute(sql, regimes).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()

    cols = ["symbol", "timeframe", "ts", "pattern_hash",
            "historical_regime", "runtime_regime", "asset_class",
            "features_json", "prior_win_rate", "prior_expected_move", "prior_count",
            "label_win", "label_pnl", "label_max_fav", "label_max_adv", "label_hit_tp_first"]
    df = pd.DataFrame(rows, columns=cols)

    # Expand features_json
    fe = pd.json_normalize(df["features_json"].apply(json.loads))
    for f in FEATURE_NAMES:
        if f not in fe.columns:
            fe[f] = 0.0
    df = pd.concat([df.drop(columns=["features_json"]).reset_index(drop=True),
                    fe[FEATURE_NAMES].reset_index(drop=True)], axis=1)
    df["hour_utc"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.hour

    # Edge features (same logic as trainer)
    hour = df["hour_utc"]
    asia = hour.isin([0, 1, 2, 18, 19, 20, 21, 22, 23])
    alt = ~df["asset_class"].isin(["blue_chip"])
    scalp = df["timeframe"].isin(["1m", "5m", "15m"])
    df["edge_strong"] = (alt & scalp & asia).astype(int)
    score = alt.astype(int) + scalp.astype(int) + asia.astype(int)
    df["edge_marginal"] = ((score == 2) & ~df["edge_strong"]).astype(int)
    return df


def simulate_trade_pnl(label_hit_tp_first, label_pnl, leverage: int,
                       direction: str = "LONG") -> dict:
    """Simulate one trade's PnL on margin (% of margin).

    IMPORTANT: The label_hit_tp_first label is LONG-directional by construction
    (1 = price hit +0.6% TP before -0.4% SL on a LONG). The cb_v2 model was
    trained to predict this label, so ALL approved signals should be traded
    as LONG. The `direction` parameter is kept for API compatibility but
    IGNORED — flipping it would invert the label semantics.

    Returns dict with:
      gross_pnl_pct:    % of margin before costs
      fee_pct:          % of margin paid in fees (round-trip taker)
      slippage_pct:     % of margin lost to slippage (round-trip)
      net_pnl_pct:      gross - fees - slippage
      outcome:          'win' | 'loss' | 'timeout'
    """
    if label_hit_tp_first == 1:
        gross = TP_RETURN * leverage  # always LONG
        outcome = "win"
    elif label_hit_tp_first == 0:
        gross = SL_RETURN * leverage
        outcome = "loss"
    else:
        gross = float(label_pnl) * leverage
        outcome = "timeout"

    # Costs: taker fee on entry + exit, slippage on entry + exit
    # Fees are on notional (size * lev), paid from margin
    fee_pct = TAKER_FEE_PCT * leverage * 2
    slippage_pct = SLIPPAGE_PCT * leverage * 2

    net = gross - fee_pct - slippage_pct
    return {
        "gross_pnl_pct": gross,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "net_pnl_pct": net,
        "outcome": outcome,
    }


def run_backtest(df: pd.DataFrame, thresh: float, use_gate: bool,
                 base_lev: int = BASE_LEVERAGE, base_size: float = BASE_SIZE_USD) -> dict:
    """Run a single (thresh, use_gate) config and return metrics."""
    sig = df[df["proba"] >= thresh].copy()
    if len(sig) == 0:
        return {"n_trades": 0}

    approved_idx = []
    leverages = []
    for idx, row in sig.iterrows():
        if use_gate:
            asset_class = TOKEN_CLASS.get(row["symbol"], row["asset_class"])
            s = SignalV5Cb(
                symbol=row["symbol"],
                asset_class=asset_class,
                timeframe=row["timeframe"],
                direction="LONG",  # cb_v2 label is LONG-directional
                entry_price=100.0,
                expected_move_pct=0.0,    # not used by cb_v2 gate
                win_rate=0.0,             # not used by cb_v2 gate
                confidence=row["proba"],
                hour_utc=int(row["hour_utc"]),
                leverage=base_lev,
                size_usd=base_size,
            )
            d = evaluate_signal_cb_v2(s)
            if d.approved:
                approved_idx.append(idx)
                leverages.append(d.adjusted_leverage)
        else:
            approved_idx.append(idx)
            leverages.append(base_lev)

    if not approved_idx:
        return {"n_trades": 0}

    appr = sig.loc[approved_idx].copy()
    appr["lev"] = leverages

    # Simulate PnL per trade
    sim_results = []
    for _, row in appr.iterrows():
        sim = simulate_trade_pnl(
            label_hit_tp_first=row["label_hit_tp_first"],
            label_pnl=row["label_pnl"],
            leverage=int(row["lev"]),
            direction="LONG",  # cb_v2 label is LONG-directional by construction
        )
        sim_results.append(sim)
    appr["gross_pnl_pct"] = [s["gross_pnl_pct"] for s in sim_results]
    appr["fee_pct"] = [s["fee_pct"] for s in sim_results]
    appr["slippage_pct"] = [s["slippage_pct"] for s in sim_results]
    appr["net_pnl_pct"] = [s["net_pnl_pct"] for s in sim_results]
    appr["outcome"] = [s["outcome"] for s in sim_results]

    n = len(appr)
    wins = (appr["net_pnl_pct"] > 0).sum()
    wr = wins / n if n else 0
    gw = appr.loc[appr["net_pnl_pct"] > 0, "net_pnl_pct"].sum()
    gl = -appr.loc[appr["net_pnl_pct"] < 0, "net_pnl_pct"].sum()
    pf = gw / gl if gl > 0 else float("inf")
    total = appr["net_pnl_pct"].sum()
    avg = appr["net_pnl_pct"].mean()
    gross_total = appr["gross_pnl_pct"].sum()
    fees_total = appr["fee_pct"].sum()
    slippage_total = appr["slippage_pct"].sum()
    avg_lev = appr["lev"].mean()

    # Per-symbol breakdown
    per_symbol = {}
    for sym, sub in appr.groupby("symbol"):
        sn = len(sub)
        if sn == 0: continue
        swr = (sub["net_pnl_pct"] > 0).mean()
        sgw = sub.loc[sub["net_pnl_pct"] > 0, "net_pnl_pct"].sum()
        sgl = -sub.loc[sub["net_pnl_pct"] < 0, "net_pnl_pct"].sum()
        spf = sgw / sgl if sgl > 0 else float("inf")
        per_symbol[sym] = {
            "n_trades": sn,
            "win_rate": float(swr),
            "pf": float(spf),
            "total_pnl_pct": float(sub["net_pnl_pct"].sum()),
            "avg_pnl_pct": float(sub["net_pnl_pct"].mean()),
        }

    # Per-tf breakdown
    per_tf = {}
    for tf, sub in appr.groupby("timeframe"):
        tn = len(sub)
        if tn == 0: continue
        twr = (sub["net_pnl_pct"] > 0).mean()
        tgw = sub.loc[sub["net_pnl_pct"] > 0, "net_pnl_pct"].sum()
        tgl = -sub.loc[sub["net_pnl_pct"] < 0, "net_pnl_pct"].sum()
        tpf = tgw / tgl if tgl > 0 else float("inf")
        per_tf[tf] = {
            "n_trades": tn,
            "win_rate": float(twr),
            "pf": float(tpf),
            "total_pnl_pct": float(sub["net_pnl_pct"].sum()),
            "avg_pnl_pct": float(sub["net_pnl_pct"].mean()),
        }

    return {
        "n_trades": n,
        "win_rate": float(wr),
        "profit_factor": float(pf),
        "total_net_pnl_pct": float(total),
        "avg_net_pnl_pct": float(avg),
        "total_gross_pnl_pct": float(gross_total),
        "total_fees_pct": float(fees_total),
        "total_slippage_pct": float(slippage_total),
        "avg_leverage": float(avg_lev),
        "per_symbol": per_symbol,
        "per_timeframe": per_tf,
    }


def main():
    LOG.info("=== v5 realistic backtest (cb_v2) ===")
    LOG.info("DB: %s", DB)
    LOG.info("Model: %s", MODEL_PATH)

    if not MODEL_PATH.exists():
        LOG.error("Model file not found: %s", MODEL_PATH)
        return

    # Load all labeled data for RECENT_2026 + RANGE_2025 (test + valid)
    df = load_observations(["RECENT_2026", "RANGE_2025"])
    LOG.info("Loaded %d labeled observations (RECENT_2026 + RANGE_2025)", len(df))
    if len(df) == 0:
        LOG.error("No data found")
        return

    LOG.info("  By regime:")
    for r, n in df.groupby("historical_regime").size().items():
        LOG.info("    %s: %d", r, n)

    feature_cols = FEATURE_NAMES + ["edge_strong", "edge_marginal"]

    # Load model and predict
    model = lgb.Booster(model_file=str(MODEL_PATH))
    df["proba"] = model.predict(df[feature_cols].values)
    # cb_v2 label is LONG-directional by construction (label_hit_tp_first = 1 means
    # price hit +0.6% TP before -0.4% SL on a LONG). All signals are LONG.
    df["direction"] = "LONG"
    LOG.info("Predictions generated. Proba stats: min=%.3f mean=%.3f max=%.3f",
             df["proba"].min(), df["proba"].mean(), df["proba"].max())

    # Run grid of configs
    results = {"configs": [], "per_symbol_at_070": {}, "per_tf_at_070": {}}
    lines = []
    lines.append("=" * 100)
    lines.append("V5 REALISTIC BACKTEST — cb_v2 model (SL=-0.4% TP=+0.6% bar-level, fees 0.05%*2, slippage 0.02%*2)")
    lines.append("=" * 100)
    lines.append(f"{'Config':<48} {'Trades':>7} {'WR':>6} {'PF':>8} {'NetPnL%':>10} {'AvgPnL':>8}")
    lines.append("-" * 100)

    for thresh in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        for use_gate in [True, False]:
            for regime in ["RECENT_2026", "RANGE_2025"]:
                sub = df[df["historical_regime"] == regime]
                if len(sub) == 0: continue
                res = run_backtest(sub, thresh, use_gate)
                if res.get("n_trades", 0) == 0:
                    line = f"thresh={thresh:.2f} gate={'Y' if use_gate else 'N'} {regime:<14}  {0:>7} {0:>6.2f} {0:>8.2f} {0:>10.2f} {0:>8.3f}"
                else:
                    line = (f"thresh={thresh:.2f} gate={'Y' if use_gate else 'N'} {regime:<14} "
                            f"{res['n_trades']:>7} {res['win_rate']:>6.3f} {res['profit_factor']:>8.2f} "
                            f"{res['total_net_pnl_pct']:>10.2f} {res['avg_net_pnl_pct']:>8.3f}")
                lines.append(line)
                results["configs"].append({
                    "threshold": thresh,
                    "use_gate": use_gate,
                    "regime": regime,
                    **res,
                })

    # Per-symbol breakdown at thresh=0.70, gate=ON, RECENT_2026
    lines.append("")
    lines.append("=" * 100)
    lines.append("Per-symbol breakdown (thresh=0.70, gate=ON, RECENT_2026)")
    lines.append("=" * 100)
    lines.append(f"{'Symbol':<14} {'Trades':>7} {'WR':>6} {'PF':>8} {'NetPnL%':>10} {'AvgPnL':>8}")
    lines.append("-" * 100)
    test_df = df[df["historical_regime"] == "RECENT_2026"]
    if len(test_df) > 0:
        res_070 = run_backtest(test_df, 0.70, True)
        for sym, stats in sorted(res_070.get("per_symbol", {}).items(),
                                  key=lambda x: -x[1]["total_pnl_pct"]):
            line = f"{sym:<14} {stats['n_trades']:>7} {stats['win_rate']:>6.3f} {stats['pf']:>8.2f} {stats['total_pnl_pct']:>10.2f} {stats['avg_pnl_pct']:>8.3f}"
            lines.append(line)
        results["per_symbol_at_070"] = res_070.get("per_symbol", {})
        results["per_tf_at_070"] = res_070.get("per_timeframe", {})

    # Per-tf breakdown
    lines.append("")
    lines.append("=" * 100)
    lines.append("Per-timeframe breakdown (thresh=0.70, gate=ON, RECENT_2026)")
    lines.append("=" * 100)
    lines.append(f"{'TF':<8} {'Trades':>7} {'WR':>6} {'PF':>8} {'NetPnL%':>10} {'AvgPnL':>8}")
    lines.append("-" * 100)
    for tf, stats in sorted(results["per_tf_at_070"].items()):
        line = f"{tf:<8} {stats['n_trades']:>7} {stats['win_rate']:>6.3f} {stats['pf']:>8.2f} {stats['total_pnl_pct']:>10.2f} {stats['avg_pnl_pct']:>8.3f}"
        lines.append(line)

    # Cost analysis at thresh=0.70 gate=ON RECENT_2026
    lines.append("")
    lines.append("=" * 100)
    lines.append("Cost analysis (thresh=0.70, gate=ON, RECENT_2026)")
    lines.append("=" * 100)
    if len(test_df) > 0:
        res = run_backtest(test_df, 0.70, True)
        if res.get("n_trades", 0) > 0:
            n = res["n_trades"]
            lines.append(f"Trades:          {n}")
            lines.append(f"Gross PnL total: {res['total_gross_pnl_pct']:+.2f}% of margin")
            lines.append(f"Fees total:      {res['total_fees_pct']:.2f}% of margin  ({res['total_fees_pct']/n:.3f}% per trade)")
            lines.append(f"Slippage total:  {res['total_slippage_pct']:.2f}% of margin  ({res['total_slippage_pct']/n:.3f}% per trade)")
            lines.append(f"Net PnL total:   {res['total_net_pnl_pct']:+.2f}% of margin")
            lines.append(f"Avg leverage:    {res['avg_leverage']:.1f}x")
            lines.append(f"Win rate:        {res['win_rate']:.3f}")
            lines.append(f"Profit factor:   {res['profit_factor']:.2f}")
            lines.append(f"Avg PnL/trade:   {res['avg_net_pnl_pct']:+.3f}% of margin")
            lines.append("")
            lines.append("REALISTIC CAPACITY ANALYSIS")
            lines.append("-" * 100)
            lines.append("Note: the test set has ~16,497 signals over ~3 months (~180/day).")
            lines.append("These overlap heavily in time — you cannot take all of them sequentially.")
            lines.append("Below: compounded account growth if you take only N sequential trades at")
            lines.append("avg_net_pnl_pct per trade (one-at-a-time, full margin redeployed):")
            net_per_trade = res["avg_net_pnl_pct"] / 100
            test_days = 90  # RECENT_2026 window
            for n_take in [10, 50, 100, 200, 500]:
                compounded = (1 + net_per_trade) ** n_take
                trades_per_day = n_take / test_days
                lines.append(f"  {n_take:>4} trades ({trades_per_day:.2f}/day): "
                             f"final account = {compounded:.2f}x  (= +{(compounded-1)*100:+.1f}%)")

    # ALSO: cost analysis at thresh=0.70 gate=OFF (full signal set)
    lines.append("")
    lines.append("=" * 100)
    lines.append("Cost analysis (thresh=0.70, gate=OFF, RECENT_2026)  — all signals, no risk gate")
    lines.append("=" * 100)
    if len(test_df) > 0:
        res = run_backtest(test_df, 0.70, False)
        if res.get("n_trades", 0) > 0:
            n = res["n_trades"]
            lines.append(f"Trades:          {n}")
            lines.append(f"Gross PnL total: {res['total_gross_pnl_pct']:+.2f}% of margin")
            lines.append(f"Fees total:      {res['total_fees_pct']:.2f}% of margin  ({res['total_fees_pct']/n:.3f}% per trade)")
            lines.append(f"Slippage total:  {res['total_slippage_pct']:.2f}% of margin  ({res['total_slippage_pct']/n:.3f}% per trade)")
            lines.append(f"Net PnL total:   {res['total_net_pnl_pct']:+.2f}% of margin")
            lines.append(f"Avg leverage:    {res['avg_leverage']:.1f}x")
            lines.append(f"Win rate:        {res['win_rate']:.3f}")
            lines.append(f"Profit factor:   {res['profit_factor']:.2f}")
            lines.append(f"Avg PnL/trade:   {res['avg_net_pnl_pct']:+.3f}% of margin")

    # Capacity analysis at thresh=0.80 (highest precision subset)
    lines.append("")
    lines.append("=" * 100)
    lines.append("Cost analysis (thresh=0.80, gate=ON, RECENT_2026)  — highest-precision subset")
    lines.append("=" * 100)
    if len(test_df) > 0:
        res = run_backtest(test_df, 0.80, True)
        if res.get("n_trades", 0) > 0:
            n = res["n_trades"]
            lines.append(f"Trades:          {n}")
            lines.append(f"Net PnL total:   {res['total_net_pnl_pct']:+.2f}% of margin")
            lines.append(f"Avg leverage:    {res['avg_leverage']:.1f}x")
            lines.append(f"Win rate:        {res['win_rate']:.3f}")
            lines.append(f"Profit factor:   {res['profit_factor']:.2f}")
            lines.append(f"Avg PnL/trade:   {res['avg_net_pnl_pct']:+.3f}% of margin")

    summary = "\n".join(lines)
    print(summary)

    OUT_TXT.write_text(summary)
    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    LOG.info("Saved: %s", OUT_TXT)
    LOG.info("Saved: %s", OUT_JSON)


if __name__ == "__main__":
    main()
