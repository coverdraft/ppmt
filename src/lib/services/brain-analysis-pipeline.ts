/**
 * Brain Analysis Pipeline - CryptoQuant Terminal
 * MASTER PIPELINE: Orchestrates ALL analysis engines in the correct sequence
 *
 * This is the central integration point that wires together every engine
 * into an automated, sequential analysis pipeline. Each token flows through:
 *
 *   1. PATTERN SCAN       → CandlestickPatternEngine (multi-timeframe)
 *   2. BEHAVIORAL PREDICT → BehavioralModelEngine (archetype flow prediction)
 *   3. CROSS-CORRELATION  → CrossCorrelationEngine (conditional probability tables)
 *   4. RECORD OBSERVATION → CrossCorrelationEngine.recordObservation() (build probability tables)
 *   5. DEEP ANALYSIS      → DeepAnalysisEngine (structured reasoning with all context)
 *   6. STRATEGY SELECTION → New logic (direction, sizing, system matching)
 *   7. PREDICTION STORAGE → PredictiveSignal table (for later validation)
 *   8. COMPRESSION        → PatternCompressionPipeline (periodic, not every cycle)
 *
 * Scheduled maintenance tasks:
 *   9. OUTCOME EVALUATION  → Evaluate pending cross-correlation observations
 *  10. BEHAVIORAL UPDATE   → Update Bayesian model when outcomes are known
 *  11. SIGNAL VALIDATION   → FeedbackLoopEngine.validateSignals()
 *  12. BACKTEST LOOP       → BacktestLoopEngine (independent but startable here)
 *
 * Design principles:
 *   - Each step catches its own errors — one engine failure does NOT block others
 *   - Execution timing is tracked for every step
 *   - Caching within a cycle avoids redundant re-scans
 *   - Supports both single-token (on-demand) and batch (brain cycle) modes
 *   - Strategy selection synthesizes ALL engine outputs into a concrete trade plan
 *
 * SERVER-SIDE ONLY — no client-side imports.
 */

import { db } from '../db';
import { candlestickPatternEngine, type PatternScanResult } from './candlestick-pattern-engine';
import { crossCorrelationEngine, type CrossCorrelationResult, type ConditionKey } from './cross-correlation-engine';
import { behavioralModelEngine, type BehavioralPrediction } from './behavioral-model-engine';
import { deepAnalysisEngine, type DeepAnalysis, type DeepAnalysisResult, type AnalysisInput, type ThinkingDepth } from './deep-analysis-engine';
import { patternCompressionPipeline, type CompressionResult } from './pattern-compression-pipeline';
import { tokenLifecycleEngine, type TokenPhase, type TraderArchetype } from './token-lifecycle-engine';
import { feedbackLoopEngine, type ValidationReport } from './feedback-loop-engine';
import { backtestLoopEngine, type LoopConfig } from './backtest-loop-engine';
import { brainCapacityEngine, type CapacityReport } from './brain-capacity-engine';
import { matchSystem, type SystemRecommendation } from './trading-system-matcher';
import type { TokenAnalysis } from './brain-orchestrator';

// ============================================================
// TYPES & INTERFACES
// ============================================================

/** Result of running the full pipeline on a single token */
export interface PipelineResult {
  tokenAddress: string;
  symbol: string;
  chain: string;
  timestamp: Date;

  // Results from each step
  patternScan: PatternScanResult | null;
  behavioralPrediction: BehavioralPrediction | null;
  crossCorrelation: CrossCorrelationResult | null;
  deepAnalysis: DeepAnalysis | DeepAnalysisResult | null;

  // Strategy selection (NEW — the key output of this pipeline)
  strategy: StrategySelection | null;

  // Pipeline metadata
  executionTimeMs: number;
  stepsCompleted: number;
  stepsFailed: number;
  dataQuality: DataQualityAssessment;
}

/** The key output: which trading system, direction, sizing, and risk params to use */
export interface StrategySelection {
  // Which trading system to use
  systemCategory: string;   // ALPHA_HUNTER, SMART_MONEY, TECHNICAL, etc.
  systemName: string;        // Specific system name

  // Direction
  direction: 'LONG' | 'SHORT' | 'HOLD' | 'WAIT';

  // Entry
  entryConditions: string[];
  entryConfidence: number;   // 0-1

  // Exit
  stopLossPct: number;
  takeProfitPct: number;
  trailingStopPct?: number;
  exitConditions: string[];

  // Position sizing
  positionSizeUsd: number;
  positionSizePctOfCapital: number;
  allocationMethod: string;

  // Context that drove this selection
  reasoning: string[];

  // Data reliability
  correlationSamples: number;
  dataReliabilityLevel: 'INSUFFICIENT' | 'MINIMAL' | 'ADEQUATE' | 'OPTIMAL';

  // Phase-specific
  tokenPhase: string;
  dominantPattern: string | null;
  dominantTraderArchetype: string;
  netBehaviorFlow: string;
}

/** Assessment of data quality for a given token */
export interface DataQualityAssessment {
  hasOHLCV: boolean;
  candles5m: number;
  candles1h: number;
  tradersAnalyzed: number;
  correlationSamples: number;
  lifecycleConfidence: number;
  overallQuality: 'POOR' | 'FAIR' | 'GOOD' | 'EXCELLENT';
}

/** Result of running the pipeline across multiple tokens */
export interface PipelineBatchResult {
  totalTokens: number;
  successfulAnalyses: number;
  failedAnalyses: number;
  predictionsGenerated: number;
  observationsRecorded: number;
  totalExecutionTimeMs: number;
  results: PipelineResult[];
}

/** Step execution timing for diagnostics */
interface StepTiming {
  step: string;
  durationMs: number;
  success: boolean;
  error?: string;
}

/** Configuration for the pipeline */
export interface PipelineConfig {
  /** Default thinking depth for deep analysis (default: STANDARD) */
  defaultThinkingDepth: ThinkingDepth;
  /** Whether to automatically record observations (default: true) */
  autoRecordObservations: boolean;
  /** Whether to run pattern compression after batch (default: false — run periodically) */
  runCompressionAfterBatch: boolean;
  /** Minimum confidence to store a prediction (default: 0.1) */
  minConfidenceToStore: number;
  /** Default capital in USD for position sizing (default: 100) */
  defaultCapitalUsd: number;
  /** Maximum position as % of capital (default: 10) */
  maxPositionPctOfCapital: number;
  /** How many correlation samples are needed for ADEQUATE reliability (default: 30) */
  adequateCorrelationSamples: number;
  /** How many correlation samples are needed for OPTIMAL reliability (default: 100) */
  optimalCorrelationSamples: number;
  /** Cache TTL in ms — skip re-scanning tokens analyzed within this window (default: 300000 = 5 min) */
  cacheTtlMs: number;
  /** Interval in cycles between pattern compression runs (default: 12 — every ~1h at 5 min cycles) */
  compressionIntervalCycles: number;
  /** Interval in cycles between signal validation runs (default: 3 — every ~15 min at 5 min cycles) */
  signalValidationIntervalCycles: number;
  /** Interval in cycles between behavioral model updates (default: 6 — every ~30 min at 5 min cycles) */
  behavioralUpdateIntervalCycles: number;
}

/** Default pipeline configuration */
export const DEFAULT_PIPELINE_CONFIG: PipelineConfig = {
  defaultThinkingDepth: 'STANDARD',
  autoRecordObservations: true,
  runCompressionAfterBatch: false,
  minConfidenceToStore: 0.1,
  defaultCapitalUsd: 100,
  maxPositionPctOfCapital: 10,
  adequateCorrelationSamples: 30,
  optimalCorrelationSamples: 100,
  cacheTtlMs: 5 * 60 * 1000,         // 5 min
  compressionIntervalCycles: 12,       // ~1h at 5 min cycles
  signalValidationIntervalCycles: 3,    // ~15 min
  behavioralUpdateIntervalCycles: 6,    // ~30 min
};

// ============================================================
// PHASE-TO-SYSTEM MAPPING
// ============================================================

/**
 * Maps token lifecycle phase to the best trading system category.
 * This drives the strategy selection step.
 */
const PHASE_SYSTEM_MAP: Record<TokenPhase, {
  category: string;
  systemName: string;
  defaultDirection: 'LONG' | 'SHORT' | 'HOLD' | 'WAIT';
  stopLossPct: number;
  takeProfitPct: number;
  trailingStopPct: number;
  positionPctOfCapital: number;
}> = {
  GENESIS: {
    category: 'ALPHA_HUNTER',
    systemName: 'alpha-hunter',
    defaultDirection: 'WAIT',
    stopLossPct: 30,
    takeProfitPct: 200,
    trailingStopPct: 20,
    positionPctOfCapital: 1,
  },
  INCIPIENT: {
    category: 'ALPHA_HUNTER',
    systemName: 'alpha-hunter',
    defaultDirection: 'HOLD',
    stopLossPct: 20,
    takeProfitPct: 100,
    trailingStopPct: 15,
    positionPctOfCapital: 2,
  },
  GROWTH: {
    category: 'SMART_MONEY',
    systemName: 'smart-money',
    defaultDirection: 'LONG',
    stopLossPct: 12,
    takeProfitPct: 40,
    trailingStopPct: 10,
    positionPctOfCapital: 5,
  },
  FOMO: {
    category: 'BOT_AWARE',
    systemName: 'bot-aware',
    defaultDirection: 'HOLD',
    stopLossPct: 8,
    takeProfitPct: 20,
    trailingStopPct: 8,
    positionPctOfCapital: 3,
  },
  DECLINE: {
    category: 'DEFENSIVE',
    systemName: 'defensive',
    defaultDirection: 'WAIT',
    stopLossPct: 5,
    takeProfitPct: 10,
    trailingStopPct: 5,
    positionPctOfCapital: 1,
  },
  LEGACY: {
    category: 'TECHNICAL',
    systemName: 'technical',
    defaultDirection: 'HOLD',
    stopLossPct: 5,
    takeProfitPct: 8,
    trailingStopPct: 3,
    positionPctOfCapital: 7,
  },
};

// ============================================================
// BRAIN ANALYSIS PIPELINE CLASS
// ============================================================

class BrainAnalysisPipeline {
  private config: PipelineConfig = { ...DEFAULT_PIPELINE_CONFIG };
  private cycleCount = 0;
  private lastCompressionCycle = 0;
  private lastSignalValidationCycle = 0;
  private lastBehavioralUpdateCycle = 0;

  /** Cache of recent pipeline results to avoid re-scanning unchanged tokens */
  private resultCache = new Map<string, { result: PipelineResult; timestamp: number }>();

  /** Aggregated metrics since the pipeline was created */
  private metrics = {
    totalTokensAnalyzed: 0,
    totalPredictionsGenerated: 0,
    totalObservationsRecorded: 0,
    totalStepsCompleted: 0,
    totalStepsFailed: 0,
    totalExecutionTimeMs: 0,
    lastBatchTimestamp: null as Date | null,
  };

  // ============================================================
  // SINGLE-TOKEN PIPELINE
  // ============================================================

  /**
   * Run the full analysis pipeline for a single token.
   *
   * This is the core method — each step is executed sequentially with
   * independent error handling so one failure doesn't cascade.
   *
   * @param tokenAddress - Token address on-chain
   * @param chain - Blockchain (default: SOL)
   * @param options - Optional overrides for this run
   * @returns Complete pipeline result with all engine outputs + strategy
   */
  async analyzeToken(
    tokenAddress: string,
    chain: string = 'SOL',
    options?: {
      thinkingDepth?: ThinkingDepth;
      forceRescan?: boolean;
      capitalUsd?: number;
    }
  ): Promise<PipelineResult> {
    const startTime = Date.now();
    const stepTimings: StepTiming[] = [];
    let stepsCompleted = 0;
    let stepsFailed = 0;

    // Check cache — skip if analyzed recently and not forced
    if (!options?.forceRescan) {
      const cached = this.resultCache.get(tokenAddress);
      if (cached && Date.now() - cached.timestamp < this.config.cacheTtlMs) {
        return cached.result;
      }
    }

    // Load token info from DB
    const token = await db.token.findUnique({
      where: { address: tokenAddress },
      include: { dna: true },
    });

    const symbol = token?.symbol ?? 'UNKNOWN';
    const currentPrice = token?.priceUsd ?? 0;
    const priceChange24h = token?.priceChange24h ?? 0;

    // Initialize result
    const result: PipelineResult = {
      tokenAddress,
      symbol,
      chain,
      timestamp: new Date(),
      patternScan: null,
      behavioralPrediction: null,
      crossCorrelation: null,
      deepAnalysis: null,
      strategy: null,
      executionTimeMs: 0,
      stepsCompleted: 0,
      stepsFailed: 0,
      dataQuality: {
        hasOHLCV: false,
        candles5m: 0,
        candles1h: 0,
        tradersAnalyzed: 0,
        correlationSamples: 0,
        lifecycleConfidence: 0,
        overallQuality: 'POOR',
      },
    };

    // ─────────────────────────────────────────────────────────
    // STEP 1: PATTERN SCAN
    // Multi-timeframe candlestick pattern detection
    // ─────────────────────────────────────────────────────────
    try {
      const stepStart = Date.now();
      result.patternScan = await candlestickPatternEngine.scanMultiTimeframe(tokenAddress, chain);
      stepTimings.push({ step: 'PATTERN_SCAN', durationMs: Date.now() - stepStart, success: true });
      stepsCompleted++;
    } catch (error) {
      stepTimings.push({
        step: 'PATTERN_SCAN',
        durationMs: 0,
        success: false,
        error: error instanceof Error ? error.message : String(error),
      });
      stepsFailed++;
    }

    // ─────────────────────────────────────────────────────────
    // STEP 2: BEHAVIORAL PREDICTION
    // Predict aggregated trader behavior from archetype matrices
    // ─────────────────────────────────────────────────────────
    try {
      const stepStart = Date.now();
      result.behavioralPrediction = await behavioralModelEngine.predictBehavior(tokenAddress, chain);
      stepTimings.push({ step: 'BEHAVIORAL_PREDICTION', durationMs: Date.now() - stepStart, success: true });
      stepsCompleted++;
    } catch (error) {
      stepTimings.push({
        step: 'BEHAVIORAL_PREDICTION',
        durationMs: 0,
        success: false,
        error: error instanceof Error ? error.message : String(error),
      });
      stepsFailed++;
    }

    // ─────────────────────────────────────────────────────────
    // STEP 3: CROSS-CORRELATION ANALYSIS
    // Build conditional probability tables from historical observations
    // ─────────────────────────────────────────────────────────
    try {
      const stepStart = Date.now();
      result.crossCorrelation = await crossCorrelationEngine.analyzeCrossCorrelation(tokenAddress, chain);
      stepTimings.push({ step: 'CROSS_CORRELATION', durationMs: Date.now() - stepStart, success: true });
      stepsCompleted++;
    } catch (error) {
      stepTimings.push({
        step: 'CROSS_CORRELATION',
        durationMs: 0,
        success: false,
        error: error instanceof Error ? error.message : String(error),
      });
      stepsFailed++;
    }

    // ─────────────────────────────────────────────────────────
    // STEP 4: RECORD OBSERVATION
    // Automatically record current conditions so probability tables build up
    // This is how the system LEARNS — every cycle records the current state
    // for later evaluation when the outcome is known.
    // ─────────────────────────────────────────────────────────
    let observationRecorded = false;
    if (this.config.autoRecordObservations && currentPrice > 0) {
      try {
        const stepStart = Date.now();
        const conditions = this.buildObservationConditions(result);
        if (conditions) {
          const obsId = await crossCorrelationEngine.recordObservationPipeline(
            tokenAddress,
            chain,
            conditions,
            currentPrice
          );
          observationRecorded = obsId !== null;
        }
        stepTimings.push({ step: 'RECORD_OBSERVATION', durationMs: Date.now() - stepStart, success: true });
        stepsCompleted++;
      } catch (error) {
        stepTimings.push({
          step: 'RECORD_OBSERVATION',
          durationMs: 0,
          success: false,
          error: error instanceof Error ? error.message : String(error),
        });
        stepsFailed++;
      }
    }

    // ─────────────────────────────────────────────────────────
    // STEP 5: DEEP ANALYSIS
    // Synthesize all engine outputs into a structured reasoning chain
    // ─────────────────────────────────────────────────────────
    try {
      const stepStart = Date.now();

      // Get lifecycle phase for context
      let lifecyclePhase: TokenPhase = 'INCIPIENT';
      let lifecycleConfidence = 0;
      try {
        const phaseResult = await tokenLifecycleEngine.detectPhase(tokenAddress, chain);
        lifecyclePhase = phaseResult.phase;
        lifecycleConfidence = phaseResult.probability;
      } catch {
        // Use defaults
      }

      // Count candles for data reliability
      const [candles5m, candles1h] = await Promise.all([
        db.priceCandle.count({ where: { tokenAddress, timeframe: '5m' } }),
        db.priceCandle.count({ where: { tokenAddress, timeframe: '1h' } }),
      ]);

      // Count traders analyzed
      const tradersAnalyzed = await db.trader.count({
        where: {
          transactions: {
            some: { tokenAddress },
          },
        },
      });

      // Get correlation stats for data reliability
      let totalCorrelationSamples = 0;
      let reliableCombinations = 0;
      try {
        const stats = await crossCorrelationEngine.getCorrelationStats();
        totalCorrelationSamples = stats.totalObservations;
        reliableCombinations = stats.reliableCombinations;
      } catch {
        // Use defaults
      }

      // Determine data reliability
      const dataReliability = this.assessDataReliability(
        totalCorrelationSamples,
        result.crossCorrelation
      );

      // Build the AnalysisInput required by DeepAnalysisEngine
      const analysisInput: AnalysisInput = {
        tokenAddress,
        symbol,
        chain,
        currentPrice,
        priceChange24h,
        regime: this.inferRegime(result),
        regimeConfidence: this.inferRegimeConfidence(result),
        lifecyclePhase,
        lifecycleConfidence,
        netBehaviorFlow: result.behavioralPrediction?.netFlowDirection ?? 'NEUTRAL',
        botSwarmLevel: this.inferBotSwarmLevel(token),
        whaleDirection: this.inferWhaleDirection(token),
        operabilityScore: this.inferOperabilityScore(token),
        patternScan: result.patternScan ?? undefined,
        crossCorrelation: result.crossCorrelation as any ?? undefined,
        dataReliability,
        candles1h,
        candles5m,
        tradersAnalyzed,
        signalsGenerated: 0, // Will be updated after storage
      };

      const thinkingDepth = options?.thinkingDepth ?? this.config.defaultThinkingDepth;
      result.deepAnalysis = await deepAnalysisEngine.analyze(analysisInput, thinkingDepth);

      // Update data quality assessment
      result.dataQuality = {
        hasOHLCV: candles1h > 0 || candles5m > 0,
        candles5m,
        candles1h,
        tradersAnalyzed,
        correlationSamples: totalCorrelationSamples,
        lifecycleConfidence,
        overallQuality: this.computeOverallQuality(candles1h, candles5m, tradersAnalyzed, totalCorrelationSamples, lifecycleConfidence),
      };

      stepTimings.push({ step: 'DEEP_ANALYSIS', durationMs: Date.now() - stepStart, success: true });
      stepsCompleted++;
    } catch (error) {
      stepTimings.push({
        step: 'DEEP_ANALYSIS',
        durationMs: 0,
        success: false,
        error: error instanceof Error ? error.message : String(error),
      });
      stepsFailed++;
    }

    // ─────────────────────────────────────────────────────────
    // STEP 6: STRATEGY SELECTION
    // Synthesize all outputs into a concrete trade strategy
    // This is the KEY OUTPUT of the pipeline — it tells you WHAT to do.
    // ─────────────────────────────────────────────────────────
    try {
      const stepStart = Date.now();
      result.strategy = this.selectStrategy(result, options?.capitalUsd);
      stepTimings.push({ step: 'STRATEGY_SELECTION', durationMs: Date.now() - stepStart, success: true });
      stepsCompleted++;
    } catch (error) {
      stepTimings.push({
        step: 'STRATEGY_SELECTION',
        durationMs: 0,
        success: false,
        error: error instanceof Error ? error.message : String(error),
      });
      stepsFailed++;
    }

    // ─────────────────────────────────────────────────────────
    // STEP 7: PREDICTION STORAGE
    // Store the prediction in PredictiveSignal for later validation
    // ─────────────────────────────────────────────────────────
    try {
      const stepStart = Date.now();
      await this.storePrediction(result);
      stepTimings.push({ step: 'PREDICTION_STORAGE', durationMs: Date.now() - stepStart, success: true });
      stepsCompleted++;
    } catch (error) {
      stepTimings.push({
        step: 'PREDICTION_STORAGE',
        durationMs: 0,
        success: false,
        error: error instanceof Error ? error.message : String(error),
      });
      stepsFailed++;
    }

    // Finalize result
    result.executionTimeMs = Date.now() - startTime;
    result.stepsCompleted = stepsCompleted;
    result.stepsFailed = stepsFailed;

    // Update cache
    this.resultCache.set(tokenAddress, { result, timestamp: Date.now() });

    // Update metrics
    this.metrics.totalTokensAnalyzed++;
    this.metrics.totalStepsCompleted += stepsCompleted;
    this.metrics.totalStepsFailed += stepsFailed;
    this.metrics.totalExecutionTimeMs += result.executionTimeMs;
    if (result.strategy && result.strategy.direction !== 'WAIT') {
      this.metrics.totalPredictionsGenerated++;
    }
    if (observationRecorded) {
      this.metrics.totalObservationsRecorded++;
    }

    return result;
  }

  // ============================================================
  // BATCH PIPELINE
  // ============================================================

  /**
   * Run the analysis pipeline for all operable tokens (batch mode).
   * Used during brain cycles by the BrainScheduler.
   *
   * @param tokenAddresses - Array of token addresses to analyze
   * @param chain - Blockchain (default: SOL)
   * @param options - Batch options
   * @returns Batch result with aggregate metrics
   */
  async analyzeBatch(
    tokenAddresses: string[],
    chain: string = 'SOL',
    options?: {
      thinkingDepth?: ThinkingDepth;
      forceRescan?: boolean;
      capitalUsd?: number;
      maxConcurrent?: number;
    }
  ): Promise<PipelineBatchResult> {
    const batchStart = Date.now();
    const results: PipelineResult[] = [];
    let predictionsGenerated = 0;
    let observationsRecorded = 0;

    // Process tokens with concurrency control
    const maxConcurrent = options?.maxConcurrent ?? 3;
    const queue = [...tokenAddresses];

    while (queue.length > 0) {
      const batch = queue.splice(0, maxConcurrent);
      const batchResults = await Promise.allSettled(
        batch.map(addr =>
          this.analyzeToken(addr, chain, {
            thinkingDepth: options?.thinkingDepth,
            forceRescan: options?.forceRescan,
            capitalUsd: options?.capitalUsd,
          })
        )
      );

      for (const settled of batchResults) {
        if (settled.status === 'fulfilled') {
          const r = settled.value;
          results.push(r);
          if (r.strategy && r.strategy.direction !== 'WAIT') {
            predictionsGenerated++;
          }
          // Count observations (approximate — if step 4 ran without error)
          if (r.stepsCompleted >= 4) {
            observationsRecorded++;
          }
        }
      }

      // Small delay between batches to reduce memory pressure
      if (queue.length > 0) {
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }

    const totalExecutionTimeMs = Date.now() - batchStart;

    // Increment cycle counter for scheduled maintenance tasks
    this.cycleCount++;

    // Run periodic maintenance tasks
    await this.runScheduledMaintenance();

    // Update batch metrics
    this.metrics.lastBatchTimestamp = new Date();

    return {
      totalTokens: tokenAddresses.length,
      successfulAnalyses: results.filter(r => r.stepsFailed === 0).length,
      failedAnalyses: results.filter(r => r.stepsFailed > 0).length,
      predictionsGenerated,
      observationsRecorded,
      totalExecutionTimeMs,
      results,
    };
  }

  // ============================================================
  // ON-DEMAND DEEP ANALYSIS
  // ============================================================

  /**
   * Run a deep, on-demand analysis for a single token.
   * This is what the UI calls when a user clicks "Deep Analyze".
   * Uses DEEP thinking depth and forces a rescan.
   */
  async deepAnalyzeToken(
    tokenAddress: string,
    chain: string = 'SOL',
    capitalUsd?: number
  ): Promise<PipelineResult> {
    return this.analyzeToken(tokenAddress, chain, {
      thinkingDepth: 'DEEP',
      forceRescan: true,
      capitalUsd,
    });
  }

  // ============================================================
  // SCHEDULED MAINTENANCE
  // ============================================================

  /**
   * Run periodic maintenance tasks based on cycle count.
   * Called automatically after each batch run.
   */
  private async runScheduledMaintenance(): Promise<void> {
    // STEP 9: Outcome Evaluation
    // Every cycle, evaluate pending observations whose time window has expired
    try {
      await crossCorrelationEngine.evaluatePendingObservations();
    } catch {
      // Non-critical — evaluation will retry next cycle
    }

    // STEP 10: Behavioral Model Update
    // When outcomes are known, update the Bayesian behavioral model
    if (this.cycleCount - this.lastBehavioralUpdateCycle >= this.config.behavioralUpdateIntervalCycles) {
      try {
        await this.updateBehavioralModels();
        this.lastBehavioralUpdateCycle = this.cycleCount;
      } catch {
        // Non-critical
      }
    }

    // STEP 11: Signal Validation
    // Periodically validate past predictions against actual outcomes
    if (this.cycleCount - this.lastSignalValidationCycle >= this.config.signalValidationIntervalCycles) {
      try {
        await feedbackLoopEngine.validateSignals();
        this.lastSignalValidationCycle = this.cycleCount;
      } catch {
        // Non-critical
      }
    }

    // STEP 8: Pattern Compression
    // Run compression periodically (not every cycle)
    if (this.cycleCount - this.lastCompressionCycle >= this.config.compressionIntervalCycles) {
      try {
        await patternCompressionPipeline.runCompression();
        this.lastCompressionCycle = this.cycleCount;
      } catch {
        // Non-critical
      }
    }

    // Clean up old cache entries (older than 2x TTL)
    const cacheExpiry = Date.now() - this.config.cacheTtlMs * 2;
    for (const [key, entry] of this.resultCache) {
      if (entry.timestamp < cacheExpiry) {
        this.resultCache.delete(key);
      }
    }
  }

  // ============================================================
  // BACKTEST LOOP CONTROL
  // ============================================================

  /**
   * Start the backtest loop engine.
   * The loop runs independently but can be triggered from the pipeline.
   */
  async startBacktestLoop(config?: Partial<LoopConfig>): Promise<{ started: boolean; message: string }> {
    return backtestLoopEngine.start(config);
  }

  /**
   * Stop the backtest loop engine.
   */
  stopBacktestLoop(): { stopped: boolean; message: string } {
    return backtestLoopEngine.stop();
  }

  // ============================================================
  // STRATEGY SELECTION (STEP 6)
  // ============================================================

  /**
   * Select the optimal trading strategy based on all engine outputs.
   *
   * This method synthesizes:
   * - Pattern scan → sentiment, dominant pattern
   * - Behavioral prediction → flow direction, archetype breakdown
   * - Cross-correlation → conditional probabilities, historical win rates
   * - Deep analysis → structured pros/cons, risk assessment, verdict
   *
   * And produces a concrete StrategySelection with:
   * - Direction (LONG/SHORT/HOLD/WAIT)
   * - System category and name
   * - Entry/exit conditions
   * - Position sizing based on operability + confidence + data reliability
   */
  private selectStrategy(
    result: PipelineResult,
    capitalUsd?: number
  ): StrategySelection | null {
    const capital = capitalUsd ?? this.config.defaultCapitalUsd;

    // Extract key signals from each engine
    const phase = (result.deepAnalysis as any)?.phaseAssessment?.phase
      ?? (result.behavioralPrediction?.phase as TokenPhase | undefined)
      ?? 'INCIPIENT';

    const patternSentiment = (result.patternScan as any)?.overallSentiment ?? result.patternScan?.overallSignal ?? 'NEUTRAL';
    const patternScore = (result.patternScan as any)?.sentimentScore ?? result.patternScan?.overallScore ?? 0;
    const dominantPattern = (result.patternScan as any)?.dominantPattern?.patternName ?? result.patternScan?.dominantPattern ?? null;
    const multiTfConfirmed = (result.patternScan as any)?.dominantPattern?.multiTfConfirmation ?? (result.patternScan?.confluences?.length ?? 0 > 0);

    const behaviorFlow = result.behavioralPrediction?.netFlowDirection ?? 'NEUTRAL';
    const behaviorScore = result.behavioralPrediction?.netFlowScore ?? 0;
    const behaviorConfidence = result.behavioralPrediction?.confidence ?? 0;
    const dominantArchetype = result.behavioralPrediction?.archetypeBreakdown?.[0]?.archetype ?? 'RETAIL_FOMO';

    const ccDirection = (result.crossCorrelation as any)?.overallAssessment?.direction ?? 'NEUTRAL';
    const ccConfidence = (result.crossCorrelation as any)?.overallAssessment?.confidence ?? 0;
    const ccStrength = (result.crossCorrelation as any)?.overallAssessment?.strength ?? 0;
    const ccWinRate = (result.crossCorrelation as any)?.bestStrategy?.expectedWinRate ?? 0;
    const ccSamples = (result.crossCorrelation as any)?.bestStrategy?.sampleSize ?? 0;

    const verdict = (result.deepAnalysis as any)?.verdict?.action ?? 'HOLD';
    const verdictConfidence = (result.deepAnalysis as any)?.verdict?.confidence ?? 0;
    const riskLevel = (result.deepAnalysis as any)?.riskAssessment?.overallRisk ?? 'MEDIUM';

    // ── Determine direction ──
    const direction = this.determineDirection(
      patternSentiment,
      patternScore,
      behaviorFlow,
      behaviorScore,
      ccDirection,
      ccStrength,
      verdict,
      riskLevel
    );

    // ── Get phase-based system template ──
    const phaseConfig = PHASE_SYSTEM_MAP[phase as TokenPhase] ?? PHASE_SYSTEM_MAP.INCIPIENT;

    // ── Override system based on cross-correlation best strategy ──
    let systemCategory = phaseConfig.category;
    let systemName = phaseConfig.systemName;

    // If cross-correlation suggests a specific strategy, consider overriding
    if (ccConfidence > 0.5 && ccSamples >= this.config.adequateCorrelationSamples) {
      const ccStrategy = (result.crossCorrelation as any)?.bestStrategy?.strategy;
      if (ccStrategy === 'LONG' || ccStrategy === 'SHORT') {
        // Cross-correlation has enough samples — use smart-money system
        systemCategory = 'SMART_MONEY';
        systemName = 'smart-money';
      }
    }

    // If bot activity is high, switch to bot-aware system
    const botSwarmLevel = (result.deepAnalysis as any)?.traderAssessment?.riskFromBots;
    if (botSwarmLevel === 'HIGH' || botSwarmLevel === 'CRITICAL') {
      systemCategory = 'BOT_AWARE';
      systemName = 'bot-aware';
    }

    // ── Compute entry confidence ──
    const entryConfidence = this.computeEntryConfidence(
      patternScore,
      behaviorScore,
      behaviorConfidence,
      ccConfidence,
      ccWinRate,
      verdictConfidence,
      multiTfConfirmed
    );

    // ── Compute position sizing ──
    const positionPctOfCapital = this.computePositionSizing(
      phaseConfig.positionPctOfCapital,
      entryConfidence,
      riskLevel,
      result.dataQuality.overallQuality,
      capital
    );
    const positionSizeUsd = Math.round(capital * (positionPctOfCapital / 100) * 100) / 100;

    // ── Build entry conditions ──
    const entryConditions = this.buildEntryConditions(
      direction,
      phase,
      dominantPattern,
      behaviorFlow,
      ccDirection,
      multiTfConfirmed
    );

    // ── Build exit conditions ──
    const exitConditions = this.buildExitConditions(
      direction,
      phase,
      riskLevel
    );

    // ── Determine data reliability level ──
    const correlationSamples = ccSamples;
    const dataReliabilityLevel = this.getDataReliabilityLevel(correlationSamples);

    // ── Build reasoning chain ──
    const reasoning = this.buildReasoningChain(
      phase,
      direction,
      patternSentiment,
      dominantPattern,
      behaviorFlow,
      dominantArchetype,
      ccDirection,
      ccConfidence,
      ccSamples,
      verdict,
      riskLevel,
      entryConfidence
    );

    return {
      systemCategory,
      systemName,
      direction,
      entryConditions,
      entryConfidence,
      stopLossPct: phaseConfig.stopLossPct,
      takeProfitPct: phaseConfig.takeProfitPct,
      trailingStopPct: phaseConfig.trailingStopPct,
      exitConditions,
      positionSizeUsd,
      positionSizePctOfCapital: positionPctOfCapital,
      allocationMethod: this.selectAllocationMethod(direction, capital),
      reasoning,
      correlationSamples,
      dataReliabilityLevel,
      tokenPhase: phase,
      dominantPattern,
      dominantTraderArchetype: dominantArchetype as string,
      netBehaviorFlow: behaviorFlow,
    };
  }

  // ============================================================
  // STRATEGY HELPER METHODS
  // ============================================================

  /**
   * Determine trade direction based on all signals.
   * Uses a weighted voting system: patterns (30%), behavior (30%),
   * cross-correlation (25%), deep analysis verdict (15%).
   */
  private determineDirection(
    patternSentiment: string,
    patternScore: number,
    behaviorFlow: string,
    behaviorScore: number,
    ccDirection: string,
    ccStrength: number,
    verdict: string,
    riskLevel: string
  ): 'LONG' | 'SHORT' | 'HOLD' | 'WAIT' {
    // If extreme risk, always WAIT
    if (riskLevel === 'EXTREME') return 'WAIT';

    let bullishScore = 0;
    let bearishScore = 0;

    // Pattern signals (weight: 0.30)
    if (patternSentiment === 'BULLISH') bullishScore += 0.30 * Math.abs(patternScore);
    else if (patternSentiment === 'BEARISH') bearishScore += 0.30 * Math.abs(patternScore);

    // Behavioral signals (weight: 0.30)
    if (behaviorFlow === 'BULLISH') bullishScore += 0.30 * Math.max(0, behaviorScore);
    else if (behaviorFlow === 'BEARISH') bearishScore += 0.30 * Math.max(0, Math.abs(behaviorScore));

    // Cross-correlation signals (weight: 0.25)
    if (ccDirection === 'BULLISH') bullishScore += 0.25 * Math.abs(ccStrength);
    else if (ccDirection === 'BEARISH') bearishScore += 0.25 * Math.abs(ccStrength);

    // Deep analysis verdict (weight: 0.15)
    if (['STRONG_BUY', 'BUY'].includes(verdict)) bullishScore += 0.15;
    else if (['STRONG_SELL', 'SELL'].includes(verdict)) bearishScore += 0.15;

    const netScore = bullishScore - bearishScore;

    // Determine direction with thresholds
    if (netScore > 0.20) return 'LONG';
    if (netScore < -0.20) return 'SHORT';
    if (netScore > 0.05 || netScore < -0.05) return 'HOLD';
    return 'WAIT';
  }

  /**
   * Compute entry confidence as a composite of all signal confidences.
   */
  private computeEntryConfidence(
    patternScore: number,
    behaviorScore: number,
    behaviorConfidence: number,
    ccConfidence: number,
    ccWinRate: number,
    verdictConfidence: number,
    multiTfConfirmed: boolean
  ): number {
    let confidence = 0;

    // Pattern component (0-0.25)
    confidence += Math.min(0.25, Math.abs(patternScore) * 0.25);

    // Behavior component (0-0.25)
    confidence += Math.min(0.25, behaviorConfidence * 0.25);

    // Cross-correlation component (0-0.25)
    confidence += Math.min(0.25, ccConfidence * 0.15 + ccWinRate * 0.10);

    // Deep analysis component (0-0.25)
    confidence += Math.min(0.25, verdictConfidence * 0.25);

    // Multi-timeframe confirmation bonus
    if (multiTfConfirmed) {
      confidence = Math.min(1, confidence * 1.15);
    }

    return Math.max(0, Math.min(1, confidence));
  }

  /**
   * Compute position sizing based on phase defaults, confidence, and risk.
   * Higher confidence → larger position. Higher risk → smaller position.
   */
  private computePositionSizing(
    basePct: number,
    entryConfidence: number,
    riskLevel: string,
    dataQuality: string,
    capital: number
  ): number {
    let pct = basePct;

    // Scale by entry confidence (0.5x at confidence=0, 1.5x at confidence=1)
    pct *= (0.5 + entryConfidence);

    // Reduce for high risk
    const riskMultiplier: Record<string, number> = {
      VERY_LOW: 1.0,
      LOW: 0.9,
      MEDIUM: 0.7,
      HIGH: 0.4,
      EXTREME: 0.1,
    };
    pct *= riskMultiplier[riskLevel] ?? 0.5;

    // Reduce for poor data quality
    const qualityMultiplier: Record<string, number> = {
      EXCELLENT: 1.0,
      GOOD: 0.9,
      FAIR: 0.7,
      POOR: 0.4,
    };
    pct *= qualityMultiplier[dataQuality] ?? 0.5;

    // Cap at max position pct
    pct = Math.min(pct, this.config.maxPositionPctOfCapital);

    // Ensure minimum meaningful position ($1)
    const minPct = capital > 0 ? (1 / capital) * 100 : 0;
    pct = Math.max(minPct, pct);

    return Math.round(pct * 100) / 100;
  }

  /**
   * Build entry conditions based on the analysis signals.
   */
  private buildEntryConditions(
    direction: string,
    phase: string,
    dominantPattern: string | null,
    behaviorFlow: string,
    ccDirection: string,
    multiTfConfirmed: boolean
  ): string[] {
    const conditions: string[] = [];

    if (direction === 'LONG' || direction === 'SHORT') {
      conditions.push(`${direction === 'LONG' ? 'Bullish' : 'Bearish'} signal composite positive`);

      if (dominantPattern) {
        conditions.push(`Pattern: ${dominantPattern}${multiTfConfirmed ? ' (multi-TF confirmed)' : ''}`);
      }

      if (behaviorFlow !== 'NEUTRAL') {
        conditions.push(`Trader flow: ${behaviorFlow}`);
      }

      if (ccDirection !== 'NEUTRAL' && ccDirection === direction) {
        conditions.push('Cross-correlation supports direction');
      }

      if (['GENESIS', 'INCIPIENT'].includes(phase)) {
        conditions.push('Early-phase entry — confirm smart money accumulation');
      }
    } else if (direction === 'HOLD') {
      conditions.push('Conflicting signals — wait for clarity');
      conditions.push('Monitor for pattern confirmation');
    } else {
      conditions.push('Insufficient signal strength to enter');
      conditions.push('Wait for stronger confluence');
    }

    return conditions;
  }

  /**
   * Build exit conditions based on direction and risk.
   */
  private buildExitConditions(
    direction: string,
    phase: string,
    riskLevel: string
  ): string[] {
    const conditions: string[] = [
      'Exit immediately if stop-loss hits — do not hope for reversal',
    ];

    if (direction === 'LONG') {
      conditions.push('Take partial profits at first target');
      if (['HIGH', 'EXTREME'].includes(riskLevel)) {
        conditions.push('Exit on any bearish reversal signal');
      }
      if (['GENESIS', 'INCIPIENT'].includes(phase)) {
        conditions.push('Exit immediately if whale distribution accelerates');
      }
    } else if (direction === 'SHORT') {
      conditions.push('Cover on bounce to resistance');
      conditions.push('Exit if bullish pattern emerges with volume');
    }

    conditions.push('Exit if underlying thesis changes (phase transition, regime shift)');

    return conditions;
  }

  /**
   * Select allocation method based on direction and capital.
   */
  private selectAllocationMethod(
    direction: string,
    capital: number
  ): string {
    if (direction === 'WAIT') return 'NONE';
    if (capital < 50) return 'FIXED_AMOUNT';
    if (direction === 'LONG') return 'KELLY_MODIFIED';
    if (direction === 'SHORT') return 'MAX_DRAWDOWN_CONTROL';
    return 'RISK_PARITY';
  }

  /**
   * Build a human-readable reasoning chain for the strategy.
   */
  private buildReasoningChain(
    phase: string,
    direction: string,
    patternSentiment: string,
    dominantPattern: string | null,
    behaviorFlow: string,
    dominantArchetype: string,
    ccDirection: string,
    ccConfidence: number,
    ccSamples: number,
    verdict: string,
    riskLevel: string,
    entryConfidence: number
  ): string[] {
    const reasoning: string[] = [];

    reasoning.push(`Phase: ${phase} → ${PHASE_SYSTEM_MAP[phase as TokenPhase]?.category ?? 'UNKNOWN'} system`);
    reasoning.push(`Direction: ${direction} (confidence: ${(entryConfidence * 100).toFixed(0)}%)`);
    reasoning.push(`Pattern: ${dominantPattern ?? 'none'} (${patternSentiment})`);
    reasoning.push(`Behavior: ${behaviorFlow} flow from ${dominantArchetype}`);
    reasoning.push(`Cross-correlation: ${ccDirection} (conf: ${(ccConfidence * 100).toFixed(0)}%, samples: ${ccSamples})`);
    reasoning.push(`Verdict: ${verdict} | Risk: ${riskLevel}`);

    return reasoning;
  }

  // ============================================================
  // DATA QUALITY HELPERS
  // ============================================================

  /**
   * Assess data reliability from cross-correlation samples.
   */
  private assessDataReliability(
    totalSamples: number,
    crossCorrelation: CrossCorrelationResult | null
  ): AnalysisInput['dataReliability'] {
    const reliableCombinations = (crossCorrelation as any)?.conditionalProbabilities
      ?.filter((cp: any) => cp.validation?.isValid && cp.totalObservations >= 30)
      .length ?? 0;

    let sufficiency: string;
    if (totalSamples >= this.config.optimalCorrelationSamples) {
      sufficiency = 'OPTIMAL';
    } else if (totalSamples >= this.config.adequateCorrelationSamples) {
      sufficiency = 'ADEQUATE';
    } else if (totalSamples >= 5) {
      sufficiency = 'MINIMAL';
    } else {
      sufficiency = 'INSUFFICIENT';
    }

    return {
      sampleSufficiency: sufficiency,
      totalCorrelationSamples: totalSamples,
      reliableCombinations,
    };
  }

  /**
   * Compute overall data quality level.
   */
  private computeOverallQuality(
    candles1h: number,
    candles5m: number,
    tradersAnalyzed: number,
    correlationSamples: number,
    lifecycleConfidence: number
  ): 'POOR' | 'FAIR' | 'GOOD' | 'EXCELLENT' {
    let score = 0;

    // Candle data (0-25 points)
    if (candles1h >= 50) score += 15;
    else if (candles1h >= 20) score += 10;
    else if (candles1h >= 5) score += 5;
    if (candles5m >= 100) score += 10;
    else if (candles5m >= 50) score += 5;

    // Trader data (0-25 points)
    if (tradersAnalyzed >= 20) score += 15;
    else if (tradersAnalyzed >= 10) score += 10;
    else if (tradersAnalyzed >= 3) score += 5;
    if (tradersAnalyzed >= 50) score += 10;

    // Correlation data (0-25 points)
    if (correlationSamples >= 100) score += 25;
    else if (correlationSamples >= 30) score += 15;
    else if (correlationSamples >= 10) score += 10;
    else if (correlationSamples >= 3) score += 5;

    // Lifecycle confidence (0-25 points)
    score += Math.round(lifecycleConfidence * 25);

    if (score >= 75) return 'EXCELLENT';
    if (score >= 50) return 'GOOD';
    if (score >= 25) return 'FAIR';
    return 'POOR';
  }

  /**
   * Get data reliability level from correlation samples.
   */
  private getDataReliabilityLevel(
    samples: number
  ): 'INSUFFICIENT' | 'MINIMAL' | 'ADEQUATE' | 'OPTIMAL' {
    if (samples >= this.config.optimalCorrelationSamples) return 'OPTIMAL';
    if (samples >= this.config.adequateCorrelationSamples) return 'ADEQUATE';
    if (samples >= 5) return 'MINIMAL';
    return 'INSUFFICIENT';
  }

  // ============================================================
  // OBSERVATION & MODEL UPDATE HELPERS
  // ============================================================

  /**
   * Build the condition key for recording a cross-correlation observation.
   * Uses the current state from pattern scan + behavioral prediction + lifecycle.
   */
  private buildObservationConditions(result: PipelineResult): ConditionKey | null {
    try {
      const phase = result.behavioralPrediction?.phase ?? 'INCIPIENT';
      const dominantArchetype: TraderArchetype =
        (result.behavioralPrediction?.archetypeBreakdown?.[0]?.archetype as TraderArchetype) ?? 'RETAIL_FOMO';
      const dominantAction = result.behavioralPrediction?.netFlowScore
        ? (result.behavioralPrediction.netFlowScore > 0.15 ? 'BUY'
          : result.behavioralPrediction.netFlowScore < -0.15 ? 'SELL' : 'HOLD')
        : 'HOLD';
      const dominantPattern = (result.patternScan as any)?.dominantPattern?.patternName ?? result.patternScan?.dominantPattern ?? 'NO_PATTERN';

      return {
        traderArchetype: dominantArchetype,
        traderAction: dominantAction,
        candlePattern: dominantPattern,
        tokenPhase: phase,
      };
    } catch {
      return null;
    }
  }

  /**
   * Update behavioral models based on evaluated outcomes.
   *
   * When cross-correlation observations have been evaluated (step 9),
   * we know the actual outcome. This method updates the Bayesian
   * behavioral model matrices accordingly.
   */
  private async updateBehavioralModels(): Promise<void> {
    try {
      // Get recently evaluated signals where outcomes are known
      const evaluatedSignals = await db.predictiveSignal.findMany({
        where: {
          signalType: 'CROSS_CORRELATION',
          wasCorrect: { not: null },
          actualOutcome: { not: null },
          updatedAt: {
            gte: new Date(Date.now() - this.config.behavioralUpdateIntervalCycles * 5 * 60 * 1000),
          },
        },
        take: 50,
      });

      for (const signal of evaluatedSignals) {
        try {
          const prediction = JSON.parse(signal.prediction || '{}');
          const conditions: ConditionKey | null = prediction.conditions;
          if (!conditions) continue;

          const actualOutcome = JSON.parse(signal.actualOutcome || '{}');
          const wasCorrect = signal.wasCorrect;

          // Map outcome to the most likely behavioral action
          const observedAction = this.mapOutcomeToAction(actualOutcome.outcome, wasCorrect);

          // Update the behavioral model with this observation
          await behavioralModelEngine.updateModel(
            conditions.traderArchetype as TraderArchetype,
            conditions.tokenPhase as TokenPhase,
            observedAction,
            true
          );
        } catch {
          // Skip individual failed updates
        }
      }
    } catch {
      // Non-critical
    }
  }

  /**
   * Map a cross-correlation outcome to a trader action for behavioral model update.
   */
  private mapOutcomeToAction(
    outcome: string,
    wasCorrect: boolean | null
  ): 'BUY' | 'SELL' | 'HOLD' | 'ACCUMULATE' | 'DISTRIBUTE' | 'WATCH' {
    if (outcome === 'BULLISH' && wasCorrect) return 'BUY';
    if (outcome === 'BEARISH' && wasCorrect) return 'SELL';
    if (outcome === 'BULLISH' && !wasCorrect) return 'DISTRIBUTE';
    if (outcome === 'BEARISH' && !wasCorrect) return 'ACCUMULATE';
    return 'HOLD';
  }

  // ============================================================
  // PREDICTION STORAGE (STEP 7)
  // ============================================================

  /**
   * Store the pipeline result as a PredictiveSignal for later validation.
   * This is how the system closes the feedback loop — predictions are
   * validated against actual outcomes after the time window expires.
   */
  private async storePrediction(result: PipelineResult): Promise<void> {
    if (!result.strategy) return;

    const confidence = result.strategy.entryConfidence;
    if (confidence < this.config.minConfidenceToStore) return;

    // Determine prediction time window based on phase
    const timeWindows: Record<string, number> = {
      GENESIS: 1,    // 1 hour
      INCIPIENT: 2,  // 2 hours
      GROWTH: 4,     // 4 hours
      FOMO: 2,       // 2 hours
      DECLINE: 4,    // 4 hours
      LEGACY: 8,     // 8 hours
    };
    const windowHours = timeWindows[result.strategy.tokenPhase] ?? 4;

    await db.predictiveSignal.create({
      data: {
        signalType: 'PIPELINE_ANALYSIS',
        chain: result.chain,
        tokenAddress: result.tokenAddress,
        prediction: JSON.stringify({
          direction: result.strategy.direction,
          systemCategory: result.strategy.systemCategory,
          systemName: result.strategy.systemName,
          entryConfidence: result.strategy.entryConfidence,
          stopLossPct: result.strategy.stopLossPct,
          takeProfitPct: result.strategy.takeProfitPct,
          positionSizeUsd: result.strategy.positionSizeUsd,
          positionSizePctOfCapital: result.strategy.positionSizePctOfCapital,
          tokenPhase: result.strategy.tokenPhase,
          dominantPattern: result.strategy.dominantPattern,
          dominantTraderArchetype: result.strategy.dominantTraderArchetype,
          netBehaviorFlow: result.strategy.netBehaviorFlow,
          dataReliabilityLevel: result.strategy.dataReliabilityLevel,
          correlationSamples: result.strategy.correlationSamples,
          reasoning: result.strategy.reasoning,
          // Store deep analysis verdict for comparison
          deepVerdict: (result.deepAnalysis as any)?.verdict?.action ?? null,
          deepVerdictConfidence: (result.deepAnalysis as any)?.verdict?.confidence ?? 0,
          // Store pattern sentiment
          patternSentiment: (result.patternScan as any)?.overallSentiment ?? result.patternScan?.overallSignal ?? 'NEUTRAL',
          patternScore: (result.patternScan as any)?.sentimentScore ?? result.patternScan?.overallScore ?? 0,
          // Store behavior flow
          behaviorFlow: result.behavioralPrediction?.netFlowDirection ?? 'NEUTRAL',
          behaviorScore: result.behavioralPrediction?.netFlowScore ?? 0,
          // Store cross-correlation assessment
          ccDirection: (result.crossCorrelation as any)?.overallAssessment?.direction ?? 'NEUTRAL',
          ccConfidence: (result.crossCorrelation as any)?.overallAssessment?.confidence ?? 0,
          ccRecommendation: (result.crossCorrelation as any)?.overallAssessment?.recommendation ?? 'NO_DATA',
          // Data quality
          dataQuality: result.dataQuality.overallQuality,
          pipelineStepsCompleted: result.stepsCompleted,
          pipelineStepsFailed: result.stepsFailed,
          pipelineExecutionTimeMs: result.executionTimeMs,
        }),
        confidence,
        timeframe: `${windowHours}h`,
        validUntil: new Date(Date.now() + windowHours * 60 * 60 * 1000),
        evidence: JSON.stringify({
          entryConditions: result.strategy.entryConditions,
          exitConditions: result.strategy.exitConditions,
          riskAssessment: (result.deepAnalysis as any)?.riskAssessment?.overallRisk ?? 'UNKNOWN',
        }),
        dataPointsUsed: result.dataQuality.candles1h + result.dataQuality.candles5m + result.dataQuality.tradersAnalyzed,
      },
    });
  }

  // ============================================================
  // INFERENCE HELPERS
  // These extract/infer values needed by AnalysisInput from
  // the token data and other engine results, since we don't
  // want to re-run the full BrainOrchestrator.
  // ============================================================

  /**
   * Infer market regime from pattern + behavior + cross-correlation signals.
   */
  private inferRegime(result: PipelineResult): 'BULL' | 'BEAR' | 'SIDEWAYS' | 'TRANSITION' {
    const sentiment = (result.patternScan as any)?.sentimentScore ?? result.patternScan?.overallScore ?? 0;
    const behavior = result.behavioralPrediction?.netFlowScore ?? 0;
    const ccStrength = (result.crossCorrelation as any)?.overallAssessment?.strength ?? 0;

    const composite = sentiment * 0.4 + behavior * 0.35 + ccStrength * 0.25;

    if (composite > 0.3) return 'BULL';
    if (composite < -0.3) return 'BEAR';
    if (Math.abs(composite) < 0.1) return 'SIDEWAYS';
    return 'TRANSITION';
  }

  /**
   * Infer regime confidence from the consistency of signals.
   */
  private inferRegimeConfidence(result: PipelineResult): number {
    const sentiment = (result.patternScan as any)?.sentimentScore ?? result.patternScan?.overallScore ?? 0;
    const behavior = result.behavioralPrediction?.netFlowScore ?? 0;
    const ccStrength = (result.crossCorrelation as any)?.overallAssessment?.strength ?? 0;

    // If all signals agree, confidence is high
    const signals = [sentiment, behavior, ccStrength];
    const positive = signals.filter(s => s > 0).length;
    const negative = signals.filter(s => s < 0).length;

    // Agreement ratio (0-1)
    const agreement = Math.max(positive, negative) / 3;
    // Signal strength (0-1)
    const strength = Math.min(1, Math.abs(sentiment) + Math.abs(behavior) + Math.abs(ccStrength)) / 3;

    return Math.min(0.95, agreement * 0.6 + strength * 0.4);
  }

  /**
   * Infer bot swarm level from token DNA data.
   */
  private inferBotSwarmLevel(token: { botActivityPct?: number; dna?: { botActivityScore?: number } | null } | null): string {
    const botPct = token?.botActivityPct ?? 0;
    const dnaScore = token?.dna?.botActivityScore ?? 0;
    const combined = (botPct + dnaScore) / 2;

    if (combined > 80) return 'CRITICAL';
    if (combined > 60) return 'HIGH';
    if (combined > 40) return 'MEDIUM';
    if (combined > 20) return 'LOW';
    return 'NONE';
  }

  /**
   * Infer whale direction from token data.
   */
  private inferWhaleDirection(token: { smartMoneyPct?: number } | null): string {
    const smPct = token?.smartMoneyPct ?? 0;
    if (smPct > 30) return 'ACCUMULATING';
    if (smPct < 10) return 'DISTRIBUITING';
    return 'NEUTRAL';
  }

  /**
   * Infer operability score from token liquidity and volume.
   */
  private inferOperabilityScore(token: { liquidity?: number; volume24h?: number } | null): number {
    const liquidity = token?.liquidity ?? 0;
    const volume = token?.volume24h ?? 0;

    let score = 0;
    if (liquidity >= 100000) score += 40;
    else if (liquidity >= 50000) score += 30;
    else if (liquidity >= 10000) score += 20;
    else if (liquidity >= 1000) score += 10;

    if (volume >= 50000) score += 30;
    else if (volume >= 10000) score += 20;
    else if (volume >= 1000) score += 10;

    // Base score for existing data
    score += 20;

    return Math.min(100, score);
  }

  // ============================================================
  // PUBLIC API
  // ============================================================

  /**
   * Update pipeline configuration at runtime.
   */
  updateConfig(updates: Partial<PipelineConfig>): void {
    this.config = { ...this.config, ...updates };
  }

  /**
   * Get the current pipeline configuration.
   */
  getConfig(): PipelineConfig {
    return { ...this.config };
  }

  /**
   * Get aggregated pipeline metrics.
   */
  getMetrics(): typeof this.metrics & {
    cacheSize: number;
    cycleCount: number;
    nextCompressionIn: number;
    nextSignalValidationIn: number;
    nextBehavioralUpdateIn: number;
  } {
    return {
      ...this.metrics,
      cacheSize: this.resultCache.size,
      cycleCount: this.cycleCount,
      nextCompressionIn: Math.max(0, this.config.compressionIntervalCycles - (this.cycleCount - this.lastCompressionCycle)),
      nextSignalValidationIn: Math.max(0, this.config.signalValidationIntervalCycles - (this.cycleCount - this.lastSignalValidationCycle)),
      nextBehavioralUpdateIn: Math.max(0, this.config.behavioralUpdateIntervalCycles - (this.cycleCount - this.lastBehavioralUpdateCycle)),
    };
  }

  /**
   * Clear the pipeline result cache.
   */
  clearCache(): void {
    this.resultCache.clear();
  }

  /**
   * Get the cached result for a token, if available.
   */
  getCachedResult(tokenAddress: string): PipelineResult | null {
    const cached = this.resultCache.get(tokenAddress);
    if (!cached) return null;
    if (Date.now() - cached.timestamp > this.config.cacheTtlMs) {
      this.resultCache.delete(tokenAddress);
      return null;
    }
    return cached.result;
  }

  /**
   * Get the brain capacity report.
   */
  async getCapacityReport(): Promise<CapacityReport> {
    return brainCapacityEngine.generateReport();
  }

  /**
   * Get the last capacity report without querying the DB.
   */
  getLastCapacityReport(): CapacityReport | null {
    return brainCapacityEngine.getLastReport();
  }

  /**
   * Get cross-correlation statistics.
   */
  async getCorrelationStats() {
    return crossCorrelationEngine.getCorrelationStats();
  }

  /**
   * Get pattern compression stats.
   */
  async getCompressionStats() {
    return patternCompressionPipeline.getStats();
  }

  /**
   * Get backtest loop status.
   */
  getBacktestLoopStatus() {
    return backtestLoopEngine.getStatus();
  }

  /**
   * Run pattern compression manually.
   */
  async runCompression(): Promise<CompressionResult> {
    this.lastCompressionCycle = this.cycleCount;
    return patternCompressionPipeline.runCompression();
  }

  /**
   * Run signal validation manually.
   */
  async runSignalValidation(): Promise<ValidationReport> {
    this.lastSignalValidationCycle = this.cycleCount;
    return feedbackLoopEngine.validateSignals();
  }

  /**
   * Run outcome evaluation manually.
   */
  async runOutcomeEvaluation(): Promise<number> {
    return crossCorrelationEngine.evaluatePendingObservations();
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

/**
 * Singleton instance of the Brain Analysis Pipeline.
 *
 * Usage:
 *   import { brainAnalysisPipeline } from '@/lib/services/brain-analysis-pipeline';
 *
 *   // Single token analysis
 *   const result = await brainAnalysisPipeline.analyzeToken(tokenAddress, 'SOL');
 *
 *   // Batch analysis (brain cycle)
 *   const batch = await brainAnalysisPipeline.analyzeBatch(addresses, 'SOL');
 *
 *   // On-demand deep analysis
 *   const deep = await brainAnalysisPipeline.deepAnalyzeToken(tokenAddress, 'SOL', 100);
 */
export const brainAnalysisPipeline = new BrainAnalysisPipeline();
