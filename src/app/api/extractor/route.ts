/**
 * Universal Data Extractor API Routes
 * POST /api/extractor - Start extraction or run specific phase
 * GET /api/extractor - Get extraction status
 * DELETE /api/extractor - Abort running extraction
 */

import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { universalExtractor, UniversalDataExtractor } from '@/lib/services/shared/universal-data-extractor';

// Track running extraction
let runningExtraction: {
  extractor: UniversalDataExtractor;
  result: unknown;
  startedAt: number;
  status: 'running' | 'completed' | 'failed';
  phase: string;
} | null = null;

export async function GET() {
  try {
    const status = universalExtractor.getStatus();

    // Get recent jobs from DB
    let recentJobs: Awaited<ReturnType<typeof db.extractionJob.findMany>> = [];
    try {
      recentJobs = await db.extractionJob.findMany({
        orderBy: { createdAt: 'desc' },
        take: 10,
      });
    } catch (dbErr) {
      console.error('[Extractor API] DB error fetching jobs:', dbErr);
    }

    return NextResponse.json({
      status: runningExtraction?.status || 'idle',
      isRunning: status.isRunning,
      currentJobId: status.currentJobId,
      cacheSize: status.cacheSize,
      activeJobs: status.activeJobs,
      errors: status.errors,
      sources: status.sourceStatus,
      config: status.config,
      lastResult: runningExtraction?.result || null,
      lastDuration: runningExtraction ? Date.now() - runningExtraction.startedAt : 0,
      recentJobs: recentJobs.map(j => ({
        id: j.id,
        jobType: j.jobType,
        status: j.status,
        sourcesUsed: JSON.parse(j.sourcesUsed || '[]'),
        tokensDiscovered: j.tokensDiscovered,
        candlesStored: j.candlesStored,
        walletsProfiled: j.walletsProfiled,
        transactionsStored: j.transactionsStored,
        signalsGenerated: j.signalsGenerated,
        protocolsStored: j.protocolsStored,
        startedAt: j.startedAt,
        completedAt: j.completedAt,
        durationMs: j.durationMs,
        errors: JSON.parse(j.error || '[]'),
        createdAt: j.createdAt,
      })),
    });
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const { action, phase, config, walletAddresses } = body;

    if (action === 'abort') {
      universalExtractor.abort();
      if (runningExtraction) runningExtraction.status = 'failed';
      return NextResponse.json({ message: 'Extraction aborted' });
    }

    if (runningExtraction?.status === 'running') {
      return NextResponse.json(
        { error: 'Extraction already running', status: runningExtraction },
        { status: 409 }
      );
    }

    // Create a new extractor with optional config
    const extractor = config ? new UniversalDataExtractor(config) : universalExtractor;

    runningExtraction = {
      extractor,
      result: null,
      startedAt: Date.now(),
      status: 'running',
      phase: phase || 'full',
    };

    // Run extraction based on phase
    const extractionPromise = (async () => {
      try {
        let result;

        switch (phase) {
          case 'scan':
            result = await extractor.discoverTokens();
            break;
          case 'enrich': {
            const discovery = await extractor.discoverTokens();
            result = await extractor.enrichTokens(discovery.addresses);
            break;
          }
          case 'ohlcv': {
            const discovery = await extractor.discoverTokens();
            result = await extractor.backfillOHLCV(discovery.addresses);
            break;
          }
          case 'traders': {
            const discovery = await extractor.discoverTokens();
            result = await extractor.extractTraders(discovery.addresses);
            break;
          }
          case 'wallets':
            result = await extractor.extractWalletIntelligence(
              walletAddresses || []
            );
            break;
          case 'sentiment':
            result = await extractor.extractSentimentIntelligence();
            break;
          case 'protocols':
            result = await extractor.extractProtocolAnalytics();
            break;
          case 'realtime':
            result = await extractor.runRealtimeSync();
            break;
          case 'bulk-backfill':
            result = await extractor.runBulkBackfill(body.timeframe || '1h');
            break;
          case 'full':
          default:
            result = await extractor.runFullExtraction();
            break;
        }

        if (runningExtraction) {
          runningExtraction.result = result;
          runningExtraction.status = 'completed';
        }

        return result;
      } catch (error) {
        if (runningExtraction) runningExtraction.status = 'failed';
        console.error('[Extractor API] Error:', error);
        throw error;
      }
    })();

    // Don't await — let it run in background
    extractionPromise.catch(() => {});

    return NextResponse.json({
      message: `Extraction started: ${phase || 'full'}`,
      phase: phase || 'full',
      startedAt: runningExtraction.startedAt,
      availablePhases: [
        'scan', 'enrich', 'ohlcv', 'traders', 'wallets',
        'sentiment', 'protocols', 'realtime', 'bulk-backfill', 'full',
      ],
    });

  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 });
  }
}

export async function DELETE() {
  universalExtractor.abort();
  if (runningExtraction) runningExtraction.status = 'failed';
  runningExtraction = null;
  return NextResponse.json({ message: 'Extraction stopped and reset' });
}
