import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/dashboard/stats
 * Computes aggregate dashboard statistics from real DB data.
 * Includes all signal types: Smart Money, Rug Pull, V-Shape, Liquidity Trap, Patterns
 */
export async function GET() {
  try {
    const { db } = await import('@/lib/db');

    const oneHourAgo = new Date(Date.now() - 3600000);
    const oneDayAgo = new Date(Date.now() - 86400000);

    let totalTokens = 0;
    let activeSignals = 0;
    let smartMoneyWallets = 0;
    let totalPatterns = 0;
    let recentEvents = 0;
    let predictiveSignals = 0;
    let tokensWithLiquidity = 0;
    let tokensWithDna: Array<{ dna: { riskScore: number } | null }> = [];

    try { totalTokens = await db.token.count(); } catch {}
    try { activeSignals = await db.signal.count({ where: { createdAt: { gte: oneHourAgo } } }); } catch {}
    try { smartMoneyWallets = await db.trader.count({ where: { isSmartMoney: true } }); } catch {}
    try { totalPatterns = await db.patternRule.count({ where: { isActive: true } }); } catch {}
    try { recentEvents = await db.userEvent.count({ where: { createdAt: { gte: oneDayAgo } } }); } catch {}
    try {
      predictiveSignals = await db.predictiveSignal.count({
        where: { OR: [{ validUntil: null }, { validUntil: { gte: new Date() } }] },
      });
    } catch {}
    try { tokensWithLiquidity = await db.token.count({ where: { liquidity: { gt: 0 } } }); } catch {}
    try {
      tokensWithDna = await db.token.findMany({
        where: { dna: { isNot: null } },
        select: { dna: { select: { riskScore: true } } },
      });
    } catch {}

    const dangerTokens = tokensWithDna.filter(t => t.dna && t.dna.riskScore > 60).length;
    const safeTokens = tokensWithDna.filter(t => t.dna && t.dna.riskScore <= 30).length;

    // Signal type breakdown - check multiple possible type values
    let rugPullSignals = 0;
    let smartMoneySignals = 0;
    let vShapeSignals = 0;
    let liquidityTrapSignals = 0;
    let patternSignals = 0;

    try {
      rugPullSignals = await db.signal.count({
        where: {
          type: { in: ['RUG_PULL', 'SMART_MONEY_ENTRY'] },
          createdAt: { gte: oneHourAgo },
        },
      });
      // RUG_PULL might match both, so separate queries
      rugPullSignals = await db.signal.count({
        where: { type: 'RUG_PULL', createdAt: { gte: oneHourAgo } },
      });
    } catch {}

    try {
      smartMoneySignals = await db.signal.count({
        where: { type: { in: ['SMART_MONEY', 'SMART_MONEY_ENTRY'] }, createdAt: { gte: oneHourAgo } },
      });
    } catch {}

    try {
      vShapeSignals = await db.signal.count({
        where: { type: 'V_SHAPE', createdAt: { gte: oneHourAgo } },
      });
    } catch {}

    try {
      liquidityTrapSignals = await db.signal.count({
        where: { type: 'LIQUIDITY_TRAP', createdAt: { gte: oneHourAgo } },
      });
    } catch {}

    try {
      patternSignals = await db.signal.count({
        where: { type: 'PATTERN', createdAt: { gte: oneHourAgo } },
      });
    } catch {}

    // FOMO Index from real data
    const eventScore = Math.min(30, Math.floor((recentEvents / Math.max(totalTokens, 1)) * 100));
    const signalScore = Math.min(25, Math.floor((activeSignals / Math.max(totalTokens, 1)) * 80));
    const predictiveScore = Math.min(20, Math.floor((predictiveSignals / Math.max(totalTokens, 1)) * 60));
    const smartMoneyScore = Math.min(15, Math.floor((smartMoneyWallets / Math.max(totalTokens, 1)) * 50));
    const dangerScore = Math.min(10, Math.floor((dangerTokens / Math.max(tokensWithDna.length, 1)) * 30));

    const fomoIndex = Math.min(100, eventScore + signalScore + predictiveScore + smartMoneyScore + dangerScore);

    return NextResponse.json({
      totalTokens,
      activeSignals,
      smartMoneyWallets,
      totalPatterns,
      recentEvents,
      dangerTokens,
      safeTokens,
      rugPullSignals,
      smartMoneySignals,
      vShapeSignals,
      liquidityTrapSignals,
      patternSignals,
      predictiveSignals,
      tokensWithLiquidity,
      fomoIndex,
      threatLevel: rugPullSignals > 5 ? 'HIGH' : rugPullSignals > 2 ? 'MEDIUM' : 'LOW',
    });
  } catch (error) {
    console.error('Error fetching dashboard stats:', error);
    return NextResponse.json({ error: 'Failed to fetch stats' }, { status: 500 });
  }
}
