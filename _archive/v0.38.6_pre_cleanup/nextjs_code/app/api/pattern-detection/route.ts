/**
 * Pattern Detection Endpoint - CryptoQuant Terminal
 *
 * Real-time pattern detection from candlestick data and token metrics.
 * Detects trading patterns and stores them as PatternRules in the DB.
 *
 * GET  /api/pattern-detection          → Current detection status
 * POST /api/pattern-detection          → Trigger detection actions
 *   { action: "detect" }               → Scan all tokens with candle data
 *   { action: "token", address: "0x…" }→ Detect patterns for a specific token
 *   { action: "status" }               → Same as GET
 */

import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ============================================================
// MODULE-LEVEL DETECTION STATE
// ============================================================

interface DetectionState {
  isRunning: boolean;
  lastRunAt: Date | null;
  lastRunDurationMs: number;
  totalDetections: number;
  lastError: string | null;
  tokensScanned: number;
  patternsCreated: number;
  patternsUpdated: number;
  recentDetections: Array<{
    name: string;
    category: string;
    tokenAddress: string;
    detectedAt: string;
  }>;
}

const detectionState: DetectionState = {
  isRunning: false,
  lastRunAt: null,
  lastRunDurationMs: 0,
  totalDetections: 0,
  lastError: null,
  tokensScanned: 0,
  patternsCreated: 0,
  patternsUpdated: 0,
  recentDetections: [],
};

const MAX_RECENT_DETECTIONS = 50;

// ============================================================
// TYPES
// ============================================================

type PatternCategory =
  | 'PRICE_ACTION'
  | 'VOLUME'
  | 'SMART_MONEY'
  | 'RISK'
  | 'MOMENTUM'
  | 'LIQUIDITY'
  | 'BOT_ACTIVITY';

interface MetricDetection {
  name: string;
  category: PatternCategory;
  description: string;
  conditions: Record<string, unknown>;
  tokenAddress: string;
  tokenSymbol: string;
  confidence: number; // 0-1
}

// ============================================================
// CANDLESTICK PATTERN ENGINE - TRY IMPORT WITH FALLBACK
// ============================================================

interface CandlestickScanResult {
  tokenAddress: string;
  patterns: Array<{
    pattern: string;
    category: string;
    direction: string;
    timeframe: string;
    confidence: number;
    reliability: number;
    weight: number;
    description: string;
    priceAtDetection: number;
  }>;
  confluences: Array<{
    pattern: string;
    timeframes: string[];
    direction: string;
    combinedWeight: number;
    combinedConfidence: number;
    description: string;
  }>;
  overallSignal: string;
  overallScore: number;
}

let scanTokenFn: ((address: string, chain?: string) => Promise<CandlestickScanResult>) | null = null;
let engineAvailable = false;
let engineInitialized = false;

/** Lazily initialize the candlestick pattern engine on first use */
async function ensureEngineInitialized(): Promise<void> {
  if (engineInitialized) return;
  engineInitialized = true;
  try {
    const engineModule = await import('@/lib/services/brain/candlestick-pattern-engine');
    if (engineModule.candlestickPatternEngine) {
      scanTokenFn = engineModule.candlestickPatternEngine.scanToken.bind(
        engineModule.candlestickPatternEngine
      );
      engineAvailable = true;
    }
  } catch {
    scanTokenFn = null;
    engineAvailable = false;
  }
}

// ============================================================
// FALLBACK SIMPLE CANDLESTICK DETECTION
// ============================================================

interface SimpleCandle {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  timestamp: Date;
}

function detectSimplePatterns(candles: SimpleCandle[]): Array<{
  pattern: string;
  direction: string;
  confidence: number;
  description: string;
}> {
  const results: Array<{
    pattern: string;
    direction: string;
    confidence: number;
    description: string;
  }> = [];

  if (candles.length < 3) return results;

  const n = candles.length;
  const last = candles[n - 1];
  const prev = candles[n - 2];
  const prev2 = candles[n - 3];

  // Helpers
  const bodySize = (o: number, c: number) => Math.abs(c - o);
  const isBullish = (o: number, c: number) => c > o;
  const isBearish = (o: number, c: number) => c < o;
  const totalRange = (h: number, l: number) => h - l;
  const upperWick = (o: number, c: number, h: number) => h - Math.max(o, c);
  const lowerWick = (o: number, c: number, l: number) => Math.min(o, c) - l;

  // Bullish Engulfing
  if (
    isBearish(prev.open, prev.close) &&
    isBullish(last.open, last.close) &&
    last.close > prev.open &&
    last.open < prev.close
  ) {
    results.push({
      pattern: 'BullEngulf',
      direction: 'BULLISH',
      confidence: 0.7,
      description: 'Bullish Engulfing pattern detected',
    });
  }

  // Bearish Engulfing
  if (
    isBullish(prev.open, prev.close) &&
    isBearish(last.open, last.close) &&
    last.close < prev.open &&
    last.open > prev.close
  ) {
    results.push({
      pattern: 'BearEngulf',
      direction: 'BEARISH',
      confidence: 0.7,
      description: 'Bearish Engulfing pattern detected',
    });
  }

  // Hammer
  const lastBody = bodySize(last.open, last.close);
  const lastRange = totalRange(last.high, last.low);
  if (lastRange > 0) {
    const lw = lowerWick(last.open, last.close, last.low);
    const uw = upperWick(last.open, last.close, last.high);
    if (lw >= 2 * lastBody && uw <= lastBody * 0.3) {
      results.push({
        pattern: 'Hammer',
        direction: 'BULLISH',
        confidence: 0.65,
        description: 'Hammer candle pattern detected',
      });
    }
    // Shooting Star
    if (uw >= 2 * lastBody && lw <= lastBody * 0.3) {
      results.push({
        pattern: 'ShootingStar',
        direction: 'BEARISH',
        confidence: 0.67,
        description: 'Shooting Star pattern detected',
      });
    }
  }

  // Morning Star
  if (
    isBearish(prev2.open, prev2.close) &&
    bodySize(prev.open, prev.close) < bodySize(prev2.open, prev2.close) * 0.3 &&
    isBullish(last.open, last.close) &&
    last.close > (prev2.open + prev2.close) / 2
  ) {
    results.push({
      pattern: 'MorningStar',
      direction: 'BULLISH',
      confidence: 0.75,
      description: 'Morning Star pattern detected',
    });
  }

  // Evening Star
  if (
    isBullish(prev2.open, prev2.close) &&
    bodySize(prev.open, prev.close) < bodySize(prev2.open, prev2.close) * 0.3 &&
    isBearish(last.open, last.close) &&
    last.close < (prev2.open + prev2.close) / 2
  ) {
    results.push({
      pattern: 'EveningStar',
      direction: 'BEARISH',
      confidence: 0.75,
      description: 'Evening Star pattern detected',
    });
  }

  // Doji
  if (lastRange > 0 && lastBody / lastRange < 0.1) {
    results.push({
      pattern: 'Doji',
      direction: 'NEUTRAL',
      confidence: 0.45,
      description: 'Doji candle - indecision pattern',
    });
  }

  // Three White Soldiers
  if (
    n >= 3 &&
    isBullish(prev2.open, prev2.close) &&
    isBullish(prev.open, prev.close) &&
    isBullish(last.open, last.close) &&
    prev.close > prev2.close &&
    last.close > prev.close
  ) {
    results.push({
      pattern: 'ThreeWhiteSoldiers',
      direction: 'BULLISH',
      confidence: 0.78,
      description: 'Three White Soldiers - strong bullish continuation',
    });
  }

  // Three Black Crows
  if (
    n >= 3 &&
    isBearish(prev2.open, prev2.close) &&
    isBearish(prev.open, prev.close) &&
    isBearish(last.open, last.close) &&
    prev.close < prev2.close &&
    last.close < prev.close
  ) {
    results.push({
      pattern: 'ThreeBlackCrows',
      direction: 'BEARISH',
      confidence: 0.78,
      description: 'Three Black Crows - strong bearish continuation',
    });
  }

  // Head and Shoulders (simplified: peak-middle-higher peak-lower peak)
  if (n >= 7) {
    const closes = candles.slice(-7).map((c) => c.close);
    const peak1 = Math.max(closes[0], closes[1]);
    const trough1 = Math.min(closes[2], closes[3]);
    const head = Math.max(closes[3], closes[4]);
    const trough2 = Math.min(closes[4], closes[5]);
    const peak2 = Math.max(closes[5], closes[6]);
    if (
      head > peak1 &&
      head > peak2 &&
      Math.abs(peak1 - peak2) / head < 0.05 &&
      trough1 < peak1 &&
      trough2 < peak2
    ) {
      results.push({
        pattern: 'HeadAndShoulders',
        direction: 'BEARISH',
        confidence: 0.72,
        description: 'Head and Shoulders pattern detected (bearish reversal)',
      });
    }
  }

  // Double Top (simplified)
  if (n >= 5) {
    const highs = candles.slice(-5).map((c) => c.high);
    const maxH = Math.max(...highs);
    const maxIdx = highs.indexOf(maxH);
    const otherHighs = highs.filter((_, i) => i !== maxIdx);
    const secondMax = Math.max(...otherHighs);
    if (Math.abs(maxH - secondMax) / maxH < 0.02 && maxIdx !== 2) {
      results.push({
        pattern: 'DoubleTop',
        direction: 'BEARISH',
        confidence: 0.6,
        description: 'Double Top pattern detected (bearish reversal)',
      });
    }
  }

  // Double Bottom (simplified)
  if (n >= 5) {
    const lows = candles.slice(-5).map((c) => c.low);
    const minL = Math.min(...lows);
    const minIdx = lows.indexOf(minL);
    const otherLows = lows.filter((_, i) => i !== minIdx);
    const secondMin = Math.min(...otherLows);
    if (Math.abs(minL - secondMin) / minL < 0.02 && minIdx !== 2) {
      results.push({
        pattern: 'DoubleBottom',
        direction: 'BULLISH',
        confidence: 0.6,
        description: 'Double Bottom pattern detected (bullish reversal)',
      });
    }
  }

  // Flag / Pennant (simplified: strong move followed by consolidation)
  if (n >= 10) {
    const first5 = candles.slice(-10, -5);
    const last5 = candles.slice(-5);
    const first5Range =
      Math.max(...first5.map((c) => c.high)) -
      Math.min(...first5.map((c) => c.low));
    const last5Range =
      Math.max(...last5.map((c) => c.high)) -
      Math.min(...last5.map((c) => c.low));
    const priceChange = last5[0].close - first5[0].close;
    if (first5Range > 0 && last5Range / first5Range < 0.5 && Math.abs(priceChange) / first5[0].close < 0.03) {
      const dir = priceChange > 0 ? 'BULLISH' : 'BEARISH';
      results.push({
        pattern: 'Flag',
        direction: dir,
        confidence: 0.55,
        description: `Flag/Pennant consolidation detected (${dir.toLowerCase()} continuation)`,
      });
    }
  }

  return results;
}

// ============================================================
// CANDLESTICK PATTERN DETECTION
// ============================================================

async function detectCandlestickPatterns(
  tokenAddress: string,
  chain: string = 'SOL'
): Promise<
  Array<{
    name: string;
    category: PatternCategory;
    description: string;
    conditions: Record<string, unknown>;
    confidence: number;
    timeframe: string;
  }>
> {
  const detected: Array<{
    name: string;
    category: PatternCategory;
    description: string;
    conditions: Record<string, unknown>;
    confidence: number;
    timeframe: string;
  }> = [];

  // Lazily initialize engine on first detection call
  await ensureEngineInitialized();

  if (engineAvailable && scanTokenFn) {
    try {
      const result = await scanTokenFn(tokenAddress, chain);

      // Process individual patterns
      for (const p of result.patterns) {
        detected.push({
          name: p.pattern,
          category: mapPatternCategory(p.category),
          description: p.description,
          conditions: {
            direction: p.direction,
            timeframe: p.timeframe,
            confidence: p.confidence,
            reliability: p.reliability,
            weight: p.weight,
            priceAtDetection: p.priceAtDetection,
            tokenAddress,
          },
          confidence: p.confidence,
          timeframe: p.timeframe,
        });
      }

      // Process confluences as higher-confidence patterns
      for (const c of result.confluences) {
        detected.push({
          name: `${c.pattern}_Confluence`,
          category: 'PRICE_ACTION',
          description: c.description,
          conditions: {
            direction: c.direction,
            timeframes: c.timeframes,
            combinedWeight: c.combinedWeight,
            combinedConfidence: c.combinedConfidence,
            tokenAddress,
          },
          confidence: c.combinedConfidence,
          timeframe: c.timeframes.join('+'),
        });
      }
    } catch (error) {
      console.error(
        `[PatternDetection] Candlestick engine failed for ${tokenAddress}:`,
        error
      );
      // Fall through to fallback
    }
  }

  // Fallback: use simple detection if engine didn't produce results
  if (detected.length === 0) {
    try {
      const timeframes = ['1h', '4h', '1d'];
      for (const tf of timeframes) {
        const candles = await loadCandlesFromDB(tokenAddress, tf);
        if (candles.length >= 3) {
          const simpleResults = detectSimplePatterns(candles);
          for (const p of simpleResults) {
            detected.push({
              name: p.pattern,
              category: 'PRICE_ACTION',
              description: p.description,
              conditions: {
                direction: p.direction,
                confidence: p.confidence,
                tokenAddress,
                timeframe: tf,
                source: 'fallback_detector',
              },
              confidence: p.confidence,
              timeframe: tf,
            });
          }
          // If we found patterns on one timeframe, skip the rest
          if (simpleResults.length > 0) break;
        }
      }
    } catch (error) {
      console.error(
        `[PatternDetection] Fallback detection failed for ${tokenAddress}:`,
        error
      );
    }
  }

  return detected;
}

// ============================================================
// LOAD CANDLES FROM DB
// ============================================================

async function loadCandlesFromDB(
  tokenAddress: string,
  timeframe: string = '1h',
  limit: number = 100
): Promise<SimpleCandle[]> {
  const candles = await db.priceCandle.findMany({
    where: {
      tokenAddress,
      timeframe,
    },
    orderBy: { timestamp: 'asc' },
    take: limit,
  });

  return candles.map((c) => ({
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
    timestamp: c.timestamp,
  }));
}

// ============================================================
// METRIC-BASED PATTERN DETECTION
// ============================================================

async function detectMetricPatterns(
  tokenAddress: string
): Promise<MetricDetection[]> {
  const detections: MetricDetection[] = [];

  try {
    // Load token with DNA
    const token = await db.token.findFirst({
      where: { address: tokenAddress },
      include: { dna: true },
    });

    if (!token) return detections;

    const dna = token.dna;

    // ──────────────────────────────────────────
    // 1. Volume Spike Detection
    // volume24h > 3x average (using marketCap as a proxy baseline)
    // ──────────────────────────────────────────
    if (token.volume24h > 0 && token.marketCap > 0) {
      const volumeToMcapRatio = token.volume24h / token.marketCap;
      // If volume exceeds 3% of market cap, it's a significant spike
      // (Typical ratio is ~1% for normal trading)
      if (volumeToMcapRatio > 0.03) {
        const spikeMultiplier = volumeToMcapRatio / 0.01; // Normalized to typical ratio
        const botContext = dna ? dna.botActivityScore : 0;

        detections.push({
          name: 'Volume Spike',
          category: 'VOLUME',
          description: `Volume spike detected: ${spikeMultiplier.toFixed(1)}x normal ratio (V/MC: ${(volumeToMcapRatio * 100).toFixed(2)}%)`,
          conditions: {
            volume24h: token.volume24h,
            marketCap: token.marketCap,
            volumeToMcapRatio,
            spikeMultiplier,
            botActivityScore: botContext,
            priceChange24h: token.priceChange24h,
          },
          tokenAddress: token.address,
          tokenSymbol: token.symbol,
          confidence: Math.min(1, spikeMultiplier / 5),
        });
      }
    }

    // ──────────────────────────────────────────
    // 2. Smart Money Accumulation
    // tokenDNA.smartMoneyScore > 60 and priceChange24h < 5
    // ──────────────────────────────────────────
    if (dna && dna.smartMoneyScore > 60 && Math.abs(token.priceChange24h) < 5) {
      detections.push({
        name: 'Smart Money Accumulation',
        category: 'SMART_MONEY',
        description: `Smart money accumulating (score: ${dna.smartMoneyScore.toFixed(1)}) with stable price (${token.priceChange24h.toFixed(2)}% 24h)`,
        conditions: {
          smartMoneyScore: dna.smartMoneyScore,
          priceChange24h: token.priceChange24h,
          whaleScore: dna.whaleScore,
          volume24h: token.volume24h,
          liquidity: token.liquidity,
        },
        tokenAddress: token.address,
        tokenSymbol: token.symbol,
        confidence: Math.min(1, dna.smartMoneyScore / 100),
      });
    }

    // ──────────────────────────────────────────
    // 3. Liquidity Drain
    // liquidity declining + volume increasing (from token data)
    // ──────────────────────────────────────────
    if (token.liquidity > 0 && token.volume24h > 0) {
      const volumeToLiquidityRatio = token.volume24h / token.liquidity;
      // If 24h volume > 50% of liquidity, it's a drain signal
      if (volumeToLiquidityRatio > 0.5) {
        const drainSeverity = Math.min(1, volumeToLiquidityRatio / 2);

        detections.push({
          name: 'Liquidity Drain',
          category: 'LIQUIDITY',
          description: `Liquidity drain: V/L ratio ${(volumeToLiquidityRatio * 100).toFixed(1)}% (vol: $${formatNum(token.volume24h)}, liq: $${formatNum(token.liquidity)})`,
          conditions: {
            liquidity: token.liquidity,
            volume24h: token.volume24h,
            volumeToLiquidityRatio,
            drainSeverity,
            marketCap: token.marketCap,
          },
          tokenAddress: token.address,
          tokenSymbol: token.symbol,
          confidence: drainSeverity,
        });
      }
    }

    // ──────────────────────────────────────────
    // 4. Breakout
    // priceChange24h > 10% + volume24h > 2x marketCap * 0.01
    // ──────────────────────────────────────────
    if (token.priceChange24h > 10 && token.marketCap > 0) {
      const volumeThreshold = token.marketCap * 0.01 * 2;
      if (token.volume24h > volumeThreshold) {
        const breakoutStrength = token.priceChange24h / 10; // Normalized to 10% threshold

        detections.push({
          name: 'Breakout',
          category: 'MOMENTUM',
          description: `Breakout: +${token.priceChange24h.toFixed(2)}% with volume $${formatNum(token.volume24h)} (threshold: $${formatNum(volumeThreshold)})`,
          conditions: {
            priceChange24h: token.priceChange24h,
            volume24h: token.volume24h,
            volumeThreshold,
            breakoutStrength,
            marketCap: token.marketCap,
          },
          tokenAddress: token.address,
          tokenSymbol: token.symbol,
          confidence: Math.min(1, breakoutStrength / 5),
        });
      }
    }

    // ──────────────────────────────────────────
    // 5. Flash Crash
    // priceChange1h < -15% or priceChange24h < -30%
    // ──────────────────────────────────────────
    if (token.priceChange1h < -15 || token.priceChange24h < -30) {
      const isHourly = token.priceChange1h < -15;
      const crashMagnitude = isHourly
        ? Math.abs(token.priceChange1h)
        : Math.abs(token.priceChange24h);
      const riskScore = dna ? dna.riskScore : 50;

      detections.push({
        name: 'Flash Crash',
        category: 'RISK',
        description: `Flash crash: ${isHourly ? '1h' : '24h'} drop of -${crashMagnitude.toFixed(2)}% (risk: ${riskScore})`,
        conditions: {
          priceChange1h: token.priceChange1h,
          priceChange24h: token.priceChange24h,
          crashMagnitude,
          timeframe: isHourly ? '1h' : '24h',
          riskScore,
          volume24h: token.volume24h,
          liquidity: token.liquidity,
        },
        tokenAddress: token.address,
        tokenSymbol: token.symbol,
        confidence: Math.min(1, crashMagnitude / 50),
      });
    }

    // ──────────────────────────────────────────
    // 6. Bot Activity Surge
    // High bot activity score indicates algo trading
    // ──────────────────────────────────────────
    if (dna && dna.botActivityScore > 70) {
      detections.push({
        name: 'Bot Activity Surge',
        category: 'BOT_ACTIVITY',
        description: `High bot activity: score ${dna.botActivityScore.toFixed(1)} (MEV: ${dna.mevPct.toFixed(1)}%, sniper: ${dna.sniperPct.toFixed(1)}%, copy: ${dna.copyBotPct.toFixed(1)}%)`,
        conditions: {
          botActivityScore: dna.botActivityScore,
          mevPct: dna.mevPct,
          sniperPct: dna.sniperPct,
          copyBotPct: dna.copyBotPct,
          washTradeProb: dna.washTradeProb,
        },
        tokenAddress: token.address,
        tokenSymbol: token.symbol,
        confidence: Math.min(1, dna.botActivityScore / 100),
      });
    }

    // ──────────────────────────────────────────
    // 7. Whale Concentration Risk
    // ──────────────────────────────────────────
    if (dna && dna.whaleScore > 70) {
      detections.push({
        name: 'Whale Concentration',
        category: 'RISK',
        description: `High whale concentration: score ${dna.whaleScore.toFixed(1)} — potential dump risk`,
        conditions: {
          whaleScore: dna.whaleScore,
          smartMoneyScore: dna.smartMoneyScore,
          liquidity: token.liquidity,
          marketCap: token.marketCap,
        },
        tokenAddress: token.address,
        tokenSymbol: token.symbol,
        confidence: Math.min(1, dna.whaleScore / 100),
      });
    }
  } catch (error) {
    console.error(
      `[PatternDetection] Metric detection error for ${tokenAddress}:`,
      error
    );
  }

  return detections;
}

// ============================================================
// PATTERN RULE CREATION / UPDATE
// ============================================================

async function upsertPatternRule(params: {
  name: string;
  category: string;
  description: string;
  conditions: Record<string, unknown>;
  confidence: number;
  tokenAddress: string;
  tokenSymbol: string;
  timeframe?: string;
}): Promise<{ created: boolean }> {
  try {
    // Check for existing rule with same name + category
    const existing = await db.patternRule.findFirst({
      where: {
        name: params.name,
        category: params.category,
      },
    });

    if (existing) {
      // Update existing rule: increment occurrences, adjust winRate
      const newOccurrences = existing.occurrences + 1;
      // Gradually adjust winRate toward the new confidence using EMA
      const alpha = 0.1;
      const newWinRate = existing.winRate * (1 - alpha) + params.confidence * alpha;

      const existingConditions = parseJSON(existing.conditions);
      const existingBacktest = parseJSON(existing.backtestResults);

      await db.patternRule.update({
        where: { id: existing.id },
        data: {
          occurrences: newOccurrences,
          winRate: newWinRate,
          description: params.description,
          conditions: JSON.stringify({
            ...existingConditions,
            lastDetection: {
              tokenAddress: params.tokenAddress,
              tokenSymbol: params.tokenSymbol,
              confidence: params.confidence,
              timeframe: params.timeframe,
              detectedAt: new Date().toISOString(),
            },
          }),
          backtestResults: JSON.stringify({
            ...existingBacktest,
            recentConfidences: [
              ...((existingBacktest.recentConfidences as number[]) ?? []).slice(
                -19
              ),
              params.confidence,
            ],
          }),
        },
      });

      return { created: false };
    } else {
      // Create new PatternRule
      await db.patternRule.create({
        data: {
          name: params.name,
          category: params.category,
          description: params.description,
          conditions: JSON.stringify({
            ...params.conditions,
            lastDetection: {
              tokenAddress: params.tokenAddress,
              tokenSymbol: params.tokenSymbol,
              confidence: params.confidence,
              timeframe: params.timeframe,
              detectedAt: new Date().toISOString(),
            },
          }),
          winRate: params.confidence,
          occurrences: 1,
          isActive: true,
          backtestResults: JSON.stringify({
            recentConfidences: [params.confidence],
          }),
        },
      });

      return { created: true };
    }
  } catch (error) {
    console.error(
      `[PatternDetection] Error upserting pattern rule "${params.name}":`,
      error
    );
    return { created: false };
  }
}

// ============================================================
// FULL DETECTION RUN (all tokens)
// ============================================================

async function runFullDetection(): Promise<void> {
  if (detectionState.isRunning) {
    return; // Prevent concurrent runs
  }

  const startTime = Date.now();
  detectionState.isRunning = true;
  detectionState.lastError = null;
  detectionState.tokensScanned = 0;
  detectionState.patternsCreated = 0;
  detectionState.patternsUpdated = 0;

  try {
    // Get all tokens that have candle data
    const tokensWithCandles = await db.token.findMany({
      where: {
        candles: {
          some: {},
        },
      },
      select: {
        address: true,
        symbol: true,
        chain: true,
      },
      take: 200, // Limit per run to avoid overwhelming the system
    });

    detectionState.tokensScanned = tokensWithCandles.length;

    for (const token of tokensWithCandles) {
      try {
        // === Candlestick Pattern Detection ===
        const candlePatterns = await detectCandlestickPatterns(
          token.address,
          token.chain
        );

        for (const pattern of candlePatterns) {
          const result = await upsertPatternRule({
            name: pattern.name,
            category: pattern.category,
            description: pattern.description,
            conditions: pattern.conditions,
            confidence: pattern.confidence,
            tokenAddress: token.address,
            tokenSymbol: token.symbol,
            timeframe: pattern.timeframe,
          });

          if (result.created) {
            detectionState.patternsCreated++;
          } else {
            detectionState.patternsUpdated++;
          }
          detectionState.totalDetections++;

          addRecentDetection(pattern.name, pattern.category, token.address);
        }

        // === Metric-Based Pattern Detection ===
        const metricDetections = await detectMetricPatterns(token.address);

        for (const detection of metricDetections) {
          const result = await upsertPatternRule({
            name: detection.name,
            category: detection.category,
            description: detection.description,
            conditions: detection.conditions,
            confidence: detection.confidence,
            tokenAddress: detection.tokenAddress,
            tokenSymbol: detection.tokenSymbol,
          });

          if (result.created) {
            detectionState.patternsCreated++;
          } else {
            detectionState.patternsUpdated++;
          }
          detectionState.totalDetections++;

          addRecentDetection(
            detection.name,
            detection.category,
            detection.tokenAddress
          );
        }

        // Small delay between tokens to avoid DB pressure
        await new Promise((r) => setTimeout(r, 50));
      } catch (error) {
        console.error(
          `[PatternDetection] Error processing token ${token.address}:`,
          error
        );
        // Continue with next token
      }
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    detectionState.lastError = msg;
    console.error('[PatternDetection] Full detection run failed:', error);
  } finally {
    detectionState.isRunning = false;
    detectionState.lastRunAt = new Date();
    detectionState.lastRunDurationMs = Date.now() - startTime;
  }
}

// ============================================================
// SINGLE TOKEN DETECTION
// ============================================================

async function runTokenDetection(tokenAddress: string): Promise<{
  candlePatterns: number;
  metricDetections: number;
  created: number;
  updated: number;
}> {
  const token = await db.token.findFirst({
    where: { address: tokenAddress },
    select: { address: true, symbol: true, chain: true },
  });

  if (!token) {
    throw new Error(`Token not found: ${tokenAddress}`);
  }

  let created = 0;
  let updated = 0;

  // Candlestick detection
  const candlePatterns = await detectCandlestickPatterns(
    token.address,
    token.chain
  );

  for (const pattern of candlePatterns) {
    const result = await upsertPatternRule({
      name: pattern.name,
      category: pattern.category,
      description: pattern.description,
      conditions: pattern.conditions,
      confidence: pattern.confidence,
      tokenAddress: token.address,
      tokenSymbol: token.symbol,
      timeframe: pattern.timeframe,
    });

    if (result.created) created++;
    else updated++;

    detectionState.totalDetections++;
    addRecentDetection(pattern.name, pattern.category, token.address);
  }

  // Metric detection
  const metricDetections = await detectMetricPatterns(token.address);

  for (const detection of metricDetections) {
    const result = await upsertPatternRule({
      name: detection.name,
      category: detection.category,
      description: detection.description,
      conditions: detection.conditions,
      confidence: detection.confidence,
      tokenAddress: detection.tokenAddress,
      tokenSymbol: detection.tokenSymbol,
    });

    if (result.created) created++;
    else updated++;

    detectionState.totalDetections++;
    addRecentDetection(
      detection.name,
      detection.category,
      detection.tokenAddress
    );
  }

  return {
    candlePatterns: candlePatterns.length,
    metricDetections: metricDetections.length,
    created,
    updated,
  };
}

// ============================================================
// STATUS HELPER
// ============================================================

async function getStatus() {
  try {
    const totalPatterns = await db.patternRule.count();
    const activePatterns = await db.patternRule.count({
      where: { isActive: true },
    });
    const categoryBreakdown = await db.patternRule.groupBy({
      by: ['category'],
      _count: { category: true },
    });

    const topPatterns = await db.patternRule.findMany({
      orderBy: { occurrences: 'desc' },
      take: 10,
      select: {
        name: true,
        category: true,
        occurrences: true,
        winRate: true,
      },
    });

    return {
      status: detectionState.isRunning ? 'RUNNING' : 'IDLE',
      engine: {
        candlestickEngineAvailable: engineAvailable,
        fallbackDetectorAvailable: true,
      },
      detection: {
        isRunning: detectionState.isRunning,
        lastRunAt: detectionState.lastRunAt,
        lastRunDurationMs: detectionState.lastRunDurationMs,
        lastError: detectionState.lastError,
        tokensScanned: detectionState.tokensScanned,
        patternsCreated: detectionState.patternsCreated,
        patternsUpdated: detectionState.patternsUpdated,
        totalDetections: detectionState.totalDetections,
      },
      patterns: {
        total: totalPatterns,
        active: activePatterns,
        categoryBreakdown: Object.fromEntries(
          categoryBreakdown.map((c) => [c.category, c._count.category])
        ),
        topPatterns,
      },
      recentDetections: detectionState.recentDetections.slice(-20),
    };
  } catch (error) {
    console.error('[PatternDetection] Status error:', error);
    return {
      status: 'ERROR' as const,
      error: error instanceof Error ? error.message : 'Unknown error',
      detection: {
        isRunning: detectionState.isRunning,
        lastRunAt: detectionState.lastRunAt,
        lastRunDurationMs: detectionState.lastRunDurationMs,
        lastError: detectionState.lastError,
        tokensScanned: detectionState.tokensScanned,
        patternsCreated: detectionState.patternsCreated,
        patternsUpdated: detectionState.patternsUpdated,
        totalDetections: detectionState.totalDetections,
      },
      patterns: {
        total: 0,
        active: 0,
        categoryBreakdown: {},
        topPatterns: [],
      },
      recentDetections: detectionState.recentDetections.slice(-20),
    };
  }
}

// ============================================================
// HTTP HANDLERS
// ============================================================

export async function GET() {
  try {
    const status = await getStatus();
    return NextResponse.json(status);
  } catch (error) {
    console.error('[PatternDetection] GET error:', error);
    return NextResponse.json(
      {
        error: 'Failed to get detection status',
        details: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const action = body.action as string | undefined;

    switch (action) {
      case 'detect': {
        // Fire-and-forget: start detection in background
        if (detectionState.isRunning) {
          return NextResponse.json(
            {
              message: 'Detection already running',
              status: 'RUNNING',
              lastRunAt: detectionState.lastRunAt,
            },
            { status: 409 }
          );
        }

        // Fire and forget
        runFullDetection().catch((err) => {
          console.error('[PatternDetection] Background detection error:', err);
        });

        return NextResponse.json({
          message: 'Pattern detection started in background',
          status: 'RUNNING',
          engine: {
            candlestickEngineAvailable: engineAvailable,
            fallbackDetectorAvailable: true,
          },
        });
      }

      case 'token': {
        const address = body.address as string | undefined;
        if (!address) {
          return NextResponse.json(
            { error: 'Token address required for "token" action' },
            { status: 400 }
          );
        }

        const result = await runTokenDetection(address);
        return NextResponse.json({
          message: `Detection complete for ${address}`,
          tokenAddress: address,
          ...result,
        });
      }

      case 'status': {
        const status = await getStatus();
        return NextResponse.json(status);
      }

      default: {
        return NextResponse.json(
          {
            error: 'Invalid action. Use "detect", "token", or "status"',
            usage: {
              detect: { action: 'detect' },
              token: { action: 'token', address: '0x...' },
              status: { action: 'status' },
            },
          },
          { status: 400 }
        );
      }
    }
  } catch (error) {
    console.error('[PatternDetection] POST error:', error);
    return NextResponse.json(
      {
        error: 'Pattern detection request failed',
        details: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    );
  }
}

// ============================================================
// UTILITY FUNCTIONS
// ============================================================

function mapPatternCategory(engineCategory: string): PatternCategory {
  switch (engineCategory) {
    case 'REVERSAL_BULL':
    case 'REVERSAL_BEAR':
      return 'PRICE_ACTION';
    case 'CONTINUATION':
      return 'MOMENTUM';
    default:
      return 'PRICE_ACTION';
  }
}

function parseJSON(str: string): Record<string, unknown> {
  try {
    return JSON.parse(str || '{}');
  } catch {
    return {};
  }
}

function addRecentDetection(
  name: string,
  category: string,
  tokenAddress: string
): void {
  detectionState.recentDetections.push({
    name,
    category,
    tokenAddress,
    detectedAt: new Date().toISOString(),
  });

  // Trim to max size
  if (detectionState.recentDetections.length > MAX_RECENT_DETECTIONS) {
    detectionState.recentDetections =
      detectionState.recentDetections.slice(-MAX_RECENT_DETECTIONS);
  }
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
  return n.toFixed(2);
}
