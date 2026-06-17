/**
 * Paper Trading Engine - CryptoQuant Terminal
 * 
 * Motor de simulación en tiempo real con persistencia en DB.
 * Las posiciones y trades sobreviven reinicios del servidor.
 * Sincronización de precios en vivo desde DexScreener.
 * 
 * Ciclo de Paper Trading:
 * 1. SCAN: Brain analiza tokens en tiempo real
 * 2. FILTER: Solo tokens operables pasan
 * 3. SIGNAL: Brain genera señales de entrada/salida
 * 4. EXECUTE: Simula orden a precio de mercado + slippage
 * 5. TRACK: Monitorea posiciones abiertas, aplica SL/TP/trailing
 * 6. SYNC: Actualiza precios desde DexScreener cada 30s
 * 7. EXIT: Cierra posiciones cuando se cumplen condiciones
 * 8. RECORD: Almacena trades en DB para análisis persistente
 */

import { analyzeToken, createEmptyTokenAnalysis, type TokenAnalysis } from '@/lib/services/brain/brain-orchestrator';

/** Parse brainAnalysis from DB JSON, falling back to empty analysis if invalid */
function parseBrainAnalysis(json: string | null | undefined, fallback: { tokenAddress: string; symbol: string; chain: string }): TokenAnalysis {
  if (!json || json === '{}' || json === 'null') {
    return createEmptyTokenAnalysis(fallback);
  }
  try {
    const parsed = JSON.parse(json);
    if (parsed && typeof parsed === 'object' && parsed.tokenAddress) {
      // Validate essential fields exist — revive Date objects
      parsed.analyzedAt = parsed.analyzedAt ? new Date(parsed.analyzedAt) : new Date();
      return parsed as TokenAnalysis;
    }
    return createEmptyTokenAnalysis(fallback);
  } catch {
    return createEmptyTokenAnalysis(fallback);
  }
}
import { tradingSystemEngine, type SystemTemplate } from '@/lib/services/strategy/trading-system-engine';
import { calculateOperabilityScore, type OperabilityInput } from '@/lib/services/risk/operability-score';
import { capitalAllocationEngine, type AllocationInput, type AllocationMethod } from '@/lib/services/risk/capital-allocation';
import { feedbackLoopEngine } from '@/lib/services/backtesting/feedback-loop-engine';
import { riskPreFilter } from '@/lib/services/risk/risk-pre-filter';
import { portfolioIntelligenceEngine } from '@/lib/services/portfolio/portfolio-intelligence-engine';
import { dexScreenerClient } from '@/lib/services/data-sources/dexscreener-client';
import { killSwitchService, type PortfolioState } from '@/lib/services/risk/kill-switch-service';
import { eventBus } from '../shared/event-bus';

// ============================================================
// TYPES
// ============================================================

export type PaperTradingStatus = 'STOPPED' | 'RUNNING' | 'PAUSED';

export interface PaperTradingConfig {
  initialCapital: number;
  chain: string;
  systemName: string;
  scanIntervalMs: number;
  maxOpenPositions: number;
  feesPct: number;
  slippagePct: number;
  minOperabilityScore: number;
  autoFeedback: boolean;
}

export interface PaperPosition {
  id: string;
  tokenAddress: string;
  symbol: string;
  chain: string;
  direction: 'LONG' | 'SHORT';
  entryTime: Date;
  entryPrice: number;
  quantity: number;
  positionSizeUsd: number;
  currentPrice: number;
  strategyName: string;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
  /** Highest price reached since entry — used for LONG MFE + LONG trailing stop */
  highWaterMark: number;
  /** Lowest price reached since entry — used for SHORT MFE + SHORT trailing stop */
  lowWaterMark: number;
  exitConditions: string[];
  systemName: string;
  brainAnalysis: TokenAnalysis;
}

export interface PaperTradeRecord {
  id: string;
  position: PaperPosition;
  exitTime: Date;
  exitPrice: number;
  exitReason: string;
  pnl: number;
  pnlPct: number;
  holdTimeMin: number;
  mfe: number;
  mae: number;
}

export interface PaperTradingStats {
  status: PaperTradingStatus;
  startedAt: Date | null;
  uptimeMs: number;
  currentCapital: number;
  initialCapital: number;
  totalReturnPct: number;
  openPositions: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
  avgPnlPct: number;
  unrealizedPnl: number;
  maxDrawdownPct: number;
  sharpeRatio: number;
  lastScanAt: Date | null;
  lastPriceSyncAt: Date | null;
  tokensScanned: number;
  signalsGenerated: number;
}

// ============================================================
// DEFAULT CONFIG
// ============================================================

const DEFAULT_PAPER_CONFIG: PaperTradingConfig = {
  initialCapital: 10,
  chain: 'SOL',
  systemName: 'Smart Entry Mirror',
  scanIntervalMs: 60000,
  maxOpenPositions: 3,
  feesPct: 0.003,
  slippagePct: 0.5,
  minOperabilityScore: 50,
  autoFeedback: true,
};

// ============================================================
// PAPER TRADING ENGINE - Con persistencia en DB
// ============================================================

class PaperTradingEngine {
  private config: PaperTradingConfig = { ...DEFAULT_PAPER_CONFIG };
  private status: PaperTradingStatus = 'STOPPED';
  private startedAt: Date | null = null;
  private pausedAt: Date | null = null;
  private lastScanAt: Date | null = null;
  private lastPriceSyncAt: Date | null = null;

  // Caché en memoria para rendimiento
  private positions: Map<string, PaperPosition> = new Map();
  private tradeHistory: PaperTradeRecord[] = [];
  private currentCapital: number = 0;
  private peakCapital: number = 0;

  // IDs de DB
  private currentRunId: string = '';
  private currentSessionId: string = '';

  // Contadores acumulativos
  private totalTokensScanned: number = 0;
  private totalSignalsGenerated: number = 0;

  // Timers
  private scanTimer: ReturnType<typeof setInterval> | null = null;
  private priceSyncTimer: ReturnType<typeof setInterval> | null = null;

  // Contador de IDs
  private idCounter: number = 0;

  // [INTEGRATION FIX] Cycle counter for periodic feedback processing
  private cycleCount: number = 0;

  // ============================================================
  // 1. START - Con restauración desde DB
  // ============================================================

  async start(config: Partial<PaperTradingConfig>): Promise<{ started: boolean; message: string }> {
    if (this.status === 'RUNNING') {
      return { started: false, message: 'Paper trading ya está corriendo' };
    }

    // Mezclar config con defaults
    this.config = { ...DEFAULT_PAPER_CONFIG, ...config };

    // Validar sistema
    const system = tradingSystemEngine.getTemplate(this.config.systemName);
    if (!system) {
      return {
        started: false,
        message: `Sistema "${this.config.systemName}" no encontrado. Disponibles: ${tradingSystemEngine.getTemplates().map(t => t.name).join(', ')}`,
      };
    }

    const { db } = await import('@/lib/db');

    // Intentar restaurar sesión activa desde DB
    const activeSession = await db.paperTradingSession.findFirst({
      where: { status: { in: ['RUNNING', 'IDLE'] } },
      orderBy: { createdAt: 'desc' },
    });

    if (activeSession) {
      // Restaurar sesión existente
      this.currentSessionId = activeSession.id;
      this.currentRunId = activeSession.id;
      this.currentCapital = activeSession.currentCapital;
      this.peakCapital = activeSession.peakCapital;
      this.config.initialCapital = activeSession.initialCapital;
      this.config.chain = activeSession.chain;
      this.config.maxOpenPositions = activeSession.maxOpenPositions;
      this.config.scanIntervalMs = activeSession.scanIntervalMs;
      // FIX: Fee unit normalization — DB stores as percentage (e.g., 0.3 = 0.3%)
      // Config stores as fraction (e.g., 0.003 = 0.3%)
      // The write path ALWAYS stores config.feesPct * 100, so we ALWAYS divide by 100 on read.
      // Previous threshold-based logic (>= 0.5) was broken for fees < 0.5% (e.g., 0.3% → DB=0.3 → not divided → 30% fee!)
      const dbFeesPct = activeSession.feesPct;
      this.config.feesPct = dbFeesPct / 100;
      this.config.slippagePct = activeSession.slippagePct;
      this.config.minOperabilityScore = activeSession.minOperabilityScore;
      this.config.autoFeedback = activeSession.autoFeedback;
      this.config.systemName = activeSession.strategyName || this.config.systemName;

      // Restaurar posiciones abiertas desde DB
      const openPositions = await db.paperTradingPosition.findMany({
        where: { status: 'OPEN', runId: this.currentRunId },
      });

      this.positions.clear();
      for (const pos of openPositions) {
        this.positions.set(pos.id, {
          id: pos.id,
          tokenAddress: pos.tokenAddress || '',
          symbol: pos.tokenSymbol,
          chain: pos.chain,
          direction: pos.direction as 'LONG' | 'SHORT',
          entryTime: pos.openedAt,
          entryPrice: pos.entryPrice,
          quantity: pos.quantity,
          positionSizeUsd: pos.sizeUsd,
          currentPrice: pos.currentPrice,
          strategyName: pos.strategyName || this.config.systemName,
          unrealizedPnl: pos.pnlUsd,
          unrealizedPnlPct: pos.pnlPct,
          highWaterMark: pos.highestPrice,
          lowWaterMark: pos.lowestPrice
            ? Math.min(pos.entryPrice, pos.lowestPrice)
            : Math.min(pos.entryPrice, pos.currentPrice || pos.entryPrice),
          exitConditions: this.buildExitConditions(pos),
          systemName: pos.strategyName || this.config.systemName,
          brainAnalysis: parseBrainAnalysis(pos.brainAnalysisJson, {
            tokenAddress: pos.tokenAddress || '',
            symbol: pos.tokenSymbol,
            chain: pos.chain,
          }),
        });
      }

      // Restaurar historial de trades desde DB
      const dbTrades = await db.paperTradingTrade.findMany({
        where: { position: { runId: this.currentRunId } },
        orderBy: { closedAt: 'desc' },
        take: 100,
      });

      this.tradeHistory = dbTrades.map(t => ({
        id: t.id,
        position: {
          id: t.positionId,
          tokenAddress: '',
          symbol: t.tokenSymbol,
          chain: t.chain,
          direction: t.direction as 'LONG' | 'SHORT',
          entryTime: t.openedAt,
          entryPrice: t.entryPrice,
          quantity: t.quantity,
          positionSizeUsd: t.sizeUsd,
          currentPrice: t.exitPrice,
          strategyName: t.strategyName || '',
          unrealizedPnl: t.pnlUsd,
          unrealizedPnlPct: t.pnlPct,
          highWaterMark: 0,
          lowWaterMark: 0,
          exitConditions: [],
          systemName: t.strategyName || '',
          brainAnalysis: parseBrainAnalysis(t.brainAnalysisJson, {
            tokenAddress: '',
            symbol: t.tokenSymbol,
            chain: t.chain,
          }),
        },
        exitTime: t.closedAt,
        exitPrice: t.exitPrice,
        exitReason: t.exitReason || '',
        pnl: t.pnlUsd,
        pnlPct: t.pnlPct,
        holdTimeMin: t.holdTimeMin || 0,
        mfe: t.mfe,
        mae: t.mae,
      }));

      // Actualizar sesión a RUNNING
      await db.paperTradingSession.update({
        where: { id: this.currentSessionId },
        data: { status: 'RUNNING', startedAt: new Date() },
      });

      this.startedAt = activeSession.startedAt || new Date();
      console.log(`[PaperTrading] Sesión restaurada desde DB: ${openPositions.length} posiciones abiertas, ${dbTrades.length} trades históricos`);
    } else {
      // Crear nueva sesión en DB
      this.positions.clear();
      this.tradeHistory = [];
      this.currentCapital = this.config.initialCapital;
      this.peakCapital = this.config.initialCapital;
      this.totalTokensScanned = 0;
      this.totalSignalsGenerated = 0;
      this.idCounter = 0;
      this.startedAt = new Date();

      const session = await db.paperTradingSession.create({
        data: {
          status: 'RUNNING',
          initialCapital: this.config.initialCapital,
          currentCapital: this.currentCapital,
          peakCapital: this.peakCapital,
          chain: this.config.chain,
          maxOpenPositions: this.config.maxOpenPositions,
          scanIntervalMs: this.config.scanIntervalMs,
          feesPct: this.config.feesPct * 100, // Guardar como porcentaje
          slippagePct: this.config.slippagePct,
          minOperabilityScore: this.config.minOperabilityScore,
          autoFeedback: this.config.autoFeedback,
          strategyName: this.config.systemName,
          startedAt: this.startedAt,
        },
      });

      this.currentSessionId = session.id;
      this.currentRunId = session.id;
    }

    this.lastScanAt = null;
    this.pausedAt = null;
    this.status = 'RUNNING';

    // Iniciar loops
    this.startScanLoop();
    this.startPriceSyncLoop();

    return {
      started: true,
      message: `Paper trading iniciado con $${this.config.initialCapital} en ${this.config.chain} usando "${this.config.systemName}". Intervalo: ${this.config.scanIntervalMs / 1000}s. Precios en vivo cada 30s.`,
    };
  }

  // ============================================================
  // 2. STOP
  // ============================================================

  async stop(): Promise<{ stopped: boolean; message: string }> {
    if (this.status === 'STOPPED') {
      return { stopped: false, message: 'Paper trading ya está detenido' };
    }

    // Detener timers
    this.stopScanLoop();
    this.stopPriceSyncLoop();

    // Cerrar todas las posiciones abiertas
    const openPositionIds = Array.from(this.positions.keys());
    let closedCount = 0;
    for (const id of openPositionIds) {
      const record = await this.forceClosePosition(id, 'ENGINE_STOPPED');
      if (record) closedCount++;
    }

    // Actualizar sesión en DB
    if (this.currentSessionId) {
      try {
        const { db } = await import('@/lib/db');
        await db.paperTradingSession.update({
          where: { id: this.currentSessionId },
          data: {
            status: 'IDLE',
            currentCapital: this.currentCapital,
            peakCapital: this.peakCapital,
            totalTrades: this.tradeHistory.length,
            winningTrades: this.tradeHistory.filter(t => t.pnl > 0).length,
            totalPnlUsd: this.tradeHistory.reduce((s, t) => s + t.pnl, 0),
          },
        });
      } catch (err) {
        console.warn('[PaperTrading] Error actualizando sesión en DB:', err);
      }
    }

    const stats = this.getStatus();
    this.status = 'STOPPED';

    return {
      stopped: true,
      message: `Paper trading detenido. ${closedCount} posiciones cerradas. Capital final: $${stats.currentCapital.toFixed(2)} (${stats.totalReturnPct >= 0 ? '+' : ''}${stats.totalReturnPct.toFixed(2)}%). Trades: ${stats.totalTrades}, Win rate: ${(stats.winRate * 100).toFixed(1)}%`,
    };
  }

  // ============================================================
  // 3. PAUSE / RESUME
  // ============================================================

  async pause(): Promise<void> {
    if (this.status !== 'RUNNING') return;
    this.status = 'PAUSED';
    this.pausedAt = new Date();
    this.stopScanLoop();
    this.stopPriceSyncLoop();

    // Actualizar DB
    try {
      const { db } = await import('@/lib/db');
      await db.paperTradingSession.update({
        where: { id: this.currentSessionId },
        data: { status: 'PAUSED' },
      });
    } catch {}
  }

  async resume(): Promise<void> {
    if (this.status !== 'PAUSED') return;
    this.status = 'RUNNING';
    this.pausedAt = null;
    this.startScanLoop();
    this.startPriceSyncLoop();

    // Actualizar DB
    try {
      const { db } = await import('@/lib/db');
      await db.paperTradingSession.update({
        where: { id: this.currentSessionId },
        data: { status: 'RUNNING' },
      });
    } catch {}
  }

  // ============================================================
  // 4. GET STATUS
  // ============================================================

  getStatus(): PaperTradingStats {
    const now = new Date();
    const uptimeMs = this.startedAt ? now.getTime() - this.startedAt.getTime() : 0;

    let unrealizedPnl = 0;
    for (const pos of Array.from(this.positions.values())) {
      unrealizedPnl += pos.unrealizedPnl;
    }

    const totalTrades = this.tradeHistory.length;
    const winningTrades = this.tradeHistory.filter(t => t.pnl > 0).length;
    const losingTrades = this.tradeHistory.filter(t => t.pnl <= 0).length;
    const winRate = totalTrades > 0 ? winningTrades / totalTrades : 0;
    const avgPnlPct = totalTrades > 0
      ? this.tradeHistory.reduce((s, t) => s + t.pnlPct, 0) / totalTrades
      : 0;

    const currentTotalValue = this.currentCapital + unrealizedPnl;
    const maxDrawdownPct = this.peakCapital > 0
      ? Math.max(0, ((this.peakCapital - currentTotalValue) / this.peakCapital) * 100)
      : 0;

    const sharpeRatio = this.calculateSharpeRatio();

    const totalReturnPct = this.config.initialCapital > 0
      ? ((currentTotalValue - this.config.initialCapital) / this.config.initialCapital) * 100
      : 0;

    return {
      status: this.status,
      startedAt: this.startedAt,
      uptimeMs,
      currentCapital: Math.round(currentTotalValue * 100) / 100,
      initialCapital: this.config.initialCapital,
      totalReturnPct: Math.round(totalReturnPct * 100) / 100,
      openPositions: this.positions.size,
      totalTrades,
      winningTrades,
      losingTrades,
      winRate: Math.round(winRate * 10000) / 10000,
      avgPnlPct: Math.round(avgPnlPct * 100) / 100,
      unrealizedPnl: Math.round(unrealizedPnl * 100) / 100,
      maxDrawdownPct: Math.round(maxDrawdownPct * 100) / 100,
      sharpeRatio: Math.round(sharpeRatio * 100) / 100,
      lastScanAt: this.lastScanAt,
      lastPriceSyncAt: this.lastPriceSyncAt,
      tokensScanned: this.totalTokensScanned,
      signalsGenerated: this.totalSignalsGenerated,
    };
  }

  // ============================================================
  // 5. GET OPEN POSITIONS
  // ============================================================

  getOpenPositions(): PaperPosition[] {
    return Array.from(this.positions.values());
  }

  // ============================================================
  // 6. GET TRADE HISTORY
  // ============================================================

  getTradeHistory(): PaperTradeRecord[] {
    return [...this.tradeHistory];
  }

  // ============================================================
  // 6b. GET PORTFOLIO STATE (for kill switch evaluation)
  // ============================================================

  getPortfolioState(): PortfolioState {
    const totalPositionValue = Array.from(this.positions.values())
      .reduce((sum, p) => sum + p.positionSizeUsd, 0);
    const totalUnrealizedPnl = Array.from(this.positions.values())
      .reduce((sum, p) => sum + p.unrealizedPnl, 0);
    // Include unrealized PnL in drawdown calculation
    // Otherwise kill switch misses deep unrealized losses on open positions
    const effectiveCapital = this.currentCapital + totalUnrealizedPnl;
    const currentDD = this.peakCapital > 0
      ? Math.max(0, (this.peakCapital - effectiveCapital) / this.peakCapital) * 100
      : 0;

    // FIX: Use effectiveCapital (includes unrealized PnL) as denominator for concentration
    // to prevent concentration exceeding 100% when positions have unrealized losses
    const concentrationDenominator = Math.max(effectiveCapital, 1);

    // Concentration by token
    const tokenConcentration: Map<string, number> = new Map();
    for (const pos of this.positions.values()) {
      const pct = (pos.positionSizeUsd / concentrationDenominator) * 100;
      tokenConcentration.set(pos.tokenAddress, (tokenConcentration.get(pos.tokenAddress) || 0) + pct);
    }

    // Concentration by chain
    const chainConcentration: Map<string, number> = new Map();
    for (const pos of this.positions.values()) {
      const pct = (pos.positionSizeUsd / concentrationDenominator) * 100;
      chainConcentration.set(pos.chain, (chainConcentration.get(pos.chain) || 0) + pct);
    }

    // Concentration by sector (inferred from token/chain)
    const sectorConcentration: Map<string, number> = new Map();
    for (const pos of this.positions.values()) {
      const sector = killSwitchService.inferSector(pos.symbol, pos.chain);
      const pct = (pos.positionSizeUsd / concentrationDenominator) * 100;
      sectorConcentration.set(sector, (sectorConcentration.get(sector) || 0) + pct);
    }

    return {
      totalCapital: this.currentCapital,
      totalPositionValue,
      totalUnrealizedPnl,
      currentDrawdownPct: currentDD,
      openPositionCount: this.positions.size,
      tokenConcentration,
      chainConcentration,
      sectorConcentration,
    };
  }

  // ============================================================
  // 7. FORCE CLOSE POSITION
  // ============================================================

  async forceClosePosition(positionId: string, reason: string): Promise<PaperTradeRecord | null> {
    const position = this.positions.get(positionId);
    if (!position) return null;

    const record = await this.closePosition(position, position.currentPrice, reason);
    return record;
  }

  // ============================================================
  // 8. RUN SINGLE SCAN
  // ============================================================

  async runSingleScan(): Promise<{ tokensScanned: number; signalsGenerated: number; tradesOpened: number }> {
    if (this.status !== 'RUNNING') {
      return { tokensScanned: 0, signalsGenerated: 0, tradesOpened: 0 };
    }

    // KILL SWITCH CHECK: Global pause
    if (killSwitchService.getState().globalPause) {
      console.warn('[PaperTrading] Kill switch: Global pause active — skipping scan');
      return { tokensScanned: 0, signalsGenerated: 0, tradesOpened: 0 };
    }

    const scanStart = new Date();
    let tokensScanned = 0;
    let signalsGenerated = 0;
    let tradesOpened = 0;

    try {
      // STEP 1: Monitorear posiciones existentes (verificar salidas)
      await this.monitorOpenPositions();

      // STEP 2: Obtener tokens de DB ordenados por volumen
      const tokens = await this.fetchTopTokens();

      for (const token of tokens) {
        tokensScanned++;

        // Saltar si ya tenemos posición en este token
        const existingPosition = Array.from(this.positions.values()).find(
          p => p.tokenAddress === token.address
        );
        if (existingPosition) continue;

        // Saltar si llegamos al máximo de posiciones
        if (this.positions.size >= this.config.maxOpenPositions) break;

        try {
          // STEP 3: Análisis del brain
          const analysis = await analyzeToken(
            token.address,
            this.config.chain,
            this.calculatePositionSize(),
            5
          );

          analysis.symbol = token.symbol || analysis.symbol;

          // STEP 4: Verificar si brain dice TRADE
          if (analysis.action !== 'TRADE') continue;

          // STEP 5: Verificar operabilidad
          if (analysis.operabilityScore < this.config.minOperabilityScore) continue;

          // STEP 6: Verificar con motor de operabilidad
          const operInput: OperabilityInput = {
            tokenAddress: token.address,
            symbol: token.symbol,
            chain: this.config.chain as 'SOL' | 'ETH' | 'BASE' | 'ARB' | string,
            priceUsd: token.priceUsd,
            liquidityUsd: token.liquidity,
            volume24h: token.volume24h,
            marketCap: token.marketCap,
            positionSizeUsd: this.calculatePositionSize(),
            expectedGainPct: 5,
            botActivityPct: token.botActivityPct,
            holderCount: token.holderCount,
            priceChange24h: token.priceChange24h,
            dexId: token.dexId || undefined,
            pairCreatedAt: token.createdAt ? new Date(token.createdAt).getTime() : undefined,
          };

          const operResult = calculateOperabilityScore(operInput);
          if (!operResult.isOperable || operResult.overallScore < this.config.minOperabilityScore) {
            continue;
          }

          // STEP 7: Señal generada
          signalsGenerated++;

          // KILL SWITCH CHECK: Can we open new positions?
          let positionSizeUsd = this.calculatePositionSize();
          // Get fresh portfolio state INSIDE the loop (not stale from before)
          // This prevents race condition where position A is opened but
          // token B's concentration check uses pre-A state
          const portfolioState = this.getPortfolioState();
          const budget = await killSwitchService.loadRiskBudget();

          // Clamp position size to concentration limit: max (maxConcentrationPct% of total capital)
          const maxAllowedByConcentration = portfolioState.totalCapital * budget.maxConcentrationPct / 100;
          const currentTokenConcentration = portfolioState.tokenConcentration.get(token.address) ?? 0;
          const currentTokenUsd = portfolioState.totalCapital * currentTokenConcentration / 100;
          const remainingTokenCapacity = Math.max(0, maxAllowedByConcentration - currentTokenUsd);
          positionSizeUsd = Math.min(positionSizeUsd, remainingTokenCapacity);

          // Clamp position size to chain concentration limit
          const maxAllowedByChain = portfolioState.totalCapital * budget.maxChainPct / 100;
          const currentChainConcentration = portfolioState.chainConcentration.get(this.config.chain) ?? 0;
          const currentChainUsd = portfolioState.totalCapital * currentChainConcentration / 100;
          const remainingChainCapacity = Math.max(0, maxAllowedByChain - currentChainUsd);
          positionSizeUsd = Math.min(positionSizeUsd, remainingChainCapacity);

          if (positionSizeUsd < 0.01) {
            // Position too small after concentration clamping — skip
            continue;
          }

          const killSwitchCheck = await killSwitchService.canOpenPosition({
            tokenAddress: token.address,
            chain: this.config.chain,
            sizeUsd: positionSizeUsd,
            strategyId: this.config.systemName,
            symbol: token.symbol,
            currentPortfolioState: portfolioState,
          });
          if (!killSwitchCheck.allowed) {
            if (killSwitchCheck.killSwitchTriggered) {
              console.warn(`[PaperTrading] Kill switch blocked position: ${killSwitchCheck.reason}`);
            }
            continue;
          }

          // [INTEGRATION FIX] STEP 7a: Risk Pre-Filter — kill invalid signals before SDE
          try {
            const preFilterResult = await riskPreFilter.filter(
              {
                tokenAddress: token.address,
                chain: this.config.chain,
                direction: 'LONG' as const,
                confidence: (analysis.operabilityScore || 50) / 100,
                strategyName: this.config.systemName || 'default',
                signalType: (analysis.action === 'TRADE' ? 'MOMENTUM' : 'EXIT') as 'MOMENTUM' | 'EXIT',
                sizeUsd: positionSizeUsd,
              },
              {
                totalCapital: this.config.initialCapital,
                freeCapital: this.getAvailableCapital(),
                openPositions: Array.from(this.positions.values()).map(p => ({
                  tokenAddress: p.tokenAddress || '',
                  chain: p.chain || this.config.chain,
                  sizeUsd: p.positionSizeUsd,
                  pnlPct: p.unrealizedPnlPct,
                  direction: p.direction || 'LONG',
                })),
                currentDD: this.getCurrentDrawdownPct() / 100,
                dailyPnL: this.getDailyPnL() / 100,
              }
            );

            if (!preFilterResult.passed) {
              console.log(`[PTE] Signal rejected by Risk Pre-Filter: ${preFilterResult.rejectionReasons.join(', ')}`);
              continue;
            }
          } catch (preFilterError) {
            // Risk pre-filter failure should NOT block trading — fail open
            console.warn('[PTE] Risk Pre-Filter error (proceeding without pre-filter):', preFilterError instanceof Error ? preFilterError.message : String(preFilterError));
          }

          // STEP 7b: SDE Validation Gate — Strategy Decision Engine must approve
          try {
            const { strategyDecisionEngine } = await import('@/lib/services/strategy/strategy-decision-engine');
            const sdeInput = await strategyDecisionEngine.buildInputFromStrategyId(
              this.config.systemName,
              {
                totalCapitalUsd: portfolioState.totalCapital,
                currentDrawdownPct: portfolioState.currentDrawdownPct,
                activeStrategies: 1,
                marketVolatility: 50, // default
                marketRegime: 'SIDEWAYS', // default
              },
            );
            if (sdeInput) {
              const sdeDecision = await strategyDecisionEngine.validate(sdeInput, true);
              if (sdeDecision.state === 'REJECTED' || sdeDecision.capitalAction === 'EXIT') {
                console.warn(`[PaperTrading] SDE rejected position: state=${sdeDecision.state}, action=${sdeDecision.capitalAction}, quality=${sdeDecision.signalQuality}`);
                continue;
              }
              if (sdeDecision.capitalAction === 'REDUCE') {
                positionSizeUsd = positionSizeUsd * 0.5;
                console.log(`[PaperTrading] SDE recommends REDUCE — halving position size to $${positionSizeUsd.toFixed(2)}`);
              }
              if (sdeDecision.state === 'ACTIVE' && sdeDecision.capitalAction === 'INCREASE') {
                // SDE approves increase — allow up to 1.5x the calculated size (still capped by concentration)
                positionSizeUsd = Math.min(positionSizeUsd * 1.5, portfolioState.totalCapital * budget.maxConcentrationPct / 100);
              }
            }
          } catch (sdeError) {
            // SDE validation failure should NOT block trading — fail open
            console.warn('[PaperTrading] SDE validation error (proceeding without SDE gate):', sdeError instanceof Error ? sdeError.message : String(sdeError));
          }

          // [INTEGRATION FIX] STEP 7c: Portfolio Intelligence Engine — evaluate impact before opening
          try {
            const currentPositions = Array.from(this.positions.values()).map(p => ({
              id: p.id,
              tokenAddress: p.tokenAddress || '',
              symbol: p.symbol,
              chain: p.chain || this.config.chain,
              sector: killSwitchService.inferSector(p.symbol, p.chain),
              sizeUsd: p.positionSizeUsd,
              entryPrice: p.entryPrice,
              currentPrice: p.currentPrice,
              unrealizedPnl: p.unrealizedPnl,
              unrealizedPnlPct: p.unrealizedPnlPct,
              weight: p.positionSizeUsd / Math.max(this.currentCapital, 1),
              volatility: 0.6,
              returns: [],
              marketCapTier: 'MID' as const,
              strategyId: p.systemName || null,
              openedAt: p.entryTime,
            }));

            const totalPortfolioValue = this.currentCapital + Array.from(this.positions.values())
              .reduce((sum, p) => sum + p.unrealizedPnl, 0);

            const portfolioImpact = await portfolioIntelligenceEngine.evaluateNewPosition(
              {
                tokenAddress: token.address,
                symbol: token.symbol,
                chain: this.config.chain,
                sector: killSwitchService.inferSector(token.symbol, this.config.chain),
                proposedSizeUsd: positionSizeUsd,
                expectedVolatility: analysis.volatilityRegime === 'EXTREME' ? 0.9 : analysis.volatilityRegime === 'HIGH' ? 0.7 : 0.5,
                expectedReturn: analysis.regime === 'BULL' ? 0.05 : analysis.regime === 'BEAR' ? -0.02 : 0.01,
                marketCapTier: 'MID' as const,
                returns: [],
                strategyId: this.config.systemName || null,
              },
              currentPositions,
              Math.max(totalPortfolioValue, 1),
            );

            if (!portfolioImpact.approved) {
              console.log(`[PTE] Position rejected by Portfolio Intelligence: ${portfolioImpact.recommendations.join(', ')}`);
              continue;
            }
            // Adjust position size based on portfolio impact
            if (portfolioImpact.impactScore < 0) {
              positionSizeUsd = Math.min(positionSizeUsd, positionSizeUsd * (1 - Math.abs(portfolioImpact.impactScore)));
            }
          } catch (portfolioError) {
            // Portfolio Intelligence failure should NOT block trading — fail open
            console.warn('[PTE] Portfolio Intelligence error (proceeding without PI gate):', portfolioError instanceof Error ? portfolioError.message : String(portfolioError));
          }

          // STEP 8: Abrir posición (use the concentration-clamped size)
          const position = await this.openPosition(token, analysis, positionSizeUsd);
          if (position) {
            tradesOpened++;
          }

        } catch (error) {
          console.warn(
            `[PaperTrading] Error analizando ${token.address}:`,
            error instanceof Error ? error.message : String(error)
          );
        }
      }
    } catch (error) {
      console.error(
        '[PaperTrading] Error en scan:',
        error instanceof Error ? error.message : String(error)
      );
    }

    // Actualizar contadores
    this.totalTokensScanned += tokensScanned;
    this.totalSignalsGenerated += signalsGenerated;
    this.lastScanAt = scanStart;

    // [INTEGRATION FIX] Process feedback for recently closed trades (every 5th cycle)
    this.cycleCount++;
    if (this.cycleCount % 5 === 0) {
      try {
        await feedbackLoopEngine.validateSignals();
      } catch (e) {
        console.error('[PTE] Feedback processing error:', e);
      }
    }

    // Actualizar sesión en DB
    await this.updateSessionInDb();

    return { tokensScanned, signalsGenerated, tradesOpened };
  }

  // ============================================================
  // 9. SYNC OPEN POSITION PRICES - Live desde DexScreener
  // ============================================================

  async syncOpenPositionPrices(): Promise<{ updated: number; errors: number }> {
    const openPositions = Array.from(this.positions.values());
    if (openPositions.length === 0) return { updated: 0, errors: 0 };

    let updated = 0;
    let errors = 0;

    // Agrupar por chain para batch fetching
    const byChain = new Map<string, PaperPosition[]>();
    for (const pos of openPositions) {
      const chain = pos.chain;
      if (!byChain.has(chain)) byChain.set(chain, []);
      byChain.get(chain)!.push(pos);
    }

    for (const [chain, positions] of byChain) {
      try {
        // Filtrar posiciones con tokenAddress válido
        const positionsWithAddress = positions.filter(p => p.tokenAddress && p.tokenAddress !== '');
        if (positionsWithAddress.length === 0) {
          // Fallback: usar precios de DB local
          for (const pos of positions) {
            const dbPrice = await this.fetchCurrentPriceFromDb(pos.tokenAddress);
            if (dbPrice > 0) {
              await this.updatePositionPrice(pos, dbPrice);
              updated++;
            }
          }
          continue;
        }

        // Batch fetch desde DexScreener (máximo 30 por request)
        for (let i = 0; i < positionsWithAddress.length; i += 30) {
          const batch = positionsWithAddress.slice(i, i + 30);
          const addresses = batch.map(p => p.tokenAddress);

          try {
            // Fetch prices for each position in the batch from DexScreener
            for (const pos of batch) {
              try {
                const pairs = await dexScreenerClient.searchTokenPairs(pos.tokenAddress);
                const matchingPair = pairs.find(p =>
                  p.baseToken?.address?.toLowerCase() === pos.tokenAddress?.toLowerCase()
                ) || (pairs.length > 0 ? pairs.reduce((a, b) =>
                  (b.liquidity?.usd || 0) > (a.liquidity?.usd || 0) ? b : a,
                ) : null);

                if (matchingPair && matchingPair.priceUsd) {
                  const newPrice = parseFloat(matchingPair.priceUsd);
                  if (newPrice > 0) {
                    await this.updatePositionPrice(pos, newPrice);
                    updated++;
                    continue; // Skip DB fallback for this position
                  }
                }
              } catch {
                // Individual position fetch failed — try DB fallback below
              }

              // Fallback to DB local price
              if (pos.currentPrice === 0 || pos.currentPrice === pos.entryPrice) {
                const dbPrice = await this.fetchCurrentPriceFromDb(pos.tokenAddress);
                if (dbPrice > 0) {
                  await this.updatePositionPrice(pos, dbPrice);
                  updated++;
                }
              }
            }
          } catch (err) {
            // Fallback a DB local para este batch
            for (const pos of batch) {
              const dbPrice = await this.fetchCurrentPriceFromDb(pos.tokenAddress);
              if (dbPrice > 0) {
                await this.updatePositionPrice(pos, dbPrice);
                updated++;
              } else {
                errors++;
              }
            }
          }
        }
      } catch (err) {
        console.error(`[PaperTrading] Error sincronizando precios para chain ${chain}:`, err);
        errors += positions.length;
      }
    }

    this.lastPriceSyncAt = new Date();

    // FIX: Update peakCapital when effective capital exceeds it
    // This ensures maxDrawdownPct is computed correctly when unrealized gains push total above peak
    const totalUnrealizedPnlSync = Array.from(this.positions.values())
      .reduce((sum, p) => sum + p.unrealizedPnl, 0);
    const effectiveCapitalSync = this.currentCapital + totalUnrealizedPnlSync;
    if (effectiveCapitalSync > this.peakCapital) {
      this.peakCapital = effectiveCapitalSync;
    }

    // KILL SWITCH: Evaluate portfolio-level and position-level kill switches after price sync
    try {
      const portfolioState = this.getPortfolioState();

      // Portfolio DD kill switch
      const portfolioKillCheck = await killSwitchService.evaluatePortfolioKillSwitches(portfolioState);
      if (portfolioKillCheck.triggered && portfolioKillCheck.actionRequired === 'PAUSE_ALL') {
        console.error(`[PaperTrading] KILL SWITCH: Portfolio DD triggered — ${portfolioKillCheck.reason}`);
        await this.pause();
        try {
          const { alertEngine } = await import('@/lib/services/risk/alert-engine');
          await alertEngine.onRiskLimitTriggered('PORTFOLIO', {
            killSwitch: true,
            currentDD: portfolioState.currentDrawdownPct,
            totalCapital: portfolioState.totalCapital,
          });
        } catch {}
      }

      // Position loss kill switches — emergency close individual positions
      // CRITICAL FIX: Collect positions to close first, then close in separate loop
      // to avoid mutating this.positions during iteration (race condition)
      const openPositionsSnapshot = Array.from(this.positions.values());
      const positionsToClose: { position: PaperPosition; reason: string }[] = [];

      for (const pos of openPositionsSnapshot) {
        const posKillCheck = await killSwitchService.evaluatePositionKillSwitch(pos.id, pos.unrealizedPnlPct);
        if (posKillCheck.triggered && posKillCheck.actionRequired === 'CLOSE_POSITION') {
          console.error(`[PaperTrading] KILL SWITCH: Position loss triggered for ${pos.symbol} — closing: ${posKillCheck.reason}`);
          positionsToClose.push({ position: pos, reason: 'KILL_SWITCH_POSITION_LOSS' });
        }
      }

      // Strategy DD kill switch — evaluate per-strategy drawdown
      const strategyGroups = new Map<string, { totalEntryUsd: number; totalCurrentUsd: number; positions: typeof openPositionsSnapshot }>();
      for (const pos of openPositionsSnapshot) {
        const key = pos.strategyName || pos.tokenAddress;
        const group = strategyGroups.get(key) || { totalEntryUsd: 0, totalCurrentUsd: 0, positions: [] as typeof openPositionsSnapshot };
        group.totalEntryUsd += pos.positionSizeUsd;
        group.totalCurrentUsd += pos.positionSizeUsd * (1 + pos.unrealizedPnlPct / 100);
        group.positions.push(pos);
        strategyGroups.set(key, group);
      }
      for (const [strategyKey, group] of strategyGroups) {
        if (group.totalEntryUsd > 0) {
          const strategyDD = Math.max(0, ((group.totalEntryUsd - group.totalCurrentUsd) / group.totalEntryUsd) * 100);
          const strategyKillCheck = await killSwitchService.evaluateStrategyKillSwitch(strategyKey, strategyDD);
          if (strategyKillCheck.triggered && strategyKillCheck.actionRequired === 'PAUSE_STRATEGY') {
            console.error(`[PaperTrading] KILL SWITCH: Strategy DD triggered for ${strategyKey} — ${strategyKillCheck.reason}`);
            for (const pos of group.positions) {
              positionsToClose.push({ position: pos, reason: 'KILL_SWITCH_STRATEGY_DD' });
            }
            try {
              const { alertEngine } = await import('@/lib/services/risk/alert-engine');
              await alertEngine.onRiskLimitTriggered('STRATEGY', {
                killSwitch: true,
                strategyId: strategyKey,
                currentDD: strategyDD,
              });
            } catch {}
          }
        }
      }

      // Now close all collected positions in a separate loop (safe from mutation)
      for (const { position, reason } of positionsToClose) {
        // Verify position still exists before closing
        if (this.positions.has(position.id)) {
          await this.closePosition(position, position.currentPrice, reason);
        }
      }
    } catch (killSwitchError) {
      console.warn('[PaperTrading] Kill switch evaluation error:', killSwitchError);
    }

    // Actualizar timestamp en sesión
    if (this.currentSessionId) {
      try {
        const { db } = await import('@/lib/db');
        await db.paperTradingSession.update({
          where: { id: this.currentSessionId },
          data: { lastPriceSyncAt: this.lastPriceSyncAt },
        });
      } catch {}
    }

    return { updated, errors };
  }

  // ============================================================
  // 10. ACTIVATE STRATEGY FROM AI MANAGER
  // ============================================================

  async activateStrategy(params: {
    tokenAddress: string;
    tokenSymbol: string;
    chain: string;
    strategyName: string;
    direction?: 'LONG' | 'SHORT';
    operabilityScore?: number;
  }): Promise<{ success: boolean; positionId?: string; message: string }> {
    if (this.status !== 'RUNNING') {
      return { success: false, message: 'Paper trading no está corriendo' };
    }

    // Verificar si ya tenemos posición en este token
    const existing = Array.from(this.positions.values()).find(
      p => p.tokenAddress === params.tokenAddress
    );
    if (existing) {
      return { success: false, message: `Ya existe posición abierta para ${params.tokenSymbol}` };
    }

    // Verificar máximo de posiciones
    if (this.positions.size >= this.config.maxOpenPositions) {
      return { success: false, message: `Máximo de posiciones alcanzado (${this.config.maxOpenPositions})` };
    }

    // Kill switch + concentration check — same gate as runSingleScan
    // STEP 1: Calculate position size
    const portfolioState = this.getPortfolioState();
    const rawPositionSizeUsd = this.calculatePositionSize();

    // STEP 2: Concentration clamping FIRST (before kill switch check)
    const budget = await killSwitchService.loadRiskBudget();
    let adjustedSize = rawPositionSizeUsd;
    const currentTokenConcentration = portfolioState.tokenConcentration.get(params.tokenAddress) ?? 0;
    const maxAllowedByConcentration = portfolioState.totalCapital * budget.maxConcentrationPct / 100;
    const currentTokenUsd = portfolioState.totalCapital * currentTokenConcentration / 100;
    const remainingTokenCapacity = Math.max(0, maxAllowedByConcentration - currentTokenUsd);
    adjustedSize = Math.min(adjustedSize, remainingTokenCapacity);

    const currentChainConcentration = portfolioState.chainConcentration.get(params.chain || this.config.chain) ?? 0;
    const maxAllowedByChain = portfolioState.totalCapital * budget.maxChainPct / 100;
    const currentChainUsd = portfolioState.totalCapital * currentChainConcentration / 100;
    const remainingChainCapacity = Math.max(0, maxAllowedByChain - currentChainUsd);
    adjustedSize = Math.min(adjustedSize, remainingChainCapacity);

    if (adjustedSize < 0.01) {
      return { success: false, message: 'Position too small after concentration limits' };
    }

    // STEP 3: Kill switch check with concentration-clamped size
    const killSwitchCheck = await killSwitchService.canOpenPosition({
      tokenAddress: params.tokenAddress,
      chain: params.chain || this.config.chain,
      sizeUsd: adjustedSize,
      strategyId: params.strategyName,
      symbol: params.tokenSymbol,
      currentPortfolioState: portfolioState,
    });
    if (!killSwitchCheck.allowed) {
      return { success: false, message: `Kill switch: ${killSwitchCheck.reason}` };
    }

    // SDE Validation Gate
    try {
      const { strategyDecisionEngine } = await import('@/lib/services/strategy/strategy-decision-engine');
      const portfolioState = this.getPortfolioState();
      const sdeInput = await strategyDecisionEngine.buildInputFromStrategyId(
        params.strategyName,
        {
          totalCapitalUsd: portfolioState.totalCapital,
          currentDrawdownPct: portfolioState.currentDrawdownPct,
          activeStrategies: 1,
          marketVolatility: 50,
          marketRegime: 'SIDEWAYS',
        },
      );
      if (sdeInput) {
        const sdeDecision = await strategyDecisionEngine.validate(sdeInput, true);
        if (sdeDecision.state === 'REJECTED' || sdeDecision.capitalAction === 'EXIT') {
          return { success: false, message: `SDE rejected: state=${sdeDecision.state}, action=${sdeDecision.capitalAction}` };
        }
        if (sdeDecision.capitalAction === 'REDUCE') {
          adjustedSize = adjustedSize * 0.5;
        }
      }
    } catch (sdeError) {
      console.warn('[PaperTrading] SDE validation error (proceeding without SDE gate):', sdeError instanceof Error ? sdeError.message : String(sdeError));
    }

    try {
      // Obtener precio actual
      const priceUsd = await this.fetchCurrentPriceFromDb(params.tokenAddress);
      if (priceUsd <= 0) {
        return { success: false, message: `No se pudo obtener precio para ${params.tokenSymbol}` };
      }

      // Use concentration-clamped size from kill switch check above
      const positionSizeUsd = adjustedSize;
      if (positionSizeUsd <= 0) {
        return { success: false, message: 'Capital insuficiente' };
      }

      // Crear análisis simplificado para estrategia activada
      const analysis = {
        tokenAddress: params.tokenAddress,
        symbol: params.tokenSymbol,
        chain: params.chain || this.config.chain,
        analyzedAt: new Date(),
        dataFreshness: 'RECENT' as const,
        candlesAvailable: 0,
        regime: 'SIDEWAYS' as const,
        regimeConfidence: 0.5,
        volatilityRegime: 'NORMAL' as const,
        lifecyclePhase: 'GROWTH' as const,
        lifecycleConfidence: 0.5,
        tradingPhase: 'growth',
        isTransitioning: false,
        netBehaviorFlow: 'NEUTRAL' as const,
        behaviorConfidence: 0.5,
        dominantArchetype: 'UNKNOWN',
        behaviorAnomaly: false,
        botSwarmLevel: 'LOW' as const,
        dominantBotType: null,
        whaleDirection: 'NEUTRAL' as const,
        whaleConfidence: 0.5,
        smartMoneyFlow: 'NEUTRAL' as const,
        operabilityScore: params.operabilityScore || 75,
        operabilityLevel: 'GOOD',
        isOperable: true,
        feeEstimate: { totalCostUsd: 0, totalCostPct: 0, slippagePct: 0 },
        recommendedPositionUsd: positionSizeUsd,
        minimumGainPct: 1,
        meanReversionZone: null,
        anomalyDetected: false,
        anomalyScore: 0,
        patternScanResult: null,
        patternSignal: 'NEUTRAL' as const,
        patternScore: 0,
        dominantPattern: null,
        patternConfluences: 0,
        deepAnalysis: null,
        deepRecommendation: null,
        deepRiskLevel: null,
        deepRiskScore: 0,
        crossCorrelation: null,
        correlatedOutcome: 'NEUTRAL' as const,
        correlatedProbability: 0.5,
        correlationConflict: false,
        recommendedSystems: [],
        action: 'TRADE' as const,
        actionReason: `Estrategia activada por AI Manager: ${params.strategyName}`,
        warnings: [],
        evidence: [],
      } satisfies TokenAnalysis;

      // Abrir posición
      const position = await this.openPosition(
        { address: params.tokenAddress, symbol: params.tokenSymbol, priceUsd },
        analysis,
        positionSizeUsd,
        params.direction || 'LONG',  // Pass direction — otherwise SHORTs open as LONG
      );

      if (position) {
        return {
          success: true,
          positionId: position.id,
          message: `Posición abierta: ${params.direction || 'LONG'} ${params.tokenSymbol} @ $${priceUsd.toFixed(6)}`,
        };
      }

      return { success: false, message: 'Error al abrir posición' };
    } catch (err) {
      return { success: false, message: `Error: ${err instanceof Error ? err.message : String(err)}` };
    }
  }

  // ============================================================
  // PRIVATE: SCAN LOOP
  // ============================================================

  private startScanLoop(): void {
    this.stopScanLoop();
    this.scanTimer = setInterval(async () => {
      try {
        await this.runSingleScan();
      } catch (error) {
        console.error(
          '[PaperTrading] Error no manejado en scan loop:',
          error instanceof Error ? error.message : String(error)
        );
      }
    }, this.config.scanIntervalMs);
  }

  private stopScanLoop(): void {
    if (this.scanTimer !== null) {
      clearInterval(this.scanTimer);
      this.scanTimer = null;
    }
  }

  // ============================================================
  // PRIVATE: PRICE SYNC LOOP - Cada 30 segundos
  // ============================================================

  private startPriceSyncLoop(): void {
    this.stopPriceSyncLoop();
    // Sincronizar precios cada 30 segundos
    this.priceSyncTimer = setInterval(async () => {
      try {
        const result = await this.syncOpenPositionPrices();
        if (result.updated > 0 || result.errors > 0) {
          console.log(`[PaperTrading] Price sync: ${result.updated} actualizados, ${result.errors} errores`);
        }
      } catch (error) {
        console.error(
          '[PaperTrading] Error en price sync loop:',
          error instanceof Error ? error.message : String(error)
        );
      }
    }, 30000);
  }

  private stopPriceSyncLoop(): void {
    if (this.priceSyncTimer !== null) {
      clearInterval(this.priceSyncTimer);
      this.priceSyncTimer = null;
    }
  }

  // ============================================================
  // PRIVATE: FETCH TOP TOKENS FROM DB
  // ============================================================

  private async fetchTopTokens(): Promise<Array<{
    address: string;
    symbol: string;
    priceUsd: number;
    liquidity: number;
    volume24h: number;
    marketCap: number;
    botActivityPct: number;
    holderCount: number;
    priceChange24h: number;
    dexId: string | null;
    createdAt: Date | string | null;
  }>> {
    try {
      const { db } = await import('@/lib/db');

      const tokens = await db.token.findMany({
        where: {
          chain: this.config.chain,
          volume24h: { gt: 0 },
          priceUsd: { gt: 0 },
        },
        orderBy: { volume24h: 'desc' },
        take: 20,
        select: {
          address: true,
          symbol: true,
          priceUsd: true,
          liquidity: true,
          volume24h: true,
          marketCap: true,
          botActivityPct: true,
          holderCount: true,
          priceChange24h: true,
          dexId: true,
          createdAt: true,
        },
      });

      return tokens.map(t => ({
        address: t.address,
        symbol: t.symbol,
        priceUsd: t.priceUsd,
        liquidity: t.liquidity,
        volume24h: t.volume24h,
        marketCap: t.marketCap,
        botActivityPct: t.botActivityPct,
        holderCount: t.holderCount,
        priceChange24h: t.priceChange24h,
        dexId: t.dexId,
        createdAt: t.createdAt,
      }));
    } catch (error) {
      console.warn(
        '[PaperTrading] Error obteniendo tokens de DB:',
        error instanceof Error ? error.message : String(error)
      );
      return [];
    }
  }

  // ============================================================
  // PRIVATE: CALCULATE POSITION SIZE
  // Uses SDE dynamic method selection: picks the best allocation
  // method based on portfolio state (drawdown, volatility, etc.)
  // Falls back to EQUAL_WEIGHT if SDE/CAE fails.
  // ============================================================

  private calculatePositionSize(): number {
    const openPositionValue = Array.from(this.positions.values()).reduce(
      (sum, p) => sum + p.positionSizeUsd,
      0
    );
    const availableCapital = this.currentCapital - openPositionValue;

    if (availableCapital <= 0) return 0;

    const remainingSlots = Math.max(1, this.config.maxOpenPositions - this.positions.size);

    // SDE-based dynamic method selection
    // Include unrealized PnL in drawdown calculation for accurate method selection
    const totalUnrealizedPnl = Array.from(this.positions.values())
      .reduce((sum, p) => sum + p.unrealizedPnl, 0);
    const effectiveCapital = this.currentCapital + totalUnrealizedPnl;
    const currentDD = this.peakCapital > 0
      ? Math.max(0, (this.peakCapital - effectiveCapital) / this.peakCapital) * 100
      : 0;

    let method: AllocationMethod = 'KELLY_MODIFIED';

    // SDE method selection logic (from Revisión 3)
    if (currentDD > 10) {
      method = 'MAX_DRAWDOWN_CONTROL';
    } else if (this.positions.size >= 2) {
      method = 'RISK_PARITY';
    } else {
      method = 'KELLY_MODIFIED';
    }

    try {
      const winRate = this.tradeHistory.length > 0
        ? this.tradeHistory.filter(t => t.pnl > 0).length / this.tradeHistory.length
        : 0.5;
      const avgWin = this.tradeHistory.length > 0
        ? this.tradeHistory.filter(t => t.pnl > 0).reduce((s, t) => s + (t.pnlPct || 0.10), 0) / Math.max(1, this.tradeHistory.filter(t => t.pnl > 0).length) / 100
        : 0.10;
      const avgLoss = this.tradeHistory.length > 0
        ? this.tradeHistory.filter(t => t.pnl <= 0).reduce((s, t) => s + Math.abs(t.pnlPct || 0.05), 0) / Math.max(1, this.tradeHistory.filter(t => t.pnl <= 0).length) / 100
        : 0.05;

      const allocationInput: AllocationInput = {
        capital: availableCapital,
        currentPositions: Array.from(this.positions.values()).map(p => ({
          tokenAddress: p.tokenAddress,
          sizeUsd: p.positionSizeUsd,
          sizePct: p.positionSizeUsd / this.currentCapital,
        })),
        signals: Array.from({ length: remainingSlots }, (_, i) => ({
          tokenAddress: `slot-${i}`,
          confidence: 1 / remainingSlots,
          direction: 'LONG' as const,
        })),
        historicalTrades: {
          winRate,
          avgWin,
          avgLoss,
          totalTrades: this.tradeHistory.length,
        },
        volatility: 0.6, // Default crypto volatility estimate
        currentDrawdown: currentDD / 100,
        maxDrawdown: 0.20,
        marketRegime: 'SIDEWAYS',
        estimatedFeePct: this.config.feesPct,
        estimatedSlippagePct: this.config.slippagePct / 100,
        minimumNetGainPct: 0.018, // 1.8% minimum after fees
        expectedGainPct: 0.05,    // 5% expected gain
      };

      const result = capitalAllocationEngine.calculate(method, allocationInput);
      const perSlotSize = result.positions.length > 0
        ? result.positions[0].sizeUsd
        : availableCapital / remainingSlots;

      return Math.max(0, Math.round(perSlotSize * 100) / 100);
    } catch {
      // Fallback to simple equal-split if CapitalAllocationEngine fails
      const positionSize = availableCapital / remainingSlots;
      return Math.max(0, Math.round(positionSize * 100) / 100);
    }
  }

  // [INTEGRATION FIX] Helper: get available (free) capital
  private getAvailableCapital(): number {
    const openPositionValue = Array.from(this.positions.values()).reduce(
      (sum, p) => sum + p.positionSizeUsd, 0
    );
    return Math.max(0, this.currentCapital - openPositionValue);
  }

  // [INTEGRATION FIX] Helper: get current drawdown percentage
  private getCurrentDrawdownPct(): number {
    const totalUnrealizedPnl = Array.from(this.positions.values())
      .reduce((sum, p) => sum + p.unrealizedPnl, 0);
    const effectiveCapital = this.currentCapital + totalUnrealizedPnl;
    return this.peakCapital > 0
      ? Math.max(0, ((this.peakCapital - effectiveCapital) / this.peakCapital) * 100)
      : 0;
  }

  // [INTEGRATION FIX] Helper: get daily PnL percentage
  private getDailyPnL(): number {
    const oneDayAgo = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const dailyTrades = this.tradeHistory.filter(t => t.exitTime >= oneDayAgo);
    const dailyPnl = dailyTrades.reduce((sum, t) => sum + t.pnl, 0);
    return this.config.initialCapital > 0
      ? (dailyPnl / this.config.initialCapital) * 100
      : 0;
  }

  // ============================================================
  // PRIVATE: OPEN POSITION - Con persistencia en DB
  // ============================================================

  private async openPosition(
    token: {
      address: string;
      symbol: string;
      priceUsd: number;
    },
    analysis: TokenAnalysis,
    recommendedPositionUsd: number,
    direction: 'LONG' | 'SHORT' = 'LONG',
  ): Promise<PaperPosition | null> {
    const positionSizeUsd = Math.min(
      this.calculatePositionSize(),
      recommendedPositionUsd
    );

    if (positionSizeUsd <= 0) return null;

    const system = tradingSystemEngine.getTemplate(this.config.systemName);
    if (!system) return null;

    // Direction-aware slippage on entry
    // LONG: we buy, so slippage makes entry price HIGHER (unfavorable)
    // SHORT: we sell, so slippage makes entry price LOWER (unfavorable)
    const isShort = direction === 'SHORT';
    const slippageMultiplier = isShort
      ? 1 - (this.config.slippagePct / 100)
      : 1 + (this.config.slippagePct / 100);
    const entryPrice = token.priceUsd * slippageMultiplier;

    // Deducir fees simuladas
    const entryFee = positionSizeUsd * this.config.feesPct;

    // Calculate quantity from NET position size (after fees)
    // Previously used gross size, causing capital accounting error
    const netPositionSize = positionSizeUsd - entryFee;
    const quantity = netPositionSize / entryPrice;

    // Construir condiciones de salida
    const exitConditions: string[] = [];
    if (system.exitSignal.stopLossPct !== 0) exitConditions.push('stop_loss');
    if (system.exitSignal.takeProfitPct !== 0) exitConditions.push('take_profit');
    if (system.exitSignal.trailingStopPct) exitConditions.push('trailing_stop');
    if (system.exitSignal.timeBasedExitMin) exitConditions.push('time_exit');
    exitConditions.push('brain_signal_change');

    const id = `paper-${++this.idCounter}-${Date.now()}`;

    const position: PaperPosition = {
      id,
      tokenAddress: token.address,
      symbol: token.symbol,
      chain: this.config.chain,
      direction: direction ?? 'LONG',
      entryTime: new Date(),
      entryPrice: Math.round(entryPrice * 100000000) / 100000000,
      quantity: Math.round(quantity * 100000000) / 100000000,
      positionSizeUsd: Math.round(netPositionSize * 100) / 100,
      currentPrice: entryPrice,
      strategyName: this.config.systemName,
      unrealizedPnl: 0,
      unrealizedPnlPct: 0,
      highWaterMark: entryPrice,
      lowWaterMark: entryPrice,
      exitConditions,
      systemName: this.config.systemName,
      brainAnalysis: { ...analysis },
    };

    // Guardar en memoria
    this.positions.set(id, position);

    // Persistir en DB
    try {
      const { db } = await import('@/lib/db');
      await db.paperTradingPosition.create({
        data: {
          id: position.id,
          runId: this.currentRunId,
          tokenSymbol: position.symbol,
          tokenAddress: position.tokenAddress,
          chain: position.chain,
          direction: position.direction,
          entryPrice: position.entryPrice,
          currentPrice: position.currentPrice,
          quantity: position.quantity,
          sizeUsd: position.positionSizeUsd,
          highestPrice: position.highWaterMark,
          lowestPrice: position.lowWaterMark,
          operabilityScore: analysis.operabilityScore,
          brainAnalysisJson: JSON.stringify(position.brainAnalysis),
          strategyName: position.systemName,
          status: 'OPEN',
          openedAt: position.entryTime,
          stopLoss: system.exitSignal.stopLossPct
            ? (direction === 'SHORT'
              ? position.entryPrice * (1 + system.exitSignal.stopLossPct / 100)   // SHORT: stop above entry (price rises = loss)
              : position.entryPrice * (1 - system.exitSignal.stopLossPct / 100))  // LONG: stop below entry (price falls = loss)
            : null,
          takeProfit: system.exitSignal.takeProfitPct
            ? (direction === 'SHORT'
              ? position.entryPrice * (1 - system.exitSignal.takeProfitPct / 100)   // SHORT: TP below entry
              : position.entryPrice * (1 + system.exitSignal.takeProfitPct / 100))  // LONG: TP above entry
            : null,
          trailingStopPct: system.exitSignal.trailingStopPct || 0,
        },
      });
    } catch (err) {
      console.warn('[PaperTrading] Error persistiendo posición en DB:', err);
    }

    console.log(
      `[PaperTrading] ABIERTA ${position.direction} ${position.symbol} @ $${entryPrice.toFixed(6)} | Size: $${position.positionSizeUsd.toFixed(2)} | Operabilidad: ${analysis.operabilityScore}/100 | Fase: ${analysis.lifecyclePhase} | Razón: ${analysis.actionReason}`
    );

    // Fire alert for trade opened
    try {
      const { alertEngine } = await import('@/lib/services/risk/alert-engine');
      await alertEngine.onTradeOpened(position.symbol, position.direction, entryPrice, position.positionSizeUsd);
    } catch (error) {
      console.warn('[PaperTrading] Alert engine error (open):', error);
    }

    // Publish POSITION_OPENED event via event bus
    try {
      eventBus.publish('POSITION_OPENED', {
        positionId: position.id,
        tokenAddress: position.tokenAddress || '',
        chain: position.chain || this.config.chain,
        sizeUsd: position.positionSizeUsd,
        direction: position.direction || 'LONG',
        timestamp: new Date(),
      }, 'paper-trading-engine');
    } catch (eventError) {
      console.warn('[PaperTrading] Event bus POSITION_OPENED error:', eventError);
    }

    return position;
  }

  // ============================================================
  // PRIVATE: CLOSE POSITION - Con persistencia en DB
  // ============================================================

  private async closePosition(
    position: PaperPosition,
    exitPrice: number,
    reason: string
  ): Promise<PaperTradeRecord> {
    // Idempotency guard — prevent double-close
    if (!this.positions.has(position.id)) {
      return null as unknown as PaperTradeRecord;
    }

    // Clear position kill switch flag
    try {
      killSwitchService.clearPositionKillSwitch(position.id);
    } catch {}

    // Direction-aware slippage on exit
    // LONG: we sell to close, so slippage makes exit price LOWER (unfavorable)
    // SHORT: we buy to cover, so slippage makes exit price HIGHER (unfavorable)
    const isShort = position.direction === 'SHORT';
    const slippageMultiplier = reason === 'ENGINE_STOPPED'
      ? 1
      : (isShort
          ? 1 + (this.config.slippagePct / 100)
          : 1 - (this.config.slippagePct / 100));
    const adjustedExitPrice = exitPrice * slippageMultiplier;

    // Direction-aware PnL
    // LONG: profit when exit > entry → exitValue - entryValue
    // SHORT: profit when entry > exit → entryValue - exitValue
    const entryValue = position.quantity * position.entryPrice;
    const exitValue = position.quantity * adjustedExitPrice;
    const exitFee = exitValue * this.config.feesPct;

    const pnl = isShort
      ? entryValue - exitValue - exitFee
      : exitValue - entryValue - exitFee;
    const pnlPct = entryValue > 0 ? (pnl / entryValue) * 100 : 0;

    const exitTime = new Date();
    const holdTimeMin = (exitTime.getTime() - position.entryTime.getTime()) / 60000;

    const mfe = this.calculateMFE(position);
    const mae = this.calculateMAE(position);

    const record: PaperTradeRecord = {
      id: `trade-${position.id}`,
      position: { ...position, currentPrice: adjustedExitPrice },
      exitTime,
      exitPrice: Math.round(adjustedExitPrice * 100000000) / 100000000,
      exitReason: reason,
      pnl: Math.round(pnl * 100) / 100,
      pnlPct: Math.round(pnlPct * 100) / 100,
      holdTimeMin: Math.round(holdTimeMin * 100) / 100,
      mfe: Math.round(mfe * 100) / 100,
      mae: Math.round(mae * 100) / 100,
    };

    // Actualizar capital
    this.currentCapital += pnl;
    if (this.currentCapital > this.peakCapital) {
      this.peakCapital = this.currentCapital;
    }

    // Remover de posiciones abiertas
    this.positions.delete(position.id);

    // Agregar a historial
    this.tradeHistory.push(record);

    // Persistir en DB
    try {
      const { db } = await import('@/lib/db');

      // Actualizar posición a CLOSED
      await db.paperTradingPosition.update({
        where: { id: position.id },
        data: {
          status: 'CLOSED',
          currentPrice: adjustedExitPrice,
          pnlUsd: record.pnl,
          pnlPct: record.pnlPct,
          mfe: record.mfe,
          mae: record.mae,
          highestPrice: position.highWaterMark,
          lowestPrice: position.lowWaterMark,
          closedAt: exitTime,
          exitReason: reason,
        },
      });

      // Crear registro de trade
      await db.paperTradingTrade.create({
        data: {
          positionId: position.id,
          tokenSymbol: position.symbol,
          chain: position.chain,
          direction: position.direction,
          entryPrice: position.entryPrice,
          exitPrice: adjustedExitPrice,
          quantity: position.quantity,
          sizeUsd: position.positionSizeUsd,
          pnlUsd: record.pnl,
          pnlPct: record.pnlPct,
          mfe: record.mfe,
          mae: record.mae,
          exitReason: reason,
          operabilityScore: position.brainAnalysis?.operabilityScore,
          brainAnalysisJson: JSON.stringify(position.brainAnalysis),
          strategyName: position.systemName,
          holdTimeMin: record.holdTimeMin,
          openedAt: position.entryTime,
          closedAt: exitTime,
        },
      });

      // Actualizar sesión
      await db.paperTradingSession.update({
        where: { id: this.currentSessionId },
        data: {
          currentCapital: this.currentCapital,
          peakCapital: this.peakCapital,
          totalTrades: this.tradeHistory.length,
          winningTrades: this.tradeHistory.filter(t => t.pnl > 0).length,
          totalPnlUsd: this.tradeHistory.reduce((s, t) => s + t.pnl, 0),
        },
      });
    } catch (err) {
      console.warn('[PaperTrading] Error persistiendo cierre en DB:', err);
    }

    console.log(
      `[PaperTrading] CERRADA ${position.direction} ${position.symbol} | Razón: ${reason} | PnL: $${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%) | Hold: ${holdTimeMin.toFixed(0)}min | Capital: $${this.currentCapital.toFixed(2)}`
    );

    // Auto-feedback: SDE feedback loop (S1.14 — Paper trading close → reevaluar con SDE → actualizar DecisionAudit)
    if (this.config.autoFeedback) {
      this.submitFeedback(record).catch(err => {
        console.warn('[PaperTrading] Error enviando feedback:', err instanceof Error ? err.message : String(err));
      });
      // Also call SDE provideFeedback to close the learning loop
      try {
        const { strategyDecisionEngine } = await import('@/lib/services/strategy/strategy-decision-engine');
        // Find the latest audit for this strategy to feed back results
        const audits = await strategyDecisionEngine.queryAudit({
          strategyId: position.systemName || this.config.systemName,
          limit: 1,
        });
        if (audits.length > 0) {
          await strategyDecisionEngine.provideFeedback(
            audits[0].id,
            pnl > 0,
            pnlPct,
          );
        }
      } catch (sdeErr) {
        // SDE feedback failure should not break the close flow
        console.warn('[PaperTrading] SDE feedback error:', sdeErr instanceof Error ? sdeErr.message : String(sdeErr));
      }
    }

    // Fire alert for trade closed
    try {
      const { alertEngine } = await import('@/lib/services/risk/alert-engine');
      await alertEngine.onTradeClosed(position.symbol, position.direction, pnl, reason);
    } catch (error) {
      console.warn('[PaperTrading] Alert engine error (close):', error);
    }

    // Publish POSITION_CLOSED event via event bus
    try {
      eventBus.publish('POSITION_CLOSED', {
        positionId: position.id,
        tokenAddress: position.tokenAddress || '',
        chain: position.chain || this.config.chain,
        pnlPct: record.pnlPct,
        exitReason: record.exitReason || 'unknown',
        timestamp: new Date(),
      }, 'paper-trading-engine');
    } catch (eventError) {
      console.warn('[PaperTrading] Event bus POSITION_CLOSED error:', eventError);
    }

    // [INTEGRATION FIX] Feed trade outcome to feedback loop engine
    try {
      const { db: feedbackDb } = await import('@/lib/db');
      await feedbackDb.predictiveSignal.create({
        data: {
          signalType: 'paper_trade_exit',
          chain: position.chain,
          tokenAddress: position.tokenAddress,
          prediction: JSON.stringify({
            direction: position.direction,
            strategyName: position.systemName || position.strategyName,
            entryPrice: position.entryPrice,
            exitPrice: adjustedExitPrice,
            pnlPct,
            phase: 'unknown',
          }),
          direction: position.direction === 'LONG' ? 'UP' : 'DOWN',
          confidence: (position.brainAnalysis?.operabilityScore || 50) / 100,
          timeframe: `${Math.round(holdTimeMin)}min`,
          validUntil: new Date(exitTime.getTime() + 60 * 60 * 1000), // 1h window for validation
          evidence: JSON.stringify([{
            actual: pnlPct > 0 ? 'UP' : 'DOWN',
            predicted: position.direction === 'LONG' ? 'UP' : 'DOWN',
            pnlPct,
          }]),
          historicalHitRate: this.tradeHistory.length > 0
            ? this.tradeHistory.filter(t => t.pnl > 0).length / this.tradeHistory.length
            : 0,
          dataPointsUsed: 1,
          wasCorrect: (position.direction === 'LONG' && pnlPct > 0) || (position.direction === 'SHORT' && pnlPct > 0) ? true : false,
          actualOutcome: JSON.stringify({ realizedPnlPct: pnlPct, exitReason: reason }),
        },
      });
    } catch (e) {
      console.error('[PTE] Feedback loop error:', e);
    }

    return record;
  }

  // ============================================================
  // PRIVATE: UPDATE POSITION PRICE
  // ============================================================

  private async updatePositionPrice(position: PaperPosition, newPrice: number): Promise<void> {
    const entryValue = position.quantity * position.entryPrice;
    const currentValue = position.quantity * newPrice;
    // Direction-aware PnL: SHORT profits when price falls, LONG profits when price rises
    const pnlUsd = position.direction === 'SHORT'
      ? Math.round((entryValue - currentValue) * 100) / 100
      : Math.round((currentValue - entryValue) * 100) / 100;
    const pnlPct = entryValue > 0
      ? position.direction === 'SHORT'
        ? Math.round(((entryValue - currentValue) / entryValue) * 10000) / 100
        : Math.round(((currentValue - entryValue) / entryValue) * 10000) / 100
      : 0;

    const mfe = Math.max(position.brainAnalysis ? 0 : pnlPct, pnlPct); // Simplificado
    const mae = Math.min(0, pnlPct);
    const highestPrice = Math.max(position.highWaterMark, newPrice);
    const lowestPrice = Math.min(position.lowWaterMark, newPrice);

    // Actualizar caché en memoria
    position.currentPrice = newPrice;
    position.unrealizedPnl = pnlUsd;
    position.unrealizedPnlPct = pnlPct;
    position.highWaterMark = highestPrice;
    position.lowWaterMark = lowestPrice;

    // Persistir en DB
    try {
      const { db } = await import('@/lib/db');

      // Verificar trailing stop
      let trailingActivated = false;
      const dbPos = await db.paperTradingPosition.findUnique({
        where: { id: position.id },
        select: { trailingStopPct: true, trailingActivated: true },
      });

      if (dbPos?.trailingStopPct && dbPos.trailingStopPct > 0) {
        // LONG: trailing activated when price rises above entry + threshold
        // SHORT: trailing activated when price falls below entry - threshold
        if (position.direction === 'SHORT') {
          const threshold = position.entryPrice * (1 - (dbPos.trailingStopPct / 100));
          if (newPrice <= threshold) trailingActivated = true;
        } else {
          const threshold = position.entryPrice * (1 + (dbPos.trailingStopPct / 100));
          if (newPrice >= threshold) trailingActivated = true;
        }
      }

      await db.paperTradingPosition.update({
        where: { id: position.id },
        data: {
          currentPrice: newPrice,
          pnlUsd,
          pnlPct,
          mfe: Math.max(mfe, pnlPct),
          mae: Math.min(mae, pnlPct),
          highestPrice,
          trailingActivated: trailingActivated || (dbPos?.trailingActivated || false),
        },
      });
    } catch (err) {
      // Silencioso - no bloquear el loop de precios
    }

    // Verificar condiciones de salida
    await this.checkExitConditions(position, newPrice, pnlPct);
  }

  // ============================================================
  // PRIVATE: CHECK EXIT CONDITIONS
  // ============================================================

  private async checkExitConditions(position: PaperPosition, currentPrice: number, pnlPct: number): Promise<void> {
    const system = tradingSystemEngine.getTemplate(position.systemName);
    if (!system) return;

    const entryPrice = position.entryPrice;
    const isShort = position.direction === 'SHORT';

    // Price change from entry — for SHORT, inverted logic
    const priceChangePct = entryPrice > 0
      ? isShort
        ? ((entryPrice - currentPrice) / entryPrice) * 100  // SHORT: profit when price falls
        : ((currentPrice - entryPrice) / entryPrice) * 100  // LONG: profit when price rises
      : 0;

    let exitReason: string | null = null;

    // 1. Stop Loss — stopLossPct is positive (e.g., 8 = 8% drop triggers exit)
    // priceChangePct is direction-aware: negative means loss in both LONG and SHORT
    if (system.exitSignal.stopLossPct !== 0) {
      const stopLossPct = Math.abs(system.exitSignal.stopLossPct);
      if (priceChangePct <= -stopLossPct) {
        exitReason = `stop_loss_hit (${priceChangePct.toFixed(2)}% <= -${stopLossPct}%)`;
      }
    }

    // 2. Take Profit — same logic for both directions (priceChangePct is already direction-aware)
    if (!exitReason && system.exitSignal.takeProfitPct !== 0 && system.exitSignal.takeProfitPct > 0) {
      if (priceChangePct >= system.exitSignal.takeProfitPct) {
        exitReason = `take_profit_hit (${priceChangePct.toFixed(2)}% >= ${system.exitSignal.takeProfitPct}%)`;
      }
    }

    // 3. Trailing Stop — LONG: tracks highWaterMark, drops from HWM trigger exit
    //                    SHORT: tracks lowWaterMark, rises from LWM trigger exit
    if (!exitReason && system.exitSignal.trailingStopPct && system.exitSignal.trailingStopPct > 0) {
      const trailingPct = system.exitSignal.trailingStopPct;
      const activationPct = system.exitSignal.trailingActivationPct ?? 0;

      if (isShort) {
        // SHORT trailing: activated when price has dropped enough from entry
        const lowWaterChangePct = entryPrice > 0
          ? ((entryPrice - position.lowWaterMark) / entryPrice) * 100
          : 0;

        if (lowWaterChangePct >= activationPct) {
          const riseFromLWM = position.lowWaterMark > 0
            ? ((currentPrice - position.lowWaterMark) / position.lowWaterMark) * 100
            : 0;

          if (riseFromLWM >= trailingPct) {
            exitReason = `trailing_stop_hit (rose ${riseFromLWM.toFixed(2)}% from LWM $${position.lowWaterMark.toFixed(6)})`;
          }
        }
      } else {
        // LONG trailing: activated when price has risen enough from entry
        const highWaterChangePct = entryPrice > 0
          ? ((position.highWaterMark - entryPrice) / entryPrice) * 100
          : 0;

        if (highWaterChangePct >= activationPct) {
          const dropFromHWM = position.highWaterMark > 0
            ? ((position.highWaterMark - currentPrice) / position.highWaterMark) * 100
            : 0;

          if (dropFromHWM >= trailingPct) {
            exitReason = `trailing_stop_hit (dropped ${dropFromHWM.toFixed(2)}% from HWM $${position.highWaterMark.toFixed(6)})`;
          }
        }
      }
    }

    // 4. Time-based exit
    if (!exitReason && system.exitSignal.timeBasedExitMin && system.exitSignal.timeBasedExitMin > 0) {
      const holdTimeMin = (Date.now() - position.entryTime.getTime()) / 60000;
      if (holdTimeMin >= system.exitSignal.timeBasedExitMin) {
        exitReason = `time_expired (${holdTimeMin.toFixed(0)}min >= ${system.exitSignal.timeBasedExitMin}min)`;
      }
    }

    // 5. Brain signal change
    if (!exitReason) {
      const minutesSinceEntry = (Date.now() - position.entryTime.getTime()) / 60000;
      if (minutesSinceEntry > 5) {
        const analysis = position.brainAnalysis;
        if (analysis?.botSwarmLevel === 'CRITICAL') {
          exitReason = 'brain_signal_change (bot_swarm_CRITICAL)';
        } else if (analysis?.regime === 'BEAR' && analysis?.lifecyclePhase === 'DECLINE') {
          exitReason = 'brain_signal_change (bear_decline)';
        }
      }
    }

    if (exitReason) {
      await this.closePosition(position, currentPrice, exitReason);
    }
  }

  // ============================================================
  // PRIVATE: MONITOR OPEN POSITIONS
  // ============================================================

  private async monitorOpenPositions(): Promise<void> {
    const positionIds = Array.from(this.positions.keys());

    for (const id of positionIds) {
      const position = this.positions.get(id);
      if (!position) continue;

      try {
        // Obtener precio actual de DB local
        const currentPrice = await this.fetchCurrentPriceFromDb(position.tokenAddress);
        if (currentPrice <= 0) continue;

        // Update position with current price — direction-aware unrealized PnL
        position.currentPrice = currentPrice;

        const entryValue = position.quantity * position.entryPrice;
        const currentValue = position.quantity * currentPrice;
        const isShort = position.direction === 'SHORT';

        position.unrealizedPnl = Math.round(
          (isShort ? entryValue - currentValue : currentValue - entryValue) * 100
        ) / 100;
        position.unrealizedPnlPct = entryValue > 0
          ? Math.round(
              (isShort
                ? ((entryValue - currentValue) / entryValue)
                : ((currentValue - entryValue) / entryValue)) * 10000
            ) / 100
          : 0;

        // Track water marks for MFE/MAE and trailing stop
        if (currentPrice > position.highWaterMark) {
          position.highWaterMark = currentPrice;
        }
        if (currentPrice < position.lowWaterMark) {
          position.lowWaterMark = currentPrice;
        }

        // Verificar condiciones de salida
        const exitReason = this.checkExitConditionsSync(position);
        if (exitReason) {
          await this.closePosition(position, currentPrice, exitReason);
        }
      } catch (error) {
        console.warn(
          `[PaperTrading] Error monitoreando posición ${id}:`,
          error instanceof Error ? error.message : String(error)
        );
      }
    }
  }

  // ============================================================
  // PRIVATE: CHECK EXIT CONDITIONS (sync version for monitor)
  // ============================================================

  private checkExitConditionsSync(position: PaperPosition): string | null {
    const system = tradingSystemEngine.getTemplate(position.systemName);
    if (!system) return null;

    const currentPrice = position.currentPrice;
    const entryPrice = position.entryPrice;
    const isShort = position.direction === 'SHORT';

    // Direction-aware price change
    const priceChangePct = entryPrice > 0
      ? (isShort
          ? ((entryPrice - currentPrice) / entryPrice) * 100  // SHORT: profit when price falls
          : ((currentPrice - entryPrice) / entryPrice) * 100)  // LONG: profit when price rises
      : 0;

    // 1. Stop Loss — stopLossPct is positive, priceChangePct is direction-aware
    if (system.exitSignal.stopLossPct !== 0) {
      const stopLossPct = Math.abs(system.exitSignal.stopLossPct);
      if (priceChangePct <= -stopLossPct) {
        return `stop_loss_hit (${priceChangePct.toFixed(2)}% <= -${stopLossPct}%)`;
      }
    }

    // 2. Take Profit — direction-aware (priceChangePct already direction-aware)
    if (system.exitSignal.takeProfitPct !== 0 && system.exitSignal.takeProfitPct > 0) {
      if (priceChangePct >= system.exitSignal.takeProfitPct) {
        return `take_profit_hit (${priceChangePct.toFixed(2)}% >= ${system.exitSignal.takeProfitPct}%)`;
      }
    }

    // 3. Trailing Stop — direction-aware
    if (system.exitSignal.trailingStopPct && system.exitSignal.trailingStopPct > 0) {
      const trailingPct = system.exitSignal.trailingStopPct;
      const activationPct = system.exitSignal.trailingActivationPct ?? 0;

      if (isShort) {
        // SHORT trailing: track lowWaterMark, trail UP from lowest
        const lowWaterChangePct = entryPrice > 0
          ? ((entryPrice - position.lowWaterMark) / entryPrice) * 100
          : 0;

        if (lowWaterChangePct >= activationPct) {
          const riseFromLWM = position.lowWaterMark > 0
            ? ((currentPrice - position.lowWaterMark) / position.lowWaterMark) * 100
            : 0;

          if (riseFromLWM >= trailingPct) {
            return `trailing_stop_hit (rose ${riseFromLWM.toFixed(2)}% from LWM $${position.lowWaterMark.toFixed(6)})`;
          }
        }
      } else {
        // LONG trailing: track highWaterMark, trail DOWN from highest
        const highWaterChangePct = entryPrice > 0
          ? ((position.highWaterMark - entryPrice) / entryPrice) * 100
          : 0;

        if (highWaterChangePct >= activationPct) {
          const dropFromHWM = position.highWaterMark > 0
            ? ((position.highWaterMark - currentPrice) / position.highWaterMark) * 100
            : 0;

          if (dropFromHWM >= trailingPct) {
            return `trailing_stop_hit (dropped ${dropFromHWM.toFixed(2)}% from HWM $${position.highWaterMark.toFixed(6)})`;
          }
        }
      }
    }

    // 4. Time-based exit
    if (system.exitSignal.timeBasedExitMin && system.exitSignal.timeBasedExitMin > 0) {
      const holdTimeMin = (Date.now() - position.entryTime.getTime()) / 60000;
      if (holdTimeMin >= system.exitSignal.timeBasedExitMin) {
        return `time_expired (${holdTimeMin.toFixed(0)}min >= ${system.exitSignal.timeBasedExitMin}min)`;
      }
    }

    // 5. Brain signal change
    const minutesSinceEntry = (Date.now() - position.entryTime.getTime()) / 60000;
    if (minutesSinceEntry > 5) {
      const analysis = position.brainAnalysis;
      if (analysis?.botSwarmLevel === 'CRITICAL') {
        return 'brain_signal_change (bot_swarm_CRITICAL)';
      }
      if (analysis?.regime === 'BEAR' && analysis?.lifecyclePhase === 'DECLINE') {
        return 'brain_signal_change (bear_decline)';
      }
    }

    return null;
  }

  // ============================================================
  // PRIVATE: FETCH CURRENT PRICE FROM DB
  // ============================================================

  private async fetchCurrentPriceFromDb(tokenAddress: string): Promise<number> {
    try {
      const { db } = await import('@/lib/db');
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
  // PRIVATE: BUILD EXIT CONDITIONS FROM DB POSITION
  // ============================================================

  private buildExitConditions(pos: { stopLoss: number | null; takeProfit: number | null; trailingStopPct: number | null }): string[] {
    const conditions: string[] = [];
    if (pos.stopLoss !== null) conditions.push('stop_loss');
    if (pos.takeProfit !== null) conditions.push('take_profit');
    if (pos.trailingStopPct && pos.trailingStopPct > 0) conditions.push('trailing_stop');
    conditions.push('brain_signal_change');
    return conditions;
  }

  // ============================================================
  // PRIVATE: UPDATE SESSION IN DB
  // ============================================================

  private async updateSessionInDb(): Promise<void> {
    if (!this.currentSessionId) return;
    try {
      const { db } = await import('@/lib/db');
      await db.paperTradingSession.update({
        where: { id: this.currentSessionId },
        data: {
          currentCapital: this.currentCapital,
          peakCapital: this.peakCapital,
          lastScanAt: this.lastScanAt,
          totalTrades: this.tradeHistory.length,
          winningTrades: this.tradeHistory.filter(t => t.pnl > 0).length,
          totalPnlUsd: this.tradeHistory.reduce((s, t) => s + t.pnl, 0),
        },
      });
    } catch {}
  }

  // ============================================================
  // PRIVATE: CALCULATE MFE
  // ============================================================

  private calculateMFE(position: PaperPosition): number {
    if (position.entryPrice <= 0) return 0;
    // LONG: MFE = highest price above entry
    // SHORT: MFE = lowest price below entry (most favorable)
    return position.direction === 'SHORT'
      ? ((position.entryPrice - position.lowWaterMark) / position.entryPrice) * 100
      : ((position.highWaterMark - position.entryPrice) / position.entryPrice) * 100;
  }

  // ============================================================
  // PRIVATE: CALCULATE MAE
  // ============================================================

  private calculateMAE(position: PaperPosition): number {
    if (position.entryPrice <= 0) return 0;
    // LONG: MAE = lowest price below entry (adverse)
    // SHORT: MAE = highest price above entry (adverse)
    const maePct = position.direction === 'SHORT'
      ? ((position.highWaterMark - position.entryPrice) / position.entryPrice) * 100
      : ((position.entryPrice - position.lowWaterMark) / position.entryPrice) * -100;
    // MAE is always returned as a negative number (adverse excursion)
    return Math.min(0, maePct > 0 ? -maePct : maePct);
  }

  // ============================================================
  // PRIVATE: CALCULATE SHARPE RATIO
  // ============================================================

  private calculateSharpeRatio(): number {
    if (this.tradeHistory.length < 2) return 0;

    const returns = this.tradeHistory.map(t => t.pnlPct);
    const avgReturn = returns.reduce((s, r) => s + r, 0) / returns.length;
    const variance = returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / (returns.length - 1);
    const stdDev = Math.sqrt(variance);

    if (stdDev === 0) return 0;

    return (avgReturn / stdDev) * Math.sqrt(365);
  }

  // ============================================================
  // PRIVATE: SUBMIT FEEDBACK
  // ============================================================

  private async submitFeedback(record: PaperTradeRecord): Promise<void> {
    try {
      const { db } = await import('@/lib/db');

      await db.predictiveSignal.upsert({
        where: { id: record.id },
        create: {
          id: record.id,
          signalType: 'PAPER_TRADE_RESULT',
          chain: record.position.chain,
          prediction: JSON.stringify({
            tokenAddress: record.position.tokenAddress,
            symbol: record.position.symbol,
            direction: record.position.direction,
            entryPrice: record.position.entryPrice,
            exitPrice: record.exitPrice,
            entryTime: record.position.entryTime,
            exitTime: record.exitTime,
            exitReason: record.exitReason,
            pnl: record.pnl,
            pnlPct: record.pnlPct,
            holdTimeMin: record.holdTimeMin,
            mfe: record.mfe,
            mae: record.mae,
            systemName: record.position.systemName,
            brainAction: record.position.brainAnalysis?.action,
            brainOperabilityScore: record.position.brainAnalysis?.operabilityScore,
            brainPhase: record.position.brainAnalysis?.lifecyclePhase,
            brainRegime: record.position.brainAnalysis?.regime,
          }),
          confidence: (record.position.brainAnalysis?.operabilityScore || 50) / 100,
          timeframe: 'paper_trade',
          evidence: JSON.stringify({
            wasCorrect: record.pnl > 0,
            exitReason: record.exitReason,
            mfe: record.mfe,
            mae: record.mae,
          }),
          historicalHitRate: record.pnl > 0 ? 1 : 0,
          dataPointsUsed: 1,
        },
        update: {
          prediction: JSON.stringify({
            tokenAddress: record.position.tokenAddress,
            symbol: record.position.symbol,
            direction: record.position.direction,
            entryPrice: record.position.entryPrice,
            exitPrice: record.exitPrice,
            entryTime: record.position.entryTime,
            exitTime: record.exitTime,
            exitReason: record.exitReason,
            pnl: record.pnl,
            pnlPct: record.pnlPct,
            holdTimeMin: record.holdTimeMin,
            mfe: record.mfe,
            mae: record.mae,
            systemName: record.position.systemName,
          }),
          evidence: JSON.stringify({
            wasCorrect: record.pnl > 0,
            exitReason: record.exitReason,
            mfe: record.mfe,
            mae: record.mae,
          }),
          historicalHitRate: record.pnl > 0 ? 1 : 0,
          dataPointsUsed: 1,
          updatedAt: new Date(),
        },
      });

      try {
        await feedbackLoopEngine.validateSignals();
      } catch {}
    } catch (error) {
      console.warn(
        '[PaperTrading] Error almacenando feedback:',
        error instanceof Error ? error.message : String(error)
      );
    }
  }

  // ============================================================
  // PRIVATE: GET CONFIG
  // ============================================================

  getConfig(): PaperTradingConfig {
    return { ...this.config };
  }

  // ============================================================
  // PUBLIC: Get current run ID
  // ============================================================

  getCurrentRunId(): string {
    return this.currentRunId;
  }

  // ============================================================
  // PUBLIC: Get last price sync timestamp
  // ============================================================

  getLastPriceSyncAt(): Date | null {
    return this.lastPriceSyncAt;
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const paperTradingEngine = new PaperTradingEngine();
