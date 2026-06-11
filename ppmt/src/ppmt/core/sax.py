"""
SAX (Symbolic Aggregate approXimation) Encoder

Converts continuous OHLCV price data into discrete SAX symbols,
enabling Trie storage and O(k) pattern matching.

Pipeline:
  1. Normalize price data (z-score per window)
  2. PAA (Piecewise Aggregate Approximation) — reduce dimensionality
  3. Discretize into SAX symbols using breakpoints

The SAX alphabet size controls granularity:
  - 4 symbols: coarse (strong directional only)
  - 8 symbols: balanced (default)
  - 16 symbols: fine-grained (more noise sensitivity)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from scipy.stats import norm

# SAX breakpoints for different alphabet sizes
# These are the z-score boundaries that divide a normal distribution
# into equal-probability regions
SAX_BREAKPOINTS: dict[int, np.ndarray] = {
    3: np.array([-0.43, 0.43]),
    4: np.array([-0.67, 0.0, 0.67]),
    5: np.array([-0.84, -0.25, 0.25, 0.84]),
    6: np.array([-0.97, -0.43, 0.0, 0.43, 0.97]),
    7: np.array([-1.07, -0.57, -0.18, 0.18, 0.57, 1.07]),
    8: np.array([-1.15, -0.67, -0.32, 0.0, 0.32, 0.67, 1.15]),
    10: np.array([-1.28, -0.84, -0.52, -0.25, 0.0, 0.25, 0.52, 0.84, 1.28]),
    12: np.array([-1.38, -1.00, -0.67, -0.43, -0.21, 0.0, 0.21, 0.43, 0.67, 1.00, 1.38]),
    16: np.array([-1.53, -1.15, -0.89, -0.67, -0.49, -0.32, -0.16, 0.0,
                   0.16, 0.32, 0.49, 0.67, 0.89, 1.15, 1.53]),
}

SAX_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


@dataclass
class SAXConfig:
    """Configuration for SAX encoding."""
    alphabet_size: int = 8
    window_size: int = 10  # PAA window: candles per SAX block
    strategy: Literal["ohlcv", "close", "typical_price"] = "ohlcv"


class SAXEncoder:
    """
    SAX Symbolic Encoder for OHLCV data.

    Converts continuous price series into discrete symbols suitable
    for Trie storage and O(k) pattern matching.

    Usage:
        encoder = SAXEncoder(alphabet_size=8, window_size=10)
        symbols = encoder.encode(df)  # df has OHLCV columns
        # symbols = ['a', 'd', 'b', 'h', 'e', ...]
    """

    def __init__(
        self,
        alphabet_size: int = 8,
        window_size: int = 10,
        strategy: str = "ohlcv",
    ):
        if alphabet_size not in SAX_BREAKPOINTS:
            supported = sorted(SAX_BREAKPOINTS.keys())
            raise ValueError(
                f"alphabet_size must be one of {supported}, "
                f"got {alphabet_size}"
            )
        self.alphabet_size = alphabet_size
        self.window_size = window_size
        self.strategy = strategy
        self.breakpoints = SAX_BREAKPOINTS[alphabet_size]

    def _extract_series(self, df: pd.DataFrame) -> np.ndarray:
        """
        Extract a 1D series from OHLCV DataFrame based on strategy.

        Returns empty array for empty DataFrames.

        Strategies:
        - 'close': Simple close prices
        - 'typical_price': (H + L + C) / 3
        - 'ohlcv': Weighted composite of O, H, L, C, V
          This captures the full candlestick information in one symbol.
        """
        if len(df) == 0:
            return np.array([])

        if self.strategy == "close":
            return df["close"].values.astype(float)

        elif self.strategy == "typical_price":
            return ((df["high"] + df["low"] + df["close"]) / 3.0).values.astype(float)

        elif self.strategy == "ohlcv":
            # Weighted composite capturing candlestick body, wicks, and volume
            # Body: |C - O| / range (relative body size)
            # Direction: sign(C - O)
            # Wick ratio: (H - max(O,C)) / range and (min(O,C) - L) / range
            # Volume: normalized volume change
            o = df["open"].values.astype(float)
            h = df["high"].values.astype(float)
            l = df["low"].values.astype(float)
            c = df["close"].values.astype(float)
            v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones_like(c)

            # Prevent division by zero
            rng = h - l
            rng = np.where(rng == 0, 1e-10, rng)

            # Body center relative to range: (C+O)/2 - L / range → [0, 1]
            body_center = ((c + o) / 2.0 - l) / rng

            # Direction: -1 to +1
            direction = (c - o) / rng

            # Volume weight: relative to rolling mean
            # Use adaptive window size to avoid convolve shape mismatch
            vol_window = min(20, len(v))
            if vol_window > 0 and len(v) > 0:
                vol_mean = np.convolve(v, np.ones(vol_window) / vol_window, mode="same")
                vol_mean = np.where(vol_mean == 0, 1.0, vol_mean)
                vol_ratio = np.clip(v / vol_mean, 0.5, 2.0)
            else:
                vol_ratio = np.ones_like(v)

            # Composite: body_center * direction * vol_ratio
            composite = body_center * direction * (0.5 + 0.5 * vol_ratio)
            return composite

        else:
            raise ValueError(f"Unknown strategy: {self.strategy}")

    def _paa(self, series: np.ndarray) -> np.ndarray:
        """
        Piecewise Aggregate Approximation.

        Reduces the series dimensionality by averaging over windows.
        E.g., 100 candles with window_size=10 → 10 PAA values.
        """
        n = len(series)
        window = self.window_size

        # Trim to exact multiple of window_size
        trim = (n // window) * window
        if trim == 0:
            return np.array([])

        trimmed = series[:trim]

        # Reshape and compute mean per window
        return trimmed.reshape(-1, window).mean(axis=1)

    def _discretize(self, paa_values: np.ndarray) -> list[str]:
        """
        Convert PAA values to SAX symbols using breakpoints.

        Each PAA value is z-scored within its local context, then
        mapped to a symbol based on which breakpoint interval it falls in.
        """
        if len(paa_values) == 0:
            return []

        # Z-score normalization
        std = np.std(paa_values)
        if std < 1e-10:
            # Constant series → all same middle symbol
            mid = self.alphabet_size // 2
            return [SAX_ALPHABET[mid]] * len(paa_values)

        z_scores = (paa_values - np.mean(paa_values)) / std

        # Map each z-score to a symbol
        symbols = []
        for z in z_scores:
            idx = int(np.searchsorted(self.breakpoints, z))
            symbols.append(SAX_ALPHABET[idx])

        return symbols

    def get_paa_values(self, df: pd.DataFrame) -> np.ndarray:
        """
        Extract PAA values from OHLCV DataFrame without discretization.

        Useful for obtaining normalization statistics before encoding.

        Args:
            df: DataFrame with columns: open, high, low, close, volume

        Returns:
            Array of PAA values.
        """
        series = self._extract_series(df)
        return self._paa(series)

    def encode_with_normalization(
        self,
        df: pd.DataFrame,
        paa_mean: float | None = None,
        paa_std: float | None = None,
    ) -> tuple[list[str], float, float]:
        """
        Encode an OHLCV DataFrame with explicit z-score normalization.

        V7.9 critical fix: Training z-score stats must be used for test encoding
        to ensure SAX symbols are consistent between train and test windows.
        Without this, regime shifts cause different symbol mappings and the
        trie never matches.

        Args:
            df: DataFrame with columns: open, high, low, close, volume
            paa_mean: If provided, use this mean instead of computing from data
            paa_std: If provided, use this std instead of computing from data

        Returns:
            Tuple of (symbols, paa_mean, paa_std) where the stats can be
            reused for consistent encoding of test data.
        """
        series = self._extract_series(df)
        paa_values = self._paa(series)

        if len(paa_values) == 0:
            return [], 0.0, 1.0

        # Use provided stats or compute from current data
        if paa_mean is None:
            paa_mean = float(np.mean(paa_values))
        if paa_std is None:
            paa_std = float(np.std(paa_values))

        if paa_std < 1e-10:
            mid = self.alphabet_size // 2
            return [SAX_ALPHABET[mid]] * len(paa_values), paa_mean, paa_std

        z_scores = (paa_values - paa_mean) / paa_std

        symbols = []
        for z in z_scores:
            idx = int(np.searchsorted(self.breakpoints, z))
            symbols.append(SAX_ALPHABET[idx])

        return symbols, paa_mean, paa_std

    def encode(self, df: pd.DataFrame) -> list[str]:
        """
        Encode an OHLCV DataFrame into SAX symbols.

        Args:
            df: DataFrame with columns: open, high, low, close, volume

        Returns:
            List of SAX symbols, one per window of candles.
            Length = len(df) // window_size
        """
        series = self._extract_series(df)
        paa_values = self._paa(series)
        return self._discretize(paa_values)

    def encode_incremental(
        self,
        new_candles: pd.DataFrame,
        buffer: list[float] | None = None,
    ) -> tuple[list[str], list[float]]:
        """
        Incremental SAX encoding for real-time streaming.

        Instead of re-encoding the entire series, this method maintains
        a buffer of partial window data and only produces a new symbol
        when a full window is completed.

        Args:
            new_candles: New candle data (can be 1 or more rows)
            buffer: Previous partial window buffer (or None for first call)

        Returns:
            Tuple of (new_symbols, updated_buffer)
        """
        if buffer is None:
            buffer = []

        series = self._extract_series(new_candles)
        buffer.extend(series.tolist())

        symbols = []
        while len(buffer) >= self.window_size:
            window_data = np.array(buffer[:self.window_size])
            buffer = buffer[self.window_size:]

            # Z-score the window
            mean = np.mean(window_data)
            std = np.std(window_data)
            if std < 1e-10:
                mid = self.alphabet_size // 2
                symbols.append(SAX_ALPHABET[mid])
            else:
                z = (np.mean(window_data) - mean) / std
                idx = int(np.searchsorted(self.breakpoints, z))
                symbols.append(SAX_ALPHABET[idx])

        return symbols, buffer

    def symbol_distance(self, a: str, b: str) -> float:
        """
        Compute distance between two SAX symbols.

        Uses the SAX distance table based on breakpoint differences.
        This enables fuzzy matching — symbols that are 'close' in the
        alphabet represent similar price movements.
        """
        idx_a = SAX_ALPHABET.index(a)
        idx_b = SAX_ALPHABET.index(b)

        if idx_a == idx_b:
            return 0.0

        # Distance based on breakpoint gap
        lo, hi = min(idx_a, idx_b), max(idx_a, idx_b)

        if lo == 0 and hi < len(self.breakpoints):
            return abs(self.breakpoints[hi])
        elif hi >= len(self.breakpoints) and lo < len(self.breakpoints):
            return abs(self.breakpoints[lo])
        elif lo < len(self.breakpoints) and hi < len(self.breakpoints):
            return abs(self.breakpoints[hi] - self.breakpoints[lo])
        else:
            return float(hi - lo)

    def sequence_distance(self, seq_a: list[str], seq_b: list[str]) -> float:
        """
        Compute distance between two SAX sequences of equal length.

        Lower distance = more similar patterns.
        Used by the FuzzyMatcher for noise-tolerant matching.
        """
        if len(seq_a) != len(seq_b):
            raise ValueError("Sequences must be equal length")

        if len(seq_a) == 0:
            return 0.0

        total = sum(self.symbol_distance(a, b) for a, b in zip(seq_a, seq_b))
        return total / len(seq_a)
