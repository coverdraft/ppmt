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
  return 100 - (100 / (1 + avgGain / avgLoss));
}

function runBacktest(candles: Candle[]): {
  total_return: number;
  sharpe: number;
  max_dd: number;
  win_rate: number;
  total_trades: number;
  avg_pnl: number;
  best_trade: number;
  worst_trade: number;
  profit_factor: number;
} {
  if (candles.length < 50) {
    return { total_return: 0, sharpe: 0, max_dd: 0, win_rate: 0, total_trades: 0, avg_pnl: 0, best_trade: 0, worst_trade: 0, profit_factor: 0 };
  }

  const trades: { pnl_pct: number }[] = [];
  let equity = 10000;
  const equityCurve = [10000];
  let position: { direction: 'LONG' | 'SHORT'; entryPrice: number; entryTime: number } | null = null;

  for (let i = 20; i < candles.length; i++) {
    const candle = candles[i];
    const prevCandles = candles.slice(Math.max(0, i - 20), i);

    if (!position && i < candles.length - 10) {
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

    if (position) {
      const pnlPct = position.direction === 'LONG'
        ? ((candle.close - position.entryPrice) / position.entryPrice) * 100
        : ((position.entryPrice - candle.close) / position.entryPrice) * 100;

      const holdingBars = i - candles.findIndex(c => c.timestamp === position!.entryTime);
      const shouldExit = pnlPct > 3 || pnlPct < -2 || holdingBars > 24;

      if (shouldExit) {
        trades.push({ pnl_pct: parseFloat(pnlPct.toFixed(2)) });
        equity *= (1 + pnlPct / 100);
        equityCurve.push(equity);
        position = null;
      }
    }
  }

  if (position && candles.length > 0) {
    const lastCandle = candles[candles.length - 1];
    const pnlPct = position.direction === 'LONG'
      ? ((lastCandle.close - position.entryPrice) / position.entryPrice) * 100
      : ((position.entryPrice - lastCandle.close) / position.entryPrice) * 100;
    trades.push({ pnl_pct: parseFloat(pnlPct.toFixed(2)) });
    equity *= (1 + pnlPct / 100);
    equityCurve.push(equity);
  }

  if (trades.length === 0) {
    return { total_return: 0, sharpe: 0, max_dd: 0, win_rate: 0, total_trades: 0, avg_pnl: 0, best_trade: 0, worst_trade: 0, profit_factor: 0 };
  }

  const totalReturn = ((equity - 10000) / 10000) * 100;
  let peak = 10000;
  let maxDD = 0;
  for (const e of equityCurve) {
    if (e > peak) peak = e;
    const dd = ((peak - e) / peak) * 100;
    if (dd > maxDD) maxDD = dd;
  }

  const wins = trades.filter(t => t.pnl_pct > 0).length;
  const winRate = (wins / trades.length) * 100;
  const avgPnl = trades.reduce((a, b) => a + b.pnl_pct, 0) / trades.length;
  const bestTrade = Math.max(...trades.map(t => t.pnl_pct));
  const worstTrade = Math.min(...trades.map(t => t.pnl_pct));

  const grossProfit = trades.filter(t => t.pnl_pct > 0).reduce((a, b) => a + b.pnl_pct, 0);
  const grossLoss = Math.abs(trades.filter(t => t.pnl_pct < 0).reduce((a, b) => a + b.pnl_pct, 0));
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? 999 : 0;

  const returns = trades.map(t => t.pnl_pct);
  const avgReturn = returns.reduce((a, b) => a + b, 0) / returns.length;
  const stdReturn = Math.sqrt(returns.reduce((sum, r) => sum + (r - avgReturn) ** 2, 0) / returns.length);
  const sharpe = stdReturn > 0 ? (avgReturn / stdReturn) * Math.sqrt(365) : 0; // v0.19.1: crypto 365 days

  return {
    total_return: parseFloat(totalReturn.toFixed(2)),
    sharpe: parseFloat(sharpe.toFixed(2)),
    max_dd: parseFloat(maxDD.toFixed(2)),
    win_rate: parseFloat(winRate.toFixed(1)),
    total_trades: trades.length,
    avg_pnl: parseFloat(avgPnl.toFixed(2)),
    best_trade: parseFloat(bestTrade.toFixed(2)),
    worst_trade: parseFloat(worstTrade.toFixed(2)),
    profit_factor: parseFloat(profitFactor.toFixed(2)),
  };
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { timeframe = '1h' } = body;

    const db = getDb();
    if (!db) {
      return NextResponse.json({ error: 'PPMT database not found' }, { status: 404 });
    }

    try {
      const assets = db.prepare(
        `SELECT DISTINCT o.symbol, a.asset_class, a.candle_count,
                (SELECT COUNT(*) FROM ohlcv o2 WHERE o2.symbol = o.symbol AND o2.timeframe = ?) as tf_candles
         FROM ohlcv o
         LEFT JOIN assets a ON o.symbol = a.symbol
         WHERE o.timeframe = ?
         ORDER BY tf_candles DESC`
      ).all(timeframe, timeframe) as any[];

      const results = assets.map(asset => {
        const candles = db.prepare(
          'SELECT timestamp, open, high, low, close, volume FROM ohlcv WHERE symbol = ? AND timeframe = ? ORDER BY timestamp ASC'
        ).all(asset.symbol, timeframe) as Candle[];

        const stats = runBacktest(candles);

        return {
          symbol: asset.symbol,
          asset_class: asset.asset_class || 'unknown',
          candle_count: asset.tf_candles,
          ...stats,
        };
      });

      results.sort((a, b) => b.total_return - a.total_return);

      return NextResponse.json({
        data: {
          timeframe,
          assets_tested: results.length,
          results,
        },
      });
    } finally {
      db.close();
    }
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
