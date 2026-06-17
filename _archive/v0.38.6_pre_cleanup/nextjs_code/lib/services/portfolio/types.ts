/**
 * Portfolio Intelligence Engine — Types
 * CryptoQuant Terminal
 *
 * Complete type definitions for the Portfolio Intelligence Engine.
 * All types are strictly typed — no `any` types.
 *
 * This module provides portfolio-level evaluation of new positions,
 * considering the impact on the ENTIRE portfolio before capital allocation.
 */

// ============================================================
// POSITION & PORTFOLIO TYPES
// ============================================================

/** A single position within the portfolio */
export interface Position {
  /** Unique identifier for this position */
  id: string;
  /** Token contract address */
  tokenAddress: string;
  /** Human-readable token symbol (e.g. "BTC", "ETH", "SOL") */
  symbol: string;
  /** Blockchain the token lives on */
  chain: string;
  /** Market sector classification */
  sector: string;
  /** Current position size in USD */
  sizeUsd: number;
  /** Entry price in USD */
  entryPrice: number;
  /** Current market price in USD */
  currentPrice: number;
  /** Unrealized PnL in USD */
  unrealizedPnl: number;
  /** Unrealized PnL as percentage */
  unrealizedPnlPct: number;
  /** Weight of this position in the portfolio (0-1) */
  weight: number;
  /** Annualized volatility of this position (0-1) */
  volatility: number;
  /** Daily returns for this position (fractional, e.g. 0.05 = +5%) */
  returns: number[];
  /** Market cap tier: determines concentration limits */
  marketCapTier: MarketCapTier;
  /** Strategy ID that opened this position */
  strategyId: string | null;
  /** Timestamp when the position was opened */
  openedAt: Date;
}

/** Market cap tier — determines max concentration limits */
export type MarketCapTier = 'MEGA' | 'LARGE' | 'MID' | 'SMALL' | 'MICRO';

/** Map of market cap tier to max concentration percentage */
export const MARKET_CAP_TIER_LIMITS: Record<MarketCapTier, number> = {
  MEGA: 15,  // BTC, ETH — up to 15%
  LARGE: 10, // Top 20 — up to 10%
  MID: 7,    // Top 100 — up to 7%
  SMALL: 5,  // Top 500 — up to 5%
  MICRO: 3,  // Long tail — up to 3%
};

// ============================================================
// PORTFOLIO STATE
// ============================================================

/** Complete portfolio snapshot */
export interface PortfolioState {
  /** All current positions */
  positions: Position[];
  /** Correlation matrix between all positions */
  correlationMatrix: CorrelationMatrix;
  /** Sector exposure breakdown: sector -> percentage */
  sectorExposure: Map<string, number>;
  /** Chain exposure breakdown: chain -> percentage */
  chainExposure: Map<string, number>;
  /** Portfolio-level risk metrics */
  riskMetrics: PortfolioRiskMetrics;
  /** Total portfolio value in USD */
  totalValueUsd: number;
  /** Total unrealized PnL in USD */
  totalUnrealizedPnl: number;
  /** Available (unallocated) capital in USD */
  availableCapital: number;
  /** Allocated capital in USD */
  allocatedCapital: number;
  /** Timestamp of this snapshot */
  snapshotAt: Date;
}

// ============================================================
// PROPOSED POSITION & IMPACT
// ============================================================

/** A position being considered for addition to the portfolio */
export interface ProposedPosition {
  /** Token contract address */
  tokenAddress: string;
  /** Human-readable token symbol */
  symbol: string;
  /** Blockchain */
  chain: string;
  /** Market sector classification */
  sector: string;
  /** Proposed position size in USD */
  proposedSizeUsd: number;
  /** Expected annualized volatility (0-1) */
  expectedVolatility: number;
  /** Expected annual return (fractional) */
  expectedReturn: number;
  /** Market cap tier */
  marketCapTier: MarketCapTier;
  /** Daily returns history if available */
  returns: number[];
  /** Strategy proposing this position */
  strategyId: string | null;
}

/** Impact assessment of adding a new position to the portfolio */
export interface PortfolioImpact {
  /** Whether the position is approved for addition */
  approved: boolean;
  /** Composite impact score: -1 (very negative) to +1 (very positive) */
  impactScore: number;
  /** Marginal risk contribution of the new position (fractional) */
  riskContribution: number;
  /** Change in diversification ratio (positive = more diversified) */
  diversificationDelta: number;
  /** Change in portfolio VaR in USD (positive = VaR increased) */
  varDelta: number;
  /** Average correlation of the new position with existing positions */
  correlationWithExisting: number;
  /** Actionable recommendations for the position */
  recommendations: string[];
}

// ============================================================
// PORTFOLIO RISK METRICS
// ============================================================

/** Comprehensive portfolio risk metrics */
export interface PortfolioRiskMetrics {
  /** Portfolio annualized volatility (weighted, with correlation) */
  portfolioVolatility: number;
  /** Parametric Value at Risk at 95% confidence */
  var95: number;
  /** Parametric Value at Risk at 99% confidence */
  var99: number;
  /** Historical Value at Risk at 95% (if enough data) */
  historicalVar95: number | null;
  /** Conditional VaR (Expected Shortfall) at 95% */
  cvar95: number;
  /** Diversification ratio: weighted avg vol / portfolio vol */
  diversificationRatio: number;
  /** Herfindahl-Hirschman Index for concentration (0 = perfectly diversified, 1 = single position) */
  hhi: number;
  /** Estimated maximum drawdown as fraction */
  maxDrawdownEstimate: number;
  /** Time horizon in days used for VaR calculations */
  timeHorizonDays: number;
  /** Timestamp when metrics were computed */
  computedAt: Date;
}

// ============================================================
// OPTIMIZATION TYPES
// ============================================================

/** Available portfolio optimization methods */
export type OptimizationMethod =
  | 'MEAN_VARIANCE'
  | 'RISK_PARITY'
  | 'MIN_VARIANCE'
  | 'MAX_DIVERSIFICATION'
  | 'BLACK_LITTERMAN';

/** Result of portfolio weight optimization */
export interface OptimalWeights {
  /** Map of token address -> optimal weight (0-1) */
  weights: Map<string, number>;
  /** Expected portfolio return (annualized, fractional) */
  expectedReturn: number;
  /** Expected portfolio volatility (annualized, fractional) */
  expectedVol: number;
  /** Sharpe ratio of the optimized portfolio */
  sharpeRatio: number;
  /** The optimization method used */
  method: OptimizationMethod;
}

/** User views for Black-Litterman model */
export interface BlackLittermanView {
  /** Token address this view applies to */
  tokenAddress: string;
  /** Expected return for this token (annualized, fractional) */
  expectedReturn: number;
  /** Confidence in this view (0-1) */
  confidence: number;
}

// ============================================================
// STRESS TEST TYPES
// ============================================================

/** A stress test scenario */
export interface StressScenario {
  /** Unique scenario identifier */
  id: string;
  /** Human-readable scenario name */
  name: string;
  /** Scenario description */
  description: string;
  /** Shocks to apply: token address -> price change (fractional, e.g. -0.20 = -20%) */
  shocks: Map<string, number>;
  /** Broad market shock: applies to all positions not in shocks map */
  marketShock: number;
  /** Correlation adjustment: multiplier applied to all correlations (1.0 = no change, 2.0 = double) */
  correlationMultiplier: number;
  /** Whether this is a predefined scenario */
  isPredefined: boolean;
}

/** Result of a stress test for a single scenario */
export interface StressTestScenarioResult {
  /** Scenario identifier */
  scenarioId: string;
  /** Scenario name */
  scenarioName: string;
  /** Portfolio impact in USD (negative = loss) */
  portfolioImpactUsd: number;
  /** Portfolio impact as percentage */
  portfolioImpactPct: number;
  /** Per-position impact: token address -> USD impact */
  positionImpacts: Map<string, number>;
  /** Estimated time to recover in days (heuristic) */
  recoveryDaysEstimate: number;
}

/** Complete stress test result */
export interface StressTestResult {
  /** Results for each scenario */
  scenarioResults: StressTestScenarioResult[];
  /** Worst case scenario result */
  worstCase: StressTestScenarioResult;
  /** Average portfolio impact across all scenarios */
  averageImpactPct: number;
  /** Timestamp when the test was run */
  computedAt: Date;
}

// ============================================================
// CORRELATION MATRIX
// ============================================================

/** Correlation matrix for portfolio positions */
export interface CorrelationMatrix {
  /** Token addresses in order corresponding to matrix indices */
  tokens: string[];
  /** NxN correlation matrix */
  matrix: number[][];
  /** Correlation method used */
  method: CorrelationMethod;
  /** Number of data points used */
  dataPoints: number;
  /** Timestamp when the matrix was computed */
  computedAt: Date;
}

/** Correlation computation method */
export type CorrelationMethod = 'PEARSON' | 'SPEARMAN' | 'DCC_SIMPLIFIED';

// ============================================================
// CONSTRAINT CHECK TYPES
// ============================================================

/** Result of checking portfolio constraints */
export interface ConstraintCheck {
  /** Whether all constraints are satisfied */
  passed: boolean;
  /** Individual constraint violations */
  violations: ConstraintViolation[];
  /** Overall constraint health score: 0 (many violations) to 1 (all clear) */
  healthScore: number;
}

/** A single constraint violation */
export interface ConstraintViolation {
  /** Constraint type that was violated */
  type: ConstraintType;
  /** Human-readable description */
  message: string;
  /** Current value */
  currentValue: number;
  /** Limit value */
  limitValue: number;
  /** Severity: how badly the constraint is violated */
  severity: 'WARNING' | 'CRITICAL';
}

/** Types of portfolio constraints */
export type ConstraintType =
  | 'MAX_TOKEN_CONCENTRATION'
  | 'MAX_CHAIN_EXPOSURE'
  | 'MAX_SECTOR_EXPOSURE'
  | 'MAX_CORRELATED_ASSETS'
  | 'MIN_DIVERSIFICATION_RATIO'
  | 'MAX_VAR_BUDGET';

// ============================================================
// LINEAR ALGEBRA TYPES
// ============================================================

/** Internal representation of a matrix for linear algebra operations */
export type Matrix = number[][];

/** Internal representation of a vector */
export type Vector = number[];

/** Result of eigenvalue decomposition */
export interface EigenResult {
  /** Eigenvalues sorted in descending order */
  values: number[];
  /** Eigenvectors corresponding to eigenvalues */
  vectors: Matrix;
}
