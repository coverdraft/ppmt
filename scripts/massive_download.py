#!/usr/bin/env python3
"""Massive Download Script — 4 Lotes con Rate Limiting Seguro.

Usage:
    python /home/z/my-project/ppmt/scripts/massive_download.py
"""

import sys
import time

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.data.bulk_downloader import BulkDownloader
from ppmt.data.storage import PPMTStorage

# Lotes definidos por la directriz
LOTS = [
    {
        "name": "Lote 1 — Blue Chips",
        "tokens": {"blue_chip": ["BTC/USDT", "ETH/USDT"]},
    },
    {
        "name": "Lote 2 — Large Cap",
        "tokens": {"large_cap": ["SOL/USDT", "BNB/USDT", "XRP/USDT"]},
    },
    {
        "name": "Lote 3 — Mid Cap",
        "tokens": {"mid_cap": ["LINK/USDT", "AVAX/USDT", "DOT/USDT"]},
    },
    {
        "name": "Lote 4 — Memes (CRÍTICO para __CLASS_meme__)",
        "tokens": {"meme": ["DOGE/USDT", "SHIB/USDT", "WIF/USDT"]},
    },
]

TIMEFRAMES = ["1m", "5m", "15m"]
DAYS = 90
# 1s per page request, 5s between tokens
PAGE_DELAY = 1.0
BETWEEN_TOKEN_PAUSE = 5.0
BETWEEN_LOT_PAUSE = 10.0


def main():
    storage = PPMTStorage()
    downloader = BulkDownloader(exchange="binance")

    total_stats = {
        "total_requested": 0,
        "total_downloaded": 0,
        "total_rows": 0,
        "errors": [],
    }

    for lot_idx, lot in enumerate(LOTS):
        print(f"\n{'#'*70}")
        print(f"# {lot['name']}")
        print(f"{'#'*70}")

        stats = downloader.download_all(
            days=DAYS,
            tokens=lot["tokens"],
            timeframes=TIMEFRAMES,
            storage=storage,
            request_delay=PAGE_DELAY,
        )

        total_stats["total_requested"] += stats["total_requested"]
        total_stats["total_downloaded"] += stats["total_downloaded"]
        total_stats["total_rows"] += stats["total_rows"]
        total_stats["errors"].extend(stats["errors"])

        # Pausa entre lotes (excepto el último)
        if lot_idx < len(LOTS) - 1:
            print(f"\n⏳ Pausa {BETWEEN_LOT_PAUSE}s antes del siguiente lote...")
            time.sleep(BETWEEN_LOT_PAUSE)

    print(f"\n{'='*70}")
    print("RESUMEN TOTAL DE DESCARGA MASIVA")
    print(f"{'='*70}")
    print(f"Total requested: {total_stats['total_requested']}")
    print(f"Total downloaded: {total_stats['total_downloaded']}")
    print(f"Total rows: {total_stats['total_rows']:,}")
    print(f"Errors: {len(total_stats['errors'])}")
    for err in total_stats["errors"]:
        print(f"  - {err}")

    # Verificar OHLCV en DB
    print(f"\n--- Verificación OHLCV en DB ---")
    import sqlite3, os
    conn = sqlite3.connect(os.path.expanduser("~/.ppmt/ppmt.db"))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT symbol, timeframe, COUNT(*) as candles FROM ohlcv GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
    )
    for r in cursor.fetchall():
        print(f"  {r[0]:15s} | {r[1]:5s} | {r[2]:>8,d} candles")
    conn.close()


if __name__ == "__main__":
    main()
