/**
 * Brain Scheduler - CryptoQuant Terminal
 * Programador de Tareas en Background para Operación 24/7
 *
 * Este módulo gestiona la ejecución periódica de todas las tareas del cerebro:
 *
 * TAREAS PROGRAMADAS:
 * ┌──────────────────────────┬───────────────┬─────────────────────────────────────────┐
 * │ Tarea                    │ Intervalo     │ Descripción                              │
 * ├──────────────────────────┼───────────────┼─────────────────────────────────────────┤
 * │ Brain Cycle              │ 5 min         │ Ciclo completo SCAN→FILTER→MATCH→STORE   │
 * │                          │               │ →FEEDBACK→GROWTH TRACK                   │
 * │ Market Data Sync         │ 2 min         │ Sincroniza tokens desde DexScreener      │
 * │ OHLCV Backfill           │ 30 min        │ Backfill de candles para top tokens      │
 * │ Signal Validation        │ 15 min        │ Valida señales predictivas vencidas      │
 * │ System Evolution Check   │ 1 hour        │ Verifica si sistemas necesitan evolución │
 * │ Capital Strategy Update  │ 10 min        │ Actualiza estrategia de capital          │
 * │ Data Cleanup             │ 6 hours       │ Limpia datos antiguos                    │
 * └──────────────────────────┴───────────────┴─────────────────────────────────────────┘
 *
 * Uso:
 *   import { brainScheduler } from '@/lib/services/brain-scheduler';
 *   await brainScheduler.start({ capitalUsd: 100, chain: 'SOL' });
 *   await brainScheduler.stop();
 *   brainScheduler.getStatus();
 */

// Lazy-loaded heavy services to prevent OOM on import
// These are loaded dynamically only when needed
let _brainCycleEngine: typeof import('./brain-cycle-engine').brainCycleEngine | null = null;
let _feedbackLoopEngine: typeof import('./feedback-loop-engine').feedbackLoopEngine | null = null;
let _ohlcvPipeline: typeof import('./ohlcv-pipeline').ohlcvPipeline | null = null;
let _capitalStrategyManager: typeof import('./capital-strategy-manager').capitalStrategyManager | null = null;

async function getBrainCycleEngine() {
  if (!_brainCycleEngine) {
    const mod = await import('./brain-cycle-engine');
    _brainCycleEngine = mod.brainCycleEngine;
  }
  return _brainCycleEngine;
}

async function getFeedbackLoopEngine() {
  if (!_feedbackLoopEngine) {
    const mod = await import('./feedback-loop-engine');
    _feedbackLoopEngine = mod.feedbackLoopEngine;
  }
  return _feedbackLoopEngine;
}

async function getOhlcvPipeline() {
  if (!_ohlcvPipeline) {
    const mod = await import('./ohlcv-pipeline');
    _ohlcvPipeline = mod.ohlcvPipeline;
  }
  return _ohlcvPipeline;
}

async function getCapitalStrategyManager() {
  if (!_capitalStrategyManager) {
    const mod = await import('./capital-strategy-manager');
    _capitalStrategyManager = mod.capitalStrategyManager;
  }
  return _capitalStrategyManager;
}

import { db } from '../db';
import {
  loadSchedulerState,
  saveSchedulerState,
  updateTaskState,
  type PersistedSchedulerConfig,
  type PersistedTaskState,
} from './scheduler-persistence';

// ============================================================
// TYPES
// ============================================================

export interface SchedulerConfig {
  /** Capital en USD */
  capitalUsd: number;
  /** Capital inicial para tracking */
  initialCapitalUsd: number;
  /** Cadena principal */
  chain: string;
  /** Límite de tokens por scan */
  scanLimit: number;

  /** Intervalo del ciclo del cerebro (ms) - default 5 min */
  cycleIntervalMs: number;
  /** Intervalo de sincronización de mercado (ms) - default 2 min */
  marketSyncIntervalMs: number;
  /** Intervalo de backfill OHLCV (ms) - default 30 min */
  backfillIntervalMs: number;
  /** Intervalo de validación de señales (ms) - default 15 min */
  signalValidationIntervalMs: number;
  /** Intervalo de check de evolución (ms) - default 1 hour */
  evolutionCheckIntervalMs: number;
  /** Intervalo de actualización de capital (ms) - default 10 min */
  capitalUpdateIntervalMs: number;
  /** Intervalo de limpieza (ms) - default 6 hours */
  cleanupIntervalMs: number;

  /** Número de tokens para backfill por ciclo */
  backfillBatchSize: number;
  /** Auto-iniciar el ciclo del cerebro */
  autoStartBrainCycle: boolean;
  /** Auto-backfill OHLCV */
  autoBackfill: boolean;
}

export type SchedulerStatus = 'STOPPED' | 'STARTING' | 'RUNNING' | 'PAUSED' | 'ERROR';

export interface TaskStatus {
  name: string;
  intervalMs: number;
  lastRunAt: Date | null;
  nextRunAt: Date | null;
  runCount: number;
  errorCount: number;
  lastError: string | null;
  lastDurationMs: number;
  isRunning: boolean;
}

export interface SchedulerStatusReport {
  status: SchedulerStatus;
  uptime: number; // ms
  config: SchedulerConfig;
  tasks: TaskStatus[];
  brainCycle: any;
  capitalStrategy: any;
  totalCyclesCompleted: number;
  lastError: string | null;
}

// ============================================================
// DEFAULT CONFIG
// ============================================================

export const DEFAULT_SCHEDULER_CONFIG: SchedulerConfig = {
  capitalUsd: 10,
  initialCapitalUsd: 10,
  chain: 'SOL',
  scanLimit: 250,

  cycleIntervalMs: 5 * 60 * 1000,         // 5 min
  marketSyncIntervalMs: 2 * 60 * 1000,      // 2 min
  backfillIntervalMs: 30 * 60 * 1000,        // 30 min
  signalValidationIntervalMs: 15 * 60 * 1000, // 15 min
  evolutionCheckIntervalMs: 60 * 60 * 1000,  // 1 hour
  capitalUpdateIntervalMs: 10 * 60 * 1000,   // 10 min
  cleanupIntervalMs: 6 * 60 * 60 * 1000,     // 6 hours

  backfillBatchSize: 5,
  autoStartBrainCycle: true,
  autoBackfill: true,
};

// ============================================================
// SCHEDULED TASK DEFINITIONS
// ============================================================

interface ScheduledTask {
  name: string;
  intervalMs: number;
  handler: () => Promise<void>;
}

// ============================================================
// SCHEDULER EVENT TYPES
// ============================================================

type SchedulerEvent =
  | { type: 'signal_generated'; data: any }
  | { type: 'cycle_completed'; data: any }
  | { type: 'status_changed'; data: { from: string; to: string } }
  | { type: 'error'; data: { task: string; error: string } };

type SchedulerEventListener = (event: SchedulerEvent) => void;

// ============================================================
// BRAIN SCHEDULER CLASS
// ============================================================

class BrainScheduler {
  private config: SchedulerConfig = { ...DEFAULT_SCHEDULER_CONFIG };
  private status: SchedulerStatus = 'STOPPED';
  private timers: Map<string, ReturnType<typeof setInterval>> = new Map();
  private taskStatuses: Map<string, TaskStatus> = new Map();
  private startedAt: Date | null = null;
  private lastError: string | null = null;
  private consecutiveErrors = 0;
  private maxConsecutiveErrors = 10;
  private taskDefinitions: ScheduledTask[] = [];
  private listeners: SchedulerEventListener[] = [];

  // ============================================================
  // START / STOP
  // ============================================================

  /**
   * Inicia el programador de tareas del cerebro.
   * Todas las tareas se ejecutan periódicamente según la configuración.
   */
  async start(config?: Partial<SchedulerConfig>): Promise<{ started: boolean; message: string }> {
    if (this.status === 'RUNNING') {
      return { started: false, message: 'Brain scheduler is already running' };
    }

    this.status = 'STARTING';

    // Load previous state from database (resumes config, task counts, etc.)
    await this.loadState();

    // Merge config (overrides loaded state if provided)
    if (config) {
      this.config = { ...this.config, ...config };
    }

    const previousStatus = this.status;
    this.startedAt = new Date();
    this.consecutiveErrors = 0;

    // Load capital strategy learning state
    try {
      const csm = await getCapitalStrategyManager();
      await csm.loadLearningState();
    } catch (error) {
      console.warn('[BrainScheduler] Could not load capital strategy state:', error);
    }

    // Initialize task statuses
    this.initializeTaskStatuses();

    // Define and register all scheduled tasks
    const tasks: ScheduledTask[] = [
      {
        name: 'brain_cycle',
        intervalMs: this.config.cycleIntervalMs,
        handler: () => this.runBrainCycle(),
      },
      {
        name: 'market_sync',
        intervalMs: this.config.marketSyncIntervalMs,
        handler: () => this.runMarketSync(),
      },
      {
        name: 'ohlcv_backfill',
        intervalMs: this.config.backfillIntervalMs,
        handler: () => this.runOHLCVBackfill(),
      },
      {
        name: 'signal_validation',
        intervalMs: this.config.signalValidationIntervalMs,
        handler: () => this.runSignalValidation(),
      },
      {
        name: 'evolution_check',
        intervalMs: this.config.evolutionCheckIntervalMs,
        handler: () => this.runEvolutionCheck(),
      },
      {
        name: 'capital_update',
        intervalMs: this.config.capitalUpdateIntervalMs,
        handler: () => this.runCapitalUpdate(),
      },
      {
        name: 'data_cleanup',
        intervalMs: this.config.cleanupIntervalMs,
        handler: () => this.runDataCleanup(),
      },
    ];

    // Store task definitions for resume()
    this.taskDefinitions = tasks;

    // Register timers
    for (const task of tasks) {
      this.registerTask(task);
    }

    // Start brain cycle engine if auto-start is enabled
    if (this.config.autoStartBrainCycle) {
      try {
        const bce = await getBrainCycleEngine();
        await bce.start({
          capitalUsd: this.config.capitalUsd,
          initialCapitalUsd: this.config.initialCapitalUsd,
          chain: this.config.chain,
          scanLimit: this.config.scanLimit,
          cycleIntervalMs: this.config.cycleIntervalMs,
          autoFeedback: true,
          autoBackfill: this.config.autoBackfill,
        });
      } catch (error) {
        console.warn('[BrainScheduler] Brain cycle auto-start failed:', error);
      }
    }

    this.status = 'RUNNING';
    this.emitEvent({ type: 'status_changed', data: { from: previousStatus, to: 'RUNNING' } });
    import('../ws-bridge').then(({ wsBridge }) => wsBridge.pushSchedulerStatus({ status: 'RUNNING', uptime: 0 })).catch(() => {});
    console.log(`[BrainScheduler] Started with $${this.config.capitalUsd} capital, chain: ${this.config.chain}`);

    // Persist the RUNNING state to database
    await this.persistState();

    return {
      started: true,
      message: `Brain scheduler started with $${this.config.capitalUsd} capital. ${tasks.length} tasks registered.`,
    };
  }

  /**
   * Detiene el programador de tareas.
   */
  async stop(): Promise<{ stopped: boolean; message: string }> {
    if (this.status === 'STOPPED') {
      return { stopped: false, message: 'Brain scheduler is not running' };
    }

    const previousStatus = this.status;

    // Clear all timers
    for (const [name, timer] of this.timers) {
      clearInterval(timer);
      console.log(`[BrainScheduler] Stopped task: ${name}`);
    }
    this.timers.clear();

    // Stop brain cycle engine
    try {
      if (_brainCycleEngine) await _brainCycleEngine.stop();
    } catch {
      // Ignore errors on stop
    }

    this.status = 'STOPPED';
    this.emitEvent({ type: 'status_changed', data: { from: previousStatus, to: 'STOPPED' } });

    // Persist the STOPPED state to database
    await this.persistState();

    return { stopped: true, message: 'Brain scheduler stopped. All tasks cancelled.' };
  }

  /**
   * Pausa la ejecución de tareas sin detener el estado.
   */
  pause(): { paused: boolean } {
    if (this.status !== 'RUNNING') {
      return { paused: false };
    }

    const previousStatus = this.status;

    for (const [, timer] of this.timers) {
      clearInterval(timer);
    }
    this.timers.clear();

    this.status = 'PAUSED';
    this.emitEvent({ type: 'status_changed', data: { from: previousStatus, to: 'PAUSED' } });

    // Persist the PAUSED state to database
    this.persistState().catch(err => {
      console.warn('[BrainScheduler] Failed to persist state on pause:', err);
    });

    return { paused: true };
  }

  /**
   * Reanuda la ejecución después de una pausa.
   */
  resume(): { resumed: boolean } {
    if (this.status !== 'PAUSED') {
      return { resumed: false };
    }

    const previousStatus = this.status;

    // Re-register all tasks from stored definitions
    for (const task of this.taskDefinitions) {
      this.registerTask(task);
    }

    // Resume brain cycle engine too
    try {
      if (_brainCycleEngine) {
        _brainCycleEngine.start({
          capitalUsd: this.config.capitalUsd,
          initialCapitalUsd: this.config.initialCapitalUsd,
          chain: this.config.chain,
          scanLimit: this.config.scanLimit,
          cycleIntervalMs: this.config.cycleIntervalMs,
          autoFeedback: true,
          autoBackfill: this.config.autoBackfill,
        }).catch(() => {});
      }
    } catch {}

    this.status = 'RUNNING';
    this.consecutiveErrors = 0; // Reset error counter on resume
    this.emitEvent({ type: 'status_changed', data: { from: previousStatus, to: 'RUNNING' } });

    // Persist the RUNNING state to database
    this.persistState().catch(err => {
      console.warn('[BrainScheduler] Failed to persist state on resume:', err);
    });

    return { resumed: true };
  }

  // ============================================================
  // STATUS
  // ============================================================

  getStatus(): SchedulerStatusReport {
    const tasks: TaskStatus[] = [];
    for (const [, status] of this.taskStatuses) {
      tasks.push(status);
    }

    // Safe access to lazy-loaded services for status
    const brainCycleStatus = _brainCycleEngine?.getStatus() ?? { status: 'IDLE', config: {}, currentCycleNumber: 0, lastCycleResult: null, lastCapitalStrategy: null, capitalSummary: { totalCapitalUsd: 0, allocatedUsd: 0, availableUsd: 0, strategyCount: 0, mode: 'ULTRA_CONSERVATIVE' }, consecutiveErrors: 0 } as any;
    const capitalSummary = _capitalStrategyManager?.getCapitalSummary() ?? { totalCapitalUsd: 0, allocatedUsd: 0, availableUsd: 0, strategyCount: 0, mode: 'ULTRA_CONSERVATIVE' } as any;

    return {
      status: this.status,
      uptime: this.startedAt ? Date.now() - this.startedAt.getTime() : 0,
      config: this.config,
      tasks,
      brainCycle: brainCycleStatus,
      capitalStrategy: capitalSummary,
      totalCyclesCompleted: tasks.find(t => t.name === 'brain_cycle')?.runCount ?? 0,
      lastError: this.lastError,
    };
  }

  getConfig(): SchedulerConfig {
    return { ...this.config };
  }

  /**
   * Actualiza la configuración en caliente.
   * Solo ciertos parámetros se pueden cambiar sin reiniciar.
   */
  updateConfig(updates: Partial<SchedulerConfig>): void {
    this.config = { ...this.config, ...updates };

    // Update brain cycle engine config if running
    if (this.status === 'RUNNING') {
      // Capital changes are hot-swappable
      console.log(`[BrainScheduler] Config updated: capital=$${this.config.capitalUsd}, chain=${this.config.chain}`);
    }
  }

  // ============================================================
  // SCHEDULED TASK IMPLEMENTATIONS
  // ============================================================

  /**
   * TASK: Brain Cycle
   * Ejecuta un ciclo completo del cerebro: SCAN→FILTER→MATCH→STORE→FEEDBACK→GROWTH TRACK
   * Este es el corazón del sistema 24/7.
   */
  private async runBrainCycle(): Promise<void> {
    const taskName = 'brain_cycle';
    this.markTaskStart(taskName);

    try {
      const bce = await getBrainCycleEngine();
      // The brain cycle engine handles the full pipeline internally
      // We just need to ensure it's running with the right config
      const cycleStatus = bce.getStatus();

      if (cycleStatus.status !== 'RUNNING') {
        // Restart if it stopped
        await bce.start({
          capitalUsd: this.config.capitalUsd,
          initialCapitalUsd: this.config.initialCapitalUsd,
          chain: this.config.chain,
          scanLimit: this.config.scanLimit,
          cycleIntervalMs: this.config.cycleIntervalMs,
          autoFeedback: true,
          autoBackfill: this.config.autoBackfill,
        });
      }

      // Update capital from cycle results
      if (cycleStatus.lastCycleResult) {
        this.config.capitalUsd = cycleStatus.config.capitalUsd;
      }

      this.markTaskSuccess(taskName);
    } catch (error) {
      this.markTaskError(taskName, error);
    }
  }

  /**
   * TASK: Market Data Sync
   * Sincroniza tokens desde CoinGecko (PRIMARY) + DexScreener hacia la BD.
   * Esto asegura que siempre haya tokens frescos para el SCAN.
   *
   * Data source priority:
   *   1. CoinGecko - market data (prices, volumes, market caps) [FREE]
   *   2. DexScreener - DEX-specific data (pairs, pools, buy/sell) [FREE]
   */
  private async runMarketSync(): Promise<void> {
    const taskName = 'market_sync';
    this.markTaskStart(taskName);

    try {
      // Import CoinGecko client for PRIMARY market data
      const { coinGeckoClient } = await import('./coingecko-client');
      const { DataIngestionPipeline } = await import('./data-ingestion');
      const pipeline = new DataIngestionPipeline();

      // Step 1: Fetch top tokens from CoinGecko (PRIMARY - free, no API key)
      let coinGeckoUpserted = 0;
      try {
        const cgTokens = await coinGeckoClient.getTopTokens(250);

        if (cgTokens.length > 0) {
          for (const token of cgTokens) {
            try {
              // For native coins (bitcoin, ethereum, solana), use coin ID as address
              // For tokens with contract addresses, resolve them
              const address = await coinGeckoClient.resolveAddress(token.coinId, this.config.chain);
              const symbol = token.symbol || 'UNKNOWN';
              const name = token.name || symbol;
              const priceUsd = token.priceUsd ?? 0;
              const volume24h = token.volume24h ?? 0;
              const marketCap = token.marketCap ?? 0;
              const priceChange1h = token.priceChange1h ?? 0;
              const priceChange24h = token.priceChange24h ?? 0;

              if (!address) continue;

              await db.token.upsert({
                where: { address },
                create: {
                  address,
                  symbol,
                  name,
                  chain: this.config.chain,
                  priceUsd,
                  volume24h,
                  liquidity: 0,
                  marketCap,
                  priceChange5m: 0,
                  priceChange15m: 0,
                  priceChange1h,
                  priceChange24h,
                },
                update: {
                  priceUsd,
                  volume24h,
                  marketCap,
                  priceChange1h,
                  priceChange24h,
                },
              });
              coinGeckoUpserted++;
            } catch {
              // Skip tokens that fail to upsert
            }
          }
          console.log(`[BrainScheduler] CoinGecko market sync: ${coinGeckoUpserted}/${cgTokens.length} tokens updated`);
        }
      } catch (error) {
        console.warn('[BrainScheduler] CoinGecko market sync failed, falling back to DexScreener:', error);
      }

      // Step 2: Fetch top tokens from DexScreener (DEX-specific data)
      const syncResult = await pipeline.syncTokenData(this.config.chain);
      const tokens = syncResult.dexTokens;

      if (tokens.length > 0) {
        // Store/update tokens in DB
        // DexScreenerToken has: baseToken.address, baseToken.symbol, baseToken.name,
        // priceUsd (string), volume.h24, liquidity.usd, marketCap, priceChange.h24/h6/h1/m5, dexId, pairAddress
        let upserted = 0;
        for (const token of tokens) {
          try {
            const address = token.baseToken?.address || '';
            const symbol = token.baseToken?.symbol || 'UNKNOWN';
            const name = token.baseToken?.name || symbol;
            const priceUsd = parseFloat(token.priceUsd || '0');
            const volume24h = token.volume?.h24 || 0;
            const liquidity = token.liquidity?.usd || 0;
            const marketCap = token.marketCap || 0;
            const priceChange5m = token.priceChange?.m5 || 0;
            const priceChange1h = token.priceChange?.h1 || 0;
            const priceChange24h = token.priceChange?.h24 || 0;

            if (!address) continue;

            await db.token.upsert({
              where: { address },
              create: {
                address,
                symbol,
                name,
                chain: this.config.chain,
                priceUsd,
                volume24h,
                liquidity,
                marketCap,
                priceChange5m,
                priceChange15m: 0,
                priceChange1h,
                priceChange24h,
                dexId: token.dexId,
                pairAddress: token.pairAddress,
              },
              update: {
                priceUsd,
                volume24h,
                liquidity,
                marketCap,
                priceChange5m,
                priceChange1h,
                priceChange24h,
              },
            });
            upserted++;
          } catch {
            // Skip tokens that fail to upsert
          }
        }
        console.log(`[BrainScheduler] DexScreener market sync: ${upserted}/${tokens.length} tokens updated`);
      }

      this.markTaskSuccess(taskName);

      // Emit signal_generated if signals were created during sync
      if (tokens.length > 0) {
        try {
          const recentSignals = await db.predictiveSignal.findMany({
            where: {
              createdAt: { gte: new Date(Date.now() - this.config.marketSyncIntervalMs) },
            },
            orderBy: { createdAt: 'desc' },
            take: 10,
          });
          if (recentSignals.length > 0) {
            this.emitEvent({ type: 'signal_generated', data: { signals: recentSignals, source: 'market_sync' } });
          }
        } catch {
          // Ignore errors checking for signals
        }
      }
    } catch (error) {
      this.markTaskError(taskName, error);
    }
  }

  /**
   * TASK: OHLCV Backfill
   * Rellena datos históricos de candles para los top tokens.
   * El cerebro necesita datos OHLCV para detectar regímenes, fases, etc.
   */
  private async runOHLCVBackfill(): Promise<void> {
    const taskName = 'ohlcv_backfill';
    this.markTaskStart(taskName);

    try {
      if (!this.config.autoBackfill) {
        this.markTaskSuccess(taskName);
        return;
      }

      const pipeline = await getOhlcvPipeline();
      const result = await pipeline.backfillTopTokens(this.config.backfillBatchSize);
      console.log(
        `[BrainScheduler] OHLCV backfill: ${result.totalCandlesStored} candles for ${result.totalTokens} tokens` +
        (result.failedTokens.length > 0 ? ` (${result.failedTokens.length} failed)` : '')
      );

      this.markTaskSuccess(taskName);
    } catch (error) {
      this.markTaskError(taskName, error);
    }
  }

  /**
   * TASK: Signal Validation
   * Valida señales predictivas vencidas contra resultados reales.
   * Esto cierra el ciclo de aprendizaje del cerebro.
   */
  private async runSignalValidation(): Promise<void> {
    const taskName = 'signal_validation';
    this.markTaskStart(taskName);

    try {
      const fle = await getFeedbackLoopEngine();
      const report = await fle.validateSignals();

      if (report.totalValidated > 0) {
        console.log(
          `[BrainScheduler] Signal validation: ${report.totalValidated} signals validated, ` +
          `accuracy: ${(report.overallAccuracy * 100).toFixed(1)}%, ` +
          `Brier: ${report.brierScore.toFixed(4)}`
        );
      }

      this.markTaskSuccess(taskName);
    } catch (error) {
      this.markTaskError(taskName, error);
    }
  }

  /**
   * TASK: Evolution Check
   * Verifica si algún sistema de trading necesita evolución basándose
   * en el rendimiento reciente. Si un sistema tiene win rate < 40%,
   * se refina automáticamente.
   */
  private async runEvolutionCheck(): Promise<void> {
    const taskName = 'evolution_check';
    this.markTaskStart(taskName);

    try {
      // Find active systems with poor recent performance
      const activeSystems = await db.tradingSystem.findMany({
        where: { isActive: true, autoOptimize: true },
        include: {
          backtests: {
            where: { status: 'COMPLETED' },
            orderBy: { createdAt: 'desc' },
            take: 3,
          },
        },
      });

      let refined = 0;
      for (const system of activeSystems) {
        if (system.backtests.length === 0) continue;

        // Check average win rate of recent backtests
        const avgWinRate = system.backtests.reduce((s, b) => s + b.winRate, 0) / system.backtests.length;

        if (avgWinRate < 0.4) {
          try {
            const fle = await getFeedbackLoopEngine();
            await fle.refineSystem(system.id);
            refined++;
            console.log(`[BrainScheduler] Refined system ${system.name} (avg WR: ${(avgWinRate * 100).toFixed(0)}%)`);
          } catch (error) {
            console.warn(`[BrainScheduler] Failed to refine ${system.name}:`, error);
          }
        }
      }

      if (refined > 0) {
        console.log(`[BrainScheduler] Evolution check: ${refined} systems refined`);
      }

      this.markTaskSuccess(taskName);
    } catch (error) {
      this.markTaskError(taskName, error);
    }
  }

  /**
   * TASK: Capital Update
   * Actualiza la estrategia de capital basándose en el rendimiento actual.
   * Ajusta allocation %, número de estrategias, y modo de operación.
   */
  private async runCapitalUpdate(): Promise<void> {
    const taskName = 'capital_update';
    this.markTaskStart(taskName);

    try {
      // Get current state from brain cycle
      if (_brainCycleEngine) {
        const cycleStatus = _brainCycleEngine.getStatus();
        const currentCapital = cycleStatus.config.capitalUsd;
        // Update our config to stay in sync
        this.config.capitalUsd = currentCapital;
      }

      // Persist learning state
      const csm = await getCapitalStrategyManager();
      await csm.persistLearningState();

      this.markTaskSuccess(taskName);
    } catch (error) {
      this.markTaskError(taskName, error);
    }
  }

  /**
   * TASK: Data Cleanup
   * Limpia datos antiguos para mantener la BD en buen estado.
   * - OperabilitySnapshots > 7 días
   * - CompoundGrowthTracker > 30 días (keep daily summaries)
   * - PredictiveSignals vencidos y validados > 7 días
   * - PriceCandles duplicados
   */
  private async runDataCleanup(): Promise<void> {
    const taskName = 'data_cleanup';
    this.markTaskStart(taskName);

    try {
      const now = new Date();
      let totalCleaned = 0;

      // Clean old operability snapshots (> 7 days)
      const sevenDaysAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
      const deletedSnapshots = await db.operabilitySnapshot.deleteMany({
        where: { createdAt: { lt: sevenDaysAgo } },
      });
      totalCleaned += deletedSnapshots.count;

      // Clean old validated signals (> 7 days after validation)
      const deletedSignals = await db.predictiveSignal.deleteMany({
        where: {
          wasCorrect: { not: null },
          updatedAt: { lt: sevenDaysAgo },
        },
      });
      totalCleaned += deletedSignals.count;

      // Clean old growth tracker entries (> 30 days, keep 1 per day)
      const thirtyDaysAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
      const deletedGrowth = await db.compoundGrowthTracker.deleteMany({
        where: {
          measuredAt: { lt: thirtyDaysAgo },
          period: '1h', // Only clean hourly entries, keep daily
        },
      });
      totalCleaned += deletedGrowth.count;

      if (totalCleaned > 0) {
        console.log(`[BrainScheduler] Data cleanup: ${totalCleaned} records cleaned`);
      }

      this.markTaskSuccess(taskName);
    } catch (error) {
      this.markTaskError(taskName, error);
    }
  }

  // ============================================================
  // MANUAL CYCLE
  // ============================================================

  /**
   * Triggers a single brain cycle immediately, regardless of the schedule.
   * Useful for on-demand analysis triggered via the WS server.
   */
  async runManualCycle(): Promise<{ success: boolean; message: string }> {
    if (this.status === 'STOPPED') {
      return { success: false, message: 'Scheduler is not running' };
    }
    try {
      await this.runBrainCycle();
      return { success: true, message: 'Manual cycle completed' };
    } catch (error) {
      return { success: false, message: error instanceof Error ? error.message : 'Unknown error' };
    }
  }

  // ============================================================
  // EVENT EMITTER
  // ============================================================

  /**
   * Subscribe to scheduler events. Returns an unsubscribe function.
   */
  onEvent(listener: SchedulerEventListener): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  /**
   * Emit an event to all registered listeners.
   */
  private emitEvent(event: SchedulerEvent): void {
    for (const listener of this.listeners) {
      try { listener(event); } catch {}
    }
  }

  // ============================================================
  // TASK MANAGEMENT
  // ============================================================

  private initializeTaskStatuses(): void {
    const defaultTasks: Array<{ name: string; intervalMs: number }> = [
      { name: 'brain_cycle', intervalMs: this.config.cycleIntervalMs },
      { name: 'market_sync', intervalMs: this.config.marketSyncIntervalMs },
      { name: 'ohlcv_backfill', intervalMs: this.config.backfillIntervalMs },
      { name: 'signal_validation', intervalMs: this.config.signalValidationIntervalMs },
      { name: 'evolution_check', intervalMs: this.config.evolutionCheckIntervalMs },
      { name: 'capital_update', intervalMs: this.config.capitalUpdateIntervalMs },
      { name: 'data_cleanup', intervalMs: this.config.cleanupIntervalMs },
    ];

    for (const task of defaultTasks) {
      this.taskStatuses.set(task.name, {
        name: task.name,
        intervalMs: task.intervalMs,
        lastRunAt: null,
        nextRunAt: null,
        runCount: 0,
        errorCount: 0,
        lastError: null,
        lastDurationMs: 0,
        isRunning: false,
      });
    }
  }

  private registerTask(task: ScheduledTask): void {
    // Run first execution after a small staggered delay
    const initialDelay = Math.random() * 5000; // 0-5s stagger

    setTimeout(async () => {
      if (this.status !== 'RUNNING') return;
      await this.executeTaskSafely(task);
    }, initialDelay);

    // Schedule recurring execution
    const timer = setInterval(async () => {
      if (this.status !== 'RUNNING') return;
      await this.executeTaskSafely(task);
    }, task.intervalMs);

    this.timers.set(task.name, timer);

    // Update next run time
    const status = this.taskStatuses.get(task.name);
    if (status) {
      status.nextRunAt = new Date(Date.now() + initialDelay);
    }
  }

  private async executeTaskSafely(task: ScheduledTask): Promise<void> {
    try {
      await task.handler();
      this.consecutiveErrors = 0;
    } catch (error) {
      this.consecutiveErrors++;
      const errorMsg = error instanceof Error ? error.message : String(error);
      this.lastError = errorMsg;

      if (this.consecutiveErrors >= this.maxConsecutiveErrors) {
        console.error(`[BrainScheduler] Too many consecutive errors (${this.consecutiveErrors}). Setting status to ERROR.`);

        // Clear all timers
        for (const [, timer] of this.timers) {
          clearInterval(timer);
        }
        this.timers.clear();

        const previousStatus = this.status;
        this.status = 'ERROR';
        this.emitEvent({ type: 'status_changed', data: { from: previousStatus, to: 'ERROR' } });

        // Persist the ERROR state to database
        await this.persistState();
      }
    }
  }

  private markTaskStart(name: string): void {
    const status = this.taskStatuses.get(name);
    if (status) {
      status.isRunning = true;
    }
  }

  private markTaskSuccess(name: string): void {
    const status = this.taskStatuses.get(name);
    if (status) {
      const now = new Date();
      status.isRunning = false;
      status.lastRunAt = now;
      status.nextRunAt = new Date(now.getTime() + status.intervalMs);
      status.runCount++;
      status.lastDurationMs = 0; // Could track this with a start time

      // Emit cycle_completed event for brain_cycle task
      if (name === 'brain_cycle' && _brainCycleEngine) {
        const cycleStatus = _brainCycleEngine.getStatus();
        this.emitEvent({ type: 'cycle_completed', data: cycleStatus });
        // Push to WS bridge for real-time dashboard updates (non-blocking)
        import('../ws-bridge').then(({ wsBridge }) => {
          wsBridge.pushBrainCycle({
            cyclesCompleted: status.runCount,
            tokensScanned: cycleStatus.lastCycleResult?.tokensScanned ?? 0,
            signalsGenerated: cycleStatus.lastCycleResult?.topPicks?.length ?? 0,
            capitalUsd: cycleStatus.config.capitalUsd,
          });
        }).catch(() => {});
      }

      // Persist state to database after every successful task
      this.persistState().catch(err => {
        console.warn('[BrainScheduler] Failed to persist state after task success:', err);
      });
    }
  }

  private markTaskError(name: string, error: unknown): void {
    const status = this.taskStatuses.get(name);
    const errorMsg = error instanceof Error ? error.message : String(error);

    if (status) {
      status.isRunning = false;
      status.errorCount++;
      status.lastError = errorMsg;
    }

    this.lastError = errorMsg;
    this.emitEvent({ type: 'error', data: { task: name, error: errorMsg } });
    console.error(`[BrainScheduler] Task ${name} error:`, errorMsg);

    // Persist state to database after every error
    this.persistState().catch(err => {
      console.warn('[BrainScheduler] Failed to persist state after task error:', err);
    });
  }

  // ============================================================
  // STATE PERSISTENCE (Survives Restarts)
  // ============================================================

  /**
   * Save current state to database so it survives server restarts.
   * Called after every task success/error, on start, on stop, on pause, and on resume.
   * Delegates to the scheduler-persistence helper module.
   */
  private async persistState(): Promise<void> {
    try {
      const taskStatesObj: Record<string, PersistedTaskState> = {};
      for (const [name, status] of this.taskStatuses) {
        taskStatesObj[name] = {
          lastRunAt: status.lastRunAt?.toISOString() ?? null,
          nextRunAt: status.nextRunAt?.toISOString() ?? null,
          runCount: status.runCount,
          errorCount: status.errorCount,
          lastError: status.lastError,
          lastDurationMs: status.lastDurationMs,
          isRunning: status.isRunning,
        };
      }

      await saveSchedulerState({
        status: this.status,
        config: this.config,
        lastCycleNumber: this.taskStatuses.get('brain_cycle')?.runCount ?? 0,
        totalCycles: this.taskStatuses.get('brain_cycle')?.runCount ?? 0,
        capitalUsd: this.config.capitalUsd,
        initialCapitalUsd: this.config.initialCapitalUsd,
        chain: this.config.chain,
        scanLimit: this.config.scanLimit,
        startedAt: this.startedAt,
        stoppedAt: this.status === 'STOPPED' ? new Date() : null,
        lastError: this.lastError,
        taskStates: taskStatesObj,
      });
    } catch (error) {
      console.warn('[BrainScheduler] persistState failed:', error);
    }
  }

  /**
   * Load previous state from database.
   * Restores config, task run counts, error counts, and timestamps.
   * Called at the beginning of start().
   * Delegates to the scheduler-persistence helper module.
   */
  private async loadState(): Promise<void> {
    try {
      const state = await loadSchedulerState();
      if (state) {
        // Restore config from DB, merging with defaults for any missing keys
        this.config = { ...DEFAULT_SCHEDULER_CONFIG, ...state.config };

        // Restore startedAt timestamp (for continuity display)
        this.startedAt = state.startedAt;
        this.lastError = state.lastError;

        // Restore task states (run counts, error counts, etc.)
        for (const [name, ts] of Object.entries(state.taskStates)) {
          const status = this.taskStatuses.get(name);
          if (status) {
            status.runCount = ts.runCount ?? 0;
            status.errorCount = ts.errorCount ?? 0;
            status.lastError = ts.lastError ?? null;
            status.lastDurationMs = ts.lastDurationMs ?? 0;
            status.isRunning = false; // Never resume as "running" - will be re-triggered by scheduler
            // Restore lastRunAt if available
            if (ts.lastRunAt) {
              status.lastRunAt = new Date(ts.lastRunAt);
            }
          }
        }

        console.log(
          `[BrainScheduler] Loaded state from DB: status=${state.status}, ` +
          `cycles=${state.totalCycles}, capital=$${state.capitalUsd}, ` +
          `lastStarted=${state.startedAt?.toISOString() ?? 'never'}`
        );
      }
    } catch (error) {
      console.warn('[BrainScheduler] loadState failed (starting fresh):', error);
    }
  }

  /**
   * Check if the scheduler was previously running (for auto-start on server restart).
   * Returns the saved state if it was RUNNING, or null otherwise.
   * Delegates to the scheduler-persistence helper module.
   */
  async getPreviousState(): Promise<{ wasRunning: boolean; config: Partial<SchedulerConfig> | null; state: any }> {
    try {
      const state = await loadSchedulerState();
      if (state) {
        return {
          wasRunning: state.status === 'RUNNING',
          config: state.config as Partial<SchedulerConfig>,
          state,
        };
      }
    } catch (error) {
      console.warn('[BrainScheduler] getPreviousState failed:', error);
    }
    return { wasRunning: false, config: null, state: null };
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const brainScheduler = new BrainScheduler();
