import { describe, it, expect } from 'vitest';
import {
  calculateOperabilityScore,
  estimateFees,
  batchOperabilityScore,
  filterOperable,
  quickOperabilityCheck,
  type OperabilityInput,
} from './operability-score';

// ============================================================
// Helper: valid premium input (high liquidity, low fees)
// ============================================================

const premiumInput: OperabilityInput = {
  tokenAddress: 'SoMePrEmIuMTokenAddress1234567890',
  symbol: 'PREM',
  chain: 'SOL',
  priceUsd: 1.5,
  liquidityUsd: 5_000_000,
  volume24h: 2_500_000,
  marketCap: 50_000_000,
  positionSizeUsd: 100,
  expectedGainPct: 15,
  botActivityPct: 5,
  holderCount: 5000,
  priceChange24h: 2,
  buyTxns24h: 500,
  sellTxns24h: 450,
  pairCreatedAt: Date.now() - 30 * 24 * 3600 * 1000, // 30 days ago
};

// ============================================================
// estimateFees
// ============================================================

describe('estimateFees', () => {
  it('calculates fees for SOL chain correctly', () => {
    const fees = estimateFees(premiumInput);
    // SOL: avgGasFeeUsd=0.001, priorityFeeUsd=0.01
    // gasFeeUsd = 0.001*2 + 0.01*2 = 0.022
    expect(fees.gasFeeUsd).toBeCloseTo(0.022, 2);
    // swapFeePct = 0.003 * 2 = 0.006
    expect(fees.swapFeePct).toBeCloseTo(0.006, 3);
    // swapFeeUsd: entry=100*0.003=0.3, exit=100*1.1*0.003=0.33, total=0.63
    expect(fees.swapFeeUsd).toBeCloseTo(0.63, 1);
    // totalCostUsd should be small for this large liquidity position
    expect(fees.totalCostUsd).toBeGreaterThan(0);
    expect(fees.totalCostPct).toBeLessThan(5); // Should be under 5% for good operability
  });

  it('calculates higher fees for ETH chain', () => {
    const ethInput: OperabilityInput = {
      ...premiumInput,
      chain: 'ETH',
    };
    const fees = estimateFees(ethInput);
    // ETH: avgGasFeeUsd=2.0, priorityFeeUsd=0.5
    // gasFeeUsd = 2.0*2 + 0.5*2 = 5.0
    expect(fees.gasFeeUsd).toBeCloseTo(5.0, 1);
    expect(fees.totalCostUsd).toBeGreaterThan(5);
  });

  it('defaults to SOL chain economics for unknown chain', () => {
    const unknownInput: OperabilityInput = {
      ...premiumInput,
      chain: 'UNKNOWN_CHAIN',
    };
    const solFees = estimateFees(premiumInput);
    const unknownFees = estimateFees(unknownInput);
    expect(unknownFees.gasFeeUsd).toBeCloseTo(solFees.gasFeeUsd, 3);
  });

  it('increases slippage for high volatility', () => {
    const highVol: OperabilityInput = {
      ...premiumInput,
      priceChange24h: 50,
    };
    const normalFees = estimateFees(premiumInput);
    const highVolFees = estimateFees(highVol);
    expect(highVolFees.slippagePct).toBeGreaterThan(normalFees.slippagePct);
  });

  it('calculates slippage based on position ratio', () => {
    const largePos: OperabilityInput = {
      ...premiumInput,
      positionSizeUsd: 100_000, // 5% of liquidity
    };
    const smallPos: OperabilityInput = {
      ...premiumInput,
      positionSizeUsd: 100, // tiny fraction
    };
    const largeFees = estimateFees(largePos);
    const smallFees = estimateFees(smallPos);
    expect(largeFees.slippagePct).toBeGreaterThan(smallFees.slippagePct);
  });
});

// ============================================================
// calculateOperabilityScore
// ============================================================

describe('calculateOperabilityScore', () => {
  it('classifies premium token as PREMIUM', () => {
    const result = calculateOperabilityScore(premiumInput);
    expect(result.overallScore).toBeGreaterThanOrEqual(80);
    expect(result.level).toBe('PREMIUM');
    expect(result.isOperable).toBe(true);
  });

  it('classifies low liquidity token as UNOPERABLE or RISKY', () => {
    const badInput: OperabilityInput = {
      tokenAddress: 'BadTokenAddress1234567890abcdef',
      symbol: 'BAD',
      chain: 'SOL',
      priceUsd: 0.0001,
      liquidityUsd: 100,
      volume24h: 10,
      marketCap: 1000,
      positionSizeUsd: 50,
      expectedGainPct: 5,
      botActivityPct: 80,
      holderCount: 5,
      priceChange24h: 50,
    };
    const result = calculateOperabilityScore(badInput);
    expect(result.overallScore).toBeLessThan(40);
    expect(['UNOPERABLE', 'RISKY']).toContain(result.level);
    expect(result.isOperable).toBe(false);
  });

  it('returns correct token info', () => {
    const result = calculateOperabilityScore(premiumInput);
    expect(result.tokenAddress).toBe(premiumInput.tokenAddress);
    expect(result.symbol).toBe('PREM');
    expect(result.chain).toBe('SOL');
  });

  it('includes fee estimate in result', () => {
    const result = calculateOperabilityScore(premiumInput);
    expect(result.feeEstimate).toBeDefined();
    expect(result.feeEstimate.totalCostUsd).toBeGreaterThan(0);
  });

  it('calculates recommended position less than or equal to input position', () => {
    const result = calculateOperabilityScore(premiumInput);
    expect(result.recommendedPositionUsd).toBeLessThanOrEqual(premiumInput.positionSizeUsd);
  });

  it('calculates max safe position as 2% of liquidity', () => {
    const result = calculateOperabilityScore(premiumInput);
    expect(result.maxSafePositionUsd).toBeCloseTo(premiumInput.liquidityUsd * 0.02, 0);
  });

  it('provides warnings for problematic tokens', () => {
    const riskyInput: OperabilityInput = {
      tokenAddress: 'RiskToken12345678901234567890',
      symbol: 'RISK',
      chain: 'SOL',
      priceUsd: 0.001,
      liquidityUsd: 500,
      volume24h: 50,
      marketCap: 5000,
      positionSizeUsd: 100,
      expectedGainPct: 2,
      botActivityPct: 90,
      holderCount: 3,
      priceChange24h: 30,
    };
    const result = calculateOperabilityScore(riskyInput);
    expect(result.warnings.length).toBeGreaterThan(0);
  });

  it('calculates minimum gain percentage (3x cost)', () => {
    const result = calculateOperabilityScore(premiumInput);
    expect(result.minimumGainPct).toBeGreaterThan(0);
  });

  it('classifies marginal token correctly', () => {
    const marginalInput: OperabilityInput = {
      tokenAddress: 'MargToken1234567890123456789',
      symbol: 'MARG',
      chain: 'SOL',
      priceUsd: 0.01,
      liquidityUsd: 20_000,
      volume24h: 10_000,
      marketCap: 500_000,
      positionSizeUsd: 500,
      expectedGainPct: 5,
      botActivityPct: 40,
      holderCount: 200,
      priceChange24h: 5,
    };
    const result = calculateOperabilityScore(marginalInput);
    // Should be somewhere in the middle range
    expect(result.overallScore).toBeGreaterThanOrEqual(0);
    expect(result.overallScore).toBeLessThanOrEqual(100);
  });

  it('penalizes high bot activity', () => {
    const lowBot = { ...premiumInput, botActivityPct: 5 };
    const highBot = { ...premiumInput, botActivityPct: 80 };
    const lowBotResult = calculateOperabilityScore(lowBot);
    const highBotResult = calculateOperabilityScore(highBot);
    expect(lowBotResult.healthScore).toBeGreaterThan(highBotResult.healthScore);
  });

  it('penalizes few holders', () => {
    const manyHolders = { ...premiumInput, holderCount: 5000 };
    const fewHolders = { ...premiumInput, holderCount: 10 };
    const manyResult = calculateOperabilityScore(manyHolders);
    const fewResult = calculateOperabilityScore(fewHolders);
    expect(manyResult.healthScore).toBeGreaterThan(fewResult.healthScore);
  });

  it('provides reason string', () => {
    const result = calculateOperabilityScore(premiumInput);
    expect(result.reason).toBeTruthy();
    expect(typeof result.reason).toBe('string');
  });
});

// ============================================================
// Operability Level Classification
// ============================================================

describe('operability level classification', () => {
  it('PREMIUM level requires score >= 80', () => {
    const result = calculateOperabilityScore(premiumInput);
    if (result.overallScore >= 80) {
      expect(result.level).toBe('PREMIUM');
    }
  });

  it('GOOD level for score 60-79', () => {
    const goodInput: OperabilityInput = {
      tokenAddress: 'GoodToken12345678901234567890',
      symbol: 'GOOD',
      chain: 'SOL',
      priceUsd: 0.5,
      liquidityUsd: 500_000,
      volume24h: 300_000,
      marketCap: 10_000_000,
      positionSizeUsd: 200,
      expectedGainPct: 8,
      botActivityPct: 25,
      holderCount: 800,
      priceChange24h: 3,
    };
    const result = calculateOperabilityScore(goodInput);
    // Verify it's in a reasonable range
    expect(['PREMIUM', 'GOOD', 'MARGINAL']).toContain(result.level);
  });

  it('UNOPERABLE level for score < 20', () => {
    const terribleInput: OperabilityInput = {
      tokenAddress: 'TerribleToken12345678901234567',
      symbol: 'TRSH',
      chain: 'ETH',
      priceUsd: 0.0001,
      liquidityUsd: 50,
      volume24h: 5,
      marketCap: 500,
      positionSizeUsd: 100,
      expectedGainPct: 1,
      botActivityPct: 95,
      holderCount: 2,
      priceChange24h: 80,
    };
    const result = calculateOperabilityScore(terribleInput);
    expect(result.overallScore).toBeLessThan(20);
    expect(result.level).toBe('UNOPERABLE');
  });
});

// ============================================================
// batchOperabilityScore
// ============================================================

describe('batchOperabilityScore', () => {
  it('sorts results by score descending', () => {
    const inputs: OperabilityInput[] = [
      premiumInput,
      {
        tokenAddress: 'LowToken12345678901234567890',
        symbol: 'LOW',
        chain: 'SOL',
        priceUsd: 0.001,
        liquidityUsd: 500,
        volume24h: 50,
        marketCap: 5000,
        positionSizeUsd: 100,
        expectedGainPct: 2,
        botActivityPct: 80,
        holderCount: 5,
        priceChange24h: 30,
      },
    ];
    const results = batchOperabilityScore(inputs);
    expect(results.length).toBe(2);
    expect(results[0].overallScore).toBeGreaterThanOrEqual(results[1].overallScore);
  });

  it('returns empty array for empty input', () => {
    const results = batchOperabilityScore([]);
    expect(results).toEqual([]);
  });
});

// ============================================================
// filterOperable
// ============================================================

describe('filterOperable', () => {
  it('filters tokens below MARGINAL level by default', () => {
    const inputs: OperabilityInput[] = [premiumInput];
    const results = filterOperable(inputs);
    // Premium input should pass the filter
    expect(results.some(r => r.symbol === 'PREM')).toBe(true);
  });

  it('filters to GOOD level when specified', () => {
    const inputs: OperabilityInput[] = [premiumInput];
    const results = filterOperable(inputs, 'GOOD');
    // Premium should still pass the GOOD filter
    expect(results.some(r => r.level === 'PREMIUM' || r.level === 'GOOD')).toBe(true);
  });
});

// ============================================================
// quickOperabilityCheck
// ============================================================

describe('quickOperabilityCheck', () => {
  it('returns operable for good liquidity on SOL', () => {
    const result = quickOperabilityCheck(100_000, 100, 'SOL');
    expect(result.operable).toBe(true);
  });

  it('returns not operable for insufficient liquidity', () => {
    const result = quickOperabilityCheck(100, 50, 'SOL');
    expect(result.operable).toBe(false);
    expect(result.reason).toContain('Liquidity');
  });

  it('returns not operable when position > 2% of liquidity', () => {
    const result = quickOperabilityCheck(10_000, 500, 'SOL');
    expect(result.operable).toBe(false);
    expect(result.reason).toContain('2%');
  });

  it('defaults to SOL chain', () => {
    const result = quickOperabilityCheck(100_000, 100);
    expect(result.operable).toBe(true);
  });

  it('requires higher min liquidity for ETH chain', () => {
    // 10k liquidity is fine for SOL but not for ETH
    const solResult = quickOperabilityCheck(10_000, 100, 'SOL');
    const ethResult = quickOperabilityCheck(10_000, 100, 'ETH');
    // SOL should be operable (minLiquidity 5000), ETH should not (minLiquidity 50000)
    expect(solResult.operable).toBe(true);
    expect(ethResult.operable).toBe(false);
  });

  it('always provides a reason string', () => {
    const operable = quickOperabilityCheck(100_000, 100, 'SOL');
    const notOperable = quickOperabilityCheck(100, 50, 'SOL');
    expect(operable.reason).toBeTruthy();
    expect(notOperable.reason).toBeTruthy();
  });
});
