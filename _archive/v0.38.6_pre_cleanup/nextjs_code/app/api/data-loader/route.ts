import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/data-loader
 *
 * Triggers the RealDataLoader to fetch real token data.
 * Actions:
 *   - 'quick': Quick start — top 500 tokens + basic enrichment
 *   - 'full': Full load — 10,000 tokens + full enrichment + OHLCV + DNA
 *   - 'resume': Resume from where we left off
 *   - 'enrich': Only run DexScreener + DexPaprika enrichment
 *   - 'ohlcv': Only fetch OHLCV candles
 *   - 'dna': Only compute missing TokenDNA
 *   - 'status': Get current data loading status
 */

export async function POST(request: NextRequest) {
  try {
    let body: any = {};
    try { body = await request.json(); } catch { /* no body */ }

    const { action = 'quick' } = body;
    const { realDataLoader } = await import('@/lib/services/data-sources/real-data-loader');
    const { db } = await import('@/lib/db');

    switch (action) {
      case 'quick': {
        const result = await realDataLoader.quickStart();
        return NextResponse.json({ success: result.success, data: result });
      }

      case 'full': {
        // Run in background to avoid timeout - return immediately
        const targetTokens = body.targetTokens || 10000;
        // Don't await - let it run in background
        Promise.resolve().then(() =>
          realDataLoader.runFullLoad(targetTokens).catch(err =>
            console.error('[DataLoader] Full load error:', err)
          )
        );
        return NextResponse.json({
          success: true,
          message: `Full load started in background — target: ${targetTokens} tokens. Check status with GET /api/data-loader or action='status'.`,
        });
      }

      case 'resume': {
        // Run in background
        Promise.resolve().then(() =>
          realDataLoader.resumeFromLastJob().catch(err =>
            console.error('[DataLoader] Resume error:', err)
          )
        );
        return NextResponse.json({
          success: true,
          message: 'Resume started in background. Check status with GET /api/data-loader.',
        });
      }

      case 'enrich': {
        const enriched = await realDataLoader.enrichWithDexScreener(body.batchSize || 100);
        return NextResponse.json({ success: true, data: { tokensEnriched: enriched } });
      }

      case 'ohlcv': {
        const candles = await realDataLoader.fetchOHLCVForTokens(body.batchSize || 30);
        return NextResponse.json({ success: true, data: { candlesStored: candles } });
      }

      case 'dna': {
        const dna = await realDataLoader.computeMissingDNA();
        return NextResponse.json({ success: true, data: { dnaComputed: dna } });
      }

      case 'status': {
        const [tokenCount, enrichedCount, candleCount, dnaCount, lifecycleCount, activeJobs] = await Promise.all([
          db.token.count(),
          db.token.count({ where: { pairAddress: { not: null } } }),
          db.priceCandle.count(),
          db.tokenDNA.count(),
          db.tokenLifecycleState.count(),
          db.extractionJob.count({ where: { status: 'RUNNING' } }),
        ]);

        const tokensWithVolume = await db.token.count({ where: { volume24h: { gt: 0 } } });
        const tokensWithLiquidity = await db.token.count({ where: { liquidity: { gt: 0 } } });

        return NextResponse.json({
          success: true,
          data: {
            tokens: tokenCount,
            tokensWithVolume,
            tokensWithLiquidity,
            tokensEnriched: enrichedCount,
            candles: candleCount,
            dnaRecords: dnaCount,
            lifecyclePhases: lifecycleCount,
            activeJobs,
            enrichmentPct: tokenCount > 0 ? Math.round((enrichedCount / tokenCount) * 100) : 0,
          },
        });
      }

      default:
        return NextResponse.json({ error: `Unknown action: ${action}` }, { status: 400 });
    }
  } catch (error) {
    console.error('[/api/data-loader] Error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Failed' },
      { status: 500 },
    );
  }
}

export async function GET() {
  try {
    const { db } = await import('@/lib/db');

    const [tokenCount, enrichedCount, candleCount, dnaCount, activeJobs] = await Promise.all([
      db.token.count(),
      db.token.count({ where: { pairAddress: { not: null } } }),
      db.priceCandle.count(),
      db.tokenDNA.count(),
      db.extractionJob.count({ where: { status: 'RUNNING' } }),
    ]);

    const tokensWithVolume = await db.token.count({ where: { volume24h: { gt: 0 } } });
    const tokensWithLiquidity = await db.token.count({ where: { liquidity: { gt: 0 } } });

    return NextResponse.json({
      success: true,
      data: {
        tokens: tokenCount,
        tokensWithVolume,
        tokensWithLiquidity,
        tokensEnriched: enrichedCount,
        candles: candleCount,
        dnaRecords: dnaCount,
        activeJobs,
        enrichmentPct: tokenCount > 0 ? Math.round((enrichedCount / tokenCount) * 100) : 0,
        status: activeJobs > 0 ? 'LOADING' : 'IDLE',
      },
    });
  } catch (error) {
    console.error('[/api/data-loader] Status error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Failed' },
      { status: 500 },
    );
  }
}
