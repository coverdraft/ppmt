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

interface EquityPoint {
  time: number;
  equity: number;
}

function computeStats(trades: Trade[], equityCurve: EquityPoint[]) {
  if (trades.length === 0) {
    return {
      total_return: 0,
      sharpe: 0,
      max_dd: 0,
      win_rate: 0,
      total_trades: 0,
    };
  }

  const finalEquity = equityCurve.length > 0 ? equityCurve[equityCurve.length - 1].equity : 10000;
  const totalReturn = ((finalEquity - 10000) / 10000) * 100;

  // Max drawdown
  let peak = 10000;
  let maxDD = 0;
  for (const pt of equityCurve) {
    if (pt.equity > peak) peak = pt.equity;
    const dd = ((peak - pt.equity) / peak) * 100;
    if (dd > maxDD) maxDD = dd;
  }

  // Win rate
  const wins = trades.filter(t => t.pnl_pct > 0).length;
  const winRate = (wins / trades.length) * 100;

  // Sharpe ratio (simplified - annualized)
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
  };
}

// Simplified statistical backtest using signal data and OHLCV
function runBacktest(candles: Candle[], signals: any[]): { trades: Trade[]; equityCurve: EquityPoint[] } {
  const trades: Trade[] = [];
  const equityCurve: EquityPoint[] = [{ time: candles[0]?.timestamp || 0, equity: 10000 }];
  let equity = 10000;
  let position: { direction: 'LONG' | 'SHORT'; entryPrice: number; entryTime: number } | null = null;

  // Create a map of signal timestamps for quick lookup
  const signalMap = new Map<number, any>();
  for (const sig of signals) {
    signalMap.set(sig.timestamp, sig);
  }

  // Walk through candles
  for (let i = 20; i < candles.length; i++) {
    const candle = candles[i];

    // Check if we have a signal near this candle
    const nearbySignal = signals.find(s =>
      Math.abs(s.timestamp - candle.timestamp) < 3600000 // Within 1 hour
    );

    // Simple momentum-based simulation with signal influence
    const prevCandles = candles.slice(Math.max(0, i - 20), i);
    const momentum = (prevCandles[prevCandles.length - 1].close - prevCandles[0].open) / prevCandles[0].open;

    // Entry logic: Use signal direction if available, otherwise momentum
    if (!position && nearbySignal && i < candles.length - 10) {
      const isLong = nearbySignal.signal_type?.includes('LONG') || (!nearbySignal.signal_type?.includes('SHORT') && momentum > 0);
      const isShort = nearbySignal.signal_type?.includes('SHORT') || (!nearbySignal.signal_type?.includes('LONG') && momentum < -0.005);

      if (isLong || isShort) {
        position = {
          direction: isLong ? 'LONG' : 'SHORT',
          entryPrice: candle.close,
          entryTime: candle.timestamp,
        };
      }
    }

    // If no signal-based entry, use momentum-based entries (simplified pattern matching simulation)
    if (!position && i < candles.length - 10) {
      // Simulate pattern detection: consecutive direction candles
      let bullishCount = 0;
      let bearishCount = 0;
      for (let j = i - 5; j < i; j++) {
        if (candles[j].close > candles[j].open) bullishCount++;
        else bearishCount++;
      }

      // Strong trend detection as pattern match proxy
      const rsi = computeSimpleRSI(prevCandles);
      if (rsi !== null) {
        if (rsi < 35 && bullishCount >= 3) {
          position = { direction: 'LONG', entryPrice: candle.close, entryTime: candle.timestamp };
        } else if (rsi > 65 && bearishCount >= 3) {
          position = { direction: 'SHORT', entryPrice: candle.close, entryTime: candle.timestamp };
        }
      }
    }

    // Exit logic
    if (position) {
      const holdingBars = i - candles.findIndex(c => c.timestamp === position!.entryTime);
      const pnlPct = position.direction === 'LONG'
        ? ((candle.close - position.entryPrice) / position.entryPrice) * 100
        : ((position.entryPrice - candle.close) / position.entryPrice) * 100;

      const shouldExit = pnlPct > 3 || pnlPct < -2 || holdingBars > 24;
      // Also check for exit signals
      const exitSignal = signals.find(s =>
        s.signal_type?.includes('EXIT') && Math.abs(s.timestamp - candle.timestamp) < 3600000
      );

      if (shouldExit || exitSignal) {
        trades.push({
          entry_time: position.entryTime,
          exit_time: candle.timestamp,
          direction: position.direction,
          entry_price: position.entryPrice,
          exit_price: candle.close,
          pnl_pct: parseFloat(pnlPct.toFixed(2)),
          holding_bars: holdingBars,
        });

        equity *= (1 + pnlPct / 100);
        equityCurve.push({ time: candle.timestamp, equity: parseFloat(equity.toFixed(2)) });
        position = null;
      }
    }
  }

  // Close any open position at the end
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
    equity *= (1 + pnlPct / 100);
    equityCurve.push({ time: lastCandle.timestamp, equity: parseFloat(equity.toFixed(2)) });
  }

  return { trades, equityCurve };
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

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { symbol, timeframe = '1h', start_date, end_date } = body;

    if (!symbol) {
      return NextResponse.json({ error: 'symbol is required' }, { status: 400 });
    }

    const db = getDb();
    if (!db) {
      return NextResponse.json({ error: 'PPMT database not found. Run ppmt init first.' }, { status: 404 });
    }

    try {
      // Load OHLCV data
      let query = 'SELECT timestamp, open, high, low, close, volume FROM ohlcv WHERE symbol = ? AND timeframe = ?';
      const params: any[] = [symbol, timeframe];

      if (start_date) {
        const startTs = new Date(start_date).getTime();
        query += ' AND timestamp >= ?';
        params.push(startTs);
      }
      if (end_date) {
        const endTs = new Date(end_date).getTime();
        query += ' AND timestamp <= ?';
        params.push(endTs);
      }

      query += ' ORDER BY timestamp ASC';
      const candles = db.prepare(query).all(...params) as Candle[];

      if (candles.length < 50) {
        return NextResponse.json({
          data: {
            trades: [],
            equity_curve: [],
            stats: { total_return: 0, sharpe: 0, max_dd: 0, win_rate: 0, total_trades: 0 },
            message: `Insufficient data: only ${candles.length} candles found. Need at least 50.`,
          },
        });
      }

      // Load signals for this symbol
      let signals: any[] = [];
      try {
        signals = db.prepare(
          'SELECT * FROM signals WHERE symbol = ? ORDER BY timestamp ASC'
        ).all(symbol) as any[];
      } catch { /* signals table might not have data */ }

      // Load trie data for pattern context
      let trieData: any = {};
      try {
        const tries = db.prepare('SELECT level, data FROM tries WHERE symbol = ?').all(symbol) as any[];
        for (const t of tries) {
          try {
            trieData[t.level] = JSON.parse(t.data);
          } catch { /* ignore */ }
        }
      } catch { /* ignore */ }

      // Run the backtest
      const { trades, equityCurve } = runBacktest(candles, signals);
      const stats = computeStats(trades, equityCurve);

      // Convert equity curve to lightweight-charts format
      const equityCurveFormatted = equityCurve.map(pt => ({
        time: Math.floor(pt.time / 1000),
        value: pt.equity,
      }));

      // Remove duplicate times (keep last)
      const seenTimes = new Set<number>();
      const uniqueEquityCurve = equityCurveFormatted.filter(pt => {
        if (seenTimes.has(pt.time)) return false;
        seenTimes.add(pt.time);
        return true;
      });

      return NextResponse.json({
        data: {
          trades,
          equity_curve: uniqueEquityCurve,
          stats,
          symbol,
          timeframe,
          candles_used: candles.length,
          trie_levels: Object.keys(trieData).length,
          signals_used: signals.length,
        },
      });
    } catch (error: any) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    } finally {
      db.close();
    }
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
