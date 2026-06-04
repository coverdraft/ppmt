/**
 * Walk-Forward Analysis Engine - CryptoQuant Terminal
 *
 * Prevents overfitting by validating trading systems on out-of-sample data.
 *
 * Walk-Forward Process:
 * 1. Split historical data into N windows (train/test pairs)
 * 2. Optimize parameters on training window
 * 3. Validate on test window (out-of-sample)
 * 4. Compare in-sample vs out-of-sample performance
 * 5. Calculate degradation metrics
 *
 * A system that performs well in-sample but poorly out-of-sample is OVERFIT.
 * A robust system shows consistent performance across both.
 *
 * WFA Metrics:
 * - Walk-Forward Efficiency (WFE): out-of-sample return / in-sample return
 * - WFE > 50% = good, > 70% = excellent, < 30% = likely overfit
 * - Parameter stability across windows
 * - Performance consistency (std dev of returns across windows)
 */

import {
  BacktestingEngine,
  type BacktestConfig,
  type BacktestResult,
  type TokenData,
} from './backtesting-engine';
import type { SystemTemplate, TokenPhase } from './trading-system-engine';

// ============================================================
// 1. TYPES & INTERFACES
// ============================================================

export interface WalkForwardWindow {
  windowIndex: number;
  trainStart: Date;
  trainEnd: Date;
  testStart: Date;
  testEnd: Date;
  inSampleResult: BacktestResult | null;
  outOfSampleResult: BacktestResult | null;
  degradationPct: number; // how much performance degraded from train to test
  wfe: number; // walk-forward efficiency
}

export interface WalkForwardConfig {
  system: SystemTemplate;
  startDate: Date;
  endDate: Date;
  /** Number of windows (default: 5) */
  windowCount: number;
  /** Ratio of train/test split within each window (default: 0.7 = 70% train, 30% test) */
  trainRatio: number;
  /** Initial capital per window */
  initialCapital: number;
  /** Trading fee pct */
  feesPct: number;
  /** Slippage pct */
  slippagePct: number;
  /** Minimum WFE for a system to be considered robust (default: 0.5) */
  minWFE: number;
  /** Anchored WFA: each training window starts from the beginning (default: false = rolling) */
  anchored: boolean;
}

export interface WalkForwardResult {
  id: string;
  systemName: string;
  config: WalkForwardConfig;
  windows: WalkForwardWindow[];
  aggregateWFE: number; // weighted average WFE across all windows
  avgInSampleReturn: number;
  avgOutOfSampleReturn: number;
  performanceConsistency: number; // std dev of OOS returns (lower = more consistent)
  isRobust: boolean; // aggregateWFE >= minWFE
  recommendation: 'ROBUST' | 'MARGINAL' | 'OVERFIT' | 'INSUFFICIENT_DATA';
  overallDegradation: number;
  parameterStability: number; // 0-1, consistency of performance across windows
  generatedAt: Date;
}

export interface WalkForwardProgress {
  percentComplete: number;
  currentWindow: number;
  totalWindows: number;
  currentPhase: 'TRAINING' | 'TESTING';
}

// ============================================================
// 2. INTERNAL HELPERS
// ============================================================

/** Generate a unique ID for WFA results */
function generateWFAId(): string {
  return `wfa_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

/**
 * Filter token data to only include bars within a given date range.
 * Tokens with no bars in the range are excluded.
 */
function filterTokensByDateRange(
  tokens: TokenData[],
  startDate: Date,
  endDate: Date,
): TokenData[] {
  return tokens
    .map((token) => {
      const filteredBars = token.bars.filter(
        (b) =>
          b.timestamp >= startDate.getTime() &&
          b.timestamp <= endDate.getTime(),
      );

      if (filteredBars.length === 0) return null;

      // Filter metricsPerBar to match filtered bars
      let filteredMetrics = token.metricsPerBar;
      if (token.metricsPerBar && token.metricsPerBar.length !== filteredBars.length) {
        // Find the start index in the original bars array
        const startIdx = token.bars.findIndex(
          (b) => b.timestamp >= startDate.getTime(),
        );
        // findLastIndex polyfill: iterate from end to find last matching index
        let endIdx = -1;
        for (let k = token.bars.length - 1; k >= 0; k--) {
          if (token.bars[k].timestamp <= endDate.getTime()) {
            endIdx = k;
            break;
          }
        }
        if (startIdx >= 0 && endIdx >= startIdx) {
          filteredMetrics = token.metricsPerBar.slice(startIdx, endIdx + 1);
        } else {
          filteredMetrics = [];
        }
      }

      return {
        ...token,
        bars: filteredBars,
        metricsPerBar: filteredMetrics,
      } as TokenData;
    })
    .filter((t): t is TokenData => t !== null && t.bars.length > 0);
}

/**
 * Calculate standard deviation of an array of numbers.
 */
function standardDeviation(values: number[]): number {
  if (values.length < 2) return 0;
  const n = values.length;
  const mean = values.reduce((s, v) => s + v, 0) / n;
  const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1);
  return Math.sqrt(variance);
}

/**
 * Calculate weighted average WFE across all windows.
 * Weight is based on the number of trades in the out-of-sample period,
 * giving more weight to windows with more trading activity.
 */
function calculateWeightedWFE(windows: WalkForwardWindow[]): number {
  let totalWeight = 0;
  let weightedSum = 0;

  for (const window of windows) {
    const oosTrades = window.outOfSampleResult?.totalTrades ?? 0;
    const isTrades = window.inSampleResult?.totalTrades ?? 0;

    // Weight by the number of trades (more trades = more statistically significant)
    const weight = oosTrades + isTrades;

    if (weight > 0) {
      weightedSum += window.wfe * weight;
      totalWeight += weight;
    }
  }

  if (totalWeight === 0) {
    // Fall back to simple average if no trades
    const validWindows = windows.filter((w) => isFinite(w.wfe));
    if (validWindows.length === 0) return 0;
    return validWindows.reduce((s, w) => s + w.wfe, 0) / validWindows.length;
  }

  return weightedSum / totalWeight;
}

// ============================================================
// 3. WALK-FORWARD ENGINE CLASS
// ============================================================

export class WalkForwardEngine {
  private backtestingEngine: BacktestingEngine;

  constructor() {
    this.backtestingEngine = new BacktestingEngine();
  }

  // ----------------------------------------------------------
  // Main Entry Point
  // ----------------------------------------------------------

  /**
   * Run a full Walk-Forward Analysis.
   *
   * Splits the historical data into N windows. For each window:
   *   1. Run backtest on the training period (in-sample)
   *   2. Run backtest on the test period (out-of-sample)
   *   3. Calculate degradation and WFE
   *
   * Then aggregates results across all windows to produce a final
   * robustness assessment.
   */
  async runWalkForwardAnalysis(
    config: WalkForwardConfig,
    tokens: TokenData[],
    onProgress?: (p: WalkForwardProgress) => void,
  ): Promise<WalkForwardResult> {
    // Step 1: Calculate window date ranges
    const windows = this.calculateWindowDates(
      config.startDate,
      config.endDate,
      config.windowCount,
      config.trainRatio,
      config.anchored,
    );

    const totalSteps = windows.length * 2; // train + test per window
    let completedSteps = 0;

    // Step 2: For each window, run train then test backtest
    for (let i = 0; i < windows.length; i++) {
      const window = windows[i];

      // --- Training (in-sample) ---
      if (onProgress) {
        onProgress({
          percentComplete: (completedSteps / totalSteps) * 100,
          currentWindow: i + 1,
          totalWindows: windows.length,
          currentPhase: 'TRAINING',
        });
      }

      const trainTokens = filterTokensByDateRange(
        tokens,
        window.trainStart,
        window.trainEnd,
      );

      if (trainTokens.length > 0) {
        const trainConfig: BacktestConfig = {
          system: config.system,
          mode: 'HISTORICAL',
          startDate: window.trainStart,
          endDate: window.trainEnd,
          initialCapital: config.initialCapital,
          feesPct: config.feesPct,
          slippagePct: config.slippagePct,
          applySlippage: true,
          enforcePhaseFilter: true,
        };

        try {
          window.inSampleResult = await this.backtestingEngine.runBacktest(
            trainConfig,
            trainTokens,
          );
        } catch {
          window.inSampleResult = null;
        }
      } else {
        window.inSampleResult = null;
      }

      completedSteps++;

      // --- Testing (out-of-sample) ---
      if (onProgress) {
        onProgress({
          percentComplete: (completedSteps / totalSteps) * 100,
          currentWindow: i + 1,
          totalWindows: windows.length,
          currentPhase: 'TESTING',
        });
      }

      const testTokens = filterTokensByDateRange(
        tokens,
        window.testStart,
        window.testEnd,
      );

      if (testTokens.length > 0) {
        const testConfig: BacktestConfig = {
          system: config.system,
          mode: 'HISTORICAL',
          startDate: window.testStart,
          endDate: window.testEnd,
          initialCapital: config.initialCapital,
          feesPct: config.feesPct,
          slippagePct: config.slippagePct,
          applySlippage: true,
          enforcePhaseFilter: true,
        };

        try {
          window.outOfSampleResult = await this.backtestingEngine.runBacktest(
            testConfig,
            testTokens,
          );
        } catch {
          window.outOfSampleResult = null;
        }
      } else {
        window.outOfSampleResult = null;
      }

      completedSteps++;

      // Calculate window-level metrics
      const trainReturn = window.inSampleResult?.totalReturnPct ?? 0;
      const testReturn = window.outOfSampleResult?.totalReturnPct ?? 0;

      // Degradation: how much performance dropped from train to test
      // Positive degradation means out-of-sample was worse
      if (trainReturn !== 0) {
        window.degradationPct =
          ((trainReturn - testReturn) / Math.abs(trainReturn)) * 100;
      } else {
        // If train return is 0, degradation is undefined; use a sentinel
        window.degradationPct = testReturn < 0 ? 100 : 0;
      }

      // Walk-Forward Efficiency: OOS return / IS return
      // Clamped to reasonable range; only meaningful when train return > 0
      if (trainReturn > 0) {
        window.wfe = testReturn / trainReturn;
      } else if (trainReturn === 0 && testReturn > 0) {
        // System made money OOS but not IS — unusual but good; cap at 1
        window.wfe = 1;
      } else if (trainReturn === 0 && testReturn <= 0) {
        // No edge in either period
        window.wfe = 0;
      } else {
        // Negative train return: if test is also negative, WFE is the ratio
        // (less negative is better). If test is positive, that's actually
        // a negative WFE (system reversed behavior).
        window.wfe = testReturn / trainReturn;
      }

      // Clamp WFE to [-10, 10] to prevent extreme outliers from skewing
      window.wfe = Math.max(-10, Math.min(10, window.wfe));
    }

    // Step 3: Aggregate results
    const aggregateWFE = calculateWeightedWFE(windows);

    const inSampleReturns = windows
      .map((w) => w.inSampleResult?.totalReturnPct ?? 0)
      .filter((_, i) => windows[i].inSampleResult !== null);
    const outOfSampleReturns = windows
      .map((w) => w.outOfSampleResult?.totalReturnPct ?? 0)
      .filter((_, i) => windows[i].outOfSampleResult !== null);

    const avgInSampleReturn =
      inSampleReturns.length > 0
        ? inSampleReturns.reduce((s, r) => s + r, 0) / inSampleReturns.length
        : 0;

    const avgOutOfSampleReturn =
      outOfSampleReturns.length > 0
        ? outOfSampleReturns.reduce((s, r) => s + r, 0) / outOfSampleReturns.length
        : 0;

    const performanceConsistency = standardDeviation(outOfSampleReturns);

    const overallDegradation =
      Math.abs(avgInSampleReturn) > 0
        ? ((avgInSampleReturn - avgOutOfSampleReturn) / Math.abs(avgInSampleReturn)) * 100
        : avgOutOfSampleReturn < 0
          ? 100
          : 0;

    // Parameter stability: based on consistency of out-of-sample win rates
    const oosWinRates = windows
      .map((w) => {
        if (!w.outOfSampleResult) return null;
        return w.outOfSampleResult.winRate;
      })
      .filter((r): r is number => r !== null);

    const parameterStability = this.calculateParameterStability(oosWinRates);

    const isRobust = aggregateWFE >= config.minWFE;

    const result: WalkForwardResult = {
      id: generateWFAId(),
      systemName: config.system.name,
      config,
      windows,
      aggregateWFE,
      avgInSampleReturn,
      avgOutOfSampleReturn,
      performanceConsistency,
      isRobust,
      recommendation: 'INSUFFICIENT_DATA', // placeholder, assessed below
      overallDegradation,
      parameterStability,
      generatedAt: new Date(),
    };

    result.recommendation = this.assessRobustness(result);

    return result;
  }

  // ----------------------------------------------------------
  // Window Date Calculation
  // ----------------------------------------------------------

  /**
   * Calculate train/test date ranges for each walk-forward window.
   *
   * Rolling WFA: Each window slides forward by (totalRange / windowCount).
   *   Train period = window start to (window start + trainRatio * windowWidth)
   *   Test period = end of train to end of window
   *
   * Anchored WFA: Each training window starts from the overall startDate.
   *   Train period = startDate to (startDate + accumulated ratio of total range)
   *   Test period = end of train to end of window
   */
  calculateWindowDates(
    startDate: Date,
    endDate: Date,
    windowCount: number,
    trainRatio: number,
    anchored: boolean,
  ): WalkForwardWindow[] {
    const totalMs = endDate.getTime() - startDate.getTime();
    if (totalMs <= 0) {
      return [];
    }

    const windows: WalkForwardWindow[] = [];
    const windowMs = totalMs / windowCount;
    const trainMs = windowMs * trainRatio;
    const testMs = windowMs * (1 - trainRatio);

    for (let i = 0; i < windowCount; i++) {
      let trainStart: Date;
      let trainEnd: Date;
      let testStart: Date;
      let testEnd: Date;

      if (anchored) {
        // Anchored: training always starts from the beginning
        // Each subsequent window has a longer training period
        const accumulatedMs = windowMs * (i + 1);
        trainStart = new Date(startDate.getTime());
        trainEnd = new Date(startDate.getTime() + accumulatedMs - testMs);
        testStart = new Date(trainEnd.getTime());
        testEnd = new Date(startDate.getTime() + accumulatedMs);

        // Ensure the last window ends at endDate
        if (i === windowCount - 1) {
          testEnd = new Date(endDate.getTime());
        }
      } else {
        // Rolling: each window slides forward
        const windowStartMs = startDate.getTime() + windowMs * i;
        trainStart = new Date(windowStartMs);
        trainEnd = new Date(windowStartMs + trainMs);
        testStart = new Date(trainEnd.getTime());
        testEnd = new Date(windowStartMs + windowMs);

        // Ensure the last window ends at endDate
        if (i === windowCount - 1) {
          testEnd = new Date(endDate.getTime());
        }
      }

      windows.push({
        windowIndex: i,
        trainStart,
        trainEnd,
        testStart,
        testEnd,
        inSampleResult: null,
        outOfSampleResult: null,
        degradationPct: 0,
        wfe: 0,
      });
    }

    return windows;
  }

  // ----------------------------------------------------------
  // Robustness Assessment
  // ----------------------------------------------------------

  /**
   * Assess the robustness of a walk-forward result.
   *
   * Decision logic:
   *   - INSUFFICIENT_DATA: Any window has 0 trades in both train & test
   *   - ROBUST: aggregateWFE >= minWFE (system holds up out-of-sample)
   *   - MARGINAL: aggregateWFE >= minWFE * 0.6 (decent but not great)
   *   - OVERFIT: aggregateWFE < minWFE * 0.6 (significant degradation)
   */
  assessRobustness(result: WalkForwardResult): WalkForwardResult['recommendation'] {
    const minWFE = result.config.minWFE;

    // Check for insufficient data: any window with 0 total trades
    const hasInsufficientData = result.windows.some((w) => {
      const isTrades = w.inSampleResult?.totalTrades ?? 0;
      const oosTrades = w.outOfSampleResult?.totalTrades ?? 0;
      return isTrades === 0 && oosTrades === 0;
    });

    if (hasInsufficientData) {
      return 'INSUFFICIENT_DATA';
    }

    // Check for completely empty results (both IS and OOS missing)
    const validWindows = result.windows.filter(
      (w) => w.inSampleResult !== null || w.outOfSampleResult !== null,
    );

    if (validWindows.length === 0) {
      return 'INSUFFICIENT_DATA';
    }

    // Robustness based on aggregate WFE
    if (result.aggregateWFE >= minWFE) {
      return 'ROBUST';
    }

    if (result.aggregateWFE >= minWFE * 0.6) {
      return 'MARGINAL';
    }

    return 'OVERFIT';
  }

  // ----------------------------------------------------------
  // Human-Readable Summary
  // ----------------------------------------------------------

  /**
   * Generate a human-readable summary of the walk-forward analysis.
   * Designed for display in dashboards, reports, and log output.
   */
  generateWFASummary(result: WalkForwardResult): string {
    const lines: string[] = [];

    lines.push('╔════════════════════════════════════════════════════════════════╗');
    lines.push('║          WALK-FORWARD ANALYSIS REPORT                         ║');
    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push(`║  System:       ${result.systemName.padEnd(47)}║`);
    lines.push(`║  Analysis ID:  ${result.id.padEnd(47)}║`);
    lines.push(`║  Generated:    ${result.generatedAt.toISOString().padEnd(47)}║`);
    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  CONFIGURATION                                                ║');
    lines.push(`║  Windows:      ${String(result.config.windowCount).padEnd(47)}║`);
    lines.push(`║  Train Ratio:  ${(result.config.trainRatio * 100).toFixed(0)}%${' '.repeat(44)}║`);
    lines.push(`║  Anchored:     ${result.config.anchored ? 'Yes' : 'No (Rolling)'}${' '.repeat(41)}║`);
    lines.push(`║  Min WFE:      ${(result.config.minWFE * 100).toFixed(0)}%${' '.repeat(44)}║`);
    lines.push(`║  Period:       ${result.config.startDate.toISOString().slice(0, 10)} → ${result.config.endDate.toISOString().slice(0, 10)}${' '.repeat(16)}║`);
    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  AGGREGATE RESULTS                                            ║');
    lines.push(`║  Aggregate WFE:       ${(result.aggregateWFE * 100).toFixed(1).padStart(7)}%${' '.repeat(35)}║`);
    lines.push(`║  Avg IS Return:       ${result.avgInSampleReturn.toFixed(2).padStart(10)}%${' '.repeat(32)}║`);
    lines.push(`║  Avg OOS Return:      ${result.avgOutOfSampleReturn.toFixed(2).padStart(10)}%${' '.repeat(32)}║`);
    lines.push(`║  Overall Degradation: ${result.overallDegradation.toFixed(1).padStart(7)}%${' '.repeat(35)}║`);
    lines.push(`║  Performance StdDev:  ${result.performanceConsistency.toFixed(2).padStart(10)}%${' '.repeat(32)}║`);
    lines.push(`║  Parameter Stability: ${(result.parameterStability * 100).toFixed(1).padStart(7)}%${' '.repeat(35)}║`);
    lines.push('╠════════════════════════════════════════════════════════════════╣');

    // Recommendation with visual indicator
    const recMap: Record<string, string> = {
      ROBUST: '✅ ROBUST — System validates well out-of-sample',
      MARGINAL: '⚠️  MARGINAL — System shows some degradation',
      OVERFIT: '❌ OVERFIT — System fails out-of-sample validation',
      INSUFFICIENT_DATA: '❓ INSUFFICIENT DATA — Not enough trades to assess',
    };
    const recLine = recMap[result.recommendation] ?? result.recommendation;
    lines.push(`║  Verdict: ${recLine.padEnd(52)}║`);

    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  WINDOW DETAILS                                               ║');
    lines.push('╠════════════════════════════════════════════════════════════════╣');

    for (const window of result.windows) {
      const isReturn = window.inSampleResult?.totalReturnPct ?? 0;
      const oosReturn = window.outOfSampleResult?.totalReturnPct ?? 0;
      const isTrades = window.inSampleResult?.totalTrades ?? 0;
      const oosTrades = window.outOfSampleResult?.totalTrades ?? 0;

      lines.push(`║  Window ${String(window.windowIndex + 1).padStart(2)} of ${result.config.windowCount}                                              ║`);
      lines.push(`║    Train: ${window.trainStart.toISOString().slice(0, 10)} → ${window.trainEnd.toISOString().slice(0, 10)} | Return: ${isReturn.toFixed(2).padStart(8)}% | Trades: ${String(isTrades).padStart(3)}  ║`);
      lines.push(`║    Test:  ${window.testStart.toISOString().slice(0, 10)} → ${window.testEnd.toISOString().slice(0, 10)} | Return: ${oosReturn.toFixed(2).padStart(8)}% | Trades: ${String(oosTrades).padStart(3)}  ║`);
      lines.push(`║    WFE: ${(window.wfe * 100).toFixed(1).padStart(6)}% | Degradation: ${window.degradationPct.toFixed(1).padStart(6)}%${' '.repeat(19)}║`);
      lines.push('║                                                                ║');
    }

    lines.push('╠════════════════════════════════════════════════════════════════╣');
    lines.push('║  INTERPRETATION GUIDE                                         ║');
    lines.push('║  WFE > 70%: Excellent — system is robust                     ║');
    lines.push('║  WFE 50-70%: Good — acceptable out-of-sample performance     ║');
    lines.push('║  WFE 30-50%: Marginal — possible overfitting                 ║');
    lines.push('║  WFE < 30%: Likely overfit — do not deploy                   ║');
    lines.push('║  Negative WFE: System reverses behavior OOS — avoid          ║');
    lines.push('╚════════════════════════════════════════════════════════════════╝');

    return lines.join('\n');
  }

  // ----------------------------------------------------------
  // Private Helpers
  // ----------------------------------------------------------

  /**
   * Calculate parameter stability from out-of-sample win rates.
   *
   * Measures how consistent the system's performance is across windows.
   * Low variance in win rates → high stability.
   * Returns a value between 0 (unstable) and 1 (perfectly stable).
   */
  private calculateParameterStability(winRates: number[]): number {
    if (winRates.length < 2) return 0.5; // Insufficient data

    const avgWinRate = winRates.reduce((s, r) => s + r, 0) / winRates.length;
    const variance =
      winRates.reduce((s, r) => s + (r - avgWinRate) ** 2, 0) /
      (winRates.length - 1);
    const stdDev = Math.sqrt(variance);

    // Convert to stability: low std dev → high stability
    // Win rates are 0-1, so std dev of 0.5 is very high
    const stability = Math.max(0, Math.min(1, 1 - stdDev * 4));
    return Math.round(stability * 1000) / 1000;
  }
}

// ============================================================
// 4. SINGLETON EXPORT
// ============================================================

export const walkForwardEngine = new WalkForwardEngine();
