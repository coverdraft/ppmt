/**
 * PPMT - Asset Classifier
 *
 * Classifies assets into their appropriate class for Level 2 Trie grouping.
 * Uses market cap, trading volume, age, and category tags.
 */

import { AssetClass, Asset } from './types';

export interface AssetInfo {
  symbol: string;
  exchange: string;
  marketCapUsd?: number;
  dailyVolumeUsd?: number;
  ageDays?: number;
  tags?: string[];
}

/** Market cap thresholds in USD */
const MARKET_CAP_THRESHOLDS = {
  blueChip: 50_000_000_000,    // $50B+ (BTC, ETH)
  largeCap: 10_000_000_000,    // $10B+ (SOL, BNB)
  midCap: 1_000_000_000,       // $1B+ (LINK, AVAX)
  smallCap: 100_000_000,       // $100M+ (UNI, AAVE)
};

/** Known meme coins (hardcoded for reliability) */
const KNOWN_MEMES = new Set([
  'PEPE', 'WIF', 'BONK', 'FLOKI', 'DOGE', 'SHIB', 'BOME',
  'MEME', 'PEOPLE', 'FIGHT', 'TRUMP', 'MELANIA', 'LIBRA',
  'TURBO', 'WOJAK', 'BRETT', 'MOG', 'SPX', 'POPCAT',
]);

export class AssetClassifier {
  private cache: Map<string, AssetClass> = new Map();

  /**
   * Classify an asset into its appropriate class.
   *
   * Priority:
   *   1. Age < 7 days → NEW_LAUNCH
   *   2. Known meme tags → MEME
   *   3. Market cap based classification
   *   4. Default: MID_CAP
   */
  classify(info: AssetInfo): AssetClass {
    // Check cache
    const cacheKey = `${info.exchange}:${info.symbol}`;
    if (this.cache.has(cacheKey)) {
      return this.cache.get(cacheKey)!;
    }

    let assetClass: AssetClass;

    // Rule 1: New launches
    if (info.ageDays !== undefined && info.ageDays < 7) {
      assetClass = AssetClass.NEW_LAUNCH;
    }
    // Rule 2: Known memes
    else if (this.isMeme(info)) {
      assetClass = AssetClass.MEME;
    }
    // Rule 3: Market cap based
    else if (info.marketCapUsd !== undefined) {
      assetClass = this.classifyByMarketCap(info.marketCapUsd);
    }
    // Rule 4: Default
    else {
      assetClass = AssetClass.MID_CAP;
    }

    this.cache.set(cacheKey, assetClass);
    return assetClass;
  }

  /**
   * Reclassify an asset (e.g., a meme that grew to mid-cap).
   * New launches become memes after 7 days.
   */
  reclassify(info: AssetInfo): AssetClass {
    const cacheKey = `${info.exchange}:${info.symbol}`;
    this.cache.delete(cacheKey);
    return this.classify(info);
  }

  /**
   * Create an Asset record from info.
   */
  createAsset(info: AssetInfo): Asset {
    return {
      symbol: info.symbol,
      exchange: info.exchange,
      assetClass: this.classify(info),
      isActive: true,
      firstSeen: Date.now() - (info.ageDays ?? 30) * 86400000,
    };
  }

  private isMeme(info: AssetInfo): boolean {
    // Check known memes
    if (KNOWN_MEMES.has(info.symbol.toUpperCase())) return true;

    // Check tags
    const memeTags = ['meme', 'memecoin', 'dog', 'pepe', 'wojak', 'cat', 'frog'];
    if (info.tags?.some(t => memeTags.includes(t.toLowerCase()))) return true;

    return false;
  }

  private classifyByMarketCap(marketCap: number): AssetClass {
    if (marketCap >= MARKET_CAP_THRESHOLDS.blueChip) return AssetClass.BLUE_CHIP;
    if (marketCap >= MARKET_CAP_THRESHOLDS.largeCap) return AssetClass.LARGE_CAP;
    if (marketCap >= MARKET_CAP_THRESHOLDS.midCap) return AssetClass.MID_CAP;
    if (marketCap >= MARKET_CAP_THRESHOLDS.smallCap) return AssetClass.DEFI;
    return AssetClass.MEME; // Very small cap = likely meme
  }
}
