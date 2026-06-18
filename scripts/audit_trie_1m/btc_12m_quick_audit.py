"""
Auditoría rápida del RegimeDetector sobre BTC con 12 meses de data.
Confirma si el detector sigue degenerado con data más larga.
"""
from __future__ import annotations
import sys
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")
sys.path.insert(0, "/home/z/my-project/scripts")

from regime_detector_audit import (
    compute_ema_slope, compute_adx, compute_realized_vol,
    detect_series_vectorized
)

OUT_DIR = Path("/home/z/my-project/download/regime_audit")
OUT_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 78)
print("Auditoría RegimeDetector — BTC 12 meses")
print("=" * 78)

# Load BTC 12m
csv = Path("/home/z/my-project/download/real_data_1m_12m/BTCUSDT_1m.csv")
df = pd.read_csv(csv)
for c in ["open", "high", "low", "close", "volume"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

print(f"BTC 12m: {len(df):,} velas, {df['dt'].iloc[0]} → {df['dt'].iloc[-1]}")

prices = df["close"].values.astype(float)
high = df["high"].values.astype(float)
low = df["low"].values.astype(float)

# Compute all metrics
print("\nComputing EMA slope...")
ema = compute_ema_slope(prices)
print("Computing ADX...")
adx = compute_adx(high, low, prices)
print("Computing realized vol...")
rvol = compute_realized_vol(prices, window=50)
print("Computing detector (vectorized)...")
regimes = detect_series_vectorized(prices, lookback=50)

# Distributions
n = len(prices)
det_dist = Counter(regimes)
ema_dist = Counter(ema.tolist())
adx_dist = Counter(adx.tolist())

# Combo: bull if both EMA=1 and ADX=1, bear if both -1, else range
combo = np.zeros(n, dtype=int)
for i in range(n):
    if ema[i] == 1 and adx[i] == 1: combo[i] = 1
    elif ema[i] == -1 and adx[i] == -1: combo[i] = -1
combo_dist = Counter(combo.tolist())

print(f"\n=== Distribución global BTC 12m ({n:,} velas) ===")
print(f"Detector actual:")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    print(f"  {r:14s} {det_dist.get(r,0):>8,}  {det_dist.get(r,0)/n*100:6.2f}%")
print(f"\nEMA slope:")
for k, label in [(1, "bull"), (-1, "bear"), (0, "neutral")]:
    print(f"  {label:14s} {ema_dist.get(k,0):>8,}  {ema_dist.get(k,0)/n*100:6.2f}%")
print(f"\nADX:")
for k, label in [(1, "bull"), (-1, "bear"), (0, "ranging")]:
    print(f"  {label:14s} {adx_dist.get(k,0):>8,}  {adx_dist.get(k,0)/n*100:6.2f}%")
print(f"\nEMA+ADX combo:")
for k, label in [(1, "bull"), (-1, "bear"), (0, "range")]:
    print(f"  {label:14s} {combo_dist.get(k,0):>8,}  {combo_dist.get(k,0)/n*100:6.2f}%")

# Per-month distribution to see if there's diversity
df["month"] = df["dt"].dt.to_period("M")
df["regime_det"] = regimes
df["ema_class"] = ema
df["adx_class"] = adx

print(f"\n=== Distribución por mes (detector actual) ===")
print(f"{'Mes':10s} {'Velas':>8s} {'Det up%':>8s} {'Det dn%':>8s} {'Det rng%':>9s} {'EMA bull%':>10s} {'EMA bear%':>10s} {'ADX bull%':>10s} {'ADX bear%':>10s}")
for month, sub in df.groupby("month"):
    n_m = len(sub)
    det_u = (sub["regime_det"] == "trending_up").sum() / n_m * 100
    det_d = (sub["regime_det"] == "trending_down").sum() / n_m * 100
    det_r = (sub["regime_det"] == "ranging").sum() / n_m * 100
    ema_u = (sub["ema_class"] == 1).sum() / n_m * 100
    ema_d = (sub["ema_class"] == -1).sum() / n_m * 100
    adx_u = (sub["adx_class"] == 1).sum() / n_m * 100
    adx_d = (sub["adx_class"] == -1).sum() / n_m * 100
    print(f"{str(month):10s} {n_m:>8,} {det_u:>7.2f}% {det_d:>7.2f}% {det_r:>8.2f}% {ema_u:>9.2f}% {ema_d:>9.2f}% {adx_u:>9.2f}% {adx_d:>9.2f}%")

# Same-period comparison: tomamos las últimas 139k velas de BTC 12m para comparar con v4
# v4 es 2026-01-30 → 2026-06-18 = ~139 días = ~200k velas
print(f"\n=== Comparación: BTC 12m últimas 200k velas vs v4 ===")
sub_df = df.tail(200_000).copy()
sub_n = len(sub_df)
sub_det_dist = Counter(sub_df["regime_det"].tolist())
sub_ema_dist = Counter(sub_df["ema_class"].tolist())
sub_adx_dist = Counter(sub_df["adx_class"].tolist())
print(f"Ventana: {sub_df['dt'].iloc[0]} → {sub_df['dt'].iloc[-1]} ({sub_n:,} velas)")
print(f"  Detector: up={sub_det_dist.get('trending_up',0)/sub_n*100:.2f}%  down={sub_det_dist.get('trending_down',0)/sub_n*100:.2f}%  ranging={sub_det_dist.get('ranging',0)/sub_n*100:.2f}%")
print(f"  EMA:      bull={sub_ema_dist.get(1,0)/sub_n*100:.2f}%  bear={sub_ema_dist.get(-1,0)/sub_n*100:.2f}%  neutral={sub_ema_dist.get(0,0)/sub_n*100:.2f}%")
print(f"  ADX:      bull={sub_adx_dist.get(1,0)/sub_n*100:.2f}%  bear={sub_adx_dist.get(-1,0)/sub_n*100:.2f}%  ranging={sub_adx_dist.get(0,0)/sub_n*100:.2f}%")

# Comparación con la PRIMERA mitad (donde el mercado tuvo más diversidad)
print(f"\n=== Comparación: BTC 12m primeras 200k velas (mercado más alcista) ===")
sub_df2 = df.head(200_000).copy()
sub_n2 = len(sub_df2)
sub_det_dist2 = Counter(sub_df2["regime_det"].tolist())
sub_ema_dist2 = Counter(sub_df2["ema_class"].tolist())
sub_adx_dist2 = Counter(sub_df2["adx_class"].tolist())
print(f"Ventana: {sub_df2['dt'].iloc[0]} → {sub_df2['dt'].iloc[-1]} ({sub_n2:,} velas)")
print(f"  Detector: up={sub_det_dist2.get('trending_up',0)/sub_n2*100:.2f}%  down={sub_det_dist2.get('trending_down',0)/sub_n2*100:.2f}%  ranging={sub_det_dist2.get('ranging',0)/sub_n2*100:.2f}%")
print(f"  EMA:      bull={sub_ema_dist2.get(1,0)/sub_n2*100:.2f}%  bear={sub_ema_dist2.get(-1,0)/sub_n2*100:.2f}%  neutral={sub_ema_dist2.get(0,0)/sub_n2*100:.2f}%")
print(f"  ADX:      bull={sub_adx_dist2.get(1,0)/sub_n2*100:.2f}%  bear={sub_adx_dist2.get(-1,0)/sub_n2*100:.2f}%  ranging={sub_adx_dist2.get(0,0)/sub_n2*100:.2f}%")

# Precio BTC primeros vs últimos 200k
p_first0 = sub_df2["close"].iloc[0]
p_first1 = sub_df2["close"].iloc[-1]
p_last0 = sub_df["close"].iloc[0]
p_last1 = sub_df["close"].iloc[-1]
print(f"\nRetorno primera mitad: {(p_first1/p_first0-1)*100:+.2f}%")
print(f"Retorno segunda mitad: {(p_last1/p_last0-1)*100:+.2f}%")

# Veredicto
det_rng_pct = det_dist.get("ranging", 0) / n
print(f"\n=== VEREDICTO ===")
print(f"Detector ranging % sobre 12 meses: {det_rng_pct*100:.2f}%")
if det_rng_pct > 0.85:
    print("CONFIRMADO: B) Detector degenerado. Sigue clasificando >85% como ranging incluso con 12 meses de data diversificada.")
elif det_rng_pct < 0.50:
    print("SORPRESA: A) Dataset insuficiente. Con 12 meses el detector mejora significativamente.")
else:
    print("MIXTO: C) Ambos. El detector mejora pero sigue siendo mayoritariamente ranging.")

# Save summary
import json
summary = {
    "symbol": "BTCUSDT",
    "period": "12m",
    "n_candles": n,
    "first_dt": str(df["dt"].iloc[0]),
    "last_dt": str(df["dt"].iloc[-1]),
    "first_half_ret_pct": (p_first1/p_first0-1)*100,
    "second_half_ret_pct": (p_last1/p_last0-1)*100,
    "detector_dist_pct": {k: det_dist.get(k,0)/n for k in ["trending_up","trending_down","ranging","volatile"]},
    "ema_dist_pct": {f"{'bull' if k==1 else 'bear' if k==-1 else 'neutral'}": ema_dist.get(k,0)/n for k in [1,-1,0]},
    "adx_dist_pct": {f"{'bull' if k==1 else 'bear' if k==-1 else 'ranging'}": adx_dist.get(k,0)/n for k in [1,-1,0]},
    "combo_dist_pct": {f"{'bull' if k==1 else 'bear' if k==-1 else 'range'}": combo_dist.get(k,0)/n for k in [1,-1,0]},
    "verdict": "B" if det_rng_pct > 0.85 else ("A" if det_rng_pct < 0.50 else "C"),
}
with open(OUT_DIR / "btc_12m_quick_audit.json", "w") as f:
    json.dump(summary, f, indent=2, default=str)
print(f"\nGuardado: {OUT_DIR / 'btc_12m_quick_audit.json'}")
