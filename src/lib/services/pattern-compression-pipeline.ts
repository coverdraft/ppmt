/**
 * Pattern Compression Pipeline - CryptoQuant Terminal
 * Pipeline de Compresión de Patrones: Data Cruda → Conocimiento Destilado
 *
 * El problema: el sistema acumula velas, transacciones y señales
 * sin comprimir. Con miles de tokens, esto escala mal.
 *
 * La solución: este pipeline comprime data cruda en patrones
 * reutilizables que capturan el COMPORTAMIENTO, no los datos.
 *
 * Flujo:
 *   OHLCV crudo → Patrones de vela detectados → Comportamiento destilado
 *   Transacciones crudas → Perfiles de trader → Arquetipos comportamentales
 *   Señales crudas → Tasas de éxito → Probabilidades condicionales
 *
 * No guardamos las 847 velas. Guardamos:
 *   "Bullish Engulfing en GROWTH con SMART_MONEY comprando → 73% win rate, 847 samples"
 *
 * Esto es lo que permite al sistema ESCALAR a miles de tokens.
 */

import { db } from '../db';
import { type TokenPhase, type TraderArchetype } from './token-lifecycle-engine';
import { type DetectedPattern, type PatternSentiment } from './candlestick-pattern-engine';
import { type MarketOutcome, type ConditionKey } from './cross-correlation-engine';
import {
  proportionConfidenceInterval,
  assessSampleSufficiency,
  type SampleSufficiency,
} from './statistical-validation';

// ============================================================
// TYPES & INTERFACES
// ============================================================

export interface DistilledPattern {
  id: string;
  category: 'CANDLE_BEHAVIOR' | 'TRADER_BEHAVIOR' | 'PHASE_TRANSITION' | 'STRATEGY_OUTCOME';
  key: string; // Unique identifier for this pattern
  description: string; // Human-readable description
  totalObservations: number;
  effectiveObservations: number; // After temporal decay
  outcomeBreakdown: Record<string, number>; // e.g., { BULLISH: 615, BEARISH: 132, NEUTRAL: 100 }
  probabilityEstimates: Record<string, number>; // Smoothed probabilities
  confidenceInterval: { lower: number; upper: number };
  sampleSufficiency: SampleSufficiency;
  avgPriceChangePct: number;
  medianPriceChangePct: number;
  lastObserved: Date;
  firstObserved: Date;
  decayRate: number; // How quickly this pattern's relevance decays
  reliability: 'LOW' | 'MODERATE' | 'HIGH' | 'VERY_HIGH';
  metadata: Record<string, unknown>;
}

export interface CompressionResult {
  patternsCompressed: number;
  rawRecordsProcessed: number;
  storageSavedEstimate: number; // Estimated bytes saved
  newPatterns: number;
  updatedPatterns: number;
  discardedPatterns: number; // Patterns that decayed below relevance
}

export interface PatternQuery {
  category?: DistilledPattern['category'];
  traderArchetype?: TraderArchetype;
  tokenPhase?: TokenPhase;
  candlePattern?: string;
  minReliability?: DistilledPattern['reliability'];
  minObservations?: number;
}

// ============================================================
// CONSTANTS
// ============================================================

const MIN_OBSERVATIONS_TO_KEEP = 5; // Below this, discard the pattern
const DECAY_THRESHOLD = 0.05; // Below this effective observation ratio, discard
const MAX_PATTERNS_PER_CATEGORY = 500; // Limit patterns per category

// ============================================================
// PATTERN COMPRESSION ENGINE
// ============================================================

class PatternCompressionPipeline {
  /**
   * Run the compression pipeline
   * 1. Compress raw candle data into pattern summaries
   * 2. Compress raw transactions into behavioral archetypes
   * 3. Compress raw signals into outcome probabilities
   * 4. Apply temporal decay
   * 5. Discard irrelevant patterns
   */
  async runCompression(): Promise<CompressionResult> {
    let patternsCompressed = 0;
    let rawRecordsProcessed = 0;
    let newPatterns = 0;
    let updatedPatterns = 0;
    let discardedPatterns = 0;

    // === 1. Compress candlestick pattern observations ===
    const candleResult = await this.compressCandlePatterns();
    patternsCompressed += candleResult.patternsCompressed;
    rawRecordsProcessed += candleResult.rawRecordsProcessed;
    newPatterns += candleResult.newPatterns;
    updatedPatterns += candleResult.updatedPatterns;

    // === 2. Compress trader behavior observations ===
    const traderResult = await this.compressTraderBehavior();
    patternsCompressed += traderResult.patternsCompressed;
    rawRecordsProcessed += traderResult.rawRecordsProcessed;
    newPatterns += traderResult.newPatterns;
    updatedPatterns += traderResult.updatedPatterns;

    // === 3. Compress cross-correlation observations ===
    const correlationResult = await this.compressCorrelations();
    patternsCompressed += correlationResult.patternsCompressed;
    rawRecordsProcessed += correlationResult.rawRecordsProcessed;
    newPatterns += correlationResult.newPatterns;
    updatedPatterns += correlationResult.updatedPatterns;

    // === 4. Apply temporal decay and discard irrelevant patterns ===
    const discardResult = await this.applyDecayAndCleanup();
    discardedPatterns = discardResult.discarded;

    // === 5. Enforce storage limits ===
    await this.enforceStorageLimits();

    const storageSavedEstimate = rawRecordsProcessed * 500 - patternsCompressed * 2000; // Rough estimate

    return {
      patternsCompressed,
      rawRecordsProcessed,
      storageSavedEstimate: Math.max(0, storageSavedEstimate),
      newPatterns,
      updatedPatterns,
      discardedPatterns,
    };
  }

  /**
   * Compress candlestick pattern signals into distilled pattern summaries
   */
  private async compressCandlePatterns(): Promise<{
    patternsCompressed: number; rawRecordsProcessed: number; newPatterns: number; updatedPatterns: number;
  }> {
    let patternsCompressed = 0;
    let rawRecordsProcessed = 0;
    let newPatterns = 0;
    let updatedPatterns = 0;

    // Get all candlestick pattern signals
    const patternSignals = await db.signal.findMany({
      where: { type: 'CANDLESTICK_PATTERN' },
      orderBy: { createdAt: 'desc' },
      take: 5000,
    });

    rawRecordsProcessed += patternSignals.length;

    // Group by pattern name + phase + sentiment
    const groups = new Map<string, typeof patternSignals>();

    for (const signal of patternSignals) {
      try {
        const meta = JSON.parse(signal.metadata || '{}');
        const phase = signal.tokenId ? await this.getTokenPhaseById(signal.tokenId) : 'UNKNOWN';
        const key = `CANDLE:${meta.patternName}:${phase}:${meta.sentiment}`;

        if (!groups.has(key)) groups.set(key, []);
        groups.get(key)!.push(signal);
      } catch {
        // Skip malformed
      }
    }

    // Compress each group into a distilled pattern
    for (const [key, signals] of groups) {
      if (signals.length < 2) continue;

      const meta0 = JSON.parse(signals[0].metadata || '{}');
      const pattern = this.buildDistilledPattern(
        'CANDLE_BEHAVIOR',
        key,
        `${meta0.patternName} in ${key.split(':')[2]} phase (${meta0.sentiment})`,
        signals.map(s => ({
          timestamp: new Date(s.createdAt),
          outcome: 'PENDING' as MarketOutcome, // Would need to evaluate
          priceChangePct: 0,
          metadata: JSON.parse(s.metadata || '{}'),
        }))
      );

      // Store or update in feedback metrics
      const existing = await db.feedbackMetrics.findFirst({
        where: { sourceType: 'DISTILLED_PATTERN', sourceId: key },
      });

      if (existing) {
        await db.feedbackMetrics.update({
          where: { id: existing.id },
          data: {
            metricValue: pattern.totalObservations,
            context: JSON.stringify(pattern),
          },
        });
        updatedPatterns++;
      } else {
        await db.feedbackMetrics.create({
          data: {
            sourceType: 'DISTILLED_PATTERN',
            sourceId: key,
            metricName: key,
            metricValue: pattern.totalObservations,
            context: JSON.stringify(pattern),
          },
        });
        newPatterns++;
      }

      patternsCompressed++;
    }

    return { patternsCompressed, rawRecordsProcessed, newPatterns, updatedPatterns };
  }

  /**
   * Compress trader behavior observations into archetype profiles
   */
  private async compressTraderBehavior(): Promise<{
    patternsCompressed: number; rawRecordsProcessed: number; newPatterns: number; updatedPatterns: number;
  }> {
    let patternsCompressed = 0;
    let rawRecordsProcessed = 0;
    let newPatterns = 0;
    let updatedPatterns = 0;

    // Get behavioral model data
    const behaviorModels = await db.traderBehaviorModel.findMany();
    rawRecordsProcessed += behaviorModels.length;

    // Group by archetype + phase
    const groups = new Map<string, typeof behaviorModels>();
    for (const model of behaviorModels) {
      const key = `TRADER:${model.archetype}:${model.tokenPhase}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(model);
    }

    // Compress each archetype + phase combination
    for (const [key, models] of groups) {
      if (models.length < 1) continue;

      const archetype = models[0].archetype;
      const phase = models[0].tokenPhase;

      // Calculate aggregate behavior
      const actionCounts: Record<string, number> = {};
      const totalObservations = models.reduce((s, m) => s + (m.observations || 0), 0);

      for (const model of models) {
        const action = model.action;
        actionCounts[action] = (actionCounts[action] || 0) + (model.observations || 0);
      }

      const pattern = this.buildDistilledPattern(
        'TRADER_BEHAVIOR',
        key,
        `${archetype} traders in ${phase} phase`,
        models.map(m => ({
          timestamp: new Date(),
          outcome: m.action as MarketOutcome,
          priceChangePct: 0,
          metadata: { action: m.action, observations: m.observations, probability: m.probability },
        }))
      );

      const existing = await db.feedbackMetrics.findFirst({
        where: { sourceType: 'DISTILLED_PATTERN', sourceId: key },
      });

      if (existing) {
        await db.feedbackMetrics.update({
          where: { id: existing.id },
          data: {
            metricValue: pattern.totalObservations,
            context: JSON.stringify(pattern),
          },
        });
        updatedPatterns++;
      } else {
        await db.feedbackMetrics.create({
          data: {
            sourceType: 'DISTILLED_PATTERN',
            sourceId: key,
            metricName: key,
            metricValue: pattern.totalObservations,
            context: JSON.stringify(pattern),
          },
        });
        newPatterns++;
      }

      patternsCompressed++;
    }

    return { patternsCompressed, rawRecordsProcessed, newPatterns, updatedPatterns };
  }

  /**
   * Compress cross-correlation observations into conditional probability tables
   */
  private async compressCorrelations(): Promise<{
    patternsCompressed: number; rawRecordsProcessed: number; newPatterns: number; updatedPatterns: number;
  }> {
    let patternsCompressed = 0;
    let rawRecordsProcessed = 0;
    let newPatterns = 0;
    let updatedPatterns = 0;

    // Get all cross-correlation outcomes
    const outcomes = await db.feedbackMetrics.findMany({
      where: { sourceType: 'CROSS_CORRELATION' },
      orderBy: { measuredAt: 'desc' as const },
      take: 10000,
    });

    rawRecordsProcessed += outcomes.length;

    // Group by condition key
    const groups = new Map<string, typeof outcomes>();
    for (const outcome of outcomes) {
      if (!groups.has(outcome.metricName)) groups.set(outcome.metricName, []);
      groups.get(outcome.metricName)!.push(outcome);
    }

    // Compress each group
    for (const [key, groupOutcomes] of groups) {
      if (groupOutcomes.length < 2) continue;

      const pattern = this.buildDistilledPattern(
        'STRATEGY_OUTCOME',
        key,
        `Cross-correlation: ${key.replace(/\|/g, ' + ')}`,
        groupOutcomes.map(o => ({
          timestamp: new Date(o.measuredAt),
          outcome: o.metricValue > 0 ? 'BULLISH' as MarketOutcome : o.metricValue < 0 ? 'BEARISH' as MarketOutcome : 'NEUTRAL' as MarketOutcome,
          priceChangePct: (() => { try { return JSON.parse(o.context || '{}').priceChangePct || 0; } catch { return 0; } })(),
          metadata: (() => { try { return JSON.parse(o.context || '{}'); } catch { return {}; } })(),
        }))
      );

      const existing = await db.feedbackMetrics.findFirst({
        where: { sourceType: 'DISTILLED_PATTERN', sourceId: key },
      });

      if (existing) {
        await db.feedbackMetrics.update({
          where: { id: existing.id },
          data: {
            metricValue: pattern.totalObservations,
            context: JSON.stringify(pattern),
          },
        });
        updatedPatterns++;
      } else {
        await db.feedbackMetrics.create({
          data: {
            sourceType: 'DISTILLED_PATTERN',
            sourceId: key,
            metricName: key,
            metricValue: pattern.totalObservations,
            context: JSON.stringify(pattern),
          },
        });
        newPatterns++;
      }

      patternsCompressed++;
    }

    return { patternsCompressed, rawRecordsProcessed, newPatterns, updatedPatterns };
  }

  /**
   * Apply temporal decay to all distilled patterns
   * Remove patterns that have decayed below relevance threshold
   */
  private async applyDecayAndCleanup(): Promise<{ discarded: number }> {
    let discarded = 0;

    const allPatterns = await db.feedbackMetrics.findMany({
      where: { sourceType: 'DISTILLED_PATTERN' },
    });

    for (const stored of allPatterns) {
      try {
        const pattern: DistilledPattern = JSON.parse(stored.context || '{}');

        // Check if pattern has decayed
        if (pattern.effectiveObservations < MIN_OBSERVATIONS_TO_KEEP) {
          await db.feedbackMetrics.delete({ where: { id: stored.id } });
          discarded++;
          continue;
        }

        // Check if pattern is too old
        const ageDays = (Date.now() - new Date(pattern.lastObserved).getTime()) / (1000 * 60 * 60 * 24);
        if (ageDays > 180 && pattern.totalObservations < 30) {
          await db.feedbackMetrics.delete({ where: { id: stored.id } });
          discarded++;
        }
      } catch {
        // Malformed pattern - discard it
        await db.feedbackMetrics.delete({ where: { id: stored.id } });
        discarded++;
      }
    }

    return { discarded };
  }

  /**
   * Enforce storage limits per category
   */
  private async enforceStorageLimits(): Promise<void> {
    const categories: DistilledPattern['category'][] = [
      'CANDLE_BEHAVIOR', 'TRADER_BEHAVIOR', 'PHASE_TRANSITION', 'STRATEGY_OUTCOME',
    ];

    for (const category of categories) {
      const count = await db.feedbackMetrics.count({
        where: {
          sourceType: 'DISTILLED_PATTERN',
          metricName: { startsWith: `${category}:` },
        },
      });

      if (count > MAX_PATTERNS_PER_CATEGORY) {
        // Delete oldest/lowest-value patterns
        const toDelete = count - MAX_PATTERNS_PER_CATEGORY;
        const oldest = await db.feedbackMetrics.findMany({
          where: {
            sourceType: 'DISTILLED_PATTERN',
            metricName: { startsWith: `${category}:` },
          },
          orderBy: { measuredAt: 'asc' },
          take: toDelete,
          select: { id: true },
        });

        for (const item of oldest) {
          await db.feedbackMetrics.delete({ where: { id: item.id } });
        }
      }
    }
  }

  // ============================================================
  // HELPER METHODS
  // ============================================================

  /**
   * Build a DistilledPattern from raw observations
   */
  private buildDistilledPattern(
    category: DistilledPattern['category'],
    key: string,
    description: string,
    observations: Array<{
      timestamp: Date;
      outcome: MarketOutcome | string;
      priceChangePct: number;
      metadata: Record<string, unknown>;
    }>
  ): DistilledPattern {
    const totalObservations = observations.length;

    // Calculate outcome breakdown
    const outcomeBreakdown: Record<string, number> = {};
    for (const obs of observations) {
      const outcome = String(obs.outcome);
      outcomeBreakdown[outcome] = (outcomeBreakdown[outcome] || 0) + 1;
    }

    // Calculate probability estimates with Laplace smoothing
    const alpha = 1;
    const k = Object.keys(outcomeBreakdown).length || 3;
    const smoothedTotal = totalObservations + alpha * k;
    const probabilityEstimates: Record<string, number> = {};
    for (const [outcome, count] of Object.entries(outcomeBreakdown)) {
      probabilityEstimates[outcome] = (count + alpha) / smoothedTotal;
    }

    // Calculate primary metric CI (first outcome)
    const primaryOutcome = Object.keys(outcomeBreakdown)[0] || 'BULLISH';
    const primaryCount = outcomeBreakdown[primaryOutcome] || 0;
    const ci = proportionConfidenceInterval(primaryCount, totalObservations);

    // Price change statistics
    const priceChanges = observations
      .map(o => o.priceChangePct)
      .filter(p => p !== 0);
    const avgPriceChangePct = priceChanges.length > 0
      ? priceChanges.reduce((s, v) => s + v, 0) / priceChanges.length : 0;
    const sortedChanges = [...priceChanges].sort((a, b) => a - b);
    const medianPriceChangePct = sortedChanges.length > 0
      ? sortedChanges[Math.floor(sortedChanges.length / 2)] : 0;

    // Temporal range
    const timestamps = observations.map(o => new Date(o.timestamp).getTime());
    const firstObserved = new Date(Math.min(...timestamps));
    const lastObserved = new Date(Math.max(...timestamps));

    // Sample sufficiency
    const sufficiency = assessSampleSufficiency(key, totalObservations);

    // Reliability
    const reliability: DistilledPattern['reliability'] =
      totalObservations >= 100 ? 'VERY_HIGH' :
      totalObservations >= 30 ? 'HIGH' :
      totalObservations >= 10 ? 'MODERATE' : 'LOW';

    return {
      id: `dp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      category,
      key,
      description,
      totalObservations,
      effectiveObservations: totalObservations, // Simplified; would use temporal decay
      outcomeBreakdown,
      probabilityEstimates,
      confidenceInterval: { lower: ci.lower, upper: ci.upper },
      sampleSufficiency: sufficiency,
      avgPriceChangePct,
      medianPriceChangePct,
      lastObserved,
      firstObserved,
      decayRate: 0.5, // Default decay rate
      reliability,
      metadata: { observations: observations.slice(0, 10) }, // Keep only last 10 for context
    };
  }

  /**
   * Get the current phase for a token
   */
  private async getTokenPhaseById(tokenId: string): Promise<string> {
    try {
      const token = await db.token.findUnique({ where: { id: tokenId }, select: { address: true } });
      if (!token) return 'UNKNOWN';
      const lastState = await db.tokenLifecycleState.findFirst({
        where: { tokenAddress: token.address },
        orderBy: { detectedAt: 'desc' },
      });
      return lastState?.phase || 'UNKNOWN';
    } catch {
      return 'UNKNOWN';
    }
  }

  /**
   * Get the current phase for a token by address
   */
  private async getTokenPhase(tokenAddress: string): Promise<string> {
    try {
      const lastState = await db.tokenLifecycleState.findFirst({
        where: { tokenAddress },
        orderBy: { detectedAt: 'desc' },
      });
      return lastState?.phase || 'UNKNOWN';
    } catch {
      return 'UNKNOWN';
    }
  }

  /**
   * Query distilled patterns by conditions
   */
  async queryPatterns(query: PatternQuery): Promise<DistilledPattern[]> {
    const where: Record<string, unknown> = {
      metricType: 'DISTILLED_PATTERN',
    };

    if (query.category) {
      where.metricName = { startsWith: `${query.category}:` };
    }
    if (query.minObservations) {
      where.metricValue = { gte: query.minObservations };
    }

    const stored = await db.feedbackMetrics.findMany({
      where,
      orderBy: { metricValue: 'desc' },
      take: 100,
    });

    const patterns: DistilledPattern[] = [];
    for (const s of stored) {
      try {
        const pattern: DistilledPattern = JSON.parse(s.context || '{}');

        // Apply filters that can't be done at DB level
        if (query.minReliability) {
          const reliabilityOrder = { LOW: 0, MODERATE: 1, HIGH: 2, VERY_HIGH: 3 };
          if ((reliabilityOrder[pattern.reliability] || 0) < (reliabilityOrder[query.minReliability] || 0)) {
            continue;
          }
        }

        if (query.traderArchetype && !pattern.key.includes(query.traderArchetype)) continue;
        if (query.tokenPhase && !pattern.key.includes(query.tokenPhase)) continue;
        if (query.candlePattern && !pattern.key.includes(query.candlePattern)) continue;

        patterns.push(pattern);
      } catch {
        // Skip malformed
      }
    }

    return patterns;
  }

  /**
   * Get compression statistics
   */
  async getStats(): Promise<{
    totalDistilledPatterns: number;
    byCategory: Record<string, number>;
    totalRawObservations: number;
    compressionRatio: number;
    estimatedStorageSavedMB: number;
  }> {
    const allPatterns = await db.feedbackMetrics.findMany({
      where: { sourceType: 'DISTILLED_PATTERN' },
    });

    const byCategory: Record<string, number> = {};
    let totalObservations = 0;

    for (const p of allPatterns) {
      try {
        const pattern: DistilledPattern = JSON.parse(p.context || '{}');
        byCategory[pattern.category] = (byCategory[pattern.category] || 0) + 1;
        totalObservations += pattern.totalObservations;
      } catch {
        // Skip malformed
      }
    }

    return {
      totalDistilledPatterns: allPatterns.length,
      byCategory,
      totalRawObservations: totalObservations,
      compressionRatio: totalObservations > 0 ? allPatterns.length / totalObservations : 0,
      estimatedStorageSavedMB: (totalObservations * 500 - allPatterns.length * 2000) / (1024 * 1024),
    };
  }
}

export const patternCompressionPipeline = new PatternCompressionPipeline();
