#!/usr/bin/env python3
"""
Diagnóstico CRÍTICO: compara features históricas de la DB vs features calculadas
en runtime con compute_features() del paper trader.

Si las features coinciden → el bug NO está en el cálculo.
Si difieren → encontramos el bug.
"""
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "v5"))

from v5_paper_trader_cb_v2 import compute_features, FEATURE_NAMES

DB_PATH = "/home/z/my-project/data/ppmt.db"

def main():
    print("=" * 75)
    print("COMPARACIÓN: features DB (pre-calculadas) vs compute_features() runtime")
    print("=" * 75)

    conn = sqlite3.connect(DB_PATH, timeout=30)

    # Get one row from the DB for BTCUSDT 5m
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, timeframe, ts, features_json, asset_class
        FROM feature_observations_cb
        WHERE symbol='BTCUSDT' AND timeframe='5m'
        ORDER BY ts DESC LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        print("No data found")
        return

    symbol, timeframe, ts, features_json, asset_class = row
    db_features = json.loads(features_json)
    print(f"\nSymbol: {symbol}, TF: {timeframe}, ts: {ts}")
    print(f"  Date: {pd.to_datetime(ts, unit='s', utc=True)}")
    print(f"  Asset class: {asset_class}")

    # Load the OHLCV for this candle + 59 previous (60 total, same as paper trader)
    # The feature_observation has historical_regime which tells us the window
    cur.execute("""
        SELECT historical_regime FROM feature_observations_cb
        WHERE symbol='BTCUSDT' AND timeframe='5m' AND ts=?
    """, (ts,))
    regime_row = cur.fetchone()
    window = regime_row[0] if regime_row else "RECENT_2026"
    print(f"  Historical regime (window): {window}")

    cur.execute(f"""
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_ext_cb
        WHERE symbol='BTCUSDT' AND timeframe='5m' AND window='{window}'
          AND timestamp <= ?
        ORDER BY timestamp DESC LIMIT 60
    """, (ts,))
    rows = cur.fetchall()
    rows = list(reversed(rows))  # ASC order
    print(f"  Loaded {len(rows)} OHLCV candles from DB (window={window})")
    print(f"  First: ts={rows[0][0]} close={rows[0][4]}")
    print(f"  Last:  ts={rows[-1][0]} close={rows[-1][4]}")
    print(f"  Target ts: {ts} (should match last row ts: {rows[-1][0]})")

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])

    # Compute features with paper trader's function
    df_feat = compute_features(df)
    last_row = df_feat.iloc[-1]

    # Build feature dict same as paper trader
    runtime_features = {f: float(last_row[f]) if not pd.isna(last_row[f]) else 0.0
                        for f in FEATURE_NAMES if f not in ("edge_strong", "edge_marginal")}

    # Compare feature by feature
    print(f"\n{'Feature':<25} {'DB value':>18} {'Runtime value':>18} {'Diff':>15} {'Match?':>8}")
    print("-" * 90)

    mismatches = []
    for f in FEATURE_NAMES:
        if f in ("edge_strong", "edge_marginal"):
            # These are computed separately, not in DB features_json
            continue
        db_val = db_features.get(f, None)
        rt_val = runtime_features.get(f, None)
        if db_val is None:
            print(f"{f:<25} {'MISSING':>18} {rt_val:>18.6f} {'N/A':>15} {'?':>8}")
            continue
        diff = abs(db_val - rt_val)
        match = "✓" if diff < 1e-6 else "✗ MISMATCH"
        if diff >= 1e-6:
            mismatches.append((f, db_val, rt_val, diff))
        print(f"{f:<25} {db_val:>18.6f} {rt_val:>18.6f} {diff:>15.6f} {match:>8}")

    print()
    if not mismatches:
        print("✅ TODAS las features coinciden — el cálculo es correcto.")
        print("   El bug NO está en compute_features().")
    else:
        print(f"❌ {len(mismatches)} features NO coinciden:")
        for f, db_v, rt_v, d in mismatches:
            print(f"   {f}: DB={db_v:.6f} vs RT={rt_v:.6f} (diff={d:.6f})")

    # Now predict with both feature sets
    import lightgbm as lgb
    model = lgb.Booster(model_file=str(_REPO_ROOT / "models" / "v5_cb_v2" / "v5_lgbm_model_cb_v2.txt"))

    # DB features prediction
    db_feat_arr = np.array([[db_features.get(f, 0.0) for f in FEATURE_NAMES]], dtype=np.float64)
    db_feat_arr = np.nan_to_num(db_feat_arr, nan=0.0)
    # Need to add edge_strong/edge_marginal to db_features
    hour = pd.to_datetime(ts, unit='s', utc=True).hour
    asia = hour in {0, 1, 2, 18, 19, 20, 21, 22, 23}
    alt = asset_class != "blue_chip"
    scalp = True
    es = 1 if (alt and scalp and asia) else 0
    scv = int(alt) + int(scalp) + int(asia)
    em = 1 if (scv == 2 and not es) else 0
    db_features["edge_strong"] = float(es)
    db_features["edge_marginal"] = float(em)
    db_feat_arr = np.array([[db_features.get(f, 0.0) for f in FEATURE_NAMES]], dtype=np.float64)
    db_feat_arr = np.nan_to_num(db_feat_arr, nan=0.0)
    p_db = model.predict(db_feat_arr)[0]

    # Runtime features prediction
    runtime_features["edge_strong"] = float(es)
    runtime_features["edge_marginal"] = float(em)
    rt_feat_arr = np.array([[runtime_features[f] for f in FEATURE_NAMES]], dtype=np.float64)
    rt_feat_arr = np.nan_to_num(rt_feat_arr, nan=0.0)
    p_rt = model.predict(rt_feat_arr)[0]

    print(f"\nPredicciones del modelo:")
    print(f"  Con features de DB:      proba = {p_db:.6f}")
    print(f"  Con features runtime:    proba = {p_rt:.6f}")
    print(f"  Diferencia:              {abs(p_db - p_rt):.6f}")

    # Check: what's the distribution of probas in the DB for BTCUSDT 5m?
    print(f"\nDistribución de probas en la DB (BTCUSDT 5m, últimas 1000 rows):")
    cur.execute("""
        SELECT features_json FROM feature_observations_cb
        WHERE symbol='BTCUSDT' AND timeframe='5m'
        ORDER BY ts DESC LIMIT 1000
    """)
    db_probas = []
    for (fj,) in cur.fetchall():
        feats = json.loads(fj)
        feats["edge_strong"] = 0.0  # placeholder, recompute below
        feats["edge_marginal"] = 0.0
        # We don't have hour_utc in features_json, skip edge for now
        arr = np.array([[feats.get(f, 0.0) for f in FEATURE_NAMES]], dtype=np.float64)
        arr = np.nan_to_num(arr, nan=0.0)
        p = model.predict(arr)[0]
        db_probas.append(p)

    db_probas = np.array(db_probas)
    print(f"  Min:    {db_probas.min():.4f}")
    print(f"  p25:    {np.percentile(db_probas, 25):.4f}")
    print(f"  p50:    {np.percentile(db_probas, 50):.4f}")
    print(f"  p75:    {np.percentile(db_probas, 75):.4f}")
    print(f"  Max:    {db_probas.max():.4f}")
    print(f"  ≥ 0.80: {sum(1 for p in db_probas if p >= 0.80)} / {len(db_probas)}")
    print()
    print("  NOTA: estas predicciones usan features de DB con edge_strong=0, edge_marginal=0")
    print("  (placeholder). Si las probas aquí son altas pero en vivo son bajas,")
    print("  el bug podría estar en cómo se calculan edge_strong/edge_marginal.")

    conn.close()

if __name__ == "__main__":
    main()
