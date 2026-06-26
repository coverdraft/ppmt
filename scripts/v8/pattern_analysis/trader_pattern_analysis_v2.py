"""
trader_pattern_analysis_v2.py
Reverse-engineer the trader's visual pattern recognition.

WHAT THIS DOES:
1. Reads the MEXC futures order history XLSX
2. Matches open/close orders into trades
3. Downloads 5m OHLCV for top 30 symbols from Binance/MEXC
4. For each trade entry, computes ~30 pattern detector features
5. Classifies each entry into a visual pattern type
6. Measures WR, PnL, Profit Factor per pattern
7. Saves results to Excel + JSON

USAGE:
  python3 trader_pattern_analysis_v2.py                          # uses default XLSX in same dir
  python3 trader_pattern_analysis_v2.py path/to/orders.xlsx      # custom XLSX path

Dependencies: pip3 install pandas openpyxl ccxt numpy scikit-learn
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

# ── CONFIG ──
SCRIPT_DIR = Path(__file__).parent.resolve()
XLSX_NAME = "MEXC - Historial de Ordenes de Futuros-20250624-20260623_1782174256031.xlsx"
# XLSX_PATH resolved in main() — supports CLI arg or same-dir fallback
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = SCRIPT_DIR / "ohlcv_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TOP_N_SYMBOLS = 30      # Download OHLCV for top N most-traded symbols
TIMEFRAME = "5m"

# ══════════════════════════════════════════════════════════════════
# STEP 1: Parse MEXC orders → trades
# ══════════════════════════════════════════════════════════════════

def parse_mexc_orders(xlsx_path: Path) -> pd.DataFrame:
    """Parse MEXC futures order history."""
    LOG.info("Loading MEXC orders from %s", xlsx_path.name)
    df = pd.read_excel(xlsx_path)
    LOG.info("Loaded %d orders", len(df))
    
    # Parse timestamps (MEXC gives UTC+2, convert to UTC)
    df["_ts"] = pd.to_datetime(df["Tiempo(UTC+02:00)"].astype(str)) - pd.Timedelta(hours=2)
    df["_ts"] = df["_ts"].dt.tz_localize("UTC")
    
    # Parse symbol: "RIVERUSDT" → "RIVER"
    df["_symbol"] = df["Par de Trading de Futuros"].str.strip().str.upper().str.replace("USDT", "", regex=False).str.strip()
    
    # Parse direction: MEXC semantics
    #   buy long  = OPEN LONG   (PNL always 0)
    #   sell long = CLOSE LONG  (PNL = trade result)
    #   sell short = OPEN SHORT  (PNL always 0)
    #   buy short = CLOSE SHORT  (PNL = trade result)
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
    
    df = df.sort_values("_ts").reset_index(drop=True)
    
    LOG.info("Action distribution: %s", df["_action"].value_counts().to_dict())
    LOG.info("Unique symbols: %d", df["_symbol"].nunique())
    LOG.info("Time range: %s → %s", df["_ts"].min(), df["_ts"].max())
    
    return df


def match_trades(orders: pd.DataFrame) -> pd.DataFrame:
    """Match open/close orders into trades (FIFO matching)."""
    LOG.info("Matching open/close orders into trades...")
    
    trades = []
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
            open_list = opens[symbol][direction]
            
            if not open_list:
                trades.append({
                    "symbol": symbol, "direction": direction,
                    "entry_time": ts - timedelta(minutes=30),
                    "entry_price": price, "exit_time": ts, "exit_price": price,
                    "pnl": pnl, "fee": fee, "n_entries": 1, "leverage": leverage,
                })
                continue
            
            # FIFO matching
            matched = []
            remaining = qty
            while open_list and remaining > 0:
                o = open_list.pop(0)
                matched.append(o)
                remaining -= o["qty"]
            
            if remaining < 0 and matched:
                partial = matched.pop()
                partial["qty"] = -remaining
                open_list.insert(0, partial)
            
            if matched:
                first = matched[0]
                total_fee = sum(o["fee"] for o in matched) + fee
                avg_entry = np.average([o["price"] for o in matched],
                                       weights=[o["qty"] for o in matched])
                trades.append({
                    "symbol": symbol, "direction": direction,
                    "entry_time": first["ts"], "entry_price": avg_entry,
                    "exit_time": ts, "exit_price": price,
                    "pnl": pnl, "fee": total_fee,
                    "n_entries": len(matched), "leverage": first.get("leverage", leverage),
                })
    
    # Remaining open positions
    for sym, dirs in opens.items():
        for direction, open_list in dirs.items():
            for o in open_list:
                trades.append({
                    "symbol": sym, "direction": direction,
                    "entry_time": o["ts"], "entry_price": o["price"],
                    "exit_time": pd.NaT, "exit_price": 0,
                    "pnl": 0, "fee": o["fee"],
                    "n_entries": 1, "leverage": o.get("leverage", 1),
                })
    
    result = pd.DataFrame(trades)
    closed = result[result["pnl"] != 0].copy()
    LOG.info("Matched %d total, %d closed (with PnL)", len(result), len(closed))
    if len(closed) > 0:
        LOG.info("Closed: WR=%.1f%% PnL=%.2f", (closed["pnl"] > 0).mean()*100, closed["pnl"].sum())
    return result


# ══════════════════════════════════════════════════════════════════
# STEP 2: Download OHLCV
# ══════════════════════════════════════════════════════════════════

def download_ohlcv(symbol: str, timeframe: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    """Download with disk caching."""
    import ccxt
    
    cache_file = CACHE_DIR / f"{symbol}_{timeframe}.parquet"
    
    if cache_file.exists():
        cached = pd.read_parquet(cache_file)
        if len(cached) > 0 and cached["timestamp"].min() <= start_ts + 86400000 and cached["timestamp"].max() >= end_ts - 86400000:
            return cached[(cached["timestamp"] >= start_ts) & (cached["timestamp"] <= end_ts)].copy()
    
    for exchange_id in ["binance", "mexc"]:
        try:
            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            ccxt_sym = f"{symbol}/USDT"
            exchange.load_markets()
            if ccxt_sym not in exchange.markets:
                continue
            
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
                df.to_parquet(cache_file, index=False)
                LOG.info("  %s: %d bars from %s (cached)", symbol, len(df), exchange_id)
                return df
        except Exception as e:
            LOG.warning("  %s on %s: %s", symbol, exchange_id, str(e)[:80])
            time.sleep(2)
    
    LOG.warning("  %s: no data available", symbol)
    return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════
# STEP 3: Pattern Feature Computation
# ══════════════════════════════════════════════════════════════════

def compute_pattern_features(closes, highs, lows, opens, volumes, i, lookback=50):
    """Compute ~30 pattern detector features at bar i (only past data)."""
    n = len(closes)
    if i < lookback or i >= n:
        return None
    
    c = closes[:i+1]
    h = highs[:i+1]
    l = lows[:i+1]
    o = opens[:i+1]
    v = volumes[:i+1]
    f = {}
    
    # ── BREAKOUT ──
    high_20 = np.max(h[-20:])
    low_20 = np.min(l[-20:])
    range_20 = high_20 - low_20
    if range_20 > 0:
        f["close_position_20"] = (c[-1] - low_20) / range_20
        f["is_breakout_up"] = float(c[-1] >= high_20)
        f["is_breakout_down"] = float(c[-1] <= low_20)
        f["breakout_strength"] = max(0, abs(c[-1] - (high_20 if c[-1] >= high_20 else low_20)) / range_20)
    else:
        f["close_position_20"] = 0.5; f["is_breakout_up"] = 0; f["is_breakout_down"] = 0; f["breakout_strength"] = 0
    
    # ── EMA BOUNCE ──
    ema9 = _ema(c, 9); ema21 = _ema(c, 21); ema50 = _ema(c, 50) if len(c) >= 50 else _ema(c, min(len(c), 21))
    atr = _atr(h, l, c, 14)
    
    if atr > 0:
        f["dist_ema9_atr"] = (c[-1] - ema9[-1]) / atr
        f["dist_ema21_atr"] = (c[-1] - ema21[-1]) / atr
        f["dist_ema50_atr"] = (c[-1] - ema50[-1]) / atr
    else:
        f["dist_ema9_atr"] = 0; f["dist_ema21_atr"] = 0; f["dist_ema50_atr"] = 0
    
    f["ema21_bounce"] = 0.0; f["ema50_bounce"] = 0.0
    if atr > 0 and len(c) >= 6:
        for j in range(1, min(4, len(c))):
            if l[-j] <= ema21[-j] + 0.3*atr and h[-j] >= ema21[-j] - 0.3*atr:
                f["ema21_bounce"] = 1.0; break
        for j in range(1, min(6, len(c))):
            if l[-j] <= ema50[-j] + 0.3*atr and h[-j] >= ema50[-j] - 0.3*atr:
                f["ema50_bounce"] = 1.0; break
    
    f["ema_alignment"] = float(np.sign(ema9[-1] - ema21[-1]))
    f["ema_trend_strength"] = (ema9[-1] - ema50[-1]) / max(abs(ema50[-1]), 1e-10) * 100
    
    # ── SQUEEZE ──
    if len(c) >= 20:
        sma20 = np.mean(c[-20:]); std20 = np.std(c[-20:])
        bb_width = 4*std20 / max(sma20, 1e-10)
        f["bb_width"] = bb_width
        bb_list = []
        for j in range(1, min(30, len(c)-20)):
            seg = c[-(20+j):-(j)]
            if len(seg) >= 20:
                s, sd = np.mean(seg), np.std(seg)
                if s > 0: bb_list.append(4*sd/s)
        f["squeeze_score"] = 1.0 - np.mean([w <= bb_width for w in bb_list]) if bb_list else 0.5
    else:
        f["bb_width"] = 0; f["squeeze_score"] = 0.5
    
    # ── VOLUME ──
    if len(v) >= 20:
        vol_ma = np.mean(v[-20:])
        f["vol_ratio"] = v[-1] / max(vol_ma, 1e-10)
        f["vol_ratio_3"] = np.mean(v[-3:]) / max(vol_ma, 1e-10)
    else:
        f["vol_ratio"] = 1.0; f["vol_ratio_3"] = 1.0
    
    # ── CANDLE PATTERNS ──
    body = c[-1] - o[-1]
    bar_range = max(h[-1] - l[-1], 1e-10)
    f["body_ratio"] = abs(body) / bar_range
    f["lower_wick_ratio"] = (min(o[-1], c[-1]) - l[-1]) / bar_range
    f["upper_wick_ratio"] = (h[-1] - max(o[-1], c[-1])) / bar_range
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
        f["is_bullish_engulf"] = 0; f["is_bearish_engulf"] = 0
    
    # ── PULLBACK ──
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
            f["pullback_depth"] = 0.5; f["is_pullback_long"] = 0; f["is_pullback_short"] = 0
    else:
        f["pullback_depth"] = 0.5; f["is_pullback_long"] = 0; f["is_pullback_short"] = 0
    
    # ── MOMENTUM ──
    f["ret_1_pct"] = (c[-1] - c[-2]) / max(c[-2], 1e-10)*100 if len(c) >= 2 else 0
    f["ret_3_pct"] = (c[-1] - c[-4]) / max(c[-4], 1e-10)*100 if len(c) >= 4 else 0
    f["ret_6_pct"] = (c[-1] - c[-7]) / max(c[-7], 1e-10)*100 if len(c) >= 7 else 0
    
    if len(c) > 10:
        rets = np.diff(c[-50:]) / np.maximum(c[-50:-1], 1e-10) if len(c) > 50 else np.diff(c) / np.maximum(c[:-1], 1e-10)
        f["momentum_z"] = (f["ret_1_pct"]/100) / max(np.std(rets), 1e-10) if len(rets) > 5 else 0
    else:
        f["momentum_z"] = 0
    
    consec = 0
    for j in range(1, min(10, len(c)-1)):
        if (c[-j] - c[-j-1]) * np.sign(f["ret_1_pct"] + 1e-20) > 0: consec += 1
        else: break
    f["consecutive_bars"] = consec * np.sign(f["ret_1_pct"])
    
    # ── LEVEL TEST ──
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
        r1 = (c[-4] - c[-7]) / max(c[-7], 1e-10)
        r2 = (c[-1] - c[-4]) / max(c[-4], 1e-10)
        f["v_reversal"] = float(np.sign(r1) != np.sign(r2) and abs(r2) > abs(r1)*0.5)
    else:
        f["v_reversal"] = 0.0
    
    # ── META ──
    f["atr_pct"] = atr / max(c[-1], 1e-10) * 100
    
    return f

def _ema(data, period):
    if len(data) < period: return np.full(len(data), data[-1] if len(data) > 0 else 0)
    alpha = 2 / (period + 1)
    result = np.zeros(len(data)); result[0] = data[0]
    for i in range(1, len(data)): result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
    return result

def _atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0.0
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
    """Classify entry into a visual pattern type."""
    if (feat.get("is_breakout_up") or feat.get("is_breakout_down")) and feat.get("vol_ratio", 1) > 1.3:
        return "BREAKOUT_UP" if feat.get("is_breakout_up") else "BREAKOUT_DOWN"
    if feat.get("breakout_strength", 0) > 0.1:
        return "BREAKOUT_UP" if feat.get("close_position_20", 0.5) > 0.9 else "BREAKOUT_DOWN"
    if feat.get("ema21_bounce") or feat.get("ema50_bounce"):
        return "EMA_BOUNCE_LONG" if feat.get("ema_alignment", 0) > 0 else "EMA_BOUNCE_SHORT"
    if feat.get("is_pullback_long"): return "PULLBACK_LONG"
    if feat.get("is_pullback_short"): return "PULLBACK_SHORT"
    if feat.get("squeeze_score", 0) > 0.7 and feat.get("range_expansion", 1) > 1.5:
        return "SQUEEZE_BREAK"
    if feat.get("v_reversal"):
        return "V_REVERSAL_UP" if feat.get("ret_1_pct", 0) > 0 else "V_REVERSAL_DOWN"
    if feat.get("is_bullish_engulf"): return "ENGULFING_BULL"
    if feat.get("is_bearish_engulf"): return "ENGULFING_BEAR"
    if feat.get("is_hammer"): return "HAMMER"
    if feat.get("is_shooting_star"): return "SHOOTING_STAR"
    if feat.get("is_bull_pin"): return "PIN_BAR_BULL"
    if feat.get("is_bear_pin"): return "PIN_BAR_BEAR"
    if abs(feat.get("momentum_z", 0)) > 2.0:
        return "IMPULSE_UP" if feat.get("momentum_z") > 0 else "IMPULSE_DOWN"
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

def resolve_xlsx_path() -> Path:
    """Resolve XLSX path: CLI arg > same-dir default > error."""
    if len(sys.argv) > 1:
        p = Path(sys.argv[1]).resolve()
        if not p.exists():
            LOG.error("XLSX not found: %s", p); sys.exit(1)
        return p
    # Same directory as script
    p = SCRIPT_DIR / XLSX_NAME
    if p.exists():
        return p
    # Current working directory
    p = Path.cwd() / XLSX_NAME
    if p.exists():
        return p
    LOG.error("XLSX not found. Place it next to this script or pass path as argument.")
    LOG.error("  Expected: %s", SCRIPT_DIR / XLSX_NAME)
    sys.exit(1)


def main():
    LOG.info("=" * 70)
    LOG.info("TRADER VISUAL PATTERN RECOGNITION ANALYSIS v2")
    LOG.info("=" * 70)
    
    xlsx_path = resolve_xlsx_path()
    LOG.info("XLSX: %s", xlsx_path)
    
    # 1) Parse orders
    orders = parse_mexc_orders(xlsx_path)
    
    # 2) Match trades
    trades = match_trades(orders)
    closed = trades[trades["pnl"] != 0].copy()
    LOG.info("Closed trades: %d", len(closed))
    
    # 3) Top symbols
    sym_counts = closed["symbol"].value_counts()
    top_syms = sym_counts.head(TOP_N_SYMBOLS).index.tolist()
    coverage = sym_counts.head(TOP_N_SYMBOLS).sum() / len(closed) * 100
    LOG.info("Top %d symbols: %d trades (%.0f%% coverage)", TOP_N_SYMBOLS, sym_counts.head(TOP_N_SYMBOLS).sum(), coverage)
    print(f"\nTop symbols: {top_syms}")
    
    # 4) Download OHLCV
    time_range = closed["entry_time"].agg(["min", "max"])
    start_ts = int((time_range["min"] - pd.Timedelta(days=3)).timestamp() * 1000)
    end_ts = int((time_range["max"] + pd.Timedelta(days=1)).timestamp() * 1000)
    LOG.info("OHLCV range: %s → %s", 
             pd.Timestamp(start_ts, unit="ms"), pd.Timestamp(end_ts, unit="ms"))
    
    ohlcv_data = {}
    for i, sym in enumerate(top_syms):
        LOG.info("[%d/%d] Downloading %s...", i+1, len(top_syms), sym)
        try:
            df = download_ohlcv(sym, TIMEFRAME, start_ts, end_ts)
            if len(df) > 0:
                ohlcv_data[sym] = df
        except Exception as e:
            LOG.warning("  %s failed: %s", sym, e)
    
    LOG.info("OHLCV downloaded for %d/%d symbols", len(ohlcv_data), len(top_syms))
    
    # 5) Compute pattern features
    LOG.info("Computing pattern features for each trade entry...")
    trades_with_data = closed[closed["symbol"].isin(ohlcv_data.keys())].copy()
    LOG.info("Trades with OHLCV: %d/%d", len(trades_with_data), len(closed))
    
    records = []
    skipped = {"no_data": 0, "short_bar": 0, "too_far": 0, "no_feat": 0}
    
    for _, trade in trades_with_data.iterrows():
        sym = trade["symbol"]
        ohlcv = ohlcv_data[sym]
        if len(ohlcv) < 60:
            skipped["short_bar"] += 1; continue
        
        entry_ts_ms = int(trade["entry_time"].timestamp() * 1000)
        time_diffs = np.abs(ohlcv["timestamp"].values - entry_ts_ms)
        bar_idx = np.argmin(time_diffs)
        
        if time_diffs[bar_idx] > 15 * 60 * 1000:
            skipped["too_far"] += 1; continue
        if bar_idx < 50:
            skipped["short_bar"] += 1; continue
        
        feat = compute_pattern_features(
            ohlcv["close"].values, ohlcv["high"].values,
            ohlcv["low"].values, ohlcv["open"].values,
            ohlcv["volume"].values, bar_idx, lookback=50,
        )
        if feat is None:
            skipped["no_feat"] += 1; continue
        
        pattern = classify_pattern(feat)
        feat["pattern"] = pattern
        feat["pattern_group"] = group_pattern(pattern)
        feat["symbol"] = sym
        feat["pnl"] = trade["pnl"]
        feat["direction"] = trade["direction"]
        feat["n_entries"] = trade["n_entries"]
        feat["leverage"] = trade["leverage"]
        feat["is_win"] = float(trade["pnl"] > 0)
        feat["duration_min"] = (trade["exit_time"] - trade["entry_time"]).total_seconds() / 60 if pd.notna(trade.get("exit_time")) else 0
        
        records.append(feat)
    
    LOG.info("Pattern records: %d (skipped: %s)", len(records), skipped)
    if not records:
        LOG.error("No records! Aborting."); return
    
    results = pd.DataFrame(records)
    
    # 6) Analysis
    n_total = len(results)
    wr_overall = results["is_win"].mean() * 100
    pnl_overall = results["pnl"].sum()
    
    print("\n" + "=" * 100)
    print("VISUAL PATTERN ANALYSIS RESULTS")
    print("=" * 100)
    print(f"\n  Analyzed: {n_total} entries | WR: {wr_overall:.1f}% | PnL: {pnl_overall:+.2f} USDT")
    
    # Per-pattern
    pstats = []
    for pattern in sorted(results["pattern"].unique()):
        sub = results[results["pattern"] == pattern]
        n = len(sub)
        if n < 2: continue
        wr = sub["is_win"].mean() * 100
        pnl = sub["pnl"].sum(); avg_pnl = sub["pnl"].mean()
        gains = sub.loc[sub["pnl"] > 0, "pnl"].sum()
        losses = abs(sub.loc[sub["pnl"] < 0, "pnl"].sum())
        pf = gains / max(losses, 1e-10)
        med_dur = sub["duration_min"].median()
        pstats.append({"pattern": pattern, "n": n, "pct": n/n_total*100,
                       "WR": wr, "PnL": pnl, "avg_pnl": avg_pnl, "PF": pf, "med_dur": med_dur})
    
    pstats_df = pd.DataFrame(pstats).sort_values("PnL", ascending=False)
    print(f"\n{'Pattern':<22} {'N':>5} {'%':>5} {'WR%':>6} {'PnL':>10} {'AvgPnL':>9} {'PF':>6} {'MedDur':>7}")
    print("-" * 80)
    for _, row in pstats_df.iterrows():
        print(f"{row['pattern']:<22} {row['n']:>5} {row['pct']:>4.1f}% "
              f"{row['WR']:>5.1f}% {row['PnL']:>+10.1f} {row['avg_pnl']:>+8.3f} "
              f"{row['PF']:>5.2f} {row['med_dur']:>6.1f}m")
    
    # Per-group
    print("\n" + "=" * 80)
    print("PATTERN GROUPS (aggregated)")
    print("=" * 80)
    
    key_features = [
        "close_position_20", "breakout_strength", "dist_ema9_atr", "dist_ema21_atr",
        "ema_alignment", "ema21_bounce", "ema50_bounce", "squeeze_score",
        "vol_ratio", "body_ratio", "lower_wick_ratio", "upper_wick_ratio",
        "momentum_z", "consecutive_bars", "range_expansion", "pullback_depth",
        "ret_1_pct", "ret_3_pct", "atr_pct", "v_reversal",
    ]
    
    gstats = []
    for group in sorted(results["pattern_group"].unique()):
        sub = results[results["pattern_group"] == group]
        n = len(sub)
        if n < 2: continue
        wr = sub["is_win"].mean() * 100
        pnl = sub["pnl"].sum(); avg_pnl = sub["pnl"].mean()
        gains = sub.loc[sub["pnl"] > 0, "pnl"].sum()
        losses = abs(sub.loc[sub["pnl"] < 0, "pnl"].sum())
        pf = gains / max(losses, 1e-10)
        med_dur = sub["duration_min"].median()
        
        feat_cols = [c for c in results.columns if c not in 
                     ["pattern", "pattern_group", "symbol", "pnl", "is_win",
                      "direction", "n_entries", "leverage", "duration_min"]]
        profile = {fc: float(sub[fc].mean()) for fc in feat_cols if fc in sub.columns}
        
        gstats.append({"group": group, "n": n, "pct": n/n_total*100,
                       "WR": wr, "PnL": pnl, "avg_pnl": avg_pnl, "PF": pf,
                       "med_dur": med_dur, "profile": profile})
    
    gstats_df = pd.DataFrame(gstats).sort_values("PnL", ascending=False)
    
    print(f"{'Group':<22} {'N':>5} {'%':>5} {'WR%':>6} {'PnL':>10} {'PF':>6} {'MedDur':>7}")
    print("-" * 65)
    for _, row in gstats_df.iterrows():
        print(f"{row['group']:<22} {row['n']:>5} {row['pct']:>4.1f}% "
              f"{row['WR']:>5.1f}% {row['PnL']:>+10.1f} {row['PF']:>5.2f} {row['med_dur']:>6.1f}m")
    
    # Feature profiles
    print("\n" + "=" * 80)
    print("KEY FEATURE PROFILES PER PATTERN GROUP")
    print("=" * 80)
    for _, row in gstats_df.iterrows():
        prof = row["profile"]
        print(f"\n  {row['group']} (n={row['n']}, WR={row['WR']:.1f}%, PnL={row['PnL']:+.1f}, PF={row['PF']:.2f}):")
        for kf in key_features:
            if kf in prof and abs(prof[kf]) > 0.001:
                print(f"    {kf:<25} = {prof[kf]:+.4f}")
    
    # Top patterns
    print("\n" + "=" * 80)
    print("TOP VISUAL PATTERNS WITH EDGE (for v8 feature encoding)")
    print("=" * 80)
    
    profitable = gstats_df[(gstats_df["n"] >= 5) & (gstats_df["PF"] > 0.5)].sort_values("PF", ascending=False)
    for rank, (_, row) in enumerate(profitable.iterrows(), 1):
        prof = row["profile"]
        print(f"\n  #{rank}: {row['group']}")
        print(f"      N={int(row['n'])} WR={row['WR']:.1f}% PnL={row['PnL']:+.1f} PF={row['PF']:.2f} MedDur={row['med_dur']:.1f}m")
        print(f"      Distinguishing features vs overall average:")
        for kf in key_features:
            if kf in prof:
                diff = prof[kf] - results[kf].mean()
                if abs(diff) > 0.01:
                    print(f"        {kf:<25} = {prof[kf]:+.4f} (diff: {diff:+.4f})")
    
    # Pattern × Direction
    print("\n" + "=" * 80)
    print("PATTERN × DIRECTION MATRIX")
    print("=" * 80)
    for group in sorted(results["pattern_group"].unique()):
        sub = results[results["pattern_group"] == group]
        if len(sub) < 5: continue
        for direction in ["long", "short"]:
            dsub = sub[sub["direction"] == direction]
            if len(dsub) < 3: continue
            wr = dsub["is_win"].mean() * 100; pnl = dsub["pnl"].sum()
            print(f"  {group:<22} {direction:<6}: N={len(dsub):>4} WR={wr:>5.1f}% PnL={pnl:>+8.1f}")
    
    # Save
    output_path = OUTPUT_DIR / "trader_pattern_analysis.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        results.to_excel(writer, sheet_name="all_entries", index=False)
        pstats_df.to_excel(writer, sheet_name="pattern_stats", index=False)
        gstats_df.drop(columns=["profile"]).to_excel(writer, sheet_name="group_stats", index=False)
    LOG.info("Excel saved to %s", output_path)
    
    # JSON
    summary = {
        "total_entries": n_total, "overall_wr": float(wr_overall), "overall_pnl": float(pnl_overall),
        "pattern_groups": [{
            "group": row["group"], "n": int(row["n"]),
            "WR": float(row["WR"]), "PnL": float(row["PnL"]), "PF": float(row["PF"]),
            "med_dur_min": float(row["med_dur"]),
            "key_features": {kf: float(prof[kf]) for kf in key_features if kf in prof and abs(prof[kf]) > 0.001},
        } for _, row in gstats_df.iterrows() for prof in [row["profile"]]],
    }
    json_path = OUTPUT_DIR / "trader_pattern_analysis.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    LOG.info("JSON saved to %s", json_path)
    
    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE — Share the output/ folder contents with me!")
    print("=" * 80)


if __name__ == "__main__":
    main()
