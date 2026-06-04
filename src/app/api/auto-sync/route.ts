/**
 * Auto-Sync Scheduler — CryptoQuant Terminal
 *
 * Continuous real-time data synchronization scheduler that runs every 15 minutes.
 * Completely separate from the brain-scheduler. This focuses purely on data sync.
 *
 * Cycle Steps (in order):
 *   1. Token Refresh  — every cycle
 *   2. Trader Discovery — every cycle
 *   3. Candle Fetch    — every cycle
 *   4. DNA Computation — every 2nd cycle
 *   5. Pattern Detection — every 3rd cycle
 *   6. Signal Generation — every cycle
 *   7. Paper Trading Sync — every cycle
 *
 * All data comes from real APIs. No simulated data.
 * Individual step failures never stop the whole cycle.
 *
 * NOTE: Actual sync logic lives in @/lib/services/sync-shared.
 *       This file handles only scheduling, state management, and API routes.
 */

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

import { NextRequest, NextResponse } from 'next/server';
import { etherscanClient } from '@/lib/services/data-sources/etherscan-client';
import {
  refreshTokensFromDexScreener,
  discoverTradersFromEtherscan,
  estimateSolanaTraderActivity,
  fetchCandlesFromCoinGecko,
  computeTokenDNA,
  detectPatternsFromCandles,
  generateSignalsFromDNA,
} from '@/lib/services/execution/sync-shared';

// ============================================================
// TYPES
// ============================================================

interface CycleResult {
  tokensRefreshed: number;
  tradersDiscovered: number;
  tradersUpdated: number;
  candlesFetched: number;
  dnaComputed: number;
  patternsDetected: number;
  signalsGenerated: number;
  paperPositionsSynced: number;
  errors: string[];
}

interface CycleHistoryEntry {
  cycle: number;
  startedAt: Date;
  completedAt: Date;
  result: CycleResult;
}

interface AutoSyncState {
  isRunning: boolean;
  currentCycle: number;
  lastCycleAt: Date | null;
  nextCycleAt: Date | null;
  lastResult: CycleResult | null;
  cycleHistory: CycleHistoryEntry[];
}

// ============================================================
// MODULE-LEVEL STATE
// ============================================================

const SYNC_INTERVAL_MS = 15 * 60 * 1000; // 15 minutes
const MAX_HISTORY = 50; // keep last 50 cycles

let syncState: AutoSyncState = {
  isRunning: false,
  currentCycle: 0,
  lastCycleAt: null,
  nextCycleAt: null,
  lastResult: null,
  cycleHistory: [],
};

let syncTimer: ReturnType<typeof setInterval> | null = null;
let isCycleRunning = false; // concurrency guard

// ============================================================
// MAIN SYNC CYCLE
// ============================================================

async function runSyncCycle(): Promise<CycleResult> {
  const cycleNumber = syncState.currentCycle + 1;
  const startedAt = new Date();
  const allErrors: string[] = [];

  console.log(`[AutoSync] === Cycle ${cycleNumber} starting at ${startedAt.toISOString()} ===`);

  const result: CycleResult = {
    tokensRefreshed: 0,
    tradersDiscovered: 0,
    tradersUpdated: 0,
    candlesFetched: 0,
    dnaComputed: 0,
    patternsDetected: 0,
    signalsGenerated: 0,
    paperPositionsSynced: 0,
    errors: [],
  };

  try {
    // Step 1: Token Refresh (every cycle)
    console.log(`[AutoSync] Step 1: Token refresh`);
    const tokenResult = await refreshTokensFromDexScreener(100);
    result.tokensRefreshed = tokenResult.refreshed;
    allErrors.push(...tokenResult.errors);

    // Step 2a: Solana Trader Estimation (every cycle)
    console.log(`[AutoSync] Step 2a: Solana trader estimation`);
    const solResult = await estimateSolanaTraderActivity(10);
    result.tradersDiscovered += solResult.estimated;
    allErrors.push(...solResult.errors);

    // Step 2b: Ethereum Trader Discovery (every cycle)
    console.log(`[AutoSync] Step 2b: Ethereum trader discovery`);
    const ethResult = await discoverTradersFromEtherscan(10);
    result.tradersDiscovered += ethResult.discovered;
    result.tradersUpdated = ethResult.updated;
    allErrors.push(...ethResult.errors);

    // Step 3: Candle Fetch with Volume (every cycle)
    console.log(`[AutoSync] Step 3: Candle fetch`);
    const candleResult = await fetchCandlesFromCoinGecko(30);
    result.candlesFetched = candleResult.fetched;
    allErrors.push(...candleResult.errors);

    // Step 4: DNA Computation (every 2nd cycle)
    if (cycleNumber % 2 === 0) {
      console.log(`[AutoSync] Step 4: DNA computation`);
      const dnaResult = await computeTokenDNA();
      result.dnaComputed = dnaResult.computed;
      allErrors.push(...dnaResult.errors);
    } else {
      console.log(`[AutoSync] Step 4: Skipped (runs every 2nd cycle, current: ${cycleNumber})`);
    }

    // Step 5: Pattern Detection (every 3rd cycle)
    if (cycleNumber % 3 === 0) {
      console.log(`[AutoSync] Step 5: Pattern detection`);
      const patternResult = await detectPatternsFromCandles(20);
      result.patternsDetected = patternResult.detected;
      allErrors.push(...patternResult.errors);
    } else {
      console.log(`[AutoSync] Step 5: Skipped (runs every 3rd cycle, current: ${cycleNumber})`);
    }

    // Step 6: Signal Generation (every cycle)
    console.log(`[AutoSync] Step 6: Signal generation`);
    const signalResult = await generateSignalsFromDNA();
    result.signalsGenerated = signalResult.generated;
    allErrors.push(...signalResult.errors);

    // Step 7: Paper Trading Price Sync (every cycle)
    try {
      const { paperTradingEngine } = await import('@/lib/services/execution/paper-trading-engine');

      const openPositions = paperTradingEngine.getOpenPositions();
      if (openPositions.length > 0) {
        console.log(`[AutoSync] Step 7: Syncing prices for ${openPositions.length} paper trading positions`);
        const ptResult = await paperTradingEngine.syncOpenPositionPrices();
        result.paperPositionsSynced = ptResult.updated;
        console.log(`[AutoSync] Step 7 complete: ${ptResult.updated} positions updated, ${ptResult.errors} errors`);
      }
    } catch (err) {
      const msg = `Paper trading price sync failed: ${err instanceof Error ? err.message : String(err)}`;
      allErrors.push(msg);
      console.error(`[AutoSync] ${msg}`);
    }
  } catch (err) {
    const msg = `Cycle ${cycleNumber} unexpected error: ${err instanceof Error ? err.message : String(err)}`;
    allErrors.push(msg);
    console.error(`[AutoSync] ${msg}`);
  }

  result.errors = allErrors;

  // Update state
  syncState.currentCycle = cycleNumber;
  syncState.lastCycleAt = new Date();
  syncState.nextCycleAt = new Date(Date.now() + SYNC_INTERVAL_MS);
  syncState.lastResult = result;

  // Add to history (bounded)
  syncState.cycleHistory.push({
    cycle: cycleNumber,
    startedAt,
    completedAt: new Date(),
    result,
  });
  if (syncState.cycleHistory.length > MAX_HISTORY) {
    syncState.cycleHistory = syncState.cycleHistory.slice(-MAX_HISTORY);
  }

  console.log(
    `[AutoSync] === Cycle ${cycleNumber} complete === ` +
    `tokens: ${result.tokensRefreshed}, traders: ${result.tradersDiscovered}, ` +
    `candles: ${result.candlesFetched}, dna: ${result.dnaComputed}, ` +
    `patterns: ${result.patternsDetected}, signals: ${result.signalsGenerated}, ` +
    `paper: ${result.paperPositionsSynced}, errors: ${allErrors.length}`,
  );

  return result;
}

// ============================================================
// SCHEDULER CONTROL
// ============================================================

function startScheduler(): { started: boolean; message: string } {
  if (syncState.isRunning) {
    return { started: false, message: 'Auto-sync is already running' };
  }

  syncState.isRunning = true;
  syncState.nextCycleAt = new Date(Date.now() + 5000); // First cycle in 5 seconds

  console.log('[AutoSync] Scheduler started — first cycle in 5 seconds, then every 15 minutes');

  // Run first cycle after 5 second delay
  setTimeout(async () => {
    if (!syncState.isRunning) return;
    if (isCycleRunning) return;

    isCycleRunning = true;
    try {
      await runSyncCycle();
    } finally {
      isCycleRunning = false;
    }
  }, 5000);

  // Schedule recurring cycles every 15 minutes
  syncTimer = setInterval(async () => {
    if (!syncState.isRunning) return;
    if (isCycleRunning) {
      console.warn('[AutoSync] Previous cycle still running — skipping this interval');
      return;
    }

    isCycleRunning = true;
    try {
      await runSyncCycle();
    } finally {
      isCycleRunning = false;
    }
  }, SYNC_INTERVAL_MS);

  return { started: true, message: 'Auto-sync scheduler started' };
}

function stopScheduler(): { stopped: boolean; message: string } {
  if (!syncState.isRunning) {
    return { stopped: false, message: 'Auto-sync is not running' };
  }

  syncState.isRunning = false;
  syncState.nextCycleAt = null;

  if (syncTimer !== null) {
    clearInterval(syncTimer);
    syncTimer = null;
  }

  console.log('[AutoSync] Scheduler stopped');
  return { stopped: true, message: 'Auto-sync scheduler stopped' };
}

// ============================================================
// API ROUTES
// ============================================================

/**
 * GET /api/auto-sync — Returns current auto-sync status
 */
export async function GET() {
  try {
    return NextResponse.json({
      isRunning: syncState.isRunning,
      currentCycle: syncState.currentCycle,
      lastCycleAt: syncState.lastCycleAt,
      nextCycleAt: syncState.nextCycleAt,
      lastResult: syncState.lastResult,
      cycleHistory: syncState.cycleHistory.slice(-10), // Return last 10 cycles
      isCycleRunning,
      syncIntervalMs: SYNC_INTERVAL_MS,
      apiKeysConfigured: {
        etherscan: etherscanClient.hasApiKey(),
      },
    });
  } catch (err) {
    console.error('[AutoSync] GET error:', err);
    return NextResponse.json(
      { error: 'Failed to get auto-sync status', details: err instanceof Error ? err.message : String(err) },
      { status: 500 },
    );
  }
}

/**
 * POST /api/auto-sync — Body: { action: "start" | "stop" | "status" | "run" }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { action } = body;

    if (!action || !['start', 'stop', 'status', 'run'].includes(action)) {
      return NextResponse.json(
        { error: 'Invalid action. Must be one of: start, stop, status, run' },
        { status: 400 },
      );
    }

    switch (action) {
      case 'start': {
        const result = startScheduler();
        return NextResponse.json(result);
      }

      case 'stop': {
        const result = stopScheduler();
        return NextResponse.json(result);
      }

      case 'status': {
        return NextResponse.json({
          isRunning: syncState.isRunning,
          currentCycle: syncState.currentCycle,
          lastCycleAt: syncState.lastCycleAt,
          nextCycleAt: syncState.nextCycleAt,
          lastResult: syncState.lastResult,
          isCycleRunning,
          syncIntervalMs: SYNC_INTERVAL_MS,
          apiKeysConfigured: {
            etherscan: etherscanClient.hasApiKey(),
          },
        });
      }

      case 'run': {
        // Fire-and-forget: trigger a manual cycle
        if (isCycleRunning) {
          return NextResponse.json({
            triggered: false,
            message: 'A sync cycle is already running',
          });
        }

        // Start the cycle asynchronously (fire-and-forget)
        isCycleRunning = true;
        runSyncCycle()
          .then(() => {
            isCycleRunning = false;
          })
          .catch((err) => {
            console.error('[AutoSync] Manual cycle error:', err);
            isCycleRunning = false;
          });

        return NextResponse.json({
          triggered: true,
          message: 'Manual sync cycle started',
        });
      }

      default:
        return NextResponse.json({ error: 'Unknown action' }, { status: 400 });
    }
  } catch (err) {
    console.error('[AutoSync] POST error:', err);
    return NextResponse.json(
      { error: 'Failed to process auto-sync action', details: err instanceof Error ? err.message : String(err) },
      { status: 500 },
    );
  }
}
