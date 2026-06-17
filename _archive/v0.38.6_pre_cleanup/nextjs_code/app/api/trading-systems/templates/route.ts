import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * Ensure every template has a unique `id` and valid `riskLevel`.
 * The engine's SystemTemplate doesn't have `id` or `riskLevel` fields,
 * but the frontend's TradingSystemTemplate requires them.
 */
function enrichTemplate(tpl: Record<string, unknown>, idx: number, category?: string): Record<string, unknown> {
  // Generate id from name or index — include category slug to avoid cross-category collisions
  const catSlug = category ? category.toLowerCase().replace(/[^a-z0-9]+/g, '-') : '';
  const nameSlug = (tpl.name as string || '').toLowerCase().replace(/[^a-z0-9]+/g, '-');
  const existingId = (tpl.id as string)?.trim();
  const id = existingId || `tpl-${catSlug}${catSlug ? '-' : ''}${nameSlug || 'unnamed'}-${idx}`;

  // Determine riskLevel from risk management config
  let riskLevel = (tpl.riskLevel as string) || 'MEDIUM';
  if (!['LOW', 'MEDIUM', 'HIGH', 'EXTREME'].includes(riskLevel)) {
    // Derive from risk management maxDrawdown
    const config = tpl.config as Record<string, unknown> | undefined;
    const riskMgmt = config?.riskManagement as Record<string, unknown> | undefined;
    const maxDD = riskMgmt?.maxDrawdown as number | undefined;
    if (maxDD != null) {
      riskLevel = maxDD > 30 ? 'EXTREME' : maxDD > 20 ? 'HIGH' : maxDD > 10 ? 'MEDIUM' : 'LOW';
    } else {
      // Derive from operationType
      const opType = tpl.operationType as string;
      if (opType === 'SCALP') riskLevel = 'HIGH';
      else if (opType === 'SWING') riskLevel = 'MEDIUM';
      else if (opType === 'HODL') riskLevel = 'LOW';
      else riskLevel = 'MEDIUM';
    }
  }

  return { ...tpl, id, riskLevel };
}

/**
 * GET /api/trading-systems/templates
 * Get all system templates from the TradingSystemEngine.
 * Returns templates grouped by category with icons and descriptions.
 * Optional ?category=ALPHA_HUNTER filter.
 */
export async function GET(request: NextRequest) {
  try {
    const tsModule = await import('@/lib/services/strategy/trading-system-engine');
    const tradingSystemEngine = tsModule.tradingSystemEngine;
    type SystemCategory = import('@/lib/services/strategy/trading-system-engine').SystemCategory;
    const { searchParams } = new URL(request.url);
    const category = searchParams.get('category') as SystemCategory | null;

    if (category) {
      // Validate category
      const validCategories: SystemCategory[] = [
        'ALPHA_HUNTER', 'SMART_MONEY', 'TECHNICAL', 'DEFENSIVE',
        'BOT_AWARE', 'DEEP_ANALYSIS', 'MICRO_STRUCTURE', 'ADAPTIVE',
      ];

      if (!validCategories.includes(category)) {
        return NextResponse.json(
          { data: null, error: `Invalid category. Must be one of: ${validCategories.join(', ')}` },
          { status: 400 },
        );
      }

      const templates = tradingSystemEngine.getTemplates(category).map((tpl, idx) => enrichTemplate(tpl as unknown as Record<string, unknown>, idx, category));
      const categories = tradingSystemEngine.getCategories();

      return NextResponse.json({
        data: {
          category: categories.find((c) => c.id === category) || null,
          templates,
        },
      });
    }

    // Return all templates grouped by category
    const groupedRaw = tradingSystemEngine.getTemplatesGroupedByCategory();
    const categories = tradingSystemEngine.getCategories();

    // Enrich all templates with IDs and riskLevels
    const grouped: Record<string, unknown[]> = {};
    for (const [cat, catTemplates] of Object.entries(groupedRaw)) {
      grouped[cat] = catTemplates.map((tpl, idx) => enrichTemplate(tpl as unknown as Record<string, unknown>, idx, cat));
    }

    return NextResponse.json({
      data: {
        categories,
        grouped,
        totalTemplates: tradingSystemEngine.getTemplateCount(),
      },
    });
  } catch (error) {
    console.error('Error getting system templates:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get system templates' },
      { status: 500 },
    );
  }
}
