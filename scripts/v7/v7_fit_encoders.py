"""
F2 — Fit all 4 sector encoders on real DB candles.

Outputs:
- data/v7_models/encoders/{sector}_encoder.json (4 files)
- Console: per-sector distribution + sample trie keys

This is a sanity-check script. The encoders are saved for use in F3
(sectorial trie construction).
"""

import os
import sys
import sqlite3
import json

# Add scripts/v7 to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "v7"))

from v7_ohlcv_encoder import (
    OHLCVCompositeEncoder,
    SECTOR_BINS,
    SECTOR_TOKENS,
    SECTOR_SEQ_LENGTHS,
    symbol_to_sector,
    compute_composite_score,
    compute_vol_ma20,
)

DB_PATH = "data/ppmt.db"
OUTPUT_DIR = "data/v7_models/encoders"


def load_candles(symbol: str, timeframe: str = "5m", limit: int = 50000):
    """Load recent OHLCV candles from ppmt.db, oldest first."""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM ohlcv_v6 WHERE symbol=? AND timeframe=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (f"{symbol}USDT", timeframe, limit),
        )
        rows = c.fetchall()
    finally:
        conn.close()
    rows.reverse()
    return rows


def fit_sector(sector: str, timeframe: str = "5m") -> OHLCVCompositeEncoder:
    """Fit encoder for a sector by pooling candles from all tokens in it."""
    tokens = SECTOR_TOKENS[sector]
    print(f"\n[{sector}] tokens={tokens}, bins={SECTOR_BINS[sector]}, seq_lengths={SECTOR_SEQ_LENGTHS[sector]}")

    all_scores = []
    per_token_data = {}
    for tok in tokens:
        rows = load_candles(tok, timeframe=timeframe, limit=50000)
        if len(rows) < 1000:
            print(f"  {tok}: only {len(rows)} rows (skip)")
            continue
        opens = [r[1] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        closes = [r[4] for r in rows]
        vols = [r[5] for r in rows]
        vmas = compute_vol_ma20(vols, window=20)
        # Skip first 20 (warmup)
        scores = [
            compute_composite_score(opens[i], highs[i], lows[i], closes[i], vols[i], vmas[i])
            for i in range(20, len(rows))
        ]
        all_scores.extend(scores)
        per_token_data[tok] = {
            "n_candles": len(rows),
            "n_scores": len(scores),
            "score_mean": sum(scores) / len(scores),
            "score_min": min(scores),
            "score_max": max(scores),
        }
        print(f"  {tok}: n={len(rows)} mean={sum(scores)/len(scores):.4f} min={min(scores):.4f} max={max(scores):.4f}")

    if len(all_scores) < SECTOR_BINS[sector] * 100:
        print(f"  ! Insufficient scores ({len(all_scores)}) — skip")
        return None

    enc = OHLCVCompositeEncoder.for_sector(sector)
    enc.fit(all_scores, method="percentile")

    print(f"  FITTED: bins={enc.bins}, train_count={enc.train_count}")
    print(f"  breakpoints={[round(b, 4) for b in enc.breakpoints]}")
    print(f"  train_mean={enc.train_mean:.4f}, train_std={enc.train_std:.4f}")

    # Verify distribution
    syms = [enc.quantize(s) for s in all_scores]
    dist = enc.symbol_distribution(syms)
    print(f"  distribution: {dist}")

    # Sample trie keys from the LAST token (just for display)
    last_tok = tokens[-1]
    if last_tok in per_token_data:
        rows = load_candles(last_tok, timeframe=timeframe, limit=200)
        candles = [(r[1], r[2], r[3], r[4], r[5], 1.0) for r in rows]
        # Compute proper vol_ma for last 200
        vols = [r[5] for r in rows]
        vmas = compute_vol_ma20(vols, window=20)
        candles = [(rows[i][1], rows[i][2], rows[i][3], rows[i][4], rows[i][5], vmas[i]) for i in range(len(rows))]
        # Use the last 15 candles
        for seq_len in SECTOR_SEQ_LENGTHS[sector]:
            if len(candles) >= seq_len:
                key = enc.encode_sequence(candles, seq_len=seq_len)
                print(f"  sample key ({last_tok}, seq_len={seq_len}): {key!r}")

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"{sector}_encoder.json")
    enc.to_json(out_path)
    print(f"  saved: {out_path}")

    return enc


def main():
    print(f"DB: {DB_PATH}")
    print(f"Output dir: {OUTPUT_DIR}")

    encoders = {}
    for sector in SECTOR_BINS:
        enc = fit_sector(sector, timeframe="5m")
        if enc is not None:
            encoders[sector] = enc

    print("\n" + "=" * 60)
    print("F2 SUMMARY")
    print("=" * 60)
    for sector, enc in encoders.items():
        print(f"  {sector:10s} bins={enc.bins} train={enc.train_count:>7d} "
              f"bp={[round(b, 3) for b in enc.breakpoints]}")
    print(f"\nAll {len(encoders)} sector encoders saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
