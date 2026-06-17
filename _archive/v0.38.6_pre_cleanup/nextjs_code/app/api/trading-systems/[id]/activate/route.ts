import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ id: string }>;
}

/**
 * POST /api/trading-systems/[id]/activate
 * Activate a trading system for paper trading or live.
 * Updates isActive and isPaperTrading fields.
 */
export async function POST(
  request: NextRequest,
  context: RouteContext,
) {
  try {
    const { id } = await context.params;
    const body = await request.json();

    const { mode } = body as { mode?: 'paper' | 'live' | 'deactivate' };

    // Check if system exists
    const existing = await db.tradingSystem.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json(
        { data: null, error: 'Trading system not found' },
        { status: 404 },
      );
    }

    let updateData: { isActive: boolean; isPaperTrading: boolean };

    switch (mode) {
      case 'live':
        updateData = { isActive: true, isPaperTrading: false };
        break;
      case 'paper':
        updateData = { isActive: true, isPaperTrading: true };
        break;
      case 'deactivate':
        updateData = { isActive: false, isPaperTrading: false };
        break;
      default:
        // Toggle: if active, deactivate; if inactive, activate in paper mode
        if (existing.isActive) {
          updateData = { isActive: false, isPaperTrading: false };
        } else {
          updateData = { isActive: true, isPaperTrading: true };
        }
    }

    const updated = await db.tradingSystem.update({
      where: { id },
      data: updateData,
    });

    const modeLabel = !updated.isActive
      ? 'deactivated'
      : updated.isPaperTrading
        ? 'paper trading'
        : 'live trading';

    return NextResponse.json({
      data: {
        id: updated.id,
        name: updated.name,
        isActive: updated.isActive,
        isPaperTrading: updated.isPaperTrading,
        modeLabel,
      },
    });
  } catch (error) {
    console.error('Error activating trading system:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to activate trading system' },
      { status: 500 },
    );
  }
}
