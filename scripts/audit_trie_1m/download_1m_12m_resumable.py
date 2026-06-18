"""
Resume-capable 12m downloader. Descarga un token por invocación, en bloques
de 25k velas. Si el proceso se cae, en la próxima invocación continúa desde
donde quedó.

Uso:
  python3 scripts/download_1m_12m_resumable.py BTCUSDT
  python3 scripts/download_1m_12m_resumable.py ETHUSDT
  ...
"""
from __future__ import annotations
import csv, json, sys, time, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone

# CSV fields can be large; raise the limit
csv.field_size_limit(10**7)

OUT_DIR = Path("/home/z/my-project/download/real_data_1m_12m")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TARGET_CANDLES = 525_600      # 365 días
PER_REQUEST    = 1000
SLEEP_SEC      = 0.12
MAX_RETRIES    = 5
CHUNK_SIZE     = 25_000       # descargar en chunks de 25k velas

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
BASE = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol: str, end_time: int | None, limit: int = PER_REQUEST) -> list[list]:
    params = {"symbol": symbol, "interval": "1m", "limit": str(limit)}
    if end_time is not None:
        params["endTime"] = str(end_time - 1)
    qs = urllib.parse.urlencode(params)
    url = f"{BASE}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url} — {last_err}")


def row_to_csv(k: list) -> list:
    return [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]),
            int(k[6]), float(k[7]), int(k[8]), float(k[9]), float(k[10]), k[11] or ""]


def count_rows(path: Path) -> int:
    if not path.exists(): return 0
    n = 0
    with path.open("r", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for _ in reader: n += 1
    return n


def get_first_open_time(path: Path) -> int | None:
    if not path.exists(): return None
    with path.open("r", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        first = next(reader, None)
        return int(first[0]) if first else None


def download_chunk(symbol: str, end_time: int | None, n_needed: int) -> tuple[list[list], int]:
    """Download up to n_needed candles, walking backwards from end_time.
    Returns (rows_in_ascending_order, n_actually_fetched).
    """
    fetched: list[list] = []
    cur_end = end_time
    while len(fetched) < n_needed:
        batch = fetch_klines(symbol, cur_end, PER_REQUEST)
        if not batch:
            print(f"    empty batch at end_time={cur_end}", flush=True)
            break
        # batch is ascending, prepend to fetched (we want final ascending)
        fetched = batch + fetched
        oldest = int(batch[0][0])
        if len(batch) < PER_REQUEST:
            print(f"    short batch ({len(batch)}), no more history", flush=True)
            break
        cur_end = oldest
        time.sleep(SLEEP_SEC)
        if len(fetched) % 5000 < PER_REQUEST:
            print(f"    fetched={len(fetched):,}/{n_needed:,}", flush=True)
    # Trim to n_needed (keep newest)
    if len(fetched) > n_needed:
        fetched = fetched[-n_needed:]
    rows = [row_to_csv(k) for k in fetched]
    return rows, len(rows)


def merge_prepend(existing_path: Path, new_rows: list[list], tmp_path: Path) -> None:
    """Write new_rows ++ existing_rows to tmp_path, then replace existing_path."""
    with tmp_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["open_time","open","high","low","close","volume",
                    "close_time","quote_volume","n_trades",
                    "taker_buy_base","taker_buy_quote","ignore"])
        # new_rows first (older)
        for r in new_rows:
            w.writerow(r)
        # then existing (newer)
        if existing_path.exists():
            with existing_path.open("r", newline="") as fin:
                reader = csv.reader(fin)
                next(reader, None)
                for r in reader:
                    w.writerow(r)
    # Use os.replace which is atomic and handles existing files
    import os
    os.replace(tmp_path, existing_path)


def download_symbol(symbol: str) -> None:
    out_path = OUT_DIR / f"{symbol}_1m.csv"
    tmp_path = OUT_DIR / f"{symbol}_1m.tmp.csv"

    current_count = count_rows(out_path)
    print(f"[{symbol}] start: existing={current_count:,}, target={TARGET_CANDLES:,}", flush=True)

    round_num = 0
    while current_count < TARGET_CANDLES:
        round_num += 1
        n_needed = min(CHUNK_SIZE, TARGET_CANDLES - current_count)
        end_time = get_first_open_time(out_path)  # None if no existing
        print(f"[{symbol}] round {round_num}: fetching {n_needed:,} candles before end_time={end_time}", flush=True)
        try:
            new_rows, n_fetched = download_chunk(symbol, end_time, n_needed)
        except Exception as e:
            print(f"[{symbol}] ERROR in round {round_num}: {e}", flush=True)
            break
        if n_fetched == 0:
            print(f"[{symbol}] no more history available", flush=True)
            break
        merge_prepend(out_path, new_rows, tmp_path)
        current_count = count_rows(out_path)
        first_ts = get_first_open_time(out_path)
        first_dt = datetime.fromtimestamp(first_ts/1000, tz=timezone.utc) if first_ts else "?"
        print(f"[{symbol}] round {round_num} done: total={current_count:,}, first={first_dt}", flush=True)
        if n_fetched < n_needed:
            print(f"[{symbol}] Binance returned less than requested, no more history", flush=True)
            break

    # Final
    first_ts = get_first_open_time(out_path)
    last_ts = None
    with out_path.open("r", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        last = None
        for r in reader: last = r
        if last: last_ts = int(last[0])
    first_dt = datetime.fromtimestamp(first_ts/1000, tz=timezone.utc) if first_ts else "?"
    last_dt = datetime.fromtimestamp(last_ts/1000, tz=timezone.utc) if last_ts else "?"
    print(f"[{symbol}] FINAL: total={current_count:,}, range={first_dt} → {last_dt}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 download_1m_12m_resumable.py SYMBOL [SYMBOL...]")
        sys.exit(1)
    for sym in sys.argv[1:]:
        download_symbol(sym)
