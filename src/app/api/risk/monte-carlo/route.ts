import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/risk/monte-carlo
 *
 * Run a Monte Carlo simulation on the backtest trades of a trading system.
 *
 * Request body:
 *   - systemId (required)  — ID of the TradingSystem whose backtest operations to use
 *   - simulations (optional, default 1000) — number of Monte Carlo paths
 *   - seed (optional, default 42) — PRNG seed for reproducibility
 *   - initialCapital (optional, default 10000) — starting capital per simulation
 *   - confidenceLevels (optional, default [5, 25, 50, 75, 95]) — percentiles
 *   - ruinThreshold (optional, default 0.5) — equity fraction that defines ruin
 *
 * The endpoint fetches BacktestOperation records for the given systemId,
 * extracts their pnlPct values (converted to fractions), and runs the
 * Monte Carlo simulator.
 */
export async function POST(request: NextRequest) {
  try {
    const mcModule = await import('@/lib/services/risk/monte-carlo-simulator');
    const monteCarloSimulator = mcModule.monteCarloSimulator;

    const body = await request.json();
    const {
      systemId,
      simulations = 1000,
      seed = 42,
      initialCapital = 10_000,
      confidenceLevels = [5, 25, 50, 75, 95],
      ruinThreshold = 0.5,
    } = body as {
      systemId?: string;
      simulations?: number;
      seed?: number;
      initialCapital?: number;
      confidenceLevels?: number[];
      ruinThreshold?: number;
    };

    // --- Validate required parameter ---
    if (!systemId) {
      return NextResponse.json(
        { data: null, error: 'systemId is required' },
        { status: 400 },
      );
    }

    // --- Validate numeric parameters ---
    if (simulations < 1 || simulations > 100_000) {
      return NextResponse.json(
        { data: null, error: 'simulations must be between 1 and 100,000' },
        { status: 400 },
      );
    }

    if (ruinThreshold <= 0 || ruinThreshold >= 1) {
      return NextResponse.json(
        { data: null, error: 'ruinThreshold must be between 0 and 1 (exclusive)' },
        { status: 400 },
      );
    }

    if (initialCapital <= 0) {
      return NextResponse.json(
        { data: null, error: 'initialCapital must be positive' },
        { status: 400 },
      );
    }

    // --- Fetch backtest operations from the database ---
    const { db } = await import('@/lib/db');

    const operations = await db.backtestOperation.findMany({
      where: {
        systemId,
        pnlPct: { not: null }, // only closed trades with a PnL
      },
      orderBy: { entryTime: 'asc' },
      select: {
        id: true,
        pnlPct: true,
        tokenSymbol: true,
        operationType: true,
      },
    });

    if (operations.length === 0) {
      return NextResponse.json(
        {
          data: null,
          error:
            'No backtest operations with PnL data found for this system. Run a backtest first.',
        },
        { status: 404 },
      );
    }

    // --- Extract PnL % as fractions ---
    // The DB stores pnlPct as a percentage (e.g. 5.0 means +5%).
    // Convert to fraction (e.g. 0.05) for the simulator.
    const tradesPnlPct: number[] = [];
    let skippedCount = 0;

    for (const op of operations) {
      if (op.pnlPct != null && isFinite(op.pnlPct)) {
        tradesPnlPct.push(op.pnlPct / 100);
      } else {
        skippedCount++;
      }
    }

    if (tradesPnlPct.length === 0) {
      return NextResponse.json(
        {
          data: null,
          error: 'All backtest operations had invalid or missing PnL data.',
        },
        { status: 404 },
      );
    }

    // --- Run Monte Carlo simulation ---
    const result = monteCarloSimulator.simulate(tradesPnlPct, {
      simulations,
      seed,
      initialCapital,
      confidenceLevels,
      ruinThreshold,
    });

    // --- Generate human-readable summary ---
    const summary = monteCarloSimulator.generateSummary(result);

    // --- Return results ---
    return NextResponse.json({
      data: {
        // Input metadata
        systemId,
        tradeCount: result.tradeCount,
        totalOperationsFound: operations.length,
        skippedOperations: skippedCount,

        // Configuration used
        config: {
          simulations: result.config.simulations,
          seed: result.config.seed,
          initialCapital: result.config.initialCapital,
          ruinThreshold: result.config.ruinThreshold,
          confidenceLevels: result.config.confidenceLevels,
        },

        // Confidence intervals
        equityPercentiles: result.equityPercentiles,
        drawdownPercentiles: result.drawdownPercentiles,
        sharpePercentiles: result.sharpePercentiles,
        winRatePercentiles: result.winRatePercentiles,
        profitFactorPercentiles: result.profitFactorPercentiles,

        // Key risk metrics
        probabilityOfProfit: Math.round(result.probabilityOfProfit * 10000) / 10000,
        riskOfRuin: Math.round(result.riskOfRuin * 10000) / 10000,
        p95MaxDrawdown: Math.round(result.p95MaxDrawdown * 10000) / 10000,
        meanFinalEquity: Math.round(result.meanFinalEquity * 100) / 100,
        medianFinalEquity: Math.round(result.medianFinalEquity * 100) / 100,
        stdDevFinalEquity: Math.round(result.stdDevFinalEquity * 100) / 100,

        // Original (unshuffled) path for comparison
        originalMetrics: {
          finalEquity: Math.round(result.originalMetrics.finalEquity * 100) / 100,
          maxDrawdown: Math.round(result.originalMetrics.maxDrawdown * 10000) / 10000,
          sharpeRatio: Math.round(result.originalMetrics.sharpeRatio * 1000) / 1000,
          winRate: Math.round(result.originalMetrics.winRate * 10000) / 10000,
          profitFactor:
            result.originalMetrics.profitFactor === Infinity
              ? null
              : Math.round(result.originalMetrics.profitFactor * 100) / 100,
          hitRuin: result.originalMetrics.hitRuin,
        },

        // Human-readable summary
        summary,
        generatedAt: result.generatedAt,
      },
    });
  } catch (error) {
    console.error('Error running Monte Carlo simulation:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to run Monte Carlo simulation' },
      { status: 500 },
    );
  }
}
