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

    # ---------------------------------------------------------------- #
    # Simple regime detection (v0.38.8)
    # ---------------------------------------------------------------- #
    # Used by PPMT engine during trie build (ppmt.py:_detect_simple_regime
    # was a static method with hardcoded 0.08 vol and 0.02 move cutoffs).
    # Now unified: detect_simple takes a window DataFrame and uses
    # RegimeThresholds.simple_vol_cutoff / simple_move_cutoff (same values,
    # 0.08 and 0.02, preserved verbatim). This keeps the trie-tagging
    # logic lightweight while sharing the threshold source-of-truth with
    # the full RegimeDetector.
    # ---------------------------------------------------------------- #

    def detect_simple(self, window_df, timeframe: str = None) -> str:
        """
        Lightweight regime detection from a window of OHLCV data.

        Uses price direction and intra-window volatility to classify:
        - trending_up:   move > simple_move_cutoff
        - trending_down: move < -simple_move_cutoff
        - volatile:      range/entry > simple_vol_cutoff
        - ranging:       none of the above

        Cutoffs come from RegimeThresholds (default 0.08 / 0.02, matching
        the historical hardcoded values in ppmt.py v0.38.7).

        v0.41.0 (FASE 3, Tarea 3.2): Accepts optional `timeframe` parameter.
        When provided, uses RegimeThresholds.for_timeframe() which applies
        calibrated cutoffs for each timeframe (e.g. tighter cutoffs for 1m/5m
        candles so they don't all classify as 'ranging').

        Args:
            window_df: pd.DataFrame with columns 'close', 'high', 'low'
                       (any length >= 2; the caller picks the window size).
            timeframe: Optional candle interval string (e.g. '1m', '5m', '1h').
                       When None, uses RegimeThresholds.default().

        Returns:
            One of: 'trending_up', 'trending_down', 'volatile', 'ranging'.
        """
        if len(window_df) < 2:
            return "ranging"

        # Lazy import to avoid hard coupling at module load time.
        from ppmt.core.thresholds import RegimeThresholds
        if timeframe:
            rt = RegimeThresholds.for_timeframe(timeframe)
        else:
            rt = RegimeThresholds.default()

        entry = window_df["close"].iloc[0]
        exit_price = window_df["close"].iloc[-1]
        high = window_df["high"].max()
        low = window_df["low"].min()

        # Direction
        move_pct = (exit_price - entry) / entry if entry > 0 else 0.0

        # Volatility: range as % of entry
        volatility = (high - low) / entry if entry > 0 else 0.0

        # Classify (matches ppmt.py v0.38.7 logic exactly, same cutoff order)
        if volatility > rt.simple_vol_cutoff:
            return "volatile"
        elif move_pct > rt.simple_move_cutoff:
            return "trending_up"
        elif move_pct < -rt.simple_move_cutoff:
            return "trending_down"
        else:
            return "ranging"
