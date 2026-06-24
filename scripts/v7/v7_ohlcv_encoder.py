"""
PPMT v7 — OHLCV Composite Encoder (F2)
========================================

Encodes raw OHLCV candles into single-symbol quantized tokens suitable
for use as trie keys. Replaces the old SAX discretization (v0.x) with
a sector-aware composite score + percentile quantization.

Why no SAX (audit conclusion, see PPMT_v7_MASTER_PLAN.md §4.1):
    - SAX discretizes a single series (close) and loses OHLCV structure
    - Pattern density too high (243 max patterns at α=3, n=5 → sparse)
    - Required delta encoding (extra complexity)
    - v7 uses a composite that fuses body, direction, and volume signal

Composite formula (per candle, see MASTER_PLAN §4.2):

    body_score = (close - open) / (high - low)         # range [-1, +1]
    direction  = sign(close - open)                     # {-1, 0, +1}
    vol_signal = clip(volume / vol_ma20, 0.5, 5.0)      # [0.5, 5.0]

    composite  = body_score * 0.40
               + direction  * 0.35
               + vol_signal * 0.25

Sectorized quantization:
    Each sector has a different bin count and sequence lengths:
        blue_chip : 3 bins  (BTC, ETH)        — small moves
        large_cap : 4 bins  (SOL, ADA, ...)
        old_meme  : 5 bins  (XRP, DOGE, SHIB)
        new_meme  : 6 bins  (PEPE, WIF, BONK) — extreme moves

    The encoder is FIT on training candles to compute percentile
    breakpoints of the composite score. Breakpoints are FROZEN at
    inference time (no leakage of future statistics).

Symbol alphabet:
    Bins are mapped to lowercase letters: 'a', 'b', 'c', ... up to N.
    A trie key for a sequence of length L is just the concatenation:
        sequence "a b c a" → key "abca"

Anti-leakage contract:
    - fit() is called ONLY on training-period candles
    - encode_sequence() at inference time uses only the frozen breakpoints
    - vol_ma20 is provided by the caller (computed via closed='left'
      rolling window — see MASTER_PLAN §11.1)
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Sector definitions (single source of truth; mirrors config/v7.yaml)
# ---------------------------------------------------------------------------

SECTOR_TOKENS: Dict[str, List[str]] = {
    "blue_chip": ["BTC", "ETH"],
    "large_cap": ["SOL", "ADA", "AVAX", "LINK"],
    "old_meme":  ["XRP", "DOGE", "SHIB"],
    "new_meme":  ["PEPE", "WIF", "BONK"],
}

SECTOR_BINS: Dict[str, int] = {
    "blue_chip": 3,
    "large_cap": 4,
    "old_meme":  5,
    "new_meme":  6,
}

SECTOR_SEQ_LENGTHS: Dict[str, List[int]] = {
    "blue_chip": [10, 15],
    "large_cap": [5, 10],
    "old_meme":  [5, 10],
    "new_meme":  [5],
}

# Default composite weights (mirror config/v7.yaml)
DEFAULT_WEIGHTS: Dict[str, float] = {
    "body_score": 0.40,
    "direction":  0.35,
    "vol_signal": 0.25,
}

# vol_signal clipping bounds
VOL_SIGNAL_MIN = 0.5
VOL_SIGNAL_MAX = 5.0

# vol_ma20 warm-up fallback (when fewer than 20 historical bars)
VOL_MA_WARMUP_FALLBACK = 1.0  # treat as "average" volume


def symbol_to_sector(symbol: str) -> str:
    """
    Map a raw symbol (e.g., 'BTCUSDT', 'BTC-USD', 'btc') to a v7 sector.

    Raises ValueError if symbol is not in any sector.
    """
    s = symbol.upper().strip()
    # Strip common exchange suffixes
    for suffix in ("USDT", "USD", "PERP", "-USD", "-USDT"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    s = s.replace("-", "").replace("_", "")

    for sector, tokens in SECTOR_TOKENS.items():
        if s in tokens:
            return sector
    raise ValueError(
        f"Symbol {symbol!r} (normalized={s!r}) is not classified in any v7 sector. "
        f"Known tokens: {sorted(t for ts in SECTOR_TOKENS.values() for t in ts)}"
    )


# ---------------------------------------------------------------------------
# Composite score (per-candle scalar)
# ---------------------------------------------------------------------------

def compute_composite_score(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    vol_ma20: float,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    Compute the per-candle composite score.

    Args:
        open_, high, low, close, volume: OHLCV candle values.
        vol_ma20: 20-period rolling mean of volume (caller provides,
                  must use closed='left' to avoid leakage).
        weights: optional weight override (defaults to DEFAULT_WEIGHTS).

    Returns:
        composite score (float, unbounded; typically in [-1.5, +1.5]).
    """
    w = weights or DEFAULT_WEIGHTS

    # --- body_score: (close - open) / (high - low) ---
    hl_range = high - low
    if hl_range <= 0.0:
        # Doji or malformed candle — no body information
        body_score = 0.0
    else:
        body_score = (close - open_) / hl_range
        # Clamp to [-1, +1] for numerical safety (open/high/low/close
        # outside [low, high] would be a data error)
        body_score = max(-1.0, min(1.0, body_score))

    # --- direction: sign(close - open) ---
    diff = close - open_
    if diff > 0.0:
        direction = 1.0
    elif diff < 0.0:
        direction = -1.0
    else:
        direction = 0.0

    # --- vol_signal: clip(volume / vol_ma20, 0.5, 5.0) ---
    if vol_ma20 is None or vol_ma20 <= 0.0 or not math.isfinite(vol_ma20):
        vol_signal = VOL_MA_WARMUP_FALLBACK
    else:
        vol_signal = volume / vol_ma20
        # Clamp
        vol_signal = max(VOL_SIGNAL_MIN, min(VOL_SIGNAL_MAX, vol_signal))

    composite = (
        w["body_score"] * body_score
        + w["direction"] * direction
        + w["vol_signal"] * vol_signal
    )
    return composite


# ---------------------------------------------------------------------------
# Encoder class
# ---------------------------------------------------------------------------

@dataclass
class OHLCVCompositeEncoder:
    """
    Sectorized OHLCV composite encoder.

    Lifecycle:
        enc = OHLCVCompositeEncoder(sector="blue_chip")
        enc.fit(composite_scores)        # training-time only
        sym = enc.encode_candle(o,h,l,c,v, vol_ma20)   # inference-time
        key = enc.encode_sequence(candles_df, seq_len=10)

    Persistence:
        enc.to_dict() / OHLCVCompositeEncoder.from_dict(d)  (JSON-safe)
    """

    sector: str
    bins: int
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    # Frozen training-time stats (set by fit())
    # Breakpoints: N-1 percentile cut points on the composite score.
    # Symbol 'a' = score <= bp[0], 'b' = bp[0] < score <= bp[1], ..., last = score > bp[N-2]
    breakpoints: List[float] = field(default_factory=list)
    train_count: int = 0
    train_mean: float = 0.0
    train_std: float = 0.0
    fitted: bool = False

    # Config (mirror MASTER_PLAN)
    vol_signal_min: float = VOL_SIGNAL_MIN
    vol_signal_max: float = VOL_SIGNAL_MAX

    def __post_init__(self) -> None:
        if self.sector not in SECTOR_BINS:
            raise ValueError(
                f"Unknown sector {self.sector!r}. "
                f"Known: {sorted(SECTOR_BINS.keys())}"
            )
        # If bins not provided explicitly, use sector default
        if self.bins <= 0:
            self.bins = SECTOR_BINS[self.sector]
        if self.bins < 2:
            raise ValueError(f"bins must be >= 2 (got {self.bins})")
        if self.bins > 26:
            raise ValueError(f"bins must be <= 26 (got {self.bins}) — alphabet limit")

    # ------------- fit -------------

    def fit(
        self,
        composite_scores: Sequence[float],
        method: str = "percentile",
    ) -> "OHLCVCompositeEncoder":
        """
        Fit breakpoints on training-time composite scores.

        Args:
            composite_scores: array of composite scores from TRAINING period
                              ONLY (caller enforces no test leakage).
            method: "percentile" (recommended, default) or "normal"
                - "percentile": use empirical quantiles (robust to outliers)
                - "normal": assume composite ~ N(0,1) and use z-score cut points
                            (faster but assumes distribution shape)

        Returns:
            self (for chaining).
        """
        scores = [float(s) for s in composite_scores if math.isfinite(s)]
        n = len(scores)
        if n < self.bins * 10:
            raise ValueError(
                f"Insufficient training samples: got {n}, need >= {self.bins * 10} "
                f"for sector={self.sector} bins={self.bins}"
            )

        # Training-time stats (for diagnostics / drift monitoring)
        self.train_count = n
        self.train_mean = sum(scores) / n
        var = sum((s - self.train_mean) ** 2 for s in scores) / max(1, n - 1)
        self.train_std = math.sqrt(var)

        # Compute breakpoints
        if method == "percentile":
            sorted_scores = sorted(scores)
            self.breakpoints = []
            for k in range(1, self.bins):
                # Linear interpolation between closest ranks (numpy default)
                rank = (k / self.bins) * (n - 1)
                lo = int(math.floor(rank))
                hi = int(math.ceil(rank))
                if lo == hi:
                    bp = sorted_scores[lo]
                else:
                    frac = rank - lo
                    bp = sorted_scores[lo] * (1.0 - frac) + sorted_scores[hi] * frac
                self.breakpoints.append(bp)
        elif method == "normal":
            # Standard normal quantile breakpoints
            # Use math.erfinv via approximation (avoid scipy dependency)
            self.breakpoints = [
                _normal_quantile(k / self.bins) for k in range(1, self.bins)
            ]
        else:
            raise ValueError(f"Unknown method {method!r}; use 'percentile' or 'normal'")

        self.fitted = True
        return self

    # ------------- encode (single candle) -------------

    def encode_candle(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        vol_ma20: float,
    ) -> str:
        """
        Encode a single OHLCV candle to a symbol ('a'..'z').

        Uses the frozen breakpoints from fit(). Raises if not fitted.
        """
        if not self.fitted:
            raise RuntimeError(
                f"Encoder for sector={self.sector!r} is not fitted. "
                "Call .fit() on training data first."
            )
        score = compute_composite_score(
            open_, high, low, close, volume, vol_ma20, self.weights
        )
        return self.quantize(score)

    def quantize(self, composite_score: float) -> str:
        """
        Quantize a pre-composite score into a symbol.

        Symbol assignment:
            score <= bp[0]           → 'a'
            bp[0] < score <= bp[1]   → 'b'
            ...
            score > bp[N-2]          → last symbol

        Args:
            composite_score: the per-candle composite score.

        Returns:
            Single character symbol (lowercase letter).
        """
        if not self.fitted:
            raise RuntimeError(
                f"Encoder for sector={self.sector!r} is not fitted."
            )
        if not math.isfinite(composite_score):
            # NaN or inf — assign to middle bin (neutral)
            mid = self.bins // 2
            return chr(ord("a") + mid)

        # Linear scan (N is tiny: 3-6)
        idx = 0
        for bp in self.breakpoints:
            if composite_score > bp:
                idx += 1
            else:
                break
        return chr(ord("a") + idx)

    # ------------- encode sequence -------------

    def encode_sequence(
        self,
        candles: Sequence[Tuple[float, float, float, float, float, float]],
        seq_len: int,
    ) -> str:
        """
        Encode a sequence of recent candles into a trie key.

        Args:
            candles: sequence of (open, high, low, close, volume, vol_ma20)
                     tuples, ORDERED OLDEST → NEWEST. The most recent
                     `seq_len` candles are used.
            seq_len: number of candles to encode (5, 10, or 15).

        Returns:
            A string key of length seq_len (e.g., "abcaab...").

        Raises:
            ValueError: if seq_len is not allowed for this sector, or
                        if fewer than seq_len candles are provided.
        """
        allowed = SECTOR_SEQ_LENGTHS.get(self.sector, [])
        if seq_len not in allowed:
            raise ValueError(
                f"seq_len={seq_len} not allowed for sector={self.sector!r}. "
                f"Allowed: {allowed}"
            )
        if len(candles) < seq_len:
            raise ValueError(
                f"Need >= {seq_len} candles, got {len(candles)}"
            )

        # Take the last seq_len candles (most recent)
        recent = candles[-seq_len:]
        symbols = []
        for (o, h, l, c, v, vma) in recent:
            sym = self.encode_candle(o, h, l, c, v, vma)
            symbols.append(sym)
        return "".join(symbols)

    # ------------- batch encode (utility) -------------

    def encode_series(
        self,
        opens: Sequence[float],
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        volumes: Sequence[float],
        vol_ma20s: Sequence[float],
    ) -> List[str]:
        """
        Batch-encode an entire series to a list of symbols.

        All inputs must have the same length. Returns a list of symbols
        (same length as inputs).
        """
        n = len(closes)
        if not (len(opens) == len(highs) == len(lows) == len(volumes) == n):
            raise ValueError("All OHLCV arrays must have equal length")
        if len(vol_ma20s) != n:
            raise ValueError(f"vol_ma20s length {len(vol_ma20s)} != {n}")

        return [
            self.encode_candle(opens[i], highs[i], lows[i], closes[i],
                               volumes[i], vol_ma20s[i])
            for i in range(n)
        ]

    # ------------- diagnostics -------------

    def symbol_distribution(self, symbols: Sequence[str]) -> Dict[str, float]:
        """
        Compute the empirical distribution of symbols in a sample.

        Useful for verifying that quantization is balanced (each bin
        should hold ~1/N of the training data by construction).
        """
        if not symbols:
            return {}
        counts: Dict[str, int] = {}
        for s in symbols:
            counts[s] = counts.get(s, 0) + 1
        total = len(symbols)
        return {k: v / total for k, v in sorted(counts.items())}

    # ------------- persistence -------------

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict."""
        return {
            "sector": self.sector,
            "bins": self.bins,
            "weights": dict(self.weights),
            "breakpoints": list(self.breakpoints),
            "train_count": self.train_count,
            "train_mean": self.train_mean,
            "train_std": self.train_std,
            "fitted": self.fitted,
            "vol_signal_min": self.vol_signal_min,
            "vol_signal_max": self.vol_signal_max,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OHLCVCompositeEncoder":
        """Deserialize from a dict (e.g., loaded from JSON)."""
        enc = cls(
            sector=d["sector"],
            bins=d.get("bins", SECTOR_BINS.get(d["sector"], 3)),
            weights=dict(d.get("weights", DEFAULT_WEIGHTS)),
            vol_signal_min=d.get("vol_signal_min", VOL_SIGNAL_MIN),
            vol_signal_max=d.get("vol_signal_max", VOL_SIGNAL_MAX),
        )
        enc.breakpoints = list(d.get("breakpoints", []))
        enc.train_count = int(d.get("train_count", 0))
        enc.train_mean = float(d.get("train_mean", 0.0))
        enc.train_std = float(d.get("train_std", 0.0))
        enc.fitted = bool(d.get("fitted", False))
        return enc

    def to_json(self, path: str) -> None:
        """Write encoder to a JSON file."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, path: str) -> "OHLCVCompositeEncoder":
        """Load encoder from a JSON file."""
        with open(path, "r") as f:
            d = json.load(f)
        return cls.from_dict(d)

    # ------------- factory -------------

    @classmethod
    def for_sector(cls, sector: str, weights: Optional[Dict[str, float]] = None) -> "OHLCVCompositeEncoder":
        """Create an unfitted encoder for a sector using default config."""
        return cls(
            sector=sector,
            bins=SECTOR_BINS[sector],
            weights=weights or dict(DEFAULT_WEIGHTS),
        )

    @classmethod
    def for_symbol(cls, symbol: str, weights: Optional[Dict[str, float]] = None) -> "OHLCVCompositeEncoder":
        """Create an unfitted encoder for the sector containing a symbol."""
        sector = symbol_to_sector(symbol)
        return cls.for_sector(sector, weights=weights)

    def __repr__(self) -> str:
        return (
            f"OHLCVCompositeEncoder(sector={self.sector!r}, bins={self.bins}, "
            f"fitted={self.fitted}, train_count={self.train_count})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normal_quantile(p: float) -> float:
    """
    Inverse CDF of standard normal at probability p in (0, 1).

    Uses Acklam's algorithm (no scipy dependency). Max error ~1.15e-9.
    """
    if not (0.0 < p < 1.0):
        raise ValueError(f"p must be in (0,1), got {p}")

    # Coefficients in rational approximations
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]

    plow = 0.02425
    phigh = 1.0 - plow

    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    return x


def compute_vol_ma20(volumes: Sequence[float], window: int = 20) -> List[float]:
    """
    Compute the 20-period rolling mean of volume with closed='left'.

    The 'left' closure means the current bar is EXCLUDED from its own
    rolling stat. This is mandatory to prevent leakage of the current
    bar's volume into its own vol_signal feature.

    For the first `window` bars, returns VOL_MA_WARMUP_FALLBACK (1.0)
    because there isn't enough history (need full `window` lookback
    before emitting a real average, mirrors pandas default min_periods).
    """
    n = len(volumes)
    out = [VOL_MA_WARMUP_FALLBACK] * n
    if n == 0:
        return out
    # closed='left' means rolling(window=window) at index i uses
    # bars [i-window, i-1]. We require a FULL window of history
    # before producing a non-fallback value (mirrors pandas
    # rolling(window=window).mean() with default min_periods=window,
    # which returns NaN for the first window-1 entries).
    running_sum = 0.0
    for i in range(n):
        # Add the bar at index i-1 (the bar just before current)
        if i >= 1:
            running_sum += volumes[i - 1]
        # Drop the bar that exited the lookback window
        if i - 1 - window >= 0:
            running_sum -= volumes[i - 1 - window]
        # Only emit a real average once we have a full window
        if i >= window:
            out[i] = running_sum / window
        # else: keep warmup fallback
    return out
