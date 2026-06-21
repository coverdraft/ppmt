# PPMT Motor Architecture v0.43.1

**Status**: CLOSED — Motor approved for V1. Terminal design phase begins.

**Date**: 2026-06-20

---

## Overview

PPMT (Probabilistic Pattern-Matching Trie) is a 4-level progressive pattern matching engine for crypto trading signals. It uses SAX (Symbolic Aggregate approXimation) to discretize price and volume data into symbols, then matches patterns across trie levels of increasing specificity using Bayesian confidence.

The core innovation is **Transfer Learning**: a token with no local history can still generate signals using shared universal and class-level pools.

---

## Trie Levels

| Level | Pool Key | SAX Encoding | Alphabet | Max Patterns | Purpose |
|-------|----------|-------------|----------|-------------|---------|
| **N1** | `__UNIVERSAL__` | Price-Only (SAXEncoder) | α=3 | 3^5 = 243 | Maximum density. Transfer learning across ALL tokens. |
| **N2** | `__CLASS_{class}__` | Price-Only (SAXEncoder) | α=3 | 3^5 = 243 | Refines shape by asset class. Shared across class members. |
| **N3** | `{SYMBOL/USDT}` | SAX Dual Price+Volume (SAXDualEncoder) | α_p=4, α_v=3 | 12^5 = 248,832 | Local validation with volume dimension. |
| **N4** | `{SYMBOL/USDT}:{regime}` | SAX Dual Price+Volume (SAXDualEncoder) | α_p=4, α_v=3 | 12^5 = 248,832 | Regime-sensitive local validation. |

### Key Design Decision: N1/N2 = Price-Only, N3/N4 = SAX Dual

**Why?** At the universal (N1) and class (N2) levels, volume is noise — different tokens have vastly different volume profiles. By encoding only price shape with α=3, we:
1. Cap the pattern space at 243 (manageable)
2. Achieve high density: ~153 obs/node at N1, ~35 obs/node at N2 (meme class)
3. Enable reliable Bayesian inference (prior strength=10 needs ~45 obs to overcome)

At the local levels (N3/N4), volume provides an independent validation signal. The dual encoding (price × volume) creates a larger pattern space but is justified because local pools accumulate observations from a single token over time.

---

## SAX Encoding Configuration

```python
LEVEL_ALPHA_CONFIG = {
    "n1": 3,           # Price-only, 3 symbols (a/b/c)
    "n2_meme": 3,       # Price-only
    "n2_new_launch": 3, # Price-only
    "n2_default": 3,    # Price-only
    "n3": 4,           # Price part of dual
    "n4": 4,           # Price part of dual
}

LEVEL_DUAL_ALPHA_CONFIG = {
    "n1": {"price": 3, "volume": 0},          # volume=0 → SAXEncoder (price-only)
    "n2_meme": {"price": 3, "volume": 0},      # price-only
    "n2_new_launch": {"price": 3, "volume": 0}, # price-only
    "n2_default": {"price": 3, "volume": 0},    # price-only
    "n3": {"price": 4, "volume": 3},            # dual (12-symbol combined alphabet)
    "n4": {"price": 4, "volume": 3},            # dual (12-symbol combined alphabet)
}
```

When `volume=0`, the engine uses `SAXEncoder` (produces list of strings like `['a','b','c','a','b']`).
When `volume>0`, the engine uses `SAXDualEncoder` (produces list of tuples like `[('a','x'),('b','y'),...]`).

---

## Window Sizes

| Timeframe | Window (W) | Rationale |
|-----------|-----------|-----------|
| 1m | 45 | ~45 minutes of data per SAX symbol |
| 5m | 18 | ~90 minutes (1.5h) of data per SAX symbol |

Pattern length is always **5** symbols.

---

## Weight Profiles

| Profile | N1 | N2 | N3 | N4 | Use Case |
|---------|----|----|----|----|---------|
| `default` | 10% | 30% | 30% | 30% | Established tokens with full local history |
| `meme` | 10% | 60% | 20% | 10% | Meme tokens (PEPE, DOGE, SHIB, WIF) |
| `new_launch` | 15% | 55% | 20% | 10% | Recently launched tokens |
| `blue_chip` | 5% | 20% | 35% | 40% | BTC, ETH — rely on local+regime data |

### Weight Redistribution

When N3/N4 have insufficient patterns (< 20 for N3, < 10 for N4), their weights redistribute proportionally to N1/N2 via `safe_default_weights()`.

When N2 is sparse (avg obs/node < 2.0), additional weight shifts from N2 to N1 to leverage the denser universal pool.

For PEPE OOS testing (N3=0, N4=0, N2 avg_obs=35.5):
- After redistribution: N1 ≈ 14.3%, N2 ≈ 85.7%, N3=0%, N4=0%

---

## Asset Classification

| Asset Class | Tokens | N2 Pool |
|------------|--------|---------|
| `blue_chip` | BTC/USDT, ETH/USDT | `__CLASS_blue_chip__` |
| `large_cap` | SOL/USDT, BNB/USDT, XRP/USDT | `__CLASS_large_cap__` |
| `mid_cap` | LINK/USDT, AVAX/USDT, DOT/USDT | `__CLASS_mid_cap__` |
| `meme` | DOGE/USDT, SHIB/USDT, WIF/USDT | `__CLASS_meme__` |
| `new_launch` | (reserved for future tokens) | `__CLASS_new_launch__` |

**PEPE/USDT** is classified as `meme` but is **excluded from the build** — used exclusively for OOS (out-of-sample) testing.

---

## Logic for New Tokens (Transfer Learning)

A token with zero local history (N3/N4 empty) operates **100% based on the geometric shape of price** via N1 (universal) and N2 (class-level) pools. The engine:

1. Encodes incoming candles using price-only SAX (α=3)
2. Matches the 5-symbol pattern against N1 (all tokens) and N2 (class peers)
3. Computes weighted confidence using redistributed weights (N3/N4 weight → N1/N2)
4. Generates direction (LONG/SHORT) from `expected_move_pct` of matched node
5. Sets SL/TP from `expected_move_pct` and `max_drawdown_pct` of matched node

Once the token accumulates sufficient candles, N3/N4 become active:
- N3 adds the **volume filter** — validates that volume pattern supports the price signal
- N4 adds **regime sensitivity** — adapts to market conditions

This two-phase approach (price-only → price+volume) is the core architectural insight.

---

## Bayesian Confidence

Each trie node maintains Bayesian statistics:

- **Prior strength**: 10 observations (uniform)
- **Confidence**: `successes / (successes + failures + prior)` where prior = 10
- **Needs ~45 obs/node** to overcome the prior and reach confidence > 0.6
- **Direction confidence**: Separate for LONG (`bayesian_wr_long`) and SHORT (`bayesian_wr_short`)

---

## Data Pipeline

### Build Phase
1. `bulk_downloader` — Downloads OHLCV data from Binance (90 days default)
2. `sequential_builder` — Processes all tokens:
   - Encodes each candle using level-appropriate SAX
   - Stores observations in N1 (universal), N2 (class), N3 (token), N4 (token+regime) pools
   - Persists tries to SQLite (`~/.ppmt/ppmt.db`)

### Query Phase (OOS / Live)
1. Load shared pools (N1, N2) from DB
2. Create PPMT engine for target token
3. Encode incoming data with `encode_all_levels()` — returns differentiated N1/N2/N3/N4 symbols
4. Match with `match_raw()` — returns per-level confidence + weighted confidence
5. Apply BTC context filter for regime awareness
6. Generate signal if confidence exceeds threshold

---

## Validated Results (PEPE OOS, 1m timeframe)

| Metric | Value |
|--------|-------|
| N1 patterns | 243 (max 243) |
| N2 meme patterns | 243 (max 243) |
| N1 avg confidence | 0.403 |
| N2 meme avg confidence | 0.320 |
| N1 max confidence | 0.511 |
| N2 meme max confidence | 0.455 |
| N1 density | ~153.8 obs/node |
| N2 meme density | ~35.5 obs/node |
| PEPE weighted_conf max | 0.463 |
| Steps with weighted_conf >= 0.40 | 312 |
| Transfer Learning | PEPE has no N3/N4 — operates purely on N1+N2 |

---

## File Structure

```
ppmt/
├── src/ppmt/
│   ├── core/
│   │   ├── sax.py          # SAXEncoder, SAXDualEncoder, LEVEL_*_CONFIG
│   │   ├── encoder.py      # Multi-level encoding orchestration
│   │   ├── trie.py         # PPMTTrie, TrieNode, BlockLifecycleMetadata
│   │   ├── matcher.py      # Pattern matching with fuzzy distance
│   │   ├── regime.py       # Market regime detection
│   │   └── ...
│   ├── engine/
│   │   ├── ppmt.py         # Main PPMT engine (4-level match, encode_all_levels)
│   │   ├── weights.py      # AdaptiveWeights, safe_default_weights
│   │   ├── signal.py       # Signal types
│   │   ├── buffer.py       # Rolling window buffer
│   │   └── ...
│   ├── data/
│   │   ├── storage.py      # SQLite persistence (tries, observations, OHLCV)
│   │   ├── sequential_builder.py  # Build pipeline
│   │   ├── bulk_downloader.py     # Data download from Binance
│   │   ├── classifier.py   # Asset classification
│   │   ├── groups.py       # Asset group definitions
│   │   └── ...
│   └── ...
├── scripts/
│   └── oos_pepe_replay.py  # OOS validation script
├── ARCHITECTURE_V1.md      # This file
├── TRAZABILIDAD.md         # Development traceability log
└── pyproject.toml          # Package configuration (v0.43.1)
```

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| v0.42.0 | 2026-06-19 | SAX Dual native tuples, Window Size fix (1m→W=45, 5m→W=18), SL/TP from expected_move |
| v0.43.0 | 2026-06-19 | SAX Stratification: N1 price-only (α=3, 243 patterns), N2/N3/N4 SAX Dual |
| v0.43.1 | 2026-06-20 | N2 ALL price-only (volume=0 for all N2 configs), SAXEncoder fallback when volume=0, 90-day meme data, PEPE OOS conf > 0.40 ✅ |
