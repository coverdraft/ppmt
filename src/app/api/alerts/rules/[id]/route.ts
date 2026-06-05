import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

// PATCH /api/alerts/rules/[id] — Update rule
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const body = await request.json();

    const data: Record<string, unknown> = {};
    if (body.name !== undefined) data.name = body.name;
    if (body.enabled !== undefined) data.enabled = body.enabled;
    if (body.category !== undefined) data.category = body.category;
    if (body.condition !== undefined) {
      data.condition = typeof body.condition === 'string'
        ? body.condition
        : JSON.stringify(body.condition);
    }
    if (body.severity !== undefined) data.severity = body.severity;
    if (body.channels !== undefined) data.channels = JSON.stringify(body.channels);
    if (body.cooldownMin !== undefined) data.cooldownMin = body.cooldownMin;

    const rule = await db.alertRule.update({
      where: { id },
      data,
    });

    return NextResponse.json({ data: rule });
  } catch (error) {
    console.error('[API /alerts/rules/[id]] PATCH error:', error);
    return NextResponse.json({ error: 'Failed to update rule' }, { status: 500 });
  }
}

// DELETE /api/alerts/rules/[id]
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    await db.alertRule.delete({ where: { id } });
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('[API /alerts/rules/[id]] DELETE error:', error);
    return NextResponse.json({ error: 'Failed to delete rule' }, { status: 500 });
  }
}
