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

## Installation

```bash
# Clone
git clone <repo-url>
cd ppmt

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install
pip install -e ".[dev]"
```

## Quick Start

```bash
# Initialize database and configuration
ppmt init

# Ingest historical data for an asset
ppmt ingest --symbol BTC/USDT --timeframe 1h --days 365

# Build the Trie from ingested data
ppmt build --symbol BTC/USDT

# Run real-time pattern matching
ppmt run --symbol BTC/USDT --timeframe 1h

# Query pattern statistics
ppmt stats --symbol BTC/USDT
```

## Project Structure

```
ppmt/
├── src/ppmt/
│   ├── core/           # SAX, Trie, Delta Encoder, Fuzzy Matcher
│   ├── engine/         # PPMT Engine, Adaptive Weights, Signal Generator
│   ├── data/           # Data Collection, Storage, Asset Classification
│   ├── risk/           # Capital Risk Manager
│   └── cli/            # Command Line Interface
├── tests/              # Unit & integration tests
├── config/             # Configuration files
└── docs/               # Documentation & PDF spec
```

## License

Proprietary - All rights reserved.
