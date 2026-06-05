import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

// PATCH /api/alerts/[id] — Mark read/dismiss
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const body = await request.json();

    const data: Record<string, unknown> = {};
    if (body.isRead === true) {
      data.isRead = true;
      data.readAt = new Date();
    }
    if (body.isDismissed === true) {
      data.isDismissed = true;
      data.dismissedAt = new Date();
    }

    const alert = await db.alert.update({
      where: { id },
      data,
    });

    return NextResponse.json({ data: alert });
  } catch (error) {
    console.error('[API /alerts/[id]] PATCH error:', error);
    return NextResponse.json({ error: 'Failed to update alert' }, { status: 500 });
  }
}

// DELETE /api/alerts/[id]
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    await db.alert.delete({ where: { id } });
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('[API /alerts/[id]] DELETE error:', error);
    return NextResponse.json({ error: 'Failed to delete alert' }, { status: 500 });
  }
}
