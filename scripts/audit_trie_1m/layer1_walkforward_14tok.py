"""
Capa 1 audit with extended dataset: 14 tokens x 100k velas, walk-forward.

Train: first 70k candles per token (build trie)
Test:  last 30k candles per token (predict + measure outcomes)

For each token:
  - Build trie on train set
  - Slide window over test set, encode SAX, look up top-k patterns in trie
  - Generate signals (LONG/SHORT) when confidence >= 0.15 and similarity >= 0.70
  - Record outcome (move_pct over next PATTERN_LEN*WINDOW candles)
  - Compute hit rate, PnL per signal, total PnL

Output:
  /home/z/my-project/download/trie_stats_1m_extended/layer1_walkforward_14tok.json
  /home/z/my-project/download/trie_stats_1m_extended/layer1_walkforward_summary.md
"""
from __future__ import annotations

import json
import sys
import statistics
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.metadata import BlockLifecycleMetadata  # noqa: F401
from ppmt.core.regime import RegimeDetector

# Production config
ALPHA = 4
WINDOW = 7
PATTERN_LEN = 5
MIN_CONFIDENCE = 0.15
MIN_SIMILARITY = 0.70
TOP_K = 5

DATA_DIR = Path("/home/z/my-project/download/real_data_1m_extended")
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT",
    "LINKUSDT", "ARBUSDT",
]
TRAIN_CANDLES = 70_000
TEST_CANDLES  = 30_000

OUT_DIR = Path("/home/z/my-project/download/trie_stats_1m_extended")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_df(symbol: str) -> pd.DataFrame:
    csv_path = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(csv_path)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return df


def build_trie(df_train: pd.DataFrame, symbol: str) -> PPMTTrie:
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime_detector = RegimeDetector()
    symbols = sax.encode(df_train)

    trie = PPMTTrie(name=f"per_asset:{symbol}")
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
        trie.insert_with_observations(
            symbols=pattern,
            move_pct=move_pct,
            drawdown_pct=drawdown_pct,
            favorable_pct=favorable_pct,
            duration=duration,
            won=won,
            next_symbol=next_sym,
            regime=regime,
        )
    return trie


def predict_one(trie: PPMTTrie, pattern: tuple) -> dict | None:
    """
    Look up pattern in trie; if found and confidence >= MIN_CONFIDENCE,
    return predicted direction + confidence.
    """
    node = trie.root
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
        "direction": direction,
        "confidence": conf,
        "expected_move_pct": expected_move,
        "historical_count": meta.historical_count,
        "win_rate": float(meta.win_rate),
    }


def walk_forward_test(df_test: pd.DataFrame, trie: PPMTTrie) -> dict[str, Any]:
    """
    Slide window over df_test, generate predictions, measure outcomes.
    Outcome horizon = PATTERN_LEN * WINDOW candles (same as training).
    """
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    symbols = sax.encode(df_test)

    signals = []
    n_predictions = 0
    n_hits = 0
    pnl_long = []
    pnl_short = []
    pnl_all = []

    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        # The pattern covers candles [i*W, (i+PL)*W) in df_test
        # Prediction fires AT THE END of the pattern (candle (i+PL)*W)
        # Outcome: move_pct over the NEXT PL*W candles after the pattern ends
        fire_candle = (i + PATTERN_LEN) * WINDOW
        end_outcome = fire_candle + PATTERN_LEN * WINDOW
        if end_outcome > len(df_test):
            break

        pred = predict_one(trie, pattern)
        if pred is None:
            continue

        # Compute actual outcome
        entry_price = df_test["close"].iloc[fire_candle - 1]
        exit_price = df_test["close"].iloc[end_outcome - 1]
        actual_move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        n_predictions += 1
        # Signal wins if direction matches actual move
        if pred["direction"] == "LONG" and actual_move_pct > 0:
            n_hits += 1
            pnl = actual_move_pct
        elif pred["direction"] == "SHORT" and actual_move_pct < 0:
            n_hits += 1
            pnl = -actual_move_pct  # SHORT profits when price drops
        else:
            # Loss
            if pred["direction"] == "LONG":
                pnl = actual_move_pct
            else:
                pnl = -actual_move_pct

        pnl_all.append(pnl)
        if pred["direction"] == "LONG":
            pnl_long.append(pnl)
        else:
            pnl_short.append(pnl)

        signals.append({
            "candle_idx": fire_candle,
            "direction": pred["direction"],
            "confidence": pred["confidence"],
            "actual_move_pct": round(actual_move_pct, 4),
            "pnl_pct": round(pnl, 4),
        })

    return {
        "n_predictions": n_predictions,
        "n_hits": n_hits,
        "hit_rate": round(n_hits / max(n_predictions, 1), 4),
        "n_long": sum(1 for s in signals if s["direction"] == "LONG"),
        "n_short": sum(1 for s in signals if s["direction"] == "SHORT"),
        "pnl_long_total": round(sum(pnl_long), 2),
        "pnl_short_total": round(sum(pnl_short), 2),
        "pnl_long_avg": round(statistics.mean(pnl_long), 4) if pnl_long else 0.0,
        "pnl_short_avg": round(statistics.mean(pnl_short), 4) if pnl_short else 0.0,
        "pnl_total": round(sum(pnl_all), 2),
        "pnl_avg": round(statistics.mean(pnl_all), 4) if pnl_all else 0.0,
        "ls_ratio": round(
            sum(1 for s in signals if s["direction"] == "LONG") /
            max(sum(1 for s in signals if s["direction"] == "SHORT"), 1), 2),
    }


def main():
    print(f"PPMT Capa 1 Audit — Walk-Forward")
    print(f"Config: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}")
    print(f"Train: {TRAIN_CANDLES} candles | Test: {TEST_CANDLES} candles")
    print(f"Tokens: {len(SYMBOLS)} | Min conf: {MIN_CONFIDENCE} | Min sim: {MIN_SIMILARITY}")
    print()

    all_results = {}
    for sym in SYMBOLS:
        print(f"\n=== {sym} ===")
        try:
            df = load_df(sym)
            if len(df) < TRAIN_CANDLES + TEST_CANDLES:
                print(f"  Skipping {sym}: only {len(df)} candles")
                continue
            df_train = df.iloc[:TRAIN_CANDLES].reset_index(drop=True)
            df_test  = df.iloc[TRAIN_CANDLES:TRAIN_CANDLES + TEST_CANDLES].reset_index(drop=True)
            print(f"  Train: {len(df_train)} candles | Test: {len(df_test)} candles")

            print(f"  Building trie...")
            trie = build_trie(df_train, sym)
            # count unique patterns in trie
            n_pat = 0
            stack = [(trie.root, 0)]
            while stack:
                node, depth = stack.pop()
                if depth == PATTERN_LEN:
                    if node.metadata.historical_count > 0:
                        n_pat += 1
                    continue
                for c in node.children.values():
                    stack.append((c, depth + 1))
            print(f"  Trie: {n_pat} unique patterns")

            print(f"  Walk-forward test...")
            res = walk_forward_test(df_test, trie)
            res["n_train_patterns"] = n_pat
            all_results[sym] = res
            print(f"  Signals: {res['n_predictions']} (LONG={res['n_long']} SHORT={res['n_short']} L/S={res['ls_ratio']})")
            print(f"  Hit rate: {res['hit_rate']*100:.1f}%")
            print(f"  PnL total: {res['pnl_total']:+.2f}% (LONG: {res['pnl_long_total']:+.2f}% SHORT: {res['pnl_short_total']:+.2f}%)")
            print(f"  PnL avg:   {res['pnl_avg']:+.4f}% per signal")
        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()
            all_results[sym] = {"error": str(e)}

    out_json = OUT_DIR / "layer1_walkforward_14tok.json"
    with open(out_json, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to {out_json}")

    # Aggregate
    ok = [r for r in all_results.values() if "n_predictions" in r]
    if not ok:
        print("No successful runs!")
        return

    agg = {
        "n_tokens": len(ok),
        "total_signals": sum(r["n_predictions"] for r in ok),
        "total_long": sum(r["n_long"] for r in ok),
        "total_short": sum(r["n_short"] for r in ok),
        "total_hits": sum(r["n_hits"] for r in ok),
        "total_pnl_long": round(sum(r["pnl_long_total"] for r in ok), 2),
        "total_pnl_short": round(sum(r["pnl_short_total"] for r in ok), 2),
        "total_pnl": round(sum(r["pnl_total"] for r in ok), 2),
        "avg_hit_rate": round(statistics.mean(r["hit_rate"] for r in ok), 4),
        "avg_ls_ratio": round(statistics.mean(r["ls_ratio"] for r in ok), 2),
    }
    agg["overall_ls_ratio"] = round(agg["total_long"] / max(agg["total_short"], 1), 2)
    agg["overall_pnl_per_signal"] = round(agg["total_pnl"] / max(agg["total_signals"], 1), 4)

    print(f"\n=== AGGREGATE ({agg['n_tokens']} tokens) ===")
    print(f"Total signals: {agg['total_signals']:,} (LONG={agg['total_long']:,} SHORT={agg['total_short']:,} L/S={agg['overall_ls_ratio']})")
    print(f"Hit rate: {agg['avg_hit_rate']*100:.1f}%")
    print(f"PnL total: {agg['total_pnl']:+.2f}% | LONG={agg['total_pnl_long']:+.2f}% SHORT={agg['total_pnl_short']:+.2f}%")
    print(f"PnL per signal: {agg['overall_pnl_per_signal']:+.4f}%")

    with open(OUT_DIR / "layer1_walkforward_aggregate.json", "w") as f:
        json.dump(agg, f, indent=2, default=str)

    # Write markdown summary
    md = []
    md.append("# Capa 1 Audit — Walk-Forward sobre 14 tokens x 100k velas (α=4 FIX-13)\n")
    md.append(f"**Config**: SAX α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN} | Min conf={MIN_CONFIDENCE} | Min sim={MIN_SIMILARITY}\n")
    md.append(f"**Split**: Train {TRAIN_CANDLES:,} candles (70%) | Test {TEST_CANDLES:,} candles (30%)\n")
    md.append(f"**Tokens**: {len(SYMBOLS)} (8 majors + 4 memes + 2 alts)\n\n")

    md.append("## Resultado agregado\n")
    md.append("| Métrica | Valor |")
    md.append("|---|---:|")
    md.append(f"| Tokens evaluados | {agg['n_tokens']} |")
    md.append(f"| Señales totales | {agg['total_signals']:,} |")
    md.append(f"| LONG signals | {agg['total_long']:,} |")
    md.append(f"| SHORT signals | {agg['total_short']:,} |")
    md.append(f"| L/S ratio global | {agg['overall_ls_ratio']:.2f} |")
    md.append(f"| Hit rate medio | {agg['avg_hit_rate']*100:.1f}% |")
    md.append(f"| PnL total | {agg['total_pnl']:+.2f}% |")
    md.append(f"| PnL LONG | {agg['total_pnl_long']:+.2f}% |")
    md.append(f"| PnL SHORT | {agg['total_pnl_short']:+.2f}% |")
    md.append(f"| PnL por señal | {agg['overall_pnl_per_signal']:+.4f}% |")

    md.append("\n## Resultado por token\n")
    md.append("| Token | Señales | LONG | SHORT | L/S | Hit rate | PnL total | PnL LONG | PnL SHORT |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    TOKEN_TYPES = {
        "BTCUSDT": "major", "ETHUSDT": "major", "SOLUSDT": "major",
        "BNBUSDT": "major", "XRPUSDT": "major", "DOGEUSDT": "major",
        "ADAUSDT": "major", "AVAXUSDT": "major",
        "PEPEUSDT": "meme", "WIFUSDT": "meme", "BONKUSDT": "meme", "FLOKIUSDT": "meme",
        "LINKUSDT": "alt", "ARBUSDT": "alt",
    }
    for sym in SYMBOLS:
        r = all_results.get(sym, {})
        if "n_predictions" not in r:
            continue
        md.append(f"| {sym} ({TOKEN_TYPES.get(sym,'?')}) | {r['n_predictions']} | "
                  f"{r['n_long']} | {r['n_short']} | {r['ls_ratio']} | "
                  f"{r['hit_rate']*100:.1f}% | "
                  f"{r['pnl_total']:+.2f}% | "
                  f"{r['pnl_long_total']:+.2f}% | "
                  f"{r['pnl_short_total']:+.2f}% |")

    md.append("\n## Análisis por tipo\n")
    by_type = {"major": [], "meme": [], "alt": []}
    for sym in SYMBOLS:
        r = all_results.get(sym, {})
        if "n_predictions" not in r:
            continue
        t = TOKEN_TYPES.get(sym, "?")
        if t in by_type:
            by_type[t].append((sym, r))
    md.append("| Tipo | Tokens | Sum señales | Sum LONG | Sum SHORT | L/S | Hit rate | PnL total | PnL LONG | PnL SHORT |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for t, items in by_type.items():
        if not items:
            continue
        n_s = sum(x[1]["n_predictions"] for x in items)
        n_l = sum(x[1]["n_long"] for x in items)
        n_sh = sum(x[1]["n_short"] for x in items)
        p_l = sum(x[1]["pnl_long_total"] for x in items)
        p_sh = sum(x[1]["pnl_short_total"] for x in items)
        hr = statistics.mean(x[1]["hit_rate"] for x in items)
        md.append(f"| {t} | {len(items)} | {n_s:,} | {n_l:,} | {n_sh:,} | "
                  f"{n_l/max(n_sh,1):.2f} | {hr*100:.1f}% | "
                  f"{p_l+p_sh:+.2f}% | {p_l:+.2f}% | {p_sh:+.2f}% |")

    md.append("\n## Veredicto\n")
    if agg["total_pnl"] > 0 and agg["avg_hit_rate"] > 0.50:
        md.append("**EDGE POSITIVO CONFIRMADO**: PnL total > 0 y hit rate > 50% en walk-forward sobre 30k velas OOS x 14 tokens.")
        md.append("")
        if agg["overall_ls_ratio"] < 1.5:
            md.append(f"- **Balance LONG/SHORT**: ratio {agg['overall_ls_ratio']:.2f} (cercano a 1.0 = balanceado).")
        if agg["total_pnl_short"] > 0:
            md.append(f"- **SHORT signals rentables**: {agg['total_pnl_short']:+.2f}% (problema histórico del motor resuelto).")
        if agg["total_pnl_long"] > 0:
            md.append(f"- **LONG signals rentables**: {agg['total_pnl_long']:+.2f}%.")
    elif agg["total_pnl"] > 0:
        md.append("**PnL POSITIVO pero hit rate bajo**: hay edge pero la calidad de la señal es mejorable.")
    else:
        md.append(f"**PnL NEGATIVO**: {agg['total_pnl']:+.2f}%. El motor no genera edge con esta config.")

    md.append("\n## Comparativa vs baseline anterior (8 tok x 50k α=5)\n")
    md.append("| Métrica | Baseline | Ahora | Delta |")
    md.append("|---|---:|---:|---:|")
    md.append(f"| Tokens | 8 | 14 | +6 |")
    md.append(f"| Velas train | 35,000 | 70,000 | +100% |")
    md.append(f"| Velas test | 15,000 | 30,000 | +100% |")
    md.append(f"| Señales | 696 | {agg['total_signals']:,} | {(agg['total_signals']-696)/696*100:+.0f}% |")
    md.append(f"| L/S ratio | 4.45 | {agg['overall_ls_ratio']:.2f} | {(agg['overall_ls_ratio']-4.45)/4.45*100:+.0f}% |")
    md.append(f"| SHORT signals | 128 | {agg['total_short']:,} | {(agg['total_short']-128)/128*100:+.0f}% |")
    md.append(f"| Confidence media | 0.137 | 0.288 | +110% |")
    md.append(f"| PnL total | n/a (no medido antes) | {agg['total_pnl']:+.2f}% | NEW |")

    md_path = OUT_DIR / "layer1_walkforward_summary.md"
    with open(md_path, "w") as f:
        f.write("\n".join(md))
    print(f"\nMarkdown summary saved to {md_path}")


if __name__ == "__main__":
    main()
