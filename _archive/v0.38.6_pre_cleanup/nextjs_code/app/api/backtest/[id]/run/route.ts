import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

/**
 * POST /api/backtest/[id]/run
 * Execute the backtest simulation with REAL data from the OHLCV pipeline.
 *
 * FLOW:
 * 1. Fetch backtest config from DB
 * 2. Load real TokenData from PriceCandle DB via BacktestDataBridge
 * 2b. [AUTO-BACKFILL] If no data, auto-backfill OHLCV from CoinGecko before retrying
 * 3. Build BacktestConfig from system template + DB record
 * 4. Run the BacktestingEngine simulation with REAL data
 * 5. Update the BacktestRun record with results
 * 6. Create BacktestOperation records for each simulated trade
 * 7. Update trading system metrics
 */
export async function POST(
  _request: NextRequest,
  context: RouteContext,
) {
  try {
    const dbModule = await import('@/lib/db');
    const db = dbModule.db;
    const bteModule = await import('@/lib/services/backtesting/backtesting-engine');
    const backtestingEngine = bteModule.backtestingEngine;
    const tseModule = await import('@/lib/services/strategy/trading-system-engine');
    const tradingSystemEngine = tseModule.tradingSystemEngine;
    const bdbModule = await import('@/lib/services/backtesting/backtest-data-bridge');
    const backtestDataBridge = bdbModule.backtestDataBridge;
    const opModule = await import('@/lib/services/data-sources/ohlcv-pipeline');
    const ohlcvPipeline = opModule.ohlcvPipeline;
    type BacktestConfig = import('@/lib/services/backtesting/backtesting-engine').BacktestConfig;

    const { id } = await context.params;

    // Fetch backtest run
    const backtest = await db.backtestRun.findUnique({
      where: { id },
      include: { system: true },
    });

    if (!backtest) {
      return NextResponse.json(
        { data: null, error: 'Backtest not found' },
        { status: 404 },
      );
    }

    // Check if already running or completed
    if (backtest.status === 'RUNNING') {
      return NextResponse.json(
        { data: null, error: 'Backtest is already running' },
        { status: 409 },
      );
    }

    if (backtest.status === 'COMPLETED') {
      return NextResponse.json(
        { data: null, error: 'Backtest already completed. Delete and recreate to run again.' },
        { status: 409 },
      );
    }

    // Mark as running
    await db.backtestRun.update({
      where: { id },
      data: {
        status: 'RUNNING',
        progress: 0.05,
        startedAt: new Date(),
      },
    });

    try {
      // Build system template from DB record or use engine template
      const systemTemplate = tradingSystemEngine.getTemplate(backtest.system.name) ??
        tradingSystemEngine.createSystemFromTemplate(
          tradingSystemEngine.getAllTemplateNames()[0],
          {
            name: backtest.system.name,
            category: backtest.system.category as 'ALPHA_HUNTER',
          },
        );

      // === LOAD REAL DATA FROM PriceCandle DB ===
      // This was the critical fix — previously passed empty []
      let tokenData = await backtestDataBridge.loadTokensForBacktest({
        startDate: backtest.periodStart,
        endDate: backtest.periodEnd,
        timeframe: systemTemplate.primaryTimeframe,
        chain: systemTemplate.assetFilter?.chains?.[0] ?? 'SOL',
        minCandles: 20,
        assetFilter: systemTemplate.assetFilter,
        maxTokens: 10,
        includeMetrics: true,
      });

      // Update progress after data load
      await db.backtestRun.update({
        where: { id },
        data: { progress: 0.2 },
      });

      // === AUTO-BACKFILL: Si no hay datos de velas, rellenar automáticamente desde CoinGecko ===
      if (tokenData.length === 0) {
        console.info('[backtest-run] No hay datos OHLCV — iniciando auto-backfill desde CoinGecko...');

        // Determinar la cadena del backtest (por defecto SOL)
        const backtestChain = systemTemplate.assetFilter?.chains?.[0] ?? 'SOL';

        // Obtener tokens para backfill: primero intentar con el filtro del sistema,
        // luego los top tokens por volumen desde la DB
        let tokensToBackfill = await db.token.findMany({
          where: {
            ...(backtestChain !== 'ALL' ? { chain: backtestChain } : {}),
            volume24h: { gt: 0 },
          },
          orderBy: { volume24h: 'desc' },
          take: 10,
          select: { address: true, chain: true, symbol: true },
        });

        // Si no hay tokens en la DB, usar IDs conocidos de CoinGecko para las monedas principales
        if (tokensToBackfill.length === 0) {
          const wellKnownTokens = [
            { address: 'bitcoin', chain: 'BTC', symbol: 'BTC' },
            { address: 'ethereum', chain: 'ETH', symbol: 'ETH' },
            { address: 'solana', chain: 'SOL', symbol: 'SOL' },
            { address: 'binancecoin', chain: 'BSC', symbol: 'BNB' },
            { address: 'ripple', chain: 'XRP', symbol: 'XRP' },
            { address: 'cardano', chain: 'ADA', symbol: 'ADA' },
            { address: 'dogecoin', chain: 'DOGE', symbol: 'DOGE' },
            { address: 'polkadot', chain: 'DOT', symbol: 'DOT' },
            { address: 'chainlink', chain: 'LINK', symbol: 'LINK' },
            { address: 'uniswap', chain: 'UNI', symbol: 'UNI' },
          ];
          tokensToBackfill = wellKnownTokens.slice(0, 10);
        }

        const totalTokens = tokensToBackfill.length;
        console.info(`[backtest-run] Backfill programado para ${totalTokens} tokens`);

        // Backfill cada token con rate limiting (200ms entre cada uno)
        for (let i = 0; i < tokensToBackfill.length; i++) {
          const token = tokensToBackfill[i];

          // Actualizar progreso del backtest para mostrar el backfill
          await db.backtestRun.update({
            where: { id },
            data: {
              progress: 0.2 + (i / totalTokens) * 0.3, // Progreso de 0.2 a 0.5
              errorLog: `Backfilling OHLCV data... token ${i + 1}/${totalTokens} (${token.symbol || token.address})`,
            },
          });

          try {
            console.info(
              `[backtest-run] Backfill token ${i + 1}/${totalTokens}: ${token.symbol || token.address} (${token.chain})`,
            );
            await ohlcvPipeline.backfillToken(token.address, token.chain, ['1h', '4h', '1d']);
          } catch (backfillErr) {
            console.warn(
              `[backtest-run] Backfill falló para ${token.symbol || token.address}:`,
              backfillErr,
            );
            // Continuar con el siguiente token — no abortar el backfill completo
          }

          // Delay de 200ms entre tokens para respetar rate limits de CoinGecko
          if (i < tokensToBackfill.length - 1) {
            await new Promise((resolve) => setTimeout(resolve, 200));
          }
        }

        console.info('[backtest-run] Auto-backfill completado — reintentando carga de datos...');

        // Reintentar la carga de datos después del backfill
        tokenData = await backtestDataBridge.loadTokensForBacktest({
          startDate: backtest.periodStart,
          endDate: backtest.periodEnd,
          timeframe: systemTemplate.primaryTimeframe,
          chain: backtest.system.primaryTimeframe ? undefined : 'SOL',
          minCandles: 10, // Reducir mínimo para dar más oportunidades después del backfill
          assetFilter: systemTemplate.assetFilter,
          maxTokens: 10,
          includeMetrics: true,
        });

        // Validar la calidad de los datos obtenidos
        if (tokenData.length > 0) {
          const validation = backtestDataBridge.validateTokenData(tokenData);
          tokenData = validation.valid;

          if (validation.rejected.length > 0) {
            console.warn(
              `[backtest-run] Tokens rechazados por validación de calidad: ${validation.rejected.join(', ')}`,
            );
          }
        }

        // Actualizar progreso post-backfill
        await db.backtestRun.update({
          where: { id },
          data: { progress: 0.5 },
        });
      }

      if (tokenData.length === 0) {
        // Aún sin datos después del auto-backfill — mark as FAILED (not COMPLETED)
        // so it doesn't pollute ranking results or count as a valid backtest
        await db.backtestRun.update({
          where: { id },
          data: {
            status: 'FAILED',
            progress: 1,
            completedAt: new Date(),
            finalCapital: backtest.initialCapital,
            totalPnl: 0,
            totalPnlPct: 0,
            totalTrades: 0,
            errorLog: 'No token data available for backtesting. Auto-backfill attempted but insufficient data retrieved. Try running OHLCV backfill manually via /api/brain/scheduler.',
          },
        });

        return NextResponse.json({
          data: {
            id,
            status: 'FAILED',
            totalTrades: 0,
            message: 'No token data available after auto-backfill. Marked as FAILED to avoid polluting rankings. Run OHLCV backfill manually via /api/brain/scheduler or the OHLCV pipeline.',
            tokensAvailable: 0,
            autoBackfillAttempted: true,
          },
        });
      }

      // Build BacktestConfig
      const btConfig: BacktestConfig = {
        system: systemTemplate,
        mode: (backtest.mode as 'HISTORICAL' | 'PAPER' | 'FORWARD') || 'HISTORICAL',
        startDate: backtest.periodStart,
        endDate: backtest.periodEnd,
        initialCapital: backtest.initialCapital,
        feesPct: 0.003,
        slippagePct: 0.5,
        applySlippage: true,
        enforcePhaseFilter: true,
      };

      // Update progress
      await db.backtestRun.update({
        where: { id },
        data: { progress: 0.3 },
      });

      // Run the backtest simulation with REAL data
      const result = await backtestingEngine.runBacktest(
        btConfig,
        tokenData,
        async (progress) => {
          // Update progress periodically
          if (progress.barsProcessed % 500 === 0) {
            try {
              await db.backtestRun.update({
                where: { id },
                data: {
                  progress: Math.min(0.9, 0.3 + progress.percentComplete * 0.006),
                },
              });
            } catch {
              // Progress update failures are non-critical
            }
          }
        },
      );

      // Update progress
      await db.backtestRun.update({
        where: { id },
        data: { progress: 0.9 },
      });

      // Create BacktestOperation records for each trade
      const operationCreates = result.trades.map((trade) => ({
        backtestId: id,
        systemId: backtest.systemId,
        tokenAddress: trade.tokenAddress,
        tokenSymbol: trade.symbol,
        chain: systemTemplate.assetFilter?.chains?.[0] || 'SOL',
        tokenPhase: trade.phase,
        tokenAgeMinutes: 0,
        marketConditions: JSON.stringify({ timeframe: systemTemplate.primaryTimeframe }),
        tokenDnaSnapshot: JSON.stringify({}),
        traderComposition: JSON.stringify({}),
        bigDataContext: JSON.stringify({}),
        operationType: trade.direction,
        timeframe: systemTemplate.primaryTimeframe,
        entryPrice: trade.entryPrice,
        entryTime: trade.entryTime,
        entryReason: JSON.stringify({ reason: 'backtest_simulation', system: systemTemplate.name }),
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
        capitalAllocPct: trade.size / backtest.initialCapital * 100,
        allocationMethodUsed: systemTemplate.allocationMethod,
      }));

      // Batch create operations
      if (operationCreates.length > 0) {
        await db.backtestOperation.createMany({
          data: operationCreates,
        });
      }

      // Update the backtest run with results
      const updatedBacktest = await db.backtestRun.update({
        where: { id },
        data: {
          status: 'COMPLETED',
          progress: 1,
          completedAt: new Date(),
          finalCapital: result.finalEquity,
          totalPnl: result.finalEquity - result.initialCapital,
          totalPnlPct: result.totalReturnPct,
          annualizedReturn: result.annualizedReturnPct,
          benchmarkReturn: 0,
          alpha: result.annualizedReturnPct,
          totalTrades: result.totalTrades,
          winTrades: result.winningTrades,
          lossTrades: result.losingTrades,
          winRate: result.winRate,
          avgWin: result.avgWinPct,
          avgLoss: result.avgLossPct,
          profitFactor: result.profitFactor,
          expectancy: result.expectancy,
          maxDrawdown: result.maxDrawdown,
          maxDrawdownPct: result.maxDrawdownPct,
          sharpeRatio: result.sharpeRatio,
          sortinoRatio: result.sortinoRatio,
          calmarRatio: result.calmarRatio,
          recoveryFactor: result.recoveryFactor,
          avgHoldTimeMin: result.avgHoldTimeMin,
          marketExposurePct: result.totalTrades > 0 && result.avgHoldTimeMin > 0
            ? Math.min(100, (result.totalTrades * result.avgHoldTimeMin) / ((backtest.periodEnd.getTime() - backtest.periodStart.getTime()) / 60000) * 100)
            : 0,
          phaseResults: JSON.stringify(result.phaseBreakdown),
          timeframeResults: JSON.stringify({ primaryTimeframe: systemTemplate.primaryTimeframe }),
          operationTypeResults: JSON.stringify({}),
          allocationMethodResults: JSON.stringify({ method: systemTemplate.allocationMethod }),
        },
      });

      // Update trading system metrics with best results
      const system = await db.tradingSystem.findUnique({
        where: { id: backtest.systemId },
      });

      if (system) {
        const metricsUpdate: Record<string, unknown> = {
          totalBacktests: system.totalBacktests + 1,
        };

        if (result.sharpeRatio > system.bestSharpe) {
          metricsUpdate.bestSharpe = result.sharpeRatio;
        }
        if (result.winRate > system.bestWinRate) {
          metricsUpdate.bestWinRate = result.winRate;
        }
        if (result.totalReturnPct > system.bestPnlPct) {
          metricsUpdate.bestPnlPct = result.totalReturnPct;
        }

        // Update average hold time
        if (system.totalBacktests === 0) {
          metricsUpdate.avgHoldTimeMin = result.avgHoldTimeMin;
        } else {
          metricsUpdate.avgHoldTimeMin =
            (system.avgHoldTimeMin * system.totalBacktests + result.avgHoldTimeMin) /
            (system.totalBacktests + 1);
        }

        await db.tradingSystem.update({
          where: { id: backtest.systemId },
          data: metricsUpdate,
        });
      }

      return NextResponse.json({
        data: {
          id: updatedBacktest.id,
          status: updatedBacktest.status,
          totalTrades: result.totalTrades,
          winRate: result.winRate,
          sharpeRatio: result.sharpeRatio,
          totalPnlPct: result.totalReturnPct,
          maxDrawdownPct: result.maxDrawdownPct,
          profitFactor: result.profitFactor,
          finalCapital: result.finalEquity,
          operationCount: operationCreates.length,
          tokensAnalyzed: tokenData.length,
          overfittingScore: result.overfittingScore,
          parameterStability: result.parameterStability,
        },
      });
    } catch (simError) {
      // Mark as failed
      await db.backtestRun.update({
        where: { id },
        data: {
          status: 'FAILED',
          errorLog: simError instanceof Error ? simError.message : 'Unknown simulation error',
          completedAt: new Date(),
        },
      });

      throw simError;
    }
  } catch (error) {
    console.error('Error running backtest:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to run backtest' },
      { status: 500 },
    );
  }
}
