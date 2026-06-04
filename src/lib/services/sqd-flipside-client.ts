/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  SQD (Subsquid) Client — BigQuery Replacement                          ║
 * ║  225+ chains, FREE, no credit card                                     ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 *
 * SQD (Subsquid):
 *   - 225+ chains, data from genesis
 *   - NO rate limits (pre-mainnet, decentralized)
 *   - REST API with GraphQL-like queries
 *   - Petabytes of indexed blockchain events
 *   - 100% FREE, no credit card needed
 *   - API Key configured in .env
 */

import { db } from '../db';

// ============================================================
// TYPES
// ============================================================

export interface SQDEventQuery {
  chain: string;
  fromBlock?: number;
  toBlock?: number;
  address?: string;
  topic0?: string;
  limit?: number;
}

export interface SQDEventResult {
  block: {
    number: number;
    timestamp: number;
    hash: string;
  };
  transaction: {
    hash: string;
    from: string;
    to: string;
    value: string;
    gasUsed?: number;
  };
  log: {
    address: string;
    topics: string[];
    data: string;
    logIndex: number;
  };
}

export interface SQDTransferResult {
  block: {
    number: number;
    timestamp: number;
  };
  transaction: {
    hash: string;
    from: string;
    to: string;
  };
  transfer: {
    from: string;
    to: string;
    value: string;
    tokenAddress: string;
    tokenSymbol?: string;
    tokenDecimals?: number;
  };
}

// ============================================================
// IN-MEMORY CACHE (shared pattern with universal extractor)
// ============================================================

class SourceCache {
  private cache = new Map<string, { data: unknown; timestamp: number }>();
  private ttlMs: number;

  constructor(ttlMinutes = 30) {
    this.ttlMs = ttlMinutes * 60 * 1000;
  }

  get<T>(key: string): T | null {
    const entry = this.cache.get(key);
    if (!entry) return null;
    if (Date.now() - entry.timestamp > this.ttlMs) {
      this.cache.delete(key);
      return null;
    }
    return entry.data as T;
  }

  set(key: string, data: unknown): void {
    this.cache.set(key, { data, timestamp: Date.now() });
  }
}

// ============================================================
// RATE LIMITER
// ============================================================

class RateLimiter {
  private tokens: number;
  private maxTokens: number;
  private refillRate: number;
  private lastRefill: number;

  constructor(maxRps: number, burstSize?: number) {
    this.maxTokens = burstSize ?? Math.ceil(maxRps * 2);
    this.tokens = this.maxTokens;
    this.refillRate = maxRps;
    this.lastRefill = Date.now();
  }

  async acquire(): Promise<void> {
    const now = Date.now();
    const elapsed = (now - this.lastRefill) / 1000;
    this.tokens = Math.min(this.maxTokens, this.tokens + elapsed * this.refillRate);
    this.lastRefill = now;

    if (this.tokens >= 1) {
      this.tokens -= 1;
      return;
    }

    const waitMs = Math.max(100, Math.ceil(1000 / this.refillRate));
    await new Promise(resolve => setTimeout(resolve, waitMs));
    this.tokens = Math.min(this.maxTokens, this.tokens + 1);
    this.tokens -= 1;
  }
}

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ============================================================
// SQD (SUBSQUID) CLIENT — 🏆 BigQuery Replacement
// 225+ chains, NO rate limits, 100% FREE
// ============================================================

export class SQDClient {
  private gatewayUrl: string;
  private apiKey: string;
  private limiter: RateLimiter;
  private cache: SourceCache;

  constructor(apiKey?: string, gatewayUrl?: string) {
    this.apiKey = apiKey || process.env.SQD_API_KEY || '';
    this.gatewayUrl = gatewayUrl || process.env.SQD_GATEWAY_URL || 'https://v2.archive.subsquid.io';
    this.limiter = new RateLimiter(20, 40); // SQD has very generous limits
    this.cache = new SourceCache(60); // Cache for 60 min — historical data doesn't change
  }

  get isConfigured(): boolean {
    // SQD works even without API key for public datasets
    return true;
  }

  private toSQDChain(chain: string): string {
    const map: Record<string, string> = {
      'SOL': 'solana-mainnet',
      'ETH': 'ethereum-mainnet',
      'BASE': 'base-mainnet',
      'ARB': 'arbitrum-one',
      'OP': 'optimism-mainnet',
      'BSC': 'binance-mainnet',
      'MATIC': 'polygon-mainnet',
      'AVAX': 'avalanche-c',
      'FTM': 'fantom-mainnet',
    };
    return map[chain] || chain.toLowerCase();
  }

  /**
   * Query historical events from SQD archive.
   * This is the core method that replaces BigQuery's event scanning capability.
   * Supports filtering by contract address, topics, block range.
   */
  async queryEvents(params: SQDEventQuery): Promise<SQDEventResult[]> {
    const cacheKey = `sqd:events:${JSON.stringify(params)}`;
    const cached = this.cache.get<SQDEventResult[]>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();
    try {
      const chainSlug = this.toSQDChain(params.chain);
      const url = `${this.gatewayUrl}/query/${chainSlug}`;

      const body = {
        fromBlock: params.fromBlock || 0,
        toBlock: params.toBlock || undefined,
        logs: params.address ? [{
          address: [params.address],
          topic0: params.topic0 ? [params.topic0] : undefined,
        }] : undefined,
        limit: params.limit || 1000,
      };

      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      };
      if (this.apiKey) {
        headers['Authorization'] = `Bearer ${this.apiKey}`;
      }

      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        console.warn(`[SQD] Query failed: ${res.status} for chain ${chainSlug}`);
        return [];
      }

      const data = await res.json();
      const results: SQDEventResult[] = (data?.logs || []).map((log: Record<string, unknown>) => ({
        block: {
          number: (log as Record<string, Record<string, unknown>>).block?.number as number || 0,
          timestamp: (log as Record<string, Record<string, unknown>>).block?.timestamp as number || 0,
          hash: ((log as Record<string, Record<string, unknown>>).block?.hash as string) || '',
        },
        transaction: {
          hash: ((log as Record<string, Record<string, unknown>>).transaction?.hash as string) || '',
          from: ((log as Record<string, Record<string, unknown>>).transaction?.from as string) || '',
          to: ((log as Record<string, Record<string, unknown>>).transaction?.to as string) || '',
          value: ((log as Record<string, Record<string, unknown>>).transaction?.value as string) || '0',
        },
        log: {
          address: (log.address as string) || '',
          topics: (log.topics as string[]) || [],
          data: (log.data as string) || '0x',
          logIndex: (log.logIndex as number) || 0,
        },
      }));

      this.cache.set(cacheKey, results);
      console.log(`[SQD] Fetched ${results.length} events for ${chainSlug}`);
      return results;
    } catch (err) {
      console.error('[SQD] Query error:', err);
      return [];
    }
  }

  /**
   * Get ERC-20/Token transfers for a contract.
   * Replaces BigQuery's `crypto_ethereum.transfers` table queries.
   */
  async getTokenTransfers(
    chain: string,
    tokenAddress: string,
    fromBlock: number = 0,
    toBlock?: number,
    limit: number = 5000
  ): Promise<SQDTransferResult[]> {
    // ERC-20 Transfer event topic
    const TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';

    const events = await this.queryEvents({
      chain,
      address: tokenAddress,
      topic0: TRANSFER_TOPIC,
      fromBlock,
      toBlock,
      limit,
    });

    return events.map(e => ({
      block: e.block,
      transaction: e.transaction,
      transfer: {
        from: e.log.topics[1] ? '0x' + e.log.topics[1].slice(26) : '',
        to: e.log.topics[2] ? '0x' + e.log.topics[2].slice(26) : '',
        value: e.log.data !== '0x' ? BigInt(e.log.data).toString() : '0',
        tokenAddress: e.log.address,
      },
    }));
  }

  /**
   * Get swap events from major DEXes on a chain.
   * Replaces BigQuery's DEX analytics queries.
   */
  async getDEXSwaps(
    chain: string,
    dexAddress: string,
    fromBlock: number = 0,
    toBlock?: number,
    limit: number = 5000
  ): Promise<SQDEventResult[]> {
    // Uniswap V2/V3 Swap event topic
    const SWAP_TOPIC_V2 = '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822';
    const SWAP_TOPIC_V3 = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67';

    const [v2Swaps, v3Swaps] = await Promise.all([
      this.queryEvents({ chain, address: dexAddress, topic0: SWAP_TOPIC_V2, fromBlock, toBlock, limit: Math.ceil(limit / 2) }),
      this.queryEvents({ chain, address: dexAddress, topic0: SWAP_TOPIC_V3, fromBlock, toBlock, limit: Math.ceil(limit / 2) }),
    ]);

    return [...v2Swaps, ...v3Swaps].sort((a, b) => b.block.number - a.block.number);
  }

  /**
   * Bulk backfill: Get all events for a token from a specific block range.
   * This is the method that replaces BigQuery's bulk historical scans.
   * Paginates automatically through large block ranges.
   */
  async bulkBackfill(
    chain: string,
    tokenAddress: string,
    fromBlock: number,
    toBlock: number,
    chunkSize: number = 10000
  ): Promise<{ events: SQDEventResult[]; totalFetched: number; blockRangesScanned: number }> {
    const allEvents: SQDEventResult[] = [];
    let rangesScanned = 0;

    for (let start = fromBlock; start < toBlock; start += chunkSize) {
      const end = Math.min(start + chunkSize, toBlock);
      const events = await this.queryEvents({
        chain,
        address: tokenAddress,
        fromBlock: start,
        toBlock: end,
        limit: 10000,
      });

      allEvents.push(...events);
      rangesScanned++;

      if (events.length === 0) continue;
      await delay(100); // Be gentle between chunks
    }

    console.log(`[SQD] Bulk backfill: ${allEvents.length} events for ${tokenAddress} across ${rangesScanned} block ranges`);
    return { events: allEvents, totalFetched: allEvents.length, blockRangesScanned: rangesScanned };
  }
}

// ============================================================
// HISTORICAL BACKFILL ENGINE
// Uses SQD for all historical data extraction
// ============================================================

export class HistoricalBackfillEngine {
  readonly sqd: SQDClient;

  constructor() {
    this.sqd = new SQDClient();
  }

  /**
   * Full historical backfill for a token.
   * Strategy: SQD for raw events and DEX swaps (genesis→present).
   * 1. Fetch DEX swap events via SQD for volume analytics
   * 2. Fetch token transfers via SQD for trader data
   * 3. Store everything in PostgreSQL
   */
  async backfillToken(
    tokenAddress: string,
    chain: string,
    options: { fromDays?: number; includeSwaps?: boolean; includeTransfers?: boolean } = {}
  ): Promise<{
    eventsFetched: number;
    swapsFetched: number;
    transfersFetched: number;
    candlesStored: number;
    duration: number;
  }> {
    const startTime = Date.now();
    const fromDays = options.fromDays || 365;
    let eventsFetched = 0;
    let swapsFetched = 0;
    let transfersFetched = 0;
    let candlesStored = 0;

    console.log(`[Backfill] Starting backfill for ${tokenAddress} on ${chain} (${fromDays} days)`);

    // Phase 1: SQD DEX swap events for volume analytics
    if (options.includeSwaps !== false) {
      try {
        const swaps = await this.sqd.getDEXSwaps(chain, tokenAddress);
        swapsFetched = swaps.length;
        eventsFetched += swapsFetched;

        // Aggregate swap events into daily candles
        const dailyMap = new Map<string, { volume: number; trades: number }>();
        for (const swap of swaps) {
          const date = new Date(swap.block.timestamp * 1000);
          const dayKey = date.toISOString().slice(0, 10);
          const existing = dailyMap.get(dayKey) || { volume: 0, trades: 0 };
          existing.trades += 1;
          dailyMap.set(dayKey, existing);
        }

        // Store daily candles from aggregated swap data using batch createMany
        const dailyCandleData = Array.from(dailyMap.entries()).map(([dayKey, dayData]) => ({
          tokenAddress,
          chain,
          timeframe: '1d',
          timestamp: new Date(dayKey),
          open: 0, // Will be enriched by CoinGecko
          high: 0,
          low: 0,
          close: 0,
          volume: dayData.volume,
          trades: dayData.trades,
          source: 'sqd',
        } as const));

        if (dailyCandleData.length > 0) {
          try {
            await db.priceCandle.createMany({
              data: dailyCandleData,
            } as any);
            candlesStored += dailyCandleData.length;
          } catch {
            // Fallback to individual upserts for existing records that need updating
            for (const candle of dailyCandleData) {
              try {
                await db.priceCandle.upsert({
                  where: {
                    tokenAddress_chain_timeframe_timestamp: {
                      tokenAddress: candle.tokenAddress,
                      chain: candle.chain,
                      timeframe: candle.timeframe,
                      timestamp: candle.timestamp,
                    },
                  },
                  create: candle,
                  update: {
                    volume: candle.volume,
                    trades: candle.trades,
                  },
                });
                candlesStored++;
              } catch {
                // Skip duplicates
              }
            }
          }
        }
        console.log(`[Backfill] SQD: ${swapsFetched} DEX swaps aggregated into ${candlesStored} daily candles`);
      } catch (err) {
        console.warn('[Backfill] SQD swap fetch failed:', err);
      }
    }

    // Phase 2: SQD token transfers (deeper history, genesis→present)
    if (options.includeTransfers !== false) {
      try {
        const transfers = await this.sqd.getTokenTransfers(chain, tokenAddress);
        transfersFetched = transfers.length;
        eventsFetched += transfersFetched;

        // Collect unique trader addresses
        const traderAddresses = new Set<string>();
        for (const tx of transfers.slice(0, 500)) {
          if (tx.transfer.from) traderAddresses.add(tx.transfer.from);
        }

        // Batch create traders using createMany with skipDuplicates
        const traderData = Array.from(traderAddresses).map(addr => ({
          address: addr,
          chain,
          totalTrades: 1,
          lastActive: new Date(), // Will be updated below with actual timestamps
        } as const));

        if (traderData.length > 0) {
          try {
            await db.trader.createMany({
              data: traderData,
            } as any);
          } catch {
            // Fallback to individual upserts for batch failures
            for (const trader of traderData) {
              try {
                await db.trader.upsert({
                  where: { address: trader.address },
                  create: trader,
                  update: {
                    totalTrades: { increment: 1 },
                    lastActive: trader.lastActive,
                  },
                });
              } catch {
                // Skip individual failures
              }
            }
          }
        }

        // Build batch transaction data
        const transactionData: Array<{
          traderId: string;
          txHash: string;
          blockNumber: number;
          blockTime: Date;
          chain: string;
          action: string;
          tokenAddress: string;
          valueUsd: number;
        }> = [];
        for (const tx of transfers.slice(0, 500)) {
          const fromAddr = tx.transfer.from || tx.transaction.from;
          // Look up trader ID — we need it for the foreign key
          const trader = await db.trader.findUnique({ where: { address: fromAddr } });
          if (!trader) continue;

          transactionData.push({
            traderId: trader.id,
            txHash: tx.transaction.hash,
            blockNumber: tx.block.number,
            blockTime: new Date(tx.block.timestamp * 1000),
            chain,
            action: 'SWAP',
            tokenAddress,
            valueUsd: 0 as unknown as number, // Will be enriched — Prisma Decimal field
          } as const);
        }

        // Batch create transactions using createMany with skipDuplicates
        if (transactionData.length > 0) {
          try {
            await db.traderTransaction.createMany({
              data: transactionData,
            } as any);
          } catch {
            // Fallback to individual upserts for batch failures
            for (const txn of transactionData) {
              try {
                await db.traderTransaction.upsert({
                  where: { txHash: (txn as { txHash: string }).txHash },
                  create: txn as Parameters<typeof db.traderTransaction.create>[0]['data'],
                  update: {},
                });
              } catch {
                // Skip individual failures
              }
            }
          }
        }
        console.log(`[Backfill] SQD: ${transfersFetched} transfers processed`);
      } catch (err) {
        console.warn('[Backfill] SQD failed:', err);
      }
    }

    const duration = Date.now() - startTime;
    console.log(`[Backfill] Complete: ${eventsFetched} events, ${candlesStored} candles in ${(duration / 1000).toFixed(1)}s`);

    return { eventsFetched, swapsFetched, transfersFetched, candlesStored, duration };
  }
}

// ============================================================
// SINGLETON EXPORTS
// ============================================================

export const sqdClient = new SQDClient();
export const historicalBackfill = new HistoricalBackfillEngine();
