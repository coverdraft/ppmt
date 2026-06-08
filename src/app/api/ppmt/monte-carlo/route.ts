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

interface Candle {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface Trade {
  entry_time: number;
  exit_time: number;
  direction: 'LONG' | 'SHORT';
  entry_price: number;
  exit_price: number;
  pnl_pct: number;
  holding_bars: number;
}

function computeSimpleRSI(candles: Candle[]): number | null {
  if (candles.length < 14) return null;
  const changes: number[] = [];
  for (let i = 1; i < candles.length; i++) {
    changes.push(candles[i].close - candles[i - 1].close);
  }
  const gains = changes.filter(c => c > 0);
  const losses = changes.filter(c => c < 0).map(c => Math.abs(c));
  const avgGain = gains.length > 0 ? gains.reduce((a, b) => a + b, 0) / changes.length : 0;
  const avgLoss = losses.length > 0 ? losses.reduce((a, b) => a + b, 0) / changes.length : 0;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - (100 / (1 + rs));
}

function runSingleBacktest(candles: Candle[], signals: any[]): Trade[] {
  const trades: Trade[] = [];
  let position: { direction: 'LONG' | 'SHORT'; entryPrice: number; entryTime: number } | null = null;

  for (let i = 20; i < candles.length; i++) {
    const candle = candles[i];
    const prevCandles = candles.slice(Math.max(0, i - 20), i);

    const nearbySignal = signals.find(s =>
      Math.abs(s.timestamp - candle.timestamp) < 3600000
    );

    if (!position && i < candles.length - 10) {
      const isLong = nearbySignal?.signal_type?.includes('LONG') || false;
      const isShort = nearbySignal?.signal_type?.includes('SHORT') || false;

      if (isLong || isShort) {
        position = { direction: isLong ? 'LONG' : 'SHORT', entryPrice: candle.close, entryTime: candle.timestamp };
      } else {
        let bullishCount = 0;
        let bearishCount = 0;
        for (let j = i - 5; j < i; j++) {
          if (candles[j].close > candles[j].open) bullishCount++;
          else bearishCount++;
        }
        const rsi = computeSimpleRSI(prevCandles);
        if (rsi !== null) {
          if (rsi < 35 && bullishCount >= 3) {
            position = { direction: 'LONG', entryPrice: candle.close, entryTime: candle.timestamp };
          } else if (rsi > 65 && bearishCount >= 3) {
            position = { direction: 'SHORT', entryPrice: candle.close, entryTime: candle.timestamp };
          }
        }
      }
    }

    if (position) {
      const holdingBars = i - candles.findIndex(c => c.timestamp === position!.entryTime);
      const pnlPct = position.direction === 'LONG'
        ? ((candle.close - position.entryPrice) / position.entryPrice) * 100
        : ((position.entryPrice - candle.close) / position.entryPrice) * 100;

      const shouldExit = pnlPct > 3 || pnlPct < -2 || holdingBars > 24;
      if (shouldExit) {
        trades.push({
          entry_time: position.entryTime,
          exit_time: candle.timestamp,
          direction: position.direction,
          entry_price: position.entryPrice,
          exit_price: candle.close,
          pnl_pct: parseFloat(pnlPct.toFixed(2)),
          holding_bars: holdingBars,
        });
        position = null;
      }
    }
  }

  if (position && candles.length > 0) {
    const lastCandle = candles[candles.length - 1];
    const pnlPct = position.direction === 'LONG'
      ? ((lastCandle.close - position.entryPrice) / position.entryPrice) * 100
      : ((position.entryPrice - lastCandle.close) / position.entryPrice) * 100;
    trades.push({
      entry_time: position.entryTime,
      exit_time: lastCandle.timestamp,
      direction: position.direction,
      entry_price: position.entryPrice,
      exit_price: lastCandle.close,
      pnl_pct: parseFloat(pnlPct.toFixed(2)),
      holding_bars: candles.length - candles.findIndex(c => c.timestamp === position!.entryTime),
    });
  }

  return trades;
}

function runMonteCarlo(trades: Trade[], simulations: number = 500): {
  distribution: number[];
  finalEquities: number[];
  var95: number;
  cvar95: number;
  meanReturn: number;
  medianReturn: number;
  bestReturn: number;
  worstReturn: number;
  pctProfitable: number;
  equityPaths: number[][];
} {
  if (trades.length === 0) {
    return {
      distribution: [],
      finalEquities: [],
      var95: 0,
      cvar95: 0,
      meanReturn: 0,
      medianReturn: 0,
      bestReturn: 0,
      worstReturn: 0,
      pctProfitable: 0,
      equityPaths: [],
    };
  }

  const pnlValues = trades.map(t => t.pnl_pct / 100);
  const finalEquities: number[] = [];
  const equityPaths: number[][] = [];
  const startingCapital = 10000;

  for (let sim = 0; sim < simulations; sim++) {
    let equity = startingCapital;
    const path = [equity];
    const shuffled = [...pnlValues].sort(() => Math.random() - 0.5);

    for (const pnl of shuffled) {
      const positionSize = equity * 0.02;
      const pnlDollar = positionSize * (pnl / 0.02);
      equity = Math.max(equity + pnlDollar * 0.5, equity * 0.5);
      path.push(parseFloat(equity.toFixed(2)));
    }

    finalEquities.push(equity);
    equityPaths.push(path);
  }

  const returns = finalEquities.map(e => ((e - startingCapital) / startingCapital) * 100);
  returns.sort((a, b) => a - b);

  const varIndex = Math.floor(returns.length * 0.05);
  const var95 = returns[varIndex] || 0;

  const tailReturns = returns.slice(0, varIndex + 1);
  const cvar95 = tailReturns.length > 0 ? tailReturns.reduce((a, b) => a + b, 0) / tailReturns.length : 0;

  const meanReturn = returns.reduce((a, b) => a + b, 0) / returns.length;
  const medianReturn = returns[Math.floor(returns.length / 2)];
  const bestReturn = returns[returns.length - 1];
  const worstReturn = returns[0];
  const pctProfitable = (returns.filter(r => r > 0).length / returns.length) * 100;

  const bucketCount = 20;
  const minReturn = worstReturn;
  const maxReturn = bestReturn;
  const bucketSize = (maxReturn - minReturn) / bucketCount || 1;
  const distribution = new Array(bucketCount).fill(0);
  for (const r of returns) {
    const idx = Math.min(Math.floor((r - minReturn) / bucketSize), bucketCount - 1);
    distribution[idx]++;
  }

  return {
    distribution,
    finalEquities,
    var95: parseFloat(var95.toFixed(2)),
    cvar95: parseFloat(cvar95.toFixed(2)),
    meanReturn: parseFloat(meanReturn.toFixed(2)),
    medianReturn: parseFloat(medianReturn.toFixed(2)),
    bestReturn: parseFloat(bestReturn.toFixed(2)),
    worstReturn: parseFloat(worstReturn.toFixed(2)),
    pctProfitable: parseFloat(pctProfitable.toFixed(1)),
    equityPaths: equityPaths.slice(0, 50),
  };
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { symbol, timeframe = '1h', simulations = 500 } = body;

    if (!symbol) {
      return NextResponse.json({ error: 'symbol is required' }, { status: 400 });
    }

    const db = getDb();
    if (!db) {
      return NextResponse.json({ error: 'PPMT database not found' }, { status: 404 });
    }

    try {
      const candles = db.prepare(
        'SELECT timestamp, open, high, low, close, volume FROM ohlcv WHERE symbol = ? AND timeframe = ? ORDER BY timestamp ASC'
      ).all(symbol, timeframe) as Candle[];

      if (candles.length < 50) {
        return NextResponse.json({
          data: {
            symbol,
            timeframe,
            message: `Insufficient data: only ${candles.length} candles. Need at least 50.`,
            mc: null,
            base_trades: 0,
            simulations: 0,
            stats: { var95: 0, cvar95: 0, meanReturn: 0, medianReturn: 0, bestReturn: 0, worstReturn: 0, pctProfitable: 0 },
          },
        });
      }

      let signals: any[] = [];
      try {
        signals = db.prepare('SELECT * FROM signals WHERE symbol = ? ORDER BY timestamp ASC').all(symbol) as any[];
      } catch { /* no signals */ }

      const trades = runSingleBacktest(candles, signals);

      if (trades.length < 5) {
        return NextResponse.json({
          data: {
            symbol,
            timeframe,
            message: `Not enough trades (${trades.length}) for Monte Carlo. Need at least 5.`,
            mc: null,
            base_trades: trades.length,
            simulations: 0,
            stats: { var95: 0, cvar95: 0, meanReturn: 0, medianReturn: 0, bestReturn: 0, worstReturn: 0, pctProfitable: 0 },
          },
        });
      }

      const mc = runMonteCarlo(trades, Math.min(simulations, 2000));

      return NextResponse.json({
        data: {
          symbol,
          timeframe,
          base_trades: trades.length,
          simulations: Math.min(simulations, 2000),
          mc,
          stats: {
            var95: mc.var95,
            cvar95: mc.cvar95,
            meanReturn: mc.meanReturn,
            medianReturn: mc.medianReturn,
            bestReturn: mc.bestReturn,
            worstReturn: mc.worstReturn,
            pctProfitable: mc.pctProfitable,
          },
        },
      });
    } finally {
      db.close();
    }
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
