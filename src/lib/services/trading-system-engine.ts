/**
 * Trading System Engine - CryptoQuant Terminal
 *
 * 30 pre-built system templates across 8 categories
 * Auto-configuration with editable parameters
 * Token phase detection (7 phases)
 *
 * Each system template contains 5 configurable layers:
 *   1. Asset Filter  — which tokens qualify
 *   2. Phase Config  — which token lifecycle phases are valid
 *   3. Entry Signal  — conditions that trigger a position open
 *   4. Execution     — how the order is placed (limit, market, DCA…)
 *   5. Exit Signal   — conditions that trigger a position close
 */

// ============================================================
// 1. TYPES & INTERFACES
// ============================================================

export type SystemCategory =
  | 'ALPHA_HUNTER'
  | 'SMART_MONEY'
  | 'TECHNICAL'
  | 'DEFENSIVE'
  | 'BOT_AWARE'
  | 'DEEP_ANALYSIS'
  | 'MICRO_STRUCTURE'
  | 'ADAPTIVE';

export type TokenPhase =
  | 'GENESIS'      // < 6 h
  | 'LAUNCH'       // 6 h – 48 h
  | 'EARLY'        // 2 d – 14 d
  | 'GROWTH'       // 14 d – 60 d
  | 'MATURE'       // 60 d – 180 d
  | 'ESTABLISHED'  // 180 d – 1 yr
  | 'LEGACY';      // > 1 yr

export type OperationType =
  | 'LONG'
  | 'SHORT'
  | 'LONG_SHORT'
  | 'SCALP'
  | 'SWING'
  | 'POSITION'
  | 'EXIT_ONLY'
  | 'HEDGE';

export type Timeframe =
  | '1m'
  | '5m'
  | '15m'
  | '1h'
  | '4h'
  | '1d'
  | '1w';

export type AllocationMethod =
  | 'FIXED_FRACTIONAL'
  | 'FIXED_RATIO'
  | 'VOLATILITY_TARGETING'
  | 'MAX_DRAWDOWN_CONTROL'
  | 'EQUAL_WEIGHT'
  | 'MEAN_VARIANCE'
  | 'MIN_VARIANCE'
  | 'RISK_PARITY'
  | 'SCORE_BASED'
  | 'KELLY_MODIFIED'
  | 'REGIME_BASED'
  | 'RL_ALLOCATION'
  | 'META_ALLOCATION'
  | 'ADAPTIVE'
  | 'CUSTOM_COMPOSITE'
  | 'FIXED_AMOUNT';

// ---- 5-Layer Config Types ----

export interface AssetFilterConfig {
  minLiquidityUsd: number;
  maxLiquidityUsd?: number;
  minMarketCapUsd?: number;
  maxMarketCapUsd?: number;
  minHolders?: number;
  maxHolders?: number;
  minVolume24h?: number;
  maxAgeHours?: number;
  minAgeHours?: number;
  chains: string[];
  tokenTypes: string[];
  excludeRugScoreAbove?: number;
  excludeWashTradeAbove?: number;
  minSmartMoneyHolders?: number;
  maxBotRatio?: number;
  customFilters?: Record<string, unknown>;
}

export interface PhaseConfig {
  allowedPhases: TokenPhase[];
  preferredPhase?: TokenPhase;
  phaseWeight?: Record<TokenPhase, number>;
}

export interface EntrySignalConfig {
  type: string;
  conditions: string[];
  minConfidence: number;
  requireConfirmation: boolean;
  confirmationTimeframes?: Timeframe[];
  indicators: string[];
  thresholds: Record<string, number | boolean>;
  customParams?: Record<string, unknown>;
}

export interface ExecutionConfig {
  orderType: 'MARKET' | 'LIMIT' | 'TWAP' | 'DCA' | 'ICEBERG';
  slippageTolerancePct: number;
  maxPositionSizePct: number;
  dcaLevels?: number[];
  dcaIntervals?: number[];
  limitOffsetPct?: number;
  timeInForce?: 'GTC' | 'IOC' | 'FOK';
  priorityFee?: number;
  customParams?: Record<string, unknown>;
}

export interface ExitSignalConfig {
  stopLossPct: number;
  takeProfitPct: number;
  trailingStopPct?: number;
  trailingActivationPct?: number;
  timeBasedExitMin?: number;
  exitConditions: string[];
  partialExitLevels?: Array<{ pctOfPosition: number; atProfitPct: number }>;
  breakevenMovePct?: number;
  customParams?: Record<string, unknown>;
}

export interface RiskParameters {
  maxPositionSizePct: number;
  maxPortfolioRiskPct: number;
  maxDailyLossPct: number;
  maxDrawdownPct: number;
  maxOpenPositions: number;
  maxCorrelatedPositions: number;
  minRiskRewardRatio: number;
  positionTimeoutMin: number;
}

export interface BigDataContextRequirement {
  requiresRegimeDetection: boolean;
  requiresWhaleForecast: boolean;
  requiresBotSwarmDetection: boolean;
  requiresLiquidityDrain: boolean;
  requiresSmartMoneyPositioning: boolean;
  requiresAnomalyDetection: boolean;
  requiresMeanReversionZones: boolean;
  customRequirements?: string[];
}

export interface SystemTemplate {
  name: string;
  icon: string;
  category: SystemCategory;
  description: string;
  operationType: OperationType;

  // 5-Layer Architecture
  assetFilter: AssetFilterConfig;
  phaseConfig: PhaseConfig;
  entrySignal: EntrySignalConfig;
  executionConfig: ExecutionConfig;
  exitSignal: ExitSignalConfig;

  // Risk
  riskParams: RiskParameters;

  // Allocation
  allocationMethod: AllocationMethod;

  // Timeframes
  primaryTimeframe: Timeframe;
  confirmTimeframes: Timeframe[];

  // Big Data
  bigDataContext: BigDataContextRequirement;
}

export interface CategoryInfo {
  id: SystemCategory;
  name: string;
  icon: string;
  description: string;
  riskLevel: 'EXTREME' | 'HIGH' | 'MEDIUM' | 'LOW' | 'VERY_LOW';
  templateNames: string[];
}

// ============================================================
// 2. SYSTEM CATEGORIES REGISTRY
// ============================================================

export const SYSTEM_CATEGORIES: Record<SystemCategory, CategoryInfo> = {
  ALPHA_HUNTER: {
    id: 'ALPHA_HUNTER',
    name: 'Alpha Hunter',
    icon: '🎯',
    description:
      'Buscan retornos extremos en tokens recién creados. Alto riesgo, alto reward.',
    riskLevel: 'EXTREME',
    templateNames: [
      'Sniper Genesis',
      'Meme Rocket',
      'Bot Follower Alpha',
      'New Listing Scalper',
    ],
  },
  SMART_MONEY: {
    id: 'SMART_MONEY',
    name: 'Smart Money',
    icon: '🧠',
    description:
      'Siguen al dinero inteligente. Baja frecuencia, alta convicción.',
    riskLevel: 'MEDIUM',
    templateNames: [
      'Whale Tail',
      'Smart Entry Mirror',
      'Early Bird',
      'SM Exit Detector',
    ],
  },
  TECHNICAL: {
    id: 'TECHNICAL',
    name: 'Technical',
    icon: '📊',
    description:
      'Análisis técnico potenciado con datos on-chain y Big Data.',
    riskLevel: 'MEDIUM',
    templateNames: [
      'Momentum Breakout',
      'Mean Reversion',
      'Trend Rider',
      'V-Shape Recovery',
      'Range Breakout',
    ],
  },
  DEFENSIVE: {
    id: 'DEFENSIVE',
    name: 'Defensive',
    icon: '🛡️',
    description:
      'Protección de capital como prioridad. Win rate alto, retornos moderados.',
    riskLevel: 'LOW',
    templateNames: [
      'Rug Pull Avoider',
      'Liquidity Guardian',
      'Stable Yield',
      'Capital Preserver',
      'Drawdown Limiter',
    ],
  },
  BOT_AWARE: {
    id: 'BOT_AWARE',
    name: 'Bot Aware',
    icon: '🤖',
    description:
      'Explotan o se protegen del comportamiento de bots.',
    riskLevel: 'HIGH',
    templateNames: [
      'MEV Shadow',
      'Anti-Sniper Shield',
      'Wash Trade Filter',
      'Bot Swarm Predictor',
    ],
  },
  DEEP_ANALYSIS: {
    id: 'DEEP_ANALYSIS',
    name: 'Deep Analysis',
    icon: '🔬',
    description:
      'Estrategias de profundidad con análisis multi-capa.',
    riskLevel: 'MEDIUM',
    templateNames: [
      'Fundamental Scanner',
      'Holder Evolution',
      'Cross-Chain Arbitrage',
      'DEX Depth Analyzer',
      'Long-Term Accumulation',
    ],
  },
  MICRO_STRUCTURE: {
    id: 'MICRO_STRUCTURE',
    name: 'Micro Structure',
    icon: '⚡',
    description:
      'Operan en micro-estructura: order flow, mempool, latencia.',
    riskLevel: 'HIGH',
    templateNames: [
      'Mempool Sniper',
      'Order Flow Imbalance',
      'Gas Fee Predictor',
      'Block Timing',
    ],
  },
  ADAPTIVE: {
    id: 'ADAPTIVE',
    name: 'Adaptive',
    icon: '🔄',
    description:
      'Se adaptan automáticamente al regime del mercado.',
    riskLevel: 'MEDIUM',
    templateNames: [
      'Regime Switcher',
      'Volatility Adapter',
      'Multi-Strategy Fusion',
      'Self-Optimizer',
    ],
  },
};

// ============================================================
// 3. SYSTEM TEMPLATES — 30 Pre-Built
// ============================================================

export const SYSTEM_TEMPLATES: SystemTemplate[] = [
  // ─── ALPHA_HUNTER (4) ────────────────────────────────────────
  {
    name: 'Sniper Genesis',
    icon: '🎯',
    category: 'ALPHA_HUNTER',
    description:
      'Entra en el bloque 0-1 de tokens recién listados. Máxima velocidad, máximo riesgo. Solo para operadores experimentados con ejecución automatizada.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 5000,
      maxAgeHours: 6,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['new_listing', 'presale'],
      excludeRugScoreAbove: 60,
      maxBotRatio: 0.7,
    },
    phaseConfig: {
      allowedPhases: ['GENESIS', 'LAUNCH'],
      preferredPhase: 'GENESIS',
      phaseWeight: { GENESIS: 1.0, LAUNCH: 0.6, EARLY: 0.2, GROWTH: 0, MATURE: 0, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'BLOCK_ZERO_ENTRY',
      conditions: ['token_created_within_1_block', 'initial_liquidity_added', 'creator_not_flagged'],
      minConfidence: 0.4,
      requireConfirmation: false,
      indicators: ['block_height', 'liquidity_event', 'creator_history'],
      thresholds: { maxAgeBlocks: 1, minInitialLiq: 5000 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 5,
      maxPositionSizePct: 2,
      priorityFee: 2,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 50,
      trailingStopPct: 15,
      trailingActivationPct: 20,
      timeBasedExitMin: 120,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 50, atProfitPct: 25 }],
    },
    riskParams: {
      maxPositionSizePct: 2,
      maxPortfolioRiskPct: 5,
      maxDailyLossPct: 3,
      maxDrawdownPct: 10,
      maxOpenPositions: 3,
      maxCorrelatedPositions: 1,
      minRiskRewardRatio: 3,
      positionTimeoutMin: 120,
    },
    allocationMethod: 'FIXED_FRACTIONAL',
    primaryTimeframe: '1m',
    confirmTimeframes: ['5m'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'Meme Rocket',
    icon: '🚀',
    category: 'ALPHA_HUNTER',
    description:
      'Detecta tokens meme con potencial viral antes del pump. Analiza velocidad de adopción, social momentum y patrones de memes exitosos previos.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 20000,
      maxAgeHours: 72,
      minVolume24h: 5000,
      chains: ['solana', 'base', 'ethereum'],
      tokenTypes: ['meme', 'social'],
      excludeRugScoreAbove: 50,
      maxBotRatio: 0.6,
      customFilters: { minHolderGrowthRate: 1.5 },
    },
    phaseConfig: {
      allowedPhases: ['GENESIS', 'LAUNCH', 'EARLY'],
      preferredPhase: 'LAUNCH',
      phaseWeight: { GENESIS: 0.7, LAUNCH: 1.0, EARLY: 0.5, GROWTH: 0, MATURE: 0, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'MEME_VIRAL_DETECT',
      conditions: ['holder_growth_accelerating', 'volume_spike_3x', 'social_mention_increase'],
      minConfidence: 0.5,
      requireConfirmation: true,
      confirmationTimeframes: ['15m', '1h'],
      indicators: ['holder_velocity', 'volume_ratio', 'social_score', 'price_momentum'],
      thresholds: { holderGrowthMin: 1.5, volumeSpikeMin: 3, priceChangeMin: 10 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 3,
      maxPositionSizePct: 3,
      limitOffsetPct: 1,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -12,
      takeProfitPct: 40,
      trailingStopPct: 20,
      trailingActivationPct: 15,
      timeBasedExitMin: 1440,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'holder_growth_stalled'],
      partialExitLevels: [
        { pctOfPosition: 30, atProfitPct: 15 },
        { pctOfPosition: 40, atProfitPct: 30 },
      ],
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 5,
      maxDrawdownPct: 15,
      maxOpenPositions: 5,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2.5,
      positionTimeoutMin: 1440,
    },
    allocationMethod: 'KELLY_MODIFIED',
    primaryTimeframe: '15m',
    confirmTimeframes: ['1h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['social_sentiment_feed', 'holder_velocity_tracker'],
    },
  },
  {
    name: 'Bot Follower Alpha',
    icon: '🤖',
    category: 'ALPHA_HUNTER',
    description:
      'Copia las operaciones de bots MEV rentables identificados. Aprovecha la velocidad y la información de bots que consistentemente generan ganancias.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 15000,
      maxAgeHours: 168,
      chains: ['solana', 'ethereum'],
      tokenTypes: ['any'],
      maxBotRatio: 0.8,
      customFilters: { requireProfitableBotActivity: true },
    },
    phaseConfig: {
      allowedPhases: ['GENESIS', 'LAUNCH', 'EARLY', 'GROWTH'],
      preferredPhase: 'LAUNCH',
      phaseWeight: { GENESIS: 0.8, LAUNCH: 1.0, EARLY: 0.7, GROWTH: 0.3, MATURE: 0, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'BOT_COPY_SIGNAL',
      conditions: ['profitable_mev_bot_entered', 'bot_historical_winrate_gt_60', 'position_size_significant'],
      minConfidence: 0.55,
      requireConfirmation: false,
      indicators: ['mev_bot_tracker', 'bot_winrate', 'bot_position_size', 'entry_timing'],
      thresholds: { minBotWinrate: 0.6, minBotPositionUsd: 500, maxDelayBlocks: 2 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 4,
      maxPositionSizePct: 2.5,
      priorityFee: 3,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -10,
      takeProfitPct: 30,
      trailingStopPct: 12,
      trailingActivationPct: 10,
      timeBasedExitMin: 240,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'bot_exited_position', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 50, atProfitPct: 15 }],
    },
    riskParams: {
      maxPositionSizePct: 2.5,
      maxPortfolioRiskPct: 7,
      maxDailyLossPct: 4,
      maxDrawdownPct: 12,
      maxOpenPositions: 4,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 240,
    },
    allocationMethod: 'SCORE_BASED',
    primaryTimeframe: '5m',
    confirmTimeframes: ['15m'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['mev_bot_tracker', 'bot_profitability_history'],
    },
  },
  {
    name: 'New Listing Scalper',
    icon: '⚡',
    category: 'ALPHA_HUNTER',
    description:
      'Aprovecha la volatilidad extrema de tokens recién listados en DEX. Entrada rápida, salida rápida, captura el primer movimiento de precio.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 10000,
      maxAgeHours: 48,
      chains: ['solana', 'ethereum', 'base', 'arbitrum'],
      tokenTypes: ['new_listing'],
      excludeRugScoreAbove: 55,
      maxBotRatio: 0.75,
    },
    phaseConfig: {
      allowedPhases: ['GENESIS', 'LAUNCH'],
      preferredPhase: 'LAUNCH',
      phaseWeight: { GENESIS: 0.9, LAUNCH: 1.0, EARLY: 0.1, GROWTH: 0, MATURE: 0, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'VOLATILITY_SPIKE_ENTRY',
      conditions: ['new_listing_detected', 'initial_price_discovery', 'volume_surge'],
      minConfidence: 0.45,
      requireConfirmation: false,
      indicators: ['listing_detector', 'price_discovery_phase', 'volume_profile'],
      thresholds: { maxAgeHours: 48, minVolumeMultiplier: 5, minPriceMove: 8 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 6,
      maxPositionSizePct: 2,
      priorityFee: 2,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 25,
      trailingStopPct: 10,
      trailingActivationPct: 12,
      timeBasedExitMin: 60,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'volume_dried_up', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 60, atProfitPct: 12 }],
    },
    riskParams: {
      maxPositionSizePct: 2,
      maxPortfolioRiskPct: 5,
      maxDailyLossPct: 4,
      maxDrawdownPct: 10,
      maxOpenPositions: 3,
      maxCorrelatedPositions: 1,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 60,
    },
    allocationMethod: 'FIXED_FRACTIONAL',
    primaryTimeframe: '1m',
    confirmTimeframes: ['5m'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },

  // ─── SMART_MONEY (4) ────────────────────────────────────────
  {
    name: 'Whale Tail',
    icon: '🐋',
    category: 'SMART_MONEY',
    description:
      'Detecta patrones de acumulación de ballenas. Cuando una ballena acumula gradualmente, el sistema entra en la misma dirección esperando un movimiento alcista posterior.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 50000,
      minMarketCapUsd: 100000,
      minHolders: 100,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      minSmartMoneyHolders: 2,
      excludeRugScoreAbove: 40,
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.3, EARLY: 0.7, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.5, LEGACY: 0.2 },
    },
    entrySignal: {
      type: 'WHALE_ACCUMULATION',
      conditions: ['whale_net_accumulation_detected', 'multiple_whales_same_direction', 'price_not_yet_moved'],
      minConfidence: 0.65,
      requireConfirmation: true,
      confirmationTimeframes: ['1h', '4h'],
      indicators: ['whale_net_flow', 'whale_wallet_count', 'price_vs_whale_entry', 'accumulation_score'],
      thresholds: { minWhaleCount: 2, minNetFlowUsd: 10000, maxPriceMovePct: 5 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 2,
      maxPositionSizePct: 5,
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -15,
      takeProfitPct: 35,
      trailingStopPct: 10,
      trailingActivationPct: 15,
      timeBasedExitMin: 10080,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'whale_distribution_detected'],
      partialExitLevels: [
        { pctOfPosition: 30, atProfitPct: 15 },
        { pctOfPosition: 30, atProfitPct: 25 },
      ],
      breakevenMovePct: 5,
    },
    riskParams: {
      maxPositionSizePct: 5,
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 12,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 10080,
    },
    allocationMethod: 'KELLY_MODIFIED',
    primaryTimeframe: '1h',
    confirmTimeframes: ['4h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: true,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'Smart Entry Mirror',
    icon: '🧠',
    category: 'SMART_MONEY',
    description:
      'Replica las entradas de billeteras Smart Money identificadas. Entra cuando SM entra, con protección adicional de confirmación multi-señal.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 30000,
      minHolders: 50,
      chains: ['solana', 'ethereum', 'base', 'arbitrum'],
      tokenTypes: ['any'],
      minSmartMoneyHolders: 1,
      excludeRugScoreAbove: 45,
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'EARLY',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.4, EARLY: 1.0, GROWTH: 0.8, MATURE: 0.5, ESTABLISHED: 0.3, LEGACY: 0.1 },
    },
    entrySignal: {
      type: 'SM_ENTRY_MIRROR',
      conditions: ['smart_money_buy_detected', 'sm_wallet_verified', 'entry_price_reasonable'],
      minConfidence: 0.7,
      requireConfirmation: true,
      confirmationTimeframes: ['1h', '4h'],
      indicators: ['sm_entry_detector', 'sm_wallet_score', 'entry_price_vs_avg', 'volume_confirmation'],
      thresholds: { minSMWalletScore: 70, maxPriceDeviationFromSM: 5, minVolumeConfirmation: 1.5 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 2,
      maxPositionSizePct: 4,
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -15,
      takeProfitPct: 40,
      trailingStopPct: 12,
      trailingActivationPct: 15,
      timeBasedExitMin: 7200,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'sm_exit_detected'],
      partialExitLevels: [
        { pctOfPosition: 40, atProfitPct: 20 },
        { pctOfPosition: 30, atProfitPct: 30 },
      ],
      breakevenMovePct: 5,
    },
    riskParams: {
      maxPositionSizePct: 4,
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 12,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2.5,
      positionTimeoutMin: 7200,
    },
    allocationMethod: 'KELLY_MODIFIED',
    primaryTimeframe: '1h',
    confirmTimeframes: ['4h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'Early Bird',
    icon: '🐦',
    category: 'SMART_MONEY',
    description:
      'Identifica tokens donde Smart Money acaba de entrar. Entra rápidamente antes de que el precio refleje la nueva demanda institucional.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 20000,
      minHolders: 30,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      minSmartMoneyHolders: 1,
      excludeRugScoreAbove: 45,
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH'],
      preferredPhase: 'EARLY',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.6, EARLY: 1.0, GROWTH: 0.7, MATURE: 0, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'SM_JUST_ENTERED',
      conditions: ['sm_entry_last_4_hours', 'price_not_yet_pumped', 'liquidity_sufficient'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1h'],
      indicators: ['sm_recent_entry', 'price_since_sm_entry', 'liquidity_check'],
      thresholds: { maxHoursSinceSMEntry: 4, maxPriceMoveSinceEntry: 8, minLiquidity: 20000 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 2,
      maxPositionSizePct: 3,
      limitOffsetPct: 1,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -12,
      takeProfitPct: 35,
      trailingStopPct: 10,
      trailingActivationPct: 12,
      timeBasedExitMin: 4320,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 50, atProfitPct: 18 }],
      breakevenMovePct: 4,
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 3,
      maxDrawdownPct: 10,
      maxOpenPositions: 5,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 4320,
    },
    allocationMethod: 'SCORE_BASED',
    primaryTimeframe: '1h',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'SM Exit Detector',
    icon: '🚪',
    category: 'SMART_MONEY',
    description:
      'Detecta cuándo Smart Money sale de una posición. Sistema de solo salida que genera alertas y puede abrir posiciones cortas basándose en la salida de SM.',
    operationType: 'EXIT_ONLY',
    assetFilter: {
      minLiquidityUsd: 30000,
      minHolders: 50,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      minSmartMoneyHolders: 2,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.6, LEGACY: 0.3 },
    },
    entrySignal: {
      type: 'SM_EXIT_SIGNAL',
      conditions: ['sm_wallet_selling_detected', 'multiple_sm_exiting', 'distribution_pattern'],
      minConfidence: 0.7,
      requireConfirmation: true,
      confirmationTimeframes: ['15m', '1h'],
      indicators: ['sm_exit_detector', 'sm_sell_volume', 'distribution_score', 'price_vs_sm_exit'],
      thresholds: { minSMExiting: 2, minSellVolumeUsd: 5000, distributionScoreMin: 0.6 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 2,
      maxPositionSizePct: 3,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 15,
      timeBasedExitMin: 1440,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'time_expired'],
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 6,
      maxDailyLossPct: 2,
      maxDrawdownPct: 8,
      maxOpenPositions: 4,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 1440,
    },
    allocationMethod: 'FIXED_FRACTIONAL',
    primaryTimeframe: '15m',
    confirmTimeframes: ['1h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: true,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: true,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
    },
  },

  // ─── TECHNICAL (5) ───────────────────────────────────────────
  {
    name: 'Momentum Breakout',
    icon: '📈',
    category: 'TECHNICAL',
    description:
      'Detecta rupturas de niveles clave con confirmación de volumen. Combina análisis técnico clásico con datos on-chain para filtrar falsas rupturas.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 50000,
      minMarketCapUsd: 500000,
      minHolders: 200,
      minVolume24h: 10000,
      chains: ['solana', 'ethereum', 'base', 'arbitrum'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 30,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.6, LEGACY: 0.4 },
    },
    entrySignal: {
      type: 'BREAKOUT_WITH_VOLUME',
      conditions: ['price_breaks_resistance', 'volume_2x_average', 'rsi_not_overbought', 'on_chain_confirms'],
      minConfidence: 0.65,
      requireConfirmation: true,
      confirmationTimeframes: ['1h', '4h'],
      indicators: ['resistance_level', 'volume_ratio', 'rsi', 'on_chain_volume', 'atr'],
      thresholds: { volumeMultiplier: 2, rsiMax: 75, minBreakoutPct: 2 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1.5,
      maxPositionSizePct: 4,
      limitOffsetPct: 0.3,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -10,
      takeProfitPct: 30,
      trailingStopPct: 8,
      trailingActivationPct: 10,
      timeBasedExitMin: 4320,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'volume_declining', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 40, atProfitPct: 15 }],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 4,
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 12,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 4320,
    },
    allocationMethod: 'VOLATILITY_TARGETING',
    primaryTimeframe: '1h',
    confirmTimeframes: ['4h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'Mean Reversion',
    icon: '📉',
    category: 'TECHNICAL',
    description:
      'Opera retornos a la media tras sobre-reacciones del mercado. Cuando el precio se desvía significativamente de su media, entra esperando la corrección.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 100000,
      minMarketCapUsd: 1000000,
      minHolders: 500,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 25,
    },
    phaseConfig: {
      allowedPhases: ['GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'MATURE',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0, GROWTH: 0.5, MATURE: 1.0, ESTABLISHED: 0.9, LEGACY: 0.7 },
    },
    entrySignal: {
      type: 'MEAN_REVERSION_ENTRY',
      conditions: ['price_below_lower_band', 'z_score_below_neg2', 'on_chain_no_fundamental_change', 'volume_declining_selloff'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['4h', '1d'],
      indicators: ['bollinger_bands', 'z_score', 'on_chain_fundamentals', 'volume_profile'],
      thresholds: { zScoreMin: -2, bollingerPercentBMax: 0.1, minRsi: 25 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1,
      maxPositionSizePct: 5,
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 20,
      timeBasedExitMin: 10080,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'price_returned_to_mean', 'time_expired'],
      partialExitLevels: [
        { pctOfPosition: 50, atProfitPct: 10 },
        { pctOfPosition: 30, atProfitPct: 15 },
      ],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 5,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 2,
      maxDrawdownPct: 8,
      maxOpenPositions: 8,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 10080,
    },
    allocationMethod: 'RISK_PARITY',
    primaryTimeframe: '4h',
    confirmTimeframes: ['1d'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: true,
    },
  },
  {
    name: 'Trend Rider',
    icon: '🏄',
    category: 'TECHNICAL',
    description:
      'Sigue la tendencia predominante con trailing stop. Entra en pullbacks dentro de una tendencia establecida y deja correr las ganancias con protección de trailing.',
    operationType: 'POSITION',
    assetFilter: {
      minLiquidityUsd: 80000,
      minMarketCapUsd: 500000,
      minHolders: 300,
      minVolume24h: 15000,
      chains: ['solana', 'ethereum', 'base', 'arbitrum'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 30,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.6, GROWTH: 1.0, MATURE: 0.7, ESTABLISHED: 0.5, LEGACY: 0.3 },
    },
    entrySignal: {
      type: 'TREND_PULLBACK_ENTRY',
      conditions: ['trend_confirmed_up', 'price_pulled_back_to_support', 'volume_on_pullback_low', 'on_chain_trend_positive'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1h', '4h'],
      indicators: ['sma_20_50_crossover', 'support_level', 'pullback_volume', 'on_chain_trend'],
      thresholds: { smaCrossoverRequired: true, maxPullbackPct: 5, volumeDeclineMin: 0.5 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1.5,
      maxPositionSizePct: 5,
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -12,
      takeProfitPct: 0, // Uses trailing only
      trailingStopPct: 8,
      trailingActivationPct: 5,
      timeBasedExitMin: 20160,
      exitConditions: ['stop_loss_hit', 'trailing_stop_hit', 'trend_reversal_detected', 'time_expired'],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 5,
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 12,
      maxOpenPositions: 5,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 20160,
    },
    allocationMethod: 'KELLY_MODIFIED',
    primaryTimeframe: '1h',
    confirmTimeframes: ['4h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'V-Shape Recovery',
    icon: '📊',
    category: 'TECHNICAL',
    description:
      'Detecta recuperaciones en forma de V tras caídas bruscas. Identifica cuando una venta masiva es seguida rápidamente por compra agresiva, indicando un rebote.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 40000,
      minHolders: 100,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 35,
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.3, EARLY: 0.6, GROWTH: 1.0, MATURE: 0.7, ESTABLISHED: 0.5, LEGACY: 0.2 },
    },
    entrySignal: {
      type: 'V_SHAPE_DETECT',
      conditions: ['sharp_drop_detected', 'quick_recovery_started', 'volume_on_recovery_high', 'no_fundamental_damage'],
      minConfidence: 0.55,
      requireConfirmation: true,
      confirmationTimeframes: ['15m', '1h'],
      indicators: ['price_drop_rate', 'recovery_rate', 'volume_profile', 'holder_behavior'],
      thresholds: { minDropPct: 15, recoveryStartPct: 5, volumeOnRecoveryMin: 2 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 2,
      maxPositionSizePct: 3,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -10,
      takeProfitPct: 25,
      trailingStopPct: 8,
      trailingActivationPct: 10,
      timeBasedExitMin: 2880,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'recovery_stalled', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 50, atProfitPct: 12 }],
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 3,
      maxDrawdownPct: 10,
      maxOpenPositions: 4,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 2880,
    },
    allocationMethod: 'SCORE_BASED',
    primaryTimeframe: '15m',
    confirmTimeframes: ['1h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: true,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: true,
    },
  },
  {
    name: 'Range Breakout',
    icon: '🔓',
    category: 'TECHNICAL',
    description:
      'Detecta rupturas de rangos laterales. Cuando un token ha estado consolidando en un rango y rompe con volumen, entra en la dirección de la ruptura.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 60000,
      minMarketCapUsd: 500000,
      minHolders: 200,
      chains: ['solana', 'ethereum', 'base', 'arbitrum'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 30,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.7, ESTABLISHED: 0.5, LEGACY: 0.3 },
    },
    entrySignal: {
      type: 'RANGE_BREAKOUT',
      conditions: ['lateral_range_identified', 'breakout_with_volume', 'range_duration_minimum', 'false_breakout_filtered'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1h'],
      indicators: ['range_high_low', 'volume_on_breakout', 'range_duration', 'adx'],
      thresholds: { minRangeDuration: 24, minBreakoutVolume: 2, adxMin: 20 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1.5,
      maxPositionSizePct: 4,
      limitOffsetPct: 0.3,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 20,
      trailingStopPct: 6,
      trailingActivationPct: 8,
      timeBasedExitMin: 4320,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'range_reentry', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 40, atProfitPct: 10 }],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 4,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 2,
      maxDrawdownPct: 8,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 4320,
    },
    allocationMethod: 'FIXED_FRACTIONAL',
    primaryTimeframe: '1h',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },

  // ─── DEFENSIVE (5) ──────────────────────────────────────────
  {
    name: 'Rug Pull Avoider',
    icon: '🛡️',
    category: 'DEFENSIVE',
    description:
      'Solo opera en tokens con rug score < 20 y liquidez > $100K. Máxima seguridad, filtra agresivamente cualquier señal de riesgo de rug pull.',
    operationType: 'POSITION',
    assetFilter: {
      minLiquidityUsd: 100000,
      minMarketCapUsd: 1000000,
      minHolders: 500,
      minVolume24h: 20000,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 20,
      excludeWashTradeAbove: 5,
      maxBotRatio: 0.3,
    },
    phaseConfig: {
      allowedPhases: ['GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'ESTABLISHED',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0, GROWTH: 0.5, MATURE: 0.8, ESTABLISHED: 1.0, LEGACY: 0.9 },
    },
    entrySignal: {
      type: 'SAFE_ENTRY',
      conditions: ['rug_score_below_20', 'liquidity_above_100k', 'holder_base_stable', 'no_suspicious_patterns', 'verified_contract'],
      minConfidence: 0.75,
      requireConfirmation: true,
      confirmationTimeframes: ['4h', '1d'],
      indicators: ['rug_score', 'liquidity_depth', 'holder_stability', 'contract_verification', 'wash_trade_ratio'],
      thresholds: { maxRugScore: 20, minLiquidity: 100000, maxWashTradePct: 5 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 0.5,
      maxPositionSizePct: 5,
      limitOffsetPct: 0.2,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 15,
      timeBasedExitMin: 20160,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'rug_score_increased', 'liquidity_dropping', 'time_expired'],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 5,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 2,
      maxDrawdownPct: 8,
      maxOpenPositions: 8,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 20160,
    },
    allocationMethod: 'MAX_DRAWDOWN_CONTROL',
    primaryTimeframe: '4h',
    confirmTimeframes: ['1d'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: true,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'Liquidity Guardian',
    icon: '💧',
    category: 'DEFENSIVE',
    description:
      'Solo opera donde hay liquidez profunda y estable. Filtra tokens con liquidez concentrada o inestable que podría dificultar la salida.',
    operationType: 'POSITION',
    assetFilter: {
      minLiquidityUsd: 200000,
      minMarketCapUsd: 5000000,
      minHolders: 1000,
      minVolume24h: 50000,
      chains: ['solana', 'ethereum'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 15,
      maxBotRatio: 0.25,
    },
    phaseConfig: {
      allowedPhases: ['MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'ESTABLISHED',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0, GROWTH: 0, MATURE: 0.7, ESTABLISHED: 1.0, LEGACY: 0.9 },
    },
    entrySignal: {
      type: 'DEEP_LIQUIDITY_ENTRY',
      conditions: ['liquidity_depth_sufficient', 'bid_ask_spread_narrow', 'liquidity_history_stable', 'no_imminent_drain'],
      minConfidence: 0.8,
      requireConfirmation: true,
      confirmationTimeframes: ['1d'],
      indicators: ['liquidity_depth', 'spread_bps', 'liquidity_stability_score', 'drain_risk'],
      thresholds: { minDepthUsd: 200000, maxSpreadBps: 50, minStabilityScore: 0.8 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 0.3,
      maxPositionSizePct: 4,
      limitOffsetPct: 0.1,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -6,
      takeProfitPct: 12,
      timeBasedExitMin: 43200,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'liquidity_deteriorating', 'time_expired'],
      breakevenMovePct: 2,
    },
    riskParams: {
      maxPositionSizePct: 4,
      maxPortfolioRiskPct: 6,
      maxDailyLossPct: 1.5,
      maxDrawdownPct: 6,
      maxOpenPositions: 10,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 43200,
    },
    allocationMethod: 'EQUAL_WEIGHT',
    primaryTimeframe: '1d',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: true,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'Stable Yield',
    icon: '🏦',
    category: 'DEFENSIVE',
    description:
      'Opera tokens establecidos con base de holders estable. Busca retornos moderados y consistentes en activos de baja volatilidad con holders a largo plazo.',
    operationType: 'POSITION',
    assetFilter: {
      minLiquidityUsd: 500000,
      minMarketCapUsd: 10000000,
      minHolders: 5000,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['established', 'defi', 'l1_l2'],
      excludeRugScoreAbove: 10,
      maxBotRatio: 0.2,
    },
    phaseConfig: {
      allowedPhases: ['ESTABLISHED', 'LEGACY'],
      preferredPhase: 'ESTABLISHED',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0, GROWTH: 0, MATURE: 0, ESTABLISHED: 1.0, LEGACY: 0.8 },
    },
    entrySignal: {
      type: 'STABLE_HOLDER_ENTRY',
      conditions: ['holder_base_growing_slowly', 'price_at_support', 'stable_holders_increasing', 'low_volatility_regime'],
      minConfidence: 0.8,
      requireConfirmation: true,
      confirmationTimeframes: ['1d', '1w'],
      indicators: ['holder_growth_rate', 'support_level', 'stable_holder_ratio', 'volatility_regime'],
      thresholds: { maxHolderGrowthWeekly: 10, minStableHolderRatio: 0.7, maxVolatility: 0.3 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 0.2,
      maxPositionSizePct: 5,
      limitOffsetPct: 0.1,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -5,
      takeProfitPct: 10,
      timeBasedExitMin: 86400,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'holder_base_declining', 'time_expired'],
      breakevenMovePct: 2,
    },
    riskParams: {
      maxPositionSizePct: 5,
      maxPortfolioRiskPct: 5,
      maxDailyLossPct: 1,
      maxDrawdownPct: 5,
      maxOpenPositions: 12,
      maxCorrelatedPositions: 4,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 86400,
    },
    allocationMethod: 'MIN_VARIANCE',
    primaryTimeframe: '1d',
    confirmTimeframes: ['1w'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: true,
    },
  },
  {
    name: 'Capital Preserver',
    icon: '💰',
    category: 'DEFENSIVE',
    description:
      'Efectivo dominante. Solo entra con >85% de confianza. Prioridad absoluta de preservar capital sobre generar ganancias.',
    operationType: 'POSITION',
    assetFilter: {
      minLiquidityUsd: 300000,
      minMarketCapUsd: 20000000,
      minHolders: 2000,
      minVolume24h: 100000,
      chains: ['solana', 'ethereum'],
      tokenTypes: ['established', 'l1_l2'],
      excludeRugScoreAbove: 10,
      maxBotRatio: 0.2,
    },
    phaseConfig: {
      allowedPhases: ['ESTABLISHED', 'LEGACY'],
      preferredPhase: 'LEGACY',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0, GROWTH: 0, MATURE: 0, ESTABLISHED: 0.5, LEGACY: 1.0 },
    },
    entrySignal: {
      type: 'ULTRA_HIGH_CONFIDENCE',
      conditions: ['confidence_above_85', 'all_signals_aligned', 'no_counter_signals', 'regime_favorable', 'liquidity_rock_solid'],
      minConfidence: 0.85,
      requireConfirmation: true,
      confirmationTimeframes: ['4h', '1d'],
      indicators: ['composite_confidence', 'signal_alignment', 'regime_detector', 'liquidity_score'],
      thresholds: { minConfidence: 0.85, maxCounterSignals: 0, minRegimeScore: 0.7 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 0.2,
      maxPositionSizePct: 3,
      limitOffsetPct: 0.1,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -5,
      takeProfitPct: 8,
      timeBasedExitMin: 43200,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'confidence_dropped', 'time_expired'],
      breakevenMovePct: 1.5,
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 4,
      maxDailyLossPct: 1,
      maxDrawdownPct: 4,
      maxOpenPositions: 8,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 1.2,
      positionTimeoutMin: 43200,
    },
    allocationMethod: 'MAX_DRAWDOWN_CONTROL',
    primaryTimeframe: '4h',
    confirmTimeframes: ['1d'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: true,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: true,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: true,
    },
  },
  {
    name: 'Drawdown Limiter',
    icon: '📉',
    category: 'DEFENSIVE',
    description:
      'Reduce automáticamente el tamaño de posición cuando el drawdown aumenta. SL y TP dinámicos que se ajustan según el rendimiento reciente del sistema.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 50000,
      minMarketCapUsd: 500000,
      minHolders: 200,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 30,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'MATURE',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.3, GROWTH: 0.7, MATURE: 1.0, ESTABLISHED: 0.8, LEGACY: 0.5 },
    },
    entrySignal: {
      type: 'DYNAMIC_DD_ENTRY',
      conditions: ['drawdown_below_threshold', 'signal_quality_sufficient', 'position_budget_available'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1h'],
      indicators: ['current_drawdown', 'signal_confidence', 'position_budget', 'recent_performance'],
      thresholds: { maxDrawdownForEntry: 0.5, minSignalConfidence: 0.6 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1,
      maxPositionSizePct: 0, // Dynamically set
      limitOffsetPct: 0.3,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: 0, // Dynamic
      takeProfitPct: 0, // Dynamic
      timeBasedExitMin: 7200,
      exitConditions: ['dynamic_stop_hit', 'dynamic_tp_hit', 'drawdown_limit_reached', 'time_expired'],
      customParams: {
        slBase: -10,
        tpBase: 20,
        ddScaleFactor: 0.8,
      },
    },
    riskParams: {
      maxPositionSizePct: 0, // Dynamic
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 15,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 7200,
    },
    allocationMethod: 'ADAPTIVE',
    primaryTimeframe: '1h',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
    },
  },

  // ─── BOT_AWARE (4) ──────────────────────────────────────────
  {
    name: 'MEV Shadow',
    icon: '👻',
    category: 'BOT_AWARE',
    description:
      'Opera conociendo los patrones MEV. Aprovecha las oportunidades creadas por la actividad MEV sin ser víctima de ella. Ejecuta con protección anti-sandwich.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 30000,
      chains: ['solana', 'ethereum'],
      tokenTypes: ['any'],
      maxBotRatio: 0.8,
      customFilters: { requireMEVActivity: true },
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH', 'MATURE'],
      preferredPhase: 'EARLY',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.5, EARLY: 1.0, GROWTH: 0.7, MATURE: 0.4, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'MEV_AWARE_ENTRY',
      conditions: ['mev_opportunity_detected', 'anti_sandwich_protection_active', 'frontrun_avoidance_set', 'profitable_gap_exists'],
      minConfidence: 0.55,
      requireConfirmation: false,
      indicators: ['mev_activity_level', 'sandwich_risk', 'frontrun_detector', 'price_gap'],
      thresholds: { maxSandwichRisk: 0.3, minProfitGapBps: 20, maxFrontrunRisk: 0.4 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 3,
      maxPositionSizePct: 3,
      priorityFee: 5,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -10,
      takeProfitPct: 25,
      trailingStopPct: 8,
      trailingActivationPct: 10,
      timeBasedExitMin: 120,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'mev_threat_increased', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 50, atProfitPct: 12 }],
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 4,
      maxDrawdownPct: 12,
      maxOpenPositions: 4,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 120,
    },
    allocationMethod: 'VOLATILITY_TARGETING',
    primaryTimeframe: '5m',
    confirmTimeframes: ['15m'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['mev_mempool_scanner', 'sandwich_detector'],
    },
  },
  {
    name: 'Anti-Sniper Shield',
    icon: '🛡️',
    category: 'BOT_AWARE',
    description:
      'Evita tokens dominados por snipers. Filtra activamente tokens donde snipers controlan gran parte del volumen y busca los que tienen distribución saludable.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 40000,
      minHolders: 100,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      maxBotRatio: 0.4,
      customFilters: { maxSniperHolderPct: 20 },
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.6, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.5, LEGACY: 0.3 },
    },
    entrySignal: {
      type: 'SNIPER_FREE_ENTRY',
      conditions: ['sniper_ratio_below_threshold', 'healthy_holder_distribution', 'no_recent_sniper_pump_dump', 'organic_volume'],
      minConfidence: 0.65,
      requireConfirmation: true,
      confirmationTimeframes: ['1h'],
      indicators: ['sniper_ratio', 'holder_distribution_gini', 'pump_dump_detector', 'organic_volume_ratio'],
      thresholds: { maxSniperPct: 20, maxGini: 0.6, minOrganicVolumePct: 60 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1.5,
      maxPositionSizePct: 4,
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 20,
      trailingStopPct: 7,
      trailingActivationPct: 10,
      timeBasedExitMin: 4320,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'sniper_activity_increased', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 40, atProfitPct: 10 }],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 4,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 2,
      maxDrawdownPct: 10,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 4320,
    },
    allocationMethod: 'RISK_PARITY',
    primaryTimeframe: '1h',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'Wash Trade Filter',
    icon: '🧹',
    category: 'BOT_AWARE',
    description:
      'Solo opera donde el wash trading es < 5%. Filtra agresivamente tokens con actividad de wash trading detectada, operando solo con volumen orgánico verificado.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 50000,
      minMarketCapUsd: 500000,
      minHolders: 300,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeWashTradeAbove: 5,
      excludeRugScoreAbove: 25,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'MATURE',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.3, GROWTH: 0.7, MATURE: 1.0, ESTABLISHED: 0.8, LEGACY: 0.5 },
    },
    entrySignal: {
      type: 'CLEAN_VOLUME_ENTRY',
      conditions: ['wash_trade_below_5pct', 'organic_volume_confirmed', 'trader_diversity_high', 'no_circular_patterns'],
      minConfidence: 0.65,
      requireConfirmation: true,
      confirmationTimeframes: ['1h', '4h'],
      indicators: ['wash_trade_score', 'organic_volume_pct', 'trader_diversity_index', 'circular_flow_detector'],
      thresholds: { maxWashTradePct: 5, minOrganicPct: 85, minTraderDiversity: 0.6 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1,
      maxPositionSizePct: 4,
      limitOffsetPct: 0.3,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -10,
      takeProfitPct: 15,
      trailingStopPct: 6,
      trailingActivationPct: 8,
      timeBasedExitMin: 7200,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'wash_trade_increased', 'time_expired'],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 4,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 2,
      maxDrawdownPct: 8,
      maxOpenPositions: 7,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 7200,
    },
    allocationMethod: 'KELLY_MODIFIED',
    primaryTimeframe: '1h',
    confirmTimeframes: ['4h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['wash_trade_detector', 'circular_flow_analyzer'],
    },
  },
  {
    name: 'Bot Swarm Predictor',
    icon: '🐝',
    category: 'BOT_AWARE',
    description:
      'Predice ataques de enjambre de bots antes de que ocurran. Usa análisis de patrones previos para anticipar coordinación masiva de bots y posicionarse accordingly.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 20000,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      customFilters: { requireBotActivityHistory: true },
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH'],
      preferredPhase: 'EARLY',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.7, EARLY: 1.0, GROWTH: 0.5, MATURE: 0, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'SWARM_PREDICTION',
      conditions: ['swarm_buildup_detected', 'historical_pattern_match', 'coordination_signals_present', 'pre_swarm_window'],
      minConfidence: 0.5,
      requireConfirmation: false,
      indicators: ['bot_coordination_score', 'pattern_matcher', 'pre_swarm_indicators', 'timing_model'],
      thresholds: { minCoordinationScore: 0.4, patternMatchMin: 0.6, preSwarmWindowMin: 5 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 4,
      maxPositionSizePct: 2.5,
      priorityFee: 3,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -12,
      takeProfitPct: 30,
      trailingStopPct: 10,
      trailingActivationPct: 12,
      timeBasedExitMin: 180,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'swarm_executed', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 60, atProfitPct: 15 }],
    },
    riskParams: {
      maxPositionSizePct: 2.5,
      maxPortfolioRiskPct: 7,
      maxDailyLossPct: 4,
      maxDrawdownPct: 12,
      maxOpenPositions: 4,
      maxCorrelatedPositions: 1,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 180,
    },
    allocationMethod: 'REGIME_BASED',
    primaryTimeframe: '15m',
    confirmTimeframes: ['1h'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['swarm_prediction_model', 'coordination_detector'],
    },
  },

  // ─── DEEP_ANALYSIS (5) ──────────────────────────────────────
  {
    name: 'Fundamental Scanner',
    icon: '🔬',
    category: 'DEEP_ANALYSIS',
    description:
      'Analiza fundamentales on-chain: holders, volumen orgánico, distribución, actividad de desarrollo. Solo entra en tokens con fundamentos sólidos.',
    operationType: 'POSITION',
    assetFilter: {
      minLiquidityUsd: 50000,
      minMarketCapUsd: 200000,
      minHolders: 200,
      chains: ['solana', 'ethereum', 'base', 'arbitrum'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 30,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.6, LEGACY: 0.3 },
    },
    entrySignal: {
      type: 'FUNDAMENTAL_QUALITY',
      conditions: ['holder_growth_positive', 'organic_volume_high', 'distribution_healthy', 'dev_activity_present', 'no_red_flags'],
      minConfidence: 0.65,
      requireConfirmation: true,
      confirmationTimeframes: ['4h', '1d'],
      indicators: ['holder_growth', 'organic_volume_pct', 'gini_coefficient', 'dev_commits', 'red_flag_score'],
      thresholds: { minHolderGrowthWeekly: 5, minOrganicPct: 70, maxGini: 0.7 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1,
      maxPositionSizePct: 4,
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -10,
      takeProfitPct: 25,
      trailingStopPct: 8,
      trailingActivationPct: 12,
      timeBasedExitMin: 20160,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'fundamentals_deteriorated', 'time_expired'],
      partialExitLevels: [
        { pctOfPosition: 30, atProfitPct: 12 },
        { pctOfPosition: 30, atProfitPct: 20 },
      ],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 4,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 2,
      maxDrawdownPct: 10,
      maxOpenPositions: 8,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 20160,
    },
    allocationMethod: 'MEAN_VARIANCE',
    primaryTimeframe: '4h',
    confirmTimeframes: ['1d'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['on_chain_fundamental_analyzer', 'dev_activity_tracker'],
    },
  },
  {
    name: 'Holder Evolution',
    icon: '👥',
    category: 'DEEP_ANALYSIS',
    description:
      'Rastrea la evolución de la base de holders. Detecta cuando la base de holders está creciendo de forma saludable y entra antes de que el precio refleje el crecimiento.',
    operationType: 'POSITION',
    assetFilter: {
      minLiquidityUsd: 30000,
      minHolders: 100,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 35,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.7, ESTABLISHED: 0.5, LEGACY: 0.2 },
    },
    entrySignal: {
      type: 'HOLDER_GROWTH_SIGNAL',
      conditions: ['holder_count_accelerating', 'new_holders_diverse', 'whale_holder_count_stable', 'price_not_yet_moved'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1d'],
      indicators: ['holder_count_velocity', 'new_holder_diversity', 'whale_holder_trend', 'price_vs_holder_growth'],
      thresholds: { minHolderVelocity: 1.2, minNewHolderDiversity: 0.5, maxPriceMovePct: 5 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1,
      maxPositionSizePct: 4,
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 20,
      trailingStopPct: 7,
      trailingActivationPct: 10,
      timeBasedExitMin: 14400,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'holder_growth_stalled', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 40, atProfitPct: 10 }],
      breakevenMovePct: 3,
    },
    riskParams: {
      maxPositionSizePct: 4,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 2,
      maxDrawdownPct: 8,
      maxOpenPositions: 7,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 14400,
    },
    allocationMethod: 'SCORE_BASED',
    primaryTimeframe: '1d',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['holder_evolution_tracker', 'holder_velocity_calculator'],
    },
  },
  {
    name: 'Cross-Chain Arbitrage',
    icon: '🔗',
    category: 'DEEP_ANALYSIS',
    description:
      'Detecta oportunidades de arbitraje entre cadenas. Aprovecha diferencias de precio del mismo token en DEXs de diferentes blockchains.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 20000,
      chains: ['solana', 'ethereum', 'base', 'arbitrum'],
      tokenTypes: ['bridged', 'native_multi_chain'],
      customFilters: { requireMultiChainPresence: true },
    },
    phaseConfig: {
      allowedPhases: ['GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'MATURE',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0, GROWTH: 0.5, MATURE: 1.0, ESTABLISHED: 0.9, LEGACY: 0.8 },
    },
    entrySignal: {
      type: 'CROSS_CHAIN_ARB',
      conditions: ['price_differential_detected', 'differential_above_fees', 'bridge_liquidity_available', 'execution_speed_sufficient'],
      minConfidence: 0.7,
      requireConfirmation: false,
      indicators: ['price_diff_bps', 'bridge_fee_bps', 'bridge_liquidity', 'execution_latency'],
      thresholds: { minDiffBps: 50, maxBridgeFeeBps: 30, minBridgeLiquidity: 10000, maxLatencyMs: 5000 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 0.5,
      maxPositionSizePct: 3,
      priorityFee: 5,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -3,
      takeProfitPct: 5,
      timeBasedExitMin: 30,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'arb_closed', 'time_expired'],
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 5,
      maxDailyLossPct: 1,
      maxDrawdownPct: 3,
      maxOpenPositions: 3,
      maxCorrelatedPositions: 1,
      minRiskRewardRatio: 1.2,
      positionTimeoutMin: 30,
    },
    allocationMethod: 'FIXED_FRACTIONAL',
    primaryTimeframe: '1m',
    confirmTimeframes: ['5m'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
      customRequirements: ['cross_chain_price_monitor', 'bridge_fee_estimator'],
    },
  },
  {
    name: 'DEX Depth Analyzer',
    icon: '📊',
    category: 'DEEP_ANALYSIS',
    description:
      'Analiza la profundidad del orderbook en DEXs. Detecta asimetrías en el libro de órdenes que preceden movimientos de precio significativos.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 40000,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 35,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.7, ESTABLISHED: 0.5, LEGACY: 0.3 },
    },
    entrySignal: {
      type: 'DEPTH_ASYMMETRY',
      conditions: ['bid_depth_significantly_higher', 'ask_wall_thinning', 'depth_imbalance_ratio_high', 'price_not_yet_reflected'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['15m', '1h'],
      indicators: ['bid_ask_depth_ratio', 'ask_wall_thickness', 'depth_imbalance_score', 'price_impact_estimate'],
      thresholds: { minBidAskRatio: 2.0, maxAskWallPct: 1, minImbalanceScore: 0.6 },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1,
      maxPositionSizePct: 3,
      limitOffsetPct: 0.3,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 15,
      trailingStopPct: 5,
      trailingActivationPct: 8,
      timeBasedExitMin: 2880,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'depth_normalized', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 50, atProfitPct: 8 }],
      breakevenMovePct: 2,
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 7,
      maxDailyLossPct: 2,
      maxDrawdownPct: 8,
      maxOpenPositions: 5,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 2880,
    },
    allocationMethod: 'VOLATILITY_TARGETING',
    primaryTimeframe: '15m',
    confirmTimeframes: ['1h'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: true,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['dex_orderbook_analyzer', 'depth_imbalance_detector'],
    },
  },
  {
    name: 'Long-Term Accumulation',
    icon: '🏗️',
    category: 'DEEP_ANALYSIS',
    description:
      'Detecta patrones de acumulación institucional a largo plazo. Entra gradualmente cuando identifica que entidades grandes están acumulando de forma discreta.',
    operationType: 'POSITION',
    assetFilter: {
      minLiquidityUsd: 100000,
      minMarketCapUsd: 1000000,
      minHolders: 500,
      chains: ['solana', 'ethereum'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 20,
    },
    phaseConfig: {
      allowedPhases: ['GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'ESTABLISHED',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0, GROWTH: 0.4, MATURE: 0.7, ESTABLISHED: 1.0, LEGACY: 0.8 },
    },
    entrySignal: {
      type: 'INSTITUTIONAL_ACCUMULATION',
      conditions: ['institutional_wallet_accumulating', 'accumulation_pattern_DCA', 'price_stable_despite_buying', 'volume_hidden'],
      minConfidence: 0.7,
      requireConfirmation: true,
      confirmationTimeframes: ['1d', '1w'],
      indicators: ['institutional_flow', 'DCA_pattern_detector', 'price_impact_vs_volume', 'dark_volume_ratio'],
      thresholds: { minInstFlowUsd: 50000, minDCApatternScore: 0.6, maxPriceImpactPct: 1 },
    },
    executionConfig: {
      orderType: 'TWAP',
      slippageTolerancePct: 0.5,
      maxPositionSizePct: 5,
      dcaLevels: [0.2, 0.3, 0.3, 0.2],
      dcaIntervals: [1440, 2880, 4320, 5760],
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 30,
      trailingStopPct: 12,
      trailingActivationPct: 15,
      timeBasedExitMin: 86400,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'institutional_distribution', 'time_expired'],
      partialExitLevels: [
        { pctOfPosition: 25, atProfitPct: 15 },
        { pctOfPosition: 25, atProfitPct: 25 },
      ],
      breakevenMovePct: 4,
    },
    riskParams: {
      maxPositionSizePct: 5,
      maxPortfolioRiskPct: 8,
      maxDailyLossPct: 1.5,
      maxDrawdownPct: 8,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2.5,
      positionTimeoutMin: 86400,
    },
    allocationMethod: 'RISK_PARITY',
    primaryTimeframe: '1d',
    confirmTimeframes: ['1w'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: true,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
      customRequirements: ['institutional_flow_tracker', 'DCA_pattern_analyzer'],
    },
  },

  // ─── MICRO_STRUCTURE (4) ────────────────────────────────────
  {
    name: 'Mempool Sniper',
    icon: '📡',
    category: 'MICRO_STRUCTURE',
    description:
      'Lee transacciones pendientes en el mempool para anticipar movimientos de precio. Ejecuta antes de que las transacciones se confirmen, con máxima velocidad.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 20000,
      chains: ['ethereum', 'solana'],
      tokenTypes: ['any'],
      customFilters: { requireMempoolAccess: true },
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH', 'MATURE'],
      preferredPhase: 'EARLY',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.6, EARLY: 1.0, GROWTH: 0.5, MATURE: 0.3, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'MEMPOOL_PREDICT',
      conditions: ['large_pending_tx_detected', 'tx_will_impact_price', 'front_run_opportunity', 'sufficient_gas_priority'],
      minConfidence: 0.5,
      requireConfirmation: false,
      indicators: ['mempool_scanner', 'pending_tx_size', 'price_impact_estimate', 'gas_priority'],
      thresholds: { minPendingTxUsd: 5000, minPriceImpactBps: 10, minGasPriority: 2 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 3,
      maxPositionSizePct: 2,
      priorityFee: 10,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -5,
      takeProfitPct: 15,
      timeBasedExitMin: 10,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'mempool_opportunity_gone', 'time_expired'],
    },
    riskParams: {
      maxPositionSizePct: 2,
      maxPortfolioRiskPct: 5,
      maxDailyLossPct: 3,
      maxDrawdownPct: 8,
      maxOpenPositions: 3,
      maxCorrelatedPositions: 1,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 10,
    },
    allocationMethod: 'FIXED_FRACTIONAL',
    primaryTimeframe: '1m',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
      customRequirements: ['mempool_monitor', 'pending_tx_analyzer'],
    },
  },
  {
    name: 'Order Flow Imbalance',
    icon: '⚖️',
    category: 'MICRO_STRUCTURE',
    description:
      'Detecta desequilibrios entre compra y venta en el flujo de órdenes. Cuando la presión compradora supera significativamente la vendedora, entra long.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 30000,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.3, EARLY: 0.7, GROWTH: 1.0, MATURE: 0.6, ESTABLISHED: 0.4, LEGACY: 0.2 },
    },
    entrySignal: {
      type: 'FLOW_IMBALANCE',
      conditions: ['buy_flow_3x_sell', 'aggressive_limit_bids', 'sell_pressure_absorbed', 'flow_sustained'],
      minConfidence: 0.55,
      requireConfirmation: false,
      indicators: ['buy_sell_flow_ratio', 'limit_bid_aggression', 'sell_absorption', 'flow_duration'],
      thresholds: { minFlowRatio: 3, minDurationSec: 30, minAbsorptionPct: 70 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 2,
      maxPositionSizePct: 3,
      priorityFee: 3,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -8,
      takeProfitPct: 20,
      trailingStopPct: 6,
      trailingActivationPct: 8,
      timeBasedExitMin: 60,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'flow_balanced', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 50, atProfitPct: 10 }],
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 7,
      maxDailyLossPct: 3,
      maxDrawdownPct: 10,
      maxOpenPositions: 4,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 60,
    },
    allocationMethod: 'KELLY_MODIFIED',
    primaryTimeframe: '1m',
    confirmTimeframes: ['5m'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: true,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
      customRequirements: ['order_flow_monitor', 'flow_imbalance_calculator'],
    },
  },
  {
    name: 'Gas Fee Predictor',
    icon: '⛽',
    category: 'MICRO_STRUCTURE',
    description:
      'Optimiza el momento de entrada según las tarifas de gas. Espera a que las fees sean bajas para ejecutar, maximizando el rendimiento neto.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 25000,
      chains: ['ethereum', 'base'],
      tokenTypes: ['any'],
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.3, EARLY: 0.6, GROWTH: 1.0, MATURE: 0.7, ESTABLISHED: 0.5, LEGACY: 0.3 },
    },
    entrySignal: {
      type: 'GAS_OPTIMIZED_ENTRY',
      conditions: ['gas_fee_below_threshold', 'signal_active', 'gas_trend_declining', 'execution_profitable_after_fees'],
      minConfidence: 0.55,
      requireConfirmation: false,
      indicators: ['current_gas_gwei', 'gas_trend', 'signal_strength', 'net_profit_after_gas'],
      thresholds: { maxGasGwei: 20, minNetProfitPct: 5, gasDeclineRequired: true },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1.5,
      maxPositionSizePct: 3,
      limitOffsetPct: 0.3,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: -10,
      takeProfitPct: 25,
      trailingStopPct: 8,
      trailingActivationPct: 10,
      timeBasedExitMin: 240,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'trailing_stop_hit', 'gas_spike_exit', 'time_expired'],
      partialExitLevels: [{ pctOfPosition: 50, atProfitPct: 12 }],
    },
    riskParams: {
      maxPositionSizePct: 3,
      maxPortfolioRiskPct: 7,
      maxDailyLossPct: 3,
      maxDrawdownPct: 10,
      maxOpenPositions: 5,
      maxCorrelatedPositions: 2,
      minRiskRewardRatio: 2,
      positionTimeoutMin: 240,
    },
    allocationMethod: 'SCORE_BASED',
    primaryTimeframe: '5m',
    confirmTimeframes: ['15m'],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
      customRequirements: ['gas_fee_predictor', 'network_congestion_monitor'],
    },
  },
  {
    name: 'Block Timing',
    icon: '⏱️',
    category: 'MICRO_STRUCTURE',
    description:
      'Opera basándose en el timing de bloques. Analiza patrones de producción de bloques y ejecuta en momentos óptimos para maximizar probabilidad de inclusión.',
    operationType: 'SCALP',
    assetFilter: {
      minLiquidityUsd: 20000,
      chains: ['solana', 'ethereum'],
      tokenTypes: ['any'],
    },
    phaseConfig: {
      allowedPhases: ['LAUNCH', 'EARLY', 'GROWTH', 'MATURE'],
      preferredPhase: 'EARLY',
      phaseWeight: { GENESIS: 0, LAUNCH: 0.5, EARLY: 1.0, GROWTH: 0.6, MATURE: 0.3, ESTABLISHED: 0, LEGACY: 0 },
    },
    entrySignal: {
      type: 'BLOCK_TIMING_ENTRY',
      conditions: ['optimal_block_window', 'validator_schedule_known', 'slot_timing_favorable', 'signal_present'],
      minConfidence: 0.5,
      requireConfirmation: false,
      indicators: ['block_production_schedule', 'validator_rotation', 'slot_timing', 'inclusion_probability'],
      thresholds: { minInclusionProbability: 0.8, maxDelayMs: 200, optimalWindowSec: 2 },
    },
    executionConfig: {
      orderType: 'MARKET',
      slippageTolerancePct: 2,
      maxPositionSizePct: 2,
      priorityFee: 5,
      timeInForce: 'IOC',
    },
    exitSignal: {
      stopLossPct: -5,
      takeProfitPct: 10,
      timeBasedExitMin: 5,
      exitConditions: ['stop_loss_hit', 'take_profit_hit', 'block_window_closed', 'time_expired'],
    },
    riskParams: {
      maxPositionSizePct: 2,
      maxPortfolioRiskPct: 4,
      maxDailyLossPct: 2,
      maxDrawdownPct: 6,
      maxOpenPositions: 3,
      maxCorrelatedPositions: 1,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 5,
    },
    allocationMethod: 'FIXED_AMOUNT',
    primaryTimeframe: '1m',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: false,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: false,
      requiresMeanReversionZones: false,
      customRequirements: ['block_timing_analyzer', 'validator_schedule_tracker'],
    },
  },

  // ─── ADAPTIVE (4) ───────────────────────────────────────────
  {
    name: 'Regime Switcher',
    icon: '🌊',
    category: 'ADAPTIVE',
    description:
      'Cambia automáticamente entre estrategias según el régimen de mercado detectado. En bull usa momentum, en bear usa defensiva, en sideways usa mean reversion.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 50000,
      minMarketCapUsd: 200000,
      minHolders: 100,
      chains: ['solana', 'ethereum', 'base', 'arbitrum'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 35,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.6, LEGACY: 0.4 },
    },
    entrySignal: {
      type: 'REGIME_ADAPTIVE',
      conditions: ['regime_detected', 'strategy_selected_for_regime', 'entry_conditions_met_for_regime'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1h', '4h'],
      indicators: ['regime_detector', 'strategy_selector', 'regime_confidence'],
      thresholds: { minRegimeConfidence: 0.6, strategySwitchCooldown: 4 },
      customParams: {
        BULL: { strategy: 'Momentum Breakout', slPct: -12, tpPct: 30 },
        BEAR: { strategy: 'Mean Reversion', slPct: -8, tpPct: 15 },
        SIDEWAYS: { strategy: 'Range Breakout', slPct: -6, tpPct: 12 },
        TRANSITION: { strategy: 'Capital Preserver', slPct: -5, tpPct: 8 },
      },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1.5,
      maxPositionSizePct: 0, // Dynamic
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: 0, // Dynamic per regime
      takeProfitPct: 0, // Dynamic per regime
      timeBasedExitMin: 0, // Dynamic
      exitConditions: ['regime_stop_hit', 'regime_tp_hit', 'regime_changed', 'time_expired'],
      customParams: { trailingStopPct: 10, trailingActivationPct: 8 },
    },
    riskParams: {
      maxPositionSizePct: 0, // Dynamic
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 12,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 0, // Dynamic
    },
    allocationMethod: 'REGIME_BASED',
    primaryTimeframe: '1h',
    confirmTimeframes: ['4h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: true,
    },
  },
  {
    name: 'Volatility Adapter',
    icon: '🎢',
    category: 'ADAPTIVE',
    description:
      'Ajusta todos los parámetros según la volatilidad actual. En alta volatilidad reduce tamaño y amplía stops; en baja volatilidad aumenta tamaño y estrecha stops.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 40000,
      minMarketCapUsd: 200000,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 35,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.6, LEGACY: 0.4 },
    },
    entrySignal: {
      type: 'VOLATILITY_ADJUSTED',
      conditions: ['volatility_measured', 'parameters_adjusted', 'entry_signal_present', 'risk_budget_available'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1h'],
      indicators: ['current_volatility', 'volatility_percentile', 'atr_normalized', 'risk_budget'],
      thresholds: { volatilityLookback: 20, sizeScaleLowVol: 1.5, sizeScaleHighVol: 0.5 },
      customParams: {
        LOW_VOL: { slMultiplier: 0.8, tpMultiplier: 1.2, sizeMultiplier: 1.5 },
        NORMAL_VOL: { slMultiplier: 1.0, tpMultiplier: 1.0, sizeMultiplier: 1.0 },
        HIGH_VOL: { slMultiplier: 1.3, tpMultiplier: 1.5, sizeMultiplier: 0.6 },
        EXTREME_VOL: { slMultiplier: 1.5, tpMultiplier: 2.0, sizeMultiplier: 0.3 },
      },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 0, // Dynamic
      maxPositionSizePct: 0, // Dynamic
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: 0, // Dynamic
      takeProfitPct: 0, // Dynamic
      trailingStopPct: 0, // Dynamic
      trailingActivationPct: 0, // Dynamic
      timeBasedExitMin: 0, // Dynamic
      exitConditions: ['adjusted_sl_hit', 'adjusted_tp_hit', 'adjusted_trailing_hit', 'volatility_regime_changed', 'time_expired'],
    },
    riskParams: {
      maxPositionSizePct: 0, // Dynamic
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 12,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 0, // Dynamic
    },
    allocationMethod: 'VOLATILITY_TARGETING',
    primaryTimeframe: '1h',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
    },
  },
  {
    name: 'Multi-Strategy Fusion',
    icon: '🔀',
    category: 'ADAPTIVE',
    description:
      'Combina múltiples sistemas de trading simultáneamente. Distribuye capital entre estrategias según su rendimiento actual y correlación.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 40000,
      minMarketCapUsd: 200000,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 30,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.6, LEGACY: 0.4 },
    },
    entrySignal: {
      type: 'MULTI_STRATEGY_CONSENSUS',
      conditions: ['multiple_strategies_signal', 'consensus_threshold_met', 'low_strategy_correlation', 'capital_available'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1h', '4h'],
      indicators: ['strategy_signals', 'consensus_score', 'strategy_correlation_matrix', 'capital_allocation'],
      thresholds: { minConsensusPct: 60, maxCorrelation: 0.5, minStrategies: 2 },
      customParams: {
        strategies: ['Momentum Breakout', 'Whale Tail', 'Mean Reversion'],
        weights: [0.4, 0.35, 0.25],
      },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 1.5,
      maxPositionSizePct: 0, // Dynamic
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: 0, // Dynamic
      takeProfitPct: 0, // Dynamic
      trailingStopPct: 0, // Dynamic
      trailingActivationPct: 0, // Dynamic
      timeBasedExitMin: 0, // Dynamic
      exitConditions: ['consensus_exit', 'strategy_divergence', 'correlation_spike', 'time_expired'],
    },
    riskParams: {
      maxPositionSizePct: 0, // Dynamic
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 12,
      maxOpenPositions: 8,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 1.5,
      positionTimeoutMin: 0, // Dynamic
    },
    allocationMethod: 'META_ALLOCATION',
    primaryTimeframe: '1h',
    confirmTimeframes: ['4h'],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: true,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: true,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: true,
    },
  },
  {
    name: 'Self-Optimizer',
    icon: '🧬',
    category: 'ADAPTIVE',
    description:
      'Optimiza sus propios parámetros basándose en el rendimiento histórico. Ajusta stops, targets, y tamaños automáticamente usando walk-forward optimization.',
    operationType: 'SWING',
    assetFilter: {
      minLiquidityUsd: 40000,
      minMarketCapUsd: 200000,
      chains: ['solana', 'ethereum', 'base'],
      tokenTypes: ['any'],
      excludeRugScoreAbove: 30,
    },
    phaseConfig: {
      allowedPhases: ['EARLY', 'GROWTH', 'MATURE', 'ESTABLISHED', 'LEGACY'],
      preferredPhase: 'GROWTH',
      phaseWeight: { GENESIS: 0, LAUNCH: 0, EARLY: 0.5, GROWTH: 1.0, MATURE: 0.8, ESTABLISHED: 0.6, LEGACY: 0.4 },
    },
    entrySignal: {
      type: 'SELF_OPTIMIZED',
      conditions: ['optimized_parameters_active', 'walk_forward_validated', 'no_overfitting_detected', 'signal_present'],
      minConfidence: 0.6,
      requireConfirmation: true,
      confirmationTimeframes: ['1h'],
      indicators: ['parameter_optimization_score', 'walk_forward_result', 'overfitting_detector', 'current_signal'],
      thresholds: { minWalkForwardSharpe: 0.8, maxOverfittingScore: 0.3, optimizationWindow: 30 },
      customParams: {
        optimizationFrequency: 'daily',
        parameterSpace: { slPct: [3, 15], tpPct: [5, 50], trailingPct: [3, 20] },
        validationMethod: 'walk_forward',
      },
    },
    executionConfig: {
      orderType: 'LIMIT',
      slippageTolerancePct: 0, // Optimized
      maxPositionSizePct: 0, // Optimized
      limitOffsetPct: 0.5,
      timeInForce: 'GTC',
    },
    exitSignal: {
      stopLossPct: 0, // Optimized
      takeProfitPct: 0, // Optimized
      trailingStopPct: 0, // Optimized
      trailingActivationPct: 0, // Optimized
      timeBasedExitMin: 0, // Optimized
      exitConditions: ['optimized_sl_hit', 'optimized_tp_hit', 'optimized_trailing_hit', 'parameter_update_needed', 'time_expired'],
    },
    riskParams: {
      maxPositionSizePct: 0, // Optimized
      maxPortfolioRiskPct: 10,
      maxDailyLossPct: 3,
      maxDrawdownPct: 12,
      maxOpenPositions: 6,
      maxCorrelatedPositions: 3,
      minRiskRewardRatio: 0, // Optimized
      positionTimeoutMin: 0, // Optimized
    },
    allocationMethod: 'ADAPTIVE',
    primaryTimeframe: '1h',
    confirmTimeframes: [],
    bigDataContext: {
      requiresRegimeDetection: true,
      requiresWhaleForecast: false,
      requiresBotSwarmDetection: false,
      requiresLiquidityDrain: false,
      requiresSmartMoneyPositioning: false,
      requiresAnomalyDetection: true,
      requiresMeanReversionZones: false,
      customRequirements: ['walk_forward_optimizer', 'overfitting_detector', 'parameter_evolution_tracker'],
    },
  },
];

// ============================================================
// 4. TOKEN PHASE DETECTION
// ============================================================

const PHASE_THRESHOLDS: Record<TokenPhase, { minMinutes: number; maxMinutes: number; recommendedSL: number; recommendedTP: number; riskLevel: string }> = {
  GENESIS:     { minMinutes: 0,       maxMinutes: 360,     recommendedSL: -12, recommendedTP: 50, riskLevel: 'EXTREME' },
  LAUNCH:      { minMinutes: 360,     maxMinutes: 2880,    recommendedSL: -10, recommendedTP: 40, riskLevel: 'VERY_HIGH' },
  EARLY:       { minMinutes: 2880,    maxMinutes: 20160,   recommendedSL: -8,  recommendedTP: 30, riskLevel: 'HIGH' },
  GROWTH:      { minMinutes: 20160,   maxMinutes: 86400,   recommendedSL: -7,  recommendedTP: 25, riskLevel: 'MEDIUM' },
  MATURE:      { minMinutes: 86400,   maxMinutes: 259200,  recommendedSL: -6,  recommendedTP: 20, riskLevel: 'LOW' },
  ESTABLISHED: { minMinutes: 259200,  maxMinutes: 525600,  recommendedSL: -5,  recommendedTP: 15, riskLevel: 'VERY_LOW' },
  LEGACY:      { minMinutes: 525600,  maxMinutes: Infinity, recommendedSL: -4,  recommendedTP: 10, riskLevel: 'MINIMAL' },
};

export function detectTokenPhase(createdAt: Date): {
  phase: TokenPhase;
  ageMinutes: number;
  ageLabel: string;
  recommendedSL: number;
  recommendedTP: number;
  riskLevel: string;
} {
  const now = new Date();
  const ageMs = now.getTime() - createdAt.getTime();
  const ageMinutes = Math.max(0, Math.floor(ageMs / 60000));

  let phase: TokenPhase = 'LEGACY';
  let recommendedSL = -4;
  let recommendedTP = 10;
  let riskLevel = 'MINIMAL';

  for (const [phaseKey, config] of Object.entries(PHASE_THRESHOLDS)) {
    if (ageMinutes >= config.minMinutes && ageMinutes < config.maxMinutes) {
      phase = phaseKey as TokenPhase;
      recommendedSL = config.recommendedSL;
      recommendedTP = config.recommendedTP;
      riskLevel = config.riskLevel;
      break;
    }
  }

  const ageLabel = formatAgeLabel(ageMinutes);

  return { phase, ageMinutes, ageLabel, recommendedSL, recommendedTP, riskLevel };
}

function formatAgeLabel(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  if (minutes < 43200) return `${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`;
  if (minutes < 525600) return `${(minutes / 43200).toFixed(1)}mo`;
  return `${(minutes / 525600).toFixed(1)}yr`;
}

// ============================================================
// 5. TRADING SYSTEM ENGINE CLASS
// ============================================================

export class TradingSystemEngine {
  private templatesByName: Map<string, SystemTemplate>;
  private templatesByCategory: Map<SystemCategory, SystemTemplate[]>;

  constructor() {
    this.templatesByName = new Map();
    this.templatesByCategory = new Map();

    for (const template of SYSTEM_TEMPLATES) {
      this.templatesByName.set(template.name, template);
      const catTemplates = this.templatesByCategory.get(template.category) ?? [];
      catTemplates.push(template);
      this.templatesByCategory.set(template.category, catTemplates);
    }
  }

  /** Retrieve a template by its exact name. */
  getTemplate(name: string): SystemTemplate | undefined {
    return this.templatesByName.get(name);
  }

  /** Get all templates belonging to a category. */
  getTemplatesByCategory(category: SystemCategory): SystemTemplate[] {
    return this.templatesByCategory.get(category) ?? [];
  }

  /** Get all category identifiers. */
  getAllCategories(): SystemCategory[] {
    return Object.keys(SYSTEM_CATEGORIES) as SystemCategory[];
  }

  /** Get the full category info object. */
  getCategoryInfo(category: SystemCategory): CategoryInfo {
    return SYSTEM_CATEGORIES[category];
  }

  /**
   * Create a new system config from a template, optionally overriding fields.
   * Returns a deep-merged copy — the original template is never mutated.
   */
  createSystemFromTemplate(
    templateName: string,
    overrides?: Partial<SystemTemplate>,
  ): SystemTemplate {
    const base = this.templatesByName.get(templateName);
    if (!base) {
      throw new Error(`Template "${templateName}" not found`);
    }

    // Deep merge — shallow for nested objects, but spreads first-level keys
    const merged: SystemTemplate = {
      ...base,
      ...overrides,
      assetFilter: { ...base.assetFilter, ...(overrides?.assetFilter ?? {}) },
      phaseConfig: { ...base.phaseConfig, ...(overrides?.phaseConfig ?? {}) },
      entrySignal: { ...base.entrySignal, ...(overrides?.entrySignal ?? {}) },
      executionConfig: { ...base.executionConfig, ...(overrides?.executionConfig ?? {}) },
      exitSignal: { ...base.exitSignal, ...(overrides?.exitSignal ?? {}) },
      riskParams: { ...base.riskParams, ...(overrides?.riskParams ?? {}) },
      bigDataContext: { ...base.bigDataContext, ...(overrides?.bigDataContext ?? {}) },
    };

    return merged;
  }

  /**
   * Validate a system configuration and return errors and warnings.
   */
  validateSystemConfig(config: SystemTemplate): {
    valid: boolean;
    errors: string[];
    warnings: string[];
  } {
    const errors: string[] = [];
    const warnings: string[] = [];

    // Name check
    if (!config.name || config.name.trim().length === 0) {
      errors.push('System name is required');
    }

    // Category check
    const validCategories = Object.keys(SYSTEM_CATEGORIES) as SystemCategory[];
    if (!validCategories.includes(config.category)) {
      errors.push(`Invalid category: ${config.category}`);
    }

    // Phase config check
    if (config.phaseConfig.allowedPhases.length === 0) {
      errors.push('At least one allowed phase is required');
    }

    // Entry signal check
    if (config.entrySignal.minConfidence < 0 || config.entrySignal.minConfidence > 1) {
      errors.push('Entry signal minConfidence must be between 0 and 1');
    }

    // Exit signal checks
    if (config.exitSignal.stopLossPct > 0) {
      errors.push('Stop loss should be a negative percentage');
    }
    if (config.exitSignal.takeProfitPct < 0) {
      errors.push('Take profit should be a positive percentage');
    }
    if (config.exitSignal.trailingStopPct !== undefined && config.exitSignal.trailingStopPct < 0) {
      errors.push('Trailing stop must be non-negative');
    }
    if (
      config.exitSignal.stopLossPct !== 0 &&
      config.exitSignal.takeProfitPct !== 0 &&
      Math.abs(config.exitSignal.stopLossPct) > config.exitSignal.takeProfitPct
    ) {
      warnings.push('Stop loss magnitude exceeds take profit — negative expected value');
    }

    // Risk params checks
    if (config.riskParams.maxPositionSizePct > 100) {
      errors.push('maxPositionSizePct cannot exceed 100%');
    }
    if (config.riskParams.maxDailyLossPct > config.riskParams.maxDrawdownPct) {
      warnings.push('maxDailyLossPct exceeds maxDrawdownPct — drawdown limit may be breached in one day');
    }
    if (config.riskParams.maxOpenPositions < 1) {
      errors.push('maxOpenPositions must be at least 1');
    }

    // Execution checks
    if (config.executionConfig.slippageTolerancePct > 10) {
      warnings.push('Slippage tolerance above 10% is very high — consider reducing');
    }
    if (config.executionConfig.maxPositionSizePct > 20) {
      warnings.push('Single position above 20% of portfolio — high concentration risk');
    }

    // Timeframe consistency
    const validTimeframes: Timeframe[] = ['1m', '5m', '15m', '1h', '4h', '1d', '1w'];
    if (!validTimeframes.includes(config.primaryTimeframe)) {
      errors.push(`Invalid primary timeframe: ${config.primaryTimeframe}`);
    }
    for (const tf of config.confirmTimeframes) {
      if (!validTimeframes.includes(tf)) {
        errors.push(`Invalid confirm timeframe: ${tf}`);
      }
    }

    // Big Data requirements consistency
    if (
      config.category === 'ALPHA_HUNTER' &&
      !config.bigDataContext.requiresBotSwarmDetection
    ) {
      warnings.push('ALPHA_HUNTER category should typically enable bot swarm detection');
    }
    if (
      config.category === 'ADAPTIVE' &&
      !config.bigDataContext.requiresRegimeDetection
    ) {
      warnings.push('ADAPTIVE category should typically enable regime detection');
    }

    return {
      valid: errors.length === 0,
      errors,
      warnings,
    };
  }

  /**
   * Get recommended systems for a given token phase, market regime, and risk tolerance.
   */
  getRecommendedSystems(
    phase: TokenPhase,
    regime: string,
    riskTolerance: string,
  ): SystemTemplate[] {
    const allTemplates = [...SYSTEM_TEMPLATES];

    // Filter by phase compatibility
    const phaseFiltered = allTemplates.filter((t) =>
      t.phaseConfig.allowedPhases.includes(phase),
    );

    // Score each template based on phase preference, regime, and risk
    const scored = phaseFiltered.map((template) => {
      let score = 0;

      // Phase preference score (0-30)
      const phaseWeight = template.phaseConfig.phaseWeight?.[phase] ?? 0;
      if (template.phaseConfig.preferredPhase === phase) {
        score += 30;
      } else {
        score += phaseWeight * 25;
      }

      // Regime alignment score (0-30)
      const regimeScore = this.calculateRegimeScore(template, regime);
      score += regimeScore;

      // Risk tolerance alignment score (0-40)
      const riskScore = this.calculateRiskScore(template, riskTolerance);
      score += riskScore;

      return { template, score };
    });

    // Sort by score descending
    scored.sort((a, b) => b.score - a.score);

    // Return top results (at most 10)
    return scored.slice(0, 10).map((s) => s.template);
  }

  private calculateRegimeScore(template: SystemTemplate, regime: string): number {
    if (!template.bigDataContext.requiresRegimeDetection) return 10; // Neutral

    const category = template.category;
    const regimeLower = regime.toLowerCase();

    const regimeMap: Record<string, SystemCategory[]> = {
      bull: ['ALPHA_HUNTER', 'TECHNICAL'],
      bear: ['DEFENSIVE', 'SMART_MONEY'],
      sideways: ['TECHNICAL', 'DEEP_ANALYSIS'],
      transition: ['ADAPTIVE', 'DEFENSIVE'],
      volatile: ['MICRO_STRUCTURE', 'BOT_AWARE'],
    };

    const preferred = regimeMap[regimeLower] ?? [];
    if (preferred.includes(category)) return 30;
    if (category === 'ADAPTIVE') return 20; // Adaptive works in any regime
    return 5;
  }

  private calculateRiskScore(template: SystemTemplate, riskTolerance: string): number {
    const category = template.category;
    const riskMap: Record<string, SystemCategory[]> = {
      extreme: ['ALPHA_HUNTER', 'MICRO_STRUCTURE'],
      high: ['BOT_AWARE', 'SMART_MONEY'],
      medium: ['TECHNICAL', 'DEEP_ANALYSIS', 'ADAPTIVE'],
      low: ['DEFENSIVE'],
    };

    const preferred = riskMap[riskTolerance.toLowerCase()] ?? [];
    if (preferred.includes(category)) return 40;
    // Adjacent risk levels get partial score
    const riskOrder: SystemCategory[] = [
      'ALPHA_HUNTER', 'MICRO_STRUCTURE', 'BOT_AWARE',
      'SMART_MONEY', 'TECHNICAL', 'DEEP_ANALYSIS', 'ADAPTIVE', 'DEFENSIVE',
    ];
    const tolIdx = preferred.length > 0 ? riskOrder.indexOf(preferred[0]) : 4;
    const catIdx = riskOrder.indexOf(category);
    const distance = Math.abs(tolIdx - catIdx);
    return Math.max(0, 40 - distance * 10);
  }

  /**
   * Get detailed info about a token phase.
   */
  getPhaseInfo(phase: TokenPhase): {
    phase: TokenPhase;
    minAge: string;
    maxAge: string;
    recommendedSL: number;
    recommendedTP: number;
    riskLevel: string;
    description: string;
    compatibleSystems: number;
  } {
    const config = PHASE_THRESHOLDS[phase];
    const compatibleSystems = SYSTEM_TEMPLATES.filter((t) =>
      t.phaseConfig.allowedPhases.includes(phase),
    ).length;

    const descriptions: Record<TokenPhase, string> = {
      GENESIS: 'Token recién creado (<6h). Máxima volatilidad y riesgo. Solo para sistemas de sniping y alta frecuencia.',
      LAUNCH: 'Token en lanzamiento (6h-48h). Alta volatilidad, todavía en price discovery. Apropiado para scalping agresivo.',
      EARLY: 'Token en fase temprana (2d-14d). Volatilidad significativa pero patrones emergentes. Buen balance riesgo/oportunidad.',
      GROWTH: 'Token en crecimiento (14d-60d). Tendencia establecida, holders creciendo. Ideal para sistemas de momentum y trend following.',
      MATURE: 'Token maduro (60d-180d). Patrones establecidos, liquidez razonable. Apropiado para análisis técnico clásico.',
      ESTABLISHED: 'Token establecido (180d-1yr). Baja volatilidad relativa, holders estables. Ideal para estrategias defensivas y yield.',
      LEGACY: 'Token legacy (>1yr). Muy baja volatilidad, alta liquidez. Solo estrategias conservadoras y de largo plazo.',
    };

    const minAgeStr = config.minMinutes === 0 ? '0m' : formatAgeLabel(config.minMinutes);
    const maxAgeStr =
      config.maxMinutes === Infinity ? '∞' : formatAgeLabel(config.maxMinutes);

    return {
      phase,
      minAge: minAgeStr,
      maxAge: maxAgeStr,
      recommendedSL: config.recommendedSL,
      recommendedTP: config.recommendedTP,
      riskLevel: config.riskLevel,
      description: descriptions[phase],
      compatibleSystems,
    };
  }

  /** Get all template names. */
  getAllTemplateNames(): string[] {
    return SYSTEM_TEMPLATES.map((t) => t.name);
  }

  /** Get total template count. */
  getTemplateCount(): number {
    return SYSTEM_TEMPLATES.length;
  }

  // ---- Alias methods for API route compatibility ----

  /** Get all templates (no filter). Alias for accessing the full list. */
  getTemplates(category?: SystemCategory): SystemTemplate[] {
    if (category) {
      return this.getTemplatesByCategory(category);
    }
    return [...SYSTEM_TEMPLATES];
  }

  /** Get all category info objects. Alias for getAllCategories with info. */
  getCategories(): CategoryInfo[] {
    return Object.values(SYSTEM_CATEGORIES);
  }

  /** Get templates grouped by category. */
  getTemplatesGroupedByCategory(): Record<SystemCategory, SystemTemplate[]> {
    const grouped: Partial<Record<SystemCategory, SystemTemplate[]>> = {};
    for (const cat of this.getAllCategories()) {
      grouped[cat] = this.getTemplatesByCategory(cat);
    }
    return grouped as Record<SystemCategory, SystemTemplate[]>;
  }
}

// ============================================================
// 6. TYPE ALIASES (for backward compatibility)
// ============================================================

/** @deprecated Use SystemCategory instead */
export type TradingSystemCategory = SystemCategory;

// ============================================================
// 7. SINGLETON EXPORT
// ============================================================

export const tradingSystemEngine = new TradingSystemEngine();
