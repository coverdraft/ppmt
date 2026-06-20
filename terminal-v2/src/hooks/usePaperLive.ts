import { useEffect, useRef, useState, useCallback } from 'react';
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
 * Field names are already snake_case in both — just fix the status typing.
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

export interface PaperLiveState {
  connected: boolean;
  candles: CandleMessage['data'][];
  brainUpdate: BrainUpdateMessage['data'] | null;
  position: PositionState | null;
  error: string | null;
}

/**
 * usePaperLive — WebSocket hook for PAPER LIVE mode.
 *
 * Connects to ws://localhost:8000/ws/paper-live/{symbol}/{timeframe}
 * and returns live candle, brain, and position state.
 */
export function usePaperLive(symbol: string | null, timeframe: string) {
  const [state, setState] = useState<PaperLiveState>({
    connected: false,
    candles: [],
    brainUpdate: null,
    position: null,
    error: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (!symbol) return;

    // Close existing connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    // Symbol in URL: "DOGE/USDT" → "DOGE-USDT" (no slashes in URL path segments)
    const urlSymbol = symbol.replace(/\//g, '-');
    const wsUrl = `ws://localhost:8000/ws/paper-live/${urlSymbol}/${timeframe}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      setState((prev) => ({ ...prev, connected: true, error: null }));
    };

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);

        switch (msg.type) {
          case 'candle':
            setState((prev) => ({
              ...prev,
              candles: [...prev.candles.slice(-199), msg.data],  // Keep last 200
            }));
            break;

          case 'brain_update':
            setState((prev) => ({
              ...prev,
              brainUpdate: msg.data,
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
              error: msg.data.message,
            }));
            break;
        }
      } catch (e) {
        console.error('[usePaperLive] Parse error:', e);
      }
    };

    ws.onclose = () => {
      setState((prev) => ({ ...prev, connected: false }));
      // Auto-reconnect after 3 seconds
      reconnectTimeoutRef.current = setTimeout(() => {
        connect();
      }, 3000);
    };

    ws.onerror = () => {
      setState((prev) => ({
        ...prev,
        connected: false,
        error: 'Connection failed — is the backend running?',
      }));
    };

    wsRef.current = ws;
  }, [symbol, timeframe]);

  // Connect on mount / when symbol changes
  useEffect(() => {
    setState({
      connected: false,
      candles: [],
      brainUpdate: null,
      position: null,
      error: null,
    });

    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return state;
}
