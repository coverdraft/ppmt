"""
Análisis extendido: múltiples políticas direccionales.

Tras el primer análisis (long_short_separation_analysis.py) encontramos:
  - SÍ hay asimetría direccional fuerte (mediana |long_wr - short_wr| = 38 pts)
  - PERO la política simple `long_wr > 0.60` NO la explota bien:
    * Skip 45.9% de señales (descarta muchas buenas)
    * En señales donde cambia de dirección, alt es +30.95% mejor que current
    * Pero el PnL total empeora (-98% → -178%) por exceso de skip

Hipótesis a verificar: la implementación actual de DirectionStats define
`long_count = #(move_pct > 0)` y `long_wr = long_count / N`, lo cual es
**matemáticamente equivalente al win_rate legacy**. No hay ganancia de info.

Para explotar la asimetría, necesitamos políticas más finas. Probamos:

  P1 (baseline current):   dir = sign(expected_move_pct)
  P2 (majority_simple):    dir = LONG if long_wr > short_wr else SHORT (siempre toma)
  P3 (majority_min_count): P2 + skip si historical_count < 5
  P4 (alt_thr60):          P1 + skip si abs(long_wr - short_wr) < 0.20 (asimetría mínima)
  P5 (alt_thr60_strict):   LONG si long_wr>0.60 & long_count>=10; SHORT análogo; sino SKIP
  P6 (majority_avg_move):  LONG si long_avg_move > |short_avg_move|; SHORT análogo
  P7 (avg_move_threshold): LONG si long_avg_move > 0.30% & long_count>=5;
                           SHORT si short_avg_move < -0.30% & short_count>=5; sino SKIP
  P8 (weighted_signal):    score = long_count*long_avg_move - short_count*|short_avg_move|
                           LONG si score > 0; SHORT si score < 0; sino SKIP
                           (este es básicamente el current dir = sign(expected_move_pct))

Para cada política:
  - N señales tomadas / skip
  - Win rate
  - PnL total, PnL medio
  - LONG N/WR/PnL, SHORT N/WR/PnL
"""
from __future__ import annotations
import json
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


def load_df(symbol: str) -> pd.DataFrame:
    csv = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(csv)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return df


def build_trie(df_train: pd.DataFrame, symbol: str) -> tuple[PPMTTrie, int]:
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


def walk_forward_extract(df_test: pd.DataFrame, trie: PPMTTrie, symbol: str) -> list[dict]:
    """Walk-forward: para cada señal, capturar todos los campos necesarios para
    evaluar MÚLTIPLES políticas offline."""
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    symbols = sax.encode(df_test)
    rows: list[dict] = []
    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        fire_candle = (i + PATTERN_LEN) * WINDOW
        end_outcome = fire_candle + PATTERN_LEN * WINDOW
        if end_outcome > len(df_test):
            break
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

        entry_price = df_test["close"].iloc[fire_candle - 1]
        exit_price = df_test["close"].iloc[end_outcome - 1]
        actual_move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        rows.append({
            "token": symbol,
            "pattern": "".join(pattern),
            "N": meta.historical_count,
            "long_count": meta.long_stats.count,
            "short_count": meta.short_stats.count,
            "long_wr": meta.win_rate_long,
            "short_wr": meta.win_rate_short,
            "long_avg_move": meta.long_stats.avg_move_pct,  # positivo
            "short_avg_move": meta.short_stats.avg_move_pct,  # negativo
            "expected_move_pct": float(getattr(meta, "expected_move_pct", 0.0)),
            "actual_move_pct": actual_move_pct,
        })
    return rows


# ===== Políticas =====
# Cada política devuelve ("LONG" | "SHORT" | None)

def policy_current(r) -> str | None:
    """P1: dir = sign(expected_move_pct). El sistema actual."""
    return "LONG" if r["expected_move_pct"] > 0 else "SHORT"


def policy_majority_simple(r) -> str | None:
    """P2: LONG si long_wr > short_wr, SHORT si short_wr > long_wr.
    En caso de empate (== 0.5), DEFAULT a LONG."""
    if r["long_wr"] > r["short_wr"]:
        return "LONG"
    if r["short_wr"] > r["long_wr"]:
        return "SHORT"
    return "LONG"  # tie-breaker arbitrario


def policy_majority_min_count(r, min_count=5) -> str | None:
    """P3: majority_simple + skip si N < min_count."""
    if r["N"] < min_count:
        return None
    return policy_majority_simple(r)


def policy_current_asym_filter(r, min_asym=0.20) -> str | None:
    """P4: current dir pero skip si |long_wr - short_wr| < min_asym."""
    if abs(r["long_wr"] - r["short_wr"]) < min_asym:
        return None
    return policy_current(r)


def policy_alt_thr60_strict(r, min_count=10) -> str | None:
    """P5: LONG si long_wr>0.60 & long_count>=10; SHORT análogo; sino SKIP."""
    if r["long_count"] >= min_count and r["long_wr"] > 0.60:
        return "LONG"
    if r["short_count"] >= min_count and r["short_wr"] > 0.60:
        return "SHORT"
    return None


def policy_majority_avg_move(r) -> str | None:
    """P6: LONG si long_avg_move > |short_avg_move| (mayor magnitud esperada)."""
    lam = r["long_avg_move"]
    sam = r["short_avg_move"]
    # Caso edge: una de las dos puede ser 0 si no hay obs en esa dirección
    if r["long_count"] == 0:
        return "SHORT" if r["short_count"] > 0 else None
    if r["short_count"] == 0:
        return "LONG"
    if lam > abs(sam):
        return "LONG"
    if abs(sam) > lam:
        return "SHORT"
    return None


def policy_avg_move_threshold(r, thr=0.30, min_count=5) -> str | None:
    """P7: LONG si long_avg_move > thr% & long_count>=min_count;
    SHORT si short_avg_move < -thr% & short_count>=min_count; sino SKIP."""
    if r["long_count"] >= min_count and r["long_avg_move"] > thr:
        return "LONG"
    if r["short_count"] >= min_count and r["short_avg_move"] < -thr:
        return "SHORT"
    return None


def policy_weighted_score(r) -> str | None:
    """P8: score = long_count*long_avg_move - short_count*|short_avg_move|
    (= N * expected_move_pct). LONG si > 0, SHORT si < 0, sino SKIP."""
    # expected_move_pct = (long_count*long_avg + short_count*short_avg)/N
    # Score = N * expected_move_pct
    score = r["long_count"] * r["long_avg_move"] + r["short_count"] * r["short_avg_move"]
    if score > 0:
        return "LONG"
    if score < 0:
        return "SHORT"
    return None


POLICIES = [
    ("P1_current", policy_current),
    ("P2_majority_simple", policy_majority_simple),
    ("P3_majority_min5", policy_majority_min_count),
    ("P4_current_asymfilter_020", policy_current_asym_filter),
    ("P5_alt_thr60_strict_min10", policy_alt_thr60_strict),
    ("P6_majority_avg_move", policy_majority_avg_move),
    ("P7_avg_move_thr_030_min5", policy_avg_move_threshold),
    ("P8_weighted_score", policy_weighted_score),
]


def evaluate_policy(df: pd.DataFrame, policy_fn) -> dict:
    """Para una política, evaluar sobre todas las señales."""
    df = df.copy()
    df["decision"] = df.apply(policy_fn, axis=1)
    df["taken"] = df["decision"].notna()
    df["pnl"] = df.apply(
        lambda r: (r["actual_move_pct"] if r["decision"] == "LONG"
                   else -r["actual_move_pct"] if r["decision"] == "SHORT"
                   else 0.0),
        axis=1
    )
    taken = df[df["taken"]]
    n_taken = len(taken)
    n_skip = len(df) - n_taken
    total_n = len(df)
    out = {
        "n_total": int(total_n),
        "n_taken": int(n_taken),
        "n_skip": int(n_skip),
        "skip_pct": round(100.0 * n_skip / total_n, 2) if total_n else 0.0,
        "win_rate": round(float((taken["pnl"] > 0).mean()), 4) if n_taken else None,
        "pnl_total": round(float(taken["pnl"].sum()), 2) if n_taken else 0.0,
        "pnl_mean": round(float(taken["pnl"].mean()), 4) if n_taken else None,
    }
    # Per-direction
    for d in ["LONG", "SHORT"]:
        sub = taken[taken["decision"] == d]
        out[f"{d.lower()}_n"] = int(len(sub))
        out[f"{d.lower()}_wr"] = round(float((sub["pnl"] > 0).mean()), 4) if len(sub) else None
        out[f"{d.lower()}_pnl_total"] = round(float(sub["pnl"].sum()), 2) if len(sub) else 0.0
        out[f"{d.lower()}_pnl_mean"] = round(float(sub["pnl"].mean()), 4) if len(sub) else None
    return out


def main():
    print("=" * 78)
    print("ANÁLISIS EXTENDIDO: múltiples políticas direccionales")
    print("=" * 78)

    # Reusar per_signal_comparison.csv si existe; si no, rebuild
    sig_csv = OUT_DIR / "per_signal_comparison.csv"
    if sig_csv.exists():
        print(f"Cargando señales desde {sig_csv} ...")
        # Necesitamos más columnas que las del CSV previo; rebuild
        # (el CSV previo no tiene long_avg_move ni short_avg_move)
        rebuild = True
    else:
        rebuild = True

    if rebuild:
        all_rows: list[dict] = []
        for sym in SYMBOLS:
            try:
                df = load_df(sym)
                if len(df) < TRAIN_CANDLES + TEST_CANDLES:
                    continue
                df_train = df.iloc[:TRAIN_CANDLES].reset_index(drop=True)
                df_test = df.iloc[TRAIN_CANDLES:TRAIN_CANDLES + TEST_CANDLES].reset_index(drop=True)
                print(f"  {sym}: build trie ...", end=" ", flush=True)
                trie, n_ins = build_trie(df_train, sym)
                print(f"walk-forward ...", end=" ", flush=True)
                rows = walk_forward_extract(df_test, trie, sym)
                print(f"{len(rows):,} señales")
                all_rows.extend(rows)
            except Exception as e:
                import traceback
                print(f"  ERROR {sym}: {e}")
                traceback.print_exc()

        df = pd.DataFrame(all_rows)
        df.to_csv(OUT_DIR / "per_signal_rich.csv", index=False)
        print(f"\nSeñales: {len(df):,} guardadas en {OUT_DIR / 'per_signal_rich.csv'}")
    else:
        df = pd.read_csv(sig_csv)

    # Evaluar todas las políticas
    print("\n" + "=" * 78)
    print("Evaluación de políticas")
    print("=" * 78)
    results: dict[str, dict] = {}
    rows_for_csv: list[dict] = []
    for name, fn in POLICIES:
        r = evaluate_policy(df, fn)
        results[name] = r
        rows_for_csv.append({"policy": name, **r})
        print(f"\n{name}:")
        print(f"  taken={r['n_taken']:,}/{r['n_total']:,} (skip {r['skip_pct']:.2f}%)  "
              f"WR={r['win_rate']}  PnL_total={r['pnl_total']:+.2f}  "
              f"PnL_mean={r['pnl_mean']}")
        print(f"  LONG:  N={r['long_n']:,}  WR={r['long_wr']}  "
              f"PnL_total={r['long_pnl_total']:+.2f}  PnL_mean={r['long_pnl_mean']}")
        print(f"  SHORT: N={r['short_n']:,}  WR={r['short_wr']}  "
              f"PnL_total={r['short_pnl_total']:+.2f}  PnL_mean={r['short_pnl_mean']}")

    # Tabla resumen
    print("\n" + "=" * 78)
    print("TABLA COMPARATIVA")
    print("=" * 78)
    summary = pd.DataFrame(rows_for_csv)
    summary = summary[["policy", "n_taken", "skip_pct", "win_rate",
                       "pnl_total", "pnl_mean",
                       "long_n", "long_wr", "long_pnl_mean",
                       "short_n", "short_wr", "short_pnl_mean"]]
    print(summary.to_string(index=False))
    summary.to_csv(OUT_DIR / "policy_comparison.csv", index=False)

    # Identificar la mejor política (por PnL total)
    best_pnl = max(r["pnl_total"] for r in results.values())
    best_policy = next(name for name, r in results.items() if r["pnl_total"] == best_pnl)
    print(f"\n>>> Mejor política por PnL total: {best_policy} ({best_pnl:+.2f})")

    # Mejor política por PnL medio (con mínimo 1000 señales tomadas)
    candidates = [(name, r) for name, r in results.items() if r["n_taken"] >= 1000]
    if candidates:
        best_mean = max(r["pnl_mean"] for _, r in candidates)
        best_mean_policy = next(name for name, r in candidates if r["pnl_mean"] == best_mean)
        print(f">>> Mejor política por PnL medio (n>=1000): {best_mean_policy} ({best_mean:+.4f})")

    # Guardar JSON
    payload = {
        "config": {
            "alpha": ALPHA, "window": WINDOW, "pattern_len": PATTERN_LEN,
            "train_candles": TRAIN_CANDLES, "test_candles": TEST_CANDLES,
            "symbols": SYMBOLS,
        },
        "policies_evaluated": [name for name, _ in POLICIES],
        "results": results,
        "best_pnl_total": {"policy": best_policy, "pnl_total": best_pnl},
    }
    (OUT_DIR / "policy_comparison.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nResultados guardados en:")
    print(f"  {OUT_DIR / 'policy_comparison.csv'}")
    print(f"  {OUT_DIR / 'policy_comparison.json'}")
    print(f"  {OUT_DIR / 'per_signal_rich.csv'}  ({len(df):,} señales)")


if __name__ == "__main__":
    main()
