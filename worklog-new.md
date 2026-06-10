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
- Commit: 9fcaa8b "v0.2.5: Fix max drawdown circuit breaker + R:R for small moves + worklog"
- Result: 54 trades (up from 7!), -43.04%, WR 33.3%, Max DD 51.3%
  - Trade count dramatically improved (54 vs 7)
  - BUT: 33% WR is terrible (worse than random 50%)
  - 2288 signals blocked by "Max drawdown reached: 51.25%" (even 50% was hit)
  - SHORT signals consistently lose (most SHORT trades hit stop_loss)
  - Many trades exit at SL with small losses (-1.91%, -2.41%, -2.65%) suggesting
    SL is too tight for BTC 1h noise (1-2% normal fluctuation)

---
Task ID: v0.2.6
Agent: Main
Task: Improve trade quality - wider SL, SHORT filter, better signal selection

Problem:
- v0.2.5 generated 54 trades but with 33.3% WR (worse than random)
- SL at 0.5% minimum gets triggered by normal BTC noise (1-2% per hour)
- SHORT signals are consistently losing (BTC trends up long-term)
- 2288 signals blocked by max drawdown (even at 50%)
- Most signals have only 10% confidence (basically noise)

Root cause analysis:
- SL = 0.5% of price is INSANE for crypto. BTC 1h ATR is typically 1-2%.
  A 0.5% SL gets hit by a single noisy candle, not by wrong direction.
- SHORT signals on BTC are fundamentally unreliable because BTC trends up.
  The Trie has fewer SHORT patterns and they have lower WR.
- Low confidence (10%) signals are indistinguishable from noise.

Changes:
1. SL/TP redesigned: SL = expected_move (full), TP = 2x expected_move, min SL = 2.0%
   - This gives R:R = 2.0 by construction with WIDE stops
   - 2% minimum SL avoids noise triggers on BTC 1h
2. SHORT filter: Require confidence >= 20% for SHORT (vs 10% for LONG)
   - SHORT predictions need higher conviction since BTC trends up
3. Minimum expected_move raised from 0.3% to 1.0%
   - Moves < 1% are noise on BTC 1h, not actionable signals
4. Position size reduced from 2% to 1% base risk
   - Conservative while tuning signal quality
5. max_drawdown_pct increased from 50% to 80% for paper trading
   - Don't block signals while still tuning the strategy
6. min_risk_reward set to 1.0 (TP=2*SL guarantees R:R=2.0 anyway)

Stage Summary:
- Commit: 99fd1e6 "v0.2.6: Wider SL, SHORT filter, better signal selection"
- Result: 173 trades (up from 54!), 38.7% WR (up from 33.3%), -18.85% P&L (up from -43.04%)
  - Trade count dramatically improved (173 vs 54)
  - WR improved from 33.3% to 38.7%
  - P&L improved from -43.04% to -18.85%
  - All trades are LONG (SHORT filter worked too well — 0 SHORT trades)
  - Profit Factor 1.11 (almost break-even)
  - Best trade +39.23%, worst -14.66% (still catastrophic single-trade losses)
  - Max DD 50%, Sharpe 0.65
  - REMAINING: SL still too wide for some trades (-14.66%), no trailing stop

---
Task ID: v0.2.7
Agent: Main
Task: ATR-based SL/TP, trailing stop, expected-value path walking

Problem:
- v0.2.6 generated 173 trades with 38.7% WR but still -18.85% P&L
- SL = expected_move can be 10-15% for some predictions, leading to catastrophic
  single-trade losses (-14.66%, -13.74%, -13.36%)
- No trailing stop — winning trades reverse and exit at SL instead of locking in profit
- _walk_path() follows most frequent child, not most profitable
- SL based on expected_move is arbitrary — doesn't adapt to market volatility

Root cause analysis:
- Expected_move of 10-15% produces SL at 10-15% from entry — way too wide.
  With 1% base risk and these SL distances, position sizing reduces exposure
  but the PnL% still shows the full move (-14%).
- Without trailing stop, a trade that reaches +8% unrealized can reverse
  all the way back to -5% SL before exiting. This destroys the R:R advantage.
- Following the most frequent child in the Trie means we follow the most
  COMMON outcome, not the most PROFITABLE one. A 40%-likely child with +5%
  expected move is better than a 50%-likely child with +0.5%.
- ATR (Average True Range) is the industry standard for setting stops
  because it adapts to current volatility. Using fixed percentages ignores
  that BTC volatility varies from 0.5%/hour to 5%/hour.

Changes:
1. ATR-based SL/TP: SL = max(1.5 × ATR_pct, 1.5%), capped at 5% max
   - High ATR (volatile) → wider stops to avoid noise
   - Low ATR (quiet) → tighter stops for better R:R
   - 5% cap prevents catastrophic single-trade losses
   - TP = SL × 2.0 (R:R = 2.0 by construction)
2. Trailing stop: activates when unrealized profit > 50% of TP distance
   - Once activated, SL trails at 1.0× ATR below current price (LONG)
   - This locks in profit and lets winners run further
   - Exit reason labeled "trailing_stop" (vs "stop_loss")
3. Expected-value path walking in prediction.py:
   - Changed _walk_path() to sort children by win_rate × abs(expected_move)
   - Instead of following the most common child, follows the most profitable
   - A 40%-likely child with +5% move now beats a 50%-likely +0.5% child
4. Added compute_atr_pct() function for ATR calculation
5. Added sl_price, tp_price, trailing_activated fields to PaperTrade
6. min_risk_reward raised to 1.5 (ATR-based SL gives R:R=2.0)

Files modified:
- src/ppmt/engine/paper_trader.py: ATR computation, SL/TP, trailing stop, PaperTrade fields
- src/ppmt/engine/prediction.py: _walk_path() expected-value sorting
- src/ppmt/__init__.py: version 0.2.7
- pyproject.toml: version 0.2.7
- src/ppmt/cli/main.py: version 0.2.7
- worklog-new.md: this entry

Stage Summary:
- Commit: 10b415a "v0.2.7: ATR-based SL/TP, trailing stop, expected-value path walking"
- Result: CRASHED with UnboundLocalError: `np` not accessible at line 324
  - Root cause: `import numpy as np` on line 734 (inside run()) shadows module-level
    import on line 36, making Python treat `np` as local variable throughout the function
  - The ATR/trailing/EV changes never actually ran

---
Task ID: v0.2.8
Agent: Main
Task: Fix np crash + implement Living Trie (incremental learning during paper trading)

Problem:
1. v0.2.7 crashed immediately: UnboundLocalError on `np` because `import numpy as np`
   inside run() (line 734) shadows the module-level import (line 36). Python treats
   `np` as local throughout the function, so it's unbound at line 324.
2. The Trie is STATIC — built from historical data, never updated during paper trading.
   Every trade outcome is LOST information. The Trie can't learn from its mistakes.

Root cause analysis:
1. The `import numpy as np` on line 734 was added for Sharpe ratio calculation,
   but `np` is already imported at module level (line 36). Python determines variable
   scope at function definition time — an assignment (including import) inside a
   function makes it local throughout that function.
2. The Trie currently operates in "batch mode" only: train once, use forever.
   This means:
   - Pattern breaks (unexpected SAX symbols) are detected but NOT recorded
   - Winning/losing trade outcomes don't update the node that generated the signal
   - The Trie never improves — it can only repeat the same predictions

Changes:
1. Fix UnboundLocalError: Removed `import numpy as np` from line 734 in run()
   (module-level import on line 36 is sufficient)

2. Living Trie — _record_observation() function:
   When a trade closes (SL/TP/trailing_stop/pattern_break/end_of_data):
   a. Find the Trie node that was the basis of the entry prediction
   b. Update that node's metadata with the actual trade outcome:
      - actual_move_pct (real PnL%)
      - max_drawdown_pct (from SL distance or actual adverse move)
      - max_favorable_pct (from TP distance or actual favorable move)
      - duration (SAX symbol steps from entry to exit)
      - won (whether trade was profitable)
      - next_symbol (what SAX symbol followed — especially for pattern breaks)
   c. If next_symbol is NOT already a child node, CREATE IT as a new child
      This means: pattern ['a','d','b','h'] getting unexpected symbol 'f' now
      creates node ['a','d','b','h','f'] — the Trie literally GROWS
   d. Periodically re-propagate metadata (every 200 symbol steps) so parent
      nodes reflect the new observations

3. Added PaperTrade fields:
   - entry_sym_idx: SAX symbol index at entry (for duration calculation)
   - trie_updated: whether this trade's outcome was recorded in the Trie

4. Added PaperTraderConfig field:
   - living_trie: bool = True (enable/disable Living Trie)

5. Living Trie statistics printed at end:
   - Observations recorded, new nodes created, metadata propagations
   - Updated Trie is saved back to storage (so next run uses improved Trie)

The feedback loop:
  Trie predicts → Paper trade executes → Outcome observed → Trie node updated
  ↓                                                            ↓
  Next prediction uses improved metadata ←─────────────────────┘

This is the "Trie Viva" concept: the structure is alive, learning from each
trade. Pattern breaks don't just close positions — they create new knowledge.

Files modified:
- src/ppmt/engine/paper_trader.py: Removed local np import, added _record_observation(),
  Living Trie integration at every trade close, metadata re-propagation, stats display
- src/ppmt/__init__.py: version 0.2.8
- pyproject.toml: version 0.2.8
- src/ppmt/cli/main.py: version 0.2.8
- worklog-new.md: this entry

Stage Summary:
- Commit: (pending)
- Expected: No crash, Living Trie records every trade outcome, Trie grows from
  pattern breaks, improved metadata after re-propagation

