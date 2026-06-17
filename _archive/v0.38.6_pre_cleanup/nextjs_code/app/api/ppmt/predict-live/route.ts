import { NextRequest, NextResponse } from 'next/server';
import { spawn } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

export const dynamic = 'force-dynamic';

const SIGNAL_DIR = '/home/z/my-project/ppmt/signals';
const PPMT_DIR = '/home/z/my-project/ppmt';
const DAEMON_SCRIPT = path.join(PPMT_DIR, 'signal_daemon.py');

// Stale threshold by timeframe (seconds)
const STALE_THRESHOLD: Record<string, number> = {
  '1m': 120,
  '5m': 600,
  '15m': 1800,
  '1h': 3600,
  '4h': 14400,
};
const DEFAULT_STALE = 600;

// In-flight prediction tracker to avoid duplicate spawns
const inflightPredictions = new Map<string, Promise<void>>();

/**
 * GET /api/ppmt/predict-live?symbol=BTC/USDT&timeframe=1h&refresh=1
 *
 * Reads the latest cached PPMT prediction signal.
 * If the cache is stale (or missing), triggers a fresh Python prediction
 * on-demand, then returns the result.
 *
 * Query params:
 *   symbol: string (e.g. "BTC/USDT")
 *   timeframe: string (e.g. "1h", "5m", "1m")
 *   refresh: if "1", force a fresh prediction regardless of cache age
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const symbol = searchParams.get('symbol') || 'BTC/USDT';
    const timeframe = searchParams.get('timeframe') || '1h';
    const forceRefresh = searchParams.get('refresh') === '1';

    const filename = `${symbol.replace('/', '_')}_${timeframe}.json`;
    const filepath = path.join(SIGNAL_DIR, filename);
    const threshold = STALE_THRESHOLD[timeframe] || DEFAULT_STALE;

    // Check if cache exists and is fresh enough
    let needsRefresh = forceRefresh;
    let cachedResult: any = null;

    if (fs.existsSync(filepath)) {
      try {
        const raw = fs.readFileSync(filepath, 'utf-8');
        cachedResult = JSON.parse(raw);
        const age = Date.now() / 1000 - (cachedResult.timestamp || 0);
        if (age > threshold) {
          needsRefresh = true;
        }
      } catch {
        needsRefresh = true;
      }
    } else {
      needsRefresh = true;
    }

    // If cache is stale, trigger a background refresh
    if (needsRefresh) {
      const key = `${symbol}@${timeframe}`;
      if (!inflightPredictions.has(key)) {
        const refreshPromise = runPythonPrediction(symbol, timeframe)
          .finally(() => inflightPredictions.delete(key));
        inflightPredictions.set(key, refreshPromise);

        // Wait for the prediction (with timeout)
        try {
          await Promise.race([
            refreshPromise,
            new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 30000)),
          ]);
        } catch {
          // If refresh fails/times out, we still return stale cache if available
        }

        // Re-read the cache after refresh
        if (fs.existsSync(filepath)) {
          try {
            const raw = fs.readFileSync(filepath, 'utf-8');
            cachedResult = JSON.parse(raw);
          } catch { /* keep old cachedResult */ }
        }
      }
    }

    if (!cachedResult) {
      return NextResponse.json({
        success: false,
        error: 'No signal available — prediction failed or not yet generated',
        hint: 'Run: cd /home/z/my-project/ppmt && PYTHONPATH=src python3 signal_daemon.py --once',
      }, { status: 404 });
    }

    if (cachedResult.error) {
      return NextResponse.json({
        success: false,
        error: cachedResult.error,
        errorType: cachedResult.errorType,
      }, { status: 500 });
    }

    const age = Date.now() / 1000 - (cachedResult.timestamp || 0);

    return NextResponse.json({
      success: true,
      data: cachedResult,
      meta: {
        ageSeconds: Math.round(age),
        isStale: age > threshold,
        source: needsRefresh ? 'fresh' : 'cache',
      },
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Failed to read signal';
    return NextResponse.json({ success: false, error: message }, { status: 500 });
  }
}

/**
 * Run a single Python prediction for a specific symbol/timeframe.
 * Uses the predict_live.py script which writes to the signals directory.
 */
function runPythonPrediction(symbol: string, timeframe: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const resultFile = path.join(SIGNAL_DIR, `${symbol.replace('/', '_')}_${timeframe}.json`);

    // Ensure signals directory exists
    fs.mkdirSync(SIGNAL_DIR, { recursive: true });

    const proc = spawn('python3', [
      path.join(PPMT_DIR, 'predict_live.py'),
      '--result-file', resultFile,
      '--symbol', symbol,
      '--timeframe', timeframe,
    ], {
      cwd: PPMT_DIR,
      env: { ...process.env, PYTHONPATH: path.join(PPMT_DIR, 'src') },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stderr = '';
    proc.stderr?.on('data', (d: Buffer) => { stderr += d.toString(); });

    proc.on('close', (code) => {
      if (code === 0 && fs.existsSync(resultFile)) {
        resolve();
      } else {
        reject(new Error(`Prediction failed (exit=${code}): ${stderr.slice(0, 200)}`));
      }
    });

    proc.on('error', (err) => reject(err));
  });
}
