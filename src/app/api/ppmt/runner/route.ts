import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const PYTHON_API = process.env.PPMT_API_URL || 'http://localhost:8430';

/**
 * GET /api/ppmt/runner
 * Get the latest PortfolioRunner result or status.
 */
export async function GET() {
  try {
    const res = await fetch(`${PYTHON_API}/api/portfolio/runner/result`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
    });
    const data = await res.json();
    return NextResponse.json(data);
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: `Python API unavailable: ${error.message}` },
      { status: 503 },
    );
  }
}

/**
 * POST /api/ppmt/runner
 * Start a PortfolioRunner session.
 *
 * Body: {
 *   tokens?: string[],       // e.g. ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
 *   timeframe?: string,      // e.g. "1h"
 *   allocationMethod?: string, // e.g. "REGIME_AWARE"
 *   initialCapital?: number, // e.g. 50000
 * }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const {
      tokens,
      timeframe = '1h',
      allocationMethod = 'REGIME_AWARE',
      initialCapital = 50_000,
    } = body;

    const params = new URLSearchParams({
      timeframe,
      allocation_method: allocationMethod,
      initial_capital: String(initialCapital),
    });
    if (tokens && Array.isArray(tokens)) {
      params.set('tokens', tokens.join(','));
    }

    const res = await fetch(
      `${PYTHON_API}/api/portfolio/runner/start?${params.toString()}`,
      { method: 'POST', cache: 'no-store', signal: AbortSignal.timeout(300_000) },
    );
    const data = await res.json();
    return NextResponse.json(data);
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: `Python API unavailable: ${error.message}` },
      { status: 503 },
    );
  }
}

/**
 * DELETE /api/ppmt/runner
 * Stop the PortfolioRunner session.
 */
export async function DELETE() {
  try {
    const res = await fetch(`${PYTHON_API}/api/portfolio/runner/stop`, {
      method: 'POST',
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
    });
    const data = await res.json();
    return NextResponse.json(data);
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: `Python API unavailable: ${error.message}` },
      { status: 503 },
    );
  }
}
