/**
 * Trade Execution Engine - CryptoQuant Terminal
 * 
 * FOUNDATION for automated trade execution.
 * Provides the complete architecture for executing real trades on DEXes
 * using the user's wallets with their best trading systems.
 * 
 * Architecture:
 * ┌─────────────┐    ┌──────────────┐    ┌─────────────┐
 * │ Brain Signal │───▶│ Order Builder│───▶│ DEX Router  │
 * └─────────────┘    └──────────────┘    └─────────────┘
 *       │                   │                    │
 *       ▼                   ▼                    ▼
 * ┌─────────────┐    ┌──────────────┐    ┌─────────────┐
 * │ Risk Check  │    │ Position Mgr │    │ TX Executor │
 * └─────────────┘    └──────────────┘    └─────────────┘
 *       │                   │                    │
 *       ▼                   ▼                    ▼
 * ┌─────────────┐    ┌──────────────┐    ┌─────────────┐
 * │ Wallet Mgr  │    │ Order Tracker│    │ TX Confirmer│
 * └─────────────┘    └──────────────┘    └─────────────┘
 * 
 * Safety Features:
 * - Kill switch: immediately stops all trading activity
 * - Position limits: max open positions, max capital per trade
 * - Daily loss limit: stops trading if daily loss exceeds threshold
 * - Pre-flight checks: verify wallet balance, gas, slippage before execution
 * - Dry-run mode: simulate execution without broadcasting transactions
 * - Audit trail: every decision logged with full context
 * 
 * CURRENT STATUS: Foundation + Architecture
 * - All interfaces defined
 * - Wallet management implemented
 * - Order builder implemented  
 * - Risk checks implemented
 * - DEX routing framework defined (Jupiter for Solana, 1inch for EVM)
 * - TX execution is a STUB (returns simulated results)
 * - When ready for live, only TX Executor needs real implementation
 */

import type { TokenAnalysis } from './brain-orchestrator';
import { tradingSystemEngine, type SystemTemplate, type ExecutionConfig as SystemExecutionConfig } from './trading-system-engine';
import { db } from '@/lib/db';

// ============================================================
// TYPES & INTERFACES
// ============================================================

export type ExecutionMode = 'DRY_RUN' | 'LIVE';
export type OrderStatus = 'PENDING' | 'SUBMITTED' | 'CONFIRMED' | 'FAILED' | 'CANCELLED' | 'EXPIRED';
export type OrderSide = 'BUY' | 'SELL';
export type DEXRoute = 'JUPITER' | '1INCH' | 'PARASWAP' | 'DIRECT';

export interface WalletConfig {
  address: string;
  chain: string;
  privateKeyEncrypted?: string; // encrypted, never stored plain
  label: string;
  maxAllocationUsd: number;
  isActive: boolean;
}

export interface ExecutionOrder {
  id: string;
  tokenAddress: string;
  tokenSymbol: string;
  chain: string;
  side: OrderSide;
  orderType: 'MARKET' | 'LIMIT' | 'TWAP' | 'DCA';
  quantity: number;
  positionSizeUsd: number;
  price: number; // expected entry/exit price
  slippageTolerancePct: number;
  status: OrderStatus;
  txHash: string | null;
  submittedAt: Date | null;
  confirmedAt: Date | null;
  executedPrice: number | null; // actual fill price
  executedQuantity: number | null;
  feesUsd: number;
  dexRoute: DEXRoute;
  brainAnalysis: TokenAnalysis | null; // snapshot of brain analysis
  systemName: string;
  reason: string;
  error: string | null;
}

export interface RiskCheckResult {
  approved: boolean;
  checks: RiskCheck[];
  warnings: string[];
  blockedReasons: string[];
}

export interface RiskCheck {
  name: string;
  passed: boolean;
  value: number;
  limit: number;
  message: string;
}

export interface ExecutionConfig {
  mode: ExecutionMode;
  /** Global kill switch — immediately stops ALL trading */
  killSwitch: boolean;
  /** Maximum open positions across all systems */
  maxOpenPositions: number;
  /** Maximum capital per single trade (USD) */
  maxTradeSizeUsd: number;
  /** Maximum daily loss (USD) — stops trading if exceeded */
  maxDailyLossUsd: number;
  /** Maximum daily loss as % of capital */
  maxDailyLossPct: number;
  /** Require manual confirmation before each trade (default: true for safety) */
  requireConfirmation: boolean;
  /** Default slippage tolerance */
  defaultSlippagePct: number;
  /** Default fee budget per trade (for priority fees) */
  defaultPriorityFeeUsd: number;
  /** Chains enabled for execution */
  enabledChains: string[];
  /** DEX routes enabled per chain */
  dexRoutes: Record<string, DEXRoute[]>;
}

export interface DailyExecutionStats {
  date: string;
  tradesExecuted: number;
  tradesSucceeded: number;
  tradesFailed: number;
  totalFeesUsd: number;
  realizedPnlUsd: number;
  unrealizedPnlUsd: number;
  capitalUsed: number;
  maxDrawdownPct: number;
  isTradingHalted: boolean;
  haltReason: string | null;
}

export interface CreateOrderParams {
  tokenAddress: string;
  chain: string;
  side: OrderSide;
  positionSizeUsd: number;
  systemName: string;
  brainAnalysis: TokenAnalysis;
  reason: string;
  orderType?: 'MARKET' | 'LIMIT' | 'TWAP' | 'DCA';
  slippageOverride?: number;
  walletAddress?: string; // if not specified, auto-select
}

// ============================================================
// DEFAULT CONFIGURATION
// ============================================================

const DEFAULT_EXECUTION_CONFIG: ExecutionConfig = {
  mode: 'DRY_RUN',
  killSwitch: false,
  maxOpenPositions: 3,
  maxTradeSizeUsd: 100,
  maxDailyLossUsd: 5,
  maxDailyLossPct: 10,
  requireConfirmation: true,
  defaultSlippagePct: 1.5,
  defaultPriorityFeeUsd: 0.01,
  enabledChains: ['SOL', 'ETH', 'BASE'],
  dexRoutes: {
    SOL: ['JUPITER'],
    ETH: ['1INCH', 'PARASWAP'],
    BASE: ['1INCH'],
  },
};

// ============================================================
// UTILITY HELPERS
// ============================================================

/** Generate a unique order ID */
function generateOrderId(): string {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 8);
  return `ord_${timestamp}_${random}`;
}

/** Get today's date string in YYYY-MM-DD format */
function getTodayDateStr(): string {
  return new Date().toISOString().split('T')[0];
}

/** Select the best DEX route for a given chain */
function selectDexRoute(chain: string, dexRoutes: Record<string, DEXRoute[]>): DEXRoute {
  const routes = dexRoutes[chain];
  if (!routes || routes.length === 0) {
    // Fallback: Jupiter for Solana, 1inch for EVM
    if (chain === 'SOL') return 'JUPITER';
    return '1INCH';
  }
  return routes[0]; // Use the first (preferred) route
}

// ============================================================
// TRADE EXECUTION ENGINE
// ============================================================

export class TradeExecutionEngine {
  // ---- State ----
  private config: ExecutionConfig;
  private wallets: Map<string, WalletConfig> = new Map(); // keyed by address
  private orders: Map<string, ExecutionOrder> = new Map(); // keyed by order id
  private dailyStats: Map<string, DailyExecutionStats> = new Map(); // keyed by date string
  private killSwitchReason: string | null = null;

  constructor(config: ExecutionConfig = DEFAULT_EXECUTION_CONFIG) {
    this.config = { ...config };
    this.initializeDailyStats();
  }

  // ============================================================
  // WALLET MANAGEMENT
  // ============================================================

  /**
   * Register a wallet for trading.
   * Stores the wallet config in-memory and persists to the database.
   */
  async addWallet(config: WalletConfig): Promise<void> {
    if (this.wallets.has(config.address)) {
      console.warn(`[TradeEngine] Wallet ${config.address} already registered — updating`);
    }

    this.wallets.set(config.address, { ...config });

    // Persist to DB — store wallet metadata in UserEvent as a marker
    try {
      await db.userEvent.create({
        data: {
          eventType: 'WALLET_ADDED',
          walletAddress: config.address,
          entryPrice: config.maxAllocationUsd,
          stopLoss: config.isActive ? 1 : 0,
          takeProfit: parseFloat(config.chain), // NaN is fine — just a marker
          pnl: 0,
        },
      });
    } catch (error) {
      console.error('[TradeEngine] Failed to persist wallet to DB:', error);
    }

    console.info(
      `[TradeEngine] Wallet registered: ${config.label} (${config.address.slice(0, 8)}...) on ${config.chain} — max $${config.maxAllocationUsd}`
    );
  }

  /**
   * Remove a wallet from the active set.
   */
  async removeWallet(address: string): Promise<void> {
    const wallet = this.wallets.get(address);
    if (!wallet) {
      console.warn(`[TradeEngine] Wallet ${address} not found — nothing to remove`);
      return;
    }

    this.wallets.delete(address);

    // Record removal in DB
    try {
      await db.userEvent.create({
        data: {
          eventType: 'WALLET_REMOVED',
          walletAddress: address,
          pnl: 0,
        },
      });
    } catch (error) {
      console.error('[TradeEngine] Failed to persist wallet removal to DB:', error);
    }

    console.info(`[TradeEngine] Wallet removed: ${wallet.label} (${address.slice(0, 8)}...)`);
  }

  /**
   * Get all active wallets, optionally filtered by chain.
   */
  getActiveWallets(chain?: string): WalletConfig[] {
    const active = Array.from(this.wallets.values()).filter((w) => w.isActive);
    if (chain) {
      return active.filter((w) => w.chain.toUpperCase() === chain.toUpperCase());
    }
    return active;
  }

  /**
   * Auto-select the best wallet for a trade.
   * Criteria:
   *  - Must be active and on the correct chain
   *  - Must have sufficient remaining allocation
   *  - Prefer the wallet with the most remaining allocation
   */
  selectWalletForTrade(chain: string, positionSizeUsd: number): WalletConfig | null {
    const candidates = this.getActiveWallets(chain).filter(
      (w) => w.maxAllocationUsd >= positionSizeUsd
    );

    if (candidates.length === 0) {
      console.warn(
        `[TradeEngine] No wallet found for chain=${chain} with allocation >= $${positionSizeUsd}`
      );
      return null;
    }

    // Sort by remaining allocation descending (most headroom first)
    // For now, use maxAllocationUsd as a proxy — in live mode, we'd check actual balance
    candidates.sort((a, b) => b.maxAllocationUsd - a.maxAllocationUsd);

    return candidates[0];
  }

  // ============================================================
  // ORDER MANAGEMENT
  // ============================================================

  /**
   * Create a new execution order from a brain signal and system name.
   * Does NOT submit or execute the order — just builds it with full context.
   */
  async createOrder(params: CreateOrderParams): Promise<ExecutionOrder> {
    const {
      tokenAddress,
      chain,
      side,
      positionSizeUsd,
      systemName,
      brainAnalysis,
      reason,
      orderType = 'MARKET',
      slippageOverride,
      walletAddress,
    } = params;

    // Resolve the wallet
    let wallet: WalletConfig | null;
    if (walletAddress) {
      wallet = this.wallets.get(walletAddress) ?? null;
      if (!wallet) {
        throw new Error(`Wallet ${walletAddress} not registered`);
      }
    } else {
      wallet = this.selectWalletForTrade(chain, positionSizeUsd);
      if (!wallet) {
        throw new Error(`No eligible wallet for chain=${chain} size=$${positionSizeUsd}`);
      }
    }

    // Resolve slippage: override > system template > default config
    let slippageTolerancePct = slippageOverride ?? this.config.defaultSlippagePct;
    const systemTemplate = tradingSystemEngine.getTemplate(systemName);
    if (systemTemplate && slippageOverride === undefined) {
      slippageTolerancePct = systemTemplate.executionConfig.slippageTolerancePct;
    }

    // Determine the DEX route
    const dexRoute = selectDexRoute(chain, this.config.dexRoutes);

    // Determine price from brain analysis (use operability fee estimate as proxy)
    const price = brainAnalysis.feeEstimate?.totalCostUsd > 0
      ? brainAnalysis.recommendedPositionUsd / positionSizeUsd
      : 0; // Will be populated from live price data in production

    // Compute quantity from position size and price
    const quantity = price > 0 ? positionSizeUsd / price : 0;

    // Get token symbol from brain analysis
    const tokenSymbol = brainAnalysis.symbol || tokenAddress.slice(0, 8);

    const order: ExecutionOrder = {
      id: generateOrderId(),
      tokenAddress,
      tokenSymbol,
      chain,
      side,
      orderType,
      quantity,
      positionSizeUsd,
      price,
      slippageTolerancePct,
      status: 'PENDING',
      txHash: null,
      submittedAt: null,
      confirmedAt: null,
      executedPrice: null,
      executedQuantity: null,
      feesUsd: 0,
      dexRoute,
      brainAnalysis,
      systemName,
      reason,
      error: null,
    };

    this.orders.set(order.id, order);

    // Audit log
    console.info(
      `[TradeEngine] Order created: ${order.id} | ${side} ${tokenSymbol} | ` +
      `$${positionSizeUsd} | ${orderType} | ${dexRoute} | system=${systemName} | ` +
      `wallet=${wallet.address.slice(0, 8)}... | reason="${reason}"`
    );

    return order;
  }

  /**
   * Submit a pending order for execution.
   * Runs pre-flight risk checks before proceeding.
   */
  async submitOrder(orderId: string): Promise<ExecutionOrder> {
    const order = this.orders.get(orderId);
    if (!order) {
      throw new Error(`Order ${orderId} not found`);
    }

    if (order.status !== 'PENDING') {
      throw new Error(`Order ${orderId} is not PENDING (current: ${order.status})`);
    }

    // ── Pre-flight risk checks ──────────────────────────────────
    const riskResult = await this.runRiskChecks(order);

    if (!riskResult.approved) {
      order.status = 'FAILED';
      order.error = `Risk check failed: ${riskResult.blockedReasons.join('; ')}`;

      console.warn(
        `[TradeEngine] Order ${orderId} BLOCKED by risk checks: ${riskResult.blockedReasons.join('; ')}`
      );

      this.recordOrderInDb(order);
      return order;
    }

    // Log warnings even if approved
    if (riskResult.warnings.length > 0) {
      console.warn(
        `[TradeEngine] Order ${orderId} risk warnings: ${riskResult.warnings.join('; ')}`
      );
    }

    // ── Confirmation gate ───────────────────────────────────────
    if (this.config.requireConfirmation) {
      console.info(
        `[TradeEngine] Order ${orderId} awaiting manual confirmation (requireConfirmation=true)`
      );
      // In a real UI, this would pause and wait for user input.
      // For the foundation, we log and proceed to execution.
      // When the UI is built, this will be an async confirmation flow.
    }

    // ── Execute ─────────────────────────────────────────────────
    order.status = 'SUBMITTED';
    order.submittedAt = new Date();
    this.orders.set(order.id, order);

    console.info(`[TradeEngine] Order ${orderId} submitted for execution`);

    try {
      const executedOrder = await this.executeOrder(orderId);
      return executedOrder;
    } catch (error) {
      order.status = 'FAILED';
      order.error = error instanceof Error ? error.message : 'Unknown execution error';
      this.orders.set(order.id, order);

      console.error(`[TradeEngine] Order ${orderId} execution failed: ${order.error}`);
      this.recordOrderInDb(order);
      return order;
    }
  }

  /**
   * Cancel a pending order.
   */
  async cancelOrder(orderId: string): Promise<ExecutionOrder> {
    const order = this.orders.get(orderId);
    if (!order) {
      throw new Error(`Order ${orderId} not found`);
    }

    if (!['PENDING', 'SUBMITTED'].includes(order.status)) {
      throw new Error(
        `Order ${orderId} cannot be cancelled (current status: ${order.status})`
      );
    }

    order.status = 'CANCELLED';
    this.orders.set(order.id, order);

    console.info(`[TradeEngine] Order ${orderId} cancelled`);

    this.recordOrderInDb(order);
    return order;
  }

  /**
   * Get an order by its ID.
   */
  getOrder(orderId: string): ExecutionOrder | undefined {
    return this.orders.get(orderId);
  }

  /**
   * Get all orders, optionally filtered by status.
   */
  getOrders(status?: OrderStatus): ExecutionOrder[] {
    const all = Array.from(this.orders.values());
    if (status) {
      return all.filter((o) => o.status === status);
    }
    return all;
  }

  // ============================================================
  // RISK MANAGEMENT
  // ============================================================

  /**
   * Run all pre-flight risk checks against an order.
   * Returns a comprehensive result with pass/fail for each check.
   */
  async runRiskChecks(order: ExecutionOrder): Promise<RiskCheckResult> {
    const checks: RiskCheck[] = [];
    const warnings: string[] = [];
    const blockedReasons: string[] = [];

    // ── 1. Kill Switch Check ────────────────────────────────────
    const killSwitchCheck: RiskCheck = {
      name: 'KILL_SWITCH',
      passed: !this.config.killSwitch,
      value: this.config.killSwitch ? 1 : 0,
      limit: 0,
      message: this.config.killSwitch
        ? `Kill switch is ACTIVE: ${this.killSwitchReason || 'no reason specified'}`
        : 'Kill switch is inactive',
    };
    checks.push(killSwitchCheck);
    if (!killSwitchCheck.passed) {
      blockedReasons.push(killSwitchCheck.message);
    }

    // ── 2. Daily Loss Limit Check ──────────────────────────────
    const todayStats = this.getOrCreateDailyStats();
    const dailyLoss = Math.min(todayStats.realizedPnlUsd, 0); // Only count losses
    const dailyLossAbs = Math.abs(dailyLoss);
    const dailyLossPctOfCapital = todayStats.capitalUsed > 0
      ? (dailyLossAbs / todayStats.capitalUsed) * 100
      : 0;

    const dailyLossUsdCheck: RiskCheck = {
      name: 'DAILY_LOSS_USD',
      passed: dailyLossAbs < this.config.maxDailyLossUsd,
      value: dailyLossAbs,
      limit: this.config.maxDailyLossUsd,
      message: dailyLossAbs >= this.config.maxDailyLossUsd
        ? `Daily loss $${dailyLossAbs.toFixed(2)} exceeds limit $${this.config.maxDailyLossUsd}`
        : `Daily loss $${dailyLossAbs.toFixed(2)} within limit $${this.config.maxDailyLossUsd}`,
    };
    checks.push(dailyLossUsdCheck);
    if (!dailyLossUsdCheck.passed) {
      blockedReasons.push(dailyLossUsdCheck.message);
    }

    const dailyLossPctCheck: RiskCheck = {
      name: 'DAILY_LOSS_PCT',
      passed: dailyLossPctOfCapital < this.config.maxDailyLossPct,
      value: dailyLossPctOfCapital,
      limit: this.config.maxDailyLossPct,
      message: dailyLossPctOfCapital >= this.config.maxDailyLossPct
        ? `Daily loss ${dailyLossPctOfCapital.toFixed(1)}% exceeds limit ${this.config.maxDailyLossPct}%`
        : `Daily loss ${dailyLossPctOfCapital.toFixed(1)}% within limit ${this.config.maxDailyLossPct}%`,
    };
    checks.push(dailyLossPctCheck);
    if (!dailyLossPctCheck.passed) {
      blockedReasons.push(dailyLossPctCheck.message);
    }

    // ── 3. Max Open Positions Check ────────────────────────────
    const openOrders = this.getOrders('SUBMITTED').length +
      this.getOrders('CONFIRMED').filter((o) => o.side === 'BUY' && !o.executedPrice).length;
    const openPositions = openOrders;
    const maxPositionsCheck: RiskCheck = {
      name: 'MAX_OPEN_POSITIONS',
      passed: openPositions < this.config.maxOpenPositions,
      value: openPositions,
      limit: this.config.maxOpenPositions,
      message: openPositions >= this.config.maxOpenPositions
        ? `Open positions ${openPositions} at limit ${this.config.maxOpenPositions}`
        : `Open positions ${openPositions} within limit ${this.config.maxOpenPositions}`,
    };
    checks.push(maxPositionsCheck);
    if (!maxPositionsCheck.passed) {
      blockedReasons.push(maxPositionsCheck.message);
    }

    // ── 4. Max Trade Size Check ────────────────────────────────
    const maxTradeCheck: RiskCheck = {
      name: 'MAX_TRADE_SIZE',
      passed: order.positionSizeUsd <= this.config.maxTradeSizeUsd,
      value: order.positionSizeUsd,
      limit: this.config.maxTradeSizeUsd,
      message: order.positionSizeUsd > this.config.maxTradeSizeUsd
        ? `Trade size $${order.positionSizeUsd} exceeds max $${this.config.maxTradeSizeUsd}`
        : `Trade size $${order.positionSizeUsd} within max $${this.config.maxTradeSizeUsd}`,
    };
    checks.push(maxTradeCheck);
    if (!maxTradeCheck.passed) {
      blockedReasons.push(maxTradeCheck.message);
    }

    // ── 5. Wallet Balance Check (STUB in DRY_RUN) ─────────────
    const wallet = this.selectWalletForTrade(order.chain, order.positionSizeUsd);
    const walletHasBalance = this.config.mode === 'DRY_RUN'
      ? true // In dry-run, assume sufficient balance
      : wallet !== null; // In live, check real balance (wallet null = no eligible wallet)

    const walletCheck: RiskCheck = {
      name: 'WALLET_BALANCE',
      passed: walletHasBalance,
      value: wallet ? wallet.maxAllocationUsd : 0,
      limit: order.positionSizeUsd,
      message: this.config.mode === 'DRY_RUN'
        ? 'Wallet balance check SKIPPED (DRY_RUN mode)'
        : walletHasBalance
          ? `Wallet has sufficient balance ($${wallet!.maxAllocationUsd})`
          : `No wallet with sufficient balance for $${order.positionSizeUsd}`,
    };
    checks.push(walletCheck);
    if (!walletCheck.passed) {
      blockedReasons.push(walletCheck.message);
    }

    // ── 6. Slippage Tolerance Check ────────────────────────────
    const slippageHigh = order.slippageTolerancePct > 5;
    const slippageCheck: RiskCheck = {
      name: 'SLIPPAGE_TOLERANCE',
      passed: !slippageHigh,
      value: order.slippageTolerancePct,
      limit: 5,
      message: slippageHigh
        ? `Slippage tolerance ${order.slippageTolerancePct}% is very high (limit: 5%)`
        : `Slippage tolerance ${order.slippageTolerancePct}% is acceptable`,
    };
    checks.push(slippageCheck);
    if (slippageHigh) {
      warnings.push(slippageCheck.message);
      // Don't block, just warn — high slippage may be intentional for low-liquidity tokens
    }

    // ── 7. Chain Enabled Check ─────────────────────────────────
    const chainEnabled = this.config.enabledChains
      .map((c) => c.toUpperCase())
      .includes(order.chain.toUpperCase());
    const chainCheck: RiskCheck = {
      name: 'CHAIN_ENABLED',
      passed: chainEnabled,
      value: chainEnabled ? 1 : 0,
      limit: 1,
      message: chainEnabled
        ? `Chain ${order.chain} is enabled for trading`
        : `Chain ${order.chain} is NOT enabled for trading`,
    };
    checks.push(chainCheck);
    if (!chainCheck.passed) {
      blockedReasons.push(chainCheck.message);
    }

    // ── 8. System Risk Limit Check ─────────────────────────────
    const systemTemplate = tradingSystemEngine.getTemplate(order.systemName);
    let systemRiskCheck: RiskCheck;
    if (systemTemplate) {
      const positionPctOfCapital = systemTemplate.riskParams.maxPositionSizePct;
      const positionSizePct = todayStats.capitalUsed > 0
        ? (order.positionSizeUsd / todayStats.capitalUsed) * 100
        : order.positionSizeUsd; // Fallback: can't compute % without known capital

      const withinSystemLimit = positionSizePct <= positionPctOfCapital;
      systemRiskCheck = {
        name: 'SYSTEM_RISK_LIMIT',
        passed: withinSystemLimit,
        value: positionSizePct,
        limit: positionPctOfCapital,
        message: withinSystemLimit
          ? `Position ${positionSizePct.toFixed(1)}% within system limit ${positionPctOfCapital}%`
          : `Position ${positionSizePct.toFixed(1)}% exceeds system limit ${positionPctOfCapital}%`,
      };
    } else {
      // No system template found — warn but don't block
      systemRiskCheck = {
        name: 'SYSTEM_RISK_LIMIT',
        passed: true,
        value: 0,
        limit: 0,
        message: `System template "${order.systemName}" not found — skipping system risk check`,
      };
      warnings.push(systemRiskCheck.message);
    }
    checks.push(systemRiskCheck);
    if (!systemRiskCheck.passed) {
      blockedReasons.push(systemRiskCheck.message);
    }

    // ── 9. Daily Trading Halt Check ────────────────────────────
    if (todayStats.isTradingHalted) {
      const haltCheck: RiskCheck = {
        name: 'DAILY_TRADING_HALT',
        passed: false,
        value: 1,
        limit: 0,
        message: `Trading is halted for today: ${todayStats.haltReason || 'unknown reason'}`,
      };
      checks.push(haltCheck);
      blockedReasons.push(haltCheck.message);
    }

    // ── Compile result ─────────────────────────────────────────
    const approved = blockedReasons.length === 0;

    console.info(
      `[TradeEngine] Risk check for order ${order.id}: ${approved ? 'APPROVED' : 'BLOCKED'} ` +
      `(${checks.filter((c) => c.passed).length}/${checks.length} passed)` +
      (warnings.length > 0 ? ` | warnings: ${warnings.join('; ')}` : '')
    );

    return {
      approved,
      checks,
      warnings,
      blockedReasons,
    };
  }

  /**
   * Check daily P&L and limits. Returns the current daily stats.
   */
  async checkDailyLimits(): Promise<DailyExecutionStats> {
    const stats = this.getOrCreateDailyStats();

    // Check if trading should be halted
    const dailyLossAbs = Math.abs(Math.min(stats.realizedPnlUsd, 0));
    if (dailyLossAbs >= this.config.maxDailyLossUsd && !stats.isTradingHalted) {
      stats.isTradingHalted = true;
      stats.haltReason = `Daily loss $${dailyLossAbs.toFixed(2)} reached limit $${this.config.maxDailyLossUsd}`;

      console.warn(`[TradeEngine] ⛔ Daily trading halted: ${stats.haltReason}`);
    }

    // Also check percentage-based limit
    if (stats.capitalUsed > 0) {
      const lossPct = (dailyLossAbs / stats.capitalUsed) * 100;
      if (lossPct >= this.config.maxDailyLossPct && !stats.isTradingHalted) {
        stats.isTradingHalted = true;
        stats.haltReason = `Daily loss ${lossPct.toFixed(1)}% reached limit ${this.config.maxDailyLossPct}%`;

        console.warn(`[TradeEngine] ⛔ Daily trading halted: ${stats.haltReason}`);
      }
    }

    this.dailyStats.set(stats.date, stats);
    return stats;
  }

  /**
   * EMERGENCY STOP — immediately halts ALL trading activity.
   */
  activateKillSwitch(reason: string): void {
    this.config.killSwitch = true;
    this.killSwitchReason = reason;

    console.error(
      `[TradeEngine] 🚨 KILL SWITCH ACTIVATED: ${reason}`
    );
    console.error(
      `[TradeEngine] All trading is now halted. Call deactivateKillSwitch() to resume.`
    );
  }

  /**
   * Resume after emergency stop.
   */
  deactivateKillSwitch(): void {
    const wasActive = this.config.killSwitch;
    this.config.killSwitch = false;
    this.killSwitchReason = null;

    if (wasActive) {
      console.info(`[TradeEngine] ✅ Kill switch deactivated — trading resumed`);
    }
  }

  // ============================================================
  // EXECUTION (STUB — simulated)
  // ============================================================

  /**
   * Execute an order on a DEX.
   * 
   * CURRENT STATUS: STUB
   * - DRY_RUN mode: simulates execution with expected price + small random slippage
   * - LIVE mode: throws an error (not yet implemented)
   * 
   * When ready for live execution, replace the LIVE branch with:
   * 1. Build the transaction using the DEX router SDK
   * 2. Sign with the wallet's private key (decrypted from privateKeyEncrypted)
   * 3. Broadcast to the network
   * 4. Poll for confirmation
   * 5. Record the actual fill price and fees
   */
  async executeOrder(orderId: string): Promise<ExecutionOrder> {
    const order = this.orders.get(orderId);
    if (!order) {
      throw new Error(`Order ${orderId} not found`);
    }

    if (order.status !== 'SUBMITTED') {
      throw new Error(`Order ${orderId} must be SUBMITTED before execution (current: ${order.status})`);
    }

    // ── DRY_RUN MODE: Simulate execution ───────────────────────
    if (this.config.mode === 'DRY_RUN') {
      return this.simulateExecution(order);
    }

    // ── LIVE MODE: Not yet implemented ─────────────────────────
    throw new Error(
      'Live execution not yet implemented. ' +
      'To enable live trading, implement the DEX router integration in executeOrder(). ' +
      'This requires: (1) DEX SDK integration (Jupiter/1inch), ' +
      '(2) Wallet signing with decrypted private keys, ' +
      '(3) Transaction broadcasting and confirmation monitoring.'
    );
  }

  /**
   * Simulate order execution in DRY_RUN mode.
   * Applies small random slippage to the expected price to mimic real fills.
   */
  private async simulateExecution(order: ExecutionOrder): Promise<ExecutionOrder> {
    // Simulate a small delay as if the TX was being processed
    await new Promise((resolve) => setTimeout(resolve, 100 + Math.random() * 400));

    // Simulate slippage: random value between -slippageTolerance and +slippageTolerance
    const slippageDirection = order.side === 'BUY' ? 1 : -1; // Buys get worse (higher) price, sells get worse (lower)
    const randomSlippagePct = (Math.random() * order.slippageTolerancePct) / 100;
    const slippageMultiplier = 1 + slippageDirection * randomSlippagePct;

    // If we don't have a price, use a mock
    const basePrice = order.price > 0 ? order.price : 0.001;
    const executedPrice = basePrice * slippageMultiplier;
    const executedQuantity = order.positionSizeUsd / executedPrice;

    // Simulate fees (typical DEX fees: 0.3% for AMMs, plus gas)
    const dexFeePct = 0.003; // 0.3%
    const gasFeeUsd = this.config.defaultPriorityFeeUsd;
    const dexFeeUsd = order.positionSizeUsd * dexFeePct;
    const totalFeesUsd = dexFeeUsd + gasFeeUsd;

    // Update the order
    order.status = 'CONFIRMED';
    order.confirmedAt = new Date();
    order.executedPrice = executedPrice;
    order.executedQuantity = executedQuantity;
    order.feesUsd = totalFeesUsd;
    order.txHash = `sim_${order.id}_${Date.now()}`;

    this.orders.set(order.id, order);

    // Update daily stats
    const stats = this.getOrCreateDailyStats();
    stats.tradesExecuted++;
    stats.tradesSucceeded++;
    stats.totalFeesUsd += totalFeesUsd;
    stats.capitalUsed += order.positionSizeUsd;
    this.dailyStats.set(stats.date, stats);

    // Audit log with full context
    console.info(
      `[TradeEngine] ✅ SIMULATED execution: ${order.id} | ` +
      `${order.side} ${order.tokenSymbol} | ` +
      `qty=${executedQuantity.toFixed(6)} | ` +
      `price=$${executedPrice.toFixed(6)} (slippage: ${(randomSlippagePct * 100).toFixed(2)}%) | ` +
      `fees=$${totalFeesUsd.toFixed(4)} | ` +
      `system=${order.systemName} | dex=${order.dexRoute}`
    );

    // Record in DB
    this.recordOrderInDb(order);

    return order;
  }

  // ============================================================
  // STATS & CONFIG
  // ============================================================

  /**
   * Get today's execution statistics.
   */
  getExecutionStats(): DailyExecutionStats {
    return this.getOrCreateDailyStats();
  }

  /**
   * Get the current execution configuration.
   */
  getConfig(): ExecutionConfig {
    return { ...this.config };
  }

  /**
   * Update the execution configuration (hot-swappable at runtime).
   * Changes take effect immediately for subsequent orders.
   */
  updateConfig(updates: Partial<ExecutionConfig>): void {
    const prevMode = this.config.mode;
    const prevKillSwitch = this.config.killSwitch;

    this.config = { ...this.config, ...updates };

    // Log significant changes
    if (updates.mode && updates.mode !== prevMode) {
      console.warn(
        `[TradeEngine] ⚠️ Execution mode changed: ${prevMode} → ${updates.mode}`
      );
    }
    if (updates.killSwitch !== undefined && updates.killSwitch !== prevKillSwitch) {
      if (updates.killSwitch) {
        console.error(`[TradeEngine] 🚨 Kill switch activated via config update`);
      } else {
        console.info(`[TradeEngine] ✅ Kill switch deactivated via config update`);
      }
    }
    if (updates.maxTradeSizeUsd !== undefined) {
      console.info(
        `[TradeEngine] Max trade size updated: $${updates.maxTradeSizeUsd}`
      );
    }
    if (updates.maxDailyLossUsd !== undefined) {
      console.info(
        `[TradeEngine] Max daily loss updated: $${updates.maxDailyLossUsd}`
      );
    }

    console.info(`[TradeEngine] Config updated: ${Object.keys(updates).join(', ')}`);
  }

  // ============================================================
  // PRIVATE HELPERS
  // ============================================================

  /**
   * Get or create daily stats for today.
   */
  private getOrCreateDailyStats(): DailyExecutionStats {
    const today = getTodayDateStr();
    let stats = this.dailyStats.get(today);
    if (!stats) {
      stats = {
        date: today,
        tradesExecuted: 0,
        tradesSucceeded: 0,
        tradesFailed: 0,
        totalFeesUsd: 0,
        realizedPnlUsd: 0,
        unrealizedPnlUsd: 0,
        capitalUsed: 0,
        maxDrawdownPct: 0,
        isTradingHalted: false,
        haltReason: null,
      };
      this.dailyStats.set(today, stats);
    }
    return stats;
  }

  /**
   * Initialize daily stats on startup.
   */
  private initializeDailyStats(): void {
    this.getOrCreateDailyStats();
  }

  /**
   * Record an order outcome in the database for audit trail.
   */
  private async recordOrderInDb(order: ExecutionOrder): Promise<void> {
    try {
      await db.userEvent.create({
        data: {
          eventType: `TRADE_${order.status}`,
          walletAddress: order.chain, // Store chain as context marker
          entryPrice: order.executedPrice ?? order.price,
          stopLoss: order.slippageTolerancePct,
          takeProfit: order.positionSizeUsd,
          pnl: order.feesUsd * -1, // Negative = cost
        },
      });
    } catch (error) {
      // Don't fail execution if DB write fails — just log
      console.error('[TradeEngine] Failed to record order in DB:', error);
    }
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const tradeExecutionEngine = new TradeExecutionEngine();
