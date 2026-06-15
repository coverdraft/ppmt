"""
Portfolio Runner - Multi-Engine Orchestrator for PPMT v0.17.0

The Portfolio Runner orchestrates MULTIPLE PPMT engines simultaneously,
each processing its own token's data, while sharing capital allocation
through a PortfolioManager. This is the bridge between single-token
PaperTrader and true multi-token portfolio trading.

Architecture:
  ┌──────────────────────────────────────────────────────────────────┐
  │                      PortfolioRunner                              │
  │                                                                  │
  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
  │  │  BTC Engine  │  │  SOL Engine  │  │  DOGE Engine │  ...       │
  │  │  ├─Trie N1-4 │  │  ├─Trie N1-4 │  │  ├─Trie N1-4│             │
  │  │  ├─SAX Enc   │  │  ├─SAX Enc   │  │  ├─SAX Enc  │             │
  │  │  ├─PredEng   │  │  ├─PredEng   │  │  ├─PredEng  │             │
  │  │  ├─FuzzyMat  │  │  ├─FuzzyMat  │  │  ├─FuzzyMat │             │
  │  │  └─Regime    │  │  └─Regime    │  │  └─Regime   │             │
  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
  │         │ signals         │ signals         │ signals             │
  │         ▼                 ▼                 ▼                     │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │              Signal Prioritization Queue                  │   │
  │  │  1. Rank by quality_score × confidence × sizing_mult     │   │
  │  │  2. Filter by portfolio governance (capital, correlation)│   │
  │  │  3. Execute top-N approved signals                       │   │
  │  └──────────────────────────────────────────────────────────┘   │
  │         │                                                        │
  │         ▼                                                        │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │                  PortfolioManager                         │   │
  │  │  • Capital allocation per token (risk budgeting)          │   │
  │  │  • Cross-token correlation matrix                        │   │
  │  │  • Regime-aware allocation shifts                        │   │
  │  │  • Exposure caps + circuit breakers                      │   │
  │  │  • Kill switch (portfolio-wide)                          │   │
  │  └──────────────────────────────────────────────────────────┘   │
  └──────────────────────────────────────────────────────────────────┘

Key Difference from PortfolioBacktester:
  PortfolioBacktester takes an EXTERNAL signal_generator_func and
  doesn't embed PPMT engines. PortfolioRunner CREATES and MANAGES
  one PPMT engine per token, complete with SAX encoding, Trie
  matching, PredictionEngine, FuzzyMatcher, and Living Trie
  feedback — just like PaperTrader does for a single token,
  but across all tokens simultaneously with shared capital.

Key Difference from PaperTrader:
  PaperTrader runs a single token with its own isolated capital.
  PortfolioRunner runs all tokens under shared PortfolioManager
  governance, with signal prioritization when multiple tokens
  signal at the same candle, and cross-token correlation checks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.sax import SAXEncoder
from ppmt.core.regime import RegimeDetector, RegimeInfo
from ppmt.core.profiles import TokenProfile, TIMEFRAME_ALPHA_DEFAULTS, TradingCalibrationEngine
from ppmt.core.matcher import FuzzyMatcher
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.signal import Signal, SignalType
from ppmt.engine.paper_trader import PaperTrade, _record_observation, compute_atr_pct
from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig, TokenSlot
from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine, CorrelationMethod
from ppmt.risk.regime_allocator import RegimeAwareAllocator
from ppmt.risk.manager import RiskManager, RiskConfig, Position

console = Console()


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class TokenEngine:
    """Per-token PPMT engine state for portfolio running.

    Holds all the components that PaperTrader creates for a single token,
    but in a form that PortfolioRunner can manage across multiple tokens.
    """

    symbol: str
    asset_class: str = ""
    timeframe: str = "1h"

    # Core engines
    sax_encoder: Optional[SAXEncoder] = None
    fuzzy_matcher: Optional[FuzzyMatcher] = None
    pred_engine: Optional[PredictionEngine] = None
    ppmt_engine: Optional[PPMT] = None
    regime_detector: Optional[RegimeDetector] = None

    # Trie levels
    trie_n1: Optional[object] = None
    trie_n2: Optional[object] = None
    trie_n3: Optional[object] = None  # Primary trie
    trie_n4: Optional[object] = None

    # Token profile
    token_profile: Optional[TokenProfile] = None

    # SAX state
    sax_symbols: list = field(default_factory=list)
    current_sax_idx: int = 0
    has_multi_level: bool = False

    # Position state (per-token)
    current_position: Optional[PaperTrade] = None
    trade_counter: int = 0
    consecutive_breaks: int = 0
    last_losing_trade_sym_idx: int = -999
    cooldown_filter_count: int = 0

    # Regime state
    current_regime: str = "UNKNOWN"
    regime_confidence: float = 0.0

    # ATR for dynamic SL/TP
    atr_pct: Optional[np.ndarray] = None

    # Trailing stop state
    trailing_activated: bool = False
    trailing_sl: float = 0.0

    # Counters
    signals_generated: int = 0
    signals_approved: int = 0
    signals_rejected: int = 0

    # Recalibration state
    candles_since_calibration: int = 0
    sax_alphabet_size: int = 3
    sax_window_size: int = 7

    @property
    def is_in_position(self) -> bool:
        return self.current_position is not None

    @property
    def primary_trie(self):
        return self.trie_n3


@dataclass
class PortfolioRunnerConfig:
    """Configuration for the PortfolioRunner.

    Attributes:
        tokens: List of token symbols to run.
        timeframe: Candle timeframe for all tokens.
        initial_capital: Total portfolio starting capital.
        allocation_method: How to allocate capital across tokens.
        pattern_length: SAX blocks per pattern.
        auto_calibrate: Whether to auto-calibrate α/W per token.
        recalibration_interval: Re-calibrate every N candles. 0 = never.
        pruning_interval: Prune tries every N SAX steps. 0 = never.
        living_trie: Whether to update tries with trade outcomes.
        min_confidence: Minimum signal confidence to enter.
        pattern_break_grace: Consecutive breaks before exit.
        reentry_cooldown: SAX steps to wait after losing trade.
        regime_aware: Whether to adjust sizing by regime.
        use_multi_level: Whether to use 4-level matching.
        start_offset: Warm-up candles to skip.
        max_positions_per_token: Max simultaneous positions per token.
        max_portfolio_positions: Max total portfolio positions.
        max_portfolio_exposure_pct: Max portfolio exposure.
        kill_switch_drawdown_pct: Portfolio drawdown kill switch.
        rebalance_interval_candles: Rebalance every N candles. 0 = never.
        regime_shift_rebalance: Whether to rebalance on regime changes.
        signal_priority_method: How to prioritize simultaneous signals.
    """

    tokens: list = field(default_factory=lambda: [
        "BTC/USDT", "ETH/USDT", "SOL/USDT",
    ])
    timeframe: str = "1h"
    initial_capital: float = 50_000.0
    allocation_method: str = "REGIME_AWARE"
    pattern_length: int = 5
    auto_calibrate: bool = True
    recalibration_interval: int = 0
    pruning_interval: int = 1000
    living_trie: bool = True
    min_confidence: float = 0.20
    pattern_break_grace: int = 2
    reentry_cooldown: int = 1
    regime_aware: bool = True
    use_multi_level: bool = True
    start_offset: int = 200
    max_positions_per_token: int = 2
    max_portfolio_positions: int = 8
    max_portfolio_exposure_pct: float = 0.80
    kill_switch_drawdown_pct: float = 0.20
    rebalance_interval_candles: int = 24
    regime_shift_rebalance: bool = True
    signal_priority_method: str = "QUALITY_WEIGHTED"
    # QUALITY_WEIGHTED: quality_score × confidence × sizing_multiplier
    # CONFIDENCE_FIRST: Sort by confidence, then quality
    # EXPECTED_VALUE: Sort by expected_profit_ahead


@dataclass
class PortfolioRunnerResult:
    """Result of a portfolio runner session.

    Combines per-token results with portfolio-level metrics.
    """

    tokens: dict = field(default_factory=dict)  # symbol -> dict of metrics
    total_capital: float = 0.0
    final_value: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_signals: int = 0
    signals_approved: int = 0
    signals_rejected: int = 0
    rejection_rate: float = 0.0
    equity_curve: list = field(default_factory=list)
    drawdown_curve: list = field(default_factory=list)
    rebalance_count: int = 0
    regime_transitions: list = field(default_factory=list)
    duration_candles: int = 0
    signal_log: list = field(default_factory=list)  # Recent signal events

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "total_capital": round(self.total_capital, 2),
            "final_value": round(self.final_value, 2),
            "total_return_pct": round(self.total_return_pct * 100, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "calmar_ratio": round(self.calmar_ratio, 3),
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "win_rate": round(self.total_wins / max(1, self.total_trades), 3),
            "total_signals": self.total_signals,
            "signals_approved": self.signals_approved,
            "signals_rejected": self.signals_rejected,
            "rejection_rate": round(self.rejection_rate * 100, 1),
            "rebalance_count": self.rebalance_count,
            "regime_transitions": len(self.regime_transitions),
            "duration_candles": self.duration_candles,
            "tokens": self.tokens,
        }


# ---------------------------------------------------------------------------
# PortfolioRunner
# ---------------------------------------------------------------------------

class PortfolioRunner:
    """
    Multi-Engine Portfolio Runner for PPMT v0.17.0.

    Orchestrates multiple PPMT engines (one per token) with shared
    capital management, signal prioritization, and portfolio-level
    governance. Each engine runs the full PPMT pipeline (SAX → Trie
    → Prediction → Signal → Risk) independently, but all position
    decisions pass through PortfolioManager for cross-token checks.

    The runner processes candles time-synchronously: at each SAX
    boundary, it:
      1. Processes all tokens that have a new SAX symbol
      2. Collects entry signals from all tokens
      3. Prioritizes signals (quality-weighted ranking)
      4. Routes each signal through PortfolioManager.can_open_position()
      5. Executes approved positions via PortfolioManager.open_position()
      6. Checks SL/TP and pattern breaks across all positions
      7. Updates Living Trie for closed trades
      8. Updates correlation matrix and regime tracking
      9. Periodic rebalancing if configured

    Usage:
        runner = PortfolioRunner(config=PortfolioRunnerConfig(
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            initial_capital=50_000,
        ))
        result = runner.run()
        runner.display_result(result)
    """

    def __init__(self, config: Optional[PortfolioRunnerConfig] = None):
        self.config = config or PortfolioRunnerConfig()
        cfg = self.config

        # Portfolio infrastructure
        pm_config = PortfolioConfig(
            initial_capital=cfg.initial_capital,
            tokens=cfg.tokens,
            allocation_method=cfg.allocation_method,
            max_positions_per_token=cfg.max_positions_per_token,
            max_portfolio_positions=cfg.max_portfolio_positions,
            max_portfolio_exposure_pct=cfg.max_portfolio_exposure_pct,
            kill_switch_drawdown_pct=cfg.kill_switch_drawdown_pct,
            rebalance_interval_candles=cfg.rebalance_interval_candles,
        )
        self.portfolio = PortfolioManager(config=pm_config)
        self.correlation = CrossTokenCorrelationEngine(
            tokens=cfg.tokens, window=60,
        )
        self.allocator = RegimeAwareAllocator()
        self.classifier = AssetClassifier()
        self.storage = PPMTStorage()

        # Per-token engines
        self.engines: dict[str, TokenEngine] = {}

        # Runner state
        self._equity_curve: list[float] = []
        self._drawdown_curve: list[float] = []
        self._peak_value: float = cfg.initial_capital
        self._candle_idx: int = 0
        self._rebalance_count: int = 0
        self._regime_transitions: list[dict] = []
        self._prev_regimes: dict[str, str] = {}
        self._signal_log: list[dict] = []

        # Risk config for per-token RiskManagers (inside PortfolioManager slots)
        self.risk_config = RiskConfig(
            base_position_size_pct=0.01,
            max_position_size_pct=0.04,
            min_position_size_pct=0.005,
            min_risk_reward=1.0,
            max_daily_loss_pct=0.10,
            max_drawdown_pct=0.80,
            min_quality_score=0.0,
        )

    # -------------------------------------------------------------------
    # Engine Initialization
    # -------------------------------------------------------------------

    def _init_token_engine(self, symbol: str) -> Optional[TokenEngine]:
        """Initialize a complete PPMT engine for one token.

        This mirrors PaperTrader.run()'s initialization for a single token,
        but stores all components in a TokenEngine object for later use
        in the portfolio loop.
        """
        cfg = self.config
        engine = TokenEngine(symbol=symbol, timeframe=cfg.timeframe)

        # Classify asset
        info = self.classifier.classify(symbol)
        engine.asset_class = info.asset_class

        # Load data
        df = self.storage.load_ohlcv(symbol, cfg.timeframe)
        if df.empty:
            console.print(f"[red]  No data for {symbol}. Skipping.[/red]")
            return None
        engine._df = df  # Store for later use in the loop

        # TokenProfile integration (same as PaperTrader)
        token_profile = None
        if cfg.auto_calibrate:
            saved_profile_dict = self.storage.load_token_profile(symbol, cfg.timeframe)
            if saved_profile_dict is not None:
                try:
                    token_profile = TokenProfile.from_dict(saved_profile_dict)
                except Exception:
                    token_profile = None

            if token_profile is None:
                token_profile = TokenProfile.from_timeframe(
                    symbol=symbol,
                    asset_class=info.asset_class,
                    timeframe=cfg.timeframe,
                )

            engine.sax_alphabet_size = token_profile.sax_alphabet_size
            engine.sax_window_size = token_profile.sax_window_size

            # Auto-calibrate if profile not already calibrated
            profile_already_calibrated = (
                token_profile.calibration_date != ""
                and token_profile.calibration_metric > 0
            )
            if not profile_already_calibrated and cfg.auto_calibrate and len(df) >= 1000:
                try:
                    calibrator = TradingCalibrationEngine(
                        train_ratio=0.70,
                        pattern_length=cfg.pattern_length,
                        timeframe=cfg.timeframe,
                    )
                    cal_profile, cal_results = calibrator.calibrate(
                        df, symbol=symbol, verbose=False
                    )
                    cal_alpha = cal_profile.sax_alphabet_size
                    cal_window = cal_profile.sax_window_size

                    if cal_alpha != engine.sax_alphabet_size or cal_window != engine.sax_window_size:
                        engine.sax_alphabet_size = cal_alpha
                        engine.sax_window_size = cal_window

                        cal_best = [r for r in cal_results
                                    if r.alphabet_size == cal_alpha
                                    and r.window_size == cal_window][0]

                        grid = {
                            f"a{r.alphabet_size}_w{r.window_size}": {
                                "trading_metric": round(r.trading_metric, 4),
                                "total_pnl_pct": round(r.total_pnl_pct, 2),
                                "win_rate": round(r.win_rate, 4),
                                "total_trades": r.total_trades,
                            }
                            for r in cal_results
                        }
                        token_profile.update_from_calibration(
                            best_alpha=cal_alpha,
                            best_window=cal_window,
                            metric=cal_best.trading_metric,
                            grid=grid,
                            n_samples=len(df),
                        )
                except Exception:
                    pass  # Use defaults

            # Save calibrated profile
            if token_profile.calibration_date:
                try:
                    self.storage.save_token_profile(symbol, cfg.timeframe, token_profile.to_dict())
                except Exception:
                    pass

        else:
            # Fallback defaults
            tf_defaults = TIMEFRAME_ALPHA_DEFAULTS.get(cfg.timeframe, TIMEFRAME_ALPHA_DEFAULTS["1h"])
            engine.sax_alphabet_size = tf_defaults["sax_alphabet_size"]
            engine.sax_window_size = tf_defaults["sax_window_size"]
            token_profile = TokenProfile.from_timeframe(
                symbol=symbol, asset_class=info.asset_class, timeframe=cfg.timeframe,
            )

        engine.token_profile = token_profile

        # Get fuzzy_threshold from profile
        fuzzy_threshold = token_profile.fuzzy_threshold if token_profile else 0.80

        # Load/build tries
        all_tries = self.storage.load_all_tries(symbol)
        trie_n1 = all_tries["n1"]
        trie_n2 = all_tries["n2"]
        trie_n3 = all_tries["n3"]
        trie_n4 = all_tries["n4"]

        has_multi_level = (
            cfg.use_multi_level
            and trie_n1 is not None
            and trie_n2 is not None
            and trie_n4 is not None
        )

        if trie_n3 is None:
            ppmt_build = PPMT(
                symbol=symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=engine.sax_alphabet_size,
                sax_window_size=engine.sax_window_size,
                sax_strategy="ohlcv",
                fuzzy_threshold=fuzzy_threshold,
                weight_profile=info.weight_profile,
            )
            ppmt_build.build(df, pattern_length=cfg.pattern_length)
            trie_n1 = ppmt_build.trie_n1
            trie_n2 = ppmt_build.trie_n2
            trie_n3 = ppmt_build.trie_n3
            trie_n4 = ppmt_build.trie_n4
            has_multi_level = True
        else:
            # Propagate metadata
            trie_n3.propagate_metadata()
            if has_multi_level:
                for t in [trie_n1, trie_n2, trie_n4]:
                    t.propagate_metadata()

        engine.trie_n1 = trie_n1
        engine.trie_n2 = trie_n2
        engine.trie_n3 = trie_n3
        engine.trie_n4 = trie_n4
        engine.has_multi_level = has_multi_level

        # Create SAX encoder
        sax_encoder = SAXEncoder(
            alphabet_size=engine.sax_alphabet_size,
            window_size=engine.sax_window_size,
            strategy="ohlcv",
        )
        engine.sax_encoder = sax_encoder

        # Encode full data
        all_sax_symbols = sax_encoder.encode(df)
        if not all_sax_symbols:
            console.print(f"[red]  Could not SAX encode data for {symbol}. Skipping.[/red]")
            return None
        engine.sax_symbols = all_sax_symbols

        # Create FuzzyMatcher
        engine.fuzzy_matcher = FuzzyMatcher(
            sax_encoder=sax_encoder,
            threshold=fuzzy_threshold,
            max_edit_distance=2,
        )

        # Create PredictionEngine
        engine.pred_engine = PredictionEngine(trie_n3, prediction_depth=cfg.pattern_length)

        # Create PPMT engine for 4-level matching
        if has_multi_level:
            ppmt_engine = PPMT(
                symbol=symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=engine.sax_alphabet_size,
                sax_window_size=engine.sax_window_size,
                sax_strategy="ohlcv",
                fuzzy_threshold=fuzzy_threshold,
                weight_profile=info.weight_profile,
            )
            ppmt_engine.set_tries(trie_n1, trie_n2, trie_n3, trie_n4)
            ppmt_engine.adapt_weights()
            engine.ppmt_engine = ppmt_engine

        # Create RegimeDetector
        engine.regime_detector = RegimeDetector()

        # Compute ATR
        engine.atr_pct = compute_atr_pct(df, period=14)

        console.print(
            f"  [green]Initialized {symbol}[/green]: "
            f"α={engine.sax_alphabet_size}, W={engine.sax_window_size}, "
            f"SAX={len(all_sax_symbols)} symbols, "
            f"N3={trie_n3.pattern_count} patterns, "
            f"multi={'ON' if has_multi_level else 'OFF'}"
        )

        return engine

    # -------------------------------------------------------------------
    # Main Run Loop
    # -------------------------------------------------------------------

    def run(self, progress: bool = True) -> PortfolioRunnerResult:
        """
        Run the portfolio trading session.

        Initializes one PPMT engine per token, then processes candles
        time-synchronously across all tokens. Signals from all tokens
        are prioritized and routed through PortfolioManager governance.

        Args:
            progress: Whether to show a progress bar.

        Returns:
            PortfolioRunnerResult with full session metrics.
        """
        cfg = self.config

        # Initialize all token engines
        console.print(f"\n[bold cyan]Portfolio Runner: Initializing {len(cfg.tokens)} tokens[/bold cyan]")
        console.print(f"  Capital: ${cfg.initial_capital:,.2f}")
        console.print(f"  Allocation: {cfg.allocation_method}")
        console.print(f"  Timeframe: {cfg.timeframe}")
        console.print()

        for symbol in cfg.tokens:
            engine = self._init_token_engine(symbol)
            if engine is not None:
                self.engines[symbol] = engine

        if not self.engines:
            console.print("[red]No engines initialized. Aborting.[/red]")
            return PortfolioRunnerResult(total_capital=cfg.initial_capital)

        # Determine session length (shortest data series in CANDLE space)
        # Note: SAX symbols ≈ candles / window_size, but we iterate in CANDLE space
        # and process SAX boundaries as they occur.
        min_candles = min(len(eng._df) for eng in self.engines.values())
        total_candles = min_candles - cfg.start_offset
        if total_candles <= 0:
            console.print("[red]Not enough data for any token. Aborting.[/red]")
            return PortfolioRunnerResult(total_capital=cfg.initial_capital)

        # Reset state
        self._reset()

        if progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console,
            ) as progress_bar:
                task = progress_bar.add_task(
                    f"[cyan]Running {len(self.engines)} tokens...",
                    total=total_candles,
                )
                self._run_loop(total_candles, progress_bar, task)
        else:
            self._run_loop(total_candles, None, None)

        # Build final result
        return self._build_result(total_candles)

    def _reset(self) -> None:
        """Reset runner state for a fresh session."""
        cfg = self.config

        pm_config = PortfolioConfig(
            initial_capital=cfg.initial_capital,
            tokens=list(self.engines.keys()),
            allocation_method=cfg.allocation_method,
            max_positions_per_token=cfg.max_positions_per_token,
            max_portfolio_positions=cfg.max_portfolio_positions,
            max_portfolio_exposure_pct=cfg.max_portfolio_exposure_pct,
            kill_switch_drawdown_pct=cfg.kill_switch_drawdown_pct,
            rebalance_interval_candles=cfg.rebalance_interval_candles,
        )
        self.portfolio = PortfolioManager(config=pm_config)
        self.correlation = CrossTokenCorrelationEngine(
            tokens=list(self.engines.keys()), window=60,
        )

        self._equity_curve = []
        self._drawdown_curve = []
        self._peak_value = cfg.initial_capital
        self._candle_idx = 0
        self._rebalance_count = 0
        self._regime_transitions = []
        self._prev_regimes = {}
        self._signal_log = []

        # Reset per-token engine state
        for eng in self.engines.values():
            eng.current_position = None
            eng.trade_counter = 0
            eng.consecutive_breaks = 0
            eng.last_losing_trade_sym_idx = -999
            eng.cooldown_filter_count = 0
            eng.trailing_activated = False
            eng.trailing_sl = 0.0
            eng.candles_since_calibration = 0
            eng.signals_generated = 0
            eng.signals_approved = 0
            eng.signals_rejected = 0

    def _run_loop(
        self,
        total_candles: int,
        progress_bar: Optional[object],
        task: Optional[object],
    ) -> None:
        """Main time-synchronized portfolio trading loop.

        Iterates over candles. At each candle:
        1. For each token: update price, check SL/TP, process SAX boundary
        2. Collect entry signals from all tokens
        3. Prioritize and execute signals through PortfolioManager
        4. Record portfolio equity and drawdown
        5. Periodic rebalancing
        """
        cfg = self.config
        start_candle = cfg.start_offset

        for candle_idx in range(start_candle, start_candle + total_candles):
            self._candle_idx = candle_idx

            # ----------------------------------------------------------
            # PHASE 1: Per-token candle processing
            # ----------------------------------------------------------
            entry_signals: list[tuple[str, Signal]] = []

            for symbol, eng in self.engines.items():
                df = eng._df
                if candle_idx >= len(df):
                    continue

                candle = df.iloc[candle_idx]
                close = float(candle.get("close", candle.get("c", 0)))
                high = float(candle.get("high", candle.get("h", 0)))
                low = float(candle.get("low", candle.get("l", 0)))

                if close <= 0:
                    continue

                # Update correlation engine
                self.correlation.update_price(symbol, close)

                # Update regime (from RegimeDetector)
                if eng.regime_detector is not None:
                    regime_info = eng.regime_detector.detect(close)
                    if regime_info:
                        eng.current_regime = regime_info.name
                        eng.regime_confidence = regime_info.confidence
                        self.portfolio.update_regime(symbol, regime_info.name)

                        # Track regime transitions
                        prev = self._prev_regimes.get(symbol, "UNKNOWN")
                        if regime_info.name != prev:
                            self._regime_transitions.append({
                                "candle": candle_idx,
                                "symbol": symbol,
                                "from": prev,
                                "to": regime_info.name,
                            })
                            self._prev_regimes[symbol] = regime_info.name

                # Update portfolio candle tracking
                self.portfolio.process_candle(
                    symbol,
                    {"close": close, "high": high, "low": low},
                    regime=eng.current_regime,
                )

                # Mark positions to market
                slot = self.portfolio.get_slot(symbol)
                if slot and slot.risk_manager:
                    for pos in slot.risk_manager.open_positions:
                        pos.unrealized_pnl_pct = self._compute_unrealized_pnl(pos, close)

                # ------------------------------------------------------
                # PHASE 1a: SL/TP check for existing positions
                # ------------------------------------------------------
                if eng.is_in_position:
                    signal = self._check_sl_tp(eng, candle_idx, close, high, low)
                    if signal is not None and signal.is_exit:
                        self._execute_exit(eng, candle_idx, close, signal.exit_reason
                                           if hasattr(signal, 'exit_reason') else "sl_tp")

                # ------------------------------------------------------
                # PHASE 1b: SAX boundary processing
                # ------------------------------------------------------
                # Determine which SAX symbol this candle falls into
                sax_idx = candle_idx // eng.sax_window_size
                prev_sax_idx = (candle_idx - 1) // eng.sax_window_size

                if sax_idx != prev_sax_idx and sax_idx < len(eng.sax_symbols):
                    # We've crossed a SAX boundary — process pattern matching
                    signal = self._process_sax_boundary(eng, sax_idx, candle_idx, close)
                    if signal is not None and signal.is_entry:
                        entry_signals.append((symbol, signal))

            # ----------------------------------------------------------
            # PHASE 2: Signal prioritization & execution
            # ----------------------------------------------------------
            if entry_signals:
                self._prioritize_and_execute(entry_signals)

            # ----------------------------------------------------------
            # PHASE 3: Portfolio-level updates
            # ----------------------------------------------------------

            # Record equity curve
            current_value = self.portfolio.total_value
            self._equity_curve.append(current_value)

            # Update peak and drawdown
            if current_value > self._peak_value:
                self._peak_value = current_value
            dd = (self._peak_value - current_value) / self._peak_value if self._peak_value > 0 else 0
            self._drawdown_curve.append(dd)

            # Periodic rebalance
            if (cfg.rebalance_interval_candles > 0 and
                    candle_idx > start_candle and
                    (candle_idx - start_candle) % cfg.rebalance_interval_candles == 0):
                self._do_rebalance(candle_idx)

            # Regime-shift rebalance
            if cfg.regime_shift_rebalance and self._regime_transitions:
                latest = self._regime_transitions[-1]
                if latest["candle"] == candle_idx:
                    self._do_rebalance(candle_idx, reason="regime_shift")

            # Update progress
            if progress_bar and task is not None:
                progress_bar.update(task, advance=1)

    # -------------------------------------------------------------------
    # SAX Boundary Processing (per-token)
    # -------------------------------------------------------------------

    def _process_sax_boundary(
        self,
        eng: TokenEngine,
        sax_idx: int,
        candle_idx: int,
        current_price: float,
    ) -> Optional[Signal]:
        """Process a SAX boundary for one token.

        If the token is in a position, checks continuation/break.
        If not in a position, checks for entry signals.

        This mirrors PaperTrader's per-SAX-step logic but adapted
        for portfolio-level governance.
        """
        cfg = self.config
        eng.current_sax_idx = sax_idx

        # Get recent SAX symbols for pattern matching
        recent_symbols = eng.sax_symbols[max(0, sax_idx - cfg.pattern_length + 1):sax_idx + 1]
        if len(recent_symbols) < cfg.pattern_length:
            return None

        pattern = recent_symbols[-cfg.pattern_length:]

        if eng.is_in_position:
            # ---- POSITION MANAGEMENT: Check continuation / break ----
            return self._check_continuation(eng, pattern, sax_idx, candle_idx, current_price)
        else:
            # ---- ENTRY SIGNAL GENERATION ----
            return self._check_entry(eng, pattern, sax_idx, candle_idx, current_price)

    def _check_continuation(
        self,
        eng: TokenEngine,
        pattern: list[str],
        sax_idx: int,
        candle_idx: int,
        current_price: float,
    ) -> Optional[Signal]:
        """Check if the current position should continue, trail, or exit.

        Uses all 4 trie levels for continuation check, with
        graduated exit based on pattern_break_score.
        """
        cfg = self.config
        pos = eng.current_position
        entry_price = pos.entry_price

        if eng.ppmt_engine is not None and eng.has_multi_level:
            # 4-level matching for continuation
            result = eng.ppmt_engine.match(
                pattern, current_price,
                is_in_position=True,
                entry_price=entry_price,
            )
            if result is not None and hasattr(result, 'continuation_signal') and result.continuation_signal:
                signal = result.continuation_signal
            else:
                # Fall through to fuzzy matcher check
                signal = None
        else:
            signal = None

        # Fallback: Use FuzzyMatcher directly for continuation check
        if signal is None and eng.fuzzy_matcher is not None:
            trie = eng.primary_trie
            if trie is not None:
                cont_result = eng.fuzzy_matcher.check_continuation(
                    trie, pos.matched_pattern, pattern[-1] if pattern else None,
                )
                if cont_result.matched:
                    # Pattern continues
                    eng.consecutive_breaks = 0
                    # Update trailing stop if in profit
                    pnl_pct = (current_price - entry_price) / entry_price * 100 if pos.direction == "LONG" \
                        else (entry_price - current_price) / entry_price * 100
                    if pnl_pct >= 3.0 and not eng.trailing_activated:
                        eng.trailing_activated = True
                        if pos.direction == "LONG":
                            eng.trailing_sl = current_price * 0.985
                        else:
                            eng.trailing_sl = current_price * 1.015
                    return None  # No exit signal — position continues
                else:
                    # Pattern break
                    break_score = cont_result.pattern_break_score
                    eng.consecutive_breaks += 1

                    # Extended grace: +1 if break score is moderate
                    effective_grace = cfg.pattern_break_grace
                    if break_score >= 0.4 and eng.consecutive_breaks <= cfg.pattern_break_grace:
                        # Moderate break — give one more chance
                        effective_grace += 1

                    if eng.consecutive_breaks >= effective_grace:
                        # Pattern truly broken — EXIT
                        exit_reason = "pattern_break"
                        if eng.trailing_activated:
                            exit_reason = "trailing_stop"
                        self._execute_exit(eng, candle_idx, current_price, exit_reason)
                        return None

                    # Within grace period — check trailing
                    pnl_pct = (current_price - entry_price) / entry_price * 100 if pos.direction == "LONG" \
                        else (entry_price - current_price) / entry_price * 100
                    if pnl_pct >= 3.0 and not eng.trailing_activated:
                        eng.trailing_activated = True
                        if pos.direction == "LONG":
                            eng.trailing_sl = current_price * 0.985
                        else:
                            eng.trailing_sl = current_price * 1.015

                    return None

        # If signal was generated by 4-level match
        if signal is not None:
            if signal.signal_type == SignalType.EXIT:
                exit_reason = "pattern_break"
                if signal.unknown_block_exit:
                    exit_reason = "unknown_block"
                self._execute_exit(eng, candle_idx, current_price, exit_reason)
                return None
            elif signal.signal_type == SignalType.TRAILING:
                eng.trailing_activated = True
                eng.trailing_sl = signal.sl_price or current_price * 0.985
                return None
            elif signal.signal_type == SignalType.HOLD:
                eng.consecutive_breaks = 0
                # Update trailing
                pnl_pct = (current_price - entry_price) / entry_price * 100 if pos.direction == "LONG" \
                    else (entry_price - current_price) / entry_price * 100
                if pnl_pct >= 3.0 and not eng.trailing_activated:
                    eng.trailing_activated = True
                    if pos.direction == "LONG":
                        eng.trailing_sl = current_price * 0.985
                    else:
                        eng.trailing_sl = current_price * 1.015
                return None

        return None

    def _check_entry(
        self,
        eng: TokenEngine,
        pattern: list[str],
        sax_idx: int,
        candle_idx: int,
        current_price: float,
    ) -> Optional[Signal]:
        """Check for entry signal at a SAX boundary.

        Uses PredictionEngine for direction/confidence, then
        PPMT 4-level matching for weighted confidence.
        """
        cfg = self.config

        # Re-entry cooldown check
        if sax_idx - eng.last_losing_trade_sym_idx < cfg.reentry_cooldown:
            eng.cooldown_filter_count += 1
            return None

        # Get prediction from PredictionEngine
        if eng.pred_engine is None:
            return None

        prediction = eng.pred_engine.predict(
            eng.sax_symbols, sax_idx,
            current_price=current_price,
        )

        if prediction is None or prediction.direction == "FLAT":
            return None

        # Get weighted confidence from 4-level matching
        confidence = prediction.confidence
        if eng.ppmt_engine is not None and eng.has_multi_level:
            raw_result = eng.ppmt_engine.match_raw(pattern, current_price)
            if raw_result is not None:
                confidence = raw_result.weighted_confidence

        # Apply regime-aware confidence adjustment
        if cfg.regime_aware and eng.current_regime:
            regime_score = self._regime_match_score(eng.current_regime, prediction.direction)
            confidence *= regime_score

        # Minimum confidence check
        effective_min_conf = cfg.min_confidence

        # SHORT gating (same logic as PaperTrader)
        if prediction.direction == "SHORT":
            if eng.token_profile is not None and not eng.token_profile.short_allowed:
                return None

            short_regime_mult = {
                "trending_down": 0.85,
                "ranging": 1.1,
                "trending_up": 1.5,
                "volatile": 1.8,
            }.get(eng.current_regime, 1.2)
            effective_min_conf = max(effective_min_conf * short_regime_mult, 0.20)

            if eng.token_profile is not None:
                effective_min_conf *= eng.token_profile.short_confidence_multiplier
                effective_min_conf = max(effective_min_conf, 0.20)

        if confidence < effective_min_conf:
            return None

        # Compute expected move from prediction
        expected_move = abs(prediction.expected_move_pct) if hasattr(prediction, 'expected_move_pct') else 0.0
        if expected_move < 0.3:
            return None

        # Compute SL/TP based on expected move
        # SL = 1.5x expected move, TP = 2.5x expected move (R:R ≈ 1.67)
        sl_distance = expected_move * 1.5
        tp_distance = expected_move * 2.5

        if prediction.direction == "LONG":
            sl_price = current_price * (1 - sl_distance / 100)
            tp_price = current_price * (1 + tp_distance / 100)
        else:
            sl_price = current_price * (1 + sl_distance / 100)
            tp_price = current_price * (1 - tp_distance / 100)

        # Create signal
        signal = Signal(
            signal_type=SignalType.ENTRY_LONG if prediction.direction == "LONG" else SignalType.ENTRY_SHORT,
            confidence=confidence,
            symbol=eng.symbol,
            entry_price=current_price,
            sl_price=sl_price,
            tp_price=tp_price,
            expected_move_pct=expected_move,
            risk_reward_ratio=tp_distance / max(sl_distance, 0.01),
            matched_pattern=pattern,
        )
        signal.quality_score = signal.compute_quality_score()
        signal.sizing_multiplier = signal.compute_sizing_multiplier()

        eng.signals_generated += 1
        return signal

    def _check_sl_tp(
        self,
        eng: TokenEngine,
        candle_idx: int,
        close: float,
        high: float,
        low: float,
    ) -> Optional[Signal]:
        """Check stop loss / take profit for an existing position.

        Also handles catastrophic loss protection and trailing stops.
        """
        pos = eng.current_position
        if pos is None:
            return None

        # Catastrophic loss check (TokenProfile-adaptive)
        cat_pct = 8.0  # Default
        if eng.token_profile is not None:
            cat_pct = eng.token_profile.catastrophic_loss_pct * 100

        if pos.direction == "LONG":
            unrealized_pct = (close - pos.entry_price) / pos.entry_price * 100
            low_pct = (low - pos.entry_price) / pos.entry_price * 100
        else:
            unrealized_pct = (pos.entry_price - close) / pos.entry_price * 100
            low_pct = (pos.entry_price - low) / pos.entry_price * 100

        # Catastrophic loss (intra-window)
        if low_pct <= -cat_pct:
            return Signal(
                signal_type=SignalType.EXIT,
                confidence=1.0,
                symbol=eng.symbol,
                sl_price=close,
                unknown_block_exit=True,
            )

        # SL check
        if pos.direction == "LONG" and close <= pos.sl_price:
            return Signal(
                signal_type=SignalType.EXIT,
                confidence=1.0,
                symbol=eng.symbol,
                sl_price=pos.sl_price,
            )
        if pos.direction == "SHORT" and close >= pos.sl_price:
            return Signal(
                signal_type=SignalType.EXIT,
                confidence=1.0,
                symbol=eng.symbol,
                sl_price=pos.sl_price,
            )

        # Trailing stop check
        if eng.trailing_activated and eng.trailing_sl > 0:
            if pos.direction == "LONG" and close <= eng.trailing_sl:
                return Signal(
                    signal_type=SignalType.TRAILING,
                    confidence=0.9,
                    symbol=eng.symbol,
                    sl_price=eng.trailing_sl,
                )
            if pos.direction == "SHORT" and close >= eng.trailing_sl:
                return Signal(
                    signal_type=SignalType.TRAILING,
                    confidence=0.9,
                    symbol=eng.symbol,
                    sl_price=eng.trailing_sl,
                )

            # Update trailing stop (move up for LONG, down for SHORT)
            if pos.direction == "LONG" and high > eng.trailing_sl * 1.015:
                eng.trailing_sl = max(eng.trailing_sl, high * 0.985)
            elif pos.direction == "SHORT" and low < eng.trailing_sl * 0.985:
                eng.trailing_sl = min(eng.trailing_sl, low * 1.015)

        # TP check
        if pos.tp_price is not None:
            if pos.direction == "LONG" and close >= pos.tp_price:
                return Signal(
                    signal_type=SignalType.EXIT,
                    confidence=1.0,
                    symbol=eng.symbol,
                    tp_price=pos.tp_price,
                )
            if pos.direction == "SHORT" and close <= pos.tp_price:
                return Signal(
                    signal_type=SignalType.EXIT,
                    confidence=1.0,
                    symbol=eng.symbol,
                    tp_price=pos.tp_price,
                )

        return None

    # -------------------------------------------------------------------
    # Signal Prioritization & Execution
    # -------------------------------------------------------------------

    def _prioritize_and_execute(self, signals: list[tuple[str, Signal]]) -> None:
        """Prioritize and execute entry signals through PortfolioManager.

        When multiple tokens signal simultaneously:
        1. Rank signals by priority method
        2. Execute top signals through PortfolioManager.can_open_position()
        3. Track approved/rejected counts
        """
        cfg = self.config

        # Rank signals by priority method
        if cfg.signal_priority_method == "QUALITY_WEIGHTED":
            ranked = sorted(
                signals,
                key=lambda x: x[1].quality_score * x[1].confidence * x[1].sizing_multiplier,
                reverse=True,
            )
        elif cfg.signal_priority_method == "CONFIDENCE_FIRST":
            ranked = sorted(
                signals,
                key=lambda x: (x[1].confidence, x[1].quality_score),
                reverse=True,
            )
        elif cfg.signal_priority_method == "EXPECTED_VALUE":
            ranked = sorted(
                signals,
                key=lambda x: x[1].expected_profit_ahead,
                reverse=True,
            )
        else:
            ranked = signals

        # Execute signals in priority order
        for symbol, signal in ranked:
            eng = self.engines.get(symbol)
            if eng is None or eng.is_in_position:
                continue

            # Calculate position size from the slot's RiskManager
            slot = self.portfolio.get_slot(symbol)
            if slot is None or not slot.is_active or slot.risk_manager is None:
                eng.signals_rejected += 1
                continue

            size = slot.risk_manager.calculate_position_size(signal)
            if size <= 0:
                eng.signals_rejected += 1
                continue

            # Portfolio-level approval
            allowed, reason = self.portfolio.can_open_position(
                signal, size, signal.entry_price or 0,
            )

            if not allowed:
                eng.signals_rejected += 1
                self._signal_log.append({
                    "candle": self._candle_idx,
                    "symbol": symbol,
                    "action": "REJECTED",
                    "reason": reason,
                    "confidence": round(signal.confidence, 3),
                    "quality": round(signal.quality_score, 3),
                })
                continue

            # Execute the position
            position = self.portfolio.open_position(signal, size)
            if position is not None:
                eng.signals_approved += 1
                eng.current_position = PaperTrade(
                    trade_id=eng.trade_counter,
                    symbol=symbol,
                    direction=signal.direction or "LONG",
                    entry_price=signal.entry_price or 0,
                    size=size,
                    confidence=signal.confidence,
                    quality_score=signal.quality_score,
                    sizing_multiplier=signal.sizing_multiplier,
                    sl_price=signal.sl_price or 0,
                    tp_price=signal.tp_price or 0,
                    win_rate=signal.win_rate,
                    risk_reward_ratio=signal.risk_reward_ratio,
                    expected_move_pct=signal.expected_move_pct,
                    matched_pattern=signal.matched_pattern,
                    regime=eng.current_regime,
                    regime_confidence=eng.regime_confidence,
                    entry_sym_idx=eng.current_sax_idx,
                )
                eng.trade_counter += 1
                eng.trailing_activated = False
                eng.trailing_sl = 0.0
                eng.consecutive_breaks = 0

                self._signal_log.append({
                    "candle": self._candle_idx,
                    "symbol": symbol,
                    "action": "ENTER",
                    "direction": signal.direction,
                    "confidence": round(signal.confidence, 3),
                    "quality": round(signal.quality_score, 3),
                    "size": round(size, 2),
                    "sl": round(signal.sl_price or 0, 2),
                    "tp": round(signal.tp_price or 0, 2),
                })

    def _execute_exit(
        self,
        eng: TokenEngine,
        candle_idx: int,
        exit_price: float,
        reason: str,
    ) -> None:
        """Execute a position exit and update Living Trie."""
        pos = eng.current_position
        if pos is None:
            return

        # Compute PnL
        if pos.direction == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        pnl_usd = pos.size * pnl_pct / 100

        pos.exit_price = exit_price
        pos.pnl_pct = pnl_pct
        pos.pnl = pnl_usd
        pos.actual_move_pct = pnl_pct
        pos.exit_reason = reason

        # Close position in PortfolioManager
        self.portfolio.close_position(eng.symbol, exit_price)

        # Update Living Trie
        if self.config.living_trie and eng.primary_trie is not None:
            next_symbol = eng.sax_symbols[eng.current_sax_idx + 1] \
                if eng.current_sax_idx + 1 < len(eng.sax_symbols) else None
            _record_observation(
                trie=eng.primary_trie,
                trade=pos,
                exit_sym_idx=eng.current_sax_idx,
                next_symbol=next_symbol,
                fuzzy_matcher=eng.fuzzy_matcher,
            )

        # Update cooldown for losing trades
        if pnl_pct < 0:
            eng.last_losing_trade_sym_idx = eng.current_sax_idx

        # Reset position state
        eng.current_position = None
        eng.trailing_activated = False
        eng.trailing_sl = 0.0
        eng.consecutive_breaks = 0

        self._signal_log.append({
            "candle": candle_idx,
            "symbol": eng.symbol,
            "action": "EXIT",
            "reason": reason,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
        })

    # -------------------------------------------------------------------
    # Portfolio-Level Operations
    # -------------------------------------------------------------------

    def _do_rebalance(self, candle_idx: int, reason: str = "periodic") -> None:
        """Execute a portfolio rebalance."""
        dominant_regime = self.portfolio._get_dominant_regime()
        corr_result = self.correlation.compute_matrix()
        corr_regime = corr_result.regime.value

        current_alloc = {
            sym: slot.capital_allocated
            for sym, slot in self.portfolio._slots.items()
        }
        perf_data = {
            sym: {"win_rate": slot.win_rate, "pnl_pct": slot.pnl_pct, "trades": slot.trades_completed}
            for sym, slot in self.portfolio._slots.items()
        }
        quality_data = {
            sym: 0.5 + slot.win_rate * 0.3
            for sym, slot in self.portfolio._slots.items()
        }

        alloc_result = self.allocator.allocate(
            regime=dominant_regime,
            tokens=list(self.portfolio._slots.keys()),
            total_capital=self.portfolio.total_value,
            current_allocations=current_alloc,
            token_performance=perf_data,
            pattern_quality=quality_data,
            correlation_regime=corr_regime,
            portfolio_drawdown_pct=self.portfolio.current_drawdown_pct,
        )

        for instr in alloc_result.instructions:
            slot = self.portfolio._slots.get(instr.symbol)
            if slot:
                slot.capital_allocated = instr.target_capital
                if slot.risk_manager:
                    slot.risk_manager.capital = instr.target_capital - slot.capital_used
                    slot.risk_manager.initial_capital = instr.target_capital

        self._rebalance_count += 1

    # -------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------

    @staticmethod
    def _compute_unrealized_pnl(pos: Position, current_price: float) -> float:
        """Compute unrealized PnL percentage for a position."""
        if pos.entry_price <= 0:
            return 0.0
        if pos.direction == "LONG":
            return (current_price - pos.entry_price) / pos.entry_price * 100
        else:
            return (pos.entry_price - current_price) / pos.entry_price * 100

    @staticmethod
    def _regime_match_score(regime: str, direction: str) -> float:
        """Score how well the signal direction matches the regime.

        High score = direction aligned with regime (more confident).
        Low score = direction fighting the regime (less confident).
        """
        if direction == "LONG":
            return {
                "trending_up": 1.2,
                "ranging": 1.0,
                "trending_down": 0.7,
                "volatile": 0.5,
            }.get(regime, 0.9)
        elif direction == "SHORT":
            return {
                "trending_down": 1.2,
                "ranging": 1.0,
                "trending_up": 0.7,
                "volatile": 0.5,
            }.get(regime, 0.9)
        return 1.0

    # -------------------------------------------------------------------
    # Result Building
    # -------------------------------------------------------------------

    def _build_result(self, total_candles: int) -> PortfolioRunnerResult:
        """Build the final PortfolioRunnerResult from session state."""
        cfg = self.config

        # Per-token results
        token_results = {}
        for sym, eng in self.engines.items():
            slot = self.portfolio.get_slot(sym)
            if slot is None:
                continue

            token_results[sym] = {
                "asset_class": eng.asset_class,
                "alpha": eng.sax_alphabet_size,
                "window": eng.sax_window_size,
                "trades": slot.trades_completed,
                "wins": slot.wins,
                "losses": slot.losses,
                "win_rate": round(slot.win_rate, 3),
                "pnl": round(slot.total_pnl, 2),
                "pnl_pct": round(slot.pnl_pct * 100, 2),
                "max_dd": round(slot.current_drawdown_pct * 100, 2),
                "signals_generated": eng.signals_generated,
                "signals_approved": eng.signals_approved,
                "signals_rejected": eng.signals_rejected,
                "rejection_rate": round(
                    eng.signals_rejected / max(1, eng.signals_generated) * 100, 1
                ),
            }

        # Portfolio-level metrics
        final_value = self.portfolio.total_value
        total_return_pct = (final_value - cfg.initial_capital) / cfg.initial_capital

        portfolio_sharpe = 0.0
        portfolio_sortino = 0.0
        if len(self._equity_curve) > 10:
            returns = np.diff(self._equity_curve) / self._equity_curve[:-1]
            returns = returns[np.isfinite(returns)]
            if len(returns) > 10:
                mean_ret = np.mean(returns)
                std_ret = np.std(returns)
                downside = np.std(returns[returns < 0]) if np.any(returns < 0) else std_ret
                if std_ret > 0:
                    portfolio_sharpe = mean_ret / std_ret * np.sqrt(252 * 24)
                if downside > 0:
                    portfolio_sortino = mean_ret / downside * np.sqrt(252 * 24)

        max_dd = max(self._drawdown_curve) if self._drawdown_curve else 0.0
        calmar = total_return_pct / max_dd if max_dd > 0 else 0.0

        total_trades = sum(r["trades"] for r in token_results.values())
        total_wins = sum(r["wins"] for r in token_results.values())
        total_losses = sum(r["losses"] for r in token_results.values())
        total_signals = sum(eng.signals_generated for eng in self.engines.values())
        signals_approved = sum(eng.signals_approved for eng in self.engines.values())
        signals_rejected = sum(eng.signals_rejected for eng in self.engines.values())

        return PortfolioRunnerResult(
            tokens=token_results,
            total_capital=cfg.initial_capital,
            final_value=round(final_value, 2),
            total_return_pct=total_return_pct,
            max_drawdown_pct=max_dd,
            sharpe_ratio=portfolio_sharpe,
            sortino_ratio=portfolio_sortino,
            calmar_ratio=calmar,
            total_trades=total_trades,
            total_wins=total_wins,
            total_losses=total_losses,
            total_signals=total_signals,
            signals_approved=signals_approved,
            signals_rejected=signals_rejected,
            rejection_rate=signals_rejected / max(1, total_signals),
            equity_curve=self._equity_curve,
            drawdown_curve=self._drawdown_curve,
            rebalance_count=self._rebalance_count,
            regime_transitions=self._regime_transitions,
            duration_candles=total_candles,
            signal_log=self._signal_log[-50:],  # Keep last 50 events
        )

    # -------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------

    def display_result(self, result: PortfolioRunnerResult) -> None:
        """Display a rich portfolio runner result."""
        pnl_color = "green" if result.total_return_pct >= 0 else "red"

        # Portfolio overview
        console.print(Panel(
            f"[bold]Capital:[/bold] ${result.total_capital:,.0f} -> ${result.final_value:,.0f}  "
            f"[{pnl_color}]Return: {result.total_return_pct * 100:+.1f}%[/{pnl_color}]  "
            f"[bold]Max DD:[/bold] {result.max_drawdown_pct * 100:.1f}%  "
            f"[bold]Sharpe:[/bold] {result.sharpe_ratio:.2f}  "
            f"[bold]Calmar:[/bold] {result.calmar_ratio:.2f}",
            title="[bold cyan]Portfolio Runner Result[/bold cyan]",
            border_style="cyan",
        ))

        # Per-token results table
        table = Table(title="Per-Token Performance", show_lines=True)
        table.add_column("Token", style="bold")
        table.add_column("Class", width=8)
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("PnL %", justify="right")
        table.add_column("Max DD", justify="right")
        table.add_column("Signals", justify="right")
        table.add_column("Approved", justify="right")
        table.add_column("Rejected", justify="right")

        for sym, r in result.tokens.items():
            pnl_str = f"${r['pnl']:,.2f}"
            if r["pnl"] < 0:
                pnl_str = f"[red]{pnl_str}[/red]"
            elif r["pnl"] > 0:
                pnl_str = f"[green]{pnl_str}[/green]"

            table.add_row(
                sym,
                r["asset_class"],
                str(r["trades"]),
                f"{r['win_rate']:.0%}",
                pnl_str,
                f"{r['pnl_pct']:+.1f}%",
                f"{r['max_dd']:.1f}%",
                str(r["signals_generated"]),
                str(r["signals_approved"]),
                str(r["signals_rejected"]),
            )

        console.print(table)

        # Signal stats
        console.print(
            f"\n  Total Signals: {result.total_signals}  "
            f"Approved: {result.signals_approved}  "
            f"Rejected: {result.signals_rejected}  "
            f"Rejection Rate: {result.rejection_rate * 100:.1f}%  "
            f"Rebalances: {result.rebalance_count}  "
            f"Regime Changes: {len(result.regime_transitions)}"
        )

    def save_result(self, result: PortfolioRunnerResult, filepath: str) -> None:
        """Save runner result to JSON file."""
        import json
        output = result.to_dict()
        # Add equity curve (sampled)
        if result.equity_curve:
            step = max(1, len(result.equity_curve) // 500)
            output["equity_curve_sampled"] = [
                {"idx": i, "value": round(v, 2)}
                for i, v in enumerate(result.equity_curve[::step])
            ]
        with open(filepath, "w") as f:
            json.dump(output, f, indent=2, default=str)
