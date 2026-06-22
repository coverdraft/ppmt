"""Quick test: hold=72 vs hold=48 on 4 tokens to see if longer hold improves WR."""
import sys, os, time, gc, logging
sys.path.insert(0, "/home/z/my-project/ppmt/src")
sys.path.insert(0, "/home/z/my-project/scripts")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")
import logging
logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

from ppmt_v25_hold48 import walk_forward_backtest_v25, ConfigV25, TOKENS
from ppmt_grid_search import compute_metrics

test_tokens = [
    ("BTC/USDT", "blue_chip"),
    ("ETH/USDT", "blue_chip"),
    ("SOL/USDT", "large_cap"),
    ("DOGE/USDT", "meme"),
    ("LINK/USDT", "mid_cap"),
]

print("=== hold_bars comparison: 48 vs 72 vs 96 ===\n", flush=True)
print(f"{'token':<10s} | hold=48 (WR PnL PF) | hold=72 (WR PnL PF) | hold=96 (WR PnL PF)", flush=True)
print("-" * 90)

for hold in [48, 72, 96]:
    pass  # just to organize

results_by_hold = {48: {}, 72: {}, 96: {}}
for hold in [48, 72, 96]:
    for sym, ac in test_tokens:
        cfg = ConfigV25(hold_bars=hold)
        t = time.time()
        trades, st = walk_forward_backtest_v25(sym, ac, cfg)
        m = compute_metrics(trades, cfg.initial_capital)
        results_by_hold[hold][sym] = m
        gc.collect()
        print(f"  hold={hold} {sym}: n={m['n_trades']} WR={m['wr']:.1f}% PnL={m['pnl_pct']:+.1f}% PF={m['pf']:.2f} ({time.time()-t:.0f}s)", flush=True)

print("\n=== SUMMARY ===")
print(f"{'token':<10s} | hold=48 (WR PnL PF) | hold=72 (WR PnL PF) | hold=96 (WR PnL PF)")
print("-" * 90)
for sym, _ in test_tokens:
    m48 = results_by_hold[48][sym]
    m72 = results_by_hold[72][sym]
    m96 = results_by_hold[96][sym]
    print(f"{sym:<10s} | {m48['wr']:4.0f}% {m48['pnl_pct']:+6.1f}% {m48['pf']:4.2f} "
          f"| {m72['wr']:4.0f}% {m72['pnl_pct']:+6.1f}% {m72['pf']:4.2f} "
          f"| {m96['wr']:4.0f}% {m96['pnl_pct']:+6.1f}% {m96['pf']:4.2f}")

# Aggregate
print("\n=== AGGREGATE ===")
for hold in [48, 72, 96]:
    results = results_by_hold[hold]
    total_pnl = sum(m['pnl_pct'] for m in results.values())
    avg_wr = sum(m['wr'] for m in results.values()) / len(results)
    avg_pf = sum(m['pf'] for m in results.values()) / len(results)
    print(f"  hold={hold}: agg_pnl={total_pnl:+.1f}% avg_wr={avg_wr:.1f}% avg_pf={avg_pf:.2f}")
