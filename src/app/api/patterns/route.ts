import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const patterns = await db.patternRule.findMany({
      orderBy: { occurrences: 'desc' },
    });

    return NextResponse.json({ patterns });
  } catch (error) {
    console.error('Error fetching patterns:', error);
    return NextResponse.json({ error: 'Failed to fetch patterns' }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { name, conditions } = body;

    const pattern = await db.patternRule.create({
      data: {
        name,
        conditions: JSON.stringify(conditions),
        isActive: true,
        backtestResults: JSON.stringify({}),
        winRate: 0,
        occurrences: 0,
      },
    });

    return NextResponse.json({ pattern }, { status: 201 });
  } catch (error) {
    console.error('Error creating pattern:', error);
    return NextResponse.json({ error: 'Failed to create pattern' }, { status: 500 });
  }
}
