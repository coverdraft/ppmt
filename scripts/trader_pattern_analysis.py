"""
trader_pattern_analysis.py — Reverse-engineer the trader's visual pattern recognition

The trader uses ONLY visual pattern recognition to decide entries.
Our job: analyze every entry point with OHLCV context to discover
WHAT visual patterns the trader was seeing, and which ones have edge.

Steps:
1. Load trader's 3,230 trades from XLSX
2. Download OHLCV data for each symbol traded
3. For each entry, compute ~30 pattern detector features
4. Cluster entries by pattern profile
5. Measure WR, PnL, edge per pattern
6. Report findings → feed into v8 features.py
"""
import sys
import logging
import os
import time
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("pattern_analysis")

# ── Paths ──────────────────────────────────────────────────────────
UPLOAD_DIR = Path("/home/z/my-project/upload")
XLSX_PATH = UPLOAD_DIR / "MEXC - Historial de Ordenes de Futuros-20250624-20260623_1782174256031.xlsx"
OUTPUT_DIR = Path("/home/z/my-project/download")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = Path("/home/z/my-project/ppmt/data/ohlcv_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════
# STEP 1: Load and reconstruct trades from XLSX
# ══════════════════════════════════════════════════════════════════

def load_trader_orders(xlsx_path: Path) -> pd.DataFrame:
    """Load raw order history from MEXC XLSX."""
    LOG.info("Loading trader orders from %s", xlsx_path.name)
    df = pd.read_excel(xlsx_path)
    LOG.info("Loaded %d orders, columns: %s", len(df), list(df.columns))
    return df


def reconstruct_trades_smart(orders: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct trades from MEXC futures order history.
    
    MEXC futures orders have: symbol, side (Open Long/Close Long/Open Short/Close Short),
    price, volume, deal_time, pnl, fee.
    """
    LOG.info("Reconstructing trades from %d orders", len(orders))
    
    # Print column info
    LOG.info("Columns: %s", list(orders.columns))
    for col in orders.columns:
        LOG.info("  %s: dtype=%s sample=%s", col, orders[col].dtype, orders[col].iloc[0] if len(orders) > 0 else "N/A")
    
    # MEXC Spanish column mapping
    # Known columns from the XLSX:
    #   UID, Tiempo(UTC+02:00), Par de Trading de Futuros, Dirección,
    #   Apalancamiento, Tipo de Orden, Cant. de la Orden (Cont.),
    #   Cant. Completada (Cont.), Cant. de la Orden (Cripto),
    #   Cant. Completada (Cripto), Cant. de la Orden (Monto),
    #   Cant. Completada (Monto), Precio de Orden,
    #   Precio promedio completo, PNL de Cierre, Comisión de Trading,
    #   Cripto Comisión-pago, Estado
    
    MEXC_COL_MAP = {
        "Tiempo(UTC+02:00)": "time",
        "Par de Trading de Futuros": "symbol",
        "Dirección": "side",
        "Apalancamiento": "leverage",
        "Tipo de Orden": "order_type",
        "Cant. Completada (Cripto)": "qty",
        "Cant. Completada (Monto)": "qty_usdt",
        "Precio promedio completo": "price",
        "Precio de Orden": "order_price",
        "PNL de Cierre": "pnl",
        "Comisión de Trading": "fee",
        "Estado": "status",
    }
    
    # Also try English names as fallback
    EN_COL_MAP = {}
    for c in orders.columns:
        cl = c.lower().strip()
        if "symbol" in cl or "pair" in cl or "contract" in cl:
            EN_COL_MAP[c] = "symbol"
        elif cl == "side" or cl == "direction":
            EN_COL_MAP[c] = "side"
        elif cl in ("price",) or ("price" in cl and "avg" not in cl and "exec" not in cl and "order" not in cl):
            if "price" not in EN_COL_MAP.values():
                EN_COL_MAP[c] = "price"
        elif cl in ("qty", "quantity", "volume", "amount", "deal") or "vol" in cl or "qty" in cl:
            EN_COL_MAP[c] = "qty"
        elif "time" in cl or "date" in cl:
            EN_COL_MAP[c] = "time"
        elif cl in ("pnl", "profit", "realizedpnl", "realized_pnl", "profitandloss"):
            EN_COL_MAP[c] = "pnl"
        elif cl in ("fee", "commission", "handlingfee"):
            EN_COL_MAP[c] = "fee"
    
    # Merge: MEXC Spanish first, then English fallback
    col_map = {**EN_COL_MAP, **{k: v for k, v in MEXC_COL_MAP.items() if k in orders.columns}}
    
    # Extract mapped columns
    symbol_col = next((k for k, v in col_map.items() if v == "symbol"), None)
    side_col = next((k for k, v in col_map.items() if v == "side"), None)
    price_col = next((k for k, v in col_map.items() if v == "price"), None)
    qty_col = next((k for k, v in col_map.items() if v == "qty"), None)
    time_col = next((k for k, v in col_map.items() if v == "time"), None)
    pnl_col = next((k for k, v in col_map.items() if v == "pnl"), None)
    fee_col = next((k for k, v in col_map.items() if v == "fee"), None)
    
    LOG.info("Identified columns: symbol=%s side=%s price=%s qty=%s time=%s pnl=%s fee=%s",
             symbol_col, side_col, price_col, qty_col, time_col, pnl_col, fee_col)
    
    if not all([symbol_col, side_col, price_col, time_col]):
        LOG.error("Could not identify essential columns!")
        return pd.DataFrame()
    
    # Parse and sort
    orders = orders.copy()
    
    # Handle MEXC timestamp format (UTC+02:00 offset)
    raw_ts = orders[time_col].astype(str)
    # MEXC format: "2026-06-01 03:31:00" (in UTC+02:00)
    # Parse as UTC+2 then convert to UTC
    orders["_ts"] = pd.to_datetime(raw_ts, utc=False)
    # The times are UTC+2, so subtract 2 hours to get UTC
    orders["_ts"] = orders["_ts"] - pd.Timedelta(hours=2)
    orders["_ts"] = orders["_ts"].dt.tz_localize("UTC")
    orders = orders.sort_values("_ts").reset_index(drop=True)
    
    LOG.info("Time range: %s → %s", orders["_ts"].min(), orders["_ts"].max())
    
    # Clean symbol names: MEXC format "PORTALUSDT" → "PORTAL"
    orders["_symbol"] = orders[symbol_col].astype(str).str.strip().str.upper()
    orders["_symbol"] = orders["_symbol"].str.replace("USDT", "", regex=False).str.replace("_", "").str.strip()
    
    # Parse side: MEXC uses "sell long" / "buy long" / "sell short" / "buy short"
    # "buy long" = open long, "sell long" = close long
    # "buy short" = close short, "sell short" = open short
    orders["_side_raw"] = orders[side_col].astype(str).str.strip().str.lower()
    
    def parse_side(s):
        s = s.lower().strip()
        # MEXC format
        if "buy" in s and "long" in s:
            return "open_long"
        elif "sell" in s and "long" in s:
            return "close_long"
        elif "sell" in s and "short" in s:
            return "open_short"
        elif "buy" in s and "short" in s:
            return "close_short"
        # Generic format
        elif "open" in s and "long" in s:
            return "open_long"
        elif "close" in s and "long" in s:
            return "close_long"
        elif "open" in s and "short" in s:
            return "open_short"
        elif "close" in s and "short" in s:
            return "close_short"
        elif "buy" in s:
            return "open_long"
        elif "sell" in s:
            return "close_long"
        else:
            return s
    
    orders["_action"] = orders["_side_raw"].apply(parse_side)
    
    # Parse numeric columns
    orders["_price"] = pd.to_numeric(orders[price_col], errors="coerce")
    orders["_qty"] = pd.to_numeric(orders[qty_col], errors="coerce") if qty_col else 1.0
    orders["_pnl"] = pd.to_numeric(orders[pnl_col], errors="coerce").fillna(0) if pnl_col else 0.0
    orders["_fee"] = pd.to_numeric(orders[fee_col], errors="coerce").fillna(0) if fee_col else 0.0
    
    # Group into trades: match opens with closes
    trades = []
    open_positions = {}  # symbol → list of open entries
    
    for _, row in orders.iterrows():
        sym = row["_symbol"]
        action = row["_action"]
        price = row["_price"]
        qty = row["_qty"] if pd.notna(row["_qty"]) else 0
        pnl = row["_pnl"] if pd.notna(row["_pnl"]) else 0
        fee = abs(row["_fee"]) if pd.notna(row["_fee"]) else 0
        ts = row["_ts"]
        
        if pd.isna(ts) or pd.isna(price):
            continue
        
        if "open" in action:
            if sym not in open_positions:
                open_positions[sym] = {
                    "direction": "long" if "long" in action else "short",
                    "entries": [],
                    "entry_time": ts,
                    "entry_price": price,
                }
            open_positions[sym]["entries"].append({
                "price": price, "qty": qty, "fee": fee, "ts": ts
            })
        
        elif "close" in action:
            if sym in open_positions:
                pos = open_positions[sym]
                trade = {
                    "symbol": sym,
                    "direction": pos["direction"],
                    "entry_time": pos["entry_time"],
                    "entry_price": pos["entry_price"],
                    "exit_time": ts,
                    "exit_price": price,
                    "pnl": pnl,
                    "fee": fee + sum(e["fee"] for e in pos["entries"]),
                    "n_entries": len(pos["entries"]),
                    "total_qty": sum(e["qty"] for e in pos["entries"]) + qty,
                }
                trades.append(trade)
                del open_positions[sym]
    
    df = pd.DataFrame(trades)
    LOG.info("Reconstructed %d trades from order matching", len(df))
    
    if len(df) == 0:
        # Fallback: simple grouping
        LOG.warning("Order matching failed, using simple grouping...")
        df = _simple_trade_grouping(orders)
    
    return df


def _simple_trade_grouping(orders: pd.DataFrame) -> pd.DataFrame:
    """Fallback: group consecutive orders for same symbol into trades."""
    trades = []
    current = None
    
    for _, row in orders.iterrows():
        sym = row.get("_symbol", "")
        if not sym or sym == "NAN":
            continue
        
        ts = row.get("_ts")
        price = row.get("_price", 0)
        pnl = row.get("_pnl", 0)
        
        if pd.isna(ts):
            continue
        
        if current is None or current["symbol"] != sym:
            if current:
                trades.append(current)
            current = {
                "symbol": sym,
                "entry_time": ts,
                "entry_price": price if pd.notna(price) else 0,
                "exit_time": ts,
                "exit_price": price if pd.notna(price) else 0,
                "pnl": pnl if pd.notna(pnl) else 0,
                "n_entries": 1,
            }
        else:
            current["exit_time"] = ts
            current["exit_price"] = price if pd.notna(price) else current["entry_price"]
            current["pnl"] += pnl if pd.notna(pnl) else 0
            current["n_entries"] += 1
    
    if current:
        trades.append(current)
    
    df = pd.DataFrame(trades)
    LOG.info("Simple grouping: %d trades", len(df))
    return df


# ══════════════════════════════════════════════════════════════════
# STEP 2: Download OHLCV data via ccxt
# ══════════════════════════════════════════════════════════════════

def download_ohlcv_ccxt(symbol: str, timeframe: str = "5m", 
                         start_ts: int = None, end_ts: int = None,
                         exchange_id: str = "binance") -> pd.DataFrame:
    """Download OHLCV data using ccxt."""
    import ccxt
    
    cache_file = CACHE_DIR / f"{symbol}_{timeframe}_{start_ts}_{end_ts}.parquet"
    if cache_file.exists():
        LOG.info("  Cache hit: %s", cache_file.name)
        return pd.read_parquet(cache_file)
    
    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    
    # Symbol format
    if "/" not in symbol:
        ccxt_symbol = f"{symbol}/USDT"
    else:
        ccxt_symbol = symbol
    
    # Check if symbol exists on this exchange
    exchange.load_markets()
    if ccxt_symbol not in exchange.markets:
        LOG.warning("  %s not found on %s, trying MEXC...", ccxt_symbol, exchange_id)
        # Try MEXC
        try:
            exchange2 = ccxt.mexc({"enableRateLimit": True})
            exchange2.load_markets()
            if ccxt_symbol in exchange2.markets:
                exchange = exchange2
                exchange_id = "mexc"
            else:
                LOG.warning("  %s not found on MEXC either", ccxt_symbol)
                return pd.DataFrame()
        except:
            return pd.DataFrame()
    
    all_ohlcv = []
    since = start_ts
    
    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(ccxt_symbol, timeframe, since=since, limit=1000)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            last_ts = ohlcv[-1][0]
            if last_ts >= end_ts or len(ohlcv) < 1000:
                break
            since = last_ts + 1
            time.sleep(exchange.rateLimit / 1000)
        except Exception as e:
            LOG.warning("  Download error: %s", e)
            break
    
    if not all_ohlcv:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    
    # Filter to requested range
    if start_ts:
        df = df[df["timestamp"] >= start_ts]
    if end_ts:
        df = df[df["timestamp"] <= end_ts]
    
    # Cache
    df.to_parquet(cache_file, index=False)
    LOG.info("  Downloaded %d bars for %s from %s (cached)", len(df), symbol, exchange_id)
    
    return df


# ══════════════════════════════════════════════════════════════════
# STEP 3: Pattern Detectors — The Core
# ══════════════════════════════════════════════════════════════════

def compute_pattern_features(closes, highs, lows, opens, volumes, i, lookback=50):
    """Compute pattern detector features at bar i.
    
    Returns a dict of features describing the visual pattern at bar i.
    All features use ONLY data up to bar i (no future info).
    """
    n = len(closes)
    if i < lookback or i >= n:
        return None
    
    c = closes[:i+1]
    h = highs[:i+1]
    l = lows[:i+1]
    o = opens[:i+1]
    v = volumes[:i+1]
    
    features = {}
    
    # ── PATTERN 1: BREAKOUT — price breaking above/below recent range ──
    high_20 = np.max(h[-20:])
    low_20 = np.min(l[-20:])
    range_20 = high_20 - low_20
    
    if range_20 > 0:
        features["close_position_20"] = (c[-1] - low_20) / range_20
        features["is_breakout_up"] = float(c[-1] >= high_20)
        features["is_breakout_down"] = float(c[-1] <= low_20)
        features["breakout_strength"] = max(0, (c[-1] - high_20) / max(range_20, 1e-10)) if c[-1] >= high_20 else (
            max(0, (low_20 - c[-1]) / max(range_20, 1e-10)) if c[-1] <= low_20 else 0)
    else:
        features["close_position_20"] = 0.5
        features["is_breakout_up"] = 0.0
        features["is_breakout_down"] = 0.0
        features["breakout_strength"] = 0.0
    
    # ── PATTERN 2: EMA BOUNCE — price bouncing off a moving average ──
    ema_9 = _ema(c, 9)
    ema_21 = _ema(c, 21)
    ema_50 = _ema(c, 50) if len(c) >= 50 else _ema(c, min(len(c), 21))
    
    atr = _atr(h, l, c, 14)
    if atr > 0:
        features["dist_ema9_atr"] = (c[-1] - ema_9[-1]) / atr
        features["dist_ema21_atr"] = (c[-1] - ema_21[-1]) / atr
        features["dist_ema50_atr"] = (c[-1] - ema_50[-1]) / atr
    else:
        features["dist_ema9_atr"] = 0
        features["dist_ema21_atr"] = 0
        features["dist_ema50_atr"] = 0
    
    # EMA bounce detection
    if atr > 0 and len(c) >= 4:
        touched_ema21 = any(
            l[-j] <= ema_21[-j] + 0.3 * atr and h[-j] >= ema_21[-j] - 0.3 * atr
            for j in range(1, min(4, len(c)))
        )
        features["ema21_bounce"] = float(touched_ema21)
        
        touched_ema50 = any(
            l[-j] <= ema_50[-j] + 0.3 * atr and h[-j] >= ema_50[-j] - 0.3 * atr
            for j in range(1, min(6, len(c)))
        )
        features["ema50_bounce"] = float(touched_ema50)
    else:
        features["ema21_bounce"] = 0
        features["ema50_bounce"] = 0
    
    features["ema_alignment"] = float(np.sign(ema_9[-1] - ema_21[-1]))
    features["ema_trend_strength"] = (ema_9[-1] - ema_50[-1]) / max(abs(ema_50[-1]), 1e-10) * 100
    
    # ── PATTERN 3: SQUEEZE / COMPRESSION — Bollinger Band squeeze ──
    if len(c) >= 20:
        sma_20 = np.mean(c[-20:])
        std_20 = np.std(c[-20:])
        bb_width = 4 * std_20 / max(sma_20, 1e-10)
        features["bb_width"] = bb_width
        
        # Squeeze score = current BB width percentile vs recent
        bb_widths = []
        for j in range(1, min(30, len(c) - 20)):
            seg = c[-(20+j):-(j)]
            if len(seg) >= 20:
                s = np.mean(seg)
                sd = np.std(seg)
                if s > 0:
                    bb_widths.append(4 * sd / s)
        
        if bb_widths:
            bb_pct = np.mean([w <= bb_width for w in bb_widths])
            features["squeeze_score"] = 1.0 - bb_pct
        else:
            features["squeeze_score"] = 0.5
    else:
        features["bb_width"] = 0
        features["squeeze_score"] = 0.5
    
    # ── PATTERN 4: VOLUME SPIKE ──
    if len(v) >= 20:
        vol_ma_20 = np.mean(v[-20:])
        if vol_ma_20 > 0:
            features["vol_ratio"] = v[-1] / vol_ma_20
            features["vol_ratio_3"] = np.mean(v[-3:]) / vol_ma_20
        else:
            features["vol_ratio"] = 1.0
            features["vol_ratio_3"] = 1.0
    else:
        features["vol_ratio"] = 1.0
        features["vol_ratio_3"] = 1.0
    
    features["breakout_vol_confirm"] = features["vol_ratio"] if (features["is_breakout_up"] or features["is_breakout_down"]) else 0.0
    
    # ── PATTERN 5: CANDLE PATTERNS ──
    body = c[-1] - o[-1]
    bar_range = h[-1] - l[-1]
    upper_wick = h[-1] - max(o[-1], c[-1])
    lower_wick = min(o[-1], c[-1]) - l[-1]
    
    if bar_range > 0:
        features["body_ratio"] = abs(body) / bar_range
        features["lower_wick_ratio"] = lower_wick / bar_range
        features["upper_wick_ratio"] = upper_wick / bar_range
    else:
        features["body_ratio"] = 0.5
        features["lower_wick_ratio"] = 0.25
        features["upper_wick_ratio"] = 0.25
    
    features["is_doji"] = float(features["body_ratio"] < 0.1)
    features["is_hammer"] = float(features["lower_wick_ratio"] > 2 * features["body_ratio"] and features["lower_wick_ratio"] > 0.4 and features["upper_wick_ratio"] < 0.15)
    features["is_shooting_star"] = float(features["upper_wick_ratio"] > 2 * features["body_ratio"] and features["upper_wick_ratio"] > 0.4 and features["lower_wick_ratio"] < 0.15)
    
    if len(c) >= 2:
        prev_body = c[-2] - o[-2]
        features["is_bullish_engulf"] = float(body > 0 and prev_body < 0 and c[-1] > o[-2] and o[-1] < c[-2])
        features["is_bearish_engulf"] = float(body < 0 and prev_body > 0 and c[-1] < o[-2] and o[-1] > c[-2])
    else:
        features["is_bullish_engulf"] = 0.0
        features["is_bearish_engulf"] = 0.0
    
    features["is_bull_pin"] = float(features["lower_wick_ratio"] > 0.6 and features["body_ratio"] < 0.3)
    features["is_bear_pin"] = float(features["upper_wick_ratio"] > 0.6 and features["body_ratio"] < 0.3)
    
    # ── PATTERN 6: PULLBACK IN TREND ──
    trend_dir = np.sign(ema_9[-1] - ema_50[-1])
    high_6 = np.max(h[-6:]) if len(h) >= 6 else np.max(h)
    low_6 = np.min(l[-6:]) if len(l) >= 6 else np.min(l)
    
    if high_6 > low_6:
        if trend_dir > 0:
            pullback = (high_6 - c[-1]) / (high_6 - low_6)
            features["pullback_depth"] = pullback
            features["is_pullback_long"] = float(pullback > 0.3 and trend_dir > 0)
            features["is_pullback_short"] = 0.0
        elif trend_dir < 0:
            pullback = (c[-1] - low_6) / (high_6 - low_6)
            features["pullback_depth"] = pullback
            features["is_pullback_short"] = float(pullback > 0.3 and trend_dir < 0)
            features["is_pullback_long"] = 0.0
        else:
            features["pullback_depth"] = 0.5
            features["is_pullback_long"] = 0.0
            features["is_pullback_short"] = 0.0
    else:
        features["pullback_depth"] = 0.5
        features["is_pullback_long"] = 0.0
        features["is_pullback_short"] = 0.0
    
    # ── PATTERN 7: MOMENTUM / IMPULSE ──
    if len(c) >= 2:
        ret_1 = (c[-1] - c[-2]) / max(c[-2], 1e-10)
    else:
        ret_1 = 0
    if len(c) >= 4:
        ret_3 = (c[-1] - c[-4]) / max(c[-4], 1e-10)
    else:
        ret_3 = 0
    if len(c) >= 7:
        ret_6 = (c[-1] - c[-7]) / max(c[-7], 1e-10)
    else:
        ret_6 = 0
    
    features["ret_1_pct"] = ret_1 * 100
    features["ret_3_pct"] = ret_3 * 100
    features["ret_6_pct"] = ret_6 * 100
    
    if len(c) > 10:
        rets_1 = np.diff(c[-50:]) / np.maximum(c[-50:-1], 1e-10) if len(c) > 50 else np.diff(c) / np.maximum(c[:-1], 1e-10)
        if len(rets_1) > 5 and np.std(rets_1) > 0:
            features["momentum_z"] = ret_1 / np.std(rets_1)
        else:
            features["momentum_z"] = 0
    else:
        features["momentum_z"] = 0
    
    # Consecutive same-direction bars
    consec = 0
    for j in range(1, min(10, len(c) - 1)):
        if (c[-j] - c[-j-1]) * np.sign(ret_1 + 1e-20) > 0:
            consec += 1
        else:
            break
    features["consecutive_bars"] = consec * np.sign(ret_1)
    
    # ── PATTERN 8: SUPPORT / RESISTANCE TEST ──
    high_50 = np.max(h[-50:]) if len(h) >= 50 else np.max(h)
    low_50 = np.min(l[-50:]) if len(l) >= 50 else np.min(l)
    
    features["near_high_50"] = float(abs(c[-1] - high_50) / max(high_50, 1e-10) < 0.01)
    features["near_low_50"] = float(abs(c[-1] - low_50) / max(low_50, 1e-10) < 0.01)
    
    # ── PATTERN 9: RANGE CONTRACTION → EXPANSION ──
    if len(h) >= 3:
        range_3 = np.mean([h[-j] - l[-j] for j in range(1, min(4, len(h)+1))])
        range_20 = np.mean([h[-j] - l[-j] for j in range(1, min(21, len(h)+1))])
        features["range_expansion"] = range_3 / max(range_20, 1e-10)
    else:
        features["range_expansion"] = 1.0
    
    # ── PATTERN 10: V-BOTTOM / V-TOP ──
    if len(c) >= 7:
        ret_first = (c[-4] - c[-7]) / max(c[-7], 1e-10)
        ret_second = (c[-1] - c[-4]) / max(c[-4], 1e-10)
        features["v_reversal"] = float(np.sign(ret_first) != np.sign(ret_second) and abs(ret_second) > abs(ret_first) * 0.5)
    else:
        features["v_reversal"] = 0.0
    
    # ── META ──
    features["atr_pct"] = atr / max(c[-1], 1e-10) * 100
    
    return features


def _ema(data, period):
    """Compute EMA."""
    if len(data) < period:
        return np.full(len(data), data[-1] if len(data) > 0 else 0)
    alpha = 2 / (period + 1)
    result = np.zeros(len(data))
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
    return result


def _atr(highs, lows, closes, period=14):
    """Compute current ATR value."""
    if len(closes) < 2:
        return 0.0
    n = min(period, len(closes) - 1)
    trs = []
    for i in range(max(1, len(closes) - n), len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return np.mean(trs) if trs else 0.0


# ══════════════════════════════════════════════════════════════════
# STEP 4: Classify each entry into a visual pattern
# ══════════════════════════════════════════════════════════════════

def classify_pattern(feat: dict) -> str:
    """Classify the entry bar into a visual pattern type."""
    
    # 1. BREAKOUT with volume confirmation
    if (feat.get("is_breakout_up", 0) or feat.get("is_breakout_down", 0)) and feat.get("vol_ratio", 1) > 1.3:
        return "BREAKOUT_UP" if feat.get("is_breakout_up", 0) else "BREAKOUT_DOWN"
    
    # 2. BREAKOUT without volume
    if feat.get("breakout_strength", 0) > 0.1:
        return "BREAKOUT_UP" if feat.get("close_position_20", 0.5) > 0.9 else "BREAKOUT_DOWN"
    
    # 3. EMA BOUNCE
    if feat.get("ema21_bounce", 0) or feat.get("ema50_bounce", 0):
        return "EMA_BOUNCE_LONG" if feat.get("ema_alignment", 0) > 0 else "EMA_BOUNCE_SHORT"
    
    # 4. PULLBACK IN TREND
    if feat.get("is_pullback_long", 0):
        return "PULLBACK_LONG"
    if feat.get("is_pullback_short", 0):
        return "PULLBACK_SHORT"
    
    # 5. SQUEEZE BREAKOUT
    if feat.get("squeeze_score", 0) > 0.7 and feat.get("range_expansion", 1) > 1.5:
        return "SQUEEZE_BREAK"
    
    # 6. V-REVERSAL
    if feat.get("v_reversal", 0):
        return "V_REVERSAL_UP" if feat.get("ret_1_pct", 0) > 0 else "V_REVERSAL_DOWN"
    
    # 7. CANDLE PATTERNS
    if feat.get("is_bullish_engulf", 0): return "ENGULFING_BULL"
    if feat.get("is_bearish_engulf", 0): return "ENGULFING_BEAR"
    if feat.get("is_hammer", 0): return "HAMMER"
    if feat.get("is_shooting_star", 0): return "SHOOTING_STAR"
    if feat.get("is_bull_pin", 0): return "PIN_BAR_BULL"
    if feat.get("is_bear_pin", 0): return "PIN_BAR_BEAR"
    
    # 8. MOMENTUM IMPULSE
    if abs(feat.get("momentum_z", 0)) > 2.0:
        return "IMPULSE_UP" if feat.get("momentum_z", 0) > 0 else "IMPULSE_DOWN"
    
    # 9. SUPPORT/RESISTANCE TEST
    if feat.get("near_low_50", 0): return "SUPPORT_TEST"
    if feat.get("near_high_50", 0): return "RESISTANCE_TEST"
    
    return "NO_PATTERN"


def group_pattern(p: str) -> str:
    """Group similar patterns into broader categories."""
    if "BREAKOUT" in p: return "BREAKOUT"
    if "EMA_BOUNCE" in p: return "EMA_BOUNCE"
    if "PULLBACK" in p: return "PULLBACK"
    if "SQUEEZE" in p: return "SQUEEZE"
    if "V_REVERSAL" in p: return "V_REVERSAL"
    if "ENGULFING" in p: return "ENGULFING"
    if any(x in p for x in ["PIN_BAR", "HAMMER", "SHOOTING"]): return "REJECTION_CANDLE"
    if "IMPULSE" in p: return "MOMENTUM_IMPULSE"
    if "SUPPORT" in p or "RESISTANCE" in p: return "LEVEL_TEST"
    return "NO_CLEAR_PATTERN"


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    LOG.info("=" * 70)
    LOG.info("TRADER VISUAL PATTERN RECOGNITION ANALYSIS")
    LOG.info("=" * 70)
    
    # ── Step 1: Load trader orders ──
    orders = load_trader_orders(XLSX_PATH)
    
    # ── Step 2: Reconstruct trades ──
    trades = reconstruct_trades_smart(orders)
    
    if len(trades) == 0:
        LOG.error("No trades reconstructed from order matching!")
        LOG.info("Action distribution: %s", pd.Series(orders.get("_action", [])).value_counts().to_dict())
        LOG.info("Unique symbols: %d", orders["_symbol"].nunique())
        # Fallback: create individual entries from each order
        trades_list = []
        for _, row in orders.iterrows():
            trades_list.append({
                "symbol": row.get("_symbol", ""),
                "direction": "long" if "long" in str(row.get("_action", "")) else "short",
                "entry_time": row.get("_ts", pd.NaT),
                "entry_price": float(row.get("_price", 0) or 0),
                "exit_time": row.get("_ts", pd.NaT),
                "exit_price": float(row.get("_price", 0) or 0),
                "pnl": float(row.get("_pnl", 0) or 0),
                "fee": float(row.get("_fee", 0) or 0),
                "n_entries": 1,
                "total_qty": float(row.get("_qty", 0) or 0),
                "action": row.get("_action", ""),
            })
        trades = pd.DataFrame(trades_list)
        LOG.info("Fallback: created %d individual order entries", len(trades))
    
    LOG.info("Working with %d trade entries", len(trades))
    if "pnl" in trades.columns:
        valid_pnl = trades["pnl"].dropna()
        if len(valid_pnl) > 0:
            wr = (valid_pnl > 0).mean() * 100
            total_pnl = valid_pnl.sum()
            LOG.info("Overall: WR=%.1f%% PnL=%.2f", wr, total_pnl)
    
    # ── Step 3: Get unique symbols and time range ──
    if "symbol" not in trades.columns:
        LOG.error("No symbol column!")
        return
    
    symbols = trades["symbol"].unique()
    LOG.info("Symbols: %s", [s for s in symbols if str(s) != "nan"][:20])
    
    # ── Step 4: Download OHLCV ──
    if "entry_time" in trades.columns:
        min_time = pd.to_datetime(trades["entry_time"], utc=True).min()
        max_time = pd.to_datetime(trades["entry_time"], utc=True).max()
    else:
        min_time = datetime(2025, 6, 1, tzinfo=timezone.utc)
        max_time = datetime(2026, 6, 24, tzinfo=timezone.utc)
    
    start_dt = min_time - timedelta(days=3)
    end_dt = max_time + timedelta(days=1)
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)
    
    LOG.info("Time range: %s → %s", start_dt, end_dt)
    
    ohlcv_data = {}
    for symbol in symbols:
        sym = str(symbol).strip().upper()
        if not sym or sym == "NAN" or len(sym) < 2:
            continue
        
        LOG.info("Downloading OHLCV for %s...", sym)
        try:
            df = download_ohlcv_ccxt(sym, "5m", start_ts, end_ts, "binance")
            if len(df) > 0:
                ohlcv_data[sym] = df
            else:
                # Try MEXC
                df = download_ohlcv_ccxt(sym, "5m", start_ts, end_ts, "mexc")
                if len(df) > 0:
                    ohlcv_data[sym] = df
        except Exception as e:
            LOG.warning("  %s failed: %s", sym, e)
    
    # Also BTC
    if "BTC" not in ohlcv_data:
        try:
            ohlcv_data["BTC"] = download_ohlcv_ccxt("BTC", "5m", start_ts, end_ts, "binance")
        except:
            pass
    
    LOG.info("Downloaded OHLCV for %d symbols", len(ohlcv_data))
    
    # ── Step 5: Compute pattern features for each entry ──
    LOG.info("Computing pattern features for each trade entry...")
    
    pattern_records = []
    skipped_no_data = 0
    skipped_no_bar = 0
    skipped_too_far = 0
    skipped_short_bar = 0
    
    for idx, trade in trades.iterrows():
        symbol = str(trade.get("symbol", "")).strip().upper()
        if symbol not in ohlcv_data:
            skipped_no_data += 1
            continue
        
        ohlcv = ohlcv_data[symbol]
        if len(ohlcv) < 60:
            skipped_short_bar += 1
            continue
        
        entry_time = trade.get("entry_time")
        if pd.isna(entry_time):
            continue
        
        # Convert to timestamp
        try:
            if hasattr(entry_time, "timestamp"):
                entry_ts_ms = int(entry_time.timestamp() * 1000)
            elif isinstance(entry_time, str):
                entry_ts_ms = int(pd.Timestamp(entry_time, tz="UTC").timestamp() * 1000)
            else:
                continue
        except:
            continue
        
        # Find closest bar
        time_diffs = np.abs(ohlcv["timestamp"].values - entry_ts_ms)
        bar_idx = np.argmin(time_diffs)
        
        if time_diffs[bar_idx] > 15 * 60 * 1000:  # 15 min tolerance
            skipped_too_far += 1
            continue
        
        if bar_idx < 50:
            skipped_no_bar += 1
            continue
        
        # Compute features
        feat = compute_pattern_features(
            ohlcv["close"].values,
            ohlcv["high"].values,
            ohlcv["low"].values,
            ohlcv["open"].values,
            ohlcv["volume"].values,
            bar_idx,
            lookback=50,
        )
        
        if feat is None:
            continue
        
        # Classify pattern
        pattern = classify_pattern(feat)
        feat["pattern"] = pattern
        feat["pattern_group"] = group_pattern(pattern)
        
        # Trade info
        feat["symbol"] = symbol
        feat["pnl"] = float(trade.get("pnl", 0) or 0)
        feat["entry_price"] = float(trade.get("entry_price", 0) or 0)
        feat["n_entries"] = int(trade.get("n_entries", 1) or 1)
        feat["is_win"] = float(feat["pnl"] > 0)
        
        # Duration
        exit_time = trade.get("exit_time")
        if pd.notna(exit_time) and pd.notna(entry_time):
            try:
                feat["duration_min"] = (pd.Timestamp(exit_time, tz="UTC") - pd.Timestamp(entry_time, tz="UTC")).total_seconds() / 60
            except:
                feat["duration_min"] = 0
        else:
            feat["duration_min"] = 0
        
        pattern_records.append(feat)
    
    LOG.info("Pattern records: %d (skipped: no_data=%d no_bar=%d too_far=%d short=%d)",
             len(pattern_records), skipped_no_data, skipped_no_bar, skipped_too_far, skipped_short_bar)
    
    if not pattern_records:
        LOG.error("No pattern records! Aborting.")
        return
    
    results = pd.DataFrame(pattern_records)
    
    # ── Step 6: Analyze patterns ──
    n_total = len(results)
    n_wins = int(results["is_win"].sum())
    total_pnl = results["pnl"].sum()
    wr_overall = n_wins / n_total * 100
    
    print("\n" + "=" * 100)
    print("VISUAL PATTERN PERFORMANCE — DETAILED")
    print("=" * 100)
    print(f"\nOverall: {n_total} trades, WR={wr_overall:.1f}%, PnL={total_pnl:.2f} USDT\n")
    
    # Per-pattern stats
    pattern_stats = []
    for pattern in sorted(results["pattern"].unique()):
        mask = results["pattern"] == pattern
        n = mask.sum()
        if n < 2:
            continue
        sub = results[mask]
        wr = sub["is_win"].mean() * 100
        pnl = sub["pnl"].sum()
        avg_pnl = sub["pnl"].mean()
        med_dur = sub["duration_min"].median()
        gains = sub.loc[sub["pnl"] > 0, "pnl"].sum()
        losses = abs(sub.loc[sub["pnl"] < 0, "pnl"].sum())
        pf = gains / max(losses, 1e-10)
        avg_ent = sub["n_entries"].mean()
        
        pattern_stats.append({
            "pattern": pattern, "n": n, "pct": n/n_total*100,
            "WR": wr, "PnL": pnl, "avg_pnl": avg_pnl, "PF": pf,
            "med_dur": med_dur, "avg_entries": avg_ent,
        })
    
    stats_df = pd.DataFrame(pattern_stats).sort_values("PnL", ascending=False)
    
    print(f"{'Pattern':<22} {'N':>5} {'%':>5} {'WR%':>6} {'PnL':>10} {'AvgPnL':>9} {'PF':>6} {'MedDur':>7} {'AvgEnt':>6}")
    print("-" * 90)
    for _, row in stats_df.iterrows():
        print(f"{row['pattern']:<22} {row['n']:>5} {row['pct']:>4.1f}% "
              f"{row['WR']:>5.1f}% {row['PnL']:>+10.1f} {row['avg_pnl']:>+8.3f} "
              f"{row['PF']:>5.2f} {row['med_dur']:>6.1f}m {row['avg_entries']:>5.1f}")
    
    # Per-group stats
    print("\n" + "=" * 80)
    print("PATTERN GROUPS (aggregated)")
    print("=" * 80)
    
    group_stats = []
    for group in sorted(results["pattern_group"].unique()):
        mask = results["pattern_group"] == group
        n = mask.sum()
        if n < 2:
            continue
        sub = results[mask]
        wr = sub["is_win"].mean() * 100
        pnl = sub["pnl"].sum()
        avg_pnl = sub["pnl"].mean()
        med_dur = sub["duration_min"].median()
        gains = sub.loc[sub["pnl"] > 0, "pnl"].sum()
        losses = abs(sub.loc[sub["pnl"] < 0, "pnl"].sum())
        pf = gains / max(losses, 1e-10)
        
        # Feature profile
        feat_cols = [c for c in results.columns if c not in 
                     ["pattern", "pattern_group", "symbol", "pnl", "is_win",
                      "entry_price", "n_entries", "duration_min"]]
        profile = {fc: sub[fc].mean() for fc in feat_cols if fc in sub.columns}
        
        group_stats.append({
            "group": group, "n": n, "pct": n/n_total*100,
            "WR": wr, "PnL": pnl, "avg_pnl": avg_pnl, "PF": pf,
            "med_dur": med_dur, "profile": profile,
        })
    
    group_df = pd.DataFrame(group_stats).sort_values("PnL", ascending=False)
    
    print(f"{'Group':<22} {'N':>5} {'%':>5} {'WR%':>6} {'PnL':>10} {'PF':>6} {'MedDur':>7}")
    print("-" * 65)
    for _, row in group_df.iterrows():
        print(f"{row['group']:<22} {row['n']:>5} {row['pct']:>4.1f}% "
              f"{row['WR']:>5.1f}% {row['PnL']:>+10.1f} {row['PF']:>5.2f} {row['med_dur']:>6.1f}m")
    
    # Feature profile comparison
    key_features = [
        "close_position_20", "breakout_strength", "dist_ema9_atr", "dist_ema21_atr",
        "ema_alignment", "ema21_bounce", "ema50_bounce", "squeeze_score",
        "vol_ratio", "body_ratio", "lower_wick_ratio", "upper_wick_ratio",
        "momentum_z", "consecutive_bars", "range_expansion", "pullback_depth",
        "ret_1_pct", "ret_3_pct", "atr_pct", "v_reversal",
    ]
    
    print("\n" + "=" * 80)
    print("KEY FEATURE PROFILES PER PATTERN GROUP")
    print("=" * 80)
    
    for _, row in group_df.iterrows():
        prof = row["profile"]
        print(f"\n  {row['group']} (n={row['n']}, WR={row['WR']:.1f}%, PnL={row['PnL']:+.1f}, PF={row['PF']:.2f}):")
        for kf in key_features:
            if kf in prof and pd.notna(prof[kf]) and abs(prof[kf]) > 0.001:
                print(f"    {kf:<25} = {prof[kf]:+.4f}")
    
    # ── Top 3 patterns with edge ──
    print("\n" + "=" * 80)
    print("TOP 3 VISUAL PATTERNS WITH EDGE")
    print("=" * 80)
    
    profitable = group_df[(group_df["n"] >= 5) & (group_df["PF"] > 0.5)].sort_values("PF", ascending=False)
    
    for rank, (_, row) in enumerate(profitable.head(3).iterrows(), 1):
        print(f"\n  #{rank}: {row['group']}")
        print(f"      N={int(row['n'])} WR={row['WR']:.1f}% PnL={row['PnL']:+.1f} PF={row['PF']:.2f} MedDur={row['med_dur']:.1f}m")
        prof = row["profile"]
        # Distinguishing features vs overall mean
        print(f"      Key distinguishing features:")
        for kf in key_features:
            if kf in prof and pd.notna(prof[kf]):
                overall_mean = results[kf].mean()
                diff = prof[kf] - overall_mean
                if abs(diff) > 0.01:
                    print(f"        {kf:<25} = {prof[kf]:+.4f} (diff from avg: {diff:+.4f})")
    
    # ── Save ──
    output_path = OUTPUT_DIR / "trader_pattern_analysis.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        results.to_excel(writer, sheet_name="all_entries", index=False)
        stats_df.to_excel(writer, sheet_name="pattern_stats", index=False)
        gdf_save = group_df.drop(columns=["profile"])
        gdf_save.to_excel(writer, sheet_name="group_stats", index=False)
    
    LOG.info("Results saved to %s", output_path)
    
    # JSON summary
    summary = {
        "total_trades": n_total,
        "overall_wr": float(wr_overall),
        "overall_pnl": float(total_pnl),
        "pattern_groups": [{
            "group": row["group"], "n": int(row["n"]),
            "WR": float(row["WR"]), "PnL": float(row["PnL"]),
            "PF": float(row["PF"]), "avg_pnl": float(row["avg_pnl"]),
            "med_dur": float(row["med_dur"]),
            "key_features": {kf: float(prof[kf]) for kf in key_features if kf in prof and pd.notna(prof[kf])},
        } for _, row in group_df.iterrows() for prof in [row["profile"]]],
    }
    
    json_path = OUTPUT_DIR / "trader_pattern_analysis.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    LOG.info("JSON summary saved to %s", json_path)
    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
