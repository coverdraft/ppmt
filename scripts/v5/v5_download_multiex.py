"""
Track A v2: Multi-exchange OHLCV download (Binance → Bybit → OKX fallback).

Same schema as v5_download_massive.py (ohlcv_ext table) but uses multiple
exchanges to avoid Binance IP bans and survive outages.

Order of preference:
  1. Bybit   — public, no API key, long spot history, stable
  2. OKX     — public, no API key, long spot history
  3. Binance — original source (only if both above fail)

Symbol/timeframe mapping:
  Bybit:  symbol as-is (DOGEUSDT), interval in minutes ('5','15','60') or 'D'
  OKX:    instId with dash (DOGE-USDT), bar like '5m','15m','1H','1D'

Usage:
    python v5_download_multiex.py --tokens DOGEUSDT --windows BULL_2024 RECENT_2026 --timeframes 5m 1m
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
from ppmt.data.storage import PPMTStorage as Storage  # noqa: E402

LOG = logging.getLogger("v5_dl_multiex")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

# ---- Exchange endpoints ----
BYBIT = "https://api.bybit.com/v5/market/kline"
OKX   = "https://www.okx.com/api/v5/market/candles"
OKX_HIST = "https://www.okx.com/api/v5/market/history-candles"  # older data
BN    = "https://api.binance.com/api/v3/klines"

_DB_WRITE_LOCK = threading.Lock()
_DB_CONN: Optional[sqlite3.Connection] = None

# ---- Windows (ms) ----
WINDOWS = [
    ("BULL_2024",   1727740800000, 1735516800000, "Oct-Dec 2024 BTC pump to 100k"),
    ("RANGE_2025",  1753990400000, 1761817200000, "Aug-Oct 2025 consolidation"),
    ("RECENT_2026", 1742774400000, 1750636800000, "Mar-Jun 2026 recent"),
    ("BEAR_2022",   1651363200000, 1659139200000, "May-Jul 2022 LUNA/3AC crash"),
    ("RANGE_2023",  1677628800000, 1698710400000, "Mar-Oct 2023 accumulation"),
]

# ---- Tokens (Binance symbol, asset_class, okx_instId) ----
TOKENS = [
    ("BTCUSDT",  "blue_chip",  "BTC-USDT"),
    ("ETHUSDT",  "blue_chip",  "ETH-USDT"),
    ("BNBUSDT",  "blue_chip",  "BNB-USDT"),
    ("SOLUSDT",  "large_cap",  "SOL-USDT"),
    ("XRPUSDT",  "large_cap",  "XRP-USDT"),
    ("ADAUSDT",  "mid_cap",    "ADA-USDT"),
    ("AVAXUSDT", "mid_cap",    "AVAX-USDT"),
    ("LINKUSDT", "mid_cap",    "LINK-USDT"),
    ("DOGEUSDT", "meme",       "DOGE-USDT"),
    ("SHIBUSDT", "meme",       "SHIB-USDT"),
    ("PEPEUSDT", "meme",       "PEPE-USDT"),
    ("WIFUSDT",  "meme",       "WIF-USDT"),
    ("BONKUSDT", "meme",       "BONK-USDT"),
]

SKIP = {
    "BEAR_2022":  {"PEPEUSDT", "WIFUSDT", "BONKUSDT"},
    "RANGE_2023": {"PEPEUSDT", "WIFUSDT", "BONKUSDT"},
}

# Bybit interval in minutes per timeframe
BYBIT_TF = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60"}
OKX_TF   = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H"}
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000}


# ---------- BYBIT ----------
def fetch_bybit(symbol: str, tf: str, start_ms: int, end_ms: int) -> list[list]:
    """Bybit returns [t, o, h, l, c, vol, turnover] DESCENDING.
    Pagination: keep start=fixed, walk end backwards using oldest_ts-1."""
    interval = BYBIT_TF[tf]
    out: list[list] = []
    cur_end = end_ms
    page_size = 1000
    max_pages = 2000
    for _ in range(max_pages):
        if cur_end <= start_ms:
            break
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": interval,
            "start": start_ms,
            "end": cur_end,
            "limit": page_size,
        }
        try:
            r = requests.get(BYBIT, params=params, timeout=20)
            if r.status_code in (429, 418):
                LOG.warning("Bybit rate limited %s %s, wait 60s", symbol, tf)
                time.sleep(60)
                continue
            j = r.json()
            if j.get("retCode") != 0:
                LOG.warning("Bybit error %s: %s", symbol, j.get("retMsg"))
                return out
            rows = j.get("result", {}).get("list", [])
            if not rows:
                break
            for row in rows:
                ts = int(row[0])
                if ts < start_ms or ts >= end_ms:
                    continue
                out.append([ts, float(row[1]), float(row[2]),
                            float(row[3]), float(row[4]), float(row[5])])
            oldest_ts = min(int(r[0]) for r in rows)
            if oldest_ts >= cur_end:
                break
            cur_end = oldest_ts  # walk end backward
            if len(rows) < page_size:
                break
            time.sleep(0.15)
        except (requests.RequestException, ValueError, KeyError) as e:
            LOG.warning("Bybit fetch err %s %s: %s — wait 5s", symbol, tf, e)
            time.sleep(5)
    return out


# ---------- OKX ----------
def fetch_okx(inst_id: str, tf: str, start_ms: int, end_ms: int) -> list[list]:
    """OKX returns [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm] desc."""
    bar = OKX_TF[tf]
    out: list[list] = []
    cursor = end_ms
    page_size = 100  # OKX max = 300 for history, 100 for live; use 100 to be safe
    max_pages = 5000
    for _ in range(max_pages):
        if cursor <= start_ms:
            break
        # history endpoint allows older data
        url = OKX_HIST if cursor < (int(time.time() * 1000) - 7 * 24 * 3600 * 1000) else OKX
        params = {"instId": inst_id, "bar": bar, "limit": page_size,
                  "before": cursor, "after": start_ms}
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code in (429, 418):
                LOG.warning("OKX rate limited %s %s, wait 30s", inst_id, tf)
                time.sleep(30)
                continue
            j = r.json()
            if j.get("code") != "0":
                LOG.warning("OKX error %s: %s", inst_id, j.get("msg"))
                return out
            rows = j.get("data", [])
            if not rows:
                break
            for row in rows:
                ts = int(row[0])
                if ts < start_ms or ts >= end_ms:
                    continue
                out.append([ts, float(row[1]), float(row[2]),
                            float(row[3]), float(row[4]), float(row[5])])
            # OKX desc: "before" = older timestamps; move cursor backwards
            oldest_ts = min(int(r[0]) for r in rows)
            if oldest_ts >= cursor:
                break
            cursor = oldest_ts
            if len(rows) < page_size:
                break
            time.sleep(0.2)
        except (requests.RequestException, ValueError, KeyError) as e:
            LOG.warning("OKX fetch err %s %s: %s — wait 5s", inst_id, tf, e)
            time.sleep(5)
    return out


# ---------- Binance (fallback only) ----------
def fetch_binance(symbol: str, tf: str, start_ms: int, end_ms: int) -> list[list]:
    out: list[list] = []
    cursor = start_ms
    page_size = 1000
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": tf,
                  "startTime": cursor, "endTime": end_ms, "limit": page_size}
        try:
            r = requests.get(BN, params=params, timeout=20)
            if r.status_code in (429, 418):
                LOG.warning("Binance rate limited %s %s, wait 60s", symbol, tf)
                time.sleep(60)
                continue
            if r.status_code == 400:
                return out
            r.raise_for_status()
            rows = r.json()
        except (requests.RequestException, ValueError) as e:
            LOG.warning("Binance err %s %s: %s", symbol, tf, e)
            return out
        if not rows:
            break
        for row in rows:
            ts = int(row[0])
            out.append([ts, float(row[1]), float(row[2]),
                        float(row[3]), float(row[4]), float(row[5])])
        cursor = int(rows[-1][6]) + 1
        if len(rows) < page_size:
            break
        time.sleep(0.3)
    return out


# ---------- Storage ----------
def store_rows(symbol: str, tf: str, window: str, rows: list[list]) -> int:
    if not rows:
        return 0
    conn = _DB_CONN
    inserted = 0
    batch = []
    for r in rows:
        ts_sec = r[0] // 1000
        batch.append((symbol, tf, ts_sec, window, r[1], r[2], r[3], r[4], r[5]))
    CHUNK = 500
    for i in range(0, len(batch), CHUNK):
        chunk = batch[i:i + CHUNK]
        placeholders = ",".join(["(?,?,?,?,?,?,?,?,?)"] * len(chunk))
        flat = [v for row in chunk for v in row]
        try:
            with _DB_WRITE_LOCK:
                conn.execute(
                    f"INSERT OR IGNORE INTO ohlcv_ext "
                    f"(symbol, timeframe, timestamp, window, open, high, low, close, volume) "
                    f"VALUES {placeholders}",
                    flat,
                )
                conn.commit()
            inserted += len(chunk)
        except Exception as e:
            LOG.error("DB insert failed %s %s %s: %s", symbol, tf, window, e)
    return inserted


def download_one(sym: str, okx_id: str, tf: str, window: str,
                 start_ms: int, end_ms: int) -> tuple:
    if sym in SKIP.get(window, set()):
        return (sym, tf, window, 0, "skipped_not_listed", "")

    # Try Bybit first
    rows = fetch_bybit(sym, tf, start_ms, end_ms)
    src = "bybit"
    if len(rows) < 100:
        # Fallback OKX
        rows2 = fetch_okx(okx_id, tf, start_ms, end_ms)
        if len(rows2) > len(rows):
            rows = rows2
            src = "okx"
    if len(rows) < 100:
        # Fallback Binance
        rows3 = fetch_binance(sym, tf, start_ms, end_ms)
        if len(rows3) > len(rows):
            rows = rows3
            src = "binance"

    # De-dup by ts
    seen = {}
    for r in rows:
        seen[r[0]] = r
    rows = sorted(seen.values(), key=lambda x: x[0])

    n = store_rows(sym, tf, window, rows)
    status = "ok" if n > 0 else "empty"
    return (sym, tf, window, n, status, src)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframes", nargs="+", default=["1m", "5m", "15m"])
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--tokens", nargs="+", default=None)
    parser.add_argument("--windows", nargs="+", default=None)
    args = parser.parse_args()

    storage = Storage()
    global _DB_CONN
    _DB_CONN = sqlite3.connect(storage.db_path, check_same_thread=False)
    _DB_CONN.execute("PRAGMA journal_mode=WAL")
    _DB_CONN.execute("PRAGMA synchronous=NORMAL")
    conn = _DB_CONN
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_ext (
            symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL, window TEXT NOT NULL DEFAULT '',
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume REAL NOT NULL,
            UNIQUE(symbol, timeframe, timestamp, window)
        )
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ohlcv_ext)").fetchall()]
    if "window" not in cols:
        conn.execute("ALTER TABLE ohlcv_ext ADD COLUMN window TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_ext_sym_tf_win "
                 "ON ohlcv_ext(symbol, timeframe, window, timestamp)")
    conn.commit()

    tokens = [(s, c, o) for (s, c, o) in TOKENS
              if args.tokens is None or s in args.tokens]
    windows = [(n, s, e, d) for (n, s, e, d) in WINDOWS
               if args.windows is None or n in args.windows]

    jobs = []
    for tf in args.timeframes:
        for (sym, _, okx_id) in tokens:
            for (wn, ws, we, _) in windows:
                jobs.append((sym, okx_id, tf, wn, ws, we))

    LOG.info("Starting download: %d jobs (%d tokens × %d TFs × %d windows)",
             len(jobs), len(tokens), len(args.timeframes), len(windows))

    results = []
    ok = skipped = empty = failed = 0
    total_candles = 0

    # Sequential execution (more reliable than ThreadPool when running in
    # detached/background mode — ThreadPoolExecutor shutdown can kill the
    # process in some sandboxed environments).
    for i, (sym, okx_id, tf, wn, ws, we) in enumerate(jobs, 1):
        try:
            res = download_one(sym, okx_id, tf, wn, ws, we)
            results.append(res)
            sym2, tf2, wn2, n, status, src = res
            if status == "ok":
                ok += 1
                total_candles += n
                LOG.info("[%d/%d] %s %s %s: %d candles via %s",
                         i, len(jobs), sym2, tf2, wn2, n, src)
            elif status == "skipped_not_listed":
                skipped += 1
            elif status == "empty":
                empty += 1
                LOG.warning("[%d/%d] %s %s %s: empty", i, len(jobs), sym2, tf2, wn2)
        except Exception as e:
            failed += 1
            LOG.exception("[%d/%d] FAILED %s %s %s: %s", i, len(jobs), sym, tf, wn, e)

    LOG.info("=" * 60)
    LOG.info("OK: %d jobs (%d candles), Skipped: %d, Empty: %d, Failed: %d",
             ok, total_candles, skipped, empty, failed)

    # Summary
    print("\n=== Summary by window ===")
    by_w = {}
    for r in results:
        if r[4] == "ok":
            by_w[r[2]] = by_w.get(r[2], 0) + r[3]
    for w, n in sorted(by_w.items()):
        print(f"  {w:14s}  {n:>10,d}")

    print("\n=== Summary by token ===")
    by_t = {}
    for r in results:
        if r[4] == "ok":
            by_t[r[0]] = by_t.get(r[0], 0) + r[3]
    for t, n in sorted(by_t.items(), key=lambda x: -x[1]):
        print(f"  {t:12s}  {n:>10,d}")

    print("\n=== Summary by source ===")
    by_src = {}
    for r in results:
        if r[4] == "ok" and r[5]:
            by_src[r[5]] = by_src.get(r[5], 0) + r[3]
    for s, n in sorted(by_src.items(), key=lambda x: -x[1]):
        print(f"  {s:8s}  {n:>10,d}")


if __name__ == "__main__":
    main()
