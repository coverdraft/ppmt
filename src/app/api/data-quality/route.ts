import { NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { dataQualityGate } from '@/lib/services/risk/data-quality-gate';

export const dynamic = 'force-dynamic';

/**
 * GET /api/data-quality
 *
 * Returns data quality metrics in the DataQualityMetrics format
 * expected by the frontend DataQualityGate component.
 * Accepts an optional `?chain=ETH` query parameter to filter by chain.
 */
export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const chain = searchParams.get('chain') ?? undefined;
    const candleWhere = chain ? { chain } : undefined;

    // ── 1. Get base report from DataQualityGate service ──────────────
    const report = await dataQualityGate.assessQuality(chain);

    // ── 2. Run additional queries the frontend needs ─────────────────

    // Candles grouped by source (coingecko, generated, dexscreener, etc.)
    const sourceGroups = await db.priceCandle.groupBy({
      by: ['source'],
      where: candleWhere,
      _count: { id: true },
    });
    const candlesBySource = sourceGroups.map((g) => ({
      source: g.source,
      count: g._count.id,
    }));

    // Candles grouped by timeframe
    const tfGroups = await db.priceCandle.groupBy({
      by: ['timeframe'],
      where: candleWhere,
      _count: { id: true },
    });
    const candlesByTimeframe = tfGroups.map((g) => ({
      timeframe: g.timeframe,
      count: g._count.id,
    }));

    // Volume stats
    const zeroVolumeCandles = await db.priceCandle.count({
      where: { volume: 0, ...(chain ? { chain } : {}) },
    });
    const candlesWithVolume = await db.priceCandle.count({
      where: { volume: { gt: 0 }, ...(chain ? { chain } : {}) },
    });

    // DNA records
    let dnaRecords = 0;
    try {
      dnaRecords = await db.tokenDNA.count();
    } catch {
      // TokenDNA table may not exist if migrations haven't run
      dnaRecords = 0;
    }

    // Tokens with enough candles for backtesting (>= 100 candles)
    const candleTokenCounts = await db.priceCandle.groupBy({
      by: ['tokenAddress'],
      where: candleWhere,
      _count: { id: true },
    });
    const tokensWithEnoughCandles = candleTokenCounts.filter(
      (g) => g._count.id >= 100,
    ).length;

    // ── 3. Compute coverage percentages ──────────────────────────────
    const totalTokens = report.tokenCount || 1; // avoid div by zero
    const totalCandles = report.totalCandles || 1;

    const candleCoverage = Math.round(
      (report.tokensWithCandles / totalTokens) * 100,
    );
    const backtestReadyCoverage = Math.round(
      (tokensWithEnoughCandles / totalTokens) * 100,
    );
    const volumeCoverage = Math.round(
      (candlesWithVolume / totalCandles) * 100,
    );
    const dnaCoverage = Math.round((dnaRecords / totalTokens) * 100);

    // ── 4. Map quality level ─────────────────────────────────────────
    // Backend: EXCELLENT | GOOD | MARGINAL | POOR | CRITICAL
    // Frontend: excellent | good | fair | poor | critical
    const levelMap: Record<string, string> = {
      EXCELLENT: 'excellent',
      GOOD: 'good',
      MARGINAL: 'fair',
      POOR: 'poor',
      CRITICAL: 'critical',
    };
    const qualityLevel = levelMap[report.level] ?? 'critical';

    // ── 5. Build recommendations ─────────────────────────────────────
    const recommendations: string[] = [...report.recommendations];

    if (volumeCoverage < 50) {
      recommendations.push(
        `${volumeCoverage}% of candles have volume data — backfill with real OHLCV sources for better backtesting`,
      );
    }

    if (dnaRecords === 0) {
      recommendations.push(
        'No token DNA profiles — run the brain pipeline to generate DNA analysis',
      );
    }

    // ── 6. Assemble DataQualityMetrics ───────────────────────────────
    const metrics = {
      totalTokens: report.tokenCount,
      tokensWithCandles: report.tokensWithCandles,
      tokensWithEnoughCandles,
      totalCandles: report.totalCandles,
      candlesByTimeframe,
      candlesBySource,
      zeroVolumeCandles,
      candlesWithVolume,
      oldestCandle: report.oldestCandle
        ? report.oldestCandle.toISOString()
        : null,
      newestCandle: report.newestCandle
        ? report.newestCandle.toISOString()
        : null,
      dnaRecords,
      coverage: {
        candleCoverage,
        backtestReadyCoverage,
        volumeCoverage,
        dnaCoverage,
      },
      qualityScore: report.overallScore,
      qualityLevel,
      recommendations,
    };

    return NextResponse.json({
      success: true,
      data: metrics,
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    console.error('[/api/data-quality] Error:', error);
    return NextResponse.json(
      { success: false, error: message },
      { status: 500 },
    );
  }
}
