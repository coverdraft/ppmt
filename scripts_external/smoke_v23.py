"""Smoke test v2.3 on 1 token only (SOL/USDT) to verify it runs end-to-end."""
import sys, os, time
sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ppmt/src")
os.environ.setdefault("PPMT_LOG_LEVEL", "WARNING")

import logging
logging.basicConfig(level=logging.WARNING)
for name in ["ppmt", "ppmt.engine", "ppmt.core", "ppmt.data"]:
    logging.getLogger(name).setLevel(logging.WARNING)

from ppmt_v23_combined import (
    ConfigV23, walk_forward_backtest, IS_DAYS, OOS_DAYS, CANDLES_PER_DAY_5M
)
from ppmt_grid_search import load_ohlcv, compute_metrics, TOKENS

t0 = time.time()
print("=== SMOKE TEST v2.3 — SOL/USDT only ===\n", flush=True)

# Reduce OOS to 7d for smoke test (faster)
import ppmt_v23_combined as v23
v23.OOS_DAYS = 7  # type: ignore

sym = "SOL/USDT"
ac = "large_cap"
df_5m = load_ohlcv(sym, "5m")
df_15m = load_ohlcv(sym, "15m")
print(f"  5m: {len(df_5m)} candles | 15m: {len(df_15m)} candles", flush=True)

cfg = ConfigV23(name="smoke_v23")
print(f"\nConfig: IS={IS_DAYS}d, OOS walk-forward=7d (smoke)\n", flush=True)

t1 = time.time()
trades = walk_forward_backtest(sym, ac, df_5m, df_15m, cfg)
m = compute_metrics(trades, cfg.initial_capital)
print(f"\n=== SOL/USDT SMOKE RESULT ===", flush=True)
print(f"  n_trades: {m['n_trades']}", flush=True)
print(f"  WR:       {m['wr']:.1f}%", flush=True)
print(f"  PnL:      {m['pnl_pct']:+.1f}%", flush=True)
print(f"  PF:       {m['pf']:.2f}", flush=True)
print(f"  shorts:   {m['shorts_pct']:.1f}%", flush=True)
print(f"  Time:     {time.time()-t1:.0f}s", flush=True)
print(f"  Total:    {time.time()-t0:.0f}s", flush=True)
