/**
 * Strategy Correlation Service — CryptoQuant Terminal
 *
 * Computes rolling correlation between strategy returns for portfolio-level
 * risk management. Used by the SDE to adjust capital allocation when strategies
 * are highly correlated (reducing diversification benefit).
 *
 * This is DIFFERENT from cross-correlation-engine.ts which handles
 * signal-level correlation (trader + pattern + phase).
 *
 * Key features:
 * - Pearson correlation coefficient for pairwise correlations
 * - 30-day rolling window
 * - 5-minute cache for correlation matrix
 * - Handles edge cases: < 2 strategies, < 5 data points, zero variance
 * - Correlation limit from RiskBudget.maxCorrelatedPct (default 40%)
 */

import { db } from '@/lib/db';

// ============================================================
// TYPES
// ============================================================

export interface StrategyReturn {
  strategyId: string;
  date: string; // ISO date string (YYYY-MM-DD)
  dailyReturnPct: number;
}

export interface CorrelationMatrix {
  strategies: string[];
  matrix: number[][]; // NxN correlation matrix
  computedAt: Date;
  dataPoints: number;
}

export interface CorrelationCheckResult {
  allowed: boolean;
  avgCorrelation: number;
  maxPairwise: number;
}

// ============================================================
// STRATEGY CORRELATION SERVICE
// ============================================================

class StrategyCorrelationService {
  private cachedMatrix: CorrelationMatrix | null = null;
  private matrixCacheTime: number = 0;
  private readonly CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

  // ============================================================
  // COMPUTE CORRELATION MATRIX
  // ============================================================

  /**
   * Compute Pearson correlation matrix from strategy returns.
   * Returns an NxN matrix where matrix[i][j] = correlation(strategy_i, strategy_j).
   */
  computeCorrelationMatrix(returns: StrategyReturn[]): CorrelationMatrix {
    // Group returns by strategy
    const strategyMap = new Map<string, Map<string, number>>();
    for (const r of returns) {
      if (!strategyMap.has(r.strategyId)) {
        strategyMap.set(r.strategyId, new Map());
      }
      strategyMap.get(r.strategyId)!.set(r.date, r.dailyReturnPct);
    }

    const strategies = Array.from(strategyMap.keys());

    // Edge case: < 2 strategies
    if (strategies.length < 2) {
      return {
        strategies,
        matrix: strategies.length === 1 ? [[1]] : [],
        computedAt: new Date(),
        dataPoints: returns.length,
      };
    }

    // Find common dates across all strategies
    const dateSets = strategies.map(s => new Set(strategyMap.get(s)!.keys()));
    const commonDates = Array.from(dateSets[0]).filter(d =>
      dateSets.every(ds => ds.has(d))
    ).sort();

    // Edge case: < 5 data points
    if (commonDates.length < 5) {
      // Return identity matrix with NaN off-diagonal
      const n = strategies.length;
      const matrix: number[][] = [];
      for (let i = 0; i < n; i++) {
        matrix[i] = [];
        for (let j = 0; j < n; j++) {
          matrix[i][j] = i === j ? 1 : 0;
        }
      }
      return { strategies, matrix, computedAt: new Date(), dataPoints: commonDates.length };
    }

    // Build return series for each strategy
    const series: number[][] = strategies.map(s => {
      const sMap = strategyMap.get(s)!;
      return commonDates.map(d => sMap.get(d) ?? 0);
    });

    // Compute Pearson correlation for each pair
    const n = strategies.length;
    const matrix: number[][] = [];

    for (let i = 0; i < n; i++) {
      matrix[i] = [];
      for (let j = 0; j < n; j++) {
        if (i === j) {
          matrix[i][j] = 1;
        } else if (i > j) {
          // Symmetric — reuse already computed value
          matrix[i][j] = matrix[j][i];
        } else {
          matrix[i][j] = pearsonCorrelation(series[i], series[j]);
        }
      }
    }

    return {
      strategies,
      matrix,
      computedAt: new Date(),
      dataPoints: commonDates.length,
    };
  }

  // ============================================================
  // AVERAGE PAIRWISE CORRELATION
  // ============================================================

  /**
   * Get average pairwise correlation for a set of strategies.
   * Only considers off-diagonal pairs (i < j).
   */
  getAverageCorrelation(matrix: CorrelationMatrix): number {
    const n = matrix.strategies.length;
    if (n < 2) return 0;

    let sum = 0;
    let count = 0;

    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const val = matrix.matrix[i]?.[j];
        if (val !== undefined && !isNaN(val)) {
          sum += val;
          count++;
        }
      }
    }

    return count > 0 ? sum / count : 0;
  }

  // ============================================================
  // CORRELATION LIMIT CHECK
  // ============================================================

  /**
   * Check if adding a new strategy would increase portfolio correlation too much.
   *
   * @param existingStrategies - IDs of existing strategies already in portfolio
   * @param newStrategyId - ID of the strategy being considered
   * @param maxCorrelatedPct - Maximum average correlation % from RiskBudget (e.g. 40 = 0.40 correlation)
   * @param matrix - Current correlation matrix
   */
  wouldExceedCorrelationLimit(
    existingStrategies: string[],
    newStrategyId: string,
    maxCorrelatedPct: number,
    matrix: CorrelationMatrix,
  ): CorrelationCheckResult {
    // If no existing strategies or matrix is too small, always allow
    if (matrix.strategies.length < 2) {
      return { allowed: true, avgCorrelation: 0, maxPairwise: 0 };
    }

    // Find the new strategy in the matrix
    const newIdx = matrix.strategies.indexOf(newStrategyId);
    if (newIdx === -1) {
      // Strategy not in matrix — no correlation data, allow with caution
      return { allowed: true, avgCorrelation: 0, maxPairwise: 0 };
    }

    // Get correlations between the new strategy and existing strategies
    const existingIndices = existingStrategies
      .map(sId => matrix.strategies.indexOf(sId))
      .filter(idx => idx !== -1);

    if (existingIndices.length === 0) {
      // No overlap with existing strategies in the matrix
      return { allowed: true, avgCorrelation: 0, maxPairwise: 0 };
    }

    // Compute pairwise correlations with the new strategy
    const pairwiseCorrs: number[] = [];
    for (const idx of existingIndices) {
      const corr = matrix.matrix[newIdx]?.[idx];
      if (corr !== undefined && !isNaN(corr)) {
        pairwiseCorrs.push(corr);
      }
    }

    if (pairwiseCorrs.length === 0) {
      return { allowed: true, avgCorrelation: 0, maxPairwise: 0 };
    }

    const avgCorrelation = pairwiseCorrs.reduce((s, v) => s + v, 0) / pairwiseCorrs.length;
    const maxPairwise = pairwiseCorrs.reduce((max, v) => Math.max(max, v), -Infinity);

    // Limit check: avg correlation should not exceed maxCorrelatedPct / 100
    const limit = maxCorrelatedPct / 100;
    const allowed = avgCorrelation <= limit;

    return {
      allowed,
      avgCorrelation: Math.round(avgCorrelation * 10000) / 10000,
      maxPairwise: Math.round(maxPairwise * 10000) / 10000,
    };
  }

  // ============================================================
  // BUILD STRATEGY RETURNS FROM DB
  // ============================================================

  /**
   * Build strategy returns from paper trading history in the DB.
   * Groups closed trades by strategy and date, computing daily return %.
   */
  async buildStrategyReturnsFromDB(): Promise<StrategyReturn[]> {
    try {
      // Get closed trades from paper trading, grouped by strategy
      const trades = await db.paperTradingTrade.findMany({
        where: {
          strategyName: { not: null },
        },
        orderBy: { closedAt: 'asc' },
        take: 1000,
      });

      if (trades.length === 0) {
        return [];
      }

      // Group by strategy + date, accumulating individual returns for compound calculation
      const returnsMap = new Map<string, Map<string, { returns: number[] }>>();

      for (const trade of trades) {
        const strategyId = trade.strategyName || 'unknown';
        const dateStr = trade.closedAt.toISOString().split('T')[0]; // YYYY-MM-DD

        if (!returnsMap.has(strategyId)) {
          returnsMap.set(strategyId, new Map());
        }
        const dateMap = returnsMap.get(strategyId)!;
        const existing = dateMap.get(dateStr);
        if (existing) {
          existing.returns.push(trade.pnlPct);
        } else {
          dateMap.set(dateStr, { returns: [trade.pnlPct] });
        }
      }

      // Convert to StrategyReturn[]
      // Compound return instead of averaging: (1+r1)*(1+r2)*... - 1
      const returns: StrategyReturn[] = [];
      for (const [strategyId, dateMap] of returnsMap) {
        for (const [date, data] of dateMap) {
          const compoundReturn = data.returns.reduce((acc, r) => acc * (1 + r / 100), 1) - 1;
          returns.push({
            strategyId,
            date,
            dailyReturnPct: compoundReturn * 100,
          });
        }
      }

      return returns;
    } catch (error) {
      console.warn('[StrategyCorrelation] Error building returns from DB:', error);
      return [];
    }
  }

  // ============================================================
  // GET CURRENT CORRELATION MATRIX (CACHED)
  // ============================================================

  /**
   * Get the current correlation matrix, using cache if fresh (< 5 min).
   * Rebuilds from DB if cache is stale or empty.
   */
  async getCurrentCorrelationMatrix(): Promise<CorrelationMatrix> {
    // Return cached if fresh
    if (this.cachedMatrix && Date.now() - this.matrixCacheTime < this.CACHE_TTL_MS) {
      return this.cachedMatrix;
    }

    const returns = await this.buildStrategyReturnsFromDB();
    const matrix = this.computeCorrelationMatrix(returns);

    this.cachedMatrix = matrix;
    this.matrixCacheTime = Date.now();

    return matrix;
  }

  // ============================================================
  // CACHE MANAGEMENT
  // ============================================================

  /** Invalidate the cached correlation matrix (call after new trade data) */
  invalidateCache(): void {
    this.cachedMatrix = null;
    this.matrixCacheTime = 0;
  }
}

// ============================================================
// PEARSON CORRELATION HELPER
// ============================================================

/**
 * Compute Pearson correlation coefficient between two series.
 * Returns 0 if either series has zero variance.
 */
function pearsonCorrelation(x: number[], y: number[]): number {
  const n = Math.min(x.length, y.length);
  if (n < 2) return 0;

  // Compute means
  let sumX = 0;
  let sumY = 0;
  for (let i = 0; i < n; i++) {
    sumX += x[i];
    sumY += y[i];
  }
  const meanX = sumX / n;
  const meanY = sumY / n;

  // Compute covariance and standard deviations
  let covXY = 0;
  let varX = 0;
  let varY = 0;

  for (let i = 0; i < n; i++) {
    const dx = x[i] - meanX;
    const dy = y[i] - meanY;
    covXY += dx * dy;
    varX += dx * dx;
    varY += dy * dy;
  }

  // Zero variance check
  if (varX === 0 || varY === 0) return 0;

  const correlation = covXY / Math.sqrt(varX * varY);

  // Clamp to [-1, 1] (floating point safety)
  return Math.max(-1, Math.min(1, correlation));
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const strategyCorrelationService = new StrategyCorrelationService();
