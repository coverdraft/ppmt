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
    2: np.array([0.0]),  # Binary split: below/above median
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


# === Per-Level Alphabet Size Configuration (FASE 1 Tarea 1.1) ===
#
# The 4-level Trie architecture needs DIFFERENT alphabet sizes per level:
#
#   N1 (Universal): shared across ALL tokens — small α so patterns fill fast.
#     α=3 → 3^5 = 243 patterns. With 5M+ observations from all assets,
#     every pattern gets 20,000+ repetitions → rock-solid confidence.
#     IMPORTANT: N1 uses ONLY PRICE (no volume) — Transfer Learning is about
#     price SHAPE, not volume. Volume adds noise at the universal level.
#
#   N2 (Asset Class): shared within asset class — SAX DUAL (price+volume).
#     α_price=3 (meme/new_launch) or 4 (default), α_vol=2.
#     Volume starts to matter here because we know the asset class context.
#
#   N3 (Per-Token): SAX DUAL. α_price=4, α_vol=3.
#     Token-specific data allows more granularity.
#
#   N4 (Per-Token+Regime): SAX DUAL, same as N3.
#
# WHY: When N1 used SAXDualEncoder (α_price=3, α_vol=2), the effective
# alphabet was 3×2=6 symbols, creating 6^5=7,776 possible patterns.
# With ~11,000 observations, each node had ~1.4 obs → the Bayesian prior
# (strength=10) dominated and confidence never exceeded 0.19.
# By making N1 PRICE-ONLY (α=3, max 243 patterns), each node gets ~45 obs
# and confidence > 0.6 becomes achievable. This is the key mathematical fix.
LEVEL_ALPHA_CONFIG: dict[str, int] = {
    "n1": 3,          # Universal: PRICE-ONLY, 3^5 = 243 patterns max
    "n2_meme": 3,     # Asset class meme/new_launch (price alpha for dual)
    "n2_new_launch": 3,  # Asset class new_launch (price alpha for dual)
    "n2_default": 4,  # Asset class blue_chip/large_cap/mid_cap/defi (price alpha for dual)
    "n3": 4,          # Per-token: price alpha for dual (vol=3 separately)
    "n4": 4,          # Per-token+regime: same as N3
}

# === Per-Level Dual SAX Configuration (v0.43.0) ===
#
# N1 is PRICE-ONLY — uses SAXEncoder, not SAXDualEncoder.
# N2/N3/N4 use SAXDualEncoder with these (price_alpha, volume_alpha) pairs.
#
# The key insight: volume is NOISE at the universal level (N1) but
# becomes useful when we know the asset class (N2) or specific token (N3/N4).
# This prevents the combinatorial explosion that was killing N1 confidence.
#
# Effective alphabet sizes per level:
#   N1: α=3 → 3^5 = 243 patterns (PRICE ONLY, Transfer Learning)
#   N2 (meme):      3×2=6 → 6^5 = 7,776 patterns (but class-specific, fills OK)
#   N2 (default):   4×2=8 → 8^5 = 32,768 patterns (class-specific, more tokens)
#   N3:             4×3=12 → 12^5 = 248,832 (per-token, unique patterns)
#   N4:             4×3=12 → same as N3 (regime-partitioned, even more specific)
#
# N1 density is the critical metric: 11,000 obs / 243 nodes ≈ 45 obs/node → conf > 0.6
# N2 density: ~2,000 obs / 7,776 nodes ≈ 0.26 obs/node → sparse, but N2 has lower weight
#   and receives observations from multiple tokens of the same class.
LEVEL_DUAL_ALPHA_CONFIG: dict[str, dict[str, int]] = {
    "n1": {"price": 3, "volume": 0},       # volume=0 means NO volume encoder (price-only)
    "n2_meme": {"price": 3, "volume": 0},   # v0.43.1: price-only for memes too (3^5=243, not 5^5=3125)
    "n2_new_launch": {"price": 3, "volume": 0},  # v0.43.1: same reasoning as memes
    "n2_default": {"price": 3, "volume": 0},     # v0.43.1: ALL N2 price-only (volume is noise at class level)
    "n3": {"price": 4, "volume": 3},
    "n4": {"price": 4, "volume": 3},
}

# v0.48.0 (FASE 2A): Per-level window size by timeframe.
#
# Lower timeframes produce too many symbols with large windows, making N3
# impossible to fill (e.g. 1m W=45 → 225 candles for P=5 → few patterns
# per symbol → sparse trie). With smaller windows for N3/N4, the local
# tries fill faster while N1/N2 keep large windows for stable patterns.
#
# Window semantics: W=number of candles per SAX symbol.
# Total candles needed for P symbols = W * P.
#   1m N1: 60*5=300 candles (5h), N3: 20*4=80 candles (80min)
#   5m N1: 24*5=120 candles (10h), N3: 10*4=40 candles (200min)
#   1h N1: 8*5=40 candles (40h), N3: 8*4=32 candles (32h) — same W
LEVEL_WINDOW_CONFIG: dict[str, dict[str, int]] = {
    "1m":  {"n1": 60, "n2": 60, "n3": 20, "n4": 20, "n5": 20},  # v0.48.0 (FASE 2B): N5=BTC context, 1m only
    "5m":  {"n1": 24, "n2": 24, "n3": 10, "n4": 10},
    "15m": {"n1": 12, "n2": 12, "n3": 6,  "n4": 6},
    "30m": {"n1": 10, "n2": 10, "n3": 8,  "n4": 8},
    "1h":  {"n1": 8,  "n2": 8,  "n3": 8,  "n4": 8},
    "4h":  {"n1": 10, "n2": 10, "n3": 10, "n4": 10},
    "1d":  {"n1": 10, "n2": 10, "n3": 10, "n4": 10},
}

# v0.48.0 (FASE 2A): Per-level pattern length.
#
# N3/N4 use shorter patterns (4 vs 5) because:
# 1. Local tries have fewer observations → shorter patterns = more matches
# 2. Dual encoders (SAXDualEncoder) produce tuples → 4^4=256 vs 4^5=1024 combos
# 3. N1/N2 keep P=5 for stability (universal pools have enough obs for 5-length)
LEVEL_PATTERN_CONFIG: dict[str, int] = {
    "n1": 5,
    "n2": 5,
    "n3": 4,
    "n4": 4,
    "n5": 4,  # v0.48.0 (FASE 2B): N5 BTC context, same as N3/N4
}


def get_alpha_for_level(
    level: str,
    asset_class: str = "default",
    calibrated_alpha: int | None = None,
) -> int:
    """
    Get the alphabet size for a given trie level and asset class.

    v0.43.0: N3/N4 default changed from 5 to 4 (used as price_alpha for dual).
    The calibrated_alpha still overrides for N3/N4 when provided.

    FASE 1 Tarea 1.1: Per-level differentiated alphabet sizes.

    Args:
        level: Trie level — "n1", "n2", "n3", or "n4".
        asset_class: Asset class — used for N2 differentiation.
        calibrated_alpha: Token's calibrated alpha (from CalibrationEngine).
            If provided, used for N3/N4 instead of the default.

    Returns:
        Alphabet size (int) for the SAXEncoder at this level.
            For N1: price-only α (no volume).
            For N2/N3/N4: price α (volume α is separate, see get_dual_alpha_for_level).

    Examples:
        >>> get_alpha_for_level("n1")
        3
        >>> get_alpha_for_level("n2", "meme")
        3
        >>> get_alpha_for_level("n2", "blue_chip")
        4
        >>> get_alpha_for_level("n3")
        4
        >>> get_alpha_for_level("n3", calibrated_alpha=5)
        5
    """
    if level == "n1":
        return LEVEL_ALPHA_CONFIG["n1"]

    elif level == "n2":
        # Meme and new_launch get α=3 (fewer tokens in class, need fewer patterns)
        if asset_class in ("meme", "new_launch"):
            return LEVEL_ALPHA_CONFIG["n2_meme"]
        else:
            # blue_chip, large_cap, mid_cap, defi, default → α=4
            return LEVEL_ALPHA_CONFIG["n2_default"]

    elif level == "n3":
        # Per-token: use calibrated alpha if available, else default
        if calibrated_alpha is not None:
            return calibrated_alpha
        return LEVEL_ALPHA_CONFIG["n3"]

    elif level == "n4":
        # Per-token+regime: same as N3
        if calibrated_alpha is not None:
            return calibrated_alpha
        return LEVEL_ALPHA_CONFIG["n4"]

    else:
        raise ValueError(f"Unknown trie level: {level!r}. Must be one of: n1, n2, n3, n4")


def get_dual_alpha_for_level(
    level: str,
    asset_class: str = "default",
    calibrated_alpha: int | None = None,
) -> dict[str, int]:
    """
    Get the dual SAX (price, volume) alphabet sizes for a given trie level.

    v0.43.0: Stratified SAX Dual — N1 is price-only (volume=0),
    N2/N3/N4 use dual encoding with different (price, volume) alphas.

    Returns:
        Dict with keys "price" and "volume". When volume=0, the level
        should use a plain SAXEncoder (no volume dimension).

    Examples:
        >>> get_dual_alpha_for_level("n1")
        {'price': 3, 'volume': 0}
        >>> get_dual_alpha_for_level("n2", "meme")
        {'price': 3, 'volume': 2}
        >>> get_dual_alpha_for_level("n2", "blue_chip")
        {'price': 4, 'volume': 2}
        >>> get_dual_alpha_for_level("n3")
        {'price': 4, 'volume': 3}
        >>> get_dual_alpha_for_level("n4")
        {'price': 4, 'volume': 3}
    """
    if level == "n1":
        return LEVEL_DUAL_ALPHA_CONFIG["n1"]

    elif level == "n2":
        if asset_class in ("meme", "new_launch"):
            return LEVEL_DUAL_ALPHA_CONFIG["n2_meme"]
        else:
            return LEVEL_DUAL_ALPHA_CONFIG["n2_default"]

    elif level == "n3":
        if calibrated_alpha is not None:
            # Override price alpha with calibrated value, keep volume=3
            return {"price": calibrated_alpha, "volume": LEVEL_DUAL_ALPHA_CONFIG["n3"]["volume"]}
        return LEVEL_DUAL_ALPHA_CONFIG["n3"]

    elif level == "n4":
        if calibrated_alpha is not None:
            return {"price": calibrated_alpha, "volume": LEVEL_DUAL_ALPHA_CONFIG["n4"]["volume"]}
        return LEVEL_DUAL_ALPHA_CONFIG["n4"]

    else:
        raise ValueError(f"Unknown trie level: {level!r}. Must be one of: n1, n2, n3, n4")


@dataclass
class SAXConfig:
    """Configuration for SAX encoding."""
    alphabet_size: int = 8
    window_size: int = 10  # PAA window: candles per SAX block
    strategy: Literal["ohlcv", "close", "typical_price", "volume"] = "ohlcv"


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

        elif self.strategy == "volume":
            # FASE 1 Tarea 1.3: Pure volume encoding for SAX Dual.
            #
            # Unlike the 'ohlcv' strategy where volume is just 25% of a
            # composite, this strategy encodes ONLY volume information.
            # The resulting symbols represent volume patterns independently
            # from price — enabling the dual encoder to preserve volume
            # signals that the single-composite approach loses.
            #
            # The encoding normalizes volume as a ratio vs its local moving
            # average, clipped to [0.5, 5.0]. This produces a near-Gaussian
            # distribution after z-scoring, which matches SAX assumptions.
            v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(df))

            vol_window = min(20, len(v))
            if vol_window > 0 and len(v) > 0:
                vol_mean = np.convolve(v, np.ones(vol_window) / vol_window, mode="same")
                vol_mean = np.where(vol_mean == 0, 1.0, vol_mean)
                vol_ratio = np.clip(v / vol_mean, 0.5, 5.0)
                return vol_ratio
            return np.ones_like(v)

        elif self.strategy == "ohlcv":
            # Additive composite capturing candlestick body, wicks, and volume.
            #
            # V0.6.2 FIX: Replaced multiplicative composite with additive.
            #
            # The previous formula `body_center * direction * vol_ratio` was
            # DEGENERATE: when direction ≈ 0 (small-body candles, 60%+ of data),
            # the entire composite collapsed to ~0, destroying body position and
            # volume information. After z-scoring, 92.5% of symbols mapped to
            # the middle symbol, producing near-zero information.
            #
            # The additive formula preserves all three features independently:
            #   - body_position (0.4 weight): WHERE in the range the body sits
            #   - direction (0.35 weight): WHICH WAY the candle moved
            #   - volume_signal (0.25 weight): HOW MUCH volume vs normal
            #
            # The resulting distribution is near-Gaussian, which matches the
            # SAX breakpoint assumptions and produces well-distributed symbols.
            o = df["open"].values.astype(float)
            h = df["high"].values.astype(float)
            l = df["low"].values.astype(float)
            c = df["close"].values.astype(float)
            v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones_like(c)

            # Prevent division by zero
            rng = h - l
            rng = np.where(rng == 0, 1e-10, rng)

            # Feature 1: Body position within range (0 to 1, ~0.5 for doji)
            # Captures WHERE the candle body sits relative to the full range.
            # A value near 0 = body near the low, near 1 = body near the high.
            body_position = ((c + o) / 2.0 - l) / rng

            # Feature 2: Direction strength (-1 to +1)
            # Captures both direction AND body size.
            # Near 0 = small body (doji), near ±1 = large body in one direction.
            direction = (c - o) / rng

            # Feature 3: Relative volume signal (normalized to ~0-1 range)
            # Captures whether this candle had above/below average volume.
            vol_window = min(20, len(v))
            if vol_window > 0 and len(v) > 0:
                vol_mean = np.convolve(v, np.ones(vol_window) / vol_window, mode="same")
                vol_mean = np.where(vol_mean == 0, 1.0, vol_mean)
                vol_ratio = np.clip(v / vol_mean, 0.5, 2.0)
                # Normalize to [0, 1]: vol_ratio 0.5→0.0, 1.0→0.33, 2.0→1.0
                vol_signal = (vol_ratio - 0.5) / 1.5
            else:
                vol_signal = np.full_like(v, 0.33)

            # Additive composite: preserves all three features independently.
            # Weights prioritize body position (most stable) and direction
            # (most predictive), with volume as confirming signal.
            # Range: body_position [0,1] × 0.4 = [0, 0.4]
            #        direction [-1,1] × 0.35 = [-0.35, 0.35]
            #        vol_signal [0,1] × 0.25 = [0, 0.25]
            # Total range: [-0.35, 1.0], near-Gaussian after z-scoring
            composite = (
                body_position * 0.4
                + direction * 0.35
                + vol_signal * 0.25
            )
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

    def encode_with_normalization(
        self,
        df: pd.DataFrame,
        paa_mean: float | None = None,
        paa_std: float | None = None,
    ) -> tuple[list[str], float, float]:
        """
        Encode an OHLCV DataFrame with explicit z-score normalization.

        v0.6.3 (V7.9 backport): Training z-score stats must be used for test
        encoding to ensure SAX symbols are consistent between train and test
        windows. Without this, regime shifts cause different symbol mappings
        and the trie never matches.

        When called with paa_mean=None, paa_std=None (training mode), computes
        stats from the current data and returns them. When called with explicit
        stats (test mode), uses those instead — ensuring consistent symbols.

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

    def encode_incremental(
        self,
        new_candles: pd.DataFrame,
        buffer: list[float] | None = None,
        paa_mean: float | None = None,
        paa_std: float | None = None,
    ) -> tuple[list[str], list[float]]:
        """
        Incremental SAX encoding for real-time streaming.

        Instead of re-encoding the entire series, this method maintains
        a buffer of partial window data and only produces a new symbol
        when a full window is completed.

        v0.19.1 FIX: The previous implementation z-scored each window against
        itself (mean of window - mean of window = 0), always producing the
        middle symbol. Now uses one of three normalization strategies:

        1. **External stats** (paa_mean, paa_std provided): Uses training
           statistics for consistent encoding between build and live. This
           is the RECOMMENDED mode for production — call encode_with_normalization()
           during build, then pass the same stats here.

        2. **Running stats** (no external stats, buffer has enough history):
           Maintains a running mean/std over all observed PAA values and
           z-scores new windows against the global distribution. This provides
           reasonable incremental encoding without requiring precomputed stats.

        3. **Window-only** (no external stats, insufficient history):
           Falls back to z-scoring the window mean against 0 with unit std,
           which effectively just discretizes the raw PAA value. This is a
           degraded mode — at minimum 2 symbols should be accumulated before
           meaningful encoding is possible.

        Args:
            new_candles: New candle data (can be 1 or more rows)
            buffer: Previous partial window buffer (or None for first call)
            paa_mean: Training PAA mean for consistent z-scoring (recommended)
            paa_std: Training PAA std for consistent z-scoring (recommended)

        Returns:
            Tuple of (new_symbols, updated_buffer)
        """
        if buffer is None:
            buffer = []

        series = self._extract_series(new_candles)
        buffer.extend(series.tolist())

        # Running PAA statistics for incremental normalization
        # These accumulate across calls to maintain a global reference distribution
        if not hasattr(self, '_running_paa_values'):
            self._running_paa_values: list[float] = []
        if paa_mean is not None:
            # Use external stats — reset running values since we have the truth
            self._running_paa_mean = paa_mean
            self._running_paa_std = paa_std if paa_std is not None else 1.0
        elif not hasattr(self, '_running_paa_mean'):
            self._running_paa_mean = None
            self._running_paa_std = None

        symbols = []
        while len(buffer) >= self.window_size:
            window_data = np.array(buffer[:self.window_size])
            buffer = buffer[self.window_size:]

            # Compute PAA value (mean of window)
            paa_value = float(np.mean(window_data))

            # Update running stats
            self._running_paa_values.append(paa_value)
            # Keep last 500 PAA values for running stats (prevents unbounded growth)
            if len(self._running_paa_values) > 500:
                self._running_paa_values = self._running_paa_values[-500:]

            # Z-score normalization
            if self._running_paa_mean is not None:
                # Strategy 1: External stats (from training) — best quality
                mean = self._running_paa_mean
                std = self._running_paa_std
            elif len(self._running_paa_values) >= 2:
                # Strategy 2: Running stats — good quality after warmup
                arr = np.array(self._running_paa_values)
                mean = float(np.mean(arr))
                std = float(np.std(arr))
            else:
                # Strategy 3: Degraded — first window only, can't z-score yet
                # Use the PAA value directly mapped to breakpoints
                # Since breakpoints are z-score boundaries, we assume mean=0, std=sigma
                # where sigma is estimated from the PAA value magnitude
                mean = 0.0
                std = max(abs(paa_value), 1e-10)

            if std < 1e-10:
                mid = self.alphabet_size // 2
                symbols.append(SAX_ALPHABET[mid])
            else:
                z = (paa_value - mean) / std
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


# ===================================================================== #
# FASE 1 Tarea 1.3: SAX Dual Encoder (Precio + Volumen)
# ===================================================================== #

# Separator for converting tuple symbols to string keys.
# Must not conflict with SAX_ALPHABET characters (a-z lowercase).
DUAL_SYMBOL_SEPARATOR = "|"


def make_symbol_key(symbol) -> str:
    """Convert a symbol (str or tuple) to a string key for Trie storage.

    DEPRECATED (v0.42.0): The trie now uses tuple keys natively.
    This function is kept ONLY for backward-compatible deserialization
    of old DB entries. Do NOT use in new code.

    Single symbols pass through unchanged: 'a' -> 'a'
    Tuple symbols are joined with DUAL_SYMBOL_SEPARATOR:
        ('a', 'x') -> 'a|x'
    """
    if isinstance(symbol, tuple):
        return DUAL_SYMBOL_SEPARATOR.join(str(s) for s in symbol)
    return str(symbol)


def parse_symbol_key(key: str):
    """Parse a string key back into a symbol or tuple.

    DEPRECATED (v0.42.0): Only used for backward-compatible deserialization.
    New code uses tuples directly.

    Keys without the separator pass through: 'a' -> 'a'
    Keys with the separator become tuples: 'a|x' -> ('a', 'x')

    This is the inverse of make_symbol_key().
    """
    if DUAL_SYMBOL_SEPARATOR in key:
        return tuple(key.split(DUAL_SYMBOL_SEPARATOR))
    return key


class SAXDualEncoder:
    """Dual SAX encoder that produces independent price and volume symbols.

    FASE 1 Tarea 1.3: Instead of a single composite symbol per position
    (where volume contributes only 25%), each position is a TUPLE of
    (price_symbol, volume_symbol). This preserves volume information
    that the single-composite approach loses.

    Example (α_price=3, α_vol=2, window=7):
        Input:  100 candles of OHLCV data
        Output: [('a','x'), ('b','y'), ('a','x'), ('c','z'), ('b','x')]

    The tuples become KEYS in the Trie's children dict, converted to
    strings like 'a|x' via make_symbol_key(). This means the Trie
    internals don't need to change at all.

    Composite alphabet size = α_price × α_vol (for compatibility with
    code that queries alphabet_size, e.g. FuzzyMatcher's alphabet
    enumeration).
    """

    def __init__(
        self,
        price_alphabet_size: int = 3,
        volume_alphabet_size: int = 2,
        window_size: int = 7,
        price_strategy: str = "ohlcv",
    ):
        self.price_encoder = SAXEncoder(
            alphabet_size=price_alphabet_size,
            window_size=window_size,
            strategy=price_strategy,
        )
        self.volume_encoder = SAXEncoder(
            alphabet_size=volume_alphabet_size,
            window_size=window_size,
            strategy="volume",
        )
        self.price_alphabet_size = price_alphabet_size
        self.volume_alphabet_size = volume_alphabet_size
        self.window_size = window_size
        self.strategy = price_strategy  # for compatibility

    @property
    def alphabet_size(self) -> int:
        """Composite alphabet size (α_price × α_vol).

        Used by FuzzyMatcher for alphabet enumeration and distance
        computation. The effective symbol space is the Cartesian product
        of price × volume symbols.
        """
        return self.price_alphabet_size * self.volume_alphabet_size

    @property
    def breakpoints(self) -> np.ndarray:
        """Breakpoints from the price encoder (for compatibility)."""
        return self.price_encoder.breakpoints

    def encode(self, df: pd.DataFrame) -> list[tuple[str, str]]:
        """Encode OHLCV DataFrame into dual symbols.

        Returns a list of (price_symbol, volume_symbol) tuples.
        Both encoders use the same window_size, so the output lists
        have equal length.
        """
        price_symbols = self.price_encoder.encode(df)
        volume_symbols = self.volume_encoder.encode(df)
        min_len = min(len(price_symbols), len(volume_symbols))
        return [(price_symbols[i], volume_symbols[i]) for i in range(min_len)]

    def encode_with_normalization(
        self,
        df: pd.DataFrame,
        paa_mean: float | None = None,
        paa_std: float | None = None,
    ) -> tuple[list[tuple[str, str]], float, float]:
        """Encode with explicit z-score normalization (price encoder stats).

        Returns (dual_symbols, paa_mean, paa_std) where stats come from
        the price encoder for backwards compatibility.
        """
        price_symbols, p_mean, p_std = self.price_encoder.encode_with_normalization(
            df, paa_mean=paa_mean, paa_std=paa_std,
        )
        volume_symbols, _, _ = self.volume_encoder.encode_with_normalization(df)
        min_len = min(len(price_symbols), len(volume_symbols))
        dual = [(price_symbols[i], volume_symbols[i]) for i in range(min_len)]
        return dual, p_mean, p_std

    def encode_incremental(
        self,
        new_candles: pd.DataFrame,
        buffer: list[float] | None = None,
        paa_mean: float | None = None,
        paa_std: float | None = None,
    ) -> tuple[list[tuple[str, str]], list[float]]:
        """Incremental dual encoding for streaming.

        Maintains separate buffers for price and volume encoders.
        Returns (new_dual_symbols, updated_price_buffer).

        Note: the returned buffer is the price encoder's buffer. The
        volume encoder's buffer is maintained internally.
        """
        if buffer is None:
            buffer = []

        # Maintain separate volume buffer
        if not hasattr(self, '_vol_buffer'):
            self._vol_buffer: list[float] = []

        price_symbols, updated_price_buffer = self.price_encoder.encode_incremental(
            new_candles, buffer,
            paa_mean=paa_mean, paa_std=paa_std,
        )
        volume_symbols, self._vol_buffer = self.volume_encoder.encode_incremental(
            new_candles, self._vol_buffer,
        )

        # Pair up symbols
        min_len = min(len(price_symbols), len(volume_symbols))
        dual = [(price_symbols[i], volume_symbols[i]) for i in range(min_len)]

        return dual, updated_price_buffer

    def symbol_distance(self, a, b) -> float:
        """Compute distance between two symbols (single or dual).

        For dual symbols (tuples), the distance is a weighted sum:
          distance = 0.6 × price_distance + 0.4 × volume_distance

        Price gets higher weight because it's more predictive for
        trading decisions. Volume is confirming signal.

        For single symbols, delegates to the price encoder.
        """
        if isinstance(a, tuple) and isinstance(b, tuple):
            price_dist = self.price_encoder.symbol_distance(a[0], b[0])
            vol_dist = self.volume_encoder.symbol_distance(a[1], b[1])
            # Normalize each distance to [0, 1] range for fair weighting
            max_price_dist = self.price_encoder.breakpoints[-1] * 2 if len(self.price_encoder.breakpoints) > 0 else 1.0
            max_vol_dist = self.volume_encoder.breakpoints[-1] * 2 if len(self.volume_encoder.breakpoints) > 0 else 1.0
            norm_price = min(price_dist / max(max_price_dist, 1e-10), 1.0)
            norm_vol = min(vol_dist / max(max_vol_dist, 1e-10), 1.0)
            return 0.6 * norm_price + 0.4 * norm_vol
        # Single symbol: delegate to price encoder
        a_str = a[0] if isinstance(a, tuple) else a
        b_str = b[0] if isinstance(b, tuple) else b
        return self.price_encoder.symbol_distance(a_str, b_str)

    def sequence_distance(self, seq_a: list, seq_b: list) -> float:
        """Compute distance between two dual-symbol sequences."""
        if len(seq_a) != len(seq_b):
            raise ValueError("Sequences must be equal length")
        if len(seq_a) == 0:
            return 0.0
        total = sum(self.symbol_distance(a, b) for a, b in zip(seq_a, seq_b))
        return total / len(seq_a)

    def get_alphabet_list(self) -> list[tuple[str, str]]:
        """Get the full Cartesian product alphabet for dual symbols.

        Returns list of all (price_sym, vol_sym) tuples.
        Used by FuzzyMatcher for edit-distance enumeration.
        """
        price_syms = [SAX_ALPHABET[i] for i in range(self.price_alphabet_size)]
        vol_syms = [SAX_ALPHABET[i] for i in range(self.volume_alphabet_size)]
        return [(p, v) for p in price_syms for v in vol_syms]
