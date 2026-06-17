import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const signal = await db.signal.findUnique({
      where: { id },
      include: { token: { include: { dna: true } } },
    });

    if (!signal) {
      return NextResponse.json({ error: 'Signal not found' }, { status: 404 });
    }

    return NextResponse.json({ signal });
  } catch (error) {
    console.error('Error fetching signal:', error);
    return NextResponse.json({ error: 'Failed to fetch signal' }, { status: 500 });
  }
}
