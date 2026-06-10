# PPMT - Progressive Pattern Matching Trie

Autonomous pattern-based trading engine using a novel 4-level Trie architecture with SAX symbolization, Block Lifecycle Metadata, and fuzzy matching.

## Architecture

### 4-Level Trie
| Level | Scope | Weight | Purpose |
|-------|-------|--------|---------|
| N1 | Universal | 10% | Cross-asset universal patterns |
| N2 | Asset Class | 30% | Group patterns (Blue Chip, Meme, DeFi...) |
| N3 | Per-Asset | 30% | Asset-specific patterns |
| N4 | Per-Asset+Regime | 30% | Highest specificity |

### Block Lifecycle Metadata
Each Trie node carries 12 metadata fields:
- `trigger_candle` - Which candle activates the pattern
- `remaining_candles` - Predicted candles left
- `expected_move_pct` - Expected price movement
- `max_drawdown_pct` - Maximum observed drawdown
- `max_favorable_pct` - Maximum favorable excursion
- `win_rate` - Historical win rate
- `avg_duration` - Average pattern duration
- `sl_price` - Stop loss level
- `tp_price` - Take profit level
- `continuation_nodes` - Nodes that continue the pattern
- `break_nodes` - Nodes that break the pattern
- `historical_count` - Number of historical occurrences

### Key Innovations
- **Unknown Block = Predictive Exit**: If next SAX block doesn't exist in Trie, pattern broke → exit signal
- **Dead Asset Knowledge Transfer**: Patterns from dead assets persist in Asset Class Trie
- **O(k) Search**: Sub-microsecond regardless of total patterns
- **Autonomous**: Only needs external Capital Risk Manager
- **V7.9 Normalization Consistency**: Training z-score stats propagate to test encoding

## Installation

```bash
# Clone
git clone <repo-url>
cd ppmt

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac

# Install
pip install -e .
```

## Quick Start

```bash
# Initialize database and configuration
ppmt init

# Ingest historical data for an asset
ppmt ingest --symbol BTC/USDT --timeframe 1h --days 365

# Build the Trie from ingested data
ppmt build --symbol BTC/USDT

# Static walk-forward backtest (70/30 split)
ppmt backtest -s BTC/USDT --csv data.csv

# Rolling walk-forward backtest (V8.0)
ppmt rolling-backtest -s BTC/USDT --csv data.csv --train-candles 4000 --test-candles 1000 --step-candles 500

# Run real-time pattern matching
ppmt run --symbol BTC/USDT --timeframe 1h

# Query pattern statistics
ppmt stats --symbol BTC/USDT
```

## Experiment Log

### V7.5 — Signal Quality Filter
- Added min_rr, min_directional_count, min_confidence thresholds
- Result: Filters reduced trade count significantly but also filtered good trades

### V7.6 — Filter Tuning
- Tested various threshold combinations
- Key finding: Any filter threshold > 0 reduced total P&L

### V7.7 — Sizing vs Binary Rejection
- Replaced binary signal rejection with sizing_multiplier
- sizing_signal drives position size instead of yes/no
- Result: Better risk-adjusted returns without losing signals

### V7.8 — Cross-Symbol Test
- Tested on ETH/USDT and SOL/USDT
- **BUG FOUND**: ETH=1 trade, SOL=0 trades in backtest
- Root cause: SAX normalization mismatch between train and test

### V7.9 — Normalization Fix (CRITICAL)
- **Bug**: `engine.build(train_df)` encoded with train-only z-scores, but `encoder.encode(df)` used global z-scores
- For BTC with stable stats this was harmless, but ETH/SOL regime shifts caused different symbols that never matched the trie
- **Fix**: Added `encode_with_normalization()` method to SAXEncoder
- Training z-score stats (paa_mean, paa_std) propagate to test encoding
- Added `symbols` parameter to `engine.build()` to skip re-encoding
- **Results on user's Mac (real data)**:
  - BTC: 97 trades, 67% WR, +$629
  - ETH: 100 trades, 64% WR, +$800
  - SOL: 101 trades, 71.3% WR, +$2,496
- **Key observation**: SHORT signals very strong across all symbols (80-88% WR)

### V8.0 — Rolling Walk-Forward Backtest
- **Purpose**: Validate robustness across market regimes, not just a single static split
- **Method**: Train on sliding window, test on next window, slide forward
- **Default config**: 4000 candle train / 1000 candle test / 500 candle step
- **Matching improvements**:
  - Searches all 4 trie levels (N3→N2→N1)
  - Uses fuzzy matching for noise tolerance
  - Falls back to prefix search (shorter patterns) if full match not found
- **Synthetic data results** (2 years, 1h candles, 26 windows):
  - BTC: 24 trades, 58.3% WR, +191.58% P&L, 81% profitable windows
  - ETH: 26 trades, 46.2% WR, +126.98% P&L, 40% profitable windows
  - SOL: 30 trades, 50.0% WR, -206.05% P&L, 41% profitable windows
- **Note**: Synthetic data has different characteristics than real markets; user's Mac results with real data showed much stronger performance
- **Next**: Run with real market data on user's Mac for definitive validation

## CLI Commands

| Command | Description |
|---------|-------------|
| `ppmt init` | Initialize database and config |
| `ppmt ingest -s BTC/USDT` | Fetch historical data |
| `ppmt build -s BTC/USDT` | Build Trie from stored data |
| `ppmt backtest -s BTC/USDT --csv data.csv` | Static walk-forward backtest |
| `ppmt rolling-backtest -s BTC/USDT --csv data.csv` | Rolling walk-forward backtest |
| `ppmt predict -s BTC/USDT` | Show current prediction |
| `ppmt stats -s BTC/USDT` | Show pattern statistics |
| `ppmt list` | List tracked assets |

## Project Structure

```
ppmt/
├── src/ppmt/
│   ├── core/           # SAX, Trie, Delta Encoder, Fuzzy Matcher, Metadata
│   ├── engine/         # PPMT Engine, Adaptive Weights, Signal Generator
│   ├── data/           # Data Collection, Storage, Asset Classification
│   ├── risk/           # Capital Risk Manager
│   └── cli/            # Command Line Interface
├── tests/              # Unit & integration tests
├── config/             # Configuration files
└── docs/               # Documentation & PDF spec
```

## Key Findings

1. **Signal quality filters are counterproductive** — sizing-based quality control is superior
2. **SAX normalization consistency is critical** — training stats must propagate to test encoding
3. **SHORT signals are very strong** — 80-88% WR across BTC, ETH, SOL (V7.9 real data)
4. **Fuzzy matching significantly increases trade count** without degrading win rate
5. **Rolling walk-forward reveals regime sensitivity** — some windows are unprofitable, confirming the need for regime detection

## License

Proprietary - All rights reserved.
