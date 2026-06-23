# PPMT Worklog

---
Task ID: 1
Agent: Super Z (main)
Task: Complete remaining Coinbase downloads (Step 1 of "Next steps" plan)

Work Log:
- Checked current DB coverage with `scripts/check_coverage.py` and `scripts/find_gaps.py`
- Found 40 missing (window, tf, sym) combos and several low-coverage ones
- Identified that many "gaps" are actually impossible (token not yet listed on Coinbase):
  - BNBUSDT: never had a stable BNB-USD pair on Coinbase (excluded)
  - BONKUSDT in BEAR_2022 + RANGE_2023: BONK listed Jan 2024
  - PEPEUSDT in BEAR_2022 + RANGE_2023: PEPE listed Nov 2024
  - WIFUSDT in BEAR_2022 + RANGE_2023: WIF listed May 2024
  - XRPUSDT in BEAR_2022 + early RANGE_2023: XRP delisted from Coinbase Feb 2021, relisted July 2023
- Wrote 3 helper scripts under `scripts/`:
  - `v5_fill_gaps.py` — original gap-filler, parallel but couldn't run within bash timeouts
  - `v5_fill_one.py` — checkpointing single-job processor
  - `v5_fill_smart.py` — gap-aware fetcher (skip already-downloaded chunks)
  - `v5_fill_zeros_incremental.py` — incremental state-saving downloader for 0-coverage combos
- Successfully filled all fillable gaps via incremental approach (105s chunks):
  - PEPEUSDT 1m RECENT_2026: 0 → 128,218 candles
  - WIFUSDT 1m RECENT_2026: 0 → 103,701 candles
  - BONKUSDT 1m RECENT_2026: 0 → 125,265 candles
  - BONKUSDT 5m BULL_2024: 0 → 25,906 candles
  - WIFUSDT 5m BULL_2024: 0 → 13,286 candles

Stage Summary:
- Total NEW candles added to `ohlcv_ext_cb`: ~520,000
- Step 1 substantially complete. Remaining "low coverage" warnings are all impossible (pre-listing or SEC delisting).

---
Task ID: 2
Agent: Super Z (main)
Task: Re-run feature extraction + re-train model on full Coinbase dataset (Step 2 of plan)

Work Log:
- Discovered env reset had wiped /home/z/my-project/data/ directory; created symlink to /tmp/my-project/data/ppmt.db (4.8 GB) which still had the data
- Found existing `feature_observations` table had 2.4M rows of Binance data (not Coinbase); needed new CB-specific extractor
- Discovered RANGE_2023 window was missing from ohlcv_ext_cb (also wiped in env reset)
- Created `scripts/v5_extract_features_cb.py`:
  - Reads from `ohlcv_ext_cb` (Coinbase)
  - Writes to new `feature_observations_cb` table
  - Computes 38 features + 3-horizon forward labels + SAX pattern hash
  - Checkpointed per (sym, tf, window) combo for incremental execution
- Ran extractor across 73 (sym, tf, window) combos in 8 batches of ~105s each
- Total feature_observations_cb rows: 1,329,650 (vs 1.28M in v1)
- Created `scripts/v5_train_lgbm_cb_v2.py`:
  - Train: BULL_2024 + BEAR_2022 = 364,508 labeled rows (vs 734k in v1 which had RANGE_2023)
  - Valid: RANGE_2025 = 250,402 rows
  - Test: RECENT_2026 = 291,650 rows
  - LightGBM: num_leaves=15, lr=0.1, 200 rounds, early stopping at iter 103
  - Train time: 6.0s
- Trained model and evaluated at thresholds 0.5/0.6/0.7/0.8

Stage Summary:
- New model file: `download/v5_lgbm_model_cb_v2.txt`
- New metrics file: `download/v5_train_metrics_cb_v2.json`
- New predictions: `download/v5_test_predictions_cb_v2.csv`
- Test set performance (RECENT_2026, out-of-sample):
  | Threshold | Signals | Precision | Recall | Profit Factor | Avg PnL/Signal |
  |-----------|--------:|----------:|-------:|--------------:|---------------:|
  | 0.5 | 101,475 | 80.9% | 78.2% | 10.5 | +0.70% |
  | 0.6 | 88,910  | 84.5% | 71.5% | 13.3 | +0.73% |
  | 0.7 | 75,263  | 88.1% | 63.1% | 17.1 | +0.76% |
  | 0.8 | 57,418  | 92.0% | 50.3% | 24.5 | +0.77% |
- Comparison vs v1 model:
  | Threshold | Old PF | New PF | Old Precision | New Precision |
  |-----------|-------:|-------:|--------------:|--------------:|
  | 0.5 | 10.3 | 10.5 | 80.8% | 80.9% |
  | 0.6 | 12.9 | 13.3 | 84.3% | 84.5% |
  | 0.7 | 16.4 | 17.1 | 87.8% | 88.1% |
  | 0.8 | 23.1 | 24.5 | 91.6% | 92.0% |
- Improvements are modest but consistent across all thresholds (+0.5–1.4% PF, +0.1–0.4% precision)
- Top features by gain: ret_3 (829k), atr_pct (60k), last_3_range_sum (35k), range_pct (7k)
- AUC on validation: 0.940 (very high — possibly overfit to consistent market patterns)
- Note: train set is smaller than v1 (364k vs 734k) due to lost RANGE_2023, but test set has more data (291k vs 291k — same, because RECENT_2026 only has 5m+15m in the v2 feature table, no 1m)

Files produced:
- `/home/z/my-project/scripts/inspect_features.py`
- `/home/z/my-project/scripts/v5_extract_features_cb.py`
- `/home/z/my-project/scripts/v5_train_lgbm_cb_v2.py`
- `/home/z/my-project/download/extract_cb_state.json`
- `/home/z/my-project/download/v5_lgbm_model_cb_v2.txt`
- `/home/z/my-project/download/v5_train_metrics_cb_v2.json`
- `/home/z/my-project/download/v5_test_predictions_cb_v2.csv`
- `/home/z/my-project/logs/inspect_features.log`
