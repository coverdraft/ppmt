/**
 * PPMT - Main Engine
 *
 * Orchestrates the complete PPMT V3 system:
 *   - SAX symbolization
 *   - Multi-level Trie search
 *   - Block Lifecycle Metadata
 *   - Regime detection
 *   - Asset classification
 *   - Risk management
 *
 * Usage:
 *   const engine = new PPMTEngine();
 *   engine.ingest(candles, 'BTC', AssetClass.BLUE_CHIP);
 *   const result = engine.analyze(candles, 'BTC', AssetClass.BLUE_CHIP);
 */

import { SAXEngine, SaxConfig } from './sax';
import { MultiLevelTrie } from './multiLevelTrie';
import { RegimeDetector, RegimeConfig } from './regime';
import { AssetClassifier, AssetInfo } from './assetClassifier';
import { RiskManager, RiskConfig } from './riskManager';

import {
  Candle,
  AssetClass,
  MarketRegime,
  Timeframe,
  PPMTResult,
  SearchOptions,
  Direction,
  PositionSizing,
  BlockMetadata,
  Asset,
} from './types';

export interface PPMTConfig {
  sax: SaxConfig;
  regime: RegimeConfig;
  risk: RiskConfig;
  /** Minimum confidence to generate a trading signal */
  minSignalConfidence: number;
  /** Minimum levels agreeing for consensus */
  minConsensusLevels: number;
}

const DEFAULT_PPMT_CONFIG: PPMTConfig = {
  sax: {
    wordLength: 8,     // 8 SAX symbols per pattern
    segmentSize: 6,    // 6 candles per segment → 48 candles per pattern
    alphabetSize: 8,   // A-H symbols
  },
  regime: {
    atrPeriod: 14,
    adxPeriod: 14,
    volumePeriod: 20,
    expansionThreshold: 0.7,
    trendThreshold: 25,
  },
  risk: {
    maxRiskPerTrade: 0.02,
    maxDailyDrawdown: 0.05,
    maxCorrelatedPositions: 3,
    capital: 10000,
  },
  minSignalConfidence: 0.60,
  minConsensusLevels: 2,
};

export class PPMTEngine {
  private sax: SAXEngine;
  private trie: MultiLevelTrie;
  private regime: RegimeDetector;
  private classifier: AssetClassifier;
  private risk: RiskManager;
  private config: PPMTConfig;

  /** Candle buffers per asset for SAX transformation */
  private candleBuffers: Map<string, Candle[]>;

  constructor(config?: Partial<PPMTConfig>) {
    this.config = { ...DEFAULT_PPMT_CONFIG, ...config };
    this.sax = new SAXEngine(this.config.sax);
    this.trie = new MultiLevelTrie();
    this.regime = new RegimeDetector(this.config.regime);
    this.classifier = new AssetClassifier();
    this.risk = new RiskManager(this.config.risk);
    this.candleBuffers = new Map();
  }

  /**
   * Ingest a completed pattern into the Trie.
   *
   * This is the LEARNING phase: the engine takes a set of candles,
   * transforms them into a SAX word, determines the outcome (direction,
   * max drawdown, max favorable), and inserts into all 4 Trie levels.
   *
   * Should be called with historical data to build the pattern database.
   */
  ingest(
    candles: Candle[],
    asset: string,
    assetClass: AssetClass,
    regime?: MarketRegime
  ): void {
    if (candles.length < this.config.sax.wordLength * this.config.sax.segmentSize) {
      return; // Not enough data for a pattern
    }

    // Detect regime if not provided
    const detectedRegime = regime ?? this.regime.detect(candles);

    // Transform candles to SAX word
    const saxWord = this.sax.transform(candles);

    // Determine the trigger candle (where the pattern becomes actionable)
    // For now: trigger at 20% of pattern length
    const triggerCandle = Math.floor(saxWord.length * 0.2);

    // Calculate outcome from the candles after trigger
    const triggerIndex = Math.floor(candles.length * 0.2);
    const entryPrice = candles[triggerIndex].close;
    let maxDrawdown = 0;
    let maxFavorable = 0;

    for (let i = triggerIndex; i < candles.length; i++) {
      const change = (candles[i].close - entryPrice) / entryPrice;
      if (change < maxDrawdown) maxDrawdown = change;
      if (change > maxFavorable) maxFavorable = change;
    }

    const direction: Direction =
      maxFavorable > Math.abs(maxDrawdown) ? Direction.LONG
      : Math.abs(maxDrawdown) > maxFavorable ? Direction.SHORT
      : Direction.NEUTRAL;

    const holdingCandles = candles.length - triggerIndex;

    // Insert into multi-level Trie
    this.trie.insert(
      saxWord,
      asset,
      assetClass,
      detectedRegime,
      triggerCandle,
      { direction, maxDrawdown, maxFavorable, holdingCandles }
    );
  }

  /**
   * Analyze current market conditions and generate a trading signal.
   *
   * This is the TRADING phase: the engine takes recent candles,
   * transforms them into a SAX word, searches all 4 Trie levels,
   * and returns a combined result with Block Lifecycle Metadata.
   */
  analyze(
    candles: Candle[],
    asset: string,
    assetClass: AssetClass,
    options?: SearchOptions
  ): PPMTResult | null {
    if (candles.length < this.config.sax.wordLength * this.config.sax.segmentSize) {
      return null; // Not enough data
    }

    // Detect regime
    const regime = this.regime.detect(candles);

    // Transform to SAX
    const saxWord = this.sax.transform(candles);

    // Search all 4 levels
    const result = this.trie.search(saxWord, asset, assetClass, regime, options);

    // Filter by minimum confidence
    if (result.confidence < (options?.minConfidence ?? this.config.minSignalConfidence)) {
      return null;
    }

    // Filter by consensus
    if (!result.consensusReached && this.config.minConsensusLevels > 1) {
      return null;
    }

    return result;
  }

  /**
   * Process a new incoming candle for an asset.
   *
   * Maintains a rolling buffer and triggers analysis when
   * enough candles have accumulated.
   */
  onCandle(
    candle: Candle,
    asset: string,
    assetClass: AssetClass
  ): PPMTResult | null {
    // Add to buffer
    if (!this.candleBuffers.has(asset)) {
      this.candleBuffers.set(asset, []);
    }
    const buffer = this.candleBuffers.get(asset)!;
    buffer.push(candle);

    // Keep buffer at a reasonable size
    const maxSize = this.config.sax.wordLength * this.config.sax.segmentSize * 2;
    if (buffer.length > maxSize) {
      buffer.splice(0, buffer.length - maxSize);
    }

    // Try to analyze
    return this.analyze(buffer, asset, assetClass);
  }

  /**
   * Evaluate a trading signal and calculate position size.
   * Returns null if the trade is rejected by risk management.
   */
  evaluateTrade(
    asset: string,
    direction: Direction,
    currentPrice: number,
    metadata: BlockMetadata | undefined,
    assetClass: string
  ): PositionSizing | null {
    return this.risk.evaluate(asset, direction, currentPrice, metadata, assetClass);
  }

  /**
   * Get engine statistics.
   */
  getStats() {
    return {
      trie: this.trie.getStats(),
      risk: {
        dailyPnl: this.risk.getDailyPnl(),
        openPositions: this.risk.getOpenPositionCount(),
        dailyLimitReached: this.risk.isDailyLimitReached(),
      },
      buffers: Object.fromEntries(
        [...this.candleBuffers.entries()].map(([k, v]) => [k, v.length])
      ),
    };
  }

  /**
   * Get the SAX engine for direct access.
   */
  getSAXEngine(): SAXEngine {
    return this.sax;
  }

  /**
   * Get the multi-level Trie for direct access.
   */
  getMultiLevelTrie(): MultiLevelTrie {
    return this.trie;
  }

  /**
   * Get the risk manager for direct access.
   */
  getRiskManager(): RiskManager {
    return this.risk;
  }

  /**
   * Get the regime detector for direct access.
   */
  getRegimeDetector(): RegimeDetector {
    return this.regime;
  }

  /**
   * Get the asset classifier for direct access.
   */
  getAssetClassifier(): AssetClassifier {
    return this.classifier;
  }
}
