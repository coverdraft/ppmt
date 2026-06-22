import { useRef, useState, useCallback } from 'react';
import { buildAuthPayload } from '../utils/credentialCrypto';
import type {
  WSMessage,
  CandleMessage,
  BrainUpdateMessage,
  PositionUpdateMessage,
  PositionStateWire,
} from '../types/ws';
import type { PositionState, PositionStatus } from '../types/position';

/**
 * Convert wire format (snake_case from Python) to the TS PositionState.
 */
function wireToPosition(wire: PositionStateWire): PositionState {
  return {
    symbol: wire.symbol,
    direction: wire.direction,
    status: wire.status as PositionStatus,
    entry_price: wire.entry_price,
    entry_time: wire.entry_time,
    size_usdt: wire.size_usdt,
    current_sl: wire.current_sl,
    current_tp: wire.current_tp,
    catastrophic_sl: wire.catastrophic_sl,
    expected_sequence: wire.expected_sequence,
    sequence_index: wire.sequence_index,
    close_price: wire.close_price ?? undefined,
    close_reason: wire.close_reason ?? undefined,
    pnl_pct: wire.pnl_pct ?? undefined,
    pnl_usdt: wire.pnl_usdt ?? undefined,
  };
}

export type LiveConnectionStatus = 'idle' | 'connecting' | 'authenticating' | 'connected' | 'error';

export interface LiveTradingState {
  status: LiveConnectionStatus;
  candles: CandleMessage['data'][];
  brainUpdate: BrainUpdateMessage['data'] | null;
  tickerPrice: number | null;  // v2.1: Real-time price from Binance ticker
  position: PositionState | null;
  error: string | null;
}

/**
 * useLiveTrading — WebSocket hook for LIVE TRADING mode (MEXC Futures).
 *
 * v0.47.0: ENTREGABLE 8 — Dynamic capital allocation (allocatedUsdt).
 *
 * State machine: idle → connecting → authenticating → connected
 *                                                    → error
 *
 * Auth flow:
 *   1. Connect WS
 *   2. Send encrypted auth payload
 *   3. Wait for auth_ok
 *   4. Send config message with allocated_usdt
 *
 * Usage:
 *   const { state, connect, disconnect } = useLiveTrading(symbol, timeframe);
 *   connect(sessionPassword, apiKey, apiSecret, allocatedUsdt);
 */
export function useLiveTrading(symbol: string | null, timeframe: string) {
  const [state, setState] = useState<LiveTradingState>({
    status: 'idle',
    candles: [],
    brainUpdate: null,
    tickerPrice: null,
    position: null,
    error: null,
  });

  const wsRef = useRef<WebSocket | null>(null);

  /** Disconnect and reset state */
  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setState({
      status: 'idle',
      candles: [],
      brainUpdate: null,
      tickerPrice: null,
      position: null,
      error: null,
    });
  }, []);

  /** Open WS, send encrypted auth, then send config with allocated_usdt */
  const connect = useCallback(
    (sessionPassword: string, apiKey: string, apiSecret: string, allocatedUsdt: number = 50, customBaseUrl?: string) => {
      if (!symbol) return;

      // Close any existing connection
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      setState({
        status: 'connecting',
        candles: [],
        brainUpdate: null,
        tickerPrice: null,
        position: null,
        error: null,
      });

      const urlSymbol = symbol.replace(/\//g, '-');
      const wsUrl = `ws://localhost:8000/ws/live-trading/${urlSymbol}/${timeframe}`;
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        // WS is open → send encrypted auth
        setState((prev) => ({ ...prev, status: 'authenticating' }));

        const authPayload = buildAuthPayload(apiKey, apiSecret, sessionPassword);
        ws.send(JSON.stringify(authPayload));
      };

      ws.onmessage = (event) => {
        try {
          const msg: WSMessage = JSON.parse(event.data);

          switch (msg.type) {
            case 'auth_ok':
              setState((prev) => ({ ...prev, status: 'connected', error: null }));
              // ── ENTREGABLE 8: Send config with allocated_usdt immediately after auth ──
              // ── ENTREGABLE 11: Also send custom_base_url if provided (proxy/VPN) ──
              const configMsg: Record<string, unknown> = {
                type: 'config',
                allocated_usdt: allocatedUsdt,
              };
              if (customBaseUrl) {
                configMsg.custom_base_url = customBaseUrl;
              }
              ws.send(JSON.stringify(configMsg));
              break;

            case 'candle':
              // Coerce time to integer — backend may send Timestamp objects or strings
              const candleData = { ...msg.data, time: Math.floor(Number(msg.data.time)) };
              // v2.1: Extract ticker_price for real-time price display
              const tp = (msg.data as Record<string, unknown>).ticker_price as number | null | undefined;
              setState((prev) => ({
                ...prev,
                candles: [...prev.candles.slice(-199), candleData],
                tickerPrice: tp != null && tp > 0 ? tp : prev.tickerPrice,
              }));
              break;

            case 'brain_update':
              // v2.1 FIX: Also extract ticker_price from brain_update for reliability
              const brainTp = (msg.data as Record<string, unknown>).ticker_price as number | null | undefined;
              setState((prev) => ({
                ...prev,
                brainUpdate: msg.data,
                tickerPrice: brainTp != null && brainTp > 0 ? brainTp : prev.tickerPrice,
              }));
              break;

            case 'position_update':
              setState((prev) => ({
                ...prev,
                position: wireToPosition(msg.data),
              }));
              break;

            case 'error':
              setState((prev) => ({
                ...prev,
                status: 'error',
                error: msg.data.message,
              }));
              break;
          }
        } catch (e) {
          console.error('[useLiveTrading] Parse error:', e);
        }
      };

      ws.onclose = () => {
        setState((prev) => {
          // Only reset to idle if we were previously connected (not manual disconnect)
          if (prev.status === 'connected' || prev.status === 'authenticating') {
            return { ...prev, status: 'error', error: 'Connection lost' };
          }
          return prev;
        });
        wsRef.current = null;
      };

      ws.onerror = () => {
        setState((prev) => ({
          ...prev,
          status: 'error',
          error: 'Connection failed — is the backend running?',
        }));
      };

      wsRef.current = ws;
    },
    [symbol, timeframe],
  );

  // Cleanup on unmount
  return { state, connect, disconnect, _wsRef: wsRef };
}
