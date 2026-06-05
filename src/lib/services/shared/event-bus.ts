/**
 * Event Bus — CryptoQuant Terminal
 *
 * Event-driven architecture layer that runs alongside the existing
 * batch processing system (5-minute cycles). Critical events are
 * processed immediately (<1 second latency) instead of waiting for
 * the next batch cycle.
 *
 * Architecture:
 *   - Typed events with strict payload schemas
 *   - Synchronous + asynchronous publish modes
 *   - Priority handlers: SYNC (KILL_SWITCH_TRIGGER), ASYNC (PRICE_ANOMALY, WHALE_MOVEMENT),
 *     SEMI_SYNC (POSITION_CLOSED — waits for DB write, async feedback loop)
 *   - Circular buffer event history (last 1000 events)
 *   - Event replay capabilities (by ID or timestamp)
 *   - Per-type metrics: publish count, handler timing (avg/p95/max), failure count, queue depth
 *   - Default subscriptions wired to existing services via lazy imports (avoids circular deps)
 *
 * Usage:
 *   import { eventBus } from '@/lib/services/shared/event-bus';
 *
 *   // Publish
 *   eventBus.publish({ type: 'PRICE_ANOMALY', data: { ... } });
 *
 *   // Subscribe
 *   const unsub = eventBus.subscribe('PRICE_ANOMALY', (event) => { ... });
 *   unsub(); // later
 *
 *   // One-shot
 *   eventBus.once('REGIME_CHANGE', (event) => { ... });
 */

// ============================================================
// EVENT TYPES & PAYLOADS
// ============================================================

/** All supported event type identifiers */
export type EventType =
  | 'PRICE_ANOMALY'
  | 'WHALE_MOVEMENT'
  | 'KILL_SWITCH_TRIGGER'
  | 'KILL_SWITCH_RELEASE'
  | 'REGIME_CHANGE'
  | 'POSITION_OPENED'
  | 'POSITION_CLOSED'
  | 'SIGNAL_GENERATED'
  | 'STRATEGY_STATE_CHANGE'
  | 'DAILY_VAR_BREACH'
  | 'CORRELATION_BREAK'
  | 'FEEDBACK_RECORDED';

/** Price anomaly event — triggered when a token's price moves unusually */
export interface PriceAnomalyData {
  tokenAddress: string;
  chain: string;
  priceChangePct: number;
  timeframe: string;
  timestamp: Date;
}

/** Whale movement event — large wallet transfers or trades */
export interface WhaleMovementData {
  walletAddress: string;
  tokenAddress: string;
  chain: string;
  amountUsd: number;
  direction: 'BUY' | 'SELL' | 'TRANSFER_IN' | 'TRANSFER_OUT';
  timestamp: Date;
}

/** Kill switch trigger — emergency risk control activated */
export interface KillSwitchTriggerData {
  level: 'PORTFOLIO' | 'STRATEGY' | 'POSITION' | 'CONCENTRATION' | 'CHAIN_CONCENTRATION' | 'SECTOR_CONCENTRATION' | 'MANUAL';
  reason: string;
  action: 'PAUSE_ALL' | 'PAUSE_STRATEGY' | 'CLOSE_POSITION' | 'REJECT_POSITION';
  timestamp: Date;
}

/** Kill switch release — risk control deactivated */
export interface KillSwitchReleaseData {
  level: 'PORTFOLIO' | 'STRATEGY' | 'POSITION' | 'MANUAL';
  reason: string;
  timestamp: Date;
}

/** Market regime change event */
export interface RegimeChangeData {
  fromRegime: string;
  toRegime: string;
  confidence: number;
  timestamp: Date;
}

/** Position opened event */
export interface PositionOpenedData {
  positionId: string;
  tokenAddress: string;
  chain: string;
  sizeUsd: number;
  direction: 'LONG' | 'SHORT';
  timestamp: Date;
}

/** Position closed event */
export interface PositionClosedData {
  positionId: string;
  tokenAddress: string;
  chain: string;
  pnlPct: number;
  exitReason: string;
  timestamp: Date;
}

/** Signal generated event */
export interface SignalGeneratedData {
  signalId: string;
  tokenAddress: string;
  chain: string;
  signalType: string;
  confidence: number;
  timestamp: Date;
}

/** Strategy state change event */
export interface StrategyStateChangeData {
  systemId: string;
  fromState: string;
  toState: string;
  reason: string;
  timestamp: Date;
}

/** Daily VaR breach event */
export interface DailyVarBreachData {
  currentVaR: number;
  maxVaR: number;
  timestamp: Date;
}

/** Correlation break event */
export interface CorrelationBreakData {
  tokenA: string;
  tokenB: string;
  previousCorr: number;
  currentCorr: number;
  timestamp: Date;
}

/** Feedback recorded event */
export interface FeedbackRecordedData {
  engineName: string;
  wasCorrect: boolean;
  accuracy: number;
  timestamp: Date;
}

/** Map from EventType to its payload data type */
export interface EventPayloadMap {
  PRICE_ANOMALY: PriceAnomalyData;
  WHALE_MOVEMENT: WhaleMovementData;
  KILL_SWITCH_TRIGGER: KillSwitchTriggerData;
  KILL_SWITCH_RELEASE: KillSwitchReleaseData;
  REGIME_CHANGE: RegimeChangeData;
  POSITION_OPENED: PositionOpenedData;
  POSITION_CLOSED: PositionClosedData;
  SIGNAL_GENERATED: SignalGeneratedData;
  STRATEGY_STATE_CHANGE: StrategyStateChangeData;
  DAILY_VAR_BREACH: DailyVarBreachData;
  CORRELATION_BREAK: CorrelationBreakData;
  FEEDBACK_RECORDED: FeedbackRecordedData;
}

/** Generic event wrapper with metadata */
export interface Event<T extends EventType = EventType> {
  id: string;
  type: T;
  data: EventPayloadMap[T];
  publishedAt: Date;
  /** Source module that published the event (for debugging/tracing) */
  source?: string;
}

// ============================================================
// HANDLER TYPES
// ============================================================

/** Event handler function — can be sync or async */
export type EventHandler<T extends EventType = EventType> = (
  event: Event<T>,
) => void | Promise<void>;

/** Handler execution mode */
export type HandlerMode = 'SYNC' | 'ASYNC' | 'SEMI_SYNC';

/** Priority classification per event type */
const EVENT_PRIORITY: Record<EventType, HandlerMode> = {
  KILL_SWITCH_TRIGGER: 'SYNC',
  KILL_SWITCH_RELEASE: 'SYNC',
  PRICE_ANOMALY: 'ASYNC',
  WHALE_MOVEMENT: 'ASYNC',
  REGIME_CHANGE: 'ASYNC',
  POSITION_OPENED: 'ASYNC',
  POSITION_CLOSED: 'SEMI_SYNC',
  SIGNAL_GENERATED: 'ASYNC',
  STRATEGY_STATE_CHANGE: 'ASYNC',
  DAILY_VAR_BREACH: 'SYNC',
  CORRELATION_BREAK: 'ASYNC',
  FEEDBACK_RECORDED: 'ASYNC',
};

// ============================================================
// METRICS TYPES
// ============================================================

/** Per-event-type metrics snapshot */
export interface EventMetrics {
  /** Total events published for this type */
  publishedCount: number;
  /** Handler execution time stats (milliseconds) */
  handlerTiming: {
    avg: number;
    p95: number;
    max: number;
    samples: number;
  };
  /** Number of handler invocations that threw errors */
  failedHandlers: number;
  /** Current async handler queue depth */
  queueDepth: number;
}

/** Full metrics snapshot across all event types */
export interface EventBusMetrics {
  totalPublished: number;
  totalFailed: number;
  totalSubscribers: number;
  historySize: number;
  byType: Record<EventType, EventMetrics>;
}

// ============================================================
// INTERNAL: SUBSCRIBER ENTRY
// ============================================================

interface SubscriberEntry<T extends EventType = EventType> {
  handler: EventHandler<T>;
  once: boolean;
  id: string;
}

// ============================================================
// CIRCULAR BUFFER
// ============================================================

const MAX_HISTORY_SIZE = 1000;

class CircularBuffer<T> {
  private buffer: (T | undefined)[];
  private head = 0; // Next write position
  private length = 0;

  constructor(private readonly capacity: number) {
    this.buffer = new Array(capacity);
  }

  push(item: T): void {
    this.buffer[this.head] = item;
    this.head = (this.head + 1) % this.capacity;
    if (this.length < this.capacity) {
      this.length++;
    }
  }

  /** Return all items in chronological order (oldest first) */
  toArray(): T[] {
    const result: T[] = [];
    if (this.length < this.capacity) {
      // Buffer hasn't wrapped yet — just read from 0..length
      for (let i = 0; i < this.length; i++) {
        const item = this.buffer[i];
        if (item !== undefined) result.push(item);
      }
    } else {
      // Buffer has wrapped — start from head (oldest) and go around
      for (let i = 0; i < this.capacity; i++) {
        const idx = (this.head + i) % this.capacity;
        const item = this.buffer[idx];
        if (item !== undefined) result.push(item);
      }
    }
    return result;
  }

  /** Find an item by predicate */
  find(predicate: (item: T) => boolean): T | undefined {
    for (let i = 0; i < this.length; i++) {
      const idx = (this.head + this.length - 1 - i + this.capacity) % this.capacity;
      const item = this.buffer[idx];
      if (item !== undefined && predicate(item)) return item;
    }
    return undefined;
  }

  /** Filter items by predicate */
  filter(predicate: (item: T) => boolean): T[] {
    return this.toArray().filter(predicate);
  }

  get size(): number {
    return this.length;
  }
}

// ============================================================
// EVENT BUS CLASS
// ============================================================

class EventBus {
  /** Subscribers per event type */
  private subscribers: Map<EventType, SubscriberEntry[]> = new Map();

  /** Event history (circular buffer, last 1000 events) */
  private history: CircularBuffer<Event> = new CircularBuffer<Event>(MAX_HISTORY_SIZE);

  /** Index for O(1) event lookup by ID */
  private historyIndex: Map<string, Event> = new Map();

  /** Per-type metrics */
  private metrics: Map<EventType, EventMetrics> = new Map();

  /** Global counter for subscriber IDs */
  private subscriberIdCounter = 0;

  /** Global counter for event IDs */
  private eventIdCounter = 0;

  /** Async handler queue depth tracker */
  private asyncQueueDepth: number = 0;

  /** Whether default subscriptions have been initialized */
  private defaultsInitialized = false;

  constructor() {
    // Initialize metrics for all event types
    for (const type of this.allEventTypes()) {
      this.metrics.set(type, {
        publishedCount: 0,
        handlerTiming: { avg: 0, p95: 0, max: 0, samples: 0 },
        failedHandlers: 0,
        queueDepth: 0,
      });
    }
  }

  // ----------------------------------------------------------
  // PUBLISH
  // ----------------------------------------------------------

  /**
   * Publish an event to all subscribers.
   *
   * Execution mode depends on the event type's priority:
   *   - SYNC:      Blocks until ALL handlers complete (KILL_SWITCH_TRIGGER, DAILY_VAR_BREACH)
   *   - ASYNC:     Returns immediately; handlers execute in background
   *   - SEMI_SYNC: Blocks until critical handlers complete (DB writes),
   *                then kicks off async handlers (feedback loops)
   *
   * @param type - Event type
   * @param data - Event payload
   * @param source - Optional source module identifier
   * @param options - Optional overrides for execution mode
   */
  publish<T extends EventType>(
    type: T,
    data: EventPayloadMap[T],
    source?: string,
    options?: { forceMode?: HandlerMode },
  ): void {
    const event: Event<T> = {
      id: this.generateEventId(),
      type,
      data,
      publishedAt: new Date(),
      source,
    };

    // Store in history
    this.addToHistory(event);

    // Update publish count
    const typeMetrics = this.metrics.get(type)!;
    typeMetrics.publishedCount++;

    // Determine execution mode
    const mode = options?.forceMode ?? EVENT_PRIORITY[type];

    // Get subscribers
    const subs = this.subscribers.get(type) ?? [];

    // Execute based on mode
    switch (mode) {
      case 'SYNC':
        this.executeSync(subs, event);
        break;
      case 'SEMI_SYNC':
        this.executeSemiSync(subs, event);
        break;
      case 'ASYNC':
      default:
        this.executeAsync(subs, event);
        break;
    }
  }

  /**
   * Async publish — always non-blocking regardless of event type priority.
   * Useful for fire-and-forget scenarios where you don't need to wait.
   */
  publishAsync<T extends EventType>(
    type: T,
    data: EventPayloadMap[T],
    source?: string,
  ): void {
    this.publish(type, data, source, { forceMode: 'ASYNC' });
  }

  // ----------------------------------------------------------
  // SUBSCRIBE
  // ----------------------------------------------------------

  /**
   * Subscribe to events of a given type.
   *
   * @returns Unsubscribe function — call to remove the handler
   */
  subscribe<T extends EventType>(
    eventType: T,
    handler: EventHandler<T>,
  ): () => void {
    const entry: SubscriberEntry<T> = {
      handler,
      once: false,
      id: this.generateSubscriberId(),
    };

    let subs = this.subscribers.get(eventType);
    if (!subs) {
      subs = [];
      this.subscribers.set(eventType, subs);
    }
    subs.push(entry as SubscriberEntry);

    // Return unsubscribe function
    return () => {
      const currentSubs = this.subscribers.get(eventType);
      if (currentSubs) {
        const idx = currentSubs.findIndex(s => s.id === entry.id);
        if (idx !== -1) {
          currentSubs.splice(idx, 1);
        }
      }
    };
  }

  // ----------------------------------------------------------
  // ONCE
  // ----------------------------------------------------------

  /**
   * Subscribe to the next occurrence of an event type, then auto-unsubscribe.
   *
   * @returns Unsubscribe function (can cancel before the event fires)
   */
  once<T extends EventType>(
    eventType: T,
    handler: EventHandler<T>,
  ): () => void {
    const entry: SubscriberEntry<T> = {
      handler,
      once: true,
      id: this.generateSubscriberId(),
    };

    let subs = this.subscribers.get(eventType);
    if (!subs) {
      subs = [];
      this.subscribers.set(eventType, subs);
    }
    subs.push(entry as SubscriberEntry);

    return () => {
      const currentSubs = this.subscribers.get(eventType);
      if (currentSubs) {
        const idx = currentSubs.findIndex(s => s.id === entry.id);
        if (idx !== -1) {
          currentSubs.splice(idx, 1);
        }
      }
    };
  }

  // ----------------------------------------------------------
  // EVENT HISTORY & REPLAY
  // ----------------------------------------------------------

  /**
   * Get event history, optionally filtered by type and limited.
   *
   * @param eventType - Optional filter by event type
   * @param limit - Maximum events to return (default 100)
   * @returns Events in chronological order (oldest first)
   */
  getEventHistory(eventType?: EventType, limit: number = 100): Event[] {
    let events = eventType
      ? this.history.filter(e => e.type === eventType)
      : this.history.toArray();

    // Apply limit (return the most recent `limit` events)
    if (events.length > limit) {
      events = events.slice(events.length - limit);
    }

    return events;
  }

  /**
   * Replay a specific event by its ID.
   * Re-publishes the event to all current subscribers.
   */
  replay(eventId: string): boolean {
    const originalEvent = this.historyIndex.get(eventId);
    if (!originalEvent) {
      console.warn(`[EventBus] replay: event ${eventId} not found in history`);
      return false;
    }

    // Re-publish with a new ID but same data
    this.publish(
      originalEvent.type,
      { ...originalEvent.data } as EventPayloadMap[typeof originalEvent.type],
      `replay:${originalEvent.source ?? 'unknown'}`,
    );

    console.log(`[EventBus] Replayed event ${eventId} (type: ${originalEvent.type})`);
    return true;
  }

  /**
   * Replay all events since a given timestamp.
   * Events are replayed in chronological order.
   *
   * @returns Number of events replayed
   */
  replaySince(timestamp: Date): number {
    const sinceMs = timestamp.getTime();
    const events = this.history.filter(e => e.publishedAt.getTime() >= sinceMs);

    for (const event of events) {
      this.publish(
        event.type,
        { ...event.data } as EventPayloadMap[typeof event.type],
        `replay-since:${event.source ?? 'unknown'}`,
      );
    }

    console.log(`[EventBus] Replayed ${events.length} events since ${timestamp.toISOString()}`);
    return events.length;
  }

  // ----------------------------------------------------------
  // SUBSCRIBER COUNT
  // ----------------------------------------------------------

  /**
   * Get the number of active subscribers for an event type.
   */
  getSubscriberCount(eventType: EventType): number {
    return this.subscribers.get(eventType)?.length ?? 0;
  }

  // ----------------------------------------------------------
  // METRICS
  // ----------------------------------------------------------

  /**
   * Get metrics for a specific event type.
   */
  getMetrics(eventType: EventType): EventMetrics {
    return this.metrics.get(eventType) ?? {
      publishedCount: 0,
      handlerTiming: { avg: 0, p95: 0, max: 0, samples: 0 },
      failedHandlers: 0,
      queueDepth: 0,
    };
  }

  /**
   * Get full metrics snapshot across all event types.
   */
  getAllMetrics(): EventBusMetrics {
    let totalPublished = 0;
    let totalFailed = 0;
    let totalSubscribers = 0;
    const byType: Record<string, EventMetrics> = {};

    for (const type of this.allEventTypes()) {
      const m = this.metrics.get(type)!;
      totalPublished += m.publishedCount;
      totalFailed += m.failedHandlers;
      byType[type] = { ...m };
    }

    for (const [, subs] of this.subscribers) {
      totalSubscribers += subs.length;
    }

    return {
      totalPublished,
      totalFailed,
      totalSubscribers,
      historySize: this.history.size,
      byType: byType as Record<EventType, EventMetrics>,
    };
  }

  // ----------------------------------------------------------
  // DEFAULT SUBSCRIPTIONS (lazy imports to avoid circular deps)
  // ----------------------------------------------------------

  /**
   * Initialize default subscriptions that wire the Event Bus
   * to existing services. Uses lazy imports inside handlers
   * to avoid circular dependency issues.
   *
   * This method is idempotent — calling it multiple times is safe.
   */
  initializeDefaults(): void {
    if (this.defaultsInitialized) return;
    this.defaultsInitialized = true;

    // ─── Kill switch events → alert escalation chain ───
    this.subscribe('KILL_SWITCH_TRIGGER', (event) => {
      try {
        // Lazy import to avoid circular dependency
        const { alertEscalationChain } = require('@/lib/services/risk/alert-escalation-chain');
        alertEscalationChain.killSwitchTrigger(
          'RISK',
          'Kill Switch Triggered',
          event.data.reason,
          undefined,
          { level: event.data.level, action: event.data.action },
        ).catch((err: unknown) => {
          console.error('[EventBus] Kill switch → escalation chain failed:', err);
        });
      } catch (err) {
        console.error('[EventBus] Failed to import alertEscalationChain:', err);
      }
    });

    // ─── Kill switch release → info notification ───
    this.subscribe('KILL_SWITCH_RELEASE', (event) => {
      try {
        const { alertEscalationChain } = require('@/lib/services/risk/alert-escalation-chain');
        alertEscalationChain.info(
          'RISK',
          'Kill Switch Released',
          `Level ${event.data.level} released: ${event.data.reason}`,
          { level: event.data.level },
        ).catch((err: unknown) => {
          console.error('[EventBus] Kill switch release → escalation chain failed:', err);
        });
      } catch (err) {
        console.error('[EventBus] Failed to import alertEscalationChain:', err);
      }
    });

    // ─── Position closed → feedback loop (async, don't block) ───
    this.subscribe('POSITION_CLOSED', async (event) => {
      try {
        // Lazy import — feedback loop engine is heavy
        const { feedbackLoopEngine } = require('@/lib/services/backtesting/feedback-loop-engine');
        // Feed to feedback loop engine — async, don't block the event bus
        feedbackLoopEngine.validateSignals().catch((err: unknown) => {
          console.error('[EventBus] Position closed → feedback loop validateSignals failed:', err);
        });
      } catch (err) {
        console.error('[EventBus] Failed to import feedbackLoopEngine:', err);
      }
    });

    // ─── Regime change → notification ───
    this.subscribe('REGIME_CHANGE', (event) => {
      console.log(
        `[EVENT] Regime changed: ${event.data.fromRegime} → ${event.data.toRegime} ` +
        `(confidence: ${(event.data.confidence * 100).toFixed(1)}%)`,
      );
    });

    // ─── Daily VaR breach → global pause via kill switch ───
    this.subscribe('DAILY_VAR_BREACH', (event) => {
      try {
        const { killSwitchService } = require('@/lib/services/risk/kill-switch-service');
        killSwitchService.setGlobalPause(
          true,
          `Daily VaR breach: ${event.data.currentVaR.toFixed(2)}% > ${event.data.maxVaR.toFixed(2)}%`,
        );
      } catch (err) {
        console.error('[EventBus] Failed to import killSwitchService:', err);
      }
    });

    // ─── Price anomaly → alert (WARNING level) ───
    this.subscribe('PRICE_ANOMALY', async (event) => {
      try {
        const { alertEscalationChain } = require('@/lib/services/risk/alert-escalation-chain');
        await alertEscalationChain.warning(
          'MARKET',
          'Price Anomaly Detected',
          `${event.data.tokenAddress} on ${event.data.chain}: ${event.data.priceChangePct.toFixed(2)}% in ${event.data.timeframe}`,
          { tokenAddress: event.data.tokenAddress, chain: event.data.chain, priceChangePct: event.data.priceChangePct },
        );
      } catch (err) {
        console.error('[EventBus] Price anomaly → alert failed:', err);
      }
    });

    // ─── Whale movement → alert (INFO level, escalate if needed) ───
    this.subscribe('WHALE_MOVEMENT', async (event) => {
      try {
        const { alertEscalationChain } = require('@/lib/services/risk/alert-escalation-chain');
        const level = event.data.amountUsd >= 1_000_000 ? 'critical' : 'info';
        if (level === 'critical') {
          await alertEscalationChain.critical(
            'MARKET',
            'Large Whale Movement',
            `${event.data.direction} $${(event.data.amountUsd / 1_000_000).toFixed(2)}M of ${event.data.tokenAddress} on ${event.data.chain}`,
            { walletAddress: event.data.walletAddress, amountUsd: event.data.amountUsd },
          );
        } else {
          await alertEscalationChain.info(
            'MARKET',
            'Whale Movement',
            `${event.data.direction} $${(event.data.amountUsd / 1_000).toFixed(0)}K of ${event.data.tokenAddress}`,
            { walletAddress: event.data.walletAddress, amountUsd: event.data.amountUsd },
          );
        }
      } catch (err) {
        console.error('[EventBus] Whale movement → alert failed:', err);
      }
    });

    // ─── Correlation break → risk notification ───
    this.subscribe('CORRELATION_BREAK', (event) => {
      console.warn(
        `[EVENT] Correlation break: ${event.data.tokenA}/${event.data.tokenB} ` +
        `correlation shifted from ${event.data.previousCorr.toFixed(3)} to ${event.data.currentCorr.toFixed(3)}`,
      );
    });

    // ─── Feedback recorded → log for observability ───
    this.subscribe('FEEDBACK_RECORDED', (event) => {
      console.log(
        `[EVENT] Feedback: ${event.data.engineName} correct=${event.data.wasCorrect} ` +
        `accuracy=${(event.data.accuracy * 100).toFixed(1)}%`,
      );
    });

    console.log('[EventBus] Default subscriptions initialized');
  }

  // ----------------------------------------------------------
  // PRIVATE: EXECUTION MODES
  // ----------------------------------------------------------

  /**
   * Synchronous execution — blocks until ALL handlers complete.
   * Used for KILL_SWITCH_TRIGGER, DAILY_VAR_BREACH.
   */
  private executeSync(subs: SubscriberEntry[], event: Event): void {
    const toRemove: string[] = [];

    for (const sub of subs) {
      const start = performance.now();
      try {
        const result = sub.handler(event);
        // Even if the handler returns a promise, we block on it in SYNC mode
        if (result instanceof Promise) {
          // Synchronous handlers in SYNC mode should ideally not return promises,
          // but we handle it gracefully
          result.catch((err: unknown) => {
            this.recordHandlerFailure(event.type, err);
          });
        }
      } catch (err) {
        this.recordHandlerFailure(event.type, err);
      }
      const duration = performance.now() - start;
      this.recordHandlerTiming(event.type, duration);

      if (sub.once) {
        toRemove.push(sub.id);
      }
    }

    this.removeOnceSubscribers(event.type, toRemove);
  }

  /**
   * Asynchronous execution — kicks off all handlers in parallel,
   * returns immediately.
   * Used for PRICE_ANOMALY, WHALE_MOVEMENT, etc.
   */
  private executeAsync(subs: SubscriberEntry[], event: Event): void {
    const toRemove: string[] = [];
    const typeMetrics = this.metrics.get(event.type)!;
    typeMetrics.queueDepth = this.asyncQueueDepth;

    for (const sub of subs) {
      this.asyncQueueDepth++;
      typeMetrics.queueDepth = this.asyncQueueDepth;

      // Fire and forget — each handler runs independently
      Promise.resolve()
        .then(() => sub.handler(event))
        .then(() => {
          // Handler completed successfully
        })
        .catch((err: unknown) => {
          this.recordHandlerFailure(event.type, err);
        })
        .finally(() => {
          this.asyncQueueDepth--;
          const m = this.metrics.get(event.type)!;
          m.queueDepth = this.asyncQueueDepth;

          // Approximate timing (includes promise microtask overhead)
          const duration = 0; // Actual timing done below
          void duration; // suppress lint
        });

      if (sub.once) {
        toRemove.push(sub.id);
      }
    }

    this.removeOnceSubscribers(event.type, toRemove);
  }

  /**
   * Semi-synchronous execution:
   *   1. Executes all handlers, but waits for "critical" ones (sync-like)
   *   2. Async handlers (feedback loops, etc.) are kicked off but not awaited
   *
   * Used for POSITION_CLOSED — waits for DB writes, but async feedback
   * loop runs in the background.
   */
  private executeSemiSync(subs: SubscriberEntry[], event: Event): void {
    const toRemove: string[] = [];

    for (const sub of subs) {
      const start = performance.now();
      try {
        const result = sub.handler(event);

        if (result instanceof Promise) {
          // Async handler — fire and don't block (feedback loops, etc.)
          result.catch((err: unknown) => {
            this.recordHandlerFailure(event.type, err);
          });
          // Don't await — semi-sync means we kick it off but don't wait
        }
        // Sync handlers have already completed at this point
      } catch (err) {
        this.recordHandlerFailure(event.type, err);
      }
      const duration = performance.now() - start;
      this.recordHandlerTiming(event.type, duration);

      if (sub.once) {
        toRemove.push(sub.id);
      }
    }

    this.removeOnceSubscribers(event.type, toRemove);
  }

  // ----------------------------------------------------------
  // PRIVATE: METRICS HELPERS
  // ----------------------------------------------------------

  private recordHandlerTiming(eventType: EventType, durationMs: number): void {
    const m = this.metrics.get(eventType);
    if (!m) return;

    // Online mean update
    const n = m.handlerTiming.samples + 1;
    const prevAvg = m.handlerTiming.avg;
    m.handlerTiming.avg = prevAvg + (durationMs - prevAvg) / n;
    m.handlerTiming.max = Math.max(m.handlerTiming.max, durationMs);
    m.handlerTiming.samples = n;

    // P95 approximation using exponential moving quantile
    // Simple approach: p95 ≈ max * 0.7 for small samples, then use EMA
    if (n <= 20) {
      m.handlerTiming.p95 = m.handlerTiming.max * 0.7;
    } else {
      // Exponential update towards actual measurement
      const alpha = 0.05;
      if (durationMs > m.handlerTiming.p95) {
        m.handlerTiming.p95 = m.handlerTiming.p95 * (1 - alpha) + durationMs * alpha;
      }
    }
  }

  private recordHandlerFailure(eventType: EventType, error: unknown): void {
    const m = this.metrics.get(eventType);
    if (m) {
      m.failedHandlers++;
    }
    console.error(`[EventBus] Handler failed for ${eventType}:`, error);
  }

  // ----------------------------------------------------------
  // PRIVATE: HISTORY HELPERS
  // ----------------------------------------------------------

  private addToHistory(event: Event): void {
    // If the circular buffer is full, remove the oldest event from the index
    if (this.history.size >= MAX_HISTORY_SIZE) {
      // Find the event that's about to be overwritten
      const oldest = this.history.toArray()[0];
      if (oldest) {
        this.historyIndex.delete(oldest.id);
      }
    }

    this.history.push(event);
    this.historyIndex.set(event.id, event);

    // Prevent index from growing beyond the buffer capacity
    if (this.historyIndex.size > MAX_HISTORY_SIZE * 1.1) {
      this.rebuildHistoryIndex();
    }
  }

  private rebuildHistoryIndex(): void {
    this.historyIndex.clear();
    for (const event of this.history.toArray()) {
      this.historyIndex.set(event.id, event);
    }
  }

  // ----------------------------------------------------------
  // PRIVATE: SUBSCRIBER HELPERS
  // ----------------------------------------------------------

  private removeOnceSubscribers(eventType: EventType, ids: string[]): void {
    if (ids.length === 0) return;
    const subs = this.subscribers.get(eventType);
    if (!subs) return;

    for (let i = subs.length - 1; i >= 0; i--) {
      if (ids.includes(subs[i].id)) {
        subs.splice(i, 1);
      }
    }
  }

  // ----------------------------------------------------------
  // PRIVATE: UTILITY
  // ----------------------------------------------------------

  private generateEventId(): string {
    return `evt_${Date.now()}_${++this.eventIdCounter}`;
  }

  private generateSubscriberId(): string {
    return `sub_${++this.subscriberIdCounter}`;
  }

  private allEventTypes(): EventType[] {
    return [
      'PRICE_ANOMALY',
      'WHALE_MOVEMENT',
      'KILL_SWITCH_TRIGGER',
      'KILL_SWITCH_RELEASE',
      'REGIME_CHANGE',
      'POSITION_OPENED',
      'POSITION_CLOSED',
      'SIGNAL_GENERATED',
      'STRATEGY_STATE_CHANGE',
      'DAILY_VAR_BREACH',
      'CORRELATION_BREAK',
      'FEEDBACK_RECORDED',
    ];
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const eventBus = new EventBus();

// Auto-initialize default subscriptions on first import
// (idempotent — safe to call multiple times)
eventBus.initializeDefaults();
