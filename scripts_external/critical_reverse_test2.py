"""Critical reverse test — writes results to file to avoid stdout buffering issues."""
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

v23.OOS_DAYS = 60  # Full 60d walk-forward (covers last 60d, matching v2.2's OOS region)
OUT_FILE = "/home/z/my-project/scripts/critical_results.json"

results = {"runs": []}

print("=== CRITICAL TEST reverse=True vs False, 30d × 5 tokens ===", flush=True)

# Load data once
data = {}
for sym, ac in TOKENS:
    df_5m = load_ohlcv(sym, "5m")
    df_15m = load_ohlcv(sym, "15m")
    data[sym] = (ac, df_5m, df_15m)
print("Data loaded.", flush=True)

for reverse in [False, True]:
    cfg = ConfigV23(reverse_direction=reverse)
    print(f"\n--- reverse_direction={reverse} ---", flush=True)
    run = {"reverse": reverse, "per_token": {}, "agg": {}}
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
    results["runs"].append(run)
    # Save incrementally
    with open(OUT_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)

print("\n=== DONE ===", flush=True)
print(f"Saved to {OUT_FILE}", flush=True)
