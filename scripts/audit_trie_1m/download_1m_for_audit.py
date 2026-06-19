"""
Descarga dataset 1m para comparación de detectores de régimen.

8 tokens × 100k velas 1m ≈ 70 días cada uno.
Resume-capable: si se interrumpe, retoma desde donde quedó.

Output: /home/z/my-project/download/real_data_1m/
"""

import os
import sys
import time
import json
import csv
from pathlib import Path
from datetime import datetime, timedelta, timezone

import urllib.request
import urllib.error

# ----------------- Config ----------------- #

OUT_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = OUT_DIR / "_state.json"

# 8 tokens mezclando majors / alts / memes para tener diversidad de regímenes
TOKENS = [
    "BTCUSDT",   # major
    "ETHUSDT",   # major
    "SOLUSDT",   # major
    "BNBUSDT",   # major
    "XRPUSDT",   # major
    "ARBUSDT",   # alt L2
    "LINKUSDT",  # alt oracle
    "PEPEUSDT",  # meme
]

CANDLES_PER_TOKEN = 100_000   # 100k velas × 1m = ~70 días
BATCH_SIZE = 1000              # Binance max
SLEEP_BETWEEN = 0.15           # 150ms entre requests

BINANCE_URL = "https://api.binance.com/api/v3/klines"


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_klines(symbol: str, start_time: int, end_time: int) -> list:
    """Fetch 1m klines from Binance between [start_time, end_time] (ms)."""
    url = (
        f"{BINANCE_URL}?symbol={symbol}&interval=1m"
        f"&startTime={start_time}&endTime={end_time}&limit={BATCH_SIZE}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "PPMT-downloader/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def download_token(symbol: str, n_candles: int) -> Path:
    """Download n_candles 1m klines for symbol. Resume-capable."""
    out_csv = OUT_DIR / f"{symbol}_1m.csv"
    state = load_state()
    key = f"{symbol}_last_end"

    # If CSV exists and we have state, resume
    existing_rows = 0
    if out_csv.exists():
        with open(out_csv) as f:
            existing_rows = sum(1 for _ in f) - 1  # minus header
        if existing_rows >= n_candles:
            print(f"  [{symbol}] ya tiene {existing_rows} velas, skip.")
            return out_csv

    # Start time: now - (n_candles * 60s), or resume from state
    if key in state and existing_rows > 0:
        start_ms = state[key]
        print(f"  [{symbol}] resumiendo desde {existing_rows} velas, start_ms={start_ms}")
    else:
        # Empezar desde hace n_candles minutos
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        start_ms = now_ms - (n_candles * 60_000)

    # Open CSV in append mode if exists, else write header
    write_header = not out_csv.exists() or existing_rows == 0
    f_out = open(out_csv, "a", newline="")
    writer = csv.writer(f_out)
    if write_header:
        writer.writerow([
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_vol",
            "taker_buy_quote", "ignore"
        ])

    try:
        total = existing_rows
        while total < n_candles:
            end_ms = start_ms + (BATCH_SIZE * 60_000)
            try:
                klines = fetch_klines(symbol, start_ms, end_ms - 1)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print(f"  [{symbol}] rate limit, durmiendo 10s...")
                    time.sleep(10)
                    continue
                raise
            if not klines:
                print(f"  [{symbol}] sin más datos, parando.")
                break

            for k in klines:
                writer.writerow(k)
            total += len(klines)
            start_ms = klines[-1][6] + 1  # next ms after last close_time
            state[key] = start_ms
            save_state(state)
            f_out.flush()

            if total % 10_000 < BATCH_SIZE:
                print(f"  [{symbol}] {total:,}/{n_candles:,} velas ({100*total/n_candles:.1f}%)")

            time.sleep(SLEEP_BETWEEN)

        print(f"  [{symbol}] ✓ {total:,} velas → {out_csv.name}")
    finally:
        f_out.close()

    return out_csv


def main():
    print("=" * 70)
    print(f"Descargando {CANDLES_PER_TOKEN:,} velas 1m × {len(TOKENS)} tokens")
    print(f"Total esperado: {CANDLES_PER_TOKEN * len(TOKENS):,} velas")
    print("=" * 70)

    for i, sym in enumerate(TOKENS, 1):
        print(f"\n[{i}/{len(TOKENS)}] {sym}")
        try:
            download_token(sym, CANDLES_PER_TOKEN)
        except Exception as e:
            print(f"  [{sym}] ERROR: {e}", file=sys.stderr)

    # Summary
    print("\n" + "=" * 70)
    print("DESCARGA COMPLETA")
    print("=" * 70)
    total_rows = 0
    for sym in TOKENS:
        p = OUT_DIR / f"{sym}_1m.csv"
        if p.exists():
            with open(p) as f:
                n = sum(1 for _ in f) - 1
            total_rows += n
            size_mb = p.stat().st_size / 1e6
            print(f"  {sym}: {n:,} velas ({size_mb:.1f} MB)")
    print(f"\nTotal: {total_rows:,} velas, {OUT_DIR}")


if __name__ == "__main__":
    main()
