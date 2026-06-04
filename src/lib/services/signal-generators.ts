/**
 * Signal Generators - Smart Money, Rug Pull, V-Shape, Liquidity Trap
 * Generates real signals from actual market data (DexScreener + CoinGecko)
 */

import { db } from '@/lib/db';
import type { TokenLiquidityData } from './dexscreener-client';

// ============================================================
// TYPES
// ============================================================

interface TokenMarketData {
  symbol: string;
  name: string;
  chain: string;
  priceUsd: number;
  volume24h: number;
  liquidityUsd: number;
  marketCap: number;
  fdv: number;
  priceChange1h: number;
  priceChange6h: number;
  priceChange24h: number;
  txns24h: { buys: number; sells: number; };
  pairCreatedAt: number;
  dexId: string;
}

export interface GeneratedSignal {
  tokenId: string;
  type: string;
  subtype: string;
  strength: number;
  direction: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  title: string;
  description: string;
  metadata: Record<string, any>;
}

// ============================================================
// SMART MONEY DETECTION
// ============================================================

export function detectSmartMoney(data: TokenMarketData): GeneratedSignal | null {
  let score = 0;
  const reasons: string[] = [];

  if (data.marketCap > 0) {
    const volMcapRatio = data.volume24h / data.marketCap;
    if (volMcapRatio > 0.5) { score += 30; reasons.push(`Extreme volume/MCap: ${(volMcapRatio * 100).toFixed(1)}%`); }
    else if (volMcapRatio > 0.2) { score += 20; reasons.push(`High volume/MCap: ${(volMcapRatio * 100).toFixed(1)}%`); }
    else if (volMcapRatio > 0.1) { score += 10; reasons.push(`Moderate volume/MCap: ${(volMcapRatio * 100).toFixed(1)}%`); }
  }

  const totalTxns = data.txns24h.buys + data.txns24h.sells;
  if (totalTxns > 10) {
    const buyRatio = data.txns24h.buys / totalTxns;
    if (buyRatio > 0.7) { score += 25; reasons.push(`Heavy buying: ${(buyRatio * 100).toFixed(1)}% buys`); }
    else if (buyRatio < 0.3) { score += 20; reasons.push(`Heavy selling: ${((1 - buyRatio) * 100).toFixed(1)}% sells`); }
  }

  if (data.liquidityUsd > 0 && data.volume24h > 0) {
    const volLiqRatio = data.volume24h / data.liquidityUsd;
    if (volLiqRatio > 2) { score += 25; reasons.push(`Volume >> Liquidity: ${volLiqRatio.toFixed(1)}x (whale activity)`); }
    else if (volLiqRatio > 1) { score += 15; reasons.push(`Volume > Liquidity: ${volLiqRatio.toFixed(1)}x`); }
  }

  if (Math.abs(data.priceChange24h) > 10 && data.volume24h > data.liquidityUsd * 0.3) {
    score += 20; reasons.push(`Significant move (${data.priceChange24h.toFixed(1)}%) with volume`);
  }

  if (score < 15) return null;

  const direction = data.txns24h.buys > data.txns24h.sells ? 'BULLISH' :
    data.txns24h.sells > data.txns24h.buys ? 'BEARISH' : 'NEUTRAL';

  return {
    tokenId: '',
    type: 'SMART_MONEY',
    subtype: direction === 'BULLISH' ? 'ACCUMULATION' : direction === 'BEARISH' ? 'DISTRIBUTION' : 'MIXED',
    strength: Math.min(score, 100),
    direction,
    title: `Smart Money ${direction === 'BULLISH' ? 'Accumulation' : direction === 'BEARISH' ? 'Distribution' : 'Activity'}`,
    description: reasons.join('. '),
    metadata: {
      volumeMcapRatio: data.marketCap > 0 ? data.volume24h / data.marketCap : 0,
      buySellRatio: totalTxns > 0 ? data.txns24h.buys / totalTxns : 0.5,
      volumeLiquidityRatio: data.liquidityUsd > 0 ? data.volume24h / data.liquidityUsd : 0,
      priceChange24h: data.priceChange24h,
    },
  };
}

// ============================================================
// RUG PULL DETECTION
// ============================================================

export function detectRugPull(data: TokenMarketData): GeneratedSignal | null {
  let score = 0;
  const reasons: string[] = [];

  const ageMs = Date.now() - data.pairCreatedAt;
  const ageDays = data.pairCreatedAt > 0 ? ageMs / 86400000 : 999;
  if (ageDays < 1) { score += 30; reasons.push(`Token < 24h old`); }
  else if (ageDays < 3) { score += 20; reasons.push(`Token only ${ageDays.toFixed(1)} days old`); }
  else if (ageDays < 7) { score += 10; reasons.push(`Token < 1 week old`); }

  if (data.priceChange24h < -50) { score += 35; reasons.push(`Extreme crash: ${data.priceChange24h.toFixed(1)}% in 24h`); }
  else if (data.priceChange24h < -30) { score += 25; reasons.push(`Severe drop: ${data.priceChange24h.toFixed(1)}% in 24h`); }
  else if (data.priceChange24h < -15) { score += 10; reasons.push(`Significant decline: ${data.priceChange24h.toFixed(1)}%`); }

  if (data.marketCap > 0 && data.liquidityUsd > 0) {
    const liqMcapRatio = data.liquidityUsd / data.marketCap;
    if (liqMcapRatio < 0.01) { score += 25; reasons.push(`Extremely low liq/MCap: ${(liqMcapRatio * 100).toFixed(2)}%`); }
    else if (liqMcapRatio < 0.05) { score += 15; reasons.push(`Low liq/MCap: ${(liqMcapRatio * 100).toFixed(2)}%`); }
  }

  if (data.fdv > 1000000 && data.liquidityUsd < 10000) {
    score += 20; reasons.push(`FDV $${(data.fdv / 1e6).toFixed(1)}M but only $${(data.liquidityUsd / 1e3).toFixed(0)}K liquidity`);
  }

  const totalTxns = data.txns24h.buys + data.txns24h.sells;
  if (totalTxns > 5 && data.txns24h.sells / totalTxns > 0.8) {
    score += 20; reasons.push(`Panic selling: ${(data.txns24h.sells / totalTxns * 100).toFixed(1)}% sells`);
  }

  if (data.priceChange6h < -20 && data.priceChange24h > 0) {
    score += 15; reasons.push(`Pump & dump: +${data.priceChange24h.toFixed(1)}% 24h but ${data.priceChange6h.toFixed(1)}% 6h`);
  }

  if (score < 20) return null;

  return {
    tokenId: '',
    type: 'RUG_PULL',
    subtype: score >= 60 ? 'HIGH_RISK' : score >= 40 ? 'MEDIUM_RISK' : 'LOW_RISK',
    strength: Math.min(score, 100),
    direction: 'BEARISH',
    title: `Rug Pull Risk (${score >= 60 ? 'HIGH' : score >= 40 ? 'MEDIUM' : 'LOW'})`,
    description: reasons.join('. '),
    metadata: { ageDays, priceChange24h: data.priceChange24h, fdv: data.fdv, liquidityUsd: data.liquidityUsd },
  };
}

// ============================================================
// V-SHAPE RECOVERY DETECTION
// ============================================================

export function detectVShape(data: TokenMarketData): GeneratedSignal | null {
  let score = 0;
  const reasons: string[] = [];

  if (data.priceChange1h > 5 && data.priceChange6h < -5) {
    score += 30; reasons.push(`V-shape: ${data.priceChange6h.toFixed(1)}% 6h recovering ${data.priceChange1h.toFixed(1)}% 1h`);
  }
  if (data.priceChange24h < -5 && data.priceChange1h > 3) {
    score += 25; reasons.push(`Recovery from dip: ${data.priceChange24h.toFixed(1)}% 24h bouncing ${data.priceChange1h.toFixed(1)}% 1h`);
  }
  if (data.priceChange6h < -10 && data.priceChange1h > 10) {
    score += 35; reasons.push(`Strong V-reversal: crashed ${data.priceChange6h.toFixed(1)}% recovering ${data.priceChange1h.toFixed(1)}%`);
  }
  if (data.liquidityUsd > 0 && data.volume24h > data.liquidityUsd * 0.5) {
    score += 15; reasons.push(`High recovery volume confirms reversal`);
  }
  const totalTxns = data.txns24h.buys + data.txns24h.sells;
  if (totalTxns > 5 && data.txns24h.buys / totalTxns > 0.6 && data.priceChange1h > 0) {
    score += 15; reasons.push(`Strong buy pressure during recovery`);
  }
  if (data.priceChange6h < -3 && data.priceChange1h > 2) {
    score += 10; reasons.push(`Mild V-shape forming`);
  }

  if (score < 15) return null;

  return {
    tokenId: '',
    type: 'V_SHAPE',
    subtype: score >= 50 ? 'STRONG_RECOVERY' : 'RECOVERY',
    strength: Math.min(score, 100),
    direction: 'BULLISH',
    title: `V-Shape Recovery (${score >= 50 ? 'Strong' : 'Forming'})`,
    description: reasons.join('. '),
    metadata: { priceChange1h: data.priceChange1h, priceChange6h: data.priceChange6h, priceChange24h: data.priceChange24h },
  };
}

// ============================================================
// LIQUIDITY TRAP DETECTION
// ============================================================

export function detectLiquidityTrap(data: TokenMarketData): GeneratedSignal | null {
  let score = 0;
  const reasons: string[] = [];

  if (data.fdv > 0 && data.liquidityUsd > 0) {
    const fdvLiqRatio = data.fdv / data.liquidityUsd;
    if (fdvLiqRatio > 1000) { score += 35; reasons.push(`FDV/Liquidity ${fdvLiqRatio.toFixed(0)}x - massive illusion`); }
    else if (fdvLiqRatio > 100) { score += 25; reasons.push(`FDV/Liquidity ${fdvLiqRatio.toFixed(0)}x - illiquid`); }
    else if (fdvLiqRatio > 50) { score += 15; reasons.push(`FDV/Liquidity ${fdvLiqRatio.toFixed(0)}x - low real liquidity`); }
  }

  if (data.liquidityUsd === 0 && data.marketCap > 0) {
    score += 40; reasons.push(`ZERO liquidity with market cap - extreme trap risk`);
  } else if (data.liquidityUsd < 5000 && data.liquidityUsd > 0) {
    score += 30; reasons.push(`Dangerously low liquidity: $${data.liquidityUsd.toFixed(0)}`);
  } else if (data.liquidityUsd < 20000) {
    score += 20; reasons.push(`Low liquidity: $${(data.liquidityUsd / 1e3).toFixed(1)}K`);
  } else if (data.liquidityUsd < 50000) {
    score += 10; reasons.push(`Thin liquidity: $${(data.liquidityUsd / 1e3).toFixed(1)}K`);
  }

  if (data.liquidityUsd > 10000 && data.volume24h > 0 && data.volume24h / data.liquidityUsd < 0.01) {
    score += 20; reasons.push(`Volume dried up`);
  }

  if (data.marketCap > 1000000 && data.liquidityUsd < 10000) {
    score += 25; reasons.push(`$${(data.marketCap / 1e6).toFixed(1)}M cap but can't sell`);
  }

  if (score < 20) return null;

  return {
    tokenId: '',
    type: 'LIQUIDITY_TRAP',
    subtype: score >= 50 ? 'EXTREME' : score >= 35 ? 'HIGH' : 'MODERATE',
    strength: Math.min(score, 100),
    direction: 'BEARISH',
    title: `Liquidity Trap (${score >= 50 ? 'Extreme' : score >= 35 ? 'High' : 'Moderate'})`,
    description: reasons.join('. '),
    metadata: {
      fdvLiquidityRatio: data.fdv > 0 && data.liquidityUsd > 0 ? data.fdv / data.liquidityUsd : null,
      liquidityUsd: data.liquidityUsd,
      marketCap: data.marketCap,
    },
  };
}

// ============================================================
// BATCH SIGNAL GENERATION
// ============================================================

export async function generateAllSignals(
  tokensWithMarketData: { tokenId: string; marketData: TokenMarketData; }[]
): Promise<GeneratedSignal[]> {
  const allSignals: GeneratedSignal[] = [];

  for (const { tokenId, marketData } of tokensWithMarketData) {
    const smSignal = detectSmartMoney(marketData);
    if (smSignal) { smSignal.tokenId = tokenId; allSignals.push(smSignal); }

    const rpSignal = detectRugPull(marketData);
    if (rpSignal) { rpSignal.tokenId = tokenId; allSignals.push(rpSignal); }

    const vsSignal = detectVShape(marketData);
    if (vsSignal) { vsSignal.tokenId = tokenId; allSignals.push(vsSignal); }

    const ltSignal = detectLiquidityTrap(marketData);
    if (ltSignal) { ltSignal.tokenId = tokenId; allSignals.push(ltSignal); }
  }

  const sm = allSignals.filter(s => s.type === 'SMART_MONEY').length;
  const rp = allSignals.filter(s => s.type === 'RUG_PULL').length;
  const vs = allSignals.filter(s => s.type === 'V_SHAPE').length;
  const lt = allSignals.filter(s => s.type === 'LIQUIDITY_TRAP').length;
  console.log(`[Signals] Generated ${allSignals.length} signals: SmartMoney=${sm}, RugPull=${rp}, VShape=${vs}, LiquidityTrap=${lt}`);

  return allSignals;
}

export async function saveSignalsToDb(signals: GeneratedSignal[]): Promise<number> {
  let saved = 0;
  for (const signal of signals) {
    try {
      await db.signal.create({
        data: {
          tokenId: signal.tokenId,
          type: signal.type,
          direction: signal.direction,
          description: signal.title + ': ' + signal.description,
          metadata: JSON.stringify({
            subtype: signal.subtype,
            strength: signal.strength,
            ...signal.metadata,
          }),
          confidence: signal.strength,
        },
      });
      saved++;
    } catch {
      // Skip duplicates
    }
  }
  console.log(`[Signals] Saved ${saved}/${signals.length} signals to DB`);
  return saved;
}

export type { TokenMarketData };

// ============================================================
// PATTERN SIGNAL GENERATOR
// Matches token data against PatternRule conditions from the DB.
// Condition fields match the actual seeded rules:
//   dropThreshold, recoveryThreshold, volumeMultiplier, minPriceChange,
//   liquidityDropPct, maxPriceChange, h1Change, h24Change, minDrop,
//   minVolume, min24hDrop, min1hBounce, minChange, min24hChange,
//   min1hChange, max24hChange, max1hChange, maxLiquidity, volumeRatio,
//   min1hAbs, min24hAbs, maxPriceChange
// ============================================================

export async function generatePatternSignals(
  tokens: Array<{
    id: string;
    symbol: string;
    chain: string;
    address: string;
    priceChange24h?: number | null;
    priceChange1h?: number | null;
    volume24h?: number | null;
    liquidity?: number | null;
    marketCap?: number | null;
  }>,
): Promise<{ signals: Array<any>; count: number }> {
  const { db } = await import('@/lib/db');

  const rules = await db.patternRule.findMany({ where: { isActive: true } });
  console.log(`[PatternSignals] Evaluating ${rules.length} rules against ${tokens.length} tokens`);

  const signals: Array<any> = [];

  for (const rule of rules) {
    let c: Record<string, any> = {};
    try {
      c = typeof rule.conditions === 'string' ? JSON.parse(rule.conditions as string) : (rule.conditions || {});
    } catch { continue; }
    if (!c || Object.keys(c).length === 0) continue;

    for (const token of tokens) {
      try {
        const pc24 = token.priceChange24h ?? 0;
        const pc1h = token.priceChange1h ?? 0;
        const vol = token.volume24h ?? 0;
        const liq = token.liquidity ?? 0;

        let matched = false;
        let confidence = 0.5;
        let desc = rule.description || rule.name;

        // Flash Crash Recovery: dropThreshold + recoveryThreshold
        if (c.dropThreshold !== undefined && pc24 < c.dropThreshold) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: Flash crash (${pc24.toFixed(2)}% 24h, threshold ${c.dropThreshold}%)`;
        }

        // Volume Spike: volumeMultiplier + minPriceChange
        if (c.volumeMultiplier !== undefined && c.minPriceChange !== undefined && Math.abs(pc24) >= c.minPriceChange && vol > 1000000) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: Volume spike (${pc24.toFixed(2)}%, vol $${(vol / 1e6).toFixed(1)}M)`;
        }

        // Liquidity Drain: liquidityDropPct + maxPriceChange
        if (c.liquidityDropPct !== undefined && c.maxPriceChange !== undefined && Math.abs(pc24) <= c.maxPriceChange && liq > 0 && liq < 1000000) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: Low liq with stable price (liq $${(liq / 1000).toFixed(0)}K)`;
        }

        // Bullish Divergence: h1Change + h24Change
        if (c.h1Change !== undefined && c.h24Change !== undefined && pc24 < c.h24Change) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: Divergence (24h ${pc24.toFixed(2)}% vs threshold ${c.h24Change}%)`;
        }

        // Rug Pull Pattern: minDrop + minVolume
        if (c.minDrop !== undefined && c.minVolume !== undefined && pc24 <= c.minDrop && vol >= c.minVolume) {
          matched = true; confidence += 0.15;
          desc = `${rule.name}: Drop ${pc24.toFixed(2)}% vol $${(vol / 1e6).toFixed(1)}M`;
        }

        // V-Shape Bounce: min24hDrop + min1hBounce
        if (c.min24hDrop !== undefined && pc24 <= c.min24hDrop) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: 24h drop ${pc24.toFixed(2)}% with bounce potential`;
        }

        // Momentum Breakout: minChange + minVolume
        if (c.minChange !== undefined && c.minVolume !== undefined && Math.abs(pc24) >= c.minChange && vol >= c.minVolume) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: ${pc24 > 0 ? 'Up' : 'Down'} momentum ${pc24.toFixed(2)}% vol $${(vol / 1e6).toFixed(1)}M`;
        }

        // Smart Money Entry: min24hChange + min1hChange + minVolume
        if (c.min24hChange !== undefined && c.minVolume !== undefined && pc24 >= c.min24hChange && vol >= c.minVolume) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: Entry signal ${pc24.toFixed(2)}% vol $${(vol / 1e6).toFixed(1)}M`;
        }

        // Smart Money Exit: max24hChange + max1hChange + minVolume
        if (c.max24hChange !== undefined && c.minVolume !== undefined && pc24 <= c.max24hChange && vol >= c.minVolume) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: Exit signal ${pc24.toFixed(2)}% vol $${(vol / 1e6).toFixed(1)}M`;
        }

        // Liquidity Trap: maxLiquidity + volumeRatio
        if (c.maxLiquidity !== undefined && c.volumeRatio !== undefined && liq > 0 && liq <= c.maxLiquidity && vol > liq * c.volumeRatio) {
          matched = true; confidence += 0.15;
          desc = `${rule.name}: Trap liq $${(liq / 1000).toFixed(0)}K with high vol ratio`;
        }

        // Trend Reversal: min1hAbs + min24hAbs
        if (c.min24hAbs !== undefined && Math.abs(pc24) >= c.min24hAbs) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: Strong move ${pc24.toFixed(2)}% suggesting reversal`;
        }

        // Accumulation Zone: maxPriceChange + minVolume
        if (c.maxPriceChange !== undefined && c.minVolume !== undefined && Math.abs(pc24) <= c.maxPriceChange && vol >= c.minVolume) {
          matched = true; confidence += 0.1;
          desc = `${rule.name}: Low volatility ${pc24.toFixed(2)}% with high vol $${(vol / 1e6).toFixed(1)}M`;
        }

        if (!matched) continue;

        confidence = Math.min(confidence, 0.95);
        const direction = pc24 > 1 ? 'BULLISH' : pc24 < -1 ? 'BEARISH' : 'NEUTRAL';

        const signal = await db.signal.create({
          data: {
            type: 'PATTERN',
            tokenId: token.id,
            confidence: Math.round(confidence * 100),
            direction,
            description: desc.slice(0, 500),
            metadata: JSON.stringify({
              patternRuleId: rule.id,
              patternRuleName: rule.name,
              category: rule.category || 'GENERAL',
              conditions: c,
              tokenSymbol: token.symbol,
              tokenChain: token.chain,
            }),
          },
        });
        signals.push(signal);
      } catch { /* skip individual token errors */ }
    }
  }

  console.log(`[PatternSignals] Created ${signals.length} pattern signals`);
  return { signals, count: signals.length };
}
