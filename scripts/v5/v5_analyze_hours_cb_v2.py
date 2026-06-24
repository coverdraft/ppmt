#!/usr/bin/env python3
"""
v5_analyze_hours_cb_v2.py — Per-hour UTC PnL analysis on cb_v2 OOS data.

Question this answers: is the gate's BAD_HOURS_UTC = {4, 5, 9, 12, 16}
rule (carried over from v1 Binance trader history) actually justified
on cb_v2 OOS data? If not, what hours (if any) should be blocked?

Method:
  1. Load all cb_v2 OOS observations (RECENT_2026 + RANGE_2025)
  2. Predict with the published cb_v2 model
  3. Filter to proba >= 0.70 (production threshold)
  4. For each hour_utc 0..23:
     - n_signals
     - precision (fraction with label_hit_tp_first == 1)
     - profit factor (sum of winning PnL / sum of losing PnL)
     - total net PnL (after fees + slippage)
     - avg net PnL per trade
  5. Compare to BAD_HOURS_UTC
  6. Recommend updated gate config

Outputs:
  /home/z/my-project/download/v5_hourly_analysis_cb_v2.json
  /home/z/my-project/download/v5_hourly_analysis_cb_v2_summary.txt
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

LOG = logging.getLogger("v5_hourly_analysis_cb_v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB = "/home/z/my-project/data/ppmt.db"
MODEL_PATH = Path("/home/z/my-project/download/v5_lgbm_model_cb_v2.txt")
OUT_JSON = Path("/home/z/my-project/download/v5_hourly_analysis_cb_v2.json")
OUT_TXT = Path("/home/z/my-project/download/v5_hourly_analysis_cb_v2_summary.txt")

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

# Bar-level SL/TP (cb_v2 label)
TP_RETURN = 0.6
SL_RETURN = -0.4
TAKER_FEE_PCT = 0.05
SLIPPAGE_PCT = 0.02
BASE_LEVERAGE = 7

# Current gate config (from v5_risk_gate_cb_v2.py)
CURRENT_BAD_HOURS = {4, 5, 9, 12, 16}
ASIA_HOURS = {0, 1, 2, 18, 19, 20, 21, 22, 23}


def load_observations(regimes: list[str]) -> pd.DataFrame:
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

    fe = pd.json_normalize(df["features_json"].apply(json.loads))
    for f in FEATURE_NAMES:
        if f not in fe.columns:
            fe[f] = 0.0
    df = pd.concat([df.drop(columns=["features_json"]).reset_index(drop=True),
                    fe[FEATURE_NAMES].reset_index(drop=True)], axis=1)
    df["hour_utc"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.hour

    # Edge features
    hour = df["hour_utc"]
    asia = hour.isin(list(ASIA_HOURS))
    alt = ~df["asset_class"].isin(["blue_chip"])
    scalp = df["timeframe"].isin(["1m", "5m", "15m"])
    df["edge_strong"] = (alt & scalp & asia).astype(int)
    score = alt.astype(int) + scalp.astype(int) + asia.astype(int)
    df["edge_marginal"] = ((score == 2) & ~df["edge_strong"]).astype(int)
    return df


def simulate_trade_pnl(label_hit_tp_first, label_pnl: float) -> float:
    """Net PnL % of margin for one LONG trade at 7x leverage."""
    if label_hit_tp_first == 1:
        gross = TP_RETURN * BASE_LEVERAGE
    elif label_hit_tp_first == 0:
        gross = SL_RETURN * BASE_LEVERAGE
    else:
        gross = float(label_pnl) * BASE_LEVERAGE
    fee = TAKER_FEE_PCT * BASE_LEVERAGE * 2
    slip = SLIPPAGE_PCT * BASE_LEVERAGE * 2
    return gross - fee - slip


def main():
    LOG.info("=== v5 hourly analysis (cb_v2) ===")
    LOG.info("DB: %s", DB)
    LOG.info("Model: %s", MODEL_PATH)

    if not MODEL_PATH.exists():
        LOG.error("Model file not found: %s", MODEL_PATH)
        return

    df = load_observations(["RECENT_2026", "RANGE_2025"])
    LOG.info("Loaded %d labeled observations (RECENT_2026 + RANGE_2025)", len(df))
    if len(df) == 0:
        LOG.error("No data found")
        return

    LOG.info("  By regime:")
    for r, n in df.groupby("historical_regime").size().items():
        LOG.info("    %s: %d", r, n)

    feature_cols = FEATURE_NAMES + ["edge_strong", "edge_marginal"]
    model = lgb.Booster(model_file=str(MODEL_PATH))
    df["proba"] = model.predict(df[feature_cols].values)
    LOG.info("Predictions generated. Proba stats: min=%.3f mean=%.3f max=%.3f",
             df["proba"].min(), df["proba"].mean(), df["proba"].max())

    # Filter to production threshold
    sig = df[df["proba"] >= 0.70].copy()
    LOG.info("Signals at thresh>=0.70: %d (%.1f%% of total)",
             len(sig), 100 * len(sig) / len(df))

    # Simulate PnL for each signal
    sig["net_pnl_pct"] = sig.apply(
        lambda r: simulate_trade_pnl(r["label_hit_tp_first"], r["label_pnl"]),
        axis=1,
    )
    sig["is_win"] = (sig["label_hit_tp_first"] == 1).astype(int)

    # ── Per-hour aggregation ──
    lines = []
    lines.append("=" * 110)
    lines.append("V5 HOURLY PnL ANALYSIS — cb_v2 OOS (RECENT_2026 + RANGE_2025, thresh=0.70)")
    lines.append("=" * 110)
    lines.append(f"Total signals: {len(sig):,}  |  Costs: taker {TAKER_FEE_PCT}%*2 + slip {SLIPPAGE_PCT}%*2 = 0.14% of margin per side")
    lines.append(f"Leverage: {BASE_LEVERAGE}x  |  TP=+{TP_RETURN}%  SL={SL_RETURN}%  (bar-level)")
    lines.append("")
    lines.append("Current gate config:")
    lines.append(f"  BAD_HOURS_UTC = {sorted(CURRENT_BAD_HOURS)}  ({len(CURRENT_BAD_HOURS)} hours blocked)")
    lines.append(f"  ASIA_HOURS_UTC = {sorted(ASIA_HOURS)}  ({len(ASIA_HOURS)} hours boosted ×1.15)")
    lines.append("")

    # Table header
    lines.append(f"{'Hour':<5} {'Daypart':<10} {'CurGate':<8} {'Signals':>8} {'Precision':>10} "
                 f"{'PF':>7} {'TotalPnL%':>11} {'AvgPnL%':>9} {'Win':>5} {'Loss':>5}")
    lines.append("-" * 110)

    hourly_results = []
    for h in range(24):
        sub = sig[sig["hour_utc"] == h]
        n = len(sub)
        if n == 0:
            lines.append(f"{h:<5} {'?':<10} {'?':<8} {0:>8} {'n/a':>10} {'n/a':>7} {0:>11.1f} {'n/a':>9} {0:>5} {0:>5}")
            continue

        # Daypart label
        if h in [0, 1, 2, 3, 4]:
            daypart = "Asia-night"
        elif h in [5, 6, 7, 8, 9, 10, 11]:
            daypart = "Asia-morn"
        elif h in [12, 13, 14, 15, 16]:
            daypart = "EU-day"
        else:
            daypart = "US-day"

        cur_gate = "BLOCKED" if h in CURRENT_BAD_HOURS else ("BOOST" if h in ASIA_HOURS else "ok")

        precision = sub["is_win"].mean()
        gw = sub.loc[sub["net_pnl_pct"] > 0, "net_pnl_pct"].sum()
        gl = -sub.loc[sub["net_pnl_pct"] < 0, "net_pnl_pct"].sum()
        pf = gw / gl if gl > 0 else float("inf")
        total_pnl = sub["net_pnl_pct"].sum()
        avg_pnl = sub["net_pnl_pct"].mean()
        wins = (sub["net_pnl_pct"] > 0).sum()
        losses = (sub["net_pnl_pct"] < 0).sum()

        lines.append(f"{h:<5} {daypart:<10} {cur_gate:<8} {n:>8} {precision:>10.4f} "
                     f"{pf:>7.2f} {total_pnl:>+11.1f} {avg_pnl:>+9.3f} {wins:>5} {losses:>5}")

        hourly_results.append({
            "hour_utc": h,
            "daypart": daypart,
            "current_gate_status": cur_gate,
            "n_signals": int(n),
            "precision": float(precision),
            "profit_factor": float(pf),
            "total_net_pnl_pct": float(total_pnl),
            "avg_net_pnl_pct": float(avg_pnl),
            "wins": int(wins),
            "losses": int(losses),
        })

    # ── Summary stats ──
    lines.append("")
    lines.append("=" * 110)
    lines.append("SUMMARY: BAD_HOURS validation")
    lines.append("=" * 110)

    total_signals = len(sig)
    bad_hours_signals = sig[sig["hour_utc"].isin(CURRENT_BAD_HOURS)]
    n_bad = len(bad_hours_signals)
    pct_blocked = 100 * n_bad / total_signals if total_signals else 0

    lines.append(f"Signals blocked by current BAD_HOURS rule: {n_bad:,} ({pct_blocked:.1f}% of total)")
    lines.append("")

    # For each currently-blocked hour, show what we'd lose by blocking it
    lines.append("Current BAD_HOURS — what we LOSE by blocking these hours:")
    lines.append(f"{'Hour':<5} {'Daypart':<10} {'Signals':>8} {'Precision':>10} {'PF':>7} {'TotalPnL%':>11} {'AvgPnL%':>9} {'Verdict':<20}")
    lines.append("-" * 110)
    for h in sorted(CURRENT_BAD_HOURS):
        sub = sig[sig["hour_utc"] == h]
        n = len(sub)
        if n == 0:
            lines.append(f"{h:<5} {'?':<10} {0:>8} {'n/a':>10} {'n/a':>7} {0:>11.1f} {'n/a':>9} {'no signals':<20}")
            continue
        precision = sub["is_win"].mean()
        gw = sub.loc[sub["net_pnl_pct"] > 0, "net_pnl_pct"].sum()
        gl = -sub.loc[sub["net_pnl_pct"] < 0, "net_pnl_pct"].sum()
        pf = gw / gl if gl > 0 else float("inf")
        total_pnl = sub["net_pnl_pct"].sum()
        avg_pnl = sub["net_pnl_pct"].mean()

        if avg_pnl > 0 and pf > 2:
            verdict = "★ UNBLOCK (profitable)"
        elif avg_pnl > 0:
            verdict = "? UNBLOCK (marginal)"
        else:
            verdict = "✓ keep blocked"
        lines.append(f"{h:<5} {'(blocked)':<10} {n:>8} {precision:>10.4f} {pf:>7.2f} {total_pnl:>+11.1f} {avg_pnl:>+9.3f} {verdict:<20}")

    # Identify if there are any ACTUALLY bad hours (avg PnL < 0 or PF < 1)
    lines.append("")
    lines.append("Hours with negative avg PnL OR PF < 1.5 (candidates for blocking):")
    lines.append(f"{'Hour':<5} {'Daypart':<10} {'Signals':>8} {'Precision':>10} {'PF':>7} {'TotalPnL%':>11} {'AvgPnL%':>9}")
    lines.append("-" * 110)
    actually_bad = []
    for h in range(24):
        sub = sig[sig["hour_utc"] == h]
        n = len(sub)
        if n == 0:
            continue
        avg_pnl = sub["net_pnl_pct"].mean()
        gw = sub.loc[sub["net_pnl_pct"] > 0, "net_pnl_pct"].sum()
        gl = -sub.loc[sub["net_pnl_pct"] < 0, "net_pnl_pct"].sum()
        pf = gw / gl if gl > 0 else float("inf")
        if avg_pnl < 0 or pf < 1.5:
            daypart = hourly_results[h]["daypart"] if h < len(hourly_results) else "?"
            lines.append(f"{h:<5} {daypart:<10} {n:>8} {sub['is_win'].mean():>10.4f} {pf:>7.2f} {sub['net_pnl_pct'].sum():>+11.1f} {avg_pnl:>+9.3f}")
            actually_bad.append(h)

    if not actually_bad:
        lines.append("  (none — all 24 hours have positive avg PnL and PF >= 1.5)")
        lines.append("")
        lines.append("  → RECOMMENDATION: REMOVE the BAD_HOURS rule entirely.")
        lines.append("    The cb_v2 LGBM model already captures hour-of-day effects via hour_sin/hour_cos features.")
        lines.append("    The rule was carried over from v1 Binance trader history and does not apply to cb_v2.")
    else:
        lines.append("")
        lines.append(f"  → RECOMMENDATION: update BAD_HOURS_UTC = {actually_bad}")

    # ── Asia hours validation ──
    lines.append("")
    lines.append("=" * 110)
    lines.append("SUMMARY: ASIA_HOURS boost validation")
    lines.append("=" * 110)
    asia_sig = sig[sig["hour_utc"].isin(ASIA_HOURS)]
    non_asia_sig = sig[~sig["hour_utc"].isin(ASIA_HOURS)]
    lines.append(f"ASIA hours signals:    {len(asia_sig):,}  precision={asia_sig['is_win'].mean():.4f}  "
                 f"PF={asia_sig.loc[asia_sig['net_pnl_pct']>0,'net_pnl_pct'].sum() / max(-asia_sig.loc[asia_sig['net_pnl_pct']<0,'net_pnl_pct'].sum(), 0.001):.2f}  "
                 f"avgPnL={asia_sig['net_pnl_pct'].mean():+.3f}%")
    lines.append(f"Non-ASIA hours:        {len(non_asia_sig):,}  precision={non_asia_sig['is_win'].mean():.4f}  "
                 f"PF={non_asia_sig.loc[non_asia_sig['net_pnl_pct']>0,'net_pnl_pct'].sum() / max(-non_asia_sig.loc[non_asia_sig['net_pnl_pct']<0,'net_pnl_pct'].sum(), 0.001):.2f}  "
                 f"avgPnL={non_asia_sig['net_pnl_pct'].mean():+.3f}%")

    asia_avg = asia_sig["net_pnl_pct"].mean()
    non_asia_avg = non_asia_sig["net_pnl_pct"].mean()
    if asia_avg > non_asia_avg * 1.05:
        lines.append(f"  → Asia hours ARE better (+{(asia_avg/non_asia_avg - 1)*100:.1f}% vs non-Asia). Keep the ×1.15 boost.")
    elif asia_avg > non_asia_avg:
        lines.append(f"  → Asia hours marginally better (+{(asia_avg/non_asia_avg - 1)*100:.1f}%). Boost is optional.")
    else:
        lines.append(f"  → Asia hours NOT better ({(asia_avg/non_asia_avg - 1)*100:+.1f}%). Remove the boost.")

    summary = "\n".join(lines)
    print(summary)

    OUT_TXT.write_text(summary)
    OUT_JSON.write_text(json.dumps({
        "config": {
            "threshold": 0.70,
            "tp_return": TP_RETURN,
            "sl_return": SL_RETURN,
            "taker_fee_pct": TAKER_FEE_PCT,
            "slippage_pct": SLIPPAGE_PCT,
            "base_leverage": BASE_LEVERAGE,
            "current_bad_hours": sorted(CURRENT_BAD_HOURS),
            "asia_hours": sorted(ASIA_HOURS),
        },
        "total_signals": len(sig),
        "hourly_results": hourly_results,
        "actually_bad_hours": actually_bad,
        "asia_avg_pnl": float(asia_avg),
        "non_asia_avg_pnl": float(non_asia_avg),
    }, indent=2, default=str))
    LOG.info("Saved: %s", OUT_TXT)
    LOG.info("Saved: %s", OUT_JSON)


if __name__ == "__main__":
    main()
