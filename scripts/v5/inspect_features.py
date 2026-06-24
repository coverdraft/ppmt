#!/usr/bin/env python3
"""Inspect feature_observations table to plan re-extraction."""
import sqlite3

DB = "/home/z/my-project/data/ppmt.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

print("=== feature_observations ===")
cur.execute("SELECT COUNT(*) FROM feature_observations")
print(f"Total: {cur.fetchone()[0]:,}")
print()

print("By historical_regime:")
cur.execute("""
    SELECT historical_regime, COUNT(*) as n,
           COUNT(DISTINCT symbol) as n_sym,
           COUNT(DISTINCT timeframe) as n_tf,
           SUM(CASE WHEN label_hit_tp_first IS NOT NULL THEN 1 ELSE 0 END) as n_labeled
    FROM feature_observations
    GROUP BY historical_regime
    ORDER BY n DESC
""")
print(f"  {'Regime':<15} {'Rows':<10} {'Syms':<6} {'TFs':<5} {'Labeled':<10}")
for r, n, ns, nt, nl in cur.fetchall():
    print(f"  {r:<15} {n:<10,} {ns:<6} {nt:<5} {nl:<10,}")
print()

print("By symbol:")
cur.execute("""
    SELECT symbol, COUNT(*) as n, COUNT(DISTINCT historical_regime) as n_reg
    FROM feature_observations
    GROUP BY symbol ORDER BY n DESC
""")
print(f"  {'Symbol':<12} {'Rows':<10} {'Regimes':<8}")
for s, n, nr in cur.fetchall():
    print(f"  {s:<12} {n:<10,} {nr:<8}")
print()

print("By timeframe:")
cur.execute("""
    SELECT timeframe, COUNT(*) as n
    FROM feature_observations GROUP BY timeframe ORDER BY n DESC
""")
for tf, n in cur.fetchall():
    print(f"  {tf}: {n:,}")
print()

# Check what's in ohlcv_ext_cb vs feature_observations per (sym, tf, window)
print("Coverage comparison (ohlcv_ext_cb has data, feature_observations does not):")
print(f"  {'Sym':<10} {'TF':<5} {'Window':<14} {'Candles':<10} {'Features':<10}")
cur.execute("""
    SELECT c.symbol, c.timeframe, c.window, COUNT(*) as candles,
           (SELECT COUNT(*) FROM feature_observations f
            WHERE f.symbol = c.symbol AND f.timeframe = c.timeframe
              AND f.historical_regime = c.window) as feats
    FROM ohlcv_ext_cb c
    GROUP BY c.symbol, c.timeframe, c.window
    HAVING feats = 0 OR feats < candles / 10
    ORDER BY candles DESC
""")
for sym, tf, win, candles, feats in cur.fetchall():
    print(f"  {sym:<10} {tf:<5} {win:<14} {candles:<10,} {feats:<10,}")
print()
conn.close()
