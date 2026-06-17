/**
 * PPMT Real-Time Signal Service
 *
 * Connects Binance WebSocket (live candles) → PPMT Python (prediction) → Signal output.
 *
 * When a candle closes on Binance, this service:
 * 1. Receives the closed candle via WebSocket
 * 2. Calls the PPMT predict_live.py bridge
 * 3. Emits the signal to subscribers (Paper Trading Engine, Dashboard)
 *
 * This is the autonomous trading loop's signal source.
 */

import { EventEmitter } from 'events';

// ============================================================
// Types
// ============================================================

export interface PPMTSignal {
  symbol: string;
  timeframe: string;
  timestamp: number;
  current_price: number;
  asset_class: string;
  regime: {
    name: string;
    confidence: number;
  };
  current_pattern: string[];
  prediction: {
    direction: 'LONG' | 'SHORT' | 'FLAT';
    confidence: number;
    expected_move_pct: number;
    probability: number;
    pattern_break_prob: number;
    estimated_candles: number;
    entry_price: number | null;
    target_price: number | null;
    sl_price: number | null;
  };
  signal: {
    signal_type: string;
    confidence: number;
    entry_price: number | null;
    sl_price: number | null;
    tp_price: number | null;
    expected_move_pct: number;
    risk_reward_ratio: number;
    win_rate: number;
    quality_score: number;
    sizing_multiplier: number;
    matched_pattern: string[];
    historical_count: number;
  } | null;
  trie_stats: {
    patterns: number;
    levels_loaded: string[];
  };
}

export interface BinanceKline {
  eventType: string;
  eventTime: number;
  symbol: string;
  startTime: number;
  endTime: number;
  interval: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  isClosed: boolean; // true = candle closed
}

// ============================================================
// Binance WebSocket Client
// ============================================================

const BINANCE_WS_BASE = 'wss://stream.binance.com:9443/ws';

// Map our symbols to Binance format (BTC/USDT → btcusdt)
function toBinanceSymbol(symbol: string): string {
  return symbol.replace('/', '').toLowerCase();
}

// Map timeframe to Binance interval
function toBinanceInterval(tf: string): string {
  return tf; // Binance uses same format: 1m, 5m, 1h, etc.
}

interface Subscription {
  symbol: string;
  timeframe: string;
  ws: WebSocket | null;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
}

// ============================================================
// PPMT Real-Time Service
// ============================================================

export class PPMTRealtimeService extends EventEmitter {
  private subscriptions: Map<string, Subscription> = new Map();
  private signalCache: Map<string, PPMTSignal> = new Map();
  private isRunning = false;
  private predictQueue: Set<string> = new Set(); // deduplicate
  private predictTimer: ReturnType<typeof setTimeout> | null = null;

  // Config
  private predictCooldownMs: number;
  private autoPredictOnCandleClose: boolean;

  constructor(config?: {
    predictCooldownMs?: number;
    autoPredictOnCandleClose?: boolean;
  }) {
    super();
    this.predictCooldownMs = config?.predictCooldownMs || 5000;
    this.autoPredictOnCandleClose = config?.autoPredictOnCandleClose ?? true;
  }

  /**
   * Subscribe to live candles for a symbol+timeframe.
   * When a candle closes, runs PPMT prediction automatically.
   */
  subscribe(symbol: string, timeframe: string): void {
    const key = `${symbol}@${timeframe}`;
    if (this.subscriptions.has(key)) return;

    const sub: Subscription = {
      symbol,
      timeframe,
      ws: null,
      reconnectTimer: null,
    };
    this.subscriptions.set(key, sub);

    this.connectWebSocket(sub);
    this.isRunning = true;

    console.log(`[PPMT-RT] Subscribed to ${key}`);
  }

  /**
   * Unsubscribe from a symbol+timeframe.
   */
  unsubscribe(symbol: string, timeframe: string): void {
    const key = `${symbol}@${timeframe}`;
    const sub = this.subscriptions.get(key);
    if (!sub) return;

    if (sub.ws) {
      sub.ws.close();
      sub.ws = null;
    }
    if (sub.reconnectTimer) {
      clearTimeout(sub.reconnectTimer);
    }
    this.subscriptions.delete(key);
    this.signalCache.delete(key);

    console.log(`[PPMT-RT] Unsubscribed from ${key}`);
  }

  /**
   * Get the latest signal for a symbol+timeframe.
   */
  getLatestSignal(symbol: string, timeframe: string): PPMTSignal | undefined {
    return this.signalCache.get(`${symbol}@${timeframe}`);
  }

  /**
   * Manually trigger a prediction (for the Brain Cycle or dashboard).
   */
  async predict(symbol: string, timeframe: string, price?: number): Promise<PPMTSignal | null> {
    try {
      const symbolEncoded = encodeURIComponent(symbol);
      // Use refresh=1 to force fresh prediction (for candle close events)
      const response = await fetch(
        `/api/ppmt/predict-live?symbol=${symbolEncoded}&timeframe=${timeframe}&refresh=1`
      );

      const json = await response.json();
      if (json.success && json.data) {
        const signal = json.data as PPMTSignal;
        this.signalCache.set(`${symbol}@${timeframe}`, signal);
        this.emit('signal', signal);

        if (signal.signal) {
          this.emit('trade-signal', signal);
          console.log(
            `[PPMT-RT] ${signal.symbol} @ ${signal.timeframe}: ` +
            `${signal.signal.signal_type} conf=${(signal.signal.confidence * 100).toFixed(0)}% ` +
            `move=${signal.prediction.expected_move_pct >= 0 ? '+' : ''}${signal.prediction.expected_move_pct.toFixed(2)}%`
          );
        }

        return signal;
      }
      return null;
    } catch (err) {
      console.error(`[PPMT-RT] Prediction failed for ${symbol}:`, err);
      return null;
    }
  }

  /**
   * Stop all subscriptions.
   */
  stop(): void {
    for (const sub of this.subscriptions.values()) {
      if (sub.ws) sub.ws.close();
      if (sub.reconnectTimer) clearTimeout(sub.reconnectTimer);
    }
    this.subscriptions.clear();
    if (this.predictTimer) clearTimeout(this.predictTimer);
    this.isRunning = false;
    console.log('[PPMT-RT] Stopped');
  }

  /**
   * Get all active subscriptions.
   */
  getActiveSubscriptions(): Array<{ symbol: string; timeframe: string; hasSignal: boolean }> {
    return Array.from(this.subscriptions.entries()).map(([key, sub]) => ({
      symbol: sub.symbol,
      timeframe: sub.timeframe,
      hasSignal: this.signalCache.has(key),
    }));
  }

  // ============================================================
  // Private: WebSocket Management
  // ============================================================

  private connectWebSocket(sub: Subscription): void {
    const binanceSymbol = toBinanceSymbol(sub.symbol);
    const interval = toBinanceInterval(sub.timeframe);
    const streamName = `${binanceSymbol}@kline_${interval}`;
    const url = `${BINANCE_WS_BASE}/${streamName}`;

    try {
      const ws = new WebSocket(url);

      ws.onopen = () => {
        console.log(`[PPMT-RT] WebSocket connected: ${streamName}`);
        sub.ws = ws;

        // Run initial prediction on connect
        if (this.autoPredictOnCandleClose) {
          this.schedulePrediction(sub.symbol, sub.timeframe);
        }
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.e === 'kline') {
            const kline = data.k;
            const isClosed = kline.x; // Binance: x = is this kline closed?

            if (isClosed && this.autoPredictOnCandleClose) {
              const closePrice = parseFloat(kline.c);
              console.log(
                `[PPMT-RT] Candle closed: ${sub.symbol} @ ${sub.timeframe} ` +
                `close=${closePrice}`
              );
              this.schedulePrediction(sub.symbol, sub.timeframe, closePrice);
            }

            // Always emit candle update for dashboard
            this.emit('candle', {
              symbol: sub.symbol,
              timeframe: sub.timeframe,
              open: parseFloat(kline.o),
              high: parseFloat(kline.h),
              low: parseFloat(kline.l),
              close: parseFloat(kline.c),
              volume: parseFloat(kline.v),
              isClosed: kline.x,
              timestamp: kline.t,
            });
          }
        } catch (parseErr) {
          // Ignore non-JSON messages
        }
      };

      ws.onerror = (err) => {
        console.error(`[PPMT-RT] WebSocket error for ${streamName}:`, err);
      };

      ws.onclose = () => {
        console.log(`[PPMT-RT] WebSocket closed: ${streamName}`);
        sub.ws = null;
        // Reconnect after 5 seconds
        sub.reconnectTimer = setTimeout(() => {
          if (this.subscriptions.has(`${sub.symbol}@${sub.timeframe}`)) {
            console.log(`[PPMT-RT] Reconnecting ${streamName}...`);
            this.connectWebSocket(sub);
          }
        }, 5000);
      };
    } catch (err) {
      console.error(`[PPMT-RT] Failed to connect WebSocket ${streamName}:`, err);
      // Retry after 10 seconds
      sub.reconnectTimer = setTimeout(() => {
        if (this.subscriptions.has(`${sub.symbol}@${sub.timeframe}`)) {
          this.connectWebSocket(sub);
        }
      }, 10000);
    }
  }

  /**
   * Schedule a prediction with cooldown deduplication.
   * Multiple candle closes in quick succession won't trigger
   * multiple predictions — only the latest one runs.
   */
  private schedulePrediction(symbol: string, timeframe: string, price?: number): void {
    const key = `${symbol}@${timeframe}`;
    this.predictQueue.add(key);

    // Store price for when prediction runs
    if (price) {
      (this as Record<string, unknown>)[`_price_${key}`] = price;
    }

    if (!this.predictTimer) {
      this.predictTimer = setTimeout(() => {
        this.flushPredictQueue();
        this.predictTimer = null;
      }, this.predictCooldownMs);
    }
  }

  private async flushPredictQueue(): Promise<void> {
    const queue = Array.from(this.predictQueue);
    this.predictQueue.clear();

    for (const key of queue) {
      const [symbol, timeframe] = key.split('@');
      const priceKey = `_price_${key}`;
      const price = (this as Record<string, unknown>)[priceKey] as number | undefined;
      delete (this as Record<string, unknown>)[priceKey];

      await this.predict(symbol, timeframe, price);
    }
  }
}

// Singleton
export const ppmtRealtimeService = new PPMTRealtimeService({
  predictCooldownMs: 5000,
  autoPredictOnCandleClose: true,
});
