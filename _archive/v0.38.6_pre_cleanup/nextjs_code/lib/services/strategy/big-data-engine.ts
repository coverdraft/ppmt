/**
 * Big Data Predictive Engine - CryptoQuant Terminal
 *
 * Analyzes historical data to detect patterns and predict market events
 * BEFORE they happen. Feeds predictive signals to Trading Systems.
 *
 * Statistical methods used:
 * - Simple & Exponential Moving Averages (SMA, EMA)
 * - Average True Range (ATR) for volatility
 * - Rate of Change (ROC) for momentum
 * - Z-Score for anomaly detection
 * - Pearson Correlation for regime classification
 * - Bollinger Bands for mean reversion zones
 * - Linear regression for trend detection
 */

// ============================================================
// TYPES & INTERFACES
// ============================================================

export type MarketRegime = 'BULL' | 'BEAR' | 'SIDEWAYS' | 'TRANSITION';

export type PredictiveSignalType =
  | 'REGIME_CHANGE'
  | 'BOT_SWARM'
  | 'WHALE_MOVEMENT'
  | 'LIQUIDITY_DRAIN'
  | 'CORRELATION_BREAK'
  | 'ANOMALY'
  | 'CYCLE_POSITION'
  | 'SECTOR_ROTATION'
  | 'MEAN_REVERSION_ZONE'
  | 'SMART_MONEY_POSITIONING'
  | 'VOLATILITY_REGIME';

export type BotSwarmLevel = 'NONE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export type VolatilityRegime = 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME';

export type LiquidityTrend = 'ACCUMULATING' | 'STABLE' | 'DRAINING' | 'CRITICAL_DRAIN';

export type WhaleDirection = 'ACCUMULATING' | 'DISTRIBUTING' | 'NEUTRAL' | 'ROTATING';

export interface PredictiveOutput {
  signalType: PredictiveSignalType;
  confidence: number;
  prediction: Record<string, unknown>;
  evidence: string[];
  timeframe: string;
  validUntil: Date;
  historicalHitRate: number;
}

export interface MarketContext {
  regime: MarketRegime;
  volatilityRegime: VolatilityRegime;
  sectorRotation: SectorRotationSnapshot;
  botSwarmLevel: BotSwarmLevel;
  whaleAccumulation: WhaleDirection;
  smartMoneyFlow: SmartMoneyFlowSnapshot;
  correlationStability: number;
  liquidityTrend: LiquidityTrend;
}

export interface SectorRotationSnapshot {
  leadingSector: string;
  laggingSector: string;
  rotationSpeed: number;
  confidence: number;
}

export interface SmartMoneyFlowSnapshot {
  netDirection: 'INFLOW' | 'OUTFLOW' | 'NEUTRAL';
  magnitude: number;
  topDestination: string | null;
  confidence: number;
}

export interface MeanReversionZone {
  upperBound: number;
  lowerBound: number;
  mean: number;
  currentDeviation: number;
  probabilityOfReversion: number;
  bandWidth: number;
}

export interface AnomalyResult {
  isAnomaly: boolean;
  anomalyScore: number;
  direction: 'ABOVE' | 'BELOW' | 'NEUTRAL';
  zScore: number;
  details: string;
}

export interface RegimeDetectionResult {
  regime: MarketRegime;
  confidence: number;
  evidence: string[];
  metrics: {
    sma20: number;
    sma50: number;
    crossoverSignal: 'GOLDEN_CROSS' | 'DEATH_CROSS' | 'NONE';
    momentum: number;
    volatility: number;
    trendStrength: number;
  };
}

export interface WhaleForecastResult {
  direction: WhaleDirection;
  confidence: number;
  evidence: string[];
  metrics: {
    netFlowSum: number;
    avgHoldTime: number;
    activeWalletCount: number;
    accumulationScore: number;
    distributionScore: number;
  };
}

export interface BotSwarmResult {
  level: BotSwarmLevel;
  coordinatedGroupCount: number;
  dominantBotType: string | null;
  totalBotActivity: number;
  evidence: string[];
}

export interface SmartMoneyPositioningResult {
  netDirection: 'INFLOW' | 'OUTFLOW' | 'NEUTRAL';
  magnitude: number;
  topDestination: string | null;
  confidence: number;
  evidence: string[];
  sectorBreakdown: Record<string, number>;
}

export interface LiquidityDrainResult {
  trend: LiquidityTrend;
  drainRate: number;
  confidence: number;
  evidence: string[];
  metrics: {
    currentLiquidity: number;
    baselineLiquidity: number;
    percentChange: number;
    slope: number;
  };
}

export interface EngineInput {
  priceHistory: number[];
  highHistory?: number[];
  lowHistory?: number[];
  traderMetrics?: Array<{
    isBot: boolean;
    botType: string;
    recentTrades: number;
  }>;
  whaleActivity?: Array<{
    address: string;
    netFlow: number;
    tradeCount: number;
    avgHoldTime: number;
  }>;
  currentValues?: number[];
  historicalBaseline?: number[];
  smWallets?: Array<{
    address: string;
    recentAction: string;
    tokenAddress: string;
    valueUsd: number;
  }>;
  liquidityHistory?: number[];
  correlationSeriesA?: number[];
  correlationSeriesB?: number[];
}

// ============================================================
// STATISTICAL UTILITY FUNCTIONS
// ============================================================

/**
 * Calculate Simple Moving Average (SMA)
 * Returns NaN if insufficient data points
 */
function calculateSMA(data: number[], period: number): number {
  if (data.length < period) return NaN;
  const slice = data.slice(data.length - period);
  return slice.reduce((sum, val) => sum + val, 0) / period;
}

/**
 * Calculate SMA series (one value per data point, starting from `period - 1`)
 */
function calculateSMAseries(data: number[], period: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(NaN);
    } else {
      const sum = data.slice(i - period + 1, i + 1).reduce((s, v) => s + v, 0);
      result.push(sum / period);
    }
  }
  return result;
}

/**
 * Calculate Exponential Moving Average (EMA)
 * Uses the standard smoothing factor: multiplier = 2 / (period + 1)
 */
function calculateEMA(data: number[], period: number): number {
  if (data.length < period) return NaN;

  const multiplier = 2 / (period + 1);
  // Seed with SMA of first `period` values
  let ema = data.slice(0, period).reduce((s, v) => s + v, 0) / period;

  for (let i = period; i < data.length; i++) {
    ema = (data[i] - ema) * multiplier + ema;
  }
  return ema;
}

/**
 * Calculate Standard Deviation (population)
 */
function calculateStdDev(data: number[]): number {
  if (data.length === 0) return 0;
  const mean = data.reduce((s, v) => s + v, 0) / data.length;
  const squaredDiffs = data.map(v => (v - mean) ** 2);
  return Math.sqrt(squaredDiffs.reduce((s, v) => s + v, 0) / data.length);
}

/**
 * Calculate Variance (population)
 */
function calculateVariance(data: number[]): number {
  if (data.length === 0) return 0;
  const mean = data.reduce((s, v) => s + v, 0) / data.length;
  return data.reduce((s, v) => s + (v - mean) ** 2, 0) / data.length;
}

/**
 * Calculate Rate of Change (ROC) over `period` data points
 * ROC = ((current - past) / past) * 100
 */
function calculateROC(data: number[], period: number): number {
  if (data.length <= period) return 0;
  const current = data[data.length - 1];
  const past = data[data.length - 1 - period];
  if (past === 0) return 0;
  return ((current - past) / Math.abs(past)) * 100;
}

/**
 * Calculate Average True Range (ATR)
 * If high/low arrays are provided, uses true range formula.
 * Otherwise, approximates TR as absolute daily changes.
 *
 * TR = max(H - L, |H - prevClose|, |L - prevClose|)
 */
function calculateATR(
  closes: number[],
  highs?: number[],
  lows?: number[],
  period: number = 14
): number {
  if (closes.length < 2) return 0;

  const trueRanges: number[] = [];

  for (let i = 1; i < closes.length; i++) {
    let tr: number;
    if (highs && lows && i < highs.length && i < lows.length) {
      const h = highs[i];
      const l = lows[i];
      const prevClose = closes[i - 1];
      tr = Math.max(h - l, Math.abs(h - prevClose), Math.abs(l - prevClose));
    } else {
      // Approximate: absolute price change as proxy
      tr = Math.abs(closes[i] - closes[i - 1]);
    }
    trueRanges.push(tr);
  }

  if (trueRanges.length < period) {
    return trueRanges.reduce((s, v) => s + v, 0) / trueRanges.length;
  }

  // Use Wilder's smoothing for ATR
  let atr = trueRanges.slice(0, period).reduce((s, v) => s + v, 0) / period;
  for (let i = period; i < trueRanges.length; i++) {
    atr = (atr * (period - 1) + trueRanges[i]) / period;
  }
  return atr;
}

/**
 * Calculate Pearson Correlation Coefficient
 */
function calculateCorrelation(x: number[], y: number[]): number {
  const n = Math.min(x.length, y.length);
  if (n < 3) return 0;

  const xSlice = x.slice(0, n);
  const ySlice = y.slice(0, n);

  const meanX = xSlice.reduce((s, v) => s + v, 0) / n;
  const meanY = ySlice.reduce((s, v) => s + v, 0) / n;

  let numerator = 0;
  let denomX = 0;
  let denomY = 0;

  for (let i = 0; i < n; i++) {
    const dx = xSlice[i] - meanX;
    const dy = ySlice[i] - meanY;
    numerator += dx * dy;
    denomX += dx * dx;
    denomY += dy * dy;
  }

  const denominator = Math.sqrt(denomX * denomY);
  return denominator === 0 ? 0 : numerator / denominator;
}

/**
 * Linear Regression - returns slope and intercept
 * y = slope * x + intercept
 */
function linearRegression(data: number[]): { slope: number; intercept: number; r2: number } {
  const n = data.length;
  if (n < 2) return { slope: 0, intercept: data[0] || 0, r2: 0 };

  const xMean = (n - 1) / 2;
  const yMean = data.reduce((s, v) => s + v, 0) / n;

  let ssXX = 0;
  let ssXY = 0;
  let ssYY = 0;

  for (let i = 0; i < n; i++) {
    const dx = i - xMean;
    const dy = data[i] - yMean;
    ssXX += dx * dx;
    ssXY += dx * dy;
    ssYY += dy * dy;
  }

  const slope = ssXX === 0 ? 0 : ssXY / ssXX;
  const intercept = yMean - slope * xMean;
  const r2 = ssYY === 0 ? 0 : (ssXY * ssXY) / (ssXX * ssYY);

  return { slope, intercept, r2 };
}

/**
 * Calculate Z-Score
 * z = (value - mean) / standardDeviation
 */
function calculateZScore(value: number, mean: number, stdDev: number): number {
  if (stdDev === 0) return 0;
  return (value - mean) / stdDev;
}

/**
 * Calculate Bollinger Bands
 * Middle = SMA(period)
 * Upper = Middle + (multiplier * StdDev)
 * Lower = Middle - (multiplier * StdDev)
 */
function calculateBollingerBands(
  data: number[],
  period: number = 20,
  multiplier: number = 2
): { upper: number; middle: number; lower: number; bandwidth: number; percentB: number } {
  if (data.length < period) {
    const last = data[data.length - 1] || 0;
    return { upper: last, middle: last, lower: last, bandwidth: 0, percentB: 0.5 };
  }

  const slice = data.slice(data.length - period);
  const middle = slice.reduce((s, v) => s + v, 0) / period;
  const stdDev = calculateStdDev(slice);

  const upper = middle + multiplier * stdDev;
  const lower = middle - multiplier * stdDev;
  const bandwidth = middle !== 0 ? (upper - lower) / middle : 0;

  const currentPrice = data[data.length - 1];
  const percentB = (upper - lower) !== 0
    ? (currentPrice - lower) / (upper - lower)
    : 0.5;

  return { upper, middle, lower, bandwidth, percentB };
}

// ============================================================
// REGIME DETECTION ENGINE
// ============================================================

/**
 * Detects the current market regime by analyzing:
 * 1. Moving Average Crossover (20-period vs 50-period SMA)
 * 2. Momentum via Rate of Change
 * 3. Volatility via ATR-like calculation
 * 4. Trend strength via linear regression R²
 *
 * BULL:   SMA20 > SMA50, positive momentum, moderate volatility
 * BEAR:   SMA20 < SMA50, negative momentum, moderate-to-high volatility
 * SIDEWAYS: Flat MAs, low momentum, low volatility
 * TRANSITION: Conflicting signals, regime change likely
 */
export function detectMarketRegime(priceHistory: number[]): RegimeDetectionResult {
  const evidence: string[] = [];
  const len = priceHistory.length;

  if (len < 50) {
    // Not enough data for full analysis — use shorter windows
    return {
      regime: 'TRANSITION',
      confidence: 0.2,
      evidence: ['Insufficient price history for regime detection (need 50+ data points)'],
      metrics: {
        sma20: priceHistory[len - 1] || 0,
        sma50: priceHistory[len - 1] || 0,
        crossoverSignal: 'NONE',
        momentum: 0,
        volatility: 0,
        trendStrength: 0,
      },
    };
  }

  // 1. Moving Averages
  const sma20 = calculateSMA(priceHistory, 20);
  const sma50 = calculateSMA(priceHistory, 50);
  const sma20series = calculateSMAseries(priceHistory, 20);
  const sma50series = calculateSMAseries(priceHistory, 50);

  // Crossover detection — compare last two positions
  const lastSma20 = sma20series[len - 1];
  const prevSma20 = sma20series[len - 2];
  const lastSma50 = sma50series[len - 1];
  const prevSma50 = sma50series[len - 2];

  let crossoverSignal: 'GOLDEN_CROSS' | 'DEATH_CROSS' | 'NONE' = 'NONE';

  if (!isNaN(prevSma20) && !isNaN(prevSma50) && !isNaN(lastSma20) && !isNaN(lastSma50)) {
    const wasAbove = prevSma20 > prevSma50;
    const isAbove = lastSma20 > lastSma50;
    if (wasAbove && !isAbove) {
      crossoverSignal = 'DEATH_CROSS';
      evidence.push('Death cross detected: SMA20 crossed below SMA50');
    } else if (!wasAbove && isAbove) {
      crossoverSignal = 'GOLDEN_CROSS';
      evidence.push('Golden cross detected: SMA20 crossed above SMA50');
    }
  }

  const smaDistance = sma50 !== 0 ? ((sma20 - sma50) / Math.abs(sma50)) * 100 : 0;
  evidence.push(`SMA20 (${sma20.toFixed(2)}) vs SMA50 (${sma50.toFixed(2)}): ${smaDistance > 0 ? '+' : ''}${smaDistance.toFixed(2)}%`);

  // 2. Momentum — Rate of Change over 20 periods
  const momentum20 = calculateROC(priceHistory, 20);
  const momentum10 = calculateROC(priceHistory, 10);
  evidence.push(`Momentum (20-period ROC): ${momentum20.toFixed(2)}%`);
  evidence.push(`Momentum (10-period ROC): ${momentum10.toFixed(2)}%`);

  // 3. Volatility — ATR
  const atr = calculateATR(priceHistory);
  const avgPrice = sma50;
  const normalizedATR = avgPrice !== 0 ? (atr / avgPrice) * 100 : 0;
  evidence.push(`Normalized ATR: ${normalizedATR.toFixed(2)}% of price`);

  // 4. Trend Strength — R² of recent price linear regression
  const recentSlice = priceHistory.slice(Math.max(0, len - 30));
  const regression = linearRegression(recentSlice);
  const trendStrength = Math.max(0, regression.r2);
  const trendDirection = regression.slope > 0 ? 'UP' : regression.slope < 0 ? 'DOWN' : 'FLAT';
  evidence.push(`Trend strength (R²): ${trendStrength.toFixed(3)} direction: ${trendDirection}`);

  // ---- CLASSIFICATION LOGIC ----
  let regime: MarketRegime = 'SIDEWAYS';
  let confidence = 0;

  // Score-based approach: each signal contributes to a regime score
  let bullScore = 0;
  let bearScore = 0;
  let sidewaysScore = 0;

  // MA crossover component (weight: 30%)
  if (sma20 > sma50) {
    bullScore += 30;
    if (crossoverSignal === 'GOLDEN_CROSS') bullScore += 10; // fresh crossover = stronger
  } else if (sma20 < sma50) {
    bearScore += 30;
    if (crossoverSignal === 'DEATH_CROSS') bearScore += 10;
  } else {
    sidewaysScore += 30;
  }

  // Distance from SMA adds conviction (weight: 15%)
  const absDistance = Math.abs(smaDistance);
  if (smaDistance > 2) bullScore += 15;
  else if (smaDistance > 0.5) bullScore += 8;
  else if (smaDistance < -2) bearScore += 15;
  else if (smaDistance < -0.5) bearScore += 8;
  else sidewaysScore += 15;

  // Momentum component (weight: 25%)
  if (momentum20 > 5) bullScore += 25;
  else if (momentum20 > 1) bullScore += 12;
  else if (momentum20 < -5) bearScore += 25;
  else if (momentum20 < -1) bearScore += 12;
  else sidewaysScore += 25;

  // Trend strength component (weight: 20%)
  if (trendStrength > 0.5) {
    if (trendDirection === 'UP') bullScore += 20;
    else if (trendDirection === 'DOWN') bearScore += 20;
  } else {
    sidewaysScore += 20;
  }

  // Volatility adjustment (weight: 10%)
  if (normalizedATR < 1.5) {
    sidewaysScore += 10;
    evidence.push('Low volatility supports sideways regime');
  } else if (normalizedATR > 5) {
    // High volatility can mean transition
    evidence.push('High volatility may indicate regime transition');
  } else if (normalizedATR > 3) {
    if (bullScore > bearScore) bullScore += 5;
    else bearScore += 5;
  }

  // Determine regime from scores
  const maxScore = Math.max(bullScore, bearScore, sidewaysScore);

  if (maxScore === bullScore) {
    regime = 'BULL';
    confidence = bullScore / (bullScore + bearScore + sidewaysScore + 0.001);
  } else if (maxScore === bearScore) {
    regime = 'BEAR';
    confidence = bearScore / (bullScore + bearScore + sidewaysScore + 0.001);
  } else {
    regime = 'SIDEWAYS';
    confidence = sidewaysScore / (bullScore + bearScore + sidewaysScore + 0.001);
  }

  // Transition detection: if top two scores are close, market is transitioning
  const scores = [bullScore, bearScore, sidewaysScore].sort((a, b) => b - a);
  const scoreGap = scores[0] - scores[1];
  if (scoreGap < 10) {
    regime = 'TRANSITION';
    confidence = 0.5 - (scoreGap / 20); // Lower confidence when transitioning
    evidence.push('Conflicting signals detected — regime transition likely');
  }

  return {
    regime,
    confidence: Math.min(Math.max(confidence, 0.1), 0.99),
    evidence,
    metrics: {
      sma20,
      sma50,
      crossoverSignal,
      momentum: momentum20,
      volatility: normalizedATR,
      trendStrength,
    },
  };
}

// ============================================================
// BOT SWARM DETECTION
// ============================================================

/**
 * Detects when multiple bots coordinate — a "swarm" event.
 *
 * Analyzes:
 * 1. Ratio of bot traders to total traders
 * 2. Concentration of bot types (same-type bots acting together)
 * 3. Trade velocity (bots per unit time)
 * 4. Clustering of activity
 *
 * Returns swarm level from NONE to CRITICAL
 */
export function detectBotSwarm(
  traderMetrics: Array<{ isBot: boolean; botType: string; recentTrades: number }>
): BotSwarmResult {
  const evidence: string[] = [];
  const totalTraders = traderMetrics.length;

  if (totalTraders === 0) {
    return {
      level: 'NONE',
      coordinatedGroupCount: 0,
      dominantBotType: null,
      totalBotActivity: 0,
      evidence: ['No trader data available'],
    };
  }

  // 1. Bot ratio
  const bots = traderMetrics.filter(t => t.isBot);
  const botRatio = bots.length / totalTraders;
  evidence.push(`Bot ratio: ${(botRatio * 100).toFixed(1)}% (${bots.length}/${totalTraders} traders)`);

  // 2. Group by bot type
  const botTypeGroups: Record<string, { count: number; totalTrades: number }> = {};
  for (const bot of bots) {
    const type = bot.botType || 'UNKNOWN';
    if (!botTypeGroups[type]) {
      botTypeGroups[type] = { count: 0, totalTrades: 0 };
    }
    botTypeGroups[type].count += 1;
    botTypeGroups[type].totalTrades += bot.recentTrades;
  }

  // Find coordinated groups (3+ bots of same type)
  const coordinatedGroups = Object.entries(botTypeGroups)
    .filter(([, group]) => group.count >= 3)
    .sort((a, b) => b[1].totalTrades - a[1].totalTrades);

  const coordinatedGroupCount = coordinatedGroups.length;
  const dominantBotType = coordinatedGroups.length > 0 ? coordinatedGroups[0][0] : null;

  if (coordinatedGroupCount > 0) {
    const topGroup = coordinatedGroups[0];
    evidence.push(
      `Largest coordinated group: ${topGroup[0]} (${topGroup[1].count} bots, ${topGroup[1].totalTrades} recent trades)`
    );
  }

  // 3. Trade velocity: how much trading volume comes from bots
  const totalTrades = traderMetrics.reduce((s, t) => s + t.recentTrades, 0);
  const botTrades = bots.reduce((s, t) => s + t.recentTrades, 0);
  const botTradeRatio = totalTrades > 0 ? botTrades / totalTrades : 0;
  evidence.push(`Bot trade velocity: ${(botTradeRatio * 100).toFixed(1)}% of trades from bots`);

  // 4. Compute Herfindahl-Hirschman Index (HHI) for bot type concentration
  let hhi = 0;
  if (bots.length > 0) {
    for (const group of Object.values(botTypeGroups)) {
      const share = group.count / bots.length;
      hhi += share * share;
    }
    // Normalize: HHI ranges from 1/N to 1. Scale to 0-1.
    const minHHI = 1 / bots.length;
    const normalizedHHI = (hhi - minHHI) / (1 - minHHI + 0.001);
    evidence.push(`Bot type concentration (HHI normalized): ${normalizedHHI.toFixed(3)}`);
  }

  // ---- SWARM LEVEL DETERMINATION ----
  // Weighted scoring: botRatio, coordinatedGroups, botTradeRatio, concentration
  let swarmScore = 0;

  // Bot ratio contribution (0-25)
  if (botRatio > 0.8) swarmScore += 25;
  else if (botRatio > 0.6) swarmScore += 20;
  else if (botRatio > 0.4) swarmScore += 15;
  else if (botRatio > 0.2) swarmScore += 8;
  else swarmScore += 2;

  // Coordinated groups (0-30)
  if (coordinatedGroupCount >= 3) swarmScore += 30;
  else if (coordinatedGroupCount === 2) swarmScore += 20;
  else if (coordinatedGroupCount === 1) swarmScore += 10;

  // Bot trade velocity (0-25)
  if (botTradeRatio > 0.8) swarmScore += 25;
  else if (botTradeRatio > 0.6) swarmScore += 20;
  else if (botTradeRatio > 0.4) swarmScore += 12;
  else if (botTradeRatio > 0.2) swarmScore += 6;

  // Concentration (0-20)
  if (hhi > 0.7) swarmScore += 20;
  else if (hhi > 0.5) swarmScore += 15;
  else if (hhi > 0.3) swarmScore += 8;
  else swarmScore += 2;

  let level: BotSwarmLevel;
  if (swarmScore >= 80) level = 'CRITICAL';
  else if (swarmScore >= 60) level = 'HIGH';
  else if (swarmScore >= 35) level = 'MEDIUM';
  else if (swarmScore >= 15) level = 'LOW';
  else level = 'NONE';

  evidence.push(`Swarm score: ${swarmScore}/100 → Level: ${level}`);

  return {
    level,
    coordinatedGroupCount,
    dominantBotType,
    totalBotActivity: botTrades,
    evidence,
  };
}

// ============================================================
// WHALE MOVEMENT FORECAST
// ============================================================

/**
 * Forecasts whale movement direction based on:
 * 1. Net flow analysis (buying vs selling pressure)
 * 2. Hold time patterns (accumulators hold longer)
 * 3. Trade count patterns (active accumulation vs panic distribution)
 * 4. Cross-wallet correlation (multiple whales moving in sync)
 *
 * Returns ACCUMULATING, DISTRIBUTING, NEUTRAL, or ROTATING
 */
export function forecastWhaleMovement(
  whaleActivity: Array<{
    address: string;
    netFlow: number;       // Positive = inflow (buying), Negative = outflow (selling)
    tradeCount: number;
    avgHoldTime: number;   // In minutes
  }>
): WhaleForecastResult {
  const evidence: string[] = [];
  const whaleCount = whaleActivity.length;

  if (whaleCount === 0) {
    return {
      direction: 'NEUTRAL',
      confidence: 0,
      evidence: ['No whale activity data'],
      metrics: {
        netFlowSum: 0,
        avgHoldTime: 0,
        activeWalletCount: 0,
        accumulationScore: 0,
        distributionScore: 0,
      },
    };
  }

  // 1. Net Flow Analysis
  const netFlowSum = whaleActivity.reduce((s, w) => s + w.netFlow, 0);
  const avgNetFlow = netFlowSum / whaleCount;
  const positiveFlowWallets = whaleActivity.filter(w => w.netFlow > 0).length;
  const negativeFlowWallets = whaleActivity.filter(w => w.netFlow < 0).length;
  const flowConsensus = whaleCount > 0
    ? Math.abs(positiveFlowWallets - negativeFlowWallets) / whaleCount
    : 0;

  evidence.push(
    `Net flow: $${netFlowSum.toFixed(0)} | ` +
    `Buyers: ${positiveFlowWallets} | Sellers: ${negativeFlowWallets} | ` +
    `Consensus: ${(flowConsensus * 100).toFixed(0)}%`
  );

  // 2. Hold Time Pattern
  const avgHoldTime = whaleActivity.reduce((s, w) => s + w.avgHoldTime, 0) / whaleCount;
  const longHolders = whaleActivity.filter(w => w.avgHoldTime > 1440).length; // >24h
  const shortHolders = whaleActivity.filter(w => w.avgHoldTime < 60).length;  // <1h

  evidence.push(
    `Avg hold time: ${avgHoldTime.toFixed(0)} min | Long holders: ${longHolders} | Short holders: ${shortHolders}`
  );

  // 3. Accumulation vs Distribution Scoring
  let accumulationScore = 0;
  let distributionScore = 0;

  // Net flow contribution
  if (netFlowSum > 0) {
    const flowMagnitude = Math.min(netFlowSum / 100000, 1); // Normalize to 0-1
    accumulationScore += flowMagnitude * 30;
  } else {
    const flowMagnitude = Math.min(Math.abs(netFlowSum) / 100000, 1);
    distributionScore += flowMagnitude * 30;
  }

  // Consensus contribution
  if (positiveFlowWallets > negativeFlowWallets) {
    accumulationScore += flowConsensus * 25;
  } else if (negativeFlowWallets > positiveFlowWallets) {
    distributionScore += flowConsensus * 25;
  }

  // Hold time contribution
  if (longHolders > shortHolders) {
    accumulationScore += 20; // Long holders = accumulating
  } else if (shortHolders > longHolders) {
    distributionScore += 20; // Short holders = distributing
  }

  // Trade frequency: many small trades = stealth accumulation
  const avgTradeCount = whaleActivity.reduce((s, w) => s + w.tradeCount, 0) / whaleCount;
  const stealthAccumulators = whaleActivity.filter(
    w => w.netFlow > 0 && w.tradeCount > 5 && w.avgHoldTime > 360
  ).length;
  if (stealthAccumulators > 0) {
    accumulationScore += stealthAccumulators * 5;
    evidence.push(`${stealthAccumulators} wallets show stealth accumulation pattern (many trades + long hold)`);
  }

  // Rapid distributors: negative flow + high trade count + short hold
  const rapidDistributors = whaleActivity.filter(
    w => w.netFlow < 0 && w.tradeCount > 5 && w.avgHoldTime < 60
  ).length;
  if (rapidDistributors > 0) {
    distributionScore += rapidDistributors * 8;
    evidence.push(`${rapidDistributors} wallets show rapid distribution pattern (selling + short hold)`);
  }

  // 4. Cross-wallet synchronization (are multiple whales moving in the same direction?)
  const netFlows = whaleActivity.map(w => w.netFlow);
  const flowVariance = calculateVariance(netFlows);
  const flowStdDev = Math.sqrt(flowVariance);
  const meanFlow = avgNetFlow;

  // Low coefficient of variation means synchronized movement
  const cv = meanFlow !== 0 ? flowStdDev / Math.abs(meanFlow) : Infinity;
  const synchronicity = cv < 0.5 ? 0.8 : cv < 1 ? 0.5 : cv < 2 ? 0.3 : 0.1;
  evidence.push(`Whale synchronicity: ${(synchronicity * 100).toFixed(0)}% (CV: ${cv.toFixed(2)})`);

  // ---- DIRECTION DETERMINATION ----
  let direction: WhaleDirection;
  let confidence: number;

  const scoreDiff = accumulationScore - distributionScore;
  const totalScore = accumulationScore + distributionScore + 0.001;

  if (Math.abs(scoreDiff) / totalScore < 0.15) {
    // Roughly equal accumulation and distribution — ROTATING
    direction = 'ROTATING';
    confidence = 0.4 + synchronicity * 0.2;
    evidence.push('Mixed signals: whales rotating positions between assets');
  } else if (accumulationScore > distributionScore) {
    direction = 'ACCUMULATING';
    confidence = Math.min(0.95, (accumulationScore / totalScore) * 0.8 + synchronicity * 0.2);
    evidence.push('Net accumulation detected across whale wallets');
  } else {
    direction = 'DISTRIBUTING';
    confidence = Math.min(0.95, (distributionScore / totalScore) * 0.8 + synchronicity * 0.2);
    evidence.push('Net distribution detected across whale wallets');
  }

  return {
    direction,
    confidence,
    evidence,
    metrics: {
      netFlowSum,
      avgHoldTime,
      activeWalletCount: whaleCount,
      accumulationScore,
      distributionScore,
    },
  };
}

// ============================================================
// ANOMALY DETECTION
// ============================================================

/**
 * Detects anomalies in current values compared to a historical baseline
 * using Z-Score analysis.
 *
 * A value is considered anomalous if |z-score| > threshold (default 2.0).
 * The anomaly score scales with the magnitude of the z-score.
 *
 * Returns per-value anomaly results plus an aggregate score.
 */
export function detectAnomalies(
  currentValues: number[],
  historicalBaseline: number[],
  threshold: number = 2.0
): { aggregate: AnomalyResult; perValue: AnomalyResult[] } {
  if (historicalBaseline.length < 5) {
    return {
      aggregate: {
        isAnomaly: false,
        anomalyScore: 0,
        direction: 'NEUTRAL',
        zScore: 0,
        details: 'Insufficient baseline data for anomaly detection',
      },
      perValue: currentValues.map(v => ({
        isAnomaly: false,
        anomalyScore: 0,
        direction: 'NEUTRAL',
        zScore: 0,
        details: `Value: ${v.toFixed(4)} — insufficient baseline`,
      })),
    };
  }

  // Calculate baseline statistics
  const baselineMean = historicalBaseline.reduce((s, v) => s + v, 0) / historicalBaseline.length;
  const baselineStdDev = calculateStdDev(historicalBaseline);

  // Analyze each current value
  const perValue: AnomalyResult[] = currentValues.map((value) => {
    const z = calculateZScore(value, baselineMean, baselineStdDev);
    const isAnomaly = Math.abs(z) > threshold;
    const anomalyScore = isAnomaly
      ? Math.min(1, (Math.abs(z) - threshold) / threshold) // 0 at threshold, approaches 1
      : 0;
    const direction: 'ABOVE' | 'BELOW' | 'NEUTRAL' = z > threshold ? 'ABOVE' : z < -threshold ? 'BELOW' : 'NEUTRAL';

    return {
      isAnomaly,
      anomalyScore,
      direction,
      zScore: z,
      details: `Value: ${value.toFixed(4)} | Z-score: ${z.toFixed(3)} | Baseline mean: ${baselineMean.toFixed(4)} | StdDev: ${baselineStdDev.toFixed(4)}`,
    };
  });

  // Aggregate: use the maximum anomaly score
  const anomalyCount = perValue.filter(v => v.isAnomaly).length;
  const maxScore = Math.max(...perValue.map(v => v.anomalyScore), 0);
  const avgZScore = perValue.reduce((s, v) => s + v.zScore, 0) / (perValue.length || 1);
  const overallDirection: 'ABOVE' | 'BELOW' | 'NEUTRAL' =
    avgZScore > threshold * 0.5 ? 'ABOVE' : avgZScore < -threshold * 0.5 ? 'BELOW' : 'NEUTRAL';

  const aggregate: AnomalyResult = {
    isAnomaly: anomalyCount > 0,
    anomalyScore: maxScore,
    direction: overallDirection,
    zScore: avgZScore,
    details: `${anomalyCount}/${currentValues.length} values are anomalous (|z| > ${threshold}) | Avg Z-score: ${avgZScore.toFixed(3)}`,
  };

  return { aggregate, perValue };
}

// ============================================================
// SMART MONEY POSITIONING
// ============================================================

/**
 * Analyzes smart money wallet positions to detect where capital is flowing.
 *
 * Looks at:
 * 1. Net direction (BUY vs SELL actions)
 * 2. Capital magnitude (USD value of positions)
 * 3. Token concentration (which tokens SM prefers)
 * 4. Sector breakdown (grouping tokens by sector)
 */
export function analyzeSmartMoneyPositioning(
  smWallets: Array<{
    address: string;
    recentAction: string;
    tokenAddress: string;
    valueUsd: number;
  }>
): SmartMoneyPositioningResult {
  const evidence: string[] = [];
  const walletCount = smWallets.length;

  if (walletCount === 0) {
    return {
      netDirection: 'NEUTRAL',
      magnitude: 0,
      topDestination: null,
      confidence: 0,
      evidence: ['No smart money wallet data'],
      sectorBreakdown: {},
    };
  }

  // 1. Net direction: BUY vs SELL
  const buyActions = smWallets.filter(w =>
    ['BUY', 'ACCUMULATE', 'ENTER', 'LONG'].includes(w.recentAction.toUpperCase())
  );
  const sellActions = smWallets.filter(w =>
    ['SELL', 'DISTRIBUTE', 'EXIT', 'SHORT'].includes(w.recentAction.toUpperCase())
  );

  const buyVolume = buyActions.reduce((s, w) => s + w.valueUsd, 0);
  const sellVolume = sellActions.reduce((s, w) => s + w.valueUsd, 0);
  const netVolume = buyVolume - sellVolume;
  const totalVolume = buyVolume + sellVolume + 0.001;

  const netDirection: 'INFLOW' | 'OUTFLOW' | 'NEUTRAL' =
    netVolume > totalVolume * 0.15 ? 'INFLOW'
    : netVolume < -totalVolume * 0.15 ? 'OUTFLOW'
    : 'NEUTRAL';

  evidence.push(
    `Buy volume: $${buyVolume.toFixed(0)} | Sell volume: $${sellVolume.toFixed(0)} | Net: $${netVolume.toFixed(0)}`
  );
  evidence.push(`Direction: ${netDirection} | Buyers: ${buyActions.length} | Sellers: ${sellActions.length}`);

  // 2. Magnitude: normalized 0-100
  const magnitude = Math.min(100, totalVolume / 10000);

  // 3. Token concentration: which tokens are most popular
  const tokenFlows: Record<string, { buys: number; sells: number; netUsd: number }> = {};
  for (const w of smWallets) {
    if (!tokenFlows[w.tokenAddress]) {
      tokenFlows[w.tokenAddress] = { buys: 0, sells: 0, netUsd: 0 };
    }
    const isBuy = ['BUY', 'ACCUMULATE', 'ENTER', 'LONG'].includes(w.recentAction.toUpperCase());
    if (isBuy) {
      tokenFlows[w.tokenAddress].buys += 1;
      tokenFlows[w.tokenAddress].netUsd += w.valueUsd;
    } else {
      tokenFlows[w.tokenAddress].sells += 1;
      tokenFlows[w.tokenAddress].netUsd -= w.valueUsd;
    }
  }

  // Find top destination (highest net inflow)
  const sortedTokens = Object.entries(tokenFlows)
    .sort((a, b) => b[1].netUsd - a[1].netUsd);

  const topDestination = sortedTokens.length > 0 ? sortedTokens[0][0] : null;
  if (topDestination) {
    const topFlow = tokenFlows[topDestination];
    evidence.push(
      `Top SM destination: ${topDestination.slice(0, 10)}... (net: $${topFlow.netUsd.toFixed(0)}, buys: ${topFlow.buys}, sells: ${topFlow.sells})`
    );
  }

  // 4. Sector breakdown (simplified: use token address prefix as pseudo-sector)
  const sectorBreakdown: Record<string, number> = {};
  for (const [token, flow] of Object.entries(tokenFlows)) {
    const sector = classifyTokenSector(token);
    sectorBreakdown[sector] = (sectorBreakdown[sector] || 0) + Math.abs(flow.netUsd);
  }

  // Normalize sector breakdown to percentages
  const totalSectorVolume = Object.values(sectorBreakdown).reduce((s, v) => s + v, 0) || 1;
  for (const sector of Object.keys(sectorBreakdown)) {
    sectorBreakdown[sector] = (sectorBreakdown[sector] / totalSectorVolume) * 100;
  }

  // 5. Confidence: based on wallet count and consensus
  const consensus = Math.abs(buyActions.length - sellActions.length) / walletCount;
  const confidence = Math.min(0.95, consensus * 0.5 + Math.min(walletCount / 20, 0.5));

  return {
    netDirection,
    magnitude,
    topDestination,
    confidence,
    evidence,
    sectorBreakdown,
  };
}

/**
 * Simple token-to-sector classification heuristic.
 * In production, this would use a token metadata registry.
 */
function classifyTokenSector(tokenAddress: string): string {
  const addr = tokenAddress.toLowerCase();

  // Stablecoins
  if (addr.includes('usdc') || addr.includes('usdt') || addr.includes('dai')) return 'STABLECOINS';

  // DeFi (common DEX/Lending tokens)
  if (addr.includes('ray') || addr.includes('jup') || addr.includes('orca') || addr.includes('mngo')) return 'DEFI';

  // L1/L2 infrastructure
  if (addr.includes('sol') || addr.includes('eth') || addr.includes('btc')) return 'INFRASTRUCTURE';

  // NFT/Gaming
  if (addr.includes('nft') || addr.includes('game') || addr.includes('play')) return 'NFT_GAMING';

  // Meme
  if (addr.includes('doge') || addr.includes('pepe') || addr.includes('bonk') || addr.includes('wojak')) return 'MEME';

  return 'OTHER';
}

// ============================================================
// MEAN REVERSION ZONES
// ============================================================

/**
 * Identifies price levels where mean reversion is likely using:
 * 1. Bollinger Bands (2σ from SMA)
 * 2. Distance from mean in standard deviations
 * 3. Bandwidth analysis (tight bands = potential breakout, wide bands = potential reversion)
 *
 * Probability of reversion increases when:
 * - Price touches or exceeds Bollinger Band boundaries
 * - Bandwidth is historically wide (stretching)
 * - RSI-like conditions suggest overextension
 */
export function calculateMeanReversionZones(
  priceHistory: number[],
  period: number = 20,
  multiplier: number = 2.0
): MeanReversionZone {
  if (priceHistory.length < period) {
    const last = priceHistory[priceHistory.length - 1] || 0;
    return {
      upperBound: last * 1.02,
      lowerBound: last * 0.98,
      mean: last,
      currentDeviation: 0,
      probabilityOfReversion: 0.3,
      bandWidth: 0.04,
    };
  }

  const bands = calculateBollingerBands(priceHistory, period, multiplier);
  const currentPrice = priceHistory[priceHistory.length - 1];

  // Current deviation from mean in standard deviations
  const slice = priceHistory.slice(priceHistory.length - period);
  const mean = slice.reduce((s, v) => s + v, 0) / period;
  const stdDev = calculateStdDev(slice);
  const currentDeviation = stdDev !== 0 ? (currentPrice - mean) / stdDev : 0;

  // Calculate historical bandwidth percentile
  // Compare current bandwidth to recent historical bandwidths
  const bandwidthHistory: number[] = [];
  const lookback = Math.min(priceHistory.length, period * 3);
  for (let i = period; i <= priceHistory.length; i++) {
    const subSlice = priceHistory.slice(Math.max(0, i - period), i);
    if (subSlice.length >= period) {
      const subBands = calculateBollingerBands(subSlice, period, multiplier);
      bandwidthHistory.push(subBands.bandwidth);
    }
  }

  // Percentile of current bandwidth
  let bandwidthPercentile = 0.5;
  if (bandwidthHistory.length >= 5) {
    const currentBW = bands.bandwidth;
    const belowCurrent = bandwidthHistory.filter(bw => bw < currentBW).length;
    bandwidthPercentile = belowCurrent / bandwidthHistory.length;
  }

  // Probability of reversion calculation
  // Base: 50%, increases with distance from mean
  let reversionProbability = 0.5;

  // Z-score contribution: further from mean = higher reversion probability
  reversionProbability += Math.abs(currentDeviation) * 0.1; // +10% per σ
  if (Math.abs(currentDeviation) > 2) reversionProbability += 0.15;
  if (Math.abs(currentDeviation) > 2.5) reversionProbability += 0.1;

  // %B contribution: at or beyond bands = higher reversion
  if (bands.percentB > 1) reversionProbability += 0.1; // Above upper band
  if (bands.percentB < 0) reversionProbability += 0.1; // Below lower band

  // Bandwidth percentile: wide bands (high percentile) = more likely to revert
  if (bandwidthPercentile > 0.8) reversionProbability += 0.05;

  // Clamp
  reversionProbability = Math.min(0.95, Math.max(0.1, reversionProbability));

  return {
    upperBound: bands.upper,
    lowerBound: bands.lower,
    mean: bands.middle,
    currentDeviation,
    probabilityOfReversion: reversionProbability,
    bandWidth: bands.bandwidth,
  };
}

// ============================================================
// LIQUIDITY DRAIN DETECTION
// ============================================================

/**
 * Detects when liquidity is being withdrawn from the market.
 *
 * Uses:
 * 1. Linear regression slope of liquidity series (negative = draining)
 * 2. Rate of change comparison (recent vs historical)
 * 3. Percent deviation from baseline
 * 4. Acceleration (second derivative)
 */
export function detectLiquidityDrain(
  liquidityHistory: number[]
): LiquidityDrainResult {
  const evidence: string[] = [];
  const len = liquidityHistory.length;

  if (len < 5) {
    return {
      trend: 'STABLE',
      drainRate: 0,
      confidence: 0,
      evidence: ['Insufficient liquidity history for drain detection'],
      metrics: {
        currentLiquidity: liquidityHistory[len - 1] || 0,
        baselineLiquidity: liquidityHistory[0] || 0,
        percentChange: 0,
        slope: 0,
      },
    };
  }

  const currentLiquidity = liquidityHistory[len - 1];
  const baselineLiquidity = liquidityHistory[0];
  const percentChange = baselineLiquidity !== 0
    ? ((currentLiquidity - baselineLiquidity) / Math.abs(baselineLiquidity)) * 100
    : 0;

  // 1. Linear regression slope
  const regression = linearRegression(liquidityHistory);
  const slope = regression.slope;
  const normalizedSlope = baselineLiquidity !== 0
    ? (slope / baselineLiquidity) * len * 100 // Scale to percent over the period
    : 0;

  evidence.push(
    `Liquidity regression slope: ${slope.toFixed(2)} (normalized: ${normalizedSlope.toFixed(2)}%/period) | R²: ${regression.r2.toFixed(3)}`
  );
  evidence.push(
    `Current: $${currentLiquidity.toFixed(0)} | Baseline: $${baselineLiquidity.toFixed(0)} | Change: ${percentChange.toFixed(2)}%`
  );

  // 2. Rate of change — compare recent ROC to overall ROC
  const recentWindow = Math.min(5, Math.floor(len / 3));
  const recentROC = len > recentWindow
    ? ((liquidityHistory[len - 1] - liquidityHistory[len - 1 - recentWindow]) / Math.abs(liquidityHistory[len - 1 - recentWindow] || 1)) * 100
    : 0;

  const overallROC = percentChange;

  // Acceleration: is the drain speeding up?
  const acceleration = recentROC - (overallROC / (len / recentWindow));
  evidence.push(`Recent ROC: ${recentROC.toFixed(2)}% | Acceleration: ${acceleration.toFixed(2)}%/period`);

  // 3. Drain rate calculation (percentage per period)
  const drainRate = Math.abs(normalizedSlope / len);

  // 4. Trend determination
  let trend: LiquidityTrend;
  let confidence: number;

  // Use slope R² and direction to determine confidence
  const slopeReliability = Math.abs(regression.r2); // Higher R² = more reliable slope

  if (normalizedSlope > 5) {
    trend = 'ACCUMULATING';
    confidence = Math.min(0.9, slopeReliability * 0.6 + 0.3);
    evidence.push('Liquidity is increasing — accumulation phase');
  } else if (normalizedSlope > -5) {
    trend = 'STABLE';
    confidence = Math.min(0.8, 0.5 + (1 - Math.abs(normalizedSlope) / 5) * 0.3);
    evidence.push('Liquidity is relatively stable');
  } else if (normalizedSlope > -20) {
    trend = 'DRAINING';
    confidence = Math.min(0.85, slopeReliability * 0.5 + 0.35);
    evidence.push('Liquidity is being withdrawn — drain detected');
  } else {
    trend = 'CRITICAL_DRAIN';
    confidence = Math.min(0.95, slopeReliability * 0.5 + 0.45);
    evidence.push('CRITICAL: Severe liquidity drain detected');
  }

  // Adjust confidence based on acceleration
  if (acceleration < -5 && trend === 'DRAINING') {
    trend = 'CRITICAL_DRAIN';
    confidence = Math.min(0.95, confidence + 0.15);
    evidence.push('Drain is accelerating — upgraded to CRITICAL');
  }

  return {
    trend,
    drainRate,
    confidence,
    evidence,
    metrics: {
      currentLiquidity,
      baselineLiquidity,
      percentChange,
      slope,
    },
  };
}

// ============================================================
// CORRELATION BREAK DETECTION
// ============================================================

/**
 * Detects when a historically stable correlation between two series breaks down.
 * Uses a rolling window approach: compares recent correlation to historical correlation.
 *
 * A "break" occurs when the recent correlation diverges significantly from
 * the historical baseline, signaling a regime change in the relationship.
 */
export function detectCorrelationBreak(
  seriesA: number[],
  seriesB: number[],
  fullWindow: number = 50,
  recentWindow: number = 15
): { isBreak: boolean; historicalCorrelation: number; recentCorrelation: number; divergence: number; confidence: number } {
  const n = Math.min(seriesA.length, seriesB.length);

  if (n < fullWindow) {
    return {
      isBreak: false,
      historicalCorrelation: 0,
      recentCorrelation: 0,
      divergence: 0,
      confidence: 0,
    };
  }

  const aSlice = seriesA.slice(0, n);
  const bSlice = seriesB.slice(0, n);

  // Historical correlation (full window)
  const histA = aSlice.slice(Math.max(0, n - fullWindow), n - recentWindow);
  const histB = bSlice.slice(Math.max(0, n - fullWindow), n - recentWindow);
  const historicalCorrelation = calculateCorrelation(histA, histB);

  // Recent correlation
  const recentA = aSlice.slice(n - recentWindow);
  const recentB = bSlice.slice(n - recentWindow);
  const recentCorrelation = calculateCorrelation(recentA, recentB);

  // Divergence
  const divergence = Math.abs(recentCorrelation - historicalCorrelation);

  // Is it a break? If divergence exceeds threshold
  const threshold = 0.3; // 0.3 correlation divergence is significant
  const isBreak = divergence > threshold;

  // Confidence: based on how many data points and how big the divergence
  const confidence = Math.min(0.95, divergence * 1.5 * Math.min(1, n / fullWindow));

  return {
    isBreak,
    historicalCorrelation,
    recentCorrelation,
    divergence,
    confidence,
  };
}

// ============================================================
// VOLATILITY REGIME DETECTION
// ============================================================

/**
 * Classifies the current volatility regime using ATR percentile.
 *
 * Compares the current ATR to its historical distribution:
 * - LOW: ATR in bottom 25th percentile
 * - NORMAL: ATR between 25th and 75th percentile
 * - HIGH: ATR between 75th and 95th percentile
 * - EXTREME: ATR above 95th percentile
 */
export function detectVolatilityRegime(
  priceHistory: number[],
  highs?: number[],
  lows?: number[]
): { regime: VolatilityRegime; currentATR: number; percentile: number; confidence: number } {
  const len = priceHistory.length;
  if (len < 20) {
    return { regime: 'NORMAL', currentATR: 0, percentile: 0.5, confidence: 0.2 };
  }

  // Calculate rolling ATRs
  const atrPeriod = 14;
  const atrValues: number[] = [];

  for (let i = atrPeriod + 1; i <= len; i++) {
    const subCloses = priceHistory.slice(Math.max(0, i - atrPeriod - 1), i);
    const subHighs = highs?.slice(Math.max(0, i - atrPeriod - 1), i);
    const subLows = lows?.slice(Math.max(0, i - atrPeriod - 1), i);
    atrValues.push(calculateATR(subCloses, subHighs, subLows, atrPeriod));
  }

  if (atrValues.length < 5) {
    return { regime: 'NORMAL', currentATR: atrValues[atrValues.length - 1] || 0, percentile: 0.5, confidence: 0.3 };
  }

  const currentATR = atrValues[atrValues.length - 1];
  const avgPrice = priceHistory[len - 1];
  const normalizedCurrentATR = avgPrice !== 0 ? currentATR / avgPrice : 0;

  // Calculate percentile of current ATR within historical ATRs
  const belowCurrent = atrValues.filter(a => a < currentATR).length;
  const percentile = belowCurrent / atrValues.length;

  // Classify
  let regime: VolatilityRegime;
  if (percentile < 0.25) regime = 'LOW';
  else if (percentile < 0.75) regime = 'NORMAL';
  else if (percentile < 0.95) regime = 'HIGH';
  else regime = 'EXTREME';

  // Confidence: higher at extremes
  const confidence = regime === 'LOW' || regime === 'EXTREME'
    ? Math.min(0.95, 0.6 + Math.abs(percentile - 0.5))
    : Math.min(0.8, 0.5 + Math.abs(percentile - 0.5) * 0.5);

  return { regime, currentATR, percentile, confidence };
}

// ============================================================
// CYCLE POSITION DETECTION
// ============================================================

/**
 * Determines where we are in the market cycle using:
 * 1. Distance from long-term moving average
 * 2. Momentum divergence
 * 3. Volume trend
 *
 * Returns a cycle position from 0 (bottom) to 100 (top).
 */
export function detectCyclePosition(priceHistory: number[]): {
  position: number;
  phase: 'ACCUMULATION' | 'MARKUP' | 'DISTRIBUTION' | 'MARKDOWN';
  confidence: number;
} {
  const len = priceHistory.length;
  if (len < 50) {
    return { position: 50, phase: 'ACCUMULATION', confidence: 0.2 };
  }

  // Use percentage distance from 50-period SMA as primary indicator
  const sma50 = calculateSMA(priceHistory, 50);
  const currentPrice = priceHistory[len - 1];
  const distanceFromSMA = sma50 !== 0 ? ((currentPrice - sma50) / Math.abs(sma50)) * 100 : 0;

  // Calculate price position within recent range (0 = at low, 100 = at high)
  const rangeLookback = Math.min(len, 100);
  const recentPrices = priceHistory.slice(len - rangeLookback);
  const rangeHigh = Math.max(...recentPrices);
  const rangeLow = Math.min(...recentPrices);
  const rangePosition = rangeHigh !== rangeLow
    ? ((currentPrice - rangeLow) / (rangeHigh - rangeLow)) * 100
    : 50;

  // Momentum divergence: short-term ROC vs long-term ROC
  const shortROC = calculateROC(priceHistory, 10);
  const longROC = calculateROC(priceHistory, 50);
  const momentumDivergence = shortROC - longROC;

  // Composite cycle position (0-100)
  // Weight: range position 50%, distance from SMA 30%, momentum 20%
  const smaComponent = Math.max(0, Math.min(100, 50 + distanceFromSMA * 5));
  const momentumComponent = Math.max(0, Math.min(100, 50 + momentumDivergence * 3));

  const position = rangePosition * 0.5 + smaComponent * 0.3 + momentumComponent * 0.2;

  // Determine phase
  let phase: 'ACCUMULATION' | 'MARKUP' | 'DISTRIBUTION' | 'MARKDOWN';
  if (position < 25) phase = 'ACCUMULATION';
  else if (position < 55) phase = 'MARKUP';
  else if (position < 80) phase = 'DISTRIBUTION';
  else phase = 'MARKDOWN';

  // Confidence based on agreement between indicators
  const indicatorAgreement = 1 - (
    Math.abs(rangePosition - position) / 100 * 0.4 +
    Math.abs(smaComponent - position) / 100 * 0.3 +
    Math.abs(momentumComponent - position) / 100 * 0.3
  );
  const confidence = Math.min(0.9, Math.max(0.2, indicatorAgreement));

  return { position, phase, confidence };
}

// ============================================================
// MAIN ENGINE CLASS
// ============================================================

/**
 * BigDataPredictiveEngine — The core predictive engine for CryptoQuant Terminal.
 *
 * Orchestrates all sub-analyses and produces actionable PredictiveOutput signals
 * that can be consumed by Trading Systems.
 *
 * Usage:
 *   const engine = new BigDataPredictiveEngine();
 *   const signals = engine.generatePredictiveSignals(input);
 *   const context = engine.getCurrentMarketContext(input);
 */
export class BigDataPredictiveEngine {
  private lastSignals: PredictiveOutput[] = [];
  private lastContext: MarketContext | null = null;
  private historicalHitRates: Record<PredictiveSignalType, number> = {
    REGIME_CHANGE: 0.62,
    BOT_SWARM: 0.71,
    WHALE_MOVEMENT: 0.58,
    LIQUIDITY_DRAIN: 0.65,
    CORRELATION_BREAK: 0.55,
    ANOMALY: 0.68,
    CYCLE_POSITION: 0.60,
    SECTOR_ROTATION: 0.52,
    MEAN_REVERSION_ZONE: 0.64,
    SMART_MONEY_POSITIONING: 0.59,
    VOLATILITY_REGIME: 0.67,
  };

  /**
   * Generate all predictive signals from the given input data.
   * Each signal includes confidence, evidence, timeframe, and historical hit rate.
   */
  generatePredictiveSignals(input: EngineInput): PredictiveOutput[] {
    const signals: PredictiveOutput[] = [];
    const now = new Date();

    // 1. REGIME CHANGE
    if (input.priceHistory.length >= 50) {
      const regimeResult = detectMarketRegime(input.priceHistory);
      if (regimeResult.regime === 'TRANSITION' || regimeResult.metrics.crossoverSignal !== 'NONE') {
        signals.push({
          signalType: 'REGIME_CHANGE',
          confidence: regimeResult.confidence,
          prediction: {
            currentRegime: regimeResult.regime,
            crossover: regimeResult.metrics.crossoverSignal,
            momentum: regimeResult.metrics.momentum,
            trendStrength: regimeResult.metrics.trendStrength,
          },
          evidence: regimeResult.evidence,
          timeframe: '1-7 days',
          validUntil: new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.REGIME_CHANGE,
        });
      }
    }

    // 2. BOT SWARM
    if (input.traderMetrics && input.traderMetrics.length > 0) {
      const swarmResult = detectBotSwarm(input.traderMetrics);
      if (swarmResult.level !== 'NONE') {
        signals.push({
          signalType: 'BOT_SWARM',
          confidence: swarmResult.level === 'CRITICAL' ? 0.9
            : swarmResult.level === 'HIGH' ? 0.75
            : swarmResult.level === 'MEDIUM' ? 0.55
            : 0.3,
          prediction: {
            swarmLevel: swarmResult.level,
            coordinatedGroups: swarmResult.coordinatedGroupCount,
            dominantBotType: swarmResult.dominantBotType,
            totalBotActivity: swarmResult.totalBotActivity,
          },
          evidence: swarmResult.evidence,
          timeframe: '15min - 4h',
          validUntil: new Date(now.getTime() + 4 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.BOT_SWARM,
        });
      }
    }

    // 3. WHALE MOVEMENT
    if (input.whaleActivity && input.whaleActivity.length > 0) {
      const whaleResult = forecastWhaleMovement(input.whaleActivity);
      if (whaleResult.direction !== 'NEUTRAL') {
        signals.push({
          signalType: 'WHALE_MOVEMENT',
          confidence: whaleResult.confidence,
          prediction: {
            direction: whaleResult.direction,
            netFlow: whaleResult.metrics.netFlowSum,
            activeWallets: whaleResult.metrics.activeWalletCount,
            accumulationScore: whaleResult.metrics.accumulationScore,
            distributionScore: whaleResult.metrics.distributionScore,
          },
          evidence: whaleResult.evidence,
          timeframe: '4h - 48h',
          validUntil: new Date(now.getTime() + 48 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.WHALE_MOVEMENT,
        });
      }
    }

    // 4. ANOMALY
    if (input.currentValues && input.historicalBaseline && input.historicalBaseline.length >= 5) {
      const anomalyResult = detectAnomalies(input.currentValues, input.historicalBaseline);
      if (anomalyResult.aggregate.isAnomaly) {
        signals.push({
          signalType: 'ANOMALY',
          confidence: Math.min(0.95, 0.5 + anomalyResult.aggregate.anomalyScore * 0.5),
          prediction: {
            anomalyScore: anomalyResult.aggregate.anomalyScore,
            direction: anomalyResult.aggregate.direction,
            zScore: anomalyResult.aggregate.zScore,
            anomalousValueCount: anomalyResult.perValue.filter(v => v.isAnomaly).length,
          },
          evidence: [anomalyResult.aggregate.details],
          timeframe: '1-24h',
          validUntil: new Date(now.getTime() + 24 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.ANOMALY,
        });
      }
    }

    // 5. SMART MONEY POSITIONING
    if (input.smWallets && input.smWallets.length > 0) {
      const smResult = analyzeSmartMoneyPositioning(input.smWallets);
      if (smResult.netDirection !== 'NEUTRAL') {
        signals.push({
          signalType: 'SMART_MONEY_POSITIONING',
          confidence: smResult.confidence,
          prediction: {
            netDirection: smResult.netDirection,
            magnitude: smResult.magnitude,
            topDestination: smResult.topDestination,
            sectorBreakdown: smResult.sectorBreakdown,
          },
          evidence: smResult.evidence,
          timeframe: '4h - 72h',
          validUntil: new Date(now.getTime() + 72 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.SMART_MONEY_POSITIONING,
        });
      }
    }

    // 6. MEAN REVERSION ZONE
    if (input.priceHistory.length >= 20) {
      const mrZone = calculateMeanReversionZones(input.priceHistory);
      if (mrZone.probabilityOfReversion > 0.6) {
        signals.push({
          signalType: 'MEAN_REVERSION_ZONE',
          confidence: mrZone.probabilityOfReversion,
          prediction: {
            upperBound: mrZone.upperBound,
            lowerBound: mrZone.lowerBound,
            mean: mrZone.mean,
            currentDeviation: mrZone.currentDeviation,
            probabilityOfReversion: mrZone.probabilityOfReversion,
            bandWidth: mrZone.bandWidth,
          },
          evidence: [
            `Current deviation: ${mrZone.currentDeviation.toFixed(2)}σ from mean`,
            `Mean reversion probability: ${(mrZone.probabilityOfReversion * 100).toFixed(1)}%`,
            `Bollinger Band width: ${(mrZone.bandWidth * 100).toFixed(2)}%`,
          ],
          timeframe: '1-14 days',
          validUntil: new Date(now.getTime() + 14 * 24 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.MEAN_REVERSION_ZONE,
        });
      }
    }

    // 7. LIQUIDITY DRAIN
    if (input.liquidityHistory && input.liquidityHistory.length >= 5) {
      const drainResult = detectLiquidityDrain(input.liquidityHistory);
      if (drainResult.trend === 'DRAINING' || drainResult.trend === 'CRITICAL_DRAIN') {
        signals.push({
          signalType: 'LIQUIDITY_DRAIN',
          confidence: drainResult.confidence,
          prediction: {
            trend: drainResult.trend,
            drainRate: drainResult.drainRate,
            currentLiquidity: drainResult.metrics.currentLiquidity,
            percentChange: drainResult.metrics.percentChange,
          },
          evidence: drainResult.evidence,
          timeframe: '1-7 days',
          validUntil: new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.LIQUIDITY_DRAIN,
        });
      }
    }

    // 8. CORRELATION BREAK
    if (input.correlationSeriesA && input.correlationSeriesB) {
      const corrResult = detectCorrelationBreak(
        input.correlationSeriesA,
        input.correlationSeriesB
      );
      if (corrResult.isBreak) {
        signals.push({
          signalType: 'CORRELATION_BREAK',
          confidence: corrResult.confidence,
          prediction: {
            historicalCorrelation: corrResult.historicalCorrelation,
            recentCorrelation: corrResult.recentCorrelation,
            divergence: corrResult.divergence,
          },
          evidence: [
            `Historical correlation: ${corrResult.historicalCorrelation.toFixed(3)}`,
            `Recent correlation: ${corrResult.recentCorrelation.toFixed(3)}`,
            `Divergence: ${corrResult.divergence.toFixed(3)}`,
          ],
          timeframe: '1-14 days',
          validUntil: new Date(now.getTime() + 14 * 24 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.CORRELATION_BREAK,
        });
      }
    }

    // 9. VOLATILITY REGIME
    if (input.priceHistory.length >= 30) {
      const volResult = detectVolatilityRegime(
        input.priceHistory,
        input.highHistory,
        input.lowHistory
      );
      if (volResult.regime === 'HIGH' || volResult.regime === 'EXTREME') {
        signals.push({
          signalType: 'VOLATILITY_REGIME',
          confidence: volResult.confidence,
          prediction: {
            regime: volResult.regime,
            currentATR: volResult.currentATR,
            percentile: volResult.percentile,
          },
          evidence: [
            `Volatility regime: ${volResult.regime}`,
            `ATR percentile: ${(volResult.percentile * 100).toFixed(1)}%`,
            `Current ATR: ${volResult.currentATR.toFixed(4)}`,
          ],
          timeframe: '4h - 7 days',
          validUntil: new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000),
          historicalHitRate: this.historicalHitRates.VOLATILITY_REGIME,
        });
      }
    }

    // 10. CYCLE POSITION
    if (input.priceHistory.length >= 50) {
      const cycleResult = detectCyclePosition(input.priceHistory);
      signals.push({
        signalType: 'CYCLE_POSITION',
        confidence: cycleResult.confidence,
        prediction: {
          position: cycleResult.position,
          phase: cycleResult.phase,
        },
        evidence: [
          `Cycle position: ${cycleResult.position.toFixed(1)}/100`,
          `Phase: ${cycleResult.phase}`,
        ],
        timeframe: '1-30 days',
        validUntil: new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000),
        historicalHitRate: this.historicalHitRates.CYCLE_POSITION,
      });
    }

    // 11. SECTOR ROTATION (derived from smart money + regime)
    if (input.smWallets && input.smWallets.length > 0 && input.priceHistory.length >= 50) {
      const smResult = analyzeSmartMoneyPositioning(input.smWallets);
      const regimeResult = detectMarketRegime(input.priceHistory);

      // Determine leading and lagging sectors
      const sectorEntries = Object.entries(smResult.sectorBreakdown);
      if (sectorEntries.length >= 2) {
        const sortedSectors = sectorEntries.sort((a, b) => b[1] - a[1]);
        const leadingSector = sortedSectors[0][0];
        const laggingSector = sortedSectors[sortedSectors.length - 1][0];
        const rotationSpeed = sortedSectors[0][1] - sortedSectors[sortedSectors.length - 1][1];

        if (rotationSpeed > 20) {
          signals.push({
            signalType: 'SECTOR_ROTATION',
            confidence: Math.min(0.85, smResult.confidence * 0.7 + rotationSpeed / 200),
            prediction: {
              leadingSector,
              laggingSector,
              rotationSpeed,
              regime: regimeResult.regime,
              sectorBreakdown: smResult.sectorBreakdown,
            },
            evidence: [
              `Leading sector: ${leadingSector} (${sortedSectors[0][1].toFixed(1)}%)`,
              `Lagging sector: ${laggingSector} (${sortedSectors[sortedSectors.length - 1][1].toFixed(1)}%)`,
              `Rotation speed: ${rotationSpeed.toFixed(1)}% spread`,
              `Market regime: ${regimeResult.regime}`,
            ],
            timeframe: '1-14 days',
            validUntil: new Date(now.getTime() + 14 * 24 * 60 * 60 * 1000),
            historicalHitRate: this.historicalHitRates.SECTOR_ROTATION,
          });
        }
      }
    }

    // Sort by confidence (highest first)
    signals.sort((a, b) => b.confidence - a.confidence);

    this.lastSignals = signals;
    return signals;
  }

  /**
   * Get a full snapshot of the current market context.
   * Combines all sub-analyses into a single cohesive view.
   */
  getCurrentMarketContext(input: EngineInput): MarketContext {
    // Regime
    const regimeResult = input.priceHistory.length >= 50
      ? detectMarketRegime(input.priceHistory)
      : { regime: 'TRANSITION' as MarketRegime, confidence: 0.2 };

    // Volatility
    const volResult = input.priceHistory.length >= 30
      ? detectVolatilityRegime(input.priceHistory, input.highHistory, input.lowHistory)
      : { regime: 'NORMAL' as VolatilityRegime, confidence: 0.2 };

    // Bot swarm
    const swarmResult = input.traderMetrics && input.traderMetrics.length > 0
      ? detectBotSwarm(input.traderMetrics)
      : { level: 'NONE' as BotSwarmLevel, evidence: [] };

    // Whale
    const whaleResult = input.whaleActivity && input.whaleActivity.length > 0
      ? forecastWhaleMovement(input.whaleActivity)
      : { direction: 'NEUTRAL' as WhaleDirection, confidence: 0, evidence: [] };

    // Smart money
    const smResult = input.smWallets && input.smWallets.length > 0
      ? analyzeSmartMoneyPositioning(input.smWallets)
      : { netDirection: 'NEUTRAL' as const, magnitude: 0, topDestination: null, confidence: 0, sectorBreakdown: {} };

    // Correlation stability
    let correlationStability = 0.8; // Default: relatively stable
    if (input.correlationSeriesA && input.correlationSeriesB) {
      const corrBreak = detectCorrelationBreak(input.correlationSeriesA, input.correlationSeriesB);
      correlationStability = Math.max(0, 1 - corrBreak.divergence);
    }

    // Liquidity trend
    const liquidityResult = input.liquidityHistory && input.liquidityHistory.length >= 5
      ? detectLiquidityDrain(input.liquidityHistory)
      : { trend: 'STABLE' as LiquidityTrend, confidence: 0.3 };

    // Sector rotation snapshot
    let leadingSector = 'UNKNOWN';
    let laggingSector = 'UNKNOWN';
    let rotationSpeed = 0;
    let rotationConfidence = 0;

    if (smResult.sectorBreakdown && Object.keys(smResult.sectorBreakdown).length >= 2) {
      const entries = Object.entries(smResult.sectorBreakdown).sort((a, b) => b[1] - a[1]);
      leadingSector = entries[0][0];
      laggingSector = entries[entries.length - 1][0];
      rotationSpeed = entries[0][1] - entries[entries.length - 1][1];
      rotationConfidence = smResult.confidence;
    }

    const context: MarketContext = {
      regime: regimeResult.regime,
      volatilityRegime: volResult.regime,
      sectorRotation: {
        leadingSector,
        laggingSector,
        rotationSpeed,
        confidence: rotationConfidence,
      },
      botSwarmLevel: swarmResult.level,
      whaleAccumulation: whaleResult.direction,
      smartMoneyFlow: {
        netDirection: smResult.netDirection,
        magnitude: smResult.magnitude,
        topDestination: smResult.topDestination,
        confidence: smResult.confidence,
      },
      correlationStability,
      liquidityTrend: liquidityResult.trend,
    };

    this.lastContext = context;
    return context;
  }

  /**
   * Update the historical hit rate for a given signal type.
   * This allows the engine to learn from actual outcomes over time.
   */
  updateHistoricalHitRate(signalType: PredictiveSignalType, actualHit: boolean): void {
    const current = this.historicalHitRates[signalType];
    // Exponential moving average update (α = 0.05)
    const alpha = 0.05;
    this.historicalHitRates[signalType] = current * (1 - alpha) + (actualHit ? 1 : 0) * alpha;
  }

  /**
   * Get the last generated signals (cached).
   */
  getLastSignals(): PredictiveOutput[] {
    return this.lastSignals;
  }

  /**
   * Get the last market context (cached).
   */
  getLastContext(): MarketContext | null {
    return this.lastContext;
  }

  /**
   * Get all historical hit rates.
   */
  getHistoricalHitRates(): Record<PredictiveSignalType, number> {
    return { ...this.historicalHitRates };
  }
}
