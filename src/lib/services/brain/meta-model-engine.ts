/**
 * Meta Model Engine — CryptoQuant Terminal
 *
 * Evaluates the performance of each sub-engine in the Brain and dynamically
 * adjusts their weights in the final decision. Instead of giving all 12
 * sub-engines equal influence, this engine tracks accuracy per engine,
 * per regime, per token phase — and boosts or penalizes weights accordingly.
 *
 * Sub-engines tracked:
 *   1.  tokenLifecycle     — Token lifecycle phase detection
 *   2.  behavioralModel    — Trader behavior prediction
 *   3.  bigData            — Market regime, anomalies, whale forecast
 *   4.  candlestickPattern — 30+ candlestick patterns, multi-timeframe
 *   5.  deepAnalysis       — LLM + rule-based deep analysis
 *   6.  crossCorrelation   — P(outcome | trader + pattern + phase)
 *   7.  walletProfiler     — Smart money, whale, sniper scoring
 *   8.  botDetection       — 8 bot type classifiers
 *   9.  smartMoneyTracker  — Smart money flow tracking
 *  10.  buySellPressure    — Buy/sell pressure analysis
 *  11.  operabilityScore   — Fee-aware trade filtering
 *  12.  regimeHeuristic    — Market regime classification
 *
 * Persistence:
 *   Uses the existing FeedbackMetrics table with:
 *     sourceType: 'meta_model'
 *     sourceId:   engineName
 *     metricName: 'accuracy' | 'brier_score' | 'hit_rate'
 *     context:    JSON { regime, phase, token }
 *     period:     '7d' | '30d' | '90d'
 */

import { db } from '@/lib/db';
import type { MarketRegime } from '@/lib/services/strategy/regime-heuristic';

// ============================================================
// TYPES
// ============================================================

/** Names of the 12 sub-engines tracked by the meta model */
export type SubEngineName =
  | 'tokenLifecycle'
  | 'behavioralModel'
  | 'bigData'
  | 'candlestickPattern'
  | 'deepAnalysis'
  | 'crossCorrelation'
  | 'walletProfiler'
  | 'botDetection'
  | 'smartMoneyTracker'
  | 'buySellPressure'
  | 'operabilityScore'
  | 'regimeHeuristic';

/** Token lifecycle phases (aligned with token-lifecycle-engine) */
export type TokenPhase = 'GENESIS' | 'INCIPIENT' | 'GROWTH' | 'FOMO' | 'DECLINE' | 'LEGACY';

/** Simplified regime categories for per-regime accuracy tracking */
export type RegimeCategory = 'TRENDING' | 'RANGING' | 'PANIC';

/** Metrics tracked per sub-engine */
export interface EngineMetrics {
  accuracy: number;         // 0-1, overall hit rate
  brierScore: number;       // 0-1, lower is better (calibration)
  hitRate: number;          // 0-1, fraction of profitable signals
  falsePositiveRate: number; // 0-1, fraction of wrong bullish signals
  sampleSize: number;       // total observations
}

/** Accuracy broken down by regime and phase */
export interface ContextualMetrics {
  byRegime: Record<RegimeCategory, EngineMetrics>;
  byPhase: Record<TokenPhase, EngineMetrics>;
}

/** Rolling window accuracy for an engine */
export interface RollingAccuracy {
  d7: number;   // 7-day accuracy (0-1)
  d30: number;  // 30-day accuracy (0-1)
  d90: number;  // 90-day accuracy (0-1)
}

/** Full report for a single engine */
export interface EngineReport {
  engineName: SubEngineName;
  overall: EngineMetrics;
  rolling: RollingAccuracy;
  contextual: ContextualMetrics;
  currentWeight: number;
  weightChange: number; // % change from previous cycle
}

/** Context provided when recording a prediction outcome */
export interface PredictionContext {
  regime?: MarketRegime;
  tokenPhase?: TokenPhase;
  tokenAddress?: string;
  chain?: string;
}

/** The outcome of a prediction to record */
export interface PredictionOutcome {
  prediction: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  confidence: number;       // 0-1, how confident the engine was
  actualOutcome: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  pnlPct?: number;          // realized PnL % if available
}

// ============================================================
// CONSTANTS
// ============================================================

/** All 12 sub-engine names */
export const SUB_ENGINE_NAMES: SubEngineName[] = [
  'tokenLifecycle',
  'behavioralModel',
  'bigData',
  'candlestickPattern',
  'deepAnalysis',
  'crossCorrelation',
  'walletProfiler',
  'botDetection',
  'smartMoneyTracker',
  'buySellPressure',
  'operabilityScore',
  'regimeHeuristic',
];

const ENGINE_COUNT = SUB_ENGINE_NAMES.length;
const BASE_WEIGHT = 1 / ENGINE_COUNT;

/** Weight adjustment bounds */
const MIN_WEIGHT_MULTIPLIER = 0.5;
const MAX_WEIGHT_MULTIPLIER = 3.0;

/** Smoothing: maximum % change per cycle */
const MAX_WEIGHT_CHANGE_PCT = 0.20;

/** Regime boost threshold */
const REGIME_ACCURACY_BOOST_THRESHOLD = 0.70;
const REGIME_BOOST_FACTOR = 1.20;

/** Phase boost threshold */
const PHASE_ACCURACY_BOOST_THRESHOLD = 0.70;
const PHASE_BOOST_FACTOR = 1.15;

/** Weak/strong engine thresholds (30d accuracy) */
const WEAK_ENGINE_THRESHOLD = 0.55;
const STRONG_ENGINE_THRESHOLD = 0.75;

/** Risk-free rate for Sharpe-like calculations (annual, crypto) */
const RISK_FREE_RATE = 0.0;

/** Map from detailed MarketRegime to simplified RegimeCategory */
function toRegimeCategory(regime?: MarketRegime): RegimeCategory {
  if (!regime) return 'RANGING';
  if (regime === 'TRENDING_UP' || regime === 'TRENDING_DOWN') return 'TRENDING';
  if (regime === 'HIGH_VOLATILITY') return 'PANIC';
  return 'RANGING';
}

// ============================================================
// SUB-ENGINE TRACKER
// ============================================================

/**
 * Tracks accuracy of each sub-engine across regimes, phases, and time windows.
 * Uses in-memory caches backed by FeedbackMetrics in the database.
 */
class SubEngineTracker {
  private cache = new Map<string, EngineMetrics>();
  private rollingCache = new Map<string, RollingAccuracy>();
  private contextualCache = new Map<string, ContextualMetrics>();
  private lastCacheRefresh = 0;
  private readonly CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

  // ---- In-memory accumulators for the current cycle ----
  private cycleAccumulators = new Map<string, {
    hits: number;
    misses: number;
    falsePositives: number;
    brierSum: number;
    pnlSum: number;
    count: number;
  }>();

  private cycleContextAccumulators = new Map<string, {
    hits: number;
    misses: number;
    falsePositives: number;
    brierSum: number;
    count: number;
  }>();

  /** Record a single prediction outcome for an engine */
  recordOutcome(
    engineName: SubEngineName,
    prediction: PredictionOutcome,
    context: PredictionContext,
  ): void {
    // --- Overall accumulator ---
    const key = engineName;
    const acc = this.getOrCreateAccumulator(key);

    const wasCorrect = prediction.prediction === prediction.actualOutcome;
    if (wasCorrect) {
      acc.hits++;
    } else {
      acc.misses++;
      // False positive: engine said BULLISH but actual was not BULLISH
      if (prediction.prediction === 'BULLISH' && prediction.actualOutcome !== 'BULLISH') {
        acc.falsePositives++;
      }
    }
    acc.brierSum += (prediction.confidence - (wasCorrect ? 1 : 0)) ** 2;
    if (prediction.pnlPct !== undefined) {
      acc.pnlSum += prediction.pnlPct;
    }
    acc.count++;

    // --- Context accumulator: regime ---
    if (context.regime) {
      const regimeCategory = toRegimeCategory(context.regime);
      const regimeKey = `${engineName}:regime:${regimeCategory}`;
      const regimeAcc = this.getOrCreateContextAccumulator(regimeKey);
      if (wasCorrect) {
        regimeAcc.hits++;
      } else {
        regimeAcc.misses++;
        if (prediction.prediction === 'BULLISH' && prediction.actualOutcome !== 'BULLISH') {
          regimeAcc.falsePositives++;
        }
      }
      regimeAcc.brierSum += (prediction.confidence - (wasCorrect ? 1 : 0)) ** 2;
      regimeAcc.count++;
    }

    // --- Context accumulator: phase ---
    if (context.tokenPhase) {
      const phaseKey = `${engineName}:phase:${context.tokenPhase}`;
      const phaseAcc = this.getOrCreateContextAccumulator(phaseKey);
      if (wasCorrect) {
        phaseAcc.hits++;
      } else {
        phaseAcc.misses++;
        if (prediction.prediction === 'BULLISH' && prediction.actualOutcome !== 'BULLISH') {
          phaseAcc.falsePositives++;
        }
      }
      phaseAcc.brierSum += (prediction.confidence - (wasCorrect ? 1 : 0)) ** 2;
      phaseAcc.count++;
    }
  }

  /** Get overall metrics for an engine (from cache or DB) */
  async getMetrics(engineName: SubEngineName): Promise<EngineMetrics> {
    await this.ensureCacheFresh();
    const cached = this.cache.get(engineName);
    if (cached) return cached;

    // Fall back to DB computation
    return this.computeMetricsFromDB(engineName);
  }

  /** Get rolling accuracy for an engine */
  async getRollingAccuracy(engineName: SubEngineName): Promise<RollingAccuracy> {
    await this.ensureCacheFresh();
    const cached = this.rollingCache.get(engineName);
    if (cached) return cached;

    return this.computeRollingFromDB(engineName);
  }

  /** Get contextual metrics (by regime and phase) for an engine */
  async getContextualMetrics(engineName: SubEngineName): Promise<ContextualMetrics> {
    await this.ensureCacheFresh();
    const cached = this.contextualCache.get(engineName);
    if (cached) return cached;

    return this.computeContextualFromDB(engineName);
  }

  /** Persist accumulated outcomes to FeedbackMetrics */
  async flushToDB(): Promise<number> {
    let stored = 0;
    const now = new Date();

    // Flush overall accumulators
    for (const [key, acc] of this.cycleAccumulators.entries()) {
      if (acc.count === 0) continue;

      const engineName = key as SubEngineName;
      const accuracy = acc.hits / acc.count;
      const brierScore = acc.brierSum / acc.count;
      const hitRate = acc.hits / acc.count;
      const falsePositiveRate = acc.count > 0 ? acc.falsePositives / acc.count : 0;

      // Store accuracy metric
      await db.feedbackMetrics.create({
        data: {
          sourceType: 'meta_model',
          sourceId: engineName,
          metricName: 'accuracy',
          metricValue: accuracy,
          context: JSON.stringify({ sampleSize: acc.count, type: 'overall' }),
          period: '24h',
          measuredAt: now,
        },
      });

      // Store Brier score
      await db.feedbackMetrics.create({
        data: {
          sourceType: 'meta_model',
          sourceId: engineName,
          metricName: 'brier_score',
          metricValue: brierScore,
          context: JSON.stringify({ sampleSize: acc.count, type: 'overall' }),
          period: '24h',
          measuredAt: now,
        },
      });

      // Store hit rate
      await db.feedbackMetrics.create({
        data: {
          sourceType: 'meta_model',
          sourceId: engineName,
          metricName: 'hit_rate',
          metricValue: hitRate,
          context: JSON.stringify({
            sampleSize: acc.count,
            falsePositiveRate,
            type: 'overall',
          }),
          period: '24h',
          measuredAt: now,
        },
      });

      stored += 3;
    }

    // Flush context accumulators
    for (const [key, acc] of this.cycleContextAccumulators.entries()) {
      if (acc.count === 0) continue;

      // Parse key: "engineName:regime:CATEGORY" or "engineName:phase:PHASE"
      const parts = key.split(':');
      if (parts.length !== 3) continue;
      const [engineName, contextType, contextValue] = parts as [SubEngineName, string, string];

      const accuracy = acc.hits / acc.count;
      const brierScore = acc.brierSum / acc.count;
      const hitRate = acc.hits / acc.count;
      const falsePositiveRate = acc.count > 0 ? acc.falsePositives / acc.count : 0;

      const contextObj: Record<string, unknown> = {
        sampleSize: acc.count,
        type: contextType,
        [contextType]: contextValue,
      };

      await db.feedbackMetrics.create({
        data: {
          sourceType: 'meta_model',
          sourceId: engineName,
          metricName: 'accuracy',
          metricValue: accuracy,
          context: JSON.stringify(contextObj),
          period: '24h',
          measuredAt: now,
        },
      });

      await db.feedbackMetrics.create({
        data: {
          sourceType: 'meta_model',
          sourceId: engineName,
          metricName: 'brier_score',
          metricValue: brierScore,
          context: JSON.stringify(contextObj),
          period: '24h',
          measuredAt: now,
        },
      });

      await db.feedbackMetrics.create({
        data: {
          sourceType: 'meta_model',
          sourceId: engineName,
          metricName: 'hit_rate',
          metricValue: hitRate,
          context: JSON.stringify({ ...contextObj, falsePositiveRate }),
          period: '24h',
          measuredAt: now,
        },
      });

      stored += 3;
    }

    // Clear accumulators after flush
    this.cycleAccumulators.clear();
    this.cycleContextAccumulators.clear();
    this.invalidateCache();

    return stored;
  }

  // ---- Private helpers ----

  private getOrCreateAccumulator(key: string): {
    hits: number; misses: number; falsePositives: number;
    brierSum: number; pnlSum: number; count: number;
  } {
    let acc = this.cycleAccumulators.get(key);
    if (!acc) {
      acc = { hits: 0, misses: 0, falsePositives: 0, brierSum: 0, pnlSum: 0, count: 0 };
      this.cycleAccumulators.set(key, acc);
    }
    return acc;
  }

  private getOrCreateContextAccumulator(key: string): {
    hits: number; misses: number; falsePositives: number;
    brierSum: number; count: number;
  } {
    let acc = this.cycleContextAccumulators.get(key);
    if (!acc) {
      acc = { hits: 0, misses: 0, falsePositives: 0, brierSum: 0, count: 0 };
      this.cycleContextAccumulators.set(key, acc);
    }
    return acc;
  }

  private invalidateCache(): void {
    this.cache.clear();
    this.rollingCache.clear();
    this.contextualCache.clear();
    this.lastCacheRefresh = 0;
  }

  private async ensureCacheFresh(): Promise<void> {
    const now = Date.now();
    if (now - this.lastCacheRefresh < this.CACHE_TTL_MS) return;

    // Refresh caches from DB
    for (const engineName of SUB_ENGINE_NAMES) {
      const metrics = await this.computeMetricsFromDB(engineName);
      this.cache.set(engineName, metrics);

      const rolling = await this.computeRollingFromDB(engineName);
      this.rollingCache.set(engineName, rolling);

      const contextual = await this.computeContextualFromDB(engineName);
      this.contextualCache.set(engineName, contextual);
    }

    this.lastCacheRefresh = Date.now();
  }

  /** Compute overall EngineMetrics from FeedbackMetrics in DB */
  private async computeMetricsFromDB(engineName: SubEngineName): Promise<EngineMetrics> {
    const rows = await db.feedbackMetrics.findMany({
      where: {
        sourceType: 'meta_model',
        sourceId: engineName,
        metricName: 'accuracy',
      },
      orderBy: { measuredAt: 'desc' },
      take: 500, // last 500 accuracy measurements
    });

    if (rows.length === 0) {
      return this.defaultMetrics();
    }

    const totalSampleSize = rows.length;
    const avgAccuracy = rows.reduce((s, r) => s + r.metricValue, 0) / totalSampleSize;

    // Get Brier score
    const brierRows = await db.feedbackMetrics.findMany({
      where: {
        sourceType: 'meta_model',
        sourceId: engineName,
        metricName: 'brier_score',
      },
      orderBy: { measuredAt: 'desc' },
      take: 500,
    });
    const avgBrier = brierRows.length > 0
      ? brierRows.reduce((s, r) => s + r.metricValue, 0) / brierRows.length
      : 0.25; // Default for a random predictor

    // Get hit rate and false positive rate from context
    const hitRateRows = await db.feedbackMetrics.findMany({
      where: {
        sourceType: 'meta_model',
        sourceId: engineName,
        metricName: 'hit_rate',
      },
      orderBy: { measuredAt: 'desc' },
      take: 500,
    });
    const avgHitRate = hitRateRows.length > 0
      ? hitRateRows.reduce((s, r) => s + r.metricValue, 0) / hitRateRows.length
      : avgAccuracy;

    // Extract false positive rate from context
    let totalFPR = 0;
    let fprCount = 0;
    for (const row of hitRateRows) {
      try {
        const ctx = JSON.parse(row.context) as { falsePositiveRate?: number };
        if (typeof ctx.falsePositiveRate === 'number') {
          totalFPR += ctx.falsePositiveRate;
          fprCount++;
        }
      } catch {
        // Skip malformed context
      }
    }
    const avgFPR = fprCount > 0 ? totalFPR / fprCount : 0;

    return {
      accuracy: avgAccuracy,
      brierScore: avgBrier,
      hitRate: avgHitRate,
      falsePositiveRate: avgFPR,
      sampleSize: totalSampleSize,
    };
  }

  /** Compute rolling accuracy from DB */
  private async computeRollingFromDB(engineName: SubEngineName): Promise<RollingAccuracy> {
    const now = new Date();
    const windows: Array<{ days: number; key: 'd7' | 'd30' | 'd90' }> = [
      { days: 7, key: 'd7' },
      { days: 30, key: 'd30' },
      { days: 90, key: 'd90' },
    ];

    const result: RollingAccuracy = { d7: 0.5, d30: 0.5, d90: 0.5 };

    for (const window of windows) {
      const cutoff = new Date(now.getTime() - window.days * 24 * 60 * 60 * 1000);
      const rows = await db.feedbackMetrics.findMany({
        where: {
          sourceType: 'meta_model',
          sourceId: engineName,
          metricName: 'accuracy',
          measuredAt: { gte: cutoff },
        },
      });

      if (rows.length > 0) {
        result[window.key] = rows.reduce((s, r) => s + r.metricValue, 0) / rows.length;
      }
    }

    return result;
  }

  /** Compute contextual metrics from DB */
  private async computeContextualFromDB(engineName: SubEngineName): Promise<ContextualMetrics> {
    const regimeCategories: RegimeCategory[] = ['TRENDING', 'RANGING', 'PANIC'];
    const phases: TokenPhase[] = ['GENESIS', 'INCIPIENT', 'GROWTH', 'FOMO', 'DECLINE', 'LEGACY'];

    const byRegime: Record<string, EngineMetrics> = {};
    const byPhase: Record<string, EngineMetrics> = {};

    // Fetch all accuracy rows with context
    const rows = await db.feedbackMetrics.findMany({
      where: {
        sourceType: 'meta_model',
        sourceId: engineName,
        metricName: 'accuracy',
      },
      orderBy: { measuredAt: 'desc' },
      take: 1000,
    });

    // Group by regime
    for (const regime of regimeCategories) {
      const regimeRows = rows.filter(r => {
        try {
          const ctx = JSON.parse(r.context) as { regime?: string; type?: string };
          return ctx.type === 'regime' && ctx.regime === regime;
        } catch {
          return false;
        }
      });

      if (regimeRows.length > 0) {
        byRegime[regime] = {
          accuracy: regimeRows.reduce((s, r) => s + r.metricValue, 0) / regimeRows.length,
          brierScore: 0.25,
          hitRate: regimeRows.reduce((s, r) => s + r.metricValue, 0) / regimeRows.length,
          falsePositiveRate: 0,
          sampleSize: regimeRows.length,
        };
      } else {
        byRegime[regime] = this.defaultMetrics();
      }
    }

    // Group by phase
    for (const phase of phases) {
      const phaseRows = rows.filter(r => {
        try {
          const ctx = JSON.parse(r.context) as { phase?: string; type?: string };
          return ctx.type === 'phase' && ctx.phase === phase;
        } catch {
          return false;
        }
      });

      if (phaseRows.length > 0) {
        byPhase[phase] = {
          accuracy: phaseRows.reduce((s, r) => s + r.metricValue, 0) / phaseRows.length,
          brierScore: 0.25,
          hitRate: phaseRows.reduce((s, r) => s + r.metricValue, 0) / phaseRows.length,
          falsePositiveRate: 0,
          sampleSize: phaseRows.length,
        };
      } else {
        byPhase[phase] = this.defaultMetrics();
      }
    }

    return {
      byRegime: byRegime as Record<RegimeCategory, EngineMetrics>,
      byPhase: byPhase as Record<TokenPhase, EngineMetrics>,
    };
  }

  private defaultMetrics(): EngineMetrics {
    return {
      accuracy: 0.5,
      brierScore: 0.25,
      hitRate: 0.5,
      falsePositiveRate: 0.33,
      sampleSize: 0,
    };
  }
}

// ============================================================
// DYNAMIC WEIGHT COMPUTER
// ============================================================

/**
 * Computes dynamic weights for each sub-engine based on:
 *  - Base weight (equal 1/12)
 *  - Recent accuracy adjustment
 *  - Regime-specific boost
 *  - Phase-specific boost
 *  - Smoothing (limit ±20% per cycle)
 *  - Bounds (0.5x to 3.0x base)
 *  - Normalization (weights sum to 1.0)
 */
class DynamicWeightComputer {
  /** Previous cycle weights for smoothing */
  private previousWeights = new Map<SubEngineName, number>();

  /** Initialize with equal weights */
  constructor() {
    for (const name of SUB_ENGINE_NAMES) {
      this.previousWeights.set(name, BASE_WEIGHT);
    }
  }

  /**
   * Compute weights for all engines given their metrics and current context.
   *
   * Algorithm:
   *  1. Start with base weight = 1/12
   *  2. Adjust by accuracy: weight = base * (accuracy / avg_accuracy)
   *  3. Boost by regime if accuracy > 70% in current regime (+20%)
   *  4. Boost by phase if accuracy > 70% for current phase (+15%)
   *  5. Apply bounds: [0.5 * base, 3.0 * base]
   *  6. Apply smoothing: limit change to ±20% of previous weight
   *  7. Normalize: all weights sum to 1.0
   */
  async computeWeights(
    metricsMap: Map<SubEngineName, EngineMetrics>,
    contextualMap: Map<SubEngineName, ContextualMetrics>,
    regime?: MarketRegime,
    tokenPhase?: TokenPhase,
  ): Promise<Map<SubEngineName, number>> {
    const weights = new Map<SubEngineName, number>();
    const regimeCategory = toRegimeCategory(regime);

    // Step 1: Compute average accuracy
    let totalAccuracy = 0;
    let count = 0;
    for (const name of SUB_ENGINE_NAMES) {
      const metrics = metricsMap.get(name);
      if (metrics && metrics.sampleSize > 0) {
        totalAccuracy += metrics.accuracy;
        count++;
      }
    }
    const avgAccuracy = count > 0 ? totalAccuracy / count : 0.5;

    // Step 2-4: Compute raw weights
    for (const name of SUB_ENGINE_NAMES) {
      const metrics = metricsMap.get(name);
      const contextual = contextualMap.get(name);
      let weight = BASE_WEIGHT;

      // Accuracy adjustment
      if (metrics && metrics.sampleSize > 0 && avgAccuracy > 0) {
        weight = BASE_WEIGHT * (metrics.accuracy / avgAccuracy);
      }

      // Regime boost
      if (regime && contextual) {
        const regimeMetrics = contextual.byRegime[regimeCategory];
        if (regimeMetrics && regimeMetrics.accuracy > REGIME_ACCURACY_BOOST_THRESHOLD) {
          weight *= REGIME_BOOST_FACTOR;
        }
      }

      // Phase boost
      if (tokenPhase && contextual) {
        const phaseMetrics = contextual.byPhase[tokenPhase];
        if (phaseMetrics && phaseMetrics.accuracy > PHASE_ACCURACY_BOOST_THRESHOLD) {
          weight *= PHASE_BOOST_FACTOR;
        }
      }

      // Step 5: Apply bounds
      weight = Math.max(BASE_WEIGHT * MIN_WEIGHT_MULTIPLIER, Math.min(BASE_WEIGHT * MAX_WEIGHT_MULTIPLIER, weight));

      weights.set(name, weight);
    }

    // Step 6: Apply smoothing
    const smoothedWeights = new Map<SubEngineName, number>();
    for (const name of SUB_ENGINE_NAMES) {
      const rawWeight = weights.get(name) ?? BASE_WEIGHT;
      const prevWeight = this.previousWeights.get(name) ?? BASE_WEIGHT;
      const maxChange = prevWeight * MAX_WEIGHT_CHANGE_PCT;
      const smoothed = Math.max(
        prevWeight - maxChange,
        Math.min(prevWeight + maxChange, rawWeight),
      );
      smoothedWeights.set(name, Math.max(0, smoothed));
    }

    // Step 7: Normalize to sum to 1.0
    const sum = Array.from(smoothedWeights.values()).reduce((s, w) => s + w, 0);
    const normalizedWeights = new Map<SubEngineName, number>();
    for (const name of SUB_ENGINE_NAMES) {
      const weight = smoothedWeights.get(name) ?? BASE_WEIGHT;
      normalizedWeights.set(name, sum > 0 ? weight / sum : BASE_WEIGHT);
    }

    // Store for next cycle's smoothing
    for (const [name, weight] of normalizedWeights.entries()) {
      this.previousWeights.set(name, weight);
    }

    return normalizedWeights;
  }

  /** Get the previous cycle's weights */
  getPreviousWeights(): Map<SubEngineName, number> {
    return new Map(this.previousWeights);
  }
}

// ============================================================
// META MODEL ENGINE CLASS
// ============================================================

class MetaModelEngine {
  private tracker = new SubEngineTracker();
  private weightComputer = new DynamicWeightComputer();
  private currentWeights = new Map<SubEngineName, number>();

  constructor() {
    // Initialize with equal weights
    for (const name of SUB_ENGINE_NAMES) {
      this.currentWeights.set(name, BASE_WEIGHT);
    }
  }

  /**
   * Record a prediction outcome for a specific sub-engine.
   *
   * This is the main entry point for the feedback loop: each time a
   * sub-engine makes a prediction and the actual outcome is known,
   * call this method to track the engine's accuracy.
   *
   * @param engineName - Which sub-engine made the prediction
   * @param prediction - The prediction details (direction, confidence, actual outcome)
   * @param actualOutcome - What actually happened
   * @param context - Market context at the time of prediction
   */
  recordOutcome(
    engineName: SubEngineName,
    prediction: 'BULLISH' | 'BEARISH' | 'NEUTRAL',
    actualOutcome: 'BULLISH' | 'BEARISH' | 'NEUTRAL',
    context: PredictionContext = {},
    confidence: number = 0.5,
    pnlPct?: number,
  ): void {
    this.tracker.recordOutcome(engineName, {
      prediction,
      confidence,
      actualOutcome,
      pnlPct,
    }, context);
  }

  /**
   * Compute current weights for all sub-engines.
   *
   * Takes into account recent accuracy, regime-specific performance,
   * and phase-specific performance. Weights are smoothed to prevent
   * whipsawing and bounded to prevent any single engine from dominating.
   *
   * @param regime - Current market regime (optional, for regime boost)
   * @param tokenPhase - Current token phase (optional, for phase boost)
   * @returns Map of engine name → weight (sums to 1.0)
   */
  async computeWeights(
    regime?: MarketRegime,
    tokenPhase?: TokenPhase,
  ): Promise<Map<SubEngineName, number>> {
    const metricsMap = new Map<SubEngineName, EngineMetrics>();
    const contextualMap = new Map<SubEngineName, ContextualMetrics>();

    for (const name of SUB_ENGINE_NAMES) {
      metricsMap.set(name, await this.tracker.getMetrics(name));
      contextualMap.set(name, await this.tracker.getContextualMetrics(name));
    }

    this.currentWeights = await this.weightComputer.computeWeights(
      metricsMap,
      contextualMap,
      regime,
      tokenPhase,
    );

    return this.currentWeights;
  }

  /**
   * Get a full accuracy report for each engine.
   *
   * Returns comprehensive metrics including rolling accuracy, contextual
   * breakdowns, current weight, and weight change from previous cycle.
   */
  async getEngineReport(): Promise<EngineReport[]> {
    const reports: EngineReport[] = [];
    const previousWeights = this.weightComputer.getPreviousWeights();

    for (const name of SUB_ENGINE_NAMES) {
      const overall = await this.tracker.getMetrics(name);
      const rolling = await this.tracker.getRollingAccuracy(name);
      const contextual = await this.tracker.getContextualMetrics(name);
      const currentWeight = this.currentWeights.get(name) ?? BASE_WEIGHT;
      const previousWeight = previousWeights.get(name) ?? BASE_WEIGHT;

      const weightChange = previousWeight > 0
        ? ((currentWeight - previousWeight) / previousWeight) * 100
        : 0;

      reports.push({
        engineName: name,
        overall,
        rolling,
        contextual,
        currentWeight,
        weightChange: Math.round(weightChange * 100) / 100,
      });
    }

    return reports;
  }

  /**
   * Combine scores from multiple sub-engines using dynamic weights.
   *
   * Each engine provides a score (typically 0-1 or -1 to 1), and this
   * method computes the weighted average using the current dynamic weights.
   *
   * @param engineScores - Map of engine name → score
   * @returns Weighted composite score
   */
  getWeightedScore(engineScores: Map<string, number>): number {
    let weightedSum = 0;
    let totalWeight = 0;

    for (const name of SUB_ENGINE_NAMES) {
      const score = engineScores.get(name);
      const weight = this.currentWeights.get(name);
      if (score !== undefined && weight !== undefined) {
        weightedSum += score * weight;
        totalWeight += weight;
      }
    }

    return totalWeight > 0 ? weightedSum / totalWeight : 0;
  }

  /**
   * Identify weak engines: those with 30-day accuracy below 55%.
   *
   * These engines should be monitored closely and may need to be
   * disabled or have their confidence thresholds raised.
   */
  async identifyWeakEngines(): Promise<Array<{ engineName: SubEngineName; accuracy: number }>> {
    const weak: Array<{ engineName: SubEngineName; accuracy: number }> = [];

    for (const name of SUB_ENGINE_NAMES) {
      const rolling = await this.tracker.getRollingAccuracy(name);
      if (rolling.d30 < WEAK_ENGINE_THRESHOLD) {
        weak.push({ engineName: name, accuracy: rolling.d30 });
      }
    }

    return weak.sort((a, b) => a.accuracy - b.accuracy);
  }

  /**
   * Identify strong engines: those with 30-day accuracy above 75%.
   *
   * These engines are performing well and should be given more weight
   * in the final decision.
   */
  async identifyStrongEngines(): Promise<Array<{ engineName: SubEngineName; accuracy: number }>> {
    const strong: Array<{ engineName: SubEngineName; accuracy: number }> = [];

    for (const name of SUB_ENGINE_NAMES) {
      const rolling = await this.tracker.getRollingAccuracy(name);
      if (rolling.d30 > STRONG_ENGINE_THRESHOLD) {
        strong.push({ engineName: name, accuracy: rolling.d30 });
      }
    }

    return strong.sort((a, b) => b.accuracy - a.accuracy);
  }

  /**
   * Persist accumulated outcomes to the database.
   *
   * Should be called at the end of each brain cycle to flush
   * in-memory accumulators to FeedbackMetrics.
   *
   * @returns Number of metric records stored
   */
  async persist(): Promise<number> {
    return this.tracker.flushToDB();
  }

  /**
   * Get the current weights without recomputing.
   */
  getCurrentWeights(): Map<SubEngineName, number> {
    return new Map(this.currentWeights);
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const metaModelEngine = new MetaModelEngine();
