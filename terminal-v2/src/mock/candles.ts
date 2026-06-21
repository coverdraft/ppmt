import type { CandlestickData, Time } from 'lightweight-charts';

/**
 * Generate 100 mock DOGE/USDT candles for ENTREGABLE 1.
 * Base price ~0.165 with realistic micro-structure.
 */
export function generateMockCandles(): CandlestickData<Time>[] {
  const candles: CandlestickData<Time>[] = [];
  let price = 0.165;
  const baseTime = Math.floor(new Date('2024-06-01T00:00:00Z').getTime() / 1000);

  for (let i = 0; i < 100; i++) {
    const drift = (Math.random() - 0.48) * 0.002;
    const noise = (Math.random() - 0.5) * 0.0008;
    const spike = Math.random() > 0.92 ? (Math.random() - 0.5) * 0.004 : 0;

    const open = price;
    const close = open + drift + noise + spike;
    const wickUp = Math.random() * 0.0012;
    const wickDown = Math.random() * 0.0012;
    const high = Math.max(open, close) + wickUp;
    const low = Math.min(open, close) - wickDown;

    candles.push({
      time: (baseTime + i * 3600) as Time,
      open: parseFloat(open.toFixed(6)),
      high: parseFloat(high.toFixed(6)),
      low: parseFloat(low.toFixed(6)),
      close: parseFloat(close.toFixed(6)),
    });

    price = close;
  }

  return candles;
}

/**
 * Mock PositionState for ENTREGABLE 1 simulation.
 * LONG on DOGE/USDT with SL/TP from expected_move.
 */
export function getMockPosition() {
  const entryPrice = 0.165;
  const expectedMove = 0.002; // 0.2% expected move per SAX step

  return {
    symbol: 'DOGE/USDT',
    direction: 'LONG' as const,
    status: 'ACTIVE' as const,
    entry_price: entryPrice,
    entry_time: '2024-06-01T12:00:00Z',
    size_usdt: 100,
    current_sl: entryPrice - (expectedMove * 1.2),     // 0.1626
    current_tp: entryPrice + (expectedMove * 2.5),      // 0.1700
    catastrophic_sl: entryPrice - (expectedMove * 3.0),  // 0.1590
    expected_sequence: [['d', 'x'], ['e', 'y'], ['f', 'z'], ['a', 'x'], ['b', 'y']],
    sequence_index: 0,
  };
}
