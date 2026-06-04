/**
 * Candlestick Pattern Engine - CryptoQuant Terminal
 * Motor de Detección de Patrones de Velas (30+ patrones, multi-timeframe)
 *
 * Escanea datos OHLCV en múltiples timeframes para detectar patrones
 * de velas japonesas que proporcionan señales de trading.
 *
 * Patrones implementados:
 *   - Reversión alcista (14): Hammer, InvHammer, BullEngulf, Piercing, MorningStar,
 *     MorningDoji, ThreeWhiteSoldiers, BullHarami, TweezerBottom, BullAbandonedBaby,
 *     DragonflyDoji, ThreeInsideUp, ThreeOutsideUp, BullKicker
 *   - Reversión bajista (14): HangingMan, ShootingStar, BearEngulf, DarkCloud,
 *     EveningStar, EveningDoji, ThreeBlackCrows, BearHarami, TweezerTop,
 *     BearAbandonedBaby, GravestoneDoji, ThreeInsideDown, ThreeOutsideDown, BearKicker
 *   - Continuación (8): Doji, SpinningTop, Marubozu, RisingThree, FallingThree,
 *     BullMatHold, BearMatHold, HighWave
 *
 * Multi-timeframe:
 *   - Escaneo en 5m, 15m, 1h, 4h, 1d
 *   - Confluencia: mismo patrón en múltiples timeframes = señal más fuerte
 *   - Peso por timeframe: 5m=0.5, 15m=0.7, 1h=1.0, 4h=1.3, 1d=1.5
 */

import { ohlcvPipeline, type OHLCVSeries } from './ohlcv-pipeline';
import { db } from '../db';
import { TokenPhase } from './token-lifecycle-engine';

// ============================================================
// TYPES
// ============================================================

export type PatternDirection = 'BULLISH' | 'BEARISH' | 'NEUTRAL';
export type PatternCategory = 'REVERSAL_BULL' | 'REVERSAL_BEAR' | 'CONTINUATION';

export interface CandlestickPattern {
  name: string;
  category: PatternCategory;
  direction: PatternDirection;
  reliability: number; // 0-1, historical reliability
  requiredCandles: number; // minimum candles needed
}

export interface DetectedPattern {
  pattern: string;
  category: PatternCategory;
  direction: PatternDirection;
  timeframe: string;
  confidence: number; // 0-1
  reliability: number; // 0-1
  weight: number; // timeframe-adjusted weight
  index: number; // candle index where detected (last candle = 0)
  description: string;
  priceAtDetection: number;
}

export interface PatternScanResult {
  tokenAddress: string;
  patterns: DetectedPattern[];
  bullishPatterns: DetectedPattern[];
  bearishPatterns: DetectedPattern[];
  neutralPatterns: DetectedPattern[];
  confluences: PatternConfluence[];
  overallSignal: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  overallScore: number; // -1 to 1
  dominantPattern: string | null;
  dominantTimeframe: string | null;
  scanTimeframes: string[];
  timestamp: Date;
}

export interface PatternConfluence {
  pattern: string;
  timeframes: string[];
  direction: PatternDirection;
  combinedWeight: number;
  combinedConfidence: number;
  description: string;
}

/** Timeframe weights for signal strength */
const TIMEFRAME_WEIGHTS: Record<string, number> = {
  '5m': 0.5,
  '15m': 0.7,
  '1h': 1.0,
  '4h': 1.3,
  '1d': 1.5,
};

const SCAN_TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d'];

// ============================================================
// PATTERN DEFINITIONS (30+)
// ============================================================

const PATTERN_DEFS: Record<string, CandlestickPattern> = {
  // === REVERSAL BULLISH (14) ===
  'Hammer':           { name: 'Hammer',           category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.65, requiredCandles: 1 },
  'InvHammer':        { name: 'InvHammer',        category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.60, requiredCandles: 1 },
  'BullEngulf':       { name: 'BullEngulf',       category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.70, requiredCandles: 2 },
  'Piercing':         { name: 'Piercing',         category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.55, requiredCandles: 2 },
  'MorningStar':      { name: 'MorningStar',      category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.75, requiredCandles: 3 },
  'MorningDoji':      { name: 'MorningDoji',      category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.72, requiredCandles: 3 },
  'ThreeWhiteSoldiers': { name: 'ThreeWhiteSoldiers', category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.78, requiredCandles: 3 },
  'BullHarami':       { name: 'BullHarami',       category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.50, requiredCandles: 2 },
  'TweezerBottom':    { name: 'TweezerBottom',    category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.62, requiredCandles: 2 },
  'BullAbandonedBaby':{ name: 'BullAbandonedBaby',category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.80, requiredCandles: 3 },
  'DragonflyDoji':    { name: 'DragonflyDoji',    category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.60, requiredCandles: 1 },
  'ThreeInsideUp':    { name: 'ThreeInsideUp',    category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.68, requiredCandles: 3 },
  'ThreeOutsideUp':   { name: 'ThreeOutsideUp',   category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.70, requiredCandles: 3 },
  'BullKicker':       { name: 'BullKicker',       category: 'REVERSAL_BULL', direction: 'BULLISH', reliability: 0.73, requiredCandles: 2 },

  // === REVERSAL BEARISH (14) ===
  'HangingMan':       { name: 'HangingMan',       category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.65, requiredCandles: 1 },
  'ShootingStar':     { name: 'ShootingStar',     category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.67, requiredCandles: 1 },
  'BearEngulf':       { name: 'BearEngulf',       category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.70, requiredCandles: 2 },
  'DarkCloud':        { name: 'DarkCloud',        category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.55, requiredCandles: 2 },
  'EveningStar':      { name: 'EveningStar',      category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.75, requiredCandles: 3 },
  'EveningDoji':      { name: 'EveningDoji',      category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.72, requiredCandles: 3 },
  'ThreeBlackCrows':  { name: 'ThreeBlackCrows',  category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.78, requiredCandles: 3 },
  'BearHarami':       { name: 'BearHarami',       category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.50, requiredCandles: 2 },
  'TweezerTop':       { name: 'TweezerTop',       category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.62, requiredCandles: 2 },
  'BearAbandonedBaby':{ name: 'BearAbandonedBaby',category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.80, requiredCandles: 3 },
  'GravestoneDoji':   { name: 'GravestoneDoji',   category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.60, requiredCandles: 1 },
  'ThreeInsideDown':  { name: 'ThreeInsideDown',  category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.68, requiredCandles: 3 },
  'ThreeOutsideDown': { name: 'ThreeOutsideDown', category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.70, requiredCandles: 3 },
  'BearKicker':       { name: 'BearKicker',       category: 'REVERSAL_BEAR', direction: 'BEARISH', reliability: 0.73, requiredCandles: 2 },

  // === CONTINUATION (8) ===
  'Doji':             { name: 'Doji',             category: 'CONTINUATION', direction: 'NEUTRAL', reliability: 0.45, requiredCandles: 1 },
  'SpinningTop':      { name: 'SpinningTop',      category: 'CONTINUATION', direction: 'NEUTRAL', reliability: 0.40, requiredCandles: 1 },
  'Marubozu':         { name: 'Marubozu',         category: 'CONTINUATION', direction: 'NEUTRAL', reliability: 0.55, requiredCandles: 1 },
  'RisingThree':      { name: 'RisingThree',      category: 'CONTINUATION', direction: 'BULLISH', reliability: 0.68, requiredCandles: 5 },
  'FallingThree':     { name: 'FallingThree',     category: 'CONTINUATION', direction: 'BEARISH', reliability: 0.68, requiredCandles: 5 },
  'BullMatHold':      { name: 'BullMatHold',      category: 'CONTINUATION', direction: 'BULLISH', reliability: 0.65, requiredCandles: 5 },
  'BearMatHold':      { name: 'BearMatHold',      category: 'CONTINUATION', direction: 'BEARISH', reliability: 0.65, requiredCandles: 5 },
  'HighWave':         { name: 'HighWave',         category: 'CONTINUATION', direction: 'NEUTRAL', reliability: 0.42, requiredCandles: 1 },
};

// ============================================================
// HELPER FUNCTIONS
// ============================================================

function bodySize(o: number, c: number): number { return Math.abs(c - o); }
function upperWick(o: number, c: number, h: number): number { return h - Math.max(o, c); }
function lowerWick(o: number, c: number, l: number): number { return Math.min(o, c) - l; }
function isBullish(o: number, c: number): boolean { return c > o; }
function isBearish(o: number, c: number): boolean { return c < o; }
function bodyRange(o: number, c: number): number { return Math.max(o, c) - Math.min(o, c); }
function totalRange(h: number, l: number): number { return h - l; }
function isDojiBody(o: number, c: number, h: number, l: number): boolean {
  const body = bodySize(o, c);
  const range = totalRange(h, l);
  return range > 0 && body / range < 0.1;
}

// ============================================================
// PATTERN DETECTORS (30+)
// ============================================================

function detectHammer(o: number, h: number, l: number, c: number, prevC: number): boolean {
  const body = bodySize(o, c);
  const range = totalRange(h, l);
  if (range === 0) return false;
  const lw = lowerWick(o, c, l);
  const uw = upperWick(o, c, h);
  return lw >= 2 * body && uw <= body * 0.3 && isBearish(prevC, o) || isBearish(prevC, c);
}

function detectInvHammer(o: number, h: number, l: number, c: number, prevC: number): boolean {
  const body = bodySize(o, c);
  const range = totalRange(h, l);
  if (range === 0) return false;
  const lw = lowerWick(o, c, l);
  const uw = upperWick(o, c, h);
  return uw >= 2 * body && lw <= body * 0.3 && (isBearish(prevC, o) || isBearish(prevC, c));
}

function detectBullEngulf(prevO: number, prevC: number, o: number, c: number): boolean {
  return isBearish(prevO, prevC) && isBullish(o, c) && c > prevO && o < prevC;
}

function detectBearEngulf(prevO: number, prevC: number, o: number, c: number): boolean {
  return isBullish(prevO, prevC) && isBearish(o, c) && c < prevO && o > prevC;
}

function detectPiercing(prevO: number, prevC: number, o: number, c: number): boolean {
  if (!isBearish(prevO, prevC) || !isBullish(o, c)) return false;
  const mid = (prevO + prevC) / 2;
  return o < prevC && c > mid && c < prevO;
}

function detectDarkCloud(prevO: number, prevC: number, o: number, c: number): boolean {
  if (!isBullish(prevO, prevC) || !isBearish(o, c)) return false;
  const mid = (prevO + prevC) / 2;
  return o > prevC && c < mid && c > prevO;
}

function detectMorningStar(prevO: number, prevC: number, o2: number, c2: number, o: number, c: number): boolean {
  if (!isBearish(prevO, prevC)) return false;
  const smallBody2 = bodySize(o2, c2) < bodySize(prevO, prevC) * 0.3;
  return smallBody2 && isBullish(o, c) && c > (prevO + prevC) / 2;
}

function detectEveningStar(prevO: number, prevC: number, o2: number, c2: number, o: number, c: number): boolean {
  if (!isBullish(prevO, prevC)) return false;
  const smallBody2 = bodySize(o2, c2) < bodySize(prevO, prevC) * 0.3;
  return smallBody2 && isBearish(o, c) && c < (prevO + prevC) / 2;
}

function detectMorningDoji(prevO: number, prevC: number, o2: number, c2: number, h2: number, l2: number, o: number, c: number): boolean {
  return isBearish(prevO, prevC) && isDojiBody(o2, c2, h2, l2) && isBullish(o, c) && c > (prevO + prevC) / 2;
}

function detectEveningDoji(prevO: number, prevC: number, o2: number, c2: number, h2: number, l2: number, o: number, c: number): boolean {
  return isBullish(prevO, prevC) && isDojiBody(o2, c2, h2, l2) && isBearish(o, c) && c < (prevO + prevC) / 2;
}

function detectThreeWhiteSoldiers(o1: number, c1: number, o2: number, c2: number, o3: number, c3: number): boolean {
  return isBullish(o1, c1) && isBullish(o2, c2) && isBullish(o3, c3)
    && o2 > o1 && o2 < c1 && o3 > o2 && o3 < c2
    && c1 > 0 && c2 / c1 > 1.001 && c3 / c2 > 1.001;
}

function detectThreeBlackCrows(o1: number, c1: number, o2: number, c2: number, o3: number, c3: number): boolean {
  return isBearish(o1, c1) && isBearish(o2, c2) && isBearish(o3, c3)
    && o2 < o1 && o2 > c1 && o3 < o2 && o3 > c2
    && c1 > 0 && c2 / c1 < 0.999 && c3 / c2 < 0.999;
}

function detectBullHarami(prevO: number, prevC: number, o: number, c: number): boolean {
  return isBearish(prevO, prevC) && isBullish(o, c)
    && o > prevC && c < prevO && bodySize(o, c) < bodySize(prevO, prevC) * 0.5;
}

function detectBearHarami(prevO: number, prevC: number, o: number, c: number): boolean {
  return isBullish(prevO, prevC) && isBearish(o, c)
    && o < prevC && c > prevO && bodySize(o, c) < bodySize(prevO, prevC) * 0.5;
}

function detectTweezerBottom(l1: number, l2: number): boolean {
  return Math.abs(l1 - l2) / Math.max(l1, l2, 0.0001) < 0.002;
}

function detectTweezerTop(h1: number, h2: number): boolean {
  return Math.abs(h1 - h2) / Math.max(h1, h2, 0.0001) < 0.002;
}

function detectBullAbandonedBaby(prevO: number, prevC: number, prevH: number,
  o2: number, c2: number, h2: number, l2: number,
  o: number, c: number, oL: number): boolean {
  return isBearish(prevO, prevC) && isDojiBody(o2, c2, h2, l2) && isBullish(o, c)
    && l2 > prevH && h2 < oL;
}

function detectBearAbandonedBaby(prevO: number, prevC: number, prevL: number,
  o2: number, c2: number, h2: number, l2: number,
  o: number, c: number, oH: number): boolean {
  return isBullish(prevO, prevC) && isDojiBody(o2, c2, h2, l2) && isBearish(o, c)
    && h2 < prevL && l2 > oH;
}

function detectDragonflyDoji(o: number, h: number, l: number, c: number): boolean {
  if (!isDojiBody(o, c, h, l)) return false;
  const uw = upperWick(o, c, h);
  const lw = lowerWick(o, c, l);
  return uw < bodySize(o, c) * 0.5 && lw > bodySize(o, c) * 3;
}

function detectGravestoneDoji(o: number, h: number, l: number, c: number): boolean {
  if (!isDojiBody(o, c, h, l)) return false;
  const uw = upperWick(o, c, h);
  const lw = lowerWick(o, c, l);
  return lw < bodySize(o, c) * 0.5 && uw > bodySize(o, c) * 3;
}

function detectThreeInsideUp(prevO: number, prevC: number, o2: number, c2: number, o3: number, c3: number): boolean {
  return detectBullHarami(prevO, prevC, o2, c2) && isBullish(o3, c3) && c3 > c2;
}

function detectThreeInsideDown(prevO: number, prevC: number, o2: number, c2: number, o3: number, c3: number): boolean {
  return detectBearHarami(prevO, prevC, o2, c2) && isBearish(o3, c3) && c3 < c2;
}

function detectThreeOutsideUp(prevO: number, prevC: number, o2: number, c2: number, o3: number, c3: number): boolean {
  return detectBullEngulf(prevO, prevC, o2, c2) && isBullish(o3, c3) && c3 > c2;
}

function detectThreeOutsideDown(prevO: number, prevC: number, o2: number, c2: number, o3: number, c3: number): boolean {
  return detectBearEngulf(prevO, prevC, o2, c2) && isBearish(o3, c3) && c3 < c2;
}

function detectBullKicker(prevO: number, prevC: number, o: number, c: number): boolean {
  return isBearish(prevO, prevC) && isBullish(o, c) && o > prevO && (o - prevO) > bodySize(prevO, prevC) * 0.5;
}

function detectBearKicker(prevO: number, prevC: number, o: number, c: number): boolean {
  return isBullish(prevO, prevC) && isBearish(o, c) && o < prevO && (prevO - o) > bodySize(prevO, prevC) * 0.5;
}

function detectDoji(o: number, h: number, l: number, c: number): boolean {
  return isDojiBody(o, c, h, l);
}

function detectSpinningTop(o: number, h: number, l: number, c: number): boolean {
  const body = bodySize(o, c);
  const range = totalRange(h, l);
  if (range === 0) return false;
  return body / range > 0.1 && body / range < 0.3 && upperWick(o, c, h) > body && lowerWick(o, c, l) > body;
}

function detectMarubozu(o: number, h: number, l: number, c: number): boolean {
  const body = bodySize(o, c);
  const range = totalRange(h, l);
  if (range === 0 || body === 0) return false;
  return body / range > 0.9;
}

function detectRisingThree(op: number[], cp: number[], hp: number[], lp: number[]): boolean {
  if (op.length < 5) return false;
  const i = op.length - 1;
  return isBullish(op[i-4], cp[i-4]) // first bullish
    && cp[i-3] < cp[i-4] && cp[i-2] < cp[i-3] && cp[i-1] < cp[i-2] // three small bearish
    && isBullish(op[i], cp[i]) && cp[i] > cp[i-4] // final bullish above first
    && bodySize(op[i-3], cp[i-3]) < bodySize(op[i-4], cp[i-4]) * 0.5
    && bodySize(op[i-2], cp[i-2]) < bodySize(op[i-4], cp[i-4]) * 0.5
    && bodySize(op[i-1], cp[i-1]) < bodySize(op[i-4], cp[i-4]) * 0.5;
}

function detectFallingThree(op: number[], cp: number[], hp: number[], lp: number[]): boolean {
  if (op.length < 5) return false;
  const i = op.length - 1;
  return isBearish(op[i-4], cp[i-4])
    && cp[i-3] > cp[i-4] && cp[i-2] > cp[i-3] && cp[i-1] > cp[i-2]
    && isBearish(op[i], cp[i]) && cp[i] < cp[i-4]
    && bodySize(op[i-3], cp[i-3]) < bodySize(op[i-4], cp[i-4]) * 0.5
    && bodySize(op[i-2], cp[i-2]) < bodySize(op[i-4], cp[i-4]) * 0.5
    && bodySize(op[i-1], cp[i-1]) < bodySize(op[i-4], cp[i-4]) * 0.5;
}

function detectBullMatHold(op: number[], cp: number[]): boolean {
  if (op.length < 5) return false;
  const i = op.length - 1;
  return isBullish(op[i-4], cp[i-4]) && cp[i-4] > 0 && cp[i-3]/cp[i-4] > 1.01
    && isBearish(op[i-3], cp[i-3]) && isBearish(op[i-2], cp[i-2]) && isBearish(op[i-1], cp[i-1])
    && isBullish(op[i], cp[i]) && cp[i] > cp[i-4];
}

function detectBearMatHold(op: number[], cp: number[]): boolean {
  if (op.length < 5) return false;
  const i = op.length - 1;
  return isBearish(op[i-4], cp[i-4]) && cp[i-4] > 0 && cp[i-3]/cp[i-4] < 0.99
    && isBullish(op[i-3], cp[i-3]) && isBullish(op[i-2], cp[i-2]) && isBullish(op[i-1], cp[i-1])
    && isBearish(op[i], cp[i]) && cp[i] < cp[i-4];
}

function detectHighWave(o: number, h: number, l: number, c: number): boolean {
  const body = bodySize(o, c);
  const range = totalRange(h, l);
  if (range === 0) return false;
  return body / range < 0.15 && upperWick(o, c, h) > body * 2 && lowerWick(o, c, l) > body * 2;
}

function detectHangingMan(o: number, h: number, l: number, c: number, prevO: number, prevC: number): boolean {
  return detectHammer(o, h, l, c, prevC) && (isBullish(prevO, prevC));
}

function detectShootingStar(o: number, h: number, l: number, c: number, prevO: number, prevC: number): boolean {
  return detectInvHammer(o, h, l, c, prevC) && (isBullish(prevO, prevC));
}

// ============================================================
// PATTERN SCANNER
// ============================================================

function scanSingleTimeframe(series: OHLCVSeries, timeframe: string): DetectedPattern[] {
  const { opens: o, highs: h, lows: l, closes: c } = series;
  const n = o.length;
  const patterns: DetectedPattern[] = [];
  if (n < 5) return patterns;

  const i = n - 1; // last candle

  // === 1-candle patterns ===
  if (detectHammer(o[i], h[i], l[i], c[i], c[i-1])) {
    patterns.push(makeDetected('Hammer', timeframe, i, c[i], h, l));
  }
  if (detectInvHammer(o[i], h[i], l[i], c[i], c[i-1])) {
    patterns.push(makeDetected('InvHammer', timeframe, i, c[i], h, l));
  }
  if (detectHangingMan(o[i], h[i], l[i], c[i], o[i-1], c[i-1])) {
    patterns.push(makeDetected('HangingMan', timeframe, i, c[i], h, l));
  }
  if (detectShootingStar(o[i], h[i], l[i], c[i], o[i-1], c[i-1])) {
    patterns.push(makeDetected('ShootingStar', timeframe, i, c[i], h, l));
  }
  if (detectDoji(o[i], h[i], l[i], c[i])) {
    patterns.push(makeDetected('Doji', timeframe, i, c[i], h, l));
  }
  if (detectSpinningTop(o[i], h[i], l[i], c[i])) {
    patterns.push(makeDetected('SpinningTop', timeframe, i, c[i], h, l));
  }
  if (detectMarubozu(o[i], h[i], l[i], c[i])) {
    patterns.push(makeDetected('Marubozu', timeframe, i, c[i], h, l));
  }
  if (detectDragonflyDoji(o[i], h[i], l[i], c[i])) {
    patterns.push(makeDetected('DragonflyDoji', timeframe, i, c[i], h, l));
  }
  if (detectGravestoneDoji(o[i], h[i], l[i], c[i])) {
    patterns.push(makeDetected('GravestoneDoji', timeframe, i, c[i], h, l));
  }
  if (detectHighWave(o[i], h[i], l[i], c[i])) {
    patterns.push(makeDetected('HighWave', timeframe, i, c[i], h, l));
  }

  // === 2-candle patterns ===
  if (detectBullEngulf(o[i-1], c[i-1], o[i], c[i])) {
    patterns.push(makeDetected('BullEngulf', timeframe, i, c[i], h, l));
  }
  if (detectBearEngulf(o[i-1], c[i-1], o[i], c[i])) {
    patterns.push(makeDetected('BearEngulf', timeframe, i, c[i], h, l));
  }
  if (detectPiercing(o[i-1], c[i-1], o[i], c[i])) {
    patterns.push(makeDetected('Piercing', timeframe, i, c[i], h, l));
  }
  if (detectDarkCloud(o[i-1], c[i-1], o[i], c[i])) {
    patterns.push(makeDetected('DarkCloud', timeframe, i, c[i], h, l));
  }
  if (detectBullHarami(o[i-1], c[i-1], o[i], c[i])) {
    patterns.push(makeDetected('BullHarami', timeframe, i, c[i], h, l));
  }
  if (detectBearHarami(o[i-1], c[i-1], o[i], c[i])) {
    patterns.push(makeDetected('BearHarami', timeframe, i, c[i], h, l));
  }
  if (detectTweezerBottom(l[i-1], l[i])) {
    patterns.push(makeDetected('TweezerBottom', timeframe, i, c[i], h, l));
  }
  if (detectTweezerTop(h[i-1], h[i])) {
    patterns.push(makeDetected('TweezerTop', timeframe, i, c[i], h, l));
  }
  if (detectBullKicker(o[i-1], c[i-1], o[i], c[i])) {
    patterns.push(makeDetected('BullKicker', timeframe, i, c[i], h, l));
  }
  if (detectBearKicker(o[i-1], c[i-1], o[i], c[i])) {
    patterns.push(makeDetected('BearKicker', timeframe, i, c[i], h, l));
  }

  // === 3-candle patterns ===
  if (n >= 3) {
    if (detectMorningStar(o[i-2], c[i-2], o[i-1], c[i-1], o[i], c[i])) {
      patterns.push(makeDetected('MorningStar', timeframe, i, c[i], h, l));
    }
    if (detectEveningStar(o[i-2], c[i-2], o[i-1], c[i-1], o[i], c[i])) {
      patterns.push(makeDetected('EveningStar', timeframe, i, c[i], h, l));
    }
    if (detectMorningDoji(o[i-2], c[i-2], o[i-1], c[i-1], h[i-1], l[i-1], o[i], c[i])) {
      patterns.push(makeDetected('MorningDoji', timeframe, i, c[i], h, l));
    }
    if (detectEveningDoji(o[i-2], c[i-2], o[i-1], c[i-1], h[i-1], l[i-1], o[i], c[i])) {
      patterns.push(makeDetected('EveningDoji', timeframe, i, c[i], h, l));
    }
    if (detectThreeWhiteSoldiers(o[i-2], c[i-2], o[i-1], c[i-1], o[i], c[i])) {
      patterns.push(makeDetected('ThreeWhiteSoldiers', timeframe, i, c[i], h, l));
    }
    if (detectThreeBlackCrows(o[i-2], c[i-2], o[i-1], c[i-1], o[i], c[i])) {
      patterns.push(makeDetected('ThreeBlackCrows', timeframe, i, c[i], h, l));
    }
    if (n >= 4 && detectBullAbandonedBaby(o[i-2], c[i-2], h[i-2], o[i-1], c[i-1], h[i-1], l[i-1], o[i], c[i], l[i])) {
      patterns.push(makeDetected('BullAbandonedBaby', timeframe, i, c[i], h, l));
    }
    if (n >= 4 && detectBearAbandonedBaby(o[i-2], c[i-2], l[i-2], o[i-1], c[i-1], h[i-1], l[i-1], o[i], c[i], h[i])) {
      patterns.push(makeDetected('BearAbandonedBaby', timeframe, i, c[i], h, l));
    }
    if (detectThreeInsideUp(o[i-2], c[i-2], o[i-1], c[i-1], o[i], c[i])) {
      patterns.push(makeDetected('ThreeInsideUp', timeframe, i, c[i], h, l));
    }
    if (detectThreeInsideDown(o[i-2], c[i-2], o[i-1], c[i-1], o[i], c[i])) {
      patterns.push(makeDetected('ThreeInsideDown', timeframe, i, c[i], h, l));
    }
    if (detectThreeOutsideUp(o[i-2], c[i-2], o[i-1], c[i-1], o[i], c[i])) {
      patterns.push(makeDetected('ThreeOutsideUp', timeframe, i, c[i], h, l));
    }
    if (detectThreeOutsideDown(o[i-2], c[i-2], o[i-1], c[i-1], o[i], c[i])) {
      patterns.push(makeDetected('ThreeOutsideDown', timeframe, i, c[i], h, l));
    }
  }

  // === 5-candle patterns ===
  if (n >= 5) {
    if (detectRisingThree(o, c, h, l)) {
      patterns.push(makeDetected('RisingThree', timeframe, i, c[i], h, l));
    }
    if (detectFallingThree(o, c, h, l)) {
      patterns.push(makeDetected('FallingThree', timeframe, i, c[i], h, l));
    }
    if (detectBullMatHold(o, c)) {
      patterns.push(makeDetected('BullMatHold', timeframe, i, c[i], h, l));
    }
    if (detectBearMatHold(o, c)) {
      patterns.push(makeDetected('BearMatHold', timeframe, i, c[i], h, l));
    }
  }

  return patterns;
}

function makeDetected(name: string, timeframe: string, index: number, price: number, h: number[], l: number[]): DetectedPattern {
  const def = PATTERN_DEFS[name];
  const tfWeight = TIMEFRAME_WEIGHTS[timeframe] ?? 1.0;
  return {
    pattern: name,
    category: def.category,
    direction: def.direction,
    timeframe,
    confidence: Math.min(1, def.reliability * tfWeight),
    reliability: def.reliability,
    weight: tfWeight,
    index,
    description: `${name} on ${timeframe} (${def.direction}, reliability: ${(def.reliability * 100).toFixed(0)}%)`,
    priceAtDetection: price,
  };
}

// ============================================================
// CONFLUENCE DETECTION
// ============================================================

function detectConfluences(patterns: DetectedPattern[]): PatternConfluence[] {
  const confluences: PatternConfluence[] = [];
  const byName: Record<string, DetectedPattern[]> = {};

  for (const p of patterns) {
    if (!byName[p.pattern]) byName[p.pattern] = [];
    byName[p.pattern].push(p);
  }

  for (const [name, instances] of Object.entries(byName)) {
    if (instances.length >= 2) {
      const uniqueTimeframes = [...new Set(instances.map(p => p.timeframe))];
      if (uniqueTimeframes.length >= 2) {
        const combinedWeight = instances.reduce((s, p) => s + p.weight, 0);
        const combinedConfidence = Math.min(1, instances.reduce((s, p) => s + p.confidence, 0) / instances.length * 1.3);
        confluences.push({
          pattern: name,
          timeframes: uniqueTimeframes,
          direction: instances[0].direction,
          combinedWeight,
          combinedConfidence,
          description: `${name} confluence on ${uniqueTimeframes.join('+')} (${instances[0].direction})`,
        });
      }
    }
  }

  return confluences.sort((a, b) => b.combinedWeight - a.combinedWeight);
}

// ============================================================
// ENGINE CLASS
// ============================================================

class CandlestickPatternEngine {
  /**
   * Scan a token for candlestick patterns across multiple timeframes.
   * This is the main entry point.
   */
  async scanToken(tokenAddress: string, chain: string = 'SOL'): Promise<PatternScanResult> {
    const allPatterns: DetectedPattern[] = [];
    const scanTfs: string[] = [];

    for (const tf of SCAN_TIMEFRAMES) {
      try {
        const series = await ohlcvPipeline.getCandleSeries(tokenAddress, tf, 100);
        if (series.count < 5) continue;
        scanTfs.push(tf);
        const tfPatterns = scanSingleTimeframe(series, tf);
        allPatterns.push(...tfPatterns);
      } catch {
        // Skip timeframe if data unavailable
      }
    }

    // Categorize
    const bullishPatterns = allPatterns.filter(p => p.direction === 'BULLISH');
    const bearishPatterns = allPatterns.filter(p => p.direction === 'BEARISH');
    const neutralPatterns = allPatterns.filter(p => p.direction === 'NEUTRAL');

    // Detect confluences
    const confluences = detectConfluences(allPatterns);

    // Calculate overall signal
    let bullScore = 0;
    let bearScore = 0;
    for (const p of bullishPatterns) bullScore += p.confidence * p.weight;
    for (const p of bearishPatterns) bearScore += p.confidence * p.weight;
    // Boost confluences
    for (const c of confluences) {
      if (c.direction === 'BULLISH') bullScore += c.combinedConfidence * c.combinedWeight * 0.5;
      else if (c.direction === 'BEARISH') bearScore += c.combinedConfidence * c.combinedWeight * 0.5;
    }

    const totalScore = bullScore + bearScore;
    const overallScore = totalScore > 0 ? (bullScore - bearScore) / totalScore : 0;
    let overallSignal: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
    if (overallScore > 0.2) overallSignal = 'BULLISH';
    else if (overallScore < -0.2) overallSignal = 'BEARISH';
    else overallSignal = 'NEUTRAL';

    // Dominant pattern (highest confidence × weight)
    const dominant = allPatterns.sort((a, b) => (b.confidence * b.weight) - (a.confidence * a.weight))[0];
    const dominantTimeframe = confluences.length > 0 ? confluences[0].timeframes[0] : (dominant?.timeframe ?? null);

    return {
      tokenAddress,
      patterns: allPatterns,
      bullishPatterns,
      bearishPatterns,
      neutralPatterns,
      confluences,
      overallSignal,
      overallScore,
      dominantPattern: dominant?.pattern ?? null,
      dominantTimeframe,
      scanTimeframes: scanTfs,
      timestamp: new Date(),
    };
  }

  /**
   * Scan multiple tokens in batch.
   */
  async scanBatch(tokenAddresses: string[], chain: string = 'SOL'): Promise<Map<string, PatternScanResult>> {
    const results = new Map<string, PatternScanResult>();
    for (const addr of tokenAddresses) {
      try {
        const result = await this.scanToken(addr, chain);
        results.set(addr, result);
      } catch {
        // Skip failed scans
      }
      await new Promise(r => setTimeout(r, 100)); // Rate limit
    }
    return results;
  }

  /**
   * Get the pattern definition library (for UI display).
   */
  getPatternDefinitions(): Record<string, CandlestickPattern> {
    return { ...PATTERN_DEFS };
  }

  /**
   * Get timeframe weights.
   */
  getTimeframeWeights(): Record<string, number> {
    return { ...TIMEFRAME_WEIGHTS };
  }

  /**
   * Scan a token across multiple timeframes (alias for scanToken).
   * The scanToken method already scans across all configured timeframes.
   */
  async scanMultiTimeframe(tokenAddress: string, chain: string = 'SOL'): Promise<PatternScanResult> {
    return this.scanToken(tokenAddress, chain);
  }

  /**
   * Store pattern scan results in DB for historical analysis.
   */
  async storeScanResults(result: PatternScanResult, cycleRunId?: string): Promise<void> {
    try {
      // Find token for foreign key
      const token = await db.token.findFirst({
        where: { address: result.tokenAddress },
      });
      if (!token) return;

      // Store each detected pattern as a signal
      for (const p of result.patterns.slice(0, 20)) { // Limit to 20 per scan
        await db.signal.create({
          data: {
            type: `CANDLESTICK_${p.pattern}`,
            direction: p.direction,
            confidence: Math.round(p.confidence * 100),
            description: p.description,
            tokenId: token.id,
            metadata: JSON.stringify({
              pattern: p.pattern,
              category: p.category,
              timeframe: p.timeframe,
              weight: p.weight,
              reliability: p.reliability,
              priceAtDetection: p.priceAtDetection,
              tokenAddress: result.tokenAddress,
              cycleRunId,
            }),
          },
        });
      }

      // Store confluences
      for (const c of result.confluences) {
        await db.signal.create({
          data: {
            type: `CANDLESTICK_CONFLUENCE_${c.pattern}`,
            direction: c.direction,
            confidence: Math.round(c.combinedConfidence * 100),
            description: c.description,
            tokenId: token.id,
            metadata: JSON.stringify({
              pattern: c.pattern,
              timeframes: c.timeframes,
              combinedWeight: c.combinedWeight,
              tokenAddress: result.tokenAddress,
              cycleRunId,
            }),
          },
        });
      }
    } catch {
      // Storage is best-effort
    }
  }
}

export type PatternSentiment = 'BULLISH' | 'BEARISH' | 'NEUTRAL';

export const candlestickPatternEngine = new CandlestickPatternEngine();
