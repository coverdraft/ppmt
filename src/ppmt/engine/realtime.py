"""
Real-Time Trading Engine - Live & Replay Mode

Streams candles through the PPMT pipeline one at a time, enabling:

  1. **Replay Mode**: Step through historical data as if it were live
     (for testing the real-time pipeline without an exchange connection)
  2. **Live Mode**: Connect to an exchange via ccxt and process candles
     in real-time with the full PPMT pipeline

The real-time engine differs from PaperTrader in a fundamental way:
  - PaperTrader: Batch processing — encodes ALL data first, then iterates
  - RealtimeTrader: Streaming — encodes each candle incrementally,
    producing SAX symbols on-the-fly as windows complete

This is essential for production because:
  - In live trading, future data doesn't exist yet
  - Incremental SAX encoding ensures no look-ahead bias
  - The streaming pattern buffer maintains the current pattern context

Usage (Replay):
    from ppmt.engine.realtime import RealtimeTrader, ReplayConfig

    config = ReplayConfig(symbol="BTC/USDT", speed=1.0)
    trader = RealtimeTrader(config=config)
    result = trader.run_replay()

Usage (Live - requires ccxt):
    from ppmt.engine.realtime import RealtimeTrader, LiveConfig

    config = LiveConfig(symbol="BTC/USDT", exchange="binance")
    trader = RealtimeTrader(config=config)
    await trader.run_live()
"""

from __future__ import annotations

import time
import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

import numpy as np
import pandas as pd

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.sax import SAXEncoder
from ppmt.core.regime import RegimeDetector, RegimeInfo
from ppmt.core.profiles import TokenProfile, TIMEFRAME_ALPHA_DEFAULTS, TradingCalibrationEngine
from ppmt.core.matcher import FuzzyMatcher
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.signal import SignalType, Signal
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.risk.manager import RiskManager, RiskConfig
from ppmt.risk.money_manager import MoneyManager, MoneyManagerConfig
from ppmt.risk.position_sizing import AdvancedPositionSizer
from ppmt.engine.buffer import StreamingPatternBuffer

# v0.15.0: TerminalState integration for live dashboard
try:
    from ppmt.terminal.state import get_terminal_state
    _terminal_state = get_terminal_state()
except ImportError:
    _terminal_state = None


console = Console()


# ============================================================
# v0.34.0: TF-aware recalibration interval
# ============================================================
# 15m es la referencia (2000 velas ≈ 21 días).
# TFs más altos → menos recalibraciones (más espaciadas).
# TFs más bajos → usar el base (no tiene sentido recalibrar cada 200 velas).
# Techo de 50k para TFs muy altos (4h, 1d) — mantiene Living Trie vivo
# sin forzar recalibraciones absurdas (526 años para 1d).
_RECALIBRATION_CEILING = 50_000
_RECALIBRATION_BASE = 2_000
_RECALIBRATION_REF_TF_MIN = 15

_TF_TO_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080, "1M": 43200,
}


def _tf_to_minutes(tf: str) -> int:
    """Convierte '15m' -> 15, '1h' -> 60, '1d' -> 1440. Default 15."""
    return _TF_TO_MINUTES.get(tf, 15)


def get_recalibration_interval(tf_minutes: int) -> int:
    """Calcula el intervalo de recalibración óptimo según el TF.

    Tabla:
      1m  → 2,000  (33h)
      5m  → 2,000  (7d)
      15m → 2,000  (21d)  ← referencia
      1h  → 8,000  (333d)
      4h  → 32,000 (1333d)
      1d  → 50,000 (526d, capped)
    """
    if tf_minutes <= 0:
        return _RECALIBRATION_BASE
    factor = max(1.0, tf_minutes / _RECALIBRATION_REF_TF_MIN)
    interval = int(_RECALIBRATION_BASE * factor)
    return min(interval, _RECALIBRATION_CEILING)


class TraderMode(Enum):
    """Trading mode."""
    REPLAY = "replay"   # Replay historical data
    LIVE = "live"       # Connect to exchange
    PAPER = "paper"     # Paper trading (no real execution)


class PositionState(Enum):
    """Current position state."""
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class ReplayConfig:
    """Configuration for replay mode."""
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    initial_capital: float = 10_000.0
    speed: float = 1.0
    """Playback speed multiplier. 1.0 = real-time, 10.0 = 10x speed,
    0.0 = maximum speed (no delays)."""
    pattern_length: int = 5
    sax_alphabet_size: int = 0
    """SAX alphabet size. 0 = auto from TokenProfile (timeframe-adaptive)."""
    sax_window_size: int = 0
    """SAX window size. 0 = auto from TokenProfile (timeframe-adaptive)."""
    sax_strategy: str = "ohlcv"
    min_confidence: float = 0.08
    """Minimum signal confidence. v0.21.0: Lowered from 0.20 to 0.08 because
    Bayesian shrinkage with low historical_count produces confidences in
    the 0.08-0.22 range. Use --min-confidence to override."""
    start_offset: int = 200
    """Number of initial candles to skip (warm-up)."""
    catastrophic_loss_pct: float = 0.0
    """Catastrophic loss threshold. 0.0 = use TokenProfile value."""
    regime_aware: bool = True
    living_trie: bool = True
    """Whether to update the Trie with observations during replay."""
    use_token_profile: bool = True
    """v0.11.0: Use TokenProfile for automatic parameter selection."""
    auto_calibrate: bool = True
    """v0.11.0: Auto-calibrate SAX α/W using TradingCalibrationEngine."""
    recalibration_interval: int = 0
    """v0.11.0: Re-calibrate every N candles. 0 = no recalibration."""
    use_multi_level: bool = True
    """v0.11.0: Enable 4-level matching (N1+N2+N3+N4)."""
    verbose: bool = True
    on_signal: Optional[Callable] = None
    """Callback fired when a signal is generated. Receives (signal, prediction)."""
    on_trade: Optional[Callable] = None
    """Callback fired when a trade is closed. Receives the trade dict."""
    on_candle: Optional[Callable] = None
    """Callback fired for each processed candle. Receives (candle_idx, price)."""
    # v0.32.3: Validation mode — relaxes strict v0.25.0 signal filters so the
    # backtest can produce enough trades to reach the 5-trade MC threshold.
    # Live trading keeps strict filters (safety first); validation needs a
    # fairer assessment of whether the system has ANY edge on this token.
    # When True:
    #   - base probability threshold: 0.30 (was 0.35)
    #   - ranging regime threshold:   0.40 (was 0.55) — Bayesian shrinkage keeps
    #     probabilities near 0.5, so 0.55 rejects almost everything in ranging
    #   - volatile regime threshold:  0.45 (was 0.60)
    #   - counter-trend threshold:    0.45 (was 0.60)
    validation_mode: bool = False
    """v0.32.3: When True, relaxes strict signal filters for fair validation."""


@dataclass
class LiveConfig:
    """Configuration for live mode (requires ccxt).

    v0.20.0: Added leverage, auto_mode, money management fields.
    """
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    initial_capital: float = 10_000.0
    exchange: str = "binance"
    api_key: str = ""
    api_secret: str = ""
    pattern_length: int = 5
    sax_alphabet_size: int = 0
    """SAX alphabet size. 0 = auto from TokenProfile."""
    sax_window_size: int = 0
    """SAX window size. 0 = auto from TokenProfile."""
    sax_strategy: str = "ohlcv"
    min_confidence: float = 0.08
    """Minimum signal confidence. v0.21.0: Lowered from 0.20 to 0.08 because
    Bayesian shrinkage with low historical_count produces confidences in
    the 0.08-0.22 range. Use --min-confidence to override."""
    catastrophic_loss_pct: float = 0.0
    """Catastrophic loss threshold. 0.0 = use TokenProfile value."""
    regime_aware: bool = True
    use_token_profile: bool = True
    """v0.11.0: Use TokenProfile for automatic parameter selection."""
    auto_calibrate: bool = True
    """v0.11.0: Auto-calibrate SAX α/W using TradingCalibrationEngine."""
    recalibration_interval: int = 0
    """v0.11.0/v0.36.1: Re-calibrate every N candles in live mode. 0 = auto
    (TF-aware: 2000 for ≤15m, scaled up for higher TFs, capped at 50k).
    See :func:`get_recalibration_interval`."""
    use_multi_level: bool = True
    """v0.11.0: Enable 4-level matching."""
    living_trie: bool = True
    """v0.11.0: Enable Living Trie updates in live mode."""
    testnet: bool = False
    """Use exchange testnet (for order execution only, NOT for market data)."""
    dry_run: bool = True
    """If True, process signals but don't actually execute orders."""

    # v0.20.0: Money Management & Portfolio Controls
    leverage: int = 1
    """Exchange leverage multiplier (1=spot, 2-125=futures). Applied via ccxt."""
    auto_mode: bool = True
    """If True, signals execute automatically. If False, signals are displayed only (manual confirmation)."""
    max_open_positions: int = 5
    """Max simultaneous open positions."""
    max_portfolio_exposure_pct: float = 0.80
    """Max total portfolio exposure (0.80 = 80% of capital at risk)."""
    max_single_position_pct: float = 0.25
    """Max single position as fraction of portfolio (0.25 = 25%)."""
    max_correlated_positions: int = 2
    """Max positions in same asset class."""
    kill_switch_pct: float = 0.95
    """If exposure exceeds this fraction, auto-close all positions."""
    daily_loss_limit_pct: float = 0.05
    """Max daily loss as fraction of capital. 0.05 = 5%."""
    max_drawdown_pct: float = 0.15
    """Max drawdown from peak before circuit breaker. 0.15 = 15%."""
    use_kelly_sizing: bool = True
    """Use Kelly Criterion + AdvancedPositionSizer instead of basic RiskManager sizing."""
    kelly_fraction: float = 0.25
    """Fraction of Kelly to use (0.25 = Quarter-Kelly, conservative)."""
    trie_persist_interval: int = 100
    """Save Living Trie to DB every N candles. 0 = only on shutdown."""""


@dataclass
class RealtimeTrade:
    """Record of a single real-time trade."""
    trade_id: int = 0
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    entry_time: str = ""
    exit_time: str = ""
    size: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    confidence: float = 0.0
    exit_reason: str = ""
    regime: str = ""
    matched_pattern: list[str] = field(default_factory=list)


@dataclass
class RealtimeResult:
    """Result of a real-time trading session."""
    mode: str = ""
    symbol: str = ""
    timeframe: str = ""
    initial_capital: float = 10_000.0
    final_capital: float = 10_000.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown: float = 0.0
    trades: list[RealtimeTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    capital_history: list[float] = field(default_factory=list)
    candles_processed: int = 0
    sax_symbols_produced: int = 0
    signals_generated: int = 0
    duration_seconds: float = 0.0


class RealtimeTrader:
    """
    Real-Time Trading Engine with incremental SAX encoding.

    Unlike PaperTrader which batch-processes all data, RealtimeTrader
    processes candles one at a time through the incremental SAX pipeline,
    maintaining a streaming pattern buffer and generating signals only
    when new SAX symbols are produced (every window_size candles).

    This mirrors production behavior where future data is unavailable.

    v0.11.0: Now fully integrated with TokenProfile, TradingCalibrationEngine,
    FuzzyMatcher, 4-level matching, Living Trie, and prediction-aware SL/TP.
    """

    def __init__(self, config=None):
        self.config = config or ReplayConfig()
        self.risk_config = RiskConfig(
            base_position_size_pct=0.01,
            max_position_size_pct=0.04,
            min_position_size_pct=0.005,
            min_risk_reward=1.0,
            min_quality_score=0.10,
            max_daily_loss_pct=0.10,
            max_drawdown_pct=0.80,
        )

    def _update_terminal_state(self, **kwargs) -> None:
        """Push state update to TerminalState for dashboard consumption.

        Safe to call from synchronous code — uses update_sync().
        No-op if terminal module is not available.
        """
        if _terminal_state is not None:
            try:
                _terminal_state.update_sync(**kwargs)
            except Exception:
                pass  # Never let dashboard updates crash the engine

    def _setup_token_profile(self, cfg, info, storage, df=None):
        """
        v0.11.0: Initialize TokenProfile with auto-calibration and persistence.

        Shared between run_replay() and run_live(). Returns:
            (token_profile, cfg_with_updated_params)
        """
        token_profile = None

        if getattr(cfg, 'use_token_profile', False):
            # Try to load previously saved profile
            saved_profile_dict = storage.load_token_profile(cfg.symbol, cfg.timeframe)
            if saved_profile_dict is not None:
                try:
                    token_profile = TokenProfile.from_dict(saved_profile_dict)
                    console.print(f"[bold green]TokenProfile restored from storage:[/bold green] "
                                  f"alpha={token_profile.sax_alphabet_size}, "
                                  f"window={token_profile.sax_window_size}, "
                                  f"calibrated={token_profile.calibration_date}")
                except Exception:
                    token_profile = None

            if token_profile is None:
                token_profile = TokenProfile.from_timeframe(
                    symbol=cfg.symbol,
                    asset_class=info.asset_class,
                    timeframe=cfg.timeframe,
                )

            # Override SAX params from profile (unless explicitly set)
            if cfg.sax_alphabet_size == 0:
                cfg.sax_alphabet_size = token_profile.sax_alphabet_size
            if cfg.sax_window_size == 0:
                cfg.sax_window_size = token_profile.sax_window_size
            if cfg.catastrophic_loss_pct == 0.0:
                cfg.catastrophic_loss_pct = token_profile.catastrophic_loss_pct * 100.0

            console.print(f"[bold green]TokenProfile loaded:[/bold green] "
                          f"alpha={token_profile.sax_alphabet_size}, "
                          f"window={token_profile.sax_window_size}, "
                          f"cat_loss={token_profile.catastrophic_loss_pct:.0%}, "
                          f"short_allowed={token_profile.short_allowed}")

            # Auto-calibrate if not already calibrated
            profile_already_calibrated = (
                token_profile.calibration_date != ""
                and token_profile.calibration_metric > 0
            )
            if profile_already_calibrated:
                console.print(f"[green]Profile already calibrated on "
                              f"{token_profile.calibration_date} — "
                              f"skipping calibration[/green]")
            elif getattr(cfg, 'auto_calibrate', False) and df is not None and len(df) >= 1000:
                console.print(f"[bold cyan]Auto-calibrating α/W...[/bold cyan] "
                              f"({len(df)} candles)")
                try:
                    calibrator = TradingCalibrationEngine(
                        train_ratio=0.70,
                        pattern_length=cfg.pattern_length,
                        timeframe=cfg.timeframe,
                    )
                    cal_profile, cal_results = calibrator.calibrate(
                        df, symbol=cfg.symbol, verbose=False
                    )
                    cal_alpha = cal_profile.sax_alphabet_size
                    cal_window = cal_profile.sax_window_size

                    cal_best = [r for r in cal_results
                                if r.alphabet_size == cal_alpha
                                and r.window_size == cal_window]
                    cal_best_result = cal_best[0] if cal_best else None

                    if cal_alpha != cfg.sax_alphabet_size or cal_window != cfg.sax_window_size:
                        old_alpha, old_window = cfg.sax_alphabet_size, cfg.sax_window_size
                        cfg.sax_alphabet_size = cal_alpha
                        cfg.sax_window_size = cal_window

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
                            metric=cal_best_result.trading_metric if cal_best_result else 0.0,
                            grid=grid,
                            n_samples=len(df),
                        )
                        console.print(f"[bold green]Calibrated:[/bold green] "
                                      f"α={old_alpha}→{cal_alpha}, W={old_window}→{cal_window} "
                                      f"(PnL={cal_best_result.total_pnl_pct:+.1f}%, "
                                      f"WR={cal_best_result.win_rate:.1%})")
                    else:
                        console.print(f"[green]Calibration confirms: "
                                      f"α={cal_alpha}/W={cal_window}[/green]")
                except Exception as e:
                    console.print(f"[yellow]Calibration failed: {e} — "
                                  f"using defaults[/yellow]")

            # Save calibrated profile
            if token_profile is not None and token_profile.calibration_date:
                try:
                    storage.save_token_profile(cfg.symbol, cfg.timeframe, token_profile.to_dict())
                except Exception:
                    pass
        else:
            # Fallback: use timeframe defaults
            if cfg.sax_alphabet_size == 0 or cfg.sax_window_size == 0:
                tf_defaults = TIMEFRAME_ALPHA_DEFAULTS.get(
                    cfg.timeframe, TIMEFRAME_ALPHA_DEFAULTS["1h"]
                )
                if cfg.sax_alphabet_size == 0:
                    cfg.sax_alphabet_size = tf_defaults["sax_alphabet_size"]
                if cfg.sax_window_size == 0:
                    cfg.sax_window_size = tf_defaults["sax_window_size"]
            if cfg.catastrophic_loss_pct == 0.0:
                cfg.catastrophic_loss_pct = 8.0

        return token_profile, cfg

    def _compute_atr_pct(self, prices: np.ndarray, highs: np.ndarray,
                         lows: np.ndarray, period: int = 14) -> float:
        """Compute current ATR as percentage of price.

        v0.20.0: Fixed to use Wilder's smoothing (same as paper_trader.py).
        Previously used simple moving average which gave inconsistent ATR values
        compared to the backtest engine.
        """
        if len(prices) < period + 1:
            return 2.0  # Default

        close = prices
        high = highs
        low = lows

        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - prev_close),
                np.abs(low - prev_close)
            )
        )

        # v0.20.0: Wilder's smoothing (exponential moving average)
        # This matches paper_trader.py compute_atr_pct()
        if len(tr) >= period:
            atr_val = np.mean(tr[:period])
            for i in range(period, len(tr)):
                atr_val = (atr_val * (period - 1) + tr[i]) / period
        else:
            atr_val = np.mean(tr)

        last_price = prices[-1] if prices[-1] > 0 else 1.0
        return (atr_val / last_price * 100) if last_price > 0 else 2.0

    def run_replay(self) -> RealtimeResult:
        """
        Run replay mode: step through historical data as if it were live.

        Each candle is processed through the incremental SAX encoder.
        When a new SAX symbol is produced (every window_size candles),
        the pattern buffer is updated and a prediction is generated.

        v0.11.0: Now uses TokenProfile, auto-calibration, FuzzyMatcher,
        4-level matching, Living Trie, and prediction-aware SL/TP.

        Returns:
            RealtimeResult with trading statistics
        """
        if not isinstance(self.config, ReplayConfig):
            raise ValueError("run_replay() requires ReplayConfig")

        cfg = self.config
        start_time = time.time()
        storage = PPMTStorage()

        # Load data
        df = storage.load_ohlcv(cfg.symbol, cfg.timeframe)
        if df.empty:
            console.print(f"[red]No data for {cfg.symbol}. Run 'ppmt ingest' first.[/red]")
            return RealtimeResult(mode="replay", symbol=cfg.symbol, timeframe=cfg.timeframe)

        # Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(cfg.symbol)

        # v0.11.0: TokenProfile + auto-calibration + persistence
        token_profile, cfg = self._setup_token_profile(cfg, info, storage, df)

        # Load or build tries (v0.11.0: all 4 levels)
        from ppmt.engine.ppmt import PPMT
        all_tries = storage.load_all_tries(cfg.symbol)
        trie_n1 = all_tries["n1"]
        trie_n2 = all_tries["n2"]
        trie_n3 = all_tries["n3"]
        trie_n4 = all_tries["n4"]

        has_multi_level = (
            getattr(cfg, 'use_multi_level', False)
            and trie_n1 is not None
            and trie_n2 is not None
            and trie_n4 is not None
        )

        if trie_n3 is None:
            console.print(f"[yellow]No Trie for {cfg.symbol}. Building from data...[/yellow]")
            engine = PPMT(
                symbol=cfg.symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=cfg.sax_alphabet_size,
                sax_window_size=cfg.sax_window_size,
                sax_strategy=cfg.sax_strategy,
                fuzzy_threshold=token_profile.fuzzy_threshold if token_profile else 0.80,
                weight_profile=info.weight_profile,
            )
            engine.build(df, pattern_length=cfg.pattern_length)
            trie_n1 = engine.trie_n1
            trie_n2 = engine.trie_n2
            trie_n3 = engine.trie_n3
            trie_n4 = engine.trie_n4
            has_multi_level = True
        else:
            console.print(f"[green]Loaded N3 Trie for {cfg.symbol} ({trie_n3.pattern_count} patterns)[/green]")
            if has_multi_level:
                console.print(f"[green]All 4 levels loaded[/green]")

        trie = trie_n3
        trie.propagate_metadata()
        if has_multi_level:
            for t in [trie_n1, trie_n2, trie_n4]:
                t.propagate_metadata()

        # v0.11.0: FuzzyMatcher
        fuzzy_threshold = token_profile.fuzzy_threshold if token_profile else 0.80

        # Create SAX encoder
        sax_encoder = SAXEncoder(
            alphabet_size=cfg.sax_alphabet_size,
            window_size=cfg.sax_window_size,
            strategy=cfg.sax_strategy,
        )

        # v0.21.0: Compute training PAA statistics for consistent incremental encoding
        # Without these stats, encode_incremental() falls back to per-window z-scoring
        # which always produces the middle symbol (e.g., 'c' with alpha=5), resulting
        # in zero pattern diversity and zero signal generation.
        _paa_mean = None
        _paa_std = None
        warmup_df = df.iloc[:cfg.start_offset]
        if len(warmup_df) >= cfg.sax_window_size * 2:
            try:
                _, _paa_mean, _paa_std = sax_encoder.encode_with_normalization(warmup_df)
                if _paa_std < 1e-10:
                    _paa_mean = None
                    _paa_std = None
                else:
                    console.print(f"  [green]Training PAA stats: mean={_paa_mean:.6f}, std={_paa_std:.6f}[/green]")
            except Exception as e:
                console.print(f"  [yellow]Warning: Could not compute PAA stats: {e}[/yellow]")
                _paa_mean = None
                _paa_std = None

        # v0.11.0: FuzzyMatcher for pattern breaks
        fuzzy_matcher = FuzzyMatcher(
            sax_encoder=sax_encoder,
            threshold=fuzzy_threshold,
            max_edit_distance=2,
        )

        # Create engines
        pred_engine = PredictionEngine(trie, prediction_depth=cfg.pattern_length)
        risk_mgr = RiskManager(capital=cfg.initial_capital, config=self.risk_config)

        # v0.11.0: PPMT engine for 4-level matching
        ppmt_engine = None
        if has_multi_level:
            ppmt_engine = PPMT(
                symbol=cfg.symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=cfg.sax_alphabet_size,
                sax_window_size=cfg.sax_window_size,
                sax_strategy=cfg.sax_strategy,
                fuzzy_threshold=fuzzy_threshold,
                weight_profile=info.weight_profile,
            )
            ppmt_engine.set_tries(trie_n1, trie_n2, trie_n3, trie_n4)
            ppmt_engine.adapt_weights()
            console.print(f"  [bold cyan]4-level matching enabled[/bold cyan]: weights={ppmt_engine.weights}")

        # Regime detector
        regime_detector = None
        current_regime = "ranging"
        regime_info = None
        if cfg.regime_aware:
            regime_detector = RegimeDetector(lookback=50, vol_threshold=0.6, trend_threshold=0.005)

        # Timeframe to hours (for timing display)
        tf_hours = {
            "1m": 1/60, "5m": 5/60, "15m": 15/60,
            "1h": 1, "4h": 4, "1d": 24,
        }.get(cfg.timeframe, 1)

        # State
        result = RealtimeResult(
            mode="replay",
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            initial_capital=cfg.initial_capital,
            final_capital=cfg.initial_capital,
        )
        result.equity_curve = [cfg.initial_capital]
        result.capital_history = [cfg.initial_capital]

        # Incremental SAX state
        sax_buffer = []          # Partial window buffer
        pattern_buffer = []      # Current SAX symbol pattern
        position_state = PositionState.FLAT
        current_position = None  # RealtimeTrade when in position
        trade_counter = 0
        peak_capital = cfg.initial_capital
        consecutive_breaks = 0
        last_losing_trade_idx = -999

        # ATR tracking (rolling window)
        atr_prices = []
        atr_highs = []
        atr_lows = []

        # Pre-extract arrays
        df_close = df['close'].values.astype(float)
        df_high = df['high'].values.astype(float)
        df_low = df['low'].values.astype(float)

        start_candle = cfg.start_offset
        if start_candle >= len(df):
            console.print(f"[red]Not enough data. Need {start_candle}, have {len(df)}.[/red]")
            return result

        console.print(f"\n[bold cyan]Starting Replay: {cfg.symbol} ({cfg.timeframe})[/bold cyan]")
        console.print(f"  Capital: ${cfg.initial_capital:,.2f}")
        console.print(f"  Data: {len(df)} candles, starting from index {start_candle}")
        console.print(f"  Speed: {'MAX' if cfg.speed == 0 else f'{cfg.speed:.1f}x'}")
        console.print(f"  SAX: window={cfg.sax_window_size}, alphabet={cfg.sax_alphabet_size}")
        console.print(f"  Trie: {trie.pattern_count} patterns")
        console.print(f"  Min confidence: {cfg.min_confidence:.0%}")
        console.print(f"  Regime-aware: {'ON' if cfg.regime_aware else 'OFF'}")
        console.print()

        # v0.15.0: Initialize TerminalState for dashboard
        self._update_terminal_state(
            is_running=True,
            mode="replay",
            started_at=time.time(),
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            exchange="replay",
            portfolio_value=cfg.initial_capital,
            cash=cfg.initial_capital,
            candles_processed=0,
            websocket_status="replay",
        )

        # Live display
        live_display = Live(console=console, refresh_per_second=4)

        with live_display:
            for candle_idx in range(start_candle, len(df)):
                loop_start = time.time()

                # Current candle data
                current_price = float(df_close[candle_idx])
                current_high = float(df_high[candle_idx])
                current_low = float(df_low[candle_idx])
                current_time = str(df.index[candle_idx]) if hasattr(df.index, 'strftime') else str(candle_idx)

                # Update ATR tracking
                atr_prices.append(current_price)
                atr_highs.append(current_high)
                atr_lows.append(current_low)
                # Keep last 50 for ATR calculation
                if len(atr_prices) > 50:
                    atr_prices = atr_prices[-50:]
                    atr_highs = atr_highs[-50:]
                    atr_lows = atr_lows[-50:]

                # Incremental SAX encoding: feed one candle at a time
                single_candle = df.iloc[candle_idx:candle_idx + 1]
                new_symbols, sax_buffer = sax_encoder.encode_incremental(
                    single_candle, sax_buffer,
                    paa_mean=_paa_mean, paa_std=_paa_std,  # v0.21.0: training stats
                )

                if new_symbols:
                    # New SAX symbol(s) produced
                    for new_sym in new_symbols:
                        pattern_buffer.append(new_sym)
                        result.sax_symbols_produced += 1

                        # Keep pattern buffer at max length
                        if len(pattern_buffer) > cfg.pattern_length * 2:
                            pattern_buffer = pattern_buffer[-(cfg.pattern_length * 2):]

                    # Regime detection
                    if regime_detector is not None and len(atr_prices) >= 50:
                        regime_prices = np.array(atr_prices)
                        regime_info = regime_detector.detect_detailed(regime_prices)
                        current_regime = regime_info.regime

                    # === POSITION MANAGEMENT ===
                    if position_state != PositionState.FLAT and current_position is not None:
                        pos = risk_mgr._positions.get(cfg.symbol)
                        if pos is None:
                            position_state = PositionState.FLAT
                            current_position = None
                            continue

                        # Catastrophic loss check
                        catastrophic_close = False
                        if cfg.catastrophic_loss_pct > 0:
                            if pos.direction == "LONG":
                                unrealized_loss = (pos.entry_price - current_price) / pos.entry_price * 100
                            else:
                                unrealized_loss = (current_price - pos.entry_price) / pos.entry_price * 100
                            if unrealized_loss >= cfg.catastrophic_loss_pct:
                                self._close_trade(risk_mgr, current_position, current_price,
                                                  current_time, "catastrophic_stop", result)
                                catastrophic_close = True
                                position_state = PositionState.FLAT
                                current_position = None
                                consecutive_breaks = 0

                        if catastrophic_close:
                            continue

                        # Trailing stop update
                        if pos.tp_price is not None:
                            entry = pos.entry_price
                            if pos.direction == "LONG":
                                unrealized_pct = (current_price - entry) / entry * 100
                                tp_distance_pct = (pos.tp_price - entry) / entry * 100
                            else:
                                unrealized_pct = (entry - current_price) / entry * 100
                                tp_distance_pct = (entry - pos.tp_price) / entry * 100

                            if (not current_position.trailing_activated and
                                    tp_distance_pct > 0 and unrealized_pct >= tp_distance_pct * 0.75):
                                current_position.trailing_activated = True

                            if current_position.trailing_activated:
                                current_atr = self._compute_atr_pct(
                                    np.array(atr_prices), np.array(atr_highs), np.array(atr_lows))
                                trailing_distance = current_atr * 1.5
                                if pos.direction == "LONG":
                                    new_sl = max(pos.sl_price, current_price * (1 - trailing_distance / 100))
                                else:
                                    new_sl = min(pos.sl_price, current_price * (1 + trailing_distance / 100))
                                pos.sl_price = new_sl

                        # SL/TP check at SAX boundary
                        sl_hit = risk_mgr.check_stop_loss(cfg.symbol, current_price)
                        tp_hit = risk_mgr.check_take_profit(cfg.symbol, current_price)

                        if sl_hit:
                            exit_reason = "trailing_stop" if current_position.trailing_activated else "stop_loss"
                            self._close_trade(risk_mgr, current_position, current_price,
                                              current_time, exit_reason, result)
                            position_state = PositionState.FLAT
                            current_position = None
                            consecutive_breaks = 0
                            continue

                        elif tp_hit:
                            self._close_trade(risk_mgr, current_position, current_price,
                                              current_time, "take_profit", result)
                            position_state = PositionState.FLAT
                            current_position = None
                            consecutive_breaks = 0
                            continue

                    # === PATTERN MATCHING & SIGNAL GENERATION ===
                    # Only generate signals if we have enough symbols and are FLAT
                    if len(pattern_buffer) >= cfg.pattern_length:
                        current_symbols = pattern_buffer[-cfg.pattern_length:]

                        # Generate prediction
                        prediction = pred_engine.predict(
                            current_symbols=current_symbols,
                            entry_price=current_price,
                            timeframe_hours=tf_hours,
                            symbol=cfg.symbol,
                        )

                        # Cooldown check (v0.24.0: increased from 1 to 3 symbols)
                        # After a losing trade, wait 3 SAX symbols before re-entering
                        # to avoid revenge trading and let the market settle.
                        sym_idx = result.sax_symbols_produced
                        cooldown_period = 3
                        if (current_position is None and
                                sym_idx - last_losing_trade_idx < cooldown_period and
                                last_losing_trade_idx >= 0):
                            continue

                        if prediction.direction == "FLAT" or prediction.confidence <= 0:
                            continue

                        # v0.11.0: 4-level matching (same as PaperTrader)
                        weighted_confidence = prediction.confidence
                        best_trie_level = "n3"
                        match_result = None

                        if ppmt_engine is not None:
                            ppmt_result = ppmt_engine.match_raw(
                                current_symbols=current_symbols,
                                current_price=current_price,
                            )
                            weighted_confidence = ppmt_result.weighted_confidence
                            match_result = ppmt_result

                            level_confs = {
                                "n1": ppmt_result.n1_confidence,
                                "n2": ppmt_result.n2_confidence,
                                "n3": ppmt_result.n3_confidence,
                                "n4": ppmt_result.n4_confidence,
                            }
                            best_trie_level = max(level_confs, key=level_confs.get)

                            if weighted_confidence <= 0 and prediction.confidence > 0:
                                weighted_confidence = prediction.confidence
                                best_trie_level = "n3"

                        # Entry signal generation
                        effective_min_conf = cfg.min_confidence

                        # v0.11.0: TokenProfile SHORT gating
                        if prediction.direction == "SHORT":
                            if token_profile is not None and not token_profile.short_allowed:
                                continue

                            # v0.11.0: Regime-aware SHORT gating
                            short_regime_mult = {
                                "trending_down": 0.85,
                                "ranging": 1.1,
                                "trending_up": 1.5,
                                "volatile": 1.8,
                            }.get(current_regime, 1.2)
                            effective_min_conf = max(effective_min_conf * short_regime_mult, 0.20)

                            if token_profile is not None:
                                effective_min_conf = max(
                                    effective_min_conf * token_profile.short_confidence_multiplier,
                                    effective_min_conf,
                                )

                        # v0.11.0: Regime-aware confidence adjustment
                        if cfg.regime_aware and current_regime and prediction.confidence > 0:
                            try:
                                matched_node = trie.search(current_symbols)
                                if matched_node and matched_node.metadata.regime_distribution:
                                    regime_adjustment = matched_node.metadata.regime_match_score(current_regime)
                                    if regime_adjustment > 0:
                                        effective_min_conf = effective_min_conf / regime_adjustment
                            except Exception:
                                pass

                        # v0.25.0: Aggressive signal quality filtering
                        # Backtest analysis: 24 trades, 8W/16L, PF=0.53
                        # Root cause: too many weak signals in ranging/volatile regimes.
                        # Strategy: Only trade high-conviction setups in favorable regimes.
                        # v0.32.3: validation_mode relaxes these thresholds so backtest
                        # can produce enough trades for MC simulation.
                        if getattr(cfg, 'validation_mode', False):
                            # v0.32.3: Relaxed thresholds for validation runs.
                            # Bayesian shrinkage keeps probabilities near 0.5 with low
                            # historical_count, so 0.55 rejects almost everything.
                            move_threshold = 0.50
                            prob_threshold = 0.30
                            base_prob_gate = 0.30
                            ranging_prob_gate = 0.40
                            volatile_prob_gate = 0.45
                            counter_trend_gate = 0.45
                        else:
                            # Original v0.25.0 strict thresholds (live trading)
                            move_threshold = 0.80
                            prob_threshold = 0.30
                            base_prob_gate = 0.35
                            ranging_prob_gate = 0.55
                            volatile_prob_gate = prob_threshold * 2.0  # 0.60
                            counter_trend_gate = 0.60

                        # Confidence boost: if overall_probability is strong and move is
                        # significant, boost confidence up to min_confidence level
                        boosted_confidence = weighted_confidence
                        boost_prob_trigger = 0.40 if getattr(cfg, 'validation_mode', False) else 0.45
                        boost_move_trigger = 0.80 if getattr(cfg, 'validation_mode', False) else 1.0
                        if (prediction.overall_probability >= boost_prob_trigger
                                and abs(prediction.expected_total_move_pct) >= boost_move_trigger):
                            # Strong pattern match — boost confidence by probability
                            boosted_confidence = max(
                                weighted_confidence,
                                weighted_confidence * (1 + prediction.overall_probability),
                            )

                        # v0.25.0: Hard quality gate — reject weak signals
                        if prediction.overall_probability < base_prob_gate:
                            continue
                        if abs(prediction.expected_total_move_pct) < 0.5:
                            continue

                        # v0.25.0: Strict regime-aware signal filtering
                        # KEY INSIGHT from trade analysis:
                        #   - Ranging regime: 8 losses, 2 wins → AVOID
                        #   - Volatile regime: mixed but catastrophic losses possible
                        #   - Trending: best win rate, should be primary focus
                        if current_regime == "ranging":
                            # Ranging markets are choppy — skip unless very high confidence
                            if prediction.overall_probability < ranging_prob_gate:
                                continue
                            if abs(prediction.expected_total_move_pct) < (0.80 if getattr(cfg, 'validation_mode', False) else 1.0):
                                continue
                        elif current_regime == "volatile":
                            # In volatile markets, only trade with the strongest signals
                            if prediction.overall_probability < volatile_prob_gate:
                                continue
                            if abs(prediction.expected_total_move_pct) < (1.20 if getattr(cfg, 'validation_mode', False) else move_threshold * 2.0):
                                continue
                        elif current_regime == "trending_down" and prediction.direction == "LONG":
                            # Counter-trend LONG in downtrend — extremely risky
                            if prediction.overall_probability < counter_trend_gate:
                                continue
                        elif current_regime == "trending_up" and prediction.direction == "SHORT":
                            # Counter-trend SHORT in uptrend — extremely risky
                            if prediction.overall_probability < counter_trend_gate:
                                continue

                        if (position_state == PositionState.FLAT
                                and prediction.direction != "FLAT"
                                and boosted_confidence >= effective_min_conf
                                and abs(prediction.expected_total_move_pct) > move_threshold
                                and prediction.overall_probability > prob_threshold):

                            result.signals_generated += 1

                            # v0.25.0: Aggressive SL/TP with trend-following bias
                            # Key insight: PF=0.53 means losses > gains. Need to:
                            #   1. Use wider SL to avoid premature stops
                            #   2. Use ambitious TP to let winners run
                            #   3. Ensure R:R >= 2:1 minimum for every trade
                            expected_move_abs = abs(prediction.expected_total_move_pct)

                            # v0.25.0: Regime-adaptive SL/TP with minimum R:R enforcement
                            if current_regime in ("trending_up", "trending_down"):
                                sl_mult = 1.5   # Wider SL in trends to survive pullbacks
                                tp_mult = 4.0   # Let profits run in trends
                            elif current_regime == "ranging":
                                sl_mult = 2.0   # Very wide SL in ranges (noise is extreme)
                                tp_mult = 2.5   # Moderate TP in ranges
                            else:  # volatile or unknown
                                sl_mult = 1.8   # Wide SL in volatile (whipsaws)
                                tp_mult = 3.0   # Good TP in volatile (big swings)

                            sl_distance_pct = max(min(expected_move_abs * sl_mult, 5.0), 1.0)
                            tp_distance_pct = expected_move_abs * tp_mult
                            # v0.25.0: Enforce minimum 2:1 R:R ratio
                            if tp_distance_pct < sl_distance_pct * 2.0:
                                tp_distance_pct = sl_distance_pct * 2.0

                            if prediction.direction == "LONG":
                                sl_price = current_price * (1 - sl_distance_pct / 100)
                                tp_price = current_price * (1 + tp_distance_pct / 100)
                            else:
                                sl_price = current_price * (1 + sl_distance_pct / 100)
                                tp_price = current_price * (1 - tp_distance_pct / 100)

                            risk_reward = tp_distance_pct / sl_distance_pct if sl_distance_pct > 0 else 0

                            # v0.11.0: Get actual historical_count from matched node
                            actual_historical_count = 10
                            matched_node_for_sizing = None
                            try:
                                matched_node_for_sizing = trie.search(current_symbols)
                                if matched_node_for_sizing and matched_node_for_sizing.metadata.historical_count > 0:
                                    actual_historical_count = matched_node_for_sizing.metadata.historical_count
                            except Exception:
                                pass

                            # Create signal
                            signal_type = (
                                SignalType.ENTRY_LONG if prediction.direction == "LONG"
                                else SignalType.ENTRY_SHORT
                            )

                            signal = Signal(
                                signal_type=signal_type,
                                confidence=boosted_confidence,  # v0.21.0: use boosted confidence
                                symbol=cfg.symbol,
                                entry_price=current_price,
                                sl_price=sl_price,
                                tp_price=tp_price,
                                expected_move_pct=prediction.expected_total_move_pct,
                                risk_reward_ratio=risk_reward,
                                win_rate=prediction.overall_probability,
                                historical_count=actual_historical_count,
                                matched_pattern=current_symbols,
                                trie_level=best_trie_level,
                            )
                            signal.quality_score = signal.compute_quality_score()
                            signal.sizing_multiplier = signal.compute_sizing_multiplier()

                            # Metadata sizing
                            mock_meta = BlockLifecycleMetadata(
                                win_rate=signal.win_rate,
                                expected_move_pct=signal.expected_move_pct,
                                max_drawdown_pct=-sl_distance_pct,
                                historical_count=actual_historical_count,
                            )
                            signal.probability_of_success = mock_meta.probability_of_success
                            signal.expected_profit_ahead = mock_meta.expected_profit_ahead
                            signal.metadata_sizing_signal = mock_meta.sizing_signal

                            # Apply regime multiplier
                            if cfg.regime_aware and current_regime:
                                regime_mults = {
                                    "trending_up": 1.2,
                                    "ranging": 1.0,
                                    "trending_down": 0.6,
                                    "volatile": 0.4,
                                }
                                regime_mult = regime_mults.get(current_regime, 1.0)
                                signal.metadata_sizing_signal *= regime_mult
                                signal.sizing_multiplier *= regime_mult

                            # Fire callback
                            if cfg.on_signal:
                                try:
                                    cfg.on_signal(signal, prediction)
                                except Exception:
                                    pass

                            # Risk check
                            can_open, reason = risk_mgr.can_open(signal, info.asset_class)
                            if can_open:
                                size = risk_mgr.calculate_position_size(signal)
                                position = risk_mgr.open_position(signal, size)

                                current_position = RealtimeTrade(
                                    trade_id=trade_counter + 1,
                                    symbol=cfg.symbol,
                                    direction=signal.direction or "LONG",
                                    entry_price=current_price,
                                    entry_time=current_time,
                                    size=size,
                                    confidence=signal.confidence,
                                    regime=current_regime,
                                    matched_pattern=current_symbols,
                                )
                                # Store SL/TP and trailing flag on the position
                                current_position.sl_price = sl_price  # type: ignore
                                current_position.tp_price = tp_price  # type: ignore
                                current_position.trailing_activated = False  # type: ignore

                                position_state = PositionState.LONG if signal.direction == "LONG" else PositionState.SHORT
                                trade_counter += 1

                                # v0.15.0: Update TerminalState — position opened
                                self._update_terminal_state(
                                    signal={
                                        "type": signal.signal_type.value,
                                        "direction": signal.direction,
                                        "confidence": signal.confidence,
                                        "price": current_price,
                                        "pattern": current_symbols,
                                        "trie_level": best_trie_level,
                                        "timestamp": time.time(),
                                    },
                                    positions=[{
                                        "symbol": cfg.symbol,
                                        "direction": signal.direction or "LONG",
                                        "entry_price": current_price,
                                        "sl": sl_price,
                                        "tp": tp_price,
                                        "size": size,
                                        "confidence": signal.confidence,
                                    }],
                                )

                # Record equity
                if candle_idx % 10 == 0:
                    unrealized = risk_mgr.capital
                    result.equity_curve.append(unrealized)
                    result.capital_history.append(unrealized)
                    if risk_mgr.capital > peak_capital:
                        peak_capital = risk_mgr.capital

                    # v0.15.0: Push equity to TerminalState
                    pnl_pct = (risk_mgr.capital - cfg.initial_capital) / cfg.initial_capital * 100
                    self._update_terminal_state(
                        current_price=current_price,
                        portfolio_value=risk_mgr.capital,
                        cash=risk_mgr.capital,
                        total_pnl_pct=pnl_pct,
                        equity_point={"value": risk_mgr.capital, "timestamp": time.time()},
                        regime=current_regime,
                    )

                result.candles_processed += 1

                # Update live display
                if cfg.verbose and candle_idx % 50 == 0:
                    position_str = f"[green]{position_state.value}[/green]" if position_state != PositionState.FLAT else "FLAT"
                    pnl_pct = (risk_mgr.capital - cfg.initial_capital) / cfg.initial_capital * 100
                    pnl_color = "green" if pnl_pct >= 0 else "red"
                    pnl_sign = "+" if pnl_pct >= 0 else ""

                    live_display.update(Panel(
                        f"  Candle: {candle_idx}/{len(df)} | "
                        f"Price: ${current_price:,.2f} | "
                        f"Position: {position_str} | "
                        f"P&L: [{pnl_color}]{pnl_sign}{pnl_pct:.2f}%[/{pnl_color}] | "
                        f"SAX: {len(pattern_buffer)} symbols | "
                        f"Regime: {current_regime}",
                        title=f"PPMT Replay: {cfg.symbol}",
                        border_style="cyan",
                    ))

                # Speed control
                if cfg.speed > 0:
                    candle_interval = tf_hours * 3600 / cfg.speed  # seconds per candle at this speed
                    elapsed = time.time() - loop_start
                    sleep_time = max(0, candle_interval - elapsed)
                    if sleep_time > 0:
                        time.sleep(sleep_time)

                # Fire candle callback
                if cfg.on_candle:
                    try:
                        cfg.on_candle(candle_idx, current_price)
                    except Exception:
                        pass

        # Close any open position at end
        if current_position is not None:
            last_price = float(df["close"].iloc[-1])
            self._close_trade(risk_mgr, current_position, last_price,
                              "end_of_data", "end_of_data", result)

        # Compute final statistics
        duration = time.time() - start_time
        result.duration_seconds = duration
        result.final_capital = risk_mgr.capital
        result.total_pnl = risk_mgr.capital - cfg.initial_capital
        result.total_pnl_pct = result.total_pnl / cfg.initial_capital * 100

        if result.total_trades > 0:
            result.win_rate = result.winning_trades / result.total_trades
            # Max drawdown
            if result.equity_curve:
                peak = result.equity_curve[0]
                max_dd = 0.0
                for eq in result.equity_curve:
                    if eq > peak:
                        peak = eq
                    dd = (peak - eq) / peak
                    if dd > max_dd:
                        max_dd = dd
                result.max_drawdown = max_dd

        # v0.15.0: Final TerminalState update with complete results
        self._update_terminal_state(
            is_running=False,
            portfolio_value=risk_mgr.capital,
            cash=risk_mgr.capital,
            total_pnl_pct=result.total_pnl_pct,
            total_trades=result.total_trades,
            winning_trades=result.winning_trades,
            win_rate=result.win_rate,
            max_drawdown=result.max_drawdown,
            positions=[],
            candles_processed=result.candles_processed,
            sax_symbols_produced=result.sax_symbols_produced,
        )

        storage.close()
        return result

    def _close_trade(self, risk_mgr: RiskManager, trade: RealtimeTrade,
                     exit_price: float, exit_time: str, exit_reason: str,
                     result: RealtimeResult) -> None:
        """Close a trade and record the result."""
        _, pnl = risk_mgr.close_position(trade.symbol, exit_price)
        trade.exit_price = exit_price
        trade.exit_time = str(exit_time)
        trade.pnl = pnl

        if trade.direction == "LONG":
            trade.pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
        else:
            trade.pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100

        trade.exit_reason = exit_reason

        if trade.pnl_pct > 0:
            result.winning_trades += 1
        else:
            result.losing_trades += 1

        result.total_trades += 1
        result.trades.append(trade)

        result.equity_curve.append(risk_mgr.capital)
        result.capital_history.append(risk_mgr.capital)

        # Fire trade callback
        if hasattr(self.config, 'on_trade') and self.config.on_trade:
            try:
                self.config.on_trade({
                    "trade_id": trade.trade_id,
                    "direction": trade.direction,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "pnl_pct": trade.pnl_pct,
                    "exit_reason": trade.exit_reason,
                    "regime": trade.regime,
                })
            except Exception:
                pass

        # v0.31.0: Save trade to persistent storage
        try:
            trade_storage = PPMTStorage()
            trade_storage.save_trade({
                "symbol": trade.symbol,
                "timeframe": getattr(self.config, 'timeframe', '1h'),
                "direction": trade.direction,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "size": abs(pnl) / trade.pnl_pct * 100 if trade.pnl_pct != 0 else 0,
                "pnl": pnl,
                "pnl_pct": trade.pnl_pct,
                "confidence": trade.confidence,
                "exit_reason": exit_reason,
                "regime": trade.regime,
                "leverage": getattr(self.config, 'leverage', 1),
                "matched_pattern": trade.matched_pattern,
            })
            trade_storage.close()
        except Exception:
            pass  # Never let storage crash the engine

        # v0.15.0: Update TerminalState — trade closed
        self._update_terminal_state(
            signal={
                "type": "TRADE_CLOSED",
                "direction": trade.direction,
                "entry_price": trade.entry_price,
                "exit_price": exit_price,
                "pnl_pct": trade.pnl_pct,
                "exit_reason": exit_reason,
                "timestamp": time.time(),
            },
            portfolio_value=risk_mgr.capital,
            cash=risk_mgr.capital,
            total_trades=result.total_trades,
            winning_trades=result.winning_trades,
            equity_point={"value": risk_mgr.capital, "timestamp": time.time()},
            positions=[],  # Will be updated by next candle
        )

    async def process_new_candle(
        self,
        candle,
        cfg,
        sax_encoder,
        pred_engine,
        risk_mgr,
        trie,
        ppmt_engine,
        fuzzy_matcher,
        token_profile,
        info,
        result,
        sax_buffer: list,
        pattern_buffer: list,
        position_state: PositionState,
        current_position,
        trade_counter: int,
        regime_detector,
        current_regime: str,
        recent_prices: list,
        recent_highs: list,
        recent_lows: list,
        peak_capital: float,
        last_losing_trade_idx: int = -999,
        exchange=None,
        paa_mean: float = None,  # v0.21.0: training stats for consistent SAX
        paa_std: float = None,
    ) -> tuple:
        """
        Process a single closed candle through the full PPMT pipeline.

        This is the core streaming pipeline:
          Candle → SAX encode → Pattern buffer → Match → Signal → Risk → Position

        Shared between run_replay(), run_live(), and external callers.
        Returns updated state tuple for functional-style state management.

        Args:
            candle: Candle object from WebSocketFeed or replay
            cfg: LiveConfig or ReplayConfig
            sax_encoder: SAXEncoder instance
            pred_engine: PredictionEngine instance
            risk_mgr: RiskManager instance
            trie: PPMTTrie (N3 level)
            ppmt_engine: PPMT engine (4-level, or None)
            fuzzy_matcher: FuzzyMatcher instance
            token_profile: TokenProfile or None
            info: AssetClassification
            result: RealtimeResult to update
            sax_buffer: Current SAX partial window buffer
            pattern_buffer: Current SAX symbol pattern
            position_state: Current PositionState
            current_position: Current RealtimeTrade or None
            trade_counter: Trade counter
            regime_detector: RegimeDetector or None
            current_regime: Current regime string
            recent_prices: Rolling price list for ATR
            recent_highs: Rolling high list
            recent_lows: Rolling low list
            peak_capital: Peak capital for drawdown
            last_losing_trade_idx: Last losing trade symbol index
            exchange: ccxt exchange for order execution (or None)

        Returns:
            (sax_buffer, pattern_buffer, position_state, current_position,
             trade_counter, current_regime, peak_capital, last_losing_trade_idx)
        """
        current_price = candle.close
        current_high = candle.high
        current_low = candle.low
        current_time = str(candle.timestamp)

        # Update ATR tracking
        recent_prices.append(current_price)
        recent_highs.append(current_high)
        recent_lows.append(current_low)
        if len(recent_prices) > 50:
            del recent_prices[:-50]
            del recent_highs[:-50]
            del recent_lows[:-50]

        # Incremental SAX encoding: feed one candle at a time
        single_df = candle.to_dataframe_row()
        new_symbols, sax_buffer = sax_encoder.encode_incremental(
            single_df, sax_buffer,
            paa_mean=paa_mean, paa_std=paa_std,  # v0.21.0: training stats
        )

        if new_symbols:
            for new_sym in new_symbols:
                pattern_buffer.append(new_sym)
                result.sax_symbols_produced += 1
                if len(pattern_buffer) > cfg.pattern_length * 2:
                    del pattern_buffer[:len(pattern_buffer) - cfg.pattern_length * 2]

            # Regime detection
            if regime_detector is not None and len(recent_prices) >= 50:
                regime_info = regime_detector.detect_detailed(np.array(recent_prices))
                current_regime = regime_info.regime

            # === POSITION MANAGEMENT ===
            if position_state != PositionState.FLAT and current_position is not None:
                pos = risk_mgr._positions.get(cfg.symbol)
                if pos is not None:
                    # Catastrophic loss check
                    if cfg.catastrophic_loss_pct > 0:
                        if pos.direction == "LONG":
                            unrealized_loss = (pos.entry_price - current_price) / pos.entry_price * 100
                        else:
                            unrealized_loss = (current_price - pos.entry_price) / pos.entry_price * 100
                        if unrealized_loss >= cfg.catastrophic_loss_pct:
                            self._close_trade(risk_mgr, current_position, current_price,
                                              current_time, "catastrophic_stop", result)
                            position_state = PositionState.FLAT
                            current_position = None
                            return (sax_buffer, pattern_buffer, position_state, current_position,
                                    trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                    # Trailing stop update
                    if pos.tp_price is not None:
                        entry = pos.entry_price
                        if pos.direction == "LONG":
                            unrealized_pct = (current_price - entry) / entry * 100
                            tp_distance_pct = (pos.tp_price - entry) / entry * 100
                        else:
                            unrealized_pct = (entry - current_price) / entry * 100
                            tp_distance_pct = (entry - pos.tp_price) / entry * 100

                        if (not getattr(current_position, 'trailing_activated', False)
                                and tp_distance_pct > 0 and unrealized_pct >= tp_distance_pct * 0.75):
                            current_position.trailing_activated = True  # type: ignore

                        if getattr(current_position, 'trailing_activated', False):
                            current_atr = self._compute_atr_pct(
                                np.array(recent_prices), np.array(recent_highs), np.array(recent_lows))
                            trailing_distance = current_atr * 1.5
                            if pos.direction == "LONG":
                                new_sl = max(pos.sl_price, current_price * (1 - trailing_distance / 100))
                            else:
                                new_sl = min(pos.sl_price, current_price * (1 + trailing_distance / 100))
                            pos.sl_price = new_sl

                    # SL/TP check
                    sl_hit = risk_mgr.check_stop_loss(cfg.symbol, current_price)
                    tp_hit = risk_mgr.check_take_profit(cfg.symbol, current_price)

                    if sl_hit:
                        exit_reason = ("trailing_stop"
                                       if getattr(current_position, 'trailing_activated', False)
                                       else "stop_loss")
                        self._close_trade(risk_mgr, current_position, current_price,
                                          current_time, exit_reason, result)
                        position_state = PositionState.FLAT
                        current_position = None
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                    elif tp_hit:
                        self._close_trade(risk_mgr, current_position, current_price,
                                          current_time, "take_profit", result)
                        position_state = PositionState.FLAT
                        current_position = None
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)

            # === PATTERN MATCHING & SIGNAL GENERATION ===
            if len(pattern_buffer) >= cfg.pattern_length and position_state == PositionState.FLAT:
                current_symbols = pattern_buffer[-cfg.pattern_length:]

                # Cooldown check
                sym_idx = result.sax_symbols_produced
                if (sym_idx - last_losing_trade_idx < 1 and last_losing_trade_idx >= 0):
                    result.candles_processed += 1
                    return (sax_buffer, pattern_buffer, position_state, current_position,
                            trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                # Generate prediction
                tf_hours = {
                    "1m": 1/60, "5m": 5/60, "15m": 15/60,
                    "1h": 1, "4h": 4, "1d": 24,
                }.get(cfg.timeframe, 1)

                prediction = pred_engine.predict(
                    current_symbols=current_symbols,
                    entry_price=current_price,
                    timeframe_hours=tf_hours,
                    symbol=cfg.symbol,
                )

                if prediction.direction == "FLAT" or prediction.confidence <= 0:
                    result.candles_processed += 1
                    return (sax_buffer, pattern_buffer, position_state, current_position,
                            trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                # v0.12.0: 4-level matching
                weighted_confidence = prediction.confidence
                best_trie_level = "n3"

                if ppmt_engine is not None:
                    ppmt_result = ppmt_engine.match_raw(
                        current_symbols=current_symbols,
                        current_price=current_price,
                    )
                    weighted_confidence = ppmt_result.weighted_confidence

                    level_confs = {
                        "n1": ppmt_result.n1_confidence,
                        "n2": ppmt_result.n2_confidence,
                        "n3": ppmt_result.n3_confidence,
                        "n4": ppmt_result.n4_confidence,
                    }
                    best_trie_level = max(level_confs, key=level_confs.get)

                    if weighted_confidence <= 0 and prediction.confidence > 0:
                        weighted_confidence = prediction.confidence
                        best_trie_level = "n3"

                effective_min_conf = cfg.min_confidence

                # v0.12.0: TokenProfile SHORT gating
                if prediction.direction == "SHORT":
                    if token_profile is not None and not token_profile.short_allowed:
                        result.candles_processed += 1
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                    short_regime_mult = {
                        "trending_down": 0.85,
                        "ranging": 1.1,
                        "trending_up": 1.5,
                        "volatile": 1.8,
                    }.get(current_regime, 1.2)
                    effective_min_conf = max(effective_min_conf * short_regime_mult, 0.20)

                    if token_profile is not None:
                        effective_min_conf = max(
                            effective_min_conf * token_profile.short_confidence_multiplier,
                            effective_min_conf,
                        )

                # v0.12.0: Regime-aware confidence adjustment
                if cfg.regime_aware and current_regime and prediction.confidence > 0:
                    try:
                        matched_node = trie.search(current_symbols)
                        if matched_node and matched_node.metadata.regime_distribution:
                            regime_adjustment = matched_node.metadata.regime_match_score(current_regime)
                            if regime_adjustment > 0:
                                effective_min_conf = effective_min_conf / regime_adjustment
                    except Exception:
                        pass

                # Entry signal check (v0.22.0: tightened thresholds)
                if (prediction.direction != "FLAT"
                        and weighted_confidence >= effective_min_conf
                        and abs(prediction.expected_total_move_pct) > 0.30
                        and prediction.overall_probability > 0.15):

                    result.signals_generated += 1

                    # v0.22.0: Prediction-aware SL/TP with tighter stops
                    expected_move_abs = abs(prediction.expected_total_move_pct)
                    sl_distance_pct = max(min(expected_move_abs * 1.2, 3.0), 0.5)
                    tp_distance_pct = expected_move_abs * 2.0
                    if tp_distance_pct < sl_distance_pct * 1.5:
                        tp_distance_pct = sl_distance_pct * 1.5

                    if prediction.direction == "LONG":
                        sl_price = current_price * (1 - sl_distance_pct / 100)
                        tp_price = current_price * (1 + tp_distance_pct / 100)
                    else:
                        sl_price = current_price * (1 + sl_distance_pct / 100)
                        tp_price = current_price * (1 - tp_distance_pct / 100)

                    risk_reward = tp_distance_pct / sl_distance_pct if sl_distance_pct > 0 else 0

                    # Get actual historical_count from matched node
                    actual_historical_count = 10
                    try:
                        matched_node_for_sizing = trie.search(current_symbols)
                        if matched_node_for_sizing and matched_node_for_sizing.metadata.historical_count > 0:
                            actual_historical_count = matched_node_for_sizing.metadata.historical_count
                    except Exception:
                        pass

                    # Create signal
                    signal_type = (
                        SignalType.ENTRY_LONG if prediction.direction == "LONG"
                        else SignalType.ENTRY_SHORT
                    )

                    signal = Signal(
                        signal_type=signal_type,
                        confidence=weighted_confidence,
                        symbol=cfg.symbol,
                        entry_price=current_price,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        expected_move_pct=prediction.expected_total_move_pct,
                        risk_reward_ratio=risk_reward,
                        win_rate=prediction.overall_probability,
                        historical_count=actual_historical_count,
                        matched_pattern=current_symbols,
                        trie_level=best_trie_level,
                    )
                    signal.quality_score = signal.compute_quality_score()
                    signal.sizing_multiplier = signal.compute_sizing_multiplier()

                    # Metadata sizing
                    mock_meta = BlockLifecycleMetadata(
                        win_rate=signal.win_rate,
                        expected_move_pct=signal.expected_move_pct,
                        max_drawdown_pct=-sl_distance_pct,
                        historical_count=actual_historical_count,
                    )
                    signal.probability_of_success = mock_meta.probability_of_success
                    signal.expected_profit_ahead = mock_meta.expected_profit_ahead
                    signal.metadata_sizing_signal = mock_meta.sizing_signal

                    # Apply regime multiplier
                    if cfg.regime_aware and current_regime:
                        regime_mults = {
                            "trending_up": 1.2,
                            "ranging": 1.0,
                            "trending_down": 0.6,
                            "volatile": 0.4,
                        }
                        regime_mult = regime_mults.get(current_regime, 1.0)
                        signal.metadata_sizing_signal *= regime_mult
                        signal.sizing_multiplier *= regime_mult

                    # Fire signal callback
                    if hasattr(cfg, 'on_signal') and cfg.on_signal:
                        try:
                            cfg.on_signal(signal, prediction)
                        except Exception:
                            pass

                    # Risk check
                    can_open, reason = risk_mgr.can_open(signal, info.asset_class)
                    if can_open:
                        size = risk_mgr.calculate_position_size(signal)

                        # Execute order (only in non-dry-run with exchange)
                        if not getattr(cfg, 'dry_run', True) and exchange is not None:
                            try:
                                side = 'buy' if prediction.direction == "LONG" else 'sell'
                                order = await exchange.create_order(
                                    cfg.symbol, 'market', side, size
                                )
                                console.print(f"[green]Order executed: {side} {size} {cfg.symbol}[/green]")
                            except Exception as e:
                                console.print(f"[red]Order failed: {e}[/red]")
                                result.candles_processed += 1
                                return (sax_buffer, pattern_buffer, position_state, current_position,
                                        trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                        position = risk_mgr.open_position(signal, size)

                        current_position = RealtimeTrade(
                            trade_id=trade_counter + 1,
                            symbol=cfg.symbol,
                            direction=signal.direction or "LONG",
                            entry_price=current_price,
                            entry_time=current_time,
                            size=size,
                            confidence=signal.confidence,
                            regime=current_regime,
                            matched_pattern=current_symbols,
                        )
                        current_position.sl_price = sl_price  # type: ignore
                        current_position.tp_price = tp_price  # type: ignore
                        current_position.trailing_activated = False  # type: ignore

                        position_state = (PositionState.LONG
                                          if prediction.direction == "LONG"
                                          else PositionState.SHORT)
                        trade_counter += 1

        # Record equity
        result.candles_processed += 1
        if risk_mgr.capital > peak_capital:
            peak_capital = risk_mgr.capital

        return (sax_buffer, pattern_buffer, position_state, current_position,
                trade_counter, current_regime, peak_capital, last_losing_trade_idx)

    async def run_live(self) -> RealtimeResult:
        """
        Run live mode: connect to exchange WebSocket and process candles in real-time.

        v0.12.0: Now uses WebSocketFeed for true streaming instead of REST polling.
        The pipeline is:
          WebSocket → Candle → process_new_candle() → SAX → Match → Signal → Trade

        Falls back to ccxt REST polling if websockets is not installed.

        Returns:
            RealtimeResult with trading statistics
        """
        if not isinstance(self.config, LiveConfig):
            raise ValueError("run_live() requires LiveConfig")

        cfg = self.config
        start_time = time.time()
        storage = PPMTStorage()

        # Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(cfg.symbol)

        # v0.12.0: TokenProfile + auto-calibration + persistence
        token_profile, cfg = self._setup_token_profile(cfg, info, storage)

        # Load tries (v0.12.0: all 4 levels)
        from ppmt.engine.ppmt import PPMT
        all_tries = storage.load_all_tries(cfg.symbol)
        trie_n1 = all_tries["n1"]
        trie_n2 = all_tries["n2"]
        trie_n3 = all_tries["n3"]
        trie_n4 = all_tries["n4"]

        if trie_n3 is None:
            console.print(f"[red]No Trie for {cfg.symbol}. Run 'ppmt build' first.[/red]")
            storage.close()
            return RealtimeResult(mode="live", symbol=cfg.symbol, timeframe=cfg.timeframe)

        trie = trie_n3
        trie.propagate_metadata()
        console.print(f"[green]Loaded N3 Trie ({trie.pattern_count} patterns)[/green]")

        has_multi_level = (
            getattr(cfg, 'use_multi_level', False)
            and trie_n1 is not None
            and trie_n2 is not None
            and trie_n4 is not None
        )

        # Create SAX encoder and engines
        sax_encoder = SAXEncoder(
            alphabet_size=cfg.sax_alphabet_size,
            window_size=cfg.sax_window_size,
            strategy=cfg.sax_strategy,
        )

        # v0.21.0→v0.27.0: Compute training PAA statistics for consistent incremental encoding
        # In live mode, we load historical data for warmup normalization stats
        _paa_mean = None
        _paa_std = None
        try:
            df_warmup = storage.load_ohlcv(cfg.symbol, cfg.timeframe)
            if df_warmup is not None and not df_warmup.empty and len(df_warmup) >= cfg.sax_window_size * 2:
                _, _paa_mean, _paa_std = sax_encoder.encode_with_normalization(df_warmup)
                if _paa_std < 1e-10:
                    _paa_mean = None
                    _paa_std = None
                else:
                    console.print(f"  [green]Training PAA stats: mean={_paa_mean:.6f}, std={_paa_std:.6f}[/green]")
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not compute PAA stats: {e}[/yellow]")

        pred_engine = PredictionEngine(trie, prediction_depth=cfg.pattern_length)

        # v0.20.0: MoneyManager replaces basic RiskManager for live mode
        # MoneyManager adds: portfolio exposure caps, kill switch, circuit breakers,
        # leverage tracking, correlation limits, auto-save, and Kelly sizing
        money_config = MoneyManagerConfig(
            initial_capital=cfg.initial_capital,
            max_open_positions=getattr(cfg, 'max_open_positions', 5),
            max_correlated_positions=getattr(cfg, 'max_correlated_positions', 2),
            max_portfolio_exposure_pct=getattr(cfg, 'max_portfolio_exposure_pct', 0.80),
            max_single_position_exposure_pct=getattr(cfg, 'max_single_position_pct', 0.25),
            kill_switch_exposure_pct=getattr(cfg, 'kill_switch_pct', 0.95),
            max_daily_loss_pct=getattr(cfg, 'daily_loss_limit_pct', 0.05),
            max_drawdown_pct=getattr(cfg, 'max_drawdown_pct', 0.15),
            auto_save_interval_minutes=5,
            state_file=os.path.join(os.path.expanduser("~/.ppmt"), f"money_mgr_{cfg.symbol.replace('/', '_')}.json"),
        )
        money_mgr = MoneyManager(config=money_config)
        risk_mgr = money_mgr.risk_manager  # Backward compat reference

        # v0.20.0: Advanced position sizer (Kelly Criterion + regime + drawdown)
        position_sizer = None
        if getattr(cfg, 'use_kelly_sizing', True):
            position_sizer = AdvancedPositionSizer(
                max_position_pct=getattr(cfg, 'max_single_position_pct', 0.25),
                min_position_pct=0.005,
                kelly_fraction=getattr(cfg, 'kelly_fraction', 0.25),
            )

        # v0.12.0: FuzzyMatcher
        fuzzy_threshold = token_profile.fuzzy_threshold if token_profile else 0.80
        fuzzy_matcher = FuzzyMatcher(
            sax_encoder=sax_encoder,
            threshold=fuzzy_threshold,
            max_edit_distance=2,
        )

        # v0.12.0: PPMT engine for 4-level matching
        ppmt_engine = None
        if has_multi_level:
            ppmt_engine = PPMT(
                symbol=cfg.symbol,
                asset_class=info.asset_class,
                sax_alphabet_size=cfg.sax_alphabet_size,
                sax_window_size=cfg.sax_window_size,
                sax_strategy=cfg.sax_strategy,
                fuzzy_threshold=fuzzy_threshold,
                weight_profile=info.weight_profile,
            )
            ppmt_engine.set_tries(trie_n1, trie_n2, trie_n3, trie_n4)
            ppmt_engine.adapt_weights()
            console.print(f"  [bold cyan]4-level matching enabled[/bold cyan]: weights={ppmt_engine.weights}")

        # Setup ccxt exchange for order execution (optional — only needed for real orders)
        exchange = None
        if not cfg.dry_run:
            try:
                import ccxt.async_support as ccxt_async
                exchange_class = getattr(ccxt_async, cfg.exchange, None)
                if exchange_class is not None:
                    exchange_config = {}
                    if cfg.api_key:
                        exchange_config['apiKey'] = cfg.api_key
                    if cfg.api_secret:
                        exchange_config['secret'] = cfg.api_secret
                    if cfg.testnet:
                        exchange_config['options'] = {'defaultType': 'future'}

                    exchange = exchange_class(exchange_config)
                    if cfg.testnet:
                        exchange.set_sandbox_mode(True)
                        console.print(f"[yellow]Using {cfg.exchange} TESTNET for orders[/yellow]")

                    await exchange.load_markets()

                    # v0.20.0: Set leverage on exchange if > 1
                    leverage = getattr(cfg, 'leverage', 1)
                    if leverage > 1:
                        try:
                            await exchange.set_leverage(leverage, cfg.symbol)
                            console.print(f"[bold cyan]Leverage set to {leverage}x on {cfg.exchange}[/bold cyan]")
                        except Exception as e:
                            console.print(f"[yellow]Could not set leverage: {e} — using default[/yellow]")

                    console.print(f"[green]Connected to {cfg.exchange} for order execution[/green]")
            except ImportError:
                console.print("[yellow]ccxt not installed — order execution disabled[/yellow]")
            except Exception as e:
                console.print(f"[yellow]Exchange connection failed: {e} — order execution disabled[/yellow]")

        # State
        result = RealtimeResult(
            mode="live",
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            initial_capital=cfg.initial_capital,
            final_capital=cfg.initial_capital,
        )
        result.equity_curve = [cfg.initial_capital]
        result.capital_history = [cfg.initial_capital]

        # v0.13.0: Use StreamingPatternBuffer for structured state management
        stream_buf = StreamingPatternBuffer(
            pattern_length=cfg.pattern_length,
            max_buffer_length=cfg.pattern_length * 3,
            track_history=True,
        )
        sax_buffer = []
        position_state = PositionState.FLAT
        current_position = None
        trade_counter = 0
        peak_capital = cfg.initial_capital
        last_losing_trade_idx = -999
        candles_since_calibration = 0  # v0.13.0: Recalibration counter

        regime_detector = None
        current_regime = "ranging"
        if cfg.regime_aware:
            regime_detector = RegimeDetector()

        recent_prices = []
        recent_highs = []
        recent_lows = []

        # v0.12.0: Choose WebSocket or REST polling
        use_websocket = True
        try:
            import websockets  # noqa: F401
        except ImportError:
            use_websocket = False
            console.print("[yellow]websockets not installed — using REST polling (slower)[/yellow]")
            console.print("[dim]Install with: pip install websockets>=12.0[/dim]")

        # Warmup: how many candles to fetch before streaming?
        # Need at least (sax_window_size + pattern_length) candles to produce first signal
        # v0.36.0: When sax_window_size=0 (auto from TokenProfile), the formula below
        # evaluates to 0 → no warmup → WS connects but no candles flow until the next
        # candle period closes (up to 1h on 1h TF!). This was the root cause of
        # "candles stuck at 35" reports — the 35 came from a previous session's
        # terminal_state, not from the new WS connection.
        # Fix: enforce a sane minimum (200) so warmup ALWAYS runs and feeds the
        # SAX encoder + Trie immediately on session start.
        _raw_warmup = cfg.sax_window_size * 2 + cfg.pattern_length * cfg.sax_window_size
        warmup_candles = max(_raw_warmup, 200)

        console.print(f"\n[bold cyan]Starting Live Trading: {cfg.symbol} ({cfg.timeframe})[/bold cyan]")
        console.print(f"  Exchange: {cfg.exchange} ({'TESTNET' if cfg.testnet else 'MAINNET'})")
        console.print(f"  Data Source: {'WebSocket' if use_websocket else 'REST polling'}")
        console.print(f"  Dry Run: {'YES' if cfg.dry_run else 'NO - REAL ORDERS'}")
        console.print(f"  Mode: {'AUTO' if getattr(cfg, 'auto_mode', True) else 'MANUAL (signals displayed only)'}")
        console.print(f"  Capital: ${cfg.initial_capital:,.2f}")
        console.print(f"  Leverage: {getattr(cfg, 'leverage', 1)}x")
        console.print(f"  Max Positions: {getattr(cfg, 'max_open_positions', 5)}")
        console.print(f"  Max Exposure: {getattr(cfg, 'max_portfolio_exposure_pct', 0.80):.0%}")
        console.print(f"  Kelly Sizing: {'ON' if getattr(cfg, 'use_kelly_sizing', True) else 'OFF'}")
        console.print(f"  Kill Switch: {getattr(cfg, 'kill_switch_pct', 0.95):.0%} exposure")
        console.print(f"  Daily Loss Limit: {getattr(cfg, 'daily_loss_limit_pct', 0.05):.0%}")
        console.print(f"  Warmup: {warmup_candles} candles")
        console.print(f"  SAX: window={cfg.sax_window_size}, alphabet={cfg.sax_alphabet_size}")
        console.print(f"  Trie: {trie.pattern_count} patterns")
        console.print()

        # v0.15.0: Initialize TerminalState for live dashboard
        self._update_terminal_state(
            is_running=True,
            mode="live",
            started_at=time.time(),
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            exchange=cfg.exchange,
            portfolio_value=cfg.initial_capital,
            cash=cfg.initial_capital,
            candles_processed=0,
            websocket_status="connecting",
        )

        try:
            with Live(console=console, refresh_per_second=2) as live_display:

                if use_websocket:
                    # === WEBSOCKET MODE (v0.12.0 → v0.13.0 with StreamingPatternBuffer) ===
                    from ppmt.data.websocket_feed import WebSocketFeed, Candle

                    async def on_candle(candle: Candle):
                        nonlocal sax_buffer, position_state
                        nonlocal current_position, trade_counter, current_regime
                        nonlocal peak_capital, last_losing_trade_idx
                        nonlocal candles_since_calibration

                        # Use StreamingPatternBuffer for SAX symbol management
                        pattern_buffer = stream_buf.pattern_buffer

                        (sax_buffer, _pattern_buffer, position_state, current_position,
                         trade_counter, current_regime, peak_capital,
                         last_losing_trade_idx) = await self.process_new_candle(
                            candle=candle,
                            cfg=cfg,
                            sax_encoder=sax_encoder,
                            pred_engine=pred_engine,
                            risk_mgr=risk_mgr,
                            trie=trie,
                            ppmt_engine=ppmt_engine,
                            fuzzy_matcher=fuzzy_matcher,
                            token_profile=token_profile,
                            info=info,
                            result=result,
                            sax_buffer=sax_buffer,
                            pattern_buffer=pattern_buffer,
                            position_state=position_state,
                            current_position=current_position,
                            trade_counter=trade_counter,
                            regime_detector=regime_detector,
                            current_regime=current_regime,
                            recent_prices=recent_prices,
                            recent_highs=recent_highs,
                            recent_lows=recent_lows,
                            peak_capital=peak_capital,
                            last_losing_trade_idx=last_losing_trade_idx,
                            exchange=exchange,
                            paa_mean=_paa_mean,
                            paa_std=_paa_std,
                        )

                        # v0.13.0: Sync buffer state back to StreamingPatternBuffer
                        # (process_new_candle returns updated lists)
                        # We track new symbols via the returned pattern_buffer
                        new_symbols_in_buf = _pattern_buffer[len(pattern_buffer):]
                        if new_symbols_in_buf:
                            for sym in new_symbols_in_buf:
                                stream_buf._pattern_buffer.append(sym)
                                stream_buf._symbol_counts[sym] += 1
                                stream_buf._total_symbols += 1
                                stream_buf._symbols_produced += 1
                            stream_buf._trim()

                        # v0.13.0: Living Trie updates
                        if getattr(cfg, 'living_trie', False) and stream_buf.has_pattern():
                            _living_trie_update(
                                stream_buf, trie, ppmt_engine, cfg, current_regime
                            )

                        # v0.13.0: Periodic recalibration
                        # v0.36.1: When recalibration_interval == 0 (default), auto-resolve
                        # via TF-aware get_recalibration_interval().
                        candles_since_calibration += 1
                        recalc_interval = getattr(cfg, 'recalibration_interval', 0)
                        if recalc_interval <= 0:
                            recalc_interval = get_recalibration_interval(
                                _tf_to_minutes(cfg.timeframe)
                            )
                        if (recalc_interval > 0
                                and candles_since_calibration >= recalc_interval):
                            _recalibrate(sax_encoder, cfg, storage, info)
                            candles_since_calibration = 0

                        # v0.20.0: Periodic Living Trie persistence (v0.30.0: all 4 levels)
                        trie_persist_interval = getattr(cfg, 'trie_persist_interval', 100)
                        if trie_persist_interval > 0 and result.candles_processed % trie_persist_interval == 0:
                            try:
                                for level_name, level_trie in [
                                    ("n1", trie_n1), ("n2", trie_n2),
                                    ("n3", trie), ("n4", trie_n4),
                                ]:
                                    if level_trie is not None:
                                        storage.save_trie(cfg.symbol, level_name, level_trie)
                            except Exception:
                                pass  # Non-critical

                        # v0.20.0: Periodic MoneyManager auto-save + update TerminalState
                        try:
                            money_mgr._maybe_auto_save()
                        except Exception:
                            pass

                        # v0.20.0: Update TerminalState with portfolio data
                        self._update_terminal_state(
                            current_price=recent_prices[-1] if recent_prices else 0,
                            candles_processed=result.candles_processed,
                            portfolio_value=money_mgr.total_value,
                            cash=money_mgr.cash,
                            unrealized_pnl=money_mgr.unrealized_pnl,
                            realized_pnl=money_mgr.realized_pnl,
                            total_trades=result.total_trades,
                            winning_trades=result.winning_trades,
                            exposure_pct=money_mgr.exposure_pct,
                            pattern_symbol=stream_buf.last_symbol if stream_buf.last_symbol else None,
                            entropy=stream_buf.entropy,
                            regime=current_regime,
                        )

                        # v0.20.0: Update money manager positions
                        if current_position is not None and recent_prices:
                            try:
                                money_mgr.update_position(cfg.symbol, recent_prices[-1])
                            except Exception:
                                pass

                        # Update display
                        _update_live_display(live_display, cfg, result, position_state,
                                             current_position, current_regime, recent_prices,
                                             risk_mgr, stream_buf)

                    def on_status(status: str, msg: str):
                        console.print(f"[dim][{status}] {msg}[/dim]")

                    def on_error(error):
                        console.print(f"[red]WebSocket error: {error}[/red]")

                    feed = WebSocketFeed(
                        symbol=cfg.symbol,
                        timeframe=cfg.timeframe,
                        exchange=cfg.exchange,
                        on_candle=on_candle,
                        on_status=on_status,
                        on_error=on_error,
                        testnet=cfg.testnet,
                        warmup_candles=warmup_candles,
                    )

                    # Run until interrupted
                    await feed.start()

                else:
                    # === REST POLLING FALLBACK (ccxt) ===
                    try:
                        import ccxt.async_support as ccxt_async
                    except ImportError:
                        console.print("[red]ccxt is required for REST polling. Install with: pip install ccxt>=4.0.0[/red]")
                        storage.close()
                        return result

                    exchange_class = getattr(ccxt_async, cfg.exchange, None)
                    if exchange_class is None:
                        console.print(f"[red]Exchange '{cfg.exchange}' not found in ccxt[/red]")
                        storage.close()
                        return result

                    exchange_config = {}
                    if cfg.api_key:
                        exchange_config['apiKey'] = cfg.api_key
                    if cfg.api_secret:
                        exchange_config['secret'] = cfg.api_secret

                    poll_exchange = exchange_class(exchange_config)
                    if cfg.testnet:
                        poll_exchange.set_sandbox_mode(True)

                    try:
                        await poll_exchange.load_markets()
                        console.print(f"[green]Connected to {cfg.exchange} (REST polling)[/green]")
                    except Exception as e:
                        console.print(f"[red]Failed to connect: {e}[/red]")
                        await poll_exchange.close()
                        storage.close()
                        return result

                    last_candle_ts = 0

                    # Warmup: fetch some historical candles first
                    try:
                        warmup_ohlcv = await poll_exchange.fetch_ohlcv(
                            cfg.symbol, cfg.timeframe, limit=min(warmup_candles, 500)
                        )
                        from ppmt.data.websocket_feed import Candle
                        for c in warmup_ohlcv:
                            candle = Candle(
                                timestamp=c[0], open=c[1], high=c[2],
                                low=c[3], close=c[4], volume=c[5],
                                closed=True, exchange=cfg.exchange,
                                symbol=cfg.symbol, timeframe=cfg.timeframe,
                            )
                            (sax_buffer, pattern_buffer, position_state, current_position,
                             trade_counter, current_regime, peak_capital,
                             last_losing_trade_idx) = await self.process_new_candle(
                                candle=candle, cfg=cfg, sax_encoder=sax_encoder,
                                pred_engine=pred_engine, risk_mgr=risk_mgr,
                                trie=trie, ppmt_engine=ppmt_engine,
                                fuzzy_matcher=fuzzy_matcher, token_profile=token_profile,
                                info=info, result=result, sax_buffer=sax_buffer,
                                pattern_buffer=pattern_buffer, position_state=position_state,
                                current_position=current_position, trade_counter=trade_counter,
                                regime_detector=regime_detector, current_regime=current_regime,
                                recent_prices=recent_prices, recent_highs=recent_highs,
                                recent_lows=recent_lows, peak_capital=peak_capital,
                                last_losing_trade_idx=last_losing_trade_idx,
                                paa_mean=_paa_mean, paa_std=_paa_std,
                            )
                        console.print(f"[green]Warmup: processed {len(warmup_ohlcv)} historical candles[/green]")
                    except Exception as e:
                        console.print(f"[yellow]Warmup failed: {e}[/yellow]")

                    while True:
                        try:
                            ohlcv = await poll_exchange.fetch_ohlcv(cfg.symbol, cfg.timeframe, limit=1)

                            if ohlcv:
                                candle_data = ohlcv[-1]
                                candle_ts = candle_data[0]

                                if candle_ts != last_candle_ts:
                                    last_candle_ts = candle_ts

                                    from ppmt.data.websocket_feed import Candle
                                    candle = Candle(
                                        timestamp=candle_data[0],
                                        open=candle_data[1], high=candle_data[2],
                                        low=candle_data[3], close=candle_data[4],
                                        volume=candle_data[5], closed=True,
                                        exchange=cfg.exchange, symbol=cfg.symbol,
                                        timeframe=cfg.timeframe,
                                    )

                                    (sax_buffer, pattern_buffer, position_state, current_position,
                                     trade_counter, current_regime, peak_capital,
                                     last_losing_trade_idx) = await self.process_new_candle(
                                        candle=candle, cfg=cfg, sax_encoder=sax_encoder,
                                        pred_engine=pred_engine, risk_mgr=risk_mgr,
                                        trie=trie, ppmt_engine=ppmt_engine,
                                        fuzzy_matcher=fuzzy_matcher, token_profile=token_profile,
                                        info=info, result=result, sax_buffer=sax_buffer,
                                        pattern_buffer=pattern_buffer, position_state=position_state,
                                        current_position=current_position, trade_counter=trade_counter,
                                        regime_detector=regime_detector, current_regime=current_regime,
                                        recent_prices=recent_prices, recent_highs=recent_highs,
                                        recent_lows=recent_lows, peak_capital=peak_capital,
                                        last_losing_trade_idx=last_losing_trade_idx,
                                        exchange=exchange if not cfg.dry_run else poll_exchange,
                                        paa_mean=_paa_mean, paa_std=_paa_std,
                                    )

                                    _update_live_display(live_display, cfg, result, position_state,
                                                         current_position, current_regime, recent_prices,
                                                         risk_mgr)

                            # Poll every 30 seconds
                            await asyncio.sleep(30)

                        except KeyboardInterrupt:
                            console.print("\n[yellow]Interrupted by user. Shutting down...[/yellow]")
                            break
                        except Exception as e:
                            console.print(f"[red]Polling error: {e}[/red]")
                            await asyncio.sleep(10)

                    await poll_exchange.close()

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Shutting down...[/yellow]")

        finally:
            # Cleanup exchange connection
            if exchange is not None:
                try:
                    await exchange.close()
                except Exception:
                    pass

            # v0.20.0: Save Living Trie to database on shutdown
            if getattr(cfg, 'living_trie', False):
                try:
                    for level_name, level_trie in [("n3", trie), ("n1", trie_n1), ("n2", trie_n2), ("n4", trie_n4)]:
                        if level_trie is not None:
                            storage.save_trie(cfg.symbol, level_name, level_trie)
                    console.print("[green]Living Trie saved to database[/green]")
                except Exception as e:
                    console.print(f"[yellow]Failed to save Living Trie: {e}[/yellow]")

            # v0.20.0: Save MoneyManager state on shutdown
            try:
                money_mgr.save_state()
                console.print("[green]Money Manager state saved[/green]")
            except Exception as e:
                console.print(f"[yellow]Failed to save Money Manager state: {e}[/yellow]")

            # Close any open position
            if current_position is not None:
                if cfg.dry_run:
                    last_price = recent_prices[-1] if recent_prices else cfg.initial_capital
                    self._close_trade(risk_mgr, current_position,
                                      last_price, "shutdown", "shutdown", result)
                else:
                    # v0.20.0: Try to close position on exchange before shutdown
                    if exchange is not None:
                        try:
                            side = 'sell' if current_position.direction == "LONG" else 'buy'
                            await exchange.create_order(
                                cfg.symbol, 'market', side, current_position.size
                            )
                            console.print(f"[yellow]Emergency closed {current_position.direction} position on exchange[/yellow]")
                        except Exception as e:
                            console.print(f"[bold red]FAILED to close position on exchange: {e}[/bold red]")
                            console.print("[bold yellow]WARNING: Open position exists! Close manually on the exchange.[/bold yellow]")
                    else:
                        console.print("[bold yellow]WARNING: Open position exists! Close manually on the exchange.[/bold yellow]")

            # Compute final statistics
            duration = time.time() - start_time
            result.duration_seconds = duration
            result.final_capital = money_mgr.total_value
            result.total_pnl = money_mgr.realized_pnl
            result.total_pnl_pct = result.total_pnl / cfg.initial_capital * 100 if cfg.initial_capital > 0 else 0

            # v0.20.0: Show MoneyManager portfolio summary on exit
            try:
                summary = money_mgr.get_portfolio_summary()
                console.print(f"\n[bold]Portfolio Summary:[/bold]")
                console.print(f"  Total Value: ${summary['total_value']:,.2f}")
                console.print(f"  Cash: ${summary['cash']:,.2f}")
                console.print(f"  Exposure: {summary['exposure_pct']:.1%}")
                console.print(f"  Leverage: {summary['leverage_ratio']:.2f}x")
                console.print(f"  Drawdown: {summary['current_drawdown']:.1%}")
                console.print(f"  Kill Switch: {'ACTIVE' if summary['kill_switch_active'] else 'OFF'}")
                console.print(f"  Circuit Breakers: {summary['circuit_breakers']}")
            except Exception:
                pass

            if result.total_trades > 0:
                result.win_rate = result.winning_trades / result.total_trades
                if result.equity_curve:
                    peak = result.equity_curve[0]
                    max_dd = 0.0
                    for eq in result.equity_curve:
                        if eq > peak:
                            peak = eq
                        dd = (peak - eq) / peak
                        if dd > max_dd:
                            max_dd = dd
                    result.max_drawdown = max_dd

            # Update terminal state with final values
            self._update_terminal_state(
                is_running=False,
                portfolio_value=money_mgr.total_value,
                cash=money_mgr.cash,
                unrealized_pnl=money_mgr.unrealized_pnl,
                realized_pnl=money_mgr.realized_pnl,
                total_trades=result.total_trades,
                winning_trades=result.winning_trades,
                win_rate=result.win_rate,
                max_drawdown=result.max_drawdown,
                websocket_status="stopped",
            )

            storage.close()

        return result


def _update_live_display(
    live_display,
    cfg,
    result: RealtimeResult,
    position_state: PositionState,
    current_position,
    current_regime: str,
    recent_prices: list,
    risk_mgr: RiskManager,
    stream_buf: Optional[StreamingPatternBuffer] = None,
) -> None:
    """Update the Rich Live display panel during live trading.

    v0.13.0: Enhanced with StreamingPatternBuffer info, entropy, and SL/TP display.
    """
    current_price = recent_prices[-1] if recent_prices else 0
    pos_str = f"[green]{position_state.value}[/green]" if position_state != PositionState.FLAT else "FLAT"
    pnl_pct = (risk_mgr.capital - cfg.initial_capital) / cfg.initial_capital * 100 if cfg.initial_capital > 0 else 0
    pnl_color = "green" if pnl_pct >= 0 else "red"
    pnl_sign = "+" if pnl_pct >= 0 else ""

    # Position details
    direction_str = ""
    sl_tp_str = ""
    if current_position is not None:
        direction_str = f" ({current_position.direction})"
        sl_val = getattr(current_position, 'sl_price', None)
        tp_val = getattr(current_position, 'tp_price', None)
        if sl_val and tp_val and current_price > 0:
            sl_dist = abs(current_price - sl_val) / current_price * 100
            tp_dist = abs(tp_val - current_price) / current_price * 100
            sl_tp_str = f" | SL: ${sl_val:,.0f} (-{sl_dist:.1f}%) TP: ${tp_val:,.0f} (+{tp_dist:.1f}%)"

    # Buffer info
    buf_str = ""
    if stream_buf is not None:
        pat = " -> ".join(stream_buf.get_pattern()) if stream_buf.has_pattern() else "..."
        buf_str = f" | Pattern: [{pat}] | Entropy: {stream_buf.entropy:.1f}b"

    live_display.update(Panel(
        f"  Price: ${current_price:,.2f} | "
        f"Position: {pos_str}{direction_str}{sl_tp_str}\n"
        f"  P&L: [{pnl_color}]{pnl_sign}{pnl_pct:.2f}%[/{pnl_color}] | "
        f"Regime: {current_regime} | "
        f"Candles: {result.candles_processed} | "
        f"Signals: {result.signals_generated} | "
        f"Trades: {result.total_trades}{buf_str}",
        title=f"PPMT Live: {cfg.symbol} ({'DRY RUN' if getattr(cfg, 'dry_run', True) else 'LIVE'})",
        border_style="cyan",
    ))


def format_realtime_result(result: RealtimeResult) -> str:
    """Format a RealtimeResult as a Rich panel summary."""
    pnl_color = "green" if result.total_pnl >= 0 else "red"
    pnl_sign = "+" if result.total_pnl >= 0 else ""

    lines = []
    lines.append(f"[bold]Real-Time Trading Results: {result.symbol} ({result.timeframe})[/bold]")
    lines.append(f"  Mode: {result.mode.upper()}")
    lines.append("")
    lines.append(f"  Capital:  ${result.initial_capital:,.2f} -> ${result.final_capital:,.2f}  [{pnl_color}]{pnl_sign}{result.total_pnl_pct:.2f}%[/{pnl_color}]")
    lines.append(f"  P&L:      [{pnl_color}]${result.total_pnl:+,.2f}[/{pnl_color}]")
    lines.append("")
    lines.append(f"  Trades:   {result.total_trades}  (W:{result.winning_trades} L:{result.losing_trades})")
    if result.total_trades > 0:
        lines.append(f"  Win Rate: {result.win_rate:.1%}")
    lines.append(f"  Max DD:   {result.max_drawdown:.1%}")
    lines.append("")
    lines.append(f"  Candles Processed: {result.candles_processed}")
    lines.append(f"  SAX Symbols:       {result.sax_symbols_produced}")
    lines.append(f"  Signals Generated: {result.signals_generated}")
    lines.append(f"  Duration:          {result.duration_seconds:.1f}s")

    return "\n".join(lines)


# ============================================================
# v0.13.0: LIVING TRIE & RECALIBRATION
# ============================================================

def _living_trie_update(
    stream_buf: StreamingPatternBuffer,
    trie,
    ppmt_engine,
    cfg,
    current_regime: str,
) -> None:
    """
    Update the Living Trie with recent pattern observations.

    The Living Trie adapts to market changes by inserting new pattern
    observations as they occur in real-time. This keeps the Trie
    synchronized with current market dynamics without full rebuilds.

    Only updates every N symbols to avoid excessive writes.
    """
    # Only update every pattern_length symbols (1 update per full pattern cycle)
    if stream_buf.symbols_produced % cfg.pattern_length != 0:
        return

    observations = stream_buf.get_recent_observations(n=5)
    if not observations:
        return

    for obs in observations[-1:]:  # Only insert the most recent
        symbols = obs["symbols"]
        if len(symbols) == cfg.pattern_length:
            try:
                trie.insert_with_observations(
                    symbols=symbols,
                    direction=None,  # Unknown at insertion time
                    move_pct=0.0,    # Will be updated on exit
                    regime=current_regime,
                )
            except Exception:
                pass  # Non-critical — Living Trie is best-effort

    # Periodically re-propagate metadata (every 50 updates)
    if stream_buf.symbols_produced % (cfg.pattern_length * 50) == 0:
        try:
            trie.propagate_metadata()
        except Exception:
            pass


def _recalibrate(sax_encoder, cfg, storage, info) -> None:
    """
    Periodically re-calibrate SAX parameters based on accumulated data.

    Uses the TradingCalibrationEngine to find optimal α/W for the
    current market regime. Only updates if new parameters are
    significantly different from current ones.
    """
    try:
        df = storage.load_ohlcv(cfg.symbol, cfg.timeframe)
        if df.empty or len(df) < 500:
            return

        calibrator = TradingCalibrationEngine(
            train_ratio=0.70,
            pattern_length=cfg.pattern_length,
            timeframe=cfg.timeframe,
        )
        cal_profile, cal_results = calibrator.calibrate(df, symbol=cfg.symbol, verbose=False)

        new_alpha = cal_profile.sax_alphabet_size
        new_window = cal_profile.sax_window_size

        # Only update if parameters changed significantly
        if new_alpha != cfg.sax_alphabet_size or new_window != cfg.sax_window_size:
            old_alpha, old_window = cfg.sax_alphabet_size, cfg.sax_window_size
            cfg.sax_alphabet_size = new_alpha
            cfg.sax_window_size = new_window

            # Update SAX encoder parameters
            sax_encoder.alphabet_size = new_alpha
            sax_encoder.window_size = new_window
            # Reset normalization stats for new params (v0.22.0: fixed attr name)
            sax_encoder._running_paa_mean = None
            sax_encoder._running_paa_std = None
            sax_encoder._running_paa_values = []

            console.print(f"[bold yellow]Recalibrated:[/bold yellow] "
                          f"alpha={old_alpha}->{new_alpha}, window={old_window}->{new_window}")
    except Exception as e:
        # Recalibration failure is non-critical
        console.print(f"[dim]Recalibration skipped: {e}[/dim]")
