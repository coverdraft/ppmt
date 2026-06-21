"""
PPMT Engine - 4-Level Progressive Pattern Matching

The main engine that orchestrates:
  1. SAX symbolization of incoming OHLCV data
  2. Parallel search across 4 Trie levels
  3. Adaptive weight-based confidence computation
  4. Signal generation from Block Lifecycle Metadata

Architecture:
  ┌─────────────────────────────────┐
  │         OHLCV Data              │
  └────────────┬────────────────────┘
               │
        ┌──────▼──────┐
        │  SAX Encode  │
        └──────┬──────┘
               │
    ┌──────────┼──────────────────────┐
    │          │                      │
 ┌──▼──┐  ┌───▼───┐  ┌────▼────┐  ┌──▼───┐
 │ N1  │  │  N2   │  │   N3    │  │  N4  │
 │10%  │  │  30%  │  │  30%    │  │ 30%  │
 └──┬──┘  └───┬───┘  └────┬────┘  └──┬───┘
    │         │           │           │
    └─────────┼───────────┼───────────┘
              │           │
        ┌─────▼───────────▼──────┐
        │  Adaptive Weight Merge  │
        └───────────┬────────────┘
                    │
           ┌────────▼────────┐
           │ Signal Generator │
           └────────┬────────┘
                    │
           ┌────────▼────────┐
           │  Trading Signal  │
           └─────────────────┘
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ppmt.core.sax import SAXEncoder, SAXDualEncoder, get_alpha_for_level, get_dual_alpha_for_level, LEVEL_WINDOW_CONFIG, LEVEL_PATTERN_CONFIG
from ppmt.core.trie import PPMTTrie, TrieNode, RegimePartitionedTrie
from ppmt.core.matcher import FuzzyMatcher, MatchResult
from ppmt.core.metadata import BlockLifecycleMetadata, compute_outcome_won
from ppmt.core.regime import RegimeDetector
from ppmt.engine.weights import AdaptiveWeights, LevelStats, WEIGHT_PROFILES
from ppmt.engine.signal import SignalGenerator, Signal, SignalType
from ppmt.data.storage import UNIVERSAL_POOL_KEY, class_pool_key

logger = logging.getLogger(__name__)


@dataclass
class PPMTResult:
    """Complete result from a PPMT pattern matching cycle."""

    signal: Signal
    """The generated trading signal."""

    n1_match: Optional[MatchResult] = None
    n2_match: Optional[MatchResult] = None
    n3_match: Optional[MatchResult] = None
    n4_match: Optional[MatchResult] = None

    n1_confidence: float = 0.0
    n2_confidence: float = 0.0
    n3_confidence: float = 0.0
    n4_confidence: float = 0.0

    weighted_confidence: float = 0.0
    sax_symbols: list[str] = field(default_factory=list)

    search_time_ms: float = 0.0
    """Time spent on pattern matching in milliseconds."""


class PPMT:
    """
    Progressive Pattern Matching Trie Engine.

    The main engine that coordinates 4 Trie levels with adaptive weights
    and generates autonomous trading signals from Block Lifecycle Metadata.

    Usage:
        engine = PPMT(symbol="BTC/USDT", asset_class="blue_chip")

        # Build from historical data
        engine.build(ohlcv_df)

        # Real-time pattern matching
        result = engine.match(current_sax_symbols, current_price)
        if result.signal.is_entry:
            # Execute entry with result.signal.sl_price, tp_price
            pass

    The engine is designed to run locally with all data stored in SQLite.
    It requires only an external Capital Risk Manager to decide position sizing.
    """

    def __init__(
        self,
        symbol: str,
        asset_class: str = "default",
        sax_alphabet_size: int = 8,
        sax_window_size: int = 10,
        sax_strategy: str = "ohlcv",
        fuzzy_threshold: float = 0.85,
        min_confidence: float = 0.60,
        min_risk_reward: float = 1.5,
        weight_profile: Optional[str] = None,
        dual_sax: bool = True,
        timeframe: Optional[str] = None,
    ):
        self.symbol = symbol
        self.asset_class = asset_class

        # v0.42.0: When timeframe is provided, override sax_alphabet_size
        # and sax_window_size from TIMEFRAME_ALPHA_DEFAULTS if the caller
        # didn't explicitly set them (i.e., they're still at defaults).
        # This ensures the engine uses the correct W for each timeframe:
        #   1m → W=45, α=3
        #   5m → W=18, α=3
        #   etc.
        self.timeframe = timeframe
        if timeframe is not None:
            from ppmt.core.profiles import TIMEFRAME_ALPHA_DEFAULTS
            tf_config = TIMEFRAME_ALPHA_DEFAULTS.get(timeframe)
            if tf_config is not None:
                # Always override when timeframe is set — the caller should
                # pass explicit sax_alphabet_size/sax_window_size if they want
                # to override the timeframe-based defaults.
                sax_alphabet_size = tf_config["sax_alphabet_size"]
                sax_window_size = tf_config["sax_window_size"]
                logger.info(
                    f"Timeframe {timeframe}: α={sax_alphabet_size}, W={sax_window_size}"
                )

        # v0.48.0 (FASE 2A): Per-level window size and pattern length.
        #
        # Each level gets its own window_size from LEVEL_WINDOW_CONFIG (by timeframe)
        # and its own pattern_length from LEVEL_PATTERN_CONFIG.
        # This fixes the "N3 impossible to fill" bug on low timeframes:
        #   1m N1 W=60 P=5 → 300 candles per pattern, N3 W=20 P=4 → 80 candles
        #   5m N1 W=24 P=5 → 120 candles, N3 W=10 P=4 → 40 candles
        # Fallback: if timeframe is None or not in config, use sax_window_size for all.
        _tf_window_config = LEVEL_WINDOW_CONFIG.get(timeframe) if timeframe else None
        if _tf_window_config is None:
            _tf_window_config = {
                "n1": sax_window_size,
                "n2": sax_window_size,
                "n3": sax_window_size,
                "n4": sax_window_size,
            }
        self.pl_n1 = LEVEL_PATTERN_CONFIG["n1"]
        self.pl_n2 = LEVEL_PATTERN_CONFIG["n2"]
        self.pl_n3 = LEVEL_PATTERN_CONFIG["n3"]
        self.pl_n4 = LEVEL_PATTERN_CONFIG["n4"]
        self.pl_n5 = LEVEL_PATTERN_CONFIG["n5"]  # v0.48.0 (FASE 2B): N5 BTC context
        # Backwards compat: self._pattern_length = max(pl) for callers that
        # use a single value (e.g. buffer sizing in match_raw).
        self._pattern_length = max(self.pl_n1, self.pl_n2, self.pl_n3, self.pl_n4, self.pl_n5)

        # v0.43.0: STRATIFIED SAX Dual — N1 is PRICE-ONLY.
        #
        # The key mathematical insight: volume is noise at the universal level.
        # When N1 used SAXDualEncoder (α_price=3, α_vol=2), the effective
        # alphabet was 3×2=6 → 6^5=7,776 possible patterns. With ~11,000 obs,
        # each node had ~1.4 obs → Bayesian prior dominated, confidence ≤0.19.
        #
        # By making N1 price-only (α=3 → max 243 patterns), each node gets
        # ~45 obs and confidence > 0.6 becomes achievable.
        #
        # N2/N3/N4 continue using SAXDualEncoder because volume becomes useful
        # when we know the asset class or specific token.
        self.dual_sax = dual_sax

        # v0.43.0: Per-level dual alpha configuration.
        # Each level gets (price_alpha, volume_alpha) from LEVEL_DUAL_ALPHA_CONFIG.
        # N1 has volume=0 → uses plain SAXEncoder (price only).
        #
        # IMPORTANT: Do NOT pass sax_alphabet_size as calibrated_alpha here.
        # The TIMEFRAME_ALPHA_DEFAULTS sets sax_alphabet_size=3 for 1m/5m,
        # but that was meant for a SINGLE encoder model. With stratified SAX,
        # each level has its own FIXED alpha from LEVEL_DUAL_ALPHA_CONFIG:
        #   N1: price=3 (no volume)
        #   N2: price=3/4, vol=2 (depends on asset class)
        #   N3: price=4, vol=3
        #   N4: price=4, vol=3
        # Only pass calibrated_alpha when the user explicitly set it AND
        # it differs from the timeframe default (i.e., manual calibration).
        n1_dual = get_dual_alpha_for_level("n1", asset_class)
        n2_dual = get_dual_alpha_for_level("n2", asset_class)
        n3_dual = get_dual_alpha_for_level("n3", asset_class)
        n4_dual = get_dual_alpha_for_level("n4", asset_class)

        # v0.48.0 (FASE 2A): Each encoder uses its own window_size.
        _w_n1 = _tf_window_config["n1"]
        _w_n2 = _tf_window_config["n2"]
        _w_n3 = _tf_window_config["n3"]
        _w_n4 = _tf_window_config["n4"]
        _w_n5 = _tf_window_config.get("n5")  # v0.48.0 (FASE 2B): N5 only for 1m
        logger.info(
            f"Per-level windows: N1={_w_n1}, N2={_w_n2}, N3={_w_n3}, N4={_w_n4}, N5={_w_n5} | "
            f"Pattern lengths: N1={self.pl_n1}, N2={self.pl_n2}, N3={self.pl_n3}, N4={self.pl_n4}, N5={self.pl_n5}"
        )

        # N1: PRICE-ONLY — uses SAXEncoder (no volume dimension).
        # This is the critical fix: N1 max 243 patterns → ~45 obs/node → conf > 0.6.
        self.sax_n1 = SAXEncoder(
            alphabet_size=n1_dual["price"],
            window_size=_w_n1,
            strategy=sax_strategy,
        )

        if dual_sax:
            # N2/N3/N4: SAXDualEncoder with per-level (price, volume) alphas.
            # v0.43.1: N2 with volume=0 uses SAXEncoder (price-only), same as N1.
            # This prevents combinatorial explosion in sparse pools like meme/new_launch.
            if n2_dual["volume"] == 0:
                self.sax_n2 = SAXEncoder(
                    alphabet_size=n2_dual["price"],
                    window_size=_w_n2,
                    strategy=sax_strategy,
                )
            else:
                self.sax_n2 = SAXDualEncoder(
                    price_alphabet_size=n2_dual["price"],
                    volume_alphabet_size=n2_dual["volume"],
                    window_size=_w_n2,
                    price_strategy=sax_strategy,
                )
            self.sax_n3 = SAXDualEncoder(
                price_alphabet_size=n3_dual["price"],
                volume_alphabet_size=n3_dual["volume"],
                window_size=_w_n3,
                price_strategy=sax_strategy,
            )
            self.sax_n4 = SAXDualEncoder(
                price_alphabet_size=n4_dual["price"],
                volume_alphabet_size=n4_dual["volume"],
                window_size=_w_n4,
                price_strategy=sax_strategy,
            )
        else:
            # Legacy single-composite encoding
            n2_alpha = get_alpha_for_level("n2", asset_class)
            n3_alpha = get_alpha_for_level("n3", asset_class, calibrated_alpha=sax_alphabet_size)
            n4_alpha = get_alpha_for_level("n4", asset_class, calibrated_alpha=sax_alphabet_size)
            self.sax_n2 = SAXEncoder(
                alphabet_size=n2_alpha,
                window_size=_w_n2,
                strategy=sax_strategy,
            )
            self.sax_n3 = SAXEncoder(
                alphabet_size=n3_alpha,
                window_size=_w_n3,
                strategy=sax_strategy,
            )
            self.sax_n4 = SAXEncoder(
                alphabet_size=n4_alpha,
                window_size=_w_n4,
                strategy=sax_strategy,
            )

        # v0.48.0 (FASE 2B): N5 — BTC Context Level (1m only).
        # N5 uses SAXDualEncoder (same params as N3) but partitions by BTC context
        # (bull/bear/neutral) instead of regime. Only created for timeframe="1m".
        self.sax_n5 = None
        if _w_n5 is not None and dual_sax:
            self.sax_n5 = SAXDualEncoder(
                price_alphabet_size=n3_dual["price"],  # same alpha as N3
                volume_alphabet_size=n3_dual["volume"],
                window_size=_w_n5,
                price_strategy=sax_strategy,
            )
        elif _w_n5 is not None:
            self.sax_n5 = SAXEncoder(
                alphabet_size=n3_dual["price"],
                window_size=_w_n5,
                strategy=sax_strategy,
            )

        # Backwards compatibility: self.sax = N3's encoder
        self.sax = self.sax_n3

        # Fuzzy matchers — one per level, each using its level's SAX encoder
        # for alphabet-aware distance computation (symbol_distance, breakpoints).
        self.matcher_n1 = FuzzyMatcher(
            sax_encoder=self.sax_n1,
            threshold=fuzzy_threshold,
        )
        self.matcher_n2 = FuzzyMatcher(
            sax_encoder=self.sax_n2,
            threshold=fuzzy_threshold,
        )
        self.matcher_n3 = FuzzyMatcher(
            sax_encoder=self.sax_n3,
            threshold=fuzzy_threshold,
        )
        self.matcher_n4 = FuzzyMatcher(
            sax_encoder=self.sax_n4,
            threshold=fuzzy_threshold,
        )
        # v0.48.0 (FASE 2B): N5 matcher (1m only)
        self.matcher_n5 = None
        if self.sax_n5 is not None:
            self.matcher_n5 = FuzzyMatcher(
                sax_encoder=self.sax_n5,
                threshold=fuzzy_threshold,
            )

        # Backwards compatibility: self.matcher = N3's matcher
        self.matcher = self.matcher_n3

        # Signal generator
        self.signal_generator = SignalGenerator(
            min_confidence=min_confidence,
            min_risk_reward=min_risk_reward,
        )

        # 4-Level Tries
        # v0.40.2 FIX-1: N4 is now a RegimePartitionedTrie — internally
        # maintains 4 sub-tries (one per regime). This breaks the
        # N1==N2==N3==N4 structural identity that CAPA 1 audit #3 found
        # was making the 4-level architecture purely decorative.
        # N1 (universal), N2 (asset_class), N3 (per_asset) stay as plain
        # PPMTTrie — they remain structurally identical in single-symbol
        # operation, but their *role* is differentiated when tries are
        # shared across PPMT instances via set_tries() (e.g., PaperTrader
        # loads N1 from a global pool, N2 from an asset_class pool, N3
        # from the per-symbol storage).
        self.trie_n1 = PPMTTrie(name=f"universal")
        self.trie_n2 = PPMTTrie(name=f"asset_class:{asset_class}")
        self.trie_n3 = PPMTTrie(name=f"per_asset:{symbol}")
        self.trie_n4 = RegimePartitionedTrie(name=f"per_asset_regime:{symbol}")
        # v0.48.0 (FASE 2B): N5 — BTC Context Partitioned Trie (1m only).
        # Partitions by BTC context (btc_bull, btc_bear, btc_neutral).
        # Only created when timeframe is "1m".
        self.trie_n5 = None
        if timeframe == "1m":
            self.trie_n5 = RegimePartitionedTrie(
                name=f"per_asset_btc_ctx:{symbol}",
                regimes=["btc_bull", "btc_bear", "btc_neutral"],
            )

        # Adaptive weights
        if weight_profile:
            self.weights = AdaptiveWeights.from_profile(weight_profile)
        else:
            self.weights = AdaptiveWeights.from_profile(
                self._infer_weight_profile(asset_class)
            )

        # Incremental SAX buffer
        self._sax_buffer: list[float] = []

        # Current regime
        self._current_regime: Optional[str] = None

        # v0.38.8: RegimeDetector instance (used for detect_simple during
        # trie build). Auto-calibrated for crypto (vol=0.15, trend=0.001).
        # The detect_simple method uses RegimeThresholds.simple_vol_cutoff
        # (0.08) and simple_move_cutoff (0.02) — preserved verbatim from
        # the previous _detect_simple_regime static method.
        self.regime_detector = RegimeDetector()

        # v0.40.3 FIX-1B: Optional storage reference for cross-asset pool
        # contribution. When set via `attach_storage()`, every observation
        # inserted during build() is also pushed to:
        #   - universal N1 pool (storage key __UNIVERSAL__, level 'n1')
        #   - class-shared N2 pool (storage key __CLASS_<asset_class>__, level 'n2')
        # This realizes the original V3 design (PPMT_Technical_Document_V3.pdf
        # §3.1-3.4) where N1 is a cross-asset safety net and N2 is the
        # same-class competitive-advantage pool.
        self._storage = None
        # In-memory accumulation buffers — flushed to storage at the end of
        # build() to amortize the cost of storage round-trips.
        self._n1_buffer = PPMTTrie(name="universal_buffer")
        self._n2_buffer = PPMTTrie(name=f"class_buffer:{asset_class}")

        # Statistics
        self._total_patterns_built = 0

    def attach_storage(self, storage) -> None:
        """
        v0.40.3 FIX-1B: Attach a PPMTStorage instance for cross-asset pool
        contribution. When attached, `build()` will push each observation
        to the universal N1 pool and the class-shared N2 pool in addition
        to the per-symbol N3 and per-symbol+regime N4. This is what makes
        N1 truly universal (5M+ patterns from all assets, per V3 design)
        and N2 truly class-shared (BTC ↔ ETH for blue_chip, etc.).

        Without attachment, the engine operates in single-symbol mode
        (v0.40.2 behavior): N1/N2 are structurally identical to N3, and
        the cross-asset advantage does not materialize.
        """
        self._storage = storage
        # (Re)initialize buffers in case attach_storage is called between builds
        self._n1_buffer = PPMTTrie(name="universal_buffer")
        self._n2_buffer = PPMTTrie(name=f"class_buffer:{self.asset_class}")

    def ensure_shared_pools(self, storage) -> dict:
        """
        v0.41.0 FIX-1B: Check if N1 and N2 shared pools exist and have data.

        Call this after ``attach_storage(storage)`` and ``build()`` to verify
        that the cross-asset pools were actually populated.  Returns a dict
        with pool status that callers can log or act on.

        Returns:
            Dict with keys ``n1_universal`` and ``n2_class``, each containing
            ``exists`` (bool) and ``pattern_count`` (int).
        """
        status = {
            "n1_universal": {"exists": False, "pattern_count": 0},
            "n2_class": {"exists": False, "pattern_count": 0},
        }
        try:
            n1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
            if n1 is not None:
                status["n1_universal"] = {
                    "exists": True,
                    "pattern_count": n1.pattern_count,
                }
        except Exception as e:
            logger.warning(f"ensure_shared_pools: N1 check failed: {e}")

        try:
            n2_key = class_pool_key(self.asset_class)
            n2 = storage.load_trie(n2_key, "n2")
            if n2 is not None:
                status["n2_class"] = {
                    "exists": True,
                    "pattern_count": n2.pattern_count,
                }
        except Exception as e:
            logger.warning(f"ensure_shared_pools: N2 check failed: {e}")

        if not status["n1_universal"]["exists"]:
            logger.warning("ensure_shared_pools: N1 universal pool is MISSING")
        if not status["n2_class"]["exists"]:
            logger.warning(
                f"ensure_shared_pools: N2 class pool for {self.asset_class} is MISSING"
            )

        return status

    @staticmethod
    def _infer_weight_profile(asset_class: str) -> str:
        """Infer the weight profile from asset class."""
        profile_map = {
            "blue_chip": "blue_chip",
            "large_cap": "default",
            "mid_cap": "default",
            "defi": "default",
            "meme": "meme",
            "new_launch": "new_launch",
        }
        return profile_map.get(asset_class, "default")

    @staticmethod
    def _detect_simple_regime(window_df: pd.DataFrame) -> str:
        """
        Detect simple market regime from a window of OHLCV data.

        v0.38.8: DEPRECATED — delegates to RegimeDetector.detect_simple().
        Kept as a thin static wrapper for backwards compatibility with
        any external callers. The thresholds (0.08 vol, 0.02 move) now
        live in RegimeThresholds (core/thresholds.py) and are preserved
        verbatim, so behaviour is identical to v0.38.7.

        New code should use:
            engine.regime_detector.detect_simple(window_df)
        instead of:
            PPMT._detect_simple_regime(window_df)

        Classification:
        - trending_up:   move > 0.02 (2%+ up)
        - trending_down: move < -0.02 (2%+ down)
        - volatile:      range/entry > 0.08 (8%+ range)
        - ranging:       none of the above
        """
        return RegimeDetector().detect_simple(window_df)

    def set_tries(
        self,
        trie_n1: PPMTTrie,
        trie_n2: PPMTTrie,
        trie_n3: PPMTTrie,
        trie_n4: PPMTTrie,
    ) -> None:
        """
        Inject pre-built Tries into the engine.

        Used by PaperTrader to load serialized Tries from storage
        instead of building new ones from scratch.

        Args:
            trie_n1: Universal Trie
            trie_n2: Asset Class Trie
            trie_n3: Per-Asset Trie
            trie_n4: Per-Asset+Regime Trie
        """
        self.trie_n1 = trie_n1
        self.trie_n2 = trie_n2
        self.trie_n3 = trie_n3
        self.trie_n4 = trie_n4

    def set_regime(self, regime: str) -> None:
        """Set the current market regime for N4 Trie selection.

        v0.40.2 FIX-1: N4 is now a RegimePartitionedTrie. Setting the
        regime routes ALL subsequent N4 search/match operations to the
        sub-trie for that regime. This is what makes N4 actually carry
        regime-specific information (vs. before, when N4 was a plain
        PPMTTrie with the same data as N1/N2/N3).
        """
        self._current_regime = regime
        # Propagate to N4's wrapper so search/match go to the right sub-trie
        if isinstance(self.trie_n4, RegimePartitionedTrie):
            self.trie_n4.set_current_regime(regime)

    def _get_btc_context(self, btc_recent_candles: Optional[pd.DataFrame] = None) -> str:
        """v0.48.0 (FASE 2B): Classify recent BTC price action into 3 states.

        Computes the simple slope of the last 20 BTC close prices and
        classifies as:
          - "btc_bull": slope > 0.0005 (BTC is trending up)
          - "btc_bear": slope < -0.0005 (BTC is trending down)
          - "btc_neutral": otherwise (BTC is ranging)

        The 0.0005 threshold is calibrated for 1m candles: BTC typically
        moves ~0.05% per minute, so 5x that in slope indicates a clear
        directional move over 20 candles.

        Args:
            btc_recent_candles: DataFrame with 'close' column (at least 20 rows).
                If None or too short, returns "btc_neutral".

        Returns:
            One of: "btc_bull", "btc_bear", "btc_neutral"
        """
        if btc_recent_candles is None or len(btc_recent_candles) < 20:
            return "btc_neutral"

        closes = btc_recent_candles["close"].values[-20:]
        if len(closes) < 20 or closes[0] == 0:
            return "btc_neutral"

        # Simple linear slope: (last - first) / (first * N)
        # Normalized by entry price so it's comparable across price levels
        slope = (closes[-1] - closes[0]) / (closes[0] * len(closes))

        if slope > 0.0005:
            return "btc_bull"
        elif slope < -0.0005:
            return "btc_bear"
        else:
            return "btc_neutral"

    def _encode(self, encoder, df: pd.DataFrame) -> list[str | tuple[str, str]]:
        """Encode OHLCV data and return symbols directly.

        v0.43.0: N1 encoder is SAXEncoder → returns list[str].
        N2/N3/N4 encoders are SAXDualEncoder → returns list[tuple[str, str]].
        The Trie handles both key types natively.
        """
        return encoder.encode(df)

    def encode_all_levels(self, df: pd.DataFrame) -> dict[str, list]:
        """Encode OHLCV data with ALL per-level encoders.

        v0.43.0: Returns dict with keys 'n1', 'n2', 'n3', 'n4' containing
        the per-level symbol sequences. N1 is list[str], N2/N3/N4 are
        list[tuple[str, str]] when dual_sax=True.

        v0.48.0 (FASE 2A): Each level uses its own window_size, so the
        returned lists may have DIFFERENT lengths. N1 with W=60 produces
        fewer symbols than N3 with W=20 for the same data.

        This is the public API for OOS replay and real-time encoding.
        """
        return {
            "n1": self._encode(self.sax_n1, df),
            "n2": self._encode(self.sax_n2, df),
            "n3": self._encode(self.sax_n3, df),
            "n4": self._encode(self.sax_n4, df),
        }

    def encode_pattern_per_level(self, df: pd.DataFrame, pattern_length: int = None) -> dict[str, list]:
        """Encode OHLCV data and return the LAST pattern_length symbols per level.

        v0.47.0: Used by the learning loop to capture the exact per-level
        pattern at the moment of entry. Each level may produce different
        symbol types (strings for N1/N2, tuples for N3/N4), so we return
        the last `pattern_length` symbols from each level's full encoding.

        v0.48.0 (FASE 2A): Uses per-level pattern_length (self.pl_n1 etc.)
        instead of a single global pattern_length. This means N1/N2 return
        the last 5 symbols, while N3/N4 return the last 4.

        Args:
            df: DataFrame with OHLCV columns (at least max(window_size * pattern_length) rows)
            pattern_length: DEPRECATED — ignored, per-level lengths are used instead.
                Kept for API backwards compatibility.

        Returns:
            Dict with keys 'n1', 'n2', 'n3', 'n4' containing the last
            pl_* symbols from each level's encoding.
        """
        full = self.encode_all_levels(df)
        return {
            "n1": full["n1"][-self.pl_n1:] if len(full["n1"]) >= self.pl_n1 else full["n1"],
            "n2": full["n2"][-self.pl_n2:] if len(full["n2"]) >= self.pl_n2 else full["n2"],
            "n3": full["n3"][-self.pl_n3:] if len(full["n3"]) >= self.pl_n3 else full["n3"],
            "n4": full["n4"][-self.pl_n4:] if len(full["n4"]) >= self.pl_n4 else full["n4"],
        }

    def build(self, df: pd.DataFrame, pattern_length: int = 5) -> int:
        """
        Build the 4-level Trie from historical OHLCV data.

        Processes the DataFrame into SAX symbols, then creates
        overlapping pattern sequences and inserts them into all
        4 Trie levels with Block Lifecycle Metadata.

        v0.48.0 (FASE 2A): Each level uses its own window_size and
        pattern_length (self.pl_n1 etc.), so levels are built independently.
        Metadata (move_pct, won, drawdown, regime) is computed per-level
        based on each level's own candle-to-symbol mapping.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            pattern_length: DEPRECATED — per-level lengths from LEVEL_PATTERN_CONFIG
                are used instead. Kept for API backwards compatibility.

        Returns:
            Number of patterns inserted (sum across all levels)
        """
        # Encode with each level's SAX encoder.
        # v0.48.0 (FASE 2A): Each level has its own window_size, so symbol
        # lists may have DIFFERENT lengths. We build each level independently.
        symbols_n1 = self._encode(self.sax_n1, df)
        symbols_n2 = self._encode(self.sax_n2, df)
        symbols_n3 = self._encode(self.sax_n3, df)
        symbols_n4 = self._encode(self.sax_n4, df)

        # Build each level independently with its own pattern_length
        count = 0
        level_configs = [
            ("n1", symbols_n1, self.pl_n1, self.sax_n1.window_size),
            ("n2", symbols_n2, self.pl_n2, self.sax_n2.window_size),
            ("n3", symbols_n3, self.pl_n3, self.sax_n3.window_size),
            ("n4", symbols_n4, self.pl_n4, self.sax_n4.window_size),
        ]

        for level_name, symbols, pl, w_size in level_configs:
            if len(symbols) < pl:
                logger.warning(
                    f"build(): {level_name} has {len(symbols)} symbols, "
                    f"need >= {pl} (pattern_length). Skipping."
                )
                continue

            for i in range(len(symbols) - pl):
                pattern = symbols[i:i + pl]

                # Next symbol for continuation
                next_sym = symbols[i + pl] if i + pl < len(symbols) else None

                # Next 3 symbols for forward sequences
                next_3 = tuple(symbols[i + pl : i + pl + 3]) if i + pl + 3 <= len(symbols) else None

                # Map symbol index to candle range
                start_candle = i * w_size
                end_candle = (i + pl) * w_size

                if end_candle > len(df):
                    break

                window_df = df.iloc[start_candle:end_candle]

                # Compute metadata from actual prices
                entry_price = window_df["close"].iloc[0]
                exit_price = window_df["close"].iloc[-1]
                move_pct = ((exit_price - entry_price) / entry_price) * 100.0

                high = window_df["high"].max()
                low = window_df["low"].min()
                drawdown_pct = ((low - entry_price) / entry_price) * 100.0
                favorable_pct = ((high - entry_price) / entry_price) * 100.0

                duration = len(window_df)

                # Compute won from post-pattern candles
                # Look up existing node for SL/TP (use N3 as reference)
                existing_node = None
                if level_name == "n3" and hasattr(self, 'trie_n3'):
                    existing_node = self.trie_n3.search(pattern)
                elif level_name == "n3":
                    existing_node = None

                if existing_node is not None and existing_node.metadata.historical_count > 0:
                    existing_meta = existing_node.metadata
                    sl_pct_for_outcome = abs(existing_meta.max_drawdown_pct) * 1.5
                    tp_pct_for_outcome = max(
                        abs(existing_meta.expected_move_pct),
                        existing_meta.max_favorable_pct,
                    ) * 1.0
                    hist_count_for_outcome = existing_node.metadata.historical_count
                else:
                    sl_pct_for_outcome = None
                    tp_pct_for_outcome = None
                    hist_count_for_outcome = 0

                # Post-pattern window for SL/TP simulation
                post_pattern_window_size = pl * w_size
                post_pattern_start = end_candle
                post_pattern_end = min(end_candle + post_pattern_window_size, len(df))
                post_pattern_df = df.iloc[post_pattern_start:post_pattern_end]
                entry_price_for_outcome = window_df["close"].iloc[-1]

                won = compute_outcome_won(
                    window_df=post_pattern_df,
                    entry_price=entry_price_for_outcome,
                    move_pct=move_pct,
                    sl_pct=sl_pct_for_outcome,
                    tp_pct=tp_pct_for_outcome,
                    historical_count=hist_count_for_outcome,
                )

                # Detect regime
                regime = self.regime_detector.detect_simple(window_df, timeframe=self.timeframe)

                # Insert into appropriate trie
                if level_name == "n1":
                    if self._storage is not None:
                        self._n1_buffer.insert_with_observations(
                            symbols=pattern, move_pct=move_pct,
                            drawdown_pct=drawdown_pct, favorable_pct=favorable_pct,
                            duration=duration, won=won, next_symbol=next_sym,
                            regime=regime, next_3_symbols=next_3,
                        )
                    else:
                        self.trie_n1.insert_with_observations(
                            symbols=pattern, move_pct=move_pct,
                            drawdown_pct=drawdown_pct, favorable_pct=favorable_pct,
                            duration=duration, won=won, next_symbol=next_sym,
                            regime=regime, next_3_symbols=next_3,
                        )
                elif level_name == "n2":
                    if self._storage is not None:
                        self._n2_buffer.insert_with_observations(
                            symbols=pattern, move_pct=move_pct,
                            drawdown_pct=drawdown_pct, favorable_pct=favorable_pct,
                            duration=duration, won=won, next_symbol=next_sym,
                            regime=regime, next_3_symbols=next_3,
                        )
                    else:
                        self.trie_n2.insert_with_observations(
                            symbols=pattern, move_pct=move_pct,
                            drawdown_pct=drawdown_pct, favorable_pct=favorable_pct,
                            duration=duration, won=won, next_symbol=next_sym,
                            regime=regime, next_3_symbols=next_3,
                        )
                elif level_name == "n3":
                    self.trie_n3.insert_with_observations(
                        symbols=pattern, move_pct=move_pct,
                        drawdown_pct=drawdown_pct, favorable_pct=favorable_pct,
                        duration=duration, won=won, next_symbol=next_sym,
                        regime=regime, next_3_symbols=next_3,
                    )
                elif level_name == "n4":
                    self.trie_n4.insert_with_observations(
                        symbols=pattern, move_pct=move_pct,
                        drawdown_pct=drawdown_pct, favorable_pct=favorable_pct,
                        duration=duration, won=won, next_symbol=next_sym,
                        regime=regime, next_3_symbols=next_3,
                    )

                count += 1

        self._total_patterns_built += count

        # v0.40.3 FIX-1B: When storage is attached, flush the in-memory N1/N2
        # buffers to the cross-asset shared pools (one storage round-trip per
        # pool, not per observation). Also persist N3 (per-symbol) and N4
        # (per-symbol+regime) so other PPMT instances can load them via
        # load_all_tries(symbol, asset_class).
        if self._storage is not None and count > 0:
            # Load existing pools, merge buffers, save back.
            def _top_regime(regime_dist) -> Optional[str]:
                """Return the most common regime from a dict-like, or None."""
                if not regime_dist:
                    return None
                # regime_dist may be a Counter or a plain dict
                if hasattr(regime_dist, "most_common"):
                    return regime_dist.most_common(1)[0][0]
                # plain dict: sort by value
                return max(regime_dist.items(), key=lambda x: x[1])[0]

            # N1 universal pool
            existing_n1 = self._storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
            if existing_n1 is None:
                merged_n1 = self._n1_buffer
            else:
                # Merge: walk our buffer's patterns and insert into existing.
                # CRITICAL FIX: Insert historical_count observations (not just 1)
                # to preserve the observation frequency from the buffer.
                # Without this, a pattern observed 50 times in the buffer
                # only contributes 1 observation to the pool, destroying
                # Bayesian confidence.
                merged_n1 = existing_n1
                for pat, node in self._n1_buffer.get_all_patterns(min_count=1):
                    meta = node.metadata
                    n_obs = meta.historical_count
                    n_wins = int(meta.win_rate * n_obs)
                    for obs_i in range(n_obs):
                        merged_n1.insert_with_observations(
                            symbols=list(pat),
                            move_pct=meta.expected_move_pct,
                            drawdown_pct=meta.max_drawdown_pct,
                            favorable_pct=meta.max_favorable_pct,
                            duration=int(meta.avg_duration),
                            won=obs_i < n_wins,  # Distribute wins first
                            next_symbol=None,
                            regime=_top_regime(meta.regime_distribution),
                        )
            self._storage.save_trie(UNIVERSAL_POOL_KEY, "n1", merged_n1)

            # N2 class-shared pool
            pool_key = class_pool_key(self.asset_class)
            existing_n2 = self._storage.load_trie(pool_key, "n2")
            if existing_n2 is None:
                merged_n2 = self._n2_buffer
            else:
                # CRITICAL FIX: Same as N1 — preserve observation counts
                merged_n2 = existing_n2
                for pat, node in self._n2_buffer.get_all_patterns(min_count=1):
                    meta = node.metadata
                    n_obs = meta.historical_count
                    n_wins = int(meta.win_rate * n_obs)
                    for obs_i in range(n_obs):
                        merged_n2.insert_with_observations(
                            symbols=list(pat),
                            move_pct=meta.expected_move_pct,
                            drawdown_pct=meta.max_drawdown_pct,
                            favorable_pct=meta.max_favorable_pct,
                            duration=int(meta.avg_duration),
                            won=obs_i < n_wins,
                            next_symbol=None,
                            regime=_top_regime(meta.regime_distribution),
                        )
            self._storage.save_trie(pool_key, "n2", merged_n2)

            # N3 (per-symbol) — persist local trie
            self._storage.save_trie(self.symbol, "n3", self.trie_n3)
            # N4 (per-symbol + regime) — persist RegimePartitionedTrie
            self._storage.save_trie(self.symbol, "n4", self.trie_n4)

            # Reset buffers for next build() call
            self._n1_buffer = PPMTTrie(name="universal_buffer")
            self._n2_buffer = PPMTTrie(name=f"class_buffer:{self.asset_class}")

            # v0.41.0 FIX-1B: Verify pools were actually saved to storage.
            # This catches silent save failures that would otherwise go
            # undetected until N1/N2 load as None in paper_trader/realtime.
            try:
                saved_n1 = self._storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
                if saved_n1 is not None and saved_n1.pattern_count > 0:
                    logger.info(
                        f"N1 universal pool verified: {saved_n1.pattern_count} patterns"
                    )
                else:
                    logger.warning(
                        "N1 universal pool save verification FAILED — "
                        "pool is empty or missing after build!"
                    )

                n2_key = class_pool_key(self.asset_class)
                saved_n2 = self._storage.load_trie(n2_key, "n2")
                if saved_n2 is not None and saved_n2.pattern_count > 0:
                    logger.info(
                        f"N2 class pool ({self.asset_class}) verified: "
                        f"{saved_n2.pattern_count} patterns"
                    )
                else:
                    logger.warning(
                        f"N2 class pool ({self.asset_class}) save verification FAILED — "
                        f"pool is empty or missing after build!"
                    )
            except Exception as e:
                logger.warning(f"Pool verification error (non-fatal): {e}")

        return count

    def match_raw(
        self,
        current_symbols: list[str],
        current_price: float = 0.0,
        current_symbols_n1: Optional[list[str]] = None,
        current_symbols_n2: Optional[list[str]] = None,
        current_symbols_n3: Optional[list[str]] = None,
        current_symbols_n4: Optional[list[str]] = None,
        recent_candles: Optional[pd.DataFrame] = None,
        btc_recent_candles: Optional[pd.DataFrame] = None,
    ) -> PPMTResult:
        """
        Raw 4-level match without signal generation.

        Used by PaperTrader to compute weighted confidence across all
        4 trie levels. Returns match results with confidence values
        but does NOT generate a trading signal (that's done by the
        PaperTrader's own entry logic).

        v0.47.0: When `recent_candles` (DataFrame with OHLCV columns) is
        provided, each level's SAX encoder re-encodes the data independently.
        This fixes the string/tuple mismatch: N3/N4 use SAXDualEncoder which
        produces tuples like ('a','x'), but realtime.py was passing plain
        strings from pattern_buffer. With recent_candles, the encoders produce
        the correct symbol type for each level automatically.

        When recent_candles is None, falls back to current_symbols for all
        levels (backwards compatibility for old tests).

        Args:
            current_symbols: Current SAX symbol sequence (backwards compat)
            current_price: Current market price (unused, for compatibility)
            current_symbols_n1: N1-encoded symbols (if None, uses current_symbols)
            current_symbols_n2: N2-encoded symbols (if None, uses current_symbols)
            current_symbols_n3: N3-encoded symbols (if None, uses current_symbols)
            current_symbols_n4: N4-encoded symbols (if None, uses current_symbols)
            recent_candles: DataFrame with OHLCV columns. When provided, each
                level's encoder re-encodes the data, producing the correct
                symbol type (strings for N1/N2, tuples for N3/N4).

        Returns:
            PPMTResult with match details and weighted confidence
        """
        start_time = time.perf_counter()

        # v0.47.0: When recent_candles is provided, encode with each level's
        # encoder to get the correct symbol types. This fixes the bug where
        # N3/N4 (SAXDualEncoder) expect tuples but received strings.
        # v0.47.1: Check against window_size * pattern_length (not just window_size)
        # so we get enough symbols for a meaningful match.
        # v0.48.0 (FASE 2A): Use per-level min_rows since each level has its
        # own window_size and pattern_length.
        _min_rows = min(
            self.sax_n1.window_size * self.pl_n1,
            self.sax_n2.window_size * self.pl_n2,
            self.sax_n3.window_size * self.pl_n3,
            self.sax_n4.window_size * self.pl_n4,
        )
        if recent_candles is not None and len(recent_candles) >= _min_rows:
            try:
                encoded = self.encode_all_levels(recent_candles)
                # v0.48.0 (FASE 2A): Truncate each level to its own pattern_length.
                # N1/N2 use pl=5, N3/N4 use pl=4. The trie stores patterns of
                # length pl_*, so we must match that exact length for search.
                syms_n1 = encoded["n1"][-self.pl_n1:] if len(encoded["n1"]) >= self.pl_n1 else encoded["n1"]
                syms_n2 = encoded["n2"][-self.pl_n2:] if len(encoded["n2"]) >= self.pl_n2 else encoded["n2"]
                syms_n3 = encoded["n3"][-self.pl_n3:] if len(encoded["n3"]) >= self.pl_n3 else encoded["n3"]
                syms_n4 = encoded["n4"][-self.pl_n4:] if len(encoded["n4"]) >= self.pl_n4 else encoded["n4"]
            except Exception:
                # Fallback to current_symbols if encoding fails
                syms_n1 = current_symbols_n1 if current_symbols_n1 is not None else current_symbols
                syms_n2 = current_symbols_n2 if current_symbols_n2 is not None else current_symbols
                syms_n3 = current_symbols_n3 if current_symbols_n3 is not None else current_symbols
                syms_n4 = current_symbols_n4 if current_symbols_n4 is not None else current_symbols
        else:
            # FASE 1 Tarea 1.1: Use per-level matchers with per-level symbols.
            # Each matcher knows its level's alphabet size for fuzzy distance.
            syms_n1 = current_symbols_n1 if current_symbols_n1 is not None else current_symbols
            syms_n2 = current_symbols_n2 if current_symbols_n2 is not None else current_symbols
            syms_n3 = current_symbols_n3 if current_symbols_n3 is not None else current_symbols
            syms_n4 = current_symbols_n4 if current_symbols_n4 is not None else current_symbols

        # Search all 4 levels with per-level matchers
        n1_match = self.matcher_n1.best_match(self.trie_n1, syms_n1)
        n2_match = self.matcher_n2.best_match(self.trie_n2, syms_n2)
        n3_match = self.matcher_n3.best_match(self.trie_n3, syms_n3)
        n4_match = self.matcher_n4.best_match(self.trie_n4, syms_n4)

        # Get confidence from each level (match_raw)
        n1_conf = n1_match.node.metadata.confidence if n1_match.node else 0.0
        n2_conf = n2_match.node.metadata.confidence if n2_match.node else 0.0
        n3_conf = n3_match.node.metadata.confidence if n3_match.node else 0.0
        n4_conf = n4_match.node.metadata.confidence if n4_match.node else 0.0

        # v0.41.0 (FASE 2, Tarea 2.4): Apply time decay to each level's
        # confidence using the node's last_seen_timestamp. Patterns that
        # haven't been observed recently get reduced confidence.
        from ppmt.engine.weights import apply_time_decay
        n1_conf = apply_time_decay(
            n1_conf, n1_match.node.metadata.last_seen_timestamp
        ) if n1_match.node and n1_conf > 0 else n1_conf
        n2_conf = apply_time_decay(
            n2_conf, n2_match.node.metadata.last_seen_timestamp
        ) if n2_match.node and n2_conf > 0 else n2_conf
        n3_conf = apply_time_decay(
            n3_conf, n3_match.node.metadata.last_seen_timestamp
        ) if n3_match.node and n3_conf > 0 else n3_conf
        n4_conf = apply_time_decay(
            n4_conf, n4_match.node.metadata.last_seen_timestamp
        ) if n4_match.node and n4_conf > 0 else n4_conf

        # v0.41.0 (FASE 3, Tarea 3.1): Apply safe default weights for
        # immature local tries. If N3 has < 20 patterns or N4 has < 10,
        # redistribute their weight to N1/N2 which have cross-asset data.
        # This prevents unreliable N3/N4 from dominating confidence.
        # v0.43.0: Also pass N2 avg obs density so sparse N2 pools shift
        # weight to the dense N1 universal pool.
        n3_count = self.trie_n3.pattern_count
        n4_count = self.trie_n4.pattern_count if hasattr(self.trie_n4, 'pattern_count') else 0

        # Compute N2 average obs/node for density-aware weight redistribution
        n2_avg_obs = 0.0
        if self.trie_n2.pattern_count > 0:
            # Compute avg obs/node from actual trie data (historical_count)
            total_obs = 0
            node_count = 0
            for _pat, node in self.trie_n2.get_all_patterns(min_count=1):
                total_obs += node.metadata.historical_count
                node_count += 1
            if node_count > 0:
                n2_avg_obs = total_obs / node_count

        safe_weights = AdaptiveWeights.from_profile(self.weights.profile)
        safe_weights.n1_universal = self.weights.n1_universal
        safe_weights.n2_asset_class = self.weights.n2_asset_class
        safe_weights.n3_per_asset = self.weights.n3_per_asset
        safe_weights.n4_per_asset_regime = self.weights.n4_per_asset_regime
        safe_weights.safe_default_weights(
            n3_pattern_count=n3_count,
            n4_pattern_count=n4_count,
            n2_avg_obs=n2_avg_obs,
        )

        # Compute weighted confidence
        weighted_conf = safe_weights.compute_weighted_confidence(
            n1_confidence=n1_conf,
            n2_confidence=n2_conf,
            n3_confidence=n3_conf,
            n4_confidence=n4_conf,
        )

        # v0.48.0 (FASE 2B): N5 — BTC Context Level (1m only).
        # N5 blends BTC-context-aware confidence at 5-10% weight into the
        # final confidence. Only active when self.trie_n5 exists (1m TF).
        n5_conf = 0.0
        n5_match = None
        if self.trie_n5 is not None and self.sax_n5 is not None and recent_candles is not None:
            # Encode with N5's SAX encoder
            try:
                n5_syms = self._encode(self.sax_n5, recent_candles)
                n5_syms = n5_syms[-self.pl_n5:] if len(n5_syms) >= self.pl_n5 else n5_syms

                # Get BTC context and route to the right sub-trie
                btc_context = self._get_btc_context(btc_recent_candles)
                self.trie_n5.set_current_regime(btc_context)

                # Match
                n5_match = self.matcher_n5.best_match(self.trie_n5, n5_syms)
                n5_conf = n5_match.node.metadata.confidence if n5_match.node else 0.0

                # Apply time decay
                n5_conf = apply_time_decay(
                    n5_conf, n5_match.node.metadata.last_seen_timestamp
                ) if n5_match.node and n5_conf > 0 else n5_conf

                # Blend: N5 gets 7.5% weight (low — it's a context signal, not primary)
                n5_weight = 0.075
                n5_count = self.trie_n5.pattern_count if hasattr(self.trie_n5, 'pattern_count') else 0
                if n5_count < 10:
                    # Too sparse to trust — reduce weight
                    n5_weight = n5_weight * (n5_count / 10.0)
                weighted_conf = weighted_conf * (1.0 - n5_weight) + n5_conf * n5_weight
            except Exception:
                pass  # N5 failure is non-fatal

        search_time = (time.perf_counter() - start_time) * 1000.0

        return PPMTResult(
            signal=Signal(signal_type=SignalType.NO_SIGNAL, symbol=self.symbol),
            n1_match=n1_match,
            n2_match=n2_match,
            n3_match=n3_match,
            n4_match=n4_match,
            n1_confidence=n1_conf,
            n2_confidence=n2_conf,
            n3_confidence=n3_conf,
            n4_confidence=n4_conf,
            weighted_confidence=weighted_conf,
            sax_symbols=current_symbols,
            search_time_ms=search_time,
        )

    def match(
        self,
        current_symbols: list[str],
        current_price: float,
        is_in_position: bool = False,
        entry_price: Optional[float] = None,
        current_symbols_n1: Optional[list[str]] = None,
        current_symbols_n2: Optional[list[str]] = None,
        current_symbols_n3: Optional[list[str]] = None,
        current_symbols_n4: Optional[list[str]] = None,
        recent_candles: Optional[pd.DataFrame] = None,
    ) -> PPMTResult:
        """
        Match current SAX sequence against all 4 Trie levels.

        Performs parallel search across N1-N4, computes adaptive
        weighted confidence, and generates a trading signal.

        This is the main real-time method called on each new candle.

        v0.47.0: When `recent_candles` is provided, each level's encoder
        re-encodes the data independently, fixing the string/tuple mismatch.

        Args:
            current_symbols: Current SAX symbol sequence (backwards compat)
            current_price: Current market price
            is_in_position: Whether we already have an open position
            entry_price: Entry price of current position (if any)
            current_symbols_n1: N1-encoded symbols (if None, uses current_symbols)
            current_symbols_n2: N2-encoded symbols (if None, uses current_symbols)
            current_symbols_n3: N3-encoded symbols (if None, uses current_symbols)
            current_symbols_n4: N4-encoded symbols (if None, uses current_symbols)
            recent_candles: DataFrame with OHLCV columns. When provided, each
                level's encoder re-encodes the data, producing the correct
                symbol type (strings for N1/N2, tuples for N3/N4).

        Returns:
            PPMTResult with signal and matching details
        """
        start_time = time.perf_counter()

        # v0.47.0: When recent_candles is provided, encode with each level's
        # encoder to get the correct symbol types.
        # v0.48.0 (FASE 2A): Per-level min_rows and per-level truncation.
        _min_rows = min(
            self.sax_n1.window_size * self.pl_n1,
            self.sax_n2.window_size * self.pl_n2,
            self.sax_n3.window_size * self.pl_n3,
            self.sax_n4.window_size * self.pl_n4,
        )
        if recent_candles is not None and len(recent_candles) >= _min_rows:
            try:
                encoded = self.encode_all_levels(recent_candles)
                # v0.48.0 (FASE 2A): Truncate each level to its own pattern_length.
                syms_n1 = encoded["n1"][-self.pl_n1:] if len(encoded["n1"]) >= self.pl_n1 else encoded["n1"]
                syms_n2 = encoded["n2"][-self.pl_n2:] if len(encoded["n2"]) >= self.pl_n2 else encoded["n2"]
                syms_n3 = encoded["n3"][-self.pl_n3:] if len(encoded["n3"]) >= self.pl_n3 else encoded["n3"]
                syms_n4 = encoded["n4"][-self.pl_n4:] if len(encoded["n4"]) >= self.pl_n4 else encoded["n4"]
            except Exception:
                syms_n1 = current_symbols_n1 if current_symbols_n1 is not None else current_symbols
                syms_n2 = current_symbols_n2 if current_symbols_n2 is not None else current_symbols
                syms_n3 = current_symbols_n3 if current_symbols_n3 is not None else current_symbols
                syms_n4 = current_symbols_n4 if current_symbols_n4 is not None else current_symbols
        else:
            # FASE 1 Tarea 1.1: Use per-level matchers with per-level symbols.
            syms_n1 = current_symbols_n1 if current_symbols_n1 is not None else current_symbols
            syms_n2 = current_symbols_n2 if current_symbols_n2 is not None else current_symbols
            syms_n3 = current_symbols_n3 if current_symbols_n3 is not None else current_symbols
            syms_n4 = current_symbols_n4 if current_symbols_n4 is not None else current_symbols

        # Search all 4 levels with per-level matchers
        n1_match = self.matcher_n1.best_match(self.trie_n1, syms_n1)
        n2_match = self.matcher_n2.best_match(self.trie_n2, syms_n2)
        n3_match = self.matcher_n3.best_match(self.trie_n3, syms_n3)
        n4_match = self.matcher_n4.best_match(self.trie_n4, syms_n4)

        # Get confidence from each level (match)
        n1_conf = n1_match.node.metadata.confidence if n1_match.node else 0.0
        n2_conf = n2_match.node.metadata.confidence if n2_match.node else 0.0
        n3_conf = n3_match.node.metadata.confidence if n3_match.node else 0.0
        n4_conf = n4_match.node.metadata.confidence if n4_match.node else 0.0

        # v0.41.0 (FASE 2, Tarea 2.4): Apply time decay to each level's
        # confidence using the node's last_seen_timestamp.
        from ppmt.engine.weights import apply_time_decay
        n1_conf = apply_time_decay(
            n1_conf, n1_match.node.metadata.last_seen_timestamp
        ) if n1_match.node and n1_conf > 0 else n1_conf
        n2_conf = apply_time_decay(
            n2_conf, n2_match.node.metadata.last_seen_timestamp
        ) if n2_match.node and n2_conf > 0 else n2_conf
        n3_conf = apply_time_decay(
            n3_conf, n3_match.node.metadata.last_seen_timestamp
        ) if n3_match.node and n3_conf > 0 else n3_conf
        n4_conf = apply_time_decay(
            n4_conf, n4_match.node.metadata.last_seen_timestamp
        ) if n4_match.node and n4_conf > 0 else n4_conf

        # v0.41.0 (FASE 3, Tarea 3.1): Apply safe default weights for
        # immature local tries. If N3 has < 20 patterns or N4 has < 10,
        # redistribute their weight to N1/N2 which have cross-asset data.
        n3_count = self.trie_n3.pattern_count
        n4_count = self.trie_n4.pattern_count if hasattr(self.trie_n4, 'pattern_count') else 0
        safe_weights = AdaptiveWeights.from_profile(self.weights.profile)
        safe_weights.n1_universal = self.weights.n1_universal
        safe_weights.n2_asset_class = self.weights.n2_asset_class
        safe_weights.n3_per_asset = self.weights.n3_per_asset
        safe_weights.n4_per_asset_regime = self.weights.n4_per_asset_regime
        safe_weights.safe_default_weights(n3_pattern_count=n3_count, n4_pattern_count=n4_count)

        # Compute weighted confidence
        weighted_conf = safe_weights.compute_weighted_confidence(
            n1_confidence=n1_conf,
            n2_confidence=n2_conf,
            n3_confidence=n3_conf,
            n4_confidence=n4_conf,
        )

        # Determine best matching level
        best_level = "n1"
        best_conf = n1_conf
        best_match = n1_match

        for level_name, conf, match in [
            ("n2", n2_conf, n2_match),
            ("n3", n3_conf, n3_match),
            ("n4", n4_conf, n4_match),
        ]:
            if conf > best_conf:
                best_conf = conf
                best_level = level_name
                best_match = match

        # Generate signal
        signal: Signal

        if is_in_position and entry_price is not None:
            # Check continuation using ALL trie levels (not just n3)
            # v0.6.5: Fuzzy Pattern Break — graduated continuation across levels
            pnl_pct = ((current_price - entry_price) / entry_price) * 100.0

            last_sym_n1 = syms_n1[-1] if syms_n1 else ""
            last_sym_n2 = syms_n2[-1] if syms_n2 else ""
            last_sym_n3 = syms_n3[-1] if syms_n3 else ""
            last_sym_n4 = syms_n4[-1] if syms_n4 else ""

            # Check continuation at all 4 levels, pick best break score
            # FASE 1 Tarea 1.1: use per-level matchers for distance computation
            cont_results = [
                self.matcher_n1.check_continuation(
                    self.trie_n1, syms_n1[:-1], last_sym_n1
                ),
                self.matcher_n2.check_continuation(
                    self.trie_n2, syms_n2[:-1], last_sym_n2
                ),
                self.matcher_n3.check_continuation(
                    self.trie_n3, syms_n3[:-1], last_sym_n3
                ),
                self.matcher_n4.check_continuation(
                    self.trie_n4, syms_n4[:-1], last_sym_n4
                ),
            ]

            # Select the continuation result with the highest pattern_break_score
            best_cont = max(cont_results, key=lambda c: c.pattern_break_score)

            signal = self.signal_generator.generate_continuation_signal(
                continuation_result=best_cont,
                current_price=current_price,
                entry_price=entry_price,
                current_pnl_pct=pnl_pct,
                symbol=self.symbol,
            )
        else:
            # Look for entry signal
            signal = self.signal_generator.generate_entry_signal(
                match_result=best_match,
                symbol=self.symbol,
                current_price=current_price,
                confidence=weighted_conf,
                trie_level=best_level,
            ) or Signal(signal_type=SignalType.NO_SIGNAL, symbol=self.symbol)

        search_time = (time.perf_counter() - start_time) * 1000.0

        return PPMTResult(
            signal=signal,
            n1_match=n1_match,
            n2_match=n2_match,
            n3_match=n3_match,
            n4_match=n4_match,
            n1_confidence=n1_conf,
            n2_confidence=n2_conf,
            n3_confidence=n3_conf,
            n4_confidence=n4_conf,
            weighted_confidence=weighted_conf,
            sax_symbols=current_symbols,
            search_time_ms=search_time,
        )

    def process_new_candle(
        self,
        candle_df: pd.DataFrame,
        current_price: float,
        is_in_position: bool = False,
        entry_price: Optional[float] = None,
        paa_mean: Optional[float] = None,
        paa_std: Optional[float] = None,
    ) -> Optional[PPMTResult]:
        """
        Process a single new candle through the SAX pipeline.

        Incrementally encodes the candle into SAX symbols and
        triggers pattern matching when a new symbol is produced.

        This is the primary method for real-time operation.

        v0.19.1: Fully implemented using StreamingPatternBuffer.
        The streaming buffer maintains a sliding window of SAX symbols
        and automatically provides the current pattern for matching.

        Args:
            candle_df: Single-row DataFrame with OHLCV data
            current_price: Current price (usually close)
            is_in_position: Whether we have an open position
            entry_price: Entry price of current position
            paa_mean: Training PAA mean for consistent incremental encoding
            paa_std: Training PAA std for consistent incremental encoding

        Returns:
            PPMTResult if a new SAX symbol was produced, None otherwise
        """
        from ppmt.engine.buffer import StreamingPatternBuffer

        # FASE 1 Tarea 1.1: Initialize 4 streaming buffers (one per level)
        # Each buffer tracks its own SAX symbol stream for its level's encoder.
        if not hasattr(self, '_streaming_buffer') or self._streaming_buffer is None:
            self._streaming_buffer = StreamingPatternBuffer(
                pattern_length=5,  # default, should match build() pattern_length
                max_buffer_length=0,  # auto
            )
        if not hasattr(self, '_streaming_buffer_n1') or self._streaming_buffer_n1 is None:
            self._streaming_buffer_n1 = StreamingPatternBuffer(
                pattern_length=5, max_buffer_length=0,
            )
        if not hasattr(self, '_streaming_buffer_n2') or self._streaming_buffer_n2 is None:
            self._streaming_buffer_n2 = StreamingPatternBuffer(
                pattern_length=5, max_buffer_length=0,
            )
        if not hasattr(self, '_streaming_buffer_n4') or self._streaming_buffer_n4 is None:
            self._streaming_buffer_n4 = StreamingPatternBuffer(
                pattern_length=5, max_buffer_length=0,
            )

        buf = self._streaming_buffer  # N3 buffer (primary, backwards compat)
        buf_n1 = self._streaming_buffer_n1
        buf_n2 = self._streaming_buffer_n2
        buf_n4 = self._streaming_buffer_n4

        # Incremental SAX encoding — one per level
        new_symbols_n3, updated_sax_buffer_n3 = self.sax_n3.encode_incremental(
            candle_df, buf.sax_buffer,
            paa_mean=paa_mean, paa_std=paa_std,
        )
        new_symbols_n1, updated_sax_buffer_n1 = self.sax_n1.encode_incremental(
            candle_df, buf_n1.sax_buffer,
            paa_mean=paa_mean, paa_std=paa_std,
        )
        new_symbols_n2, updated_sax_buffer_n2 = self.sax_n2.encode_incremental(
            candle_df, buf_n2.sax_buffer,
            paa_mean=paa_mean, paa_std=paa_std,
        )
        new_symbols_n4, updated_sax_buffer_n4 = self.sax_n4.encode_incremental(
            candle_df, buf_n4.sax_buffer,
            paa_mean=paa_mean, paa_std=paa_std,
        )

        # Update streaming buffers with new symbols
        matchable_n3 = buf.update(new_symbols_n3, updated_sax_buffer_n3)
        buf_n1.update(new_symbols_n1, updated_sax_buffer_n1)
        buf_n2.update(new_symbols_n2, updated_sax_buffer_n2)
        buf_n4.update(new_symbols_n4, updated_sax_buffer_n4)

        if not matchable_n3 or not buf.has_pattern():
            return None

        # Get current patterns for matching (per-level)
        current_pattern_n3 = buf.get_pattern()
        current_pattern_n1 = buf_n1.get_pattern()
        current_pattern_n2 = buf_n2.get_pattern()
        current_pattern_n4 = buf_n4.get_pattern()

        # Run 4-level match with per-level symbols
        result = self.match(
            current_symbols=current_pattern_n3,
            current_price=current_price,
            is_in_position=is_in_position,
            entry_price=entry_price,
            current_symbols_n1=current_pattern_n1 or None,
            current_symbols_n2=current_pattern_n2 or None,
            current_symbols_n3=current_pattern_n3,
            current_symbols_n4=current_pattern_n4 or None,
        )

        # Record match/break in buffer
        if result.signal.signal_type != SignalType.NO_SIGNAL:
            buf.record_match(
                confidence=result.weighted_confidence,
                matched_pattern=current_pattern_n3,
            )

        return result

    def adapt_weights(self) -> None:
        """
        Adapt weights based on current data availability.

        Should be called periodically (e.g., every 100 new candles)
        to ensure weights reflect the current state of each Trie level.
        """
        stats = {}
        for key, trie in [
            ("n1", self.trie_n1),
            ("n2", self.trie_n2),
            ("n3", self.trie_n3),
            ("n4", self.trie_n4),
        ]:
            patterns = trie.get_all_patterns(min_count=1)
            if patterns:
                avg_count = np.mean([
                    node.metadata.historical_count for _, node in patterns
                ])
                avg_wr = np.mean([
                    node.metadata.win_rate for _, node in patterns
                ])
                avg_conf = np.mean([
                    node.metadata.confidence for _, node in patterns
                ])
            else:
                avg_count = 0.0
                avg_wr = 0.0
                avg_conf = 0.0

            stats[key] = LevelStats(
                pattern_count=trie.pattern_count,
                avg_historical_count=avg_count,
                avg_win_rate=avg_wr,
                avg_confidence=avg_conf,
            )

        self.weights.adapt(stats)

    def get_stats(self) -> dict:
        """Get engine statistics."""
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "weights": self.weights.to_dict(),
            "trie_n1": str(self.trie_n1),
            "trie_n2": str(self.trie_n2),
            "trie_n3": str(self.trie_n3),
            "trie_n4": str(self.trie_n4),
            "total_patterns_built": self._total_patterns_built,
            "current_regime": self._current_regime,
        }
