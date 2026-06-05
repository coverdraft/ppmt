import { NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface LifecycleState {
  state: string;
  from: string;
  to: string;
  timestamp: string;
  reason: string;
  scores?: {
    robustness: number;
    overfitting: number;
    stability: number;
  };
  capitalAction?: string;
}

interface StrategyLifecycle {
  strategyId: string;
  strategyName: string;
  states: LifecycleState[];
}

/**
 * GET /api/portfolio/lifecycle
 *
 * Returns strategy lifecycle data:
 * - Array of { strategyId, strategyName, states: Array<{ state, from, to, timestamp, reason }> }
 * - Data from DecisionAudit table
 * - Ordered by timestamp
 */
export async function GET() {
  try {
    // Get all audit records, ordered by strategy and timestamp
    const audits = await db.decisionAudit.findMany({
      orderBy: [{ strategyId: 'asc' }, { timestamp: 'asc' }],
      select: {
        id: true,
        strategyId: true,
        timestamp: true,
        decision: true,
        processing: true,
      },
      take: 500,
    });

    // Group by strategy
    const strategyMap = new Map<string, StrategyLifecycle>();

    for (const audit of audits) {
      try {
        const decisionData = JSON.parse(audit.decision) as Record<string, unknown>;
        const processingData = JSON.parse(audit.processing) as Record<string, unknown>;

        const strategyId = audit.strategyId;
        const state = (decisionData.state as string) || 'UNKNOWN';
        const strategyName = (decisionData.strategyName as string) || strategyId;
        const capitalAction = (decisionData.capitalAction as string) || 'MAINTAIN';
        const scores = processingData.scores as { robustness: number; overfitting: number; stability: number } | undefined;

        // Determine the reason from veto results
        const vetoResults = processingData.vetoResults as Array<{ veto: string; passed: boolean; reason: string }> | undefined;
        const failedVetos = vetoResults?.filter(v => !v.passed) ?? [];
        const reason = failedVetos.length > 0
          ? failedVetos.map(v => v.veto).join(', ')
          : `Signal quality: ${processingData.signalQuality || 'N/A'}`;

        if (!strategyMap.has(strategyId)) {
          strategyMap.set(strategyId, {
            strategyId,
            strategyName,
            states: [],
          });
        }

        const lifecycle = strategyMap.get(strategyId)!;
        const prevState = lifecycle.states.length > 0
          ? lifecycle.states[lifecycle.states.length - 1].state
          : 'NONE';

        lifecycle.states.push({
          state,
          from: prevState,
          to: state,
          timestamp: audit.timestamp.toISOString(),
          reason,
          scores,
          capitalAction,
        });
      } catch {
        // Skip malformed audit records
      }
    }

    const lifecycles = Array.from(strategyMap.values());

    // Also include trading systems that have no audit records yet
    const systems = await db.tradingSystem.findMany({
      where: { isActive: true },
      select: { id: true, name: true, createdAt: true },
    });

    for (const system of systems) {
      if (!strategyMap.has(system.id)) {
        lifecycles.push({
          strategyId: system.id,
          strategyName: system.name,
          states: [{
            state: 'ACTIVE',
            from: 'NONE',
            to: 'ACTIVE',
            timestamp: system.createdAt?.toISOString() ?? new Date().toISOString(),
            reason: 'System activated (no SDE evaluation yet)',
          }],
        });
      }
    }

    return NextResponse.json({
      data: {
        lifecycles,
        totalStrategies: lifecycles.length,
        stateDistribution: {
          ACTIVE: lifecycles.filter(l => l.states.length > 0 && l.states[l.states.length - 1].state === 'ACTIVE').length,
          CONDITIONAL: lifecycles.filter(l => l.states.length > 0 && l.states[l.states.length - 1].state === 'CONDITIONAL').length,
          PAUSED: lifecycles.filter(l => l.states.length > 0 && l.states[l.states.length - 1].state === 'PAUSED').length,
          REJECTED: lifecycles.filter(l => l.states.length > 0 && l.states[l.states.length - 1].state === 'REJECTED').length,
        },
      },
    });
  } catch (error) {
    console.error('[Portfolio Lifecycle API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Failed to fetch lifecycle data' },
      { status: 500 },
    );
  }
}
