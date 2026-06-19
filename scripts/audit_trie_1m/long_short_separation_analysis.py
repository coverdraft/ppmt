"""
Análisis de Separación LONG/SHORT — ¿cuánta información se pierde al promediar?

HIPÓTESIS (FIX-A): La metadata del trie actualmente agrega observaciones LONG y
SHORT en un único `win_rate` y `expected_move_pct`. Esto destruye información
direccional: un patrón con 22 obs positivas y 8 negativas se reporta como
win_rate=0.73 (bueno para LONG), pero el motor no sabe que SHORT solo ganaría
27% de las veces. La auditoría v0.40.17 mostró que LONG pierde dinero en todos
los umbrales de confidence mientras SHORT gana — el sesgo podría venir de esta
agregación.

PERO ANTES DE IMPLEMENTAR el cambio en el motor, queremos MEDIR:

  1. ¿Qué fracción de patrones tienen asimetría LONG/SHORT significativa?
  2. ¿Es la mayoría simétrica (≈50/50) o direccional?
  3. Si aplicamos una política simple "LONG si long_wr > short_wr, SHORT si
     short_wr > long_wr" — ¿mejora el PnL vs el sistema actual?

METODOLOGÍA
  - Mismo setup que long_confidence_audit.py: 8 tokens × 70k train / 30k test.
  - Build per-asset trie (N3) sobre train.
  - Walk trie y para cada nodo a profundidad = PATTERN_LEN, extraer:
      pattern (str), historical_count,
      long_count, short_count,
      long_wr = long_count / N,
      short_wr = short_count / N,
      long_avg_move, short_avg_move
  - Análisis 1: distribución de |long_wr - short_wr|
      * media, mediana, P10..P99
      * % con diff > 10/20/30/40 puntos
  - Análisis 2: simulación offline de política alternativa
      Regla simple (sin motor):
        if long_count >= 5 and long_wr > 0.60:  alt_dir = "LONG"
        elif short_count >= 5 and short_wr > 0.60:  alt_dir = "SHORT"
        else:  alt_dir = None  (no signal)
      Para cada señal del test:
        current_dir = "LONG" if expected_move_pct > 0 else "SHORT"
        current_pnl = pnl si current_dir coincide con outcome, sino -pnl
        alt_pnl = pnl si alt_dir coincide, sino -pnl (o 0 si None)
      Comparar: n_señales, WR, PnL total, PnL medio, per-direction.

OUTPUT en /home/z/my-project/download/long_short_separation/:
  - per_pattern_stats.csv   (una fila por patrón único)
  - per_signal_comparison.csv (una fila por señal test: current vs alt)
  - analysis.json           (estructurado)
  - analysis.md             (reporte legible)
"""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie
from ppmt.core.metadata import BlockLifecycleMetadata  # noqa: F401
from ppmt.core.regime import RegimeDetector

# ----- config (idéntica a long_confidence_audit.py para comparabilidad) -----
ALPHA = 4
WINDOW = 7
PATTERN_LEN = 5
TRAIN_CANDLES = 70_000
TEST_CANDLES = 30_000

DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR = Path("/home/z/my-project/download/long_short_separation")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "LINKUSDT", "PEPEUSDT", "ARBUSDT",
]

# Política alternativa (la que el usuario pide simular)
MIN_COUNT_DIR = 5     # mínimo n de obs en la dirección para confiar
MIN_WIN_RATE = 0.60   # win_rate mínimo en la dirección para tomar señal


def load_df(symbol: str) -> pd.DataFrame:
    csv = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(csv)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return df


def build_trie(df_train: pd.DataFrame, symbol: str) -> tuple[PPMTTrie, int]:
    """Construye N3 (per-asset) sobre train — idéntico a long_confidence_audit.py."""
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    regime = RegimeDetector()
    symbols = sax.encode(df_train)

    trie = PPMTTrie(name=f"per_asset:{symbol}")
    count = 0
    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        next_sym = symbols[i + PATTERN_LEN] if i + PATTERN_LEN < len(symbols) else None
        start_candle = i * WINDOW
        end_candle = (i + PATTERN_LEN) * WINDOW
        if end_candle > len(df_train):
            break
        win = df_train.iloc[start_candle:end_candle]
        entry = win["close"].iloc[0]
        exit_ = win["close"].iloc[-1]
        move_pct = ((exit_ - entry) / entry) * 100.0
        high = win["high"].max()
        low = win["low"].min()
        dd_pct = ((low - entry) / entry) * 100.0
        fav_pct = ((high - entry) / entry) * 100.0
        duration = len(win)
        won = move_pct > 0
        rg = regime.detect_simple(win)

        trie.insert_with_observations(
            symbols=pattern, move_pct=move_pct, drawdown_pct=dd_pct,
            favorable_pct=fav_pct, duration=duration, won=won,
            next_symbol=next_sym, regime=rg,
        )
        count += 1
    trie.propagate_metadata()
    return trie, count


def walk_trie_collect_pattern_stats(trie: PPMTTrie, symbol: str) -> list[dict]:
    """Recorre el trie y para cada nodo a profundidad PATTERN_LEN, extrae
    estadísticas direccionales. Solo nodos con historical_count > 0."""
    rows: list[dict] = []
    target_depth = PATTERN_LEN

    def _walk(node, path: list[str]):
        if node.depth == target_depth:
            meta = node.metadata
            if meta.historical_count > 0:
                lc = meta.long_stats.count
                sc = meta.short_stats.count
                N = meta.historical_count
                # win_rate_long / short ya vienen con la fórmula count/N
                lwr = meta.win_rate_long
                swr = meta.win_rate_short
                l_avg = meta.long_stats.avg_move_pct
                s_avg = meta.short_stats.avg_move_pct
                rows.append({
                    "token": symbol,
                    "pattern": "".join(path),
                    "historical_count": N,
                    "long_count": lc,
                    "short_count": sc,
                    "long_wr": round(lwr, 4),
                    "short_wr": round(swr, 4),
                    "long_avg_move": round(l_avg, 4),
                    "short_avg_move": round(s_avg, 4),
                    "abs_wr_diff": round(abs(lwr - swr), 4),
                })
            return  # no bajar más allá del target depth para no duplicar
        for sym, child in node.children.items():
            _walk(child, path + [sym])

    _walk(trie.root, [])
    return rows


def walk_forward_compare(df_test: pd.DataFrame, trie: PPMTTrie, symbol: str) -> list[dict]:
    """Walk-forward sobre test. Para cada señal:
       - current_dir = sign(expected_move_pct) — lo que hace el motor HOY
       - alt_dir = política directional (LONG si long_count>=5 & long_wr>0.60, etc.)
       - actual_move_pct = outcome real
       - current_pnl = actual_move (LONG) o -actual_move (SHORT)
       - alt_pnl = actual_move (LONG) o -actual_move (SHORT), 0 si None
    """
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    symbols = sax.encode(df_test)

    rows: list[dict] = []
    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        fire_candle = (i + PATTERN_LEN) * WINDOW
        end_outcome = fire_candle + PATTERN_LEN * WINDOW
        if end_outcome > len(df_test):
            break

        # Lookup directo al nodo
        node = trie.root
        for sym in pattern:
            if sym not in node.children:
                node = None
                break
            node = node.children[sym]
        if node is None:
            continue
        meta = node.metadata
        if meta.historical_count < 1:
            continue

        # Outcome real
        entry_price = df_test["close"].iloc[fire_candle - 1]
        exit_price = df_test["close"].iloc[end_outcome - 1]
        actual_move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        # Sistema actual: dirección por signo de expected_move_pct
        expected_move = float(getattr(meta, "expected_move_pct", 0.0))
        current_dir = "LONG" if expected_move > 0 else "SHORT"
        current_pnl = actual_move_pct if current_dir == "LONG" else -actual_move_pct

        # Política alternativa: directional
        lc = meta.long_stats.count
        sc = meta.short_stats.count
        lwr = meta.win_rate_long
        swr = meta.win_rate_short
        if lc >= MIN_COUNT_DIR and lwr > MIN_WIN_RATE:
            alt_dir = "LONG"
        elif sc >= MIN_COUNT_DIR and swr > MIN_WIN_RATE:
            alt_dir = "SHORT"
        else:
            alt_dir = None

        if alt_dir is None:
            alt_pnl = 0.0  # no trade → no PnL
            alt_skipped = True
        else:
            alt_pnl = actual_move_pct if alt_dir == "LONG" else -actual_move_pct
            alt_skipped = False

        rows.append({
            "token": symbol,
            "pattern": "".join(pattern),
            "historical_count": meta.historical_count,
            "long_count": lc,
            "short_count": sc,
            "long_wr": round(lwr, 4),
            "short_wr": round(swr, 4),
            "expected_move_pct": round(expected_move, 4),
            "actual_move_pct": round(actual_move_pct, 4),
            "current_dir": current_dir,
            "current_pnl": round(current_pnl, 4),
            "alt_dir": alt_dir if alt_dir else "SKIP",
            "alt_pnl": round(alt_pnl, 4),
            "alt_skipped": alt_skipped,
            "direction_changed": (current_dir != alt_dir) if alt_dir else False,
        })
    return rows


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    arr = np.array(values, dtype=float)
    return float(np.percentile(arr, p))


def distribution_analysis(pattern_df: pd.DataFrame) -> dict:
    """Análisis 1: distribución de |long_wr - short_wr|."""
    diffs = pattern_df["abs_wr_diff"].astype(float).tolist()
    out: dict[str, Any] = {
        "n_patterns": len(diffs),
        "mean": round(float(np.mean(diffs)), 4),
        "median": round(float(np.median(diffs)), 4),
        "std": round(float(np.std(diffs)), 4),
        "min": round(float(np.min(diffs)), 4),
        "max": round(float(np.max(diffs)), 4),
        "percentiles": {
            f"P{p}": round(percentile(diffs, p), 4)
            for p in [10, 25, 50, 75, 90, 95, 99]
        },
        "pct_above_thresholds": {
            f">{int(t*100)}pts": round(
                100.0 * sum(1 for d in diffs if d > t) / len(diffs), 2
            )
            for t in [0.10, 0.20, 0.30, 0.40]
        },
        # Distribución por buckets
        "buckets": {
            "0-10pts":    round(100.0 * sum(1 for d in diffs if d <= 0.10) / len(diffs), 2),
            "10-20pts":   round(100.0 * sum(1 for d in diffs if 0.10 < d <= 0.20) / len(diffs), 2),
            "20-30pts":   round(100.0 * sum(1 for d in diffs if 0.20 < d <= 0.30) / len(diffs), 2),
            "30-40pts":   round(100.0 * sum(1 for d in diffs if 0.30 < d <= 0.40) / len(diffs), 2),
            "40+pts":     round(100.0 * sum(1 for d in diffs if d > 0.40) / len(diffs), 2),
        },
    }
    # Per-token
    out_per_token: dict[str, dict] = {}
    for tok, sub in pattern_df.groupby("token"):
        d = sub["abs_wr_diff"].astype(float).tolist()
        out_per_token[tok] = {
            "n_patterns": len(d),
            "mean": round(float(np.mean(d)), 4),
            "median": round(float(np.median(d)), 4),
            "pct_>10pts": round(100.0 * sum(1 for x in d if x > 0.10) / len(d), 2),
            "pct_>20pts": round(100.0 * sum(1 for x in d if x > 0.20) / len(d), 2),
            "pct_>30pts": round(100.0 * sum(1 for x in d if x > 0.30) / len(d), 2),
            "pct_>40pts": round(100.0 * sum(1 for x in d if x > 0.40) / len(d), 2),
        }
    out["per_token"] = out_per_token
    return out


def policy_comparison(signal_df: pd.DataFrame) -> dict:
    """Análisis 2: comparativa current vs alt."""
    out: dict[str, Any] = {}

    # Current
    cur_n = len(signal_df)
    cur_won = (signal_df["current_pnl"] > 0).sum()
    cur_pnl_total = float(signal_df["current_pnl"].sum())
    cur_pnl_mean = float(signal_df["current_pnl"].mean())
    cur_long = signal_df[signal_df["current_dir"] == "LONG"]
    cur_short = signal_df[signal_df["current_dir"] == "SHORT"]
    out["current_system"] = {
        "n_signals": int(cur_n),
        "win_rate": round(float(cur_won / cur_n), 4) if cur_n else 0.0,
        "pnl_total_pct": round(cur_pnl_total, 2),
        "pnl_mean_pct": round(cur_pnl_mean, 4),
        "long_n": int(len(cur_long)),
        "long_wr": round(float((cur_long["current_pnl"] > 0).mean()), 4) if len(cur_long) else None,
        "long_pnl_mean": round(float(cur_long["current_pnl"].mean()), 4) if len(cur_long) else None,
        "long_pnl_total": round(float(cur_long["current_pnl"].sum()), 2) if len(cur_long) else None,
        "short_n": int(len(cur_short)),
        "short_wr": round(float((cur_short["current_pnl"] > 0).mean()), 4) if len(cur_short) else None,
        "short_pnl_mean": round(float(cur_short["current_pnl"].mean()), 4) if len(cur_short) else None,
        "short_pnl_total": round(float(cur_short["current_pnl"].sum()), 2) if len(cur_short) else None,
    }

    # Alt — solo sobre señales NO skip
    alt_taken = signal_df[~signal_df["alt_skipped"]]
    alt_skipped = signal_df[signal_df["alt_skipped"]]
    alt_n = len(alt_taken)
    alt_won = (alt_taken["alt_pnl"] > 0).sum()
    alt_pnl_total = float(alt_taken["alt_pnl"].sum())
    alt_pnl_mean = float(alt_taken["alt_pnl"].mean()) if alt_n else 0.0
    alt_long = alt_taken[alt_taken["alt_dir"] == "LONG"]
    alt_short = alt_taken[alt_taken["alt_dir"] == "SHORT"]
    out["alt_system"] = {
        "n_signals_taken": int(alt_n),
        "n_signals_skipped": int(len(alt_skipped)),
        "skip_rate_pct": round(100.0 * len(alt_skipped) / cur_n, 2) if cur_n else 0.0,
        "win_rate_taken": round(float(alt_won / alt_n), 4) if alt_n else 0.0,
        "pnl_total_pct": round(alt_pnl_total, 2),
        "pnl_mean_pct": round(alt_pnl_mean, 4),
        "long_n": int(len(alt_long)),
        "long_wr": round(float((alt_long["alt_pnl"] > 0).mean()), 4) if len(alt_long) else None,
        "long_pnl_mean": round(float(alt_long["alt_pnl"].mean()), 4) if len(alt_long) else None,
        "long_pnl_total": round(float(alt_long["alt_pnl"].sum()), 2) if len(alt_long) else None,
        "short_n": int(len(alt_short)),
        "short_wr": round(float((alt_short["alt_pnl"] > 0).mean()), 4) if len(alt_short) else None,
        "short_pnl_mean": round(float(alt_short["alt_pnl"].mean()), 4) if len(alt_short) else None,
        "short_pnl_total": round(float(alt_short["alt_pnl"].sum()), 2) if len(alt_short) else None,
    }

    # Delta
    out["delta"] = {
        "n_signals_delta": int(alt_n - cur_n),  # negativo = alt toma menos
        "pnl_total_delta_pct": round(alt_pnl_total - cur_pnl_total, 2),
        "pnl_mean_delta_pct": round(alt_pnl_mean - cur_pnl_mean, 4),
        "win_rate_delta": round(
            (alt_won / alt_n if alt_n else 0.0) - (cur_won / cur_n if cur_n else 0.0), 4
        ),
    }

    # Análisis de cuándo difieren
    diff = signal_df[signal_df["direction_changed"]]
    out["direction_changes"] = {
        "n_changed": int(len(diff)),
        "pct_changed": round(100.0 * len(diff) / cur_n, 2) if cur_n else 0.0,
        "current_pnl_on_changed": round(float(diff["current_pnl"].sum()), 2),
        "alt_pnl_on_changed": round(float(diff["alt_pnl"].sum()), 2),
        "delta_on_changed": round(float(diff["alt_pnl"].sum() - diff["current_pnl"].sum()), 2),
    }
    # Cambios por tipo
    changes = diff.groupby(["current_dir", "alt_dir"]).size().reset_index(name="n")
    out["direction_changes"]["breakdown"] = changes.to_dict(orient="records")

    # Per-token comparativa
    per_token: dict[str, dict] = {}
    for tok, sub in signal_df.groupby("token"):
        cur_t_pnl = float(sub["current_pnl"].sum())
        alt_taken_t = sub[~sub["alt_skipped"]]
        alt_t_pnl = float(alt_taken_t["alt_pnl"].sum())
        per_token[tok] = {
            "n_signals": int(len(sub)),
            "current_pnl_total": round(cur_t_pnl, 2),
            "alt_pnl_total": round(alt_t_pnl, 2),
            "alt_n_taken": int(len(alt_taken_t)),
            "alt_n_skipped": int(len(sub) - len(alt_taken_t)),
            "delta": round(alt_t_pnl - cur_t_pnl, 2),
        }
    out["per_token"] = per_token

    return out


def write_report(dist: dict, pol: dict, out_path: Path) -> None:
    lines: list[str] = []
    lines.append("# Análisis de Separación LONG/SHORT\n")
    lines.append(f"**Config:** α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}, "
                 f"train={TRAIN_CANDLES:,} / test={TEST_CANDLES:,} velas, "
                 f"{len(SYMBOLS)} tokens.\n")
    lines.append(f"**Política alt:** LONG si `long_count >= {MIN_COUNT_DIR}` "
                 f"y `long_wr > {MIN_WIN_RATE}`; SHORT análogo; si no, SKIP.\n\n")

    lines.append("## 1. Distribución de |long_wr - short_wr|\n\n")
    lines.append(f"- Patrones únicos analizados: **{dist['n_patterns']:,}**\n")
    lines.append(f"- Media: **{dist['mean']:.4f}** ({dist['mean']*100:.2f} pts)\n")
    lines.append(f"- Mediana: **{dist['median']:.4f}** ({dist['median']*100:.2f} pts)\n")
    lines.append(f"- Std: **{dist['std']:.4f}**\n")
    lines.append(f"- Min/Max: {dist['min']:.4f} / {dist['max']:.4f}\n\n")
    lines.append("### Percentiles\n\n")
    lines.append("| P | valor |\n|---|---|\n")
    for p, v in dist["percentiles"].items():
        lines.append(f"| {p} | {v:.4f} ({v*100:.2f} pts) |\n")
    lines.append("\n### % de patrones con diferencia superior a\n\n")
    lines.append("| Umbral | % patrones |\n|---|---|\n")
    for k, v in dist["pct_above_thresholds"].items():
        lines.append(f"| {k} | {v:.2f}% |\n")
    lines.append("\n### Distribución por buckets\n\n")
    lines.append("| Bucket | % patrones |\n|---|---|\n")
    for k, v in dist["buckets"].items():
        lines.append(f"| {k} | {v:.2f}% |\n")
    lines.append("\n### Per-token\n\n")
    lines.append("| Token | N patrones | media | mediana | >10pts | >20pts | >30pts | >40pts |\n")
    lines.append("|---|---|---|---|---|---|---|---|\n")
    for tok, t in dist["per_token"].items():
        lines.append(f"| {tok} | {t['n_patterns']:,} | {t['mean']:.4f} | "
                     f"{t['median']:.4f} | {t['pct_>10pts']:.2f}% | "
                     f"{t['pct_>20pts']:.2f}% | {t['pct_>30pts']:.2f}% | "
                     f"{t['pct_>40pts']:.2f}% |\n")

    lines.append("\n## 2. Comparativa: sistema actual vs política direccional\n\n")
    cur = pol["current_system"]
    alt = pol["alt_system"]
    dl = pol["delta"]
    lines.append("| Métrica | Current | Alt | Delta |\n|---|---|---|---|\n")
    lines.append(f"| N señales | {cur['n_signals']:,} | "
                 f"{alt['n_signals_taken']:,} (+{alt['n_signals_skipped']:,} skip) | "
                 f"{dl['n_signals_delta']:+,} |\n")
    cur_wr = cur['win_rate']
    alt_wr = alt['win_rate_taken']
    lines.append(f"| Win rate | {cur_wr:.4f} | {alt_wr:.4f} | {dl['win_rate_delta']:+.4f} |\n")
    lines.append(f"| PnL total % | {cur['pnl_total_pct']:.2f} | {alt['pnl_total_pct']:.2f} | "
                 f"{dl['pnl_total_delta_pct']:+.2f} |\n")
    lines.append(f"| PnL medio % | {cur['pnl_mean_pct']:.4f} | {alt['pnl_mean_pct']:.4f} | "
                 f"{dl['pnl_mean_delta_pct']:+.4f} |\n")
    lines.append(f"| Skip rate | 0% | {alt['skip_rate_pct']:.2f}% | — |\n")

    lines.append("\n### Desglose por dirección\n\n")
    lines.append("| Dir | Current N | Current WR | Current PnL medio | "
                 "Alt N | Alt WR | Alt PnL medio |\n")
    lines.append("|---|---|---|---|---|---|---|\n")
    lines.append(f"| LONG | {cur['long_n']:,} | {cur['long_wr']} | "
                 f"{cur['long_pnl_mean']} | {alt['long_n']:,} | {alt['long_wr']} | "
                 f"{alt['long_pnl_mean']} |\n")
    lines.append(f"| SHORT | {cur['short_n']:,} | {cur['short_wr']} | "
                 f"{cur['short_pnl_mean']} | {alt['short_n']:,} | {alt['short_wr']} | "
                 f"{alt['short_pnl_mean']} |\n")

    lines.append("\n### Cambios de dirección (current ≠ alt)\n\n")
    dc = pol["direction_changes"]
    lines.append(f"- N señales con cambio: **{dc['n_changed']:,}** "
                 f"({dc['pct_changed']:.2f}% del total)\n")
    lines.append(f"- PnL current sobre esas señales: {dc['current_pnl_on_changed']:.2f}\n")
    lines.append(f"- PnL alt sobre esas señales: {dc['alt_pnl_on_changed']:.2f}\n")
    lines.append(f"- Delta sobre esas señales: {dc['delta_on_changed']:+.2f}\n\n")
    lines.append("Breakdown:\n\n")
    lines.append("| Current | Alt | N |\n|---|---|---|\n")
    for b in dc["breakdown"]:
        lines.append(f"| {b['current_dir']} | {b['alt_dir']} | {b['n']:,} |\n")

    lines.append("\n### Per-token\n\n")
    lines.append("| Token | N | Current PnL | Alt PnL | Alt taken | Alt skip | Delta |\n")
    lines.append("|---|---|---|---|---|---|---|\n")
    for tok, t in pol["per_token"].items():
        lines.append(f"| {tok} | {t['n_signals']:,} | {t['current_pnl_total']:.2f} | "
                     f"{t['alt_pnl_total']:.2f} | {t['alt_n_taken']:,} | "
                     f"{t['alt_n_skipped']:,} | {t['delta']:+.2f} |\n")

    lines.append("\n## 3. Veredicto\n\n")
    # Heurística simple para veredicto
    delta_pnl = dl['pnl_total_delta_pct']
    pct_asym_30 = dist['pct_above_thresholds']['>30pts']
    pct_asym_20 = dist['pct_above_thresholds']['>20pts']
    if pct_asym_30 > 20 and delta_pnl > 0:
        verdict = ("INFORMATION_IS_REAL: >20% de patrones tienen asimetría >30pts "
                   "y la política direccional MEJORA el PnL total. "
                   "Vale la pena integrar FIX-A al motor.")
    elif pct_asym_20 > 30 and delta_pnl > 0:
        verdict = ("INFORMATION_PARTIAL: >30% de patrones tienen asimetría >20pts "
                   "y la política direccional mejora el PnL (aunque marginalmente). "
                   "FIX-A promete pero requiere ajustar thresholds.")
    elif delta_pnl > 0:
        verdict = ("MARGINAL: la política direccional mejora PnL pero la mayoría "
                   "de patrones son simétricos. Considerar si la complejidad "
                   "adicional vale la pena.")
    else:
        verdict = ("NO_INFORMATION: la política direccional NO mejora PnL "
                   "o lo empeora. La mayoría de patrones son simétricos. "
                   "ABORTAR integración de FIX-A al motor.")
    lines.append(f"**{verdict}**\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    print(f"Reporte escrito a {out_path}")


def main():
    print("=" * 78)
    print("ANÁLISIS DE SEPARACIÓN LONG/SHORT — ¿cuánta info se pierde al promediar?")
    print("=" * 78)
    print(f"Config: α={ALPHA}, W={WINDOW}, PL={PATTERN_LEN}")
    print(f"Split:  Train {TRAIN_CANDLES:,}  |  Test {TEST_CANDLES:,}")
    print(f"Tokens: {len(SYMBOLS)}  ({', '.join(SYMBOLS)})")
    print(f"Política alt: LONG si long_count>={MIN_COUNT_DIR} & long_wr>{MIN_WIN_RATE}, "
          f"SHORT análogo, sino SKIP")
    print()

    all_pattern_rows: list[dict] = []
    all_signal_rows: list[dict] = []

    for sym in SYMBOLS:
        try:
            df = load_df(sym)
            if len(df) < TRAIN_CANDLES + TEST_CANDLES:
                print(f"  SKIP {sym}: solo {len(df)} velas")
                continue
            df_train = df.iloc[:TRAIN_CANDLES].reset_index(drop=True)
            df_test = df.iloc[TRAIN_CANDLES:TRAIN_CANDLES + TEST_CANDLES].reset_index(drop=True)
            print(f"  {sym}: build trie ({TRAIN_CANDLES:,} train) ...", end=" ", flush=True)
            trie, n_ins = build_trie(df_train, sym)
            print(f"{n_ins:,} patrones. Collecting pattern stats ...", end=" ", flush=True)
            pat_rows = walk_trie_collect_pattern_stats(trie, sym)
            print(f"{len(pat_rows):,} únicos. Walk-forward ...", end=" ", flush=True)
            sig_rows = walk_forward_compare(df_test, trie, sym)
            n_long = sum(1 for r in sig_rows if r["current_dir"] == "LONG")
            n_short = sum(1 for r in sig_rows if r["current_dir"] == "SHORT")
            print(f"{len(sig_rows):,} señales (L={n_long:,} S={n_short:,})")
            all_pattern_rows.extend(pat_rows)
            all_signal_rows.extend(sig_rows)
        except Exception as e:
            import traceback
            print(f"  ERROR {sym}: {e}")
            traceback.print_exc()

    if not all_signal_rows:
        print("\nNO HAY SEÑALES — abortando")
        return

    pattern_df = pd.DataFrame(all_pattern_rows)
    signal_df = pd.DataFrame(all_signal_rows)

    pattern_df.to_csv(OUT_DIR / "per_pattern_stats.csv", index=False)
    signal_df.to_csv(OUT_DIR / "per_signal_comparison.csv", index=False)
    print(f"\nGuardados:")
    print(f"  {OUT_DIR / 'per_pattern_stats.csv'}  ({len(pattern_df):,} patrones)")
    print(f"  {OUT_DIR / 'per_signal_comparison.csv'}  ({len(signal_df):,} señales)")

    # ----- Análisis 1: distribución -----
    print("\n" + "=" * 78)
    print("ANÁLISIS 1: Distribución de |long_wr - short_wr|")
    print("=" * 78)
    dist = distribution_analysis(pattern_df)
    print(f"\nPatrones únicos: {dist['n_patterns']:,}")
    print(f"  media   = {dist['mean']:.4f} ({dist['mean']*100:.2f} pts)")
    print(f"  mediana = {dist['median']:.4f} ({dist['median']*100:.2f} pts)")
    print(f"  std     = {dist['std']:.4f}")
    print(f"  min/max = {dist['min']:.4f} / {dist['max']:.4f}")
    print("\nPercentiles:")
    for p, v in dist["percentiles"].items():
        print(f"  {p:>4} = {v:.4f} ({v*100:.2f} pts)")
    print("\n% de patrones con diferencia superior a:")
    for k, v in dist["pct_above_thresholds"].items():
        print(f"  {k:>8} = {v:.2f}%")
    print("\nDistribución por buckets:")
    for k, v in dist["buckets"].items():
        print(f"  {k:>10} = {v:.2f}%")

    # ----- Análisis 2: comparativa -----
    print("\n" + "=" * 78)
    print("ANÁLISIS 2: Current vs Política Direccional")
    print("=" * 78)
    pol = policy_comparison(signal_df)
    cur = pol["current_system"]
    alt = pol["alt_system"]
    dl = pol["delta"]
    print(f"\nSistema ACTUAL:")
    print(f"  N señales    = {cur['n_signals']:,}")
    print(f"  Win rate     = {cur['win_rate']:.4f}")
    print(f"  PnL total %  = {cur['pnl_total_pct']:.2f}")
    print(f"  PnL medio %  = {cur['pnl_mean_pct']:.4f}")
    print(f"  LONG:  N={cur['long_n']:,}  WR={cur['long_wr']}  PnL_medio={cur['long_pnl_mean']}")
    print(f"  SHORT: N={cur['short_n']:,}  WR={cur['short_wr']}  PnL_medio={cur['short_pnl_mean']}")
    print(f"\nPolítica ALT:")
    print(f"  N señales tomadas = {alt['n_signals_taken']:,}")
    print(f"  N señales skip    = {alt['n_signals_skipped']:,}  "
          f"({alt['skip_rate_pct']:.2f}% skip)")
    print(f"  Win rate (taken)  = {alt['win_rate_taken']:.4f}")
    print(f"  PnL total %       = {alt['pnl_total_pct']:.2f}")
    print(f"  PnL medio %       = {alt['pnl_mean_pct']:.4f}")
    print(f"  LONG:  N={alt['long_n']:,}  WR={alt['long_wr']}  PnL_medio={alt['long_pnl_mean']}")
    print(f"  SHORT: N={alt['short_n']:,}  WR={alt['short_wr']}  PnL_medio={alt['short_pnl_mean']}")
    print(f"\nDELTA (alt - current):")
    print(f"  N señales delta   = {dl['n_signals_delta']:+,}")
    print(f"  Win rate delta    = {dl['win_rate_delta']:+.4f}")
    print(f"  PnL total delta % = {dl['pnl_total_delta_pct']:+.2f}")
    print(f"  PnL medio delta % = {dl['pnl_mean_delta_pct']:+.4f}")

    print(f"\nCambios de dirección:")
    dc = pol["direction_changes"]
    print(f"  N cambios         = {dc['n_changed']:,} ({dc['pct_changed']:.2f}%)")
    print(f"  PnL current sobre cambiados = {dc['current_pnl_on_changed']:.2f}")
    print(f"  PnL alt sobre cambiados     = {dc['alt_pnl_on_changed']:.2f}")
    print(f"  Delta sobre cambiados       = {dc['delta_on_changed']:+.2f}")
    print(f"  Breakdown:")
    for b in dc["breakdown"]:
        print(f"    {b['current_dir']:>6} → {b['alt_dir']:<6}  N={b['n']:,}")

    # ----- Per-token -----
    print(f"\nPer-token:")
    print(f"  {'Token':<10} {'N':>7} {'Current':>10} {'Alt':>10} {'Taken':>7} "
          f"{'Skip':>7} {'Delta':>10}")
    for tok, t in pol["per_token"].items():
        print(f"  {tok:<10} {t['n_signals']:>7,} {t['current_pnl_total']:>10.2f} "
              f"{t['alt_pnl_total']:>10.2f} {t['alt_n_taken']:>7,} "
              f"{t['alt_n_skipped']:>7,} {t['delta']:>+10.2f}")

    # ----- Guardar JSON + MD -----
    payload = {
        "config": {
            "alpha": ALPHA, "window": WINDOW, "pattern_len": PATTERN_LEN,
            "train_candles": TRAIN_CANDLES, "test_candles": TEST_CANDLES,
            "symbols": SYMBOLS,
            "alt_policy": {
                "min_count_dir": MIN_COUNT_DIR,
                "min_win_rate": MIN_WIN_RATE,
            },
        },
        "distribution_analysis": dist,
        "policy_comparison": pol,
    }
    (OUT_DIR / "analysis.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(dist, pol, OUT_DIR / "analysis.md")
    print(f"\nJSON: {OUT_DIR / 'analysis.json'}")
    print(f"MD:   {OUT_DIR / 'analysis.md'}")


if __name__ == "__main__":
    main()
