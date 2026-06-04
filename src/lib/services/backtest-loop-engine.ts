/**
 * Backtest Loop Engine - CryptoQuant Terminal
 * Motor de Loops de Backtesting con Mejora Continua
 *
 * Este motor ejecuta loops automáticos de:
 *   1. BACKTEST → Ejecuta backtest en un sistema de trading
 *   2. ANALYZE → Analiza resultados por fase, identifica debilidades
 *   3. REFINE → Ajusta parámetros del sistema basándose en el análisis
 *   4. GENERATE → Genera sistemas sintéticos especializados para fases débiles
 *   5. RE-BACKTEST → Ejecuta backtest en los sistemas refinados/sintéticos
 *   6. COMPARE → Compara rendimiento del refinado vs original
 *   7. EVOLVE → Si mejora, adopta; si no, revierte y prueba otra estrategia
 *
 * El loop se ejecuta continuamente, mejorando los sistemas de trading
 * en cada iteración. Cada loop genera métricas que alimentan al siguiente.
 *
 * Estructura de fases del ciclo de vida de trading:
 *   EARLY (GENESIS + INCIPIENT) → Tokens nuevos, alta volatilidad, bots dominan
 *   MID (GROWTH + FOMO) → Tokens en crecimiento, smart money activo
 *   STABLE (DECLINE + LEGACY) → Tokens establecidos tipo BTC/altcoins grandes
 */

import { db } from '../db';
import { feedbackLoopEngine } from './feedback-loop-engine';
import { tokenLifecycleEngine, type TokenPhase } from './token-lifecycle-engine';

// ============================================================
// TYPES
// ============================================================

export type TradingStage = 'EARLY' | 'MID' | 'STABLE';

export type LoopStatus = 'IDLE' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'FAILED';

export interface LoopIteration {
  iterationNumber: number;
  status: LoopStatus;
  startedAt: Date;
  completedAt: Date | null;

  // Input
  systemId: string;
  systemName: string;
  stage: TradingStage;

  // Backtest results
  backtestId: string | null;
  originalSharpe: number;
  originalWinRate: number;
  originalPnlPct: number;

  // Analysis
  weakPhases: string[];
  strongPhases: string[];
  analysisSummary: string;

  // Refinement
  refinedSystemId: string | null;
  syntheticSystemIds: string[];
  refinementType: string;

  // Re-backtest results
  refinedSharpe: number;
  refinedWinRate: number;
  refinedPnlPct: number;

  // Comparison
  improvementPct: number;
  adopted: boolean;
  comparisonSummary: string;
}

export interface LoopReport {
  loopId: string;
  status: LoopStatus;
  startedAt: Date;
  completedAt: Date | null;
  totalIterations: number;
  iterations: LoopIteration[];
  summary: {
    systemsImproved: number;
    systemsDegraded: number;
    systemsUnchanged: number;
    totalSyntheticGenerated: number;
    bestImprovement: number;
    averageImprovement: number;
  };
  stageResults: Record<TradingStage, {
    iterations: number;
    improved: number;
    avgImprovement: number;
    bestSharpe: number;
  }>;
}

export interface LoopConfig {
  /** Máximo de iteraciones por loop (default: 10) */
  maxIterations: number;
  /** Mejora mínima para adoptar un refinamiento (default: 2%) */
  minImprovementPct: number;
  /** Si generar sistemas sintéticos automáticamente (default: true) */
  autoGenerateSynthetic: boolean;
  /** Si adoptar mejoras automáticamente (default: true) */
  autoAdopt: boolean;
  /** Intervalo entre loops en ms (default: 30 min) */
  loopIntervalMs: number;
}

// ============================================================
// STAGE MAPPING
// ============================================================

const PHASE_TO_STAGE: Record<TokenPhase, TradingStage> = {
  GENESIS: 'EARLY',
  INCIPIENT: 'EARLY',
  GROWTH: 'MID',
  FOMO: 'MID',
  DECLINE: 'STABLE',
  LEGACY: 'STABLE',
};

const STAGE_CONFIG: Record<TradingStage, {
  label: string;
  color: string;
  description: string;
  focusAreas: string[];
  riskProfile: string;
}> = {
  EARLY: {
    label: 'Early Stage',
    color: '#ef4444',
    description: 'Tokens nuevos con alta volatilidad y dominio de bots',
    focusAreas: ['Bot detection', 'Sniper tracking', 'Rug pull protection', 'Quick entries/exits'],
    riskProfile: 'ULTRA_HIGH',
  },
  MID: {
    label: 'Mid Stage',
    color: '#f59e0b',
    description: 'Tokens en crecimiento con smart money activo',
    focusAreas: ['Smart money following', 'Momentum riding', 'Volume analysis', 'Phase transition'],
    riskProfile: 'MODERATE',
  },
  STABLE: {
    label: 'Stable Stage',
    color: '#10b981',
    description: 'Tokens establecidos tipo BTC y altcoins grandes',
    focusAreas: ['Trend following', 'Mean reversion', 'Macro correlation', 'Long-term holds'],
    riskProfile: 'CONSERVATIVE',
  },
};

// ============================================================
// BACKTEST LOOP ENGINE CLASS
// ============================================================

class BacktestLoopEngine {
  private config: LoopConfig = {
    maxIterations: 10,
    minImprovementPct: 2,
    autoGenerateSynthetic: true,
    autoAdopt: true,
    loopIntervalMs: 30 * 60 * 1000,
  };
  private currentLoop: LoopReport | null = null;
  private isRunning = false;
  private loopTimer: ReturnType<typeof setInterval> | null = null;
  private loopCounter = 0;

  /**
   * Inicia el motor de loops de backtesting.
   */
  async start(config?: Partial<LoopConfig>): Promise<{ started: boolean; message: string }> {
    if (this.isRunning) {
      return { started: false, message: 'Loop engine is already running' };
    }

    if (config) {
      this.config = { ...this.config, ...config };
    }

    this.isRunning = true;

    // Run first loop after a short delay
    setTimeout(async () => {
      if (!this.isRunning) return;
      try {
        await this.runLoop();
      } catch (error) {
        console.error('[BacktestLoop] First loop error:', error);
      }
    }, 15000);

    // Schedule subsequent loops
    this.loopTimer = setInterval(async () => {
      if (!this.isRunning) return;
      try {
        await this.runLoop();
      } catch (error) {
        console.error('[BacktestLoop] Loop error:', error);
      }
    }, this.config.loopIntervalMs);

    return { started: true, message: `Backtest loop engine started with ${this.config.maxIterations} max iterations per loop` };
  }

  /**
   * Detiene el motor de loops.
   */
  stop(): { stopped: boolean; message: string } {
    if (this.loopTimer) {
      clearInterval(this.loopTimer);
      this.loopTimer = null;
    }
    this.isRunning = false;
    return { stopped: true, message: 'Backtest loop engine stopped' };
  }

  /**
   * Ejecuta un loop completo de backtesting-mejora.
   */
  async runLoop(): Promise<LoopReport> {
    this.loopCounter++;
    const loopId = `loop_${this.loopCounter}_${Date.now()}`;

    const report: LoopReport = {
      loopId,
      status: 'RUNNING',
      startedAt: new Date(),
      completedAt: null,
      totalIterations: 0,
      iterations: [],
      summary: {
        systemsImproved: 0,
        systemsDegraded: 0,
        systemsUnchanged: 0,
        totalSyntheticGenerated: 0,
        bestImprovement: 0,
        averageImprovement: 0,
      },
      stageResults: {
        EARLY: { iterations: 0, improved: 0, avgImprovement: 0, bestSharpe: 0 },
        MID: { iterations: 0, improved: 0, avgImprovement: 0, bestSharpe: 0 },
        STABLE: { iterations: 0, improved: 0, avgImprovement: 0, bestSharpe: 0 },
      },
    };

    this.currentLoop = report;

    try {
      // STEP 1: Get active trading systems to improve
      const activeSystems = await db.tradingSystem.findMany({
        where: {
          isActive: true,
          autoOptimize: true,
        },
        include: {
          backtests: {
            where: { status: 'COMPLETED' },
            orderBy: { createdAt: 'desc' },
            take: 3,
          },
        },
        take: this.config.maxIterations,
      });

      if (activeSystems.length === 0) {
        // Also try inactive systems that have been backtested
        const anySystems = await db.tradingSystem.findMany({
          where: { totalBacktests: { gt: 0 } },
          include: {
            backtests: {
              where: { status: 'COMPLETED' },
              orderBy: { createdAt: 'desc' },
              take: 3,
            },
          },
          take: this.config.maxIterations,
        });

        if (anySystems.length === 0) {
          report.status = 'COMPLETED';
          report.completedAt = new Date();
          // No systems available — skip gracefully
          return report;
        }
      }

      const systemsToProcess = activeSystems.length > 0 ? activeSystems : (await db.tradingSystem.findMany({
        where: { totalBacktests: { gt: 0 } },
        include: {
          backtests: {
            where: { status: 'COMPLETED' },
            orderBy: { createdAt: 'desc' },
            take: 3,
          },
        },
        take: this.config.maxIterations,
      }));

      // STEP 2: Run iteration for each system
      const totalImprovements: number[] = [];
      const stageImprovements: Record<TradingStage, number[]> = { EARLY: [], MID: [], STABLE: [] };

      for (const system of systemsToProcess) {
        try {
          // Determine the trading stage from system's phaseConfig
          const stage = this.determineSystemStage(system.id, system.phaseConfig);
          const iteration = await this.runIteration(system, stage);

          report.iterations.push(iteration);
          report.totalIterations++;

          // Track improvements
          if (iteration.improvementPct > 0) {
            report.summary.systemsImproved++;
            totalImprovements.push(iteration.improvementPct);
            stageImprovements[stage].push(iteration.improvementPct);
          } else if (iteration.improvementPct < 0) {
            report.summary.systemsDegraded++;
          } else {
            report.summary.systemsUnchanged++;
          }

          report.summary.totalSyntheticGenerated += iteration.syntheticSystemIds.length;

          // Track by stage
          report.stageResults[stage].iterations++;
          if (iteration.improvementPct > 0) {
            report.stageResults[stage].improved++;
          }
          if (iteration.refinedSharpe > report.stageResults[stage].bestSharpe) {
            report.stageResults[stage].bestSharpe = iteration.refinedSharpe;
          }
        } catch (error) {
          console.error(`[BacktestLoop] Iteration failed for system ${system.name}:`, error);
        }
      }

      // Calculate summary metrics
      if (totalImprovements.length > 0) {
        report.summary.bestImprovement = Math.max(...totalImprovements);
        report.summary.averageImprovement = totalImprovements.reduce((s, v) => s + v, 0) / totalImprovements.length;
      }

      for (const stage of ['EARLY', 'MID', 'STABLE'] as TradingStage[]) {
        const imps = stageImprovements[stage];
        if (imps.length > 0) {
          report.stageResults[stage].avgImprovement = imps.reduce((s, v) => s + v, 0) / imps.length;
        }
      }

      report.status = 'COMPLETED';
      report.completedAt = new Date();
    } catch (error) {
      report.status = 'FAILED';
      report.completedAt = new Date();
      console.error('[BacktestLoop] Loop failed:', error);
    }

    this.currentLoop = report;
    return report;
  }

  /**
   * Ejecuta una iteración completa del loop para un sistema.
   */
  private async runIteration(
    system: any,
    stage: TradingStage
  ): Promise<LoopIteration> {
    const iteration: LoopIteration = {
      iterationNumber: this.loopCounter,
      status: 'RUNNING',
      startedAt: new Date(),
      completedAt: null,
      systemId: system.id,
      systemName: system.name,
      stage,
      backtestId: null,
      originalSharpe: system.bestSharpe || 0,
      originalWinRate: system.bestWinRate || 0,
      originalPnlPct: system.bestPnlPct || 0,
      weakPhases: [],
      strongPhases: [],
      analysisSummary: '',
      refinedSystemId: null,
      syntheticSystemIds: [],
      refinementType: '',
      refinedSharpe: 0,
      refinedWinRate: 0,
      refinedPnlPct: 0,
      improvementPct: 0,
      adopted: false,
      comparisonSummary: '',
    };

    try {
      // STEP 1: Get latest backtest results for analysis
      const latestBacktest = system.backtests?.[0];
      if (!latestBacktest) {
        iteration.status = 'COMPLETED';
        iteration.analysisSummary = 'No backtest data available for this system';
        iteration.completedAt = new Date();
        return iteration;
      }

      iteration.backtestId = latestBacktest.id;

      // STEP 2: Analyze backtest results
      let feedbackResult: Awaited<ReturnType<typeof feedbackLoopEngine.processBacktestFeedback>> | null = null;
      try {
        feedbackResult = await feedbackLoopEngine.processBacktestFeedback(latestBacktest.id);
        iteration.weakPhases = feedbackResult.weakPhases.map(p => p.phase);
        iteration.strongPhases = feedbackResult.strongPhases.map(p => p.phase);
        iteration.analysisSummary = feedbackResult.suggestions.join('; ');
      } catch {
        iteration.analysisSummary = 'Feedback analysis skipped (insufficient data)';
      }

      // STEP 3: Refine the system
      let refinedSystemId: string | null = null;
      try {
        const refinement = await feedbackLoopEngine.refineSystem(system.id);
        refinedSystemId = refinement.newSystemId;
        iteration.refinedSystemId = refinedSystemId;
        iteration.refinementType = refinement.evolutionType;
      } catch {
        iteration.refinementType = 'refinement_failed';
      }

      // STEP 4: Generate synthetic systems for weak phases
      if (this.config.autoGenerateSynthetic && iteration.weakPhases.length > 0) {
        for (const weakPhase of iteration.weakPhases.slice(0, 2)) { // Max 2 synthetic per iteration
          try {
            const synthetic = await feedbackLoopEngine.generateSyntheticSystem(
              system.id,
              weakPhase as TokenPhase
            );
            iteration.syntheticSystemIds.push(synthetic.newSystemId);
          } catch {
            // Skip if generation fails
          }
        }
      }

      // STEP 5: Compare refined system vs original
      if (refinedSystemId) {
        const refinedSystem = await db.tradingSystem.findUnique({
          where: { id: refinedSystemId },
          include: {
            backtests: {
              where: { status: 'COMPLETED' },
              orderBy: { createdAt: 'desc' },
              take: 1,
            },
          },
        });

        if (refinedSystem && refinedSystem.backtests.length > 0) {
          iteration.refinedSharpe = refinedSystem.backtests[0].sharpeRatio;
          iteration.refinedWinRate = refinedSystem.backtests[0].winRate;
          iteration.refinedPnlPct = refinedSystem.backtests[0].totalPnlPct;

          // Calculate improvement
          const originalScore = iteration.originalSharpe;
          const refinedScore = iteration.refinedSharpe;
          iteration.improvementPct = originalScore !== 0
            ? ((refinedScore - originalScore) / Math.abs(originalScore)) * 100
            : 0;

          iteration.comparisonSummary = `Sharpe: ${originalScore.toFixed(2)} → ${refinedScore.toFixed(2)} (${iteration.improvementPct >= 0 ? '+' : ''}${iteration.improvementPct.toFixed(1)}%)`;

          // Adopt if improvement is significant
          if (iteration.improvementPct >= this.config.minImprovementPct && this.config.autoAdopt) {
            await db.tradingSystem.update({
              where: { id: system.id },
              data: { isActive: false },
            });
            await db.tradingSystem.update({
              where: { id: refinedSystemId },
              data: { isActive: true },
            });
            iteration.adopted = true;
          }
        } else {
          // No backtest for refined system yet - use original metrics
          iteration.refinedSharpe = iteration.originalSharpe;
          iteration.refinedWinRate = iteration.originalWinRate;
          iteration.refinedPnlPct = iteration.originalPnlPct;
          iteration.comparisonSummary = 'Refined system awaiting backtest validation';
        }
      }

      iteration.status = 'COMPLETED';
    } catch (error) {
      iteration.status = 'FAILED';
    }

    iteration.completedAt = new Date();
    return iteration;
  }

  /**
   * Determina la etapa de trading de un sistema basándose en su phaseConfig.
   */
  private determineSystemStage(systemId: string, phaseConfigJson: string | null): TradingStage {
    if (!phaseConfigJson) return 'MID'; // Default

    try {
      const phaseConfig = JSON.parse(phaseConfigJson);
      const phases = Object.keys(phaseConfig);

      // Find the phase with highest weight
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
   * Retorna el reporte del loop actual.
   */
  getCurrentLoop(): LoopReport | null {
    return this.currentLoop;
  }

  /**
   * Retorna el estado del motor.
   */
  getStatus(): {
    isRunning: boolean;
    loopsCompleted: number;
    currentLoop: LoopReport | null;
    config: LoopConfig;
  } {
    return {
      isRunning: this.isRunning,
      loopsCompleted: this.loopCounter,
      currentLoop: this.currentLoop,
      config: this.config,
    };
  }

  /**
   * Retorna el historial de loops completados.
   */
  async getLoopHistory(limit: number = 20): Promise<Array<{
    loopId: string;
    completedAt: Date;
    totalIterations: number;
    systemsImproved: number;
    bestImprovement: number;
  }>> {
    // Get evolution history as proxy for loop history
    const evolutions = await db.systemEvolution.findMany({
      orderBy: { createdAt: 'desc' },
      take: limit,
      select: {
        id: true,
        evolutionType: true,
        improvementPct: true,
        createdAt: true,
      },
    });

    return evolutions.map(e => ({
      loopId: e.id,
      completedAt: e.createdAt,
      totalIterations: 1,
      systemsImproved: e.improvementPct > 0 ? 1 : 0,
      bestImprovement: e.improvementPct,
    }));
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const backtestLoopEngine = new BacktestLoopEngine();

// Export stage helpers for UI
export { STAGE_CONFIG, PHASE_TO_STAGE };
