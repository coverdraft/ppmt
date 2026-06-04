/**
 * Autonomous Execution Engine - CryptoQuant Terminal
 *
 * The bridge between the Brain Analysis Pipeline's strategy output
 * and actual (or simulated) trade execution.
 *
 * Architecture:
 * ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
 * │ Brain Pipeline    │───>│ Execution Engine  │───>│ Paper / Live     │
 * │ (StrategySelection)│   │ (Order Manager)   │   │ Executor         │
 * └──────────────────┘    └──────────────────┘    └──────────────────┘
 *        │                       │                        │
 *        ▼                       ▼                        ▼
 * ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
 * │ PipelineResult    │    │ Position Tracker  │    │ DEX Router      │
 * │ (Full context)    │    │ (SL/TP/Trailing) │    │ (Future: Jupiter)│
 * └──────────────────┘    └──────────────────┘    └──────────────────┘
 *
 * Modes:
 *   PAPER — Simulated execution with realistic slippage & fees.
 *           Tracks virtual PnL, win rate, Sharpe ratio.
 *           Stores trades in BacktestOperation with backtestId = "paper_trading".
 *
 *   LIVE  — Real wallet execution (interface defined, not implemented yet).
 *           For now, logs intent and stores it without broadcasting tx.
 *           When ready for production, only the executor layer changes.
 *
 * Key integration point:
 *   The brain scheduler calls `updateOpenPositions()` every cycle
 *   to check stop-loss / take-profit / trailing stops on open positions.
 *
 * SERVER-SIDE ONLY — no client-side imports.
 */

import { db } from '../db';
import type { StrategySelection, PipelineResult } from './brain-analysis-pipeline';

// ============================================================
// TYPES & INTERFACES
// ============================================================

/** A single execution order created from a pipeline strategy signal */
export interface ExecutionOrder {
  id: string;
  tokenAddress: string;
  symbol: string;
  chain: string;

  // From StrategySelection
  direction: 'LONG' | 'SHORT';
  systemCategory: string;
  systemName: string;

  // Execution details
  orderType: 'MARKET' | 'LIMIT';
  positionSizeUsd: number;
  positionSizePctOfCapital: number;
  entryPrice: number;
  stopLossPct: number;
  takeProfitPct: number;
  trailingStopPct?: number;

  // State
  status: 'PENDING' | 'FILLED' | 'PARTIALLY_FILLED' | 'CANCELLED' | 'EXPIRED';
  mode: 'PAPER' | 'LIVE';

  // Timing
  createdAt: Date;
  filledAt?: Date;
  closedAt?: Date;

  // Results (after close)
  exitPrice?: number;
  exitReason?: string;
  pnlUsd?: number;
  pnlPct?: number;
  holdTimeMin?: number;
  maxFavorableExcursion?: number;
  maxAdverseExcursion?: number;

  // Context from analysis
  entryConfidence: number;
  tokenPhase: string;
  dominantPattern: string | null;
  dominantArchetype: string;
  netBehaviorFlow: string;
  reasoning: string[];
}

/** Current portfolio state snapshot */
export interface ExecutionPortfolio {
  totalCapitalUsd: number;
  availableCapitalUsd: number;
  allocatedCapitalUsd: number;
  openPositions: number;
  maxPositions: number;
  unrealizedPnlUsd: number;
  realizedPnlUsd: number;
  totalFeesUsd: number;
  winRate: number;
  sharpeRatio: number;
  mode: 'PAPER' | 'LIVE';
}

/** Engine configuration */
export interface ExecutionEngineConfig {
  mode: 'PAPER' | 'LIVE';
  initialCapitalUsd: number;
  maxOpenPositions: number;
  maxPositionPctOfCapital: number;
  defaultSlippagePct: number;
  feePct: number;
  autoExecute: boolean;
  minConfidenceToExecute: number;
  requireSystemMatch: boolean;
}

/** Aggregate performance metrics over the engine's lifetime */
export interface PerformanceMetrics {
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
  avgWinPct: number;
  avgLossPct: number;
  profitFactor: number;
  totalPnlUsd: number;
  totalFeesUsd: number;
  feeAdjustedPnlUsd: number;
  sharpeRatio: number;
  maxDrawdownPct: number;
  avgHoldTimeMin: number;
  totalReturnPct: number;
  bestTradePct: number;
  worstTradePct: number;
  consecutiveWins: number;
  consecutiveLosses: number;
  currentStreak: 'WIN' | 'LOSS' | 'NONE';
  mode: 'PAPER' | 'LIVE';
}

/** Internal position tracking (in-memory) */
interface OpenPosition {
  order: ExecutionOrder;
  quantity: number;
  highWaterMark: number;
  lowWaterMark: number;
  stopLossPrice: number;
  takeProfitPrice: number;
  trailingStopActivated: boolean;
  trailingStopPrice?: number;
}

/** Result of executing from a strategy signal */
interface ExecutionResult {
  executed: boolean;
  orderId: string | null;
  reason: string;
  order?: ExecutionOrder;
}

/** Result of closing a position */
interface CloseResult {
  closed: boolean;
  orderId: string;
  pnlUsd: number;
  pnlPct: number;
  exitReason: string;
  holdTimeMin: number;
}

/** Result of updating open positions (from scheduler) */
interface UpdateResult {
  checked: number;
  closed: number;
  stillOpen: number;
  errors: number;
  closedOrders: CloseResult[];
}

// ============================================================
// DEFAULT CONFIGURATION
// ============================================================

export const DEFAULT_EXECUTION_CONFIG: ExecutionEngineConfig = {
  mode: 'PAPER',
  initialCapitalUsd: 100,
  maxOpenPositions: 3,
  maxPositionPctOfCapital: 10,
  defaultSlippagePct: 0.5,
  feePct: 0.3,
  autoExecute: true,
  minConfidenceToExecute: 0.3,
  requireSystemMatch: false,
};

/** Special backtestId used to identify paper trading records in BacktestOperation */
const PAPER_TRADING_BACKTEST_ID = 'paper_trading_autonomous';
/** Special systemId used for paper trading records in BacktestOperation */
const PAPER_TRADING_SYSTEM_ID = 'autonomous_execution';

// ============================================================
// UTILITY HELPERS
// ============================================================

/** Generate a unique order ID */
function generateOrderId(): string {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 8);
  return `exo_${timestamp}_${random}`;
}

/** Round a number to a given number of decimal places */
function roundTo(value: number, decimals: number): number {
  const factor = Math.pow(10, decimals);
  return Math.round(value * factor) / factor;
}

// ============================================================
// AUTONOMOUS EXECUTION ENGINE CLASS
// ============================================================

class AutonomousExecutionEngine {
  private config: ExecutionEngineConfig;
  private openPositions: Map<string, OpenPosition> = new Map();
  private closedOrders: ExecutionOrder[] = [];
  private currentCapital: number;
  private peakCapital: number;
  private totalFeesUsd: number = 0;

  constructor(config: Partial<ExecutionEngineConfig> = {}) {
    this.config = { ...DEFAULT_EXECUTION_CONFIG, ...config };
    this.currentCapital = this.config.initialCapitalUsd;
    this.peakCapital = this.config.initialCapitalUsd;
  }

  // ============================================================
  // 1. EXECUTE FROM STRATEGY
  // ============================================================

  /**
   * Take a pipeline result and create an execution order.
   *
   * This is the primary entry point called by the brain scheduler
   * after a PipelineResult with a non-HOLD/WAIT strategy is produced.
   *
   * @param strategy - The strategy selection from the pipeline
   * @param pipelineResult - The full pipeline result for context
   * @returns ExecutionResult indicating whether the order was placed
   */
  async executeFromStrategy(
    strategy: StrategySelection,
    pipelineResult: PipelineResult
  ): Promise<ExecutionResult> {
    try {
      // ── Validate direction ──────────────────────────────────────
      if (strategy.direction === 'HOLD' || strategy.direction === 'WAIT') {
        return {
          executed: false,
          orderId: null,
          reason: `Strategy direction is ${strategy.direction} — no execution needed`,
        };
      }

      // ── Check confidence threshold ──────────────────────────────
      if (strategy.entryConfidence < this.config.minConfidenceToExecute) {
        return {
          executed: false,
          orderId: null,
          reason: `Entry confidence ${strategy.entryConfidence.toFixed(3)} below minimum ${this.config.minConfidenceToExecute}`,
        };
      }

      // ── Check max open positions ────────────────────────────────
      if (this.openPositions.size >= this.config.maxOpenPositions) {
        return {
          executed: false,
          orderId: null,
          reason: `Max open positions reached (${this.openPositions.size}/${this.config.maxOpenPositions})`,
        };
      }

      // ── Check if already have a position in this token ──────────
      const existingPosition = Array.from(this.openPositions.values()).find(
        (p) => p.order.tokenAddress === pipelineResult.tokenAddress
      );
      if (existingPosition) {
        return {
          executed: false,
          orderId: null,
          reason: `Already have open position in ${pipelineResult.symbol || pipelineResult.tokenAddress}`,
        };
      }

      // ── Check available capital ─────────────────────────────────
      const allocatedCapital = Array.from(this.openPositions.values()).reduce(
        (sum, p) => sum + p.order.positionSizeUsd, 0
      );
      const availableCapital = this.currentCapital - allocatedCapital;
      if (strategy.positionSizeUsd > availableCapital) {
        return {
          executed: false,
          orderId: null,
          reason: `Insufficient available capital: need $${strategy.positionSizeUsd.toFixed(2)}, have $${availableCapital.toFixed(2)}`,
        };
      }

      // ── Check max position % of capital ─────────────────────────
      const positionPct = this.currentCapital > 0
        ? (strategy.positionSizeUsd / this.currentCapital) * 100
        : 0;
      if (positionPct > this.config.maxPositionPctOfCapital) {
        return {
          executed: false,
          orderId: null,
          reason: `Position size ${positionPct.toFixed(1)}% exceeds max ${this.config.maxPositionPctOfCapital}% of capital`,
        };
      }

      // ── Check auto-execute flag ─────────────────────────────────
      if (!this.config.autoExecute) {
        return {
          executed: false,
          orderId: null,
          reason: 'Auto-execute is disabled — orders require manual approval',
        };
      }

      // ── Get current price from DB ──────────────────────────────
      const entryPrice = await this.fetchCurrentPrice(pipelineResult.tokenAddress);
      if (entryPrice <= 0) {
        return {
          executed: false,
          orderId: null,
          reason: `Cannot determine current price for ${pipelineResult.tokenAddress}`,
        };
      }

      // ── Create the execution order ──────────────────────────────
      const orderId = generateOrderId();
      const now = new Date();

      // Apply simulated slippage to entry price
      const slippageMultiplier = strategy.direction === 'LONG'
        ? 1 + (this.config.defaultSlippagePct / 100)
        : 1 - (this.config.defaultSlippagePct / 100);
      const filledPrice = roundTo(entryPrice * slippageMultiplier, 8);

      // Deduct entry fee
      const entryFee = roundTo(strategy.positionSizeUsd * (this.config.feePct / 100), 4);

      const order: ExecutionOrder = {
        id: orderId,
        tokenAddress: pipelineResult.tokenAddress,
        symbol: pipelineResult.symbol,
        chain: pipelineResult.chain,
        direction: strategy.direction,
        systemCategory: strategy.systemCategory,
        systemName: strategy.systemName,
        orderType: 'MARKET',
        positionSizeUsd: roundTo(strategy.positionSizeUsd - entryFee, 2),
        positionSizePctOfCapital: strategy.positionSizePctOfCapital,
        entryPrice: filledPrice,
        stopLossPct: strategy.stopLossPct,
        takeProfitPct: strategy.takeProfitPct,
        trailingStopPct: strategy.trailingStopPct,
        status: 'FILLED',
        mode: this.config.mode,
        createdAt: now,
        filledAt: now,
        entryConfidence: strategy.entryConfidence,
        tokenPhase: strategy.tokenPhase,
        dominantPattern: strategy.dominantPattern,
        dominantArchetype: strategy.dominantTraderArchetype,
        netBehaviorFlow: strategy.netBehaviorFlow,
        reasoning: strategy.reasoning,
      };

      // ── Calculate position parameters ──────────────────────────
      const quantity = order.positionSizeUsd / filledPrice;
      const stopLossPrice = strategy.direction === 'LONG'
        ? roundTo(filledPrice * (1 - strategy.stopLossPct / 100), 8)
        : roundTo(filledPrice * (1 + strategy.stopLossPct / 100), 8);
      const takeProfitPrice = strategy.direction === 'LONG'
        ? roundTo(filledPrice * (1 + strategy.takeProfitPct / 100), 8)
        : roundTo(filledPrice * (1 - strategy.takeProfitPct / 100), 8);

      const position: OpenPosition = {
        order,
        quantity: roundTo(quantity, 8),
        highWaterMark: filledPrice,
        lowWaterMark: filledPrice,
        stopLossPrice,
        takeProfitPrice,
        trailingStopActivated: false,
        trailingStopPrice: undefined,
      };

      // ── Track fee ──────────────────────────────────────────────
      this.totalFeesUsd += entryFee;

      // ── Store in memory ─────────────────────────────────────────
      this.openPositions.set(orderId, position);

      // ── Persist to database ─────────────────────────────────────
      await this.persistOrderToDb(order, pipelineResult);

      // ── Log execution ───────────────────────────────────────────
      const modeLabel = this.config.mode === 'PAPER' ? '📄' : '🔴';
      console.info(
        `[AutoExec] ${modeLabel} ${this.config.mode} FILLED: ${order.direction} ${order.symbol} @ $${filledPrice.toFixed(6)} | ` +
        `Size: $${order.positionSizeUsd.toFixed(2)} (${order.positionSizePctOfCapital}% of capital) | ` +
        `SL: ${order.stopLossPct}% TP: ${order.takeProfitPct}% | ` +
        `Confidence: ${(order.entryConfidence * 100).toFixed(1)}% | ` +
        `System: ${order.systemCategory}/${order.systemName} | ` +
        `Phase: ${order.tokenPhase} | Flow: ${order.netBehaviorFlow}`
      );

      return {
        executed: true,
        orderId,
        reason: `Order filled at $${filledPrice.toFixed(6)} (${this.config.mode} mode)`,
        order,
      };
    } catch (error) {
      console.error(
        '[AutoExec] Error executing from strategy:',
        error instanceof Error ? error.message : String(error)
      );
      return {
        executed: false,
        orderId: null,
        reason: `Execution error: ${error instanceof Error ? error.message : String(error)}`,
      };
    }
  }

  // ============================================================
  // 2. CLOSE POSITION
  // ============================================================

  /**
   * Close an open position by orderId.
   *
   * @param orderId - The ID of the order to close
   * @param exitReason - Reason for closing (STOP_LOSS, TAKE_PROFIT, TRAILING_STOP, MANUAL, SIGNAL_EXIT, TIMEOUT)
   * @param exitPriceOverride - Optional exit price override; if not provided, uses current market price
   * @returns CloseResult with PnL details
   */
  async closePosition(
    orderId: string,
    exitReason: string,
    exitPriceOverride?: number
  ): Promise<CloseResult> {
    const position = this.openPositions.get(orderId);

    if (!position) {
      return {
        closed: false,
        orderId,
        pnlUsd: 0,
        pnlPct: 0,
        exitReason: 'Position not found',
        holdTimeMin: 0,
      };
    }

    try {
      // ── Get exit price ──────────────────────────────────────────
      let exitPrice = exitPriceOverride ?? await this.fetchCurrentPrice(position.order.tokenAddress);
      if (exitPrice <= 0) {
        exitPrice = position.order.entryPrice; // Fallback to entry if no price available
      }

      // ── Apply slippage on exit ──────────────────────────────────
      const exitSlippageMultiplier = position.order.direction === 'LONG'
        ? 1 - (this.config.defaultSlippagePct / 100)
        : 1 + (this.config.defaultSlippagePct / 100);
      const adjustedExitPrice = roundTo(exitPrice * exitSlippageMultiplier, 8);

      // ── Calculate PnL ───────────────────────────────────────────
      const entryValue = position.quantity * position.order.entryPrice;
      const exitValue = position.quantity * adjustedExitPrice;

      // Deduct exit fee
      const exitFee = roundTo(exitValue * (this.config.feePct / 100), 4);
      this.totalFeesUsd += exitFee;

      let pnlUsd: number;
      let pnlPct: number;

      if (position.order.direction === 'LONG') {
        pnlUsd = roundTo(exitValue - entryValue - exitFee, 4);
        pnlPct = entryValue > 0 ? roundTo((pnlUsd / entryValue) * 100, 4) : 0;
      } else {
        // SHORT: profit when price goes down
        pnlUsd = roundTo(entryValue - exitValue - exitFee, 4);
        pnlPct = entryValue > 0 ? roundTo((pnlUsd / entryValue) * 100, 4) : 0;
      }

      // ── Calculate hold time ─────────────────────────────────────
      const closedAt = new Date();
      const holdTimeMin = roundTo(
        (closedAt.getTime() - position.order.createdAt.getTime()) / 60000,
        2
      );

      // ── Calculate MFE / MAE ─────────────────────────────────────
      const mfe = position.order.direction === 'LONG'
        ? roundTo(((position.highWaterMark - position.order.entryPrice) / position.order.entryPrice) * 100, 2)
        : roundTo(((position.order.entryPrice - position.lowWaterMark) / position.order.entryPrice) * 100, 2);

      const mae = position.order.direction === 'LONG'
        ? roundTo(((position.order.entryPrice - position.lowWaterMark) / position.order.entryPrice) * 100, 2)
        : roundTo(((position.highWaterMark - position.order.entryPrice) / position.order.entryPrice) * 100, 2);

      // ── Update the order ────────────────────────────────────────
      position.order.status = 'FILLED'; // Keep as FILLED (completed)
      position.order.closedAt = closedAt;
      position.order.exitPrice = adjustedExitPrice;
      position.order.exitReason = exitReason;
      position.order.pnlUsd = pnlUsd;
      position.order.pnlPct = pnlPct;
      position.order.holdTimeMin = holdTimeMin;
      position.order.maxFavorableExcursion = mfe;
      position.order.maxAdverseExcursion = mae;

      // ── Update capital ──────────────────────────────────────────
      this.currentCapital += pnlUsd;
      if (this.currentCapital > this.peakCapital) {
        this.peakCapital = this.currentCapital;
      }

      // ── Move to closed orders ───────────────────────────────────
      this.openPositions.delete(orderId);
      this.closedOrders.push(position.order);

      // ── Persist closure to database ─────────────────────────────
      await this.persistCloseToDb(position.order);

      // ── Log closure ─────────────────────────────────────────────
      const pnlEmoji = pnlUsd >= 0 ? '✅' : '❌';
      const modeLabel = this.config.mode === 'PAPER' ? '📄' : '🔴';
      console.info(
        `[AutoExec] ${modeLabel} ${pnlEmoji} CLOSED: ${position.order.direction} ${position.order.symbol} | ` +
        `Reason: ${exitReason} | PnL: $${pnlUsd.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%) | ` +
        `Hold: ${holdTimeMin.toFixed(0)}min | Capital: $${this.currentCapital.toFixed(2)} | ` +
        `MFE: ${mfe.toFixed(2)}% MAE: ${mae.toFixed(2)}%`
      );

      return {
        closed: true,
        orderId,
        pnlUsd,
        pnlPct,
        exitReason,
        holdTimeMin,
      };
    } catch (error) {
      console.error(
        `[AutoExec] Error closing position ${orderId}:`,
        error instanceof Error ? error.message : String(error)
      );
      return {
        closed: false,
        orderId,
        pnlUsd: 0,
        pnlPct: 0,
        exitReason: `Close error: ${error instanceof Error ? error.message : String(error)}`,
        holdTimeMin: 0,
      };
    }
  }

  // ============================================================
  // 3. UPDATE OPEN POSITIONS
  // ============================================================

  /**
   * Check all open positions against current prices.
   * Apply stop-loss, take-profit, and trailing stop rules.
   *
   * This method is designed to be called by the brain scheduler
   * on every cycle (e.g., every 5 minutes).
   *
   * @returns UpdateResult with details of what was checked and closed
   */
  async updateOpenPositions(): Promise<UpdateResult> {
    const result: UpdateResult = {
      checked: 0,
      closed: 0,
      stillOpen: 0,
      errors: 0,
      closedOrders: [],
    };

    const positionIds = Array.from(this.openPositions.keys());

    for (const orderId of positionIds) {
      const position = this.openPositions.get(orderId);
      if (!position) continue;

      result.checked++;

      try {
        // ── Fetch current price ──────────────────────────────────
        const currentPrice = await this.fetchCurrentPrice(position.order.tokenAddress);
        if (currentPrice <= 0) {
          result.stillOpen++;
          continue;
        }

        // ── Update high/low water marks ──────────────────────────
        if (currentPrice > position.highWaterMark) {
          position.highWaterMark = currentPrice;
        }
        if (currentPrice < position.lowWaterMark) {
          position.lowWaterMark = currentPrice;
        }

        // ── Calculate current price change from entry ────────────
        const priceChangePct = position.order.direction === 'LONG'
          ? ((currentPrice - position.order.entryPrice) / position.order.entryPrice) * 100
          : ((position.order.entryPrice - currentPrice) / position.order.entryPrice) * 100;

        // ── Check STOP LOSS ──────────────────────────────────────
        if (position.order.direction === 'LONG' && currentPrice <= position.stopLossPrice) {
          const closeResult = await this.closePosition(orderId, 'STOP_LOSS', currentPrice);
          if (closeResult.closed) {
            result.closed++;
            result.closedOrders.push(closeResult);
          }
          continue;
        }
        if (position.order.direction === 'SHORT' && currentPrice >= position.stopLossPrice) {
          const closeResult = await this.closePosition(orderId, 'STOP_LOSS', currentPrice);
          if (closeResult.closed) {
            result.closed++;
            result.closedOrders.push(closeResult);
          }
          continue;
        }

        // ── Check TAKE PROFIT ────────────────────────────────────
        if (position.order.direction === 'LONG' && currentPrice >= position.takeProfitPrice) {
          const closeResult = await this.closePosition(orderId, 'TAKE_PROFIT', currentPrice);
          if (closeResult.closed) {
            result.closed++;
            result.closedOrders.push(closeResult);
          }
          continue;
        }
        if (position.order.direction === 'SHORT' && currentPrice <= position.takeProfitPrice) {
          const closeResult = await this.closePosition(orderId, 'TAKE_PROFIT', currentPrice);
          if (closeResult.closed) {
            result.closed++;
            result.closedOrders.push(closeResult);
          }
          continue;
        }

        // ── Check TRAILING STOP ──────────────────────────────────
        if (position.order.trailingStopPct && position.order.trailingStopPct > 0) {
          // Activate trailing stop once price moves in our favor by the take profit activation threshold
          // Use half of takeProfitPct as activation threshold
          const activationThresholdPct = position.order.takeProfitPct * 0.5;

          if (!position.trailingStopActivated && priceChangePct >= activationThresholdPct) {
            position.trailingStopActivated = true;
          }

          if (position.trailingStopActivated) {
            // Calculate trailing stop price from high water mark
            const newTrailingStopPrice = position.order.direction === 'LONG'
              ? roundTo(position.highWaterMark * (1 - position.order.trailingStopPct / 100), 8)
              : roundTo(position.lowWaterMark * (1 + position.order.trailingStopPct / 100), 8);

            // Update trailing stop if it moved in our favor
            if (position.trailingStopPrice === undefined ||
                (position.order.direction === 'LONG' && newTrailingStopPrice > position.trailingStopPrice) ||
                (position.order.direction === 'SHORT' && newTrailingStopPrice < position.trailingStopPrice)) {
              position.trailingStopPrice = newTrailingStopPrice;
            }

            // Check if trailing stop was hit
            if (position.trailingStopPrice !== undefined) {
              if (position.order.direction === 'LONG' && currentPrice <= position.trailingStopPrice) {
                const closeResult = await this.closePosition(orderId, 'TRAILING_STOP', currentPrice);
                if (closeResult.closed) {
                  result.closed++;
                  result.closedOrders.push(closeResult);
                }
                continue;
              }
              if (position.order.direction === 'SHORT' && currentPrice >= position.trailingStopPrice) {
                const closeResult = await this.closePosition(orderId, 'TRAILING_STOP', currentPrice);
                if (closeResult.closed) {
                  result.closed++;
                  result.closedOrders.push(closeResult);
                }
                continue;
              }
            }
          }
        }

        // ── Check TIMEOUT (24h max hold for paper, 4h for live) ──
        const holdTimeMin = (Date.now() - position.order.createdAt.getTime()) / 60000;
        const maxHoldTimeMin = this.config.mode === 'PAPER' ? 24 * 60 : 4 * 60;
        if (holdTimeMin >= maxHoldTimeMin) {
          const closeResult = await this.closePosition(orderId, 'TIMEOUT', currentPrice);
          if (closeResult.closed) {
            result.closed++;
            result.closedOrders.push(closeResult);
          }
          continue;
        }

        result.stillOpen++;
      } catch (error) {
        result.errors++;
        console.error(
          `[AutoExec] Error updating position ${orderId}:`,
          error instanceof Error ? error.message : String(error)
        );
        result.stillOpen++;
      }
    }

    return result;
  }

  // ============================================================
  // 4. GET PORTFOLIO
  // ============================================================

  /**
   * Returns the current portfolio state snapshot.
   * Includes unrealized PnL from open positions and aggregate metrics.
   */
  async getPortfolio(): Promise<ExecutionPortfolio> {
    let unrealizedPnlUsd = 0;

    for (const position of Array.from(this.openPositions.values())) {
      try {
        const currentPrice = await this.fetchCurrentPrice(position.order.tokenAddress);
        if (currentPrice > 0) {
          if (position.order.direction === 'LONG') {
            unrealizedPnlUsd += (currentPrice - position.order.entryPrice) * position.quantity;
          } else {
            unrealizedPnlUsd += (position.order.entryPrice - currentPrice) * position.quantity;
          }
        }
      } catch {
        // Skip positions with price fetch errors
      }
    }

    const allocatedCapital = Array.from(this.openPositions.values()).reduce(
      (sum, p) => sum + p.order.positionSizeUsd, 0
    );

    const realizedPnlUsd = this.closedOrders.reduce((sum, o) => sum + (o.pnlUsd ?? 0), 0);
    const metrics = this.calculatePerformanceMetrics();

    return {
      totalCapitalUsd: roundTo(this.currentCapital, 2),
      availableCapitalUsd: roundTo(this.currentCapital - allocatedCapital, 2),
      allocatedCapitalUsd: roundTo(allocatedCapital, 2),
      openPositions: this.openPositions.size,
      maxPositions: this.config.maxOpenPositions,
      unrealizedPnlUsd: roundTo(unrealizedPnlUsd, 2),
      realizedPnlUsd: roundTo(realizedPnlUsd, 2),
      totalFeesUsd: roundTo(this.totalFeesUsd, 2),
      winRate: metrics.winRate,
      sharpeRatio: metrics.sharpeRatio,
      mode: this.config.mode,
    };
  }

  // ============================================================
  // 5. GET OPEN POSITIONS
  // ============================================================

  /**
   * Returns all currently open positions as ExecutionOrder objects.
   */
  getOpenPositions(): ExecutionOrder[] {
    return Array.from(this.openPositions.values()).map((p) => p.order);
  }

  // ============================================================
  // 6. GET TRADE HISTORY
  // ============================================================

  /**
   * Returns closed trade history, most recent first.
   *
   * @param limit - Maximum number of trades to return (default: 50)
   */
  getTradeHistory(limit: number = 50): ExecutionOrder[] {
    return [...this.closedOrders]
      .sort((a, b) => {
        const aTime = a.closedAt?.getTime() ?? 0;
        const bTime = b.closedAt?.getTime() ?? 0;
        return bTime - aTime;
      })
      .slice(0, limit);
  }

  // ============================================================
  // 7. GET PERFORMANCE METRICS
  // ============================================================

  /**
   * Returns aggregate performance metrics across all closed trades.
   */
  getPerformanceMetrics(): PerformanceMetrics {
    return this.calculatePerformanceMetrics();
  }

  // ============================================================
  // CONFIGURATION
  // ============================================================

  /**
   * Get the current engine configuration.
   */
  getConfig(): ExecutionEngineConfig {
    return { ...this.config };
  }

  /**
   * Update the engine configuration at runtime.
   * Changes take effect immediately.
   */
  updateConfig(updates: Partial<ExecutionEngineConfig>): void {
    const prevMode = this.config.mode;
    this.config = { ...this.config, ...updates };

    if (updates.mode && updates.mode !== prevMode) {
      console.warn(
        `[AutoExec] ⚠️ Execution mode changed: ${prevMode} → ${updates.mode}`
      );
    }

    console.info(`[AutoExec] Config updated: ${Object.keys(updates).join(', ')}`);
  }

  /**
   * Switch execution mode between PAPER and LIVE.
   */
  setMode(mode: 'PAPER' | 'LIVE'): void {
    this.updateConfig({ mode });
  }

  /**
   * Reset the engine to initial state.
   * Closes all open positions and resets capital.
   */
  async reset(): Promise<{ closedPositions: number; message: string }> {
    const openCount = this.openPositions.size;

    // Close all open positions
    const orderIds = Array.from(this.openPositions.keys());
    for (const orderId of orderIds) {
      await this.closePosition(orderId, 'ENGINE_RESET');
    }

    // Reset state
    this.closedOrders = [];
    this.currentCapital = this.config.initialCapitalUsd;
    this.peakCapital = this.config.initialCapitalUsd;
    this.totalFeesUsd = 0;

    const message = `Engine reset. Closed ${openCount} positions. Capital reset to $${this.config.initialCapitalUsd}.`;
    console.info(`[AutoExec] ${message}`);

    return { closedPositions: openCount, message };
  }

  // ============================================================
  // PRIVATE: FETCH CURRENT PRICE
  // ============================================================

  private async fetchCurrentPrice(tokenAddress: string): Promise<number> {
    try {
      const token = await db.token.findUnique({
        where: { address: tokenAddress },
        select: { priceUsd: true },
      });
      return token?.priceUsd ?? 0;
    } catch {
      return 0;
    }
  }

  // ============================================================
  // PRIVATE: CALCULATE PERFORMANCE METRICS
  // ============================================================

  private calculatePerformanceMetrics(): PerformanceMetrics {
    const trades = this.closedOrders;
    const totalTrades = trades.length;
    const winningTrades = trades.filter((t) => (t.pnlUsd ?? 0) > 0).length;
    const losingTrades = trades.filter((t) => (t.pnlUsd ?? 0) <= 0).length;
    const winRate = totalTrades > 0 ? winningTrades / totalTrades : 0;

    const wins = trades.filter((t) => (t.pnlPct ?? 0) > 0);
    const losses = trades.filter((t) => (t.pnlPct ?? 0) <= 0);

    const avgWinPct = wins.length > 0
      ? wins.reduce((s, t) => s + (t.pnlPct ?? 0), 0) / wins.length
      : 0;

    const avgLossPct = losses.length > 0
      ? losses.reduce((s, t) => s + Math.abs(t.pnlPct ?? 0), 0) / losses.length
      : 0;

    const grossProfit = wins.reduce((s, t) => s + (t.pnlUsd ?? 0), 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + (t.pnlUsd ?? 0), 0));
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

    const totalPnlUsd = trades.reduce((s, t) => s + (t.pnlUsd ?? 0), 0);
    const feeAdjustedPnlUsd = totalPnlUsd - this.totalFeesUsd;

    // Sharpe ratio from trade returns
    const sharpeRatio = this.calculateSharpeRatio();

    // Max drawdown
    const maxDrawdownPct = this.calculateMaxDrawdownPct();

    // Average hold time
    const avgHoldTimeMin = totalTrades > 0
      ? trades.reduce((s, t) => s + (t.holdTimeMin ?? 0), 0) / totalTrades
      : 0;

    // Total return
    const totalReturnPct = this.config.initialCapitalUsd > 0
      ? ((this.currentCapital - this.config.initialCapitalUsd) / this.config.initialCapitalUsd) * 100
      : 0;

    // Best / worst trade
    const tradePcts = trades.map((t) => t.pnlPct ?? 0);
    const bestTradePct = tradePcts.length > 0 ? Math.max(...tradePcts) : 0;
    const worstTradePct = tradePcts.length > 0 ? Math.min(...tradePcts) : 0;

    // Consecutive wins/losses
    const { maxConsecWins, maxConsecLosses, currentStreak } = this.calculateStreaks();

    return {
      totalTrades,
      winningTrades,
      losingTrades,
      winRate: roundTo(winRate, 4),
      avgWinPct: roundTo(avgWinPct, 2),
      avgLossPct: roundTo(avgLossPct, 2),
      profitFactor: roundTo(profitFactor, 2),
      totalPnlUsd: roundTo(totalPnlUsd, 2),
      totalFeesUsd: roundTo(this.totalFeesUsd, 2),
      feeAdjustedPnlUsd: roundTo(feeAdjustedPnlUsd, 2),
      sharpeRatio: roundTo(sharpeRatio, 2),
      maxDrawdownPct: roundTo(maxDrawdownPct, 2),
      avgHoldTimeMin: roundTo(avgHoldTimeMin, 2),
      totalReturnPct: roundTo(totalReturnPct, 2),
      bestTradePct: roundTo(bestTradePct, 2),
      worstTradePct: roundTo(worstTradePct, 2),
      consecutiveWins: maxConsecWins,
      consecutiveLosses: maxConsecLosses,
      currentStreak,
      mode: this.config.mode,
    };
  }

  // ============================================================
  // PRIVATE: SHARPE RATIO
  // ============================================================

  private calculateSharpeRatio(): number {
    const trades = this.closedOrders;
    if (trades.length < 2) return 0;

    const returns = trades.map((t) => t.pnlPct ?? 0);
    const avgReturn = returns.reduce((s, r) => s + r, 0) / returns.length;
    const variance = returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / (returns.length - 1);
    const stdDev = Math.sqrt(variance);

    if (stdDev === 0) return 0;

    // Annualized Sharpe (assuming ~5000 trades/year at 5-min cycle intervals)
    return (avgReturn / stdDev) * Math.sqrt(5000);
  }

  // ============================================================
  // PRIVATE: MAX DRAWDOWN
  // ============================================================

  private calculateMaxDrawdownPct(): number {
    const trades = this.closedOrders;
    if (trades.length === 0) return 0;

    // Calculate running capital curve
    let runningCapital = this.config.initialCapitalUsd;
    let peakCapital = runningCapital;
    let maxDrawdownPct = 0;

    for (const trade of trades) {
      runningCapital += trade.pnlUsd ?? 0;
      if (runningCapital > peakCapital) {
        peakCapital = runningCapital;
      }
      if (peakCapital > 0) {
        const drawdownPct = ((peakCapital - runningCapital) / peakCapital) * 100;
        if (drawdownPct > maxDrawdownPct) {
          maxDrawdownPct = drawdownPct;
        }
      }
    }

    return maxDrawdownPct;
  }

  // ============================================================
  // PRIVATE: STREAKS
  // ============================================================

  private calculateStreaks(): {
    maxConsecWins: number;
    maxConsecLosses: number;
    currentStreak: 'WIN' | 'LOSS' | 'NONE';
  } {
    const trades = this.closedOrders;
    if (trades.length === 0) {
      return { maxConsecWins: 0, maxConsecLosses: 0, currentStreak: 'NONE' };
    }

    let maxConsecWins = 0;
    let maxConsecLosses = 0;
    let currentWinStreak = 0;
    let currentLossStreak = 0;

    for (const trade of trades) {
      if ((trade.pnlUsd ?? 0) > 0) {
        currentWinStreak++;
        currentLossStreak = 0;
        maxConsecWins = Math.max(maxConsecWins, currentWinStreak);
      } else {
        currentLossStreak++;
        currentWinStreak = 0;
        maxConsecLosses = Math.max(maxConsecLosses, currentLossStreak);
      }
    }

    const lastTrade = trades[trades.length - 1];
    const currentStreak = (lastTrade?.pnlUsd ?? 0) > 0 ? 'WIN' : 'LOSS';

    return { maxConsecWins, maxConsecLosses, currentStreak };
  }

  // ============================================================
  // PRIVATE: PERSIST ORDER TO DATABASE
  // ============================================================

  /**
   * Persist an execution order to BacktestOperation table
   * with backtestId = "paper_trading_autonomous" for paper trades,
   * or "live_execution" for live mode.
   */
  private async persistOrderToDb(
    order: ExecutionOrder,
    pipelineResult: PipelineResult
  ): Promise<void> {
    try {
      const backtestId = order.mode === 'PAPER'
        ? PAPER_TRADING_BACKTEST_ID
        : 'live_execution';

      await db.backtestOperation.create({
        data: {
          backtestId,
          systemId: PAPER_TRADING_SYSTEM_ID,
          tokenAddress: order.tokenAddress,
          tokenSymbol: order.symbol,
          chain: order.chain,
          tokenPhase: order.tokenPhase,
          tokenAgeMinutes: 0, // Not tracked in execution
          marketConditions: JSON.stringify({
            netBehaviorFlow: order.netBehaviorFlow,
            dominantPattern: order.dominantPattern,
            dominantArchetype: order.dominantArchetype,
            pipelineStepsCompleted: pipelineResult.stepsCompleted,
            pipelineStepsFailed: pipelineResult.stepsFailed,
            dataQuality: pipelineResult.dataQuality.overallQuality,
            executionMode: order.mode,
          }),
          tokenDnaSnapshot: JSON.stringify({
            entryConfidence: order.entryConfidence,
            reasoning: order.reasoning,
          }),
          traderComposition: JSON.stringify({
            systemCategory: order.systemCategory,
            systemName: order.systemName,
            direction: order.direction,
          }),
          bigDataContext: JSON.stringify({
            correlationSamples: pipelineResult.strategy?.correlationSamples ?? 0,
            dataReliabilityLevel: pipelineResult.strategy?.dataReliabilityLevel ?? 'INSUFFICIENT',
          }),
          operationType: order.direction === 'LONG' ? 'SWING_LONG' : 'SCALP',
          timeframe: '5m',
          entryPrice: order.entryPrice,
          entryTime: order.createdAt,
          entryReason: JSON.stringify({
            conditions: pipelineResult.strategy?.entryConditions ?? [],
            confidence: order.entryConfidence,
            exitConditions: pipelineResult.strategy?.exitConditions ?? [],
            stopLossPct: order.stopLossPct,
            takeProfitPct: order.takeProfitPct,
            trailingStopPct: order.trailingStopPct,
            positionSizePctOfCapital: order.positionSizePctOfCapital,
            allocationMethod: pipelineResult.strategy?.allocationMethod ?? 'UNKNOWN',
          }),
          quantity: order.positionSizeUsd / order.entryPrice,
          positionSizeUsd: order.positionSizeUsd,
          capitalAllocPct: order.positionSizePctOfCapital,
          allocationMethodUsed: pipelineResult.strategy?.allocationMethod ?? 'KELLY_MODIFIED',
        },
      });

      // Also store the associated prediction in PredictiveSignal
      await db.predictiveSignal.create({
        data: {
          signalType: 'EXECUTION_SIGNAL',
          chain: order.chain,
          tokenAddress: order.tokenAddress,
          sector: order.systemCategory,
          prediction: JSON.stringify({
            orderId: order.id,
            direction: order.direction,
            entryPrice: order.entryPrice,
            positionSizeUsd: order.positionSizeUsd,
            stopLossPct: order.stopLossPct,
            takeProfitPct: order.takeProfitPct,
            trailingStopPct: order.trailingStopPct,
            tokenPhase: order.tokenPhase,
            dominantArchetype: order.dominantArchetype,
            netBehaviorFlow: order.netBehaviorFlow,
          }),
          confidence: order.entryConfidence,
          timeframe: '5m',
          validUntil: new Date(Date.now() + 24 * 60 * 60 * 1000), // Valid for 24h
          evidence: JSON.stringify(order.reasoning),
          historicalHitRate: 0, // Will be updated on close
          dataPointsUsed: pipelineResult.strategy?.correlationSamples ?? 0,
        },
      });
    } catch (error) {
      console.error(
        '[AutoExec] Failed to persist order to DB:',
        error instanceof Error ? error.message : String(error)
      );
    }
  }

  // ============================================================
  // PRIVATE: PERSIST CLOSE TO DATABASE
  // ============================================================

  /**
   * Update the BacktestOperation record with exit details
   * and update the associated PredictiveSignal with outcome.
   */
  private async persistCloseToDb(order: ExecutionOrder): Promise<void> {
    try {
      // Find the matching BacktestOperation by tokenAddress, entryPrice, and entryTime
      const matchingOp = await db.backtestOperation.findFirst({
        where: {
          backtestId: order.mode === 'PAPER' ? PAPER_TRADING_BACKTEST_ID : 'live_execution',
          systemId: PAPER_TRADING_SYSTEM_ID,
          tokenAddress: order.tokenAddress,
          entryPrice: order.entryPrice,
          exitPrice: null, // Not yet closed
        },
        orderBy: { createdAt: 'desc' },
      });

      if (matchingOp) {
        await db.backtestOperation.update({
          where: { id: matchingOp.id },
          data: {
            exitPrice: order.exitPrice ?? undefined,
            exitTime: order.closedAt ?? undefined,
            exitReason: order.exitReason ?? undefined,
            pnlUsd: order.pnlUsd ?? undefined,
            pnlPct: order.pnlPct ?? undefined,
            holdTimeMin: order.holdTimeMin ?? undefined,
            maxFavorableExc: order.maxFavorableExcursion ?? undefined,
            maxAdverseExc: order.maxAdverseExcursion ?? undefined,
          },
        });
      }

      // Update the associated PredictiveSignal with the outcome
      const matchingSignal = await db.predictiveSignal.findFirst({
        where: {
          signalType: 'EXECUTION_SIGNAL',
          tokenAddress: order.tokenAddress,
          chain: order.chain,
          wasCorrect: null, // Not yet validated
        },
        orderBy: { createdAt: 'desc' },
      });

      if (matchingSignal) {
        await db.predictiveSignal.update({
          where: { id: matchingSignal.id },
          data: {
            wasCorrect: (order.pnlUsd ?? 0) > 0,
            actualOutcome: JSON.stringify({
              orderId: order.id,
              exitPrice: order.exitPrice,
              exitReason: order.exitReason,
              pnlUsd: order.pnlUsd,
              pnlPct: order.pnlPct,
              holdTimeMin: order.holdTimeMin,
              maxFavorableExcursion: order.maxFavorableExcursion,
              maxAdverseExcursion: order.maxAdverseExcursion,
            }),
            updatedAt: new Date(),
          },
        });
      }

      // Also record as a UserEvent for quick querying
      await db.userEvent.create({
        data: {
          eventType: order.mode === 'PAPER' ? 'PAPER_TRADE_CLOSE' : 'LIVE_TRADE_CLOSE',
          tokenId: matchingOp?.id,
          walletAddress: order.tokenAddress,
          entryPrice: order.entryPrice,
          stopLoss: order.stopLossPct,
          takeProfit: order.takeProfitPct,
          pnl: order.pnlUsd ?? 0,
        },
      });
    } catch (error) {
      console.error(
        '[AutoExec] Failed to persist close to DB:',
        error instanceof Error ? error.message : String(error)
      );
    }
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const autonomousExecutionEngine = new AutonomousExecutionEngine();
