import { NextRequest, NextResponse } from 'next/server';
import {
  strategyDecisionEngine,
  type SDEInput,
  type RiskProfile,
} from '@/lib/services/strategy/strategy-decision-engine';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/strategy-decision/validate
 *
 * Validate a strategy through the SDE pipeline and get a decision.
 *
 * Body: SDEInput (full input with backtest, MC, WF, operability snapshots)
 * Query: ?strategyId=xxx  (alternative: build input from DB)
 * Query: ?riskProfile=MODERATE  (optional)
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    // Option 1: Full SDEInput provided directly
    if (body.backtest && body.monteCarlo && body.walkForward && body.operability && body.portfolioState) {
      const input: SDEInput = {
        strategyId: body.strategyId || 'unknown',
        strategyName: body.strategyName || 'Unknown Strategy',
        backtest: body.backtest,
        monteCarlo: body.monteCarlo,
        walkForward: body.walkForward,
        operability: body.operability,
        paperTrading: body.paperTrading,
        portfolioState: body.portfolioState,
        riskProfile: (['CONSERVATIVE', 'MODERATE', 'AGGRESSIVE'].includes(body.riskProfile) ? body.riskProfile : 'MODERATE') as RiskProfile,
        dataQuality: body.dataQuality, // Preserve data quality flag — prevents placeholder bypass
      };

      const decision = await strategyDecisionEngine.validate(input);
      return NextResponse.json({ data: decision });
    }

    // Option 2: strategyId provided — build input from DB
    const strategyId = body.strategyId || new URL(request.url).searchParams.get('strategyId');
    if (!strategyId) {
      return NextResponse.json(
        { data: null, error: 'Provide either full SDEInput or strategyId' },
        { status: 400 },
      );
    }

    const portfolioState = body.portfolioState || {
      totalCapitalUsd: 100,
      currentDrawdownPct: 0,
      activeStrategies: 1,
      marketVolatility: 50,
      marketRegime: 'SIDEWAYS',
    };

    const riskProfile = (body.riskProfile as RiskProfile) || 'MODERATE';

    const input = await strategyDecisionEngine.buildInputFromStrategyId(
      strategyId,
      portfolioState,
      riskProfile,
    );

    if (!input) {
      return NextResponse.json(
        { data: null, error: `Strategy ${strategyId} not found` },
        { status: 404 },
      );
    }

    const decision = await strategyDecisionEngine.validate(input);
    return NextResponse.json({ data: decision });
  } catch (error) {
    console.error('[SDE Validate API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Validation failed' },
      { status: 500 },
    );
  }
}

/**
 * GET /api/strategy-decision/validate?strategyId=xxx
 * Quick validate using DB data.
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const strategyId = searchParams.get('strategyId');

    if (!strategyId) {
      return NextResponse.json(
        { data: null, error: 'strategyId query parameter required' },
        { status: 400 },
      );
    }

    const riskProfileRaw = searchParams.get('riskProfile') || 'MODERATE';
    const riskProfile = (['CONSERVATIVE', 'MODERATE', 'AGGRESSIVE'].includes(riskProfileRaw) ? riskProfileRaw : 'MODERATE') as RiskProfile;

    const portfolioState = {
      totalCapitalUsd: parseFloat(searchParams.get('capital') || '100'),
      currentDrawdownPct: parseFloat(searchParams.get('drawdown') || '0'),
      activeStrategies: parseInt(searchParams.get('activeStrategies') || '1', 10),
      marketVolatility: parseFloat(searchParams.get('volatility') || '50'),
      marketRegime: searchParams.get('regime') || 'SIDEWAYS',
    };

    const input = await strategyDecisionEngine.buildInputFromStrategyId(
      strategyId,
      portfolioState,
      riskProfile,
    );

    if (!input) {
      return NextResponse.json(
        { data: null, error: `Strategy ${strategyId} not found` },
        { status: 404 },
      );
    }

    const decision = await strategyDecisionEngine.validate(input);
    return NextResponse.json({ data: decision });
  } catch (error) {
    console.error('[SDE Validate API] Error:', error);
    return NextResponse.json(
      { data: null, error: error instanceof Error ? error.message : 'Validation failed' },
      { status: 500 },
    );
  }
}
