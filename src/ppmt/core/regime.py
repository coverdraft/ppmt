"""
Regime Detection Module

Detects market regimes (trending_up, trending_down, ranging, volatile)
using volatility, trend strength, and mean-reversion metrics.
"""

import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class RegimeInfo:
    """Detailed regime information for a point in time."""
    regime: str
    volatility: float
    trend_strength: float
    hurst_exponent: float
    confidence: float


class RegimeDetector:
    """
    Multi-feature regime detector.

    Uses:
    - Volatility (annualized std of returns)
    - Trend strength (linear regression R²)
    - Hurst exponent (mean-reversion vs trending)
    """

    REGIMES = ["trending_up", "trending_down", "ranging", "volatile"]

    def __init__(self, lookback: int = 50, vol_threshold: float = 0.6,
                 trend_threshold: float = 0.005):
        self.lookback = lookback
        # v0.11.0: Auto-calibrate vol_threshold for crypto if left at default.
        # The default 0.6 (60% annualized vol) was designed for stocks.
        # Crypto has much higher base volatility (BTC ~11%, alts ~30-100%),
        # so 0.6 was NEVER triggered. We now use 0.15 (15% annualized) as the
        # default for crypto-appropriate regime detection. At 0.15:
        #   - BTC normal: ~8-15% vol → ranging (below threshold)
        #   - BTC volatile: >15% vol → volatile regime
        #   - Alt coins: >20-30% vol → volatile regime
        self.vol_threshold = vol_threshold if vol_threshold != 0.6 else 0.15
        # v0.11.0: Auto-calibrate trend_threshold for crypto.
        # The default 0.005 (0.5% per candle relative slope) was too high for
        # high-priced assets like BTC. At $60k, rel_slope > 0.005 requires a
        # $300/candle slope (25% move over 50 candles). Lowering to 0.001
        # (0.1% per candle) makes trending detection work for crypto:
        #   - 0.001 * $60k = $60/candle → 5% move over 50 candles → trending
        self.trend_threshold = trend_threshold if trend_threshold != 0.005 else 0.001

    def compute_hurst(self, prices: np.ndarray, max_lag: int = 20) -> float:
        """Compute Hurst exponent using R/S analysis."""
        if len(prices) < max_lag * 2:
            return 0.5
        returns = np.diff(np.log(prices))
        lags = range(2, min(max_lag, len(returns) // 2))
        rs_values = []
        for lag in lags:
            segments = len(returns) // lag
            rs_seg = []
            for i in range(segments):
                seg = returns[i * lag:(i + 1) * lag]
                mean = seg.mean()
                cumdev = np.cumsum(seg - mean)
                r = cumdev.max() - cumdev.min()
                s = seg.std()
                if s > 1e-12:
                    rs_seg.append(r / s)
            if rs_seg:
                rs_values.append((np.log(lag), np.log(np.mean(rs_seg))))
        if len(rs_values) < 2:
            return 0.5
        x = np.array([v[0] for v in rs_values])
        y = np.array([v[1] for v in rs_values])
        slope = np.polyfit(x, y, 1)[0]
        return float(np.clip(slope, 0.0, 1.0))

    def detect(self, prices: np.ndarray) -> str:
        """Detect current regime."""
        if len(prices) < self.lookback:
            return "ranging"
        return self.detect_detailed(prices).regime

    def detect_detailed(self, prices: np.ndarray) -> RegimeInfo:
        """Detect regime with full details."""
        window = prices[-self.lookback:]
        returns = np.diff(window) / window[:-1]

        # Volatility (annualized)
        vol = float(np.std(returns) * np.sqrt(365))  # v0.19.1: crypto trades 365 days

        # Trend: linear regression
        x = np.arange(len(window))
        slope, intercept = np.polyfit(x, window, 1)
        rel_slope = slope / np.mean(window)
        ss_res = np.sum((window - (slope * x + intercept)) ** 2)
        ss_tot = np.sum((window - window.mean()) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        trend_strength = float(r_squared)

        # Hurst
        hurst = self.compute_hurst(prices[-100:] if len(prices) >= 100 else prices)

        # Classify
        if vol > self.vol_threshold:
            regime = "volatile"
            confidence = min(1.0, vol / self.vol_threshold)
        elif rel_slope > self.trend_threshold and hurst > 0.55:
            regime = "trending_up"
            confidence = min(1.0, rel_slope / self.trend_threshold * 0.5 + (hurst - 0.5) * 2)
        elif rel_slope < -self.trend_threshold and hurst > 0.55:
            regime = "trending_down"
            confidence = min(1.0, abs(rel_slope) / self.trend_threshold * 0.5 + (hurst - 0.5) * 2)
        else:
            regime = "ranging"
            confidence = 1.0 - trend_strength

        return RegimeInfo(
            regime=regime,
            volatility=vol,
            trend_strength=trend_strength,
            hurst_exponent=hurst,
            confidence=float(np.clip(confidence, 0, 1)),
        )

    def detect_series(self, prices: np.ndarray) -> List[str]:
        """Regime labels for each point."""
        regimes = []
        for i in range(len(prices)):
            if i < self.lookback:
                regimes.append("ranging")
            else:
                regimes.append(self.detect(prices[:i + 1]))
        return regimes
