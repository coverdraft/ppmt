# PPMT Tuning Traceability Log

This file records **every applied tuning patch** and the resulting engine metrics,
so we can compare each iteration and decide whether to keep, refine, or roll back.

## Comparison Table

| Snapshot | Date | Patch | Trades | Win% | PF | PnL% | MaxDD% | AvgHold | LastStreak | ML Stage | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| v9 | 2026-06-28 18:45 | (baseline) | 20 | 40 | 0.92 | -0.03 | 0.03 | 13m | L7 | BOOTSTRAP | ⚠️ REGRESS |
| v10 | _pending_ | v9→v10 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

---

## v9 — Baseline (2026-06-28 18:45:23 local)

- **Source snapshot**: `ppmt-export-v9` (uploaded by user)
- **Patch applied**: none (baseline)
- **Engine mode**: paper / auto
- **Session length**: 12.5 min
- **Total trades**: 20 (8 W / 12 L)
- **Win rate**: 40 %
- **Profit factor**: 0.92
- **Total PnL**: -0.03 %
- **Max drawdown**: 0.03 %
- **Avg win / Avg loss**: +0.68 / -0.49 USDT (payoff ratio 1.39)
- **Avg hold**: 13 min
- **Last streak**: L7
- **ML stage**: BOOTSTRAP (no retrain)
- **Strategies active**: B (Mean Rev, 14 trades, 60% WR, +2.63), C (Breakout, 10 trades, 30% WR, -1.25), D (Squeeze, 2 trades, 0% WR, -0.85), A (Momentum, 0 trades)
- **Exposure**: 5.8 %
- **Monte Carlo**: PASS (⚠️ false positive — session too short)
- **Open issues**:
  - Strategy A never fired (3,000 USDT idle)
  - Strategy C underperforming (30% WR)
  - SL/TP imbalance: 12 SL vs 8 TP, ratio pago 1.39 vs R:R config 2.5
  - Confidence 0.9 SHORT clavada con EV 0.27 (overconfidence)
  - 0 trailing-stop closes (activation 1.0% never reached)

### v9 Diagnosis → Recommendations

See `v9_snapshot_analysis.md` for the full diagnostic.
See `v9_to_v10_tuning_patch.yaml` for the explicit parameter changes applied.

---

## v10 — _pending_

**To fill in after applying the v9→v10 patch and running ≥24h:**

```
- Snapshot path: ppmt-export-v10 (TBD)
- Date applied: ___________
- Date evaluated: ___________ (>= 24h after apply)
- Session length: ___________ min
- Total trades: ___________
- Win rate: ___________ %
- Profit factor: ___________
- Total PnL: ___________ %
- Max drawdown: ___________ %
- Avg win / Avg loss: ___________ / ___________ USDT
- Payoff ratio: ___________
- Avg hold: ___________ min
- Last streak: ___________
- ML stage: ___________
- Strategies:
    A (Momentum):   __ trades, __% WR, __ USDT
    B (Mean Rev):    __ trades, __% WR, __ USDT
    C (Breakout):    __ trades, __% WR, __ USDT  ← capital reduced to 1000
    D (Squeeze):     __ trades, __% WR, __ USDT
- Exposure: ___________ %
- Monte Carlo verdict: ___________
- Trailing-stop closes: ___________ (was 0 in v9)
- Streak circuit breaker triggers: ___________
```

### Per-change evaluation (v9 → v10)

| Change ID | Description | Expected | Actual | Verdict |
|---|---|---|---|---|
| CHANGE_1 | TP 2.5→1.8, SL 1.5→1.2 ATR | win_rate +5-10pp, payoff 1.6-1.8, PF 1.05-1.20 | __pending__ | __pending__ |
| CHANGE_2 | Strategy C capital 2500→1000 | C max loss -60% | __pending__ | __pending__ |
| CHANGE_3 | Strategy A debug log rejections | identify rejection reason | __pending__ | __pending__ |
| CHANGE_4 | min_confidence 0.65→0.75, min_ev_score 0→0.30 | signal_rate -30%, win_rate +5-8pp | __pending__ | __pending__ |
| CHANGE_5 | Exposure cap 80%→30% during bootstrap | tail risk -62% | __pending__ | __pending__ |
| CHANGE_6 | Streak circuit breaker L10 → pause | auto-pause on L10 | __pending__ | __pending__ |
| CHANGE_7 | Trailing activation 1.0%→0.7% | trailing closes 10-20% of trades | __pending__ | __pending__ |

### v10 Decision

- [ ] **KEEP** all changes
- [ ] **REFINE** (specify which changes need re-tuning): ___________
- [ ] **ROLLBACK** to v9 (specify reason): ___________

---

## How to Add a New Round

1. Copy `v9_to_v10_tuning_patch.yaml` → `v10_to_v11_tuning_patch.yaml`
2. Update `baseline_metrics` with v10 results
3. Add new `CHANGE_*` blocks
4. Apply patch to runtime
5. Run ≥24h, export snapshot as `ppmt-export-v11`
6. Add new section to this file with the same template
7. Fill in the comparison table at the top

## Decision Rules

- **KEEP** if PF ≥ 1.10 AND win_rate ≥ 45% AND no new critical issue
- **REFINE** if PF improved but < 1.10, OR one specific change regressed
- **ROLLBACK** if PF < 0.92 (v9 baseline) OR win_rate < 35% OR L10+ streak
- **EMERGENCY ROLLBACK** if max_drawdown > 5% OR daily_loss > 4%
