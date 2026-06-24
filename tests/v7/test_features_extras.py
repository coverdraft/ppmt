"""
Tests for v7_features_extras (F4).

Verifies:
- Sector one-hot encoding (4 binary + 1 categorical int)
- Day-of-week cyclical encoding (sin/cos)
- BinanceFundingFetcher: cache schema, fetch+cache, last_settled_rate (anti-leakage)
- BinanceOIFetcher: cache schema, fetch+cache, get_oi_at, compute_oi_change
- FeaturesExtrasExtractor: extract() returns all 12 features
- Funding z-score computation
- Anti-leakage: future funding rates never used
- Live API smoke test (skipped if no network)
"""

import sys
import os
import json
import math
import time
import sqlite3
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "v7"))

from v7_features_extras import (
    BinanceFundingFetcher,
    BinanceOIFetcher,
    FeaturesExtrasExtractor,
    encode_sector_one_hot,
    encode_day_of_week,
    get_feature_names,
    to_binance_symbol,
    SECTOR_INDEX,
    FUNDING_INTERVAL_SECONDS,
    FUNDING_Z_WINDOW,
)


# ---------------------------------------------------------------------------
# 0. Binance symbol mapping
# ---------------------------------------------------------------------------

def test_to_binance_symbol_low_priced():
    """Low-priced tokens get 1000x prefix."""
    assert to_binance_symbol("SHIBUSDT") == "1000SHIBUSDT"
    assert to_binance_symbol("PEPEUSDT") == "1000PEPEUSDT"
    assert to_binance_symbol("BONKUSDT") == "1000BONKUSDT"
    print("✓ test_to_binance_symbol_low_priced")


def test_to_binance_symbol_passthrough():
    """Normal tokens pass through unchanged."""
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "AVAXUSDT",
                "LINKUSDT", "XRPUSDT", "DOGEUSDT", "WIFUSDT"]:
        assert to_binance_symbol(sym) == sym
    print("✓ test_to_binance_symbol_passthrough")


# ---------------------------------------------------------------------------
# 1. Sector one-hot
# ---------------------------------------------------------------------------

def test_encode_sector_one_hot_all_sectors():
    cases = [
        ("BTC", "blue_chip"),
        ("BTCUSDT", "blue_chip"),
        ("ETH-USD", "blue_chip"),
        ("SOL", "large_cap"),
        ("ADAUSDT", "large_cap"),
        ("XRP", "old_meme"),
        ("DOGE", "old_meme"),
        ("PEPE", "new_meme"),
        ("WIFUSDT", "new_meme"),
    ]
    for symbol, expected_sector in cases:
        f = encode_sector_one_hot(symbol)
        assert f["sector_idx"] == SECTOR_INDEX[expected_sector]
        # Only one of the 4 binaries is 1.0
        binaries = [f["sector_blue_chip"], f["sector_large_cap"],
                    f["sector_old_meme"], f["sector_new_meme"]]
        assert sum(binaries) == 1.0, f"{symbol}: {binaries}"
        assert binaries[SECTOR_INDEX[expected_sector]] == 1.0
    print("✓ test_encode_sector_one_hot_all_sectors")


def test_encode_sector_one_hot_unknown():
    try:
        encode_sector_one_hot("LTC")
        assert False
    except ValueError:
        pass
    print("✓ test_encode_sector_one_hot_unknown")


# ---------------------------------------------------------------------------
# 2. Day-of-week cyclical encoding
# ---------------------------------------------------------------------------

def test_encode_day_of_week_returns_sin_cos():
    # Monday 2024-01-01 00:00:00 UTC (dow=0)
    ts_monday = 1704067200  # 2024-01-01 00:00:00 UTC
    f = encode_day_of_week(ts_monday)
    assert f["day_of_week"] == 0.0  # Monday
    # sin(0) = 0, cos(0) = 1
    assert abs(f["day_of_week_sin"] - 0.0) < 1e-9
    assert abs(f["day_of_week_cos"] - 1.0) < 1e-9
    print("✓ test_encode_day_of_week_returns_sin_cos")


def test_encode_day_of_week_sunday():
    # Sunday 2024-01-07 00:00:00 UTC (dow=6)
    ts_sunday = 1704585600  # 2024-01-07 00:00:00 UTC
    f = encode_day_of_week(ts_sunday)
    assert f["day_of_week"] == 6.0
    angle = 2 * math.pi * 6 / 7
    assert abs(f["day_of_week_sin"] - math.sin(angle)) < 1e-9
    assert abs(f["day_of_week_cos"] - math.cos(angle)) < 1e-9
    print("✓ test_encode_day_of_week_sunday")


def test_encode_day_of_week_range():
    """sin and cos must always be in [-1, 1]."""
    # Sample 7 days
    base_ts = 1704067200  # Monday
    for day_offset in range(7):
        ts = base_ts + day_offset * 86400
        f = encode_day_of_week(ts)
        assert -1.0 <= f["day_of_week_sin"] <= 1.0
        assert -1.0 <= f["day_of_week_cos"] <= 1.0
        assert 0 <= f["day_of_week"] <= 6
    print("✓ test_encode_day_of_week_range")


# ---------------------------------------------------------------------------
# 3. BinanceFundingFetcher
# ---------------------------------------------------------------------------

def _make_temp_cache_dir():
    return tempfile.mkdtemp(prefix="ppmt_v7_test_")


def test_funding_cache_creation():
    cache_dir = _make_temp_cache_dir()
    try:
        ff = BinanceFundingFetcher(cache_dir=cache_dir)
        assert os.path.exists(ff.cache_path)
        # Schema exists
        conn = sqlite3.connect(ff.cache_path)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        assert "funding_rates" in tables
        conn.close()
        print("✓ test_funding_cache_creation")
    finally:
        shutil.rmtree(cache_dir)


def test_funding_manual_insert_and_query():
    """Manually insert funding rates and verify queries."""
    cache_dir = _make_temp_cache_dir()
    try:
        ff = BinanceFundingFetcher(cache_dir=cache_dir)
        conn = ff._connect()
        # Insert 5 funding rates: t=1000, 2000, 3000, 4000, 5000 (seconds, *1000 for ms)
        rates = [(1000, 0.0001), (2000, 0.0002), (3000, 0.0003), (4000, 0.0004), (5000, 0.0005)]
        for ts_s, rate in rates:
            conn.execute("""
                INSERT INTO funding_rates (symbol, funding_time, funding_rate, mark_price, fetched_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("BTCUSDT", ts_s * 1000, rate, 60000.0, int(time.time())))
        conn.commit()
        conn.close()

        # Query: last settled at t=3500 → should return t=3000, rate=0.0003
        rate, ts = ff.get_last_settled_rate("BTCUSDT", 3500)
        assert abs(rate - 0.0003) < 1e-9, f"Expected 0.0003, got {rate}"
        assert abs(ts - 3000.0) < 1e-9, f"Expected 3000.0, got {ts}"

        # Query: t=1000 → should return t=1000 (boundary inclusive)
        rate, ts = ff.get_last_settled_rate("BTCUSDT", 1000)
        assert abs(rate - 0.0001) < 1e-9
        assert abs(ts - 1000.0) < 1e-9

        # Query: t=999 → no settled rate yet (returns 0.0)
        rate, ts = ff.get_last_settled_rate("BTCUSDT", 999)
        assert rate == 0.0
        assert ts == 0.0

        # Query: t=6000 (future) → returns t=5000
        rate, ts = ff.get_last_settled_rate("BTCUSDT", 6000)
        assert abs(rate - 0.0005) < 1e-9
        assert abs(ts - 5000.0) < 1e-9
        print("✓ test_funding_manual_insert_and_query")
    finally:
        shutil.rmtree(cache_dir)


def test_funding_anti_leakage():
    """
    CRITICAL: At ts=T, we must NOT see a funding rate that settles at ts>T.

    Binance funding settles at fixed times (00:00, 08:00, 16:00 UTC).
    A rate at fundingTime=1609507200000 (2021-01-01 12:00 UTC) is the rate
    that was settled AT 12:00. A query at ts=1609507199 (11:59:59) must NOT
    see it — that would be lookahead (the rate wasn't known yet).
    """
    cache_dir = _make_temp_cache_dir()
    try:
        ff = BinanceFundingFetcher(cache_dir=cache_dir)
        conn = ff._connect()
        # Insert a rate at funding_time=10000 ms (ts=10 seconds)
        conn.execute("""
            INSERT INTO funding_rates (symbol, funding_time, funding_rate, mark_price, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("BTCUSDT", 10000, 0.001, 60000.0, int(time.time())))
        conn.commit()
        conn.close()

        # Query at ts=5 seconds (BEFORE settle) → must return 0.0 (no settled rate)
        rate, ts = ff.get_last_settled_rate("BTCUSDT", 5)
        assert rate == 0.0, f"LEAKAGE: query at ts=5 returned rate {rate} settled at ts=10"
        assert ts == 0.0

        # Query at ts=10 (exactly at settle) → returns the rate
        rate, ts = ff.get_last_settled_rate("BTCUSDT", 10)
        assert abs(rate - 0.001) < 1e-9

        # Query at ts=15 (after settle) → returns the rate
        rate, ts = ff.get_last_settled_rate("BTCUSDT", 15)
        assert abs(rate - 0.001) < 1e-9
        print("✓ test_funding_anti_leakage")
    finally:
        shutil.rmtree(cache_dir)


def test_funding_z_score():
    """Compute z-score of current funding rate vs history."""
    cache_dir = _make_temp_cache_dir()
    try:
        ff = BinanceFundingFetcher(cache_dir=cache_dir)
        conn = ff._connect()
        # Insert 20 funding rates with mean=0.0002, std=0.0001
        # rate = 0.0002 + 0.0001 * [-1, 0, 1, 0, -1, 0, 1, 0, ...]
        # Use a simple pattern: rates 0.0001, 0.0002, 0.0003, alternating
        for i in range(20):
            ts_s = 1000 + i * 100
            rate = 0.0001 + (i % 3) * 0.0001  # 0.0001, 0.0002, 0.0003, 0.0001, ...
            conn.execute("""
                INSERT INTO funding_rates (symbol, funding_time, funding_rate, mark_price, fetched_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("BTCUSDT", ts_s * 1000, rate, 60000.0, int(time.time())))
        conn.commit()
        conn.close()

        # Current ts = 3000 (after last insert at ts_s=2900)
        # Last settled rate = at ts_s=2900, i=19 → rate = 0.0001 + (19%3)*0.0001 = 0.0001 + 0.0001 = 0.0002
        # History (last 90 rates, but we only have 20):
        # rates = [0.0001, 0.0002, 0.0003, 0.0001, 0.0002, 0.0003, ...] (20 of them)
        # mean = (0.0001*7 + 0.0002*7 + 0.0003*6) / 20 ≈ 0.0002
        # Let's just verify z-score is finite and not zero
        z = ff.compute_funding_z("BTCUSDT", 3000, window=90)
        assert math.isfinite(z), f"z must be finite, got {z}"
        # Should be small (current = mean)
        assert abs(z) < 1.0, f"Expected z near 0, got {z}"
        print(f"✓ test_funding_z_score (z={z:.4f})")
    finally:
        shutil.rmtree(cache_dir)


def test_funding_z_insufficient_history():
    """If < 10 historical rates, z-score should be 0.0."""
    cache_dir = _make_temp_cache_dir()
    try:
        ff = BinanceFundingFetcher(cache_dir=cache_dir)
        conn = ff._connect()
        # Insert only 3 rates
        for i in range(3):
            conn.execute("""
                INSERT INTO funding_rates (symbol, funding_time, funding_rate, mark_price, fetched_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("BTCUSDT", (1000 + i * 100) * 1000, 0.0001 * (i + 1), 60000.0, int(time.time())))
        conn.commit()
        conn.close()

        z = ff.compute_funding_z("BTCUSDT", 5000, window=90)
        assert z == 0.0, f"Expected 0.0 (insufficient history), got {z}"
        print("✓ test_funding_z_insufficient_history")
    finally:
        shutil.rmtree(cache_dir)


# ---------------------------------------------------------------------------
# 4. BinanceOIFetcher
# ---------------------------------------------------------------------------

def test_oi_cache_creation():
    cache_dir = _make_temp_cache_dir()
    try:
        of = BinanceOIFetcher(cache_dir=cache_dir)
        assert os.path.exists(of.cache_path)
        conn = sqlite3.connect(of.cache_path)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        assert "oi_history" in tables
        conn.close()
        print("✓ test_oi_cache_creation")
    finally:
        shutil.rmtree(cache_dir)


def test_oi_manual_insert_and_query():
    cache_dir = _make_temp_cache_dir()
    try:
        of = BinanceOIFetcher(cache_dir=cache_dir)
        conn = of._connect()
        # Insert OI snapshots: t=1000, 2000, 3000, 4000, 5000
        oi_values = [(1000, 100.0), (2000, 110.0), (3000, 105.0), (4000, 120.0), (5000, 130.0)]
        for ts_s, oi in oi_values:
            conn.execute("""
                INSERT INTO oi_history (symbol, timestamp, open_interest, open_interest_value, fetched_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("BTCUSDT", ts_s * 1000, oi, oi * 60000.0, int(time.time())))
        conn.commit()
        conn.close()

        # get_oi_at(t=3500) → returns t=3000, oi=105.0
        oi = of.get_oi_at("BTCUSDT", 3500)
        assert oi == 105.0, f"Expected 105.0, got {oi}"

        # get_oi_at(t=5000) → returns 130.0
        oi = of.get_oi_at("BTCUSDT", 5000)
        assert oi == 130.0

        # get_oi_at(t=500) → None (no data)
        oi = of.get_oi_at("BTCUSDT", 500)
        assert oi is None
        print("✓ test_oi_manual_insert_and_query")
    finally:
        shutil.rmtree(cache_dir)


def test_oi_change_1h():
    """Compute % change vs 1h ago (3600 seconds)."""
    cache_dir = _make_temp_cache_dir()
    try:
        of = BinanceOIFetcher(cache_dir=cache_dir)
        conn = of._connect()
        # Insert OI at t=0 and t=3600 (1h later)
        # OI grew from 100 to 110 → 10% change
        conn.execute("""
            INSERT INTO oi_history (symbol, timestamp, open_interest, open_interest_value, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("BTCUSDT", 0, 100.0, 6_000_000.0, int(time.time())))
        conn.execute("""
            INSERT INTO oi_history (symbol, timestamp, open_interest, open_interest_value, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("BTCUSDT", 3600 * 1000, 110.0, 6_600_000.0, int(time.time())))
        conn.commit()
        conn.close()

        # Query at t=3600: change vs 1h ago = (110-100)/100 * 100 = 10%
        change = of.compute_oi_change("BTCUSDT", 3600, lookback_seconds=3600)
        assert abs(change - 10.0) < 1e-6, f"Expected 10.0%, got {change}"
        print(f"✓ test_oi_change_1h (change={change:.4f}%)")
    finally:
        shutil.rmtree(cache_dir)


def test_oi_change_missing_data():
    """If either current or past OI is missing, return 0.0."""
    cache_dir = _make_temp_cache_dir()
    try:
        of = BinanceOIFetcher(cache_dir=cache_dir)
        # No data at all
        change = of.compute_oi_change("BTCUSDT", 3600, lookback_seconds=3600)
        assert change == 0.0
        print("✓ test_oi_change_missing_data")
    finally:
        shutil.rmtree(cache_dir)


def test_oi_anti_leakage():
    """OI at future timestamps must NOT be visible."""
    cache_dir = _make_temp_cache_dir()
    try:
        of = BinanceOIFetcher(cache_dir=cache_dir)
        conn = of._connect()
        # Insert OI at t=1000 only
        conn.execute("""
            INSERT INTO oi_history (symbol, timestamp, open_interest, open_interest_value, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("BTCUSDT", 1000 * 1000, 100.0, 6_000_000.0, int(time.time())))
        conn.commit()
        conn.close()

        # Query at t=500 (before insert) → None
        assert of.get_oi_at("BTCUSDT", 500) is None
        # Query at t=1000 → 100.0
        assert of.get_oi_at("BTCUSDT", 1000) == 100.0
        # Query at t=2000 → 100.0 (last known)
        assert of.get_oi_at("BTCUSDT", 2000) == 100.0
        print("✓ test_oi_anti_leakage")
    finally:
        shutil.rmtree(cache_dir)


# ---------------------------------------------------------------------------
# 5. FeaturesExtrasExtractor (combined)
# ---------------------------------------------------------------------------

def test_extractor_feature_names():
    extractor = FeaturesExtrasExtractor(cache_dir=_make_temp_cache_dir())
    names = extractor.FEATURE_NAMES
    expected = get_feature_names()
    assert names == expected
    assert len(names) == 12
    # Critical features present
    for f in ["funding_rate", "funding_rate_z", "oi_change_1h", "oi_change_4h",
              "sector_idx", "day_of_week_sin", "day_of_week_cos"]:
        assert f in names, f"Missing {f}"
    print(f"✓ test_extractor_feature_names ({len(names)} features)")


def test_extractor_extract_all_keys_present():
    cache_dir = _make_temp_cache_dir()
    try:
        extractor = FeaturesExtrasExtractor(cache_dir=cache_dir)
        # No funding/OI data cached → defaults to 0.0
        features = extractor.extract(symbol="BTCUSDT", ts_seconds=1704067200)
        # All 12 features present
        for name in get_feature_names():
            assert name in features, f"Missing {name}"
        # Funding features default to 0.0 when no data
        assert features["funding_rate"] == 0.0
        assert features["funding_rate_z"] == 0.0
        assert features["oi_change_1h"] == 0.0
        assert features["oi_change_4h"] == 0.0
        # Sector features work even without API data
        assert features["sector_idx"] == 0.0  # BTC = blue_chip
        assert features["sector_blue_chip"] == 1.0
        assert features["sector_large_cap"] == 0.0
        # Day-of-week features work
        assert -1.0 <= features["day_of_week_sin"] <= 1.0
        assert -1.0 <= features["day_of_week_cos"] <= 1.0
        print("✓ test_extractor_extract_all_keys_present")
    finally:
        shutil.rmtree(cache_dir)


def test_extractor_extract_with_cached_data():
    cache_dir = _make_temp_cache_dir()
    try:
        extractor = FeaturesExtrasExtractor(cache_dir=cache_dir)

        # Manually cache funding + OI data
        fconn = extractor.funding_fetcher._connect()
        # Insert 20 funding rates with mean=0.0002
        for i in range(20):
            ts_s = 1704067200 - (20 - i) * 8 * 3600  # 8h intervals, ending near ts=1704067200
            rate = 0.0002 + (i % 3 - 1) * 0.0001  # 0.0001, 0.0002, 0.0003
            fconn.execute("""
                INSERT INTO funding_rates (symbol, funding_time, funding_rate, mark_price, fetched_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("BTCUSDT", ts_s * 1000, rate, 60000.0, int(time.time())))
        fconn.commit()
        fconn.close()

        oconn = extractor.oi_fetcher._connect()
        # Insert OI snapshots at t and t-3600
        ts_now = 1704067200
        oconn.execute("""
            INSERT INTO oi_history (symbol, timestamp, open_interest, open_interest_value, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("BTCUSDT", (ts_now - 3600) * 1000, 100.0, 6_000_000.0, int(time.time())))
        oconn.execute("""
            INSERT INTO oi_history (symbol, timestamp, open_interest, open_interest_value, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("BTCUSDT", ts_now * 1000, 110.0, 6_600_000.0, int(time.time())))
        oconn.commit()
        oconn.close()

        # Extract features
        features = extractor.extract(symbol="BTCUSDT", ts_seconds=ts_now)
        # Funding rate should be the last settled rate
        assert features["funding_rate"] != 0.0, "Funding rate should be non-zero with cached data"
        # Z-score should be finite
        assert math.isfinite(features["funding_rate_z"])
        # OI change should be 10% (110 vs 100)
        assert abs(features["oi_change_1h"] - 10.0) < 1e-6, \
            f"Expected 10.0%, got {features['oi_change_1h']}"
        print(f"✓ test_extractor_extract_with_cached_data "
              f"(funding={features['funding_rate']:.6f}, z={features['funding_rate_z']:.3f}, "
              f"oi_1h={features['oi_change_1h']:.2f}%)")
    finally:
        shutil.rmtree(cache_dir)


def test_extractor_extract_batch():
    cache_dir = _make_temp_cache_dir()
    try:
        extractor = FeaturesExtrasExtractor(cache_dir=cache_dir)
        timestamps = [1704067200 + i * 86400 for i in range(7)]  # 7 days
        features_list = extractor.extract_batch("BTCUSDT", timestamps)
        assert len(features_list) == 7
        # Each should have all 12 features
        for f in features_list:
            for name in get_feature_names():
                assert name in f
        # Day-of-week should vary across 7 days
        dows = [f["day_of_week"] for f in features_list]
        assert len(set(dows)) == 7, f"Expected 7 unique dows, got {set(dows)}"
        print(f"✓ test_extractor_extract_batch (7 days, dows={dows})")
    finally:
        shutil.rmtree(cache_dir)


# ---------------------------------------------------------------------------
# 6. Live API smoke test (network required)
# ---------------------------------------------------------------------------

def test_live_api_funding_smoke():
    """
    Live test: fetch 5 funding rates from Binance for BTCUSDT.
    Skipped if no network or Binance API unavailable.
    """
    cache_dir = _make_temp_cache_dir()
    try:
        ff = BinanceFundingFetcher(cache_dir=cache_dir, timeout_seconds=10)
        try:
            # Fetch last 5 funding rates
            from v7_features_extras import BINANCE_FAPI_BASE
            import urllib.request
            url = f"{BINANCE_FAPI_BASE}/fapi/v1/fundingRate?symbol=BTCUSDT&limit=5"
            req = urllib.request.Request(url, headers={"User-Agent": "ppmt-v7-test"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            assert len(data) >= 1
            # All records have required fields
            for d in data:
                assert "fundingTime" in d
                assert "fundingRate" in d
            # Cache them
            n = ff.fetch_and_cache("BTCUSDT", max_pages=1)
            # fetch_and_cache uses default start/end (no params), so it
            # fetches the last 1000 records. We just verify it doesn't crash.
            assert n >= 1
            print(f"✓ test_live_api_funding_smoke (fetched {n} rates)")
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"⏭ test_live_api_funding_smoke (skipped: network/Binance unavailable: {e})")
            return
    finally:
        shutil.rmtree(cache_dir)


def test_live_api_oi_smoke():
    """Live test: fetch OI history from Binance."""
    cache_dir = _make_temp_cache_dir()
    try:
        of = BinanceOIFetcher(cache_dir=cache_dir, timeout_seconds=10)
        try:
            n = of.fetch_and_cache("BTCUSDT", max_pages=1)
            assert n >= 1
            # Verify we can query
            oi = of.get_oi_at("BTCUSDT", time.time())
            assert oi is not None
            print(f"✓ test_live_api_oi_smoke (fetched {n} OI snapshots, latest OI={oi:.2f})")
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"⏭ test_live_api_oi_smoke (skipped: network/Binance unavailable: {e})")
            return
    finally:
        shutil.rmtree(cache_dir)


# ---------------------------------------------------------------------------
# 7. Run all
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        test_to_binance_symbol_low_priced,
        test_to_binance_symbol_passthrough,
        test_encode_sector_one_hot_all_sectors,
        test_encode_sector_one_hot_unknown,
        test_encode_day_of_week_returns_sin_cos,
        test_encode_day_of_week_sunday,
        test_encode_day_of_week_range,
        test_funding_cache_creation,
        test_funding_manual_insert_and_query,
        test_funding_anti_leakage,
        test_funding_z_score,
        test_funding_z_insufficient_history,
        test_oi_cache_creation,
        test_oi_manual_insert_and_query,
        test_oi_change_1h,
        test_oi_change_missing_data,
        test_oi_anti_leakage,
        test_extractor_feature_names,
        test_extractor_extract_all_keys_present,
        test_extractor_extract_with_cached_data,
        test_extractor_extract_batch,
        test_live_api_funding_smoke,
        test_live_api_oi_smoke,
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
    print(f"\n{'='*60}\nF4 tests: {n_pass} passed, {n_fail} failed (total {n_pass + n_fail})\n{'='*60}")
    return n_fail == 0


if __name__ == "__main__":
    import sys
    ok = run_all()
    sys.exit(0 if ok else 1)
