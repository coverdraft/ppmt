/**
 * Strategy Kill Switch API - POST /api/kill-switch/strategy
 *
 * POST — Toggle per-strategy pause
 *   Body: { strategyId: string, action: 'PAUSE' | 'RESUME', reason?: string }
 */

import { NextRequest, NextResponse } from 'next/server';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { strategyId, action, reason } = body;

    if (!strategyId || typeof strategyId !== 'string') {
      return NextResponse.json(
        { success: false, error: 'strategyId is required' },
        { status: 400 }
      );
    }

    if (!action || !['PAUSE', 'RESUME'].includes(action)) {
      return NextResponse.json(
        { success: false, error: 'Invalid action. Must be PAUSE or RESUME' },
        { status: 400 }
      );
    }

    killSwitchService.setStrategyPause(
      strategyId,
      action === 'PAUSE',
      reason || `${action === 'PAUSE' ? 'Manual pause' : 'Manual resume'} via API`
    );

    const state = killSwitchService.getStateSerializable();
    return NextResponse.json({
      success: true,
      message: `Strategy ${strategyId} ${action === 'PAUSE' ? 'paused' : 'resumed'}`,
      data: state,
    });
  } catch (error) {
    console.error('[KillSwitch Strategy API] POST error:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to toggle strategy kill switch' },
      { status: 500 }
    );
  }
}
