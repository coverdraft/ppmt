#!/usr/bin/env python3
"""Find missing (window, timeframe, symbol) combinations in ohlcv_ext_cb."""
import sqlite3
import datetime

DB = "/home/z/my-project/data/ppmt.db"
TARGET_SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
               "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOGEUSDT", "SHIBUSDT",
               "PEPEUSDT", "WIFUSDT", "BONKUSDT"]
TARGET_TFS = ["1m", "5m", "15m"]
WINDOWS = {
    "BULL_2024":   ("2024-10-01", "2024-12-31", 91),
    "RANGE_2025":  ("2025-08-01", "2025-10-31", 92),
    "RECENT_2026": ("2026-03-01", "2026-06-23", 115),
    "BEAR_2022":   ("2022-05-01", "2022-07-31", 91),
    "RANGE_2023":  ("2023-03-01", "2023-10-31", 245),
}
# expected candles per day per TF
CPD = {"1m": 1440, "5m": 288, "15m": 96}

conn = sqlite3.connect(DB)
cur = conn.cursor()

print(f"{'Window':<14} {'TF':<5} {'Have':<6} {'Need':<6} {'Missing symbols':<40} {'Coverage %':<10}")
print("-" * 90)

missing_summary = []
for wname, (wstart, wend, ndays) in WINDOWS.items():
    for tf in TARGET_TFS:
        cur.execute(
            "SELECT symbol, COUNT(*) FROM ohlcv_ext_cb "
            "WHERE window=? AND timeframe=? GROUP BY symbol",
            (wname, tf),
        )
        rows = {r[0]: r[1] for r in cur.fetchall()}
        have = sorted(set(rows.keys()) & set(TARGET_SYMS))
        missing = sorted(set(TARGET_SYMS) - set(rows.keys()))
        expected = ndays * CPD[tf]
        # Coverage % based on the best-covered symbol's candle count
        if rows:
            best_cnt = max(rows.values())
            cov_pct = round(100 * best_cnt / expected, 1)
        else:
            cov_pct = 0
        miss_str = ", ".join(missing) if missing else "-"
        print(f"{wname:<14} {tf:<5} {len(have):<6} {len(TARGET_SYMS):<6} {miss_str:<40} {cov_pct}%")
        for m in missing:
            missing_summary.append((wname, tf, m))

print()
print(f"Total missing (window, tf, sym) combos: {len(missing_summary)}")
print()
if missing_summary:
    print("Missing combos:")
    for w, t, s in missing_summary:
        print(f"  - {w} / {t} / {s}")

# Also check for low-coverage symbols (have rows but far below expected)
print()
print("Low-coverage symbols (< 80% of expected candles):")
print("-" * 60)
for wname, (wstart, wend, ndays) in WINDOWS.items():
    for tf in TARGET_TFS:
        expected = ndays * CPD[tf]
        cur.execute(
            "SELECT symbol, COUNT(*) FROM ohlcv_ext_cb "
            "WHERE window=? AND timeframe=? GROUP BY symbol",
            (wname, tf),
        )
        for sym, cnt in cur.fetchall():
            if sym not in TARGET_SYMS:
                continue
            pct = 100 * cnt / expected
            if pct < 80:
                print(f"  {wname} / {tf} / {sym}: {cnt:,} / {expected:,} ({pct:.1f}%)")

conn.close()
