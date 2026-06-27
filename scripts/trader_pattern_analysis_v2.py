"""
trader_pattern_analysis_v2.py — Efficient reverse-engineering of trader's visual patterns.

Key insights from data exploration:
- 12,429 orders, 615 unique symbols, 3,224 close orders (with PNL)
- MEXC semantics: buy long=OPEN, sell long=CLOSE, sell short=OPEN, buy short=CLOSE
- Time range: 2025-06-24 → 2026-06-01 (UTC+2)
- We focus on top 15 symbols (cover ~50% of trades) for OHLCV download efficiency

Strategy:
1. Parse all orders, match open/close pairs → trades
2. Download OHLCV for top symbols only
3. For each CLOSE order (which has PNL), find the matching OPEN
4. At each OPEN, compute pattern features from OHLCV context
5. Measure WR/PnL/PF per pattern type
"""
import sys
import logging
import time
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
LOG = logging.getLogger("pattern_v2")

XLSX_PATH = Path("/home/z/my-project/upload/MEXC - Historial de Ordenes de Futuros-20250624-20260623_1782174256031.xlsx")
OUTPUT_DIR = Path("/home/z/my-project/download")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = Path("/home/z/my-project/ppmt/data/ohlcv_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════
# STEP 1: Parse MEXC orders → trades
# ══════════════════════════════════════════════════════════════════

def parse_mexc_orders(xlsx_path: Path) -> pd.DataFrame:
    """Parse MEXC futures order history into structured trades."""
    LOG.info("Loading MEXC orders from %s", xlsx_path.name)
    df = pd.read_excel(xlsx_path)
    LOG.info("Loaded %d orders", len(df))
    
    # Parse timestamps (UTC+2 → UTC)
    df["_ts"] = pd.to_datetime(df["Tiempo(UTC+02:00)"].astype(str)) - pd.Timedelta(hours=2)
    df["_ts"] = df["_ts"].dt.tz_localize("UTC")
    
    # Parse symbol
    df["_symbol"] = df["Par de Trading de Futuros"].str.strip().str.upper().str.replace("USDT", "", regex=False).str.strip()
    
    # Parse direction/action
    # buy long = OPEN LONG, sell long = CLOSE LONG
    # sell short = OPEN SHORT, buy short = CLOSE SHORT
    df["_action"] = df["Dirección"].str.strip().str.lower().map({
        "buy long": "open_long",
        "sell long": "close_long", 
        "sell short": "open_short",
        "buy short": "close_short",
    })
    
    # Numeric columns
    df["_price"] = pd.to_numeric(df["Precio promedio completo"], errors="coerce")
    df["_pnl"] = pd.to_numeric(df["PNL de Cierre"], errors="coerce").fillna(0)
    df["_fee"] = pd.to_numeric(df["Comisión de Trading"], errors="coerce").fillna(0).abs()
    df["_qty"] = pd.to_numeric(df["Cant. Completada (Cripto)"], errors="coerce").fillna(0)
    df["_qty_usdt"] = pd.to_numeric(df["Cant. Completada (Monto)"], errors="coerce").fillna(0)
    df["_leverage"] = pd.to_numeric(df["Apalancamiento"], errors="coerce").fillna(1)
    
    # Sort by time
    df = df.sort_values("_ts").reset_index(drop=True)
    
    LOG.info("Action distribution: %s", df["_action"].value_counts().to_dict())
    LOG.info("Unique symbols: %d", df["_symbol"].nunique())
    LOG.info("Time range: %s → %s", df["_ts"].min(), df["_ts"].max())
    
    return df


def match_trades(orders: pd.DataFrame) -> pd.DataFrame:
    """Match open/close orders into trades.
    
    Strategy: For each close order, find the most recent unmatched open order
    for the same symbol + direction.
    """
    LOG.info("Matching open/close orders into trades...")
    
    trades = []
    # Track unmatched opens: symbol → direction → list of (ts, price, qty, fee, idx)
    opens = defaultdict(lambda: defaultdict(list))
    
    for idx, row in orders.iterrows():
        action = row.get("_action")
        if not action or pd.isna(action):
            continue
        
        symbol = row["_symbol"]
        ts = row["_ts"]
        price = row["_price"]
        pnl = row["_pnl"]
        fee = row["_fee"]
        qty = row["_qty"]
        leverage = row["_leverage"]
        
        if "open" in action:
            direction = "long" if "long" in action else "short"
            opens[symbol][direction].append({
                "ts": ts, "price": price, "fee": fee, "qty": qty, 
                "leverage": leverage, "idx": idx
            })
        
        elif "close" in action:
            direction = "long" if "long" in action else "short"
            
            # Find matching opens
            open_list = opens[symbol][direction]
            if not open_list:
                # No matching open — create a trade anyway (orphan close)
                trades.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_time": ts - timedelta(minutes=30),  # estimate
                    "entry_price": price,
                    "exit_time": ts,
                    "exit_price": price,
                    "pnl": pnl,
                    "fee": fee,
                    "n_entries": 1,
                    "leverage": leverage,
                })
                continue
            
            # Match with earliest unmatched open (FIFO)
            # But first check if there are multiple opens (DCA/pyramid)
            matched_opens = []
            total_qty = qty
            
            # Take opens until we've covered the close quantity
            remaining_qty = total_qty
            while open_list and remaining_qty > 0:
                o = open_list.pop(0)
                matched_opens.append(o)
                remaining_qty -= o["qty"]
            
            # If we took too many, put the last one back (partial fill)
            if remaining_qty < 0 and matched_opens:
                partial = matched_opens.pop()
                partial["qty"] = -remaining_qty
                open_list.insert(0, partial)
            
            # Create trade from matched opens
            if matched_opens:
                first_open = matched_opens[0]
                total_fee = sum(o["fee"] for o in matched_opens) + fee
                avg_entry = np.average([o["price"] for o in matched_opens], 
                                       weights=[o["qty"] for o in matched_opens])
                
                trades.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_time": first_open["ts"],
                    "entry_price": avg_entry,
                    "exit_time": ts,
                    "exit_price": price,
                    "pnl": pnl,
                    "fee": total_fee,
                    "n_entries": len(matched_opens),
                    "leverage": first_open.get("leverage", leverage),
                })
    
    # Also add still-open positions (not closed)
    for sym, dirs in opens.items():
        for direction, open_list in dirs.items():
            for o in open_list:
                trades.append({
                    "symbol": sym,
                    "direction": direction,
                    "entry_time": o["ts"],
                    "entry_price": o["price"],
                    "exit_time": pd.NaT,
                    "exit_price": 0,
                    "pnl": 0,
                    "fee": o["fee"],
                    "n_entries": 1,
                    "leverage": o.get("leverage", 1),
                })
    
    result = pd.DataFrame(trades)
    
    # Filter to trades with actual PnL (closed trades)
    closed = result[result["pnl"] != 0].copy()
    
    LOG.info("Matched %d total trades, %d closed (with PnL)", len(result), len(closed))
    if len(closed) > 0:
        wr = (closed["pnl"] > 0).mean() * 100
        total_pnl = closed["pnl"].sum()
        LOG.info("Closed trades: WR=%.1f%% PnL=%.2f", wr, total_pnl)
    
    return result


# ══════════════════════════════════════════════════════════════════
# STEP 2: Download OHLCV (efficient, top symbols only)
# ══════════════════════════════════════════════════════════════════

def download_ohlcv(symbol: str, timeframe: str = "5m", 
                    start_ts: int = None, end_ts: int = None) -> pd.DataFrame:
    """Download OHLCV with caching."""
    import ccxt
    
    cache_file = CACHE_DIR / f"{symbol}_{timeframe}.parquet"
    
    # Check cache
    if cache_file.exists():
        cached = pd.read_parquet(cache_file)
        # Check if cache covers our range
        if len(cached) > 0 and cached["timestamp"].min() <= start_ts and cached["timestamp"].max() >= end_ts:
            # Filter to range
            return cached[(cached["timestamp"] >= start_ts) & (cached["timestamp"] <= end_ts)].copy()
        elif len(cached) > 0:
            # Extend cache
            existing_start = cached["timestamp"].min()
            existing_end = cached["timestamp"].max()
            # Download missing parts
            all_data = [cached]
            
            if start_ts < existing_start:
                new_data = _fetch_ccxt(symbol, timeframe, start_ts, existing_start)
                if len(new_data) > 0:
                    all_data.append(new_data)
            
            if end_ts > existing_end:
                new_data = _fetch_ccxt(symbol, timeframe, existing_end, end_ts)
                if len(new_data) > 0:
                    all_data.append(new_data)
            
            combined = pd.concat(all_data).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
            combined.to_parquet(cache_file, index=False)
            return combined[(combined["timestamp"] >= start_ts) & (combined["timestamp"] <= end_ts)].copy()
    
    # No cache — download fresh
    data = _fetch_ccxt(symbol, timeframe, start_ts, end_ts)
    if len(data) > 0:
        data.to_parquet(cache_file, index=False)
    return data


def _fetch_ccxt(symbol: str, timeframe: str, start_ts: int, end_ts: int,
                 max_retries: int = 2) -> pd.DataFrame:
    """Fetch OHLCV from Binance, fallback to MEXC."""
    import ccxt
    
    for exchange_id in ["binance", "mexc"]:
        for attempt in range(max_retries):
            try:
                exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
                ccxt_sym = f"{symbol}/USDT"
                
                exchange.load_markets()
                if ccxt_sym not in exchange.markets:
                    LOG.debug("  %s not on %s", ccxt_sym, exchange_id)
                    break
                
                all_ohlcv = []
                since = start_ts
                
                while True:
                    ohlcv = exchange.fetch_ohlcv(ccxt_sym, timeframe, since=since, limit=1000)
                    if not ohlcv:
                        break
                    all_ohlcv.extend(ohlcv)
                    last_ts = ohlcv[-1][0]
                    if last_ts >= end_ts or len(ohlcv) < 1000:
                        break
                    since = last_ts + 1
                    time.sleep(exchange.rateLimit / 1000)
                
                if all_ohlcv:
                    df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
                    df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]
                    LOG.info("  Downloaded %d bars for %s from %s", len(df), symbol, exchange_id)
                    return df
                else:
                    break
                    
            except Exception as e:
                if attempt == max_retries - 1:
                    LOG.debug("  %s on %s failed: %s", symbol, exchange_id, e)
                time.sleep(2)
    
    return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════
# STEP 3: Pattern Feature Computation
# ══════════════════════════════════════════════════════════════════

def compute_pattern_features(closes, highs, lows, opens, volumes, i, lookback=50):
    """Compute pattern detector features at bar i using only past data."""
    n = len(closes)
    if i < lookback or i >= n:
        return None
    
    c = closes[:i+1]
    h = highs[:i+1]
    l = lows[:i+1]
    o = opens[:i+1]
    v = volumes[:i+1]
    
    f = {}  # features dict
    
    # ── BREAKOUT: price at/beyond recent range edge ──
    high_20 = np.max(h[-20:])
    low_20 = np.min(l[-20:])
    range_20 = high_20 - low_20
    
    if range_20 > 0:
        f["close_position_20"] = (c[-1] - low_20) / range_20  # 0=at low, 1=at high
        f["is_breakout_up"] = float(c[-1] >= high_20)
        f["is_breakout_down"] = float(c[-1] <= low_20)
        f["breakout_strength"] = max(0, abs(c[-1] - (high_20 if c[-1] >= high_20 else low_20)) / range_20)
    else:
        f["close_position_20"] = 0.5
        f["is_breakout_up"] = 0.0
        f["is_breakout_down"] = 0.0
        f["breakout_strength"] = 0.0
    
    # ── EMA BOUNCE: distance to moving averages ──
    ema9 = _ema(c, 9)
    ema21 = _ema(c, 21)
    ema50 = _ema(c, 50) if len(c) >= 50 else _ema(c, min(len(c), 21))
    
    atr = _atr(h, l, c, 14)
    
    if atr > 0:
        f["dist_ema9_atr"] = (c[-1] - ema9[-1]) / atr
        f["dist_ema21_atr"] = (c[-1] - ema21[-1]) / atr
        f["dist_ema50_atr"] = (c[-1] - ema50[-1]) / atr
    else:
        f["dist_ema9_atr"] = 0
        f["dist_ema21_atr"] = 0
        f["dist_ema50_atr"] = 0
    
    # EMA touch/bounce detection
    f["ema21_bounce"] = 0.0
    f["ema50_bounce"] = 0.0
    if atr > 0 and len(c) >= 6:
        for j in range(1, min(4, len(c))):
            if l[-j] <= ema21[-j] + 0.3 * atr and h[-j] >= ema21[-j] - 0.3 * atr:
                f["ema21_bounce"] = 1.0
                break
        for j in range(1, min(6, len(c))):
            if l[-j] <= ema50[-j] + 0.3 * atr and h[-j] >= ema50[-j] - 0.3 * atr:
                f["ema50_bounce"] = 1.0
                break
    
    f["ema_alignment"] = float(np.sign(ema9[-1] - ema21[-1]))
    f["ema_trend_strength"] = (ema9[-1] - ema50[-1]) / max(abs(ema50[-1]), 1e-10) * 100
    
    # ── SQUEEZE: Bollinger Band compression ──
    if len(c) >= 20:
        sma20 = np.mean(c[-20:])
        std20 = np.std(c[-20:])
        bb_width = 4 * std20 / max(sma20, 1e-10)
        f["bb_width"] = bb_width
        
        # Percentile vs last 30 windows
        bb_list = []
        for j in range(1, min(30, len(c) - 20)):
            seg = c[-(20+j):-(j)]
            if len(seg) >= 20:
                s, sd = np.mean(seg), np.std(seg)
                if s > 0:
                    bb_list.append(4 * sd / s)
        f["squeeze_score"] = 1.0 - np.mean([w <= bb_width for w in bb_list]) if bb_list else 0.5
    else:
        f["bb_width"] = 0
        f["squeeze_score"] = 0.5
    
    # ── VOLUME ──
    if len(v) >= 20:
        vol_ma = np.mean(v[-20:])
        f["vol_ratio"] = v[-1] / max(vol_ma, 1e-10)
        f["vol_ratio_3"] = np.mean(v[-3:]) / max(vol_ma, 1e-10)
    else:
        f["vol_ratio"] = 1.0
        f["vol_ratio_3"] = 1.0
    
    # ── CANDLE PATTERNS ──
    body = c[-1] - o[-1]
    bar_range = max(h[-1] - l[-1], 1e-10)
    upper_wick = h[-1] - max(o[-1], c[-1])
    lower_wick = min(o[-1], c[-1]) - l[-1]
    
    f["body_ratio"] = abs(body) / bar_range
    f["lower_wick_ratio"] = lower_wick / bar_range
    f["upper_wick_ratio"] = upper_wick / bar_range
    f["is_doji"] = float(f["body_ratio"] < 0.1)
    f["is_hammer"] = float(f["lower_wick_ratio"] > 0.4 and f["upper_wick_ratio"] < 0.15)
    f["is_shooting_star"] = float(f["upper_wick_ratio"] > 0.4 and f["lower_wick_ratio"] < 0.15)
    f["is_bull_pin"] = float(f["lower_wick_ratio"] > 0.6 and f["body_ratio"] < 0.3)
    f["is_bear_pin"] = float(f["upper_wick_ratio"] > 0.6 and f["body_ratio"] < 0.3)
    
    if len(c) >= 2:
        prev_body = c[-2] - o[-2]
        f["is_bullish_engulf"] = float(body > 0 and prev_body < 0 and c[-1] > o[-2] and o[-1] < c[-2])
        f["is_bearish_engulf"] = float(body < 0 and prev_body > 0 and c[-1] < o[-2] and o[-1] > c[-2])
    else:
        f["is_bullish_engulf"] = 0.0
        f["is_bearish_engulf"] = 0.0
    
    # ── PULLBACK IN TREND ──
    trend_dir = np.sign(ema9[-1] - ema50[-1])
    high_6 = np.max(h[-6:]) if len(h) >= 6 else np.max(h)
    low_6 = np.min(l[-6:]) if len(l) >= 6 else np.min(l)
    
    if high_6 > low_6:
        if trend_dir > 0:
            f["pullback_depth"] = (high_6 - c[-1]) / (high_6 - low_6)
            f["is_pullback_long"] = float(f["pullback_depth"] > 0.3 and trend_dir > 0)
            f["is_pullback_short"] = 0.0
        elif trend_dir < 0:
            f["pullback_depth"] = (c[-1] - low_6) / (high_6 - low_6)
            f["is_pullback_short"] = float(f["pullback_depth"] > 0.3 and trend_dir < 0)
            f["is_pullback_long"] = 0.0
        else:
            f["pullback_depth"] = 0.5
            f["is_pullback_long"] = 0.0
            f["is_pullback_short"] = 0.0
    else:
        f["pullback_depth"] = 0.5
        f["is_pullback_long"] = 0.0
        f["is_pullback_short"] = 0.0
    
    # ── MOMENTUM / IMPULSE ──
    f["ret_1_pct"] = (c[-1] - c[-2]) / max(c[-2], 1e-10) * 100 if len(c) >= 2 else 0
    f["ret_3_pct"] = (c[-1] - c[-4]) / max(c[-4], 1e-10) * 100 if len(c) >= 4 else 0
    f["ret_6_pct"] = (c[-1] - c[-7]) / max(c[-7], 1e-10) * 100 if len(c) >= 7 else 0
    
    if len(c) > 10:
        rets = np.diff(c[-50:]) / np.maximum(c[-50:-1], 1e-10) if len(c) > 50 else np.diff(c) / np.maximum(c[:-1], 1e-10)
        f["momentum_z"] = (f["ret_1_pct"] / 100) / max(np.std(rets), 1e-10) if len(rets) > 5 else 0
    else:
        f["momentum_z"] = 0
    
    # Consecutive bars
    consec = 0
    for j in range(1, min(10, len(c) - 1)):
        if (c[-j] - c[-j-1]) * np.sign(f["ret_1_pct"] + 1e-20) > 0:
            consec += 1
        else:
            break
    f["consecutive_bars"] = consec * np.sign(f["ret_1_pct"])
    
    # ── SUPPORT/RESISTANCE TEST ──
    high_50 = np.max(h[-50:]) if len(h) >= 50 else np.max(h)
    low_50 = np.min(l[-50:]) if len(l) >= 50 else np.min(l)
    f["near_high_50"] = float(abs(c[-1] - high_50) / max(high_50, 1e-10) < 0.01)
    f["near_low_50"] = float(abs(c[-1] - low_50) / max(low_50, 1e-10) < 0.01)
    
    # ── RANGE EXPANSION ──
    if len(h) >= 3:
        range_3 = np.mean([h[-j] - l[-j] for j in range(1, min(4, len(h)+1))])
        range_20r = np.mean([h[-j] - l[-j] for j in range(1, min(21, len(h)+1))])
        f["range_expansion"] = range_3 / max(range_20r, 1e-10)
    else:
        f["range_expansion"] = 1.0
    
    # ── V-REVERSAL ──
    if len(c) >= 7:
        ret_first = (c[-4] - c[-7]) / max(c[-7], 1e-10)
        ret_second = (c[-1] - c[-4]) / max(c[-4], 1e-10)
        f["v_reversal"] = float(np.sign(ret_first) != np.sign(ret_second) and abs(ret_second) > abs(ret_first) * 0.5)
    else:
        f["v_reversal"] = 0.0
    
    # ── META ──
    f["atr_pct"] = atr / max(c[-1], 1e-10) * 100
    
    return f


def _ema(data, period):
    if len(data) < period:
        return np.full(len(data), data[-1] if len(data) > 0 else 0)
    alpha = 2 / (period + 1)
    result = np.zeros(len(data))
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
    return result


def _atr(highs, lows, closes, period=14):
    if len(closes) < 2:
        return 0.0
    n = min(period, len(closes) - 1)
    trs = []
    for i in range(max(1, len(closes) - n), len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return np.mean(trs) if trs else 0.0


# ══════════════════════════════════════════════════════════════════
# STEP 4: Classify pattern
# ══════════════════════════════════════════════════════════════════

def classify_pattern(feat: dict) -> str:
    """Classify the visual pattern at entry."""
    # Priority: most visually salient patterns first
    
    # 1. BREAKOUT with volume
    if (feat.get("is_breakout_up") or feat.get("is_breakout_down")) and feat.get("vol_ratio", 1) > 1.3:
        return "BREAKOUT_UP" if feat.get("is_breakout_up") else "BREAKOUT_DOWN"
    
    # 2. BREAKOUT (any)
    if feat.get("breakout_strength", 0) > 0.1:
        return "BREAKOUT_UP" if feat.get("close_position_20", 0.5) > 0.9 else "BREAKOUT_DOWN"
    
    # 3. EMA BOUNCE
    if feat.get("ema21_bounce") or feat.get("ema50_bounce"):
        return "EMA_BOUNCE_LONG" if feat.get("ema_alignment", 0) > 0 else "EMA_BOUNCE_SHORT"
    
    # 4. PULLBACK IN TREND
    if feat.get("is_pullback_long"): return "PULLBACK_LONG"
    if feat.get("is_pullback_short"): return "PULLBACK_SHORT"
    
    # 5. SQUEEZE BREAKOUT
    if feat.get("squeeze_score", 0) > 0.7 and feat.get("range_expansion", 1) > 1.5:
        return "SQUEEZE_BREAK"
    
    # 6. V-REVERSAL
    if feat.get("v_reversal"):
        return "V_REVERSAL_UP" if feat.get("ret_1_pct", 0) > 0 else "V_REVERSAL_DOWN"
    
    # 7. CANDLE PATTERNS
    if feat.get("is_bullish_engulf"): return "ENGULFING_BULL"
    if feat.get("is_bearish_engulf"): return "ENGULFING_BEAR"
    if feat.get("is_hammer"): return "HAMMER"
    if feat.get("is_shooting_star"): return "SHOOTING_STAR"
    if feat.get("is_bull_pin"): return "PIN_BAR_BULL"
    if feat.get("is_bear_pin"): return "PIN_BAR_BEAR"
    
    # 8. MOMENTUM IMPULSE
    if abs(feat.get("momentum_z", 0)) > 2.0:
        return "IMPULSE_UP" if feat.get("momentum_z") > 0 else "IMPULSE_DOWN"
    
    # 9. LEVEL TEST
    if feat.get("near_low_50"): return "SUPPORT_TEST"
    if feat.get("near_high_50"): return "RESISTANCE_TEST"
    
    return "NO_PATTERN"


def group_pattern(p: str) -> str:
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
    LOG.info("TRADER VISUAL PATTERN RECOGNITION ANALYSIS v2")
    LOG.info("=" * 70)
    
    # Step 1: Parse orders
    orders = parse_mexc_orders(XLSX_PATH)
    
    # Step 2: Match trades
    trades = match_trades(orders)
    
    # Focus on closed trades with PnL
    closed = trades[trades["pnl"] != 0].copy()
    LOG.info("Closed trades: %d", len(closed))
    
    # Get top symbols (cover most trades)
    sym_counts = closed["symbol"].value_counts()
    top_symbols = sym_counts.head(20).index.tolist()
    LOG.info("Top 20 symbols cover %d/%d trades (%.0f%%)",
             sym_counts.head(20).sum(), len(closed), sym_counts.head(20).sum() / len(closed) * 100)
    
    # Step 3: Download OHLCV for top symbols
    time_range = closed["entry_time"].agg(["min", "max"])
    start_ts = int((time_range["min"] - pd.Timedelta(days=3)).timestamp() * 1000)
    end_ts = int((time_range["max"] + pd.Timedelta(days=1)).timestamp() * 1000)
    
    ohlcv_data = {}
    for sym in top_symbols:
        LOG.info("Downloading %s...", sym)
        try:
            df = download_ohlcv(sym, "5m", start_ts, end_ts)
            if len(df) > 0:
                ohlcv_data[sym] = df
                LOG.info("  %s: %d bars", sym, len(df))
        except Exception as e:
            LOG.warning("  %s failed: %s", sym, e)
    
    # BTC for context
    if "BTC" not in ohlcv_data:
        try:
            ohlcv_data["BTC"] = download_ohlcv("BTC", "5m", start_ts, end_ts)
        except:
            pass
    
    LOG.info("OHLCV downloaded for %d symbols", len(ohlcv_data))
    
    # Step 4: Compute pattern features for each trade entry
    LOG.info("Computing pattern features for each trade entry...")
    
    # Filter to trades with OHLCV data
    trades_with_data = closed[closed["symbol"].isin(ohlcv_data.keys())].copy()
    LOG.info("Trades with OHLCV: %d/%d", len(trades_with_data), len(closed))
    
    records = []
    for _, trade in trades_with_data.iterrows():
        sym = trade["symbol"]
        ohlcv = ohlcv_data[sym]
        
        if len(ohlcv) < 60:
            continue
        
        entry_ts_ms = int(trade["entry_time"].timestamp() * 1000)
        
        # Find closest bar to entry time
        time_diffs = np.abs(ohlcv["timestamp"].values - entry_ts_ms)
        bar_idx = np.argmin(time_diffs)
        
        # Skip if too far (15min)
        if time_diffs[bar_idx] > 15 * 60 * 1000:
            continue
        
        if bar_idx < 50:
            continue
        
        feat = compute_pattern_features(
            ohlcv["close"].values, ohlcv["high"].values,
            ohlcv["low"].values, ohlcv["open"].values,
            ohlcv["volume"].values, bar_idx, lookback=50,
        )
        
        if feat is None:
            continue
        
        pattern = classify_pattern(feat)
        feat["pattern"] = pattern
        feat["pattern_group"] = group_pattern(pattern)
        feat["symbol"] = sym
        feat["pnl"] = trade["pnl"]
        feat["direction"] = trade["direction"]
        feat["n_entries"] = trade["n_entries"]
        feat["leverage"] = trade["leverage"]
        feat["is_win"] = float(trade["pnl"] > 0)
        
        # Duration
        if pd.notna(trade.get("exit_time")):
            feat["duration_min"] = (trade["exit_time"] - trade["entry_time"]).total_seconds() / 60
        else:
            feat["duration_min"] = 0
        
        records.append(feat)
    
    if not records:
        LOG.error("No pattern records created!")
        return
    
    results = pd.DataFrame(records)
    LOG.info("Created %d pattern records", len(results))
    
    # Step 5: Analyze patterns
    n_total = len(results)
    wr_overall = results["is_win"].mean() * 100
    pnl_overall = results["pnl"].sum()
    
    print("\n" + "=" * 100)
    print("VISUAL PATTERN ANALYSIS RESULTS")
    print("=" * 100)
    print(f"\n  Total entries analyzed: {n_total}")
    print(f"  Overall WR: {wr_overall:.1f}%")
    print(f"  Overall PnL: {pnl_overall:+.2f} USDT")
    
    # ── Per-pattern stats ──
    pattern_stats = []
    for pattern in sorted(results["pattern"].unique()):
        sub = results[results["pattern"] == pattern]
        n = len(sub)
        if n < 2:
            continue
        wr = sub["is_win"].mean() * 100
        pnl = sub["pnl"].sum()
        avg_pnl = sub["pnl"].mean()
        gains = sub.loc[sub["pnl"] > 0, "pnl"].sum()
        losses = abs(sub.loc[sub["pnl"] < 0, "pnl"].sum())
        pf = gains / max(losses, 1e-10)
        med_dur = sub["duration_min"].median()
        avg_ent = sub["n_entries"].mean()
        
        pattern_stats.append({
            "pattern": pattern, "n": n, "pct": n/n_total*100,
            "WR": wr, "PnL": pnl, "avg_pnl": avg_pnl, "PF": pf,
            "med_dur": med_dur, "avg_entries": avg_ent,
        })
    
    pstats_df = pd.DataFrame(pattern_stats).sort_values("PnL", ascending=False)
    
    print(f"\n{'Pattern':<22} {'N':>5} {'%':>5} {'WR%':>6} {'PnL':>10} {'AvgPnL':>9} {'PF':>6} {'MedDur':>7} {'AvgEnt':>6}")
    print("-" * 90)
    for _, row in pstats_df.iterrows():
        print(f"{row['pattern']:<22} {row['n']:>5} {row['pct']:>4.1f}% "
              f"{row['WR']:>5.1f}% {row['PnL']:>+10.1f} {row['avg_pnl']:>+8.3f} "
              f"{row['PF']:>5.2f} {row['med_dur']:>6.1f}m {row['avg_entries']:>5.1f}")
    
    # ── Per-group stats ──
    print("\n" + "=" * 80)
    print("PATTERN GROUPS (aggregated)")
    print("=" * 80)
    
    group_stats = []
    for group in sorted(results["pattern_group"].unique()):
        sub = results[results["pattern_group"] == group]
        n = len(sub)
        if n < 2:
            continue
        wr = sub["is_win"].mean() * 100
        pnl = sub["pnl"].sum()
        avg_pnl = sub["pnl"].mean()
        gains = sub.loc[sub["pnl"] > 0, "pnl"].sum()
        losses = abs(sub.loc[sub["pnl"] < 0, "pnl"].sum())
        pf = gains / max(losses, 1e-10)
        med_dur = sub["duration_min"].median()
        
        # Feature profile
        feat_cols = [c for c in results.columns if c not in 
                     ["pattern", "pattern_group", "symbol", "pnl", "is_win",
                      "direction", "n_entries", "leverage", "duration_min"]]
        profile = {}
        for fc in feat_cols:
            try:
                profile[fc] = float(sub[fc].mean())
            except:
                pass
        
        group_stats.append({
            "group": group, "n": n, "pct": n/n_total*100,
            "WR": wr, "PnL": pnl, "avg_pnl": avg_pnl, "PF": pf,
            "med_dur": med_dur, "profile": profile,
        })
    
    gstats_df = pd.DataFrame(group_stats).sort_values("PnL", ascending=False)
    
    print(f"{'Group':<22} {'N':>5} {'%':>5} {'WR%':>6} {'PnL':>10} {'PF':>6} {'MedDur':>7}")
    print("-" * 65)
    for _, row in gstats_df.iterrows():
        print(f"{row['group']:<22} {row['n']:>5} {row['pct']:>4.1f}% "
              f"{row['WR']:>5.1f}% {row['PnL']:>+10.1f} {row['PF']:>5.2f} {row['med_dur']:>6.1f}m")
    
    # ── Feature profiles ──
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
    
    for _, row in gstats_df.iterrows():
        prof = row["profile"]
        print(f"\n  {row['group']} (n={row['n']}, WR={row['WR']:.1f}%, PnL={row['PnL']:+.1f}, PF={row['PF']:.2f}):")
        for kf in key_features:
            if kf in prof and abs(prof[kf]) > 0.001:
                print(f"    {kf:<25} = {prof[kf]:+.4f}")
    
    # ── Top 3 patterns ──
    print("\n" + "=" * 80)
    print("TOP 3 VISUAL PATTERNS WITH EDGE (for v8 feature encoding)")
    print("=" * 80)
    
    profitable = gstats_df[(gstats_df["n"] >= 5) & (gstats_df["PF"] > 0.5)].sort_values("PF", ascending=False)
    
    for rank, (_, row) in enumerate(profitable.head(5).iterrows(), 1):
        prof = row["profile"]
        print(f"\n  #{rank}: {row['group']}")
        print(f"      N={int(row['n'])} WR={row['WR']:.1f}% PnL={row['PnL']:+.1f} PF={row['PF']:.2f} MedDur={row['med_dur']:.1f}m")
        print(f"      Key distinguishing features vs overall average:")
        for kf in key_features:
            if kf in prof:
                overall_mean = results[kf].mean()
                diff = prof[kf] - overall_mean
                if abs(diff) > 0.01:
                    print(f"        {kf:<25} = {prof[kf]:+.4f} (diff: {diff:+.4f})")
    
    # ── Direction analysis within patterns ──
    print("\n" + "=" * 80)
    print("PATTERN × DIRECTION MATRIX")
    print("=" * 80)
    
    for group in sorted(results["pattern_group"].unique()):
        sub = results[results["pattern_group"] == group]
        if len(sub) < 5:
            continue
        for direction in ["long", "short"]:
            dsub = sub[sub["direction"] == direction]
            if len(dsub) < 3:
                continue
            wr = dsub["is_win"].mean() * 100
            pnl = dsub["pnl"].sum()
            print(f"  {group:<22} {direction:<6}: N={len(dsub):>4} WR={wr:>5.1f}% PnL={pnl:>+8.1f}")
    
    # ── Save results ──
    output_path = OUTPUT_DIR / "trader_pattern_analysis.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        results.to_excel(writer, sheet_name="all_entries", index=False)
        pstats_df.to_excel(writer, sheet_name="pattern_stats", index=False)
        gstats_df.drop(columns=["profile"]).to_excel(writer, sheet_name="group_stats", index=False)
    
    LOG.info("Saved to %s", output_path)
    
    # JSON summary
    summary = {
        "total_entries": n_total,
        "overall_wr": float(wr_overall),
        "overall_pnl": float(pnl_overall),
        "pattern_groups": [{
            "group": row["group"], "n": int(row["n"]),
            "WR": float(row["WR"]), "PnL": float(row["PnL"]),
            "PF": float(row["PF"]), "med_dur_min": float(row["med_dur"]),
            "key_features": {kf: float(prof[kf]) for kf in key_features if kf in prof and abs(prof[kf]) > 0.001},
        } for _, row in gstats_df.iterrows() for prof in [row["profile"]]],
    }
    
    json_path = OUTPUT_DIR / "trader_pattern_analysis.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    LOG.info("JSON saved to %s", json_path)
    
    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
