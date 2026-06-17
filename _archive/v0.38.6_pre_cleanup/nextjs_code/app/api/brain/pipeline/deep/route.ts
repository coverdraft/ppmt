import { NextRequest, NextResponse } from 'next/server';

// ============================================================
// Types for the request/response
// ============================================================

interface DeepAnalysisPostBody {
  tokenAddress: string;
  chain?: string;
  capitalUsd?: number;
}

// ============================================================
// POST /api/brain/pipeline/deep
// On-demand deep analysis with DEEP thinking depth
// ============================================================

export async function POST(request: NextRequest) {
  try {
    const body: DeepAnalysisPostBody = await request.json();

    // Validate required field
    if (!body.tokenAddress) {
      return NextResponse.json(
        { data: null, error: 'tokenAddress is required' },
        { status: 400 },
      );
    }

    // Lazy import to avoid startup issues
    const { brainAnalysisPipeline } = await import('@/lib/services/brain/brain-analysis-pipeline');

    const chain = body.chain ?? 'SOL';

    // Run deep analysis — uses DEEP thinking depth and forces rescan
    const result = await brainAnalysisPipeline.deepAnalyzeToken(
      body.tokenAddress,
      chain,
      body.capitalUsd,
    );

    return NextResponse.json({
      data: result,
      error: null,
    }, { status: 200 });
  } catch (error) {
    console.error('[/api/brain/pipeline/deep] POST error:', error);
    const message = error instanceof Error ? error.message : 'Deep analysis failed';
    return NextResponse.json(
      { data: null, error: message },
      { status: 500 },
    );
  }
}
