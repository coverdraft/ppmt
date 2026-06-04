/**
 * OHLCV Data Collection Pipeline - CryptoQuant Terminal
 *
 * Multi-source OHLCV data collection with extended timeframes,
 * candle aggregation, and CoinGecko integration.
 *
 * Data sources (in priority order):
 * - CoinGecko API (PRIMARY - free, no API key, OHLCV via /coins/{id}/ohlc)
 * - DexPaprika API (35 chains, pool-level OHLCV)
 * - DexScreener API (supplementary price data)
 * - Internal aggregation (live candle building from tick data)
 *
 * Timeframes: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w
 */

import { db } from '../db';
import { dexPaprikaClient, type DexPaprikaOHLCV } from './dexpaprika-client';
import { coinGeckoClient, type CoinGeckoOHLCVCandle } from './coingecko-client';

// ============================================================
// RATE LIMITER — Protección contra exceso de llamadas a CoinGecko
// ============================================================

/**
 * Rate limiter para llamadas a la API de CoinGecko.
 * Límite conservador: 25 llamadas por minuto (tier gratuito).
 * Si se alcanza el límite, espera hasta que se reinicie la ventana.
 */
class CoinGeckoRateLimiter {
  /** Marcas de tiempo de las llamadas realizadas en la ventana actual */
  private callTimestamps: number[] = [];
  /** Máximo de llamadas permitidas por minuto */
  private readonly maxCallsPerMinute: number;
  /** Duración de la ventana en milisegundos (60 segundos) */
  private readonly windowMs = 60_000;

  constructor(maxCallsPerMinute: number = 25) {
    this.maxCallsPerMinute = maxCallsPerMinute;
  }

  /**
   * Esperar hasta que haya espacio para una nueva llamada.
   * Si se alcanza el límite, bloquea hasta que la ventana se reinicie.
   */
  async acquire(): Promise<void> {
    const now = Date.now();

    // Limpiar timestamps fuera de la ventana actual
    this.callTimestamps = this.callTimestamps.filter(
      (ts) => now - ts < this.windowMs,
    );

    if (this.callTimestamps.length >= this.maxCallsPerMinute) {
      // Calcular cuánto esperar hasta que la llamada más antigua salga de la ventana
      const oldestCall = this.callTimestamps[0];
      const waitMs = oldestCall + this.windowMs - now + 100; // +100ms margen de seguridad
      console.warn(
        `[rate-limiter] Límite de CoinGecko alcanzado (${this.maxCallsPerMinute}/min). ` +
        `Esperando ${Math.ceil(waitMs / 1000)}s hasta que se reinicie la ventana...`,
      );
      await new Promise((resolve) => setTimeout(resolve, waitMs));

      // Limpiar de nuevo después de esperar
      const afterWait = Date.now();
      this.callTimestamps = this.callTimestamps.filter(
        (ts) => afterWait - ts < this.windowMs,
      );
    }

    // Registrar la llamada
    this.callTimestamps.push(Date.now());
  }

  /**
   * Obtener el número de llamadas restantes en la ventana actual.
   */
  getRemainingCalls(): number {
    const now = Date.now();
    this.callTimestamps = this.callTimestamps.filter(
      (ts) => now - ts < this.windowMs,
    );
    return Math.max(0, this.maxCallsPerMinute - this.callTimestamps.length);
  }

  /**
   * Reiniciar el contador de llamadas.
   */
  reset(): void {
    this.callTimestamps = [];
  }
}

/** Instancia singleton del rate limiter para CoinGecko */
const coinGeckoRateLimiter = new CoinGeckoRateLimiter(25);

// ============================================================
// TYPES
// ============================================================

export interface BackfillResult {
  tokenAddress: string;
  chain: string;
  timeframes: {
    timeframe: string;
    candlesFetched: number;
    candlesStored: number;
    oldestCandle: Date | null;
    newestCandle: Date | null;
  }[];
  totalStored: number;
  duration: number; // ms
}

export interface BatchBackfillResult {
  totalTokens: number;
  results: Map<string, BackfillResult>;
  totalCandlesStored: number;
  failedTokens: string[];
  duration: number;
}

export interface LivePriceData {
  priceUsd: number;
  volume24h: number;
  timestamp: Date;
}

export interface PriceCandleRow {
  id: string;
  tokenAddress: string;
  chain: string;
  timeframe: string;
  timestamp: Date;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  trades: number;
  source: string;
}

export interface OHLCVSeries {
  tokenAddress: string;
  timeframe: string;
  count: number;
  timestamps: number[];
  opens: number[];
  highs: number[];
  lows: number[];
  closes: number[];
  volumes: number[];
}

// ============================================================
// EXTENDED TIMEFRAME SYSTEM
// ============================================================

/**
 * All supported timeframes, from 1m to 1w.
 * Organized by category for use in different analysis contexts.
 */
export const ALL_TIMEFRAMES = [
  '1m', '3m', '5m', '15m', '30m',
  '1h', '2h', '4h', '6h', '12h',
  '1d', '1w',
] as const;

export type Timeframe = typeof ALL_TIMEFRAMES[number];

/** Timeframes fetched directly from APIs */
export const SOURCE_TIMEFRAMES = [
  '1m', '3m', '5m', '15m', '30m',
  '1h', '4h', '1d',
] as const;

/** Timeframes that can be aggregated from source timeframes */
export const AGGREGATED_TIMEFRAMES = ['2h', '6h', '12h', '1w'] as const;

/** Default timeframes for backfill (balance coverage vs API calls) */
const DEFAULT_TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'] as const;

/** Timeframes for live candle ingestion */
const LIVE_TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'] as const;

/** Map internal timeframes to CoinGecko OHLCV days parameter.
 * CoinGecko only supports specific day values.
 * Candle granularity depends on days selected:
 *   1 day → 30 min candles
 *   7-14 days → 4 hour candles
 *   30+ days → 4 hour candles
 */
const COINGECKO_TF_DAYS_MAP: Record<string, number> = {
  '30m': 1,
  '1h': 1,
  '4h': 7,
  '1d': 30,
  '1w': 90,
};

/** Map CoinGecko days to the candle timeframe they produce */
const COINGECKO_DAYS_TO_TF: Record<number, string> = {
  1: '30m',
  7: '4h',
  14: '4h',
  30: '4h',
  90: '4h',
  180: '4h',
  365: '4h',
};

/** Duration of each timeframe in seconds */
export const TIMEFRAME_SECONDS: Record<string, number> = {
  '1m': 60,
  '3m': 180,
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '1h': 3600,
  '2h': 7200,
  '4h': 14400,
  '6h': 21600,
  '12h': 43200,
  '1d': 86400,
  '1w': 604800,
};

/**
 * Aggregation rules: how to build higher timeframes from lower ones.
 * key = target timeframe, value = source timeframe + count
 */
const AGGREGATION_RULES: Record<string, { source: string; count: number }> = {
  '2h':  { source: '1h',  count: 2 },
  '6h':  { source: '1h',  count: 6 },
  '12h': { source: '4h',  count: 3 },
  '1w':  { source: '1d',  count: 7 },
};

/**
 * Multi-timeframe analysis context.
 * Defines which timeframes to use for different analysis types.
 */
export const TIMEFRAME_CONTEXTS = {
  /** Scalping: fast timeframes */
  SCALPING: ['1m', '3m', '5m', '15m'] as const,
  /** Day trading: medium timeframes */
  DAY_TRADING: ['5m', '15m', '1h', '4h'] as const,
  /** Swing trading: higher timeframes */
  SWING: ['1h', '4h', '1d', '1w'] as const,
  /** Full multi-timeframe analysis */
  FULL: ['1m', '5m', '15m', '1h', '4h', '1d'] as const,
  /** Quick analysis (minimal API calls) */
  QUICK: ['5m', '1h', '1d'] as const,
} as const;

/** Delay between sequential API calls (ms) */
const INTER_REQUEST_DELAY = 50;

// ============================================================
// CANDLE AGGREGATION
// ============================================================

/**
 * Aggregate lower-timeframe candles into higher-timeframe candles.
 * For example, 6 x 1h candles → 1 x 6h candle.
 */
export function aggregateCandles(
  sourceCandles: PriceCandleRow[],
  targetTimeframe: string,
): PriceCandleRow[] {
  const rule = AGGREGATION_RULES[targetTimeframe];
  if (!rule) return sourceCandles; // Not an aggregatable timeframe

  const targetSeconds = TIMEFRAME_SECONDS[targetTimeframe];
  if (!targetSeconds) return [];

  // Group candles by their target period
  const groups = new Map<number, PriceCandleRow[]>();

  for (const candle of sourceCandles) {
    const periodStart = Math.floor(candle.timestamp.getTime() / (targetSeconds * 1000)) * (targetSeconds * 1000);
    const existing = groups.get(periodStart) ?? [];
    existing.push(candle);
    groups.set(periodStart, existing);
  }

  // Build aggregated candles
  const result: PriceCandleRow[] = [];

  for (const [periodStart, group] of groups) {
    if (group.length === 0) continue;

    // Sort by timestamp within the group
    group.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime());

    const aggregated: PriceCandleRow = {
      id: `agg_${group[0].id}`, // Synthetic ID
      tokenAddress: group[0].tokenAddress,
      chain: group[0].chain,
      timeframe: targetTimeframe,
      timestamp: new Date(periodStart),
      open: group[0].open,
      high: Math.max(...group.map(c => c.high)),
      low: Math.min(...group.map(c => c.low)),
      close: group[group.length - 1].close,
      volume: group.reduce((s, c) => s + c.volume, 0),
      trades: group.reduce((s, c) => s + c.trades, 0),
      source: 'aggregated',
    };

    result.push(aggregated);
  }

  return result;
}

// ============================================================
// OHLCV PIPELINE CLASS
// ============================================================

export class OHLCVPipeline {
  constructor() {
    // CoinGecko is the ONLY OHLCV source.
  }

  // ----------------------------------------------------------
  // 0. fetchCoinGeckoOHLCV (PRIMARY OHLCV SOURCE)
  // ----------------------------------------------------------

  /**
   * Fetch OHLCV data from CoinGecko (PRIMARY source).
   * CoinGecko provides OHLCV via /coins/{id}/ohlc - free, no API key needed.
   *
   * For tokens stored in our DB, we need to map from the token address
   * to a CoinGecko coin_id. This is done by:
   *   1. Using the token address directly if it looks like a CoinGecko ID
   *   2. Looking up the contract address on the appropriate platform
   *   3. Falling back to search
   *
   * CoinGecko OHLCV returns candles with granularity based on days:
   *   1 day → 30 min candles
   *   7 days → 4 hour candles
   *   30 days → 4 hour candles
   */
  async fetchCoinGeckoOHLCV(
    tokenAddress: string,
    chain: string,
    days: number = 7,
  ): Promise<Array<{ timestamp: number; open: number; high: number; low: number; close: number; volume: number }>> {
    try {
      // Step 0: Respetar rate limit antes de hacer cualquier llamada a CoinGecko
      await coinGeckoRateLimiter.acquire();

      // Step 1: Resolve token address to CoinGecko coin ID
      let coinId: string | null = null;

      // Check if the address is already a CoinGecko coin ID
      // (native coins like "bitcoin", "ethereum", "solana")
      const nativeCoinPattern = /^[a-z0-9-]+$/;
      if (nativeCoinPattern.test(tokenAddress) && !tokenAddress.startsWith('0x') && tokenAddress.length < 50) {
        // Might be a CoinGecko ID directly
        coinId = tokenAddress;
      }

      // If not a direct ID, try to resolve via contract address lookup
      if (!coinId) {
        try {
          await coinGeckoRateLimiter.acquire();
          coinId = await coinGeckoClient.getCoinIdFromContract(chain, tokenAddress);
        } catch {
          // Contract lookup failed
        }
      }

      // If still no coin ID, try search as last resort
      if (!coinId) {
        try {
          await coinGeckoRateLimiter.acquire();
          // Try to find by searching the address
          const searchResults = await coinGeckoClient.searchTokens(tokenAddress);
          if (searchResults.length > 0) {
            coinId = searchResults[0].id;
          }
        } catch {
          // Search failed
        }
      }

      if (!coinId) {
        console.warn(`[ohlcv-pipeline] Could not resolve CoinGecko coin ID for ${tokenAddress}`);
        return [];
      }

      // Step 2: Fetch OHLCV data from CoinGecko (con rate limiter)
      await coinGeckoRateLimiter.acquire();
      const candles = await coinGeckoClient.getOHLCV(coinId, days);

      if (!candles || candles.length === 0) {
        return [];
      }

      // Step 3: Fetch volume data from /market_chart (OHLCV endpoint doesn't include volume)
      let volumeMap = new Map<number, number>();
      try {
        await coinGeckoRateLimiter.acquire();
        const chartData = await coinGeckoClient.getMarketChart(coinId, days);
        if (chartData?.total_volumes) {
          const candleMs = days === 1 ? 30 * 60 * 1000 : 4 * 60 * 60 * 1000;
          volumeMap = coinGeckoClient.buildVolumeMap(chartData.total_volumes, candleMs);
        }
      } catch {
        // Volume fetch failed — candles will have volume=0 (acceptable degradation)
      }

      // Step 4: Map CoinGecko OHLCV to our internal format, enriched with volume
      const candleMs = days === 1 ? 30 * 60 * 1000 : 4 * 60 * 60 * 1000;
      return candles.map(candle => {
        const roundedTs = Math.floor(candle.timestamp / candleMs) * candleMs;
        const volume = volumeMap.get(roundedTs) || 0;
        return {
          timestamp: candle.timestamp,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume,
        };
      });
    } catch (error) {
      console.warn(`[ohlcv-pipeline] CoinGecko OHLCV failed for ${tokenAddress}:`, error);
      return [];
    }
  }

  // ----------------------------------------------------------
  // 1. backfillToken
  // ----------------------------------------------------------

  /**
   * Backfill historical OHLCV candles for a single token across one or more
   * timeframes. Supports both source timeframes and aggregated timeframes.
   *
   * Strategy (in priority order):
   * 1. Try CoinGecko OHLCV (free, no API key, good for major coins)
   * 2. Try DexScreener/DexPaprika for DEX-specific data
   * 3. For aggregated timeframes, build from source data
   */
  async backfillToken(
    tokenAddress: string,
    chain: string,
    timeframes: string[] = [...DEFAULT_TIMEFRAMES],
  ): Promise<BackfillResult> {
    const startTime = Date.now();
    const tfResults: BackfillResult['timeframes'] = [];
    let totalStored = 0;

    // Separate source vs aggregated timeframes
    const sourceTfs = timeframes.filter(tf => (SOURCE_TIMEFRAMES as readonly string[]).includes(tf));
    const aggTfs = timeframes.filter(tf => (AGGREGATED_TIMEFRAMES as readonly string[]).includes(tf));

    // Step 1: Try CoinGecko FIRST (free, no API key needed)
    // CoinGecko supports limited timeframes: 30m (1 day), 4h (7-30 days), etc.
    const coinGeckoTfs = sourceTfs.filter(tf => COINGECKO_TF_DAYS_MAP[tf] !== undefined);
    const nonCoinGeckoTfs = sourceTfs.filter(tf => COINGECKO_TF_DAYS_MAP[tf] === undefined);

    if (coinGeckoTfs.length > 0) {
      try {
        const cgResult = await this.backfillFromCoinGecko(tokenAddress, chain, coinGeckoTfs);
        if (cgResult.candlesStored > 0) {
          tfResults.push(cgResult);
          totalStored += cgResult.candlesStored;
        }
      } catch {
        // CoinGecko failed, will fall back to other sources
      }
    }

    // Step 2: Backfill remaining source timeframes from CoinGecko (use closest days mapping)
    for (const tf of nonCoinGeckoTfs) {
      // For timeframes not directly supported by CoinGecko, try the closest mapping
      // These will get 30m or 4h candles from CoinGecko
      const fallbackDays = tf === '1m' || tf === '3m' || tf === '5m' || tf === '15m' ? 1 : 7;
      try {
        const tfResult = await this.backfillTimeframe(tokenAddress, chain, tf, fallbackDays);
        tfResults.push(tfResult);
        totalStored += tfResult.candlesStored;
      } catch {
        // Backfill failed for this timeframe
      }
      await this.delay(INTER_REQUEST_DELAY);
    }

    // Step 3: Try DexPaprika for additional coverage (especially for non-Solana chains)
    if (chain !== 'SOL') {
      try {
        const dpResult = await this.backfillFromDexPaprika(tokenAddress, chain, sourceTfs);
        if (dpResult.candlesStored > 0) {
          tfResults.push(dpResult);
          totalStored += dpResult.candlesStored;
        }
      } catch {
        // DexPaprika is supplementary - ignore errors
      }
    }

    // Step 4: Build aggregated timeframes from source data
    for (const targetTf of aggTfs) {
      const rule = AGGREGATION_RULES[targetTf];
      if (!rule) continue;

      // Check if we have the source timeframe data
      const sourceTfResult = tfResults.find(r => r.timeframe === rule.source);
      if (!sourceTfResult || !sourceTfResult.newestCandle) continue;

      // Load source candles and aggregate
      try {
        const sourceCandles = await this.getCandles(tokenAddress, rule.source, undefined, undefined, 500);
        if (sourceCandles.length >= rule.count) {
          const aggCandles = aggregateCandles(sourceCandles, targetTf);
          let aggStored = 0;

          for (const candle of aggCandles) {
            try {
              await db.priceCandle.upsert({
                where: {
                  tokenAddress_chain_timeframe_timestamp: {
                    tokenAddress: candle.tokenAddress,
                    chain: candle.chain,
                    timeframe: targetTf,
                    timestamp: candle.timestamp,
                  },
                },
                create: {
                  tokenAddress: candle.tokenAddress,
                  chain: candle.chain,
                  timeframe: targetTf,
                  timestamp: candle.timestamp,
                  open: candle.open,
                  high: candle.high,
                  low: candle.low,
                  close: candle.close,
                  volume: candle.volume,
                  trades: candle.trades,
                  source: 'aggregated',
                },
                update: {
                  high: candle.high,
                  low: candle.low,
                  close: candle.close,
                  volume: candle.volume,
                },
              });
              aggStored++;
            } catch {
              // Skip failed upserts
            }
          }

          tfResults.push({
            timeframe: targetTf,
            candlesFetched: aggCandles.length,
            candlesStored: aggStored,
            oldestCandle: aggCandles[0]?.timestamp ?? null,
            newestCandle: aggCandles[aggCandles.length - 1]?.timestamp ?? null,
          });
          totalStored += aggStored;
        }
      } catch {
        // Aggregation failed - skip
      }
    }

    return {
      tokenAddress,
      chain,
      timeframes: tfResults,
      totalStored,
      duration: Date.now() - startTime,
    };
  }

  /**
   * Backfill a single timeframe for a token using CoinGecko.
   * CoinGecko is the ONLY OHLCV source now.
   */
  private async backfillTimeframe(
    tokenAddress: string,
    chain: string,
    internalTf: string,
    days: number,
  ): Promise<BackfillResult['timeframes'][0]> {
    let candlesFetched = 0;
    let candlesStored = 0;
    let oldestCandle: Date | null = null;
    let newestCandle: Date | null = null;

    try {
      // Fetch OHLCV from CoinGecko
      const candles = await this.fetchCoinGeckoOHLCV(tokenAddress, chain, days);

      if (candles.length === 0) {
        return {
          timeframe: internalTf,
          candlesFetched: 0,
          candlesStored: 0,
          oldestCandle: null,
          newestCandle: null,
        };
      }

      // Determine the actual timeframe from CoinGecko
      const actualTf = COINGECKO_DAYS_TO_TF[days] ?? internalTf;

      for (const item of candles) {
        const candleTs = new Date(item.timestamp);

        try {
          await db.priceCandle.upsert({
            where: {
              tokenAddress_chain_timeframe_timestamp: {
                tokenAddress,
                chain,
                timeframe: actualTf,
                timestamp: candleTs,
              },
            },
            create: {
              tokenAddress,
              chain,
              timeframe: actualTf,
              timestamp: candleTs,
              open: item.open,
              high: item.high,
              low: item.low,
              close: item.close,
              volume: item.volume,
              trades: 0,
              source: 'coingecko',
            },
            update: {
              close: item.close,
              volume: item.volume,
            },
          });

          candlesFetched++;
          candlesStored++;
          if (!oldestCandle || candleTs < oldestCandle) oldestCandle = candleTs;
          if (!newestCandle || candleTs > newestCandle) newestCandle = candleTs;
        } catch {
          // Skip failed upserts
        }
      }
    } catch {
      // CoinGecko backfill failed for this timeframe
    }

    return {
      timeframe: internalTf,
      candlesFetched,
      candlesStored,
      oldestCandle,
      newestCandle,
    };
  }

  /**
   * Backfill OHLCV from CoinGecko for supported timeframes.
   * CoinGecko is the PRIMARY source - free, no API key needed.
   * Only supports specific timeframes based on the days parameter:
   *   1 day → 30m candles, 7 days → 4h candles, 30 days → 4h candles, etc.
   */
  private async backfillFromCoinGecko(
    tokenAddress: string,
    chain: string,
    timeframes: string[],
  ): Promise<BackfillResult['timeframes'][0]> {
    let candlesFetched = 0;
    let candlesStored = 0;
    let oldestCandle: Date | null = null;
    let newestCandle: Date | null = null;

    // Group timeframes by their CoinGecko days parameter to avoid duplicate requests
    const daysToTfs = new Map<number, string[]>();
    for (const tf of timeframes) {
      const days = COINGECKO_TF_DAYS_MAP[tf];
      if (days !== undefined) {
        const existing = daysToTfs.get(days) ?? [];
        existing.push(tf);
        daysToTfs.set(days, existing);
      }
    }

    for (const [days, tfs] of daysToTfs) {
      try {
        // Fetch OHLCV from CoinGecko
        const candles = await this.fetchCoinGeckoOHLCV(tokenAddress, chain, days);

        if (candles.length === 0) continue;
        candlesFetched += candles.length;

        // Determine the actual timeframe from the days parameter
        const actualTf = COINGECKO_DAYS_TO_TF[days] ?? '4h';

        // Store candles for each target timeframe
        // If the actual timeframe matches one of our targets, store directly
        // If the actual timeframe can be aggregated to our target, we'll handle that later
        for (const targetTf of tfs) {
          // If CoinGecko's granularity matches our target timeframe
          if (targetTf === actualTf) {
            for (const candle of candles) {
              const candleTs = new Date(candle.timestamp);

              try {
                await db.priceCandle.upsert({
                  where: {
                    tokenAddress_chain_timeframe_timestamp: {
                      tokenAddress,
                      chain,
                      timeframe: targetTf,
                      timestamp: candleTs,
                    },
                  },
                  create: {
                    tokenAddress,
                    chain,
                    timeframe: targetTf,
                    timestamp: candleTs,
                    open: candle.open,
                    high: candle.high,
                    low: candle.low,
                    close: candle.close,
                    volume: candle.volume,
                    trades: 0,
                    source: 'coingecko',
                  },
                  update: {
                    close: candle.close,
                    volume: candle.volume,
                  },
                });

                candlesStored++;
                if (!oldestCandle || candleTs < oldestCandle) oldestCandle = candleTs;
                if (!newestCandle || candleTs > newestCandle) newestCandle = candleTs;
              } catch {
                // Skip failed upserts
              }
            }
          } else {
            // Store under the actual timeframe - aggregation will happen in Step 4
            for (const candle of candles) {
              const candleTs = new Date(candle.timestamp);

              try {
                await db.priceCandle.upsert({
                  where: {
                    tokenAddress_chain_timeframe_timestamp: {
                      tokenAddress,
                      chain,
                      timeframe: actualTf,
                      timestamp: candleTs,
                    },
                  },
                  create: {
                    tokenAddress,
                    chain,
                    timeframe: actualTf,
                    timestamp: candleTs,
                    open: candle.open,
                    high: candle.high,
                    low: candle.low,
                    close: candle.close,
                    volume: candle.volume,
                    trades: 0,
                    source: 'coingecko',
                  },
                  update: {
                    close: candle.close,
                    volume: candle.volume,
                  },
                });

                candlesStored++;
                if (!oldestCandle || candleTs < oldestCandle) oldestCandle = candleTs;
                if (!newestCandle || candleTs > newestCandle) newestCandle = candleTs;
              } catch {
                // Skip failed upserts
              }
            }
          }
        }

        await this.delay(INTER_REQUEST_DELAY);
      } catch {
        // CoinGecko request failed for this timeframe group
        continue;
      }
    }

    return {
      timeframe: 'coingecko-combined',
      candlesFetched,
      candlesStored,
      oldestCandle,
      newestCandle,
    };
  }

  /**
   * Backfill from DexPaprika for non-Solana chains.
   * DexPaprika supports 35 chains, making it ideal for cross-chain data.
   */
  private async backfillFromDexPaprika(
    tokenAddress: string,
    chain: string,
    timeframes: string[],
  ): Promise<BackfillResult['timeframes'][0]> {
    let candlesFetched = 0;
    let candlesStored = 0;
    let oldestCandle: Date | null = null;
    let newestCandle: Date | null = null;

    for (const tf of timeframes) {
      try {
        // DexPaprika uses pool-based OHLCV, so we need to find the pool first
        const pools = await dexPaprikaClient.searchPools({
          query: tokenAddress,
          chain: dexPaprikaClient.toDexPaprikaChain(chain),
          limit: 1,
        });

        if (pools.length === 0) continue;

        const poolId = pools[0].id;
        const ohlcvData = await dexPaprikaClient.getOHLCV(chain, poolId, tf, 200);

        for (const bar of ohlcvData) {
          const candleTs = new Date(bar.timestamp * 1000);
          candlesFetched++;

          try {
            await db.priceCandle.upsert({
              where: {
                tokenAddress_chain_timeframe_timestamp: {
                  tokenAddress,
                  chain,
                  timeframe: tf,
                  timestamp: candleTs,
                },
              },
              create: {
                tokenAddress,
                chain,
                timeframe: tf,
                timestamp: candleTs,
                open: bar.open,
                high: bar.high,
                low: bar.low,
                close: bar.close,
                volume: bar.volume,
                trades: 0,
                source: 'dexpaprika',
              },
              update: {
                close: bar.close,
                volume: bar.volume,
              },
            });

            candlesStored++;
            if (!oldestCandle || candleTs < oldestCandle) oldestCandle = candleTs;
            if (!newestCandle || candleTs > newestCandle) newestCandle = candleTs;
          } catch {
            // Skip failed upserts
          }
        }
      } catch {
        // DexPaprika OHLCV may not be available for all pools
        continue;
      }

      await this.delay(INTER_REQUEST_DELAY);
    }

    return {
      timeframe: 'dexpaprika-combined',
      candlesFetched,
      candlesStored,
      oldestCandle,
      newestCandle,
    };
  }

  // ----------------------------------------------------------
  // 2. backfillTopTokens
  // ----------------------------------------------------------

  /**
   * Backfill OHLCV data for the top N tokens by 24h volume.
   * Processes tokens sequentially to respect API rate limits.
   */
  async backfillTopTokens(limit = 20): Promise<BatchBackfillResult> {
    const startTime = Date.now();
    const results = new Map<string, BackfillResult>();
    const failedTokens: string[] = [];
    let totalCandlesStored = 0;

    // Load top tokens from DB ordered by volume24h DESC
    const topTokens = await db.token.findMany({
      where: { volume24h: { gt: 0 } },
      orderBy: { volume24h: 'desc' },
      take: limit,
      select: { address: true, chain: true },
    });

    for (const token of topTokens) {
      try {
        const result = await this.backfillToken(token.address, token.chain);
        results.set(token.address, result);
        totalCandlesStored += result.totalStored;
      } catch (err) {
        console.error(
          `[ohlcv-pipeline] backfill failed for ${token.address}:`,
          err,
        );
        failedTokens.push(token.address);
      }

      // Delay between tokens to avoid rate limits
      await this.delay(INTER_REQUEST_DELAY * 2);
    }

    return {
      totalTokens: topTokens.length,
      results,
      totalCandlesStored,
      failedTokens,
      duration: Date.now() - startTime,
    };
  }

  // ----------------------------------------------------------
  // 3. ingestLiveCandle
  // ----------------------------------------------------------

  /**
   * Given a current price tick, aggregate into the current candle period for
   * each standard timeframe. Upserts the PriceCandle row.
   */
  async ingestLiveCandle(
    tokenAddress: string,
    chain: string,
    priceData: LivePriceData,
  ): Promise<void> {
    const { priceUsd, volume24h, timestamp } = priceData;

    for (const tf of LIVE_TIMEFRAMES) {
      const candleTs = this.floorToPeriod(timestamp, tf);

      try {
        const existing = await db.priceCandle.findUnique({
          where: {
            tokenAddress_chain_timeframe_timestamp: {
              tokenAddress,
              chain,
              timeframe: tf,
              timestamp: candleTs,
            },
          },
        });

        if (existing) {
          // Update existing candle
          await db.priceCandle.update({
            where: { id: existing.id },
            data: {
              high: Math.max(existing.high, priceUsd),
              low: Math.min(existing.low, priceUsd),
              close: priceUsd,
              volume: existing.volume + volume24h * 0.001,
              trades: existing.trades + 1,
            },
          });
        } else {
          // Create new candle with open = high = low = close = price
          await db.priceCandle.create({
            data: {
              tokenAddress,
              chain,
              timeframe: tf,
              timestamp: candleTs,
              open: priceUsd,
              high: priceUsd,
              low: priceUsd,
              close: priceUsd,
              volume: volume24h * 0.001,
              trades: 1,
              source: 'internal',
            },
          });
        }
      } catch (err) {
        console.warn(
          `[ohlcv-pipeline] ingestLiveCandle upsert failed for ${tokenAddress} ${tf}:`,
          err,
        );
      }
    }
  }

  // ----------------------------------------------------------
  // 4. getCandles
  // ----------------------------------------------------------

  /**
   * Load candles from the database with optional time range filtering.
   * Supports all timeframes including aggregated ones.
   * If no candles exist for an aggregated timeframe, attempts on-demand
   * aggregation from the source timeframe.
   */
  async getCandles(
    tokenAddress: string,
    timeframe: string,
    from?: Date,
    to?: Date,
    limit = 500,
  ): Promise<PriceCandleRow[]> {
    // Build filter
    const where: Record<string, unknown> = {
      tokenAddress,
      timeframe,
    };

    if (from || to) {
      where.timestamp = {
        ...(from && { gte: from }),
        ...(to && { lte: to }),
      };
    }

    let candles = await db.priceCandle.findMany({
      where,
      orderBy: { timestamp: 'asc' },
      take: limit,
    });

    // On-demand fetch if no candles in DB
    if (candles.length === 0) {
      // Check if this is an aggregated timeframe
      const aggRule = AGGREGATION_RULES[timeframe];
      if (aggRule) {
        // Try to build from source timeframe
        const sourceCandles = await this.getCandles(
          tokenAddress, aggRule.source, from, to,
          limit * aggRule.count,
        );
        if (sourceCandles.length > 0) {
          const aggregated = aggregateCandles(sourceCandles, timeframe);
          // Store aggregated candles for future use
          for (const candle of aggregated) {
            try {
              await db.priceCandle.upsert({
                where: {
                  tokenAddress_chain_timeframe_timestamp: {
                    tokenAddress: candle.tokenAddress,
                    chain: candle.chain,
                    timeframe,
                    timestamp: candle.timestamp,
                  },
                },
                create: {
                  tokenAddress: candle.tokenAddress,
                  chain: candle.chain,
                  timeframe,
                  timestamp: candle.timestamp,
                  open: candle.open,
                  high: candle.high,
                  low: candle.low,
                  close: candle.close,
                  volume: candle.volume,
                  trades: candle.trades,
                  source: 'aggregated',
                },
                update: {
                  close: candle.close,
                  volume: candle.volume,
                },
              });
            } catch {
              // Skip
            }
          }
          return aggregated;
        }
      }

      // Try CoinGecko on-demand fetch first (PRIMARY source, free)
      const coinGeckoDays = COINGECKO_TF_DAYS_MAP[timeframe];
      if (coinGeckoDays !== undefined) {
        try {
          const chain = 'SOL';
          const cgCandles = await this.fetchCoinGeckoOHLCV(tokenAddress, chain, coinGeckoDays);

          if (cgCandles.length > 0) {
            const actualTf = COINGECKO_DAYS_TO_TF[coinGeckoDays] ?? timeframe;

            for (const item of cgCandles) {
              const candleTs = new Date(item.timestamp);
              try {
                await db.priceCandle.upsert({
                  where: {
                    tokenAddress_chain_timeframe_timestamp: {
                      tokenAddress,
                      chain,
                      timeframe: actualTf,
                      timestamp: candleTs,
                    },
                  },
                  create: {
                    tokenAddress,
                    chain,
                    timeframe: actualTf,
                    timestamp: candleTs,
                    open: item.open,
                    high: item.high,
                    low: item.low,
                    close: item.close,
                    volume: item.volume,
                    trades: 0,
                    source: 'coingecko',
                  },
                  update: {
                    close: item.close,
                    volume: item.volume,
                  },
                });
              } catch {
                // Skip
              }
            }

            candles = await db.priceCandle.findMany({
              where,
              orderBy: { timestamp: 'asc' },
              take: limit,
            });

            if (candles.length > 0) {
              return candles.map((c) => ({
                id: c.id,
                tokenAddress: c.tokenAddress,
                chain: c.chain,
                timeframe: c.timeframe,
                timestamp: c.timestamp,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
                volume: c.volume,
                trades: c.trades,
                source: c.source,
              }));
            }
          }
        } catch {
          // CoinGecko on-demand failed
        }
      }
    }

    return candles.map((c) => ({
      id: c.id,
      tokenAddress: c.tokenAddress,
      chain: c.chain,
      timeframe: c.timeframe,
      timestamp: c.timestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
      trades: c.trades,
      source: c.source,
    }));
  }

  // ----------------------------------------------------------
  // 5. getCandleSeries
  // ----------------------------------------------------------

  /**
   * Get the last `count` candles for a token+timeframe as typed arrays.
   * Supports aggregated timeframes with on-demand aggregation.
   */
  async getCandleSeries(
    tokenAddress: string,
    timeframe: string,
    count: number,
  ): Promise<OHLCVSeries> {
    let candles = await db.priceCandle.findMany({
      where: { tokenAddress, timeframe },
      orderBy: { timestamp: 'desc' },
      take: count,
    });

    // If no candles and it's an aggregated timeframe, try on-demand aggregation
    if (candles.length === 0) {
      const aggRule = AGGREGATION_RULES[timeframe];
      if (aggRule) {
        const sourceCandles = await db.priceCandle.findMany({
          where: { tokenAddress, timeframe: aggRule.source },
          orderBy: { timestamp: 'desc' },
          take: count * aggRule.count,
        });

        if (sourceCandles.length > 0) {
          const sourceRows: PriceCandleRow[] = sourceCandles.map(c => ({
            id: c.id,
            tokenAddress: c.tokenAddress,
            chain: c.chain,
            timeframe: c.timeframe,
            timestamp: c.timestamp,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
            trades: c.trades,
            source: c.source,
          }));

          const aggregated = aggregateCandles(sourceRows, timeframe);
          candles = aggregated.map(c => ({
            ...c,
            createdAt: new Date(),
          }));
        }
      }
    }

    // Reverse to ASC order (oldest first)
    const sorted = [...candles].reverse();

    return {
      tokenAddress,
      timeframe,
      count: sorted.length,
      timestamps: sorted.map((c) => c.timestamp.getTime()),
      opens: sorted.map((c) => c.open),
      highs: sorted.map((c) => c.high),
      lows: sorted.map((c) => c.low),
      closes: sorted.map((c) => c.close),
      volumes: sorted.map((c) => c.volume),
    };
  }

  // ----------------------------------------------------------
  // 6. getMultiTimeframeSeries (NEW)
  // ----------------------------------------------------------

  /**
   * Get candle series for multiple timeframes at once.
   * This is the primary method for multi-timeframe analysis.
   *
   * @param context - Predefined context or custom timeframes
   * @returns Map of timeframe → OHLCVSeries
   */
  async getMultiTimeframeSeries(
    tokenAddress: string,
    context: readonly string[] | keyof typeof TIMEFRAME_CONTEXTS = 'FULL',
    count = 100,
  ): Promise<Map<string, OHLCVSeries>> {
    const timeframes: readonly string[] = Array.isArray(context)
      ? context
      : (TIMEFRAME_CONTEXTS as Record<string, readonly string[]>)[context as string] ?? TIMEFRAME_CONTEXTS.FULL;

    const results = new Map<string, OHLCVSeries>();

    // Fetch in parallel (each is an independent DB query)
    const promises = timeframes.map(async (tf: string) => {
      try {
        const series = await this.getCandleSeries(tokenAddress, tf, count);
        results.set(tf, series);
      } catch {
        // Individual timeframe failure shouldn't block others
      }
    });

    await Promise.allSettled(promises);
    return results;
  }

  // ----------------------------------------------------------
  // PRIVATE HELPERS
  // ----------------------------------------------------------

  private floorToPeriod(date: Date, timeframe: string): Date {
    const ts = date.getTime();
    const seconds = TIMEFRAME_SECONDS[timeframe];
    if (!seconds) return date;
    const floored = Math.floor(ts / (seconds * 1000)) * (seconds * 1000);
    return new Date(floored);
  }

  /**
   * Simple async delay utility.
   */
  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const ohlcvPipeline = new OHLCVPipeline();
