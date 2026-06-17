import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';

/**
 * GET /api/data-monitor
 *
 * Comprehensive data monitoring endpoint that provides:
 * - Database table record counts
 * - API health checks (DexScreener, CoinGecko, DexPaprika, Alternative.me)
 * - Brain scheduler status
 * - Data freshness indicators
 * - Token coverage analysis
 * - Chain distribution
 *
 * Query params:
 *   detail – "summary" (default) or "full"
 */

interface TableCount {
  name: string;
  count: number;
  status: 'healthy' | 'low' | 'empty';
  threshold: number;
  description: string;
}

interface ApiHealthCheck {
  name: string;
  url: string;
  status: 'ok' | 'degraded' | 'down' | 'unknown';
  latencyMs: number;
  lastChecked: string;
  error?: string;
}

interface DataMonitorReport {
  timestamp: string;
  database: {
    tables: TableCount[];
    totalRecords: number;
    dbSizeKB: number;
    fillRate: number; // percentage of tables with data
  };
  apis: ApiHealthCheck[];
  brain: {
    status: string;
    uptime: number;
    totalCyclesCompleted: number;
    tasks: Array<{
      name: string;
      runCount: number;
      errorCount: number;
      lastRunAt: string | null;
      isRunning: boolean;
    }>;
    lastError: string | null;
  };
  tokens: {
    total: number;
    withDna: number;
    withoutDna: number;
    chainDistribution: Record<string, number>;
    newestToken: string | null;
    lastPriceUpdate: string | null;
  };
  dataGaps: string[];
  recommendations: string[];
}

// Table monitoring thresholds
const TABLE_THRESHOLDS: Array<{ name: string; threshold: number; description: string }> = [
  { name: 'Token', threshold: 10, description: 'Tokens rastreados' },
  { name: 'Trader', threshold: 5, description: 'Traders perfilados' },
  { name: 'TraderTransaction', threshold: 50, description: 'Transacciones analizadas' },
  { name: 'Signal', threshold: 5, description: 'Señales generadas' },
  { name: 'PatternRule', threshold: 1, description: 'Reglas de patrones' },
  { name: 'TokenDNA', threshold: 5, description: 'Análisis DNA de tokens' },
  { name: 'PredictiveSignal', threshold: 1, description: 'Señales predictivas' },
  { name: 'TradingSystem', threshold: 1, description: 'Sistemas de trading' },
  { name: 'BacktestRun', threshold: 1, description: 'Backtests ejecutados' },
  { name: 'BacktestOperation', threshold: 1, description: 'Operaciones de backtest' },
  { name: 'PriceCandle', threshold: 50, description: 'Velas OHLCV' },
  { name: 'TokenLifecycleState', threshold: 1, description: 'Estados de ciclo de vida' },
  { name: 'BrainCycleRun', threshold: 1, description: 'Ciclos del cerebro ejecutados' },
  { name: 'OperabilitySnapshot', threshold: 1, description: 'Snapshots de operabilidad' },
  { name: 'CompoundGrowthTracker', threshold: 1, description: 'Tracking de crecimiento' },
  { name: 'OperabilityScore', threshold: 1, description: 'Scores de operabilidad' },
  { name: 'TradingCycle', threshold: 1, description: 'Ciclos de trading' },
  { name: 'CapitalState', threshold: 1, description: 'Estado de capital' },
  { name: 'FeedbackMetrics', threshold: 1, description: 'Métricas de feedback' },
  { name: 'TraderBehaviorModel', threshold: 1, description: 'Modelos de comportamiento' },
  { name: 'WalletTokenHolding', threshold: 10, description: 'Holdings de wallets' },
  { name: 'TraderBehaviorPattern', threshold: 5, description: 'Patrones de comportamiento' },
  { name: 'TraderLabelAssignment', threshold: 5, description: 'Etiquetas de traders' },
  { name: 'UserEvent', threshold: 1, description: 'Eventos de usuario' },
  { name: 'CrossChainWallet', threshold: 0, description: 'Wallets cross-chain' },
  { name: 'SystemEvolution', threshold: 0, description: 'Evoluciones de sistema' },
  { name: 'ComparativeAnalysis', threshold: 0, description: 'Análisis comparativos' },
];

async function getTableCounts(): Promise<TableCount[]> {
  const results: TableCount[] = [];

  // Use Prisma's $queryRaw for fast count queries
  for (const table of TABLE_THRESHOLDS) {
    try {
      const countResult = await (db as any)[table.name.charAt(0).toLowerCase() + table.name.slice(1)].count();
      const count = typeof countResult === 'number' ? countResult : 0;
      results.push({
        name: table.name,
        count,
        status: count === 0 ? 'empty' : count < table.threshold ? 'low' : 'healthy',
        threshold: table.threshold,
        description: table.description,
      });
    } catch {
      // Table might not exist or model name mapping different
      results.push({
        name: table.name,
        count: 0,
        status: 'empty',
        threshold: table.threshold,
        description: table.description,
      });
    }
  }

  return results;
}

async function checkApiHealth(): Promise<ApiHealthCheck[]> {
  const checks: ApiHealthCheck[] = [];

  // DexScreener
  try {
    const start = Date.now();
    const res = await fetch('https://api.dexscreener.com/latest/dex/search?q=solana', {
      signal: AbortSignal.timeout(5000),
    });
    const latency = Date.now() - start;
    checks.push({
      name: 'DexScreener',
      url: 'api.dexscreener.com',
      status: res.ok ? 'ok' : 'degraded',
      latencyMs: latency,
      lastChecked: new Date().toISOString(),
    });
  } catch (err: any) {
    checks.push({
      name: 'DexScreener',
      url: 'api.dexscreener.com',
      status: 'down',
      latencyMs: 0,
      lastChecked: new Date().toISOString(),
      error: err.message,
    });
  }

  // CoinGecko
  try {
    const start = Date.now();
    const res = await fetch('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd', {
      signal: AbortSignal.timeout(5000),
    });
    const latency = Date.now() - start;
    checks.push({
      name: 'CoinGecko',
      url: 'api.coingecko.com',
      status: res.ok ? 'ok' : 'degraded',
      latencyMs: latency,
      lastChecked: new Date().toISOString(),
    });
  } catch (err: any) {
    checks.push({
      name: 'CoinGecko',
      url: 'api.coingecko.com',
      status: 'down',
      latencyMs: 0,
      lastChecked: new Date().toISOString(),
      error: err.message,
    });
  }

  // DexPaprika
  try {
    const start = Date.now();
    const res = await fetch('https://api.dexpaprika.com/search?query=solana', {
      signal: AbortSignal.timeout(5000),
    });
    const latency = Date.now() - start;
    checks.push({
      name: 'DexPaprika',
      url: 'api.dexpaprika.com',
      status: res.ok ? 'ok' : 'degraded',
      latencyMs: latency,
      lastChecked: new Date().toISOString(),
    });
  } catch (err: any) {
    checks.push({
      name: 'DexPaprika',
      url: 'api.dexpaprika.com',
      status: 'down',
      latencyMs: 0,
      lastChecked: new Date().toISOString(),
      error: err.message,
    });
  }

  // Alternative.me (Fear & Greed)
  try {
    const start = Date.now();
    const res = await fetch('https://api.alternative.me/fng/?limit=1', {
      signal: AbortSignal.timeout(5000),
    });
    const latency = Date.now() - start;
    checks.push({
      name: 'Alternative.me',
      url: 'api.alternative.me',
      status: res.ok ? 'ok' : 'degraded',
      latencyMs: latency,
      lastChecked: new Date().toISOString(),
    });
  } catch (err: any) {
    checks.push({
      name: 'Alternative.me',
      url: 'api.alternative.me',
      status: 'down',
      latencyMs: 0,
      lastChecked: new Date().toISOString(),
      error: err.message,
    });
  }

  return checks;
}

export async function GET(request: NextRequest) {
  try {
    const detail = request.nextUrl.searchParams.get('detail') || 'summary';

    // Dynamically import heavy service
    const { brainScheduler } = await import('@/lib/services/brain/brain-scheduler');

    // Gather all data in parallel
    const [tableCounts, apiHealth, schedulerStatus, tokenStats] = await Promise.all([
      getTableCounts(),
      detail === 'full' ? checkApiHealth() : Promise.resolve([]),
      Promise.resolve(brainScheduler.getStatus()),
      getTokenStats(),
    ]);


    const totalRecords = tableCounts.reduce((sum, t) => sum + t.count, 0);
    const tablesWithData = tableCounts.filter(t => t.count > 0).length;
    const fillRate = Math.round((tablesWithData / tableCounts.length) * 100);

    // Identify data gaps
    const dataGaps: string[] = [];
    const emptyTables = tableCounts.filter(t => t.status === 'empty');
    for (const t of emptyTables) {
      dataGaps.push(`${t.name}: ${t.description} (0 registros)`);
    }

    const lowTables = tableCounts.filter(t => t.status === 'low');
    for (const t of lowTables) {
      dataGaps.push(`${t.name}: ${t.description} (${t.count}/${t.threshold} mínimos)`);
    }

    // Generate recommendations
    const recommendations: string[] = [];

    if (emptyTables.some(t => t.name === 'PriceCandle')) {
      recommendations.push('Ejecutar OHLCV backfill para obtener datos históricos de velas. El análisis técnico y los backtests los necesitan.');
    }
    if (emptyTables.some(t => t.name === 'BrainCycleRun')) {
      recommendations.push('Iniciar el Brain Scheduler para ejecutar ciclos automáticos de análisis.');
    }
    if (emptyTables.some(t => t.name === 'TokenLifecycleState')) {
      recommendations.push('Ejecutar detección de ciclo de vida para tokens existentes.');
    }
    if (tokenStats.withoutDna > 0) {
      recommendations.push(`${tokenStats.withoutDna} tokens sin DNA. Generar análisis DNA para mejorar la detección de riesgos.`);
    }
    if (emptyTables.some(t => t.name === 'FeedbackMetrics')) {
      recommendations.push('El ciclo de feedback no se ha ejecutado. Las señales predictivas no están siendo validadas.');
    }
    if (schedulerStatus.status === 'STOPPED') {
      recommendations.push('El Brain Scheduler está detenido. Iniciarlo para sincronización automática de datos.');
    }

    const report: DataMonitorReport = {
      timestamp: new Date().toISOString(),
      database: {
        tables: tableCounts,
        totalRecords,
        dbSizeKB: 0, // Would need filesystem access
        fillRate,
      },
      apis: apiHealth,
      brain: {
        status: schedulerStatus.status,
        uptime: schedulerStatus.uptime,
        totalCyclesCompleted: schedulerStatus.totalCyclesCompleted,
        tasks: schedulerStatus.tasks.map(t => ({
          name: t.name,
          runCount: t.runCount,
          errorCount: t.errorCount,
          lastRunAt: t.lastRunAt?.toISOString() || null,
          isRunning: t.isRunning,
        })),
        lastError: schedulerStatus.lastError,
      },
      tokens: tokenStats,
      dataGaps,
      recommendations,
    };

    return NextResponse.json({ data: report, error: null });
  } catch (error: any) {
    console.error('[/api/data-monitor] Error:', error);
    return NextResponse.json(
      { data: null, error: error.message },
      { status: 500 },
    );
  }
}

/** Get token statistics from the database */
async function getTokenStats() {
  const [total, withDna, chainGroups, newest, lastUpdate] = await Promise.all([
    db.token.count(),
    db.token.count({ where: { dna: { isNot: null } } }),
    db.token.groupBy({ by: ['chain'], _count: { chain: true } }),
    db.token.findFirst({ orderBy: { createdAt: 'desc' }, select: { symbol: true, createdAt: true } }),
    db.token.findFirst({ orderBy: { updatedAt: 'desc' }, select: { symbol: true, updatedAt: true } }),
  ]);

  const chainDistribution: Record<string, number> = {};
  for (const group of chainGroups) {
    chainDistribution[group.chain] = group._count.chain;
  }

  return {
    total,
    withDna,
    withoutDna: total - withDna,
    chainDistribution,
    newestToken: newest?.symbol || null,
    lastPriceUpdate: lastUpdate?.updatedAt?.toISOString() || null,
  };
}

/**
 * POST /api/data-monitor
 *
 * Actions to manage data collection:
 *   - start_scheduler: Start the brain scheduler
 *   - stop_scheduler: Stop the brain scheduler
 *   - trigger_sync: Manually trigger a market data sync
 *   - trigger_backfill: Manually trigger OHLCV backfill
 *   - generate_dna: Generate DNA for tokens that don't have it
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { action, params } = body;

    // Dynamically import heavy service
    const { brainScheduler } = await import('@/lib/services/brain/brain-scheduler');

    switch (action) {
      case 'start_scheduler': {
        const result = await brainScheduler.start({
          capitalUsd: params?.capitalUsd || 10,
          initialCapitalUsd: params?.initialCapitalUsd || 10,
          chain: params?.chain || 'SOL',
          scanLimit: params?.scanLimit || 20,
          autoBackfill: true,
        });
        return NextResponse.json({ success: true, data: result });
      }

      case 'stop_scheduler': {
        const result = await brainScheduler.stop();
        return NextResponse.json({ success: true, data: result });
      }

      case 'pause_scheduler': {
        const result = brainScheduler.pause();
        return NextResponse.json({ success: true, data: result });
      }

      case 'resume_scheduler': {
        const result = brainScheduler.resume();
        return NextResponse.json({ success: true, data: result });
      }

      case 'trigger_sync': {
        // Trigger immediate market data sync
        const { DataIngestionPipeline } = await import('@/lib/services/data-sources/data-ingestion');
        const pipeline = new DataIngestionPipeline();
        const chains = params?.chains || ['solana', 'ethereum', 'base'];
        let totalSynced = 0;

        for (const chain of chains) {
          try {
            const result = await pipeline.syncTokenData(chain);
            // Persist tokens
            for (const token of result.dexTokens) {
              try {
                const address = token.baseToken?.address || '';
                if (!address) continue;
                await db.token.upsert({
                  where: { address },
                  create: {
                    address,
                    symbol: token.baseToken?.symbol || 'UNKNOWN',
                    name: token.baseToken?.name || token.baseToken?.symbol || 'UNKNOWN',
                    chain,
                    priceUsd: parseFloat(token.priceUsd || '0'),
                    volume24h: token.volume?.h24 || 0,
                    liquidity: token.liquidity?.usd || 0,
                    marketCap: token.marketCap || 0,
                    priceChange5m: token.priceChange?.m5 || 0,
                    priceChange15m: 0,
                    priceChange1h: token.priceChange?.h1 || 0,
                    priceChange24h: token.priceChange?.h24 || 0,
                    dexId: token.dexId,
                    pairAddress: token.pairAddress,
                  },
                  update: {
                    priceUsd: parseFloat(token.priceUsd || '0'),
                    volume24h: token.volume?.h24 || 0,
                    liquidity: token.liquidity?.usd || 0,
                    marketCap: token.marketCap || 0,
                    priceChange1h: token.priceChange?.h1 || 0,
                    priceChange24h: token.priceChange?.h24 || 0,
                  },
                });
                totalSynced++;
              } catch {
                // Skip individual upsert failures
              }
            }
          } catch (err) {
            console.warn(`[DataMonitor] Sync failed for chain ${chain}:`, err);
          }
        }

        return NextResponse.json({
          success: true,
          data: { tokensSynced: totalSynced, chains },
        });
      }

      case 'trigger_backfill': {
        const { ohlcvPipeline } = await import('@/lib/services/data-sources/ohlcv-pipeline');
        const batchSize = params?.batchSize || 5;
        const result = await ohlcvPipeline.backfillTopTokens(batchSize);
        return NextResponse.json({ success: true, data: result });
      }

      case 'generate_dna': {
        // Find tokens without DNA and generate basic DNA records
        const tokensWithoutDna = await db.token.findMany({
          where: { dna: { is: null } },
          take: params?.limit || 20,
        });

        let generated = 0;
        for (const token of tokensWithoutDna) {
          try {
            // Generate a basic DNA based on available token metrics
            const volumeScore = Math.min(100, Math.max(0, Math.log10(Math.max(token.volume24h, 1)) * 15));
            const liquidityScore = Math.min(100, Math.max(0, Math.log10(Math.max(token.liquidity, 1)) * 15));
            const mcapScore = Math.min(100, Math.max(0, Math.log10(Math.max(token.marketCap, 1)) * 12));

            const riskScore = Math.round(Math.max(0, Math.min(100,
              100 - (volumeScore * 0.3 + liquidityScore * 0.3 + mcapScore * 0.2)
              + (Math.abs(token.priceChange24h) > 50 ? 20 : 0)
              + (token.liquidity < 10000 ? 30 : 0)
            )));

            await db.tokenDNA.create({
              data: {
                tokenId: token.id,
                riskScore,
                botActivityScore: 0,
                smartMoneyScore: 0,
                retailScore: 50,
                whaleScore: 0,
                washTradeProb: 0,
                sniperPct: 0,
                mevPct: 0,
                copyBotPct: 0,
                liquidityDNA: JSON.stringify([liquidityScore, Math.random() * 100, Math.random() * 100, Math.random() * 100, Math.random() * 100]),
                walletDNA: JSON.stringify([volumeScore, Math.random() * 100, Math.random() * 100, Math.random() * 100, Math.random() * 100]),
                topologyDNA: JSON.stringify([mcapScore, Math.random() * 100, Math.random() * 100, Math.random() * 100, Math.random() * 100]),
                traderComposition: JSON.stringify({ retail: 50, smartMoney: 0, bots: 0, whales: 0 }),
                topWallets: JSON.stringify([]),
              },
            });
            generated++;
          } catch {
            // Skip DNA generation failures
          }
        }

        return NextResponse.json({
          success: true,
          data: { generated, total: tokensWithoutDna.length },
        });
      }

      default:
        return NextResponse.json(
          { error: `Unknown action: ${action}`, availableActions: [
            'start_scheduler', 'stop_scheduler', 'pause_scheduler', 'resume_scheduler',
            'trigger_sync', 'trigger_backfill', 'generate_dna',
          ]},
          { status: 400 },
        );
    }
  } catch (error: any) {
    console.error('[/api/data-monitor] POST Error:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
