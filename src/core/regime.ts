/**
 * PPMT - Market Regime Detector
 *
 * Classifies the current market state for Level 4 grouping
 * and for the regime filter in the trading system.
 *
 * Uses ATR (volatility), ADX (trend strength), and volume
 * to classify into: EXPANSION, COMPRESSION, TRENDING_UP,
 * TRENDING_DOWN, LATERAL, TRANSITION.
 */

import { Candle, MarketRegime } from './types';

export interface RegimeConfig {
  /** ATR period for volatility measurement */
  atrPeriod: number;
  /** ADX period for trend strength */
  adxPeriod: number;
  /** Volume moving average period */
  volumePeriod: number;
  /** Threshold for expansion vs compression (ATR percentile) */
  expansionThreshold: number;
  /** Threshold for trend vs lateral (ADX threshold) */
  trendThreshold: number;
}

const DEFAULT_REGIME_CONFIG: RegimeConfig = {
  atrPeriod: 14,
  adxPeriod: 14,
  volumePeriod: 20,
  expansionThreshold: 0.7,   // 70th percentile = expansion
  trendThreshold: 25,         // ADX > 25 = trending
};

export class RegimeDetector {
  private config: RegimeConfig;
  private atrHistory: number[] = [];
  private volumeHistory: number[] = [];

  constructor(config?: Partial<RegimeConfig>) {
    this.config = { ...DEFAULT_REGIME_CONFIG, ...config };
  }

  /**
   * Detect the current market regime from recent candles.
   *
   * Algorithm:
   * 1. Calculate ATR → determine if volatility is expanding or compressing
   * 2. Calculate ADX → determine if there's a trend
   * 3. Calculate volume relative to average → confirm or deny
   * 4. Classify into one of 6 regimes
   */
  detect(candles: Candle[]): MarketRegime {
    if (candles.length < Math.max(this.config.atrPeriod, this.config.adxPeriod, this.config.volumePeriod) + 1) {
      return MarketRegime.TRANSITION; // Not enough data
    }

    const atr = this.calculateATR(candles, this.config.atrPeriod);
    const adx = this.calculateADX(candles, this.config.adxPeriod);
    const volumeRatio = this.calculateVolumeRatio(candles, this.config.volumePeriod);

    // Track ATR history for percentile calculation
    this.atrHistory.push(atr);
    if (this.atrHistory.length > 100) this.atrHistory.shift();

    // Calculate ATR percentile
    const atrPercentile = this.percentile(this.atrHistory, atr);

    // Classification logic
    const isHighVolatility = atrPercentile >= this.config.expansionThreshold;
    const isLowVolatility = atrPercentile <= (1 - this.config.expansionThreshold);
    const isTrending = adx > this.config.trendThreshold;
    const isHighVolume = volumeRatio > 1.3;

    // Recent price direction for trend classification
    const recentCandles = candles.slice(-5);
    const priceChange = (recentCandles[recentCandles.length - 1].close - recentCandles[0].open) / recentCandles[0].open;
    const isUp = priceChange > 0.01;
    const isDown = priceChange < -0.01;

    if (isHighVolatility && isTrending) {
      return isUp ? MarketRegime.EXPANSION : MarketRegime.COMPRESSION;
    }

    if (isTrending && isHighVolume) {
      return isUp ? MarketRegime.TRENDING_UP : MarketRegime.TRENDING_DOWN;
    }

    if (isLowVolatility && !isTrending) {
      return MarketRegime.LATERAL;
    }

    if (isTrending && !isHighVolume) {
      return isUp ? MarketRegime.TRENDING_UP : MarketRegime.TRENDING_DOWN;
    }

    return MarketRegime.TRANSITION;
  }

  /**
   * Calculate Average True Range (ATR).
   * Measures market volatility.
   */
  private calculateATR(candles: Candle[], period: number): number {
    const trueRanges: number[] = [];

    for (let i = 1; i < candles.length; i++) {
      const high = candles[i].high;
      const low = candles[i].low;
      const prevClose = candles[i - 1].close;

      const tr = Math.max(
        high - low,
        Math.abs(high - prevClose),
        Math.abs(low - prevClose)
      );
      trueRanges.push(tr);
    }

    // Use last 'period' true ranges
    const recent = trueRanges.slice(-period);
    return recent.reduce((a, b) => a + b, 0) / recent.length;
  }

  /**
   * Calculate Average Directional Index (ADX).
   * Measures trend strength (not direction).
   * ADX > 25 = trending, ADX < 20 = lateral.
   *
   * Simplified implementation for performance.
   */
  private calculateADX(candles: Candle[], period: number): number {
    if (candles.length < period + 1) return 0;

    let plusDM = 0;
    let minusDM = 0;
    let tr = 0;

    for (let i = candles.length - period; i < candles.length; i++) {
      const high = candles[i].high;
      const low = candles[i].low;
      const prevHigh = candles[i - 1].high;
      const prevLow = candles[i - 1].low;
      const prevClose = candles[i - 1].close;

      const upMove = high - prevHigh;
      const downMove = prevLow - low;

      if (upMove > downMove && upMove > 0) plusDM += upMove;
      if (downMove > upMove && downMove > 0) minusDM += downMove;

      tr += Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
    }

    if (tr === 0) return 0;

    const plusDI = (plusDM / tr) * 100;
    const minusDI = (minusDM / tr) * 100;
    const dx = Math.abs(plusDI - minusDI) / (plusDI + minusDI) * 100;

    return dx;
  }

  /**
   * Calculate current volume relative to moving average.
   * > 1 = above average volume, < 1 = below average.
   */
  private calculateVolumeRatio(candles: Candle[], period: number): number {
    const recent = candles.slice(-period);
    const avgVolume = recent.reduce((a, c) => a + c.volume, 0) / recent.length;
    const currentVolume = candles[candles.length - 1].volume;

    return avgVolume > 0 ? currentVolume / avgVolume : 1;
  }

  /**
   * Calculate percentile of a value in an array.
   */
  private percentile(arr: number[], value: number): number {
    if (arr.length === 0) return 0.5;
    const below = arr.filter(v => v < value).length;
    return below / arr.length;
  }
}
