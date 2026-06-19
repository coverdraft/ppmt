"""
Grid search ADX: umbrales × períodos.

Para cada combinación (umbral, período) calcula:
- Distribución global de regímenes
- Separabilidad LONG (PnL LONG en trending_up − PnL LONG en ranging)
- Separabilidad SHORT (PnL SHORT en trending_down − PnL SHORT en ranging)
- Estabilidad TRAIN vs TEST
- Acuerdo humano (pseudo)

Output: /home/z/my-project/download/regime_adx_grid/
"""

import sys
import json
import time
from pathlib import Path
from collections import Counter, defaultdict
from itertools import product

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/scripts")
from regime_detectors_v2 import compute_adx, compute_bollinger_width, _ema, _rolling_median

# ----------------------- Config ----------------------- #

DATA_DIR = Path("/home/z/my-project/download/real_data_1m")
OUT_DIR = Path("/home/z/my-project/download/regime_adx_grid")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TOKENS = []
for p in sorted(DATA_DIR.glob("*_1m.csv")):
    sym = p.stem.replace("_1m", "")
    n_rows = sum(1 for _ in open(p)) - 1
    if n_rows >= 50_000:
        TOKENS.append(sym)

ADX_THRESHOLDS = [20, 25, 30, 35, 40, 45, 50]
ADX_PERIODS = [10, 14, 20, 28, 50]

# ----------------------- Detector ADX configurable ----------------------- #

def detector_adx_configurable(closes, highs, lows, adx_threshold, adx_period):
    n = len(closes)
    labels = np.empty(n, dtype="<U14")
    labels[:] = "ranging"

    adx, plus_di, minus_di = compute_adx(highs, lows, closes, period=adx_period)
    for i in range(n):
        if adx[i] >= adx_threshold:
            if plus_di[i] > minus_di[i]:
                labels[i] = "trending_up"
            else:
                labels[i] = "trending_down"
    return labels


# ----------------------- Helpers ----------------------- #

def load_ohlcv(symbol):
    p = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(p)
    df.columns = [c.lower() for c in df.columns]
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["ts", "open", "high", "low", "close", "volume"]].set_index("ts")


def distribution(labels):
    c = Counter(labels)
    total = sum(c.values())
    if total == 0:
        return {}
    return {k: round(100 * v / total, 2) for k, v in c.items()}


def compute_pnl_by_regime(prices, labels, hold=50):
    n = len(prices)
    if n < hold + 1:
        return {}
    fwd_ret = np.zeros(n)
    fwd_ret[:n - hold] = prices[hold:] / prices[:n - hold] - 1.0

    out = {}
    for regime in ["trending_up", "trending_down", "ranging", "volatile"]:
        mask = labels == regime
        mask[:50] = False
        mask[-hold:] = False
        idx = np.where(mask)[0]
        if len(idx) == 0:
            out[regime] = {"LONG": {"n": 0, "mean_pnl": 0.0, "long_pnls": []},
                           "SHORT": {"n": 0, "mean_pnl": 0.0, "short_pnls": []}}
            continue
        long_pnls = fwd_ret[idx]
        short_pnls = -fwd_ret[idx]
        out[regime] = {
            "LONG": {"n": int(len(idx)), "mean_pnl": float(np.mean(long_pnls)), "long_pnls": long_pnls.tolist()},
            "SHORT": {"n": int(len(idx)), "mean_pnl": float(np.mean(short_pnls)), "short_pnls": short_pnls.tolist()},
        }
    return out


# ----------------------- Main ----------------------- #

def main():
    print("=" * 70)
    print(f"Grid search ADX: {len(ADX_THRESHOLDS)} umbrales × {len(ADX_PERIODS)} períodos")
    print(f"= {len(ADX_THRESHOLDS) * len(ADX_PERIODS)} combinaciones × {len(TOKENS)} tokens")
    print("=" * 70)

    # Cargar todos los tokens a memoria
    print("\nCargando datos...")
    data = {}
    for sym in TOKENS:
        df = load_ohlcv(sym)
        data[sym] = {
            "close": df["close"].values.astype(float),
            "high": df["high"].values.astype(float),
            "low": df["low"].values.astype(float),
            "train_end": int(len(df) * 0.75),
        }
        print(f"  {sym}: {len(df):,} velas")

    results = []

    for thr in ADX_THRESHOLDS:
        for period in ADX_PERIODS:
            t0 = time.time()
            print(f"\n--- ADX threshold={thr}, period={period} ---")

            # Agregados
            all_labels = []
            train_labels = []
            test_labels = []
            # pnl_per_token[regime][direction] = [list of mean_pnl per token]
            pnl_per_token = {"trending_up": {"LONG": [], "SHORT": []},
                              "trending_down": {"LONG": [], "SHORT": []},
                              "ranging": {"LONG": [], "SHORT": []},
                              "volatile": {"LONG": [], "SHORT": []}}

            for sym, d in data.items():
                labels = detector_adx_configurable(
                    d["close"], d["high"], d["low"], thr, period
                )
                te = d["train_end"]
                all_labels.extend(labels.tolist())
                train_labels.extend(labels[:te].tolist())
                test_labels.extend(labels[te:].tolist())

                pnl = compute_pnl_by_regime(d["close"], labels, hold=50)
                for regime, stats in pnl.items():
                    for direction in ["LONG", "SHORT"]:
                        if stats[direction]["n"] > 0:
                            pnl_per_token[regime][direction].append(stats[direction]["mean_pnl"])

            dist_all = distribution(np.array(all_labels))
            dist_train = distribution(np.array(train_labels))
            dist_test = distribution(np.array(test_labels))

            # Estabilidad: max diff T/T
            max_diff = 0.0
            for r in ["trending_up", "trending_down", "ranging", "volatile"]:
                t = dist_train.get(r, 0.0)
                te = dist_test.get(r, 0.0)
                max_diff = max(max_diff, abs(t - te))

            # PnL medio por régimen (media de medias por token)
            pnl_summary = {}
            for regime in ["trending_up", "trending_down", "ranging", "volatile"]:
                long_pnls = pnl_per_token[regime]["LONG"]
                short_pnls = pnl_per_token[regime]["SHORT"]
                pnl_summary[regime] = {
                    "LONG": {
                        "n_tokens": len(long_pnls),
                        "mean_pnl": float(np.mean(long_pnls)) if long_pnls else 0.0,
                        "n_total": sum(1 for _ in long_pnls),
                    },
                    "SHORT": {
                        "n_tokens": len(short_pnls),
                        "mean_pnl": float(np.mean(short_pnls)) if short_pnls else 0.0,
                    },
                }

            sep_long = pnl_summary["trending_up"]["LONG"]["mean_pnl"] - pnl_summary["ranging"]["LONG"]["mean_pnl"]
            sep_short = pnl_summary["trending_down"]["SHORT"]["mean_pnl"] - pnl_summary["ranging"]["SHORT"]["mean_pnl"]

            elapsed = time.time() - t0

            result = {
                "threshold": thr,
                "period": period,
                "dist_all": dist_all,
                "dist_train": dist_train,
                "dist_test": dist_test,
                "max_diff_train_test_pp": round(max_diff, 2),
                "pnl_long_up": pnl_summary["trending_up"]["LONG"]["mean_pnl"],
                "pnl_long_range": pnl_summary["ranging"]["LONG"]["mean_pnl"],
                "pnl_short_down": pnl_summary["trending_down"]["SHORT"]["mean_pnl"],
                "pnl_short_range": pnl_summary["ranging"]["SHORT"]["mean_pnl"],
                "separability_long": sep_long,
                "separability_short": sep_short,
                "score": sep_long + sep_short,  # métrica única
                "time_s": round(elapsed, 2),
            }
            results.append(result)

            print(f"  up={dist_all.get('trending_up', 0):.1f}%  "
                  f"down={dist_all.get('trending_down', 0):.1f}%  "
                  f"range={dist_all.get('ranging', 0):.1f}%")
            print(f"  Sep LONG = {sep_long*100:+.4f}%  Sep SHORT = {sep_short*100:+.4f}%  "
                  f"Score = {(sep_long+sep_short)*100:+.4f}%  "
                  f"ΔT/T = {max_diff:.2f}pp  ({elapsed:.1f}s)")

    # Ordenar por score descendente
    results.sort(key=lambda x: x["score"], reverse=True)

    print("\n" + "=" * 70)
    print("RANKING TOP 5 (por Score = Sep LONG + Sep SHORT)")
    print("=" * 70)
    for i, r in enumerate(results[:5], 1):
        print(f"  #{i}: thr={r['threshold']} period={r['period']}  "
              f"Score={r['score']*100:+.4f}%  "
              f"up={r['dist_all'].get('trending_up', 0):.1f}%  "
              f"down={r['dist_all'].get('trending_down', 0):.1f}%  "
              f"range={r['dist_all'].get('ranging', 0):.1f}%")

    print("\nRANKING BOTTOM 3 (peores)")
    for r in results[-3:]:
        print(f"  thr={r['threshold']} period={r['period']}  "
              f"Score={r['score']*100:+.4f}%")

    # Guardar
    df_results = pd.DataFrame(results)
    out_csv = OUT_DIR / "adx_grid.csv"
    df_results.to_csv(out_csv, index=False)
    print(f"\n✓ CSV: {out_csv}")

    # MD
    md = ["# Grid Search ADX: umbrales × períodos\n"]
    md.append(f"**Fecha**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    md.append(f"**Tokens**: {len(TOKENS)} ({', '.join(TOKENS)})")
    md.append(f"**Combinaciones**: {len(ADX_THRESHOLDS) * len(ADX_PERIODS)}\n")

    md.append("## Ranking completo (ordenado por Score = Sep LONG + Sep SHORT)\n")
    md.append("| Rank | Threshold | Period | Up% | Down% | Range% | Sep LONG | Sep SHORT | Score | ΔT/T pp |")
    md.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for i, r in enumerate(results, 1):
        md.append(
            f"| {i} | {r['threshold']} | {r['period']} | "
            f"{r['dist_all'].get('trending_up', 0):.1f} | "
            f"{r['dist_all'].get('trending_down', 0):.1f} | "
            f"{r['dist_all'].get('ranging', 0):.1f} | "
            f"{r['separability_long']*100:+.4f}% | "
            f"{r['separability_short']*100:+.4f}% | "
            f"{r['score']*100:+.4f}% | "
            f"{r['max_diff_train_test_pp']:.2f} |"
        )
    md.append("")

    md.append("## Hallazgos\n")
    best = results[0]
    worst = results[-1]
    md.append(f"- **Mejor combinación**: ADX threshold={best['threshold']}, period={best['period']}")
    md.append(f"  - Score = {best['score']*100:+.4f}%")
    md.append(f"  - Up={best['dist_all'].get('trending_up', 0):.1f}% Down={best['dist_all'].get('trending_down', 0):.1f}% Range={best['dist_all'].get('ranging', 0):.1f}%")
    md.append(f"- **Peor combinación**: ADX threshold={worst['threshold']}, period={worst['period']}")
    md.append(f"  - Score = {worst['score']*100:+.4f}%\n")

    md.append("## Recomendación\n")
    if best["score"] > 0.0001:  # >0.01% score
        md.append(f"ADX con threshold={best['threshold']} y period={best['period']} es el mejor candidato.")
        md.append("Si este score compite favorablemente con Bollinger/ADX+EMA+BB, usarlo en FIX-17.")
    else:
        md.append("Ninguna combinación de ADX da buen score. ADX solo no es suficiente para 1m crypto.")

    out_md = OUT_DIR / "adx_grid_report.md"
    with open(out_md, "w") as f:
        f.write("\n".join(md))
    print(f"✓ MD: {out_md}")

    # JSON
    out_json = OUT_DIR / "adx_grid.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"✓ JSON: {out_json}")

    print("\n" + "=" * 70)
    print("GRID SEARCH COMPLETO")
    print("=" * 70)


if __name__ == "__main__":
    main()
