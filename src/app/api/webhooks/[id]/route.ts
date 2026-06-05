import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

// PATCH /api/webhooks/[id] — Update webhook config
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const body = await request.json();

    const data: Record<string, unknown> = {};
    if (body.name !== undefined) data.name = body.name;
    if (body.url !== undefined) data.url = body.url;
    if (body.secret !== undefined) data.secret = body.secret;
    if (body.events !== undefined) data.events = JSON.stringify(body.events);
    if (body.enabled !== undefined) data.enabled = body.enabled;

    const webhook = await db.webhookConfig.update({
      where: { id },
      data,
    });

    return NextResponse.json({ data: webhook });
  } catch (error) {
    console.error('[API /webhooks/[id]] PATCH error:', error);
    return NextResponse.json({ error: 'Failed to update webhook' }, { status: 500 });
  }
}

// DELETE /api/webhooks/[id]
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    await db.webhookConfig.delete({ where: { id } });
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('[API /webhooks/[id]] DELETE error:', error);
    return NextResponse.json({ error: 'Failed to delete webhook' }, { status: 500 });
  }
}
