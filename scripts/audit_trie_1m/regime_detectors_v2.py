"""
5 detectores de régimen standalone para comparación.

Cada detector expone la misma API:
    labels = detector(prices_close, highs, lows) -> np.ndarray[str]

Los labels son siempre: "trending_up", "trending_down", "ranging", "volatile"

Detectores:
  1. ADX (período 14, umbral 25) → ranging/trending_up/trending_down vía +DI/-DI
  2. EMA slope (EMA21 vs EMA55, slope 5 velas, umbral 0.15%) → up/down/range
  3. Bollinger Band Width (período 20, σ=2, umbral relativo) → volatile vs range
  4. ADX + EMA (ADX>=25 + DI confirmado por EMA slope) → up/down/range
  5. ADX + EMA + Bollinger (combo completo con volatile override)

Calibración para 1m crypto basada en auditoría previa:
  - ADX 25 (Wilder estándar)
  - EMA slope 0.15% (5 velas) → 3% move en 100 velas
  - BB width umbral dinámico: 1.5× mediana móvil (500 velas)
"""

import numpy as np
import pandas as pd
from typing import Tuple


# ============================================================ #
# Helpers
# ============================================================ #

def _ema(values: np.ndarray, span: int) -> np.ndarray:
    s = pd.Series(values)
    return s.ewm(span=span, adjust=False).mean().values


def _rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(values)
    return s.rolling(window).std().fillna(0.0).values


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(values)
    return s.rolling(window).mean().fillna(method="bfill").values


def _rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(values)
    return s.rolling(window).median().fillna(method="bfill").values


def compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    ADX, +DI, -DI vectorizados (Wilder's method).
    Retorna 3 arrays del mismo length que el input.
    """
    n = len(closes)
    adx = np.zeros(n)
    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    if n < 2 * period:
        return adx, plus_di, minus_di

    # True Range
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # Directional Movement
    up_move = np.zeros(n)
    down_move = np.zeros(n)
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        if up > down and up > 0:
            up_move[i] = up
        if down > up and down > 0:
            down_move[i] = down

    # Wilder smoothing
    atr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    atr[period] = np.sum(tr[1:period + 1])
    plus_dm[period] = np.sum(up_move[1:period + 1])
    minus_dm[period] = np.sum(down_move[1:period + 1])
    for i in range(period + 1, n):
        atr[i] = atr[i - 1] - (atr[i - 1] / period) + tr[i]
        plus_dm[i] = plus_dm[i - 1] - (plus_dm[i - 1] / period) + up_move[i]
        minus_dm[i] = minus_dm[i] - (minus_dm[i - 1] / period) + down_move[i]

    # +DI / -DI
    for i in range(period, n):
        if atr[i] > 0:
            plus_di[i] = 100 * plus_dm[i] / atr[i]
            minus_di[i] = 100 * minus_dm[i] / atr[i]

    # DX → ADX
    dx = np.zeros(n)
    for i in range(period, n):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

    first_adx_idx = 2 * period
    if first_adx_idx < n:
        adx[first_adx_idx] = np.mean(dx[period:first_adx_idx + 1])
        for i in range(first_adx_idx + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx, plus_di, minus_di


def compute_bollinger_width(closes: np.ndarray, period: int = 20,
                             num_std: float = 2.0) -> np.ndarray:
    """
    Bollinger Band Width normalizado = (upper - lower) / mid.
    Devuelve array del mismo length que closes.
    """
    s = pd.Series(closes)
    mid = s.rolling(period).mean()
    std = s.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid
    return width.fillna(0.0).values


# ============================================================ #
# Detector 1: ADX
# ============================================================ #

def detector_adx(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                 adx_threshold: float = 25.0) -> np.ndarray:
    """
    ADX solo. Si ADX >= umbral, trending_up/down según +DI vs -DI.
    Si no, ranging. Nunca clasifica volatile.
    """
    n = len(closes)
    labels = np.empty(n, dtype="<U14")
    labels[:] = "ranging"

    adx, plus_di, minus_di = compute_adx(highs, lows, closes)
    for i in range(n):
        if adx[i] >= adx_threshold:
            if plus_di[i] > minus_di[i]:
                labels[i] = "trending_up"
            else:
                labels[i] = "trending_down"
    return labels


# ============================================================ #
# Detector 2: EMA slope
# ============================================================ #

def detector_ema_slope(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                       ema_fast: int = 21, ema_slow: int = 55,
                       slope_window: int = 5, slope_threshold: float = 0.0015
                       ) -> np.ndarray:
    """
    EMA slope. Si EMA21 > EMA55 y slope positivo > umbral → up.
    Si EMA21 < EMA55 y slope negativo < -umbral → down. Sino range.
    Nunca clasifica volatile.
    """
    n = len(closes)
    labels = np.empty(n, dtype="<U14")
    labels[:] = "ranging"

    ema_f = _ema(closes, ema_fast)
    ema_s = _ema(closes, ema_slow)

    for i in range(n):
        if i < slope_window:
            continue
        slope_f = (ema_f[i] - ema_f[i - slope_window]) / max(ema_f[i - slope_window], 1e-9)
        if slope_f > slope_threshold and ema_f[i] > ema_s[i]:
            labels[i] = "trending_up"
        elif slope_f < -slope_threshold and ema_f[i] < ema_s[i]:
            labels[i] = "trending_down"
    return labels


# ============================================================ #
# Detector 3: Bollinger Band Width
# ============================================================ #

def detector_bollinger(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                       bb_period: int = 20, bb_std: float = 2.0,
                       vol_window: int = 500, vol_mult: float = 1.5
                       ) -> np.ndarray:
    """
    Bollinger Band Width. Si width > 1.5× mediana móvil → volatile.
    Dentro de volatile, distingue up/down según signo del retorno acumulado
    en la ventana. Si no es volatile, ranging (no distingue trending).
    """
    n = len(closes)
    labels = np.empty(n, dtype="<U14")
    labels[:] = "ranging"

    width = compute_bollinger_width(closes, bb_period, bb_std)
    median_width = _rolling_median(width, vol_window)

    for i in range(n):
        if width[i] > vol_mult * median_width[i] and median_width[i] > 0:
            # Direction via cumulative return over bb_period
            if i >= bb_period:
                ret = (closes[i] - closes[i - bb_period]) / closes[i - bb_period]
                if ret > 0.005:
                    labels[i] = "trending_up"
                elif ret < -0.005:
                    labels[i] = "trending_down"
                else:
                    labels[i] = "volatile"
            else:
                labels[i] = "volatile"
    return labels


# ============================================================ #
# Detector 4: ADX + EMA
# ============================================================ #

def detector_adx_ema(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                     adx_threshold: float = 25.0,
                     ema_fast: int = 21, ema_slow: int = 55,
                     slope_window: int = 5, slope_threshold: float = 0.0015
                     ) -> np.ndarray:
    """
    ADX + EMA. Requiere ADX >= umbral Y EMA slope confirmatorio.
    Si ADX fuerte pero EMA no confirma → ranging (no trend).
    Si EMA confirma pero ADX débil → ranging.
    Nunca clasifica volatile.
    """
    n = len(closes)
    labels = np.empty(n, dtype="<U14")
    labels[:] = "ranging"

    adx, plus_di, minus_di = compute_adx(highs, lows, closes)
    ema_f = _ema(closes, ema_fast)
    ema_s = _ema(closes, ema_slow)

    for i in range(n):
        if i < slope_window:
            continue
        slope_f = (ema_f[i] - ema_f[i - slope_window]) / max(ema_f[i - slope_window], 1e-9)

        adx_ok = adx[i] >= adx_threshold
        ema_up = slope_f > slope_threshold and ema_f[i] > ema_s[i]
        ema_down = slope_f < -slope_threshold and ema_f[i] < ema_s[i]
        di_up = plus_di[i] > minus_di[i]

        if adx_ok and ema_up and di_up:
            labels[i] = "trending_up"
        elif adx_ok and ema_down and not di_up:
            labels[i] = "trending_down"
    return labels


# ============================================================ #
# Detector 5: ADX + EMA + Bollinger Width
# ============================================================ #

def detector_adx_ema_bb(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                        adx_threshold: float = 25.0,
                        ema_fast: int = 21, ema_slow: int = 55,
                        slope_window: int = 5, slope_threshold: float = 0.0015,
                        bb_period: int = 20, bb_std: float = 2.0,
                        vol_window: int = 500, vol_mult: float = 1.5
                        ) -> np.ndarray:
    """
    Combo completo. Prioridad:
      1. Volatile (Bollinger width > 1.5× mediana)
      2. Trending up/down (ADX + EMA + DI alineados)
      3. Ranging (default)
    """
    n = len(closes)
    labels = np.empty(n, dtype="<U14")
    labels[:] = "ranging"

    adx, plus_di, minus_di = compute_adx(highs, lows, closes)
    ema_f = _ema(closes, ema_fast)
    ema_s = _ema(closes, ema_slow)
    width = compute_bollinger_width(closes, bb_period, bb_std)
    median_width = _rolling_median(width, vol_window)

    for i in range(n):
        # 1. Volatile check
        if width[i] > vol_mult * median_width[i] and median_width[i] > 0:
            labels[i] = "volatile"
            continue

        if i < slope_window:
            continue

        # 2. Trending check
        slope_f = (ema_f[i] - ema_f[i - slope_window]) / max(ema_f[i - slope_window], 1e-9)
        adx_ok = adx[i] >= adx_threshold
        ema_up = slope_f > slope_threshold and ema_f[i] > ema_s[i]
        ema_down = slope_f < -slope_threshold and ema_f[i] < ema_s[i]
        di_up = plus_di[i] > minus_di[i]

        if adx_ok and ema_up and di_up:
            labels[i] = "trending_up"
        elif adx_ok and ema_down and not di_up:
            labels[i] = "trending_down"
        # else: ranging (default)
    return labels


# ============================================================ #
# Registry
# ============================================================ #

DETECTORS = {
    "adx":             detector_adx,
    "ema_slope":       detector_ema_slope,
    "bollinger":       detector_bollinger,
    "adx_ema":         detector_adx_ema,
    "adx_ema_bb":      detector_adx_ema_bb,
}
