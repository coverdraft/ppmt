"""Smoke test: 1 token × 1 config to validate end-to-end and measure speed."""
import sys, time
sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt_grid_search import (
    load_ohlcv, build_engine, backtest, compute_metrics, BacktestConfig, TOKENS, TF
)

t0 = time.time()
print(f"Loading {TOKENS[0][0]} {TF}...")
df = load_ohlcv(TOKENS[0][0], TF)
print(f"  loaded {len(df)} candles in {time.time()-t0:.1f}s")

# Split IS/OOS
is_df = df.iloc[:-30*288]
oos_df = df.iloc[-30*288:].reset_index(drop=True)
print(f"  IS={len(is_df)} OOS={len(oos_df)}")

# Build engine
t1 = time.time()
print(f"Building engine (α=5)...")
engine = build_engine(TOKENS[0][0], TOKENS[0][1], is_df, alpha_n3n4=5)
n3 = engine.trie_n3.pattern_count if engine.trie_n3 else 0
n4 = engine.trie_n4.pattern_count if hasattr(engine.trie_n4, 'pattern_count') else 0
n1 = engine.trie_n1.pattern_count if engine.trie_n1 else 0
print(f"  built in {time.time()-t1:.1f}s. N1={n1} N3={n3} N4={n4}")

# Backtest with baseline Config F
t2 = time.time()
cfg = BacktestConfig(name="F_baseline", weights=(0.10, 0.00, 0.90, 0.00),
                     ev_threshold=0.20, sl_multiplier=2.0,
                     hard_move_floor=0.10, min_confidence=0.08)
trades = backtest(engine, oos_df, cfg, TOKENS[0][0])
print(f"  backtest in {time.time()-t2:.1f}s → {len(trades)} trades")

if trades:
    metrics = compute_metrics(trades)
    print(f"  metrics: WR={metrics['wr']:.1f}% PnL={metrics['pnl_pct']:+.1f}% "
          f"PF={metrics['pf']:.2f} shorts={metrics['shorts_pct']:.1f}%")
    print(f"  first 5 trades:")
    for t in trades[:5]:
        print(f"    {t.direction:5s} entry={t.entry_price:.2f} exit={t.exit_price:.2f} "
              f"sl={t.sl_price:.2f} tp={t.tp_price:.2f} pnl={t.pnl_pct:+.3f}% "
              f"reason={t.exit_reason} conf={t.weighted_confidence:.3f}")

print(f"\nTotal time: {time.time()-t0:.1f}s")
