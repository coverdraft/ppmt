import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/deep-analysis/detect-chain?address=0x...
 * Auto-detect which chain a token/wallet address belongs to by querying DexScreener.
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const address = searchParams.get('address');

    if (!address) {
      return NextResponse.json({ error: 'address parameter required' }, { status: 400 });
    }

    // Try DexScreener first — it returns chain info for any token
    try {
      const { dexScreenerClient } = await import('@/lib/services/data-sources/dexscreener-client');
      const pairs = await dexScreenerClient.searchTokenPairs(address);

      if (pairs && pairs.length > 0) {
        const pair = pairs[0];
        const chainId = (pair.chainId || '').toLowerCase();

        // Map DexScreener chain IDs to our chain values
        const chainMap: Record<string, string> = {
          solana: 'SOL',
          ethereum: 'ETH',
          base: 'BASE',
          bsc: 'BSC',
          arbitrum: 'ARB',
          polygon: 'MATIC',
          optimism: 'OP',
          avalanche: 'AVAX',
          fantom: 'FTM',
        };

        const detectedChain = chainMap[chainId];
        if (detectedChain) {
          return NextResponse.json({
            chain: detectedChain,
            source: 'dexscreener',
            symbol: pair.baseToken?.symbol,
            name: pair.baseToken?.name,
            priceUsd: pair.priceUsd,
            confidence: 0.9,
          });
        }

        // Return raw chainId if not in our map
        if (chainId) {
          return NextResponse.json({
            chain: chainId.toUpperCase(),
            source: 'dexscreener',
            symbol: pair.baseToken?.symbol,
            name: pair.baseToken?.name,
            priceUsd: pair.priceUsd,
            confidence: 0.7,
          });
        }
      }
    } catch (err) {
      console.warn('[detect-chain] DexScreener failed:', err);
    }

    // Heuristic: check address format
    const addr = address.trim().toLowerCase();

    // Solana addresses are base58, typically 32-44 chars, no 0x prefix
    if (!addr.startsWith('0x') && addr.length >= 32 && addr.length <= 44) {
      return NextResponse.json({
        chain: 'SOL',
        source: 'heuristic',
        confidence: 0.5,
      });
    }

    // EVM addresses start with 0x and are 42 chars
    if (addr.startsWith('0x') && addr.length === 42) {
      // Check DB for known chain
      try {
        const { db } = await import('@/lib/db');
        const token = await db.token.findFirst({
          where: { address: addr },
          select: { chain: true, symbol: true },
        });
        if (token) {
          return NextResponse.json({
            chain: token.chain,
            source: 'database',
            symbol: token.symbol,
            confidence: 0.95,
          });
        }
      } catch {
        // DB lookup failed
      }

      // Default EVM chain
      return NextResponse.json({
        chain: 'ETH',
        source: 'heuristic',
        confidence: 0.4,
      });
    }

    // Unknown format
    return NextResponse.json({
      chain: null,
      source: 'unknown',
      confidence: 0,
      hint: 'Could not auto-detect chain. Please select manually.',
    });
  } catch (error) {
    console.error('[detect-chain] Error:', error);
    return NextResponse.json(
      { chain: null, error: 'Failed to detect chain' },
      { status: 500 },
    );
  }
}
