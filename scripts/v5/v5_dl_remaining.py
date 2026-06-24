"""Run downloads one combo at a time, each as a short subprocess with timeout."""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
from ppmt.data.storage import PPMTStorage

TF_MS = {"1m": 60, "5m": 300, "15m": 900}
WINDOWS = [
    ("BULL_2024", 1727740800, 1735516800),
    ("RANGE_2025", 1753990400, 1761817200),
    ("RECENT_2026", 1742774400, 1750636800),
    ("BEAR_2022", 1651363200, 1659139200),
    ("RANGE_2023", 1677628800, 1698710400),
]
TOKENS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","AVAXUSDT",
          "LINKUSDT","DOGEUSDT","SHIBUSDT","PEPEUSDT","WIFUSDT","BONKUSDT"]
SKIP = {
    "BEAR_2022": {"PEPEUSDT","WIFUSDT","BONKUSDT"},
    "RANGE_2023": {"PEPEUSDT","WIFUSDT","BONKUSDT"},
}

def already_done(s, sym, tf, win):
    cur = s.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ohlcv_ext WHERE symbol=? AND timeframe=? AND window=?",
                (sym, tf, win))
    return cur.fetchone()[0] > 0

def main():
    s = PPMTStorage()
    # Build a set of already-done combos ONCE (faster than re-querying each iter)
    cur = s.conn.cursor()
    cur.execute("SELECT symbol, timeframe, window FROM ohlcv_ext")
    done = {(r[0], r[1], r[2]) for r in cur.fetchall()}
    print(f"Already done: {len(done)} combos", flush=True)

    # Order: 5m all, then 15m all, then 1m all (smallest first)
    for tf in ["5m", "15m", "1m"]:
        for win, ws, we in WINDOWS:
            for sym in TOKENS:
                if sym in SKIP.get(win, set()):
                    continue
                if (sym, tf, win) in done:
                    continue
                print(f"DL {sym} {tf} {win}...", flush=True)
                cmd = ["/home/z/.venv/bin/python", "-u",
                       "/home/z/my-project/scripts/v5_download_multiex.py",
                       "--timeframes", tf, "--windows", win,
                       "--tokens", sym, "--workers", "1"]
                # Use timeout: 5m = 60s, 15m = 30s, 1m = 200s
                # (RANGE_2023 has 8 months of data — needs more pages)
                timeout = {"5m": 60, "15m": 30, "1m": 200}[tf]
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True,
                                       timeout=timeout)
                    out = r.stdout
                    if "OK:" in out:
                        for line in out.split("\n"):
                            if "OK:" in line:
                                print(f"  -> {line.strip()}", flush=True)
                                break
                        done.add((sym, tf, win))
                    else:
                        print(f"  no OK in output; rc={r.returncode}", flush=True)
                        if r.stderr:
                            print(f"  stderr: {r.stderr[-300:]}", flush=True)
                except subprocess.TimeoutExpired:
                    print(f"  TIMEOUT after {timeout}s", flush=True)
                except Exception as e:
                    print(f"  ERROR: {e}", flush=True)
                time.sleep(0.5)

if __name__ == "__main__":
    main()
