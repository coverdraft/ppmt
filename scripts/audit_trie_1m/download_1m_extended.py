"""
Download extended 1m data: 100k candles per token, 14 tokens.

Existing 8 tokens (extend 50k -> 100k):
  BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT

New 6 tokens (memes + alts):
  PEPEUSDT, WIFUSDT, BONKUSDT, FLOKIUSDT, LINKUSDT, ARBUSDT

Output: /home/z/my-project/download/real_data_1m_extended/{SYMBOL}_1m.csv
"""
from __future__ import annotations
import csv
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import urllib.request
import urllib.parse

OUT_DIR = Path("/home/z/my-project/download/real_data_1m_extended")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    # Existing majors (extend back further)
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    # New memes
    "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT",
    # New alts
    "LINKUSDT", "ARBUSDT",
]

TARGET_CANDLES = 100_000        # ~70 days of 1m
PER_REQUEST    = 1000
SLEEP_SEC      = 0.15
MAX_RETRIES    = 3

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
BASE = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol: str, end_time: int | None, limit: int = PER_REQUEST) -> list[list]:
    """Fetch up to `limit` klines ending at end_time (exclusive). Returns newest-first."""
    params = {"symbol": symbol, "interval": "1m", "limit": str(limit)}
    if end_time is not None:
        params["endTime"] = str(end_time - 1)
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                import json as _j
                return _j.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"fetch_klines({symbol}, end={end_time}) failed after {MAX_RETRIES} retries: {last_err}")


def download_symbol(symbol: str) -> dict:
    out_csv = OUT_DIR / f"{symbol}_1m.csv"
    # If partial file exists, resume from earliest timestamp
    start_end_time = None
    existing_rows: list[dict] = []
    if out_csv.exists():
        with open(out_csv, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_rows.append(row)
        if existing_rows:
            # earliest open_time among existing
            existing_rows.sort(key=lambda r: int(r["open_time"]))
            start_end_time = int(existing_rows[0]["open_time"])
            print(f"  [{symbol}] resuming: {len(existing_rows)} existing, going back from {start_end_time}")

    all_rows: list[list] = []
    # Convert existing_rows back to kline list (only fields we need)
    for r in existing_rows:
        all_rows.append([
            int(r["open_time"]), r["open"], r["high"], r["low"], r["close"], r["volume"],
            int(r["close_time"]), r["quote_volume"], int(r["trades"]),
            r["taker_buy_base"], r["taker_buy_quote"], "0",
        ])

    end_time = start_end_time
    fetched_this_session = 0
    while fetched_this_session + len(existing_rows) < TARGET_CANDLES:
        try:
            batch = fetch_klines(symbol, end_time)
        except Exception as e:
            print(f"  [{symbol}] FETCH ERROR: {e}")
            break
        if not batch:
            print(f"  [{symbol}] no more data")
            break
        all_rows.extend(batch)
        fetched_this_session += len(batch)
        # next batch ends before the earliest open_time of this batch
        end_time = int(batch[0][0])
        # progress
        if fetched_this_session % 5000 == 0 or fetched_this_session == len(batch):
            dt = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"  [{symbol}] fetched {fetched_this_session} (total {len(all_rows)}) — earliest so far: {dt}")
        if len(batch) < PER_REQUEST:
            print(f"  [{symbol}] short batch, stopping")
            break
        time.sleep(SLEEP_SEC)

    # Dedup by open_time + sort
    seen = {}
    for k in all_rows:
        ot = int(k[0])
        if ot not in seen:
            seen[ot] = k
    dedup = sorted(seen.values(), key=lambda k: int(k[0]))

    # Write CSV
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "open_time","open","high","low","close","volume",
            "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote",
        ])
        for k in dedup:
            w.writerow([
                k[0], k[1], k[2], k[3], k[4], k[5],
                k[6], k[7], k[8], k[9], k[10],
            ])

    n = len(dedup)
    if n > 0:
        t_first = datetime.fromtimestamp(int(dedup[0][0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        t_last  = datetime.fromtimestamp(int(dedup[-1][0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    else:
        t_first = t_last = "n/a"
    print(f"  [{symbol}] DONE: {n} candles, range {t_first} -> {t_last}")
    return {
        "symbol": symbol,
        "n_candles": n,
        "first": t_first,
        "last": t_last,
        "csv_path": str(out_csv),
    }


def main():
    print(f"Target: {TARGET_CANDLES} candles x {len(SYMBOLS)} symbols")
    print(f"Output dir: {OUT_DIR}\n")

    # Filter out symbols that fail entirely (e.g. delisted)
    results = []
    for sym in SYMBOLS:
        print(f"\n=== {sym} ===")
        try:
            r = download_symbol(sym)
            results.append(r)
        except Exception as e:
            print(f"  [{sym}] FATAL: {e}")
            results.append({"symbol": sym, "error": str(e)})

    summary = {
        "target_candles": TARGET_CANDLES,
        "n_symbols_planned": len(SYMBOLS),
        "n_symbols_ok": sum(1 for r in results if "n_candles" in r),
        "results": results,
    }
    with open(OUT_DIR / "_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print("\n=== SUMMARY ===")
    for r in results:
        if "n_candles" in r:
            print(f"  {r['symbol']}: {r['n_candles']:>7} candles  {r['first']} -> {r['last']}")
        else:
            print(f"  {r['symbol']}: ERROR — {r.get('error')}")
    print(f"\nTotal candles: {sum(r.get('n_candles',0) for r in results):,}")


if __name__ == "__main__":
    main()
