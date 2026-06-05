/**
 * Kill Switch API - GET/POST /api/kill-switch
 *
 * GET  — Get current kill switch state
 * POST — Toggle global pause (action: PAUSE | RESUME)
 */

import { NextRequest, NextResponse } from 'next/server';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';

export async function GET() {
  try {
    const state = killSwitchService.getStateSerializable();
    return NextResponse.json({ success: true, data: state });
  } catch (error) {
    console.error('[KillSwitch API] GET error:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to get kill switch state' },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { action, reason } = body;

    if (!action || !['PAUSE', 'RESUME'].includes(action)) {
      return NextResponse.json(
        { success: false, error: 'Invalid action. Must be PAUSE or RESUME' },
        { status: 400 }
      );
    }

    if (action === 'PAUSE') {
      killSwitchService.setGlobalPause(true, reason || 'Manual pause via API');
    } else {
      killSwitchService.setGlobalPause(false);
    }

    const state = killSwitchService.getStateSerializable();
    return NextResponse.json({
      success: true,
      message: action === 'PAUSE' ? 'Global pause activated' : 'Global pause deactivated',
      data: state,
    });
  } catch (error) {
    console.error('[KillSwitch API] POST error:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to toggle kill switch' },
      { status: 500 }
    );
  }
}
