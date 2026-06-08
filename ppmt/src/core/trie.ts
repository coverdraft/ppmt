/**
 * PPMT - Trie Data Structure
 *
 * Core Trie implementation for O(k) pattern search.
 * Each level of the Trie corresponds to one SAX symbol.
 * Descending the Trie = matching one more candle of the pattern.
 *
 * Key properties:
 *   - Search: O(k) where k = pattern length (independent of total patterns)
 *   - Insert: O(k)
 *   - Memory: Shared branches compress overlapping patterns
 *   - Progressive: Each new candle descends one level (O(1) amortized)
 */

import {
  SaxSymbol,
  SaxWord,
  TrieNode,
  BlockMetadata,
  Direction,
  ForwardLink,
  BackwardLink,
  WILDCARD,
} from './types';

/** Create a new empty Trie node */
function createNode(symbol: SaxSymbol, blockId: string): TrieNode {
  return {
    symbol,
    children: new Map(),
    patternCount: 0,
    blockId,
  };
}

/** Create empty BlockMetadata with defaults */
function createEmptyMetadata(totalCandles: number, triggerCandle: number): BlockMetadata {
  return {
    totalCandles,
    triggerCandle,
    remainingCandles: totalCandles - triggerCandle,
    expectedMove: { direction: Direction.NEUTRAL, magnitude: 0 },
    maxDrawdown: 0,
    maxFavorable: 0,
    stopLossDistance: 0,
    takeProfitDistance: 0,
    forwardLinks: new Map(),
    backwardLinks: new Map(),
    winRateFromHere: 0,
    avgHoldingCandles: 0,
    sampleCount: 0,
    lastUpdated: Date.now(),
  };
}

export interface TrieSearchResult {
  found: boolean;
  node: TrieNode | null;
  path: TrieNode[];
  depth: number;
  confidence: number;
}

export class PatternTrie {
  private root: TrieNode;
  private _patternCount: number;
  private _maxDepth: number;

  constructor() {
    this.root = createNode('', 'ROOT');
    this._patternCount = 0;
    this._maxDepth = 0;
  }

  /** Get total number of complete patterns stored */
  get patternCount(): number {
    return this._patternCount;
  }

  /** Get maximum depth of the Trie */
  get maxDepth(): number {
    return this._maxDepth;
  }

  /**
   * Insert a SAX word into the Trie with its outcome metadata.
   *
   * This is the core learning operation:
   * 1. Descend the Trie following the SAX symbols
   * 2. Create nodes as needed
   * 3. At the trigger candle node, initialize Block Lifecycle Metadata
   * 4. Update forward/backward links between consecutive nodes
   * 5. Update outcome statistics at each node along the path
   *
   * Complexity: O(k) where k = word length
   */
  insert(
    saxWord: SaxWord,
    triggerCandle: number,
    outcome: {
      direction: Direction;
      maxDrawdown: number;
      maxFavorable: number;
      holdingCandles: number;
    }
  ): void {
    const path: TrieNode[] = [];
    let current = this.root;

    for (let i = 0; i < saxWord.length; i++) {
      const symbol = saxWord[i];
      const blockId = `B${i}_${symbol}_${saxWord.slice(0, i + 1).join('')}`;

      if (!current.children.has(symbol)) {
        current.children.set(symbol, createNode(symbol, blockId));
      }

      current = current.children.get(symbol)!;
      current.patternCount++;
      path.push(current);

      // Initialize metadata at trigger candle and beyond
      if (i >= triggerCandle && !current.metadata) {
        current.metadata = createEmptyMetadata(saxWord.length, triggerCandle);
      }

      // Update metadata statistics
      if (current.metadata) {
        this.updateMetadata(current.metadata, i, triggerCandle, outcome, saxWord.length);
      }
    }

    // Update forward/backward links between consecutive nodes
    for (let i = triggerCandle + 1; i < path.length; i++) {
      const child = path[i];
      const parent = path[i - 1];

      if (parent.metadata && child.metadata) {
        // Forward link: parent → child
        this.updateForwardLink(parent.metadata, child.blockId, outcome);
        // Backward link: child ← parent
        this.updateBackwardLink(child.metadata, parent.blockId, outcome);
      }
    }

    this._patternCount++;
    this._maxDepth = Math.max(this._maxDepth, saxWord.length);
  }

  /**
   * Search for a SAX word in the Trie.
   *
   * Progressive search: returns the deepest match found.
   * If the full word matches, confidence = 1.0.
   * If partial match, confidence = depth / wordLength.
   *
   * Complexity: O(k) where k = word length
   *
   * Supports fuzzy matching with WILDCARD symbols.
   */
  search(saxWord: SaxWord, fuzzy: boolean = false): TrieSearchResult {
    const path: TrieNode[] = [];
    let current = this.root;
    let depth = 0;
    let confidence = 1.0;

    for (let i = 0; i < saxWord.length; i++) {
      const symbol = saxWord[i];

      if (symbol === WILDCARD && fuzzy) {
        // Wildcard: match any symbol, take the child with highest pattern count
        let bestChild: TrieNode | null = null;
        let bestCount = 0;

        for (const child of current.children.values()) {
          if (child.patternCount > bestCount) {
            bestCount = child.patternCount;
            bestChild = child;
          }
        }

        if (bestChild) {
          current = bestChild;
          path.push(current);
          depth++;
          confidence *= 0.8; // Small penalty for wildcard
        } else {
          break;
        }
      } else {
        const child = current.children.get(symbol);
        if (child) {
          current = child;
          path.push(current);
          depth++;
        } else if (fuzzy) {
          // Fuzzy: try adjacent symbols
          const adjacent = this.findAdjacentSymbol(current, symbol);
          if (adjacent) {
            current = adjacent;
            path.push(current);
            depth++;
            confidence *= 0.85; // Penalty for fuzzy match
          } else {
            break;
          }
        } else {
          break;
        }
      }
    }

    const found = depth === saxWord.length;
    const finalConfidence = found ? confidence : (depth / saxWord.length) * confidence;

    return {
      found,
      node: depth > 0 ? path[path.length - 1] : null,
      path,
      depth,
      confidence: finalConfidence,
    };
  }

  /**
   * Progressive search: given a symbol stream arriving one at a time,
   * maintain a cursor in the Trie for O(1) amortized updates.
   */
  createProgressiveCursor(): ProgressiveCursor {
    return new ProgressiveCursor(this.root);
  }

  /**
   * Check if a specific path exists in the Trie.
   * Returns true only if the complete path exists.
   */
  exists(saxWord: SaxWord): boolean {
    let current = this.root;
    for (const symbol of saxWord) {
      const child = current.children.get(symbol);
      if (!child) return false;
      current = child;
    }
    return true;
  }

  /**
   * Get Block Metadata for a specific node path.
   * Returns null if the path doesn't exist or has no metadata.
   */
  getMetadata(saxWord: SaxWord): BlockMetadata | null {
    let current = this.root;
    for (const symbol of saxWord) {
      const child = current.children.get(symbol);
      if (!child) return null;
      current = child;
    }
    return current.metadata ?? null;
  }

  /**
   * Count total nodes in the Trie (for statistics).
   */
  countNodes(): number {
    return this.countNodesRecursive(this.root);
  }

  /**
   * Estimate memory usage in bytes.
   * Rough estimate: each node ≈ 120 bytes (symbol + map + metadata pointer)
   * Metadata ≈ 200 bytes when present.
   */
  estimateMemoryBytes(): number {
    const nodeCount = this.countNodes();
    const metadataCount = this.countMetadataNodes(this.root);
    return nodeCount * 120 + metadataCount * 200;
  }

  // ─── Private Methods ───

  private updateMetadata(
    meta: BlockMetadata,
    currentIndex: number,
    triggerCandle: number,
    outcome: { direction: Direction; maxDrawdown: number; maxFavorable: number; holdingCandles: number },
    totalCandles: number
  ): void {
    const n = meta.sampleCount;
    meta.sampleCount++;
    meta.lastUpdated = Date.now();

    // Update remaining candles
    meta.remainingCandles = totalCandles - currentIndex;

    // Update expected move (exponential moving average)
    const moveMagnitude = outcome.maxFavorable > Math.abs(outcome.maxDrawdown)
      ? outcome.maxFavorable
      : outcome.maxDrawdown;
    const moveDirection = outcome.direction;

    if (n === 0) {
      meta.expectedMove = { direction: moveDirection, magnitude: moveMagnitude };
      meta.maxDrawdown = outcome.maxDrawdown;
      meta.maxFavorable = outcome.maxFavorable;
      meta.avgHoldingCandles = outcome.holdingCandles;
      meta.winRateFromHere = outcome.direction !== Direction.NEUTRAL ? 1 : 0;
    } else {
      // EMA update with alpha = 0.1
      const alpha = 0.1;
      meta.expectedMove.magnitude =
        alpha * moveMagnitude + (1 - alpha) * meta.expectedMove.magnitude;

      meta.maxDrawdown =
        alpha * outcome.maxDrawdown + (1 - alpha) * meta.maxDrawdown;

      meta.maxFavorable =
        alpha * outcome.maxFavorable + (1 - alpha) * meta.maxFavorable;

      meta.avgHoldingCandles =
        alpha * outcome.holdingCandles + (1 - alpha) * meta.avgHoldingCandles;

      const isWin = outcome.direction !== Direction.NEUTRAL ? 1 : 0;
      meta.winRateFromHere =
        alpha * isWin + (1 - alpha) * meta.winRateFromHere;
    }

    // Compute stop loss and take profit distances
    meta.stopLossDistance = Math.abs(meta.maxDrawdown) * 1.2; // 20% buffer
    meta.takeProfitDistance = meta.maxFavorable * 0.8; // 80% of max favorable
  }

  private updateForwardLink(
    parentMeta: BlockMetadata,
    childBlockId: string,
    outcome: { direction: Direction }
  ): void {
    let link = parentMeta.forwardLinks.get(childBlockId);
    const isWin = outcome.direction !== Direction.NEUTRAL ? 1 : 0;

    if (!link) {
      link = { probability: 0, winRate: 0, sampleCount: 0 };
      parentMeta.forwardLinks.set(childBlockId, link);
    }

    link.sampleCount++;
    const alpha = 1 / link.sampleCount; // Simple average for first samples
    link.probability = link.sampleCount / parentMeta.sampleCount;
    link.winRate = alpha * isWin + (1 - alpha) * link.winRate;
  }

  private updateBackwardLink(
    childMeta: BlockMetadata,
    parentBlockId: string,
    outcome: { direction: Direction }
  ): void {
    let link = childMeta.backwardLinks.get(parentBlockId);
    const isWin = outcome.direction !== Direction.NEUTRAL ? 1 : 0;

    if (!link) {
      link = { winRate: 0, sampleCount: 0 };
      childMeta.backwardLinks.set(parentBlockId, link);
    }

    link.sampleCount++;
    const alpha = 1 / link.sampleCount;
    link.winRate = alpha * isWin + (1 - alpha) * link.winRate;
  }

  private findAdjacentSymbol(node: TrieNode, target: SaxSymbol): TrieNode | null {
    // Find the child whose symbol is alphabetically closest to the target
    let closest: TrieNode | null = null;
    let minDist = Infinity;

    for (const child of node.children.values()) {
      const dist = Math.abs(child.symbol.charCodeAt(0) - target.charCodeAt(0));
      if (dist < minDist && dist <= 1) { // Only adjacent symbols
        minDist = dist;
        closest = child;
      }
    }

    return closest;
  }

  private countNodesRecursive(node: TrieNode): number {
    let count = 1;
    for (const child of node.children.values()) {
      count += this.countNodesRecursive(child);
    }
    return count;
  }

  private countMetadataNodes(node: TrieNode): number {
    let count = node.metadata ? 1 : 0;
    for (const child of node.children.values()) {
      count += this.countMetadataNodes(child);
    }
    return count;
  }
}

/**
 * Progressive Cursor for O(1) amortized search updates.
 *
 * Instead of searching from the root for each new candle,
 * the cursor maintains its position in the Trie and
 * descends one level per new symbol.
 */
export class ProgressiveCursor {
  private currentNode: TrieNode;
  private root: TrieNode;
  private depth: number;
  private path: TrieNode[];

  constructor(root: TrieNode) {
    this.root = root;
    this.currentNode = root;
    this.depth = 0;
    this.path = [];
  }

  /**
   * Advance the cursor with a new SAX symbol.
   * Returns true if the symbol was found (pattern continues).
   * Returns false if the symbol was NOT found (pattern broken → exit signal).
   *
   * This is the key method for Block Lifecycle Metadata:
   * When advance() returns false, the pattern is broken and
   * the system should exit the position.
   */
  advance(symbol: SaxSymbol): { found: boolean; metadata?: BlockMetadata; depth: number } {
    const child = this.currentNode.children.get(symbol);

    if (child) {
      this.currentNode = child;
      this.depth++;
      this.path.push(child);
      return { found: true, metadata: child.metadata, depth: this.depth };
    }

    // Pattern broken - this is the exit signal!
    return { found: false, depth: this.depth };
  }

  /** Reset cursor to root for a new search */
  reset(): void {
    this.currentNode = this.root;
    this.depth = 0;
    this.path = [];
  }

  /** Get current depth in the Trie */
  getDepth(): number {
    return this.depth;
  }

  /** Get the metadata of the current node */
  getCurrentMetadata(): BlockMetadata | undefined {
    return this.currentNode.metadata;
  }

  /** Get the path traversed so far */
  getPath(): TrieNode[] {
    return [...this.path];
  }

  /** Check if we're at a leaf node */
  isLeaf(): boolean {
    return this.currentNode.children.size === 0;
  }
}
