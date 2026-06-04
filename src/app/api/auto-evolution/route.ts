import { NextRequest, NextResponse } from 'next/server';
import {
  autoEvolutionLoop,
  DEFAULT_AUTO_EVOLUTION_CONFIG,
  type AutoEvolutionConfig,
} from '@/lib/services/strategy/auto-evolution-loop';
import { DEFAULT_EVOLUTION_CONFIG, type EvolutionConfig } from '@/lib/services/strategy/strategy-evolution-engine';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// POST /api/auto-evolution
// Control the auto-evolution loop.
//
// Actions:
//   - "start": Begin the auto-evolution loop with N discrete cycles (server-side interval)
//   - "stop": Stop after current cycle completes
//   - "status": Get current cycle number, progress, best results so far
//   - "run_single_cycle": Run one discrete evolution cycle immediately
//   - "run_full_pipeline": Run one full pipeline cycle (bootstrap + evolve)
//   - "run_next_cycle": Frontend-driven discrete cycle — runs next cycle if idle
//
// Body params (for "start"):
//   totalCycles?: number          - Number of cycles to run (default: 5)
//   intervalMs?: number           - Interval between cycles (default: 300000 = 5 min)
//   minSharpeRatio?: number       - Minimum Sharpe ratio to auto-activate (default: 0.5)
//   minWinRate?: number           - Minimum win rate to auto-activate (default: 0.4)
//   maxConcurrentPositions?: number - Max concurrent paper positions (default: 5)
//   positionSizeUsd?: number      - Position size for auto-trades (default: 100)
//   enableTrailingStop?: boolean  - Enable trailing stop monitoring (default: true)
//   enableTimeBasedExit?: boolean - Enable time-based exit (default: true)
//   maxHoldTimeMin?: number       - Max hold time in minutes (default: 1440)
//   evolutionConfig?: object      - Override evolution engine config
//   bootstrap?: boolean           - Whether to bootstrap if no backtests exist (default: true)
// ============================================================

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const action = body.action as string;

    if (action === 'start') {
      // Build the auto-evolution config from the request
      const evolutionConfig: EvolutionConfig = {
        maxIterations: body.evolutionConfig?.maxIterations ?? DEFAULT_EVOLUTION_CONFIG.maxIterations,
        improvementThreshold: body.evolutionConfig?.improvementThreshold ?? DEFAULT_EVOLUTION_CONFIG.improvementThreshold,
        mutationRate: body.evolutionConfig?.mutationRate ?? DEFAULT_EVOLUTION_CONFIG.mutationRate,
        topN: body.evolutionConfig?.topN ?? DEFAULT_EVOLUTION_CONFIG.topN,
        capital: body.evolutionConfig?.capital ?? DEFAULT_EVOLUTION_CONFIG.capital,
      };

      const config: Partial<AutoEvolutionConfig> = {
        totalCycles: Number(body.totalCycles) || DEFAULT_AUTO_EVOLUTION_CONFIG.totalCycles,
        intervalMs: Number(body.intervalMs) || DEFAULT_AUTO_EVOLUTION_CONFIG.intervalMs,
        evolutionConfig,
        minSharpeRatio: Number(body.minSharpeRatio) || DEFAULT_AUTO_EVOLUTION_CONFIG.minSharpeRatio,
        minWinRate: Number(body.minWinRate) || DEFAULT_AUTO_EVOLUTION_CONFIG.minWinRate,
        maxConcurrentPositions: Number(body.maxConcurrentPositions) || DEFAULT_AUTO_EVOLUTION_CONFIG.maxConcurrentPositions,
        positionSizeUsd: Number(body.positionSizeUsd) || DEFAULT_AUTO_EVOLUTION_CONFIG.positionSizeUsd,
        enableTrailingStop: body.enableTrailingStop !== false,
        enableTimeBasedExit: body.enableTimeBasedExit !== false,
        maxHoldTimeMin: Number(body.maxHoldTimeMin) || DEFAULT_AUTO_EVOLUTION_CONFIG.maxHoldTimeMin,
      };

      await autoEvolutionLoop.start(config);

      return NextResponse.json({
        data: {
          status: 'started',
          message: `Auto-evolution loop started with ${config.totalCycles} cycles, interval ${config.intervalMs! / 1000}s`,
          config,
        },
      });
    }

    if (action === 'stop') {
      autoEvolutionLoop.stop();

      return NextResponse.json({
        data: {
          status: 'stopping',
          message: 'Auto-evolution loop will stop after current cycle completes',
        },
      });
    }

    if (action === 'run_full_pipeline') {
      // Run a complete pipeline cycle: Scan → Generate → Backtest → Rank → Evolve
      // This is a convenience action that runs a single cycle with bootstrap enabled
      if (autoEvolutionLoop.getStatus().isRunning) {
        return NextResponse.json(
          { data: null, error: 'Auto-evolution is already running' },
          { status: 409 },
        );
      }

      const evolutionConfig: EvolutionConfig = {
        maxIterations: body.evolutionConfig?.maxIterations ?? DEFAULT_EVOLUTION_CONFIG.maxIterations,
        improvementThreshold: body.evolutionConfig?.improvementThreshold ?? DEFAULT_EVOLUTION_CONFIG.improvementThreshold,
        mutationRate: body.evolutionConfig?.mutationRate ?? DEFAULT_EVOLUTION_CONFIG.mutationRate,
        topN: body.evolutionConfig?.topN ?? DEFAULT_EVOLUTION_CONFIG.topN,
        capital: body.evolutionConfig?.capital ?? DEFAULT_EVOLUTION_CONFIG.capital,
      };

      const config: Partial<AutoEvolutionConfig> = {
        totalCycles: 1,
        intervalMs: 0,
        evolutionConfig,
        minSharpeRatio: Number(body.minSharpeRatio) || DEFAULT_AUTO_EVOLUTION_CONFIG.minSharpeRatio,
        minWinRate: Number(body.minWinRate) || DEFAULT_AUTO_EVOLUTION_CONFIG.minWinRate,
        maxConcurrentPositions: Number(body.maxConcurrentPositions) || DEFAULT_AUTO_EVOLUTION_CONFIG.maxConcurrentPositions,
        positionSizeUsd: Number(body.positionSizeUsd) || DEFAULT_AUTO_EVOLUTION_CONFIG.positionSizeUsd,
      };

      const result = await autoEvolutionLoop.runSingleCycle(config);

      return NextResponse.json({
        data: {
          status: result ? 'completed' : 'failed',
          message: result
            ? 'Full pipeline cycle completed (Scan → Generate → Backtest → Evaluate → Evolve)'
            : 'Full pipeline cycle failed',
          cycleResult: result,
        },
      });
    }

    if (action === 'run_single_cycle') {
      if (autoEvolutionLoop.getStatus().isRunning) {
        return NextResponse.json(
          { data: null, error: 'Auto-evolution is already running' },
          { status: 409 },
        );
      }

      const evolutionConfig: EvolutionConfig = {
        maxIterations: body.evolutionConfig?.maxIterations ?? DEFAULT_EVOLUTION_CONFIG.maxIterations,
        improvementThreshold: body.evolutionConfig?.improvementThreshold ?? DEFAULT_EVOLUTION_CONFIG.improvementThreshold,
        mutationRate: body.evolutionConfig?.mutationRate ?? DEFAULT_EVOLUTION_CONFIG.mutationRate,
        topN: body.evolutionConfig?.topN ?? DEFAULT_EVOLUTION_CONFIG.topN,
        capital: body.evolutionConfig?.capital ?? DEFAULT_EVOLUTION_CONFIG.capital,
      };

      const config: Partial<AutoEvolutionConfig> = {
        totalCycles: 1,
        intervalMs: 0,
        evolutionConfig,
        minSharpeRatio: Number(body.minSharpeRatio) || DEFAULT_AUTO_EVOLUTION_CONFIG.minSharpeRatio,
        minWinRate: Number(body.minWinRate) || DEFAULT_AUTO_EVOLUTION_CONFIG.minWinRate,
        maxConcurrentPositions: Number(body.maxConcurrentPositions) || DEFAULT_AUTO_EVOLUTION_CONFIG.maxConcurrentPositions,
        positionSizeUsd: Number(body.positionSizeUsd) || DEFAULT_AUTO_EVOLUTION_CONFIG.positionSizeUsd,
      };

      const result = await autoEvolutionLoop.runSingleCycle(config);

      return NextResponse.json({
        data: {
          status: result ? 'completed' : 'failed',
          message: result ? 'Single evolution cycle completed' : 'Single cycle failed to run',
          cycleResult: result,
        },
      });
    }

    if (action === 'status') {
      const status = autoEvolutionLoop.getStatus();

      // Also get the best results from Hall of Fame across all cycles
      const bestHofEntries = await db.aIBestStrategy.findMany({
        orderBy: { score: 'desc' },
        take: 5,
      });

      // Get completed cycle summaries from DB
      let dbCycles: Array<{
        id: string;
        cycleNumber: number;
        runId: string;
        currentPhase: string;
        status: string;
        bestScore: number;
        improvedCount: number;
        degradedCount: number;
        totalMutations: number;
        strategiesActivated: string;
        startedAt: Date;
        completedAt: Date | null;
        totalDurationMs: number;
      }> = [];

      if (status.runId) {
        dbCycles = await db.evolutionCycle.findMany({
          where: { runId: status.runId },
          orderBy: { cycleNumber: 'asc' },
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
            startedAt: true,
            completedAt: true,
            totalDurationMs: true,
          },
        });
      }

      return NextResponse.json({
        data: {
          isRunning: status.isRunning,
          currentCycle: status.currentCycle,
          totalCycles: status.totalCycles,
          currentPhase: status.currentPhase,
          progress: status.totalCycles > 0
            ? `${status.currentCycle}/${status.totalCycles} cycles (${Math.round((status.currentCycle / status.totalCycles) * 100)}%)`
            : '0/0',
          stopRequested: status.stopRequested,
          startedAt: status.startedAt,
          lastError: status.lastError,
          config: {
            totalCycles: status.config.totalCycles,
            intervalMs: status.config.intervalMs,
            minSharpeRatio: status.config.minSharpeRatio,
            minWinRate: status.config.minWinRate,
            maxConcurrentPositions: status.config.maxConcurrentPositions,
            positionSizeUsd: status.config.positionSizeUsd,
            enableTrailingStop: status.config.enableTrailingStop,
            enableTimeBasedExit: status.config.enableTimeBasedExit,
            maxHoldTimeMin: status.config.maxHoldTimeMin,
          },
          activeStrategies: status.activeStrategies,
          totalPaperTrades: status.totalPaperTrades,
          totalExitsProcessed: status.totalExitsProcessed,
          totalEvolutions: status.totalEvolutions,
          runId: status.runId,
          bestResultsSoFar: bestHofEntries.map(e => ({
            id: e.id,
            strategyName: e.strategyName,
            category: e.category,
            timeframe: e.timeframe,
            score: e.score,
            sharpeRatio: e.sharpeRatio,
            winRate: e.winRate,
            pnlPct: e.pnlPct,
            totalTrades: e.totalTrades,
          })),
          completedCycles: dbCycles.map(c => ({
            cycleNumber: c.cycleNumber,
            status: c.status,
            currentPhase: c.currentPhase,
            bestScore: c.bestScore,
            improvedCount: c.improvedCount,
            degradedCount: c.degradedCount,
            totalMutations: c.totalMutations,
            strategiesActivated: (() => { try { return JSON.parse(c.strategiesActivated || '[]'); } catch { return []; } })(),
            durationMs: c.totalDurationMs,
            startedAt: c.startedAt,
            completedAt: c.completedAt,
          })),
          lastCycleResult: status.lastCycleResult
            ? {
                cycleNumber: status.lastCycleResult.cycleNumber,
                phase: status.lastCycleResult.phase,
                timestamp: status.lastCycleResult.timestamp,
                evolutionResult: status.lastCycleResult.evolutionResult,
                strategiesActivated: status.lastCycleResult.strategiesActivated,
                entriesExecuted: status.lastCycleResult.entriesExecuted,
                exitsProcessed: status.lastCycleResult.exitsProcessed,
                errors: status.lastCycleResult.errors,
              }
            : null,
        },
      });
    }

    // ============================================================
    // "run_next_cycle" — Frontend-driven discrete cycle trigger
    // Designed for auto-scheduling from the UI: if no cycle is running,
    // execute one discrete cycle and return the result immediately.
    // If a cycle is already running, return its current status.
    // ============================================================
    if (action === 'run_next_cycle') {
      const status = autoEvolutionLoop.getStatus();

      // If already running, return current status (don't 409)
      if (status.isRunning) {
        return NextResponse.json({
          data: {
            status: 'already_running',
            message: 'A cycle is currently running, skipping this trigger',
            currentCycle: status.currentCycle,
            currentPhase: status.currentPhase,
            progress: status.totalCycles > 0
              ? `${status.currentCycle}/${status.totalCycles}`
              : '0/0',
          },
        });
      }

      // Build config for a single discrete cycle
      const evolutionConfig: EvolutionConfig = {
        maxIterations: body.evolutionConfig?.maxIterations ?? DEFAULT_EVOLUTION_CONFIG.maxIterations,
        improvementThreshold: body.evolutionConfig?.improvementThreshold ?? DEFAULT_EVOLUTION_CONFIG.improvementThreshold,
        mutationRate: body.evolutionConfig?.mutationRate ?? DEFAULT_EVOLUTION_CONFIG.mutationRate,
        topN: body.evolutionConfig?.topN ?? DEFAULT_EVOLUTION_CONFIG.topN,
        capital: body.evolutionConfig?.capital ?? DEFAULT_EVOLUTION_CONFIG.capital,
      };

      const config: Partial<AutoEvolutionConfig> = {
        totalCycles: 1,
        intervalMs: 0, // No auto-interval — frontend drives the schedule
        evolutionConfig,
        minSharpeRatio: Number(body.minSharpeRatio) || DEFAULT_AUTO_EVOLUTION_CONFIG.minSharpeRatio,
        minWinRate: Number(body.minWinRate) || DEFAULT_AUTO_EVOLUTION_CONFIG.minWinRate,
        maxConcurrentPositions: Number(body.maxConcurrentPositions) || DEFAULT_AUTO_EVOLUTION_CONFIG.maxConcurrentPositions,
        positionSizeUsd: Number(body.positionSizeUsd) || DEFAULT_AUTO_EVOLUTION_CONFIG.positionSizeUsd,
        enableTrailingStop: body.enableTrailingStop !== false,
        enableTimeBasedExit: body.enableTimeBasedExit !== false,
        maxHoldTimeMin: Number(body.maxHoldTimeMin) || DEFAULT_AUTO_EVOLUTION_CONFIG.maxHoldTimeMin,
      };

      // Determine if we should bootstrap first
      const shouldBootstrap = body.bootstrap !== false;
      let bootstrapResult: unknown = null;

      if (shouldBootstrap) {
        // Check if any completed backtests WITH TRADES exist
        // (backtests with 0 trades are effectively empty — bootstrap should still run)
        const completedBacktests = await db.backtestRun.count({
          where: { status: 'COMPLETED', totalTrades: { gt: 0 } },
        });
        if (completedBacktests === 0) {
          // Run full pipeline (bootstrap) instead of single cycle
          const result = await autoEvolutionLoop.runSingleCycle(config);
          bootstrapResult = result as Awaited<typeof result> | null;
          return NextResponse.json({
            data: {
              status: result ? 'bootstrap_completed' : 'bootstrap_failed',
              message: result
                ? 'Bootstrap pipeline completed (no prior backtests found)'
                : 'Bootstrap pipeline failed',
              wasBootstrap: true,
              cycleResult: result,
            },
          });
        }
      }

      // Run a single discrete evolution cycle
      const result = await autoEvolutionLoop.runSingleCycle(config);

      return NextResponse.json({
        data: {
          status: result ? 'completed' : 'failed',
          message: result
            ? 'Discrete evolution cycle completed'
            : 'Discrete evolution cycle failed',
          wasBootstrap: false,
          cycleResult: result,
        },
      });
    }

    return NextResponse.json(
      { data: null, error: `Unknown action: ${action}. Valid actions: start, stop, status, run_single_cycle, run_full_pipeline, run_next_cycle` },
      { status: 400 },
    );
  } catch (error) {
    console.error('[AutoEvolution API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Auto-evolution API failed' },
      { status: 500 },
    );
  }
}

// ============================================================
// GET /api/auto-evolution
// Get the current status of the auto-evolution loop.
// ============================================================

export async function GET() {
  try {
    const status = autoEvolutionLoop.getStatus();

    // Load DB cycle history when not running (or always as supplement)
    let dbCycleHistory: Array<{
      cycleNumber: number;
      runId: string;
      status: string;
      currentPhase: string;
      bestScore: number;
      improvedCount: number;
      degradedCount: number;
      totalMutations: number;
      strategiesActivated: string[];
      entriesExecuted: string[];
      exitsProcessed: Array<{ backtestId: string; exitReason: string; pnlUsd: number }>;
      errors: string[];
      durationMs: number;
      startedAt: Date;
      completedAt: Date | null;
    }> = [];

    try {
      const dbCycles = await db.evolutionCycle.findMany({
        where: { status: 'COMPLETED' },
        orderBy: { startedAt: 'desc' },
        take: 20,
        select: {
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
        },
      });

      dbCycleHistory = dbCycles.map(c => ({
        cycleNumber: c.cycleNumber,
        runId: c.runId,
        status: c.status,
        currentPhase: c.currentPhase,
        bestScore: c.bestScore,
        improvedCount: c.improvedCount,
        degradedCount: c.degradedCount,
        totalMutations: c.totalMutations,
        strategiesActivated: (() => { try { return JSON.parse(c.strategiesActivated || '[]'); } catch { return []; } })(),
        entriesExecuted: (() => { try { return JSON.parse(c.entriesExecuted || '[]'); } catch { return []; } })(),
        exitsProcessed: (() => { try { return JSON.parse(c.exitsProcessed || '[]'); } catch { return []; } })(),
        errors: (() => { try { return JSON.parse(c.errors || '[]'); } catch { return []; } })(),
        durationMs: c.totalDurationMs,
        startedAt: c.startedAt,
        completedAt: c.completedAt,
      }));
    } catch {
      // DB query failed — return empty history
    }

    return NextResponse.json({
      data: {
        isRunning: status.isRunning,
        currentCycle: status.currentCycle,
        totalCycles: status.totalCycles,
        currentPhase: status.currentPhase,
        progress: status.totalCycles > 0
          ? `${status.currentCycle}/${status.totalCycles} cycles (${Math.round((status.currentCycle / status.totalCycles) * 100)}%)`
          : '0/0',
        stopRequested: status.stopRequested,
        lastCycleAt: status.lastCycleAt,
        lastError: status.lastError,
        startedAt: status.startedAt,
        config: {
          totalCycles: status.config.totalCycles,
          intervalMs: status.config.intervalMs,
          minSharpeRatio: status.config.minSharpeRatio,
          minWinRate: status.config.minWinRate,
          maxConcurrentPositions: status.config.maxConcurrentPositions,
          positionSizeUsd: status.config.positionSizeUsd,
          enableTrailingStop: status.config.enableTrailingStop,
          enableTimeBasedExit: status.config.enableTimeBasedExit,
          maxHoldTimeMin: status.config.maxHoldTimeMin,
        },
        activeStrategies: status.activeStrategies,
        totalPaperTrades: status.totalPaperTrades,
        totalExitsProcessed: status.totalExitsProcessed,
        totalEvolutions: status.totalEvolutions,
        runId: status.runId,
        lastCycleResult: status.lastCycleResult
          ? {
              cycleNumber: status.lastCycleResult.cycleNumber,
              phase: status.lastCycleResult.phase,
              timestamp: status.lastCycleResult.timestamp,
              evolutionResult: status.lastCycleResult.evolutionResult,
              strategiesActivated: status.lastCycleResult.strategiesActivated,
              entriesExecuted: status.lastCycleResult.entriesExecuted,
              exitsProcessed: status.lastCycleResult.exitsProcessed,
              errors: status.lastCycleResult.errors,
            }
          : null,
        completedCycles: status.completedCycles,
        dbCycleHistory,
      },
    });
  } catch (error) {
    console.error('[AutoEvolution API] GET error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Failed to get auto-evolution status' },
      { status: 500 },
    );
  }
}
