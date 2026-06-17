import { NextRequest, NextResponse } from 'next/server';
import { validateOrError, brainActionSchema } from '@/lib/validations';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// CEREBRO API - Unified Big Data Brain Endpoint
// ============================================================
// POST /api/brain - Execute brain operations
// GET  /api/brain - Query brain state
//
// STABILITY FIXES:
// - Heavy operations protected by semaphore (max 2 concurrent)
// - Light operations (status, cycle_status, summary) use minimal imports
// - All heavy imports are lazy-loaded ONLY when needed
// - Request timeout protection
// ============================================================

// Lightweight actions that don't need heavy service imports
const LIGHT_ACTIONS = new Set([
  'brain_cycle_status',
  'capital_strategy_summary',
  'capital_strategy_load',
]);

// Actions that need the brain orchestrator (the heaviest module)
const HEAVY_ACTIONS = new Set([
  'analyze_token', 'analyze_batch', 'scan_market',
  'suggest_evolutions', 'profile_wallet',
  'capital_strategy_decision', 'run_single_cycle',
  'start_brain_cycle',
]);

// Simple semaphore implementation inline
const semaphores = {
  heavy: { running: 0, queue: [] as Array<() => void>, max: 2 },
  light: { running: 0, queue: [] as Array<() => void>, max: 6 },
};

async function acquire(type: 'heavy' | 'light'): Promise<void> {
  const sem = semaphores[type];
  if (sem.running < sem.max) {
    sem.running++;
    return;
  }
  return new Promise<void>((resolve) => sem.queue.push(resolve));
}

function release(type: 'heavy' | 'light'): void {
  const sem = semaphores[type];
  sem.running--;
  const next = sem.queue.shift();
  if (next) {
    sem.running++;
    next();
  }
}

// Timeout wrapper
function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
    ),
  ]);
}

export async function POST(request: NextRequest) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const validation = validateOrError(brainActionSchema, body);
  if (!validation.success) {
    return NextResponse.json({ error: validation.error }, { status: 400 });
  }

  const { action, params = {} } = validation.data;

  // ---- LIGHTWEIGHT ACTIONS (no heavy imports) ----
  if (LIGHT_ACTIONS.has(action)) {
    return handleLightAction(action, params);
  }

  // ---- MEDIUM ACTIONS (need specific sub-engines, not full orchestrator) ----
  if (!HEAVY_ACTIONS.has(action)) {
    return handleMediumAction(action, params);
  }

  // ---- HEAVY ACTIONS (need brain orchestrator, semaphore protected) ----
  return handleHeavyAction(action, params);
}

// ============================================================
// LIGHT ACTIONS - no heavy imports, fast response
// ============================================================

async function handleLightAction(action: string, params: any) {
  await acquire('light');
  try {
    switch (action) {
      case 'brain_cycle_status': {
        const { brainCycleEngine } = await import('@/lib/services/brain/brain-cycle-engine');
        const status = brainCycleEngine.getStatus();
        return NextResponse.json({ success: true, data: status });
      }

      case 'capital_strategy_summary': {
        const { capitalStrategyManager } = await import('@/lib/services/risk/capital-strategy-manager');
        const summary = capitalStrategyManager.getCapitalSummary();
        const learningState = capitalStrategyManager.getLearningState();
        return NextResponse.json({ success: true, data: { summary, learningState } });
      }

      case 'capital_strategy_load': {
        const { capitalStrategyManager } = await import('@/lib/services/risk/capital-strategy-manager');
        await capitalStrategyManager.loadLearningState();
        return NextResponse.json({ success: true, data: capitalStrategyManager.getCapitalSummary() });
      }

      default:
        return NextResponse.json({ error: `Unknown light action: ${action}` }, { status: 400 });
    }
  } catch (error: any) {
    console.error(`[Brain API] Light action ${action} error:`, error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  } finally {
    release('light');
  }
}

// ============================================================
// MEDIUM ACTIONS - need sub-engines but NOT the full orchestrator
// ============================================================

async function handleMediumAction(action: string, params: any) {
  await acquire('light');
  try {
    switch (action) {
      // ---- LIFECYCLE ----
      case 'detect_phase': {
        const { tokenAddress, chain = 'SOL' } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const { tokenLifecycleEngine } = await import('@/lib/services/brain/token-lifecycle-engine');
        const result = await tokenLifecycleEngine.detectPhase(tokenAddress, chain);
        return NextResponse.json({ success: true, data: result });
      }

      case 'detect_transition': {
        const { tokenAddress } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const { tokenLifecycleEngine } = await import('@/lib/services/brain/token-lifecycle-engine');
        const result = await tokenLifecycleEngine.detectTransition(tokenAddress);
        return NextResponse.json({ success: true, data: result });
      }

      case 'batch_detect_phases': {
        const { tokenAddresses } = params;
        if (!Array.isArray(tokenAddresses) || tokenAddresses.length === 0)
          return NextResponse.json({ error: 'tokenAddresses array is required' }, { status: 400 });
        const { tokenLifecycleEngine } = await import('@/lib/services/brain/token-lifecycle-engine');
        const results = await tokenLifecycleEngine.batchDetectPhases(tokenAddresses);
        const serialized = Object.fromEntries(Array.from(results.entries()).map(([k, v]) => [k, v]));
        return NextResponse.json({ success: true, data: serialized });
      }

      // ---- BEHAVIORAL ----
      case 'predict_behavior': {
        const { tokenAddress, chain = 'SOL' } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const { behavioralModelEngine } = await import('@/lib/services/brain/behavioral-model-engine');
        const result = await behavioralModelEngine.predictBehavior(tokenAddress, chain);
        return NextResponse.json({ success: true, data: result });
      }

      case 'detect_behavior_anomaly': {
        const { tokenAddress, chain = 'SOL' } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const { behavioralModelEngine } = await import('@/lib/services/brain/behavioral-model-engine');
        const result = await behavioralModelEngine.detectBehaviorAnomaly(tokenAddress, chain);
        return NextResponse.json({ success: true, data: result });
      }

      case 'initialize_behavioral_matrices': {
        const { behavioralModelEngine } = await import('@/lib/services/brain/behavioral-model-engine');
        await behavioralModelEngine.initializeDefaultMatrices();
        return NextResponse.json({ success: true, message: 'Behavioral matrices initialized' });
      }

      // ---- FEEDBACK ----
      case 'validate_signals': {
        const { feedbackLoopEngine } = await import('@/lib/services/backtesting/feedback-loop-engine');
        const result = await feedbackLoopEngine.validateSignals();
        return NextResponse.json({ success: true, data: result });
      }

      case 'process_backtest_feedback': {
        const { backtestRunId } = params;
        if (!backtestRunId) return NextResponse.json({ error: 'backtestRunId is required' }, { status: 400 });
        const { feedbackLoopEngine } = await import('@/lib/services/backtesting/feedback-loop-engine');
        const result = await feedbackLoopEngine.processBacktestFeedback(backtestRunId);
        return NextResponse.json({ success: true, data: result });
      }

      case 'refine_system': {
        const { systemId } = params;
        if (!systemId) return NextResponse.json({ error: 'systemId is required' }, { status: 400 });
        const { feedbackLoopEngine } = await import('@/lib/services/backtesting/feedback-loop-engine');
        const result = await feedbackLoopEngine.refineSystem(systemId);
        return NextResponse.json({ success: true, data: result });
      }

      case 'generate_synthetic_system': {
        const { parentSystemId, targetPhase } = params;
        if (!parentSystemId || !targetPhase)
          return NextResponse.json({ error: 'parentSystemId and targetPhase are required' }, { status: 400 });
        const { feedbackLoopEngine } = await import('@/lib/services/backtesting/feedback-loop-engine');
        const result = await feedbackLoopEngine.generateSyntheticSystem(parentSystemId, targetPhase);
        return NextResponse.json({ success: true, data: result });
      }

      case 'run_comparative_analysis': {
        const { feedbackLoopEngine } = await import('@/lib/services/backtesting/feedback-loop-engine');
        const result = await feedbackLoopEngine.runComparativeAnalysis();
        return NextResponse.json({ success: true, data: result });
      }

      // ---- OHLCV ----
      case 'backfill_token': {
        const { tokenAddress, chain = 'SOL', timeframes } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const { ohlcvPipeline } = await import('@/lib/services/data-sources/ohlcv-pipeline');
        const result = await ohlcvPipeline.backfillToken(tokenAddress, chain, timeframes);
        return NextResponse.json({ success: true, data: result });
      }

      case 'backfill_top_tokens': {
        const { limit = 10 } = params; // Reduced default from 20 to 10
        const { ohlcvPipeline } = await import('@/lib/services/data-sources/ohlcv-pipeline');
        const result = await ohlcvPipeline.backfillTopTokens(limit);
        const serialized = {
          totalTokens: result.totalTokens,
          totalCandlesStored: result.totalCandlesStored,
          failedTokens: result.failedTokens,
          duration: result.duration,
        };
        return NextResponse.json({ success: true, data: serialized });
      }

      case 'get_candles': {
        const { tokenAddress, timeframe = '1h', from, to, limit = 100 } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const { ohlcvPipeline } = await import('@/lib/services/data-sources/ohlcv-pipeline');
        const result = await ohlcvPipeline.getCandles(
          tokenAddress, timeframe,
          from ? new Date(from) : undefined,
          to ? new Date(to) : undefined,
          limit
        );
        return NextResponse.json({ success: true, data: result });
      }

      case 'get_candle_series': {
        const { tokenAddress, timeframe = '1h', count = 50 } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const { ohlcvPipeline } = await import('@/lib/services/data-sources/ohlcv-pipeline');
        const result = await ohlcvPipeline.getCandleSeries(tokenAddress, timeframe, count);
        return NextResponse.json({ success: true, data: result });
      }

      // ---- OPERABILITY ----
      case 'operability_score': {
        type OperabilityInput = import('@/lib/services/risk/operability-score').OperabilityInput;
        const input: OperabilityInput = params;
        if (!input.tokenAddress || input.liquidityUsd === undefined)
          return NextResponse.json({ error: 'tokenAddress, liquidityUsd are required' }, { status: 400 });
        const { calculateOperabilityScore } = await import('@/lib/services/risk/operability-score');
        const result = calculateOperabilityScore(input);
        return NextResponse.json({ success: true, data: result });
      }

      case 'quick_operability': {
        const { liquidityUsd, positionSizeUsd, chain = 'SOL' } = params;
        if (liquidityUsd === undefined || positionSizeUsd === undefined)
          return NextResponse.json({ error: 'liquidityUsd and positionSizeUsd are required' }, { status: 400 });
        const { quickOperabilityCheck } = await import('@/lib/services/risk/operability-score');
        const result = quickOperabilityCheck(liquidityUsd, positionSizeUsd, chain);
        return NextResponse.json({ success: true, data: result });
      }

      // ---- STOP BRAIN CYCLE ----
      case 'stop_brain_cycle': {
        const { brainCycleEngine } = await import('@/lib/services/brain/brain-cycle-engine');
        const result = await brainCycleEngine.stop();
        return NextResponse.json({ success: true, data: result });
      }

      // ---- GROWTH REPORT ----
      case 'growth_report': {
        const { brainCycleEngine } = await import('@/lib/services/brain/brain-cycle-engine');
        const report = await brainCycleEngine.getGrowthReport();
        return NextResponse.json({ success: true, data: report });
      }

      default:
        return NextResponse.json({
          error: `Unknown action: ${action}`,
          availableActions: [
            'detect_phase', 'detect_transition', 'batch_detect_phases',
            'predict_behavior', 'detect_behavior_anomaly', 'initialize_behavioral_matrices',
            'validate_signals', 'process_backtest_feedback', 'refine_system',
            'generate_synthetic_system', 'run_comparative_analysis',
            'backfill_token', 'backfill_top_tokens', 'get_candles', 'get_candle_series',
            'analyze_token', 'analyze_batch', 'scan_market',
            'operability_score', 'quick_operability',
            'profile_wallet', 'suggest_evolutions',
            'start_brain_cycle', 'stop_brain_cycle', 'brain_cycle_status',
            'growth_report', 'run_single_cycle',
            'capital_strategy_decision', 'capital_strategy_summary', 'capital_strategy_load',
          ],
        }, { status: 400 });
    }
  } catch (error: any) {
    console.error(`[Brain API] Medium action ${action} error:`, error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  } finally {
    release('light');
  }
}

// ============================================================
// HEAVY ACTIONS - need brain orchestrator, semaphore protected
// ============================================================

async function handleHeavyAction(action: string, params: any) {
  await acquire('heavy');
  try {
    // Lazy-load only the heavy modules we actually need
    const { brainOrchestrator } = await import('@/lib/services/brain/brain-orchestrator');
    const { matchSystem, batchMatchSystems, suggestEvolutions } = await import('@/lib/services/strategy/trading-system-matcher');

    switch (action) {
      case 'analyze_token': {
        const { tokenAddress, chain = 'SOL', positionSizeUsd = 10, expectedGainPct = 5 } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const analysis = await withTimeout(
          brainOrchestrator.analyzeToken(tokenAddress, chain, positionSizeUsd, expectedGainPct),
          30000, 'analyze_token'
        );
        const systemMatch = matchSystem(analysis);
        return NextResponse.json({ success: true, data: { analysis, systemRecommendation: systemMatch } });
      }

      case 'analyze_batch': {
        const { tokenAddresses, chain = 'SOL', positionSizeUsd = 10, expectedGainPct = 5 } = params;
        if (!Array.isArray(tokenAddresses) || tokenAddresses.length === 0)
          return NextResponse.json({ error: 'tokenAddresses array is required' }, { status: 400 });
        if (tokenAddresses.length > 20) // Reduced from 50 to 20
          return NextResponse.json({ error: 'Maximum 20 tokens per batch (memory limit)' }, { status: 400 });

        const batchResult = await withTimeout(
          brainOrchestrator.analyzeBatch(tokenAddresses, chain, positionSizeUsd, expectedGainPct),
          60000, 'analyze_batch'
        );
        const systemRecommendations = batchMatchSystems(batchResult.results);
        return NextResponse.json({
          success: true,
          data: {
            summary: batchResult.summary,
            operableCount: batchResult.operableTokens.length,
            tradeableCount: batchResult.tradeableTokens.length,
            topRecommendations: systemRecommendations.filter((r: any) => r.shouldTrade).slice(0, 10),
          },
        });
      }

      case 'scan_market': {
        const { chain = 'SOL', positionSizeUsd = 10, expectedGainPct = 5, limit = 10 } = params;
        const { db } = await import('@/lib/db');

        try {
          const tokens = await db.token.findMany({
            where: { chain, liquidity: { gt: 5000 }, volume24h: { gt: 1000 } },
            orderBy: { volume24h: 'desc' },
            take: Math.min(limit, 10), // Cap at 10 for memory safety
          });

          if (tokens.length === 0) {
            return NextResponse.json({
              success: true,
              data: { message: 'No tokens in DB matching criteria. Run /api/brain/init first.' },
            });
          }

          const addresses = tokens.map(t => t.address);
          const batchResult = await withTimeout(
            brainOrchestrator.analyzeBatch(addresses, chain, positionSizeUsd, expectedGainPct),
            60000, 'scan_market'
          );
          const systemRecommendations = batchMatchSystems(batchResult.results);

          for (const rec of systemRecommendations) {
            const token = tokens.find(t => t.address === rec.tokenAddress);
            if (token) rec.symbol = token.symbol;
          }

          return NextResponse.json({
            success: true,
            data: {
              totalScanned: tokens.length,
              operable: batchResult.operableTokens.length,
              tradeable: batchResult.tradeableTokens.length,
              topPicks: systemRecommendations.filter((r: any) => r.shouldTrade).slice(0, 5),
            },
          });
        } catch (dbError: any) {
          console.error('[Brain API] scan_market DB error:', dbError.message);
          return NextResponse.json({ error: dbError.message }, { status: 500 });
        }
      }

      case 'suggest_evolutions': {
        const { tokenAddress, chain = 'SOL', positionSizeUsd = 10, expectedGainPct = 5, recentWinRates = {} } = params;
        if (!tokenAddress) return NextResponse.json({ error: 'tokenAddress is required' }, { status: 400 });
        const analysis = await withTimeout(
          brainOrchestrator.analyzeToken(tokenAddress, chain, positionSizeUsd, expectedGainPct),
          30000, 'suggest_evolutions'
        );
        const evolutions = suggestEvolutions(analysis, recentWinRates);
        return NextResponse.json({ success: true, data: { analysis, evolutions } });
      }

      case 'profile_wallet': {
        const { address, chain = 'SOL' } = params;
        if (!address) return NextResponse.json({ error: 'address is required' }, { status: 400 });
        const result = await withTimeout(
          brainOrchestrator.profileWallet(address, chain),
          30000, 'profile_wallet'
        );
        return NextResponse.json({ success: true, data: result });
      }

      case 'capital_strategy_decision': {
        const { capitalUsd = 10, initialCapitalUsd = 10, chain = 'SOL' } = params || {};
        const { db: dbCS } = await import('@/lib/db');
        const { capitalStrategyManager } = await import('@/lib/services/risk/capital-strategy-manager');

        let csTokens: any[] = [];
        try {
          csTokens = await dbCS.token.findMany({
            where: { chain, liquidity: { gt: 5000 }, volume24h: { gt: 1000 } },
            orderBy: { volume24h: 'desc' },
            take: 10, // Reduced from 20
          });
        } catch { /* fallback */ }

        let csSystemRecs: any[] = [];
        if (csTokens.length > 0) {
          try {
            const csAddresses = csTokens.map(t => t.address);
            const csBatch = await withTimeout(
              brainOrchestrator.analyzeBatch(csAddresses, chain, Number(capitalUsd) * 0.1, 5),
              45000, 'capital_strategy_batch'
            );
            csSystemRecs = batchMatchSystems(csBatch.results);
            for (const rec of csSystemRecs) {
              const token = csTokens.find(t => t.address === rec.tokenAddress);
              if (token) rec.symbol = token.symbol;
            }
          } catch { /* fallback to empty */ }
        }

        const decision = await capitalStrategyManager.decide(
          Number(capitalUsd), Number(initialCapitalUsd), csSystemRecs, chain
        );
        return NextResponse.json({ success: true, data: decision });
      }

      case 'start_brain_cycle': {
        const { capitalUsd, initialCapitalUsd, chain, scanLimit, cycleIntervalMs, expectedGainPct, minOperabilityLevel, autoFeedback, autoBackfill } = params || {};
        const { brainCycleEngine } = await import('@/lib/services/brain/brain-cycle-engine');
        const result = await brainCycleEngine.start({
          ...(capitalUsd !== undefined && { capitalUsd: Number(capitalUsd) }),
          ...(initialCapitalUsd !== undefined && { initialCapitalUsd: Number(initialCapitalUsd) }),
          ...(chain && { chain }),
          ...(scanLimit !== undefined && { scanLimit: Math.min(Number(scanLimit), 10) }), // Cap at 10
          ...(cycleIntervalMs !== undefined && { cycleIntervalMs: Number(cycleIntervalMs) }),
          ...(expectedGainPct !== undefined && { expectedGainPct: Number(expectedGainPct) }),
          ...(minOperabilityLevel && { minOperabilityLevel }),
          ...(autoFeedback !== undefined && { autoFeedback: Boolean(autoFeedback) }),
          ...(autoBackfill !== undefined && { autoBackfill: Boolean(autoBackfill) }),
        });
        return NextResponse.json({ success: true, data: result });
      }

      case 'run_single_cycle': {
        const { capitalUsd = 10, chain = 'SOL', scanLimit = 10, expectedGainPct = 5 } = params || {};
        const { brainCycleEngine } = await import('@/lib/services/brain/brain-cycle-engine');
        const result = await brainCycleEngine.start({
          capitalUsd: Number(capitalUsd),
          initialCapitalUsd: Number(capitalUsd),
          chain,
          scanLimit: Math.min(Number(scanLimit), 10),
          expectedGainPct: Number(expectedGainPct),
          cycleIntervalMs: 999999999,
          autoFeedback: true,
          autoBackfill: false, // Don't auto-backfill in single cycle (memory)
        });
        await brainCycleEngine.stop();
        return NextResponse.json({ success: true, data: { started: result.started, message: result.message } });
      }

      default:
        return NextResponse.json({ error: `Unknown heavy action: ${action}` }, { status: 400 });
    }
  } catch (error: any) {
    console.error(`[Brain API] Heavy action ${action} error:`, error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  } finally {
    release('heavy');
  }
}

// ============================================================
// GET endpoint - lightweight, DB-only
// ============================================================

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const query = searchParams.get('q');

    switch (query) {
      case 'phase_signals': {
        const { tokenLifecycleEngine } = await import('@/lib/services/brain/token-lifecycle-engine');
        const phases = ['GENESIS', 'INCIPIENT', 'GROWTH', 'FOMO', 'DECLINE', 'LEGACY'] as const;
        const configs = phases.map(phase => ({
          phase,
          config: tokenLifecycleEngine.getPhaseSpecificSignals(phase),
        }));
        return NextResponse.json({ success: true, data: configs });
      }

      case 'status': {
        // DB-only, no heavy imports
        const { db } = await import('@/lib/db');

        let candleCount = 0, lifecycleCount = 0, behaviorCount = 0, feedbackCount = 0;
        let evolutionCount = 0, comparativeCount = 0, signalCount = 0, unvalidatedCount = 0;
        let tokenCount = 0, traderCount = 0, validatedCount = 0;

        try {
          [candleCount, lifecycleCount, behaviorCount, feedbackCount,
           evolutionCount, comparativeCount, signalCount, unvalidatedCount,
           tokenCount, traderCount] = await Promise.all([
            db.priceCandle.count().catch(() => 0),
            db.tokenLifecycleState.count().catch(() => 0),
            db.traderBehaviorModel.count().catch(() => 0),
            db.feedbackMetrics.count().catch(() => 0),
            db.systemEvolution.count().catch(() => 0),
            db.comparativeAnalysis.count().catch(() => 0),
            db.predictiveSignal.count().catch(() => 0),
            db.predictiveSignal.count({ where: { wasCorrect: null } }).catch(() => 0),
            db.token.count().catch(() => 0),
            db.trader.count().catch(() => 0),
          ]);

          // Also get validated count for health determination
          validatedCount = await db.predictiveSignal.count({ where: { wasCorrect: { not: null } } }).catch(() => 0);
        } catch (e) {
          console.error('[Brain API] status DB error:', e);
        }

        return NextResponse.json({
          success: true,
          data: {
            ohlcvCandles: candleCount,
            tokensTracked: tokenCount,
            tradersProfiled: traderCount,
            lifecycleStates: lifecycleCount,
            behavioralModels: behaviorCount,
            feedbackMetrics: feedbackCount,
            systemEvolutions: evolutionCount,
            comparativeAnalyses: comparativeCount,
            totalSignals: signalCount,
            unvalidatedSignals: unvalidatedCount,
            brainHealth: signalCount === 0 ? 'IDLE' :
              validatedCount === 0 ? 'LEARNING' :
              unvalidatedCount > 0 ? 'ACTIVE' :
              'HEALTHY',
            brainStatusMessage:
              signalCount === 0 ? 'No signals yet — start the Brain to begin analysis' :
              validatedCount === 0 ? 'Brain is learning — signals pending validation' :
              unvalidatedCount > 0 ? `Brain is active — ${unvalidatedCount} signals pending validation (normal)` :
              'Brain is healthy — all signals validated',
            enginesWired: [
              'lifecycle', 'behavioral', 'feedback', 'ohlcv',
              'big-data', 'wallet-profiler', 'bot-detection',
              'operability', 'system-matcher', 'brain-orchestrator',
              'brain-cycle', 'capital-strategy',
            ],
          },
        });
      }

      default:
        return NextResponse.json({
          success: true,
          endpoints: {
            'GET ?q=status': 'Brain status (DB-only, lightweight)',
            'GET ?q=phase_signals': 'Phase-specific signal configurations',
            'POST { action: "detect_phase" }': 'Detect token lifecycle phase',
            'POST { action: "brain_cycle_status" }': 'Get current cycle status (lightweight)',
            'POST { action: "capital_strategy_summary" }': 'Capital strategy summary (lightweight)',
            'POST { action: "analyze_token" }': 'Full brain analysis (heavy, semaphore-protected)',
            'POST { action: "scan_market" }': 'Market scan (heavy, max 10 tokens)',
            'POST { action: "start_brain_cycle" }': 'Start brain cycle (heavy)',
          },
        });
    }
  } catch (error: any) {
    console.error('[Brain API] GET Error:', error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
