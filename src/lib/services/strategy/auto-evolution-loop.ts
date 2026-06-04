/**
 * Auto Evolution Loop - CryptoQuant Terminal
 *
 * Cycle-based evolution loop that:
 *   - Runs N discrete, independent cycles (default: 5)
 *   - Each cycle: SCAN → GENERATE → BACKTEST → EVALUATE → SAVE → EVOLVE
 *   - Each cycle persists its state to the EvolutionCycle DB model
 *   - If a cycle fails, previous cycles' results are preserved
 *   - On restart, can resume from the last completed cycle
 *   - Can be started/stopped/status-checked via API
 *
 * Key design: each cycle is an atomic unit of work. Progress is never lost.
 */

import { strategyEvolutionEngine, DEFAULT_EVOLUTION_CONFIG, type EvolutionConfig } from './strategy-evolution-engine';
import { strategyStateManager } from './strategy-state-manager';
import { dexScreenerClient } from '@/lib/services/data-sources/dexscreener-client';
import { db } from '../../db';

// ============================================================
// TYPES
// ============================================================

export interface AutoEvolutionConfig {
  /** Number of cycles to run (default: 5) */
  totalCycles: number;
  /** Interval in milliseconds between cycles (default: 5 minutes) */
  intervalMs: number;
  /** Evolution config for the strategy-evolution-engine */
  evolutionConfig: EvolutionConfig;
  /** Minimum Sharpe ratio to auto-activate a strategy for paper trading */
  minSharpeRatio: number;
  /** Minimum win rate to auto-activate a strategy for paper trading */
  minWinRate: number;
  /** Maximum number of concurrent paper trading positions */
  maxConcurrentPositions: number;
  /** Position size in USD for auto-trades */
  positionSizeUsd: number;
  /** Enable trailing stop monitoring */
  enableTrailingStop: boolean;
  /** Enable time-based exit monitoring */
  enableTimeBasedExit: boolean;
  /** Maximum hold time in minutes before time-based exit */
  maxHoldTimeMin: number;
}

export const DEFAULT_AUTO_EVOLUTION_CONFIG: AutoEvolutionConfig = {
  totalCycles: 5,
  intervalMs: 5 * 60 * 1000, // 5 minutes
  evolutionConfig: DEFAULT_EVOLUTION_CONFIG,
  minSharpeRatio: 0.5,
  minWinRate: 0.4,
  maxConcurrentPositions: 5,
  positionSizeUsd: 100,
  enableTrailingStop: true,
  enableTimeBasedExit: true,
  maxHoldTimeMin: 1440, // 24 hours
};

export interface AutoEvolutionStatus {
  isRunning: boolean;
  currentCycle: number;
  totalCycles: number;
  currentPhase: string;
  lastCycleAt: Date | null;
  lastError: string | null;
  startedAt: Date | null;
  config: AutoEvolutionConfig;
  lastCycleResult: AutoEvolutionCycleResult | null;
  activeStrategies: string[];
  totalPaperTrades: number;
  totalExitsProcessed: number;
  totalEvolutions: number;
  completedCycles: AutoEvolutionCycleResult[];
  runId: string | null;
  stopRequested: boolean;
}

export interface AutoEvolutionCycleResult {
  cycleNumber: number;
  runId: string;
  timestamp: Date;
  phase: string;
  evolutionResult: {
    improved: number;
    degraded: number;
    totalMutations: number;
    bestScore: number;
  } | null;
  strategiesActivated: string[];
  entriesExecuted: string[];
  exitsProcessed: Array<{
    backtestId: string;
    exitReason: string;
    pnlUsd: number;
  }>;
  errors: string[];
  cycleDbId: string | null;
}

// ============================================================
// AUTO EVOLUTION LOOP CLASS
// ============================================================

class AutoEvolutionLoop {
  private intervalHandle: ReturnType<typeof setInterval> | null = null;
  private isRunning = false;
  private currentCycle = 0;
  private totalCycles = 5;
  private currentPhase = 'IDLE';
  private lastCycleAt: Date | null = null;
  private lastError: string | null = null;
  private startedAt: Date | null = null;
  private config: AutoEvolutionConfig = { ...DEFAULT_AUTO_EVOLUTION_CONFIG };
  private lastCycleResult: AutoEvolutionCycleResult | null = null;
  private totalPaperTrades = 0;
  private totalExitsProcessed = 0;
  private totalEvolutions = 0;
  private activeStrategies: Set<string> = new Set();
  private completedCycles: AutoEvolutionCycleResult[] = [];
  private runId: string | null = null;
  private stopRequested = false;
  private isCycleRunning = false;
  private consecutiveNoImproveCycles = 0;

  /**
   * Start the auto-evolution loop with N discrete cycles.
   * If a previous run was interrupted, resume from the last completed cycle.
   */
  async start(config?: Partial<AutoEvolutionConfig>): Promise<void> {
    if (this.isRunning) {
      console.warn('[AutoEvolution] Already running, ignoring start request');
      return;
    }

    this.config = { ...DEFAULT_AUTO_EVOLUTION_CONFIG, ...config };
    this.totalCycles = this.config.totalCycles;
    this.isRunning = true;
    this.stopRequested = false;
    this.startedAt = new Date();
    this.currentPhase = 'INITIALIZING';
    this.lastError = null;
    this.completedCycles = [];

    // Generate a unique run ID
    this.runId = `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    console.log(
      `[AutoEvolution] Starting cycle-based loop: ${this.totalCycles} cycles, ` +
      `interval ${this.config.intervalMs / 1000}s, ` +
      `minSharpe=${this.config.minSharpeRatio}, minWinRate=${this.config.minWinRate}`
    );

    // Try to resume from a previous interrupted run
    const resumedFrom = await this.tryResume();
    if (resumedFrom > 0) {
      console.log(`[AutoEvolution] Resumed from cycle ${resumedFrom}, running cycles ${resumedFrom + 1} to ${this.totalCycles}`);
    }

    // Run the first cycle immediately
    await this.runNextCycle();

    // If more cycles remain and not stopped, schedule them
    if (this.currentCycle < this.totalCycles && !this.stopRequested) {
      this.scheduleNextCycle();
    }
  }

  /**
   * Stop after current cycle completes gracefully.
   */
  stop(): void {
    if (!this.isRunning) {
      console.warn('[AutoEvolution] Not running, ignoring stop request');
      return;
    }

    this.stopRequested = true;
    console.log('[AutoEvolution] Stop requested — will finish current cycle and then stop');

    if (!this.isCycleRunning && this.intervalHandle) {
      // If no cycle is currently running, stop immediately
      clearInterval(this.intervalHandle);
      this.intervalHandle = null;
      this.isRunning = false;
      this.currentPhase = 'STOPPED';
      console.log(
        `[AutoEvolution] Stopped after ${this.currentCycle} cycles. ` +
        `Total: ${this.totalEvolutions} evolutions, ${this.totalPaperTrades} trades, ${this.totalExitsProcessed} exits`
      );
    }
  }

  /**
   * Get the current status of the auto-evolution loop.
   */
  getStatus(): AutoEvolutionStatus {
    return {
      isRunning: this.isRunning,
      currentCycle: this.currentCycle,
      totalCycles: this.totalCycles,
      currentPhase: this.currentPhase,
      lastCycleAt: this.lastCycleAt,
      lastError: this.lastError,
      startedAt: this.startedAt,
      config: { ...this.config },
      lastCycleResult: this.lastCycleResult,
      activeStrategies: Array.from(this.activeStrategies),
      totalPaperTrades: this.totalPaperTrades,
      totalExitsProcessed: this.totalExitsProcessed,
      totalEvolutions: this.totalEvolutions,
      completedCycles: this.completedCycles,
      runId: this.runId,
      stopRequested: this.stopRequested,
    };
  }

  /**
   * Try to resume from a previous interrupted run.
   * Returns the cycle number to resume from (0 if no previous run found).
   */
  private async tryResume(): Promise<number> {
    try {
      // Find the latest run that has incomplete cycles (regardless of runId,
      // since this.runId is freshly generated and won't match previous runs)
      const latestCycle = await db.evolutionCycle.findFirst({
        orderBy: { startedAt: 'desc' },
      });

      if (!latestCycle) return 0;

      // Adopt the previous run's ID so we continue in the same run
      const previousRunId = latestCycle.runId;

      if (latestCycle.status === 'COMPLETED') {
        // Check if this run has all cycles completed
        const completedCount = await db.evolutionCycle.count({
          where: { runId: previousRunId, status: 'COMPLETED' },
        });

        // Find the total cycles from config or infer from cycle numbers
        const maxCycleNumber = await db.evolutionCycle.aggregate({
          where: { runId: previousRunId },
          _max: { cycleNumber: true },
        });

        const totalCyclesInRun = maxCycleNumber._max.cycleNumber ?? 0;

        if (completedCount >= totalCyclesInRun && totalCyclesInRun > 0) {
          // All cycles completed — nothing to resume
          return 0;
        }

        // Resume from the next cycle after the last completed one
        this.currentCycle = latestCycle.cycleNumber;
        this.runId = previousRunId;

        // Load completed cycle results from DB
        const completedCycles = await db.evolutionCycle.findMany({
          where: { runId: previousRunId, status: 'COMPLETED' },
          orderBy: { cycleNumber: 'asc' },
        });

        for (const cycle of completedCycles) {
          this.completedCycles.push(this.cycleDbToResult(cycle));
        }

        // Restore counters from DB data
        this.totalPaperTrades = completedCycles.reduce((sum, c) => {
          try { return sum + (JSON.parse(c.entriesExecuted as string || '[]') as unknown[]).length; } catch { return sum; }
        }, 0);
        this.totalExitsProcessed = completedCycles.reduce((sum, c) => {
          try { return sum + (JSON.parse(c.exitsProcessed as string || '[]') as unknown[]).length; } catch { return sum; }
        }, 0);
        this.totalEvolutions = completedCycles.filter(c => c.evaluationResults && c.evaluationResults !== '{}').length;

        console.log(`[AutoEvolution] Resuming run ${previousRunId} from cycle ${this.currentCycle + 1}`);
        return this.currentCycle;
      }

      if (latestCycle.status === 'RUNNING') {
        // The previous cycle was interrupted — mark it as failed
        await db.evolutionCycle.update({
          where: { id: latestCycle.id },
          data: {
            status: 'FAILED',
            errorLog: 'Process interrupted during cycle execution',
            completedAt: new Date(),
          },
        });

        this.currentCycle = latestCycle.cycleNumber;
        this.runId = previousRunId;

        // Load completed cycles before the failed one
        const completedCycles = await db.evolutionCycle.findMany({
          where: { runId: previousRunId, status: 'COMPLETED' },
          orderBy: { cycleNumber: 'asc' },
        });

        for (const cycle of completedCycles) {
          this.completedCycles.push(this.cycleDbToResult(cycle));
        }

        // Restore counters from DB data
        this.totalPaperTrades = completedCycles.reduce((sum, c) => {
          try { return sum + (JSON.parse(c.entriesExecuted as string || '[]') as unknown[]).length; } catch { return sum; }
        }, 0);
        this.totalExitsProcessed = completedCycles.reduce((sum, c) => {
          try { return sum + (JSON.parse(c.exitsProcessed as string || '[]') as unknown[]).length; } catch { return sum; }
        }, 0);
        this.totalEvolutions = completedCycles.filter(c => c.evaluationResults && c.evaluationResults !== '{}').length;

        console.log(`[AutoEvolution] Resuming interrupted run ${previousRunId} from cycle ${this.currentCycle + 1}`);
        return this.currentCycle;
      }

      return 0;
    } catch (err) {
      console.warn('[AutoEvolution] Could not check for previous runs, starting fresh:', err);
      return 0;
    }
  }

  /**
   * Schedule the next cycle run.
   */
  private scheduleNextCycle(): void {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle);
    }

    this.intervalHandle = setInterval(async () => {
      if (this.stopRequested) {
        if (this.intervalHandle) {
          clearInterval(this.intervalHandle);
          this.intervalHandle = null;
        }
        this.isRunning = false;
        this.currentPhase = 'STOPPED';
        console.log('[AutoEvolution] Stopped after stop request');
        return;
      }

      if (this.currentCycle < this.totalCycles) {
        await this.runNextCycle();

        // Check if we should stop after this cycle
        if (this.currentCycle >= this.totalCycles || this.stopRequested) {
          if (this.intervalHandle) {
            clearInterval(this.intervalHandle);
            this.intervalHandle = null;
          }
          this.isRunning = false;
          this.currentPhase = 'COMPLETED';
          console.log(
            `[AutoEvolution] All ${this.currentCycle} cycles completed. ` +
            `Total: ${this.totalEvolutions} evolutions, ${this.totalPaperTrades} trades, ${this.totalExitsProcessed} exits`
          );
        }
      }
    }, this.config.intervalMs);
  }

  /**
   * Run the next discrete cycle.
   * Each cycle is: SCAN → GENERATE → BACKTEST → EVALUATE → SAVE → EVOLVE
   * All state is persisted to DB so no progress is lost on failure.
   */
  private async runNextCycle(): Promise<void> {
    if (this.isCycleRunning) {
      console.warn('[AutoEvolution] Cycle already running, skipping');
      return;
    }

    this.isCycleRunning = true;
    const cycleNumber = this.currentCycle + 1;

    const cycleResult: AutoEvolutionCycleResult = {
      cycleNumber,
      runId: this.runId!,
      timestamp: new Date(),
      phase: 'SCAN',
      evolutionResult: null,
      strategiesActivated: [],
      entriesExecuted: [],
      exitsProcessed: [],
      errors: [],
      cycleDbId: null,
    };

    console.log(`[AutoEvolution] ═══════════════════════════════════════════`);
    console.log(`[AutoEvolution] Starting Cycle ${cycleNumber}/${this.totalCycles}`);

    // Create a DB record for this cycle at the start
    let cycleDbId: string | null = null;
    try {
      const cycleRecord = await db.evolutionCycle.create({
        data: {
          cycleNumber,
          runId: this.runId!,
          currentPhase: 'SCAN',
          status: 'RUNNING',
          configSnapshot: JSON.stringify(this.config),
        },
      });
      cycleDbId = cycleRecord.id;
      cycleResult.cycleDbId = cycleDbId;
    } catch (err) {
      console.error('[AutoEvolution] Failed to create cycle DB record:', err);
    }

    try {
      // ═══════════════════════════════════════════════════════════
      // PHASE 1: SCAN — Find top tokens from DexScreener/DB
      // ═══════════════════════════════════════════════════════════
      const scanStart = Date.now();
      this.currentPhase = 'SCAN';
      cycleResult.phase = 'SCAN';

      let tokensScanned = 0;
      let topTokenAddresses: string[] = [];

      try {
        // ═══ Try DexScreener for fresh data first ═══
        const freshTokens = await this.fetchFreshTokens();

        if (freshTokens.length > 0) {
          tokensScanned = freshTokens.length;
          topTokenAddresses = freshTokens.map(t => t.address);
          console.log(`[AutoEvolution] SCAN: Found ${tokensScanned} fresh tokens from DexScreener+DB`);
        } else {
          // Fallback: DB-only scan
          const topTokens = await db.token.findMany({
            where: { volume24h: { gt: 0 }, liquidity: { gt: 0 } },
            orderBy: { volume24h: 'desc' },
            take: 20,
            select: { address: true, symbol: true, volume24h: true },
          });

          tokensScanned = topTokens.length;
          topTokenAddresses = topTokens.map(t => t.address);
          console.log(`[AutoEvolution] SCAN: Found ${tokensScanned} tokens from DB fallback`);
        }
      } catch (err) {
        const errMsg = `SCAN error: ${err instanceof Error ? err.message : String(err)}`;
        cycleResult.errors.push(errMsg);
        console.error(`[AutoEvolution] ${errMsg}`);
      }

      const scanDurationMs = Date.now() - scanStart;

      // Update DB with scan results
      await this.updateCyclePhase(cycleDbId, {
        currentPhase: 'GENERATE',
        tokensScanned,
        topTokenAddresses: JSON.stringify(topTokenAddresses),
        scanDurationMs,
      });

      // ═══════════════════════════════════════════════════════════
      // PHASE 2: GENERATE — Create strategy variants from seeds
      // ═══════════════════════════════════════════════════════════
      const generateStart = Date.now();
      this.currentPhase = 'GENERATE';
      cycleResult.phase = 'GENERATE';

      let variantsGenerated = 0;
      let seedSystemIds: string[] = [];

      try {
        // ═══ Bootstrap check: if no completed backtests exist, create initial ones ═══
        const hasBacktests = await this.hasCompletedBacktests();
        if (!hasBacktests) {
          console.log('[AutoEvolution] BOOTSTRAP: No completed backtests found — running bootstrap pipeline');
          try {
            await this.runBootstrapPipeline(cycleResult);
          } catch (err) {
            const errMsg = `Bootstrap pipeline error: ${err instanceof Error ? err.message : String(err)}`;
            cycleResult.errors.push(errMsg);
            console.error(`[AutoEvolution] BOOTSTRAP: ${errMsg}`);
          }
        }

        // Use seeds from previous cycle if available
        if (cycleNumber > 1 && this.completedCycles.length > 0) {
          // Get the system IDs from the previous cycle's best strategies
          const prevBest = await db.backtestRun.findMany({
            where: {
              status: 'COMPLETED',
              totalTrades: { gt: 0 },
              sharpeRatio: { gte: this.config.minSharpeRatio },
            },
            orderBy: { sharpeRatio: 'desc' },
            take: this.config.evolutionConfig.topN,
            select: { systemId: true },
          });
          seedSystemIds = prevBest.map(s => s.systemId).filter(Boolean) as string[];
          console.log(`[AutoEvolution] GENERATE: Using ${seedSystemIds.length} seeds from previous cycles`);
        }

        // Also add any currently active strategies as seeds
        for (const activeId of this.activeStrategies) {
          if (!seedSystemIds.includes(activeId)) {
            seedSystemIds.push(activeId);
          }
        }

        // ═══ DIVERSITY INJECTION: Every 3rd cycle, inject fresh strategy variants ═══
        // The auto-evolution loop tends to only mutate existing strategies, which can
        // lead to premature convergence. Periodically creating new strategies from
        // templates ensures diversity in the gene pool.
        if (cycleNumber % 3 === 0 && topTokenAddresses.length > 0) {
          console.log(`[AutoEvolution] GENERATE: DIVERSITY INJECTION — creating fresh strategy variants for cycle ${cycleNumber}`);
          try {
            const freshVariantIds = await this.injectFreshStrategies(topTokenAddresses);
            if (freshVariantIds.length > 0) {
              seedSystemIds.push(...freshVariantIds);
              console.log(`[AutoEvolution] GENERATE: Injected ${freshVariantIds.length} fresh strategy variants`);
            }
          } catch (err) {
            const errMsg = `Diversity injection error: ${err instanceof Error ? err.message : String(err)}`;
            cycleResult.errors.push(errMsg);
            console.warn(`[AutoEvolution] ${errMsg}`);
          }
        }

        variantsGenerated = seedSystemIds.length;

        // Pass seeds to evolution config for the BACKTEST phase
        this.config.evolutionConfig.seedSystemIds = seedSystemIds.length > 0 ? seedSystemIds : undefined;
      } catch (err) {
        const errMsg = `GENERATE error: ${err instanceof Error ? err.message : String(err)}`;
        cycleResult.errors.push(errMsg);
        console.error(`[AutoEvolution] ${errMsg}`);
      }

      const generateDurationMs = Date.now() - generateStart;

      // Update DB with generation results
      await this.updateCyclePhase(cycleDbId, {
        currentPhase: 'BACKTEST',
        variantsGenerated,
        generateDurationMs,
      });

      // ═══════════════════════════════════════════════════════════
      // PHASE 3: BACKTEST — Run backtests via evolution engine
      // ═══════════════════════════════════════════════════════════
      const backtestStart = Date.now();
      this.currentPhase = 'BACKTEST';
      cycleResult.phase = 'BACKTEST';

      let evolutionResult: AutoEvolutionCycleResult['evolutionResult'] = null;
      let backtestsRun = 0;
      let backtestIds: string[] = [];
      let variantIds: string[] = [];
      let improvedCount = 0;
      let degradedCount = 0;
      let totalMutations = 0;
      let bestScore = 0;
      let bestSystemId: string | null = null;
      let bestBacktestId: string | null = null;

      try {
        const evoResult = await strategyEvolutionEngine.runEvolution(
          this.config.evolutionConfig,
        );

        evolutionResult = {
          improved: evoResult.improved,
          degraded: evoResult.degraded,
          totalMutations: evoResult.totalMutations,
          bestScore: evoResult.bestScore,
        };

        backtestsRun = evoResult.allStrategies.length;
        backtestIds = evoResult.allStrategies.map(s => s.backtestId);
        variantIds = evoResult.allStrategies.map(s => s.systemId);
        improvedCount = evoResult.improved;
        degradedCount = evoResult.degraded;
        totalMutations = evoResult.totalMutations;
        bestScore = evoResult.bestScore;
        bestSystemId = evoResult.bestStrategy?.systemId ?? null;
        bestBacktestId = evoResult.bestStrategy?.backtestId ?? null;

        this.totalEvolutions++;

        console.log(
          `[AutoEvolution] BACKTEST complete: ${evoResult.improved} improved, ` +
          `${evoResult.degraded} degraded out of ${evoResult.totalMutations} mutations, ` +
          `bestScore=${evoResult.bestScore.toFixed(1)}`
        );

        cycleResult.evolutionResult = evolutionResult;

        // ═══════════════════════════════════════════════════════════
        // Auto-activate improved strategies for paper trading
        // ═══════════════════════════════════════════════════════════
        if (evoResult.bestStrategy) {
          const best = evoResult.bestStrategy;

          if (best.sharpeRatio >= this.config.minSharpeRatio && best.winRate >= this.config.minWinRate) {
            console.log(
              `[AutoEvolution] Best strategy qualifies for paper trading: ` +
              `sharpe=${best.sharpeRatio.toFixed(2)}, winRate=${best.winRate.toFixed(2)}, score=${best.score.toFixed(1)}`
            );

            try {
              const activated = await this.activateStrategyForPaperTrading(best.systemId, best);
              if (activated) {
                cycleResult.strategiesActivated.push(best.systemId);
                this.activeStrategies.add(best.systemId);

                await strategyStateManager.recordStateTransition({
                  systemId: best.systemId,
                  newStatus: 'PAPER_TRADING',
                  triggerReason: 'AUTO_EVOLVE',
                  metrics: {
                    sharpeRatio: best.sharpeRatio,
                    winRate: best.winRate,
                    totalPnlPct: best.pnlPct,
                    totalTrades: best.totalTrades,
                  },
                  evolution: {
                    generation: best.generation,
                    parentId: best.parentId,
                    improvementPct: best.improvement,
                  },
                  metadata: {
                    event: 'auto_evolution_activation',
                    cycleNumber,
                    score: best.score,
                    mutations: best.mutations,
                  },
                });
              }

              if (activated && this.activeStrategies.size < this.config.maxConcurrentPositions) {
                const entryResult = await this.autoExecuteEntry(best.systemId, best.backtestId);
                if (entryResult) {
                  cycleResult.entriesExecuted.push(entryResult);
                  this.totalPaperTrades++;
                }
              }
            } catch (err) {
              const errMsg = `Failed to activate strategy ${best.systemId}: ${err instanceof Error ? err.message : String(err)}`;
              cycleResult.errors.push(errMsg);
              console.error(`[AutoEvolution] ${errMsg}`);
            }
          } else {
            console.log(
              `[AutoEvolution] Best strategy does not meet thresholds: ` +
              `sharpe=${best.sharpeRatio.toFixed(2)} (need ${this.config.minSharpeRatio}), ` +
              `winRate=${best.winRate.toFixed(2)} (need ${this.config.minWinRate})`
            );
          }
        }

        // Also activate other improved strategies that meet thresholds
        const otherImproved = evoResult.allStrategies.filter(
          (s) =>
            s.status === 'improved' &&
            s.sharpeRatio >= this.config.minSharpeRatio &&
            s.winRate >= this.config.minWinRate &&
            s.systemId !== evoResult.bestStrategy?.systemId,
        );

        for (const strategy of otherImproved) {
          if (this.activeStrategies.size >= this.config.maxConcurrentPositions) break;

          try {
            const activated = await this.activateStrategyForPaperTrading(strategy.systemId, strategy);
            if (activated) {
              cycleResult.strategiesActivated.push(strategy.systemId);
              this.activeStrategies.add(strategy.systemId);

              await strategyStateManager.recordStateTransition({
                systemId: strategy.systemId,
                newStatus: 'PAPER_TRADING',
                triggerReason: 'AUTO_EVOLVE',
                metrics: {
                  sharpeRatio: strategy.sharpeRatio,
                  winRate: strategy.winRate,
                  totalPnlPct: strategy.pnlPct,
                  totalTrades: strategy.totalTrades,
                },
                evolution: {
                  generation: strategy.generation,
                  parentId: strategy.parentId,
                  improvementPct: strategy.improvement,
                },
                metadata: {
                  event: 'auto_evolution_activation',
                  cycleNumber,
                  score: strategy.score,
                },
              });

              const entryResult = await this.autoExecuteEntry(strategy.systemId, strategy.backtestId);
              if (entryResult) {
                cycleResult.entriesExecuted.push(entryResult);
                this.totalPaperTrades++;
              }
            }
          } catch (err) {
            const errMsg = `Failed to activate strategy ${strategy.systemId}: ${err instanceof Error ? err.message : String(err)}`;
            cycleResult.errors.push(errMsg);
            console.error(`[AutoEvolution] ${errMsg}`);
          }
        }
      } catch (err) {
        const errMsg = `Evolution engine error: ${err instanceof Error ? err.message : String(err)}`;
        cycleResult.errors.push(errMsg);
        console.error(`[AutoEvolution] ${errMsg}`);
      }

      const backtestDurationMs = Date.now() - backtestStart;

      // Update DB with backtest results
      await this.updateCyclePhase(cycleDbId, {
        currentPhase: 'EVALUATE',
        variantsGenerated: variantIds.length,
        variantIds: JSON.stringify(variantIds),
        backtestsRun,
        backtestIds: JSON.stringify(backtestIds),
        bestScore,
        bestSystemId,
        bestBacktestId,
        improvedCount,
        degradedCount,
        totalMutations,
        evaluationResults: JSON.stringify(evolutionResult || {}),
        generateDurationMs,
        backtestDurationMs,
      });

      // ═══════════════════════════════════════════════════════════
      // PHASE 4: EVALUATE — Compare against previous cycles
      // ═══════════════════════════════════════════════════════════
      const evaluateStart = Date.now();
      this.currentPhase = 'EVALUATE';
      cycleResult.phase = 'EVALUATE';

      // Compare current best score with previous cycles
      try {
        const previousBestScore = this.completedCycles.length > 0
          ? Math.max(...this.completedCycles.map(c => c.evolutionResult?.bestScore ?? 0))
          : 0;

        if (bestScore > 0 && previousBestScore > 0) {
          const improvementVsPrev = bestScore - previousBestScore;
          if (improvementVsPrev > 0) {
            console.log(
              `[AutoEvolution] EVALUATE: Score improved by +${improvementVsPrev.toFixed(1)} ` +
              `vs previous best (${previousBestScore.toFixed(1)} → ${bestScore.toFixed(1)})`
            );
          } else if (improvementVsPrev < 0) {
            console.log(
              `[AutoEvolution] EVALUATE: Score degraded by ${improvementVsPrev.toFixed(1)} ` +
              `vs previous best (${previousBestScore.toFixed(1)} → ${bestScore.toFixed(1)})`
            );
          } else {
            console.log(`[AutoEvolution] EVALUATE: Score unchanged at ${bestScore.toFixed(1)}`);
          }
        } else if (bestScore > 0) {
          console.log(`[AutoEvolution] EVALUATE: First cycle best score = ${bestScore.toFixed(1)}`);
        }
      } catch (err) {
        const errMsg = `EVALUATE error: ${err instanceof Error ? err.message : String(err)}`;
        cycleResult.errors.push(errMsg);
      }

      const evaluateDurationMs = Date.now() - evaluateStart;

      await this.updateCyclePhase(cycleDbId, {
        currentPhase: 'SAVE',
        evaluateDurationMs,
      });

      // ═══════════════════════════════════════════════════════════
      // Early stopping: if 3 consecutive cycles show no improvement, stop
      // ═══════════════════════════════════════════════════════════
      const previousBestScore = this.completedCycles.length > 0
        ? Math.max(...this.completedCycles.map(c => c.evolutionResult?.bestScore ?? 0))
        : 0;

      if (bestScore > previousBestScore && bestScore > 0) {
        this.consecutiveNoImproveCycles = 0;
      } else {
        this.consecutiveNoImproveCycles++;
      }

      if (this.consecutiveNoImproveCycles >= 3) {
        console.log(
          `[AutoEvolution] Early stopping: ${this.consecutiveNoImproveCycles} consecutive cycles without improvement. ` +
          `Stopping after cycle ${cycleNumber}.`
        );
        // Mark as completed and set stopRequested to prevent more cycles
        this.stopRequested = true;
      }

      // ═══════════════════════════════════════════════════════════
      // PHASE 5: SAVE — Save best results to Hall of Fame (AIBestStrategy)
      // ═══════════════════════════════════════════════════════════
      const saveStart = Date.now();
      this.currentPhase = 'SAVE';
      cycleResult.phase = 'SAVE';

      let strategiesSavedToHof = 0;
      let hofStrategyIds: string[] = [];

      try {
        if (bestSystemId && bestBacktestId) {
          const system = await db.tradingSystem.findUnique({ where: { id: bestSystemId } });
          const backtest = await db.backtestRun.findUnique({ where: { id: bestBacktestId } });

          if (system && backtest) {
            // Parse strategy metadata from strategyMeta JSON (proper field), fallback to capitalAllocation (legacy)
            let strategyMeta: Record<string, unknown> = {};
            try { strategyMeta = JSON.parse(backtest.strategyMeta || '{}'); } catch { /* ignore */ }
            if (Object.keys(strategyMeta).length === 0) {
              try {
                const capAlloc = JSON.parse(backtest.capitalAllocation || '{}');
                if (capAlloc.strategyName || capAlloc.category) strategyMeta = capAlloc;
              } catch { /* ignore */ }
            }

            // Use upsert to avoid duplicate constraint violations on backtestId
            const hofEntry = await db.aIBestStrategy.upsert({
              where: { backtestId: bestBacktestId },
              update: {
                strategyName: system.name,
                category: system.category,
                timeframe: system.primaryTimeframe,
                tokenAgeCategory: (strategyMeta.tokenAgeCategory as string) || 'GROWTH',
                riskTolerance: (strategyMeta.riskTolerance as string) || 'MODERATE',
                capitalAllocation: this.config.positionSizeUsd,
                pnlPct: backtest.totalPnlPct,
                pnlUsd: backtest.totalPnl,
                sharpeRatio: backtest.sharpeRatio,
                winRate: backtest.winRate,
                maxDrawdownPct: backtest.maxDrawdownPct,
                profitFactor: backtest.profitFactor,
                totalTrades: backtest.totalTrades,
                avgHoldTimeMin: backtest.avgHoldTimeMin,
                score: bestScore,
                isActive: true,
              },
              create: {
                strategyName: system.name,
                category: system.category,
                timeframe: system.primaryTimeframe,
                tokenAgeCategory: (strategyMeta.tokenAgeCategory as string) || 'GROWTH',
                riskTolerance: (strategyMeta.riskTolerance as string) || 'MODERATE',
                capitalAllocation: this.config.positionSizeUsd,
                pnlPct: backtest.totalPnlPct,
                pnlUsd: backtest.totalPnl,
                sharpeRatio: backtest.sharpeRatio,
                winRate: backtest.winRate,
                maxDrawdownPct: backtest.maxDrawdownPct,
                profitFactor: backtest.profitFactor,
                totalTrades: backtest.totalTrades,
                avgHoldTimeMin: backtest.avgHoldTimeMin,
                score: bestScore,
                backtestId: bestBacktestId,
                isActive: true,
              },
            });

            // Deactivate previous HoF entries for the same system (superseded)
            try {
              await db.aIBestStrategy.updateMany({
                where: {
                  strategyName: system.name,
                  isActive: true,
                  id: { not: hofEntry.id },
                },
                data: { isActive: false },
              });
            } catch { /* non-critical */ }

            strategiesSavedToHof = 1;
            hofStrategyIds = [hofEntry.id];

            console.log(
              `[AutoEvolution] SAVE: Saved to Hall of Fame — ${system.name} ` +
              `(score: ${bestScore.toFixed(1)}, sharpe: ${backtest.sharpeRatio.toFixed(2)})`
            );
          }
        }
      } catch (err) {
        const errMsg = `Hall of Fame save error: ${err instanceof Error ? err.message : String(err)}`;
        cycleResult.errors.push(errMsg);
        console.error(`[AutoEvolution] ${errMsg}`);
      }

      const saveDurationMs = Date.now() - saveStart;

      await this.updateCyclePhase(cycleDbId, {
        currentPhase: 'EVOLVE',
        strategiesSavedToHof,
        hofStrategyIds: JSON.stringify(hofStrategyIds),
        strategiesActivated: JSON.stringify(cycleResult.strategiesActivated),
        entriesExecuted: JSON.stringify(cycleResult.entriesExecuted),
        saveDurationMs,
      });

      // ═══════════════════════════════════════════════════════════
      // PHASE 6: EVOLVE — Prepare mutations for next cycle
      // ═══════════════════════════════════════════════════════════
      const evolveStart = Date.now();
      this.currentPhase = 'EVOLVE';
      cycleResult.phase = 'EVOLVE';

      // Monitor and exit open positions
      try {
        const exitResults = await this.monitorAndExitPositions();
        cycleResult.exitsProcessed = exitResults;
        this.totalExitsProcessed += exitResults.length;

        await this.updateCyclePhase(cycleDbId, {
          exitsProcessed: JSON.stringify(exitResults),
        });
      } catch (err) {
        const errMsg = `Position monitoring error: ${err instanceof Error ? err.message : String(err)}`;
        cycleResult.errors.push(errMsg);
        console.error(`[AutoEvolution] ${errMsg}`);
      }

      // Clean up stale strategies
      await this.cleanupStaleActiveStrategies();

      // Identify seed strategies for next cycle
      let mutationsPrepared = 0;
      let nextCycleSeedIds: string[] = [];

      try {
        // Get the top improved strategies from this cycle to use as seeds for next
        const topImprovedSystems = await db.backtestRun.findMany({
          where: {
            status: 'COMPLETED',
            totalTrades: { gt: 0 },
            sharpeRatio: { gte: this.config.minSharpeRatio },
          },
          orderBy: { sharpeRatio: 'desc' },
          take: this.config.evolutionConfig.topN,
          select: { systemId: true },
        });

        nextCycleSeedIds = topImprovedSystems.map(s => s.systemId);
        mutationsPrepared = nextCycleSeedIds.length;

        console.log(`[AutoEvolution] EVOLVE: Prepared ${mutationsPrepared} seeds for next cycle`);
      } catch (err) {
        const errMsg = `Evolve phase error: ${err instanceof Error ? err.message : String(err)}`;
        cycleResult.errors.push(errMsg);
        console.error(`[AutoEvolution] ${errMsg}`);
      }

      const evolveDurationMs = Date.now() - evolveStart;
      const totalDurationMs = Date.now() - cycleResult.timestamp.getTime();

      // Mark cycle as COMPLETED in DB
      await this.updateCyclePhase(cycleDbId, {
        currentPhase: 'COMPLETED',
        status: 'COMPLETED',
        mutationsPrepared,
        nextCycleSeedIds: JSON.stringify(nextCycleSeedIds),
        errors: JSON.stringify(cycleResult.errors),
        evolveDurationMs,
        totalDurationMs,
        completedAt: new Date(),
      });

      // Update cycle tracking
      this.currentCycle = cycleNumber;
      this.lastCycleAt = new Date();
      this.lastCycleResult = cycleResult;
      this.completedCycles.push(cycleResult);

      console.log(
        `[AutoEvolution] Cycle ${cycleNumber} COMPLETED: ` +
        `${cycleResult.strategiesActivated.length} activated, ` +
        `${cycleResult.entriesExecuted.length} entries, ` +
        `${cycleResult.exitsProcessed.length} exits, ` +
        `${cycleResult.errors.length} errors, ` +
        `duration: ${(totalDurationMs / 1000).toFixed(1)}s`
      );
      console.log(`[AutoEvolution] ═══════════════════════════════════════════`);

    } catch (err) {
      const errMsg = `Cycle ${cycleNumber} fatal error: ${err instanceof Error ? err.message : String(err)}`;
      cycleResult.errors.push(errMsg);
      this.lastError = errMsg;
      console.error(`[AutoEvolution] ${errMsg}`);

      // Mark cycle as FAILED in DB — previous cycles remain safe
      await this.updateCyclePhase(cycleDbId, {
        currentPhase: this.currentPhase,
        status: 'FAILED',
        errorLog: errMsg,
        errors: JSON.stringify(cycleResult.errors),
        completedAt: new Date(),
      });

      // Still record what we completed
      this.currentCycle = cycleNumber;
      this.lastCycleAt = new Date();
      this.lastCycleResult = cycleResult;
      this.completedCycles.push(cycleResult);

      console.log(
        `[AutoEvolution] Cycle ${cycleNumber} FAILED — previous ${this.completedCycles.length - 1} cycles' results are preserved`
      );
      console.log(`[AutoEvolution] ═══════════════════════════════════════════`);
    } finally {
      this.isCycleRunning = false;
      this.currentPhase = this.currentCycle >= this.totalCycles ? 'COMPLETED' : 'WAITING';
    }
  }

  /**
   * Check if there are any completed backtests with totalTrades > 0 in the DB.
   * Used to determine if bootstrap is needed.
   */
  private async hasCompletedBacktests(): Promise<boolean> {
    try {
      const count = await db.backtestRun.count({
        where: {
          status: 'COMPLETED',
          totalTrades: { gt: 0 },
        },
      });
      return count > 0;
    } catch (err) {
      console.warn('[AutoEvolution] BOOTSTRAP: Failed to check for completed backtests:', err);
      return false;
    }
  }

  /**
   * Bootstrap pipeline — creates initial strategies and backtests when none exist.
   * This runs only on the first cycle ever (or after a DB wipe) to seed the
   * evolution engine with completed backtests it can evolve from.
   *
   * Uses the same logic as /api/strategy-optimizer's handleGenerateStrategies + handleRunLoop.
   */
  private async runBootstrapPipeline(cycleResult: AutoEvolutionCycleResult): Promise<void> {
    this.currentPhase = 'BOOTSTRAP';
    console.log('[AutoEvolution] BOOTSTRAP: Starting bootstrap pipeline...');

    const capital = this.config.evolutionConfig.capital || 10000;
    const timeframe = '4h';
    const riskTolerance: string = 'MODERATE';

    // ═══ Step 1: Scan for fresh tokens ═══
    console.log('[AutoEvolution] BOOTSTRAP: Scanning for fresh tokens...');
    const freshTokens = await this.fetchFreshTokens();
    console.log(`[AutoEvolution] BOOTSTRAP: Found ${freshTokens.length} fresh tokens`);

    // ═══ Step 2: Generate strategy configs (same logic as handleGenerateStrategies) ═══
    console.log('[AutoEvolution] BOOTSTRAP: Generating strategy configs...');

    const riskPresets: Record<string, {
      maxDrawdown: number; stopLoss: number; takeProfit: number;
      positionSize: number; confidenceThreshold: number; maxConcurrent: number;
    }> = {
      CONSERVATIVE: { maxDrawdown: 10, stopLoss: 8, takeProfit: 25, positionSize: 3, confidenceThreshold: 80, maxConcurrent: 3 },
      MODERATE: { maxDrawdown: 20, stopLoss: 15, takeProfit: 40, positionSize: 5, confidenceThreshold: 65, maxConcurrent: 5 },
      AGGRESSIVE: { maxDrawdown: 35, stopLoss: 25, takeProfit: 80, positionSize: 10, confidenceThreshold: 50, maxConcurrent: 8 },
    };

    const preset = riskPresets[riskTolerance] || riskPresets.MODERATE;

    // Bootstrap with 3 diverse strategy categories for variety
    const bootstrapCategories = [
      { category: 'ALPHA_HUNTER', icon: '🎯', namePrefix: 'Alpha Hunter' },
      { category: 'SMART_MONEY', icon: '🧠', namePrefix: 'Smart Money' },
      { category: 'ADAPTIVE', icon: '🔄', namePrefix: 'Adaptive' },
    ];

    const tokenAges = ['NEW', 'MEDIUM', 'OLD'];
    const perStrategyCapital = capital / bootstrapCategories.length;

    interface BootstrapStrategyConfig {
      id: string;
      name: string;
      category: string;
      icon: string;
      timeframe: string;
      tokenAgeCategory: string;
      riskTolerance: string;
      capitalAllocation: number;
      config: {
        assetFilter: Record<string, unknown>;
        phaseConfig: Record<string, unknown>;
        entrySignal: Record<string, unknown>;
        exitSignal: Record<string, unknown>;
        riskManagement: Record<string, unknown>;
        executionConfig: Record<string, unknown>;
      };
    }

    const strategies: BootstrapStrategyConfig[] = [];
    let strategyId = 0;

    for (const template of bootstrapCategories) {
      for (const tokenAge of tokenAges) {
        strategyId++;
        const tokenAgeLabel = tokenAge === 'NEW' ? '<7d' : tokenAge === 'MEDIUM' ? '<30d' : '>30d';

        strategies.push({
          id: `bootstrap-strategy-${strategyId}`,
          name: `Bootstrap ${template.namePrefix} | ${timeframe} | ${tokenAgeLabel}`,
          category: template.category,
          icon: template.icon,
          timeframe,
          tokenAgeCategory: tokenAge,
          riskTolerance,
          capitalAllocation: perStrategyCapital,
          config: {
            assetFilter: {
              minLiquidity: tokenAge === 'NEW' ? 5000 : tokenAge === 'MEDIUM' ? 10000 : 50000,
              minVolume24h: tokenAge === 'NEW' ? 500 : 5000,
              maxMarketCap: tokenAge === 'NEW' ? 100000000 : tokenAge === 'MEDIUM' ? 1000000000 : 0,
              tokenAge: tokenAge === 'NEW' ? '<7D' : tokenAge === 'MEDIUM' ? '<30D' : '>30D',
              chains: ['SOL', 'ETH', 'BASE'],
            },
            phaseConfig: {
              genesis: tokenAge === 'NEW',
              early: tokenAge === 'NEW' || tokenAge === 'MEDIUM',
              growth: true,
              maturity: tokenAge === 'OLD',
              decline: false,
            },
            entrySignal: {
              signalType: template.category === 'SMART_MONEY' ? 'SMART_MONEY_ENTRY' :
                         template.category === 'ALPHA_HUNTER' ? 'MOMENTUM_BREAKOUT' : 'LIQUIDITY_SURGE',
              confidenceThreshold: preset.confidenceThreshold,
              confirmationRequired: riskTolerance !== 'AGGRESSIVE',
              timeWindow: 240,
            },
            exitSignal: {
              takeProfit: preset.takeProfit,
              stopLoss: preset.stopLoss,
              trailingStop: riskTolerance !== 'CONSERVATIVE',
              trailingStopPercent: Math.round(preset.takeProfit * 0.6),
              timeBasedExit: 2880,
            },
            riskManagement: {
              maxDrawdown: preset.maxDrawdown,
              maxConcurrentTrades: preset.maxConcurrent,
              maxDailyLoss: Math.round(preset.maxDrawdown * 0.5),
              positionSizing: 'RISK_BASED',
            },
            executionConfig: {
              orderType: 'LIMIT',
              slippageTolerance: tokenAge === 'NEW' ? 2.0 : 1.0,
              maxPositionSize: preset.positionSize,
              executionDelay: 0,
            },
          },
        });
      }
    }

    console.log(`[AutoEvolution] BOOTSTRAP: Generated ${strategies.length} strategy configs`);

    // ═══ Step 3: Create TradingSystem records and run backtests (same logic as handleRunLoop) ═══
    console.log('[AutoEvolution] BOOTSTRAP: Creating trading systems and running backtests...');

    const bteModule = await import('@/lib/services/backtesting/backtesting-engine');
    const backtestingEngine = bteModule.backtestingEngine;
    type BacktestConfig = import('@/lib/services/backtesting/backtesting-engine').BacktestConfig;

    const tseModule = await import('./trading-system-engine');
    const tradingSystemEngine = tseModule.tradingSystemEngine;

    const bdbModule = await import('@/lib/services/backtesting/backtest-data-bridge');
    const backtestDataBridge = bdbModule.backtestDataBridge;

    // Get or create a default trading system as parent
    let defaultSystem = await db.tradingSystem.findFirst({
      where: { category: 'ADAPTIVE' },
      orderBy: { createdAt: 'desc' },
    });

    if (!defaultSystem) {
      defaultSystem = await db.tradingSystem.create({
        data: {
          name: 'Bootstrap - Adaptive',
          category: 'ADAPTIVE',
          icon: '🔄',
          assetFilter: JSON.stringify({ tokenAge: 'ANY', chains: ['SOL', 'ETH', 'BASE'] }),
          phaseConfig: JSON.stringify({ genesis: true, early: true, growth: true, maturity: true, decline: false }),
          entrySignal: JSON.stringify({ signalType: 'MOMENTUM_BREAKOUT', confidenceThreshold: 60 }),
          executionConfig: JSON.stringify({ orderType: 'LIMIT', slippageTolerance: 1.5 }),
          exitSignal: JSON.stringify({ takeProfit: 40, stopLoss: 15, trailingStop: true, trailingStopPercent: 25 }),
          bigDataContext: JSON.stringify({}),
          primaryTimeframe: '1h',
          allocationMethod: 'KELLY_MODIFIED',
          maxPositionPct: 5,
          stopLossPct: 15,
          takeProfitPct: 40,
          cashReservePct: 20,
          isActive: false,
          isPaperTrading: false,
        },
      });
    }

    let completedCount = 0;
    let failedCount = 0;

    // Run strategies SEQUENTIALLY to avoid overloading the DB
    for (const strategy of strategies) {
      try {
        // 1. Create a TradingSystem record
        const system = await db.tradingSystem.create({
          data: {
            name: strategy.name,
            category: strategy.category as 'ALPHA_HUNTER',
            icon: strategy.icon,
            assetFilter: JSON.stringify(strategy.config.assetFilter),
            phaseConfig: JSON.stringify(strategy.config.phaseConfig),
            entrySignal: JSON.stringify(strategy.config.entrySignal),
            executionConfig: JSON.stringify(strategy.config.executionConfig),
            exitSignal: JSON.stringify(strategy.config.exitSignal),
            bigDataContext: JSON.stringify({}),
            primaryTimeframe: strategy.timeframe,
            allocationMethod: 'KELLY_MODIFIED',
            maxPositionPct: (strategy.config.riskManagement as Record<string, unknown>).maxPositionSize as number || 5,
            stopLossPct: (strategy.config.exitSignal as Record<string, unknown>).stopLoss as number || 15,
            takeProfitPct: (strategy.config.exitSignal as Record<string, unknown>).takeProfit as number || 40,
            cashReservePct: 20,
            isActive: false,
            isPaperTrading: false,
            parentSystemId: defaultSystem.id,
          },
        });

        // 2. Create a BacktestRun record
        const periodStart = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000);
        const periodEnd = new Date();
        const initialCapital = strategy.capitalAllocation || capital / strategies.length;

        const backtest = await db.backtestRun.create({
          data: {
            systemId: system.id,
            mode: 'HISTORICAL',
            periodStart,
            periodEnd,
            initialCapital,
            allocationMethod: 'KELLY_MODIFIED',
            capitalAllocation: JSON.stringify({
              method: 'KELLY_MODIFIED',
              initialCapital,
            }),
            strategyMeta: JSON.stringify({
              strategyId: strategy.id,
              strategyName: strategy.name,
              category: strategy.category,
              timeframe: strategy.timeframe,
              tokenAgeCategory: strategy.tokenAgeCategory,
              riskTolerance: strategy.riskTolerance,
              bootstrap: true,
            }),
            status: 'RUNNING',
            progress: 0.05,
            startedAt: new Date(),
          },
        });

        // 3. Build a system template from the trading system engine
        const systemTemplate = tradingSystemEngine.getTemplate(system.name) ??
          tradingSystemEngine.createSystemFromTemplate(
            tradingSystemEngine.getAllTemplateNames()[0],
            {
              name: system.name,
              category: system.category as 'ALPHA_HUNTER',
            },
          );

        // 4. Load token data via backtestDataBridge
        const backtestTimeframe = strategy.timeframe || systemTemplate.primaryTimeframe || '4h';

        let maxAgeHours: number | undefined;
        const assetFilter = strategy.config.assetFilter as Record<string, unknown>;
        const tokenAgeStr = assetFilter?.tokenAge as string | undefined;
        if (tokenAgeStr && tokenAgeStr !== 'ANY') {
          const match = tokenAgeStr.match(/<?(\d+)([DHM])/i);
          if (match) {
            const value = parseInt(match[1]);
            const unit = match[2].toUpperCase();
            maxAgeHours = unit === 'D' ? value * 24 : unit === 'H' ? value : Math.round(value / 60);
          }
        }

        const enhancedAssetFilter = {
          ...systemTemplate.assetFilter,
          ...(maxAgeHours ? { maxAgeHours } : {}),
          chains: (assetFilter?.chains as string[]) || ['SOL', 'ETH', 'BASE'],
        };

        let tokenData = await backtestDataBridge.loadTokensForBacktest({
          startDate: periodStart,
          endDate: periodEnd,
          timeframe: backtestTimeframe,
          chain: undefined,
          minCandles: 20,
          assetFilter: enhancedAssetFilter,
          maxTokens: 10,
          includeMetrics: true,
        });

        await db.backtestRun.update({
          where: { id: backtest.id },
          data: { progress: 0.2 },
        });

        // Validate token data
        const { valid: validTokenData, rejected } = backtestDataBridge.validateTokenData(tokenData);
        if (rejected.length > 0) {
          console.warn(`[AutoEvolution] BOOTSTRAP: Rejected ${rejected.length} tokens with bad data`);
        }
        tokenData = validTokenData;

        if (tokenData.length === 0) {
          // No data — mark as FAILED so it's excluded from ranking
          await db.backtestRun.update({
            where: { id: backtest.id },
            data: {
              status: 'FAILED',
              progress: 1,
              completedAt: new Date(),
              finalCapital: initialCapital,
              totalPnl: 0,
              totalPnlPct: 0,
              errorLog: 'Bootstrap: No token data available for backtesting.',
            },
          });
          failedCount++;
          continue;
        }

        // 5. Build BacktestConfig and run the backtesting engine
        const btConfig: BacktestConfig = {
          system: systemTemplate,
          mode: 'HISTORICAL',
          startDate: periodStart,
          endDate: periodEnd,
          initialCapital,
          feesPct: 0.003,
          slippagePct: 0.5,
          applySlippage: true,
          enforcePhaseFilter: true,
        };

        await db.backtestRun.update({
          where: { id: backtest.id },
          data: { progress: 0.3 },
        });

        const result = await backtestingEngine.runBacktest(
          btConfig,
          tokenData,
          async (progress) => {
            if (progress.barsProcessed % 500 === 0) {
              try {
                await db.backtestRun.update({
                  where: { id: backtest.id },
                  data: {
                    progress: Math.min(0.9, 0.3 + progress.percentComplete * 0.006),
                  },
                });
              } catch { /* non-critical */ }
            }
          },
        );

        await db.backtestRun.update({
          where: { id: backtest.id },
          data: { progress: 0.9 },
        });

        // 6. Create BacktestOperation records for each trade
        const operationCreates = result.trades.map((trade) => ({
          backtestId: backtest.id,
          systemId: system.id,
          tokenAddress: trade.tokenAddress,
          tokenSymbol: trade.symbol,
          chain: (() => {
            const addr = trade.tokenAddress || '';
            if (addr.startsWith('0x')) return 'ethereum';
            if (addr.length > 30 && !addr.startsWith('0x')) return 'solana';
            return 'eth';
          })(),
          tokenPhase: trade.phase,
          tokenAgeMinutes: 0,
          marketConditions: JSON.stringify({ timeframe: systemTemplate.primaryTimeframe }),
          tokenDnaSnapshot: JSON.stringify({}),
          traderComposition: JSON.stringify({}),
          bigDataContext: JSON.stringify({}),
          operationType: trade.direction,
          timeframe: systemTemplate.primaryTimeframe,
          entryPrice: trade.entryPrice,
          entryTime: trade.entryTime,
          entryReason: JSON.stringify({ reason: 'bootstrap_simulation', system: systemTemplate.name }),
          exitPrice: trade.exitPrice ?? 0,
          exitTime: trade.exitTime ?? new Date(),
          exitReason: trade.exitReason,
          quantity: trade.quantity,
          positionSizeUsd: trade.size,
          pnlUsd: trade.pnl,
          pnlPct: trade.pnlPct,
          holdTimeMin: trade.holdTimeMin,
          maxFavorableExc: trade.mfe,
          maxAdverseExc: trade.mae,
          capitalAllocPct: trade.size / initialCapital * 100,
          allocationMethodUsed: systemTemplate.allocationMethod,
        }));

        if (operationCreates.length > 0) {
          await db.backtestOperation.createMany({ data: operationCreates });
        }

        // 7. Update the BacktestRun with results
        await db.backtestRun.update({
          where: { id: backtest.id },
          data: {
            status: 'COMPLETED',
            progress: 1,
            completedAt: new Date(),
            finalCapital: result.finalEquity,
            totalPnl: result.finalEquity - result.initialCapital,
            totalPnlPct: result.totalReturnPct,
            annualizedReturn: result.annualizedReturnPct,
            benchmarkReturn: 0,
            alpha: result.annualizedReturnPct,
            totalTrades: result.totalTrades,
            winTrades: result.winningTrades,
            lossTrades: result.losingTrades,
            winRate: result.winRate,
            avgWin: result.avgWinPct,
            avgLoss: result.avgLossPct,
            profitFactor: result.profitFactor,
            expectancy: result.expectancy,
            maxDrawdown: result.maxDrawdown,
            maxDrawdownPct: result.maxDrawdownPct,
            sharpeRatio: result.sharpeRatio,
            sortinoRatio: result.sortinoRatio,
            calmarRatio: result.calmarRatio,
            recoveryFactor: result.recoveryFactor,
            avgHoldTimeMin: result.avgHoldTimeMin,
            marketExposurePct: result.totalTrades > 0 && result.avgHoldTimeMin > 0
              ? Math.min(100, (result.totalTrades * result.avgHoldTimeMin) / ((periodEnd.getTime() - periodStart.getTime()) / 60000) * 100)
              : 0,
            phaseResults: JSON.stringify(result.phaseBreakdown),
            timeframeResults: JSON.stringify({ primaryTimeframe: systemTemplate.primaryTimeframe }),
            operationTypeResults: JSON.stringify({}),
            allocationMethodResults: JSON.stringify({ method: systemTemplate.allocationMethod }),
          },
        });

        // Update trading system metrics
        const updatedSystem = await db.tradingSystem.findUnique({ where: { id: system.id } });
        if (updatedSystem) {
          const metricsUpdate: Record<string, unknown> = {
            totalBacktests: updatedSystem.totalBacktests + 1,
          };
          if (result.sharpeRatio > updatedSystem.bestSharpe) metricsUpdate.bestSharpe = result.sharpeRatio;
          if (result.winRate > updatedSystem.bestWinRate) metricsUpdate.bestWinRate = result.winRate;
          if (result.totalReturnPct > updatedSystem.bestPnlPct) metricsUpdate.bestPnlPct = result.totalReturnPct;
          if (updatedSystem.totalBacktests === 0) {
            metricsUpdate.avgHoldTimeMin = result.avgHoldTimeMin;
          } else {
            metricsUpdate.avgHoldTimeMin =
              (updatedSystem.avgHoldTimeMin * updatedSystem.totalBacktests + result.avgHoldTimeMin) /
              (updatedSystem.totalBacktests + 1);
          }
          await db.tradingSystem.update({ where: { id: system.id }, data: metricsUpdate });
        }

        completedCount++;
        console.log(
          `[AutoEvolution] BOOTSTRAP: Strategy "${strategy.name}" completed — ` +
          `trades=${result.totalTrades}, sharpe=${result.sharpeRatio.toFixed(2)}, ` +
          `pnl=${result.totalReturnPct.toFixed(2)}%`
        );
      } catch (err) {
        failedCount++;
        const errMsg = `Bootstrap strategy "${strategy.name}" failed: ${err instanceof Error ? err.message : String(err)}`;
        cycleResult.errors.push(errMsg);
        console.error(`[AutoEvolution] BOOTSTRAP: ${errMsg}`);
      }
    }

    console.log(
      `[AutoEvolution] BOOTSTRAP: Pipeline complete — ${completedCount} completed, ${failedCount} failed out of ${strategies.length} strategies`
    );

    // Verify that we now have completed backtests
    const hasBacktestsAfter = await this.hasCompletedBacktests();
    if (hasBacktestsAfter) {
      console.log('[AutoEvolution] BOOTSTRAP: Completed backtests now available — evolution engine can proceed');
    } else {
      console.warn('[AutoEvolution] BOOTSTRAP: No completed backtests after bootstrap — evolution may still be empty');
    }
  }

  /**
   * Update a cycle's phase in the DB.
   * Best-effort — failures are logged but don't crash the cycle.
   */
  private async updateCyclePhase(
    cycleDbId: string | null,
    data: Record<string, unknown>,
  ): Promise<void> {
    if (!cycleDbId) return;

    try {
      await db.evolutionCycle.update({
        where: { id: cycleDbId },
        data,
      });
    } catch (err) {
      console.warn('[AutoEvolution] Failed to update cycle phase in DB:', err);
    }
  }

  /**
   * Convert a DB EvolutionCycle record to a cycle result.
   */
  private cycleDbToResult(cycle: { id: string; cycleNumber: number; runId: string; currentPhase: string; status: string; bestScore: number; improvedCount: number; degradedCount: number; totalMutations: number; strategiesActivated: string; entriesExecuted: string; exitsProcessed: string; errors: string; startedAt: Date; completedAt: Date | null }): AutoEvolutionCycleResult {
    let exitsProcessed: Array<{ backtestId: string; exitReason: string; pnlUsd: number }> = [];
    try {
      exitsProcessed = JSON.parse(cycle.exitsProcessed || '[]');
    } catch { /* ignore */ }

    let errors: string[] = [];
    try {
      errors = JSON.parse(cycle.errors || '[]');
    } catch { /* ignore */ }

    let strategiesActivated: string[] = [];
    try {
      strategiesActivated = JSON.parse(cycle.strategiesActivated || '[]');
    } catch { /* ignore */ }

    let entriesExecuted: string[] = [];
    try {
      entriesExecuted = JSON.parse(cycle.entriesExecuted || '[]');
    } catch { /* ignore */ }

    return {
      cycleNumber: cycle.cycleNumber,
      runId: cycle.runId,
      timestamp: cycle.startedAt,
      phase: cycle.currentPhase,
      evolutionResult: {
        improved: cycle.improvedCount,
        degraded: cycle.degradedCount,
        totalMutations: cycle.totalMutations,
        bestScore: cycle.bestScore,
      },
      strategiesActivated,
      entriesExecuted,
      exitsProcessed,
      errors,
      cycleDbId: cycle.id,
    };
  }

  /**
   * Activate a strategy for paper trading by updating the TradingSystem record.
   */
  private async activateStrategyForPaperTrading(
    systemId: string,
    strategy: { name: string; score: number },
  ): Promise<boolean> {
    try {
      const system = await db.tradingSystem.findUnique({ where: { id: systemId } });
      if (!system) {
        console.warn(`[AutoEvolution] System ${systemId} not found, skipping activation`);
        return false;
      }

      // Already active in paper trading
      if (system.isActive && system.isPaperTrading) {
        console.log(`[AutoEvolution] System ${system.name} already active in paper trading`);
        return true;
      }

      await db.tradingSystem.update({
        where: { id: systemId },
        data: { isActive: true, isPaperTrading: true },
      });

      console.log(`[AutoEvolution] Activated ${strategy.name} (score: ${strategy.score.toFixed(1)}) for paper trading`);
      return true;
    } catch (err) {
      console.error(`[AutoEvolution] Failed to activate ${systemId}:`, err);
      return false;
    }
  }

  /**
   * Auto-execute a paper trade entry for a strategy.
   */
  private async autoExecuteEntry(
    systemId: string,
    backtestId: string,
  ): Promise<string | null> {
    try {
      const openPositions = await strategyEvolutionEngine.getOpenPositions();
      if (openPositions.length >= this.config.maxConcurrentPositions) {
        console.log(`[AutoEvolution] Max concurrent positions reached (${openPositions.length}/${this.config.maxConcurrentPositions})`);
        return null;
      }

      const backtestOp = await db.backtestOperation.findFirst({
        where: { backtestId, pnlUsd: { not: null } },
        orderBy: { pnlUsd: 'desc' },
      });

      if (!backtestOp) {
        const anyOp = await db.backtestOperation.findFirst({
          where: { systemId, exitPrice: { not: null } },
          orderBy: { entryTime: 'desc' },
        });

        if (!anyOp) {
          console.log('[AutoEvolution] No suitable tokens found for entry');
          return null;
        }

        const token = await db.token.findFirst({
          where: { address: anyOp.tokenAddress },
          select: { priceUsd: true, symbol: true },
        });

        const entryPrice = token?.priceUsd || anyOp.entryPrice || 0.001;
        const tokenSymbol = token?.symbol || anyOp.tokenSymbol || '';

        const result = await strategyEvolutionEngine.executeEntry({
          systemId,
          tokenAddress: anyOp.tokenAddress,
          tokenSymbol,
          direction: 'LONG',
          entryPrice,
          positionSizeUsd: this.config.positionSizeUsd,
        });

        console.log(`[AutoEvolution] Auto-executed entry: tradeId=${result.tradeId}`);
        return result.tradeId;
      }

      const token = await db.token.findFirst({
        where: { address: backtestOp.tokenAddress },
        select: { priceUsd: true, symbol: true },
      });

      const entryPrice = token?.priceUsd || backtestOp.entryPrice || 0.001;
      const tokenSymbol = token?.symbol || backtestOp.tokenSymbol || '';

      const result = await strategyEvolutionEngine.executeEntry({
        systemId,
        tokenAddress: backtestOp.tokenAddress,
        tokenSymbol,
        direction: 'LONG',
        entryPrice,
        positionSizeUsd: this.config.positionSizeUsd,
      });

      await strategyStateManager.recordStateTransition({
        systemId,
        newStatus: 'PAPER_TRADING',
        triggerReason: 'SCHEDULER',
        metadata: {
          event: 'auto_entry_executed',
          tradeId: result.tradeId,
          tokenAddress: backtestOp.tokenAddress,
          tokenSymbol,
          entryPrice,
          positionSizeUsd: this.config.positionSizeUsd,
        },
      });

      console.log(`[AutoEvolution] Auto-executed entry: tradeId=${result.tradeId}`);
      return result.tradeId;
    } catch (err) {
      console.error('[AutoEvolution] Auto-execute entry failed:', err);
      return null;
    }
  }

  /**
   * Monitor open positions and apply exit rules.
   */
  private async monitorAndExitPositions(): Promise<
    Array<{ backtestId: string; exitReason: string; pnlUsd: number }>
  > {
    const results: Array<{ backtestId: string; exitReason: string; pnlUsd: number }> = [];

    try {
      const openPositions = await strategyEvolutionEngine.getOpenPositions();

      if (openPositions.length === 0) {
        return results;
      }

      for (const position of openPositions) {
        try {
          const token = await db.token.findFirst({
            where: { address: position.tokenAddress },
            select: { priceUsd: true },
          });

          const currentPrice = token?.priceUsd;
          if (!currentPrice || currentPrice <= 0) {
            continue;
          }

          const system = await db.tradingSystem.findUnique({
            where: { id: position.systemId },
          });

          if (!system) continue;

          let exitConfig: Record<string, unknown> = {};
          try {
            exitConfig = JSON.parse(system.exitSignal || '{}');
          } catch {
            /* ignore */
          }

          const takeProfitPct = (exitConfig.takeProfit as number) || system.takeProfitPct || 40;
          const stopLossPct = (exitConfig.stopLoss as number) || system.stopLossPct || 15;
          const trailingStopPct = (exitConfig.trailingStopPercent as number) || system.trailingStopPct || 25;
          const timeBasedExitMin = (exitConfig.timeBasedExit as number) || 1440;

          const entryPrice = position.entryPrice;
          const holdTimeMin = (Date.now() - position.entryTime.getTime()) / 60000;
          const priceChangePct = ((currentPrice - entryPrice) / entryPrice) * 100;

          let shouldExit = false;
          let exitReason = '';

          if (priceChangePct >= takeProfitPct) {
            shouldExit = true;
            exitReason = 'TAKE_PROFIT';
          }

          if (priceChangePct <= -stopLossPct) {
            shouldExit = true;
            exitReason = 'STOP_LOSS';
          }

          if (this.config.enableTrailingStop && !shouldExit) {
            const operations = await db.backtestOperation.findMany({
              where: {
                backtestId: position.backtestId,
                exitPrice: null,
              },
            });

            for (const op of operations) {
              const mfe = op.maxFavorableExc || 0;
              if (mfe > 0) {
                const retracementFromPeak = mfe - priceChangePct;
                if (retracementFromPeak >= trailingStopPct && priceChangePct > 0) {
                  shouldExit = true;
                  exitReason = 'TRAILING_STOP';
                  break;
                }
              }
            }
          }

          if (this.config.enableTimeBasedExit && !shouldExit) {
            if (holdTimeMin >= timeBasedExitMin) {
              shouldExit = true;
              exitReason = 'TIME_BASED_EXIT';
            }
          }

          if (shouldExit) {
            console.log(
              `[AutoEvolution] Exit triggered for ${position.tokenSymbol}: ` +
              `${exitReason} (price change: ${priceChangePct.toFixed(2)}%, hold: ${holdTimeMin.toFixed(0)}min)`
            );

            try {
              const exitResult = await strategyEvolutionEngine.executeExit({
                backtestId: position.backtestId,
                exitPrice: currentPrice,
                exitReason,
              });

              results.push({
                backtestId: position.backtestId,
                exitReason,
                pnlUsd: exitResult.pnlUsd,
              });

              await strategyStateManager.recordStateTransition({
                systemId: position.systemId,
                newStatus: 'IDLE',
                triggerReason: 'SCHEDULER',
                metrics: {
                  totalPnlUsd: exitResult.pnlUsd,
                  totalPnlPct: exitResult.pnlPct,
                  openPositions: 0,
                },
                metadata: {
                  event: 'auto_exit_executed',
                  exitReason,
                  tokenSymbol: position.tokenSymbol,
                  pnlUsd: exitResult.pnlUsd,
                  pnlPct: exitResult.pnlPct,
                  holdTimeMin,
                  priceChangePct,
                },
              });
            } catch (err) {
              console.error(
                `[AutoEvolution] Failed to execute exit for ${position.backtestId}:`,
                err,
              );
            }
          }
        } catch (err) {
          console.error(
            `[AutoEvolution] Error monitoring position ${position.backtestId}:`,
            err,
          );
        }
      }
    } catch (err) {
      console.error('[AutoEvolution] Error in position monitoring:', err);
    }

    return results;
  }

  /**
   * Get the count of open positions for a specific backtest ID's system.
   */
  private async getOpenPositionCountForStrategy(backtestId: string): Promise<number> {
    try {
      const operation = await db.backtestOperation.findFirst({
        where: { backtestId },
        select: { systemId: true },
      });

      if (!operation) return 0;

      const openCount = await db.backtestOperation.count({
        where: {
          systemId: operation.systemId,
          exitPrice: null,
          backtest: { mode: 'PAPER' },
        },
      });

      return openCount;
    } catch {
      return 0;
    }
  }

  /**
   * Run a single evolution cycle (for quick testing / manual trigger).
   * Does not schedule subsequent cycles — just runs one cycle and returns.
   */
  async runSingleCycle(config?: Partial<AutoEvolutionConfig>): Promise<AutoEvolutionCycleResult | null> {
    if (this.isRunning) {
      console.warn('[AutoEvolution] Already running, cannot run single cycle');
      return null;
    }

    // Apply config overrides for this single run
    if (config) {
      this.config = { ...DEFAULT_AUTO_EVOLUTION_CONFIG, ...config };
    }
    this.totalCycles = 1;
    this.isRunning = true;
    this.stopRequested = false;
    this.startedAt = new Date();
    this.currentPhase = 'SINGLE_CYCLE';
    this.lastError = null;
    this.runId = `single-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    console.log('[AutoEvolution] Running single evolution cycle');

    await this.runNextCycle();

    // Ensure we stop after the single cycle
    this.isRunning = false;
    this.currentPhase = 'COMPLETED';

    return this.lastCycleResult;
  }

  /**
   * Fetch fresh tokens from DexScreener API + DB.
   * Tries DexScreener first for live market data, falls back to DB.
   * Upserts discovered tokens into the DB for backtest data bridge.
   */
  private async fetchFreshTokens(): Promise<Array<{ address: string; symbol: string; volume24h: number }>> {
    const results: Array<{ address: string; symbol: string; volume24h: number }> = [];
    const seenAddresses = new Set<string>();

    // ═══ Step 1: Try DexScreener API for fresh tokens ═══
    try {
      const searchQueries = ['solana', 'ethereum', 'base'];
      const searchResults = await Promise.allSettled(
        searchQueries.map(q =>
          fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(q)}`, {
            next: { revalidate: 60 },
            signal: AbortSignal.timeout(8000),
          })
        )
      );

      for (const result of searchResults) {
        if (result.status !== 'fulfilled' || !result.value.ok) continue;
        try {
          const data = await result.value.json();
          const pairs = data.pairs || [];

          for (const pair of pairs) {
            if (!pair.baseToken?.address) continue;
            const addr = pair.baseToken.address;
            if (seenAddresses.has(addr)) continue;

            const volume24h = pair.volume?.h24 || 0;
            const liquidity = pair.liquidity?.usd || 0;
            if (volume24h < 10000 || liquidity < 5000) continue;

            seenAddresses.add(addr);
            results.push({
              address: addr,
              symbol: pair.baseToken.symbol || 'UNKNOWN',
              volume24h,
            });

            // Upsert token into DB for backtest data bridge
            try {
              await db.token.upsert({
                where: { address: addr },
                update: {
                  symbol: pair.baseToken.symbol || 'UNKNOWN',
                  name: pair.baseToken.name || 'Unknown',
                  chain: (pair.chainId || 'unknown').toUpperCase(),
                  priceUsd: parseFloat(pair.priceUsd || '0'),
                  volume24h,
                  liquidity,
                  marketCap: pair.marketCap || pair.fdv || 0,
                  priceChange24h: pair.priceChange?.h24 || 0,
                  updatedAt: new Date(),
                },
                create: {
                  address: addr,
                  symbol: pair.baseToken.symbol || 'UNKNOWN',
                  name: pair.baseToken.name || 'Unknown',
                  chain: (pair.chainId || 'unknown').toUpperCase(),
                  priceUsd: parseFloat(pair.priceUsd || '0'),
                  volume24h,
                  liquidity,
                  marketCap: pair.marketCap || pair.fdv || 0,
                  priceChange24h: pair.priceChange?.h24 || 0,
                },
              });
            } catch {
              // Best-effort upsert — don't block evolution on DB errors
            }
          }
        } catch {
          continue;
        }
      }

      // Also try boosted/trending tokens
      try {
        const boostRes = await fetch('https://api.dexscreener.com/token-boosts/top/v1', {
          next: { revalidate: 60 },
          signal: AbortSignal.timeout(8000),
        });

        if (boostRes.ok) {
          const boostedTokens = await boostRes.json();
          const toEnrich = (Array.isArray(boostedTokens) ? boostedTokens : [])
            .filter((bt: Record<string, unknown>) => bt.tokenAddress && !seenAddresses.has(bt.tokenAddress as string))
            .slice(0, 5);

          for (const bt of toEnrich) {
            try {
              const pairs = await dexScreenerClient.searchTokenPairs(bt.tokenAddress as string);
              if (pairs.length === 0) continue;
              pairs.sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0));
              const best = pairs[0];

              const volume24h = best.volume?.h24 || 0;
              const liquidity = best.liquidity?.usd || 0;
              if (volume24h < 10000 || liquidity < 5000) continue;

              seenAddresses.add(bt.tokenAddress as string);
              results.push({
                address: bt.tokenAddress as string,
                symbol: best.baseToken?.symbol || 'UNKNOWN',
                volume24h,
              });

              // Upsert into DB
              try {
                await db.token.upsert({
                  where: { address: bt.tokenAddress as string },
                  update: {
                    symbol: best.baseToken?.symbol || 'UNKNOWN',
                    name: best.baseToken?.name || 'Unknown',
                    chain: (best.chainId || 'unknown').toUpperCase(),
                    priceUsd: parseFloat(best.priceUsd || '0'),
                    volume24h,
                    liquidity,
                    marketCap: best.marketCap || best.fdv || 0,
                    priceChange24h: best.priceChange?.h24 || 0,
                    updatedAt: new Date(),
                  },
                  create: {
                    address: bt.tokenAddress as string,
                    symbol: best.baseToken?.symbol || 'UNKNOWN',
                    name: best.baseToken?.name || 'Unknown',
                    chain: (best.chainId || 'unknown').toUpperCase(),
                    priceUsd: parseFloat(best.priceUsd || '0'),
                    volume24h,
                    liquidity,
                    marketCap: best.marketCap || best.fdv || 0,
                    priceChange24h: best.priceChange?.h24 || 0,
                  },
                });
              } catch {
                // Best-effort
              }
            } catch {
              continue;
            }
          }
        }
      } catch {
        // Boosted tokens fetch failed — continue with search results only
      }
    } catch (err) {
      console.warn('[AutoEvolution] DexScreener fetch failed, will use DB fallback:', err);
    }

    // ═══ Step 2: Supplement with DB tokens ═══
    try {
      const dbTokens = await db.token.findMany({
        where: { volume24h: { gt: 0 }, liquidity: { gt: 0 } },
        orderBy: { volume24h: 'desc' },
        take: 20,
        select: { address: true, symbol: true, volume24h: true },
      });

      for (const t of dbTokens) {
        if (!seenAddresses.has(t.address)) {
          seenAddresses.add(t.address);
          results.push({ address: t.address, symbol: t.symbol, volume24h: t.volume24h });
        }
      }
    } catch {
      // DB supplement failed — continue with whatever we have
    }

    // Sort by volume descending
    results.sort((a, b) => b.volume24h - a.volume24h);

    return results.slice(0, 30); // Cap at 30 tokens max
  }

  /**
   * Get past evolution cycle history from DB (for runs that completed before restart).
   */
  async getDbCycleHistory(limit: number = 20): Promise<AutoEvolutionCycleResult[]> {
    try {
      const dbCycles = await db.evolutionCycle.findMany({
        where: { status: 'COMPLETED' },
        orderBy: { startedAt: 'desc' },
        take: limit,
        select: {
          id: true,
          cycleNumber: true,
          runId: true,
          currentPhase: true,
          status: true,
          bestScore: true,
          improvedCount: true,
          degradedCount: true,
          totalMutations: true,
          strategiesActivated: true,
          entriesExecuted: true,
          exitsProcessed: true,
          errors: true,
          startedAt: true,
          completedAt: true,
          totalDurationMs: true,
          tokensScanned: true,
          backtestsRun: true,
        },
      });

      return dbCycles.map(c => this.cycleDbToResult(c as Parameters<typeof this.cycleDbToResult>[0]));
    } catch {
      return [];
    }
  }

  /**
   * Clean up strategies that are no longer active from the active set.
   */
  private async cleanupStaleActiveStrategies(): Promise<void> {
    const toRemove: string[] = [];

    for (const systemId of this.activeStrategies) {
      try {
        const system = await db.tradingSystem.findUnique({
          where: { id: systemId },
          select: { isActive: true, isPaperTrading: true },
        });

        if (!system || (!system.isActive && !system.isPaperTrading)) {
          toRemove.push(systemId);
        }
      } catch {
        toRemove.push(systemId);
      }
    }

    for (const systemId of toRemove) {
      this.activeStrategies.delete(systemId);
    }
  }

  /**
   * Inject fresh strategy variants to maintain diversity in the gene pool.
   * Called every 3rd cycle to prevent premature convergence.
   * Creates new TradingSystem records from diverse category templates.
   */
  private async injectFreshStrategies(_topTokenAddresses: string[]): Promise<string[]> {
    const variantIds: string[] = [];

    // Diverse templates: each with different signal types and risk profiles
    const diversityTemplates = [
      { category: 'ALPHA_HUNTER', icon: '🎯', namePrefix: 'Diversity Alpha', signalType: 'MOMENTUM_BREAKOUT', tp: 50, sl: 12 },
      { category: 'SMART_MONEY', icon: '🧠', namePrefix: 'Diversity Smart', signalType: 'SMART_MONEY_ENTRY', tp: 35, sl: 10 },
      { category: 'ADAPTIVE', icon: '🔄', namePrefix: 'Diversity Adaptive', signalType: 'LIQUIDITY_SURGE', tp: 60, sl: 18 },
      { category: 'ALPHA_HUNTER', icon: '🎯', namePrefix: 'Diversity Momentum', signalType: 'VOLUME_SPIKE', tp: 45, sl: 15 },
      { category: 'SMART_MONEY', icon: '🧠', namePrefix: 'Diversity Whale', signalType: 'WHALE_ACCUMULATION', tp: 30, sl: 8 },
    ];

    const timeframes = ['1h', '4h'];

    for (const template of diversityTemplates) {
      for (const tf of timeframes) {
        try {
          const system = await db.tradingSystem.create({
            data: {
              name: `${template.namePrefix} | ${tf} | C${this.currentCycle + 1}`,
              category: template.category as 'ALPHA_HUNTER',
              icon: template.icon,
              assetFilter: JSON.stringify({
                chains: ['SOL', 'ETH', 'BASE'],
                minLiquidity: 10000,
                minVolume24h: 2000,
              }),
              phaseConfig: JSON.stringify({ genesis: true, early: true, growth: true, maturity: true, decline: false }),
              entrySignal: JSON.stringify({
                signalType: template.signalType,
                confidenceThreshold: 60,
                confirmationRequired: true,
                timeWindow: 240,
              }),
              executionConfig: JSON.stringify({ orderType: 'LIMIT', slippageTolerance: 1.5 }),
              exitSignal: JSON.stringify({
                takeProfit: template.tp,
                stopLoss: template.sl,
                trailingStop: true,
                trailingStopPercent: Math.round(template.tp * 0.6),
                timeBasedExit: 2880,
              }),
              bigDataContext: JSON.stringify({}),
              primaryTimeframe: tf,
              allocationMethod: 'KELLY_MODIFIED',
              maxPositionPct: 5,
              stopLossPct: template.sl,
              takeProfitPct: template.tp,
              cashReservePct: 20,
              isActive: false,
              isPaperTrading: false,
            },
          });

          variantIds.push(system.id);
        } catch (err) {
          console.warn(`[AutoEvolution] Diversity injection: Failed to create ${template.namePrefix}:`, err);
        }
      }
    }

    console.log(`[AutoEvolution] DIVERSITY: Created ${variantIds.length} fresh strategy variants from ${diversityTemplates.length} templates`);
    return variantIds;
  }
}

// Singleton
export const autoEvolutionLoop = new AutoEvolutionLoop();
