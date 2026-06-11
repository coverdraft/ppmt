"""
PPMT Validation Suite — One-Click Statistical Validation Engine

Runs three complementary validation tests to determine whether
the PPMT trading system is robust or overfit:

  P0: Out-of-Sample (OOS) — Train/test split to detect overfitting
  P1: Monte Carlo — Trade order randomization to test robustness
  P2: Walk-Forward — Rolling window validation for regime consistency

Produces a composite verdict: ROBUST / MARGINAL / OVERFIT / INSUFFICIENT_DATA

Architecture:
  ┌─────────────────────────────────────────────┐
  │           Validation Suite                   │
  │  ┌─────────┐ ┌─────────┐ ┌──────────────┐  │
  │  │ P0: OOS │ │ P1: MC  │ │ P2: Walk-Fwd │  │
  │  │ 70/30   │ │ 1000sim │ │ 5 windows    │  │
  │  └────┬────┘ └────┬────┘ └──────┬───────┘  │
  │       │           │             │           │
  │       └───────────┼─────────────┘           │
  │                   ▼                         │
  │          ┌─────────────────┐                │
  │          │ Composite Score │                │
  │          │ 0-100 points    │                │
  │          └────────┬────────┘                │
  │                   ▼                         │
  │          ┌─────────────────┐                │
  │          │   VERDICT       │                │
  │          │ ROBUST/MARGINAL │                │
  │          │ /OVERFIT/INSUFF │                │
  │          └─────────────────┘                │
  └─────────────────────────────────────────────┘
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ppmt.core.sax import SAXEncoder
from ppmt.core.matcher import FuzzyMatcher
from ppmt.engine.ppmt import PPMT
from ppmt.data.classifier import AssetClassifier
from ppmt.engine.monte_carlo import MonteCarloEngine

logger = logging.getLogger(__name__)


# ============================================================
# Configuration
# ============================================================

@dataclass
class ValidationConfig:
    """Configuration for the full validation suite."""

    symbol: str = "BTC/USDT"
    """Trading pair to validate."""

    train_ratio: float = 0.7
    """P0: Fraction of data used for training (rest for testing)."""

    mc_simulations: int = 1000
    """P1: Number of Monte Carlo simulation paths."""

    wf_window_count: int = 5
    """P2: Number of walk-forward windows."""

    wf_train_ratio: float = 0.7
    """P2: Train ratio within each walk-forward window."""

    initial_capital: float = 10000.0
    """Starting capital for simulations."""

    pattern_length: int = 5
    """SAX blocks per pattern."""

    forward_window: int = 5
    """Forward window in SAX blocks for outcome measurement."""

    position_size_pct: float = 1.0
    """Position size multiplier for backtest."""

    ruin_threshold: float = 0.5
    """Equity fraction that defines 'ruin' in Monte Carlo."""

    seed: int = 42
    """PRNG seed for reproducibility."""

    # SAX config
    sax_alphabet_size: int = 8
    sax_window_size: int = 10
    sax_strategy: str = "ohlcv"

    # Matching
    min_dir_count: int = 0
    """Min directional count for pattern to be considered valid."""


# ============================================================
# Result Data Classes
# ============================================================

@dataclass
class OOSResult:
    """P0: Out-of-Sample validation results."""

    train_candles: int = 0
    test_candles: int = 0
    patterns_trained: int = 0

    # In-sample metrics (training period)
    is_total_trades: int = 0
    is_win_rate: float = 0.0
    is_total_pnl_pct: float = 0.0
    is_sharpe: float = 0.0
    is_max_dd_pct: float = 0.0
    is_long_trades: int = 0
    is_short_trades: int = 0
    is_long_wr: float = 0.0
    is_short_wr: float = 0.0

    # Out-of-sample metrics (test period)
    oos_total_trades: int = 0
    oos_win_rate: float = 0.0
    oos_total_pnl_pct: float = 0.0
    oos_sharpe: float = 0.0
    oos_max_dd_pct: float = 0.0
    oos_long_trades: int = 0
    oos_short_trades: int = 0
    oos_long_wr: float = 0.0
    oos_short_wr: float = 0.0

    # Degradation analysis
    pnl_degradation_pct: float = 0.0
    wr_degradation_pct: float = 0.0
    oos_ratio: float = 0.0  # Walk-Forward Efficiency equivalent
    avg_trade_degradation_pct: float = 0.0  # Degradation of avg trade PnL
    oos_avg_trade_ratio: float = 0.0  # OOS avg trade / IS avg trade

    # Trade lists (capped for JSON size)
    is_trades: List[Dict] = field(default_factory=list)
    oos_trades: List[Dict] = field(default_factory=list)
    oos_equity_curve: List[float] = field(default_factory=list)


@dataclass
class MCValidationResult:
    """P1: Monte Carlo validation results."""

    n_simulations: int = 0
    n_trades_used: int = 0

    risk_of_ruin_pct: float = 0.0
    profit_probability_pct: float = 0.0
    p95_max_drawdown_pct: float = 0.0
    mean_final_equity: float = 0.0
    median_final_equity: float = 0.0
    ci_5: float = 0.0
    ci_25: float = 0.0
    ci_75: float = 0.0
    ci_95: float = 0.0
    sharpe_ratio: float = 0.0
    mean_win_rate_pct: float = 0.0
    mean_pnl_pct: float = 0.0


@dataclass
class WFWindowResult:
    """Single walk-forward window result."""

    window_index: int = 0
    train_start_idx: int = 0
    train_end_idx: int = 0
    test_start_idx: int = 0
    test_end_idx: int = 0
    is_return_pct: float = 0.0
    oos_return_pct: float = 0.0
    is_trades: int = 0
    oos_trades: int = 0
    is_win_rate: float = 0.0
    oos_win_rate: float = 0.0
    wfe: float = 0.0
    degradation_pct: float = 0.0


@dataclass
class WFValidationResult:
    """P2: Walk-Forward validation results."""

    windows: List[WFWindowResult] = field(default_factory=list)
    aggregate_wfe: float = 0.0
    avg_is_return: float = 0.0
    avg_oos_return: float = 0.0
    overall_degradation: float = 0.0
    profitable_windows: int = 0
    total_windows: int = 0
    consistency_pct: float = 0.0


@dataclass
class ValidationVerdict:
    """Composite validation verdict with all results."""

    recommendation: str = "INSUFFICIENT_DATA"
    confidence_score: float = 0.0  # 0-100

    # Breakdown scores per validation
    p0_score: float = 0.0  # 0-40
    p1_score: float = 0.0  # 0-30
    p2_score: float = 0.0  # 0-30

    oos: OOSResult = field(default_factory=OOSResult)
    mc: MCValidationResult = field(default_factory=MCValidationResult)
    wf: WFValidationResult = field(default_factory=WFValidationResult)

    symbol: str = ""
    total_candles: int = 0
    elapsed_seconds: float = 0.0
    summary: str = ""

    def to_dict(self) -> Dict:
        """Serialize to dictionary for JSON output."""
        return {
            "recommendation": self.recommendation,
            "confidence_score": round(self.confidence_score, 1),
            "p0_score": round(self.p0_score, 1),
            "p1_score": round(self.p1_score, 1),
            "p2_score": round(self.p2_score, 1),
            "symbol": self.symbol,
            "total_candles": self.total_candles,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "oos": self._oos_dict(),
            "mc": self._mc_dict(),
            "wf": self._wf_dict(),
            "summary": self.summary,
        }

    def _oos_dict(self) -> Dict:
        o = self.oos
        return {
            "train_candles": o.train_candles,
            "test_candles": o.test_candles,
            "patterns_trained": o.patterns_trained,
            "is_total_trades": o.is_total_trades,
            "is_win_rate": round(o.is_win_rate, 4),
            "is_total_pnl_pct": round(o.is_total_pnl_pct, 2),
            "is_sharpe": round(o.is_sharpe, 2),
            "is_max_dd_pct": round(o.is_max_dd_pct, 2),
            "is_long_trades": o.is_long_trades,
            "is_short_trades": o.is_short_trades,
            "is_long_wr": round(o.is_long_wr, 4),
            "is_short_wr": round(o.is_short_wr, 4),
            "oos_total_trades": o.oos_total_trades,
            "oos_win_rate": round(o.oos_win_rate, 4),
            "oos_total_pnl_pct": round(o.oos_total_pnl_pct, 2),
            "oos_sharpe": round(o.oos_sharpe, 2),
            "oos_max_dd_pct": round(o.oos_max_dd_pct, 2),
            "oos_long_trades": o.oos_long_trades,
            "oos_short_trades": o.oos_short_trades,
            "oos_long_wr": round(o.oos_long_wr, 4),
            "oos_short_wr": round(o.oos_short_wr, 4),
            "pnl_degradation_pct": round(o.pnl_degradation_pct, 1),
            "wr_degradation_pct": round(o.wr_degradation_pct, 1),
            "oos_ratio": round(o.oos_ratio, 3),
            "oos_avg_trade_ratio": round(o.oos_avg_trade_ratio, 3),
            "avg_trade_degradation_pct": round(o.avg_trade_degradation_pct, 1),
            "oos_equity_curve": _cap_list(o.oos_equity_curve, 500),
            "oos_trades": _cap_list(o.oos_trades, 100),
            "is_trades": _cap_list(o.is_trades, 50),
        }

    def _mc_dict(self) -> Dict:
        m = self.mc
        return {
            "n_simulations": m.n_simulations,
            "n_trades_used": m.n_trades_used,
            "risk_of_ruin_pct": round(m.risk_of_ruin_pct, 2),
            "profit_probability_pct": round(m.profit_probability_pct, 2),
            "p95_max_drawdown_pct": round(m.p95_max_drawdown_pct, 2),
            "mean_final_equity": round(m.mean_final_equity, 2),
            "median_final_equity": round(m.median_final_equity, 2),
            "ci_5": round(m.ci_5, 2),
            "ci_25": round(m.ci_25, 2),
            "ci_75": round(m.ci_75, 2),
            "ci_95": round(m.ci_95, 2),
            "sharpe_ratio": round(m.sharpe_ratio, 2),
            "mean_win_rate_pct": round(m.mean_win_rate_pct, 2),
            "mean_pnl_pct": round(m.mean_pnl_pct, 2),
        }

    def _wf_dict(self) -> Dict:
        w = self.wf
        return {
            "aggregate_wfe": round(w.aggregate_wfe, 3),
            "avg_is_return": round(w.avg_is_return, 2),
            "avg_oos_return": round(w.avg_oos_return, 2),
            "overall_degradation": round(w.overall_degradation, 1),
            "profitable_windows": w.profitable_windows,
            "total_windows": w.total_windows,
            "consistency_pct": round(w.consistency_pct, 1),
            "windows": [
                {
                    "window_index": win.window_index,
                    "is_return_pct": round(win.is_return_pct, 2),
                    "oos_return_pct": round(win.oos_return_pct, 2),
                    "is_trades": win.is_trades,
                    "oos_trades": win.oos_trades,
                    "is_win_rate": round(win.is_win_rate, 4),
                    "oos_win_rate": round(win.oos_win_rate, 4),
                    "wfe": round(win.wfe, 3),
                    "degradation_pct": round(win.degradation_pct, 1),
                }
                for win in w.windows
            ],
        }


def _cap_list(lst, max_len):
    """Cap a list to max_len items for JSON size control."""
    if len(lst) <= max_len:
        return lst
    # Evenly sample for equity curves, take last N for trades
    if lst and isinstance(lst[0], (int, float)):
        step = len(lst) / max_len
        return [lst[int(i * step)] for i in range(max_len)]
    return lst[-max_len:]


# ============================================================
# Validation Engine
# ============================================================

class ValidationEngine:
    """
    PPMT Validation Suite Engine.

    Orchestrates three complementary validation tests to determine
    whether the PPMT trading system's results are robust or overfit.

    Scoring system (100 points total):
      P0 Out-of-Sample:  40 points max
        - OOS profitability:   20 pts
        - OOS win rate:        10 pts
        - OOS ratio (WFE):    10 pts
      P1 Monte Carlo:    30 points max
        - Profit probability:  15 pts
        - Risk of ruin:        10 pts
        - P95 drawdown:         5 pts
      P2 Walk-Forward:   30 points max
        - Aggregate WFE:       10 pts
        - Consistency:         10 pts
        - Low degradation:     10 pts

    Verdict thresholds:
      ROBUST:    score >= 70
      MARGINAL:  score >= 45
      OVERFIT:   score < 45 (with sufficient data)
      INSUFFICIENT_DATA: < 10 OOS trades
    """

    def __init__(self, config: Optional[ValidationConfig] = None):
        self.config = config or ValidationConfig()
        self.rng = np.random.default_rng(self.config.seed)

    def run_full_validation(self, df: pd.DataFrame) -> ValidationVerdict:
        """
        Run the complete validation suite: P0 + P1 + P2.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume

        Returns:
            ValidationVerdict with all results and composite scoring
        """
        start_time = time.time()
        cfg = self.config

        verdict = ValidationVerdict(
            symbol=cfg.symbol,
            total_candles=len(df),
        )

        logger.info(f"=== PPMT Validation Suite for {cfg.symbol} ===")
        logger.info(f"  Data: {len(df)} candles")
        logger.info(f"  Train ratio: {cfg.train_ratio}")
        logger.info(f"  MC simulations: {cfg.mc_simulations}")
        logger.info(f"  WF windows: {cfg.wf_window_count}")

        # ─── P0: Out-of-Sample validation ───
        logger.info("P0: Running Out-of-Sample validation...")
        oos_result = self._run_oos_validation(df)
        verdict.oos = oos_result
        logger.info(
            f"  P0 done: IS={oos_result.is_total_trades}t/{oos_result.is_win_rate:.0%}WR/{oos_result.is_total_pnl_pct:+.1f}% | "
            f"OOS={oos_result.oos_total_trades}t/{oos_result.oos_win_rate:.0%}WR/{oos_result.oos_total_pnl_pct:+.1f}% | "
            f"ratio={oos_result.oos_ratio:.3f}"
        )

        # ─── P1: Monte Carlo validation ───
        logger.info("P1: Running Monte Carlo simulation on OOS trades...")
        mc_result = self._run_mc_validation(oos_result.oos_trades)
        verdict.mc = mc_result
        logger.info(
            f"  P1 done: RoR={mc_result.risk_of_ruin_pct:.2f}% | "
            f"Profit prob={mc_result.profit_probability_pct:.1f}% | "
            f"P95 DD={mc_result.p95_max_drawdown_pct:.1f}%"
        )

        # ─── P2: Walk-Forward validation ───
        logger.info("P2: Running Walk-Forward analysis...")
        wf_result = self._run_wf_validation(df)
        verdict.wf = wf_result
        logger.info(
            f"  P2 done: WFE={wf_result.aggregate_wfe:.1%} | "
            f"Consistency={wf_result.consistency_pct:.0f}% | "
            f"Degradation={wf_result.overall_degradation:.1f}%"
        )

        # ─── Composite scoring ───
        p0_score = self._score_p0(verdict.oos)
        p1_score = self._score_p1(verdict.mc, verdict.oos)
        p2_score = self._score_p2(verdict.wf)
        total_score = p0_score + p1_score + p2_score

        verdict.p0_score = p0_score
        verdict.p1_score = p1_score
        verdict.p2_score = p2_score
        verdict.confidence_score = total_score

        # Determine recommendation
        if verdict.oos.oos_total_trades < 10:
            verdict.recommendation = "INSUFFICIENT_DATA"
        elif total_score >= 70:
            verdict.recommendation = "ROBUST"
        elif total_score >= 45:
            verdict.recommendation = "MARGINAL"
        else:
            verdict.recommendation = "OVERFIT"

        verdict.summary = self._generate_summary(verdict)
        verdict.elapsed_seconds = time.time() - start_time

        logger.info(f"=== VERDICT: {verdict.recommendation} (score: {total_score:.0f}/100) ===")

        return verdict

    # ──────────────────────────────────────────────
    # P0: Out-of-Sample Validation
    # ──────────────────────────────────────────────

    def _run_oos_validation(self, df: pd.DataFrame) -> OOSResult:
        """
        P0: Train/test split validation.

        Splits data into training and test sets, builds the PPMT trie
        on training data only, then walks through test data to evaluate
        prediction quality on unseen data.

        Uses V7.9 normalization fix: training z-score stats propagate
        to test encoding for consistent SAX symbol mapping.
        """
        cfg = self.config
        result = OOSResult()

        # Split data
        split_idx = int(len(df) * cfg.train_ratio)
        train_df = df.iloc[:split_idx]
        test_df = df.iloc[split_idx:]

        result.train_candles = len(train_df)
        result.test_candles = len(test_df)

        logger.info(f"  Split: {len(train_df)} train / {len(test_df)} test candles")

        # Create SAX encoder
        encoder = SAXEncoder(
            alphabet_size=cfg.sax_alphabet_size,
            window_size=cfg.sax_window_size,
            strategy=cfg.sax_strategy,
        )

        # V7.9: Encode training data first to establish normalization stats
        train_symbols, paa_mean, paa_std = encoder.encode_with_normalization(train_df)

        # Encode ALL data using training normalization stats
        all_symbols, _, _ = encoder.encode_with_normalization(
            df, paa_mean=paa_mean, paa_std=paa_std
        )

        if not train_symbols or not all_symbols:
            logger.warning("  SAX encoding produced no symbols")
            return result

        # Build PPMT engine on training data
        classifier = AssetClassifier()
        info = classifier.classify(cfg.symbol)

        engine = PPMT(
            symbol=cfg.symbol,
            asset_class=info.asset_class,
            sax_alphabet_size=cfg.sax_alphabet_size,
            sax_window_size=cfg.sax_window_size,
            sax_strategy=cfg.sax_strategy,
            weight_profile=info.weight_profile,
        )

        train_count = engine.build(
            train_df,
            pattern_length=cfg.pattern_length,
            symbols=train_symbols,
        )
        result.patterns_trained = train_count

        if train_count == 0:
            logger.warning("  Trie built 0 patterns from training data")
            return result

        logger.info(f"  Built {train_count} patterns from training data")

        # ── In-Sample backtest (training period) ──
        is_trades = self._match_and_trade(
            symbols=all_symbols[: len(train_symbols)],
            price_df=train_df,
            engine=engine,
            encoder=encoder,
            symbol_offset=0,
        )
        result.is_trades = is_trades
        result.is_total_trades = len(is_trades)
        if is_trades:
            is_wins = sum(1 for t in is_trades if t["won"])
            result.is_win_rate = is_wins / len(is_trades)
            result.is_total_pnl_pct = sum(t["pnl_pct"] for t in is_trades)
            result.is_sharpe = _compute_sharpe([t["pnl_pct"] for t in is_trades])
            result.is_max_dd_pct = _compute_max_dd([t["pnl_pct"] for t in is_trades])

            longs = [t for t in is_trades if t["direction"] == "LONG"]
            shorts = [t for t in is_trades if t["direction"] == "SHORT"]
            result.is_long_trades = len(longs)
            result.is_short_trades = len(shorts)
            result.is_long_wr = sum(1 for t in longs if t["won"]) / len(longs) if longs else 0
            result.is_short_wr = sum(1 for t in shorts if t["won"]) / len(shorts) if shorts else 0

        # ── Out-of-Sample backtest (test period) ──
        n_train_symbols = len(train_symbols)
        oos_trades = self._match_and_trade(
            symbols=all_symbols,
            price_df=df,
            engine=engine,
            encoder=encoder,
            symbol_offset=n_train_symbols,
        )
        result.oos_trades = oos_trades
        result.oos_total_trades = len(oos_trades)

        if oos_trades:
            oos_wins = sum(1 for t in oos_trades if t["won"])
            result.oos_win_rate = oos_wins / len(oos_trades)
            result.oos_total_pnl_pct = sum(t["pnl_pct"] for t in oos_trades)
            result.oos_sharpe = _compute_sharpe([t["pnl_pct"] for t in oos_trades])
            result.oos_max_dd_pct = _compute_max_dd([t["pnl_pct"] for t in oos_trades])

            longs = [t for t in oos_trades if t["direction"] == "LONG"]
            shorts = [t for t in oos_trades if t["direction"] == "SHORT"]
            result.oos_long_trades = len(longs)
            result.oos_short_trades = len(shorts)
            result.oos_long_wr = sum(1 for t in longs if t["won"]) / len(longs) if longs else 0
            result.oos_short_wr = sum(1 for t in shorts if t["won"]) / len(shorts) if shorts else 0

            # Build equity curve
            equity = cfg.initial_capital
            curve = [equity]
            for t in oos_trades:
                equity *= (1 + t["pnl_pct"] / 100)
                curve.append(equity)
            result.oos_equity_curve = curve

        # Compute degradation
        if result.is_total_pnl_pct > 0:
            result.oos_ratio = result.oos_total_pnl_pct / result.is_total_pnl_pct
            result.pnl_degradation_pct = max(0, (1 - result.oos_ratio) * 100)
        elif result.is_total_pnl_pct == 0 and result.oos_total_pnl_pct > 0:
            result.oos_ratio = float("inf")
            result.pnl_degradation_pct = 0
        else:
            result.oos_ratio = 0
            result.pnl_degradation_pct = 100

        result.wr_degradation_pct = max(0, (result.is_win_rate - result.oos_win_rate) * 100)

        # Avg trade PnL ratio (more meaningful than total PnL ratio)
        is_avg_trade = result.is_total_pnl_pct / result.is_total_trades if result.is_total_trades > 0 else 0
        oos_avg_trade = result.oos_total_pnl_pct / result.oos_total_trades if result.oos_total_trades > 0 else 0
        if is_avg_trade > 0:
            result.oos_avg_trade_ratio = oos_avg_trade / is_avg_trade
            result.avg_trade_degradation_pct = max(0, (1 - result.oos_avg_trade_ratio) * 100)
        elif oos_avg_trade > 0:
            result.oos_avg_trade_ratio = float("inf")
            result.avg_trade_degradation_pct = 0
        else:
            result.oos_avg_trade_ratio = 0
            result.avg_trade_degradation_pct = 100

        return result

    def _match_and_trade(
        self,
        symbols: List[str],
        price_df: pd.DataFrame,
        engine: PPMT,
        encoder: SAXEncoder,
        symbol_offset: int = 0,
    ) -> List[Dict]:
        """
        Walk through SAX symbols and generate simulated trades
        by matching patterns against the trie.

        This is the core of the validation: it uses the exact same
        matching logic as the real PPMT engine (exact → fuzzy → prefix
        fallback across N1-N4 trie levels) but only trades on the
        region defined by symbol_offset.

        Args:
            symbols: Full list of SAX symbols (encoded with training stats)
            price_df: Price data for computing entry/exit/PnL
            engine: PPMT engine with trie already built from training data
            encoder: SAX encoder (for window_size reference)
            symbol_offset: Start matching from this symbol index
                           (set to len(train_symbols) for OOS testing)

        Returns:
            List of trade dictionaries with PnL, direction, pattern info
        """
        cfg = self.config
        trades = []
        fuzzy_matcher = FuzzyMatcher(sax_encoder=encoder, threshold=0.85)
        window_size = encoder.window_size

        # Only process symbols from offset onwards
        start = max(symbol_offset, 0)
        end = len(symbols) - cfg.pattern_length

        for i in range(start, max(start, end)):
            current_pattern = symbols[i : i + cfg.pattern_length]

            # Search all trie levels: N4 (most specific) → N1 (universal)
            best_node = None
            best_level = None

            for trie, level_name in [
                (engine.trie_n4, "N4"),
                (engine.trie_n3, "N3"),
                (engine.trie_n2, "N2"),
                (engine.trie_n1, "N1"),
            ]:
                # Exact match first
                node = trie.search(current_pattern)
                if node is not None and node.metadata.historical_count >= max(1, cfg.min_dir_count):
                    best_node = node
                    best_level = level_name
                    break

                # Fuzzy match fallback
                match_result = fuzzy_matcher.best_match(trie, current_pattern)
                if (
                    match_result.node is not None
                    and match_result.node.metadata.historical_count >= max(1, cfg.min_dir_count)
                ):
                    best_node = match_result.node
                    best_level = f"{level_name}(fuzzy)"
                    break

            # Prefix search fallback for shorter patterns
            if best_node is None and cfg.pattern_length > 3:
                for shorter_len in range(cfg.pattern_length - 1, 2, -1):
                    short_pattern = symbols[i : i + shorter_len]
                    for trie, level_name in [
                        (engine.trie_n4, "N4"),
                        (engine.trie_n3, "N3"),
                        (engine.trie_n2, "N2"),
                        (engine.trie_n1, "N1"),
                    ]:
                        node, matched_depth = trie.search_prefix(short_pattern)
                        if (
                            node is not None
                            and matched_depth >= shorter_len
                            and node.metadata.historical_count >= max(1, cfg.min_dir_count)
                        ):
                            best_node = node
                            best_level = f"{level_name}(prefix-{matched_depth})"
                            break
                    if best_node is not None:
                        break

            if best_node is None:
                continue

            meta = best_node.metadata

            # Skip patterns with no directional signal
            if abs(meta.expected_move_pct) < 0.001:
                continue

            direction = "LONG" if meta.expected_move_pct > 0 else "SHORT"

            # Compute entry/exit from price data
            entry_candle = i * window_size
            # Use forward_window to determine trade exit point
            exit_candle = (i + cfg.pattern_length + cfg.forward_window) * window_size

            if entry_candle >= len(price_df) or exit_candle > len(price_df):
                continue

            entry_price = price_df["close"].iloc[entry_candle]
            exit_price = price_df["close"].iloc[exit_candle - 1]

            if direction == "LONG":
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 * cfg.position_size_pct
            else:
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100.0 * cfg.position_size_pct

            won = pnl_pct > 0

            # R:R computation from price window
            window_df = price_df.iloc[entry_candle:exit_candle]
            high = window_df["high"].max()
            low = window_df["low"].min()

            if direction == "LONG":
                favorable = ((high - entry_price) / entry_price) * 100.0
                drawdown = ((low - entry_price) / entry_price) * 100.0
            else:
                favorable = ((entry_price - low) / entry_price) * 100.0
                drawdown = ((entry_price - high) / entry_price) * 100.0

            rr = favorable / abs(drawdown) if abs(drawdown) > 1e-10 else 0

            trades.append({
                "direction": direction,
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "pnl_pct": round(pnl_pct, 4),
                "won": won,
                "rr": round(rr, 3),
                "pattern": "".join(current_pattern),
                "match_level": best_level,
                "candle_idx": entry_candle,
                "win_rate_historical": round(meta.win_rate, 4),
                "expected_move": round(meta.expected_move_pct, 4),
                "sizing_signal": round(meta.sizing_signal, 3),
                "historical_count": meta.historical_count,
            })

        return trades

    # ──────────────────────────────────────────────
    # P1: Monte Carlo Validation
    # ──────────────────────────────────────────────

    def _run_mc_validation(self, oos_trades: List[Dict]) -> MCValidationResult:
        """
        P1: Monte Carlo simulation on out-of-sample trades.

        Resamples the OOS trade PnLs with replacement to test whether
        the system's profitability is robust to trade ordering or
        merely a product of lucky sequencing.
        """
        cfg = self.config
        result = MCValidationResult()

        if len(oos_trades) < 5:
            logger.warning(f"  Insufficient OOS trades for MC ({len(oos_trades)} < 5)")
            return result

        trade_pnl_pcts = np.array([t["pnl_pct"] for t in oos_trades])
        trade_pnls = np.zeros(len(oos_trades))  # Placeholder (not used directly)

        mc_engine = MonteCarloEngine(seed=cfg.seed)
        mc_result = mc_engine.simulate_from_trades(
            trade_pnls=trade_pnls,
            trade_pnl_pcts=trade_pnl_pcts,
            symbol=cfg.symbol,
            initial_capital=cfg.initial_capital,
            n_simulations=cfg.mc_simulations,
            ruin_threshold=cfg.ruin_threshold,
            position_size_pct=0.02,
        )

        result.n_simulations = cfg.mc_simulations
        result.n_trades_used = len(oos_trades)

        if mc_result.stats:
            s = mc_result.stats
            result.risk_of_ruin_pct = s.get("risk_of_ruin_pct", 0)
            result.profit_probability_pct = s.get("profit_probability_pct", 0)
            result.p95_max_drawdown_pct = s.get("worst_max_drawdown_pct", 0)
            result.mean_final_equity = s.get("mean_final_equity", 0)
            result.median_final_equity = s.get("ci_50", 0)
            result.ci_5 = s.get("ci_5", 0)
            result.ci_25 = s.get("ci_25", 0)
            result.ci_75 = s.get("ci_75", 0)
            result.ci_95 = s.get("ci_95", 0)
            result.sharpe_ratio = s.get("sharpe_ratio", 0)
            result.mean_win_rate_pct = s.get("mean_win_rate_pct", 0)
            result.mean_pnl_pct = s.get("mean_pnl_pct", 0)

        return result

    # ──────────────────────────────────────────────
    # P2: Walk-Forward Validation
    # ──────────────────────────────────────────────

    def _run_wf_validation(self, df: pd.DataFrame) -> WFValidationResult:
        """
        P2: Walk-Forward analysis with rolling windows.

        Divides the dataset into K windows. For each window:
          1. Train on the first portion (wf_train_ratio)
          2. Test on the remaining portion
          3. Compute IS and OOS returns
          4. Calculate Walk-Forward Efficiency (WFE = OOS/IS)

        This reveals whether the system is consistently profitable
        across different market regimes, not just a single period.
        """
        cfg = self.config
        result = WFValidationResult()
        total_candles = len(df)

        # Calculate window sizes
        window_total = total_candles // cfg.wf_window_count
        train_size = int(window_total * cfg.wf_train_ratio)
        test_size = window_total - train_size

        if train_size < 100 or test_size < 50:
            logger.warning(
                f"  Walk-forward windows too small: train={train_size}, test={test_size}"
            )
            return result

        logger.info(f"  Window sizes: train={train_size}, test={test_size} candles")

        # Create reusable objects
        classifier = AssetClassifier()
        info = classifier.classify(cfg.symbol)

        for w_idx in range(cfg.wf_window_count):
            window_start = w_idx * window_total
            train_start = window_start
            train_end = window_start + train_size
            test_start = train_end
            test_end = min(window_start + window_total, total_candles)

            if test_end <= test_start + 50:
                break

            train_df = df.iloc[train_start:train_end]
            window_df = df.iloc[train_start:test_end]

            # Encode with V7.9 normalization
            encoder = SAXEncoder(
                alphabet_size=cfg.sax_alphabet_size,
                window_size=cfg.sax_window_size,
                strategy=cfg.sax_strategy,
            )

            try:
                train_symbols, paa_mean, paa_std = encoder.encode_with_normalization(train_df)
                all_symbols, _, _ = encoder.encode_with_normalization(
                    window_df, paa_mean=paa_mean, paa_std=paa_std
                )
            except Exception as e:
                logger.warning(f"  Window {w_idx}: encoding failed: {e}")
                result.windows.append(
                    WFWindowResult(window_index=w_idx,
                                   train_start_idx=train_start, train_end_idx=train_end,
                                   test_start_idx=test_start, test_end_idx=test_end)
                )
                continue

            if not train_symbols or not all_symbols:
                result.windows.append(
                    WFWindowResult(window_index=w_idx,
                                   train_start_idx=train_start, train_end_idx=train_end,
                                   test_start_idx=test_start, test_end_idx=test_end)
                )
                continue

            # Build engine on training data
            engine = PPMT(
                symbol=cfg.symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=cfg.sax_alphabet_size,
                sax_window_size=cfg.sax_window_size,
                sax_strategy=cfg.sax_strategy,
                weight_profile=info.weight_profile,
            )
            train_count = engine.build(
                train_df,
                pattern_length=cfg.pattern_length,
                symbols=train_symbols,
            )

            if train_count == 0:
                result.windows.append(
                    WFWindowResult(window_index=w_idx,
                                   train_start_idx=train_start, train_end_idx=train_end,
                                   test_start_idx=test_start, test_end_idx=test_end)
                )
                continue

            # In-sample trades (training period)
            is_trades = self._match_and_trade(
                symbols=all_symbols[: len(train_symbols)],
                price_df=train_df,
                engine=engine,
                encoder=encoder,
                symbol_offset=0,
            )
            is_return = sum(t["pnl_pct"] for t in is_trades) if is_trades else 0
            is_wr = sum(1 for t in is_trades if t["won"]) / len(is_trades) if is_trades else 0

            # Out-of-sample trades (test period)
            oos_trades = self._match_and_trade(
                symbols=all_symbols,
                price_df=window_df,
                engine=engine,
                encoder=encoder,
                symbol_offset=len(train_symbols),
            )
            oos_return = sum(t["pnl_pct"] for t in oos_trades) if oos_trades else 0
            oos_wr = sum(1 for t in oos_trades if t["won"]) / len(oos_trades) if oos_trades else 0

            # WFE calculation
            if is_return > 0:
                wfe = max(0, min(1.5, oos_return / is_return))
                degradation = max(0, (1 - wfe) * 100)
            elif oos_return > 0:
                wfe = 1.5  # OOS positive while IS zero is actually good
                degradation = 0
            else:
                wfe = 0
                degradation = 100

            result.windows.append(WFWindowResult(
                window_index=w_idx,
                train_start_idx=train_start,
                train_end_idx=train_end,
                test_start_idx=test_start,
                test_end_idx=test_end,
                is_return_pct=round(is_return, 2),
                oos_return_pct=round(oos_return, 2),
                is_trades=len(is_trades),
                oos_trades=len(oos_trades),
                is_win_rate=is_wr,
                oos_win_rate=oos_wr,
                wfe=round(wfe, 3),
                degradation_pct=round(degradation, 1),
            ))

            logger.info(
                f"  Window {w_idx + 1}/{cfg.wf_window_count}: "
                f"IS={len(is_trades)}t/{is_return:+.1f}% | "
                f"OOS={len(oos_trades)}t/{oos_return:+.1f}% | "
                f"WFE={wfe:.2f}"
            )

        # Aggregate
        if result.windows:
            wfes = [w.wfe for w in result.windows if w.wfe > 0]
            is_returns = [w.is_return_pct for w in result.windows]
            oos_returns = [w.oos_return_pct for w in result.windows]

            result.aggregate_wfe = float(np.mean(wfes)) if wfes else 0
            result.avg_is_return = float(np.mean(is_returns)) if is_returns else 0
            result.avg_oos_return = float(np.mean(oos_returns)) if oos_returns else 0
            result.profitable_windows = sum(1 for w in result.windows if w.oos_return_pct > 0)
            result.total_windows = len(result.windows)
            result.consistency_pct = (
                result.profitable_windows / result.total_windows * 100
                if result.total_windows > 0
                else 0
            )

            if result.avg_is_return > 0:
                result.overall_degradation = max(
                    0, (1 - result.avg_oos_return / result.avg_is_return) * 100
                )
            else:
                result.overall_degradation = 100

        return result

    # ──────────────────────────────────────────────
    # Scoring Functions
    # ──────────────────────────────────────────────

    @staticmethod
    def _score_p0(oos: OOSResult) -> float:
        """
        Score P0 Out-of-Sample validation (0-40 points).

        Evaluates:
        - OOS profitability (0-20): Is the system profitable on unseen data?
        - OOS win rate (0-10): Does it maintain a positive edge?
        - OOS ratio / WFE (0-10): How much degrades from IS to OOS?
        """
        score = 0.0

        if oos.oos_total_trades < 10:
            return max(0, oos.oos_total_trades * 0.5)  # Tiny partial credit

        # OOS profitability (20 pts)
        if oos.oos_total_pnl_pct > 0:
            score += 10
            if oos.oos_total_pnl_pct > 50:
                score += 5
            if oos.oos_total_pnl_pct > 100:
                score += 5

        # OOS win rate (10 pts)
        if oos.oos_win_rate >= 0.60:
            score += 10
        elif oos.oos_win_rate >= 0.55:
            score += 8
        elif oos.oos_win_rate >= 0.50:
            score += 6
        elif oos.oos_win_rate >= 0.45:
            score += 3

        # OOS avg trade ratio (better than total PnL ratio)
        # Compares per-trade profitability, not total (which is skewed by trade count)
        ratio = oos.oos_avg_trade_ratio if oos.oos_avg_trade_ratio > 0 else oos.oos_ratio
        if ratio >= 0.7:
            score += 10
        elif ratio >= 0.5:
            score += 8
        elif ratio >= 0.3:
            score += 5
        elif ratio > 0:
            score += 2

        return min(score, 40)

    @staticmethod
    def _score_p1(mc: MCValidationResult, oos: OOSResult) -> float:
        """
        Score P1 Monte Carlo validation (0-30 points).

        Evaluates:
        - Profit probability (0-15): Chance of being profitable in random orderings
        - Risk of ruin (0-10): Probability of catastrophic loss
        - P95 drawdown (0-5): Worst-case drawdown at 95th percentile
        """
        score = 0.0

        if mc.n_trades_used < 10:
            return 0

        # Profit probability (15 pts)
        if mc.profit_probability_pct >= 90:
            score += 15
        elif mc.profit_probability_pct >= 80:
            score += 12
        elif mc.profit_probability_pct >= 70:
            score += 10
        elif mc.profit_probability_pct >= 60:
            score += 7
        elif mc.profit_probability_pct >= 50:
            score += 4

        # Risk of ruin (10 pts)
        if mc.risk_of_ruin_pct < 0.5:
            score += 10
        elif mc.risk_of_ruin_pct < 1:
            score += 9
        elif mc.risk_of_ruin_pct < 2:
            score += 8
        elif mc.risk_of_ruin_pct < 5:
            score += 6
        elif mc.risk_of_ruin_pct < 10:
            score += 3

        # P95 drawdown (5 pts)
        if mc.p95_max_drawdown_pct < 15:
            score += 5
        elif mc.p95_max_drawdown_pct < 25:
            score += 4
        elif mc.p95_max_drawdown_pct < 35:
            score += 3
        elif mc.p95_max_drawdown_pct < 50:
            score += 1

        return min(score, 30)

    @staticmethod
    def _score_p2(wf: WFValidationResult) -> float:
        """
        Score P2 Walk-Forward validation (0-30 points).

        Evaluates:
        - Aggregate WFE (0-10): Average Walk-Forward Efficiency
        - Consistency (0-10): % of profitable windows
        - Low degradation (0-10): How much performance degrades IS → OOS
        """
        score = 0.0

        if wf.total_windows < 2:
            return 0

        # Aggregate WFE (10 pts)
        if wf.aggregate_wfe >= 0.7:
            score += 10
        elif wf.aggregate_wfe >= 0.5:
            score += 8
        elif wf.aggregate_wfe >= 0.3:
            score += 5
        elif wf.aggregate_wfe >= 0.1:
            score += 2

        # Consistency (10 pts)
        if wf.consistency_pct >= 80:
            score += 10
        elif wf.consistency_pct >= 70:
            score += 8
        elif wf.consistency_pct >= 60:
            score += 6
        elif wf.consistency_pct >= 50:
            score += 3

        # Low degradation (10 pts)
        if wf.overall_degradation < 20:
            score += 10
        elif wf.overall_degradation < 30:
            score += 8
        elif wf.overall_degradation < 40:
            score += 6
        elif wf.overall_degradation < 60:
            score += 3
        elif wf.overall_degradation < 80:
            score += 1

        return min(score, 30)

    # ──────────────────────────────────────────────
    # Summary Generation
    # ──────────────────────────────────────────────

    @staticmethod
    def _generate_summary(verdict: ValidationVerdict) -> str:
        """Generate a human-readable summary of the validation results."""
        oos = verdict.oos
        mc = verdict.mc
        wf = verdict.wf

        rec_emoji = {
            "ROBUST": "✓",
            "MARGINAL": "⚠",
            "OVERFIT": "✗",
            "INSUFFICIENT_DATA": "?",
        }.get(verdict.recommendation, "?")

        lines = [
            f"PPMT Validation Suite — {rec_emoji} {verdict.recommendation} "
            f"(score: {verdict.confidence_score:.0f}/100)",
            f"",
            f"P0 Out-of-Sample ({oos.train_candles} train / {oos.test_candles} test):",
            f"  Patterns trained: {oos.patterns_trained}",
            f"  IS: {oos.is_total_trades} trades, WR={oos.is_win_rate:.1%}, "
            f"PnL={oos.is_total_pnl_pct:+.1f}%, Sharpe={oos.is_sharpe:.2f}",
            f"  OOS: {oos.oos_total_trades} trades, WR={oos.oos_win_rate:.1%}, "
            f"PnL={oos.oos_total_pnl_pct:+.1f}%, Sharpe={oos.oos_sharpe:.2f}",
            f"  OOS Ratio: {oos.oos_ratio:.3f}, "
            f"Degradation: {oos.pnl_degradation_pct:.1f}%",
            f"",
            f"P1 Monte Carlo ({mc.n_simulations} sims on {mc.n_trades_used} OOS trades):",
            f"  Profit Probability: {mc.profit_probability_pct:.1f}%",
            f"  Risk of Ruin: {mc.risk_of_ruin_pct:.2f}%",
            f"  P95 Max DD: {mc.p95_max_drawdown_pct:.1f}%",
            f"  Median Final Equity: ${mc.median_final_equity:,.0f}",
            f"",
            f"P2 Walk-Forward ({wf.total_windows} windows):",
            f"  Aggregate WFE: {wf.aggregate_wfe:.1%}",
            f"  Consistency: {wf.consistency_pct:.0f}% profitable windows",
            f"  Avg IS: {wf.avg_is_return:+.1f}%, Avg OOS: {wf.avg_oos_return:+.1f}%",
            f"  Overall Degradation: {wf.overall_degradation:.1f}%",
            f"",
            f"Score Breakdown: P0={verdict.p0_score:.0f}/40 "
            f"P1={verdict.p1_score:.0f}/30 P2={verdict.p2_score:.0f}/30",
        ]

        return "\n".join(lines)


# ============================================================
# Utility Functions
# ============================================================

def _compute_sharpe(pnl_pcts: List[float]) -> float:
    """Compute annualized Sharpe ratio from trade PnL percentages."""
    if len(pnl_pcts) < 2:
        return 0.0
    returns = [p / 100 for p in pnl_pcts]
    mean_ret = np.mean(returns)
    std_ret = np.std(returns, ddof=1)
    if std_ret > 1e-10:
        return float((mean_ret / std_ret) * np.sqrt(252))
    return 0.0


def _compute_max_dd(pnl_pcts: List[float]) -> float:
    """Compute maximum drawdown percentage from trade PnL series."""
    if not pnl_pcts:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnl_pcts:
        cumulative += pnl
        peak = max(peak, cumulative)
        dd = cumulative - peak
        max_dd = min(max_dd, dd)
    return abs(max_dd)
