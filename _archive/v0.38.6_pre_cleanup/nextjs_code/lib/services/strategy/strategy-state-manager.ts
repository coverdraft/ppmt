import { db } from '@/lib/db';

// ============================================================
// TYPES
// ============================================================

export type StrategyStatus =
  | 'IDLE'
  | 'BACKTESTING'
  | 'PAPER_TRADING'
  | 'LIVE'
  | 'PAUSED'
  | 'ERROR'
  | 'EVOLVED';

export type TriggerReason =
  | 'MANUAL'
  | 'AUTO_BACKTEST'
  | 'AUTO_EVOLVE'
  | 'RISK_LIMIT'
  | 'SDE_VALIDATION_FAILED'
  | 'SCHEDULER';

export interface StateTransitionInput {
  systemId: string;
  newStatus: StrategyStatus;
  triggerReason: TriggerReason;
  metadata?: Record<string, unknown>;
  metrics?: {
    totalPnlUsd?: number;
    totalPnlPct?: number;
    sharpeRatio?: number;
    winRate?: number;
    totalTrades?: number;
    openPositions?: number;
  };
  evolution?: {
    generation?: number;
    parentId?: string;
    improvementPct?: number;
  };
}

export interface StrategyWithState {
  id: string;
  name: string;
  category: string;
  icon: string;
  isActive: boolean;
  isPaperTrading: boolean;
  version: number;
  parentSystemId: string | null;
  totalBacktests: number;
  bestSharpe: number;
  bestWinRate: number;
  bestPnlPct: number;
  createdAt: Date;
  updatedAt: Date;
  currentState: {
    status: StrategyStatus;
    previousStatus: string | null;
    triggerReason: TriggerReason;
    totalPnlUsd: number;
    totalPnlPct: number;
    sharpeRatio: number;
    winRate: number;
    totalTrades: number;
    openPositions: number;
    generation: number;
    improvementPct: number;
    recordedAt: Date;
  } | null;
  stateHistory: Array<{
    id: string;
    status: string;
    previousStatus: string | null;
    triggerReason: string;
    totalPnlUsd: number;
    totalPnlPct: number;
    sharpeRatio: number;
    winRate: number;
    totalTrades: number;
    openPositions: number;
    generation: number;
    parentId: string | null;
    improvementPct: number;
    metadata: string;
    createdAt: Date;
  }>;
}

export interface StateStatistics {
  statusCounts: Record<string, number>;
  totalStrategies: number;
  avgTimeInState: Record<string, number>; // in minutes
  recentTransitions: Array<{
    systemId: string;
    systemName: string;
    fromStatus: string | null;
    toStatus: string;
    triggerReason: string;
    createdAt: Date;
  }>;
  transitionCounts: Record<string, number>; // "FROM->TO" => count
}

// ============================================================
// STRATEGY STATE MANAGER SERVICE
// ============================================================

class StrategyStateManager {
  /**
   * Record a state transition for a strategy
   */
  async recordStateTransition(input: StateTransitionInput) {
    const { systemId, newStatus, triggerReason, metadata, metrics, evolution } = input;

    // Get the previous state for this system
    const previousState = await db.strategyStateHistory.findFirst({
      where: { systemId },
      orderBy: { createdAt: 'desc' },
    });

    const previousStatus = previousState?.status || null;

    // Don't record duplicate state if nothing changed
    if (previousStatus === newStatus && triggerReason !== 'MANUAL') {
      return previousState;
    }

    const record = await db.strategyStateHistory.create({
      data: {
        systemId,
        status: newStatus,
        previousStatus,
        totalPnlUsd: metrics?.totalPnlUsd ?? 0,
        totalPnlPct: metrics?.totalPnlPct ?? 0,
        sharpeRatio: metrics?.sharpeRatio ?? 0,
        winRate: metrics?.winRate ?? 0,
        totalTrades: metrics?.totalTrades ?? 0,
        openPositions: metrics?.openPositions ?? 0,
        triggerReason,
        metadata: JSON.stringify(metadata || {}),
        generation: evolution?.generation ?? previousState?.generation ?? 1,
        parentId: evolution?.parentId ?? null,
        improvementPct: evolution?.improvementPct ?? 0,
      },
    });

    // Fire alert for state change
    try {
      const { alertEngine } = await import('@/lib/services/risk/alert-engine');
      await alertEngine.onStrategyStateChanged(systemId, previousStatus || 'NONE', newStatus, triggerReason);
    } catch (error) {
      // Alert engine is optional, don't break the flow
      console.warn('[StrategyStateManager] Alert engine error:', error);
    }

    return record;
  }

  /**
   * Get the full timeline of state changes for a strategy
   */
  async getStrategyTimeline(systemId: string, limit = 100) {
    const history = await db.strategyStateHistory.findMany({
      where: { systemId },
      orderBy: { createdAt: 'desc' },
      take: limit,
    });

    return history;
  }

  /**
   * Get all strategies with their current state and recent history
   */
  async getCurrentStates(options?: {
    status?: StrategyStatus;
    category?: string;
    limit?: number;
  }) {
    const { status, category, limit = 50 } = options || {};

    // Build where clause for trading systems
    const where: Record<string, unknown> = {};
    if (category) where.category = category;

    const systems = await db.tradingSystem.findMany({
      where,
      orderBy: { updatedAt: 'desc' },
      take: limit,
      include: {
        backtests: {
          where: { status: 'COMPLETED' },
          orderBy: { completedAt: 'desc' },
          take: 1,
          select: {
            sharpeRatio: true,
            winRate: true,
            totalPnlPct: true,
            totalTrades: true,
            completedAt: true,
          },
        },
      },
    });

    // Get current state for each system
    const results: StrategyWithState[] = [];

    // --- Batch query to avoid N+1 ---
    // Previously, this loop did findFirst + findMany per system (2 DB queries per system).
    // Now we fetch all states at once and group by systemId in memory.
    const systemIds = systems.map(s => s.id);
    const allStates = await db.strategyStateHistory.findMany({
      where: { systemId: { in: systemIds } },
      orderBy: { createdAt: 'desc' },
    });

    // Group by systemId, preserving desc order (latest first)
    const statesBySystem = new Map<string, typeof allStates>();
    for (const state of allStates) {
      const arr = statesBySystem.get(state.systemId) ?? [];
      arr.push(state);
      statesBySystem.set(state.systemId, arr);
    }

    for (const system of systems) {
      const systemStates = statesBySystem.get(system.id) ?? [];

      // Get latest state (first in desc-ordered array)
      const latestState = systemStates[0] ?? null;

      // Determine derived status from TradingSystem fields if no state history
      const derivedStatus = this.deriveStatus(system);

      // Filter by status if requested
      const effectiveStatus = latestState?.status || derivedStatus;
      if (status && effectiveStatus !== status) continue;

      // Get recent history (last 10 from the pre-fetched array)
      const stateHistory = systemStates.slice(0, 10);

      results.push({
        id: system.id,
        name: system.name,
        category: system.category,
        icon: system.icon,
        isActive: system.isActive,
        isPaperTrading: system.isPaperTrading,
        version: system.version,
        parentSystemId: system.parentSystemId,
        totalBacktests: system.totalBacktests,
        bestSharpe: system.bestSharpe,
        bestWinRate: system.bestWinRate,
        bestPnlPct: system.bestPnlPct,
        createdAt: system.createdAt,
        updatedAt: system.updatedAt,
        currentState: latestState
          ? {
              status: latestState.status as StrategyStatus,
              previousStatus: latestState.previousStatus,
              triggerReason: latestState.triggerReason as TriggerReason,
              totalPnlUsd: latestState.totalPnlUsd,
              totalPnlPct: latestState.totalPnlPct,
              sharpeRatio: latestState.sharpeRatio,
              winRate: latestState.winRate,
              totalTrades: latestState.totalTrades,
              openPositions: latestState.openPositions,
              generation: latestState.generation,
              improvementPct: latestState.improvementPct,
              recordedAt: latestState.createdAt,
            }
          : {
              status: derivedStatus,
              previousStatus: null,
              triggerReason: 'MANUAL' as TriggerReason,
              totalPnlUsd: 0,
              totalPnlPct: system.bestPnlPct,
              sharpeRatio: system.bestSharpe,
              winRate: system.bestWinRate,
              totalTrades: 0,
              openPositions: 0,
              generation: 1,
              improvementPct: 0,
              recordedAt: system.createdAt,
            },
        stateHistory: stateHistory.map((h) => ({
          id: h.id,
          status: h.status,
          previousStatus: h.previousStatus,
          triggerReason: h.triggerReason,
          totalPnlUsd: h.totalPnlUsd,
          totalPnlPct: h.totalPnlPct,
          sharpeRatio: h.sharpeRatio,
          winRate: h.winRate,
          totalTrades: h.totalTrades,
          openPositions: h.openPositions,
          generation: h.generation,
          parentId: h.parentId,
          improvementPct: h.improvementPct,
          metadata: h.metadata,
          createdAt: h.createdAt,
        })),
      });
    }

    return results;
  }

  /**
   * Get aggregate state statistics
   */
  async getStateStatistics(): Promise<StateStatistics> {
    // Get all current states (latest per system)
    const allSystems = await db.tradingSystem.findMany({
      select: { id: true, name: true, isActive: true, isPaperTrading: true, totalBacktests: true },
    });

    const statusCounts: Record<string, number> = {};
    const avgTimeInState: Record<string, number> = {};

    // --- Batch query to avoid N+1 ---
    // Previously: findFirst per system in a loop. Now: single query, group in memory.
    const systemIds = allSystems.map(s => s.id);
    const allLatestStates = await db.strategyStateHistory.findMany({
      where: { systemId: { in: systemIds } },
      orderBy: { createdAt: 'desc' },
    });

    // Group by systemId and take latest per system
    const latestBySystem = new Map<string, typeof allLatestStates[0]>();
    for (const state of allLatestStates) {
      if (!latestBySystem.has(state.systemId)) {
        latestBySystem.set(state.systemId, state);
      }
    }

    // Count statuses
    for (const system of allSystems) {
      const latestState = latestBySystem.get(system.id) ?? null;

      const status = latestState?.status || this.deriveStatus(system);
      statusCounts[status] = (statusCounts[status] || 0) + 1;

      // Calculate time in current state
      if (latestState) {
        const timeInState = Date.now() - new Date(latestState.createdAt).getTime();
        const minutes = timeInState / (1000 * 60);
        if (!avgTimeInState[status]) avgTimeInState[status] = 0;
        avgTimeInState[status] += minutes;
      }
    }

    // Average the times
    for (const status of Object.keys(avgTimeInState)) {
      if (statusCounts[status] > 0) {
        avgTimeInState[status] = Math.round(avgTimeInState[status] / statusCounts[status]);
      }
    }

    // Recent transitions
    const recentHistory = await db.strategyStateHistory.findMany({
      orderBy: { createdAt: 'desc' },
      take: 20,
    });

    const systemNameMap = new Map(allSystems.map((s) => [s.id, s.name]));

    const recentTransitions = recentHistory.map((h) => ({
      systemId: h.systemId,
      systemName: systemNameMap.get(h.systemId) || 'Unknown',
      fromStatus: h.previousStatus,
      toStatus: h.status,
      triggerReason: h.triggerReason,
      createdAt: h.createdAt,
    }));

    // Transition counts
    const allHistory = await db.strategyStateHistory.findMany({
      select: { previousStatus: true, status: true },
    });

    const transitionCounts: Record<string, number> = {};
    for (const h of allHistory) {
      const key = h.previousStatus
        ? `${h.previousStatus}->${h.status}`
        : `INIT->${h.status}`;
      transitionCounts[key] = (transitionCounts[key] || 0) + 1;
    }

    return {
      statusCounts,
      totalStrategies: allSystems.length,
      avgTimeInState,
      recentTransitions,
      transitionCounts,
    };
  }

  /**
   * Get full history for a specific strategy with date range filtering
   */
  async getStrategyHistory(
    systemId: string,
    options?: {
      startDate?: string;
      endDate?: string;
      limit?: number;
    },
  ) {
    const { startDate, endDate, limit = 100 } = options || {};

    const where: Record<string, unknown> = { systemId };

    if (startDate || endDate) {
      const createdAt: Record<string, Date> = {};
      if (startDate) createdAt.gte = new Date(startDate);
      if (endDate) createdAt.lte = new Date(endDate);
      where.createdAt = createdAt;
    }

    const history = await db.strategyStateHistory.findMany({
      where,
      orderBy: { createdAt: 'desc' },
      take: limit,
    });

    return history;
  }

  /**
   * Derive status from TradingSystem fields when no state history exists
   */
  private deriveStatus(system: {
    isActive: boolean;
    isPaperTrading: boolean;
    totalBacktests: number;
  }): StrategyStatus {
    if (system.isActive && system.isPaperTrading) return 'PAPER_TRADING';
    if (system.isActive) return 'LIVE';
    if (system.totalBacktests > 0) return 'IDLE'; // was tested but now idle
    return 'IDLE';
  }

  /**
   * Auto-record state transitions based on system events
   * Call this when a backtest starts
   */
  async onBacktestStart(systemId: string) {
    return this.recordStateTransition({
      systemId,
      newStatus: 'BACKTESTING',
      triggerReason: 'AUTO_BACKTEST',
      metadata: { event: 'backtest_started' },
    });
  }

  /**
   * Auto-record state transitions when a backtest completes
   */
  async onBacktestComplete(systemId: string, results: {
    sharpeRatio?: number;
    winRate?: number;
    totalPnlPct?: number;
    totalTrades?: number;
  }) {
    // After backtest, return to previous non-backtesting state or IDLE
    const prevState = await db.strategyStateHistory.findFirst({
      where: { systemId, status: { not: 'BACKTESTING' } },
      orderBy: { createdAt: 'desc' },
    });

    const returnStatus: StrategyStatus = (prevState?.status as StrategyStatus) || 'IDLE';

    return this.recordStateTransition({
      systemId,
      newStatus: returnStatus,
      triggerReason: 'AUTO_BACKTEST',
      metrics: {
        sharpeRatio: results.sharpeRatio,
        winRate: results.winRate,
        totalPnlPct: results.totalPnlPct,
        totalTrades: results.totalTrades,
      },
      metadata: { event: 'backtest_completed' },
    });
  }

  /**
   * Auto-record when strategy is activated
   */
  async onStrategyActivated(systemId: string, isPaperTrading: boolean) {
    return this.recordStateTransition({
      systemId,
      newStatus: isPaperTrading ? 'PAPER_TRADING' : 'LIVE',
      triggerReason: 'MANUAL',
      metadata: { event: 'strategy_activated', isPaperTrading },
    });
  }

  /**
   * Auto-record when strategy is deactivated
   */
  async onStrategyDeactivated(systemId: string) {
    return this.recordStateTransition({
      systemId,
      newStatus: 'PAUSED',
      triggerReason: 'MANUAL',
      metadata: { event: 'strategy_deactivated' },
    });
  }

  /**
   * Auto-record when an evolution produces a new strategy
   */
  async onEvolution(
    parentSystemId: string,
    childSystemId: string,
    improvementPct: number,
    generation: number,
  ) {
    // Record parent transition to EVOLVED
    await this.recordStateTransition({
      systemId: parentSystemId,
      newStatus: 'EVOLVED',
      triggerReason: 'AUTO_EVOLVE',
      evolution: {
        generation,
        parentId: undefined,
        improvementPct,
      },
      metadata: { event: 'evolved', childSystemId },
    });

    // Record child starting as IDLE
    await this.recordStateTransition({
      systemId: childSystemId,
      newStatus: 'IDLE',
      triggerReason: 'AUTO_EVOLVE',
      evolution: {
        generation,
        parentId: parentSystemId,
        improvementPct,
      },
      metadata: { event: 'evolution_created', parentSystemId },
    });
  }

  /**
   * Auto-record when risk limits trigger a pause
   */
  async onRiskLimitTriggered(systemId: string, reason: string) {
    return this.recordStateTransition({
      systemId,
      newStatus: 'PAUSED',
      triggerReason: 'RISK_LIMIT',
      metadata: { event: 'risk_limit_triggered', reason },
    });
  }

  /**
   * Auto-record scheduler-triggered state changes
   */
  async onSchedulerAction(systemId: string, newStatus: StrategyStatus, reason: string) {
    return this.recordStateTransition({
      systemId,
      newStatus,
      triggerReason: 'SCHEDULER',
      metadata: { event: 'scheduler_action', reason },
    });
  }

  /**
   * Batch helper: fetch latest state for multiple systems in a single query.
   * Useful for callers that need to check the current state of many systems
   * without triggering N+1 queries.
   *
   * Returns a Map of systemId → latest StrategyStateHistory record.
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async batchGetLatestStates(systemIds: string[]): Promise<Map<string, any>> {
    if (systemIds.length === 0) return new Map();

    const allStates = await db.strategyStateHistory.findMany({
      where: { systemId: { in: systemIds } },
      orderBy: { createdAt: 'desc' },
    });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const latestBySystem = new Map<string, any>();
    for (const state of allStates) {
      if (!latestBySystem.has(state.systemId)) {
        latestBySystem.set(state.systemId, state);
      }
    }
    return latestBySystem;
  }

  // TODO: onBacktestComplete() still does a findFirst per systemId call.
  // If called in a batch loop (e.g. after running multiple backtests),
  // this becomes N+1. Consider adding a batchOnBacktestComplete() method
  // that pre-fetches states for all systemIds and reuses them.
}

// Singleton instance
export const strategyStateManager = new StrategyStateManager();
