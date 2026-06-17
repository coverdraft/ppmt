import { NextRequest, NextResponse } from 'next/server';
import { normalizeChain } from '@/lib/format';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/market/wallet/[address]
 *
 * Fetches wallet transaction history using the DataIngestionPipeline,
 * which combines Solana RPC signatures
 * (for SOL) or Ethereum RPC data (for ETH).
 *
 * Route params:
 *   address – wallet address (path parameter)
 *
 * Query params:
 *   chain – chain identifier, "SOL" or "ETH" (default: "SOL")
 *
 * Rate-limit notes (free tiers):
 *   - Solana RPC (public):   ~100 req/10s per IP – getSignaturesForAddress + individual
 *                            getTransaction calls can exhaust this quickly.
 *                            Pipeline caps at 20 tx lookups per request.
 *   - Ethereum RPC (public): Varies by provider, typically 10-50 req/s.
 *
 * Response envelope:
 *   { data: WalletHistoryResponse | null, error: string | null, source: 'live' | 'cache' | 'fallback' }
 */

let _pipeline: import('@/lib/services/data-sources/data-ingestion').DataIngestionPipeline | null = null;
async function getPipeline() {
  if (!_pipeline) {
    const { DataIngestionPipeline } = await import('@/lib/services/data-sources/data-ingestion');
    _pipeline = new DataIngestionPipeline();
  }
  return _pipeline;
}

interface WalletHistoryResponse {
  address: string;
  chain: string;
  transactions: TransactionItem[];
  totalCount: number;
  hasMore: boolean;
}

interface TransactionItem {
  txHash: string;
  blockTime: string; // ISO string
  action: string;
  tokenAddress: string;
  tokenSymbol?: string;
  quoteToken?: string;
  amountIn: number;
  amountOut: number;
  valueUsd: number;
  dex?: string;
  slippageBps?: number;
  isFrontrun: boolean;
  isSandwich: boolean;
  priorityFee?: number;
  gasUsed?: number;
}

function formatTransaction(tx: import('@/lib/services/data-sources/data-ingestion').ParsedTransaction): TransactionItem {
  return {
    txHash: tx.txHash,
    blockTime: tx.blockTime.toISOString(),
    action: tx.action,
    tokenAddress: tx.tokenAddress,
    tokenSymbol: tx.tokenSymbol,
    quoteToken: tx.quoteToken,
    amountIn: tx.amountIn,
    amountOut: tx.amountOut,
    valueUsd: tx.valueUsd,
    dex: tx.dex,
    slippageBps: tx.slippageBps,
    isFrontrun: tx.isFrontrun,
    isSandwich: tx.isSandwich,
    priorityFee: tx.priorityFee,
    gasUsed: tx.gasUsed,
  };
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ address: string }> },
) {
  const { address } = await params;
  const searchParams = request.nextUrl.searchParams;
  const chain = (searchParams.get('chain') || 'SOL').toUpperCase();

  if (!address) {
    return NextResponse.json(
      { data: null, error: 'Wallet address is required', source: 'live' as const },
      { status: 400 },
    );
  }

  // Validate chain parameter - accept both short and long forms
  const canonicalChain = normalizeChain(chain);
  if (!['SOL', 'ETH'].includes(canonicalChain)) {
    return NextResponse.json(
      {
        data: null,
        error: `Unsupported chain "${chain}". Supported: SOL, ETH`,
        source: 'live' as const,
      },
      { status: 400 },
    );
  }

  try {
    const pipeline = await getPipeline();
    const history: import('@/lib/services/data-sources/data-ingestion').WalletTransactionHistory = await pipeline.getWalletHistory(address, chain);

    const response: WalletHistoryResponse = {
      address: history.address,
      chain: history.chain,
      transactions: history.transactions.map(formatTransaction),
      totalCount: history.totalCount,
      hasMore: history.hasMore,
    };

    // Persist transactions to DB (fire-and-forget)
    persistTransactions(address, chain, history.transactions).catch(() => {});

    return NextResponse.json({
      data: response,
      error: null,
      source: 'live' as const,
    });
  } catch (error) {
    console.error('[/api/market/wallet/[address]] Live fetch failed, falling back to DB:', error);

    try {
      const { db } = await import('@/lib/db');
      const trader = await db.trader.findFirst({
        where: { address },
      });

      if (!trader) {
        return NextResponse.json(
          { data: null, error: 'Wallet not found in database', source: 'fallback' as const },
          { status: 404 },
        );
      }

      const dbTransactions = await db.traderTransaction.findMany({
        where: { traderId: trader.id },
        orderBy: { blockTime: 'desc' },
        take: 100,
      });

      const response: WalletHistoryResponse = {
        address,
        chain,
        transactions: dbTransactions.map((tx) => ({
          txHash: tx.txHash,
          blockTime: tx.blockTime.toISOString(),
          action: tx.action,
          tokenAddress: tx.tokenAddress,
          tokenSymbol: tx.tokenSymbol ?? undefined,
          quoteToken: tx.quoteToken ?? undefined,
          amountIn: tx.amountIn,
          amountOut: tx.amountOut,
          valueUsd: tx.valueUsd,
          dex: tx.dex ?? undefined,
          slippageBps: tx.slippageBps ?? undefined,
          isFrontrun: tx.isFrontrun,
          isSandwich: tx.isSandwich,
          priorityFee: tx.priorityFee ?? undefined,
          gasUsed: tx.gasUsed ?? undefined,
        })),
        totalCount: dbTransactions.length,
        hasMore: dbTransactions.length >= 100,
      };

      return NextResponse.json({
        data: response,
        error: null,
        source: 'fallback' as const,
      });
    } catch (dbError) {
      console.error('[/api/market/wallet/[address]] DB fallback also failed:', dbError);
      return NextResponse.json(
        {
          data: null,
          error: 'Wallet lookup failed from live source and database',
          source: 'fallback' as const,
        },
        { status: 500 },
      );
    }
  }
}

async function persistTransactions(
  address: string,
  chain: string,
  transactions: import('@/lib/services/data-sources/data-ingestion').ParsedTransaction[],
) {
  try {
    const { db } = await import('@/lib/db');
    // Ensure trader record exists
    const trader = await db.trader.upsert({
      where: { address },
      update: { lastActive: new Date() },
      create: {
        address,
        chain: normalizeChain(chain),
      },
    });

    // Insert transactions (skip duplicates via txHash unique constraint)
    for (const tx of transactions) {
      try {
        await db.traderTransaction.create({
          data: {
            traderId: trader.id,
            txHash: tx.txHash || `unknown-${Date.now()}-${Math.random()}`,
            blockTime: tx.blockTime,
            chain: normalizeChain(chain),
            action: tx.action,
            tokenAddress: tx.tokenAddress,
            tokenSymbol: tx.tokenSymbol,
            quoteToken: tx.quoteToken,
            amountIn: tx.amountIn,
            amountOut: tx.amountOut,
            valueUsd: tx.valueUsd,
            dex: tx.dex,
            slippageBps: tx.slippageBps,
            isFrontrun: tx.isFrontrun,
            isSandwich: tx.isSandwich,
            priorityFee: tx.priorityFee,
            gasUsed: tx.gasUsed,
          },
        });
      } catch {
        // Duplicate txHash – ignore (upsert alternative is heavier)
      }
    }
  } catch {
    // Trader upsert or batch insert failed – non-critical
  }
}
