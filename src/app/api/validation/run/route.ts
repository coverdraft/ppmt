import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/validation/run
 *
 * One-click validation suite that runs the PPMT Python engine
 * to perform P0 (OOS), P1 (Monte Carlo), and P2 (Walk-Forward)
 * validation on a trading symbol.
 *
 * The route executes the Python validator via subprocess and returns
 * the JSON verdict directly.
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const {
      symbol = 'BTC/USDT',
      trainRatio = 0.7,
      mcSimulations = 1000,
      wfWindows = 5,
      wfTrainRatio = 0.7,
      patternLength = 5,
      forwardWindow = 5,
      positionSize = 1.0,
      ruinThreshold = 0.5,
      seed = 42,
    } = body as {
      symbol?: string;
      trainRatio?: number;
      mcSimulations?: number;
      wfWindows?: number;
      wfTrainRatio?: number;
      patternLength?: number;
      forwardWindow?: number;
      positionSize?: number;
      ruinThreshold?: number;
      seed?: number;
    };

    if (!symbol) {
      return NextResponse.json(
        { data: null, error: 'symbol is required' },
        { status: 400 },
      );
    }

    // Use ppmt CLI validate-all --json-output for reliable subprocess execution
    // This avoids path issues and uses the installed ppmt package directly
    const args = [
      '-m', 'ppmt.cli.main',
      'validate-all',
      '--symbol', symbol,
      '--train-ratio', String(trainRatio),
      '--mc-simulations', String(mcSimulations),
      '--wf-windows', String(wfWindows),
      '--pattern-length', String(patternLength),
      '--seed', String(seed),
      '--capital', String(10000),
      '--json-output',
    ];

    const result = await executePythonArgs(args);

    if (result.error) {
      return NextResponse.json(
        { data: null, error: result.error },
        { status: 500 },
      );
    }

    return NextResponse.json({ data: result.data });
  } catch (error) {
    console.error('Error running validation suite:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to run validation suite' },
      { status: 500 },
    );
  }
}

async function executePythonArgs(
  args: string[],
): Promise<{ data: Record<string, unknown> | null; error: string | null }> {
  const { execFile } = require('child_process');

  return new Promise((resolve) => {
    execFile(
      'python3',
      args,
      {
        timeout: 300_000, // 5 minutes max for full validation
        maxBuffer: 10 * 1024 * 1024, // 10MB buffer for large results
        cwd: '/home/z/my-project/ppmt',
      },
      (error: Error | null, stdout: string, stderr: string) => {
        if (error) {
          console.error('Python validation error:', stderr || error.message);
          resolve({
            data: null,
            error: `Python engine error: ${stderr?.slice(0, 500) || error.message}`,
          });
          return;
        }

        try {
          const data = JSON.parse(stdout.trim());
          if (data.error) {
            resolve({ data: null, error: data.error });
            return;
          }
          resolve({ data, error: null });
        } catch (parseErr) {
          console.error('JSON parse error:', stdout.slice(0, 500));
          resolve({
            data: null,
            error: 'Failed to parse validation results',
          });
        }
      },
    );
  });
}
