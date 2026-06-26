# V8 Pattern Analysis — Trader Visual Pattern Recognition

Reverse-engineers the trader's visual pattern recognition from MEXC order history.

## What it does

1. Parses the MEXC futures order history XLSX (Spanish columns, UTC+2 timestamps)
2. Matches open/close orders into trades with FIFO + DCA detection
3. Downloads 5m OHLCV for top 30 symbols (Binance → MEXC fallback, with disk cache)
4. Computes ~30 pattern detector features per trade entry
5. Classifies each entry into a visual pattern type
6. Measures WR, PnL, Profit Factor per pattern and pattern group
7. Outputs Excel + JSON with full results

## Quick Start

```bash
# From this directory:
cd scripts/v8/pattern_analysis

# Install dependencies
pip3 install pandas openpyxl ccxt numpy scikit-learn

# Run (XLSX is included in this directory)
python3 trader_pattern_analysis_v2.py
```

Or point to a custom XLSX:
```bash
python3 trader_pattern_analysis_v2.py ~/Downloads/my_orders.xlsx
```

## Output

All results go to `./output/`:
- `trader_pattern_analysis.xlsx` — Full entry data + pattern stats + group stats
- `trader_pattern_analysis.json` — Summary with key features per pattern group

OHLCV cache goes to `./ohlcv_cache/` (parquet files, safe to delete).

## Patterns Detected

| Category | Patterns |
|----------|----------|
| BREAKOUT | Breakout up/down with volume confirmation |
| EMA BOUNCE | Price bouncing off EMA21/EMA50 in trend direction |
| PULLBACK | Pullback within established trend |
| SQUEEZE | Bollinger Band compression → expansion |
| V-REVERSAL | Sharp V-shaped reversal |
| ENGULFING | Bullish/bearish engulfing candles |
| REJECTION CANDLE | Hammer, shooting star, pin bars |
| MOMENTUM IMPULSE | Z-score momentum spike |
| LEVEL TEST | Price near 50-bar high/low support/resistance |

## Next Steps

After running, share the `output/` folder contents. The results will feed directly into:
- `v8/features.py` — Encode discovered patterns as ML features
- `v8/labels.py` — Regression EV labels with ATR-adaptive TP/SL
- `v8/model.py` — LightGBM with -EV loss
