import { NextRequest, NextResponse } from 'next/server';
import { portfolioIntelligenceEngine } from '@/lib/services/portfolio/portfolio-intelligence-engine';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const VALID_SCENARIOS = [
  'market_crash_20',
  'crypto_winter_50',
  'flash_crash_10',
  'correlation_break',
  'liquidity_crisis',
];

/**
 * POST /api/portfolio/stress-test
 * Run stress test on current portfolio.
 * Body: { scenario?: string } (one of the predefined scenarios, or empty for all)
 * Response: StressTestResult
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { scenario } = body;

    if (scenario && !VALID_SCENARIOS.includes(scenario)) {
      return NextResponse.json(
        { data: null, error: `Invalid scenario. Must be one of: ${VALID_SCENARIOS.join(', ')}` },
        { status: 400 }
      );
    }

    const { db } = await import('@/lib/db');
    const session = await db.paperTradingSession.findFirst({
      orderBy: { createdAt: 'desc' },
    });

    const openPositions = session
      ? await db.paperTradingPosition.findMany({
          where: { status: 'OPEN', runId: session.id },
        })
      : [];

    if (openPositions.length === 0) {
      return NextResponse.json({
        data: {
          scenarioResults: [],
          worstCase: null,
          averageImpactPct: 0,
          computedAt: new Date().toISOString(),
        },
      });
    }

    const positions = openPositions.map(p => ({
      id: p.id,
      tokenAddress: p.tokenAddress || '',
      symbol: p.tokenSymbol,
      chain: p.chain,
      sector: killSwitchService.inferSector(p.tokenSymbol, p.chain),
      sizeUsd: p.sizeUsd,
      entryPrice: p.entryPrice,
      currentPrice: p.currentPrice,
      unrealizedPnl: p.pnlUsd,
      unrealizedPnlPct: p.pnlPct,
      weight: p.sizeUsd / Math.max(session?.currentCapital ?? 10, 1),
      volatility: 0.6,
      returns: [],
      marketCapTier: 'MID' as const,
      strategyId: p.strategyName || null,
      openedAt: p.openedAt,
    }));

    const customScenarios = scenario
      ? [{
          id: scenario,
          name: scenario,
          description: `Custom scenario: ${scenario}`,
          shocks: new Map<string, number>(),
          marketShock: scenario === 'market_crash_20' ? -0.20
            : scenario === 'crypto_winter_50' ? -0.50
            : scenario === 'flash_crash_10' ? -0.10
            : scenario === 'liquidity_crisis' ? -0.15
            : 0,
          correlationMultiplier: scenario === 'correlation_break' ? 0.2
            : scenario === 'liquidity_crisis' ? 1.8
            : scenario === 'crypto_winter_50' ? 2.0
            : 1.5,
          isPredefined: false,
        }]
      : [];

    const result = portfolioIntelligenceEngine.stressTest(positions, customScenarios);

    // Serialize Maps for JSON response
    const serializedResults = result.scenarioResults.map(sr => ({
      scenarioId: sr.scenarioId,
      scenarioName: sr.scenarioName,
      portfolioImpactUsd: sr.portfolioImpactUsd,
      portfolioImpactPct: sr.portfolioImpactPct,
      positionImpacts: Object.fromEntries(sr.positionImpacts),
      recoveryDaysEstimate: sr.recoveryDaysEstimate,
    }));

    return NextResponse.json({
      data: {
        scenarioResults: serializedResults,
        worstCase: result.worstCase ? {
          scenarioId: result.worstCase.scenarioId,
          scenarioName: result.worstCase.scenarioName,
          portfolioImpactUsd: result.worstCase.portfolioImpactUsd,
          portfolioImpactPct: result.worstCase.portfolioImpactPct,
          positionImpacts: Object.fromEntries(result.worstCase.positionImpacts),
          recoveryDaysEstimate: result.worstCase.recoveryDaysEstimate,
        } : null,
        averageImpactPct: result.averageImpactPct,
        computedAt: result.computedAt.toISOString(),
      },
    });
  } catch (error) {
    console.error('Error running stress test:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to run stress test' },
      { status: 500 }
    );
  }
}
