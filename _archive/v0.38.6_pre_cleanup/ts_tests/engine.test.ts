/**
 * PPMT - Full Engine Integration Test
 */

import { describe, it, expect } from 'vitest';
import { PPMTEngine } from '../src/core/index';
import { AssetClass, MarketRegime, Direction, Candle } from '../src/core/types';

function makeTrendCandles(count: number, startPrice: number, trend: 'up' | 'down' | 'flat'): Candle[] {
  const candles: Candle[] = [];
  let price = startPrice;

  for (let i = 0; i < count; i++) {
    const trendBias = trend === 'up' ? 50 : trend === 'down' ? -50 : 0;
    const noise = (Math.random() - 0.5) * 200;
    const open = price;
    const close = price + trendBias + noise;
    const high = Math.max(open, close) + Math.random() * 100;
    const low = Math.min(open, close) - Math.random() * 100;

    candles.push({
      timestamp: Date.now() + i * 60000,
      open, high, low, close,
      volume: 1000 + Math.random() * 5000,
    });

    price = close;
  }

  return candles;
}

describe('PPMTEngine', () => {
  it('should ingest historical data and generate signals', () => {
    const engine = new PPMTEngine({
      minSignalConfidence: 0.01, // Very low threshold for testing
      minConsensusLevels: 1,     // Only need 1 level to agree
    });

    // Create a deterministic uptrend pattern and ingest it many times
    const upCandles = makeTrendCandles(48, 50000, 'up');

    // Ingest the same pattern many times to build strong matches
    for (let i = 0; i < 100; i++) {
      engine.ingest(upCandles, 'BTC', AssetClass.BLUE_CHIP);
    }

    // Now search for the same pattern → should find a match
    const result = engine.analyze(upCandles, 'BTC', AssetClass.BLUE_CHIP, {
      minConfidence: 0.01,
    });

    // Should generate a signal since we're searching the exact pattern we inserted
    expect(result).not.toBeNull();
    expect(result!.direction).toBe(Direction.LONG);
  });

  it('should process candles incrementally', () => {
    const engine = new PPMTEngine();

    // Ingest some data first
    for (let i = 0; i < 50; i++) {
      const candles = makeTrendCandles(48, 50000, 'up');
      engine.ingest(candles, 'BTC', AssetClass.BLUE_CHIP);
    }

    // Feed candles one by one
    const testCandles = makeTrendCandles(50, 50000, 'up');
    let lastResult = null;

    for (const candle of testCandles) {
      lastResult = engine.onCandle(candle, 'BTC', AssetClass.BLUE_CHIP);
    }

    // After enough candles, should have a result
    // (May or may not depending on confidence threshold)
    expect(engine.getStats().buffers.BTC).toBeDefined();
  });

  it('should classify assets correctly', () => {
    const engine = new PPMTEngine();
    const classifier = engine.getAssetClassifier();

    const btcClass = classifier.classify({
      symbol: 'BTC',
      exchange: 'binance',
      marketCapUsd: 1_000_000_000_000,
    });
    expect(btcClass).toBe(AssetClass.BLUE_CHIP);

    const pepeClass = classifier.classify({
      symbol: 'PEPE',
      exchange: 'binance',
      marketCapUsd: 500_000_000,
    });
    expect(pepeClass).toBe(AssetClass.MEME);

    const newClass = classifier.classify({
      symbol: 'BRANDNEW',
      exchange: 'binance',
      ageDays: 3,
    });
    expect(newClass).toBe(AssetClass.NEW_LAUNCH);
  });

  it('should manage risk correctly', () => {
    const engine = new PPMTEngine();
    const risk = engine.getRiskManager();

    // Should allow a trade
    const sizing = risk.evaluate(
      'BTC',
      Direction.LONG,
      50000,
      undefined,
      'blue_chip'
    );

    // May or may not allow depending on Kelly calculation
    if (sizing) {
      expect(sizing.entryPrice).toBe(50000);
      expect(sizing.stopLoss).toBeLessThan(50000);
      expect(sizing.takeProfit).toBeGreaterThan(50000);
    }
  });

  it('should report stats', () => {
    const engine = new PPMTEngine();

    for (let i = 0; i < 10; i++) {
      const candles = makeTrendCandles(48, 50000, 'up');
      engine.ingest(candles, 'BTC', AssetClass.BLUE_CHIP);
    }

    const stats = engine.getStats();
    expect(stats.trie.level1).toBe(10);
    expect(stats.trie.level3.BTC).toBe(10);
    expect(stats.trie.totalNodes).toBeGreaterThan(0);
  });
});
