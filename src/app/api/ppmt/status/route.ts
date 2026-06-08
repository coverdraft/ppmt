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

export async function GET() {
  const db = getDb();
  if (!db) {
    return NextResponse.json({ error: 'PPMT database not found. Run ppmt init first.' }, { status: 404 });
  }

  try {
    // Asset overview
    const assets = db.prepare('SELECT symbol, asset_class, weight_profile, candle_count, first_seen, last_updated FROM assets ORDER BY symbol').all();

    // Total candles
    const candleStats = db.prepare('SELECT symbol, timeframe, COUNT(*) as count, MIN(timestamp) as first_ts, MAX(timestamp) as last_ts FROM ohlcv GROUP BY symbol, timeframe').all();

    // Trie stats (include data for pattern counts)
    const tries = db.prepare('SELECT symbol, level, data, updated_at FROM tries').all();

    // Signal count
    let signalCount = 0;
    try {
      const row = db.prepare('SELECT COUNT(*) as cnt FROM signals').get() as any;
      signalCount = row?.cnt ?? 0;
    } catch { /* signals table might be empty */ }

    // Engine states
    const engineStates = db.prepare('SELECT symbol, data, updated_at FROM engine_states').all();

    // DB file size
    const fs = await import('fs');
    let dbSize = 0;
    try {
      const stat = fs.statSync(path.join(os.homedir(), '.ppmt', 'ppmt.db'));
      dbSize = stat.size;
    } catch { /* ignore */ }

    // Organize data
    const assetsWithDetails = assets.map((asset: any) => {
      const assetCandles = candleStats.filter((c: any) => c.symbol === asset.symbol);
      const assetTries = tries.filter((t: any) => t.symbol === asset.symbol);
      const engineState = engineStates.find((e: any) => e.symbol === asset.symbol);

      let trieDetails: any = {};
      let totalPatterns = 0;
      try {
        for (const t of assetTries) {
          const data = JSON.parse((t as any).data);
          trieDetails[(t as any).level] = {
            patternCount: data.pattern_count ?? 0,
            maxDepth: data.max_depth ?? 0,
          };
          totalPatterns += data.pattern_count ?? 0;
        }
      } catch { /* ignore parse errors */ }

      let engineData: any = null;
      try {
        if (engineState) {
          engineData = JSON.parse((engineState as any).data);
        }
      } catch { /* ignore */ }

      // Calculate actual OHLCV candle count (not the claimed one from assets table)
      const actualCandleCount = assetCandles.reduce((sum: number, c: any) => sum + c.count, 0);
      const tfCount = assetCandles.length;
      const trieLevelCount = Object.keys(trieDetails).length;

      // Data sufficiency: 50K+ candles, 4+ timeframes, 4 trie levels for reliable prediction
      const candleScore = Math.min(actualCandleCount / 50000, 1);
      const tfScore = Math.min(tfCount / 6, 1);
      const trieScore = Math.min(trieLevelCount / 4, 1);
      const sufficiencyScore = (candleScore * 0.5 + tfScore * 0.25 + trieScore * 0.25) * 100;
      const sufficiency: 'sufficient' | 'partial' | 'insufficient' =
        sufficiencyScore >= 70 ? 'sufficient' : sufficiencyScore >= 35 ? 'partial' : 'insufficient';

      return {
        symbol: asset.symbol,
        assetClass: asset.asset_class,
        weightProfile: asset.weight_profile,
        candleCount: actualCandleCount, // Use actual OHLCV count, not claimed
        claimedCandleCount: asset.candle_count, // Keep claimed for reference
        firstSeen: asset.first_seen,
        lastUpdated: asset.last_updated,
        timeframes: assetCandles.map((c: any) => ({
          timeframe: c.timeframe,
          count: c.count,
          firstTs: c.first_ts,
          lastTs: c.last_ts,
        })),
        tries: trieDetails,
        totalPatterns,
        engineState: engineData,
        sufficiency,
        sufficiencyScore: Math.round(sufficiencyScore),
        trieLevelCount,
      };
    });

    const totalCandles = candleStats.reduce((sum: number, c: any) => sum + c.count, 0);
    const totalPatterns = assetsWithDetails.reduce((sum: number, a: any) => sum + a.totalPatterns, 0);

    return NextResponse.json({
      data: {
        assets: assetsWithDetails,
        totalAssets: assets.length,
        totalCandles,
        totalPatterns,
        signalCount,
        dbSizeBytes: dbSize,
        dbSizeMB: (dbSize / 1024 / 1024).toFixed(1),
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  } finally {
    db.close();
  }
}
