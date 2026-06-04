import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/brain/scheduler
 * Returns current scheduler status.
 * Primary source: SchedulerState from DB (survives restarts).
 * Supplemental: in-memory status from brainScheduler singleton for real-time task info.
 */
export async function GET() {
  try {
    const { brainScheduler } = await import('@/lib/services/brain/brain-scheduler');
    const { loadSchedulerState } = await import('@/lib/services/brain/scheduler-persistence');
    const { db } = await import('@/lib/db');

    // Get in-memory status from the singleton
    const memStatus = brainScheduler.getStatus();

    // Load persisted state from DB (authoritative for survival across restarts)
    const persistedState = await loadSchedulerState();

    // DB stats
    const [tokens, candles, signals, predictiveSignals] = await Promise.all([
      db.token.count().catch(() => 0),
      db.priceCandle.count().catch(() => 0),
      db.signal.count().catch(() => 0),
      db.predictiveSignal.count().catch(() => 0),
    ]);

    // Signal type breakdown
    const oneHourAgo = new Date(Date.now() - 3600000);
    const [smartMoneySignals, rugPullSignals, vShapeSignals, liquidityTrapSignals, patternSignals] = await Promise.all([
      db.signal.count({ where: { type: 'SMART_MONEY', createdAt: { gte: oneHourAgo } } }).catch(() => 0),
      db.signal.count({ where: { type: 'RUG_PULL', createdAt: { gte: oneHourAgo } } }).catch(() => 0),
      db.signal.count({ where: { type: 'V_SHAPE', createdAt: { gte: oneHourAgo } } }).catch(() => 0),
      db.signal.count({ where: { type: 'LIQUIDITY_TRAP', createdAt: { gte: oneHourAgo } } }).catch(() => 0),
      db.signal.count({ where: { type: 'PATTERN', createdAt: { gte: oneHourAgo } } }).catch(() => 0),
    ]);

    // Token with real liquidity
    const tokensWithLiquidity = await db.token.count({
      where: { liquidity: { gt: 0 } },
    }).catch(() => 0);

    return NextResponse.json({
      success: true,
      data: {
        // From brainScheduler singleton (in-memory, real-time)
        status: memStatus.status,
        uptime: memStatus.uptime,
        config: memStatus.config,
        tasks: memStatus.tasks,
        brainCycle: memStatus.brainCycle,
        capitalStrategy: memStatus.capitalStrategy,
        totalCyclesCompleted: memStatus.totalCyclesCompleted,
        lastError: memStatus.lastError,

        // From persisted state in DB (survives restarts)
        persisted: persistedState ? {
          status: persistedState.status,
          startedAt: persistedState.startedAt,
          stoppedAt: persistedState.stoppedAt,
          totalCycles: persistedState.totalCycles,
          lastCycleNumber: persistedState.lastCycleNumber,
          capitalUsd: persistedState.capitalUsd,
          initialCapitalUsd: persistedState.initialCapitalUsd,
          chain: persistedState.chain,
          scanLimit: persistedState.scanLimit,
          lastError: persistedState.lastError,
          taskStates: persistedState.taskStates,
          updatedAt: persistedState.updatedAt,
        } : null,

        // DB stats
        dbStats: {
          tokens,
          candles,
          signals,
          predictiveSignals,
          tokensWithLiquidity,
          signalBreakdown: {
            smartMoney: smartMoneySignals,
            rugPull: rugPullSignals,
            vShape: vShapeSignals,
            liquidityTrap: liquidityTrapSignals,
            patterns: patternSignals,
          },
        },
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}

/**
 * POST /api/brain/scheduler
 * Controls the brain scheduler: start, stop, pause, resume, run_cycle, update_config, auto_start
 *
 * Body: { action: string, params?: object }
 */
export async function POST(request: NextRequest) {
  try {
    const { brainScheduler } = await import('@/lib/services/brain/brain-scheduler');
    const { saveSchedulerState } = await import('@/lib/services/brain/scheduler-persistence');

    let body: any = {};
    try { body = await request.json(); } catch { /* no body */ }

    const { action, params } = body;

    switch (action) {
      case 'start': {
        const config: any = {};
        if (params?.capitalUsd) config.capitalUsd = Number(params.capitalUsd);
        if (params?.initialCapitalUsd) config.initialCapitalUsd = Number(params.initialCapitalUsd);
        if (params?.chain) config.chain = params.chain;
        if (params?.scanLimit) config.scanLimit = Number(params.scanLimit);

        // Update DB state to RUNNING before starting the in-memory scheduler
        // This ensures the DB record is set even if start() encounters an error
        try {
          await saveSchedulerState({
            status: 'RUNNING',
            config: config,
            lastCycleNumber: 0,
            totalCycles: 0,
            capitalUsd: config.capitalUsd ?? 10,
            initialCapitalUsd: config.initialCapitalUsd ?? 10,
            chain: config.chain ?? 'SOL',
            scanLimit: config.scanLimit ?? 250,
            startedAt: new Date(),
            lastError: null,
            taskStates: {},
          });
        } catch (dbErr) {
          console.warn('[SchedulerAPI] Pre-start DB state update failed:', dbErr);
        }

        const result = await brainScheduler.start(
          Object.keys(config).length > 0 ? config : undefined
        );

        return NextResponse.json({
          success: true,
          data: result,
        });
      }
      case 'stop': {
        // Update DB state to STOPPED
        try {
          await saveSchedulerState({
            status: 'STOPPED',
            config: brainScheduler.getConfig(),
            lastCycleNumber: 0,
            totalCycles: 0,
            capitalUsd: brainScheduler.getConfig().capitalUsd,
            initialCapitalUsd: brainScheduler.getConfig().initialCapitalUsd,
            chain: brainScheduler.getConfig().chain,
            scanLimit: brainScheduler.getConfig().scanLimit,
            startedAt: null,
            stoppedAt: new Date(),
            lastError: null,
            taskStates: {},
          });
        } catch (dbErr) {
          console.warn('[SchedulerAPI] Pre-stop DB state update failed:', dbErr);
        }

        const result = await brainScheduler.stop();
        return NextResponse.json({ success: true, data: result });
      }
      case 'pause': {
        const result = brainScheduler.pause();
        return NextResponse.json({ success: true, data: result });
      }
      case 'resume': {
        const result = brainScheduler.resume();
        return NextResponse.json({ success: true, data: result });
      }
      case 'run_cycle': {
        // Manual cycle trigger
        const result = await brainScheduler.runManualCycle();
        return NextResponse.json({ success: true, data: result });
      }
      case 'update_config': {
        const updates: any = {};
        if (params?.capitalUsd) updates.capitalUsd = Number(params.capitalUsd);
        if (params?.chain) updates.chain = params.chain;
        if (params?.scanLimit) updates.scanLimit = Number(params.scanLimit);
        brainScheduler.updateConfig(updates);
        return NextResponse.json({
          success: true,
          data: { message: 'Config updated', config: brainScheduler.getConfig() },
        });
      }
      case 'auto_start': {
        // Check if scheduler was previously running and auto-start it
        const previousState = await brainScheduler.getPreviousState();
        if (previousState.wasRunning && previousState.config) {
          const result = await brainScheduler.start(previousState.config);
          return NextResponse.json({
            success: true,
            data: {
              autoStarted: true,
              ...result,
              previousState: {
                startedAt: previousState.state?.startedAt,
                totalCycles: previousState.state?.totalCycles,
                capitalUsd: previousState.state?.capitalUsd,
              },
            },
          });
        }
        return NextResponse.json({
          success: true,
          data: { autoStarted: false, message: 'Scheduler was not previously running' },
        });
      }
      default:
        return NextResponse.json({ error: `Unknown action: ${action}` }, { status: 400 });
    }
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}

/**
 * DELETE /api/brain/scheduler
 * Stops the brain scheduler and updates DB state to STOPPED.
 */
export async function DELETE() {
  try {
    const { brainScheduler } = await import('@/lib/services/brain/brain-scheduler');
    const { saveSchedulerState } = await import('@/lib/services/brain/scheduler-persistence');

    // Update DB state to STOPPED before stopping the in-memory scheduler
    try {
      const currentConfig = brainScheduler.getConfig();
      await saveSchedulerState({
        status: 'STOPPED',
        config: currentConfig,
        lastCycleNumber: 0,
        totalCycles: 0,
        capitalUsd: currentConfig.capitalUsd,
        initialCapitalUsd: currentConfig.initialCapitalUsd,
        chain: currentConfig.chain,
        scanLimit: currentConfig.scanLimit,
        startedAt: null,
        stoppedAt: new Date(),
        lastError: null,
        taskStates: {},
      });
    } catch (dbErr) {
      console.warn('[SchedulerAPI] Pre-delete DB state update failed:', dbErr);
    }

    const result = await brainScheduler.stop();
    return NextResponse.json({
      success: true,
      data: result,
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
