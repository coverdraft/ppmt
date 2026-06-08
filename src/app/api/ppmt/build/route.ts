import { NextResponse } from 'next/server';
import { execSync } from 'child_process';

export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { symbol, timeframe = '1h' } = body;

    if (!symbol) {
      return NextResponse.json({ error: 'symbol is required' }, { status: 400 });
    }

    // Run ppmt build command
    const cmd = `ppmt build -s "${symbol}" -t "${timeframe}"`;
    const output = execSync(cmd, { timeout: 120000, encoding: 'utf-8' });

    return NextResponse.json({
      data: {
        success: true,
        symbol,
        timeframe,
        output: output.slice(-500),
      },
    });
  } catch (error: any) {
    return NextResponse.json({
      error: error.message,
      output: error.stdout?.slice(-500) || '',
    }, { status: 500 });
  }
}
