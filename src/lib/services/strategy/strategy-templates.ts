/**
 * Strategy Templates Service - CryptoQuant Terminal
 *
 * 12 pre-built strategy templates covering different trading categories.
 * Each template has a full strategyConfig JSON compatible with the
 * existing TradingSystem / strategy generation pipeline.
 */

import { db } from '@/lib/db';

// ============================================================
// TYPES
// ============================================================

export type TemplateCategory =
  | 'MOMENTUM'
  | 'MEAN_REVERSION'
  | 'BREAKOUT'
  | 'SCALPING'
  | 'SWING'
  | 'ARBITRAGE'
  | 'VOLUME'
  | 'VOLATILITY';

export type TemplateDifficulty = 'BEGINNER' | 'INTERMEDIATE' | 'ADVANCED';

export interface BuiltInTemplate {
  name: string;
  description: string;
  category: TemplateCategory;
  difficulty: TemplateDifficulty;
  author: string;
  tags: string[];
  rating: number;
  isFeatured: boolean;
  strategyConfig: {
    indicators: Array<{ type: string; params: Record<string, number | string> }>;
    entryRules: {
      conditions: string[];
      logic: 'AND' | 'OR';
    };
    exitRules: {
      takeProfitPct: number;
      stopLossPct: number;
      trailingStopPct?: number;
    };
    riskManagement: {
      maxPositionSizePct: number;
      maxOpenPositions: number;
    };
    timeframe: string;
    direction: 'LONG' | 'SHORT' | 'BOTH';
  };
  expectedWinRate: number;
  expectedProfitFactor: number;
  expectedAvgTrades: number;
  expectedMaxDrawdown: number;
  applicableChains: string[];
  applicableTimeframes: string[];
}

// ============================================================
// 12 BUILT-IN TEMPLATES
// ============================================================

const BUILT_IN_TEMPLATES: BuiltInTemplate[] = [
  // ─── 1. MOMENTUM RIDER ──────────────────────────────────────
  {
    name: 'Momentum Rider',
    description:
      'Ride the trend using RSI and EMA crossover signals. Enters when RSI confirms momentum and price crosses above EMA, with trailing stops to lock in profits as the trend extends. Best suited for tokens in Growth or Mature phases with strong volume.',
    category: 'MOMENTUM',
    difficulty: 'BEGINNER',
    author: 'CryptoQuant',
    tags: ['trend-following', 'RSI', 'EMA', 'momentum', 'beginner-friendly'],
    rating: 4.5,
    isFeatured: true,
    strategyConfig: {
      indicators: [
        { type: 'RSI', params: { period: 14 } },
        { type: 'EMA', params: { period: 20 } },
        { type: 'EMA', params: { period: 50 } },
        { type: 'VOLUME', params: { maPeriod: 20 } },
      ],
      entryRules: {
        conditions: ['RSI > 50 AND RSI < 70', 'Price > EMA20', 'EMA20 > EMA50', 'Volume > VolumeMA'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 8,
        stopLossPct: 3,
        trailingStopPct: 2,
      },
      riskManagement: {
        maxPositionSizePct: 8,
        maxOpenPositions: 4,
      },
      timeframe: '4h',
      direction: 'LONG',
    },
    expectedWinRate: 58,
    expectedProfitFactor: 1.8,
    expectedAvgTrades: 12,
    expectedMaxDrawdown: 15,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['1h', '4h', '1d'],
  },

  // ─── 2. MEAN REVERSION ALPHA ────────────────────────────────
  {
    name: 'Mean Reversion Alpha',
    description:
      'Exploit overbought/oversold conditions using Bollinger Bands and RSI. Enters when price touches the lower band with RSI oversold confirmation, targeting a return to the mean. Works best in ranging or sideways markets with established tokens.',
    category: 'MEAN_REVERSION',
    difficulty: 'INTERMEDIATE',
    author: 'CryptoQuant',
    tags: ['mean-reversion', 'bollinger-bands', 'RSI', 'oversold', 'range'],
    rating: 4.2,
    isFeatured: true,
    strategyConfig: {
      indicators: [
        { type: 'RSI', params: { period: 14 } },
        { type: 'BB', params: { period: 20, stdDev: 2 } },
        { type: 'EMA', params: { period: 200 } },
      ],
      entryRules: {
        conditions: ['Price < LowerBB', 'RSI < 30', 'Price > EMA200'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 5,
        stopLossPct: 2.5,
        trailingStopPct: 1.5,
      },
      riskManagement: {
        maxPositionSizePct: 6,
        maxOpenPositions: 5,
      },
      timeframe: '4h',
      direction: 'LONG',
    },
    expectedWinRate: 62,
    expectedProfitFactor: 1.6,
    expectedAvgTrades: 8,
    expectedMaxDrawdown: 10,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['1h', '4h', '1d'],
  },

  // ─── 3. BREAKOUT HUNTER ─────────────────────────────────────
  {
    name: 'Breakout Hunter',
    description:
      'Capture explosive moves from price breakouts with volume confirmation. Identifies consolidation ranges and enters when price breaks out with above-average volume. ATR-based position sizing adapts to current volatility for optimal risk management.',
    category: 'BREAKOUT',
    difficulty: 'INTERMEDIATE',
    author: 'CryptoQuant',
    tags: ['breakout', 'volume', 'ATR', 'consolidation', 'range-break'],
    rating: 4.0,
    isFeatured: true,
    strategyConfig: {
      indicators: [
        { type: 'ATR', params: { period: 14 } },
        { type: 'VOLUME', params: { maPeriod: 20 } },
        { type: 'BB', params: { period: 20, stdDev: 1.5 } },
        { type: 'ADX', params: { period: 14 } },
      ],
      entryRules: {
        conditions: ['Price > UpperBB', 'Volume > 2x VolumeMA', 'ADX > 25'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 10,
        stopLossPct: 3,
        trailingStopPct: 3,
      },
      riskManagement: {
        maxPositionSizePct: 7,
        maxOpenPositions: 3,
      },
      timeframe: '1h',
      direction: 'LONG',
    },
    expectedWinRate: 45,
    expectedProfitFactor: 2.2,
    expectedAvgTrades: 6,
    expectedMaxDrawdown: 18,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['15m', '1h', '4h'],
  },

  // ─── 4. SCALP SNIPER ────────────────────────────────────────
  {
    name: 'Scalp Sniper',
    description:
      'Ultra-fast scalping on 1m/5m timeframes using fast RSI signals with extremely tight stops. Requires low-latency execution and is best suited for high-liquidity pairs. Not for the faint of heart — high frequency, small targets, strict risk control.',
    category: 'SCALPING',
    difficulty: 'ADVANCED',
    author: 'CryptoQuant',
    tags: ['scalping', 'fast-RSI', 'tight-stops', 'high-frequency', '1m'],
    rating: 3.8,
    isFeatured: false,
    strategyConfig: {
      indicators: [
        { type: 'RSI', params: { period: 5 } },
        { type: 'EMA', params: { period: 8 } },
        { type: 'EMA', params: { period: 21 } },
        { type: 'VOLUME', params: { maPeriod: 10 } },
      ],
      entryRules: {
        conditions: ['RSI5 < 20', 'EMA8 > EMA21', 'Volume spike'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 1.5,
        stopLossPct: 0.75,
        trailingStopPct: 0.5,
      },
      riskManagement: {
        maxPositionSizePct: 4,
        maxOpenPositions: 6,
      },
      timeframe: '5m',
      direction: 'LONG',
    },
    expectedWinRate: 55,
    expectedProfitFactor: 1.4,
    expectedAvgTrades: 40,
    expectedMaxDrawdown: 8,
    applicableChains: ['SOL', 'ETH'],
    applicableTimeframes: ['1m', '5m'],
  },

  // ─── 5. SWING TRADER PRO ────────────────────────────────────
  {
    name: 'Swing Trader Pro',
    description:
      'Classic swing trading with MACD and EMA alignment on 4h/daily timeframes. Captures multi-day moves with larger profit targets and relaxed stop losses. Perfect for traders who prefer fewer but higher-conviction trades.',
    category: 'SWING',
    difficulty: 'BEGINNER',
    author: 'CryptoQuant',
    tags: ['swing', 'MACD', 'EMA', 'multi-day', 'trend'],
    rating: 4.6,
    isFeatured: true,
    strategyConfig: {
      indicators: [
        { type: 'MACD', params: { fast: 12, slow: 26, signal: 9 } },
        { type: 'EMA', params: { period: 50 } },
        { type: 'EMA', params: { period: 200 } },
        { type: 'RSI', params: { period: 14 } },
      ],
      entryRules: {
        conditions: ['MACD bullish crossover', 'Price > EMA50 > EMA200', 'RSI > 40 AND RSI < 70'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 15,
        stopLossPct: 5,
        trailingStopPct: 4,
      },
      riskManagement: {
        maxPositionSizePct: 10,
        maxOpenPositions: 3,
      },
      timeframe: '4h',
      direction: 'LONG',
    },
    expectedWinRate: 52,
    expectedProfitFactor: 2.0,
    expectedAvgTrades: 4,
    expectedMaxDrawdown: 12,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['4h', '1d'],
  },

  // ─── 6. VOLATILITY CRUSHER ──────────────────────────────────
  {
    name: 'Volatility Crusher',
    description:
      'ATR-based entries and exits designed to capitalize on volatility squeezes and expansions. Enters when volatility contracts (squeeze) and rides the explosive move that follows. Dynamically adjusts position size based on current ATR readings.',
    category: 'VOLATILITY',
    difficulty: 'INTERMEDIATE',
    author: 'CryptoQuant',
    tags: ['volatility', 'ATR', 'squeeze', 'BB', 'expansion'],
    rating: 4.1,
    isFeatured: false,
    strategyConfig: {
      indicators: [
        { type: 'ATR', params: { period: 14 } },
        { type: 'BB', params: { period: 20, stdDev: 2 } },
        { type: 'BB', params: { period: 20, stdDev: 1 } },
        { type: 'RSI', params: { period: 14 } },
      ],
      entryRules: {
        conditions: ['BB width < recent low (squeeze)', 'Price breaks BB1std', 'RSI confirming direction'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 6,
        stopLossPct: 2 * 1.5, // 1.5x ATR
        trailingStopPct: 2,
      },
      riskManagement: {
        maxPositionSizePct: 6,
        maxOpenPositions: 4,
      },
      timeframe: '1h',
      direction: 'BOTH',
    },
    expectedWinRate: 50,
    expectedProfitFactor: 2.1,
    expectedAvgTrades: 8,
    expectedMaxDrawdown: 14,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['15m', '1h', '4h'],
  },

  // ─── 7. VOLUME PROFILE SCALPER ──────────────────────────────
  {
    name: 'Volume Profile Scalper',
    description:
      'Volume-weighted entries at key price levels where high trading activity has occurred. Identifies Point of Control (POC) and Value Area boundaries for precision entries. Advanced strategy requiring understanding of volume profile concepts.',
    category: 'VOLUME',
    difficulty: 'ADVANCED',
    author: 'CryptoQuant',
    tags: ['volume-profile', 'POC', 'value-area', 'key-levels', 'advanced'],
    rating: 3.9,
    isFeatured: false,
    strategyConfig: {
      indicators: [
        { type: 'VOLUME', params: { maPeriod: 20 } },
        { type: 'VWAP', params: { period: 'session' } },
        { type: 'RSI', params: { period: 14 } },
        { type: 'EMA', params: { period: 20 } },
      ],
      entryRules: {
        conditions: ['Price near POC', 'Volume spike at level', 'RSI diverging', 'VWAP confirming'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 3,
        stopLossPct: 1.5,
        trailingStopPct: 1,
      },
      riskManagement: {
        maxPositionSizePct: 5,
        maxOpenPositions: 5,
      },
      timeframe: '15m',
      direction: 'LONG',
    },
    expectedWinRate: 56,
    expectedProfitFactor: 1.5,
    expectedAvgTrades: 20,
    expectedMaxDrawdown: 10,
    applicableChains: ['SOL', 'ETH'],
    applicableTimeframes: ['5m', '15m', '1h'],
  },

  // ─── 8. TREND FOLLOWING MACHINE ─────────────────────────────
  {
    name: 'Trend Following Machine',
    description:
      'Multi-EMA alignment with ADX trend strength filter. Only enters when multiple moving averages align and ADX confirms a strong trend is in place. Conservative approach that avoids choppy markets and focuses on clean, directional moves.',
    category: 'MOMENTUM',
    difficulty: 'INTERMEDIATE',
    author: 'CryptoQuant',
    tags: ['trend-following', 'multi-EMA', 'ADX', 'alignment', 'filter'],
    rating: 4.3,
    isFeatured: false,
    strategyConfig: {
      indicators: [
        { type: 'EMA', params: { period: 10 } },
        { type: 'EMA', params: { period: 20 } },
        { type: 'EMA', params: { period: 50 } },
        { type: 'EMA', params: { period: 200 } },
        { type: 'ADX', params: { period: 14 } },
      ],
      entryRules: {
        conditions: ['EMA10 > EMA20 > EMA50 > EMA200', 'ADX > 25', 'Pullback to EMA20'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 12,
        stopLossPct: 4,
        trailingStopPct: 3,
      },
      riskManagement: {
        maxPositionSizePct: 8,
        maxOpenPositions: 3,
      },
      timeframe: '4h',
      direction: 'LONG',
    },
    expectedWinRate: 55,
    expectedProfitFactor: 2.3,
    expectedAvgTrades: 5,
    expectedMaxDrawdown: 11,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['1h', '4h', '1d'],
  },

  // ─── 9. RANGE TRADER ────────────────────────────────────────
  {
    name: 'Range Trader',
    description:
      'Identifies support and resistance levels and trades bounces within the range. Buys near support with RSI oversold confirmation, sells near resistance. Simple but effective in sideways markets. Ideal for beginners learning technical analysis.',
    category: 'MEAN_REVERSION',
    difficulty: 'BEGINNER',
    author: 'CryptoQuant',
    tags: ['range', 'support-resistance', 'RSI', 'bounce', 'beginner-friendly'],
    rating: 4.4,
    isFeatured: false,
    strategyConfig: {
      indicators: [
        { type: 'RSI', params: { period: 14 } },
        { type: 'BB', params: { period: 20, stdDev: 2 } },
        { type: 'EMA', params: { period: 50 } },
      ],
      entryRules: {
        conditions: ['Price near support level', 'RSI < 35', 'Price touching lower BB'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 4,
        stopLossPct: 2,
        trailingStopPct: 1,
      },
      riskManagement: {
        maxPositionSizePct: 7,
        maxOpenPositions: 5,
      },
      timeframe: '1h',
      direction: 'LONG',
    },
    expectedWinRate: 60,
    expectedProfitFactor: 1.5,
    expectedAvgTrades: 15,
    expectedMaxDrawdown: 8,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['15m', '1h', '4h'],
  },

  // ─── 10. MOMENTUM DIVERGENCE ────────────────────────────────
  {
    name: 'Momentum Divergence',
    description:
      'Detects RSI and MACD divergences to anticipate trend reversals before they happen. Bullish divergence signals potential upside reversal, bearish divergence signals potential downside. Requires patience but offers excellent risk/reward ratios.',
    category: 'MOMENTUM',
    difficulty: 'ADVANCED',
    author: 'CryptoQuant',
    tags: ['divergence', 'RSI', 'MACD', 'reversal', 'advanced'],
    rating: 4.0,
    isFeatured: false,
    strategyConfig: {
      indicators: [
        { type: 'RSI', params: { period: 14 } },
        { type: 'MACD', params: { fast: 12, slow: 26, signal: 9 } },
        { type: 'EMA', params: { period: 200 } },
        { type: 'ATR', params: { period: 14 } },
      ],
      entryRules: {
        conditions: ['Bullish RSI divergence', 'MACD histogram turning up', 'Price above EMA200'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 10,
        stopLossPct: 3,
        trailingStopPct: 2.5,
      },
      riskManagement: {
        maxPositionSizePct: 6,
        maxOpenPositions: 3,
      },
      timeframe: '4h',
      direction: 'LONG',
    },
    expectedWinRate: 48,
    expectedProfitFactor: 2.5,
    expectedAvgTrades: 3,
    expectedMaxDrawdown: 12,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['1h', '4h', '1d'],
  },

  // ─── 11. LIQUIDITY SWEEP ────────────────────────────────────
  {
    name: 'Liquidity Sweep',
    description:
      'Detects liquidity grabs below key support levels and trades the subsequent reversal. When price sweeps below support to hit stop losses then quickly reverses, this strategy enters in the direction of the reversal. Advanced concept requiring understanding of market structure.',
    category: 'VOLUME',
    difficulty: 'ADVANCED',
    author: 'CryptoQuant',
    tags: ['liquidity-sweep', 'stop-hunt', 'reversal', 'smart-money', 'market-structure'],
    rating: 3.7,
    isFeatured: false,
    strategyConfig: {
      indicators: [
        { type: 'VOLUME', params: { maPeriod: 20 } },
        { type: 'RSI', params: { period: 14 } },
        { type: 'EMA', params: { period: 50 } },
        { type: 'ATR', params: { period: 14 } },
      ],
      entryRules: {
        conditions: ['Price swept below support', 'Quick reversal candle', 'Volume spike on sweep', 'RSI oversold then recovery'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 7,
        stopLossPct: 2.5,
        trailingStopPct: 2,
      },
      riskManagement: {
        maxPositionSizePct: 5,
        maxOpenPositions: 3,
      },
      timeframe: '1h',
      direction: 'LONG',
    },
    expectedWinRate: 46,
    expectedProfitFactor: 2.4,
    expectedAvgTrades: 5,
    expectedMaxDrawdown: 16,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['15m', '1h', '4h'],
  },

  // ─── 12. MULTI-TF CONFLUENCE ────────────────────────────────
  {
    name: 'Multi-TF Confluence',
    description:
      'Aligns signals across 1h, 4h, and daily timeframes for high-probability entries. Only takes a trade when the direction is confirmed on all three timeframes, significantly reducing false signals. Lower trade frequency but higher win rate.',
    category: 'SWING',
    difficulty: 'INTERMEDIATE',
    author: 'CryptoQuant',
    tags: ['multi-timeframe', 'confluence', '1h-4h-1d', 'high-probability', 'swing'],
    rating: 4.7,
    isFeatured: false,
    strategyConfig: {
      indicators: [
        { type: 'EMA', params: { period: 20 } },
        { type: 'EMA', params: { period: 50 } },
        { type: 'RSI', params: { period: 14 } },
        { type: 'MACD', params: { fast: 12, slow: 26, signal: 9 } },
        { type: 'ADX', params: { period: 14 } },
      ],
      entryRules: {
        conditions: ['1h: EMA20 > EMA50', '4h: MACD bullish', 'Daily: Price > EMA50', 'ADX > 20'],
        logic: 'AND',
      },
      exitRules: {
        takeProfitPct: 12,
        stopLossPct: 4,
        trailingStopPct: 3,
      },
      riskManagement: {
        maxPositionSizePct: 8,
        maxOpenPositions: 3,
      },
      timeframe: '4h',
      direction: 'LONG',
    },
    expectedWinRate: 64,
    expectedProfitFactor: 2.0,
    expectedAvgTrades: 3,
    expectedMaxDrawdown: 9,
    applicableChains: ['SOL', 'ETH', 'BASE'],
    applicableTimeframes: ['1h', '4h', '1d'],
  },
];

// ============================================================
// PUBLIC API
// ============================================================

/**
 * Returns all built-in template definitions
 */
export function getBuiltInTemplates(): BuiltInTemplate[] {
  return BUILT_IN_TEMPLATES;
}

/**
 * Returns a specific built-in template by name
 */
export function getBuiltInTemplateByName(name: string): BuiltInTemplate | undefined {
  return BUILT_IN_TEMPLATES.find((t) => t.name === name);
}

/**
 * Seeds the database with built-in templates if they don't exist yet.
 * Uses the template name as a unique key to avoid duplicates.
 */
export async function seedTemplates(): Promise<{ seeded: number; skipped: number }> {
  let seeded = 0;
  let skipped = 0;

  for (const template of BUILT_IN_TEMPLATES) {
    const existing = await db.strategyTemplate.findFirst({
      where: { name: template.name, isBuiltIn: true },
    });

    if (existing) {
      skipped++;
      continue;
    }

    await db.strategyTemplate.create({
      data: {
        name: template.name,
        description: template.description,
        category: template.category,
        difficulty: template.difficulty,
        author: template.author,
        tags: JSON.stringify(template.tags),
        rating: template.rating,
        isFeatured: template.isFeatured,
        isBuiltIn: true,
        strategyConfig: JSON.stringify(template.strategyConfig),
        expectedWinRate: template.expectedWinRate,
        expectedProfitFactor: template.expectedProfitFactor,
        expectedAvgTrades: template.expectedAvgTrades,
        expectedMaxDrawdown: template.expectedMaxDrawdown,
        applicableChains: JSON.stringify(template.applicableChains),
        applicableTimeframes: JSON.stringify(template.applicableTimeframes),
      },
    });

    seeded++;
  }

  return { seeded, skipped };
}

/**
 * Category metadata for the UI
 */
export const CATEGORY_META: Record<
  TemplateCategory,
  { label: string; icon: string; color: string; gradient: string; bg: string }
> = {
  MOMENTUM: {
    label: 'Momentum',
    icon: '🚀',
    color: 'text-orange-400',
    gradient: 'from-orange-500/20 to-red-500/20',
    bg: 'bg-orange-500/15',
  },
  MEAN_REVERSION: {
    label: 'Mean Reversion',
    icon: '🔄',
    color: 'text-cyan-400',
    gradient: 'from-cyan-500/20 to-teal-500/20',
    bg: 'bg-cyan-500/15',
  },
  BREAKOUT: {
    label: 'Breakout',
    icon: '💥',
    color: 'text-yellow-400',
    gradient: 'from-yellow-500/20 to-amber-500/20',
    bg: 'bg-yellow-500/15',
  },
  SCALPING: {
    label: 'Scalping',
    icon: '⚡',
    color: 'text-pink-400',
    gradient: 'from-pink-500/20 to-rose-500/20',
    bg: 'bg-pink-500/15',
  },
  SWING: {
    label: 'Swing',
    icon: '🏄',
    color: 'text-emerald-400',
    gradient: 'from-emerald-500/20 to-green-500/20',
    bg: 'bg-emerald-500/15',
  },
  ARBITRAGE: {
    label: 'Arbitrage',
    icon: '⚖️',
    color: 'text-violet-400',
    gradient: 'from-violet-500/20 to-purple-500/20',
    bg: 'bg-violet-500/15',
  },
  VOLUME: {
    label: 'Volume',
    icon: '📊',
    color: 'text-blue-400',
    gradient: 'from-blue-500/20 to-sky-500/20',
    bg: 'bg-blue-500/15',
  },
  VOLATILITY: {
    label: 'Volatility',
    icon: '🌪️',
    color: 'text-amber-400',
    gradient: 'from-amber-500/20 to-orange-500/20',
    bg: 'bg-amber-500/15',
  },
};

/**
 * Difficulty metadata for the UI
 */
export const DIFFICULTY_META: Record<
  TemplateDifficulty,
  { label: string; color: string; bg: string }
> = {
  BEGINNER: { label: 'Beginner', color: 'text-emerald-400', bg: 'bg-emerald-400/10' },
  INTERMEDIATE: { label: 'Intermediate', color: 'text-amber-400', bg: 'bg-amber-400/10' },
  ADVANCED: { label: 'Advanced', color: 'text-red-400', bg: 'bg-red-400/10' },
};
