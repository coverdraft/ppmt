/**
 * POST /api/backfill
 *
 * Alias for /api/ohlcv/backfill — triggers OHLCV data backfill.
 * This route exists because the DataQualityGate component calls /api/backfill.
 */

import { NextRequest, NextResponse } from 'next/server';
import { ohlcvPipeline } from '@/lib/services/data-sources/ohlcv-pipeline';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface BackfillBody {
  limit?: number;
  timeframes?: string[];
  days?: number;
}

export async function POST(request: NextRequest) {
  try {
    const body: BackfillBody = await request.json().catch(() => ({}));
    const limit = Math.min(Math.max(body.limit ?? 20, 1), 100);

    // Get tokens that need backfill (sorted by volume)
    const tokensToBackfill = await db.token.findMany({
      where: { volume24h: { gt: 0 } },
      orderBy: { volume24h: 'desc' },
      take: limit,
      select: { address: true, chain: true },
    });

    if (tokensToBackfill.length === 0) {
      // Fallback: any tokens with no candles
      const tokensWithCandles = await db.priceCandle.groupBy({
        by: ['tokenAddress'],
      });
      const addressesWithCandles = new Set(tokensWithCandles.map(t => t.tokenAddress));

      const allTokens = await db.token.findMany({
        take: limit,
        select: { address: true, chain: true },
      });

      const tokensWithoutCandles = allTokens.filter(
        t => !addressesWithCandles.has(t.address),
      );

      if (tokensWithoutCandles.length === 0) {
        return NextResponse.json({
          status: 'COMPLETED',
          message: 'All tokens already have candle data',
          totalTokens: 0,
          totalCandlesStored: 0,
        });
      }
    }

    // Use the pipeline's backfill method
    const result = await ohlcvPipeline.backfillTopTokens(limit);

    // Convert Map to array for serialization
    const results: Array<{
      tokenAddress: string;
      chain: string;
      totalStored: number;
      duration: number;
    }> = [];

    for (const [, tokenResult] of result.results) {
      results.push({
        tokenAddress: tokenResult.tokenAddress,
        chain: tokenResult.chain,
        totalStored: tokenResult.totalStored,
        duration: tokenResult.duration,
      });
    }

    return NextResponse.json({
      status: 'COMPLETED',
      message: `Backfilled ${result.totalTokens} tokens with ${result.totalCandlesStored} candles in ${(result.duration / 1000).toFixed(1)}s`,
      totalTokens: result.totalTokens,
      totalCandlesStored: result.totalCandlesStored,
      failedTokens: result.failedTokens,
      durationMs: result.duration,
      results: results.slice(0, 50),
    });
  } catch (error) {
    console.error('[/api/backfill] POST error:', error);
    return NextResponse.json(
      {
        status: 'FAILED',
        error: error instanceof Error ? error.message : String(error),
        message: 'Backfill failed. CoinGecko rate limits may apply — try again in a few minutes.',
      },
      { status: 500 },
    );
  }
}
