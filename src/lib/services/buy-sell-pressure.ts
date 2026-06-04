/**
 * Buy/Sell Pressure Service - CryptoQuant Terminal
 *
 * Uses DexPaprika's unique buy/sell ratio data (24h/6h/1h) to detect
 * market pressure and generate actionable signals for the brain.
 *
 * Key capabilities:
 * - Real-time buy/sell pressure per pool
 * - Pressure acceleration detection (1h vs 6h vs 24h)
 * - Cross-pool pressure correlation
 * - Integration with existing Token and Signal models
 */

import { dexPaprikaClient, type BuySellPressure } from './dexpaprika-client';
import { db } from '../db';
import { unifiedCache, cacheKeyWithChain } from '../unified-cache';

// ============================================================
// TYPES
// ============================================================

export interface PressureSignal {
  poolId: string;
  chain: string;
  tokenAddress: string;
  tokenSymbol: string;

  /** Current pressure state */
  pressure1h: BuySellPressure['pressure1h'];
  pressure6h: BuySellPressure['pressure6h'];
  pressure24h: BuySellPressure['pressure24h'];

  /** Acceleration indicator */
  acceleration: BuySellPressure['acceleration'];

  /** Raw ratios */
  buyRatio1h: number;
  buyRatio6h: number;
  buyRatio24h: number;

  /** Combined score (-100 to +100) */
  pressureScore: number;

  /** Signal type for the brain */
  signalType: 'STRONG_BUY_PRESSURE' | 'BUY_PRESSURE' | 'NEUTRAL' | 'SELL_PRESSURE' | 'STRONG_SELL_PRESSURE';

  /** Confidence (0-1) */
  confidence: number;

  /** Whether this is an acceleration signal (pressure changing) */
  isAccelerationSignal: boolean;

  detectedAt: Date;
}

export interface PressureScanResult {
  scanned: number;
  signals: PressureSignal[];
  strongBuy: PressureSignal[];
  strongSell: PressureSignal[];
  accelerating: PressureSignal[];
  summary: {
    avgPressureScore: number;
    bullishCount: number;
    bearishCount: number;
    neutralCount: number;
  };
}

// ============================================================
// BUY/SELL PRESSURE SERVICE CLASS
// ============================================================

class BuySellPressureService {
  private readonly SOURCE = 'buy-sell-pressure';

  /**
   * Analyze buy/sell pressure for a single pool.
   */
  async analyzePressure(
    chain: string,
    poolId: string,
    tokenAddress: string = '',
    tokenSymbol: string = '',
  ): Promise<PressureSignal> {
    const key = cacheKeyWithChain(this.SOURCE, 'pressure', chain, poolId);

    return unifiedCache.getOrFetch(
      key,
      () => this._analyzePressure(chain, poolId, tokenAddress, tokenSymbol),
      this.SOURCE,
      30_000,
    );
  }

  private async _analyzePressure(
    chain: string,
    poolId: string,
    tokenAddress: string,
    tokenSymbol: string,
  ): Promise<PressureSignal> {
    const pressure = await dexPaprikaClient.getBuySellPressure(chain, poolId);

    // Calculate combined pressure score (-100 to +100)
    // Weight: 1h = 50%, 6h = 30%, 24h = 20%
    const score1h = (pressure.buyRatio1h - 0.5) * 200;
    const score6h = (pressure.buyRatio6h - 0.5) * 200;
    const score24h = (pressure.buyRatio24h - 0.5) * 200;
    const pressureScore = Math.max(-100, Math.min(100,
      score1h * 0.5 + score6h * 0.3 + score24h * 0.2
    ));

    // Determine signal type
    let signalType: PressureSignal['signalType'] = 'NEUTRAL';
    if (pressureScore > 60) signalType = 'STRONG_BUY_PRESSURE';
    else if (pressureScore > 25) signalType = 'BUY_PRESSURE';
    else if (pressureScore < -60) signalType = 'STRONG_SELL_PRESSURE';
    else if (pressureScore < -25) signalType = 'SELL_PRESSURE';

    // Confidence based on how aligned the timeframes are
    const directions = [pressure.pressure1h, pressure.pressure6h, pressure.pressure24h];
    const bullishCount = directions.filter(d => d === 'BULLISH').length;
    const bearishCount = directions.filter(d => d === 'BEARISH').length;
    const maxAligned = Math.max(bullishCount, bearishCount);
    const confidence = maxAligned / 3;

    // Acceleration signal
    const isAccelerationSignal = pressure.acceleration === 'INCREASING_BUY' || pressure.acceleration === 'INCREASING_SELL';

    // Store signal
    if (tokenAddress && Math.abs(pressureScore) > 25) {
      try {
        await db.signal.create({
          data: {
            type: 'BUY_SELL_PRESSURE',
            tokenId: tokenAddress,
            confidence: Math.round(confidence * 100),
            direction: pressureScore > 0 ? 'LONG' : 'SHORT',
            description: `${signalType} for ${tokenSymbol || tokenAddress.slice(0, 8)}: score=${pressureScore.toFixed(0)}, 1h=${pressure.pressure1h}, 6h=${pressure.pressure6h}, 24h=${pressure.pressure24h}, accel=${pressure.acceleration}`,
            metadata: JSON.stringify({
              poolId,
              chain,
              buyRatio1h: pressure.buyRatio1h,
              buyRatio6h: pressure.buyRatio6h,
              buyRatio24h: pressure.buyRatio24h,
              pressureScore,
              acceleration: pressure.acceleration,
            }),
          },
        });
      } catch {
        // Best-effort storage
      }
    }

    return {
      poolId,
      chain,
      tokenAddress,
      tokenSymbol,
      pressure1h: pressure.pressure1h,
      pressure6h: pressure.pressure6h,
      pressure24h: pressure.pressure24h,
      acceleration: pressure.acceleration,
      buyRatio1h: pressure.buyRatio1h,
      buyRatio6h: pressure.buyRatio6h,
      buyRatio24h: pressure.buyRatio24h,
      pressureScore,
      signalType,
      confidence,
      isAccelerationSignal,
      detectedAt: new Date(),
    };
  }

  /**
   * Scan multiple pools for buy/sell pressure signals.
   */
  async scanPools(
    chain: string,
    poolIds: string[],
    tokenAddresses: string[] = [],
    tokenSymbols: string[] = [],
  ): Promise<PressureScanResult> {
    const signals = await Promise.all(
      poolIds.map((poolId, i) =>
        this.analyzePressure(chain, poolId, tokenAddresses[i], tokenSymbols[i])
      )
    );

    const strongBuy = signals.filter(s => s.signalType === 'STRONG_BUY_PRESSURE');
    const strongSell = signals.filter(s => s.signalType === 'STRONG_SELL_PRESSURE');
    const accelerating = signals.filter(s => s.isAccelerationSignal);

    const bullishCount = signals.filter(s => s.pressureScore > 0).length;
    const bearishCount = signals.filter(s => s.pressureScore < 0).length;
    const neutralCount = signals.length - bullishCount - bearishCount;
    const avgPressureScore = signals.length > 0
      ? signals.reduce((s, sig) => s + sig.pressureScore, 0) / signals.length
      : 0;

    return {
      scanned: signals.length,
      signals,
      strongBuy,
      strongSell,
      accelerating,
      summary: {
        avgPressureScore,
        bullishCount,
        bearishCount,
        neutralCount,
      },
    };
  }

  /**
   * Get pressure for a token by finding its primary pool.
   * Helper method that combines pool search with pressure analysis.
   */
  async getTokenPressure(
    chain: string,
    tokenAddress: string,
    tokenSymbol: string = '',
  ): Promise<PressureSignal | null> {
    try {
      const pools = await dexPaprikaClient.searchPools({
        query: tokenAddress,
        chain,
        limit: 1,
      });

      if (pools.length === 0) return null;

      return this.analyzePressure(chain, pools[0].id, tokenAddress, tokenSymbol);
    } catch {
      return null;
    }
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const buySellPressureService = new BuySellPressureService();
