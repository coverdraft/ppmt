import { NextResponse } from 'next/server';
import Database from 'better-sqlite3';
import os from 'os';
import path from 'path';

export const dynamic = 'force-dynamic';

const DB_PATH = path.join(os.homedir(), '.ppmt', 'ppmt.db');

function getDb(): Database.Database | null {
  try {
    const db = new Database(DB_PATH, { readonly: true });
    db.pragma('journal_mode = WAL');
    return db;
  } catch {
    return null;
  }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get('symbol');
  const timeframe = searchParams.get('timeframe') || '1h';
  const limit = parseInt(searchParams.get('limit') || '2000');

  if (!symbol) {
    return NextResponse.json({ error: 'symbol parameter required' }, { status: 400 });
  }

  const db = getDb();
  if (!db) {
    return NextResponse.json({ error: 'PPMT database not found. Run ppmt init first.' }, { status: 404 });
  }

  try {
    const candles = db.prepare(
      'SELECT timestamp, open, high, low, close, volume FROM ohlcv WHERE symbol = ? AND timeframe = ? ORDER BY timestamp ASC LIMIT ?'
    ).all(symbol, timeframe, limit) as any[];

    const formatted = candles.map(c => ({
      time: Math.floor(c.timestamp / 1000), // Convert ms to seconds for lightweight-charts
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
    }));

    return NextResponse.json({
      data: {
        candles: formatted,
        symbol,
        timeframe,
        count: formatted.length,
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  } finally {
    db.close();
  }
}
