"""
Experimento crítico: ¿particionar por régimen aporta información?

Compara 4 configuraciones usando EXACTAMENTE el mismo walk-forward:
  1. Trie único (N3): todos los patrones SAX de train juntos
  2. Trie particionado por detector A (Bollinger Width — mejor Sep LONG)
  3. Trie particionado por detector B (ADX+EMA+BB — mejor Sep LONG combo)
  4. Trie particionado por detector C (EMA slope — más simple)

Metodología (k-NN SAX):
- Codificar cada vela como patrón SAX (window=10, alpha=4, pattern_length=5)
  → 50 velas → 5 símbolos
- Para cada vela i en test, buscar matches EXACTOS en train (mismo patrón SAX)
- Predecir retorno forward (50 velas) promediando los matches
- Comparar:
  a) Trie único: usa TODOS los matches en train
  b) Trie particionado: usa solo matches en train donde régimen(detector) == régimen(i)
- Métrica: MAE entre predicción y retorno forward realizado
- Score: MAE(trie único) − MAE(trie particionado). Si >0, particionar aporta.

Si NINGÚN detector mejora el trie único, la hipótesis de particionar es FALSA.
Si ALGÚN detector mejora, ese detector es el candidato para FIX-17.

Output: /home/z/my-project/download/regime_partition_experiment/
"""

import sys
import json
import time
from pathlib import Path
from collections import defaultdict, Counter

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/scripts")
sys.path.insert(0, "/home/z/my-project/ppmt/src")

from regime_detectors_v2 import (
    detector_adx, detector_ema_slope, detector_bollinger,
    detector_adx_ema, detector_adx_ema_bb,
)
from ppmt.core.sax import SAXEncoder, SAX_BREAKPOINTS

# ----------------------- Config ----------------------- #

DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR = Path("/home/z/my-project/download/regime_partition_experiment")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# SAX config (igual que PPMT en producción)
SAX_ALPHA = 4
SAX_WINDOW = 10        # 10 velas por símbolo
PATTERN_LENGTH = 5     # patrón de 5 símbolos = 50 velas

TRAIN_FRAC = 0.75
FORWARD_HORIZON = 50   # predecir retorno 50 velas adelante

# Detectores a comparar
DETECTORS = {
    "bollinger":    detector_bollinger,
    "adx_ema_bb":   detector_adx_ema_bb,
    "ema_slope":    detector_ema_slope,
}

TOKENS = []
for p in sorted(DATA_DIR.glob("*_1m.csv")):
    sym = p.stem.replace("_1m", "")
    n_rows = sum(1 for _ in open(p)) - 1
    if n_rows >= 50_000:
        TOKENS.append(sym)
print(f"Tokens: {TOKENS}")

# ----------------------- SAX Pattern Generation ----------------------- #

def encode_sax_patterns(df: pd.DataFrame, encoder: SAXEncoder,
                        pattern_length: int) -> np.ndarray:
    """
    Genera todos los patrones SAX (deslizantes) para un DataFrame.
    Retorna array 2D de shape (n_patterns, pattern_length) con enteros 0..alpha-1.

    Cada patrón empieza en vela i y cubre i..i+pattern_length*window_size.
    """
    n = len(df)
    pw = pattern_length * encoder.window_size
    n_patterns = n - pw + 1
    if n_patterns <= 0:
        return np.array([])

    # Codificar de forma deslizante: para cada inicio i, tomar df[i:i+pw]
    # y codificar con el encoder.
    patterns = np.zeros((n_patterns, pattern_length), dtype=np.int8)
    for i in range(n_patterns):
        block = df.iloc[i:i + pw]
        symbols = encoder.encode(block)
        # Convertir letras a enteros 0..alpha-1
        for j, s in enumerate(symbols):
            patterns[i, j] = ord(s) - ord('a')

    return patterns


def compute_forward_returns(prices: np.ndarray, horizon: int) -> np.ndarray:
    """forward_returns[i] = prices[i+horizon] / prices[i] - 1."""
    n = len(prices)
    fwd = np.zeros(n)
    if n > horizon:
        fwd[:n - horizon] = prices[horizon:] / prices[:n - horizon] - 1.0
    return fwd


# ----------------------- Partition Experiment ----------------------- #

def run_experiment_for_token(sym: str, df: pd.DataFrame) -> dict:
    """
    Para un token, compara trie único vs trie particionado por detector.
    """
    n = len(df)
    train_end = int(n * TRAIN_FRAC)
    print(f"\n  [{sym}] {n:,} velas, train={train_end:,} test={n-train_end:,}")

    # 1. Generar patrones SAX para TODO el dataset
    encoder = SAXEncoder(alphabet_size=SAX_ALPHA, window_size=SAX_WINDOW,
                          strategy="ohlcv")
    print(f"  [{sym}] codificando patrones SAX...")
    t0 = time.time()
    patterns = encode_sax_patterns(df, encoder, PATTERN_LENGTH)
    print(f"  [{sym}] {patterns.shape[0]:,} patrones en {time.time()-t0:.1f}s")

    # 2. Forward returns
    prices = df["close"].values.astype(float)
    fwd = compute_forward_returns(prices, FORWARD_HORIZON)

    # El patrón i empieza en vela i y cubre 50 velas (PATTERN_LENGTH × SAX_WINDOW)
    # La "predicción" es el forward return de la última vela del patrón: i + pw - 1
    pw = PATTERN_LENGTH * SAX_WINDOW
    pattern_fwd = np.zeros(len(patterns))
    for i in range(len(patterns)):
        end_idx = i + pw - 1
        if end_idx < len(fwd):
            pattern_fwd[i] = fwd[end_idx]

    # 3. Split train/test
    # El patrón i "pertenece" al test si su vela de inicio está en test
    pattern_train_mask = np.zeros(len(patterns), dtype=bool)
    pattern_test_mask = np.zeros(len(patterns), dtype=bool)
    for i in range(len(patterns)):
        if i < train_end - pw:
            pattern_train_mask[i] = True
        elif i >= train_end:
            pattern_test_mask[i] = True

    n_train = pattern_train_mask.sum()
    n_test = pattern_test_mask.sum()
    print(f"  [{sym}] patterns train={n_train:,} test={n_test:,}")

    # 4. Para cada detector, generar labels de régimen para TODO el dataset
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    closes = df["close"].values.astype(float)

    detector_labels = {}
    for det_name, det_fn in DETECTORS.items():
        t0 = time.time()
        labels = det_fn(closes, highs, lows)
        detector_labels[det_name] = labels
        print(f"  [{sym}] detector {det_name}: {time.time()-t0:.1f}s, dist={Counter(labels)}")

    # 5. Para cada patrón, su régimen = régimen de la última vela del patrón
    pattern_regimes = {}
    for det_name, labels in detector_labels.items():
        pr = np.empty(len(patterns), dtype="<U14")
        for i in range(len(patterns)):
            end_idx = i + pw - 1
            if end_idx < len(labels):
                pr[i] = labels[end_idx]
            else:
                pr[i] = "ranging"
        pattern_regimes[det_name] = pr

    # 6. Indexar train por patrón SAX (trie único)
    # train_index[pattern_tuple] = [list of pattern indices]
    print(f"  [{sym}] indexando patrones train...")
    train_index = defaultdict(list)
    train_fwd_by_pattern = defaultdict(list)
    for i in np.where(pattern_train_mask)[0]:
        key = tuple(patterns[i])
        train_index[key].append(i)
        train_fwd_by_pattern[key].append(pattern_fwd[i])

    # Pre-calcular media de fwd por pattern en train
    train_fwd_mean = {k: float(np.mean(v)) for k, v in train_fwd_by_pattern.items()}
    train_fwd_count = {k: len(v) for k, v in train_fwd_by_pattern.items()}

    # 7. Indexar train por (patrón, régimen) para cada detector (tries particionados)
    print(f"  [{sym}] indexando patrones train por régimen...")
    partitioned_indexes = {}
    for det_name in DETECTORS:
        idx = defaultdict(list)
        fwd_by_pat_reg = defaultdict(list)
        for i in np.where(pattern_train_mask)[0]:
            key = (tuple(patterns[i]), pattern_regimes[det_name][i])
            idx[key].append(i)
            fwd_by_pat_reg[key].append(pattern_fwd[i])
        partitioned_indexes[det_name] = {
            "index": idx,
            "fwd_mean": {k: float(np.mean(v)) for k, v in fwd_by_pat_reg.items()},
            "fwd_count": {k: len(v) for k, v in fwd_by_pat_reg.items()},
        }
        print(f"  [{sym}] {det_name}: {len(idx):,} (patrón, régimen) buckets en train")

    # 8. Evaluar sobre TEST
    print(f"  [{sym}] evaluando sobre {n_test:,} patrones test...")

    results = {
        "n_train": int(n_train),
        "n_test": int(n_test),
        "n_unique_train_patterns": len(train_index),
        "trie_unique": {"mae": 0.0, "mse": 0.0, "n_matched": 0, "n_total": 0,
                         "predictions": [], "actuals": []},
        "partitioned": {},
    }

    # Trie único
    pred_errors = []
    n_matched = 0
    for i in np.where(pattern_test_mask)[0]:
        key = tuple(patterns[i])
        actual = pattern_fwd[i]
        if key in train_fwd_mean and train_fwd_count[key] >= 1:
            pred = train_fwd_mean[key]
            pred_errors.append(abs(pred - actual))
            n_matched += 1
    results["trie_unique"]["mae"] = float(np.mean(pred_errors)) if pred_errors else 0.0
    results["trie_unique"]["mse"] = float(np.mean(np.array(pred_errors)**2)) if pred_errors else 0.0
    results["trie_unique"]["n_matched"] = n_matched
    results["trie_unique"]["n_total"] = int(n_test)
    print(f"  [{sym}] Trie único: MAE={results['trie_unique']['mae']*100:.4f}% matched={n_matched}/{n_test}")

    # Tries particionados
    for det_name in DETECTORS:
        part = partitioned_indexes[det_name]
        pred_errors_p = []
        n_matched_p = 0
        n_fallback = 0  # casos donde no hay match en régimen → fallback a trie único
        for i in np.where(pattern_test_mask)[0]:
            key_p = (tuple(patterns[i]), pattern_regimes[det_name][i])
            actual = pattern_fwd[i]
            if key_p in part["fwd_mean"] and part["fwd_count"][key_p] >= 1:
                pred = part["fwd_mean"][key_p]
                pred_errors_p.append(abs(pred - actual))
                n_matched_p += 1
            else:
                # Fallback: usar trie único
                key = tuple(patterns[i])
                if key in train_fwd_mean and train_fwd_count[key] >= 1:
                    pred = train_fwd_mean[key]
                    pred_errors_p.append(abs(pred - actual))
                    n_fallback += 1
        mae_p = float(np.mean(pred_errors_p)) if pred_errors_p else 0.0
        mse_p = float(np.mean(np.array(pred_errors_p)**2)) if pred_errors_p else 0.0
        results["partitioned"][det_name] = {
            "mae": mae_p,
            "mse": mse_p,
            "n_matched": n_matched_p,
            "n_fallback": n_fallback,
            "n_total": int(n_test),
            "mae_diff_vs_unique": results["trie_unique"]["mae"] - mae_p,  # >0 = mejor
        }
        print(f"  [{sym}] {det_name}: MAE={mae_p*100:.4f}% matched={n_matched_p} fallback={n_fallback} "
              f"ΔMAE={results['trie_unique']['mae']-mae_p:+.6f}")

    return results


# ----------------------- Main ----------------------- #

def main():
    print("=" * 70)
    print("Experimento: ¿particionar por régimen aporta información?")
    print(f"SAX config: alpha={SAX_ALPHA}, window={SAX_WINDOW}, pattern_length={PATTERN_LENGTH}")
    print(f"Walk-forward: train {TRAIN_FRAC*100:.0f}% / test {(1-TRAIN_FRAC)*100:.0f}%")
    print(f"Forward horizon: {FORWARD_HORIZON} velas")
    print(f"Detectores: {list(DETECTORS.keys())}")
    print(f"Tokens: {len(TOKENS)}")
    print("=" * 70)

    all_results = {}
    for sym in TOKENS:
        print(f"\n>>> {sym}")
        p = DATA_DIR / f"{sym}_1m.csv"
        df = pd.read_csv(p)
        df.columns = [c.lower() for c in df.columns]
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df[["ts", "open", "high", "low", "close", "volume"]].set_index("ts")
        all_results[sym] = run_experiment_for_token(sym, df)

    # Agregar
    print("\n" + "=" * 70)
    print("AGREGADO")
    print("=" * 70)

    agg = {
        "trie_unique": {"mae": [], "n_matched": [], "n_total": []},
        "partitioned": {det: {"mae": [], "n_matched": [], "n_fallback": [], "n_total": [], "mae_diff": []}
                         for det in DETECTORS},
    }

    for sym, r in all_results.items():
        agg["trie_unique"]["mae"].append(r["trie_unique"]["mae"])
        agg["trie_unique"]["n_matched"].append(r["trie_unique"]["n_matched"])
        agg["trie_unique"]["n_total"].append(r["trie_unique"]["n_total"])
        for det in DETECTORS:
            if det in r["partitioned"]:
                agg["partitioned"][det]["mae"].append(r["partitioned"][det]["mae"])
                agg["partitioned"][det]["n_matched"].append(r["partitioned"][det]["n_matched"])
                agg["partitioned"][det]["n_fallback"].append(r["partitioned"][det]["n_fallback"])
                agg["partitioned"][det]["n_total"].append(r["partitioned"][det]["n_total"])
                agg["partitioned"][det]["mae_diff"].append(r["partitioned"][det]["mae_diff_vs_unique"])

    # Medias
    summary = {
        "trie_unique": {
            "mae_mean": float(np.mean(agg["trie_unique"]["mae"])),
            "n_matched_total": int(sum(agg["trie_unique"]["n_matched"])),
            "n_total": int(sum(agg["trie_unique"]["n_total"])),
            "match_rate_pct": round(100 * sum(agg["trie_unique"]["n_matched"]) / max(1, sum(agg["trie_unique"]["n_total"])), 2),
        },
        "partitioned": {},
    }

    print(f"\nTrie único: MAE medio = {summary['trie_unique']['mae_mean']*100:.4f}%  "
          f"matched {summary['trie_unique']['n_matched_total']:,}/{summary['trie_unique']['n_total']:,} "
          f"({summary['trie_unique']['match_rate_pct']}%)")

    for det in DETECTORS:
        mae_mean = float(np.mean(agg["partitioned"][det]["mae"]))
        mae_diff_mean = float(np.mean(agg["partitioned"][det]["mae_diff"]))
        n_matched = int(sum(agg["partitioned"][det]["n_matched"]))
        n_fallback = int(sum(agg["partitioned"][det]["n_fallback"]))
        n_total = int(sum(agg["partitioned"][det]["n_total"]))
        summary["partitioned"][det] = {
            "mae_mean": mae_mean,
            "mae_diff_vs_unique": mae_diff_mean,  # >0 = mejor que único
            "n_matched": n_matched,
            "n_fallback": n_fallback,
            "n_total": n_total,
            "match_rate_pct": round(100 * n_matched / max(1, n_total), 2),
            "improvement_pct": round(100 * mae_diff_mean / max(1e-9, summary["trie_unique"]["mae_mean"]), 2),
        }
        print(f"\nParticionado por {det}:")
        print(f"  MAE medio = {mae_mean*100:.4f}% (vs único {summary['trie_unique']['mae_mean']*100:.4f}%)")
        print(f"  ΔMAE = {mae_diff_mean*100:+.6f}%  ({'MEJOR' if mae_diff_mean > 0 else 'PEOR'} que único)")
        print(f"  matched {n_matched:,} + fallback {n_fallback:,} / {n_total:,} "
              f"({100*n_matched/max(1,n_total):.2f}% match directo)")
        print(f"  Improvement relativo = {summary['partitioned'][det]['improvement_pct']:+.2f}%")

    # Veredicto
    print("\n" + "=" * 70)
    print("VEREDICTO")
    print("=" * 70)

    any_improves = False
    for det in DETECTORS:
        s = summary["partitioned"][det]
        if s["mae_diff_vs_unique"] > 0:
            print(f"✓ {det}: mejora trie único en {s['mae_diff_vs_unique']*100:+.4f}% "
                  f"(relativo {s['improvement_pct']:+.2f}%)")
            any_improves = True
        else:
            print(f"✗ {det}: NO mejora trie único ({s['mae_diff_vs_unique']*100:+.4f}%, "
                  f"relativo {s['improvement_pct']:+.2f}%)")

    if not any_improves:
        print("\n⚠️  NINGÚN detector mejora el trie único.")
        print("    → La hipótesis de particionar por régimen NO está sostenida por la data.")
        print("    → El problema NO es el detector; es la hipótesis de partición.")
        print("    → Recomendación: ABORTAR FIX-17, no implementar partición por régimen.")
    else:
        best_det = max(DETECTORS.keys(),
                       key=lambda d: summary["partitioned"][d]["mae_diff_vs_unique"])
        best = summary["partitioned"][best_det]
        print(f"\n✓ Mejor detector: {best_det} (ΔMAE = {best['mae_diff_vs_unique']*100:+.4f}%)")
        print(f"  Match rate directo: {best['match_rate_pct']}% (sin fallback)")
        print(f"  → Hipótesis de partición SÍ sostenida, proceder con FIX-17 usando {best_det}.")

    # Guardar
    out = {
        "config": {
            "sax_alpha": SAX_ALPHA,
            "sax_window": SAX_WINDOW,
            "pattern_length": PATTERN_LENGTH,
            "train_frac": TRAIN_FRAC,
            "forward_horizon": FORWARD_HORIZON,
            "detectors": list(DETECTORS.keys()),
            "tokens": TOKENS,
        },
        "per_token": all_results,
        "summary": summary,
    }
    out_json = OUT_DIR / "partition_experiment.json"
    with open(out_json, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n✓ JSON: {out_json}")

    # CSV resumen
    rows = [{
        "config": "trie_unique",
        "detector": "",
        "mae_mean": summary["trie_unique"]["mae_mean"],
        "n_matched": summary["trie_unique"]["n_matched_total"],
        "n_total": summary["trie_unique"]["n_total"],
        "match_rate_pct": summary["trie_unique"]["match_rate_pct"],
        "mae_diff_vs_unique": 0.0,
        "improvement_pct": 0.0,
    }]
    for det in DETECTORS:
        s = summary["partitioned"][det]
        rows.append({
            "config": "partitioned",
            "detector": det,
            "mae_mean": s["mae_mean"],
            "n_matched": s["n_matched"],
            "n_total": s["n_total"],
            "match_rate_pct": s["match_rate_pct"],
            "mae_diff_vs_unique": s["mae_diff_vs_unique"],
            "improvement_pct": s["improvement_pct"],
        })
    df_summary = pd.DataFrame(rows)
    out_csv = OUT_DIR / "partition_experiment_summary.csv"
    df_summary.to_csv(out_csv, index=False)
    print(f"✓ CSV: {out_csv}")

    # MD
    md = generate_md_report(out, summary)
    out_md = OUT_DIR / "partition_experiment_report.md"
    with open(out_md, "w") as f:
        f.write(md)
    print(f"✓ MD: {out_md}")

    print("\n" + "=" * 70)
    print("EXPERIMENTO COMPLETO")
    print("=" * 70)


def generate_md_report(full_results: dict, summary: dict) -> str:
    lines = []
    lines.append("# Experimento: ¿particionar por régimen aporta información?\n")
    lines.append(f"**Fecha**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")

    cfg = full_results["config"]
    lines.append("## Setup\n")
    lines.append(f"- SAX: alpha={cfg['sax_alpha']}, window={cfg['sax_window']}, "
                  f"pattern_length={cfg['pattern_length']} (patrones de {cfg['sax_alpha']}^{cfg['pattern_length']} = {cfg['sax_alpha']**cfg['pattern_length']} posibles)")
    lines.append(f"- Walk-forward: {cfg['train_frac']*100:.0f}% train / {(1-cfg['train_frac'])*100:.0f}% test")
    lines.append(f"- Forward horizon: {cfg['forward_horizon']} velas")
    lines.append(f"- Tokens: {len(cfg['tokens'])} ({', '.join(cfg['tokens'])})")
    lines.append(f"- Detectores: {', '.join(cfg['detectors'])}\n")

    lines.append("## Metodología\n")
    lines.append("Para cada vela `i` en test:")
    lines.append("1. Codificar su patrón SAX (5 símbolos, alfabeto 4)")
    lines.append("2. Buscar matches EXACTOS del mismo patrón en train")
    lines.append("3. **Trie único**: usar TODOS los matches → predecir retorno forward medio")
    lines.append("4. **Trie particionado**: usar solo matches donde régimen(detector) coincide con régimen(i)")
    lines.append("5. Métrica: MAE entre predicción y retorno forward realizado\n")
    lines.append("**Score**: `MAE(trie_único) − MAE(trie_particionado)`. Si >0, particionar aporta información.\n")

    lines.append("## Resultados agregados\n")
    lines.append("| Config | Detector | MAE medio | Match directo | ΔMAE vs único | Mejora relativa |")
    lines.append("|---|---|---:|---:|---:|---:|")
    lines.append(f"| trie_unique | — | "
                  f"{summary['trie_unique']['mae_mean']*100:.4f}% | "
                  f"{summary['trie_unique']['match_rate_pct']}% | "
                  f"— | — |")
    for det in cfg["detectors"]:
        s = summary["partitioned"][det]
        emoji = "✓" if s["mae_diff_vs_unique"] > 0 else "✗"
        lines.append(f"| partitioned | {det} | "
                      f"{s['mae_mean']*100:.4f}% | "
                      f"{s['match_rate_pct']}% | "
                      f"{s['mae_diff_vs_unique']*100:+.4f}% | "
                      f"{s['improvement_pct']:+.2f}% {emoji} |")
    lines.append("")

    # Per-token
    lines.append("## Resultados por token\n")
    lines.append("| Token | Trie único MAE | Bollinger MAE | ADX+EMA+BB MAE | EMA slope MAE |")
    lines.append("|---|---:|---:|---:|---:|")
    for sym in cfg["tokens"]:
        r = full_results["per_token"][sym]
        unique_mae = r["trie_unique"]["mae"] * 100
        boll = r["partitioned"].get("bollinger", {}).get("mae", 0) * 100
        aeb = r["partitioned"].get("adx_ema_bb", {}).get("mae", 0) * 100
        emas = r["partitioned"].get("ema_slope", {}).get("mae", 0) * 100
        lines.append(f"| {sym} | {unique_mae:.4f}% | {boll:.4f}% | {aeb:.4f}% | {emas:.4f}% |")
    lines.append("")

    lines.append("## Veredicto\n")
    any_improves = any(
        summary["partitioned"][det]["mae_diff_vs_unique"] > 0
        for det in cfg["detectors"]
    )
    if not any_improves:
        lines.append("❌ **Ningún detector mejora el trie único.**\n")
        lines.append("**Conclusión**: La hipótesis de particionar por régimen NO está sostenida por la data.")
        lines.append("El problema NO es el detector (Bollinger / ADX+EMA+BB / EMA slope). El problema es")
        lines.append("la **hipótesis subyacente** de que los patrones SAX tienen distribuciones de retorno")
        lines.append("distintas entre regímenes. En 1m crypto, esto es FALSO.\n")
        lines.append("### Recomendación")
        lines.append("- **ABORTAR FIX-17** (no implementar nuevo RegimeDetector).")
        lines.append("- **ABORTAR FIX-14** (N4 RegimePartitionedTrie no aporta valor).")
        lines.append("- **Reorientar esfuerzo** a FIX-15 (thresholds por dirección) y otras mejoras del motor.")
        lines.append("- Considerar que el motor SAX/Trie actual es ya razonablemente óptimo en 1m crypto.\n")
    else:
        best_det = max(cfg["detectors"],
                       key=lambda d: summary["partitioned"][d]["mae_diff_vs_unique"])
        best = summary["partitioned"][best_det]
        lines.append(f"✓ **{best_det} mejora el trie único.**\n")
        lines.append(f"- ΔMAE = {best['mae_diff_vs_unique']*100:+.4f}%")
        lines.append(f"- Mejora relativa = {best['improvement_pct']:+.2f}%")
        lines.append(f"- Match directo = {best['match_rate_pct']}%")
        lines.append("\n**Conclusión**: La hipótesis de partición SÍ está sostenida para este detector.")
        lines.append(f"Proceder con FIX-17 usando **{best_det}** como nuevo RegimeDetector.\n")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
