/**
 * Risk Budget API - GET/POST /api/risk-budget
 *
 * GET  — Get current risk budget config (from RiskBudget table)
 * POST — Update risk budget config (upsert)
 *   Body: { maxPortfolioDrawdownPct?, maxStrategyDrawdownPct?, maxPositionLossPct?,
 *           maxConcentrationPct?, maxSectorPct?, maxChainPct?, maxCorrelatedPct?, riskProfile? }
 */

import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { killSwitchService } from '@/lib/services/risk/kill-switch-service';

export async function GET() {
  try {
    const row = await db.riskBudget.findFirst();
    if (row) {
      return NextResponse.json({
        success: true,
        data: {
          id: row.id,
          maxPortfolioDrawdownPct: row.maxPortfolioDrawdownPct,
          maxStrategyDrawdownPct: row.maxStrategyDrawdownPct,
          maxPositionLossPct: row.maxPositionLossPct,
          maxConcentrationPct: row.maxConcentrationPct,
          maxSectorPct: row.maxSectorPct,
          maxChainPct: row.maxChainPct,
          maxCorrelatedPct: row.maxCorrelatedPct,
          riskProfile: row.riskProfile,
          updatedAt: row.updatedAt.toISOString(),
        },
      });
    }

    // No row yet — return defaults
    return NextResponse.json({
      success: true,
      data: {
        maxPortfolioDrawdownPct: 20,
        maxStrategyDrawdownPct: 30,
        maxPositionLossPct: 50,
        maxConcentrationPct: 15,
        maxSectorPct: 30,
        maxChainPct: 50,
        maxCorrelatedPct: 40,
        riskProfile: 'MODERATE',
      },
    });
  } catch (error) {
    console.error('[RiskBudget API] GET error:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to get risk budget' },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const {
      maxPortfolioDrawdownPct,
      maxStrategyDrawdownPct,
      maxPositionLossPct,
      maxConcentrationPct,
      maxSectorPct,
      maxChainPct,
      maxCorrelatedPct,
      riskProfile,
    } = body;

    // Validate ranges
    const validations: Array<[string, number, number, number]> = [
      ['maxPortfolioDrawdownPct', maxPortfolioDrawdownPct, 1, 100],
      ['maxStrategyDrawdownPct', maxStrategyDrawdownPct, 1, 100],
      ['maxPositionLossPct', maxPositionLossPct, 1, 100],
      ['maxConcentrationPct', maxConcentrationPct, 1, 100],
      ['maxSectorPct', maxSectorPct, 1, 100],
      ['maxChainPct', maxChainPct, 1, 100],
      ['maxCorrelatedPct', maxCorrelatedPct, 1, 100],
    ];

    for (const [field, value, min, max] of validations) {
      if (value !== undefined && (typeof value !== 'number' || value < min || value > max)) {
        return NextResponse.json(
          { success: false, error: `${field} must be a number between ${min} and ${max}` },
          { status: 400 }
        );
      }
    }

    if (riskProfile !== undefined && !['CONSERVATIVE', 'MODERATE', 'AGGRESSIVE'].includes(riskProfile)) {
      return NextResponse.json(
        { success: false, error: 'riskProfile must be CONSERVATIVE, MODERATE, or AGGRESSIVE' },
        { status: 400 }
      );
    }

    // Upsert the risk budget row
    const existing = await db.riskBudget.findFirst();

    const data: Record<string, unknown> = {};
    if (maxPortfolioDrawdownPct !== undefined) data.maxPortfolioDrawdownPct = maxPortfolioDrawdownPct;
    if (maxStrategyDrawdownPct !== undefined) data.maxStrategyDrawdownPct = maxStrategyDrawdownPct;
    if (maxPositionLossPct !== undefined) data.maxPositionLossPct = maxPositionLossPct;
    if (maxConcentrationPct !== undefined) data.maxConcentrationPct = maxConcentrationPct;
    if (maxSectorPct !== undefined) data.maxSectorPct = maxSectorPct;
    if (maxChainPct !== undefined) data.maxChainPct = maxChainPct;
    if (maxCorrelatedPct !== undefined) data.maxCorrelatedPct = maxCorrelatedPct;
    if (riskProfile !== undefined) data.riskProfile = riskProfile;

    let row;
    if (existing) {
      row = await db.riskBudget.update({
        where: { id: existing.id },
        data,
      });
    } else {
      row = await db.riskBudget.create({
        data: {
          maxPortfolioDrawdownPct: maxPortfolioDrawdownPct ?? 20,
          maxStrategyDrawdownPct: maxStrategyDrawdownPct ?? 30,
          maxPositionLossPct: maxPositionLossPct ?? 50,
          maxConcentrationPct: maxConcentrationPct ?? 15,
          maxSectorPct: maxSectorPct ?? 30,
          maxChainPct: maxChainPct ?? 50,
          maxCorrelatedPct: maxCorrelatedPct ?? 40,
          riskProfile: riskProfile ?? 'MODERATE',
        },
      });
    }

    // Invalidate kill switch cache so it picks up the new config
    killSwitchService.invalidateRiskBudgetCache();

    return NextResponse.json({
      success: true,
      message: 'Risk budget updated',
      data: {
        id: row.id,
        maxPortfolioDrawdownPct: row.maxPortfolioDrawdownPct,
        maxStrategyDrawdownPct: row.maxStrategyDrawdownPct,
        maxPositionLossPct: row.maxPositionLossPct,
        maxConcentrationPct: row.maxConcentrationPct,
        maxSectorPct: row.maxSectorPct,
        maxChainPct: row.maxChainPct,
        maxCorrelatedPct: row.maxCorrelatedPct,
        riskProfile: row.riskProfile,
        updatedAt: row.updatedAt.toISOString(),
      },
    });
  } catch (error) {
    console.error('[RiskBudget API] POST error:', error);
    return NextResponse.json(
      { success: false, error: 'Failed to update risk budget' },
      { status: 500 }
    );
  }
}
