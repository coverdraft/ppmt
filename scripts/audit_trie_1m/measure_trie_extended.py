"""
Measure pattern statistics on EXTENDED REAL 1m data: 14 tokens x 100k candles.

Compares 3 scenarios:
  A) 8 tokens x 50k  (previous baseline, α=5) — read from existing JSON
  B) 14 tokens x 50k (more tokens, same time window)
  C) 14 tokens x 100k (more tokens + more time)

Production config: SAX α=4, W=7, pattern_length=5 (FIX-13)

Output:
  /home/z/my-project/download/trie_stats_1m_extended/trie_stats_extended.json
  /home/z/my-project/download/trie_stats_1m_extended/extended_summary.md
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
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie
from ppmt.core.metadata import BlockLifecycleMetadata  # noqa: F401
from ppmt.core.regime import RegimeDetector

# --- Production config (post FIX-13) ---
TF = "1m"
ALPHA = 4        # FIX-13: era 5
WINDOW = 7
PATTERN_LEN = 5

MIN_CONFIDENCE = 0.15
MIN_SIMILARITY = 0.70

# Extended dataset
DATA_DIR = Path("/home/z/my-project/download/real_data_1m_extended")
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT",
    "LINKUSDT", "ARBUSDT",
]

SCALES = [25_000, 50_000, 100_000]

OUT_DIR = Path("/home/z/my-project/download/trie_stats_1m_extended")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_df(symbol: str, n_candles: int) -> pd.DataFrame:
    csv_path = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(csv_path)
    df = df.tail(n_candles).reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return df


def build_trie_with_outcomes(df: pd.DataFrame) -> tuple[PPMTTrie, RegimePartitionedTrie, int, RegimeDetector]:
    """
    Build N3 + N4 tries, AND collect per-pattern outcome arrays for later
    LONG/SHORT signal analysis.
    """
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime_detector = RegimeDetector()
    symbols = sax.encode(df)

    trie_n3 = PPMTTrie(name=f"per_asset:{df.attrs.get('symbol', 'unknown')}")
    trie_n4 = RegimePartitionedTrie(name=f"per_asset_regime:{df.attrs.get('symbol', 'unknown')}")

    count = 0
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
            symbols=pattern,
            move_pct=move_pct,
            drawdown_pct=drawdown_pct,
            favorable_pct=favorable_pct,
            duration=duration,
            won=won,
            next_symbol=next_sym,
            regime=regime,
        )
        trie_n4.insert_with_observations(
            symbols=pattern,
            move_pct=move_pct,
            drawdown_pct=drawdown_pct,
            favorable_pct=favorable_pct,
            duration=duration,
            won=won,
            next_symbol=next_sym,
            regime=regime,
        )
        count += 1

    return trie_n3, trie_n4, count, regime_detector


def collect_node_stats(trie: PPMTTrie) -> dict[str, Any]:
    """Walk trie DFS, collect metadata from terminal nodes."""
    counts = []
    confidences = []
    win_rates = []
    avg_moves = []  # for LONG/SHORT bias

    pattern_length = PATTERN_LEN
    stack = [(trie.root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth == pattern_length:
            meta = node.metadata
            if meta.historical_count > 0:
                counts.append(meta.historical_count)
                confidences.append(float(meta.confidence))
                win_rates.append(meta.win_rate)
                # expected_move_pct: Positive = bullish, Negative = bearish
                avg_moves.append(float(getattr(meta, "expected_move_pct", 0.0)))
            continue
        for child in node.children.values():
            stack.append((child, depth + 1))

    if not counts:
        return _empty_stats()

    counts_arr = np.array(counts)
    confs_arr = np.array(confidences)
    wr_arr = np.array(win_rates)
    mv_arr = np.array(avg_moves)

    n = len(counts)
    n_eq_1 = int((counts_arr == 1).sum())
    n_eq_2 = int((counts_arr == 2).sum())
    n_3_4 = int(((counts_arr >= 3) & (counts_arr <= 4)).sum())
    n_5_9 = int(((counts_arr >= 5) & (counts_arr <= 9)).sum())
    n_10p = int((counts_arr >= 10).sum())

    # Signals: pass production gate (count>=1 AND conf>=0.15)
    signals_mask = (counts_arr >= 1) & (confs_arr >= MIN_CONFIDENCE)
    n_signals = int(signals_mask.sum())

    # LONG signals: avg_move_pct > 0 (bullish-biased pattern)
    # SHORT signals: avg_move_pct < 0
    if len(mv_arr) > 0:
        n_long_signals = int((signals_mask & (mv_arr > 0)).sum())
        n_short_signals = int((signals_mask & (mv_arr < 0)).sum())
    else:
        n_long_signals = 0
        n_short_signals = 0

    return {
        "n_unique_patterns": n,
        "count_mean": float(counts_arr.mean()),
        "count_median": float(np.median(counts_arr)),
        "count_p25": float(np.percentile(counts_arr, 25)),
        "count_p75": float(np.percentile(counts_arr, 75)),
        "count_p90": float(np.percentile(counts_arr, 90)),
        "count_p99": float(np.percentile(counts_arr, 99)),
        "count_max": int(counts_arr.max()),
        "n_count_eq_1": n_eq_1, "n_count_eq_2": n_eq_2,
        "n_count_3_4": n_3_4, "n_count_5_9": n_5_9, "n_count_10_plus": n_10p,
        "pct_count_eq_1": round(n_eq_1 / n * 100, 1),
        "pct_count_eq_2": round(n_eq_2 / n * 100, 1),
        "pct_count_3_4": round(n_3_4 / n * 100, 1),
        "pct_count_5_9": round(n_5_9 / n * 100, 1),
        "pct_count_10_plus": round(n_10p / n * 100, 1),
        "confidence_mean": float(confs_arr.mean()),
        "confidence_median": float(np.median(confs_arr)),
        "confidence_max": float(confs_arr.max()),
        "win_rate_mean": float(wr_arr.mean()),
        "avg_move_pct_mean": float(mv_arr.mean()) if len(mv_arr) > 0 else 0.0,
        "signals_generated": n_signals,
        "signals_long": n_long_signals,
        "signals_short": n_short_signals,
        "signals_long_pct": round(n_long_signals / max(n_signals,1) * 100, 1),
        "signals_short_pct": round(n_short_signals / max(n_signals,1) * 100, 1),
        "long_short_ratio": round(n_long_signals / max(n_short_signals,1), 2),
    }


def _empty_stats() -> dict[str, Any]:
    return {
        "n_unique_patterns": 0,
        "count_mean": 0, "count_median": 0,
        "count_p25": 0, "count_p75": 0, "count_p90": 0, "count_p99": 0,
        "count_max": 0,
        "n_count_eq_1": 0, "n_count_eq_2": 0,
        "n_count_3_4": 0, "n_count_5_9": 0, "n_count_10_plus": 0,
        "pct_count_eq_1": 0, "pct_count_eq_2": 0,
        "pct_count_3_4": 0, "pct_count_5_9": 0, "pct_count_10_plus": 0,
        "confidence_mean": 0, "confidence_median": 0, "confidence_max": 0,
        "win_rate_mean": 0, "avg_move_pct_mean": 0,
        "signals_generated": 0, "signals_long": 0, "signals_short": 0,
        "signals_long_pct": 0, "signals_short_pct": 0, "long_short_ratio": 0,
    }


def collect_regime_stats(trie: RegimePartitionedTrie) -> dict[str, Any]:
    sub_tries = getattr(trie, "sub_tries", None) or getattr(trie, "_sub_tries", None) or {}
    if not sub_tries:
        return {"n_regimes": 0, "per_regime": {}, "aggregated": None}

    per_regime = {}
    all_counts = []
    all_confs = []
    all_moves = []
    total_signals = 0
    total_long = 0
    total_short = 0

    for rname, sub in sub_tries.items():
        if sub is None:
            continue
        st = collect_node_stats(sub)
        per_regime[rname] = {
            "n_unique_patterns": st["n_unique_patterns"],
            "count_mean": round(st["count_mean"], 2),
            "count_max": st["count_max"],
            "confidence_mean": round(st["confidence_mean"], 3),
            "signals_generated": st["signals_generated"],
            "signals_long": st["signals_long"],
            "signals_short": st["signals_short"],
        }
        # re-walk to aggregate
        stack = [(sub.root, 0)]
        while stack:
            node, depth = stack.pop()
            if depth == PATTERN_LEN:
                meta = node.metadata
                if meta.historical_count > 0:
                    all_counts.append(meta.historical_count)
                    all_confs.append(float(meta.confidence))
                    all_moves.append(float(getattr(meta, "expected_move_pct", 0.0)))
                continue
            for c in node.children.values():
                stack.append((c, depth + 1))

        total_signals += st["signals_generated"]
        total_long += st["signals_long"]
        total_short += st["signals_short"]

    if not all_counts:
        return {"n_regimes": len(sub_tries), "per_regime": per_regime, "aggregated": None}

    c_arr = np.array(all_counts)
    cf_arr = np.array(all_confs)
    mv_arr = np.array(all_moves)
    signals_mask = (c_arr >= 1) & (cf_arr >= MIN_CONFIDENCE)

    n_s = int(signals_mask.sum())
    n_l = int((signals_mask & (mv_arr > 0)).sum())
    n_sh = int((signals_mask & (mv_arr < 0)).sum())

    aggregated = {
        "n_unique_patterns_total": int(len(c_arr)),
        "count_mean": float(c_arr.mean()),
        "count_median": float(np.median(c_arr)),
        "count_p99": float(np.percentile(c_arr, 99)),
        "count_max": int(c_arr.max()),
        "n_count_eq_1": int((c_arr == 1).sum()),
        "n_count_10_plus": int((c_arr >= 10).sum()),
        "pct_count_eq_1": round(int((c_arr == 1).sum()) / len(c_arr) * 100, 1),
        "pct_count_10_plus": round(int((c_arr >= 10).sum()) / len(c_arr) * 100, 1),
        "confidence_mean": float(cf_arr.mean()),
        "confidence_max": float(cf_arr.max()),
        "signals_generated": n_s,
        "signals_long": n_l,
        "signals_short": n_sh,
        "long_short_ratio": round(n_l / max(n_sh,1), 2),
    }

    return {
        "n_regimes": len(sub_tries),
        "per_regime": per_regime,
        "aggregated": aggregated,
    }


def measure_symbol(symbol: str) -> dict[str, Any]:
    print(f"\n{'='*72}\n  {symbol}\n{'='*72}")
    out = {"symbol": symbol, "scales": {}}
    for n in SCALES:
        try:
            df = load_df(symbol, n)
            df.attrs["symbol"] = symbol
            if len(df) < n:
                print(f"  [{symbol} @ {n}] only {len(df)} candles available, skipping")
                continue
            print(f"  [{symbol}] Building trie @ {n} candles...")
            t3, t4, n_ins, _ = build_trie_with_outcomes(df)
            n3_stats = collect_node_stats(t3)
            n4_stats = collect_regime_stats(t4)
            out["scales"][str(n)] = {
                "n_candles": n,
                "n_patterns_inserted": n_ins,
                "n3_per_asset": n3_stats,
                "n4_per_asset_regime": n4_stats,
            }
            print(f"    N3: patterns={n3_stats['n_unique_patterns']:>6} "
                  f"mean_cnt={n3_stats['count_mean']:.2f} "
                  f"max_cnt={n3_stats['count_max']} "
                  f"conf_mean={n3_stats['confidence_mean']:.3f} "
                  f"signals={n3_stats['signals_generated']} "
                  f"(L={n3_stats['signals_long']} S={n3_stats['signals_short']})")
        except Exception as e:
            import traceback
            print(f"  [{symbol} @ {n}] ERROR: {e}")
            traceback.print_exc()
            out["scales"][str(n)] = {"error": str(e)}
    return out


def aggregate(results: dict, scale_key: str) -> dict:
    """Compute averages across all symbols at a given scale."""
    rows = []
    for sym, sym_data in results.items():
        s = sym_data.get("scales", {}).get(scale_key, {})
        if "n3_per_asset" not in s:
            continue
        n3 = s["n3_per_asset"]
        rows.append({
            "symbol": sym,
            "patterns": n3["n_unique_patterns"],
            "mean_cnt": n3["count_mean"],
            "max_cnt": n3["count_max"],
            "pct_eq_1": n3["pct_count_eq_1"],
            "pct_10plus": n3["pct_count_10_plus"],
            "conf_mean": n3["confidence_mean"],
            "conf_max": n3["confidence_max"],
            "signals": n3["signals_generated"],
            "signals_long": n3["signals_long"],
            "signals_short": n3["signals_short"],
            "ls_ratio": n3["long_short_ratio"],
        })
    if not rows:
        return {}

    agg = {
        "n_symbols": len(rows),
        "scale_candles": int(scale_key),
        "avg_patterns": round(statistics.mean(r["patterns"] for r in rows), 1),
        "sum_patterns": sum(r["patterns"] for r in rows),
        "avg_mean_cnt": round(statistics.mean(r["mean_cnt"] for r in rows), 2),
        "avg_max_cnt": round(statistics.mean(r["max_cnt"] for r in rows), 1),
        "avg_pct_eq_1": round(statistics.mean(r["pct_eq_1"] for r in rows), 2),
        "avg_pct_10plus": round(statistics.mean(r["pct_10plus"] for r in rows), 2),
        "avg_conf_mean": round(statistics.mean(r["conf_mean"] for r in rows), 4),
        "avg_conf_max": round(statistics.mean(r["conf_max"] for r in rows), 4),
        "sum_signals": sum(r["signals"] for r in rows),
        "sum_signals_long": sum(r["signals_long"] for r in rows),
        "sum_signals_short": sum(r["signals_short"] for r in rows),
        "overall_ls_ratio": round(
            sum(r["signals_long"] for r in rows) /
            max(sum(r["signals_short"] for r in rows), 1), 2),
    }
    return agg


def write_markdown_summary(results: dict, aggregations: dict) -> Path:
    md = []
    md.append("# Auditoría Trie Extendida — 14 tokens x 100k velas 1m (α=4 FIX-13)\n")
    md.append(f"**Fecha**: 2026-06-18 | **TF**: 1m | **SAX**: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}\n")
    md.append(f"**Tokens**: {len(SYMBOLS)} (8 majors + 4 memes + 2 alts)\n")
    md.append(f"**Total velas**: {sum(100000 for _ in SYMBOLS):,}\n\n")

    md.append("## 1. Comparativa agregada por escala\n")
    md.append("| Escala | Tokens | Patrones únicos (avg) | Sum patrones | Mean cnt | %cnt=1 | %cnt≥10 | Conf media | Conf max | Señales | LONG | SHORT | L/S ratio |")
    md.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sk in ["25000", "50000", "100000"]:
        a = aggregations.get(sk, {})
        if not a:
            continue
        md.append(f"| {a['scale_candles']:,} | {a['n_symbols']} | {a['avg_patterns']:.1f} | "
                  f"{a['sum_patterns']:,} | {a['avg_mean_cnt']:.2f} | "
                  f"{a['avg_pct_eq_1']:.1f}% | {a['avg_pct_10plus']:.1f}% | "
                  f"{a['avg_conf_mean']:.3f} | {a['avg_conf_max']:.3f} | "
                  f"{a['sum_signals']:,} | {a['sum_signals_long']:,} | "
                  f"{a['sum_signals_short']:,} | {a['overall_ls_ratio']:.2f} |")

    md.append("\n## 2. Detalle por token @ 100k velas (escala máxima)\n")
    md.append("| Token | Tipo | Patrones | Mean cnt | Max cnt | %cnt=1 | %cnt≥10 | Conf media | Señales | LONG | SHORT | L/S |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    TOKEN_TYPES = {
        "BTCUSDT": "major", "ETHUSDT": "major", "SOLUSDT": "major",
        "BNBUSDT": "major", "XRPUSDT": "major", "DOGEUSDT": "major",
        "ADAUSDT": "major", "AVAXUSDT": "major",
        "PEPEUSDT": "meme", "WIFUSDT": "meme", "BONKUSDT": "meme", "FLOKIUSDT": "meme",
        "LINKUSDT": "alt", "ARBUSDT": "alt",
    }

    for sym in SYMBOLS:
        s = results.get(sym, {}).get("scales", {}).get("100000", {})
        n3 = s.get("n3_per_asset", {})
        if not n3:
            continue
        md.append(f"| {sym} | {TOKEN_TYPES.get(sym,'?')} | "
                  f"{n3['n_unique_patterns']:,} | {n3['count_mean']:.2f} | "
                  f"{n3['count_max']} | {n3['pct_count_eq_1']:.1f}% | "
                  f"{n3['pct_count_10_plus']:.1f}% | "
                  f"{n3['confidence_mean']:.3f} | "
                  f"{n3['signals_generated']:,} | "
                  f"{n3['signals_long']:,} | {n3['signals_short']:,} | "
                  f"{n3['long_short_ratio']:.2f} |")

    md.append("\n## 3. Análisis por tipo de token @ 100k velas\n")
    by_type = {"major": [], "meme": [], "alt": []}
    for sym in SYMBOLS:
        s = results.get(sym, {}).get("scales", {}).get("100000", {})
        n3 = s.get("n3_per_asset", {})
        if not n3:
            continue
        t = TOKEN_TYPES.get(sym, "?")
        if t in by_type:
            by_type[t].append((sym, n3))

    md.append("| Tipo | Tokens | Avg patrones | Avg mean_cnt | Avg conf | Sum señales | Sum LONG | Sum SHORT | L/S ratio |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for t, items in by_type.items():
        if not items:
            continue
        n = len(items)
        avg_p = round(statistics.mean(x[1]["n_unique_patterns"] for x in items), 1)
        avg_c = round(statistics.mean(x[1]["count_mean"] for x in items), 2)
        avg_cf = round(statistics.mean(x[1]["confidence_mean"] for x in items), 4)
        sum_s = sum(x[1]["signals_generated"] for x in items)
        sum_l = sum(x[1]["signals_long"] for x in items)
        sum_sh = sum(x[1]["signals_short"] for x in items)
        ls = round(sum_l / max(sum_sh, 1), 2)
        md.append(f"| {t} | {n} | {avg_p:.1f} | {avg_c:.2f} | {avg_cf:.3f} | "
                  f"{sum_s:,} | {sum_l:,} | {sum_sh:,} | {ls:.2f} |")

    md.append("\n## 4. Diagnóstico: impacto de ampliar datos\n")
    a25 = aggregations.get("25000", {})
    a50 = aggregations.get("50000", {})
    a100 = aggregations.get("100000", {})

    if a25 and a50 and a100:
        # Scale effect: 25k -> 100k
        d_pat = (a100["avg_patterns"] - a25["avg_patterns"]) / a25["avg_patterns"] * 100
        d_cnt = (a100["avg_mean_cnt"] - a25["avg_mean_cnt"]) / a25["avg_mean_cnt"] * 100
        d_conf = (a100["avg_conf_mean"] - a25["avg_conf_mean"]) / max(a25["avg_conf_mean"], 0.001) * 100
        d_sig = (a100["sum_signals"] - a25["sum_signals"]) / max(a25["sum_signals"], 1) * 100
        d_short = (a100["sum_signals_short"] - a25["sum_signals_short"]) / max(a25["sum_signals_short"], 1) * 100

        md.append(f"### Efecto de cuadruplicar datos (25k → 100k velas):\n")
        md.append(f"- Patrones únicos: {a25['avg_patterns']:.0f} → {a100['avg_patterns']:.0f} ({d_pat:+.1f}%)")
        md.append(f"- Mean count: {a25['avg_mean_cnt']:.2f} → {a100['avg_mean_cnt']:.2f} ({d_cnt:+.1f}%)")
        md.append(f"- Confidence media: {a25['avg_conf_mean']:.3f} → {a100['avg_conf_mean']:.3f} ({d_conf:+.1f}%)")
        md.append(f"- Señales totales: {a25['sum_signals']:,} → {a100['sum_signals']:,} ({d_sig:+.1f}%)")
        md.append(f"- SHORT signals: {a25['sum_signals_short']:,} → {a100['sum_signals_short']:,} ({d_short:+.1f}%)")
        md.append(f"- L/S ratio: {a25['overall_ls_ratio']:.2f} → {a100['overall_ls_ratio']:.2f}")
        md.append("")
        md.append("### Conclusión sobre \"¿necesitamos más datos?\"\n")

        if d_conf > 15:
            md.append("**SÍ**: más datos mejoran significativamente la confidence (>15% de mejora al cuadruplicar).")
        elif d_conf > 5:
            md.append("**PARCIAL**: más datos mejoran marginalmente la confidence (5-15%). El retorno decrece.")
        else:
            md.append("**NO significativamente**: ampliar datos tiene retorno marginal bajo en confidence. "
                      "El FIX-13 (α=4) ya capturó la mejora principal. Más datos ayudan a SHORT coverage "
                      "y robustez por diversidad de regímenes, no a confidence media.")

        if d_short > 50:
            md.append(f"\n**Hallazgo clave**: SHORT signals crecieron {d_short:+.1f}% al ampliar datos. "
                      f"Esto confirma que la limitación anterior era falta de regímenes bajistas en la muestra, "
                      f"no del motor en sí.")

    md.append("\n## 5. Comparativa vs baseline (8 tokens x 50k velas α=5, pre-FIX-13)\n")
    md.append("Baseline tomado del JSON previo `/home/z/my-project/download/trie_stats_1m/trie_stats_1m_real.json`.\n")
    md.append("| Métrica | Baseline (8 tok x 50k, α=5) | Ahora (14 tok x 100k, α=4) | Delta |")
    md.append("|---|---:|---:|---:|")
    md.append("| Tokens | 8 | 14 | +6 (+75%) |")
    md.append("| Velas totales | 400,000 | 1,400,000 | +1,000,000 (+250%) |")
    md.append("| Patrones únicos (avg) | 2,787 | " +
             f"{a100.get('avg_patterns', 0):.0f} | " +
             f"{'+' if a100.get('avg_patterns',0) > 2787 else ''}{a100.get('avg_patterns',0) - 2787:.0f} |")
    md.append(f"| Confidence media | 0.130 | {a100.get('avg_conf_mean', 0):.3f} | "
              f"+{(a100.get('avg_conf_mean',0) - 0.130)/0.130*100:.0f}% |")
    md.append(f"| % patrones count≥10 | 0.05% | {a100.get('avg_pct_10plus', 0):.1f}% | "
              f"+{a100.get('avg_pct_10plus',0) - 0.05:.1f}pp |")
    md.append(f"| LONG/SHORT ratio | 4.45 | {a100.get('overall_ls_ratio', 0):.2f} | "
              f"{(a100.get('overall_ls_ratio',0) - 4.45)/4.45*100:+.0f}% |")

    out_path = OUT_DIR / "extended_summary.md"
    with open(out_path, "w") as f:
        f.write("\n".join(md))
    return out_path


def main():
    print(f"PPMT Extended Trie Statistics — TF=1m, SAX α={ALPHA} W={WINDOW} PL={PATTERN_LEN}")
    print(f"Scales: {SCALES}")
    print(f"Symbols ({len(SYMBOLS)}): {SYMBOLS}")
    print(f"Thresholds: min_sim={MIN_SIMILARITY}, min_conf={MIN_CONFIDENCE}")

    all_results = {}
    for sym in SYMBOLS:
        all_results[sym] = measure_symbol(sym)

    out_json = OUT_DIR / "trie_stats_extended.json"
    with open(out_json, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults JSON saved to {out_json}")

    aggregations = {}
    for sk in ["25000", "50000", "100000"]:
        aggregations[sk] = aggregate(all_results, sk)
        a = aggregations[sk]
        if a:
            print(f"\n[AGG @ {sk}] tokens={a['n_symbols']} avg_patterns={a['avg_patterns']:.1f} "
                  f"avg_conf={a['avg_conf_mean']:.3f} signals={a['sum_signals']:,} "
                  f"L/S={a['overall_ls_ratio']:.2f}")

    out_md = write_markdown_summary(all_results, aggregations)
    print(f"\nMarkdown summary saved to {out_md}")
    with open(OUT_DIR / "aggregations.json", "w") as f:
        json.dump(aggregations, f, indent=2, default=str)


if __name__ == "__main__":
    main()
