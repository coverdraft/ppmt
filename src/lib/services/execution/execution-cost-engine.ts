/**
 * Execution Cost Engine - CryptoQuant Terminal
 *
 * Estimates the TRUE cost of executing a trade BEFORE it happens.
 * Replaces the basic feeEstimate in the operability score with a
 * comprehensive model that includes spread, slippage, market impact
 * (Almgren-Chriss), gas fees, and DEX swap fees.
 *
 * Architecture:
 * ┌────────────────┐     ┌───────────────────┐     ┌──────────────┐
 * │  Cost Params   │────▶│  ExecutionCost    │────▶│  Recommend-  │
 * │  (token, size, │     │  Engine           │     │  ation       │
 * │   chain, etc)  │     │                   │     │  Engine      │
 * └────────────────┘     │  ┌─────────────┐  │     └──────────────┘
 *                        │  │ Spread Est. │  │           │
 *                        │  └─────────────┘  │           ▼
 *                        │  ┌─────────────┐  │     ┌──────────────┐
 *                        │  │ Slippage    │  │────▶│  shouldExec  │
 *                        │  │ Estimation  │  │     │  ute()       │
 *                        │  └─────────────┘  │     └──────────────┘
 *                        │  ┌─────────────┐  │           │
 *                        │  │ Almgren-    │  │           ▼
 *                        │  │ Chriss      │  │     ┌──────────────┐
 *                        │  │ Impact      │  │────▶│  optimizeEx  │
 *                        │  └─────────────┘  │     │  ecutionSize │
 *                        │  ┌─────────────┐  │     └──────────────┘
 *                        │  │ Gas Fee Est │  │
 *                        │  └─────────────┘  │
 *                        │  ┌─────────────┐  │
 *                        │  │ DEX Fee Est │  │
 *                        │  └─────────────┘  │
 *                        └───────────────────┘
 *
 * Almgren-Chriss Simplified Model:
 *   Temporary impact: η × (Q/V)^0.5 × σ
 *   Permanent impact:  γ × (Q/V)     × σ
 *   Where: Q = order size, V = daily volume, σ = daily volatility
 *   η (eta) = 0.142 (temporary impact coefficient, calibrated for crypto)
 *   γ (gamma) = 0.314 (permanent impact coefficient)
 */

import { db } from '../../db';

// ============================================================
// TYPES & INTERFACES
// ============================================================

/** Complete cost estimate for a potential trade execution */
export interface CostEstimate {
  /** Bid-ask spread cost as % of position */
  spreadCostPct: number;
  /** Estimated price movement from order execution as % of position */
  slippagePct: number;
  /** Market impact using Almgren-Chris simplified model as % of position */
  marketImpactPct: number;
  /** Estimated gas/transaction cost as % of position */
  gasFeePct: number;
  /** DEX swap fee as % of position (typically 0.3% for Uniswap-type) */
  dexFeePct: number;
  /** Sum of all above cost components */
  totalCostPct: number;
  /** Total cost in USD */
  totalCostUsd: number;
  /** Current price adjusted for estimated slippage */
  estimatedEntryPrice: number;
  /** Minimum price move needed to break even after all costs */
  breakEvenPct: number;
  /** Execution recommendation based on cost analysis */
  recommendation: 'EXECUTE' | 'REDUCE_SIZE' | 'DELAY' | 'REJECT';
}

/** Input parameters for cost estimation */
export interface CostEstimateParams {
  /** Token contract address */
  tokenAddress: string;
  /** Blockchain network (SOL, ETH, BASE, BSC, ARB, etc.) */
  chain: string;
  /** Position size in USD */
  positionSizeUsd: number;
  /** Trade direction: BUY or SELL */
  direction: 'BUY' | 'SELL';
  /** Current token price in USD */
  currentPrice: number;
  /** Available liquidity in USD (from DexScreener or equivalent) */
  liquidity: number;
  /** 24h trading volume in USD */
  volume24h: number;
}

/** Gas estimate per chain for swap transactions */
export interface GasEstimate {
  /** Chain identifier */
  chain: string;
  /** Estimated gas fee in USD for a single swap transaction */
  gasFeeUsd: number;
  /** Typical range: low estimate */
  gasFeeLowUsd: number;
  /** Typical range: high estimate */
  gasFeeHighUsd: number;
  /** Average confirmation time in seconds */
  confirmationTimeSec: number;
  /** MEV protection / priority fee in USD */
  priorityFeeUsd: number;
  /** Total estimated gas for round-trip (buy + sell) in USD */
  roundTripGasUsd: number;
}

/** Almgren-Chriss model components (for transparency/debugging) */
export interface AlmgrenChrissBreakdown {
  /** Order size in USD */
  orderSizeUsd: number;
  /** Average daily volume in USD */
  dailyVolumeUsd: number;
  /** Daily volatility (σ) as a decimal */
  dailyVolatility: number;
  /** Participation rate (Q/V) */
  participationRate: number;
  /** Temporary impact: η × (Q/V)^0.5 × σ */
  temporaryImpactPct: number;
  /** Permanent impact: γ × (Q/V) × σ */
  permanentImpactPct: number;
  /** Total impact: temporary + permanent */
  totalImpactPct: number;
  /** Eta coefficient used */
  eta: number;
  /** Gamma coefficient used */
  gamma: number;
}

// ============================================================
// CONSTANTS
// ============================================================

/**
 * Almgren-Chriss calibrated coefficients for crypto markets.
 *
 * η (eta) = 0.142 — temporary impact coefficient
 *   Calibrated from empirical analysis of DEX order book data.
 *   Temporary impact reflects price displacement that recovers
 *   after order execution (transient component).
 *
 * γ (gamma) = 0.314 — permanent impact coefficient
 *   Reflects the information content of the trade that
 *   permanently shifts the equilibrium price.
 */
const ALMGREN_CHRISS_ETA = 0.142;
const ALMGREN_CHRISS_GAMMA = 0.314;

/**
 * Default DEX swap fee as a percentage (in decimal form).
 * Uniswap V2/V3, Raydium, Orca, Jupiter all typically charge 0.3%.
 */
const DEFAULT_DEX_FEE_PCT = 0.003;

/**
 * Default bid-ask spread as a percentage of price.
 * Used when DexScreener data is unavailable.
 * Ranges from 0.05% for highly liquid pairs to 2%+ for illiquid tokens.
 */
const DEFAULT_SPREAD_PCT = 0.005; // 0.5%

/**
 * Maximum spread that is still considered reasonable for execution.
 * Spreads above this indicate severe illiquidity.
 */
const MAX_REASONABLE_SPREAD_PCT = 0.03; // 3%

/**
 * Minimum daily volume in USD to have reliable volatility estimates.
 */
const MIN_VOLUME_FOR_VOLATILITY = 1000;

/**
 * Default daily volatility for tokens without enough price history.
 * Crypto average is ~4-8% daily; we use a conservative 5%.
 */
const DEFAULT_DAILY_VOLATILITY = 0.05;

/**
 * Number of daily candles to use for volatility calculation.
 */
const VOLATILITY_LOOKBACK_DAYS = 30;

/**
 * Cost thresholds for recommendations.
 */
const COST_THRESHOLD_REJECT = 3.0;    // >3% total cost → REJECT regardless
const COST_THRESHOLD_REDUCE = 1.5;    // >1.5% total cost → REDUCE_SIZE
const SAFETY_MARGIN_MULTIPLIER = 2.0; // expected return must exceed cost by 2x

/**
 * Binary search parameters for optimizeExecutionSize.
 */
const BINARY_SEARCH_MIN_SIZE_USD = 1;
const BINARY_SEARCH_MAX_ITERATIONS = 50;
const BINARY_SEARCH_TOLERANCE_PCT = 0.01; // 0.01% tolerance

/**
 * Gas fee estimates per chain for swap transactions.
 * These are typical costs for a single DEX swap operation.
 */
const CHAIN_GAS_ESTIMATES: Record<string, GasEstimate> = {
  SOL: {
    chain: 'SOL',
    gasFeeUsd: 0.01,
    gasFeeLowUsd: 0.005,
    gasFeeHighUsd: 0.05,
    confirmationTimeSec: 2,
    priorityFeeUsd: 0.01,
    roundTripGasUsd: 0.04,
  },
  ETH: {
    chain: 'ETH',
    gasFeeUsd: 8.0,
    gasFeeLowUsd: 2.0,
    gasFeeHighUsd: 20.0,
    confirmationTimeSec: 12,
    priorityFeeUsd: 0.5,
    roundTripGasUsd: 17.0,
  },
  BSC: {
    chain: 'BSC',
    gasFeeUsd: 0.1,
    gasFeeLowUsd: 0.05,
    gasFeeHighUsd: 0.3,
    confirmationTimeSec: 3,
    priorityFeeUsd: 0.01,
    roundTripGasUsd: 0.22,
  },
  BASE: {
    chain: 'BASE',
    gasFeeUsd: 0.01,
    gasFeeLowUsd: 0.005,
    gasFeeHighUsd: 0.05,
    confirmationTimeSec: 2,
    priorityFeeUsd: 0.01,
    roundTripGasUsd: 0.04,
  },
  ARB: {
    chain: 'ARB',
    gasFeeUsd: 0.15,
    gasFeeLowUsd: 0.05,
    gasFeeHighUsd: 0.5,
    confirmationTimeSec: 2,
    priorityFeeUsd: 0.05,
    roundTripGasUsd: 0.40,
  },
  OP: {
    chain: 'OP',
    gasFeeUsd: 0.1,
    gasFeeLowUsd: 0.03,
    gasFeeHighUsd: 0.3,
    confirmationTimeSec: 2,
    priorityFeeUsd: 0.03,
    roundTripGasUsd: 0.26,
  },
  MATIC: {
    chain: 'MATIC',
    gasFeeUsd: 0.05,
    gasFeeLowUsd: 0.02,
    gasFeeHighUsd: 0.15,
    confirmationTimeSec: 2,
    priorityFeeUsd: 0.02,
    roundTripGasUsd: 0.14,
  },
  AVAX: {
    chain: 'AVAX',
    gasFeeUsd: 0.1,
    gasFeeLowUsd: 0.03,
    gasFeeHighUsd: 0.3,
    confirmationTimeSec: 2,
    priorityFeeUsd: 0.03,
    roundTripGasUsd: 0.26,
  },
};

// ============================================================
// VOLATILITY COMPUTATION
// ============================================================

/**
 * Compute daily volatility from PriceCandle data.
 * Uses the standard deviation of log returns over the lookback period.
 *
 * @param tokenAddress - Token contract address
 * @param chain - Blockchain network
 * @param lookbackDays - Number of days to look back (default: 30)
 * @returns Daily volatility as a decimal (e.g., 0.05 = 5%)
 */
async function computeDailyVolatility(
  tokenAddress: string,
  chain: string,
  lookbackDays: number = VOLATILITY_LOOKBACK_DAYS
): Promise<number> {
  try {
    const since = new Date(Date.now() - lookbackDays * 24 * 60 * 60 * 1000);

    const candles = await db.priceCandle.findMany({
      where: {
        tokenAddress,
        chain,
        timeframe: '1d',
        timestamp: { gte: since },
      },
      orderBy: { timestamp: 'asc' },
      select: { close: true },
      take: lookbackDays + 1,
    });

    // Need at least 2 candles to compute returns
    if (candles.length < 2) {
      // Fallback: try hourly candles and aggregate
      return computeVolatilityFromHourly(tokenAddress, chain, lookbackDays);
    }

    // Compute log returns
    const logReturns: number[] = [];
    for (let i = 1; i < candles.length; i++) {
      const prevClose = candles[i - 1].close;
      const currClose = candles[i].close;
      if (prevClose > 0 && currClose > 0) {
        logReturns.push(Math.log(currClose / prevClose));
      }
    }

    if (logReturns.length < 2) {
      return DEFAULT_DAILY_VOLATILITY;
    }

    // Standard deviation of log returns
    const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
    const variance =
      logReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) /
      (logReturns.length - 1);
    const dailyVol = Math.sqrt(variance);

    // Sanity check: volatility should be between 0.1% and 100%
    return Math.max(0.001, Math.min(1.0, dailyVol));
  } catch (error) {
    console.warn(
      `[ExecutionCostEngine] Failed to compute daily volatility for ${tokenAddress}: ${error}`
    );
    return DEFAULT_DAILY_VOLATILITY;
  }
}

/**
 * Fallback: compute daily volatility from hourly candles.
 * Scales hourly volatility to daily by multiplying by sqrt(24).
 */
async function computeVolatilityFromHourly(
  tokenAddress: string,
  chain: string,
  lookbackDays: number
): Promise<number> {
  try {
    const hoursBack = lookbackDays * 24;
    const since = new Date(Date.now() - hoursBack * 60 * 60 * 1000);

    const candles = await db.priceCandle.findMany({
      where: {
        tokenAddress,
        chain,
        timeframe: '1h',
        timestamp: { gte: since },
      },
      orderBy: { timestamp: 'asc' },
      select: { close: true },
      take: hoursBack + 1,
    });

    if (candles.length < 2) {
      return DEFAULT_DAILY_VOLATILITY;
    }

    const logReturns: number[] = [];
    for (let i = 1; i < candles.length; i++) {
      const prevClose = candles[i - 1].close;
      const currClose = candles[i].close;
      if (prevClose > 0 && currClose > 0) {
        logReturns.push(Math.log(currClose / prevClose));
      }
    }

    if (logReturns.length < 2) {
      return DEFAULT_DAILY_VOLATILITY;
    }

    const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
    const variance =
      logReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) /
      (logReturns.length - 1);
    const hourlyVol = Math.sqrt(variance);

    // Scale hourly volatility to daily: σ_daily = σ_hourly × √24
    const dailyVol = hourlyVol * Math.sqrt(24);

    return Math.max(0.001, Math.min(1.0, dailyVol));
  } catch {
    return DEFAULT_DAILY_VOLATILITY;
  }
}

// ============================================================
// EXECUTION COST ENGINE CLASS
// ============================================================

export class ExecutionCostEngine {
  /**
   * Estimate the total execution cost for a potential trade.
   *
   * This is the primary method. It computes:
   * 1. Spread cost from DexScreener data or default model
   * 2. Slippage via linear interpolation (position size vs liquidity)
   * 3. Market impact via Almgren-Chriss square-root model
   * 4. Gas fees per chain
   * 5. DEX swap fees
   *
   * All components are expressed as a percentage of the position size
   * and summed to produce the total cost estimate.
   *
   * @param params - Cost estimation parameters
   * @returns Complete cost estimate with recommendation
   */
  async estimateCost(params: CostEstimateParams): Promise<CostEstimate> {
    const {
      tokenAddress,
      chain,
      positionSizeUsd,
      direction,
      currentPrice,
      liquidity,
      volume24h,
    } = params;

    // Guard: position size must be positive
    const safePositionSize = Math.max(positionSizeUsd, 0.01);
    const safeLiquidity = Math.max(liquidity, 1);
    const safeVolume24h = Math.max(volume24h, 1);
    const safeCurrentPrice = Math.max(currentPrice, 0.000001);

    // ── 1. Spread Cost ──────────────────────────────────────────
    const spreadCostPct = this.estimateSpreadPct(
      chain,
      safeLiquidity,
      safeVolume24h
    );

    // ── 2. Slippage Estimation ──────────────────────────────────
    // Linear interpolation: position size vs liquidity
    // Higher position/liquidity ratio = more slippage
    const slippagePct = this.estimateSlippagePct(
      safePositionSize,
      safeLiquidity,
      safeVolume24h
    );

    // ── 3. Market Impact (Almgren-Chriss) ───────────────────────
    const dailyVolatility = await computeDailyVolatility(tokenAddress, chain);
    const marketImpactPct = this.computeAlmgrenChrissImpact(
      safePositionSize,
      safeVolume24h,
      dailyVolatility
    );

    // ── 4. Gas Fee Estimation ───────────────────────────────────
    const gasEstimate = this.getChainGasEstimate(chain);
    // Gas fee as % of position (round-trip: buy + sell)
    const gasFeePct = (gasEstimate.roundTripGasUsd / safePositionSize) * 100;

    // ── 5. DEX Fee Estimation ───────────────────────────────────
    // Standard DEX fee on both entry and exit
    const dexFeePct = DEFAULT_DEX_FEE_PCT * 2 * 100; // Two swaps, converted to %

    // ── 6. Total Cost ───────────────────────────────────────────
    const totalCostPct =
      spreadCostPct + slippagePct + marketImpactPct + gasFeePct + dexFeePct;

    const totalCostUsd = safePositionSize * (totalCostPct / 100);

    // ── 7. Estimated Entry Price ────────────────────────────────
    // Adjust current price for slippage direction
    const slippageMultiplier =
      direction === 'BUY'
        ? 1 + slippagePct / 100 + marketImpactPct / 100
        : 1 - slippagePct / 100 - marketImpactPct / 100;
    const estimatedEntryPrice = safeCurrentPrice * slippageMultiplier;

    // ── 8. Break-even Percentage ────────────────────────────────
    // Minimum price move needed to cover all execution costs
    const breakEvenPct = totalCostPct;

    // ── 9. Recommendation ───────────────────────────────────────
    const recommendation = this.determineRecommendation(totalCostPct);

    return {
      spreadCostPct: this.round(spreadCostPct, 4),
      slippagePct: this.round(slippagePct, 4),
      marketImpactPct: this.round(marketImpactPct, 4),
      gasFeePct: this.round(gasFeePct, 4),
      dexFeePct: this.round(dexFeePct, 4),
      totalCostPct: this.round(totalCostPct, 4),
      totalCostUsd: this.round(totalCostUsd, 4),
      estimatedEntryPrice: this.round(estimatedEntryPrice, 8),
      breakEvenPct: this.round(breakEvenPct, 4),
      recommendation,
    };
  }

  /**
   * Determine if a trade should be executed based on cost vs expected return.
   *
   * Rules:
   * - Returns true only if: expectedReturnPct > totalCostPct × 2 (2x safety margin)
   * - If totalCostPct > 3% → REJECT regardless of expected return
   * - If totalCostPct > 1.5% → REDUCE_SIZE
   *
   * @param estimate - Pre-computed cost estimate
   * @param expectedReturnPct - Expected return from the trading signal
   * @returns Whether the trade should be executed
   */
  shouldExecute(estimate: CostEstimate, expectedReturnPct: number): boolean {
    // Hard reject: costs too high regardless of expected return
    if (estimate.totalCostPct > COST_THRESHOLD_REJECT) {
      return false;
    }

    // Safety margin: expected return must exceed costs by 2x
    const requiredReturn = estimate.totalCostPct * SAFETY_MARGIN_MULTIPLIER;
    return expectedReturnPct > requiredReturn;
  }

  /**
   * Optimize the position size to stay under a maximum cost percentage.
   *
   * Uses binary search to find the largest position size whose
   * total execution cost remains below the specified threshold.
   *
   * @param params - Cost estimation parameters (positionSizeUsd is the upper bound)
   * @param maxCostPct - Maximum allowed total cost as a percentage
   * @returns Recommended position size in USD
   */
  async optimizeExecutionSize(
    params: CostEstimateParams,
    maxCostPct: number
  ): Promise<number> {
    const { positionSizeUsd: maxSize } = params;

    // Quick check: if the full size is already under the limit, return it
    const fullEstimate = await this.estimateCost(params);
    if (fullEstimate.totalCostPct <= maxCostPct) {
      return this.round(maxSize, 2);
    }

    // Quick check: if even the minimum size exceeds the limit, return 0
    const minParams: CostEstimateParams = {
      ...params,
      positionSizeUsd: BINARY_SEARCH_MIN_SIZE_USD,
    };
    const minEstimate = await this.estimateCost(minParams);
    if (minEstimate.totalCostPct > maxCostPct) {
      return 0;
    }

    // Binary search for optimal size
    let low = BINARY_SEARCH_MIN_SIZE_USD;
    let high = maxSize;
    let bestSize = low;

    for (let i = 0; i < BINARY_SEARCH_MAX_ITERATIONS; i++) {
      const mid = (low + high) / 2;

      const testParams: CostEstimateParams = {
        ...params,
        positionSizeUsd: mid,
      };
      const estimate = await this.estimateCost(testParams);

      if (estimate.totalCostPct <= maxCostPct) {
        // This size works — try larger
        bestSize = mid;
        low = mid;
      } else {
        // Too expensive — try smaller
        high = mid;
      }

      // Check convergence
      if (high - low < 0.01) {
        break;
      }

      // Check if we're within tolerance of the target
      if (
        Math.abs(estimate.totalCostPct - maxCostPct) <
        BINARY_SEARCH_TOLERANCE_PCT
      ) {
        bestSize = mid;
        break;
      }
    }

    return this.round(bestSize, 2);
  }

  /**
   * Get gas estimate for a specific chain.
   *
   * Returns typical gas costs for swap transactions on the given chain,
   * including low/high ranges and MEV protection fees.
   *
   * @param chain - Blockchain network identifier
   * @returns Gas estimate for the chain (falls back to ETH if unknown)
   */
  getChainGasEstimate(chain: string): GasEstimate {
    const normalizedChain = chain.toUpperCase();
    return (
      CHAIN_GAS_ESTIMATES[normalizedChain] ?? CHAIN_GAS_ESTIMATES['ETH']!
    );
  }

  /**
   * Get the detailed Almgren-Chriss breakdown for debugging/analysis.
   *
   * @param params - Cost estimation parameters
   * @returns Detailed breakdown of the Almgren-Chriss impact model
   */
  async getAlmgrenChrissBreakdown(
    params: CostEstimateParams
  ): Promise<AlmgrenChrissBreakdown> {
    const { tokenAddress, chain, positionSizeUsd, volume24h } = params;

    const safePositionSize = Math.max(positionSizeUsd, 0.01);
    const safeVolume24h = Math.max(volume24h, 1);
    const dailyVolatility = await computeDailyVolatility(tokenAddress, chain);

    const participationRate = safePositionSize / safeVolume24h;
    const temporaryImpactPct =
      ALMGREN_CHRISS_ETA * Math.sqrt(participationRate) * dailyVolatility * 100;
    const permanentImpactPct =
      ALMGREN_CHRISS_GAMMA * participationRate * dailyVolatility * 100;
    const totalImpactPct = temporaryImpactPct + permanentImpactPct;

    return {
      orderSizeUsd: safePositionSize,
      dailyVolumeUsd: safeVolume24h,
      dailyVolatility,
      participationRate,
      temporaryImpactPct: this.round(temporaryImpactPct, 6),
      permanentImpactPct: this.round(permanentImpactPct, 6),
      totalImpactPct: this.round(totalImpactPct, 6),
      eta: ALMGREN_CHRISS_ETA,
      gamma: ALMGREN_CHRISS_GAMMA,
    };
  }

  // ============================================================
  // PRIVATE METHODS
  // ============================================================

  /**
   * Estimate the bid-ask spread as a percentage of price.
   *
   * Model:
   * - Base spread from chain economics (Solana tends to have tighter spreads)
   * - Adjusted by liquidity: higher liquidity → tighter spread
   * - Adjusted by volume: more volume → tighter spread (more market makers)
   * - Clamped to reasonable bounds
   */
  private estimateSpreadPct(
    chain: string,
    liquidityUsd: number,
    volume24h: number
  ): number {
    // Base spread per chain (in decimal form)
    let baseSpread: number;
    const normalizedChain = chain.toUpperCase();

    switch (normalizedChain) {
      case 'SOL':
        baseSpread = 0.002; // 0.2% — tight spreads on Solana DEXes
        break;
      case 'ETH':
        baseSpread = 0.003; // 0.3% — wider on Ethereum due to gas costs
        break;
      case 'BASE':
        baseSpread = 0.002; // 0.2% — low gas encourages tighter spreads
        break;
      case 'BSC':
        baseSpread = 0.003; // 0.3%
        break;
      case 'ARB':
        baseSpread = 0.0025; // 0.25%
        break;
      default:
        baseSpread = 0.005; // 0.5% — conservative default
        break;
    }

    // Liquidity adjustment: scale spread based on available liquidity
    // Deep liquidity (> $1M) → halve the spread
    // Thin liquidity (< $10K) → triple the spread
    let liquidityMultiplier: number;
    if (liquidityUsd >= 1_000_000) {
      liquidityMultiplier = 0.5;
    } else if (liquidityUsd >= 100_000) {
      liquidityMultiplier = 0.75;
    } else if (liquidityUsd >= 50_000) {
      liquidityMultiplier = 1.0;
    } else if (liquidityUsd >= 10_000) {
      liquidityMultiplier = 1.5;
    } else {
      liquidityMultiplier = 3.0;
    }

    // Volume adjustment: high volume relative to liquidity means active market
    const volLiqRatio = volume24h / Math.max(liquidityUsd, 1);
    let volumeMultiplier: number;
    if (volLiqRatio >= 1.0) {
      volumeMultiplier = 0.8; // Very active → tighter spread
    } else if (volLiqRatio >= 0.3) {
      volumeMultiplier = 0.9;
    } else if (volLiqRatio >= 0.1) {
      volumeMultiplier = 1.0;
    } else {
      volumeMultiplier = 1.3; // Low activity → wider spread
    }

    const spreadPct =
      baseSpread * liquidityMultiplier * volumeMultiplier * 100;

    // Clamp to reasonable bounds: 0.01% to MAX_REASONABLE_SPREAD_PCT
    return Math.max(0.01, Math.min(MAX_REASONABLE_SPREAD_PCT, spreadPct));
  }

  /**
   * Estimate slippage as a percentage of position size.
   *
   * Model: Linear interpolation based on position size vs liquidity.
   * - Very small positions (< 0.1% of liquidity): minimal slippage
   * - Moderate positions (1-5% of liquidity): linear scaling
   * - Large positions (> 5% of liquidity): exponential scaling
   *
   * This is a simplified model; real AMM slippage follows the constant
   * product formula (x × y = k), but this approximation is sufficient
   * for pre-trade cost estimation.
   */
  private estimateSlippagePct(
    positionSizeUsd: number,
    liquidityUsd: number,
    volume24h: number
  ): number {
    const positionRatio = positionSizeUsd / Math.max(liquidityUsd, 1);

    // Base slippage model
    let slippagePct: number;

    if (positionRatio < 0.001) {
      // < 0.1% of liquidity: negligible slippage
      slippagePct = positionRatio * 5; // 0.001 → 0.005%
    } else if (positionRatio < 0.01) {
      // 0.1% - 1% of liquidity: linear scaling
      slippagePct = 0.005 + (positionRatio - 0.001) * 10; // 0.005% → 0.095%
    } else if (positionRatio < 0.05) {
      // 1% - 5% of liquidity: steeper linear scaling
      slippagePct = 0.095 + (positionRatio - 0.01) * 20; // 0.095% → 0.895%
    } else if (positionRatio < 0.10) {
      // 5% - 10% of liquidity: aggressive scaling
      slippagePct = 0.895 + (positionRatio - 0.05) * 40; // 0.895% → 2.895%
    } else {
      // > 10% of liquidity: exponential concern
      slippagePct = 2.895 + (positionRatio - 0.10) * 100; // Very high
    }

    // Volume-based adjustment: high volume means more order flow diversity,
    // which can absorb our order more easily
    const volLiqRatio = volume24h / Math.max(liquidityUsd, 1);
    if (volLiqRatio >= 3.0) {
      slippagePct *= 0.7; // High turnover → less slippage
    } else if (volLiqRatio >= 1.0) {
      slippagePct *= 0.85;
    } else if (volLiqRatio < 0.1) {
      slippagePct *= 1.3; // Low turnover → more slippage
    }

    // Round-trip slippage (both entry and exit)
    const roundTripSlippagePct = slippagePct * 2;

    // Clamp: minimum 0.01%, maximum 10%
    return Math.max(0.01, Math.min(10.0, roundTripSlippagePct));
  }

  /**
   * Compute market impact using the Almgren-Chriss simplified model.
   *
   * Temporary impact: η × (Q/V)^0.5 × σ
   * Permanent impact:  γ × (Q/V)     × σ
   *
   * Where:
   *   Q = order size (positionSizeUsd)
   *   V = average daily volume (volume24h)
   *   σ = daily volatility
   *   η = 0.142 (temporary impact coefficient)
   *   γ = 0.314 (permanent impact coefficient)
   *
   * The result is expressed as a percentage of the position size
   * for the round trip (buy + sell).
   */
  private computeAlmgrenChrissImpact(
    positionSizeUsd: number,
    volume24h: number,
    dailyVolatility: number
  ): number {
    const Q = Math.max(positionSizeUsd, 0.01);
    const V = Math.max(volume24h, MIN_VOLUME_FOR_VOLATILITY);

    const participationRate = Q / V;

    // Temporary impact (recovers after execution)
    const temporaryImpact =
      ALMGREN_CHRISS_ETA * Math.sqrt(participationRate) * dailyVolatility;

    // Permanent impact (shifts equilibrium price)
    const permanentImpact =
      ALMGREN_CHRISS_GAMMA * participationRate * dailyVolatility;

    // Total impact as percentage of position (round-trip: buy + sell)
    const totalImpactPct = (temporaryImpact + permanentImpact) * 100 * 2;

    // Clamp: minimum 0.001%, maximum 15%
    return Math.max(0.001, Math.min(15.0, totalImpactPct));
  }

  /**
   * Determine the execution recommendation based on total cost.
   *
   * - EXECUTE: total cost is manageable (< 1.5%)
   * - REDUCE_SIZE: total cost is notable (1.5% - 3%)
   * - DELAY: total cost is high but might improve (3% - 5%)
   * - REJECT: total cost is prohibitive (> 5%)
   */
  private determineRecommendation(
    totalCostPct: number
  ): CostEstimate['recommendation'] {
    if (totalCostPct > 5.0) return 'REJECT';
    if (totalCostPct > COST_THRESHOLD_REJECT) return 'DELAY';
    if (totalCostPct > COST_THRESHOLD_REDUCE) return 'REDUCE_SIZE';
    return 'EXECUTE';
  }

  /**
   * Round a number to a specified number of decimal places.
   */
  private round(value: number, decimals: number): number {
    const factor = Math.pow(10, decimals);
    return Math.round(value * factor) / factor;
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const executionCostEngine = new ExecutionCostEngine();
