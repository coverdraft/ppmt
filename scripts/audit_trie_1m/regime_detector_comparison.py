"""
Comparativa de 5 detectores de régimen sobre dataset 1m crypto.

Para cada detector evalúa:
  1. Acuerdo con etiquetado humano (bull/bear/range ventanas visualmente claras)
  2. Estabilidad TRAIN vs TEST (distribución por split)
  3. Distribución de regímenes (%)
  4. Correlación con resultados LONG y SHORT (señales sintéticas vía cruces EMA)

Output: /home/z/my-project/download/regime_detector_comparison/
"""

import os
import sys
import json
import time
from pathlib import Path
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

# Importar los 5 detectores
sys.path.insert(0, "/home/z/my-project/scripts")
from regime_detectors_v2 import (
    DETECTORS,
    detector_adx, detector_ema_slope, detector_bollinger,
    detector_adx_ema, detector_adx_ema_bb,
    compute_adx, compute_bollinger_width,
)

# ----------------------------- Config ----------------------------- #

DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR = Path("/home/z/my-project/download/regime_detector_comparison")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TOKENS = []
for p in sorted(DATA_DIR.glob("*_1m.csv")):
    sym = p.stem.replace("_1m", "")
    n_rows = sum(1 for _ in open(p)) - 1
    if n_rows >= 50_000:  # mínimo 50k velas
        TOKENS.append(sym)
print(f"Tokens disponibles: {TOKENS}")

TRAIN_FRAC = 0.75   # 75% train, 25% test
LOOKBACK = 50       # ventanas de evaluación

# ----------------------------- Helpers ----------------------------- #

def load_ohlcv(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(p)
    df.columns = [c.lower() for c in df.columns]
    # Binance kline format: open_time, open, high, low, close, volume, close_time, ...
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["ts", "open", "high", "low", "close", "volume"]].set_index("ts")


def distribution(labels: np.ndarray) -> dict:
    c = Counter(labels)
    total = sum(c.values())
    if total == 0:
        return {}
    return {k: round(100 * v / total, 2) for k, v in c.items()}


# --------------------- Human-labeled windows --------------------- #
# Generamos etiquetas humanas programáticas: ventanas donde el retorno
# acumulado y la volatilidad son consistentes con bull/bear/range.
# Esto NO es etiquetado manual visual, pero es un proxy objetivo y reproducible.

def generate_pseudo_human_labels(prices: np.ndarray, window: int = 200,
                                  step: int = 50) -> list:
    """
    Genera etiquetas pseudo-humanas basadas en retorno acumulado y volatilidad.

    Criterio (calibrado para 1m crypto, window=200 velas):
      - bull:    retorno acumulado > +3%   Y   vol < 1.5× mediana
      - bear:    retorno acumulado < -3%   Y   vol < 1.5× mediana
      - range:   |retorno| < 1%            Y   vol < 1.0× mediana
      - volatile: vol > 2.0× mediana (excluida del acuerdo humano)
    """
    n = len(prices)
    if n < window:
        return []

    rets = np.diff(prices) / prices[:-1]
    vol_rolling = pd.Series(rets).rolling(window).std().fillna(0).values
    median_vol = np.median(vol_rolling[window:])

    windows = []
    for start in range(0, n - window, step):
        end = start + window - 1
        cum_ret = (prices[end] - prices[start]) / prices[start]
        vol = vol_rolling[end]
        rel_vol = vol / median_vol if median_vol > 0 else 1.0

        if rel_vol > 2.0:
            label = "volatile"
        elif cum_ret > 0.03 and rel_vol < 1.5:
            label = "bull"
        elif cum_ret < -0.03 and rel_vol < 1.5:
            label = "bear"
        elif abs(cum_ret) < 0.01 and rel_vol < 1.0:
            label = "range"
        else:
            label = None  # ambiguous, skip

        if label is not None:
            windows.append({
                "start": start,
                "end": end,
                "label": label,
                "cum_ret": float(cum_ret),
                "rel_vol": float(rel_vol),
            })

    return windows


def evaluate_human_agreement(prices: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                              detector_fn, windows: list) -> dict:
    """
    Para cada ventana humana, aplica el detector a toda la ventana y toma
    el régimen modal (más frecuente). Compara con la etiqueta humana.

    Mapping: bull → trending_up, bear → trending_down, range → ranging.
    """
    mapping = {
        "bull": "trending_up",
        "bear": "trending_down",
        "range": "ranging",
        "volatile": "volatile",
    }

    # Calcular labels del detector para toda la serie una vez
    all_labels = detector_fn(prices, highs, lows)

    results = defaultdict(lambda: {"correct": 0, "total": 0})
    for w in windows:
        if w["label"] not in mapping:
            continue
        expected = mapping[w["label"]]
        window_labels = all_labels[w["start"]:w["end"] + 1]
        if len(window_labels) == 0:
            continue
        # Régimen modal
        c = Counter(window_labels)
        detected = c.most_common(1)[0][0]
        results[w["label"]]["total"] += 1
        if detected == expected:
            results[w["label"]]["correct"] += 1

    out = {}
    for k, v in results.items():
        if v["total"] > 0:
            out[k] = {
                "correct": v["correct"],
                "total": v["total"],
                "pct": round(100 * v["correct"] / v["total"], 1),
            }
        else:
            out[k] = {"correct": 0, "total": 0, "pct": 0.0}
    return out


# --------------------- Synthetic LONG/SHORT PnL --------------------- #
# Genera señales LONG/SHORT sintéticas: en cada momento, LONG = mantener
# posición larga 50 velas, SHORT = mantener posición corta 50 velas.
# Calcula PnL por régimen detectado.

def compute_pnl_by_regime(prices: np.ndarray, labels: np.ndarray,
                          hold: int = 50) -> dict:
    """
    Para cada vela i, simula:
      LONG:  comprar en i, vender en i+hold → pnl = close[i+hold]/close[i] - 1
      SHORT: vender corto en i, cubrir en i+hold → pnl = -(close[i+hold]/close[i] - 1)
    El régimen es labels[i].

    Retorna dict: {regime: {"LONG": {"n": int, "mean_pnl": float, "win_rate": float},
                              "SHORT": {...}}}
    """
    n = len(prices)
    if n < hold + 1:
        return {}

    # Vectorizado: forward returns
    fwd_ret = np.zeros(n)
    fwd_ret[:n - hold] = prices[hold:] / prices[:n - hold] - 1.0

    out = {}
    for regime in ["trending_up", "trending_down", "ranging", "volatile"]:
        mask = labels == regime
        mask[:LOOKBACK] = False  # warmup
        mask[-hold:] = False     # no hay forward
        idx = np.where(mask)[0]
        if len(idx) == 0:
            out[regime] = {
                "LONG": {"n": 0, "mean_pnl": 0.0, "win_rate": 0.0},
                "SHORT": {"n": 0, "mean_pnl": 0.0, "win_rate": 0.0},
            }
            continue

        long_pnls = fwd_ret[idx]
        short_pnls = -fwd_ret[idx]

        out[regime] = {
            "LONG": {
                "n": int(len(idx)),
                "mean_pnl": float(np.mean(long_pnls)),
                "win_rate": float(np.mean(long_pnls > 0)),
            },
            "SHORT": {
                "n": int(len(idx)),
                "mean_pnl": float(np.mean(short_pnls)),
                "win_rate": float(np.mean(short_pnls > 0)),
            },
        }
    return out


# --------------------- Main --------------------- #

def main():
    print("=" * 70)
    print("Comparativa de 5 detectores de régimen")
    print("=" * 70)

    results = {
        "tokens": TOKENS,
        "detectors": list(DETECTORS.keys()),
        "per_token": {},
        "aggregate": {},
    }

    # Agregados
    agg_labels = {det: [] for det in DETECTORS}
    agg_labels_train = {det: [] for det in DETECTORS}
    agg_labels_test = {det: [] for det in DETECTORS}
    agg_pnl = {det: defaultdict(lambda: {"LONG": [], "SHORT": []}) for det in DETECTORS}
    agg_human = {det: defaultdict(lambda: {"correct": 0, "total": 0}) for det in DETECTORS}

    for sym in TOKENS:
        print(f"\n>>> {sym}")
        df = load_ohlcv(sym)
        prices = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        n = len(prices)
        train_end = int(n * TRAIN_FRAC)
        print(f"    {n:,} velas | train={train_end:,} test={n-train_end:,}")

        # Generar etiquetas pseudo-humanas (sobre TODO el dataset)
        windows = generate_pseudo_human_labels(prices, window=200, step=50)
        print(f"    {len(windows)} ventanas pseudo-humanas")

        token_results = {"n_candles": n, "detectors": {}}

        for det_name, det_fn in DETECTORS.items():
            t0 = time.time()
            labels = det_fn(prices, highs, lows)
            elapsed = time.time() - t0

            dist_all = distribution(labels)
            dist_train = distribution(labels[:train_end])
            dist_test = distribution(labels[train_end:])

            # Acuerdo humano
            ha = evaluate_human_agreement(prices, highs, lows, det_fn, windows)

            # PnL por régimen
            pnl = compute_pnl_by_regime(prices, labels, hold=50)

            token_results["detectors"][det_name] = {
                "time_s": round(elapsed, 3),
                "dist_all": dist_all,
                "dist_train": dist_train,
                "dist_test": dist_test,
                "human_agreement": ha,
                "pnl_by_regime": pnl,
            }

            # Agregar
            agg_labels[det_name].extend(labels.tolist())
            agg_labels_train[det_name].extend(labels[:train_end].tolist())
            agg_labels_test[det_name].extend(labels[train_end:].tolist())
            for regime, stats in pnl.items():
                for direction in ["LONG", "SHORT"]:
                    if stats[direction]["n"] > 0:
                        agg_pnl[det_name][regime][direction].append(
                            stats[direction]["mean_pnl"]
                        )
            for label_key, stats in ha.items():
                agg_human[det_name][label_key]["correct"] += stats["correct"]
                agg_human[det_name][label_key]["total"] += stats["total"]

            print(f"    [{det_name}] {elapsed:.2f}s | dist={dist_all}")

        results["per_token"][sym] = token_results

    # -------- Agregados finales -------- #
    print("\n" + "=" * 70)
    print("AGREGADO FINAL")
    print("=" * 70)

    for det_name in DETECTORS:
        agg = {
            "dist_all": distribution(np.array(agg_labels[det_name])),
            "dist_train": distribution(np.array(agg_labels_train[det_name])),
            "dist_test": distribution(np.array(agg_labels_test[det_name])),
            "human_agreement": {
                k: {
                    "correct": v["correct"],
                    "total": v["total"],
                    "pct": round(100 * v["correct"] / v["total"], 1) if v["total"] > 0 else 0.0,
                }
                for k, v in agg_human[det_name].items()
            },
            "pnl_by_regime": {},
            "train_test_stability": {},
        }

        # PnL promedio por régimen (media de medias por token)
        for regime in ["trending_up", "trending_down", "ranging", "volatile"]:
            long_pnls = agg_pnl[det_name][regime]["LONG"]
            short_pnls = agg_pnl[det_name][regime]["SHORT"]
            agg["pnl_by_regime"][regime] = {
                "LONG": {
                    "n_tokens": len(long_pnls),
                    "mean_pnl": float(np.mean(long_pnls)) if long_pnls else 0.0,
                },
                "SHORT": {
                    "n_tokens": len(short_pnls),
                    "mean_pnl": float(np.mean(short_pnls)) if short_pnls else 0.0,
                },
            }

        # Estabilidad TRAIN vs TEST: diferencia absoluta en puntos porcentuales
        for regime in ["trending_up", "trending_down", "ranging", "volatile"]:
            t = agg["dist_train"].get(regime, 0.0)
            te = agg["dist_test"].get(regime, 0.0)
            agg["train_test_stability"][regime] = {
                "train_pct": t,
                "test_pct": te,
                "abs_diff_pp": round(abs(t - te), 2),
            }
        # Métrica de estabilidad global: máxima diferencia porcentual
        max_diff = max(
            agg["train_test_stability"][r]["abs_diff_pp"]
            for r in ["trending_up", "trending_down", "ranging", "volatile"]
        )
        agg["train_test_stability"]["max_diff_pp"] = max_diff

        # Separabilidad: cuánto se diferencian los PnL LONG en trending_up vs ranging
        pnl_up = agg["pnl_by_regime"]["trending_up"]["LONG"]["mean_pnl"]
        pnl_range = agg["pnl_by_regime"]["ranging"]["LONG"]["mean_pnl"]
        agg["separability_long"] = round(pnl_up - pnl_range, 6)

        pnl_dn = agg["pnl_by_regime"]["trending_down"]["SHORT"]["mean_pnl"]
        agg["separability_short"] = round(pnl_dn - pnl_range, 6)

        results["aggregate"][det_name] = agg

        print(f"\n[{det_name}]")
        print(f"  Distribución all: {agg['dist_all']}")
        print(f"  Distribución train: {agg['dist_train']}")
        print(f"  Distribución test:  {agg['dist_test']}")
        print(f"  Max diff train/test: {agg['train_test_stability']['max_diff_pp']:.2f}pp")
        print(f"  Acuerdo humano:")
        for k, v in agg["human_agreement"].items():
            print(f"    {k}: {v['correct']}/{v['total']} ({v['pct']}%)")
        print(f"  PnL LONG por régimen:")
        for r in ["trending_up", "trending_down", "ranging", "volatile"]:
            p = agg["pnl_by_regime"][r]["LONG"]
            print(f"    {r}: {p['mean_pnl']*100:+.4f}% (n_tokens={p['n_tokens']})")
        print(f"  PnL SHORT por régimen:")
        for r in ["trending_up", "trending_down", "ranging", "volatile"]:
            p = agg["pnl_by_regime"][r]["SHORT"]
            print(f"    {r}: {p['mean_pnl']*100:+.4f}% (n_tokens={p['n_tokens']})")
        print(f"  Separabilidad LONG (up - range): {agg['separability_long']*100:+.4f}%")
        print(f"  Separabilidad SHORT (down - range): {agg['separability_short']*100:+.4f}%")

    # -------- Guardar -------- #
    out_json = OUT_DIR / "comparison.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n✓ JSON: {out_json}")

    # CSV resumen
    rows = []
    for det_name, agg in results["aggregate"].items():
        rows.append({
            "detector": det_name,
            "ranging_pct": agg["dist_all"].get("ranging", 0),
            "trending_up_pct": agg["dist_all"].get("trending_up", 0),
            "trending_down_pct": agg["dist_all"].get("trending_down", 0),
            "volatile_pct": agg["dist_all"].get("volatile", 0),
            "max_diff_train_test_pp": agg["train_test_stability"]["max_diff_pp"],
            "human_bull_pct": agg["human_agreement"].get("bull", {}).get("pct", 0),
            "human_bear_pct": agg["human_agreement"].get("bear", {}).get("pct", 0),
            "human_range_pct": agg["human_agreement"].get("range", {}).get("pct", 0),
            "pnl_long_up": agg["pnl_by_regime"]["trending_up"]["LONG"]["mean_pnl"],
            "pnl_long_range": agg["pnl_by_regime"]["ranging"]["LONG"]["mean_pnl"],
            "pnl_short_down": agg["pnl_by_regime"]["trending_down"]["SHORT"]["mean_pnl"],
            "pnl_short_range": agg["pnl_by_regime"]["ranging"]["SHORT"]["mean_pnl"],
            "separability_long": agg["separability_long"],
            "separability_short": agg["separability_short"],
        })
    df_summary = pd.DataFrame(rows)
    out_csv = OUT_DIR / "comparison_summary.csv"
    df_summary.to_csv(out_csv, index=False)
    print(f"✓ CSV: {out_csv}")

    # Reporte MD
    md = generate_md_report(results, df_summary)
    out_md = OUT_DIR / "comparison_report.md"
    with open(out_md, "w") as f:
        f.write(md)
    print(f"✓ MD: {out_md}")

    print("\n" + "=" * 70)
    print("COMPARATIVA COMPLETA")
    print("=" * 70)


def generate_md_report(results: dict, df_summary: pd.DataFrame) -> str:
    lines = []
    lines.append("# Comparativa de Detectores de Régimen (1m crypto)\n")
    lines.append(f"**Fecha**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"**Tokens**: {', '.join(results['tokens'])}")
    lines.append(f"**Total velas**: {sum(t['n_candles'] for t in results['per_token'].values()):,}\n")

    lines.append("## Resumen ejecutivo\n")
    lines.append("| Detector | Ranging% | Up% | Down% | Vol% | ΔT/T pp | Bull% | Bear% | Range% | PnL LONG up | PnL SHORT down | Sep LONG | Sep SHORT |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, row in df_summary.iterrows():
        lines.append(
            f"| {row['detector']} | "
            f"{row['ranging_pct']:.1f} | "
            f"{row['trending_up_pct']:.1f} | "
            f"{row['trending_down_pct']:.1f} | "
            f"{row['volatile_pct']:.1f} | "
            f"{row['max_diff_train_test_pp']:.1f} | "
            f"{row['human_bull_pct']:.0f} | "
            f"{row['human_bear_pct']:.0f} | "
            f"{row['human_range_pct']:.0f} | "
            f"{row['pnl_long_up']*100:+.4f} | "
            f"{row['pnl_short_down']*100:+.4f} | "
            f"{row['separability_long']*100:+.4f} | "
            f"{row['separability_short']*100:+.4f} |"
        )
    lines.append("")

    lines.append("## Interpretación de métricas\n")
    lines.append("- **Ranging% / Up% / Down% / Vol%**: distribución global del detector.")
    lines.append("- **ΔT/T pp**: máxima diferencia porcentual TRAIN vs TEST (menor = más estable).")
    lines.append("- **Bull% / Bear% / Range%**: acuerdo con etiquetado pseudo-humano (mayor = mejor).")
    lines.append("- **PnL LONG up**: PnL medio de LONG cuando el detector dice 'trending_up' (debería ser >0).")
    lines.append("- **PnL SHORT down**: PnL medio de SHORT cuando el detector dice 'trending_down' (debería ser >0).")
    lines.append("- **Sep LONG**: PnL LONG en up − PnL LONG en range (mayor = mejor separación).")
    lines.append("- **Sep SHORT**: PnL SHORT en down − PnL SHORT en range (mayor = mejor separación).\n")

    # Veredicto
    lines.append("## Veredicto\n")
    # Ranking: score = separability_long + separability_short + agreement_avg
    df_summary["score"] = (
        df_summary["separability_long"] +
        df_summary["separability_short"] +
        df_summary[["human_bull_pct", "human_bear_pct", "human_range_pct"]].mean(axis=1) / 1000
    )
    best = df_summary.sort_values("score", ascending=False).iloc[0]
    lines.append(f"**Mejor detector global**: `{best['detector']}`")
    lines.append(f"  - Sep LONG = {best['separability_long']*100:+.4f}%")
    lines.append(f"  - Sep SHORT = {best['separability_short']*100:+.4f}%")
    lines.append(f"  - Acuerdo humano (bull/bear/range): "
                  f"{best['human_bull_pct']:.0f}% / "
                  f"{best['human_bear_pct']:.0f}% / "
                  f"{best['human_range_pct']:.0f}%")
    lines.append(f"  - Distribución: {best['trending_up_pct']:.1f}% up / "
                  f"{best['trending_down_pct']:.1f}% down / "
                  f"{best['ranging_pct']:.1f}% range / "
                  f"{best['volatile_pct']:.1f}% vol")
    lines.append("")

    # Detalle por detector
    lines.append("## Detalle por detector\n")
    for det_name, agg in results["aggregate"].items():
        lines.append(f"### {det_name}\n")
        lines.append(f"- **Distribución all**: {agg['dist_all']}")
        lines.append(f"- **Distribución train**: {agg['dist_train']}")
        lines.append(f"- **Distribución test**:  {agg['dist_test']}")
        lines.append(f"- **Estabilidad (max diff T/T)**: {agg['train_test_stability']['max_diff_pp']:.2f} pp")
        lines.append(f"- **Acuerdo humano**:")
        for k, v in agg["human_agreement"].items():
            lines.append(f"  - {k}: {v['correct']}/{v['total']} ({v['pct']}%)")
        lines.append(f"- **PnL LONG por régimen**:")
        for r in ["trending_up", "trending_down", "ranging", "volatile"]:
            p = agg["pnl_by_regime"][r]["LONG"]
            lines.append(f"  - {r}: {p['mean_pnl']*100:+.4f}% (n_tokens={p['n_tokens']})")
        lines.append(f"- **PnL SHORT por régimen**:")
        for r in ["trending_up", "trending_down", "ranging", "volatile"]:
            p = agg["pnl_by_regime"][r]["SHORT"]
            lines.append(f"  - {r}: {p['mean_pnl']*100:+.4f}% (n_tokens={p['n_tokens']})")
        lines.append(f"- **Separabilidad LONG (up − range)**: {agg['separability_long']*100:+.4f}%")
        lines.append(f"- **Separabilidad SHORT (down − range)**: {agg['separability_short']*100:+.4f}%\n")

    lines.append("## Recomendación\n")
    lines.append("El detector con MAYOR `separability_long + separability_short` y MAYOR acuerdo humano")
    lines.append("es el que mejor separa los contextos donde el trie obtiene resultados distintos.\n")
    lines.append("Si el ganador es `adx_ema_bb` (combo completo), implementarlo como nuevo `RegimeDetector`.")
    lines.append("Si el ganador es `adx_ema` (sin Bollinger), evaluar si la complejidad extra de BB justifica.")
    lines.append("Si `adx` solo gana, descartar EMA slope y Bollinger (simplificación).\n")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
