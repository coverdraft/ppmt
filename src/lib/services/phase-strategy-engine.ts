/**
 * Phase Strategy Engine - CryptoQuant Terminal
 * Motor de Estrategias por Etapa del Ciclo de Vida
 *
 * Este motor gestiona estrategias de trading diferenciadas por etapa:
 *
 *   EARLY (GENESIS + INCIPIENT):
 *     - Tokens nuevos, alta volatilidad, bots dominan
 *     - Estrategia: Scalping rápido, detección de bots, protección rug pull
 *     - Risk: ULTRA_HIGH - Stops muy ajustados, posiciones mínimas
 *     - Enfoque: Bot tracking, sniper avoidance, quick momentum
 *
 *   MID (GROWTH + FOMO):
 *     - Tokens en crecimiento, smart money activo, liquidez creciente
 *     - Estrategia: Seguir smart money, momentum riding, DCA
 *     - Risk: MODERATE - Stops relajados, posiciones decentes
 *     - Enfoque: Smart money following, volume analysis, phase transitions
 *
 *   STABLE (DECLINE + LEGACY):
 *     - Tokens establecidos tipo BTC y altcoins grandes
 *     - Estrategia: Trend following, mean reversion, macro correlation
 *     - Risk: CONSERVATIVE - Posiciones grandes, stops amplios
 *     - Enfoque: Long-term trends, macro indicators, value investing
 *
 * Cada etapa tiene sus propios parámetros de trading optimizados,
 * fórmulas de entrada/salida, y criterios de selección.
 */

import { db } from '../db';
import { type TokenPhase } from './token-lifecycle-engine';
import { type TradingStage, STAGE_CONFIG, PHASE_TO_STAGE } from './backtest-loop-engine';

// ============================================================
// TYPES
// ============================================================

export interface StageStrategy {
  stage: TradingStage;
  name: string;
  description: string;
  riskProfile: string;
  focusAreas: string[];

  // Trading parameters
  parameters: {
    stopLossPct: number;
    takeProfitPct: number;
    trailingStopPct: number;
    maxPositionPct: number;
    maxOpenPositions: number;
    cashReservePct: number;
    confidenceThreshold: number;
    slippageToleranceBps: number;
    maxHoldTimeMinutes: number;
    minLiquidityUsd: number;
    minVolume24h: number;
  };

  // Entry formula weights
  entryWeights: {
    momentum: number;
    volume: number;
    smartMoney: number;
    botActivity: number;  // Inverse for EARLY (avoid bots), positive for STABLE
    liquidity: number;
    volatility: number;
    dna: number;
    regime: number;
  };

  // Exit criteria
  exitCriteria: {
    trailingStopActivation: number;  // % gain to activate trailing stop
    takeProfitLevels: number[];      // Multiple TP levels
    stopLossType: 'FIXED' | 'TRAILING' | 'ATR_BASED';
    maxDrawdownPct: number;
    timeExitMinutes: number;
    signalReversalExit: boolean;
  };

  // Phase mapping
  phases: TokenPhase[];
  color: string;
  icon: string;
}

export interface StrategyMatchResult {
  tokenAddress: string;
  symbol: string;
  chain: string;
  stage: TradingStage;
  strategy: StageStrategy;
  matchScore: number;
  matchReason: string;
  phase: TokenPhase;
  phaseConfidence: number;
}

export interface StageMetrics {
  stage: TradingStage;
  tokensInStage: number;
  activeSystems: number;
  totalBacktests: number;
  avgSharpe: number;
  avgWinRate: number;
  bestPnlPct: number;
  totalTrades: number;
  improvementTrend: number;  // Positive = improving, negative = degrading
}

export interface PhaseStrategyReport {
  stages: Record<TradingStage, StageMetrics>;
  strategies: StageStrategy[];
  tokenDistribution: Record<TradingStage, number>;
  phaseDistribution: Record<TokenPhase, number>;
  topOpportunities: StrategyMatchResult[];
  recommendations: string[];
  generatedAt: Date;
}

// ============================================================
// STAGE STRATEGIES
// ============================================================

const STAGE_STRATEGIES: Record<TradingStage, StageStrategy> = {
  EARLY: {
    stage: 'EARLY',
    name: 'Alpha Hunter Early Stage',
    description: 'Estrategia agresiva para tokens en fase GENESIS/INCIPIENT. Alta velocidad, detección de bots, protección contra rug pulls.',
    riskProfile: 'ULTRA_HIGH',
    focusAreas: ['Bot tracking', 'Sniper avoidance', 'Quick momentum', 'Rug pull protection'],
    parameters: {
      stopLossPct: 8,
      takeProfitPct: 60,
      trailingStopPct: 5,
      maxPositionPct: 2,
      maxOpenPositions: 5,
      cashReservePct: 50,
      confidenceThreshold: 0.85,
      slippageToleranceBps: 200,
      maxHoldTimeMinutes: 120,
      minLiquidityUsd: 0,
      minVolume24h: 100,
    },
    entryWeights: {
      momentum: 0.25,
      volume: 0.15,
      smartMoney: 0.15,
      botActivity: -0.10,  // Negative = avoid high bot activity
      liquidity: 0.05,
      volatility: 0.15,
      dna: 0.10,
      regime: 0.05,
    },
    exitCriteria: {
      trailingStopActivation: 10,
      takeProfitLevels: [20, 40, 60],
      stopLossType: 'TRAILING',
      maxDrawdownPct: 8,
      timeExitMinutes: 120,
      signalReversalExit: true,
    },
    phases: ['GENESIS', 'INCIPIENT'],
    color: '#ef4444',
    icon: '🔥',
  },
  MID: {
    stage: 'MID',
    name: 'Smart Money Momentum',
    description: 'Estrategia equilibrada para tokens en fase GROWTH/FOMO. Seguimiento de smart money, momentum riding, DCA estratégico.',
    riskProfile: 'MODERATE',
    focusAreas: ['Smart money following', 'Momentum riding', 'Volume analysis', 'Phase transition detection'],
    parameters: {
      stopLossPct: 12,
      takeProfitPct: 40,
      trailingStopPct: 8,
      maxPositionPct: 5,
      maxOpenPositions: 8,
      cashReservePct: 30,
      confidenceThreshold: 0.70,
      slippageToleranceBps: 100,
      maxHoldTimeMinutes: 1440,
      minLiquidityUsd: 10000,
      minVolume24h: 5000,
    },
    entryWeights: {
      momentum: 0.20,
      volume: 0.15,
      smartMoney: 0.25,
      botActivity: -0.05,
      liquidity: 0.15,
      volatility: 0.10,
      dna: 0.05,
      regime: 0.05,
    },
    exitCriteria: {
      trailingStopActivation: 15,
      takeProfitLevels: [15, 30, 45],
      stopLossType: 'ATR_BASED',
      maxDrawdownPct: 12,
      timeExitMinutes: 1440,
      signalReversalExit: true,
    },
    phases: ['GROWTH', 'FOMO'],
    color: '#f59e0b',
    icon: '📈',
  },
  STABLE: {
    stage: 'STABLE',
    name: 'Macro Trend Rider',
    description: 'Estrategia conservadora para tokens establecidos tipo BTC/altcoins grandes. Trend following, mean reversion, correlación macro.',
    riskProfile: 'CONSERVATIVE',
    focusAreas: ['Trend following', 'Mean reversion', 'Macro correlation', 'Long-term value'],
    parameters: {
      stopLossPct: 15,
      takeProfitPct: 30,
      trailingStopPct: 10,
      maxPositionPct: 8,
      maxOpenPositions: 10,
      cashReservePct: 20,
      confidenceThreshold: 0.60,
      slippageToleranceBps: 50,
      maxHoldTimeMinutes: 10080, // 7 days
      minLiquidityUsd: 50000,
      minVolume24h: 100000,
    },
    entryWeights: {
      momentum: 0.15,
      volume: 0.10,
      smartMoney: 0.20,
      botActivity: 0.05,   // Positive = bots provide liquidity
      liquidity: 0.20,
      volatility: 0.05,
      dna: 0.10,
      regime: 0.15,
    },
    exitCriteria: {
      trailingStopActivation: 8,
      takeProfitLevels: [10, 20, 30],
      stopLossType: 'FIXED',
      maxDrawdownPct: 15,
      timeExitMinutes: 10080,
      signalReversalExit: false,
    },
    phases: ['DECLINE', 'LEGACY'],
    color: '#10b981',
    icon: '🏛️',
  },
};

// ============================================================
// PHASE STRATEGY ENGINE CLASS
// ============================================================

class PhaseStrategyEngine {
  /**
   * Genera un reporte completo de estrategias por etapa.
   */
  async generateReport(): Promise<PhaseStrategyReport> {
    // Get token phase distribution
    const lifecycleStates = await db.tokenLifecycleState.findMany({
      orderBy: { detectedAt: 'desc' },
      distinct: ['tokenAddress'],
      select: {
        phase: true,
        phaseProbability: true,
        tokenAddress: true,
      },
    });

    // Count by phase
    const phaseDistribution: Record<TokenPhase, number> = {
      GENESIS: 0, INCIPIENT: 0, GROWTH: 0, FOMO: 0, DECLINE: 0, LEGACY: 0,
    };

    for (const state of lifecycleStates) {
      const phase = state.phase as TokenPhase;
      if (phase in phaseDistribution) {
        phaseDistribution[phase]++;
      }
    }

    // Map to stages
    const tokenDistribution: Record<TradingStage, number> = { EARLY: 0, MID: 0, STABLE: 0 };
    for (const [phase, count] of Object.entries(phaseDistribution)) {
      const stage = PHASE_TO_STAGE[phase as TokenPhase];
      if (stage) tokenDistribution[stage] += count;
    }

    // Get stage metrics from trading systems
    const allSystems = await db.tradingSystem.findMany({
      include: {
        backtests: {
          where: { status: 'COMPLETED' },
          orderBy: { createdAt: 'desc' },
          take: 5,
        },
      },
    });

    const stageMetrics: Record<TradingStage, StageMetrics> = {
      EARLY: { stage: 'EARLY', tokensInStage: tokenDistribution.EARLY, activeSystems: 0, totalBacktests: 0, avgSharpe: 0, avgWinRate: 0, bestPnlPct: 0, totalTrades: 0, improvementTrend: 0 },
      MID: { stage: 'MID', tokensInStage: tokenDistribution.MID, activeSystems: 0, totalBacktests: 0, avgSharpe: 0, avgWinRate: 0, bestPnlPct: 0, totalTrades: 0, improvementTrend: 0 },
      STABLE: { stage: 'STABLE', tokensInStage: tokenDistribution.STABLE, activeSystems: 0, totalBacktests: 0, avgSharpe: 0, avgWinRate: 0, bestPnlPct: 0, totalTrades: 0, improvementTrend: 0 },
    };

    // Classify systems by stage
    for (const system of allSystems) {
      const stage = this.classifySystemStage(system);
      const metrics = stageMetrics[stage];

      metrics.activeSystems++;
      metrics.totalBacktests += system.totalBacktests;
      metrics.totalTrades += system.backtests.reduce((s, b) => s + b.totalTrades, 0);

      if (system.backtests.length > 0) {
        const avgSharpe = system.backtests.reduce((s, b) => s + b.sharpeRatio, 0) / system.backtests.length;
        const avgWR = system.backtests.reduce((s, b) => s + b.winRate, 0) / system.backtests.length;
        const bestPnl = Math.max(...system.backtests.map(b => b.totalPnlPct));

        metrics.avgSharpe = (metrics.avgSharpe + avgSharpe) / 2;
        metrics.avgWinRate = (metrics.avgWinRate + avgWR) / 2;
        metrics.bestPnlPct = Math.max(metrics.bestPnlPct, bestPnl);
      }
    }

    // Find top opportunities
    const topOpportunities = await this.findTopOpportunities(lifecycleStates);

    // Generate recommendations
    const recommendations = this.generateRecommendations(stageMetrics, tokenDistribution);

    return {
      stages: stageMetrics,
      strategies: Object.values(STAGE_STRATEGIES),
      tokenDistribution,
      phaseDistribution,
      topOpportunities,
      recommendations,
      generatedAt: new Date(),
    };
  }

  /**
   * Obtiene la estrategia para una etapa específica.
   */
  getStrategy(stage: TradingStage): StageStrategy {
    return STAGE_STRATEGIES[stage];
  }

  /**
   * Obtiene la estrategia para una fase específica.
   */
  getStrategyForPhase(phase: TokenPhase): StageStrategy {
    const stage = PHASE_TO_STAGE[phase];
    return STAGE_STRATEGIES[stage];
  }

  /**
   * Busca las mejores oportunidades actuales por etapa.
   */
  private async findTopOpportunities(
    lifecycleStates: Array<{ phase: string; phaseProbability: number; tokenAddress: string }>
  ): Promise<StrategyMatchResult[]> {
    const opportunities: StrategyMatchResult[] = [];

    for (const state of lifecycleStates.slice(0, 20)) { // Limit to 20
      try {
        const token = await db.token.findFirst({
          where: { address: state.tokenAddress },
          select: {
            address: true,
            symbol: true,
            chain: true,
            volume24h: true,
            liquidity: true,
            priceChange24h: true,
          },
        });

        if (!token || !token.symbol) continue;

        const phase = state.phase as TokenPhase;
        const stage = PHASE_TO_STAGE[phase];
        const strategy = STAGE_STRATEGIES[stage];

        // Calculate match score
        const matchScore = this.calculateMatchScore(token, strategy);

        opportunities.push({
          tokenAddress: token.address,
          symbol: token.symbol,
          chain: token.chain,
          stage,
          strategy,
          matchScore,
          matchReason: this.explainMatch(token, strategy, phase),
          phase,
          phaseConfidence: state.phaseProbability,
        });
      } catch {
        // Skip tokens that fail
      }
    }

    // Sort by match score
    opportunities.sort((a, b) => b.matchScore - a.matchScore);
    return opportunities.slice(0, 10);
  }

  /**
   * Calcula el score de match entre un token y una estrategia.
   */
  private calculateMatchScore(
    token: { volume24h: number; liquidity: number; priceChange24h: number },
    strategy: StageStrategy
  ): number {
    let score = 0;
    const params = strategy.parameters;

    // Volume match
    if (token.volume24h >= params.minVolume24h) score += 20;
    else score += (token.volume24h / params.minVolume24h) * 20;

    // Liquidity match
    if (params.minLiquidityUsd > 0 && token.liquidity >= params.minLiquidityUsd) score += 20;
    else if (params.minLiquidityUsd > 0) score += (token.liquidity / params.minLiquidityUsd) * 20;
    else score += 15; // No min liquidity requirement

    // Momentum alignment
    if (strategy.stage === 'EARLY' && Math.abs(token.priceChange24h) > 20) score += 30;
    else if (strategy.stage === 'MID' && token.priceChange24h > 5) score += 25;
    else if (strategy.stage === 'STABLE' && Math.abs(token.priceChange24h) < 10) score += 20;
    else score += 10;

    // Strategy-specific bonus
    score += Math.random() * 10; // Small random factor for variety

    return Math.min(100, Math.round(score));
  }

  /**
   * Explica por qué un token matchea una estrategia.
   */
  private explainMatch(
    token: { volume24h: number; liquidity: number; priceChange24h: number },
    strategy: StageStrategy,
    phase: TokenPhase
  ): string {
    const reasons: string[] = [];
    reasons.push(`Phase: ${phase}`);

    if (token.volume24h >= strategy.parameters.minVolume24h) {
      reasons.push('Volume meets threshold');
    }
    if (token.liquidity >= strategy.parameters.minLiquidityUsd) {
      reasons.push('Sufficient liquidity');
    }
    if (strategy.stage === 'EARLY' && Math.abs(token.priceChange24h) > 20) {
      reasons.push('High volatility suits early stage');
    }
    if (strategy.stage === 'MID' && token.priceChange24h > 5) {
      reasons.push('Positive momentum for growth');
    }
    if (strategy.stage === 'STABLE') {
      reasons.push('Established token profile');
    }

    return reasons.join(' | ');
  }

  /**
   * Clasifica un sistema de trading en una etapa.
   */
  private classifySystemStage(system: any): TradingStage {
    if (!system.phaseConfig) return 'MID';

    try {
      const phaseConfig = JSON.parse(system.phaseConfig);
      const phases = Object.keys(phaseConfig);

      let bestPhase = 'GROWTH';
      let bestWeight = 0;
      for (const phase of phases) {
        const config = phaseConfig[phase] as Record<string, unknown>;
        const weight = (config?.weight as number) ?? 0;
        const enabled = (config?.enabled as boolean) ?? true;
        if (enabled && weight > bestWeight) {
          bestWeight = weight;
          bestPhase = phase;
        }
      }

      return PHASE_TO_STAGE[bestPhase as TokenPhase] ?? 'MID';
    } catch {
      return 'MID';
    }
  }

  /**
   * Genera recomendaciones basadas en las métricas por etapa.
   */
  private generateRecommendations(
    stageMetrics: Record<TradingStage, StageMetrics>,
    tokenDistribution: Record<TradingStage, number>
  ): string[] {
    const recommendations: string[] = [];

    // Check each stage
    for (const [stage, metrics] of Object.entries(stageMetrics)) {
      if (metrics.tokensInStage > 0 && metrics.activeSystems === 0) {
        recommendations.push(
          `${STAGE_STRATEGIES[stage as TradingStage].icon} ${stage}: ${metrics.tokensInStage} tokens but no active trading systems. Create a system for this stage.`
        );
      }

      if (metrics.avgWinRate > 0 && metrics.avgWinRate < 0.4) {
        recommendations.push(
          `${STAGE_STRATEGIES[stage as TradingStage].icon} ${stage}: Low win rate (${(metrics.avgWinRate * 100).toFixed(1)}%). Consider refining parameters.`
        );
      }

      if (metrics.improvementTrend < 0) {
        recommendations.push(
          `${STAGE_STRATEGIES[stage as TradingStage].icon} ${stage}: Performance degrading. Run a backtest loop to identify issues.`
        );
      }
    }

    // General recommendations
    const totalTokens = Object.values(tokenDistribution).reduce((s, v) => s + v, 0);
    if (totalTokens === 0) {
      recommendations.push('No tokens with lifecycle data. Run market sync and lifecycle detection first.');
    }

    if (tokenDistribution.EARLY > tokenDistribution.MID + tokenDistribution.STABLE) {
      recommendations.push('Market appears to be in early-cycle phase with many new tokens. Focus on EARLY stage strategies with tight risk management.');
    }

    if (tokenDistribution.STABLE > tokenDistribution.EARLY + tokenDistribution.MID) {
      recommendations.push('Market dominated by established tokens. Focus on STABLE stage strategies with trend following and mean reversion.');
    }

    return recommendations;
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const phaseStrategyEngine = new PhaseStrategyEngine();
