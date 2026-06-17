import { NextRequest, NextResponse } from 'next/server';
import { analyzeToken, analyzeBatch } from '@/lib/services/brain/brain-orchestrator';
import { crossCorrelationEngine } from '@/lib/services/risk/cross-correlation-engine';
import { candlestickPatternEngine } from '@/lib/services/brain/candlestick-pattern-engine';
import { deepAnalysisEngine } from '@/lib/services/strategy/deep-analysis-engine';

/**
 * POST /api/brain/analyze
 * Full deep analysis on a token or batch of tokens.
 * Runs the complete 11-phase pipeline including:
 *   - Candlestick Pattern Scan (30+ patterns, multi-timeframe)
 *   - Cross-Correlation P(outcome | trader + pattern + phase)
 *   - Deep Analysis + LLM
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    
    // Single token analysis
    if (body.tokenAddress) {
      const analysis = await analyzeToken(
        body.tokenAddress,
        body.chain || 'SOL',
        body.positionSizeUsd || 10,
        body.expectedGainPct || 5
      );
      
      return NextResponse.json({
        data: analysis,
        error: null,
      });
    }
    
    // Batch analysis
    if (body.tokenAddresses && Array.isArray(body.tokenAddresses)) {
      const result = await analyzeBatch(
        body.tokenAddresses.slice(0, 20), // Limit to 20 tokens
        body.chain || 'SOL',
        body.positionSizeUsd || 10,
        body.expectedGainPct || 5
      );
      
      return NextResponse.json({
        data: result,
        error: null,
      });
    }
    
    return NextResponse.json(
      { data: null, error: 'Provide tokenAddress or tokenAddresses' },
      { status: 400 }
    );
  } catch (error) {
    console.error('[/api/brain/analyze] Error:', error);
    return NextResponse.json(
      { data: null, error: String(error) },
      { status: 500 }
    );
  }
}

/**
 * GET /api/brain/analyze?token=xxx
 * Quick analysis endpoint
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const tokenAddress = searchParams.get('token');
    const mode = searchParams.get('mode') || 'full'; // full, patterns, correlation, deep
    
    if (!tokenAddress) {
      return NextResponse.json(
        { data: null, error: 'Provide ?token=xxx parameter' },
        { status: 400 }
      );
    }
    
    const chain = searchParams.get('chain') || 'SOL';
    
    if (mode === 'patterns') {
      const patterns = await candlestickPatternEngine.scanMultiTimeframe(tokenAddress);
      return NextResponse.json({ data: patterns, error: null });
    }
    
    if (mode === 'correlation') {
      const correlation = await crossCorrelationEngine.analyzeCrossCorrelation(tokenAddress, chain);
      return NextResponse.json({ data: correlation, error: null });
    }
    
    if (mode === 'deep') {
      const { db } = await import('@/lib/db');
      const token = await db.token.findFirst({ where: { address: tokenAddress } });
      const deepResult = await deepAnalysisEngine.analyze({
        tokenAddress,
        symbol: token?.symbol || '',
        chain,
        brainAnalysis: {
          tokenAddress,
          chain,
          lifecyclePhase: 'INCIPIENT',
          lifecycleConfidence: 0.5,
          regime: 'SIDEWAYS',
          regimeConfidence: 0.5,
          volatilityRegime: 'NORMAL',
          operabilityLevel: 'GOOD',
          operabilityScore: 50,
          isOperable: true,
          botSwarmLevel: 'NONE',
          whaleDirection: 'NEUTRAL',
          whaleConfidence: 0.5,
          smartMoneyFlow: 'NEUTRAL',
          meanReversionZone: null,
          anomalyDetected: false,
          anomalyScore: 0,
          isTransitioning: false,
          warnings: [],
          evidence: [],
        } as any,
        depth: 'STANDARD',
      });
      return NextResponse.json({ data: deepResult, error: null });
    }
    
    // Full analysis
    const analysis = await analyzeToken(tokenAddress, chain);
    return NextResponse.json({ data: analysis, error: null });
  } catch (error) {
    console.error('[/api/brain/analyze] GET error:', error);
    return NextResponse.json(
      { data: null, error: String(error) },
      { status: 500 }
    );
  }
}
