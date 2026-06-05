import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface RiskControlsPayload {
  maxPositionSizePct: number;
  maxPortfolioRiskPct: number;
  stopLossDefaultPct: number;
  dailyLossLimitPct: number;
}

/**
 * GET /api/risk/controls
 * Returns the persisted risk controls config (or defaults if none saved).
 */
export async function GET() {
  try {
    const { db } = await import('@/lib/db');

    // Get the most recent config (global, userId = null)
    const config = await db.riskControlsConfig.findFirst({
      where: { userId: null },
      orderBy: { updatedAt: 'desc' },
    });

    if (!config) {
      // Return defaults
      return NextResponse.json({
        data: {
          maxPositionSizePct: 10,
          maxPortfolioRiskPct: 25,
          stopLossDefaultPct: 5,
          dailyLossLimitPct: 10,
        },
      });
    }

    return NextResponse.json({
      data: {
        maxPositionSizePct: config.maxPositionSizePct,
        maxPortfolioRiskPct: config.maxPortfolioRiskPct,
        stopLossDefaultPct: config.stopLossDefaultPct,
        dailyLossLimitPct: config.dailyLossLimitPct,
      },
    });
  } catch (error) {
    console.error('Error fetching risk controls:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to fetch risk controls' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/risk/controls
 * Saves (upserts) the risk controls config.
 * Body: { maxPositionSizePct, maxPortfolioRiskPct, stopLossDefaultPct, dailyLossLimitPct }
 */
export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as RiskControlsPayload;

    // Validate
    const {
      maxPositionSizePct,
      maxPortfolioRiskPct,
      stopLossDefaultPct,
      dailyLossLimitPct,
    } = body;

    if (
      typeof maxPositionSizePct !== 'number' ||
      typeof maxPortfolioRiskPct !== 'number' ||
      typeof stopLossDefaultPct !== 'number' ||
      typeof dailyLossLimitPct !== 'number' ||
      maxPositionSizePct <= 0 || maxPositionSizePct > 100 ||
      maxPortfolioRiskPct <= 0 || maxPortfolioRiskPct > 100 ||
      stopLossDefaultPct <= 0 || stopLossDefaultPct > 100 ||
      dailyLossLimitPct <= 0 || dailyLossLimitPct > 100
    ) {
      return NextResponse.json(
        { data: null, error: 'Invalid risk controls values. All values must be numbers between 0 and 100.' },
        { status: 400 }
      );
    }

    const { db } = await import('@/lib/db');

    // Find existing global config
    const existing = await db.riskControlsConfig.findFirst({
      where: { userId: null },
      orderBy: { updatedAt: 'desc' },
    });

    let config;
    if (existing) {
      config = await db.riskControlsConfig.update({
        where: { id: existing.id },
        data: {
          maxPositionSizePct,
          maxPortfolioRiskPct,
          stopLossDefaultPct,
          dailyLossLimitPct,
        },
      });
    } else {
      config = await db.riskControlsConfig.create({
        data: {
          maxPositionSizePct,
          maxPortfolioRiskPct,
          stopLossDefaultPct,
          dailyLossLimitPct,
          userId: null,
        },
      });
    }

    return NextResponse.json({
      data: {
        maxPositionSizePct: config.maxPositionSizePct,
        maxPortfolioRiskPct: config.maxPortfolioRiskPct,
        stopLossDefaultPct: config.stopLossDefaultPct,
        dailyLossLimitPct: config.dailyLossLimitPct,
      },
    });
  } catch (error) {
    console.error('Error saving risk controls:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to save risk controls' },
      { status: 500 }
    );
  }
}
