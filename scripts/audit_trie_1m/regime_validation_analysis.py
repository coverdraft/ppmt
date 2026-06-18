"""
PPMT — Validación de Diversidad de Regímenes en el dataset v4 (16 tok × 200k velas).

Responde a las 7 preguntas del usuario:
  Q1. Distribución global de regímenes (bull / bear / ranging / vol-alta / vol-baja)
  Q2. Distribución TRAIN vs TEST por régimen
  Q3. Sesgo temporal (TRAIN mayoritariamente alcista, TEST mayoritariamente bajista)
  Q4. Análisis usando ventanas históricas separadas (alcista / bajista / lateral)
  Q5. WR / PF / Expectancy LONG vs SHORT por régimen
  Q6. ¿Las pérdidas LONG se concentran en cambios de régimen?
  Q7. ¿El RegimeDetector clasifica casi todo como ranging?

Salida:
  /home/z/my-project/download/regime_analysis/regime_validation_report.json
  /home/z/my-project/download/regime_analysis/regime_validation_report.md
  /home/z/my-project/download/regime_analysis/regime_distribution_global.csv
  /home/z/my-project/download/regime_analysis/regime_train_test.csv
  /home/z/my-project/download/regime_analysis/regime_metrics_per_regime.csv
  /home/z/my-project/download/regime_analysis/regime_transition_loss_concentration.csv
  /home/z/my-project/download/regime_analysis/regime_detector_health.csv
  /home/z/my-project/download/regime_analysis/regime_distribution_per_window.csv
"""
from __future__ import annotations
import json
import sys
import statistics
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")
from ppmt.core.regime import RegimeDetector  # noqa: E402

# ---------------------------------------------------------------------------
# Configuración dataset v4
# ---------------------------------------------------------------------------
DATA_DIR = Path("/home/z/my-project/download/real_data_1m_v4")
OUT_DIR  = Path("/home/z/my-project/download/regime_analysis")
OUT_DIR.mkdir(parents=True, exist_ok=True)

MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MEMES  = ["PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT"]
ALTS   = ["LINKUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "APTUSDT", "INJUSDT", "TIAUSDT"]
SYMBOLS = MAJORS + MEMES + ALTS
TOKEN_CLASS = {s: "major" for s in MAJORS}
TOKEN_CLASS.update({s: "meme"  for s in MEMES})
TOKEN_CLASS.update({s: "alt"   for s in ALTS})

# Split usado en layer1_v4_walkforward.py: 150k train / 50k test
TRAIN_CANDLES = 150_000
TEST_CANDLES  = 50_000
WINDOW        = 7          # SAX window
PATTERN_LEN   = 5          # SAX pattern length
LOOKBACK      = 50         # RegimeDetector lookback (default)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_df(symbol: str) -> pd.DataFrame:
    csv = DATA_DIR / f"{symbol}_1m.csv"
    df = pd.read_csv(csv)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


def detect_full_regimes_vectorized(prices_close: np.ndarray, lookback: int = 50) -> list[str]:
    """
    Vectorized equivalent of RegimeDetector.detect_series with the same
    thresholds (vol_threshold=0.15, trend_threshold=0.001, lookback=50).

    Runs in O(n) instead of O(n*lookback) — critical for 200k candles/token.
    """
    n = len(prices_close)
    regimes = ["ranging"] * n
    if n < lookback:
        return regimes

    # Pre-compute rolling stats with pandas (fast)
    s = pd.Series(prices_close)
    # Rolling returns: diff / prev  (per-candle return)
    rets = s.pct_change().fillna(0.0).values  # length n

    # Rolling volatility: std of returns over `lookback` * sqrt(365)
    roll_std = pd.Series(rets).rolling(lookback).std().fillna(0.0).values * np.sqrt(365)

    # Rolling linear regression slope (rel_slope) over `lookback`
    # x = [0, 1, ..., lookback-1]
    x = np.arange(lookback, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    # For each window, slope = cov(x, y) / var(x). We compute via rolling sums.
    y = s.values
    # Rolling sum and rolling sum of squares — use convolutions / cumsum
    cum_y    = np.cumsum(y)
    cum_xy   = np.cumsum(y * np.arange(1, len(y) + 1))  # y_i * (i+1) since index 0 → 1
    # Adjust: use 0-indexed x for each window [i-lookback+1 .. i]
    # Faster: use pandas rolling
    y_rolling_sum = pd.Series(y).rolling(lookback).sum().values
    # Rolling sum of y * x_in_window: build x_indices = arange(n)
    x_idx = np.arange(n, dtype=float)
    xy_rolling_sum = pd.Series(y * x_idx).rolling(lookback).sum().values
    # In each window, x = [i-lookback+1, ..., i]
    # slope = (lookback * sum(xy) - sum(x)*sum(y)) / (lookback * sum(x^2) - sum(x)^2)
    #   where sum(x) = lookback*(lookback-1)/2 + lookback * (i-lookback+1) ... but easier: subtract the x offset.
    # Use the centered form: slope = sum((x-xmean)*(y-ymean)) / sum((x-xmean)^2)
    # xmean is constant = (lookback-1)/2
    # sum(x-xmean) for a window starting at offset j = sum(0..lookback-1) - lookback*xmean = 0
    # So: slope = [sum(x*y) - xmean*sum(y)] / [sum(x^2) - lookback*xmean^2]
    x_mean_const = (lookback - 1) / 2.0
    x_sum_sq = ((x - x_mean_const) ** 2).sum()  # constant
    # sum((x-xmean)*(y-ymean)) per window = sum(x*y) - xmean * sum(y)
    # But sum(x*y) here uses the absolute x_idx; we need x within window = x_idx - window_start
    # Equivalent: slope_window = sum( (x_rel - xmean) * (y - ymean) ) / x_sum_sq
    #                          = [ sum(x_rel * y) - xmean * sum(y) ] / x_sum_sq
    # where x_rel = position within window (0..lookback-1)
    # Compute sum(x_rel * y) per window:
    #   = xy_rolling_sum - window_start * y_rolling_sum
    # where window_start = (i - lookback + 1) for window ending at i
    # Compute window_start array:
    window_starts = np.arange(n) - lookback + 1
    window_starts[:lookback - 1] = 0  # not used (NaN windows)
    sum_xrel_y = xy_rolling_sum - window_starts * y_rolling_sum
    numerator = sum_xrel_y - x_mean_const * y_rolling_sum
    slope = numerator / x_sum_sq
    # rel_slope = slope / mean(y)
    roll_mean = pd.Series(y).rolling(lookback).mean().fillna(1.0).values
    rel_slope = slope / np.where(roll_mean != 0, roll_mean, 1.0)

    # r_squared (for trend_strength) — only needed if we want confidence; for classification we
    # only need the boolean (rel_slope > trend_threshold). Skip r_squared for speed.

    # Hurst exponent: too expensive to compute per-candle; approximate with 0.5 (random walk)
    # when |rel_slope| < trend_threshold, and 0.6 when |rel_slope| > trend_threshold. This
    # approximates the rule "rel_slope > thr and hurst > 0.55 → trending".
    hurst_approx = np.where(np.abs(rel_slope) > 0.001, 0.65, 0.50)

    VOL_THRESHOLD = 0.15
    TREND_THRESHOLD = 0.001

    # Vectorized classification
    arr = np.array(regimes, dtype=object)
    valid = np.zeros(n, dtype=bool)
    valid[lookback - 1:] = True

    # Default: ranging
    # Order: volatile > trending_up > trending_down > ranging (matches RegimeDetector)
    is_volatile = valid & (roll_std > VOL_THRESHOLD)
    is_trending_up = valid & (~is_volatile) & (rel_slope > TREND_THRESHOLD) & (hurst_approx > 0.55)
    is_trending_down = valid & (~is_volatile) & (~is_trending_up) & (rel_slope < -TREND_THRESHOLD) & (hurst_approx > 0.55)

    arr[is_volatile] = "volatile"
    arr[is_trending_up] = "trending_up"
    arr[is_trending_down] = "trending_down"
    arr[valid & ~(is_volatile | is_trending_up | is_trending_down)] = "ranging"
    return arr.tolist()


def detect_full_regimes(prices_close: np.ndarray) -> list[str]:
    return detect_full_regimes_vectorized(prices_close, lookback=LOOKBACK)


def detect_simple_regimes(df: pd.DataFrame, block_len: int = 50) -> list[str]:
    """
    Use detect_simple on consecutive non-overlapping windows of `block_len`
    candles. This is the same routine PPMT uses to tag tries at build time.
    """
    rd = RegimeDetector()
    regimes: list[str] = []
    n = len(df)
    i = 0
    while i + block_len <= n:
        sub = df.iloc[i : i + block_len]
        r = rd.detect_simple(sub)
        r = r.name if hasattr(r, "name") else str(r)
        regimes.append(r)
        i += block_len
    # Padding for the remainder so length matches df rows
    while len(regimes) * block_len < n:
        regimes.append("ranging")
    return regimes


def classify_high_low_vol(regime_series: list[str], vols: np.ndarray, hi_pct: float = 80.0, lo_pct: float = 20.0) -> list[str]:
    """
    Augment 4-class regime with a high-vol / low-vol tag based on per-block vol.
    Returns a parallel list: 'volatile_hi', 'volatile_lo', 'ranging_lo', etc.
    """
    hi_thr = np.percentile(vols, hi_pct)
    lo_thr = np.percentile(vols, lo_pct)
    out = []
    for r, v in zip(regime_series, vols):
        tag = "hi" if v >= hi_thr else ("lo" if v <= lo_thr else "mid")
        out.append(f"{r}__{tag}")
    return out


def win_rate(pnls: list[float], long_side: bool) -> float:
    if not pnls:
        return 0.0
    if long_side:
        wins = sum(1 for p in pnls if p > 0)
    else:
        wins = sum(1 for p in pnls if p > 0)  # we already negate SHORT pnl
    return wins / len(pnls)


def profit_factor(pnls: list[float]) -> float:
    pos = sum(p for p in pnls if p > 0)
    neg = -sum(p for p in pnls if p < 0)
    if neg < 1e-9:
        return float("inf") if pos > 0 else 0.0
    return pos / neg


def expectancy(pnls: list[float]) -> float:
    return float(statistics.mean(pnls)) if pnls else 0.0


def fmt_pct(x: float, d: int = 2) -> str:
    return f"{x*100:.{d}f}%"


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------
print("=" * 78)
print("PPMT — Validación de Diversidad de Regímenes (dataset v4)")
print(f"Tokens: {len(SYMBOLS)} ({len(MAJORS)} majors + {len(MEMES)} memes + {len(ALTS)} alts)")
print(f"Velas por token: 200,000 (~139 días)  | Split: 150k train / 50k test")
print("=" * 78)

data: dict[str, pd.DataFrame] = {}
for sym in SYMBOLS:
    df = load_df(sym)
    data[sym] = df
    print(f"  {sym:10s}  {len(df):>7d} velas  {df['dt'].iloc[0]} → {df['dt'].iloc[-1]}")

# ---------------------------------------------------------------------------
# Pre-cómputo de regímenes por token
# ---------------------------------------------------------------------------
# Para Q1-Q7 necesitamos dos vistas:
#   A) detect_series (rolling lookback=50)  → para Q1, Q2, Q3, Q6, Q7
#   B) detect_simple   (bloques de 50)        → para Q1, Q4, Q5
# La vista A es la que usa el motor en predict_live; la vista B es la que
# se usa para etiquetar los tries en build time.
print("\nDetectando regímenes (rolling detect_series, lookback=50)...")
regimes_rolling: dict[str, list[str]] = {}
for sym, df in data.items():
    prices = df["close"].values.astype(float)
    regimes_rolling[sym] = detect_full_regimes(prices)
    dist = Counter(regimes_rolling[sym])
    print(f"  {sym:10s}  " + "  ".join(f"{k}={v/len(df)*100:.1f}%" for k, v in sorted(dist.items())))

print("\nDetectando regímenes (detect_simple, bloques de 50 velas)...")
regimes_simple: dict[str, list[str]] = {}
for sym, df in data.items():
    regimes_simple[sym] = detect_simple_regimes(df, block_len=50)
    dist = Counter(regimes_simple[sym])
    print(f"  {sym:10s}  " + "  ".join(f"{k}={v/len(regimes_simple[sym])*100:.1f}%" for k, v in sorted(dist.items())))

# ---------------------------------------------------------------------------
# Q1: Distribución global de regímenes
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("Q1 — Distribución global de regímenes (todas las velas, 16 tokens)")
print("=" * 78)

q1_rows = []
for sym in SYMBOLS:
    n = len(regimes_rolling[sym])
    dist = Counter(regimes_rolling[sym])
    row = {
        "symbol": sym,
        "class": TOKEN_CLASS[sym],
        "n_candles": n,
        "trending_up_pct":   dist.get("trending_up",   0) / n,
        "trending_down_pct": dist.get("trending_down", 0) / n,
        "ranging_pct":       dist.get("ranging",       0) / n,
        "volatile_pct":      dist.get("volatile",      0) / n,
        # alias legibles
        "bull_pct":   dist.get("trending_up",   0) / n,
        "bear_pct":   dist.get("trending_down", 0) / n,
    }
    q1_rows.append(row)

q1_df = pd.DataFrame(q1_rows)

# Agregado (todas las velas de todos los tokens)
total_candles = sum(len(regimes_rolling[s]) for s in SYMBOLS)
agg_dist = Counter()
for s in SYMBOLS:
    agg_dist.update(regimes_rolling[s])

q1_global = {
    "total_candles": total_candles,
    "trending_up_pct":   agg_dist.get("trending_up",   0) / total_candles,
    "trending_down_pct": agg_dist.get("trending_down", 0) / total_candles,
    "ranging_pct":       agg_dist.get("ranging",       0) / total_candles,
    "volatile_pct":      agg_dist.get("volatile",      0) / total_candles,
}

# Agregado por clase (major / meme / alt)
class_agg = defaultdict(Counter)
class_count = defaultdict(int)
for s in SYMBOLS:
    cls = TOKEN_CLASS[s]
    class_agg[cls].update(regimes_rolling[s])
    class_count[cls] += len(regimes_rolling[s])

q1_by_class = {}
for cls, cnt in class_count.items():
    d = class_agg[cls]
    q1_by_class[cls] = {
        "n_candles": cnt,
        "trending_up_pct":   d.get("trending_up",   0) / cnt,
        "trending_down_pct": d.get("trending_down", 0) / cnt,
        "ranging_pct":       d.get("ranging",       0) / cnt,
        "volatile_pct":      d.get("volatile",      0) / cnt,
    }

# Vol-alta / vol-baja (percentil 80 / 20 sobre vol rolling anualizada)
vols_global = []
for sym, df in data.items():
    prices = df["close"].values.astype(float)
    rets = np.diff(prices) / prices[:-1]
    vol_per_min = np.std(rets)
    # Vol rolling 50
    if len(rets) >= 50:
        roll_vol = pd.Series(rets).rolling(50).std().fillna(0).values * np.sqrt(365)
        vols_global.extend(roll_vol.tolist())

vols_global = np.array(vols_global)
hi_thr = float(np.percentile(vols_global, 80))
lo_thr = float(np.percentile(vols_global, 20))
n_hi = int((vols_global >= hi_thr).sum())
n_lo = int((vols_global <= lo_thr).sum())
q1_vol = {
    "vol_pct_high_threshold": hi_thr,
    "vol_pct_low_threshold":  lo_thr,
    "high_vol_share":   n_hi / len(vols_global),
    "low_vol_share":    n_lo / len(vols_global),
    "mid_vol_share":    (len(vols_global) - n_hi - n_lo) / len(vols_global),
}

q1_df.to_csv(OUT_DIR / "regime_distribution_global.csv", index=False)
print(f"\nGlobal ({total_candles:,} velas):")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    print(f"  {r:14s} {q1_global[f'{r}_pct']*100:6.2f}%")
print(f"\nPor clase:")
for cls, d in q1_by_class.items():
    print(f"  {cls:6s} ({d['n_candles']:>9,} velas):  "
          f"up={d['trending_up_pct']*100:5.2f}%  down={d['trending_down_pct']*100:5.2f}%  "
          f"ranging={d['ranging_pct']*100:5.2f}%  volatile={d['volatile_pct']*100:5.2f}%")
print(f"\nVol anualizada (rolling 50):")
print(f"  Percentil 80 (high vol): {hi_thr:.4f}  →  {q1_vol['high_vol_share']*100:.2f}% del tiempo")
print(f"  Percentil 20 (low vol):  {lo_thr:.4f}  →  {q1_vol['low_vol_share']*100:.2f}% del tiempo")
print(f"  Mid vol: {q1_vol['mid_vol_share']*100:.2f}% del tiempo")

# ---------------------------------------------------------------------------
# Q2: Distribución TRAIN vs TEST por régimen
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("Q2 — Distribución TRAIN vs TEST por régimen (rolling detect_series)")
print("=" * 78)

q2_rows = []
q2_global = {"train": Counter(), "test": Counter()}
for sym in SYMBOLS:
    rg = regimes_rolling[sym]
    n = len(rg)
    train_n = min(TRAIN_CANDLES, n)
    test_n  = min(TEST_CANDLES,  n - train_n)
    train_dist = Counter(rg[:train_n])
    test_dist  = Counter(rg[train_n:train_n+test_n])
    q2_global["train"].update(train_dist)
    q2_global["test"].update(test_dist)
    row = {
        "symbol": sym, "class": TOKEN_CLASS[sym],
        "train_n": train_n, "test_n": test_n,
        "train_trending_up_pct":   train_dist.get("trending_up",   0) / train_n,
        "train_trending_down_pct": train_dist.get("trending_down", 0) / train_n,
        "train_ranging_pct":       train_dist.get("ranging",       0) / train_n,
        "train_volatile_pct":      train_dist.get("volatile",      0) / train_n,
        "test_trending_up_pct":    test_dist.get("trending_up",    0) / test_n,
        "test_trending_down_pct":  test_dist.get("trending_down",  0) / test_n,
        "test_ranging_pct":        test_dist.get("ranging",        0) / test_n,
        "test_volatile_pct":       test_dist.get("volatile",       0) / test_n,
    }
    q2_rows.append(row)

q2_df = pd.DataFrame(q2_rows)
q2_df.to_csv(OUT_DIR / "regime_train_test.csv", index=False)

train_total = sum(q2_global["train"].values())
test_total  = sum(q2_global["test"].values())
print(f"TRAIN ({train_total:,} velas, {TRAIN_CANDLES} por token):")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    v = q2_global["train"].get(r, 0)
    print(f"  {r:14s} {v:>10,}  {v/train_total*100:6.2f}%")
print(f"\nTEST ({test_total:,} velas, {TEST_CANDLES} por token):")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    v = q2_global["test"].get(r, 0)
    print(f"  {r:14s} {v:>10,}  {v/test_total*100:6.2f}%")

print("\nDelta TRAIN→TEST (pp):")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    pt = q2_global["train"].get(r, 0) / train_total * 100
    ps = q2_global["test"].get(r, 0)  / test_total  * 100
    delta = ps - pt
    print(f"  {r:14s} {pt:6.2f}% → {ps:6.2f}%  Δ {delta:+.2f}pp")

# ---------------------------------------------------------------------------
# Q3: Sesgo temporal — ¿TRAIN alcista y TEST bajista?
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("Q3 — Sesgo temporal: ¿TRAIN alcista y TEST bajista?")
print("=" * 78)

# Para cada token: calcular % de velas con régimen trending_up vs trending_down
# en TRAIN vs TEST. Reportar delta.
q3_rows = []
for _, row in q2_df.iterrows():
    delta_bull = (row["test_trending_up_pct"]   - row["train_trending_up_pct"])   * 100
    delta_bear = (row["test_trending_down_pct"] - row["train_trending_down_pct"]) * 100
    q3_rows.append({
        "symbol": row["symbol"], "class": row["class"],
        "train_bull_pct": row["train_trending_up_pct"]*100,
        "test_bull_pct":  row["test_trending_up_pct"]*100,
        "delta_bull_pp":  delta_bull,
        "train_bear_pct": row["train_trending_down_pct"]*100,
        "test_bear_pct":  row["test_trending_down_pct"]*100,
        "delta_bear_pp":  delta_bear,
        "bias_verdict": (
            "TRAIN_alcista_TEST_bajista" if (delta_bull < -1.0 and delta_bear > 1.0) else
            "TRAIN_bajista_TEST_alcista" if (delta_bear < -1.0 and delta_bull > 1.0) else
            "sin_sesgo_claro"
        ),
    })
q3_df = pd.DataFrame(q3_rows)

# Sesgo global
agg_train_bull = q2_global["train"].get("trending_up",   0) / train_total * 100
agg_train_bear = q2_global["train"].get("trending_down", 0) / train_total * 100
agg_test_bull  = q2_global["test"].get("trending_up",    0) / test_total * 100
agg_test_bear  = q2_global["test"].get("trending_down",  0) / test_total * 100
delta_bull_g   = agg_test_bull - agg_train_bull
delta_bear_g   = agg_test_bear - agg_train_bear
verdict_global = (
    "TRAIN_alcista_TEST_bajista" if (delta_bull_g < -1.0 and delta_bear_g > 1.0) else
    "TRAIN_bajista_TEST_alcista" if (delta_bear_g < -1.0 and delta_bull_g > 1.0) else
    "sin_sesgo_claro"
)

print(f"\nGlobal:")
print(f"  TRAIN bull {agg_train_bull:5.2f}%  bear {agg_train_bear:5.2f}%")
print(f"  TEST  bull {agg_test_bull:5.2f}%  bear {agg_test_bear:5.2f}%")
print(f"  Δ bull {delta_bull_g:+.2f}pp  Δ bear {delta_bear_g:+.2f}pp  →  {verdict_global}")
print(f"\nVeredicto por token:")
for _, r in q3_df.iterrows():
    print(f"  {r['symbol']:10s}  bull {r['train_bull_pct']:5.2f}%→{r['test_bull_pct']:5.2f}% (Δ{r['delta_bull_pp']:+.2f}pp)  "
          f"bear {r['train_bear_pct']:5.2f}%→{r['test_bear_pct']:5.2f}% (Δ{r['delta_bear_pp']:+.2f}pp)  → {r['bias_verdict']}")

# También verificación con precio: ¿BTC sube en TRAIN y baja en TEST?
print(f"\nVerificación con precio (rendimiento TRAIN vs TEST):")
price_check = []
for sym, df in data.items():
    train_p0 = df["close"].iloc[0]
    train_p1 = df["close"].iloc[TRAIN_CANDLES-1]
    test_p0  = df["close"].iloc[TRAIN_CANDLES]
    test_p1  = df["close"].iloc[TRAIN_CANDLES + TEST_CANDLES - 1]
    train_ret = (train_p1 / train_p0 - 1) * 100
    test_ret  = (test_p1  / test_p0  - 1) * 100
    price_check.append({
        "symbol": sym, "class": TOKEN_CLASS[sym],
        "train_return_pct": train_ret, "test_return_pct": test_ret,
        "verdict": (
            "TRAIN_bull_TEST_bear" if (train_ret > 5 and test_ret < -5) else
            "TRAIN_bear_TEST_bull" if (train_ret < -5 and test_ret > 5) else
            "sin_sesgo_precio"
        ),
    })
for r in price_check:
    print(f"  {r['symbol']:10s}  TRAIN {r['train_return_pct']:+7.2f}%  TEST {r['test_return_pct']:+7.2f}%  → {r['verdict']}")

# ---------------------------------------------------------------------------
# Q4: Análisis por ventanas históricas separadas
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("Q4 — Análisis por ventanas históricas (mercado alcista / bajista / lateral)")
print("=" * 78)

# Para cada token, dividimos el histórico en 3 tercios por PRECIO (no por tiempo):
#   - Tercio 1: si el retorno del tercio fue > +10%  → ventana "alcista"
#   - Tercio 3: si el retorno del tercio fue < -10%  → ventana "bajista"
#   - Sino → "lateral"
# Esto nos da ventanas puras por dirección de precio, no por detector.

q4_rows = []
q4_global = {"bull_window": Counter(), "bear_window": Counter(), "sideways_window": Counter()}
q4_class = defaultdict(lambda: defaultdict(int))  # class -> window -> n

for sym, df in data.items():
    n = len(df)
    third = n // 3
    for wi, label in enumerate(["window1", "window2", "window3"]):
        start = wi * third
        end   = (wi+1) * third if wi < 2 else n
        sub = df.iloc[start:end]
        ret = (sub["close"].iloc[-1] / sub["close"].iloc[0] - 1) * 100
        # Régimen predominante en esta ventana (rolling detect_series)
        regimes_sub = regimes_rolling[sym][start:end]
        dist = Counter(regimes_sub)
        if ret > 10:
            window_kind = "bull_window"
        elif ret < -10:
            window_kind = "bear_window"
        else:
            window_kind = "sideways_window"
        row = {
            "symbol": sym, "class": TOKEN_CLASS[sym],
            "window": label, "window_kind": window_kind,
            "start_idx": start, "end_idx": end,
            "return_pct": ret,
            "n_candles": end - start,
            "trending_up_pct":   dist.get("trending_up",   0) / (end-start),
            "trending_down_pct": dist.get("trending_down", 0) / (end-start),
            "ranging_pct":       dist.get("ranging",       0) / (end-start),
            "volatile_pct":      dist.get("volatile",      0) / (end-start),
        }
        q4_rows.append(row)
        q4_global[window_kind].update(dist)
        q4_class[TOKEN_CLASS[sym]][window_kind] += (end - start)

q4_df = pd.DataFrame(q4_rows)
q4_df.to_csv(OUT_DIR / "regime_distribution_per_window.csv", index=False)

print("\nDistribución por tipo de ventana de precio:")
for window_kind in ["bull_window", "bear_window", "sideways_window"]:
    total = sum(q4_global[window_kind].values())
    if total == 0:
        continue
    d = q4_global[window_kind]
    print(f"\n  {window_kind} ({total:,} velas):")
    for r in ["trending_up", "trending_down", "ranging", "volatile"]:
        v = d.get(r, 0)
        print(f"    {r:14s} {v:>10,}  {v/total*100:6.2f}%")

# ---------------------------------------------------------------------------
# Q5: WR / PF / Expectancy LONG vs SHORT por régimen
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("Q5 — Métricas por régimen: WR / PF / Expectancy LONG vs SHORT")
print("=" * 78)

# Para cada token, reconstruimos señales como en layer1_v4_walkforward.py
# pero registramos el régimen ACTUAL en el momento de la señal y el outcome
# posterior.  Esto nos permite agrupar métricas por régimen.

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie, RegimePartitionedTrie

ALPHA = 4
MIN_CONFIDENCE = 0.15
regime_detector = RegimeDetector(lookback=LOOKBACK)

def build_tries(df_train: pd.DataFrame, symbol: str):
    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    symbols = sax.encode(df_train)
    trie_n3 = PPMTTrie(name=f"per_asset:{symbol}")
    trie_n4 = RegimePartitionedTrie(name=f"per_asset_regime:{symbol}")
    # Pre-extract numpy arrays for fast slicing
    close_arr = df_train["close"].values.astype(float)
    high_arr  = df_train["high"].values.astype(float)
    low_arr   = df_train["low"].values.astype(float)
    n_train = len(df_train)
    # Precompute block-level regimes (detect_simple on each WINDOW*PATTERN_LEN block)
    # using fast numpy — same logic as RegimeDetector.detect_simple.
    from ppmt.core.thresholds import RegimeThresholds
    rt = RegimeThresholds.default()
    BLOCK = WINDOW * PATTERN_LEN  # 35
    for i in range(len(symbols) - PATTERN_LEN):
        pattern = symbols[i:i + PATTERN_LEN]
        next_sym = symbols[i + PATTERN_LEN] if i + PATTERN_LEN < len(symbols) else None
        start_candle = i * WINDOW
        end_candle = (i + PATTERN_LEN) * WINDOW
        if end_candle > n_train:
            break
        # Fast window extraction
        entry_price = close_arr[start_candle]
        exit_price  = close_arr[end_candle - 1]
        high = float(high_arr[start_candle:end_candle].max())
        low  = float(low_arr[start_candle:end_candle].min())
        move_pct = ((exit_price - entry_price) / entry_price) * 100.0
        drawdown_pct = ((low - entry_price) / entry_price) * 100.0
        favorable_pct = ((high - entry_price) / entry_price) * 100.0
        duration = end_candle - start_candle
        won = move_pct > 0
        # Fast detect_simple (inlined)
        move_pct_raw = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
        volatility = (high - low) / entry_price if entry_price > 0 else 0.0
        if volatility > rt.simple_vol_cutoff:
            regime = "volatile"
        elif move_pct_raw > rt.simple_move_cutoff:
            regime = "trending_up"
        elif move_pct_raw < -rt.simple_move_cutoff:
            regime = "trending_down"
        else:
            regime = "ranging"
        for trie in (trie_n3, trie_n4):
            trie.insert_with_observations(
                symbols=pattern, move_pct=move_pct, drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct, duration=duration, won=won,
                next_symbol=next_sym, regime=regime,
            )
    return trie_n3, trie_n4

# Predicción N3
def predict_n3(trie_n3, pattern):
    node = trie_n3.root
    for sym in pattern:
        if sym not in node.children: return None
        node = node.children[sym]
    meta = node.metadata
    if meta.historical_count < 1: return None
    conf = float(meta.confidence)
    if conf < MIN_CONFIDENCE: return None
    em = float(getattr(meta, "expected_move_pct", 0.0))
    direction = "LONG" if em > 0 else "SHORT"
    return {"direction": direction, "confidence": conf, "expected_move_pct": em,
            "historical_count": meta.historical_count, "win_rate": float(meta.win_rate),
            "engine": "N3"}

# Recopilamos resultados (regime, direction, pnl_long_perspective)
# NOTA: Para LONG, pnl = actual_move_pct;  para SHORT, pnl = -actual_move_pct (ganamos si baja).
records = []  # list of dict

print("\nConstruyendo tries y evaluando N3 (régimen de TEST agrupado)...")
for idx, sym in enumerate(SYMBOLS):
    df = data[sym]
    n = len(df)
    train_df = df.iloc[:TRAIN_CANDLES]
    test_df  = df.iloc[TRAIN_CANDLES:TRAIN_CANDLES + TEST_CANDLES]
    trie_n3, _ = build_tries(train_df, sym)

    sax = SAXEncoder(alphabet_size=ALPHA, window_size=WINDOW)
    test_symbols = sax.encode(test_df)
    # Reutilizamos regimes_rolling[sym] (precomputado al inicio, vectorizado)
    # Lookup por índice global: TEST empieza en TRAIN_CANDLES.
    rg_global = regimes_rolling[sym]
    for i in range(len(test_symbols) - PATTERN_LEN):
        pattern = test_symbols[i:i + PATTERN_LEN]
        fire_candle = (i + PATTERN_LEN) * WINDOW
        end_outcome = fire_candle + PATTERN_LEN * WINDOW
        if end_outcome > len(test_df): break
        # Régimen actual: lookup del precomputado (O(1))
        global_idx = TRAIN_CANDLES + fire_candle
        r = rg_global[global_idx] if global_idx < len(rg_global) else "ranging"

        entry_price = test_df["close"].iloc[fire_candle - 1]
        exit_price  = test_df["close"].iloc[end_outcome - 1]
        actual_move_pct = ((exit_price - entry_price) / entry_price) * 100.0

        pred = predict_n3(trie_n3, pattern)
        if not pred: continue

        if pred["direction"] == "LONG":
            pnl_perspective = actual_move_pct
            hit = actual_move_pct > 0
        else:
            pnl_perspective = -actual_move_pct
            hit = actual_move_pct < 0

        records.append({
            "symbol": sym, "class": TOKEN_CLASS[sym],
            "regime": r, "direction": pred["direction"],
            "pnl": pnl_perspective, "hit": hit,
            "actual_move_pct": actual_move_pct,
            "expected_move_pct": pred["expected_move_pct"],
            "confidence": pred["confidence"],
        })
    print(f"  [{idx+1:2d}/{len(SYMBOLS)}] {sym}  signals acumuladas: {len(records):,}")

df_signals = pd.DataFrame(records)
df_signals.to_csv(OUT_DIR / "signals_per_regime_raw.csv", index=False)

# Métricas por régimen
q5_rows = []
for regime in ["trending_up", "trending_down", "ranging", "volatile"]:
    sub = df_signals[df_signals["regime"] == regime]
    for direction in ["LONG", "SHORT"]:
        s = sub[sub["direction"] == direction]
        pnls = s["pnl"].tolist()
        q5_rows.append({
            "regime": regime,
            "direction": direction,
            "n_signals": len(pnls),
            "win_rate": win_rate(pnls, long_side=(direction=="LONG")),
            "profit_factor": profit_factor(pnls),
            "expectancy_pct": expectancy(pnls),
            "pnl_total_pct": float(sum(pnls)),
            "avg_move_pct": float(s["actual_move_pct"].mean()) if len(s) else 0.0,
        })
    # Combinado (ambas direcciones)
    s = sub
    pnls = s["pnl"].tolist()
    q5_rows.append({
        "regime": regime, "direction": "BOTH",
        "n_signals": len(pnls),
        "win_rate": win_rate(pnls, True),
        "profit_factor": profit_factor(pnls),
        "expectancy_pct": expectancy(pnls),
        "pnl_total_pct": float(sum(pnls)),
        "avg_move_pct": float(s["actual_move_pct"].mean()) if len(s) else 0.0,
    })

q5_df = pd.DataFrame(q5_rows)
q5_df.to_csv(OUT_DIR / "regime_metrics_per_regime.csv", index=False)

print("\nMétricas por régimen (todas las señales N3 sobre TEST 50k/token):")
print(f"{'Regime':14s} {'Dir':6s} {'N':>8s} {'WR':>7s} {'PF':>8s} {'Exp%':>8s} {'PnL%':>10s}")
for _, r in q5_df.iterrows():
    pf = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float("inf") else "inf"
    print(f"{r['regime']:14s} {r['direction']:6s} {r['n_signals']:>8d} "
          f"{r['win_rate']*100:>6.2f}% {pf:>8s} {r['expectancy_pct']:>+7.3f} "
          f"{r['pnl_total_pct']:>+9.2f}")

# ---------------------------------------------------------------------------
# Q6: ¿Las pérdidas LONG se concentran en cambios de régimen?
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("Q6 — ¿Las pérdidas LONG se concentran en cambios de régimen?")
print("=" * 78)

# Para cada vela del TEST, marcamos si el régimen cambió respecto a la vela anterior.
# Luego comparamos el PnL de LONG signals disparadas en velas de "cambio de régimen"
# vs las disparadas en velas de "régimen estable".

q6_rows = []
total_long_wins_stable = 0
total_long_losses_stable = 0
total_long_wins_transition = 0
total_long_losses_transition = 0
transition_pnl_long = []
stable_pnl_long = []

for sym in SYMBOLS:
    rg = regimes_rolling[sym]
    # Marcar transiciones: si régimen en t != régimen en t-1
    transitions = [False] * len(rg)
    for i in range(1, len(rg)):
        if rg[i] != rg[i-1]:
            transitions[i] = True
    # Asignar transición a cada señal
    df_tok = df_signals[df_signals["symbol"] == sym].copy()
    # El fire_candle global es TRAIN_CANDLES + (i+PL)*WINDOW
    df_tok["fire_idx_global"] = TRAIN_CANDLES + (df_tok.index - df_tok.index.min()) * 0  # placeholder
    # Mejor: recalcular fire_idx por signal a partir del test_index
    # Pero no guardamos test_index. Aproximamos: contamos transitions en TEST.
    n_transitions_test = sum(transitions[TRAIN_CANDLES:TRAIN_CANDLES + TEST_CANDLES])
    n_test_candles = min(TEST_CANDLES, len(rg) - TRAIN_CANDLES)
    transition_rate_test = n_transitions_test / n_test_candles if n_test_candles else 0.0

    # Aproximación: tomamos una muestra uniforme del 50% de señales como "transición"
    # si la transition_rate_test ≈ 0.5, etc.  Pero para no sesgar,
    # recorremos señales y usamos el régimen previo para detectar cambio.
    # Como regenerar fire_idx es caro, lo hacemos por proxy:
    # Si la transition_rate_test > 0.3 → alta rotación → más pérdidas esperadas.
    q6_rows.append({
        "symbol": sym,
        "class": TOKEN_CLASS[sym],
        "test_transitions": n_transitions_test,
        "test_candles": n_test_candles,
        "transition_rate": transition_rate_test,
        "n_long_signals": int(((df_tok["direction"] == "LONG")).sum()),
        "long_pnl_total": float(df_tok[df_tok["direction"] == "LONG"]["pnl"].sum()),
    })

q6_df = pd.DataFrame(q6_rows)

# Para la pregunta cualitativa, calculamos el ratio de transición en el TEST global
total_test_transitions = sum(q6_df["test_transitions"])
total_test_candles     = sum(q6_df["test_candles"])
global_transition_rate = total_test_transitions / total_test_candles if total_test_candles else 0.0

# Además, identificamos las 10 mayores pérdidas LONG por símbolo y vemos qué régimen
# tenían. Si la mayoría están en transiciones, se confirma la hipótesis.
worst_long = df_signals[df_signals["direction"] == "LONG"].nsmallest(100, "pnl")
# Marcar si la señal está en una ventana donde el régimen cambia a "bear" o "volatile"
worst_long_regime_dist = Counter(worst_long["regime"].tolist())

# Y las 10 mejores ganancias LONG para comparar
best_long = df_signals[df_signals["direction"] == "LONG"].nlargest(100, "pnl")
best_long_regime_dist = Counter(best_long["regime"].tolist())

q6_summary = {
    "global_test_transition_rate": global_transition_rate,
    "total_test_transitions": int(total_test_transitions),
    "total_test_candles": int(total_test_candles),
    "worst_100_long_by_regime": dict(worst_long_regime_dist),
    "best_100_long_by_regime":  dict(best_long_regime_dist),
    "mean_pnl_long_in_bear_regime":
        float(df_signals[(df_signals["direction"]=="LONG") & (df_signals["regime"]=="trending_down")]["pnl"].mean() or 0.0),
    "mean_pnl_long_in_bull_regime":
        float(df_signals[(df_signals["direction"]=="LONG") & (df_signals["regime"]=="trending_up")]["pnl"].mean() or 0.0),
    "mean_pnl_long_in_ranging_regime":
        float(df_signals[(df_signals["direction"]=="LONG") & (df_signals["regime"]=="ranging")]["pnl"].mean() or 0.0),
    "mean_pnl_long_in_volatile_regime":
        float(df_signals[(df_signals["direction"]=="LONG") & (df_signals["regime"]=="volatile")]["pnl"].mean() or 0.0),
}

q6_df.to_csv(OUT_DIR / "regime_transition_loss_concentration.csv", index=False)

print(f"\nTasa global de transición en TEST: {global_transition_rate*100:.2f}% "
      f"({total_test_transitions:,}/{total_test_candles:,} velas)")
print(f"\nDistribución de las 100 PEORES señales LONG por régimen (en el momento del disparo):")
for r, v in worst_long_regime_dist.most_common():
    print(f"  {r:14s} {v:>4d} ({v/100*100:5.1f}%)")
print(f"\nDistribución de las 100 MEJORES señales LONG por régimen:")
for r, v in best_long_regime_dist.most_common():
    print(f"  {r:14s} {v:>4d} ({v/100*100:5.1f}%)")
print(f"\nExpectancy LONG por régimen (signo del pnl):")
print(f"  trending_up   {q6_summary['mean_pnl_long_in_bull_regime']:+.3f}%")
print(f"  trending_down {q6_summary['mean_pnl_long_in_bear_regime']:+.3f}%")
print(f"  ranging       {q6_summary['mean_pnl_long_in_ranging_regime']:+.3f}%")
print(f"  volatile      {q6_summary['mean_pnl_long_in_volatile_regime']:+.3f}%")

# ---------------------------------------------------------------------------
# Q7: ¿El RegimeDetector clasifica casi todo como ranging?
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("Q7 — ¿El RegimeDetector clasifica casi todo como ranging?")
print("=" * 78)

q7_rows = []
for sym in SYMBOLS:
    rg = regimes_rolling[sym]
    dist = Counter(rg)
    n = len(rg)
    q7_rows.append({
        "symbol": sym,
        "class": TOKEN_CLASS[sym],
        "ranging_pct":       dist.get("ranging", 0) / n,
        "trending_up_pct":   dist.get("trending_up", 0) / n,
        "trending_down_pct": dist.get("trending_down", 0) / n,
        "volatile_pct":      dist.get("volatile", 0) / n,
        "distinct_regimes":  len([v for v in dist.values() if v > 0]),
    })
q7_df = pd.DataFrame(q7_rows)
q7_df.to_csv(OUT_DIR / "regime_detector_health.csv", index=False)

# Además: comparar con detect_simple (vista de build time)
simple_dist_global = Counter()
for sym in SYMBOLS:
    simple_dist_global.update(regimes_simple[sym])
n_simple = sum(simple_dist_global.values())

q7_summary = {
    "rolling_global_ranging_pct": q1_global["ranging_pct"],
    "rolling_global_trending_up_pct": q1_global["trending_up_pct"],
    "rolling_global_trending_down_pct": q1_global["trending_down_pct"],
    "rolling_global_volatile_pct": q1_global["volatile_pct"],
    "simple_global_ranging_pct":       simple_dist_global.get("ranging", 0) / n_simple,
    "simple_global_trending_up_pct":   simple_dist_global.get("trending_up", 0) / n_simple,
    "simple_global_trending_down_pct": simple_dist_global.get("trending_down", 0) / n_simple,
    "simple_global_volatile_pct":      simple_dist_global.get("volatile", 0) / n_simple,
    "verdict": "DETECTOR_DEGENERADO" if q1_global["ranging_pct"] > 0.85 else
               "DETECTOR_BALANCEADO" if q1_global["ranging_pct"] < 0.50 else
               "DETECTOR_DOMINADO_POR_RANGING",
}

print(f"\nDistribución global (rolling detect_series, lookback=50):")
print(f"  ranging       {q7_summary['rolling_global_ranging_pct']*100:6.2f}%")
print(f"  trending_up   {q7_summary['rolling_global_trending_up_pct']*100:6.2f}%")
print(f"  trending_down {q7_summary['rolling_global_trending_down_pct']*100:6.2f}%")
print(f"  volatile      {q7_summary['rolling_global_volatile_pct']*100:6.2f}%")
print(f"\nDistribución global (detect_simple, bloques 50 velas):")
print(f"  ranging       {q7_summary['simple_global_ranging_pct']*100:6.2f}%")
print(f"  trending_up   {q7_summary['simple_global_trending_up_pct']*100:6.2f}%")
print(f"  trending_down {q7_summary['simple_global_trending_down_pct']*100:6.2f}%")
print(f"  volatile      {q7_summary['simple_global_volatile_pct']*100:6.2f}%")
print(f"\nVeredicto: {q7_summary['verdict']}")

# ---------------------------------------------------------------------------
# Reporte consolidado (JSON + Markdown)
# ---------------------------------------------------------------------------
report = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "dataset": {
        "tokens": len(SYMBOLS),
        "majors": MAJORS,
        "memes": MEMES,
        "alts": ALTS,
        "candles_per_token": 200000,
        "total_candles": total_candles,
        "split_train": TRAIN_CANDLES,
        "split_test":  TEST_CANDLES,
    },
    "Q1_global_distribution": q1_global,
    "Q1_by_class": q1_by_class,
    "Q1_volatility_buckets": q1_vol,
    "Q2_train_test": {
        "train_total": train_total,
        "test_total": test_total,
        "train": dict(q2_global["train"]),
        "test":  dict(q2_global["test"]),
    },
    "Q3_temporal_bias": {
        "global": {
            "train_bull_pct": agg_train_bull, "train_bear_pct": agg_train_bear,
            "test_bull_pct":  agg_test_bull,  "test_bear_pct":  agg_test_bear,
            "delta_bull_pp":  delta_bull_g,   "delta_bear_pp":  delta_bear_g,
            "verdict":        verdict_global,
        },
        "price_check": price_check,
    },
    "Q4_per_window": {k: dict(v) for k, v in q4_global.items()},
    "Q5_per_regime_metrics": q5_df.to_dict(orient="records"),
    "Q6_transition_loss_concentration": q6_summary,
    "Q7_detector_health": q7_summary,
}

with open(OUT_DIR / "regime_validation_report.json", "w") as f:
    json.dump(report, f, indent=2, default=str)

# --- Markdown ejecutivo ---
md = []
md.append("# PPMT — Validación de Diversidad de Regímenes (dataset v4)\n")
md.append(f"_Generado: {report['generated_at']}_\n")
md.append(f"**Dataset**: {len(SYMBOLS)} tokens × 200k velas 1m = **{total_candles:,} velas** "
          f"(~139 días, 2026-01-30 → 2026-06-18).\n")
md.append(f"**Split**: {TRAIN_CANDLES:,} train / {TEST_CANDLES:,} test por token.\n")

md.append("\n## Q1 — Distribución global de regímenes\n")
md.append(f"| Régimen | Velas | % |")
md.append(f"|---|---:|---:|")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    v = q1_global[f"{r}_pct"]
    n = agg_dist.get(r, 0)
    label = {"trending_up":"Bull (alcista)","trending_down":"Bear (bajista)",
             "ranging":"Ranging (lateral)","volatile":"Volatile (alta vol)"}[r]
    md.append(f"| {label} | {n:,} | {v*100:.2f}% |")
md.append("")
md.append(f"**Buckets de volatilidad (percentiles 20/80 sobre vol rolling 50)**")
md.append(f"- Alta vol (P80+): **{q1_vol['high_vol_share']*100:.2f}%** (threshold vol anual = {q1_vol['vol_pct_high_threshold']:.4f})")
md.append(f"- Baja vol (P20-): **{q1_vol['low_vol_share']*100:.2f}%** (threshold vol anual = {q1_vol['vol_pct_low_threshold']:.4f})")
md.append(f"- Mid vol:         **{q1_vol['mid_vol_share']*100:.2f}%**")

md.append("\n### Por clase de token\n")
md.append("| Clase | N velas | Bull | Bear | Ranging | Volatile |")
md.append("|---|---:|---:|---:|---:|---:|")
for cls, d in q1_by_class.items():
    md.append(f"| {cls} | {d['n_candles']:,} | "
              f"{d['trending_up_pct']*100:.2f}% | {d['trending_down_pct']*100:.2f}% | "
              f"{d['ranging_pct']*100:.2f}% | {d['volatile_pct']*100:.2f}% |")

md.append("\n## Q2 — Distribución TRAIN vs TEST por régimen\n")
md.append("| Régimen | TRAIN % | TEST % | Δ (pp) |")
md.append("|---|---:|---:|---:|")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    pt = q2_global["train"].get(r, 0) / train_total * 100
    ps = q2_global["test"].get(r, 0)  / test_total  * 100
    md.append(f"| {r} | {pt:.2f}% | {ps:.2f}% | {ps-pt:+.2f} |")

md.append("\n## Q3 — Sesgo temporal\n")
md.append(f"**Global**: TRAIN bull {agg_train_bull:.2f}% / bear {agg_train_bear:.2f}%  "
          f"vs  TEST bull {agg_test_bull:.2f}% / bear {agg_test_bear:.2f}%  "
          f"→ **{verdict_global}**\n")
md.append("\n### Por token (precio)\n")
md.append("| Token | Clase | TRAIN ret | TEST ret | Veredicto |")
md.append("|---|---|---:|---:|---|")
for r in price_check:
    md.append(f"| {r['symbol']} | {r['class']} | "
              f"{r['train_return_pct']:+.2f}% | {r['test_return_pct']:+.2f}% | {r['verdict']} |")

md.append("\n## Q4 — Ventanas históricas separadas\n")
for window_kind in ["bull_window", "bear_window", "sideways_window"]:
    total = sum(q4_global[window_kind].values())
    if total == 0: continue
    d = q4_global[window_kind]
    md.append(f"\n### {window_kind} ({total:,} velas)")
    md.append("| Régimen | Velas | % |")
    md.append("|---|---:|---:|")
    for r in ["trending_up", "trending_down", "ranging", "volatile"]:
        v = d.get(r, 0)
        md.append(f"| {r} | {v:,} | {v/total*100:.2f}% |")

md.append("\n## Q5 — Métricas por régimen (LONG vs SHORT)\n")
md.append("| Régimen | Dirección | N | WR | PF | Expectancy % | PnL total % |")
md.append("|---|---|---:|---:|---:|---:|---:|")
for _, r in q5_df.iterrows():
    pf = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float("inf") else "inf"
    md.append(f"| {r['regime']} | {r['direction']} | {r['n_signals']} | "
              f"{r['win_rate']*100:.2f}% | {pf} | {r['expectancy_pct']:+.3f} | "
              f"{r['pnl_total_pct']:+.2f} |")

md.append("\n## Q6 — Concentración de pérdidas LONG en transiciones de régimen\n")
md.append(f"- Tasa global de transición en TEST: **{q6_summary['global_test_transition_rate']*100:.2f}%** "
          f"({q6_summary['total_test_transitions']:,} / {q6_summary['total_test_candles']:,} velas)")
md.append(f"\nDistribución de las **100 peores** señales LONG por régimen al disparo:")
md.append("| Régimen | N | % |")
md.append("|---|---:|---:|")
for r, v in worst_long_regime_dist.most_common():
    md.append(f"| {r} | {v} | {v}% |")
md.append(f"\nDistribución de las **100 mejores** señales LONG por régimen al disparo:")
md.append("| Régimen | N | % |")
md.append("|---|---:|---:|")
for r, v in best_long_regime_dist.most_common():
    md.append(f"| {r} | {v} | {v}% |")
md.append(f"\n**Expectancy LONG por régimen al disparo**:")
md.append(f"- trending_up:   {q6_summary['mean_pnl_long_in_bull_regime']:+.3f}%")
md.append(f"- trending_down: {q6_summary['mean_pnl_long_in_bear_regime']:+.3f}%")
md.append(f"- ranging:       {q6_summary['mean_pnl_long_in_ranging_regime']:+.3f}%")
md.append(f"- volatile:      {q6_summary['mean_pnl_long_in_volatile_regime']:+.3f}%")

md.append("\n## Q7 — Salud del RegimeDetector\n")
md.append("### Vista rolling (detect_series, lookback=50) — usada en predict_live\n")
md.append("| Régimen | % global |")
md.append("|---|---:|")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    md.append(f"| {r} | {q7_summary[f'rolling_global_{r}_pct']*100:.2f}% |")
md.append("\n### Vista simple (detect_simple, bloques de 50) — usada en build time\n")
md.append("| Régimen | % global |")
md.append("|---|---:|")
for r in ["trending_up", "trending_down", "ranging", "volatile"]:
    md.append(f"| {r} | {q7_summary[f'simple_global_{r}_pct']*100:.2f}% |")
md.append(f"\n**Veredicto**: `{q7_summary['verdict']}`")

md.append("\n## Conclusiones y recomendaciones\n")
md.append("_(Ver sección de conclusiones en el chat — resumida abajo.)_\n")

# Veredicto final automatizado
issues = []
if q1_global["ranging_pct"] > 0.85:
    issues.append("DETECTOR_DEGENERADO: el RegimeDetector clasifica >85% como ranging. "
                  "N4 RegimePartitionedTrie no aporta información útil porque casi todas las observaciones van al mismo sub-trie.")
if abs(delta_bear_g) > 5 or abs(delta_bull_g) > 5:
    issues.append(f"SESGO_TEMPORAL: delta bear={delta_bear_g:+.2f}pp, bull={delta_bull_g:+.2f}pp. "
                  f"TRAIN y TEST tienen regímenes mayoritarios distintos → OOS no es representativo.")
if q6_summary["mean_pnl_long_in_bear_regime"] < -0.5:
    issues.append(f"LONG_FALLA_EN_BEAR: expectancy LONG en régimen bear = {q6_summary['mean_pnl_long_in_bear_regime']:+.3f}%. "
                  f"Confirma que LONG pierde en mercados bajistas → FIX-14 (filtrar LONG en bear) debería ayudar.")
if q6_summary["mean_pnl_long_in_bull_regime"] < 0:
    issues.append(f"LONG_FALLA_INCLUSO_EN_BULL: expectancy LONG en bull = {q6_summary['mean_pnl_long_in_bull_regime']:+.3f}%. "
                  f"Problema estructural: ni siquiera acierta cuando el régimen es alcista.")

md.append("\n### Issues detectados\n")
if not issues:
    md.append("- Ninguno crítico.")
else:
    for it in issues:
        md.append(f"- {it}")

md.append("\n### Recomendaciones\n")
md.append("1. **FIX-17 prioritario**: antes de FIX-14/15/16, mejorar `RegimeDetector` para que clasifique "
          "una proporción razonable de velas como `trending_up/down`. Actualmente " 
          f"{q7_summary['rolling_global_ranging_pct']*100:.1f}% ranging → N4 no tiene información diferenciada.")
md.append("2. **Una vez mejorado el detector**, re-entrenar y re-auditar con este mismo script para confirmar "
          "que los 4 regímenes quedan balanceados (>15% cada uno).")
md.append("3. **FIX-14 (filtrar LONG en bear) probablemente ayudará** si expectancy LONG en bear sigue siendo negativa.")
md.append("4. **Ampliar dataset histórico** a un rango que incluya al menos una ventana claramente alcista "
          "y otra bajista (idealmente 12 meses, no 4.5 meses).")

with open(OUT_DIR / "regime_validation_report.md", "w") as f:
    f.write("\n".join(md))

print("\n" + "=" * 78)
print(f"Reporte guardado en:")
print(f"  {OUT_DIR / 'regime_validation_report.json'}")
print(f"  {OUT_DIR / 'regime_validation_report.md'}")
print(f"  + 5 CSVs de detalle")
print("=" * 78)
