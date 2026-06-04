import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/market/smart-money
 *
 * Tracks smart money wallets for a given pool.
 * NOTE: Smart money tracking requires on-chain wallet-level data which
 * is NOT available from DexPaprika or DexScreener APIs. This endpoint
 * currently returns an empty array. For real smart money tracking,
 * integrate Helius API (Solana) or Etherscan API (Ethereum).
 *
 * Query params:
 *   chain        – chain identifier (default: "solana")
 *   poolId       – required; the pool to analyse
 *   minSwapCount – minimum swaps for a wallet to qualify (default: 2)
 *   minValueUsd  – minimum total USD value for a wallet to qualify (default: 100)
 *
 * Response envelope:
 *   { smartMoney: SmartMoneySwap[], source: 'dexpaprika', note?: string }
 */

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const chain = searchParams.get('chain') || 'solana';
  const poolId = searchParams.get('poolId') || '';
  const minSwapCount = Math.max(parseInt(searchParams.get('minSwapCount') || '2', 10), 1);
  const minValueUsd = Math.max(parseInt(searchParams.get('minValueUsd') || '100', 10), 0);

  if (!poolId) {
    return NextResponse.json(
      { smartMoney: [], source: 'dexpaprika' as const, error: 'poolId is required' },
      { status: 400 },
    );
  }

  try {
    const smModule = await import('@/lib/services/data-sources/dexpaprika-client');
    const dexPaprikaClient = smModule.dexPaprikaClient;
    const smartMoney = await dexPaprikaClient.trackSmartMoney(chain, poolId, minSwapCount, minValueUsd);

    return NextResponse.json({
      smartMoney,
      source: 'dexpaprika' as const,
      note: smartMoney.length === 0
        ? 'Smart money tracking requires on-chain wallet data (Helius/Etherscan). Neither DexPaprika nor DexScreener provides individual swap data with wallet addresses.'
        : undefined,
    });
  } catch (error) {
    console.error('[/api/market/smart-money] Failed to track smart money:', error);
    return NextResponse.json(
      {
        smartMoney: [],
        source: 'dexpaprika' as const,
        error: error instanceof Error ? error.message : 'Failed to track smart money',
      },
      { status: 500 },
    );
  }
}
