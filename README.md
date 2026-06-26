# PPMT — Progressive Pattern Matching for Crypto Trading

LightGBM + sectorial trie system for crypto trading on Coinbase Advanced.

**Current version:** v12 (low-TF microstructure, 1m base, H=12)
**Previous stable:** v7.5 binary classification (Sharpe 2.80, PnL +333.76%)
**Legacy:** v6 LONG-only (+11.4% ROI/5mo walk-forward, WR 72%, PF 1.86, Sharpe +6.38)

---

## Quick start

### 1. Setup

```bash
git clone https://github.com/coverdraft/ppmt.git
cd ppmt
python -m venv venv
source venv/bin/activate
pip install -e .
```

### 2. Database (OHLCV candles)

The system uses a SQLite database at `data/ppmt.db` (4.4GB, gitignored) and
1m parquet cache at `data/v10/ohlcv_cache/`.

To populate from scratch:

```bash
python scripts/v6/v6_download_ohlcv.py --timeframe 5m
python scripts/v6/v6_download_ohlcv.py --timeframe 15m
```

### 3. V12 — Low-TF Microstructure Pipeline (current)

```bash
# Build 1m-based dataset with 80 microstructure features
python scripts/v11/v11_build_dataset.py

# Train binary classifiers per symbol × horizon
python scripts/v11/v11_train.py

# Optimize trading parameters (quantile thresholds, direction, trend filter)
python scripts/v12/v12_optimize.py

# Walk-forward validation of best configs
python scripts/v12/v12_validate.py

# Paper trading (see RUNBOOK_v12_paper_trading.md)
python -m scripts.v12.paper_trader --symbol SOL --once
```

### 4. v7 — 24h Horizon (alternative pipeline)

```bash
python scripts/v7/v7_train_v75.py
python scripts/v7/v7_backtest_v75.py
```

See `PPMT_v7_MASTER_PLAN.md` for the full plan.

---

## Repository structure

```
ppmt/
├── PPMT_v7_MASTER_PLAN.md           # v7 architecture & plan
├── ESTADO_PROYECTO.md               # Project status (Spanish)
├── worklog.md                       # execution history (append-only)
├── RUNBOOK_paper_trading.md         # v7 paper trading runbook
├── RUNBOOK_v12_paper_trading.md     # v12 paper trading runbook
├── README.md                        # this file
├── config/
│   └── v7.yaml                      # v7 runtime config
├── docs/
│   ├── v7/                          # v7 phase docs
│   ├── v12/                         # v12 optimization & validation docs
│   ├── audit_alternative/           # legacy trie audit (N1-N4 analysis)
│   └── *.pdf                        # original PPMT technical docs
├── scripts/
│   ├── v6/                          # production v6 code (16 files)
│   ├── v7/                          # v7 code (paper trader + rolling retrain)
│   │   └── paper_trader/            # v7 paper trading package
│   ├── v11/                         # V11 low-TF pipeline (5 files)
│   └── v12/                         # V12 optimization + paper trading (8+ files)
│       └── paper_trader/            # v12 paper trading package
├── src/ppmt/                        # legacy code (v0.x trie + SAX, kept for reference)
├── data/                            # gitignored (4.4GB DB + models + results)
│   ├── v10/ohlcv_cache/             # 1m OHLCV parquet files (18 symbols)
│   ├── v11/                         # V11 datasets + models
│   ├── v12/                         # V12 optimization + validation results
│   └── paper_trading/               # paper trading logs & state
└── tests/
    └── v7/                          # v7 tests
```

---

## Versions

| Version | Status | Timeframe | Features | WR | Key Result |
|---------|--------|-----------|----------|-----|------------|
| v6 | PRODUCTION | 5m, H=288 (24h) | 59 | 72% | +11.4% ROI/5mo, LONG-only |
| v7 | COMPLETED | 5m, H=288 (24h) | 58 | 64-72% | Sharpe 2.80, +333.76% PnL, 4/4 consistency |
| v7.5 | COMPLETED | 5m, H=288 (24h) | 71 | — | MaxDD -7.09%, deep optimization 5040 configs |
| v8 | COMPLETED | Pattern-based MEXC | — | 88%* | BREAKOUT_UP pattern (scalpel) |
| v9 | COMPLETED | Fixed v8 backtest | — | 38% | Poor — confirmed v8 overfit |
| v10 | COMPLETED | 1m microstructure | 80 | 39% | Dataset builder only, poor WR |
| v11 | COMPLETED | 1m→5m, H=12 (1h) | 80 | ~61% | Low-TF pipeline, microstructure features |
| **v12** | **CURRENT** | **1m→5m, H=12 (1h)** | **80** | **0.65-0.69** | **Optimized Q thresholds, 6/6 WF robust** |

*V8 WR is for a single pattern type, not overall trading.

### V12 Key Improvement

V12 = V11 model + optimized trading parameters. The model already had edge;
the breakthrough came from **quantile selectivity** (Q95/5, Q98/2) rather than
model architecture changes. WR improved from 0.39 (V10) to 0.693 (V12, SOL).

---

## Two Active Pipelines

| | V7 (24h Horizon) | V12 (1h Horizon) |
|---|---|---|
| Base timeframe | 5m candles | 1m candles → 5m bars |
| Prediction horizon | 24h (H=288) | 1h (H=12) |
| Features | 58 (price/volume) | 80 (includes microstructure) |
| Trading signal | Quantile Q82-95 | Quantile Q95-98 |
| Win rate | 64-72% | 65-69% |
| Consistency | 3-4/4 windows | 4-6/6 windows |
| Paper trading | `scripts/v7/paper_trader/` | `scripts/v12/paper_trader/` |
| Cost model | Maker 0.04% | Maker 0.04% |
| Short trades | Yes (essential) | Yes (both directions) |

---

## Documentation

- `ESTADO_PROYECTO.md` — **Project status and honest assessment** (Spanish)
- `PPMT_v7_MASTER_PLAN.md` — v7 architecture & plan
- `docs/v12/` — V12 optimization & validation documentation
- `worklog.md` — execution history (every commit documented)
- `RUNBOOK_v12_paper_trading.md` — V12 paper trading operational guide
- `RUNBOOK_paper_trading.md` — V7 paper trading operational guide
- `docs/audit_alternative/N1_N4_STRUCTURE_ANALYSIS.md` — legacy trie audit

---

## Contact

- Repo owner: coverdraft
- AI agent: super-z (GLM)
