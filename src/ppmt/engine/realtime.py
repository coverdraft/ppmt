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


console = Console()


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
    min_confidence: float = 0.20
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


@dataclass
class LiveConfig:
    """Configuration for live mode (requires ccxt)."""
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
    min_confidence: float = 0.20
    catastrophic_loss_pct: float = 0.0
    """Catastrophic loss threshold. 0.0 = use TokenProfile value."""
    regime_aware: bool = True
    use_token_profile: bool = True
    """v0.11.0: Use TokenProfile for automatic parameter selection."""
    auto_calibrate: bool = True
    """v0.11.0: Auto-calibrate SAX α/W using TradingCalibrationEngine."""
    recalibration_interval: int = 2000
    """v0.11.0: Re-calibrate every N candles in live mode. Default 2000."""
    use_multi_level: bool = True
    """v0.11.0: Enable 4-level matching."""
    living_trie: bool = True
    """v0.11.0: Enable Living Trie updates in live mode."""
    testnet: bool = True
    """Use exchange testnet (paper trading on exchange)."""
    dry_run: bool = True
    """If True, process signals but don't actually execute orders."""


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
            max_daily_loss_pct=0.10,
            max_drawdown_pct=0.80,
        )

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
        """Compute current ATR as percentage of price."""
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

        # Wilder's smoothing for last value
        atr_val = 0.0
        if len(tr) >= period:
            atr_val = np.mean(tr[-period:])

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
                new_symbols, sax_buffer = sax_encoder.encode_incremental(single_candle, sax_buffer)

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

                        # Cooldown check
                        sym_idx = result.sax_symbols_produced
                        if (current_position is None and
                                sym_idx - last_losing_trade_idx < 1 and
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

                        # v0.11.0: Lower move threshold for alpha=3 (same as PaperTrader)
                        if (position_state == PositionState.FLAT
                                and prediction.direction != "FLAT"
                                and weighted_confidence >= effective_min_conf
                                and abs(prediction.expected_total_move_pct) > 0.3
                                and prediction.overall_probability > 0.20):

                            result.signals_generated += 1

                            # v0.11.0: Prediction-aware SL/TP (same as PaperTrader)
                            expected_move_abs = abs(prediction.expected_total_move_pct)

                            if prediction.direction == "LONG":
                                sl_distance_pct = max(min(expected_move_abs * 1.5, 5.0), 0.5)
                                tp_distance_pct = expected_move_abs * 2.5
                                if tp_distance_pct < sl_distance_pct * 1.5:
                                    tp_distance_pct = sl_distance_pct * 1.5
                            else:  # SHORT
                                sl_distance_pct = max(min(expected_move_abs * 1.5, 5.0), 0.5)
                                tp_distance_pct = expected_move_abs * 2.5
                                if tp_distance_pct < sl_distance_pct * 1.5:
                                    tp_distance_pct = sl_distance_pct * 1.5

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

                # Record equity
                if candle_idx % 10 == 0:
                    unrealized = risk_mgr.capital
                    result.equity_curve.append(unrealized)
                    result.capital_history.append(unrealized)
                    if risk_mgr.capital > peak_capital:
                        peak_capital = risk_mgr.capital

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

    async def run_live(self) -> RealtimeResult:
        """
        Run live mode: connect to exchange and process candles in real-time.

        Requires ccxt (Python 3.10+). Uses the exchange's WebSocket or
        REST polling to receive new candles and process them through
        the PPMT pipeline.

        Returns:
            RealtimeResult with trading statistics
        """
        if not isinstance(self.config, LiveConfig):
            raise ValueError("run_live() requires LiveConfig")

        cfg = self.config

        try:
            import ccxt.async_support as ccxt_async
        except ImportError:
            console.print("[red]ccxt is required for live trading. Install with: pip install ccxt>=4.0.0[/red]")
            console.print("[dim]Note: ccxt requires Python 3.10+[/dim]")
            return RealtimeResult(mode="live", symbol=cfg.symbol, timeframe=cfg.timeframe)

        storage = PPMTStorage()

        # Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(cfg.symbol)

        # v0.11.0: TokenProfile + auto-calibration + persistence
        token_profile, cfg = self._setup_token_profile(cfg, info, storage)

        # Load tries (v0.11.0: all 4 levels)
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
        pred_engine = PredictionEngine(trie, prediction_depth=cfg.pattern_length)
        risk_mgr = RiskManager(capital=cfg.initial_capital, config=self.risk_config)

        # v0.11.0: FuzzyMatcher
        fuzzy_threshold = token_profile.fuzzy_threshold if token_profile else 0.80
        fuzzy_matcher = FuzzyMatcher(
            sax_encoder=sax_encoder,
            threshold=fuzzy_threshold,
            max_edit_distance=2,
        )

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

        # Connect to exchange
        exchange_class = getattr(ccxt_async, cfg.exchange, None)
        if exchange_class is None:
            console.print(f"[red]Exchange '{cfg.exchange}' not found in ccxt[/red]")
            storage.close()
            return RealtimeResult(mode="live", symbol=cfg.symbol, timeframe=cfg.timeframe)

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
            console.print(f"[yellow]Using {cfg.exchange} TESTNET[/yellow]")

        try:
            await exchange.load_markets()
            console.print(f"[green]Connected to {cfg.exchange}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to connect to {cfg.exchange}: {e}[/red]")
            await exchange.close()
            storage.close()
            return RealtimeResult(mode="live", symbol=cfg.symbol, timeframe=cfg.timeframe)

        # Timeframe to milliseconds
        tf_ms = {
            "1m": 60_000, "5m": 300_000, "15m": 900_000,
            "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
        }.get(cfg.timeframe, 3_600_000)

        # State
        result = RealtimeResult(
            mode="live",
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            initial_capital=cfg.initial_capital,
            final_capital=cfg.initial_capital,
        )
        sax_buffer = []
        pattern_buffer = []
        position_state = PositionState.FLAT
        current_position = None
        trade_counter = 0
        peak_capital = cfg.initial_capital

        # Regime detection
        regime_detector = None
        current_regime = "ranging"
        regime_info = None
        if cfg.regime_aware:
            regime_detector = RegimeDetector()

        # ATR tracking
        recent_prices = []
        recent_highs = []
        recent_lows = []

        # Track last candle timestamp to detect new candles
        last_candle_ts = 0

        console.print(f"\n[bold cyan]Starting Live Trading: {cfg.symbol} ({cfg.timeframe})[/bold cyan]")
        console.print(f"  Exchange: {cfg.exchange} ({'TESTNET' if cfg.testnet else 'MAINNET'})")
        console.print(f"  Dry Run: {'YES' if cfg.dry_run else 'NO - REAL ORDERS'}")
        console.print(f"  Capital: ${cfg.initial_capital:,.2f}")
        console.print(f"  Mode: {'PAPER (no execution)' if cfg.dry_run else 'LIVE (real execution)'}")
        console.print()

        try:
            with Live(console=console, refresh_per_second=2) as live_display:
                while True:
                    try:
                        # Fetch latest OHLCV
                        ohlcv = await exchange.fetch_ohlcv(cfg.symbol, cfg.timeframe, limit=1)

                        if ohlcv:
                            candle = ohlcv[-1]
                            candle_ts = candle[0]
                            current_price = candle[4]  # Close price
                            current_high = candle[2]
                            current_low = candle[3]

                            # Only process if this is a new candle
                            if candle_ts != last_candle_ts:
                                last_candle_ts = candle_ts

                                # Update ATR tracking
                                recent_prices.append(current_price)
                                recent_highs.append(current_high)
                                recent_lows.append(current_low)
                                if len(recent_prices) > 50:
                                    recent_prices = recent_prices[-50:]
                                    recent_highs = recent_highs[-50:]
                                    recent_lows = recent_lows[-50:]

                                # Incremental SAX encoding
                                # Create a single-row DataFrame for the encoder
                                single_df = pd.DataFrame([{
                                    'open': candle[1], 'high': candle[2],
                                    'low': candle[3], 'close': candle[4],
                                    'volume': candle[5]
                                }])

                                new_symbols, sax_buffer = sax_encoder.encode_incremental(single_df, sax_buffer)

                                if new_symbols:
                                    for sym in new_symbols:
                                        pattern_buffer.append(sym)
                                        result.sax_symbols_produced += 1
                                        if len(pattern_buffer) > cfg.pattern_length * 2:
                                            pattern_buffer = pattern_buffer[-(cfg.pattern_length * 2):]

                                    # Regime detection
                                    if regime_detector and len(recent_prices) >= 50:
                                        regime_info = regime_detector.detect_detailed(np.array(recent_prices))
                                        current_regime = regime_info.regime

                                    # Position management (SL/TP checks)
                                    if position_state != PositionState.FLAT and current_position:
                                        pos = risk_mgr._positions.get(cfg.symbol)
                                        if pos:
                                            sl_hit = risk_mgr.check_stop_loss(cfg.symbol, current_price)
                                            tp_hit = risk_mgr.check_take_profit(cfg.symbol, current_price)

                                            if sl_hit or tp_hit:
                                                reason = ("stop_loss" if sl_hit else "take_profit")
                                                self._close_trade(risk_mgr, current_position,
                                                                  current_price, str(candle_ts),
                                                                  reason, result)
                                                position_state = PositionState.FLAT
                                                current_position = None

                                    # v0.11.0: Signal generation with TokenProfile + 4-level matching
                                    if (len(pattern_buffer) >= cfg.pattern_length
                                            and position_state == PositionState.FLAT):
                                        current_symbols = pattern_buffer[-cfg.pattern_length:]
                                        prediction = pred_engine.predict(
                                            current_symbols=current_symbols,
                                            entry_price=current_price,
                                            timeframe_hours=1.0,
                                            symbol=cfg.symbol,
                                        )

                                        if prediction.direction == "FLAT" or prediction.confidence <= 0:
                                            pass  # Skip, no signal
                                        else:
                                            # v0.11.0: 4-level matching
                                            weighted_confidence = prediction.confidence
                                            if ppmt_engine is not None:
                                                ppmt_result = ppmt_engine.match_raw(
                                                    current_symbols=current_symbols,
                                                    current_price=current_price,
                                                )
                                                weighted_confidence = ppmt_result.weighted_confidence
                                                if weighted_confidence <= 0 and prediction.confidence > 0:
                                                    weighted_confidence = prediction.confidence

                                            effective_min_conf = cfg.min_confidence

                                            # v0.11.0: TokenProfile SHORT gating
                                            if prediction.direction == "SHORT":
                                                if token_profile is not None and not token_profile.short_allowed:
                                                    pass  # Skip SHORT
                                                else:
                                                    short_regime_mult = {
                                                        "trending_down": 0.85, "ranging": 1.1,
                                                        "trending_up": 1.5, "volatile": 1.8,
                                                    }.get(current_regime, 1.2)
                                                    effective_min_conf = max(effective_min_conf * short_regime_mult, 0.20)
                                                    if token_profile is not None:
                                                        effective_min_conf = max(
                                                            effective_min_conf * token_profile.short_confidence_multiplier,
                                                            effective_min_conf,
                                                        )

                                            if (prediction.direction != "FLAT"
                                                    and weighted_confidence >= effective_min_conf
                                                    and abs(prediction.expected_total_move_pct) > 0.3
                                                    and prediction.overall_probability > 0.20):

                                                result.signals_generated += 1

                                                # v0.11.0: Prediction-aware SL/TP
                                                expected_move_abs = abs(prediction.expected_total_move_pct)
                                                sl_distance_pct = max(min(expected_move_abs * 1.5, 5.0), 0.5)
                                                tp_distance_pct = expected_move_abs * 2.5
                                                if tp_distance_pct < sl_distance_pct * 1.5:
                                                    tp_distance_pct = sl_distance_pct * 1.5

                                                sl_price = current_price * (1 - sl_distance_pct / 100) if prediction.direction == "LONG" else current_price * (1 + sl_distance_pct / 100)
                                                tp_price = current_price * (1 + tp_distance_pct / 100) if prediction.direction == "LONG" else current_price * (1 - tp_distance_pct / 100)

                                                # v0.11.0: Get actual historical_count
                                                actual_historical_count = 10
                                                try:
                                                    matched_node = trie.search(current_symbols)
                                                    if matched_node and matched_node.metadata.historical_count > 0:
                                                        actual_historical_count = matched_node.metadata.historical_count
                                                except Exception:
                                                    pass

                                                signal_type = (SignalType.ENTRY_LONG
                                                               if prediction.direction == "LONG"
                                                               else SignalType.ENTRY_SHORT)

                                                signal = Signal(
                                                    signal_type=signal_type,
                                                    confidence=weighted_confidence,
                                                    symbol=cfg.symbol,
                                                    entry_price=current_price,
                                                    sl_price=sl_price,
                                                    tp_price=tp_price,
                                                    expected_move_pct=prediction.expected_total_move_pct,
                                                    risk_reward_ratio=tp_distance_pct / sl_distance_pct if sl_distance_pct > 0 else 0,
                                                    win_rate=prediction.overall_probability,
                                                    historical_count=actual_historical_count,
                                                    matched_pattern=current_symbols,
                                                )
                                                signal.quality_score = signal.compute_quality_score()
                                                signal.sizing_multiplier = signal.compute_sizing_multiplier()

                                                mock_meta = BlockLifecycleMetadata(
                                                    win_rate=signal.win_rate,
                                                    expected_move_pct=signal.expected_move_pct,
                                                    max_drawdown_pct=-sl_distance_pct,
                                                    historical_count=actual_historical_count,
                                                )
                                                signal.probability_of_success = mock_meta.probability_of_success
                                                signal.expected_profit_ahead = mock_meta.expected_profit_ahead
                                                signal.metadata_sizing_signal = mock_meta.sizing_signal

                                                if cfg.regime_aware and current_regime:
                                                    regime_mults = {"trending_up": 1.2, "ranging": 1.0,
                                                                    "trending_down": 0.6, "volatile": 0.4}
                                                    rm = regime_mults.get(current_regime, 1.0)
                                                    signal.metadata_sizing_signal *= rm
                                                    signal.sizing_multiplier *= rm

                                                can_open, reason = risk_mgr.can_open(signal, info.asset_class)
                                                if can_open:
                                                    size = risk_mgr.calculate_position_size(signal)

                                                    if not cfg.dry_run:
                                                        # Execute real order on exchange
                                                        try:
                                                            side = 'buy' if prediction.direction == "LONG" else 'sell'
                                                            order = await exchange.create_order(
                                                                cfg.symbol, 'market', side, size
                                                            )
                                                            console.print(f"[green]Order executed: {side} {size} {cfg.symbol}[/green]")
                                                        except Exception as e:
                                                            console.print(f"[red]Order failed: {e}[/red]")
                                                            continue

                                                    risk_mgr.open_position(signal, size)
                                                    current_position = RealtimeTrade(
                                                        trade_id=trade_counter + 1,
                                                        symbol=cfg.symbol,
                                                        direction=signal.direction or "LONG",
                                                        entry_price=current_price,
                                                        entry_time=str(candle_ts),
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

                                result.candles_processed += 1

                                # Update display
                                pos_str = f"[green]{position_state.value}[/green]" if position_state != PositionState.FLAT else "FLAT"
                                pnl_pct = (risk_mgr.capital - cfg.initial_capital) / cfg.initial_capital * 100
                                pnl_color = "green" if pnl_pct >= 0 else "red"
                                pnl_sign = "+" if pnl_pct >= 0 else ""

                                live_display.update(Panel(
                                    f"  Price: ${current_price:,.2f} | "
                                    f"Position: {pos_str} | "
                                    f"P&L: [{pnl_color}]{pnl_sign}{pnl_pct:.2f}%[/{pnl_color}] | "
                                    f"Regime: {current_regime} | "
                                    f"Trades: {result.total_trades}",
                                    title=f"PPMT Live: {cfg.symbol} ({'DRY RUN' if cfg.dry_run else 'LIVE'})",
                                    border_style="cyan",
                                ))

                        # Poll interval: check every 30 seconds
                        await asyncio.sleep(30)

                    except KeyboardInterrupt:
                        console.print("\n[yellow]Interrupted by user. Shutting down...[/yellow]")
                        break
                    except Exception as e:
                        console.print(f"[red]Error in live loop: {e}[/red]")
                        await asyncio.sleep(10)

        finally:
            # Cleanup
            await exchange.close()

            # Close any open position
            if current_position is not None:
                # In live mode, don't auto-close on shutdown (might be real money)
                if cfg.dry_run:
                    self._close_trade(risk_mgr, current_position,
                                      risk_mgr.capital, "shutdown", "shutdown", result)
                else:
                    console.print("[yellow]WARNING: Open position exists! Close manually on the exchange.[/yellow]")

            result.final_capital = risk_mgr.capital
            result.total_pnl = risk_mgr.capital - cfg.initial_capital
            result.total_pnl_pct = result.total_pnl / cfg.initial_capital * 100 if cfg.initial_capital > 0 else 0

            storage.close()

        return result


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
