import { NextRequest, NextResponse } from 'next/server';
import { portfolioIntelligenceEngine } from '@/lib/services/portfolio/portfolio-intelligence-engine';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';
import type { OptimizationMethod } from '@/lib/services/portfolio/types';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const VALID_METHODS: OptimizationMethod[] = ['MEAN_VARIANCE', 'RISK_PARITY', 'MIN_VARIANCE', 'MAX_DIVERSIFICATION'];

/**
 * POST /api/portfolio/optimize
 * Optimize portfolio weights.
 * Body: { method: 'mean_variance' | 'risk_parity' | 'min_variance' | 'max_diversification' }
 * Response: { weights: {tokenAddress: weight}, expectedReturn, expectedVol, sharpeRatio }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { method } = body;

    const normalizedMethod = typeof method === 'string'
      ? method.toUpperCase() as OptimizationMethod
      : null;

    if (!normalizedMethod || !VALID_METHODS.includes(normalizedMethod)) {
      return NextResponse.json(
        { data: null, error: `Invalid method. Must be one of: ${VALID_METHODS.join(', ')}` },
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
          weights: {},
          expectedReturn: 0,
          expectedVol: 0,
          sharpeRatio: 0,
          method: normalizedMethod,
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

    const result = portfolioIntelligenceEngine.optimizeWeights(positions, normalizedMethod);

    return NextResponse.json({
      data: {
        weights: Object.fromEntries(result.weights),
        expectedReturn: result.expectedReturn,
        expectedVol: result.expectedVol,
        sharpeRatio: result.sharpeRatio,
        method: result.method,
      },
    });
  } catch (error) {
    console.error('Error optimizing portfolio weights:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to optimize portfolio weights' },
      { status: 500 }
    );
  }
}
