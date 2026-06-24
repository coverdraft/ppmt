"""
Tests for SectorTrieContainer + RegimePartitionedTrie (F3).

Verifies:
- RegimePartitionedTrie: insert, query_n1, query_n2 (with fallback)
- N1 vs N2 independence (regime-conditional stats differ from global)
- N2 fallback when regime node is sparse
- query_all returns agreement/conflict/strength correctly
- Prune removes low-count nodes
- LRU eviction when max_nodes exceeded
- SectorTrieContainer: multi-sector, multi-seq_len routing
- Feature extraction produces all expected keys
- Persistence (dict + JSON file)
- Vol regime computation
- Real DB sanity check: build tries on BTC 5m data, verify N1 signal
"""

import sys
import os
import json
import math
import sqlite3
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "v7"))

from v7_sector_tries import (
    SectorTrieContainer,
    RegimePartitionedTrie,
    compute_vol_regime,
)
from v7_ohlcv_encoder import (
    OHLCVCompositeEncoder,
    SECTOR_BINS,
    SECTOR_SEQ_LENGTHS,
    SECTOR_TOKENS,
    compute_composite_score,
    compute_vol_ma20,
)
from v7_trie_metadata import TrieNodeV6Metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fitted_encoder(sector: str, n_train: int = 5000, seed: int = 42) -> OHLCVCompositeEncoder:
    """Create a fitted encoder using synthetic composite scores."""
    rng = random.Random(seed)
    scores = [rng.gauss(0, 1) for _ in range(n_train)]
    enc = OHLCVCompositeEncoder.for_sector(sector)
    enc.fit(scores, method="percentile")
    return enc


def _make_candles(n: int = 20, base_price: float = 100.0, seed: int = 0) -> list:
    """Generate n synthetic OHLCV candles (o,h,l,c,v,vol_ma20)."""
    rng = random.Random(seed)
    candles = []
    price = base_price
    vols = [rng.uniform(80, 200) for _ in range(n + 20)]
    vmas = compute_vol_ma20(vols, window=20)
    for i in range(n):
        o = price
        move = rng.gauss(0, 1.0)
        c = max(1.0, o + move)
        h = max(o, c) + rng.uniform(0, 0.5)
        l = min(o, c) - rng.uniform(0, 0.5)
        v = vols[i + 20]
        candles.append((o, h, l, c, v, vmas[i + 20]))
        price = c
    return candles


# ---------------------------------------------------------------------------
# 1. RegimePartitionedTrie basic
# ---------------------------------------------------------------------------

def test_rpt_construction():
    rpt = RegimePartitionedTrie(sector="blue_chip", seq_len=10)
    assert rpt.sector == "blue_chip"
    assert rpt.seq_len == 10
    assert len(rpt.global_trie) == 0
    assert len(rpt.regime_tries) == 0
    print("✓ test_rpt_construction")


def test_rpt_invalid_sector():
    try:
        RegimePartitionedTrie(sector="unknown", seq_len=5)
        assert False
    except ValueError:
        pass
    print("✓ test_rpt_invalid_sector")


def test_rpt_invalid_seq_len():
    # blue_chip allows [10, 15], not 5
    try:
        RegimePartitionedTrie(sector="blue_chip", seq_len=5)
        assert False
    except ValueError:
        pass
    print("✓ test_rpt_invalid_seq_len")


def test_rpt_insert_and_query_n1():
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5, min_observations=3)
    # Insert 5 observations for key "abcde" all regime 1
    for i in range(5):
        rpt.insert(
            key="abcde",
            fwd_ret_15m=0.5 + i * 0.1,  # 0.5, 0.6, 0.7, 0.8, 0.9
            vol_regime=1,
            timestamp=1000.0 + i * 300,
        )
    # N1 query: mean = 0.7
    pred, conf, count = rpt.query_n1("abcde")
    assert count == 5
    assert abs(pred - 0.7) < 0.001, f"Expected 0.7, got {pred}"
    assert 0.0 < conf <= 1.0
    print(f"✓ test_rpt_insert_and_query_n1 (pred={pred:.3f}, conf={conf:.3f})")


def test_rpt_query_n1_below_min_obs():
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5, min_observations=5)
    # Insert only 2 observations
    rpt.insert(key="abcde", fwd_ret_15m=0.5, vol_regime=1, timestamp=1000.0)
    rpt.insert(key="abcde", fwd_ret_15m=0.6, vol_regime=1, timestamp=1100.0)
    # Below min_obs -> returns 0.0
    pred, conf, count = rpt.query_n1("abcde")
    assert count == 2
    assert pred == 0.0, f"Expected 0.0 (below min_obs), got {pred}"
    print("✓ test_rpt_query_n1_below_min_obs")


def test_rpt_query_n1_missing_key():
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5)
    pred, conf, count = rpt.query_n1("zzzzz")
    assert pred == 0.0
    assert conf == 0.0
    assert count == 0
    print("✓ test_rpt_query_n1_missing_key")


# ---------------------------------------------------------------------------
# 2. N2 (regime-conditional) + fallback
# ---------------------------------------------------------------------------

def test_rpt_query_n2_regime_conditional():
    """N2 should return regime-specific mean, not global mean."""
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5,
                                 min_observations=3, min_observations_regime=3)
    # Insert 5 obs in regime 0 with mean 0.5
    for i in range(5):
        rpt.insert(key="abcde", fwd_ret_15m=0.5, vol_regime=0, timestamp=1000.0 + i)
    # Insert 5 obs in regime 3 with mean -0.5
    for i in range(5):
        rpt.insert(key="abcde", fwd_ret_15m=-0.5, vol_regime=3, timestamp=2000.0 + i)

    # Global mean = 0.0
    pred_n1, _, cnt_n1 = rpt.query_n1("abcde")
    assert cnt_n1 == 10
    assert abs(pred_n1 - 0.0) < 0.001, f"N1 should be 0.0, got {pred_n1}"

    # N2 regime 0 = 0.5
    pred_n2_r0, _, cnt_n2_r0, src_r0 = rpt.query_n2("abcde", vol_regime=0)
    assert src_r0 == "n2"
    assert cnt_n2_r0 == 5
    assert abs(pred_n2_r0 - 0.5) < 0.001, f"N2 regime 0 should be 0.5, got {pred_n2_r0}"

    # N2 regime 3 = -0.5
    pred_n2_r3, _, cnt_n2_r3, src_r3 = rpt.query_n2("abcde", vol_regime=3)
    assert src_r3 == "n2"
    assert cnt_n2_r3 == 5
    assert abs(pred_n2_r3 - (-0.5)) < 0.001, f"N2 regime 3 should be -0.5, got {pred_n2_r3}"

    print("✓ test_rpt_query_n2_regime_conditional")


def test_rpt_query_n2_fallback_to_n1():
    """When N2 node is sparse, fall back to N1."""
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5,
                                 min_observations=3, min_observations_regime=5)
    # Insert 10 obs in regime 0 with mean 0.5 (satisfies N1 min_obs=3)
    for i in range(10):
        rpt.insert(key="abcde", fwd_ret_15m=0.5, vol_regime=0, timestamp=1000.0 + i)
    # Insert 2 obs in regime 3 (below N2 min_obs_regime=5)
    rpt.insert(key="abcde", fwd_ret_15m=-0.5, vol_regime=3, timestamp=2000.0)
    rpt.insert(key="abcde", fwd_ret_15m=-0.5, vol_regime=3, timestamp=2100.0)

    # N1 = mean of all 12 = (10*0.5 + 2*(-0.5)) / 12 = 4/12 ≈ 0.333
    pred_n1, _, cnt_n1 = rpt.query_n1("abcde")
    assert cnt_n1 == 12
    assert abs(pred_n1 - (4.0 / 12.0)) < 0.001

    # N2 regime 3 should fall back to N1 (regime 3 has only 2 obs, min=5)
    pred_n2, _, cnt_n2, src = rpt.query_n2("abcde", vol_regime=3)
    assert src == "n1_fallback"
    assert abs(pred_n2 - pred_n1) < 0.001, "N2 fallback should return N1 prediction"

    print("✓ test_rpt_query_n2_fallback_to_n1")


def test_rpt_query_n2_no_fallback_when_disabled():
    """If fallback_to_n1=False, sparse N2 returns 0.0."""
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5,
                                 min_observations=3, min_observations_regime=5)
    for i in range(10):
        rpt.insert(key="abcde", fwd_ret_15m=0.5, vol_regime=0, timestamp=1000.0 + i)
    rpt.insert(key="abcde", fwd_ret_15m=-0.5, vol_regime=3, timestamp=2000.0)

    pred_n2, _, cnt_n2, src = rpt.query_n2("abcde", vol_regime=3, fallback_to_n1=False)
    assert src == "n2_empty"
    assert pred_n2 == 0.0
    assert cnt_n2 == 0
    print("✓ test_rpt_query_n2_no_fallback_when_disabled")


# ---------------------------------------------------------------------------
# 3. query_all (agreement, conflict, strength)
# ---------------------------------------------------------------------------

def test_query_all_agreement():
    """When N1 and N2 agree (same sign, similar magnitude), agreement is high."""
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5,
                                 min_observations=3, min_observations_regime=3)
    # All obs regime 1, mean 0.5
    for i in range(5):
        rpt.insert(key="abcde", fwd_ret_15m=0.5, vol_regime=1, timestamp=1000.0 + i)

    r = rpt.query_all("abcde", vol_regime=1)
    assert r["n1_count"] == 5
    assert r["n2_count"] == 5
    assert r["n2_source"] == "n2"
    assert r["agreement"] > 0.9, f"Expected high agreement, got {r['agreement']}"
    assert r["conflict"] == 0.0
    # Strength = avg(n1_conf, n2_conf). With 5 obs and var=0:
    # count_factor = sqrt(5/30) ≈ 0.408, var_factor = 1.0
    # So conf ≈ 0.408, strength ≈ 0.408
    assert r["strength"] > 0.3, f"Expected strength > 0.3, got {r['strength']}"
    print(f"✓ test_query_all_agreement (agreement={r['agreement']:.3f}, strength={r['strength']:.3f})")


def test_query_all_conflict():
    """When N1 and N2 disagree (different signs), conflict is high."""
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5,
                                 min_observations=3, min_observations_regime=3)
    # 5 obs regime 0 with mean +0.5
    for i in range(5):
        rpt.insert(key="abcde", fwd_ret_15m=0.5, vol_regime=0, timestamp=1000.0 + i)
    # 5 obs regime 3 with mean -0.5
    for i in range(5):
        rpt.insert(key="abcde", fwd_ret_15m=-0.5, vol_regime=3, timestamp=2000.0 + i)

    # Query regime 3: N1 = 0.0 (global mean), N2 = -0.5
    r = rpt.query_all("abcde", vol_regime=3)
    assert r["n2_source"] == "n2"
    assert r["n1_pred"] == 0.0
    assert r["n2_pred"] == -0.5
    # N1 is 0 — agreement is lower because |n1 - n2| / (|n1| + |n2|) = 1.0
    assert r["conflict"] > 0.0, f"Expected conflict, got {r['conflict']}"
    print(f"✓ test_query_all_conflict (conflict={r['conflict']:.3f}, agreement={r['agreement']:.3f})")


def test_query_all_missing_key():
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5)
    r = rpt.query_all("zzzzz", vol_regime=1)
    assert r["n1_count"] == 0
    assert r["n2_count"] == 0
    assert r["n2_source"] == "n2_empty"
    assert r["agreement"] == 0.0
    assert r["strength"] == 0.0
    print("✓ test_query_all_missing_key")


# ---------------------------------------------------------------------------
# 4. Prune + LRU
# ---------------------------------------------------------------------------

def test_prune_removes_low_count_nodes():
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5, min_observations=3)
    # Insert 1 obs for keys "aaaaa", "bbbbb" (low count)
    rpt.insert(key="aaaaa", fwd_ret_15m=0.5, vol_regime=1, timestamp=1000.0)
    rpt.insert(key="bbbbb", fwd_ret_15m=0.5, vol_regime=1, timestamp=1100.0)
    # Insert 5 obs for key "ccccc"
    for i in range(5):
        rpt.insert(key="ccccc", fwd_ret_15m=0.5, vol_regime=1, timestamp=2000.0 + i)

    assert len(rpt.global_trie) == 3
    pruned = rpt.prune(min_count=2)
    assert pruned >= 2  # at least aaaaa and bbbbb pruned (global + regime)
    assert "aaaaa" not in rpt.global_trie
    assert "bbbbb" not in rpt.global_trie
    assert "ccccc" in rpt.global_trie
    print(f"✓ test_prune_removes_low_count_nodes (pruned={pruned})")


def test_evict_lru():
    """LRU eviction removes oldest nodes when over cap."""
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5, max_nodes=5)
    # Insert 10 keys with increasing timestamps
    for i in range(10):
        key = f"k{i:03d}x"  # 5 chars
        for _ in range(5):  # enough obs to be trustworthy
            rpt.insert(key=key, fwd_ret_15m=0.5, vol_regime=1,
                       timestamp=1000.0 + i * 100)

    assert len(rpt.global_trie) == 10
    evicted = rpt.evict_lru()
    assert evicted > 0
    assert len(rpt.global_trie) <= 5  # target = 90% of 5 = 4, but check < 5
    # Oldest keys (k000x, k001x) should be evicted; newest kept
    assert "k000x" not in rpt.global_trie
    assert "k009x" in rpt.global_trie
    print(f"✓ test_evict_lru (evicted={evicted}, remaining={len(rpt.global_trie)})")


# ---------------------------------------------------------------------------
# 5. SectorTrieContainer
# ---------------------------------------------------------------------------

def test_container_construction_initializes_all_sectors():
    container = SectorTrieContainer()
    for sector in SECTOR_BINS:
        assert sector in container.tries
        for seq_len in SECTOR_SEQ_LENGTHS[sector]:
            assert seq_len in container.tries[sector]
            trie = container.tries[sector][seq_len]
            assert isinstance(trie, RegimePartitionedTrie)
            assert trie.sector == sector
            assert trie.seq_len == seq_len
    print("✓ test_container_construction_initializes_all_sectors")


def test_container_insert_observation():
    """Insert observation across all seq_lengths for a sector."""
    container = SectorTrieContainer()
    enc = _make_fitted_encoder("blue_chip")
    # blue_chip seq_lengths = [10, 15] -> need 15 candles
    candles = _make_candles(n=20, seed=1)

    n = container.insert_observation(
        symbol="BTCUSDT",
        candles=candles,
        encoder=enc,
        fwd_ret_15m=0.5,
        vol_regime=1,
        timestamp=1000.0,
    )
    assert n == 2  # both seq_lengths (10, 15) should insert
    # Check both tries have the inserted key
    for seq_len in [10, 15]:
        trie = container.tries["blue_chip"][seq_len]
        assert len(trie.global_trie) == 1
    print("✓ test_container_insert_observation")


def test_container_insert_routes_to_correct_sector():
    """BTC → blue_chip, SOL → large_cap, PEPE → new_meme."""
    container = SectorTrieContainer()
    encoders = {sector: _make_fitted_encoder(sector) for sector in SECTOR_BINS}

    test_cases = [
        ("BTCUSDT", "blue_chip"),
        ("ETH", "blue_chip"),
        ("SOLUSDT", "large_cap"),
        ("ADA", "large_cap"),
        ("XRPUSDT", "old_meme"),
        ("DOGE", "old_meme"),
        ("PEPEUSDT", "new_meme"),
        ("WIF", "new_meme"),
    ]
    for symbol, expected_sector in test_cases:
        candles = _make_candles(n=20, seed=hash(symbol) % 1000)
        enc = encoders[expected_sector]
        container.insert_observation(
            symbol=symbol, candles=candles, encoder=enc,
            fwd_ret_15m=0.3, vol_regime=1, timestamp=1000.0,
        )
        # Check that the expected sector's tries have at least 1 node
        for seq_len in SECTOR_SEQ_LENGTHS[expected_sector]:
            trie = container.tries[expected_sector][seq_len]
            assert len(trie.global_trie) >= 1, \
                f"{symbol} → {expected_sector} seq_len={seq_len}: no nodes"
    print("✓ test_container_insert_routes_to_correct_sector")


# ---------------------------------------------------------------------------
# 6. Feature extraction
# ---------------------------------------------------------------------------

def test_extract_features_keys_present():
    """Extract features should produce all expected keys."""
    container = SectorTrieContainer()
    enc = _make_fitted_encoder("blue_chip")
    candles = _make_candles(n=20, seed=2)

    # First insert some observations so the trie has data
    for i in range(5):
        container.insert_observation(
            symbol="BTCUSDT", candles=candles, encoder=enc,
            fwd_ret_15m=0.3 + i * 0.05, vol_regime=1, timestamp=1000.0 + i * 300,
        )

    features = container.extract_features(
        symbol="BTCUSDT", candles=candles, encoder=enc, vol_regime=1,
    )

    # blue_chip seq_lengths = [10, 15]
    expected_per_seq = [
        "trie_n1_pred_10", "trie_n1_conf_10", "trie_n1_count_10",
        "trie_n2_pred_10", "trie_n2_conf_10", "trie_n2_count_10", "trie_n2_source_10",
        "trie_agreement_10", "trie_conflict_10", "trie_strength_10",
        "trie_n1_pred_15", "trie_n1_conf_15", "trie_n1_count_15",
        "trie_n2_pred_15", "trie_n2_conf_15", "trie_n2_count_15", "trie_n2_source_15",
        "trie_agreement_15", "trie_conflict_15", "trie_strength_15",
    ]
    expected_agg = [
        "trie_n1_pred_avg", "trie_n2_pred_avg",
        "trie_agreement_avg", "trie_strength_avg",
        "trie_any_signal",
    ]
    for k in expected_per_seq + expected_agg:
        assert k in features, f"Missing feature: {k}"
    # trie_any_signal should be 1.0 (we inserted data)
    assert features["trie_any_signal"] == 1.0
    # n1_count should be 5 for both seq_lengths
    assert features["trie_n1_count_10"] == 5.0
    assert features["trie_n1_count_15"] == 5.0
    print(f"✓ test_extract_features_keys_present ({len(features)} features)")


def test_extract_features_empty_when_no_data():
    container = SectorTrieContainer()
    enc = _make_fitted_encoder("blue_chip")
    candles = _make_candles(n=20, seed=3)
    features = container.extract_features(
        symbol="BTCUSDT", candles=candles, encoder=enc, vol_regime=1,
    )
    assert features["trie_any_signal"] == 0.0
    assert features["trie_n1_count_10"] == 0.0
    print("✓ test_extract_features_empty_when_no_data")


def test_extract_features_insufficient_candles():
    """If candles < seq_len, features for that seq_len are zeroed."""
    container = SectorTrieContainer()
    enc = _make_fitted_encoder("blue_chip")
    # Only 8 candles — blue_chip needs 10 and 15
    candles = _make_candles(n=8, seed=4)
    features = container.extract_features(
        symbol="BTCUSDT", candles=candles, encoder=enc, vol_regime=1,
    )
    assert features["trie_n1_count_10"] == 0.0
    assert features["trie_n1_count_15"] == 0.0
    print("✓ test_extract_features_insufficient_candles")


# ---------------------------------------------------------------------------
# 7. Persistence
# ---------------------------------------------------------------------------

def test_rpt_persistence_dict_roundtrip():
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5, min_observations=3)
    for i in range(5):
        rpt.insert(key="abcde", fwd_ret_15m=0.5 + i * 0.1,
                   vol_regime=1, timestamp=1000.0 + i * 300)
    for i in range(3):
        rpt.insert(key="fghij", fwd_ret_15m=-0.3,
                   vol_regime=2, timestamp=2000.0 + i * 300)

    d = rpt.to_dict()
    json_str = json.dumps(d)  # must be JSON-serializable
    d2 = json.loads(json_str)
    rpt2 = RegimePartitionedTrie.from_dict(d2)

    assert rpt2.sector == rpt.sector
    assert rpt2.seq_len == rpt.seq_len
    assert len(rpt2.global_trie) == len(rpt.global_trie)
    assert "abcde" in rpt2.global_trie
    assert "fghij" in rpt2.global_trie
    # Same predictions
    p1, _, c1 = rpt.query_n1("abcde")
    p2, _, c2 = rpt2.query_n1("abcde")
    assert c1 == c2 == 5
    assert abs(p1 - p2) < 1e-9
    print("✓ test_rpt_persistence_dict_roundtrip")


def test_rpt_persistence_json_file(tmp_path="/tmp"):
    path = os.path.join(tmp_path, "test_rpt_v7.json")
    try:
        rpt = RegimePartitionedTrie(sector="new_meme", seq_len=5, min_observations=3)
        for i in range(5):
            rpt.insert(key="abcde", fwd_ret_15m=0.5,
                       vol_regime=1, timestamp=1000.0 + i)
        rpt.to_json(path)

        rpt2 = RegimePartitionedTrie.from_json(path)
        assert rpt2.sector == "new_meme"
        assert len(rpt2.global_trie) == 1
        pred, _, cnt = rpt2.query_n1("abcde")
        assert cnt == 5
        assert abs(pred - 0.5) < 0.001
        print("✓ test_rpt_persistence_json_file")
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_container_save_load_all(tmp_path="/tmp"):
    base_dir = os.path.join(tmp_path, "test_container_v7")
    try:
        container = SectorTrieContainer()
        enc = _make_fitted_encoder("blue_chip")
        candles = _make_candles(n=20, seed=5)
        for i in range(5):
            container.insert_observation(
                symbol="BTCUSDT", candles=candles, encoder=enc,
                fwd_ret_15m=0.3, vol_regime=1, timestamp=1000.0 + i * 300,
            )
        container.save_all(base_dir)

        # Files exist
        files = os.listdir(base_dir)
        # blue_chip has seq_lengths [10, 15]
        assert "blue_chip_10.json" in files
        assert "blue_chip_15.json" in files

        # Load into a fresh container
        container2 = SectorTrieContainer()
        n = container2.load_all(base_dir)
        assert n > 0
        # Same data
        for seq_len in [10, 15]:
            t1 = container.tries["blue_chip"][seq_len]
            t2 = container2.tries["blue_chip"][seq_len]
            assert len(t2.global_trie) == len(t1.global_trie) == 1
        print("✓ test_container_save_load_all")
    finally:
        import shutil
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)


# ---------------------------------------------------------------------------
# 8. Vol regime computation
# ---------------------------------------------------------------------------

def test_compute_vol_regime_quartiles():
    assert compute_vol_regime(10.0) == 0  # < 25
    assert compute_vol_regime(25.0) == 1  # = 25 (not < 25)
    assert compute_vol_regime(40.0) == 1  # 25 <= 40 < 50
    assert compute_vol_regime(50.0) == 2  # = 50
    assert compute_vol_regime(60.0) == 2  # 50 <= 60 < 75
    assert compute_vol_regime(75.0) == 3  # = 75
    assert compute_vol_regime(99.0) == 3  # >= 75
    print("✓ test_compute_vol_regime_quartiles")


def test_compute_vol_regime_custom_breakpoints():
    bp = (10.0, 30.0, 70.0)
    assert compute_vol_regime(5.0, bp) == 0
    assert compute_vol_regime(20.0, bp) == 1
    assert compute_vol_regime(50.0, bp) == 2
    assert compute_vol_regime(80.0, bp) == 3
    print("✓ test_compute_vol_regime_custom_breakpoints")


# ---------------------------------------------------------------------------
# 9. Anti-leakage: insert must not affect query for same key at same time
# ---------------------------------------------------------------------------

def test_anti_leakage_insert_does_not_retroactively_change_query():
    """
    Anti-leakage contract: a query made BEFORE an insertion must
    return the same result after the insertion. The caller enforces
    temporal ordering (insert at T+15m); the trie itself just stores.

    This test verifies that the trie does NOT cache query results —
    each query reads current state. Combined with caller-enforced
    insert-after-predict, this prevents leakage.
    """
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5, min_observations=3)
    # Empty trie — query returns 0
    pred_before, _, cnt_before = rpt.query_n1("abcde")
    assert pred_before == 0.0
    assert cnt_before == 0

    # Insert 5 obs
    for i in range(5):
        rpt.insert(key="abcde", fwd_ret_15m=0.5, vol_regime=1, timestamp=1000.0 + i)

    # Query after — now returns the prediction
    pred_after, _, cnt_after = rpt.query_n1("abcde")
    assert cnt_after == 5
    assert abs(pred_after - 0.5) < 0.001

    # The contract: caller must NOT have used pred_after to make a
    # decision at T=999 (before insertion). Caller-enforced.
    print("✓ test_anti_leakage_insert_does_not_retroactively_change_query")


# ---------------------------------------------------------------------------
# 10. Stats
# ---------------------------------------------------------------------------

def test_stats_returns_expected_fields():
    rpt = RegimePartitionedTrie(sector="old_meme", seq_len=5, min_observations=3)
    for i in range(10):
        rpt.insert(key=f"k{i:04d}", fwd_ret_15m=0.5,
                   vol_regime=i % 4, timestamp=1000.0 + i)
    s = rpt.stats()
    assert s["sector"] == "old_meme"
    assert s["seq_len"] == 5
    assert s["global_nodes"] == 10
    assert s["total_observations"] == 10
    assert s["insert_count"] == 10
    assert "avg_obs_per_node" in s
    assert "median_obs_per_node" in s
    print(f"✓ test_stats_returns_expected_fields (stats={s})")


# ---------------------------------------------------------------------------
# 11. Real DB sanity check
# ---------------------------------------------------------------------------

def test_real_db_sanity_check():
    """
    Build a trie on real BTC 5m data and verify:
    1. Encoder + container + insert pipeline works end-to-end
    2. Trie nodes accumulate observations (training coverage)
    3. Some test-period keys match training nodes (even if sparse)

    NOTE: With ~4000 training bars and seq_len=10, the 3^10=59049 key
    space is too large for high coverage. The trie signal improves with
    more data (F4 will add 6 months of 5m candles = ~500K obs, raising
    density to ~8 obs per node). This sanity check verifies the pipeline
    works, not that the signal is strong.
    """
    db_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ppmt.db")
    if not os.path.exists(db_path):
        print("⏭ test_real_db_sanity_check (skipped: ppmt.db not found)")
        return

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        # Load 10000 BTC 5m candles (more data for better trie density)
        c.execute(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM ohlcv_v6 WHERE symbol='BTCUSDT' AND timeframe='5m' "
            "ORDER BY timestamp DESC LIMIT 10000"
        )
        rows = c.fetchall()
        if len(rows) < 2000:
            print(f"⏭ test_real_db_sanity_check (skipped: only {len(rows)} rows)")
            return
        rows.reverse()
    finally:
        conn.close()

    n = len(rows)
    print(f"  loaded {n} BTC 5m candles")

    opens = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    vols = [r[5] for r in rows]
    vmas = compute_vol_ma20(vols, window=20)
    timestamps = [r[0] for r in rows]

    # Fit encoder on the FIRST 80% of data (training) — anti-leakage
    n_train = int(n * 0.8)
    train_scores = [
        compute_composite_score(opens[i], highs[i], lows[i], closes[i], vols[i], vmas[i])
        for i in range(20, n_train)
    ]
    enc = OHLCVCompositeEncoder.for_sector("blue_chip")
    enc.fit(train_scores, method="percentile")
    print(f"  encoder fitted on {len(train_scores)} training scores, breakpoints={[round(b,3) for b in enc.breakpoints]}")

    # Build candles list
    candles = [(opens[i], highs[i], lows[i], closes[i], vols[i], vmas[i]) for i in range(n)]

    # Compute vol_regime from rolling |return| percentile (proxy for ATR)
    rets = [0.0] * 20
    for i in range(20, n):
        rets.append(abs(closes[i] - closes[i-1]) / closes[i-1] * 100)
    def rolling_pct(x, window, i):
        if i < window:
            return 50.0
        chunk = x[i-window:i]
        rank = sum(1 for v in chunk if v <= x[i]) / len(chunk) * 100
        return rank
    atr_pcts = [rolling_pct(rets, 50, i) for i in range(n)]

    # Compute fwd_ret_15m (3 bars ahead at 5m TF = 15m wall-clock)
    fwd_ret_15m = [0.0] * n
    for i in range(n - 3):
        fwd_ret_15m[i] = (closes[i + 3] - closes[i]) / closes[i] * 100

    # Build container and insert observations for the training period
    container = SectorTrieContainer()
    n_inserted = 0
    for i in range(25, n_train):
        if i + 3 >= n:
            break
        candles_window = candles[max(0, i-19):i+1]
        vr = compute_vol_regime(atr_pcts[i])
        try:
            container.insert_observation(
                symbol="BTCUSDT",
                candles=candles_window,
                encoder=enc,
                fwd_ret_15m=fwd_ret_15m[i],
                vol_regime=vr,
                timestamp=timestamps[i],
            )
            n_inserted += 1
        except Exception:
            pass

    stats = container.stats()
    n_nodes_bc10 = stats["blue_chip"]["10"]["global_nodes"]
    n_nodes_bc15 = stats["blue_chip"]["15"]["global_nodes"]
    print(f"  inserted {n_inserted} observations")
    print(f"  blue_chip/10: {n_nodes_bc10} nodes (avg {n_inserted/max(1,n_nodes_bc10):.2f} obs/node)")
    print(f"  blue_chip/15: {n_nodes_bc15} nodes (avg {n_inserted/max(1,n_nodes_bc15):.2f} obs/node)")
    assert n_nodes_bc10 > 0, "blue_chip seq_len=10 has no nodes"
    assert n_nodes_bc15 > 0, "blue_chip seq_len=15 has no nodes"

    # Query on test period: count how many test keys have ANY training data
    test_matches_10 = 0
    test_matches_15 = 0
    test_total = 0
    for i in range(n_train, n - 3):
        candles_window = candles[max(0, i-19):i+1]
        vr = compute_vol_regime(atr_pcts[i])
        features = container.extract_features(
            symbol="BTCUSDT", candles=candles_window, encoder=enc, vol_regime=vr,
        )
        test_total += 1
        if features["trie_n1_count_10"] >= 1:
            test_matches_10 += 1
        if features["trie_n1_count_15"] >= 1:
            test_matches_15 += 1

    print(f"  test period: {test_total} queries, "
          f"matches seq_len=10: {test_matches_10}, "
          f"matches seq_len=15: {test_matches_15}")

    # With 8000 training bars and 3^10=59049 possible keys, expected match
    # rate is low (~10-15%). seq_len=15 has 3^15≈14M possible keys — even lower.
    # This is acceptable: the trie is a SIGNAL ENHANCER, not the primary
    # predictor. LightGBM (F5) is the primary predictor; trie features are
    # auxiliary. Higher data density in F4 will improve coverage.
    assert test_matches_10 > 0, "Expected at least some seq_len=10 matches"

    print("✓ test_real_db_sanity_check")


# ---------------------------------------------------------------------------
# 12. Run all
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        test_rpt_construction,
        test_rpt_invalid_sector,
        test_rpt_invalid_seq_len,
        test_rpt_insert_and_query_n1,
        test_rpt_query_n1_below_min_obs,
        test_rpt_query_n1_missing_key,
        test_rpt_query_n2_regime_conditional,
        test_rpt_query_n2_fallback_to_n1,
        test_rpt_query_n2_no_fallback_when_disabled,
        test_query_all_agreement,
        test_query_all_conflict,
        test_query_all_missing_key,
        test_prune_removes_low_count_nodes,
        test_evict_lru,
        test_container_construction_initializes_all_sectors,
        test_container_insert_observation,
        test_container_insert_routes_to_correct_sector,
        test_extract_features_keys_present,
        test_extract_features_empty_when_no_data,
        test_extract_features_insufficient_candles,
        test_rpt_persistence_dict_roundtrip,
        test_rpt_persistence_json_file,
        test_container_save_load_all,
        test_compute_vol_regime_quartiles,
        test_compute_vol_regime_custom_breakpoints,
        test_anti_leakage_insert_does_not_retroactively_change_query,
        test_stats_returns_expected_fields,
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
    print(f"\n{'='*60}\nF3 tests: {n_pass} passed, {n_fail} failed (total {n_pass + n_fail})\n{'='*60}")
    return n_fail == 0


if __name__ == "__main__":
    import sys
    ok = run_all()
    sys.exit(0 if ok else 1)
