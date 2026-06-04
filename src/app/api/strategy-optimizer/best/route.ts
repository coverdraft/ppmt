import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// Best Strategies - persisted to Prisma DB (AIBestStrategy model)
// ============================================================

interface BestStrategyInput {
  strategyName: string;
  category?: string;
  timeframe?: string;
  tokenAgeCategory?: string;
  riskTolerance?: string;
  capitalAllocation?: number;
  pnlPct?: number;
  pnlUsd?: number;
  sharpeRatio?: number;
  winRate?: number;
  maxDrawdownPct?: number;
  profitFactor?: number;
  totalTrades?: number;
  avgHoldTimeMin?: number;
  score?: number;
  backtestId?: string;
}

/**
 * GET /api/strategy-optimizer/best
 * Returns saved best strategies ordered by score descending
 */
export async function GET() {
  try {
    const strategies = await db.aIBestStrategy.findMany({
      orderBy: { score: 'desc' },
    });

    return NextResponse.json({
      data: strategies,
    });
  } catch (error) {
    console.error('Error fetching best strategies:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to fetch best strategies' },
      { status: 500 },
    );
  }
}

/**
 * POST /api/strategy-optimizer/best
 * Save a strategy as "best" (upsert by backtestId)
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const strategy = body.strategy as BestStrategyInput;

    if (!strategy || !strategy.strategyName) {
      return NextResponse.json(
        { data: null, error: 'Strategy data is required' },
        { status: 400 },
      );
    }

    const backtestId = strategy.backtestId || `no-backtest-${Date.now()}`;

    const entry = await db.aIBestStrategy.upsert({
      where: { backtestId },
      update: {
        strategyName: strategy.strategyName,
        category: strategy.category || 'UNKNOWN',
        timeframe: strategy.timeframe || '1h',
        tokenAgeCategory: strategy.tokenAgeCategory || 'UNKNOWN',
        riskTolerance: strategy.riskTolerance || 'MODERATE',
        capitalAllocation: strategy.capitalAllocation || 0,
        pnlPct: strategy.pnlPct || 0,
        pnlUsd: strategy.pnlUsd || 0,
        sharpeRatio: strategy.sharpeRatio || 0,
        winRate: strategy.winRate || 0,
        maxDrawdownPct: strategy.maxDrawdownPct || 0,
        profitFactor: strategy.profitFactor || 0,
        totalTrades: strategy.totalTrades || 0,
        avgHoldTimeMin: strategy.avgHoldTimeMin || 0,
        score: strategy.score || 0,
      },
      create: {
        strategyName: strategy.strategyName,
        category: strategy.category || 'UNKNOWN',
        timeframe: strategy.timeframe || '1h',
        tokenAgeCategory: strategy.tokenAgeCategory || 'UNKNOWN',
        riskTolerance: strategy.riskTolerance || 'MODERATE',
        capitalAllocation: strategy.capitalAllocation || 0,
        pnlPct: strategy.pnlPct || 0,
        pnlUsd: strategy.pnlUsd || 0,
        sharpeRatio: strategy.sharpeRatio || 0,
        winRate: strategy.winRate || 0,
        maxDrawdownPct: strategy.maxDrawdownPct || 0,
        profitFactor: strategy.profitFactor || 0,
        totalTrades: strategy.totalTrades || 0,
        avgHoldTimeMin: strategy.avgHoldTimeMin || 0,
        score: strategy.score || 0,
        backtestId,
      },
    });

    return NextResponse.json({
      data: entry,
      message: 'Strategy saved to Hall of Fame',
    });
  } catch (error) {
    console.error('Error saving best strategy:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to save best strategy' },
      { status: 500 },
    );
  }
}

/**
 * DELETE /api/strategy-optimizer/best
 * Remove a strategy from the best list
 */
export async function DELETE(request: NextRequest) {
  try {
    const body = await request.json();
    const { id } = body as { id?: string };
    const { backtestId } = body as { backtestId?: string };

    if (!id && !backtestId) {
      return NextResponse.json(
        { data: null, error: 'Strategy id or backtestId is required' },
        { status: 400 },
      );
    }

    let removed = 0;

    if (id) {
      try {
        await db.aIBestStrategy.delete({ where: { id } });
        removed = 1;
      } catch {
        // Record not found
        removed = 0;
      }
    } else if (backtestId) {
      try {
        await db.aIBestStrategy.delete({ where: { backtestId } });
        removed = 1;
      } catch {
        // Record not found
        removed = 0;
      }
    }

    return NextResponse.json({
      data: { removed },
      message: removed > 0 ? 'Strategy removed from Hall of Fame' : 'Strategy not found',
    });
  } catch (error) {
    console.error('Error deleting best strategy:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to delete best strategy' },
      { status: 500 },
    );
  }
}
