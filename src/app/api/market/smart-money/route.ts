import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/market/smart-money
 *
 * Tracks smart money wallets for a given pool using the DB-based
 * SmartMoneyTracker which queries actual Trader/TraderTransaction data.
 * Falls back to DexPaprika if no DB data is available.
 *
 * Query params:
 *   chain        – chain identifier (default: "SOL")
 *   poolId       – required; the pool to analyse
 *   tokenAddress – optional; token address to look up in DB
 *   minSwapCount – minimum swaps for a wallet to qualify (default: 2)
 *   minValueUsd  – minimum total USD value for a wallet to qualify (default: 100)
 *
 * Response envelope:
 *   { smartMoney: SmartMoneySwap[], source: string, note?: string }
 */

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const chain = searchParams.get('chain') || 'SOL';
  const poolId = searchParams.get('poolId') || '';
  const tokenAddress = searchParams.get('tokenAddress') || '';
  const minSwapCount = Math.max(parseInt(searchParams.get('minSwapCount') || '2', 10), 1);
  const minValueUsd = Math.max(parseInt(searchParams.get('minValueUsd') || '100', 10), 0);

  if (!poolId) {
    return NextResponse.json(
      { smartMoney: [], source: 'none', error: 'poolId is required' },
      { status: 400 },
    );
  }

  try {
    // Primary: Use DB-based SmartMoneyTracker with real trader data
    if (tokenAddress) {
      try {
        const { smartMoneyTracker } = await import('@/lib/services/execution/smart-money-tracker');
        const analysis = await smartMoneyTracker.analyzePool(chain, poolId, tokenAddress);
        if (analysis && analysis.activeWallets.length > 0) {
          return NextResponse.json({
            smartMoney: analysis.activeWallets.map(w => ({
              wallet: w.address,
              poolId,
              chain,
              swapCount: w.swapCount,
              netBuyValueUsd: w.netBuyValueUsd,
              averageSizeUsd: w.swapCount > 0 ? w.netBuyValueUsd / w.swapCount : 0,
              isSmartMoney: w.isKnown,
              label: w.label,
            })),
            source: 'db-smart-money-tracker',
            netDirection: analysis.netDirection,
            signalStrength: analysis.signalStrength,
          });
        }
      } catch (err) {
        console.warn('[/api/market/smart-money] DB tracker failed, falling back to DexPaprika:', err);
      }
    }

    // Fallback: DexPaprika (may return empty — no wallet-level data available)
    const smModule = await import('@/lib/services/data-sources/dexpaprika-client');
    const dexPaprikaClient = smModule.dexPaprikaClient;
    const smartMoney = await dexPaprikaClient.trackSmartMoney(chain, poolId, minSwapCount, minValueUsd);

    return NextResponse.json({
      smartMoney,
      source: 'dexpaprika' as const,
      note: smartMoney.length === 0
        ? 'No smart money data available. Run smart-money-sync first to populate trader data, or provide tokenAddress parameter for DB-based analysis.'
        : undefined,
    });
  } catch (error) {
    console.error('[/api/market/smart-money] Failed to track smart money:', error);
    return NextResponse.json(
      {
        smartMoney: [],
        source: 'none',
        error: error instanceof Error ? error.message : 'Failed to track smart money',
      },
      { status: 500 },
    );
  }
}
