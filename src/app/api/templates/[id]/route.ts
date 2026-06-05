import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/templates/[id]
 * Get a single strategy template by ID
 */
export async function GET(
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

    const data = {
      id: template.id,
      name: template.name,
      description: template.description,
      category: template.category,
      difficulty: template.difficulty,
      author: template.author,
      tags: JSON.parse(template.tags),
      rating: template.rating,
      downloads: template.downloads,
      isFeatured: template.isFeatured,
      isBuiltIn: template.isBuiltIn,
      strategyConfig: JSON.parse(template.strategyConfig),
      expectedWinRate: template.expectedWinRate,
      expectedProfitFactor: template.expectedProfitFactor,
      expectedAvgTrades: template.expectedAvgTrades,
      expectedMaxDrawdown: template.expectedMaxDrawdown,
      applicableChains: JSON.parse(template.applicableChains),
      applicableTimeframes: JSON.parse(template.applicableTimeframes),
      createdAt: template.createdAt,
      updatedAt: template.updatedAt,
    };

    return NextResponse.json({ data });
  } catch (error) {
    console.error('Error getting strategy template:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get strategy template' },
      { status: 500 },
    );
  }
}
