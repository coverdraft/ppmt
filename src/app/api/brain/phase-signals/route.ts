import { NextResponse } from 'next/server';

/**
 * GET /api/brain/phase-signals
 * 
 * Phase-specific signal configurations.
 */
export async function GET() {
  try {
    const { tokenLifecycleEngine } = await import('@/lib/services/brain/token-lifecycle-engine');
    
    const phases = ['GENESIS', 'INCIPIENT', 'GROWTH', 'FOMO', 'DECLINE', 'LEGACY'] as const;
    const configs = phases.map(phase => ({
      phase,
      config: tokenLifecycleEngine.getPhaseSpecificSignals(phase),
    }));
    
    return NextResponse.json({ success: true, data: configs });
  } catch (error: any) {
    console.error('[/api/brain/phase-signals] Error:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
