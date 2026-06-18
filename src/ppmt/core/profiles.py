"""
Token Profile & Auto-Calibration Engine

The TokenProfile encapsulates ALL parameters a token needs to operate,
and the CalibrationEngine discovers the best parameters automatically
via mini-backtesting on real data.

Architecture:
  ┌──────────────────────────────────────┐
  │        NEW TOKEN DETECTED            │
  └──────────────┬───────────────────────┘
                 │
        ┌────────▼────────┐
        │  CALIBRATION    │  Mini-backtest: 70% train / 30% OOS
        │  PHASE          │  Grid: alpha x window = 9 combos
        │                 │  Metric: information x repetition
        │  alpha: 3,4,5   │
        │  window: 5,7,10 │
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │  BEST PROFILE   │  Auto-selected from OOS performance
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │  OPERATE        │  Trade with calibrated profile
        │  + RECALIBRATE  │  Every N new candles, re-verify
        └─────────────────┘

This replaces manual per-token parameter tuning with data-driven
auto-calibration. The system discovers what works for each token
based on real OOS performance, not assumptions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ppmt.core.sax import SAXEncoder
from ppmt.core.trie import PPMTTrie


# === Timeframe-Adaptive Alpha Defaults ===
# VALIDATED across 22 token-timeframe combinations:
#   12 tokens @ 1h (alpha=3), 6 tokens @ 5m (alpha=4), 4 tokens @ 1m (alpha=5)
# Key finding: alpha MUST scale with timeframe granularity.
#   - Lower TF = more candles = need more symbols to differentiate patterns
#   - With alpha=3 at 1m, the system generates ZERO trades (all patterns identical)
#   - alpha=5/window=7 was the previous optimal config for 1m, producing 350+ trades
#
# v0.40.8 FIX-13 (2026-06-19): lowered 1m alpha from 5 to 4 based on empirical
# audit on REAL 1m data (50k candles × 8 tokens, see
# docs/AUDIT_TRIE_STATS_1M_REAL_DATA.md). With alpha=5 the trie had:
#   - 2,787 unique patterns but 25.9% singletones
#   - confidence media = 0.13 (BELOW production threshold 0.15)
#   - only 0.05% of patterns had count>=10 (statistical robustness)
# With alpha=4:
#   - 1,022 unique patterns but only 1% singletones
#   - confidence media = 0.22 (ABOVE production threshold 0.15)
#   - 81.6% of patterns pass confidence gate (vs 26.5% with alpha=5)
#   - mean_count per pattern = 6.98 (vs 2.56 with alpha=5)
# Trade-off: lower pattern discrimination, but more repetitions per pattern
# gives the metadata real statistical grounding. SL/TP from compute_sl_tp()
# becomes reliable because it's based on 7+ observations instead of 2.
TIMEFRAME_ALPHA_DEFAULTS = {
    "1m":  {"sax_alphabet_size": 4, "sax_window_size": 7},  # v0.40.8 FIX-13: era 5
    "5m":  {"sax_alphabet_size": 4, "sax_window_size": 7},
    "15m": {"sax_alphabet_size": 4, "sax_window_size": 5},
    "30m": {"sax_alphabet_size": 4, "sax_window_size": 5},
    "1h":  {"sax_alphabet_size": 3, "sax_window_size": 7},
    "4h":  {"sax_alphabet_size": 3, "sax_window_size": 10},
    "1d":  {"sax_alphabet_size": 3, "sax_window_size": 10},
}


# === Default profiles by asset class ===
# These are STARTING POINTS — the CalibrationEngine overrides them
# with data-discovered values when sufficient data is available.

ASSET_CLASS_DEFAULTS = {
    "blue_chip": {
        "sax_alphabet_size": 3,
        "sax_window_size": 10,
        "catastrophic_loss_pct": 0.08,
        "max_position_pct": 0.10,
        "short_allowed": True,
        "short_confidence_multiplier": 1.5,
        "weight_profile": "blue_chip",
        "fuzzy_threshold": 0.85,
        "min_observations_for_trade": 3,
        "min_candles_for_training": 2000,
    },
    "large_cap": {
        "sax_alphabet_size": 4,
        "sax_window_size": 7,
        "catastrophic_loss_pct": 0.10,
        "max_position_pct": 0.07,
        "short_allowed": True,
        "short_confidence_multiplier": 1.8,
        "weight_profile": "default",
        "fuzzy_threshold": 0.80,
        "min_observations_for_trade": 5,
        "min_candles_for_training": 1500,
    },
    "defi": {
        "sax_alphabet_size": 4,
        "sax_window_size": 7,
        "catastrophic_loss_pct": 0.12,
        "max_position_pct": 0.05,
        "short_allowed": True,
        "short_confidence_multiplier": 2.0,
        "weight_profile": "default",
        "fuzzy_threshold": 0.80,
        "min_observations_for_trade": 5,
        "min_candles_for_training": 1500,
    },
    "meme": {
        "sax_alphabet_size": 5,
        "sax_window_size": 5,
        "catastrophic_loss_pct": 0.15,
        "max_position_pct": 0.03,
        "short_allowed": False,
        "short_confidence_multiplier": 99.0,  # effectively disabled
        "weight_profile": "meme",
        "fuzzy_threshold": 0.75,
        "min_observations_for_trade": 5,
        "min_candles_for_training": 1000,
    },
    "new_launch": {
        "sax_alphabet_size": 3,
        "sax_window_size": 5,
        "catastrophic_loss_pct": 0.20,
        "max_position_pct": 0.01,
        "short_allowed": False,
        "short_confidence_multiplier": 99.0,
        "weight_profile": "new_launch",
        "fuzzy_threshold": 0.70,
        "min_observations_for_trade": 10,
        "min_candles_for_training": 500,
    },
}


@dataclass
class TokenProfile:
    """
    Complete profile for a token — the engine auto-adapts from this.

    This encapsulates ALL parameters that vary by token type.
    The engine reads this profile to configure itself — no if/else
    scattered through the codebase.

    Key insight: Parameters are DISCOVERED by CalibrationEngine,
    not manually set. The defaults below are starting points for
    when insufficient data exists for calibration.
    """

    # === Identity ===
    symbol: str = ""
    asset_class: str = "default"

    # === SAX Parameters (auto-calibrated) ===
    sax_alphabet_size: int = 5
    sax_window_size: int = 10

    # === Risk Parameters ===
    catastrophic_loss_pct: float = 0.08
    max_position_pct: float = 0.10
    short_allowed: bool = True
    short_confidence_multiplier: float = 1.5

    # === Trie Weight Profile ===
    weight_profile: str = "default"

    # === Fuzzy Matching ===
    fuzzy_threshold: float = 0.85

    # === Data Quality Gates ===
    min_observations_for_trade: int = 3
    min_candles_for_training: int = 2000

    # === Calibration Metadata ===
    calibration_date: str = ""
    calibration_pnl: float = 0.0
    calibration_metric: float = 0.0
    calibration_samples: int = 0
    calibration_grid: dict = field(default_factory=dict)
    regime_fingerprint: str = ""

    # === Living Recalibration ===
    recalibration_interval: int = 500
    last_recalibration: int = 0
    profile_changes: int = 0

    @classmethod
    def from_asset_class(cls, symbol: str, asset_class: str) -> TokenProfile:
        """Create a profile with defaults for an asset class."""
        defaults = ASSET_CLASS_DEFAULTS.get(asset_class, ASSET_CLASS_DEFAULTS["large_cap"])
        return cls(symbol=symbol, asset_class=asset_class, **defaults)

    @classmethod
    def from_timeframe(cls, symbol: str, asset_class: str, timeframe: str) -> TokenProfile:
        """Create a profile combining asset class defaults + timeframe-adaptive alpha.

        This is the RECOMMENDED way to create a TokenProfile for live trading.
        It combines:
        1. Risk parameters from asset_class (catastrophic_loss_pct, max_position_pct, etc.)
        2. SAX parameters from timeframe (alpha scales with granularity)

        The timeframe alpha defaults are validated across 22 token-timeframe
        combinations with 6+ months of real Binance data each.
        """
        # Start with asset class defaults (risk params, fuzzy, etc.)
        defaults = ASSET_CLASS_DEFAULTS.get(asset_class, ASSET_CLASS_DEFAULTS["large_cap"])
        profile = cls(symbol=symbol, asset_class=asset_class, **defaults)

        # Override SAX params with timeframe-adaptive values
        tf_defaults = TIMEFRAME_ALPHA_DEFAULTS.get(timeframe, TIMEFRAME_ALPHA_DEFAULTS["1h"])
        profile.sax_alphabet_size = tf_defaults["sax_alphabet_size"]
        profile.sax_window_size = tf_defaults["sax_window_size"]

        return profile

    def update_from_calibration(
        self,
        best_alpha: int,
        best_window: int,
        metric: float,
        grid: dict,
        n_samples: int,
        recalibration_candle: int = 0,
    ) -> None:
        """Update profile with calibration results.

        v0.11.0: Added recalibration_candle parameter to track which
        candle index the calibration was performed at. This enables
        live recalibration to know when the last update occurred.
        """
        self.sax_alphabet_size = best_alpha
        self.sax_window_size = best_window
        self.calibration_date = time.strftime("%Y-%m-%d %H:%M")
        self.calibration_metric = metric
        self.calibration_grid = grid
        self.calibration_samples = n_samples
        self.profile_changes += 1
        if recalibration_candle > 0:
            self.last_recalibration = recalibration_candle

    def to_dict(self) -> dict:
        """Serialize profile to dictionary."""
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "sax_alphabet_size": self.sax_alphabet_size,
            "sax_window_size": self.sax_window_size,
            "catastrophic_loss_pct": self.catastrophic_loss_pct,
            "max_position_pct": self.max_position_pct,
            "short_allowed": self.short_allowed,
            "short_confidence_multiplier": self.short_confidence_multiplier,
            "weight_profile": self.weight_profile,
            "fuzzy_threshold": self.fuzzy_threshold,
            "min_observations_for_trade": self.min_observations_for_trade,
            "min_candles_for_training": self.min_candles_for_training,
            "calibration_date": self.calibration_date,
            "calibration_metric": round(self.calibration_metric, 4),
            "calibration_samples": self.calibration_samples,
            "calibration_grid": self.calibration_grid,
            "last_recalibration": self.last_recalibration,
            "profile_changes": self.profile_changes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TokenProfile:
        """Reconstruct a TokenProfile from a serialized dictionary.

        v0.11.0: Enables loading a previously calibrated profile from storage,
        skipping the expensive TradingCalibrationEngine grid search on startup.

        Args:
            data: Dict from TokenProfile.to_dict()

        Returns:
            TokenProfile with all fields restored from the dict.
        """
        # Only pass fields that are actual dataclass fields
        from dataclasses import fields
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class CalibrationResult:
    """Result from a single calibration run (one alpha x window combo)."""
    alphabet_size: int
    window_size: int
    train_symbols: int = 0
    oos_symbols: int = 0
    unique_patterns: int = 0
    overlap_ratio: float = 0.0
    oos_match_rate: float = 0.0
    oos_exact_match_rate: float = 0.0
    avg_confidence: float = 0.0
    avg_win_rate: float = 0.0
    avg_historical_count: float = 0.0
    information: float = 0.0
    repetition: float = 0.0
    calibration_metric: float = 0.0  # information x repetition
    symbol_distribution: dict = field(default_factory=dict)


class CalibrationEngine:
    """
    Auto-calibration engine that discovers optimal SAX parameters.

    For each token, it tests a grid of alpha x window combinations
    on real data and selects the one that maximizes:

        calibration_metric = information x repetition

    Where:
        information = 1 - max_symbol_concentration
            (lower concentration = more information encoded)

        repetition = oos_match_rate * overlap_ratio
            (higher = patterns repeat and are findable OOS)

    This ensures the SAX encoding is neither too granular (each pattern
    is unique → no repetition) nor too coarse (all patterns are the
    same → no information).

    .. deprecated:: v0.6.7
        This engine has a **structural bias toward alpha=3/window=5**
        because lower alpha always produces higher match rates, which
        dominate the pattern-matching metric. Diagnostic data shows
        it selects alpha=3/w=5 in 100% of cases across 6 tokens.

        Use :class:`TradingCalibrationEngine` instead, which selects
        parameters based on actual trading PnL and produces diverse,
        data-driven alpha/window selections.
    """

    # Grid search space
    ALPHABET_GRID = [3, 4, 5]
    WINDOW_GRID = [5, 7, 10]

    def __init__(self, train_ratio: float = 0.70, pattern_length: int = 5):
        import warnings
        warnings.warn(
            "CalibrationEngine has a structural bias toward alpha=3/window=5. "
            "Use TradingCalibrationEngine instead, which selects parameters "
            "based on actual trading performance and produces diverse, "
            "data-driven alpha/window selections. "
            "(Deprecated since v0.6.7)",
            DeprecationWarning,
            stacklevel=2,
        )
        self.train_ratio = train_ratio
        self.pattern_length = pattern_length

    def calibrate(
        self,
        df: pd.DataFrame,
        symbol: str = "",
        verbose: bool = True,
    ) -> tuple[TokenProfile, list[CalibrationResult]]:
        """
        Run full calibration on a DataFrame.

        Tests all alpha x window combos and returns the best TokenProfile
        along with all results for traceability.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            symbol: Token symbol for the profile
            verbose: Print progress

        Returns:
            Tuple of (best TokenProfile, list of all CalibrationResults)
        """
        # Split train/OOS
        n = len(df)
        split = int(n * self.train_ratio)
        train_df = df.iloc[:split]
        oos_df = df.iloc[split:]

        if verbose:
            print(f"\n{'='*60}")
            print(f"  CALIBRATION: {symbol}")
            print(f"  Data: {n} candles | Train: {split} | OOS: {n - split}")
            print(f"  Grid: alpha={self.ALPHABET_GRID} x window={self.WINDOW_GRID}")
            print(f"{'='*60}")

        results = []

        for alpha in self.ALPHABET_GRID:
            for window in self.WINDOW_GRID:
                result = self._evaluate_combo(train_df, oos_df, alpha, window)
                results.append(result)

                if verbose:
                    print(
                        f"  alpha={alpha} window={window:2d} | "
                        f"overlap={result.overlap_ratio:.2f}x "
                        f"oos_match={result.oos_match_rate:.1%} "
                        f"info={result.information:.3f} "
                        f"repet={result.repetition:.3f} "
                        f"metric={result.calibration_metric:.4f}"
                    )

        # Select best by calibration metric
        best = max(results, key=lambda r: r.calibration_metric)

        if verbose:
            print(f"\n  >>> BEST: alpha={best.alphabet_size} window={best.window_size} "
                  f"metric={best.calibration_metric:.4f}")
            print(f"  >>> overlap={best.overlap_ratio:.2f}x "
                  f"oos_match={best.oos_match_rate:.1%} "
                  f"info={best.information:.3f} "
                  f"repet={best.repetition:.3f}")

        # Build profile from best result
        asset_class = self._infer_asset_class(symbol)
        profile = TokenProfile.from_asset_class(symbol, asset_class)
        grid = {
            f"a{r.alphabet_size}_w{r.window_size}": {
                "overlap_ratio": round(r.overlap_ratio, 2),
                "oos_match_rate": round(r.oos_match_rate, 4),
                "information": round(r.information, 4),
                "repetition": round(r.repetition, 4),
                "metric": round(r.calibration_metric, 4),
                "avg_confidence": round(r.avg_confidence, 4),
                "avg_win_rate": round(r.avg_win_rate, 4),
                "avg_hist_count": round(r.avg_historical_count, 1),
                "symbol_distribution": r.symbol_distribution,
            }
            for r in results
        }
        profile.update_from_calibration(
            best_alpha=best.alphabet_size,
            best_window=best.window_size,
            metric=best.calibration_metric,
            grid=grid,
            n_samples=n,
        )

        return profile, results

    def _evaluate_combo(
        self,
        train_df: pd.DataFrame,
        oos_df: pd.DataFrame,
        alphabet_size: int,
        window_size: int,
    ) -> CalibrationResult:
        """Evaluate a single alpha x window combination."""
        # Encode training data
        try:
            encoder = SAXEncoder(
                alphabet_size=alphabet_size,
                window_size=window_size,
                strategy="ohlcv",
            )
        except ValueError:
            return CalibrationResult(
                alphabet_size=alphabet_size,
                window_size=window_size,
                calibration_metric=0.0,
            )

        train_symbols = encoder.encode(train_df)
        oos_symbols = encoder.encode(oos_df)

        if len(train_symbols) < self.pattern_length + 1:
            return CalibrationResult(
                alphabet_size=alphabet_size,
                window_size=window_size,
                calibration_metric=0.0,
            )

        # Build trie from training data
        trie = PPMTTrie(name=f"calibration_a{alphabet_size}_w{window_size}")

        for i in range(len(train_symbols) - self.pattern_length):
            pattern = train_symbols[i:i + self.pattern_length]
            next_sym = train_symbols[i + self.pattern_length] if i + self.pattern_length < len(train_symbols) else None

            # Get price data for this pattern
            start_candle = i * window_size
            end_candle = (i + self.pattern_length) * window_size
            if end_candle > len(train_df):
                break

            window_df = train_df.iloc[start_candle:end_candle]
            if len(window_df) < 2:
                continue

            entry_price = window_df["close"].iloc[0]
            exit_price = window_df["close"].iloc[-1]
            move_pct = ((exit_price - entry_price) / entry_price) * 100.0

            high = window_df["high"].max()
            low = window_df["low"].min()
            drawdown_pct = ((low - entry_price) / entry_price) * 100.0
            favorable_pct = ((high - entry_price) / entry_price) * 100.0

            duration = len(window_df)
            won = move_pct > 0

            trie.insert_with_observations(
                symbols=pattern,
                move_pct=move_pct,
                drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct,
                duration=duration,
                won=won,
                next_symbol=next_sym,
            )

        # Propagate metadata
        trie.propagate_metadata()

        # === Compute Metrics ===

        # 1. Symbol distribution (information metric)
        sym_counts = {}
        for s in train_symbols:
            sym_counts[s] = sym_counts.get(s, 0) + 1
        total_syms = len(train_symbols)
        symbol_distribution = {
            s: round(c / total_syms, 4) if total_syms > 0 else 0.0
            for s, c in sorted(sym_counts.items())
        }
        max_concentration = max(sym_counts.values()) / total_syms if total_syms > 0 else 1.0
        information = 1.0 - max_concentration

        # 2. Overlap ratio
        unique_patterns = trie.pattern_count
        total_possible = max(len(train_symbols) - self.pattern_length, 1)
        overlap_ratio = total_possible / unique_patterns if unique_patterns > 0 else 0.0

        # 3. OOS match rate
        oos_match_count = 0
        oos_exact_match_count = 0
        oos_test_count = 0
        matched_confidences = []
        matched_win_rates = []
        matched_hist_counts = []

        for i in range(len(oos_symbols) - self.pattern_length):
            pattern = oos_symbols[i:i + self.pattern_length]
            oos_test_count += 1

            # Exact match
            node = trie.search(pattern)
            if node is not None and node.metadata.historical_count > 0:
                oos_exact_match_count += 1
                oos_match_count += 1
                matched_confidences.append(node.metadata.confidence)
                matched_win_rates.append(node.metadata.win_rate)
                matched_hist_counts.append(node.metadata.historical_count)
            else:
                # Try prefix match (fuzzy with threshold)
                node_prefix, depth = trie.search_prefix(pattern)
                if node_prefix is not None and depth >= self.pattern_length - 1:
                    oos_match_count += 1
                    matched_confidences.append(node_prefix.metadata.confidence)
                    matched_win_rates.append(node_prefix.metadata.win_rate)
                    matched_hist_counts.append(node_prefix.metadata.historical_count)

        oos_match_rate = oos_match_count / oos_test_count if oos_test_count > 0 else 0.0
        oos_exact_match_rate = oos_exact_match_count / oos_test_count if oos_test_count > 0 else 0.0

        # 4. Repetition = oos_match_rate * overlap_ratio (both need to be good)
        repetition = oos_match_rate * min(overlap_ratio, 10.0) / 10.0

        # 5. Calibration metric = information x repetition
        # This penalizes both: too much concentration (low info) and
        # too little overlap (low repetition)
        calibration_metric = information * (0.4 * oos_match_rate + 0.35 * min(overlap_ratio, 10.0) / 10.0 + 0.25 * repetition)

        # Averages from matched patterns
        avg_confidence = np.mean(matched_confidences) if matched_confidences else 0.0
        avg_win_rate = np.mean(matched_win_rates) if matched_win_rates else 0.0
        avg_hist_count = np.mean(matched_hist_counts) if matched_hist_counts else 0.0

        return CalibrationResult(
            alphabet_size=alphabet_size,
            window_size=window_size,
            train_symbols=len(train_symbols),
            oos_symbols=len(oos_symbols),
            unique_patterns=unique_patterns,
            overlap_ratio=round(overlap_ratio, 2),
            oos_match_rate=round(oos_match_rate, 4),
            oos_exact_match_rate=round(oos_exact_match_rate, 4),
            avg_confidence=round(avg_confidence, 4),
            avg_win_rate=round(avg_win_rate, 4),
            avg_historical_count=round(avg_hist_count, 1),
            information=round(information, 4),
            repetition=round(repetition, 4),
            calibration_metric=round(calibration_metric, 4),
            symbol_distribution=symbol_distribution,
        )

    @staticmethod
    def _infer_asset_class(symbol: str) -> str:
        """Infer asset class from symbol for default profile."""
        symbol_upper = symbol.upper()
        blue_chips = {"BTC/USDT", "BTCUSDT", "ETH/USDT", "ETHUSDT"}
        large_caps = {"SOL/USDT", "SOLUSDT", "AVAX/USDT", "AVAXUSDT",
                      "BNB/USDT", "BNBUSDT", "XRP/USDT", "XRPUSDT",
                      "ADA/USDT", "ADAUSDT"}
        defi_tokens = {"LINK/USDT", "LINKUSDT", "UNI/USDT", "UNIUSDT",
                       "ATOM/USDT", "ATOMUSDT", "AAVE/USDT", "AAVEUSDT",
                       "MKR/USDT", "MKRUSDT", "COMP/USDT", "COMPUSDT",
                       "CRV/USDT", "CRVUSDT", "SNX/USDT", "SNXUSDT"}
        memes = {"DOGE/USDT", "DOGEUSDT", "SHIB/USDT", "SHIBUSDT",
                 "PEPE/USDT", "PEPEUSDT", "FLOKI/USDT", "FLOKIUSDT",
                 "WIF/USDT", "WIFUSDT", "BONK/USDT", "BONKUSDT"}

        if symbol_upper in blue_chips:
            return "blue_chip"
        elif symbol_upper in large_caps:
            return "large_cap"
        elif symbol_upper in defi_tokens:
            return "defi"
        elif symbol_upper in memes:
            return "meme"
        else:
            return "large_cap"  # safe default


# ================================================================
# TradingCalibrationEngine — Trading-Performance-Based Calibration
# ================================================================
# The original CalibrationEngine uses a pattern-matching metric:
#   calibration_metric = information × (match_rate + overlap + repetition)
#
# PROBLEM: This ALWAYS selects alpha=3/window=5 because:
#   - Lower alpha → fewer unique symbols → higher match rate
#   - Higher match rate = higher metric = always wins
#   - But alpha=3 may produce POOR trading signals (too coarse)
#
# SOLUTION: TradingCalibrationEngine runs mini-backtests for each
#   α/W combo and selects the one with the best TRADING performance.
#   This fixes the structural bias and produces +74% to +211% PnL
#   improvement over pattern-matching calibration.
#
# v0.6.7: Added to replace CalibrationEngine in validation scripts
# ================================================================


@dataclass
class TradingCalibrationResult:
    """Result of trading-based calibration for a single α/W combo."""
    alphabet_size: int
    window_size: int
    # Pattern-matching metrics (from CalibrationEngine)
    pattern_metric: float = 0.0
    overlap_ratio: float = 0.0
    oos_match_rate: float = 0.0
    information: float = 0.0
    # Trading metrics (NEW)
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_approx: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    # Combined metric
    trading_metric: float = 0.0


class TradingCalibrationEngine:
    """
    Trading-Performance-Based Calibration Engine.

    Unlike CalibrationEngine which selects α/W by pattern-matching metrics
    (always converging to alpha=3/window=5), this engine runs mini-backtests
    for each α/W combination and selects the one with the best TRADING PnL.

    Diagnostic data (6 tokens, 1h, 600 days each):
      - CalibrationEngine selects alpha=3/w=5 in 100% of cases
      - TradingCalibrationEngine selects: alpha=3 (17%), alpha=4 (50%), alpha=5 (33%)

    The metric combines trading performance with pattern quality and robustness:

        trading_metric = pnl_score + 0.1 × pattern_quality + 0.05 × count_bonus
                         - 0.1 × volatility_penalty

    Where:
        - pnl_score = sign(PnL) × log(1 + |PnL|) — logarithmic scaling
        - pattern_quality = min(oos_match_rate, 0.8) × min(win_rate, 0.9)
        - count_bonus = log(1 + trades) / log(1 + 100) — statistical significance
        - volatility_penalty = max(0, std(pnls) - 5) / 10 — penalize unstable results
        - A combo must produce at least MIN_TRADES trades to be eligible
        - Negative PnL combos get 1.5× penalty

    This ensures the selected parameters produce:
    1. Profitable trades (not just pattern matches)
    2. Sufficient trade frequency (at least some signals)
    3. Reasonable pattern quality (not degenerate matches)
    4. Stable results (not wildly variable PnL)

    SL/TP are configurable and adapt to asset class volatility:
        - blue_chip: SL=2.5% / TP=4.0%  (lower volatility, tighter stops)
        - large_cap: SL=3.0% / TP=5.0%  (moderate volatility)
        - defi:     SL=3.5% / TP=6.0%  (higher volatility)
        - meme:     SL=5.0% / TP=8.0%  (very high volatility, wider stops)
        - default:  SL=3.0% / TP=5.0%

    Sharpe ratio is timeframe-aware (derives annualization factor from
    candle interval rather than hardcoding sqrt(365*24)).

    Usage:
        engine = TradingCalibrationEngine(train_ratio=0.70, pattern_length=5)
        profile, results = engine.calibrate(df, symbol="BTC/USDT")
    """

    # Grid search space (same as CalibrationEngine for compatibility)
    ALPHABET_GRID = [3, 4, 5]
    WINDOW_GRID = [5, 7, 10]

    # Minimum trades required for a combo to be considered
    MIN_TRADES = 5

    # SL/TP defaults for mini-backtest (overridden by asset class)
    DEFAULT_SL_PCT = 3.0
    DEFAULT_TP_PCT = 5.0

    # Asset-class-adaptive SL/TP for mini-backtest
    ASSET_CLASS_SL_TP = {
        "blue_chip":  {"sl_pct": 2.5, "tp_pct": 4.0},
        "large_cap":  {"sl_pct": 3.0, "tp_pct": 5.0},
        "defi":       {"sl_pct": 3.5, "tp_pct": 6.0},
        "meme":       {"sl_pct": 5.0, "tp_pct": 8.0},
        "new_launch": {"sl_pct": 5.0, "tp_pct": 8.0},
    }

    # Timeframe → candles-per-year mapping (for Sharpe annualization)
    TIMEFRAME_CANDLES_PER_YEAR = {
        "1m":  365 * 24 * 60,
        "5m":  365 * 24 * 12,
        "15m": 365 * 24 * 4,
        "30m": 365 * 24 * 2,
        "1h":  365 * 24,
        "4h":  365 * 6,
        "1d":  365,
    }

    def __init__(
        self,
        train_ratio: float = 0.70,
        pattern_length: int = 5,
        sl_pct: float | None = None,
        tp_pct: float | None = None,
        timeframe: str = "1h",
    ):
        self.train_ratio = train_ratio
        self.pattern_length = pattern_length
        self.timeframe = timeframe
        # SL/TP: use explicit values if provided, otherwise asset-class-adaptive
        self._explicit_sl = sl_pct
        self._explicit_tp = tp_pct
        self.sl_pct = sl_pct if sl_pct is not None else self.DEFAULT_SL_PCT
        self.tp_pct = tp_pct if tp_pct is not None else self.DEFAULT_TP_PCT
        # Sharpe annualization factor from timeframe
        cpy = self.TIMEFRAME_CANDLES_PER_YEAR.get(timeframe, 365 * 24)
        self._sharpe_annual_factor = np.sqrt(cpy)

    def _get_sl_tp_for_symbol(self, symbol: str) -> tuple[float, float]:
        """Get SL/TP percentages adapted to the token's asset class."""
        if self._explicit_sl is not None and self._explicit_tp is not None:
            return self._explicit_sl, self._explicit_tp
        asset_class = CalibrationEngine._infer_asset_class(symbol)
        sl_tp = self.ASSET_CLASS_SL_TP.get(asset_class, {"sl_pct": self.DEFAULT_SL_PCT, "tp_pct": self.DEFAULT_TP_PCT})
        return sl_tp["sl_pct"], sl_tp["tp_pct"]

    def calibrate(
        self,
        df: pd.DataFrame,
        symbol: str = "",
        verbose: bool = True,
    ) -> tuple:
        """
        Run trading-based calibration on a DataFrame.

        Tests all α × window combos, runs mini-backtests on OOS data,
        and selects the combo with the best trading performance.

        Args:
            df: OHLCV DataFrame
            symbol: Token symbol for the profile
            verbose: Print progress

        Returns:
            Tuple of (best TokenProfile, list of TradingCalibrationResults)
        """
        # Resolve SL/TP for this symbol's asset class
        self.sl_pct, self.tp_pct = self._get_sl_tp_for_symbol(symbol)

        n = len(df)
        split = int(n * self.train_ratio)
        train_df = df.iloc[:split]
        oos_df = df.iloc[split:]

        if verbose:
            print(f"\n{'='*70}")
            print(f"  TRADING CALIBRATION: {symbol}")
            print(f"  Data: {n} candles | Train: {split} | OOS: {n - split}")
            print(f"  Grid: alpha={self.ALPHABET_GRID} x window={self.WINDOW_GRID}")
            print(f"  SL={self.sl_pct}% / TP={self.tp_pct}% (asset-class-adaptive)")
            print(f"  Timeframe: {self.timeframe} (Sharpe ann. factor: {self._sharpe_annual_factor:.1f})")
            print(f"{'='*70}")

        results = []

        for alpha in self.ALPHABET_GRID:
            for window in self.WINDOW_GRID:
                result = self._evaluate_combo(train_df, oos_df, alpha, window)
                results.append(result)

                if verbose:
                    pnl_sign = "+" if result.total_pnl_pct >= 0 else ""
                    print(
                        f"  alpha={alpha} window={window:2d} | "
                        f"Trades={result.total_trades:3d} "
                        f"WR={result.win_rate:.1%} "
                        f"PF={result.profit_factor:.2f} "
                        f"PnL={pnl_sign}{result.total_pnl_pct:.1f}% "
                        f"DD={result.max_drawdown_pct:.1f}% "
                        f"tmetric={result.trading_metric:.4f}"
                    )

        # Select best by trading metric
        eligible = [r for r in results if r.total_trades >= self.MIN_TRADES]

        if not eligible:
            # Fallback: use pattern-matching metric if no combo produces trades
            if verbose:
                print(f"\n  ⚠️  No combo produced ≥{self.MIN_TRADES} trades — "
                      f"falling back to pattern metric")
            best = max(results, key=lambda r: r.pattern_metric)
        else:
            best = max(eligible, key=lambda r: r.trading_metric)

        if verbose:
            pnl_sign = "+" if best.total_pnl_pct >= 0 else ""
            print(f"\n  >>> BEST: alpha={best.alphabet_size} window={best.window_size} "
                  f"trading_metric={best.trading_metric:.4f}")
            print(f"  >>> Trades={best.total_trades} WR={best.win_rate:.1%} "
                  f"PF={best.profit_factor:.2f} PnL={pnl_sign}{best.total_pnl_pct:.1f}%")

        # Build profile from best result
        asset_class = CalibrationEngine._infer_asset_class(symbol)
        profile = TokenProfile.from_asset_class(symbol, asset_class)

        grid = {
            f"a{r.alphabet_size}_w{r.window_size}": {
                "total_trades": r.total_trades,
                "win_rate": round(r.win_rate, 4),
                "profit_factor": round(r.profit_factor, 4),
                "total_pnl_pct": round(r.total_pnl_pct, 2),
                "max_drawdown_pct": round(r.max_drawdown_pct, 2),
                "trading_metric": round(r.trading_metric, 4),
                "pattern_metric": round(r.pattern_metric, 4),
                "oos_match_rate": round(r.oos_match_rate, 4),
                "overlap_ratio": round(r.overlap_ratio, 2),
            }
            for r in results
        }
        profile.update_from_calibration(
            best_alpha=best.alphabet_size,
            best_window=best.window_size,
            metric=best.trading_metric,
            grid=grid,
            n_samples=n,
        )

        return profile, results

    def _evaluate_combo(
        self,
        train_df: pd.DataFrame,
        oos_df: pd.DataFrame,
        alphabet_size: int,
        window_size: int,
    ) -> TradingCalibrationResult:
        """Evaluate a single α × window combo with mini-backtest."""
        result = TradingCalibrationResult(
            alphabet_size=alphabet_size,
            window_size=window_size,
        )

        # Encode data
        try:
            encoder = SAXEncoder(
                alphabet_size=alphabet_size,
                window_size=window_size,
                strategy="ohlcv",
            )
        except ValueError:
            return result

        train_symbols = encoder.encode(train_df)
        oos_symbols = encoder.encode(oos_df)

        if len(train_symbols) < self.pattern_length + 1:
            return result
        if len(oos_symbols) < self.pattern_length + 1:
            return result

        # Build trie from training data
        trie = PPMTTrie(name=f"tcal_a{alphabet_size}_w{window_size}")

        for i in range(len(train_symbols) - self.pattern_length):
            pattern = train_symbols[i:i + self.pattern_length]
            next_sym = train_symbols[i + self.pattern_length] if i + self.pattern_length < len(train_symbols) else None

            start_candle = i * window_size
            end_candle = (i + self.pattern_length) * window_size
            if end_candle > len(train_df):
                break

            window_df = train_df.iloc[start_candle:end_candle]
            if len(window_df) < 2:
                continue

            entry_price = window_df["close"].iloc[0]
            exit_price = window_df["close"].iloc[-1]
            move_pct = ((exit_price - entry_price) / entry_price) * 100.0

            high = window_df["high"].max()
            low = window_df["low"].min()
            drawdown_pct = ((low - entry_price) / entry_price) * 100.0
            favorable_pct = ((high - entry_price) / entry_price) * 100.0

            duration = len(window_df)
            won = move_pct > 0

            trie.insert_with_observations(
                symbols=pattern,
                move_pct=move_pct,
                drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct,
                duration=duration,
                won=won,
                next_symbol=next_sym,
            )

        trie.propagate_metadata()

        # === Pattern-matching metrics (for comparison) ===
        sym_counts = {}
        for s in train_symbols:
            sym_counts[s] = sym_counts.get(s, 0) + 1
        total_syms = len(train_symbols)
        max_concentration = max(sym_counts.values()) / total_syms if total_syms > 0 else 1.0
        information = 1.0 - max_concentration

        unique_patterns = trie.pattern_count
        total_possible = max(len(train_symbols) - self.pattern_length, 1)
        overlap_ratio = total_possible / unique_patterns if unique_patterns > 0 else 0.0

        oos_match_count = 0
        oos_test_count = 0
        for i in range(len(oos_symbols) - self.pattern_length):
            pattern = oos_symbols[i:i + self.pattern_length]
            oos_test_count += 1
            node = trie.search(pattern)
            if node is not None and node.metadata.historical_count > 0:
                oos_match_count += 1
            else:
                node_prefix, depth = trie.search_prefix(pattern)
                if node_prefix is not None and depth >= self.pattern_length - 1:
                    oos_match_count += 1

        oos_match_rate = oos_match_count / oos_test_count if oos_test_count > 0 else 0.0
        repetition = oos_match_rate * min(overlap_ratio, 10.0) / 10.0
        pattern_metric = information * (0.4 * oos_match_rate + 0.35 * min(overlap_ratio, 10.0) / 10.0 + 0.25 * repetition)

        # === Mini-backtest on OOS data ===
        trades = self._mini_backtest(trie, oos_df, oos_symbols, window_size)

        # Compute trading stats
        if not trades:
            result.pattern_metric = pattern_metric
            result.information = information
            result.overlap_ratio = overlap_ratio
            result.oos_match_rate = oos_match_rate
            return result

        pnls = [t["pnl_pct"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_wins = sum(wins) if wins else 0.0
        total_losses = abs(sum(losses)) if losses else 0.0

        win_rate = len(wins) / len(trades) if trades else 0.0
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        total_pnl = sum(pnls)

        # Max drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = cumulative - peak
        max_dd = abs(min(drawdowns)) if len(drawdowns) > 0 else 0.0

        # Sharpe approximation (annualized, timeframe-aware)
        if len(pnls) > 1 and np.std(pnls) > 0:
            sharpe = np.mean(pnls) / np.std(pnls) * self._sharpe_annual_factor
        else:
            sharpe = 0.0

        long_trades = sum(1 for t in trades if t["direction"] == "LONG")
        short_trades = sum(1 for t in trades if t["direction"] == "SHORT")

        # === Trading metric ===
        # Logarithmic PnL scaling to avoid domination by extreme values
        # Positive PnL gets diminishing returns; negative PnL gets amplified penalty
        if total_pnl > 0:
            pnl_score = np.log1p(total_pnl)
        else:
            pnl_score = -np.log1p(abs(total_pnl)) * 1.5  # 1.5x penalty for losses

        # Pattern quality bonus: rewards match rate + win rate, but capped
        pattern_quality = min(oos_match_rate, 0.8) * min(win_rate, 0.9)

        # Trade count bonus: more trades = more statistical significance
        # But with diminishing returns (log scale)
        count_bonus = np.log1p(len(trades)) / np.log1p(100)  # Normalized to ~1 at 100 trades

        # Volatility penalty: penalize unstable PnL distributions
        # High std means results could be unreliable
        pnl_std = float(np.std(pnls)) if len(pnls) > 1 else 0.0
        volatility_penalty = max(0.0, pnl_std - 5.0) / 10.0

        # Combined metric: PnL is king, quality and count provide small bonuses,
        # volatility provides a penalty
        trading_metric = pnl_score + 0.1 * pattern_quality + 0.05 * count_bonus \
                        - 0.1 * volatility_penalty

        # Update result
        result.pattern_metric = round(pattern_metric, 4)
        result.information = round(information, 4)
        result.overlap_ratio = round(overlap_ratio, 2)
        result.oos_match_rate = round(oos_match_rate, 4)
        result.total_trades = len(trades)
        result.win_rate = round(win_rate, 4)
        result.profit_factor = round(profit_factor, 4) if profit_factor != float('inf') else 99.99
        result.total_pnl_pct = round(total_pnl, 2)
        result.max_drawdown_pct = round(max_dd, 2)
        result.sharpe_approx = round(sharpe, 2)
        result.long_trades = long_trades
        result.short_trades = short_trades
        result.trading_metric = round(trading_metric, 4)

        return result

    def _mini_backtest(
        self,
        trie: PPMTTrie,
        oos_df: pd.DataFrame,
        oos_symbols: list,
        window_size: int,
    ) -> list:
        """
        Run a simplified trading backtest on OOS data.

        For each pattern in OOS symbols:
        1. Look up the pattern in the trie
        2. If matched with sufficient confidence, enter a trade
        3. Apply simple SL/TP exit logic
        4. Record the trade PnL

        Returns list of trade dicts with pnl_pct and direction.
        """
        trades = []
        in_position = False
        entry_price = 0.0
        entry_sl = 0.0
        entry_tp = 0.0
        position_direction = "LONG"

        for i in range(len(oos_symbols) - self.pattern_length):
            pattern = oos_symbols[i:i + self.pattern_length]

            # Get price for this candle position
            candle_idx = min((i + self.pattern_length) * window_size, len(oos_df) - 1)
            if candle_idx >= len(oos_df):
                break

            row = oos_df.iloc[candle_idx]
            current_price = float(row["close"])
            current_low = float(row["low"])
            current_high = float(row["high"])

            # SL/TP check for open positions
            if in_position:
                exited = False
                if position_direction == "LONG":
                    if current_low <= entry_sl:
                        pnl = ((entry_sl - entry_price) / entry_price) * 100.0
                        trades.append({"pnl_pct": round(pnl, 4), "direction": "LONG", "exit": "SL"})
                        exited = True
                    elif current_high >= entry_tp:
                        pnl = ((entry_tp - entry_price) / entry_price) * 100.0
                        trades.append({"pnl_pct": round(pnl, 4), "direction": "LONG", "exit": "TP"})
                        exited = True
                elif position_direction == "SHORT":
                    if current_high >= entry_sl:
                        pnl = ((entry_price - entry_sl) / entry_price) * 100.0
                        trades.append({"pnl_pct": round(pnl, 4), "direction": "SHORT", "exit": "SL"})
                        exited = True
                    elif current_low <= entry_tp:
                        pnl = ((entry_price - entry_tp) / entry_price) * 100.0
                        trades.append({"pnl_pct": round(pnl, 4), "direction": "SHORT", "exit": "TP"})
                        exited = True

                if exited:
                    in_position = False
                    continue

            # Pattern lookup for new entries
            if in_position:
                continue

            node = trie.search(pattern)
            if node is None:
                # Try prefix match
                node_prefix, depth = trie.search_prefix(pattern)
                if node_prefix is not None and depth >= self.pattern_length - 1:
                    node = node_prefix
                else:
                    continue

            meta = node.metadata
            if meta.historical_count < 3:  # Need minimum observations
                continue

            # Direction from win rate and expected move
            confidence = meta.confidence
            if confidence < 0.05:  # Minimum confidence gate
                continue

            # Determine direction from expected move
            if meta.expected_move_pct > 0:
                direction = "LONG"
            elif meta.expected_move_pct < 0:
                direction = "SHORT"
            else:
                continue  # No directional signal

            # Enter trade
            in_position = True
            entry_price = current_price
            position_direction = direction

            if direction == "LONG":
                entry_sl = current_price * (1 - self.sl_pct / 100.0)
                entry_tp = current_price * (1 + self.tp_pct / 100.0)
            else:
                entry_sl = current_price * (1 + self.sl_pct / 100.0)
                entry_tp = current_price * (1 - self.tp_pct / 100.0)

        # Close any open position at end
        if in_position and len(oos_df) > 0:
            last_price = float(oos_df["close"].iloc[-1])
            if position_direction == "LONG":
                pnl = ((last_price - entry_price) / entry_price) * 100.0
            else:
                pnl = ((entry_price - last_price) / entry_price) * 100.0
            trades.append({"pnl_pct": round(pnl, 4), "direction": position_direction, "exit": "END"})

        return trades
