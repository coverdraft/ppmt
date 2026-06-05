import { NextRequest, NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { createId } from '@paralleldrive/cuid2';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Import structure validation
interface ImportData {
  version?: string;
  type?: string;
  exportedAt?: string;
  strategies?: unknown[];
  sessions?: unknown[];
  positions?: unknown[];
  trades?: unknown[];
  alertRules?: unknown[];
  webhookConfigs?: unknown[];
}

function generateNewId(): string {
  return createId();
}

/**
 * POST /api/import
 * Import data from JSON file
 * Query: ?mode=merge|replace — merge with existing or replace
 * Query: ?types=strategies,trades,config — what to import
 * Body: multipart form data with JSON file
 */
export async function POST(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const mode = searchParams.get('mode') || 'merge'; // merge | replace
    const typesParam = searchParams.get('types') || 'strategies,trades,config';
    const requestedTypes = typesParam.split(',').map((t) => t.trim()).filter(Boolean);

    // Parse the incoming data
    let importData: ImportData;

    const contentType = request.headers.get('content-type') || '';

    if (contentType.includes('multipart/form-data')) {
      const formData = await request.formData();
      const file = formData.get('file') as File | null;
      if (!file) {
        return NextResponse.json(
          { error: 'No file provided. Please upload a JSON file.' },
          { status: 400 },
        );
      }
      const text = await file.text();
      importData = JSON.parse(text);
    } else {
      // Accept raw JSON body as well
      importData = await request.json();
    }

    // Validate import structure
    if (!importData || typeof importData !== 'object') {
      return NextResponse.json(
        { error: 'Invalid import data: must be a JSON object' },
        { status: 400 },
      );
    }

    const result = {
      imported: { strategies: 0, trades: 0, sessions: 0, positions: 0, rules: 0, webhooks: 0 },
      skipped: 0,
      errors: [] as string[],
    };

    const isReplace = mode === 'replace';

    // ── Import Strategies (TradingSystems + BacktestRuns) ──
    if (requestedTypes.includes('strategies') && importData.strategies && Array.isArray(importData.strategies)) {
      if (isReplace) {
        // Delete existing in reverse dependency order
        await db.backtestOperation.deleteMany({});
        await db.backtestRun.deleteMany({});
        await db.tradingSystem.deleteMany({});
      }

      for (const strategy of importData.strategies) {
        try {
          const s = strategy as Record<string, unknown>;
          const existingId = s.id as string;

          // In merge mode, check for conflicts
          if (!isReplace && existingId) {
            const existing = await db.tradingSystem.findUnique({ where: { id: existingId } });
            if (existing) {
              result.skipped++;
              continue;
            }
          }

          const newId = isReplace ? existingId : generateNewId();
          const backtests = (s.backtests as Array<Record<string, unknown>>) || [];

          // Create the trading system
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          const { backtests: _bt, derivedSystems: _ds, parentSystem: _ps, operations: _ops, ...systemData } = s;

          await db.tradingSystem.create({
            data: {
              id: newId,
              name: (systemData.name as string) || 'Imported Strategy',
              description: (systemData.description as string) || null,
              category: (systemData.category as string) || 'TECHNICAL',
              icon: (systemData.icon as string) || '🎯',
              assetFilter: (systemData.assetFilter as string) || '{}',
              phaseConfig: (systemData.phaseConfig as string) || '{}',
              entrySignal: (systemData.entrySignal as string) || '{}',
              executionConfig: (systemData.executionConfig as string) || '{}',
              exitSignal: (systemData.exitSignal as string) || '{}',
              bigDataContext: (systemData.bigDataContext as string) || '{}',
              primaryTimeframe: (systemData.primaryTimeframe as string) || '1h',
              confirmTimeframes: (systemData.confirmTimeframes as string) || '[]',
              maxPositionPct: (systemData.maxPositionPct as number) ?? 5,
              maxOpenPositions: (systemData.maxOpenPositions as number) ?? 10,
              stopLossPct: (systemData.stopLossPct as number) ?? 15,
              takeProfitPct: (systemData.takeProfitPct as number) ?? 40,
              trailingStopPct: (systemData.trailingStopPct as number) ?? null,
              cashReservePct: (systemData.cashReservePct as number) ?? 20,
              allocationMethod: (systemData.allocationMethod as string) || 'KELLY_MODIFIED',
              allocationConfig: (systemData.allocationConfig as string) || '{}',
              isActive: (systemData.isActive as boolean) ?? false,
              isPaperTrading: (systemData.isPaperTrading as boolean) ?? false,
              version: (systemData.version as number) ?? 1,
              parentSystemId: (systemData.parentSystemId as string) || null,
              autoOptimize: (systemData.autoOptimize as boolean) ?? false,
              optimizationMethod: (systemData.optimizationMethod as string) || null,
              optimizationFreq: (systemData.optimizationFreq as string) || null,
              totalBacktests: (systemData.totalBacktests as number) ?? 0,
              bestSharpe: (systemData.bestSharpe as number) ?? 0,
              bestWinRate: (systemData.bestWinRate as number) ?? 0,
              bestPnlPct: (systemData.bestPnlPct as number) ?? 0,
              avgHoldTimeMin: (systemData.avgHoldTimeMin as number) ?? 0,
            },
          });

          // Import backtests for this strategy
          for (const bt of backtests) {
            try {
              const btNewId = generateNewId();
              const operations = (bt.operations as Array<Record<string, unknown>>) || [];
              // eslint-disable-next-line @typescript-eslint/no-unused-vars
              const { operations: _ops, ...btData } = bt;

              await db.backtestRun.create({
                data: {
                  id: btNewId,
                  systemId: newId,
                  mode: (btData.mode as string) || 'HISTORICAL',
                  periodStart: btData.periodStart ? new Date(btData.periodStart as string) : new Date(),
                  periodEnd: btData.periodEnd ? new Date(btData.periodEnd as string) : new Date(),
                  initialCapital: (btData.initialCapital as number) ?? 1000,
                  capitalAllocation: (btData.capitalAllocation as string) || '{}',
                  allocationMethod: (btData.allocationMethod as string) || 'KELLY_MODIFIED',
                  strategyMeta: (btData.strategyMeta as string) || '{}',
                  finalCapital: (btData.finalCapital as number) ?? 0,
                  totalPnl: (btData.totalPnl as number) ?? 0,
                  totalPnlPct: (btData.totalPnlPct as number) ?? 0,
                  totalTrades: (btData.totalTrades as number) ?? 0,
                  winTrades: (btData.winTrades as number) ?? 0,
                  lossTrades: (btData.lossTrades as number) ?? 0,
                  winRate: (btData.winRate as number) ?? 0,
                  avgWin: (btData.avgWin as number) ?? 0,
                  avgLoss: (btData.avgLoss as number) ?? 0,
                  profitFactor: (btData.profitFactor as number) ?? 0,
                  expectancy: (btData.expectancy as number) ?? 0,
                  maxDrawdown: (btData.maxDrawdown as number) ?? 0,
                  maxDrawdownPct: (btData.maxDrawdownPct as number) ?? 0,
                  sharpeRatio: (btData.sharpeRatio as number) ?? 0,
                  status: (btData.status as string) || 'COMPLETED',
                },
              });

              // Import operations
              for (const op of operations) {
                try {
                  await db.backtestOperation.create({
                    data: {
                      id: generateNewId(),
                      backtestId: btNewId,
                      systemId: newId,
                      tokenAddress: (op.tokenAddress as string) || '',
                      tokenSymbol: (op.tokenSymbol as string) || null,
                      chain: (op.chain as string) || 'SOL',
                      tokenPhase: (op.tokenPhase as string) || 'GROWTH',
                      tokenAgeMinutes: (op.tokenAgeMinutes as number) ?? 0,
                      operationType: (op.operationType as string) || 'SWING_LONG',
                      timeframe: (op.timeframe as string) || '1h',
                      entryPrice: (op.entryPrice as number) ?? 0,
                      entryTime: op.entryTime ? new Date(op.entryTime as string) : new Date(),
                      quantity: (op.quantity as number) ?? 0,
                      positionSizeUsd: (op.positionSizeUsd as number) ?? 0,
                      capitalAllocPct: (op.capitalAllocPct as number) ?? 0,
                    },
                  });
                } catch (opErr) {
                  result.errors.push(`BacktestOperation import error: ${opErr instanceof Error ? opErr.message : String(opErr)}`);
                }
              }
            } catch (btErr) {
              result.errors.push(`BacktestRun import error: ${btErr instanceof Error ? btErr.message : String(btErr)}`);
            }
          }

          result.imported.strategies++;
        } catch (sErr) {
          result.errors.push(`Strategy import error: ${sErr instanceof Error ? sErr.message : String(sErr)}`);
        }
      }
    }

    // ── Import Trades (Sessions + Positions + Trades) ──
    if (requestedTypes.includes('trades')) {
      if (isReplace) {
        await db.paperTradingTrade.deleteMany({});
        await db.paperTradingPosition.deleteMany({});
        await db.paperTradingSession.deleteMany({});
      }

      // Import sessions
      if (importData.sessions && Array.isArray(importData.sessions)) {
        for (const session of importData.sessions) {
          try {
            const ses = session as Record<string, unknown>;
            const existingId = ses.id as string;

            if (!isReplace && existingId) {
              const existing = await db.paperTradingSession.findUnique({ where: { id: existingId } });
              if (existing) {
                result.skipped++;
                continue;
              }
            }

            await db.paperTradingSession.create({
              data: {
                id: isReplace ? existingId : generateNewId(),
                status: (ses.status as string) || 'IDLE',
                initialCapital: (ses.initialCapital as number) ?? 10,
                currentCapital: (ses.currentCapital as number) ?? 10,
                peakCapital: (ses.peakCapital as number) ?? 10,
                chain: (ses.chain as string) || 'SOL',
                maxOpenPositions: (ses.maxOpenPositions as number) ?? 3,
                scanIntervalMs: (ses.scanIntervalMs as number) ?? 60000,
                feesPct: (ses.feesPct as number) ?? 0.3,
                slippagePct: (ses.slippagePct as number) ?? 0.5,
                minOperabilityScore: (ses.minOperabilityScore as number) ?? 50,
                autoFeedback: (ses.autoFeedback as boolean) ?? true,
                totalTrades: (ses.totalTrades as number) ?? 0,
                winningTrades: (ses.winningTrades as number) ?? 0,
                totalPnlUsd: (ses.totalPnlUsd as number) ?? 0,
                strategyName: (ses.strategyName as string) || null,
                startedAt: ses.startedAt ? new Date(ses.startedAt as string) : null,
              },
            });
            result.imported.sessions++;
          } catch (e) {
            result.errors.push(`Session import error: ${e instanceof Error ? e.message : String(e)}`);
          }
        }
      }

      // Import positions
      const positionIdMap = new Map<string, string>();
      if (importData.positions && Array.isArray(importData.positions)) {
        for (const position of importData.positions) {
          try {
            const pos = position as Record<string, unknown>;
            const oldId = pos.id as string;
            const newId = isReplace ? oldId : generateNewId();
            positionIdMap.set(oldId, newId);

            if (!isReplace && oldId) {
              const existing = await db.paperTradingPosition.findUnique({ where: { id: oldId } });
              if (existing) {
                result.skipped++;
                continue;
              }
            }

            await db.paperTradingPosition.create({
              data: {
                id: newId,
                runId: (pos.runId as string) || '',
                tokenSymbol: (pos.tokenSymbol as string) || '',
                tokenAddress: (pos.tokenAddress as string) || null,
                chain: (pos.chain as string) || 'SOL',
                direction: (pos.direction as string) || 'LONG',
                entryPrice: (pos.entryPrice as number) ?? 0,
                currentPrice: (pos.currentPrice as number) ?? 0,
                quantity: (pos.quantity as number) ?? 0,
                stopLoss: (pos.stopLoss as number) ?? null,
                takeProfit: (pos.takeProfit as number) ?? null,
                trailingStopPct: (pos.trailingStopPct as number) ?? 0,
                trailingActivated: (pos.trailingActivated as boolean) ?? false,
                highestPrice: (pos.highestPrice as number) ?? 0,
                sizeUsd: (pos.sizeUsd as number) ?? 0,
                pnlUsd: (pos.pnlUsd as number) ?? 0,
                pnlPct: (pos.pnlPct as number) ?? 0,
                mfe: (pos.mfe as number) ?? 0,
                mae: (pos.mae as number) ?? 0,
                operabilityScore: (pos.operabilityScore as number) ?? null,
                brainAnalysisJson: (pos.brainAnalysisJson as string) || '{}',
                strategyName: (pos.strategyName as string) || null,
                status: (pos.status as string) || 'OPEN',
                openedAt: pos.openedAt ? new Date(pos.openedAt as string) : new Date(),
                closedAt: pos.closedAt ? new Date(pos.closedAt as string) : null,
                exitReason: (pos.exitReason as string) || null,
              },
            });
            result.imported.positions++;
          } catch (e) {
            result.errors.push(`Position import error: ${e instanceof Error ? e.message : String(e)}`);
          }
        }
      }

      // Import trades
      if (importData.trades && Array.isArray(importData.trades)) {
        for (const trade of importData.trades) {
          try {
            const t = trade as Record<string, unknown>;
            const oldPositionId = (t.positionId as string) || '';
            const newPositionId = positionIdMap.get(oldPositionId) || oldPositionId;

            await db.paperTradingTrade.create({
              data: {
                id: generateNewId(),
                positionId: newPositionId,
                tokenSymbol: (t.tokenSymbol as string) || '',
                chain: (t.chain as string) || 'SOL',
                direction: (t.direction as string) || 'LONG',
                entryPrice: (t.entryPrice as number) ?? 0,
                exitPrice: (t.exitPrice as number) ?? 0,
                quantity: (t.quantity as number) ?? 0,
                sizeUsd: (t.sizeUsd as number) ?? 0,
                pnlUsd: (t.pnlUsd as number) ?? 0,
                pnlPct: (t.pnlPct as number) ?? 0,
                mfe: (t.mfe as number) ?? 0,
                mae: (t.mae as number) ?? 0,
                exitReason: (t.exitReason as string) || null,
                operabilityScore: (t.operabilityScore as number) ?? null,
                brainAnalysisJson: (t.brainAnalysisJson as string) || '{}',
                strategyName: (t.strategyName as string) || null,
                holdTimeMin: (t.holdTimeMin as number) ?? null,
                openedAt: t.openedAt ? new Date(t.openedAt as string) : new Date(),
                closedAt: t.closedAt ? new Date(t.closedAt as string) : new Date(),
              },
            });
            result.imported.trades++;
          } catch (e) {
            result.errors.push(`Trade import error: ${e instanceof Error ? e.message : String(e)}`);
          }
        }
      }
    }

    // ── Import Config (AlertRules + WebhookConfigs) ──
    if (requestedTypes.includes('config')) {
      if (isReplace) {
        await db.alert.deleteMany({});
        await db.alertRule.deleteMany({});
        await db.webhookConfig.deleteMany({});
      }

      // Import alert rules
      if (importData.alertRules && Array.isArray(importData.alertRules)) {
        for (const rule of importData.alertRules) {
          try {
            const r = rule as Record<string, unknown>;
            const existingId = r.id as string;

            if (!isReplace && existingId) {
              const existing = await db.alertRule.findUnique({ where: { id: existingId } });
              if (existing) {
                result.skipped++;
                continue;
              }
            }

            await db.alertRule.create({
              data: {
                id: isReplace ? existingId : generateNewId(),
                name: (r.name as string) || 'Imported Rule',
                enabled: (r.enabled as boolean) ?? true,
                category: (r.category as string) || 'SYSTEM',
                condition: (r.condition as string) || '{}',
                severity: (r.severity as string) || 'INFO',
                channels: (r.channels as string) || '["IN_APP"]',
                cooldownMin: (r.cooldownMin as number) ?? 5,
                lastTriggeredAt: r.lastTriggeredAt ? new Date(r.lastTriggeredAt as string) : null,
              },
            });
            result.imported.rules++;
          } catch (e) {
            result.errors.push(`AlertRule import error: ${e instanceof Error ? e.message : String(e)}`);
          }
        }
      }

      // Import webhook configs
      if (importData.webhookConfigs && Array.isArray(importData.webhookConfigs)) {
        for (const webhook of importData.webhookConfigs) {
          try {
            const w = webhook as Record<string, unknown>;
            const existingId = w.id as string;

            if (!isReplace && existingId) {
              const existing = await db.webhookConfig.findUnique({ where: { id: existingId } });
              if (existing) {
                result.skipped++;
                continue;
              }
            }

            await db.webhookConfig.create({
              data: {
                id: isReplace ? existingId : generateNewId(),
                name: (w.name as string) || 'Imported Webhook',
                url: (w.url as string) || '',
                secret: (w.secret as string) || null,
                events: (w.events as string) || '[]',
                enabled: (w.enabled as boolean) ?? true,
                lastDeliveryAt: w.lastDeliveryAt ? new Date(w.lastDeliveryAt as string) : null,
                lastStatus: (w.lastStatus as string) || null,
                failureCount: (w.failureCount as number) ?? 0,
              },
            });
            result.imported.webhooks++;
          } catch (e) {
            result.errors.push(`WebhookConfig import error: ${e instanceof Error ? e.message : String(e)}`);
          }
        }
      }
    }

    return NextResponse.json({
      success: true,
      mode,
      imported: result.imported,
      skipped: result.skipped,
      errors: result.errors,
    });
  } catch (error) {
    console.error('[API /import] Error:', error);
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : 'Failed to import data',
        imported: { strategies: 0, trades: 0, sessions: 0, positions: 0, rules: 0, webhooks: 0 },
        skipped: 0,
        errors: [error instanceof Error ? error.message : 'Unknown error'],
      },
      { status: 500 },
    );
  }
}
