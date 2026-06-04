import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/templates/[id]/import
 * Import a strategy template as a new TradingSystem.
 * Creates a TradingSystem from the template's strategyConfig
 * and increments the template's download count.
 */
export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;

    const template = await db.strategyTemplate.findUnique({
      where: { id },
    });

    if (!template) {
      return NextResponse.json(
        { data: null, error: 'Template not found' },
        { status: 404 },
      );
    }

    const config = JSON.parse(template.strategyConfig) as {
      indicators?: Array<{ type: string; params: Record<string, number | string> }>;
      entryRules?: { conditions: string[]; logic: string };
      exitRules?: {
        takeProfitPct: number;
        stopLossPct: number;
        trailingStopPct?: number;
      };
      riskManagement?: {
        maxPositionSizePct: number;
        maxOpenPositions: number;
      };
      timeframe?: string;
      direction?: string;
    };

    // Map template category to TradingSystem category
    const categoryMap: Record<string, string> = {
      MOMENTUM: 'TECHNICAL',
      MEAN_REVERSION: 'TECHNICAL',
      BREAKOUT: 'TECHNICAL',
      SCALPING: 'ALPHA_HUNTER',
      SWING: 'TECHNICAL',
      ARBITRAGE: 'DEEP_ANALYSIS',
      VOLUME: 'DEEP_ANALYSIS',
      VOLATILITY: 'ADAPTIVE',
    };

    // Build the TradingSystem from the template config
    const exitRules = config.exitRules || { takeProfitPct: 5, stopLossPct: 2 };
    const riskMgmt = config.riskManagement || { maxPositionSizePct: 5, maxOpenPositions: 3 };

    // Build entry signal config
    const entrySignal = {
      type: 'TEMPLATE_IMPORT',
      conditions: config.entryRules?.conditions || [],
      minConfidence: 0.5,
      requireConfirmation: true,
      indicators: config.indicators?.map((i) => i.type) || [],
      thresholds: {},
      customParams: { templateId: id, templateName: template.name },
    };

    // Build exit signal config
    const exitSignal = {
      stopLossPct: exitRules.stopLossPct,
      takeProfitPct: exitRules.takeProfitPct,
      trailingStopPct: exitRules.trailingStopPct,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit'],
    };

    // Build execution config
    const executionConfig = {
      orderType: 'LIMIT',
      slippageTolerancePct: 2,
      maxPositionSizePct: riskMgmt.maxPositionSizePct,
      timeInForce: 'GTC',
    };

    // Build asset filter
    const applicableChains = JSON.parse(template.applicableChains) as string[];
    const assetFilter = {
      minLiquidityUsd: 20000,
      chains: applicableChains.map((c) => c.toLowerCase()),
      tokenTypes: ['any'],
    };

    const tradingSystem = await db.tradingSystem.create({
      data: {
        name: template.name,
        description: `Imported from template: ${template.description}`,
        category: categoryMap[template.category] || 'TECHNICAL',
        icon: '📋',
        assetFilter: JSON.stringify(assetFilter),
        phaseConfig: JSON.stringify({ allowedPhases: ['GROWTH', 'MATURE', 'ESTABLISHED'] }),
        entrySignal: JSON.stringify(entrySignal),
        executionConfig: JSON.stringify(executionConfig),
        exitSignal: JSON.stringify(exitSignal),
        bigDataContext: JSON.stringify({}),
        primaryTimeframe: config.timeframe || '4h',
        confirmTimeframes: JSON.stringify([]),
        maxPositionPct: riskMgmt.maxPositionSizePct,
        maxOpenPositions: riskMgmt.maxOpenPositions,
        stopLossPct: exitRules.stopLossPct,
        takeProfitPct: exitRules.takeProfitPct,
        trailingStopPct: exitRules.trailingStopPct ?? null,
        cashReservePct: 20,
        allocationMethod: 'KELLY_MODIFIED',
        allocationConfig: JSON.stringify({}),
        isActive: false,
        isPaperTrading: false,
        version: 1,
        parentSystemId: null,
        autoOptimize: false,
      },
    });

    // Increment download count
    await db.strategyTemplate.update({
      where: { id },
      data: { downloads: { increment: 1 } },
    });

    return NextResponse.json(
      {
        data: {
          tradingSystem,
          templateName: template.name,
          templateCategory: template.category,
        },
      },
      { status: 201 },
    );
  } catch (error) {
    console.error('Error importing strategy template:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to import strategy template' },
      { status: 500 },
    );
  }
}
