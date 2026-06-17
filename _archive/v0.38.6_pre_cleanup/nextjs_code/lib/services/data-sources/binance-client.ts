/**
 * Binance OHLCV Client - CryptoQuant Terminal
 *
 * FREE data source using Binance public API (no API key required).
 * Provides HIGH-QUALITY OHLCV data with ALL timeframes and real volume.
 *
 * Why Binance?
 *   - All timeframes: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
 *   - Volume included in every candle (no separate API call needed)
 *   - Up to 1000 candles per request
 *   - Rate limit: 1200 req/min (very generous)
 *   - Real trade data from the largest crypto exchange
 *
 * Data routing:
 *   - OHLCV candles → GET /api/v3/klines
 *   - Exchange info (symbol list) → GET /api/v3/exchangeInfo
 *   - 24h ticker → GET /api/v3/ticker/24hr
 *
 * Token mapping:
 *   - Our DB stores tokens by symbol (BTC, ETH, SOL, etc.)
 *   - Binance uses trading pairs (BTCUSDT, ETHUSDT, SOLUSDT)
 *   - This client handles the symbol → pair mapping automatically
 */

import { unifiedCache, cacheKey } from '../../unified-cache';

// ============================================================
// TYPES
// ============================================================

export interface BinanceCandle {
  openTime: number;      // Kline open time (ms)
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;        // Base asset volume (REAL volume from trades)
  closeTime: number;     // Kline close time (ms)
  quoteVolume: number;   // Quote asset volume (USDT volume)
  trades: number;        // Number of trades
  takerBuyBaseVolume: number;
  takerBuyQuoteVolume: number;
}

export interface BinanceSymbolInfo {
  symbol: string;        // e.g., "BTCUSDT"
  baseAsset: string;     // e.g., "BTC"
  quoteAsset: string;    // e.g., "USDT"
  status: string;        // "TRADING" or others
}

export interface BinanceTicker {
  symbol: string;
  priceChange: number;
  priceChangePercent: number;
  weightedAvgPrice: number;
  lastPrice: number;
  volume: number;         // Base volume
  quoteVolume: number;    // USDT volume
  highPrice: number;
  lowPrice: number;
  count: number;          // Trade count
}

// ============================================================
// CONSTANTS
// ============================================================

const SOURCE = 'binance';
const BASE_URL = 'https://api.binance.com';

/** Supported Binance kline intervals */
export const BINANCE_INTERVALS = [
  '1m', '3m', '5m', '15m', '30m',
  '1h', '2h', '4h', '6h', '8h', '12h',
  '1d', '3d', '1w', '1M',
] as const;

export type BinanceInterval = typeof BINANCE_INTERVALS[number];

/** Map our internal timeframes to Binance intervals */
const TF_TO_BINANCE: Record<string, BinanceInterval> = {
  '1m': '1m',
  '3m': '3m',
  '5m': '5m',
  '15m': '15m',
  '30m': '30m',
  '1h': '1h',
  '2h': '2h',
  '4h': '4h',
  '6h': '6h',
  '12h': '12h',
  '1d': '1d',
  '1w': '1w',
};

/** Cache TTLs */
const CACHE_TTLS = {
  klines: 30_000,        // 30 seconds (candles change frequently)
  exchangeInfo: 6 * 60 * 60 * 1000, // 6 hours (symbol list rarely changes)
  tickers: 60_000,       // 1 minute
  symbolMap: 6 * 60 * 60 * 1000,     // 6 hours
};

/** Rate limiting: Binance allows 1200 req/min, we use conservatively */
const INTER_REQUEST_DELAY = 200; // 200ms = ~300 req/min max

// ============================================================
// BINANCE CLIENT CLASS
// ============================================================

class BinanceClient {
  private lastRequestTime = 0;

  // Cache the symbol → Binance pair mapping
  private symbolMap: Map<string, string> = new Map(); // BTC → BTCUSDT
  private symbolMapLoaded = false;
  private symbolMapPromise: Promise<void> | null = null;

  // ----------------------------------------------------------
  // OHLCV / KLINES
  // ----------------------------------------------------------

  /**
   * Get OHLCV candles from Binance /api/v3/klines.
   *
   * @param symbol - Trading pair symbol (e.g., "BTCUSDT")
   * @param interval - Kline interval (e.g., "1m", "5m", "1h", "4h", "1d")
   * @param limit - Number of candles (max 1000)
   * @param startTime - Optional start time in ms
   * @param endTime - Optional end time in ms
   */
  async getKlines(
    symbol: string,
    interval: BinanceInterval = '1h',
    limit: number = 500,
    startTime?: number,
    endTime?: number,
  ): Promise<BinanceCandle[]> {
    const cacheKeyStr = cacheKey(SOURCE, 'klines', `${symbol}:${interval}:${limit}:${startTime || ''}:${endTime || ''}`);

    return unifiedCache.getOrFetch(
      cacheKeyStr,
      async () => {
        await this.throttle();

        const params = new URLSearchParams({
          symbol: symbol.toUpperCase(),
          interval,
          limit: String(Math.min(limit, 1000)),
        });

        if (startTime) params.set('startTime', String(startTime));
        if (endTime) params.set('endTime', String(endTime));

        const url = `${BASE_URL}/api/v3/klines?${params}`;

        try {
          const res = await fetch(url, {
            headers: {
              'Accept': 'application/json',
              'User-Agent': 'CryptoQuant-Terminal/1.0',
            },
          });

          if (res.status === 429) {
            // Retry with exponential backoff (1s, 3s, 9s)
            for (let attempt = 0; attempt < 3; attempt++) {
              const delayMs = 1000 * Math.pow(3, attempt);
              await new Promise(resolve => setTimeout(resolve, delayMs));
              const retryResponse = await fetch(url);
              if (retryResponse.ok) {
                return retryResponse.json();
              }
              if (retryResponse.status !== 429) break;
            }
            console.warn('[Binance] Rate limit exceeded after retries');
            return [];
          }

          if (!res.ok) {
            console.warn(`[Binance] API error: ${res.status} ${res.statusText}`);
            return [];
          }

          const data = await res.json();

          if (!Array.isArray(data)) return [];

          return data.map((k: any[]) => ({
            openTime: k[0],
            open: parseFloat(k[1]) || 0,
            high: parseFloat(k[2]) || 0,
            low: parseFloat(k[3]) || 0,
            close: parseFloat(k[4]) || 0,
            volume: parseFloat(k[5]) || 0,
            closeTime: k[6],
            quoteVolume: parseFloat(k[7]) || 0,
            trades: k[8],
            takerBuyBaseVolume: parseFloat(k[9]) || 0,
            takerBuyQuoteVolume: parseFloat(k[10]) || 0,
          }));
        } catch (error) {
          console.warn(`[Binance] Klines fetch failed for ${symbol}:`, error instanceof Error ? error.message : String(error));
          return [];
        }
      },
      SOURCE,
      CACHE_TTLS.klines,
    );
  }

  /**
   * Get OHLCV candles for a token by its symbol.
   * Automatically resolves the symbol to a Binance trading pair.
   *
   * @param tokenSymbol - Token symbol (e.g., "BTC", "ETH", "SOL")
   * @param timeframe - Our internal timeframe (e.g., "1m", "5m", "1h", "4h", "1d")
   * @param limit - Number of candles (max 1000)
   */
  async getOHLCVBySymbol(
    tokenSymbol: string,
    timeframe: string = '4h',
    limit: number = 500,
  ): Promise<BinanceCandle[]> {
    // Resolve symbol to Binance pair
    const binanceSymbol = await this.resolveSymbol(tokenSymbol);
    if (!binanceSymbol) {
      return [];
    }

    const interval = TF_TO_BINANCE[timeframe];
    if (!interval) {
      console.warn(`[Binance] Unsupported timeframe: ${timeframe}`);
      return [];
    }

    return this.getKlines(binanceSymbol, interval, limit);
  }

  // ----------------------------------------------------------
  // EXCHANGE INFO & SYMBOL RESOLUTION
  // ----------------------------------------------------------

  /**
   * Get all trading pairs from Binance.
   * Caches the result for 6 hours.
   */
  async getExchangeInfo(): Promise<BinanceSymbolInfo[]> {
    const key = cacheKey(SOURCE, 'exchange-info', 'all');

    return unifiedCache.getOrFetch(
      key,
      async () => {
        await this.throttle();

        try {
          const res = await fetch(`${BASE_URL}/api/v3/exchangeInfo`, {
            headers: {
              'Accept': 'application/json',
              'User-Agent': 'CryptoQuant-Terminal/1.0',
            },
          });

          if (!res.ok) return [];

          const data = await res.json();
          const symbols: BinanceSymbolInfo[] = [];

          if (data.symbols && Array.isArray(data.symbols)) {
            for (const s of data.symbols) {
              if (s.status === 'TRADING' && s.quoteAsset === 'USDT') {
                symbols.push({
                  symbol: s.symbol,
                  baseAsset: s.baseAsset,
                  quoteAsset: s.quoteAsset,
                  status: s.status,
                });
              }
            }
          }

          console.log(`[Binance] Loaded ${symbols.length} USDT trading pairs`);
          return symbols;
        } catch (error) {
          console.warn('[Binance] Exchange info fetch failed:', error instanceof Error ? error.message : String(error));
          return [];
        }
      },
      SOURCE,
      CACHE_TTLS.exchangeInfo,
    );
  }

  /**
   * Resolve a token symbol to its Binance trading pair.
   * Examples: BTC → BTCUSDT, ETH → ETHUSDT, SOL → SOLUSDT
   *
   * Returns null if the token is not listed on Binance.
   */
  async resolveSymbol(tokenSymbol: string): Promise<string | null> {
    // Check cache first
    const normalizedSymbol = tokenSymbol.toUpperCase().replace(/[^A-Z0-9]/g, '');

    if (this.symbolMapLoaded && this.symbolMap.has(normalizedSymbol)) {
      return this.symbolMap.get(normalizedSymbol)!;
    }

    // Load symbol map if not loaded
    if (!this.symbolMapLoaded) {
      if (!this.symbolMapPromise) {
        this.symbolMapPromise = this.loadSymbolMap();
      }
      await this.symbolMapPromise;
    }

    return this.symbolMap.get(normalizedSymbol) ?? null;
  }

  /**
   * Check if a token is listed on Binance.
   */
  async isListed(tokenSymbol: string): Promise<boolean> {
    const pair = await this.resolveSymbol(tokenSymbol);
    return pair !== null;
  }

  /**
   * Get all token symbols available on Binance.
   */
  async getListedSymbols(): Promise<string[]> {
    if (!this.symbolMapLoaded) {
      if (!this.symbolMapPromise) {
        this.symbolMapPromise = this.loadSymbolMap();
      }
      await this.symbolMapPromise;
    }
    return Array.from(this.symbolMap.keys());
  }

  /**
   * Get 24h ticker data for a symbol.
   */
  async get24hrTicker(symbol: string): Promise<BinanceTicker | null> {
    const key = cacheKey(SOURCE, 'ticker', symbol.toUpperCase());

    return unifiedCache.getOrFetch(
      key,
      async () => {
        await this.throttle();

        try {
          const res = await fetch(
            `${BASE_URL}/api/v3/ticker/24hr?symbol=${encodeURIComponent(symbol.toUpperCase())}`,
            {
              headers: {
                'Accept': 'application/json',
                'User-Agent': 'CryptoQuant-Terminal/1.0',
              },
            },
          );

          if (!res.ok) return null;

          const data = await res.json();
          return {
            symbol: data.symbol,
            priceChange: parseFloat(data.priceChange) || 0,
            priceChangePercent: parseFloat(data.priceChangePercent) || 0,
            weightedAvgPrice: parseFloat(data.weightedAvgPrice) || 0,
            lastPrice: parseFloat(data.lastPrice) || 0,
            volume: parseFloat(data.volume) || 0,
            quoteVolume: parseFloat(data.quoteVolume) || 0,
            highPrice: parseFloat(data.highPrice) || 0,
            lowPrice: parseFloat(data.lowPrice) || 0,
            count: data.count,
          };
        } catch {
          return null;
        }
      },
      SOURCE,
      CACHE_TTLS.tickers,
    );
  }

  // ----------------------------------------------------------
  // PRIVATE HELPERS
  // ----------------------------------------------------------

  /**
   * Load the symbol map from Binance exchange info.
   * Maps base asset symbols to their USDT trading pairs.
   */
  private async loadSymbolMap(): Promise<void> {
    try {
      const symbols = await this.getExchangeInfo();

      this.symbolMap.clear();

      for (const s of symbols) {
        // Map base asset (BTC) → full symbol (BTCUSDT)
        // Prefer USDT pairs, but also add USDC/BUSD as fallbacks
        if (s.quoteAsset === 'USDT') {
          this.symbolMap.set(s.baseAsset, s.symbol);
        }
      }

      this.symbolMapLoaded = true;
      console.log(`[Binance] Symbol map loaded: ${this.symbolMap.size} tokens`);
    } catch (error) {
      console.warn('[Binance] Failed to load symbol map:', error instanceof Error ? error.message : String(error));
      // Reset state so next call retries — without this, the map never recovers
      this.symbolMapLoaded = false;
      this.symbolMapPromise = null;
    }
  }

  /**
   * Throttle requests to respect rate limits.
   */
  private async throttle(): Promise<void> {
    const now = Date.now();
    const elapsed = now - this.lastRequestTime;
    if (elapsed < INTER_REQUEST_DELAY) {
      await new Promise(resolve => setTimeout(resolve, INTER_REQUEST_DELAY - elapsed));
    }
    this.lastRequestTime = Date.now();
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const binanceClient = new BinanceClient();
