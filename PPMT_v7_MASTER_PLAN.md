# PPMT v7 — Master Plan & Architecture Document

**Status:** ACTIVE — implementation phase
**Created:** 2026-06-24
**Last updated:** 2026-06-24
**Author:** super-z (with coverdraft)
**Repository:** https://github.com/coverdraft/ppmt
**Branch:** main

---

## 0. How to use this document

This is the **single source of truth** for PPMT v7. If any agent (human or AI) is lost, they should:

1. Read this document completely (sections 1-12)
2. Read `worklog.md` for execution history
3. Check `/home/z/my-project/scripts/v7/` for current code
4. Check `/home/z/my-project/data/v7_models/` for trained models
5. Check `/home/z/my-project/config/v7.yaml` for runtime config

**If something in this document contradicts code, the document wins** (fix the code, then update the doc).

---

## 1. Executive summary

PPMT v7 is the next-generation crypto trading system that combines:

- **LightGBM dual-expert regression** (LONG expert + SHORT expert, both regression on `fwd_ret_15m`)
- **Sector-aware trie v2** (4 sectorial tries: blue_chip, large_cap, old_meme, new_meme — NO SAX, NO LONG/SHORT classification, NO N3/N4/N5 hierarchy)
- **Online learning in 3 layers** (trie updates per candle, rolling retrain every 6h, walk-forward validation monthly)
- **Adaptive SL/TP manager** (inter-trade decision: maintain, breakeven, or close based on prediction delta)
- **Circuit breakers** (auto-pause on drawdown, drift, or extreme funding rate)

**Goal:** LONG maintains +11% ROI/5mo (v6 baseline) while SHORT unlocks to WR > 52% (currently 45%).

---

## 2. History — what came before v7

| Version | Status | Why it died / what we learned |
|---------|--------|-------------------------------|
| v0.x (trie + SAX) | DEAD | N1-N4 tries were structurally identical. SAX discretization lost information. AUC illusory due to label leakage in `simulate_first_touch`. |
| v2.5 (always-reverse + hold=48) | DEAD | Worked on Binance data, doesn't generalize. Mean-reversion edge required 4h hold — incompatible with 5m microstructure on Coinbase. |
| v5_cb_v2 (LightGBM classification) | DEAD | AUC 0.94 was **data leakage**. Real AUC after fix: 0.54. Walk-forward validated but the binary TP/SL label was intrinsically leaky. Documented in `docs/v5_cb_v2/v5_leakage_postmortem.md`. |
| v6 (LightGBM regression, 5m) | **PRODUCTION** | Walk-forward +11.4% ROI/5mo, WR 72%, PF 1.86, Sharpe +6.38. LONG-only because SHORT lost -$6,510. |
| v6 Fase 1 (more bear data) | FAILED | 5m crypto is random walk in all regimes. "Bear" windows at 5m don't have more red bars. |
| v6 Fase 3 (15m TF re-download) | PARTIAL | SHORT losses cut by 98% (-$6,510 → -$109). Combined LONG+SHORT first time positive (+$230). But 5m LONG-only still beats 15m combined in 2025 bull regime. |
| **v7** | **IN PROGRESS** | This document. Adds trie+ML hybrid, sector awareness, dual experts, online learning, adaptive SL/TP. |

---

## 3. Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PPMT v7 SYSTEM                               │
└─────────────────────────────────────────────────────────────────────┘

  [Vela 5m cierra]
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  LAYER 1: FEATURE EXTRACTION (instant, every 5m)                  │
│                                                                   │
│   59 numeric features (from v6, verified non-leakage):           │
│     - Multi-TF BTC (8): btc_ret_1m/5m/15m, btc_vol_z, etc.       │
│     - Microstructure (6): vol_delta_3, wick_imbalance_3, etc.    │
│     - Regime (4): atr_percentile_50, trend_strength_50, etc.     │
│     - Cross-asset (3): alt_lead_5m, alt_lag_signal, etc.         │
│     - TA classic (38): RSI, MACD, EMA9/50, ATR, volume ratios    │
│                                                                   │
│   6 new features (F4):                                            │
│     - funding_rate, funding_rate_z, oi_change_1h, oi_change_4h,  │
│       sector_one_hot (4 bins → 1 categorical)                    │
│                                                                   │
│   4 trie features (F6):                                           │
│     - n1_pred_5, n1_pred_10, n1_pred_15, n2_pred_regime          │
│     - trie_agreement, trie_conflict, trie_strength (3 extras)    │
│                                                                   │
│   TOTAL: 59 + 6 + 4 + 3 = 72 features                            │
└───────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  LAYER 2: TRIE SECTORIAL QUERY (instant, every 5m)                │
│                                                                   │
│   token → sector → trie[sector]                                   │
│                                                                   │
│   ┌────────────────┐ ┌────────────────┐ ┌────────────────┐       │
│   │ TRIE           │ │ TRIE           │ │ TRIE           │       │
│   │ blue_chip      │ │ large_cap      │ │ old_meme       │  ...  │
│   │ (BTC,ETH)      │ │ (SOL,ADA,...)  │ │ (XRP,DOGE,SHIB)│       │
│   │ seq_len=10,15  │ │ seq_len=5,10   │ │ seq_len=5,10   │       │
│   │ bins=3         │ │ bins=4         │ │ bins=5         │       │
│   │ min_obs=30     │ │ min_obs=20     │ │ min_obs=15     │       │
│   └────────────────┘ └────────────────┘ └────────────────┘       │
│                                                                   │
│   Each trie: RegimePartitioned (4 vol_regime sub-tries 0-3)       │
│   Each query returns: prediction (mean fwd_ret_15m), confidence   │
└───────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  LAYER 3: DUAL LightGBM EXPERTS                                   │
│                                                                   │
│   ┌────────────────────┐    ┌────────────────────┐                │
│   │ LightGBM-LONG      │    │ LightGBM-SHORT     │                │
│   │ expert             │    │ expert             │                │
│   │                    │    │                    │                │
│   │ Trained on:        │    │ Trained on:        │                │
│   │   fwd_ret_15m > 0  │    │   fwd_ret_15m < 0  │                │
│   │                    │    │                    │                │
│   │ + sample_weight    │    │ + sample_weight    │                │
│   │   2x drops         │    │   2x drops         │                │
│   │   2x BEAR_2022     │    │   2x BEAR_2022     │                │
│   │                    │    │                    │                │
│   │ Output: pred_long  │    │ Output: pred_short │                │
│   └─────────┬──────────┘    └──────────┬─────────┘                │
│             │                          │                          │
│             └────────────┬─────────────┘                          │
│                          ▼                                        │
│              [Decision layer]                                     │
│                                                                   │
│   if pred_long > thr_long  AND trie_agreement > 0.6              │
│       AND sector_supportive  → LONG                              │
│                                                                   │
│   if |pred_short| > thr_short AND trie_agreement > 0.7           │
│       AND funding_rate_z > 1.5  → SHORT                          │
│                                                                   │
│   else → WAIT                                                    │
└───────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  LAYER 4: ADAPTIVE MANAGER (inter-trade, every 5m while open)     │
│                                                                   │
│   While position open:                                           │
│     recalc pred_long / pred_short with updated features          │
│                                                                   │
│   if pred_new contradicts position direction                     │
│      AND |pred_new| > 0.15%  → CLOSE EARLY                      │
│                                                                   │
│   if pred_new weakens but doesn't contradict                     │
│      → MOVE SL TO BREAKEVEN                                      │
│                                                                   │
│   if pred_new reinforces                                         │
│      → EXTEND TP, MAINTAIN POSITION                              │
└───────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  LAYER 5: ONLINE LEARNING (3 layers, see §6)                     │
└───────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  LAYER 6: RISK & CIRCUIT BREAKERS                                │
│                                                                   │
│   - Position size: $700 notional (7x leverage on $100 margin)    │
│   - Cost: 0.14% round-trip (Coinbase Advanced blended)           │
│   - Circuit breaker: 3 losses in a row → pause 1h                │
│   - Drawdown breaker: >5% in 24h → pause 1h                      │
│   - Drift breaker: >1.0% → STOP TRADING                          │
│   - Funding gate SHORT: only SHORT if funding_z > 1.5            │
└───────────────────────────────────────────────────────────────────┘
```

---

## 4. Trie v2 design (the new trie, no SAX)

### 4.1 Why no SAX

SAX (Symbolic Aggregate approXimation) discretizes continuous values to symbols 'a','b','c'. The old v0.x trie used SAX on close prices. Audit findings:

- Lost information (continuous → 3-5 bins)
- Required delta encoding (extra complexity)
- Pattern density too high (243 max patterns at α=3, n=5 → too many sparse nodes)
- Audit verdict: **SAX is obsolete** for v7

### 4.2 OHLCV composite encoding (the v7 way)

Per candle, compute a composite score:

```python
body_score = (close - open) / (high - low)         # range [-1, +1]
direction = sign(close - open)                      # {-1, 0, +1}
vol_signal = clip(volume / volume.rolling(20).mean(), 0.5, 5.0)  # [0.5, 5.0]

composite = body_score * 0.4 + direction * 0.35 + vol_signal * 0.25
```

Then quantize `composite` to N bins using z-score breakpoints (training-time stats, frozen at inference):

- blue_chip sector: 3 bins (low resolution, small moves)
- large_cap sector: 4 bins
- old_meme sector: 5 bins
- new_meme sector: 6 bins (high resolution, extreme moves)

**Each candle → 1 symbol** ('a','b','c',... up to N). Sequence of 5/10/15 candles → trie key.

### 4.3 TrieNodeV6Metadata (8 fields, simplified)

The old trie had 27 metadata fields per node. v7 uses 8:

```python
@dataclass
class TrieNodeV6Metadata:
    historical_count: int                    # total observations
    sum_fwd_ret_15m: float                   # for mean calculation
    sum_sq_fwd_ret_15m: float                # for variance (Welford)
    last_observation_time: float             # epoch seconds
    vol_regime_distribution: dict[int, int]  # {0: 12, 1: 5, 2: 8, 3: 2}
    vol_regime_stats: dict[int, RegimeStatsV6]  # per-regime stats
    node_type: str                           # "independent" or "dependent"
    trading_observations: int                # gate: only trust if >= min_obs
```

**Dropped from old trie (19 fields):** trigger_candle, remaining_candles, expected_move_pct, max_drawdown_pct, max_favorable_pct, win_rate, avg_duration, sl_price, tp_price, continuation_nodes, expected_sequences, break_nodes, regime (string), regime_confidence, dominant_regime, long_stats, short_stats, move_variance, move_mean_for_variance, min_independent_count, observation_timespan, last_seen_timestamp.

### 4.4 Two levels (N1 + N2), not four (N1/N2/N3/N4)

**N1 — Sequence length variations:**
- `n1_pred_5`  — query trie with last 5 candles, return mean(fwd_ret_15m) of matched nodes
- `n1_pred_10` — same with 10 candles
- `n1_pred_15` — same with 15 candles

**N2 — Regime-filtered:**
- `n2_pred_regime` — same as `n1_pred_15` but only consider observations from current `vol_regime`

**Why only 2 levels:** Audit found N3 (per_asset) and N4 (per_asset_regime) were structurally identical to N1/N2 — same patterns inserted in all 4 levels. The 2-level design captures the only meaningful distinction: unconditional vs regime-conditional probability.

**Critical lesson from old system (v2.1 Config F):** "N3=90%, N4=0% (sparse N4 data hurts more than it helps)." → v7 must monitor N2 trie density. If N2 has < min_obs per node, fall back to N1 prediction (set `n2_pred_regime = n1_pred_15`).

### 4.5 Sectorial tries (4 parallel)

```
trie_blue_chip:  BTC, ETH         — seq_len [10, 15], bins=3, min_obs=30
trie_large_cap:  SOL, ADA, AVAX, LINK — seq_len [5, 10], bins=4, min_obs=20
trie_old_meme:   XRP, DOGE, SHIB  — seq_len [5, 10], bins=5, min_obs=15
trie_new_meme:   PEPE, WIF, BONK  — seq_len [5],      bins=6, min_obs=10
```

**Why sectorial, not per-token:**
- 12 tokens × 4 seq_len × 4 regimes = 192 sub-tries → too sparse
- 4 sectors × 4 seq_len × 4 regimes = 64 sub-tries → manageable
- Tokens within a sector share microstructure (PEPE/WIF/BONK pump/dump similarly)
- LightGBM sees `sector_one_hot` feature and learns fine-grained per-token adjustments

### 4.6 Asset classification mapping

| Sector | Tokens | Rationale |
|--------|--------|-----------|
| blue_chip | BTC, ETH | High liquidity, slow moves, reacts to macro news |
| large_cap | SOL, ADA, AVAX, LINK | Beta ~1.5 vs BTC, cleaner trends |
| old_meme | XRP, DOGE, SHIB | Violent spikes without news, frequent washouts |
| new_meme | PEPE, WIF, BONK | Extreme volatility, 100% pumps in hours |

**Note:** XRP was `large_cap` in old code but is `old_meme` in v7 (corrects a long-standing misclassification).

---

## 5. LightGBM dual experts

### 5.1 Why dual experts

A single LightGBM struggles with SHORT because:
- 60% of training labels are positive (bull regime 2024-2025)
- Model biases toward LONG predictions
- SHORT signals get diluted

**Solution:** train two separate LightGBM models:

| Expert | Training filter | Output | Threshold |
|--------|-----------------|--------|-----------|
| LONG expert | `fwd_ret_15m > 0` only | `pred_long` (always positive) | `thr_long = 0.30%` (tunable) |
| SHORT expert | `fwd_ret_15m < 0` only | `pred_short` (always negative) | `thr_short = 0.40%` (tunable, more selective) |

### 5.2 Sample weighting (carried from v6)

Both experts use:
- `2x weight` for observations where `fwd_ret_15m` is a drop (large negative)
- `2x weight` for observations in BEAR_2022 window
- Compounds to `4x` for bear-drops (the rare-but-critical cases)

### 5.3 Hyperparameters (frozen from v6)

```python
params = {
    "objective": "regression",
    "metric": ["rmse", "l1"],
    "num_leaves": 31,
    "learning_rate": 0.05,
    "n_estimators": 300,
    "early_stopping": 30,
    "min_child_samples": 50,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "random_state": 42,
    "verbose": -1,
}
```

### 5.4 Anti-leakage guards (mandatory, never disable)

After every train, verify:
- `top_feat_gain < 30%` of total gain (no single feature dominates)
- `train_corr < 0.85` (model not overfit)
- Walk-forward: train on months 1-N, test on month N+1, never the reverse

If any guard fails: **reject the model, keep the previous one, alert**.

### 5.5 Decision layer (final signal)

```python
def decide(pred_long, pred_short, trie_agreement, sector, funding_rate_z, thr_long, thr_short):
    # LONG signal
    if pred_long > thr_long and trie_agreement > 0.6:
        return "LONG"
    
    # SHORT signal (more restrictive)
    if abs(pred_short) > thr_short and trie_agreement > 0.7 and funding_rate_z > 1.5:
        return "SHORT"
    
    return "WAIT"
```

---

## 6. Online learning — 3 layers

### 6.1 Layer 1 — Trie online (every 5m)

**Trigger:** every 5m candle close + 15m delay (to know `fwd_ret_15m` outcome)

**Process:**
```
T = 10:00:00  Vela cierra
T = 10:00:01  Consultar trie para features de ESTA vela (sin leakage)
T = 10:00:02  LightGBM predice pred_long, pred_short
T = 10:00:03  Decisión: LONG / SHORT / WAIT
T = 10:00:04  Si trade: abrir posición
T = 10:15:00  Vela + 15m cierra → sabemos outcome real
T = 10:15:01  ★ INSERTAR en trie: (vela_10:00, sector, régimen) → fwd_ret_real
T = 10:15:02  Posición cerrada (exit +15m)
T = 10:15:03  Registrar PnL
```

**CRITICAL RULE — INSERT-AFTER-PREDICT:**
The trie insertion happens 15 minutes AFTER the prediction. Never before. This is enforced by code structure (separate function calls, never inline).

**Trie hygiene:**
- `prune()` every 1000 insertions — remove nodes with <3 observations
- Time decay half-life = 24h (stale patterns lose weight exponentially)
- Max nodes per sector: 100K (LRU eviction if exceeded)

### 6.2 Layer 2 — Rolling retrain (every 6h)

**Trigger:** every 6h (00:00, 06:00, 12:00, 18:00 UTC)

**Process:**
1. Load last 30 days of data (12 tokens × 30 days × 288 candles = 103K candles)
2. Calculate features v7 (72 features per candle)
3. Walk-forward split:
   - Train: days 1-25 (75K candles)
   - Validation: days 26-28 (10K candles) — early stopping
   - Test: days 29-30 (8K candles) — final acceptance gate
4. Train LightGBM-LONG expert (filter: fwd_ret > 0)
5. Train LightGBM-SHORT expert (filter: fwd_ret < 0)
6. Validate on test set:
   - LONG WR > 60%? → accept
   - SHORT WR > 50%? → accept
   - If either fails → reject, keep previous model, log alert
7. Hot-swap atomic: replace `.txt` model files with file lock

**Anti-overfitting protections:**
- 30 days context = 103K obs. One trade moves model < 0.001%.
- Early stopping on validation (never test).
- If model rejected 3 times in a row → trigger Layer 3 early.

### 6.3 Layer 3 — Walk-forward monthly

**Trigger:** 1st of every month

**Process:**
1. Load last 90 days of data (309K candles)
2. Run 5 walk-forward windows (monthly)
3. Full anti-leakage guards verification
4. Generate report: per-window WR, PF, PnL, Sharpe, drift metrics
5. Decision: continue with current config / adjust hyperparams / alert human

---

## 7. Adaptive SL/TP manager

### 7.1 Inter-trade decision logic

While a position is open, every 5m:

```python
def adaptive_check(position, current_features):
    # Recalculate predictions with updated features
    pred_long_new = lightgbm_long.predict(current_features)
    pred_short_new = lightgbm_short.predict(current_features)
    
    if position.direction == "LONG":
        # Reinforces: keep, extend TP
        if pred_long_new > pred_long_entry * 0.8:
            position.extend_tp()
        
        # Weakens but doesn't contradict: move SL to breakeven
        elif pred_long_new > 0 and pred_long_new < pred_long_entry * 0.5:
            position.move_sl_to_breakeven()
        
        # Contradicts: close early
        elif pred_short_new < -0.15:  # predicts drop > 0.15%
            position.close("early_exit_contradiction")
    
    elif position.direction == "SHORT":
        # Symmetric logic
        ...
```

### 7.2 Slippage protection

- Don't close early if estimated loss < 0.05% (not worth the transaction cost)
- Only close early if model predicts opposite direction > 0.15%
- Max 1 early close per position (avoid churn)

---

## 8. Circuit breakers & risk

### 8.1 Auto-pause triggers

| Trigger | Action | Reset |
|---------|--------|-------|
| 3 consecutive losses (same sector) | Pause 1h | Auto-resume after 1h |
| Drawdown > 5% in 24h | Pause 1h | Manual resume required |
| Drift > 0.5% (pred avg vs outcome avg, 24h) | Force Layer 2 retrain early | Auto-resume after retrain accepted |
| Drift > 1.0% | STOP TRADING | Manual investigation required |
| Funding rate > 0.10%/8h (extreme) | Block new SHORTs | Auto-resume when funding < 0.05% |

### 8.2 Position sizing

- Notional: $700 per trade (7x leverage on $100 margin)
- Cost: 0.14% round-trip (Coinbase Advanced blended maker/taker)
- Max concurrent positions: 3
- Max trades per day: 50 (avoid overtrading)

### 8.3 Cost model

```python
round_trip_cost = 0.14%  # taker fee both sides
breakeven_move = 0.14%   # need to move 0.14% to break even
minimum_trade_threshold = 0.30%  # pred must exceed this to enter
```

---

## 9. Implementation plan — 13 phases

| Phase | Task | Effort | Status | Blocks |
|-------|------|--------|--------|--------|
| F0 | Cleanup repo + write master plan | 1h | DONE | None |
| F1 | TrieNodeV6Metadata (8 fields, no SAX) | 1h | DONE | F0 |
| F2 | OHLCV composite encoder (sectorized) | 3-4h | DONE | F1 |
| F3 | 4 sectorial tries + RegimePartitioned | 4-5h | DONE | F2 |
| F4 | Features extras (funding rate, OI, sector) | 3-4h | DONE | F3 |
| F5a | LightGBM-LONG expert (retrain with labels>0) | 2-3h | pending | F4 |
| F5b | LightGBM-SHORT expert (retrain with labels<0) | 2-3h | pending | F4 |
| F6 | Trie conflict features (agreement, strength) | 1-2h | pending | F5a, F5b |
| F7 | Walk-forward backtest (dual expert) | 4-6h | pending | F6 |
| **CHECKPOINT** | If SHORT WR > 52%: continue. If < 50%: re-evaluate. | — | — | F7 |
| F8 | Layer 1: Trie online (insert-after-predict) | 3-4h | pending | F7 |
| F9 | Layer 2: Rolling retrain every 6h | 3-4h | pending | F8 |
| F10 | Adaptive Manager (SL/TP dynamic) | 1-2 days | pending | F9 |
| F11 | Layer 3: Walk-forward monthly + drift monitor | 3-4h | pending | F10 |
| F12 | Adaptive threshold + circuit breakers | 2-3h | pending | F11 |
| F13 | Maximize trades (k-hours retune) | 2-3h | pending | F12 |

**Total estimated:** 10-12 days of focused work.

---

## 10. Repository structure (target after F0)

```
ppmt/                                  # repo root
├── PPMT_v7_MASTER_PLAN.md             # THIS document
├── worklog.md                         # execution history (append-only)
├── README.md                          # quickstart (NEW, written in F0)
├── .gitignore
├── pyproject.toml
├── config/
│   ├── v7.yaml                        # runtime config (NEW in F0)
│   └── github_token.txt               # NOT tracked (gitignored)
├── docs/
│   ├── v7/
│   │   └── (empty, populated by each phase)
│   ├── audit_alternative/
│   │   └── N1_N4_STRUCTURE_ANALYSIS.md   # legacy audit, kept for reference
│   └── *.pdf                          # original PPMT technical docs
├── scripts/
│   ├── v6/                            # v6 production code (KEEP for rollback)
│   │   ├── v6_download_ohlcv.py
│   │   ├── v6_extract_features.py
│   │   ├── v6_train_wf.py
│   │   ├── v6_backtest_filtered.py
│   │   └── ... (16 files)
│   └── v7/                            # NEW v7 code (created in F1+)
│       ├── v7_trie_metadata.py        # F1
│       ├── v7_ohlcv_encoder.py        # F2
│       ├── v7_sector_tries.py         # F3
│       ├── v7_features_extras.py      # F4
│       ├── v7_train_long_expert.py    # F5a
│       ├── v7_train_short_expert.py   # F5b
│       ├── v7_trie_conflict.py        # F6
│       ├── v7_backtest_dual.py        # F7
│       ├── v7_trie_online.py          # F8
│       ├── v7_rolling_retrain.py      # F9
│       ├── v7_adaptive_manager.py     # F10
│       ├── v7_walkforward_monthly.py  # F11
│       ├── v7_circuit_breaker.py      # F12
│       └── v7_khours_retune.py        # F13
├── src/
│   └── ppmt/                          # legacy code (KEEP for reference, do NOT use in v7)
│       ├── core/                      # old trie + SAX (legacy)
│       ├── engine/                    # old paper trader (legacy)
│       ├── risk/                      # old risk gates (legacy)
│       └── ...
├── data/                              # gitignored (large files)
│   ├── ppmt.db                        # OHLCV database (4.4GB)
│   ├── v6_models/                     # trained v6 models
│   ├── v7_models/                     # NEW v7 models (created in F5+)
│   └── personal/                      # gitignored (MEXC xlsx etc.)
├── tests/
│   └── v7/                            # NEW v7 tests
└── state/                             # gitignored runtime state
```

**What gets deleted in F0:**
- `skills/` (1083 files, 62MB — bot skills, not PPMT)
- `agent-ctx/` (24 files — historical subagent context)
- `terminal-v2/` (26 files — old React dashboard, superseded)
- `_archive/` (already removed)
- `scripts_external/` (already removed)
- `scripts/v5/` (47 files — v5_cb_v2 dead code, leakage postmortem)
- `docs/v5_cb_v2/` (already removed)
- Root .md files: `ARCHITECTURE_V1.md`, `HANDOFF.md`, `TRAZABILIDAD.md`, `OPTIMIZATION_v2.*.md`
- `mini-services/`, `prisma/`, `models/`, `.zscripts/`, `Caddyfile`, `start.sh`, `setup_fresh.sh`, `groups_config.json`

**What stays:**
- `scripts/v6/` (16 files — production code, rollback path)
- `src/ppmt/` (71 files — legacy code, kept for reference / partial reuse)
- `data/ppmt.db` (4.4GB — OHLCV database, base of everything)
- `data/v6_models/` (38 files — trained models)
- `worklog.md` (432 lines — execution history)
- `docs/audit_alternative/N1_N4_STRUCTURE_ANALYSIS.md` (legacy audit)
- `docs/*.pdf` (technical docs)
- `tests/` (existing tests)

---

## 11. Anti-leakage rules (NON-NEGOTIABLE)

These rules are baked into the code. Violating them = critical bug.

### 11.1 Temporal leakage

- **INSERT-AFTER-PREDICT:** Trie insertion at T+15m, prediction at T. Never the reverse.
- **Train/test split:** Always by timestamp. `train.ts < test.ts_min`. No random shuffling.
- **Feature computation:** `fwd_ret_*` always uses `shift(-N)`. Never `shift(N)`.
- **Rolling stats:** `rolling(N).mean()` uses `closed='left'` (excludes current bar).

### 11.2 Model overfitting

- **Top feature guard:** `top_feat_gain < 30%` of total. If violated, regularize more.
- **Train correlation guard:** `corr(pred_train, y_train) < 0.85`. If violated, reduce `num_leaves`.
- **Walk-forward:** Train on months 1-N, test on month N+1. Never peek ahead.
- **Early stopping:** On validation set, never test.

### 11.3 Online learning safety

- **No per-trade retrain:** Always 30+ days of context. One trade cannot move the model.
- **Drift monitor:** If `|pred_avg - outcome_avg| > 0.5%` (24h rolling), force retrain.
- **Circuit breaker:** 3 losses in a row → pause. Don't try to "win it back".

### 11.4 SHORT-specific safety

- **Funding rate gate:** SHORT only if `funding_rate_z > 1.5` (longs overleveraged).
- **Higher threshold:** `thr_short = 0.40%` (vs `thr_long = 0.30%`). More selective.
- **Trie agreement:** SHORT requires `trie_agreement > 0.7` (vs 0.6 for LONG).
- **Max SHORT exposure:** 30% of concurrent positions can be SHORT.

---

## 12. Decision points & failure modes

### 12.1 F7 checkpoint — SHORT unlock validation

After F7 (walk-forward backtest with dual experts):

| SHORT WR | Decision |
|----------|----------|
| > 55% | ✅ Excellent. Continue with F8-F13. |
| 52-55% | ✅ Acceptable. Continue but monitor in F11. |
| 50-52% | ⚠ Marginal. Investigate per-sector, per-window. May need F4 funding rate tuning. |
| < 50% | ❌ SHORT not unlocked. Fall back to v6 LONG-only + 15m hedge. Document and stop. |

### 12.2 Drift escalation

| Drift level | Action |
|-------------|--------|
| < 0.2% | Normal. Continue. |
| 0.2-0.5% | Warning. Log + monitor. |
| 0.5-1.0% | Critical. Force Layer 2 retrain. |
| > 1.0% | Stop trading. Manual investigation. |

### 12.3 Rollback path

If v7 fails catastrophically:
1. Stop trading
2. Restore v6 LONG-only models from `data/v6_models/`
3. Run `scripts/v6/v6_backtest_filtered.py` to verify
4. Deploy v6 LONG-only as fallback
5. Investigate v7 failure in `worklog.md`

---

## 13. GitHub & commit discipline

### 13.1 Commit format

```
<type>(v7/<phase>): <description>

[optional body explaining what + why]

[footer referencing worklog Task ID]
```

Types: `feat`, `fix`, `exp`, `docs`, `chore`, `refactor`

Examples:
- `feat(v7/f1): TrieNodeV6Metadata with 8 fields, no SAX/LONG-SHORT`
- `exp(v7/f7): walk-forward backtest with dual experts — SHORT WR 53%`
- `docs(v7): update master plan with F7 results`

### 13.2 Commit cadence

- Commit after every phase completion
- Push to `origin/main` immediately after commit
- Update `worklog.md` with Task ID + Stage Summary BEFORE commit
- Never commit secrets (token is in `config/github_token.txt`, gitignored)

### 13.3 Branch strategy

For now: direct to `main`. If we need experiment isolation:
- `exp/v7-trie-sectorial` for risky experiments
- Merge to `main` only after F7 validation passes

---

## 14. Glossary

| Term | Definition |
|------|------------|
| fwd_ret_15m | Forward 15-minute return: `(close[T+3] - close[T]) / close[T]` on 5m TF |
| vol_regime | Volatility regime 0-3 (0=low vol, 3=extreme vol), computed from ATR percentile |
| sector | Token group: blue_chip, large_cap, old_meme, new_meme |
| trie_agreement | Fraction of trie predictions (n1_pred_5/10/15, n2_pred_regime) that agree in sign |
| trie_conflict | `\|sector_pred - cross_sector_pred\|` — divergence between sectors |
| drift | `\|pred_avg_24h - outcome_avg_24h\|` — model prediction error |
| funding_rate_z | Z-score of funding rate vs 30-day rolling mean |
| Layer 1/2/3 | Trie online / rolling retrain / walk-forward monthly |
| LONG expert | LightGBM trained only on `fwd_ret_15m > 0` observations |
| SHORT expert | LightGBM trained only on `fwd_ret_15m < 0` observations |

---

## 15. Contact & ownership

- **Repo owner:** coverdraft
- **AI agent:** super-z (GLM)
- **Token location:** `config/github_token.txt` (gitignored, persistent in this env)
- **Worklog:** `worklog.md` (append-only, all agents must update)

**End of master plan. Implementation begins with F0.**
