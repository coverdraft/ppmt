# Worklog - PPMT Paper Trading Fix

---
Task ID: v0.2.1
Agent: Main
Task: Fix paper trading 0 trades - propagate Trie metadata upward

Problem:
- Paper trading produced 0 trades despite 2549 signals passing threshold
- PredictionEngine couldn't find meaningful metadata because only terminal
  Trie nodes had statistics; intermediate nodes had historical_count=0

Work Log:
- Added propagate_metadata() and _propagate_node() to PPMTTrie (trie.py)
- Rewrote _find_best_node() in PredictionEngine for prefix matching
- Improved _compute_confidence() to work with propagated metadata
- Added propagate_metadata() call after loading Tries in PaperTrader
- Updated ppmt.py to call propagate_metadata() on all 4 Tries after build()

Stage Summary:
- Commit: 8015caf "fix: prediction confidence=0% and paper trading 0 trades"
- Result: Confidence improved to ~13.5-15%, but still 0 trades (RiskManager blocking)

---
Task ID: v0.2.2
Agent: Main
Task: Fix RiskManager blocking all trades with hardcoded thresholds

Problem:
- can_open() had confidence < 0.5 (hardcoded), quality_score < 0.3, risk_reward < 1.5
- Paper trading needed permissive thresholds to validate signals

Work Log:
- Made all thresholds configurable via RiskConfig dataclass
- Added min_confidence field to RiskConfig (default 0.0)
- Lowered min_risk_reward from 1.5 to 0.5 in PaperTrader's RiskConfig
- Lowered min_quality_score from 0.3 to 0.0
- Removed suffix-based pattern shortening in PaperTrader (was searching wrong paths)
- Set default min_confidence=0.10 in PaperTraderConfig and CLI

Stage Summary:
- Commit: 38ba447 "v0.2.2: Fix RiskManager blocking all trades with hardcoded thresholds"
- Result: 1 trade generated (+7.38%), but Risk rejections showed:
  - 'Daily loss limit reached': 1652 rejections
  - 'R:R too low: 0.01-0.11': 895 rejections

---
Task ID: v0.2.3
Agent: Main
Task: Fix daily loss limit bug and R:R calculation (partial fix)

Problem:
- SL calculation used pattern_break_probability * 2 + 0.01, producing tiny stop distances
- Daily loss limit still blocking too many signals

Work Log:
- Changed SL calculation to use expected_move_pct * 0.5 with minimum 1%
- Added day tracking in paper trader loop for daily P&L reset (partial)

Stage Summary:
- Commit: b9c2f2a "v0.2.3: Fix daily loss limit bug and R:R calculation"
- Result: 6 trades, +9.15%, but still major blockers:
  - 'Daily loss limit reached': 1581 (reset not working properly)
  - 'R:R too low: 0.04-0.50': ~895 (max_drawdown_pct still dominating SL)

---
Task ID: v0.2.4
Agent: Main
Task: Fix SL/TP calculation and daily loss limit reset

Problem:
- SL was using max_drawdown_pct from PredictionEngine (worst observed drawdown),
  which is 2-4x larger than expected_move, producing R:R = 0.04-0.50
- risk_mgr.reset_daily() was NEVER called in the simulation loop,
  so once daily loss limit triggered, all subsequent signals across ALL days were blocked
- This caused 1581/2537 rejections by "Daily loss limit reached"

Work Log:
- Rewrote SL/TP calculation in paper_trader.py to use expected_move-based stops:
  SL = 50% of expected_move (min 1%), TP = 100% of expected_move
- Added current_date tracking in simulation loop
- Added risk_mgr.reset_daily() call when date changes
- Restored min_risk_reward to 1.5 (since R:R >= 2.0 now)
- Updated versions to 0.2.4

Stage Summary:
- Commit: d6bcb5f "v0.2.4: Fix SL/TP calculation and daily loss limit reset"
- Result: 7 trades, -2.27%. Daily loss limit fix worked (1 vs 1581).
  NEW blockers found:
  - 'Max drawdown reached: 15.91%': 1648 rejections (permanent circuit breaker)
  - 'R:R too low: 0.50-1.49': ~800 rejections (min_sl_pct=1% too large for small moves)

---
Task ID: v0.2.5
Agent: Main
Task: Fix max drawdown circuit breaker and R:R for small moves

Problem:
- max_drawdown_pct=0.15 (15%) is a PERMANENT circuit breaker. Once portfolio drops
  15% from peak, ALL trades are blocked forever (no daily reset). This caused 1648
  rejections - more than the original daily loss limit bug.
- min_sl_pct=1.0% is too large for small expected_moves (<2%), producing R:R < 2.0.
  Example: expected_move=1.5% -> tp=1.5%, sl=1.0%, R:R=1.5 (rejected by min 1.5).

Work Log:
- Increased max_drawdown_pct from 0.15 (15%) to 0.50 (50%) for paper trading
- Increased max_daily_loss_pct from 0.05 (5%) to 0.10 (10%) for paper trading
- Lowered min_sl_pct from 1.0% to 0.5% for noise protection
- Lowered min_risk_reward from 1.5 to 0.5 to accept R:R >= 0.5
- Updated worklog-new.md with full traceability of v0.2.1 through v0.2.5
- Updated versions to 0.2.5

Stage Summary:
- Commit: (pending push)
- Expected: Far more trades generated. Previous 2518 signals that passed threshold
  should now mostly pass risk checks instead of being blocked by drawdown/R:R.
