import { NextResponse } from 'next/server';
import { getDb, type Asset } from '@/lib/ppmt-db';

export async function GET() {
  try {
    const db = getDb();

    const assets = db.prepare('SELECT * FROM assets ORDER BY candle_count DESC').all() as Asset[];

    // Prepare statements once for reuse
    const tfStmt = db.prepare('SELECT DISTINCT timeframe FROM ohlcv WHERE symbol = ?');
    const ohlcvStmt = db.prepare('SELECT COUNT(*) as c FROM ohlcv WHERE symbol = ?');
    const trieStmt = db.prepare('SELECT level, data FROM tries WHERE symbol = ?');
    const engineStmt = db.prepare('SELECT COUNT(*) as c FROM engine_states WHERE symbol = ?');
    const signalStmt = db.prepare('SELECT COUNT(*) as c FROM signals WHERE symbol = ?');

    // Get timeframes per symbol
    const enrichedAssets = assets.map(asset => {
      const timeframes = (tfStmt.all(asset.symbol) as { timeframe: string }[]).map(r => r.timeframe);
      const ohlcvCount = (ohlcvStmt.get(asset.symbol) as { c: number }).c;
      const trieLevels = (trieStmt.all(asset.symbol) as { level: string; data: string }[]).map(r => ({
        level: r.level,
        ...JSON.parse(r.data),
      }));
      const hasEngineState = (engineStmt.get(asset.symbol) as { c: number }).c > 0;
      const signalCount = (signalStmt.get(asset.symbol) as { c: number }).c;

      return {
        ...asset,
        timeframes,
        ohlcvCount,
        trieLevels,
        hasEngineState,
        signalCount,
      };
    });

    return NextResponse.json({ data: enrichedAssets });
  } catch (error) {
    console.error('Error reading PPMT assets:', error);
    return NextResponse.json(
      { error: 'Failed to read assets', details: String(error) },
      { status: 500 }
    );
  }
}
