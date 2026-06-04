import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { validateOrError, tradingSystemCreateSchema } from '@/lib/validations';
import { getCurrentUserId, userScope } from '@/lib/services/shared/user-data-filter';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/trading-systems
 * POST /api/trading-systems
 */
export async function GET(request: NextRequest) {
  try {
    const userId = await getCurrentUserId();

    const { searchParams } = new URL(request.url);
    const category = searchParams.get('category');
    const isActive = searchParams.get('isActive');
    const search = searchParams.get('search');

    const where: Record<string, unknown> = userScope(userId);

    if (category) {
      where.category = category;
    }

    if (isActive !== null && isActive !== undefined) {
      where.isActive = isActive === 'true';
    }

    if (search) {
      // Merge search filter WITH user scope (don't overwrite OR)
      const userOr = userScope(userId).OR;
      where.AND = [
        { OR: userOr },
        { OR: [
          { name: { contains: search } },
          { description: { contains: search } },
        ]},
      ];
      delete where.OR;
    }

    const tradingSystems = await db.tradingSystem.findMany({
      where,
      include: {
        _count: {
          select: { backtests: true },
        },
        backtests: {
          where: { status: 'COMPLETED' },
          orderBy: { completedAt: 'desc' },
          take: 1,
          select: {
            sharpeRatio: true,
            winRate: true,
            totalPnlPct: true,
            maxDrawdownPct: true,
            completedAt: true,
          },
        },
        derivedSystems: {
          select: { id: true, name: true },
        },
      },
      orderBy: { createdAt: 'desc' },
    });

    const data = tradingSystems.map((system) => ({
      id: system.id,
      name: system.name,
      description: system.description,
      category: system.category,
      icon: system.icon,
      isActive: system.isActive,
      isPaperTrading: system.isPaperTrading,
      version: system.version,
      allocationMethod: system.allocationMethod,
      primaryTimeframe: system.primaryTimeframe,
      stopLossPct: system.stopLossPct,
      takeProfitPct: system.takeProfitPct,
      maxPositionPct: system.maxPositionPct,
      maxOpenPositions: system.maxOpenPositions,
      cashReservePct: system.cashReservePct,
      totalBacktests: system.totalBacktests,
      bestSharpe: system.bestSharpe,
      bestWinRate: system.bestWinRate,
      bestPnlPct: system.bestPnlPct,
      avgHoldTimeMin: system.avgHoldTimeMin,
      backtestCount: system._count.backtests,
      latestBacktest: system.backtests[0] || null,
      derivedSystems: system.derivedSystems,
      parentSystemId: system.parentSystemId,
      createdAt: system.createdAt,
      updatedAt: system.updatedAt,
    }));

    return NextResponse.json({ data });
  } catch (error) {
    console.error('Error listing trading systems:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to list trading systems' },
      { status: 500 },
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const userId = await getCurrentUserId();

    const body = await request.json();

    const validation = validateOrError(tradingSystemCreateSchema, body);
    if (!validation.success) {
      return NextResponse.json(
        { data: null, error: validation.error },
        { status: 400 },
      );
    }

    const validated = validation.data;

    const {
      icon, assetFilter, phaseConfig,
      entrySignal, executionConfig, exitSignal, bigDataContext,
      confirmTimeframes, maxOpenPositions,
      trailingStopPct, cashReservePct,
      allocationMethod, allocationConfig, isActive, isPaperTrading,
      parentSystemId, autoOptimize, optimizationMethod, optimizationFreq,
    } = body;

    const tradingSystem = await db.tradingSystem.create({
      data: {
        name: validated.name,
        description: validated.description || null,
        category: validated.category,
        icon: icon || '🎯',
        assetFilter: typeof assetFilter === 'string' ? assetFilter : JSON.stringify(assetFilter || {}),
        phaseConfig: typeof phaseConfig === 'string' ? phaseConfig : JSON.stringify(phaseConfig || {}),
        entrySignal: typeof entrySignal === 'string' ? entrySignal : JSON.stringify(entrySignal || {}),
        executionConfig: typeof executionConfig === 'string' ? executionConfig : JSON.stringify(executionConfig || {}),
        exitSignal: typeof exitSignal === 'string' ? exitSignal : JSON.stringify(exitSignal || {}),
        bigDataContext: typeof bigDataContext === 'string' ? bigDataContext : JSON.stringify(bigDataContext || {}),
        primaryTimeframe: validated.primaryTimeframe,
        confirmTimeframes: typeof confirmTimeframes === 'string' ? confirmTimeframes : JSON.stringify(confirmTimeframes || []),
        maxPositionPct: validated.maxPositionPct,
        maxOpenPositions: maxOpenPositions ?? 10,
        stopLossPct: validated.stopLossPct,
        takeProfitPct: validated.takeProfitPct,
        trailingStopPct: trailingStopPct ?? null,
        cashReservePct: cashReservePct ?? 20,
        allocationMethod: allocationMethod || 'KELLY_MODIFIED',
        allocationConfig: typeof allocationConfig === 'string' ? allocationConfig : JSON.stringify(allocationConfig || {}),
        isActive: isActive ?? false,
        isPaperTrading: isPaperTrading ?? false,
        parentSystemId: parentSystemId || null,
        autoOptimize: autoOptimize ?? false,
        optimizationMethod: optimizationMethod || null,
        optimizationFreq: optimizationFreq || null,
        userId,
      },
    });

    return NextResponse.json({ data: tradingSystem }, { status: 201 });
  } catch (error) {
    console.error('Error creating trading system:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to create trading system' },
      { status: 500 },
    );
  }
}
