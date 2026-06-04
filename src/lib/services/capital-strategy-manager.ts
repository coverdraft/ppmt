/**
 * Capital Strategy Manager - CryptoQuant Terminal
 *
 * EL CEREBRO DEL CAPITAL: Decide automáticamente:
 *
 * 1. CUÁNTAS estrategias usar (1 concentrada vs múltiples diversificadas)
 * 2. QUÉ porcentaje del capital asignar a cada estrategia
 * 3. CUÁNDO cambiar de estrategia (switch dinámico)
 * 4. CÓMO ajustar basándose en el feedback de ciclos anteriores
 *
 * Reglas del capital:
 * - Capital < $20: 1 sola estrategia (concentración máxima, fees lo matan si diversifica)
 * - Capital $20-$100: 1-2 estrategias (concentración con reserva)
 * - Capital $100-$500: 2-3 estrategias (diversificación moderada)
 * - Capital $500+: 3-5 estrategias (diversificación inteligente)
 *
 * Aprendizaje continuo:
 * - Si una estrategia tiene win rate > 60% en los últimos N ciclos → aumentar su peso
 * - Si una estrategia tiene win rate < 40% → reducir peso o pausar
 * - Si el drawdown supera el 15% → modo conservador (1 estrategia, tamaño mínimo)
 * - Si estamos en win streak → aumentar tamaño (ADAPTIVE sizing)
 * - Si estamos en loss streak → reducir tamaño y concentrar en la mejor estrategia
 *
 * Fee awareness:
 * - Con $10 de capital, fees de ~0.6% round-trip = $0.06 por trade
 * - Para ser rentable después de fees, necesitamos ganar > 1.8% por trade
 * - Si solo 2 de 20 tokens son operables → usar 1 estrategia en esos 2
 * - No diversificar si los fees se comen la ganancia marginal
 */

import { db } from '../db';
import {
  CapitalAllocationEngine,
  type AllocationMethod,
  type AllocationInput,
  type AllocationOutput,
  type Signal,
} from './capital-allocation';
import type { SystemRecommendation } from './trading-system-matcher';
import type { CycleResult } from './brain-cycle-engine';

// ============================================================
// TYPES
// ============================================================

export type StrategyMode = 'CONCENTRATED' | 'DUAL' | 'DIVERSIFIED' | 'ULTRA_CONSERVATIVE';

export interface StrategyAllocation {
  /** ID de la estrategia (system category) */
  strategyId: string;
  /** Nombre legible */
  strategyName: string;
  /** Porcentaje del capital asignado (0-100) */
  capitalPct: number;
  /** Monto en USD */
  capitalUsd: number;
  /** Método de allocation usado */
  allocationMethod: AllocationMethod;
  /** Número máximo de posiciones para esta estrategia */
  maxPositions: number;
  /** Tamaño por posición en USD */
  positionSizeUsd: number;
  /** Confianza en esta estrategia basada en rendimiento histórico */
  confidenceScore: number;
  /** Razón de la asignación */
  reason: string;
}

export interface CapitalStrategyDecision {
  /** Modo de estrategia seleccionado */
  mode: StrategyMode;
  /** Capital total disponible */
  totalCapitalUsd: number;
  /** Capital disponible para operar (después de reserva) */
  tradeableCapitalUsd: number;
  /** Reserva de cash (%) */
  cashReservePct: number;
  /** Reserva de cash (USD) */
  cashReserveUsd: number;
  /** Asignaciones por estrategia */
  allocations: StrategyAllocation[];
  /** Número total de estrategias activas */
  activeStrategies: number;
  /** Ganancia mínima esperada después de fees (%) */
  minimumGainAfterFeesPct: number;
  /** Score de salud del capital */
  capitalHealthScore: number;
  /** Razones de la decisión */
  reasoning: string[];
  /** Timestamp */
  decidedAt: Date;
}

export interface StrategyPerformanceRecord {
  strategyId: string;
  cyclesUsed: number;
  winRate: number;
  avgPnlPct: number;
  totalPnlUsd: number;
  sharpeRatio: number;
  lastUsedAt: Date;
}

export interface CapitalLearningState {
  /** Win streak actual (positivo = wins, negativo = losses) */
  winStreak: number;
  /** Drawdown actual (%) */
  currentDrawdownPct: number;
  /** Último modo usado */
  lastMode: StrategyMode;
  /** Rendimiento por estrategia (últimos N ciclos) */
  strategyPerformance: Record<string, StrategyPerformanceRecord>;
  /** Número de ciclos completados */
  totalCyclesCompleted: number;
  /** Capital inicial */
  initialCapitalUsd: number;
  /** Mejor capital alcanzado */
  peakCapitalUsd: number;
  /** Ajustes acumulados por feedback */
  feedbackAdjustments: {
    confidenceBoost: Record<string, number>;  // strategyId → boost amount
    confidencePenalty: Record<string, number>; // strategyId → penalty amount
    operabilityThresholdAdjust: number;        // adjust min operability level
    expectedGainAdjust: number;                // adjust expected gain %
  };
}

// ============================================================
// CAPITAL THRESHOLDS
// ============================================================

const CAPITAL_THRESHOLDS = {
  ULTRA_SMALL: 20,    // $0-$20: ultra concentrated
  SMALL: 100,         // $20-$100: concentrated with reserve
  MEDIUM: 500,        // $100-$500: moderate diversification
  LARGE: 5000,        // $500-$5000: diversified
  // $5000+: ultra diversified
};

const FEE_THRESHOLDS = {
  /** Ganancia mínima después de fees para que valga la pena */
  MIN_GAIN_AFTER_FEES_PCT: 1.8,
  /** Costo promedio round-trip en Solana (%) */
  AVG_SOL_ROUNDTRIP_PCT: 0.6,
  /** Costo promedio round-trip en Ethereum (%) */
  AVG_ETH_ROUNDTRIP_PCT: 2.5,
  /** Factor de seguridad: la ganancia esperada debe ser N× los fees */
  FEE_SAFETY_MULTIPLIER: 3,
};

const DRAWDOWN_THRESHOLDS = {
  WARNING: 10,      // 10% drawdown → reduce position sizes
  DANGER: 15,       // 15% drawdown → switch to ultra conservative
  CRITICAL: 25,     // 25% drawdown → stop trading, only watch
};

// ============================================================
// STRATEGY REGISTRY
// ============================================================

interface StrategyInfo {
  id: string;
  name: string;
  category: string;
  description: string;
  minCapitalUsd: number;
  riskLevel: 'LOW' | 'MEDIUM' | 'HIGH';
  feeTolerance: number;  // How well this strategy handles fees (0-1)
  defaultWeight: number;
}

const STRATEGY_REGISTRY: StrategyInfo[] = [
  {
    id: 'alpha-hunter',
    name: 'Alpha Hunter',
    category: 'ALPHA_HUNTER',
    description: 'Busca tokens en fases tempranas con alto potencial. Mejor con capital concentrado.',
    minCapitalUsd: 5,
    riskLevel: 'HIGH',
    feeTolerance: 0.6,
    defaultWeight: 0.25,
  },
  {
    id: 'smart-money',
    name: 'Smart Money',
    category: 'SMART_MONEY',
    description: 'Sigue los movimientos de smart money. Funciona bien en todas las condiciones de capital.',
    minCapitalUsd: 5,
    riskLevel: 'MEDIUM',
    feeTolerance: 0.8,
    defaultWeight: 0.25,
  },
  {
    id: 'technical',
    name: 'Technical',
    category: 'TECHNICAL',
    description: 'Análisis técnico clásico. Necesita liquidez mínima para ser efectivo.',
    minCapitalUsd: 10,
    riskLevel: 'MEDIUM',
    feeTolerance: 0.7,
    defaultWeight: 0.15,
  },
  {
    id: 'defensive',
    name: 'Defensive',
    category: 'DEFENSIVE',
    description: 'Estrategia defensiva para mercados en declive. Prioriza preservar capital.',
    minCapitalUsd: 5,
    riskLevel: 'LOW',
    feeTolerance: 0.9,
    defaultWeight: 0.15,
  },
  {
    id: 'bot-aware',
    name: 'Bot Aware',
    category: 'BOT_AWARE',
    description: 'Opera evitando trampas de bots. Bueno para tokens con alta actividad de bots.',
    minCapitalUsd: 5,
    riskLevel: 'MEDIUM',
    feeTolerance: 0.75,
    defaultWeight: 0.10,
  },
  {
    id: 'deep-research',
    name: 'Deep Research',
    category: 'DEEP_ANALYSIS',
    description: 'Análisis profundo con señales confirmadas. Requiere más tiempo pero más preciso.',
    minCapitalUsd: 10,
    riskLevel: 'LOW',
    feeTolerance: 0.85,
    defaultWeight: 0.05,
  },
  {
    id: 'micro-cap',
    name: 'Micro Cap',
    category: 'MICRO_STRUCTURE',
    description: 'Especializado en microestructura de mercado. Para tokens con poca liquidez.',
    minCapitalUsd: 5,
    riskLevel: 'HIGH',
    feeTolerance: 0.5,
    defaultWeight: 0.03,
  },
  {
    id: 'adaptive',
    name: 'Adaptive',
    category: 'ADAPTIVE',
    description: 'Se adapta dinámicamente a las condiciones del mercado. Meta-estrategia.',
    minCapitalUsd: 10,
    riskLevel: 'MEDIUM',
    feeTolerance: 0.8,
    defaultWeight: 0.02,
  },
];

// ============================================================
// CAPITAL STRATEGY MANAGER CLASS
// ============================================================

class CapitalStrategyManager {
  private allocationEngine: CapitalAllocationEngine;
  private learningState: CapitalLearningState;

  constructor() {
    this.allocationEngine = new CapitalAllocationEngine();
    this.learningState = {
      winStreak: 0,
      currentDrawdownPct: 0,
      lastMode: 'CONCENTRATED',
      strategyPerformance: {},
      totalCyclesCompleted: 0,
      initialCapitalUsd: 10,
      peakCapitalUsd: 10,
      feedbackAdjustments: {
        confidenceBoost: {},
        confidencePenalty: {},
        operabilityThresholdAdjust: 0,
        expectedGainAdjust: 0,
      },
    };
  }

  // ============================================================
  // 1. DECIDE STRATEGY MODE & ALLOCATION
  // ============================================================

  /**
   * Toma la decisión completa de cómo asignar el capital.
   *
   * Pipeline:
   * 1. Evaluar estado del capital (tamaño, drawdown, streak)
   * 2. Determinar modo de estrategia
   * 3. Seleccionar estrategias elegibles
   * 4. Calcular asignación por estrategia
   * 5. Aplicar fee awareness
   * 6. Aplicar ajustes de feedback/aprendizaje
   */
  async decide(
    capitalUsd: number,
    initialCapitalUsd: number,
    systemRecommendations: SystemRecommendation[],
    chain: string = 'SOL'
  ): Promise<CapitalStrategyDecision> {
    const reasoning: string[] = [];
    const decidedAt = new Date();

    // Update learning state
    this.learningState.initialCapitalUsd = initialCapitalUsd;
    if (capitalUsd > this.learningState.peakCapitalUsd) {
      this.learningState.peakCapitalUsd = capitalUsd;
    }
    this.learningState.currentDrawdownPct = this.learningState.peakCapitalUsd > 0
      ? ((this.learningState.peakCapitalUsd - capitalUsd) / this.learningState.peakCapitalUsd) * 100
      : 0;

    // === STEP 1: Evaluate capital health ===
    const capitalHealth = this.evaluateCapitalHealth(capitalUsd, initialCapitalUsd);
    reasoning.push(`Capital health: ${capitalHealth.score}/100 (${capitalHealth.status})`);

    // === STEP 2: Determine strategy mode ===
    const mode = this.determineMode(capitalUsd, capitalHealth);
    reasoning.push(`Strategy mode: ${mode} (capital: $${capitalUsd.toFixed(2)})`);

    // === STEP 3: Calculate cash reserve ===
    const cashReservePct = this.calculateCashReserve(capitalUsd, capitalHealth);
    const cashReserveUsd = capitalUsd * (cashReservePct / 100);
    const tradeableCapitalUsd = capitalUsd - cashReserveUsd;
    reasoning.push(`Cash reserve: ${cashReservePct.toFixed(0)}% ($${cashReserveUsd.toFixed(2)}) → Tradeable: $${tradeableCapitalUsd.toFixed(2)}`);

    // === STEP 4: Calculate minimum gain after fees ===
    const avgFeePct = chain === 'ETH' ? FEE_THRESHOLDS.AVG_ETH_ROUNDTRIP_PCT : FEE_THRESHOLDS.AVG_SOL_ROUNDTRIP_PCT;
    const minimumGainAfterFeesPct = FEE_THRESHOLDS.MIN_GAIN_AFTER_FEES_PCT + this.learningState.feedbackAdjustments.expectedGainAdjust;
    reasoning.push(`Min gain after fees: ${minimumGainAfterFeesPct.toFixed(1)}% (fees: ~${avgFeePct}%)`);

    // === STEP 5: Select eligible strategies ===
    const eligibleStrategies = this.selectEligibleStrategies(capitalUsd, mode, systemRecommendations);
    reasoning.push(`Eligible strategies: ${eligibleStrategies.map(s => s.id).join(', ')} (${eligibleStrategies.length}/${STRATEGY_REGISTRY.length})`);

    // === STEP 6: Calculate allocation per strategy ===
    const allocations = this.calculateAllocations(
      eligibleStrategies,
      tradeableCapitalUsd,
      mode,
      systemRecommendations,
      chain
    );

    reasoning.push(`Active strategies: ${allocations.length} | Positions: ${allocations.reduce((s, a) => s + a.maxPositions, 0)}`);

    // === STEP 7: Apply learning adjustments ===
    this.applyLearningAdjustments(allocations);
    reasoning.push(`Learning adjustments applied: streak=${this.learningState.winStreak}, drawdown=${this.learningState.currentDrawdownPct.toFixed(1)}%`);

    return {
      mode,
      totalCapitalUsd: capitalUsd,
      tradeableCapitalUsd,
      cashReservePct,
      cashReserveUsd,
      allocations,
      activeStrategies: allocations.length,
      minimumGainAfterFeesPct,
      capitalHealthScore: capitalHealth.score,
      reasoning,
      decidedAt,
    };
  }

  // ============================================================
  // 2. CAPITAL HEALTH EVALUATION
  // ============================================================

  private evaluateCapitalHealth(
    capitalUsd: number,
    initialCapitalUsd: number
  ): { score: number; status: 'HEALTHY' | 'WARNING' | 'DANGER' | 'CRITICAL' } {
    let score = 50; // Base
    const drawdown = this.learningState.currentDrawdownPct;
    const growthPct = initialCapitalUsd > 0 ? ((capitalUsd - initialCapitalUsd) / initialCapitalUsd) * 100 : 0;

    // Growth bonus (0-25 points)
    if (growthPct > 50) score += 25;
    else if (growthPct > 20) score += 18;
    else if (growthPct > 5) score += 10;
    else if (growthPct > 0) score += 5;
    else if (growthPct < -10) score -= 10;
    else if (growthPct < -25) score -= 20;

    // Drawdown penalty (0-25 points)
    if (drawdown < 5) score += 25;
    else if (drawdown < 10) score += 15;
    else if (drawdown < 15) score += 5;
    else if (drawdown < 25) score -= 10;
    else score -= 25;

    // Win streak bonus/penalty (0-15 points)
    const streak = this.learningState.winStreak;
    if (streak > 5) score += 15;
    else if (streak > 3) score += 10;
    else if (streak > 0) score += 5;
    else if (streak < -3) score -= 10;
    else if (streak < -5) score -= 15;

    // Capital size factor (0-10 points)
    if (capitalUsd >= 1000) score += 10;
    else if (capitalUsd >= 100) score += 7;
    else if (capitalUsd >= 20) score += 4;
    else score += 1;

    score = Math.max(0, Math.min(100, score));

    let status: 'HEALTHY' | 'WARNING' | 'DANGER' | 'CRITICAL';
    if (drawdown >= DRAWDOWN_THRESHOLDS.CRITICAL) status = 'CRITICAL';
    else if (drawdown >= DRAWDOWN_THRESHOLDS.DANGER) status = 'DANGER';
    else if (drawdown >= DRAWDOWN_THRESHOLDS.WARNING) status = 'WARNING';
    else status = 'HEALTHY';

    return { score, status };
  }

  // ============================================================
  // 3. DETERMINE STRATEGY MODE
  // ============================================================

  private determineMode(
    capitalUsd: number,
    health: { score: number; status: string }
  ): StrategyMode {
    // Override: si drawdown crítico → ultra conservador
    if (health.status === 'CRITICAL' || health.status === 'DANGER') {
      return 'ULTRA_CONSERVATIVE';
    }

    // Override: si win streak negativo fuerte → concentrar en la mejor
    if (this.learningState.winStreak < -3) {
      return 'CONCENTRATED';
    }

    // Basado en tamaño de capital
    if (capitalUsd < CAPITAL_THRESHOLDS.ULTRA_SMALL) {
      return 'CONCENTRATED';
    }
    if (capitalUsd < CAPITAL_THRESHOLDS.SMALL) {
      // Con capital pequeño, podemos intentar dual si tenemos buena racha
      return this.learningState.winStreak > 2 ? 'DUAL' : 'CONCENTRATED';
    }
    if (capitalUsd < CAPITAL_THRESHOLDS.MEDIUM) {
      return this.learningState.winStreak > 3 ? 'DIVERSIFIED' : 'DUAL';
    }
    if (capitalUsd < CAPITAL_THRESHOLDS.LARGE) {
      return 'DIVERSIFIED';
    }

    // Capital grande → diversificación inteligente
    return 'DIVERSIFIED';
  }

  // ============================================================
  // 4. CASH RESERVE CALCULATION
  // ============================================================

  private calculateCashReserve(
    capitalUsd: number,
    health: { score: number; status: string }
  ): number {
    let reservePct = 20; // Base 20%

    // Más reserva cuando hay drawdown
    if (health.status === 'DANGER') reservePct = 50;
    else if (health.status === 'WARNING') reservePct = 35;

    // Más reserva cuando perdemos seguido
    if (this.learningState.winStreak < -3) reservePct = Math.min(70, reservePct + 20);

    // Menos reserva cuando ganamos seguido (pero nunca < 15%)
    if (this.learningState.winStreak > 5) reservePct = Math.max(15, reservePct - 5);

    // Con capital muy pequeño, necesitamos más reserva porque 1 trade malo nos elimina
    if (capitalUsd < 20) reservePct = Math.max(reservePct, 30);

    return reservePct;
  }

  // ============================================================
  // 5. SELECT ELIGIBLE STRATEGIES
  // ============================================================

  private selectEligibleStrategies(
    capitalUsd: number,
    mode: StrategyMode,
    recommendations: SystemRecommendation[]
  ): StrategyInfo[] {
    // Filtrar por capital mínimo
    let eligible = STRATEGY_REGISTRY.filter(s => capitalUsd >= s.minCapitalUsd);

    // Priorizar estrategias que tienen tokens operables recomendados
    const recommendedSystems = new Set(recommendations.map(r => r.primarySystem));
    eligible = eligible.filter(s => recommendedSystems.has(s.id) || mode === 'ULTRA_CONSERVATIVE');

    // Si no hay coincidencias con las recomendaciones, usar las mejores por defecto
    if (eligible.length === 0) {
      eligible = STRATEGY_REGISTRY.filter(s => capitalUsd >= s.minCapitalUsd);
    }

    // Limitar por modo
    switch (mode) {
      case 'CONCENTRATED':
      case 'ULTRA_CONSERVATIVE':
        // Solo la mejor estrategia
        eligible = [this.selectBestStrategy(eligible, recommendations)];
        break;
      case 'DUAL':
        // Las 2 mejores
        eligible = this.rankStrategies(eligible, recommendations).slice(0, 2);
        break;
      case 'DIVERSIFIED':
        // 3-5 mejores
        eligible = this.rankStrategies(eligible, recommendations).slice(0, Math.min(5, eligible.length));
        break;
    }

    return eligible;
  }

  private selectBestStrategy(
    strategies: StrategyInfo[],
    recommendations: SystemRecommendation[]
  ): StrategyInfo {
    // Si hay recomendaciones, elegir la que tiene más tokens tradeable
    const tradeableCounts: Record<string, number> = {};
    for (const rec of recommendations) {
      if (rec.shouldTrade) {
        tradeableCounts[rec.primarySystem] = (tradeableCounts[rec.primarySystem] || 0) + 1;
      }
    }

    // Score compuesto: tradeable tokens + fee tolerance + performance history
    const scored = strategies.map(s => {
      let score = s.defaultWeight * 100;
      score += (tradeableCounts[s.id] || 0) * 20;
      score += s.feeTolerance * 30;

      // Learning boost/penalty
      const perf = this.learningState.strategyPerformance[s.id];
      if (perf) {
        if (perf.winRate > 0.6) score += 30;
        else if (perf.winRate > 0.5) score += 15;
        else if (perf.winRate < 0.4) score -= 20;
      }

      const boost = this.learningState.feedbackAdjustments.confidenceBoost[s.id] || 0;
      const penalty = this.learningState.feedbackAdjustments.confidencePenalty[s.id] || 0;
      score += (boost - penalty) * 10;

      return { strategy: s, score };
    });

    scored.sort((a, b) => b.score - a.score);
    return scored[0]?.strategy || strategies[0];
  }

  private rankStrategies(
    strategies: StrategyInfo[],
    recommendations: SystemRecommendation[]
  ): StrategyInfo[] {
    const tradeableCounts: Record<string, number> = {};
    for (const rec of recommendations) {
      if (rec.shouldTrade) {
        tradeableCounts[rec.primarySystem] = (tradeableCounts[rec.primarySystem] || 0) + 1;
      }
    }

    return strategies
      .map(s => {
        let score = s.defaultWeight * 100;
        score += (tradeableCounts[s.id] || 0) * 20;
        score += s.feeTolerance * 30;

        const perf = this.learningState.strategyPerformance[s.id];
        if (perf) {
          if (perf.winRate > 0.6) score += 30;
          else if (perf.winRate > 0.5) score += 15;
          else if (perf.winRate < 0.4) score -= 20;
        }

        const boost = this.learningState.feedbackAdjustments.confidenceBoost[s.id] || 0;
        const penalty = this.learningState.feedbackAdjustments.confidencePenalty[s.id] || 0;
        score += (boost - penalty) * 10;

        return { strategy: s, score };
      })
      .sort((a, b) => b.score - a.score)
      .map(s => s.strategy);
  }

  // ============================================================
  // 6. CALCULATE ALLOCATIONS
  // ============================================================

  private calculateAllocations(
    strategies: StrategyInfo[],
    tradeableCapitalUsd: number,
    mode: StrategyMode,
    recommendations: SystemRecommendation[],
    chain: string
  ): StrategyAllocation[] {
    if (strategies.length === 0 || tradeableCapitalUsd <= 0) {
      return [];
    }

    // Determine allocation method based on mode
    const allocationMethod = this.selectAllocationMethod(mode, tradeableCapitalUsd);

    // Build signals for allocation engine
    const signals: Signal[] = strategies.map(s => ({
      tokenAddress: s.id, // Using strategy ID as identifier
      confidence: this.calculateStrategyConfidence(s, recommendations),
      direction: 'LONG' as const,
    }));

    // Count tradeable tokens per strategy
    const tradeablePerStrategy: Record<string, number> = {};
    for (const rec of recommendations) {
      if (rec.shouldTrade) {
        tradeablePerStrategy[rec.primarySystem] = (tradeablePerStrategy[rec.primarySystem] || 0) + 1;
      }
    }

    // Calculate weights based on strategy performance and tradeable tokens
    const weights = this.calculateStrategyWeights(strategies, tradeablePerStrategy, mode);

    // Calculate position sizes using the allocation engine
    const allocationInput: AllocationInput = {
      capital: tradeableCapitalUsd,
      currentPositions: [],
      signals,
      historicalTrades: {
        winRate: this.getOverallWinRate(),
        avgWin: 0.15, // 15% average win
        avgLoss: 0.08, // 8% average loss
        totalTrades: Math.max(1, this.learningState.totalCyclesCompleted),
      },
      volatility: 0.6, // Crypto volatility
      currentDrawdown: this.learningState.currentDrawdownPct / 100,
      maxDrawdown: 0.25,
      marketRegime: 'SIDEWAYS', // Will be overridden by brain context
      estimatedFeePct: (chain === 'ETH' ? FEE_THRESHOLDS.AVG_ETH_ROUNDTRIP_PCT : FEE_THRESHOLDS.AVG_SOL_ROUNDTRIP_PCT) / 100,
      estimatedSlippagePct: 0.005, // 0.5% slippage
      minimumNetGainPct: FEE_THRESHOLDS.MIN_GAIN_AFTER_FEES_PCT / 100,
      expectedGainPct: 0.05, // 5% expected gain
      riskPerTrade: this.calculateRiskPerTrade(),
      stopLossPct: 0.10,
      streakType: this.learningState.winStreak > 0 ? 'WIN' : 'LOSS',
      streakLength: Math.abs(this.learningState.winStreak),
    };

    let engineOutput: AllocationOutput;
    try {
      engineOutput = this.allocationEngine.calculate(allocationMethod, allocationInput);
    } catch {
      // Fallback to equal weight
      engineOutput = this.allocationEngine.calculate('EQUAL_WEIGHT', allocationInput);
    }

    // Map engine output to strategy allocations
    const allocations: StrategyAllocation[] = strategies.map((strategy, i) => {
      const weight = weights[i] || (1 / strategies.length);
      const capitalPct = weight * 100;
      const capitalUsd = tradeableCapitalUsd * weight;
      const maxPositions = this.calculateMaxPositions(capitalUsd, mode, strategy);
      const positionSizeUsd = maxPositions > 0 ? capitalUsd / maxPositions : 0;

      return {
        strategyId: strategy.id,
        strategyName: strategy.name,
        capitalPct: Math.round(capitalPct * 100) / 100,
        capitalUsd: Math.round(capitalUsd * 100) / 100,
        allocationMethod,
        maxPositions,
        positionSizeUsd: Math.round(positionSizeUsd * 100) / 100,
        confidenceScore: this.calculateStrategyConfidence(strategy, recommendations),
        reason: this.getAllocationReason(strategy, mode, weight, tradeablePerStrategy[strategy.id] || 0),
      };
    });

    return allocations;
  }

  // ============================================================
  // 7. HELPER METHODS
  // ============================================================

  private selectAllocationMethod(mode: StrategyMode, capitalUsd: number): AllocationMethod {
    switch (mode) {
      case 'CONCENTRATED':
        return capitalUsd < 50 ? 'FIXED_AMOUNT' : 'KELLY_MODIFIED';
      case 'ULTRA_CONSERVATIVE':
        return 'MAX_DRAWDOWN_CONTROL';
      case 'DUAL':
        return capitalUsd < 100 ? 'SCORE_BASED' : 'KELLY_MODIFIED';
      case 'DIVERSIFIED':
        return capitalUsd < 500 ? 'RISK_PARITY' : 'META_ALLOCATION';
      default:
        return 'EQUAL_WEIGHT';
    }
  }

  private calculateStrategyConfidence(
    strategy: StrategyInfo,
    recommendations: SystemRecommendation[]
  ): number {
    let confidence = strategy.feeTolerance * 0.5; // Base from fee tolerance

    // Boost if this strategy has recommended tokens
    const recsForStrategy = recommendations.filter(r => r.primarySystem === strategy.id);
    const tradeableRecs = recsForStrategy.filter(r => r.shouldTrade);
    if (tradeableRecs.length > 0) {
      confidence += Math.min(0.4, tradeableRecs.length * 0.1);
    }

    // Adjust based on learning
    const perf = this.learningState.strategyPerformance[strategy.id];
    if (perf) {
      confidence += (perf.winRate - 0.5) * 0.3; // Positive if >50% win rate
    }

    return Math.max(0.1, Math.min(0.95, confidence));
  }

  private calculateStrategyWeights(
    strategies: StrategyInfo[],
    tradeablePerStrategy: Record<string, number>,
    mode: StrategyMode
  ): number[] {
    // Calculate raw weights
    const rawWeights = strategies.map(s => {
      let weight = s.defaultWeight;
      weight += (tradeablePerStrategy[s.id] || 0) * 0.1;

      // Performance adjustment
      const perf = this.learningState.strategyPerformance[s.id];
      if (perf) {
        weight *= (0.5 + perf.winRate); // Double weight for 50%+ win rate
      }

      return Math.max(weight, 0.01); // Minimum weight
    });

    // Normalize to sum to 1
    const totalWeight = rawWeights.reduce((s, w) => s + w, 0);
    if (totalWeight === 0) return strategies.map(() => 1 / strategies.length);

    return rawWeights.map(w => w / totalWeight);
  }

  private calculateMaxPositions(
    strategyCapitalUsd: number,
    mode: StrategyMode,
    strategy: StrategyInfo
  ): number {
    // Fee awareness: con capital pequeño, menos posiciones = menos fees
    const avgFeeUsd = strategyCapitalUsd * 0.006; // ~0.6% per round trip
    const minPositionSize = 1; // Minimum $1 per position

    let maxPositions: number;

    switch (mode) {
      case 'CONCENTRATED':
      case 'ULTRA_CONSERVATIVE':
        maxPositions = 1; // Una sola posición concentrada
        break;
      case 'DUAL':
        maxPositions = Math.min(2, Math.floor(strategyCapitalUsd / (minPositionSize * 3)));
        break;
      case 'DIVERSIFIED':
        maxPositions = Math.min(5, Math.floor(strategyCapitalUsd / (minPositionSize * 5)));
        break;
      default:
        maxPositions = 1;
    }

    // Asegurar que al menos 1 posición si hay capital suficiente
    if (strategyCapitalUsd >= minPositionSize && maxPositions === 0) {
      maxPositions = 1;
    }

    // Fee check: no abrir más posiciones de las que los fees permiten
    const maxAffordablePositions = avgFeeUsd > 0
      ? Math.floor(strategyCapitalUsd / (avgFeeUsd * FEE_THRESHOLDS.FEE_SAFETY_MULTIPLIER))
      : maxPositions;

    return Math.max(1, Math.min(maxPositions, maxAffordablePositions));
  }

  private calculateRiskPerTrade(): number {
    // Risk per trade as fraction of capital
    let riskPct = 0.02; // Base 2%

    // Reduce risk in drawdown
    if (this.learningState.currentDrawdownPct > 15) riskPct = 0.005;
    else if (this.learningState.currentDrawdownPct > 10) riskPct = 0.01;

    // Increase risk slightly on win streaks
    if (this.learningState.winStreak > 5) riskPct = 0.03;
    else if (this.learningState.winStreak > 3) riskPct = 0.025;

    return riskPct;
  }

  private getOverallWinRate(): number {
    const perfs = Object.values(this.learningState.strategyPerformance);
    if (perfs.length === 0) return 0.5; // Default 50%

    const totalCycles = perfs.reduce((s, p) => s + p.cyclesUsed, 0);
    if (totalCycles === 0) return 0.5;

    const weightedWinRate = perfs.reduce((s, p) => s + (p.winRate * p.cyclesUsed), 0) / totalCycles;
    return weightedWinRate;
  }

  private getAllocationReason(
    strategy: StrategyInfo,
    mode: StrategyMode,
    weight: number,
    tradeableTokens: number
  ): string {
    const reasons: string[] = [];

    reasons.push(`${mode} mode`);
    if (tradeableTokens > 0) {
      reasons.push(`${tradeableTokens} operable tokens`);
    }
    reasons.push(`${(weight * 100).toFixed(0)}% allocation`);
    reasons.push(`fee tolerance: ${(strategy.feeTolerance * 100).toFixed(0)}%`);

    const perf = this.learningState.strategyPerformance[strategy.id];
    if (perf) {
      reasons.push(`historical WR: ${(perf.winRate * 100).toFixed(0)}%`);
    }

    return reasons.join(' | ');
  }

  // ============================================================
  // 8. LEARNING & FEEDBACK
  // ============================================================

  /**
   * Aplica ajustes basados en el aprendizaje acumulado.
   * Modifica las asignaciones según el feedback histórico.
   */
  private applyLearningAdjustments(allocations: StrategyAllocation[]): void {
    for (const allocation of allocations) {
      const boost = this.learningState.feedbackAdjustments.confidenceBoost[allocation.strategyId] || 0;
      const penalty = this.learningState.feedbackAdjustments.confidencePenalty[allocation.strategyId] || 0;

      // Adjust confidence
      allocation.confidenceScore = Math.max(0.05, Math.min(0.95,
        allocation.confidenceScore + boost - penalty
      ));

      // Adjust position size based on confidence
      const confidenceMultiplier = 0.5 + allocation.confidenceScore; // 0.5x to 1.45x
      allocation.positionSizeUsd = Math.round(allocation.positionSizeUsd * confidenceMultiplier * 100) / 100;
    }
  }

  /**
   * Actualiza el estado de aprendizaje después de un ciclo.
   * Este es el punto donde el cerebro APRENDE de sus resultados.
   */
  async updateFromCycleResult(result: CycleResult): Promise<void> {
    this.learningState.totalCyclesCompleted++;

    // Update win/loss streak
    if (result.estimatedCyclePnlUsd > 0) {
      if (this.learningState.winStreak > 0) {
        this.learningState.winStreak++;
      } else {
        this.learningState.winStreak = 1;
      }
    } else if (result.estimatedCyclePnlUsd < 0) {
      if (this.learningState.winStreak < 0) {
        this.learningState.winStreak--;
      } else {
        this.learningState.winStreak = -1;
      }
    }

    // Update drawdown
    this.learningState.currentDrawdownPct = result.cumulativeReturnPct < 0
      ? Math.abs(result.cumulativeReturnPct)
      : 0;

    // Update strategy performance from top picks
    for (const pick of result.topPicks) {
      const strategyId = pick.primarySystem;
      if (!this.learningState.strategyPerformance[strategyId]) {
        this.learningState.strategyPerformance[strategyId] = {
          strategyId,
          cyclesUsed: 0,
          winRate: 0.5,
          avgPnlPct: 0,
          totalPnlUsd: 0,
          sharpeRatio: 0,
          lastUsedAt: new Date(),
        };
      }

      const perf = this.learningState.strategyPerformance[strategyId];
      perf.cyclesUsed++;
      perf.lastUsedAt = new Date();

      // Estimate win rate from recommendation confidence
      const estimatedWin = pick.config.confidence > 0.6;
      const alpha = 0.1; // EMA smoothing
      perf.winRate = perf.winRate * (1 - alpha) + (estimatedWin ? 1 : 0) * alpha;
      perf.avgPnlPct = perf.avgPnlPct * (1 - alpha) + pick.estimatedGainPct * alpha;
    }

    // Update feedback adjustments
    this.updateFeedbackAdjustments(result);

    // Persist learning state to DB
    try {
      await this.persistLearningState();
    } catch (error) {
      console.warn('[CapitalStrategy] Failed to persist learning state:', error);
    }
  }

  private updateFeedbackAdjustments(result: CycleResult): void {
    // Win streak adjustments
    if (this.learningState.winStreak > 5) {
      // Good streak: boost confidence in current strategies
      for (const pick of result.topPicks) {
        const current = this.learningState.feedbackAdjustments.confidenceBoost[pick.primarySystem] || 0;
        this.learningState.feedbackAdjustments.confidenceBoost[pick.primarySystem] = Math.min(0.3, current + 0.05);
      }
    } else if (this.learningState.winStreak < -3) {
      // Bad streak: penalize current strategies
      for (const pick of result.topPicks) {
        const current = this.learningState.feedbackAdjustments.confidencePenalty[pick.primarySystem] || 0;
        this.learningState.feedbackAdjustments.confidencePenalty[pick.primarySystem] = Math.min(0.5, current + 0.1);
      }
    }

    // Drawdown adjustments
    if (this.learningState.currentDrawdownPct > 15) {
      // Increase operability threshold (only trade premium tokens)
      this.learningState.feedbackAdjustments.operabilityThresholdAdjust = 20; // +20 points
      this.learningState.feedbackAdjustments.expectedGainAdjust = 2; // +2% expected gain
    } else if (this.learningState.currentDrawdownPct < 5) {
      // Can be more relaxed
      this.learningState.feedbackAdjustments.operabilityThresholdAdjust = -5;
      this.learningState.feedbackAdjustments.expectedGainAdjust = -0.5;
    }

    // Decay old adjustments (gradually forget)
    for (const key of Object.keys(this.learningState.feedbackAdjustments.confidenceBoost)) {
      this.learningState.feedbackAdjustments.confidenceBoost[key] *= 0.95;
      if (this.learningState.feedbackAdjustments.confidenceBoost[key] < 0.01) {
        delete this.learningState.feedbackAdjustments.confidenceBoost[key];
      }
    }
    for (const key of Object.keys(this.learningState.feedbackAdjustments.confidencePenalty)) {
      this.learningState.feedbackAdjustments.confidencePenalty[key] *= 0.95;
      if (this.learningState.feedbackAdjustments.confidencePenalty[key] < 0.01) {
        delete this.learningState.feedbackAdjustments.confidencePenalty[key];
      }
    }
  }

  async persistLearningState(): Promise<void> {
    // Store in a simple JSON format in the compound growth tracker or a separate mechanism
    // For now, we use the DB to track this via a PredictiveSignal with special type
    await db.predictiveSignal.upsert({
      where: {
        id: 'capital-strategy-learning-state',
      },
      create: {
        id: 'capital-strategy-learning-state',
        signalType: 'CAPITAL_STRATEGY_STATE',
        chain: 'SOL',
        prediction: JSON.stringify(this.learningState),
        confidence: 1,
        timeframe: 'permanent',
        evidence: JSON.stringify({
          totalCycles: this.learningState.totalCyclesCompleted,
          winStreak: this.learningState.winStreak,
          drawdown: this.learningState.currentDrawdownPct,
        }),
        historicalHitRate: this.getOverallWinRate(),
        dataPointsUsed: this.learningState.totalCyclesCompleted,
      },
      update: {
        prediction: JSON.stringify(this.learningState),
        evidence: JSON.stringify({
          totalCycles: this.learningState.totalCyclesCompleted,
          winStreak: this.learningState.winStreak,
          drawdown: this.learningState.currentDrawdownPct,
        }),
        historicalHitRate: this.getOverallWinRate(),
        dataPointsUsed: this.learningState.totalCyclesCompleted,
        updatedAt: new Date(),
      },
    });
  }

  /**
   * Carga el estado de aprendizaje desde la BD al iniciar.
   */
  async loadLearningState(): Promise<void> {
    try {
      const stored = await db.predictiveSignal.findUnique({
        where: { id: 'capital-strategy-learning-state' },
      });

      if (stored) {
        const loaded = JSON.parse(stored.prediction as string) as CapitalLearningState;
        this.learningState = {
          ...this.learningState,
          ...loaded,
          // Ensure numeric fields are valid
          winStreak: loaded.winStreak || 0,
          currentDrawdownPct: loaded.currentDrawdownPct || 0,
          totalCyclesCompleted: loaded.totalCyclesCompleted || 0,
          peakCapitalUsd: loaded.peakCapitalUsd || this.learningState.initialCapitalUsd,
          feedbackAdjustments: loaded.feedbackAdjustments || this.learningState.feedbackAdjustments,
          strategyPerformance: loaded.strategyPerformance || {},
        };
      }
    } catch (error) {
      console.warn('[CapitalStrategy] Could not load learning state:', error);
    }
  }

  // ============================================================
  // 9. GETTERS & UTILS
  // ============================================================

  getLearningState(): CapitalLearningState {
    return { ...this.learningState };
  }

  getAdjustedOperabilityThreshold(): number {
    return this.learningState.feedbackAdjustments.operabilityThresholdAdjust;
  }

  getAdjustedExpectedGain(): number {
    return this.learningState.feedbackAdjustments.expectedGainAdjust;
  }

  /**
   * Retorna un resumen ejecutivo del estado del capital para UI.
   */
  getCapitalSummary(): {
    mode: StrategyMode;
    capitalHealth: string;
    activeStrategies: number;
    winStreak: number;
    drawdownPct: number;
    overallWinRate: number;
    bestPerformingStrategy: string | null;
    worstPerformingStrategy: string | null;
    feedbackAdjustmentsActive: number;
  } {
    const perfs = Object.values(this.learningState.strategyPerformance);
    const sorted = [...perfs].sort((a, b) => b.winRate - a.winRate);

    return {
      mode: this.learningState.lastMode,
      capitalHealth: this.learningState.currentDrawdownPct < 5 ? 'HEALTHY'
        : this.learningState.currentDrawdownPct < 15 ? 'WARNING'
        : this.learningState.currentDrawdownPct < 25 ? 'DANGER'
        : 'CRITICAL',
      activeStrategies: perfs.filter(p => p.cyclesUsed > 0).length,
      winStreak: this.learningState.winStreak,
      drawdownPct: Math.round(this.learningState.currentDrawdownPct * 100) / 100,
      overallWinRate: Math.round(this.getOverallWinRate() * 100) / 100,
      bestPerformingStrategy: sorted[0]?.strategyId || null,
      worstPerformingStrategy: sorted[sorted.length - 1]?.strategyId || null,
      feedbackAdjustmentsActive:
        Object.keys(this.learningState.feedbackAdjustments.confidenceBoost).length +
        Object.keys(this.learningState.feedbackAdjustments.confidencePenalty).length,
    };
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const capitalStrategyManager = new CapitalStrategyManager();
