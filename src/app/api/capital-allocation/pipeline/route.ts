/**
 * POST /api/capital-allocation/pipeline
 *
 * Runs the full capital allocation pipeline for a strategy or portfolio:
 * 1. SDE validation (vetos + scores + state)
 * 2. Kill switch check
 * 3. Concentration check
 * 4. Correlation check
 * 5. Capital allocation (method selection + sizing)
 * 6. Return comprehensive result
 */

import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// TYPES
// ============================================================

interface PipelineRequest {
  strategyId?: string;
  portfolioReview?: boolean;
}

interface PipelineKillSwitchStatus {
  allowed: boolean;
  reason?: string;
}

interface PipelineConcentrationStatus {
  allowed: boolean;
  tokenPct?: number;
  chainPct?: number;
}

interface PipelineCorrelationStatus {
  allowed: boolean;
  avgCorrelation?: number;
  maxPairwise?: number;
}

interface PipelineFinalAllocation {
  method: string;
  sizeUsd: number;
  targetPct: number;
  adjustedReason: string;
}

interface PipelineResult {
  strategyId: string;
  sdeDecision: Record<string, unknown>;
  killSwitchStatus: PipelineKillSwitchStatus;
  concentrationStatus: PipelineConcentrationStatus;
  correlationStatus: PipelineCorrelationStatus;
  finalAllocation: PipelineFinalAllocation;
}

// ============================================================
// POST HANDLER
// ============================================================

export async function POST(request: NextRequest) {
  try {
    const body = await request.json() as PipelineRequest;

    const { strategyDecisionEngine } = await import('@/lib/services/strategy/strategy-decision-engine');
    const { killSwitchService } = await import('@/lib/services/risk/kill-switch-service');
    const { strategyCorrelationService } = await import('@/lib/services/risk/strategy-correlation-service');
    const { db } = await import('@/lib/db');

    // ---- Portfolio Review: review all active strategies ----
    if (body.portfolioReview) {
      const systems = await db.tradingSystem.findMany({
        where: { isActive: true },
        take: 20,
      });

      const results: PipelineResult[] = [];

      for (const system of systems) {
        try {
          const result = await runPipelineForStrategy(
            system.id,
            strategyDecisionEngine,
            killSwitchService,
            strategyCorrelationService,
          );
          results.push(result);
        } catch (err) {
          results.push({
            strategyId: system.id,
            sdeDecision: { state: 'REJECTED', error: String(err) },
            killSwitchStatus: { allowed: false, reason: 'Pipeline error' },
            concentrationStatus: { allowed: false },
            correlationStatus: { allowed: false },
            finalAllocation: {
              method: 'EQUAL_WEIGHT',
              sizeUsd: 0,
              targetPct: 0,
              adjustedReason: `Pipeline error: ${err instanceof Error ? err.message : 'unknown'}`,
            },
          });
        }
      }

      return NextResponse.json({
        data: {
          results,
          totalStrategies: results.length,
          timestamp: new Date().toISOString(),
        },
      });
    }

    // ---- Single Strategy Pipeline ----
    if (!body.strategyId) {
      return NextResponse.json(
        { data: null, error: 'strategyId is required when portfolioReview is not true' },
        { status: 400 },
      );
    }

    const result = await runPipelineForStrategy(
      body.strategyId,
      strategyDecisionEngine,
      killSwitchService,
      strategyCorrelationService,
    );

    return NextResponse.json({
      data: {
        result,
        timestamp: new Date().toISOString(),
      },
    });
  } catch (error) {
    console.error('[CapitalAllocation/Pipeline] Error:', error);
    return NextResponse.json(
      { data: null, error: `Pipeline failed: ${error instanceof Error ? error.message : 'unknown'}` },
      { status: 500 },
    );
  }
}

// ============================================================
// PIPELINE EXECUTION FOR A SINGLE STRATEGY
// ============================================================

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function runPipelineForStrategy(
  strategyId: string,
  sde: any,
  ks: any,
  scs: any,
): Promise<PipelineResult> {
  const { db } = await import('@/lib/db');

  // Build a default portfolio state
  const portfolioState = {
    totalCapitalUsd: 10000,
    currentDrawdownPct: 0,
    activeStrategies: 0,
    marketVolatility: 50,
    marketRegime: 'SIDEWAYS',
  };

  // Try to get capital and drawdown from latest session
  try {
    const latestSession = await db.paperTradingSession.findFirst({
      orderBy: { createdAt: 'desc' },
    });
    if (latestSession) {
      portfolioState.totalCapitalUsd = latestSession.currentCapital;
      portfolioState.currentDrawdownPct = latestSession.peakCapital > latestSession.currentCapital
        ? ((latestSession.peakCapital - latestSession.currentCapital) / latestSession.peakCapital) * 100
        : 0;
    }

    const activeSystems = await db.tradingSystem.count({
      where: { isActive: true },
    });
    portfolioState.activeStrategies = activeSystems;
  } catch { /* use defaults */ }

  // STEP 1: SDE Validation
  let sdeDecision: Record<string, unknown>;
  try {
    const sdeInput = await sde.buildInputFromStrategyId(
      strategyId,
      portfolioState,
    );

    if (sdeInput) {
      const decision = await sde.validate(sdeInput);
      sdeDecision = {
        strategyId: decision.strategyId,
        strategyName: decision.strategyName,
        state: decision.state,
        capitalAction: decision.capitalAction,
        signalQuality: decision.signalQuality,
        scores: decision.scores,
        capitalRecommendation: decision.capitalRecommendation,
        vetoResults: decision.vetoResults,
        recommendations: decision.recommendations,
      };
    } else {
      sdeDecision = {
        state: 'REJECTED',
        capitalAction: 'EXIT',
        reason: 'Strategy not found in DB',
      };
    }
  } catch (err) {
    sdeDecision = {
      state: 'REJECTED',
      capitalAction: 'EXIT',
      reason: `SDE validation failed: ${err instanceof Error ? err.message : 'unknown'}`,
    };
  }

  // STEP 2: Kill Switch Check
  // CRITICAL FIX: Fail-safe default is DENY, not ALLOW — a failed check must block allocation
  let killSwitchStatus: PipelineKillSwitchStatus = { allowed: false, reason: 'Kill switch check not yet completed' };
  try {
    const killState = ks.getState();
    if (killState.globalPause) {
      killSwitchStatus = {
        allowed: false,
        reason: killState.globalPauseReason || 'Global pause active',
      };
    } else if (killState.strategyPauses.has(strategyId)) {
      const pauseInfo = killState.strategyPauses.get(strategyId)!;
      killSwitchStatus = {
        allowed: false,
        reason: pauseInfo.reason,
      };
    } else if (killState.strategyDDTriggered && killState.strategyDDTriggered.has(strategyId)) {
      killSwitchStatus = {
        allowed: false,
        reason: `Strategy ${strategyId} drawdown kill switch active`,
      };
    } else {
      killSwitchStatus = { allowed: true };
    }
  } catch (err) {
    killSwitchStatus = { allowed: false, reason: `Kill switch check failed: ${err instanceof Error ? err.message : 'unknown'}` };
  }

  // STEP 3: Concentration Check (includes proposed allocation)
  // CRITICAL FIX: Fail-safe default is DENY — a failed check must block allocation
  let concentrationStatus: PipelineConcentrationStatus = { allowed: false };
  try {
    const budget = await ks.loadRiskBudget();
    const existingPositions = await db.paperTradingPosition.findMany({
      where: { status: 'OPEN', strategyName: strategyId },
    });
    const totalCapital = portfolioState.totalCapitalUsd;
    const existingPct = totalCapital > 0
      ? existingPositions.reduce((sum, p) => sum + p.sizeUsd, 0) / totalCapital * 100
      : 0;
    // Include proposed new allocation in concentration check
    const proposedSizeUsd = (sdeDecision.capitalRecommendation as { sizeUsd?: number } | undefined)?.sizeUsd ?? 0;
    const proposedPct = existingPct + (totalCapital > 0 ? proposedSizeUsd / totalCapital * 100 : 0);

    if (proposedPct >= budget.maxConcentrationPct) {
      concentrationStatus = {
        allowed: false,
        tokenPct: proposedPct,
      };
    }

    // Also check chain concentration — look up chain from strategy or existing positions
    const allOpenPositions = await db.paperTradingPosition.findMany({
      where: { status: 'OPEN' },
    });
    const chainOfStrategy = allOpenPositions.find(p => p.strategyName === strategyId)?.chain
      || (await db.tradingSystem.findUnique({ where: { id: strategyId }, select: { name: true } }))?.name;
    if (chainOfStrategy && totalCapital > 0) {
      const chainPct = allOpenPositions
        .filter(p => p.chain === chainOfStrategy)
        .reduce((sum, p) => sum + p.sizeUsd, 0) / totalCapital * 100;
      const proposedChainPct = chainPct + (proposedSizeUsd / totalCapital * 100);
      if (proposedChainPct > budget.maxChainPct) {
        concentrationStatus = {
          allowed: false,
          tokenPct: proposedPct,
          chainPct: proposedChainPct,
        };
      }
    }
    // Only set allowed=true if no check failed (concentrationStatus.allowed is still not false)
    if (concentrationStatus.allowed !== false) {
      concentrationStatus = { allowed: true };
    }
  } catch (err) {
    concentrationStatus = { allowed: false }; // Fail-safe: block on error
  }

  // STEP 4: Correlation Check (with existing strategies)
  // CRITICAL FIX: Fail-safe default is DENY — a failed check must block allocation
  let correlationStatus: PipelineCorrelationStatus = { allowed: false };
  try {
    const matrix = await scs.getCurrentCorrelationMatrix();
    const budget = await ks.loadRiskBudget();
    // Get active strategy IDs for proper correlation check
    const activeSystems = await db.tradingSystem.findMany({
      where: { isActive: true },
      select: { id: true },
    });
    const existingStrategyIds = activeSystems.map(s => s.id).filter(id => id !== strategyId);
    const corrCheck = scs.wouldExceedCorrelationLimit(
      existingStrategyIds,
      strategyId,
      budget.maxCorrelatedPct,
      matrix,
    );
    correlationStatus = {
      allowed: corrCheck.allowed,
      avgCorrelation: corrCheck.avgCorrelation,
      maxPairwise: corrCheck.maxPairwise,
    };
  } catch (err) {
    correlationStatus = { allowed: false }; // Fail-safe: block on error
  }

  // STEP 5: Capital Allocation (from SDE decision)
  let finalAllocation: PipelineFinalAllocation;
  const sdeRec = sdeDecision.capitalRecommendation as { method?: string; sizeUsd?: number; targetPct?: number; reason?: string } | undefined;
  if (sdeRec && typeof sdeRec === 'object') {
    let adjustedSize = sdeRec.sizeUsd ?? 0;
    let adjustedPct = sdeRec.targetPct ?? 0;
    const reasons: string[] = [sdeRec.reason || 'SDE recommendation'];

    // Apply kill switch: if not allowed, set to 0
    if (!killSwitchStatus.allowed) {
      adjustedSize = 0;
      adjustedPct = 0;
      reasons.push(`Kill switch: ${killSwitchStatus.reason}`);
    }

    // Apply concentration: HARD CONSTRAINT per architecture Decision 3 ("Portfolio > Strategy")
    // Concentration is a hard limit, not a suggestion — reject position entirely
    if (!concentrationStatus.allowed) {
      adjustedSize = 0;
      adjustedPct = 0;
      reasons.push(`BLOCKED: Concentration limit reached (${concentrationStatus.tokenPct?.toFixed(1)}%) — hard constraint`);
    }

    // Apply correlation: reduce to proportional limit (soft but meaningful)
    if (!correlationStatus.allowed && adjustedSize > 0) {
      const reductionFactor = Math.max(0.1, 1 - (correlationStatus.avgCorrelation ?? 0));
      adjustedSize = Math.round(adjustedSize * reductionFactor * 100) / 100;
      adjustedPct = Math.round(adjustedPct * reductionFactor * 100) / 100;
      reasons.push(`Correlation-adjusted: avg=${((correlationStatus.avgCorrelation ?? 0) * 100).toFixed(1)}%, factor=${reductionFactor.toFixed(2)}`);
    }

    finalAllocation = {
      method: sdeRec.method || 'EQUAL_WEIGHT',
      sizeUsd: Math.round(adjustedSize * 100) / 100,
      targetPct: Math.round(adjustedPct * 100) / 100,
      adjustedReason: reasons.join(' | '),
    };
  } else {
    finalAllocation = {
      method: 'EQUAL_WEIGHT',
      sizeUsd: 0,
      targetPct: 0,
      adjustedReason: sdeDecision.state === 'REJECTED' || sdeDecision.state === 'PAUSED'
        ? 'Strategy rejected or paused'
        : 'No SDE recommendation available',
    };
  }

  return {
    strategyId,
    sdeDecision,
    killSwitchStatus,
    concentrationStatus,
    correlationStatus,
    finalAllocation,
  };
}
