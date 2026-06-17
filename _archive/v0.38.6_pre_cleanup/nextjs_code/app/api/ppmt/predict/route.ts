import { NextResponse } from 'next/server';
import { execPpmt } from '@/lib/ppmt-cli';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get('symbol');
  const timeframe = searchParams.get('timeframe') || '1h';
  const depth = searchParams.get('depth') || '5';

  if (!symbol) {
    return NextResponse.json({ error: 'symbol parameter required' }, { status: 400 });
  }

  try {
    const output = execPpmt(`predict -s "${symbol}" -t "${timeframe}" -d ${depth}`, {
      timeout: 30000,
    });

    return NextResponse.json({
      data: {
        symbol,
        timeframe,
        output,
      },
    });
  } catch (error: any) {
    return NextResponse.json({
      error: error.message,
      output: error.stdout?.slice(-500) || '',
    }, { status: 500 });
  }
}
