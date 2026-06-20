import { useEffect, useRef, useState, useCallback } from 'react';
import { buildAuthPayload } from '../utils/credentialCrypto';
import type {
  WSMessage,
  CandleMessage,
  BrainUpdateMessage,
  PositionUpdateMessage,
  PositionStateWire,
  AuthOkMessage,
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

export interface LiveTradingState {
  connected: boolean;
  authenticated: boolean;
  candles: CandleMessage['data'][];
  brainUpdate: BrainUpdateMessage['data'] | null;
  position: PositionState | null;
  error: string | null;
}

/**
 * useLiveTrading — WebSocket hook for LIVE TRADING mode (MEXC Futures).
 *
 * v0.45.0: ENTREGABLE 6 — Encrypted credentials.
 *
 * Connects to ws://localhost:8000/ws/live-trading/{symbol}/{timeframe}
 * Sends Fernet-encrypted API keys as the first message.
 * Returns live candle, brain, and position state.
 */
export function useLiveTrading(
  symbol: string | null,
  timeframe: string,
  credentials: { apiKey: string; apiSecret: string; sessionPassword: string } | null,
) {
  const [state, setState] = useState<LiveTradingState>({
    connected: false,
    authenticated: false,
    candles: [],
    brainUpdate: null,
    position: null,
    error: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (!symbol || !credentials) return;

    // Close existing connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    // Symbol in URL: "DOGE/USDT" → "DOGE-USDT"
    const urlSymbol = symbol.replace(/\//g, '-');
    const wsUrl = `ws://localhost:8000/ws/live-trading/${urlSymbol}/${timeframe}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      setState((prev) => ({ ...prev, connected: true, error: null }));

      // ── Send ENCRYPTED auth as first message ──
      const authPayload = buildAuthPayload(
        credentials.apiKey,
        credentials.apiSecret,
        credentials.sessionPassword,
      );
      ws.send(JSON.stringify(authPayload));
    };

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);

        switch (msg.type) {
          case 'auth_ok':
            setState((prev) => ({ ...prev, authenticated: true }));
            break;

          case 'candle':
            setState((prev) => ({
              ...prev,
              candles: [...prev.candles.slice(-199), msg.data],
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
        console.error('[useLiveTrading] Parse error:', e);
      }
    };

    ws.onclose = () => {
      setState((prev) => ({ ...prev, connected: false, authenticated: false }));
      // Auto-reconnect after 3 seconds
      reconnectTimeoutRef.current = setTimeout(() => {
        connect();
      }, 3000);
    };

    ws.onerror = () => {
      setState((prev) => ({
        ...prev,
        connected: false,
        authenticated: false,
        error: 'Connection failed — is the backend running?',
      }));
    };

    wsRef.current = ws;
  }, [symbol, timeframe, credentials]);

  // Connect on mount / when symbol changes
  useEffect(() => {
    setState({
      connected: false,
      authenticated: false,
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
