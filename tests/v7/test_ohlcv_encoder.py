"""
Tests for OHLCVCompositeEncoder (F2).

Verifies:
- Symbol → sector routing (BTCUSDT, BTC, BTC-USD, etc.)
- Composite score math (body, direction, vol_signal)
- Sectorized bin counts (3/4/5/6)
- fit() percentile breakpoints (balanced distribution)
- Quantization correctness (boundary behavior)
- encode_sequence: trie key length and alphabet
- Anti-leakage: vol_ma20 uses closed='left'
- Serialization round-trip
- Method='normal' (z-score breakpoints)
- Edge cases (doji, NaN, warm-up)
- Real DB sanity check (skipped if DB not available)
"""

import sys
import os
import json
import math
import statistics
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "v7"))

from v7_ohlcv_encoder import (
    OHLCVCompositeEncoder,
    SECTOR_BINS,
    SECTOR_SEQ_LENGTHS,
    SECTOR_TOKENS,
    DEFAULT_WEIGHTS,
    symbol_to_sector,
    compute_composite_score,
    compute_vol_ma20,
    VOL_SIGNAL_MIN,
    VOL_SIGNAL_MAX,
    VOL_MA_WARMUP_FALLBACK,
)


# ---------------------------------------------------------------------------
# 1. Symbol → sector routing
# ---------------------------------------------------------------------------

def test_symbol_to_sector_basic():
    assert symbol_to_sector("BTC") == "blue_chip"
    assert symbol_to_sector("ETH") == "blue_chip"
    assert symbol_to_sector("SOL") == "large_cap"
    assert symbol_to_sector("ADA") == "large_cap"
    assert symbol_to_sector("AVAX") == "large_cap"
    assert symbol_to_sector("LINK") == "large_cap"
    assert symbol_to_sector("XRP") == "old_meme"
    assert symbol_to_sector("DOGE") == "old_meme"
    assert symbol_to_sector("SHIB") == "old_meme"
    assert symbol_to_sector("PEPE") == "new_meme"
    assert symbol_to_sector("WIF") == "new_meme"
    assert symbol_to_sector("BONK") == "new_meme"
    print("✓ test_symbol_to_sector_basic")


def test_symbol_to_sector_with_suffix():
    assert symbol_to_sector("BTCUSDT") == "blue_chip"
    assert symbol_to_sector("BTCUSD") == "blue_chip"
    assert symbol_to_sector("BTC-USDT") == "blue_chip"
    assert symbol_to_sector("BTC-USD") == "blue_chip"
    assert symbol_to_sector("BTCPERP") == "blue_chip"
    assert symbol_to_sector("btcusdt") == "blue_chip"
    assert symbol_to_sector("  btc  ") == "blue_chip"
    print("✓ test_symbol_to_sector_with_suffix")


def test_symbol_to_sector_unknown():
    try:
        symbol_to_sector("UNKNOWN")
        assert False, "Expected ValueError"
    except ValueError:
        pass
    try:
        symbol_to_sector("LTC")
        assert False, "Expected ValueError for LTC (not in any sector)"
    except ValueError:
        pass
    print("✓ test_symbol_to_sector_unknown")


# ---------------------------------------------------------------------------
# 2. Composite score math
# ---------------------------------------------------------------------------

def test_composite_bullish_candle():
    """Strong bullish candle: body>0, direction=+1, vol above average."""
    # open=100, close=110, high=112, low=99, vol=200, vol_ma=100
    # body = (110-100)/(112-99) = 10/13 ≈ 0.769
    # direction = +1
    # vol_signal = clip(200/100, 0.5, 5.0) = 2.0
    # composite = 0.769*0.4 + 1*0.35 + 2.0*0.25 = 0.308 + 0.35 + 0.5 = 1.158
    score = compute_composite_score(100, 112, 99, 110, 200, 100)
    assert score > 1.0, f"Expected strong positive, got {score}"
    assert abs(score - 1.158) < 0.01, f"Expected ~1.158, got {score}"
    print("✓ test_composite_bullish_candle")


def test_composite_bearish_candle():
    """Strong bearish candle: body<0, direction=-1, vol above average."""
    # open=110, close=100, high=112, low=99, vol=200, vol_ma=100
    # body = (100-110)/(112-99) = -10/13 ≈ -0.769
    # direction = -1
    # vol_signal = 2.0
    # composite = -0.308 - 0.35 + 0.5 = -0.158
    score = compute_composite_score(110, 112, 99, 100, 200, 100)
    assert score < 0.0, f"Expected negative, got {score}"
    assert abs(score - (-0.158)) < 0.01, f"Expected ~-0.158, got {score}"
    print("✓ test_composite_bearish_candle")


def test_composite_doji_candle():
    """Doji: open==close, body=0, direction=0. Should still get vol_signal."""
    # body=0, direction=0, vol_signal=2.0
    # composite = 0 + 0 + 0.5 = 0.5
    score = compute_composite_score(100, 110, 95, 100, 200, 100)
    assert abs(score - 0.5) < 0.001, f"Expected 0.5, got {score}"
    print("✓ test_composite_doji_candle")


def test_composite_vol_signal_clipping():
    """Volume signal must be clipped to [0.5, 5.0]."""
    # vol=10000, vol_ma=100 → ratio=100, clipped to 5.0
    # body = (110-100)/(110-95) = 10/15 ≈ 0.667
    # direction = +1
    # vol_signal = 5.0
    # composite = 0.667*0.4 + 1*0.35 + 5.0*0.25 = 0.267 + 0.35 + 1.25 = 1.867
    score = compute_composite_score(100, 110, 95, 110, 10000, 100)
    assert abs(score - 1.867) < 0.001, f"Expected 1.867, got {score}"

    # vol=10, vol_ma=100 → ratio=0.1, clipped to 0.5
    # body = (95-100)/(110-95) = -5/15 ≈ -0.333
    # direction = -1
    # vol_signal = 0.5
    # composite = -0.333*0.4 + (-1)*0.35 + 0.5*0.25 = -0.133 - 0.35 + 0.125 = -0.358
    score2 = compute_composite_score(100, 110, 95, 95, 10, 100)
    assert abs(score2 - (-0.358)) < 0.001, f"Expected -0.358, got {score2}"
    print("✓ test_composite_vol_signal_clipping")


def test_composite_zero_range():
    """high==low edge case: body_score = 0 (avoid div by zero)."""
    score = compute_composite_score(100, 100, 100, 100, 100, 100)
    # body=0, direction=0, vol_signal=1.0
    # composite = 0 + 0 + 0.25 = 0.25
    assert abs(score - 0.25) < 0.001, f"Expected 0.25, got {score}"
    print("✓ test_composite_zero_range")


def test_composite_vol_ma_warmup():
    """vol_ma20=0 or NaN → fallback to 1.0 (average volume)."""
    # open=100, high=110, low=95, close=110, vol=200, vol_ma=0 → fallback to 1.0
    # body = (110-100)/(110-95) = 10/15 ≈ 0.667
    # direction = +1
    # vol_signal = 1.0 (warmup fallback)
    # composite = 0.667*0.4 + 1*0.35 + 1.0*0.25 = 0.267 + 0.35 + 0.25 = 0.867
    score_zero = compute_composite_score(100, 110, 95, 110, 200, 0)
    score_nan = compute_composite_score(100, 110, 95, 110, 200, float("nan"))
    assert abs(score_zero - 0.867) < 0.001, f"Expected 0.867, got {score_zero}"
    assert abs(score_nan - 0.867) < 0.001, f"Expected 0.867, got {score_nan}"
    print("✓ test_composite_vol_ma_warmup")


# ---------------------------------------------------------------------------
# 3. Encoder construction
# ---------------------------------------------------------------------------

def test_encoder_factory_for_sector():
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")
    assert enc.sector == "blue_chip"
    assert enc.bins == 3
    assert not enc.fitted

    enc2 = OHLCVCompositeEncoder.for_sector("new_meme")
    assert enc2.sector == "new_meme"
    assert enc2.bins == 6
    print("✓ test_encoder_factory_for_sector")


def test_encoder_factory_for_symbol():
    enc = OHLCVCompositeEncoder.for_symbol("BTCUSDT")
    assert enc.sector == "blue_chip"
    assert enc.bins == 3

    enc2 = OHLCVCompositeEncoder.for_symbol("PEPE")
    assert enc2.sector == "new_meme"
    assert enc2.bins == 6
    print("✓ test_encoder_factory_for_symbol")


def test_encoder_unknown_sector():
    try:
        OHLCVCompositeEncoder(sector="unknown", bins=3)
        assert False
    except ValueError:
        pass
    print("✓ test_encoder_unknown_sector")


def test_encoder_not_fitted_raises():
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")
    try:
        enc.encode_candle(100, 110, 95, 105, 200, 100)
        assert False
    except RuntimeError:
        pass
    print("✓ test_encoder_not_fitted_raises")


# ---------------------------------------------------------------------------
# 4. fit() + quantization
# ---------------------------------------------------------------------------

def _gen_synthetic_scores(n=10000, seed=42):
    """Generate synthetic composite scores ~ N(0, 1)."""
    import random
    rng = random.Random(seed)
    # Use Box-Muller
    out = []
    for _ in range(n // 2):
        u1 = max(1e-10, rng.random())
        u2 = rng.random()
        z0 = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        z1 = math.sqrt(-2.0 * math.log(u1)) * math.sin(2.0 * math.pi * u2)
        out.append(z0)
        out.append(z1)
    return out[:n]


def test_fit_percentile_balanced():
    """With percentile breakpoints, each bin should hold ~1/N of the data."""
    scores = _gen_synthetic_scores(n=20000)
    for sector, bins in SECTOR_BINS.items():
        enc = OHLCVCompositeEncoder.for_sector(sector)
        enc.fit(scores, method="percentile")
        assert enc.fitted
        assert len(enc.breakpoints) == bins - 1

        # Quantize all scores and check distribution
        syms = [enc.quantize(s) for s in scores]
        dist = enc.symbol_distribution(syms)
        expected = 1.0 / bins
        for sym, frac in dist.items():
            assert abs(frac - expected) < 0.02, (
                f"sector={sector} sym={sym} frac={frac:.3f} expected={expected:.3f}"
            )
        # All expected symbols present
        assert len(dist) == bins, f"Expected {bins} symbols, got {len(dist)}"
        # First symbol is 'a'
        assert sorted(dist.keys()) == [chr(ord("a") + i) for i in range(bins)]
    print("✓ test_fit_percentile_balanced")


def test_fit_normal_method():
    """normal method uses z-score breakpoints; should be roughly balanced
    on normally-distributed input."""
    scores = _gen_synthetic_scores(n=20000)
    enc = OHLCVCompositeEncoder.for_sector("large_cap")  # 4 bins
    enc.fit(scores, method="normal")
    assert len(enc.breakpoints) == 3
    # Expected breakpoints for 4 bins (quartiles of N(0,1)):
    # Φ^-1(0.25) ≈ -0.674, Φ^-1(0.50) = 0, Φ^-1(0.75) ≈ +0.674
    assert abs(enc.breakpoints[0] - (-0.674)) < 0.02
    assert abs(enc.breakpoints[1] - 0.0) < 0.02
    assert abs(enc.breakpoints[2] - 0.674) < 0.02
    print("✓ test_fit_normal_method")


def test_fit_insufficient_samples():
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")  # bins=3, needs >= 30
    try:
        enc.fit([0.1, 0.2, 0.3])
        assert False, "Expected ValueError"
    except ValueError:
        pass
    print("✓ test_fit_insufficient_samples")


def test_quantize_boundary_behavior():
    """Score exactly at a breakpoint goes to the LOWER bin (<= comparison)."""
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")  # bins=3
    enc.breakpoints = [-0.5, 0.5]
    enc.fitted = True

    # Score well below bp[0] → 'a'
    assert enc.quantize(-10.0) == "a"
    # Score exactly at bp[0] → 'a' (boundary)
    assert enc.quantize(-0.5) == "a"
    # Score just above bp[0] → 'b'
    assert enc.quantize(-0.499) == "b"
    # Score well in middle → 'b'
    assert enc.quantize(0.0) == "b"
    # Score exactly at bp[1] → 'b'
    assert enc.quantize(0.5) == "b"
    # Score just above bp[1] → 'c'
    assert enc.quantize(0.501) == "c"
    # Score well above → 'c'
    assert enc.quantize(10.0) == "c"
    print("✓ test_quantize_boundary_behavior")


def test_quantize_nan_returns_middle():
    """NaN or inf composite score → middle bin (neutral)."""
    enc = OHLCVCompositeEncoder.for_sector("large_cap")  # bins=4, mid=2 → 'c'
    enc.breakpoints = [-0.5, 0.0, 0.5]
    enc.fitted = True

    assert enc.quantize(float("nan")) == "c"
    assert enc.quantize(float("inf")) == "c"
    assert enc.quantize(float("-inf")) == "c"
    print("✓ test_quantize_nan_returns_middle")


# ---------------------------------------------------------------------------
# 5. encode_sequence
# ---------------------------------------------------------------------------

def test_encode_sequence_length():
    """Encoded key length must equal seq_len."""
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")
    enc.breakpoints = [-0.5, 0.5]
    enc.fitted = True

    # Generate 20 fake candles (o,h,l,c,v,vma)
    candles = []
    for i in range(20):
        o = 100 + i
        c = 100 + i + (1 if i % 2 == 0 else -1)
        h = max(o, c) + 1
        l = min(o, c) - 1
        candles.append((o, h, l, c, 150, 100))

    key10 = enc.encode_sequence(candles, seq_len=10)
    assert len(key10) == 10
    assert all(ch in "abc" for ch in key10)

    key15 = enc.encode_sequence(candles, seq_len=15)
    assert len(key15) == 15
    print("✓ test_encode_sequence_length")


def test_encode_sequence_uses_last_n():
    """Sequence must use the most recent `seq_len` candles."""
    # Use old_meme sector which allows seq_len=5
    enc = OHLCVCompositeEncoder.for_sector("old_meme")
    enc.breakpoints = [-0.5, 0.0, 0.5, 1.0]  # 5 bins -> 4 breakpoints
    enc.fitted = True

    # 5 candles: first 3 doji, last 2 bullish (close > open)
    candles = [
        (100, 100, 100, 100, 100, 100),  # doji → composite 0.25 → 'c'
        (100, 100, 100, 100, 100, 100),  # doji
        (100, 100, 100, 100, 100, 100),  # doji
        (100, 110, 95, 110, 200, 100),   # bullish → 'd' or 'e'
        (100, 110, 95, 110, 200, 100),   # bullish → 'd' or 'e'
    ]
    # Encode last 5 candles (all 5)
    key5 = enc.encode_sequence(candles, seq_len=5)
    assert len(key5) == 5
    # The last 2 chars should be the same (both bullish)
    assert key5[-1] == key5[-2], f"Last 2 chars differ: {key5}"
    # The last 2 chars should be higher alphabetically than the first 3
    assert key5[-1] > key5[0], f"Bullish last should be higher bin: {key5}"
    print(f"✓ test_encode_sequence_uses_last_n (key5={key5!r})")


def test_encode_sequence_invalid_seq_len():
    """seq_len not allowed for sector must raise."""
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")  # allowed: [10, 15]
    enc.breakpoints = [-0.5, 0.5]
    enc.fitted = True
    candles = [(100, 110, 95, 100, 150, 100)] * 20

    try:
        enc.encode_sequence(candles, seq_len=5)
        assert False
    except ValueError:
        pass
    try:
        enc.encode_sequence(candles, seq_len=7)
        assert False
    except ValueError:
        pass
    print("✓ test_encode_sequence_invalid_seq_len")


def test_encode_sequence_insufficient_candles():
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")
    enc.breakpoints = [-0.5, 0.5]
    enc.fitted = True
    candles = [(100, 110, 95, 100, 150, 100)] * 5
    try:
        enc.encode_sequence(candles, seq_len=10)
        assert False
    except ValueError:
        pass
    print("✓ test_encode_sequence_insufficient_candles")


# ---------------------------------------------------------------------------
# 6. Anti-leakage: vol_ma20 closed='left'
# ---------------------------------------------------------------------------

def test_vol_ma20_closed_left():
    """vol_ma20 at index i must NOT include volume[i] (closed='left')."""
    volumes = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0,
               110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0, 190.0, 200.0,
               210.0, 220.0, 230.0, 240.0, 250.0]
    ma = compute_vol_ma20(volumes, window=20)
    assert len(ma) == len(volumes)
    # Warm-up: first 19 bars get fallback (1.0)
    for i in range(19):
        assert ma[i] == VOL_MA_WARMUP_FALLBACK, f"index {i} should be warmup"
    # At i=20: ma = mean(volumes[0:20]) = mean(10..200) = 105
    assert abs(ma[20] - 105.0) < 0.001, f"Expected 105.0 at i=20, got {ma[20]}"
    # CRITICAL: ma[20] does NOT include volumes[20]=210
    # If it did, mean would be (10+20+...+200+210)/21 = 115.0
    assert ma[20] != 115.0, "LEAKAGE: vol_ma[20] includes volumes[20]"
    # At i=21: ma = mean(volumes[1:21]) = mean(20..210) = 115
    assert abs(ma[21] - 115.0) < 0.001, f"Expected 115.0 at i=21, got {ma[21]}"
    print("✓ test_vol_ma20_closed_left")


# ---------------------------------------------------------------------------
# 7. Serialization
# ---------------------------------------------------------------------------

def test_serialization_roundtrip():
    """JSON round-trip preserves all fields."""
    scores = _gen_synthetic_scores(n=2000)
    enc = OHLCVCompositeEncoder.for_sector("old_meme")  # bins=5
    enc.fit(scores, method="percentile")
    assert enc.fitted

    d = enc.to_dict()
    # JSON-serializable
    json_str = json.dumps(d)
    d2 = json.loads(json_str)
    enc2 = OHLCVCompositeEncoder.from_dict(d2)

    assert enc2.sector == enc.sector
    assert enc2.bins == enc.bins
    assert enc2.fitted == enc.fitted
    assert enc2.train_count == enc.train_count
    assert abs(enc2.train_mean - enc.train_mean) < 1e-9
    assert abs(enc2.train_std - enc.train_std) < 1e-9
    assert enc2.breakpoints == enc.breakpoints
    assert enc2.weights == enc.weights

    # Quantization must produce identical results
    for s in [-2.0, -0.5, 0.0, 0.5, 2.0]:
        assert enc.quantize(s) == enc2.quantize(s)
    print("✓ test_serialization_roundtrip")


def test_to_json_file(tmp_path="/tmp"):
    """File persistence round-trip."""
    path = os.path.join(tmp_path, "test_encoder_v7.json")
    try:
        scores = _gen_synthetic_scores(n=2000)
        enc = OHLCVCompositeEncoder.for_sector("new_meme")
        enc.fit(scores)
        enc.to_json(path)

        enc2 = OHLCVCompositeEncoder.from_json(path)
        assert enc2.sector == "new_meme"
        assert enc2.fitted
        assert enc2.breakpoints == enc.breakpoints
        print("✓ test_to_json_file")
    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# 8. encode_series (batch)
# ---------------------------------------------------------------------------

def test_encode_series_batch():
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")
    enc.breakpoints = [-0.5, 0.5]
    enc.fitted = True

    opens = [100, 100, 100, 100]
    highs = [110, 105, 110, 105]
    lows =  [95,  95,  95,  95]
    closes= [110, 90,  110, 90]
    vols =  [200, 200, 200, 200]
    vmas =  [100, 100, 100, 100]

    syms = enc.encode_series(opens, highs, lows, closes, vols, vmas)
    assert len(syms) == 4
    assert all(isinstance(s, str) and len(s) == 1 for s in syms)
    # Bull candles should be higher bin than bear candles
    assert syms[0] >= syms[1], f"Expected bull >= bear, got {syms}"
    print("✓ test_encode_series_batch")


def test_encode_series_length_mismatch():
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")
    enc.breakpoints = [-0.5, 0.5]
    enc.fitted = True
    try:
        enc.encode_series([1,2,3], [1,2], [1,2,3], [1,2,3], [1,2,3], [1,2,3])
        assert False
    except ValueError:
        pass
    print("✓ test_encode_series_length_mismatch")


# ---------------------------------------------------------------------------
# 9. Real DB sanity check (optional — skipped if DB not available)
# ---------------------------------------------------------------------------

def test_real_db_sanity_check():
    """
    Fit encoder on real BTC 5m candles from ppmt.db.
    Verify:
    - Distribution is roughly balanced (each bin 25%-40% of data)
    - All symbols in 'a'..'c' (3 bins for blue_chip)
    - Sequence keys are strings of length 10 or 15
    """
    db_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ppmt.db")
    if not os.path.exists(db_path):
        print("⏭ test_real_db_sanity_check (skipped: ppmt.db not found)")
        return

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        # Load 5000 most recent BTC 5m candles
        c.execute(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM ohlcv_v6 WHERE symbol='BTCUSDT' AND timeframe='5m' "
            "ORDER BY timestamp DESC LIMIT 5000"
        )
        rows = c.fetchall()
        if len(rows) < 1000:
            print(f"⏭ test_real_db_sanity_check (skipped: only {len(rows)} rows)")
            return
        rows.reverse()  # oldest → newest

        timestamps = [r[0] for r in rows]
        opens = [r[1] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        closes = [r[4] for r in rows]
        vols = [r[5] for r in rows]
        vmas = compute_vol_ma20(vols, window=20)

        # Compute composite scores
        scores = [
            compute_composite_score(opens[i], highs[i], lows[i], closes[i],
                                     vols[i], vmas[i])
            for i in range(len(rows))
        ]

        # Fit blue_chip encoder
        enc = OHLCVCompositeEncoder.for_sector("blue_chip")
        enc.fit(scores[20:], method="percentile")  # skip warmup
        assert enc.fitted
        assert len(enc.breakpoints) == 2  # 3 bins → 2 breakpoints

        # Encode all candles
        syms = enc.encode_series(opens, highs, lows, closes, vols, vmas)
        assert len(syms) == len(rows)

        # Check distribution
        dist = enc.symbol_distribution(syms)
        assert len(dist) == 3, f"Expected 3 symbols, got {sorted(dist.keys())}"
        for sym, frac in dist.items():
            assert 0.20 < frac < 0.50, (
                f"Bin {sym} = {frac:.3f} (expected 0.20-0.50)"
            )

        # Check that all symbols are in 'a','b','c'
        assert all(s in "abc" for s in syms)

        # Encode a sequence
        candles = list(zip(opens, highs, lows, closes, vols, vmas))
        key10 = enc.encode_sequence(candles, seq_len=10)
        assert len(key10) == 10
        assert all(ch in "abc" for ch in key10)

        key15 = enc.encode_sequence(candles, seq_len=15)
        assert len(key15) == 15

        print(f"✓ test_real_db_sanity_check (BTC: dist={dist}, key10={key10!r})")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 10. Run all
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        test_symbol_to_sector_basic,
        test_symbol_to_sector_with_suffix,
        test_symbol_to_sector_unknown,
        test_composite_bullish_candle,
        test_composite_bearish_candle,
        test_composite_doji_candle,
        test_composite_vol_signal_clipping,
        test_composite_zero_range,
        test_composite_vol_ma_warmup,
        test_encoder_factory_for_sector,
        test_encoder_factory_for_symbol,
        test_encoder_unknown_sector,
        test_encoder_not_fitted_raises,
        test_fit_percentile_balanced,
        test_fit_normal_method,
        test_fit_insufficient_samples,
        test_quantize_boundary_behavior,
        test_quantize_nan_returns_middle,
        test_encode_sequence_length,
        test_encode_sequence_uses_last_n,
        test_encode_sequence_invalid_seq_len,
        test_encode_sequence_insufficient_candles,
        test_vol_ma20_closed_left,
        test_serialization_roundtrip,
        test_to_json_file,
        test_encode_series_batch,
        test_encode_series_length_mismatch,
        test_real_db_sanity_check,
    ]
    n_pass = 0
    n_fail = 0
    for t in tests:
        try:
            t()
            n_pass += 1
        except Exception as e:
            n_fail += 1
            print(f"✗ {t.__name__}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{'='*60}\nF2 tests: {n_pass} passed, {n_fail} failed (total {n_pass + n_fail})\n{'='*60}")
    return n_fail == 0


if __name__ == "__main__":
    import sys
    ok = run_all()
    sys.exit(0 if ok else 1)
