# PPMT Tuning Roadmap

Living document. Each round moves a card from `Backlog` → `In Progress` → `Done` (or `Rolled Back`).

## Legend

- 🟢 Done & verified
- 🟡 In progress / awaiting data
- 🔴 Backlog
- ⚫ Rolled back

---

## Active Iterations

### Round 1 — v9 → v10 (TP/SL rebalance + risk tightening) 🟡
- **Patch**: `tuning/v9_to_v10_tuning_patch.yaml`
- **Status**: applied locally + pushed to GitHub (commit `7ab8f02`)
- **Awaiting**: user to apply to runtime engine, run ≥24h, export `ppmt-export-v10`
- **Acceptance gate**: PF ≥ 1.10 AND win_rate ≥ 45% AND no L10+ streak
- **Rollback condition**: PF < 0.92 OR win_rate < 35% OR MaxDD > 5%

---

## Backlog (proposed future rounds)

### Round 2 — Strategy A (Momentum) revival 🔴
- **Depends on**: CHANGE_3 from v10 patch producing rejection logs
- **Goal**: identify why Momentum has 0 trades despite 3,000 USDT allocated
- **Candidate fixes** (depending on what the log reveals):
  - If `vol_filter` dominates → relax volume threshold (e.g. 1.5x avg → 1.2x)
  - If `confidence_too_low` dominates → lower min_confidence only for Strategy A (0.65 → 0.55)
  - If `cooldown` dominates → check reentry_cooldown setting
  - If `no_candidate` dominates → expand token universe for Strategy A
- **Success metric**: Strategy A ≥ 5 trades / 24h with win_rate ≥ 50%

### Round 3 — ML bootstrap exit & exposure lift 🔴
- **Depends on**: ML stage transitioning from BOOTSTRAP → LEARNED in v10 or v11
- **Trigger**: total_trades ≥ 500 AND last_retrain_time != null
- **Action**: lift CHANGE_5 (exposure cap 30% → 80%) if PF over last 100 trades ≥ 1.10
- **Caution**: keep streak circuit breaker (CHANGE_6) active

### Round 4 — Trailing stop & break-even tuning 🔴
- **Depends on**: v10 producing ≥20 trailing-stop closes (CHANGE_7 effect)
- **Goal**: optimize trailing distance based on realized give-back data
- **Candidate**: trailingStopDistancePct 0.5 → 0.4 if avg win shrinks after CHANGE_7

### Round 5 — Monte Carlo verdict calibration 🔴
- **Issue identified in v9**: `p95_dd=0` and `verdict=PASS` with `prob_profit=45.4%` is a false positive (session too short)
- **Proposed fix**: add a `min_trades_for_mc_verdict = 100` gate in the runtime; if below, `verdict = "INSUFFICIENT_DATA"` instead of PASS
- **Also**: increase Monte Carlo simulation paths (current setting TBD in runtime)

### Round 6 — Regime-aware strategy weights 🔴
- **Idea**: in `volatile` regime (41/50 observations in v9), reduce Breakout allocation further (already done in CHANGE_2) and boost Mean Reversion (already performing best)
- **Implementation**: dynamic weight table per regime, applied at the MoneyManager level

### Round 7 — Per-symbol Kelly differentiation 🔴
- **Idea**: v9 used a flat `kellyFraction=0.5` for all tokens. PEPE/Squeeze tokens should have lower Kelly (higher variance) than BTC/ETH blue chips
- **Proposed**: `kelly_fraction_per_class = {blue_chip: 0.5, large_cap: 0.4, mid_cap: 0.3, defi: 0.25, meme: 0.2}`

---

## Done

(none yet — round 1 still in progress)

---

## Rolled Back

(none yet)

---

## How to Update This File

After each snapshot export:
1. Move the corresponding card from `In Progress` to either `Done` or `Rolled Back`
2. Add a one-line summary with the vN → vN+1 transition and the verdict
3. Pull next card from `Backlog` into `In Progress`
4. Commit with message: `roadmap: v{N}→v{N+1} {KEEP|REFINE|ROLLBACK}, summary line`
