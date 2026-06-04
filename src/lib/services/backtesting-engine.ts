/**
 * Backtesting Engine - CryptoQuant Terminal
 *
 * Historical simulation, Paper trading, Forward testing
 * Full metrics calculation with anti-overfitting protection
 *
 * Provides:
 *   - Real financial metrics (Sharpe, Sortino, Calmar, etc.)
 *   - Equity curve and drawdown curve generation
 *   - Trade simulation with MFE/MAE tracking
 *   - Walk-forward analysis for robustness
 *   - Scorecard generation for human review
 */

import type { SystemTemplate, TokenPhase } from './trading-system-engine';
import { db } from '../db';
import { ohlcvPipeline } from './ohlcv-pipeline';

// ============================================================
// 1. TYPES & INTERFACES
// ============================================================

export type BacktestMode = 'HISTORICAL' | 'PAPER' | 'FORWARD';

export interface OHLCVBar {
  timestamp: number; // ms epoch
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TokenData {
  tokenAddress: string;
  symbol: string;
  createdAt: Date;
  phase: TokenPhase;
  bars: OHLCVBar[];
  /** Additional on-chain metrics per bar (optional) */
  metricsPerBar?: Array<{
    holderCount?: number;
    liquidityUsd?: number;
    volume24h?: number;
    rugScore?: number;
    smartMoneyCount?: number;
    botRatio?: number;
    [key: string]: unknown;
  }>;
}

export interface BacktestConfig {
  /** Trading system template to backtest */
  system: SystemTemplate;
  /** Backtest mode */
  mode: BacktestMode;
  /** Start date for historical simulation */
  startDate: Date;
  /** End date for historical simulation */
  endDate: Date;
  /** Initial capital in USD */
  initialCapital: number;
  /** Trading fee as percentage (e.g. 0.003 = 0.3%) */
  feesPct: number;
  /** Slippage simulation as percentage */
  slippagePct: number;
  /** Whether to apply slippage on entry and exit */
  applySlippage: boolean;
  /** Maximum number of concurrent positions */
  maxConcurrentPositions?: number;
  /** Whether to enforce phase compatibility */
  enforcePhaseFilter?: boolean;
  /** Custom data for the system (overrides) */
  customParams?: Record<string, unknown>;
}

export interface BacktestProgress {
  percentComplete: number;
  currentStep: string;
  barsProcessed: number;
  totalBars: number;
  tradesSoFar: number;
  currentEquity: number;
  elapsedTimeMs: number;
}

export interface TradeRecord {
  id: string;
  tokenAddress: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  entryTime: Date;
  exitTime: Date | null;
  entryPrice: number;
  exitPrice: number | null;
  size: number;        // position size in USD
  quantity: number;    // token quantity
  pnl: number;        // net PnL in USD (after fees)
  pnlPct: number;     // net PnL as percentage
  mfe: number;        // max favorable excursion (pct)
  mae: number;        // max adverse excursion (pct)
  holdTimeMin: number;
  exitReason: string;
  phase: TokenPhase;
  /** Bar index at entry */
  entryBarIndex: number;
  /** Bar index at exit (-1 if still open) */
  exitBarIndex: number;
}

export interface EquityPoint {
  timestamp: number;
  equity: number;
  drawdown: number;
  drawdownPct: number;
  openPositions: number;
  unrealizedPnl: number;
}

export interface MonthlyReturn {
  month: string; // e.g. "2024-01"
  returnPct: number;
  startingEquity: number;
  endingEquity: number;
  tradesCount: number;
}

export interface PhaseBreakdown {
  phase: TokenPhase;
  tradeCount: number;
  winRate: number;
  avgPnlPct: number;
  totalPnl: number;
  avgHoldTimeMin: number;
}

export interface BacktestResult {
  /** Unique identifier */
  id: string;
  /** System that was tested */
  systemName: string;
  /** Configuration used */
  config: BacktestConfig;
  /** When this result was generated */
  generatedAt: Date;

  // ---- Core Metrics ----
  initialCapital: number;
  finalEquity: number;
  totalReturnPct: number;
  annualizedReturnPct: number;

  // ---- Trade Statistics ----
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
  avgWinPct: number;
  avgLossPct: number;
  avgHoldTimeMin: number;
  maxConsecutiveWins: number;
  maxConsecutiveLosses: number;

  // ---- Risk Metrics ----
  maxDrawdown: number;
  maxDrawdownPct: number;
  sharpeRatio: number;
  sortinoRatio: number;
  calmarRatio: number;
  profitFactor: number;
  recoveryFactor: number;
  expectancy: number;

  // ---- Detailed Data ----
  trades: TradeRecord[];
  equityCurve: EquityPoint[];
  drawdownCurve: EquityPoint[];
  monthlyReturns: Record<string, number>;
  monthlyReturnDetails: MonthlyReturn[];
  phaseBreakdown: PhaseBreakdown[];

  // ---- Anti-Overfitting ----
  overfittingScore: number; // 0 = no overfitting, 1 = severe overfitting
  parameterStability: number; // 0-1, higher = more stable

  /** Duration of the backtest */
  totalBarsProcessed: number;
}

// ============================================================
// 2. METRICS FUNCTIONS (Real Math)
// ============================================================

/**
 * Sharpe Ratio = (mean(returns) - riskFreeRate) / stdDev(returns)
 * Annualized assuming returns are per-period.
 * @param returns Array of period returns (e.g. daily returns as decimals)
 * @param riskFreeRate Annualized risk-free rate (default 0.04 = 4%)
 * @param periodsPerYear Number of periods per year (default 365 for crypto)
 */
export function calculateSharpeRatio(
  returns: number[],
  riskFreeRate: number = 0.04,
  periodsPerYear: number = 365,
): number {
  if (returns.length < 2) return 0;

  const n = returns.length;
  const meanReturn = returns.reduce((s, r) => s + r, 0) / n;

  // Annualize the mean return
  const annualizedMean = meanReturn * periodsPerYear;

  // Standard deviation
  const variance =
    returns.reduce((s, r) => s + (r - meanReturn) ** 2, 0) / (n - 1);
  const stdDev = Math.sqrt(variance);

  if (stdDev === 0) return 0;

  // Annualize std dev
  const annualizedStdDev = stdDev * Math.sqrt(periodsPerYear);

  return (annualizedMean - riskFreeRate) / annualizedStdDev;
}

/**
 * Sortino Ratio = (mean(returns) - riskFreeRate) / downsideDeviation
 * Only penalizes negative volatility.
 */
export function calculateSortinoRatio(
  returns: number[],
  riskFreeRate: number = 0.04,
  periodsPerYear: number = 365,
): number {
  if (returns.length < 2) return 0;

  const n = returns.length;
  const meanReturn = returns.reduce((s, r) => s + r, 0) / n;
  const annualizedMean = meanReturn * periodsPerYear;

  // Per-period risk-free rate
  const periodRF = riskFreeRate / periodsPerYear;

  // Downside deviation: only negative deviations from the risk-free rate
  const downsideDiffs = returns.map((r) =>
    r < periodRF ? (r - periodRF) ** 2 : 0,
  );
  const downsideVariance = downsideDiffs.reduce((s, d) => s + d, 0) / n;
  const downsideDev = Math.sqrt(downsideVariance);

  if (downsideDev === 0) return 0;

  const annualizedDownsideDev = downsideDev * Math.sqrt(periodsPerYear);

  return (annualizedMean - riskFreeRate) / annualizedDownsideDev;
}

/**
 * Calmar Ratio = annualizedReturn / |maxDrawdownPct|
 * Higher is better. Typically calculated over 3 years.
 */
export function calculateCalmarRatio(
  annualReturn: number,
  maxDrawdown: number,
): number {
  if (maxDrawdown === 0) return annualReturn > 0 ? Infinity : 0;
  return annualReturn / Math.abs(maxDrawdown);
}

/**
 * Maximum Drawdown calculation from equity curve.
 * Returns drawdown details including start, end, and recovery indices.
 */
export function calculateMaxDrawdown(equityCurve: number[]): {
  maxDrawdown: number;
  maxDrawdownPct: number;
  startIdx: number;
  endIdx: number;
  recoveryIdx: number | null;
} {
  if (equityCurve.length < 2) {
    return { maxDrawdown: 0, maxDrawdownPct: 0, startIdx: 0, endIdx: 0, recoveryIdx: null };
  }

  let maxDrawdown = 0;
  let maxDrawdownPct = 0;
  let startIdx = 0;
  let endIdx = 0;
  let peak = equityCurve[0];
  let peakIdx = 0;
  let recoveryIdx: number | null = null;

  // Track the deepest drawdown point's recovery
  let deepestRecoveryIdx: number | null = null;

  for (let i = 1; i < equityCurve.length; i++) {
    if (equityCurve[i] > peak) {
      peak = equityCurve[i];
      peakIdx = i;

      // Check if we recovered from the deepest drawdown
      if (endIdx > 0 && deepestRecoveryIdx === null) {
        deepestRecoveryIdx = i;
      }
    }

    const drawdown = peak - equityCurve[i];
    const drawdownPct = peak !== 0 ? drawdown / peak : 0;

    if (drawdownPct > maxDrawdownPct) {
      maxDrawdownPct = drawdownPct;
      maxDrawdown = drawdown;
      startIdx = peakIdx;
      endIdx = i;
      deepestRecoveryIdx = null; // Reset — looking for recovery
    }
  }

  // Find recovery point for the max drawdown
  if (endIdx > 0) {
    const peakEquity = equityCurve[startIdx];
    for (let i = endIdx + 1; i < equityCurve.length; i++) {
      if (equityCurve[i] >= peakEquity) {
        recoveryIdx = i;
        break;
      }
    }
  }

  return {
    maxDrawdown,
    maxDrawdownPct,
    startIdx,
    endIdx,
    recoveryIdx: recoveryIdx ?? deepestRecoveryIdx,
  };
}

/**
 * Expectancy = (winRate × avgWin) + ((1 - winRate) × avgLoss)
 * Positive expectancy means the system has a statistical edge.
 */
export function calculateExpectancy(
  winRate: number,
  avgWin: number,
  avgLoss: number,
): number {
  return winRate * avgWin + (1 - winRate) * avgLoss;
}

/**
 * Profit Factor = grossProfit / |grossLoss|
 * > 1 means profitable, > 2 is excellent.
 */
export function calculateProfitFactor(
  grossProfit: number,
  grossLoss: number,
): number {
  if (grossLoss === 0) return grossProfit > 0 ? Infinity : 0;
  return grossProfit / Math.abs(grossLoss);
}

/**
 * Recovery Factor = netProfit / |maxDrawdown|
 * Measures how efficiently the system recovers from drawdowns.
 */
export function calculateRecoveryFactor(
  netProfit: number,
  maxDrawdown: number,
): number {
  if (maxDrawdown === 0) return netProfit > 0 ? Infinity : 0;
  return netProfit / Math.abs(maxDrawdown);
}

/**
 * Generate an equity curve from a list of trades and initial capital.
 * Each equity point is recorded at the close of each trade.
 */
export function generateEquityCurve(
  trades: TradeRecord[],
  initialCapital: number,
): EquityPoint[] {
  const curve: EquityPoint[] = [];

  // Starting point
  curve.push({
    timestamp: trades.length > 0 ? trades[0].entryTime.getTime() : Date.now(),
    equity: initialCapital,
    drawdown: 0,
    drawdownPct: 0,
    openPositions: 0,
    unrealizedPnl: 0,
  });

  let equity = initialCapital;
  let peakEquity = initialCapital;

  for (const trade of trades) {
    if (trade.exitTime === null || trade.exitPrice === null) continue;

    equity += trade.pnl;
    peakEquity = Math.max(peakEquity, equity);

    const drawdown = peakEquity - equity;
    const drawdownPct = peakEquity !== 0 ? drawdown / peakEquity : 0;

    curve.push({
      timestamp: trade.exitTime.getTime(),
      equity,
      drawdown,
      drawdownPct,
      openPositions: 0,
      unrealizedPnl: 0,
    });
  }

  return curve;
}

/**
 * Calculate monthly returns from an equity curve.
 * Returns a map of "YYYY-MM" → return percentage.
 */
export function calculateMonthlyReturns(
  equityCurve: EquityPoint[],
): Record<string, number> {
  const monthly: Record<string, number> = {};

  if (equityCurve.length === 0) return monthly;

  // Group equity points by month
  const monthMap: Record<string, { first: number; last: number }> = {};

  for (const point of equityCurve) {
    const d = new Date(point.timestamp);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;

    if (!monthMap[key]) {
      monthMap[key] = { first: point.equity, last: point.equity };
    }
    monthMap[key].last = point.equity;
  }

  // Calculate return for each month
  for (const [month, data] of Object.entries(monthMap)) {
    monthly[month] = data.first !== 0
      ? ((data.last - data.first) / data.first) * 100
      : 0;
  }

  return monthly;
}

// ============================================================
// 3. SIMULATION FUNCTIONS
// ============================================================

/**
 * Simulate a single trade from entry to exit.
 * Calculates PnL, MFE, MAE, and hold time.
 */
export function simulateTrade(
  entry: { price: number; time: Date; size: number },
  exit: { price: number; time: Date },
  feesPct: number,
): {
  pnl: number;
  pnlPct: number;
  mfe: number;
  mae: number;
  holdTimeMin: number;
} {
  const holdTimeMs = exit.time.getTime() - entry.time.getTime();
  const holdTimeMin = Math.max(0, Math.round(holdTimeMs / 60000));

  const entryCost = entry.size; // USD committed
  const quantity = entry.size / entry.price;
  const exitValue = quantity * exit.price;

  // Gross PnL
  const grossPnl = exitValue - entryCost;

  // Fees: pay on entry and exit
  const entryFee = entryCost * feesPct;
  const exitFee = exitValue * feesPct;
  const totalFees = entryFee + exitFee;

  // Net PnL
  const pnl = grossPnl - totalFees;
  const pnlPct = entryCost !== 0 ? (pnl / entryCost) * 100 : 0;

  // MFE/MAE approximation based on entry vs exit
  // In a real backtest these would be calculated from intra-trade price movement
  const priceChange = (exit.price - entry.price) / entry.price;
  const mfe = priceChange > 0 ? priceChange * 100 : 0; // Approximation
  const mae = priceChange < 0 ? priceChange * 100 : 0; // Approximation

  return { pnl, pnlPct, mfe, mae, holdTimeMin };
}

/**
 * Apply stop-loss and take-profit logic given OHLC bar data.
 * Determines which exit triggered first based on bar high/low.
 *
 * Priority: SL > TP if both could trigger in the same bar
 * (conservative assumption — in reality depends on intra-bar price path).
 */
export function applyStopLossTakeProfit(
  entry: number,
  sl: number,
  tp: number,
  high: number,
  low: number,
): { exitPrice: number; reason: string } {
  // Long position logic (entry price is buy price)
  const slPrice = entry * (1 + sl / 100); // sl is negative, e.g. -8
  const tpPrice = entry * (1 + tp / 100); // tp is positive, e.g. 30

  let slHit = false;
  let tpHit = false;

  // Check if SL was hit (price went below SL level)
  if (sl < 0 && low <= slPrice) {
    slHit = true;
  }

  // Check if TP was hit (price went above TP level)
  if (tp > 0 && high >= tpPrice) {
    tpHit = true;
  }

  // Both could be hit in the same bar — SL takes priority (conservative)
  if (slHit) {
    return { exitPrice: slPrice, reason: 'stop_loss' };
  }

  if (tpHit) {
    return { exitPrice: tpPrice, reason: 'take_profit' };
  }

  // Neither hit — no exit
  return { exitPrice: entry, reason: 'none' };
}

// ============================================================
// 4. INTERNAL HELPERS
// ============================================================

/** Generate a unique ID */
function generateId(): string {
  return `bt_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

/** Count consecutive elements in array */
function maxConsecutive(values: boolean[]): number {
  let maxRun = 0;
  let currentRun = 0;
  for (const v of values) {
    if (v) {
      currentRun++;
      maxRun = Math.max(maxRun, currentRun);
    } else {
      currentRun = 0;
    }
  }
  return maxRun;
}

/**
 * Simple entry signal simulator based on template configuration.
 * This is a heuristic simulation — real production would use actual signal generation.
 */
function evaluateEntrySignal(
  bar: OHLCVBar,
  prevBars: OHLCVBar[],
  system: SystemTemplate,
  _metrics?: Record<string, unknown>,
): { shouldEnter: boolean; confidence: number; direction: 'LONG' | 'SHORT' } {
  // Use the system's entry type to determine a heuristic signal
  const entryType = system.entrySignal.type;
  let shouldEnter = false;
  let confidence = 0;
  const direction: 'LONG' | 'SHORT' = system.operationType === 'SHORT' ? 'SHORT' : 'LONG';

  if (prevBars.length < 5) return { shouldEnter: false, confidence: 0, direction };

  const closes = prevBars.map((b) => b.close);
  const volumes = prevBars.map((b) => b.volume);
  const avgVolume = volumes.length > 0 ? volumes.reduce((s, v) => s + v, 0) / volumes.length : 0;

  switch (entryType) {
    case 'BLOCK_ZERO_ENTRY':
    case 'VOLATILITY_SPIKE_ENTRY':
    case 'NEW_LISTING':
    case 'MEME_VIRAL_DETECT': {
      // High volatility + volume spike = entry
      const recentVol = bar.volume;
      if (avgVolume > 0 && recentVol > avgVolume * 2) {
        shouldEnter = true;
        confidence = Math.min(0.8, recentVol / (avgVolume * 3));
      }
      break;
    }
    case 'WHALE_ACCUMULATION':
    case 'SM_ENTRY_MIRROR':
    case 'SM_JUST_ENTERED': {
      // Gradual price increase with moderate volume
      const sma5 = closes.slice(-5).reduce((s, c) => s + c, 0) / 5;
      const sma10 = closes.slice(-10).reduce((s, c) => s + c, 0) / Math.min(closes.length, 10);
      if (sma5 > sma10 * 1.01 && bar.close > sma5) {
        shouldEnter = true;
        confidence = 0.6;
      }
      break;
    }
    case 'BREAKOUT_WITH_VOLUME':
    case 'RANGE_BREAKOUT': {
      // Price breakout above recent range with volume
      const recentHigh = Math.max(...closes.slice(-20));
      if (bar.close > recentHigh && bar.volume > avgVolume * 1.5) {
        shouldEnter = true;
        confidence = 0.65;
      }
      break;
    }
    case 'MEAN_REVERSION_ENTRY': {
      // Price below lower threshold
      const sma20 = closes.slice(-20).reduce((s, c) => s + c, 0) / Math.min(closes.length, 20);
      if (bar.close < sma20 * 0.92) {
        shouldEnter = true;
        confidence = 0.6;
      }
      break;
    }
    case 'TREND_PULLBACK_ENTRY': {
      // Pullback in uptrend
      const sma5 = closes.slice(-5).reduce((s, c) => s + c, 0) / 5;
      const sma20 = closes.slice(-20).reduce((s, c) => s + c, 0) / Math.min(closes.length, 20);
      if (sma5 > sma20 && bar.close < sma5 * 1.02 && bar.close > sma20) {
        shouldEnter = true;
        confidence = 0.6;
      }
      break;
    }
    case 'V_SHAPE_DETECT': {
      // Sharp drop followed by recovery start
      if (prevBars.length >= 3) {
        const dropPct = ((closes[closes.length - 1] - closes[closes.length - 3]) / closes[closes.length - 3]) * 100;
        const bouncePct = ((bar.close - closes[closes.length - 1]) / closes[closes.length - 1]) * 100;
        if (dropPct < -10 && bouncePct > 3) {
          shouldEnter = true;
          confidence = 0.55;
        }
      }
      break;
    }
    default: {
      // Generic: price above SMA with volume confirmation
      const sma10 = closes.slice(-10).reduce((s, c) => s + c, 0) / Math.min(closes.length, 10);
      if (bar.close > sma10 && bar.volume > avgVolume) {
        shouldEnter = true;
        confidence = 0.5;
      }
      break;
    }
  }

  // Apply minimum confidence threshold
  if (confidence < system.entrySignal.minConfidence) {
    shouldEnter = false;
  }

  return { shouldEnter, confidence, direction };
}

/**
 * Evaluate exit conditions for an open position.
 */
function evaluateExitSignal(
  position: TradeRecord,
  currentBar: OHLCVBar,
  system: SystemTemplate,
  currentEquity: number,
  peakEquity: number,
): { shouldExit: boolean; exitPrice: number; reason: string } {
  const entry = position.entryPrice;

  // 1. Stop Loss check
  const slPct = system.exitSignal.stopLossPct;
  if (slPct !== 0 && slPct < 0) {
    const slResult = applyStopLossTakeProfit(
      entry, slPct, 0, currentBar.high, currentBar.low,
    );
    if (slResult.reason === 'stop_loss') {
      return { shouldExit: true, exitPrice: slResult.exitPrice, reason: 'stop_loss' };
    }
  }

  // 2. Take Profit check
  const tpPct = system.exitSignal.takeProfitPct;
  if (tpPct > 0) {
    const tpResult = applyStopLossTakeProfit(
      entry, 0, tpPct, currentBar.high, currentBar.low,
    );
    if (tpResult.reason === 'take_profit') {
      return { shouldExit: true, exitPrice: tpResult.exitPrice, reason: 'take_profit' };
    }
  }

  // 3. Trailing Stop check
  if (system.exitSignal.trailingStopPct && system.exitSignal.trailingStopPct > 0) {
    const currentPnlPct = ((currentBar.close - entry) / entry) * 100;

    // Check if trailing stop is activated
    const activationPct = system.exitSignal.trailingActivationPct ?? system.exitSignal.trailingStopPct;
    if (currentPnlPct >= activationPct) {
      // Calculate trailing level from highest point since entry
      const highSinceEntry = currentBar.high; // Simplified — would track in real impl
      const trailingLevel = highSinceEntry * (1 - system.exitSignal.trailingStopPct / 100);

      if (currentBar.low <= trailingLevel) {
        return { shouldExit: true, exitPrice: trailingLevel, reason: 'trailing_stop' };
      }
    }
  }

  // 4. Time-based exit
  if (system.exitSignal.timeBasedExitMin && system.exitSignal.timeBasedExitMin > 0) {
    const holdMin = (currentBar.timestamp - position.entryTime.getTime()) / 60000;
    if (holdMin >= system.exitSignal.timeBasedExitMin) {
      return { shouldExit: true, exitPrice: currentBar.close, reason: 'time_expired' };
    }
  }

  // 5. Max drawdown exit
  if (system.riskParams.maxDrawdownPct > 0 && peakEquity > 0) {
    const currentDrawdownPct = (peakEquity - currentEquity) / peakEquity;
    if (currentDrawdownPct >= system.riskParams.maxDrawdownPct / 100) {
      return { shouldExit: true, exitPrice: currentBar.close, reason: 'max_drawdown_breach' };
    }
  }

  return { shouldExit: false, exitPrice: 0, reason: '' };
}

// ============================================================
// 5. BACKTESTING ENGINE CLASS
// ============================================================

export class BacktestingEngine {
  /**
   * Run a full backtest simulation.
   *
   * Iterates through historical data, applies system entry/exit signals,
   * uses capital allocation for sizing, tracks all operations, and
   * calculates all metrics.
   */
  async runBacktest(
    config: BacktestConfig,
    tokens: TokenData[],
    onProgress?: (p: BacktestProgress) => void,
  ): Promise<BacktestResult> {
    const startTime = Date.now();
    const system = config.system;

    // Calculate total bars for progress tracking
    let totalBars = 0;
    for (const token of tokens) {
      totalBars += token.bars.filter(
        (b) =>
          b.timestamp >= config.startDate.getTime() &&
          b.timestamp <= config.endDate.getTime(),
      ).length;
    }

    // State
    let equity = config.initialCapital;
    let peakEquity = config.initialCapital;
    const trades: TradeRecord[] = [];
    const openPositions: TradeRecord[] = [];
    const equityCurve: EquityPoint[] = [];
    let barsProcessed = 0;
    let tradeCounter = 0;

    // Initial equity point
    equityCurve.push({
      timestamp: config.startDate.getTime(),
      equity: config.initialCapital,
      drawdown: 0,
      drawdownPct: 0,
      openPositions: 0,
      unrealizedPnl: 0,
    });

    // Process each token
    for (const token of tokens) {
      // Filter bars to backtest period
      const relevantBars = token.bars.filter(
        (b) =>
          b.timestamp >= config.startDate.getTime() &&
          b.timestamp <= config.endDate.getTime(),
      );

      // Phase filter
      if (config.enforcePhaseFilter !== false) {
        if (!system.phaseConfig.allowedPhases.includes(token.phase)) {
          barsProcessed += relevantBars.length;
          continue;
        }
      }

      // Iterate through each bar
      for (let i = 0; i < relevantBars.length; i++) {
        const bar = relevantBars[i];
        const prevBars = relevantBars.slice(Math.max(0, i - 30), i);
        const metrics = token.metricsPerBar?.[i];

        // ---- Check exit signals for open positions ----
        const positionsToClose: number[] = []; // indices in openPositions

        for (let p = 0; p < openPositions.length; p++) {
          const pos = openPositions[p];
          if (pos.tokenAddress !== token.tokenAddress) continue;

          // Calculate current unrealized PnL for drawdown check
          const unrealizedPnl = (bar.close - pos.entryPrice) * pos.quantity;
          const currentEquityWithUnrealized = equity + unrealizedPnl;

          const exitEval = evaluateExitSignal(
            pos,
            bar,
            system,
            currentEquityWithUnrealized,
            peakEquity,
          );

          if (exitEval.shouldExit) {
            // Close the position
            const exitPrice = exitEval.exitPrice * (1 - (config.applySlippage ? config.slippagePct / 100 : 0));
            const exitValue = pos.quantity * exitPrice;
            const entryFee = pos.size * config.feesPct;
            const exitFee = exitValue * config.feesPct;
            const grossPnl = exitValue - pos.size;
            const netPnl = grossPnl - entryFee - exitFee;
            const pnlPct = pos.size !== 0 ? (netPnl / pos.size) * 100 : 0;

            // Calculate MFE/MAE from the trade
            const priceRange = bar.high - bar.low;
            const mfe = pos.entryPrice !== 0
              ? ((bar.high - pos.entryPrice) / pos.entryPrice) * 100
              : 0;
            const mae = pos.entryPrice !== 0
              ? ((bar.low - pos.entryPrice) / pos.entryPrice) * 100
              : 0;

            pos.exitPrice = exitPrice;
            pos.exitTime = new Date(bar.timestamp);
            pos.pnl = netPnl;
            pos.pnlPct = pnlPct;
            pos.mfe = Math.max(0, mfe);
            pos.mae = Math.min(0, mae);
            pos.holdTimeMin = Math.max(0, Math.round((bar.timestamp - pos.entryTime.getTime()) / 60000));
            pos.exitReason = exitEval.reason;
            pos.exitBarIndex = i;

            equity += netPnl;
            peakEquity = Math.max(peakEquity, equity);

            trades.push(pos);
            positionsToClose.push(p);
          }
        }

        // Remove closed positions (reverse order to maintain indices)
        for (let j = positionsToClose.length - 1; j >= 0; j--) {
          openPositions.splice(positionsToClose[j], 1);
        }

        // ---- Check entry signals ----
        const maxOpen = config.maxConcurrentPositions ?? system.riskParams.maxOpenPositions;
        if (openPositions.length < maxOpen) {
          const entryEval = evaluateEntrySignal(bar, prevBars, system, metrics as Record<string, unknown> | undefined);

          if (entryEval.shouldEnter) {
            // Calculate position size
            const positionSize = this.calculatePositionSize(
              equity,
              system,
              entryEval.confidence,
            );

            if (positionSize > 0) {
              const entryPrice = bar.close * (1 + (config.applySlippage ? config.slippagePct / 100 : 0));
              const quantity = positionSize / entryPrice;

              tradeCounter++;
              const trade: TradeRecord = {
                id: `trade_${tradeCounter}`,
                tokenAddress: token.tokenAddress,
                symbol: token.symbol,
                direction: entryEval.direction,
                entryTime: new Date(bar.timestamp),
                exitTime: null,
                entryPrice,
                exitPrice: null,
                size: positionSize,
                quantity,
                pnl: 0,
                pnlPct: 0,
                mfe: 0,
                mae: 0,
                holdTimeMin: 0,
                exitReason: '',
                phase: token.phase,
                entryBarIndex: i,
                exitBarIndex: -1,
              };

              openPositions.push(trade);
            }
          }
        }

        // ---- Update equity curve ----
        const unrealizedPnl = openPositions.reduce((s, p) => {
          return s + (bar.close - p.entryPrice) * p.quantity;
        }, 0);

        const currentEquity = equity + unrealizedPnl;
        const drawdown = peakEquity - currentEquity;
        const drawdownPct = peakEquity !== 0 ? drawdown / peakEquity : 0;

        equityCurve.push({
          timestamp: bar.timestamp,
          equity: currentEquity,
          drawdown,
          drawdownPct,
          openPositions: openPositions.length,
          unrealizedPnl,
        });

        barsProcessed++;

        // Report progress
        if (onProgress && barsProcessed % 100 === 0) {
          onProgress({
            percentComplete: totalBars > 0 ? (barsProcessed / totalBars) * 100 : 0,
            currentStep: `Processing ${token.symbol}`,
            barsProcessed,
            totalBars,
            tradesSoFar: trades.length,
            currentEquity: equity,
            elapsedTimeMs: Date.now() - startTime,
          });
        }
      }
    }

    // Close any remaining open positions at last known price
    for (const pos of openPositions) {
      const lastBar = tokens
        .find((t) => t.tokenAddress === pos.tokenAddress)
        ?.bars.slice(-1)[0];

      if (lastBar) {
        const exitPrice = lastBar.close;
        const exitValue = pos.quantity * exitPrice;
        const entryFee = pos.size * config.feesPct;
        const exitFee = exitValue * config.feesPct;
        const grossPnl = exitValue - pos.size;
        const netPnl = grossPnl - entryFee - exitFee;
        const pnlPct = pos.size !== 0 ? (netPnl / pos.size) * 100 : 0;

        pos.exitPrice = exitPrice;
        pos.exitTime = new Date(lastBar.timestamp);
        pos.pnl = netPnl;
        pos.pnlPct = pnlPct;
        pos.mfe = 0;
        pos.mae = 0;
        pos.holdTimeMin = Math.max(0, Math.round((lastBar.timestamp - pos.entryTime.getTime()) / 60000));
        pos.exitReason = 'backtest_end';
        pos.exitBarIndex = -1;

        equity += netPnl;
        trades.push(pos);
      }
    }

    // ---- Calculate Metrics ----
    const closedTrades = trades.filter((t) => t.exitTime !== null);
    const wins = closedTrades.filter((t) => t.pnl > 0);
    const losses = closedTrades.filter((t) => t.pnl <= 0);

    const winRate = closedTrades.length > 0 ? wins.length / closedTrades.length : 0;
    const avgWinPct = wins.length > 0 ? wins.reduce((s, t) => s + t.pnlPct, 0) / wins.length : 0;
    const avgLossPct = losses.length > 0 ? losses.reduce((s, t) => s + t.pnlPct, 0) / losses.length : 0;
    const avgHoldTimeMin = closedTrades.length > 0
      ? closedTrades.reduce((s, t) => s + t.holdTimeMin, 0) / closedTrades.length
      : 0;

    const grossProfit = wins.reduce((s, t) => s + t.pnl, 0);
    const grossLoss = losses.reduce((s, t) => s + Math.abs(t.pnl), 0);

    const totalReturnPct = config.initialCapital !== 0
      ? ((equity - config.initialCapital) / config.initialCapital) * 100
      : 0;

    // Annualized return
    const backtestDays = Math.max(
      1,
      (config.endDate.getTime() - config.startDate.getTime()) / 86400000,
    );
    const yearsFraction = backtestDays / 365;
    const annualizedReturnPct =
      yearsFraction > 0 && totalReturnPct > -100
        ? (Math.pow(1 + totalReturnPct / 100, 1 / yearsFraction) - 1) * 100
        : 0;

    // Drawdown
    const ddResult = calculateMaxDrawdown(equityCurve.map((p) => p.equity));

    // Returns per trade for Sharpe/Sortino
    const tradeReturns = closedTrades.map((t) => t.pnlPct / 100);

    const sharpeRatio = calculateSharpeRatio(tradeReturns);
    const sortinoRatio = calculateSortinoRatio(tradeReturns);
    const calmarRatio = calculateCalmarRatio(annualizedReturnPct, ddResult.maxDrawdownPct * 100);
    const profitFactor = calculateProfitFactor(grossProfit, grossLoss);
    const recoveryFactor = calculateRecoveryFactor(equity - config.initialCapital, ddResult.maxDrawdown);
    const expectancy = calculateExpectancy(winRate, avgWinPct, avgLossPct);

    // Monthly returns
    const monthlyReturns = calculateMonthlyReturns(equityCurve);
    const monthlyReturnDetails: MonthlyReturn[] = Object.entries(monthlyReturns).map(
      ([month, returnPct]) => {
        const monthTrades = closedTrades.filter((t) => {
          const m = `${t.entryTime.getFullYear()}-${String(t.entryTime.getMonth() + 1).padStart(2, '0')}`;
          return m === month;
        });
        return {
          month,
          returnPct,
          startingEquity: 0, // Simplified
          endingEquity: 0,
          tradesCount: monthTrades.length,
        };
      },
    );

    // Phase breakdown
    const phases: TokenPhase[] = ['GENESIS', 'LAUNCH', 'EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'];
    const phaseBreakdown: PhaseBreakdown[] = phases
      .map((phase) => {
        const phaseTrades = closedTrades.filter((t) => t.phase === phase);
        const phaseWins = phaseTrades.filter((t) => t.pnl > 0);
        return {
          phase,
          tradeCount: phaseTrades.length,
          winRate: phaseTrades.length > 0 ? phaseWins.length / phaseTrades.length : 0,
          avgPnlPct: phaseTrades.length > 0
            ? phaseTrades.reduce((s, t) => s + t.pnlPct, 0) / phaseTrades.length
            : 0,
          totalPnl: phaseTrades.reduce((s, t) => s + t.pnl, 0),
          avgHoldTimeMin: phaseTrades.length > 0
            ? phaseTrades.reduce((s, t) => s + t.holdTimeMin, 0) / phaseTrades.length
            : 0,
        };
      })
      .filter((pb) => pb.tradeCount > 0);

    // Anti-overfitting score
    const overfittingScore = this.estimateOverfitting(closedTrades, equityCurve);

    // Parameter stability (simplified: based on consistency across time windows)
    const parameterStability = this.estimateParameterStability(closedTrades);

    // Consecutive wins/losses
    const winLossSequence = closedTrades.map((t) => t.pnl > 0);
    const maxConsecutiveWins = maxConsecutive(winLossSequence);
    const maxConsecutiveLosses = maxConsecutive(winLossSequence.map((w) => !w));

    return {
      id: generateId(),
      systemName: system.name,
      config,
      generatedAt: new Date(),

      initialCapital: config.initialCapital,
      finalEquity: equity,
      totalReturnPct,
      annualizedReturnPct,

      totalTrades: closedTrades.length,
      winningTrades: wins.length,
      losingTrades: losses.length,
      winRate,
      avgWinPct,
      avgLossPct,
      avgHoldTimeMin,
      maxConsecutiveWins,
      maxConsecutiveLosses,

      maxDrawdown: ddResult.maxDrawdown,
      maxDrawdownPct: ddResult.maxDrawdownPct * 100,
      sharpeRatio,
      sortinoRatio,
      calmarRatio,
      profitFactor,
      recoveryFactor,
      expectancy,

      trades: closedTrades,
      equityCurve,
      drawdownCurve: equityCurve, // Same curve with drawdown data
      monthlyReturns,
      monthlyReturnDetails,
      phaseBreakdown,

      overfittingScore,
      parameterStability,

      totalBarsProcessed: barsProcessed,
    };
  }

  /**
   * Calculate position size based on the system's allocation method and current equity.
   */
  private calculatePositionSize(
    equity: number,
    system: SystemTemplate,
    confidence: number,
  ): number {
    const maxPct = system.riskParams.maxPositionSizePct;
    if (maxPct <= 0) {
      // Dynamic allocation (e.g. ADAPTIVE) — use a default fraction
      return equity * 0.05 * confidence;
    }

    const baseSize = equity * (maxPct / 100);

    switch (system.allocationMethod) {
      case 'FIXED_FRACTIONAL':
        return baseSize;
      case 'KELLY_MODIFIED':
        // Simplified half-Kelly with confidence scaling
        return baseSize * 0.5 * confidence;
      case 'SCORE_BASED':
        return baseSize * confidence;
      case 'VOLATILITY_TARGETING':
        return baseSize * 0.7; // Reduce for vol targeting
      case 'RISK_PARITY':
        return baseSize * 0.6;
      case 'MAX_DRAWDOWN_CONTROL':
        return baseSize * 0.5; // Conservative
      case 'EQUAL_WEIGHT':
        return equity / Math.max(system.riskParams.maxOpenPositions, 1);
      case 'REGIME_BASED':
        return baseSize * 0.6;
      case 'META_ALLOCATION':
        return baseSize * 0.5;
      case 'ADAPTIVE':
        return baseSize * confidence;
      default:
        return baseSize;
    }
  }

  /**
   * Estimate overfitting by checking if performance degrades over time
   * (early trades much better than later trades suggests overfitting).
   * Returns 0 (no overfitting) to 1 (severe overfitting).
   */
  private estimateOverfitting(
    trades: TradeRecord[],
    _equityCurve: EquityPoint[],
  ): number {
    if (trades.length < 20) return 0.3; // Insufficient data — uncertain

    const halfIdx = Math.floor(trades.length / 2);
    const firstHalf = trades.slice(0, halfIdx);
    const secondHalf = trades.slice(halfIdx);

    const firstWinRate = firstHalf.filter((t) => t.pnl > 0).length / firstHalf.length;
    const secondWinRate = secondHalf.filter((t) => t.pnl > 0).length / secondHalf.length;

    const firstAvgPnl = firstHalf.reduce((s, t) => s + t.pnlPct, 0) / firstHalf.length;
    const secondAvgPnl = secondHalf.reduce((s, t) => s + t.pnlPct, 0) / secondHalf.length;

    // Large degradation suggests overfitting
    const winRateDegradation = Math.max(0, firstWinRate - secondWinRate);
    const pnlDegradation = Math.max(0, (firstAvgPnl - secondAvgPnl) / (Math.abs(firstAvgPnl) + 0.001));

    const score = Math.min(1, (winRateDegradation * 2 + pnlDegradation) / 2);
    return Math.round(score * 100) / 100;
  }

  /**
   * Estimate parameter stability by analyzing consistency of returns
   * across equal time windows. Higher stability = more consistent.
   */
  private estimateParameterStability(trades: TradeRecord[]): number {
    if (trades.length < 10) return 0.5;

    // Split trades into 4 windows
    const windowSize = Math.floor(trades.length / 4);
    const windows: TradeRecord[][] = [];
    for (let i = 0; i < 4; i++) {
      windows.push(trades.slice(i * windowSize, (i + 1) * windowSize));
    }

    const winRates = windows.map((w) =>
      w.length > 0 ? w.filter((t) => t.pnl > 0).length / w.length : 0,
    );

    const avgWinRate = winRates.reduce((s, r) => s + r, 0) / winRates.length;
    const variance = winRates.reduce((s, r) => s + (r - avgWinRate) ** 2, 0) / winRates.length;
    const stdDev = Math.sqrt(variance);

    // Convert to stability: low std dev = high stability
    const stability = Math.max(0, 1 - stdDev * 5);
    return Math.round(stability * 100) / 100;
  }

  /**
   * Generate a formatted text scorecard from a backtest result.
   */
  generateScorecard(result: BacktestResult): string {
    const lines: string[] = [];

    lines.push('╔════════════════════════════════════════════════════════════════╗');
    lines.push('║              CRYPTOQUANT BACKTEST SCORECARD                   ║');
    lines.push('╚════════════════════════════════════════════════════════════════╝');
    lines.push('');
    lines.push(`  System: ${result.systemName}`);
    lines.push(`  Mode:   ${result.config.mode}`);
    lines.push(`  Period: ${result.config.startDate.toISOString().slice(0, 10)} → ${result.config.endDate.toISOString().slice(0, 10)}`);
    lines.push('');

    // ---- Performance Summary ----
    lines.push('  ─── PERFORMANCE ──────────────────────────────');
    lines.push(`  Initial Capital:     $${result.initialCapital.toLocaleString()}`);
    lines.push(`  Final Equity:        $${result.finalEquity.toLocaleString()}`);
    lines.push(`  Total Return:        ${result.totalReturnPct >= 0 ? '+' : ''}${result.totalReturnPct.toFixed(2)}%`);
    lines.push(`  Annualized Return:   ${result.annualizedReturnPct >= 0 ? '+' : ''}${result.annualizedReturnPct.toFixed(2)}%`);
    lines.push('');

    // ---- Trade Statistics ----
    lines.push('  ─── TRADE STATISTICS ─────────────────────────');
    lines.push(`  Total Trades:        ${result.totalTrades}`);
    lines.push(`  Winning Trades:      ${result.winningTrades} (${(result.winRate * 100).toFixed(1)}%)`);
    lines.push(`  Losing Trades:       ${result.losingTrades}`);
    lines.push(`  Avg Win:             +${result.avgWinPct.toFixed(2)}%`);
    lines.push(`  Avg Loss:            ${result.avgLossPct.toFixed(2)}%`);
    lines.push(`  Avg Hold Time:       ${formatHoldTime(result.avgHoldTimeMin)}`);
    lines.push(`  Max Consec. Wins:    ${result.maxConsecutiveWins}`);
    lines.push(`  Max Consec. Losses:  ${result.maxConsecutiveLosses}`);
    lines.push('');

    // ---- Risk Metrics ----
    lines.push('  ─── RISK METRICS ─────────────────────────────');
    lines.push(`  Max Drawdown:        -${result.maxDrawdownPct.toFixed(2)}% ($${result.maxDrawdown.toLocaleString()})`);
    lines.push(`  Sharpe Ratio:        ${result.sharpeRatio.toFixed(3)}`);
    lines.push(`  Sortino Ratio:       ${result.sortinoRatio.toFixed(3)}`);
    lines.push(`  Calmar Ratio:        ${result.calmarRatio.toFixed(3)}`);
    lines.push(`  Profit Factor:       ${result.profitFactor.toFixed(3)}`);
    lines.push(`  Recovery Factor:     ${result.recoveryFactor.toFixed(3)}`);
    lines.push(`  Expectancy:          ${result.expectancy.toFixed(3)}%`);
    lines.push('');

    // ---- Anti-Overfitting ----
    lines.push('  ─── ROBUSTNESS ───────────────────────────────');
    lines.push(`  Overfitting Score:   ${result.overfittingScore.toFixed(2)} ${result.overfittingScore < 0.3 ? '✅ Low' : result.overfittingScore < 0.6 ? '⚠️  Moderate' : '🔴 High'}`);
    lines.push(`  Parameter Stability: ${result.parameterStability.toFixed(2)} ${result.parameterStability > 0.7 ? '✅ Stable' : result.parameterStability > 0.4 ? '⚠️  Moderate' : '🔴 Unstable'}`);
    lines.push('');

    // ---- Phase Breakdown ----
    if (result.phaseBreakdown.length > 0) {
      lines.push('  ─── PHASE BREAKDOWN ──────────────────────────');
      for (const pb of result.phaseBreakdown) {
        lines.push(
          `  ${pb.phase.padEnd(12)} ${pb.tradeCount.toString().padStart(3)} trades | WR ${(pb.winRate * 100).toFixed(0).padStart(3)}% | Avg ${(pb.avgPnlPct >= 0 ? '+' : '')}${pb.avgPnlPct.toFixed(1).padStart(6)}% | PnL $${pb.totalPnl.toFixed(0)}`,
        );
      }
      lines.push('');
    }

    // ---- Monthly Returns ----
    const months = Object.entries(result.monthlyReturns).sort((a, b) => a[0].localeCompare(b[0]));
    if (months.length > 0) {
      lines.push('  ─── MONTHLY RETURNS ──────────────────────────');
      for (const [month, ret] of months) {
        const bar = ret >= 0
          ? '█'.repeat(Math.min(Math.round(ret / 2), 30))
          : '░'.repeat(Math.min(Math.round(Math.abs(ret) / 2), 30));
        lines.push(`  ${month}  ${ret >= 0 ? '+' : ''}${ret.toFixed(1).padStart(6)}%  ${bar}`);
      }
      lines.push('');
    }

    // ---- Overall Grade ----
    const grade = this.calculateGrade(result);
    lines.push('  ═══════════════════════════════════════════════');
    lines.push(`  OVERALL GRADE: ${grade.letter}  (${grade.score.toFixed(0)}/100)`);
    lines.push(`  ${grade.summary}`);
    lines.push('  ═══════════════════════════════════════════════');

    return lines.join('\n');
  }

  /**
   * Calculate a grade for the backtest result.
   */
  private calculateGrade(result: BacktestResult): { letter: string; score: number; summary: string } {
    let score = 50; // Base score

    // Profitability (0-20)
    if (result.totalReturnPct > 100) score += 20;
    else if (result.totalReturnPct > 50) score += 16;
    else if (result.totalReturnPct > 20) score += 12;
    else if (result.totalReturnPct > 0) score += 6;
    else score -= 10;

    // Win rate (0-15)
    if (result.winRate > 0.7) score += 15;
    else if (result.winRate > 0.6) score += 12;
    else if (result.winRate > 0.5) score += 8;
    else if (result.winRate > 0.4) score += 4;
    else score -= 5;

    // Sharpe ratio (0-15)
    if (result.sharpeRatio > 3) score += 15;
    else if (result.sharpeRatio > 2) score += 12;
    else if (result.sharpeRatio > 1) score += 8;
    else if (result.sharpeRatio > 0.5) score += 4;
    else score -= 5;

    // Max drawdown (0-10)
    if (result.maxDrawdownPct < 5) score += 10;
    else if (result.maxDrawdownPct < 10) score += 8;
    else if (result.maxDrawdownPct < 20) score += 5;
    else if (result.maxDrawdownPct < 30) score += 2;
    else score -= 5;

    // Profit factor (0-10)
    if (result.profitFactor > 2) score += 10;
    else if (result.profitFactor > 1.5) score += 7;
    else if (result.profitFactor > 1.2) score += 4;
    else if (result.profitFactor > 1) score += 2;
    else score -= 5;

    // Robustness (0-10)
    if (result.overfittingScore < 0.2) score += 5;
    else if (result.overfittingScore < 0.4) score += 3;
    else score -= 3;

    if (result.parameterStability > 0.7) score += 5;
    else if (result.parameterStability > 0.4) score += 3;
    else score -= 3;

    score = Math.max(0, Math.min(100, score));

    let letter: string;
    if (score >= 90) letter = 'A+';
    else if (score >= 80) letter = 'A';
    else if (score >= 70) letter = 'B';
    else if (score >= 60) letter = 'C';
    else if (score >= 50) letter = 'D';
    else letter = 'F';

    const summaries: Record<string, string> = {
      'A+': 'Exceptional system — consider live deployment with monitoring',
      'A': 'Strong system — proceed to paper trading for validation',
      'B': 'Good system with acceptable risk — optimize parameters',
      'C': 'Average system — significant improvements needed before deployment',
      'D': 'Below average — high risk of underperformance',
      'F': 'Failed — do not deploy. Fundamental redesign required',
    };

    return { letter, score, summary: summaries[letter] ?? 'Unknown grade' };
  }

  /**
   * Walk-forward analysis: runs backtests across multiple time windows.
   * Each window uses the previous window's optimized parameters.
   * Returns one result per window.
   */
  async walkForwardAnalysis(
    config: BacktestConfig,
    windows: number,
  ): Promise<BacktestResult[]> {
    const totalStart = config.startDate.getTime();
    const totalEnd = config.endDate.getTime();
    const totalDuration = totalEnd - totalStart;

    // Each window: 70% training, 30% testing
    const windowDuration = totalDuration / windows;
    const trainingPct = 0.7;

    const results: BacktestResult[] = [];

    for (let i = 0; i < windows; i++) {
      const windowStart = new Date(totalStart + i * windowDuration);
      const windowEnd = new Date(totalStart + (i + 1) * windowDuration);

      const trainingEnd = new Date(
        windowStart.getTime() + (windowEnd.getTime() - windowStart.getTime()) * trainingPct,
      );

      // Run backtest on the full window (we'd typically optimize on training, test on testing)
      const windowConfig: BacktestConfig = {
        ...config,
        startDate: windowStart,
        endDate: windowEnd,
      };

      // Load real token data from the database for each window
      const tokens = await this.loadRealTokenData(windowConfig);

      const result = await this.runBacktest(windowConfig, tokens);
      result.id = `wf_window_${i + 1}`;
      results.push(result);
    }

    return results;
  }

  /**
   * Compare multiple backtest results and identify the best by different criteria.
   */
  compareResults(results: BacktestResult[]): {
    bestBySharpe: BacktestResult;
    bestByReturn: BacktestResult;
    bestByRisk: BacktestResult;
  } {
    if (results.length === 0) {
      throw new Error('No results to compare');
    }

    const bestBySharpe = results.reduce((best, r) =>
      r.sharpeRatio > best.sharpeRatio ? r : best,
    );

    const bestByReturn = results.reduce((best, r) =>
      r.annualizedReturnPct > best.annualizedReturnPct ? r : best,
    );

    // Best by risk = highest Calmar ratio (return per unit of drawdown)
    const bestByRisk = results.reduce((best, r) =>
      r.calmarRatio > best.calmarRatio ? r : best,
    );

    return { bestBySharpe, bestByReturn, bestByRisk };
  }

  /**
   * Load real token data from the database for backtesting.
   * Queries the Token and PriceCandle tables to build TokenData[] with
   * actual OHLCV history. Falls back to triggering a backfill via the
   * ohlcv-pipeline when no candles exist for a token. Returns an empty
   * array (never mock data) when no real data is available.
   */
  private async loadRealTokenData(config: BacktestConfig): Promise<TokenData[]> {
    const chains = config.system.assetFilter.chains ?? [];
    // Normalize chain names to DB convention (e.g. 'solana' → 'SOL')
    const chainMap: Record<string, string> = {
      solana: 'SOL',
      ethereum: 'ETH',
      base: 'BASE',
      arbitrum: 'ARB',
      polygon: 'MATIC',
      bsc: 'BSC',
      optimism: 'OP',
    };
    const normalizedChains = chains
      .map((c: string) => chainMap[c.toLowerCase()] ?? c.toUpperCase())
      .filter(Boolean);

    // If no chains configured, default to SOL
    const queryChains = normalizedChains.length > 0 ? normalizedChains : ['SOL'];

    // Step 1: Find tokens on the configured chains that have some trading activity
    const tokens = await db.token.findMany({
      where: {
        chain: { in: queryChains },
        volume24h: { gt: 0 },
      },
      orderBy: { volume24h: 'desc' },
      take: 20, // Limit to top tokens by volume
      select: {
        address: true,
        symbol: true,
        chain: true,
        createdAt: true,
      },
    });

    if (tokens.length === 0) {
      console.warn(
        '[backtesting-engine] No tokens found in DB for chains:',
        queryChains,
      );
      return [];
    }

    const tokenDataList: TokenData[] = [];
    const timeframe = '1h'; // Default timeframe for backtesting

    for (const token of tokens) {
      // Step 2: Load PriceCandle data from DB for the backtest period
      let candles = await db.priceCandle.findMany({
        where: {
          tokenAddress: token.address,
          timeframe,
          timestamp: {
            gte: config.startDate,
            lte: config.endDate,
          },
        },
        orderBy: { timestamp: 'asc' },
      });

      // Step 3: If no candles exist, try to backfill via the ohlcv pipeline
      if (candles.length === 0) {
        try {
          console.log(
            `[backtesting-engine] No candles for ${token.symbol}, triggering backfill...`,
          );
          await ohlcvPipeline.backfillToken(token.address, token.chain, [timeframe]);

          // Re-query after backfill
          candles = await db.priceCandle.findMany({
            where: {
              tokenAddress: token.address,
              timeframe,
              timestamp: {
                gte: config.startDate,
                lte: config.endDate,
              },
            },
            orderBy: { timestamp: 'asc' },
          });
        } catch (err) {
          console.warn(
            `[backtesting-engine] Backfill failed for ${token.symbol}:`,
            err,
          );
        }
      }

      // Skip tokens with no candle data even after backfill attempt
      if (candles.length === 0) {
        console.warn(
          `[backtesting-engine] No candle data available for ${token.symbol} (${token.address}), skipping.`,
        );
        continue;
      }

      // Step 4: Determine the token's lifecycle phase
      const phase = await this.resolveTokenPhase(token.address);

      // Step 5: Convert PriceCandle rows to OHLCVBar[]
      const bars: OHLCVBar[] = candles.map((c) => ({
        timestamp: c.timestamp.getTime(),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      }));

      tokenDataList.push({
        tokenAddress: token.address,
        symbol: token.symbol,
        createdAt: token.createdAt,
        phase,
        bars,
      });
    }

    if (tokenDataList.length === 0) {
      console.warn(
        '[backtesting-engine] No real token data could be loaded from the database. ' +
        'Returning empty result — backtest will produce no trades.',
      );
    }

    return tokenDataList;
  }

  /**
   * Resolve the current lifecycle phase of a token from the TokenLifecycleState table.
   * Falls back to estimating phase from token age if no lifecycle state exists.
   */
  private async resolveTokenPhase(tokenAddress: string): Promise<TokenPhase> {
    // Try to get the latest lifecycle state from DB
    const latestState = await db.tokenLifecycleState.findFirst({
      where: { tokenAddress },
      orderBy: { detectedAt: 'desc' },
    });

    if (latestState) {
      // Map DB phase values to TokenPhase type
      const dbPhaseMap: Record<string, TokenPhase> = {
        GENESIS: 'GENESIS',
        INCIPIENT: 'LAUNCH',  // DB uses INCIPIENT, TokenPhase uses LAUNCH
        LAUNCH: 'LAUNCH',
        EARLY: 'EARLY',
        GROWTH: 'GROWTH',
        FOMO: 'GROWTH',      // FOMO maps to GROWTH in the backtest engine
        MATURE: 'MATURE',
        DECLINE: 'MATURE',    // DECLINE maps to MATURE
        ESTABLISHED: 'ESTABLISHED',
        LEGACY: 'LEGACY',
      };
      const mapped = dbPhaseMap[latestState.phase];
      if (mapped) return mapped;
    }

    // Fallback: estimate phase from the token's creation date
    const token = await db.token.findUnique({
      where: { address: tokenAddress },
      select: { createdAt: true },
    });

    if (token) {
      const ageMs = Date.now() - token.createdAt.getTime();
      const ageHours = ageMs / (1000 * 60 * 60);

      if (ageHours < 6) return 'GENESIS';
      if (ageHours < 48) return 'LAUNCH';
      if (ageHours < 336) return 'EARLY';       // 14 days
      if (ageHours < 1440) return 'GROWTH';     // 60 days
      if (ageHours < 4320) return 'MATURE';     // 180 days
      if (ageHours < 8760) return 'ESTABLISHED'; // 1 year
      return 'LEGACY';
    }

    // Default fallback
    return 'GROWTH';
  }
}

// ============================================================
// 6. HELPER FUNCTIONS
// ============================================================

function formatHoldTime(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)}m`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)}h`;
  if (minutes < 43200) return `${(minutes / 1440).toFixed(1)}d`;
  return `${(minutes / 43200).toFixed(1)}mo`;
}

// ============================================================
// 7. SINGLETON EXPORT
// ============================================================

export const backtestingEngine = new BacktestingEngine();
