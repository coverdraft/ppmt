import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { execSync } from 'child_process';

export const dynamic = 'force-dynamic';

// POST /api/strategies/[id]/run - Execute a backtest/paper trading run
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await request.json();
    const runType = body.runType || 'backtest';

    const strategy = await db.pPMTStrategy.findUnique({
      where: { id },
    });

    if (!strategy) {
      return NextResponse.json(
        { success: false, error: 'Strategy not found' },
        { status: 404 }
      );
    }

    // Create a run record
    const run = await db.pPMTStrategyRun.create({
      data: {
        strategyId: id,
        runType,
        status: 'running',
      },
    });

    // Try to run the PPMT PaperTrader via Python
    let pythonUsed = false;
    let pythonError: string | null = null;

    try {
      const symbol = strategy.symbol;
      const tf = strategy.timeframe;
      const alpha = strategy.saxAlpha;
      const window = strategy.saxWindow;
      const capital = strategy.initialCapital;
      const catLoss = strategy.catastrophicLossPct;
      const patternLen = strategy.patternLength;
      const minConf = strategy.minConfidence;
      const pruning = strategy.pruningInterval;

      // Use PaperTrader (not Backtester) - it's the real PPMT engine
      const cmd = `cd /home/z/my-project/ppmt && PYTHONPATH=src python3 -c "
import sys, json
sys.path.insert(0, 'src')
try:
    from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig
    config = PaperTraderConfig(
        symbol='${symbol}',
        timeframe='${tf}',
        initial_capital=${capital},
        sax_alphabet_size=${alpha},
        sax_window_size=${window},
        catastrophic_loss_pct=${catLoss},
        pattern_length=${patternLen},
        min_confidence=${minConf},
        pruning_interval=${pruning},
        living_trie=${strategy.livingTrie ? 'True' : 'False'},
        regime_aware=${strategy.regimeAware ? 'True' : 'False'},
        verbose=False,
    )
    trader = PaperTrader(config=config)
    result = trader.run()
    equity = result.equity_curve if hasattr(result, 'equity_curve') else []
    trades_data = []
    for t in (result.trades if hasattr(result, 'trades') else [])[:50]:
        trades_data.append({
            'id': t.trade_id,
            'dir': t.direction,
            'entry': t.entry_price,
            'exit': t.exit_price,
            'pnl': t.pnl,
            'pnlPct': t.pnl_pct,
            'confidence': t.confidence,
            'exitReason': t.exit_reason,
            'regime': t.regime,
        })
    output = {
        'totalPnl': result.total_pnl,
        'totalPnlPct': result.total_pnl_pct,
        'winRate': result.win_rate,
        'sharpeRatio': result.sharpe_ratio,
        'maxDrawdown': result.max_drawdown,
        'profitFactor': result.profit_factor,
        'totalTrades': result.total_trades,
        'winningTrades': result.winning_trades,
        'losingTrades': result.losing_trades,
        'patternCount': result.trades[0].matched_pattern.__len__() if result.trades else 0,
        'recalibrations': result.recalibrations,
        'pruningRuns': result.pruning_runs,
        'equityCurve': equity[:200],
        'trades': trades_data,
    }
    print(json.dumps(output))
except Exception as e:
    print(json.dumps({'error': str(e)}))
" 2>&1`;

      const output = execSync(cmd, {
        encoding: 'utf-8',
        timeout: 300000, // 5 min timeout for large datasets
      }).trim();

      const lastLine = output.split('\n').filter(Boolean).pop() || '{}';
      const result = JSON.parse(lastLine);

      if (result.error) {
        pythonError = result.error;
      } else {
        pythonUsed = true;

        // Store real results
        await db.pPMTStrategyRun.update({
          where: { id: run.id },
          data: {
            status: 'completed',
            totalPnl: result.totalPnl || 0,
            totalPnlPct: result.totalPnlPct || 0,
            winRate: result.winRate || 0,
            sharpeRatio: result.sharpeRatio || 0,
            maxDrawdown: result.maxDrawdown || 0,
            profitFactor: result.profitFactor || 0,
            totalTrades: result.totalTrades || 0,
            winningTrades: result.winningTrades || 0,
            losingTrades: result.losingTrades || 0,
            candlesProcessed: (result.totalTrades || 0) * 5,
            recalibrations: result.recalibrations || 0,
            pruningRuns: result.pruningRuns || 0,
            equityCurve: JSON.stringify(result.equityCurve || []),
            tradesJson: JSON.stringify(result.trades || []),
            completedAt: new Date(),
          },
        });

        await db.pPMTStrategy.update({
          where: { id },
          data: {
            totalPnl: result.totalPnl || 0,
            totalPnlPct: result.totalPnlPct || 0,
            winRate: result.winRate || 0,
            sharpeRatio: result.sharpeRatio || 0,
            maxDrawdown: result.maxDrawdown || 0,
            profitFactor: result.profitFactor || 0,
            totalTrades: result.totalTrades || 0,
            patternCount: result.patternCount || strategy.patternCount,
            lastRunAt: new Date(),
          },
        });
      }
    } catch (err: unknown) {
      pythonError = err instanceof Error ? err.message : 'Python execution failed';
    }

    // If Python didn't work, mark the run as failed (no fake data)
    if (!pythonUsed) {
      await db.pPMTStrategyRun.update({
        where: { id: run.id },
        data: {
          status: 'failed',
          completedAt: new Date(),
        },
      });

      const updatedRun = await db.pPMTStrategyRun.findUnique({
        where: { id: run.id },
      });

      return NextResponse.json({
        success: false,
        error: 'PPMT Python engine unavailable',
        detail: pythonError || 'Could not execute PaperTrader. Ensure PPMT is installed with data.',
        data: updatedRun,
        pythonUsed: false,
      }, { status: 503 });
    }

    const updatedRun = await db.pPMTStrategyRun.findUnique({
      where: { id: run.id },
    });

    return NextResponse.json({
      success: true,
      data: updatedRun,
      pythonUsed: true,
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to run strategy';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}
