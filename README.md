# PPMT — Progressive Pattern Matching for Crypto Trading

LightGBM + sectorial trie system for crypto trading on Coinbase Advanced.

**Current version:** v7 (in development)
**Previous stable:** v6 LONG-only (+11.4% ROI/5mo walk-forward, WR 72%, PF 1.86, Sharpe +6.38)

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

The system uses a SQLite database at `data/ppmt.db` (4.4GB, gitignored).

To populate from scratch:

```bash
python scripts/v6/v6_download_ohlcv.py --timeframe 5m
python scripts/v6/v6_download_ohlcv.py --timeframe 15m
```

### 3. Train v6 (current production)

```bash
python scripts/v6/v6_extract_features.py --timeframe 5m
python scripts/v6/v6_train_wf.py  # trains 5 walk-forward windows
python scripts/v6/v6_backtest_filtered.py
```

### 4. v7 (in development)

See `PPMT_v7_MASTER_PLAN.md` for the full plan.

---

## Repository structure

```
ppmt/
├── PPMT_v7_MASTER_PLAN.md    # v7 architecture & plan (READ THIS FIRST)
├── worklog.md                # execution history (append-only)
├── README.md                 # this file
├── config/
│   └── v7.yaml               # v7 runtime config
├── docs/
│   ├── v7/                   # v7 phase docs (added per phase)
│   ├── audit_alternative/    # legacy trie audit (N1-N4 analysis)
│   └── *.pdf                 # original PPMT technical docs
├── scripts/
│   ├── v6/                   # production v6 code (16 files)
│   └── v7/                   # new v7 code (added per phase F1-F13)
├── src/ppmt/                 # legacy code (v0.x trie + SAX, kept for reference)
├── data/                     # gitignored (4.4GB DB + models)
│   ├── ppmt.db               # OHLCV candles (5m + 15m, 12 tokens, ~1.4M rows)
│   ├── v6_models/            # trained v6 LightGBM models
│   └── v7_models/            # NEW v7 models
└── tests/
    └── v7/                   # v7 tests (added per phase)
```

---

## Versions

| Version | Status | Description |
|---------|--------|-------------|
| v6 | **PRODUCTION** | LightGBM regression, 5m, 59 features, LONG-only (+11.4% ROI/5mo) |
| v7 | IN DEVELOPMENT | Adds trie+ML hybrid, sector awareness, dual experts, online learning |

See `PPMT_v7_MASTER_PLAN.md` §2 for full version history.

---

## Documentation

- `PPMT_v7_MASTER_PLAN.md` — **the single source of truth** for v7
- `worklog.md` — execution history (every commit documented)
- `docs/v7/` — per-phase documentation (added as phases complete)
- `docs/audit_alternative/N1_N4_STRUCTURE_ANALYSIS.md` — legacy trie audit

---

## Contact

- Repo owner: coverdraft
- AI agent: super-z (GLM)
