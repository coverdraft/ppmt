/**
 * Market Regime Engine — CryptoQuant Terminal
 *
 * HMM-inspired regime detection engine replacing the basic regime-heuristic.ts
 * that only used MA(7) vs MA(25) + volatility percentile.
 *
 * This engine uses multi-factor scoring across 5 dimensions:
 *   1. Trend Strength   — MA alignment, ADX, EMA crossovers
 *   2. Volatility Regime — Realized vol, BB width, ATR percentile
 *   3. Volume Profile    — Volume trend, up/down volume ratio
 *   4. Smart Money Flow  — Net flow, whale activity, exchange flows
 *   5. Momentum          — RSI, rate of change, MACD histogram
 *
 * Regime Types (unified):
 *   TRENDING_BULL    — Sustained upward trend with momentum
 *   TRENDING_BEAR    — Sustained downward trend with momentum
 *   RANGING          — Sideways, mean-reverting, low directional conviction
 *   ACCUMULATION     — Smart money accumulating, low vol, positive flow
 *   DISTRIBUTION     — Smart money distributing, high volume, negative flow
 *   PANIC            — Extreme selling, high vol, cascading liquidations
 *   EUPHORIA         — Extreme buying, FOMO, parabolic moves
 *
 * Backward Compatibility:
 *   - Re-exports `regimeHeuristic` alias from the legacy engine
 *   - Exports `marketRegimeEngine` as the new singleton
 *   - Falls back to legacy engine when insufficient data
 */

import { db } from '../../db';
import { regimeHeuristic as legacyRegime } from './regime-heuristic';
import type { OnChainData } from '../feature-store/types';
import {
  sma,
  ema,
  computeRSI,
  computeMACD,
  computeBollinger,
  computeATR,
  computeADX,
  computeVolumeProfile,
  type CandleData,
} from './technical-indicators';

// ============================================================
// REGIME TYPES — Unified across the entire system
// ============================================================

export type MarketRegime =
  | 'TRENDING_BULL'
  | 'TRENDING_BEAR'
  | 'RANGING'
  | 'ACCUMULATION'
  | 'DISTRIBUTION'
  | 'PANIC'
  | 'EUPHORIA';

/** All possible regime values for iteration */
export const ALL_REGIMES: MarketRegime[] = [
  'TRENDING_BULL',
  'TRENDING_BEAR',
  'RANGING',
  'ACCUMULATION',
  'DISTRIBUTION',
  'PANIC',
  'EUPHORIA',
];

// ============================================================
// REGIME ASSESSMENT OUTPUT
// ============================================================

export interface KeyIndicator {
  name: string;
  value: number;
  signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
}

export interface RegimeAssessment {
  regime: MarketRegime;
  confidence: number; // 0-1
  transitionProbabilities: Map<MarketRegime, number>;
  durationEstimate: 'hours' | 'days' | 'weeks';
  keyIndicators: KeyIndicator[];
  lastChangedAt: Date;
  assessedAt: Date;
}

// ============================================================
// FACTOR SCORE TYPES
// ============================================================

export interface FactorScores {
  trendStrength: number;       // -1 to 1 (negative = bearish trend)
  volatilityRegime: number;    // 0 to 1 (0 = low, 1 = extreme)
  volumeProfile: number;       // 0 to 1 (0 = low, 1 = high)
  smartMoneyFlow: number;      // -1 to 1 (negative = distribution, positive = accumulation)
  momentum: number;            // -1 to 1 (negative = bearish momentum)
}

/** Internal computation details for debugging / transparency */
export interface RegimeComputationDetail {
  factors: FactorScores;
  rawScores: {
    ma7: number;
    ma25: number;
    ma50: number;
    ma200: number;
    adx: number;
    volPercentile: number;
    bbWidthPercentile: number;
    atrPercentile: number;
    volumeTrend7d: number;
    volumeVsAvg: number;
    upVolumeRatio: number;
    rsi14: number;
    roc7d: number;
    roc30d: number;
    macdHistogramTrend: number;
    smartMoneyNetFlow: number;
    whaleActivity: number;
  };
  classificationScores: Record<MarketRegime, number>;
  dataPointsUsed: number;
  onChainDataAvailable: boolean;
}

// ============================================================
// LEGACY REGIME MAPPING
// ============================================================

/** Map from legacy regime types to new regime types */
const LEGACY_TO_NEW_MAP: Record<string, MarketRegime> = {
  TRENDING_UP: 'TRENDING_BULL',
  TRENDING_DOWN: 'TRENDING_BEAR',
  SIDEWAYS: 'RANGING',
  HIGH_VOLATILITY: 'PANIC',
  LOW_VOLATILITY: 'RANGING',
};

// ============================================================
// DEFAULT TRANSITION MATRIX
// ============================================================

/**
 * Empirical base transition probabilities (row-stochastic).
 * Will be overridden by DB data when available.
 *
 * Structure: from current regime → probability of transitioning to each regime.
 * Diagonal is high (regimes tend to persist).
 */
const BASE_TRANSITION_MATRIX: Record<MarketRegime, Record<MarketRegime, number>> = {
  TRENDING_BULL: {
    TRENDING_BULL: 0.65,
    TRENDING_BEAR: 0.03,
    RANGING: 0.15,
    ACCUMULATION: 0.02,
    DISTRIBUTION: 0.08,
    PANIC: 0.02,
    EUPHORIA: 0.05,
  },
  TRENDING_BEAR: {
    TRENDING_BULL: 0.03,
    TRENDING_BEAR: 0.60,
    RANGING: 0.15,
    ACCUMULATION: 0.05,
    DISTRIBUTION: 0.02,
    PANIC: 0.12,
    EUPHORIA: 0.03,
  },
  RANGING: {
    TRENDING_BULL: 0.15,
    TRENDING_BEAR: 0.15,
    RANGING: 0.45,
    ACCUMULATION: 0.10,
    DISTRIBUTION: 0.05,
    PANIC: 0.05,
    EUPHORIA: 0.05,
  },
  ACCUMULATION: {
    TRENDING_BULL: 0.30,
    TRENDING_BEAR: 0.03,
    RANGING: 0.20,
    ACCUMULATION: 0.35,
    DISTRIBUTION: 0.05,
    PANIC: 0.02,
    EUPHORIA: 0.05,
  },
  DISTRIBUTION: {
    TRENDING_BULL: 0.03,
    TRENDING_BEAR: 0.30,
    RANGING: 0.20,
    ACCUMULATION: 0.05,
    DISTRIBUTION: 0.30,
    PANIC: 0.10,
    EUPHORIA: 0.02,
  },
  PANIC: {
    TRENDING_BULL: 0.05,
    TRENDING_BEAR: 0.30,
    RANGING: 0.25,
    ACCUMULATION: 0.15,
    DISTRIBUTION: 0.05,
    PANIC: 0.15,
    EUPHORIA: 0.05,
  },
  EUPHORIA: {
    TRENDING_BULL: 0.25,
    TRENDING_BEAR: 0.10,
    RANGING: 0.10,
    ACCUMULATION: 0.02,
    DISTRIBUTION: 0.25,
    PANIC: 0.08,
    EUPHORIA: 0.20,
  },
};

// ============================================================
// DURATION ESTIMATION HEURISTICS
// ============================================================

/** Based on typical regime durations in crypto markets */
const REGIME_DURATION_ESTIMATE: Record<MarketRegime, 'hours' | 'days' | 'weeks'> = {
  TRENDING_BULL: 'weeks',
  TRENDING_BEAR: 'weeks',
  RANGING: 'days',
  ACCUMULATION: 'weeks',
  DISTRIBUTION: 'days',
  PANIC: 'hours',
  EUPHORIA: 'hours',
};

// ============================================================
// MINIMUM DATA POINTS
// ============================================================

const MIN_PRICES_FOR_FULL_ENGINE = 50;
const MIN_PRICES_FOR_PARTIAL = 25;

// ============================================================
// MARKET REGIME ENGINE CLASS
// ============================================================

class MarketRegimeEngine {
  /** Cache of last known regime change per token/chain key */
  private regimeChangeCache = new Map<string, Date>();

  /** Cache of transition matrices loaded from DB */
  private dbTransitionMatrix: Record<MarketRegime, Record<MarketRegime, number>> | null = null;
  private lastTransitionMatrixLoad = 0;
  private readonly TRANSITION_MATRIX_TTL_MS = 30 * 60 * 1000; // 30 minutes

  // ============================================================
  // PUBLIC API
  // ============================================================

  /**
   * Assess current market regime for a specific token or overall market.
   *
   * @param tokenAddress - If provided, assess regime for that token
   * @param chain - Blockchain (default: SOL)
   * @returns RegimeAssessment with full details
   */
  async assessRegime(tokenAddress?: string, chain?: string): Promise<RegimeAssessment> {
    const effectiveChain = chain ?? 'SOL';

    if (tokenAddress) {
      return this.assessTokenRegime(tokenAddress, effectiveChain);
    }

    // Overall market regime using BTC + ETH + top tokens
    return this.assessMarketRegime(effectiveChain);
  }

  /**
   * Core regime detection from raw market data arrays.
   *
   * @param prices - Array of closing prices (chronological, oldest first)
   * @param volumes - Array of volume values (same length as prices)
   * @param onChainData - Optional on-chain data for smart money flow factor
   * @returns RegimeAssessment with multi-factor analysis
   */
  assessFromMarketData(
    prices: number[],
    volumes: number[],
    onChainData?: OnChainData,
  ): RegimeAssessment {
    const now = new Date();

    // Insufficient data — fall back to legacy engine
    if (prices.length < MIN_PRICES_FOR_PARTIAL) {
      const legacyResult = legacyRegime.assessRegime(prices);
      const mappedRegime = LEGACY_TO_NEW_MAP[legacyResult.regime] ?? 'RANGING';
      return {
        regime: mappedRegime,
        confidence: Math.max(0.1, legacyResult.confidence * 0.5),
        transitionProbabilities: this.getDefaultTransitionProbabilities(mappedRegime),
        durationEstimate: 'days',
        keyIndicators: [
          { name: 'legacy_regime', value: 1, signal: 'NEUTRAL' },
          { name: 'data_points', value: prices.length, signal: 'NEUTRAL' },
        ],
        lastChangedAt: now,
        assessedAt: now,
      };
    }

    // Build candle data from prices and volumes for indicator computation
    const candles = this.buildCandles(prices, volumes);

    // Compute all 5 factor scores
    const factors = this.computeFactorScores(prices, volumes, candles, onChainData);

    // Classify regime based on factor scores
    const { regime, confidence, classificationScores } = this.classifyRegime(factors);

    // Build key indicators list
    const keyIndicators = this.buildKeyIndicators(prices, volumes, candles, factors);

    // Estimate duration
    const durationEstimate = REGIME_DURATION_ESTIMATE[regime];

    // Get or initialize last changed date
    const lastChangedAt = now; // In production, this would be tracked from DB

    // Get transition probabilities
    const transitionProbabilities = this.computeTransitionProbabilities(regime, confidence, factors);

    return {
      regime,
      confidence: Math.round(confidence * 100) / 100,
      transitionProbabilities,
      durationEstimate,
      keyIndicators,
      lastChangedAt,
      assessedAt: now,
    };
  }

  /**
   * Get transition probabilities from a current regime.
   *
   * Queries TokenLifecycleState + PredictiveSignal history from DB
   * to build empirical transition matrix. Falls back to base matrix
   * when insufficient DB data.
   *
   * @param currentRegime - The current regime state
   * @returns Map of regime → transition probability
   */
  async getTransitionProbabilities(currentRegime: MarketRegime): Promise<Map<MarketRegime, number>> {
    const matrix = await this.loadTransitionMatrix();
    const row = matrix[currentRegime];
    const result = new Map<MarketRegime, number>();
    for (const regime of ALL_REGIMES) {
      result.set(regime, row[regime] ?? 0);
    }
    return result;
  }

  // ============================================================
  // TOKEN-SPECIFIC REGIME ASSESSMENT
  // ============================================================

  private async assessTokenRegime(
    tokenAddress: string,
    chain: string,
  ): Promise<RegimeAssessment> {
    try {
      // Load price candles from DB
      const candles = await this.loadCandlesFromDB(tokenAddress, chain);

      if (candles.length < MIN_PRICES_FOR_PARTIAL) {
        // Fall back to legacy engine
        const legacyResult = await legacyRegime.assessRegimeFromDB(tokenAddress, chain);
        const mappedRegime = LEGACY_TO_NEW_MAP[legacyResult.regime] ?? 'RANGING';
        return {
          regime: mappedRegime,
          confidence: Math.max(0.1, legacyResult.confidence * 0.6),
          transitionProbabilities: this.getDefaultTransitionProbabilities(mappedRegime),
          durationEstimate: 'days',
          keyIndicators: [
            { name: 'legacy_regime', value: 1, signal: 'NEUTRAL' },
            { name: 'data_points', value: candles.length, signal: 'NEUTRAL' },
          ],
          lastChangedAt: new Date(),
          assessedAt: new Date(),
        };
      }

      const prices = candles.map(c => c.close);
      const volumes = candles.map(c => c.volume);

      // Try to load on-chain data
      const onChainData = await this.loadOnChainDataFromDB(tokenAddress, chain);

      return this.assessFromMarketData(prices, volumes, onChainData ?? undefined);
    } catch (error) {
      console.error('[MarketRegimeEngine] Error assessing token regime:', error);
      return {
        regime: 'RANGING',
        confidence: 0.1,
        transitionProbabilities: this.getDefaultTransitionProbabilities('RANGING'),
        durationEstimate: 'days',
        keyIndicators: [],
        lastChangedAt: new Date(),
        assessedAt: new Date(),
      };
    }
  }

  // ============================================================
  // MARKET-WIDE REGIME ASSESSMENT
  // ============================================================

  private async assessMarketRegime(chain: string): Promise<RegimeAssessment> {
    try {
      // Try BTC and ETH as market proxies
      const btcAssessment = await this.assessTokenRegime('BTC', chain);
      const ethAssessment = await this.assessTokenRegime('ETH', chain);

      // Also try top tokens from DB
      const topTokens = await db.token.findMany({
        where: { chain },
        orderBy: { marketCap: 'desc' },
        take: 10,
        select: { address: true },
      });

      const tokenAssessments: RegimeAssessment[] = [btcAssessment, ethAssessment];

      // Assess up to 5 additional top tokens
      for (const token of topTokens.slice(0, 5)) {
        try {
          const assessment = await this.assessTokenRegime(token.address, chain);
          tokenAssessments.push(assessment);
        } catch {
          // Skip tokens with insufficient data
        }
      }

      // Aggregate: weighted vote by confidence
      return this.aggregateRegimeAssessments(tokenAssessments);
    } catch (error) {
      console.error('[MarketRegimeEngine] Error assessing market regime:', error);
      return {
        regime: 'RANGING',
        confidence: 0.1,
        transitionProbabilities: this.getDefaultTransitionProbabilities('RANGING'),
        durationEstimate: 'days',
        keyIndicators: [],
        lastChangedAt: new Date(),
        assessedAt: new Date(),
      };
    }
  }

  /**
   * Aggregate multiple regime assessments into one market-wide assessment.
   * Uses confidence-weighted voting.
   */
  private aggregateRegimeAssessments(
    assessments: RegimeAssessment[],
  ): RegimeAssessment {
    if (assessments.length === 0) {
      return {
        regime: 'RANGING',
        confidence: 0.1,
        transitionProbabilities: this.getDefaultTransitionProbabilities('RANGING'),
        durationEstimate: 'days',
        keyIndicators: [],
        lastChangedAt: new Date(),
        assessedAt: new Date(),
      };
    }

    if (assessments.length === 1) {
      return assessments[0];
    }

    // Confidence-weighted vote
    const voteMap = new Map<MarketRegime, number>();
    for (const assessment of assessments) {
      const current = voteMap.get(assessment.regime) ?? 0;
      voteMap.set(assessment.regime, current + assessment.confidence);
    }

    // Find winner
    let bestRegime: MarketRegime = 'RANGING';
    let bestScore = 0;
    for (const [regime, score] of voteMap) {
      if (score > bestScore) {
        bestScore = score;
        bestRegime = regime;
      }
    }

    // Confidence is the weighted fraction of the winning regime
    const totalConfidence = assessments.reduce((s, a) => s + a.confidence, 0);
    const confidence = totalConfidence > 0
      ? Math.min(1, bestScore / totalConfidence * 0.9 + 0.1)
      : 0.1;

    // Merge transition probabilities
    const mergedTransitions = new Map<MarketRegime, number>();
    for (const regime of ALL_REGIMES) {
      let weightedProb = 0;
      let totalWeight = 0;
      for (const assessment of assessments) {
        const prob = assessment.transitionProbabilities.get(regime) ?? 0;
        weightedProb += prob * assessment.confidence;
        totalWeight += assessment.confidence;
      }
      mergedTransitions.set(regime, totalWeight > 0 ? weightedProb / totalWeight : 0);
    }

    // Collect top key indicators across assessments
    const allIndicators = assessments.flatMap(a => a.keyIndicators);

    return {
      regime: bestRegime,
      confidence: Math.round(confidence * 100) / 100,
      transitionProbabilities: mergedTransitions,
      durationEstimate: REGIME_DURATION_ESTIMATE[bestRegime],
      keyIndicators: allIndicators.slice(0, 10),
      lastChangedAt: new Date(),
      assessedAt: new Date(),
    };
  }

  // ============================================================
  // FACTOR COMPUTATION
  // ============================================================

  /**
   * Compute all 5 factor scores from market data.
   */
  private computeFactorScores(
    prices: number[],
    volumes: number[],
    candles: CandleData[],
    onChainData?: OnChainData,
  ): FactorScores {
    const useFullEngine = prices.length >= MIN_PRICES_FOR_FULL_ENGINE;

    // Factor 1: Trend Strength
    const trendStrength = this.computeTrendStrength(prices, candles, useFullEngine);

    // Factor 2: Volatility Regime
    const volatilityRegime = this.computeVolatilityRegime(prices, candles, useFullEngine);

    // Factor 3: Volume Profile
    const volumeProfile = this.computeVolumeProfile(volumes, prices, useFullEngine);

    // Factor 4: Smart Money Flow
    const smartMoneyFlow = this.computeSmartMoneyFlow(onChainData, volumes, prices);

    // Factor 5: Momentum
    const momentum = this.computeMomentum(prices, candles, useFullEngine);

    return {
      trendStrength: clamp(trendStrength, -1, 1),
      volatilityRegime: clamp(volatilityRegime, 0, 1),
      volumeProfile: clamp(volumeProfile, 0, 1),
      smartMoneyFlow: clamp(smartMoneyFlow, -1, 1),
      momentum: clamp(momentum, -1, 1),
    };
  }

  // ----------------------------------------------------------
  // Factor 1: Trend Strength (-1 to 1)
  // ----------------------------------------------------------

  private computeTrendStrength(
    prices: number[],
    candles: CandleData[],
    fullEngine: boolean,
  ): number {
    const closes = prices;
    const n = closes.length;
    const lastPrice = closes[n - 1];

    // MA computation — use defaults if not enough data
    const ma7 = this.lastValid(sma(closes, 7)) ?? lastPrice;
    const ma25 = this.lastValid(sma(closes, 25)) ?? lastPrice;
    const ma50 = fullEngine ? (this.lastValid(sma(closes, 50)) ?? lastPrice) : null;
    const ma200 = fullEngine && n >= 200 ? (this.lastValid(sma(closes, 200)) ?? lastPrice) : null;

    let score = 0;

    // MA alignment score (up to 0.4)
    if (fullEngine && ma50 !== null && ma200 !== null) {
      if (ma7 > ma25 && ma25 > ma50 && ma50 > ma200) {
        score += 0.4; // Perfect bull alignment
      } else if (ma7 < ma25 && ma25 < ma50 && ma50 < ma200) {
        score -= 0.4; // Perfect bear alignment
      } else if (ma7 > ma25 && ma25 > ma50) {
        score += 0.25; // Partial bull
      } else if (ma7 < ma25 && ma25 < ma50) {
        score -= 0.25; // Partial bear
      } else if (ma7 > ma25) {
        score += 0.1;
      } else if (ma7 < ma25) {
        score -= 0.1;
      }
    } else {
      // Limited MAs
      if (ma7 > ma25) {
        score += 0.2;
      } else if (ma7 < ma25) {
        score -= 0.2;
      }
    }

    // Price vs MAs (up to 0.3)
    if (ma25 > 0) {
      const priceVsMA25 = (lastPrice - ma25) / ma25;
      score += clamp(priceVsMA25 * 5, -0.3, 0.3);
    }

    // ADX contribution (up to 0.3)
    if (fullEngine && candles.length >= 28) {
      const adxValues = computeADX(candles, 14);
      const adx = this.lastValid(adxValues);
      if (adx !== null && !isNaN(adx)) {
        // ADX > 25 = trending, > 50 = strong trend
        const adxScore = Math.min(1, Math.max(0, (adx - 15) / 40));
        // Direction from MA comparison
        const direction = ma7 > ma25 ? 1 : ma7 < ma25 ? -1 : 0;
        score += adxScore * 0.3 * direction;
      }
    }

    // EMA crossover signal (bonus)
    if (fullEngine && n >= 26) {
      const ema12Vals = ema(closes, 12);
      const ema26Vals = ema(closes, 26);
      const ema12Last = this.lastValid(ema12Vals);
      const ema26Last = this.lastValid(ema26Vals);
      if (ema12Last !== null && ema26Last !== null && !isNaN(ema12Last) && !isNaN(ema26Last)) {
        const emaDiff = (ema12Last - ema26Last) / ema26Last;
        score += clamp(emaDiff * 3, -0.1, 0.1);
      }
    }

    return clamp(score, -1, 1);
  }

  // ----------------------------------------------------------
  // Factor 2: Volatility Regime (0 to 1)
  // ----------------------------------------------------------

  private computeVolatilityRegime(
    prices: number[],
    candles: CandleData[],
    fullEngine: boolean,
  ): number {
    const n = prices.length;
    let score = 0;

    // Realized vol vs 30-day average
    const returns = this.computeReturns(prices);
    if (returns.length >= 30) {
      const recentVol = this.stdDev(returns.slice(-7));
      const avgVol = this.stdDev(returns.slice(-30));
      if (avgVol > 0) {
        const volRatio = recentVol / avgVol;
        // Ratio 1 = normal, >1.5 = high, >2.5 = extreme
        score += clamp((volRatio - 0.5) / 2.5, 0, 0.4);
      }
    } else if (returns.length >= 7) {
      const vol = this.stdDev(returns.slice(-7));
      // Compare to a baseline (rough heuristic)
      score += clamp(vol * 10, 0, 0.3);
    }

    // Bollinger Band width percentile
    if (fullEngine && n >= 20) {
      const bb = computeBollinger(closes_from_prices(prices), 20, 2);
      const bandwidth = this.lastValid(bb.bandwidth);
      if (bandwidth !== null && !isNaN(bandwidth)) {
        // Compute bandwidth percentile over history
        const validBandwidths = bb.bandwidth.filter(v => !isNaN(v));
        if (validBandwidths.length >= 5) {
          const belowCount = validBandwidths.filter(v => v < bandwidth).length;
          const pct = belowCount / validBandwidths.length;
          score += pct * 0.3;
        }
      }
    }

    // ATR vs historical ATR percentile
    if (fullEngine && candles.length >= 15) {
      const atrValues = computeATR(candles, 14);
      const atrCurrent = this.lastValid(atrValues);
      if (atrCurrent !== null && !isNaN(atrCurrent)) {
        const validATRs = atrValues.filter(v => !isNaN(v) && v > 0);
        if (validATRs.length >= 5) {
          const belowCount = validATRs.filter(v => v < atrCurrent).length;
          const pct = belowCount / validATRs.length;
          score += pct * 0.3;
        }
      }
    }

    return clamp(score, 0, 1);
  }

  // ----------------------------------------------------------
  // Factor 3: Volume Profile (0 to 1)
  // ----------------------------------------------------------

  private computeVolumeProfile(
    volumes: number[],
    prices: number[],
    _fullEngine: boolean,
  ): number {
    const n = volumes.length;
    if (n < 7) return 0.5;

    let score = 0;

    // Volume trend: increasing/decreasing over 7-day
    const vol7 = volumes.slice(-7);
    const vol7Avg = vol7.reduce((s, v) => s + v, 0) / 7;
    const vol7FirstHalf = vol7.slice(0, 3).reduce((s, v) => s + v, 0) / 3;
    const vol7SecondHalf = vol7.slice(-3).reduce((s, v) => s + v, 0) / 3;

    if (vol7FirstHalf > 0) {
      const volTrend = vol7SecondHalf / vol7FirstHalf;
      // Trend > 1.5 = strong increasing, < 0.5 = strong decreasing
      score += clamp((volTrend - 0.5) / 2, 0, 0.3);
    }

    // Volume vs 30-day average
    if (n >= 30) {
      const vol30Avg = volumes.slice(-30).reduce((s, v) => s + v, 0) / 30;
      if (vol30Avg > 0) {
        const volVsAvg = vol7Avg / vol30Avg;
        // Ratio > 2 = very high volume, < 0.5 = very low
        score += clamp((volVsAvg - 0.3) / 2, 0, 0.4);
      }
    }

    // Up-volume ratio: volume on up days vs down days
    if (n >= 7) {
      let upVolume = 0;
      let downVolume = 0;
      for (let i = Math.max(1, n - 7); i < n; i++) {
        if (prices[i] > prices[i - 1]) {
          upVolume += volumes[i];
        } else if (prices[i] < prices[i - 1]) {
          downVolume += volumes[i];
        }
      }
      const totalVol = upVolume + downVolume;
      if (totalVol > 0) {
        const upVolRatio = upVolume / totalVol;
        // 0.5 = neutral, > 0.6 = bullish volume, < 0.4 = bearish volume
        score += clamp((upVolRatio - 0.3) * 1.5, 0, 0.3);
      }
    }

    return clamp(score, 0, 1);
  }

  // ----------------------------------------------------------
  // Factor 4: Smart Money Flow (-1 to 1)
  // ----------------------------------------------------------

  private computeSmartMoneyFlow(
    onChainData?: OnChainData,
    volumes?: number[],
    prices?: number[],
  ): number {
    // If on-chain data available, use it
    if (onChainData) {
      let score = 0;

      // Net smart money flow
      const smNetFlow = onChainData.smartMoneyNetFlow;
      // Normalize: we need a reference volume to make this relative
      const refVolume = Math.abs(onChainData.whaleFlow24h) || 1;
      const normalizedFlow = smNetFlow / refVolume;
      score += clamp(normalizedFlow * 2, -0.4, 0.4);

      // Whale wallet activity
      const whaleFlow1h = onChainData.whaleFlow1h;
      const whaleFlow24h = onChainData.whaleFlow24h;
      if (whaleFlow24h !== 0) {
        const whaleActivity = whaleFlow1h / (whaleFlow24h / 24);
        // >1 = accelerating, <1 = decelerating
        if (whaleActivity > 1.5) {
          score += whaleFlow1h > 0 ? 0.2 : -0.2;
        } else if (whaleActivity < 0.5) {
          score += 0; // Neutral
        }
      }

      // Bot activity ratio (lower bots = more "smart" dominated)
      if (onChainData.botActivityRatio < 0.2) {
        score += 0.1; // Low bot activity = more organic/smart
      } else if (onChainData.botActivityRatio > 0.6) {
        score -= 0.1; // High bot activity = less smart money signal
      }

      return clamp(score, -1, 1);
    }

    // Fallback: estimate from price-volume patterns
    if (volumes && prices && volumes.length >= 14) {
      const n = volumes.length;
      // Smart money tends to accumulate on down days with volume
      // and distribute on up days with volume
      let accumulationScore = 0;
      let count = 0;

      for (let i = Math.max(1, n - 14); i < n; i++) {
        const priceChange = (prices[i] - prices[i - 1]) / prices[i - 1];
        const volChange = volumes[i] / (volumes.slice(-14).reduce((s, v) => s + v, 0) / 14 + 1);

        // High volume on down day = potential accumulation
        if (priceChange < -0.01 && volChange > 1.2) {
          accumulationScore += 0.3;
        }
        // High volume on up day = potential distribution
        else if (priceChange > 0.01 && volChange > 1.2) {
          accumulationScore -= 0.3;
        }
        // Low volume, flat price = accumulation
        else if (Math.abs(priceChange) < 0.005 && volChange < 0.8) {
          accumulationScore += 0.1;
        }
        count++;
      }

      return count > 0 ? clamp(accumulationScore / count * 3, -1, 1) : 0;
    }

    return 0; // Neutral if no data
  }

  // ----------------------------------------------------------
  // Factor 5: Momentum (-1 to 1)
  // ----------------------------------------------------------

  private computeMomentum(
    prices: number[],
    candles: CandleData[],
    fullEngine: boolean,
  ): number {
    const n = prices.length;
    if (n < 14) return 0;

    let score = 0;

    // RSI(14) contribution
    const rsiValues = computeRSI(prices, 14);
    const rsi = this.lastValid(rsiValues);
    if (rsi !== null && !isNaN(rsi)) {
      // RSI 50 = neutral, >70 = overbought, <30 = oversold
      const rsiScore = (rsi - 50) / 50; // -1 to 1
      score += clamp(rsiScore * 0.4, -0.4, 0.4);
    }

    // Rate of change: 7-day and 30-day
    if (n >= 8) {
      const roc7d = (prices[n - 1] - prices[n - 8]) / prices[n - 8];
      score += clamp(roc7d * 5, -0.2, 0.2);
    }
    if (n >= 31) {
      const roc30d = (prices[n - 1] - prices[n - 31]) / prices[n - 31];
      score += clamp(roc30d * 2, -0.15, 0.15);
    }

    // MACD histogram trend
    if (fullEngine && n >= 35) {
      const macdResult = computeMACD(prices, 12, 26, 9);
      const hist = macdResult.histogram;
      const histLast = this.lastValid(hist);
      const histPrev = this.lastValidBefore(hist);

      if (histLast !== null && histPrev !== null && !isNaN(histLast) && !isNaN(histPrev)) {
        // Normalize by price to make it comparable
        const lastPrice = prices[n - 1] || 1;
        const normalizedHist = histLast / lastPrice * 100;
        const normalizedPrev = histPrev / lastPrice * 100;

        // Positive and rising = bullish momentum
        if (normalizedHist > 0 && normalizedHist > normalizedPrev) {
          score += 0.15;
        } else if (normalizedHist < 0 && normalizedHist < normalizedPrev) {
          score -= 0.15;
        } else if (normalizedHist > 0) {
          score += 0.05;
        } else if (normalizedHist < 0) {
          score -= 0.05;
        }
      }
    }

    return clamp(score, -1, 1);
  }

  // ============================================================
  // REGIME CLASSIFICATION
  // ============================================================

  /**
   * Classify the current regime from factor scores using HMM-inspired
   * scoring matrix. Returns the regime with highest classification score
   * along with confidence and all scores.
   */
  private classifyRegime(
    factors: FactorScores,
  ): { regime: MarketRegime; confidence: number; classificationScores: Record<MarketRegime, number> } {
    const { trendStrength, volatilityRegime, volumeProfile, smartMoneyFlow, momentum } = factors;

    // Compute classification score for each regime
    // Higher score = more likely the current state matches this regime
    const scores: Record<MarketRegime, number> = {
      TRENDING_BULL: 0,
      TRENDING_BEAR: 0,
      RANGING: 0,
      ACCUMULATION: 0,
      DISTRIBUTION: 0,
      PANIC: 0,
      EUPHORIA: 0,
    };

    // ---- TRENDING_BULL ----
    // Trend>0.7, Vol<0.7, Volume>0.5, Momentum>0.5
    {
      const trendMatch = sigmoid(trendStrength, 0.3, 10);
      const volMatch = 1 - sigmoid(volatilityRegime, 0.7, 8);
      const volumeMatch = sigmoid(volumeProfile, 0.3, 8);
      const momentumMatch = sigmoid(momentum, 0.2, 10);
      scores.TRENDING_BULL = (trendMatch * 0.35 + volMatch * 0.15 + volumeMatch * 0.20 + momentumMatch * 0.30);
    }

    // ---- TRENDING_BEAR ----
    // Trend<-0.7, Vol<0.7, Volume>0.5, Momentum<-0.5
    {
      const trendMatch = sigmoid(-trendStrength, 0.3, 10);
      const volMatch = 1 - sigmoid(volatilityRegime, 0.7, 8);
      const volumeMatch = sigmoid(volumeProfile, 0.3, 8);
      const momentumMatch = sigmoid(-momentum, 0.2, 10);
      scores.TRENDING_BEAR = (trendMatch * 0.35 + volMatch * 0.15 + volumeMatch * 0.20 + momentumMatch * 0.30);
    }

    // ---- RANGING ----
    // |Trend|<0.3, Vol<0.5
    {
      const trendMatch = 1 - sigmoid(Math.abs(trendStrength), 0.3, 8);
      const volMatch = 1 - sigmoid(volatilityRegime, 0.5, 8);
      scores.RANGING = (trendMatch * 0.6 + volMatch * 0.4);
    }

    // ---- ACCUMULATION ----
    // |Trend|<0.3, Vol<0.3, SmartMoney>0.5, Volume<0.3
    {
      const trendMatch = 1 - sigmoid(Math.abs(trendStrength), 0.3, 8);
      const volMatch = 1 - sigmoid(volatilityRegime, 0.3, 8);
      const smartMoneyMatch = sigmoid(smartMoneyFlow, 0.3, 8);
      const volumeMatch = 1 - sigmoid(volumeProfile, 0.4, 8);
      scores.ACCUMULATION = (trendMatch * 0.20 + volMatch * 0.25 + smartMoneyMatch * 0.35 + volumeMatch * 0.20);
    }

    // ---- DISTRIBUTION ----
    // |Trend|<0.5, Vol<0.5, SmartMoney<-0.5, Volume>0.5
    {
      const trendMatch = 1 - sigmoid(Math.abs(trendStrength), 0.5, 6);
      const volMatch = 1 - sigmoid(volatilityRegime, 0.5, 8);
      const smartMoneyMatch = sigmoid(-smartMoneyFlow, 0.3, 8);
      const volumeMatch = sigmoid(volumeProfile, 0.3, 8);
      scores.DISTRIBUTION = (trendMatch * 0.15 + volMatch * 0.15 + smartMoneyMatch * 0.40 + volumeMatch * 0.30);
    }

    // ---- PANIC ----
    // Vol>0.8, Momentum<-0.7, Volume>0.8
    {
      const volMatch = sigmoid(volatilityRegime, 0.6, 8);
      const momentumMatch = sigmoid(-momentum, 0.4, 8);
      const volumeMatch = sigmoid(volumeProfile, 0.6, 8);
      const trendBonus = trendStrength < -0.3 ? 0.15 : 0;
      scores.PANIC = (volMatch * 0.35 + momentumMatch * 0.35 + volumeMatch * 0.15 + trendBonus + (1 - sigmoid(Math.abs(smartMoneyFlow), 0.3, 6)) * 0.15);
    }

    // ---- EUPHORIA ----
    // Vol>0.7, Momentum>0.7, Volume>0.8, RSI>75 (encoded in momentum)
    {
      const volMatch = sigmoid(volatilityRegime, 0.5, 8);
      const momentumMatch = sigmoid(momentum, 0.5, 8);
      const volumeMatch = sigmoid(volumeProfile, 0.5, 8);
      const trendBonus = trendStrength > 0.3 ? 0.15 : 0;
      scores.EUPHORIA = (volMatch * 0.25 + momentumMatch * 0.35 + volumeMatch * 0.15 + trendBonus + sigmoid(smartMoneyFlow, 0.3, 6) * 0.10);
    }

    // Find the regime with highest score
    let bestRegime: MarketRegime = 'RANGING';
    let bestScore = -Infinity;
    for (const regime of ALL_REGIMES) {
      if (scores[regime] > bestScore) {
        bestScore = scores[regime];
        bestRegime = regime;
      }
    }

    // Confidence: how much the best regime stands out
    // If best is close to second-best, confidence is low
    const sortedScores = Object.values(scores).sort((a, b) => b - a);
    const gap = sortedScores[0] - (sortedScores[1] ?? 0);
    const confidence = clamp(gap * 3 + 0.3, 0.1, 0.95);

    return {
      regime: bestRegime,
      confidence,
      classificationScores: scores,
    };
  }

  // ============================================================
  // TRANSITION PROBABILITIES
  // ============================================================

  /**
   * Compute transition probabilities from the current regime,
   * blending the base matrix with current factor information.
   */
  private computeTransitionProbabilities(
    currentRegime: MarketRegime,
    confidence: number,
    factors: FactorScores,
  ): Map<MarketRegime, number> {
    const baseRow = BASE_TRANSITION_MATRIX[currentRegime];
    const result = new Map<MarketRegime, number>();

    // Start with base probabilities
    for (const regime of ALL_REGIMES) {
      result.set(regime, baseRow[regime] ?? 0);
    }

    // Adjust based on current factors (HMM-inspired emission influence)
    // If factors suggest a different regime is likely, boost that transition
    const factorBoosts = this.computeFactorBasedTransitionBoosts(factors);
    for (const regime of ALL_REGIMES) {
      const baseProb = result.get(regime) ?? 0;
      const boost = factorBoosts.get(regime) ?? 0;
      result.set(regime, baseProb + boost * (1 - confidence) * 0.2);
    }

    // Normalize to sum to 1
    this.normalizeProbabilities(result);

    return result;
  }

  /**
   * Compute transition probability boosts based on current factors.
   * This simulates the emission probability influence in an HMM.
   */
  private computeFactorBasedTransitionBoosts(factors: FactorScores): Map<MarketRegime, number> {
    const boosts = new Map<MarketRegime, number>();

    for (const regime of ALL_REGIMES) {
      boosts.set(regime, 0);
    }

    // If momentum is strongly positive, boost transitions toward bull regimes
    if (factors.momentum > 0.5) {
      boosts.set('TRENDING_BULL', factors.momentum * 0.3);
      boosts.set('EUPHORIA', factors.momentum * 0.15);
    }

    // If momentum is strongly negative, boost transitions toward bear regimes
    if (factors.momentum < -0.5) {
      boosts.set('TRENDING_BEAR', Math.abs(factors.momentum) * 0.3);
      boosts.set('PANIC', Math.abs(factors.momentum) * 0.15);
    }

    // If volatility is high, boost toward panic/euphoria
    if (factors.volatilityRegime > 0.7) {
      boosts.set('PANIC', factors.volatilityRegime * 0.2);
      boosts.set('EUPHORIA', factors.volatilityRegime * 0.15);
    }

    // If smart money is flowing in, boost toward accumulation
    if (factors.smartMoneyFlow > 0.5) {
      boosts.set('ACCUMULATION', factors.smartMoneyFlow * 0.25);
      boosts.set('TRENDING_BULL', factors.smartMoneyFlow * 0.15);
    }

    // If smart money is flowing out, boost toward distribution
    if (factors.smartMoneyFlow < -0.5) {
      boosts.set('DISTRIBUTION', Math.abs(factors.smartMoneyFlow) * 0.25);
      boosts.set('TRENDING_BEAR', Math.abs(factors.smartMoneyFlow) * 0.15);
    }

    return boosts;
  }

  /**
   * Normalize a probability map so all values sum to 1.
   */
  private normalizeProbabilities(probs: Map<MarketRegime, number>): void {
    let sum = 0;
    for (const [, val] of probs) {
      sum += Math.max(0, val);
    }
    if (sum > 0) {
      for (const [key, val] of probs) {
        probs.set(key, Math.max(0, val) / sum);
      }
    }
  }

  /**
   * Get default transition probabilities for a regime (no factor influence).
   */
  private getDefaultTransitionProbabilities(currentRegime: MarketRegime): Map<MarketRegime, number> {
    const row = BASE_TRANSITION_MATRIX[currentRegime];
    const result = new Map<MarketRegime, number>();
    for (const regime of ALL_REGIMES) {
      result.set(regime, row[regime] ?? 0);
    }
    return result;
  }

  // ============================================================
  // TRANSITION MATRIX LOADING FROM DB
  // ============================================================

  /**
   * Load transition matrix from DB, merging empirical observations
   * with the base matrix. Returns base matrix if insufficient data.
   */
  private async loadTransitionMatrix(): Promise<Record<MarketRegime, Record<MarketRegime, number>>> {
    const now = Date.now();
    if (this.dbTransitionMatrix && now - this.lastTransitionMatrixLoad < this.TRANSITION_MATRIX_TTL_MS) {
      return this.dbTransitionMatrix;
    }

    try {
      // Query PredictiveSignal history for regime change patterns
      const regimeSignals = await db.predictiveSignal.findMany({
        where: {
          signalType: { in: ['REGIME_CHANGE', 'VOLATILITY_REGIME'] },
        },
        orderBy: { createdAt: 'desc' },
        take: 500,
        select: {
          prediction: true,
          direction: true,
          confidence: true,
          wasCorrect: true,
          createdAt: true,
        },
      });

      if (regimeSignals.length < 10) {
        // Not enough data — use base matrix
        this.dbTransitionMatrix = BASE_TRANSITION_MATRIX;
        this.lastTransitionMatrixLoad = now;
        return BASE_TRANSITION_MATRIX;
      }

      // Count transitions from TokenLifecycleState history
      const lifecycleStates = await db.tokenLifecycleState.findMany({
        orderBy: { detectedAt: 'asc' },
        take: 2000,
        select: {
          phase: true,
          transitionFrom: true,
          transitionProb: true,
          detectedAt: true,
        },
      });

      // Build empirical transition counts
      const transCounts: Record<string, Record<string, number>> = {};
      for (const regime of ALL_REGIMES) {
        transCounts[regime] = {};
        for (const r2 of ALL_REGIMES) {
          transCounts[regime][r2] = 0;
        }
      }

      // Map lifecycle phases to regime categories for transition estimation
      const PHASE_TO_REGIME: Record<string, MarketRegime> = {
        GENESIS: 'ACCUMULATION',
        INCIPIENT: 'ACCUMULATION',
        GROWTH: 'TRENDING_BULL',
        FOMO: 'EUPHORIA',
        DECLINE: 'TRENDING_BEAR',
        LEGACY: 'RANGING',
      };

      // Process lifecycle transitions
      let prevRegime: MarketRegime | null = null;
      for (const state of lifecycleStates) {
        const currentRegime = PHASE_TO_REGIME[state.phase] ?? 'RANGING';
        if (prevRegime !== null && prevRegime !== currentRegime) {
          transCounts[prevRegime][currentRegime]++;
          transCounts[prevRegime][prevRegime]++; // Self-transition counts too
        } else if (prevRegime !== null) {
          transCounts[prevRegime][prevRegime]++;
        }
        prevRegime = currentRegime;
      }

      // Blend empirical with base matrix (Bayesian combination)
      const BETA = 20; // Prior strength (higher = more weight on base)
      const result: Record<MarketRegime, Record<MarketRegime, number>> = {} as Record<MarketRegime, Record<MarketRegime, number>>;

      for (const fromRegime of ALL_REGIMES) {
        result[fromRegime] = {} as Record<MarketRegime, number>;
        let totalEmpirical = 0;
        for (const toRegime of ALL_REGIMES) {
          totalEmpirical += transCounts[fromRegime][toRegime];
        }

        for (const toRegime of ALL_REGIMES) {
          const empirical = transCounts[fromRegime][toRegime];
          const prior = (BASE_TRANSITION_MATRIX[fromRegime][toRegime]) * BETA;
          const combined = (empirical + prior) / (totalEmpirical + BETA);
          result[fromRegime][toRegime] = Math.round(combined * 10000) / 10000;
        }

        // Normalize row
        const rowSum = ALL_REGIMES.reduce((s, r) => s + result[fromRegime][r], 0);
        if (rowSum > 0) {
          for (const toRegime of ALL_REGIMES) {
            result[fromRegime][toRegime] = result[fromRegime][toRegime] / rowSum;
          }
        }
      }

      this.dbTransitionMatrix = result;
      this.lastTransitionMatrixLoad = now;
      return result;
    } catch (error) {
      console.error('[MarketRegimeEngine] Error loading transition matrix:', error);
      return BASE_TRANSITION_MATRIX;
    }
  }

  // ============================================================
  // KEY INDICATORS BUILDER
  // ============================================================

  private buildKeyIndicators(
    prices: number[],
    volumes: number[],
    candles: CandleData[],
    factors: FactorScores,
  ): KeyIndicator[] {
    const indicators: KeyIndicator[] = [];
    const closes = prices;
    const n = closes.length;

    // RSI
    const rsiValues = computeRSI(closes, 14);
    const rsi = this.lastValid(rsiValues);
    if (rsi !== null && !isNaN(rsi)) {
      indicators.push({
        name: 'RSI(14)',
        value: Math.round(rsi * 100) / 100,
        signal: rsi > 70 ? 'BEARISH' : rsi < 30 ? 'BULLISH' : 'NEUTRAL',
      });
    }

    // ADX
    if (n >= 28) {
      const adxValues = computeADX(candles, 14);
      const adx = this.lastValid(adxValues);
      if (adx !== null && !isNaN(adx)) {
        indicators.push({
          name: 'ADX(14)',
          value: Math.round(adx * 100) / 100,
          signal: adx > 25 ? (factors.trendStrength > 0 ? 'BULLISH' : 'BEARISH') : 'NEUTRAL',
        });
      }
    }

    // MA alignment
    const ma7 = this.lastValid(sma(closes, 7));
    const ma25 = this.lastValid(sma(closes, 25));
    if (ma7 !== null && ma25 !== null) {
      const maDiff = ((ma7 - ma25) / ma25) * 100;
      indicators.push({
        name: 'MA(7/25) Gap',
        value: Math.round(maDiff * 100) / 100,
        signal: maDiff > 0.5 ? 'BULLISH' : maDiff < -0.5 ? 'BEARISH' : 'NEUTRAL',
      });
    }

    // Volatility percentile
    const returns = this.computeReturns(prices);
    if (returns.length >= 10) {
      const volPercentile = this.computeVolPercentile(prices);
      indicators.push({
        name: 'Vol Percentile',
        value: Math.round(volPercentile * 100) / 100,
        signal: volPercentile > 75 ? 'BEARISH' : volPercentile < 25 ? 'BULLISH' : 'NEUTRAL',
      });
    }

    // Volume vs average
    if (volumes.length >= 20) {
      const avgVol = volumes.slice(-20).reduce((s, v) => s + v, 0) / 20;
      const curVol = volumes[volumes.length - 1];
      if (avgVol > 0) {
        const volRatio = curVol / avgVol;
        indicators.push({
          name: 'Volume Ratio',
          value: Math.round(volRatio * 100) / 100,
          signal: volRatio > 1.5 ? 'BULLISH' : volRatio < 0.5 ? 'BEARISH' : 'NEUTRAL',
        });
      }
    }

    // MACD
    if (n >= 35) {
      const macdResult = computeMACD(closes);
      const hist = this.lastValid(macdResult.histogram);
      if (hist !== null && !isNaN(hist)) {
        indicators.push({
          name: 'MACD Histogram',
          value: Math.round(hist * 10000) / 10000,
          signal: hist > 0 ? 'BULLISH' : hist < 0 ? 'BEARISH' : 'NEUTRAL',
        });
      }
    }

    // Trend Strength factor
    indicators.push({
      name: 'Trend Strength',
      value: Math.round(factors.trendStrength * 100) / 100,
      signal: factors.trendStrength > 0.3 ? 'BULLISH' : factors.trendStrength < -0.3 ? 'BEARISH' : 'NEUTRAL',
    });

    // Smart Money Flow factor
    if (factors.smartMoneyFlow !== 0) {
      indicators.push({
        name: 'Smart Money Flow',
        value: Math.round(factors.smartMoneyFlow * 100) / 100,
        signal: factors.smartMoneyFlow > 0.3 ? 'BULLISH' : factors.smartMoneyFlow < -0.3 ? 'BEARISH' : 'NEUTRAL',
      });
    }

    return indicators;
  }

  // ============================================================
  // DATA LOADING HELPERS
  // ============================================================

  private async loadCandlesFromDB(
    tokenAddress: string,
    chain: string,
  ): Promise<CandleData[]> {
    try {
      // Try 4h candles first
      let candles = await db.priceCandle.findMany({
        where: { tokenAddress, chain, timeframe: '4h' },
        orderBy: { timestamp: 'asc' },
        take: 200,
      });

      if (candles.length < 25) {
        // Fall back to 1h candles
        candles = await db.priceCandle.findMany({
          where: { tokenAddress, chain, timeframe: '1h' },
          orderBy: { timestamp: 'asc' },
          take: 400,
        });
      }

      return candles.map(c => ({
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

  private async loadOnChainDataFromDB(
    tokenAddress: string,
    _chain: string,
  ): Promise<OnChainData | null> {
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

  // ============================================================
  // MATH UTILITIES
  // ============================================================

  private buildCandles(prices: number[], volumes: number[]): CandleData[] {
    // When we only have close prices and volumes, approximate OHLC
    return prices.map((close, i) => {
      const vol = i < volumes.length ? volumes[i] : 0;
      // Approximate high/low as ±0.5% of close (will be refined if we have real candle data)
      const range = close * 0.005;
      return {
        timestamp: i * 3600000, // Fake timestamps for indicator computation
        open: i > 0 ? prices[i - 1] : close,
        high: close + range,
        low: Math.max(0, close - range),
        close,
        volume: vol,
      };
    });
  }

  private computeReturns(prices: number[]): number[] {
    const returns: number[] = [];
    for (let i = 1; i < prices.length; i++) {
      if (prices[i - 1] > 0) {
        returns.push((prices[i] - prices[i - 1]) / prices[i - 1]);
      }
    }
    return returns;
  }

  private computeRollingVol(prices: number[], period: number): number {
    const returns = this.computeReturns(prices.slice(-(period + 1)));
    if (returns.length < 2) return 0;
    return this.stdDev(returns);
  }

  private computeVolPercentile(prices: number[]): number {
    if (prices.length < 10) return 50;

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

    const currentVol = rollingVols[rollingVols.length - 1];
    const belowCount = rollingVols.slice(0, -1).filter(v => v < currentVol).length;
    const totalHistorical = rollingVols.length - 1;

    return totalHistorical > 0 ? (belowCount / totalHistorical) * 100 : 50;
  }

  private stdDev(values: number[]): number {
    if (values.length < 2) return 0;
    const n = values.length;
    const mean = values.reduce((s, v) => s + v, 0) / n;
    const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1);
    return Math.sqrt(Math.max(0, variance));
  }

  /** Get last valid (non-NaN) value from an indicator array */
  private lastValid(arr: number[]): number | null {
    for (let i = arr.length - 1; i >= 0; i--) {
      if (!isNaN(arr[i])) return arr[i];
    }
    return null;
  }

  /** Get second-to-last valid (non-NaN) value from an indicator array */
  private lastValidBefore(arr: number[]): number | null {
    let found = false;
    for (let i = arr.length - 1; i >= 0; i--) {
      if (!isNaN(arr[i])) {
        if (found) return arr[i];
        found = true;
      }
    }
    return null;
  }

  /**
   * Get full computation details for debugging/transparency.
   * Not exposed in the public API but useful for analysis.
   */
  getComputationDetails(
    prices: number[],
    volumes: number[],
    onChainData?: OnChainData,
  ): RegimeComputationDetail | null {
    if (prices.length < MIN_PRICES_FOR_PARTIAL) return null;

    const candles = this.buildCandles(prices, volumes);
    const n = prices.length;
    const fullEngine = n >= MIN_PRICES_FOR_FULL_ENGINE;

    const factors = this.computeFactorScores(prices, volumes, candles, onChainData);
    const { classificationScores } = this.classifyRegime(factors);

    // Compute raw scores
    const closes = prices;
    const ma7 = this.lastValid(sma(closes, 7)) ?? 0;
    const ma25 = this.lastValid(sma(closes, 25)) ?? 0;
    const ma50 = this.lastValid(sma(closes, 50)) ?? 0;
    const ma200 = n >= 200 ? (this.lastValid(sma(closes, 200)) ?? 0) : 0;
    const adxVals = fullEngine ? computeADX(candles, 14) : [];
    const adx = this.lastValid(adxVals) ?? 0;

    const rsiValues = computeRSI(closes, 14);
    const rsi14 = this.lastValid(rsiValues) ?? 50;
    const roc7d = n >= 8 ? (closes[n - 1] - closes[n - 8]) / closes[n - 8] : 0;
    const roc30d = n >= 31 ? (closes[n - 1] - closes[n - 31]) / closes[n - 31] : 0;
    const macdResult = n >= 35 ? computeMACD(closes) : null;
    const macdHist = macdResult ? this.lastValid(macdResult.histogram) : 0;
    const macdPrev = macdResult ? this.lastValidBefore(macdResult.histogram) : 0;
    const macdTrend = macdHist !== null && macdPrev !== null
      ? macdHist - macdPrev
      : 0;

    const vol7 = this.computeRollingVol(prices, 7);
    const vol30 = this.computeRollingVol(prices, 30);
    const volPercentile = this.computeVolPercentile(prices);

    const vol7Avg = volumes.length >= 7
      ? volumes.slice(-7).reduce((s, v) => s + v, 0) / 7
      : 0;
    const vol30Avg = volumes.length >= 30
      ? volumes.slice(-30).reduce((s, v) => s + v, 0) / 30
      : 1;
    const volumeVsAvg = vol30Avg > 0 ? vol7Avg / vol30Avg : 1;

    // Up volume ratio
    let upVol = 0, downVol = 0;
    const start = Math.max(1, n - 7);
    for (let i = start; i < n; i++) {
      if (prices[i] > prices[i - 1]) upVol += volumes[i];
      else if (prices[i] < prices[i - 1]) downVol += volumes[i];
    }
    const totalVol = upVol + downVol;
    const upVolumeRatio = totalVol > 0 ? upVol / totalVol : 0.5;

    const bb = n >= 20 ? computeBollinger(closes_from_prices(closes), 20, 2) : null;
    const bbWidth = bb ? this.lastValid(bb.bandwidth) : 0;
    const bbWidthPct = bb && bb.bandwidth.length > 0
      ? (() => {
          const valid = bb.bandwidth.filter(v => !isNaN(v));
          if (valid.length < 3 || bbWidth === null || isNaN(bbWidth)) return 0.5;
          return valid.filter(v => v < bbWidth).length / valid.length;
        })()
      : 0.5;

    const atrVals = fullEngine ? computeATR(candles, 14) : [];
    const atrCurrent = this.lastValid(atrVals) ?? 0;
    const validATRs = atrVals.filter(v => !isNaN(v) && v > 0);
    const atrPct = validATRs.length >= 3 && atrCurrent > 0
      ? validATRs.filter(v => v < atrCurrent).length / validATRs.length
      : 0.5;

    // Volume trend
    const vol7First = volumes.length >= 7
      ? volumes.slice(-7, -4).reduce((s, v) => s + v, 0) / 3
      : 0;
    const vol7Second = volumes.length >= 7
      ? volumes.slice(-3).reduce((s, v) => s + v, 0) / 3
      : 0;
    const volumeTrend7d = vol7First > 0 ? vol7Second / vol7First : 1;

    return {
      factors,
      rawScores: {
        ma7,
        ma25,
        ma50,
        ma200,
        adx,
        volPercentile,
        bbWidthPercentile: bbWidthPct,
        atrPercentile: atrPct,
        volumeTrend7d,
        volumeVsAvg,
        upVolumeRatio,
        rsi14,
        roc7d,
        roc30d,
        macdHistogramTrend: macdTrend,
        smartMoneyNetFlow: onChainData?.smartMoneyNetFlow ?? 0,
        whaleActivity: onChainData?.whaleFlow1h ?? 0,
      },
      classificationScores,
      dataPointsUsed: n,
      onChainDataAvailable: onChainData !== undefined,
    };
  }
}

// ============================================================
// HELPER FUNCTIONS
// ============================================================

/** Clamp a number between min and max */
function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/**
 * Sigmoid-like function for smooth threshold transitions.
 * Maps input to 0-1 range with a soft threshold at `threshold`.
 * Higher `steepness` = sharper transition.
 *
 * f(x) = 1 / (1 + exp(-steepness * (x - threshold)))
 */
function sigmoid(x: number, threshold: number, steepness: number): number {
  return 1 / (1 + Math.exp(-steepness * (x - threshold)));
}

/**
 * Helper to create a closes array compatible with computeBollinger.
 * The Bollinger function expects a number array.
 */
function closes_from_prices(prices: number[]): number[] {
  return prices;
}

// ============================================================
// SINGLETON EXPORTS
// ============================================================

/** New engine singleton — the primary export */
export const marketRegimeEngine = new MarketRegimeEngine();

/**
 * Backward compatibility alias.
 * The legacy `regimeHeuristic` is re-exported so existing consumers
 * (risk-pre-filter, regime API route, alpha-ranking-engine, meta-model-engine)
 * continue to work without changes.
 *
 * Usage:
 *   import { regimeHeuristic } from './regime-heuristic';  // Legacy (still works)
 *   import { marketRegimeEngine } from './market-regime-engine';  // New engine
 */
export { regimeHeuristic } from './regime-heuristic';

/**
 * Re-export legacy types for backward compatibility.
 * The old MarketRegime type had different values (TRENDING_UP, TRENDING_DOWN, etc.)
 * Import the new MarketRegime type from this module instead.
 */
export type { MarketRegime as LegacyMarketRegime } from './regime-heuristic';
export type { RegimeAssessment as LegacyRegimeAssessment } from './regime-heuristic';
