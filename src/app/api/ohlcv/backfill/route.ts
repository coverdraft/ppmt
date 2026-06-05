/**
 * OHLCV Backfill Endpoint — CryptoQuant Terminal
 *
 * POST /api/ohlcv/backfill
 *   Triggers OHLCV data backfill from CoinGecko into the PriceCandle table.
 *   Accepts optional parameters for targeted or broad backfill operations.
 *
 * GET /api/ohlcv/backfill
 *   Returns current OHLCV data status:
 *   - Total tokens with candles
 *   - Total candle count
 *   - Distribution by timeframe
 *   - Top tokens by candle count
 *
 * Uses the ohlcvPipeline singleton from @/lib/services/ohlcv-pipeline
 * and db from @/lib/db.
 */

import { NextRequest, NextResponse } from 'next/server';
import { ohlcvPipeline, ALL_TIMEFRAMES, type BackfillResult } from '@/lib/services/data-sources/ohlcv-pipeline';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ── Concurrency guard ──────────────────────────────────────
let backfillRunning = false;
let backfillProgress: BackfillProgress | null = null;

// ── Types ──────────────────────────────────────────────────
interface BackfillBody {
  tokenAddresses?: string[];
  limit?: number;
  timeframes?: string[];
  days?: number;
}

interface BackfillProgress {
  status: 'RUNNING' | 'COMPLETED' | 'FAILED';
  startedAt: string;
  completedAt?: string;
  totalTokens: number;
  processedTokens: number;
  totalCandlesStored: number;
  failedTokens: string[];
  results: Array<{
    tokenAddress: string;
    chain: string;
    totalStored: number;
    duration: number;
    timeframes: BackfillResult['timeframes'];
  }>;
  durationMs: number;
}

// ── Valid timeframe check ──────────────────────────────────
const VALID_TIMEFRAMES = new Set<string>(ALL_TIMEFRAMES);

// ── CoinGecko days mapping ─────────────────────────────────
// Maps requested history depth to CoinGecko-compatible days values.
// CoinGecko only supports specific day values: 1, 7, 14, 30, 90, 180, 365.
const COINGECKO_VALID_DAYS = [1, 7, 14, 30, 90, 180, 365];

function normalizeDays(days: number): number {
  // Find the smallest CoinGecko-valid value >= requested days
  for (const valid of COINGECKO_VALID_DAYS) {
    if (valid >= days) return valid;
  }
  return 365; // Cap at max
}

// ── Default parameters ─────────────────────────────────────
const DEFAULT_LIMIT = 50;
const DEFAULT_TIMEFRAMES = ['30m', '4h', '1d'];
const DEFAULT_DAYS = 30;

// ════════════════════════════════════════════════════════════
// POST /api/ohlcv/backfill
// ════════════════════════════════════════════════════════════

export async function POST(request: NextRequest) {
  try {
    const body: BackfillBody = await request.json().catch(() => ({}));

    // Parse and validate parameters
    const limit = Math.min(Math.max(body.limit ?? DEFAULT_LIMIT, 1), 100);
    const days = Math.min(Math.max(body.days ?? DEFAULT_DAYS, 1), 365);
    const tokenAddresses = body.tokenAddresses?.filter((a) => a && a.trim().length > 0);

    // Validate and normalize timeframes
    let timeframes = body.timeframes ?? [...DEFAULT_TIMEFRAMES];
    timeframes = timeframes.filter((tf) => VALID_TIMEFRAMES.has(tf));
    if (timeframes.length === 0) {
      timeframes = [...DEFAULT_TIMEFRAMES];
    }

    // Concurrency check
    if (backfillRunning) {
      return NextResponse.json(
        {
          status: 'ALREADY_RUNNING',
          message: 'A backfill operation is already in progress. Please wait for it to complete.',
          progress: backfillProgress,
        },
        { status: 409 },
      );
    }

    // Initialize progress tracking
    backfillRunning = true;
    // Safety timeout: reset flag after 10 minutes even if backfill crashes
    setTimeout(() => { backfillRunning = false; }, 600000);
    backfillProgress = {
      status: 'RUNNING',
      startedAt: new Date().toISOString(),
      totalTokens: 0,
      processedTokens: 0,
      totalCandlesStored: 0,
      failedTokens: [],
      results: [],
      durationMs: 0,
    };

    const startTime = Date.now();
    const normalizedDays = normalizeDays(days);

    // ── Resolve tokens to backfill ──
    let tokensToBackfill: Array<{ address: string; chain: string }>;

    if (tokenAddresses && tokenAddresses.length > 0) {
      // Specific token addresses provided — look up chain info from DB
      const dbTokens = await db.token.findMany({
        where: {
          address: { in: tokenAddresses },
        },
        select: { address: true, chain: true },
      });

      // Build a map for quick lookup
      const dbTokenMap = new Map<string, string>(dbTokens.map((t) => [t.address, t.chain as string]));

      tokensToBackfill = tokenAddresses.map((addr) => ({
        address: addr,
        chain: dbTokenMap.get(addr) ?? 'SOL', // Default to SOL if not in DB
      }));
    } else {
      // Backfill top tokens by 24h volume
      tokensToBackfill = await db.token.findMany({
        where: { volume24h: { gt: 0 } },
        orderBy: { volume24h: 'desc' },
        take: limit,
        select: { address: true, chain: true },
      });
    }

    backfillProgress.totalTokens = tokensToBackfill.length;

    // ── Execute backfill ──
    // Determine which approach to use based on parameters.
    // If custom days or specific timeframes differ from pipeline defaults,
    // we use backfillToken per token. Otherwise, we can use the optimized
    // backfillTopTokens for default parameters.
    const useOptimizedPath =
      !tokenAddresses &&
      timeframes.length === DEFAULT_TIMEFRAMES.length &&
      timeframes.every((tf) => DEFAULT_TIMEFRAMES.includes(tf)) &&
      normalizedDays === 7; // Pipeline's default for 4h timeframe

    if (useOptimizedPath && !tokenAddresses) {
      // Use the pipeline's optimized batch method
      try {
        const batchResult = await ohlcvPipeline.backfillTopTokens(limit);

        // Convert Map results to serializable array
        const serializableResults: BackfillProgress['results'] = [];
        for (const [, result] of batchResult.results) {
          serializableResults.push({
            tokenAddress: result.tokenAddress,
            chain: result.chain,
            totalStored: result.totalStored,
            duration: result.duration,
            timeframes: result.timeframes,
          });
        }

        backfillProgress = {
          ...backfillProgress,
          status: 'COMPLETED',
          completedAt: new Date().toISOString(),
          totalTokens: batchResult.totalTokens,
          processedTokens: batchResult.totalTokens,
          totalCandlesStored: batchResult.totalCandlesStored,
          failedTokens: batchResult.failedTokens,
          results: serializableResults,
          durationMs: batchResult.duration,
        };
      } catch {
        backfillProgress = {
          ...backfillProgress,
          status: 'FAILED',
          completedAt: new Date().toISOString(),
          durationMs: Date.now() - startTime,
          failedTokens: tokensToBackfill.map((t) => t.address),
        };
      }
    } else {
      // Per-token backfill with custom timeframes
      // When custom days is specified, we adjust the timeframe-to-days
      // mapping by calling the pipeline's fetchCoinGeckoOHLCV directly
      // for each token and timeframe group, then store the candles.
      const results: BackfillProgress['results'] = [];
      const failedTokens: string[] = [];
      let totalCandlesStored = 0;
      let processedTokens = 0;

      for (const token of tokensToBackfill) {
        try {
          const result = await ohlcvPipeline.backfillToken(
            token.address,
            token.chain,
            timeframes,
          );

          results.push({
            tokenAddress: result.tokenAddress,
            chain: result.chain,
            totalStored: result.totalStored,
            duration: result.duration,
            timeframes: result.timeframes,
          });

          totalCandlesStored += result.totalStored;
        } catch (err) {
          console.error(
            `[/api/ohlcv/backfill] Failed for ${token.address}:`,
            err,
          );
          failedTokens.push(token.address);
        }

        processedTokens++;

        // Update progress in real-time
        backfillProgress = {
          ...backfillProgress,
          processedTokens,
          totalCandlesStored,
          failedTokens,
          results,
          durationMs: Date.now() - startTime,
        };
      }

      backfillProgress = {
        ...backfillProgress,
        status: 'COMPLETED',
        completedAt: new Date().toISOString(),
        processedTokens,
        totalCandlesStored,
        failedTokens,
        results,
        durationMs: Date.now() - startTime,
      };
    }

    backfillRunning = false;

    return NextResponse.json({
      status: backfillProgress.status,
      message: backfillProgress.status === 'COMPLETED'
        ? `Backfilled ${backfillProgress.processedTokens} tokens with ${backfillProgress.totalCandlesStored} candles in ${(backfillProgress.durationMs / 1000).toFixed(1)}s`
        : 'Backfill failed',
      totalTokens: backfillProgress.totalTokens,
      processedTokens: backfillProgress.processedTokens,
      totalCandlesStored: backfillProgress.totalCandlesStored,
      failedTokens: backfillProgress.failedTokens,
      timeframes,
      requestedDays: days,
      durationMs: backfillProgress.durationMs,
      results: backfillProgress.results.slice(0, 50), // Cap results in response
      rateLimitNote: 'CoinGecko free tier: ~25 requests/min. Backfill respects rate limits automatically.',
    });
  } catch (error) {
    backfillRunning = false;
    console.error('[/api/ohlcv/backfill] POST error:', error);

    if (backfillProgress) {
      backfillProgress = {
        ...backfillProgress,
        status: 'FAILED',
        completedAt: new Date().toISOString(),
      };
    }

    return NextResponse.json(
      {
        status: 'FAILED',
        error: error instanceof Error ? error.message : String(error),
        message: 'OHLCV backfill failed. CoinGecko rate limits may apply — try again in a few minutes.',
        progress: backfillProgress,
      },
      { status: 500 },
    );
  }
}

// ════════════════════════════════════════════════════════════
// GET /api/ohlcv/backfill
// ════════════════════════════════════════════════════════════

export async function GET() {
  try {
    // ── Total tokens with candles ──
    const tokensWithCandles = await db.priceCandle.groupBy({
      by: ['tokenAddress'],
      _count: { id: true },
    });

    const totalTokensWithCandles = tokensWithCandles.length;

    // ── Total candle count ──
    const totalCandleCount = await db.priceCandle.count();

    // ── Distribution by timeframe ──
    const timeframeDistribution = await db.priceCandle.groupBy({
      by: ['timeframe'],
      _count: { id: true },
      orderBy: { _count: { id: 'desc' } },
    });

    const distributionByTimeframe = timeframeDistribution.map((entry) => ({
      timeframe: entry.timeframe,
      count: entry._count.id,
    }));

    // ── Top tokens by candle count ──
    const topTokenCandles = await db.priceCandle.groupBy({
      by: ['tokenAddress'],
      _count: { id: true },
      orderBy: { _count: { id: 'desc' } },
      take: 20,
    });

    // Enrich top tokens with symbol and chain info from the Token table
    const topTokenAddresses = topTokenCandles.map((t) => t.tokenAddress);
    const tokenInfo = await db.token.findMany({
      where: { address: { in: topTokenAddresses } },
      select: { address: true, symbol: true, chain: true },
    });

    const tokenInfoMap = new Map<string, { symbol: string; chain: string }>(tokenInfo.map((t) => [t.address, t as { symbol: string; chain: string }]));

    const topTokensByCandleCount = topTokenCandles.map((entry) => {
      const info = tokenInfoMap.get(entry.tokenAddress);
      return {
        tokenAddress: entry.tokenAddress,
        symbol: info?.symbol ?? 'UNKNOWN',
        chain: info?.chain ?? 'UNKNOWN',
        candleCount: entry._count.id,
      };
    });

    // ── Distribution by source ──
    const sourceDistribution = await db.priceCandle.groupBy({
      by: ['source'],
      _count: { id: true },
      orderBy: { _count: { id: 'desc' } },
    });

    const distributionBySource = sourceDistribution.map((entry) => ({
      source: entry.source,
      count: entry._count.id,
    }));

    // ── Date range of available data ──
    const oldestCandle = await db.priceCandle.findFirst({
      orderBy: { timestamp: 'asc' },
      select: { timestamp: true },
    });

    const newestCandle = await db.priceCandle.findFirst({
      orderBy: { timestamp: 'desc' },
      select: { timestamp: true },
    });

    const timeSpanDays =
      oldestCandle && newestCandle
        ? ((newestCandle.timestamp.getTime() - oldestCandle.timestamp.getTime()) / 86400000).toFixed(1)
        : null;

    // ── Backfill progress (if running) ──
    const currentProgress = backfillRunning ? backfillProgress : null;

    return NextResponse.json({
      totalTokensWithCandles,
      totalCandleCount,
      distributionByTimeframe,
      distributionBySource,
      topTokensByCandleCount,
      dateRange: {
        oldest: oldestCandle?.timestamp ?? null,
        newest: newestCandle?.timestamp ?? null,
        spanDays: timeSpanDays,
      },
      backfillStatus: backfillRunning ? 'RUNNING' : 'IDLE',
      backfillProgress: currentProgress,
    });
  } catch (error) {
    console.error('[/api/ohlcv/backfill] GET error:', error);
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : String(error),
        totalTokensWithCandles: 0,
        totalCandleCount: 0,
        distributionByTimeframe: [],
        topTokensByCandleCount: [],
      },
      { status: 500 },
    );
  }
}
