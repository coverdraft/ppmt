"""Final v2.3 config: V6 SL/TP + moderate filter tightening.
Target: push WR from 63% → 67%+ (break-even for RR=0.5) on all tokens.
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
OUT_FILE = "/home/z/my-project/scripts/v23_final_results.json"

data = {}
for sym, ac in TOKENS:
    df_5m = load_ohlcv(sym, "5m")
    df_15m = load_ohlcv(sym, "15m")
    data[sym] = (ac, df_5m, df_15m)
print("Data loaded (5 tokens).", flush=True)

# Final config: V6 SL/TP + moderate filter tightening
cfg = ConfigV23(
    reverse_direction=True,
    weights=(0.40, 0.20, 0.20, 0.20),
    chi2_p_threshold=0.20,        # moderate (was 0.30 in V6)
    min_node_count=10,             # moderate (was 8)
    min_dir_edge=0.15,             # moderate (was 0.10) — require 15% tilt
    alphas=(5, 7),
    min_alpha_agreement=2,
    sl_atr_mult=4.0,
    tp_atr_mult=2.0,
    sl_cap_pct=4.5,
    tp_cap_pct=3.0,
    sl_floor_pct=0.40,
    tp_floor_pct=0.30,
    enforce_rr2=False,
    max_hold_bars=48,
    use_multi_tf=True,
    risk_pct=0.02,
    min_confidence=0.08,           # moderate (was 0.05)
    hard_move_floor=0.08,          # moderate (was 0.05)
    fee_pct=0.04,
)
print(f"FINAL: chi2_p<{cfg.chi2_p_threshold} count>={cfg.min_node_count} "
      f"edge>{cfg.min_dir_edge} conf>{cfg.min_confidence}", flush=True)
print(f"SL={cfg.sl_atr_mult}×ATR TP={cfg.tp_atr_mult}×ATR RR={cfg.tp_atr_mult/cfg.sl_atr_mult:.2f}\n", flush=True)

results = {"name": "v23_final", "per_token": {}, "agg": {}}
total_pnl = 0; total_n = 0; total_wins = 0; total_shorts = 0
tokens_profitable = 0

for sym, (ac, df_5m, df_15m) in data.items():
    t1 = time.time()
    trades = walk_forward_backtest(sym, ac, df_5m, df_15m, cfg)
    m = compute_metrics(trades, cfg.initial_capital)
    results["per_token"][sym] = m
    total_pnl += m["pnl_pct"]
    total_n += m["n_trades"]
    total_wins += int(m["wr"]/100 * m["n_trades"])
    total_shorts += int(m["shorts_pct"]/100 * m["n_trades"])
    if m["pnl_pct"] > 0: tokens_profitable += 1
    marker = "✓" if m["pnl_pct"] > 0 else "✗"
    print(f"  {marker} {sym:10s} n={m['n_trades']:3d} WR={m['wr']:5.1f}% "
          f"PnL={m['pnl_pct']:+7.1f}% PF={m['pf']:.2f} "
          f"shorts={m['shorts_pct']:4.1f}% ({time.time()-t1:.0f}s)", flush=True)
    results["agg"] = {
        "n_trades": total_n,
        "wr": total_wins/total_n*100 if total_n else 0,
        "pnl_pct": total_pnl,
        "shorts_pct": total_shorts/total_n*100 if total_n else 0,
        "tokens_profitable": tokens_profitable,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)

print(f"\nFINAL AGG: n={total_n} WR={results['agg']['wr']:.1f}% "
      f"PnL={total_pnl:+.1f}% shorts={results['agg']['shorts_pct']:.1f}% "
      f"profitable={tokens_profitable}/5", flush=True)

# Compute Monte Carlo if profitable
if total_pnl > 0 and total_n > 30:
    from ppmt_grid_search import monte_carlo
    # Need to re-run to get trades for MC
    all_trades = []
    for sym, (ac, df_5m, df_15m) in data.items():
        trades = walk_forward_backtest(sym, ac, df_5m, df_15m, cfg)
        all_trades.extend(trades)
    mc = monte_carlo(all_trades, n_sims=3000, initial_capital=cfg.initial_capital, risk_pct=cfg.risk_pct)
    results["mc"] = mc
    print(f"MC: profit_prob={mc['mc_prob_profit']:.1f}% ruin={mc['mc_risk_ruin']:.2f}% "
          f"p95_dd={mc['mc_p95_dd']:.1f}%", flush=True)
    with open(OUT_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)

print("DONE", flush=True)
