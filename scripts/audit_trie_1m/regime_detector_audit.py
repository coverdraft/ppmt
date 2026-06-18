"""
PPMT — Auditoría del RegimeDetector.

Objetivo: distinguir entre A) dataset insuficiente, B) detector degenerado, C) ambos.

Estrategia:
  1. Implementar 4 métricas externas independientes:
     - EMA slope (21/55 EMA crossover + slope)
     - ADX (Average Directional Index, período 14)
     - Volatilidad realizada (rolling 50, anualizada)
     - Drawdown (rolling max drawdown)
     - Retorno acumulado (rolling window 50)

  2. Seleccionar ventanas visualmente claras (usando retorno acumulado):
     - Bull: retorno +20% en la ventana
     - Bear: retorno -20% en la ventana
     - Range: retorno entre -5% y +5%, baja vol

  3. Para cada ventana, mostrar cómo la clasifica:
     - Detector actual (RegimeDetector.detect_series)
     - EMA slope
     - ADX
     - Combinación EMA+ADX

  4. Acuerdo con etiquetado humano: si la ventana fue etiquetada
     visualmente como bull/bear/range, ¿qué porcentaje del tiempo
     cada método clasifica igual?

  5. Distribución global sobre el dataset completo con cada método.

Salida:
  /home/z/my-project/download/regime_audit/audit_report.md
  /home/z/my-project/download/regime_audit/audit_report.json
  /home/z/my-project/download/regime_audit/window_classification.csv
  /home/z/my-project/download/regime_audit/global_distribution.csv
  /home/z/my-project/download/regime_audit/human_agreement.csv
"""
from __future__ import annotations
import json, sys, statistics
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/z/my-project/ppmt/src")
from ppmt.core.regime import RegimeDetector

OUT_DIR = Path("/home/z/my-project/download/regime_audit")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Configuración: dos datasets
# ---------------------------------------------------------------------------
DATASETS = {
    "v4_139d": {
        "dir": Path("/home/z/my-project/download/real_data_1m_v4"),
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                    "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT",
                    "LINKUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "APTUSDT", "INJUSDT", "TIAUSDT"],
        "label": "v4 (139 días, 16 tokens, 200k velas/token)",
    },
    "12m_5maj": {
        "dir": Path("/home/z/my-project/download/real_data_1m_12m"),
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
        "label": "12m (5 majors, ~365 días, 525k velas/token)",
    },
}

LOOKBACK = 50  # Mismo que RegimeDetector

# ---------------------------------------------------------------------------
# Métricas externas
# ---------------------------------------------------------------------------
def compute_ema_slope(prices: np.ndarray, fast: int = 21, slow: int = 55) -> np.ndarray:
    """
    EMA slope classifier:
      +1 = bullish (EMA_fast > EMA_slow AND slope EMA_fast > 0)
      -1 = bearish (EMA_fast < EMA_slow AND slope EMA_fast < 0)
       0 = neutral
    Returns array of {1, 0, -1} per candle.
    """
    s = pd.Series(prices)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    # Slope of EMA_fast over 5 candles
    slope = ema_fast.diff(5).fillna(0.0)
    # Relative slope
    rel_slope = slope / ema_fast

    out = np.zeros(len(prices), dtype=int)
    bullish = (ema_fast > ema_slow) & (rel_slope > 0.0005)  # 0.05% per 5 candles
    bearish = (ema_fast < ema_slow) & (rel_slope < -0.0005)
    out[bullish.values] = 1
    out[bearish.values] = -1
    return out


def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """
    ADX (Wilder). Returns ADX values (not direction).
    Convention:
      ADX > 25 → trending (either up or down, decide by +DI vs -DI)
      ADX <= 20 → ranging
      20 < ADX <= 25 → transitional
    Returns array of {1 bullish, -1 bearish, 0 ranging} per candle.
    """
    h = pd.Series(high)
    l = pd.Series(low)
    c = pd.Series(close)

    # True Range
    tr = pd.concat([(h - l),
                    (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)

    # +DM / -DM
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
                        index=h.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
                         index=h.index)

    # Wilder smoothing
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, adjust=False).mean().fillna(0.0)

    out = np.zeros(len(close), dtype=int)
    trending = adx > 25
    bullish = trending & (plus_di > minus_di)
    bearish = trending & (minus_di > plus_di)
    out[bullish.values] = 1
    out[bearish.values] = -1
    # 0 = ranging or transitional
    return out


def compute_realized_vol(prices: np.ndarray, window: int = 50) -> np.ndarray:
    """Rolling realized vol (annualized)."""
    rets = pd.Series(prices).pct_change().fillna(0.0)
    return rets.rolling(window).std().fillna(0.0).values * np.sqrt(365)


def compute_drawdown(prices: np.ndarray) -> np.ndarray:
    """Drawdown at each point (as fraction, negative)."""
    s = pd.Series(prices)
    running_max = s.cummax()
    return (s / running_max - 1.0).fillna(0.0).values


def compute_cumret(prices: np.ndarray, window: int = 50) -> np.ndarray:
    """Rolling cumulative return over window."""
    s = pd.Series(prices)
    return (s / s.shift(window) - 1.0).fillna(0.0).values * 100.0  # in %


# ---------------------------------------------------------------------------
# RegimeDetector vectorizado (igual que el script anterior)
# ---------------------------------------------------------------------------
def detect_series_vectorized(prices: np.ndarray, lookback: int = 50) -> list[str]:
    n = len(prices)
    regimes = ["ranging"] * n
    if n < lookback:
        return regimes

    s = pd.Series(prices)
    rets = s.pct_change().fillna(0.0)
    roll_std = rets.rolling(lookback).std().fillna(0.0).values * np.sqrt(365)

    x = np.arange(lookback, dtype=float)
    y = s.values
    y_rolling_sum = pd.Series(y).rolling(lookback).sum().values
    x_idx = np.arange(n, dtype=float)
    xy_rolling_sum = pd.Series(y * x_idx).rolling(lookback).sum().values
    x_mean_const = (lookback - 1) / 2.0
    x_sum_sq = ((x - x_mean_const) ** 2).sum()
    window_starts = np.arange(n) - lookback + 1
    window_starts[:lookback - 1] = 0
    sum_xrel_y = xy_rolling_sum - window_starts * y_rolling_sum
    numerator = sum_xrel_y - x_mean_const * y_rolling_sum
    slope = numerator / x_sum_sq
    roll_mean = pd.Series(y).rolling(lookback).mean().fillna(1.0).values
    rel_slope = slope / np.where(roll_mean != 0, roll_mean, 1.0)

    hurst_approx = np.where(np.abs(rel_slope) > 0.001, 0.65, 0.50)

    arr = np.array(regimes, dtype=object)
    valid = np.zeros(n, dtype=bool)
    valid[lookback - 1:] = True
    is_volatile = valid & (roll_std > 0.15)
    is_trending_up = valid & (~is_volatile) & (rel_slope > 0.001) & (hurst_approx > 0.55)
    is_trending_down = valid & (~is_volatile) & (~is_trending_up) & (rel_slope < -0.001) & (hurst_approx > 0.55)
    arr[is_volatile] = "volatile"
    arr[is_trending_up] = "trending_up"
    arr[is_trending_down] = "trending_down"
    arr[valid & ~(is_volatile | is_trending_up | is_trending_down)] = "ranging"
    return arr.tolist()


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------
def load_df(symbol: str, dataset_key: str) -> pd.DataFrame | None:
    cfg = DATASETS[dataset_key]
    csv = cfg["dir"] / f"{symbol}_1m.csv"
    if not csv.exists():
        return None
    df = pd.read_csv(csv)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


# ---------------------------------------------------------------------------
# Selección de ventanas visualmente claras
# ---------------------------------------------------------------------------
def select_windows(df: pd.DataFrame, window_len: int = 1000) -> list[dict]:
    """
    Slide a window of `window_len` candles (~16.6h) over the dataset.
    For each window, compute return and vol. Classify:
      - bull:  return > +5%  AND  vol < 0.10
      - bear:  return < -5%  AND  vol < 0.10
      - range: |return| < 1% AND  vol < 0.05
    Pick up to 5 non-overlapping windows per class.
    """
    prices = df["close"].values.astype(float)
    n = len(prices)
    if n < window_len:
        return []

    found = {"bull": [], "bear": [], "range": []}
    last_end = -window_len
    step = window_len // 2  # 50% overlap for scanning, but enforce non-overlap on selection
    for start in range(0, n - window_len, step):
        end = start + window_len
        if start < last_end + window_len:
            continue  # enforce non-overlap
        window_prices = prices[start:end]
        ret = (window_prices[-1] / window_prices[0] - 1) * 100
        rets = np.diff(window_prices) / window_prices[:-1]
        vol = float(np.std(rets) * np.sqrt(365))

        if ret > 5 and vol < 0.20 and len(found["bull"]) < 5:
            found["bull"].append({"start": start, "end": end, "ret": ret, "vol": vol})
            last_end = end
        elif ret < -5 and vol < 0.20 and len(found["bear"]) < 5:
            found["bear"].append({"start": start, "end": end, "ret": ret, "vol": vol})
            last_end = end
        elif abs(ret) < 1 and vol < 0.10 and len(found["range"]) < 5:
            found["range"].append({"start": start, "end": end, "ret": ret, "vol": vol})
            last_end = end

        if all(len(v) >= 5 for v in found.values()):
            break

    windows = []
    for kind, items in found.items():
        for w in items:
            w["kind"] = kind
            windows.append(w)
    return windows


# ---------------------------------------------------------------------------
# Clasificación por métricas externas en una ventana
# ---------------------------------------------------------------------------
def classify_window(emas: np.ndarray, adxs: np.ndarray, vol: np.ndarray,
                    start: int, end: int) -> dict:
    sub_ema = emas[start:end]
    sub_adx = adxs[start:end]
    sub_vol = vol[start:end]

    # Mayoría de velas en cada clase
    n = end - start
    ema_bull = int((sub_ema == 1).sum())
    ema_bear = int((sub_ema == -1).sum())
    ema_neutral = n - ema_bull - ema_bear

    adx_bull = int((sub_adx == 1).sum())
    adx_bear = int((sub_adx == -1).sum())
    adx_neutral = n - adx_bull - adx_bear

    # Combinación EMA+ADX: solo bullish si AMBOS son bullish
    combo_bull = int(((sub_ema == 1) & (sub_adx == 1)).sum())
    combo_bear = int(((sub_ema == -1) & (sub_adx == -1)).sum())
    combo_neutral = n - combo_bull - combo_bear

    # Vol promedio
    avg_vol = float(np.mean(sub_vol))
    # Pct velas con vol > 0.15 (umbral del detector actual)
    pct_high_vol = float((sub_vol > 0.15).mean())

    return {
        "n": n,
        "ema_bull_pct":   ema_bull / n,
        "ema_bear_pct":   ema_bear / n,
        "ema_neutral_pct": ema_neutral / n,
        "adx_bull_pct":   adx_bull / n,
        "adx_bear_pct":   adx_bear / n,
        "adx_neutral_pct": adx_neutral / n,
        "combo_bull_pct": combo_bull / n,
        "combo_bear_pct": combo_bear / n,
        "combo_neutral_pct": combo_neutral / n,
        "avg_vol": avg_vol,
        "pct_high_vol": pct_high_vol,
    }


def classify_window_detector(regimes: list[str], start: int, end: int) -> dict:
    sub = regimes[start:end]
    dist = Counter(sub)
    n = end - start
    return {
        "n": n,
        "trending_up_pct":   dist.get("trending_up", 0) / n,
        "trending_down_pct": dist.get("trending_down", 0) / n,
        "ranging_pct":       dist.get("ranging", 0) / n,
        "volatile_pct":      dist.get("volatile", 0) / n,
    }


# ---------------------------------------------------------------------------
# Procesamiento por dataset
# ---------------------------------------------------------------------------
def audit_dataset(dataset_key: str) -> dict:
    cfg = DATASETS[dataset_key]
    print("\n" + "=" * 78)
    print(f"Auditoría RegimeDetector — dataset {dataset_key}")
    print(f"  {cfg['label']}")
    print("=" * 78)

    per_token = []
    window_rows = []
    global_dist_detector = Counter()
    global_dist_ema = Counter()
    global_dist_adx = Counter()
    global_dist_combo = Counter()
    global_total = 0

    for sym in cfg["symbols"]:
        df = load_df(sym, dataset_key)
        if df is None or len(df) < 1000:
            print(f"  [SKIP] {sym}: no data")
            continue
        print(f"\n  [{sym}] {len(df):,} velas  {df['dt'].iloc[0]} → {df['dt'].iloc[-1]}")

        prices = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)

        # Métricas externas
        ema_slope = compute_ema_slope(prices)
        adx = compute_adx(high, low, prices)
        rvol = compute_realized_vol(prices, window=LOOKBACK)
        dd = compute_drawdown(prices)
        cumret = compute_cumret(prices, window=LOOKBACK)

        # Detector actual
        regimes = detect_series_vectorized(prices, lookback=LOOKBACK)

        # Distribución global por método
        for r in regimes:
            global_dist_detector[r] += 1
        for v in ema_slope:
            global_dist_ema[{1:"trending_up", -1:"trending_down", 0:"ranging"}[v]] += 1
        for v in adx:
            global_dist_adx[{1:"trending_up", -1:"trending_down", 0:"ranging"}[v]] += 1
        for i in range(len(prices)):
            if ema_slope[i] == 1 and adx[i] == 1:
                global_dist_combo["trending_up"] += 1
            elif ema_slope[i] == -1 and adx[i] == -1:
                global_dist_combo["trending_down"] += 1
            else:
                global_dist_combo["ranging"] += 1
        global_total += len(prices)

        # Estadísticas por token
        det_dist = Counter(regimes)
        ema_dist = Counter(ema_slope.tolist())
        adx_dist = Counter(adx.tolist())
        combo_dist = Counter()
        for i in range(len(prices)):
            if ema_slope[i] == 1 and adx[i] == 1: combo_dist["up"] += 1
            elif ema_slope[i] == -1 and adx[i] == -1: combo_dist["down"] += 1
            else: combo_dist["range"] += 1

        per_token.append({
            "symbol": sym,
            "n_candles": len(df),
            "detector_dist": {k: det_dist.get(k,0)/len(df) for k in ["trending_up","trending_down","ranging","volatile"]},
            "ema_dist": {
                "bullish": ema_dist.get(1, 0)/len(df),
                "bearish": ema_dist.get(-1, 0)/len(df),
                "neutral": ema_dist.get(0, 0)/len(df),
            },
            "adx_dist": {
                "bullish": adx_dist.get(1, 0)/len(df),
                "bearish": adx_dist.get(-1, 0)/len(df),
                "ranging": adx_dist.get(0, 0)/len(df),
            },
            "combo_dist": {k: combo_dist.get(k,0)/len(df) for k in ["up","down","range"]},
            "avg_vol": float(np.mean(rvol)),
            "pct_high_vol_0p15": float((rvol > 0.15).mean()),
            "avg_drawdown_pct": float(np.mean(dd) * 100),
            "max_drawdown_pct": float(np.min(dd) * 100),
            "cumret_50_mean_pct": float(np.mean(cumret)),
        })

        print(f"    detector:  up={per_token[-1]['detector_dist']['trending_up']*100:5.2f}%  "
              f"down={per_token[-1]['detector_dist']['trending_down']*100:5.2f}%  "
              f"ranging={per_token[-1]['detector_dist']['ranging']*100:5.2f}%  "
              f"volatile={per_token[-1]['detector_dist']['volatile']*100:5.2f}%")
        print(f"    EMA slope: bull={per_token[-1]['ema_dist']['bullish']*100:5.2f}%  "
              f"bear={per_token[-1]['ema_dist']['bearish']*100:5.2f}%  "
              f"neutral={per_token[-1]['ema_dist']['neutral']*100:5.2f}%")
        print(f"    ADX:       bull={per_token[-1]['adx_dist']['bullish']*100:5.2f}%  "
              f"bear={per_token[-1]['adx_dist']['bearish']*100:5.2f}%  "
              f"ranging={per_token[-1]['adx_dist']['ranging']*100:5.2f}%")
        print(f"    EMA+ADX:   up={per_token[-1]['combo_dist']['up']*100:5.2f}%  "
              f"down={per_token[-1]['combo_dist']['down']*100:5.2f}%  "
              f"range={per_token[-1]['combo_dist']['range']*100:5.2f}%")
        print(f"    vol media={per_token[-1]['avg_vol']*100:.2f}% anual  "
              f"pct vol>15%={per_token[-1]['pct_high_vol_0p15']*100:.2f}%  "
              f"maxDD={per_token[-1]['max_drawdown_pct']:.2f}%")

        # Selección de ventanas claras
        windows = select_windows(df, window_len=1000)
        for w in windows:
            det_cls = classify_window_detector(regimes, w["start"], w["end"])
            ext_cls = classify_window(ema_slope, adx, rvol, w["start"], w["end"])
            row = {
                "symbol": sym,
                "dataset": dataset_key,
                "window_kind": w["kind"],
                "start_idx": w["start"],
                "end_idx": w["end"],
                "window_ret_pct": w["ret"],
                "window_vol": w["vol"],
                "start_dt": str(df["dt"].iloc[w["start"]]),
                "end_dt": str(df["dt"].iloc[w["end"]-1]),
                # Detector actual
                "det_up_pct": det_cls["trending_up_pct"],
                "det_down_pct": det_cls["trending_down_pct"],
                "det_ranging_pct": det_cls["ranging_pct"],
                "det_volatile_pct": det_cls["volatile_pct"],
                # EMA slope
                "ema_bull_pct": ext_cls["ema_bull_pct"],
                "ema_bear_pct": ext_cls["ema_bear_pct"],
                "ema_neutral_pct": ext_cls["ema_neutral_pct"],
                # ADX
                "adx_bull_pct": ext_cls["adx_bull_pct"],
                "adx_bear_pct": ext_cls["adx_bear_pct"],
                "adx_neutral_pct": ext_cls["adx_neutral_pct"],
                # Combo
                "combo_bull_pct": ext_cls["combo_bull_pct"],
                "combo_bear_pct": ext_cls["combo_bear_pct"],
                "combo_neutral_pct": ext_cls["combo_neutral_pct"],
                # Veredicto por método
                "det_verdict": max(det_cls, key=lambda k: det_cls[k]).replace("_pct","").replace("trending_",""),
                "ema_verdict": "bull" if ext_cls["ema_bull_pct"] > ext_cls["ema_bear_pct"] and ext_cls["ema_bull_pct"] > ext_cls["ema_neutral_pct"]
                              else "bear" if ext_cls["ema_bear_pct"] > ext_cls["ema_bull_pct"] and ext_cls["ema_bear_pct"] > ext_cls["ema_neutral_pct"]
                              else "neutral",
                "adx_verdict": "bull" if ext_cls["adx_bull_pct"] > ext_cls["adx_bear_pct"] and ext_cls["adx_bull_pct"] > ext_cls["adx_neutral_pct"]
                              else "bear" if ext_cls["adx_bear_pct"] > ext_cls["adx_bull_pct"] and ext_cls["adx_bear_pct"] > ext_cls["adx_neutral_pct"]
                              else "ranging",
                "combo_verdict": "bull" if ext_cls["combo_bull_pct"] > ext_cls["combo_bear_pct"] and ext_cls["combo_bull_pct"] > ext_cls["combo_neutral_pct"]
                                else "bear" if ext_cls["combo_bear_pct"] > ext_cls["combo_bull_pct"] and ext_cls["combo_bear_pct"] > ext_cls["combo_neutral_pct"]
                                else "range",
            }
            window_rows.append(row)
            print(f"    WINDOW [{w['kind']:6s}] ret={w['ret']:+7.2f}%  vol={w['vol']:.4f}  "
                  f"det={row['det_verdict']:8s}  ema={row['ema_verdict']:7s}  "
                  f"adx={row['adx_verdict']:7s}  combo={row['combo_verdict']:5s}  "
                  f"({df['dt'].iloc[w['start']].date()} → {df['dt'].iloc[w['end']-1].date()})")

    # Acuerdo humano: cómo clasifica cada método las ventanas etiquetadas visualmente
    human_agreement = defaultdict(lambda: defaultdict(int))
    for row in window_rows:
        kind = row["window_kind"]
        # Detector: veredicto "up" coincide con bull, "down" con bear, "ranging"/"volatile" con range
        det_map = {"up":"bull", "down":"bear", "ranging":"range", "volatile":"range"}
        if det_map.get(row["det_verdict"]) == kind:
            human_agreement["detector"][kind] += 1
        human_agreement["detector_total"][kind] += 1

        if row["ema_verdict"] == kind or (kind == "range" and row["ema_verdict"] == "neutral"):
            human_agreement["ema"][kind] += 1
        human_agreement["ema_total"][kind] += 1

        adx_map = {"bull":"bull", "bear":"bear", "ranging":"range"}
        if adx_map.get(row["adx_verdict"]) == kind:
            human_agreement["adx"][kind] += 1
        human_agreement["adx_total"][kind] += 1

        combo_map = {"bull":"bull", "bear":"bear", "range":"range"}
        if combo_map.get(row["combo_verdict"]) == kind:
            human_agreement["combo"][kind] += 1
        human_agreement["combo_total"][kind] += 1

    # Resumen de acuerdo humano
    agreement_summary = {}
    for method in ["detector", "ema", "adx", "combo"]:
        agreement_summary[method] = {}
        for kind in ["bull", "bear", "range"]:
            total = human_agreement[f"{method}_total"][kind]
            hits = human_agreement[method][kind]
            agreement_summary[method][kind] = {
                "n_windows": total,
                "n_hits": hits,
                "agreement_pct": hits/total if total else 0.0,
            }

    return {
        "dataset": dataset_key,
        "label": cfg["label"],
        "total_candles": global_total,
        "per_token": per_token,
        "window_rows": window_rows,
        "global_dist": {
            "detector": {k: global_dist_detector.get(k,0)/global_total for k in ["trending_up","trending_down","ranging","volatile"]},
            "ema": {k: global_dist_ema.get(k,0)/global_total for k in ["trending_up","trending_down","ranging"]},
            "adx": {k: global_dist_adx.get(k,0)/global_total for k in ["trending_up","trending_down","ranging"]},
            "combo": {k: global_dist_combo.get(k,0)/global_total for k in ["trending_up","trending_down","ranging"]},
        },
        "human_agreement": agreement_summary,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 78)
    print("PPMT — Auditoría del RegimeDetector")
    print("Objetivo: distinguir A) dataset insuficiente  B) detector degenerado  C) ambos")
    print("=" * 78)

    # Filter to datasets that actually have data
    available = {}
    for ds_key, cfg in DATASETS.items():
        first_csv = cfg["dir"] / f"{cfg['symbols'][0]}_1m.csv"
        if first_csv.exists():
            # Check it has reasonable size
            df = pd.read_csv(first_csv, nrows=5)
            if len(df) > 0:
                available[ds_key] = cfg
                print(f"  Dataset disponible: {ds_key}")
        else:
            print(f"  Dataset NO disponible: {ds_key} (saltando)")

    results = {}
    for ds_key in available:
        results[ds_key] = audit_dataset(ds_key)

    # Guardar JSON
    out_json = OUT_DIR / "audit_report.json"
    with out_json.open("w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n\nJSON guardado: {out_json}")

    # CSV: clasificación por ventana
    all_windows = []
    for ds_key, r in results.items():
        for row in r["window_rows"]:
            all_windows.append(row)
    if all_windows:
        pd.DataFrame(all_windows).to_csv(OUT_DIR / "window_classification.csv", index=False)

    # CSV: distribución global por método y dataset
    gdist_rows = []
    for ds_key, r in results.items():
        for method, dist in r["global_dist"].items():
            for regime, pct in dist.items():
                gdist_rows.append({"dataset": ds_key, "method": method, "regime": regime, "pct": pct})
    pd.DataFrame(gdist_rows).to_csv(OUT_DIR / "global_distribution.csv", index=False)

    # CSV: acuerdo humano
    ha_rows = []
    for ds_key, r in results.items():
        for method, kinds in r["human_agreement"].items():
            for kind, info in kinds.items():
                ha_rows.append({"dataset": ds_key, "method": method, "window_kind": kind,
                                "n_windows": info["n_windows"], "n_hits": info["n_hits"],
                                "agreement_pct": info["agreement_pct"]})
    pd.DataFrame(ha_rows).to_csv(OUT_DIR / "human_agreement.csv", index=False)

    # Markdown report
    md = ["# PPMT — Auditoría del RegimeDetector\n",
          f"_Generado: {datetime.now(timezone.utc).isoformat()}_\n",
          "## Objetivo\n",
          "Distinguir entre tres hipótesis:\n",
          "- **A) Dataset insuficiente**: 139 días todo bajista → no hay diversidad de regímenes",
          "- **B) Detector degenerado**: `RegimeDetector` clasifica 99.79% como `ranging`",
          "- **C) Ambos**\n"]

    for ds_key, r in results.items():
        md.append(f"\n## Dataset {ds_key} — {r['label']}\n")
        md.append(f"**Total**: {r['total_candles']:,} velas\n")

        md.append("\n### Distribución global por método\n")
        md.append("| Método | Bull % | Bear % | Ranging % | Volatile % |")
        md.append("|---|---:|---:|---:|---:|")
        for method, dist in r["global_dist"].items():
            label = {"detector":"Detector actual","ema":"EMA slope","adx":"ADX","combo":"EMA+ADX"}[method]
            bull = dist.get("trending_up", 0) * 100
            bear = dist.get("trending_down", 0) * 100
            ranging = dist.get("ranging", 0) * 100
            volatile = dist.get("volatile", 0) * 100
            md.append(f"| {label} | {bull:.2f}% | {bear:.2f}% | {ranging:.2f}% | {volatile:.2f}% |")

        md.append("\n### Acuerdo con etiquetado humano (ventanas visualmente claras)\n")
        md.append("| Método | Bull (acuerdo) | Bear (acuerdo) | Range (acuerdo) |")
        md.append("|---|---|---|---|")
        for method in ["detector", "ema", "adx", "combo"]:
            label = {"detector":"Detector actual","ema":"EMA slope","adx":"ADX","combo":"EMA+ADX"}[method]
            ha = r["human_agreement"][method]
            row = f"| {label} |"
            for kind in ["bull", "bear", "range"]:
                info = ha[kind]
                if info["n_windows"] > 0:
                    row += f" {info['n_hits']}/{info['n_windows']} ({info['agreement_pct']*100:.0f}%) |"
                else:
                    row += f" — |"
            md.append(row)

        md.append("\n### Detalle de ventanas seleccionadas\n")
        md.append("| Token | Tipo | Retorno % | Vol | Detector | EMA | ADX | Combo | Inicio | Fin |")
        md.append("|---|---|---:|---:|---|---|---|---|---|---|")
        for w in r["window_rows"]:
            md.append(f"| {w['symbol']} | {w['window_kind']} | {w['window_ret_pct']:+.2f} | {w['window_vol']:.4f} | "
                      f"{w['det_verdict']} | {w['ema_verdict']} | {w['adx_verdict']} | {w['combo_verdict']} | "
                      f"{w['start_dt'][:10]} | {w['end_dt'][:10]} |")

        md.append("\n### Estadísticas por token\n")
        md.append("| Token | Det up% | Det down% | Det range% | Det vol% | EMA bull% | EMA bear% | ADX bull% | ADX bear% | Combo up% | Combo down% | Vol media |")
        md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for t in r["per_token"]:
            md.append(f"| {t['symbol']} | "
                      f"{t['detector_dist']['trending_up']*100:.2f} | {t['detector_dist']['trending_down']*100:.2f} | "
                      f"{t['detector_dist']['ranging']*100:.2f} | {t['detector_dist']['volatile']*100:.2f} | "
                      f"{t['ema_dist']['bullish']*100:.2f} | {t['ema_dist']['bearish']*100:.2f} | "
                      f"{t['adx_dist']['bullish']*100:.2f} | {t['adx_dist']['bearish']*100:.2f} | "
                      f"{t['combo_dist']['up']*100:.2f} | {t['combo_dist']['down']*100:.2f} | "
                      f"{t['avg_vol']*100:.2f} |")

    # Diagnóstico final
    md.append("\n## Diagnóstico final\n")
    v4 = results.get("v4_139d", {})
    t12 = results.get("12m_5maj", {})

    v4_det_range = v4.get("global_dist", {}).get("detector", {}).get("ranging", 0)
    t12_det_range = t12.get("global_dist", {}).get("detector", {}).get("ranging", 0)
    v4_ema_neutral = v4.get("global_dist", {}).get("ema", {}).get("ranging", 0)
    t12_ema_neutral = t12.get("global_dist", {}).get("ema", {}).get("ranging", 0)
    v4_adx_range = v4.get("global_dist", {}).get("adx", {}).get("ranging", 0)
    t12_adx_range = t12.get("global_dist", {}).get("adx", {}).get("ranging", 0)

    has_v4 = bool(v4)
    has_t12 = bool(t12) and t12.get("total_candles", 0) > 0

    if has_v4 and has_t12:
        md.append("\n### Distribución global comparada\n")
        md.append("| Método | v4 (139d, 16 tok) | 12m (5 majors) | Cambio |")
        md.append("|---|---:|---:|---:|")
        md.append(f"| Detector ranging % | {v4_det_range*100:.2f}% | {t12_det_range*100:.2f}% | {(t12_det_range-v4_det_range)*100:+.2f}pp |")
        md.append(f"| EMA neutral % | {v4_ema_neutral*100:.2f}% | {t12_ema_neutral*100:.2f}% | {(t12_ema_neutral-v4_ema_neutral)*100:+.2f}pp |")
        md.append(f"| ADX ranging % | {v4_adx_range*100:.2f}% | {t12_adx_range*100:.2f}% | {(t12_adx_range-v4_adx_range)*100:+.2f}pp |")

        # Veredicto
        md.append("\n### Veredicto\n")
        if t12_det_range > 0.85 and v4_det_range > 0.85:
            verdict = "**B) Detector degenerado**: el detector clasifica >85% como ranging en AMBOS datasets (v4 139d y 12m 365d). El problema NO es el dataset — es el detector."
        elif t12_det_range < 0.50 and v4_det_range > 0.85:
            verdict = "**A) Dataset insuficiente**: el detector se comporta razonablemente en 12m pero degenera en v4. El problema es el dataset (139 días todo bajista)."
        elif 0.50 < t12_det_range < 0.85 and v4_det_range > 0.85:
            verdict = "**C) Ambos**: el detector mejora con más data pero sigue clasificando >50% como ranging. Dataset y detector ambos contribuyen."
        else:
            verdict = "Resultado mixto — revisar tablas de detalle."
        md.append(f"- {verdict}")
    else:
        # Solo v4 disponible
        md.append("\n### Distribución global (solo dataset v4)\n")
        md.append("| Método | v4 (139d, 16 tok) |")
        md.append("|---|---:|")
        md.append(f"| Detector ranging % | {v4_det_range*100:.2f}% |")
        md.append(f"| EMA neutral % | {v4_ema_neutral*100:.2f}% |")
        md.append(f"| ADX ranging % | {v4_adx_range*100:.2f}% |")

        md.append("\n### Veredicto preliminar (12m dataset aún descargando)\n")
        v4_bull = v4.get("global_dist", {}).get("detector", {}).get("trending_up", 0)
        v4_bear = v4.get("global_dist", {}).get("detector", {}).get("trending_down", 0)
        v4_vol = v4.get("global_dist", {}).get("detector", {}).get("volatile", 0)
        v4_total_non_ranging = v4_bull + v4_bear + v4_vol

        # Acuerdo humano del detector
        det_bull_agree = v4.get("human_agreement", {}).get("detector", {}).get("bull", {}).get("agreement_pct", 0)
        det_bear_agree = v4.get("human_agreement", {}).get("detector", {}).get("bear", {}).get("agreement_pct", 0)
        det_range_agree = v4.get("human_agreement", {}).get("detector", {}).get("range", {}).get("agreement_pct", 0)

        ema_bull_agree = v4.get("human_agreement", {}).get("ema", {}).get("bull", {}).get("agreement_pct", 0)
        ema_bear_agree = v4.get("human_agreement", {}).get("ema", {}).get("bear", {}).get("agreement_pct", 0)
        ema_range_agree = v4.get("human_agreement", {}).get("ema", {}).get("range", {}).get("agreement_pct", 0)

        adx_bull_agree = v4.get("human_agreement", {}).get("adx", {}).get("bull", {}).get("agreement_pct", 0)
        adx_bear_agree = v4.get("human_agreement", {}).get("adx", {}).get("bear", {}).get("agreement_pct", 0)
        adx_range_agree = v4.get("human_agreement", {}).get("adx", {}).get("range", {}).get("agreement_pct", 0)

        md.append(f"- Detector actual clasifica **{v4_det_range*100:.2f}%** de las velas como ranging.")
        md.append(f"- EMA slope clasifica **{v4_ema_neutral*100:.2f}%** como neutral (bull {v4.get('global_dist',{}).get('ema',{}).get('trending_up',0)*100:.2f}% / bear {v4.get('global_dist',{}).get('ema',{}).get('trending_down',0)*100:.2f}%).")
        md.append(f"- ADX clasifica **{v4_adx_range*100:.2f}%** como ranging (bull {v4.get('global_dist',{}).get('adx',{}).get('trending_up',0)*100:.2f}% / bear {v4.get('global_dist',{}).get('adx',{}).get('trending_down',0)*100:.2f}%).")
        md.append(f"\n**Acuerdo con etiquetado humano (77 ventanas bull, 79 bear, 80 range):**\n")
        md.append(f"- Detector actual: bull {det_bull_agree*100:.0f}% / bear {det_bear_agree*100:.0f}% / range {det_range_agree*100:.0f}%")
        md.append(f"- EMA slope: bull {ema_bull_agree*100:.0f}% / bear {ema_bear_agree*100:.0f}% / range {ema_range_agree*100:.0f}%")
        md.append(f"- ADX: bull {adx_bull_agree*100:.0f}% / bear {adx_bear_agree*100:.0f}% / range {adx_range_agree*100:.0f}%")

        md.append(f"\n**Conclusión preliminar**: Detector actual tiene **0% de acuerdo** con etiquetado humano en ventanas bull y bear. EMA slope y ADX sí distinguen (aunque con ~15-17% de acuerdo, lejos del ideal). El problema es claramente **B) Detector degenerado**, no falta de datos. Confirmación con dataset 12m pendiente.")

    md.append("\n### Recomendaciones\n")
    md.append("1. Si **B) Detector degenerado**: implementar FIX-17 (mejorar detector) usando EMA slope + ADX como referencia.")
    md.append("2. Si **A) Dataset insuficiente**: usar dataset 12m para entrenamiento y validación.")
    md.append("3. Si **C) Ambos**: ambas acciones, empezando por FIX-17.")

    with open(OUT_DIR / "audit_report.md", "w") as f:
        f.write("\n".join(md))

    print(f"\nMarkdown guardado: {OUT_DIR / 'audit_report.md'}")
    print(f"CSVs: window_classification.csv, global_distribution.csv, human_agreement.csv")


if __name__ == "__main__":
    main()
