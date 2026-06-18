"""
Download v4 dataset: 5 majors + 4 memes + 7 alts = 16 tokens × 200k candles 1m.

Token selection (per user request 2026-06-19 "reducir majors a 5 y subir alts a 6-7"):
  Majors (5): BTC, ETH, SOL, BNB, XRP   (dropped ADA, AVAX, DOGE-as-major)
  Memes  (4): PEPE, WIF, BONK, FLOKI
  Alts   (7): LINK, ARB, OP, SUI, APT, INJ, TIA

Total: 16 tokens × 200k candles = 3,200,000 candles (vs v3 1.4M, +128%)

Resume-capable: if a CSV already exists with >= TARGET_CANDLES rows, it is skipped.
Binance-safe: 0.15s sleep between requests = 400 req/min (limit 1200/min without API key).
Estimated time: ~12 min for 16 tokens × 200k candles.

Output: /home/z/my-project/download/real_data_1m_v4/{SYMBOL}_1m.csv
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

OUT_DIR = Path("/home/z/my-project/download/real_data_1m_v4")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Token taxonomy (used by N2 asset_class trie layer)
MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MEMES  = ["PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT"]
ALTS   = ["LINKUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "APTUSDT", "INJUSDT", "TIAUSDT"]

SYMBOLS = MAJORS + MEMES + ALTS
TOKEN_CLASSES = {s: "major" for s in MAJORS}
TOKEN_CLASSES.update({s: "meme" for s in MEMES})
TOKEN_CLASSES.update({s: "alt" for s in ALTS})

TARGET_CANDLES = 200_000        # ~139 days of 1m
PER_REQUEST    = 1000
SLEEP_SEC      = 0.15
MAX_RETRIES    = 3

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
BASE = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol: str, end_time: int | None, limit: int = PER_REQUEST) -> list[list]:
    """Fetch up to `limit` klines with open_time <= end_time (inclusive of end_time-1 to avoid dupes).

    Binance returns klines in ASCENDING order (oldest first). This function returns them as-is
    (oldest first) so the caller can prepend to the existing CSV cleanly.
    """
    params = {"symbol": symbol, "interval": "1m", "limit": str(limit)}
    if end_time is not None:
        params["endTime"] = str(end_time - 1)
    qs = urllib.parse.urlencode(params)
    url = f"{BASE}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url} — {last_err}")


def row_to_csv(k: list) -> list:
    """Binance kline -> CSV row. Schema matches v3 dataset."""
    return [
        int(k[0]),        # open_time (ms)
        float(k[1]),      # open
        float(k[2]),      # high
        float(k[3]),      # low
        float(k[4]),      # close
        float(k[5]),      # volume
        int(k[6]),        # close_time (ms)
        float(k[7]),      # quote_volume
        int(k[8]),        # n_trades
        float(k[9]),      # taker_buy_base
        float(k[10]),     # taker_buy_quote
        k[11] or "",      # ignore field
    ]


def load_existing_count(path: Path) -> int:
    """Count rows in an existing CSV (without loading fully)."""
    if not path.exists():
        return 0
    n = 0
    with path.open("r", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for _ in reader:
            n += 1
    return n


def download_symbol(symbol: str) -> dict:
    """Download TARGET_CANDLES rows for symbol, prepending to existing CSV if present."""
    out_path = OUT_DIR / f"{symbol}_1m.csv"
    existing_count = load_existing_count(out_path)

    info = {
        "symbol": symbol,
        "asset_class": TOKEN_CLASSES[symbol],
        "target_candles": TARGET_CANDLES,
        "existing_candles": existing_count,
        "new_candles_fetched": 0,
        "total_candles_after": existing_count,
        "first_open_time_ms": None,
        "last_open_time_ms": None,
        "skipped": False,
    }

    if existing_count >= TARGET_CANDLES:
        info["skipped"] = True
        print(f"  [SKIP] {symbol}: already has {existing_count:,} rows")
        return info

    # Determine the starting point: if existing CSV, take its earliest open_time - 1.
    earliest_open_time: int | None = None
    existing_rows: list[list] = []
    if existing_count > 0:
        with out_path.open("r", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                existing_rows.append(row)
        # existing_rows is in chronological order (oldest first). earliest = first row's open_time
        earliest_open_time = int(existing_rows[0][0])
        info["first_open_time_ms"] = int(existing_rows[0][0])
        info["last_open_time_ms"] = int(existing_rows[-1][0])

    # Fetch backwards from earliest_open_time (or "now" if no existing data)
    end_time = earliest_open_time  # None means "now"
    fetched: list[list] = []  # Will hold all new klines in ASCENDING order (oldest first)
    target_new = TARGET_CANDLES - existing_count

    print(f"  [START] {symbol}: existing={existing_count:,}, need={target_new:,} more")
    while len(fetched) < target_new:
        batch = fetch_klines(symbol, end_time, PER_REQUEST)
        if not batch:
            print(f"    [WARN] {symbol}: empty batch (Binance returned no more data)")
            break
        # batch is in ASCENDING order (oldest first). batch[0] = oldest, batch[-1] = newest.
        # fetched should also be ASCENDING; new batch is OLDER than existing fetched, so PREPEND.
        fetched = batch + fetched
        oldest_in_batch = int(batch[0][0])
        newest_in_batch = int(batch[-1][0])
        print(f"    [PROG] {symbol}: fetched={len(fetched):,}/{target_new:,} "
              f"(batch range: {datetime.fromtimestamp(oldest_in_batch/1000, tz=timezone.utc).date()} → "
              f"{datetime.fromtimestamp(newest_in_batch/1000, tz=timezone.utc).date()})")
        if len(batch) < PER_REQUEST:
            print(f"    [DONE] {symbol}: Binance returned short batch ({len(batch)}), no more history available")
            break
        # Next end_time: must be strictly less than the OLDEST in this batch to avoid duplicates.
        end_time = oldest_in_batch
        time.sleep(SLEEP_SEC)

    # Trim: keep only the NEWEST target_new of the fetched (those closest to existing data).
    # We're extending BACKWARDS, so the newest part of fetched is contiguous with existing.
    if len(fetched) > target_new:
        fetched = fetched[-target_new:]
    info["new_candles_fetched"] = len(fetched)

    # Convert fetched klines to CSV rows
    fetched_rows = [row_to_csv(k) for k in fetched]

    # Final dataset = fetched_rows (oldest of new) ++ existing_rows
    all_rows = fetched_rows + existing_rows
    info["total_candles_after"] = len(all_rows)
    if all_rows:
        info["first_open_time_ms"] = all_rows[0][0]
        info["last_open_time_ms"] = all_rows[-1][0]

    # Write CSV (overwrite with merged dataset)
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "n_trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        w.writerows(all_rows)

    print(f"  [OK]   {symbol}: total={len(all_rows):,} rows, "
          f"range={datetime.fromtimestamp(all_rows[0][0]/1000, tz=timezone.utc).date()} → "
          f"{datetime.fromtimestamp(all_rows[-1][0]/1000, tz=timezone.utc).date()}")
    return info


def main():
    print(f"PPMT data download v4 — {len(SYMBOLS)} tokens × {TARGET_CANDLES:,} candles 1m")
    print(f"Output dir: {OUT_DIR}")
    print(f"Tokens: {len(MAJORS)} majors + {len(MEMES)} memes + {len(ALTS)} alts")
    print()

    summary = {
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_candles_per_token": TARGET_CANDLES,
        "n_symbols": len(SYMBOLS),
        "majors": MAJORS,
        "memes": MEMES,
        "alts": ALTS,
        "tokens": [],
    }

    for sym in SYMBOLS:
        try:
            info = download_symbol(sym)
        except Exception as e:
            info = {"symbol": sym, "error": str(e)}
            print(f"  [ERR] {sym}: {e}")
        summary["tokens"].append(info)
        time.sleep(0.5)

    # Write summary
    summary_path = OUT_DIR / "_summary.json"
    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Print grand total
    total_candles = sum(t.get("total_candles_after", 0) for t in summary["tokens"])
    print()
    print(f"=== SUMMARY ===")
    print(f"Tokens: {len(SYMBOLS)}")
    print(f"Total candles: {total_candles:,}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
