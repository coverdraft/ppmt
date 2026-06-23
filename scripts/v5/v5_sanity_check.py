"""Sanity check on ohlcv_ext data: gaps, duplicates, OHLC coherence, TF alignment."""
from __future__ import annotations
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ppmt" / "src"))
from ppmt.data.storage import PPMTStorage

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000}
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}

def main():
    s = PPMTStorage()
    cur = s.conn.cursor()

    # Get all (symbol, tf, window) combos present
    cur.execute("""
        SELECT symbol, timeframe, window, COUNT(*) AS n,
               MIN(timestamp) AS mn, MAX(timestamp) AS mx
        FROM ohlcv_ext
        GROUP BY symbol, timeframe, window
        ORDER BY symbol, timeframe, window
    """)
    combos = cur.fetchall()
    print(f"=== Combos in ohlcv_ext: {len(combos)} ===\n")
    print(f"{'symbol':<12} {'tf':<4} {'window':<14} {'rows':>10} {'start':<11} {'end':<11}")
    for sym, tf, win, n, mn, mx in combos:
        from datetime import datetime, timezone
        s_dt = datetime.fromtimestamp(mn, tz=timezone.utc).strftime('%Y-%m-%d')
        e_dt = datetime.fromtimestamp(mx, tz=timezone.utc).strftime('%Y-%m-%d')
        print(f"{sym:<12} {tf:<4} {win:<14} {n:>10,} {s_dt:<11} {e_dt:<11}")

    print("\n=== Per-combo checks ===\n")
    issues_total = 0
    for sym, tf, win, n, mn, mx in combos:
        cur.execute("""
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv_ext
            WHERE symbol=? AND timeframe=? AND window=?
            ORDER BY timestamp
        """, (sym, tf, win))
        rows = cur.fetchall()
        if not rows:
            continue

        # 1. Duplicates
        ts_list = [r[0] for r in rows]
        dups = len(ts_list) - len(set(ts_list))
        # 2. OHLC coherence: high >= max(o,c), low <= min(o,c), high >= low
        bad_ohlc = 0
        for ts, o, h, l, c, v in rows:
            if h < max(o, c) - 1e-9 or l > min(o, c) + 1e-9 or h < l - 1e-9:
                bad_ohlc += 1
            if v < 0:
                bad_ohlc += 1
        # 3. Gaps: count intervals missing (allowing <=2% gaps for exchanges)
        step = TF_SEC[tf]
        expected = (mx - mn) / step + 1
        gaps = expected - len(rows)
        gap_pct = (gaps / expected * 100) if expected > 0 else 0
        # 4. Negative prices
        neg_prices = sum(1 for r in rows if r[1] <= 0 or r[2] <= 0 or r[3] <= 0 or r[4] <= 0)

        issues = 0
        flags = []
        if dups > 0:
            flags.append(f"DUPS={dups}"); issues += 1
        if bad_ohlc > 0:
            flags.append(f"BAD_OHLC={bad_ohlc}"); issues += 1
        if gap_pct > 5:
            flags.append(f"GAPS={gaps:.0f}({gap_pct:.1f}%)"); issues += 1
        if neg_prices > 0:
            flags.append(f"NEG={neg_prices}"); issues += 1

        status = "OK" if not flags else "FAIL: " + " ".join(flags)
        print(f"  {sym:<10} {tf:<4} {win:<14} {n:>8,}  {status}")
        issues_total += issues

    # 5. TF alignment: 5m candles should fall exactly on 1m candle boundaries
    # For DOGE BULL_2024: check that 5m close == 5x1m aggregated close
    print("\n=== TF alignment check (DOGE BULL_2024 1m vs 5m) ===\n")
    cur.execute("""
        SELECT timestamp, close, high, low, open, volume FROM ohlcv_ext
        WHERE symbol='DOGEUSDT' AND timeframe='1m' AND window='BULL_2024'
        ORDER BY timestamp
    """)
    m1 = {r[0]: r for r in cur.fetchall()}
    cur.execute("""
        SELECT timestamp, close, high, low, open, volume FROM ohlcv_ext
        WHERE symbol='DOGEUSDT' AND timeframe='5m' AND window='BULL_2024'
        ORDER BY timestamp
    """)
    m5_rows = cur.fetchall()

    if not m1 or not m5_rows:
        print("  SKIP: missing 1m or 5m data")
    else:
        mismatches = 0
        checked = 0
        for ts5, c5, h5, l5, o5, v5 in m5_rows[:500]:  # check first 500
            ts1_base = ts5
            ts1s = [ts1_base + 60*i for i in range(5)]
            if not all(t in m1 for t in ts1s):
                continue
            m1_rows = [m1[t] for t in ts1s]
            m1_close = m1_rows[-1][1]  # last 1m close == 5m close
            m1_high = max(r[2] for r in m1_rows)
            m1_low = min(r[3] for r in m1_rows)
            m1_open = m1_rows[0][4]
            m1_vol = sum(r[5] for r in m1_rows)
            checked += 1
            if abs(m1_close - c5) > 1e-6 * max(c5, 1e-9):
                mismatches += 1
                if mismatches <= 3:
                    print(f"  close mismatch @ {ts5}: 1m_agg={m1_close:.6f} vs 5m={c5:.6f}")
            if abs(m1_high - h5) > 1e-6 * max(h5, 1e-9):
                mismatches += 1
                if mismatches <= 6:
                    print(f"  high mismatch @ {ts5}: 1m_agg={m1_high:.6f} vs 5m={h5:.6f}")
            if abs(m1_low - l5) > 1e-6 * max(l5, 1e-9):
                mismatches += 1
                if mismatches <= 9:
                    print(f"  low mismatch @ {ts5}: 1m_agg={m1_low:.6f} vs 5m={l5:.6f}")
        print(f"  Checked {checked} 5m candles. Mismatches: {mismatches}")
        if checked > 0:
            print(f"  Mismatch rate: {mismatches/checked*100:.2f}%")

    print(f"\n=== TOTAL ISSUES: {issues_total} ===")
    print("VERDICT:", "PASS" if issues_total == 0 else "INVESTIGATE")


if __name__ == "__main__":
    main()
