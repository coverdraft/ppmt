/**
 * Portfolio Intelligence Engine — CryptoQuant Terminal
 *
 * Evaluates the impact of new positions on the ENTIRE portfolio before
 * capital allocation. This is the institutional-grade pre-check that
 * shifts the system from per-token thinking ("Should I buy BTC?")
 * to per-portfolio thinking ("How does adding BTC affect my entire portfolio?").
 *
 * Core capabilities:
 *   a) evaluateNewPosition — marginal risk, diversification delta, VaR impact
 *   b) computePortfolioRiskMetrics — volatility, VaR, CVaR, HHI, diversification
 *   c) optimizeWeights — Markowitz, Risk Parity, Min Variance, Max Div, Black-Litterman
 *   d) stressTest — predefined + custom scenarios
 *   e) computeCorrelationMatrix — Pearson, Spearman, DCC simplified
 *   f) checkPortfolioConstraints — concentration, chain, sector, correlation, VaR
 *
 * Integration:
 *   - Uses killSwitchService for risk budget checks
 *   - Uses strategyCorrelationService for strategy-level correlations
 *   - Uses Prisma client (db) for PriceCandle data
 *   - Designed to be called BY capital-allocation.ts as a pre-check
 *
 * Design principles:
 *   - NO `any` types — strict TypeScript throughout
 *   - Matrix operations: custom linear algebra with regularization
 *   - Ridge regularization on matrix inversion for crypto's high correlations
 *   - All financial values in USD
 *   - VaR time horizon configurable (default 1 day)
 */

import { db } from '@/lib/db';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';
import { strategyCorrelationService } from '@/lib/services/risk/strategy-correlation-service';
import {
  type Position,
  type ProposedPosition,
  type PortfolioImpact,
  type PortfolioRiskMetrics,
  type OptimizationMethod,
  type OptimalWeights,
  type StressScenario,
  type StressTestResult,
  type StressTestScenarioResult,
  type CorrelationMatrix,
  type CorrelationMethod,
  type ConstraintCheck,
  type ConstraintViolation,
  type ConstraintType,
  type PortfolioState,
  type BlackLittermanView,
  type Matrix,
  type Vector,
  MARKET_CAP_TIER_LIMITS,
} from './types';

// ============================================================
// LINEAR ALGEBRA UTILITIES
// ============================================================

/**
 * Matrix multiplication: C = A × B
 * Returns empty array if dimensions are incompatible.
 */
function matMul(a: Matrix, b: Matrix): Matrix {
  const m = a.length;
  const n = b.length;
  if (m === 0 || n === 0) return [];
  const p = b[0]?.length ?? 0;
  if ((a[0]?.length ?? 0) !== n) return [];

  const c: Matrix = Array.from({ length: m }, () => new Array(p).fill(0));
  for (let i = 0; i < m; i++) {
    for (let j = 0; j < p; j++) {
      let sum = 0;
      for (let k = 0; k < n; k++) {
        sum += (a[i]?.[k] ?? 0) * (b[k]?.[j] ?? 0);
      }
      c[i][j] = sum;
    }
  }
  return c;
}

/**
 * Matrix × Vector multiplication: result = A × v
 */
function matVecMul(a: Matrix, v: Vector): Vector {
  return a.map(row => row.reduce((sum, val, j) => sum + val * (v[j] ?? 0), 0));
}

/**
 * Matrix transpose
 */
function transpose(m: Matrix): Matrix {
  if (m.length === 0) return [];
  const rows = m.length;
  const cols = m[0]?.length ?? 0;
  const result: Matrix = Array.from({ length: cols }, () => new Array(rows).fill(0));
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) {
      result[j][i] = m[i]?.[j] ?? 0;
    }
  }
  return result;
}

/**
 * Invert a square matrix using Gauss-Jordan elimination with ridge regularization.
 *
 * The `ridgeLambda` parameter adds a small value to the diagonal before inversion
 * to prevent singular matrices. This is critical for crypto assets which are
 * often highly correlated, making the covariance matrix near-singular.
 *
 * Default ridge = 1e-6 provides numerical stability without materially
 * distorting the result.
 */
function invertMatrixRegularized(m: Matrix, ridgeLambda: number = 1e-6): Matrix {
  const n = m.length;
  if (n === 0) return [];

  // Apply ridge regularization: add lambda to diagonal
  const regularized: Matrix = m.map((row, i) =>
    row.map((val, j) => (i === j ? val + ridgeLambda : val))
  );

  // Augment with identity matrix
  const aug: Matrix = regularized.map((row, i) => {
    const identityRow = new Array(n).fill(0);
    identityRow[i] = 1;
    return [...row, ...identityRow];
  });

  // Gauss-Jordan elimination with partial pivoting
  for (let col = 0; col < n; col++) {
    // Find pivot row (largest absolute value in this column)
    let maxRow = col;
    let maxVal = Math.abs(aug[col]?.[col] ?? 0);
    for (let row = col + 1; row < n; row++) {
      const val = Math.abs(aug[row]?.[col] ?? 0);
      if (val > maxVal) {
        maxVal = val;
        maxRow = row;
      }
    }

    // Swap rows
    [aug[col], aug[maxRow]] = [aug[maxRow], aug[col]];

    // Singular check — with regularization this should be rare
    const pivot = aug[col]?.[col] ?? 0;
    if (Math.abs(pivot) < 1e-14) {
      // Even with regularization, matrix is too ill-conditioned
      // Return identity-scaled as safe fallback
      const fallback: Matrix = Array.from({ length: n }, (_, i) =>
        new Array(n).fill(0).map((_, j) => (i === j ? 1 / (m[i]?.[i] ?? 1 + ridgeLambda) : 0))
      );
      return fallback;
    }

    // Scale pivot row
    for (let j = 0; j < 2 * n; j++) {
      aug[col][j] = (aug[col]?.[j] ?? 0) / pivot;
    }

    // Eliminate other rows
    for (let row = 0; row < n; row++) {
      if (row === col) continue;
      const factor = aug[row]?.[col] ?? 0;
      for (let j = 0; j < 2 * n; j++) {
        aug[row][j] = (aug[row]?.[j] ?? 0) - factor * (aug[col]?.[j] ?? 0);
      }
    }
  }

  // Extract the inverse (right half of augmented matrix)
  return aug.map(row => row.slice(n));
}

/**
 * Compute eigenvalues using the power iteration method.
 * Returns eigenvalues sorted in descending order.
 * This is a simplified approach — for production, consider a library.
 */
function eigenvalues(m: Matrix, maxIter: number = 100): Vector {
  const n = m.length;
  if (n === 0) return [];
  if (n === 1) return [m[0]?.[0] ?? 0];

  const eigenvals: Vector = [];
  const remaining: Matrix = m.map(row => [...row]);

  for (let k = 0; k < n; k++) {
    // Random initial vector
    let v: Vector = new Array(n - k).fill(0).map(() => Math.random() - 0.5);
    const vNorm = Math.sqrt(v.reduce((s, x) => s + x * x, 0));
    if (vNorm > 0) v = v.map(x => x / vNorm);

    let eigenvalue = 0;

    for (let iter = 0; iter < maxIter; iter++) {
      // Multiply by matrix
      const mv: Vector = new Array(n - k).fill(0);
      for (let i = 0; i < n - k; i++) {
        for (let j = 0; j < n - k; j++) {
          mv[i] += (remaining[i]?.[j] ?? 0) * v[j];
        }
      }

      // Compute eigenvalue (Rayleigh quotient)
      const num = v.reduce((s, x, i) => s + x * mv[i], 0);
      const den = v.reduce((s, x) => s + x * x, 0);
      eigenvalue = den !== 0 ? num / den : 0;

      // Normalize
      const mvNorm = Math.sqrt(mv.reduce((s, x) => s + x * x, 0));
      if (mvNorm < 1e-14) break;
      v = mv.map(x => x / mvNorm);
    }

    eigenvals.push(eigenvalue);

    // Deflate: remove the found eigenvalue/eigenvector contribution
    // A' = A - lambda * v * v^T
    for (let i = 0; i < n - k; i++) {
      for (let j = 0; j < n - k; j++) {
        remaining[i][j] = (remaining[i]?.[j] ?? 0) - eigenvalue * v[i] * v[j];
      }
    }
  }

  return eigenvals.sort((a, b) => b - a);
}

// ============================================================
// STATISTICAL UTILITIES
// ============================================================

/** Compute the mean of a number array */
function mean(arr: Vector): number {
  if (arr.length === 0) return 0;
  return arr.reduce((s, v) => s + v, 0) / arr.length;
}

/** Compute the standard deviation (sample) of a number array */
function stdDev(arr: Vector): number {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  const variance = arr.reduce((s, v) => s + (v - m) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(Math.max(variance, 0));
}

/** Compute covariance between two arrays */
function covariance(x: Vector, y: Vector): number {
  const n = Math.min(x.length, y.length);
  if (n < 2) return 0;
  const mx = mean(x);
  const my = mean(y);
  let cov = 0;
  for (let i = 0; i < n; i++) {
    cov += (x[i] - mx) * (y[i] - my);
  }
  return cov / (n - 1);
}

/** Pearson correlation coefficient */
function pearsonCorrelation(x: Vector, y: Vector): number {
  const vx = stdDev(x);
  const vy = stdDev(y);
  if (vx < 1e-12 || vy < 1e-12) return 0;
  const cov = covariance(x, y);
  return Math.max(-1, Math.min(1, cov / (vx * vy)));
}

/** Spearman rank correlation */
function spearmanCorrelation(x: Vector, y: Vector): number {
  const n = Math.min(x.length, y.length);
  if (n < 2) return 0;
  const rankX = computeRanks(x.slice(0, n));
  const rankY = computeRanks(y.slice(0, n));
  return pearsonCorrelation(rankX, rankY);
}

/** Compute ranks for Spearman correlation (with tie handling using average rank) */
function computeRanks(arr: Vector): Vector {
  const n = arr.length;
  const indexed = arr.map((v, i) => ({ v, i }));
  indexed.sort((a, b) => a.v - b.v);

  const ranks = new Array(n).fill(0);
  let i = 0;
  while (i < n) {
    let j = i;
    while (j < n && indexed[j].v === indexed[i].v) {
      j++;
    }
    // Assign average rank for ties
    const avgRank = (i + j - 1) / 2 + 1; // 1-based rank
    for (let k = i; k < j; k++) {
      ranks[indexed[k].i] = avgRank;
    }
    i = j;
  }
  return ranks;
}

/** Normal CDF using the rational approximation (Abramowitz & Stegun) */
function normalCDF(x: number): number {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;

  const sign = x < 0 ? -1 : 1;
  const absX = Math.abs(x);
  const t = 1.0 / (1.0 + p * absX);
  const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-absX * absX / 2);

  return 0.5 * (1.0 + sign * y);
}

/** Inverse normal CDF (quantile function) using rational approximation */
function normalInvCDF(p: number): number {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  if (p === 0.5) return 0;

  // Rational approximation for the inverse normal CDF (Peter Acklam's algorithm)
  const a: Vector = [
    -3.969683028665376e+01,
     2.209460984245205e+02,
    -2.759285104469687e+02,
     1.383577518672690e+02,
    -3.066479806614716e+01,
     2.506628277459239e+00,
  ];
  const b: Vector = [
    -5.447609879822406e+01,
     1.615858368580409e+02,
    -1.556989798598866e+02,
     6.680131188771972e+01,
    -1.328068155288572e+01,
  ];
  const c: Vector = [
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e+00,
    -2.549732539343734e+00,
     4.374664141464968e+00,
     2.938163982698783e+00,
  ];
  const d: Vector = [
     7.784695709041462e-03,
     3.224671290700398e-01,
     2.445134137142996e+00,
     3.754408661907416e+00,
  ];

  const pLow = 0.02425;
  const pHigh = 1 - pLow;

  let q: number, r: number;
  let result: number;

  if (p < pLow) {
    q = Math.sqrt(-2 * Math.log(p));
    result = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
             ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  } else if (p <= pHigh) {
    q = p - 0.5;
    r = q * q;
    result = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
             (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
  } else {
    q = Math.sqrt(-2 * Math.log(1 - p));
    result = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
              ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }

  return result;
}

// ============================================================
// PREDEFINED STRESS SCENARIOS
// ============================================================

const PREDEFINED_STRESS_SCENARIOS: StressScenario[] = [
  {
    id: 'market_crash_20',
    name: 'Market Crash -20%',
    description: 'Broad market sell-off of 20% across all crypto assets with elevated correlations',
    shocks: new Map(),
    marketShock: -0.20,
    correlationMultiplier: 1.5,
    isPredefined: true,
  },
  {
    id: 'crypto_winter_50',
    name: 'Crypto Winter -50%',
    description: 'Prolonged bear market with 50% decline, correlations spike to near 1',
    shocks: new Map(),
    marketShock: -0.50,
    correlationMultiplier: 2.0,
    isPredefined: true,
  },
  {
    id: 'flash_crash_10',
    name: 'Flash Crash -10%',
    description: 'Sudden 10% drop within hours, liquidity evaporates temporarily',
    shocks: new Map(),
    marketShock: -0.10,
    correlationMultiplier: 1.3,
    isPredefined: true,
  },
  {
    id: 'correlation_break',
    name: 'Correlation Break',
    description: 'Assets that normally move together decouple; some rally while others crash',
    shocks: new Map([
      ['__HALF_POSITIVE__', 0.15],  // Sentinel: first half of positions get +15%
      ['__HALF_NEGATIVE__', -0.15], // Second half get -15%
    ]),
    marketShock: 0,
    correlationMultiplier: 0.2,
    isPredefined: true,
  },
  {
    id: 'liquidity_crisis',
    name: 'Liquidity Crisis',
    description: 'Severe liquidity drain: small caps drop 40%, large caps drop 15%',
    shocks: new Map(),
    marketShock: -0.15,
    correlationMultiplier: 1.8,
    isPredefined: true,
  },
];

// ============================================================
// PORTFOLIO INTELLIGENCE ENGINE
// ============================================================

class PortfolioIntelligenceEngine {
  /** Cache for correlation matrix (5 minute TTL) */
  private correlationCache: Map<string, { matrix: CorrelationMatrix; expiresAt: number }> = new Map();
  private readonly CORRELATION_CACHE_TTL_MS = 5 * 60 * 1000;

  /** Cache for portfolio state snapshot */
  private portfolioStateCache: PortfolioState | null = null;
  private portfolioStateCacheTime: number = 0;
  private readonly PORTFOLIO_STATE_CACHE_TTL_MS = 60 * 1000; // 1 minute

  // ============================================================
  // a) EVALUATE NEW POSITION
  // ============================================================

  /**
   * Evaluate the impact of adding a new position to the portfolio.
   *
   * This is the primary entry point for the capital-allocation pre-check.
   * It computes:
   *   - Marginal risk contribution of the new position
   *   - Change in diversification ratio
   *   - Impact on portfolio VaR
   *   - Average correlation with existing positions
   *
   * Returns a PortfolioImpact object with approval status and recommendations.
   */
  async evaluateNewPosition(
    proposedPosition: ProposedPosition,
    currentPositions: Position[],
    totalPortfolioValue: number,
  ): Promise<PortfolioImpact> {
    if (currentPositions.length === 0) {
      // First position — always approve with baseline metrics
      return {
        approved: true,
        impactScore: 0.5,
        riskContribution: proposedPosition.expectedVolatility * proposedPosition.proposedSizeUsd / totalPortfolioValue,
        diversificationDelta: 1.0, // Going from 0 to 1 position = infinite improvement
        varDelta: 0,
        correlationWithExisting: 0,
        recommendations: ['First position in portfolio — baseline established'],
      };
    }

    // 1. Compute current portfolio risk metrics
    const currentMetrics = this.computePortfolioRiskMetrics(currentPositions);

    // 2. Create hypothetical portfolio with the new position
    const hypotheticalPositions = [...currentPositions, this.proposedToPosition(proposedPosition, totalPortfolioValue)];
    const hypotheticalMetrics = this.computePortfolioRiskMetrics(hypotheticalPositions);

    // 3. Compute marginal risk contribution
    // MRC_i = (dσ_p / dw_i) = [(Σw)_i / σ_p]
    // Simplified: risk contribution = increase in portfolio volatility per unit of weight
    const newWeight = proposedPosition.proposedSizeUsd / totalPortfolioValue;
    const volIncrease = hypotheticalMetrics.portfolioVolatility - currentMetrics.portfolioVolatility;
    const riskContribution = newWeight > 0 ? volIncrease / newWeight : 0;

    // 4. Compute diversification delta
    const diversificationDelta = hypotheticalMetrics.diversificationRatio - currentMetrics.diversificationRatio;

    // 5. Compute VaR delta
    const varDelta = hypotheticalMetrics.var95 - currentMetrics.var95;

    // 6. Compute correlation with existing positions
    const correlationWithExisting = await this.computeCorrelationWithExisting(
      proposedPosition,
      currentPositions,
    );

    // 7. Check portfolio constraints
    const constraintCheck = this.checkPortfolioConstraints(
      hypotheticalPositions,
      proposedPosition,
    );

    // 8. Build recommendations
    const recommendations: string[] = [];

    if (diversificationDelta > 0) {
      recommendations.push(`Improves diversification by ${(diversificationDelta * 100).toFixed(2)}%`);
    } else {
      recommendations.push(`Reduces diversification by ${(Math.abs(diversificationDelta) * 100).toFixed(2)}% — consider reducing position size`);
    }

    if (correlationWithExisting > 0.6) {
      recommendations.push(`High correlation (${(correlationWithExisting * 100).toFixed(1)}%) with existing positions — adds concentrated risk`);
    } else if (correlationWithExisting < 0.3) {
      recommendations.push(`Low correlation (${(correlationWithExisting * 100).toFixed(1)}%) — good diversification benefit`);
    }

    if (varDelta > totalPortfolioValue * 0.01) {
      recommendations.push(`VaR increase >1% of portfolio — position may be too large`);
    }

    // Check kill switch status
    const killSwitchState = killSwitchService.getState();
    if (killSwitchState.globalPause) {
      recommendations.push('BLOCKED: Global kill switch is active');
    }
    if (killSwitchState.portfolioDDTriggered) {
      recommendations.push('BLOCKED: Portfolio drawdown kill switch is active');
    }

    for (const violation of constraintCheck.violations) {
      recommendations.push(`${violation.severity}: ${violation.message}`);
    }

    // 9. Compute composite impact score (-1 to +1)
    // Positive = good for portfolio, Negative = bad
    const diversificationScore = Math.max(-1, Math.min(1, diversificationDelta * 10));
    const correlationScore = Math.max(-1, Math.min(1, (0.5 - correlationWithExisting) * 2));
    const varScore = Math.max(-1, Math.min(1, -varDelta / (totalPortfolioValue * 0.05)));
    const constraintScore = constraintCheck.healthScore;

    const impactScore = (
      diversificationScore * 0.3 +
      correlationScore * 0.25 +
      varScore * 0.25 +
      constraintScore * 0.2
    );

    // 10. Determine approval
    const approved =
      constraintCheck.passed &&
      !killSwitchState.globalPause &&
      !killSwitchState.portfolioDDTriggered &&
      impactScore > -0.3;

    return {
      approved,
      impactScore: Math.round(impactScore * 10000) / 10000,
      riskContribution: Math.round(riskContribution * 10000) / 10000,
      diversificationDelta: Math.round(diversificationDelta * 10000) / 10000,
      varDelta: Math.round(varDelta * 100) / 100,
      correlationWithExisting: Math.round(correlationWithExisting * 10000) / 10000,
      recommendations,
    };
  }

  // ============================================================
  // b) COMPUTE PORTFOLIO RISK METRICS
  // ============================================================

  /**
   * Compute comprehensive portfolio risk metrics.
   *
   * Includes:
   *   - Portfolio volatility (weighted, with correlations)
   *   - Parametric VaR (95%, 99%)
   *   - Historical VaR (if enough data)
   *   - CVaR (Expected Shortfall)
   *   - Diversification ratio
   *   - Herfindahl-Hirschman Index
   *   - Max drawdown estimate
   */
  computePortfolioRiskMetrics(
    positions: Position[],
    timeHorizonDays: number = 1,
  ): PortfolioRiskMetrics {
    const n = positions.length;
    const now = new Date();

    if (n === 0) {
      return {
        portfolioVolatility: 0,
        var95: 0,
        var99: 0,
        historicalVar95: null,
        cvar95: 0,
        diversificationRatio: 0,
        hhi: 0,
        maxDrawdownEstimate: 0,
        timeHorizonDays,
        computedAt: now,
      };
    }

    // Compute weights
    const totalValue = positions.reduce((s, p) => s + p.sizeUsd, 0);
    const weights: Vector = totalValue > 0
      ? positions.map(p => p.sizeUsd / totalValue)
      : positions.map(() => 1 / n);

    // Compute portfolio volatility with correlation
    const portfolioVol = this.computePortfolioVolatility(positions, weights);

    // Parametric VaR (Variance-Covariance method, assumes normal distribution)
    // VaR = portfolio_value × z_score × σ_p × √(time_horizon)
    const sqrtHorizon = Math.sqrt(timeHorizonDays);
    const z95 = Math.abs(normalInvCDF(0.05)); // ~1.645
    const z99 = Math.abs(normalInvCDF(0.01)); // ~2.326

    const var95 = totalValue * z95 * portfolioVol * sqrtHorizon;
    const var99 = totalValue * z99 * portfolioVol * sqrtHorizon;

    // Historical VaR (if enough return data)
    let historicalVar95: number | null = null;
    const portfolioReturns = this.computePortfolioReturns(positions, weights);
    if (portfolioReturns.length >= 30) {
      const sorted = [...portfolioReturns].sort((a, b) => a - b);
      const idx5 = Math.floor(0.05 * sorted.length);
      historicalVar95 = Math.abs(sorted[idx5] ?? 0) * totalValue * sqrtHorizon;
    }

    // CVaR (Expected Shortfall) at 95%
    // Average of losses beyond VaR threshold
    let cvar95: number;
    if (portfolioReturns.length >= 30) {
      const sorted = [...portfolioReturns].sort((a, b) => a - b);
      const tailStart = Math.floor(0.05 * sorted.length);
      const tailReturns = sorted.slice(0, Math.max(tailStart, 1));
      cvar95 = tailReturns.length > 0
        ? Math.abs(mean(tailReturns)) * totalValue * sqrtHorizon
        : var95 * 1.2; // Fallback: parametric CVaR ≈ 1.2 × VaR under normality
    } else {
      // Under normal distribution: CVaR_95 ≈ 1.2 × VaR_95
      cvar95 = var95 * 1.2;
    }

    // Diversification ratio
    // DR = (Σ w_i × σ_i) / σ_p
    // If DR > 1, portfolio benefits from diversification
    const weightedAvgVol = weights.reduce((s, w, i) =>
      s + w * positions[i].volatility, 0);
    const diversificationRatio = portfolioVol > 1e-12
      ? weightedAvgVol / portfolioVol
      : 1.0;

    // Herfindahl-Hirschman Index
    // HHI = Σ w_i² (0 = perfect diversification, 1 = single position)
    const hhi = weights.reduce((s, w) => s + w * w, 0);

    // Max drawdown estimate
    // Heuristic: use portfolio volatility and a scaling factor
    // MDD ≈ 2 × σ_annual × √(T/π) where T is average recovery time
    // Simplified: MDD ≈ 2 × σ_annual for crypto (which has faster but deeper drawdowns)
    const annualVol = portfolioVol * Math.sqrt(365);
    const maxDrawdownEstimate = Math.min(2 * annualVol, 1.0);

    return {
      portfolioVolatility: Math.round(portfolioVol * 10000) / 10000,
      var95: Math.round(var95 * 100) / 100,
      var99: Math.round(var99 * 100) / 100,
      historicalVar95: historicalVar95 !== null ? Math.round(historicalVar95 * 100) / 100 : null,
      cvar95: Math.round(cvar95 * 100) / 100,
      diversificationRatio: Math.round(diversificationRatio * 10000) / 10000,
      hhi: Math.round(hhi * 10000) / 10000,
      maxDrawdownEstimate: Math.round(maxDrawdownEstimate * 10000) / 10000,
      timeHorizonDays,
      computedAt: now,
    };
  }

  // ============================================================
  // c) OPTIMIZE WEIGHTS
  // ============================================================

  /**
   * Optimize portfolio weights using the specified method.
   *
   * Methods:
   *   - MEAN_VARIANCE: Markowitz MPT — max Sharpe ratio portfolio
   *   - RISK_PARITY: Equal risk contribution from each asset
   *   - MIN_VARIANCE: Minimum portfolio variance
   *   - MAX_DIVERSIFICATION: Maximum diversification ratio
   *   - BLACK_LITTERMAN: Incorporate user views into equilibrium
   */
  optimizeWeights(
    positions: Position[],
    method: OptimizationMethod,
    views?: BlackLittermanView[],
  ): OptimalWeights {
    const n = positions.length;

    if (n === 0) {
      return {
        weights: new Map(),
        expectedReturn: 0,
        expectedVol: 0,
        sharpeRatio: 0,
        method,
      };
    }

    // Build covariance matrix from positions
    const covMatrix = this.buildCovarianceMatrix(positions);

    // Compute expected returns (mean of historical returns)
    const expectedReturns: Vector = positions.map(p => {
      if (p.returns.length === 0) return 0;
      return mean(p.returns) * 365; // Annualize daily returns
    });

    // Individual volatilities
    const vols: Vector = positions.map(p => p.volatility);

    let weights: Vector;

    switch (method) {
      case 'MEAN_VARIANCE':
        weights = this.optimizeMeanVariance(covMatrix, expectedReturns);
        break;
      case 'RISK_PARITY':
        weights = this.optimizeRiskParity(covMatrix, vols);
        break;
      case 'MIN_VARIANCE':
        weights = this.optimizeMinVariance(covMatrix);
        break;
      case 'MAX_DIVERSIFICATION':
        weights = this.optimizeMaxDiversification(covMatrix, vols);
        break;
      case 'BLACK_LITTERMAN':
        weights = this.optimizeBlackLitterman(covMatrix, positions, views);
        break;
      default:
        weights = new Array(n).fill(1 / n);
    }

    // Normalize weights to sum to 1 and clamp negatives
    weights = weights.map(w => Math.max(w, 0));
    const weightSum = weights.reduce((s, w) => s + w, 0);
    if (weightSum > 0) {
      weights = weights.map(w => w / weightSum);
    } else {
      weights = new Array(n).fill(1 / n);
    }

    // Compute expected portfolio return and volatility
    const expectedReturn = weights.reduce((s, w, i) => s + w * expectedReturns[i], 0);
    const portfolioVar = this.portfolioVariance(weights, covMatrix);
    const expectedVol = Math.sqrt(Math.max(portfolioVar, 0));

    // Sharpe ratio (annualized, assuming risk-free = 0)
    const sharpeRatio = expectedVol > 1e-12 ? expectedReturn / expectedVol : 0;

    // Build weights map
    const weightsMap = new Map<string, number>();
    positions.forEach((p, i) => {
      weightsMap.set(p.tokenAddress, Math.round(weights[i] * 10000) / 10000);
    });

    return {
      weights: weightsMap,
      expectedReturn: Math.round(expectedReturn * 10000) / 10000,
      expectedVol: Math.round(expectedVol * 10000) / 10000,
      sharpeRatio: Math.round(sharpeRatio * 10000) / 10000,
      method,
    };
  }

  // ============================================================
  // d) STRESS TEST
  // ============================================================

  /**
   * Run stress tests on the portfolio.
   *
   * Includes 5 predefined scenarios and any custom scenarios provided.
   * Returns per-scenario impact, worst case, and recovery estimate.
   */
  stressTest(
    positions: Position[],
    customScenarios: StressScenario[] = [],
  ): StressTestResult {
    const allScenarios = [...PREDEFINED_STRESS_SCENARIOS, ...customScenarios];
    const totalValue = positions.reduce((s, p) => s + p.sizeUsd, 0);

    if (positions.length === 0 || totalValue === 0) {
      const emptyResult: StressTestScenarioResult = {
        scenarioId: 'none',
        scenarioName: 'Empty Portfolio',
        portfolioImpactUsd: 0,
        portfolioImpactPct: 0,
        positionImpacts: new Map(),
        recoveryDaysEstimate: 0,
      };
      return {
        scenarioResults: [emptyResult],
        worstCase: emptyResult,
        averageImpactPct: 0,
        computedAt: new Date(),
      };
    }

    const scenarioResults: StressTestScenarioResult[] = allScenarios.map(scenario => {
      return this.runStressScenario(positions, scenario, totalValue);
    });

    // Find worst case
    const worstCase = scenarioResults.reduce((worst, result) =>
      result.portfolioImpactPct < worst.portfolioImpactPct ? result : worst
    );

    // Average impact
    const averageImpactPct = scenarioResults.length > 0
      ? scenarioResults.reduce((s, r) => s + r.portfolioImpactPct, 0) / scenarioResults.length
      : 0;

    return {
      scenarioResults,
      worstCase,
      averageImpactPct: Math.round(averageImpactPct * 10000) / 10000,
      computedAt: new Date(),
    };
  }

  // ============================================================
  // e) COMPUTE CORRELATION MATRIX
  // ============================================================

  /**
   * Compute the correlation matrix between all positions.
   *
   * Supports three methods:
   *   - PEARSON: Rolling 30-day Pearson correlation (default)
   *   - SPEARMAN: Rank correlation (more robust to outliers)
   *   - DCC_SIMPLIFIED: Dynamic Conditional Correlation (exponentially weighted)
   *
   * Uses price history from the PriceCandle table when available,
   * falling back to the returns stored in each Position.
   */
  async computeCorrelationMatrix(
    positions: Position[],
    method: CorrelationMethod = 'PEARSON',
  ): Promise<CorrelationMatrix> {
    const cacheKey = `${positions.map(p => p.tokenAddress).sort().join(',')}:${method}`;
    const cached = this.correlationCache.get(cacheKey);
    if (cached && Date.now() < cached.expiresAt) {
      return cached.matrix;
    }

    const n = positions.length;

    if (n === 0) {
      return {
        tokens: [],
        matrix: [],
        method,
        dataPoints: 0,
        computedAt: new Date(),
      };
    }

    if (n === 1) {
      const result: CorrelationMatrix = {
        tokens: [positions[0].tokenAddress],
        matrix: [[1]],
        method,
        dataPoints: positions[0].returns.length,
        computedAt: new Date(),
      };
      return result;
    }

    // Try to get returns from DB (PriceCandle) first
    const returnSeries: Map<string, Vector> = new Map();
    for (const position of positions) {
      const dbReturns = await this.getReturnsFromDB(position.tokenAddress, position.chain);
      returnSeries.set(position.tokenAddress, dbReturns.length >= 30 ? dbReturns : position.returns);
    }

    // Align return series to common dates
    const minLength = Math.min(
      ...positions.map(p => returnSeries.get(p.tokenAddress)?.length ?? 0)
    );

    if (minLength < 5) {
      // Not enough data — return identity-like matrix
      const matrix: Matrix = Array.from({ length: n }, (_, i) =>
        Array.from({ length: n }, (_, j) => i === j ? 1 : 0)
      );
      return {
        tokens: positions.map(p => p.tokenAddress),
        matrix,
        method,
        dataPoints: minLength,
        computedAt: new Date(),
      };
    }

    // Build aligned return series
    const alignedSeries: Vector[] = positions.map(p => {
      const series = returnSeries.get(p.tokenAddress) ?? p.returns;
      return series.slice(0, minLength);
    });

    // Compute correlation matrix
    const matrix: Matrix = Array.from({ length: n }, () => new Array(n).fill(0));

    for (let i = 0; i < n; i++) {
      matrix[i][i] = 1;
      for (let j = i + 1; j < n; j++) {
        let corr: number;
        switch (method) {
          case 'SPEARMAN':
            corr = spearmanCorrelation(alignedSeries[i], alignedSeries[j]);
            break;
          case 'DCC_SIMPLIFIED':
            corr = this.dccSimplified(alignedSeries[i], alignedSeries[j]);
            break;
          case 'PEARSON':
          default:
            corr = pearsonCorrelation(alignedSeries[i], alignedSeries[j]);
            break;
        }
        matrix[i][j] = Math.round(corr * 10000) / 10000;
        matrix[j][i] = matrix[i][j];
      }
    }

    const result: CorrelationMatrix = {
      tokens: positions.map(p => p.tokenAddress),
      matrix,
      method,
      dataPoints: minLength,
      computedAt: new Date(),
    };

    // Cache result
    this.correlationCache.set(cacheKey, {
      matrix: result,
      expiresAt: Date.now() + this.CORRELATION_CACHE_TTL_MS,
    });

    return result;
  }

  // ============================================================
  // f) CHECK PORTFOLIO CONSTRAINTS
  // ============================================================

  /**
   * Check all portfolio constraints.
   *
   * Constraints:
   *   - Max concentration per token (5-15% based on market cap tier)
   *   - Max chain exposure (50%)
   *   - Max sector exposure (30%)
   *   - Max correlated assets (>60% correlation = same bucket)
   *   - Min diversification ratio (>0.5)
   *   - Max VaR budget (5% daily)
   */
  checkPortfolioConstraints(
    positions: Position[],
    newPosition?: ProposedPosition,
  ): ConstraintCheck {
    const violations: ConstraintViolation[] = [];
    const totalValue = positions.reduce((s, p) => s + p.sizeUsd, 0) +
      (newPosition?.proposedSizeUsd ?? 0);

    if (totalValue <= 0) {
      return { passed: true, violations: [], healthScore: 1 };
    }

    // Load risk budget from kill switch service (synchronous access to cached config)
    const riskBudget = killSwitchService.getState();

    // 1. Token concentration check
    const tokenWeights = new Map<string, number>();
    for (const pos of positions) {
      const current = tokenWeights.get(pos.tokenAddress) ?? 0;
      tokenWeights.set(pos.tokenAddress, current + pos.sizeUsd / totalValue);
    }
    if (newPosition) {
      const current = tokenWeights.get(newPosition.tokenAddress) ?? 0;
      tokenWeights.set(newPosition.tokenAddress, current + newPosition.proposedSizeUsd / totalValue);
    }

    for (const [token, weight] of tokenWeights) {
      // Find the market cap tier for this token
      const pos = positions.find(p => p.tokenAddress === token);
      const tier = pos?.marketCapTier ?? newPosition?.marketCapTier ?? 'SMALL';
      const maxConcentration = MARKET_CAP_TIER_LIMITS[tier] / 100;

      if (weight > maxConcentration) {
        violations.push({
          type: 'MAX_TOKEN_CONCENTRATION',
          message: `Token ${token} concentration ${(weight * 100).toFixed(1)}% exceeds ${(maxConcentration * 100).toFixed(0)}% limit for ${tier} tier`,
          currentValue: weight,
          limitValue: maxConcentration,
          severity: weight > maxConcentration * 1.5 ? 'CRITICAL' : 'WARNING',
        });
      }
    }

    // 2. Chain exposure check
    const chainExposure = new Map<string, number>();
    for (const pos of positions) {
      const current = chainExposure.get(pos.chain) ?? 0;
      chainExposure.set(pos.chain, current + pos.sizeUsd / totalValue);
    }
    if (newPosition) {
      const current = chainExposure.get(newPosition.chain) ?? 0;
      chainExposure.set(newPosition.chain, current + newPosition.proposedSizeUsd / totalValue);
    }

    const maxChainExposure = 0.50; // 50%
    for (const [chain, exposure] of chainExposure) {
      if (exposure > maxChainExposure) {
        violations.push({
          type: 'MAX_CHAIN_EXPOSURE',
          message: `Chain ${chain} exposure ${(exposure * 100).toFixed(1)}% exceeds 50% limit`,
          currentValue: exposure,
          limitValue: maxChainExposure,
          severity: exposure > 0.7 ? 'CRITICAL' : 'WARNING',
        });
      }
    }

    // 3. Sector exposure check
    const sectorExposure = new Map<string, number>();
    for (const pos of positions) {
      const current = sectorExposure.get(pos.sector) ?? 0;
      sectorExposure.set(pos.sector, current + pos.sizeUsd / totalValue);
    }
    if (newPosition) {
      const current = sectorExposure.get(newPosition.sector) ?? 0;
      sectorExposure.set(newPosition.sector, current + newPosition.proposedSizeUsd / totalValue);
    }

    const maxSectorExposure = 0.30; // 30%
    for (const [sector, exposure] of sectorExposure) {
      if (exposure > maxSectorExposure) {
        violations.push({
          type: 'MAX_SECTOR_EXPOSURE',
          message: `Sector ${sector} exposure ${(exposure * 100).toFixed(1)}% exceeds 30% limit`,
          currentValue: exposure,
          limitValue: maxSectorExposure,
          severity: exposure > 0.45 ? 'CRITICAL' : 'WARNING',
        });
      }
    }

    // 4. Correlated assets check (>60% correlation = same bucket, max 40% per bucket)
    const correlatedBuckets = this.findCorrelatedBuckets(positions, 0.60);
    for (const bucket of correlatedBuckets) {
      const bucketWeight = bucket.tokens.reduce((s, token) => {
        const pos = positions.find(p => p.tokenAddress === token);
        return s + (pos ? pos.sizeUsd / totalValue : 0);
      }, 0);

      if (bucketWeight > 0.40) {
        violations.push({
          type: 'MAX_CORRELATED_ASSETS',
          message: `Correlated bucket (avg corr ${(bucket.avgCorrelation * 100).toFixed(0)}%) has ${(bucketWeight * 100).toFixed(1)}% exposure, exceeding 40% limit`,
          currentValue: bucketWeight,
          limitValue: 0.40,
          severity: bucketWeight > 0.55 ? 'CRITICAL' : 'WARNING',
        });
      }
    }

    // 5. Diversification ratio check
    const metrics = this.computePortfolioRiskMetrics(positions);
    if (metrics.diversificationRatio < 0.5) {
      violations.push({
        type: 'MIN_DIVERSIFICATION_RATIO',
        message: `Diversification ratio ${metrics.diversificationRatio.toFixed(2)} is below 0.5 minimum — portfolio is too concentrated`,
        currentValue: metrics.diversificationRatio,
        limitValue: 0.5,
        severity: metrics.diversificationRatio < 0.3 ? 'CRITICAL' : 'WARNING',
      });
    }

    // 6. VaR budget check (5% daily max)
    const varBudget = 0.05; // 5% daily
    const dailyVarPct = metrics.var95 / totalValue;
    if (dailyVarPct > varBudget) {
      violations.push({
        type: 'MAX_VAR_BUDGET',
        message: `Daily VaR ${(dailyVarPct * 100).toFixed(1)}% exceeds 5% budget`,
        currentValue: dailyVarPct,
        limitValue: varBudget,
        severity: dailyVarPct > 0.08 ? 'CRITICAL' : 'WARNING',
      });
    }

    // Compute health score (1 = all clear, 0 = many severe violations)
    const criticalCount = violations.filter(v => v.severity === 'CRITICAL').length;
    const warningCount = violations.filter(v => v.severity === 'WARNING').length;
    const healthScore = Math.max(0, 1 - (criticalCount * 0.3 + warningCount * 0.1));

    return {
      passed: violations.length === 0,
      violations,
      healthScore: Math.round(healthScore * 10000) / 10000,
    };
  }

  // ============================================================
  // PORTFOLIO STATE BUILDER
  // ============================================================

  /**
   * Build a complete PortfolioState snapshot.
   * Uses caching to avoid recomputing every time.
   */
  async buildPortfolioState(positions: Position[]): Promise<PortfolioState> {
    if (this.portfolioStateCache && Date.now() - this.portfolioStateCacheTime < this.PORTFOLIO_STATE_CACHE_TTL_MS) {
      return this.portfolioStateCache;
    }

    const totalValue = positions.reduce((s, p) => s + p.sizeUsd, 0);
    const totalUnrealizedPnl = positions.reduce((s, p) => s + p.unrealizedPnl, 0);
    const allocatedCapital = totalValue;

    const correlationMatrix = await this.computeCorrelationMatrix(positions);
    const riskMetrics = this.computePortfolioRiskMetrics(positions);

    // Sector exposure
    const sectorExposure = new Map<string, number>();
    for (const pos of positions) {
      const current = sectorExposure.get(pos.sector) ?? 0;
      sectorExposure.set(pos.sector, current + (totalValue > 0 ? pos.sizeUsd / totalValue : 0));
    }

    // Chain exposure
    const chainExposure = new Map<string, number>();
    for (const pos of positions) {
      const current = chainExposure.get(pos.chain) ?? 0;
      chainExposure.set(pos.chain, current + (totalValue > 0 ? pos.sizeUsd / totalValue : 0));
    }

    const state: PortfolioState = {
      positions,
      correlationMatrix,
      sectorExposure,
      chainExposure,
      riskMetrics,
      totalValueUsd: totalValue,
      totalUnrealizedPnl,
      availableCapital: 0, // Needs to be set by caller based on total capital
      allocatedCapital,
      snapshotAt: new Date(),
    };

    this.portfolioStateCache = state;
    this.portfolioStateCacheTime = Date.now();

    return state;
  }

  /** Invalidate portfolio state cache */
  invalidateCache(): void {
    this.portfolioStateCache = null;
    this.portfolioStateCacheTime = 0;
    this.correlationCache.clear();
  }

  // ============================================================
  // PRIVATE HELPERS — RISK METRICS
  // ============================================================

  /**
   * Compute portfolio volatility using weighted positions with correlations.
   * σ_p = √(w'Σw)
   */
  private computePortfolioVolatility(positions: Position[], weights: Vector): number {
    const n = positions.length;
    if (n === 0) return 0;

    const covMatrix = this.buildCovarianceMatrix(positions);
    const variance = this.portfolioVariance(weights, covMatrix);
    return Math.sqrt(Math.max(variance, 0));
  }

  /**
   * Compute portfolio variance: w'Σw
   */
  private portfolioVariance(weights: Vector, covMatrix: Matrix): number {
    const n = weights.length;
    let variance = 0;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        variance += weights[i] * weights[j] * (covMatrix[i]?.[j] ?? 0);
      }
    }
    return variance;
  }

  /**
   * Build covariance matrix from positions.
   * Σ_ij = σ_i × σ_j × ρ_ij
   * Uses returns-based correlation when available, falls back to 0.3 default.
   */
  private buildCovarianceMatrix(positions: Position[]): Matrix {
    const n = positions.length;
    const covMatrix: Matrix = Array.from({ length: n }, () => new Array(n).fill(0));

    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if (i === j) {
          covMatrix[i][j] = positions[i].volatility * positions[i].volatility;
        } else if (i < j) {
          let corr = 0.3; // Default correlation for crypto
          if (positions[i].returns.length >= 10 && positions[j].returns.length >= 10) {
            corr = pearsonCorrelation(positions[i].returns, positions[j].returns);
          }
          const cov = positions[i].volatility * positions[j].volatility * corr;
          covMatrix[i][j] = cov;
          covMatrix[j][i] = cov;
        }
      }
    }

    return covMatrix;
  }

  /**
   * Compute historical portfolio returns from position returns.
   */
  private computePortfolioReturns(positions: Position[], weights: Vector): Vector {
    if (positions.length === 0) return [];

    // Find minimum return length
    const minLen = Math.min(...positions.map(p => p.returns.length));
    if (minLen === 0) return [];

    const portfolioReturns: Vector = new Array(minLen).fill(0);
    for (let t = 0; t < minLen; t++) {
      for (let i = 0; i < positions.length; i++) {
        portfolioReturns[t] += weights[i] * (positions[i].returns[t] ?? 0);
      }
    }

    return portfolioReturns;
  }

  // ============================================================
  // PRIVATE HELPERS — OPTIMIZATION
  // ============================================================

  /**
   * Mean-Variance Optimization (Markowitz MPT).
   * Finds the portfolio with maximum Sharpe ratio.
   * w* = (Σ⁻¹ μ) / (1' Σ⁻¹ μ)
   */
  private optimizeMeanVariance(covMatrix: Matrix, expectedReturns: Vector): Vector {
    const n = covMatrix.length;
    if (n === 0) return [];

    const invCov = invertMatrixRegularized(covMatrix, 1e-6);
    if (invCov.length === 0) return new Array(n).fill(1 / n);

    // w = Σ⁻¹ × μ
    const rawWeights = matVecMul(invCov, expectedReturns);

    // Normalize: w = w / (1' × w)
    const sum = rawWeights.reduce((s, w) => s + w, 0);
    if (Math.abs(sum) < 1e-12) return new Array(n).fill(1 / n);

    return rawWeights.map(w => w / sum);
  }

  /**
   * Risk Parity — equal risk contribution from each asset.
   * Uses the Spinu (2013) iterative algorithm.
   */
  private optimizeRiskParity(covMatrix: Matrix, vols: Vector): Vector {
    const n = covMatrix.length;
    if (n === 0) return [];

    // Start with inverse-volatility weights
    const invVols = vols.map(v => v > 0 ? 1 / v : 0);
    const invVolSum = invVols.reduce((s, v) => s + v, 0);
    let weights: Vector = invVolSum > 0
      ? invVols.map(iv => iv / invVolSum)
      : new Array(n).fill(1 / n);

    // Iterative risk parity
    const maxIter = 100;
    const tol = 1e-8;

    for (let iter = 0; iter < maxIter; iter++) {
      const sigma2 = this.portfolioVariance(weights, covMatrix);
      if (sigma2 <= 0) break;

      const marginalRisk = matVecMul(covMatrix, weights);
      const riskContribs = weights.map((w, i) => w * marginalRisk[i] / Math.sqrt(sigma2));
      const totalRC = riskContribs.reduce((s, rc) => s + rc, 0);
      if (totalRC === 0) break;

      const targetRC = totalRC / n;

      // Check convergence
      const maxDeviation = Math.max(...riskContribs.map(rc => Math.abs(rc - targetRC)));
      if (maxDeviation < tol) break;

      // Square-root update for stability
      weights = weights.map((w, i) => {
        if (riskContribs[i] <= 0) return w;
        return w * Math.sqrt(targetRC / riskContribs[i]);
      });

      // Normalize
      const wSum = weights.reduce((s, w) => s + w, 0);
      if (wSum > 0) weights = weights.map(w => w / wSum);
    }

    return weights;
  }

  /**
   * Minimum Variance Portfolio.
   * w = Σ⁻¹1 / (1'Σ⁻¹1)
   */
  private optimizeMinVariance(covMatrix: Matrix): Vector {
    const n = covMatrix.length;
    if (n === 0) return [];

    const invCov = invertMatrixRegularized(covMatrix, 1e-6);
    if (invCov.length === 0) return new Array(n).fill(1 / n);

    const ones = new Array(n).fill(1);
    const invCovOnes = matVecMul(invCov, ones);
    const denom = ones.reduce((sum, _, i) => sum + invCovOnes[i], 0);

    if (Math.abs(denom) < 1e-12) return new Array(n).fill(1 / n);

    return invCovOnes.map(v => v / denom);
  }

  /**
   * Maximum Diversification Portfolio.
   * Maximizes the diversification ratio: (w'σ) / √(w'Σw)
   * Solution: w ∝ Σ⁻¹σ
   */
  private optimizeMaxDiversification(covMatrix: Matrix, vols: Vector): Vector {
    const n = covMatrix.length;
    if (n === 0) return [];

    const invCov = invertMatrixRegularized(covMatrix, 1e-6);
    if (invCov.length === 0) return new Array(n).fill(1 / n);

    // w ∝ Σ⁻¹ × σ
    const rawWeights = matVecMul(invCov, vols);

    // Normalize
    const sum = rawWeights.reduce((s, w) => s + w, 0);
    if (Math.abs(sum) < 1e-12) return new Array(n).fill(1 / n);

    return rawWeights.map(w => w / sum);
  }

  /**
   * Black-Litterman model.
   * Combines market equilibrium with investor views.
   *
   * E[R] = [(τΣ)⁻¹ + P'Ω⁻¹P]⁻¹ × [(τΣ)⁻¹Π + P'Ω⁻¹Q]
   *
   * Where:
   *   τ = scalar (uncertainty in prior)
   *   Σ = covariance matrix
   *   Π = implied equilibrium returns (reverse optimization)
   *   P = pick matrix (which assets each view applies to)
   *   Ω = diagonal matrix of view uncertainties
   *   Q = view vector (expected returns)
   */
  private optimizeBlackLitterman(
    covMatrix: Matrix,
    positions: Position[],
    views?: BlackLittermanView[],
    tau: number = 0.05,
  ): Vector {
    const n = covMatrix.length;
    if (n === 0) return [];

    // Implied equilibrium returns: Π = δ × Σ × w_mkt
    // Using market-cap weights as proxy for w_mkt
    const delta = 2.5; // Risk aversion coefficient
    const wMkt = new Array(n).fill(1 / n); // Equal weight as proxy
    const pi: Vector = matVecMul(
      covMatrix.map(row => row.map(v => v * delta)),
      wMkt,
    );

    if (!views || views.length === 0) {
      // No views — return equilibrium
      const sum = pi.reduce((s, v) => s + Math.max(v, 0), 0);
      if (sum === 0) return new Array(n).fill(1 / n);
      return pi.map(v => Math.max(v, 0)).map(v => v / sum);
    }

    // Build pick matrix P and view vector Q
    const k = views.length;
    const P: Matrix = Array.from({ length: k }, () => new Array(n).fill(0));
    const Q: Vector = new Array(k).fill(0);
    const omega: Matrix = Array.from({ length: k }, () => new Array(k).fill(0));

    for (let v = 0; v < k; v++) {
      const view = views[v];
      const assetIdx = positions.findIndex(p => p.tokenAddress === view.tokenAddress);
      if (assetIdx >= 0) {
        P[v][assetIdx] = 1;
        Q[v] = view.expectedReturn;
        // View uncertainty inversely proportional to confidence
        omega[v][v] = (1 - view.confidence) * 0.1;
      }
    }

    // τΣ
    const tauCov = covMatrix.map(row => row.map(v => v * tau));

    // (τΣ)⁻¹
    const invTauCov = invertMatrixRegularized(tauCov, 1e-6);
    if (invTauCov.length === 0) return new Array(n).fill(1 / n);

    // Ω⁻¹
    const invOmega = invertMatrixRegularized(omega, 1e-6);

    // P'Ω⁻¹P
    const pTransOmegaInv = matMul(transpose(P), invOmega);
    const pTransOmegaInvP = matMul(pTransOmegaInv, P);

    // (τΣ)⁻¹ + P'Ω⁻¹P
    const combined: Matrix = invTauCov.map((row, i) =>
      row.map((val, j) => val + (pTransOmegaInvP[i]?.[j] ?? 0))
    );

    const invCombined = invertMatrixRegularized(combined, 1e-6);
    if (invCombined.length === 0) return new Array(n).fill(1 / n);

    // P'Ω⁻¹Q
    const pTransOmegaInvQ = matVecMul(pTransOmegaInv, Q);

    // (τΣ)⁻¹Π
    const invTauCovPi = matVecMul(invTauCov, pi);

    // Combined right side
    const rightSide: Vector = invTauCovPi.map((v, i) => v + (pTransOmegaInvQ[i] ?? 0));

    // E[R] = invCombined × rightSide
    const blReturns = matVecMul(invCombined, rightSide);

    // Compute optimal weights from BL returns using mean-variance
    return this.optimizeMeanVariance(covMatrix, blReturns);
  }

  // ============================================================
  // PRIVATE HELPERS — STRESS TESTING
  // ============================================================

  /**
   * Run a single stress scenario.
   */
  private runStressScenario(
    positions: Position[],
    scenario: StressScenario,
    totalValue: number,
  ): StressTestScenarioResult {
    const positionImpacts = new Map<string, number>();
    let portfolioImpactUsd = 0;

    const halfIdx = Math.floor(positions.length / 2);

    for (let i = 0; i < positions.length; i++) {
      const pos = positions[i];
      let shock: number;

      // Check for specific token shock
      if (scenario.shocks.has(pos.tokenAddress)) {
        shock = scenario.shocks.get(pos.tokenAddress)!;
      } else if (scenario.shocks.has('__HALF_POSITIVE__') && i < halfIdx) {
        shock = scenario.shocks.get('__HALF_POSITIVE__')!;
      } else if (scenario.shocks.has('__HALF_NEGATIVE__') && i >= halfIdx) {
        shock = scenario.shocks.get('__HALF_NEGATIVE__')!;
      } else {
        // Apply broad market shock, adjusted by position volatility
        // More volatile positions get amplified shock
        const volMultiplier = pos.volatility > 0 ? pos.volatility / 0.7 : 1; // 0.7 = avg crypto vol
        shock = scenario.marketShock * Math.max(volMultiplier, 0.5);
      }

      const impactUsd = pos.sizeUsd * shock;
      positionImpacts.set(pos.tokenAddress, Math.round(impactUsd * 100) / 100);
      portfolioImpactUsd += impactUsd;
    }

    // Adjust for correlation multiplier (higher correlations amplify losses)
    if (scenario.correlationMultiplier > 1) {
      const concentrationPenalty = 1 + (scenario.correlationMultiplier - 1) * 0.3;
      portfolioImpactUsd *= concentrationPenalty;
    }

    const portfolioImpactPct = totalValue > 0 ? portfolioImpactUsd / totalValue : 0;

    // Recovery estimate: days to recover based on expected daily return
    // Using heuristic: expected daily return ≈ 0.05% for a diversified crypto portfolio
    const expectedDailyReturn = 0.0005;
    const recoveryDaysEstimate = portfolioImpactPct < 0
      ? Math.ceil(Math.abs(portfolioImpactPct) / expectedDailyReturn)
      : 0;

    return {
      scenarioId: scenario.id,
      scenarioName: scenario.name,
      portfolioImpactUsd: Math.round(portfolioImpactUsd * 100) / 100,
      portfolioImpactPct: Math.round(portfolioImpactPct * 10000) / 10000,
      positionImpacts,
      recoveryDaysEstimate,
    };
  }

  // ============================================================
  // PRIVATE HELPERS — CORRELATION
  // ============================================================

  /**
   * Simplified DCC (Dynamic Conditional Correlation).
   * Uses exponential weighting to give more weight to recent observations.
   */
  private dccSimplified(x: Vector, y: Vector, lambda: number = 0.94): number {
    const n = Math.min(x.length, y.length);
    if (n < 5) return pearsonCorrelation(x, y);

    // Compute exponentially weighted covariance
    const mx = mean(x);
    const my = mean(y);

    let ewCovXY = 0;
    let ewVarX = 0;
    let ewVarY = 0;
    let weightSum = 0;

    for (let i = 0; i < n; i++) {
      const decay = Math.pow(lambda, n - 1 - i);
      const dx = x[i] - mx;
      const dy = y[i] - my;

      ewCovXY += decay * dx * dy;
      ewVarX += decay * dx * dx;
      ewVarY += decay * dy * dy;
      weightSum += decay;
    }

    if (weightSum === 0 || ewVarX < 1e-12 || ewVarY < 1e-12) return 0;

    const corr = (ewCovXY / weightSum) /
      Math.sqrt((ewVarX / weightSum) * (ewVarY / weightSum));

    return Math.max(-1, Math.min(1, corr));
  }

  /**
   * Compute correlation of a proposed position with existing positions.
   */
  private async computeCorrelationWithExisting(
    proposed: ProposedPosition,
    existing: Position[],
  ): Promise<number> {
    if (existing.length === 0) return 0;

    const correlations: Vector = [];

    for (const pos of existing) {
      if (proposed.returns.length >= 10 && pos.returns.length >= 10) {
        correlations.push(pearsonCorrelation(proposed.returns, pos.returns));
      } else {
        // Default: same sector = 0.5, different sector = 0.3
        correlations.push(proposed.sector === pos.sector ? 0.5 : 0.3);
      }
    }

    return correlations.length > 0 ? mean(correlations) : 0;
  }

  /**
   * Fetch daily returns from PriceCandle table.
   */
  private async getReturnsFromDB(tokenAddress: string, chain: string): Promise<Vector> {
    try {
      const candles = await db.priceCandle.findMany({
        where: {
          tokenAddress,
          chain,
          timeframe: '1d',
        },
        orderBy: { timestamp: 'asc' },
        take: 30,
      });

      if (candles.length < 2) return [];

      const returns: Vector = [];
      for (let i = 1; i < candles.length; i++) {
        const prevClose = candles[i - 1]?.close ?? 0;
        const currClose = candles[i]?.close ?? 0;
        if (prevClose > 0) {
          returns.push(currClose / prevClose - 1);
        }
      }
      return returns;
    } catch {
      return [];
    }
  }

  // ============================================================
  // PRIVATE HELPERS — CONSTRAINTS
  // ============================================================

  /**
   * Find groups of highly correlated assets (>60% correlation = same bucket).
   */
  private findCorrelatedBuckets(
    positions: Position[],
    threshold: number,
  ): Array<{ tokens: string[]; avgCorrelation: number }> {
    const n = positions.length;
    if (n < 2) return [];

    // Compute pairwise correlations
    const correlationCache = new Map<string, number>();
    const getCorr = (i: number, j: number): number => {
      if (i === j) return 1;
      const key = `${Math.min(i, j)}:${Math.max(i, j)}`;
      if (correlationCache.has(key)) return correlationCache.get(key)!;

      const corr = (positions[i].returns.length >= 10 && positions[j].returns.length >= 10)
        ? pearsonCorrelation(positions[i].returns, positions[j].returns)
        : 0.3; // Default

      correlationCache.set(key, corr);
      return corr;
    };

    // Union-Find to group correlated assets
    const parent = new Array(n).fill(0).map((_, i) => i);
    const find = (x: number): number => {
      while (parent[x] !== x) {
        parent[x] = parent[parent[x]];
        x = parent[x];
      }
      return x;
    };
    const union = (x: number, y: number): void => {
      const px = find(x);
      const py = find(y);
      if (px !== py) parent[px] = py;
    };

    // Group assets with correlation > threshold
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (getCorr(i, j) > threshold) {
          union(i, j);
        }
      }
    }

    // Collect buckets (only those with 2+ members)
    const buckets = new Map<number, number[]>();
    for (let i = 0; i < n; i++) {
      const root = find(i);
      if (!buckets.has(root)) buckets.set(root, []);
      buckets.get(root)!.push(i);
    }

    const result: Array<{ tokens: string[]; avgCorrelation: number }> = [];
    for (const [, indices] of buckets) {
      if (indices.length < 2) continue;

      // Compute average pairwise correlation within bucket
      let totalCorr = 0;
      let pairCount = 0;
      for (let i = 0; i < indices.length; i++) {
        for (let j = i + 1; j < indices.length; j++) {
          totalCorr += getCorr(indices[i], indices[j]);
          pairCount++;
        }
      }

      result.push({
        tokens: indices.map(idx => positions[idx].tokenAddress),
        avgCorrelation: pairCount > 0 ? totalCorr / pairCount : 0,
      });
    }

    return result;
  }

  // ============================================================
  // PRIVATE HELPERS — UTILITY
  // ============================================================

  /**
   * Convert a ProposedPosition to a Position (for hypothetical portfolio construction).
   */
  private proposedToPosition(proposed: ProposedPosition, totalPortfolioValue: number): Position {
    return {
      id: `proposed_${proposed.tokenAddress}`,
      tokenAddress: proposed.tokenAddress,
      symbol: proposed.symbol,
      chain: proposed.chain,
      sector: proposed.sector,
      sizeUsd: proposed.proposedSizeUsd,
      entryPrice: 0, // Unknown for proposed position
      currentPrice: 0, // Unknown for proposed position
      unrealizedPnl: 0,
      unrealizedPnlPct: 0,
      weight: proposed.proposedSizeUsd / totalPortfolioValue,
      volatility: proposed.expectedVolatility,
      returns: proposed.returns,
      marketCapTier: proposed.marketCapTier,
      strategyId: proposed.strategyId,
      openedAt: new Date(),
    };
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const portfolioIntelligenceEngine = new PortfolioIntelligenceEngine();
