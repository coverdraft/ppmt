import { describe, it, expect } from 'vitest';
import {
  detectBot,
  batchDetectBots,
  getBotDetectionSummary,
  type TraderMetrics,
} from './bot-detection';

// ============================================================
// Helper factories
// ============================================================

const humanMetrics: TraderMetrics = {
  totalTrades: 45,
  avgTimeBetweenTradesMin: 120,
  consistencyScore: 0.2,
  isActive247: false,
  isActiveAtNight: false,
  avgSlippageBps: 30,
  frontrunCount: 0,
  sandwichCount: 0,
  washTradeScore: 0.01,
  copyTradeScore: 0.05,
  mevExtractionUsd: 0,
  avgHoldTimeMin: 1440, // 1 day
  tradingHourPattern: [5, 3, 1, 0, 0, 2, 8, 15, 20, 25, 30, 28, 22, 18, 15, 12, 10, 8, 6, 4, 3, 5, 7, 6],
  block0EntryCount: 0,
  avgBlockToTrade: 50,
  priorityFeeUsd: 0,
  justInTimeCount: 0,
  multiHopCount: 0,
  sameTokenPairCount: 5,
  selfTradeCount: 0,
};

const mevBotMetrics: TraderMetrics = {
  totalTrades: 500,
  avgTimeBetweenTradesMin: 0.5,
  consistencyScore: 0.95,
  isActive247: true,
  isActiveAtNight: true,
  avgSlippageBps: 5,
  frontrunCount: 150,
  sandwichCount: 80,
  washTradeScore: 0.1,
  copyTradeScore: 0.1,
  mevExtractionUsd: 50000,
  avgHoldTimeMin: 0.1,
  tradingHourPattern: Array(24).fill(20),
  block0EntryCount: 50,
  avgBlockToTrade: 0.5,
  priorityFeeUsd: 500,
  justInTimeCount: 30,
  multiHopCount: 200,
  sameTokenPairCount: 300,
  selfTradeCount: 0,
};

const sniperBotMetrics: TraderMetrics = {
  totalTrades: 200,
  avgTimeBetweenTradesMin: 5,
  consistencyScore: 0.9,
  isActive247: true,
  isActiveAtNight: true,
  avgSlippageBps: 50,
  frontrunCount: 10,
  sandwichCount: 0,
  washTradeScore: 0.05,
  copyTradeScore: 0.1,
  mevExtractionUsd: 0,
  avgHoldTimeMin: 5,
  tradingHourPattern: Array(24).fill(20),
  block0EntryCount: 10,
  avgBlockToTrade: 0.5,
  priorityFeeUsd: 0,
  justInTimeCount: 0,
  multiHopCount: 0,
  sameTokenPairCount: 50,
  selfTradeCount: 0,
};

const washTradingMetrics: TraderMetrics = {
  totalTrades: 500,
  avgTimeBetweenTradesMin: 1,
  consistencyScore: 0.85,
  isActive247: true,
  isActiveAtNight: true,
  avgSlippageBps: 2,
  frontrunCount: 0,
  sandwichCount: 0,
  washTradeScore: 0.8,
  copyTradeScore: 0.4,
  mevExtractionUsd: 0,
  avgHoldTimeMin: 15,
  tradingHourPattern: Array(24).fill(15),
  block0EntryCount: 0,
  avgBlockToTrade: 20,
  priorityFeeUsd: 0,
  justInTimeCount: 0,
  multiHopCount: 0,
  sameTokenPairCount: 80,
  selfTradeCount: 20,
};

const sandwichBotMetrics: TraderMetrics = {
  totalTrades: 300,
  avgTimeBetweenTradesMin: 0.3,
  consistencyScore: 0.85,
  isActive247: true,
  isActiveAtNight: true,
  avgSlippageBps: 200,
  frontrunCount: 25,
  sandwichCount: 30,
  washTradeScore: 0.1,
  copyTradeScore: 0.05,
  mevExtractionUsd: 200,
  avgHoldTimeMin: 1,
  tradingHourPattern: Array(24).fill(20),
  block0EntryCount: 0,
  avgBlockToTrade: 5,
  priorityFeeUsd: 50,
  justInTimeCount: 0,
  multiHopCount: 10,
  sameTokenPairCount: 100,
  selfTradeCount: 0,
};

const arbitrageBotMetrics: TraderMetrics = {
  totalTrades: 500,
  avgTimeBetweenTradesMin: 1,
  consistencyScore: 0.9,
  isActive247: true,
  isActiveAtNight: true,
  avgSlippageBps: 3,
  frontrunCount: 0,
  sandwichCount: 0,
  washTradeScore: 0.05,
  copyTradeScore: 0.05,
  mevExtractionUsd: 0,
  avgHoldTimeMin: 0.3,
  tradingHourPattern: Array(24).fill(20),
  block0EntryCount: 0,
  avgBlockToTrade: 10,
  priorityFeeUsd: 0,
  justInTimeCount: 0,
  multiHopCount: 100,
  sameTokenPairCount: 200,
  selfTradeCount: 0,
};

const copyBotMetrics: TraderMetrics = {
  totalTrades: 500,
  avgTimeBetweenTradesMin: 1,
  consistencyScore: 0.9,
  isActive247: true,
  isActiveAtNight: true,
  avgSlippageBps: 5,
  frontrunCount: 8,
  sandwichCount: 6,
  washTradeScore: 0.2,
  copyTradeScore: 0.9,
  mevExtractionUsd: 200,
  avgHoldTimeMin: 15,
  tradingHourPattern: Array(24).fill(20),
  block0EntryCount: 0,
  avgBlockToTrade: 8,
  priorityFeeUsd: 40,
  justInTimeCount: 2,
  multiHopCount: 10,
  sameTokenPairCount: 100,
  selfTradeCount: 0,
};

const jitBotMetrics: TraderMetrics = {
  totalTrades: 300,
  avgTimeBetweenTradesMin: 2,
  consistencyScore: 0.9,
  isActive247: true,
  isActiveAtNight: true,
  avgSlippageBps: 5,
  frontrunCount: 10,
  sandwichCount: 0,
  washTradeScore: 0.1,
  copyTradeScore: 0.1,
  mevExtractionUsd: 500,
  avgHoldTimeMin: 2,
  tradingHourPattern: Array(24).fill(20),
  block0EntryCount: 0,
  avgBlockToTrade: 5,
  priorityFeeUsd: 50,
  justInTimeCount: 20,
  multiHopCount: 5,
  sameTokenPairCount: 80,
  selfTradeCount: 0,
};

// ============================================================
// detectBot - General behavior
// ============================================================

describe('detectBot', () => {
  it('classifies human trader as NOT a bot', () => {
    const result = detectBot(humanMetrics);
    expect(result.isBot).toBe(false);
    expect(result.botType).toBeNull();
  });

  it('classifies MEV bot as a bot', () => {
    const result = detectBot(mevBotMetrics);
    expect(result.isBot).toBe(true);
    expect(result.botType).toBeTruthy();
  });

  it('returns confidence between 0 and 1', () => {
    const humanResult = detectBot(humanMetrics);
    const botResult = detectBot(mevBotMetrics);
    expect(humanResult.confidence).toBeGreaterThanOrEqual(0);
    expect(humanResult.confidence).toBeLessThanOrEqual(1);
    expect(botResult.confidence).toBeGreaterThanOrEqual(0);
    expect(botResult.confidence).toBeLessThanOrEqual(1);
  });

  it('returns 8 signals (one per detector)', () => {
    const result = detectBot(humanMetrics);
    expect(result.signals).toHaveLength(8);
  });

  it('each signal has required properties', () => {
    const result = detectBot(humanMetrics);
    for (const signal of result.signals) {
      expect(signal).toHaveProperty('type');
      expect(signal).toHaveProperty('name');
      expect(signal).toHaveProperty('weight');
      expect(signal).toHaveProperty('value');
      expect(signal).toHaveProperty('description');
      expect(signal).toHaveProperty('evidence');
      expect(signal.weight).toBeGreaterThanOrEqual(0);
      expect(signal.weight).toBeLessThanOrEqual(1);
      expect(signal.value).toBeGreaterThanOrEqual(0);
      expect(signal.value).toBeLessThanOrEqual(1);
    }
  });

  it('returns classification object', () => {
    const result = detectBot(mevBotMetrics);
    expect(result.classification).toBeDefined();
    expect(result.classification.primary).toBeTruthy();
    expect(result.classification.secondary).toBeInstanceOf(Array);
    expect(result.classification.confidence).toBeGreaterThanOrEqual(0);
    expect(result.classification.reasoning).toBeDefined();
  });
});

// ============================================================
// Bot Type Classification
// ============================================================

describe('bot type classification', () => {
  it('detects MEV_EXTRACTOR for MEV bot', () => {
    const result = detectBot(mevBotMetrics);
    expect(result.isBot).toBe(true);
    expect(result.botType).toBe('MEV_EXTRACTOR');
  });

  it('detects SNIPER_BOT for sniper bot', () => {
    const result = detectBot(sniperBotMetrics);
    expect(result.isBot).toBe(true);
    expect(result.botType).toBe('SNIPER_BOT');
  });

  it('detects WASH_TRADING_BOT for wash trader', () => {
    const result = detectBot(washTradingMetrics);
    expect(result.isBot).toBe(true);
    expect(result.botType).toBe('WASH_TRADING_BOT');
  });

  it('detects SANDWICH_BOT for sandwich attacker', () => {
    const result = detectBot(sandwichBotMetrics);
    expect(result.isBot).toBe(true);
    expect(result.botType).toBe('SANDWICH_BOT');
  });

  it('detects ARBITRAGE_BOT for arbitrageur', () => {
    const result = detectBot(arbitrageBotMetrics);
    expect(result.isBot).toBe(true);
    expect(result.botType).toBe('ARBITRAGE_BOT');
  });

  it('detects COPY_BOT for copy trader', () => {
    const result = detectBot(copyBotMetrics);
    expect(result.isBot).toBe(true);
    expect(result.botType).toBe('COPY_BOT');
  });

  it('detects JIT_LP_BOT for JIT liquidity bot', () => {
    const result = detectBot(jitBotMetrics);
    expect(result.isBot).toBe(true);
    expect(result.botType).toBe('JIT_LP_BOT');
  });

  it('classifies human as NOT_BOT', () => {
    const result = detectBot(humanMetrics);
    expect(result.classification.primary).toBe('NOT_BOT');
  });
});

// ============================================================
// Confidence Scoring
// ============================================================

describe('confidence scoring', () => {
  it('gives higher confidence for more obvious bots', () => {
    const humanResult = detectBot(humanMetrics);
    const botResult = detectBot(mevBotMetrics);
    expect(botResult.confidence).toBeGreaterThan(humanResult.confidence);
  });

  it('signals for bots have higher values than for humans', () => {
    const humanResult = detectBot(humanMetrics);
    const botResult = detectBot(mevBotMetrics);
    const humanAvgSignal = humanResult.signals.reduce((sum, s) => sum + s.value, 0) / humanResult.signals.length;
    const botAvgSignal = botResult.signals.reduce((sum, s) => sum + s.value, 0) / botResult.signals.length;
    expect(botAvgSignal).toBeGreaterThan(humanAvgSignal);
  });
});

// ============================================================
// Detection Signal Analysis
// ============================================================

describe('detection signal analysis', () => {
  it('MEV detection signal triggers on high priority fees', () => {
    const result = detectBot(mevBotMetrics);
    const mevSignal = result.signals.find(s => s.type === 'MEV_EXTRACTION');
    expect(mevSignal).toBeDefined();
    expect(mevSignal!.value).toBeGreaterThan(0.3);
    expect(mevSignal!.evidence.length).toBeGreaterThan(0);
  });

  it('sniper detection signal triggers on block-0 entries', () => {
    const result = detectBot(sniperBotMetrics);
    const sniperSignal = result.signals.find(s => s.type === 'SNIPER_BOT');
    expect(sniperSignal).toBeDefined();
    expect(sniperSignal!.value).toBeGreaterThan(0.3);
  });

  it('wash trading signal triggers on high wash trade score', () => {
    const result = detectBot(washTradingMetrics);
    const washSignal = result.signals.find(s => s.type === 'WASH_TRADING');
    expect(washSignal).toBeDefined();
    expect(washSignal!.value).toBeGreaterThan(0.3);
  });

  it('sandwich signal triggers on sandwich count', () => {
    const result = detectBot(sandwichBotMetrics);
    const sandwichSignal = result.signals.find(s => s.type === 'SANDWICH_BOT');
    expect(sandwichSignal).toBeDefined();
    expect(sandwichSignal!.value).toBeGreaterThan(0.3);
  });

  it('24/7 detection signal triggers on continuous activity', () => {
    const result = detectBot(mevBotMetrics);
    const activitySignal = result.signals.find(s => s.type === 'TWENTY_FOUR_SEVEN');
    expect(activitySignal).toBeDefined();
    expect(activitySignal!.value).toBeGreaterThan(0.3);
  });

  it('human trader has low signal values across all detectors', () => {
    const result = detectBot(humanMetrics);
    const significantSignals = result.signals.filter(s => s.value > 0.3);
    expect(significantSignals.length).toBe(0);
  });

  it('signal evidence provides actionable information', () => {
    const result = detectBot(mevBotMetrics);
    const signalsWithEvidence = result.signals.filter(s => s.value > 0 && s.evidence.length > 0);
    expect(signalsWithEvidence.length).toBeGreaterThan(0);
    // Evidence strings should be descriptive
    for (const signal of signalsWithEvidence) {
      for (const e of signal.evidence) {
        expect(e.length).toBeGreaterThan(5);
      }
    }
  });

  it('arbitrage detection triggers on multi-hop swaps', () => {
    const result = detectBot(arbitrageBotMetrics);
    const arbSignal = result.signals.find(s => s.type === 'ARBITRAGE_BOT');
    expect(arbSignal).toBeDefined();
    expect(arbSignal!.value).toBeGreaterThan(0.3);
  });

  it('copy bot detection triggers on copy trade score', () => {
    const result = detectBot(copyBotMetrics);
    const copySignal = result.signals.find(s => s.type === 'COPY_BOT');
    expect(copySignal).toBeDefined();
    expect(copySignal!.value).toBeGreaterThan(0.3);
  });

  it('JIT detection triggers on just-in-time count', () => {
    const result = detectBot(jitBotMetrics);
    const jitSignal = result.signals.find(s => s.type === 'JIT_LP_BOT');
    expect(jitSignal).toBeDefined();
    expect(jitSignal!.value).toBeGreaterThan(0.3);
  });
});

// ============================================================
// batchDetectBots
// ============================================================

describe('batchDetectBots', () => {
  it('processes multiple traders', () => {
    const results = batchDetectBots([humanMetrics, mevBotMetrics]);
    expect(results).toHaveLength(2);
    expect(results[0].isBot).toBe(false);
    expect(results[1].isBot).toBe(true);
  });

  it('returns empty array for empty input', () => {
    const results = batchDetectBots([]);
    expect(results).toEqual([]);
  });
});

// ============================================================
// getBotDetectionSummary
// ============================================================

describe('getBotDetectionSummary', () => {
  it('returns human-readable summary for non-bot', () => {
    const result = detectBot(humanMetrics);
    const summary = getBotDetectionSummary(result);
    expect(summary).toContain('No bot activity');
  });

  it('returns detailed summary for detected bot', () => {
    const result = detectBot(mevBotMetrics);
    const summary = getBotDetectionSummary(result);
    expect(summary).toContain('Detected');
    expect(summary).toContain('confidence');
  });

  it('includes confidence percentage', () => {
    const result = detectBot(mevBotMetrics);
    const summary = getBotDetectionSummary(result);
    expect(summary).toContain('%');
  });
});
