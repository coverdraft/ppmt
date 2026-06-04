/**
 * Monte Carlo Simulator — CryptoQuant Terminal
 *
 * Monte Carlo simulation engine for assessing the robustness of a trading
 * system's backtest results. By reshuffling the order of trades many times
 * and recomputing equity curves, we estimate the distribution of possible
 * outcomes and derive confidence intervals for key risk metrics.
 *
 * Core idea:
 *   The sequence of wins and losses in a backtest is just ONE realisation.
 *   If the same trades occurred in a different order the equity curve — and
 *   therefore the max drawdown, Sharpe ratio, etc. — could be very different.
 *   Monte Carlo resampling reveals how sensitive the results are to trade
 *   ordering, which is critical for position-sizing and risk-of-ruin analysis.
 *
 * Design choices:
 *   - Uses a **seeded Linear Congruential Generator (LCG)** instead of
 *     Math.random(), ensuring reproducible results for a given seed.
 *   - Processes one simulation at a time, keeping only aggregate statistics
 *     (not N full equity curves) in memory — efficient for 1 000+ runs.
 *   - All exported types are fully typed with no `any`.
 *
 * References:
 *   - Van Tharp, "Trade Your Way to Financial Freedom" (Monte Carlo chapter)
 *   - Ralph Vince, "The Leverage Space Trading Model"
 */

// ============================================================
// 1. SEEDED PRNG — Linear Congruential Generator
// ============================================================

/**
 * Parameters for the LCG follow the Numerical Recipes convention,
 * which provides a full-period generator for any 32-bit seed.
 *
 *   X_{n+1} = (a * X_n + c) mod m
 *
 * Period: 2^32 ≈ 4.3 billion values.
 */
const LCG_A = 1_664_525;
const LCG_C = 1_013_904_223;
const LCG_M = 2 ** 32;

/** Stateful PRNG that yields reproducible pseudo-random numbers. */
class SeededPRNG {
  private state: number;

  constructor(seed: number) {
    // Ensure the seed is a positive 32-bit integer
    this.state = seed >>> 0;
  }

  /** Return a pseudo-random integer in [0, 2^32). */
  nextInt(): number {
    this.state = ((LCG_A * this.state + LCG_C) >>> 0) % LCG_M;
    return this.state;
  }

  /** Return a pseudo-random float in [0, 1). */
  next(): number {
    return this.nextInt() / LCG_M;
  }

  /** Return a pseudo-random integer in [0, n). */
  nextIntN(n: number): number {
    return Math.floor(this.next() * n);
  }
}

// ============================================================
// 2. FISHER-YATES SHUFFLE (in-place, using seeded PRNG)
// ============================================================

/**
 * Shuffle an array in-place using the Fisher-Yates algorithm with the
 * provided PRNG. This guarantees an unbiased permutation as long as the
 * PRNG produces uniformly distributed values.
 */
function fisherYatesShuffle<T>(arr: T[], rng: SeededPRNG): void {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = rng.nextIntN(i + 1);
    // Swap elements at indices i and j
    const tmp = arr[i];
    arr[i] = arr[j];
    arr[j] = tmp;
  }
}

// ============================================================
// 3. TYPES & INTERFACES
// ============================================================

/** Configuration for a Monte Carlo simulation run. */
export interface MonteCarloConfig {
  /** Number of simulations to run (default: 1000). More = tighter confidence intervals. */
  simulations: number;
  /** Seed for the PRNG (default: 42). Same seed + same trades = same results. */
  seed: number;
  /** Starting capital for each simulation (default: 10 000). */
  initialCapital: number;
  /** Confidence levels to compute, as percentiles (default: [5, 25, 50, 75, 95]). */
  confidenceLevels: number[];
  /**
   * Equity fraction below which the system is considered "ruined".
   * E.g. 0.5 means ruin = equity drops below 50% of initial capital.
   * (default: 0.5)
   */
  ruinThreshold: number;
}

/** Metrics computed for a single simulation path. */
export interface SimulationPathMetrics {
  /** Final equity after all trades are applied. */
  finalEquity: number;
  /** Maximum peak-to-trough drawdown as a fraction (0–1). */
  maxDrawdown: number;
  /** Annualised Sharpe ratio (assuming 252 trading days, risk-free = 0). */
  sharpeRatio: number;
  /** Fraction of trades that were profitable. */
  winRate: number;
  /** Gross profit / gross loss; Infinity if no losses. */
  profitFactor: number;
  /** Whether equity ever fell below ruinThreshold × initialCapital. */
  hitRuin: boolean;
}

/** Confidence interval for a single metric. */
export interface ConfidenceInterval {
  level: number; // e.g. 5, 25, 50, 75, 95
  value: number;
}

/** Full result of a Monte Carlo simulation run. */
export interface MonteCarloResult {
  /** The configuration used for this run. */
  config: MonteCarloConfig;
  /** Number of input trades. */
  tradeCount: number;
  /** Timestamp when the simulation was generated. */
  generatedAt: Date;

  // --- Equity confidence intervals ---
  equityPercentiles: ConfidenceInterval[];

  // --- Max drawdown confidence intervals (as fraction 0–1) ---
  drawdownPercentiles: ConfidenceInterval[];

  // --- Sharpe ratio confidence intervals ---
  sharpePercentiles: ConfidenceInterval[];

  // --- Win rate confidence intervals ---
  winRatePercentiles: ConfidenceInterval[];

  // --- Profit factor confidence intervals ---
  profitFactorPercentiles: ConfidenceInterval[];

  // --- Key risk metrics ---
  /** Probability of profit: fraction of sims where finalEquity > initialCapital. */
  probabilityOfProfit: number;
  /** Risk of ruin: fraction of sims where equity breached the ruin threshold. */
  riskOfRuin: number;
  /** P95 max drawdown — the drawdown that is exceeded only 5% of the time. */
  p95MaxDrawdown: number;
  /** Mean final equity across all simulations. */
  meanFinalEquity: number;
  /** Median final equity (P50). */
  medianFinalEquity: number;
  /** Standard deviation of final equity. */
  stdDevFinalEquity: number;

  // --- Original (unshuffled) path metrics for comparison ---
  originalMetrics: SimulationPathMetrics;
}

/** Input for the compound growth projection. */
export interface CompoundGrowthConfig {
  /** Mean return per period (as fraction, e.g. 0.02 = 2%). */
  meanReturn: number;
  /** Standard deviation of returns per period (as fraction). */
  stdDevReturn: number;
  /** Number of periods to project forward. */
  periods: number;
  /** Number of simulation paths (default: 1000). */
  simulations: number;
  /** Starting capital (default: 10 000). */
  initialCapital: number;
  /** Seed for the PRNG (default: 42). */
  seed: number;
  /** Confidence levels (default: [5, 25, 50, 75, 95]). */
  confidenceLevels: number[];
}

/** Result of a compound growth projection. */
export interface CompoundGrowthResult {
  config: CompoundGrowthConfig;
  /** Equity at each period for each confidence level. Each entry: { period, values } */
  equityPaths: Array<{
    level: number;
    equity: number[]; // one value per period
  }>;
  /** Probability of ending above initial capital. */
  probabilityOfProfit: number;
  /** Risk of ruin (equity < ruinThreshold × initialCapital at any point). */
  riskOfRuin: number;
  /** Ruin threshold used (defaults to 0.5). */
  ruinThreshold: number;
  generatedAt: Date;
}

// ============================================================
// 4. INTERNAL METRICS HELPERS
// ============================================================

/**
 * Compute max drawdown from an equity curve.
 * Drawdown is expressed as a fraction of the peak (0–1).
 *
 * Algorithm: track running peak; drawdown = (peak - equity) / peak.
 */
function computeMaxDrawdown(equityCurve: number[]): number {
  let peak = equityCurve[0];
  let maxDD = 0;

  for (let i = 1; i < equityCurve.length; i++) {
    if (equityCurve[i] > peak) {
      peak = equityCurve[i];
    }
    const dd = peak > 0 ? (peak - equityCurve[i]) / peak : 0;
    if (dd > maxDD) {
      maxDD = dd;
    }
  }

  return maxDD;
}

/**
 * Compute Sharpe ratio from a series of periodic returns.
 *
 * Sharpe = (meanReturn - riskFreeRate) / stdDevReturn
 * We assume riskFreeRate = 0 and use sample std deviation (n-1).
 * If std dev is 0, Sharpe is 0 (not Infinity, to avoid skewing averages).
 */
function computeSharpeRatio(returns: number[]): number {
  if (returns.length < 2) return 0;

  const n = returns.length;
  const mean = returns.reduce((s, r) => s + r, 0) / n;
  const variance =
    returns.reduce((s, r) => s + (r - mean) ** 2, 0) / (n - 1);
  const stdDev = Math.sqrt(variance);

  if (stdDev < 1e-12) return 0;
  return mean / stdDev;
}

/**
 * Compute profit factor = grossProfit / grossLoss.
 * Returns Infinity if there are no losses.
 */
function computeProfitFactor(returns: number[]): number {
  let grossProfit = 0;
  let grossLoss = 0;

  for (const r of returns) {
    if (r > 0) {
      grossProfit += r;
    } else if (r < 0) {
      grossLoss += Math.abs(r);
    }
  }

  if (grossLoss < 1e-12) return grossProfit > 0 ? Infinity : 0;
  return grossProfit / grossLoss;
}

/**
 * Compute the percentile value from a sorted array.
 * Uses linear interpolation (same as numpy's default).
 */
function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  if (sorted.length === 1) return sorted[0];

  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  const frac = idx - lo;

  if (lo === hi) return sorted[lo];
  return sorted[lo] * (1 - frac) + sorted[hi] * frac;
}

/**
 * Build percentile confidence intervals from a raw array of values.
 */
function buildConfidenceIntervals(
  values: number[],
  levels: number[],
): ConfidenceInterval[] {
  // Sort a copy to avoid mutating the input
  const sorted = [...values].sort((a, b) => a - b);
  return levels.map((level) => ({
    level,
    value: percentile(sorted, level),
  }));
}

// ============================================================
// 5. SIMULATION PATH COMPUTATION
// ============================================================

/**
 * Run a single simulation path: shuffle the trades, compute the equity
 * curve, and return the key metrics.
 *
 * This function mutates `tradeCopy` (shuffles it in-place) but does NOT
 * retain the equity curve — only the scalar metrics are returned, keeping
 * memory usage O(1) per simulation regardless of trade count.
 */
function runSinglePath(
  tradeCopy: number[],   // PnL % per trade (as fractions, e.g. 0.05 = +5%)
  rng: SeededPRNG,
  initialCapital: number,
  ruinThreshold: number,
): SimulationPathMetrics {
  // 1. Shuffle the trade order
  fisherYatesShuffle(tradeCopy, rng);

  // 2. Build equity curve and compute metrics in a single pass
  let equity = initialCapital;
  let peak = initialCapital;
  let maxDD = 0;
  let hitRuin = false;
  let wins = 0;
  let grossProfit = 0;
  let grossLoss = 0;

  // We need returns for Sharpe; store them compactly
  // Each return is the PnL % applied to the capital at the start of that trade
  const returns: number[] = new Array(tradeCopy.length);

  const ruinLevel = initialCapital * ruinThreshold;

  for (let i = 0; i < tradeCopy.length; i++) {
    const pnlPct = tradeCopy[i]; // e.g. 0.05 for +5%

    // Track the return relative to current equity
    returns[i] = pnlPct;

    // Apply compound growth: new equity = equity * (1 + pnlPct)
    equity = equity * (1 + pnlPct);

    // Ensure equity doesn't go negative (can happen with large losses)
    if (equity < 0) equity = 0;

    // Update peak and max drawdown
    if (equity > peak) peak = equity;
    const dd = peak > 0 ? (peak - equity) / peak : 0;
    if (dd > maxDD) maxDD = dd;

    // Check ruin
    if (equity <= ruinLevel) hitRuin = true;

    // Win/loss tracking
    if (pnlPct > 0) {
      wins++;
      grossProfit += pnlPct;
    } else if (pnlPct < 0) {
      grossLoss += Math.abs(pnlPct);
    }
  }

  const winRate = tradeCopy.length > 0 ? wins / tradeCopy.length : 0;
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

  return {
    finalEquity: equity,
    maxDrawdown: maxDD,
    sharpeRatio: computeSharpeRatio(returns),
    winRate,
    profitFactor,
    hitRuin,
  };
}

// ============================================================
// 6. MONTE CARLO SIMULATOR CLASS
// ============================================================

export class MonteCarloSimulator {
  /**
   * Run a full Monte Carlo simulation on a set of backtest trades.
   *
   * @param tradesPnlPct — Array of PnL % per trade as fractions (e.g. 0.05 = +5%, -0.03 = -3%)
   * @param config       — Simulation configuration
   * @returns MonteCarloResult with confidence intervals and risk metrics
   */
  simulate(tradesPnlPct: number[], config?: Partial<MonteCarloConfig>): MonteCarloResult {
    // --- Merge config with defaults ---
    const fullConfig: MonteCarloConfig = {
      simulations: config?.simulations ?? 1000,
      seed: config?.seed ?? 42,
      initialCapital: config?.initialCapital ?? 10_000,
      confidenceLevels: config?.confidenceLevels ?? [5, 25, 50, 75, 95],
      ruinThreshold: config?.ruinThreshold ?? 0.5,
    };

    const n = tradesPnlPct.length;

    // Edge case: no trades
    if (n === 0) {
      const emptyMetrics: SimulationPathMetrics = {
        finalEquity: fullConfig.initialCapital,
        maxDrawdown: 0,
        sharpeRatio: 0,
        winRate: 0,
        profitFactor: 0,
        hitRuin: false,
      };
      return {
        config: fullConfig,
        tradeCount: 0,
        generatedAt: new Date(),
        equityPercentiles: fullConfig.confidenceLevels.map((l) => ({ level: l, value: fullConfig.initialCapital })),
        drawdownPercentiles: fullConfig.confidenceLevels.map((l) => ({ level: l, value: 0 })),
        sharpePercentiles: fullConfig.confidenceLevels.map((l) => ({ level: l, value: 0 })),
        winRatePercentiles: fullConfig.confidenceLevels.map((l) => ({ level: l, value: 0 })),
        profitFactorPercentiles: fullConfig.confidenceLevels.map((l) => ({ level: l, value: 0 })),
        probabilityOfProfit: 0,
        riskOfRuin: 0,
        p95MaxDrawdown: 0,
        meanFinalEquity: fullConfig.initialCapital,
        medianFinalEquity: fullConfig.initialCapital,
        stdDevFinalEquity: 0,
        originalMetrics: emptyMetrics,
      };
    }

    // --- Compute original (unshuffled) path metrics ---
    const originalEquityCurve = this.buildEquityCurve(tradesPnlPct, fullConfig.initialCapital);
    const originalReturns = [...tradesPnlPct]; // PnL % are the returns
    const originalMetrics: SimulationPathMetrics = {
      finalEquity: originalEquityCurve[originalEquityCurve.length - 1],
      maxDrawdown: computeMaxDrawdown(originalEquityCurve),
      sharpeRatio: computeSharpeRatio(originalReturns),
      winRate: tradesPnlPct.filter((r) => r > 0).length / n,
      profitFactor: computeProfitFactor(originalReturns),
      hitRuin: originalEquityCurve.some((e) => e <= fullConfig.initialCapital * fullConfig.ruinThreshold),
    };

    // --- Run N simulations ---
    // Accumulate metrics in flat arrays for memory efficiency.
    // We do NOT store full equity curves for each simulation.
    const finalEquities: number[] = new Array(fullConfig.simulations);
    const maxDrawdowns: number[] = new Array(fullConfig.simulations);
    const sharpeRatios: number[] = new Array(fullConfig.simulations);
    const winRates: number[] = new Array(fullConfig.simulations);
    const profitFactors: number[] = new Array(fullConfig.simulations);
    let profitCount = 0;
    let ruinCount = 0;

    const rng = new SeededPRNG(fullConfig.seed);

    for (let sim = 0; sim < fullConfig.simulations; sim++) {
      // Create a fresh copy of trades for this simulation
      const tradeCopy = [...tradesPnlPct];

      const metrics = runSinglePath(
        tradeCopy,
        rng,
        fullConfig.initialCapital,
        fullConfig.ruinThreshold,
      );

      finalEquities[sim] = metrics.finalEquity;
      maxDrawdowns[sim] = metrics.maxDrawdown;
      sharpeRatios[sim] = metrics.sharpeRatio;
      winRates[sim] = metrics.winRate;
      profitFactors[sim] = metrics.profitFactor;

      if (metrics.finalEquity > fullConfig.initialCapital) profitCount++;
      if (metrics.hitRuin) ruinCount++;
    }

    // --- Compute aggregate statistics ---
    const meanFinalEquity =
      finalEquities.reduce((s, e) => s + e, 0) / fullConfig.simulations;
    const variance =
      finalEquities.reduce((s, e) => s + (e - meanFinalEquity) ** 2, 0) /
      (fullConfig.simulations - 1);
    const stdDevFinalEquity = Math.sqrt(Math.max(variance, 0));

    // --- Build confidence intervals ---
    const equityPercentiles = buildConfidenceIntervals(finalEquities, fullConfig.confidenceLevels);
    const drawdownPercentiles = buildConfidenceIntervals(maxDrawdowns, fullConfig.confidenceLevels);
    const sharpePercentiles = buildConfidenceIntervals(sharpeRatios, fullConfig.confidenceLevels);
    const winRatePercentiles = buildConfidenceIntervals(winRates, fullConfig.confidenceLevels);

    // For profit factor, replace Infinity with a sentinel for sorting
    const pfSorted = [...profitFactors].sort((a, b) => {
      if (a === Infinity && b === Infinity) return 0;
      if (a === Infinity) return 1;
      if (b === Infinity) return -1;
      return a - b;
    });
    const profitFactorPercentiles = fullConfig.confidenceLevels.map((level) => ({
      level,
      value: percentile(pfSorted, level),
    }));

    // P95 max drawdown: the drawdown exceeded only 5% of the time
    // This is the 95th percentile of the drawdown distribution
    const p95MaxDrawdown = percentile(
      [...maxDrawdowns].sort((a, b) => a - b),
      95,
    );

    // Median final equity
    const medianFinalEquity = percentile(
      [...finalEquities].sort((a, b) => a - b),
      50,
    );

    return {
      config: fullConfig,
      tradeCount: n,
      generatedAt: new Date(),
      equityPercentiles,
      drawdownPercentiles,
      sharpePercentiles,
      winRatePercentiles,
      profitFactorPercentiles,
      probabilityOfProfit: profitCount / fullConfig.simulations,
      riskOfRuin: ruinCount / fullConfig.simulations,
      p95MaxDrawdown,
      meanFinalEquity,
      medianFinalEquity,
      stdDevFinalEquity,
      originalMetrics,
    };
  }

  /**
   * Project forward equity growth using a normal distribution of returns.
   *
   * Unlike `simulate()` which reshuffles historical trades, this method
   * generates random returns from N(mean, stdDev) for each period and
   * applies compound growth. Useful for "what if" forward projections.
   *
   * Uses the Box-Muller transform to convert uniform PRNG outputs to
   * normally-distributed values.
   *
   * @param config — Compound growth projection configuration
   * @returns CompoundGrowthResult with equity paths at each confidence level
   */
  simulateCompoundGrowth(config: CompoundGrowthConfig): CompoundGrowthResult {
    const fullConfig: CompoundGrowthConfig = {
      simulations: config.simulations ?? 1000,
      initialCapital: config.initialCapital ?? 10_000,
      seed: config.seed ?? 42,
      confidenceLevels: config.confidenceLevels ?? [5, 25, 50, 75, 95],
      meanReturn: config.meanReturn,
      stdDevReturn: config.stdDevReturn,
      periods: config.periods,
    };

    const ruinThreshold = 0.5;
    const { periods, simulations } = fullConfig;

    // For each period, we collect the equity across all simulation paths,
    // then compute percentiles. This avoids storing all paths in memory.
    // Instead, we store equity-per-period for all sims, then compute
    // percentiles period-by-period.

    // equityByPeriod[t][sim] = equity at period t for simulation sim
    // To save memory, we process period-by-period, keeping only the
    // running equity for each simulation path.

    // Initialize: all sims start at initialCapital
    let currentEquities = new Array(simulations).fill(fullConfig.initialCapital);

    // For ruin and profit tracking
    let everHitRuin = new Array(simulations).fill(false);

    // Result structure: one equity array per confidence level
    const equityPaths: Array<{ level: number; equity: number[] }> =
      fullConfig.confidenceLevels.map((level) => ({
        level,
        equity: [fullConfig.initialCapital], // period 0
      }));

    const rng = new SeededPRNG(fullConfig.seed);

    for (let t = 0; t < periods; t++) {
      const nextEquities = new Array(simulations);

      for (let sim = 0; sim < simulations; sim++) {
        // Box-Muller transform for normal distribution
        const u1 = Math.max(rng.next(), 1e-10); // avoid log(0)
        const u2 = rng.next();
        const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);

        // Generate return from N(mean, stdDev)
        const ret = fullConfig.meanReturn + fullConfig.stdDevReturn * z;

        // Apply compound growth
        nextEquities[sim] = Math.max(currentEquities[sim] * (1 + ret), 0);

        // Track ruin
        if (nextEquities[sim] <= fullConfig.initialCapital * ruinThreshold) {
          everHitRuin[sim] = true;
        }
      }

      currentEquities = nextEquities;

      // Compute percentiles for this period
      const sorted = [...currentEquities].sort((a, b) => a - b);
      for (let i = 0; i < fullConfig.confidenceLevels.length; i++) {
        const level = fullConfig.confidenceLevels[i];
        equityPaths[i].equity.push(percentile(sorted, level));
      }
    }

    // Final statistics
    const finalEquities = currentEquities;
    const profitCount = finalEquities.filter((e) => e > fullConfig.initialCapital).length;
    const ruinCount = everHitRuin.filter(Boolean).length;

    return {
      config: fullConfig,
      equityPaths,
      probabilityOfProfit: profitCount / simulations,
      riskOfRuin: ruinCount / simulations,
      ruinThreshold,
      generatedAt: new Date(),
    };
  }

  /**
   * Build the full equity curve from a sequence of PnL % values.
   * Useful for the original (unshuffled) path comparison.
   *
   * Returns an array where element 0 = initialCapital and element i
   * = equity after applying trades 0..i-1 with compound growth.
   */
  buildEquityCurve(tradesPnlPct: number[], initialCapital: number): number[] {
    const curve = new Array(tradesPnlPct.length + 1);
    curve[0] = initialCapital;

    for (let i = 0; i < tradesPnlPct.length; i++) {
      curve[i + 1] = Math.max(curve[i] * (1 + tradesPnlPct[i]), 0);
    }

    return curve;
  }

  /**
   * Generate a human-readable summary of the Monte Carlo simulation results.
   */
  generateSummary(result: MonteCarloResult): string {
    const lines: string[] = [];
    const c = result.config;

    lines.push('╔════════════════════════════════════════════════════════════════╗');
    lines.push('║          MONTE CARLO SIMULATION REPORT                        ║');
    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push(`║  Trades:       ${String(result.tradeCount).padEnd(47)}║`);
    lines.push(`║  Simulations:  ${String(c.simulations).padEnd(47)}║`);
    lines.push(`║  Seed:         ${String(c.seed).padEnd(47)}║`);
    lines.push(`║  Initial Capital: $${c.initialCapital.toFixed(2).padEnd(42)}║`);
    lines.push(`║  Ruin Threshold: ${(c.ruinThreshold * 100).toFixed(0)}%${' '.repeat(44)}║`);
    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  EQUITY CONFIDENCE INTERVALS                                  ║');

    for (const ci of result.equityPercentiles) {
      const label = `P${ci.level}`;
      lines.push(`║  ${label.padEnd(6)}: $${ci.value.toFixed(2).padStart(12)}${' '.repeat(30)}║`);
    }

    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  DRAWDOWN CONFIDENCE INTERVALS                                ║');

    for (const ci of result.drawdownPercentiles) {
      const label = `P${ci.level}`;
      lines.push(`║  ${label.padEnd(6)}: ${(ci.value * 100).toFixed(2).padStart(8)}%${' '.repeat(34)}║`);
    }

    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  KEY RISK METRICS                                             ║');
    lines.push(`║  Probability of Profit: ${(result.probabilityOfProfit * 100).toFixed(1).padStart(7)}%${' '.repeat(35)}║`);
    lines.push(`║  Risk of Ruin:          ${(result.riskOfRuin * 100).toFixed(1).padStart(7)}%${' '.repeat(35)}║`);
    lines.push(`║  P95 Max Drawdown:      ${(result.p95MaxDrawdown * 100).toFixed(1).padStart(7)}%${' '.repeat(35)}║`);
    lines.push(`║  Mean Final Equity:     $${result.meanFinalEquity.toFixed(2).padStart(12)}${' '.repeat(27)}║`);
    lines.push(`║  Median Final Equity:   $${result.medianFinalEquity.toFixed(2).padStart(12)}${' '.repeat(27)}║`);
    lines.push(`║  StdDev Final Equity:   $${result.stdDevFinalEquity.toFixed(2).padStart(12)}${' '.repeat(27)}║`);
    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  ORIGINAL (UNSHUFFLED) PATH                                   ║');
    lines.push(`║  Final Equity:    $${result.originalMetrics.finalEquity.toFixed(2).padStart(12)}${' '.repeat(27)}║`);
    lines.push(`║  Max Drawdown:    ${(result.originalMetrics.maxDrawdown * 100).toFixed(1).padStart(7)}%${' '.repeat(35)}║`);
    lines.push(`║  Sharpe Ratio:    ${result.originalMetrics.sharpeRatio.toFixed(3).padStart(10)}${' '.repeat(33)}║`);
    lines.push(`║  Win Rate:        ${(result.originalMetrics.winRate * 100).toFixed(1).padStart(7)}%${' '.repeat(35)}║`);
    lines.push(`║  Profit Factor:   ${result.originalMetrics.profitFactor === Infinity ? '      INF' : result.originalMetrics.profitFactor.toFixed(2).padStart(10)}${' '.repeat(33)}║`);
    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  INTERPRETATION GUIDE                                         ║');
    lines.push('║  Risk of Ruin < 1%:  Excellent — system is very safe          ║');
    lines.push('║  Risk of Ruin 1-5%:  Good — acceptable for most traders      ║');
    lines.push('║  Risk of Ruin 5-10%: Marginal — consider reducing position   ║');
    lines.push('║  Risk of Ruin > 10%: Dangerous — high chance of blow-up      ║');
    lines.push('║  P95 Drawdown: worst drawdown expected 95% of the time        ║');
    lines.push('║  Wide equity CI: results depend heavily on trade sequencing   ║');
    lines.push('╚════════════════════════════════════════════════════════════════╝');

    return lines.join('\n');
  }
}

// ============================================================
// 7. SINGLETON EXPORT
// ============================================================

export const monteCarloSimulator = new MonteCarloSimulator();
