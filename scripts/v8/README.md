# v8 Pattern-Based — Low-TF Multi-Token Trading System

Based on REAL pattern analysis of 269 matched trades from MEXC order history.

## Key Findings

| Pattern | N | WR | PnL | PF | Verdict |
|---------|---|-----|-----|-----|---------|
| BREAKOUT_UP | 76 | 88.2% | +156.0 | 3.10 | THE EDGE |
| BREAKOUT_DOWN | 184 | 66.3% | -263.7 | 0.65 | THE HOLE |
| EMA_BOUNCE | 5 | 60.0% | +3.6 | 3.04 | Promising |

## Modules

- `labels.py` — EV regression labels with ATR-adaptive TP/SL + 30min time stop
- `features.py` — 61 pattern-informed features (12 groups, including G5 Breakout Context + G6 Trend Alignment)
- `validation.py` — Purged K-Fold + Embargo + Walk-Forward + CPCV
- `model.py` — LightGBM regression on EV labels with sample weighting
- `backtest.py` — Realistic backtest with hard rules from pattern analysis
- `engine.py` — Live trading engine (max 3 entries, averaging UP only)
- `runner.py` — CLI entry point for train/validate/backtest

## Pattern Analysis

See `pattern_analysis/` for the trader pattern analysis script and results.

## Quick Start

```bash
# Validate (train + CV + backtest)
python -m scripts.v8.runner --mode validate --days 90

# Train production model
python -m scripts.v8.runner --mode train --days 180
```
