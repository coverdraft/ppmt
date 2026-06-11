/**
 * PPMT - Progressive Pattern Matching Trie
 * Core Types & Interfaces
 *
 * This module defines all the types used across the PPMT system.
 * The design follows the V3 specification with Block Lifecycle Metadata.
 */

// ─── SAX & Symbolization ───

/** SAX alphabet size (number of distinct symbols) */
export type SaxAlphabetSize = 4 | 6 | 8 | 10 | 12 | 16 | 26;

/** A single SAX symbol (letter A-Z) */
export type SaxSymbol = string;

/** A sequence of SAX symbols representing a pattern */
export type SaxWord = SaxSymbol[];

/** Wildcard symbol for fuzzy matching */
export const WILDCARD = '?';

// ─── Trie Structure ───

/** Direction of expected movement from a block */
export enum Direction {
  LONG = 'LONG',
  SHORT = 'SHORT',
  NEUTRAL = 'NEUTRAL',
}

/** Block Lifecycle Metadata stored at each Trie node */
export interface BlockMetadata {
  /** Total candles in the full pattern */
  totalCandles: number;
  /** Candle index where pattern was detected (trigger point) */
  triggerCandle: number;
  /** Remaining candles of prediction from this node */
  remainingCandles: number;
  /** Expected directional move */
  expectedMove: {
    direction: Direction;
    magnitude: number; // percentage, e.g. 5.2 means +5.2%
  };
  /** Maximum adverse excursion from trigger (negative %) */
  maxDrawdown: number;
  /** Maximum favorable excursion from trigger (positive %) */
  maxFavorable: number;
  /** Natural stop loss distance from entry (positive %) */
  stopLossDistance: number;
  /** Natural take profit distance from entry (positive %) */
  takeProfitDistance: number;
  /** Forward links: child block IDs → probability + result */
  forwardLinks: Map<string, ForwardLink>;
  /** Backward links: parent block IDs → result */
  backwardLinks: Map<string, BackwardLink>;
  /** Win rate from this point onward */
  winRateFromHere: number;
  /** Average holding candles before exit/SL */
  avgHoldingCandles: number;
  /** Number of times this node has been traversed (sample count) */
  sampleCount: number;
  /** Last time this node was updated */
  lastUpdated: number;
}

/** Forward link: probability of reaching a child node and its result */
export interface ForwardLink {
  /** Probability of reaching this child (0-1) */
  probability: number;
  /** Win rate when this path is taken */
  winRate: number;
  /** Number of samples for this link */
  sampleCount: number;
}

/** Backward link: result when arriving from a parent node */
export interface BackwardLink {
  /** Win rate when arriving from this parent */
  winRate: number;
  /** Number of samples for this path */
  sampleCount: number;
}

/** A node in the PPMT Trie */
export interface TrieNode {
  /** The SAX symbol at this node */
  symbol: SaxSymbol;
  /** Children indexed by symbol */
  children: Map<SaxSymbol, TrieNode>;
  /** Block Lifecycle Metadata (only on nodes that are trigger points or beyond) */
  metadata?: BlockMetadata;
  /** Number of complete patterns that pass through this node */
  patternCount: number;
  /** Unique block ID for this node (computed from path) */
  blockId: string;
}

// ─── Multi-Level Architecture ───

/** Asset class for Level 2 grouping */
export enum AssetClass {
  BLUE_CHIP = 'blue_chip',
  LARGE_CAP = 'large_cap',
  MID_CAP = 'mid_cap',
  DEFI = 'defi',
  MEME = 'meme',
  NEW_LAUNCH = 'new_launch',
}

/** Market regime for Level 4 */
export enum MarketRegime {
  EXPANSION = 'expansion',
  COMPRESSION = 'compression',
  TRENDING_UP = 'trending_up',
  TRENDING_DOWN = 'trending_down',
  LATERAL = 'lateral',
  TRANSITION = 'transition',
}

/** Timeframe for pattern resolution */
export enum Timeframe {
  M1 = '1m',
  M5 = '5m',
  M15 = '15m',
  H1 = '1h',
  H4 = '4h',
  D1 = '1d',
}

/** Adaptive weights for the 4-level architecture */
export interface AdaptiveWeights {
  w1: number; // Universal Trie weight
  w2: number; // Asset Class Trie weight
  w3: number; // Per-Asset Trie weight
  w4: number; // Per-Asset+Regime Trie weight
}

/** Configuration for the multi-level Trie */
export interface MultiLevelConfig {
  /** Minimum patterns for N4 to have full weight */
  n4MinPatterns: number;
  /** Minimum patterns for N3 to have full weight */
  n3MinPatterns: number;
  /** Default weights when all levels have sufficient data */
  defaultWeights: AdaptiveWeights;
}

// ─── Search & Matching ───

/** Result from a single Trie level search */
export interface LevelSearchResult {
  level: number;
  levelName: string;
  matched: boolean;
  confidence: number;
  direction: Direction;
  metadata?: BlockMetadata;
  matchCount: number;
}

/** Combined result from all 4 levels */
export interface PPMTResult {
  /** Combined direction after weighted merge */
  direction: Direction;
  /** Combined confidence (0-1) */
  confidence: number;
  /** Results from each individual level */
  levelResults: LevelSearchResult[];
  /** Applied weights */
  weights: AdaptiveWeights;
  /** Block metadata from best match */
  metadata?: BlockMetadata;
  /** Whether at least 2 levels agree on direction */
  consensusReached: boolean;
  /** Latency in microseconds */
  latencyUs: number;
}

/** Search options */
export interface SearchOptions {
  /** Minimum confidence threshold (0-1) */
  minConfidence?: number;
  /** Use fuzzy matching with wildcards */
  fuzzy?: boolean;
  /** Number of wildcard positions allowed */
  fuzzyPositions?: number;
  /** Specific levels to search (default: all 4) */
  levels?: number[];
}

// ─── Data Pipeline ───

/** A single OHLCV candle */
export interface Candle {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** Asset identifier */
export interface Asset {
  symbol: string;
  exchange: string;
  assetClass: AssetClass;
  isActive: boolean;
  firstSeen: number;
}

/** Pattern record for insertion */
export interface PatternRecord {
  saxWord: SaxWord;
  asset: string;
  assetClass: AssetClass;
  timeframe: Timeframe;
  regime: MarketRegime;
  entryCandle: number;
  totalCandles: number;
  outcome: {
    direction: Direction;
    maxDrawdown: number;
    maxFavorable: number;
    holdingCandles: number;
  };
  timestamp: number;
}

// ─── Risk Manager ───

/** Position sizing from risk manager */
export interface PositionSizing {
  size: number;       // Number of units
  riskPercent: number; // Risk as % of capital
  stopLoss: number;    // Stop loss price
  takeProfit: number;  // Take profit price
  entryPrice: number;  // Entry price
}

/** Risk manager config */
export interface RiskConfig {
  maxRiskPerTrade: number;  // e.g. 0.02 = 2%
  maxDailyDrawdown: number; // e.g. 0.05 = 5%
  maxCorrelatedPositions: number;
  capital: number;
}
