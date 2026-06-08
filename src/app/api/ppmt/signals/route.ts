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
  const limit = parseInt(searchParams.get('limit') || '50');

  const db = getDb();
  if (!db) {
    return NextResponse.json({ error: 'PPMT database not found' }, { status: 404 });
  }

  try {
    let signals;
    if (symbol) {
      signals = db.prepare(
        'SELECT * FROM signals WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?'
      ).all(symbol, limit);
    } else {
      signals = db.prepare(
        'SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?'
      ).all(limit);
    }

    const parsed = signals.map((s: any) => {
      let matchedPattern = [];
      let predictedPath = [];
      try {
        matchedPattern = JSON.parse(s.matched_pattern || '[]');
      } catch { /* ignore */ }
      try {
        predictedPath = JSON.parse(s.predicted_path || '[]');
      } catch { /* ignore */ }

      return {
        ...s,
        matchedPattern,
        predictedPath,
      };
    });

    return NextResponse.json({ data: parsed });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  } finally {
    db.close();
  }
}
