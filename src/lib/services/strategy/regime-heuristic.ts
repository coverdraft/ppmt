/**
 * Regime Heuristic — CryptoQuant Terminal
 *
 * Simple regime detection using:
 *   1. Rolling volatility percentile (25th/75th)
 *   2. Trend direction (MA slopes)
 *
 * No GARCH, no 3-layer system. Just enough to inform the SDE.
 * From Revisión 7: "Heurística simple vol+trend" is sufficient for v1.
 *
 * Regimes:
 *   TRENDING_UP    — short MA > long MA, vol not extreme
 *   TRENDING_DOWN  — short MA < long MA, vol not extreme
 *   SIDEWAYS       — MAs are flat (no clear trend)
 *   HIGH_VOLATILITY — vol percentile > 75
 *   LOW_VOLATILITY  — vol percentile < 25
 *
 * Priority: HIGH_VOLATILITY > LOW_VOLATILITY > TRENDING_* > SIDEWAYS
 */

import { db } from '../../db';

// ============================================================
// TYPES
// ============================================================

export type MarketRegime = 'TRENDING_UP' | 'TRENDING_DOWN' | 'SIDEWAYS' | 'HIGH_VOLATILITY' | 'LOW_VOLATILITY';

export interface RegimeAssessment {
  regime: MarketRegime;
  confidence: number; // 0-1
  volatilityPercentile: number; // 0-100
  trendDirection: 'UP' | 'DOWN' | 'FLAT';
  trendStrength: number; // 0-1
  details: {
    shortMA: number; // 7-period MA
    longMA: number;  // 25-period MA
    volShort: number; // 7-period vol
    volLong: number;  // 25-period vol
    volRatio: number; // volShort / volLong
  };
  timestamp: Date;
}

interface TrendResult {
  direction: 'UP' | 'DOWN' | 'FLAT';
  strength: number; // 0-1
}

// ============================================================
// REGIME HEURISTIC CLASS
// ============================================================

class RegimeHeuristic {
  /**
   * Assess current market regime from price data.
   *
   * @param prices - Array of closing prices (most recent last).
   *                 Should be at least 25 data points for meaningful calculation.
   *                 If less than 25, returns SIDEWAYS with low confidence.
   */
  assessRegime(prices: number[]): RegimeAssessment {
    const MIN_PRICES = 25;

    // Insufficient data — return conservative default
    if (prices.length < MIN_PRICES) {
      return {
        regime: 'SIDEWAYS',
        confidence: Math.max(0.1, prices.length / MIN_PRICES * 0.3),
        volatilityPercentile: 50,
        trendDirection: 'FLAT',
        trendStrength: 0,
        details: {
          shortMA: prices.length > 0 ? prices[prices.length - 1] : 0,
          longMA: prices.length > 0 ? prices[prices.length - 1] : 0,
          volShort: 0,
          volLong: 0,
          volRatio: 1,
        },
        timestamp: new Date(),
      };
    }

    // Step 1: Compute short and long moving averages
    const shortPeriod = 7;
    const longPeriod = 25;

    const shortMA = this.computeMA(prices, shortPeriod);
    const longMA = this.computeMA(prices, longPeriod);

    // Step 2: Compute trend direction and strength
    const trend = this.computeTrend(prices, shortPeriod, longPeriod);

    // Step 3: Compute rolling volatility
    const volShort = this.computeRollingVol(prices, shortPeriod);
    const volLong = this.computeRollingVol(prices, longPeriod);

    // Step 4: Compute volatility percentile
    const volPercentile = this.computeVolPercentile(prices);

    // Step 5: Compute vol ratio (short/long)
    const volRatio = volLong > 0 ? volShort / volLong : 1;

    // Step 6: Classify regime
    const regime = this.classifyRegime(volPercentile, trend);

    // Step 7: Compute confidence
    const confidence = this.computeConfidence(regime, volPercentile, trend, volRatio);

    return {
      regime,
      confidence: Math.round(confidence * 100) / 100,
      volatilityPercentile: Math.round(volPercentile * 100) / 100,
      trendDirection: trend.direction,
      trendStrength: Math.round(trend.strength * 100) / 100,
      details: {
        shortMA: Math.round(shortMA * 10000) / 10000,
        longMA: Math.round(longMA * 10000) / 10000,
        volShort: Math.round(volShort * 10000) / 10000,
        volLong: Math.round(volLong * 10000) / 10000,
        volRatio: Math.round(volRatio * 10000) / 10000,
      },
      timestamp: new Date(),
    };
  }

  /**
   * Get regime from DB token data (convenience method).
   * Loads PriceCandle data for the token and assesses regime.
   */
  async assessRegimeFromDB(tokenAddress: string, chain: string = 'SOL'): Promise<RegimeAssessment> {
    try {
      // Get recent price candles for this token
      const candles = await db.priceCandle.findMany({
        where: {
          tokenAddress,
          chain,
          timeframe: '4h', // Use 4h candles for regime assessment
        },
        orderBy: { timestamp: 'desc' },
        take: 100, // Need enough for 25-period lookback + volatility percentile + buffer
      });

      if (candles.length < 5) {
        // Try with 1h candles if 4h not available
        const hourlyCandles = await db.priceCandle.findMany({
          where: {
            tokenAddress,
            chain,
            timeframe: '1h',
          },
          orderBy: { timestamp: 'desc' },
          take: 200,
        });

        if (hourlyCandles.length < 5) {
          return {
            regime: 'SIDEWAYS',
            confidence: 0.1,
            volatilityPercentile: 50,
            trendDirection: 'FLAT',
            trendStrength: 0,
            details: {
              shortMA: 0,
              longMA: 0,
              volShort: 0,
              volLong: 0,
              volRatio: 1,
            },
            timestamp: new Date(),
          };
        }

        // Reverse to get chronological order (oldest first)
        const prices = [...hourlyCandles].reverse().map(c => c.close);
        return this.assessRegime(prices);
      }

      // Reverse to get chronological order (oldest first)
      const prices = [...candles].reverse().map(c => c.close);
      return this.assessRegime(prices);
    } catch (error) {
      console.error('[RegimeHeuristic] Error assessing regime from DB:', error);
      return {
        regime: 'SIDEWAYS',
        confidence: 0.1,
        volatilityPercentile: 50,
        trendDirection: 'FLAT',
        trendStrength: 0,
        details: {
          shortMA: 0,
          longMA: 0,
          volShort: 0,
          volLong: 0,
          volRatio: 1,
        },
        timestamp: new Date(),
      };
    }
  }

  // ============================================================
  // PRIVATE HELPERS
  // ============================================================

  /**
   * Compute rolling volatility (standard deviation of returns).
   */
  private computeRollingVol(prices: number[], period: number): number {
    if (prices.length < period + 1) {
      // Not enough data for the full period — use what we have
      const availableReturns = this.computeReturns(prices);
      if (availableReturns.length < 2) return 0;
      return this.stdDev(availableReturns);
    }

    // Use the last `period` prices to compute returns
    const recentPrices = prices.slice(-(period + 1));
    const returns = this.computeReturns(recentPrices);
    return this.stdDev(returns);
  }

  /**
   * Compute returns from a price array.
   */
  private computeReturns(prices: number[]): number[] {
    const returns: number[] = [];
    for (let i = 1; i < prices.length; i++) {
      if (prices[i - 1] > 0) {
        returns.push((prices[i] - prices[i - 1]) / prices[i - 1]);
      }
    }
    return returns;
  }

  /**
   * Compute simple moving average of the last `period` prices.
   */
  private computeMA(prices: number[], period: number): number {
    if (prices.length < period) {
      // Not enough data — use what we have
      const available = prices.slice(-Math.min(prices.length, period));
      return available.reduce((s, v) => s + v, 0) / available.length;
    }

    const recentPrices = prices.slice(-period);
    return recentPrices.reduce((s, v) => s + v, 0) / period;
  }

  /**
   * Compute trend direction and strength from MA comparison.
   *
   * Trend direction: shortMA vs longMA
   *   UP:   shortMA > longMA by at least 0.5%
   *   DOWN: shortMA < longMA by at least 0.5%
   *   FLAT: otherwise
   *
   * Trend strength: |shortMA - longMA| / longMA
   *   Normalized to 0-1 range, capped at 10% divergence
   */
  private computeTrend(prices: number[], shortPeriod: number, longPeriod: number): TrendResult {
    const shortMA = this.computeMA(prices, shortPeriod);
    const longMA = this.computeMA(prices, longPeriod);

    if (longMA <= 0) {
      return { direction: 'FLAT', strength: 0 };
    }

    const divergence = (shortMA - longMA) / longMA;

    // Threshold: 0.5% divergence to confirm a trend
    const trendThreshold = 0.005;

    let direction: 'UP' | 'DOWN' | 'FLAT';
    if (divergence > trendThreshold) {
      direction = 'UP';
    } else if (divergence < -trendThreshold) {
      direction = 'DOWN';
    } else {
      direction = 'FLAT';
    }

    // Strength: absolute divergence, capped at 10% = strength 1.0
    const strength = Math.min(1, Math.abs(divergence) / 0.10);

    return { direction, strength };
  }

  /**
   * Compute volatility percentile.
   *
   * Compare current short-term volatility against the distribution
   * of all rolling volatilities over the price history.
   * Returns 0-100 percentile.
   */
  private computeVolPercentile(prices: number[]): number {
    if (prices.length < 10) return 50;

    // Compute rolling volatilities over the entire price history
    const windowSize = 7;
    const rollingVols: number[] = [];

    for (let i = windowSize; i < prices.length; i++) {
      const windowPrices = prices.slice(i - windowSize, i + 1);
      const returns = this.computeReturns(windowPrices);
      if (returns.length >= 2) {
        rollingVols.push(this.stdDev(returns));
      }
    }

    if (rollingVols.length < 3) return 50;

    // Current volatility is the most recent rolling vol
    const currentVol = rollingVols[rollingVols.length - 1];

    // Count how many historical vols are below the current vol
    const belowCount = rollingVols.slice(0, -1).filter(v => v < currentVol).length;
    const totalHistorical = rollingVols.length - 1;

    if (totalHistorical === 0) return 50;

    return (belowCount / totalHistorical) * 100;
  }

  /**
   * Map vol percentile + trend to regime.
   *
   * Priority rules:
   *   volPercentile > 75 → HIGH_VOLATILITY (regardless of trend)
   *   volPercentile < 25 → LOW_VOLATILITY (regardless of trend)
   *   trend UP + vol normal → TRENDING_UP
   *   trend DOWN + vol normal → TRENDING_DOWN
   *   else → SIDEWAYS
   */
  private classifyRegime(volPercentile: number, trend: TrendResult): MarketRegime {
    // Extreme volatility overrides trend
    if (volPercentile > 75) return 'HIGH_VOLATILITY';
    if (volPercentile < 25) return 'LOW_VOLATILITY';

    // Normal volatility — use trend direction
    if (trend.direction === 'UP') return 'TRENDING_UP';
    if (trend.direction === 'DOWN') return 'TRENDING_DOWN';

    return 'SIDEWAYS';
  }

  /**
   * Compute confidence for the regime assessment.
   *
   * - HIGH_VOLATILITY: high confidence if vol is very high (>90)
   * - LOW_VOLATILITY: high confidence if vol is very low (<10)
   * - TRENDING: confidence based on trend strength
   * - SIDEWAYS: low confidence (it's the "uncertain" regime)
   */
  private computeConfidence(
    regime: MarketRegime,
    volPercentile: number,
    trend: TrendResult,
    volRatio: number,
  ): number {
    switch (regime) {
      case 'HIGH_VOLATILITY': {
        // More extreme vol = more confident
        const volConfidence = Math.min(1, (volPercentile - 75) / 25);
        // If vol is accelerating (ratio > 1), more confident
        const accelBonus = volRatio > 1.2 ? 0.1 : 0;
        return Math.min(1, 0.6 + volConfidence * 0.3 + accelBonus);
      }
      case 'LOW_VOLATILITY': {
        // Lower vol = more confident
        const volConfidence = Math.min(1, (25 - volPercentile) / 25);
        return Math.min(1, 0.6 + volConfidence * 0.3);
      }
      case 'TRENDING_UP':
      case 'TRENDING_DOWN': {
        // Stronger trend = more confident
        return Math.min(1, 0.4 + trend.strength * 0.5);
      }
      case 'SIDEWAYS':
      default: {
        // SIDEWAYS is inherently uncertain
        return Math.min(0.5, 0.2 + (1 - trend.strength) * 0.2);
      }
    }
  }

  /**
   * Standard deviation of an array of numbers.
   */
  private stdDev(values: number[]): number {
    if (values.length < 2) return 0;
    const n = values.length;
    const mean = values.reduce((s, v) => s + v, 0) / n;
    const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1);
    return Math.sqrt(variance);
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const regimeHeuristic = new RegimeHeuristic();
