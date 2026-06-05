import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

// GET /api/alerts/rules — List alert rules
export async function GET() {
  try {
    const rules = await db.alertRule.findMany({
      take: 100,
      orderBy: { createdAt: 'desc' },
    });

    return NextResponse.json({ data: rules });
  } catch (error) {
    console.error('[API /alerts/rules] GET error:', error);
    return NextResponse.json({ error: 'Failed to fetch rules' }, { status: 500 });
  }
}

// POST /api/alerts/rules — Create alert rule
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { name, category, condition, severity, channels, cooldownMin, enabled } = body;

    if (!name || !category || !condition) {
      return NextResponse.json(
        { error: 'name, category, and condition are required' },
        { status: 400 },
      );
    }

    const rule = await db.alertRule.create({
      data: {
        name,
        category,
        condition: typeof condition === 'string' ? condition : JSON.stringify(condition),
        severity: severity || 'INFO',
        channels: channels ? JSON.stringify(channels) : '["IN_APP"]',
        cooldownMin: cooldownMin || 5,
        enabled: enabled !== undefined ? enabled : true,
      },
    });

    return NextResponse.json({ data: rule }, { status: 201 });
  } catch (error) {
    console.error('[API /alerts/rules] POST error:', error);
    return NextResponse.json({ error: 'Failed to create rule' }, { status: 500 });
  }
}
