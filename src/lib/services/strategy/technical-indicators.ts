/**
 * Technical Indicators - CryptoQuant Terminal
 *
 * Computes REAL technical indicators from OHLCV candle data.
 * This is the foundation of quantitative analysis — RSI, MACD, Bollinger,
 * EMA, ATR, Volume Profile, Stochastic, etc.
 *
 * All functions take raw candle arrays and return indicator values.
 * No external dependencies — pure math.
 *
 * Used by:
 *   - signal-generators.ts (for generating signals from real data)
 *   - backtesting-engine.ts (for entry/exit signal evaluation)
 *   - DNA computation (for volatility/momentum profiles)
 */

// ============================================================
// TYPES
// ============================================================

export interface CandleData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IndicatorResult {
  name: string;
  value: number | null;
  signal: 'BUY' | 'SELL' | 'NEUTRAL';
  strength: number; // 0-100
}

export interface TechnicalIndicators {
  rsi: IndicatorResult;
  macd: {
    name: string;
    macdLine: number | null;
    signalLine: number | null;
    histogram: number | null;
    signal: 'BUY' | 'SELL' | 'NEUTRAL';
    strength: number;
  };
  bollinger: {
    name: string;
    upper: number | null;
    middle: number | null;
    lower: number | null;
    bandwidth: number | null;
    percentB: number | null;
    signal: 'BUY' | 'SELL' | 'NEUTRAL';
    strength: number;
  };
  ema: {
    ema9: number | null;
    ema21: number | null;
    ema50: number | null;
    signal: 'BUY' | 'SELL' | 'NEUTRAL';
    strength: number;
  };
  atr: IndicatorResult;
  stochastic: {
    name: string;
    k: number | null;
    d: number | null;
    signal: 'BUY' | 'SELL' | 'NEUTRAL';
    strength: number;
  };
  volumeProfile: {
    name: string;
    avgVolume: number | null;
    volumeRatio: number | null;  // current volume / avg volume
    signal: 'BUY' | 'SELL' | 'NEUTRAL';
    strength: number;
  };
  adx: IndicatorResult;  // Average Directional Index
  vwap: IndicatorResult;
  overall: {
    signal: 'BUY' | 'SELL' | 'NEUTRAL';
    strength: number;      // 0-100 composite
    bullishCount: number;
    bearishCount: number;
    neutralCount: number;
    indicators: string[];  // Names of indicators contributing
  };
}

// ============================================================
// SIMPLE MOVING AVERAGE
// ============================================================

export function sma(data: number[], period: number): number[] {
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

// ============================================================
// EXPONENTIAL MOVING AVERAGE
// ============================================================

export function ema(data: number[], period: number): number[] {
  const result: number[] = [];
  const multiplier = 2 / (period + 1);

  // Start with SMA for the first value
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
    // EMA formula: (Close - EMA_prev) * multiplier + EMA_prev
    result.push((data[i] - result[i - 1]) * multiplier + result[i - 1]);
  }
  return result;
}

// ============================================================
// RSI (Relative Strength Index)
// ============================================================

export function computeRSI(closes: number[], period: number = 14): number[] {
  const result: number[] = [];
  const gains: number[] = [];
  const losses: number[] = [];

  for (let i = 0; i < closes.length; i++) {
    if (i === 0) {
      result.push(NaN);
      continue;
    }

    const change = closes[i] - closes[i - 1];
    gains.push(change > 0 ? change : 0);
    losses.push(change < 0 ? Math.abs(change) : 0);

    if (i < period) {
      result.push(NaN);
      continue;
    }

    if (i === period) {
      // First RSI: simple average
      const avgGain = gains.slice(0, period).reduce((a, b) => a + b, 0) / period;
      const avgLoss = losses.slice(0, period).reduce((a, b) => a + b, 0) / period;
      if (avgLoss === 0) { result.push(100); continue; }
      const rs = avgGain / avgLoss;
      result.push(100 - (100 / (1 + rs)));
      continue;
    }

    // Smoothed averages (Wilder's smoothing)
    const prevRSI = result[i - 1];
    const prevAvgGain = (100 / (100 - prevRSI) - 1) > 0
      ? gains[gains.length - 2] // Approximation
      : 0;
    // Simpler: use simple moving average for robustness
    const recentGains = gains.slice(-period);
    const recentLosses = losses.slice(-period);
    const avgGain = recentGains.reduce((a, b) => a + b, 0) / period;
    const avgLoss = recentLosses.reduce((a, b) => a + b, 0) / period;

    if (avgLoss === 0) { result.push(100); continue; }
    const rs = avgGain / avgLoss;
    result.push(100 - (100 / (1 + rs)));
  }
  return result;
}

// ============================================================
// MACD (Moving Average Convergence Divergence)
// ============================================================

export function computeMACD(
  closes: number[],
  fastPeriod: number = 12,
  slowPeriod: number = 26,
  signalPeriod: number = 9,
): { macdLine: number[]; signalLine: number[]; histogram: number[] } {
  const emaFast = ema(closes, fastPeriod);
  const emaSlow = ema(closes, slowPeriod);

  // MACD Line = EMA(fast) - EMA(slow)
  const macdLine: number[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (isNaN(emaFast[i]) || isNaN(emaSlow[i])) {
      macdLine.push(NaN);
    } else {
      macdLine.push(emaFast[i] - emaSlow[i]);
    }
  }

  // Signal Line = EMA of MACD Line
  const validMacd = macdLine.filter(v => !isNaN(v));
  const signalEma = ema(validMacd, signalPeriod);

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

  // Histogram = MACD - Signal
  const histogram: number[] = [];
  for (let i = 0; i < macdLine.length; i++) {
    if (isNaN(macdLine[i]) || isNaN(signalLine[i])) {
      histogram.push(NaN);
    } else {
      histogram.push(macdLine[i] - signalLine[i]);
    }
  }

  return { macdLine, signalLine, histogram };
}

// ============================================================
// BOLLINGER BANDS
// ============================================================

export function computeBollinger(
  closes: number[],
  period: number = 20,
  stdDevMultiplier: number = 2,
): { upper: number[]; middle: number[]; lower: number[]; bandwidth: number[]; percentB: number[] } {
  const middle = sma(closes, period);
  const upper: number[] = [];
  const lower: number[] = [];
  const bandwidth: number[] = [];
  const percentB: number[] = [];

  for (let i = 0; i < closes.length; i++) {
    if (isNaN(middle[i])) {
      upper.push(NaN);
      lower.push(NaN);
      bandwidth.push(NaN);
      percentB.push(NaN);
      continue;
    }

    // Standard deviation
    let sumSq = 0;
    for (let j = i - period + 1; j <= i; j++) {
      sumSq += (closes[j] - middle[i]) ** 2;
    }
    const stdDev = Math.sqrt(sumSq / period);

    upper.push(middle[i] + stdDevMultiplier * stdDev);
    lower.push(middle[i] - stdDevMultiplier * stdDev);
    bandwidth.push(upper[i] !== middle[i] ? ((upper[i] - lower[i]) / middle[i]) * 100 : 0);
    percentB.push(upper[i] !== lower[i] ? (closes[i] - lower[i]) / (upper[i] - lower[i]) : 0.5);
  }

  return { upper, middle, lower, bandwidth, percentB };
}

// ============================================================
// ATR (Average True Range)
// ============================================================

export function computeATR(candles: CandleData[], period: number = 14): number[] {
  const tr: number[] = [];

  for (let i = 0; i < candles.length; i++) {
    if (i === 0) {
      tr.push(candles[i].high - candles[i].low);
      continue;
    }
    const hl = candles[i].high - candles[i].low;
    const hc = Math.abs(candles[i].high - candles[i - 1].close);
    const lc = Math.abs(candles[i].low - candles[i - 1].close);
    tr.push(Math.max(hl, hc, lc));
  }

  return sma(tr, period);
}

// ============================================================
// STOCHASTIC OSCILLATOR
// ============================================================

export function computeStochastic(
  candles: CandleData[],
  kPeriod: number = 14,
  dPeriod: number = 3,
): { k: number[]; d: number[] } {
  const kValues: number[] = [];

  for (let i = 0; i < candles.length; i++) {
    if (i < kPeriod - 1) {
      kValues.push(NaN);
      continue;
    }

    let highestHigh = -Infinity;
    let lowestLow = Infinity;
    for (let j = i - kPeriod + 1; j <= i; j++) {
      highestHigh = Math.max(highestHigh, candles[j].high);
      lowestLow = Math.min(lowestLow, candles[j].low);
    }

    const range = highestHigh - lowestLow;
    kValues.push(range === 0 ? 50 : ((candles[i].close - lowestLow) / range) * 100);
  }

  const dValues = sma(kValues.filter(v => !isNaN(v)), dPeriod);
  // Map back to full length
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

  return { k: kValues, d: dFull };
}

// ============================================================
// ADX (Average Directional Index)
// ============================================================

export function computeADX(candles: CandleData[], period: number = 14): number[] {
  if (candles.length < period * 2) return candles.map(() => NaN);

  const plusDM: number[] = [];
  const minusDM: number[] = [];
  const tr: number[] = [];

  for (let i = 0; i < candles.length; i++) {
    if (i === 0) {
      plusDM.push(0);
      minusDM.push(0);
      tr.push(candles[i].high - candles[i].low);
      continue;
    }

    const upMove = candles[i].high - candles[i - 1].high;
    const downMove = candles[i - 1].low - candles[i].low;

    plusDM.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDM.push(downMove > upMove && downMove > 0 ? downMove : 0);

    const hl = candles[i].high - candles[i].low;
    const hc = Math.abs(candles[i].high - candles[i - 1].close);
    const lc = Math.abs(candles[i].low - candles[i - 1].close);
    tr.push(Math.max(hl, hc, lc));
  }

  const smoothPlusDM = sma(plusDM, period);
  const smoothMinusDM = sma(minusDM, period);
  const smoothTR = sma(tr, period);

  const dx: number[] = [];
  for (let i = 0; i < candles.length; i++) {
    if (isNaN(smoothPlusDM[i]) || isNaN(smoothMinusDM[i]) || isNaN(smoothTR[i]) || smoothTR[i] === 0) {
      dx.push(NaN);
      continue;
    }
    const plusDI = (smoothPlusDM[i] / smoothTR[i]) * 100;
    const minusDI = (smoothMinusDM[i] / smoothTR[i]) * 100;
    const diSum = plusDI + minusDI;
    dx.push(diSum === 0 ? 0 : (Math.abs(plusDI - minusDI) / diSum) * 100);
  }

  return sma(dx.filter(v => !isNaN(v)), period).map(v => isNaN(v) ? NaN : v);
}

// ============================================================
// VWAP (Volume Weighted Average Price)
// ============================================================

export function computeVWAP(candles: CandleData[]): number[] {
  const result: number[] = [];
  let cumTypicalVol = 0;
  let cumVolume = 0;

  for (const candle of candles) {
    const typicalPrice = (candle.high + candle.low + candle.close) / 3;
    cumTypicalVol += typicalPrice * candle.volume;
    cumVolume += candle.volume;
    result.push(cumVolume === 0 ? typicalPrice : cumTypicalVol / cumVolume);
  }
  return result;
}

// ============================================================
// VOLUME PROFILE
// ============================================================

export function computeVolumeProfile(
  candles: CandleData[],
  period: number = 20,
): { avgVolume: number[]; volumeRatio: number[] } {
  const volumes = candles.map(c => c.volume);
  const avgVolume = sma(volumes, period);

  const volumeRatio: number[] = [];
  for (let i = 0; i < candles.length; i++) {
    if (isNaN(avgVolume[i]) || avgVolume[i] === 0) {
      volumeRatio.push(NaN);
    } else {
      volumeRatio.push(candles[i].volume / avgVolume[i]);
    }
  }

  return { avgVolume, volumeRatio };
}

// ============================================================
// COMPOSITE: COMPUTE ALL INDICATORS
// ============================================================

/**
 * Compute all technical indicators for a candle series.
 * Returns the latest values + signals for each indicator.
 *
 * Signal logic:
 *   RSI < 30 = BUY (oversold), RSI > 70 = SELL (overbought)
 *   MACD histogram > 0 and rising = BUY, < 0 and falling = SELL
 *   Bollinger %B < 0 = BUY (below lower band), > 1 = SELL (above upper band)
 *   EMA9 > EMA21 = BUY (golden cross zone), EMA9 < EMA21 = SELL
 *   Stochastic K < 20 = BUY (oversold), K > 80 = SELL (overbought)
 *   Volume ratio > 2 = confirmation signal (high activity)
 *   ADX > 25 = strong trend, < 20 = weak/no trend
 */
export function computeAllIndicators(candles: CandleData[]): TechnicalIndicators {
  if (!candles || candles.length < 30) {
    return emptyIndicators();
  }

  const closes = candles.map(c => c.close);
  const last = closes.length - 1;

  // --- RSI ---
  const rsiValues = computeRSI(closes, 14);
  const rsiValue = rsiValues[last];
  let rsiSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let rsiStrength = 0;
  if (!isNaN(rsiValue)) {
    if (rsiValue < 30) { rsiSignal = 'BUY'; rsiStrength = Math.round(70 + (30 - rsiValue)); }
    else if (rsiValue < 40) { rsiSignal = 'BUY'; rsiStrength = Math.round(30 + (40 - rsiValue) * 4); }
    else if (rsiValue > 70) { rsiSignal = 'SELL'; rsiStrength = Math.round(70 + (rsiValue - 70)); }
    else if (rsiValue > 60) { rsiSignal = 'SELL'; rsiStrength = Math.round(30 + (rsiValue - 60) * 4); }
  }

  // --- MACD ---
  const macdResult = computeMACD(closes);
  const macdLineVal = macdResult.macdLine[last];
  const signalLineVal = macdResult.signalLine[last];
  const histogramVal = macdResult.histogram[last];
  let macdSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let macdStrength = 0;
  if (!isNaN(histogramVal)) {
    const prevHist = macdResult.histogram[last - 1];
    if (histogramVal > 0 && (isNaN(prevHist) || histogramVal > prevHist)) {
      macdSignal = 'BUY'; macdStrength = Math.min(80, Math.round(Math.abs(histogramVal) * 1000 + 30));
    } else if (histogramVal < 0 && (isNaN(prevHist) || histogramVal < prevHist)) {
      macdSignal = 'SELL'; macdStrength = Math.min(80, Math.round(Math.abs(histogramVal) * 1000 + 30));
    } else if (histogramVal > 0) {
      macdSignal = 'BUY'; macdStrength = 20; // Bullish but weakening
    } else if (histogramVal < 0) {
      macdSignal = 'SELL'; macdStrength = 20;
    }
  }

  // --- Bollinger ---
  const bollResult = computeBollinger(closes);
  const bbPercentB = bollResult.percentB[last];
  const bbBandwidth = bollResult.bandwidth[last];
  let bbSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let bbStrength = 0;
  if (!isNaN(bbPercentB)) {
    if (bbPercentB < 0) { bbSignal = 'BUY'; bbStrength = Math.round(60 + Math.abs(bbPercentB) * 80); }
    else if (bbPercentB < 0.2) { bbSignal = 'BUY'; bbStrength = Math.round(20 + (0.2 - bbPercentB) * 200); }
    else if (bbPercentB > 1) { bbSignal = 'SELL'; bbStrength = Math.round(60 + (bbPercentB - 1) * 80); }
    else if (bbPercentB > 0.8) { bbSignal = 'SELL'; bbStrength = Math.round(20 + (bbPercentB - 0.8) * 200); }
  }

  // --- EMA ---
  const ema9 = ema(closes, 9);
  const ema21 = ema(closes, 21);
  const ema50 = ema(closes, 50);
  const ema9Val = ema9[last];
  const ema21Val = ema21[last];
  const ema50Val = ema50[last];
  let emaSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let emaStrength = 0;
  if (!isNaN(ema9Val) && !isNaN(ema21Val)) {
    if (ema9Val > ema21Val) {
      emaSignal = 'BUY';
      emaStrength = Math.min(80, Math.round(((ema9Val - ema21Val) / ema21Val) * 10000 + 30));
    } else {
      emaSignal = 'SELL';
      emaStrength = Math.min(80, Math.round(((ema21Val - ema9Val) / ema21Val) * 10000 + 30));
    }
  }

  // --- ATR ---
  const atrValues = computeATR(candles);
  const atrValue = atrValues[last];
  const avgClose = closes.slice(-20).reduce((a, b) => a + b, 0) / Math.min(20, closes.length);
  const atrPercent = !isNaN(atrValue) && avgClose > 0 ? (atrValue / avgClose) * 100 : null;

  // --- Stochastic ---
  const stochResult = computeStochastic(candles);
  const stochK = stochResult.k[last];
  const stochD = stochResult.d[last];
  let stochSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let stochStrength = 0;
  if (!isNaN(stochK)) {
    if (stochK < 20) { stochSignal = 'BUY'; stochStrength = Math.round(60 + (20 - stochK) * 2); }
    else if (stochK < 30) { stochSignal = 'BUY'; stochStrength = Math.round(20 + (30 - stochK) * 4); }
    else if (stochK > 80) { stochSignal = 'SELL'; stochStrength = Math.round(60 + (stochK - 80) * 2); }
    else if (stochK > 70) { stochSignal = 'SELL'; stochStrength = Math.round(20 + (stochK - 70) * 4); }
  }

  // --- ADX ---
  const adxValues = computeADX(candles);
  const adxValue = adxValues[last];
  let adxSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let adxStrength = 0;
  if (!isNaN(adxValue)) {
    if (adxValue > 25) { adxStrength = Math.min(80, Math.round(adxValue * 2)); }
    else { adxStrength = Math.round(adxValue * 2); }
    // ADX doesn't give direction, just trend strength
  }

  // --- VWAP ---
  const vwapValues = computeVWAP(candles);
  const vwapValue = vwapValues[last];
  let vwapSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let vwapStrength = 0;
  if (!isNaN(vwapValue) && vwapValue > 0 && !isNaN(closes[last])) {
    if (closes[last] > vwapValue) { vwapSignal = 'BUY'; vwapStrength = 30; }
    else { vwapSignal = 'SELL'; vwapStrength = 30; }
  }

  // --- Volume Profile ---
  const volResult = computeVolumeProfile(candles);
  const volRatio = volResult.volumeRatio[last];
  let volSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let volStrength = 0;
  if (!isNaN(volRatio)) {
    volStrength = Math.min(80, Math.round(volRatio * 30));
    // High volume confirms direction from other indicators
  }

  // --- COMPOSITE OVERALL SIGNAL ---
  const indicators: { signal: 'BUY' | 'SELL' | 'NEUTRAL'; strength: number; name: string }[] = [
    { signal: rsiSignal, strength: rsiStrength, name: 'RSI' },
    { signal: macdSignal, strength: macdStrength, name: 'MACD' },
    { signal: bbSignal, strength: bbStrength, name: 'Bollinger' },
    { signal: emaSignal, strength: emaStrength, name: 'EMA' },
    { signal: stochSignal, strength: stochStrength, name: 'Stochastic' },
    { signal: vwapSignal, strength: vwapStrength, name: 'VWAP' },
  ];

  let buyScore = 0, sellScore = 0, buyCount = 0, sellCount = 0, neutralCount = 0;
  for (const ind of indicators) {
    if (ind.signal === 'BUY') { buyScore += ind.strength; buyCount++; }
    else if (ind.signal === 'SELL') { sellScore += ind.strength; sellCount++; }
    else { neutralCount++; }
  }

  const totalStrength = buyScore + sellScore;
  let overallSignal: 'BUY' | 'SELL' | 'NEUTRAL' = 'NEUTRAL';
  let overallStrength = 0;
  if (buyScore > sellScore && buyCount >= 2) {
    overallSignal = 'BUY';
    overallStrength = Math.min(100, Math.round((buyScore / Math.max(1, totalStrength)) * 60 + buyCount * 8));
  } else if (sellScore > buyScore && sellCount >= 2) {
    overallSignal = 'SELL';
    overallStrength = Math.min(100, Math.round((sellScore / Math.max(1, totalStrength)) * 60 + sellCount * 8));
  }

  const contributingIndicators = indicators
    .filter(i => i.signal !== 'NEUTRAL' && i.strength > 10)
    .map(i => `${i.name}(${i.signal})`);

  return {
    rsi: { name: 'RSI', value: isNaN(rsiValue) ? null : Math.round(rsiValue * 100) / 100, signal: rsiSignal, strength: rsiStrength },
    macd: {
      name: 'MACD',
      macdLine: isNaN(macdLineVal) ? null : Math.round(macdLineVal * 10000) / 10000,
      signalLine: isNaN(signalLineVal) ? null : Math.round(signalLineVal * 10000) / 10000,
      histogram: isNaN(histogramVal) ? null : Math.round(histogramVal * 10000) / 10000,
      signal: macdSignal,
      strength: macdStrength,
    },
    bollinger: {
      name: 'Bollinger',
      upper: isNaN(bollResult.upper[last]) ? null : Math.round(bollResult.upper[last] * 100) / 100,
      middle: isNaN(bollResult.middle[last]) ? null : Math.round(bollResult.middle[last] * 100) / 100,
      lower: isNaN(bollResult.lower[last]) ? null : Math.round(bollResult.lower[last] * 100) / 100,
      bandwidth: isNaN(bbBandwidth) ? null : Math.round(bbBandwidth * 100) / 100,
      percentB: isNaN(bbPercentB) ? null : Math.round(bbPercentB * 100) / 100,
      signal: bbSignal,
      strength: bbStrength,
    },
    ema: {
      ema9: isNaN(ema9Val) ? null : Math.round(ema9Val * 100) / 100,
      ema21: isNaN(ema21Val) ? null : Math.round(ema21Val * 100) / 100,
      ema50: isNaN(ema50Val) ? null : Math.round(ema50Val * 100) / 100,
      signal: emaSignal,
      strength: emaStrength,
    },
    atr: { name: 'ATR', value: atrPercent !== null ? Math.round(atrPercent * 100) / 100 : null, signal: 'NEUTRAL', strength: adxStrength },
    stochastic: {
      name: 'Stochastic',
      k: isNaN(stochK) ? null : Math.round(stochK * 100) / 100,
      d: isNaN(stochD) ? null : Math.round(stochD * 100) / 100,
      signal: stochSignal,
      strength: stochStrength,
    },
    volumeProfile: {
      name: 'Volume',
      avgVolume: isNaN(volResult.avgVolume[last]) ? null : Math.round(volResult.avgVolume[last] * 100) / 100,
      volumeRatio: isNaN(volRatio) ? null : Math.round(volRatio * 100) / 100,
      signal: volSignal,
      strength: volStrength,
    },
    adx: { name: 'ADX', value: isNaN(adxValue) ? null : Math.round(adxValue * 100) / 100, signal: adxSignal, strength: adxStrength },
    vwap: { name: 'VWAP', value: isNaN(vwapValue) ? null : Math.round(vwapValue * 100) / 100, signal: vwapSignal, strength: vwapStrength },
    overall: {
      signal: overallSignal,
      strength: overallStrength,
      bullishCount: buyCount,
      bearishCount: sellCount,
      neutralCount,
      indicators: contributingIndicators,
    },
  };
}

// ============================================================
// EMPTY INDICATORS (for insufficient data)
// ============================================================

function emptyIndicators(): TechnicalIndicators {
  const empty = { name: '', value: null, signal: 'NEUTRAL' as const, strength: 0 };
  return {
    rsi: { ...empty, name: 'RSI' },
    macd: { name: 'MACD', macdLine: null, signalLine: null, histogram: null, signal: 'NEUTRAL', strength: 0 },
    bollinger: { name: 'Bollinger', upper: null, middle: null, lower: null, bandwidth: null, percentB: null, signal: 'NEUTRAL', strength: 0 },
    ema: { ema9: null, ema21: null, ema50: null, signal: 'NEUTRAL', strength: 0 },
    atr: { ...empty, name: 'ATR' },
    stochastic: { name: 'Stochastic', k: null, d: null, signal: 'NEUTRAL', strength: 0 },
    volumeProfile: { name: 'Volume', avgVolume: null, volumeRatio: null, signal: 'NEUTRAL', strength: 0 },
    adx: { ...empty, name: 'ADX' },
    vwap: { ...empty, name: 'VWAP' },
    overall: { signal: 'NEUTRAL', strength: 0, bullishCount: 0, bearishCount: 0, neutralCount: 6, indicators: [] },
  };
}
