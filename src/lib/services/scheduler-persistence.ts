/**
 * Scheduler Persistence - CryptoQuant Terminal
 * Helper module for persisting and loading Brain Scheduler state to/from the database.
 *
 * This ensures the scheduler state survives server restarts by storing it
 * in the SchedulerState table via Prisma.
 *
 * Exports:
 *   - loadSchedulerState(): Reads from DB, returns parsed state or null
 *   - saveSchedulerState(state): Upserts the full state to DB
 *   - updateTaskState(taskName, data): Updates a single task's state within the taskStates JSON
 */

import { db } from '../db';

// ============================================================
// TYPES
// ============================================================

export interface PersistedSchedulerConfig {
  capitalUsd?: number;
  initialCapitalUsd?: number;
  chain?: string;
  scanLimit?: number;
  cycleIntervalMs?: number;
  marketSyncIntervalMs?: number;
  backfillIntervalMs?: number;
  signalValidationIntervalMs?: number;
  evolutionCheckIntervalMs?: number;
  capitalUpdateIntervalMs?: number;
  cleanupIntervalMs?: number;
  backfillBatchSize?: number;
  autoStartBrainCycle?: boolean;
  autoBackfill?: boolean;
}

export interface PersistedTaskState {
  lastRunAt: string | null;
  nextRunAt: string | null;
  runCount: number;
  errorCount: number;
  lastError: string | null;
  lastDurationMs: number;
  isRunning: boolean;
}

export interface PersistedSchedulerState {
  id: string;
  status: string;
  config: PersistedSchedulerConfig;
  lastCycleNumber: number;
  totalCycles: number;
  capitalUsd: number;
  initialCapitalUsd: number;
  chain: string;
  scanLimit: number;
  startedAt: Date | null;
  stoppedAt: Date | null;
  lastError: string | null;
  taskStates: Record<string, PersistedTaskState>;
  updatedAt: Date;
}

export interface SaveSchedulerStateInput {
  status: string;
  config: PersistedSchedulerConfig;
  lastCycleNumber: number;
  totalCycles: number;
  capitalUsd: number;
  initialCapitalUsd: number;
  chain: string;
  scanLimit: number;
  startedAt: Date | null;
  stoppedAt?: Date | null;
  lastError?: string | null;
  taskStates: Record<string, PersistedTaskState>;
}

// ============================================================
// CONSTANTS
// ============================================================

/** The singleton row ID used for the scheduler state record */
const SCHEDULER_STATE_ID = 'main';

// ============================================================
// PUBLIC API
// ============================================================

/**
 * Load the scheduler state from the database.
 * Returns the parsed state object, or null if no record exists (first run).
 *
 * On startup, if a record exists with status "RUNNING", the caller can
 * use the returned state to resume from where it left off.
 */
export async function loadSchedulerState(): Promise<PersistedSchedulerState | null> {
  try {
    const row = await db.schedulerState.findUnique({
      where: { id: SCHEDULER_STATE_ID },
    });

    if (!row) {
      return null;
    }

    const config: PersistedSchedulerConfig = JSON.parse(row.config || '{}');
    const taskStates: Record<string, PersistedTaskState> = JSON.parse(row.taskStates || '{}');

    return {
      id: row.id,
      status: row.status,
      config,
      lastCycleNumber: row.lastCycleNumber,
      totalCycles: row.totalCycles,
      capitalUsd: row.capitalUsd,
      initialCapitalUsd: row.initialCapitalUsd,
      chain: row.chain,
      scanLimit: row.scanLimit,
      startedAt: row.startedAt,
      stoppedAt: row.stoppedAt,
      lastError: row.lastError,
      taskStates,
      updatedAt: row.updatedAt,
    };
  } catch (error) {
    console.warn('[SchedulerPersistence] loadSchedulerState failed:', error);
    return null;
  }
}

/**
 * Save (upsert) the full scheduler state to the database.
 * Creates the record if it doesn't exist, updates it if it does.
 *
 * Called on:
 *   - Start (status = RUNNING)
 *   - Stop (status = STOPPED, stoppedAt = now)
 *   - Pause (status = PAUSED)
 *   - Error (status = ERROR)
 *   - Every cycle/task completion (status stays RUNNING, counts updated)
 */
export async function saveSchedulerState(state: SaveSchedulerStateInput): Promise<void> {
  try {
    await db.schedulerState.upsert({
      where: { id: SCHEDULER_STATE_ID },
      create: {
        id: SCHEDULER_STATE_ID,
        status: state.status,
        config: JSON.stringify(state.config),
        lastCycleNumber: state.lastCycleNumber,
        totalCycles: state.totalCycles,
        capitalUsd: state.capitalUsd,
        initialCapitalUsd: state.initialCapitalUsd,
        chain: state.chain,
        scanLimit: state.scanLimit,
        startedAt: state.startedAt,
        stoppedAt: state.stoppedAt ?? null,
        lastError: state.lastError ?? null,
        taskStates: JSON.stringify(state.taskStates),
      },
      update: {
        status: state.status,
        config: JSON.stringify(state.config),
        lastCycleNumber: state.lastCycleNumber,
        totalCycles: state.totalCycles,
        capitalUsd: state.capitalUsd,
        initialCapitalUsd: state.initialCapitalUsd,
        chain: state.chain,
        scanLimit: state.scanLimit,
        startedAt: state.startedAt,
        stoppedAt: state.stoppedAt ?? undefined,
        lastError: state.lastError ?? undefined,
        taskStates: JSON.stringify(state.taskStates),
        ...(state.status === 'STOPPED' ? { stoppedAt: state.stoppedAt ?? new Date() } : {}),
      },
    });
  } catch (error) {
    console.warn('[SchedulerPersistence] saveSchedulerState failed:', error);
  }
}

/**
 * Update a single task's state within the taskStates JSON field.
 * Reads the current state from DB, merges the update for the given task,
 * and writes it back.
 *
 * This is useful for incremental updates (e.g., after a single task run)
 * without needing to serialize the full scheduler state.
 *
 * @param taskName - The task identifier (e.g., "brain_cycle", "market_sync")
 * @param data - Partial task state data to merge into the existing state
 */
export async function updateTaskState(
  taskName: string,
  data: Partial<PersistedTaskState>
): Promise<void> {
  try {
    // Read current state
    const row = await db.schedulerState.findUnique({
      where: { id: SCHEDULER_STATE_ID },
    });

    const currentTaskStates: Record<string, PersistedTaskState> = row
      ? JSON.parse(row.taskStates || '{}')
      : {};

    // Merge the update
    const existingTask = currentTaskStates[taskName] || {
      lastRunAt: null,
      nextRunAt: null,
      runCount: 0,
      errorCount: 0,
      lastError: null,
      lastDurationMs: 0,
      isRunning: false,
    };

    currentTaskStates[taskName] = {
      ...existingTask,
      ...data,
    };

    // Write back
    if (row) {
      await db.schedulerState.update({
        where: { id: SCHEDULER_STATE_ID },
        data: {
          taskStates: JSON.stringify(currentTaskStates),
        },
      });
    } else {
      // No existing record — create a minimal one
      await db.schedulerState.create({
        data: {
          id: SCHEDULER_STATE_ID,
          status: 'STOPPED',
          config: '{}',
          taskStates: JSON.stringify(currentTaskStates),
        },
      });
    }
  } catch (error) {
    console.warn('[SchedulerPersistence] updateTaskState failed:', error);
  }
}
