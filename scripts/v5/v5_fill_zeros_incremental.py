#!/usr/bin/env python3
"""
v5_fill_zeros_incremental.py — Download 0-coverage combos with incremental save.

Fetches candles in chunks and saves to DB after each chunk. This way,
progress is preserved even if the script is killed mid-fetch.

After each chunk:
  - Insert into DB
  - Update progress file with current chunk cursor

Next invocation resumes from the saved cursor.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v5_download_coinbase import WINDOWS, TOKENS, TF_MAP, EARLIEST_MS, PAGE_CANDLES

LOG = logging.getLogger("v5_zeros_inc")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)

DB = "/home/z/my-project/data/ppmt.db"
STATE_FILE = "/home/z/my-project/download/zeros_inc_state.json"


def load_state():
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text())
    return {}


def save_state(state):
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))


def find_pending_targets(cur):
    """Find 0-coverage OR partial-coverage combos that have a saved state cursor
    that hasn't reached end_ms. Excludes impossible XRP BEAR/early-RANGE."""
    SKIP = {
        # XRP was delisted from Coinbase Feb 2021, relisted July 2023
        ("XRPUSDT", "1m", "BEAR_2022"),
        ("XRPUSDT", "5m", "BEAR_2022"),
        ("XRPUSDT", "15m", "BEAR_2022"),
        ("XRPUSDT", "1m", "RANGE_2023"),
        ("XRPUSDT", "5m", "RANGE_2023"),
        ("XRPUSDT", "15m", "RANGE_2023"),
    }
    state = load_state()
    targets = []
    for (bsym, pair, cls) in TOKENS:
        if bsym == "BNBUSDT":
            continue
        for tf in ["1m", "5m", "15m"]:
            for (wn, ws, we, _desc) in WINDOWS:
                if (bsym, tf, wn) in SKIP:
                    continue
                earliest = EARLIEST_MS.get(pair, 0)
                if we < earliest:
                    continue
                key = f"{bsym}|{tf}|{wn}"
                st = state.get(key, {})
                if st.get("done"):
                    continue
                # Include if: (a) 0-coverage in DB, OR (b) has saved cursor < end_ms
                cur.execute(
                    "SELECT COUNT(*) FROM ohlcv_ext_cb "
                    "WHERE symbol=? AND timeframe=? AND window=?",
                    (bsym, tf, wn),
                )
                cnt = cur.fetchone()[0]
                has_state = bool(st) and "cursor" in st
                cursor_lt_end = st.get("cursor", 0) < we
                if cnt == 0 or (has_state and cursor_lt_end):
                    targets.append({
                        "sym": bsym, "pair": pair, "tf": tf, "window": wn,
                        "start_ms": ws, "end_ms": we,
                    })
    return targets


def fetch_and_save_chunk(conn, sym, pair, tf, window, chunk_start_ms, chunk_end_ms):
    """Fetch one chunk from Coinbase and save to DB. Returns (rows_fetched, rows_inserted)."""
    granularity, ms_per_candle = TF_MAP[tf]
    start_iso = datetime.fromtimestamp(chunk_start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = datetime.fromtimestamp(chunk_end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"https://api.exchange.coinbase.com/products/{pair}/candles"
    params = {"granularity": granularity, "start": start_iso, "end": end_iso}
    headers = {"User-Agent": "ppmt-v5-research/1.0", "Accept": "application/json"}

    for attempt in range(5):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                LOG.warning("429 — wait %ds", wait)
                time.sleep(wait)
                continue
            if r.status_code == 400:
                # chunk too big — return 0, caller will halve
                return 0, 0, True
            r.raise_for_status()
            rows = r.json()
            break
        except Exception as e:
            wait = 2 ** attempt
            LOG.warning("Error attempt %d: %s — wait %ds", attempt + 1, e, wait)
            time.sleep(wait)
    else:
        LOG.error("Failed chunk after 5 retries")
        return 0, 0, False

    if not isinstance(rows, list) or not rows:
        return 0, 0, False

    # Reverse to ASC (Coinbase returns DESC)
    rows_asc = list(reversed(rows))

    # Store
    batch = []
    for r in rows_asc:
        time_sec = r[0]
        low, high, opn, close, vol = r[1], r[2], r[3], r[4], r[5]
        batch.append((
            sym, tf, time_sec, window,
            float(opn), float(high), float(low), float(close), float(vol),
        ))

    CHUNK_SIZE = 500
    inserted = 0
    for i in range(0, len(batch), CHUNK_SIZE):
        chunk = batch[i:i + CHUNK_SIZE]
        placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(chunk))
        flat = [v for row in chunk for v in row]
        conn.execute(
            f"INSERT OR IGNORE INTO ohlcv_ext_cb "
            f"(symbol, timeframe, timestamp, window, open, high, low, close, volume) "
            f"VALUES {placeholders}",
            flat,
        )
    conn.commit()
    inserted = len(batch)

    return len(rows_asc), inserted, False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-seconds", type=int, default=110)
    args = parser.parse_args()

    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()

    # Find pending targets
    targets = find_pending_targets(cur)
    LOG.info("Found %d 0-coverage pending targets", len(targets))

    state = load_state()
    t_start = time.time()

    for t in targets:
        if time.time() - t_start > args.max_seconds:
            LOG.info("Hit max_seconds, exiting")
            break

        key = f"{t['sym']}|{t['tf']}|{t['window']}"
        pair = t['pair']
        tf = t['tf']
        window = t['window']
        ms_per_candle = TF_MAP[tf][1]
        chunk_ms = PAGE_CANDLES * ms_per_candle  # 300 candles per chunk

        # Resume from saved cursor, or start from window start (clipped to listing date)
        earliest = EARLIEST_MS.get(pair, 0)
        default_start = max(t['start_ms'], earliest)
        cursor = state.get(key, {}).get("cursor", default_start)
        end_ms = t['end_ms']

        if cursor >= end_ms:
            LOG.info("SKIP %s — already complete (cursor=%d)", key, cursor)
            continue

        # Check if DB now has data (maybe a previous run finished it)
        cur.execute(
            "SELECT COUNT(*) FROM ohlcv_ext_cb WHERE symbol=? AND timeframe=? AND window=?",
            (t['sym'], tf, window),
        )
        cur_cnt = cur.fetchone()[0]
        if cur_cnt > 0 and state.get(key, {}).get("cursor", 0) >= end_ms:
            LOG.info("SKIP %s — DB has %d candles, cursor at end", key, cur_cnt)
            continue

        LOG.info("→ %s: cursor=%s → %s (DB has %d candles)",
                 key,
                 datetime.fromtimestamp(cursor / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                 datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                 cur_cnt)

        # Fetch in chunks, save state after each chunk
        last_save = time.time()
        chunk_count = 0
        while cursor < end_ms:
            if time.time() - t_start > args.max_seconds:
                LOG.info("Hit max_seconds mid-job, saving state and exiting")
                state[key] = {"cursor": cursor, "end_ms": end_ms}
                save_state(state)
                return

            chunk_end = min(cursor + chunk_ms, end_ms)
            fetched, inserted, was_400 = fetch_and_save_chunk(
                conn, t['sym'], pair, tf, window, cursor, chunk_end
            )
            chunk_count += 1

            if was_400:
                # Halve chunk
                chunk_ms = max(chunk_ms // 2, ms_per_candle)
                LOG.info("  400 at chunk %d — halved chunk_ms to %d", chunk_count, chunk_ms)
                continue

            # Advance cursor: chunk_end + 1 to avoid re-fetching the boundary
            cursor = chunk_end + 1
            state[key] = {"cursor": cursor, "end_ms": end_ms}

            # Save state every 10 chunks or 30s
            if chunk_count % 10 == 0 or (time.time() - last_save) > 30:
                save_state(state)
                last_save = time.time()
                cur.execute(
                    "SELECT COUNT(*) FROM ohlcv_ext_cb WHERE symbol=? AND timeframe=? AND window=?",
                    (t['sym'], tf, window),
                )
                cur_cnt = cur.fetchone()[0]
                LOG.info("  chunk %d: cursor=%s, total=%d candles",
                         chunk_count,
                         datetime.fromtimestamp(cursor / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                         cur_cnt)

            time.sleep(0.04)  # be polite

        # Done with this target
        cur.execute(
            "SELECT COUNT(*) FROM ohlcv_ext_cb WHERE symbol=? AND timeframe=? AND window=?",
            (t['sym'], tf, window),
        )
        final_cnt = cur.fetchone()[0]
        LOG.info("✓ %s: DONE, %d candles total", key, final_cnt)
        state[key] = {"cursor": end_ms, "end_ms": end_ms, "done": True, "final_count": final_cnt}
        save_state(state)

    conn.close()
    LOG.info("Session done.")


if __name__ == "__main__":
    main()
