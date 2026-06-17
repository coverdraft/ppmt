/**
 * PPMT - Multi-Level Trie Tests
 */

import { describe, it, expect } from 'vitest';
import { MultiLevelTrie } from '../src/core/multiLevelTrie';
import { AssetClass, MarketRegime, Direction } from '../src/core/types';

describe('MultiLevelTrie', () => {
  it('should insert and search across all 4 levels', () => {
    const mlt = new MultiLevelTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    mlt.insert(word, 'BTC', AssetClass.BLUE_CHIP, MarketRegime.EXPANSION, 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    const result = mlt.search(
      word, 'BTC', AssetClass.BLUE_CHIP, MarketRegime.EXPANSION
    );

    expect(result.direction).toBe(Direction.LONG);
    expect(result.confidence).toBeGreaterThan(0);
    expect(result.levelResults).toHaveLength(4);
    expect(result.latencyUs).toBeGreaterThan(0);
  });

  it('should calculate adaptive weights based on data availability', () => {
    const mlt = new MultiLevelTrie();

    // Before any data: N3/N4 have 0 patterns → weights shift to N2
    const weightsLow = mlt.calculateAdaptiveWeights('NEWCOIN', MarketRegime.EXPANSION);
    expect(weightsLow.w2).toBeGreaterThan(weightsLow.w3); // N2 should dominate

    // Add lots of data for BTC
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
    for (let i = 0; i < 100; i++) {
      mlt.insert(word, 'BTC', AssetClass.BLUE_CHIP, MarketRegime.EXPANSION, 2, {
        direction: Direction.LONG,
        maxDrawdown: -0.02,
        maxFavorable: 0.05,
        holdingCandles: 6,
      });
    }

    // After data: weights should be more balanced
    const weightsHigh = mlt.calculateAdaptiveWeights('BTC', MarketRegime.EXPANSION);
    expect(weightsHigh.w3).toBeGreaterThan(weightsLow.w3);
  });

  it('should achieve consensus when multiple levels agree', () => {
    const mlt = new MultiLevelTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    // Insert the same pattern many times → all levels agree
    for (let i = 0; i < 50; i++) {
      mlt.insert(word, 'ETH', AssetClass.BLUE_CHIP, MarketRegime.TRENDING_UP, 2, {
        direction: Direction.LONG,
        maxDrawdown: -0.02,
        maxFavorable: 0.05,
        holdingCandles: 6,
      });
    }

    const result = mlt.search(
      word, 'ETH', AssetClass.BLUE_CHIP, MarketRegime.TRENDING_UP
    );

    expect(result.consensusReached).toBe(true);
    expect(result.direction).toBe(Direction.LONG);
  });

  it('should track statistics across all levels', () => {
    const mlt = new MultiLevelTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    mlt.insert(word, 'PEPE', AssetClass.MEME, MarketRegime.EXPANSION, 2, {
      direction: Direction.SHORT,
      maxDrawdown: -0.05,
      maxFavorable: 0.02,
      holdingCandles: 4,
    });

    const stats = mlt.getStats();
    expect(stats.level1).toBe(1);
    expect(stats.level2.meme).toBe(1);
    expect(stats.level3.PEPE).toBe(1);
    expect(stats.totalNodes).toBeGreaterThan(0);
    expect(stats.estimatedMemoryMB).toBeGreaterThan(0);
  });

  it('should handle meme coins with class-level data', () => {
    const mlt = new MultiLevelTrie();

    // Add patterns from other meme coins
    const memeCoins = ['PEPE', 'WIF', 'BONK', 'FLOKI'];
    for (const coin of memeCoins) {
      for (let i = 0; i < 20; i++) {
        const word = ['C', 'C', 'C', 'B', 'A', 'Z', 'Z', 'Z']; // Rug Pull pattern
        mlt.insert(word, coin, AssetClass.MEME, MarketRegime.COMPRESSION, 2, {
          direction: Direction.SHORT,
          maxDrawdown: -0.08,
          maxFavorable: 0.01,
          holdingCandles: 3,
        });
      }
    }

    // Now search for a NEW meme coin - it should get results from class level
    const result = mlt.search(
      ['C', 'C', 'C', 'B', 'A', 'Z', 'Z', 'Z'],
      'NEWMEME',
      AssetClass.MEME,
      MarketRegime.COMPRESSION
    );

    // Level 2 (class) should have matches even though NEWMEME has no data
    expect(result.levelResults[1].matched).toBe(true); // Level 2 = class level
    expect(result.direction).toBe(Direction.SHORT);
  });
});
