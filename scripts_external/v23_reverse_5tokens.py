"""Verify the REVERSE direction trick on all 5 tokens, 14d OOS each.

If WR > 55% and PnL > 0 on 4+ tokens, the reverse trick is real and we
can build v2.3-reverse as the production strategy.
"""
import sys, os, time
sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ppmt/src")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")

import logging
logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

import ppmt_v23_combined as v23
from ppmt_v23_combined import ConfigV23, walk_forward_backtest
from ppmt_grid_search import load_ohlcv, compute_metrics, TOKENS

v23.OOS_DAYS = 14  # 14d OOS for speed

cfg = ConfigV23(
    name="reverse_v23",
    weights=(0.40, 0.20, 0.20, 0.20),
    chi2_p_threshold=0.30,
    min_node_count=8,
    use_alpha_ensemble=True,
    alphas=(5, 7),
    min_alpha_agreement=2,
    sl_atr_mult=1.0,
    tp_atr_mult=2.0,
    min_confidence=0.05,
    hard_move_floor=0.05,
    min_dir_edge=0.10,
    use_multi_tf=True,
    risk_pct=0.02,
    max_hold_bars=24,
)

t0 = time.time()
print("=== v2.3 REVERSE DIRECTION — 5 tokens × 14d OOS ===\n", flush=True)

total_pnl = 0
total_n = 0
total_wins = 0
total_shorts = 0
tokens_profitable = 0
per_token = {}

for sym, ac in TOKENS:
    t1 = time.time()
    df_5m = load_ohlcv(sym, "5m")
    df_15m = load_ohlcv(sym, "15m")
    trades = walk_forward_backtest(sym, ac, df_5m, df_15m, cfg)
    # REVERSE: swap PnL sign for each trade (simulates flipping direction at entry)
    for t in trades:
        t.pnl_pct = -t.pnl_pct
    m = compute_metrics(trades, cfg.initial_capital)
    per_token[sym] = m
    total_pnl += m["pnl_pct"]
    total_n += m["n_trades"]
    total_wins += int(m["wr"]/100 * m["n_trades"])
    total_shorts += int(m["shorts_pct"]/100 * m["n_trades"])
    if m["pnl_pct"] > 0:
        tokens_profitable += 1
    marker = "✓" if m["pnl_pct"] > 0 else "✗"
    print(f"  {marker} {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
          f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
          f"shorts={m['shorts_pct']:4.1f}% ({time.time()-t1:.0f}s)", flush=True)

print(f"\n=== AGGREGATE ===", flush=True)
print(f"  Tokens profitable: {tokens_profitable}/5", flush=True)
print(f"  Total trades:      {total_n}", flush=True)
print(f"  Aggregate WR:      {total_wins/total_n*100 if total_n else 0:.1f}%", flush=True)
print(f"  Aggregate PnL:     {total_pnl:+.1f}%", flush=True)
print(f"  Aggregate shorts:  {total_shorts/total_n*100 if total_n else 0:.1f}%", flush=True)
print(f"\nTotal time: {time.time()-t0:.0f}s", flush=True)
