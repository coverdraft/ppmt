/**
 * Parameter Drift Analyzer — CryptoQuant Terminal
 *
 * Analyzes how strategy parameters drift across Walk-Forward windows.
 * High drift = unstable strategy = overfitting risk.
 * Low drift = stable parameters = more reliable strategy.
 *
 * For each parameter extracted from WF window results, we compute:
 *   - Mean, StdDev across windows
 *   - Coefficient of Variation (CV = StdDev / Mean) — relative drift measure
 *   - Drift category: STABLE / MODERATE / DRIFTING / UNSTABLE
 *
 * Core parameters (stopLoss, takeProfit, confidenceThreshold) are weighted
 * more heavily in the overall drift score because they define the strategy's
 * risk profile. If they drift, the strategy's behavior changes fundamentally.
 */

import type { WalkForwardWindow } from '../backtesting/walk-forward-engine';

// ============================================================
// TYPES
// ============================================================

export type DriftCategory = 'STABLE' | 'MODERATE' | 'DRIFTING' | 'UNSTABLE';

export interface ParameterDrift {
  parameter: string;
  valuesPerWindow: number[];
  meanValue: number;
  stdDev: number;
  coefficientOfVariation: number; // stdDev/mean (relative drift)
  driftCategory: DriftCategory;
}

export interface ParameterDriftResult {
  strategyId: string;
  overallDriftScore: number; // 0-100 (0 = no drift, 100 = extreme drift)
  parameterDrifts: ParameterDrift[];
  windowCount: number;
  recommendation: string;
  isStable: boolean; // overallDriftScore < 30
}

// ============================================================
// PARAMETER WEIGHTS — Core params count more
// ============================================================

const PARAMETER_WEIGHTS: Record<string, number> = {
  // Core risk params — high weight
  stopLoss: 2.0,
  takeProfit: 2.0,
  stopLossPct: 2.0,
  takeProfitPct: 2.0,
  trailingStopPercent: 1.5,
  confidenceThreshold: 1.5,
  maxPositionPct: 1.5,
  maxDrawdown: 1.5,

  // Signal params — moderate weight
  takeProfitRatio: 1.0,
  riskRewardRatio: 1.0,
  winRate: 1.0,
  sharpeRatio: 0.8,
  profitFactor: 0.8,

  // Other params — lower weight
  avgHoldTimeMin: 0.5,
  totalTrades: 0.3,
  maxConcurrentTrades: 0.5,
};

const DEFAULT_PARAMETER_WEIGHT = 0.8;

// ============================================================
// PARAMETER EXTRACTOR
// ============================================================

/**
 * Extract numeric parameters from a WF window's backtest result.
 *
 * We extract from the backtest result's config.system (SystemTemplate),
 * which contains the parameters used for that specific window's backtest.
 * If the system template isn't available, we fall back to extracting
 * metrics from the result itself (winRate, sharpe, etc.)
 */
function extractParametersFromWindow(window: WalkForwardWindow): Record<string, number> {
  const params: Record<string, number> = {};

  // Try to extract from the system config in the in-sample result
  const result = window.inSampleResult || window.outOfSampleResult;
  if (!result) return params;

  // Extract from the system template config
  const system = result.config?.system;
  if (system) {
    // Risk management params
    const rm = system.riskManagement as Record<string, unknown> | undefined;
    if (rm) {
      if (typeof rm.maxDrawdown === 'number') params.maxDrawdown = rm.maxDrawdown;
      if (typeof rm.maxConcurrentTrades === 'number') params.maxConcurrentTrades = rm.maxConcurrentTrades;
      if (typeof rm.maxDailyLoss === 'number') params.maxDailyLoss = rm.maxDailyLoss;
    }

    // Exit signal params
    const es = system.exitSignal as Record<string, unknown> | undefined;
    if (es) {
      if (typeof es.takeProfit === 'number') params.takeProfit = es.takeProfit;
      if (typeof es.stopLoss === 'number') params.stopLoss = es.stopLoss;
      if (typeof es.trailingStopPercent === 'number') params.trailingStopPercent = es.trailingStopPercent;
      if (typeof es.timeBasedExit === 'number') params.timeBasedExit = es.timeBasedExit;
    }

    // Entry signal params
    const is = system.entrySignal as Record<string, unknown> | undefined;
    if (is) {
      if (typeof is.confidenceThreshold === 'number') params.confidenceThreshold = is.confidenceThreshold;
    }

    // Execution config params
    const ec = system.executionConfig as Record<string, unknown> | undefined;
    if (ec) {
      if (typeof ec.maxPositionSize === 'number') params.maxPositionSize = ec.maxPositionSize;
      if (typeof ec.slippageTolerance === 'number') params.slippageTolerance = ec.slippageTolerance;
    }
  }

  // Also extract key performance metrics as proxy parameters
  // (If system params are identical across windows, these are what actually drift)
  if (result.winRate !== undefined) params.winRate = result.winRate;
  if (result.sharpeRatio !== undefined) params.sharpeRatio = result.sharpeRatio;
  if (result.profitFactor !== undefined) params.profitFactor = result.profitFactor;
  if (result.avgWinPct !== undefined) params.avgWinPct = result.avgWinPct;
  if (result.avgLossPct !== undefined) params.avgLossPct = Math.abs(result.avgLossPct);
  if (result.avgHoldTimeMin !== undefined) params.avgHoldTimeMin = result.avgHoldTimeMin;
  if (result.totalTrades !== undefined) params.totalTrades = result.totalTrades;

  // Derived: risk-reward ratio
  if (params.avgWinPct && params.avgLossPct && params.avgLossPct > 0) {
    params.riskRewardRatio = params.avgWinPct / params.avgLossPct;
  }

  return params;
}

// ============================================================
// DRIFT CLASSIFIER
// ============================================================

/**
 * Classify the drift level based on the coefficient of variation.
 *
 * CV < 0.1  → STABLE    (parameters barely change)
 * CV 0.1-0.25 → MODERATE (some drift, but within reason)
 * CV 0.25-0.5 → DRIFTING (significant drift — strategy is changing)
 * CV > 0.5  → UNSTABLE  (parameters are all over the place)
 */
function classifyDrift(cv: number): DriftCategory {
  if (cv < 0.1) return 'STABLE';
  if (cv < 0.25) return 'MODERATE';
  if (cv < 0.5) return 'DRIFTING';
  return 'UNSTABLE';
}

// ============================================================
// OVERALL SCORE COMPUTATION
// ============================================================

/**
 * Compute the overall drift score from individual parameter drifts.
 *
 * Uses weighted average where core parameters (stopLoss, takeProfit, etc.)
 * count more than peripheral ones. The score is normalized to 0-100.
 *
 * Formula:
 *   overallScore = sum(weight_i * cv_i * 100) / sum(weight_i)
 *   Capped to [0, 100]
 */
function computeOverallScore(drifts: ParameterDrift[]): number {
  if (drifts.length === 0) return 50; // No data — assume moderate drift

  let weightedSum = 0;
  let totalWeight = 0;

  for (const drift of drifts) {
    const weight = PARAMETER_WEIGHTS[drift.parameter] ?? DEFAULT_PARAMETER_WEIGHT;
    weightedSum += weight * drift.coefficientOfVariation;
    totalWeight += weight;
  }

  if (totalWeight === 0) return 50;

  // Convert to 0-100 scale
  const score = (weightedSum / totalWeight) * 100;
  return Math.round(Math.min(100, Math.max(0, score)) * 100) / 100;
}

// ============================================================
// RECOMMENDATION GENERATOR
// ============================================================

function generateRecommendation(result: ParameterDriftResult): string {
  if (result.overallDriftScore < 15) {
    return 'Parameters are very stable across windows — strategy is robust and reliable.';
  }
  if (result.overallDriftScore < 30) {
    return 'Parameters are mostly stable — strategy is suitable for deployment with monitoring.';
  }
  if (result.overallDriftScore < 50) {
    return 'Moderate parameter drift detected — strategy may need parameter constraints or simplified rules.';
  }
  if (result.overallDriftScore < 75) {
    return 'Significant parameter drift — strategy is likely overfitting. Consider reducing parameter count or using more regularized optimization.';
  }
  return 'Extreme parameter drift — strategy is unstable across windows. Do NOT deploy. Re-design with fewer, more robust parameters.';
}

// ============================================================
// PARAMETER DRIFT ANALYZER CLASS
// ============================================================

class ParameterDriftAnalyzer {
  /**
   * Analyze drift from walk-forward window results.
   *
   * For each numeric parameter found across windows, compute:
   * - Values per window
   * - Mean and standard deviation
   * - Coefficient of variation (relative drift measure)
   * - Drift category
   *
   * Then combine into an overall drift score with weighted parameters.
   */
  analyzeDrift(windows: WalkForwardWindow[], strategyId: string = 'unknown'): ParameterDriftResult {
    // Step 1: Extract parameters from each window
    const allParamsPerWindow: Record<string, number[]> = {};

    for (const window of windows) {
      const params = extractParametersFromWindow(window);
      for (const [key, value] of Object.entries(params)) {
        if (!isFinite(value) || isNaN(value)) continue;
        if (!allParamsPerWindow[key]) {
          allParamsPerWindow[key] = [];
        }
        allParamsPerWindow[key].push(value);
      }
    }

    // Step 2: Compute drift for each parameter
    const parameterDrifts: ParameterDrift[] = [];

    for (const [parameter, values] of Object.entries(allParamsPerWindow)) {
      if (values.length < 2) continue; // Need at least 2 values to compute drift

      const n = values.length;
      const meanValue = values.reduce((s, v) => s + v, 0) / n;

      // Skip parameters with near-zero mean (CV would be meaningless)
      if (Math.abs(meanValue) < 1e-10) continue;

      const variance = values.reduce((s, v) => s + (v - meanValue) ** 2, 0) / Math.max(1, n - 1);
      const stdDev = Math.sqrt(variance);
      const coefficientOfVariation = Math.abs(stdDev / meanValue);

      const driftCategory = classifyDrift(coefficientOfVariation);

      parameterDrifts.push({
        parameter,
        valuesPerWindow: values,
        meanValue: Math.round(meanValue * 10000) / 10000,
        stdDev: Math.round(stdDev * 10000) / 10000,
        coefficientOfVariation: Math.round(coefficientOfVariation * 10000) / 10000,
        driftCategory,
      });
    }

    // Step 3: Compute overall drift score
    const overallDriftScore = computeOverallScore(parameterDrifts);

    // Step 4: Build result
    const result: ParameterDriftResult = {
      strategyId,
      overallDriftScore,
      parameterDrifts,
      windowCount: windows.length,
      recommendation: '', // Will be set below
      isStable: overallDriftScore < 30,
    };

    result.recommendation = generateRecommendation(result);

    return result;
  }

  /**
   * Classify drift level from coefficient of variation.
   * Public accessor for use outside the analyzer.
   */
  classifyDrift(cv: number): DriftCategory {
    return classifyDrift(cv);
  }

  /**
   * Get overall drift score from individual parameter drifts.
   * Public accessor for use outside the analyzer.
   */
  computeOverallScore(drifts: ParameterDrift[]): number {
    return computeOverallScore(drifts);
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const parameterDriftAnalyzer = new ParameterDriftAnalyzer();
