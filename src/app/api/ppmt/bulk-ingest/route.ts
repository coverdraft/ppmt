import { NextResponse } from 'next/server';
import { execSync } from 'child_process';

export const dynamic = 'force-dynamic';

interface BulkIngestRequest {
  pairs?: string[];
  timeframes?: string[];
  days?: number;
}

interface IngestResult {
  symbol: string;
  timeframe: string;
  success: boolean;
  candle_count: number;
  error: string;
}

interface BuildResult {
  symbol: string;
  timeframe: string;
  success: boolean;
  pattern_count: number;
  error: string;
}

interface BulkIngestSummary {
  total_pairs: number;
  total_timeframes: number;
  total_requests: number;
  successful_ingests: number;
  failed_ingests: number;
  total_candles: number;
  total_patterns: number;
  successful_builds: number;
  failed_builds: number;
  failed_pairs: string[];
  elapsed_seconds: number;
  results: IngestResult[];
  build_results: BuildResult[];
}

export async function POST(request: Request) {
  try {
    const body: BulkIngestRequest = await request.json();
    const { pairs, timeframes, days = 365 } = body;

    // Build the command
    const args: string[] = [];

    if (pairs && pairs.length > 0) {
      args.push(`--pairs "${pairs.join(',')}"`);
    }

    if (timeframes && timeframes.length > 0) {
      args.push(`--timeframes "${timeframes.join(',')}"`);
    }

    args.push(`--days ${days}`);

    const cmd = `cd /home/z/my-project/ppmt && python -m ppmt.scripts.bulk_ingest ${args.join(' ')}`;

    // Use execSync with a 5-minute timeout since bulk ingest can take a while
    // 20 pairs × 3 timeframes = 60 requests × ~1s each ≈ 60s+ for ingest + build time
    const output = execSync(cmd, {
      timeout: 300000, // 5 minutes
      encoding: 'utf-8',
      maxBuffer: 10 * 1024 * 1024, // 10MB buffer for large output
    });

    // Parse the summary from stdout
    // The Python script prints a structured summary at the end
    const summary = parseSummaryFromOutput(output);

    return NextResponse.json({
      success: true,
      results: summary.results,
      buildResults: summary.build_results,
      summary: {
        totalPairs: summary.total_pairs,
        totalCandles: summary.total_candles,
        totalPatterns: summary.total_patterns,
        successfulIngests: summary.successful_ingests,
        failedIngests: summary.failed_ingests,
        successfulBuilds: summary.successful_builds,
        failedBuilds: summary.failed_builds,
        failedPairs: summary.failed_pairs,
        elapsedSeconds: summary.elapsed_seconds,
      },
      output: output.slice(-2000), // Last 2000 chars of output for debugging
    });
  } catch (error: any) {
    console.error('Bulk ingest error:', error);

    // Try to extract useful info from the error output
    const stdout = error.stdout?.toString() || '';
    const stderr = error.stderr?.toString() || '';
    const errorMessage = stderr || error.message || 'Unknown error';

    return NextResponse.json({
      success: false,
      error: errorMessage.slice(0, 500),
      output: stdout.slice(-1000),
      summary: {
        totalPairs: 0,
        totalCandles: 0,
        totalPatterns: 0,
        successfulIngests: 0,
        failedIngests: 0,
        successfulBuilds: 0,
        failedBuilds: 0,
        failedPairs: [],
        elapsedSeconds: 0,
      },
    }, { status: 500 });
  }
}

/**
 * Parse the Python script output to extract summary statistics.
 * The script prints lines like:
 *   Total pairs:       20
 *   Total candles:     52,340
 * etc.
 */
function parseSummaryFromOutput(output: string): BulkIngestSummary {
  const summary: BulkIngestSummary = {
    total_pairs: 0,
    total_timeframes: 0,
    total_requests: 0,
    successful_ingests: 0,
    failed_ingests: 0,
    total_candles: 0,
    total_patterns: 0,
    successful_builds: 0,
    failed_builds: 0,
    failed_pairs: [],
    elapsed_seconds: 0,
    results: [],
    build_results: [],
  };

  const lines = output.split('\n');
  for (const line of lines) {
    const trimmed = line.trim();

    // Parse "Total pairs:       20"
    if (trimmed.startsWith('Total pairs:')) {
      summary.total_pairs = parseInt(trimmed.split(':')[1]?.trim().replace(/,/g, '') || '0', 10);
    }
    if (trimmed.startsWith('Total requests:')) {
      summary.total_requests = parseInt(trimmed.split(':')[1]?.trim().replace(/,/g, '') || '0', 10);
    }
    if (trimmed.startsWith('Successful:') && !trimmed.includes('Build')) {
      summary.successful_ingests = parseInt(trimmed.split(':')[1]?.trim().replace(/,/g, '') || '0', 10);
    }
    if (trimmed.startsWith('Failed:') && !trimmed.includes('Build') && !trimmed.includes('pairs')) {
      summary.failed_ingests = parseInt(trimmed.split(':')[1]?.trim().replace(/,/g, '') || '0', 10);
    }
    if (trimmed.startsWith('Total candles:')) {
      summary.total_candles = parseInt(trimmed.split(':')[1]?.trim().replace(/,/g, '') || '0', 10);
    }
    if (trimmed.startsWith('Total patterns:')) {
      summary.total_patterns = parseInt(trimmed.split(':')[1]?.trim().replace(/,/g, '') || '0', 10);
    }
    if (trimmed.startsWith('Tries built:')) {
      summary.successful_builds = parseInt(trimmed.split(':')[1]?.trim().replace(/,/g, '') || '0', 10);
    }
    if (trimmed.startsWith('Build failures:')) {
      summary.failed_builds = parseInt(trimmed.split(':')[1]?.trim().replace(/,/g, '') || '0', 10);
    }
    if (trimmed.startsWith('Failed pairs:')) {
      const pairsStr = trimmed.split(':')[1]?.trim() || '';
      if (pairsStr) {
        summary.failed_pairs = pairsStr.split(',').map(p => p.trim()).filter(Boolean);
      }
    }
    if (trimmed.startsWith('Elapsed:')) {
      summary.elapsed_seconds = parseFloat(trimmed.split(':')[1]?.trim().replace('s', '') || '0');
    }
  }

  // Also parse individual results from lines like "✓ BTC/USDT 1h: 8760 candles"
  for (const line of lines) {
    const match = line.match(/✓\s+(\S+)\s+(\S+):\s+(\d+)\s+candles/);
    if (match) {
      summary.results.push({
        symbol: match[1],
        timeframe: match[2],
        success: true,
        candle_count: parseInt(match[3], 10),
        error: '',
      });
    }

    const failMatch = line.match(/✗\s+(\S+)\s+(\S+):\s+(.*)/);
    if (failMatch) {
      summary.results.push({
        symbol: failMatch[1],
        timeframe: failMatch[2],
        success: false,
        candle_count: 0,
        error: failMatch[3],
      });
    }
  }

  return summary;
}
