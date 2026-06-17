/**
 * Feature Store - CryptoQuant Terminal
 *
 * Sits between raw data ingestion (OHLCVPipeline) and the Brain analysis pipeline.
 * Computes features ONCE, caches them, and serves them to all consumers.
 *
 * Components:
 *   FeatureEngine  - Computes raw features from OHLCV + on-chain data
 *   FeatureStore    - Cache layer with TTL and versioning
 *   FeatureCatalog  - Metadata and lineage tracking
 *
 * Exported singletons: featureEngine, featureStore, featureCatalog
 */

import { db } from '@/lib/db';
import { OHLCVPipeline, type PriceCandleRow } from '@/lib/services/data-sources/ohlcv-pipeline';
import {
  type FeatureName,
  type FeatureCategory,
  type FeatureValue,
  type FeatureSet,
  type FeatureVector,
  type FeatureCacheEntry,
  type FeatureCacheStats,
  type FeatureTTLConfig,
  type FeatureLineage,
  type FeatureDefinition,
  type FeatureComputationInput,
  type OHLCVBar,
  type OnChainData,
  type LiquidityData,
  type SentimentData,
  type PointInTimeFeatureSet,
  ALL_FEATURE_NAMES,
  FEATURE_CATEGORY_MAP,
  DEFAULT_FEATURE_TTLS,
  FEATURE_STORE_VERSION,
  TOTAL_FEATURE_COUNT,
} from './types';

// ============================================================
// SHARED OHLCV PIPELINE INSTANCE
// ============================================================

const ohlcvPipeline = new OHLCVPipeline();

// ============================================================
// MATH UTILITIES
// ============================================================

/** Simple Moving Average — returns array same length as input (NaN for initial values) */
function sma(data: number[], period: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(NaN);
      continue;
    }
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) {
      sum += data[j];
    }
    result.push(sum / period);
  }
  return result;
}

/** Exponential Moving Average */
function ema(data: number[], period: number): number[] {
  const result: number[] = [];
  const multiplier = 2 / (period + 1);

  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      sum += data[i];
      result.push(NaN);
      continue;
    }
    if (i === period - 1) {
      sum += data[i];
      result.push(sum / period);
      continue;
    }
    result.push((data[i] - result[i - 1]) * multiplier + result[i - 1]);
  }
  return result;
}

/** Last valid value from an indicator array */
function lastValid(arr: number[]): number {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (!isNaN(arr[i])) return arr[i];
  }
  return NaN;
}

/** Compute True Range array from OHLCV bars */
function trueRange(bars: OHLCVBar[]): number[] {
  const result: number[] = [];
  for (let i = 0; i < bars.length; i++) {
    if (i === 0) {
      result.push(bars[i].high - bars[i].low);
      continue;
    }
    const hl = bars[i].high - bars[i].low;
    const hc = Math.abs(bars[i].high - bars[i - 1].close);
    const lc = Math.abs(bars[i].low - bars[i - 1].close);
    result.push(Math.max(hl, hc, lc));
  }
  return result;
}

/** Compute data quality score based on available data points vs required */
function qualityScore(available: number, required: number): number {
  if (available >= required) return 1.0;
  if (available <= 0) return 0.0;
  // Partial data: proportional quality, minimum 0.3 if we have at least half
  const ratio = available / required;
  return Math.max(0, Math.min(1, ratio));
}

/** Create a FeatureValue with quality based on NaN check */
function featureVal(value: number, source: string, quality: number): FeatureValue {
  return {
    value: isNaN(value) ? 0 : value,
    timestamp: Date.now(),
    quality: isNaN(value) ? 0 : quality,
    source,
  };
}

// ============================================================
// FEATURE ENGINE — Computes raw features
// ============================================================

export class FeatureEngine {
  private readonly SOURCE = 'feature-engine';

  /**
   * Compute all features for a token from raw OHLCV + supplementary data.
   */
  async computeAll(input: FeatureComputationInput): Promise<FeatureSet> {
    const features: Record<string, FeatureValue> = {} as Record<FeatureName, FeatureValue>;

    // --- TECHNICAL FEATURES (22) ---
    const techFeatures = this.computeTechnicalFeatures(input);
    for (const [name, fv] of Object.entries(techFeatures)) {
      features[name] = fv;
    }

    // --- VOLATILITY FEATURES (5) ---
    const volFeatures = this.computeVolatilityFeatures(input);
    for (const [name, fv] of Object.entries(volFeatures)) {
      features[name] = fv;
    }

    // --- VOLUME FEATURES (4) ---
    const volProfileFeatures = this.computeVolumeFeatures(input);
    for (const [name, fv] of Object.entries(volProfileFeatures)) {
      features[name] = fv;
    }

    // --- ON-CHAIN FEATURES (6) ---
    const onChainFeatures = this.computeOnChainFeatures(input);
    for (const [name, fv] of Object.entries(onChainFeatures)) {
      features[name] = fv;
    }

    // --- LIQUIDITY FEATURES (3) ---
    const liqFeatures = this.computeLiquidityFeatures(input);
    for (const [name, fv] of Object.entries(liqFeatures)) {
      features[name] = fv;
    }

    // --- SENTIMENT FEATURES (3) ---
    const sentFeatures = this.computeSentimentFeatures(input);
    for (const [name, fv] of Object.entries(sentFeatures)) {
      features[name] = fv;
    }

    // Compute overall quality
    const allValues = Object.values(features);
    const overallQuality = allValues.length > 0
      ? allValues.reduce((sum, fv) => sum + fv.quality, 0) / allValues.length
      : 0;

    return {
      tokenAddress: input.tokenAddress,
      chain: input.chain,
      computedAt: input.computedAt || Date.now(),
      version: FEATURE_STORE_VERSION,
      features: features as Record<FeatureName, FeatureValue>,
      featureCount: Object.keys(features).length,
      overallQuality,
    };
  }

  /**
   * Compute a single feature by name (for lazy evaluation).
   */
  computeFeature(name: FeatureName, input: FeatureComputationInput): FeatureValue {
    const category = FEATURE_CATEGORY_MAP[name];

    switch (category) {
      case 'technical': return this.computeTechnicalFeatures(input)[name] ?? featureVal(NaN, this.SOURCE, 0);
      case 'volatility': return this.computeVolatilityFeatures(input)[name] ?? featureVal(NaN, this.SOURCE, 0);
      case 'volume': return this.computeVolumeFeatures(input)[name] ?? featureVal(NaN, this.SOURCE, 0);
      case 'on-chain': return this.computeOnChainFeatures(input)[name] ?? featureVal(NaN, this.SOURCE, 0);
      case 'liquidity': return this.computeLiquidityFeatures(input)[name] ?? featureVal(NaN, this.SOURCE, 0);
      case 'sentiment': return this.computeSentimentFeatures(input)[name] ?? featureVal(NaN, this.SOURCE, 0);
      default: return featureVal(NaN, this.SOURCE, 0);
    }
  }

  // ----------------------------------------------------------
  // TECHNICAL FEATURES (22)
  // ----------------------------------------------------------

  private computeTechnicalFeatures(input: FeatureComputationInput): Record<string, FeatureValue> {
    const bars = input.ohlcvBars;
    const closes = bars.map(b => b.close);
    const highs = bars.map(b => b.high);
    const lows = bars.map(b => b.low);
    const volumes = bars.map(b => b.volume);
    const src = `${this.SOURCE}:technical`;
    const result: Record<string, FeatureValue> = {};

    // --- RSI(14) ---
    {
      const period = 14;
      const gains: number[] = [];
      const losses: number[] = [];
      const rsiValues: number[] = [];

      for (let i = 0; i < closes.length; i++) {
        if (i === 0) {
          rsiValues.push(NaN);
          continue;
        }
        const change = closes[i] - closes[i - 1];
        gains.push(change > 0 ? change : 0);
        losses.push(change < 0 ? Math.abs(change) : 0);

        if (i < period) {
          rsiValues.push(NaN);
          continue;
        }

        if (i === period) {
          const avgGain = gains.slice(0, period).reduce((a, b) => a + b, 0) / period;
          const avgLoss = losses.slice(0, period).reduce((a, b) => a + b, 0) / period;
          if (avgLoss === 0) { rsiValues.push(100); continue; }
          const rs = avgGain / avgLoss;
          rsiValues.push(100 - (100 / (1 + rs)));
          continue;
        }

        // Wilder's smoothing
        const recentGains = gains.slice(-period);
        const recentLosses = losses.slice(-period);
        const avgGain = recentGains.reduce((a, b) => a + b, 0) / period;
        const avgLoss = recentLosses.reduce((a, b) => a + b, 0) / period;
        if (avgLoss === 0) { rsiValues.push(100); continue; }
        const rs = avgGain / avgLoss;
        rsiValues.push(100 - (100 / (1 + rs)));
      }

      const rsiValue = lastValid(rsiValues);
      result['rsi_14'] = featureVal(rsiValue, src, qualityScore(bars.length, period + 1));
    }

    // --- Simple Moving Averages: 7, 25, 50, 200 ---
    const ma7 = sma(closes, 7);
    const ma25 = sma(closes, 25);
    const ma50 = sma(closes, 50);
    const ma200 = sma(closes, 200);

    result['ma_7'] = featureVal(lastValid(ma7), src, qualityScore(bars.length, 7));
    result['ma_25'] = featureVal(lastValid(ma25), src, qualityScore(bars.length, 25));
    result['ma_50'] = featureVal(lastValid(ma50), src, qualityScore(bars.length, 50));
    result['ma_200'] = featureVal(lastValid(ma200), src, qualityScore(bars.length, 200));

    // --- EMA(12) and EMA(26) ---
    const ema12 = ema(closes, 12);
    const ema26 = ema(closes, 26);

    result['ema_12'] = featureVal(lastValid(ema12), src, qualityScore(bars.length, 12));
    result['ema_26'] = featureVal(lastValid(ema26), src, qualityScore(bars.length, 26));

    // --- Bollinger Bands (20-period, 2 stddev) ---
    {
      const period = 20;
      const middle = sma(closes, period);
      let upper = NaN, lower = NaN, bandwidth = NaN, percentB = NaN;

      const lastIdx = closes.length - 1;
      if (!isNaN(middle[lastIdx])) {
        let sumSq = 0;
        for (let j = lastIdx - period + 1; j <= lastIdx; j++) {
          sumSq += (closes[j] - middle[lastIdx]) ** 2;
        }
        const stdDev = Math.sqrt(sumSq / period);
        upper = middle[lastIdx] + 2 * stdDev;
        lower = middle[lastIdx] - 2 * stdDev;
        bandwidth = upper !== middle[lastIdx] ? ((upper - lower) / middle[lastIdx]) * 100 : 0;
        percentB = upper !== lower ? (closes[lastIdx] - lower) / (upper - lower) : 0.5;
      }

      result['bollinger_upper'] = featureVal(upper, src, qualityScore(bars.length, period));
      result['bollinger_middle'] = featureVal(lastValid(middle), src, qualityScore(bars.length, period));
      result['bollinger_lower'] = featureVal(lower, src, qualityScore(bars.length, period));
      result['bollinger_bandwidth'] = featureVal(bandwidth, src, qualityScore(bars.length, period));
      result['bollinger_percent_b'] = featureVal(percentB, src, qualityScore(bars.length, period));
    }

    // --- ATR(14) ---
    {
      const period = 14;
      const tr = trueRange(bars);
      const atrValues = sma(tr, period);
      const atrValue = lastValid(atrValues);
      // Normalize ATR as percentage of price
      const lastClose = closes[closes.length - 1] || 1;
      const atrPct = !isNaN(atrValue) && lastClose > 0 ? (atrValue / lastClose) * 100 : NaN;
      result['atr_14'] = featureVal(atrPct, src, qualityScore(bars.length, period + 1));
    }

    // --- MACD (12, 26, 9) ---
    {
      const emaFast = ema(closes, 12);
      const emaSlow = ema(closes, 26);

      const macdLine: number[] = [];
      for (let i = 0; i < closes.length; i++) {
        if (isNaN(emaFast[i]) || isNaN(emaSlow[i])) {
          macdLine.push(NaN);
        } else {
          macdLine.push(emaFast[i] - emaSlow[i]);
        }
      }

      // Signal line = EMA(9) of MACD line
      const validMacd = macdLine.filter(v => !isNaN(v));
      const signalEma = ema(validMacd, 9);

      const signalLine: number[] = [];
      let validIdx = 0;
      for (let i = 0; i < macdLine.length; i++) {
        if (isNaN(macdLine[i])) {
          signalLine.push(NaN);
        } else {
          signalLine.push(signalEma[validIdx] ?? NaN);
          validIdx++;
        }
      }

      const histogram: number[] = [];
      for (let i = 0; i < macdLine.length; i++) {
        if (isNaN(macdLine[i]) || isNaN(signalLine[i])) {
          histogram.push(NaN);
        } else {
          histogram.push(macdLine[i] - signalLine[i]);
        }
      }

      const lastClose = closes[closes.length - 1] || 1;
      // Normalize MACD as percentage of price for cross-asset comparability
      result['macd_line'] = featureVal(lastValid(macdLine) / lastClose * 100, src, qualityScore(bars.length, 26 + 9));
      result['macd_signal'] = featureVal(lastValid(signalLine) / lastClose * 100, src, qualityScore(bars.length, 26 + 9));
      result['macd_histogram'] = featureVal(lastValid(histogram) / lastClose * 100, src, qualityScore(bars.length, 26 + 9));
    }

    // --- Stochastic Oscillator (14, 3) ---
    {
      const kPeriod = 14;
      const dPeriod = 3;
      const kValues: number[] = [];

      for (let i = 0; i < bars.length; i++) {
        if (i < kPeriod - 1) {
          kValues.push(NaN);
          continue;
        }
        let highestHigh = -Infinity;
        let lowestLow = Infinity;
        for (let j = i - kPeriod + 1; j <= i; j++) {
          highestHigh = Math.max(highestHigh, highs[j]);
          lowestLow = Math.min(lowestLow, lows[j]);
        }
        const range = highestHigh - lowestLow;
        kValues.push(range === 0 ? 50 : ((closes[i] - lowestLow) / range) * 100);
      }

      const dValues = sma(kValues.filter(v => !isNaN(v)), dPeriod);
      // Map back
      const dFull: number[] = [];
      let dIdx = 0;
      for (let i = 0; i < kValues.length; i++) {
        if (isNaN(kValues[i])) {
          dFull.push(NaN);
        } else {
          dFull.push(dValues[dIdx] ?? NaN);
          dIdx++;
        }
      }

      result['stochastic_k'] = featureVal(lastValid(kValues), src, qualityScore(bars.length, kPeriod));
      result['stochastic_d'] = featureVal(lastValid(dFull), src, qualityScore(bars.length, kPeriod + dPeriod));
    }

    // --- ADX (14) ---
    {
      const period = 14;
      if (bars.length < period * 2) {
        result['adx'] = featureVal(NaN, src, 0);
      } else {
        const plusDM: number[] = [];
        const minusDM: number[] = [];
        const tr = trueRange(bars);

        for (let i = 0; i < bars.length; i++) {
          if (i === 0) {
            plusDM.push(0);
            minusDM.push(0);
            continue;
          }
          const upMove = highs[i] - highs[i - 1];
          const downMove = lows[i - 1] - lows[i];
          plusDM.push(upMove > downMove && upMove > 0 ? upMove : 0);
          minusDM.push(downMove > upMove && downMove > 0 ? downMove : 0);
        }

        const smoothPlusDM = sma(plusDM, period);
        const smoothMinusDM = sma(minusDM, period);
        const smoothTR = sma(tr, period);

        const dx: number[] = [];
        for (let i = 0; i < bars.length; i++) {
          if (isNaN(smoothPlusDM[i]) || isNaN(smoothMinusDM[i]) || isNaN(smoothTR[i]) || smoothTR[i] === 0) {
            dx.push(NaN);
            continue;
          }
          const plusDI = (smoothPlusDM[i] / smoothTR[i]) * 100;
          const minusDI = (smoothMinusDM[i] / smoothTR[i]) * 100;
          const diSum = plusDI + minusDI;
          dx.push(diSum === 0 ? 0 : (Math.abs(plusDI - minusDI) / diSum) * 100);
        }

        const adxValues = sma(dx.filter(v => !isNaN(v)), period);
        result['adx'] = featureVal(lastValid(adxValues), src, qualityScore(bars.length, period * 2));
      }
    }

    // --- CCI (Commodity Channel Index, 20-period) ---
    {
      const period = 20;
      const typicalPrices = bars.map((b, i) => (highs[i] + lows[i] + closes[i]) / 3);
      const tpSma = sma(typicalPrices, period);

      let cciValue = NaN;
      const lastIdx = typicalPrices.length - 1;
      if (!isNaN(tpSma[lastIdx])) {
        let meanDev = 0;
        for (let j = lastIdx - period + 1; j <= lastIdx; j++) {
          meanDev += Math.abs(typicalPrices[j] - tpSma[lastIdx]);
        }
        meanDev /= period;
        cciValue = meanDev === 0 ? 0 : (typicalPrices[lastIdx] - tpSma[lastIdx]) / (0.015 * meanDev);
      }

      result['cci'] = featureVal(cciValue, src, qualityScore(bars.length, period));
    }

    // --- OBV (On-Balance Volume) ---
    {
      // OBV as a ratio: current OBV normalized by dividing by 20-period average absolute volume
      let obv = 0;
      for (let i = 1; i < closes.length; i++) {
        if (closes[i] > closes[i - 1]) obv += volumes[i];
        else if (closes[i] < closes[i - 1]) obv -= volumes[i];
      }

      const avgVol = volumes.length >= 20
        ? volumes.slice(-20).reduce((a, b) => a + Math.abs(b), 0) / 20
        : volumes.reduce((a, b) => a + Math.abs(b), 0) / Math.max(1, volumes.length);

      const obvRatio = avgVol > 0 ? obv / (avgVol * Math.max(1, closes.length)) : 0;
      result['obv'] = featureVal(obvRatio, src, qualityScore(bars.length, 2));
    }

    // --- VWAP (Volume Weighted Average Price) ---
    {
      // VWAP relative to close: (VWAP - close) / close
      let cumTypicalVol = 0;
      let cumVolume = 0;
      // Use last 24 bars for VWAP (intraday convention)
      const startIdx = Math.max(0, bars.length - 24);
      for (let i = startIdx; i < bars.length; i++) {
        const typicalPrice = (highs[i] + lows[i] + closes[i]) / 3;
        cumTypicalVol += typicalPrice * volumes[i];
        cumVolume += volumes[i];
      }
      const vwap = cumVolume === 0 ? closes[closes.length - 1] : cumTypicalVol / cumVolume;
      const lastClose = closes[closes.length - 1] || 1;
      const vwapDeviation = ((vwap - lastClose) / lastClose) * 100;

      result['vwap'] = featureVal(vwapDeviation, src, qualityScore(bars.length, 2));
    }

    return result;
  }

  // ----------------------------------------------------------
  // VOLATILITY FEATURES (5)
  // ----------------------------------------------------------

  private computeVolatilityFeatures(input: FeatureComputationInput): Record<string, FeatureValue> {
    const src = `${this.SOURCE}:volatility`;
    const result: Record<string, FeatureValue> = {};

    // Realized volatility = sqrt(sum(log_returns^2) / n) * sqrt(annualization_factor)
    // We express as percentage (annualized with appropriate factor for the window)

    // --- realized_vol_1h (using last 1h of 1h bars = 1 bar, so use last 24 bars of 1h data) ---
    result['realized_vol_1h'] = this.computeRealizedVol(
      input.ohlcvBars.slice(-24), src, 'realized_vol_1h'
    );

    // --- realized_vol_4h ---
    result['realized_vol_4h'] = this.computeRealizedVol(
      input.ohlcv4h.length > 0 ? input.ohlcv4h.slice(-42) : input.ohlcvBars.slice(-168), src, 'realized_vol_4h'
    );

    // --- realized_vol_24h ---
    result['realized_vol_24h'] = this.computeRealizedVol(
      input.ohlcv1d.length > 0 ? input.ohlcv1d.slice(-30) : input.ohlcvBars.slice(-720), src, 'realized_vol_24h'
    );

    // --- Garman-Klass Volatility ---
    {
      const bars = input.ohlcvBars.slice(-30);
      if (bars.length < 2) {
        result['garman_klass_vol'] = featureVal(NaN, src, 0);
      } else {
        let sumGK = 0;
        for (const bar of bars) {
          const logHL = Math.log(bar.high / bar.low);
          const logCO = Math.log(bar.close / bar.open);
          sumGK += 0.5 * logHL * logHL - (2 * Math.log(2) - 1) * logCO * logCO;
        }
        const gkVol = Math.sqrt(Math.max(0, sumGK / bars.length)) * Math.sqrt(8760); // annualize for hourly
        result['garman_klass_vol'] = featureVal(gkVol * 100, src, qualityScore(bars.length, 10));
      }
    }

    // --- Parkinson Volatility ---
    {
      const bars = input.ohlcvBars.slice(-30);
      if (bars.length < 2) {
        result['parkinson_vol'] = featureVal(NaN, src, 0);
      } else {
        let sumP = 0;
        for (const bar of bars) {
          const logHL = Math.log(bar.high / bar.low);
          sumP += logHL * logHL;
        }
        const parkVol = Math.sqrt(sumP / (4 * bars.length * Math.log(2))) * Math.sqrt(8760);
        result['parkinson_vol'] = featureVal(parkVol * 100, src, qualityScore(bars.length, 10));
      }
    }

    return result;
  }

  private computeRealizedVol(bars: OHLCVBar[], src: string, name: string): FeatureValue {
    if (bars.length < 2) {
      return featureVal(NaN, src, 0);
    }

    const logReturns: number[] = [];
    for (let i = 1; i < bars.length; i++) {
      if (bars[i].close > 0 && bars[i - 1].close > 0) {
        logReturns.push(Math.log(bars[i].close / bars[i - 1].close));
      }
    }

    if (logReturns.length < 2) {
      return featureVal(NaN, src, 0);
    }

    const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
    const variance = logReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) / (logReturns.length - 1);
    // Annualize: 1h bars → *sqrt(8760), 4h → *sqrt(2190), 1d → *sqrt(365)
    let annualizationFactor = 8760; // default for hourly
    if (name.includes('4h')) annualizationFactor = 2190;
    if (name.includes('24h')) annualizationFactor = 365;

    const realizedVol = Math.sqrt(Math.max(0, variance)) * Math.sqrt(annualizationFactor) * 100;
    return featureVal(realizedVol, src, qualityScore(bars.length, 10));
  }

  // ----------------------------------------------------------
  // VOLUME FEATURES (4)
  // ----------------------------------------------------------

  private computeVolumeFeatures(input: FeatureComputationInput): Record<string, FeatureValue> {
    const src = `${this.SOURCE}:volume`;
    const result: Record<string, FeatureValue> = {};
    const bars = input.ohlcvBars;
    const volumes = bars.map(b => b.volume);
    const closes = bars.map(b => b.close);

    // --- volume_ma_ratio (current volume / 20-period MA volume) ---
    {
      const period = 20;
      if (volumes.length < period) {
        result['volume_ma_ratio'] = featureVal(NaN, src, qualityScore(volumes.length, period));
      } else {
        const avgVol = volumes.slice(-period).reduce((a, b) => a + b, 0) / period;
        const ratio = avgVol > 0 ? volumes[volumes.length - 1] / avgVol : 0;
        result['volume_ma_ratio'] = featureVal(ratio, src, qualityScore(volumes.length, period));
      }
    }

    // --- volume_trend_1h (volume-weighted price change over last hour) ---
    {
      const recentBars = bars.slice(-1);
      if (recentBars.length < 1 || closes.length < 2) {
        result['volume_trend_1h'] = featureVal(NaN, src, 0);
      } else {
        const priceChange = (closes[closes.length - 1] - closes[closes.length - 2]) / closes[closes.length - 2];
        const volTrend = priceChange * volumes[volumes.length - 1];
        result['volume_trend_1h'] = featureVal(volTrend, src, qualityScore(bars.length, 2));
      }
    }

    // --- volume_trend_4h ---
    {
      const bars4h = input.ohlcv4h.length > 0 ? input.ohlcv4h : [];
      if (bars4h.length >= 2) {
        const closes4h = bars4h.map(b => b.close);
        const volumes4h = bars4h.map(b => b.volume);
        const priceChange = (closes4h[closes4h.length - 1] - closes4h[closes4h.length - 2]) / closes4h[closes4h.length - 2];
        const volTrend = priceChange * volumes4h[volumes4h.length - 1];
        result['volume_trend_4h'] = featureVal(volTrend, src, qualityScore(bars4h.length, 2));
      } else if (bars.length >= 4) {
        // Approximate from 1h bars
        const last4 = bars.slice(-4);
        const firstClose = last4[0].close;
        const lastClose = last4[last4.length - 1].close;
        const totalVol = last4.reduce((s, b) => s + b.volume, 0);
        const priceChange = firstClose > 0 ? (lastClose - firstClose) / firstClose : 0;
        result['volume_trend_4h'] = featureVal(priceChange * totalVol, src, qualityScore(bars.length, 4));
      } else {
        result['volume_trend_4h'] = featureVal(NaN, src, 0);
      }
    }

    // --- relative_volume (volume relative to the token's own 7-day average) ---
    {
      const bars1d = input.ohlcv1d;
      if (bars1d.length >= 2) {
        const avgVol7d = bars1d.slice(-7).reduce((s, b) => s + b.volume, 0) / Math.min(7, bars1d.length);
        const currentVol = bars1d[bars1d.length - 1].volume;
        const relative = avgVol7d > 0 ? currentVol / avgVol7d : 0;
        result['relative_volume'] = featureVal(relative, src, qualityScore(bars1d.length, 2));
      } else if (bars.length >= 24) {
        // Estimate from hourly data
        const currentVol = volumes.slice(-24).reduce((s, v) => s + v, 0);
        const avgVol7d = volumes.slice(-168).reduce((s, v) => s + v, 0) / 7;
        const relative = avgVol7d > 0 ? currentVol / (avgVol7d / 7) : 0;
        result['relative_volume'] = featureVal(relative, src, qualityScore(bars.length, 24));
      } else {
        result['relative_volume'] = featureVal(NaN, src, qualityScore(bars.length, 2));
      }
    }

    return result;
  }

  // ----------------------------------------------------------
  // ON-CHAIN FEATURES (6)
  // ----------------------------------------------------------

  private computeOnChainFeatures(input: FeatureComputationInput): Record<string, FeatureValue> {
    const src = `${this.SOURCE}:on-chain`;
    const result: Record<string, FeatureValue> = {};
    const onChain = input.onChainData;

    if (onChain) {
      result['whale_flow_1h'] = featureVal(onChain.whaleFlow1h, src, 0.9);
      result['whale_flow_4h'] = featureVal(onChain.whaleFlow4h, src, 0.9);
      result['whale_flow_24h'] = featureVal(onChain.whaleFlow24h, src, 0.85);
      result['smart_money_net_flow'] = featureVal(onChain.smartMoneyNetFlow, src, 0.8);
      result['bot_activity_ratio'] = featureVal(onChain.botActivityRatio, src, 0.75);
      result['holder_change_24h'] = featureVal(onChain.holderChange24h, src, 0.8);
    } else {
      // Fallback: try to get from DB Token model
      result['whale_flow_1h'] = featureVal(0, `${src}:fallback`, 0.1);
      result['whale_flow_4h'] = featureVal(0, `${src}:fallback`, 0.1);
      result['whale_flow_24h'] = featureVal(0, `${src}:fallback`, 0.1);
      result['smart_money_net_flow'] = featureVal(0, `${src}:fallback`, 0.1);
      result['bot_activity_ratio'] = featureVal(0, `${src}:fallback`, 0.1);
      result['holder_change_24h'] = featureVal(0, `${src}:fallback`, 0.1);
    }

    return result;
  }

  // ----------------------------------------------------------
  // LIQUIDITY FEATURES (3)
  // ----------------------------------------------------------

  private computeLiquidityFeatures(input: FeatureComputationInput): Record<string, FeatureValue> {
    const src = `${this.SOURCE}:liquidity`;
    const result: Record<string, FeatureValue> = {};
    const liq = input.liquidityData;

    if (liq) {
      result['spread_pct'] = featureVal(liq.spreadPct, src, 0.9);
      result['depth_ratio'] = featureVal(liq.depthRatio, src, 0.85);
      result['slippage_estimate'] = featureVal(liq.slippageEstimate, src, 0.8);
    } else {
      result['spread_pct'] = featureVal(0, `${src}:fallback`, 0.1);
      result['depth_ratio'] = featureVal(1, `${src}:fallback`, 0.1);
      result['slippage_estimate'] = featureVal(0, `${src}:fallback`, 0.1);
    }

    return result;
  }

  // ----------------------------------------------------------
  // SENTIMENT FEATURES (3)
  // ----------------------------------------------------------

  private computeSentimentFeatures(input: FeatureComputationInput): Record<string, FeatureValue> {
    const src = `${this.SOURCE}:sentiment`;
    const result: Record<string, FeatureValue> = {};
    const sent = input.sentimentData;

    if (sent) {
      result['buy_sell_pressure'] = featureVal(sent.buySellPressure, src, 0.85);
      result['funding_rate_deviation'] = featureVal(sent.fundingRateDeviation, src, 0.8);
      result['open_interest_change'] = featureVal(sent.openInterestChange, src, 0.8);
    } else {
      result['buy_sell_pressure'] = featureVal(0, `${src}:fallback`, 0.1);
      result['funding_rate_deviation'] = featureVal(0, `${src}:fallback`, 0.1);
      result['open_interest_change'] = featureVal(0, `${src}:fallback`, 0.1);
    }

    return result;
  }

  // ----------------------------------------------------------
  // DATA LOADING
  // ----------------------------------------------------------

  /**
   * Load OHLCV bars from the database for a token.
   */
  async loadOHLCVBars(
    tokenAddress: string,
    chain: string,
    timeframe: string,
    limit: number = 300,
  ): Promise<OHLCVBar[]> {
    try {
      const candleRows = await ohlcvPipeline.getCandles(tokenAddress, timeframe, undefined, undefined, limit);

      return candleRows.map((c: PriceCandleRow) => ({
        timestamp: c.timestamp.getTime(),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      }));
    } catch {
      return [];
    }
  }

  /**
   * Load on-chain data from the database for a token.
   */
  async loadOnChainData(tokenAddress: string, _chain: string): Promise<OnChainData | null> {
    try {
      const token = await db.token.findFirst({
        where: {
          OR: [
            { address: tokenAddress },
            { id: tokenAddress },
          ],
        },
        select: {
          botActivityPct: true,
          smartMoneyPct: true,
          holderCount: true,
          volume24h: true,
        },
      });

      if (!token) return null;

      // Try to get DNA for more detailed on-chain metrics
      const dna = await db.tokenDNA.findFirst({
        where: { token: { address: tokenAddress } },
        select: {
          botActivityScore: true,
          smartMoneyScore: true,
          whaleScore: true,
        },
      });

      const volume24h = token.volume24h || 0;
      const whaleScore = dna?.whaleScore || 0;

      return {
        whaleFlow1h: (whaleScore / 100) * volume24h / 24 * 0.5,
        whaleFlow4h: (whaleScore / 100) * volume24h / 6 * 0.5,
        whaleFlow24h: (whaleScore / 100) * volume24h * 0.5,
        smartMoneyNetFlow: ((dna?.smartMoneyScore || token.smartMoneyPct) / 100) * volume24h * 0.1,
        botActivityRatio: (dna?.botActivityScore || token.botActivityPct) / 100,
        holderChange24h: token.holderCount > 0 ? token.holderCount * 0.01 : 0,
      };
    } catch {
      return null;
    }
  }

  /**
   * Load liquidity data from the database for a token.
   */
  async loadLiquidityData(tokenAddress: string, _chain: string): Promise<LiquidityData | null> {
    try {
      const token = await db.token.findFirst({
        where: {
          OR: [
            { address: tokenAddress },
            { id: tokenAddress },
          ],
        },
        select: {
          liquidity: true,
          volume24h: true,
        },
      });

      if (!token) return null;

      const liq = token.liquidity || 0;
      const vol = token.volume24h || 0;

      // Estimate spread from liquidity: higher liquidity → lower spread
      const spreadPct = liq > 0 ? Math.max(0.01, (1 / Math.sqrt(liq / 10000)) * 0.5) : 5.0;
      // Depth ratio: assume symmetric unless we have orderbook data
      const depthRatio = 1.0;
      // Slippage estimate for $1000 order
      const slippageEstimate = liq > 0 ? (1000 / liq) * 100 : 10.0;

      return {
        spreadPct,
        depthRatio,
        slippageEstimate: Math.min(100, slippageEstimate),
      };
    } catch {
      return null;
    }
  }

  /**
   * Load sentiment data (from DB and estimations).
   */
  async loadSentimentData(tokenAddress: string, _chain: string): Promise<SentimentData | null> {
    try {
      // Use recent signals from DB for sentiment estimation
      const recentSignals = await db.signal.findMany({
        where: {
          token: { address: tokenAddress },
          createdAt: { gte: new Date(Date.now() - 4 * 60 * 60 * 1000) },
        },
        orderBy: { createdAt: 'desc' },
        take: 10,
        select: { direction: true, confidence: true },
      });

      // Calculate buy/sell pressure from signal directions
      let buyScore = 0;
      let sellScore = 0;
      for (const signal of recentSignals) {
        if (signal.direction === 'LONG') buyScore += signal.confidence;
        else if (signal.direction === 'SHORT') sellScore += signal.confidence;
      }

      const totalScore = buyScore + sellScore;
      const pressure = totalScore > 0 ? ((buyScore - sellScore) / totalScore) * 100 : 0;

      return {
        buySellPressure: pressure,
        fundingRateDeviation: 0,
        openInterestChange: 0,
      };
    } catch {
      return null;
    }
  }

  /**
   * Build the complete computation input for a token.
   */
  async buildComputationInput(tokenAddress: string, chain: string): Promise<FeatureComputationInput> {
    const [ohlcv1h, ohlcv4h, ohlcv1d, onChainData, liquidityData, sentimentData] = await Promise.all([
      this.loadOHLCVBars(tokenAddress, chain, '1h', 300),
      this.loadOHLCVBars(tokenAddress, chain, '4h', 100),
      this.loadOHLCVBars(tokenAddress, chain, '1d', 60),
      this.loadOnChainData(tokenAddress, chain),
      this.loadLiquidityData(tokenAddress, chain),
      this.loadSentimentData(tokenAddress, chain),
    ]);

    return {
      tokenAddress,
      chain,
      ohlcvBars: ohlcv1h,
      ohlcv4h,
      ohlcv1d,
      onChainData,
      liquidityData,
      sentimentData,
      computedAt: Date.now(),
    };
  }
}

// ============================================================
// FEATURE STORE — Cache layer with TTL and versioning
// ============================================================

export class FeatureStore {
  private cache = new Map<string, FeatureCacheEntry>();
  private accessOrder: string[] = []; // LRU tracking
  private ttlConfig: FeatureTTLConfig;
  private maxEntries: number;
  private stats = { hits: 0, misses: 0, evictions: 0 };
  private memoryEstimate = 0;
  private cleanupTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    ttlConfig: FeatureTTLConfig = DEFAULT_FEATURE_TTLS,
    maxEntries: number = 2000,
  ) {
    this.ttlConfig = ttlConfig;
    this.maxEntries = maxEntries;

    // Periodic cleanup
    const timer = setInterval(() => this.evictExpired(), 60_000);
    this.cleanupTimer = timer;
    // Don't prevent process exit
    if (typeof timer === 'object' && 'unref' in timer) {
      timer.unref();
    }
  }

  /** Build cache key */
  private cacheKey(tokenAddress: string, chain: string): string {
    return `features:${chain}:${tokenAddress}`;
  }

  /** Move key to most-recently-used position */
  private touchAccess(key: string): void {
    const idx = this.accessOrder.indexOf(key);
    if (idx !== -1) {
      this.accessOrder.splice(idx, 1);
    }
    this.accessOrder.push(key);
  }

  /** Evict least recently used entry */
  private evictLRU(): void {
    if (this.accessOrder.length === 0) return;
    const oldestKey = this.accessOrder.shift();
    if (oldestKey) {
      const entry = this.cache.get(oldestKey);
      if (entry) {
        this.memoryEstimate -= this.estimateSize(entry);
      }
      this.cache.delete(oldestKey);
      this.stats.evictions++;
    }
  }

  /** Evict all expired entries */
  private evictExpired(): void {
    const now = Date.now();
    for (const [key, entry] of this.cache) {
      if (now - entry.cachedAt > entry.ttlMs) {
        this.memoryEstimate -= this.estimateSize(entry);
        this.cache.delete(key);
        const idx = this.accessOrder.indexOf(key);
        if (idx !== -1) this.accessOrder.splice(idx, 1);
        this.stats.evictions++;
      }
    }
  }

  /** Estimate memory size of a cache entry */
  private estimateSize(entry: FeatureCacheEntry): number {
    // Rough: 43 features * ~64 bytes each + overhead = ~4000 bytes
    return entry.featureSet.featureCount * 64 + 500;
  }

  /** Get max TTL across all categories for a feature set */
  private maxTTL(): number {
    return Math.max(
      this.ttlConfig.technical,
      this.ttlConfig.volatility,
      this.ttlConfig.volume,
      this.ttlConfig['on-chain'],
      this.ttlConfig.liquidity,
      this.ttlConfig.sentiment,
    );
  }

  /**
   * Get features from cache. Returns null if not found or expired.
   */
  get(tokenAddress: string, chain: string): FeatureSet | null {
    const key = this.cacheKey(tokenAddress, chain);
    const entry = this.cache.get(key);

    if (!entry) {
      this.stats.misses++;
      return null;
    }

    const age = Date.now() - entry.cachedAt;
    if (age > entry.ttlMs) {
      this.memoryEstimate -= this.estimateSize(entry);
      this.cache.delete(key);
      const idx = this.accessOrder.indexOf(key);
      if (idx !== -1) this.accessOrder.splice(idx, 1);
      this.stats.misses++;
      return null;
    }

    entry.hitCount++;
    this.stats.hits++;
    this.touchAccess(key);
    return entry.featureSet;
  }

  /**
   * Get specific features from cache. Returns only the requested features,
   * or null if the cache entry doesn't exist or is expired.
   */
  getFeatures(
    tokenAddress: string,
    chain: string,
    featureNames: FeatureName[],
  ): Record<FeatureName, FeatureValue> | null {
    const featureSet = this.get(tokenAddress, chain);
    if (!featureSet) return null;

    const result: Partial<Record<FeatureName, FeatureValue>> = {};
    for (const name of featureNames) {
      result[name] = featureSet.features[name];
    }
    return result as Record<FeatureName, FeatureValue>;
  }

  /**
   * Store a feature set in cache.
   */
  set(tokenAddress: string, chain: string, featureSet: FeatureSet): void {
    const key = this.cacheKey(tokenAddress, chain);

    // Remove old entry if exists
    const existing = this.cache.get(key);
    if (existing) {
      this.memoryEstimate -= this.estimateSize(existing);
    }

    // Evict LRU if at capacity
    while (this.cache.size >= this.maxEntries) {
      this.evictLRU();
    }

    const entry: FeatureCacheEntry = {
      featureSet,
      cachedAt: Date.now(),
      ttlMs: this.maxTTL(),
      hitCount: 0,
      version: FEATURE_STORE_VERSION,
    };

    this.cache.set(key, entry);
    this.memoryEstimate += this.estimateSize(entry);
    this.touchAccess(key);
  }

  /**
   * Invalidate cached features for a token, forcing refresh on next access.
   */
  invalidateFeatures(tokenAddress: string, chain: string): boolean {
    const key = this.cacheKey(tokenAddress, chain);
    const entry = this.cache.get(key);
    if (entry) {
      this.memoryEstimate -= this.estimateSize(entry);
    }
    const deleted = this.cache.delete(key);
    const idx = this.accessOrder.indexOf(key);
    if (idx !== -1) this.accessOrder.splice(idx, 1);
    return deleted;
  }

  /**
   * Get the full feature vector as Float64Array for ML consumption.
   */
  getFeatureVector(tokenAddress: string, chain: string): FeatureVector | null {
    const featureSet = this.get(tokenAddress, chain);
    if (!featureSet) return null;

    return this.featureSetToVector(featureSet);
  }

  /**
   * Convert a FeatureSet to a FeatureVector (Float64Array).
   */
  featureSetToVector(featureSet: FeatureSet): FeatureVector {
    const values = new Float64Array(ALL_FEATURE_NAMES.length);
    const qualityScores = new Float64Array(ALL_FEATURE_NAMES.length);

    for (let i = 0; i < ALL_FEATURE_NAMES.length; i++) {
      const name = ALL_FEATURE_NAMES[i];
      const fv = featureSet.features[name];
      if (fv) {
        values[i] = fv.value;
        qualityScores[i] = fv.quality;
      } else {
        values[i] = 0;
        qualityScores[i] = 0;
      }
    }

    return {
      tokenAddress: featureSet.tokenAddress,
      chain: featureSet.chain,
      timestamp: featureSet.computedAt,
      featureNames: [...ALL_FEATURE_NAMES],
      values,
      qualityScores,
      length: ALL_FEATURE_NAMES.length,
    };
  }

  /**
   * Get cache statistics.
   */
  getStats(): FeatureCacheStats {
    return {
      hits: this.stats.hits,
      misses: this.stats.misses,
      evictions: this.stats.evictions,
      entries: this.cache.size,
      maxEntries: this.maxEntries,
      memoryEstimateBytes: this.memoryEstimate,
      hitRate: this.stats.hits + this.stats.misses > 0
        ? this.stats.hits / (this.stats.hits + this.stats.misses)
        : 0,
    };
  }

  /**
   * Clear the entire cache.
   */
  clear(): void {
    this.cache.clear();
    this.accessOrder = [];
    this.memoryEstimate = 0;
  }

  /**
   * Destroy the cache and cleanup timers.
   */
  destroy(): void {
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = null;
    }
    this.clear();
  }
}

// ============================================================
// FEATURE CATALOG — Metadata and lineage
// ============================================================

export class FeatureCatalog {
  private lineageMap = new Map<FeatureName, FeatureLineage>();
  private initialized = false;

  /** Initialize the catalog with all feature definitions */
  initialize(): void {
    if (this.initialized) return;

    const definitions = this.getAllDefinitions();
    for (const def of definitions) {
      this.lineageMap.set(def.name, {
        featureName: def.name,
        category: def.category,
        description: def.description,
        sourceDependencies: def.sourceDependencies,
        computeFunction: def.computeFunction,
        minDataPoints: def.minDataPoints,
        definitionVersion: FEATURE_STORE_VERSION,
      });
    }

    this.initialized = true;
  }

  /**
   * Get lineage information for a specific feature.
   */
  getLineage(name: FeatureName): FeatureLineage | null {
    if (!this.initialized) this.initialize();
    return this.lineageMap.get(name) ?? null;
  }

  /**
   * Get all features in a category.
   */
  getFeaturesByCategory(category: FeatureCategory): FeatureLineage[] {
    if (!this.initialized) this.initialize();
    const result: FeatureLineage[] = [];
    for (const lineage of this.lineageMap.values()) {
      if (lineage.category === category) result.push(lineage);
    }
    return result;
  }

  /**
   * Get all feature lineages.
   */
  getAllLineages(): FeatureLineage[] {
    if (!this.initialized) this.initialize();
    return Array.from(this.lineageMap.values());
  }

  /**
   * Get the quality score for a feature set.
   */
  assessQuality(featureSet: FeatureSet): { overall: number; byCategory: Record<FeatureCategory, number> } {
    const byCategory: Record<FeatureCategory, number[]> = {
      technical: [],
      volatility: [],
      volume: [],
      'on-chain': [],
      liquidity: [],
      sentiment: [],
    };

    for (const [name, fv] of Object.entries(featureSet.features)) {
      const category = FEATURE_CATEGORY_MAP[name as FeatureName];
      if (category) {
        byCategory[category].push(fv.quality);
      }
    }

    const categoryAverages: Record<string, number> = {};
    for (const [cat, scores] of Object.entries(byCategory)) {
      categoryAverages[cat] = scores.length > 0
        ? scores.reduce((a, b) => a + b, 0) / scores.length
        : 0;
    }

    const overall = Object.values(categoryAverages).reduce((a, b) => a + b, 0) / Object.keys(categoryAverages).length;

    return { overall, byCategory: categoryAverages as Record<FeatureCategory, number> };
  }

  /**
   * Full feature definitions registry.
   */
  private getAllDefinitions(): FeatureDefinition[] {
    const ohlcvDep = (timeframe: string, minBars: number): FeatureLineage['sourceDependencies'] => [
      { type: 'ohlcv', timeframe, minHistoryBars: minBars, source: 'binance/coingecko' },
    ];

    const onChainDep: FeatureLineage['sourceDependencies'] = [
      { type: 'on-chain', minHistoryBars: 1, source: 'sqd/dune' },
    ];

    const orderbookDep: FeatureLineage['sourceDependencies'] = [
      { type: 'orderbook', minHistoryBars: 1, source: 'dexscreener/dexpaprika' },
    ];

    return [
      // Technical (22)
      { name: 'rsi_14', category: 'technical', description: 'Relative Strength Index (14-period). Momentum oscillator 0-100, <30 oversold, >70 overbought', compute: () => featureVal(0, '', 0), computeFunction: 'computeRSI', minDataPoints: 15, sourceDependencies: ohlcvDep('1h', 15) },
      { name: 'ma_7', category: 'technical', description: 'Simple Moving Average (7-period). Short-term trend indicator', compute: () => featureVal(0, '', 0), computeFunction: 'computeSMA', minDataPoints: 7, sourceDependencies: ohlcvDep('1h', 7) },
      { name: 'ma_25', category: 'technical', description: 'Simple Moving Average (25-period). Medium-term trend indicator', compute: () => featureVal(0, '', 0), computeFunction: 'computeSMA', minDataPoints: 25, sourceDependencies: ohlcvDep('1h', 25) },
      { name: 'ma_50', category: 'technical', description: 'Simple Moving Average (50-period). Long-term trend indicator', compute: () => featureVal(0, '', 0), computeFunction: 'computeSMA', minDataPoints: 50, sourceDependencies: ohlcvDep('1h', 50) },
      { name: 'ma_200', category: 'technical', description: 'Simple Moving Average (200-period). Macro trend indicator, golden/death cross', compute: () => featureVal(0, '', 0), computeFunction: 'computeSMA', minDataPoints: 200, sourceDependencies: ohlcvDep('1h', 200) },
      { name: 'ema_12', category: 'technical', description: 'Exponential Moving Average (12-period). Fast EMA for MACD', compute: () => featureVal(0, '', 0), computeFunction: 'computeEMA', minDataPoints: 12, sourceDependencies: ohlcvDep('1h', 12) },
      { name: 'ema_26', category: 'technical', description: 'Exponential Moving Average (26-period). Slow EMA for MACD', compute: () => featureVal(0, '', 0), computeFunction: 'computeEMA', minDataPoints: 26, sourceDependencies: ohlcvDep('1h', 26) },
      { name: 'bollinger_upper', category: 'technical', description: 'Bollinger Band upper (20, 2σ). Resistance level', compute: () => featureVal(0, '', 0), computeFunction: 'computeBollinger', minDataPoints: 20, sourceDependencies: ohlcvDep('1h', 20) },
      { name: 'bollinger_middle', category: 'technical', description: 'Bollinger Band middle (20-period SMA). Mean reversion level', compute: () => featureVal(0, '', 0), computeFunction: 'computeBollinger', minDataPoints: 20, sourceDependencies: ohlcvDep('1h', 20) },
      { name: 'bollinger_lower', category: 'technical', description: 'Bollinger Band lower (20, 2σ). Support level', compute: () => featureVal(0, '', 0), computeFunction: 'computeBollinger', minDataPoints: 20, sourceDependencies: ohlcvDep('1h', 20) },
      { name: 'bollinger_bandwidth', category: 'technical', description: 'Bollinger Bandwidth (upper-lower)/middle * 100. Squeeze indicator', compute: () => featureVal(0, '', 0), computeFunction: 'computeBollinger', minDataPoints: 20, sourceDependencies: ohlcvDep('1h', 20) },
      { name: 'bollinger_percent_b', category: 'technical', description: 'Bollinger %B (price relative to band). <0 below lower, >1 above upper', compute: () => featureVal(0, '', 0), computeFunction: 'computeBollinger', minDataPoints: 20, sourceDependencies: ohlcvDep('1h', 20) },
      { name: 'atr_14', category: 'technical', description: 'Average True Range (14-period) as % of price. Volatility measure', compute: () => featureVal(0, '', 0), computeFunction: 'computeATR', minDataPoints: 15, sourceDependencies: ohlcvDep('1h', 15) },
      { name: 'macd_line', category: 'technical', description: 'MACD Line (12,26). EMA12 - EMA26, normalized as % of price', compute: () => featureVal(0, '', 0), computeFunction: 'computeMACD', minDataPoints: 35, sourceDependencies: ohlcvDep('1h', 35) },
      { name: 'macd_signal', category: 'technical', description: 'MACD Signal Line (9-period EMA of MACD line), normalized', compute: () => featureVal(0, '', 0), computeFunction: 'computeMACD', minDataPoints: 35, sourceDependencies: ohlcvDep('1h', 35) },
      { name: 'macd_histogram', category: 'technical', description: 'MACD Histogram (MACD - Signal). Divergence indicator, normalized', compute: () => featureVal(0, '', 0), computeFunction: 'computeMACD', minDataPoints: 35, sourceDependencies: ohlcvDep('1h', 35) },
      { name: 'stochastic_k', category: 'technical', description: 'Stochastic %K (14-period). Momentum oscillator, <20 oversold, >80 overbought', compute: () => featureVal(0, '', 0), computeFunction: 'computeStochastic', minDataPoints: 14, sourceDependencies: ohlcvDep('1h', 14) },
      { name: 'stochastic_d', category: 'technical', description: 'Stochastic %D (3-period SMA of %K). Signal line for Stochastic', compute: () => featureVal(0, '', 0), computeFunction: 'computeStochastic', minDataPoints: 17, sourceDependencies: ohlcvDep('1h', 17) },
      { name: 'adx', category: 'technical', description: 'Average Directional Index (14-period). Trend strength, >25 trending, <20 ranging', compute: () => featureVal(0, '', 0), computeFunction: 'computeADX', minDataPoints: 28, sourceDependencies: ohlcvDep('1h', 28) },
      { name: 'cci', category: 'technical', description: 'Commodity Channel Index (20-period). Mean reversion oscillator, >200 overbought', compute: () => featureVal(0, '', 0), computeFunction: 'computeCCI', minDataPoints: 20, sourceDependencies: ohlcvDep('1h', 20) },
      { name: 'obv', category: 'technical', description: 'On-Balance Volume ratio. Volume-weighted price trend, normalized', compute: () => featureVal(0, '', 0), computeFunction: 'computeOBV', minDataPoints: 2, sourceDependencies: ohlcvDep('1h', 2) },
      { name: 'vwap', category: 'technical', description: 'VWAP deviation from close (%). Institutional price benchmark', compute: () => featureVal(0, '', 0), computeFunction: 'computeVWAP', minDataPoints: 2, sourceDependencies: ohlcvDep('1h', 24) },

      // Volatility (5)
      { name: 'realized_vol_1h', category: 'volatility', description: 'Annualized realized volatility from 1h returns (24 bars). Standard deviation of log returns', compute: () => featureVal(0, '', 0), computeFunction: 'computeRealizedVol', minDataPoints: 10, sourceDependencies: ohlcvDep('1h', 24) },
      { name: 'realized_vol_4h', category: 'volatility', description: 'Annualized realized volatility from 4h returns (42 bars). Medium-term vol', compute: () => featureVal(0, '', 0), computeFunction: 'computeRealizedVol', minDataPoints: 10, sourceDependencies: ohlcvDep('4h', 42) },
      { name: 'realized_vol_24h', category: 'volatility', description: 'Annualized realized volatility from daily returns (30 bars). Long-term vol', compute: () => featureVal(0, '', 0), computeFunction: 'computeRealizedVol', minDataPoints: 10, sourceDependencies: ohlcvDep('1d', 30) },
      { name: 'garman_klass_vol', category: 'volatility', description: 'Garman-Klass volatility estimator. Uses OHLC for more efficient vol estimate', compute: () => featureVal(0, '', 0), computeFunction: 'computeGarmanKlass', minDataPoints: 10, sourceDependencies: ohlcvDep('1h', 30) },
      { name: 'parkinson_vol', category: 'volatility', description: 'Parkinson volatility estimator. Uses H/L range, more efficient than close-to-close', compute: () => featureVal(0, '', 0), computeFunction: 'computeParkinson', minDataPoints: 10, sourceDependencies: ohlcvDep('1h', 30) },

      // Volume (4)
      { name: 'volume_ma_ratio', category: 'volume', description: 'Current volume / 20-period average volume. >2 indicates abnormal activity', compute: () => featureVal(0, '', 0), computeFunction: 'computeVolumeMARatio', minDataPoints: 20, sourceDependencies: ohlcvDep('1h', 20) },
      { name: 'volume_trend_1h', category: 'volume', description: '1h volume-weighted price change. Indicates direction of volume pressure', compute: () => featureVal(0, '', 0), computeFunction: 'computeVolumeTrend', minDataPoints: 2, sourceDependencies: ohlcvDep('1h', 2) },
      { name: 'volume_trend_4h', category: 'volume', description: '4h volume-weighted price change. Medium-term volume direction', compute: () => featureVal(0, '', 0), computeFunction: 'computeVolumeTrend', minDataPoints: 2, sourceDependencies: ohlcvDep('4h', 2) },
      { name: 'relative_volume', category: 'volume', description: 'Current 24h volume relative to 7-day average. Unusual volume detector', compute: () => featureVal(0, '', 0), computeFunction: 'computeRelativeVolume', minDataPoints: 2, sourceDependencies: ohlcvDep('1d', 7) },

      // On-chain (6)
      { name: 'whale_flow_1h', category: 'on-chain', description: 'Net whale flow (buy-sell) in USD over 1h. Large holder activity', compute: () => featureVal(0, '', 0), computeFunction: 'computeWhaleFlow', minDataPoints: 1, sourceDependencies: onChainDep },
      { name: 'whale_flow_4h', category: 'on-chain', description: 'Net whale flow (buy-sell) in USD over 4h. Medium-term whale activity', compute: () => featureVal(0, '', 0), computeFunction: 'computeWhaleFlow', minDataPoints: 1, sourceDependencies: onChainDep },
      { name: 'whale_flow_24h', category: 'on-chain', description: 'Net whale flow (buy-sell) in USD over 24h. Daily whale accumulation/distribution', compute: () => featureVal(0, '', 0), computeFunction: 'computeWhaleFlow', minDataPoints: 1, sourceDependencies: onChainDep },
      { name: 'smart_money_net_flow', category: 'on-chain', description: 'Smart money net flow in USD. Tracks sophisticated trader activity', compute: () => featureVal(0, '', 0), computeFunction: 'computeSmartMoneyFlow', minDataPoints: 1, sourceDependencies: onChainDep },
      { name: 'bot_activity_ratio', category: 'on-chain', description: 'Proportion of volume from identified bots (0-1). High values suggest artificial volume', compute: () => featureVal(0, '', 0), computeFunction: 'computeBotActivity', minDataPoints: 1, sourceDependencies: onChainDep },
      { name: 'holder_change_24h', category: 'on-chain', description: 'Change in holder count over 24h. Positive = new entrants, negative = exiting', compute: () => featureVal(0, '', 0), computeFunction: 'computeHolderChange', minDataPoints: 1, sourceDependencies: onChainDep },

      // Liquidity (3)
      { name: 'spread_pct', category: 'liquidity', description: 'Bid-ask spread as percentage. Lower = more liquid', compute: () => featureVal(0, '', 0), computeFunction: 'computeSpread', minDataPoints: 1, sourceDependencies: orderbookDep },
      { name: 'depth_ratio', category: 'liquidity', description: 'Bid depth / ask depth at 2% from mid. >1 buying pressure, <1 selling pressure', compute: () => featureVal(0, '', 0), computeFunction: 'computeDepthRatio', minDataPoints: 1, sourceDependencies: orderbookDep },
      { name: 'slippage_estimate', category: 'liquidity', description: 'Estimated slippage for $1000 market order (%). Trade execution cost', compute: () => featureVal(0, '', 0), computeFunction: 'computeSlippage', minDataPoints: 1, sourceDependencies: orderbookDep },

      // Sentiment (3)
      { name: 'buy_sell_pressure', category: 'sentiment', description: 'Buy/sell pressure score (-100 to +100). Derived from trade flow and signals', compute: () => featureVal(0, '', 0), computeFunction: 'computeBuySellPressure', minDataPoints: 1, sourceDependencies: [{ type: 'trades', minHistoryBars: 1, source: 'dexpaprika/dexscreener' }] },
      { name: 'funding_rate_deviation', category: 'sentiment', description: 'Funding rate deviation from 7d average (bps). Positive = crowded long', compute: () => featureVal(0, '', 0), computeFunction: 'computeFundingRateDeviation', minDataPoints: 1, sourceDependencies: [{ type: 'external', minHistoryBars: 1, source: 'binance/bybit' }] },
      { name: 'open_interest_change', category: 'sentiment', description: 'Open interest change over 4h (%). Rising OI + price = strong trend', compute: () => featureVal(0, '', 0), computeFunction: 'computeOpenInterestChange', minDataPoints: 1, sourceDependencies: [{ type: 'external', minHistoryBars: 1, source: 'binance/bybit' }] },
    ];
  }
}

// ============================================================
// INTEGRATION HOOKS
// ============================================================

/**
 * Compute all features for a token and store in cache.
 * This is the primary entry point for the Brain pipeline.
 */
export async function computeAndStore(
  tokenAddress: string,
  chain: string,
): Promise<FeatureSet> {
  // Check cache first
  const cached = featureStore.get(tokenAddress, chain);
  if (cached) return cached;

  // Build input and compute
  const input = await featureEngine.buildComputationInput(tokenAddress, chain);
  const featureSet = await featureEngine.computeAll(input);

  // Store in cache
  featureStore.set(tokenAddress, chain, featureSet);

  return featureSet;
}

/**
 * Get or compute a single feature (lazy evaluation).
 * Useful when a Brain sub-engine only needs one feature.
 */
export async function getOrCompute(
  tokenAddress: string,
  chain: string,
  featureName: FeatureName,
): Promise<FeatureValue> {
  // Check if we have a cached feature set
  const cached = featureStore.get(tokenAddress, chain);
  if (cached && cached.features[featureName]) {
    return cached.features[featureName];
  }

  // Need to compute — check if we have a partial cache
  // For efficiency, compute the whole set rather than individual features
  const featureSet = await computeAndStore(tokenAddress, chain);
  return featureSet.features[featureName];
}

/**
 * Get features for multiple specific feature names.
 */
export async function getFeatures(
  tokenAddress: string,
  chain: string,
  featureNames: FeatureName[],
): Promise<Record<FeatureName, FeatureValue>> {
  const cached = featureStore.getFeatures(tokenAddress, chain, featureNames);
  if (cached) return cached;

  const featureSet = await computeAndStore(tokenAddress, chain);
  const result: Partial<Record<FeatureName, FeatureValue>> = {};
  for (const name of featureNames) {
    result[name] = featureSet.features[name];
  }
  return result as Record<FeatureName, FeatureValue>;
}

/**
 * Get point-in-time features for backtesting.
 * Returns features as they would have existed at a given timestamp,
 * preventing look-ahead bias.
 */
export async function getBacktestFeatures(
  tokenAddress: string,
  chain: string,
  asOfTimestamp: number,
): Promise<PointInTimeFeatureSet> {
  // Load candles up to asOfTimestamp only (no future data)
  const asOfDate = new Date(asOfTimestamp);
  const from1h = new Date(asOfTimestamp - 300 * 3600 * 1000); // 300 hours back
  const from4h = new Date(asOfTimestamp - 100 * 4 * 3600 * 1000);
  const from1d = new Date(asOfTimestamp - 60 * 24 * 3600 * 1000);

  const [ohlcv1h, ohlcv4h, ohlcv1d, onChainData, liquidityData, sentimentData] = await Promise.all([
    featureEngine.loadOHLCVBars(tokenAddress, chain, '1h', 300),
    featureEngine.loadOHLCVBars(tokenAddress, chain, '4h', 100),
    featureEngine.loadOHLCVBars(tokenAddress, chain, '1d', 60),
    featureEngine.loadOnChainData(tokenAddress, chain),
    featureEngine.loadLiquidityData(tokenAddress, chain),
    featureEngine.loadSentimentData(tokenAddress, chain),
  ]);

  // Filter: only use bars up to asOfTimestamp (prevent look-ahead)
  const filterBars = (bars: OHLCVBar[]): OHLCVBar[] =>
    bars.filter(b => b.timestamp <= asOfTimestamp);

  const input: FeatureComputationInput = {
    tokenAddress,
    chain,
    ohlcvBars: filterBars(ohlcv1h),
    ohlcv4h: filterBars(ohlcv4h),
    ohlcv1d: filterBars(ohlcv1d),
    onChainData,
    liquidityData,
    sentimentData,
    computedAt: asOfTimestamp,
  };

  const featureSet = await featureEngine.computeAll(input);

  return {
    featureSet,
    asOfTimestamp,
    isExact: true,
    codeVersion: FEATURE_STORE_VERSION,
  };
}

/**
 * Invalidate cached features for a token, forcing refresh on next access.
 */
export function invalidateFeatures(tokenAddress: string, chain: string): boolean {
  return featureStore.invalidateFeatures(tokenAddress, chain);
}

/**
 * Get the feature vector (Float64Array) for ML consumption.
 */
export async function getFeatureVector(tokenAddress: string, chain: string): Promise<FeatureVector> {
  const featureSet = await computeAndStore(tokenAddress, chain);
  return featureStore.featureSetToVector(featureSet);
}

// ============================================================
// SINGLETON EXPORTS
// ============================================================

export const featureEngine = new FeatureEngine();
export const featureStore = new FeatureStore();
export const featureCatalog = new FeatureCatalog();

// Initialize catalog on import
featureCatalog.initialize();
