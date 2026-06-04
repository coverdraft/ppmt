/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  UNIVERSAL DATA EXTRACTOR — CryptoQuant Terminal Brain Core             ║
 * ║  THE BOSS: Single source of truth for ALL data extraction               ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 *
 * Architecture:
 *   UniversalDataExtractor (THE BOSS)
 *   ├── RealtimeScanner   — DexScreener + DeFi Llama + CoinGecko trending
 *   ├── WalletProfiler    — Moralis + Helius + Etherscan for wallet history
 *   ├── OHLCVBackfiller   — CoinGecko OHLCV + DexScreener snapshots + CryptoDataDownload CSV
 *   ├── ProtocolAnalyzer  — DeFi Llama TVL/yields/fees
 *   └── ExtractionScheduler — manages jobs, rate limits, resumability
 *
 * Data Sources (11 active — 7 original + 3 BigQuery replacements + 1 multi-chain):
 *   1. Moralis          🥇 — Wallet TX history EVM+Solana, 10M req/mes, 25 RPS
 *   2. Helius           🥈 — Solana wallet detail, 1M credits/mes, 10 RPS
 *   3. CoinGecko        🥉 — OHLCV + metadata, 10K credits/mes, 30 RPM
 *   4. DexScreener      — New tokens/pairs, 300 req/min, NO auth
 *   5. DeFi Llama       — Protocol analytics, NO auth, NO limits
 *   6. Etherscan V2     — EVM TX backup, 100K calls/day, 3/s
 *   7. CryptoDataDownload — Bulk CSV OHLCV, unlimited
 *   8. SQD (Subsquid)   🏆 — BigQuery replacement, 225+ chains, NO limits, FREE
 *   9. Dune Analytics   🔮 — Decoded blockchain data, wallet labels, top traders
 *  10. Footprint Analytics 📊 — Structured analytics, OHLCV, protocol & chain data
 *  11. DexPaprika       🌐 — 35 chains, 26M+ tokens, FREE, no API key, pool swaps + buy/sell ratios
 *
 * 6-Phase Pipeline: SCAN → ENRICH → BACKFILL_OHLCV → EXTRACT_TRADERS → PROFILE_WALLETS → UPDATE_PROTOCOLS
 */

import { db } from '../db';
import { SQDClient, HistoricalBackfillEngine } from './sqd-client';
import { DuneClient } from './dune-client';
import { FootprintClient } from './footprint-client';
import { RateLimiter } from './rate-limiter';
import { UnifiedCache } from './source-cache';

/** @deprecated Use UnifiedCache instead. Kept for backward compatibility. */
export { UnifiedCache as ExtractorCache } from './source-cache';

// ============================================================
// TYPES & INTERFACES
// ============================================================

export interface ExtractorConfig {
  moralisApiKey?: string;
  heliusApiKey?: string;
  coingeckoApiKey?: string;
  etherscanApiKey?: string;
  sqdApiKey?: string;
  sqdGatewayUrl?: string;
  duneApiKey?: string;
  footprintApiKey?: string;
  dexPaprikaApiKey?: string; // DexPaprika doesn't need a key, but kept for consistency
  interRequestDelay: number;
  maxRetries: number;
  batchSize: number;
  enableCaching: boolean;
  cacheTtlMinutes: number;
  maxTokensPerDiscovery: number;
  maxWalletsPerCycle: number;
  ohlcvTimeframes: string[];
  ohlcvHistoryDays: number;
  chains: string[];
}

export const DEFAULT_EXTRACTOR_CONFIG: ExtractorConfig = {
  moralisApiKey: process.env.MORALIS_API_KEY,
  heliusApiKey: process.env.HELIUS_API_KEY,
  coingeckoApiKey: process.env.COINGECKO_API_KEY,
  etherscanApiKey: process.env.ETHERSCAN_API_KEY,
  sqdApiKey: process.env.SQD_API_KEY,
  sqdGatewayUrl: process.env.SQD_GATEWAY_URL,
  duneApiKey: process.env.DUNE_API_KEY,
  footprintApiKey: process.env.FOOTPRINT_API_KEY,
  dexPaprikaApiKey: '', // DexPaprika is FREE, no API key needed
  interRequestDelay: 150,
  maxRetries: 3,
  batchSize: 5,
  enableCaching: true,
  cacheTtlMinutes: 15,
  maxTokensPerDiscovery: 200,
  maxWalletsPerCycle: 100,
  ohlcvTimeframes: ['1h', '4h', '1d'],
  ohlcvHistoryDays: 365,
  chains: ['solana', 'ethereum', 'base', 'arbitrum', 'bsc'],
};

export interface ExtractionPhaseResult {
  phase: ExtractionPhase;
  success: boolean;
  duration: number;
  recordsProcessed: number;
  recordsStored: number;
  errors: string[];
  metadata: Record<string, unknown>;
}

export type ExtractionPhase =
  | 'SCAN'
  | 'ENRICH'
  | 'BACKFILL_OHLCV'
  | 'EXTRACT_TRADERS'
  | 'PROFILE_WALLETS'
  | 'UPDATE_PROTOCOLS';

export interface RealtimeScanResult {
  tokensDiscovered: number;
  tokensUpdated: number;
  trendingCoins: CoinGeckoTrendingItem[];
  dexPairs: DexScreenerPair[];
  protocolUpdates: number;
  timestamp: number;
}

export interface OHLCVBackfillResult {
  tokenAddress: string;
  chain: string;
  timeframes: { timeframe: string; candlesFetched: number; candlesStored: number }[];
  totalStored: number;
  duration: number;
}

export interface WalletProfileResult {
  address: string;
  chain: string;
  transactionsFound: number;
  transactionsStored: number;
  tokenBalances: number;
  netWorth: number;
  duration: number;
}

export interface ProtocolData {
  id: string;
  name: string;
  chain: string;
  category: string;
  tvl: number;
  tvlChange1d: number;
  tvlChange7d: number;
  mcap: number | null;
  fdv: number | null;
  chains: string[];
  geckoId: string | null;
  url: string | null;
  logo: string | null;
  description: string | null;
}

export interface YieldData {
  symbol: string;
  tvlUsd: number;
  apyBase: number | null;
  apyReward: number | null;
  apy: number | null;
  project: string;
  chain: string;
  stablecoin: boolean;
  ilRisk: string;
  poolMeta: string | null;
}

// ============================================================
// CACHE — Now using UnifiedCache from source-cache.ts
// ============================================================

// ============================================================
// UTILITY
// ============================================================

function normalizeChain(chain: string): string {
  const lower = chain.toLowerCase();
  if (lower === 'solana' || lower === 'sol') return 'SOL';
  if (lower === 'ethereum' || lower === 'eth') return 'ETH';
  if (lower === 'base') return 'BASE';
  if (lower === 'arbitrum' || lower === 'arb') return 'ARB';
  if (lower === 'optimism' || lower === 'op') return 'OP';
  if (lower === 'bsc' || lower === 'binance' || lower === 'bnb') return 'BSC';
  if (lower === 'polygon' || lower === 'matic') return 'MATIC';
  return chain.toUpperCase();
}

function toMoralisChain(chain: string): string {
  const map: Record<string, string> = {
    'SOL': 'solana', 'ETH': 'eth', 'BASE': 'base',
    'ARB': 'arbitrum', 'OP': 'optimism', 'BSC': 'bsc', 'MATIC': 'polygon',
  };
  return map[chain] || 'eth';
}

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ============================================================
// MORALIS CLIENT — 🥇 Wallet History King
// 10M requests/month FREE, 25 RPS, 60+ chains
// ============================================================

export class MoralisClient {
  private apiKey: string;
  private baseUrl = 'https://deep-index.moralis.io/api/v2.2';
  private limiter: RateLimiter;
  private cache: UnifiedCache;

  constructor(apiKey: string, cache: UnifiedCache) {
    this.apiKey = apiKey;
    this.limiter = new RateLimiter(25, 10);
    this.cache = cache;
  }

  get isConfigured(): boolean {
    return !!this.apiKey;
  }

  private async fetch<T>(endpoint: string, params: Record<string, string> = {}): Promise<T | null> {
    if (!this.apiKey) return null;

    const cacheKey = `moralis:${endpoint}:${JSON.stringify(params)}`;
    const cached = this.cache.get<T>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();
    try {
      const url = new URL(`${this.baseUrl}${endpoint}`);
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));

      const res = await fetch(url.toString(), {
        headers: { 'X-API-Key': this.apiKey, 'Accept': 'application/json' },
      });

      if (res.status === 429) {
        console.warn('[Moralis] Rate limited — backing off 5s');
        await delay(5000);
        return this.fetch(endpoint, params);
      }
      if (!res.ok) {
        console.error(`[Moralis] API error: ${res.status} ${res.statusText}`);
        return null;
      }

      const data = await res.json();
      this.cache.set(cacheKey, data);
      return data as T;
    } catch (err) {
      console.error('[Moralis] Fetch error:', err);
      return null;
    }
  }

  /** Decoded wallet transaction history (EVM chains) */
  async getWalletTransactions(
    address: string, chain: string = 'eth', cursor?: string, limit: number = 200
  ): Promise<{ result: MoralisTx[]; cursor?: string; total?: number } | null> {
    const params: Record<string, string> = {
      chain, limit: String(limit), include_internal_transactions: 'false',
    };
    if (cursor) params.cursor = cursor;
    return this.fetch(`/wallets/${address}/history`, params);
  }

  /** Full paginated wallet history */
  async getFullWalletHistory(
    address: string, chain: string = 'eth', maxPages: number = 50
  ): Promise<MoralisTx[]> {
    const allTx: MoralisTx[] = [];
    let cursor: string | undefined;
    let pages = 0;

    while (pages < maxPages) {
      const result = await this.getWalletTransactions(address, chain, cursor, 200);
      if (!result?.result?.length) break;
      allTx.push(...result.result);
      cursor = result.cursor;
      pages++;
      if (!cursor) break;
    }
    return allTx;
  }

  /** Token balances for a wallet */
  async getWalletTokenBalances(
    address: string, chain: string = 'eth'
  ): Promise<MoralisTokenBalance[] | null> {
    return this.fetch(`/wallets/${address}/tokens`, { chain });
  }

  /** Wallet net worth across chains */
  async getWalletNetWorth(
    address: string, chains: string[] = ['eth', 'solana']
  ): Promise<{ total_networth_usd: string; chains: Array<{ chain: string; networth_usd: string }> } | null> {
    return this.fetch(`/wallets/${address}/net-worth`, { chains: chains.join(',') });
  }

  /** Token price */
  async getTokenPrice(
    address: string, chain: string = 'eth'
  ): Promise<{ usdPrice: number; nativePrice: number; exchangeName: string; exchangeAddress: string } | null> {
    return this.fetch(`/erc20/${address}/price`, { chain, include: 'percent_change' });
  }

  /** Token metadata */
  async getTokenMetadata(
    addresses: string[], chain: string = 'eth'
  ): Promise<MoralisTokenMeta[] | null> {
    return this.fetch(`/erc20/metadata`, { addresses: addresses.join(','), chain });
  }

  /** Native balance */
  async getNativeBalance(
    address: string, chain: string = 'eth'
  ): Promise<{ balance: string; balance_formatted: string } | null> {
    return this.fetch(`/wallets/${address}/balance`, { chain });
  }

  /** ERC-20 token transfers */
  async getTokenTransfers(
    address: string, chain: string = 'eth', limit: number = 100
  ): Promise<{ result: MoralisErc20Transfer[] } | null> {
    return this.fetch(`/wallets/${address}/erc20`, { chain, limit: String(limit) });
  }

  /** Solana wallet transactions */
  async getSolanaWalletTransactions(
    address: string, limit: number = 100
  ): Promise<{ result: MoralisTx[] } | null> {
    return this.fetch(`/wallets/${address}/history`, { chain: 'solana', limit: String(limit) });
  }
}

// Moralis types
export interface MoralisTx {
  hash: string;
  nonce: string;
  from_address: string;
  to_address: string;
  value: string;
  gas?: string;
  gas_price?: string;
  block_timestamp: string;
  block_number: string;
  block_hash: string;
  transaction_index: number;
  chain: string;
  logs?: Array<{
    log_index: number;
    transaction_hash: string;
    address: string;
    topic0: string;
    topic1?: string;
    topic2?: string;
    topic3?: string;
    data: string;
  }>;
  erc20_transfers?: Array<{
    token_name: string;
    token_symbol: string;
    token_logo?: string;
    token_decimals: string;
    from_address: string;
    to_address: string;
    value: string;
    address: string;
    possible_spam: boolean;
  }>;
  native_transfers?: Array<{
    from_address: string;
    to_address: string;
    value: string;
  }>;
  summary?: string;
  category: string;
  possible_spam: boolean;
}

export interface MoralisTokenBalance {
  token_address: string;
  symbol: string;
  name: string;
  logo?: string;
  thumbnail?: string;
  decimals: number;
  balance: string;
  possible_spam: boolean;
  usd_price?: number;
  usd_value?: number;
}

export interface MoralisTokenMeta {
  address: string;
  name: string;
  symbol: string;
  decimals: string;
  logo?: string;
  thumbnail?: string;
  possible_spam: boolean;
  total_supply: string;
  total_supply_formatted: string;
  fully_diluted_valuation?: string;
  market_cap?: string;
}

export interface MoralisErc20Transfer {
  token_address: string;
  token_name: string;
  token_symbol: string;
  token_decimals: string;
  from_address: string;
  to_address: string;
  value: string;
  value_formatted: string;
  transaction_hash: string;
  block_number: string;
  block_timestamp: string;
  possible_spam: boolean;
}

// ============================================================
// HELIUS CLIENT — 🥈 Solana Wallet Intelligence
// 1M credits/month FREE, 10 RPS
// ============================================================

export class HeliusClient {
  private apiKey: string;
  private baseUrl = 'https://api.helius.xyz/v0';
  private limiter: RateLimiter;
  private cache: UnifiedCache;

  constructor(apiKey: string, cache: UnifiedCache) {
    this.apiKey = apiKey;
    this.limiter = new RateLimiter(10, 5);
    this.cache = cache;
  }

  get isConfigured(): boolean {
    return !!this.apiKey;
  }

  private async fetchApi<T>(endpoint: string, params: Record<string, string> = {}): Promise<T | null> {
    if (!this.apiKey) return null;

    const cacheKey = `helius:${endpoint}:${JSON.stringify(params)}`;
    const cached = this.cache.get<T>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();
    try {
      const url = new URL(`${this.baseUrl}${endpoint}`);
      url.searchParams.set('api-key', this.apiKey);
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));

      const res = await fetch(url.toString());
      if (res.status === 429) {
        await delay(3000);
        return this.fetchApi(endpoint, params);
      }
      if (!res.ok) return null;

      const data = await res.json();
      this.cache.set(cacheKey, data);
      return data as T;
    } catch {
      return null;
    }
  }

  private async postApi<T>(endpoint: string, body: unknown): Promise<T | null> {
    if (!this.apiKey) return null;

    await this.limiter.acquire();
    try {
      const url = `${this.baseUrl}${endpoint}?api-key=${this.apiKey}`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.status === 429) {
        await delay(3000);
        return this.postApi(endpoint, body);
      }
      if (!res.ok) return null;
      return await res.json() as T;
    } catch {
      return null;
    }
  }

  /** Parsed transactions for a Solana wallet */
  async getWalletTransactions(
    address: string, limit: number = 100, before?: string
  ): Promise<HeliusParsedTx[]> {
    const body: Record<string, unknown> = {
      transactions: [address],
      options: { limit },
    };
    if (before) body.options = { limit, before };

    const result = await this.postApi<{ result: HeliusParsedTx[] }>(
      '/addresses/transactions', body
    );
    return result?.result || [];
  }

  /** Full wallet history with pagination */
  async getFullWalletHistory(address: string, maxPages: number = 20): Promise<HeliusParsedTx[]> {
    const allTx: HeliusParsedTx[] = [];
    let before: string | undefined;
    let pages = 0;

    while (pages < maxPages) {
      const txs = await this.getWalletTransactions(address, 100, before);
      if (!txs.length) break;
      allTx.push(...txs);
      before = txs[txs.length - 1]?.signature;
      pages++;
    }
    return allTx;
  }

  /** Enhanced transaction details */
  async getEnhancedTransaction(signature: string): Promise<HeliusParsedTx | null> {
    return this.fetchApi<HeliusParsedTx>(`/transactions/${signature}`);
  }

  /** DAS assets for a wallet */
  async getWalletAssets(address: string): Promise<unknown> {
    return this.postApi('/addresses/assets', { address });
  }

  /** Token metadata from Solana */
  async getTokenMetadata(mintAddresses: string[]): Promise<unknown> {
    return this.postApi('/token-metadata', { mintAccounts: mintAddresses });
  }
}

export interface HeliusParsedTx {
  signature: string;
  timestamp: number;
  type: string;
  source: string;
  fee: number;
  feePayer: string;
  description: string;
  nativeTransfers: Array<{
    fromUserAccount: string;
    toUserAccount: string;
    amount: number;
  }>;
  tokenTransfers: Array<{
    fromUserAccount: string;
    toUserAccount: string;
    tokenAmount: number;
    mint: string;
  }>;
  innerInstructions?: unknown[];
}

// ============================================================
// COINGECKO CLIENT — 🥉 OHLCV + Metadata
// 10K credits/month free, 30 RPM
// ============================================================

export class CoinGeckoClient {
  private apiKey: string | undefined;
  private baseUrl = 'https://api.coingecko.com/api/v3';
  private proUrl = 'https://pro-api.coingecko.com/api/v3';
  private limiter: RateLimiter;
  private cache: UnifiedCache;

  constructor(apiKey: string | undefined = undefined, cache: UnifiedCache) {
    this.apiKey = apiKey;
    this.limiter = new RateLimiter(apiKey ? 30 : 10, 5);
    this.cache = cache;
  }

  get isConfigured(): boolean {
    return true; // Works without key (free tier)
  }

  private get effectiveBaseUrl(): string {
    return this.apiKey ? this.proUrl : this.baseUrl;
  }

  private async fetch<T>(endpoint: string, params: Record<string, string> = {}): Promise<T | null> {
    const cacheKey = `coingecko:${endpoint}:${JSON.stringify(params)}`;
    const cached = this.cache.get<T>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();
    try {
      const url = new URL(`${this.effectiveBaseUrl}${endpoint}`);
      if (this.apiKey) url.searchParams.set('x_cg_pro_api_key', this.apiKey);
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));

      const headers: Record<string, string> = { 'Accept': 'application/json' };
      if (this.apiKey) headers['x-cg-pro-api-key'] = this.apiKey;

      const res = await fetch(url.toString(), { headers });
      if (res.status === 429) {
        console.warn('[CoinGecko] Rate limited — waiting 60s');
        await delay(60000);
        return this.fetch(endpoint, params);
      }
      if (!res.ok) return null;

      const data = await res.json();
      this.cache.set(cacheKey, data);
      return data as T;
    } catch {
      return null;
    }
  }

  /** OHLCV candles up to 1 year */
  async getOHLCV(
    coinId: string, vsCurrency: string = 'usd', days: number = 365
  ): Promise<Array<[number, number, number, number, number]>> {
    const result = await this.fetch<Array<[number, number, number, number, number]>>(
      `/coins/${coinId}/ohlc`, { vs_currency: vsCurrency, days: String(days) }
    );
    return result || [];
  }

  /** Coin market data (price, mcap, volume) */
  async getCoinMarketData(coinId: string, vsCurrency: string = 'usd'): Promise<CoinGeckoMarketData | null> {
    return this.fetch(`/coins/${coinId}`, {
      localization: 'false', tickers: 'false', market_data: 'true',
      community_data: 'false', developer_data: 'false', sparkline: 'false',
    });
  }

  /** Trending coins */
  async getTrending(): Promise<CoinGeckoTrendingItem[]> {
    const result = await this.fetch<CoinGeckoTrendingResponse>('/search/trending');
    return result?.coins?.map(c => c.item) || [];
  }

  /** Top coins by market cap */
  async getTopCoins(
    vsCurrency: string = 'usd', perPage: number = 100, page: number = 1
  ): Promise<CoinGeckoCoinMarket[]> {
    const result = await this.fetch<CoinGeckoCoinMarket[]>('/coins/markets', {
      vs_currency: vsCurrency, order: 'market_cap_desc',
      per_page: String(perPage), page: String(page), sparkline: 'false',
    });
    return result || [];
  }

  /** Market chart (price + volume + mcap over time) */
  async getMarketChart(
    coinId: string, vsCurrency: string = 'usd', days: number = 365
  ): Promise<{ prices: number[][]; total_volumes: number[][]; market_caps: number[][] } | null> {
    return this.fetch(`/coins/${coinId}/market_chart`, {
      vs_currency: vsCurrency, days: String(days),
    });
  }

  /** New coins listed */
  async getNewCoins(): Promise<unknown> {
    return this.fetch('/coins/list/new');
  }

  /** Search coins */
  async searchCoins(query: string): Promise<unknown> {
    return this.fetch('/search', { query });
  }

  /** Coin list (all coins with IDs) */
  async getCoinList(): Promise<Array<{ id: string; symbol: string; name: string }>> {
    const result = await this.fetch<Array<{ id: string; symbol: string; name: string }>>('/coins/list');
    return result || [];
  }
}

export interface CoinGeckoTrendingResponse {
  coins: Array<{ item: CoinGeckoTrendingItem }>;
}

export interface CoinGeckoTrendingItem {
  id: string;
  coin_id: number;
  name: string;
  symbol: string;
  market_cap_rank: number;
  thumb: string;
  small: string;
  large: string;
  slug: string;
  price_btc: number;
  score: number;
  data?: {
    price: number;
    price_change_percentage_24h: Record<string, number>;
    market_cap: string;
    total_volume: string;
    sparkline: string;
  };
}

export interface CoinGeckoMarketData {
  id: string;
  symbol: string;
  name: string;
  market_data: {
    current_price: Record<string, number>;
    market_cap: Record<string, number>;
    total_volume: Record<string, number>;
    price_change_percentage_24h: number;
    price_change_percentage_7d: number;
    high_24h: Record<string, number>;
    low_24h: Record<string, number>;
    ath: Record<string, number>;
    atl: Record<string, number>;
    circulating_supply: number;
    total_supply: number | null;
    fully_diluted_valuation: Record<string, number>;
  };
  image: { thumb: string; small: string; large: string };
  categories: string[];
}

export interface CoinGeckoCoinMarket {
  id: string;
  symbol: string;
  name: string;
  image: string;
  current_price: number;
  market_cap: number;
  market_cap_rank: number;
  total_volume: number;
  price_change_percentage_24h: number;
  price_change_percentage_7d_in_currency: number;
  circulating_supply: number;
  total_supply: number;
  ath: number;
  ath_change_percentage: number;
  ath_date: string;
  atl: number;
  atl_change_percentage: number;
  atl_date: string;
  last_updated: string;
  sparkline_in_7d?: { price: number[] };
}

// ============================================================
// DEXSCREENER CLIENT — New tokens/pairs
// 300 req/min, NO auth
// ============================================================

export class DexScreenerClient {
  private baseUrl = 'https://api.dexscreener.com';
  private limiter: RateLimiter;
  private cache: UnifiedCache;

  constructor(cache: UnifiedCache) {
    this.limiter = new RateLimiter(5, 10); // 300/min = 5/sec
    this.cache = cache;
  }

  get isConfigured(): boolean {
    return true; // No auth required
  }

  private async fetch<T>(endpoint: string, params: Record<string, string> = {}): Promise<T | null> {
    const cacheKey = `dexscreener:${endpoint}:${JSON.stringify(params)}`;
    const cached = this.cache.get<T>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();
    try {
      const url = new URL(`${this.baseUrl}${endpoint}`);
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));

      const res = await fetch(url.toString());
      if (!res.ok) return null;

      const data = await res.json();
      this.cache.set(cacheKey, data);
      return data as T;
    } catch {
      return null;
    }
  }

  /** Search tokens by query */
  async searchTokens(query: string): Promise<DexScreenerPair[]> {
    const result = await this.fetch<{ pairs: DexScreenerPair[] }>(
      '/latest/dex/search', { q: query }
    );
    return result?.pairs || [];
  }

  /** Get token pairs by address */
  async getTokenPairs(address: string): Promise<DexScreenerPair[]> {
    const result = await this.fetch<{ pairs: DexScreenerPair[] }>(
      `/latest/dex/tokens/${address}`
    );
    return result?.pairs || [];
  }

  /** Get specific pair data */
  async getPair(chainId: string, pairAddress: string): Promise<DexScreenerPair | null> {
    const result = await this.fetch<{ pairs: DexScreenerPair[] }>(
      `/latest/dex/pairs/${chainId}/${pairAddress}`
    );
    return result?.pairs?.[0] || null;
  }

  /** Get latest token profiles (boosted) */
  async getLatestTokenProfiles(): Promise<DexScreenerPair[]> {
    const result = await this.fetch<{ pairs: DexScreenerPair[] }>(
      '/latest/dex/search', { q: 'trending' }
    );
    return result?.pairs || [];
  }

  /** Search for tokens across multiple queries */
  async batchSearch(queries: string[]): Promise<DexScreenerPair[]> {
    const allPairs: DexScreenerPair[] = [];
    const seen = new Set<string>();

    for (const q of queries) {
      const pairs = await this.searchTokens(q);
      for (const p of pairs) {
        const key = `${p.chainId}:${p.pairAddress}`;
        if (!seen.has(key)) {
          seen.add(key);
          allPairs.push(p);
        }
      }
      await delay(200);
    }
    return allPairs;
  }
}

export interface DexScreenerPair {
  chainId: string;
  dexId: string;
  pairAddress: string;
  baseToken: { address: string; symbol: string; name: string };
  quoteToken: { address: string; symbol: string; name: string };
  priceNative: string;
  priceUsd: string;
  priceChange?: { h24: number; h6: number; h1: number; m5: number; m15?: number };
  txns?: {
    h24: { buys: number; sells: number };
    h6: { buys: number; sells: number };
    h1: { buys: number; sells: number };
    m5?: { buys: number; sells: number };
  };
  volume?: { h24: number; h6: number; h1: number; m5?: number };
  liquidity?: { usd: number; base: number; quote: number };
  fdv: number;
  marketCap: number;
  pairCreatedAt: number;
  info?: {
    imageUrl: string;
    websites: { url: string }[];
    socials: { type: string; url: string }[];
  };
}

// ============================================================
// DEFI LLAMA CLIENT — Protocol Analytics
// NO auth, NO rate limits
// ============================================================

export class DefiLlamaClient {
  private baseUrl = 'https://api.llama.fi';
  private limiter: RateLimiter;
  private cache: UnifiedCache;

  constructor(cache: UnifiedCache) {
    this.limiter = new RateLimiter(5, 10);
    this.cache = cache;
  }

  get isConfigured(): boolean {
    return true;
  }

  private async fetch<T>(endpoint: string): Promise<T | null> {
    const cacheKey = `defillama:${endpoint}`;
    const cached = this.cache.get<T>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();
    try {
      const res = await fetch(`${this.baseUrl}${endpoint}`);
      if (!res.ok) return null;
      const data = await res.json();
      this.cache.set(cacheKey, data);
      return data as T;
    } catch {
      return null;
    }
  }

  /** All protocols with TVL */
  async getProtocols(): Promise<ProtocolData[]> {
    const result = await this.fetch<Array<{
      id: string; name: string; address: string; symbol: string; url: string;
      description: string; chain: string; logo: string; category: string;
      chains: string[]; tvl: number; chainTvls: Record<string, number>;
      change_1h: number; change_1d: number; change_7d: number;
      mcap: number | null; fdv: number | null; gecko_id: string | null;
    }>>('/protocols');

    return (result || []).map(p => ({
      id: p.id,
      name: p.name,
      chain: p.chain,
      category: p.category,
      tvl: p.tvl,
      tvlChange1d: p.change_1d ?? 0,
      tvlChange7d: p.change_7d ?? 0,
      mcap: p.mcap,
      fdv: p.fdv,
      chains: p.chains,
      geckoId: p.gecko_id ?? null,
      url: p.url ?? null,
      logo: p.logo ?? null,
      description: p.description ?? null,
    }));
  }

  /** Chain TVLs */
  async getChains(): Promise<Array<{ name: string; gecko_id: string; tvl: number; tokenSymbol: string }>> {
    const result = await this.fetch<Array<{ name: string; gecko_id: string; tvl: number; tokenSymbol: string }>>('/v2/chains');
    return result || [];
  }

  /** Yield pools */
  async getYields(): Promise<YieldData[]> {
    const result = await this.fetch<Array<{
      symbol: string; tvlUsd: number; apyBase: number | null; apyReward: number | null;
      apy: number | null; project: string; chain: string; stablecoin: boolean;
      ilRisk: string; poolMeta: string | null;
    }>>('/yields');

    return (result || []).map(y => ({
      symbol: y.symbol,
      tvlUsd: y.tvlUsd,
      apyBase: y.apyBase,
      apyReward: y.apyReward,
      apy: y.apy,
      project: y.project,
      chain: y.chain,
      stablecoin: y.stablecoin,
      ilRisk: y.ilRisk,
      poolMeta: y.poolMeta ?? null,
    }));
  }

  /** DEX volumes */
  async getDexVolumes(): Promise<unknown> {
    return this.fetch('/overview/dexs');
  }

  /** Fees and revenue */
  async getFees(): Promise<unknown> {
    return this.fetch('/overview/fees');
  }
}

// ============================================================
// ETHERSCAN V2 CLIENT — EVM TX Backup
// 100K calls/day free, 3/s
// ============================================================

export class EtherscanV2Client {
  private apiKey: string;
  private baseUrl = 'https://api.etherscan.io/v2/api';
  private limiter: RateLimiter;
  private cache: UnifiedCache;

  constructor(apiKey: string, cache: UnifiedCache) {
    this.apiKey = apiKey;
    this.limiter = new RateLimiter(3, 2);
    this.cache = cache;
  }

  get isConfigured(): boolean {
    return !!this.apiKey;
  }

  private async fetch<T>(params: Record<string, string>): Promise<T | null> {
    if (!this.apiKey) return null;

    const cacheKey = `etherscan:${JSON.stringify(params)}`;
    const cached = this.cache.get<T>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();
    try {
      const url = new URL(this.baseUrl);
      url.searchParams.set('apikey', this.apiKey);
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));

      const res = await fetch(url.toString());
      if (!res.ok) return null;

      const data = await res.json();
      if (data.status === '0' && data.message !== 'No transactions found') {
        console.warn(`[Etherscan] API error: ${data.message}`);
        return null;
      }

      this.cache.set(cacheKey, data);
      return data as T;
    } catch {
      return null;
    }
  }

  /** Normal transactions */
  async getTransactions(
    address: string, chainId: string = '1',
    startBlock: number = 0, endBlock: number = 99999999,
    page: number = 1, offset: number = 100
  ): Promise<EtherscanTx[]> {
    const result = await this.fetch<{ result: EtherscanTx[] }>({
      module: 'account', action: 'txlist', address, chainid: chainId,
      startBlock: String(startBlock), endBlock: String(endBlock),
      page: String(page), offset: String(offset), sort: 'desc',
    });
    return result?.result || [];
  }

  /** ERC-20 token transfers */
  async getTokenTransfers(
    address: string, chainId: string = '1',
    startBlock: number = 0, endBlock: number = 99999999,
    page: number = 1, offset: number = 100
  ): Promise<EtherscanTokenTx[]> {
    const result = await this.fetch<{ result: EtherscanTokenTx[] }>({
      module: 'account', action: 'tokentx', address, chainid: chainId,
      startBlock: String(startBlock), endBlock: String(endBlock),
      page: String(page), offset: String(offset), sort: 'desc',
    });
    return result?.result || [];
  }

  /** Internal transactions */
  async getInternalTransactions(
    address: string, chainId: string = '1',
    startBlock: number = 0, endBlock: number = 99999999
  ): Promise<EtherscanInternalTx[]> {
    const result = await this.fetch<{ result: EtherscanInternalTx[] }>({
      module: 'account', action: 'txlistinternal', address, chainid: chainId,
      startBlock: String(startBlock), endBlock: String(endBlock), sort: 'desc',
    });
    return result?.result || [];
  }

  /** Full paginated wallet history */
  async getFullWalletHistory(
    address: string, chainId: string = '1', maxPages: number = 10
  ): Promise<EtherscanTx[]> {
    const allTx: EtherscanTx[] = [];
    for (let page = 1; page <= maxPages; page++) {
      const txs = await this.getTransactions(address, chainId, 0, 99999999, page, 1000);
      if (!txs.length) break;
      allTx.push(...txs);
      if (txs.length < 1000) break;
    }
    return allTx;
  }
}

export interface EtherscanTx {
  blockNumber: string;
  timeStamp: string;
  hash: string;
  nonce: string;
  blockHash: string;
  transactionIndex: string;
  from: string;
  to: string;
  value: string;
  gas: string;
  gasPrice: string;
  isError: string;
  txreceipt_status: string;
  input: string;
  contractAddress: string;
  cumulativeGasUsed: string;
  gasUsed: string;
  confirmations: string;
  functionName: string;
}

export interface EtherscanTokenTx {
  blockNumber: string;
  timeStamp: string;
  hash: string;
  nonce: string;
  blockHash: string;
  from: string;
  contractAddress: string;
  to: string;
  value: string;
  tokenName: string;
  tokenSymbol: string;
  tokenDecimal: string;
  transactionIndex: string;
  gas: string;
  gasPrice: string;
  gasUsed: string;
  cumulativeGasUsed: string;
  input: string;
  confirmations: string;
}

export interface EtherscanInternalTx {
  blockNumber: string;
  timeStamp: string;
  hash: string;
  from: string;
  to: string;
  value: string;
  contractAddress: string;
  input: string;
  type: string;
  gas: string;
  gasUsed: string;
  isError: string;
  errCode: string;
}

// ============================================================
// CRYPTODATADOWNLOAD CLIENT — Bulk CSV OHLCV
// Unlimited, no auth
// ============================================================

export class CryptoDataDownloadClient {
  private baseUrl = 'https://www.cryptodatadownload.com/cdd';
  private limiter: RateLimiter;
  private cache: UnifiedCache;

  constructor(cache: UnifiedCache) {
    this.limiter = new RateLimiter(1, 2); // Be respectful
    this.cache = cache;
  }

  get isConfigured(): boolean {
    return true;
  }

  /**
   * Fetch historical OHLCV data from CryptoDataDownload CSVs.
   * URL pattern: {exchange}_{pair}_{timeframe}.csv
   * Exchanges: Binance, Coinbase, Kraken, Bitfinex, etc.
   * Timeframes: 1m, 5m, 15m, 30m, 1h, 4h, 1d
   */
  async fetchOHLCVCsv(
    exchange: string, pair: string, timeframe: string
  ): Promise<Array<{
    timestamp: Date;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>> {
    const cacheKey = `cdd:${exchange}:${pair}:${timeframe}`;
    const cached = this.cache.get<Array<{ timestamp: Date; open: number; high: number; low: number; close: number; volume: number }>>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();
    try {
      const url = `${this.baseUrl}/${exchange}_${pair}_${timeframe}.csv`;
      const res = await fetch(url);
      if (!res.ok) {
        console.warn(`[CDD] CSV not found: ${url}`);
        return [];
      }

      const csv = await res.text();
      const lines = csv.trim().split('\n');

      // Skip header lines (CDD has 2 header rows)
      const dataLines = lines.slice(2);
      const candles: Array<{ timestamp: Date; open: number; high: number; low: number; close: number; volume: number }> = [];

      for (const line of dataLines) {
        const cols = line.split(',');
        if (cols.length < 8) continue;

        try {
          // CDD CSV format: timestamp, open, high, low, close, volume, ...
          const timestamp = new Date(cols[0] || cols[1]);
          const open = parseFloat(cols[3] || cols[2]);
          const high = parseFloat(cols[4] || cols[3]);
          const low = parseFloat(cols[5] || cols[4]);
          const close = parseFloat(cols[6] || cols[5]);
          const volume = parseFloat(cols[7] || cols[6]);

          if (!isNaN(open) && !isNaN(high) && !isNaN(low) && !isNaN(close)) {
            candles.push({ timestamp, open, high, low, close, volume });
          }
        } catch {
          // Skip malformed lines
        }
      }

      this.cache.set(cacheKey, candles);
      console.log(`[CDD] Fetched ${candles.length} candles for ${exchange}_${pair}_${timeframe}`);
      return candles;
    } catch (err) {
      console.error('[CDD] Fetch error:', err);
      return [];
    }
  }

  /** Get available exchange/pair combos for backfill */
  getCommonPairs(): Array<{ exchange: string; pair: string; name: string }> {
    return [
      { exchange: 'Binance', pair: 'BTCUSDT', name: 'Bitcoin' },
      { exchange: 'Binance', pair: 'ETHUSDT', name: 'Ethereum' },
      { exchange: 'Binance', pair: 'SOLUSDT', name: 'Solana' },
      { exchange: 'Binance', pair: 'BNBUSDT', name: 'BNB' },
      { exchange: 'Binance', pair: 'XRPUSDT', name: 'XRP' },
      { exchange: 'Binance', pair: 'DOGEUSDT', name: 'Dogecoin' },
      { exchange: 'Binance', pair: 'ADAUSDT', name: 'Cardano' },
      { exchange: 'Binance', pair: 'AVAXUSDT', name: 'Avalanche' },
      { exchange: 'Coinbase', pair: 'BTCUSD', name: 'Bitcoin' },
      { exchange: 'Coinbase', pair: 'ETHUSD', name: 'Ethereum' },
      { exchange: 'Kraken', pair: 'BTCUSD', name: 'Bitcoin' },
      { exchange: 'Kraken', pair: 'ETHUSD', name: 'Ethereum' },
    ];
  }

  /** Bulk backfill from CDD for common pairs */
  async bulkBackfill(timeframe: string = '1h'): Promise<number> {
    let totalStored = 0;
    const pairs = this.getCommonPairs();

    for (const p of pairs) {
      const candles = await this.fetchOHLCVCsv(p.exchange, p.pair, timeframe);
      for (const c of candles) {
        try {
          // Find or create a token for this pair
          const tokenAddress = `cdd:${p.exchange}:${p.pair}`;
          const chain = 'CDD'; // Virtual chain for CDD data

          await db.priceCandle.upsert({
            where: {
              tokenAddress_chain_timeframe_timestamp: {
                tokenAddress, chain, timeframe, timestamp: c.timestamp,
              },
            },
            create: {
              tokenAddress, chain, timeframe, timestamp: c.timestamp,
              open: c.open, high: c.high, low: c.low, close: c.close,
              volume: c.volume, trades: 0, source: 'cryptodatadownload',
            },
            update: {
              close: c.close, volume: c.volume,
            },
          });
          totalStored++;
        } catch {
          // Skip duplicates
        }
      }
      await delay(500);
    }

    return totalStored;
  }
}

// ============================================================
// REALTIME SCANNER
// Scans DexScreener + DeFi Llama + CoinGecko every cycle
// ============================================================

class RealtimeScanner {
  private dexscreener: DexScreenerClient;
  private coingecko: CoinGeckoClient;
  private defiLlama: DefiLlamaClient;

  constructor(dexscreener: DexScreenerClient, coingecko: CoinGeckoClient, defiLlama: DefiLlamaClient) {
    this.dexscreener = dexscreener;
    this.coingecko = coingecko;
    this.defiLlama = defiLlama;
  }

  /** Run a full realtime scan across all sources */
  async scan(config: ExtractorConfig): Promise<RealtimeScanResult> {
    const startTime = Date.now();
    let tokensDiscovered = 0;
    let tokensUpdated = 0;
    let protocolUpdates = 0;
    const trendingCoins: CoinGeckoTrendingItem[] = [];
    const dexPairs: DexScreenerPair[] = [];

    // Source 1: CoinGecko trending
    try {
      const trending = await this.coingecko.getTrending();
      trendingCoins.push(...trending);
      tokensDiscovered += trending.length;
    } catch (err) {
      console.error('[RealtimeScanner] CoinGecko trending error:', err);
    }

    // Source 2: DexScreener popular searches
    const popularQueries = ['SOL', 'BONK', 'WIF', 'JUP', 'PEPE', 'ETH', 'MEME'];
    try {
      const pairs = await this.dexscreener.batchSearch(popularQueries.slice(0, 3));
      dexPairs.push(...pairs);

      // Persist discovered tokens
      for (const pair of pairs) {
        if (!pair.baseToken?.address) continue;
        const addr = pair.baseToken.address;
        const chain = normalizeChain(pair.chainId);

        try {
          const existing = await db.token.findUnique({ where: { address: addr } });
          if (existing) {
            // Update with latest data
            await db.token.update({
              where: { address: addr },
              data: {
                priceUsd: parseFloat(pair.priceUsd || '0') || existing.priceUsd,
                volume24h: pair.volume?.h24 || existing.volume24h,
                liquidity: pair.liquidity?.usd || existing.liquidity,
                marketCap: pair.marketCap || pair.fdv || existing.marketCap,
                priceChange5m: pair.priceChange?.m5 || existing.priceChange5m,
                priceChange15m: pair.priceChange?.m15 || existing.priceChange15m,
                priceChange1h: pair.priceChange?.h1 || existing.priceChange1h,
                priceChange24h: pair.priceChange?.h24 || existing.priceChange24h,
                dexId: pair.dexId,
                pairAddress: pair.pairAddress,
                dex: pair.dexId,
                pairUrl: `https://dexscreener.com/${pair.chainId}/${pair.pairAddress}`,
                uniqueWallets24h: (pair.txns?.h24?.buys || 0) + (pair.txns?.h24?.sells || 0),
              },
            });
            tokensUpdated++;
          } else {
            await db.token.create({
              data: {
                address: addr,
                symbol: pair.baseToken.symbol || 'UNKNOWN',
                name: pair.baseToken.name || 'Unknown',
                chain,
                priceUsd: parseFloat(pair.priceUsd || '0'),
                volume24h: pair.volume?.h24 || 0,
                liquidity: pair.liquidity?.usd || 0,
                marketCap: pair.marketCap || pair.fdv || 0,
                priceChange5m: pair.priceChange?.m5 || 0,
                priceChange15m: pair.priceChange?.m15 || 0,
                priceChange1h: pair.priceChange?.h1 || 0,
                priceChange24h: pair.priceChange?.h24 || 0,
                dexId: pair.dexId,
                pairAddress: pair.pairAddress,
                dex: pair.dexId,
                pairUrl: `https://dexscreener.com/${pair.chainId}/${pair.pairAddress}`,
                uniqueWallets24h: (pair.txns?.h24?.buys || 0) + (pair.txns?.h24?.sells || 0),
              },
            });
            tokensDiscovered++;
          }
        } catch (err) {
          // Skip individual token persistence errors
        }
      }
    } catch (err) {
      console.error('[RealtimeScanner] DexScreener error:', err);
    }

    // Source 3: CoinGecko top coins (market data)
    try {
      const topCoins = await this.coingecko.getTopCoins('usd', 50);
      for (const coin of topCoins) {
        // We don't have contract addresses for all, so skip if no address
        // Top coins are tracked by CoinGecko ID, not contract address
        // This data is primarily for enrichment in the ENRICH phase
      }
    } catch (err) {
      console.error('[RealtimeScanner] CoinGecko top coins error:', err);
    }

    // Source 4: DeFi Llama chain TVLs
    try {
      const chains = await this.defiLlama.getChains();
      // Store as signals/protocol data if needed
      if (chains.length > 0) protocolUpdates = chains.length;
    } catch (err) {
      console.error('[RealtimeScanner] DeFi Llama chains error:', err);
    }

    return {
      tokensDiscovered,
      tokensUpdated,
      trendingCoins,
      dexPairs,
      protocolUpdates,
      timestamp: Date.now(),
    };
  }
}

// ============================================================
// WALLET PROFILER
// Moralis + Helius + Etherscan for wallet history
// ============================================================

class WalletProfiler {
  private moralis: MoralisClient;
  private helius: HeliusClient;
  private etherscan: EtherscanV2Client;

  constructor(moralis: MoralisClient, helius: HeliusClient, etherscan: EtherscanV2Client) {
    this.moralis = moralis;
    this.helius = helius;
    this.etherscan = etherscan;
  }

  /** Profile a single wallet across all available sources */
  async profileWallet(address: string, chain: string): Promise<WalletProfileResult> {
    const startTime = Date.now();
    let transactionsFound = 0;
    let transactionsStored = 0;
    let tokenBalances = 0;
    let netWorth = 0;
    const normalizedChain = normalizeChain(chain);

    // Ensure trader exists
    const trader = await db.trader.upsert({
      where: { address },
      create: { address, chain: normalizedChain },
      update: { lastActive: new Date() },
    });

    // Source 1: Moralis (best for EVM chains)
    if (this.moralis.isConfigured) {
      try {
        const moralisChain = toMoralisChain(normalizedChain);

        if (normalizedChain === 'SOL') {
          // Moralis Solana wallet transactions
          const txResult = await this.moralis.getSolanaWalletTransactions(address, 100);
          if (txResult?.result) {
            transactionsFound += txResult.result.length;
            for (const tx of txResult.result) {
              try {
                await db.traderTransaction.upsert({
                  where: { txHash: tx.hash },
                  create: {
                    traderId: trader.id,
                    txHash: tx.hash,
                    blockTime: new Date(tx.block_timestamp),
                    chain: 'SOL',
                    action: this.categorizeAction(tx.category),
                    tokenAddress: tx.erc20_transfers?.[0]?.address || '',
                    tokenSymbol: tx.erc20_transfers?.[0]?.token_symbol || '',
                    valueUsd: 0,
                    isFrontrun: false,
                    isSandwich: false,
                    isWashTrade: false,
                    isJustInTime: false,
                    metadata: JSON.stringify({
                      category: tx.category,
                      summary: tx.summary,
                      possibleSpam: tx.possible_spam,
                    }),
                  },
                  update: {},
                });
                transactionsStored++;
              } catch {
                // Skip duplicates
              }
            }
          }
        } else {
          // EVM wallet transactions
          const txs = await this.moralis.getFullWalletHistory(address, moralisChain, 20);
          transactionsFound += txs.length;
          for (const tx of txs) {
            try {
              const action = this.categorizeAction(tx.category);
              const erc20Transfer = tx.erc20_transfers?.[0];

              await db.traderTransaction.upsert({
                where: { txHash: tx.hash },
                create: {
                  traderId: trader.id,
                  txHash: tx.hash,
                  blockNumber: parseInt(tx.block_number) || null,
                  blockTime: new Date(tx.block_timestamp),
                  chain: normalizedChain,
                  action,
                  tokenAddress: erc20Transfer?.address || '',
                  tokenSymbol: erc20Transfer?.token_symbol || '',
                  quoteToken: tx.native_transfers?.[0] ? 'NATIVE' : undefined,
                  valueUsd: this.estimateTxValue(tx),
                  gasUsed: tx.gas ? parseFloat(tx.gas) : null,
                  gasPrice: tx.gas_price ? parseFloat(tx.gas_price) : null,
                  isFrontrun: false,
                  isSandwich: false,
                  isWashTrade: tx.possible_spam,
                  isJustInTime: false,
                  metadata: JSON.stringify({
                    category: tx.category,
                    summary: tx.summary,
                    erc20Transfers: tx.erc20_transfers?.length || 0,
                    nativeTransfers: tx.native_transfers?.length || 0,
                  }),
                },
                update: {},
              });
              transactionsStored++;
            } catch {
              // Skip duplicates
            }
          }
        }

        // Get token balances
        const balances = await this.moralis.getWalletTokenBalances(address, moralisChain);
        if (balances) {
          tokenBalances = balances.filter(b => !b.possible_spam).length;
          for (const balance of balances.filter(b => !b.possible_spam)) {
            try {
              await db.walletTokenHolding.upsert({
                where: {
                  id: `${trader.id}_${balance.token_address}`,
                },
                create: {
                  traderId: trader.id,
                  tokenAddress: balance.token_address,
                  tokenSymbol: balance.symbol,
                  chain: normalizedChain,
                  balance: parseFloat(balance.balance) / Math.pow(10, balance.decimals),
                  valueUsd: balance.usd_value || 0,
                  buyCount: 0,
                  sellCount: 0,
                },
                update: {
                  balance: parseFloat(balance.balance) / Math.pow(10, balance.decimals),
                  valueUsd: balance.usd_value || 0,
                },
              });
            } catch {
              // Skip
            }
          }
        }

        // Get net worth
        const netWorthData = await this.moralis.getWalletNetWorth(address);
        if (netWorthData) {
          netWorth = parseFloat(netWorthData.total_networth_usd) || 0;
        }
      } catch (err) {
        console.error(`[WalletProfiler] Moralis error for ${address}:`, err);
      }
    }

    // Source 2: Helius (best for Solana detailed transactions)
    if (normalizedChain === 'SOL' && this.helius.isConfigured) {
      try {
        const heliusTxs = await this.helius.getWalletTransactions(address, 100);
        transactionsFound += heliusTxs.length;

        for (const tx of heliusTxs) {
          try {
            const action = this.categorizeHeliusAction(tx.type, tx.source);
            const tokenTransfer = tx.tokenTransfers?.[0];

            await db.traderTransaction.upsert({
              where: { txHash: tx.signature },
              create: {
                traderId: trader.id,
                txHash: tx.signature,
                blockTime: new Date(tx.timestamp * 1000),
                chain: 'SOL',
                dex: tx.source?.toLowerCase(),
                action,
                tokenAddress: tokenTransfer?.mint || '',
                amountIn: tokenTransfer?.tokenAmount || 0,
                valueUsd: this.estimateHeliusValue(tx),
                isFrontrun: false,
                isSandwich: false,
                isWashTrade: false,
                isJustInTime: false,
                totalFeeUsd: tx.fee / 1e9, // lamports to SOL
                metadata: JSON.stringify({
                  type: tx.type,
                  source: tx.source,
                  description: tx.description,
                  nativeTransferCount: tx.nativeTransfers?.length || 0,
                  tokenTransferCount: tx.tokenTransfers?.length || 0,
                }),
              },
              update: {},
            });
            transactionsStored++;
          } catch {
            // Skip duplicates
          }
        }
      } catch (err) {
        console.error(`[WalletProfiler] Helius error for ${address}:`, err);
      }
    }

    // Source 3: Etherscan (EVM backup)
    if (this.etherscan.isConfigured && normalizedChain !== 'SOL') {
      try {
        const chainIdMap: Record<string, string> = {
          'ETH': '1', 'BSC': '56', 'ARB': '42161',
          'OP': '10', 'BASE': '8453', 'MATIC': '137',
        };
        const chainId = chainIdMap[normalizedChain] || '1';

        const ethTxs = await this.etherscan.getTransactions(address, chainId);
        transactionsFound += ethTxs.length;

        for (const tx of ethTxs.slice(0, 50)) { // Limit to avoid overwhelming
          try {
            const isFrom = tx.from.toLowerCase() === address.toLowerCase();
            await db.traderTransaction.upsert({
              where: { txHash: tx.hash },
              create: {
                traderId: trader.id,
                txHash: tx.hash,
                blockNumber: parseInt(tx.blockNumber) || null,
                blockTime: new Date(parseInt(tx.timeStamp) * 1000),
                chain: normalizedChain,
                action: isFrom ? 'SELL' : 'BUY',
                tokenAddress: tx.contractAddress || tx.to || '',
                valueUsd: parseFloat(tx.value || '0') / 1e18 || 0, // wei to ETH
                gasUsed: parseFloat(tx.gasUsed || '0') || null,
                gasPrice: parseFloat(tx.gasPrice || '0') || null,
                isFrontrun: false,
                isSandwich: false,
                isWashTrade: false,
                isJustInTime: false,
                metadata: JSON.stringify({
                  from: tx.from,
                  to: tx.to,
                  functionName: tx.functionName,
                  isError: tx.isError === '1',
                }),
              },
              update: {},
            });
            transactionsStored++;
          } catch {
            // Skip duplicates
          }
        }

        // Also get ERC-20 transfers
        const tokenTxs = await this.etherscan.getTokenTransfers(address, chainId);
        transactionsFound += tokenTxs.length;

        for (const tx of tokenTxs.slice(0, 50)) {
          try {
            const isFrom = tx.from.toLowerCase() === address.toLowerCase();
            await db.traderTransaction.upsert({
              where: { txHash: tx.hash },
              create: {
                traderId: trader.id,
                txHash: `${tx.hash}-${(tx as unknown as Record<string, unknown>).logIndex || '0'}`, // Ensure uniqueness
                blockNumber: parseInt(tx.blockNumber) || null,
                blockTime: new Date(parseInt(tx.timeStamp) * 1000),
                chain: normalizedChain,
                action: isFrom ? 'SELL' : 'BUY',
                tokenAddress: tx.contractAddress,
                tokenSymbol: tx.tokenSymbol,
                amountIn: parseFloat(tx.value) / Math.pow(10, parseInt(tx.tokenDecimal)),
                valueUsd: 0,
                isFrontrun: false,
                isSandwich: false,
                isWashTrade: false,
                isJustInTime: false,
                metadata: JSON.stringify({ tokenName: tx.tokenName }),
              },
              update: {},
            });
            transactionsStored++;
          } catch {
            // Skip duplicates
          }
        }
      } catch (err) {
        console.error(`[WalletProfiler] Etherscan error for ${address}:`, err);
      }
    }

    // Update trader stats
    await db.trader.update({
      where: { id: trader.id },
      data: {
        totalTrades: transactionsStored,
        totalVolumeUsd: netWorth,
        lastActive: new Date(),
        dataQuality: Math.min(1, transactionsStored / 50),
      },
    });

    return {
      address,
      chain: normalizedChain,
      transactionsFound,
      transactionsStored,
      tokenBalances,
      netWorth,
      duration: Date.now() - startTime,
    };
  }

  /** Profile multiple wallets */
  async profileWallets(
    addresses: Array<{ address: string; chain: string }>,
    maxWallets: number = 100
  ): Promise<WalletProfileResult[]> {
    const results: WalletProfileResult[] = [];
    const batch = addresses.slice(0, maxWallets);

    for (const { address, chain } of batch) {
      try {
        const result = await this.profileWallet(address, chain);
        results.push(result);
      } catch (err) {
        console.error(`[WalletProfiler] Error profiling ${address}:`, err);
      }
      await delay(100);
    }

    return results;
  }

  private categorizeAction(category: string): string {
    const map: Record<string, string> = {
      'swap': 'SWAP',
      'token transfer': 'TRANSFER',
      'nft transfer': 'TRANSFER',
      'approve': 'TRANSFER',
      'contract interaction': 'SWAP',
      'receive': 'BUY',
      'send': 'SELL',
    };
    return map[category?.toLowerCase()] || 'UNKNOWN';
  }

  private categorizeHeliusAction(type: string, source: string): string {
    if (type === 'TRANSFER') return 'TRANSFER';
    if (type === 'SWAP') return 'SWAP';
    if (type === 'NFT_SALE') return 'SELL';
    if (source?.toLowerCase().includes('jupiter')) return 'SWAP';
    if (source?.toLowerCase().includes('raydium')) return 'SWAP';
    if (source?.toLowerCase().includes('orca')) return 'SWAP';
    return 'UNKNOWN';
  }

  private estimateTxValue(tx: MoralisTx): number {
    // Rough estimation from ERC-20 transfers
    const erc20 = tx.erc20_transfers;
    if (erc20 && erc20.length > 0) {
      // This is very rough — we don't know the price
      return 0;
    }
    // Native transfer value
    if (tx.native_transfers && tx.native_transfers.length > 0) {
      const nativeVal = parseFloat(tx.native_transfers[0].value || '0');
      // Assume ETH if chain is ETH
      return nativeVal / 1e18 * 2000; // Very rough
    }
    return 0;
  }

  private estimateHeliusValue(tx: HeliusParsedTx): number {
    // Very rough estimate
    if (tx.nativeTransfers && tx.nativeTransfers.length > 0) {
      const lamports = tx.nativeTransfers[0].amount || 0;
      return lamports / 1e9 * 150; // Rough SOL price
    }
    return 0;
  }
}

// ============================================================
// OHLCV BACKFILLER
// CoinGecko OHLCV + DexScreener snapshots + CryptoDataDownload CSV
// ============================================================

class OHLCVBackfiller {
  private coingecko: CoinGeckoClient;
  private dexscreener: DexScreenerClient;
  private cdd: CryptoDataDownloadClient;

  constructor(coingecko: CoinGeckoClient, dexscreener: DexScreenerClient, cdd: CryptoDataDownloadClient) {
    this.coingecko = coingecko;
    this.dexscreener = dexscreener;
    this.cdd = cdd;
  }

  /** Backfill OHLCV for a token using CoinGecko */
  async backfillFromCoinGecko(
    coinId: string, tokenAddress: string, chain: string,
    days: number = 365, timeframes: string[] = ['1h', '4h', '1d']
  ): Promise<OHLCVBackfillResult> {
    const startTime = Date.now();
    const tfResults: OHLCVBackfillResult['timeframes'] = [];
    let totalStored = 0;

    for (const tf of timeframes) {
      let candlesFetched = 0;
      let candlesStored = 0;

      try {
        const ohlcv = await this.coingecko.getOHLCV(coinId, 'usd', days);
        candlesFetched = ohlcv.length;

        // CoinGecko OHLCV returns: [timestamp, open, high, low, close]
        // Time granularity depends on days:
        //   1-2 days: 30-min candles
        //   3-30 days: 4-hour candles
        //   31+ days: 4-hour candles (we map to closest)
        const effectiveTf = this.mapCoinGeckoGranularity(days, tf);

        for (const [ts, open, high, low, close] of ohlcv) {
          try {
            await db.priceCandle.upsert({
              where: {
                tokenAddress_chain_timeframe_timestamp: {
                  tokenAddress, chain, timeframe: effectiveTf,
                  timestamp: new Date(ts),
                },
              },
              create: {
                tokenAddress, chain, timeframe: effectiveTf,
                timestamp: new Date(ts),
                open, high, low, close,
                volume: 0,
                trades: 0,
                source: 'coingecko',
              },
              update: {
                close, high: Math.max(high, low), low: Math.min(low, high),
              },
            });
            candlesStored++;
          } catch {
            // Skip duplicates
          }
        }
      } catch (err) {
        console.error(`[OHLCVBackfiller] CoinGecko backfill error for ${coinId} ${tf}:`, err);
      }

      tfResults.push({ timeframe: tf, candlesFetched, candlesStored });
      totalStored += candlesStored;
      await delay(2200); // CoinGecko rate limit
    }

    return {
      tokenAddress, chain, timeframes: tfResults, totalStored,
      duration: Date.now() - startTime,
    };
  }

  /** Backfill from CoinGecko market chart (has volume data) */
  async backfillFromMarketChart(
    coinId: string, tokenAddress: string, chain: string,
    days: number = 365
  ): Promise<number> {
    let stored = 0;

    try {
      const chartData = await this.coingecko.getMarketChart(coinId, 'usd', days);
      if (!chartData) return 0;

      // Build daily candles from market chart data
      const { prices, total_volumes } = chartData;

      // Group prices by day
      const dailyCandles = new Map<string, { open: number; high: number; low: number; close: number; volume: number }>();

      for (let i = 0; i < prices.length; i++) {
        const [ts, price] = prices[i];
        const date = new Date(ts);
        const dayKey = `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`;
        const dayTs = new Date(`${dayKey}T00:00:00Z`);

        const existing = dailyCandles.get(dayKey);
        const volume = total_volumes[i]?.[1] || 0;

        if (existing) {
          existing.high = Math.max(existing.high, price);
          existing.low = Math.min(existing.low, price);
          existing.close = price;
          existing.volume += volume;
        } else {
          dailyCandles.set(dayKey, {
            open: price, high: price, low: price, close: price, volume,
          });
        }
      }

      // Store daily candles
      for (const [dayKey, candle] of dailyCandles) {
        const timestamp = new Date(`${dayKey}T00:00:00Z`);
        try {
          await db.priceCandle.upsert({
            where: {
              tokenAddress_chain_timeframe_timestamp: {
                tokenAddress, chain, timeframe: '1d', timestamp,
              },
            },
            create: {
              tokenAddress, chain, timeframe: '1d', timestamp,
              open: candle.open, high: candle.high, low: candle.low,
              close: candle.close, volume: candle.volume, trades: 0,
              source: 'coingecko_chart',
            },
            update: {
              close: candle.close, volume: candle.volume,
              high: candle.high, low: candle.low,
            },
          });
          stored++;
        } catch {
          // Skip
        }
      }

      // Build hourly candles from price data
      const hourlyCandles = new Map<number, { open: number; high: number; low: number; close: number }>();
      for (const [ts, price] of prices) {
        const hourTs = Math.floor(ts / 3600000) * 3600000;
        const existing = hourlyCandles.get(hourTs);
        if (existing) {
          existing.high = Math.max(existing.high, price);
          existing.low = Math.min(existing.low, price);
          existing.close = price;
        } else {
          hourlyCandles.set(hourTs, { open: price, high: price, low: price, close: price });
        }
      }

      for (const [hourTs, candle] of hourlyCandles) {
        try {
          await db.priceCandle.upsert({
            where: {
              tokenAddress_chain_timeframe_timestamp: {
                tokenAddress, chain, timeframe: '1h', timestamp: new Date(hourTs),
              },
            },
            create: {
              tokenAddress, chain, timeframe: '1h',
              timestamp: new Date(hourTs),
              open: candle.open, high: candle.high, low: candle.low,
              close: candle.close, volume: 0, trades: 0,
              source: 'coingecko_chart',
            },
            update: {
              close: candle.close, high: candle.high, low: candle.low,
            },
          });
          stored++;
        } catch {
          // Skip
        }
      }
    } catch (err) {
      console.error(`[OHLCVBackfiller] MarketChart error for ${coinId}:`, err);
    }

    return stored;
  }

  /** Build approximate candles from DexScreener price snapshots */
  async backfillFromDexScreener(
    tokenAddress: string, chain: string
  ): Promise<number> {
    let candlesStored = 0;
    const now = new Date();

    try {
      const pairs = await this.dexscreener.getTokenPairs(tokenAddress);
      const pair = pairs.find(p => p.chainId === chain.toLowerCase()) || pairs[0];
      if (!pair) return 0;

      const currentPrice = parseFloat(pair.priceUsd || '0');
      if (currentPrice <= 0) return 0;

      const priceChange = (pair.priceChange || {}) as Record<string, number>;
      const volume = (pair.volume || {}) as Record<string, number>;

      // 1h candle
      const change1h = priceChange.h1 || 0;
      const price1hAgo = currentPrice / (1 + change1h / 100);
      const hourFloor = new Date(Math.floor(now.getTime() / 3600000) * 3600000);

      try {
        await db.priceCandle.upsert({
          where: {
            tokenAddress_chain_timeframe_timestamp: {
              tokenAddress, chain, timeframe: '1h', timestamp: hourFloor,
            },
          },
          create: {
            tokenAddress, chain, timeframe: '1h', timestamp: hourFloor,
            open: price1hAgo, high: Math.max(currentPrice, price1hAgo) * 1.005,
            low: Math.min(currentPrice, price1hAgo) * 0.995,
            close: currentPrice, volume: volume.h1 || (volume.h24 || 0) / 24 || 0,
            trades: 0, source: 'dexscreener_snapshot',
          },
          update: { close: currentPrice, volume: volume.h1 || (volume.h24 || 0) / 24 || 0 },
        });
        candlesStored++;
      } catch { /* Skip */ }

      // 1d candle
      const change24h = priceChange.h24 || 0;
      const price24hAgo = currentPrice / (1 + change24h / 100);
      const dayFloor = new Date(Math.floor(now.getTime() / 86400000) * 86400000);

      try {
        await db.priceCandle.upsert({
          where: {
            tokenAddress_chain_timeframe_timestamp: {
              tokenAddress, chain, timeframe: '1d', timestamp: dayFloor,
            },
          },
          create: {
            tokenAddress, chain, timeframe: '1d', timestamp: dayFloor,
            open: price24hAgo, high: Math.max(currentPrice, price24hAgo) * 1.01,
            low: Math.min(currentPrice, price24hAgo) * 0.99,
            close: currentPrice, volume: volume.h24 || 0,
            trades: 0, source: 'dexscreener_snapshot',
          },
          update: { close: currentPrice, volume: volume.h24 || 0 },
        });
        candlesStored++;
      } catch { /* Skip */ }

      // 5m candle
      const change5m = priceChange.m5 || 0;
      if (change5m !== 0) {
        const price5mAgo = currentPrice / (1 + change5m / 100);
        const fiveMinFloor = new Date(Math.floor(now.getTime() / 300000) * 300000);

        try {
          await db.priceCandle.upsert({
            where: {
              tokenAddress_chain_timeframe_timestamp: {
                tokenAddress, chain, timeframe: '5m', timestamp: fiveMinFloor,
              },
            },
            create: {
              tokenAddress, chain, timeframe: '5m', timestamp: fiveMinFloor,
              open: price5mAgo, high: Math.max(currentPrice, price5mAgo),
              low: Math.min(currentPrice, price5mAgo),
              close: currentPrice, volume: (volume.h24 || 0) / 288,
              trades: 0, source: 'dexscreener_snapshot',
            },
            update: { close: currentPrice },
          });
          candlesStored++;
        } catch { /* Skip */ }
      }

    } catch (err) {
      console.error(`[OHLCVBackfiller] DexScreener snapshot error for ${tokenAddress}:`, err);
    }

    return candlesStored;
  }

  private mapCoinGeckoGranularity(days: number, requestedTf: string): string {
    if (days <= 2) return '30m';
    if (days <= 30) return '4h';
    return requestedTf === '1h' ? '4h' : requestedTf;
  }
}

// ============================================================
// PROTOCOL ANALYZER
// DeFi Llama TVL/yields/fees
// ============================================================

class ProtocolAnalyzer {
  private defiLlama: DefiLlamaClient;

  constructor(defiLlama: DefiLlamaClient) {
    this.defiLlama = defiLlama;
  }

  /** Analyze all protocols and store results */
  async analyzeProtocols(): Promise<{
    protocolsStored: number;
    yieldsStored: number;
    chainsStored: number;
    duration: number;
  }> {
    const startTime = Date.now();
    let protocolsStored = 0;
    let yieldsStored = 0;
    let chainsStored = 0;

    // Fetch protocols
    try {
      const protocols = await this.defiLlama.getProtocols();
      for (const p of protocols) {
        try {
          // Store top protocols as signals
          if (p.tvl > 1_000_000) { // Only store significant protocols
            await db.signal.upsert({
              where: { id: `defillama_${p.id}` },
              create: {
                type: 'PROTOCOL_TVl',
                tokenId: `protocol_${p.id}`,
                confidence: Math.min(100, Math.floor(p.tvl / 1_000_000)),
                direction: p.tvlChange1d > 0 ? 'LONG' : 'SHORT',
                description: `${p.name} TVL: $${(p.tvl / 1e9).toFixed(2)}B (${p.tvlChange1d > 0 ? '+' : ''}${p.tvlChange1d?.toFixed(1)}% 24h)`,
                metadata: JSON.stringify(p),
              },
              update: {
                confidence: Math.min(100, Math.floor(p.tvl / 1_000_000)),
                direction: p.tvlChange1d > 0 ? 'LONG' : 'SHORT',
                description: `${p.name} TVL: $${(p.tvl / 1e9).toFixed(2)}B (${p.tvlChange1d > 0 ? '+' : ''}${p.tvlChange1d?.toFixed(1)}% 24h)`,
                metadata: JSON.stringify(p),
              },
            });
            protocolsStored++;
          }
        } catch {
          // Skip
        }
      }

      // Persist DeFi Llama protocol data to ProtocolData model (batch upsert for top 50)
      for (const protocol of protocols.slice(0, 50)) {
        try {
          await db.protocolData.upsert({
            where: {
              slug_chain: `${protocol.id}_${protocol.chain || 'unknown'}`,
            },
            create: {
              slug: protocol.id,
              chain: protocol.chain || 'unknown',
              protocol: protocol.name || protocol.id,
              tvlUsd: protocol.tvl,
              metadata: JSON.stringify({
                category: protocol.category,
                name: protocol.name,
                tvlChange1d: protocol.tvlChange1d,
                tvlChange7d: protocol.tvlChange7d,
                mcap: protocol.mcap,
                fdv: protocol.fdv,
                url: protocol.url,
                logo: protocol.logo,
                description: protocol.description,
                chains: protocol.chains,
              }),
            },
            update: {
              tvlUsd: protocol.tvl,
              metadata: JSON.stringify({
                tvlChange1d: protocol.tvlChange1d,
                tvlChange7d: protocol.tvlChange7d,
                mcap: protocol.mcap,
                fdv: protocol.fdv,
              }),
            },
          });
        } catch (error) {
          // Skip individual failures
        }
      }
    } catch (err) {
      console.error('[ProtocolAnalyzer] Protocols error:', err);
    }

    // Fetch chain TVLs
    try {
      const chains = await this.defiLlama.getChains();
      chainsStored = chains.length;
    } catch (err) {
      console.error('[ProtocolAnalyzer] Chains error:', err);
    }

    // Fetch yields
    try {
      const yields = await this.defiLlama.getYields();
      // Store top yield opportunities
      const topYields = yields
        .filter(y => y.apy && y.apy > 5 && y.tvlUsd > 100_000)
        .sort((a, b) => (b.apy || 0) - (a.apy || 0))
        .slice(0, 50);

      for (const y of topYields) {
        try {
          await db.signal.upsert({
            where: { id: `yield_${y.project}_${y.chain}_${y.symbol}` },
            create: {
              type: 'YIELD_OPPORTUNITY',
              tokenId: `yield_${y.project}`,
              confidence: Math.min(100, Math.floor(y.tvlUsd / 100_000)),
              direction: 'LONG',
              description: `${y.project} ${y.symbol}: ${(y.apy || 0).toFixed(1)}% APY ($${(y.tvlUsd / 1e6).toFixed(1)}M TVL)`,
              metadata: JSON.stringify(y),
            },
            update: {
              confidence: Math.min(100, Math.floor(y.tvlUsd / 100_000)),
              description: `${y.project} ${y.symbol}: ${(y.apy || 0).toFixed(1)}% APY ($${(y.tvlUsd / 1e6).toFixed(1)}M TVL)`,
              metadata: JSON.stringify(y),
            },
          });
          yieldsStored++;
        } catch {
          // Skip
        }
      }
    } catch (err) {
      console.error('[ProtocolAnalyzer] Yields error:', err);
    }

    return {
      protocolsStored,
      yieldsStored,
      chainsStored,
      duration: Date.now() - startTime,
    };
  }
}

// ============================================================
// EXTRACTION SCHEDULER
// Manages jobs, rate limits, resumability
// ============================================================

class ExtractionScheduler {
  private activeJobs = new Map<string, AbortController>();

  /** Create and start a new extraction job */
  async createJob(
    jobType: string,
    sources: string[],
    config: Record<string, unknown> = {}
  ): Promise<string> {
    const job = await db.extractionJob.create({
      data: {
        type: jobType,
        jobType,
        status: 'PENDING',
        sourcesUsed: JSON.stringify(sources),
        config: JSON.stringify(config),
      },
    });

    const abortController = new AbortController();
    this.activeJobs.set(job.id, abortController);

    return job.id;
  }

  /** Update job status */
  async updateJob(
    jobId: string,
    updates: {
      status?: string;
      tokensDiscovered?: number;
      candlesStored?: number;
      walletsProfiled?: number;
      transactionsStored?: number;
      signalsGenerated?: number;
      protocolsStored?: number;
      errors?: string[];
      resultJson?: Record<string, unknown>;
    }
  ): Promise<void> {
    const data: Record<string, unknown> = {};

    if (updates.status) {
      data.status = updates.status;
      if (updates.status === 'RUNNING') data.startedAt = new Date();
      if (['COMPLETED', 'FAILED', 'ABORTED'].includes(updates.status)) {
        data.completedAt = new Date();
      }
    }
    if (updates.tokensDiscovered !== undefined) data.tokensDiscovered = updates.tokensDiscovered;
    if (updates.candlesStored !== undefined) data.candlesStored = updates.candlesStored;
    if (updates.walletsProfiled !== undefined) data.walletsProfiled = updates.walletsProfiled;
    if (updates.transactionsStored !== undefined) data.transactionsStored = updates.transactionsStored;
    if (updates.signalsGenerated !== undefined) data.signalsGenerated = updates.signalsGenerated;
    if (updates.protocolsStored !== undefined) data.protocolsStored = updates.protocolsStored;
    if (updates.errors) data.errors = JSON.stringify(updates.errors);
    if (updates.resultJson) data.resultJson = JSON.stringify(updates.resultJson);

    try {
      await db.extractionJob.update({ where: { id: jobId }, data });
    } catch (err) {
      console.error(`[ExtractionScheduler] Failed to update job ${jobId}:`, err);
    }
  }

  /** Get job by ID */
  async getJob(jobId: string) {
    return db.extractionJob.findUnique({ where: { id: jobId } });
  }

  /** Get recent jobs */
  async getRecentJobs(limit: number = 20) {
    return db.extractionJob.findMany({
      orderBy: { createdAt: 'desc' },
      take: limit,
    });
  }

  /** Abort a running job */
  async abortJob(jobId: string): Promise<boolean> {
    const controller = this.activeJobs.get(jobId);
    if (controller) {
      controller.abort();
      await this.updateJob(jobId, { status: 'ABORTED' });
      this.activeJobs.delete(jobId);
      return true;
    }
    return false;
  }

  /** Check if a job is aborted */
  isAborted(jobId: string): boolean {
    return this.activeJobs.get(jobId)?.signal.aborted ?? true;
  }

  /** Remove completed job from active tracking */
  completeJob(jobId: string): void {
    this.activeJobs.delete(jobId);
  }

  /** Get active job count */
  get activeJobCount(): number {
    return this.activeJobs.size;
  }
}

// ============================================================
// UNIVERSAL DATA EXTRACTOR — THE BOSS
// ============================================================

export class UniversalDataExtractor {
  // Clients
  readonly moralis: MoralisClient;
  readonly helius: HeliusClient;
  readonly coingecko: CoinGeckoClient;
  readonly dexscreener: DexScreenerClient;
  readonly defiLlama: DefiLlamaClient;
  readonly etherscan: EtherscanV2Client;
  readonly cdd: CryptoDataDownloadClient;
  readonly sqd: SQDClient;
  private dune: DuneClient;
  private footprint: FootprintClient;
  readonly historicalBackfill: HistoricalBackfillEngine;

  // Subsystems
  readonly scanner: RealtimeScanner;
  readonly walletProfiler: WalletProfiler;
  readonly ohlcvBackfiller: OHLCVBackfiller;
  readonly protocolAnalyzer: ProtocolAnalyzer;
  readonly scheduler: ExtractionScheduler;

  // State
  private config: ExtractorConfig;
  private cache: UnifiedCache;
  private _isRunning = false;
  private _currentJobId: string | null = null;
  private _errors: string[] = [];

  constructor(config: Partial<ExtractorConfig> = {}) {
    this.config = { ...DEFAULT_EXTRACTOR_CONFIG, ...config };
    this.cache = new UnifiedCache(this.config.cacheTtlMinutes);

    // Initialize clients
    this.moralis = new MoralisClient(this.config.moralisApiKey || '', this.cache);
    this.helius = new HeliusClient(this.config.heliusApiKey || '', this.cache);
    this.coingecko = new CoinGeckoClient(this.config.coingeckoApiKey, this.cache);
    this.dexscreener = new DexScreenerClient(this.cache);
    this.defiLlama = new DefiLlamaClient(this.cache);
    this.etherscan = new EtherscanV2Client(this.config.etherscanApiKey || '', this.cache);
    this.cdd = new CryptoDataDownloadClient(this.cache);
    this.sqd = new SQDClient(this.config.sqdApiKey, this.config.sqdGatewayUrl);
    this.dune = new DuneClient(this.config.duneApiKey);
    this.footprint = new FootprintClient(this.config.footprintApiKey);
    this.historicalBackfill = new HistoricalBackfillEngine();

    // Initialize subsystems
    this.scanner = new RealtimeScanner(this.dexscreener, this.coingecko, this.defiLlama);
    this.walletProfiler = new WalletProfiler(this.moralis, this.helius, this.etherscan);
    this.ohlcvBackfiller = new OHLCVBackfiller(this.coingecko, this.dexscreener, this.cdd);
    this.protocolAnalyzer = new ProtocolAnalyzer(this.defiLlama);
    this.scheduler = new ExtractionScheduler();
  }

  // ----------------------------------------------------------
  // STATUS
  // ----------------------------------------------------------

  getStatus(): {
    isRunning: boolean;
    currentJobId: string | null;
    cacheSize: number;
    activeJobs: number;
    errors: string[];
    config: {
      moralisApiKey: boolean;
      heliusApiKey: boolean;
      coingeckoApiKey: boolean;
      etherscanApiKey: boolean;
    };
    sourceStatus: Record<string, boolean>;
  } {
    return {
      isRunning: this._isRunning,
      currentJobId: this._currentJobId,
      cacheSize: this.cache.size(),
      activeJobs: this.scheduler.activeJobCount,
      errors: this._errors,
      config: {
        moralisApiKey: this.moralis.isConfigured,
        heliusApiKey: this.helius.isConfigured,
        coingeckoApiKey: this.coingecko.isConfigured,
        etherscanApiKey: this.etherscan.isConfigured,
      },
      sourceStatus: {
        moralis: this.moralis.isConfigured,
        helius: this.helius.isConfigured,
        coingecko: this.coingecko.isConfigured,
        dexscreener: this.dexscreener.isConfigured,
        defiLlama: this.defiLlama.isConfigured,
        etherscan: this.etherscan.isConfigured,
        cryptoDataDownload: this.cdd.isConfigured,
        sqd: this.sqd.isConfigured,
      },
    };
  }

  // ----------------------------------------------------------
  // PHASE 1: SCAN — Discover new tokens
  // ----------------------------------------------------------

  async discoverTokens(): Promise<{ addresses: string[]; count: number; scanResult: RealtimeScanResult }> {
    const scanResult = await this.scanner.scan(this.config);
    const addresses: string[] = [];

    // Collect addresses from discovered tokens
    for (const pair of scanResult.dexPairs) {
      if (pair.baseToken?.address) {
        addresses.push(pair.baseToken.address);
      }
    }

    // Also get tokens already in DB that might need updating
    const existingTokens = await db.token.findMany({
      where: { updatedAt: { lt: new Date(Date.now() - 5 * 60 * 1000) } }, // Not updated in 5 min
      orderBy: { volume24h: 'desc' },
      take: 50,
      select: { address: true },
    });

    for (const t of existingTokens) {
      if (!addresses.includes(t.address)) {
        addresses.push(t.address);
      }
    }

    return {
      addresses: addresses.slice(0, this.config.maxTokensPerDiscovery),
      count: addresses.length,
      scanResult,
    };
  }

  // ----------------------------------------------------------
  // PHASE 2: ENRICH — Get full metadata
  // ----------------------------------------------------------

  async enrichTokens(tokenAddresses: string[]): Promise<number> {
    let enriched = 0;

    for (const addr of tokenAddresses) {
      try {
        const token = await db.token.findUnique({ where: { address: addr } });
        if (!token) continue;

        // Enrich from DexScreener
        const pairs = await this.dexscreener.getTokenPairs(addr);
        const pair = pairs[0]; // Best matching pair

        if (pair) {
          const chain = normalizeChain(pair.chainId);
          await db.token.update({
            where: { address: addr },
            data: {
              priceUsd: parseFloat(pair.priceUsd || '0') || token.priceUsd,
              volume24h: pair.volume?.h24 || token.volume24h,
              liquidity: pair.liquidity?.usd || token.liquidity,
              marketCap: pair.marketCap || pair.fdv || token.marketCap,
              priceChange5m: pair.priceChange?.m5 || token.priceChange5m,
              priceChange15m: pair.priceChange?.m15 || token.priceChange15m,
              priceChange1h: pair.priceChange?.h1 || token.priceChange1h,
              priceChange24h: pair.priceChange?.h24 || token.priceChange24h,
              dexId: pair.dexId || token.dexId,
              pairAddress: pair.pairAddress || token.pairAddress,
              dex: pair.dexId || token.dex,
              pairUrl: pair.pairAddress ? `https://dexscreener.com/${pair.chainId}/${pair.pairAddress}` : token.pairUrl,
              uniqueWallets24h: pair.txns ? (pair.txns.h24?.buys || 0) + (pair.txns.h24?.sells || 0) : token.uniqueWallets24h,
            },
          });
          enriched++;
        }

        // Try CoinGecko enrichment for known coins
        if (this.coingecko.isConfigured) {
          try {
            const searchResult = await this.coingecko.searchCoins(token.symbol);
            // CoinGecko search returns coin IDs which we can use for OHLCV backfill
          } catch {
            // CoinGecko enrichment is optional
          }
        }
      } catch (err) {
        this._errors.push(`Enrich ${addr}: ${String(err)}`);
      }

      await delay(this.config.interRequestDelay);
    }

    return enriched;
  }

  // ----------------------------------------------------------
  // PHASE 3: BACKFILL_OHLCV — Historical candles
  // ----------------------------------------------------------

  async backfillOHLCV(tokenAddresses: string[]): Promise<number> {
    let totalCandles = 0;

    for (const addr of tokenAddresses) {
      try {
        const token = await db.token.findUnique({ where: { address: addr }, select: { chain: true, symbol: true } });
        if (!token) continue;
        const chain = normalizeChain(token.chain);

        // Strategy 1: DexScreener snapshots (fast, always available)
        const snapshotCandles = await this.ohlcvBackfiller.backfillFromDexScreener(addr, chain);
        totalCandles += snapshotCandles;

        // Strategy 2: CoinGecko (if we can identify the coin)
        if (this.coingecko.isConfigured && token.symbol) {
          // Try common CoinGecko IDs for known tokens
          const commonIds: Record<string, string> = {
            'BTC': 'bitcoin', 'ETH': 'ethereum', 'SOL': 'solana',
            'BNB': 'binancecoin', 'XRP': 'ripple', 'ADA': 'cardano',
            'DOGE': 'dogecoin', 'AVAX': 'avalanche-2', 'DOT': 'polkadot',
            'MATIC': 'matic-network', 'LINK': 'chainlink', 'UNI': 'uniswap',
            'AAVE': 'aave', 'MKR': 'maker',
          };
          const coinId = commonIds[token.symbol.toUpperCase()];
          if (coinId) {
            const cgCandles = await this.ohlcvBackfiller.backfillFromCoinGecko(
              coinId, addr, chain, this.config.ohlcvHistoryDays, this.config.ohlcvTimeframes
            );
            totalCandles += cgCandles.totalStored;

            // Also get market chart for volume data
            const chartCandles = await this.ohlcvBackfiller.backfillFromMarketChart(
              coinId, addr, chain, this.config.ohlcvHistoryDays
            );
            totalCandles += chartCandles;
          }
        }
      } catch (err) {
        this._errors.push(`OHLCV backfill ${addr}: ${String(err)}`);
      }

      await delay(this.config.interRequestDelay);
    }

    return totalCandles;
  }

  // ----------------------------------------------------------
  // PHASE 4: EXTRACT_TRADERS — Find top wallets
  // ----------------------------------------------------------

  async extractTraders(tokenAddresses: string[]): Promise<number> {
    let totalTraders = 0;

    for (const addr of tokenAddresses) {
      try {
        // Get pair data from DexScreener which includes transaction data
        const pairs = await this.dexscreener.getTokenPairs(addr);

        for (const pair of pairs) {
          // Extract unique wallet addresses from pair data
          // DexScreener doesn't provide individual wallet addresses directly,
          // but we can identify the pair creators and liquidity providers
          if (pair.pairAddress) {
            // Create a trader record for the pair (for tracking)
            try {
              await db.trader.upsert({
                where: { address: pair.pairAddress },
                create: {
                  address: pair.pairAddress,
                  chain: normalizeChain(pair.chainId),
                  primaryLabel: 'LIQUIDITY_POOL',
                  totalTrades: (pair.txns?.h24?.buys || 0) + (pair.txns?.h24?.sells || 0),
                  totalVolumeUsd: pair.volume?.h24 || 0,
                  lastActive: new Date(),
                },
                update: {
                  totalTrades: (pair.txns?.h24?.buys || 0) + (pair.txns?.h24?.sells || 0),
                  totalVolumeUsd: pair.volume?.h24 || 0,
                  lastActive: new Date(),
                },
              });
              totalTraders++;
            } catch {
              // Skip duplicates
            }
          }
        }
      } catch (err) {
        this._errors.push(`Extract traders ${addr}: ${String(err)}`);
      }

      await delay(this.config.interRequestDelay * 2);
    }

    return totalTraders;
  }

  // ----------------------------------------------------------
  // PHASE 5: PROFILE_WALLETS — Full wallet history
  // ----------------------------------------------------------

  async extractWalletIntelligence(walletAddresses: string[]): Promise<WalletProfileResult[]> {
    const results: WalletProfileResult[] = [];

    // Determine chain for each wallet
    const wallets = walletAddresses.map(addr => {
      // Simple heuristic: if 42 chars starting with 0x, it's EVM; otherwise Solana
      const isEvm = addr.startsWith('0x') && addr.length === 42;
      return { address: addr, chain: isEvm ? 'ETH' : 'SOL' };
    });

    const batchResults = await this.walletProfiler.profileWallets(
      wallets, this.config.maxWalletsPerCycle
    );
    results.push(...batchResults);

    return results;
  }

  // ----------------------------------------------------------
  // PHASE 6: UPDATE_PROTOCOLS — DeFi Llama protocol data
  // ----------------------------------------------------------

  async extractProtocolAnalytics(): Promise<{
    protocolsStored: number;
    yieldsStored: number;
    chainsStored: number;
  }> {
    const result = await this.protocolAnalyzer.analyzeProtocols();
    return {
      protocolsStored: result.protocolsStored,
      yieldsStored: result.yieldsStored,
      chainsStored: result.chainsStored,
    };
  }

  // ----------------------------------------------------------
  // SENTIMENT — Polymarket (bonus)
  // ----------------------------------------------------------

  async extractSentimentIntelligence(): Promise<{ markets: number }> {
    try {
      const res = await fetch('https://gamma-api.polymarket.com/markets?tag=crypto&limit=50&active=true');
      if (!res.ok) return { markets: 0 };
      const markets = await res.json();

      for (const market of markets.slice(0, 20)) {
        try {
          const id = market.id || market.conditionId;
          if (!id) continue;

          await db.signal.upsert({
            where: { id: `polymarket_${id}` },
            create: {
              type: 'PREDICTION_MARKET',
              tokenId: `polymarket_${id}`,
              confidence: Math.round(parseFloat(market.outcomePrices?.replace(/[[\]"']/g, '').split(',')[0] || '0.5') * 100),
              direction: 'LONG',
              description: market.question || 'Crypto prediction market',
              metadata: JSON.stringify({
                outcomes: market.outcomes,
                outcomePrices: market.outcomePrices,
                volume: market.volume,
                liquidity: market.liquidity,
                endDate: market.endDate,
              }),
            },
            update: {
              confidence: Math.round(parseFloat(market.outcomePrices?.replace(/[[\]"']/g, '').split(',')[0] || '0.5') * 100),
              description: market.question || 'Crypto prediction market',
              metadata: JSON.stringify({
                outcomes: market.outcomes,
                outcomePrices: market.outcomePrices,
                volume: market.volume,
                liquidity: market.liquidity,
                endDate: market.endDate,
              }),
            },
          });
        } catch {
          // Skip
        }
      }

      return { markets: markets.length };
    } catch {
      return { markets: 0 };
    }
  }

  // ----------------------------------------------------------
  // REALTIME SYNC
  // ----------------------------------------------------------

  async runRealtimeSync(): Promise<RealtimeScanResult> {
    return this.scanner.scan(this.config);
  }

  // ----------------------------------------------------------
  // FULL EXTRACTION — 6-Phase Pipeline
  // ----------------------------------------------------------

  async runFullExtraction(): Promise<{
    phases: ExtractionPhaseResult[];
    totalDuration: number;
    jobId: string | null;
  }> {
    const pipelineStart = Date.now();
    const phases: ExtractionPhaseResult[] = [];
    this._isRunning = true;
    this._errors = [];

    // Create extraction job
    let jobId: string | null = null;
    try {
      jobId = await this.scheduler.createJob('FULL_EXTRACTION', [
        'moralis', 'helius', 'coingecko', 'dexscreener', 'defiLlama', 'etherscan', 'cryptoDataDownload',
      ], { config: this.config });
      this._currentJobId = jobId;
      await this.scheduler.updateJob(jobId, { status: 'RUNNING' });
    } catch (err) {
      console.error('[UniversalExtractor] Failed to create job:', err);
    }

    try {
      // PHASE 1: SCAN
      const scanStart = Date.now();
      let scanResult;
      let discoveredAddresses: string[] = [];
      try {
        scanResult = await this.discoverTokens();
        discoveredAddresses = scanResult.addresses;
        phases.push({
          phase: 'SCAN',
          success: true,
          duration: Date.now() - scanStart,
          recordsProcessed: scanResult.count,
          recordsStored: scanResult.scanResult.tokensDiscovered,
          errors: [],
          metadata: {
            trendingCoins: scanResult.scanResult.trendingCoins.length,
            dexPairs: scanResult.scanResult.dexPairs.length,
          },
        });
        if (jobId) await this.scheduler.updateJob(jobId, { tokensDiscovered: scanResult.scanResult.tokensDiscovered });
      } catch (err) {
        phases.push({
          phase: 'SCAN', success: false, duration: Date.now() - scanStart,
          recordsProcessed: 0, recordsStored: 0, errors: [String(err)], metadata: {},
        });
      }

      // Check abort
      if (jobId && this.scheduler.isAborted(jobId)) throw new Error('Aborted');

      // PHASE 2: ENRICH
      const enrichStart = Date.now();
      let enrichedCount = 0;
      try {
        enrichedCount = await this.enrichTokens(discoveredAddresses);
        phases.push({
          phase: 'ENRICH', success: true, duration: Date.now() - enrichStart,
          recordsProcessed: discoveredAddresses.length, recordsStored: enrichedCount,
          errors: [], metadata: {},
        });
      } catch (err) {
        phases.push({
          phase: 'ENRICH', success: false, duration: Date.now() - enrichStart,
          recordsProcessed: discoveredAddresses.length, recordsStored: 0,
          errors: [String(err)], metadata: {},
        });
      }

      if (jobId && this.scheduler.isAborted(jobId)) throw new Error('Aborted');

      // PHASE 3: BACKFILL_OHLCV
      const ohlcvStart = Date.now();
      let candleCount = 0;
      try {
        candleCount = await this.backfillOHLCV(discoveredAddresses);
        phases.push({
          phase: 'BACKFILL_OHLCV', success: true, duration: Date.now() - ohlcvStart,
          recordsProcessed: discoveredAddresses.length, recordsStored: candleCount,
          errors: [], metadata: {},
        });
        if (jobId) await this.scheduler.updateJob(jobId, { candlesStored: candleCount });
      } catch (err) {
        phases.push({
          phase: 'BACKFILL_OHLCV', success: false, duration: Date.now() - ohlcvStart,
          recordsProcessed: discoveredAddresses.length, recordsStored: 0,
          errors: [String(err)], metadata: {},
        });
      }

      if (jobId && this.scheduler.isAborted(jobId)) throw new Error('Aborted');

      // PHASE 4: EXTRACT_TRADERS
      const tradersStart = Date.now();
      let traderCount = 0;
      try {
        traderCount = await this.extractTraders(discoveredAddresses);
        phases.push({
          phase: 'EXTRACT_TRADERS', success: true, duration: Date.now() - tradersStart,
          recordsProcessed: discoveredAddresses.length, recordsStored: traderCount,
          errors: [], metadata: {},
        });
      } catch (err) {
        phases.push({
          phase: 'EXTRACT_TRADERS', success: false, duration: Date.now() - tradersStart,
          recordsProcessed: discoveredAddresses.length, recordsStored: 0,
          errors: [String(err)], metadata: {},
        });
      }

      if (jobId && this.scheduler.isAborted(jobId)) throw new Error('Aborted');

      // PHASE 5: PROFILE_WALLETS
      const walletStart = Date.now();
      let walletResults: WalletProfileResult[] = [];
      try {
        // Get wallets that need profiling
        const traders = await db.trader.findMany({
          where: {
            OR: [
              { dataQuality: { lt: 0.3 } },
              { totalTrades: 0 },
            ],
          },
          orderBy: { lastActive: 'desc' },
          take: this.config.maxWalletsPerCycle,
          select: { address: true, chain: true },
        });

        walletResults = await this.walletProfiler.profileWallets(
          traders.map(t => ({ address: t.address, chain: t.chain })),
          this.config.maxWalletsPerCycle,
        );

        const totalTxStored = walletResults.reduce((sum, r) => sum + r.transactionsStored, 0);
        phases.push({
          phase: 'PROFILE_WALLETS', success: true, duration: Date.now() - walletStart,
          recordsProcessed: traders.length, recordsStored: walletResults.length,
          errors: [], metadata: { totalTransactionsStored: totalTxStored },
        });
        if (jobId) await this.scheduler.updateJob(jobId, {
          walletsProfiled: walletResults.length,
          transactionsStored: totalTxStored,
        });
      } catch (err) {
        phases.push({
          phase: 'PROFILE_WALLETS', success: false, duration: Date.now() - walletStart,
          recordsProcessed: 0, recordsStored: 0, errors: [String(err)], metadata: {},
        });
      }

      if (jobId && this.scheduler.isAborted(jobId)) throw new Error('Aborted');

      // PHASE 6: UPDATE_PROTOCOLS
      const protocolStart = Date.now();
      try {
        const protocolResult = await this.extractProtocolAnalytics();
        phases.push({
          phase: 'UPDATE_PROTOCOLS', success: true, duration: Date.now() - protocolStart,
          recordsProcessed: protocolResult.chainsStored,
          recordsStored: protocolResult.protocolsStored + protocolResult.yieldsStored,
          errors: [], metadata: {
            protocols: protocolResult.protocolsStored,
            yields: protocolResult.yieldsStored,
            chains: protocolResult.chainsStored,
          },
        });
        if (jobId) await this.scheduler.updateJob(jobId, {
          protocolsStored: protocolResult.protocolsStored,
          signalsGenerated: protocolResult.yieldsStored,
        });
      } catch (err) {
        phases.push({
          phase: 'UPDATE_PROTOCOLS', success: false, duration: Date.now() - protocolStart,
          recordsProcessed: 0, recordsStored: 0, errors: [String(err)], metadata: {},
        });
      }

      // Mark job as completed
      if (jobId) {
        await this.scheduler.updateJob(jobId, {
          status: 'COMPLETED',
          errors: this._errors,
          resultJson: { phases: phases.map(p => ({ phase: p.phase, success: p.success, recordsStored: p.recordsStored })) },
        });
        this.scheduler.completeJob(jobId);
      }

    } catch (err) {
      if (jobId) {
        await this.scheduler.updateJob(jobId, {
          status: err instanceof Error && err.message === 'Aborted' ? 'ABORTED' : 'FAILED',
          errors: [String(err), ...this._errors],
        });
        this.scheduler.completeJob(jobId);
      }
    }

    this._isRunning = false;
    this._currentJobId = null;

    return {
      phases,
      totalDuration: Date.now() - pipelineStart,
      jobId,
    };
  }

  // ----------------------------------------------------------
  // CDD BULK BACKFILL
  // ----------------------------------------------------------

  async runBulkBackfill(timeframe: string = '1h'): Promise<number> {
    return this.cdd.bulkBackfill(timeframe);
  }

  // ----------------------------------------------------------
  // ABORT
  // ----------------------------------------------------------

  abort(): void {
    if (this._currentJobId) {
      this.scheduler.abortJob(this._currentJobId);
    }
    this._isRunning = false;
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const universalExtractor = new UniversalDataExtractor();
