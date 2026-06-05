# Task 2-d: Meta Model Engine & Alpha Ranking Engine

## Summary
Created two critical institutional terminal services for the CryptoQuant Terminal project.

## Files Created
1. `/home/z/cryptoquant-terminal/src/lib/services/brain/meta-model-engine.ts` (~550 lines)
2. `/home/z/cryptoquant-terminal/src/lib/services/strategy/alpha-ranking-engine.ts` (~680 lines)

## Files Modified
1. `/home/z/cryptoquant-terminal/src/lib/services/brain/index.ts` - added meta-model-engine export
2. `/home/z/cryptoquant-terminal/src/lib/services/strategy/index.ts` - added alpha-ranking-engine export

## Service 1: Meta Model Engine
- SubEngineTracker: Tracks accuracy of 12 sub-engines with in-memory accumulators + FeedbackMetrics persistence
- DynamicWeightComputer: Computes weights with accuracy adjustment, regime boost, phase boost, smoothing, bounds
- MetaModelEngine: recordOutcome(), computeWeights(), getEngineReport(), getWeightedScore(), identifyWeakEngines(), identifyStrongEngines(), persist()
- Singleton: `metaModelEngine`

## Service 2: Alpha Ranking Engine
- Alpha Score: 5-component composite (signal 30%, risk-adj 25%, operability 20%, portfolio fit 15%, regime 10%)
- rankOpportunities(), getTopOpportunities(), computeAlphaScore(), suggestAllocation()
- Risk parity allocation with concentration limits
- Singleton: `alphaRankingEngine`

## Build Status
- TypeScript: 0 new errors in created files
- Pre-existing errors in UI components are unrelated
