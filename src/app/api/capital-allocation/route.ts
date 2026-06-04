import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/capital-allocation
 */
export async function GET(request: NextRequest) {
  try {
    const mod = await import('@/lib/services/risk/capital-allocation');
    const capitalAllocationEngine = mod.capitalAllocationEngine;
    const ALLOCATION_METHODS = mod.ALLOCATION_METHODS;
    type AllocationMethod = import('@/lib/services/risk/capital-allocation').AllocationMethod;
    type AllocationCategory = import('@/lib/services/risk/capital-allocation').AllocationCategory;
    const { searchParams } = new URL(request.url);
    const category = searchParams.get('category') as AllocationCategory | null;
    const grouped = searchParams.get('grouped');

    // Build methods list
    const methods = (Object.entries(ALLOCATION_METHODS) as [AllocationMethod, typeof ALLOCATION_METHODS[AllocationMethod]][]).map(
      ([method, info]) => ({
        method,
        name: info.name,
        icon: info.icon,
        description: info.description,
        category: info.category,
      }),
    );

    // Filter by category if specified
    const filtered = category
      ? methods.filter((m) => m.category === category)
      : methods;

    if (grouped === 'true') {
      // Group by category
      const groupedMethods: Record<string, typeof filtered> = {};
      for (const method of filtered) {
        const cat = method.category;
        if (!groupedMethods[cat]) groupedMethods[cat] = [];
        groupedMethods[cat].push(method);
      }

      const categories: Array<{
        id: AllocationCategory;
        name: string;
        methodCount: number;
      }> = [
        { id: 'BASIC', name: 'Basic', methodCount: groupedMethods['BASIC']?.length || 0 },
        { id: 'ADVANCED', name: 'Advanced', methodCount: groupedMethods['ADVANCED']?.length || 0 },
        { id: 'PORTFOLIO_OPTIMIZATION', name: 'Portfolio Optimization', methodCount: groupedMethods['PORTFOLIO_OPTIMIZATION']?.length || 0 },
        { id: 'ADAPTIVE', name: 'Adaptive', methodCount: groupedMethods['ADAPTIVE']?.length || 0 },
        { id: 'COMBINED', name: 'Combined', methodCount: groupedMethods['COMBINED']?.length || 0 },
      ];

      return NextResponse.json({
        data: {
          categories,
          grouped: groupedMethods,
          totalMethods: filtered.length,
        },
      });
    }

    return NextResponse.json({
      data: {
        methods: filtered,
        totalMethods: filtered.length,
      },
    });
  } catch (error) {
    console.error('Error getting allocation methods:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get allocation methods' },
      { status: 500 },
    );
  }
}

/**
 * POST /api/capital-allocation
 * Calculate allocation for a given method and input params.
 * Returns the calculated positions.
 */
export async function POST(request: NextRequest) {
  try {
    const mod = await import('@/lib/services/risk/capital-allocation');
    const capitalAllocationEngine = mod.capitalAllocationEngine;
    const ALLOCATION_METHODS = mod.ALLOCATION_METHODS;
    type AllocationMethod = import('@/lib/services/risk/capital-allocation').AllocationMethod;
    type AllocationInput = import('@/lib/services/risk/capital-allocation').AllocationInput;
    const body = await request.json();

    const { method, input } = body as {
      method?: string;
      input?: AllocationInput;
    };

    if (!method) {
      return NextResponse.json(
        { data: null, error: 'method is required' },
        { status: 400 },
      );
    }

    const validMethods = Object.keys(ALLOCATION_METHODS) as AllocationMethod[];
    if (!validMethods.includes(method as AllocationMethod)) {
      return NextResponse.json(
        { data: null, error: `Invalid method. Must be one of: ${validMethods.join(', ')}` },
        { status: 400 },
      );
    }

    if (!input) {
      return NextResponse.json(
        { data: null, error: 'input parameters are required' },
        { status: 400 },
      );
    }

    // Validate required input fields
    if (!input.capital || input.capital <= 0) {
      return NextResponse.json(
        { data: null, error: 'input.capital must be a positive number' },
        { status: 400 },
      );
    }

    if (!input.signals || input.signals.length === 0) {
      return NextResponse.json(
        { data: null, error: 'input.signals must be a non-empty array' },
        { status: 400 },
      );
    }

    // Validate signals
    for (const signal of input.signals) {
      if (!signal.tokenAddress) {
        return NextResponse.json(
          { data: null, error: 'Each signal must have a tokenAddress' },
          { status: 400 },
        );
      }
      if (signal.confidence < 0 || signal.confidence > 1) {
        return NextResponse.json(
          { data: null, error: 'Signal confidence must be between 0 and 1' },
          { status: 400 },
        );
      }
    }

    // Set defaults for missing optional fields
    const fullInput: AllocationInput = {
      capital: input.capital,
      currentPositions: input.currentPositions || [],
      signals: input.signals,
      historicalTrades: input.historicalTrades || {
        winRate: 0.5,
        avgWin: 0.15,
        avgLoss: 0.05,
        totalTrades: 100,
      },
      volatility: input.volatility ?? 0.5,
      currentDrawdown: input.currentDrawdown ?? 0,
      maxDrawdown: input.maxDrawdown ?? 0.20,
      marketRegime: input.marketRegime || 'SIDEWAYS',
      targetVolatility: input.targetVolatility,
      riskPerTrade: input.riskPerTrade,
      stopLossPct: input.stopLossPct,
      delta: input.delta,
      currentUnits: input.currentUnits,
      baseSizePct: input.baseSizePct,
      signalScore: input.signalScore,
      fraction: input.fraction,
      amountPerTrade: input.amountPerTrade,
      streakType: input.streakType,
      streakLength: input.streakLength,
      strategies: input.strategies,
      systems: input.systems,
      performanceHistory: input.performanceHistory,
      compositeMethods: input.compositeMethods,
      compositeWeights: input.compositeWeights,
      returns: input.returns,
      covMatrix: input.covMatrix,
      volatilities: input.volatilities,
      correlations: input.correlations,
      qTable: input.qTable,
      rlState: input.rlState,
    };

    const result = capitalAllocationEngine.calculate(
      method as AllocationMethod,
      fullInput,
    );

    // Get method info for context
    const methodInfo = ALLOCATION_METHODS[method as AllocationMethod];

    return NextResponse.json({
      data: {
        method: result.method,
        methodName: methodInfo.name,
        methodIcon: methodInfo.icon,
        positions: result.positions,
        cashReserve: result.cashReserve,
        cashReservePct: result.cashReserve / input.capital,
        totalAllocated: result.totalAllocated,
        totalAllocatedPct: result.totalAllocated / input.capital,
        inputSummary: {
          capital: input.capital,
          signalCount: input.signals.length,
          marketRegime: fullInput.marketRegime,
          volatility: fullInput.volatility,
        },
      },
    });
  } catch (error) {
    console.error('Error calculating allocation:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to calculate allocation' },
      { status: 500 },
    );
  }
}
