import { NextRequest, NextResponse } from 'next/server';
import { universalExtractor } from '@/lib/services/shared/universal-data-extractor';
import { db } from '@/lib/db';

/**
 * POST /api/brain/backfill
 * 
 * Historical data backfill using the Universal Data Extractor (8 sources).
 * Replaces the legacy HistoricalDataExtractor.
 * 
 * Sources: Moralis + Helius + CoinGecko + DexScreener + DeFi Llama +
 *          Etherscan + CryptoDataDownload + SQD(Subsquid)
 * 
 * Actions:
 *  - start: Start full extraction pipeline
 *  - stop: Abort running extraction
 *  - progress: Get current extraction status
 *  - quick_scan: Fast 20-token scan (Solana only)
 *  - backfill_token: Backfill a specific token using SQD
 *  - db_stats: Get database statistics
 *  - bulk_historical: Deep historical backfill using SQD (BigQuery replacement)
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const action = body.action || 'db_stats';

    switch (action) {
      case 'start': {
        const status = universalExtractor.getStatus();
        if (status.isRunning) {
          return NextResponse.json({
            status: 'ALREADY_RUNNING',
            message: 'Extraction already in progress. Use /api/extractor for details.',
          });
        }

        // Trigger extraction via the extractor API
        const extractRes = await fetch('http://localhost:3000/api/extractor', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phase: 'full', config: body.config }),
        });
        const extractData = await extractRes.json();

        return NextResponse.json({
          status: 'STARTED',
          message: 'Full extraction started via Universal Data Extractor',
          sources: status.sourceStatus,
          extractionInfo: extractData,
        });
      }

      case 'stop': {
        universalExtractor.abort();
        return NextResponse.json({ status: 'ABORTING', message: 'Extraction abort signal sent' });
      }

      case 'progress': {
        const status = universalExtractor.getStatus();
        return NextResponse.json({
          status: status.isRunning ? 'RUNNING' : 'IDLE',
          ...status,
        });
      }

      case 'quick_scan': {
        // Fast scan using only DexScreener + CoinGecko (no deep backfill)
        try {
          const scanResult = await universalExtractor.discoverTokens();
          return NextResponse.json({
            status: 'COMPLETED',
            tokensDiscovered: scanResult.count,
            addresses: scanResult.addresses.slice(0, 20),
          });
        } catch (err) {
          return NextResponse.json({ status: 'FAILED', error: String(err) }, { status: 500 });
        }
      }

      case 'backfill_token': {
        const { tokenAddress, chain, fromDays } = body;
        if (!tokenAddress) {
          return NextResponse.json({ error: 'tokenAddress required' }, { status: 400 });
        }

        // Use SQD for deep historical backfill
        const result = await universalExtractor.historicalBackfill.backfillToken(
          tokenAddress,
          chain || 'ETH',
          { fromDays: fromDays || 365, includeSwaps: true, includeTransfers: true }
        );

        return NextResponse.json({
          status: 'COMPLETED',
          tokenAddress,
          chain: chain || 'ETH',
          result,
        });
      }

      case 'bulk_historical': {
        // Deep historical backfill using SQD (BigQuery replacement)
        const { tokenAddress, chain, fromBlock, toBlock } = body;
        if (!tokenAddress || !chain) {
          return NextResponse.json({ error: 'tokenAddress and chain required' }, { status: 400 });
        }

        const result = await universalExtractor.sqd.bulkBackfill(
          chain,
          tokenAddress,
          fromBlock || 0,
          toBlock || 99999999,
        );

        return NextResponse.json({
          status: 'COMPLETED',
          tokenAddress,
          chain,
          eventsFetched: result.totalFetched,
          blockRangesScanned: result.blockRangesScanned,
        });
      }

      case 'ohlcv_backfill': {
        // Simple OHLCV backfill using CoinGecko via OHLCV Pipeline
        // This is the recommended way to get price data for backtesting
        const { tokenCount: limit = 20 } = body;
        try {
          const { ohlcvPipeline } = await import('@/lib/services/data-sources/ohlcv-pipeline');
          const result = await ohlcvPipeline.backfillTopTokens(limit);
          return NextResponse.json({
            status: 'COMPLETED',
            totalTokens: result.totalTokens,
            totalCandlesStored: result.totalCandlesStored,
            failedTokens: result.failedTokens,
            durationMs: result.duration,
            message: `Backfilled ${result.totalTokens} tokens with ${result.totalCandlesStored} candles in ${(result.duration / 1000).toFixed(1)}s`,
          });
        } catch (err) {
          return NextResponse.json({ 
            status: 'FAILED', 
            error: String(err),
            message: 'OHLCV backfill failed. CoinGecko rate limits may apply — try again in a few minutes.',
          }, { status: 500 });
        }
      }

      case 'db_stats': {
        let tokenCount = 0, candleCount = 0, traderCount = 0, txCount = 0;
        let signalCount = 0, systemCount = 0, cycleCount = 0, jobCount = 0;
        let oldestCandle: { timestamp: Date } | null = null;
        let newestCandle: { timestamp: Date } | null = null;

        try {
          [tokenCount, candleCount, traderCount, txCount,
           signalCount, systemCount, cycleCount, jobCount] = await Promise.all([
            db.token.count(),
            db.priceCandle.count(),
            db.trader.count(),
            db.traderTransaction.count(),
            db.predictiveSignal.count(),
            db.tradingSystem.count(),
            db.brainCycleRun.count(),
            db.extractionJob.count().catch(() => 0),
          ]);

          oldestCandle = await db.priceCandle.findFirst({ orderBy: { timestamp: 'asc' } });
          newestCandle = await db.priceCandle.findFirst({ orderBy: { timestamp: 'desc' } });
        } catch (dbErr) {
          console.error('[backfill/db_stats] DB error:', dbErr);
        }
        let recentJobs: Array<{ id: string; jobType: string; status: string; tokensDiscovered: number; candlesStored: number; createdAt: Date }> = [];
        try {
          recentJobs = await db.extractionJob.findMany({
            orderBy: { createdAt: 'desc' },
            take: 5,
            select: { id: true, jobType: true, status: true, tokensDiscovered: true, candlesStored: true, createdAt: true },
          });
        } catch { /* extractionJob might not exist yet */ }

        return NextResponse.json({
          tokens: tokenCount,
          candles: candleCount,
          traders: traderCount,
          transactions: txCount,
          signals: signalCount,
          tradingSystems: systemCount,
          brainCycles: cycleCount,
          extractionJobs: jobCount,
          oldestCandle: oldestCandle?.timestamp || null,
          newestCandle: newestCandle?.timestamp || null,
          candleTimeSpan: oldestCandle && newestCandle
            ? `${((newestCandle.timestamp.getTime() - oldestCandle.timestamp.getTime()) / 86400000).toFixed(1)} days`
            : 'No candles',
          recentJobs,
          sources: universalExtractor.getStatus().sourceStatus,
        });
      }

      default:
        return NextResponse.json({
          error: `Unknown action: ${action}`,
          availableActions: ['start', 'stop', 'progress', 'quick_scan', 'backfill_token', 'bulk_historical', 'ohlcv_backfill', 'db_stats'],
        });
    }
  } catch (error) {
    console.error('[api/brain/backfill] Error:', error);
    return NextResponse.json(
      { error: String(error) },
      { status: 500 }
    );
  }
}

export async function GET() {
  const status = universalExtractor.getStatus();
  return NextResponse.json({
    status: status.isRunning ? 'RUNNING' : 'IDLE',
    ...status,
  });
}
