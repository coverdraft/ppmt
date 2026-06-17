import { NextResponse } from 'next/server';

export async function GET() {
  try {
    const { patternCompressionPipeline } = await import('@/lib/services/brain/pattern-compression-pipeline');
    const stats = await patternCompressionPipeline.getStats();
    return NextResponse.json({ success: true, stats });
  } catch (error) {
    console.error('[PatternStats API] Error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal error' },
      { status: 500 }
    );
  }
}

export async function POST() {
  try {
    const { patternCompressionPipeline } = await import('@/lib/services/brain/pattern-compression-pipeline');
    const result = await patternCompressionPipeline.runCompression();
    return NextResponse.json({ success: true, result });
  } catch (error) {
    console.error('[PatternStats API] Compression error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal error' },
      { status: 500 }
    );
  }
}
