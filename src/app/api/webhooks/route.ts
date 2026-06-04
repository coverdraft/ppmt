import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { getCurrentUserId, userScope } from '@/lib/services/shared/user-data-filter';

// GET /api/webhooks — List webhook configs
export async function GET() {
  try {
    const userId = await getCurrentUserId();

    const webhooks = await db.webhookConfig.findMany({
      where: userScope(userId),
      orderBy: { createdAt: 'desc' },
    });

    return NextResponse.json({ data: webhooks });
  } catch (error) {
    console.error('[API /webhooks] GET error:', error);
    return NextResponse.json({ error: 'Failed to fetch webhooks' }, { status: 500 });
  }
}

// POST /api/webhooks — Create webhook config
export async function POST(request: NextRequest) {
  try {
    const userId = await getCurrentUserId();

    const body = await request.json();
    const { name, url, secret, events, enabled } = body;

    if (!name || !url) {
      return NextResponse.json(
        { error: 'name and url are required' },
        { status: 400 },
      );
    }

    const webhook = await db.webhookConfig.create({
      data: {
        name,
        url,
        secret: secret || null,
        events: events ? JSON.stringify(events) : '[]',
        enabled: enabled !== undefined ? enabled : true,
        userId,
      },
    });

    return NextResponse.json({ data: webhook }, { status: 201 });
  } catch (error) {
    console.error('[API /webhooks] POST error:', error);
    return NextResponse.json({ error: 'Failed to create webhook' }, { status: 500 });
  }
}
