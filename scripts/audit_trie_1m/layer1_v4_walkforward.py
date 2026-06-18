"""
Capa 1 audit v4 — N3 vs N4 (FIX-14) on EXPANDED dataset.

Dataset: 16 tokens (5 majors + 4 memes + 7 alts) × 200k candles 1m = 3.2M candles
Split: 150k train / 50k test per token (test on the most recent 50k = ~35 days OOS)

Compares N3-only (regime-agnostic baseline) vs N4-regime-routed (FIX-14).

Output:
  /home/z/my-project/download/trie_stats_1m_v4/layer1_v4_walkforward.json
  /home/z/my-project/download/trie_stats_1m_v4/layer1_v4_summary.md
"""
from __future__ import annotations
import json, sys, statistics
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
from ppmt.core.metadata import BlockLifecycleMetadata  # noqa: F401
from ppmt.core.regime import RegimeDetector

ALPHA = 4
WINDOW = 7
PATTERN_LEN = 5
MIN_CONFIDENCE = 0.15
MIN_SIMILARITY = 0.70  # not enforced in this simplified audit (depth=5 exact match)

DATA_DIR = Path("/home/z/my-project/download/real_data_1m_v4")

MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MEMES  = ["PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT"]
ALTS   = ["LINKUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "APTUSDT", "INJUSDT", "TIAUSDT"]

SYMBOLS = MAJORS + MEMES + ALTS
TOKEN_CLASSES = {s: "major" for s in MAJORS}
TOKEN_CLASSES.update({s: "meme" for s in MEMES})
TOKEN_CLASSES.update({s: "alt" for s in ALTS})

TRAIN_CANDLES = 150_000   # ~104 days
TEST_CANDLES  = 50_000    # ~35 days OOS

OUT_DIR = Path("/home/z/my-project/download/trie_stats_1m_v4")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_df(symbol: str) -> pd.DataFrame:
    csv_path = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(csv_path)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return df


def build_tries(df_train: pd.DataFrame, symbol: str) -> tuple[PPMTTrie, RegimePartitionedTrie, int, dict]:
    """Build BOTH N3 (regime-agnostic) and N4 (regime-partitioned) tries."""
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime_detector = RegimeDetector()
    symbols = sax.encode(df_train)

    trie_n3 = PPMTTrie(name=f"per_asset:{symbol}")
    trie_n4 = RegimePartitionedTrie(name=f"per_asset_regime:{symbol}")

    count = 0
    regime_dist: dict[str, int] = {}
    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        next_sym = symbols[i + PATTERN_LEN] if i + PATTERN_LEN < len(symbols) else None
        start_candle = i * WINDOW
        end_candle = (i + PATTERN_LEN) * WINDOW
        if end_candle > len(df_train):
            break
        window_df = df_train.iloc[start_candle:end_candle]
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
        regime_name = regime.name if hasattr(regime, "name") else str(regime)
        regime_dist[regime_name] = regime_dist.get(regime_name, 0) + 1

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
        count += 1
    return trie_n3, trie_n4, count, regime_dist


def predict_n3(trie_n3: PPMTTrie, pattern: tuple) -> dict | None:
    node = trie_n3.root
    for sym in pattern:
        if sym not in node.children:
            return None
        node = node.children[sym]
    meta = node.metadata
    if meta.historical_count < 1:
        return None
    conf = float(meta.confidence)
    if conf < MIN_CONFIDENCE:
        return None
    expected_move = float(getattr(meta, "expected_move_pct", 0.0))
    direction = "LONG" if expected_move > 0 else "SHORT"
    return {
        "direction": direction, "confidence": conf,
        "expected_move_pct": expected_move,
        "historical_count": meta.historical_count,
        "win_rate": float(meta.win_rate),
        "engine": "N3",
    }


def predict_n4(trie_n4: RegimePartitionedTrie, pattern: tuple, regime: str) -> dict | None:
    if regime not in trie_n4.sub_tries:
        trie_n4.set_current_regime("ranging")
    else:
        trie_n4.set_current_regime(regime)
    sub_trie = trie_n4.sub_tries[trie_n4.get_current_regime()]
    node = sub_trie.root
    for sym in pattern:
        if sym not in node.children:
            return None
        node = node.children[sym]
    meta = node.metadata
    if meta.historical_count < 1:
        return None
    conf = float(meta.confidence)
    if conf < MIN_CONFIDENCE:
        return None
    expected_move = float(getattr(meta, "expected_move_pct", 0.0))
    direction = "LONG" if expected_move > 0 else "SHORT"
    return {
        "direction": direction, "confidence": conf,
        "expected_move_pct": expected_move,
        "historical_count": meta.historical_count,
        "win_rate": float(meta.win_rate),
        "engine": "N4",
        "regime": regime,
    }


def walk_forward(df_test: pd.DataFrame, trie_n3: PPMTTrie, trie_n4: RegimePartitionedTrie) -> dict[str, Any]:
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime_detector = RegimeDetector()
    symbols = sax.encode(df_test)

    stats = {
        "N3": _new_engine_stats(),
        "N4": _new_engine_stats(),
    }

    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        fire_candle = (i + PATTERN_LEN) * WINDOW
        end_outcome = fire_candle + PATTERN_LEN * WINDOW
        if end_outcome > len(df_test):
            break

        lookback_start = max(0, fire_candle - 50)
        regime_info = regime_detector.detect(df_test["close"].iloc[lookback_start:fire_candle].values.astype(float))
        current_regime = regime_info.name if hasattr(regime_info, 'name') else str(regime_info)

        entry_price = df_test["close"].iloc[fire_candle - 1]
        exit_price = df_test["close"].iloc[end_outcome - 1]
        actual_move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        pred_n3 = predict_n3(trie_n3, pattern)
        if pred_n3:
            _record_prediction(stats["N3"], pred_n3, actual_move_pct, current_regime)

        pred_n4 = predict_n4(trie_n4, pattern, current_regime)
        if pred_n4:
            _record_prediction(stats["N4"], pred_n4, actual_move_pct, current_regime)

    for engine_name, s in stats.items():
        s["hit_rate"] = round(s["n_hits"] / max(s["n_pred"], 1), 4)
        s["pnl_total"] = round(sum(s["pnl_all"]), 2)
        s["pnl_long_total"] = round(sum(s["pnl_long"]), 2)
        s["pnl_short_total"] = round(sum(s["pnl_short"]), 2)
        s["pnl_avg"] = round(statistics.mean(s["pnl_all"]), 4) if s["pnl_all"] else 0.0
        s["ls_ratio"] = round(s["n_long"] / max(s["n_short"], 1), 2)
        s.pop("pnl_long"); s.pop("pnl_short"); s.pop("pnl_all")

    return stats


def _new_engine_stats() -> dict:
    return {
        "n_pred": 0, "n_hits": 0, "n_long": 0, "n_short": 0,
        "pnl_long": [], "pnl_short": [], "pnl_all": [],
        "regime_breakdown": {"trending_up": 0, "trending_down": 0, "ranging": 0, "volatile": 0},
    }


def _record_prediction(s: dict, pred: dict, actual_move_pct: float, regime: str) -> None:
    s["n_pred"] += 1
    if regime in s["regime_breakdown"]:
        s["regime_breakdown"][regime] += 1

    if pred["direction"] == "LONG":
        s["n_long"] += 1
        if actual_move_pct > 0:
            s["n_hits"] += 1
        pnl = actual_move_pct
        s["pnl_long"].append(pnl)
        s["pnl_all"].append(pnl)
    else:
        s["n_short"] += 1
        if actual_move_pct < 0:
            s["n_hits"] += 1
        pnl = -actual_move_pct
        s["pnl_short"].append(pnl)
        s["pnl_all"].append(pnl)


def main():
    print(f"PPMT Capa 1 Audit v4 — N3 vs N4 (FIX-14) on expanded dataset")
    print(f"Config: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}, min_conf={MIN_CONFIDENCE}")
    print(f"Train: {TRAIN_CANDLES:,} (~{TRAIN_CANDLES/60/24:.0f}d) | Test: {TEST_CANDLES:,} (~{TEST_CANDLES/60/24:.0f}d)")
    print(f"Tokens: {len(MAJORS)} majors + {len(MEMES)} memes + {len(ALTS)} alts = {len(SYMBOLS)} total\n")

    all_results = {}
    train_stats = {}
    for sym in SYMBOLS:
        print(f"\n=== {sym} ({TOKEN_CLASSES[sym]}) ===")
        try:
            df = load_df(sym)
            print(f"  Loaded: {len(df):,} candles")
            if len(df) < TRAIN_CANDLES + TEST_CANDLES:
                print(f"  Skipping {sym}: only {len(df)} candles (need {TRAIN_CANDLES + TEST_CANDLES})")
                continue
            df_train = df.iloc[:TRAIN_CANDLES].reset_index(drop=True)
            df_test  = df.iloc[TRAIN_CANDLES:TRAIN_CANDLES + TEST_CANDLES].reset_index(drop=True)

            print(f"  Building N3 + N4 tries...")
            trie_n3, trie_n4, n_ins, regime_dist = build_tries(df_train, sym)
            train_stats[sym] = {"n_patterns_inserted": n_ins, "regime_distribution": regime_dist}
            print(f"  Built: {n_ins:,} patterns inserted | regimes: {regime_dist}")

            print(f"  Walk-forward test (N3 vs N4)...")
            res = walk_forward(df_test, trie_n3, trie_n4)
            all_results[sym] = res

            for engine_name in ["N3", "N4"]:
                s = res[engine_name]
                print(f"  [{engine_name}] Signals={s['n_pred']:,} (L={s['n_long']} S={s['n_short']} L/S={s['ls_ratio']}) "
                      f"Hit={s['hit_rate']*100:.1f}% "
                      f"PnL={s['pnl_total']:+.2f}% (LONG={s['pnl_long_total']:+.2f}% SHORT={s['pnl_short_total']:+.2f}%)")
        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()
            all_results[sym] = {"error": str(e)}

    # Save raw results
    out_json = OUT_DIR / "layer1_v4_walkforward.json"
    with open(out_json, "w") as f:
        json.dump({"results": all_results, "train_stats": train_stats}, f, indent=2, default=str)
    print(f"\nResults saved to {out_json}")

    # Aggregate by class
    agg = {
        "overall": {"N3": _aggregate(all_results, "N3", SYMBOLS), "N4": _aggregate(all_results, "N4", SYMBOLS)},
        "majors":  {"N3": _aggregate(all_results, "N3", MAJORS),  "N4": _aggregate(all_results, "N4", MAJORS)},
        "memes":   {"N3": _aggregate(all_results, "N3", MEMES),   "N4": _aggregate(all_results, "N4", MEMES)},
        "alts":    {"N3": _aggregate(all_results, "N3", ALTS),    "N4": _aggregate(all_results, "N4", ALTS)},
    }

    print(f"\n=== AGGREGATE (overall) ===")
    for engine_name in ["N3", "N4"]:
        a = agg["overall"][engine_name]
        if not a:
            continue
        print(f"\n[{engine_name}] ({a['n_tokens']} tokens)")
        print(f"  Signals: {a['total_signals']:,} (LONG={a['total_long']:,} SHORT={a['total_short']:,} L/S={a['overall_ls_ratio']:.2f})")
        print(f"  Hit rate: {a['avg_hit_rate']*100:.1f}%")
        print(f"  PnL total: {a['total_pnl']:+.2f}% | LONG={a['total_pnl_long']:+.2f}% SHORT={a['total_pnl_short']:+.2f}%")

    # Build markdown summary
    md = []
    md.append("# Capa 1 Audit v4 — N3 vs N4 (FIX-14) on expanded dataset\n")
    md.append(f"**Versión**: v0.40.11 | Dataset: 16 tokens (5 majors + 4 memes + 7 alts) × 200k candles 1m")
    md.append(f"**Config**: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN} | Min conf={MIN_CONFIDENCE}")
    md.append(f"**Split**: Train {TRAIN_CANDLES:,} candles (~{TRAIN_CANDLES/60/24:.0f}d) | Test {TEST_CANDLES:,} candles (~{TEST_CANDLES/60/24:.0f}d OOS)")
    md.append(f"**Total OOS candles**: {TEST_CANDLES * len(SYMBOLS):,} ({len(SYMBOLS)} tokens × {TEST_CANDLES:,})\n")

    md.append("\n## Resultado agregado (overall)\n")
    md.append("| Métrica | N3-only (baseline) | N4-regime (FIX-14) | Delta |")
    md.append("|---|---:|---:|---:|")
    _agg_row(md, agg["overall"], "Señales totales", "total_signals", fmt="d", delta_fmt="pct")
    _agg_row(md, agg["overall"], "LONG signals", "total_long", fmt="d", delta_fmt="pct")
    _agg_row(md, agg["overall"], "SHORT signals", "total_short", fmt="d", delta_fmt="pct")
    _agg_row(md, agg["overall"], "L/S ratio", "overall_ls_ratio", fmt=".2f", delta_fmt="pct")
    _agg_row(md, agg["overall"], "Hit rate", "avg_hit_rate", fmt="pct", delta_fmt="pp")
    _agg_row(md, agg["overall"], "PnL total", "total_pnl", fmt="pct", delta_fmt="pp")
    _agg_row(md, agg["overall"], "PnL LONG", "total_pnl_long", fmt="pct", delta_fmt="pp")
    _agg_row(md, agg["overall"], "PnL SHORT", "total_pnl_short", fmt="pct", delta_fmt="pp")
    _agg_row(md, agg["overall"], "PnL/señal", "overall_pnl_per_signal", fmt="pct4", delta_fmt="pp4")

    md.append("\n## Resultado por clase de token\n")
    for class_name, label in [("majors", "Majors (BTC, ETH, SOL, BNB, XRP)"), ("memes", "Memes (PEPE, WIF, BONK, FLOKI)"), ("alts", "Alts (LINK, ARB, OP, SUI, APT, INJ, TIA)")]:
        md.append(f"\n### {label}\n")
        md.append("| Métrica | N3-only | N4-regime | Delta |")
        md.append("|---|---:|---:|---:|")
        _agg_row(md, agg[class_name], "Señales", "total_signals", fmt="d", delta_fmt="pct")
        _agg_row(md, agg[class_name], "L/S ratio", "overall_ls_ratio", fmt=".2f", delta_fmt="pct")
        _agg_row(md, agg[class_name], "Hit rate", "avg_hit_rate", fmt="pct", delta_fmt="pp")
        _agg_row(md, agg[class_name], "PnL total", "total_pnl", fmt="pct", delta_fmt="pp")
        _agg_row(md, agg[class_name], "PnL LONG", "total_pnl_long", fmt="pct", delta_fmt="pp")
        _agg_row(md, agg[class_name], "PnL SHORT", "total_pnl_short", fmt="pct", delta_fmt="pp")

    md.append("\n## Resultado por token\n")
    md.append("| Token | Class | Engine | Señales | LONG | SHORT | L/S | Hit | PnL total | PnL LONG | PnL SHORT |")
    md.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sym in SYMBOLS:
        r = all_results.get(sym, {})
        if "N3" not in r:
            continue
        for engine_name in ["N3", "N4"]:
            s = r[engine_name]
            md.append(f"| {sym} | {TOKEN_CLASSES[sym]} | {engine_name} | {s['n_pred']:,} | {s['n_long']:,} | {s['n_short']:,} | "
                      f"{s['ls_ratio']:.2f} | {s['hit_rate']*100:.1f}% | "
                      f"{s['pnl_total']:+.2f}% | {s['pnl_long_total']:+.2f}% | {s['pnl_short_total']:+.2f}% |")

    md.append("\n## Distribución de regímenes en el train set\n")
    md.append("| Token | trending_up | trending_down | ranging | volatile |")
    md.append("|---|---:|---:|---:|---:|")
    for sym in SYMBOLS:
        ts = train_stats.get(sym, {})
        rd = ts.get("regime_distribution", {})
        md.append(f"| {sym} | {rd.get('trending_up', 0):,} | {rd.get('trending_down', 0):,} | {rd.get('ranging', 0):,} | {rd.get('volatile', 0):,} |")

    md.append("\n## Veredicto\n")
    n3 = agg["overall"]["N3"]
    n4 = agg["overall"]["N4"]
    if not n3 or not n4:
        md.append("No se pudo calcular veredicto (data insuficiente).")
    else:
        delta_pnl = n4["total_pnl"] - n3["total_pnl"]
        delta_long = n4["total_pnl_long"] - n3["total_pnl_long"]
        delta_short = n4["total_pnl_short"] - n3["total_pnl_short"]
        if delta_pnl > 0 and n4["total_pnl"] > 0:
            md.append(f"**FIX-14 MEJORA EL EDGE**: PnL {n3['total_pnl']:+.2f}% → {n4['total_pnl']:+.2f}% (+{delta_pnl:.2f}pp).")
        elif delta_pnl > 0:
            md.append(f"**FIX-14 MEJORA PERO NO SUFICIENTE**: PnL {n3['total_pnl']:+.2f}% → {n4['total_pnl']:+.2f}% (+{delta_pnl:.2f}pp). Aún negativo.")
        else:
            md.append(f"**FIX-14 NO MEJORA**: PnL {n3['total_pnl']:+.2f}% → {n4['total_pnl']:+.2f}% ({delta_pnl:+.2f}pp).")
        md.append(f"\nL/S ratio: {n3['overall_ls_ratio']:.2f} → {n4['overall_ls_ratio']:.2f}")
        md.append(f"\nPnL LONG: {n3['total_pnl_long']:+.2f}% → {n4['total_pnl_long']:+.2f}% "
                  f"({'MEJORA' if delta_long > 0 else 'EMPEORA'} {delta_long:+.2f}pp)")
        md.append(f"\nPnL SHORT: {n3['total_pnl_short']:+.2f}% → {n4['total_pnl_short']:+.2f}% "
                  f"({'MEJORA' if delta_short > 0 else 'EMPEORA'} {delta_short:+.2f}pp)")

    md_path = OUT_DIR / "layer1_v4_summary.md"
    with open(md_path, "w") as f:
        f.write("\n".join(md))
    print(f"\nMarkdown saved to {md_path}")

    with open(OUT_DIR / "layer1_v4_aggregate.json", "w") as f:
        json.dump(agg, f, indent=2, default=str)


def _agg_row(md: list, agg_dict: dict, label: str, key: str, fmt: str, delta_fmt: str) -> None:
    n3 = agg_dict.get("N3", {})
    n4 = agg_dict.get("N4", {})
    if not n3 or not n4:
        return
    v3 = n3.get(key, 0)
    v4 = n4.get(key, 0)
    if fmt == "d":
        s3, s4 = f"{v3:,}", f"{v4:,}"
    elif fmt == ".2f":
        s3, s4 = f"{v3:.2f}", f"{v4:.2f}"
    elif fmt == "pct":
        s3, s4 = f"{v3*100:.1f}%", f"{v4*100:.1f}%"
    elif fmt == "pct4":
        s3, s4 = f"{v3*100:.4f}%", f"{v4*100:.4f}%"
    else:
        s3, s4 = str(v3), str(v4)
    if delta_fmt == "pct":
        denom = max(abs(v3), 0.01)
        delta = f"{(v4-v3)/denom*100:+.1f}%"
    elif delta_fmt == "pp":
        delta = f"{v4-v3:+.2f}pp"
    elif delta_fmt == "pp4":
        delta = f"{v4-v3:+.4f}pp"
    else:
        delta = f"{v4-v3:+}"
    md.append(f"| {label} | {s3} | {s4} | {delta} |")


def _aggregate(all_results: dict, engine_name: str, symbols: list[str]) -> dict:
    rows = []
    for sym in symbols:
        res = all_results.get(sym, {})
        if "error" in res:
            continue
        s = res.get(engine_name, {})
        if "n_pred" not in s:
            continue
        rows.append((sym, s))
    if not rows:
        return {}
    return {
        "n_tokens": len(rows),
        "total_signals": sum(s["n_pred"] for _, s in rows),
        "total_long": sum(s["n_long"] for _, s in rows),
        "total_short": sum(s["n_short"] for _, s in rows),
        "total_hits": sum(s["n_hits"] for _, s in rows),
        "total_pnl_long": round(sum(s["pnl_long_total"] for _, s in rows), 2),
        "total_pnl_short": round(sum(s["pnl_short_total"] for _, s in rows), 2),
        "total_pnl": round(sum(s["pnl_total"] for _, s in rows), 2),
        "avg_hit_rate": round(statistics.mean(s["hit_rate"] for _, s in rows), 4),
        "avg_ls_ratio": round(statistics.mean(s["ls_ratio"] for _, s in rows), 2),
        "overall_ls_ratio": round(
            sum(s["n_long"] for _, s in rows) /
            max(sum(s["n_short"] for _, s in rows), 1), 2),
        "overall_pnl_per_signal": round(
            sum(s["pnl_total"] for _, s in rows) /
            max(sum(s["n_pred"] for _, s in rows), 1), 4),
    }


if __name__ == "__main__":
    main()
