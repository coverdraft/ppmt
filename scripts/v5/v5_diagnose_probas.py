#!/usr/bin/env python3
"""
Diagnóstico extendido: predicciones en los últimos 300 candles (25h) por token.

Para cada token, muestra:
- Distribución de probas (min, p25, p50, p75, max)
- Cuántas señales superarían thresholds 0.50, 0.60, 0.70, 0.80
- Si hay algún momento con proba alta en las últimas 25h

Esto nos dice si las probas bajas son por el régimen actual (mercado bajista)
o si hay un bug sistemático en el feature pipeline.
"""
import sys
import json
import time
import requests
import numpy as np
import pandas as pd
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "v5"))

import lightgbm as lgb
from v5_paper_trader_cb_v2 import compute_features, FEATURE_NAMES, TOKENS, CoinbaseFeed

MODEL_PATH = _REPO_ROOT / "models" / "v5_cb_v2" / "v5_lgbm_model_cb_v2.txt"

def main():
    print("=" * 75)
    print("DIAGNÓSTICO EXTENDIDO — Distribución de probas en 300 candles (25h)")
    print("=" * 75)

    model = lgb.Booster(model_file=str(MODEL_PATH))
    session = requests.Session()

    all_probas = []
    print(f"\n{'Symbol':<12} {'Class':<12} {'Min':>7} {'p25':>7} {'p50':>7} {'p75':>7} {'Max':>7} {'≥0.5':>5} {'≥0.6':>5} {'≥0.7':>5} {'≥0.8':>5}")
    print("-" * 95)

    for sym, pair, asset_class in TOKENS:
        feed = CoinbaseFeed(pair, session)
        candles = feed.fetch_recent(300)
        if not candles:
            print(f"{sym:<12} {asset_class:<12} ERROR")
            continue

        df = pd.DataFrame(candles)
        df_feat = compute_features(df)

        # Compute proba for every row (skip first 55 which have NaN in rsi/ema)
        probas = []
        for i in range(55, len(df_feat)):
            row = df_feat.iloc[i]
            if pd.isna(row["rsi_14"]):
                continue
            feat = {f: float(row[f]) if not pd.isna(row[f]) else 0.0
                    for f in FEATURE_NAMES if f not in ("edge_strong", "edge_marginal")}
            h = int(row["hour_utc"])
            a = h in {0, 1, 2, 18, 19, 20, 21, 22, 23}
            al = asset_class != "blue_chip"
            sc = True
            es = 1 if (al and sc and a) else 0
            scv = int(al) + int(sc) + int(a)
            em = 1 if (scv == 2 and not es) else 0
            feat["edge_strong"] = float(es)
            feat["edge_marginal"] = float(em)
            X = np.nan_to_num(np.array([[feat[f] for f in FEATURE_NAMES]], dtype=np.float64), nan=0.0)
            p = model.predict(X)[0]
            probas.append(p)

        probas = np.array(probas)
        all_probas.extend(probas)

        n_50 = sum(1 for p in probas if p >= 0.50)
        n_60 = sum(1 for p in probas if p >= 0.60)
        n_70 = sum(1 for p in probas if p >= 0.70)
        n_80 = sum(1 for p in probas if p >= 0.80)

        print(f"{sym:<12} {asset_class:<12} {probas.min():>7.3f} {np.percentile(probas,25):>7.3f} "
              f"{np.percentile(probas,50):>7.3f} {np.percentile(probas,75):>7.3f} {probas.max():>7.3f} "
              f"{n_50:>5} {n_60:>5} {n_70:>5} {n_80:>5}")

    # Aggregate stats
    all_probas = np.array(all_probas)
    print()
    print("=" * 75)
    print(f"AGREGADO — {len(all_probas)} predicciones en total (12 tokens × ~245 candles c/u)")
    print("=" * 75)
    print(f"  Min:    {all_probas.min():.4f}")
    print(f"  p25:    {np.percentile(all_probas, 25):.4f}")
    print(f"  p50:    {np.percentile(all_probas, 50):.4f}")
    print(f"  p75:    {np.percentile(all_probas, 75):.4f}")
    print(f"  p90:    {np.percentile(all_probas, 90):.4f}")
    print(f"  Max:    {all_probas.max():.4f}")
    print()
    print(f"  Señales ≥ 0.50: {sum(1 for p in all_probas if p >= 0.50):>5} / {len(all_probas)} ({sum(1 for p in all_probas if p >= 0.50)/len(all_probas)*100:.1f}%)")
    print(f"  Señales ≥ 0.60: {sum(1 for p in all_probas if p >= 0.60):>5} / {len(all_probas)} ({sum(1 for p in all_probas if p >= 0.60)/len(all_probas)*100:.1f}%)")
    print(f"  Señales ≥ 0.70: {sum(1 for p in all_probas if p >= 0.70):>5} / {len(all_probas)} ({sum(1 for p in all_probas if p >= 0.70)/len(all_probas)*100:.1f}%)")
    print(f"  Señales ≥ 0.80: {sum(1 for p in all_probas if p >= 0.80):>5} / {len(all_probas)} ({sum(1 for p in all_probas if p >= 0.80)/len(all_probas)*100:.1f}%)")
    print()
    print("Referencia del backtest (Paso 5, RECENT_2026 OOS):")
    print("  ~25-30% de señales superaban 0.80")
    print("  p50 ≈ 0.55-0.65")
    print()
    print("INTERPRETACIÓN:")
    print("  - Si p50 < 0.30 y < 5% superan 0.50 → el mercado está en régimen bajista")
    print("    y el modelo está haciendo lo correcto (prediciendo baja proba de LONG TP)")
    print("  - Si p50 > 0.40 y > 15% superan 0.50 → el régimen es normal pero thr=0.80")
    print("    es demasiado alto para este mercado; considerar bajar a 0.60-0.70")
    print("  - Si p50 < 0.20 y NINGUNA supera 0.40 → posible bug en feature pipeline")
    print("    (comparar features con backtest para detectar divergencia)")

if __name__ == "__main__":
    main()
