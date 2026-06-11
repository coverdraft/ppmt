/**
 * PPMT - SAX (Symbolic Aggregate approXimation) Engine
 *
 * Transforms continuous price series into discrete symbol sequences.
 * Two-step process:
 *   1. Z-score normalization (removes trend & absolute volatility)
 *   2. Breakpoint-based discretization (assigns symbols A-Z)
 *
 * Key property: SAX distance is a lower bound of Euclidean distance
 * between the original series → if SAX words are similar, the originals are similar.
 */

import { SaxSymbol, SaxWord, SaxAlphabetSize, Candle } from './types';

/** Statistical breakpoints for Gaussian distribution N(0,1) */
const BREAKPOINTS: Record<SaxAlphabetSize, number[]> = {
  4:  [-0.674, 0, 0.674],
  6:  [-0.968, -0.430, 0, 0.430, 0.968],
  8:  [-1.150, -0.674, -0.318, 0, 0.318, 0.674, 1.150],
  10: [-1.281, -0.841, -0.524, -0.253, 0, 0.253, 0.524, 0.841, 1.281],
  12: [-1.382, -0.994, -0.674, -0.430, -0.210, 0, 0.210, 0.430, 0.674, 0.994, 1.382],
  16: [-1.534, -1.214, -0.968, -0.764, -0.588, -0.430, -0.282, -0.137, 0, 0.137, 0.282, 0.430, 0.588, 0.764, 0.968],
  26: [-1.751, -1.534, -1.382, -1.281, -1.214, -1.150, -0.968, -0.841, -0.764, -0.674,
        -0.588, -0.524, -0.430, -0.358, -0.282, -0.210, -0.137, 0,
        0.137, 0.210, 0.282, 0.358, 0.430, 0.524, 0.588],
};

/** Symbol alphabet: A = lowest, Z = highest */
const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

export interface SaxConfig {
  /** Number of SAX segments (pattern length in symbols) */
  wordLength: number;
  /** Number of candles per segment (window size) */
  segmentSize: number;
  /** Alphabet size (4-26 symbols) */
  alphabetSize: SaxAlphabetSize;
}

export class SAXEngine {
  private config: SaxConfig;
  private breakpoints: number[];
  private symbols: string[];

  constructor(config: SaxConfig) {
    this.config = config;
    this.breakpoints = BREAKPOINTS[config.alphabetSize];
    this.symbols = ALPHABET.slice(0, config.alphabetSize).split('');
  }

  /**
   * Transform a series of candles into a SAX word.
   *
   * Step 1: Extract close prices
   * Step 2: Piecewise Aggregate Approximation (PAA) - average per segment
   * Step 3: Z-score normalize the PAA
   * Step 4: Discretize using breakpoints → symbols
   */
  transform(candles: Candle[]): SaxWord {
    const closes = candles.map(c => c.close);
    const totalWindowSize = this.config.wordLength * this.config.segmentSize;

    if (closes.length < totalWindowSize) {
      throw new Error(
        `Need at least ${totalWindowSize} candles, got ${closes.length}`
      );
    }

    // Use the last totalWindowSize candles
    const window = closes.slice(-totalWindowSize);

    // Step 2: PAA - reduce to wordLength segments
    const paa = this.piecewiseAggregate(window);

    // Step 3: Z-score normalize
    const normalized = this.zScoreNormalize(paa);

    // Step 4: Discretize
    return normalized.map(value => this.discretize(value));
  }

  /**
   * Incremental SAX update: update the SAX word with a new candle
   * without recomputing the entire transformation.
   * Returns the new SAX word or null if not enough data yet.
   */
  incrementalTransform(
    previousWord: SaxWord | null,
    buffer: Candle[]
  ): SaxWord | null {
    const totalWindowSize = this.config.wordLength * this.config.segmentSize;

    if (buffer.length < totalWindowSize) {
      return null;
    }

    // For now, recompute (optimization: incremental PAA update)
    return this.transform(buffer);
  }

  /**
   * Piecewise Aggregate Approximation (PAA)
   * Averages the time series into n equal-sized segments.
   */
  private piecewiseAggregate(series: number[]): number[] {
    const n = this.config.wordLength;
    const len = series.length;
    const segmentSize = len / n;
    const paa: number[] = [];

    for (let i = 0; i < n; i++) {
      const start = Math.floor(i * segmentSize);
      const end = Math.floor((i + 1) * segmentSize);
      let sum = 0;
      for (let j = start; j < end; j++) {
        sum += series[j];
      }
      paa.push(sum / (end - start));
    }

    return paa;
  }

  /**
   * Z-score normalization: (x - mean) / std
   * Removes absolute price level and volatility, leaving only shape.
   */
  private zScoreNormalize(values: number[]): number[] {
    const n = values.length;
    const mean = values.reduce((a, b) => a + b, 0) / n;
    const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / n;
    const std = Math.sqrt(variance);

    if (std < 1e-10) {
      // Flat line - all symbols get middle value
      return values.map(() => 0);
    }

    return values.map(v => (v - mean) / std);
  }

  /**
   * Discretize a normalized value into a SAX symbol.
   * Uses breakpoints from the Gaussian distribution.
   */
  private discretize(value: number): SaxSymbol {
    for (let i = 0; i < this.breakpoints.length; i++) {
      if (value < this.breakpoints[i]) {
        return this.symbols[i];
      }
    }
    return this.symbols[this.symbols.length - 1];
  }

  /**
   * Compute SAX distance between two SAX words.
   * This is a lower bound of the true Euclidean distance.
   * Returns 0 for identical words.
   */
  distance(word1: SaxWord, word2: SaxWord): number {
    if (word1.length !== word2.length) {
      throw new Error('SAX words must have equal length');
    }

    let dist = 0;
    for (let i = 0; i < word1.length; i++) {
      dist += this.symbolDistance(word1[i], word2[i]) ** 2;
    }
    return Math.sqrt(dist);
  }

  /**
   * Distance between two individual symbols based on breakpoint intervals.
   */
  private symbolDistance(a: SaxSymbol, b: SaxSymbol): number {
    if (a === b) return 0;

    const idxA = this.symbols.indexOf(a);
    const idxB = this.symbols.indexOf(b);

    if (idxA === -1 || idxB === -1) return 0;

    const lo = Math.min(idxA, idxB);
    const hi = Math.max(idxA, idxB);

    // The distance is the breakpoint between the two symbol ranges
    if (hi === 0) return Math.abs(this.breakpoints[0]);
    if (lo === this.symbols.length - 1) return 0;

    return Math.abs(this.breakpoints[hi - 1]);
  }

  /**
   * Calculate confidence score between two SAX words (0-1).
   * 1.0 = identical, 0.0 = completely different.
   */
  confidence(word1: SaxWord, word2: SaxWord): number {
    const maxDist = Math.sqrt(word1.length) * 4; // Approximate max distance
    const dist = this.distance(word1, word2);
    return Math.max(0, 1 - dist / maxDist);
  }

  /** Get the current configuration */
  getConfig(): Readonly<SaxConfig> {
    return { ...this.config };
  }

  /** Get alphabet symbols for current config */
  getAlphabet(): string[] {
    return [...this.symbols];
  }
}
