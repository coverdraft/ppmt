import { NextResponse } from 'next/server';
import { execSync } from 'child_process';

export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { symbol, timeframe = '1h', days = 365 } = body;

    if (!symbol) {
      return NextResponse.json({ error: 'symbol is required' }, { status: 400 });
    }

    // Run ppmt ingest command
    const cmd = `ppmt ingest -s "${symbol}" -t "${timeframe}" -d ${days}`;
    const output = execSync(cmd, { timeout: 300000, encoding: 'utf-8' });

    return NextResponse.json({
      data: {
        success: true,
        symbol,
        timeframe,
        days,
        output: output.slice(-500), // Last 500 chars of output
      },
    });
  } catch (error: any) {
    return NextResponse.json({
      error: error.message,
      output: error.stdout?.slice(-500) || '',
    }, { status: 500 });
  }
}
