import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

/**
 * GET /api/brain/growth
 *
 * Returns CompoundGrowthTracker time series data for the Capital Evolution chart.
 * Query params:
 *   limit – number of records to return (default 50)
 */
export async function GET(request: NextRequest) {
  try {
    const limit = parseInt(request.nextUrl.searchParams.get('limit') || '50', 10);

    const growthData = await db.compoundGrowthTracker.findMany({
      orderBy: { measuredAt: 'asc' },
      take: limit,
      select: {
        id: true,
        capitalUsd: true,
        initialCapitalUsd: true,
        totalReturnPct: true,
        totalPnlUsd: true,
        periodPnlUsd: true,
        periodReturnPct: true,
        totalFeesPaidUsd: true,
        totalSlippageUsd: true,
        feeAdjustedPnlUsd: true,
        feeAdjustedReturnPct: true,
        dailyCompoundRate: true,
        projectedAnnualReturn: true,
        winRate: true,
        period: true,
        measuredAt: true,
      },
    });

    // Also get recent brain cycle runs for activity feed
    const recentCycles = await db.brainCycleRun.findMany({
      orderBy: { createdAt: 'desc' },
      take: 20,
      select: {
        id: true,
        cycleNumber: true,
        status: true,
        tokensScanned: true,
        tokensOperable: true,
        tokensTradeable: true,
        capitalBeforeCycle: true,
        capitalAfterCycle: true,
        cyclePnlUsd: true,
        cumulativeReturnPct: true,
        dominantRegime: true,
        createdAt: true,
        completedAt: true,
        cycleDurationMs: true,
        errorLog: true,
      },
    });

    // Get recent operability snapshots for distribution
    const latestSnapshots = await db.operabilitySnapshot.findMany({
      orderBy: { createdAt: 'desc' },
      take: 100,
      select: {
        operabilityLevel: true,
        isOperable: true,
        createdAt: true,
      },
    });

    // Count operability distribution from latest snapshots
    const operabilityDist: Record<string, number> = { PREMIUM: 0, GOOD: 0, MARGINAL: 0, RISKY: 0, UNOPERABLE: 0 };
    const seen = new Set<string>();
    for (const snap of latestSnapshots) {
      // Only count unique token addresses from latest batch
      if (!seen.has(snap.operabilityLevel)) {
        operabilityDist[snap.operabilityLevel] = (operabilityDist[snap.operabilityLevel] || 0) + 1;
      }
    }
    // Count all from latest batch properly
    const distCounts: Record<string, number> = { PREMIUM: 0, GOOD: 0, MARGINAL: 0, RISKY: 0, UNOPERABLE: 0 };
    for (const snap of latestSnapshots) {
      if (distCounts[snap.operabilityLevel] !== undefined) {
        distCounts[snap.operabilityLevel]++;
      }
    }

    return NextResponse.json({
      success: true,
      data: {
        growthHistory: growthData,
        recentCycles,
        operabilityDistribution: distCounts,
      },
    });
  } catch (error: any) {
    console.error('[/api/brain/growth] Error:', error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
