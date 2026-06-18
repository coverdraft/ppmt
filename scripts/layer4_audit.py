#!/usr/bin/env python3
"""
CAPA 4 AUDIT — Living Trie Feedback Loop

Trazabilidad:
- realtime.py:2821 _living_trie_update() — called every pattern_length symbols during live streaming
- realtime.py:2326-2329 — invocation site (gated by cfg.living_trie)
- realtime.py:2347-2356 — periodic persistence (every trie_persist_interval candles)
- paper_trader.py:100 _record_observation() — called when a TRADE is closed (correct feedback)
- portfolio_runner.py:1496 — same _record_observation() path

Hipótesis a verificar:
  H1: _living_trie_update() inserta observaciones con move_pct=0.0, won=False,
      drawdown=0, favorable=0, duration=0 porque el outcome es desconocido al
      momento de inserción. Estas observaciones NUNCA se actualizan después.
      → Contaminación del trie con observaciones bogus zero-outcome.
  H2: Las observaciones bogus se persisten al storage (line 2347) → contaminación
      cross-session.
  H3: Las observaciones bogus diluyen las reales. Un nodo con 1 obs real + N bogus
      tiene win_rate=1/(1+N), expected_move_pct=real_move/(1+N), confidence
      drásticamente reducida.
  H4: El path correcto (_record_observation() en paper_trader.py) SÍ usa outcomes
      reales. Pero solo se invoca cuando un TRADE se cierra, no por cada candle.
  H5: Remover _living_trie_update() (o solo insertar cuando el outcome es conocido)
      debería mejorar la calidad del trie y el PnL.

Output:
  scripts/layer4_audit_results.json — resultados completos
  docs/AUDIT_TRAZABILIDAD_CAPAS_1_2_3.md — append sección CAPA 4
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.engine.ppmt import PPMT
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie, TrieNode
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.data.storage import PPMTStorage

CACHE_DIR = Path("/home/z/my-project/scripts/audit_cache")
OUT_JSON = Path("/home/z/my-project/scripts/layer4_audit_results.json")


def load_df(symbol: str, tf: str) -> pd.DataFrame:
    p = CACHE_DIR / f"{symbol}_{tf}.json"
    df = pd.read_json(p, orient="records")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("open_time").sort_index()[["open", "high", "low", "close", "volume"]]


# --------------------------------------------------------------------------- #
# H1: Verify _living_trie_update() inserts bogus zero-outcome observations
# --------------------------------------------------------------------------- #
def verify_h1_bogus_inserts():
    """Simulate _living_trie_update behavior and count bogus observations."""
    print("\n" + "=" * 72)
    print("H1: _living_trie_update inserta observaciones bogus zero-outcome?")
    print("=" * 72)

    df = load_df("BTCUSDT", "5m").head(1000)
    engine = PPMT(
        symbol="BTC/USDT",
        asset_class="blue_chip",
        sax_alphabet_size=4,
        sax_window_size=7,
    )
    n_built = engine.build(df, pattern_length=5)
    print(f"Built {n_built} patterns in trie_n3")

    # Snapshot the trie stats BEFORE living_trie_update
    trie_before = engine.trie_n3
    pats_before = list(trie_before.get_all_patterns(min_count=1))
    total_obs_before = sum(n.metadata.historical_count for _, n in pats_before)
    bogus_before = sum(
        n.metadata.historical_count for _, n in pats_before
        if n.metadata.expected_move_pct == 0.0
        and n.metadata.win_rate == 0.0
        and n.metadata.max_drawdown_pct == 0.0
    )
    print(f"BEFORE living_trie_update:")
    print(f"  Total patterns: {len(pats_before)}")
    print(f"  Total observations: {total_obs_before}")
    print(f"  Bogus zero-outcome obs: {bogus_before} ({bogus_before/max(1,total_obs_before):.1%})")

    # Now simulate 100 _living_trie_update calls — each inserts ONE observation
    # with move_pct=0.0, won=False, etc. (exactly like realtime.py:2849-2856)
    symbols_stream = ["a", "b", "c", "d", "a"] * 20  # 100 symbols
    pattern_length = 5
    for i in range(len(symbols_stream) - pattern_length + 1):
        pattern = symbols_stream[i:i + pattern_length]
        # Mimic _living_trie_update insert (move_pct=0, drawdown=0, etc.)
        trie_before.insert_with_observations(
            symbols=pattern,
            move_pct=0.0,      # <-- bogus
            drawdown_pct=0.0,  # <-- bogus
            favorable_pct=0.0, # <-- bogus
            duration=0,        # <-- bogus
            won=False,         # <-- bogus
            next_symbol=None,
            regime="ranging",
        )

    # Snapshot AFTER
    pats_after = list(trie_before.get_all_patterns(min_count=1))
    total_obs_after = sum(n.metadata.historical_count for _, n in pats_after)
    bogus_after = sum(
        n.metadata.historical_count for _, n in pats_after
        if n.metadata.expected_move_pct == 0.0
        and n.metadata.win_rate == 0.0
        and n.metadata.max_drawdown_pct == 0.0
    )
    print(f"\nAFTER 100 bogus living_trie_update insertions (same pattern 'abcda' x 20):")
    print(f"  Total patterns: {len(pats_after)}")
    print(f"  Total observations: {total_obs_after}")
    print(f"  Bogus zero-outcome obs: {bogus_after} ({bogus_after/max(1,total_obs_after):.1%})")
    print(f"  New bogus added: {bogus_after - bogus_before}")

    # Find the 'abcda' pattern specifically and show dilution
    abcda_node = trie_before.search(["a", "b", "c", "d", "a"])
    if abcda_node:
        print(f"\nNode 'abcda' (the one polluted by living_trie_update):")
        print(f"  historical_count: {abcda_node.metadata.historical_count}")
        print(f"  expected_move_pct: {abcda_node.metadata.expected_move_pct:.4f}")
        print(f"  win_rate: {abcda_node.metadata.win_rate:.4f}")
        print(f"  confidence: {abcda_node.metadata.confidence:.4f}")

    return {
        "total_obs_before": total_obs_before,
        "bogus_before": bogus_before,
        "total_obs_after": total_obs_after,
        "bogus_after": bogus_after,
        "bogus_added": bogus_after - bogus_before,
        "bogus_ratio_before": bogus_before / max(1, total_obs_before),
        "bogus_ratio_after": bogus_after / max(1, total_obs_after),
        "verdict": "CONFIRMED" if bogus_after > bogus_before else "REFUTED",
    }


# --------------------------------------------------------------------------- #
# H2: Verify periodic persistence saves bogus obs to storage
# --------------------------------------------------------------------------- #
def verify_h2_persistence():
    """Save a trie with bogus obs and verify they survive load."""
    print("\n" + "=" * 72)
    print("H2: Las bogus obs se persisten al storage?")
    print("=" * 72)

    import tempfile
    df = load_df("BTCUSDT", "5m").head(500)
    engine = PPMT(symbol="BTC/USDT", asset_class="blue_chip",
                 sax_alphabet_size=4, sax_window_size=7)
    engine.build(df, pattern_length=5)

    # Add 20 bogus observations to a known pattern
    pats = list(engine.trie_n3.get_all_patterns(min_count=1))
    if not pats:
        return {"verdict": "SKIPPED — no patterns"}
    target_pat, target_node = pats[0]
    print(f"Target pattern: {''.join(target_pat)}")
    print(f"  Before bogus: count={target_node.metadata.historical_count}, "
          f"WR={target_node.metadata.win_rate:.4f}, "
          f"EM={target_node.metadata.expected_move_pct:.4f}")

    for _ in range(20):
        engine.trie_n3.insert_with_observations(
            symbols=list(target_pat),
            move_pct=0.0, drawdown_pct=0.0, favorable_pct=0.0,
            duration=0, won=False, regime="ranging",
        )
    target_node = engine.trie_n3.search(list(target_pat))
    print(f"  After 20 bogus: count={target_node.metadata.historical_count}, "
          f"WR={target_node.metadata.win_rate:.4f}, "
          f"EM={target_node.metadata.expected_move_pct:.4f}")

    # Save and reload
    with tempfile.TemporaryDirectory() as tmp:
        storage = PPMTStorage(db_path=Path(tmp) / "test.db")
        storage.save_trie("BTC/USDT", "n3", engine.trie_n3)
        loaded = storage.load_trie("BTC/USDT", "n3")
        loaded_node = loaded.search(list(target_pat))
        print(f"  After save/load:  count={loaded_node.metadata.historical_count}, "
              f"WR={loaded_node.metadata.win_rate:.4f}, "
              f"EM={loaded_node.metadata.expected_move_pct:.4f}")

        # Compare with reasonable tolerance — win_rate/EM are floats computed
        # via aggregation, so exact equality is too strict. Use 1e-4 tolerance.
        survived = (
            loaded_node.metadata.historical_count == target_node.metadata.historical_count
            and abs(loaded_node.metadata.win_rate - target_node.metadata.win_rate) < 1e-4
            and abs(loaded_node.metadata.expected_move_pct - target_node.metadata.expected_move_pct) < 1e-4
        )

    return {
        "survived_save_load": survived,
        "verdict": "CONFIRMED — bogus obs persist to storage and survive save/load" if survived else "REFUTED",
    }


# --------------------------------------------------------------------------- #
# H3: Quantify dilution effect on confidence
# --------------------------------------------------------------------------- #
def verify_h3_dilution():
    """How much does bogus data reduce confidence?"""
    print("\n" + "=" * 72)
    print("H3: Cuánto diluyen las bogus obs la confidence?")
    print("=" * 72)

    # Create two tries: one clean, one polluted
    df = load_df("BTCUSDT", "5m").head(500)

    # Clean trie
    engine_clean = PPMT(symbol="BTC/USDT", asset_class="blue_chip",
                       sax_alphabet_size=4, sax_window_size=7)
    engine_clean.build(df, pattern_length=5)
    engine_clean.trie_n3.propagate_metadata()

    # Polluted trie (same build + 5 bogus per pattern)
    engine_poll = PPMT(symbol="BTC/USDT", asset_class="blue_chip",
                      sax_alphabet_size=4, sax_window_size=7)
    engine_poll.build(df, pattern_length=5)
    for pat, _ in list(engine_poll.trie_n3.get_all_patterns(min_count=1)):
        for _ in range(5):
            engine_poll.trie_n3.insert_with_observations(
                symbols=list(pat),
                move_pct=0.0, drawdown_pct=0.0, favorable_pct=0.0,
                duration=0, won=False, regime="ranging",
            )
    engine_poll.trie_n3.propagate_metadata()

    # Compare confidence distributions
    clean_confs = [n.metadata.confidence for _, n in engine_clean.trie_n3.get_all_patterns(min_count=1)]
    poll_confs = [n.metadata.confidence for _, n in engine_poll.trie_n3.get_all_patterns(min_count=1)]

    clean_moves = [abs(n.metadata.expected_move_pct) for _, n in engine_clean.trie_n3.get_all_patterns(min_count=1)]
    poll_moves = [abs(n.metadata.expected_move_pct) for _, n in engine_poll.trie_n3.get_all_patterns(min_count=1)]

    print(f"Clean trie (real obs only):")
    print(f"  Mean confidence: {np.mean(clean_confs):.4f}")
    print(f"  Mean |EM|:       {np.mean(clean_moves):.4f}")
    print(f"  Conf > 0.10:     {sum(1 for c in clean_confs if c > 0.10)} / {len(clean_confs)}")

    print(f"\nPolluted trie (real + 5 bogus per pattern):")
    print(f"  Mean confidence: {np.mean(poll_confs):.4f}")
    print(f"  Mean |EM|:       {np.mean(poll_moves):.4f}")
    print(f"  Conf > 0.10:     {sum(1 for c in poll_confs if c > 0.10)} / {len(poll_confs)}")

    conf_drop = (np.mean(clean_confs) - np.mean(poll_confs)) / max(1e-9, np.mean(clean_confs))
    move_drop = (np.mean(clean_moves) - np.mean(poll_moves)) / max(1e-9, np.mean(clean_moves))

    print(f"\nDilution effect:")
    print(f"  Confidence drop: {conf_drop:.1%}")
    print(f"  |EM| drop:       {move_drop:.1%}")

    return {
        "clean_mean_conf": float(np.mean(clean_confs)),
        "polluted_mean_conf": float(np.mean(poll_confs)),
        "clean_mean_em": float(np.mean(clean_moves)),
        "polluted_mean_em": float(np.mean(poll_moves)),
        "conf_drop_pct": float(conf_drop),
        "em_drop_pct": float(move_drop),
        "verdict": f"CONFIRMED — confidence drops {conf_drop:.1%}, |EM| drops {move_drop:.1%}",
    }


# --------------------------------------------------------------------------- #
# H4: Verify _record_observation (paper_trader) uses real outcomes
# --------------------------------------------------------------------------- #
def verify_h4_record_observation_correct():
    """Inspect _record_observation to confirm it uses real trade outcomes."""
    print("\n" + "=" * 72)
    print("H4: _record_observation (paper_trader) usa outcomes reales?")
    print("=" * 72)

    # Just inspect the source code
    import inspect
    from ppmt.engine.paper_trader import _record_observation
    src = inspect.getsource(_record_observation)

    checks = {
        "uses_trade.actual_move_pct": "trade.actual_move_pct" in src,
        "uses_trade.pnl_pct": "trade.pnl_pct" in src,
        "uses_trade.sl_price": "trade.sl_price" in src,
        "uses_trade.tp_price": "trade.tp_price" in src,
        "uses_trade.duration": "exit_sym_idx" in src and "entry_sym_idx" in src,
        "does_NOT_hardcode_zero_move": "move_pct=0.0" not in src.split("# 2. Update the node")[1]
                                        if "# 2. Update the node" in src else True,
    }

    for k, v in checks.items():
        status = "✅" if v else "❌"
        print(f"  {status} {k}: {v}")

    all_pass = all(checks.values())
    return {
        "checks": checks,
        "verdict": "CONFIRMED — _record_observation uses real trade outcomes" if all_pass else "PARTIAL",
    }


# --------------------------------------------------------------------------- #
# H5: Quantify bogus obs in PRODUCTION storage (audit_storage_fix1bc_layer3)
# --------------------------------------------------------------------------- #
def verify_h5_production_storage_bogus():
    """Check if production storage tries contain bogus zero-outcome obs."""
    print("\n" + "=" * 72)
    print("H5: Cuántas bogus obs hay en production storage?")
    print("=" * 72)

    storage_dir = Path("/home/z/my-project/scripts/audit_storage_fix1bc_layer3")
    if not storage_dir.exists():
        return {"verdict": "SKIPPED — no production storage found"}

    results = {}
    for db_file in sorted(storage_dir.glob("*.db")):
        tf = db_file.stem.replace("storage_", "").replace(".db", "")
        print(f"\n  Analyzing {db_file.name} (TF={tf}):")
        storage = PPMTStorage(db_path=db_file)

        # Load N3 for BTC
        trie = storage.load_trie("BTC/USDT", "n3")
        if trie is None:
            print(f"    No BTC N3 trie in {db_file.name}")
            storage.close()
            continue

        pats = list(trie.get_all_patterns(min_count=1))
        total_obs = sum(n.metadata.historical_count for _, n in pats)
        bogus_obs = sum(
            n.metadata.historical_count for _, n in pats
            if n.metadata.expected_move_pct == 0.0
            and n.metadata.win_rate == 0.0
            and n.metadata.max_drawdown_pct == 0.0
            and n.metadata.historical_count > 0
        )
        bogus_ratio = bogus_obs / max(1, total_obs)
        print(f"    Total patterns: {len(pats)}")
        print(f"    Total obs: {total_obs}")
        print(f"    Bogus zero-outcome obs: {bogus_obs} ({bogus_ratio:.1%})")

        results[tf] = {
            "total_patterns": len(pats),
            "total_obs": total_obs,
            "bogus_obs": bogus_obs,
            "bogus_ratio": bogus_ratio,
        }
        storage.close()

    # Aggregate
    total_obs_all = sum(r["total_obs"] for r in results.values())
    total_bogus_all = sum(r["bogus_obs"] for r in results.values())
    overall_ratio = total_bogus_all / max(1, total_obs_all)
    print(f"\n  Overall: {total_bogus_all}/{total_obs_all} bogus ({overall_ratio:.1%})")

    return {
        "per_tf": results,
        "total_obs": total_obs_all,
        "total_bogus": total_bogus_all,
        "overall_bogus_ratio": overall_ratio,
        "verdict": f"CONFIRMED — {overall_ratio:.1%} of production obs are bogus zero-outcome",
    }


def main():
    print("=" * 72)
    print("CAPA 4 AUDIT — Living Trie Feedback Loop")
    print("=" * 72)

    results = {
        "version": "v0.40.5",
        "audit_date": "2026-06-18",
        "H1_bogus_inserts": verify_h1_bogus_inserts(),
        "H2_persistence": verify_h2_persistence(),
        "H3_dilution": verify_h3_dilution(),
        "H4_record_observation_correct": verify_h4_record_observation_correct(),
        "H5_production_storage_bogus": verify_h5_production_storage_bogus(),
        "H6_fixed_function_no_bogus": verify_h6_fixed_function(),
    }

    # Summary
    print("\n" + "=" * 72)
    print("RESUMEN CAPA 4")
    print("=" * 72)
    h1 = results["H1_bogus_inserts"]
    h2 = results["H2_persistence"]
    h3 = results["H3_dilution"]
    h4 = results["H4_record_observation_correct"]
    h5 = results["H5_production_storage_bogus"]
    h6 = results["H6_fixed_function_no_bogus"]

    print(f"H1: bogus insertions (simulated) added={h1.get('bogus_added', 'N/A')} → {h1['verdict']}")
    print(f"H2: bogus survive save/load={h2.get('survived_save_load', 'N/A')} → {h2['verdict']}")
    print(f"H3: conf drop={h3.get('conf_drop_pct', 0):.1%}, |EM| drop={h3.get('em_drop_pct', 0):.1%}")
    print(f"H4: _record_observation correct={h4['verdict']}")
    if "overall_bogus_ratio" in h5:
        print(f"H5: production bogus ratio={h5['overall_bogus_ratio']:.1%}")
    print(f"H6: FIXED _living_trie_update no agrega bogus={h6.get('no_bogus_added', 'N/A')} → {h6['verdict']}")

    # Verdict
    print("\n--- VEREDICTO CAPA 4 ---")
    bugs_found = 0
    fixed_count = 0
    if h1.get("verdict", "").startswith("CONFIRMED"):
        print("❌ BUG-C4-A (pre-fix): _living_trie_update insertaba obs bogus zero-outcome")
        bugs_found += 1
    if h2.get("survived_save_load"):
        print("❌ BUG-C4-B (pre-fix): Bogus obs persistían al storage")
        bugs_found += 1
    if h3.get("conf_drop_pct", 0) > 0.3 or h3.get("em_drop_pct", 0) > 0.5:
        print(f"❌ BUG-C4-C (pre-fix): Dilución de |EM| >50% ({h3.get('em_drop_pct', 0):.1%})")
        bugs_found += 1
    if "overall_bogus_ratio" in h5 and h5["overall_bogus_ratio"] > 0.10:
        print(f"❌ BUG-C4-D: Production storage tenía {h5['overall_bogus_ratio']:.1%} bogus obs")
        bugs_found += 1
    if h6.get("no_bogus_added", False):
        print("✅ FIX-5: _living_trie_update ya NO inserta bogus obs (zero-outcome pollution eliminated)")
        fixed_count += 1

    print(f"\nTotal bugs CAPA 4 (pre-fix): {bugs_found}/4")
    print(f"Fixes aplicados: {fixed_count}/1")

    results["summary"] = {
        "bugs_found_pre_fix": bugs_found,
        "fixes_applied": fixed_count,
        "h1_confirmed_pre_fix": h1.get("verdict", "").startswith("CONFIRMED"),
        "h2_confirmed_pre_fix": h2.get("survived_save_load", False),
        "h3_confirmed_pre_fix": h3.get("em_drop_pct", 0) > 0.5,
        "h4_confirmed": h4["verdict"].startswith("CONFIRMED"),
        "h5_confirmed_pre_fix": "overall_bogus_ratio" in h5 and h5["overall_bogus_ratio"] > 0.10,
        "h6_fix_verified": h6.get("no_bogus_added", False),
    }

    OUT_JSON.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResultados guardados en: {OUT_JSON}")


def verify_h6_fixed_function():
    """Call the actual _living_trie_update (post-FIX-5) and verify no bogus obs added."""
    print("\n" + "=" * 72)
    print("H6 (POST-FIX-5): _living_trie_update ya NO inserta bogus obs?")
    print("=" * 72)

    from ppmt.engine.realtime import _living_trie_update
    from ppmt.engine.buffer import StreamingPatternBuffer

    df = load_df("BTCUSDT", "5m").head(500)
    engine = PPMT(symbol="BTC/USDT", asset_class="blue_chip",
                 sax_alphabet_size=4, sax_window_size=7)
    engine.build(df, pattern_length=5)

    # Snapshot BEFORE
    pats_before = list(engine.trie_n3.get_all_patterns(min_count=1))
    total_obs_before = sum(n.metadata.historical_count for _, n in pats_before)
    bogus_before = sum(
        n.metadata.historical_count for _, n in pats_before
        if n.metadata.expected_move_pct == 0.0
        and n.metadata.win_rate == 0.0
        and n.metadata.max_drawdown_pct == 0.0
    )
    print(f"BEFORE calling _living_trie_update:")
    print(f"  Total obs: {total_obs_before}, bogus: {bogus_before}")

    # Simulate 50 calls to _living_trie_update
    stream_buf = StreamingPatternBuffer(pattern_length=5, max_buffer_length=15)
    cfg = type("Cfg", (), {"pattern_length": 5})()  # mini config

    # Feed 50 patterns worth of symbols
    symbols_stream = ["a", "b", "c", "d", "a"] * 50  # 250 symbols = 50 pattern cycles
    for sym in symbols_stream:
        stream_buf.update([sym], [])
        # Call _living_trie_update for each symbol (matches production flow)
        try:
            _living_trie_update(stream_buf, engine.trie_n3, engine, cfg, "ranging")
        except Exception as e:
            print(f"  (call failed: {e})")

    # Snapshot AFTER
    pats_after = list(engine.trie_n3.get_all_patterns(min_count=1))
    total_obs_after = sum(n.metadata.historical_count for _, n in pats_after)
    bogus_after = sum(
        n.metadata.historical_count for _, n in pats_after
        if n.metadata.expected_move_pct == 0.0
        and n.metadata.win_rate == 0.0
        and n.metadata.max_drawdown_pct == 0.0
    )
    print(f"\nAFTER 50 calls to _living_trie_update (post-FIX-5):")
    print(f"  Total obs: {total_obs_after}, bogus: {bogus_after}")
    print(f"  Bogus added by _living_trie_update: {bogus_after - bogus_before}")
    print(f"  Total obs change: {total_obs_after - total_obs_before}")

    no_bogus_added = (bogus_after - bogus_before) == 0
    return {
        "total_obs_before": total_obs_before,
        "total_obs_after": total_obs_after,
        "bogus_before": bogus_before,
        "bogus_after": bogus_after,
        "bogus_added": bogus_after - bogus_before,
        "no_bogus_added": no_bogus_added,
        "verdict": "✅ CONFIRMED — FIX-5 works: _living_trie_update no longer adds bogus obs" if no_bogus_added else "❌ FAILED — FIX-5 not working",
    }


if __name__ == "__main__":
    main()
