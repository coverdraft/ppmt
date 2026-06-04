import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { getCurrentUserId, templateScope } from '@/lib/services/shared/user-data-filter';
import { seedTemplates } from '@/lib/services/strategy/strategy-templates';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/templates
 * List strategy templates with optional filters.
 * Built-in templates (isBuiltIn=true) are shared with all users.
 * User-created templates are filtered by userId.
 */
export async function GET(request: NextRequest) {
  try {
    const userId = await getCurrentUserId();

    // Auto-seed built-in templates (idempotent)
    await seedTemplates();

    const { searchParams } = new URL(request.url);
    const category = searchParams.get('category');
    const difficulty = searchParams.get('difficulty');
    const search = searchParams.get('search');
    const featured = searchParams.get('featured');

    const where: Record<string, unknown> = templateScope(userId);

    if (category && category !== 'ALL') {
      where.category = category;
    }

    if (difficulty && difficulty !== 'ALL') {
      where.difficulty = difficulty;
    }

    if (featured === 'true') {
      where.isFeatured = true;
    }

    if (search) {
      where.OR = [
        { name: { contains: search } },
        { description: { contains: search } },
      ];
    }

    const templates = await db.strategyTemplate.findMany({
      where,
      orderBy: [{ isFeatured: 'desc' }, { rating: 'desc' }, { downloads: 'desc' }],
    });

    const data = templates.map((t) => ({
      id: t.id,
      name: t.name,
      description: t.description,
      category: t.category,
      difficulty: t.difficulty,
      author: t.author,
      tags: JSON.parse(t.tags),
      rating: t.rating,
      downloads: t.downloads,
      isFeatured: t.isFeatured,
      isBuiltIn: t.isBuiltIn,
      strategyConfig: JSON.parse(t.strategyConfig),
      expectedWinRate: t.expectedWinRate,
      expectedProfitFactor: t.expectedProfitFactor,
      expectedAvgTrades: t.expectedAvgTrades,
      expectedMaxDrawdown: t.expectedMaxDrawdown,
      applicableChains: JSON.parse(t.applicableChains),
      applicableTimeframes: JSON.parse(t.applicableTimeframes),
      createdAt: t.createdAt,
      updatedAt: t.updatedAt,
    }));

    return NextResponse.json({ data });
  } catch (error) {
    console.error('Error listing strategy templates:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to list strategy templates' },
      { status: 500 },
    );
  }
}
