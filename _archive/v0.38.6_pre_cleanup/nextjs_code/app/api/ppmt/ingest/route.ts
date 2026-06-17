import { NextResponse } from 'next/server';
import { execPpmt } from '@/lib/ppmt-cli';

export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { symbol, timeframe = '1h', days = 365 } = body;

    if (!symbol) {
      return NextResponse.json({ error: 'symbol is required' }, { status: 400 });
    }

    const output = execPpmt(`ingest -s "${symbol}" -t "${timeframe}" -d ${days}`, {
      timeout: 300000,
    });

    return NextResponse.json({
      data: {
        success: true,
        symbol,
        timeframe,
        days,
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
