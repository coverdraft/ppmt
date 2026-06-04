/**
 * Cross-Correlation Engine - CryptoQuant Terminal
 * Motor de Correlación Cruzada: P(outcome | trader + pattern + phase)
 *
 * Este motor computa la probabilidad condicional de un resultado (bullish/bearish/neutral)
 * dada la combinación de:
 *   - Comportamiento del trader (archetype + action)
 *   - Patrón de vela detectado (candlestick pattern)
 *   - Fase del ciclo de vida del token (lifecycle phase)
 *
 * Usa inferencia Bayesiana con suavizado de Laplace para estimar:
 *   P(outcome | trader_behavior, pattern, phase)
 *
 * Los datos se almacenan en CrossCorrelationObservation y se actualizan
 * después de cada evaluación de resultado (Outcome Evaluation).
 *
 * Flujo:
 *   1. Observar: (trader_action, pattern, phase) → outcome
 *   2. Registrar: incrementar counts en la tabla de correlación
 *   3. Predecir: P(outcome | evidence) usando Bayes con Laplace smoothing
 *   4. Evaluar: comparar predicción con resultado real
 *   5. Actualizar: ajustar probabilidades según resultado
 */

import { db } from '../db';
import { type PatternScanResult, type PatternDirection } from './candlestick-pattern-engine';
import { type BehavioralPrediction } from './behavioral-model-engine';
import { TokenPhase, type TraderArchetype } from './token-lifecycle-engine';
import { TraderAction } from './behavioral-model-engine';
import { type TokenAnalysis } from './brain-orchestrator';

// Import tokenLifecycleEngine lazily
let _tokenLifecycleEngine: typeof import('./token-lifecycle-engine').tokenLifecycleEngine | null = null;
async function getTokenLifecycleEngine() {
  if (!_tokenLifecycleEngine) {
    const mod = await import('./token-lifecycle-engine');
    _tokenLifecycleEngine = mod.tokenLifecycleEngine;
  }
  return _tokenLifecycleEngine;
}

// ============================================================
// TYPES
// ============================================================

export type Outcome = 'BULLISH' | 'BEARISH' | 'NEUTRAL';
export type MarketOutcome = 'BULLISH' | 'BEARISH' | 'NEUTRAL';

export interface ConditionKey {
  traderArchetype: string;
  traderAction: string;
  candlePattern: string;
  tokenPhase: string;
}

export interface CorrelationInput {
  tokenAddress: string;
  chain: string;
  phase: TokenPhase;
  dominantArchetype: TraderArchetype;
  dominantAction: TraderAction;
  dominantPattern: string | null;
  patternDirection: PatternDirection | null;
  behaviorDirection: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  patternSignal: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
}

export interface CorrelationPrediction {
  tokenAddress: string;
  outcome: Outcome;
  probability: number; // 0-1
  confidence: number; // 0-1
  breakdown: {
    phasePrior: Record<Outcome, number>;
    patternLikelihood: Record<Outcome, number>;
    behaviorLikelihood: Record<Outcome, number>;
  };
  evidence: string[];
  observationsUsed: number;
}

export interface CrossCorrelationResult {
  tokenAddress: string;
  chain: string;
  prediction: CorrelationPrediction;
  allPredictions: Record<Outcome, number>;
  dominantFactors: string[];
  conflictDetected: boolean;
  conflictDescription: string | null;
  timestamp: Date;
}

/** Observation record for storage */
interface CorrelationObservation {
  phase: TokenPhase;
  archetype: TraderArchetype;
  action: TraderAction;
  pattern: string;
  outcome: Outcome;
  count: number;
}

// ============================================================
// PRIOR PROBABILITIES
// ============================================================

/** Prior P(outcome | phase) - base rates per lifecycle phase */
const PHASE_PRIORS: Record<TokenPhase, Record<Outcome, number>> = {
  GENESIS:   { BULLISH: 0.30, BEARISH: 0.40, NEUTRAL: 0.30 },
  INCIPIENT: { BULLISH: 0.40, BEARISH: 0.25, NEUTRAL: 0.35 },
  GROWTH:    { BULLISH: 0.50, BEARISH: 0.20, NEUTRAL: 0.30 },
  FOMO:      { BULLISH: 0.35, BEARISH: 0.40, NEUTRAL: 0.25 },
  DECLINE:   { BULLISH: 0.15, BEARISH: 0.60, NEUTRAL: 0.25 },
  LEGACY:    { BULLISH: 0.25, BEARISH: 0.30, NEUTRAL: 0.45 },
};

/** Likelihood P(pattern_signal | outcome, phase) - how patterns predict outcomes */
const PATTERN_LIKELIHOODS: Record<PatternDirection, Record<Outcome, number>> = {
  BULLISH: { BULLISH: 0.60, BEARISH: 0.15, NEUTRAL: 0.25 },
  BEARISH: { BULLISH: 0.15, BEARISH: 0.60, NEUTRAL: 0.25 },
  NEUTRAL: { BULLISH: 0.30, BEARISH: 0.30, NEUTRAL: 0.40 },
};

/** Likelihood P(behavior_direction | outcome, archetype) */
const BEHAVIOR_LIKELIHOODS: Record<string, Record<Outcome, number>> = {
  BULLISH: { BULLISH: 0.55, BEARISH: 0.15, NEUTRAL: 0.30 },
  BEARISH: { BULLISH: 0.15, BEARISH: 0.55, NEUTRAL: 0.30 },
  NEUTRAL: { BULLISH: 0.30, BEARISH: 0.30, NEUTRAL: 0.40 },
};

/** Laplace smoothing alpha */
const ALPHA = 1;
const OUTCOMES: Outcome[] = ['BULLISH', 'BEARISH', 'NEUTRAL'];
const K = OUTCOMES.length;

// ============================================================
// ENGINE CLASS
// ============================================================

class CrossCorrelationEngine {
  private observationCache: Map<string, CorrelationObservation[]> = new Map();
  private cacheLoaded = false;

  /**
   * Predict the outcome for a token using cross-correlation:
   * P(outcome | trader_behavior, pattern, phase)
   *
   * Uses Bayes theorem:
   * P(outcome | evidence) ∝ P(outcome | phase) × P(pattern | outcome) × P(behavior | outcome)
   *
   * With Laplace smoothing for rare combinations.
   */
  async predict(
    input: CorrelationInput,
  ): Promise<CrossCorrelationResult> {
    await this.ensureCacheLoaded();

    const evidence: string[] = [];
    const dominantFactors: string[] = [];

    // Step 1: Get prior P(outcome | phase)
    const phasePrior = PHASE_PRIORS[input.phase] ?? PHASE_PRIORS.INCIPIENT;
    evidence.push(`Phase prior (${input.phase}): Bull=${(phasePrior.BULLISH * 100).toFixed(0)}% Bear=${(phasePrior.BEARISH * 100).toFixed(0)}%`);

    // Step 2: Get pattern likelihood P(pattern_signal | outcome)
    let patternLikelihood: Record<Outcome, number>;
    if (input.patternSignal !== 'NEUTRAL' && input.dominantPattern) {
      patternLikelihood = PATTERN_LIKELIHOODS[input.patternSignal] ?? PATTERN_LIKELIHOODS.NEUTRAL;
      dominantFactors.push(`Pattern: ${input.dominantPattern} (${input.patternSignal})`);
      evidence.push(`Pattern signal: ${input.patternSignal} from ${input.dominantPattern}`);
    } else {
      patternLikelihood = PATTERN_LIKELIHOODS.NEUTRAL;
      evidence.push('No significant candlestick pattern signal');
    }

    // Step 3: Get behavior likelihood P(behavior | outcome)
    const behaviorLikelihood = BEHAVIOR_LIKELIHOODS[input.behaviorDirection] ?? BEHAVIOR_LIKELIHOODS.NEUTRAL;
    if (input.behaviorDirection !== 'NEUTRAL') {
      dominantFactors.push(`Behavior: ${input.dominantArchetype} ${input.dominantAction} (${input.behaviorDirection})`);
      evidence.push(`Trader behavior: ${input.behaviorDirection} (${input.dominantArchetype} ${input.dominantAction})`);
    }

    // Step 4: Load observed correlations for this specific combination
    const observedCounts = this.getObservedCounts(input);

    // Step 5: Compute posterior using Bayes with Laplace smoothing
    const unnormalized: Record<Outcome, number> = { BULLISH: 0, BEARISH: 0, NEUTRAL: 0 };

    for (const outcome of OUTCOMES) {
      // P(outcome | phase) × P(pattern | outcome) × P(behavior | outcome) × P(observed | outcome)
      const prior = phasePrior[outcome];
      const patternL = patternLikelihood[outcome];
      const behaviorL = behaviorLikelihood[outcome];

      // Observed counts with Laplace smoothing
      const observedCount = observedCounts[outcome] ?? 0;
      const totalObserved = OUTCOMES.reduce((s, o) => s + (observedCounts[o] ?? 0), 0);
      const observedL = (observedCount + ALPHA) / (totalObserved + ALPHA * K);

      unnormalized[outcome] = prior * patternL * behaviorL * observedL;
    }

    // Normalize
    const total = OUTCOMES.reduce((s, o) => s + unnormalized[o], 0);
    const predictions: Record<Outcome, number> = { BULLISH: 0, BEARISH: 0, NEUTRAL: 0 };
    for (const outcome of OUTCOMES) {
      predictions[outcome] = total > 0 ? unnormalized[outcome] / total : 1 / K;
    }

    // Determine best prediction
    let bestOutcome: Outcome = 'NEUTRAL';
    let bestProb = 0;
    for (const outcome of OUTCOMES) {
      if (predictions[outcome] > bestProb) {
        bestProb = predictions[outcome];
        bestOutcome = outcome;
      }
    }

    // Calculate confidence: how far is the best from uniform?
    const uniform = 1 / K;
    const confidence = Math.min(1, (bestProb - uniform) / (1 - uniform));

    // Detect conflicts (pattern says X but behavior says Y)
    let conflictDetected = false;
    let conflictDescription: string | null = null;
    if (input.patternSignal !== 'NEUTRAL' && input.behaviorDirection !== 'NEUTRAL'
        && input.patternSignal !== input.behaviorDirection) {
      conflictDetected = true;
      conflictDescription = `Conflict: patterns signal ${input.patternSignal} but traders behave ${input.behaviorDirection}`;
      evidence.push(conflictDescription);
    }

    // Total observations used
    const totalObservations = OUTCOMES.reduce((s, o) => s + (observedCounts[o] ?? 0), 0);

    const prediction: CorrelationPrediction = {
      tokenAddress: input.tokenAddress,
      outcome: bestOutcome,
      probability: bestProb,
      confidence,
      breakdown: {
        phasePrior,
        patternLikelihood,
        behaviorLikelihood,
      },
      evidence,
      observationsUsed: totalObservations,
    };

    return {
      tokenAddress: input.tokenAddress,
      chain: input.chain,
      prediction,
      allPredictions: predictions,
      dominantFactors,
      conflictDetected,
      conflictDescription,
      timestamp: new Date(),
    };
  }

  /**
   * Record an observation: (trader, pattern, phase) → outcome
   * Called after outcome evaluation to update the correlation model.
   */
  async recordObservation(
    input: CorrelationInput,
    actualOutcome: Outcome,
  ): Promise<void> {
    const key = this.makeKey(input);

    try {
      // Try to update existing record
      const existing = await db.feedbackMetrics.findFirst({
        where: {
          sourceType: 'cross_correlation',
          metricName: key,
        },
        orderBy: { measuredAt: 'desc' },
      });

      const context = JSON.stringify({
        phase: input.phase,
        archetype: input.dominantArchetype,
        action: input.dominantAction,
        pattern: input.dominantPattern,
        patternSignal: input.patternSignal,
        behaviorDirection: input.behaviorDirection,
        outcome: actualOutcome,
      });

      if (existing) {
        // Parse current counts and increment
        const currentContext = JSON.parse(existing.context || '{}');
        const counts: Record<Outcome, number> = currentContext.counts ?? { BULLISH: 0, BEARISH: 0, NEUTRAL: 0 };
        counts[actualOutcome] = (counts[actualOutcome] || 0) + 1;

        await db.feedbackMetrics.update({
          where: { id: existing.id },
          data: {
            metricValue: existing.metricValue + 1,
            context: JSON.stringify({ ...currentContext, counts }),
            measuredAt: new Date(),
          },
        });
      } else {
        const counts: Record<Outcome, number> = { BULLISH: 0, BEARISH: 0, NEUTRAL: 0 };
        counts[actualOutcome] = 1;

        await db.feedbackMetrics.create({
          data: {
            sourceType: 'cross_correlation',
            sourceId: input.tokenAddress,
            metricName: key,
            metricValue: 1,
            context: JSON.stringify({
              phase: input.phase,
              archetype: input.dominantArchetype,
              action: input.dominantAction,
              pattern: input.dominantPattern,
              patternSignal: input.patternSignal,
              behaviorDirection: input.behaviorDirection,
              counts,
            }),
            period: 'lifetime',
            measuredAt: new Date(),
          },
        });
      }

      // Invalidate cache
      this.observationCache.delete(key);
      this.cacheLoaded = false;
    } catch (error) {
      console.warn('[CrossCorrelation] Failed to record observation:', error instanceof Error ? error.message : String(error));
    }
  }

  /**
   * Batch update: record multiple observations from outcome evaluation.
   */
  async batchRecordObservations(
    observations: Array<{ input: CorrelationInput; outcome: Outcome }>,
  ): Promise<void> {
    for (const obs of observations) {
      await this.recordObservation(obs.input, obs.outcome);
    }
  }

  /**
   * Build CorrelationInput from existing analysis data.
   */
  buildInput(
    brainAnalysis: TokenAnalysis,
    patternScan: PatternScanResult | undefined,
    behavioralPrediction: BehavioralPrediction | undefined,
  ): CorrelationInput {
    return {
      tokenAddress: brainAnalysis.tokenAddress,
      chain: brainAnalysis.chain,
      phase: brainAnalysis.lifecyclePhase as TokenPhase,
      dominantArchetype: (behavioralPrediction?.archetypeBreakdown[0]?.archetype ?? 'RETAIL_FOMO') as TraderArchetype,
      dominantAction: (behavioralPrediction?.archetypeBreakdown[0]?.dominantAction ?? 'HOLD') as TraderAction,
      dominantPattern: patternScan?.dominantPattern ?? null,
      patternDirection: patternScan?.patterns[0]?.direction ?? null,
      patternSignal: patternScan?.overallSignal ?? 'NEUTRAL',
      behaviorDirection: behavioralPrediction?.netFlowDirection ?? 'NEUTRAL',
    };
  }

  // ============================================================
  // PRIVATE HELPERS
  // ============================================================

  private makeKey(input: CorrelationInput): string {
    return `${input.phase}:${input.dominantArchetype}:${input.dominantAction}:${input.dominantPattern || 'none'}:${input.patternSignal}:${input.behaviorDirection}`;
  }

  private getObservedCounts(input: CorrelationInput): Record<Outcome, number> {
    const key = this.makeKey(input);
    const observations = this.observationCache.get(key);
    if (!observations || observations.length === 0) {
      return { BULLISH: 0, BEARISH: 0, NEUTRAL: 0 };
    }
    const counts: Record<Outcome, number> = { BULLISH: 0, BEARISH: 0, NEUTRAL: 0 };
    for (const obs of observations) {
      counts[obs.outcome] = (counts[obs.outcome] || 0) + obs.count;
    }
    return counts;
  }

  private async ensureCacheLoaded(): Promise<void> {
    if (this.cacheLoaded) return;

    try {
      const records = await db.feedbackMetrics.findMany({
        where: { sourceType: 'cross_correlation' },
      });

      this.observationCache.clear();
      for (const record of records) {
        try {
          const context = JSON.parse(record.context || '{}');
          const counts: Record<Outcome, number> = context.counts ?? { BULLISH: 0, BEARISH: 0, NEUTRAL: 0 };

          const observations: CorrelationObservation[] = [];
          for (const outcome of OUTCOMES) {
            if (counts[outcome] > 0) {
              observations.push({
                phase: context.phase as TokenPhase,
                archetype: context.archetype as TraderArchetype,
                action: context.action as TraderAction,
                pattern: context.pattern || 'none',
                outcome,
                count: counts[outcome],
              });
            }
          }

          if (observations.length > 0) {
            this.observationCache.set(record.metricName, observations);
          }
        } catch {
          // Skip malformed records
        }
      }

      this.cacheLoaded = true;
    } catch {
      // If DB fails, proceed with empty cache
      this.cacheLoaded = true;
    }
  }

  /**
   * High-level cross-correlation analysis for a token.
   * Builds input from token data and runs prediction.
   */
  async analyzeCrossCorrelation(
    tokenAddress: string,
    chain: string = 'SOL',
  ): Promise<CrossCorrelationResult & {
    overallAssessment?: { direction: string; confidence: number; strength: number; recommendation: string };
    bestStrategy?: { strategy: string; expectedWinRate: number; sampleSize: number };
    conditionalProbabilities?: Array<{ condition: string; outcome: string; probability: number; totalObservations: number; validation: { isValid: boolean } }>;
  }> {
    try {
      // Get lifecycle phase
      let phase: TokenPhase = 'INCIPIENT';
      try {
        const tle = await getTokenLifecycleEngine();
        const phaseResult = await tle.detectPhase(tokenAddress, chain);
        phase = phaseResult.phase;
      } catch { /* use default */ }

      // Build a minimal input using defaults
      const input: CorrelationInput = {
        tokenAddress,
        chain,
        phase,
        dominantArchetype: 'RETAIL_FOMO',
        dominantAction: 'HOLD' as TraderAction,
        dominantPattern: null,
        patternDirection: null,
        patternSignal: 'NEUTRAL',
        behaviorDirection: 'NEUTRAL',
      };

      const result = await this.predict(input);

      // Enrich with assessment and strategy info
      const bestOutcome = result.prediction.outcome;
      const bestProb = result.prediction.probability;
      const confidence = result.prediction.confidence;

      return {
        ...result,
        overallAssessment: {
          direction: bestOutcome,
          confidence,
          strength: bestProb - 1 / K,
          recommendation: bestOutcome === 'BULLISH' ? 'LONG' : bestOutcome === 'BEARISH' ? 'SHORT' : 'HOLD',
        },
        bestStrategy: {
          strategy: bestOutcome === 'BULLISH' ? 'LONG' : bestOutcome === 'BEARISH' ? 'SHORT' : 'HOLD',
          expectedWinRate: bestProb,
          sampleSize: result.prediction.observationsUsed,
        },
        conditionalProbabilities: Object.entries(result.allPredictions).map(([outcome, prob]) => ({
          condition: `${phase}:RETAIL_FOMO:HOLD:none:NEUTRAL:NEUTRAL`,
          outcome,
          probability: prob,
          totalObservations: result.prediction.observationsUsed,
          validation: { isValid: result.prediction.observationsUsed >= 30 },
        })),
      };
    } catch (error) {
      // Return a default result on error
      return {
        tokenAddress,
        chain,
        prediction: {
          tokenAddress,
          outcome: 'NEUTRAL' as Outcome,
          probability: 1 / K,
          confidence: 0,
          breakdown: {
            phasePrior: { BULLISH: 1/3, BEARISH: 1/3, NEUTRAL: 1/3 },
            patternLikelihood: { BULLISH: 1/3, BEARISH: 1/3, NEUTRAL: 1/3 },
            behaviorLikelihood: { BULLISH: 1/3, BEARISH: 1/3, NEUTRAL: 1/3 },
          },
          evidence: ['Insufficient data for cross-correlation analysis'],
          observationsUsed: 0,
        },
        allPredictions: { BULLISH: 1/3, BEARISH: 1/3, NEUTRAL: 1/3 },
        dominantFactors: [],
        conflictDetected: false,
        conflictDescription: null,
        timestamp: new Date(),
        overallAssessment: {
          direction: 'NEUTRAL',
          confidence: 0,
          strength: 0,
          recommendation: 'NO_DATA',
        },
        bestStrategy: {
          strategy: 'HOLD',
          expectedWinRate: 0.33,
          sampleSize: 0,
        },
        conditionalProbabilities: [],
      };
    }
  }

  /**
   * Get statistics about the cross-correlation data.
   */
  async getCorrelationStats(): Promise<{
    totalCombinations: number;
    totalObservations: number;
    reliableCombinations: number;
  }> {
    try {
      const records = await db.feedbackMetrics.findMany({
        where: { sourceType: 'cross_correlation' },
      });

      let totalObservations = 0;
      let reliableCombinations = 0;

      for (const record of records) {
        try {
          const context = JSON.parse(record.context || '{}');
          const counts: Record<string, number> = context.counts ?? {};
          const total = Object.values(counts).reduce((s: number, v) => s + (v as number), 0);
          totalObservations += total;
          if (total >= 30) reliableCombinations++;
        } catch { /* skip malformed */ }
      }

      return {
        totalCombinations: records.length,
        totalObservations,
        reliableCombinations,
      };
    } catch {
      return { totalCombinations: 0, totalObservations: 0, reliableCombinations: 0 };
    }
  }

  /**
   * Evaluate pending cross-correlation observations whose time window has expired.
   * Compares predicted outcomes with actual price movements and updates the model.
   * Returns the number of observations evaluated.
   */
  async evaluatePendingObservations(): Promise<number> {
    try {
      // Find pending observations that have passed their evaluation window
      const pendingSignals = await db.predictiveSignal.findMany({
        where: {
          signalType: 'PIPELINE_ANALYSIS',
          wasCorrect: null,
          validUntil: { lt: new Date() },
        },
        take: 50,
        orderBy: { createdAt: 'asc' },
      });

      let evaluated = 0;
      for (const signal of pendingSignals) {
        try {
          // Get the current price to compare with prediction
          const token = await db.token.findFirst({
            where: { address: signal.tokenAddress ?? undefined },
          });

          if (!token) continue;

          // Determine if prediction was correct
          try {
            const pred = JSON.parse(signal.prediction) as Record<string, unknown>;
            const direction = pred.direction as string;
            const wasCorrect = (direction === 'LONG' && token.priceChange24h > 0)
              || (direction === 'SHORT' && token.priceChange24h < 0)
              || (direction === 'HOLD' && Math.abs(token.priceChange24h ?? 0) < 5)
              || (direction === 'WAIT');

            await db.predictiveSignal.update({
              where: { id: signal.id },
              data: { wasCorrect },
            });
            evaluated++;
          } catch { /* skip malformed prediction */ }
        } catch { /* skip signal */ }
      }

      return evaluated;
    } catch {
      return 0;
    }
  }

  /**
   * Record an observation with simplified parameters for the pipeline.
   */
  async recordObservationPipeline(
    tokenAddress: string,
    chain: string,
    conditions: ConditionKey,
    currentPrice: number,
  ): Promise<string | null> {
    try {
      const input: CorrelationInput = {
        tokenAddress,
        chain,
        phase: conditions.tokenPhase as TokenPhase,
        dominantArchetype: conditions.traderArchetype as TraderArchetype,
        dominantAction: conditions.traderAction as TraderAction,
        dominantPattern: conditions.candlePattern === 'NO_PATTERN' ? null : conditions.candlePattern,
        patternDirection: null,
        patternSignal: 'NEUTRAL',
        behaviorDirection: 'NEUTRAL',
      };

      // Record with NEUTRAL outcome as placeholder (will be evaluated later)
      await this.recordObservation(input, 'NEUTRAL');
      return `obs_${Date.now()}`;
    } catch {
      return null;
    }
  }
}

export const crossCorrelationEngine = new CrossCorrelationEngine();
