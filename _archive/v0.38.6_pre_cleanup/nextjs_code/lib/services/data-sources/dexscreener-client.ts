/**
 * DexScreener Client - Real Liquidity & DEX Data
 * Free API - No key required
 * Provides: liquidity, volume, buy/sell ratios, pair info, price changes
 */

import { unifiedCache, cacheKey } from '../../unified-cache';

const DEXSCREENER_API = 'https://api.dexscreener.com';
const SOURCE = 'dexscreener';

// ============================================================
// TYPES
// ============================================================

export interface DexScreenerPair {
  chainId: string;
  dexId: string;
  pairAddress: string;
  baseToken: { address: string; name: string; symbol: string; };
  quoteToken: { address: string; name: string; symbol: string; };
  priceNative: string;
  priceUsd: string;
  txns: {
    m5: { buys: number; sells: number; };
    h1: { buys: number; sells: number; };
    h6: { buys: number; sells: number; };
    h24: { buys: number; sells: number; };
  };
  volume: { m5: number; h1: number; h6: number; h24: number; };
  priceChange: { m5: number; h1: number; h6: number; h24: number; };
  liquidity: { usd: number; base: number; quote: number; };
  fdv: number;
  marketCap: number;
  pairCreatedAt: number;
  info?: {
    imageUrl: string;
    websites: { label: string; url: string; }[];
    socials: { type: string; url: string; }[];
  };
}

export interface TokenLiquidityData {
  symbol: string;
  name: string;
  chain: string;
  priceUsd: number;
  volume24h: number;
  liquidityUsd: number;
  marketCap: number;
  fdv: number;
  priceChange1h: number;
  priceChange6h: number;
  priceChange24h: number;
  txns24h: { buys: number; sells: number; };
  pairAddress: string;
  dexId: string;
  pairCreatedAt: number;
}

// ============================================================
// CONSTANTS
// ============================================================

const CACHE_TTLS = {
  tokenSearch: 30_000,    // 30s
  pairData: 60_000,       // 1min
  boostedTokens: 120_000, // 2min
  nameSearch: 60_000,     // 1min
};

const INTER_REQUEST_DELAY = 300; // ms between requests

// ============================================================
// CLIENT CLASS
// ============================================================

export class DexScreenerClient {
  private lastRequestTime = 0;

  private async rateLimitedFetch(url: string, retriesLeft = 3): Promise<Response> {
    const elapsed = Date.now() - this.lastRequestTime;
    if (elapsed < INTER_REQUEST_DELAY) {
      await new Promise(r => setTimeout(r, INTER_REQUEST_DELAY - elapsed));
    }
    this.lastRequestTime = Date.now();

    const res = await fetch(url, {
      headers: { 'Accept': 'application/json' },
      signal: AbortSignal.timeout(15000),
    });

    if (res.status === 429) {
      if (retriesLeft <= 1) {
        console.warn('[DexScreener] Rate limited and retries exhausted, returning null');
        return res;
      }
      // Shorter backoff: 5s first retry, max 2 retries total
      const delayMs = retriesLeft <= 2 ? 5000 : 10000;
      console.log(`[DexScreener] Rate limited, retrying in ${delayMs / 1000}s... (${retriesLeft - 1} retries left)`);
      await new Promise(r => setTimeout(r, delayMs));
      return this.rateLimitedFetch(url, retriesLeft - 1);
    }

    return res;
  }

  /**
   * Search for token pairs by token address
   */
  async searchTokenPairs(tokenAddress: string): Promise<DexScreenerPair[]> {
    const key = cacheKey(SOURCE, 'token-search', tokenAddress);
    return unifiedCache.getOrFetch(key, async () => {
      try {
        const res = await this.rateLimitedFetch(`${DEXSCREENER_API}/latest/dex/tokens/${tokenAddress}`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.pairs || [];
      } catch {
        return [];
      }
    }, SOURCE, CACHE_TTLS.tokenSearch);
  }

  /**
   * Batch search for token pairs by multiple addresses.
   * DexScreener supports up to 30 comma-separated addresses in one request.
   * Returns a Map keyed by baseToken address for easy lookup.
   */
  async searchTokenPairsBatch(addresses: string[]): Promise<Map<string, DexScreenerPair[]>> {
    const results = new Map<string, DexScreenerPair[]>();
    const MAX_BATCH = 30; // DexScreener limit

    for (let i = 0; i < addresses.length; i += MAX_BATCH) {
      const batch = addresses.slice(i, i + MAX_BATCH);
      const joinedAddrs = batch.join(',');
      const key = cacheKey(SOURCE, 'token-search-batch', joinedAddrs);

      try {
        const allPairs = await unifiedCache.getOrFetch(key, async () => {
          const res = await this.rateLimitedFetch(
            `${DEXSCREENER_API}/latest/dex/tokens/${joinedAddrs}`
          );
          if (!res.ok) return [];
          const json = await res.json();
          return json.pairs || [];
        }, SOURCE, CACHE_TTLS.tokenSearch);

        // Group pairs by baseToken address
        for (const pair of allPairs) {
          const addr = pair.baseToken?.address;
          if (!addr) continue;
          const existing = results.get(addr) || [];
          existing.push(pair);
          results.set(addr, existing);
        }
      } catch {
        // Batch failed — fall back to individual lookups for this batch
        for (const addr of batch) {
          try {
            const pairs = await this.searchTokenPairs(addr);
            if (pairs.length > 0) results.set(addr, pairs);
          } catch {
            // Skip failed individual lookups
          }
        }
      }
    }
    return results;
  }

  /**
   * Search for token pairs by name/symbol
   */
  async searchTokenByName(query: string): Promise<DexScreenerPair[]> {
    const key = cacheKey(SOURCE, 'name-search', query);
    return unifiedCache.getOrFetch(key, async () => {
      try {
        const res = await this.rateLimitedFetch(
          `${DEXSCREENER_API}/latest/dex/search?q=${encodeURIComponent(query)}`
        );
        if (!res.ok) return [];
        const data = await res.json();
        return data.pairs || [];
      } catch {
        return [];
      }
    }, SOURCE, CACHE_TTLS.nameSearch);
  }

  /**
   * Get latest data for specific pairs by chain + pair address
   */
  async getPairData(chainId: string, pairAddress: string): Promise<DexScreenerPair | null> {
    const key = cacheKey(SOURCE, 'pair-data', `${chainId}:${pairAddress}`);
    return unifiedCache.getOrFetch(key, async () => {
      try {
        const res = await this.rateLimitedFetch(
          `${DEXSCREENER_API}/latest/dex/pairs/${chainId}/${pairAddress}`
        );
        if (!res.ok) return null;
        const data = await res.json();
        return data.pairs?.[0] || null;
      } catch {
        return null;
      }
    }, SOURCE, CACHE_TTLS.pairData);
  }

  /**
   * Get liquidity data for multiple tokens by symbol.
   * Returns the best pair (highest liquidity) for each token.
   * Uses DexScreener batch endpoint (up to 30 addresses per call) for efficiency.
   */
  async getTokensLiquidityData(
    tokens: { symbol: string; name?: string; address?: string; chain?: string; }[]
  ): Promise<Map<string, TokenLiquidityData>> {
    const results = new Map<string, TokenLiquidityData>();

    // Separate tokens with and without contract addresses
    const tokensWithAddress = tokens.filter(t => t.address && t.address !== t.symbol.toLowerCase());
    const tokensWithoutAddress = tokens.filter(t => !t.address || t.address === t.symbol.toLowerCase());

    // Step 1: Batch lookup by addresses (up to 30 per DexScreener call)
    if (tokensWithAddress.length > 0) {
      const addressToSymbol = new Map<string, { symbol: string; chain?: string }>();
      for (const t of tokensWithAddress) {
        addressToSymbol.set(t.address!.toLowerCase(), { symbol: t.symbol, chain: t.chain });
      }

      const batchResults = await this.searchTokenPairsBatch(
        tokensWithAddress.map(t => t.address!)
      );

      // Process batch results — map back to token symbols
      for (const [addr, pairs] of batchResults) {
        const lookup = addressToSymbol.get(addr.toLowerCase());
        if (!lookup || pairs.length === 0) continue;

        let filteredPairs = pairs;
        if (lookup.chain) {
          const chainNorm = this.normalizeChain(lookup.chain);
          const filtered = filteredPairs.filter(p => this.normalizeChain(p.chainId) === chainNorm);
          if (filtered.length > 0) filteredPairs = filtered;
        }

        filteredPairs.sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0));
        const best = filteredPairs[0];
        if (!best) continue;

        results.set(lookup.symbol.toUpperCase(), {
          symbol: best.baseToken.symbol.toUpperCase(),
          name: best.baseToken.name,
          chain: best.chainId,
          priceUsd: parseFloat(best.priceUsd || '0'),
          volume24h: best.volume?.h24 || 0,
          liquidityUsd: best.liquidity?.usd || 0,
          marketCap: best.marketCap || 0,
          fdv: best.fdv || 0,
          priceChange1h: best.priceChange?.h1 || 0,
          priceChange6h: best.priceChange?.h6 || 0,
          priceChange24h: best.priceChange?.h24 || 0,
          txns24h: best.txns?.h24 || { buys: 0, sells: 0 },
          pairAddress: best.pairAddress,
          dexId: best.dexId,
          pairCreatedAt: best.pairCreatedAt || 0,
        });
      }
    }

    // Step 2: Fallback — search by name for tokens without addresses
    if (tokensWithoutAddress.length > 0) {
      const NAME_BATCH_SIZE = 5;
      for (let i = 0; i < tokensWithoutAddress.length; i += NAME_BATCH_SIZE) {
        const batch = tokensWithoutAddress.slice(i, i + NAME_BATCH_SIZE);

        await Promise.all(batch.map(async (token) => {
          try {
            const pairs = await this.searchTokenByName(token.symbol);
            if (pairs.length === 0) return;

            let filteredPairs = pairs;
            if (token.chain) {
              const chainNorm = this.normalizeChain(token.chain);
              const filtered = filteredPairs.filter(p => this.normalizeChain(p.chainId) === chainNorm);
              if (filtered.length > 0) filteredPairs = filtered;
            }

            filteredPairs.sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0));
            const best = filteredPairs[0];
            if (!best) return;

            results.set(token.symbol.toUpperCase(), {
              symbol: best.baseToken.symbol.toUpperCase(),
              name: best.baseToken.name,
              chain: best.chainId,
              priceUsd: parseFloat(best.priceUsd || '0'),
              volume24h: best.volume?.h24 || 0,
              liquidityUsd: best.liquidity?.usd || 0,
              marketCap: best.marketCap || 0,
              fdv: best.fdv || 0,
              priceChange1h: best.priceChange?.h1 || 0,
              priceChange6h: best.priceChange?.h6 || 0,
              priceChange24h: best.priceChange?.h24 || 0,
              txns24h: best.txns?.h24 || { buys: 0, sells: 0 },
              pairAddress: best.pairAddress,
              dexId: best.dexId,
              pairCreatedAt: best.pairCreatedAt || 0,
            });
          } catch (err) {
            console.error(`[DexScreener] Error fetching ${token.symbol}:`, err);
          }
        }));

        if (i + NAME_BATCH_SIZE < tokensWithoutAddress.length) {
          await new Promise(r => setTimeout(r, 500));
        }
      }
    }

    console.log(`[DexScreener] Got liquidity data for ${results.size}/${tokens.length} tokens (batch optimized)`);
    return results;
  }

  /**
   * Fetch top token pairs for a specific chain
   * Uses the search endpoint with chain-specific queries
   */
  async getTopChainTokens(chain: string, limit: number = 50): Promise<DexScreenerPair[]> {
    try {
      // DexScreener doesn't have a "top tokens" endpoint per chain,
      // so we search for popular terms on the chain
      const chainNorm = this.normalizeChain(chain);
      const searchTerms = this.getChainSearchTerms(chainNorm);

      const allPairs: DexScreenerPair[] = [];
      for (const term of searchTerms.slice(0, 3)) {
        const pairs = await this.searchTokenByName(term);
        const chainPairs = pairs.filter(p => this.normalizeChain(p.chainId) === chainNorm);
        allPairs.push(...chainPairs);
      }

      // Deduplicate by pair address
      const seen = new Set<string>();
      const unique = allPairs.filter(p => {
        if (seen.has(p.pairAddress)) return false;
        seen.add(p.pairAddress);
        return true;
      });

      // Sort by volume
      unique.sort((a, b) => (b.volume?.h24 || 0) - (a.volume?.h24 || 0));

      return unique.slice(0, limit);
    } catch {
      return [];
    }
  }

  /**
   * Get boosted/trending tokens from DexScreener
   */
  async getBoostedTokens(): Promise<any[]> {
    const key = cacheKey(SOURCE, 'boosted', 'latest');
    return unifiedCache.getOrFetch(key, async () => {
      try {
        const res = await this.rateLimitedFetch(`${DEXSCREENER_API}/token-boosts/latest/v1`);
        if (!res.ok) return [];
        return await res.json();
      } catch {
        return [];
      }
    }, SOURCE, CACHE_TTLS.boostedTokens);
  }

  /**
   * Normalize chain names across different APIs
   */
  normalizeChain(chain: string): string {
    const map: Record<string, string> = {
      'solana': 'solana', 'sol': 'solana',
      'ethereum': 'ethereum', 'eth': 'ethereum',
      'bsc': 'bsc', 'binance': 'bsc', 'binance-smart-chain': 'bsc',
      'polygon': 'polygon', 'matic': 'polygon', 'polygon-pos': 'polygon',
      'arbitrum': 'arbitrum', 'arbitrum-one': 'arbitrum',
      'optimism': 'optimism', 'optimistic-ethereum': 'optimism',
      'avalanche': 'avalanche', 'avax': 'avalanche',
      'base': 'base',
      'fantom': 'fantom', 'ftm': 'fantom',
    };
    return map[chain.toLowerCase()] || chain.toLowerCase();
  }

  /**
   * Get search terms for a specific chain to find popular tokens
   */
  private getChainSearchTerms(chain: string): string[] {
    const terms: Record<string, string[]> = {
      'solana': ['SOL', 'USDC', 'JUP', 'BONK', 'WIF', 'RAY', 'ORCA', 'JTO', 'PYTH', 'MEME'],
      'ethereum': ['ETH', 'USDT', 'UNI', 'LINK', 'AAVE', 'PEPE', 'SHIB', 'MKR', 'COMP'],
      'bsc': ['BNB', 'CAKE', 'BUSD', 'BABYDOGE', 'SAFEMOON'],
      'base': ['ETH', 'USDC', 'AERO', 'VELODROME', 'BRETT'],
      'arbitrum': ['ARB', 'GMX', 'RDNT', 'PENDLE', 'MAGIC'],
      'polygon': ['MATIC', 'QUICK', 'SUSHI', 'AAVE'],
    };
    return terms[chain] || ['USDC', 'ETH', 'BTC'];
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const dexScreenerClient = new DexScreenerClient();
