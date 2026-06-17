/**
 * Data Quality Gate - CryptoQuant Terminal
 *
 * Pre-backtest data quality assessment service.
 * Prevents the AI Strategy Manager from running backtests with
 * insufficient or poor-quality data.
 *
 * Scoring breakdown (100 points total):
 *   - Token count:     30 pts  (20 tokens = 30, <5 = 5)
 *   - Candle count:    25 pts  (>=10000 = 25, <100 = 0)
 *   - Real data %:     25 pts  (100% real = 25, 0% real = 5)
 *   - Time coverage:   20 pts  (>=90 days = 20, <1 day = 0)
 *
 * Levels:
 *   EXCELLENT: 90+
 *   GOOD:      70-89
 *   MARGINAL:  50-69
 *   POOR:      30-49
 *   CRITICAL:  <30
 */

import { db } from '@/lib/db';

// ============================================================
// TYPES
// ============================================================

export interface DataQualityReport {
  overallScore: number;       // 0-100
  isReady: boolean;           // true if score >= 50
  level: 'EXCELLENT' | 'GOOD' | 'MARGINAL' | 'POOR' | 'CRITICAL';

  // Breakdown
  tokenCount: number;
  tokensWithCandles: number;
  tokensWithRealCandles: number;    // source === 'coingecko'
  tokensWithGeneratedCandles: number; // source === 'generated'
  totalCandles: number;
  realCandlesPct: number;           // % of candles that are real (not generated)

  // Time coverage
  oldestCandle: Date | null;
  newestCandle: Date | null;
  coverageDays: number;

  // Per-timeframe coverage
  timeframeCoverage: Record<string, { count: number; tokens: number }>;

  // Recommendations
  warnings: string[];
  recommendations: string[];
}

// ============================================================
// SCORING HELPERS
// ============================================================

function scoreTokenCount(count: number): number {
  if (count >= 20) return 30;
  if (count < 5) return 5;
  return Math.round(5 + ((count - 5) / (20 - 5)) * (30 - 5));
}

function scoreCandleCount(count: number): number {
  if (count >= 10000) return 25;
  if (count < 100) return 0;
  return Math.round(((count - 100) / (10000 - 100)) * 25);
}

function scoreRealDataPct(pct: number): number {
  if (pct >= 100) return 25;
  if (pct <= 0) return 5;
  return Math.round(5 + (pct / 100) * (25 - 5));
}

function scoreTimeCoverage(days: number): number {
  if (days >= 90) return 20;
  if (days < 1) return 0;
  return Math.round((days / 90) * 20);
}

function getQualityLevel(score: number): DataQualityReport['level'] {
  if (score >= 90) return 'EXCELLENT';
  if (score >= 70) return 'GOOD';
  if (score >= 50) return 'MARGINAL';
  if (score >= 30) return 'POOR';
  return 'CRITICAL';
}

// ============================================================
// DATA QUALITY GATE CLASS
// ============================================================

export class DataQualityGate {
  private cachedReport: DataQualityReport | null = null;
  private cachedAt: number = 0;
  private readonly CACHE_TTL_MS = 60_000; // 60 seconds
  private _cacheKey: string | null = null;

  /**
   * Main method: assess the quality of data in the database.
   */
  async assessQuality(chain?: string): Promise<DataQualityReport> {
    const cacheKey = chain ?? '__all__';
    if (
      this.cachedReport &&
      cacheKey === this._cacheKey &&
      Date.now() - this.cachedAt < this.CACHE_TTL_MS
    ) {
      return this.cachedReport;
    }

    const tokenWhere = chain ? { chain } : {};

    // Token counts
    const tokenCount = await db.token.count({ where: tokenWhere });

    const candleTokenAddresses = await db.priceCandle.groupBy({
      by: ['tokenAddress'],
      where: chain ? { chain } : undefined,
      _count: { id: true },
    });
    const tokensWithCandles = candleTokenAddresses.length;

    const realCandleTokenAddresses = await db.priceCandle.groupBy({
      by: ['tokenAddress'],
      where: { source: 'coingecko', ...(chain ? { chain } : {}) },
    });
    const tokensWithRealCandles = realCandleTokenAddresses.length;

    const generatedCandleTokenAddresses = await db.priceCandle.groupBy({
      by: ['tokenAddress'],
      where: { source: 'generated', ...(chain ? { chain } : {}) },
    });
    const tokensWithGeneratedCandles = generatedCandleTokenAddresses.length;

    // Candle counts
    const totalCandles = await db.priceCandle.count({
      where: chain ? { chain } : undefined,
    });
    const realCandles = await db.priceCandle.count({
      where: { source: 'coingecko', ...(chain ? { chain } : {}) },
    });
    const generatedCandles = await db.priceCandle.count({
      where: { source: 'generated', ...(chain ? { chain } : {}) },
    });
    const realCandlesPct = totalCandles > 0
      ? Math.round((realCandles / totalCandles) * 1000) / 10
      : 0;

    // Time coverage
    let oldestCandle: Date | null = null;
    let newestCandle: Date | null = null;
    let coverageDays = 0;

    if (totalCandles > 0) {
      const timeRange = await db.priceCandle.aggregate({
        where: chain ? { chain } : undefined,
        _min: { timestamp: true },
        _max: { timestamp: true },
      });
      oldestCandle = timeRange._min.timestamp;
      newestCandle = timeRange._max.timestamp;
      if (oldestCandle && newestCandle) {
        coverageDays = Math.round(
          (newestCandle.getTime() - oldestCandle.getTime()) / (1000 * 60 * 60 * 24),
        );
      }
    }

    // Per-timeframe coverage
    const tfGroups = await db.priceCandle.groupBy({
      by: ['timeframe'],
      where: chain ? { chain } : undefined,
      _count: { id: true },
    });
    const tfTokenCounts = await db.priceCandle.groupBy({
      by: ['timeframe', 'tokenAddress'],
      where: chain ? { chain } : undefined,
    });
    const tfTokenMap = new Map<string, Set<string>>();
    for (const row of tfTokenCounts) {
      const existing = tfTokenMap.get(row.timeframe) ?? new Set<string>();
      existing.add(row.tokenAddress);
      tfTokenMap.set(row.timeframe, existing);
    }
    const timeframeCoverage: Record<string, { count: number; tokens: number }> = {};
    for (const group of tfGroups) {
      timeframeCoverage[group.timeframe] = {
        count: group._count.id,
        tokens: tfTokenMap.get(group.timeframe)?.size ?? 0,
      };
    }

    // Compute scores
    const tokenScore = scoreTokenCount(tokenCount);
    const candleScore = scoreCandleCount(totalCandles);
    const realScore = scoreRealDataPct(realCandlesPct);
    const coverageScore = scoreTimeCoverage(coverageDays);
    const overallScore = Math.min(100, tokenScore + candleScore + realScore + coverageScore);
    const level = getQualityLevel(overallScore);
    const isReady = overallScore >= 50;

    // Warnings and recommendations
    const warnings: string[] = [];
    const recommendations: string[] = [];

    if (tokenCount === 0) {
      warnings.push('No tokens in the database');
      recommendations.push('Run the seed script to populate tokens');
    } else if (tokenCount < 5) {
      warnings.push(`Only ${tokenCount} tokens in the database (minimum 5 recommended)`);
      recommendations.push('Add more tokens via the seed script or data collector');
    }

    if (tokensWithCandles === 0) {
      warnings.push('No tokens have OHLCV candle data');
      recommendations.push('Run the OHLCV pipeline backfill to collect historical price data');
    } else if (tokensWithCandles < tokenCount * 0.5) {
      warnings.push(`Only ${tokensWithCandles}/${tokenCount} tokens have candle data`);
      recommendations.push('Run backfill for tokens missing OHLCV data');
    }

    if (totalCandles === 0) {
      warnings.push('No candle data available');
      recommendations.push('Run the OHLCV pipeline to fetch and store historical candles');
    } else if (totalCandles < 1000) {
      warnings.push(`Only ${totalCandles} candles total (10000+ recommended for reliable backtests)`);
      recommendations.push('Backfill more historical data for better backtest accuracy');
    }

    if (realCandlesPct < 20) {
      warnings.push(`Only ${realCandlesPct}% of candles are from real market data`);
      recommendations.push('Prioritize fetching real OHLCV data from CoinGecko or other sources');
    }

    if (generatedCandles > 0 && totalCandles > 0) {
      const generatedPct = Math.round((generatedCandles / totalCandles) * 100);
      if (generatedPct > 50) {
        warnings.push(`${generatedPct}% of candle data is generated/synthetic`);
        recommendations.push('Replace generated data with real market data for more reliable backtests');
      }
    }

    if (coverageDays < 7) {
      warnings.push(`Only ${coverageDays} days of price data coverage (90+ recommended)`);
      recommendations.push('Backfill historical data to extend time coverage');
    } else if (coverageDays < 30) {
      warnings.push(`${coverageDays} days of coverage - limited for statistical significance`);
      recommendations.push('Extend historical data to 90+ days for robust backtesting');
    }

    if (level === 'CRITICAL') {
      recommendations.push('Do NOT run backtests until data quality improves to MARGINAL or above');
    } else if (level === 'POOR') {
      recommendations.push('Backtest results will be unreliable - improve data quality first');
    } else if (level === 'MARGINAL') {
      recommendations.push('Backtests can run but results should be treated with caution');
    }

    const report: DataQualityReport = {
      overallScore, isReady, level,
      tokenCount, tokensWithCandles, tokensWithRealCandles, tokensWithGeneratedCandles,
      totalCandles, realCandlesPct,
      oldestCandle, newestCandle, coverageDays,
      timeframeCoverage,
      warnings, recommendations,
    };

    this.cachedReport = report;
    this.cachedAt = Date.now();
    this._cacheKey = cacheKey;

    return report;
  }

  /**
   * Quick check: is the data ready for backtesting?
   */
  async isReadyForBacktest(minCandles: number = 500): Promise<boolean> {
    const report = await this.assessQuality();
    return report.isReady && report.totalCandles >= minCandles;
  }

  /**
   * Get a full quality report.
   */
  async getQualityReport(): Promise<DataQualityReport> {
    return this.assessQuality();
  }

  /**
   * Invalidate the cache.
   */
  invalidateCache(): void {
    this.cachedReport = null;
    this.cachedAt = 0;
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const dataQualityGate = new DataQualityGate();
