import { NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { dataQualityGate } from '@/lib/services/risk/data-quality-gate';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ─── Types ──────────────────────────────────────────────────────────────────

interface SectionCheck {
  status: 'ok' | 'degraded' | 'critical';
  message: string;
  details?: Record<string, unknown>;
}

interface HealthResponse {
  status: 'ok' | 'degraded' | 'critical';
  timestamp: string;
  uptime: number;
  database: SectionCheck;
  dataAvailability: SectionCheck;
  apiKeys: SectionCheck;
  services: SectionCheck;
  dataQualityGate: SectionCheck;
  recommendations: string[];
}

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Classify overall status — the worst sub-status wins */
function worstStatus(
  ...statuses: Array<'ok' | 'degraded' | 'critical'>
): 'ok' | 'degraded' | 'critical' {
  if (statuses.includes('critical')) return 'critical';
  if (statuses.includes('degraded')) return 'degraded';
  return 'ok';
}

// ─── Handler ────────────────────────────────────────────────────────────────

/**
 * GET /api/health
 *
 * Comprehensive system health check for the CryptoQuant Terminal.
 * Returns a single JSON object with:
 *   - Database connectivity
 *   - Data availability metrics (tokens, candles, systems, backtests, etc.)
 *   - API key status (configured / missing, never revealing values)
 *   - Service availability
 *   - Data quality gate (enough data for AI Strategy Manager?)
 *   - Overall status: "ok" | "degraded" | "critical"
 */
export async function GET() {
  const startMs = Date.now();

  // 1 ── Database connectivity
  const database = await checkDatabase();

  // 2 ── Data availability metrics (reuse DB counts when possible)
  const dataAvailability = await checkDataAvailability();

  // 3 ── API key status
  const apiKeys = checkApiKeys();

  // 4 ── Service status
  const services = await checkServices();

  // 5 ── Data quality gate
  const dataQualityGate = await checkDataQualityGate();

  // Overall status = worst of all sections
  const status = worstStatus(
    database.status,
    dataAvailability.status,
    apiKeys.status,
    services.status,
    dataQualityGate.status,
  );

  // Recommendations
  const recommendations = buildRecommendations(
    database,
    dataAvailability,
    apiKeys,
    services,
    dataQualityGate,
  );

  const response: HealthResponse = {
    status,
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
    database,
    dataAvailability,
    apiKeys,
    services,
    dataQualityGate,
    recommendations,
  };

  const elapsed = Date.now() - startMs;
  console.info(
    `[/api/health] Completed in ${elapsed}ms — status: ${status}`,
  );

  return NextResponse.json(response, {
    status: status === 'critical' ? 503 : 200,
  });
}

// ─── 1. Database Connectivity ───────────────────────────────────────────────

async function checkDatabase(): Promise<SectionCheck> {
  try {
    // Lightweight connectivity probe
    await db.$queryRaw`SELECT 1`;

    return {
      status: 'ok',
      message: 'SQLite database connected and responsive',
    };
  } catch (err) {
    return {
      status: 'critical',
      message: `Database unreachable: ${err instanceof Error ? err.message : 'unknown'}`,
    };
  }
}

// ─── 2. Data Availability Metrics ──────────────────────────────────────────

async function checkDataAvailability(): Promise<SectionCheck> {
  try {
    const [
      tokenCount,
      candleCount,
      tradingSystemCount,
      backtestRunCount,
    ] = await Promise.all([
      db.token.count(),
      db.priceCandle.count(),
      db.tradingSystem.count(),
      db.backtestRun.count(),
    ]);

    // Tokens whose candle count >= 50 (sufficient for backtesting)
    const candleTokenGroups = await db.priceCandle.groupBy({
      by: ['tokenAddress'],
      _count: { id: true },
    });
    const tokensWith50PlusCandles = candleTokenGroups.filter(
      (g) => g._count.id >= 50,
    ).length;

    const status: 'ok' | 'degraded' | 'critical' =
      tokenCount === 0
        ? 'critical'
        : candleCount === 0
          ? 'degraded'
          : 'ok';

    const message =
      tokenCount === 0
        ? 'No tokens in database — seed data first'
        : candleCount === 0
          ? 'Tokens exist but no OHLCV candles — run backfill'
          : `${tokenCount} tokens, ${candleCount} candles, ${tokensWith50PlusCandles} tokens with ≥50 candles`;

    return {
      status,
      message,
      details: {
        tokenCount,
        priceCandleCount: candleCount,
        tradingSystemCount,
        backtestRunCount,
        tokensWithSufficientCandles: tokensWith50PlusCandles,
        sufficientCandleThreshold: 50,
      },
    };
  } catch (err) {
    return {
      status: 'critical',
      message: `Data availability check failed: ${err instanceof Error ? err.message : 'unknown'}`,
    };
  }
}

// ─── 3. API Key Status ─────────────────────────────────────────────────────

function checkApiKeys(): SectionCheck {
  const etherscanKey = process.env.ETHERSCAN_API_KEY;
  const etherscanConfigured = !!etherscanKey && etherscanKey.length > 0;

  // CoinGecko free tier — no API key needed
  // Birdeye — eliminated from the project

  const details: Record<string, string> = {
    ETHERSCAN_API_KEY: etherscanConfigured ? 'configured' : 'missing',
    COINGECKO: 'free tier (no key needed)',
    BIRDEYE_API_KEY: 'eliminated',
  };

  const missingKeys: string[] = [];
  if (!etherscanConfigured) missingKeys.push('ETHERSCAN_API_KEY');

  const status: 'ok' | 'degraded' | 'critical' =
    missingKeys.length === 0 ? 'ok' : 'degraded'; // missing keys aren't critical

  const message =
    missingKeys.length === 0
      ? 'All API keys configured'
      : `Missing keys: ${missingKeys.join(', ')} — some features will be limited`;

  return {
    status,
    message,
    details,
  };
}

// ─── 4. Service Status ─────────────────────────────────────────────────────

async function checkServices(): Promise<SectionCheck> {
  const services: Record<string, 'available' | 'unavailable' | 'missing_key'> = {};

  // OHLCV Pipeline
  try {
    const mod = await import('@/lib/services/data-sources/ohlcv-pipeline');
    services['OHLCV Pipeline'] = mod.ohlcvPipeline ? 'available' : 'unavailable';
  } catch {
    services['OHLCV Pipeline'] = 'unavailable';
  }

  // Backtest Engine
  try {
    const mod = await import('@/lib/services/backtesting/backtesting-engine');
    services['Backtest Engine'] = mod.backtestingEngine ? 'available' : 'unavailable';
  } catch {
    services['Backtest Engine'] = 'unavailable';
  }

  // CoinGecko Client
  try {
    const mod = await import('@/lib/services/data-sources/coingecko-client');
    services['CoinGecko Client'] = mod.coinGeckoClient ? 'available' : 'unavailable';
  } catch {
    services['CoinGecko Client'] = 'unavailable';
  }

  // Etherscan Client
  try {
    const mod = await import('@/lib/services/data-sources/etherscan-client');
    const client = mod.etherscanClient;
    if (client) {
      services['Etherscan Client'] = client.hasApiKey() ? 'available' : 'missing_key';
    } else {
      services['Etherscan Client'] = 'unavailable';
    }
  } catch {
    services['Etherscan Client'] = 'unavailable';
  }

  const unavailableCount = Object.values(services).filter(
    s => s === 'unavailable',
  ).length;
  const missingKeyCount = Object.values(services).filter(
    s => s === 'missing_key',
  ).length;

  const status: 'ok' | 'degraded' | 'critical' =
    unavailableCount > 0
      ? 'degraded'
      : missingKeyCount > 0
        ? 'degraded'
        : 'ok';

  const message =
    unavailableCount > 0
      ? `${unavailableCount} service(s) unavailable`
      : missingKeyCount > 0
        ? `${missingKeyCount} service(s) missing API key`
        : 'All services operational';

  return {
    status,
    message,
    details: services,
  };
}

// ─── 5. Data Quality Gate ──────────────────────────────────────────────────

async function checkDataQualityGate(): Promise<SectionCheck> {
  try {
    const report = await dataQualityGate.assessQuality();

    const status: 'ok' | 'degraded' | 'critical' =
      report.level === 'EXCELLENT' || report.level === 'GOOD'
        ? 'ok'
        : report.level === 'MARGINAL'
          ? 'degraded'
          : 'critical';

    const message =
      report.isReady
        ? `Data quality: ${report.level} (score ${report.overallScore}/100) — ${report.tokenCount} tokens, ${report.totalCandles} candles, ${report.realCandlesPct}% real, ${report.coverageDays}d coverage`
        : `Data quality: ${report.level} (score ${report.overallScore}/100) — ${report.warnings[0] ?? 'insufficient data'}`;

    return {
      status,
      message,
      details: {
        overallScore: report.overallScore,
        level: report.level,
        isReady: report.isReady,
        tokenCount: report.tokenCount,
        tokensWithCandles: report.tokensWithCandles,
        tokensWithRealCandles: report.tokensWithRealCandles,
        tokensWithGeneratedCandles: report.tokensWithGeneratedCandles,
        totalCandles: report.totalCandles,
        realCandlesPct: report.realCandlesPct,
        coverageDays: report.coverageDays,
        timeframeCoverage: report.timeframeCoverage,
        warnings: report.warnings,
        recommendations: report.recommendations,
      },
    };
  } catch (err) {
    return {
      status: 'critical',
      message: `Data quality gate check failed: ${err instanceof Error ? err.message : 'unknown'}`,
    };
  }
}

// ─── Recommendations Builder ───────────────────────────────────────────────

function buildRecommendations(
  database: SectionCheck,
  dataAvailability: SectionCheck,
  apiKeys: SectionCheck,
  services: SectionCheck,
  dataQualityGate: SectionCheck,
): string[] {
  const recs: string[] = [];

  if (database.status === 'critical') {
    recs.push(
      'Database is unreachable — check DATABASE_URL and ensure SQLite file exists',
    );
  }

  if (dataAvailability.status === 'critical') {
    recs.push(
      'No tokens in database — run POST /api/seed to populate initial data',
    );
  } else if (dataAvailability.status === 'degraded') {
    recs.push(
      'Tokens exist but no OHLCV candles — run POST /api/brain/backfill or use the seed endpoint with automatic backfill',
    );
  }

  if (apiKeys.status === 'degraded') {
    const missing: string[] = [];
    if (!process.env.ETHERSCAN_API_KEY) missing.push('ETHERSCAN_API_KEY');
    if (missing.length > 0) {
      recs.push(
        `Configure missing API keys in .env: ${missing.join(', ')} — Etherscan free tier provides 100K calls/day`,
      );
    }
  }

  if (services.status === 'degraded') {
    const svcDetails = services.details as Record<string, string> | undefined;
    if (svcDetails) {
      const unavailable = Object.entries(svcDetails)
        .filter(([, v]) => v === 'unavailable')
        .map(([k]) => k);
      const missingKey = Object.entries(svcDetails)
        .filter(([, v]) => v === 'missing_key')
        .map(([k]) => k);
      if (unavailable.length > 0) {
        recs.push(
          `Services unavailable: ${unavailable.join(', ')} — check import errors or missing dependencies`,
        );
      }
      if (missingKey.length > 0) {
        recs.push(
          `Services missing API key: ${missingKey.join(', ')} — add the required key to .env`,
        );
      }
    }
  }

  if (dataQualityGate.status !== 'ok') {
    const gateDetails = dataQualityGate.details as { qualifyingTokens?: number; threshold?: number } | undefined;
    const current = gateDetails?.qualifyingTokens ?? 0;
    const needed = (gateDetails?.threshold ?? 5) - current;

    if (current === 0) {
      recs.push(
        'No tokens have sufficient candle data — run OHLCV backfill for at least 5 tokens (POST /api/brain/backfill)',
      );
    } else if (needed > 0) {
      recs.push(
        `Need ${needed} more token(s) with ≥50 candles — backfill OHLCV data for additional tokens to enable the AI Strategy Manager`,
      );
    }
  }

  // If everything is fine, give a positive recommendation
  if (recs.length === 0) {
    recs.push('System is fully operational — no action needed');
  }

  return recs;
}
