"""Compare Coinbase (new) vs Binance (existing) data for DOGEUSDT 5m BULL_2024."""
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
from ppmt.data.storage import PPMTStorage as Storage

storage = Storage()
conn = sqlite3.connect(storage.db_path)

print("=" * 80)
print("DOGEUSDT 5m BULL_2024: Binance (ohlcv_ext) vs Coinbase (ohlcv_ext_cb)")
print("=" * 80)

# Binance
print("\n--- Binance (ohlcv_ext) ---")
rows = conn.execute("""
    SELECT COUNT(*), MIN(timestamp), MAX(timestamp),
           MIN(close), MAX(close), AVG(volume)
    FROM ohlcv_ext
    WHERE symbol='DOGEUSDT' AND timeframe='5m' AND window='BULL_2024'
""").fetchone()
if rows and rows[0]:
    from datetime import datetime, timezone
    n, tmin, tmax, cmin, cmax, vavg = rows
    print(f"  candles:    {n:,}")
    print(f"  range UTC:  {datetime.fromtimestamp(tmin, tz=timezone.utc)} → {datetime.fromtimestamp(tmax, tz=timezone.utc)}")
    print(f"  close min/max: {cmin:.6f} / {cmax:.6f}")
    print(f"  avg volume: {vavg:,.2f}")
else:
    print("  no data")

# Coinbase
print("\n--- Coinbase (ohlcv_ext_cb) ---")
rows = conn.execute("""
    SELECT COUNT(*), MIN(timestamp), MAX(timestamp),
           MIN(close), MAX(close), AVG(volume)
    FROM ohlcv_ext_cb
    WHERE symbol='DOGEUSDT' AND timeframe='5m' AND window='BULL_2024'
""").fetchone()
n, tmin, tmax, cmin, cmax, vavg = rows
from datetime import datetime, timezone
print(f"  candles:    {n:,}")
print(f"  range UTC:  {datetime.fromtimestamp(tmin, tz=timezone.utc)} → {datetime.fromtimestamp(tmax, tz=timezone.utc)}")
print(f"  close min/max: {cmin:.6f} / {cmax:.6f}")
print(f"  avg volume: {vavg:,.2f}")

# Direct comparison: same timestamps
print("\n--- Direct candle-by-candle comparison (overlap) ---")
overlap = conn.execute("""
    SELECT b.timestamp, b.close AS binance_close, c.close AS coinbase_close,
           b.volume AS binance_vol, c.volume AS coinbase_vol
    FROM ohlcv_ext b
    INNER JOIN ohlcv_ext_cb c USING (symbol, timeframe, timestamp, window)
    WHERE b.symbol='DOGEUSDT' AND b.timeframe='5m' AND b.window='BULL_2024'
    ORDER BY b.timestamp
    LIMIT 5
""").fetchall()
if overlap:
    print(f"  overlap rows: {len(overlap)}+ (showing 5)")
    for ts, bc, cc, bv, cv in overlap:
        diff_pct = abs(bc - cc) / bc * 100
        print(f"  ts={ts} binance_close={bc:.6f} coinbase_close={cc:.6f} diff={diff_pct:.3f}%  vol_b={bv:.0f} vol_c={cv:.0f}")
else:
    print("  NO OVERLAP — Binance has no DOGEUSDT 5m BULL_2024 data")

# Coinbase data sanity check: gaps, duplicates, ordering
print("\n--- Coinbase data integrity check ---")
all_ts = [r[0] for r in conn.execute("""
    SELECT timestamp FROM ohlcv_ext_cb
    WHERE symbol='DOGEUSDT' AND timeframe='5m' AND window='BULL_2024'
    ORDER BY timestamp
""").fetchall()]
print(f"  total candles: {len(all_ts):,}")
gaps = sum(1 for i in range(1, len(all_ts)) if all_ts[i] - all_ts[i-1] != 300)
print(f"  gaps (where Δt ≠ 300s): {gaps:,}")
dups = len(all_ts) - len(set(all_ts))
print(f"  duplicates: {dups}")
if len(all_ts) >= 2:
    print(f"  first 3 ts: {all_ts[:3]}")
    print(f"  last 3 ts: {all_ts[-3:]}")

conn.close()
