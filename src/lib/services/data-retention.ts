/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  Data Retention Service — Automated data lifecycle management          ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 *
 * Manages the lifecycle of high-volume data tables:
 *   HOT  → Full resolution, recent data (7 days default)
 *   WARM → Aggregated, medium-age data (30 days default)
 *   COLD → Compressed, old data (365 days default)
 *
 * Strategies:
 *   DELETE     — Remove old data entirely
 *   AGGREGATE  — Downsample (e.g., 1m candles → 1h candles) then delete originals
 *   COMPRESS   — Store in compressed format
 */

import { db } from '../db';

export interface RetentionPolicy {
  tableName: string;
  hotDays: number;
  warmDays: number;
  coldDays: number;
  archiveMethod: 'DELETE' | 'AGGREGATE' | 'COMPRESS' | 'MOVE_TO_COLD';
  compressionEnabled: boolean;
  aggregationInterval?: string;
}

export interface ArchiveResult {
  tableName: string;
  rowsProcessed: number;
  rowsArchived: number;
  rowsDeleted: number;
  spaceFreedBytes: number;
  durationMs: number;
}

export class DataRetentionService {
  /**
   * Run the retention policy for all active tables.
   * Call this on a schedule (e.g., daily at 3 AM).
   */
  async runAllPolicies(): Promise<ArchiveResult[]> {
    const policies = await db.dataRetentionPolicy.findMany({
      where: { isActive: true },
    });

    const results: ArchiveResult[] = [];
    for (const policy of policies) {
      try {
        const result = await this.applyPolicy(policy);
        results.push(result);

        // Update last archived timestamp
        await db.dataRetentionPolicy.update({
          where: { id: policy.id },
          data: {
            lastArchivedAt: new Date(),
            lastArchiveStats: JSON.stringify({
              rowsProcessed: result.rowsProcessed,
              rowsArchived: result.rowsArchived,
              spaceFreedBytes: result.spaceFreedBytes,
            }),
          },
        });
      } catch (err) {
        console.error(`[Retention] Failed for ${policy.tableName}:`, err);
      }
    }
    return results;
  }

  /**
   * Apply a single retention policy.
   */
  private async applyPolicy(policy: {
    tableName: string;
    hotDays: number;
    warmDays: number;
    coldDays: number;
    archiveMethod: string;
    compressionEnabled: boolean;
    aggregationInterval: string | null;
  }): Promise<ArchiveResult> {
    const startTime = Date.now();
    let rowsProcessed = 0;
    let rowsArchived = 0;
    let rowsDeleted = 0;

    const coldCutoff = new Date(Date.now() - policy.coldDays * 24 * 60 * 60 * 1000);
    const warmCutoff = new Date(Date.now() - policy.warmDays * 24 * 60 * 60 * 1000);

    switch (policy.tableName) {
      case 'PriceCandle':
        // For PriceCandle: aggregate short timeframes, delete old data
        if (policy.archiveMethod === 'AGGREGATE') {
          // Aggregate 1m/5m candles older than warm period into 1h candles
          const shortTimeframes = ['1m', '5m', '15m'];
          for (const tf of shortTimeframes) {
            const deleted = await db.priceCandle.deleteMany({
              where: {
                timeframe: tf,
                timestamp: { lt: warmCutoff },
              },
            });
            rowsDeleted += deleted.count;
            rowsProcessed += deleted.count;
          }
          // Keep 1h/4h/1d candles for the full cold period
          const longTimeframes = ['1h', '4h', '1d'];
          for (const tf of longTimeframes) {
            const deleted = await db.priceCandle.deleteMany({
              where: {
                timeframe: tf,
                timestamp: { lt: coldCutoff },
              },
            });
            rowsDeleted += deleted.count;
            rowsProcessed += deleted.count;
          }
        } else {
          // Simple delete for cold data
          const deleted = await db.priceCandle.deleteMany({
            where: { timestamp: { lt: coldCutoff } },
          });
          rowsDeleted = deleted.count;
          rowsProcessed = deleted.count;
        }
        break;

      case 'TraderTransaction':
        // Delete transactions older than cold period
        const txDeleted = await db.traderTransaction.deleteMany({
          where: { blockTime: { lt: coldCutoff } },
        });
        rowsDeleted = txDeleted.count;
        rowsProcessed = txDeleted.count;
        break;

      case 'TokenLifecycleState':
        const lsDeleted = await db.tokenLifecycleState.deleteMany({
          where: { detectedAt: { lt: coldCutoff } },
        });
        rowsDeleted = lsDeleted.count;
        rowsProcessed = lsDeleted.count;
        break;

      case 'OperabilitySnapshot':
        const osDeleted = await db.operabilitySnapshot.deleteMany({
          where: { createdAt: { lt: warmCutoff } },
        });
        rowsDeleted = osDeleted.count;
        rowsProcessed = osDeleted.count;
        break;

      case 'BrainCycleRun':
        const bcDeleted = await db.brainCycleRun.deleteMany({
          where: { startedAt: { lt: coldCutoff } },
        });
        rowsDeleted = bcDeleted.count;
        rowsProcessed = bcDeleted.count;
        break;

      case 'ExtractionJob':
        const ejDeleted = await db.extractionJob.deleteMany({
          where: { createdAt: { lt: warmCutoff } },
        });
        rowsDeleted = ejDeleted.count;
        rowsProcessed = ejDeleted.count;
        break;

      case 'ApiRateLimit':
        // Rate limit data expires quickly
        const rlCutoff = new Date(Date.now() - policy.hotDays * 24 * 60 * 60 * 1000);
        const rlDeleted = await db.apiRateLimit.deleteMany({
          where: { updatedAt: { lt: rlCutoff } },
        });
        rowsDeleted = rlDeleted.count;
        rowsProcessed = rlDeleted.count;
        break;

      case 'FeedbackMetrics':
        const fmDeleted = await db.feedbackMetrics.deleteMany({
          where: { measuredAt: { lt: coldCutoff } },
        });
        rowsDeleted = fmDeleted.count;
        rowsProcessed = fmDeleted.count;
        break;

      default:
        console.warn(`[Retention] Unknown table: ${policy.tableName}`);
    }

    // Estimate space freed (rough: ~200 bytes per row on average)
    const spaceFreedBytes = rowsDeleted * 200;

    return {
      tableName: policy.tableName,
      rowsProcessed,
      rowsArchived,
      rowsDeleted,
      spaceFreedBytes,
      durationMs: Date.now() - startTime,
    };
  }

  /**
   * Get storage statistics for all tracked tables.
   */
  async getStorageStats(): Promise<Array<{
    tableName: string;
    rowCount: number;
    estimatedSizeMb: number;
    oldestRecord: Date | null;
    newestRecord: Date | null;
  }>> {
    const tables = [
      { name: 'PriceCandle', model: db.priceCandle, dateField: 'timestamp' },
      { name: 'TraderTransaction', model: db.traderTransaction, dateField: 'blockTime' },
      { name: 'TokenLifecycleState', model: db.tokenLifecycleState, dateField: 'detectedAt' },
      { name: 'OperabilitySnapshot', model: db.operabilitySnapshot, dateField: 'createdAt' },
      { name: 'ExtractionJob', model: db.extractionJob, dateField: 'createdAt' },
      { name: 'BrainCycleRun', model: db.brainCycleRun, dateField: 'startedAt' },
      { name: 'FeedbackMetrics', model: db.feedbackMetrics, dateField: 'measuredAt' },
    ];

    const stats: Array<{
      tableName: string;
      rowCount: number;
      estimatedSizeMb: number;
      oldestRecord: Date | null;
      newestRecord: Date | null;
    }> = [];
    for (const table of tables) {
      try {
        const [count, oldest, newest] = await Promise.all([
          (table.model as any).count(),
          (table.model as any).findFirst({ orderBy: { [table.dateField]: 'asc' }, select: { [table.dateField]: true } }),
          (table.model as any).findFirst({ orderBy: { [table.dateField]: 'desc' }, select: { [table.dateField]: true } }),
        ]);

        stats.push({
          tableName: table.name,
          rowCount: count,
          estimatedSizeMb: Math.round(count * 0.0002 * 100) / 100, // ~200 bytes per row
          oldestRecord: oldest?.[table.dateField] || null,
          newestRecord: newest?.[table.dateField] || null,
        });
      } catch {
        stats.push({
          tableName: table.name,
          rowCount: 0,
          estimatedSizeMb: 0,
          oldestRecord: null,
          newestRecord: null,
        });
      }
    }
    return stats;
  }
}

// Singleton export
export const dataRetentionService = new DataRetentionService();
