/**
 * Strategy Decision Engine (SDE) — CryptoQuant Terminal
 *
 * EL MOTOR CENTRAL. Toma datos de Backtest, Monte Carlo, Walk-Forward,
 * Operability y Paper Trading, y produce una decisión accionable:
 *
 *   State:  ACTIVE | CONDITIONAL | PAUSED | REJECTED
 *   Action: INCREASE | MAINTAIN | REDUCE | EXIT
 *   Quality: STRONG | ADEQUATE | WEAK
 *
 * Pipeline:
 *   STEP 1: Vetos duros (cualquier fallo = REJECTED + EXIT)
 *   STEP 2: Composite scores (Robustness, Overfitting, Stability)
 *   STEP 3: Signal Quality (STRONG / ADEQUATE / WEAK)
 *   STEP 4: State + Capital Action mapping
 *   STEP 5: Capital Recommendation (método + tamaño)
 *   STEP 6: Audit record (siempre se genera)
 *
 * Principios:
 *   - Vetos antes que scores (un veto SIEMPRE gana a un score alto)
 *   - Decisión > Información (el output es accionable, no descriptivo)
 *   - Portfolio > Estrategia (concentration limits son hard constraints)
 *   - Conservador por defecto (ante incertidumbre, reducir exposición)
 *   - Reproducibilidad obligatoria (mismos inputs = mismo output)
 *   - Transparencia radical (cada decisión tiene su audit completo)
 */

import { db } from '../../db';
import {
  capitalAllocationEngine,
  type AllocationInput,
  type AllocationMethod,
} from '../risk/capital-allocation';
import { strategyCorrelationService } from '../risk/strategy-correlation-service';

// ============================================================
// TYPES
// ============================================================

/** 4 estados posibles de una estrategia */
export type StrategyState = 'ACTIVE' | 'CONDITIONAL' | 'PAUSED' | 'REJECTED';

/** 4 acciones posibles sobre el capital */
export type CapitalAction = 'INCREASE' | 'MAINTAIN' | 'REDUCE' | 'EXIT';

/** 3 niveles de calidad de señal */
export type SignalQuality = 'STRONG' | 'ADEQUATE' | 'WEAK';

/** Perfil de riesgo del usuario */
export type RiskProfile = 'CONSERVATIVE' | 'MODERATE' | 'AGGRESSIVE';

/** Los 5 métodos de allocation activos en v1 */
export type ActiveAllocationMethod =
  | 'KELLY_MODIFIED'
  | 'RISK_PARITY'
  | 'VOLATILITY_TARGETING'
  | 'MAX_DRAWDOWN_CONTROL'
  | 'EQUAL_WEIGHT';

/** Resultado de un veto individual */
export interface VetoResult {
  veto: string;
  passed: boolean;
  value: number;
  threshold: number;
  reason: string;
}

/** Scores compuestos calculados por el SDE */
export interface CompositeScores {
  robustness: number;   // 0-100: WFE × 0.35 + probOfProfit × 0.30 + paramStability × 0.35
  overfitting: number;  // 0-100: degradation × 0.45 + WFE_variance × 0.30 + tradeCountPenalty × 0.25
  stability: number;    // 0-100: paramStability × 0.40 + OOS_winRate_std × 0.30 + regimeConsistency × 0.30
}

/** Pesos usados en el cálculo (para audit) */
export interface ScoreWeights {
  robustness: { wfe: number; probOfProfit: number; paramStability: number };
  overfitting: { degradation: number; wfeVariance: number; tradeCountPenalty: number };
  stability: { paramStability: number; oosWinRateStd: number; regimeConsistency: number };
}

/** Recomendación de capital */
export interface CapitalRecommendation {
  targetPct: number;           // % del portfolio a asignar
  sizeUsd: number;             // tamaño en USD
  method: AllocationMethod;    // método de allocation usado
  reason: string;              // por qué este método
}

/** Decisión completa del SDE */
export interface StrategyDecision {
  strategyId: string;
  strategyName: string;
  timestamp: Date;

  // Las 3 dimensiones de la decisión
  state: StrategyState;
  capitalAction: CapitalAction;
  signalQuality: SignalQuality;

  // Detalle del pipeline
  vetoResults: VetoResult[];
  scores: CompositeScores;
  weightsUsed: ScoreWeights;

  // Recomendación de capital
  capitalRecommendation: CapitalRecommendation;

  // Sugerencias adicionales
  recommendations: string[];
  nextReviewDate: Date;

  // Referencia al audit
  auditId: string;
}

// ============================================================
// INPUT SNAPSHOTS (para audit)
// ============================================================

export interface BacktestSnapshot {
  totalTrades: number;
  winRate: number;
  avgWinPct: number;
  avgLossPct: number;
  maxDrawdownPct: number;
  sharpeRatio: number;
  sortinoRatio: number;
  profitFactor: number;
  expectancy: number;
  overfittingScore: number;
  parameterStability: number;
  recoveryFactor: number;
  payoffRatio: number;  // avgWin / avgLoss
}

export interface MonteCarloSnapshot {
  riskOfRuin: number;
  probabilityOfProfit: number;
  p95MaxDrawdown: number;
  meanFinalEquity: number;
  medianFinalEquity: number;
  stdDevFinalEquity: number;
  simulationsCount: number;
  ruinThreshold: number;
}

export interface WalkForwardSnapshot {
  aggregateWFE: number;
  isRobust: boolean;
  recommendation: string;
  parameterStability: number;
  overallDegradation: number;
  performanceConsistency: number;
  windowCount: number;
}

export interface OperabilitySnapshot {
  overallScore: number;
  level: string;
  isOperable: boolean;
  recommendedPositionUsd: number;
  minimumGainPct: number;
  feeEstimateTotalCostPct: number;
}

export interface PaperTradingSnapshot {
  totalTrades: number;
  winRate: number;
  unrealizedPnlPct: number;
  currentDrawdownPct: number;
  daysActive: number;
  sharpeRatio: number;
}

export interface SDEInput {
  strategyId: string;
  strategyName: string;

  backtest: BacktestSnapshot;
  monteCarlo: MonteCarloSnapshot;
  walkForward: WalkForwardSnapshot;
  operability: OperabilitySnapshot;
  paperTrading?: PaperTradingSnapshot;

  // Data quality flag: 'INSUFFICIENT' when MC/WF data is fabricated (no real simulation)
  dataQuality?: 'REAL' | 'PLACEHOLDER' | 'INSUFFICIENT';

  // Portfolio context
  portfolioState: {
    totalCapitalUsd: number;
    currentDrawdownPct: number;
    activeStrategies: number;
    marketVolatility: number;       // 0-100 percentile
    marketRegime: string;
  };

  // Regime assessment from RegimeHeuristic
  regimeAssessment?: {
    regime: string;
    confidence: number;
    volatilityPercentile: number;
    trendDirection: string;
    trendStrength: number;
  };

  riskProfile?: RiskProfile;
}

// ============================================================
// AUDIT RECORD (se persiste en DB)
// ============================================================

export interface DecisionAuditRecord {
  id: string;
  strategyId: string;
  timestamp: Date;

  inputs: {
    backtest: BacktestSnapshot;
    monteCarlo: MonteCarloSnapshot;
    walkForward: WalkForwardSnapshot;
    operability: OperabilitySnapshot;
    paperTrading?: PaperTradingSnapshot;
    portfolioState: SDEInput['portfolioState'];
    riskProfile: RiskProfile;
  };

  processing: {
    vetoResults: VetoResult[];
    scores: CompositeScores;
    weightsUsed: ScoreWeights;
    signalQuality: SignalQuality;
  };

  decision: StrategyDecision;

  configSnapshot: {
    vetoThresholds: Record<string, number>;
    scoreWeights: ScoreWeights;
    riskProfile: RiskProfile;
    sdeVersion: string;
  };

  feedback?: {
    wasCorrect: boolean;
    realizedPnlPct: number;
    daysUntilReevaluation: number;
    feedbackDate: Date;
  };
}

// ============================================================
// CONFIG — Pesos fijos v1 + Umbrales de veto
// ============================================================

const SDE_VERSION = '1.0.0';

const DEFAULT_WEIGHTS: ScoreWeights = {
  robustness: { wfe: 0.35, probOfProfit: 0.30, paramStability: 0.35 },
  overfitting: { degradation: 0.45, wfeVariance: 0.30, tradeCountPenalty: 0.25 },
  stability: { paramStability: 0.40, oosWinRateStd: 0.30, regimeConsistency: 0.30 },
};

const VETO_THRESHOLDS_BY_PROFILE: Record<RiskProfile, {
  minTrades: number;
  maxRiskOfRuin: number;
  maxDrawdownPct: number;
  minWFE: number;
  minWinRate: number;
  maxPayoffRatioForWinRateVeto: number;
}> = {
  CONSERVATIVE: {
    minTrades: 50,
    maxRiskOfRuin: 0.01,   // 1%
    maxDrawdownPct: 30,    // Conservative: lower DD tolerance
    minWFE: 0.40,          // Higher WFE bar
    minWinRate: 0.40,      // Higher win rate bar
    maxPayoffRatioForWinRateVeto: 2.5,
  },
  MODERATE: {
    minTrades: 50,
    maxRiskOfRuin: 0.03,   // 3%
    maxDrawdownPct: 40,    // Moderate: moderate DD tolerance
    minWFE: 0.30,
    minWinRate: 0.35,
    maxPayoffRatioForWinRateVeto: 2.5,
  },
  AGGRESSIVE: {
    minTrades: 50,
    maxRiskOfRuin: 0.05,   // 5%
    maxDrawdownPct: 50,    // Aggressive: higher DD tolerance
    minWFE: 0.25,          // Lower WFE bar
    minWinRate: 0.30,      // Lower win rate bar
    maxPayoffRatioForWinRateVeto: 2.5,
  },
};

const SIGNAL_QUALITY_THRESHOLDS = {
  STRONG:    { robustness: 75, overfitting: 25, stability: 70 },
  ADEQUATE:  { robustness: 50, overfitting: 40, stability: 45 },
};

// ============================================================
// SDE CLASS
// ============================================================

class StrategyDecisionEngine {

  // ============================================================
  // PUBLIC: VALIDATE — Pipeline completo
  // ============================================================

  async validate(input: SDEInput, skipAudit: boolean = false): Promise<StrategyDecision> {
    const profile: RiskProfile = input.riskProfile || 'MODERATE';

    // Input validation — fail early with clear errors
    if (!input.backtest || !input.monteCarlo || !input.walkForward || !input.operability) {
      throw new Error('SDE validate: missing required input (backtest, monteCarlo, walkForward, operability)');
    }
    if (!input.portfolioState || typeof input.portfolioState.totalCapitalUsd !== 'number') {
      throw new Error('SDE validate: invalid portfolioState');
    }

    const thresholds = VETO_THRESHOLDS_BY_PROFILE[profile];
    const weights = DEFAULT_WEIGHTS;

    // STEP 1: Vetos duros
    const vetoResults = this.runVetos(input, thresholds);

    // STEP 2: Composite scores (siempre se calculan, incluso con vetos fallidos)
    const scores = this.calculateScores(input, weights);

    // STEP 3: Signal Quality
    const signalQuality = this.determineSignalQuality(scores);

    // STEP 4: State + Capital Action
    const { state, capitalAction } = this.determineStateAndAction(
      vetoResults,
      signalQuality,
      scores,
      input,
    );

    // STEP 5: Capital Recommendation
    const capitalRecommendation = await this.calculateCapitalRecommendation(
      state,
      capitalAction,
      input,
    );

    // STEP 6: Recommendations
    const recommendations = this.generateRecommendations(
      state, capitalAction, signalQuality, scores, vetoResults, input,
    );

    // Next review date based on signal quality
    const nextReviewDate = this.calculateNextReviewDate(signalQuality, state);

    // Build decision
    const decision: StrategyDecision = {
      strategyId: input.strategyId,
      strategyName: input.strategyName,
      timestamp: new Date(),
      state,
      capitalAction,
      signalQuality,
      vetoResults,
      scores,
      weightsUsed: weights,
      capitalRecommendation,
      recommendations,
      nextReviewDate,
      auditId: '', // Will be set after persisting
    };

    // STEP 7: Persist audit record (skip if caller requests, e.g. dashboard auto-refresh)
    let auditId = '';
    if (!skipAudit) {
      auditId = await this.persistAudit(input, decision, profile, thresholds, weights);
    } else {
      auditId = `audit_skipped_${Date.now()}_${input.strategyId.slice(0, 8)}`;
    }
    decision.auditId = auditId;

    return decision;
  }

  // ============================================================
  // STEP 1: VETOS DUROS
  // ============================================================

  private runVetos(
    input: SDEInput,
    thresholds: typeof VETO_THRESHOLDS_BY_PROFILE.MODERATE,
  ): VetoResult[] {
    const bt = input.backtest;
    const mc = input.monteCarlo;
    const wf = input.walkForward;
    const payoffRatio = bt.payoffRatio;

    return [
      {
        veto: 'MIN_TRADES',
        passed: bt.totalTrades >= thresholds.minTrades,
        value: bt.totalTrades,
        threshold: thresholds.minTrades,
        reason: bt.totalTrades < thresholds.minTrades
          ? `Insufficient trades: ${bt.totalTrades} < ${thresholds.minTrades}`
          : `Trades OK: ${bt.totalTrades} >= ${thresholds.minTrades}`,
      },
      {
        veto: 'MAX_RISK_OF_RUIN',
        passed: mc.riskOfRuin <= thresholds.maxRiskOfRuin,
        value: mc.riskOfRuin,
        threshold: thresholds.maxRiskOfRuin,
        reason: mc.riskOfRuin > thresholds.maxRiskOfRuin
          ? `Risk of ruin too high: ${(mc.riskOfRuin * 100).toFixed(1)}% > ${(thresholds.maxRiskOfRuin * 100).toFixed(1)}%`
          : `Risk of ruin OK: ${(mc.riskOfRuin * 100).toFixed(1)}% <= ${(thresholds.maxRiskOfRuin * 100).toFixed(1)}%`,
      },
      {
        veto: 'MAX_DRAWDOWN',
        passed: bt.maxDrawdownPct <= thresholds.maxDrawdownPct,
        value: bt.maxDrawdownPct,
        threshold: thresholds.maxDrawdownPct,
        reason: bt.maxDrawdownPct > thresholds.maxDrawdownPct
          ? `Max drawdown too high: ${bt.maxDrawdownPct.toFixed(1)}% > ${thresholds.maxDrawdownPct}%`
          : `Max drawdown OK: ${bt.maxDrawdownPct.toFixed(1)}% <= ${thresholds.maxDrawdownPct}%`,
      },
      {
        veto: 'MIN_WFE',
        passed: wf.aggregateWFE >= thresholds.minWFE,
        value: wf.aggregateWFE,
        threshold: thresholds.minWFE,
        reason: wf.aggregateWFE < thresholds.minWFE
          ? `Walk-Forward Efficiency too low: ${(wf.aggregateWFE * 100).toFixed(1)}% < ${(thresholds.minWFE * 100).toFixed(1)}%`
          : `WFE OK: ${(wf.aggregateWFE * 100).toFixed(1)}% >= ${(thresholds.minWFE * 100).toFixed(1)}%`,
      },
      {
        veto: 'MIN_WIN_RATE_WITH_LOW_PAYOFF',
        passed: !(bt.winRate < thresholds.minWinRate && payoffRatio < thresholds.maxPayoffRatioForWinRateVeto),
        value: bt.winRate,
        threshold: thresholds.minWinRate,
        reason: (bt.winRate < thresholds.minWinRate && payoffRatio < thresholds.maxPayoffRatioForWinRateVeto)
          ? `Win rate ${(bt.winRate * 100).toFixed(1)}% < ${(thresholds.minWinRate * 100).toFixed(0)}% AND payoff ratio ${payoffRatio.toFixed(2)} < ${thresholds.maxPayoffRatioForWinRateVeto}`
          : `Win rate/payoff OK: WR ${(bt.winRate * 100).toFixed(1)}%, PR ${payoffRatio.toFixed(2)}`,
      },
    ];
  }

  // ============================================================
  // STEP 2: COMPOSITE SCORES
  // ============================================================

  private calculateScores(input: SDEInput, weights: ScoreWeights): CompositeScores {
    const bt = input.backtest;
    const mc = input.monteCarlo;
    const wf = input.walkForward;

    // --- Robustness: WFE × 0.35 + probOfProfit × 0.30 + paramStability × 0.35 ---
    const wfeNorm = Math.min(1, Math.max(0, wf.aggregateWFE));
    const probOfProfitNorm = Math.min(1, Math.max(0, mc.probabilityOfProfit));
    const paramStabilityNorm = Math.min(1, Math.max(0, wf.parameterStability));

    const robustness =
      wfeNorm * weights.robustness.wfe * 100 +
      probOfProfitNorm * weights.robustness.probOfProfit * 100 +
      paramStabilityNorm * weights.robustness.paramStability * 100;

    // --- Overfitting: degradation × 0.45 + WFE_variance × 0.30 + tradeCountPenalty × 0.25 ---
    // Higher overfitting score = MORE overfitting = WORSE
    const degradationNorm = Math.min(1, Math.max(0, wf.overallDegradation)); // 0-1
    // WFE variance: use performanceConsistency inverse (low consistency = high variance)
    const perfConsistencyClamped = Math.min(1, Math.max(0, wf.performanceConsistency));
    const wfeVarianceNorm = 1 - perfConsistencyClamped; // invert: 0 = consistent, 1 = variable
    // Trade count penalty: fewer trades = more likely overfitting
    const tradeCountPenaltyNorm = bt.totalTrades >= 200 ? 0
      : bt.totalTrades >= 100 ? 0.2
      : bt.totalTrades >= 50 ? 0.5
      : 0.8;

    const overfitting =
      degradationNorm * weights.overfitting.degradation * 100 +
      wfeVarianceNorm * weights.overfitting.wfeVariance * 100 +
      tradeCountPenaltyNorm * weights.overfitting.tradeCountPenalty * 100;

    // --- Stability: paramStability × 0.40 + OOS_winRate_std × 0.30 + regimeConsistency × 0.30 ---
    const paramStabilityStabNorm = Math.min(1, Math.max(0, wf.parameterStability));
    // OOS win rate std: use 1 - (backtest overfitting score) as proxy
    // Lower overfittingScore = more stable OOS performance
    const oosWinRateStdNorm = 1 - Math.min(1, bt.overfittingScore);
    // Regime consistency: use regime assessment if available, otherwise WF recommendation as proxy
    let regimeConsistencyNorm: number;
    if (input.regimeAssessment) {
      const confidenceClamped = Math.min(1, Math.max(0, input.regimeAssessment.confidence));
      // If regime is SIDEWAYS, strategy should be robust in sideways (consistency high)
      // If regime is TRENDING, strategy should adapt (consistency moderate)
      regimeConsistencyNorm = confidenceClamped * (
        input.regimeAssessment.regime === 'SIDEWAYS' ? 0.85 :
        input.regimeAssessment.regime === 'TRENDING_UP' ? 0.70 :
        input.regimeAssessment.regime === 'TRENDING_DOWN' ? 0.70 :
        input.regimeAssessment.regime === 'HIGH_VOLATILITY' ? 0.40 :
        0.60  // LOW_VOLATILITY
      );
    } else {
      // Fallback: use WF recommendation as proxy
      regimeConsistencyNorm = wf.recommendation === 'ROBUST' ? 0.9
        : wf.recommendation === 'MARGINAL' ? 0.5
        : 0.2;
    }

    const stability =
      paramStabilityStabNorm * weights.stability.paramStability * 100 +
      oosWinRateStdNorm * weights.stability.oosWinRateStd * 100 +
      regimeConsistencyNorm * weights.stability.regimeConsistency * 100;

    return {
      robustness: Math.round(robustness * 100) / 100,
      overfitting: Math.round(overfitting * 100) / 100,
      stability: Math.round(stability * 100) / 100,
    };
  }

  // ============================================================
  // STEP 3: SIGNAL QUALITY
  // ============================================================

  private determineSignalQuality(scores: CompositeScores): SignalQuality {
    const { robustness, overfitting, stability } = scores;
    const th = SIGNAL_QUALITY_THRESHOLDS;

    if (robustness >= th.STRONG.robustness && overfitting <= th.STRONG.overfitting && stability >= th.STRONG.stability) {
      return 'STRONG';
    }
    if (robustness >= th.ADEQUATE.robustness && overfitting <= th.ADEQUATE.overfitting && stability >= th.ADEQUATE.stability) {
      return 'ADEQUATE';
    }
    return 'WEAK';
  }

  // ============================================================
  // STEP 4: STATE + CAPITAL ACTION
  // ============================================================

  private determineStateAndAction(
    vetoResults: VetoResult[],
    signalQuality: SignalQuality,
    scores: CompositeScores,
    input: SDEInput,
  ): { state: StrategyState; capitalAction: CapitalAction } {
    // [INTEGRATION FIX] Hard veto for insufficient data — no real MC/WF simulation exists
    if (input.dataQuality === 'INSUFFICIENT') {
      return {
        state: 'REJECTED' as const,
        capitalAction: 'EXIT' as const,
      };
    }

    // If data quality is placeholder, downgrade: ACTIVE→CONDITIONAL, INCREASE→MAINTAIN
    // Placeholder MC/WF data should not support aggressive decisions
    if (input.dataQuality === 'PLACEHOLDER') {
      // Even if vetos pass, be conservative with placeholder data
      const anyVetoFailed = vetoResults.some(v => !v.passed);
      if (anyVetoFailed) {
        return { state: 'REJECTED', capitalAction: 'EXIT' };
      }
      // Downgrade: never go above CONDITIONAL with placeholder data
      return { state: 'CONDITIONAL', capitalAction: 'MAINTAIN' };
    }

    // If any veto failed → REJECTED + EXIT
    const anyVetoFailed = vetoResults.some(v => !v.passed);
    if (anyVetoFailed) {
      return { state: 'REJECTED', capitalAction: 'EXIT' };
    }

    // If has paper trading and drawdown > 15% → PAUSED + EXIT
    if (input.paperTrading && input.paperTrading.currentDrawdownPct > 15) {
      return { state: 'PAUSED', capitalAction: 'EXIT' };
    }

    // If has paper trading and drawdown > 10% → CONDITIONAL + REDUCE
    if (input.paperTrading && input.paperTrading.currentDrawdownPct > 10) {
      return { state: 'CONDITIONAL', capitalAction: 'REDUCE' };
    }

    // If robustness < 30 (but passed vetos) → CONDITIONAL + REDUCE
    if (scores.robustness < 30) {
      return { state: 'CONDITIONAL', capitalAction: 'REDUCE' };
    }

    // Signal quality based mapping
    switch (signalQuality) {
      case 'STRONG':
        return { state: 'ACTIVE', capitalAction: 'INCREASE' };
      case 'ADEQUATE':
        return { state: 'ACTIVE', capitalAction: 'MAINTAIN' };
      case 'WEAK':
        return { state: 'CONDITIONAL', capitalAction: 'MAINTAIN' };
      default:
        return { state: 'CONDITIONAL', capitalAction: 'REDUCE' };
    }
  }

  // ============================================================
  // STEP 5: CAPITAL RECOMMENDATION
  // ============================================================

  private async calculateCapitalRecommendation(
    state: StrategyState,
    capitalAction: CapitalAction,
    input: SDEInput,
  ): Promise<CapitalRecommendation> {
    const portfolio = input.portfolioState;
    const method = this.selectAllocationMethod(state, capitalAction, input);

    // If EXIT or the strategy is rejected/paused, target 0%
    if (state === 'REJECTED' || state === 'PAUSED' || capitalAction === 'EXIT') {
      return {
        targetPct: 0,
        sizeUsd: 0,
        method,
        reason: `${state} + ${capitalAction}: no capital allocation`,
      };
    }

    // Calculate position size using CapitalAllocationEngine
    const bt = input.backtest;
    const mc = input.monteCarlo;

    try {
      const allocationInput: AllocationInput = {
        capital: portfolio.totalCapitalUsd,
        currentPositions: [],  // Filled by paper trading in real usage
        signals: [{
          tokenAddress: input.strategyId,
          confidence: mc.probabilityOfProfit,
          direction: 'LONG' as const,
        }],
        historicalTrades: {
          winRate: bt.winRate,
          avgWin: bt.avgWinPct / 100,
          avgLoss: Math.abs(bt.avgLossPct) / 100,
          totalTrades: bt.totalTrades,
        },
        volatility: portfolio.marketVolatility / 100,
        currentDrawdown: portfolio.currentDrawdownPct / 100,
        maxDrawdown: 0.20, // Portfolio-level max DD target
        marketRegime: (portfolio.marketRegime || 'SIDEWAYS') as AllocationInput['marketRegime'],
        estimatedFeePct: input.operability.feeEstimateTotalCostPct / 100,
        estimatedSlippagePct: 0.005,
        minimumNetGainPct: input.operability.minimumGainPct / 100,
        expectedGainPct: bt.avgWinPct / 100,
      };

      const result = capitalAllocationEngine.calculate(method, allocationInput);

      // Get size for this strategy
      const position = result.positions.find(p => p.tokenAddress === input.strategyId);
      const sizeUsd = position?.sizeUsd ?? result.positions[0]?.sizeUsd ?? (portfolio.totalCapitalUsd * 0.05);
      let targetPct = (sizeUsd / portfolio.totalCapitalUsd) * 100;

      // Apply capital action modifiers
      if (capitalAction === 'INCREASE') {
        targetPct = Math.min(targetPct * 1.5, 15); // Cap at 15% of portfolio
      } else if (capitalAction === 'REDUCE') {
        targetPct = targetPct * 0.5; // Half exposure
      }

      // Concentration limit: max 15% per strategy (hard constraint)
      targetPct = Math.min(targetPct, 15);
      if (targetPct <= 0) {
        return {
          targetPct: 0,
          sizeUsd: 0,
          method,
          reason: `Capital allocation is 0 — no allocation possible`,
        };
      }

      // Correlation check: if adding this strategy would push avg correlation > limit
      let correlationNote = '';
      if (portfolio.activeStrategies >= 1) {
        try {
          const matrix = await strategyCorrelationService.getCurrentCorrelationMatrix();
          const corrCheck = strategyCorrelationService.wouldExceedCorrelationLimit(
            [], // existing strategy IDs — not available in SDEInput, but matrix has all
            input.strategyId,
            40, // from RiskBudget.maxCorrelatedPct
            matrix,
          );
          if (!corrCheck.allowed) {
            targetPct *= 0.5; // Halve allocation for highly correlated strategies
            correlationNote = ` [Correlation reduced: avg=${(corrCheck.avgCorrelation * 100).toFixed(1)}%, max=${(corrCheck.maxPairwise * 100).toFixed(1)}%]`;
          }
        } catch (correlationError) {
          // Correlation check failure should not block allocation
          console.warn('[SDE] Correlation check failed:', correlationError);
        }
      }

      return {
        targetPct: Math.round(targetPct * 100) / 100,
        sizeUsd: Math.round(portfolio.totalCapitalUsd * targetPct / 100 * 100) / 100,
        method,
        reason: this.explainMethodSelection(method, state, capitalAction, input) + correlationNote,
      };
    } catch (error) {
      // Fallback: 5% of portfolio
      const fallbackPct = capitalAction === 'REDUCE' ? 2.5 : 5;
      return {
        targetPct: fallbackPct,
        sizeUsd: portfolio.totalCapitalUsd * fallbackPct / 100,
        method: 'EQUAL_WEIGHT' as AllocationMethod,
        reason: `Fallback allocation (allocation engine error: ${error instanceof Error ? error.message : 'unknown'})`,
      };
    }
  }

  /**
   * Select allocation method based on state + portfolio context.
   * Implements the 5-method selection logic from Revisión 3.
   */
  private selectAllocationMethod(
    state: StrategyState,
    capitalAction: CapitalAction,
    input: SDEInput,
  ): AllocationMethod {
    const portfolio = input.portfolioState;

    // Emergency: drawdown > 10% → Max DD Control
    if (portfolio.currentDrawdownPct > 10 || capitalAction === 'REDUCE') {
      return 'MAX_DRAWDOWN_CONTROL';
    }

    // Correlation-aware: if avg pairwise correlation > 0.5 → RISK_PARITY
    // (equal risk contribution is more important when strategies are correlated)
    // This check runs synchronously using cached matrix; falls through if unavailable
    try {
      const cachedMatrix = (strategyCorrelationService as unknown as { cachedMatrix: { strategies: string[]; matrix: number[][]; computedAt: Date; dataPoints: number } | null })['cachedMatrix'];
      if (cachedMatrix && cachedMatrix.strategies.length >= 2) {
        const avgCorr = strategyCorrelationService.getAverageCorrelation(cachedMatrix);
        if (avgCorr > 0.5) {
          return 'RISK_PARITY';
        }
      }
    } catch {
      // If correlation check fails, proceed with normal method selection
    }

    // Regime-aware allocation: use regime assessment if available
    if (input.regimeAssessment?.regime === 'HIGH_VOLATILITY') {
      return 'VOLATILITY_TARGETING';
    }
    if (input.regimeAssessment?.regime === 'TRENDING_DOWN' && portfolio.currentDrawdownPct > 5) {
      return 'MAX_DRAWDOWN_CONTROL';
    }

    // High volatility: reduce exposure
    if (portfolio.marketVolatility > 75) {
      return 'VOLATILITY_TARGETING';
    }

    // Multi-strategy: diversify risk
    if (portfolio.activeStrategies >= 2) {
      return 'RISK_PARITY';
    }

    // Single strategy, good conditions: Kelly
    return 'KELLY_MODIFIED';
  }

  private explainMethodSelection(
    method: AllocationMethod,
    state: StrategyState,
    capitalAction: CapitalAction,
    input: SDEInput,
  ): string {
    const portfolio = input.portfolioState;
    const reasons: Record<string, string> = {
      MAX_DRAWDOWN_CONTROL: `Portfolio DD ${portfolio.currentDrawdownPct.toFixed(1)}% > 10% — scaling down to protect capital`,
      VOLATILITY_TARGETING: `Market volatility at ${portfolio.marketVolatility}th percentile — reducing exposure`,
      RISK_PARITY: `${portfolio.activeStrategies} active strategies — equalizing risk contribution`,
      KELLY_MODIFIED: `Single strategy with good conditions — optimal growth sizing (half-Kelly)`,
      EQUAL_WEIGHT: `Fallback: simple equal-weight allocation`,
    };
    return reasons[method] || `Using ${method} allocation`;
  }

  // ============================================================
  // STEP 6: RECOMMENDATIONS
  // ============================================================

  private generateRecommendations(
    state: StrategyState,
    capitalAction: CapitalAction,
    signalQuality: SignalQuality,
    scores: CompositeScores,
    vetoResults: VetoResult[],
    input: SDEInput,
  ): string[] {
    const recs: string[] = [];

    // Veto-based recommendations
    const failedVetos = vetoResults.filter(v => !v.passed);
    for (const v of failedVetos) {
      recs.push(`REJECT: ${v.reason}`);
    }

    // State-based recommendations
    if (state === 'PAUSED') {
      recs.push('Suggest RETRAIN with adjusted parameters before re-enabling');
    }
    if (state === 'CONDITIONAL' && signalQuality === 'WEAK') {
      recs.push('Re-evaluate within 24 hours — signals are weak');
    }
    if (state === 'ACTIVE' && signalQuality === 'STRONG') {
      recs.push('Next review in 7 days — strategy is performing well');
    }

    // Score-based recommendations
    if (scores.overfitting > 40 && state !== 'REJECTED') {
      recs.push('Overfitting risk elevated — consider simplifying parameters');
    }
    if (scores.stability < 45 && state !== 'REJECTED') {
      recs.push('Stability below threshold — monitor for regime changes');
    }

    // Paper trading recommendations
    if (input.paperTrading) {
      if (input.paperTrading.currentDrawdownPct > 5) {
        recs.push(`Paper trading DD at ${input.paperTrading.currentDrawdownPct.toFixed(1)}% — watch closely`);
      }
      if (input.paperTrading.totalTrades < 20) {
        recs.push(`Only ${input.paperTrading.totalTrades} paper trades — insufficient live track record`);
      }
    } else {
      recs.push('No paper trading data — recommend paper trading before capital allocation');
    }

    // Regime-based recommendations
    if (input.regimeAssessment?.regime === 'HIGH_VOLATILITY') {
      recs.push('High volatility regime detected — reduce position sizes and widen stops');
    }
    if (input.regimeAssessment?.regime === 'TRENDING_DOWN') {
      recs.push('Downtrend regime — consider SHORT strategies or defensive positioning');
    }

    return recs;
  }

  // ============================================================
  // NEXT REVIEW DATE
  // ============================================================

  private calculateNextReviewDate(signalQuality: SignalQuality, state: StrategyState): Date {
    const now = new Date();

    if (state === 'REJECTED') {
      // Rejected: review in 30 days (or when data changes significantly)
      return new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);
    }
    if (state === 'PAUSED') {
      // Paused: review in 48 hours
      return new Date(now.getTime() + 48 * 60 * 60 * 1000);
    }
    if (state === 'CONDITIONAL') {
      // Conditional: review in 24 hours
      return new Date(now.getTime() + 24 * 60 * 60 * 1000);
    }

    // ACTIVE
    switch (signalQuality) {
      case 'STRONG':
        return new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000); // 7 days
      case 'ADEQUATE':
        return new Date(now.getTime() + 3 * 24 * 60 * 60 * 1000); // 3 days
      case 'WEAK':
        return new Date(now.getTime() + 24 * 60 * 60 * 1000);     // 1 day
    }
  }

  // ============================================================
  // AUDIT PERSISTENCE
  // ============================================================

  private async persistAudit(
    input: SDEInput,
    decision: StrategyDecision,
    riskProfile: RiskProfile,
    vetoThresholds: Record<string, number>,
    scoreWeights: ScoreWeights,
  ): Promise<string> {
    const id = `audit_${Date.now()}_${input.strategyId.slice(0, 8)}`;

    const auditRecord: DecisionAuditRecord = {
      id,
      strategyId: input.strategyId,
      timestamp: decision.timestamp,
      inputs: {
        backtest: input.backtest,
        monteCarlo: input.monteCarlo,
        walkForward: input.walkForward,
        operability: input.operability,
        paperTrading: input.paperTrading,
        portfolioState: input.portfolioState,
        riskProfile,
      },
      processing: {
        vetoResults: decision.vetoResults,
        scores: decision.scores,
        weightsUsed: decision.weightsUsed,
        signalQuality: decision.signalQuality,
      },
      decision,
      configSnapshot: {
        vetoThresholds,
        scoreWeights,
        riskProfile,
        sdeVersion: SDE_VERSION,
      },
    };

    try {
      await db.decisionAudit.create({
        data: {
          id: auditRecord.id,
          strategyId: auditRecord.strategyId,
          timestamp: auditRecord.timestamp,
          inputs: JSON.stringify(auditRecord.inputs),
          processing: JSON.stringify(auditRecord.processing),
          decision: JSON.stringify(auditRecord.decision),
          configSnapshot: JSON.stringify(auditRecord.configSnapshot),
        },
      });
    } catch (error) {
      // Audit persistence failure should NOT block the decision
      console.error('[SDE] Failed to persist audit record:', error);
    }

    return id;
  }

  // ============================================================
  // PORTFOLIO REVIEW — Review all active strategies
  // ============================================================

  async portfolioReview(
    strategies: SDEInput[],
    riskProfile?: RiskProfile,
    skipAudit: boolean = false,
  ): Promise<StrategyDecision[]> {
    const decisions: StrategyDecision[] = [];

    for (const strategyInput of strategies) {
      const input = { ...strategyInput, riskProfile };
      const decision = await this.validate(input, skipAudit);
      decisions.push(decision);
    }

    // Sort by: REJECTED first, then PAUSED, CONDITIONAL, ACTIVE
    const stateOrder: Record<StrategyState, number> = {
      REJECTED: 0,
      PAUSED: 1,
      CONDITIONAL: 2,
      ACTIVE: 3,
    };

    decisions.sort((a, b) => stateOrder[a.state] - stateOrder[b.state]);

    return decisions;
  }

  // ============================================================
  // FEEDBACK — Update audit record with outcome
  // ============================================================

  async provideFeedback(
    auditId: string,
    wasCorrect: boolean,
    realizedPnlPct: number,
  ): Promise<void> {
    try {
      // Find the audit record
      const audit = await db.decisionAudit.findUnique({
        where: { id: auditId },
      });

      if (!audit) {
        console.warn(`[SDE] Audit record ${auditId} not found for feedback`);
        return;
      }

      const now = new Date();
      const decisionData = JSON.parse(audit.decision) as StrategyDecision;
      // JSON.parse serializes Date as ISO string — must revive
      const decisionTimestamp = new Date(decisionData.timestamp).getTime();
      const daysUntilReeval = Math.max(0,
        (now.getTime() - decisionTimestamp) / (1000 * 60 * 60 * 24),
      );

      const feedback = {
        wasCorrect,
        realizedPnlPct,
        daysUntilReevaluation: Math.round(daysUntilReeval * 100) / 100,
        feedbackDate: now.toISOString(),
      };

      await db.decisionAudit.update({
        where: { id: auditId },
        data: { feedback: JSON.stringify(feedback) },
      });
    } catch (error) {
      console.error('[SDE] Failed to update audit feedback:', error);
    }
  }

  // ============================================================
  // QUERY AUDIT — Retrieve audit records
  // ============================================================

  async queryAudit(params: {
    strategyId?: string;
    from?: Date;
    to?: Date;
    limit?: number;
  }): Promise<DecisionAuditRecord[]> {
    const where: Record<string, unknown> = {};

    if (params.strategyId) {
      where.strategyId = params.strategyId;
    }

    if (params.from || params.to) {
      where.timestamp = {};
      if (params.from) (where.timestamp as Record<string, unknown>).gte = params.from;
      if (params.to) (where.timestamp as Record<string, unknown>).lte = params.to;
    }

    const records = await db.decisionAudit.findMany({
      where,
      orderBy: { timestamp: 'desc' },
      take: params.limit ?? 50,
    });

    return records.map(r => ({
      id: r.id,
      strategyId: r.strategyId,
      timestamp: r.timestamp,
      inputs: JSON.parse(r.inputs),
      processing: JSON.parse(r.processing),
      decision: JSON.parse(r.decision),
      configSnapshot: JSON.parse(r.configSnapshot),
      feedback: r.feedback ? JSON.parse(r.feedback) : undefined,
    }));
  }

  // ============================================================
  // HELPER: Build SDE input from strategy ID
  // ============================================================

  async buildInputFromStrategyId(
    strategyId: string,
    portfolioState: SDEInput['portfolioState'],
    riskProfile?: RiskProfile,
  ): Promise<SDEInput | null> {
    try {
      // Load the trading system
      const system = await db.tradingSystem.findUnique({
        where: { id: strategyId },
        include: { backtests: { orderBy: { createdAt: 'desc' }, take: 1 } },
      });

      if (!system) return null;

      // Get latest backtest
      const latestBacktest = system.backtests[0];

      // Default snapshots (will be overwritten if data available)
      const backtest: BacktestSnapshot = {
        totalTrades: latestBacktest?.totalTrades ?? 0,
        winRate: latestBacktest?.winRate ?? 0,
        avgWinPct: latestBacktest?.avgWin ?? 0,
        avgLossPct: Math.abs(latestBacktest?.avgLoss ?? 0),
        maxDrawdownPct: latestBacktest?.maxDrawdownPct ?? 0,
        sharpeRatio: latestBacktest?.sharpeRatio ?? 0,
        sortinoRatio: latestBacktest?.sortinoRatio ?? 0,
        profitFactor: latestBacktest?.profitFactor ?? 0,
        expectancy: latestBacktest?.expectancy ?? 0,
        overfittingScore: latestBacktest?.inSampleScore != null && latestBacktest?.outOfSampleScore != null
          ? Math.max(0, latestBacktest.inSampleScore - latestBacktest.outOfSampleScore)
          : 0.5,
        parameterStability: latestBacktest?.walkForwardRatio ?? 0.5,
        recoveryFactor: latestBacktest?.recoveryFactor ?? 0,
        payoffRatio: (latestBacktest?.avgWin && latestBacktest?.avgLoss && latestBacktest.avgLoss !== 0)
          ? latestBacktest.avgWin / Math.abs(latestBacktest.avgLoss)
          : 1,
      };

      // Default MC (placeholder — real MC requires running simulation)
      const monteCarlo: MonteCarloSnapshot = {
        riskOfRuin: backtest.maxDrawdownPct > 30 ? 0.10 : 0.02,
        probabilityOfProfit: backtest.winRate > 0.5 ? 0.65 : 0.35,
        p95MaxDrawdown: backtest.maxDrawdownPct * 1.5,
        meanFinalEquity: 10000,
        medianFinalEquity: 10000,
        stdDevFinalEquity: 3000,
        simulationsCount: 0,
        ruinThreshold: 0.5,
      };

      // Default WF (placeholder — real WF requires running analysis)
      const walkForward: WalkForwardSnapshot = {
        aggregateWFE: backtest.parameterStability,
        isRobust: backtest.parameterStability >= 0.3,
        recommendation: backtest.parameterStability >= 0.5 ? 'ROBUST'
          : backtest.parameterStability >= 0.3 ? 'MARGINAL' : 'OVERFIT',
        parameterStability: backtest.parameterStability,
        overallDegradation: backtest.overfittingScore,
        performanceConsistency: 1 - backtest.overfittingScore,
        windowCount: 0,
      };

      // Default operability
      const operability: OperabilitySnapshot = {
        overallScore: 50,
        level: 'MARGINAL',
        isOperable: true,
        recommendedPositionUsd: portfolioState.totalCapitalUsd * 0.05,
        minimumGainPct: 3,
        feeEstimateTotalCostPct: 1,
      };

      // [INTEGRATION FIX] Check if MC/WF data is real or fabricated
      const hasRealMCData = monteCarlo.simulationsCount > 0;
      const hasRealWFData = walkForward.windowCount > 0;

      if (!hasRealMCData || !hasRealWFData) {
        // No real MC/WF data — hard block instead of placeholder
        return {
          strategyId: system.id,
          strategyName: system.name,
          backtest,
          monteCarlo: null as unknown as MonteCarloSnapshot,
          walkForward: null as unknown as WalkForwardSnapshot,
          operability,
          portfolioState,
          riskProfile,
          dataQuality: 'INSUFFICIENT' as const,
        };
      }

      return {
        strategyId: system.id,
        strategyName: system.name,
        backtest,
        monteCarlo,
        walkForward,
        operability,
        portfolioState,
        riskProfile,
        dataQuality: 'REAL',
      };
    } catch (error) {
      console.error('[SDE] Failed to build input from strategy ID:', error);
      return null;
    }
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const strategyDecisionEngine = new StrategyDecisionEngine();
