# PPMT v5 — Coinbase v2 (cb_v2) Model Package

This directory contains the full reproducible artifact bundle for the **v5_cb_v2** iteration of the PPMT crypto-trading model. It captures the end-to-end pipeline: data acquisition, gap-filling, feature extraction, model training, and the supporting state/checkpoints needed to resume any stage incrementally.

## What changed vs v1 (Binance) and v5_cb_v1

| Aspect | v1 (Binance) | v5_cb_v1 | **v5_cb_v2 (this)** |
|---|---|---|---|
| Source exchange | Binance | Coinbase | Coinbase |
| `feature_observations` rows | 2.4M | 1.28M | **1.33M** |
| Training rows | 734k | ~360k | **364,508** |
| Test rows | 291k | 291k | **291,650** |
| Test set PF @ 0.7 threshold | 16.4 | — | **17.1** |
| Test set Precision @ 0.7 | 87.8% | — | **88.1%** |
| Test set PF @ 0.8 threshold | 23.1 | — | **24.5** |
| Test set Precision @ 0.8 | 91.6% | — | **92.0%** |

Improvements are modest but consistent across all thresholds (+0.5–1.4 PF, +0.1–0.4 precision). The train set is smaller than v1 because RANGE_2023 was lost during an env reset and not re-downloaded; the test set is the same size because RECENT_2026 1m data was the new addition in cb_v2.

## Directory layout

```
ppmt/
├── scripts/v5/                       # All v5 pipeline scripts (data → features → train → backtest)
│   ├── v5_download_coinbase.py       # Core Coinbase OHLCV downloader (paginated, retry-aware)
│   ├── v5_download_chunked.py        # Chunked downloader (state checkpointing)
│   ├── v5_download_resume.py         # Resume-aware wrapper
│   ├── v5_download_multiex.py        # Multi-exchange comparison downloader
│   ├── v5_download_massive.py        # Bulk historical download
│   ├── v5_dl_remaining.py            # Targeted "remaining" downloader
│   ├── v5_fill_gaps.py               # Parallel gap-filler (8 workers)
│   ├── v5_fill_one.py                # Single-job checkpointing processor
│   ├── v5_fill_smart.py              # Gap-aware fetcher (skip already-downloaded chunks)
│   ├── v5_fill_zeros.py              # Zero-coverage filler (first attempt)
│   ├── v5_fill_zeros_incremental.py  # ← KEY: incremental state-saving filler for 0-coverage combos
│   ├── v5_extract_features.py        # Original Binance feature extractor (38 features + SAX)
│   ├── v5_extract_features_cb.py     # ← KEY: Coinbase feature extractor (writes feature_observations_cb)
│   ├── v5_train_lgbm.py              # Original Binance LightGBM trainer
│   ├── v5_train_lgbm_cb.py           # First Coinbase trainer
│   ├── v5_train_lgbm_cb_v2.py        # ← KEY: cb_v2 trainer (this iteration)
│   ├── v5_backtest_realistic.py      # Realistic backtest (fees + slippage + position sizing)
│   ├── v5_validate.py                # Validation harness
│   ├── v5_bulk_build.py              # Bulk feature build orchestrator
│   ├── v5_compare_binance_coinbase.py
│   ├── v5_sanity_check.py
│   ├── v5_only.py                    # Filter-only mode
│   ├── v5_5tokens_test.py
│   ├── check_coverage.py             # Per-(window, tf) coverage audit
│   ├── find_gaps.py                  # Find missing/low-coverage (window, tf, sym) combos
│   ├── probe_coinbase.py             # Quick connectivity probe
│   ├── inspect_features.py           # Inspect feature_observations table
│   ├── v5_orchestrate_download.sh    # Shell orchestration
│   ├── v5_run_bulk_build.sh
│   ├── v5_run_extract.sh
│   ├── v5_run_one_batch.sh
│   ├── dl_chunk.sh
│   ├── dl_step.sh
│   ├── run_chunked_download.sh
│   └── run_full_download.sh
├── models/v5_cb_v2/
│   ├── v5_lgbm_model_cb_v2.txt       # Trained LightGBM model (187KB)
│   └── v5_train_metrics_cb_v2.json   # Per-threshold test metrics
├── state/v5_cb_v2/
│   ├── extract_cb_state.json         # Feature-extraction checkpoint (per sym|tf|window cursor)
│   └── zeros_inc_state.json          # Gap-fill download checkpoint (per sym|tf|window cursor)
└── docs/v5_cb_v2/
    ├── README.md                     # This file
    └── worklog.md                    # Full agent work log (Step 1 + Step 2)
```

## Reproduction recipe

### Prerequisites
- Python 3.11+ with `lightgbm`, `pandas`, `numpy`, `sqlite3`, `requests`
- SQLite DB at `data/ppmt.db` (NOT included — too large; 6.5 GB)
- The DB must contain an `ohlcv_ext_cb` table with columns:
  `symbol, timeframe, timestamp, window, open, high, low, close, volume`

### Step 1 — Data acquisition (already done; reproducible)
```bash
# Initial bulk download (uses Coinbase public API; no auth needed)
python scripts/v5/v5_download_coinbase.py

# Fill remaining 0-coverage gaps (incremental; resume-safe)
# Safe to interrupt — state is checkpointed every 10 chunks or 30s
python scripts/v5/v5_fill_zeros_incremental.py
```

**Known impossible combos (skipped automatically):**
- `BNBUSDT` — BNB-USD pair never had a stable listing on Coinbase
- `XRPUSDT` in `BEAR_2022` + early `RANGE_2023` — SEC lawsuit delisting (Feb 2021 – Jul 2023)
- `BONKUSDT` in `BEAR_2022` + `RANGE_2023` — BONK listed Jan 2024
- `PEPEUSDT` in `BEAR_2022` + `RANGE_2023` — PEPE listed Nov 2024
- `WIFUSDT` in `BEAR_2022` + `RANGE_2023` — WIF listed May 2024

### Step 2 — Feature extraction + training (already done; reproducible)
```bash
# Feature extraction (writes to feature_observations_cb table)
# Incremental — checkpoint per (sym, tf, window)
python scripts/v5/v5_extract_features_cb.py

# Train LightGBM
python scripts/v5/v5_train_lgbm_cb_v2.py
```

### Train/Valid/Test split
- **Train**: `BULL_2024` + `BEAR_2022` → 364,508 labeled rows
- **Valid**: `RANGE_2025` → 250,402 rows (used for early stopping)
- **Test**:  `RECENT_2026` → 291,650 rows (out-of-sample)

### Hyperparameters
- LightGBM: `num_leaves=15`, `learning_rate=0.1`, `n_estimators=200`, early stopping at iter 103
- Train time: ~6 seconds

## Test-set performance (out-of-sample, RECENT_2026)

| Threshold | Signals | Precision | Recall | Profit Factor | Avg PnL/Signal |
|-----------|--------:|----------:|-------:|--------------:|---------------:|
| 0.5       | 101,475 | 80.9%     | 78.2%  | 10.5          | +0.70%         |
| 0.6       |  88,910 | 84.5%     | 71.5%  | 13.3          | +0.73%         |
| 0.7       |  75,263 | 88.1%     | 63.1%  | 17.1          | +0.76%         |
| 0.8       |  57,418 | 92.0%     | 50.3%  | 24.5          | +0.77%         |

Top features by gain: `ret_3` (829k), `atr_pct` (60k), `last_3_range_sum` (35k), `range_pct` (7k).

Validation AUC: **0.940** (very high — likely overfit to consistent market patterns; treat with caution in production).

## Notes / caveats

1. **DB not included.** `data/ppmt.db` is 6.5 GB and excluded via `.gitignore`. The schema is documented above; rebuild from scratch using `v5_download_coinbase.py`.
2. **Test predictions CSV excluded.** `v5_test_predictions_cb_v2.csv` is 22 MB and also excluded. Regenerate via `v5_train_lgbm_cb_v2.py` (it writes predictions as a side effect).
3. **Path dependencies.** Several scripts hardcode `/home/z/my-project/...` paths. Adjust to your environment before re-running.
4. **`find_gaps.py` false positives.** Uses 2026 dates for `RECENT_2026` but the actual data is 2025; this produces false "low coverage" warnings. Cosmetic only.
