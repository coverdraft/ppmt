"""
V5 End-to-end validation: backtest the full PPMT-LGBM-RiskGate pipeline.

Loads feature_observations for the test regime (RECENT_2026), runs them
through the trained LightGBM model + V5 Risk Gate, and produces a
performance report (PF, WR, PnL) per symbol, per timeframe, per hour.

Usage:
    python /home/z/my-project/scripts/v5_validate.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

from ppmt.data.storage import PPMTStorage  # noqa: E402
from ppmt.risk.v5_risk_gate import SignalV5, evaluate_signal, summarize_decisions  # noqa: E402

LOG = logging.getLogger("v5_validate")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

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

MODEL_PATH = Path("/home/z/my-project/download/v5_lgbm_model.txt")

# Token class lookup
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


def load_test_observations(storage: PPMTStorage, regime: str = "RECENT_2026") -> pd.DataFrame:
    """Load all feature observations for the test regime."""
    conn = storage._ensure_conn()
    rows = conn.execute(
        """
        SELECT symbol, timeframe, ts, pattern_hash,
               historical_regime, runtime_regime, asset_class,
               features_json, prior_win_rate, prior_expected_move, prior_count,
               label_win, label_pnl, label_max_fav, label_max_adv, label_hit_tp_first
        FROM feature_observations
        WHERE historical_regime = ?
        ORDER BY ts ASC
        """,
        (regime,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    cols = ["symbol", "timeframe", "ts", "pattern_hash",
            "historical_regime", "runtime_regime", "asset_class",
            "features_json", "prior_win_rate", "prior_expected_move", "prior_count",
            "label_win", "label_pnl", "label_max_fav", "label_max_adv", "label_hit_tp_first"]
    df = pd.DataFrame(rows, columns=cols)
    features_expanded = pd.json_normalize(df["features_json"].apply(json.loads))
    for f in FEATURE_NAMES:
        if f not in features_expanded.columns:
            features_expanded[f] = 0.0
    df = pd.concat([df.drop(columns=["features_json"]).reset_index(drop=True),
                    features_expanded[FEATURE_NAMES].reset_index(drop=True)], axis=1)
    # Compute hour from ts
    df["hour_utc"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.hour
    return df


def backtest(
    df: pd.DataFrame,
    model,
    feature_cols: list[str],
    use_risk_gate: bool = True,
    threshold: float = 0.6,
) -> pd.DataFrame:
    """Run the backtest on the test set.

    For each observation:
      1. Predict LGBM probability of TP-first hit
      2. If prob >= threshold, generate a signal
      3. Run through Risk Gate (if use_risk_gate=True)
      4. If approved, simulate the trade using label_pnl as the realized PnL

    Returns the input df augmented with: pred_proba, signal (1/0),
    approved (1/0), realized_pnl_pct, realized_pnl_usd.
    """
    X = df[feature_cols].values
    if HAS_LGB:
        proba = model.predict(X)
    else:
        proba = model.predict_proba(X)[:, 1]

    df = df.copy()
    df["pred_proba"] = proba
    df["signal"] = (proba >= threshold).astype(int)

    # Direction from prior_expected_move: positive → LONG, negative → SHORT
    df["direction"] = np.where(df["prior_expected_move"] > 0, "LONG", "SHORT")

    approved = []
    pnl_pct_margin = []
    size_usd = []
    lev_used = []
    reason = []

    base_size = 100.0
    base_lev = 7

    for _, row in df.iterrows():
        if row["signal"] == 0:
            approved.append(0)
            pnl_pct_margin.append(0.0)
            size_usd.append(0.0)
            lev_used.append(0)
            reason.append("no_signal")
            continue

        asset_class = TOKEN_CLASS.get(row["symbol"], row.get("asset_class", "default"))
        sig = SignalV5(
            symbol=row["symbol"],
            asset_class=asset_class,
            timeframe=row["timeframe"],
            direction=row["direction"],
            entry_price=100.0,  # placeholder — we work in % space
            expected_move_pct=row["prior_expected_move"],
            win_rate=row["prior_win_rate"],
            confidence=row["pred_proba"],
            hour_utc=int(row["hour_utc"]),
            leverage=base_lev,
            size_usd=base_size,
        )
        if use_risk_gate:
            dec = evaluate_signal(sig)
            approved.append(int(dec.approved))
            reason.append(dec.reason)
            if dec.approved:
                # Realized PnL on margin = label_pnl * leverage
                pnl_pct_margin.append(row["label_pnl"] * dec.adjusted_leverage)
                size_usd.append(dec.adjusted_size_usd)
                lev_used.append(dec.adjusted_leverage)
            else:
                pnl_pct_margin.append(0.0)
                size_usd.append(0.0)
                lev_used.append(0)
        else:
            # No gate: trade everything above threshold
            approved.append(1)
            pnl_pct_margin.append(row["label_pnl"] * base_lev)
            size_usd.append(base_size)
            lev_used.append(base_lev)
            reason.append("no_gate")

    df["approved"] = approved
    df["pnl_pct_margin"] = pnl_pct_margin
    df["size_usd"] = size_usd
    df["lev_used"] = lev_used
    df["reason"] = reason
    return df


def report_metrics(df: pd.DataFrame, label: str) -> dict:
    """Compute and log metrics on a backtest result df."""
    trades = df[df["approved"] == 1].copy()
    n = len(trades)
    if n == 0:
        LOG.info("=== %s === NO TRADES", label)
        return {"label": label, "n_trades": 0}

    pnl = trades["pnl_pct_margin"].values
    wins = (pnl > 0).sum()
    losses = (pnl < 0).sum()
    wr = wins / n if n else 0
    gross_win = pnl[pnl > 0].sum()
    gross_loss = -pnl[pnl < 0].sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    total_pnl = pnl.sum()
    avg_pnl = pnl.mean()
    max_dd = pnl.cumsum().min()

    LOG.info("=== %s ===", label)
    LOG.info("  Trades:        %d", n)
    LOG.info("  Win rate:      %.3f  (%d W / %d L)", wr, wins, losses)
    LOG.info("  Profit factor: %.3f", pf)
    LOG.info("  Total PnL %%:   %.2f%%", total_pnl)
    LOG.info("  Avg PnL/trade: %.3f%%", avg_pnl)
    LOG.info("  Max DD %%:      %.2f%%", max_dd)

    return {
        "label": label,
        "n_trades": n,
        "win_rate": wr,
        "profit_factor": pf,
        "total_pnl_pct": total_pnl,
        "avg_pnl_pct": avg_pnl,
        "max_drawdown_pct": max_dd,
    }


def main() -> None:
    storage = PPMTStorage()
    counts = storage.count_feature_observations()
    LOG.info("feature_observations: %s", counts)

    if counts.get("total", 0) == 0:
        LOG.error("No feature observations. Run v5_extract_features.py first.")
        return

    # Load test set
    df = load_test_observations(storage, "RECENT_2026")
    LOG.info("Test set: %d observations across %d symbols",
             len(df), df["symbol"].nunique() if len(df) else 0)
    if len(df) == 0:
        LOG.error("No test observations for RECENT_2026. Trying RANGE_2025 as fallback...")
        df = load_test_observations(storage, "RANGE_2025")
        LOG.info("Fallback set: %d observations", len(df))

    if len(df) == 0:
        LOG.error("No data to validate.")
        return

    # Drop rows without labels (we can't compute PnL on them)
    df_labeled = df.dropna(subset=["label_pnl", "label_hit_tp_first"]).copy()
    LOG.info("Labeled rows: %d / %d", len(df_labeled), len(df))

    # Load model
    if not MODEL_PATH.exists():
        LOG.error("Model not found at %s — run v5_train_lgbm.py first", MODEL_PATH)
        return
    model = lgb.Booster(model_file=str(MODEL_PATH)) if HAS_LGB else None
    if model is None:
        LOG.error("LightGBM not available — cannot run validation")
        return

    # Add edge_label features (same as training)
    hour = pd.to_datetime(df_labeled["ts"], unit="s", utc=True).dt.hour
    asia_hours = hour.isin([0, 1, 2, 18, 19, 20, 21, 22, 23])
    is_altcoin = ~df_labeled["asset_class"].isin(["blue_chip"])
    is_scalp_tf = df_labeled["timeframe"].isin(["1m", "5m", "15m"])
    df_labeled["edge_strong"] = (is_altcoin & is_scalp_tf & asia_hours).astype(int)
    score = is_altcoin.astype(int) + is_scalp_tf.astype(int) + asia_hours.astype(int)
    df_labeled["edge_marginal"] = ((score == 2) & ~df_labeled["edge_strong"]).astype(int)

    feature_cols = FEATURE_NAMES + ["edge_strong", "edge_marginal"]

    # Run 4 configurations
    results = []
    for thresh in [0.55, 0.60, 0.65, 0.70]:
        for use_gate in [True, False]:
            label = f"thresh={thresh} gate={'ON' if use_gate else 'OFF'}"
            res = backtest(df_labeled, model, feature_cols, use_risk_gate=use_gate, threshold=thresh)
            metrics = report_metrics(res, label)
            metrics["threshold"] = thresh
            metrics["use_gate"] = use_gate
            results.append(metrics)
            # Per-symbol breakdown for the best config
            if use_gate and thresh == 0.60:
                LOG.info("--- Per-symbol breakdown ---")
                for sym, sub in res[res["approved"] == 1].groupby("symbol"):
                    n = len(sub)
                    if n == 0: continue
                    wr = (sub["pnl_pct_margin"] > 0).mean()
                    pf = sub.loc[sub["pnl_pct_margin"] > 0, "pnl_pct_margin"].sum() / max(-sub.loc[sub["pnl_pct_margin"] < 0, "pnl_pct_margin"].sum(), 0.001)
                    LOG.info("  %-12s  n=%4d  WR=%.2f  PF=%.2f  PnL=%.1f%%",
                             sym, n, wr, pf, sub["pnl_pct_margin"].sum())

    # Save report
    report = {
        "test_set_size": len(df_labeled),
        "configs": results,
    }
    out_path = Path("/home/z/my-project/download/v5_validation_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    LOG.info("Report saved to %s", out_path)

    # Also save as markdown table
    md = ["# V5 Validation Report\n",
          f"Test set: **{len(df_labeled)}** labeled observations (RECENT_2026)\n",
          "| Config | Trades | WR | PF | Total PnL % | Avg PnL % | Max DD % |",
          "|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| thresh={r['threshold']} gate={'ON' if r['use_gate'] else 'OFF'} | "
                  f"{r['n_trades']} | {r['win_rate']:.3f} | {r['profit_factor']:.2f} | "
                  f"{r['total_pnl_pct']:.1f} | {r['avg_pnl_pct']:.3f} | "
                  f"{r['max_drawdown_pct']:.1f} |")
    md_path = Path("/home/z/my-project/download/v5_validation_report.md")
    md_path.write_text("\n".join(md))
    LOG.info("Markdown report saved to %s", md_path)


if __name__ == "__main__":
    main()
