"""
Compute trie node counts for v4 expanded dataset (16 tokens × 200k candles 1m).

Compares saturation vs v3 (14 tokens × 100k) to verify if N4 is maturing as predicted.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
from ppmt.core.metadata import BlockLifecycleMetadata  # noqa: F401
from ppmt.core.regime import RegimeDetector

ALPHA = 4
WINDOW = 7
PATTERN_LEN = 5

DATA_DIR = Path("/home/z/my-project/download/real_data_1m_v4")
MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MEMES  = ["PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT"]
ALTS   = ["LINKUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "APTUSDT", "INJUSDT", "TIAUSDT"]
SYMBOLS = MAJORS + MEMES + ALTS

OUT_DIR = Path("/home/z/my-project/download/trie_stats_1m_v4")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def count_nodes_ppmt_trie(trie: PPMTTrie, max_depth: int) -> dict:
    terminal = 0
    internal = 0
    total_with_meta = 0
    by_depth = {0: 1}

    stack = [(trie.root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 0:
            by_depth[depth] = by_depth.get(depth, 0) + 1
        if depth == max_depth:
            terminal += 1
            if node.metadata and node.metadata.historical_count > 0:
                total_with_meta += 1
        else:
            if depth > 0:
                internal += 1
            for child in node.children.values():
                stack.append((child, depth + 1))
    return {
        "terminal_nodes": terminal,
        "internal_nodes": internal,
        "total_nodes": terminal + internal + 1,
        "terminal_with_observations": total_with_meta,
        "by_depth": by_depth,
    }


def count_nodes_regime_trie(rtrie: RegimePartitionedTrie, max_depth: int) -> dict:
    per_regime = {}
    total_terminal = 0
    total_internal = 0
    total_with_meta = 0
    total_all_nodes = 0
    for regime_name, sub in rtrie.sub_tries.items():
        s = count_nodes_ppmt_trie(sub, max_depth)
        per_regime[regime_name] = s
        total_terminal += s["terminal_nodes"]
        total_internal += s["internal_nodes"]
        total_with_meta += s["terminal_with_observations"]
        total_all_nodes += s["total_nodes"]
    return {
        "per_regime": per_regime,
        "total_terminal": total_terminal,
        "total_internal": total_internal,
        "total_with_observations": total_with_meta,
        "total_all_nodes": total_all_nodes,
        "n_regimes_active": sum(1 for r in per_regime.values() if r["terminal_with_observations"] > 0),
    }


def main():
    print(f"PPMT Trie Node Counter v4 — α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}")
    print(f"Theoretical max patterns per trie: {ALPHA ** PATTERN_LEN} = {ALPHA**PATTERN_LEN}")
    print(f"Tokens: {len(SYMBOLS)} (5 majors + 4 memes + 7 alts)\n")

    results = {}
    grand_total_n3 = {"terminal": 0, "internal": 0, "with_meta": 0, "total": 0}
    grand_total_n4 = {"terminal": 0, "internal": 0, "with_meta": 0, "total": 0, "regimes_active": 0}
    grand_total_by_class = {
        "major": {"n3_total": 0, "n3_meta": 0, "n4_total": 0, "n4_meta": 0},
        "meme":  {"n3_total": 0, "n3_meta": 0, "n4_total": 0, "n4_meta": 0},
        "alt":   {"n3_total": 0, "n3_meta": 0, "n4_total": 0, "n4_meta": 0},
    }

    for sym in SYMBOLS:
        csv = DATA_DIR / f"{sym}_1m.csv"
        if not csv.exists():
            print(f"  [MISSING] {sym}: no CSV found")
            continue
        df = pd.read_csv(csv)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)

        if sym in MAJORS: cls = "major"
        elif sym in MEMES: cls = "meme"
        else: cls = "alt"

        sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
        regime_detector = RegimeDetector()
        symbols = sax.encode(df)

        trie_n3 = PPMTTrie(name=f"per_asset:{sym}")
        trie_n4 = RegimePartitionedTrie(name=f"per_asset_regime:{sym}")

        n_inserted = 0
        for i in range(len(symbols) - PATTERN_LEN):
            pattern = symbols[i:i + PATTERN_LEN]
            next_sym = symbols[i + PATTERN_LEN] if i + PATTERN_LEN < len(symbols) else None
            start_candle = i * WINDOW
            end_candle = (i + PATTERN_LEN) * WINDOW
            if end_candle > len(df):
                break
            window_df = df.iloc[start_candle:end_candle]
            entry_price = window_df["close"].iloc[0]
            exit_price = window_df["close"].iloc[-1]
            move_pct = ((exit_price - entry_price) / entry_price) * 100.0
            high = window_df["high"].max()
            low = window_df["low"].min()
            drawdown_pct = ((low - entry_price) / entry_price) * 100.0
            favorable_pct = ((high - entry_price) / entry_price) * 100.0
            duration = len(window_df)
            won = move_pct > 0
            regime = regime_detector.detect_simple(window_df)
            trie_n3.insert_with_observations(
                symbols=pattern, move_pct=move_pct, drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct, duration=duration, won=won,
                next_symbol=next_sym, regime=regime,
            )
            trie_n4.insert_with_observations(
                symbols=pattern, move_pct=move_pct, drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct, duration=duration, won=won,
                next_symbol=next_sym, regime=regime,
            )
            n_inserted += 1

        n3_stats = count_nodes_ppmt_trie(trie_n3, PATTERN_LEN)
        n4_stats = count_nodes_regime_trie(trie_n4, PATTERN_LEN)

        results[sym] = {
            "asset_class": cls,
            "n_candles": len(df),
            "n_patterns_inserted": n_inserted,
            "n3": n3_stats,
            "n4": n4_stats,
        }

        grand_total_n3["terminal"] += n3_stats["terminal_nodes"]
        grand_total_n3["internal"] += n3_stats["internal_nodes"]
        grand_total_n3["with_meta"] += n3_stats["terminal_with_observations"]
        grand_total_n3["total"] += n3_stats["total_nodes"]
        grand_total_n4["terminal"] += n4_stats["total_terminal"]
        grand_total_n4["internal"] += n4_stats["total_internal"]
        grand_total_n4["with_meta"] += n4_stats["total_with_observations"]
        grand_total_n4["total"] += n4_stats["total_all_nodes"]
        grand_total_n4["regimes_active"] += n4_stats["n_regimes_active"]
        grand_total_by_class[cls]["n3_total"] += n3_stats["total_nodes"]
        grand_total_by_class[cls]["n3_meta"] += n3_stats["terminal_with_observations"]
        grand_total_by_class[cls]["n4_total"] += n4_stats["total_all_nodes"]
        grand_total_by_class[cls]["n4_meta"] += n4_stats["total_with_observations"]

        print(f"  {sym:>10} [{cls:>5}]: candles={len(df):>7,} inserted={n_inserted:>7,} | "
              f"N3={n3_stats['total_nodes']:>5,} (meta={n3_stats['terminal_with_observations']:>4}) | "
              f"N4={n4_stats['total_all_nodes']:>6,} (meta={n4_stats['total_with_observations']:>5}, regimes={n4_stats['n_regimes_active']})")

    print(f"\n=== GRAND TOTAL ({len(results)} tokens, v4 dataset) ===")
    print(f"N3 (per-asset):")
    print(f"  Terminal nodes:          {grand_total_n3['terminal']:>10,}")
    print(f"  Internal nodes:          {grand_total_n3['internal']:>10,}")
    print(f"  Terminal with obs (>0):  {grand_total_n3['with_meta']:>10,}")
    print(f"  TOTAL nodes:             {grand_total_n3['total']:>10,}")
    print(f"\nN4 (per-asset + regime, 4 sub-tries/token):")
    print(f"  Terminal nodes:          {grand_total_n4['terminal']:>10,}")
    print(f"  Internal nodes:          {grand_total_n4['internal']:>10,}")
    print(f"  Terminal with obs (>0):  {grand_total_n4['with_meta']:>10,}")
    print(f"  TOTAL nodes:             {grand_total_n4['total']:>10,}")
    print(f"  Total regimes active:    {grand_total_n4['regimes_active']:>10,}")
    print(f"\n  Combined N3+N4:          {grand_total_n3['total'] + grand_total_n4['total']:>10,}")

    theoretical_per_trie = ALPHA ** PATTERN_LEN
    theoretical_n3_total = theoretical_per_trie * len(results)
    theoretical_n4_total = theoretical_per_trie * 4 * len(results)
    n3_sat = grand_total_n3['with_meta']/theoretical_n3_total*100
    n4_sat = grand_total_n4['with_meta']/theoretical_n4_total*100

    print(f"\n=== THEORETICAL BOUNDS ===")
    print(f"Max terminal nodes per single trie: {theoretical_per_trie:,}")
    print(f"Max terminal nodes N3 total ({len(results)} tokens): {theoretical_n3_total:,}")
    print(f"Max terminal nodes N4 total ({len(results)} tokens × 4 regimes): {theoretical_n4_total:,}")
    print(f"N3 saturation: {n3_sat:.1f}% ({grand_total_n3['with_meta']:,} / {theoretical_n3_total:,})")
    print(f"N4 saturation: {n4_sat:.1f}% ({grand_total_n4['with_meta']:,} / {theoretical_n4_total:,})")

    print(f"\n=== COMPARISON vs V3 (14 tokens × 100k) ===")
    print(f"v3 N3: 19,109 nodes (saturated 100.0%)")
    print(f"v3 N4: 23,482 nodes (saturated 27.9%)")
    print(f"v3 combined: 42,591 nodes")
    print(f"v4 N3: {grand_total_n3['total']:,} nodes (saturated {n3_sat:.1f}%)")
    print(f"v4 N4: {grand_total_n4['total']:,} nodes (saturated {n4_sat:.1f}%)")
    print(f"v4 combined: {grand_total_n3['total'] + grand_total_n4['total']:,} nodes")
    print(f"\nN4 saturation delta: 27.9% → {n4_sat:.1f}% ({n4_sat - 27.9:+.1f}pp)")

    print(f"\n=== BY ASSET CLASS ===")
    for cls, label in [("major", "Majors (5)"), ("meme", "Memes (4)"), ("alt", "Alts (7)")]:
        d = grand_total_by_class[cls]
        print(f"{label}: N3={d['n3_total']:,} nodes (with_meta={d['n3_meta']:,}) | "
              f"N4={d['n4_total']:,} nodes (with_meta={d['n4_meta']:,})")

    out = {
        "config": {"alpha": ALPHA, "window": WINDOW, "pattern_length": PATTERN_LEN,
                   "n_tokens": len(results), "dataset": "v4 (16 tokens x 200k candles)"},
        "per_token": results,
        "grand_totals": {
            "n3": grand_total_n3,
            "n4": grand_total_n4,
            "combined_n3_n4": grand_total_n3["total"] + grand_total_n4["total"],
        },
        "by_class": grand_total_by_class,
        "theoretical_bounds": {
            "max_terminal_per_trie": theoretical_per_trie,
            "max_terminal_n3_total": theoretical_n3_total,
            "max_terminal_n4_total": theoretical_n4_total,
            "n3_saturation_pct": round(n3_sat, 2),
            "n4_saturation_pct": round(n4_sat, 2),
        },
        "comparison_v3": {
            "v3_n3_total_nodes": 19109,
            "v3_n4_total_nodes": 23482,
            "v3_combined": 42591,
            "v3_n4_saturation_pct": 27.9,
            "v4_n4_saturation_pct": round(n4_sat, 2),
            "delta_n4_saturation_pp": round(n4_sat - 27.9, 2),
        },
    }
    out_path = OUT_DIR / "node_counts_v4.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
