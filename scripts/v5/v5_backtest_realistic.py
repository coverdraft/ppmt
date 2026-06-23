"""
V5 Realistic backtest: simulate trades with proper SL/TP using label_max_fav and label_max_adv.

For each approved signal:
  - If LONG: SL = entry * (1 - 0.4%/lev), TP = entry * (1 + 0.6%/lev)
    Wait — actual margin SL is -5%, but the bar-level SL we use is 0.4% (TP=0.6%)
    BEFORE leverage. The margin impact is lev * bar_move.
  - Simulate using label_max_fav (max favorable) and label_max_adv (max adverse):
    If max_adv <= -0.4% → SL hit first (loss = -0.4% * lev on margin)
    If max_fav >= 0.6% → TP hit first (win = +0.6% * lev on margin)
    If neither → use label_pnl (forward return at horizon H)
    If both hit → conservative: SL hit first (loss)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
import lightgbm as lgb

from ppmt.data.storage import PPMTStorage
from ppmt.risk.v5_risk_gate import SignalV5, evaluate_signal

LOG = logging.getLogger("v5_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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

TP_PCT = 1.5  # take profit at +1.5% (bar level) — realistic for 5m scalp
SL_PCT = 1.0  # stop loss at -1.0% (bar level) — R:R = 1.5

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

MODEL_PATH = Path("/home/z/my-project/download/v5_lgbm_model.txt")


def simulate_trade_pnl(label_max_fav: float, label_max_adv: float, label_pnl: float,
                       label_hit_tp_first: int, direction: str, leverage: int) -> float:
    """Simulate one trade's PnL on margin (% of margin).

    Uses the LABEL_HIT_TP_FIRST column directly:
      - If 1: TP hit first at +0.6% → realized = +0.6% * lev
      - If 0: SL hit first at -0.4% → realized = -0.4% * lev
    This matches what the LGBM was trained on.

    Args:
        label_max_fav: max favorable excursion (unused now, kept for compat)
        label_max_adv: max adverse excursion (unused now)
        label_pnl: actual realized return at horizon H (used as fallback)
        label_hit_tp_first: 1 if TP hit before SL, 0 if SL hit first, NaN if neither
        direction: 'LONG' or 'SHORT' — but with HIT_TP_FIRST label, the direction
                   is already encoded (it's "going LONG" if model predicts TP-first
                   for an up move)
        leverage: integer leverage used

    Returns: pnl as % of margin (positive = win, negative = loss)
    """
    TP_RETURN = 0.6
    SL_RETURN = -0.4

    if label_hit_tp_first == 1:
        return TP_RETURN * leverage
    elif label_hit_tp_first == 0:
        return SL_RETURN * leverage
    else:
        # Neither hit — use realized return
        return label_pnl * leverage


def main():
    storage = PPMTStorage()
    # Load ALL labeled observations (we'll split train/valid/test by regime)
    conn = storage._ensure_conn()
    rows = conn.execute("""
        SELECT symbol, timeframe, ts, pattern_hash,
               historical_regime, runtime_regime, asset_class,
               features_json, prior_win_rate, prior_expected_move, prior_count,
               label_win, label_pnl, label_max_fav, label_max_adv, label_hit_tp_first
        FROM feature_observations
        WHERE label_hit_tp_first IS NOT NULL
        ORDER BY ts ASC
    """).fetchall()
    if not rows:
        LOG.error("No labeled data")
        return

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
    asia = hour.isin([0, 1, 2, 18, 19, 20, 21, 22, 23])
    alt = ~df["asset_class"].isin(["blue_chip"])
    scalp = df["timeframe"].isin(["1m", "5m", "15m"])
    df["edge_strong"] = (alt & scalp & asia).astype(int)
    score = alt.astype(int) + scalp.astype(int) + asia.astype(int)
    df["edge_marginal"] = ((score == 2) & ~df["edge_strong"]).astype(int)

    feature_cols = FEATURE_NAMES + ["edge_strong", "edge_marginal"]
    LOG.info("Total labeled rows: %d", len(df))
    LOG.info("  By regime:")
    for r in df.groupby("historical_regime").size().items():
        LOG.info("    %s: %d", r, df[df["historical_regime"] == r].shape[0])

    # Load model
    model = lgb.Booster(model_file=str(MODEL_PATH))
    LOG.info("Model loaded")

    # Predict probabilities
    X = df[feature_cols].values
    df["proba"] = model.predict(X)

    # Direction from prior_expected_move
    df["direction"] = np.where(df["prior_expected_move"] > 0, "LONG", "SHORT")

    # === Run realistic backtest ===
    base_size = 100.0
    base_lev = 7

    print("\n" + "=" * 80)
    print("V5 REALISTIC BACKTEST — with SL=-0.4% TP=+0.6% simulation")
    print("=" * 80)
    print(f"{'Config':<45} {'Trades':>7} {'WR':>6} {'PF':>8} {'PnL%':>10} {'AvgPnL':>8}")
    print("-" * 80)

    all_results = []

    for thresh in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        for use_gate in [True, False]:
            for test_regime in ["RECENT_2026", "RANGE_2025"]:
                test_df = df[df["historical_regime"] == test_regime].copy()
                if len(test_df) == 0: continue

                # Filter by threshold
                sig = test_df[test_df["proba"] >= thresh].copy()
                if len(sig) == 0:
                    print(f"thresh={thresh} gate={'Y' if use_gate else 'N'} {test_regime:<14}"
                          f"  {0:>7} {0:>6.2f} {0:>8.2f} {0:>10.2f} {0:>8.3f}")
                    continue

                # Run through Risk Gate
                approved_idx = []
                leverages = []
                for idx, row in sig.iterrows():
                    if use_gate:
                        asset_class = TOKEN_CLASS.get(row["symbol"], row["asset_class"])
                        s = SignalV5(
                            symbol=row["symbol"],
                            asset_class=asset_class,
                            timeframe=row["timeframe"],
                            direction=row["direction"],
                            entry_price=100.0,
                            expected_move_pct=row["prior_expected_move"],
                            win_rate=row["prior_win_rate"],
                            confidence=row["proba"],
                            hour_utc=int(row["hour_utc"]),
                            leverage=base_lev,
                            size_usd=base_size,
                        )
                        d = evaluate_signal(s)
                        if d.approved:
                            approved_idx.append(idx)
                            leverages.append(d.adjusted_leverage)
                    else:
                        # No gate: simulate at fixed leverage, fixed SL/TP
                        approved_idx.append(idx)
                        leverages.append(base_lev)

                if not approved_idx:
                    print(f"thresh={thresh} gate={'Y' if use_gate else 'N'} {test_regime:<14}"
                          f"  {0:>7} {0:>6.2f} {0:>8.2f} {0:>10.2f} {0:>8.3f}")
                    continue

                appr = sig.loc[approved_idx].copy()
                appr["lev"] = leverages

                # Simulate PnL
                pnls = []
                for idx, row in appr.iterrows():
                    p = simulate_trade_pnl(
                        label_max_fav=row["label_max_fav"],
                        label_max_adv=row["label_max_adv"],
                        label_pnl=row["label_pnl"],
                        label_hit_tp_first=row["label_hit_tp_first"],
                        direction=row["direction"],
                        leverage=int(row["lev"]),
                    )
                    pnls.append(p)
                appr["pnl_margin"] = pnls

                n = len(appr)
                wins = (appr["pnl_margin"] > 0).sum()
                wr = wins / n if n else 0
                gw = appr.loc[appr["pnl_margin"] > 0, "pnl_margin"].sum()
                gl = -appr.loc[appr["pnl_margin"] < 0, "pnl_margin"].sum()
                pf = gw / gl if gl > 0 else float("inf")
                total = appr["pnl_margin"].sum()
                avg = appr["pnl_margin"].mean()

                config = f"thresh={thresh} gate={'Y' if use_gate else 'N'} {test_regime}"
                print(f"{config:<45} {n:>7} {wr:>6.3f} {pf:>8.2f} {total:>10.2f} {avg:>8.3f}")
                all_results.append({
                    "config": config, "n_trades": n, "win_rate": wr,
                    "profit_factor": pf, "total_pnl_pct": total, "avg_pnl_pct": avg,
                })

    print("\n" + "=" * 80)
    print("Per-symbol breakdown (thresh=0.65, gate=ON, ALL regimes pooled)")
    print("=" * 80)
    print(f"{'Symbol':<14} {'Trades':>7} {'WR':>6} {'PF':>8} {'PnL%':>10} {'AvgLev':>7}")
    print("-" * 80)

    sig = df[df["proba"] >= 0.65].copy()
    if len(sig) > 0:
        appr_idx = []
        leverages = []
        for idx, row in sig.iterrows():
            asset_class = TOKEN_CLASS.get(row["symbol"], row["asset_class"])
            s = SignalV5(
                symbol=row["symbol"], asset_class=asset_class,
                timeframe=row["timeframe"], direction=row["direction"],
                entry_price=100.0, expected_move_pct=row["prior_expected_move"],
                win_rate=row["prior_win_rate"], confidence=row["proba"],
                hour_utc=int(row["hour_utc"]), leverage=base_lev, size_usd=base_size,
            )
            d = evaluate_signal(s)
            if d.approved:
                appr_idx.append(idx)
                leverages.append(d.adjusted_leverage)

        if appr_idx:
            appr = sig.loc[appr_idx].copy()
            appr["lev"] = leverages
            appr["pnl_margin"] = [
                simulate_trade_pnl(r["label_max_fav"], r["label_max_adv"],
                                   r["label_pnl"], r["label_hit_tp_first"],
                                   r["direction"], int(r["lev"]))
                for _, r in appr.iterrows()
            ]
            for sym, sub in appr.groupby("symbol"):
                n = len(sub)
                if n == 0: continue
                wr = (sub["pnl_margin"] > 0).mean()
                gw = sub.loc[sub["pnl_margin"] > 0, "pnl_margin"].sum()
                gl = -sub.loc[sub["pnl_margin"] < 0, "pnl_margin"].sum()
                pf = gw / gl if gl > 0 else float("inf")
                total = sub["pnl_margin"].sum()
                avg_lev = sub["lev"].mean()
                print(f"{sym:<14} {n:>7} {wr:>6.3f} {pf:>8.2f} {total:>10.2f} {avg_lev:>7.1f}")
            print("\nBy regime:")
            print(f"{'Regime':<14} {'Trades':>7} {'WR':>6} {'PF':>8} {'PnL%':>10}")
            for reg, sub in appr.groupby("historical_regime"):
                n = len(sub)
                if n == 0: continue
                wr = (sub["pnl_margin"] > 0).mean()
                gw = sub.loc[sub["pnl_margin"] > 0, "pnl_margin"].sum()
                gl = -sub.loc[sub["pnl_margin"] < 0, "pnl_margin"].sum()
                pf = gw / gl if gl > 0 else float("inf")
                total = sub["pnl_margin"].sum()
                print(f"{reg:<14} {n:>7} {wr:>6.3f} {pf:>8.2f} {total:>10.2f}")

    # Save final report
    out = Path("/home/z/my-project/download/v5_realistic_backtest.json")
    out.write_text(json.dumps(all_results, indent=2, default=str))
    LOG.info("Report saved to %s", out)


if __name__ == "__main__":
    main()
