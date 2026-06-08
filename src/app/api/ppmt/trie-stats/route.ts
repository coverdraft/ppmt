import { NextResponse } from 'next/server';
import Database from 'better-sqlite3';
import os from 'os';
import path from 'path';

export const dynamic = 'force-dynamic';

function getDb() {
  const dbPath = path.join(os.homedir(), '.ppmt', 'ppmt.db');
  try {
    return new Database(dbPath, { readonly: true });
  } catch {
    return null;
  }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get('symbol');

  if (!symbol) {
    return NextResponse.json({ error: 'symbol parameter required' }, { status: 400 });
  }

  const db = getDb();
  if (!db) {
    return NextResponse.json({ error: 'PPMT database not found' }, { status: 404 });
  }

  try {
    // Load tries
    const tries = db.prepare('SELECT level, data FROM tries WHERE symbol = ?').all(symbol);
    
    const trieStats: any = {};
    for (const t of tries) {
      try {
        const data = JSON.parse((t as any).data);
        trieStats[(t as any).level] = {
          patternCount: data.pattern_count ?? 0,
          maxDepth: data.max_depth ?? 0,
          name: data.name ?? '',
        };
      } catch { /* ignore */ }
    }

    // Load engine state
    const stateRow = db.prepare('SELECT data FROM engine_states WHERE symbol = ?').get(symbol) as any;
    let engineState = null;
    try {
      if (stateRow) engineState = JSON.parse(stateRow.data);
    } catch { /* ignore */ }

    // Load candle stats
    const candleStats = db.prepare(
      'SELECT timeframe, COUNT(*) as count, MIN(timestamp) as first_ts, MAX(timestamp) as last_ts FROM ohlcv WHERE symbol = ? GROUP BY timeframe'
    ).all(symbol);

    return NextResponse.json({
      data: {
        symbol,
        tries: trieStats,
        engineState,
        candleStats: candleStats.map((c: any) => ({
          timeframe: c.timeframe,
          count: c.count,
          firstTs: c.first_ts,
          lastTs: c.last_ts,
        })),
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  } finally {
    db.close();
  }
}
