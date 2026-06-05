import { NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/export/config
 * Export user configuration: AlertRules, WebhookConfigs
 */
export async function GET() {
  try {
    const [alertRules, webhookConfigs] = await Promise.all([
      db.alertRule.findMany({ orderBy: { createdAt: 'desc' } }),
      db.webhookConfig.findMany({ orderBy: { createdAt: 'desc' } }),
    ]);

    const exportData = {
      version: '1.0',
      exportedAt: new Date().toISOString(),
      type: 'config',
      alertRules: alertRules.map((r) => ({
        ...r,
        lastTriggeredAt: r.lastTriggeredAt?.toISOString() ?? null,
        createdAt: r.createdAt.toISOString(),
        updatedAt: r.updatedAt.toISOString(),
      })),
      webhookConfigs: webhookConfigs.map((w) => ({
        ...w,
        lastDeliveryAt: w.lastDeliveryAt?.toISOString() ?? null,
        createdAt: w.createdAt.toISOString(),
        updatedAt: w.updatedAt.toISOString(),
      })),
    };

    const date = new Date().toISOString().split('T')[0];
    const body = JSON.stringify(exportData, null, 2);

    return new NextResponse(body, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Content-Disposition': `attachment; filename="cryptoquant-config-${date}.json"`,
      },
    });
  } catch (error) {
    console.error('[API /export/config] Error:', error);
    return NextResponse.json(
      { error: 'Failed to export configuration' },
      { status: 500 },
    );
  }
}
