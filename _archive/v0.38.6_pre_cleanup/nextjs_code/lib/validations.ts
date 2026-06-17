import { z } from 'zod';

// Common schemas
export const addressSchema = z.string().min(32).max(64).regex(/^[A-Za-z0-9]+$/, "Invalid address format");

export const chainSchema = z.enum(['SOL', 'ETH', 'BASE', 'ARB', 'OP', 'MATIC', 'BSC', 'ALL']);

export const timeframeSchema = z.enum(['1m', '5m', '10m', '15m', '30m', '1h', '4h', '1d']);

export const paginationSchema = z.object({
  page: z.coerce.number().int().positive().default(1),
  limit: z.coerce.number().int().positive().max(100).default(20),
});

export const tokenQuerySchema = z.object({
  chain: chainSchema.optional().default('ALL'),
  page: z.coerce.number().int().positive().default(1),
  limit: z.coerce.number().int().positive().max(5000).default(500),
  sort: z.enum(['volume24h', 'priceChange24h', 'marketCap', 'liquidity']).optional().default('volume24h'),
  order: z.enum(['asc', 'desc']).optional().default('desc'),
  search: z.string().optional(),
});

export const ohlcvQuerySchema = z.object({
  address: z.string().min(1),
  timeframe: timeframeSchema.optional().default('1h'),
  days: z.coerce.number().int().positive().max(365).default(7),
  chain: chainSchema.optional().default('ALL'),
});

export const brainActionSchema = z.object({
  action: z.enum([
    'start', 'stop', 'pause', 'resume', 'status', 'init',
    'analyze', 'run_cycle', 'run_pipeline', 'run_backtest',
    'force_signal', 'validate_signals', 'evolve_systems',
    'update_growth', 'cleanup_data', 'get_capacity'
  ]),
  params: z.record(z.string(), z.any()).optional(),
});

export const backtestCreateSchema = z.object({
  systemId: z.string().min(1),
  periodStart: z.string().min(1),
  periodEnd: z.string().min(1),
  initialCapital: z.number().positive().default(1000),
  mode: z.enum(['HISTORICAL', 'PAPER', 'FORWARD']).optional().default('HISTORICAL'),
  allocationMethod: z.string().optional().default('KELLY_MODIFIED'),
});

export const tradingSystemCreateSchema = z.object({
  name: z.string().min(1).max(100),
  description: z.string().max(500).optional(),
  category: z.enum([
    'ALPHA_HUNTER', 'SMART_MONEY', 'TECHNICAL',
    'DEFENSIVE', 'BOT_AWARE', 'DEEP_ANALYSIS',
    'MICRO_STRUCTURE', 'ADAPTIVE'
  ]),
  primaryTimeframe: timeframeSchema.optional().default('1h'),
  maxPositionPct: z.number().min(1).max(100).optional().default(5),
  stopLossPct: z.number().min(1).max(50).optional().default(15),
  takeProfitPct: z.number().min(1).max(200).optional().default(40),
});

export const signalQuerySchema = z.object({
  tokenId: z.string().optional(),
  type: z.string().optional(),
  direction: z.enum(['LONG', 'SHORT', 'NEUTRAL']).optional(),
  minConfidence: z.coerce.number().min(0).max(100).optional(),
  limit: z.coerce.number().int().positive().max(100).default(20),
});

// Helper to validate and return typed data or error response
export function validateOrError<T>(schema: z.ZodSchema<T>, data: unknown): { success: true; data: T } | { success: false; error: string } {
  const result = schema.safeParse(data);
  if (result.success) {
    return { success: true, data: result.data };
  }
  const errors = result.error.issues.map(e => `${e.path.join('.')}: ${e.message}`).join(', ');
  return { success: false, error: errors };
}
