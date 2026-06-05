# Task 5-b: Market Regime Engine Upgrade

## Agent: Main Agent

## Task
Upgrade the Market Regime Engine for CryptoQuant Terminal — replace basic MA(7) vs MA(25) + volatility percentile with HMM-inspired multi-factor regime detection.

## Work Summary

### Files Created
- `/home/z/cryptoquant-terminal/src/lib/services/strategy/market-regime-engine.ts` (~900 lines)

### Files Modified
- `/home/z/cryptoquant-terminal/src/lib/services/strategy/index.ts` (added barrel export)

### Key Implementation Details

**7 New Regime Types** (unified across system):
- TRENDING_BULL, TRENDING_BEAR, RANGING, ACCUMULATION, DISTRIBUTION, PANIC, EUPHORIA

**5 Detection Factors**:
1. Trend Strength (-1 to 1): MA alignment, ADX, EMA crossovers
2. Volatility Regime (0 to 1): Realized vol ratio, BB width percentile, ATR percentile
3. Volume Profile (0 to 1): Volume trend, volume vs avg, up-volume ratio
4. Smart Money Flow (-1 to 1): On-chain data or price-volume estimation
5. Momentum (-1 to 1): RSI, ROC, MACD histogram

**HMM-Inspired Classification**:
- Sigmoid scoring matrix for smooth regime transitions
- Each regime has weighted factor matching with adjustable steepness
- Confidence derived from gap between best and second-best regime scores

**Transition Probabilities**:
- Base 7×7 empirical prior matrix
- DB-backed transitions from TokenLifecycleState + PredictiveSignal history
- Bayesian blending with BETA=20 prior strength
- Factor-based emission influence for real-time adjustment

**Backward Compatibility**:
- Re-exports legacy `regimeHeuristic` singleton
- Maps legacy regime names to new regime names
- Falls back to legacy engine when < 25 data points
- Existing consumers work unchanged

### Build Status
- TypeScript: 0 errors (verified via tsc --noEmit)
- No new lint issues
