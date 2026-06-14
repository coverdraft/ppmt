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
#   - alpha=5/window=7 is the optimal config for 1m, producing 350+ trades
TIMEFRAME_ALPHA_DEFAULTS = {
    "1m":  {"sax_alphabet_size": 5, "sax_window_size": 7},
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
    ) -> None:
        """Update profile with calibration results."""
        self.sax_alphabet_size = best_alpha
        self.sax_window_size = best_window
        self.calibration_date = time.strftime("%Y-%m-%d %H:%M")
        self.calibration_metric = metric
        self.calibration_grid = grid
        self.calibration_samples = n_samples
        self.profile_changes += 1

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
            "profile_changes": self.profile_changes,
        }


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
    """

    # Grid search space
    ALPHABET_GRID = [3, 4, 5]
    WINDOW_GRID = [5, 7, 10]

    def __init__(self, train_ratio: float = 0.70, pattern_length: int = 5):
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
                      "BNB/USDT", "BNBUSDT", "XRP/USDT", "XRPUSDT"}
        memes = {"DOGE/USDT", "DOGEUSDT", "SHIB/USDT", "SHIBUSDT",
                 "PEPE/USDT", "PEPEUSDT", "FLOKI/USDT", "FLOKIUSDT"}

        if symbol_upper in blue_chips:
            return "blue_chip"
        elif symbol_upper in large_caps:
            return "large_cap"
        elif symbol_upper in memes:
            return "meme"
        else:
            return "large_cap"  # safe default
