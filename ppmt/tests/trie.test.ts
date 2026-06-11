/**
 * PPMT - Trie Tests
 */

import { describe, it, expect } from 'vitest';
import { PatternTrie } from '../src/core/trie';
import { Direction } from '../src/core/types';

describe('PatternTrie', () => {
  it('should insert and search for a pattern', () => {
    const trie = new PatternTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    trie.insert(word, 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    expect(trie.patternCount).toBe(1);

    const result = trie.search(word);
    expect(result.found).toBe(true);
    expect(result.depth).toBe(8);
    expect(result.confidence).toBe(1);
  });

  it('should return partial match for prefix', () => {
    const trie = new PatternTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    trie.insert(word, 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    const result = trie.search(['A', 'B', 'C', 'D']);
    // The 4-symbol prefix exists in the Trie, so found=true for that prefix
    expect(result.found).toBe(true);
    expect(result.depth).toBe(4);
    // Partial match means we only matched 4/8 of the full pattern
    // The node won't have complete metadata for the full pattern
  });

  it('should return not found for completely different pattern', () => {
    const trie = new PatternTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    trie.insert(word, 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    const result = trie.search(['H', 'G', 'F', 'E', 'D', 'C', 'B', 'A']);
    expect(result.found).toBe(false);
    expect(result.depth).toBe(0);
  });

  it('should handle multiple patterns sharing branches', () => {
    const trie = new PatternTrie();

    trie.insert(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    trie.insert(['A', 'B', 'C', 'D', 'X', 'Y', 'Z', 'Z'], 2, {
      direction: Direction.SHORT,
      maxDrawdown: -0.04,
      maxFavorable: 0.02,
      holdingCandles: 4,
    });

    expect(trie.patternCount).toBe(2);

    // Both should be found
    const r1 = trie.search(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']);
    const r2 = trie.search(['A', 'B', 'C', 'D', 'X', 'Y', 'Z', 'Z']);

    expect(r1.found).toBe(true);
    expect(r2.found).toBe(true);
    expect(r1.node?.metadata?.expectedMove.direction).toBe(Direction.LONG);
    expect(r2.node?.metadata?.expectedMove.direction).toBe(Direction.SHORT);
  });

  it('should create Block Lifecycle Metadata at trigger candle', () => {
    const trie = new PatternTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    trie.insert(word, 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    const result = trie.search(word);
    expect(result.node?.metadata).toBeDefined();
    expect(result.node?.metadata?.totalCandles).toBe(8);
    expect(result.node?.metadata?.triggerCandle).toBe(2);
    expect(result.node?.metadata?.expectedMove.direction).toBe(Direction.LONG);
  });

  it('should update forward and backward links', () => {
    const trie = new PatternTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    trie.insert(word, 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    // Search for prefix to get a node with forward links
    const result = trie.search(['A', 'B', 'C', 'D']);
    // Node at depth 4 (after trigger) should have forward links
    if (result.node?.metadata) {
      expect(result.node.metadata.forwardLinks.size).toBeGreaterThan(0);
    }
  });

  it('should count nodes and estimate memory', () => {
    const trie = new PatternTrie();

    for (let i = 0; i < 10; i++) {
      const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
      // Vary the last symbol
      word[7] = String.fromCharCode(65 + i % 8);
      trie.insert(word, 2, {
        direction: Direction.LONG,
        maxDrawdown: -0.02,
        maxFavorable: 0.05,
        holdingCandles: 6,
      });
    }

    expect(trie.countNodes()).toBeGreaterThan(0);
    expect(trie.estimateMemoryBytes()).toBeGreaterThan(0);
  });
});

describe('ProgressiveCursor', () => {
  it('should progressively descend the Trie', () => {
    const trie = new PatternTrie();
    const word = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    trie.insert(word, 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    const cursor = trie.createProgressiveCursor();

    for (let i = 0; i < word.length; i++) {
      const result = cursor.advance(word[i]);
      expect(result.found).toBe(true);
      expect(result.depth).toBe(i + 1);
    }
  });

  it('should detect pattern break (exit signal)', () => {
    const trie = new PatternTrie();
    trie.insert(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], 2, {
      direction: Direction.LONG,
      maxDrawdown: -0.02,
      maxFavorable: 0.05,
      holdingCandles: 6,
    });

    const cursor = trie.createProgressiveCursor();

    // Advance through known symbols
    expect(cursor.advance('A').found).toBe(true);
    expect(cursor.advance('B').found).toBe(true);
    expect(cursor.advance('C').found).toBe(true);

    // Now try a symbol that doesn't exist → EXIT SIGNAL
    const result = cursor.advance('Z');
    expect(result.found).toBe(false); // Pattern broken!
  });
});
