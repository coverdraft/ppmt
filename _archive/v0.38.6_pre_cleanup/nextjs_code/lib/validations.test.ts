import { describe, it, expect } from 'vitest';
import {
  brainActionSchema,
  backtestCreateSchema,
  tradingSystemCreateSchema,
  validateOrError,
  chainSchema,
  timeframeSchema,
  paginationSchema,
  addressSchema,
  tokenQuerySchema,
  ohlcvQuerySchema,
  signalQuerySchema,
} from './validations';

// ============================================================
// brainActionSchema
// ============================================================

describe('brainActionSchema', () => {
  it('validates a correct "start" action', () => {
    const result = brainActionSchema.safeParse({ action: 'start' });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.action).toBe('start');
    }
  });

  it('validates a correct "analyze" action with params', () => {
    const result = brainActionSchema.safeParse({
      action: 'analyze',
      params: { tokenAddress: 'abc123' },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.action).toBe('analyze');
      expect(result.data.params).toEqual({ tokenAddress: 'abc123' });
    }
  });

  it('validates all valid action types', () => {
    const validActions = [
      'start', 'stop', 'pause', 'resume', 'status', 'init',
      'analyze', 'run_cycle', 'run_pipeline', 'run_backtest',
      'force_signal', 'validate_signals', 'evolve_systems',
      'update_growth', 'cleanup_data', 'get_capacity',
    ];
    for (const action of validActions) {
      const result = brainActionSchema.safeParse({ action });
      expect(result.success, `Expected "${action}" to be valid`).toBe(true);
    }
  });

  it('rejects an invalid action type', () => {
    const result = brainActionSchema.safeParse({ action: 'fly_to_moon' });
    expect(result.success).toBe(false);
  });

  it('rejects missing action field', () => {
    const result = brainActionSchema.safeParse({});
    expect(result.success).toBe(false);
  });

  it('allows optional params to be omitted', () => {
    const result = brainActionSchema.safeParse({ action: 'status' });
    expect(result.success).toBe(true);
  });
});

// ============================================================
// backtestCreateSchema
// ============================================================

describe('backtestCreateSchema', () => {
  const validBacktest = {
    systemId: 'sys-001',
    periodStart: '2025-01-01T00:00:00Z',
    periodEnd: '2025-06-01T00:00:00Z',
  };

  it('validates correct backtest data', () => {
    const result = backtestCreateSchema.safeParse(validBacktest);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.systemId).toBe('sys-001');
      expect(result.data.initialCapital).toBe(1000); // default
      expect(result.data.mode).toBe('HISTORICAL'); // default
    }
  });

  it('applies default values for optional fields', () => {
    const result = backtestCreateSchema.safeParse(validBacktest);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.initialCapital).toBe(1000);
      expect(result.data.mode).toBe('HISTORICAL');
    }
  });

  it('validates custom initialCapital and mode', () => {
    const result = backtestCreateSchema.safeParse({
      ...validBacktest,
      initialCapital: 5000,
      mode: 'PAPER',
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.initialCapital).toBe(5000);
      expect(result.data.mode).toBe('PAPER');
    }
  });

  it('rejects missing systemId', () => {
    const result = backtestCreateSchema.safeParse({
      periodStart: '2025-01-01T00:00:00Z',
      periodEnd: '2025-06-01T00:00:00Z',
    });
    expect(result.success).toBe(false);
  });

  it('rejects missing periodStart', () => {
    const result = backtestCreateSchema.safeParse({
      systemId: 'sys-001',
      periodEnd: '2025-06-01T00:00:00Z',
    });
    expect(result.success).toBe(false);
  });

  it('rejects missing periodEnd', () => {
    const result = backtestCreateSchema.safeParse({
      systemId: 'sys-001',
      periodStart: '2025-01-01T00:00:00Z',
    });
    expect(result.success).toBe(false);
  });

  it('rejects empty systemId', () => {
    const result = backtestCreateSchema.safeParse({
      ...validBacktest,
      systemId: '',
    });
    expect(result.success).toBe(false);
  });

  it('rejects invalid datetime format', () => {
    const result = backtestCreateSchema.safeParse({
      ...validBacktest,
      periodStart: 'not-a-date',
    });
    expect(result.success).toBe(false);
  });

  it('rejects negative initialCapital', () => {
    const result = backtestCreateSchema.safeParse({
      ...validBacktest,
      initialCapital: -100,
    });
    expect(result.success).toBe(false);
  });

  it('rejects invalid mode', () => {
    const result = backtestCreateSchema.safeParse({
      ...validBacktest,
      mode: 'INVALID',
    });
    expect(result.success).toBe(false);
  });
});

// ============================================================
// tradingSystemCreateSchema
// ============================================================

describe('tradingSystemCreateSchema', () => {
  const validSystem = {
    name: 'Alpha Hunter v1',
    category: 'ALPHA_HUNTER',
  };

  it('validates correct trading system data', () => {
    const result = tradingSystemCreateSchema.safeParse(validSystem);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.name).toBe('Alpha Hunter v1');
      expect(result.data.category).toBe('ALPHA_HUNTER');
    }
  });

  it('applies defaults for optional fields', () => {
    const result = tradingSystemCreateSchema.safeParse(validSystem);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.primaryTimeframe).toBe('1h');
      expect(result.data.maxPositionPct).toBe(5);
      expect(result.data.stopLossPct).toBe(15);
      expect(result.data.takeProfitPct).toBe(40);
    }
  });

  it('validates all valid categories', () => {
    const categories = [
      'ALPHA_HUNTER', 'SMART_MONEY', 'TECHNICAL',
      'DEFENSIVE', 'BOT_AWARE', 'DEEP_ANALYSIS',
      'MICRO_STRUCTURE', 'ADAPTIVE',
    ];
    for (const category of categories) {
      const result = tradingSystemCreateSchema.safeParse({
        name: 'Test System',
        category,
      });
      expect(result.success, `Expected "${category}" to be valid`).toBe(true);
    }
  });

  it('rejects missing name', () => {
    const result = tradingSystemCreateSchema.safeParse({
      category: 'ALPHA_HUNTER',
    });
    expect(result.success).toBe(false);
  });

  it('rejects empty name', () => {
    const result = tradingSystemCreateSchema.safeParse({
      name: '',
      category: 'ALPHA_HUNTER',
    });
    expect(result.success).toBe(false);
  });

  it('rejects name exceeding 100 chars', () => {
    const result = tradingSystemCreateSchema.safeParse({
      name: 'A'.repeat(101),
      category: 'ALPHA_HUNTER',
    });
    expect(result.success).toBe(false);
  });

  it('rejects missing category', () => {
    const result = tradingSystemCreateSchema.safeParse({
      name: 'Test System',
    });
    expect(result.success).toBe(false);
  });

  it('rejects invalid category', () => {
    const result = tradingSystemCreateSchema.safeParse({
      name: 'Test System',
      category: 'INVALID_CATEGORY',
    });
    expect(result.success).toBe(false);
  });

  it('accepts optional description', () => {
    const result = tradingSystemCreateSchema.safeParse({
      ...validSystem,
      description: 'A test trading system',
    });
    expect(result.success).toBe(true);
  });

  it('rejects description exceeding 500 chars', () => {
    const result = tradingSystemCreateSchema.safeParse({
      ...validSystem,
      description: 'A'.repeat(501),
    });
    expect(result.success).toBe(false);
  });
});

// ============================================================
// validateOrError
// ============================================================

describe('validateOrError', () => {
  it('returns success for valid data', () => {
    const result = validateOrError(brainActionSchema, { action: 'start' });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.action).toBe('start');
    }
  });

  it('returns error for invalid data', () => {
    const result = validateOrError(brainActionSchema, { action: 'invalid' });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error).toBeTruthy();
      expect(typeof result.error).toBe('string');
    }
  });

  it('returns error with path information', () => {
    const result = validateOrError(backtestCreateSchema, {});
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error).toContain('systemId');
    }
  });

  it('returns success for valid pagination with defaults', () => {
    const result = validateOrError(paginationSchema, {});
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.page).toBe(1);
      expect(result.data.limit).toBe(20);
    }
  });
});

// ============================================================
// Supporting schemas
// ============================================================

describe('chainSchema', () => {
  it('accepts valid chains', () => {
    const chains = ['SOL', 'ETH', 'BASE', 'ARB', 'OP', 'MATIC', 'BSC'];
    for (const chain of chains) {
      expect(chainSchema.safeParse(chain).success, `Expected "${chain}" to be valid`).toBe(true);
    }
  });

  it('rejects invalid chain', () => {
    expect(chainSchema.safeParse('INVALID').success).toBe(false);
  });
});

describe('timeframeSchema', () => {
  it('accepts valid timeframes', () => {
    const timeframes = ['1m', '5m', '15m', '1h', '4h', '1d'];
    for (const tf of timeframes) {
      expect(timeframeSchema.safeParse(tf).success, `Expected "${tf}" to be valid`).toBe(true);
    }
  });

  it('rejects invalid timeframe', () => {
    expect(timeframeSchema.safeParse('2h').success).toBe(false);
  });
});

describe('addressSchema', () => {
  it('accepts valid address (32-64 alphanumeric chars)', () => {
    const result = addressSchema.safeParse('A'.repeat(32));
    expect(result.success).toBe(true);
  });

  it('rejects address shorter than 32 chars', () => {
    const result = addressSchema.safeParse('A'.repeat(31));
    expect(result.success).toBe(false);
  });

  it('rejects address longer than 64 chars', () => {
    const result = addressSchema.safeParse('A'.repeat(65));
    expect(result.success).toBe(false);
  });

  it('rejects address with special characters', () => {
    const result = addressSchema.safeParse('A'.repeat(32) + '!');
    expect(result.success).toBe(false);
  });
});

describe('ohlcvQuerySchema', () => {
  it('validates with required fields and applies defaults', () => {
    const result = ohlcvQuerySchema.safeParse({ address: 'SoMeToken' });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.address).toBe('SoMeToken');
      expect(result.data.timeframe).toBe('1h');
      expect(result.data.days).toBe(7);
      expect(result.data.chain).toBe('SOL');
    }
  });

  it('rejects missing address', () => {
    const result = ohlcvQuerySchema.safeParse({});
    expect(result.success).toBe(false);
  });
});

describe('signalQuerySchema', () => {
  it('applies default limit', () => {
    const result = signalQuerySchema.safeParse({});
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.limit).toBe(20);
    }
  });

  it('validates direction filter', () => {
    const result = signalQuerySchema.safeParse({ direction: 'LONG' });
    expect(result.success).toBe(true);
  });

  it('rejects invalid direction', () => {
    const result = signalQuerySchema.safeParse({ direction: 'UP' });
    expect(result.success).toBe(false);
  });
});

describe('tokenQuerySchema', () => {
  it('applies all defaults', () => {
    const result = tokenQuerySchema.safeParse({});
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.chain).toBe('SOL');
      expect(result.data.page).toBe(1);
      expect(result.data.limit).toBe(20);
      expect(result.data.sort).toBe('volume24h');
      expect(result.data.order).toBe('desc');
    }
  });
});
