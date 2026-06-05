/**
 * Risk Pre-Filter — CryptoQuant Terminal
 *
 * Sits BEFORE the Decision Engine to kill invalid signals early,
 * saving computation resources. Runs a sequential filter chain
 * ordered by computational cost (cheapest first) so that
 * clearly invalid signals are rejected before expensive checks.
 *
 * Filter Chain Order:
 *   a) Hard Vetoes       — zero computation (kill switch, blacklist, liquidity, spread)
 *   b) Portfolio Constraints — light computation (concentration, exposure, capital, limits)
 *   c) Market Regime     — medium computation (regime-signal compatibility)
 *   d) Correlation Check — medium computation (position/strategy correlation)
 *   e) Data Quality Gate — light computation (candle count, quality score, staleness)
 *   f) VaR Budget Check  — medium computation (parametric VaR estimation)
 *
 * Each filter records its computation time for observability.
 * Warnings reduce adjusted confidence; rejections kill the signal immediately.
 */

import { killSwitchService } from './kill-switch-service';
import { regimeHeuristic } from '../strategy/regime-heuristic';
import { dataQualityGate } from './data-quality-gate';
import { strategyCorrelationService } from './strategy-correlation-service';
import { db } from '../../db';

// ============================================================
// TYPES
// ============================================================

export interface TradeSignal {
  tokenAddress: string;
  chain: string;
  direction: 'LONG' | 'SHORT';
  confidence: number;       // 0-1
  strategyName: string;
  signalType: 'MOMENTUM' | 'MEAN_REVERSION' | 'BREAKOUT' | 'DEFENSIVE' | 'EXIT';
  sizeUsd: number;
  /** Spread in percentage terms (e.g. 1.5 = 1.5%) */
  spreadPct?: number;
  /** Liquidity in USD */
  liquidityUsd?: number;
  /** Sector classification (if known) */
  sector?: string;
  /** Number of candles available for this token */
  candleCount?: number;
  /** Data quality score 0-1 */
  dataQualityScore?: number;
  /** Age of last price data in minutes */
  priceDataAgeMinutes?: number;
  /** Volatility estimate for VaR (daily, as decimal e.g. 0.05 = 5%) */
  volatilityEstimate?: number;
}

/**
 * Represents an open position in the portfolio for pre-filter evaluation.
 * Distinct from other position types in the system — this is the lightweight
 * view used by the RiskPreFilter only.
 */
export interface PreFilterOpenPosition {
  tokenAddress: string;
  chain: string;
  sizeUsd: number;
  pnlPct: number;
  direction: 'LONG' | 'SHORT';
  /** Strategy name that opened this position */
  strategyName?: string;
  /** Sector classification */
  sector?: string;
}

/**
 * Portfolio state snapshot for the RiskPreFilter.
 * Distinct from kill-switch-service's PortfolioState (which uses concentration maps);
 * this version uses an array of open positions for position-level checks.
 */
export interface PreFilterPortfolioState {
  totalCapital: number;
  freeCapital: number;
  openPositions: PreFilterOpenPosition[];
  /** Current drawdown as decimal (0.05 = 5%) */
  currentDD: number;
  /** Daily PnL as percentage (negative = loss) */
  dailyPnL: number;
}

export interface FilterDetail {
  filterName: string;
  passed: boolean;
  reason?: string;
  computationTimeMs: number;
}

export interface PreFilterResult {
  /** Whether the signal survived all filters */
  passed: boolean;
  /** Hard rejection reasons — signal should NOT proceed */
  rejectionReasons: string[];
  /** Soft warnings — signal proceeds with reduced confidence/size */
  warnings: string[];
  /** Aggregate risk score 0-1 (higher = riskier) */
  riskScore: number;
  /** Confidence after adjustments (may be lower than input) */
  adjustedConfidence: number;
  /** Per-filter execution details */
  filterDetails: FilterDetail[];
}

// ============================================================
// CONFIGURATION
// ============================================================

/** Minimum liquidity in USD for a token to be tradeable */
const MIN_LIQUIDITY_USD = 10_000;
/** Maximum spread percentage allowed */
const MAX_SPREAD_PCT = 3;
/** Maximum single-token concentration (fraction of portfolio) */
const MAX_TOKEN_CONCENTRATION = 0.15;
/** Maximum chain exposure (fraction of portfolio) */
const MAX_CHAIN_EXPOSURE = 0.50;
/** Maximum sector exposure (fraction of portfolio) */
const MAX_SECTOR_EXPOSURE = 0.30;
/** Maximum correlated exposure (fraction of portfolio) */
const MAX_CORRELATED_EXPOSURE = 0.40;
/** Maximum number of open positions */
const MAX_OPEN_POSITIONS = 30;
/** Daily loss limit as fraction of total capital */
const DAILY_LOSS_LIMIT = 0.05;
/** Minimum candle count for data quality */
const MIN_CANDLE_COUNT = 100;
/** Minimum data quality score */
const MIN_DATA_QUALITY_SCORE = 0.5;
/** Maximum price data staleness in minutes (warning threshold) */
const MAX_STALE_DATA_MINUTES = 5;
/** Daily VaR limit as fraction of portfolio */
const DAILY_VAR_LIMIT = 0.05;
/** VaR budget warning threshold (fraction of limit consumed) */
const VAR_BUDGET_WARN_THRESHOLD = 0.80;
/** Correlation threshold for position warning */
const CORRELATION_WARN_THRESHOLD = 0.70;
/** Portfolio correlation concentration warning */
const PORTFOLIO_CORRELATION_WARN = 0.60;
/** Confidence floor — below this, signal is rejected */
const CONFIDENCE_FLOOR = 0.20;

// Confidence penalty constants
const PENALTY_REGIME_MISMATCH = 0.15;
const PENALTY_HIGH_CORRELATION = 0.10;
const PENALTY_STALE_DATA = 0.10;
const PENALTY_APPROACHING_LIMIT = 0.05;
const PENALTY_GENERIC_WARNING = 0.05;

// ============================================================
// TOKEN BLACKLIST (could be moved to DB in production)
// ============================================================

const TOKEN_BLACKLIST = new Set<string>([
  // Add known scam/rug-pull tokens here
]);

// ============================================================
// RISK PRE-FILTER CLASS
// ============================================================

class RiskPreFilter {
  // ----------------------------------------------------------
  // MAIN FILTER METHOD
  // ----------------------------------------------------------

  /**
   * Run the full pre-filter chain on a trade signal.
   * Returns early on first hard rejection to save computation.
   */
  async filter(signal: TradeSignal, portfolioState: PreFilterPortfolioState): Promise<PreFilterResult> {
    const rejectionReasons: string[] = [];
    const warnings: string[] = [];
    const filterDetails: FilterDetail[] = [];
    let adjustedConfidence = signal.confidence;
    let riskScore = 0;

    // ---- (a) HARD VETOES ----
    // These are essentially free — no DB or computation needed.
    {
      const t0 = performance.now();

      // Global kill switch
      const ksState = killSwitchService.getState();
      if (ksState.globalPause) {
        rejectionReasons.push(`Global kill switch active: ${ksState.globalPauseReason || 'Manual emergency pause'}`);
        filterDetails.push({
          filterName: 'HARD_VETO_GLOBAL_KILL_SWITCH',
          passed: false,
          reason: `Global kill switch active: ${ksState.globalPauseReason || 'Manual emergency pause'}`,
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 1.0, adjustedConfidence, filterDetails);
      }
      filterDetails.push({ filterName: 'HARD_VETO_GLOBAL_KILL_SWITCH', passed: true, computationTimeMs: performance.now() - t0 });
    }

    {
      const t0 = performance.now();
      // Strategy-specific pause
      const ksState = killSwitchService.getState();
      const strategyPause = ksState.strategyPauses.get(signal.strategyName);
      if (strategyPause?.paused) {
        rejectionReasons.push(`Strategy "${signal.strategyName}" is paused: ${strategyPause.reason}`);
        filterDetails.push({
          filterName: 'HARD_VETO_STRATEGY_PAUSE',
          passed: false,
          reason: `Strategy "${signal.strategyName}" is paused: ${strategyPause.reason}`,
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 1.0, adjustedConfidence, filterDetails);
      }
      filterDetails.push({ filterName: 'HARD_VETO_STRATEGY_PAUSE', passed: true, computationTimeMs: performance.now() - t0 });
    }

    {
      const t0 = performance.now();
      // Token blacklist
      if (TOKEN_BLACKLIST.has(signal.tokenAddress)) {
        rejectionReasons.push(`Token ${signal.tokenAddress} is blacklisted`);
        filterDetails.push({
          filterName: 'HARD_VETO_TOKEN_BLACKLIST',
          passed: false,
          reason: `Token ${signal.tokenAddress} is blacklisted`,
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 1.0, adjustedConfidence, filterDetails);
      }
      filterDetails.push({ filterName: 'HARD_VETO_TOKEN_BLACKLIST', passed: true, computationTimeMs: performance.now() - t0 });
    }

    {
      const t0 = performance.now();
      // Insufficient liquidity
      if (signal.liquidityUsd !== undefined && signal.liquidityUsd < MIN_LIQUIDITY_USD) {
        rejectionReasons.push(`Insufficient liquidity: $${signal.liquidityUsd.toLocaleString()} < $${MIN_LIQUIDITY_USD.toLocaleString()} minimum`);
        filterDetails.push({
          filterName: 'HARD_VETO_LIQUIDITY',
          passed: false,
          reason: `Insufficient liquidity: $${signal.liquidityUsd.toLocaleString()} < $${MIN_LIQUIDITY_USD.toLocaleString()} minimum`,
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 1.0, adjustedConfidence, filterDetails);
      }
      filterDetails.push({ filterName: 'HARD_VETO_LIQUIDITY', passed: true, computationTimeMs: performance.now() - t0 });
    }

    {
      const t0 = performance.now();
      // Spread too wide
      if (signal.spreadPct !== undefined && signal.spreadPct > MAX_SPREAD_PCT) {
        rejectionReasons.push(`Spread too wide: ${signal.spreadPct.toFixed(2)}% > ${MAX_SPREAD_PCT}% maximum`);
        filterDetails.push({
          filterName: 'HARD_VETO_SPREAD',
          passed: false,
          reason: `Spread too wide: ${signal.spreadPct.toFixed(2)}% > ${MAX_SPREAD_PCT}% maximum`,
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 1.0, adjustedConfidence, filterDetails);
      }
      filterDetails.push({ filterName: 'HARD_VETO_SPREAD', passed: true, computationTimeMs: performance.now() - t0 });
    }

    // ---- (b) PORTFOLIO CONSTRAINTS ----
    {
      const t0 = performance.now();
      const constraintResult = this.checkPortfolioConstraints(signal, portfolioState);
      if (!constraintResult.passed) {
        rejectionReasons.push(...constraintResult.rejections);
        filterDetails.push({
          filterName: 'PORTFOLIO_CONSTRAINTS',
          passed: false,
          reason: constraintResult.rejections.join('; '),
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 0.8, adjustedConfidence, filterDetails);
      }
      if (constraintResult.warnings.length > 0) {
        warnings.push(...constraintResult.warnings);
        adjustedConfidence -= PENALTY_APPROACHING_LIMIT * constraintResult.warnings.length;
        riskScore += 0.05 * constraintResult.warnings.length;
      }
      filterDetails.push({ filterName: 'PORTFOLIO_CONSTRAINTS', passed: true, computationTimeMs: performance.now() - t0 });
    }

    // ---- (c) MARKET REGIME FILTER ----
    {
      const t0 = performance.now();
      const regimeResult = await this.checkMarketRegime(signal);
      if (!regimeResult.passed) {
        rejectionReasons.push(regimeResult.reason!);
        filterDetails.push({
          filterName: 'MARKET_REGIME_FILTER',
          passed: false,
          reason: regimeResult.reason,
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 0.7, adjustedConfidence, filterDetails);
      }
      if (regimeResult.warning) {
        warnings.push(regimeResult.warning);
        adjustedConfidence -= PENALTY_REGIME_MISMATCH;
        riskScore += 0.10;
      }
      filterDetails.push({
        filterName: 'MARKET_REGIME_FILTER',
        passed: true,
        reason: regimeResult.warning || undefined,
        computationTimeMs: performance.now() - t0,
      });
    }

    // ---- (d) CORRELATION CHECK ----
    {
      const t0 = performance.now();
      const corrResult = await this.checkCorrelation(signal, portfolioState);
      if (corrResult.warnings.length > 0) {
        warnings.push(...corrResult.warnings);
        adjustedConfidence -= PENALTY_HIGH_CORRELATION;
        riskScore += 0.08;
      }
      filterDetails.push({
        filterName: 'CORRELATION_CHECK',
        passed: true,
        reason: corrResult.warnings.length > 0 ? corrResult.warnings.join('; ') : undefined,
        computationTimeMs: performance.now() - t0,
      });
    }

    // ---- (e) DATA QUALITY GATE ----
    {
      const t0 = performance.now();
      const dqResult = this.checkDataQuality(signal);
      if (!dqResult.passed) {
        rejectionReasons.push(dqResult.reason!);
        filterDetails.push({
          filterName: 'DATA_QUALITY_GATE',
          passed: false,
          reason: dqResult.reason,
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 0.6, adjustedConfidence, filterDetails);
      }
      if (dqResult.warning) {
        warnings.push(dqResult.warning);
        adjustedConfidence -= PENALTY_STALE_DATA;
        riskScore += 0.05;
      }
      filterDetails.push({
        filterName: 'DATA_QUALITY_GATE',
        passed: true,
        reason: dqResult.warning || undefined,
        computationTimeMs: performance.now() - t0,
      });
    }

    // ---- (f) VaR BUDGET CHECK ----
    {
      const t0 = performance.now();
      const varResult = this.checkVaRBudget(signal, portfolioState);
      if (!varResult.passed) {
        rejectionReasons.push(varResult.reason!);
        filterDetails.push({
          filterName: 'VAR_BUDGET_CHECK',
          passed: false,
          reason: varResult.reason,
          computationTimeMs: performance.now() - t0,
        });
        return this.buildResult(false, rejectionReasons, warnings, 0.9, adjustedConfidence, filterDetails);
      }
      if (varResult.warning) {
        warnings.push(varResult.warning);
        adjustedConfidence -= PENALTY_APPROACHING_LIMIT;
        riskScore += 0.05;
      }
      filterDetails.push({
        filterName: 'VAR_BUDGET_CHECK',
        passed: true,
        reason: varResult.warning || undefined,
        computationTimeMs: performance.now() - t0,
      });
    }

    // ---- FINAL CONFIDENCE CHECK ----
    adjustedConfidence = Math.max(0, adjustedConfidence);
    if (adjustedConfidence < CONFIDENCE_FLOOR) {
      rejectionReasons.push(`Adjusted confidence ${ (adjustedConfidence * 100).toFixed(1)}% below floor ${ (CONFIDENCE_FLOOR * 100).toFixed(0)}%`);
      return this.buildResult(false, rejectionReasons, warnings, riskScore, adjustedConfidence, filterDetails);
    }

    // Cap risk score at 1.0
    riskScore = Math.min(1, riskScore);

    return this.buildResult(true, rejectionReasons, warnings, riskScore, adjustedConfidence, filterDetails);
  }

  // ----------------------------------------------------------
  // (b) PORTFOLIO CONSTRAINTS
  // ----------------------------------------------------------

  private checkPortfolioConstraints(
    signal: TradeSignal,
    portfolio: PreFilterPortfolioState,
  ): { passed: boolean; rejections: string[]; warnings: string[] } {
    const rejections: string[] = [];
    const warningsList: string[] = [];

    if (portfolio.totalCapital <= 0) {
      rejections.push('Portfolio has no capital');
      return { passed: false, rejections, warnings: warningsList };
    }

    // Token concentration
    const currentTokenSize = portfolio.openPositions
      .filter(p => p.tokenAddress === signal.tokenAddress)
      .reduce((sum, p) => sum + p.sizeUsd, 0);
    const newTokenPct = (currentTokenSize + signal.sizeUsd) / portfolio.totalCapital;
    if (newTokenPct > MAX_TOKEN_CONCENTRATION) {
      rejections.push(
        `Token concentration would exceed ${(MAX_TOKEN_CONCENTRATION * 100).toFixed(0)}%: ` +
        `${(newTokenPct * 100).toFixed(1)}% (current ${(currentTokenSize / portfolio.totalCapital * 100).toFixed(1)}% + ${(signal.sizeUsd / portfolio.totalCapital * 100).toFixed(1)}%)`,
      );
    } else if (newTokenPct > MAX_TOKEN_CONCENTRATION * 0.8) {
      warningsList.push(`Token concentration approaching limit: ${(newTokenPct * 100).toFixed(1)}% of ${(MAX_TOKEN_CONCENTRATION * 100).toFixed(0)}%`);
    }

    // Chain exposure
    const currentChainSize = portfolio.openPositions
      .filter(p => p.chain === signal.chain)
      .reduce((sum, p) => sum + p.sizeUsd, 0);
    const newChainPct = (currentChainSize + signal.sizeUsd) / portfolio.totalCapital;
    if (newChainPct > MAX_CHAIN_EXPOSURE) {
      rejections.push(
        `Chain exposure would exceed ${(MAX_CHAIN_EXPOSURE * 100).toFixed(0)}% for ${signal.chain}: ` +
        `${(newChainPct * 100).toFixed(1)}%`,
      );
    } else if (newChainPct > MAX_CHAIN_EXPOSURE * 0.8) {
      warningsList.push(`Chain exposure approaching limit for ${signal.chain}: ${(newChainPct * 100).toFixed(1)}% of ${(MAX_CHAIN_EXPOSURE * 100).toFixed(0)}%`);
    }

    // Sector exposure
    const sector = signal.sector || killSwitchService.inferSector(signal.tokenAddress, signal.chain);
    const currentSectorSize = portfolio.openPositions
      .filter(p => (p.sector || killSwitchService.inferSector(p.tokenAddress, p.chain)) === sector)
      .reduce((sum, p) => sum + p.sizeUsd, 0);
    const newSectorPct = (currentSectorSize + signal.sizeUsd) / portfolio.totalCapital;
    if (newSectorPct > MAX_SECTOR_EXPOSURE) {
      rejections.push(
        `Sector exposure would exceed ${(MAX_SECTOR_EXPOSURE * 100).toFixed(0)}% for ${sector}: ` +
        `${(newSectorPct * 100).toFixed(1)}%`,
      );
    } else if (newSectorPct > MAX_SECTOR_EXPOSURE * 0.8) {
      warningsList.push(`Sector exposure approaching limit for ${sector}: ${(newSectorPct * 100).toFixed(1)}% of ${(MAX_SECTOR_EXPOSURE * 100).toFixed(0)}%`);
    }

    // Correlated exposure (same direction, same chain)
    const correlatedSize = portfolio.openPositions
      .filter(p => p.chain === signal.chain && p.direction === signal.direction)
      .reduce((sum, p) => sum + p.sizeUsd, 0);
    const newCorrelatedPct = (correlatedSize + signal.sizeUsd) / portfolio.totalCapital;
    if (newCorrelatedPct > MAX_CORRELATED_EXPOSURE) {
      rejections.push(
        `Correlated exposure would exceed ${(MAX_CORRELATED_EXPOSURE * 100).toFixed(0)}%: ` +
        `${(newCorrelatedPct * 100).toFixed(1)}%`,
      );
    } else if (newCorrelatedPct > MAX_CORRELATED_EXPOSURE * 0.8) {
      warningsList.push(`Correlated exposure approaching limit: ${(newCorrelatedPct * 100).toFixed(1)}% of ${(MAX_CORRELATED_EXPOSURE * 100).toFixed(0)}%`);
    }

    // Max open positions
    if (portfolio.openPositions.length >= MAX_OPEN_POSITIONS) {
      rejections.push(
        `Already at max open positions: ${portfolio.openPositions.length}/${MAX_OPEN_POSITIONS}`,
      );
    } else if (portfolio.openPositions.length >= MAX_OPEN_POSITIONS * 0.8) {
      warningsList.push(`Approaching max open positions: ${portfolio.openPositions.length}/${MAX_OPEN_POSITIONS}`);
    }

    // Insufficient free capital
    if (signal.sizeUsd > portfolio.freeCapital) {
      rejections.push(
        `Insufficient free capital: $${signal.sizeUsd.toLocaleString()} needed, $${portfolio.freeCapital.toLocaleString()} available`,
      );
    } else if (signal.sizeUsd > portfolio.freeCapital * 0.8) {
      warningsList.push(`Position would consume >80% of free capital: $${signal.sizeUsd.toLocaleString()} / $${portfolio.freeCapital.toLocaleString()}`);
    }

    // Daily loss limit
    if (portfolio.dailyPnL < 0) {
      const dailyLossPct = Math.abs(portfolio.dailyPnL);
      if (dailyLossPct > DAILY_LOSS_LIMIT * 100) {
        rejections.push(
          `Daily loss limit reached: ${dailyLossPct.toFixed(1)}% > ${(DAILY_LOSS_LIMIT * 100).toFixed(0)}% limit`,
        );
      } else if (dailyLossPct > DAILY_LOSS_LIMIT * 100 * 0.8) {
        warningsList.push(`Approaching daily loss limit: ${dailyLossPct.toFixed(1)}% of ${(DAILY_LOSS_LIMIT * 100).toFixed(0)}%`);
      }
    }

    return { passed: rejections.length === 0, rejections, warnings: warningsList };
  }

  // ----------------------------------------------------------
  // (c) MARKET REGIME FILTER
  // ----------------------------------------------------------

  private async checkMarketRegime(
    signal: TradeSignal,
  ): Promise<{ passed: boolean; reason?: string; warning?: string }> {
    try {
      // Get current regime from DB
      const assessment = await regimeHeuristic.assessRegimeFromDB(signal.tokenAddress, signal.chain);
      const regime = assessment.regime;

      // PANIC regime (HIGH_VOLATILITY with strong downtrend) — only allow defensive/exit
      if (regime === 'HIGH_VOLATILITY' && assessment.trendDirection === 'DOWN' && assessment.trendStrength > 0.5) {
        if (signal.signalType !== 'DEFENSIVE' && signal.signalType !== 'EXIT') {
          return {
            passed: false,
            reason: `Market in PANIC regime (HIGH_VOLATILITY + strong downtrend) — only defensive/exit signals allowed, got ${signal.signalType}`,
          };
        }
      }

      // HIGH_VOLATILITY (general) — warn and reduce
      if (regime === 'HIGH_VOLATILITY') {
        return {
          passed: true,
          warning: `Market in HIGH_VOLATILITY regime (vol percentile: ${assessment.volatilityPercentile.toFixed(0)}) — position size should be reduced by 50%`,
        };
      }

      // EUPHORIA (TRENDING_UP with extreme strength) — reduce position size by 50%
      if (regime === 'TRENDING_UP' && assessment.trendStrength > 0.7 && assessment.confidence > 0.8) {
        return {
          passed: true,
          warning: `Market in EUPHORIA regime (strong uptrend, confidence ${assessment.confidence.toFixed(2)}) — position size should be reduced by 50%`,
        };
      }

      // RANGING/SIDEWAYS — reject momentum signals
      if (regime === 'SIDEWAYS' && signal.signalType === 'MOMENTUM') {
        return {
          passed: false,
          reason: `Market in RANGING/SIDEWAYS regime — momentum signals rejected`,
        };
      }

      // TRENDING_DOWN — warn for LONG momentum
      if (regime === 'TRENDING_DOWN' && signal.direction === 'LONG' && signal.signalType === 'MOMENTUM') {
        return {
          passed: true,
          warning: `Market in downtrend — LONG momentum signals are counter-trend, reduce confidence`,
        };
      }

      return { passed: true };
    } catch (error) {
      // Fail open on regime errors — don't block signals
      console.warn('[RiskPreFilter] Regime check error (fail-open):', error);
      return { passed: true, warning: 'Regime assessment unavailable — proceeding with caution' };
    }
  }

  // ----------------------------------------------------------
  // (d) CORRELATION CHECK
  // ----------------------------------------------------------

  private async checkCorrelation(
    signal: TradeSignal,
    portfolio: PreFilterPortfolioState,
  ): Promise<{ warnings: string[] }> {
    const warnings: string[] = [];

    try {
      // Position-level correlation: check if new position correlates >70% with existing
      const sameDirectionPositions = portfolio.openPositions.filter(
        p => p.direction === signal.direction,
      );

      for (const pos of sameDirectionPositions) {
        const correlation = this.estimatePositionCorrelation(signal, pos);
        if (correlation > CORRELATION_WARN_THRESHOLD) {
          warnings.push(
            `New position correlation ${(correlation * 100).toFixed(0)}% > ${CORRELATION_WARN_THRESHOLD * 100}% with existing ${pos.tokenAddress} position — consider reducing size`,
          );
          break; // Only warn once for the highest correlation
        }
      }

      // Strategy-level correlation using strategyCorrelationService
      const corrMatrix = await strategyCorrelationService.getCurrentCorrelationMatrix();
      const avgCorr = strategyCorrelationService.getAverageCorrelation(corrMatrix);
      if (avgCorr > PORTFOLIO_CORRELATION_WARN) {
        warnings.push(
          `Portfolio correlation concentration ${(avgCorr * 100).toFixed(1)}% > ${PORTFOLIO_CORRELATION_WARN * 100}% — diversification benefit reduced`,
        );
      }
    } catch (error) {
      // Fail open — correlation check shouldn't block signals
      console.warn('[RiskPreFilter] Correlation check error (fail-open):', error);
    }

    return { warnings };
  }

  /**
   * Estimate correlation between a new signal and an existing position.
   * Uses chain + sector overlap as a proxy for correlation.
   * Same token = 0.95, same chain+sector = 0.75, same chain = 0.50, otherwise = 0.20.
   */
  private estimatePositionCorrelation(signal: TradeSignal, position: PreFilterOpenPosition): number {
    // Same token → very high correlation
    if (signal.tokenAddress === position.tokenAddress) return 0.95;

    // Same chain + same sector → high correlation
    const signalSector = signal.sector || killSwitchService.inferSector(signal.tokenAddress, signal.chain);
    const posSector = position.sector || killSwitchService.inferSector(position.tokenAddress, position.chain);
    const sameChain = signal.chain === position.chain;
    const sameSector = signalSector === posSector;

    if (sameChain && sameSector) return 0.75;
    if (sameChain) return 0.50;
    if (sameSector) return 0.60;

    return 0.20;
  }

  // ----------------------------------------------------------
  // (e) DATA QUALITY GATE
  // ----------------------------------------------------------

  private checkDataQuality(
    signal: TradeSignal,
  ): { passed: boolean; reason?: string; warning?: string } {
    // Token has < 100 candles → REJECT
    if (signal.candleCount !== undefined && signal.candleCount < MIN_CANDLE_COUNT) {
      return {
        passed: false,
        reason: `Insufficient candle data: ${signal.candleCount} < ${MIN_CANDLE_COUNT} minimum`,
      };
    }

    // Data quality score < 0.5 → REJECT
    if (signal.dataQualityScore !== undefined && signal.dataQualityScore < MIN_DATA_QUALITY_SCORE) {
      return {
        passed: false,
        reason: `Data quality score too low: ${signal.dataQualityScore.toFixed(2)} < ${MIN_DATA_QUALITY_SCORE} minimum`,
      };
    }

    // Price data stale > 5 minutes → WARN, reduce confidence
    if (signal.priceDataAgeMinutes !== undefined && signal.priceDataAgeMinutes > MAX_STALE_DATA_MINUTES) {
      return {
        passed: true,
        warning: `Price data is ${signal.priceDataAgeMinutes} minutes stale (max ${MAX_STALE_DATA_MINUTES} min for real-time) — confidence reduced`,
      };
    }

    return { passed: true };
  }

  // ----------------------------------------------------------
  // (f) VaR BUDGET CHECK
  // ----------------------------------------------------------

  private checkVaRBudget(
    signal: TradeSignal,
    portfolio: PreFilterPortfolioState,
  ): { passed: boolean; reason?: string; warning?: string } {
    if (portfolio.totalCapital <= 0) {
      return { passed: false, reason: 'Portfolio has no capital for VaR calculation' };
    }

    // Simple parametric VaR: position_size * volatility * z_score(95%)
    // Using 1.645 for 95% confidence level
    const Z_SCORE_95 = 1.645;
    const volatility = signal.volatilityEstimate || 0.05; // Default 5% daily vol

    // Calculate current portfolio VaR (sum of individual position VaRs — conservative)
    const currentPortfolioVaR = portfolio.openPositions.reduce((sum, pos) => {
      // Use a default volatility for existing positions if not available
      const posVol = 0.05; // Conservative 5% daily
      return sum + pos.sizeUsd * posVol * Z_SCORE_95;
    }, 0);

    // New position VaR
    const newPositionVaR = signal.sizeUsd * volatility * Z_SCORE_95;

    // Total VaR after adding new position
    const totalVaR = currentPortfolioVaR + newPositionVaR;
    const varLimit = portfolio.totalCapital * DAILY_VAR_LIMIT;

    // Adding position would exceed daily VaR limit → REJECT
    if (totalVaR > varLimit) {
      return {
        passed: false,
        reason: `Adding position would exceed daily VaR limit: $${totalVaR.toFixed(0)} > $${varLimit.toFixed(0)} (${(DAILY_VAR_LIMIT * 100).toFixed(0)}% of portfolio)`,
      };
    }

    // Current portfolio VaR already > 80% of budget → WARN, reduce size
    if (totalVaR > varLimit * VAR_BUDGET_WARN_THRESHOLD) {
      return {
        passed: true,
        warning: `Portfolio VaR approaching limit: $${totalVaR.toFixed(0)} / $${varLimit.toFixed(0)} (${((totalVaR / varLimit) * 100).toFixed(0)}% of budget) — consider reducing position size`,
      };
    }

    return { passed: true };
  }

  // ----------------------------------------------------------
  // HELPERS
  // ----------------------------------------------------------

  private buildResult(
    passed: boolean,
    rejectionReasons: string[],
    warnings: string[],
    riskScore: number,
    adjustedConfidence: number,
    filterDetails: FilterDetail[],
  ): PreFilterResult {
    return {
      passed,
      rejectionReasons,
      warnings,
      riskScore: Math.min(1, Math.max(0, riskScore)),
      adjustedConfidence: Math.min(1, Math.max(0, adjustedConfidence)),
      filterDetails,
    };
  }

  // ----------------------------------------------------------
  // BLACKLIST MANAGEMENT
  // ----------------------------------------------------------

  /** Add a token to the blacklist */
  addToBlacklist(tokenAddress: string): void {
    TOKEN_BLACKLIST.add(tokenAddress);
  }

  /** Remove a token from the blacklist */
  removeFromBlacklist(tokenAddress: string): void {
    TOKEN_BLACKLIST.delete(tokenAddress);
  }

  /** Check if a token is blacklisted */
  isBlacklisted(tokenAddress: string): boolean {
    return TOKEN_BLACKLIST.has(tokenAddress);
  }

  /** Get all blacklisted tokens */
  getBlacklistedTokens(): string[] {
    return Array.from(TOKEN_BLACKLIST);
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const riskPreFilter = new RiskPreFilter();
