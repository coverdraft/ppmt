/**
 * Decision Engine - CryptoQuant Terminal
 *
 * Core engine that produces, stores, and evaluates trading decisions
 * for tokens. Integrates with the token lifecycle engine, behavioral
 * models, and operability scores to produce actionable decisions.
 *
 * Capabilities:
 *   1. Decide — Analyze a token and produce a decision (OPERATE / SKIP / WATCH / EXIT / ADJUST)
 *   2. Recent — Retrieve the latest decisions from the DecisionLog
 *   3. Track Record — Aggregate performance statistics over a time window
 *   4. Feedback — Close the learning loop by recording outcomes on past decisions
 */

import { db } from '../db';
import { toNum } from '../utils';
import { tokenLifecycleEngine, TokenPhase } from './token-lifecycle-engine';

// ============================================================
// TYPES & INTERFACES
// ============================================================

export type DecisionType = 'OPERATE' | 'SKIP' | 'WATCH' | 'EXIT' | 'ADJUST';
export type RecommendedSystem =
  | 'ALPHA_HUNTER'
  | 'SMART_MONEY'
  | 'TECHNICAL'
  | 'DEFENSIVE'
  | 'BOT_AWARE'
  | 'ADAPTIVE';
export type Outcome = 'PROFIT' | 'LOSS' | 'BREAKEVEN' | 'MISSED' | 'AVOIDED_LOSS';

/** Result returned by the decide() method */
export interface DecisionResult {
  id: string;
  tokenAddress: string;
  chain: string;
  tokenSymbol: string | null;
  decisionType: DecisionType;
  recommendedSystem: RecommendedSystem | null;
  confidence: number;
  dataQualityScore: number;
  reasoning: DecisionReasoning;
  tokenPhaseAtDecision: TokenPhase | null;
  regimeAtDecision: string | null;
  operabilityAtDecision: number | null;
}

/** Structured reasoning behind a decision */
export interface DecisionReasoning {
  lifecycle: { phase: TokenPhase; probability: number };
  operability: { score: number; tradeable: boolean };
  riskAssessment: { level: string; factors: string[] };
  systemRecommendation: { system: RecommendedSystem | null; rationale: string };
}

/** Feedback payload accepted by provideFeedback() */
export interface DecisionFeedback {
  wasActedUpon: boolean;
  outcome: Outcome;
  realizedPnlPct?: number;
  realizedPnlUsd?: number;
  holdTimeMin?: number;
  maxFavorable?: number;
  maxAdverse?: number;
  notes?: string;
}

/** Track record summary over a time window */
export interface TrackRecord {
  totalDecisions: number;
  actedUpon: number;
  outcomes: Record<Outcome, number>;
  avgRealizedPnlPct: number;
  avgConfidence: number;
  correctDecisions: number;
  accuracy: number;
  byDecisionType: Record<string, { count: number; actedUpon: number; correct: number }>;
  period: { from: Date; to: Date; days: number };
}

// ============================================================
// DECISION MAPPING TABLES
// ============================================================

/**
 * Map token lifecycle phase → recommended decision type.
 *
 * GENESIS   → SKIP   (too early, high bot/rug risk)
 * INCIPIENT → WATCH  (monitoring, smart money may enter)
 * GROWTH    → OPERATE (ideal conditions, SM active)
 * FOMO      → ADJUST (cautious, SM distributing)
 * DECLINE   → EXIT   (losing momentum, SM leaving)
 * LEGACY    → OPERATE (stable, liquid, lower risk)
 */
const PHASE_DECISION_MAP: Record<TokenPhase, DecisionType> = {
  GENESIS: 'SKIP',
  INCIPIENT: 'WATCH',
  GROWTH: 'OPERATE',
  FOMO: 'ADJUST',
  DECLINE: 'EXIT',
  LEGACY: 'OPERATE',
};

/**
 * Map token lifecycle phase → recommended trading system.
 *
 * GENESIS   → BOT_AWARE   (bot-heavy environment)
 * INCIPIENT → ALPHA_HUNTER (early opportunity)
 * GROWTH    → SMART_MONEY  (follow smart money flow)
 * FOMO      → ADAPTIVE     (volatile, need flexibility)
 * DECLINE   → DEFENSIVE    (protect capital)
 * LEGACY    → TECHNICAL    (mature markets, TA works)
 */
const PHASE_SYSTEM_MAP: Record<TokenPhase, RecommendedSystem> = {
  GENESIS: 'BOT_AWARE',
  INCIPIENT: 'ALPHA_HUNTER',
  GROWTH: 'SMART_MONEY',
  FOMO: 'ADAPTIVE',
  DECLINE: 'DEFENSIVE',
  LEGACY: 'TECHNICAL',
};

/**
 * Risk level thresholds based on operability score.
 */
const OPERABILITY_THRESHOLDS = {
  HIGH_RISK: 0.3,
  MEDIUM_RISK: 0.6,
  LOW_RISK: 0.8,
};

// ============================================================
// DECISION ENGINE CLASS
// ============================================================

class DecisionEngine {
  // ============================================================
  // 1. DECIDE — Produce a new decision for a token
  // ============================================================

  /**
   * Analyzes a token and produces a structured decision.
   *
   * Process:
   *   1. Detect token lifecycle phase
   *   2. Fetch operability score
   *   3. Compute risk assessment
   *   4. Determine recommended system
   *   5. Calculate overall confidence
   *   6. Persist the decision in DecisionLog
   *
   * @param tokenAddress - Token address on-chain
   * @param chain - Blockchain (default: "SOL")
   * @returns Decision result with reasoning and metadata
   */
  async decide(tokenAddress: string, chain: string = 'SOL'): Promise<DecisionResult> {
    // Step 1: Detect lifecycle phase
    const phaseResult = await tokenLifecycleEngine.detectPhase(tokenAddress, chain);
    const phase = phaseResult.phase;

    // Step 2: Fetch operability score
    const operabilityScore = await this.getOperabilityScore(tokenAddress, chain);
    const tradeable = operabilityScore >= OPERABILITY_THRESHOLDS.MEDIUM_RISK;

    // Step 3: Determine decision type
    let decisionType: DecisionType = PHASE_DECISION_MAP[phase];

    // Override: If operability is very low, skip regardless of phase
    if (operabilityScore < OPERABILITY_THRESHOLDS.HIGH_RISK) {
      decisionType = 'SKIP';
    }

    // Step 4: Determine recommended system
    let recommendedSystem: RecommendedSystem | null = PHASE_SYSTEM_MAP[phase];

    // If decision is SKIP, no system needed
    if (decisionType === 'SKIP') {
      recommendedSystem = null;
    }

    // Step 5: Build risk assessment
    const riskFactors = this.assessRisk(phase, phaseResult.probability, operabilityScore);
    const riskLevel = this.classifyRisk(operabilityScore, phaseResult.probability);

    // Step 6: Calculate confidence
    const confidence = this.calculateConfidence(phaseResult.probability, operabilityScore);

    // Step 7: Calculate data quality score
    const dataQualityScore = this.assessDataQuality(phaseResult, operabilityScore);

    // Step 8: Detect regime (simplified — based on phase and volatility)
    const regime = this.inferRegime(phase, phaseResult.signals.volatilityScore);

    // Step 9: Build reasoning
    const reasoning: DecisionReasoning = {
      lifecycle: { phase, probability: phaseResult.probability },
      operability: { score: operabilityScore, tradeable },
      riskAssessment: { level: riskLevel, factors: riskFactors },
      systemRecommendation: {
        system: recommendedSystem,
        rationale: recommendedSystem
          ? `${recommendedSystem} recommended for ${phase} phase tokens`
          : 'No system recommended — token should be skipped',
      },
    };

    // Step 10: Look up token symbol
    const token = await db.token.findFirst({
      where: { address: tokenAddress, chain },
      select: { symbol: true },
    });

    // Step 11: Persist the decision
    const decisionLog = await db.decisionLog.create({
      data: {
        tokenAddress,
        chain,
        tokenSymbol: token?.symbol ?? null,
        decisionType,
        decision: recommendedSystem ? 'BUY' : 'HOLD',
        recommendedSystem,
        reasoning: JSON.stringify(reasoning),
        confidence,
        dataQualityScore,
        tokenPhaseAtDecision: phase,
        regimeAtDecision: regime,
        smartMoneySignal: JSON.stringify({
          smartMoneyFlowScore: phaseResult.signals.smartMoneyFlowScore,
          whaleSignal: { holderVelocityScore: phaseResult.signals.holderVelocityScore },
          botActivitySignal: { botRatioScore: phaseResult.signals.botRatioScore },
        }),
        operabilityAtDecision: operabilityScore,
        decidedAt: new Date(),
      },
    });

    return {
      id: decisionLog.id,
      tokenAddress,
      chain,
      tokenSymbol: token?.symbol ?? null,
      decisionType,
      recommendedSystem,
      confidence,
      dataQualityScore,
      reasoning,
      tokenPhaseAtDecision: phase,
      regimeAtDecision: regime,
      operabilityAtDecision: operabilityScore,
    };
  }

  // ============================================================
  // 2. GET RECENT DECISIONS
  // ============================================================

  /**
   * Returns the most recent decisions from the DecisionLog.
   *
   * @param limit - Number of decisions to return (default: 20)
   * @returns Array of recent decision records
   */
  async getRecentDecisions(limit: number = 20): Promise<DecisionResult[]> {
    const decisions = await db.decisionLog.findMany({
      orderBy: { decidedAt: 'desc' },
      take: limit,
    });

    return decisions.map((d) => ({
      id: d.id,
      tokenAddress: d.tokenAddress ?? '',
      chain: d.chain ?? '',
      tokenSymbol: d.tokenSymbol ?? '',
      decisionType: d.decisionType as DecisionType,
      recommendedSystem: d.recommendedSystem as RecommendedSystem | null,
      confidence: d.confidence,
      dataQualityScore: d.dataQualityScore,
      reasoning: this.parseReasoning(d.reasoning),
      tokenPhaseAtDecision: d.tokenPhaseAtDecision as TokenPhase | null,
      regimeAtDecision: d.regimeAtDecision,
      operabilityAtDecision: d.operabilityAtDecision,
    }));
  }

  // ============================================================
  // 3. GET TRACK RECORD
  // ============================================================

  /**
   * Aggregates performance statistics for decisions within a time window.
   *
   * @param days - Number of days to look back (default: 30)
   * @returns Track record summary with outcomes and accuracy
   */
  async getTrackRecord(days: number = 30): Promise<TrackRecord> {
    const from = new Date();
    from.setDate(from.getDate() - days);

    const decisions = await db.decisionLog.findMany({
      where: {
        decidedAt: { gte: from },
        outcome: { not: null },
      },
    });

    const totalDecisions = decisions.length;
    const actedUpon = decisions.filter((d) => d.wasActedUpon === true).length;

    // Count outcomes
    const outcomes: Record<Outcome, number> = {
      PROFIT: 0,
      LOSS: 0,
      BREAKEVEN: 0,
      MISSED: 0,
      AVOIDED_LOSS: 0,
    };
    for (const d of decisions) {
      if (d.outcome && d.outcome in outcomes) {
        outcomes[d.outcome as Outcome]++;
      }
    }

    // Average realized PnL %
    const pnlDecisions = decisions.filter((d) => d.realizedPnlPct !== null);
    const avgRealizedPnlPct =
      pnlDecisions.length > 0
        ? pnlDecisions.reduce((s, d) => s + (d.realizedPnlPct ?? 0), 0) / pnlDecisions.length
        : 0;

    // Average confidence
    const avgConfidence =
      totalDecisions > 0
        ? decisions.reduce((s, d) => s + d.confidence, 0) / totalDecisions
        : 0;

    // Correct decisions: PROFIT, AVOIDED_LOSS, BREAKEVEN (acted upon)
    const correctDecisions = decisions.filter(
      (d) =>
        d.decisionWasCorrect === true ||
        (d.outcome === 'PROFIT' || d.outcome === 'AVOIDED_LOSS')
    ).length;

    const accuracy = totalDecisions > 0 ? correctDecisions / totalDecisions : 0;

    // Breakdown by decision type
    const byDecisionType: TrackRecord['byDecisionType'] = {};
    for (const d of decisions) {
      if (!byDecisionType[d.decisionType]) {
        byDecisionType[d.decisionType] = { count: 0, actedUpon: 0, correct: 0 };
      }
      byDecisionType[d.decisionType].count++;
      if (d.wasActedUpon) byDecisionType[d.decisionType].actedUpon++;
      if (d.outcome === 'PROFIT' || d.outcome === 'AVOIDED_LOSS') {
        byDecisionType[d.decisionType].correct++;
      }
    }

    return {
      totalDecisions,
      actedUpon,
      outcomes,
      avgRealizedPnlPct: Math.round(avgRealizedPnlPct * 100) / 100,
      avgConfidence: Math.round(avgConfidence * 100) / 100,
      correctDecisions,
      accuracy: Math.round(accuracy * 1000) / 1000,
      byDecisionType,
      period: { from, to: new Date(), days },
    };
  }

  // ============================================================
  // 4. PROVIDE FEEDBACK
  // ============================================================

  /**
   * Records feedback on a past decision, closing the learning loop.
   *
   * Updates the DecisionLog with:
   *   - Whether the decision was acted upon
   *   - The actual outcome
   *   - Realized PnL and excursion metrics
   *   - Whether the decision was correct in hindsight
   *   - Optional notes
   *
   * @param decisionId - ID of the DecisionLog entry
   * @param feedback - Feedback payload
   */
  async provideFeedback(decisionId: string, feedback: DecisionFeedback): Promise<void> {
    // Determine if the decision was correct in hindsight
    const decisionWasCorrect = this.evaluateCorrectness(
      feedback.wasActedUpon,
      feedback.outcome
    );

    const now = new Date();

    await db.decisionLog.update({
      where: { id: decisionId },
      data: {
        wasActedUpon: feedback.wasActedUpon,
        outcome: feedback.outcome,
        realizedPnlPct: feedback.realizedPnlPct ?? null,
        realizedPnlUsd: feedback.realizedPnlUsd ?? 0,
        decisionWasCorrect,
        reasoning: JSON.stringify({
          notes: feedback.notes ?? '',
          holdTimeMin: feedback.holdTimeMin ?? null,
          maxFavorable: feedback.maxFavorable ?? null,
          maxAdverse: feedback.maxAdverse ?? null,
          recordedAt: now.toISOString(),
        }),
      },
    });
  }

  // ============================================================
  // PRIVATE HELPERS
  // ============================================================

  /**
   * Fetch the operability score for a token from the OperabilitySnapshot table.
   * Falls back to a computed estimate if no snapshot exists.
   */
  private async getOperabilityScore(tokenAddress: string, chain: string): Promise<number> {
    const snapshot = await db.operabilitySnapshot.findFirst({
      where: { tokenAddress, chain },
      orderBy: { createdAt: 'desc' },
    });

    if (snapshot) {
      return snapshot.overallScore;
    }

    // Fallback: compute from token data
    const token = await db.token.findFirst({
      where: { address: tokenAddress, chain },
    });

    if (!token) return 0;

    // Simple heuristic: liquidity + holders + smart money
    const liquidityScore = Math.min(1, Math.log10(toNum(token.liquidity) + 1) / 6);
    const holderScore = Math.min(1, token.holderCount / 500);
    const smScore = token.smartMoneyPct / 100;

    return Math.round((liquidityScore * 0.4 + holderScore * 0.3 + smScore * 0.3) * 100) / 100;
  }

  /**
   * Assess risk factors for a token based on phase and signals.
   */
  private assessRisk(phase: TokenPhase, phaseProbability: number, operability: number): string[] {
    const factors: string[] = [];

    if (phase === 'GENESIS' || phase === 'INCIPIENT') {
      factors.push('Token in early lifecycle — high rug/abandonment risk');
    }
    if (phase === 'FOMO') {
      factors.push('FOMO phase — smart money likely distributing');
    }
    if (phase === 'DECLINE') {
      factors.push('Decline phase — liquidity draining');
    }
    if (phaseProbability < 0.4) {
      factors.push('Low phase detection confidence — uncertain classification');
    }
    if (operability < OPERABILITY_THRESHOLDS.HIGH_RISK) {
      factors.push('Very low operability — likely untradeable');
    }
    if (operability < OPERABILITY_THRESHOLDS.MEDIUM_RISK) {
      factors.push('Below-medium operability — proceed with caution');
    }

    return factors.length > 0 ? factors : ['No significant risk factors identified'];
  }

  /**
   * Classify the overall risk level.
   */
  private classifyRisk(operability: number, phaseProbability: number): string {
    if (operability < OPERABILITY_THRESHOLDS.HIGH_RISK || phaseProbability < 0.3) {
      return 'HIGH';
    }
    if (operability < OPERABILITY_THRESHOLDS.MEDIUM_RISK || phaseProbability < 0.5) {
      return 'MEDIUM';
    }
    if (operability < OPERABILITY_THRESHOLDS.LOW_RISK) {
      return 'LOW-MEDIUM';
    }
    return 'LOW';
  }

  /**
   * Calculate decision confidence as a weighted combination of
   * phase detection confidence and operability score.
   */
  private calculateConfidence(phaseProbability: number, operability: number): number {
    // Weight: phase confidence 60%, operability 40%
    const raw = phaseProbability * 0.6 + operability * 0.4;
    return Math.round(raw * 100) / 100;
  }

  /**
   * Assess data quality based on available signals and their reliability.
   */
  private assessDataQuality(
    phaseResult: { probability: number; signals: Record<string, number> },
    operability: number
  ): number {
    // Start with phase probability as base
    let quality = phaseResult.probability * 0.5 + operability * 0.5;

    // Penalize if signals are at default/fallback values
    const signals = phaseResult.signals;
    const zeroSignals = Object.values(signals).filter((v) => v === 0).length;
    if (zeroSignals > 4) {
      quality *= 0.5; // Many missing signals → low quality
    } else if (zeroSignals > 2) {
      quality *= 0.75;
    }

    return Math.round(Math.min(1, quality) * 100) / 100;
  }

  /**
   * Infer market regime from phase and volatility.
   */
  private inferRegime(phase: TokenPhase, volatilityScore: number): string {
    if (phase === 'GENESIS' || phase === 'INCIPIENT') {
      return volatilityScore > 0.7 ? 'VOLATILE' : 'SIDEWAYS';
    }
    if (phase === 'GROWTH') {
      return 'BULL';
    }
    if (phase === 'FOMO') {
      return volatilityScore > 0.6 ? 'VOLATILE' : 'BULL';
    }
    if (phase === 'DECLINE') {
      return 'BEAR';
    }
    // LEGACY
    return volatilityScore > 0.4 ? 'VOLATILE' : 'SIDEWAYS';
  }

  /**
   * Determine whether a decision was correct in hindsight based on
   * whether it was acted upon and the actual outcome.
   */
  private evaluateCorrectness(wasActedUpon: boolean, outcome: Outcome): boolean {
    if (wasActedUpon) {
      // Acted upon: correct if profit or breakeven
      return outcome === 'PROFIT' || outcome === 'BREAKEVEN';
    } else {
      // Not acted upon: correct if missed profit (wrong skip) is NOT the case,
      // or if we avoided a loss
      return outcome === 'AVOIDED_LOSS';
    }
  }

  /**
   * Safely parse the reasoning JSON from a DecisionLog record.
   */
  private parseReasoning(reasoningJson: string): DecisionReasoning {
    try {
      return JSON.parse(reasoningJson) as DecisionReasoning;
    } catch {
      return {
        lifecycle: { phase: 'LEGACY', probability: 0 },
        operability: { score: 0, tradeable: false },
        riskAssessment: { level: 'UNKNOWN', factors: [] },
        systemRecommendation: { system: null, rationale: 'Could not parse reasoning' },
      };
    }
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const decisionEngine = new DecisionEngine();
