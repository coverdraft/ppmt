import { NextRequest, NextResponse } from 'next/server';
import { decisionEngine } from '@/lib/services/strategy/token-decision-engine';
import { db } from '@/lib/db';

export const dynamic = 'force-dynamic';

/**
 * GET /api/decisions
 * 
 * Query parameters:
 *   - action: "recent" | "track-record" | "analyze" (default: "recent")
 *   - tokenAddress: token address (required for "analyze")
 *   - chain: chain (default: "SOL")
 *   - days: days for track record (default: 30)
 *   - limit: number of results (default: 20)
 */
export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const action = searchParams.get('action') || 'recent';
    const tokenAddress = searchParams.get('tokenAddress') || '';
    const chain = searchParams.get('chain') || 'SOL';
    const days = parseInt(searchParams.get('days') || '30');
    const limit = parseInt(searchParams.get('limit') || '20');

    switch (action) {
      case 'analyze': {
        if (!tokenAddress) {
          return NextResponse.json({ error: 'tokenAddress required for analyze action' }, { status: 400 });
        }
        const result = await decisionEngine.decide(tokenAddress, chain);
        return NextResponse.json({ decision: result });
      }

      case 'track-record': {
        const trackRecord = await decisionEngine.getTrackRecord(days);
        return NextResponse.json({ trackRecord });
      }

      case 'recent':
      default: {
        const decisions = await decisionEngine.getRecentDecisions(limit);
        return NextResponse.json({ decisions });
      }
    }
  } catch (error) {
    console.error('[/api/decisions] GET error:', error);
    return NextResponse.json({ error: 'Failed to fetch decisions' }, { status: 500 });
  }
}

/**
 * POST /api/decisions
 * 
 * Provide feedback on a past decision (close the learning loop).
 * 
 * Body:
 *   - decisionId: string (required)
 *   - wasActedUpon: boolean (required)
 *   - outcome: "PROFIT" | "LOSS" | "BREAKEVEN" | "MISSED" | "AVOIDED_LOSS" (required)
 *   - realizedPnlPct?: number
 *   - realizedPnlUsd?: number
 *   - holdTimeMin?: number
 *   - maxFavorable?: number
 *   - maxAdverse?: number
 *   - notes?: string
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { decisionId, wasActedUpon, outcome, realizedPnlPct, realizedPnlUsd, holdTimeMin, maxFavorable, maxAdverse, notes } = body;

    if (!decisionId || wasActedUpon === undefined || !outcome) {
      return NextResponse.json({ error: 'decisionId, wasActedUpon, and outcome are required' }, { status: 400 });
    }

    const validOutcomes = ['PROFIT', 'LOSS', 'BREAKEVEN', 'MISSED', 'AVOIDED_LOSS'];
    if (!validOutcomes.includes(outcome)) {
      return NextResponse.json({ error: `outcome must be one of: ${validOutcomes.join(', ')}` }, { status: 400 });
    }

    await decisionEngine.provideFeedback(decisionId, {
      wasActedUpon,
      outcome,
      realizedPnlPct,
      realizedPnlUsd,
      holdTimeMin,
      maxFavorable,
      maxAdverse,
      notes,
    });

    return NextResponse.json({ success: true, message: 'Feedback recorded' });
  } catch (error) {
    console.error('[/api/decisions] POST error:', error);
    return NextResponse.json({ error: 'Failed to record feedback' }, { status: 500 });
  }
}
