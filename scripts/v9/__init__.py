"""
v9 — Supervised Trader Clone

KEY INSIGHT: The trader has edge in ENTRY (80% WR after filtering big losses),
but loses money through bad EXIT management (not cutting losers, over-DCAing).

STRATEGY:
  1. Train a classifier on the trader's ACTUAL entries vs random bars
  2. Use 1m OHLCV (the trader decides on 1m)
  3. Filter: remove trades with loss > $5 (bad exits, not bad entries)
  4. Backtest with MECHANICAL exits (strict SL, time stop, no DCA)

PIPELINE:
  Step 1: parse_trades.py    — Parse XLSX → filtered trades
  Step 2: build_dataset.py   — Download 1m data + build labeled dataset
  Step 3: train.py           — Train binary classifier
  Step 4: backtest.py        — Backtest with mechanical exits
"""
