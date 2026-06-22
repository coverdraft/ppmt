"""Test RR=1 (SL=2ATR, TP=2ATR). Break-even at WR=50%, we're at 59% → should profit."""
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
OUT_FILE = "/home/z/my-project/scripts/rr1_results.json"

data = {}
for sym, ac in TOKENS:
    df_5m = load_ohlcv(sym, "5m")
    df_15m = load_ohlcv(sym, "15m")
    data[sym] = (ac, df_5m, df_15m)
print("Data loaded (5 tokens).", flush=True)

# RR=1 config: SL=2ATR, TP=2ATR
cfg = ConfigV23(
    reverse_direction=True,
    sl_atr_mult=2.0,
    tp_atr_mult=2.0,
    sl_cap_pct=2.5,
    tp_cap_pct=2.5,
    sl_floor_pct=0.25,
    tp_floor_pct=0.25,
    enforce_rr2=False,
    max_hold_bars=30,
)
print(f"RR=1: SL={cfg.sl_atr_mult}×ATR TP={cfg.tp_atr_mult}×ATR", flush=True)
print(f"reverse={cfg.reverse_direction} max_hold={cfg.max_hold_bars}\n", flush=True)

results = {"name": "RR1_sl2_tp2", "per_token": {}, "agg": {}}
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

print(f"\nRR=1 AGG: n={total_n} WR={results['agg']['wr']:.1f}% "
      f"PnL={total_pnl:+.1f}% shorts={results['agg']['shorts_pct']:.1f}%", flush=True)
print("DONE", flush=True)
