import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const pattern = await db.patternRule.findUnique({ where: { id } });

    if (!pattern) {
      return NextResponse.json({ error: 'Pattern not found' }, { status: 404 });
    }

    // Simulate backtest results
    const winRate = Math.random() * 0.6 + 0.3;
    const occurrences = Math.floor(Math.random() * 300 + 50);
    const avgReturn = (Math.random() - 0.3) * 40;
    const maxDrawdown = -(Math.random() * 30 + 5);
    const sharpeRatio = (Math.random() - 0.2) * 3;

    const backtestResults = {
      winRate,
      occurrences,
      avgReturn,
      maxDrawdown,
      sharpeRatio,
      distribution: Array.from({ length: 20 }, () =>
        Math.floor((Math.random() - 0.3) * 50)
      ),
      runDate: new Date().toISOString(),
    };

    const updated = await db.patternRule.update({
      where: { id },
      data: {
        backtestResults: JSON.stringify(backtestResults),
        winRate,
        occurrences,
      },
    });

    return NextResponse.json({ pattern: updated, backtestResults });
  } catch (error) {
    console.error('Error running backtest:', error);
    return NextResponse.json({ error: 'Failed to run backtest' }, { status: 500 });
  }
}
