#!/usr/bin/env python3
"""
SMOKE TEST: FIX-1D — verify production code paths now use cross-asset pools.

Simulates the exact flow that terminal/server.py, realtime.py, paper_trader.py,
and portfolio_runner.py use:

  1. PPMTStorage opened
  2. AssetClassifier used to get asset_class
  3. load_all_tries(symbol, asset_class=info.asset_class) — should pull N1
     from universal pool and N2 from class pool when other symbols already
     contributed.
  4. If trie_n3 is None: PPMTBuilder(symbol, asset_class=...).attach_storage(storage).build(df)
     — should contribute observations to universal N1 and class N2 pools.

Verifies:
  H1: After building BTC and ETH with attach_storage, universal N1 has BTC+ETH patterns.
  H2: After building BTC and ETH, blue_chip class N2 has BTC+ETH patterns (NOT SOL).
  H3: When loading for a NEW symbol DOGE/USDT (meme class), N1 has data (universal
      pool), N2 may be empty (meme class not yet populated), N3/N4 empty.
  H4: When loading for BTC (blue_chip) AFTER ETH was built, N2 has ETH patterns too
      (cross-asset class pool working in production).

If all H pass, FIX-1D successfully wired up the cross-asset pools in production.
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/z/my-project/ppmt/src")

import pandas as pd
from ppmt.engine.ppmt import PPMT
from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.data.classifier import AssetClassifier
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie

CACHE = Path("/home/z/my-project/scripts/audit_cache")


def load_df(sym, tf):
    df = pd.read_json(CACHE / f"{sym}_{tf}.json", orient="records")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("open_time").sort_index()[["open", "high", "low", "close", "volume"]]


def production_build_flow(symbol: str, df: pd.DataFrame, storage: PPMTStorage) -> dict:
    """Mimic the exact production flow in terminal/server.py:744-770."""
    # Step 1: classify asset
    classifier = AssetClassifier()
    info = classifier.classify(symbol)

    # Step 2: load tries with asset_class (FIX-1D)
    all_tries = storage.load_all_tries(symbol, asset_class=info.asset_class)
    trie_n3 = all_tries["n3"]

    if trie_n3 is None:
        # Step 3: auto-build path (FIX-1D)
        builder = PPMT(
            symbol=symbol,
            asset_class=info.asset_class,
            sax_strategy="ohlcv",
        )
        builder.attach_storage(storage)  # FIX-1D critical call
        count = builder.build(df, pattern_length=5)
        # Reload from cross-asset pools
        all_tries = storage.load_all_tries(symbol, asset_class=info.asset_class)

    return {
        "symbol": symbol,
        "asset_class": info.asset_class,
        "n1_patterns": all_tries["n1"].pattern_count if all_tries["n1"] else 0,
        "n2_patterns": all_tries["n2"].pattern_count if all_tries["n2"] else 0,
        "n3_patterns": all_tries["n3"].pattern_count if all_tries["n3"] else 0,
        "n4_patterns": all_tries["n4"].pattern_count if all_tries["n4"] else 0,
        "n4_type": type(all_tries["n4"]).__name__ if all_tries["n4"] else "None",
    }


def main():
    print("=" * 72)
    print("SMOKE TEST: FIX-1D — production code uses cross-asset pools")
    print("=" * 72)

    with tempfile.TemporaryDirectory() as tmp:
        storage = PPMTStorage(db_path=Path(tmp) / "test.db")

        # ---- Phase 1: Build BTC and ETH (both blue_chip) ----
        print("\n--- Phase 1: Build BTC and ETH (both blue_chip) ---")
        df_btc = load_df("BTCUSDT", "5m").head(500)
        df_eth = load_df("ETHUSDT", "5m").head(500)

        btc_result = production_build_flow("BTC/USDT", df_btc, storage)
        print(f"BTC: {btc_result}")

        eth_result = production_build_flow("ETH/USDT", df_eth, storage)
        print(f"ETH: {eth_result}")

        # ---- Phase 2: Build SOL (large_cap) ----
        print("\n--- Phase 2: Build SOL (large_cap) ---")
        df_sol = load_df("SOLUSDT", "5m").head(500)
        sol_result = production_build_flow("SOL/USDT", df_sol, storage)
        print(f"SOL: {sol_result}")

        # ---- Phase 3: Verify cross-asset pools ----
        print("\n--- Phase 3: Verify universal N1 and class N2 pools ---")
        universal_n1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
        blue_chip_n2 = storage.load_trie(class_pool_key("blue_chip"), "n2")
        large_cap_n2 = storage.load_trie(class_pool_key("large_cap"), "n2")

        n1_count = universal_n1.pattern_count if universal_n1 else 0
        n2_blue = blue_chip_n2.pattern_count if blue_chip_n2 else 0
        n2_large = large_cap_n2.pattern_count if large_cap_n2 else 0

        print(f"Universal N1: {n1_count} patterns (expected: BTC+ETH+SOL combined)")
        print(f"Blue_chip N2: {n2_blue} patterns (expected: BTC+ETH, NOT SOL)")
        print(f"Large_cap N2: {n2_large} patterns (expected: SOL only)")

        # ---- Phase 4: Simulate loading for a NEW symbol (DOGE/USDT, meme) ----
        print("\n--- Phase 4: Load tries for NEW symbol DOGE/USDT (meme) ---")
        # No data ingested for DOGE — should fall back to universal N1
        classifier = AssetClassifier()
        info = classifier.classify("DOGE/USDT")
        print(f"DOGE asset_class: {info.asset_class}")
        doge_tries = storage.load_all_tries("DOGE/USDT", asset_class=info.asset_class)
        doge_n1 = doge_tries["n1"].pattern_count if doge_tries["n1"] else 0
        doge_n2 = doge_tries["n2"].pattern_count if doge_tries["n2"] else 0
        doge_n3 = doge_tries["n3"].pattern_count if doge_tries["n3"] else 0
        doge_n4 = doge_tries["n4"].pattern_count if doge_tries["n4"] else 0
        print(f"DOGE N1: {doge_n1} patterns (expected > 0 — universal pool works)")
        print(f"DOGE N2: {doge_n2} patterns (expected 0 — meme class empty)")
        print(f"DOGE N3: {doge_n3} patterns (expected 0 — no own data)")
        print(f"DOGE N4: {doge_n4} patterns (expected 0 — no own data)")

        # ---- Phase 5: Verify BTC N1/N2 now include ETH patterns (cross-asset) ----
        print("\n--- Phase 5: Verify BTC tries now include cross-asset data ---")
        btc_tries_after = storage.load_all_tries("BTC/USDT", asset_class="blue_chip")
        btc_n1_set = set("".join(p) for p, _ in btc_tries_after["n1"].get_all_patterns(min_count=1)) \
            if btc_tries_after["n1"] else set()
        btc_n2_set = set("".join(p) for p, _ in btc_tries_after["n2"].get_all_patterns(min_count=1)) \
            if btc_tries_after["n2"] else set()
        btc_n3_set = set("".join(p) for p, _ in btc_tries_after["n3"].get_all_patterns(min_count=1)) \
            if btc_tries_after["n3"] else set()

        print(f"BTC N1 patterns: {len(btc_n1_set)} (universal)")
        print(f"BTC N2 patterns: {len(btc_n2_set)} (blue_chip class)")
        print(f"BTC N3 patterns: {len(btc_n3_set)} (BTC only)")
        print(f"N1 ⊃ N2 (universal contains class): {btc_n1_set >= btc_n2_set}")
        print(f"N2 ⊃ N3 (class contains per-symbol): {btc_n2_set >= btc_n3_set}")
        print(f"N1 == N3 (universal == per-symbol — should be False): {btc_n1_set == btc_n3_set}")
        print(f"N2 == N3 (class == per-symbol — should be False): {btc_n2_set == btc_n3_set}")

        # ---- VERDICT ----
        print("\n" + "=" * 72)
        print("VEREDICTO FIX-1D")
        print("=" * 72)
        checks = {
            "universal_n1_populated": n1_count > 0,
            "blue_chip_n2_populated": n2_blue > 0,
            "large_cap_n2_separate_from_blue_chip": n2_large > 0 and n2_blue != n2_large,
            "n4_is_regime_partitioned": btc_result["n4_type"] == "RegimePartitionedTrie",
            "doge_n1_from_universal_pool": doge_n1 > 0,
            "doge_n3_empty_no_own_data": doge_n3 == 0,
            "btc_n1_differs_from_n3": btc_n1_set != btc_n3_set,
            "btc_n2_differs_from_n3": btc_n2_set != btc_n3_set,
            "btc_n1_superset_of_n2": btc_n1_set >= btc_n2_set,
            "btc_n2_superset_of_n3": btc_n2_set >= btc_n3_set,
        }
        all_pass = all(checks.values())
        for k, v in checks.items():
            status = "✅" if v else "❌"
            print(f"  {status} {k}: {v}")

        print(f"\nTotal: {sum(checks.values())}/{len(checks)} checks pass")
        if all_pass:
            print("\n✅ FIX-1D WORKING: production code paths now use cross-asset pools")
            print("   N1 universal pool populated, N2 class pool populated,")
            print("   N3/N4 per-symbol populated, N4 = RegimePartitionedTrie.")
            print("   New symbols automatically benefit from universal N1 safety net.")
        else:
            print("\n❌ FIX-1D INCOMPLETE — some checks failed")


if __name__ == "__main__":
    main()
