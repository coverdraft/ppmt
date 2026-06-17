import Database from 'better-sqlite3';
import path from 'path';
import os from 'os';

const DB_PATH = path.join(os.homedir(), '.ppmt', 'ppmt.db');

let _db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (!_db) {
    _db = new Database(DB_PATH, { readonly: true });
    _db.pragma('journal_mode = WAL');
  }
  return _db;
}

export interface Asset {
  symbol: string;
  asset_class: string;
  weight_profile: string | null;
  first_seen: string | null;
  last_updated: string | null;
  candle_count: number;
}

export interface OHLCV {
  id: number;
  symbol: string;
  timeframe: string;
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Trie {
  symbol: string;
  level: string;
  data: string;
  updated_at: string | null;
}

export interface EngineState {
  symbol: string;
  data: string;
  updated_at: string | null;
}

export interface Signal {
  id: number;
  symbol: string;
  signal_type: string;
  confidence: number | null;
  quality_score: number | null;
  sizing_multiplier: number | null;
  entry_price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  expected_move_pct: number | null;
  win_rate: number | null;
  remaining_candles: number | null;
  matched_pattern: string | null;
  predicted_path: string | null;
  timestamp: number;
}
