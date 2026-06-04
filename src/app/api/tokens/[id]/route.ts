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
    const token = await db.token.findUnique({
      where: { id },
      include: {
        dna: true,
        signals: {
          orderBy: { createdAt: 'desc' },
          take: 10,
        },
      },
    });

    if (!token) {
      return NextResponse.json({ error: 'Token not found' }, { status: 404 });
    }

    const userEvents = await db.userEvent.findMany({
      where: { tokenId: id },
      orderBy: { createdAt: 'desc' },
      take: 100,
    });

    return NextResponse.json({ token, userEvents });
  } catch (error) {
    console.error('Error fetching token:', error);
    return NextResponse.json({ error: 'Failed to fetch token' }, { status: 500 });
  }
}
