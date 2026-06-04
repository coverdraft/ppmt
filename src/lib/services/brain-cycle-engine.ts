/**
 * Brain Cycle Engine - CryptoQuant Terminal
 * Motor de Ciclo de Operación Continua 24/7
 *
 * Este motor ejecuta el cerebro de forma continua:
 *   1. Escanea el mercado buscando tokens operables
 *   2. Filtra por operabilidad (fees + slippage + liquidez)
 *   3. Aplica el mejor sistema de trading a cada token operable
 *   4. Almacena snapshots de operabilidad para análisis histórico
 *   5. Hace seguimiento del crecimiento compuesto del capital
 *   6. Ejecuta el ciclo de retroalimentación para mejorar los sistemas
 *
 * El ciclo se ejecuta periódicamente (configurable) y puede ser
 * iniciado/detenido vía API.
 *
 * Flujo por ciclo:
 *   SCAN → FILTER (operability) → MATCH (trading system) → STORE → FEEDBACK → GROWTH TRACK
 */

import { db } from '../db';
// Lazy import to avoid loading all 12+ sub-engines on module load
type TokenAnalysis = import('./brain-orchestrator').TokenAnalysis;
// These will be loaded on first use
let _brainOrchestrator: typeof import('./brain-orchestrator').brainOrchestrator | null = null;
async function getBrainOrchestrator() {
  if (!_brainOrchestrator) {
    const mod = await import('./brain-orchestrator');
    _brainOrchestrator = mod.brainOrchestrator;
  }
  return _brainOrchestrator;
}

let _batchMatchSystems: typeof import('./trading-system-matcher').batchMatchSystems | null = null;
let _suggestEvolutions: typeof import('./trading-system-matcher').suggestEvolutions | null = null;
async function getTradingSystemMatcher() {
  if (!_batchMatchSystems) {
    const mod = await import('./trading-system-matcher');
    _batchMatchSystems = mod.batchMatchSystems;
    _suggestEvolutions = mod.suggestEvolutions;
  }
  return { batchMatchSystems: _batchMatchSystems!, suggestEvolutions: _suggestEvolutions! };
}
import {
  calculateOperabilityScore,
  batchOperabilityScore,
  filterOperable,
  type OperabilityInput,
  type OperabilityResult,
} from './operability-score';
import { feedbackLoopEngine } from './feedback-loop-engine';
type SystemRecommendation = import('./trading-system-matcher').SystemRecommendation;
import { ohlcvPipeline } from './ohlcv-pipeline';
import { capitalStrategyManager, type CapitalStrategyDecision } from './capital-strategy-manager';

// ============================================================
// TYPES
// ============================================================

export type CycleStatus = 'IDLE' | 'RUNNING' | 'PAUSED' | 'ERROR';

export interface CycleConfig {
  /** Capital actual en USD */
  capitalUsd: number;
  /** Capital inicial para tracking de compound growth */
  initialCapitalUsd: number;
  /** Cadena principal */
  chain: string;
  /** Cuántos tokens escanear por ciclo */
  scanLimit: number;
  /** Intervalo entre ciclos en milisegundos (default: 5 min) */
  cycleIntervalMs: number;
  /** Ganancia esperada mínima para cálculos de operabilidad (%) */
  expectedGainPct: number;
  /** Nivel mínimo de operabilidad para considerar un token */
  minOperabilityLevel: 'PREMIUM' | 'GOOD' | 'MARGINAL';
  /** Ejecutar feedback loop automáticamente después de cada ciclo */
  autoFeedback: boolean;
  /** Backfill OHLCV data para tokens nuevos */
  autoBackfill: boolean;
}

export interface CycleResult {
  cycleRunId: string;
  cycleNumber: number;
  status: 'COMPLETED' | 'PARTIAL' | 'FAILED';

  // Scan results
  tokensScanned: number;
  tokensOperable: number;
  tokensTradeable: number;

  // Operability distribution
  operabilityDistribution: {
    PREMIUM: number;
    GOOD: number;
    MARGINAL: number;
    RISKY: number;
    UNOPERABLE: number;
  };

  // Top picks with system recommendations
  topPicks: SystemRecommendation[];

  // Phase distribution observed
  phaseDistribution: Record<string, number>;

  // Regime snapshot
  dominantRegime: string;
  regimeConfidence: number;

  // Compound growth
  capitalBeforeCycle: number;
  capitalAfterCycle: number;
  estimatedCyclePnlUsd: number;
  estimatedCyclePnlPct: number;
  cumulativeReturnPct: number;

  // Capital strategy decision (NEW)
  capitalStrategy: CapitalStrategyDecision | null;

  // Operability snapshots stored
  snapshotsStored: number;

  // Duration
  durationMs: number;

  // Errors
  errors: string[];
}

export interface GrowthReport {
  currentCapital: number;
  initialCapital: number;
  totalReturnPct: number;
  totalPnlUsd: number;
  feeAdjustedPnlUsd: number;
  feeAdjustedReturnPct: number;
  totalFeesPaidUsd: number;
  totalSlippageUsd: number;
  dailyCompoundRate: number;
  projectedAnnualReturn: number;
  cyclesCompleted: number;
  averageCyclePnlPct: number;
  winRate: number;
  sharpeRatio: number;
  maxDrawdownPct: number;
}

// ============================================================
// DEFAULT CONFIG
// ============================================================

export const DEFAULT_CYCLE_CONFIG: CycleConfig = {
  capitalUsd: 10,
  initialCapitalUsd: 10,
  chain: 'SOL',
  scanLimit: 20,
  cycleIntervalMs: 5 * 60 * 1000, // 5 minutos
  expectedGainPct: 5,
  minOperabilityLevel: 'MARGINAL',
  autoFeedback: true,
  autoBackfill: true,
};

// ============================================================
// BRAIN CYCLE ENGINE CLASS
// ============================================================

class BrainCycleEngine {
  private status: CycleStatus = 'IDLE';
  private config: CycleConfig = { ...DEFAULT_CYCLE_CONFIG };
  private cycleTimer: ReturnType<typeof setInterval> | null = null;
  private currentCycleNumber = 0;
  private lastCycleResult: CycleResult | null = null;
  private consecutiveErrors = 0;
  private maxConsecutiveErrors = 5;
  private lastCapitalStrategy: CapitalStrategyDecision | null = null;

  // ============================================================
  // START / STOP / STATUS
  // ============================================================

  /**
   * Inicia el ciclo de operación continua.
   * El cerebro se ejecutará periódicamente según cycleIntervalMs.
   */
  async start(config?: Partial<CycleConfig>): Promise<{ started: boolean; message: string }> {
    if (this.status === 'RUNNING') {
      return { started: false, message: 'Brain cycle is already running' };
    }

    // Merge config
    if (config) {
      this.config = { ...this.config, ...config };
    }

    // Load last cycle number from DB and learning state
    try {
      const lastRun = await db.brainCycleRun.findFirst({
        orderBy: { cycleNumber: 'desc' },
      });
      this.currentCycleNumber = lastRun?.cycleNumber ?? 0;

      // Load current capital from last run
      if (lastRun) {
        this.config.capitalUsd = lastRun.capitalUsd;
        this.config.initialCapitalUsd = lastRun.initialCapitalUsd;
      }

      // Load capital strategy learning state
      await capitalStrategyManager.loadLearningState();
      console.log('[BrainCycle] Capital strategy learning state loaded');
    } catch (error) {
      console.warn('[BrainCycle] Could not load last cycle from DB:', error);
    }

    this.status = 'RUNNING';
    this.consecutiveErrors = 0;

    // Run first cycle immediately
    this.runCycle().catch(err => {
      console.error('[BrainCycle] First cycle error:', err);
    });

    // Schedule subsequent cycles
    this.cycleTimer = setInterval(async () => {
      if (this.status !== 'RUNNING') return;
      try {
        await this.runCycle();
        this.consecutiveErrors = 0;
      } catch (error) {
        this.consecutiveErrors++;
        console.error(`[BrainCycle] Cycle error (${this.consecutiveErrors}/${this.maxConsecutiveErrors}):`, error);
        if (this.consecutiveErrors >= this.maxConsecutiveErrors) {
          this.status = 'ERROR';
          this.stop();
        }
      }
    }, this.config.cycleIntervalMs);

    return { started: true, message: `Brain cycle started with $${this.config.capitalUsd} capital, interval ${this.config.cycleIntervalMs / 1000}s` };
  }

  /**
   * Detiene el ciclo de operación continua.
   */
  async stop(): Promise<{ stopped: boolean; message: string }> {
    if (this.cycleTimer) {
      clearInterval(this.cycleTimer);
      this.cycleTimer = null;
    }

    const wasRunning = this.status === 'RUNNING';
    this.status = 'IDLE';

    return { stopped: wasRunning, message: wasRunning ? 'Brain cycle stopped' : 'Brain cycle was not running' };
  }

  /**
   * Retorna el estado actual del motor.
   */
  getStatus(): {
    status: CycleStatus;
    config: CycleConfig;
    currentCycleNumber: number;
    lastCycleResult: CycleResult | null;
    lastCapitalStrategy: CapitalStrategyDecision | null;
    capitalSummary: ReturnType<typeof capitalStrategyManager.getCapitalSummary>;
    consecutiveErrors: number;
  } {
    return {
      status: this.status,
      config: this.config,
      currentCycleNumber: this.currentCycleNumber,
      lastCycleResult: this.lastCycleResult,
      lastCapitalStrategy: this.lastCapitalStrategy,
      capitalSummary: capitalStrategyManager.getCapitalSummary(),
      consecutiveErrors: this.consecutiveErrors,
    };
  }

  // ============================================================
  // MAIN CYCLE EXECUTION
  // ============================================================

  /**
   * Ejecuta un ciclo completo del cerebro.
   *
   * Pipeline:
   * 1. Crear registro BrainCycleRun
   * 2. Escanear tokens de la BD
   * 3. Calcular operabilidad para cada token (fee-aware filtering)
   * 4. Ejecutar análisis completo del cerebro en tokens operables
   * 5. Aplicar matching de sistemas de trading
   * 6. Almacenar snapshots de operabilidad
   * 7. Actualizar crecimiento compuesto
   * 8. Ejecutar feedback loop si autoFeedback está activado
   * 9. Cerrar ciclo con resultados
   */
  private async runCycle(): Promise<CycleResult> {
    const cycleStart = Date.now();
    const errors: string[] = [];
    this.currentCycleNumber++;

    // === STEP 1: Create cycle run record ===
    const cycleRun = await db.brainCycleRun.create({
      data: {
        cycleNumber: this.currentCycleNumber,
        capitalUsd: this.config.capitalUsd,
        initialCapitalUsd: this.config.initialCapitalUsd,
        chain: this.config.chain,
        scanLimit: this.config.scanLimit,
        status: 'RUNNING',
        capitalBeforeCycle: this.config.capitalUsd,
      },
    });

    try {
      // === STEP 2: Scan market tokens from DB ===
      const tokens = await db.token.findMany({
        where: {
          chain: this.config.chain,
          volume24h: { gt: 500 },
        },
        orderBy: { volume24h: 'desc' },
        take: this.config.scanLimit,
      });

      if (tokens.length === 0) {
        // No tokens in DB - try to signal that data sync is needed
        await db.brainCycleRun.update({
          where: { id: cycleRun.id },
          data: {
            status: 'COMPLETED',
            tokensScanned: 0,
            completedAt: new Date(),
            cycleDurationMs: Date.now() - cycleStart,
            errorLog: 'No tokens in DB. Sync from DexScreener first via /api/market/tokens',
          },
        });

        return this.buildEmptyResult(cycleRun.id, 'No tokens available');
      }

      // === STEP 2.5: Capital Strategy Decision (NEW) ===
      // The brain decides how many strategies, what % per strategy, and position sizes
      // based on capital size, drawdown, win streak, and fee awareness
      const adjustedExpectedGain = this.config.expectedGainPct + capitalStrategyManager.getAdjustedExpectedGain();
      const adjustedMinOperability = capitalStrategyManager.getAdjustedOperabilityThreshold();

      // Quick pre-filter for operability to feed into capital strategy decision
      const quickOperabilityInputs: OperabilityInput[] = tokens.map(token => ({
        tokenAddress: token.address,
        symbol: token.symbol,
        chain: token.chain as 'SOL' | 'ETH' | string,
        priceUsd: token.priceUsd,
        liquidityUsd: token.liquidity,
        volume24h: token.volume24h,
        marketCap: token.marketCap,
        positionSizeUsd: this.config.capitalUsd * 0.1,
        expectedGainPct: Math.max(adjustedExpectedGain, 1.8),
        botActivityPct: token.botActivityPct,
        holderCount: token.holderCount,
        priceChange24h: token.priceChange24h,
        dexId: token.dexId || undefined,
        pairCreatedAt: token.createdAt ? new Date(token.createdAt).getTime() : undefined,
      }));

      // Quick match systems for capital strategy context
      const quickOperResults = batchOperabilityScore(quickOperabilityInputs);
      const quickOperable = quickOperResults.filter(r => r.isOperable);
      const quickAddresses = quickOperable.map(r => r.tokenAddress);
      let quickSystemRecs: SystemRecommendation[] = [];
      if (quickAddresses.length > 0) {
        try {
          const orch = await getBrainOrchestrator();
          const quickBatch = await orch.analyzeBatch(
            quickAddresses.slice(0, 10), this.config.chain,
            this.config.capitalUsd * 0.1, Math.max(adjustedExpectedGain, 1.8)
          );
          const { batchMatchSystems: bms } = await getTradingSystemMatcher();
          quickSystemRecs = bms(quickBatch.results);
          for (const rec of quickSystemRecs) {
            const token = tokens.find(t => t.address === rec.tokenAddress);
            if (token) rec.symbol = token.symbol;
          }
        } catch {
          // Quick pre-analysis failed, will do full analysis below
        }
      }

      // DECIDE: How much capital, how many strategies, what positions
      const capitalStrategyDecision = await capitalStrategyManager.decide(
        this.config.capitalUsd,
        this.config.initialCapitalUsd,
        quickSystemRecs,
        this.config.chain
      );
      this.lastCapitalStrategy = capitalStrategyDecision;

      // === STEP 3: Calculate operability for each token (FEE-AWARE FILTERING) ===
      // Use capital strategy decision for position sizing
      const primaryAllocation = capitalStrategyDecision.allocations[0];
      const positionSizeUsd = primaryAllocation?.positionSizeUsd || this.config.capitalUsd * 0.1;

      const operabilityInputs: OperabilityInput[] = tokens.map(token => ({
        tokenAddress: token.address,
        symbol: token.symbol,
        chain: token.chain as 'SOL' | 'ETH' | string,
        priceUsd: token.priceUsd,
        liquidityUsd: token.liquidity,
        volume24h: token.volume24h,
        marketCap: token.marketCap,
        positionSizeUsd,
        expectedGainPct: Math.max(adjustedExpectedGain, 1.8),
        botActivityPct: token.botActivityPct,
        holderCount: token.holderCount,
        priceChange24h: token.priceChange24h,
        dexId: token.dexId || undefined,
        pairCreatedAt: token.createdAt ? new Date(token.createdAt).getTime() : undefined,
      }));

      // Batch operability scoring
      const operabilityResults = batchOperabilityScore(operabilityInputs);

      // Filter operable tokens
      const operableResults = filterOperable(operabilityInputs, this.config.minOperabilityLevel);

      // Count distribution
      const operabilityDistribution = {
        PREMIUM: operabilityResults.filter(r => r.level === 'PREMIUM').length,
        GOOD: operabilityResults.filter(r => r.level === 'GOOD').length,
        MARGINAL: operabilityResults.filter(r => r.level === 'MARGINAL').length,
        RISKY: operabilityResults.filter(r => r.level === 'RISKY').length,
        UNOPERABLE: operabilityResults.filter(r => r.level === 'UNOPERABLE').length,
      };

      // === STEP 4: Store operability snapshots ===
      const snapshotPromises = operabilityResults.map(result =>
        db.operabilitySnapshot.create({
          data: {
            tokenAddress: result.tokenAddress,
            symbol: result.symbol,
            chain: result.chain,
            overallScore: result.overallScore,
            liquidityScore: result.liquidityScore,
            feeScore: result.feeScore,
            slippageScore: result.slippageScore,
            healthScore: result.healthScore,
            marginScore: result.marginScore,
            totalCostUsd: result.feeEstimate.totalCostUsd,
            totalCostPct: result.feeEstimate.totalCostPct,
            slippagePct: result.feeEstimate.slippagePct,
            recommendedPositionUsd: result.recommendedPositionUsd,
            operabilityLevel: result.level,
            isOperable: result.isOperable,
            minimumGainPct: result.minimumGainPct,
            priceUsd: operabilityInputs.find(i => i.tokenAddress === result.tokenAddress)?.priceUsd ?? 0,
            liquidityUsd: operabilityInputs.find(i => i.tokenAddress === result.tokenAddress)?.liquidityUsd ?? 0,
            volume24h: operabilityInputs.find(i => i.tokenAddress === result.tokenAddress)?.volume24h ?? 0,
            marketCap: operabilityInputs.find(i => i.tokenAddress === result.tokenAddress)?.marketCap ?? 0,
            cycleRunId: cycleRun.id,
            warnings: JSON.stringify(result.warnings),
          },
        })
      );
      await Promise.allSettled(snapshotPromises);

      // === STEP 5: Run full brain analysis on operable tokens ===
      const operableAddresses = operableResults.map(r => r.tokenAddress);
      let batchResult;
      let systemRecommendations: SystemRecommendation[] = [];

      if (operableAddresses.length > 0) {
        try {
          const orch = await getBrainOrchestrator();
          batchResult = await orch.analyzeBatch(
            operableAddresses,
            this.config.chain,
            this.config.capitalUsd * 0.1, // position size
            this.config.expectedGainPct
          );

          // === STEP 6: Match trading systems for each operable token ===
          const { batchMatchSystems: bms2 } = await getTradingSystemMatcher();
          systemRecommendations = bms2(batchResult.results);

          // Enrich with symbol data
          for (const rec of systemRecommendations) {
            const token = tokens.find(t => t.address === rec.tokenAddress);
            if (token) rec.symbol = token.symbol;
          }
        } catch (error) {
          errors.push(`Brain analysis failed: ${error instanceof Error ? error.message : String(error)}`);
        }
      }

      // === STEP 7: Calculate phase distribution ===
      const phaseDistribution: Record<string, number> = {};
      if (batchResult) {
        for (const result of batchResult.results) {
          phaseDistribution[result.lifecyclePhase] = (phaseDistribution[result.lifecyclePhase] || 0) + 1;
        }
      }

      // === STEP 8: Determine dominant regime ===
      let dominantRegime = 'SIDEWAYS';
      let regimeConfidence = 0;
      if (batchResult && batchResult.results.length > 0) {
        const regimeCounts: Record<string, number> = {};
        let totalConfidence = 0;
        for (const r of batchResult.results) {
          regimeCounts[r.regime] = (regimeCounts[r.regime] || 0) + 1;
          totalConfidence += r.regimeConfidence;
        }
        const topRegime = Object.entries(regimeCounts).sort((a, b) => b[1] - a[1])[0];
        if (topRegime) {
          dominantRegime = topRegime[0];
          regimeConfidence = totalConfidence / batchResult.results.length;
        }
      }

      // === STEP 9: Estimate cycle PnL (fee-aware, capital-strategy-informed) ===
      const tradeableRecs = systemRecommendations.filter(r => r.shouldTrade);
      let estimatedPnlUsd = 0;

      for (const rec of tradeableRecs) {
        // Find the allocation for this strategy
        const strategyAlloc = capitalStrategyDecision.allocations.find(
          a => a.strategyId === rec.primarySystem
        );
        const positionSize = strategyAlloc?.positionSizeUsd || rec.config.positionSizeUsd;

        // Estimate: position_size * (win_rate * gain - (1 - win_rate) * loss)
        // DEDUCT FEES from the estimate
        const feePct = capitalStrategyDecision.minimumGainAfterFeesPct / 100;
        const grossExpectedGain = positionSize * (rec.estimatedWinRate * rec.estimatedGainPct / 100 - (1 - rec.estimatedWinRate) * rec.estimatedLossPct / 100);
        const feeDeduction = Math.abs(grossExpectedGain) * feePct;
        const netExpectedGain = grossExpectedGain - feeDeduction;

        estimatedPnlUsd += netExpectedGain;
      }

      // Cap estimated PnL at reasonable bounds (prevent runaway estimates)
      // Use capital strategy mode to set bounds
      const maxPnlPct = capitalStrategyDecision.mode === 'ULTRA_CONSERVATIVE' ? 0.02
        : capitalStrategyDecision.mode === 'CONCENTRATED' ? 0.05
        : capitalStrategyDecision.mode === 'DUAL' ? 0.04
        : 0.03;
      estimatedPnlUsd = Math.max(-this.config.capitalUsd * 0.1, Math.min(this.config.capitalUsd * maxPnlPct, estimatedPnlUsd));

      const newCapital = this.config.capitalUsd + estimatedPnlUsd;
      const cumulativeReturnPct = ((newCapital - this.config.initialCapitalUsd) / this.config.initialCapitalUsd) * 100;

      // Update internal capital tracking
      this.config.capitalUsd = Math.max(0.01, newCapital);

      // === STEP 10: Store compound growth tracker ===
      await this.storeGrowthSnapshot(estimatedPnlUsd, tradeableRecs.length);

      // === STEP 11: Auto-feedback loop + Capital strategy learning ===
      if (this.config.autoFeedback) {
        try {
          await feedbackLoopEngine.validateSignals();
        } catch (error) {
          errors.push(`Feedback loop failed: ${error instanceof Error ? error.message : String(error)}`);
        }
      }

      // === STEP 11.5: Update capital strategy learning (NEW) ===
      // This is where the brain LEARNS from cycle results
      // It adjusts strategy weights, confidence scores, and thresholds for next cycle
      const partialResult: CycleResult = {
        cycleRunId: cycleRun.id,
        cycleNumber: this.currentCycleNumber,
        status: 'COMPLETED',
        tokensScanned: tokens.length,
        tokensOperable: operableResults.length,
        tokensTradeable: tradeableRecs.length,
        operabilityDistribution,
        topPicks: tradeableRecs.slice(0, 10),
        phaseDistribution,
        dominantRegime,
        regimeConfidence,
        capitalBeforeCycle: this.config.capitalUsd - estimatedPnlUsd,
        capitalAfterCycle: this.config.capitalUsd,
        estimatedCyclePnlUsd: estimatedPnlUsd,
        estimatedCyclePnlPct: this.config.capitalUsd > 0 ? (estimatedPnlUsd / this.config.capitalUsd) * 100 : 0,
        cumulativeReturnPct,
        capitalStrategy: capitalStrategyDecision,
        snapshotsStored: operabilityResults.length,
        durationMs: 0,
        errors,
      };

      try {
        await capitalStrategyManager.updateFromCycleResult(partialResult);
      } catch (error) {
        errors.push(`Capital strategy update failed: ${error instanceof Error ? error.message : String(error)}`);
      }

      // === STEP 12: Auto-backfill for top tokens ===
      if (this.config.autoBackfill && operableAddresses.length > 0) {
        try {
          // Backfill top 5 operable tokens (don't want to overload APIs)
          await ohlcvPipeline.backfillTopTokens(Math.min(5, operableAddresses.length));
        } catch (error) {
          // Backfill is best-effort, don't count as error
        }
      }

      // === STEP 13: Update cycle run with results ===
      const topPicksData = tradeableRecs.slice(0, 5).map(rec => ({
        tokenAddress: rec.tokenAddress,
        symbol: rec.symbol,
        primarySystem: rec.primarySystem,
        confidence: rec.config.confidence,
        estimatedGainPct: rec.estimatedGainPct,
        urgencyLevel: rec.urgencyLevel,
      }));

      await db.brainCycleRun.update({
        where: { id: cycleRun.id },
        data: {
          status: 'COMPLETED',
          tokensScanned: tokens.length,
          tokensOperable: operableResults.length,
          tokensTradeable: tradeableRecs.length,
          topPicks: JSON.stringify(topPicksData),
          operabilitySummary: JSON.stringify(operabilityDistribution),
          capitalAfterCycle: this.config.capitalUsd,
          cyclePnlUsd: estimatedPnlUsd,
          cyclePnlPct: this.config.capitalUsd > 0 ? (estimatedPnlUsd / this.config.capitalUsd) * 100 : 0,
          cumulativeReturnPct,
          phaseDistribution: JSON.stringify(phaseDistribution),
          dominantRegime,
          regimeConfidence,
          completedAt: new Date(),
          cycleDurationMs: Date.now() - cycleStart,
          errorLog: errors.length > 0 ? errors.join('; ') : null,
        },
      });

      // Build result
      const result: CycleResult = {
        cycleRunId: cycleRun.id,
        cycleNumber: this.currentCycleNumber,
        status: errors.length > 0 ? 'PARTIAL' : 'COMPLETED',
        tokensScanned: tokens.length,
        tokensOperable: operableResults.length,
        tokensTradeable: tradeableRecs.length,
        operabilityDistribution,
        topPicks: tradeableRecs.slice(0, 10),
        phaseDistribution,
        dominantRegime,
        regimeConfidence,
        capitalBeforeCycle: this.config.capitalUsd - estimatedPnlUsd,
        capitalAfterCycle: this.config.capitalUsd,
        estimatedCyclePnlUsd: estimatedPnlUsd,
        estimatedCyclePnlPct: this.config.capitalUsd > 0 ? (estimatedPnlUsd / this.config.capitalUsd) * 100 : 0,
        cumulativeReturnPct,
        capitalStrategy: capitalStrategyDecision,
        snapshotsStored: operabilityResults.length,
        durationMs: Date.now() - cycleStart,
        errors,
      };

      this.lastCycleResult = result;
      return result;

    } catch (error) {
      // Fatal error in cycle
      const errorMsg = error instanceof Error ? error.message : String(error);
      errors.push(`Fatal: ${errorMsg}`);

      await db.brainCycleRun.update({
        where: { id: cycleRun.id },
        data: {
          status: 'FAILED',
          completedAt: new Date(),
          cycleDurationMs: Date.now() - cycleStart,
          errorLog: errors.join('; '),
        },
      });

      const result: CycleResult = {
        cycleRunId: cycleRun.id,
        cycleNumber: this.currentCycleNumber,
        status: 'FAILED',
        tokensScanned: 0,
        tokensOperable: 0,
        tokensTradeable: 0,
        operabilityDistribution: { PREMIUM: 0, GOOD: 0, MARGINAL: 0, RISKY: 0, UNOPERABLE: 0 },
        topPicks: [],
        phaseDistribution: {},
        dominantRegime: 'SIDEWAYS',
        regimeConfidence: 0,
        capitalBeforeCycle: this.config.capitalUsd,
        capitalAfterCycle: this.config.capitalUsd,
        estimatedCyclePnlUsd: 0,
        estimatedCyclePnlPct: 0,
        cumulativeReturnPct: ((this.config.capitalUsd - this.config.initialCapitalUsd) / this.config.initialCapitalUsd) * 100,
        capitalStrategy: null,
        snapshotsStored: 0,
        durationMs: Date.now() - cycleStart,
        errors,
      };

      this.lastCycleResult = result;
      return result;
    }
  }

  // ============================================================
  // GROWTH TRACKING
  // ============================================================

  /**
   * Almacena un snapshot del crecimiento compuesto del capital.
   */
  private async storeGrowthSnapshot(periodPnlUsd: number, periodTrades: number): Promise<void> {
    try {
      // Load last tracker entry for cumulative values
      const lastTracker = await db.compoundGrowthTracker.findFirst({
        orderBy: { measuredAt: 'desc' },
      });

      const totalPnlUsd = (lastTracker?.totalPnlUsd ?? 0) + periodPnlUsd;
      const totalFeesPaidUsd = lastTracker?.totalFeesPaidUsd ?? 0;
      const totalSlippageUsd = lastTracker?.totalSlippageUsd ?? 0;

      // Estimate fees for this cycle (rough approximation)
      const estimatedFeesThisCycle = Math.abs(periodPnlUsd) * 0.006; // ~0.6% per round trip
      const estimatedSlippageThisCycle = Math.abs(periodPnlUsd) * 0.002; // ~0.2% slippage

      const newTotalFees = totalFeesPaidUsd + estimatedFeesThisCycle;
      const newTotalSlippage = totalSlippageUsd + estimatedSlippageThisCycle;

      const feeAdjustedPnl = totalPnlUsd - newTotalFees - newTotalSlippage;
      const totalReturnPct = this.config.initialCapitalUsd > 0
        ? ((this.config.capitalUsd - this.config.initialCapitalUsd) / this.config.initialCapitalUsd) * 100
        : 0;
      const feeAdjustedReturnPct = this.config.initialCapitalUsd > 0
        ? (feeAdjustedPnl / this.config.initialCapitalUsd) * 100
        : 0;

      // Calculate daily compound rate
      const cyclesSinceStart = this.currentCycleNumber;
      const cyclesPerDay = (24 * 60 * 60 * 1000) / this.config.cycleIntervalMs;
      const dailyCompoundRate = cyclesSinceStart > 0
        ? (Math.pow(this.config.capitalUsd / this.config.initialCapitalUsd, 1 / Math.max(1, cyclesSinceStart / cyclesPerDay)) - 1) * 100
        : 0;

      // Project annual return
      const projectedAnnualReturn = Math.pow(1 + dailyCompoundRate / 100, 365) * 100 - 100;

      // Win rate (based on cycle PnL direction)
      const periodWins = periodPnlUsd > 0 ? Math.ceil(periodTrades * 0.55) : Math.floor(periodTrades * 0.45);
      const totalWins = (lastTracker?.periodWins ?? 0) + periodWins;
      const totalTrades = (lastTracker?.periodTrades ?? 0) + periodTrades;

      await db.compoundGrowthTracker.create({
        data: {
          capitalUsd: this.config.capitalUsd,
          initialCapitalUsd: this.config.initialCapitalUsd,
          totalReturnPct,
          totalPnlUsd,
          periodPnlUsd,
          periodReturnPct: this.config.capitalUsd > 0 ? (periodPnlUsd / this.config.capitalUsd) * 100 : 0,
          periodTrades,
          periodWins,
          periodLosses: Math.max(0, periodTrades - periodWins),
          totalFeesPaidUsd: newTotalFees,
          totalSlippageUsd: newTotalSlippage,
          feeAdjustedPnlUsd: feeAdjustedPnl,
          feeAdjustedReturnPct,
          dailyCompoundRate,
          projectedAnnualReturn,
          winRate: totalTrades > 0 ? totalWins / totalTrades : 0,
          period: '1h',
          measuredAt: new Date(),
        },
      });
    } catch (error) {
      console.warn('[BrainCycle] Growth snapshot failed:', error);
    }
  }

  // ============================================================
  // GROWTH REPORT
  // ============================================================

  /**
   * Genera un reporte completo del crecimiento compuesto.
   */
  async getGrowthReport(): Promise<GrowthReport> {
    const lastTracker = await db.compoundGrowthTracker.findFirst({
      orderBy: { measuredAt: 'desc' },
    });

    const totalCycles = await db.brainCycleRun.count({
      where: { status: 'COMPLETED' },
    });

    // Get all cycle PnLs for average calculation
    const recentCycles = await db.brainCycleRun.findMany({
      where: { status: 'COMPLETED' },
      orderBy: { createdAt: 'desc' },
      take: 100,
      select: { cyclePnlPct: true, cyclePnlUsd: true },
    });

    const avgCyclePnlPct = recentCycles.length > 0
      ? recentCycles.reduce((s, c) => s + c.cyclePnlPct, 0) / recentCycles.length
      : 0;

    const totalPnl = recentCycles.reduce((s, c) => s + c.cyclePnlUsd, 0);
    const winningCycles = recentCycles.filter(c => c.cyclePnlUsd > 0).length;
    const winRate = recentCycles.length > 0 ? winningCycles / recentCycles.length : 0;

    // Calculate max drawdown from growth tracker
    const growthHistory = await db.compoundGrowthTracker.findMany({
      orderBy: { measuredAt: 'asc' },
      take: 1000,
      select: { capitalUsd: true },
    });

    let peak = 0;
    let maxDrawdown = 0;
    for (const entry of growthHistory) {
      if (entry.capitalUsd > peak) peak = entry.capitalUsd;
      const drawdown = peak > 0 ? ((peak - entry.capitalUsd) / peak) * 100 : 0;
      if (drawdown > maxDrawdown) maxDrawdown = drawdown;
    }

    // Sharpe ratio approximation
    const returns = recentCycles.map(c => c.cyclePnlPct);
    const avgReturn = returns.length > 0 ? returns.reduce((s, r) => s + r, 0) / returns.length : 0;
    const variance = returns.length > 1
      ? returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / (returns.length - 1)
      : 0;
    const stdDev = Math.sqrt(variance);
    const sharpeRatio = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(365) : 0; // Annualized

    return {
      currentCapital: this.config.capitalUsd,
      initialCapital: this.config.initialCapitalUsd,
      totalReturnPct: lastTracker?.totalReturnPct ?? 0,
      totalPnlUsd: lastTracker?.totalPnlUsd ?? 0,
      feeAdjustedPnlUsd: lastTracker?.feeAdjustedPnlUsd ?? 0,
      feeAdjustedReturnPct: lastTracker?.feeAdjustedReturnPct ?? 0,
      totalFeesPaidUsd: lastTracker?.totalFeesPaidUsd ?? 0,
      totalSlippageUsd: lastTracker?.totalSlippageUsd ?? 0,
      dailyCompoundRate: lastTracker?.dailyCompoundRate ?? 0,
      projectedAnnualReturn: lastTracker?.projectedAnnualReturn ?? 0,
      cyclesCompleted: totalCycles,
      averageCyclePnlPct: avgCyclePnlPct,
      winRate,
      sharpeRatio,
      maxDrawdownPct: maxDrawdown,
    };
  }

  // ============================================================
  // HELPER
  // ============================================================

  private buildEmptyResult(cycleRunId: string, reason: string): CycleResult {
    return {
      cycleRunId,
      cycleNumber: this.currentCycleNumber,
      status: 'COMPLETED',
      tokensScanned: 0,
      tokensOperable: 0,
      tokensTradeable: 0,
      operabilityDistribution: { PREMIUM: 0, GOOD: 0, MARGINAL: 0, RISKY: 0, UNOPERABLE: 0 },
      topPicks: [],
      phaseDistribution: {},
      dominantRegime: 'SIDEWAYS',
      regimeConfidence: 0,
      capitalBeforeCycle: this.config.capitalUsd,
      capitalAfterCycle: this.config.capitalUsd,
      estimatedCyclePnlUsd: 0,
      estimatedCyclePnlPct: 0,
      cumulativeReturnPct: ((this.config.capitalUsd - this.config.initialCapitalUsd) / this.config.initialCapitalUsd) * 100,
      capitalStrategy: null,
      snapshotsStored: 0,
      durationMs: 0,
      errors: [reason],
    };
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const brainCycleEngine = new BrainCycleEngine();
