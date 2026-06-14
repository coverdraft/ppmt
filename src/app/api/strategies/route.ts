import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const dynamic = 'force-dynamic';

// DEMO strategies to seed when DB is empty
const DEMO_STRATEGIES = [
  {
    symbol: 'BTC/USDT',
    timeframe: '1h',
    assetClass: 'blue_chip',
    status: 'live',
    totalPnl: 2450,
    totalPnlPct: 24.5,
    winRate: 0.58,
    sharpeRatio: 1.82,
    maxDrawdown: 0.08,
    profitFactor: 2.14,
    totalTrades: 142,
    capitalAllocated: 10000,
    patternCount: 1847,
    trieLevel: 'n3',
  },
  {
    symbol: 'ETH/USDT',
    timeframe: '1h',
    assetClass: 'blue_chip',
    status: 'paper_trading',
    totalPnl: 1230,
    totalPnlPct: 12.3,
    winRate: 0.52,
    sharpeRatio: 1.24,
    maxDrawdown: 0.12,
    profitFactor: 1.67,
    totalTrades: 89,
    capitalAllocated: 5000,
    patternCount: 923,
    trieLevel: 'n3',
  },
  {
    symbol: 'SOL/USDT',
    timeframe: '5m',
    assetClass: 'large_cap',
    status: 'forward_testing',
    totalPnl: -320,
    totalPnlPct: -3.2,
    winRate: 0.47,
    sharpeRatio: 0.65,
    maxDrawdown: 0.18,
    profitFactor: 0.88,
    totalTrades: 56,
    capitalAllocated: 3000,
    patternCount: 412,
    trieLevel: 'n4',
  },
  {
    symbol: 'DOGE/USDT',
    timeframe: '5m',
    assetClass: 'meme',
    status: 'backtesting',
    totalPnl: 0,
    totalPnlPct: 0,
    winRate: 0,
    sharpeRatio: 0,
    maxDrawdown: 0,
    profitFactor: 0,
    totalTrades: 0,
    capitalAllocated: 0,
    patternCount: 0,
    trieLevel: 'n3',
  },
  {
    symbol: 'LINK/USDT',
    timeframe: '1m',
    assetClass: 'defi',
    status: 'draft',
    totalPnl: 0,
    totalPnlPct: 0,
    winRate: 0,
    sharpeRatio: 0,
    maxDrawdown: 0,
    profitFactor: 0,
    totalTrades: 0,
    capitalAllocated: 0,
    patternCount: 0,
    trieLevel: 'n3',
  },
];

async function seedDemoIfEmpty() {
  const count = await db.pPMTStrategy.count();
  if (count === 0) {
    for (const demo of DEMO_STRATEGIES) {
      await db.pPMTStrategy.create({ data: demo });
    }
  }
}

// GET /api/strategies - List all strategies
export async function GET() {
  try {
    await seedDemoIfEmpty();
    const strategies = await db.pPMTStrategy.findMany({
      orderBy: { createdAt: 'desc' },
      include: {
        runs: {
          orderBy: { startedAt: 'desc' },
          take: 5,
        },
      },
    });
    return NextResponse.json({ success: true, data: strategies });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to fetch strategies';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}

// POST /api/strategies - Create a new strategy
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    const strategy = await db.pPMTStrategy.create({
      data: {
        symbol: body.symbol,
        timeframe: body.timeframe,
        assetClass: body.assetClass || 'large_cap',
        status: body.status || 'draft',
        saxAlpha: body.saxAlpha ?? 3,
        saxWindow: body.saxWindow ?? 7,
        catastrophicLossPct: body.catastrophicLossPct ?? 8.0,
        fuzzyThreshold: body.fuzzyThreshold ?? 0.8,
        initialCapital: body.initialCapital ?? 10000,
        patternLength: body.patternLength ?? 5,
        minConfidence: body.minConfidence ?? 0.2,
        livingTrie: body.livingTrie ?? true,
        regimeAware: body.regimeAware ?? true,
        pruningInterval: body.pruningInterval ?? 1000,
        recalibrationInterval: body.recalibrationInterval ?? 0,
        trieLevel: body.trieLevel ?? 'n3',
        capitalAllocated: body.capitalAllocated ?? 0,
      },
    });

    return NextResponse.json({ success: true, data: strategy }, { status: 201 });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to create strategy';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}
