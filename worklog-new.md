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
- Commit: (pending)
- Expected: Higher WR (wider SL avoids noise exits), fewer but better SHORT trades,
  more total trades (max_drawdown at 80% blocks fewer signals)
