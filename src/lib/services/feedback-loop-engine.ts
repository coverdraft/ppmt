/**
 * Continuous Feedback Loop Engine - CryptoQuant Terminal
 * Motor de Retroalimentación Continua para Cierre del Ciclo de Aprendizaje
 *
 * Este motor cierra el ciclo de aprendizaje: valida predicciones pasadas contra
 * resultados reales, retroalimenta los resultados de backtesting al cerebro,
 * y refina automáticamente los sistemas de trading.
 *
 * Arquitectura:
 *   1. Validación de señales → Compara predicciones con resultados reales (Brier score)
 *   2. Procesamiento de backtests → Extrae métricas, identifica fases fuertes/débiles
 *   3. Refinamiento de sistemas → Ajusta parámetros de TradingSystem según rendimiento
 *   4. Generación sintética → Crea sistemas especializados por fase desde un padre
 *   5. Análisis comparativo → Rankea sistemas y genera recomendaciones
 *
 * Dependencias:
 *   - Prisma via db (NO prisma directa)
 *   - TokenPhase desde token-lifecycle-engine
 *   - TraderArchetype, TraderAction desde behavioral-model-engine
 */

import { db } from '../db';
import { TokenPhase, TraderArchetype } from './token-lifecycle-engine';
import { TraderAction } from './behavioral-model-engine';

// ============================================================
// TYPES & INTERFACES
// ============================================================

/** Reporte completo de validación de señales predictivas */
export interface ValidationReport {
  totalValidated: number;
  correctCount: number;
  overallAccuracy: number;
  brierScore: number;
  bySignalType: Record<string, { total: number; correct: number; accuracy: number }>;
  byPhase: Record<string, { total: number; correct: number; accuracy: number }>;
  timestamp: Date;
}

/** Resultado del procesamiento de retroalimentación de backtesting */
export interface BacktestFeedbackResult {
  backtestRunId: string;
  systemId: string;
  overallSharpe: number;
  overallWinRate: number;
  weakPhases: { phase: TokenPhase; winRate: number; sharpe: number }[];
  strongPhases: { phase: TokenPhase; winRate: number; sharpe: number }[];
  suggestions: string[];
  metricsStored: number;
}

/** Resultado del refinamiento de un sistema de trading */
export interface RefinementResult {
  originalSystemId: string;
  newSystemId: string;
  evolutionType: string;
  changes: Record<string, { from: unknown; to: unknown }>;
  expectedImprovement: string;
}

/** Resultado de la generación de un sistema sintético especializado */
export interface SyntheticSystemResult {
  parentSystemId: string;
  newSystemId: string;
  targetPhase: TokenPhase;
  name: string;
  adjustedParameters: Record<string, unknown>;
}

/** Reporte comparativo entre sistemas de trading */
export interface ComparativeReport {
  comparisons: {
    modelA: string;
    modelB: string;
    dimension: string;
    winner: string;
    metricsA: Record<string, number>;
    metricsB: Record<string, number>;
  }[];
  rankings: { systemId: string; name: string; overallScore: number }[];
  recommendations: string[];
}

/** Ajustes de riesgo específicos por fase del ciclo de vida */
interface RiskAdjustment {
  stopLossMultiplier: number;
  positionSizeMultiplier: number;
  confidenceThreshold: number;
  takeProfitMultiplier: number;
}

// ============================================================
// PHASE-SPECIFIC RISK PARAMETER ADJUSTMENTS
// ============================================================

/**
 * Ajustes de riesgo predeterminados para cada fase del ciclo de vida.
 *
 * GENESIS   → Stop-loss muy ajustado, tamaño mínimo, alta confianza requerida, TP amplio
 * INCIPIENT → Conservador, tamaño reducido, confianza alta, TP moderado
 * GROWTH    → Stop-loss relajado, tamaño decente, confianza moderada, TP conservador
 * FOMO      → Stop-loss ajustado, tamaño reducido, alta confianza, TP corto
 * DECLINE   → Stop-loss ligeramente ajustado, tamaño mínimo, alta confianza, TP muy corto
 * LEGACY    → Parámetros neutros, tamaño moderado, confianza estándar, TP normal
 */
const PHASE_RISK_ADJUSTMENTS: Record<TokenPhase, RiskAdjustment> = {
  GENESIS: {
    stopLossMultiplier: 0.6,
    positionSizeMultiplier: 0.3,
    confidenceThreshold: 0.85,
    takeProfitMultiplier: 2.0,
  },
  INCIPIENT: {
    stopLossMultiplier: 0.7,
    positionSizeMultiplier: 0.5,
    confidenceThreshold: 0.75,
    takeProfitMultiplier: 1.5,
  },
  GROWTH: {
    stopLossMultiplier: 1.1,
    positionSizeMultiplier: 0.8,
    confidenceThreshold: 0.65,
    takeProfitMultiplier: 1.3,
  },
  FOMO: {
    stopLossMultiplier: 0.8,
    positionSizeMultiplier: 0.4,
    confidenceThreshold: 0.8,
    takeProfitMultiplier: 0.8,
  },
  DECLINE: {
    stopLossMultiplier: 0.9,
    positionSizeMultiplier: 0.2,
    confidenceThreshold: 0.85,
    takeProfitMultiplier: 0.6,
  },
  LEGACY: {
    stopLossMultiplier: 1.0,
    positionSizeMultiplier: 0.7,
    confidenceThreshold: 0.6,
    takeProfitMultiplier: 1.0,
  },
};

/** Umbrales para clasificar fases como débiles o fuertes */
const WEAK_PHASE_WIN_RATE = 0.4;
const WEAK_PHASE_SHARPE = 0.5;
const STRONG_PHASE_WIN_RATE = 0.6;
const STRONG_PHASE_SHARPE = 1.0;

/** Ajustes de refinamiento por fase para sistemas con rendimiento débil */
const REFINEMENT_ADJUSTMENTS: Record<TokenPhase, {
  stopLossFactor: number;
  positionSizeFactor: number;
  confidenceFactor: number;
  description: string;
}> = {
  GENESIS: {
    stopLossFactor: 1.0,     // No se ajusta stop-loss (ya es muy ajustado)
    positionSizeFactor: 0.85, // Reducir posición ligeramente
    confidenceFactor: 1.15,   // Aumentar umbral de confianza +15%
    description: 'Higher confidence thresholds in GENESIS phase (+15%)',
  },
  INCIPIENT: {
    stopLossFactor: 0.95,
    positionSizeFactor: 0.9,
    confidenceFactor: 1.1,
    description: 'Slightly tighter risk in INCIPIENT phase',
  },
  GROWTH: {
    stopLossFactor: 1.1,     // Wider stop-loss in GROWTH (+10%)
    positionSizeFactor: 1.0,
    confidenceFactor: 1.0,
    description: 'Wider stop-loss in GROWTH phase (+10%)',
  },
  FOMO: {
    stopLossFactor: 0.8,     // Tighter stop-loss in FOMO (-20%)
    positionSizeFactor: 0.9,
    confidenceFactor: 1.0,
    description: 'Tighter stop-loss in FOMO phase (-20%)',
  },
  DECLINE: {
    stopLossFactor: 1.0,
    positionSizeFactor: 0.7,  // Lower position size in DECLINE (-30%)
    confidenceFactor: 1.1,
    description: 'Lower position size in DECLINE phase (-30%)',
  },
  LEGACY: {
    stopLossFactor: 1.0,
    positionSizeFactor: 0.95,
    confidenceFactor: 1.0,
    description: 'Minor position adjustment in LEGACY phase',
  },
};

// ============================================================
// HELPER FUNCTIONS
// ============================================================

/**
 * Calcula el Brier score para una predicción binaria.
 *
 * Brier score = (predicted_prob - outcome)^2
 * donde outcome es 1 si la predicción fue correcta, 0 si no.
 *
 * Un Brier score más bajo indica mejor calibración.
 * Rango: [0, 1] donde 0 = perfecto, 1 = completamente errado.
 */
function computeBrierScore(predictedConfidence: number, wasCorrect: boolean): number {
  const outcome = wasCorrect ? 1 : 0;
  return (predictedConfidence - outcome) ** 2;
}

/**
 * Parsea un campo JSON de manera segura, retornando un fallback si falla.
 */
function safeJsonParse<T>(jsonString: string | null | undefined, fallback: T): T {
  if (!jsonString) return fallback;
  try {
    return JSON.parse(jsonString) as T;
  } catch {
    return fallback;
  }
}

/**
 * Determina la dirección del movimiento de precio entre dos valores.
 * Retorna 'UP' si el precio subió, 'DOWN' si bajó, 'FLAT' si sin cambio.
 */
function getPriceDirection(startPrice: number, endPrice: number): 'UP' | 'DOWN' | 'FLAT' {
  const change = endPrice - startPrice;
  const changePct = startPrice > 0 ? Math.abs(change / startPrice) : 0;
  // Ignorar movimientos menores al 0.5%
  if (changePct < 0.005) return 'FLAT';
  return change > 0 ? 'UP' : 'DOWN';
}

/**
 * Genera sugerencias de mejora basadas en las fases débiles identificadas.
 */
function generatePhaseSuggestions(weakPhases: { phase: TokenPhase; winRate: number; sharpe: number }[]): string[] {
  const suggestions: string[] = [];

  for (const weak of weakPhases) {
    const adj = REFINEMENT_ADJUSTMENTS[weak.phase];

    if (weak.winRate < 0.25) {
      suggestions.push(
        `CRITICAL: ${weak.phase} phase win rate is ${(weak.winRate * 100).toFixed(1)}%. ` +
        `Consider disabling trading in this phase or ${adj.description.toLowerCase()}.`
      );
    } else if (weak.winRate < WEAK_PHASE_WIN_RATE) {
      suggestions.push(
        `WARNING: ${weak.phase} phase underperforming (win rate: ${(weak.winRate * 100).toFixed(1)}%, ` +
        `Sharpe: ${weak.sharpe.toFixed(2)}). ${adj.description}.`
      );
    }

    if (weak.sharpe < 0) {
      suggestions.push(
        `${weak.phase} phase has negative Sharpe ratio (${weak.sharpe.toFixed(2)}). ` +
        `Risk-adjusted returns are worse than risk-free. Consider skipping this phase.`
      );
    }
  }

  return suggestions;
}

// ============================================================
// FEEDBACK LOOP ENGINE CLASS
// ============================================================

class FeedbackLoopEngine {
  // ============================================================
  // 1. VALIDATE SIGNALS
  // ============================================================

  /**
   * Valida señales predictivas pasadas contra resultados reales.
   *
   * Busca todas las señales PredictiveSignal donde `wasCorrect` es null
   * y `validUntil` ya pasó (fuera de la ventana de predicción). Para cada
   * señal, compara la dirección predicha con el movimiento real del precio,
   * calcula el Brier score, y almacena las métricas en FeedbackMetrics.
   *
   * @returns Resumen de validación con precisión global, por tipo de señal y por fase
   */
  async validateSignals(): Promise<ValidationReport> {
    const now = new Date();

    // Encontrar todas las señales vencidas sin validar
    const pendingSignals = await db.predictiveSignal.findMany({
      where: {
        wasCorrect: null,
        validUntil: { lt: now },
      },
    });

    if (pendingSignals.length === 0) {
      return {
        totalValidated: 0,
        correctCount: 0,
        overallAccuracy: 0,
        brierScore: 0,
        bySignalType: {},
        byPhase: {},
        timestamp: now,
      };
    }

    // Inicializar acumuladores
    let totalCorrect = 0;
    let totalBrier = 0;
    const bySignalType: ValidationReport['bySignalType'] = {};
    const byPhase: ValidationReport['byPhase'] = {};

    // Validar cada señal
    const updatePromises: Promise<unknown>[] = [];

    for (const signal of pendingSignals) {
      const validation = await this.validateSingleSignal(signal);

      // Acumular contadores globales
      if (validation.wasCorrect) totalCorrect++;
      totalBrier += validation.brierScore;

      // Acumular por tipo de señal
      const signalType = signal.signalType;
      if (!bySignalType[signalType]) {
        bySignalType[signalType] = { total: 0, correct: 0, accuracy: 0 };
      }
      bySignalType[signalType].total++;
      if (validation.wasCorrect) bySignalType[signalType].correct++;

      // Acumular por fase (extraer del contexto del prediction JSON)
      const prediction = safeJsonParse<Record<string, unknown>>(signal.prediction, {});
      const phase = (prediction.phase as string) ?? 'UNKNOWN';
      if (!byPhase[phase]) {
        byPhase[phase] = { total: 0, correct: 0, accuracy: 0 };
      }
      byPhase[phase].total++;
      if (validation.wasCorrect) byPhase[phase].correct++;

      // Actualizar la señal en la BD
      updatePromises.push(
        db.predictiveSignal.update({
          where: { id: signal.id },
          data: {
            wasCorrect: validation.wasCorrect,
            actualOutcome: JSON.stringify(validation.actualOutcome),
          },
        })
      );

      // Almacenar métricas de feedback
      updatePromises.push(
        db.feedbackMetrics.create({
          data: {
            sourceType: 'signal',
            sourceId: signal.id,
            metricName: 'brier_score',
            metricValue: validation.brierScore,
            context: JSON.stringify({
              signalType: signal.signalType,
              phase,
              confidence: signal.confidence,
              wasCorrect: validation.wasCorrect,
            }),
            period: '24h',
            measuredAt: now,
          },
        })
      );
    }

    // Ejecutar todas las actualizaciones y métricas en paralelo
    await Promise.allSettled(updatePromises);

    // Calcular precisiones finales
    const overallAccuracy = pendingSignals.length > 0 ? totalCorrect / pendingSignals.length : 0;
    const overallBrier = pendingSignals.length > 0 ? totalBrier / pendingSignals.length : 0;

    for (const key of Object.keys(bySignalType)) {
      const entry = bySignalType[key];
      entry.accuracy = entry.total > 0 ? entry.correct / entry.total : 0;
    }

    for (const key of Object.keys(byPhase)) {
      const entry = byPhase[key];
      entry.accuracy = entry.total > 0 ? entry.correct / entry.total : 0;
    }

    return {
      totalValidated: pendingSignals.length,
      correctCount: totalCorrect,
      overallAccuracy,
      brierScore: overallBrier,
      bySignalType,
      byPhase,
      timestamp: now,
    };
  }

  // ============================================================
  // 2. PROCESS BACKTEST FEEDBACK
  // ============================================================

  /**
   * Procesa los resultados de un backtest y genera retroalimentación accionable.
   *
   * Carga el BacktestRun con sus operaciones, extrae métricas por fase,
   * identifica fases débiles y fuertes, y almacena métricas comprehensivas
   * en FeedbackMetrics para futuras decisiones de refinamiento.
   *
   * @param backtestRunId - ID del BacktestRun a procesar
   * @returns Retroalimentación estructurada con sugerencias de mejora
   */
  async processBacktestFeedback(backtestRunId: string): Promise<BacktestFeedbackResult> {
    // Cargar el backtest con sus operaciones
    const backtest = await db.backtestRun.findUnique({
      where: { id: backtestRunId },
      include: { operations: true },
    });

    if (!backtest) {
      throw new Error(`BacktestRun not found: ${backtestRunId}`);
    }

    // Métricas generales del backtest
    const overallSharpe = backtest.sharpeRatio;
    const overallWinRate = backtest.winRate;

    // Agrupar operaciones por fase para análisis granular
    const phaseOps: Record<string, typeof backtest.operations> = {};
    for (const op of backtest.operations) {
      const phase = op.tokenPhase as TokenPhase;
      if (!phaseOps[phase]) phaseOps[phase] = [];
      phaseOps[phase].push(op);
    }

    // Calcular métricas por fase
    const weakPhases: BacktestFeedbackResult['weakPhases'] = [];
    const strongPhases: BacktestFeedbackResult['strongPhases'] = [];

    for (const [phase, ops] of Object.entries(phaseOps)) {
      if (ops.length === 0) continue;

      // Win rate por fase
      const wins = ops.filter(op => (op.pnlUsd ?? 0) > 0).length;
      const winRate = wins / ops.length;

      // Sharpe simplificado por fase
      const returns = ops.map(op => op.pnlPct ?? 0);
      const avgReturn = returns.reduce((s, r) => s + r, 0) / returns.length;
      const variance = returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / returns.length;
      const stdDev = Math.sqrt(variance);
      const sharpe = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;

      const phaseMetrics = {
        phase: phase as TokenPhase,
        winRate,
        sharpe,
      };

      // Clasificar como débil o fuerte
      if (winRate < WEAK_PHASE_WIN_RATE || sharpe < WEAK_PHASE_SHARPE) {
        weakPhases.push(phaseMetrics);
      } else if (winRate > STRONG_PHASE_WIN_RATE && sharpe > STRONG_PHASE_SHARPE) {
        strongPhases.push(phaseMetrics);
      }
    }

    // Generar sugerencias basadas en las fases débiles
    const suggestions = generatePhaseSuggestions(weakPhases);

    // Agregar sugerencias generales
    if (overallSharpe < 0.5) {
      suggestions.push(
        `Overall Sharpe ratio is ${overallSharpe.toFixed(2)}. Consider reducing market exposure ` +
        `or increasing selectivity of entry signals.`
      );
    }
    if (overallWinRate < 0.4) {
      suggestions.push(
        `Overall win rate is ${(overallWinRate * 100).toFixed(1)}%. Review entry criteria ` +
        `and consider tightening signal confirmation requirements.`
      );
    }
    if (strongPhases.length > 0) {
      const phaseNames = strongPhases.map(p => p.phase).join(', ');
      suggestions.push(
        `Strong phases detected: ${phaseNames}. Consider specializing the system ` +
        `for these phases or increasing position sizes in them.`
      );
    }

    // Almacenar métricas comprehensivas en FeedbackMetrics
    const now = new Date();
    const metricsToStore: Array<{
      sourceType: string;
      sourceId: string;
      metricName: string;
      metricValue: number;
      context: string;
    }> = [];

    // Métrica global: Sharpe
    metricsToStore.push({
      sourceType: 'backtest',
      sourceId: backtestRunId,
      metricName: 'sharpe',
      metricValue: overallSharpe,
      context: JSON.stringify({ systemId: backtest.systemId }),
    });

    // Métrica global: Win rate
    metricsToStore.push({
      sourceType: 'backtest',
      sourceId: backtestRunId,
      metricName: 'win_rate',
      metricValue: overallWinRate,
      context: JSON.stringify({ systemId: backtest.systemId }),
    });

    // Métrica global: Sortino (si está disponible)
    if (backtest.sortinoRatio !== null && backtest.sortinoRatio !== undefined) {
      metricsToStore.push({
        sourceType: 'backtest',
        sourceId: backtestRunId,
        metricName: 'sortino',
        metricValue: backtest.sortinoRatio,
        context: JSON.stringify({ systemId: backtest.systemId }),
      });
    }

    // Métricas por fase
    for (const [phase, ops] of Object.entries(phaseOps)) {
      if (ops.length === 0) continue;
      const wins = ops.filter(op => (op.pnlUsd ?? 0) > 0).length;
      const winRate = wins / ops.length;
      const returns = ops.map(op => op.pnlPct ?? 0);
      const avgReturn = returns.reduce((s, r) => s + r, 0) / returns.length;
      const variance = returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / returns.length;
      const stdDev = Math.sqrt(variance);
      const sharpe = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;

      metricsToStore.push({
        sourceType: 'backtest',
        sourceId: backtestRunId,
        metricName: 'phase_win_rate',
        metricValue: winRate,
        context: JSON.stringify({ systemId: backtest.systemId, phase }),
      });

      metricsToStore.push({
        sourceType: 'backtest',
        sourceId: backtestRunId,
        metricName: 'phase_sharpe',
        metricValue: sharpe,
        context: JSON.stringify({ systemId: backtest.systemId, phase }),
      });

      // Max drawdown por fase
      const maxDd = this.computeMaxDrawdown(ops.map(op => op.pnlPct ?? 0));
      metricsToStore.push({
        sourceType: 'backtest',
        sourceId: backtestRunId,
        metricName: 'phase_max_drawdown',
        metricValue: maxDd,
        context: JSON.stringify({ systemId: backtest.systemId, phase }),
      });
    }

    // Persistir todas las métricas en paralelo
    const storePromises = metricsToStore.map(m =>
      db.feedbackMetrics.create({
        data: {
          ...m,
          period: '24h',
          measuredAt: now,
        },
      })
    );

    await Promise.allSettled(storePromises);

    return {
      backtestRunId,
      systemId: backtest.systemId,
      overallSharpe,
      overallWinRate,
      weakPhases,
      strongPhases,
      suggestions,
      metricsStored: metricsToStore.length,
    };
  }

  // ============================================================
  // 3. REFINE SYSTEM
  // ============================================================

  /**
   * Refina un sistema de trading ajustando parámetros según su rendimiento.
   *
   * Analiza el rendimiento por fase usando datos de BacktestOperation.
   * Si el sistema tiene fases débiles, ajusta los parámetros según reglas
   * predefinidas (stop-loss más ajustado en FOMO, más amplio en GROWTH, etc.).
   * Crea un nuevo TradingSystem derivado con los parámetros ajustados y
   * registra la evolución en SystemEvolution.
   *
   * @param systemId - ID del TradingSystem a refinar
   * @returns Detalles del refinamiento incluyendo el nuevo sistema creado
   */
  async refineSystem(systemId: string): Promise<RefinementResult> {
    // Cargar el sistema y sus backtests recientes
    const system = await db.tradingSystem.findUnique({
      where: { id: systemId },
      include: {
        backtests: {
          where: { status: 'COMPLETED' },
          orderBy: { createdAt: 'desc' },
          take: 5,
        },
      },
    });

    if (!system) {
      throw new Error(`TradingSystem not found: ${systemId}`);
    }

    // Cargar operaciones de backtests recientes para análisis por fase
    const recentBacktestIds = system.backtests.map(b => b.id);
    const operations = recentBacktestIds.length > 0
      ? await db.backtestOperation.findMany({
          where: {
            systemId,
            backtestId: { in: recentBacktestIds },
          },
        })
      : [];

    // Analizar rendimiento por fase
    const phasePerformance = this.analyzePhasePerformance(operations);

    // Determinar qué parámetros ajustar basándose en las fases débiles
    const changes: RefinementResult['changes'] = {};
    let hasWeakPhases = false;

    // Parámetros base del sistema actual
    const currentStopLoss = system.stopLossPct;
    const currentPositionPct = system.maxPositionPct;
    const currentPhaseConfig = safeJsonParse<Record<string, unknown>>(system.phaseConfig, {});
    const currentEntrySignal = safeJsonParse<Record<string, unknown>>(system.entrySignal, {});

    // Aplicar ajustes por cada fase débil
    const adjustedStopLoss: Record<string, number> = {};
    const adjustedPositionSize: Record<string, number> = {};
    const adjustedConfidence: Record<string, number> = {};

    for (const [phase, perf] of Object.entries(phasePerformance)) {
      const phaseKey = phase as TokenPhase;
      const adj = REFINEMENT_ADJUSTMENTS[phaseKey];

      if (perf.winRate < WEAK_PHASE_WIN_RATE || perf.sharpe < WEAK_PHASE_SHARPE) {
        hasWeakPhases = true;

        // Aplicar ajustes de refinamiento
        const phaseStopLoss = currentStopLoss * adj.stopLossFactor;
        const phasePositionSize = currentPositionPct * adj.positionSizeFactor;
        const currentConfidence = (currentEntrySignal.confidenceThreshold as number) ?? 0.7;
        const phaseConfidence = currentConfidence * adj.confidenceFactor;

        adjustedStopLoss[phase] = Math.round(phaseStopLoss * 100) / 100;
        adjustedPositionSize[phase] = Math.round(phasePositionSize * 100) / 100;
        adjustedConfidence[phase] = Math.round(phaseConfidence * 100) / 100;

        changes[`stopLossPct_${phase}`] = {
          from: currentStopLoss,
          to: adjustedStopLoss[phase],
        };
        changes[`maxPositionPct_${phase}`] = {
          from: currentPositionPct,
          to: adjustedPositionSize[phase],
        };
        changes[`confidenceThreshold_${phase}`] = {
          from: currentConfidence,
          to: adjustedConfidence[phase],
        };
      }
    }

    // Si no hay fases débiles, crear un refinamiento menor (optimización general)
    if (!hasWeakPhases) {
      // Ajuste menor: aumentar ligeramente el tamaño de posición ya que el sistema es robusto
      const newMaxPosition = Math.min(10, currentPositionPct * 1.05);
      changes['maxPositionPct'] = {
        from: currentPositionPct,
        to: Math.round(newMaxPosition * 100) / 100,
      };
    }

    // Construir la nueva configuración de fases con ajustes
    const newPhaseConfig = { ...currentPhaseConfig };
    for (const [phase, sl] of Object.entries(adjustedStopLoss)) {
      if (!newPhaseConfig[phase]) newPhaseConfig[phase] = {};
      (newPhaseConfig[phase] as Record<string, unknown>).stopLossPct = sl;
    }
    for (const [phase, ps] of Object.entries(adjustedPositionSize)) {
      if (!newPhaseConfig[phase]) newPhaseConfig[phase] = {};
      (newPhaseConfig[phase] as Record<string, unknown>).maxPositionPct = ps;
    }
    for (const [phase, cf] of Object.entries(adjustedConfidence)) {
      if (!newPhaseConfig[phase]) newPhaseConfig[phase] = {};
      (newPhaseConfig[phase] as Record<string, unknown>).confidenceThreshold = cf;
    }

    // Construir la nueva configuración de entrada con umbrales ajustados
    const newEntrySignal = { ...currentEntrySignal };
    if (Object.keys(adjustedConfidence).length > 0) {
      newEntrySignal.phaseConfidenceThresholds = adjustedConfidence;
    }

    // Calcular nuevos parámetros globales (promedio ponderado de ajustes por fase)
    const allAdjustedSl = Object.values(adjustedStopLoss);
    const newStopLossPct = allAdjustedSl.length > 0
      ? allAdjustedSl.reduce((s, v) => s + v, 0) / allAdjustedSl.length
      : system.stopLossPct;

    const newVersion = system.version + 1;

    // Crear el nuevo sistema derivado
    const newSystem = await db.tradingSystem.create({
      data: {
        name: `${system.name}_refined_V${newVersion}`,
        description: `Auto-refined from ${system.name} V${system.version}. Adjustments: ${Object.keys(changes).join(', ')}`,
        category: system.category,
        icon: system.icon,
        assetFilter: system.assetFilter,
        phaseConfig: JSON.stringify(newPhaseConfig),
        entrySignal: JSON.stringify(newEntrySignal),
        executionConfig: system.executionConfig,
        exitSignal: system.exitSignal,
        bigDataContext: system.bigDataContext,
        primaryTimeframe: system.primaryTimeframe,
        confirmTimeframes: system.confirmTimeframes,
        maxPositionPct: hasWeakPhases ? currentPositionPct : Math.round(Math.min(10, currentPositionPct * 1.05) * 100) / 100,
        maxOpenPositions: system.maxOpenPositions,
        stopLossPct: Math.round(newStopLossPct * 100) / 100,
        takeProfitPct: system.takeProfitPct,
        trailingStopPct: system.trailingStopPct,
        cashReservePct: system.cashReservePct,
        allocationMethod: system.allocationMethod,
        allocationConfig: system.allocationConfig,
        isActive: false, // Nuevo sistema inicia inactivo hasta backtest
        isPaperTrading: true, // Iniciar en paper trading para validación
        version: newVersion,
        parentSystemId: systemId,
        autoOptimize: system.autoOptimize,
        optimizationMethod: system.optimizationMethod,
        optimizationFreq: system.optimizationFreq,
      },
    });

    // Registrar la evolución en SystemEvolution
    await db.systemEvolution.create({
      data: {
        parentSystemId: systemId,
        childSystemId: newSystem.id,
        evolutionType: 'parameter_adjust',
        triggerMetric: 'phase_performance',
        triggerValue: hasWeakPhases
          ? Math.min(...Object.values(phasePerformance).map(p => p.winRate))
          : system.bestWinRate,
        improvementPct: 0, // Se actualizará después del backtest del nuevo sistema
        createdAt: new Date(),
      },
    });

    // Generar descripción de mejora esperada
    const expectedImprovement = hasWeakPhases
      ? `Expected improvement in weak phases (${weakPhasesList(phasePerformance)}) ` +
        `through parameter adjustments: ${Object.keys(changes).join(', ')}`
      : `Minor position size increase (5%) based on overall robust performance`;

    return {
      originalSystemId: systemId,
      newSystemId: newSystem.id,
      evolutionType: 'parameter_adjust',
      changes,
      expectedImprovement,
    };
  }

  // ============================================================
  // 4. GENERATE SYNTHETIC SYSTEM
  // ============================================================

  /**
   * Genera un sistema sintético especializado para una fase objetivo.
   *
   * Crea un nuevo sistema derivado del padre, optimizado para operar
   * específicamente en la fase indicada. Ajusta las 5 capas de configuración
   * (assetFilter, phaseConfig, entrySignal, executionConfig, exitSignal)
   * para enfocarse en la fase objetivo con peso 0.7 y parámetros de riesgo
   * apropiados.
   *
   * @param parentSystemId - ID del sistema padre del cual derivar
   * @param targetPhase - Fase del ciclo de vida para la cual especializar
   * @returns Detalles del nuevo sistema sintético creado
   */
  async generateSyntheticSystem(
    parentSystemId: string,
    targetPhase: TokenPhase
  ): Promise<SyntheticSystemResult> {
    // Cargar el sistema padre
    const parent = await db.tradingSystem.findUnique({
      where: { id: parentSystemId },
    });

    if (!parent) {
      throw new Error(`Parent TradingSystem not found: ${parentSystemId}`);
    }

    const riskAdj = PHASE_RISK_ADJUSTMENTS[targetPhase];
    const newVersion = parent.version + 1;

    // === Capa 1: Asset Filter ===
    // Ajustar filtros de activos según la fase objetivo
    const parentAssetFilter = safeJsonParse<Record<string, unknown>>(parent.assetFilter, {});
    const syntheticAssetFilter = {
      ...parentAssetFilter,
      // Fases tempranas requieren más liquidez mínima
      minLiquidity: targetPhase === 'GENESIS' || targetPhase === 'INCIPIENT'
        ? 0 // Sin mínimo de liquidez en fases tempranas
        : (parentAssetFilter.minLiquidity as number) ?? 50000,
      // Fases de declive requieren más holders para seguridad
      minHolders: targetPhase === 'DECLINE' || targetPhase === 'LEGACY'
        ? Math.max((parentAssetFilter.minHolders as number) ?? 100, 500)
        : (parentAssetFilter.minHolders as number) ?? 100,
    };

    // === Capa 2: Phase Config ===
    // Configuración centrada en la fase objetivo con peso 0.7
    const parentPhaseConfig = safeJsonParse<Record<string, unknown>>(parent.phaseConfig, {});
    const allPhases: TokenPhase[] = ['GENESIS', 'INCIPIENT', 'GROWTH', 'FOMO', 'DECLINE', 'LEGACY'];
    const syntheticPhaseConfig: Record<string, unknown> = {};

    for (const phase of allPhases) {
      if (phase === targetPhase) {
        // Fase objetivo: peso alto, parámetros de riesgo específicos
        syntheticPhaseConfig[phase] = {
          enabled: true,
          weight: 0.7,
          stopLossPct: parent.stopLossPct * riskAdj.stopLossMultiplier,
          maxPositionPct: parent.maxPositionPct * riskAdj.positionSizeMultiplier,
          takeProfitPct: parent.takeProfitPct * riskAdj.takeProfitMultiplier,
          confidenceThreshold: riskAdj.confidenceThreshold,
        };
      } else {
        // Otras fases: peso bajo, deshabilitadas o con exposición mínima
        const parentPhaseEntry = parentPhaseConfig[phase] as Record<string, unknown> | undefined;
        syntheticPhaseConfig[phase] = {
          enabled: false,
          weight: 0.3 / (allPhases.length - 1), // Distribuir 0.3 entre las demás
          ...(parentPhaseEntry ?? {}),
        };
      }
    }

    // === Capa 3: Entry Signal ===
    // Ajustar señales de entrada según la fase objetivo
    const parentEntrySignal = safeJsonParse<Record<string, unknown>>(parent.entrySignal, {});
    const syntheticEntrySignal = {
      ...parentEntrySignal,
      confidenceThreshold: riskAdj.confidenceThreshold,
      // Fases tempranas: señales de volumen/bot más importantes
      // Fases tardías: señales de smart money/liquidez más importantes
      phaseSpecificWeights: this.getPhaseSpecificEntryWeights(targetPhase),
      targetPhase,
    };

    // === Capa 4: Execution Config ===
    // Ajustar ejecución según la fase objetivo
    const parentExecutionConfig = safeJsonParse<Record<string, unknown>>(parent.executionConfig, {});
    const syntheticExecutionConfig = {
      ...parentExecutionConfig,
      // FOMO y GENESIS: ejecución más rápida (slippage tolerance mayor)
      slippageToleranceBps: targetPhase === 'FOMO' || targetPhase === 'GENESIS' ? 200 : 50,
      // DECLINE: usar limit orders para evitar slippage en venta
      preferLimitOrders: targetPhase === 'DECLINE',
      // GROWTH y LEGACY: DCA permitido
      allowDCA: targetPhase === 'GROWTH' || targetPhase === 'LEGACY',
    };

    // === Capa 5: Exit Signal ===
    // Ajustar señales de salida según la fase objetivo
    const parentExitSignal = safeJsonParse<Record<string, unknown>>(parent.exitSignal, {});
    const syntheticExitSignal = {
      ...parentExitSignal,
      stopLossPct: parent.stopLossPct * riskAdj.stopLossMultiplier,
      takeProfitPct: parent.takeProfitPct * riskAdj.takeProfitMultiplier,
      // FOMO: trailing stop más ajustado
      trailingStopPct: targetPhase === 'FOMO' ? 5 : targetPhase === 'GROWTH' ? 10 : 8,
      // DECLINE: salida rápida en señales de distribución
      quickExitOnDistribution: targetPhase === 'DECLINE' || targetPhase === 'FOMO',
    };

    // Calcular parámetros de riesgo finales
    const adjustedParameters: Record<string, unknown> = {
      stopLossPct: Math.round(parent.stopLossPct * riskAdj.stopLossMultiplier * 100) / 100,
      maxPositionPct: Math.round(parent.maxPositionPct * riskAdj.positionSizeMultiplier * 100) / 100,
      takeProfitPct: Math.round(parent.takeProfitPct * riskAdj.takeProfitMultiplier * 100) / 100,
      confidenceThreshold: riskAdj.confidenceThreshold,
      stopLossMultiplier: riskAdj.stopLossMultiplier,
      positionSizeMultiplier: riskAdj.positionSizeMultiplier,
      takeProfitMultiplier: riskAdj.takeProfitMultiplier,
      targetPhaseWeight: 0.7,
    };

    const systemName = `${parent.name}_${targetPhase}_V${newVersion}`;

    // Crear el nuevo sistema sintético
    const newSystem = await db.tradingSystem.create({
      data: {
        name: systemName,
        description: `Phase-specialized system for ${targetPhase}, derived from ${parent.name} V${parent.version}. ` +
          `Risk: SL×${riskAdj.stopLossMultiplier}, Pos×${riskAdj.positionSizeMultiplier}, TP×${riskAdj.takeProfitMultiplier}`,
        category: parent.category,
        icon: '🔬', // Icono de sistema sintético
        assetFilter: JSON.stringify(syntheticAssetFilter),
        phaseConfig: JSON.stringify(syntheticPhaseConfig),
        entrySignal: JSON.stringify(syntheticEntrySignal),
        executionConfig: JSON.stringify(syntheticExecutionConfig),
        exitSignal: JSON.stringify(syntheticExitSignal),
        bigDataContext: parent.bigDataContext,
        primaryTimeframe: parent.primaryTimeframe,
        confirmTimeframes: parent.confirmTimeframes,
        maxPositionPct: adjustedParameters.maxPositionPct as number,
        maxOpenPositions: Math.max(3, Math.floor(parent.maxOpenPositions * 0.7)),
        stopLossPct: adjustedParameters.stopLossPct as number,
        takeProfitPct: adjustedParameters.takeProfitPct as number,
        trailingStopPct: targetPhase === 'FOMO' ? 5 : targetPhase === 'GROWTH' ? 10 : 8,
        cashReservePct: Math.max(20, parent.cashReservePct), // Mínimo 20% en reserva
        allocationMethod: parent.allocationMethod,
        allocationConfig: parent.allocationConfig,
        isActive: false, // Inactivo hasta validación
        isPaperTrading: true,
        version: newVersion,
        parentSystemId,
        autoOptimize: true, // Sistemas sintéticos se auto-optimizan
        optimizationMethod: 'WALK_FORWARD',
        optimizationFreq: 'WEEKLY',
      },
    });

    // Registrar la evolución en SystemEvolution
    await db.systemEvolution.create({
      data: {
        parentSystemId,
        childSystemId: newSystem.id,
        evolutionType: 'phase_specialize',
        triggerMetric: 'phase_specialization',
        triggerValue: 0.7, // Peso de la fase objetivo
        improvementPct: 0, // Se actualizará después del backtest
        createdAt: new Date(),
      },
    });

    return {
      parentSystemId,
      newSystemId: newSystem.id,
      targetPhase,
      name: systemName,
      adjustedParameters,
    };
  }

  // ============================================================
  // 5. RUN COMPARATIVE ANALYSIS
  // ============================================================

  /**
   * Ejecuta un análisis comparativo entre todos los sistemas de trading backtesteados.
   *
   * Para cada par de sistemas que comparten categoría, compara:
   *   - Métricas globales (Sharpe, win rate, profit factor)
   *   - Rendimiento por fase (cuál sistema es mejor en cada fase)
   *   - Rendimiento reciente (últimos 7d vs 30d)
   *
   * Almacena los resultados en ComparativeAnalysis y retorna un ranking
   * con recomendaciones accionables.
   *
   * @returns Reporte comparativo con rankings y recomendaciones
   */
  async runComparativeAnalysis(): Promise<ComparativeReport> {
    // Cargar todos los sistemas que han sido backesteados
    const systems = await db.tradingSystem.findMany({
      where: {
        totalBacktests: { gt: 0 },
      },
      include: {
        backtests: {
          where: { status: 'COMPLETED' },
          orderBy: { createdAt: 'desc' },
          take: 10,
        },
      },
    });

    if (systems.length < 2) {
      return {
        comparisons: [],
        rankings: systems.map(s => ({
          systemId: s.id,
          name: s.name,
          overallScore: s.bestSharpe,
        })),
        recommendations: [
          'Insufficient systems for comparative analysis. Need at least 2 backtested systems.',
        ],
      };
    }

    // Agrupar sistemas por categoría para comparaciones justas
    const systemsByCategory: Record<string, typeof systems> = {};
    for (const system of systems) {
      if (!systemsByCategory[system.category]) {
        systemsByCategory[system.category] = [];
      }
      systemsByCategory[system.category].push(system);
    }

    const comparisons: ComparativeReport['comparisons'] = [];
    const systemScores: Record<string, number> = {};

    // Comparar cada par de sistemas dentro de la misma categoría
    for (const [category, categorySystems] of Object.entries(systemsByCategory)) {
      for (let i = 0; i < categorySystems.length; i++) {
        for (let j = i + 1; j < categorySystems.length; j++) {
          const sysA = categorySystems[i];
          const sysB = categorySystems[j];

          // Obtener las mejores métricas de cada sistema
          const metricsA = this.extractBestMetrics(sysA);
          const metricsB = this.extractBestMetrics(sysB);

          // --- Dimensión 1: Modelo vs Modelo (métricas globales) ---
          const globalWinner = this.determineGlobalWinner(metricsA, metricsB);
          comparisons.push({
            modelA: sysA.name,
            modelB: sysB.name,
            dimension: `${category}_model_vs_model`,
            winner: globalWinner,
            metricsA,
            metricsB,
          });

          // --- Dimensión 2: Fase vs Fase ---
          const phaseComparison = await this.compareByPhase(sysA, sysB);
          for (const comp of phaseComparison) {
            comparisons.push(comp);
          }

          // --- Dimensión 3: Período reciente ---
          const recentComparison = this.compareByRecentPeriod(sysA, sysB);
          comparisons.push(...recentComparison);

          // Acumular scores para ranking global
          if (!systemScores[sysA.id]) systemScores[sysA.id] = 0;
          if (!systemScores[sysB.id]) systemScores[sysB.id] = 0;

          if (globalWinner === 'A') systemScores[sysA.id] += 1;
          else if (globalWinner === 'B') systemScores[sysB.id] += 1;

          // Bonus por victorias en fases
          for (const comp of phaseComparison) {
            if (comp.winner === 'A') systemScores[sysA.id] += 0.5;
            else if (comp.winner === 'B') systemScores[sysB.id] += 0.5;
          }
        }
      }
    }

    // Generar ranking ordenado por score
    const rankings: ComparativeReport['rankings'] = systems
      .map(s => ({
        systemId: s.id,
        name: s.name,
        overallScore: systemScores[s.id] ?? 0,
      }))
      .sort((a, b) => b.overallScore - a.overallScore);

    // Almacenar comparaciones en la BD
    const now = new Date();
    const storePromises = comparisons.map(comp =>
      db.comparativeAnalysis.create({
        data: {
          modelA: comp.modelA,
          modelB: comp.modelB,
          dimension: comp.dimension,
          context: JSON.stringify({ category: comp.dimension.split('_')[0] }),
          metricsA: JSON.stringify(comp.metricsA),
          metricsB: JSON.stringify(comp.metricsB),
          winner: comp.winner === 'A' ? 'A' : comp.winner === 'B' ? 'B' : 'tie',
          confidenceDiff: Math.abs(
            (comp.metricsA.sharpe ?? 0) - (comp.metricsB.sharpe ?? 0)
          ),
          measuredAt: now,
        },
      })
    );

    await Promise.allSettled(storePromises);

    // Generar recomendaciones
    const recommendations = this.generateRecommendations(rankings, comparisons);

    return {
      comparisons,
      rankings,
      recommendations,
    };
  }

  // ============================================================
  // PRIVATE HELPER METHODS
  // ============================================================

  /**
   * Valida una señal individual comparándola con el movimiento real del precio.
   *
   * Carga los candles del token en el período de predicción y determina
   * si la dirección predicha coincidió con el resultado real.
   */
  private async validateSingleSignal(signal: {
    id: string;
    signalType: string;
    tokenAddress: string | null;
    sector: string | null;
    prediction: string;
    confidence: number;
    timeframe: string;
    validUntil: Date | null;
    createdAt: Date;
  }): Promise<{
    wasCorrect: boolean;
    brierScore: number;
    actualOutcome: {
      direction: string;
      priceChangePct: number;
      startPrice: number | null;
      endPrice: number | null;
    };
  }> {
    const prediction = safeJsonParse<Record<string, unknown>>(signal.prediction, {});
    const predictedDirection = (prediction.direction as string) ?? 'UP';

    // Si no hay token address, no se puede validar con precio
    if (!signal.tokenAddress || !signal.validUntil) {
      return {
        wasCorrect: false,
        brierScore: computeBrierScore(signal.confidence, false),
        actualOutcome: { direction: 'UNKNOWN', priceChangePct: 0, startPrice: null, endPrice: null },
      };
    }

    // Cargar candles del token en la ventana de predicción
    const candles = await db.priceCandle.findMany({
      where: {
        tokenAddress: signal.tokenAddress,
        timeframe: signal.timeframe === '1h' ? '1h' : '1h', // Usar 1h como resolución base
        timestamp: {
          gte: signal.createdAt,
          lte: signal.validUntil,
        },
      },
      orderBy: { timestamp: 'asc' },
    });

    // Si no hay datos de precio, no se puede validar
    if (candles.length < 2) {
      return {
        wasCorrect: false,
        brierScore: computeBrierScore(signal.confidence, false),
        actualOutcome: { direction: 'NO_DATA', priceChangePct: 0, startPrice: null, endPrice: null },
      };
    }

    // Determinar el movimiento real del precio
    const startPrice = candles[0].open;
    const endPrice = candles[candles.length - 1].close;
    const actualDirection = getPriceDirection(startPrice, endPrice);
    const priceChangePct = startPrice > 0 ? ((endPrice - startPrice) / startPrice) * 100 : 0;

    // Comparar predicción con resultado
    let wasCorrect = false;
    if (predictedDirection === 'LONG' || predictedDirection === 'UP') {
      wasCorrect = actualDirection === 'UP';
    } else if (predictedDirection === 'SHORT' || predictedDirection === 'DOWN') {
      wasCorrect = actualDirection === 'DOWN';
    } else if (predictedDirection === 'NEUTRAL' || predictedDirection === 'FLAT') {
      wasCorrect = actualDirection === 'FLAT';
    }

    const brierScore = computeBrierScore(signal.confidence, wasCorrect);

    return {
      wasCorrect,
      brierScore,
      actualOutcome: {
        direction: actualDirection,
        priceChangePct: Math.round(priceChangePct * 100) / 100,
        startPrice,
        endPrice,
      },
    };
  }

  /**
   * Analiza el rendimiento por fase a partir de operaciones de backtest.
   * Retorna un mapa de fase → {winRate, sharpe, totalOps}.
   */
  private analyzePhasePerformance(
    operations: Array<{ tokenPhase: string; pnlPct: number | null; pnlUsd: number | null }>
  ): Record<string, { winRate: number; sharpe: number; totalOps: number }> {
    const phaseOps: Record<string, number[]> = {};
    const phaseWins: Record<string, number> = {};

    for (const op of operations) {
      const phase = op.tokenPhase;
      if (!phaseOps[phase]) {
        phaseOps[phase] = [];
        phaseWins[phase] = 0;
      }
      phaseOps[phase].push(op.pnlPct ?? 0);
      if ((op.pnlUsd ?? 0) > 0) phaseWins[phase]++;
    }

    const result: Record<string, { winRate: number; sharpe: number; totalOps: number }> = {};

    for (const [phase, returns] of Object.entries(phaseOps)) {
      const totalOps = returns.length;
      const winRate = totalOps > 0 ? (phaseWins[phase] ?? 0) / totalOps : 0;

      const avgReturn = returns.reduce((s, r) => s + r, 0) / totalOps;
      const variance = returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / totalOps;
      const stdDev = Math.sqrt(variance);
      const sharpe = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;

      result[phase] = { winRate, sharpe, totalOps };
    }

    return result;
  }

  /**
   * Calcula el max drawdown a partir de una lista de retornos porcentuales.
   * Retorna el max drawdown como valor positivo (0-100).
   */
  private computeMaxDrawdown(returns: number[]): number {
    if (returns.length === 0) return 0;

    let peak = 1.0;
    let maxDd = 0;
    let cumulative = 1.0;

    for (const ret of returns) {
      cumulative *= (1 + ret / 100);
      if (cumulative > peak) peak = cumulative;
      const dd = (peak - cumulative) / peak;
      if (dd > maxDd) maxDd = dd;
    }

    return maxDd * 100; // Convertir a porcentaje
  }

  /**
   * Retorna los pesos de entrada específicos para cada fase.
   *
   * En fases tempranas (GENESIS, INCIPIENT), las señales de volumen
   * y actividad bot son más importantes. En fases maduras (GROWTH, FOMO),
   * smart money y liquidez dominan. En DECLINE/LEGACY, señales de salida
   * y distribución son clave.
   */
  private getPhaseSpecificEntryWeights(phase: TokenPhase): Record<string, number> {
    const weightProfiles: Record<TokenPhase, Record<string, number>> = {
      GENESIS: {
        volumeSpike: 0.3,
        botActivity: 0.25,
        holderVelocity: 0.2,
        liquidityLevel: 0.05,
        smartMoneyFlow: 0.1,
        priceMomentum: 0.1,
      },
      INCIPIENT: {
        botActivity: 0.2,
        smartMoneyFlow: 0.25,
        volumeSpike: 0.2,
        holderVelocity: 0.15,
        liquidityLevel: 0.1,
        priceMomentum: 0.1,
      },
      GROWTH: {
        smartMoneyFlow: 0.25,
        liquidityLevel: 0.2,
        holderVelocity: 0.2,
        priceMomentum: 0.15,
        volumeSpike: 0.1,
        botActivity: 0.1,
      },
      FOMO: {
        liquidityLevel: 0.25,
        holderVelocity: 0.2,
        priceMomentum: 0.2,
        smartMoneyFlow: 0.15,
        volumeSpike: 0.1,
        botActivity: 0.1,
      },
      DECLINE: {
        smartMoneyDistribution: 0.3,
        liquidityDrain: 0.25,
        botActivity: 0.15,
        priceMomentum: 0.15,
        holderVelocity: 0.1,
        volumeSpike: 0.05,
      },
      LEGACY: {
        liquidityLevel: 0.3,
        smartMoneyFlow: 0.2,
        priceMomentum: 0.2,
        volumeSpike: 0.15,
        holderVelocity: 0.1,
        botActivity: 0.05,
      },
    };

    return weightProfiles[phase];
  }

  /**
   * Extrae las mejores métricas de un sistema a partir de sus backtests.
   * Retorna las métricas del mejor backtest (mayor Sharpe).
   */
  private extractBestMetrics(
    system: {
      bestSharpe: number;
      bestWinRate: number;
      bestPnlPct: number;
      backtests: Array<{
        sharpeRatio: number;
        winRate: number;
        profitFactor: number;
        totalPnlPct: number;
        sortinoRatio: number | null;
        maxDrawdownPct: number;
      }>;
    }
  ): Record<string, number> {
    // Si hay backtests, usar el mejor por Sharpe
    if (system.backtests.length > 0) {
      const best = system.backtests.reduce((a, b) =>
        a.sharpeRatio > b.sharpeRatio ? a : b
      );

      return {
        sharpe: best.sharpeRatio,
        winRate: best.winRate,
        profitFactor: best.profitFactor,
        pnlPct: best.totalPnlPct,
        sortino: best.sortinoRatio ?? 0,
        maxDrawdownPct: best.maxDrawdownPct,
      };
    }

    // Fallback a las métricas almacenadas en el sistema
    return {
      sharpe: system.bestSharpe,
      winRate: system.bestWinRate,
      profitFactor: 0,
      pnlPct: system.bestPnlPct,
      sortino: 0,
      maxDrawdownPct: 0,
    };
  }

  /**
   * Determina el ganador global entre dos sistemas basándose en métricas.
   * Usa un sistema de puntuación ponderado.
   */
  private determineGlobalWinner(
    metricsA: Record<string, number>,
    metricsB: Record<string, number>
  ): string {
    // Pesos para cada métrica en la comparación
    const weights: Record<string, number> = {
      sharpe: 0.35,
      winRate: 0.25,
      profitFactor: 0.2,
      pnlPct: 0.15,
      maxDrawdownPct: 0.05, // Menor es mejor (se invierte)
    };

    let scoreA = 0;
    let scoreB = 0;

    for (const [metric, weight] of Object.entries(weights)) {
      const valA = metricsA[metric] ?? 0;
      const valB = metricsB[metric] ?? 0;

      if (metric === 'maxDrawdownPct') {
        // Menor drawdown es mejor
        if (valA < valB) scoreA += weight;
        else if (valB < valA) scoreB += weight;
      } else {
        if (valA > valB) scoreA += weight;
        else if (valB > valA) scoreB += weight;
      }
    }

    if (scoreA > scoreB) return 'A';
    if (scoreB > scoreA) return 'B';
    return 'tie';
  }

  /**
   * Compara dos sistemas por fase usando operaciones de backtest.
   * Retorna una comparación por cada fase donde ambos tienen datos.
   */
  private async compareByPhase(
    sysA: { id: string; name: string; backtests: Array<{ id: string }> },
    sysB: { id: string; name: string; backtests: Array<{ id: string }> }
  ): Promise<ComparativeReport['comparisons']> {
    const comparisons: ComparativeReport['comparisons'] = [];

    // Cargar operaciones de ambos sistemas
    const backtestIdsA = sysA.backtests.map(b => b.id);
    const backtestIdsB = sysB.backtests.map(b => b.id);

    const [opsA, opsB] = await Promise.all([
      db.backtestOperation.findMany({
        where: { systemId: sysA.id, backtestId: { in: backtestIdsA } },
      }),
      db.backtestOperation.findMany({
        where: { systemId: sysB.id, backtestId: { in: backtestIdsB } },
      }),
    ]);

    // Agrupar por fase
    const phasesA = this.groupOpsByPhase(opsA);
    const phasesB = this.groupOpsByPhase(opsB);

    // Comparar en fases comunes
    const commonPhases = Array.from(new Set([...Object.keys(phasesA), ...Object.keys(phasesB)]));

    for (const phase of commonPhases) {
      const perfA = phasesA[phase];
      const perfB = phasesB[phase];

      if (!perfA || !perfB) continue;

      const metricsA: Record<string, number> = {
        winRate: perfA.winRate,
        sharpe: perfA.sharpe,
        totalOps: perfA.totalOps,
      };
      const metricsB: Record<string, number> = {
        winRate: perfB.winRate,
        sharpe: perfB.sharpe,
        totalOps: perfB.totalOps,
      };

      // Determinar ganador por Sharpe en esta fase
      let winner = 'tie';
      if (perfA.sharpe > perfB.sharpe + 0.1) winner = 'A';
      else if (perfB.sharpe > perfA.sharpe + 0.1) winner = 'B';

      comparisons.push({
        modelA: sysA.name,
        modelB: sysB.name,
        dimension: `phase_${phase}`,
        winner,
        metricsA,
        metricsB,
      });
    }

    return comparisons;
  }

  /**
   * Compara dos sistemas por rendimiento reciente (últimos 7d vs 30d).
   */
  private compareByRecentPeriod(
    sysA: {
      name: string;
      backtests: Array<{
        createdAt: Date;
        sharpeRatio: number;
        winRate: number;
        profitFactor: number;
        totalPnlPct: number;
      }>;
    },
    sysB: {
      name: string;
      backtests: Array<{
        createdAt: Date;
        sharpeRatio: number;
        winRate: number;
        profitFactor: number;
        totalPnlPct: number;
      }>;
    }
  ): ComparativeReport['comparisons'] {
    const now = Date.now();
    const sevenDaysAgo = new Date(now - 7 * 24 * 60 * 60 * 1000);
    const thirtyDaysAgo = new Date(now - 30 * 24 * 60 * 60 * 1000);

    // Filtrar backtests por período reciente
    const recent7dA = sysA.backtests.filter(b => b.createdAt >= sevenDaysAgo);
    const recent7dB = sysB.backtests.filter(b => b.createdAt >= sevenDaysAgo);
    const recent30dA = sysA.backtests.filter(b => b.createdAt >= thirtyDaysAgo);
    const recent30dB = sysB.backtests.filter(b => b.createdAt >= thirtyDaysAgo);

    // Promediar métricas recientes
    const avgMetrics = (backtests: typeof recent7dA): Record<string, number> => {
      if (backtests.length === 0) return { sharpe: 0, winRate: 0, profitFactor: 0, pnlPct: 0 };
      return {
        sharpe: backtests.reduce((s, b) => s + b.sharpeRatio, 0) / backtests.length,
        winRate: backtests.reduce((s, b) => s + b.winRate, 0) / backtests.length,
        profitFactor: backtests.reduce((s, b) => s + b.profitFactor, 0) / backtests.length,
        pnlPct: backtests.reduce((s, b) => s + b.totalPnlPct, 0) / backtests.length,
      };
    };

    const metrics7dA = avgMetrics(recent7dA);
    const metrics7dB = avgMetrics(recent7dB);
    const metrics30dA = avgMetrics(recent30dA);
    const metrics30dB = avgMetrics(recent30dB);

    // Determinar ganador por período reciente (7d tiene más peso)
    const score7d = (metrics7dA.sharpe > metrics7dB.sharpe ? 1 : metrics7dB.sharpe > metrics7dA.sharpe ? -1 : 0);
    const score30d = (metrics30dA.sharpe > metrics30dB.sharpe ? 1 : metrics30dB.sharpe > metrics30dA.sharpe ? -1 : 0);

    let winner = 'tie';
    if (score7d + score30d > 0) winner = 'A';
    else if (score7d + score30d < 0) winner = 'B';

    return [{
      modelA: sysA.name,
      modelB: sysB.name,
      dimension: 'period_comparison',
      winner,
      metricsA: {
        sharpe_7d: metrics7dA.sharpe,
        sharpe_30d: metrics30dA.sharpe,
        winRate_7d: metrics7dA.winRate,
        winRate_30d: metrics30dA.winRate,
      },
      metricsB: {
        sharpe_7d: metrics7dB.sharpe,
        sharpe_30d: metrics30dB.sharpe,
        winRate_7d: metrics7dB.winRate,
        winRate_30d: metrics30dB.winRate,
      },
    }];
  }

  /**
   * Agrupa operaciones por fase y calcula métricas agregadas.
   */
  private groupOpsByPhase(
    operations: Array<{ tokenPhase: string; pnlPct: number | null; pnlUsd: number | null }>
  ): Record<string, { winRate: number; sharpe: number; totalOps: number }> {
    const phaseOps: Record<string, number[]> = {};
    const phaseWins: Record<string, number> = {};

    for (const op of operations) {
      const phase = op.tokenPhase;
      if (!phaseOps[phase]) {
        phaseOps[phase] = [];
        phaseWins[phase] = 0;
      }
      phaseOps[phase].push(op.pnlPct ?? 0);
      if ((op.pnlUsd ?? 0) > 0) phaseWins[phase]++;
    }

    const result: Record<string, { winRate: number; sharpe: number; totalOps: number }> = {};

    for (const [phase, returns] of Object.entries(phaseOps)) {
      const totalOps = returns.length;
      const winRate = totalOps > 0 ? (phaseWins[phase] ?? 0) / totalOps : 0;
      const avgReturn = returns.reduce((s, r) => s + r, 0) / totalOps;
      const variance = returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / totalOps;
      const stdDev = Math.sqrt(variance);
      const sharpe = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;

      result[phase] = { winRate, sharpe, totalOps };
    }

    return result;
  }

  /**
   * Genera recomendaciones basadas en el ranking y las comparaciones.
   */
  private generateRecommendations(
    rankings: ComparativeReport['rankings'],
    comparisons: ComparativeReport['comparisons']
  ): string[] {
    const recommendations: string[] = [];

    // Recomendación #1: Sistema top
    if (rankings.length > 0 && rankings[0].overallScore > 0) {
      recommendations.push(
        `Top performing system: "${rankings[0].name}" (score: ${rankings[0].overallScore.toFixed(1)}). ` +
        `Consider promoting to active trading.`
      );
    }

    // Recomendación #2: Sistemas con rendimiento decreciente
    const phaseComparisons = comparisons.filter(c => c.dimension.startsWith('phase_'));
    const losingSystems = new Set<string>();
    for (const comp of phaseComparisons) {
      if (comp.winner === 'A') losingSystems.add(comp.modelB);
      else if (comp.winner === 'B') losingSystems.add(comp.modelA);
    }

    for (const system of Array.from(losingSystems)) {
      recommendations.push(
        `System "${system}" underperforms in most phase comparisons. ` +
        `Consider running refineSystem() to adjust parameters.`
      );
    }

    // Recomendación #3: Combinación de sistemas
    if (rankings.length >= 2) {
      // Encontrar sistemas que ganan en fases diferentes
      const phaseWinners: Record<string, string> = {};
      for (const comp of phaseComparisons) {
        const phase = comp.dimension.replace('phase_', '');
        if (!phaseWinners[phase] && comp.winner !== 'tie') {
          phaseWinners[phase] = comp.winner === 'A' ? comp.modelA : comp.modelB;
        }
      }

      const uniqueWinners = new Set(Object.values(phaseWinners));
      if (uniqueWinners.size >= 2) {
        recommendations.push(
          `Multiple systems excel in different phases. Consider creating an ensemble ` +
          `or using generateSyntheticSystem() for phase-specific specialization.`
        );
      }
    }

    // Recomendación #4: Gap de rendimiento
    if (rankings.length >= 2) {
      const scoreGap = rankings[0].overallScore - rankings[rankings.length - 1].overallScore;
      if (scoreGap > 3) {
        recommendations.push(
          `Significant performance gap (${scoreGap.toFixed(1)} points) between top and bottom systems. ` +
          `Consider retiring low-performing systems or major parameter overhaul.`
        );
      }
    }

    return recommendations;
  }
}

/**
 * Lista las fases débiles a partir de un mapa de rendimiento por fase.
 * Retorna una cadena legible con las fases débiles y su win rate.
 */
function weakPhasesList(
  phasePerformance: Record<string, { winRate: number; sharpe: number; totalOps: number }>
): string {
  return Object.entries(phasePerformance)
    .filter(([, perf]) => perf.winRate < WEAK_PHASE_WIN_RATE || perf.sharpe < WEAK_PHASE_SHARPE)
    .map(([phase, perf]) => `${phase} (${(perf.winRate * 100).toFixed(0)}% WR)`)
    .join(', ');
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

/**
 * Instancia singleton del motor de retroalimentación continua.
 * Usar: import { feedbackLoopEngine } from '@/lib/services/feedback-loop-engine'
 */
export const feedbackLoopEngine = new FeedbackLoopEngine();
