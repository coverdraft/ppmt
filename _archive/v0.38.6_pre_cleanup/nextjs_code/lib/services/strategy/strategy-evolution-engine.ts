/**
 * Strategy Evolution Engine - CryptoQuant Terminal
 *
 * Synthetic evolution loop for trading strategies.
 * After backtesting, the best strategies are iteratively improved:
 *   - Parameters are mutated (TP, SL, position size, timeframe)
 *   - Mutations that improve performance are kept
 *   - Mutations that degrade performance are discarded
 *   - The AI determines which strategies are best based on composite scores
 *
 * This creates a genetic-algorithm-style optimization that converges
 * on the most profitable strategy configurations.
 */

import { db } from '../../db';

// ============================================================
// SEEDED PRNG — Same LCG as Monte Carlo (reproducible evolution)
// ============================================================

const LCG_A = 1_664_525;
const LCG_C = 1_013_904_223;
const LCG_M = 2 ** 32;

class SeededPRNG {
  private state: number;
  constructor(seed: number) { this.state = seed >>> 0; }
  next(): number {
    this.state = ((LCG_A * this.state + LCG_C) >>> 0) % LCG_M;
    return this.state / LCG_M;
  }
}

// ============================================================
// TYPES
// ============================================================

export interface EvolutionConfig {
  /** Maximum number of evolution iterations */
  maxIterations: number;
  /** Minimum improvement threshold to keep a mutation (in score points) */
  improvementThreshold: number;
  /** Mutation magnitude (0-1, higher = bigger parameter changes) */
  mutationRate: number;
  /** Number of top strategies to evolve per iteration */
  topN: number;
  /** Initial capital for backtesting evolved strategies */
  capital: number;
  /** Optional seed system IDs from previous cycle — used for cross-cycle learning */
  seedSystemIds?: string[];
  /** Number of consecutive non-improving iterations before early stop (default: 3) */
  earlyStopPatience?: number;
  /** Enable adaptive mutation rate (decrease when stalled) */
  adaptiveMutation?: boolean;
  /** Seed for the PRNG — same seed + same data = same evolution (default: 42) */
  seed?: number;
}

export interface EvolvedStrategy {
  id: string;
  parentId: string;
  generation: number;
  systemId: string;
  backtestId: string;
  name: string;
  category: string;
  timeframe: string;
  score: number;
  parentScore: number;
  improvement: number;
  mutations: string[];
  pnlPct: number;
  sharpeRatio: number;
  winRate: number;
  totalTrades: number;
  status: 'pending' | 'running' | 'improved' | 'degraded' | 'failed';
  createdAt: Date;
}

export interface EvolutionResult {
  iterations: number;
  totalMutations: number;
  improved: number;
  degraded: number;
  bestScore: number;
  bestStrategy: EvolvedStrategy | null;
  allStrategies: EvolvedStrategy[];
  improvementHistory: { iteration: number; avgScore: number; bestScore: number }[];
}

// ============================================================
// DEFAULT CONFIG
// ============================================================

export const DEFAULT_EVOLUTION_CONFIG: EvolutionConfig = {
  maxIterations: 5,
  improvementThreshold: 2,
  mutationRate: 0.3,
  topN: 3,
  capital: 10000,
  earlyStopPatience: 3,
  adaptiveMutation: true,
  seed: 42,
};

// ============================================================
// MUTATION FUNCTIONS
// ============================================================

interface MutableParams {
  takeProfit: number;
  stopLoss: number;
  positionSizePct: number;
  trailingStopPct: number;
  confidenceThreshold: number;
  maxConcurrentTrades: number;
  timeBasedExitMin: number;
}

/**
 * Mutate strategy parameters with controlled, reproducible randomness.
 * Uses seeded PRNG (same LCG as Monte Carlo) instead of Math.random()
 * to ensure reproducible evolution runs for a given seed.
 */
function mutateParams(
  params: MutableParams,
  mutationRate: number,
  rng: SeededPRNG,
): { mutated: MutableParams; changes: string[] } {
  const changes: string[] = [];
  const mutated = { ...params };

  // Take Profit: mutate by ±mutationRate * current value, clamped [5, 200]
  if (rng.next() < 0.6) {
    const delta = mutated.takeProfit * mutationRate * (rng.next() * 2 - 1);
    const newVal = Math.round(Math.max(5, Math.min(200, mutated.takeProfit + delta)));
    if (newVal !== mutated.takeProfit) {
      changes.push(`TP: ${mutated.takeProfit}% → ${newVal}%`);
      mutated.takeProfit = newVal;
    }
  }

  // Stop Loss: mutate by ±mutationRate * current value, clamped [2, 50]
  if (rng.next() < 0.6) {
    const delta = mutated.stopLoss * mutationRate * (rng.next() * 2 - 1);
    const newVal = Math.round(Math.max(2, Math.min(50, mutated.stopLoss + delta)));
    if (newVal !== mutated.stopLoss) {
      changes.push(`SL: ${mutated.stopLoss}% → ${newVal}%`);
      mutated.stopLoss = newVal;
    }
  }

  // Position Size: mutate by ±mutationRate, clamped [1, 20]
  if (rng.next() < 0.4) {
    const delta = mutated.positionSizePct * mutationRate * (rng.next() * 2 - 1);
    const newVal = Math.round(Math.max(1, Math.min(20, mutated.positionSizePct + delta)));
    if (newVal !== mutated.positionSizePct) {
      changes.push(`Size: ${mutated.positionSizePct}% → ${newVal}%`);
      mutated.positionSizePct = newVal;
    }
  }

  // Trailing Stop: mutate by ±mutationRate, clamped [5, 80]
  if (rng.next() < 0.3) {
    const delta = mutated.trailingStopPct * mutationRate * (rng.next() * 2 - 1);
    const newVal = Math.round(Math.max(5, Math.min(80, mutated.trailingStopPct + delta)));
    if (newVal !== mutated.trailingStopPct) {
      changes.push(`Trail: ${mutated.trailingStopPct}% → ${newVal}%`);
      mutated.trailingStopPct = newVal;
    }
  }

  // Confidence Threshold: mutate by ±10, clamped [30, 95]
  if (rng.next() < 0.3) {
    const delta = 10 * mutationRate * (rng.next() * 2 - 1);
    const newVal = Math.round(Math.max(30, Math.min(95, mutated.confidenceThreshold + delta)));
    if (newVal !== mutated.confidenceThreshold) {
      changes.push(`Conf: ${mutated.confidenceThreshold}% → ${newVal}%`);
      mutated.confidenceThreshold = newVal;
    }
  }

  // Max Concurrent: mutate by ±2, clamped [1, 10]
  if (rng.next() < 0.2) {
    const delta = Math.round(2 * mutationRate * (rng.next() * 2 - 1));
    const newVal = Math.max(1, Math.min(10, mutated.maxConcurrentTrades + delta));
    if (newVal !== mutated.maxConcurrentTrades) {
      changes.push(`Concurrent: ${mutated.maxConcurrentTrades} → ${newVal}`);
      mutated.maxConcurrentTrades = newVal;
    }
  }

  return { mutated, changes };
}

/**
 * Calculate composite score for a backtest result (v2).
 * Higher is better. Backtests with 0 trades get score 0.
 *
 * Key improvements:
 *   - 0-trade backtests get score 0 (no free points from normalizedPnl base)
 *   - Sharpe normalization uses wider range [-2, +3] for better spread
 *   - PnL normalization uses 5x multiplier for finer granularity
 *   - Trade count bonus rewards statistical significance
 */
function calculateCompositeScore(params: {
  sharpeRatio: number;
  winRate: number;
  pnlPct: number;
  profitFactor: number;
  maxDrawdownPct: number;
  totalTrades: number;
}): number {
  if (params.totalTrades === 0) return 0;

  const normalizedSharpe = Math.min(100, Math.max(0, (params.sharpeRatio + 2) * 20));
  const normalizedWinRate = params.winRate * 100;
  const normalizedPnl = Math.min(100, Math.max(0, 50 + params.pnlPct * 5));
  const normalizedPF = Math.min(100, Math.max(0, params.profitFactor * 20));
  const normalizedDD = Math.min(100, Math.max(0, params.maxDrawdownPct * 2));

  // Trade count bonus: more trades = more statistically significant
  const tradeBonus = Math.min(15, params.totalTrades * 0.5);

  const score = (
    normalizedSharpe * 0.30 +
    normalizedWinRate * 0.20 +
    normalizedPnl    * 0.25 +
    normalizedPF     * 0.10 +
    tradeBonus       * 0.05 -
    normalizedDD     * 0.10
  );

  return Math.max(0, score);
}

// ============================================================
// EVOLUTION ENGINE CLASS
// ============================================================

export class StrategyEvolutionEngine {
  /**
   * Run the synthetic evolution loop.
   *
   * Algorithm:
   * 1. Fetch top N completed backtests from the DB
   * 2. For each iteration:
   *    a. Mutate parameters of the top strategies
   *    b. Create new trading systems + backtests with mutated params
   *    c. Run backtests
   *    d. Compare results with parent strategies
   *    e. Keep improved strategies, discard degraded ones
   * 3. Return the final set of strategies with their evolution history
   */
  async runEvolution(
    config: EvolutionConfig = DEFAULT_EVOLUTION_CONFIG,
  ): Promise<EvolutionResult> {
    const allStrategies: EvolvedStrategy[] = [];
    const improvementHistory: EvolutionResult['improvementHistory'] = [];
    let totalMutations = 0;
    let improved = 0;
    let degraded = 0;

    // Seeded PRNG for reproducible evolution
    const rng = new SeededPRNG(config.seed ?? 42);

    // Dynamic imports
    const bteModule = await import('@/lib/services/backtesting/backtesting-engine');
    const backtestingEngine = bteModule.backtestingEngine;
    type BacktestConfig = import('@/lib/services/backtesting/backtesting-engine').BacktestConfig;

    const tseModule = await import('./trading-system-engine');
    const tradingSystemEngine = tseModule.tradingSystemEngine;

    const bdbModule = await import('@/lib/services/backtesting/backtest-data-bridge');
    const backtestDataBridge = bdbModule.backtestDataBridge;

    // Step 1: Fetch seed strategies (from previous cycle) or top N from DB
    let topBacktests;

    if (config.seedSystemIds && config.seedSystemIds.length > 0) {
      // Use seeds from previous cycle for cross-cycle learning
      topBacktests = await db.backtestRun.findMany({
        where: {
          status: 'COMPLETED',
          totalTrades: { gt: 0 },
          systemId: { in: config.seedSystemIds },
        },
        include: { system: true },
        orderBy: { sharpeRatio: 'desc' },
        take: config.topN,
      });

      // If not enough seeded results, supplement with top from DB
      if (topBacktests.length < config.topN) {
        const seededSystemIds = new Set(topBacktests.map(bt => bt.systemId));
        const additional = await db.backtestRun.findMany({
          where: {
            status: 'COMPLETED',
            totalTrades: { gt: 0 },
            systemId: { notIn: [...seededSystemIds] as string[] },
          },
          include: { system: true },
          orderBy: { sharpeRatio: 'desc' },
          take: config.topN - topBacktests.length,
        });
        topBacktests = [...topBacktests, ...additional];
      }

      console.log(
        `[Evolution] Using ${config.seedSystemIds.length} seed IDs from previous cycle, ` +
        `fetched ${topBacktests.length} total parent backtests`
      );
    } else {
      // No seeds — fetch top N from DB by Sharpe (default behavior)
      topBacktests = await db.backtestRun.findMany({
        where: { status: 'COMPLETED', totalTrades: { gt: 0 } },
        include: { system: true },
        orderBy: { sharpeRatio: 'desc' },
        take: config.topN,
      });
    }

    if (topBacktests.length === 0) {
      return {
        iterations: 0,
        totalMutations: 0,
        improved: 0,
        degraded: 0,
        bestScore: 0,
        bestStrategy: null,
        allStrategies: [],
        improvementHistory: [],
      };
    }

    // Sanity check: filter out garbage backtests before evolution
    // (extremely negative Sharpe, 0 win rate with many trades, etc.)
    const validBacktests = topBacktests.filter(bt => {
      // Skip if Sharpe is extremely negative (broken strategy)
      if (bt.sharpeRatio < -10) return false;
      // Skip if 100% loss rate with many trades (completely broken)
      if (bt.winRate === 0 && bt.totalTrades > 10) return false;
      // Skip if max drawdown > 99% (effectively wiped out)
      if (bt.maxDrawdownPct > 99) return false;
      return true;
    });

    if (validBacktests.length < topBacktests.length) {
      console.warn(
        `[Evolution] Filtered out ${topBacktests.length - validBacktests.length} garbage backtests ` +
        `(extreme negative Sharpe, 0% win rate, or >99% drawdown)`
      );
    }

    // Use filtered backtests for evolution
    topBacktests = validBacktests;

    if (topBacktests.length === 0) {
      console.warn('[Evolution] No valid backtests remain after quality filtering — returning empty result');
      return {
        iterations: 0,
        totalMutations: 0,
        improved: 0,
        degraded: 0,
        bestScore: 0,
        bestStrategy: null,
        allStrategies: [],
        improvementHistory: [],
      };
    }

    // Calculate parent scores
    const parentStrategies: Map<string, { score: number; params: MutableParams }> = new Map();
    for (const bt of topBacktests) {
      const score = calculateCompositeScore({
        sharpeRatio: bt.sharpeRatio,
        winRate: bt.winRate,
        pnlPct: bt.totalPnlPct,
        profitFactor: bt.profitFactor,
        maxDrawdownPct: bt.maxDrawdownPct,
        totalTrades: bt.totalTrades,
      });

      // Extract current params from system
      let exitSignal: Record<string, unknown> = {};
      let executionConfig: Record<string, unknown> = {};
      try { exitSignal = JSON.parse(bt.system.exitSignal || '{}'); } catch { /* ignore */ }
      try { executionConfig = JSON.parse(bt.system.executionConfig || '{}'); } catch { /* ignore */ }

      const params: MutableParams = {
        takeProfit: (exitSignal.takeProfit as number) || bt.system.takeProfitPct || 40,
        stopLoss: (exitSignal.stopLoss as number) || bt.system.stopLossPct || 15,
        positionSizePct: (executionConfig.maxPositionSize as number) || bt.system.maxPositionPct || 5,
        trailingStopPct: (exitSignal.trailingStopPercent as number) || 25,
        confidenceThreshold: 65,
        maxConcurrentTrades: 5,
        timeBasedExitMin: 1440,
      };

      parentStrategies.set(bt.id, { score, params });
    }

    // Step 2: Evolution loop
    let currentBest = [...topBacktests];
    let consecutiveNoImprove = 0;
    let currentMutationRate = config.mutationRate;
    let bestScoreEver = Math.max(...[...parentStrategies.values()].map(p => p.score), 0);

    for (let iteration = 0; iteration < config.maxIterations; iteration++) {
      const iterationStrategies: EvolvedStrategy[] = [];

      for (const parentBt of currentBest) {
        const parentInfo = parentStrategies.get(parentBt.id);
        if (!parentInfo) continue;

        // Mutate parameters (use adaptive mutation rate)
        const { mutated, changes } = mutateParams(parentInfo.params, currentMutationRate, rng);
        if (changes.length === 0) continue; // No mutation happened

        totalMutations++;

        try {
          // Create evolved trading system
          const parentSystem = parentBt.system;
          const evolvedSystem = await db.tradingSystem.create({
            data: {
              name: `${parentSystem.name} [Gen${iteration + 1}]`,
              category: parentSystem.category,
              icon: parentSystem.icon,
              assetFilter: parentSystem.assetFilter,
              phaseConfig: parentSystem.phaseConfig,
              entrySignal: parentSystem.entrySignal,
              executionConfig: JSON.stringify({
                ...JSON.parse(parentSystem.executionConfig || '{}'),
                maxPositionSize: mutated.positionSizePct,
              }),
              exitSignal: JSON.stringify({
                ...JSON.parse(parentSystem.exitSignal || '{}'),
                takeProfit: mutated.takeProfit,
                stopLoss: mutated.stopLoss,
                trailingStop: mutated.trailingStopPct > 0,
                trailingStopPercent: mutated.trailingStopPct,
                timeBasedExit: mutated.timeBasedExitMin,
              }),
              bigDataContext: parentSystem.bigDataContext,
              primaryTimeframe: parentSystem.primaryTimeframe,
              allocationMethod: parentSystem.allocationMethod,
              maxPositionPct: mutated.positionSizePct,
              stopLossPct: mutated.stopLoss,
              takeProfitPct: mutated.takeProfit,
              cashReservePct: parentSystem.cashReservePct,
              isActive: false,
              isPaperTrading: false,
              parentSystemId: parentSystem.id,
            },
          });

          // Create backtest for evolved system
          const periodStart = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000);
          const periodEnd = new Date();
          const initialCapital = config.capital;

          const evolvedBacktest = await db.backtestRun.create({
            data: {
              systemId: evolvedSystem.id,
              mode: 'HISTORICAL',
              periodStart,
              periodEnd,
              initialCapital,
              allocationMethod: parentSystem.allocationMethod || 'KELLY_MODIFIED',
              capitalAllocation: JSON.stringify({
                method: parentSystem.allocationMethod || 'KELLY_MODIFIED',
                initialCapital,
              }),
              strategyMeta: JSON.stringify({
                strategyId: `evolved-gen${iteration + 1}`,
                strategyName: evolvedSystem.name,
                category: parentSystem.category,
                timeframe: evolvedSystem.primaryTimeframe,
                tokenAgeCategory: 'MEDIUM',
                riskTolerance: 'MODERATE',
                generation: iteration + 1,
                parentId: parentBt.id,
                mutations: changes,
              }),
              status: 'RUNNING',
              progress: 0.05,
              startedAt: new Date(),
            },
          });

          // Run backtest
          const systemTemplate = tradingSystemEngine.getTemplate(evolvedSystem.name) ??
            tradingSystemEngine.createSystemFromTemplate(
              tradingSystemEngine.getAllTemplateNames()[0],
              { name: evolvedSystem.name, category: evolvedSystem.category as 'ALPHA_HUNTER' },
            );

          // Use the evolved system's primaryTimeframe (from parent), NOT the template's default
          const backtestTimeframe = evolvedSystem.primaryTimeframe || systemTemplate.primaryTimeframe || '4h';

          const tokenData = await backtestDataBridge.loadTokensForBacktest({
            startDate: periodStart,
            endDate: periodEnd,
            timeframe: backtestTimeframe,
            minCandles: 20,
            maxTokens: 10,
            includeMetrics: true,
          });

          let childScore = 0;
          let pnlPct = 0;
          let sharpeRatio = 0;
          let winRate = 0;
          let totalTrades = 0;
          let status: EvolvedStrategy['status'] = 'failed';

          if (tokenData.length > 0) {
            const btConfig: BacktestConfig = {
              system: systemTemplate,
              mode: 'HISTORICAL',
              startDate: periodStart,
              endDate: periodEnd,
              initialCapital,
              feesPct: 0.003,
              slippagePct: 0.5,
              applySlippage: true,
              enforcePhaseFilter: true,
            };

            const result = await backtestingEngine.runBacktest(btConfig, tokenData);

            // Store operations
            if (result.trades.length > 0) {
              await db.backtestOperation.createMany({
                data: result.trades.map(trade => ({
                  backtestId: evolvedBacktest.id,
                  systemId: evolvedSystem.id,
                  tokenAddress: trade.tokenAddress,
                  tokenSymbol: trade.symbol,
                  chain: parentSystem.chain || 'SOL',
                  tokenPhase: trade.phase,
                  tokenAgeMinutes: trade.entryTime ? Math.max(0, Math.round((Date.now() - new Date(trade.entryTime).getTime()) / 60000)) : 0,
                  marketConditions: JSON.stringify({ generation: iteration + 1 }),
                  tokenDnaSnapshot: JSON.stringify({}),
                  traderComposition: JSON.stringify({}),
                  bigDataContext: JSON.stringify({}),
                  operationType: trade.direction,
                  timeframe: systemTemplate.primaryTimeframe,
                  entryPrice: trade.entryPrice,
                  entryTime: trade.entryTime,
                  entryReason: JSON.stringify({ reason: 'evolved_backtest', mutations: changes }),
                  exitPrice: trade.exitPrice ?? 0,
                  exitTime: trade.exitTime ?? new Date(),
                  exitReason: trade.exitReason,
                  quantity: trade.quantity,
                  positionSizeUsd: trade.size,
                  pnlUsd: trade.pnl,
                  pnlPct: trade.pnlPct,
                  holdTimeMin: trade.holdTimeMin,
                  maxFavorableExc: trade.mfe,
                  maxAdverseExc: trade.mae,
                  capitalAllocPct: (trade.size / initialCapital) * 100,
                  allocationMethodUsed: systemTemplate.allocationMethod,
                })),
              });
            }

            // Update backtest record
            await db.backtestRun.update({
              where: { id: evolvedBacktest.id },
              data: {
                status: 'COMPLETED',
                progress: 1,
                completedAt: new Date(),
                finalCapital: result.finalEquity,
                totalPnl: result.finalEquity - result.initialCapital,
                totalPnlPct: result.totalReturnPct,
                totalTrades: result.totalTrades,
                winTrades: result.winningTrades,
                lossTrades: result.losingTrades,
                winRate: result.winRate,
                profitFactor: result.profitFactor,
                sharpeRatio: result.sharpeRatio,
                maxDrawdown: result.maxDrawdown,
                maxDrawdownPct: result.maxDrawdownPct,
                avgHoldTimeMin: result.avgHoldTimeMin,
                sortinoRatio: result.sortinoRatio,
                calmarRatio: result.calmarRatio,
                recoveryFactor: result.recoveryFactor,
                expectancy: result.expectancy,
                phaseResults: JSON.stringify(result.phaseBreakdown),
              },
            });

            childScore = calculateCompositeScore({
              sharpeRatio: result.sharpeRatio,
              winRate: result.winRate,
              pnlPct: result.totalReturnPct,
              profitFactor: result.profitFactor,
              maxDrawdownPct: result.maxDrawdownPct,
              totalTrades: result.totalTrades,
            });
            pnlPct = result.totalReturnPct;
            sharpeRatio = result.sharpeRatio;
            winRate = result.winRate;
            totalTrades = result.totalTrades;

            const improvement = childScore - parentInfo.score;
            status = improvement >= config.improvementThreshold ? 'improved' : 'degraded';

            if (status === 'improved') {
              improved++;
              // Update the parent strategy map with improved params
              parentStrategies.set(evolvedBacktest.id, { score: childScore, params: mutated });
            } else {
              degraded++;
            }
          } else {
            // No data available — mark as FAILED to exclude from ranking
            await db.backtestRun.update({
              where: { id: evolvedBacktest.id },
              data: {
                status: 'FAILED',
                progress: 1,
                completedAt: new Date(),
                finalCapital: initialCapital,
                totalPnl: 0,
                totalPnlPct: 0,
                errorLog: 'No token data available for evolution backtest.',
              },
            });
            status = 'failed';
          }

          const evolved: EvolvedStrategy = {
            id: `evo-${iteration}-${evolvedSystem.id.slice(0, 8)}`,
            parentId: parentBt.id,
            generation: iteration + 1,
            systemId: evolvedSystem.id,
            backtestId: evolvedBacktest.id,
            name: evolvedSystem.name,
            category: evolvedSystem.category,
            timeframe: evolvedSystem.primaryTimeframe,
            score: childScore,
            parentScore: parentInfo.score,
            improvement: childScore - parentInfo.score,
            mutations: changes,
            pnlPct,
            sharpeRatio,
            winRate,
            totalTrades,
            status,
            createdAt: new Date(),
          };

          iterationStrategies.push(evolved);
          allStrategies.push(evolved);
        } catch (error) {
          console.error(`[Evolution] Iteration ${iteration} failed:`, error);
        }
      }

      // Update currentBest for next iteration: use improved strategies
      const improvedStrategies = iterationStrategies.filter(s => s.status === 'improved');
      if (improvedStrategies.length > 0) {
        // Fetch the improved backtests for next iteration
        const improvedBts = await db.backtestRun.findMany({
          where: { id: { in: improvedStrategies.map(s => s.backtestId) } },
          include: { system: true },
        });
        currentBest = improvedBts.length > 0 ? improvedBts : currentBest;
      }

      // Track improvement history
      const avgScore = allStrategies.length > 0
        ? allStrategies.reduce((s, st) => s + st.score, 0) / allStrategies.length
        : 0;
      const bestScore = allStrategies.length > 0
        ? Math.max(...allStrategies.map(s => s.score))
        : 0;

      // Early stopping: if no improvement for N iterations, stop
      const iterationImproved = iterationStrategies.some(s => s.status === 'improved');
      if (iterationImproved) {
        consecutiveNoImprove = 0;
        if (bestScore > bestScoreEver) {
          bestScoreEver = bestScore;
        }
      } else {
        consecutiveNoImprove++;
      }

      // Adaptive mutation: decrease rate when stalled, increase when improving
      if (config.adaptiveMutation) {
        if (consecutiveNoImprove >= 2) {
          currentMutationRate = Math.min(0.8, currentMutationRate * 1.3); // Increase diversity
          console.log(`[Evolution] Stalled ${consecutiveNoImprove} iters, increasing mutation rate to ${currentMutationRate.toFixed(2)}`);
        } else if (iterationImproved) {
          currentMutationRate = Math.max(0.1, currentMutationRate * 0.9); // Fine-tune when improving
        }
      }

      // Check early stop
      const patience = config.earlyStopPatience ?? 3;
      if (consecutiveNoImprove >= patience) {
        console.log(`[Evolution] Early stopping: no improvement for ${patience} consecutive iterations`);
        improvementHistory.push({ iteration: iteration + 1, avgScore, bestScore });
        break;
      }

      improvementHistory.push({
        iteration: iteration + 1,
        avgScore,
        bestScore,
      });
    }

    // Find best strategy
    const sortedStrategies = [...allStrategies].sort((a, b) => b.score - a.score);
    const bestStrategy = sortedStrategies[0] || null;

    // Record evolution in DB
    try {
      await db.systemEvolution.create({
        data: {
          parentSystemId: topBacktests[0]?.systemId,
          childSystemId: bestStrategy?.systemId,
          evolutionType: 'synthetic_loop',
          triggerMetric: 'composite_score',
          triggerValue: bestStrategy?.score ?? 0,
          improvementPct: bestStrategy?.improvement ?? 0,
          backtestId: bestStrategy?.backtestId,
        },
      });
    } catch {
      // Best-effort evolution logging
    }

    return {
      iterations: config.maxIterations,
      totalMutations,
      improved,
      degraded,
      bestScore: bestStrategy?.score ?? 0,
      bestStrategy,
      allStrategies: sortedStrategies,
      improvementHistory,
    };
  }

  /**
   * Execute a trade for a given strategy (paper trading).
   * Records the entry and manages the position.
   */
  async executeEntry(params: {
    systemId: string;
    tokenAddress: string;
    tokenSymbol: string;
    direction: 'LONG' | 'SHORT';
    entryPrice: number;
    positionSizeUsd: number;
    chain?: string;
  }): Promise<{ tradeId: string; status: string }> {
    const { systemId, tokenAddress, tokenSymbol, direction, entryPrice, positionSizeUsd, chain = 'ETH' } = params;

    // Get the trading system
    const system = await db.tradingSystem.findUnique({ where: { id: systemId } });
    if (!system) {
      throw new Error(`Trading system not found: ${systemId}`);
    }

    // Parse exit signal config
    let exitConfig: Record<string, unknown> = {};
    try { exitConfig = JSON.parse(system.exitSignal || '{}'); } catch { /* ignore */ }

    const quantity = positionSizeUsd / entryPrice;

    // Create a backtest run as paper trade container
    const paperTrade = await db.backtestRun.create({
      data: {
        systemId,
        mode: 'PAPER',
        periodStart: new Date(),
        periodEnd: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000), // 30 days
        initialCapital: positionSizeUsd,
        allocationMethod: system.allocationMethod,
        capitalAllocation: JSON.stringify({ type: 'paper_trade', direction, tokenAddress }),
        status: 'RUNNING',
        progress: 0,
        startedAt: new Date(),
      },
    });

    // Create the operation record (entry)
    await db.backtestOperation.create({
      data: {
        backtestId: paperTrade.id,
        systemId,
        tokenAddress,
        tokenSymbol,
        chain,
        tokenPhase: 'GROWTH',
        tokenAgeMinutes: 0,
        marketConditions: JSON.stringify({ paperTrade: true }),
        tokenDnaSnapshot: JSON.stringify({}),
        traderComposition: JSON.stringify({}),
        bigDataContext: JSON.stringify({}),
        operationType: direction,
        timeframe: system.primaryTimeframe,
        entryPrice,
        entryTime: new Date(),
        entryReason: JSON.stringify({
          reason: 'paper_entry',
          systemName: system.name,
          category: system.category,
          takeProfit: exitConfig.takeProfit,
          stopLoss: exitConfig.stopLoss,
        }),
        exitPrice: null,
        exitTime: null,
        exitReason: null,
        quantity,
        positionSizeUsd,
        pnlUsd: null,
        pnlPct: null,
        holdTimeMin: null,
        maxFavorableExc: null,
        maxAdverseExc: null,
        capitalAllocPct: 100,
        allocationMethodUsed: system.allocationMethod,
      },
    });

    // Mark the system as active paper trading
    await db.tradingSystem.update({
      where: { id: systemId },
      data: { isActive: true, isPaperTrading: true },
    });

    return {
      tradeId: paperTrade.id,
      status: 'entry_executed',
    };
  }

  /**
   * Close an open paper trade position.
   */
  async executeExit(params: {
    backtestId: string;
    exitPrice: number;
    exitReason: string;
  }): Promise<{ pnlUsd: number; pnlPct: number; status: string }> {
    const { backtestId, exitPrice, exitReason } = params;

    // Find the open operation
    const operation = await db.backtestOperation.findFirst({
      where: { backtestId, exitPrice: null },
      orderBy: { entryTime: 'desc' },
    });

    if (!operation) {
      throw new Error(`No open position found for backtest: ${backtestId}`);
    }

    const holdTimeMin = Math.max(0, Math.round((Date.now() - operation.entryTime.getTime()) / 60000));
    const entryValue = operation.positionSizeUsd;
    const quantity = operation.quantity;
    const exitValue = quantity * exitPrice;

    // Calculate PnL after fees (0.3% each side)
    const entryFee = entryValue * 0.003;
    const exitFee = exitValue * 0.003;
    const grossPnl = exitValue - entryValue;
    const netPnl = grossPnl - entryFee - exitFee;
    const pnlPct = entryValue !== 0 ? (netPnl / entryValue) * 100 : 0;

    // MFE/MAE approximation
    const priceChange = (exitPrice - operation.entryPrice) / operation.entryPrice;
    const mfe = priceChange > 0 ? priceChange * 100 : 0;
    const mae = priceChange < 0 ? priceChange * 100 : 0;

    // Update the operation with exit data
    await db.backtestOperation.update({
      where: { id: operation.id },
      data: {
        exitPrice,
        exitTime: new Date(),
        exitReason,
        pnlUsd: netPnl,
        pnlPct,
        holdTimeMin,
        maxFavorableExc: mfe,
        maxAdverseExc: mae,
      },
    });

    // Update the backtest run
    const btRun = await db.backtestRun.findUnique({ where: { id: backtestId } });
    if (btRun) {
      const currentPnl = (btRun.totalPnl ?? 0) + netPnl;
      const currentTrades = (btRun.totalTrades ?? 0) + 1;
      const currentWins = netPnl > 0 ? (btRun.winTrades ?? 0) + 1 : (btRun.winTrades ?? 0);

      await db.backtestRun.update({
        where: { id: backtestId },
        data: {
          totalPnl: currentPnl,
          totalPnlPct: btRun.initialCapital !== 0 ? (currentPnl / btRun.initialCapital) * 100 : 0,
          finalCapital: btRun.initialCapital + currentPnl,
          totalTrades: currentTrades,
          winTrades: currentWins,
          lossTrades: currentTrades - currentWins,
          winRate: currentTrades > 0 ? currentWins / currentTrades : 0,
          status: 'COMPLETED',
          completedAt: new Date(),
          progress: 1,
        },
      });
    }

    return {
      pnlUsd: netPnl,
      pnlPct,
      status: 'exit_executed',
    };
  }

  /**
   * Get all open paper trade positions.
   */
  async getOpenPositions(): Promise<Array<{
    backtestId: string;
    systemId: string;
    systemName: string;
    tokenAddress: string;
    tokenSymbol: string;
    direction: string;
    entryPrice: number;
    entryTime: Date;
    positionSizeUsd: number;
    quantity: number;
    unrealizedPnl: number;
  }>> {
    const openOps = await db.backtestOperation.findMany({
      where: { exitPrice: null, backtest: { mode: 'PAPER' } },
      include: {
        backtest: { include: { system: true } },
      },
      orderBy: { entryTime: 'desc' },
    });

    return openOps.map(op => ({
      backtestId: op.backtestId,
      systemId: op.systemId,
      systemName: op.backtest.system.name,
      tokenAddress: op.tokenAddress,
      tokenSymbol: op.tokenSymbol || '',
      direction: op.operationType,
      entryPrice: op.entryPrice,
      entryTime: op.entryTime,
      positionSizeUsd: op.positionSizeUsd,
      quantity: op.quantity,
      unrealizedPnl: 0, // Calculated below with live price
    }));
  }

  /**
   * Get open positions with real-time unrealized PnL from DB token prices.
   */
  async getOpenPositionsLive(): Promise<Array<{
    backtestId: string;
    systemId: string;
    systemName: string;
    tokenAddress: string;
    tokenSymbol: string;
    direction: string;
    entryPrice: number;
    entryTime: Date;
    positionSizeUsd: number;
    quantity: number;
    unrealizedPnl: number;
    unrealizedPnlPct: number;
    currentPrice: number;
  }>> {
    const openOps = await db.backtestOperation.findMany({
      where: { exitPrice: null, backtest: { mode: 'PAPER' } },
      include: {
        backtest: { include: { system: true } },
      },
      orderBy: { entryTime: 'desc' },
    });

    if (openOps.length === 0) return [];

    // Fetch current prices for all token addresses in one query
    const tokenAddresses = [...new Set(openOps.map(op => op.tokenAddress))];
    const tokens = await db.token.findMany({
      where: { address: { in: tokenAddresses } },
      select: { address: true, priceUsd: true, symbol: true },
    });
    const priceMap = new Map(tokens.map(t => [t.address, t.priceUsd]));

    return openOps.map(op => {
      const currentPrice = priceMap.get(op.tokenAddress) ?? op.entryPrice;
      const isLong = op.operationType === 'LONG' || op.operationType === 'BUY';
      const priceChange = isLong
        ? (currentPrice - op.entryPrice) / op.entryPrice
        : (op.entryPrice - currentPrice) / op.entryPrice;
      const unrealizedPnl = op.positionSizeUsd * priceChange;
      const unrealizedPnlPct = priceChange * 100;

      return {
        backtestId: op.backtestId,
        systemId: op.systemId,
        systemName: op.backtest.system.name,
        tokenAddress: op.tokenAddress,
        tokenSymbol: op.tokenSymbol || '',
        direction: op.operationType,
        entryPrice: op.entryPrice,
        entryTime: op.entryTime,
        positionSizeUsd: op.positionSizeUsd,
        quantity: op.quantity,
        unrealizedPnl,
        unrealizedPnlPct,
        currentPrice,
      };
    });
  }

  /**
   * Get trade history (closed positions) for a system.
   */
  async getTradeHistory(systemId?: string, limit: number = 50): Promise<Array<{
    id: string;
    systemId: string;
    systemName: string;
    tokenAddress: string;
    tokenSymbol: string;
    direction: string;
    entryPrice: number;
    exitPrice: number;
    entryTime: Date;
    exitTime: Date;
    pnlUsd: number | null;
    pnlPct: number | null;
    holdTimeMin: number | null;
    exitReason: string | null;
    mode: string;
  }>> {
    const where: Record<string, unknown> = {
      exitPrice: { not: null },
    };
    if (systemId) where.systemId = systemId;

    const operations = await db.backtestOperation.findMany({
      where,
      include: {
        backtest: { include: { system: true } },
      },
      orderBy: { exitTime: 'desc' },
      take: limit,
    });

    return operations.map(op => ({
      id: op.id,
      systemId: op.systemId,
      systemName: op.backtest.system.name,
      tokenAddress: op.tokenAddress,
      tokenSymbol: op.tokenSymbol || '',
      direction: op.operationType,
      entryPrice: op.entryPrice,
      exitPrice: op.exitPrice ?? 0,
      entryTime: op.entryTime,
      exitTime: op.exitTime ?? new Date(),
      pnlUsd: op.pnlUsd,
      pnlPct: op.pnlPct,
      holdTimeMin: op.holdTimeMin,
      exitReason: op.exitReason,
      mode: op.backtest.mode,
    }));
  }
}

// Singleton
export const strategyEvolutionEngine = new StrategyEvolutionEngine();
