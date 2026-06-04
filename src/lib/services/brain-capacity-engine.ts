/**
 * Brain Capacity Engine - CryptoQuant Terminal
 * Motor de Capacidad Analítica del Cerebro
 *
 * Este módulo mide y reporta la capacidad del cerebro para realizar análisis fuertes:
 * - Info Pura: datos crudos recopilados (tokens, candles, traders, señales)
 * - Info Analizada: datos procesados (DNA, operability, lifecycle, backtests)
 * - Capacidad Analítica: readiness score para generar señales de alta confianza
 * - Data Massiva: métricas de volumen de datos por categoría
 *
 * El motor calcula umbrales mínimos para cada tipo de análisis y muestra
 * cuándo el cerebro tiene suficiente información para operar con confianza.
 *
 * Niveles de capacidad:
 *   DORMANT    → Sin datos, no puede analizar
 *   GATHERING  → Recopilando datos básicos, análisis débil
 *   READY      → Suficiente para análisis básicos y señales
 *   CAPABLE    → Datos masivos, análisis fuerte, backtests confiables
 *   OPTIMAL    → Data massiva completa, análisis predictivo de alta confianza
 */

import { db } from '../db';
import fs from 'fs';
import path from 'path';
import os from 'os';

// ============================================================
// TYPES
// ============================================================

export type CapacityLevel = 'DORMANT' | 'GATHERING' | 'READY' | 'CAPABLE' | 'OPTIMAL';

export interface DataCategoryMetrics {
  /** Nombre de la categoría */
  name: string;
  /** Registros actuales */
  count: number;
  /** Mínimo requerido para nivel READY */
  minReady: number;
  /** Mínimo requerido para nivel CAPABLE */
  minCapable: number;
  /** Mínimo requerido para nivel OPTIMAL */
  minOptimal: number;
  /** Porcentaje de completitud hacia OPTIMAL */
  fillPct: number;
  /** Nivel alcanzado */
  level: CapacityLevel;
  /** Color para UI */
  color: string;
  /** Icono para UI */
  icon: string;
  /** Tamaño estimado en KB */
  sizeKB: number;
}

export interface StorageMetrics {
  /** Tamaño del archivo de BD en MB */
  dbFileSizeMB: number;
  /** Tamaño estimado de info pura en KB */
  rawDataSizeKB: number;
  /** Tamaño estimado de info analizada en KB */
  analyzedDataSizeKB: number;
  /** Uso de memoria del proceso Node en MB */
  processMemoryMB: number;
  /** Uso de memoria RSS en MB */
  rssMemoryMB: number;
  /** Memoria total del sistema en MB */
  systemTotalMemoryMB: number;
  /** Memoria libre del sistema en MB */
  systemFreeMemoryMB: number;
  /** Uso de memoria del sistema en % */
  systemMemoryUsagePct: number;
  /** Tamaño estimado por tabla */
  tableSizes: Array<{
    table: string;
    records: number;
    estimatedKB: number;
  }>;
}

export interface DataMetrics {
  /** Info Pura - datos crudos */
  rawInfo: DataCategoryMetrics[];
  /** Info Analizada - datos procesados */
  analyzedInfo: DataCategoryMetrics[];
  /** Métricas globales */
  totalRawRecords: number;
  totalAnalyzedRecords: number;
  /** Timestamp */
  measuredAt: Date;
}

export interface CapacityReport {
  /** Nivel general de capacidad */
  level: CapacityLevel;
  /** Score general 0-100 */
  overallScore: number;
  /** Score de info pura 0-100 */
  rawInfoScore: number;
  /** Score de info analizada 0-100 */
  analyzedInfoScore: number;
  /** Porcentaje de datos para análisis fuerte */
  strongAnalysisReadiness: number;
  /** Detalle por categoría */
  metrics: DataMetrics;
  /** Métricas de almacenamiento y memoria */
  storage: StorageMetrics;
  /** Qué se necesita para subir de nivel */
  nextLevelRequirements: string[];
  /** Tiempo estimado para siguiente nivel (basado en rate de recolección) */
  estimatedTimeToNextLevel: string;
  /** Capacidad por tipo de análisis */
  analysisCapabilities: {
    basicSignals: boolean;
    dnaAnalysis: boolean;
    backtesting: boolean;
    predictiveModeling: boolean;
    phaseStrategy: boolean;
    syntheticGeneration: boolean;
    walkForward: boolean;
    autonomousTrading: boolean;
  };
  /** Historial de capacidad (últimas 24h) */
  capacityHistory: Array<{
    timestamp: string;
    score: number;
    level: string;
    rawScore: number;
    analyzedScore: number;
  }>;
  /** Tasa de recolección de datos */
  collectionRate: {
    tokensPerHour: number;
    candlesPerHour: number;
    signalsPerHour: number;
    analysisPerHour: number;
  };
  /** Timestamp del reporte */
  generatedAt: Date;
}

// ============================================================
// ESTIMATED ROW SIZES (bytes per record, approximate for SQLite+Prisma)
// ============================================================

const ROW_SIZE_ESTIMATES: Record<string, number> = {
  Token: 512,
  PriceCandle: 128,
  Trader: 1024,
  Signal: 256,
  TokenDNA: 768,
  OperabilitySnapshot: 256,
  TokenLifecycleState: 512,
  BacktestRun: 512,
  FeedbackMetrics: 128,
  SystemEvolution: 256,
  CompoundGrowthTracker: 128,
  BrainCycleRun: 256,
};

// ============================================================
// THRESHOLDS
// ============================================================

const THRESHOLDS = {
  tokens: { minReady: 20, minCapable: 100, minOptimal: 500 },
  candles: { minReady: 200, minCapable: 2000, minOptimal: 10000 },
  traders: { minReady: 10, minCapable: 50, minOptimal: 200 },
  signals: { minReady: 5, minCapable: 30, minOptimal: 100 },
  dna: { minReady: 10, minCapable: 50, minOptimal: 200 },
  operability: { minReady: 10, minCapable: 50, minOptimal: 200 },
  lifecycle: { minReady: 5, minCapable: 30, minOptimal: 100 },
  backtests: { minReady: 2, minCapable: 10, minOptimal: 50 },
  feedback: { minReady: 5, minCapable: 30, minOptimal: 100 },
  evolution: { minReady: 1, minCapable: 5, minOptimal: 20 },
  growth: { minReady: 5, minCapable: 20, minOptimal: 100 },
  cycles: { minReady: 3, minCapable: 20, minOptimal: 100 },
};

// ============================================================
// BRAIN CAPACITY ENGINE CLASS
// ============================================================

class BrainCapacityEngine {
  private lastReport: CapacityReport | null = null;
  private previousMetrics: Array<{
    timestamp: number;
    tokens: number;
    candles: number;
    signals: number;
    analysis: number;
  }> = [];

  /**
   * Genera un reporte completo de capacidad del cerebro.
   * Consulta la BD para obtener conteos actuales de todas las tablas.
   */
  async generateReport(): Promise<CapacityReport> {
    // Contar registros en todas las tablas relevantes
    const [
      tokenCount,
      candleCount,
      traderCount,
      signalCount,
      dnaCount,
      operabilityCount,
      lifecycleCount,
      backtestCount,
      feedbackCount,
      evolutionCount,
      growthCount,
      cycleCount,
    ] = await Promise.all([
      db.token.count(),
      db.priceCandle.count(),
      db.trader.count(),
      db.signal.count(),
      db.tokenDNA.count(),
      db.operabilitySnapshot.count(),
      db.tokenLifecycleState.count(),
      db.backtestRun.count(),
      db.feedbackMetrics.count(),
      db.systemEvolution.count(),
      db.compoundGrowthTracker.count(),
      db.brainCycleRun.count(),
    ]);

    // Info Pura (datos crudos)
    const rawInfo: DataCategoryMetrics[] = [
      this.buildCategoryMetrics('Tokens', tokenCount, THRESHOLDS.tokens, '#9945FF', '🪙', 'Token'),
      this.buildCategoryMetrics('OHLCV Candles', candleCount, THRESHOLDS.candles, '#06b6d4', '📈', 'PriceCandle'),
      this.buildCategoryMetrics('Traders', traderCount, THRESHOLDS.traders, '#f59e0b', '👤', 'Trader'),
      this.buildCategoryMetrics('Signals', signalCount, THRESHOLDS.signals, '#10b981', '📡', 'Signal'),
    ];

    // Info Analizada (datos procesados)
    const analyzedInfo: DataCategoryMetrics[] = [
      this.buildCategoryMetrics('DNA Profiles', dnaCount, THRESHOLDS.dna, '#8b5cf6', '🧬', 'TokenDNA'),
      this.buildCategoryMetrics('Operability Scores', operabilityCount, THRESHOLDS.operability, '#f97316', '⚖️', 'OperabilitySnapshot'),
      this.buildCategoryMetrics('Lifecycle States', lifecycleCount, THRESHOLDS.lifecycle, '#ec4899', '🔄', 'TokenLifecycleState'),
      this.buildCategoryMetrics('Backtest Runs', backtestCount, THRESHOLDS.backtests, '#d4af37', '🧪', 'BacktestRun'),
      this.buildCategoryMetrics('Feedback Metrics', feedbackCount, THRESHOLDS.feedback, '#14b8a6', '📊', 'FeedbackMetrics'),
      this.buildCategoryMetrics('System Evolutions', evolutionCount, THRESHOLDS.evolution, '#a855f7', '🧬', 'SystemEvolution'),
      this.buildCategoryMetrics('Growth Records', growthCount, THRESHOLDS.growth, '#22c55e', '📉', 'CompoundGrowthTracker'),
      this.buildCategoryMetrics('Brain Cycles', cycleCount, THRESHOLDS.cycles, '#eab308', '🧠', 'BrainCycleRun'),
    ];

    const metrics: DataMetrics = {
      rawInfo,
      analyzedInfo,
      totalRawRecords: tokenCount + candleCount + traderCount + signalCount,
      totalAnalyzedRecords: dnaCount + operabilityCount + lifecycleCount + backtestCount + feedbackCount + evolutionCount + growthCount + cycleCount,
      measuredAt: new Date(),
    };

    // Calcular scores
    const rawInfoScore = this.calculateCategoryScore(rawInfo);
    const analyzedInfoScore = this.calculateCategoryScore(analyzedInfo);

    // Score general: 60% raw + 40% analyzed (necesitas datos crudos primero)
    const overallScore = Math.round(rawInfoScore * 0.6 + analyzedInfoScore * 0.4);

    // Nivel de capacidad
    const level = this.determineCapacityLevel(overallScore, rawInfoScore, analyzedInfoScore);

    // Readiness para análisis fuerte (combinación de raw + analyzed)
    const strongAnalysisReadiness = Math.min(100, Math.round(
      (rawInfoScore * 0.4 + analyzedInfoScore * 0.6)
    ));

    // Capabilities
    const analysisCapabilities = {
      basicSignals: tokenCount >= THRESHOLDS.tokens.minReady,
      dnaAnalysis: dnaCount >= THRESHOLDS.dna.minReady,
      backtesting: candleCount >= THRESHOLDS.candles.minReady && backtestCount >= 1,
      predictiveModeling: signalCount >= THRESHOLDS.signals.minReady && feedbackCount >= THRESHOLDS.feedback.minReady,
      phaseStrategy: lifecycleCount >= THRESHOLDS.lifecycle.minReady,
      syntheticGeneration: evolutionCount >= THRESHOLDS.evolution.minReady && backtestCount >= THRESHOLDS.backtests.minReady,
      walkForward: backtestCount >= THRESHOLDS.backtests.minCapable,
      autonomousTrading: overallScore >= 60 && cycleCount >= THRESHOLDS.cycles.minCapable,
    };

    // Collection rate
    const now = Date.now();
    this.previousMetrics.push({
      timestamp: now,
      tokens: tokenCount,
      candles: candleCount,
      signals: signalCount,
      analysis: dnaCount + operabilityCount + lifecycleCount,
    });
    // Keep only last 24 hours of metrics
    const oneDayAgo = now - 24 * 60 * 60 * 1000;
    this.previousMetrics = this.previousMetrics.filter(m => m.timestamp > oneDayAgo);

    const collectionRate = this.calculateCollectionRate();

    // Capacity history (from last 24 metrics points)
    const capacityHistory = this.previousMetrics.map(m => ({
      timestamp: new Date(m.timestamp).toISOString(),
      score: overallScore,
      level,
      rawScore: rawInfoScore,
      analyzedScore: analyzedInfoScore,
    }));

    // Next level requirements
    const nextLevelRequirements = this.getNextLevelRequirements(level, metrics);

    // Estimated time to next level
    const estimatedTimeToNextLevel = this.estimateTimeToNextLevel(level, metrics, collectionRate);

    // Storage & Memory Metrics
    const storage = this.calculateStorageMetrics(
      [tokenCount, candleCount, traderCount, signalCount],
      [dnaCount, operabilityCount, lifecycleCount, backtestCount, feedbackCount, evolutionCount, growthCount, cycleCount],
      rawInfo,
      analyzedInfo,
    );

    const report: CapacityReport = {
      level,
      overallScore,
      rawInfoScore,
      analyzedInfoScore,
      strongAnalysisReadiness,
      metrics,
      storage,
      nextLevelRequirements,
      estimatedTimeToNextLevel,
      analysisCapabilities,
      capacityHistory,
      collectionRate,
      generatedAt: new Date(),
    };

    this.lastReport = report;
    return report;
  }

  /**
   * Retorna el último reporte generado sin consultar la BD.
   */
  getLastReport(): CapacityReport | null {
    return this.lastReport;
  }

  /**
   * Retorna un resumen rápido del nivel de capacidad.
   */
  async getQuickStatus(): Promise<{
    level: CapacityLevel;
    score: number;
    rawScore: number;
    analyzedScore: number;
    readiness: number;
  }> {
    // Use cached report if recent (<30s)
    if (this.lastReport && (Date.now() - this.lastReport.generatedAt.getTime()) < 30000) {
      return {
        level: this.lastReport.level,
        score: this.lastReport.overallScore,
        rawScore: this.lastReport.rawInfoScore,
        analyzedScore: this.lastReport.analyzedInfoScore,
        readiness: this.lastReport.strongAnalysisReadiness,
      };
    }

    const report = await this.generateReport();
    return {
      level: report.level,
      score: report.overallScore,
      rawScore: report.rawInfoScore,
      analyzedScore: report.analyzedInfoScore,
      readiness: report.strongAnalysisReadiness,
    };
  }

  // ============================================================
  // PRIVATE HELPERS
  // ============================================================

  private buildCategoryMetrics(
    name: string,
    count: number,
    thresholds: { minReady: number; minCapable: number; minOptimal: number },
    color: string,
    icon: string,
    tableName: string,
  ): DataCategoryMetrics {
    const fillPct = Math.min(100, Math.round((count / thresholds.minOptimal) * 100));
    const level: CapacityLevel = count >= thresholds.minOptimal ? 'OPTIMAL'
      : count >= thresholds.minCapable ? 'CAPABLE'
      : count >= thresholds.minReady ? 'READY'
      : count > 0 ? 'GATHERING'
      : 'DORMANT';
    const rowSize = ROW_SIZE_ESTIMATES[tableName] || 256;
    const sizeKB = Math.round((count * rowSize) / 1024);

    return { name, count, minReady: thresholds.minReady, minCapable: thresholds.minCapable, minOptimal: thresholds.minOptimal, fillPct, level, color, icon, sizeKB };
  }

  private calculateCategoryScore(categories: DataCategoryMetrics[]): number {
    if (categories.length === 0) return 0;
    const totalScore = categories.reduce((sum, cat) => {
      // Score based on fill percentage towards optimal
      return sum + Math.min(100, cat.fillPct);
    }, 0);
    return Math.round(totalScore / categories.length);
  }

  private determineCapacityLevel(
    overallScore: number,
    rawScore: number,
    analyzedScore: number
  ): CapacityLevel {
    if (overallScore >= 75 && rawScore >= 60 && analyzedScore >= 60) return 'OPTIMAL';
    if (overallScore >= 50 && rawScore >= 40 && analyzedScore >= 35) return 'CAPABLE';
    if (overallScore >= 25 && rawScore >= 20) return 'READY';
    if (overallScore >= 5) return 'GATHERING';
    return 'DORMANT';
  }

  private getNextLevelRequirements(level: CapacityLevel, metrics: DataMetrics): string[] {
    const requirements: string[] = [];
    const allCategories = [...metrics.rawInfo, ...metrics.analyzedInfo];

    for (const cat of allCategories) {
      if (cat.level === 'DORMANT' || cat.level === 'GATHERING') {
        requirements.push(`${cat.icon} ${cat.name}: ${cat.count}/${cat.minReady} (need ${cat.minReady - cat.count} more for READY)`);
      } else if (cat.level === 'READY') {
        requirements.push(`${cat.icon} ${cat.name}: ${cat.count}/${cat.minCapable} (need ${cat.minCapable - cat.count} more for CAPABLE)`);
      } else if (cat.level === 'CAPABLE') {
        requirements.push(`${cat.icon} ${cat.name}: ${cat.count}/${cat.minOptimal} (need ${cat.minOptimal - cat.count} more for OPTIMAL)`);
      }
    }

    // Sort by urgency (furthest from next level first)
    return requirements.slice(0, 8); // Top 8 most needed
  }

  private estimateTimeToNextLevel(
    level: CapacityLevel,
    metrics: DataMetrics,
    rate: { tokensPerHour: number; candlesPerHour: number; signalsPerHour: number; analysisPerHour: number }
  ): string {
    if (level === 'OPTIMAL') return 'Already at optimal capacity';

    // Find the bottleneck category (lowest fill %)
    const allCategories = [...metrics.rawInfo, ...metrics.analyzedInfo];
    let bottleneck = allCategories[0];
    let lowestFill = 100;

    for (const cat of allCategories) {
      if (cat.fillPct < lowestFill && cat.fillPct < 100) {
        lowestFill = cat.fillPct;
        bottleneck = cat;
      }
    }

    if (!bottleneck || bottleneck.fillPct >= 100) return 'Calculating...';

    const deficit = (level === 'DORMANT' || level === 'GATHERING')
      ? bottleneck.minReady - bottleneck.count
      : level === 'READY'
        ? bottleneck.minCapable - bottleneck.count
        : bottleneck.minOptimal - bottleneck.count;

    if (deficit <= 0) return 'Almost there...';

    // Estimate based on collection rate
    const relevantRate = bottleneck.name.includes('Token') ? rate.tokensPerHour
      : bottleneck.name.includes('Candle') || bottleneck.name.includes('OHLCV') ? rate.candlesPerHour
      : bottleneck.name.includes('Signal') ? rate.signalsPerHour
      : rate.analysisPerHour;

    if (relevantRate <= 0) return 'Waiting for data collection to start';

    const hoursNeeded = deficit / relevantRate;
    if (hoursNeeded < 1) return `~${Math.round(hoursNeeded * 60)} minutes`;
    if (hoursNeeded < 24) return `~${Math.round(hoursNeeded)} hours`;
    return `~${Math.round(hoursNeeded / 24)} days`;
  }

  /**
   * Calcula métricas de almacenamiento y memoria del sistema.
   */
  private calculateStorageMetrics(
    rawCounts: number[],
    analyzedCounts: number[],
    rawCategories: DataCategoryMetrics[],
    analyzedCategories: DataCategoryMetrics[],
  ): StorageMetrics {
    // DB file size
    let dbFileSizeMB = 0;
    try {
      const dbPath = process.env.DATABASE_URL?.replace('file:', '') || path.join(process.cwd(), 'db', 'custom.db');
      const stats = fs.statSync(dbPath);
      dbFileSizeMB = Math.round((stats.size / 1024 / 1024) * 100) / 100;
    } catch {
      // Fallback: estimate from record counts
      dbFileSizeMB = 0;
    }

    // Data sizes by phase
    const rawDataSizeKB = rawCategories.reduce((sum, cat) => sum + cat.sizeKB, 0);
    const analyzedDataSizeKB = analyzedCategories.reduce((sum, cat) => sum + cat.sizeKB, 0);

    // Process memory
    const memUsage = process.memoryUsage();
    const processMemoryMB = Math.round((memUsage.heapUsed / 1024 / 1024) * 100) / 100;
    const rssMemoryMB = Math.round((memUsage.rss / 1024 / 1024) * 100) / 100;

    // System memory
    const systemTotalMemoryMB = Math.round((os.totalmem() / 1024 / 1024));
    const systemFreeMemoryMB = Math.round((os.freemem() / 1024 / 1024));
    const systemMemoryUsagePct = Math.round(((1 - os.freemem() / os.totalmem()) * 100));

    // Table sizes breakdown
    const tableSizes = [...rawCategories, ...analyzedCategories].map(cat => ({
      table: cat.name,
      records: cat.count,
      estimatedKB: cat.sizeKB,
    }));

    return {
      dbFileSizeMB,
      rawDataSizeKB,
      analyzedDataSizeKB,
      processMemoryMB,
      rssMemoryMB,
      systemTotalMemoryMB,
      systemFreeMemoryMB,
      systemMemoryUsagePct,
      tableSizes,
    };
  }

  private calculateCollectionRate(): {
    tokensPerHour: number;
    candlesPerHour: number;
    signalsPerHour: number;
    analysisPerHour: number;
  } {
    if (this.previousMetrics.length < 2) {
      return { tokensPerHour: 0, candlesPerHour: 0, signalsPerHour: 0, analysisPerHour: 0 };
    }

    const latest = this.previousMetrics[this.previousMetrics.length - 1];
    const oldest = this.previousMetrics[0];
    const hoursDiff = (latest.timestamp - oldest.timestamp) / (1000 * 60 * 60);

    if (hoursDiff <= 0) {
      return { tokensPerHour: 0, candlesPerHour: 0, signalsPerHour: 0, analysisPerHour: 0 };
    }

    return {
      tokensPerHour: Math.round((latest.tokens - oldest.tokens) / hoursDiff),
      candlesPerHour: Math.round((latest.candles - oldest.candles) / hoursDiff),
      signalsPerHour: Math.round((latest.signals - oldest.signals) / hoursDiff),
      analysisPerHour: Math.round((latest.analysis - oldest.analysis) / hoursDiff),
    };
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const brainCapacityEngine = new BrainCapacityEngine();
