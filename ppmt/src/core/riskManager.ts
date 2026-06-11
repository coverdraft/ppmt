/**
 * PPMT - Risk Manager
 *
 * The ONLY external component needed with Block Lifecycle Metadata.
 * Controls position sizing and portfolio risk, not trading decisions.
 * All directional intelligence comes from the PPMT engine.
 *
 * Functions:
 *   - Position sizing (Kelly Criterion adjusted)
 *   - Max risk per trade (e.g. 2% of capital)
 *   - Max daily drawdown (e.g. 5%)
 *   - Correlation limit (no 5 meme longs simultaneously)
 *   - Daily loss circuit breaker
 */

import { RiskConfig, PositionSizing, Direction, BlockMetadata } from './types';

const DEFAULT_RISK_CONFIG: RiskConfig = {
  maxRiskPerTrade: 0.02,    // 2% risk per trade
  maxDailyDrawdown: 0.05,   // 5% max daily drawdown
  maxCorrelatedPositions: 3,
  capital: 10000,
};

export interface PositionRecord {
  asset: string;
  direction: Direction;
  entryPrice: number;
  size: number;
  stopLoss: number;
  takeProfit: number;
  assetClass: string;
  openTime: number;
}

export class RiskManager {
  private config: RiskConfig;
  private openPositions: PositionRecord[] = [];
  private dailyPnl: number = 0;
  private dailyStartTime: number;

  constructor(config?: Partial<RiskConfig>) {
    this.config = { ...DEFAULT_RISK_CONFIG, ...config };
    this.dailyStartTime = this.getTodayStart();
  }

  /**
   * Evaluate whether a trade is allowed and calculate position size.
   *
   * Uses Kelly Criterion adjusted by the PPMT's win rate:
   *   kelly_f = (p * b - q) / b
   *   where p = win_rate, q = 1 - p, b = reward/risk ratio
   *
   * Then applies fractional Kelly (25%) for safety.
   *
   * Returns null if the trade is rejected.
   */
  evaluate(
    asset: string,
    direction: Direction,
    currentPrice: number,
    metadata: BlockMetadata | undefined,
    assetClass: string
  ): PositionSizing | null {
    // Reset daily PnL if new day
    this.checkDayReset();

    // Circuit breaker: stop if daily drawdown exceeded
    if (this.dailyPnl <= -(this.config.capital * this.config.maxDailyDrawdown)) {
      return null; // DAILY LIMIT REACHED
    }

    // Check correlation limit
    const sameClassPositions = this.openPositions.filter(
      p => p.assetClass === assetClass && p.direction === direction
    ).length;
    if (sameClassPositions >= this.config.maxCorrelatedPositions) {
      return null; // TOO MANY CORRELATED POSITIONS
    }

    // Check if already in position for this asset
    const existingPosition = this.openPositions.find(p => p.asset === asset);
    if (existingPosition) {
      return null; // ALREADY IN POSITION
    }

    // Calculate stop loss and take profit from metadata
    let stopLoss: number;
    let takeProfit: number;

    if (metadata) {
      // Use Block Lifecycle Metadata (natural SL/TP)
      const slDist = metadata.stopLossDistance / 100;
      const tpDist = metadata.takeProfitDistance / 100;

      if (direction === Direction.LONG) {
        stopLoss = currentPrice * (1 - slDist);
        takeProfit = currentPrice * (1 + tpDist);
      } else {
        stopLoss = currentPrice * (1 + slDist);
        takeProfit = currentPrice * (1 - tpDist);
      }
    } else {
      // Fallback: use default 1.5% SL / 3.75% TP
      if (direction === Direction.LONG) {
        stopLoss = currentPrice * 0.985;
        takeProfit = currentPrice * 1.0375;
      } else {
        stopLoss = currentPrice * 1.015;
        takeProfit = currentPrice * 0.9625;
      }
    }

    // Position sizing using fractional Kelly
    const riskPerUnit = Math.abs(currentPrice - stopLoss);
    const rewardPerUnit = Math.abs(takeProfit - currentPrice);
    const rewardRiskRatio = riskPerUnit > 0 ? rewardPerUnit / riskPerUnit : 1;

    // Kelly Criterion
    const winRate = metadata?.winRateFromHere ?? 0.55;
    const kellyF = this.kellyFraction(winRate, rewardRiskRatio);
    const fractionalKelly = kellyF * 0.25; // 25% of Kelly for safety

    // Position size in risk terms
    const riskAmount = this.config.capital * this.config.maxRiskPerTrade * Math.min(fractionalKelly, 1);
    const size = riskPerUnit > 0 ? riskAmount / riskPerUnit : 0;

    // Cap position size to not exceed 10% of capital
    const maxSize = (this.config.capital * 0.10) / currentPrice;
    const finalSize = Math.min(size, maxSize);

    if (finalSize <= 0) {
      return null;
    }

    return {
      size: finalSize,
      riskPercent: (riskAmount / this.config.capital) * 100,
      stopLoss,
      takeProfit,
      entryPrice: currentPrice,
    };
  }

  /**
   * Register an opened position.
   */
  openPosition(position: PositionRecord): void {
    this.openPositions.push(position);
  }

  /**
   * Close a position and update daily PnL.
   */
  closePosition(asset: string, closePrice: number): number {
    const idx = this.openPositions.findIndex(p => p.asset === asset);
    if (idx === -1) return 0;

    const pos = this.openPositions[idx];
    let pnl: number;

    if (pos.direction === Direction.LONG) {
      pnl = (closePrice - pos.entryPrice) * pos.size;
    } else {
      pnl = (pos.entryPrice - closePrice) * pos.size;
    }

    this.dailyPnl += pnl;
    this.openPositions.splice(idx, 1);

    return pnl;
  }

  /**
   * Get current daily PnL.
   */
  getDailyPnl(): number {
    return this.dailyPnl;
  }

  /**
   * Get number of open positions.
   */
  getOpenPositionCount(): number {
    return this.openPositions.length;
  }

  /**
   * Check if daily limit has been reached.
   */
  isDailyLimitReached(): boolean {
    this.checkDayReset();
    return this.dailyPnl <= -(this.config.capital * this.config.maxDailyDrawdown);
  }

  // ─── Private Methods ───

  /**
   * Kelly Criterion: f = (p*b - q) / b
   * p = win rate, q = 1-p, b = reward/risk ratio
   */
  private kellyFraction(winRate: number, rewardRiskRatio: number): number {
    const p = winRate;
    const q = 1 - p;
    const b = rewardRiskRatio;

    const f = (p * b - q) / b;
    return Math.max(0, f); // Never negative
  }

  private getTodayStart(): number {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  }

  private checkDayReset(): void {
    const todayStart = this.getTodayStart();
    if (todayStart > this.dailyStartTime) {
      this.dailyPnl = 0;
      this.dailyStartTime = todayStart;
    }
  }
}
