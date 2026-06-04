import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const { db } = await import('@/lib/db');
    const events = await db.userEvent.findMany({
      where: {
        OR: [
          { eventType: 'STOP_LOSS_HIT' },
          { eventType: 'TAKE_PROFIT_HIT' },
        ],
      },
      orderBy: { createdAt: 'desc' },
      take: 500,
    });

    // Cluster stop losses and take profits by price range
    const stopLossClusters: { price: number; count: number; density: number }[] = [];
    const takeProfitClusters: { price: number; count: number; density: number }[] = [];

    const stopLosses = events.filter(e => e.eventType === 'STOP_LOSS_HIT');
    const takeProfits = events.filter(e => e.eventType === 'TAKE_PROFIT_HIT');

    // Group by price ranges
    const priceBuckets = (items: typeof events, field: 'stopLoss' | 'takeProfit') => {
      const buckets: Record<string, number> = {};
      for (const item of items) {
        const price = item[field];
        if (price) {
          const bucket = Math.round(price * 100) / 100;
          buckets[bucket] = (buckets[bucket] || 0) + 1;
        }
      }
      return Object.entries(buckets)
        .map(([price, count]) => ({
          price: parseFloat(price),
          count,
          density: count / items.length,
        }))
        .sort((a, b) => b.count - a.count);
    };

    return NextResponse.json({
      stopLossClusters: priceBuckets(stopLosses, 'stopLoss'),
      takeProfitClusters: priceBuckets(takeProfits, 'takeProfit'),
      totalEvents: events.length,
      smartMoneyEntries: events.filter(e =>
        e.eventType === 'OPEN_POSITION' && e.pnl && e.pnl > 0
      ).length,
    });
  } catch (error) {
    console.error('Error fetching heatmap:', error);
    return NextResponse.json({ error: 'Failed to fetch heatmap data' }, { status: 500 });
  }
}
