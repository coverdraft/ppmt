"""Test SL/TP variants on 3 tokens × 60d with reverse=True.
Hypothesis: with RR=2 and tight SL=1ATR, fees eat the small edge.
Wider SL/TP should reduce fee impact while maintaining edge.

Variants:
  V1: SL=1ATR, TP=2ATR (current baseline, RR=2)
  V2: SL=2ATR, TP=4ATR (wider, RR=2 maintained, fees relatively smaller)
  V3: SL=1.5ATR, TP=3ATR (medium, RR=2)
  V4: SL=2ATR, TP=3ATR (RR=1.5, favors TP hits → higher WR)
  V5: SL=3ATR, TP=2ATR (RR=0.67, favors SL survival → much higher WR)
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
from ppmt_grid_search import load_ohlcv, compute_metrics

v23.OOS_DAYS = 60
OUT_FILE = "/home/z/my-project/scripts/sltp_variants.json"

TOKENS_FAST = [
    ("BTC/USDT", "blue_chip"),
    ("SOL/USDT", "large_cap"),
    ("DOGE/USDT", "meme"),
]

# Load data once
data = {}
for sym, ac in TOKENS_FAST:
    df_5m = load_ohlcv(sym, "5m")
    df_15m = load_ohlcv(sym, "15m")
    data[sym] = (ac, df_5m, df_15m)
print("Data loaded.", flush=True)

variants = [
    {"name": "V1_sl1_tp2_RR2",   "sl_atr_mult": 1.0, "tp_atr_mult": 2.0, "sl_cap_pct": 1.5, "tp_cap_pct": 3.0},
    {"name": "V2_sl2_tp4_RR2",   "sl_atr_mult": 2.0, "tp_atr_mult": 4.0, "sl_cap_pct": 2.5, "tp_cap_pct": 5.0},
    {"name": "V3_sl15_tp3_RR2",  "sl_atr_mult": 1.5, "tp_atr_mult": 3.0, "sl_cap_pct": 2.0, "tp_cap_pct": 4.0},
    {"name": "V4_sl2_tp3_RR15",  "sl_atr_mult": 2.0, "tp_atr_mult": 3.0, "sl_cap_pct": 2.5, "tp_cap_pct": 4.0},
    {"name": "V5_sl3_tp2_RR067", "sl_atr_mult": 3.0, "tp_atr_mult": 2.0, "sl_cap_pct": 3.5, "tp_cap_pct": 3.0},
]

all_results = {"variants": []}

for v in variants:
    print(f"\n=== {v['name']} ===", flush=True)
    # If TP < 2*SL (RR<2), don't enforce RR=2 — let the variant use its own RR
    rr = v["tp_atr_mult"] / v["sl_atr_mult"]
    enforce_rr2 = rr >= 2.0
    cfg = ConfigV23(reverse_direction=True,
                    sl_atr_mult=v["sl_atr_mult"],
                    tp_atr_mult=v["tp_atr_mult"],
                    sl_cap_pct=v["sl_cap_pct"],
                    tp_cap_pct=v["tp_cap_pct"],
                    sl_floor_pct=v["sl_cap_pct"] * 0.1,
                    tp_floor_pct=v["tp_cap_pct"] * 0.1,
                    enforce_rr2=enforce_rr2,
                    )
    run = {"name": v["name"], "config": v, "per_token": {}, "agg": {}}
    total_pnl = 0; total_n = 0; total_wins = 0; total_shorts = 0
    for sym, (ac, df_5m, df_15m) in data.items():
        t1 = time.time()
        trades = walk_forward_backtest(sym, ac, df_5m, df_15m, cfg)
        m = compute_metrics(trades, cfg.initial_capital)
        run["per_token"][sym] = m
        total_pnl += m["pnl_pct"]
        total_n += m["n_trades"]
        total_wins += int(m["wr"]/100 * m["n_trades"])
        total_shorts += int(m["shorts_pct"]/100 * m["n_trades"])
        print(f"  {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
              f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
              f"shorts={m['shorts_pct']:4.1f}% ({time.time()-t1:.0f}s)", flush=True)
    run["agg"] = {
        "n_trades": total_n,
        "wr": total_wins/total_n*100 if total_n else 0,
        "pnl_pct": total_pnl,
        "shorts_pct": total_shorts/total_n*100 if total_n else 0,
    }
    print(f"  AGG: n={total_n} WR={run['agg']['wr']:.1f}% "
          f"PnL={total_pnl:+.1f}% shorts={run['agg']['shorts_pct']:.1f}%", flush=True)
    all_results["variants"].append(run)
    with open(OUT_FILE, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

print("\n=== SUMMARY ===", flush=True)
for v in all_results["variants"]:
    marker = "✓" if v["agg"]["pnl_pct"] > 0 else "✗"
    print(f"  {marker} {v['name']:25s} n={v['agg']['n_trades']:4d} "
          f"WR={v['agg']['wr']:5.1f}% PnL={v['agg']['pnl_pct']:+7.1f}%", flush=True)
print(f"\nSaved to {OUT_FILE}", flush=True)
