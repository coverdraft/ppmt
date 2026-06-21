/**
 * PositionState — The heartbeat of every open position.
 * 
 * This interface is the EXACT mirror of the Python dataclass.
 * Both must stay in sync. Any field added here must be added there.
 */
export interface PositionState {
  symbol: string;              // 'DOGE/USDT'
  direction: 'LONG' | 'SHORT';
  status: PositionStatus;
  
  entry_price: number;
  entry_time: string;
  size_usdt: number;
  
  current_sl: number;          // Dynamic — moves with Walk-Forward
  current_tp: number;          // Dynamic — extends with Walk-Forward
  catastrophic_sl: number;     // Static — NEVER moves
  
  expected_sequence: string[][];  // e.g. [['d','x'], ['e','y'], ['f','z']]
  sequence_index: number;        // 0 at start, advances with each match
  
  close_price?: number;
  close_reason?: string;
  pnl_pct?: number;
  pnl_usdt?: number;
}

export type PositionStatus = 
  | 'ACTIVE' 
  | 'BREAK_EVEN_SECURED' 
  | 'TP_EXTENDED' 
  | 'CLOSED_BY_TP' 
  | 'CLOSED_BY_SL' 
  | 'CLOSED_BY_DIVERGENCE' 
  | 'CLOSED_CATASTROPHIC' 
  | 'CLOSED_KILL_SWITCH';

export type OperationMode = 'ANALYSIS' | 'PAPER_LIVE' | 'LIVE_TRADING';
