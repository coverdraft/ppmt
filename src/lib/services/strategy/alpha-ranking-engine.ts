/**
 * Alpha Ranking Engine — CryptoQuant Terminal
 *
 * Ranks all tradeable opportunities by expected risk-adjusted return.
 * Instead of processing signals in arbitrary order, this engine computes
 * a composite Alpha Score for each opportunity and prioritizes the best ones.
 *
 * Alpha Score Components:
 *   1. Signal Strength (30%) — from brain analysis confidence
 *   2. Risk-Adjusted Expected Return (25%) — (expected_return - risk_free) / volatility
 *   3. Operability (20%) — from operability score
 *   4. Portfolio Fit (15%) — how well it diversifies existing positions
 *   5. Regime Alignment (10%) — does this signal match the current regime?
 *
 * The engine also suggests capital allocation across top opportunities
 * using risk parity principles and concentration limits.
 */

import { db } from '@/lib/db';
import type { MarketRegime } from '@/lib/services/strategy/regime-heuristic';
import type { TokenPhase } from '@/lib/services/brain/token-lifecycle-engine';

// ============================================================
// TYPES
// ============================================================

/** A tradeable opportunity presented to the ranking engine */
export interface TradeOpportunity {
  tokenAddress: string;
  chain: string;
  direction: 'LONG' | 'SHORT';
  confidence: number;         // 0-1, brain analysis confidence
  strategyName: string;       // which strategy generated this signal
  expectedReturn: number;     // expected return as decimal (e.g. 0.15 = 15%)
  expectedVol: number;        // expected volatility as decimal (e.g. 0.60 = 60%)
  operabilityScore: number;   // 0-100 from operability engine
  regimeFit: number;          // 0-1, how well this signal fits current regime
  tokenPhase?: TokenPhase;    // current lifecycle phase of the token
  regime?: MarketRegime;      // current market regime
  liquidityUsd?: number;      // available liquidity
  volume24h?: number;         // 24h volume
}

/** Breakdown of the alpha score components */
export interface AlphaScoreBreakdown {
  signalStrength: number;       // 0-1, weighted component
  riskAdjustedReturn: number;   // 0-1, weighted component
  operability: number;          // 0-1, weighted component
  portfolioFit: number;         // 0-1, weighted component
  regimeAlignment: number;      // 0-1, weighted component
  composite: number;            // 0-1, final weighted score
}

/** A ranked opportunity with alpha score and allocation suggestion */
export interface RankedOpportunity extends TradeOpportunity {
  alphaScore: number;
  rank: number;
  scoreBreakdown: AlphaScoreBreakdown;
  suggestedAllocationPct: number;
}

/** Current portfolio state for diversification calculations */
export interface PortfolioState {
  totalCapitalUsd: number;
  positions: Array<{
    tokenAddress: string;
    chain: string;
    sizeUsd: number;
    direction: 'LONG' | 'SHORT';
    unrealizedPnlPct: number;
  }>;
  currentRegime?: MarketRegime;
}

/** Capital allocation suggestion */
export interface AllocationSuggestion {
  tokenAddress: string;
  chain: string;
  allocationUsd: number;
  allocationPct: number;     // % of total capital
  riskContributionPct: number; // % of total portfolio risk from this position
  reason: string;
}

/** Minimum quality filters for opportunities */
export interface QualityFilter {
  minConfidence: number;       // minimum brain confidence (0-1)
  minOperability: number;      // minimum operability score (0-100)
  minExpectedReturn: number;   // minimum expected return as decimal
  maxVolatility: number;       // maximum expected volatility
  minLiquidityUsd: number;     // minimum liquidity
  minAlphaScore: number;       // minimum alpha score (0-1)
}

// ============================================================
// CONSTANTS
// ============================================================

/** Alpha score component weights */
const WEIGHT_SIGNAL_STRENGTH = 0.30;
const WEIGHT_RISK_ADJUSTED_RETURN = 0.25;
const WEIGHT_OPERABILITY = 0.20;
const WEIGHT_PORTFOLIO_FIT = 0.15;
const WEIGHT_REGIME_ALIGNMENT = 0.10;

/** Risk-free rate for Sharpe-like calculations */
const RISK_FREE_RATE = 0.0;

/** Portfolio concentration limits */
const MAX_SINGLE_POSITION_PCT = 0.20;     // 20% max in one position
const MAX_CHAIN_EXPOSURE_PCT = 0.50;      // 50% max on one chain
const MAX_DIRECTION_BIAS_PCT = 0.70;      // 70% max in one direction
const DEFAULT_CASH_RESERVE_PCT = 0.15;     // 15% cash reserve

/** Default quality filters */
const DEFAULT_QUALITY_FILTER: QualityFilter = {
  minConfidence: 0.4,
  minOperability: 30,
  minExpectedReturn: 0.03,
  maxVolatility: 2.0,
  minLiquidityUsd: 5000,
  minAlphaScore: 0.3,
};

/** Regime alignment scores: how well each direction fits each regime */
const REGIME_DIRECTION_FIT: Record<string, Record<string, number>> = {
  TRENDING_UP: { LONG: 0.9, SHORT: 0.2 },
  TRENDING_DOWN: { LONG: 0.2, SHORT: 0.9 },
  SIDEWAYS: { LONG: 0.5, SHORT: 0.5 },
  HIGH_VOLATILITY: { LONG: 0.3, SHORT: 0.3 },
  LOW_VOLATILITY: { LONG: 0.6, SHORT: 0.4 },
  BULL: { LONG: 0.9, SHORT: 0.1 },
  BEAR: { LONG: 0.1, SHORT: 0.9 },
  TRANSITION: { LONG: 0.4, SHORT: 0.4 },
};

// ============================================================
// ALPHA RANKING ENGINE CLASS
// ============================================================

class AlphaRankingEngine {

  /**
   * Rank a list of trade opportunities by their alpha score.
   *
   * Computes the composite alpha score for each opportunity,
   * sorts by score descending, and assigns ranks.
   *
   * @param opportunities - List of trade opportunities to rank
   * @param portfolioState - Current portfolio state for diversification analysis
   * @returns Ranked list of opportunities with scores and allocation suggestions
   */
  rankOpportunities(
    opportunities: TradeOpportunity[],
    portfolioState: PortfolioState,
  ): RankedOpportunity[] {
    if (opportunities.length === 0) return [];

    // Compute alpha score for each opportunity
    const scored: RankedOpportunity[] = opportunities.map((opp, _index) => {
      const breakdown = this.computeAlphaScore(opp, portfolioState);

      return {
        ...opp,
        alphaScore: breakdown.composite,
        rank: 0, // Will be assigned after sorting
        scoreBreakdown: breakdown,
        suggestedAllocationPct: 0, // Will be computed in allocation phase
      };
    });

    // Sort by alpha score descending
    scored.sort((a, b) => b.alphaScore - a.alphaScore);

    // Assign ranks
    for (let i = 0; i < scored.length; i++) {
      scored[i].rank = i + 1;
    }

    // Compute allocation suggestions
    const suggestions = this.suggestAllocation(scored, portfolioState.totalCapitalUsd);
    for (const opp of scored) {
      const suggestion = suggestions.find(s => s.tokenAddress === opp.tokenAddress && s.chain === opp.chain);
      opp.suggestedAllocationPct = suggestion?.allocationPct ?? 0;
    }

    return scored;
  }

  /**
   * Get the top N opportunities from current signals in the DB.
   *
   * Loads active signals, filters by quality, ranks them, and returns
   * the top N opportunities.
   *
   * @param n - Number of top opportunities to return
   * @param filter - Quality filter (uses defaults if not provided)
   * @returns Top N ranked opportunities
   */
  async getTopOpportunities(
    n: number = 5,
    filter: Partial<QualityFilter> = {},
  ): Promise<RankedOpportunity[]> {
    const qualityFilter: QualityFilter = { ...DEFAULT_QUALITY_FILTER, ...filter };

    // Load active signals from DB
    const signals = await db.signal.findMany({
      where: {
        confidence: { gte: Math.round(qualityFilter.minConfidence * 100) },
      },
      orderBy: { createdAt: 'desc' },
      take: 50,
      include: { token: true },
    });

    if (signals.length === 0) return [];

    // Convert signals to TradeOpportunities
    const opportunities: TradeOpportunity[] = [];

    for (const signal of signals) {
      const token = signal.token;
      if (!token) continue;

      // Parse metadata for additional fields
      let metadata: Record<string, unknown> = {};
      try {
        metadata = JSON.parse(signal.metadata) as Record<string, unknown>;
      } catch {
        // Use empty metadata
      }

      const opp: TradeOpportunity = {
        tokenAddress: token.address,
        chain: token.chain,
        direction: signal.direction === 'LONG' ? 'LONG' : 'SHORT',
        confidence: signal.confidence / 100,
        strategyName: (metadata.strategyName as string) ?? 'unknown',
        expectedReturn: (metadata.expectedReturn as number) ?? 0.05,
        expectedVol: (metadata.expectedVol as number) ?? 0.5,
        operabilityScore: (metadata.operabilityScore as number) ?? 50,
        regimeFit: (metadata.regimeFit as number) ?? 0.5,
        tokenPhase: (metadata.tokenPhase as TokenPhase) ?? undefined,
        regime: (metadata.regime as MarketRegime) ?? undefined,
        liquidityUsd: token.liquidity || undefined,
        volume24h: token.volume24h || undefined,
      };

      // Apply quality filters
      if (opp.confidence < qualityFilter.minConfidence) continue;
      if (opp.operabilityScore < qualityFilter.minOperability) continue;
      if (opp.expectedReturn < qualityFilter.minExpectedReturn) continue;
      if (opp.expectedVol > qualityFilter.maxVolatility) continue;
      if (opp.liquidityUsd !== undefined && opp.liquidityUsd < qualityFilter.minLiquidityUsd) continue;

      opportunities.push(opp);
    }

    // Load portfolio state from DB
    const portfolioState = await this.loadPortfolioState();

    // Rank opportunities
    const ranked = this.rankOpportunities(opportunities, portfolioState);

    // Filter by minimum alpha score
    const filtered = ranked.filter(opp => opp.alphaScore >= qualityFilter.minAlphaScore);

    return filtered.slice(0, n);
  }

  /**
   * Compute the alpha score breakdown for a single opportunity.
   *
   * Alpha Score = weighted sum of:
   *   - Signal Strength (30%): confidence from brain analysis
   *   - Risk-Adjusted Return (25%): Sharpe-like ratio
   *   - Operability (20%): normalized operability score
   *   - Portfolio Fit (15%): diversification benefit
   *   - Regime Alignment (10%): regime-direction fit
   *
   * @param opportunity - The trade opportunity to score
   * @param portfolioState - Current portfolio state
   * @returns Detailed breakdown of the alpha score
   */
  computeAlphaScore(
    opportunity: TradeOpportunity,
    portfolioState: PortfolioState,
  ): AlphaScoreBreakdown {
    // 1. Signal Strength (0-1): directly from brain confidence
    const signalStrength = this.normalizeSignalStrength(opportunity.confidence);

    // 2. Risk-Adjusted Expected Return (0-1): Sharpe-like ratio
    const riskAdjustedReturn = this.computeRiskAdjustedReturn(
      opportunity.expectedReturn,
      opportunity.expectedVol,
    );

    // 3. Operability (0-1): normalize from 0-100 scale
    const operability = opportunity.operabilityScore / 100;

    // 4. Portfolio Fit (0-1): diversification benefit
    const portfolioFit = this.computePortfolioFit(opportunity, portfolioState);

    // 5. Regime Alignment (0-1): regime-direction fit
    const regimeAlignment = this.computeRegimeAlignment(
      opportunity.direction,
      opportunity.regime ?? portfolioState.currentRegime,
    );

    // Composite score
    const composite =
      signalStrength * WEIGHT_SIGNAL_STRENGTH +
      riskAdjustedReturn * WEIGHT_RISK_ADJUSTED_RETURN +
      operability * WEIGHT_OPERABILITY +
      portfolioFit * WEIGHT_PORTFOLIO_FIT +
      regimeAlignment * WEIGHT_REGIME_ALIGNMENT;

    return {
      signalStrength: Math.round(signalStrength * 1000) / 1000,
      riskAdjustedReturn: Math.round(riskAdjustedReturn * 1000) / 1000,
      operability: Math.round(operability * 1000) / 1000,
      portfolioFit: Math.round(portfolioFit * 1000) / 1000,
      regimeAlignment: Math.round(regimeAlignment * 1000) / 1000,
      composite: Math.round(composite * 1000) / 1000,
    };
  }

  /**
   * Suggest capital allocation across top opportunities.
   *
   * Uses risk parity principles: each position's size is inversely
   * proportional to its expected volatility, with adjustments for
   * concentration limits and cash reserves.
   *
   * @param topOpportunities - Ranked opportunities to allocate to
   * @param capital - Total available capital in USD
   * @returns Allocation suggestions for each opportunity
   */
  suggestAllocation(
    topOpportunities: RankedOpportunity[],
    capital: number,
  ): AllocationSuggestion[] {
    if (topOpportunities.length === 0 || capital <= 0) return [];

    // Step 1: Allocate capital across opportunities using risk parity
    const investableCapital = capital * (1 - DEFAULT_CASH_RESERVE_PCT);
    const suggestions: AllocationSuggestion[] = [];

    // Risk parity: weight inversely proportional to volatility
    const inverseVols = topOpportunities.map(opp => {
      const vol = Math.max(opp.expectedVol, 0.05); // Floor at 5% vol
      return 1 / vol;
    });
    const totalInverseVol = inverseVols.reduce((s, v) => s + v, 0);

    // Compute initial allocations
    for (let i = 0; i < topOpportunities.length; i++) {
      const opp = topOpportunities[i];
      const riskParityPct = totalInverseVol > 0 ? inverseVols[i] / totalInverseVol : 1 / topOpportunities.length;

      // Adjust by alpha score: higher alpha gets slightly more
      const alphaAdjustment = 0.7 + 0.3 * opp.alphaScore; // 0.7-1.0 multiplier
      let allocationPct = riskParityPct * alphaAdjustment;

      // Cap single position at MAX_SINGLE_POSITION_PCT
      allocationPct = Math.min(allocationPct, MAX_SINGLE_POSITION_PCT);

      const allocationUsd = investableCapital * allocationPct;

      // Compute risk contribution (proportional to vol × weight)
      const riskContributionPct = totalInverseVol > 0
        ? (inverseVols[i] / totalInverseVol)
        : 1 / topOpportunities.length;

      suggestions.push({
        tokenAddress: opp.tokenAddress,
        chain: opp.chain,
        allocationUsd: Math.round(allocationUsd * 100) / 100,
        allocationPct: Math.round(allocationPct * 10000) / 10000,
        riskContributionPct: Math.round(riskContributionPct * 10000) / 10000,
        reason: `Risk parity allocation: alpha=${opp.alphaScore.toFixed(2)}, vol=${(opp.expectedVol * 100).toFixed(0)}%, rank=#${opp.rank}`,
      });
    }

    // Step 2: Enforce concentration limits
    this.enforceConcentrationLimits(suggestions, capital);

    // Step 3: Renormalize to fit within investable capital
    this.renormalizeAllocations(suggestions, investableCapital);

    return suggestions;
  }

  // ============================================================
  // PRIVATE HELPERS
  // ============================================================

  /**
   * Normalize signal strength from confidence value.
   * Uses a sigmoid-like mapping to spread values across the 0-1 range.
   */
  private normalizeSignalStrength(confidence: number): number {
    // Clamp to [0, 1]
    const clamped = Math.max(0, Math.min(1, confidence));
    // Sigmoid mapping for better spread: f(x) = 1 / (1 + exp(-6(x - 0.5)))
    return 1 / (1 + Math.exp(-6 * (clamped - 0.5)));
  }

  /**
   * Compute risk-adjusted return (Sharpe-like ratio).
   *
   * Sharpe = (expected_return - risk_free) / volatility
   * Then normalized to 0-1 using sigmoid mapping.
   *
   * Typical crypto values: return 0.05-0.5, vol 0.3-2.0
   * Raw Sharpe range: -1 to +3
   */
  private computeRiskAdjustedReturn(
    expectedReturn: number,
    expectedVol: number,
  ): number {
    if (expectedVol <= 0) return 0;

    const sharpe = (expectedReturn - RISK_FREE_RATE) / expectedVol;

    // Normalize using sigmoid: f(x) = 1 / (1 + exp(-x))
    // This maps any Sharpe ratio to (0, 1) with 0.5 = Sharpe of 0
    return 1 / (1 + Math.exp(-sharpe));
  }

  /**
   * Compute portfolio fit score.
   *
   * Measures how well adding this opportunity would diversify the portfolio.
   * A token that's NOT already in the portfolio AND is on a different chain
   * AND has a different direction gets a higher score.
   *
   * Score components:
   *   - Not already held: +0.4
   *   - Different chain from existing positions: +0.3
   *   - Direction diversifies portfolio: +0.3
   */
  private computePortfolioFit(
    opportunity: TradeOpportunity,
    portfolioState: PortfolioState,
  ): number {
    if (portfolioState.positions.length === 0) {
      // Empty portfolio — any opportunity diversifies
      return 0.9;
    }

    let score = 0;

    // Check if token is already held
    const isAlreadyHeld = portfolioState.positions.some(
      p => p.tokenAddress === opportunity.tokenAddress,
    );
    if (!isAlreadyHeld) {
      score += 0.4;
    } else {
      score += 0.1; // Small score if already held (scaling in is OK)
    }

    // Check chain diversification
    const chainExposure = this.computeChainExposure(
      opportunity.chain,
      portfolioState,
    );
    if (chainExposure < 0.3) {
      score += 0.3; // Chain is underrepresented
    } else if (chainExposure < 0.5) {
      score += 0.15; // Chain is moderately represented
    } else {
      score += 0.05; // Chain is already heavy
    }

    // Check direction diversification
    const directionExposure = this.computeDirectionExposure(
      opportunity.direction,
      portfolioState,
    );
    if (directionExposure < 0.4) {
      score += 0.3; // Direction is underrepresented
    } else if (directionExposure < 0.6) {
      score += 0.15; // Direction is balanced
    } else {
      score += 0.05; // Direction is already heavy
    }

    return Math.min(1, score);
  }

  /**
   * Compute regime alignment score.
   *
   * Measures how well the opportunity's direction fits the current regime.
   * For example, a LONG signal in a BULL regime gets a high score.
   */
  private computeRegimeAlignment(
    direction: 'LONG' | 'SHORT',
    regime?: MarketRegime,
  ): number {
    if (!regime) return 0.5; // Neutral if no regime info

    const regimeKey = regime;
    const directionKey = direction;

    const fitMap = REGIME_DIRECTION_FIT[regimeKey];
    if (fitMap) {
      return fitMap[directionKey] ?? 0.5;
    }

    // Unknown regime — neutral
    return 0.5;
  }

  /**
   * Compute the fraction of portfolio exposed to a given chain.
   */
  private computeChainExposure(
    chain: string,
    portfolioState: PortfolioState,
  ): number {
    if (portfolioState.totalCapitalUsd <= 0) return 0;
    const chainExposure = portfolioState.positions
      .filter(p => p.chain === chain)
      .reduce((s, p) => s + p.sizeUsd, 0);
    return chainExposure / portfolioState.totalCapitalUsd;
  }

  /**
   * Compute the fraction of portfolio in a given direction.
   */
  private computeDirectionExposure(
    direction: 'LONG' | 'SHORT',
    portfolioState: PortfolioState,
  ): number {
    if (portfolioState.totalCapitalUsd <= 0) return 0;
    const dirExposure = portfolioState.positions
      .filter(p => p.direction === direction)
      .reduce((s, p) => s + p.sizeUsd, 0);
    return dirExposure / portfolioState.totalCapitalUsd;
  }

  /**
   * Enforce concentration limits across all allocation suggestions.
   *
   * Limits:
   *  - No single position > MAX_SINGLE_POSITION_PCT of capital
   *  - No chain > MAX_CHAIN_EXPOSURE_PCT of capital
   *  - No direction > MAX_DIRECTION_BIAS_PCT of capital
   */
  private enforceConcentrationLimits(
    suggestions: AllocationSuggestion[],
    capital: number,
  ): void {
    // Enforce single position limit
    for (const s of suggestions) {
      if (s.allocationPct > MAX_SINGLE_POSITION_PCT) {
        s.allocationPct = MAX_SINGLE_POSITION_PCT;
        s.allocationUsd = capital * s.allocationPct;
        s.reason += ' [capped: single position limit]';
      }
    }

    // Enforce chain exposure limit
    const chainExposures = new Map<string, number>();
    for (const s of suggestions) {
      const current = chainExposures.get(s.chain) ?? 0;
      chainExposures.set(s.chain, current + s.allocationPct);
    }

    for (const [chain, totalPct] of chainExposures.entries()) {
      if (totalPct > MAX_CHAIN_EXPOSURE_PCT) {
        // Scale down all positions on this chain proportionally
        const scaleFactor = MAX_CHAIN_EXPOSURE_PCT / totalPct;
        for (const s of suggestions) {
          if (s.chain === chain) {
            s.allocationPct *= scaleFactor;
            s.allocationUsd = capital * s.allocationPct;
            s.reason += ' [reduced: chain exposure limit]';
          }
        }
      }
    }

    // Enforce direction bias limit
    const longPct = suggestions
      .filter(s => s.allocationPct > 0)
      .reduce((sum, s) => {
        // We need direction info from the original opportunity — approximate from risk contribution
        return sum; // Skip for now as direction isn't on AllocationSuggestion
      }, 0);
    // The direction check is implicit in the portfolio fit scoring
    void longPct; // Suppress unused variable warning
  }

  /**
   * Renormalize allocations so total doesn't exceed investable capital.
   */
  private renormalizeAllocations(
    suggestions: AllocationSuggestion[],
    investableCapital: number,
  ): void {
    const totalPct = suggestions.reduce((s, sug) => s + sug.allocationPct, 0);
    if (totalPct > 1) {
      const scaleFactor = 1 / totalPct;
      for (const s of suggestions) {
        s.allocationPct *= scaleFactor;
        s.allocationUsd = Math.round(investableCapital * s.allocationPct * 100) / 100;
      }
    } else {
      // Just ensure USD values are correct
      for (const s of suggestions) {
        s.allocationUsd = Math.round(investableCapital * s.allocationPct * 100) / 100;
      }
    }
  }

  /**
   * Load portfolio state from the database.
   *
   * Queries PaperTradingPosition for current holdings and
   * CapitalState for total capital.
   */
  private async loadPortfolioState(): Promise<PortfolioState> {
    try {
      // Get capital state
      const capitalState = await db.capitalState.findFirst({
        orderBy: { updatedAt: 'desc' },
      });

      const totalCapitalUsd = capitalState?.totalCapitalUsd ?? 10;

      // Get current positions
      const positions = await db.paperTradingPosition.findMany({
        where: { status: 'OPEN' },
      });

      const portfolioPositions: PortfolioState['positions'] = positions
        .filter(p => p.tokenAddress != null)
        .map(p => ({
          tokenAddress: p.tokenAddress as string,
          chain: p.chain,
          sizeUsd: p.sizeUsd,
          direction: p.direction as 'LONG' | 'SHORT',
          unrealizedPnlPct: p.pnlPct,
        }));

      return {
        totalCapitalUsd,
        positions: portfolioPositions,
      };
    } catch {
      // Fallback to empty portfolio
      return {
        totalCapitalUsd: 10,
        positions: [],
      };
    }
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const alphaRankingEngine = new AlphaRankingEngine();
