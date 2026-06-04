import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { getCurrentUserId, userScope } from '@/lib/services/shared/user-data-filter';

// GET /api/alerts — List alerts with filters
export async function GET(request: NextRequest) {
  try {
    const userId = await getCurrentUserId();

    const { searchParams } = new URL(request.url);
    const category = searchParams.get('category') || undefined;
    const severity = searchParams.get('severity') || undefined;
    const isReadParam = searchParams.get('isRead');
    const isRead = isReadParam !== null ? isReadParam === 'true' : undefined;
    const limit = parseInt(searchParams.get('limit') || '50');
    const offset = parseInt(searchParams.get('offset') || '0');

    const where: Record<string, unknown> = { isDismissed: false, ...userScope(userId) };
    if (category) where.category = category;
    if (severity) where.severity = severity;
    if (isRead !== undefined) where.isRead = isRead;

    const [alerts, total] = await Promise.all([
      db.alert.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        take: limit,
        skip: offset,
      }),
      db.alert.count({ where }),
    ]);

    const unreadCount = await db.alert.count({
      where: { isRead: false, isDismissed: false, ...userScope(userId) },
    });

    return NextResponse.json({
      data: alerts,
      total,
      unreadCount,
    });
  } catch (error) {
    console.error('[API /alerts] GET error:', error);
    return NextResponse.json({ error: 'Failed to fetch alerts' }, { status: 500 });
  }
}

// POST /api/alerts — Create a manual alert
export async function POST(request: NextRequest) {
  try {
    const userId = await getCurrentUserId();

    const body = await request.json();
    const { title, message, category, severity, metadata, linkTo, ruleId } = body;

    if (!title || !message || !category) {
      return NextResponse.json(
        { error: 'title, message, and category are required' },
        { status: 400 },
      );
    }

    const alert = await db.alert.create({
      data: {
        title,
        message,
        category,
        severity: severity || 'INFO',
        metadata: metadata ? JSON.stringify(metadata) : null,
        linkTo: linkTo || null,
        ruleId: ruleId || null,
        userId,
      },
    });

    // Push via WS bridge
    const { wsBridge } = await import('@/lib/ws-bridge');
    await wsBridge.pushAlert({
      id: alert.id,
      title: alert.title,
      message: alert.message,
      category: alert.category,
      severity: alert.severity,
      metadata,
      linkTo: alert.linkTo || undefined,
      createdAt: alert.createdAt.toISOString(),
    });

    return NextResponse.json({ data: alert }, { status: 201 });
  } catch (error) {
    console.error('[API /alerts] POST error:', error);
    return NextResponse.json({ error: 'Failed to create alert' }, { status: 500 });
  }
}
