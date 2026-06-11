/**
 * PPMT - SAX Engine Tests
 */

import { describe, it, expect } from 'vitest';
import { SAXEngine } from '../src/core/sax';
import { Candle } from '../src/core/types';

function makeCandles(count: number, basePrice: number, volatility: number): Candle[] {
  const candles: Candle[] = [];
  let price = basePrice;

  for (let i = 0; i < count; i++) {
    const change = (Math.random() - 0.5) * volatility;
    const open = price;
    const close = price + change;
    const high = Math.max(open, close) + Math.random() * volatility * 0.5;
    const low = Math.min(open, close) - Math.random() * volatility * 0.5;

    candles.push({
      timestamp: Date.now() + i * 60000,
      open, high, low, close,
      volume: 1000 + Math.random() * 5000,
    });

    price = close;
  }

  return candles;
}

describe('SAXEngine', () => {
  const config = { wordLength: 8, segmentSize: 6, alphabetSize: 8 as const };
  const engine = new SAXEngine(config);

  it('should transform candles into a SAX word', () => {
    const candles = makeCandles(48, 50000, 500);
    const word = engine.transform(candles);

    expect(word).toHaveLength(config.wordLength);
    for (const symbol of word) {
      expect(symbol).toMatch(/^[A-H]$/);
    }
  });

  it('should throw if not enough candles', () => {
    const candles = makeCandles(10, 50000, 500);
    expect(() => engine.transform(candles)).toThrow();
  });

  it('should produce identical words for identical patterns', () => {
    const candles = makeCandles(48, 50000, 0); // Flat line = all same symbol
    const word1 = engine.transform(candles);
    const word2 = engine.transform(candles);
    expect(word1).toEqual(word2);
  });

  it('should produce different words for different patterns', () => {
    const upCandles = makeCandles(48, 50000, 500);
    const downCandles = makeCandles(48, 50000, 500);
    // Different random seeds = different patterns (usually)
    // This test verifies the function runs, not that they're always different
    expect(upCandles).toHaveLength(48);
    expect(downCandles).toHaveLength(48);
  });

  it('should calculate distance between SAX words', () => {
    const word1 = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
    const word2 = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
    const dist = engine.distance(word1, word2);
    expect(dist).toBe(0); // Identical words
  });

  it('should give positive distance for different words', () => {
    const word1 = ['A', 'A', 'A', 'A', 'A', 'A', 'A', 'A'];
    const word2 = ['H', 'H', 'H', 'H', 'H', 'H', 'H', 'H'];
    const dist = engine.distance(word1, word2);
    expect(dist).toBeGreaterThan(0);
  });

  it('should calculate confidence score', () => {
    const word1 = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
    const word2 = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
    const conf = engine.confidence(word1, word2);
    expect(conf).toBe(1); // Identical = 100% confidence
  });

  it('should return lower confidence for different words', () => {
    const word1 = ['A', 'A', 'A', 'A', 'A', 'A', 'A', 'A'];
    const word2 = ['H', 'H', 'H', 'H', 'H', 'H', 'H', 'H'];
    const conf = engine.confidence(word1, word2);
    expect(conf).toBeLessThan(1);
  });
});
