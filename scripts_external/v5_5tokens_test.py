"""Run V5 (SL=3ATR, TP=2ATR, RR=0.67) on all 5 tokens × 60d OOS.
V5 had the best PnL (-25% on 3 tokens). This test extends to 5 tokens.
Also tests fee_pct=0.02 (maker) to see if maker orders flip it to profitable.
"""
import sys, os, time, json
sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ppmt/src")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")
import logging
logging.basicConfig(level=logging.WARNING)
for n in ["ppmt","ppmt.engine","ppmt.core","ppmt.data"]: logging.getLogger(n).setLevel(logging.WARNING)

import ppmt_v23_combined as v23
from ppmt_v23_combined import ConfigV23, walk_forward_backtest
from ppmt_grid_search import load_ohlcv, compute_metrics, TOKENS

v23.OOS_DAYS = 60
OUT_FILE = "/home/z/my-project/scripts/v5_5tokens_results.json"

data = {}
for sym, ac in TOKENS:
    df_5m = load_ohlcv(sym, "5m")
    df_15m = load_ohlcv(sym, "15m")
    data[sym] = (ac, df_5m, df_15m)
print("Data loaded (5 tokens).", flush=True)

# Test 2 fee scenarios: taker (0.04) and maker (0.02)
for fee_label, fee_pct in [("taker_0.04", 0.04), ("maker_0.02", 0.02)]:
    cfg = ConfigV23(
        reverse_direction=True,
        sl_atr_mult=3.0,
        tp_atr_mult=2.0,
        sl_cap_pct=3.5,
        tp_cap_pct=3.0,
        sl_floor_pct=0.35,
        tp_floor_pct=0.30,
        enforce_rr2=False,
        max_hold_bars=36,
        fee_pct=fee_pct,
    )
    print(f"\n=== V5 with {fee_label} fee ===", flush=True)
    print(f"SL={cfg.sl_atr_mult}×ATR TP={cfg.tp_atr_mult}×ATR RR={cfg.tp_atr_mult/cfg.sl_atr_mult:.2f}", flush=True)

    results = {"name": f"V5_{fee_label}", "per_token": {}, "agg": {}}
    total_pnl = 0; total_n = 0; total_wins = 0; total_shorts = 0

    for sym, (ac, df_5m, df_15m) in data.items():
        t1 = time.time()
        trades = walk_forward_backtest(sym, ac, df_5m, df_15m, cfg)
        m = compute_metrics(trades, cfg.initial_capital)
        results["per_token"][sym] = m
        total_pnl += m["pnl_pct"]
        total_n += m["n_trades"]
        total_wins += int(m["wr"]/100 * m["n_trades"])
        total_shorts += int(m["shorts_pct"]/100 * m["n_trades"])
        marker = "✓" if m["pnl_pct"] > 0 else "✗"
        print(f"  {marker} {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
              f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
              f"shorts={m['shorts_pct']:4.1f}% ({time.time()-t1:.0f}s)", flush=True)
        results["agg"] = {
            "n_trades": total_n,
            "wr": total_wins/total_n*100 if total_n else 0,
            "pnl_pct": total_pnl,
            "shorts_pct": total_shorts/total_n*100 if total_n else 0,
        }
        with open(OUT_FILE, "w") as f:
            json.dump(results, f, indent=2, default=str)

    print(f"\n  V5_{fee_label} AGG: n={total_n} WR={results['agg']['wr']:.1f}% "
          f"PnL={total_pnl:+.1f}% shorts={results['agg']['shorts_pct']:.1f}%", flush=True)

print("\nDONE", flush=True)
