/**
 * Kill Switch Service - CryptoQuant Terminal
 *
 * Centralized risk enforcement service that:
 * - Monitors portfolio-level, strategy-level, and position-level risk limits
 * - Auto-triggers kill switches when thresholds are breached
 * - Provides manual pause/resume controls (global & per-strategy)
 * - Loads risk budget from DB with sensible defaults
 * - Concentration checks (token & chain) before opening positions
 */

import { db } from '@/lib/db';
import { alertEngine } from '@/lib/services/risk/alert-engine';
import { alertEscalationChain } from '@/lib/services/risk/alert-escalation-chain';
import { eventBus } from '@/lib/services/shared/event-bus';

// ============================================================
// TYPES
// ============================================================

export interface PortfolioState {
  totalCapital: number;
  totalPositionValue: number;
  totalUnrealizedPnl: number;
  currentDrawdownPct: number;
  openPositionCount: number;
  tokenConcentration: Map<string, number>;
  chainConcentration: Map<string, number>;
  sectorConcentration: Map<string, number>;
}

export interface RiskBudgetConfig {
  maxPortfolioDrawdownPct: number;
  maxStrategyDrawdownPct: number;
  maxPositionLossPct: number;
  maxConcentrationPct: number;
  maxSectorPct: number;
  maxChainPct: number;
  maxCorrelatedPct: number;
  maxDailyVaR: number;
  riskProfile: string;
}

export interface KillSwitchEvaluation {
  triggered: boolean;
  level: 'PORTFOLIO' | 'STRATEGY' | 'POSITION' | 'CONCENTRATION' | 'CHAIN_CONCENTRATION' | 'SECTOR_CONCENTRATION' | 'MANUAL';
  reason: string;
  actionRequired: 'PAUSE_ALL' | 'PAUSE_STRATEGY' | 'CLOSE_POSITION' | 'REJECT_POSITION' | 'NONE';
  details: Record<string, unknown>;
}

export interface KillSwitchState {
  globalPause: boolean;
  globalPauseReason: string | null;
  globalPauseAt: Date | null;
  strategyPauses: Map<string, { paused: boolean; reason: string; pausedAt: Date }>;
  portfolioDDTriggered: boolean;
  strategyDDTriggered: Set<string>;
  positionLossTriggered: Set<string>;
  lastEvaluatedAt: Date | null;
  lastTriggeredKillSwitches: Array<{
    level: string;
    reason: string;
    triggeredAt: Date;
  }>;
}

// ============================================================
// DEFAULT RISK BUDGET
// ============================================================

const DEFAULT_RISK_BUDGET: RiskBudgetConfig = {
  maxPortfolioDrawdownPct: 20,
  maxStrategyDrawdownPct: 30,
  maxPositionLossPct: 50,
  maxConcentrationPct: 15,
  maxSectorPct: 30,
  maxChainPct: 50,
  maxCorrelatedPct: 40,
  maxDailyVaR: 5,
  riskProfile: 'MODERATE',
};

// ============================================================
// KILL SWITCH SERVICE
// ============================================================

class KillSwitchService {
  private state: KillSwitchState = {
    globalPause: false,
    globalPauseReason: null,
    globalPauseAt: null,
    strategyPauses: new Map(),
    portfolioDDTriggered: false,
    strategyDDTriggered: new Set(),
    positionLossTriggered: new Set(),
    lastEvaluatedAt: null,
    lastTriggeredKillSwitches: [],
  };

  private cachedRiskBudget: RiskBudgetConfig | null = null;
  private riskBudgetLoadedAt: number = 0;
  private readonly RISK_BUDGET_CACHE_MS = 60_000; // 1 min cache

  // ============================================================
  // RISK BUDGET LOADING
  // ============================================================

  async loadRiskBudget(): Promise<RiskBudgetConfig> {
    // Return cached if fresh
    if (this.cachedRiskBudget && Date.now() - this.riskBudgetLoadedAt < this.RISK_BUDGET_CACHE_MS) {
      return this.cachedRiskBudget;
    }

    try {
      const row = await db.riskBudget.findFirst();
      if (row) {
        this.cachedRiskBudget = {
          maxPortfolioDrawdownPct: row.maxPortfolioDrawdownPct,
          maxStrategyDrawdownPct: row.maxStrategyDrawdownPct,
          maxPositionLossPct: row.maxPositionLossPct,
          maxConcentrationPct: row.maxConcentrationPct,
          maxSectorPct: row.maxSectorPct,
          maxChainPct: row.maxChainPct,
          maxCorrelatedPct: row.maxCorrelatedPct,
          maxDailyVaR: row.maxDailyVaR,
          riskProfile: row.riskProfile,
        };
      } else {
        this.cachedRiskBudget = { ...DEFAULT_RISK_BUDGET };
      }
      this.riskBudgetLoadedAt = Date.now();
      return this.cachedRiskBudget;
    } catch (error) {
      console.warn('[KillSwitch] Error loading risk budget from DB, using defaults:', error);
      this.cachedRiskBudget = { ...DEFAULT_RISK_BUDGET };
      this.riskBudgetLoadedAt = Date.now();
      return this.cachedRiskBudget;
    }
  }

  /** Force refresh risk budget cache (call after updating config) */
  invalidateRiskBudgetCache(): void {
    this.cachedRiskBudget = null;
    this.riskBudgetLoadedAt = 0;
  }

  // ============================================================
  // PRE-TRADE CHECKS
  // ============================================================

  /**
   * Check all risk limits before opening a new position.
   * Returns whether the position is allowed, and if not, why.
   */
  async canOpenPosition(params: {
    tokenAddress: string;
    chain: string;
    sizeUsd: number;
    strategyId?: string;
    symbol?: string;
    currentPortfolioState: PortfolioState;
  }): Promise<{ allowed: boolean; reason?: string; killSwitchTriggered?: string }> {
    const budget = await this.loadRiskBudget();

    // 1. Global manual pause
    if (this.state.globalPause) {
      return {
        allowed: false,
        reason: `Global pause active: ${this.state.globalPauseReason || 'Manual emergency pause'}`,
        killSwitchTriggered: 'GLOBAL_PAUSE',
      };
    }

    // 2. Per-strategy manual pause
    if (params.strategyId) {
      const strategyPause = this.state.strategyPauses.get(params.strategyId);
      if (strategyPause?.paused) {
        return {
          allowed: false,
          reason: `Strategy ${params.strategyId} paused: ${strategyPause.reason}`,
          killSwitchTriggered: 'STRATEGY_PAUSE',
        };
      }
    } else {
      console.warn('[KillSwitch] canOpenPosition called without strategyId — strategy-level checks skipped');
    }

    // 3. Portfolio drawdown kill switch
    if (this.state.portfolioDDTriggered) {
      return {
        allowed: false,
        reason: `Portfolio drawdown kill switch active (DD > ${budget.maxPortfolioDrawdownPct}%)`,
        killSwitchTriggered: 'PORTFOLIO_DD',
      };
    }

    // 4. Strategy drawdown kill switch
    if (params.strategyId && this.state.strategyDDTriggered.has(params.strategyId)) {
      return {
        allowed: false,
        reason: `Strategy ${params.strategyId} drawdown kill switch active (DD > ${budget.maxStrategyDrawdownPct}%)`,
        killSwitchTriggered: 'STRATEGY_DD',
      };
    }

    // 5. Token concentration check
    const currentTokenPct = params.currentPortfolioState.tokenConcentration.get(params.tokenAddress) ?? 0;
    const newTokenPct = params.currentPortfolioState.totalCapital > 0
      ? currentTokenPct + (params.sizeUsd / params.currentPortfolioState.totalCapital) * 100
      : 0;

    if (newTokenPct > budget.maxConcentrationPct) {
      return {
        allowed: false,
        reason: `Token concentration would exceed ${budget.maxConcentrationPct}% (current: ${currentTokenPct.toFixed(1)}%, +${(params.sizeUsd / params.currentPortfolioState.totalCapital * 100).toFixed(1)}%)`,
        killSwitchTriggered: 'TOKEN_CONCENTRATION',
      };
    }

    // 6. Chain concentration check
    const currentChainPct = params.currentPortfolioState.chainConcentration.get(params.chain) ?? 0;
    const newChainPct = params.currentPortfolioState.totalCapital > 0
      ? currentChainPct + (params.sizeUsd / params.currentPortfolioState.totalCapital) * 100
      : 0;

    if (newChainPct > budget.maxChainPct) {
      return {
        allowed: false,
        reason: `Chain concentration would exceed ${budget.maxChainPct}% for ${params.chain} (current: ${currentChainPct.toFixed(1)}%, +${(params.sizeUsd / params.currentPortfolioState.totalCapital * 100).toFixed(1)}%)`,
        killSwitchTriggered: 'CHAIN_CONCENTRATION',
      };
    }

    // 7. Sector concentration check — use symbol when available for accurate classification
    const sectorKey = params.symbol || params.strategyId || params.tokenAddress;
    const sector = this.inferSector(sectorKey, params.chain);
    const currentSectorPct = params.currentPortfolioState.sectorConcentration.get(sector) ?? 0;
    const newSectorPct = params.currentPortfolioState.totalCapital > 0
      ? currentSectorPct + (params.sizeUsd / params.currentPortfolioState.totalCapital) * 100
      : 0;

    if (newSectorPct > budget.maxSectorPct) {
      return {
        allowed: false,
        reason: `Sector concentration would exceed ${budget.maxSectorPct}% for ${sector} (current: ${currentSectorPct.toFixed(1)}%, +${(params.sizeUsd / params.currentPortfolioState.totalCapital * 100).toFixed(1)}%)`,
        killSwitchTriggered: 'SECTOR_CONCENTRATION',
      };
    }

    return { allowed: true };
  }

  // ============================================================
  // REAL-TIME EVALUATION (called on every price sync)
  // ============================================================

  /**
   * Evaluate portfolio-level kill switches.
   * Called on every price sync cycle.
   */
  async evaluatePortfolioKillSwitches(portfolioState: PortfolioState): Promise<KillSwitchEvaluation> {
    const budget = await this.loadRiskBudget();
    this.state.lastEvaluatedAt = new Date();

    // Check portfolio drawdown
    if (portfolioState.currentDrawdownPct > budget.maxPortfolioDrawdownPct) {
      if (!this.state.portfolioDDTriggered) {
        this.state.portfolioDDTriggered = true;
        this.addTriggeredHistory('PORTFOLIO', `Portfolio DD ${portfolioState.currentDrawdownPct.toFixed(1)}% > ${budget.maxPortfolioDrawdownPct}%`);

        // Fire alert
        try {
          await alertEngine.onKillSwitchTriggered('PORTFOLIO', {
            currentDD: portfolioState.currentDrawdownPct,
            threshold: budget.maxPortfolioDrawdownPct,
            totalCapital: portfolioState.totalCapital,
            totalUnrealizedPnl: portfolioState.totalUnrealizedPnl,
          });
        } catch (err) {
          console.warn('[KillSwitch] Alert error:', err);
        }

        // Publish KILL_SWITCH_TRIGGER event via event bus
        try {
          eventBus.publish('KILL_SWITCH_TRIGGER', {
            level: 'PORTFOLIO',
            reason: `Portfolio DD ${portfolioState.currentDrawdownPct.toFixed(1)}% exceeds threshold ${budget.maxPortfolioDrawdownPct}%`,
            action: 'PAUSE_ALL',
            timestamp: new Date(),
          }, 'kill-switch-service');
        } catch (eventErr) {
          console.warn('[KillSwitch] Event bus KILL_SWITCH_TRIGGER error:', eventErr);
        }

        // [INTEGRATION FIX] Alert escalation chain for portfolio DD kill switch
        try {
          await alertEscalationChain.killSwitchTrigger(
            'RISK',
            'Portfolio Kill Switch',
            `Portfolio DD ${(portfolioState.currentDrawdownPct).toFixed(1)}% exceeds threshold ${budget.maxPortfolioDrawdownPct}%`,
            undefined,
            { level: 'N2', action: 'PAUSE_ALL', currentDD: portfolioState.currentDrawdownPct, threshold: budget.maxPortfolioDrawdownPct },
          );
        } catch (err) {
          console.warn('[KillSwitch] Escalation chain error:', err);
        }
      }

      return {
        triggered: true,
        level: 'PORTFOLIO',
        reason: `Portfolio drawdown ${portfolioState.currentDrawdownPct.toFixed(1)}% exceeds limit ${budget.maxPortfolioDrawdownPct}%`,
        actionRequired: 'PAUSE_ALL',
        details: {
          currentDD: portfolioState.currentDrawdownPct,
          threshold: budget.maxPortfolioDrawdownPct,
          totalCapital: portfolioState.totalCapital,
        },
      };
    }

    // Portfolio DD recovered — clear flag
    if (this.state.portfolioDDTriggered && portfolioState.currentDrawdownPct <= budget.maxPortfolioDrawdownPct * 0.8) {
      this.state.portfolioDDTriggered = false;

      // Publish KILL_SWITCH_RELEASE event via event bus
      try {
        eventBus.publish('KILL_SWITCH_RELEASE', {
          level: 'PORTFOLIO',
          reason: 'Recovery hysteresis reached',
          timestamp: new Date(),
        }, 'kill-switch-service');
      } catch (eventErr) {
        console.warn('[KillSwitch] Event bus KILL_SWITCH_RELEASE error:', eventErr);
      }

      // [INTEGRATION FIX] Alert escalation chain for portfolio resume
      try {
        await alertEscalationChain.info('RISK', 'Portfolio Resumed', `Portfolio resumed from kill switch (DD recovered to ${portfolioState.currentDrawdownPct.toFixed(1)}%)`, { level: 'N2' });
      } catch (err) {
        console.warn('[KillSwitch] Escalation chain resume error:', err);
      }
    }

    return { triggered: false, level: 'PORTFOLIO', reason: '', actionRequired: 'NONE', details: {} };
  }

  /**
   * Evaluate strategy-level kill switches.
   */
  async evaluateStrategyKillSwitch(strategyId: string, strategyDD: number): Promise<KillSwitchEvaluation> {
    const budget = await this.loadRiskBudget();

    if (strategyDD > budget.maxStrategyDrawdownPct) {
      if (!this.state.strategyDDTriggered.has(strategyId)) {
        this.state.strategyDDTriggered.add(strategyId);
        this.addTriggeredHistory('STRATEGY', `Strategy ${strategyId} DD ${strategyDD.toFixed(1)}% > ${budget.maxStrategyDrawdownPct}%`);

        // Auto-pause the strategy
        this.state.strategyPauses.set(strategyId, {
          paused: true,
          reason: `Auto-paused: DD ${strategyDD.toFixed(1)}% > ${budget.maxStrategyDrawdownPct}%`,
          pausedAt: new Date(),
        });

        // Fire alert
        try {
          await alertEngine.onKillSwitchTriggered('STRATEGY', {
            strategyId,
            currentDD: strategyDD,
            threshold: budget.maxStrategyDrawdownPct,
          });
        } catch (err) {
          console.warn('[KillSwitch] Alert error:', err);
        }

        // Publish KILL_SWITCH_TRIGGER event via event bus
        try {
          eventBus.publish('KILL_SWITCH_TRIGGER', {
            level: 'STRATEGY',
            reason: `Strategy DD ${strategyDD.toFixed(1)}% exceeds threshold ${budget.maxStrategyDrawdownPct}%`,
            action: 'PAUSE_STRATEGY',
            timestamp: new Date(),
          }, 'kill-switch-service');
        } catch (eventErr) {
          console.warn('[KillSwitch] Event bus KILL_SWITCH_TRIGGER error:', eventErr);
        }

        // [INTEGRATION FIX] Alert escalation chain for strategy DD kill switch
        try {
          await alertEscalationChain.killSwitchTrigger(
            'RISK',
            'Strategy Kill Switch',
            `Strategy DD ${strategyDD.toFixed(1)}% exceeds threshold ${budget.maxStrategyDrawdownPct}%`,
            strategyId,
            { level: 'N2', action: 'PAUSE_STRATEGY', strategyId, currentDD: strategyDD, threshold: budget.maxStrategyDrawdownPct },
          );
        } catch (err) {
          console.warn('[KillSwitch] Escalation chain error:', err);
        }
      }

      return {
        triggered: true,
        level: 'STRATEGY',
        reason: `Strategy ${strategyId} drawdown ${strategyDD.toFixed(1)}% exceeds limit ${budget.maxStrategyDrawdownPct}%`,
        actionRequired: 'PAUSE_STRATEGY',
        details: { strategyId, currentDD: strategyDD, threshold: budget.maxStrategyDrawdownPct },
      };
    }

    // Strategy DD recovered
    if (this.state.strategyDDTriggered.has(strategyId) && strategyDD <= budget.maxStrategyDrawdownPct * 0.8) {
      this.state.strategyDDTriggered.delete(strategyId);
      // Also unpause if auto-paused
      const pauseInfo = this.state.strategyPauses.get(strategyId);
      if (pauseInfo?.reason.startsWith('Auto-paused:')) {
        this.state.strategyPauses.delete(strategyId);

        // Publish KILL_SWITCH_RELEASE event via event bus
        try {
          eventBus.publish('KILL_SWITCH_RELEASE', {
            level: 'STRATEGY',
            reason: 'Recovery hysteresis reached',
            timestamp: new Date(),
          }, 'kill-switch-service');
        } catch (eventErr) {
          console.warn('[KillSwitch] Event bus KILL_SWITCH_RELEASE error:', eventErr);
        }

        // [INTEGRATION FIX] Alert escalation chain for strategy resume
        try {
          await alertEscalationChain.info('RISK', 'Strategy Resumed', `Strategy ${strategyId} resumed from kill switch (DD recovered to ${strategyDD.toFixed(1)}%)`, { level: 'N2', strategyId });
        } catch (err) {
          console.warn('[KillSwitch] Escalation chain resume error:', err);
        }
      }
    }

    return { triggered: false, level: 'STRATEGY', reason: '', actionRequired: 'NONE', details: {} };
  }

  /**
   * Evaluate position-level kill switches (emergency close).
   */
  async evaluatePositionKillSwitch(positionId: string, positionLossPct: number): Promise<KillSwitchEvaluation> {
    const budget = await this.loadRiskBudget();

    if (positionLossPct <= -budget.maxPositionLossPct) {
      if (!this.state.positionLossTriggered.has(positionId)) {
        this.state.positionLossTriggered.add(positionId);
        this.addTriggeredHistory('POSITION', `Position ${positionId} loss ${positionLossPct.toFixed(1)}% > ${budget.maxPositionLossPct}%`);

        // Fire alert
        try {
          await alertEngine.onKillSwitchTriggered('POSITION', {
            positionId,
            currentLoss: positionLossPct,
            threshold: budget.maxPositionLossPct,
          });
        } catch (err) {
          console.warn('[KillSwitch] Alert error:', err);
        }

        // Publish KILL_SWITCH_TRIGGER event via event bus
        try {
          eventBus.publish('KILL_SWITCH_TRIGGER', {
            level: 'POSITION',
            reason: `Position loss ${positionLossPct.toFixed(1)}% exceeds limit -${budget.maxPositionLossPct}%`,
            action: 'CLOSE_POSITION',
            timestamp: new Date(),
          }, 'kill-switch-service');
        } catch (eventErr) {
          console.warn('[KillSwitch] Event bus KILL_SWITCH_TRIGGER error:', eventErr);
        }

        // [INTEGRATION FIX] Alert escalation chain for position loss kill switch
        try {
          await alertEscalationChain.killSwitchTrigger(
            'RISK',
            'Position Kill Switch',
            `Position loss ${positionLossPct.toFixed(1)}% exceeds limit -${budget.maxPositionLossPct}%`,
            undefined,
            { level: 'N2', action: 'CLOSE_POSITION', positionId, currentLoss: positionLossPct, threshold: budget.maxPositionLossPct },
          );
        } catch (err) {
          console.warn('[KillSwitch] Escalation chain error:', err);
        }
      }

      return {
        triggered: true,
        level: 'POSITION',
        reason: `Position loss ${positionLossPct.toFixed(1)}% exceeds limit -${budget.maxPositionLossPct}%`,
        actionRequired: 'CLOSE_POSITION',
        details: { positionId, currentLoss: positionLossPct, threshold: budget.maxPositionLossPct },
      };
    }

    return { triggered: false, level: 'POSITION', reason: '', actionRequired: 'NONE', details: {} };
  }

  /**
   * Evaluate daily VaR kill switch.
   * Triggers global pause when current daily loss percentage exceeds maxDailyVaR.
   */
  async evaluateDailyVaRKillSwitch(currentDailyLossPct: number): Promise<KillSwitchEvaluation> {
    const budget = await this.loadRiskBudget();
    this.state.lastEvaluatedAt = new Date();

    if (currentDailyLossPct > budget.maxDailyVaR) {
      if (!this.state.globalPause) {
        this.state.globalPause = true;
        this.state.globalPauseReason = `Auto-paused: Daily VaR breach ${currentDailyLossPct.toFixed(1)}% > ${budget.maxDailyVaR}%`;
        this.state.globalPauseAt = new Date();
        this.addTriggeredHistory('PORTFOLIO', `Daily VaR breach: ${currentDailyLossPct.toFixed(1)}% > ${budget.maxDailyVaR}%`);

        // Fire alert
        try {
          await alertEngine.onKillSwitchTriggered('PORTFOLIO', {
            currentDailyLossPct,
            threshold: budget.maxDailyVaR,
          });
        } catch (err) {
          console.warn('[KillSwitch] Alert error:', err);
        }

        // Publish KILL_SWITCH_TRIGGER event via event bus
        try {
          eventBus.publish('KILL_SWITCH_TRIGGER', {
            level: 'PORTFOLIO',
            reason: `Daily VaR breach: ${currentDailyLossPct.toFixed(1)}% > ${budget.maxDailyVaR}%`,
            action: 'PAUSE_ALL',
            timestamp: new Date(),
          }, 'kill-switch-service');
        } catch (eventErr) {
          console.warn('[KillSwitch] Event bus KILL_SWITCH_TRIGGER error:', eventErr);
        }

        // [INTEGRATION FIX] Alert escalation chain for daily VaR kill switch
        try {
          await alertEscalationChain.killSwitchTrigger(
            'RISK',
            'Daily VaR Kill Switch',
            `Daily loss ${currentDailyLossPct.toFixed(1)}% exceeds VaR limit ${budget.maxDailyVaR}%`,
            undefined,
            { level: 'N2', action: 'PAUSE_ALL', currentDailyLossPct, threshold: budget.maxDailyVaR },
          );
        } catch (err) {
          console.warn('[KillSwitch] Escalation chain error:', err);
        }
      }

      return {
        triggered: true,
        level: 'PORTFOLIO',
        reason: `Daily loss ${currentDailyLossPct.toFixed(1)}% exceeds VaR limit ${budget.maxDailyVaR}%`,
        actionRequired: 'PAUSE_ALL',
        details: { currentDailyLossPct, threshold: budget.maxDailyVaR },
      };
    }

    return { triggered: false, level: 'PORTFOLIO', reason: '', actionRequired: 'NONE', details: {} };
  }

  // ============================================================
  // MANUAL CONTROLS
  // ============================================================

  setGlobalPause(pause: boolean, reason?: string): void {
    this.state.globalPause = pause;
    if (pause) {
      this.state.globalPauseReason = reason || 'Manual emergency pause';
      this.state.globalPauseAt = new Date();
      this.addTriggeredHistory('MANUAL', `Global pause: ${this.state.globalPauseReason}`);
      console.warn(`[KillSwitch] GLOBAL PAUSE activated: ${this.state.globalPauseReason}`);

      // Publish KILL_SWITCH_TRIGGER event via event bus
      try {
        eventBus.publish('KILL_SWITCH_TRIGGER', {
          level: 'MANUAL',
          reason: this.state.globalPauseReason,
          action: 'PAUSE_ALL',
          timestamp: new Date(),
        }, 'kill-switch-service');
      } catch (eventErr) {
        console.warn('[KillSwitch] Event bus KILL_SWITCH_TRIGGER error:', eventErr);
      }
    } else {
      this.state.globalPauseReason = null;
      this.state.globalPauseAt = null;
      // DO NOT clear portfolioDDTriggered on resume — let evaluatePortfolioKillSwitches
      // handle recovery via the 80% hysteresis threshold
      console.log('[KillSwitch] Global pause deactivated');

      // Publish KILL_SWITCH_RELEASE event via event bus
      try {
        eventBus.publish('KILL_SWITCH_RELEASE', {
          level: 'MANUAL',
          reason: reason || 'Manual resume',
          timestamp: new Date(),
        }, 'kill-switch-service');
      } catch (eventErr) {
        console.warn('[KillSwitch] Event bus KILL_SWITCH_RELEASE error:', eventErr);
      }
    }
  }

  setStrategyPause(strategyId: string, pause: boolean, reason?: string): void {
    if (pause) {
      this.state.strategyPauses.set(strategyId, {
        paused: true,
        reason: reason || 'Manual pause',
        pausedAt: new Date(),
      });
      this.addTriggeredHistory('MANUAL', `Strategy ${strategyId} paused: ${reason || 'Manual pause'}`);

      // Publish KILL_SWITCH_TRIGGER event via event bus
      try {
        eventBus.publish('KILL_SWITCH_TRIGGER', {
          level: 'STRATEGY',
          reason: reason || 'Manual strategy pause',
          action: 'PAUSE_STRATEGY',
          timestamp: new Date(),
        }, 'kill-switch-service');
      } catch (eventErr) {
        console.warn('[KillSwitch] Event bus KILL_SWITCH_TRIGGER error:', eventErr);
      }
    } else {
      this.state.strategyPauses.delete(strategyId);
      // DO NOT clear strategyDDTriggered — let evaluateStrategyKillSwitch
      // handle recovery via the 80% hysteresis threshold (same pattern as setGlobalPause)

      // Publish KILL_SWITCH_RELEASE event via event bus
      try {
        eventBus.publish('KILL_SWITCH_RELEASE', {
          level: 'STRATEGY',
          reason: reason || 'Manual strategy resume',
          timestamp: new Date(),
        }, 'kill-switch-service');
      } catch (eventErr) {
        console.warn('[KillSwitch] Event bus KILL_SWITCH_RELEASE error:', eventErr);
      }
    }
  }

  // ============================================================
  // STATE ACCESS
  // ============================================================

  getState(): KillSwitchState {
    return this.state;
  }

  /** Clear position kill switch flag when position is closed */
  clearPositionKillSwitch(positionId: string): void {
    this.state.positionLossTriggered.delete(positionId);
  }

  /** Get a serializable version of state for API responses */
  getStateSerializable(): Record<string, unknown> {
    return {
      globalPause: this.state.globalPause,
      globalPauseReason: this.state.globalPauseReason,
      globalPauseAt: this.state.globalPauseAt?.toISOString() ?? null,
      strategyPauses: Object.fromEntries(
        Array.from(this.state.strategyPauses.entries()).map(([k, v]) => [
          k,
          { ...v, pausedAt: v.pausedAt.toISOString() },
        ])
      ),
      portfolioDDTriggered: this.state.portfolioDDTriggered,
      strategyDDTriggered: Array.from(this.state.strategyDDTriggered),
      positionLossTriggered: Array.from(this.state.positionLossTriggered),
      lastEvaluatedAt: this.state.lastEvaluatedAt?.toISOString() ?? null,
      lastTriggeredKillSwitches: this.state.lastTriggeredKillSwitches.map(e => ({
        ...e,
        triggeredAt: e.triggeredAt.toISOString(),
      })),
    };
  }

  // ============================================================
  // INTERNAL HELPERS
  // ============================================================

  /**
   * Infer sector from token address/chain.
   * Simple heuristic: use chain as proxy for sector classification.
   * In production, this should use a proper token-to-sector mapping from an external data source.
   */
  /** Public: infer sector from token symbol/chain — used by PTE and pipeline */
  inferSector(tokenAddress: string, chain: string): string {
    // Map well-known tokens to sectors
    const TOKEN_SECTOR_MAP: Record<string, string> = {
      // Stablecoins
      'USDT': 'STABLECOIN', 'USDC': 'STABLECOIN', 'DAI': 'STABLECOIN', 'BUSD': 'STABLECOIN',
      'TUSD': 'STABLECOIN', 'FRAX': 'STABLECOIN', 'LUSD': 'STABLECOIN', 'PYUSD': 'STABLECOIN',
      // L1s
      'ETH': 'L1', 'SOL': 'L1', 'BNB': 'L1', 'MATIC': 'L1', 'AVAX': 'L1', 'FTM': 'L1',
      'ADA': 'L1', 'DOT': 'L1', 'NEAR': 'L1', 'ATOM': 'L1',
      // DeFi
      'UNI': 'DEFI', 'AAVE': 'DEFI', 'MKR': 'DEFI', 'COMP': 'DEFI', 'CRV': 'DEFI',
      'SNX': 'DEFI', 'DYDX': 'DEFI', 'GMX': 'DEFI', 'JUP': 'DEFI', 'RAY': 'DEFI',
      // L2s
      'ARB': 'L2', 'OP': 'L2', 'STRK': 'L2', 'IMX': 'L2',
      // Meme
      'DOGE': 'MEME', 'SHIB': 'MEME', 'PEPE': 'MEME', 'FLOKI': 'MEME', 'BONK': 'MEME',
      'WIF': 'MEME',
    };
    // Try exact symbol match first (check symbol field in tokenAddress)
    // tokenAddress may be a contract address or a symbol like "SOL"
    const tokenSymbol = tokenAddress.split('/').pop()?.toUpperCase() || '';
    for (const [symbol, sector] of Object.entries(TOKEN_SECTOR_MAP)) {
      // Exact match only — substring matching causes false positives
      if (tokenSymbol === symbol || tokenAddress.toUpperCase() === symbol) return sector;
    }
    // Default: classify by chain as a rough proxy
    const CHAIN_SECTOR_DEFAULT: Record<string, string> = {
      'ETH': 'L1', 'SOL': 'L1', 'BNB': 'L1', 'MATIC': 'L1', 'AVAX': 'L1',
      'ARB': 'L2', 'OP': 'L2', 'BASE': 'L2',
    };
    return CHAIN_SECTOR_DEFAULT[chain.toUpperCase()] || 'UNKNOWN';
  }

  private addTriggeredHistory(level: string, reason: string): void {
    this.state.lastTriggeredKillSwitches.unshift({
      level,
      reason,
      triggeredAt: new Date(),
    });
    // Keep only last 20
    if (this.state.lastTriggeredKillSwitches.length > 20) {
      this.state.lastTriggeredKillSwitches = this.state.lastTriggeredKillSwitches.slice(0, 20);
    }
  }
}

// Singleton instance
export const killSwitchService = new KillSwitchService();
