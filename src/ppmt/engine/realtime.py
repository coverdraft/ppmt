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
from rich.markup import escape as _rich_escape

import numpy as np
import pandas as pd

from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.data.classifier import AssetClassifier
from ppmt.core.trie import PPMTTrie
from ppmt.core.sax import SAXEncoder
from ppmt.core.regime import RegimeDetector, RegimeInfo
from ppmt.core.profiles import TokenProfile, TIMEFRAME_ALPHA_DEFAULTS, TradingCalibrationEngine
from ppmt.core.matcher import FuzzyMatcher
from ppmt.core.thresholds import SignalThresholds
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.signal import SignalType, Signal
from ppmt.core.metadata import BlockLifecycleMetadata
from ppmt.risk.manager import RiskManager, RiskConfig
from ppmt.risk.money_manager import MoneyManager, MoneyManagerConfig
from ppmt.risk.position_sizing import AdvancedPositionSizer
from ppmt.engine.buffer import StreamingPatternBuffer
from ppmt.engine.divergence_monitor import PatternDivergenceMonitor
from ppmt.engine.btc_filter import BTCContextFilter

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
    on_position: Optional[Callable] = None
    """v0.39.0: Callback fired when a position opens OR closes.
    Receives a dict with: {action: 'open'|'close', symbol, direction,
    entry_price, entry_time, sl_price, tp_price, size, confidence,
    exit_price (only on close), exit_time (only on close),
    pnl_pct (only on close), exit_reason (only on close)}.
    Used by the dashboard server to update _multi_sessions[node_id]
    so /api/multi-status can report the open position to the chart
    for entry/exit price-line overlay."""
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
    exchange: str = "mexc"
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
    """Save Living Trie to DB every N candles. 0 = only on shutdown."""
    use_websocket: bool = False
    """v0.38.6: Use WebSocket feed (True) or REST polling (False).
    Default False because Binance WS limits to 5 connections per IP per 5min,
    so launching 20+ parallel tokens via Start All gets most handshakes rejected.
    REST polling via ccxt uses ephemeral connections and handles rate limits
    automatically, making it reliable for multi-token paper trading."""
    on_signal: Optional[Callable] = None
    """v0.39.0: Same as ReplayConfig.on_signal — fired when a signal generates."""
    on_trade: Optional[Callable] = None
    """v0.39.0: Same as ReplayConfig.on_trade — fired when a trade closes."""
    on_candle: Optional[Callable] = None
    """v0.39.0: Same as ReplayConfig.on_candle — fired for each candle."""
    on_position: Optional[Callable] = None
    """v0.39.0: Fired when a position opens OR closes in live mode.
    Payload: {action, symbol, direction, entry_price, entry_time, sl_price,
    tp_price, size, confidence, exit_price?, exit_time?, pnl_pct?, exit_reason?}.
    The dashboard server hooks this to update _multi_sessions[node_id]
    so /api/multi-status can report the open position for chart overlay."""


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


# v0.40.33: Direct HTTP polling using requests+asyncio.to_thread.
# v0.40.32 used aiohttp but it timed out silently on some networks
# (asyncio.TimeoutError has empty str repr → "Failed to connect: ").
# requests uses the same network stack as the working /api/market/price
# endpoint (ccxt sync + requests), so behavior is now consistent.
class _DirectPollExchange:
    """Lightweight exchange wrapper using requests + asyncio.to_thread.

    Implements only the methods the engine actually uses:
    - fetch_ticker(symbol) -> {last, close, ...}
    - fetch_ohlcv(symbol, timeframe, limit) -> [[ts, o, h, l, c, v], ...]
    - close() -> no-op (requests creates a new session per call, no cleanup needed)

    Supports: mexc, binance, bybit (spot). All HTTP calls go through the
    `requests` library wrapped in asyncio.to_thread, so we get the same
    network behavior as sync ccxt calls (which work on the user's Mac).
    """

    _BASE_URLS = {
        "mexc": "https://api.mexc.com",
        "binance": "https://api.binance.com",
        "bybit": "https://api.bybit.com",
    }

    _TIMEFRAME_MAP = {
        # CCXT-style -> exchange-native
        "mexc": {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1H", "4h": "4H", "1d": "1D",
        },
        "binance": {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1h", "4h": "4h", "1d": "1d",
        },
        "bybit": {
            "1m": "1", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "4h": "240", "1d": "D",
        },
    }

    def __init__(self, exchange_name: str, api_key: str = None, api_secret: str = None):
        import requests  # noqa: F401 — sanity check that requests is installed
        self.exchange_name = exchange_name.lower()
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = self._BASE_URLS.get(self.exchange_name)
        if not self.base_url:
            raise ValueError(f"_DirectPollExchange: unsupported exchange '{exchange_name}'")
        # ccxt-compat fields
        self.markets = {}
        self.markets_by_id = {}
        # Shared requests session for connection pooling
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "ppmt/0.40.33"})

    def _sync_fetch_ticker(self, symbol: str) -> dict:
        """Sync impl — runs in thread via to_thread."""
        symbol_id = symbol.replace("/", "").upper()
        if self.exchange_name in ("mexc", "binance"):
            url = f"{self.base_url}/api/v3/ticker/price"
            params = {"symbol": symbol_id}
        elif self.exchange_name == "bybit":
            url = f"{self.base_url}/v5/market/tickers"
            params = {"category": "spot", "symbol": symbol_id}
        else:
            raise ValueError(f"fetch_ticker not implemented for {self.exchange_name}")
        resp = self._session.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"{self.exchange_name} fetch_ticker HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if self.exchange_name in ("mexc", "binance"):
            price = float(data.get("price", 0))
            return {"symbol": symbol, "last": price, "close": price, "info": data}
        elif self.exchange_name == "bybit":
            tickers = data.get("result", {}).get("list", [])
            if not tickers:
                raise RuntimeError(f"bybit fetch_ticker: no data for {symbol}")
            price = float(tickers[0].get("lastPrice", 0))
            return {"symbol": symbol, "last": price, "close": price, "info": data}

    async def fetch_ticker(self, symbol: str) -> dict:
        """Async wrapper around sync impl via asyncio.to_thread."""
        import asyncio
        return await asyncio.to_thread(self._sync_fetch_ticker, symbol)

    def _sync_fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        """Sync impl — runs in thread via to_thread."""
        symbol_id = symbol.replace("/", "").upper()
        tf_native = self._TIMEFRAME_MAP.get(self.exchange_name, {}).get(timeframe, timeframe)
        if self.exchange_name in ("mexc", "binance"):
            url = f"{self.base_url}/api/v3/klines"
            params = {"symbol": symbol_id, "interval": tf_native, "limit": limit}
        elif self.exchange_name == "bybit":
            url = f"{self.base_url}/v5/market/kline"
            params = {"category": "spot", "symbol": symbol_id, "interval": tf_native, "limit": limit}
        else:
            raise ValueError(f"fetch_ohlcv not implemented for {self.exchange_name}")
        resp = self._session.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"{self.exchange_name} fetch_ohlcv HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if self.exchange_name in ("mexc", "binance"):
            # [[ts, o, h, l, c, v, ...], ...]
            return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in data]
        elif self.exchange_name == "bybit":
            klines = data.get("result", {}).get("list", [])
            # Bybit returns newest-first, reverse to oldest-first
            klines = list(reversed(klines))
            return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in klines]

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        """Async wrapper around sync impl via asyncio.to_thread."""
        import asyncio
        return await asyncio.to_thread(self._sync_fetch_ohlcv, symbol, timeframe, limit)

    async def close(self):
        # No-op — requests.Session closes itself on GC. Kept for API compat
        # with the engine's `await poll_exchange.close()` calls.
        try:
            self._session.close()
        except Exception:
            pass





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

    def __init__(self, config=None, state_callback=None):
        """
        Args:
            config: ReplayConfig or LiveConfig. Defaults to ReplayConfig().
            state_callback: Optional callable that receives keyword args mirroring
                TerminalState.update_sync(). When provided, every state update is
                also forwarded to this callback — used by the multi-token launcher
                in server.py to keep per-session state in sync (last_price, pnl_pct,
                signals, trades, candles_processed, status).
                v0.36.2.
        """
        self.config = config or ReplayConfig()
        self._state_callback = state_callback
        # v0.38.3 FIX: Removed hardcoded RiskConfig that OVERRIDED the relaxed
        # defaults in risk/manager.py (min_quality_score=0.03, min_risk_reward=0.5
        # set in v0.38.1). The hardcoded values (min_quality_score=0.10,
        # min_risk_reward=1.0) were rejecting 95%+ of signals in backtest →
        # INSUFFICIENT_DATA verdicts. Now uses RiskConfig() defaults which match
        # the v0.38.1 relaxed values.
        # Note: run_live() uses MoneyManager which builds its own RiskConfig from
        # MoneyManagerConfig, so this is mainly for run_replay() backtest path.
        self.risk_config = RiskConfig()

    def _update_terminal_state(self, **kwargs) -> None:
        """Push state update to TerminalState for dashboard consumption.

        Safe to call from synchronous code — uses update_sync().
        No-op if terminal module is not available.

        v0.36.2: Also forwards the same kwargs to the per-session state_callback
        (if provided) so multi-token sessions can track their own state.

        v0.39.1 FIX (Bug #7 cross-contamination of prices/signals between
        parallel sessions): when a state_callback is provided (multi-token
        mode via /api/multi-start), we SKIP the global _terminal_state
        singleton entirely. Previously all 22 parallel traders wrote to
        the SAME singleton, so ZIL's current_price was overwritten 50ms
        later by MANA's, then by SUSHI's, etc. The dashboard showed the
        last writer's price for whatever symbol was selected — leading
        to the "ZIL shows 0.0032 / 0.0116 / 0.2262" cross-contamination
        the user reported.
        Now: in multi-token mode the dashboard reads per-session state
        via /api/multi-status (which queries _multi_sessions directly),
        and the global singleton is left alone so it doesn't get
        polluted. In single-token mode (no state_callback) the singleton
        is still used as before.
        """
        # v0.39.1: In multi-token mode, skip the global singleton.
        if self._state_callback is None:
            # Single-token mode — update the global singleton as before.
            if _terminal_state is not None:
                try:
                    _terminal_state.update_sync(**kwargs)
                except Exception:
                    pass  # Never let dashboard updates crash the engine
            return

        # Multi-token mode — only forward to per-session callback.
        try:
            self._state_callback(**kwargs)
        except Exception:
            pass  # Never let callback errors crash the engine

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
        # v0.40.4 FIX-1D: pass asset_class so N1/N2 load from cross-asset pools.
        from ppmt.engine.ppmt import PPMT
        all_tries = storage.load_all_tries(cfg.symbol, asset_class=info.asset_class)
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
            # FIX-1D: contribute to universal N1 + class N2 pools
            engine.attach_storage(storage)
            engine.build(df, pattern_length=cfg.pattern_length)
            # Reload N1/N2 from cross-asset pools (engine.trie_n1/2 are empty
            # in storage mode)
            # v0.41.0 FIX-1B: DO NOT fall back to engine.trie_n1/n2 — those
            # are empty in storage mode and would mask a missing shared pool.
            # Empty pools correctly get 0 confidence, allowing weight
            # redistribution to N2/N3 via AdaptiveWeights.
            all_tries = storage.load_all_tries(cfg.symbol, asset_class=info.asset_class)
            trie_n1 = all_tries.get("n1")
            trie_n2 = all_tries.get("n2")
            if trie_n1 is None:
                console.print("[yellow]WARNING: N1 universal pool is empty after build. Only N2/N3/N4 available.[/yellow]")
                trie_n1 = PPMTTrie(name="universal_empty")
            if trie_n2 is None:
                console.print(f"[yellow]WARNING: N2 class pool for {info.asset_class} is empty after build.[/yellow]")
                trie_n2 = PPMTTrie(name=f"class_empty:{info.asset_class}")
            trie_n3 = engine.trie_n3
            trie_n4 = engine.trie_n4
            has_multi_level = True
        else:
            console.print(f"[green]Loaded N3 Trie for {cfg.symbol} ({trie_n3.pattern_count} patterns)[/green]")
            # v0.41.0 FIX-1B: Replace None N1/N2 with empty tries instead of
            # silently dropping to single-level mode.
            if trie_n1 is None:
                console.print("[yellow]WARNING: N1 universal pool not found. Using empty trie.[/yellow]")
                trie_n1 = PPMTTrie(name="universal_empty")
            if trie_n2 is None:
                console.print(f"[yellow]WARNING: N2 class pool for {info.asset_class} not found. Using empty trie.[/yellow]")
                trie_n2 = PPMTTrie(name=f"class_empty:{info.asset_class}")
            if has_multi_level:
                console.print(f"[green]All 4 levels loaded: N1={trie_n1.pattern_count}, "
                              f"N2={trie_n2.pattern_count}, N3={trie_n3.pattern_count}, "
                              f"N4={trie_n4.pattern_count}[/green]")
            else:
                console.print(f"[yellow]N1/N2 loaded (possibly empty). N4 unavailable — running 3-level mode.[/yellow]")

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
        # FIX-14 (v0.40.10): pass trie_n4 as regime_trie for regime-aware lookup
        pred_engine = PredictionEngine(
            trie,
            prediction_depth=cfg.pattern_length,
            regime_trie=trie_n4 if (has_multi_level and trie_n4 is not None) else None,
        )
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

        # v0.46.0: Pattern Divergence Monitor & BTC Context Filter
        divergence_monitor = PatternDivergenceMonitor(divergence_threshold=0.667)
        btc_filter = BTCContextFilter()

        # v0.47.0: Active learning — captures per-level patterns at entry
        # so outcomes can be written back to N1/N2/N3 tries on close.
        _active_learning = None  # dict with pattern_n1, pattern_n3, entry_price, direction, entry_ts
        _learning_n1_buffer = PPMTTrie(name="learning_n1_buffer")
        _learning_n2_buffer = PPMTTrie(name="learning_n2_buffer")

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

                    # v0.46.0: Update BTC context filter periodically
                    # If this IS BTC, feed its prices directly.
                    # For altcoins, BTC prices must come from a separate feed (future).
                    if result.candles_processed % 100 == 0 and len(atr_prices) >= 50:
                        if cfg.symbol == "BTC/USDT":
                            btc_filter.update_btc_context(btc_prices=np.array(atr_prices))

                    # === POSITION MANAGEMENT ===
                    if position_state != PositionState.FLAT and current_position is not None:
                        pos = risk_mgr._positions.get(cfg.symbol)
                        if pos is None:
                            position_state = PositionState.FLAT
                            current_position = None

                            # v0.40.34: Push position closure to state_callback

                            try:

                                self._update_terminal_state(open_position=None)

                            except Exception:

                                pass

                            # v0.40.34: Push position closure to state_callback

                            try:

                                self._update_terminal_state(open_position=None)

                            except Exception:

                                pass

                            # v0.40.34: Push position closure to state_callback

                            try:

                                self._update_terminal_state(open_position=None)

                            except Exception:

                                pass

                            # v0.40.34: Push position closure to state_callback

                            try:

                                self._update_terminal_state(open_position=None)

                            except Exception:

                                pass
                            continue

                        # Catastrophic loss check
                        catastrophic_close = False
                        if cfg.catastrophic_loss_pct > 0:
                            if pos.direction == "LONG":
                                unrealized_loss = (pos.entry_price - current_price) / pos.entry_price * 100
                            else:
                                unrealized_loss = (current_price - pos.entry_price) / pos.entry_price * 100
                            if unrealized_loss >= cfg.catastrophic_loss_pct:
                                # v0.47.0: Insert trade outcome into tries (learning loop)
                                _learning_insert_outcome(
                                    _active_learning, current_position, current_price,
                                    "catastrophic_stop", trie_n1, trie_n2, trie,
                                    _learning_n1_buffer, _learning_n2_buffer,
                                    regime=current_regime,
                                )
                                _active_learning = None
                                self._close_trade(risk_mgr, current_position, current_price,
                                                  current_time, "catastrophic_stop", result,
                                                  source="backtest")
                                catastrophic_close = True
                                position_state = PositionState.FLAT
                                current_position = None

                                # v0.40.34: Push position closure to state_callback

                                try:

                                    self._update_terminal_state(open_position=None)

                                except Exception:

                                    pass
                                consecutive_breaks = 0

                        if catastrophic_close:
                            continue

                        # v0.46.0: Pattern Divergence Exit
                        # Compare real SAX stream with expected_sequences from
                        # the node that generated the entry signal.
                        # v0.48.0 (FASE 2B FIX): Use N3-encoded tuples instead of
                        # pattern_buffer strings. expected_sequences stores tuples,
                        # so comparing strings vs tuples always showed "no divergence".
                        if divergence_monitor.expected_sequence is not None:
                            recent_symbols = None
                            if ppmt_engine is not None:
                                try:
                                    _n3_needed = ppmt_engine.sax_n3.window_size * 3
                                    if candle_idx >= _n3_needed:
                                        _div_df = df.iloc[candle_idx - _n3_needed + 1:candle_idx + 1]
                                        _n3_all = ppmt_engine._encode(ppmt_engine.sax_n3, _div_df)
                                        recent_symbols = _n3_all[-3:] if len(_n3_all) >= 3 else None
                                except Exception:
                                    pass
                            if recent_symbols is None and len(pattern_buffer) >= 3:
                                recent_symbols = pattern_buffer[-3:]  # fallback: strings (old behavior)
                            if recent_symbols is not None:
                                div_result = divergence_monitor.check_divergence(recent_symbols)
                            else:
                                div_result = {'diverged': False}
                            if div_result['diverged']:
                                # v0.47.0: Insert trade outcome into tries (learning loop)
                                _learning_insert_outcome(
                                    _active_learning, current_position, current_price,
                                    "pattern_broken", trie_n1, trie_n2, trie,
                                    _learning_n1_buffer, _learning_n2_buffer,
                                    regime=current_regime,
                                )
                                _active_learning = None
                                self._close_trade(
                                    risk_mgr, current_position, current_price,
                                    current_time, "pattern_broken", result,
                                    source="backtest",
                                )
                                position_state = PositionState.FLAT
                                current_position = None
                                consecutive_breaks = 0
                                try:
                                    self._update_terminal_state(open_position=None)
                                except Exception:
                                    pass
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
                            # v0.47.0: Insert trade outcome into tries (learning loop)
                            _learning_insert_outcome(
                                _active_learning, current_position, current_price,
                                exit_reason, trie_n1, trie_n2, trie,
                                _learning_n1_buffer, _learning_n2_buffer,
                                regime=current_regime,
                            )
                            _active_learning = None
                            self._close_trade(risk_mgr, current_position, current_price,
                                              current_time, exit_reason, result,
                                              source="backtest")
                            position_state = PositionState.FLAT
                            current_position = None
                            consecutive_breaks = 0
                            continue

                        elif tp_hit:
                            # v0.47.0: Insert trade outcome into tries (learning loop)
                            _learning_insert_outcome(
                                _active_learning, current_position, current_price,
                                "take_profit", trie_n1, trie_n2, trie,
                                _learning_n1_buffer, _learning_n2_buffer,
                                regime=current_regime,
                            )
                            _active_learning = None
                            self._close_trade(risk_mgr, current_position, current_price,
                                              current_time, "take_profit", result,
                                              source="backtest")
                            position_state = PositionState.FLAT
                            current_position = None
                            consecutive_breaks = 0
                            continue

                    # === PATTERN MATCHING & SIGNAL GENERATION ===
                    # Only generate signals if we have enough symbols and are FLAT
                    if len(pattern_buffer) >= cfg.pattern_length:
                        current_symbols = pattern_buffer[-cfg.pattern_length:]

                        # Generate prediction
                        # FIX-14 (v0.40.10): pass current_regime so PredictionEngine
                        # routes lookups to the matching N4 sub-trie.
                        prediction = pred_engine.predict(
                            current_symbols=current_symbols,
                            entry_price=current_price,
                            timeframe_hours=tf_hours,
                            symbol=cfg.symbol,
                            current_regime=current_regime,
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
                            # v0.47.0: Pass recent_candles DataFrame so each level's
                            # encoder re-encodes with the correct symbol type.
                            # This fixes the string/tuple mismatch where N3/N4
                            # (SAXDualEncoder) expect tuples but received strings.
                            _recent_df = None
                            _w = cfg.sax_window_size
                            _n_needed = _w * cfg.pattern_length
                            if candle_idx >= _n_needed:
                                _recent_df = df.iloc[candle_idx - _n_needed + 1:candle_idx + 1]
                            ppmt_result = ppmt_engine.match_raw(
                                current_symbols=current_symbols,
                                current_price=current_price,
                                recent_candles=_recent_df,
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

                        # v0.46.0: BTC Context Filter
                        # Adjust confidence based on BTC market regime.
                        # Longs in BTC downtrend get penalized; shorts in BTC uptrend get penalized.
                        if btc_filter._btc_regime is not None and weighted_confidence > 0:
                            btc_result = btc_filter.filter_signal(
                                prediction.direction, weighted_confidence,
                            )
                            if btc_result['rejected']:
                                if result.candles_processed % 50 == 0:
                                    console.print(
                                        f"[dim][{cfg.symbol}] BTC filter rejected {prediction.direction}: "
                                        f"btc_regime={btc_result['btc_regime']} conf={weighted_confidence:.2f}[/dim]"
                                    )
                                continue
                            weighted_confidence = btc_result['adjusted_confidence']

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
                        # v0.38.8: All thresholds now sourced from SignalThresholds
                        # (core/thresholds.py). .paper() for validation_mode=True,
                        # .real() for real-money. Values preserved verbatim from v0.38.7.
                        _sig_thresholds = SignalThresholds.for_mode(
                            getattr(cfg, 'validation_mode', False)
                        )
                        move_threshold = _sig_thresholds.move_threshold
                        prob_threshold = _sig_thresholds.base_prob_gate  # final entry gate
                        base_prob_gate = _sig_thresholds.base_prob_gate
                        ranging_prob_gate = _sig_thresholds.ranging_prob_gate
                        volatile_prob_gate = _sig_thresholds.volatile_prob_gate
                        counter_trend_gate = _sig_thresholds.counter_trend_gate

                        # Confidence boost: if overall_probability is strong and move is
                        # significant, boost confidence up to min_confidence level
                        boosted_confidence = weighted_confidence
                        boost_prob_trigger = _sig_thresholds.boost_prob_trigger
                        boost_move_trigger = _sig_thresholds.boost_move_trigger
                        if (prediction.overall_probability >= boost_prob_trigger
                                and abs(prediction.expected_total_move_pct) >= boost_move_trigger):
                            # Strong pattern match — boost confidence by probability
                            boosted_confidence = max(
                                weighted_confidence,
                                weighted_confidence * (1 + prediction.overall_probability),
                            )

                        # v0.25.0: Hard quality gate — reject weak signals
                        # v0.38.3: Log the reason so user can see WHY signals don't fire.
                        if prediction.overall_probability < base_prob_gate:
                            if result.candles_processed % 20 == 0:
                                console.print(
                                    f"[dim][{cfg.symbol}] skip: prob={prediction.overall_probability:.2f} < {base_prob_gate} gate | regime={current_regime} | pattern={''.join(current_symbols)}[/dim]"
                                )
                            continue
                        # v0.38.5: Hard move floor — must be <= move_threshold so a signal
                        # that passes the floor also passes the final entry gate.
                        # Paper trading: 0.05% (just above Binance spread).
                        # Real money: 0.5% (only meaningful moves).
                        _hard_move_floor = _sig_thresholds.hard_move_floor
                        if abs(prediction.expected_total_move_pct) < _hard_move_floor:
                            if result.candles_processed % 20 == 0:
                                console.print(
                                    f"[dim][{cfg.symbol}] skip: move={prediction.expected_total_move_pct:.2f}% < {_hard_move_floor}% | regime={current_regime} | pattern={''.join(current_symbols)}[/dim]"
                                )
                            continue

                        # v0.25.0: Strict regime-aware signal filtering
                        # KEY INSIGHT from trade analysis:
                        #   - Ranging regime: 8 losses, 2 wins → AVOID
                        #   - Volatile regime: mixed but catastrophic losses possible
                        #   - Trending: best win rate, should be primary focus
                        if current_regime == "ranging":
                            # Ranging markets are choppy — skip unless very high confidence
                            if prediction.overall_probability < ranging_prob_gate:
                                if result.candles_processed % 20 == 0:
                                    console.print(
                                        f"[dim][{cfg.symbol}] skip: ranging prob={prediction.overall_probability:.2f} < {ranging_prob_gate}[/dim]"
                                    )
                                continue
                            _ranging_move_floor = _sig_thresholds.ranging_move_floor
                            if abs(prediction.expected_total_move_pct) < _ranging_move_floor:
                                if result.candles_processed % 20 == 0:
                                    console.print(
                                        f"[dim][{cfg.symbol}] skip: ranging move={prediction.expected_total_move_pct:.2f}% < {_ranging_move_floor}%[/dim]"
                                    )
                                continue
                        elif current_regime == "volatile":
                            # In volatile markets, only trade with the strongest signals
                            if prediction.overall_probability < volatile_prob_gate:
                                if result.candles_processed % 20 == 0:
                                    console.print(
                                        f"[dim][{cfg.symbol}] skip: volatile prob={prediction.overall_probability:.2f} < {volatile_prob_gate}[/dim]"
                                    )
                                continue
                            _volatile_move_floor = _sig_thresholds.volatile_move_floor
                            if abs(prediction.expected_total_move_pct) < _volatile_move_floor:
                                if result.candles_processed % 20 == 0:
                                    console.print(
                                        f"[dim][{cfg.symbol}] skip: volatile move={prediction.expected_total_move_pct:.2f}% < {_volatile_move_floor}%[/dim]"
                                    )
                                continue
                        elif current_regime == "trending_down" and prediction.direction == "LONG":
                            # Counter-trend LONG in downtrend — extremely risky
                            if prediction.overall_probability < counter_trend_gate:
                                if result.candles_processed % 20 == 0:
                                    console.print(
                                        f"[dim][{cfg.symbol}] skip: counter-trend LONG in downtrend prob={prediction.overall_probability:.2f} < {counter_trend_gate}[/dim]"
                                    )
                                continue
                        elif current_regime == "trending_up" and prediction.direction == "SHORT":
                            # Counter-trend SHORT in uptrend — extremely risky
                            if prediction.overall_probability < counter_trend_gate:
                                if result.candles_processed % 20 == 0:
                                    console.print(
                                        f"[dim][{cfg.symbol}] skip: counter-trend SHORT in uptrend prob={prediction.overall_probability:.2f} < {counter_trend_gate}[/dim]"
                                    )
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

                                # v0.46.0: Set expected sequence for divergence monitoring
                                # v0.48.0 (FASE 2B FIX): Use _active_learning patterns (tuples)
                                # instead of current_symbols (strings). N3 trie has tuple keys,
                                # so trie.search(current_strings) never found nodes.
                                try:
                                    if _active_learning is not None and _active_learning.get("pattern_n3"):
                                        matched_node = trie.search(_active_learning["pattern_n3"])
                                    else:
                                        matched_node = trie.search(current_symbols)
                                    if matched_node and matched_node.metadata.expected_sequences:
                                        divergence_monitor.set_expected(matched_node.metadata)
                                except Exception:
                                    pass

                                # v0.47.0: Capture per-level patterns for learning loop.
                                # When the position closes, the outcome (won/move_pct)
                                # will be written back to N1/N2/N3 tries.
                                if ppmt_engine is not None:
                                    try:
                                        _pl = cfg.pattern_length
                                        _w = cfg.sax_window_size
                                        _n_needed = _w * _pl
                                        if candle_idx >= _n_needed:
                                            _entry_df = df.iloc[candle_idx - _n_needed + 1:candle_idx + 1]
                                            _entry_patterns = ppmt_engine.encode_pattern_per_level(
                                                _entry_df, pattern_length=_pl
                                            )
                                            _active_learning = {
                                                "pattern_n1": _entry_patterns["n1"],
                                                "pattern_n2": _entry_patterns["n2"],
                                                "pattern_n3": _entry_patterns["n3"],
                                                "entry_price": current_price,
                                                "direction": signal.direction or "LONG",
                                                "entry_ts": candle_idx,
                                                "regime": current_regime,
                                            }
                                    except Exception:
                                        _active_learning = None

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
                              "end_of_data", "end_of_data", result,
                              source="backtest")

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
                     result: RealtimeResult, source: str = "live") -> None:
        """Close a trade and record the result.

        v0.38.9: Added `source` parameter ('live' | 'backtest') so the storage
        layer can deduplicate + filter trades by origin. `run_replay` passes
        'backtest' (historical validation runs), `process_new_candle` and
        `run_live` pass 'live' (real-time paper/live trading).
        """
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
        # v0.38.9: Pass `source` so storage can deduplicate + filter
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
                "source": source,
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
        # v0.39.0: Fire on_position callback with action='close' so the
        # dashboard server can clear _multi_sessions[node_id]["open_position"]
        # and the chart entry-price-line is removed.
        if hasattr(self.config, 'on_position') and self.config.on_position:
            try:
                self.config.on_position({
                    "action": "close",
                    "symbol": trade.symbol,
                    "direction": trade.direction,
                    "entry_price": trade.entry_price,
                    "entry_time": trade.entry_time,
                    "exit_price": exit_price,
                    "exit_time": str(exit_time),
                    "pnl_pct": trade.pnl_pct,
                    "exit_reason": exit_reason,
                    "trade_id": trade.trade_id,
                })
            except Exception:
                pass

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
        divergence_monitor=None,  # v0.46.0: Pattern Divergence Monitor
        btc_filter=None,  # v0.46.0: BTC Context Filter
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
        # v0.47.1 FIX: Dynamic buffer size based on SAX encoding needs.
        # Previously hard-coded to 50, which is insufficient for short timeframes:
        #   5m (W=18, P=5) needs 90 candles → 50 caused _recent_df=None always
        #   1m (W=45, P=5) needs 225 candles → 50 caused _recent_df=None always
        # Now keeps at least W*P + 20 (margin for ATR/regime), capped at 500.
        _min_buf = cfg.sax_window_size * cfg.pattern_length + 20
        _max_buf = max(200, _min_buf, 500)
        if len(recent_prices) > _max_buf:
            del recent_prices[:-_max_buf]
            del recent_highs[:-_max_buf]
            del recent_lows[:-_max_buf]

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

            # v0.46.0: Update BTC context filter periodically
            if btc_filter is not None and result.candles_processed % 100 == 0 and len(recent_prices) >= 50:
                if cfg.symbol == "BTC/USDT":
                    btc_filter.update_btc_context(btc_prices=np.array(recent_prices))

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
                            # v0.47.0: Insert trade outcome into tries (learning loop)
                            _learning_insert_outcome(
                                _active_learning, current_position, current_price,
                                "catastrophic_stop", trie_n1, trie_n2, trie,
                                _learning_n1_buffer, _learning_n2_buffer,
                                regime=current_regime,
                            )
                            _active_learning = None
                            self._close_trade(risk_mgr, current_position, current_price,
                                              current_time, "catastrophic_stop", result)
                            position_state = PositionState.FLAT
                            current_position = None
                            return (sax_buffer, pattern_buffer, position_state, current_position,
                                    trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                    # v0.46.0: Pattern Divergence Exit (live mode)
                    # v0.48.0 (FASE 2B FIX): Use N3-encoded tuples instead of
                    # pattern_buffer strings for tuple-vs-tuple comparison.
                    if (divergence_monitor is not None
                            and divergence_monitor.expected_sequence is not None):
                        recent_symbols = None
                        if ppmt_engine is not None:
                            try:
                                _n3_needed = ppmt_engine.sax_n3.window_size * 3
                                if len(recent_prices) >= _n3_needed:
                                    _s = len(recent_prices) - _n3_needed
                                    _div_df = pd.DataFrame({
                                        'close': recent_prices[_s:],
                                        'high': recent_highs[_s:],
                                        'low': recent_lows[_s:],
                                        'open': recent_prices[_s:],
                                        'volume': [0] * _n3_needed,
                                    })
                                    _n3_all = ppmt_engine._encode(ppmt_engine.sax_n3, _div_df)
                                    recent_symbols = _n3_all[-3:] if len(_n3_all) >= 3 else None
                            except Exception:
                                pass
                        if recent_symbols is None and len(pattern_buffer) >= 3:
                            recent_symbols = pattern_buffer[-3:]  # fallback: strings
                        if recent_symbols is not None:
                            div_result = divergence_monitor.check_divergence(recent_symbols)
                        else:
                            div_result = {'diverged': False}
                        if div_result['diverged']:
                            # v0.47.0: Insert trade outcome into tries (learning loop)
                            _learning_insert_outcome(
                                _active_learning, current_position, current_price,
                                "pattern_broken", trie_n1, trie_n2, trie,
                                _learning_n1_buffer, _learning_n2_buffer,
                                regime=current_regime,
                            )
                            _active_learning = None
                            self._close_trade(risk_mgr, current_position, current_price,
                                              current_time, "pattern_broken", result)
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
                        # v0.47.0: Insert trade outcome into tries (learning loop)
                        _learning_insert_outcome(
                            _active_learning, current_position, current_price,
                            exit_reason, trie_n1, trie_n2, trie,
                            _learning_n1_buffer, _learning_n2_buffer,
                            regime=current_regime,
                        )
                        _active_learning = None
                        self._close_trade(risk_mgr, current_position, current_price,
                                          current_time, exit_reason, result)
                        position_state = PositionState.FLAT
                        current_position = None

                        # v0.40.34: Push position closure to state_callback

                        try:

                            self._update_terminal_state(open_position=None)

                        except Exception:

                            pass

                        # v0.40.34: Push position closure to state_callback

                        try:

                            self._update_terminal_state(open_position=None)

                        except Exception:

                            pass
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                    elif tp_hit:
                        # v0.47.0: Insert trade outcome into tries (learning loop)
                        _learning_insert_outcome(
                            _active_learning, current_position, current_price,
                            "take_profit", trie_n1, trie_n2, trie,
                            _learning_n1_buffer, _learning_n2_buffer,
                            regime=current_regime,
                        )
                        _active_learning = None
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

                # FIX-14 (v0.40.10): pass current_regime for N4 routing
                prediction = pred_engine.predict(
                    current_symbols=current_symbols,
                    entry_price=current_price,
                    timeframe_hours=tf_hours,
                    symbol=cfg.symbol,
                    current_regime=current_regime,
                )

                if prediction.direction == "FLAT" or prediction.confidence <= 0:
                    result.candles_processed += 1
                    return (sax_buffer, pattern_buffer, position_state, current_position,
                            trade_counter, current_regime, peak_capital, last_losing_trade_idx)

                # v0.12.0: 4-level matching
                weighted_confidence = prediction.confidence
                best_trie_level = "n3"

                if ppmt_engine is not None:
                    # v0.47.0: Pass recent_candles DataFrame so each level's
                    # encoder re-encodes with the correct symbol type.
                    _recent_df = None
                    try:
                        _w = cfg.sax_window_size
                        _n_needed = _w * cfg.pattern_length
                        if len(recent_prices) >= _n_needed:
                            # Build mini DataFrame from recent prices for encoding
                            _s = len(recent_prices) - _n_needed
                            _recent_df = pd.DataFrame({
                                'close': recent_prices[_s:],
                                'high': recent_highs[_s:],
                                'low': recent_lows[_s:],
                                'open': recent_prices[_s:],  # approximate
                                'volume': [0] * _n_needed,  # no volume in live buffer
                            })
                    except Exception:
                        _recent_df = None
                    ppmt_result = ppmt_engine.match_raw(
                        current_symbols=current_symbols,
                        current_price=current_price,
                        recent_candles=_recent_df,
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

                # v0.46.0: BTC Context Filter (live mode)
                if btc_filter is not None and btc_filter._btc_regime is not None and weighted_confidence > 0:
                    btc_result = btc_filter.filter_signal(
                        prediction.direction, weighted_confidence,
                    )
                    if btc_result['rejected']:
                        result.candles_processed += 1
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                    weighted_confidence = btc_result['adjusted_confidence']

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

                # Entry signal check — v0.40.0: unified with backtest path.
                # Previous live path used 2 hardcoded floors (0.30/0.15 real,
                # 0.10/0.08 paper) and skipped the 7 SignalThresholds-sourced
                # filters that the backtest applies (regime gates, counter-trend,
                # boost logic). This caused live/backtest asymmetry: live fired
                # signals that backtest would have rejected, and vice versa.
                # Now we apply the SAME 7-filter block as run_replay
                # (realtime.py:992-1085) so live and backtest produce comparable
                # signal counts and trade decisions.
                _live_sig = SignalThresholds.for_mode(
                    getattr(cfg, 'validation_mode', False)
                )
                # Filter 1: base probability gate
                if prediction.overall_probability < _live_sig.base_prob_gate:
                    result.candles_processed += 1
                    return (sax_buffer, pattern_buffer, position_state, current_position,
                            trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                # Filter 2: hard move floor
                if abs(prediction.expected_total_move_pct) < _live_sig.hard_move_floor:
                    result.candles_processed += 1
                    return (sax_buffer, pattern_buffer, position_state, current_position,
                            trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                # Filter 3: ranging regime gate
                if current_regime == "ranging":
                    if prediction.overall_probability < _live_sig.ranging_prob_gate:
                        result.candles_processed += 1
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                    if abs(prediction.expected_total_move_pct) < _live_sig.ranging_move_floor:
                        result.candles_processed += 1
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                # Filter 4: volatile regime gate
                elif current_regime == "volatile":
                    if prediction.overall_probability < _live_sig.volatile_prob_gate:
                        result.candles_processed += 1
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                    if abs(prediction.expected_total_move_pct) < _live_sig.volatile_move_floor:
                        result.candles_processed += 1
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                # Filter 5: counter-trend gates
                elif current_regime == "trending_down" and prediction.direction == "LONG":
                    if prediction.overall_probability < _live_sig.counter_trend_gate:
                        result.candles_processed += 1
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                elif current_regime == "trending_up" and prediction.direction == "SHORT":
                    if prediction.overall_probability < _live_sig.counter_trend_gate:
                        result.candles_processed += 1
                        return (sax_buffer, pattern_buffer, position_state, current_position,
                                trade_counter, current_regime, peak_capital, last_losing_trade_idx)
                # Confidence boost (same as backtest path)
                _boosted_conf = weighted_confidence
                if (prediction.overall_probability >= _live_sig.boost_prob_trigger
                        and abs(prediction.expected_total_move_pct) >= _live_sig.boost_move_trigger):
                    _boosted_conf = max(
                        weighted_confidence,
                        weighted_confidence * (1 + prediction.overall_probability),
                    )
                # Final entry gate (mirrors backtest's compound condition)
                if (prediction.direction != "FLAT"
                        and _boosted_conf >= effective_min_conf
                        and abs(prediction.expected_total_move_pct) > _live_sig.move_threshold
                        and prediction.overall_probability > _live_sig.base_prob_gate):

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
                        # v0.40.34: Push open_position to state_callback so the
                        # dashboard header can show LONG/SHORT + entry + P&L.
                        try:
                            self._update_terminal_state(
                                open_position={
                                    "direction": current_position.direction,
                                    "entry_price": current_position.entry_price,
                                    "size": current_position.size,
                                    "sl_price": float(getattr(current_position, 'sl_price', 0) or 0),
                                    "tp_price": float(getattr(current_position, 'tp_price', 0) or 0),
                                    "entry_time": current_position.entry_time,
                                    "trade_id": current_position.trade_id,
                                },
                            )
                        except Exception:
                            pass
                        current_position.tp_price = tp_price  # type: ignore
                        current_position.trailing_activated = False  # type: ignore

                        position_state = (PositionState.LONG
                                          if prediction.direction == "LONG"
                                          else PositionState.SHORT)
                        trade_counter += 1

                        # v0.46.0: Set expected sequence for divergence monitoring (live mode)
                        # v0.48.0 (FASE 2B FIX): Use _active_learning patterns (tuples)
                        # instead of current_symbols (strings). N3 trie has tuple keys.
                        if divergence_monitor is not None:
                            try:
                                if _active_learning is not None and _active_learning.get("pattern_n3"):
                                    matched_node = trie.search(_active_learning["pattern_n3"])
                                else:
                                    matched_node = trie.search(current_symbols)
                                if matched_node and matched_node.metadata.expected_sequences:
                                    divergence_monitor.set_expected(matched_node.metadata)
                            except Exception:
                                pass

                        # v0.47.0: Capture per-level patterns for learning loop (live mode).
                        if ppmt_engine is not None:
                            try:
                                _pl = cfg.pattern_length
                                _w = cfg.sax_window_size
                                _n_needed = _w * _pl
                                if len(recent_prices) >= _n_needed:
                                    _s = len(recent_prices) - _n_needed
                                    _entry_df = pd.DataFrame({
                                        'close': recent_prices[_s:],
                                        'high': recent_highs[_s:],
                                        'low': recent_lows[_s:],
                                        'open': recent_prices[_s:],
                                        'volume': [0] * _n_needed,
                                    })
                                    _entry_patterns = ppmt_engine.encode_pattern_per_level(
                                        _entry_df, pattern_length=_pl
                                    )
                                    _active_learning = {
                                        "pattern_n1": _entry_patterns["n1"],
                                        "pattern_n2": _entry_patterns["n2"],
                                        "pattern_n3": _entry_patterns["n3"],
                                        "entry_price": current_price,
                                        "direction": signal.direction or "LONG",
                                        "entry_ts": result.candles_processed,
                                        "regime": current_regime,
                                    }
                            except Exception:
                                _active_learning = None

                        # v0.38.1: Log trade execution so user can see WHY trades happen
                        console.print(
                            f"[bold green]TRADE #{trade_counter}[/bold green] "
                            f"{prediction.direction} {cfg.symbol} @ ${current_price:.4f} "
                            f"| conf={weighted_confidence:.2f} | SL=${sl_price:.4f} "
                            f"TP=${tp_price:.4f} | pattern={''.join(current_symbols)}"
                        )
                        # v0.39.0: Fire on_position callback so the dashboard
                        # server can update _multi_sessions[node_id]["open_position"]
                        # for /api/multi-status → chart entry-price-line overlay.
                        if hasattr(cfg, 'on_position') and cfg.on_position:
                            try:
                                cfg.on_position({
                                    "action": "open",
                                    "symbol": cfg.symbol,
                                    "direction": signal.direction or "LONG",
                                    "entry_price": current_price,
                                    "entry_time": current_time,
                                    "sl_price": sl_price,
                                    "tp_price": tp_price,
                                    "size": size,
                                    "confidence": signal.confidence,
                                    "trade_id": trade_counter,
                                })
                            except Exception:
                                pass
                    else:
                        # v0.38.2: Log EVERY rejection (removed throttle) so user
                        # can see WHY Trades=0 even with Signals > 0.
                        # Previously throttled to 1-in-10 which hid critical info
                        # when only 2-3 signals were generated per token.
                        console.print(
                            f"[yellow]Signal #{result.signals_generated} rejected:[/yellow] "
                            f"{reason} | conf={weighted_confidence:.2f} "
                            f"quality={signal.quality_score:.2f} "
                            f"RR={signal.risk_reward_ratio:.2f}"
                        )

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

        # v0.38.3 FIX: When dry_run (paper trading), force validation_mode=True
        # so the v0.25.0 strict signal filters are relaxed. Previously, live
        # paper trading used the strict thresholds (ranging_prob_gate=0.55,
        # volatile_prob_gate=0.60) which rejected 95%+ of signals in fresh tries
        # where Bayesian shrinkage keeps overall_probability near 0.5.
        # With validation_mode=True, gates are: ranging=0.40, volatile=0.45,
        # move_threshold=0.50 — permissive enough for paper trading to actually
        # execute trades so the user can SEE the system working.
        # When --live (real money), validation_mode stays False (strict).
        if getattr(cfg, 'dry_run', True) and not getattr(cfg, 'validation_mode', False):
            cfg.validation_mode = True
            console.print("[cyan]Paper trading: validation_mode=ON (relaxed signal gates)[/cyan]")

        # Classify asset
        classifier = AssetClassifier()
        info = classifier.classify(cfg.symbol)

        # v0.12.0: TokenProfile + auto-calibration + persistence
        token_profile, cfg = self._setup_token_profile(cfg, info, storage)

        # Load tries (v0.12.0: all 4 levels)
        # v0.40.4 FIX-1D: pass asset_class so N1/N2 load from cross-asset pools.
        from ppmt.engine.ppmt import PPMT
        all_tries = storage.load_all_tries(cfg.symbol, asset_class=info.asset_class)
        trie_n1 = all_tries["n1"]
        trie_n2 = all_tries["n2"]
        trie_n3 = all_tries["n3"]
        trie_n4 = all_tries["n4"]

        if trie_n3 is None:
            # v0.39.3: Surface clear error to state_callback so the dashboard
            # can show WHY this session is dead instead of silently returning.
            # Previously the session would just become STOPPED with no reason,
            # leaving the user to guess why "bot not operating".
            _msg = (f"No Trie for {cfg.symbol} — run Validate or Build first. "
                    f"Auto-build may have failed (check data ingestion).")
            console.print(f"[red]{_msg}[/red]")
            try:
                self._update_terminal_state(
                    is_running=False,
                    websocket_status="error",
                    error=_msg,
                )
            except Exception:
                pass
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

        # FIX-14 (v0.40.10): pass trie_n4 as regime_trie for regime-aware lookup
        pred_engine = PredictionEngine(
            trie,
            prediction_depth=cfg.pattern_length,
            regime_trie=trie_n4 if (has_multi_level and trie_n4 is not None) else None,
        )

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

        # v0.46.0: Pattern Divergence Monitor & BTC Context Filter (live mode)
        divergence_monitor = PatternDivergenceMonitor(divergence_threshold=0.667)
        btc_filter = BTCContextFilter()

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

                    # v0.40.33: Skip load_markets() — it's the endpoint that's
                    # blocked on the user's network. Order execution paths that
                    # need market metadata should use _DirectPollExchange or
                    # fetch_ticker on-demand instead.
                    # await exchange.load_markets()

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

        # v0.40.34: Push position closure to state_callback

        try:

            self._update_terminal_state(open_position=None)

        except Exception:

            pass
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

        # v0.38.6: Choose WebSocket or REST polling.
        # Default: REST polling (cfg.use_websocket=False) because Binance WS is
        # frequently blocked from EU networks. User can opt-in via LiveConfig.
        use_websocket = getattr(cfg, 'use_websocket', False)
        if use_websocket:
            try:
                import websockets  # noqa: F401
            except ImportError:
                use_websocket = False
                console.print("[yellow]websockets not installed — falling back to REST polling[/yellow]")
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
        # v0.40.28: is_running=False until the polling loop actually
        # delivers the first price tick. Previously this was True, which
        # made _state_cb flip the session to RUNNING prematurely —
        # misleading the user when load_markets() then failed.
        self._update_terminal_state(
            is_running=False,
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
            # v0.38.2 FIX: When state_callback is provided (server-side multi-token
            # mode via /api/multi-start), run HEADLESS — skip Live panel rendering.
            # Why: each parallel token spawned its own Live() writing to stdout,
            # causing 25+ panels to overlap and duplicate (PHA appearing 8+ times).
            # Headless mode still calls _update_terminal_state + state_callback so
            # the dashboard gets live updates via the API, just no stdout noise.
            headless = self._state_callback is not None

            if headless:
                console.print(f"[dim]Headless mode: {cfg.symbol} ({cfg.timeframe}) — updates via state_callback only[/dim]")

            class _NullLive:
                """No-op Live replacement for headless mode."""
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def update(self, *a, **kw): pass

            live_ctx = Live(console=console, refresh_per_second=2) if not headless else _NullLive()

            # v0.40.28: initialize poll_exchange to None so the finally
            # block can safely close it even if we never entered the
            # REST polling branch (e.g. CancelledError during warmup).
            poll_exchange = None

            with live_ctx as live_display:

                if use_websocket:
                    # === WEBSOCKET MODE (v0.12.0 → v0.13.0 with StreamingPatternBuffer) ===
                    from ppmt.data.websocket_feed import WebSocketFeed, Candle

                    async def on_candle(candle: Candle):
                        nonlocal sax_buffer, position_state
                        nonlocal current_position, trade_counter, current_regime
                        nonlocal peak_capital, last_losing_trade_idx
                        nonlocal candles_since_calibration

                        # v0.37.0 CRITICAL FIX: StreamingPatternBuffer sync bug.
                        #
                        # PREVIOUS BUG: `pattern_buffer = stream_buf.pattern_buffer`
                        # returns a COPY. We then passed that copy to process_new_candle,
                        # which mutated it in-place (append + del). After the call,
                        # both `pattern_buffer` and the returned `_pattern_buffer`
                        # pointed to the SAME mutated list, so the diff
                        # `_pattern_buffer[len(pattern_buffer):]` was ALWAYS empty.
                        # Result: stream_buf._pattern_buffer, _symbol_counts,
                        # _total_symbols, _symbols_produced NEVER updated.
                        # Dashboard showed Pattern: [...] | Entropy: 0.0b forever,
                        # even though SAX symbols were produced internally.
                        #
                        # FIX: Use the authoritative `result.sax_symbols_produced`
                        # counter (incremented inside process_new_candle) to compute
                        # how many new symbols were produced this candle, then sync
                        # the StreamingPatternBuffer accordingly.
                        prev_produced = stream_buf._symbols_produced

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
                            divergence_monitor=divergence_monitor,
                            btc_filter=btc_filter,
                        )

                        # v0.38.1 FIX: Replaced incremental sync (which had multiple
                        # subtle bugs around copy vs reference, trimming mismatch
                        # between process_new_candle's pattern_length*2 and
                        # stream_buf's pattern_length*3, and early returns leaving
                        # _pattern_buffer out of sync) with a single authoritative
                        # assignment. _pattern_buffer is the list returned by
                        # process_new_candle — it contains the latest trimmed snapshot.
                        # We rebuild stream_buf's internal state from it.
                        if _pattern_buffer != stream_buf._pattern_buffer:
                            # Recompute symbol counts from the new buffer to keep
                            # entropy stats consistent with the trimmed buffer.
                            from collections import Counter as _Counter
                            stream_buf._pattern_buffer = list(_pattern_buffer)
                            stream_buf._symbol_counts = _Counter(_pattern_buffer)
                            stream_buf._total_symbols = sum(stream_buf._symbol_counts.values())
                            stream_buf._symbols_produced = result.sax_symbols_produced
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

                                # v0.47.0: Flush learning N1/N2 buffers to shared pools.
                                # These buffers accumulate real trade outcomes from the
                                # learning loop and need to be merged into the cross-asset
                                # universal (__UNIVERSAL__) and class (__CLASS_*__) pools.
                                if _learning_n1_buffer.pattern_count > 0:
                                    try:
                                        existing_n1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
                                        if existing_n1 is None:
                                            storage.save_trie(UNIVERSAL_POOL_KEY, "n1", _learning_n1_buffer)
                                        else:
                                            for _pat, _node in _learning_n1_buffer.get_all_patterns(min_count=1):
                                                _meta = _node.metadata
                                                for _obs in range(_meta.historical_count):
                                                    existing_n1.insert_with_observations(
                                                        symbols=_pat,
                                                        move_pct=_meta.expected_move_pct,
                                                        won=_meta.win_rate > 0.5,
                                                        regime=_meta.regime_distribution,
                                                    )
                                            storage.save_trie(UNIVERSAL_POOL_KEY, "n1", existing_n1)
                                        _learning_n1_buffer = PPMTTrie(name="learning_n1_buffer")
                                        logger.info("[LEARN] Flushed N1 learning buffer to __UNIVERSAL__ pool")
                                    except Exception as e:
                                        logger.warning(f"[LEARN] N1 buffer flush failed: {e}")

                                if _learning_n2_buffer.pattern_count > 0:
                                    try:
                                        _n2_key = class_pool_key(info.asset_class)
                                        existing_n2 = storage.load_trie(_n2_key, "n2")
                                        if existing_n2 is None:
                                            storage.save_trie(_n2_key, "n2", _learning_n2_buffer)
                                        else:
                                            for _pat, _node in _learning_n2_buffer.get_all_patterns(min_count=1):
                                                _meta = _node.metadata
                                                for _obs in range(_meta.historical_count):
                                                    existing_n2.insert_with_observations(
                                                        symbols=_pat,
                                                        move_pct=_meta.expected_move_pct,
                                                        won=_meta.win_rate > 0.5,
                                                        regime=_meta.regime_distribution,
                                                    )
                                            storage.save_trie(_n2_key, "n2", existing_n2)
                                        _learning_n2_buffer = PPMTTrie(name="learning_n2_buffer")
                                        logger.info(f"[LEARN] Flushed N2 learning buffer to {_n2_key} pool")
                                    except Exception as e:
                                        logger.warning(f"[LEARN] N2 buffer flush failed: {e}")
                            except Exception:
                                pass  # Non-critical

                        # v0.20.0: Periodic MoneyManager auto-save + update TerminalState
                        try:
                            money_mgr._maybe_auto_save()
                        except Exception:
                            pass

                        # v0.20.0: Update TerminalState with portfolio data
                        # v0.36.2 FIX: Previously passed pattern_symbol=last_symbol (or None)
                        # every candle, which caused update_sync() to append str(None)="None"
                        # to pattern_buffer — hence the dashboard showed "N N N N..." instead
                        # of actual SAX symbols (a, b, c, ...).
                        # Now we pass the full pattern_buffer snapshot directly. The state's
                        # setattr replaces the list wholesale (no append, no "None" pollution).
                        # v0.38.2: Also pass signals_generated so dashboard "signals" reflects
                        # actual trading signals (not sax_symbols_produced count).
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
                            pattern_buffer=list(stream_buf.pattern_buffer)[-30:],
                            sax_symbols_produced=stream_buf.symbols_produced,
                            entropy=stream_buf.entropy,
                            regime=current_regime,
                            signals_generated=result.signals_generated,
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

# v0.40.32: Use _DirectPollExchange (aiohttp direct HTTP) instead of ccxt.
                    # ccxt's fetch_ticker/fetch_ohlcv internally call load_markets()
                    # which times out on some networks. _DirectPollExchange bypasses
                    # ccxt entirely, calling MEXC/Binance/Bybit REST APIs directly.
                    _effective_exchange = cfg.exchange
                    try:
                        poll_exchange = _DirectPollExchange(
                            cfg.exchange,
                            api_key=cfg.api_key,
                            api_secret=cfg.api_secret,
                        )
                        # Verify connection with a single fetch_ticker
                        _ticker = await poll_exchange.fetch_ticker(cfg.symbol)
                        _ticker_price = _ticker.get('last') or _ticker.get('close')
                        if _ticker_price is None or _ticker_price == 0:
                            raise RuntimeError(f"fetch_ticker returned no price for {cfg.symbol}")
                        console.print(f"[green]Connected to {cfg.exchange} (direct HTTP polling, no load_markets)[/green]")
                        console.print(f"  {cfg.symbol} last price: ${_ticker_price}")
                    except Exception as e_primary:
                        # Auto-fallback Binance → MEXC
                        if cfg.exchange.lower() == 'binance':
                            console.print(f"[yellow]Binance direct poll failed ({e_primary}). Falling back to MEXC…[/yellow]")
                            try:
                                if 'poll_exchange' in dir():
                                    await poll_exchange.close()
                            except Exception:
                                pass
                            try:
                                poll_exchange = _DirectPollExchange(
                                    'mexc',
                                    api_key=cfg.api_key,
                                    api_secret=cfg.api_secret,
                                )
                                _ticker = await poll_exchange.fetch_ticker(cfg.symbol)
                                _ticker_price = _ticker.get('last') or _ticker.get('close')
                                if _ticker_price is None or _ticker_price == 0:
                                    raise RuntimeError(f"MEXC fetch_ticker returned no price for {cfg.symbol}")
                                _effective_exchange = 'mexc'
                                console.print(f"[green]Connected to MEXC (fallback from Binance) — direct HTTP polling[/green]")
                                console.print(f"  {cfg.symbol} last price: ${_ticker_price}")
                            except Exception as e_mexc:
                                _err_msg = (f"Exchange connection failed (binance + mexc fallback): "
                                    f"binance={type(e_primary).__name__}: {e_primary} | "
                                    f"mexc={type(e_mexc).__name__}: {e_mexc}")
                                console.print(f"[red]Failed to connect: binance={type(e_primary).__name__}: {e_primary} | mexc fallback={type(e_mexc).__name__}: {e_mexc}[/red]")
                                try:
                                    self._update_terminal_state(
                                        is_running=False,
                                        websocket_status="disconnected",
                                        error=_err_msg,
                                    )
                                except Exception:
                                    pass
                                try:
                                    await poll_exchange.close()
                                except Exception:
                                    pass
                                storage.close()
                                return result
                        else:
                            _err_msg = f"Exchange connection failed: {type(e_primary).__name__}: {e_primary}"
                            console.print(f"[red]Failed to connect: {type(e_primary).__name__}: {e_primary}[/red]")
                            try:
                                self._update_terminal_state(
                                    is_running=False,
                                    websocket_status="disconnected",
                                    error=_err_msg,
                                )
                            except Exception:
                                pass
                            try:
                                await poll_exchange.close()
                            except Exception:
                                pass
                            storage.close()
                            return result

                    last_candle_ts = 0

                    # v0.37.0 FIX: Initialize pattern_buffer for REST polling mode.
                    # Previously this was missing — first call to process_new_candle
                    # would crash with NameError: pattern_buffer is not defined.
                    pattern_buffer = []

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
                            # v0.37.0: Track prev_produced for streaming buffer sync
                            _prev_prod = stream_buf._symbols_produced
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
                                divergence_monitor=divergence_monitor,
                                btc_filter=btc_filter,
                            )
                            # v0.38.1 FIX: Authoritative sync (same as WS mode)
                            if pattern_buffer != stream_buf._pattern_buffer:
                                from collections import Counter as _Counter
                                stream_buf._pattern_buffer = list(pattern_buffer)
                                stream_buf._symbol_counts = _Counter(pattern_buffer)
                                stream_buf._total_symbols = sum(stream_buf._symbol_counts.values())
                                stream_buf._symbols_produced = result.sax_symbols_produced
                                stream_buf._trim()
                        console.print(f"[green]Warmup: processed {len(warmup_ohlcv)} historical candles[/green]")
                        # v0.40.34: Push state IMMEDIATELY after warmup so the
                        # dashboard shows Candles>0 and status=RUNNING without
                        # waiting for the first polling cycle (which can take 5s).
                        # Previously, the UI showed Candles: 0 and STOPPED for
                        # 5-30s after warmup completed, confusing the user.
                        try:
                            self._update_terminal_state(
                                is_running=True,  # promote to RUNNING (v0.40.34)
                                candles_processed=len(warmup_ohlcv),
                                current_price=recent_prices[-1] if recent_prices else 0,
                                portfolio_value=money_mgr.total_value,
                                cash=money_mgr.cash,
                                websocket_status="polling",
                                regime=current_regime,
                            )
                        except Exception:
                            pass
                    except Exception as e:
                        console.print(f"[yellow]Warmup failed: {e}[/yellow]")

                    while True:
                        try:
                            # v0.40.34: Fetch live ticker FIRST for real-time price.
                            # Previously we only fetched ohlcv (1m candle close) which
                            # could be 5-60s stale on 1m+ TFs, causing visible price
                            # jumps in the UI when the new candle finally closed.
                            # Now: ticker gives real-time spot, ohlcv gives candle boundary.
                            try:
                                _live_ticker = await poll_exchange.fetch_ticker(cfg.symbol)
                                live_price = float(_live_ticker.get('last') or _live_ticker.get('close') or 0)
                            except Exception:
                                live_price = 0.0
                            ohlcv = await poll_exchange.fetch_ohlcv(cfg.symbol, cfg.timeframe, limit=1)

                            if ohlcv:
                                candle_data = ohlcv[-1]
                                candle_ts = candle_data[0]
                                # v0.40.34: Prefer ticker price (real-time). Fall back
                                # to ohlcv close only if ticker failed.
                                if live_price <= 0:
                                    live_price = float(candle_data[4]) if len(candle_data) > 4 else 0.0
                                if live_price > 0:
                                    recent_prices.append(live_price)
                                    if len(recent_prices) > 200:
                                        recent_prices.pop(0)
                                    # Keep recent_highs/lows fresh too
                                    if len(candle_data) > 4:
                                        recent_highs.append(float(candle_data[2]))
                                        recent_lows.append(float(candle_data[3]))
                                        if len(recent_highs) > 200:
                                            recent_highs.pop(0)
                                        if len(recent_lows) > 200:
                                            recent_lows.pop(0)

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

                                    # v0.37.0: Track prev_produced for streaming buffer sync
                                    _prev_prod = stream_buf._symbols_produced
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
                                        divergence_monitor=divergence_monitor,
                                        btc_filter=btc_filter,
                                    )
                                    # v0.38.1 FIX: Authoritative sync (same as WS mode)
                                    if pattern_buffer != stream_buf._pattern_buffer:
                                        from collections import Counter as _Counter
                                        stream_buf._pattern_buffer = list(pattern_buffer)
                                        stream_buf._symbol_counts = _Counter(pattern_buffer)
                                        stream_buf._total_symbols = sum(stream_buf._symbol_counts.values())
                                        stream_buf._symbols_produced = result.sax_symbols_produced
                                        stream_buf._trim()

                                # v0.38.2: Push state EVERY poll (not only on new candle close)
                                # so the dashboard's last_price updates in real time.
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
                                    pattern_buffer=list(stream_buf.pattern_buffer)[-30:],
                                    sax_symbols_produced=stream_buf.symbols_produced,
                                    entropy=stream_buf.entropy,
                                    regime=current_regime,
                                    is_running=True,
                                    websocket_status="polling",
                                    signals_generated=result.signals_generated,
                                    win_rate=(result.winning_trades / result.total_trades * 100.0) if result.total_trades > 0 else 0.0,
                                )
                                # v0.20.0: Update money manager positions
                                if current_position is not None and recent_prices:
                                    try:
                                        money_mgr.update_position(cfg.symbol, recent_prices[-1])
                                    except Exception:
                                        pass
                                _update_live_display(live_display, cfg, result, position_state,
                                                     current_position, current_regime, recent_prices,
                                                     risk_mgr, stream_buf)

                            # Poll every 5 seconds for low-timeframe responsiveness (v0.38.0)
                            await asyncio.sleep(5)

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
            # v0.40.28: also close poll_exchange (REST polling exchange).
            # Previously only `exchange` (order-execution) was closed, but
            # `poll_exchange` was leaked — causing "Unclosed client session"
            # warnings + eventual server death when many sessions were
            # started/stopped in sequence.
            if poll_exchange is not None:
                try:
                    await poll_exchange.close()
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
        # v0.38.0 FIX: Previous condition `if sl_val and tp_val` was True for
        # any non-zero sl_val/tp_val, but the format string `${sl_val:,.0f}`
        # rounded prices < $0.5 to "$0" — so XLM/OP/INJ showed "SL: $0 (-2.4%)".
        # Now use dynamic decimal places based on price magnitude, and require
        # sl_val/tp_val to be strictly non-None and strictly positive.
        if sl_val is not None and tp_val is not None and sl_val > 0 and tp_val > 0 and current_price > 0:
            sl_dist = abs(current_price - sl_val) / current_price * 100
            tp_dist = abs(tp_val - current_price) / current_price * 100
            # v0.38.0: Dynamic decimals — $1000+ → 0dp, $1+ → 2dp, <$1 → 4dp, <$0.01 → 6dp
            def _fmt_price(p: float) -> str:
                if p >= 1000:
                    return f"${p:,.0f}"
                elif p >= 1:
                    return f"${p:,.2f}"
                elif p >= 0.01:
                    return f"${p:,.4f}"
                else:
                    return f"${p:,.6f}"
            sl_tp_str = f" | SL: {_fmt_price(sl_val)} (-{sl_dist:.1f}%) TP: {_fmt_price(tp_val)} (+{tp_dist:.1f}%)"

    # Buffer info
    buf_str = ""
    if stream_buf is not None:
        # v0.38.2 FIX: Rich was eating the `[]` brackets as markup tags when
        # pat was non-empty (e.g. "[a]" — single-letter tag → silently dropped,
        # leaving "Pattern:  | Entropy" with brackets vanished).
        # Escape BOTH the brackets AND the pat content so they render literally.
        pat = " -> ".join(stream_buf.get_pattern()) if stream_buf.has_pattern() else "..."
        pat_display = _rich_escape(f"[{pat}]")
        buf_str = f" | Pattern: {pat_display} | Entropy: {stream_buf.entropy:.1f}b"

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
    Update the Living Trie stats and periodically re-propagate metadata.

    v0.40.5 FIX-5: This function NO LONGER inserts bogus zero-outcome
    observations into the trie. The previous implementation called
    `trie.insert_with_observations(symbols=..., move_pct=0.0, won=False,
    drawdown=0, favorable=0, duration=0)` for every pattern produced,
    which:
      1. Added observations with bogus zero outcomes (move_pct=0, won=False).
      2. These were never updated with real outcomes (no exit handler existed).
      3. They diluted real observations: a node with 1 real obs + 5 bogus
         had win_rate=1/6=16.7% (instead of 100%) and
         expected_move_pct=real_move/6 (drastically reduced).
      4. Paradoxically, count_bonus grew with more obs, so confidence
         INCREASED while |EM| DECREASED — engine reported high confidence
         in a near-zero move → many false signals.
      5. The bogus obs persisted to storage via periodic save_trie (line 2347),
         contaminating future sessions.

    See scripts/layer4_audit_results.json CAPA 4 H1/H2/H3 for evidence.

    The trie still learns from:
      - build() at startup (uses real prices)
      - _record_observation() in paper_trader.py when trades close (uses real
        pnl, actual_move_pct, sl_price, tp_price, duration)

    This function now only:
      - Pushes current trie stats to the dashboard (so the "Living Trie Stats"
        widget continues to show real data).
      - Periodically re-propagates metadata (every 50 pattern cycles) so
        intermediate node stats stay fresh even without new observations.
    """
    # Only update every pattern_length symbols (1 update per full pattern cycle)
    if stream_buf.symbols_produced % cfg.pattern_length != 0:
        return

    # FIX-5: NO bogus insertion. The trie learns only from real trade outcomes
    # via _record_observation (paper_trader.py / portfolio_runner.py) and from
    # build() at startup. Inserting observations with move_pct=0 just to "have
    # data" poisoned the trie — see CAPA 4 audit.

    # Push current trie stats to the dashboard so the Patterns tab and Trading
    # tab "Living Trie Stats" widget continue to show real data (v0.38.9).
    if _terminal_state is not None:
        try:
            _terminal_state.update_sync(
                living_trie_stats={
                    "pattern_count": trie.pattern_count,
                    "max_depth": trie.max_depth,
                    "trading_observations": trie.trading_observations,
                    "last_update": time.time(),
                },
            )
        except Exception:
            pass

    # Periodically re-propagate metadata (every 50 updates) — keeps
    # intermediate node stats fresh even without new observations.
    if stream_buf.symbols_produced % (cfg.pattern_length * 50) == 0:
        try:
            trie.propagate_metadata()
        except Exception:
            pass


def _learning_insert_outcome(
    active_learning: dict,
    current_position,
    current_price: float,
    exit_reason: str,
    trie_n1,
    trie_n2,
    trie_n3,
    n1_buffer: PPMTTrie,
    n2_buffer: PPMTTrie,
    regime: str = None,
) -> None:
    """v0.47.0: Insert trade outcome into N1/N2/N3 tries (learning loop).

    Called when a position closes. Computes won/move_pct/duration/drawdown
    from the position and inserts the observation into each trie level.
    Also accumulates observations in N1/N2 buffers for later flush to
    shared pools (__UNIVERSAL__ and __CLASS_*__).
    """
    if active_learning is None or current_position is None:
        return

    try:
        entry_price = active_learning.get("entry_price", current_position.entry_price)
        direction = active_learning.get("direction", current_position.direction)

        # Compute outcome
        if direction == "LONG":
            move_pct = (current_price - entry_price) / entry_price * 100
        else:
            move_pct = (entry_price - current_price) / entry_price * 100

        won = exit_reason == "take_profit"
        drawdown_pct = min(move_pct, 0) if move_pct < 0 else 0
        favorable_pct = max(move_pct, 0) if move_pct > 0 else 0
        duration = max(1, getattr(current_position, '_candles_held', 1))

        # Insert into N3 (per-symbol trie)
        pattern_n3 = active_learning.get("pattern_n3")
        if pattern_n3 and trie_n3 is not None:
            trie_n3.insert_with_observations(
                symbols=pattern_n3,
                move_pct=move_pct,
                drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct,
                duration=duration,
                won=won,
                regime=regime,
            )
            logger.info(
                f"[LEARN] N3: won={won} move={move_pct:.2f}% "
                f"reason={exit_reason} pattern={pattern_n3}"
            )

        # Insert into N1 (universal pool buffer)
        pattern_n1 = active_learning.get("pattern_n1")
        if pattern_n1 and n1_buffer is not None:
            n1_buffer.insert_with_observations(
                symbols=pattern_n1,
                move_pct=move_pct,
                drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct,
                duration=duration,
                won=won,
                regime=regime,
            )

        # Insert into N2 (class pool buffer)
        pattern_n2 = active_learning.get("pattern_n2")
        if pattern_n2 and n2_buffer is not None:
            n2_buffer.insert_with_observations(
                symbols=pattern_n2,
                move_pct=move_pct,
                drawdown_pct=drawdown_pct,
                favorable_pct=favorable_pct,
                duration=duration,
                won=won,
                regime=regime,
            )

    except Exception as e:
        logger.warning(f"[LEARN] Failed to insert outcome: {e}")


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
