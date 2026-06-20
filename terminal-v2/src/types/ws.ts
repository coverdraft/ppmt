/**
 * WebSocket message types for the PPMT V2 Terminal.
 *
 * These must match the JSON structure emitted by the Python backend
 * in v2_server.py. snake_case is preserved on the wire — the frontend
 * uses the same casing to avoid transform middleware.
 */

export interface CandleMessage {
  type: 'candle';
  data: {
    time: number;      // Unix timestamp in seconds
    open: number;
    high: number;
    low: number;
    close: number;
  };
}

export interface BrainUpdateMessage {
  type: 'brain_update';
  data: {
    current_sax_symbol: string[];
    active_path_ids: string[];
    n1_confidence: number;
    n2_confidence: number;
    weighted_confidence: number;
    signal_type: string;
  };
}

export interface PositionUpdateMessage {
  type: 'position_update';
  data: PositionStateWire;
}

export interface ErrorMessage {
  type: 'error';
  data: { message: string };
}

/** v0.45.0: Auth confirmation from the live-trading endpoint. */
export interface AuthOkMessage {
  type: 'auth_ok';
}

/** v0.47.0: Config message from frontend (dynamic capital allocation). */
/** v0.50.0: ENTREGABLE 11 — Added custom_base_url for proxy/VPN support. */
export interface ConfigMessage {
  type: 'config';
  allocated_usdt: number;
  custom_base_url?: string;
}

/**
 * PositionState from the wire (snake_case, matching Python dataclass).
 * This is the EXACT shape sent by PaperExecutor.to_dict().
 */
export interface PositionStateWire {
  symbol: string;
  direction: 'LONG' | 'SHORT';
  status: string; // PositionStatus values
  entry_price: number;
  entry_time: string;
  size_usdt: number;
  current_sl: number;
  current_tp: number;
  catastrophic_sl: number;
  expected_sequence: string[][];
  sequence_index: number;
  close_price: number | null;
  close_reason: string | null;
  pnl_pct: number | null;
  pnl_usdt: number | null;
}

export type WSMessage = CandleMessage | BrainUpdateMessage | PositionUpdateMessage | ErrorMessage | AuthOkMessage;
