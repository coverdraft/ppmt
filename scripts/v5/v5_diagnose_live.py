#!/usr/bin/env python3
"""
Diagnóstico del paper trader: compara features calculadas en vivo vs esperadas.

Toma las últimas 60 candles de BTC-USD de Coinbase, calcula features con
compute_features() (la misma función del paper trader), y muestra:
1. Los valores de las 40 features
2. La predicción del modelo
3. Compara con lo que esperamos del backtest (p50 ~ 0.55-0.65)
"""
import sys
import json
import time
import requests
import numpy as np
import pandas as pd
from pathlib import Path

# Setup paths
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "v5"))

import lightgbm as lgb
from v5_paper_trader_cb_v2 import compute_features, FEATURE_NAMES, TOKENS, CoinbaseFeed

MODEL_PATH = _REPO_ROOT / "models" / "v5_cb_v2" / "v5_lgbm_model_cb_v2.txt"

def main():
    print("=" * 70)
    print("DIAGNÓSTICO DEL PAPER TRADER")
    print("=" * 70)

    # Load model
    print(f"\n1. Cargando modelo desde: {MODEL_PATH}")
    model = lgb.Booster(model_file=str(MODEL_PATH))
    print(f"   Modelo cargado: {model.num_trees()} trees, {model.num_feature()} features")

    # Fetch candles for BTC-USD
    session = requests.Session()
    print(f"\n2. Fetching 60 candles de BTC-USD desde Coinbase...")
    feed = CoinbaseFeed("BTC-USD", session)
    candles = feed.fetch_recent(60)
    print(f"   Got {len(candles)} candles")
    if not candles:
        print("   ERROR: no candles")
        return
    print(f"   Primera: ts={candles[0]['timestamp']} close={candles[0]['close']:.2f}")
    print(f"   Última:  ts={candles[-1]['timestamp']} close={candles[-1]['close']:.2f}")
    print(f"   Última candle close time (UTC): {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(candles[-1]['timestamp'] + 300))}")

    # Build DataFrame
    df = pd.DataFrame(candles)
    print(f"\n3. DataFrame columns: {list(df.columns)}")
    print(f"   DataFrame shape: {df.shape}")
    print(f"   Últimas 3 rows (close):")
    print(df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(3).to_string())

    # Compute features
    print(f"\n4. Calculando features con compute_features()...")
    df_feat = compute_features(df)
    last_row = df_feat.iloc[-1]
    print(f"   Features calculadas. df_feat shape: {df_feat.shape}")

    # Build feature dict (same logic as paper trader)
    features = {f: float(last_row[f]) if not pd.isna(last_row[f]) else 0.0
                for f in FEATURE_NAMES if f not in ("edge_strong", "edge_marginal")}

    # Compute edge features
    hour_utc = int(last_row["hour_utc"])
    asia = hour_utc in {0, 1, 2, 18, 19, 20, 21, 22, 23}
    alt = "blue_chip" != "blue_chip"  # BTC is blue_chip, so alt = False
    scalp = "5m" in {"1m", "5m", "15m"}  # True
    edge_strong = 1 if (alt and scalp and asia) else 0
    score = int(alt) + int(scalp) + int(asia)
    edge_marginal = 1 if (score == 2 and not edge_strong) else 0
    features["edge_strong"] = float(edge_strong)
    features["edge_marginal"] = float(edge_marginal)

    # Show all features
    print(f"\n5. Features para el último candle (hour_utc={hour_utc}, asia={asia}, alt={alt}):")
    print(f"   {'Feature':<25} {'Value':>15}")
    print(f"   {'-'*25} {'-'*15}")
    for f in FEATURE_NAMES:
        val = features[f]
        print(f"   {f:<25} {val:>15.6f}")

    # Predict
    print(f"\n6. Predicción del modelo:")
    X = np.array([[features[f] for f in FEATURE_NAMES]], dtype=np.float64)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    proba = model.predict(X)[0]
    print(f"   proba = {proba:.6f}")

    # Also predict on the last 5 rows to see distribution
    print(f"\n7. Predicciones en las últimas 5 candles:")
    for i in range(-5, 0):
        row = df_feat.iloc[i]
        feat = {f: float(row[f]) if not pd.isna(row[f]) else 0.0
                for f in FEATURE_NAMES if f not in ("edge_strong", "edge_marginal")}
        h = int(row["hour_utc"])
        a = h in {0, 1, 2, 18, 19, 20, 21, 22, 23}
        al = False  # BTC
        sc = True
        es = 1 if (al and sc and a) else 0
        sc_val = int(al) + int(sc) + int(a)
        em = 1 if (sc_val == 2 and not es) else 0
        feat["edge_strong"] = float(es)
        feat["edge_marginal"] = float(em)
        Xi = np.array([[feat[f] for f in FEATURE_NAMES]], dtype=np.float64)
        Xi = np.nan_to_num(Xi, nan=0.0, posinf=0.0, neginf=0.0)
        p = model.predict(Xi)[0]
        ts_str = time.strftime('%H:%M:%S', time.gmtime(row['timestamp']))
        print(f"   candle ts={ts_str} close={row['close']:.2f} hour={h} proba={p:.4f}")

    # Compare: what does the backtest say proba should look like?
    print(f"\n8. Para referencia, en el backtest del Paso 5 (RECENT_2026, thr=0.80):")
    print(f"   - ~25-30% de señales superaban 0.80")
    print(f"   - Mediana de proba ≈ 0.55-0.65")
    print(f"   - Si tu proba está en 0.10-0.20, hay un bug en el feature pipeline")

    # Check for NaN/zero issues
    print(f"\n9. Sanity check — features con valor 0.0 o NaN:")
    for f in FEATURE_NAMES:
        v = features[f]
        if v == 0.0:
            print(f"   ⚠️  {f} = 0.0 (podría ser legit o bug)")
        elif np.isnan(v):
            print(f"   ❌ {f} = NaN (bug!)")

    print(f"\n{'='*70}")
    print(f"Si la proba es < 0.30, el bug está en las features (no en el modelo).")
    print(f"Compará los valores de features arriba con los del backtest.")
    print(f"{'='*70}")

if __name__ == "__main__":
    main()
