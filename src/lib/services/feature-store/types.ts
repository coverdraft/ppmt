/**
 * Feature Store Types - CryptoQuant Terminal
 *
 * Type definitions for the Feature Store service that sits between
 * raw data ingestion (OHLCVPipeline) and the Brain analysis pipeline.
 *
 * Features are computed once, cached with TTL, and served to all
 * consumers (12 Brain sub-engines) consistently.
 */

// ============================================================
// CORE FEATURE TYPES
// ============================================================

/** A single computed feature with metadata */
export interface FeatureValue {
  /** The computed value of the feature */
  value: number;
  /** Unix timestamp (ms) when this feature was computed */
  timestamp: number;
  /** Data quality score 0-1 (1 = perfect, 0 = unusable) */
  quality: number;
  /** Source data identifier (e.g. 'ohlcv:1h:binance', 'on-chain:sqd') */
  source: string;
}

/** Category of feature — determines TTL and refresh strategy */
export type FeatureCategory =
  | 'technical'
  | 'volatility'
  | 'volume'
  | 'on-chain'
  | 'liquidity'
  | 'sentiment';

/** All feature names, organized by category */
export type TechnicalFeatureName =
  | 'rsi_14'
  | 'ma_7'
  | 'ma_25'
  | 'ma_50'
  | 'ma_200'
  | 'ema_12'
  | 'ema_26'
  | 'bollinger_upper'
  | 'bollinger_middle'
  | 'bollinger_lower'
  | 'bollinger_bandwidth'
  | 'bollinger_percent_b'
  | 'atr_14'
  | 'macd_line'
  | 'macd_signal'
  | 'macd_histogram'
  | 'stochastic_k'
  | 'stochastic_d'
  | 'adx'
  | 'cci'
  | 'obv'
  | 'vwap';

export type VolatilityFeatureName =
  | 'realized_vol_1h'
  | 'realized_vol_4h'
  | 'realized_vol_24h'
  | 'garman_klass_vol'
  | 'parkinson_vol';

export type VolumeFeatureName =
  | 'volume_ma_ratio'
  | 'volume_trend_1h'
  | 'volume_trend_4h'
  | 'relative_volume';

export type OnChainFeatureName =
  | 'whale_flow_1h'
  | 'whale_flow_4h'
  | 'whale_flow_24h'
  | 'smart_money_net_flow'
  | 'bot_activity_ratio'
  | 'holder_change_24h';

export type LiquidityFeatureName =
  | 'spread_pct'
  | 'depth_ratio'
  | 'slippage_estimate';

export type SentimentFeatureName =
  | 'buy_sell_pressure'
  | 'funding_rate_deviation'
  | 'open_interest_change';

/** Union of all feature names */
export type FeatureName =
  | TechnicalFeatureName
  | VolatilityFeatureName
  | VolumeFeatureName
  | OnChainFeatureName
  | LiquidityFeatureName
  | SentimentFeatureName;

/** Mapping from category to its feature names */
export type CategoryFeatureNames = {
  technical: TechnicalFeatureName;
  volatility: VolatilityFeatureName;
  volume: VolumeFeatureName;
  'on-chain': OnChainFeatureName;
  liquidity: LiquidityFeatureName;
  sentiment: SentimentFeatureName;
};

// ============================================================
// FEATURE SET — All features for a single token
// ============================================================

/** A complete set of features for a token at a point in time */
export interface FeatureSet {
  /** Token address */
  tokenAddress: string;
  /** Blockchain */
  chain: string;
  /** Computation timestamp */
  computedAt: number;
  /** Version for backtest reproducibility */
  version: string;
  /** Features keyed by name */
  features: Record<FeatureName, FeatureValue>;
  /** Number of features computed */
  featureCount: number;
  /** Overall data quality (average across features) */
  overallQuality: number;
}

// ============================================================
// FEATURE VECTOR — ML-ready representation
// ============================================================

/** Standardized ML feature vector with metadata */
export interface FeatureVector {
  /** The token these features belong to */
  tokenAddress: string;
  /** The chain */
  chain: string;
  /** When the vector was computed */
  timestamp: number;
  /** Ordered feature names corresponding to the vector positions */
  featureNames: FeatureName[];
  /** Float64Array for efficient ML consumption */
  values: Float64Array;
  /** Quality scores per position */
  qualityScores: Float64Array;
  /** Total number of features */
  length: number;
}

// ============================================================
// CACHE TYPES
// ============================================================

/** Cache entry with TTL and version tracking */
export interface FeatureCacheEntry {
  /** The feature set */
  featureSet: FeatureSet;
  /** When this entry was cached */
  cachedAt: number;
  /** TTL in milliseconds for this entry */
  ttlMs: number;
  /** How many times this entry has been read */
  hitCount: number;
  /** Version tag for backtest reproducibility */
  version: string;
}

/** TTL configuration per feature category (in milliseconds) */
export interface FeatureTTLConfig {
  technical: number;
  volatility: number;
  volume: number;
  'on-chain': number;
  liquidity: number;
  sentiment: number;
}

/** LRU cache statistics */
export interface FeatureCacheStats {
  hits: number;
  misses: number;
  evictions: number;
  entries: number;
  maxEntries: number;
  memoryEstimateBytes: number;
  hitRate: number;
}

// ============================================================
// CATALOG / LINEAGE TYPES
// ============================================================

/** Metadata about a feature's source data and computation */
export interface FeatureLineage {
  /** Feature name */
  featureName: FeatureName;
  /** Category */
  category: FeatureCategory;
  /** Description of what this feature measures */
  description: string;
  /** Source data dependencies */
  sourceDependencies: SourceDependency[];
  /** Computation function name */
  computeFunction: string;
  /** Minimum data points required */
  minDataPoints: number;
  /** When this feature definition was last updated */
  definitionVersion: string;
}

/** A source data dependency for a feature */
export interface SourceDependency {
  /** Type of source data */
  type: 'ohlcv' | 'on-chain' | 'orderbook' | 'trades' | 'external';
  /** Required timeframe (for OHLCV data) */
  timeframe?: string;
  /** Minimum history required */
  minHistoryBars: number;
  /** Source identifier (e.g. 'binance', 'coingecko', 'sqd') */
  source: string;
}

/** Point-in-time feature snapshot for backtesting */
export interface PointInTimeFeatureSet {
  /** The feature set as it existed at asOfTimestamp */
  featureSet: FeatureSet;
  /** The requested point-in-time timestamp */
  asOfTimestamp: number;
  /** Whether this is exact or nearest-available */
  isExact: boolean;
  /** The version of the feature computation code used */
  codeVersion: string;
}

// ============================================================
// COMPUTATION INPUT TYPES
// ============================================================

/** Raw OHLCV candle data for feature computation */
export interface OHLCVBar {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** On-chain supplementary data for feature computation */
export interface OnChainData {
  /** Net whale flow (buy - sell) in USD for different windows */
  whaleFlow1h: number;
  whaleFlow4h: number;
  whaleFlow24h: number;
  /** Smart money net flow in USD */
  smartMoneyNetFlow: number;
  /** Bot activity ratio (0-1, proportion of volume from bots) */
  botActivityRatio: number;
  /** Holder count change in last 24h */
  holderChange24h: number;
}

/** Liquidity/orderbook data for feature computation */
export interface LiquidityData {
  /** Bid-ask spread as percentage */
  spreadPct: number;
  /** Depth ratio (bid depth / ask depth) at 2% from mid */
  depthRatio: number;
  /** Estimated slippage for a $1000 market order, as percentage */
  slippageEstimate: number;
}

/** Sentiment/derivatives data for feature computation */
export interface SentimentData {
  /** Buy/sell pressure score (-100 to +100) */
  buySellPressure: number;
  /** Funding rate deviation from 7d average (in basis points) */
  fundingRateDeviation: number;
  /** Open interest change in last 4h (as percentage) */
  openInterestChange: number;
}

/** Complete input data for feature computation */
export interface FeatureComputationInput {
  tokenAddress: string;
  chain: string;
  /** OHLCV bars for the primary timeframe (1h), sorted ascending */
  ohlcvBars: OHLCVBar[];
  /** 4h OHLCV bars for volume/volatility features */
  ohlcv4h: OHLCVBar[];
  /** 1d OHLCV bars for longer-term features */
  ohlcv1d: OHLCVBar[];
  /** On-chain supplementary data (nullable if unavailable) */
  onChainData: OnChainData | null;
  /** Liquidity data (nullable if unavailable) */
  liquidityData: LiquidityData | null;
  /** Sentiment data (nullable if unavailable) */
  sentimentData: SentimentData | null;
  /** Timestamp of computation */
  computedAt: number;
}

// ============================================================
// FEATURE DEFINITION REGISTRY
// ============================================================

/** A feature definition with its compute function and metadata */
export interface FeatureDefinition {
  name: FeatureName;
  category: FeatureCategory;
  description: string;
  compute: (input: FeatureComputationInput) => FeatureValue;
  /** Name of the computation function (for lineage tracking) */
  computeFunction: string;
  minDataPoints: number;
  sourceDependencies: SourceDependency[];
}

// ============================================================
// CONSTANTS
// ============================================================

/** Default TTL configuration per category (in milliseconds) */
export const DEFAULT_FEATURE_TTLS: FeatureTTLConfig = {
  technical: 30_000,      // 30 seconds
  volatility: 60_000,     // 60 seconds
  volume: 30_000,         // 30 seconds
  'on-chain': 300_000,    // 5 minutes
  liquidity: 60_000,      // 60 seconds
  sentiment: 120_000,     // 2 minutes
};

/** Current feature computation code version */
export const FEATURE_STORE_VERSION = '1.0.0';

/** Total number of features per token */
export const TOTAL_FEATURE_COUNT = 43;

/** Ordered list of all feature names (defines vector order) */
export const ALL_FEATURE_NAMES: FeatureName[] = [
  // Technical (22)
  'rsi_14',
  'ma_7',
  'ma_25',
  'ma_50',
  'ma_200',
  'ema_12',
  'ema_26',
  'bollinger_upper',
  'bollinger_middle',
  'bollinger_lower',
  'bollinger_bandwidth',
  'bollinger_percent_b',
  'atr_14',
  'macd_line',
  'macd_signal',
  'macd_histogram',
  'stochastic_k',
  'stochastic_d',
  'adx',
  'cci',
  'obv',
  'vwap',
  // Volatility (5)
  'realized_vol_1h',
  'realized_vol_4h',
  'realized_vol_24h',
  'garman_klass_vol',
  'parkinson_vol',
  // Volume (4)
  'volume_ma_ratio',
  'volume_trend_1h',
  'volume_trend_4h',
  'relative_volume',
  // On-chain (6)
  'whale_flow_1h',
  'whale_flow_4h',
  'whale_flow_24h',
  'smart_money_net_flow',
  'bot_activity_ratio',
  'holder_change_24h',
  // Liquidity (3)
  'spread_pct',
  'depth_ratio',
  'slippage_estimate',
  // Sentiment (3)
  'buy_sell_pressure',
  'funding_rate_deviation',
  'open_interest_change',
];

/** Mapping from feature name to its category */
export const FEATURE_CATEGORY_MAP: Record<FeatureName, FeatureCategory> = {
  // Technical
  rsi_14: 'technical',
  ma_7: 'technical',
  ma_25: 'technical',
  ma_50: 'technical',
  ma_200: 'technical',
  ema_12: 'technical',
  ema_26: 'technical',
  bollinger_upper: 'technical',
  bollinger_middle: 'technical',
  bollinger_lower: 'technical',
  bollinger_bandwidth: 'technical',
  bollinger_percent_b: 'technical',
  atr_14: 'technical',
  macd_line: 'technical',
  macd_signal: 'technical',
  macd_histogram: 'technical',
  stochastic_k: 'technical',
  stochastic_d: 'technical',
  adx: 'technical',
  cci: 'technical',
  obv: 'technical',
  vwap: 'technical',
  // Volatility
  realized_vol_1h: 'volatility',
  realized_vol_4h: 'volatility',
  realized_vol_24h: 'volatility',
  garman_klass_vol: 'volatility',
  parkinson_vol: 'volatility',
  // Volume
  volume_ma_ratio: 'volume',
  volume_trend_1h: 'volume',
  volume_trend_4h: 'volume',
  relative_volume: 'volume',
  // On-chain
  whale_flow_1h: 'on-chain',
  whale_flow_4h: 'on-chain',
  whale_flow_24h: 'on-chain',
  smart_money_net_flow: 'on-chain',
  bot_activity_ratio: 'on-chain',
  holder_change_24h: 'on-chain',
  // Liquidity
  spread_pct: 'liquidity',
  depth_ratio: 'liquidity',
  slippage_estimate: 'liquidity',
  // Sentiment
  buy_sell_pressure: 'sentiment',
  funding_rate_deviation: 'sentiment',
  open_interest_change: 'sentiment',
};
