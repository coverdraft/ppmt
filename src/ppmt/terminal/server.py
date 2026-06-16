"""
PPMT Terminal Server — FastAPI + WebSocket web dashboard with Money Management.

Serves the real-time trading dashboard and provides both REST and WebSocket
endpoints for the front-end to consume state from :class:`TerminalState`.

v0.27.0: Complete dashboard with candlestick chart, entry/exit markers,
real-time MEXC data, paper trading, and backtesting with real data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ppmt.terminal.state import TerminalState, get_terminal_state
from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Static files path
# ------------------------------------------------------------------ #
_STATIC_DIR = Path(__file__).parent / "static"
_DASHBOARD_HTML_PATH = _STATIC_DIR / "index.html"

# ------------------------------------------------------------------ #
# Config directory for persistent state
# ------------------------------------------------------------------ #
CONFIG_DIR = os.path.expanduser("~/.ppmt")

# ------------------------------------------------------------------ #
# FastAPI application
# ------------------------------------------------------------------ #
app = FastAPI(title="PPMT Terminal", version="0.32.0")

# Global terminal state (shared with engine)
terminal_state: TerminalState = get_terminal_state()

# Connected WebSocket clients
_ws_clients: set[WebSocket] = set()

# Parent Node Manager (lazy-loaded)
_parent_manager = None


def _get_parent_manager():
    """Get or create the ParentNodeManager."""
    global _parent_manager
    if _parent_manager is None:
        from ppmt.risk.money_manager import ParentNodeManager, ChildNodeConfig
        state_file = os.path.join(CONFIG_DIR, "parent_node_state.json")
        import yaml

        # Default capital from terminal state
        capital = terminal_state.portfolio_value or 10_000.0
        _parent_manager = ParentNodeManager(total_capital=capital)

        # Load saved state
        if os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    saved = yaml.safe_load(f) or {}
                _parent_manager.total_capital = saved.get("total_capital", capital)
                for child_data in saved.get("children", []):
                    cfg = ChildNodeConfig(**child_data)
                    _parent_manager.register_child(cfg)
                if _parent_manager._children:
                    _parent_manager.distribute_capital()
            except Exception as e:
                logger.warning(f"Failed to load parent node state: {e}")

    return _parent_manager


def _save_parent_manager():
    """Save ParentNodeManager state to disk."""
    import yaml
    pm = _get_parent_manager()
    state_file = os.path.join(CONFIG_DIR, "parent_node_state.json")
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        state = {
            "total_capital": pm.total_capital,
            "children": [
                {
                    "node_id": cfg.node_id,
                    "symbol": cfg.symbol,
                    "timeframe": cfg.timeframe,
                    "capital_allocation_pct": cfg.capital_allocation_pct,
                    "leverage": cfg.leverage,
                    "auto_mode": cfg.auto_mode,
                    "max_position_pct": cfg.max_position_pct,
                    "enabled": cfg.enabled,
                }
                for cfg in pm._children.values()
            ],
        }
        with open(state_file, "w") as f:
            yaml.dump(state, f, default_flow_style=False)
    except Exception as e:
        logger.warning(f"Failed to save parent node state: {e}")


# ------------------------------------------------------------------ #
# HTML serving
# ------------------------------------------------------------------ #


def _load_dashboard_html() -> str:
    """Load the dashboard HTML from the static directory."""
    if _DASHBOARD_HTML_PATH.exists():
        return _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    return _FALLBACK_HTML


@app.get("/")
async def dashboard() -> HTMLResponse:
    """Serve the dashboard HTML."""
    return HTMLResponse(content=_load_dashboard_html())


# ------------------------------------------------------------------ #
# REST endpoints — State
# ------------------------------------------------------------------ #


@app.get("/api/status")
async def get_status() -> dict:
    """REST endpoint for current terminal status."""
    return terminal_state.to_dict()


@app.get("/api/snapshot")
async def get_snapshot() -> dict:
    """Full state snapshot including uptime."""
    return terminal_state.get_snapshot()


@app.get("/api/portfolio")
async def get_portfolio() -> dict:
    """Portfolio summary."""
    return {
        "value": terminal_state.portfolio_value,
        "cash": terminal_state.cash,
        "unrealized_pnl": terminal_state.unrealized_pnl,
        "realized_pnl": terminal_state.realized_pnl,
        "total_pnl_pct": terminal_state.total_pnl_pct,
        "exposure_pct": terminal_state.exposure_pct,
        "daily_return_pct": terminal_state.daily_return_pct,
        "positions": terminal_state.positions,
        "leverage": terminal_state.leverage,
        "auto_mode": terminal_state.auto_mode,
    }


@app.get("/api/signals")
async def get_signals() -> dict:
    """Recent signals."""
    return {"signals": terminal_state.signals_history}


@app.get("/api/performance")
async def get_performance() -> dict:
    """Performance metrics."""
    return {
        "total_trades": terminal_state.total_trades,
        "winning_trades": terminal_state.winning_trades,
        "win_rate": terminal_state.win_rate,
        "max_drawdown": terminal_state.max_drawdown,
        "equity_curve": terminal_state.equity_curve,
        "equity_timestamps": terminal_state.equity_timestamps,
    }


@app.get("/api/risk")
async def get_risk() -> dict:
    """Risk state."""
    return {
        "circuit_breakers": terminal_state.circuit_breakers,
        "is_trading_allowed": terminal_state.is_trading_allowed,
        "kill_switch_active": terminal_state.kill_switch_active,
        "exposure_pct": terminal_state.exposure_pct,
        "daily_return_pct": terminal_state.daily_return_pct,
        "max_drawdown_pct": terminal_state.max_drawdown_pct,
        "daily_loss_pct": terminal_state.daily_loss_pct,
    }


# ------------------------------------------------------------------ #
# REST endpoints — Money Management & Nodes (v0.25.0)
# ------------------------------------------------------------------ #


@app.get("/api/nodes")
async def get_nodes() -> dict:
    """Get all child nodes and parent state."""
    pm = _get_parent_manager()
    children = []
    for node_id, cfg in pm._children.items():
        state = pm._child_states.get(node_id)
        children.append({
            "node_id": cfg.node_id,
            "symbol": cfg.symbol,
            "timeframe": cfg.timeframe,
            "capital_allocation_pct": cfg.capital_allocation_pct,
            "leverage": cfg.leverage,
            "auto_mode": cfg.auto_mode,
            "max_position_pct": cfg.max_position_pct,
            "enabled": cfg.enabled,
            "allocated_capital": state.allocated_capital if state else 0,
            "available_capital": state.available_capital if state else 0,
            "realized_pnl": state.realized_pnl if state else 0,
            "unrealized_pnl": state.unrealized_pnl if state else 0,
            "open_positions": state.open_positions if state else 0,
            "total_trades": state.total_trades if state else 0,
            "winning_trades": state.winning_trades if state else 0,
        })

    return {
        "total_capital": pm.total_capital,
        "reserve_capital": pm.reserve_capital,
        "total_portfolio_value": pm.total_portfolio_value,
        "total_exposure_pct": pm.total_exposure_pct,
        "total_realized_pnl": pm.total_realized_pnl,
        "total_unrealized_pnl": pm.total_unrealized_pnl,
        "kill_switch_active": pm._global_kill_switch,
        "children": children,
    }


class AddNodeRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    capital_allocation_pct: float = 0.20
    leverage: int = 1
    auto_mode: bool = True


@app.post("/api/nodes/add")
async def add_node(req: AddNodeRequest) -> dict:
    """Add a child node."""
    from ppmt.risk.money_manager import ChildNodeConfig
    pm = _get_parent_manager()
    node_id = f"{req.symbol.split('/')[0].lower()}_{req.timeframe}"

    if node_id in pm._children:
        return {"ok": False, "error": f"Node '{node_id}' already exists"}

    cfg = ChildNodeConfig(
        node_id=node_id,
        symbol=req.symbol,
        timeframe=req.timeframe,
        capital_allocation_pct=req.capital_allocation_pct,
        leverage=req.leverage,
        auto_mode=req.auto_mode,
    )

    try:
        pm.register_child(cfg)
        pm.distribute_capital()
        _save_parent_manager()
        return {"ok": True, "node_id": node_id, "capital": pm.get_child_capital(node_id)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class RemoveNodeRequest(BaseModel):
    node_id: str


@app.post("/api/nodes/remove")
async def remove_node(req: RemoveNodeRequest) -> dict:
    """Remove a child node."""
    pm = _get_parent_manager()
    try:
        pm.unregister_child(req.node_id)
        _save_parent_manager()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SetLeverageRequest(BaseModel):
    node_id: str
    leverage: int


@app.post("/api/nodes/leverage")
async def set_leverage(req: SetLeverageRequest) -> dict:
    """Set leverage for a child node."""
    pm = _get_parent_manager()
    try:
        pm.set_child_leverage(req.node_id, req.leverage)
        _save_parent_manager()
        return {"ok": True, "node_id": req.node_id, "leverage": req.leverage}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SetAutoModeRequest(BaseModel):
    node_id: str
    auto_mode: bool


@app.post("/api/nodes/auto-mode")
async def set_auto_mode(req: SetAutoModeRequest) -> dict:
    """Set auto/manual mode for a child node."""
    pm = _get_parent_manager()
    try:
        pm.set_child_auto_mode(req.node_id, req.auto_mode)
        _save_parent_manager()
        return {"ok": True, "node_id": req.node_id, "auto_mode": req.auto_mode}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SetCapitalRequest(BaseModel):
    total_capital: float


@app.post("/api/nodes/capital")
async def set_capital(req: SetCapitalRequest) -> dict:
    """Set total parent capital."""
    pm = _get_parent_manager()
    pm.total_capital = req.total_capital
    pm.distribute_capital()
    _save_parent_manager()
    return {"ok": True, "total_capital": pm.total_capital}


@app.post("/api/nodes/kill-switch/activate")
async def activate_kill_switch() -> dict:
    """Activate the global kill switch."""
    pm = _get_parent_manager()
    pm.activate_global_kill_switch()
    terminal_state.kill_switch_active = True
    terminal_state.is_trading_allowed = False
    _save_parent_manager()
    return {"ok": True, "kill_switch": True}


@app.post("/api/nodes/kill-switch/deactivate")
async def deactivate_kill_switch() -> dict:
    """Deactivate the global kill switch."""
    pm = _get_parent_manager()
    pm.deactivate_global_kill_switch()
    terminal_state.kill_switch_active = False
    terminal_state.is_trading_allowed = True
    _save_parent_manager()
    return {"ok": True, "kill_switch": False}


class RedistributeRequest(BaseModel):
    allocations: dict  # node_id -> pct (as fraction 0-1)


@app.post("/api/nodes/redistribute")
async def redistribute_capital(req: RedistributeRequest) -> dict:
    """Redistribute capital among child nodes."""
    pm = _get_parent_manager()
    try:
        pm.redistribute_capital(req.allocations)
        _save_parent_manager()
        return {"ok": True, "allocations": req.allocations}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoint — Run backtest via dashboard (v0.25.0)
# ------------------------------------------------------------------ #


class BacktestRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    capital: float = 10_000.0


@app.post("/api/backtest")
async def run_backtest(req: BacktestRequest) -> dict:
    """Run a quick backtest and return results."""
    try:
        from ppmt.engine.realtime import RealtimeTrader, ReplayConfig
        config = ReplayConfig(
            symbol=req.symbol,
            timeframe=req.timeframe,
            initial_capital=req.capital,
            speed=0,
            verbose=False,
        )
        trader = RealtimeTrader(config=config)
        result = trader.run_replay()

        trades = []
        for t in result.trades:
            # Parse timestamps for chart markers
            entry_ts = None
            exit_ts = None
            try:
                if t.entry_time:
                    import pandas as pd
                    dt = pd.Timestamp(t.entry_time)
                    entry_ts = int(dt.timestamp())
                if t.exit_time:
                    import pandas as pd
                    dt = pd.Timestamp(t.exit_time)
                    exit_ts = int(dt.timestamp())
            except Exception:
                pass

            trades.append({
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_ts": entry_ts,
                "exit_ts": exit_ts,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
                "regime": t.regime,
            })

        return {
            "ok": True,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "total_pnl_pct": result.total_pnl_pct,
            "max_drawdown": result.max_drawdown,
            "trades": trades,
            "equity_curve": result.equity_curve[-200:] if result.equity_curve else [],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoints — Market Data & OHLCV for Chart (v0.27.0)
# ------------------------------------------------------------------ #


@app.get("/api/ohlcv")
async def get_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1m",
    limit: int = 200,
    exchange: str = "mexc",
) -> dict:
    """Fetch real OHLCV data from exchange via ccxt for the candlestick chart."""
    try:
        import ccxt
        ex = getattr(ccxt, exchange, None)
        if ex is None:
            return {"ok": False, "error": f"Exchange '{exchange}' not found"}
        exc = ex()
        try:
            ohlcv = exc.fetch_ohlcv(symbol, timeframe, limit=min(limit, 1000))
            candles = []
            for c in ohlcv:
                candles.append({
                    "t": c[0],       # timestamp
                    "o": c[1],       # open
                    "h": c[2],       # high
                    "l": c[3],       # low
                    "c": c[4],       # close
                    "v": c[5],       # volume
                })
            return {"ok": True, "symbol": symbol, "timeframe": timeframe, "candles": candles}
        finally:
            if hasattr(exc, 'close'):
                exc.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/market/price")
async def get_market_price(
    symbol: str = "BTC/USDT",
    exchange: str = "mexc",
) -> dict:
    """Get current market price from exchange."""
    try:
        import ccxt
        ex = getattr(ccxt, exchange, None)
        if ex is None:
            return {"ok": False, "error": f"Exchange '{exchange}' not found"}
        exc = ex()
        try:
            ticker = exc.fetch_ticker(symbol)
            return {
                "ok": True,
                "symbol": symbol,
                "price": ticker.get("last", 0),
                "change_24h": ticker.get("percentage", 0),
                "high_24h": ticker.get("high", 0),
                "low_24h": ticker.get("low", 0),
                "volume_24h": ticker.get("quoteVolume", 0),
            }
        finally:
            if hasattr(exc, 'close'):
                exc.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/market/symbols")
async def get_market_symbols(exchange: str = "mexc") -> dict:
    """Get available trading symbols from exchange."""
    try:
        import ccxt
        ex = getattr(ccxt, exchange, None)
        if ex is None:
            return {"ok": False, "error": f"Exchange '{exchange}' not found"}
        exc = ex()
        try:
            markets = exc.load_markets()
            usdt_pairs = sorted([
                s for s in markets.keys()
                if s.endswith("/USDT") and markets[s].get("active", True)
            ])
            # Return top 100 by default
            return {"ok": True, "exchange": exchange, "symbols": usdt_pairs[:100]}
        finally:
            if hasattr(exc, 'close'):
                exc.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


class IngestRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1m"
    exchange: str = "mexc"
    days: int = 7


@app.post("/api/ingest")
async def ingest_data(req: IngestRequest) -> dict:
    """Download historical OHLCV data and store it in the PPMT database."""
    try:
        storage = PPMTStorage()
        collector = DataCollector(exchange=req.exchange, storage=storage)
        df = collector.fetch_and_save(req.symbol, req.timeframe, days=req.days)
        if df is None or df.empty:
            return {"ok": False, "error": "No data fetched"}
        count = len(df)
        if hasattr(collector, 'close'):
            collector.close()
        storage.close()
        return {"ok": True, "symbol": req.symbol, "timeframe": req.timeframe, "candles": count}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# Background Trading Session Management (v0.29.0)
# ------------------------------------------------------------------ #

# Active trading task
_trading_task: Optional[asyncio.Task] = None
_trading_stop_event = asyncio.Event()


class StartTradingRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    exchange: str = "mexc"
    capital: float = 10_000.0
    leverage: int = 1
    auto_mode: bool = True
    max_positions: int = 5
    max_exposure: float = 0.80
    kill_switch_pct: float = 0.95
    daily_loss_pct: float = 0.05
    days_ingest: int = 30
    """How many days of data to ingest before starting."""


@app.post("/api/start-trading")
async def start_trading(req: StartTradingRequest) -> dict:
    """Start a paper trading session in the background.

    v0.31.0: Pre-trade gate — checks validation before allowing trading.
    If no recent validation exists, runs auto-setup first.

    This endpoint performs the full workflow:
    1. Checks pre-trade validation (backtest + MC)
    2. Ingests historical data (if not already present)
    3. Auto-builds Trie (if not already present)
    4. Starts the RealtimeTrader in live/paper mode as a background asyncio task

    The dashboard WebSocket will receive real-time updates automatically.
    """
    global _trading_task

    if _trading_task is not None and not _trading_task.done():
        return {"ok": False, "error": "Trading session already running. Stop it first."}

    # v0.31.0: Pre-trade validation gate
    try:
        storage = PPMTStorage()
        latest_val = storage.get_latest_validation(req.symbol, req.timeframe)
        storage.close()

        if latest_val is None or latest_val.get("verdict") != "PASS":
            # Auto-validate if no recent validation
            val_result = await validate_token(ValidateRequest(
                symbol=req.symbol,
                timeframe=req.timeframe,
                exchange=req.exchange,
                capital=req.capital,
            ))
            if val_result.get("verdict") != "PASS":
                return {
                    "ok": False,
                    "error": f"Pre-trade validation FAILED for {req.symbol} {req.timeframe}",
                    "validation": val_result,
                    "checks": val_result.get("checks", {}),
                }
    except Exception as e:
        logger.warning(f"Pre-trade gate check failed: {e} — proceeding anyway")

    try:
        # Step 1: Auto-ingest data if needed
        storage = PPMTStorage()
        df = storage.load_ohlcv(req.symbol, req.timeframe)
        candles_count = len(df) if df is not None and not df.empty else 0

        if candles_count < 500:
            logger.info(f"Auto-ingesting {req.days_ingest} days of {req.symbol} {req.timeframe} data...")
            try:
                collector = DataCollector(exchange=req.exchange, storage=storage)
                df = collector.fetch_and_save(req.symbol, req.timeframe, days=req.days_ingest)
                candles_count = len(df) if df is not None and not df.empty else 0
                logger.info(f"Ingested {candles_count} candles")
                if hasattr(collector, 'close'):
                    collector.close()
            except Exception as e:
                logger.warning(f"Auto-ingest failed: {e} — continuing with existing data")

        # Step 2: Auto-build Trie if needed
        all_tries = storage.load_all_tries(req.symbol)
        if all_tries.get("n3") is None:
            logger.info(f"Auto-building Trie for {req.symbol} {req.timeframe}...")
            try:
                from ppmt.engine.ppmt import PPMT as PPMTBuilder
                df = storage.load_ohlcv(req.symbol, req.timeframe)
                if df is not None and not df.empty:
                    builder = PPMTBuilder(
                        symbol=req.symbol,
                        sax_strategy="ohlcv",
                    )
                    # Build all 4 levels in one call
                    count = builder.build(df, pattern_length=5)
                    logger.info(f"Built {count} patterns across all 4 trie levels")
                    # Save each trie level individually
                    for level_name, trie_obj in [("n1", builder.trie_n1), ("n2", builder.trie_n2), ("n3", builder.trie_n3), ("n4", builder.trie_n4)]:
                        storage.save_trie(req.symbol, level_name, trie_obj)
                        logger.info(f"Saved {level_name} trie: {trie_obj.pattern_count} patterns")
                    all_tries = storage.load_all_tries(req.symbol)
                else:
                    storage.close()
                    return {"ok": False, "error": f"No data available for {req.symbol} {req.timeframe}. Ingest failed."}
            except Exception as e:
                logger.warning(f"Auto-build failed: {e}")
                storage.close()
                return {"ok": False, "error": f"Auto-build failed: {str(e)}"}

        storage.close()

        # Step 3: Start trading as background task
        _trading_stop_event.clear()

        async def _run_trading():
            """Background trading task."""
            from ppmt.engine.realtime import RealtimeTrader, LiveConfig

            config = LiveConfig(
                symbol=req.symbol,
                timeframe=req.timeframe,
                initial_capital=req.capital,
                exchange=req.exchange,
                dry_run=True,  # Always paper trading from dashboard
                testnet=False,  # v0.29.0: Use mainnet data feed (testnet=404 on Binance WS)
                leverage=req.leverage,
                auto_mode=req.auto_mode,
                max_open_positions=req.max_positions,
                max_portfolio_exposure_pct=req.max_exposure,
                kill_switch_pct=req.kill_switch_pct,
                daily_loss_limit_pct=req.daily_loss_pct,
                use_kelly_sizing=True,
                kelly_fraction=0.25,
                use_token_profile=True,
                auto_calibrate=True,
                regime_aware=True,
                use_multi_level=True,
                living_trie=True,
            )

            trader = RealtimeTrader(config=config)
            try:
                result = await trader.run_live()
                logger.info(f"Trading session ended: {result.total_trades} trades, P&L: {result.total_pnl_pct:.2f}%")
            except Exception as e:
                logger.error(f"Trading session error: {e}")
            finally:
                terminal_state.update_sync(is_running=False, websocket_status="stopped")

        _trading_task = asyncio.create_task(_run_trading())

        return {
            "ok": True,
            "message": f"Paper trading started for {req.symbol} {req.timeframe} on {req.exchange}",
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "candles_available": candles_count,
            "trie_patterns": all_tries.get("n3").pattern_count if all_tries.get("n3") else 0,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/stop-trading")
async def stop_trading() -> dict:
    """Stop the active trading session."""
    global _trading_task

    if _trading_task is None or _trading_task.done():
        return {"ok": False, "error": "No active trading session"}

    _trading_stop_event.set()
    _trading_task.cancel()
    try:
        await asyncio.wait_for(_trading_task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass

    _trading_task = None
    terminal_state.update_sync(is_running=False, websocket_status="stopped")
    return {"ok": True, "message": "Trading session stopped"}


@app.get("/api/trading-status")
async def get_trading_status() -> dict:
    """Check if a trading session is active."""
    is_active = _trading_task is not None and not _trading_task.done()
    return {
        "is_running": is_active,
        "symbol": terminal_state.symbol,
        "timeframe": terminal_state.timeframe,
        "exchange": terminal_state.exchange,
        "candles_processed": terminal_state.candles_processed,
        "total_trades": terminal_state.total_trades,
        "pnl_pct": terminal_state.total_pnl_pct,
    }


# ------------------------------------------------------------------ #
# REST endpoint — Trade History (v0.31.0)
# ------------------------------------------------------------------ #


@app.get("/api/trades")
async def get_trades(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Get closed trade history from persistent storage."""
    try:
        storage = PPMTStorage()
        trades = storage.get_trades(symbol=symbol, timeframe=timeframe, limit=limit)
        summary = storage.get_trade_summary(symbol=symbol)
        storage.close()
        return {"ok": True, "trades": trades, "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/trade-summary")
async def get_trade_summary(symbol: Optional[str] = None) -> dict:
    """Get aggregate trade statistics."""
    try:
        storage = PPMTStorage()
        summary = storage.get_trade_summary(symbol=symbol)
        storage.close()
        return {"ok": True, "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoint — Validate Token (v0.31.0)
# ------------------------------------------------------------------ #


class ValidateRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    exchange: str = "mexc"
    capital: float = 10_000.0


@app.post("/api/validate")
async def validate_token(req: ValidateRequest) -> dict:
    """Run backtest + Monte Carlo to validate a token before trading.

    Returns a verdict (PASS/FAIL) with detailed metrics.
    The pre-trade gate checks:
    - Win Rate > 40%
    - Profit Factor > 0.8
    - Monte Carlo Risk of Ruin < 20%
    - Monte Carlo Verdict != HIGH RISK
    - Min 5 trades in backtest
    """
    try:
        terminal_state.update_sync(
            auto_setup_status={"step": "validating", "message": f"Validating {req.symbol} {req.timeframe}...", "percent": 10}
        )

        # Step 1: Ensure data exists
        storage = PPMTStorage()
        df = storage.load_ohlcv(req.symbol, req.timeframe)
        candles = len(df) if df is not None and not df.empty else 0

        if candles < 500:
            terminal_state.update_sync(
                auto_setup_status={"step": "ingesting", "message": f"Ingesting data for {req.symbol}...", "percent": 20}
            )
            try:
                collector = DataCollector(exchange=req.exchange, storage=storage)
                df = collector.fetch_and_save(req.symbol, req.timeframe, days=30)
                if hasattr(collector, 'close'):
                    collector.close()
            except Exception as e:
                logger.warning(f"Auto-ingest for validation failed: {e}")

        # Step 2: Ensure trie exists
        all_tries = storage.load_all_tries(req.symbol)
        if all_tries.get("n3") is None:
            terminal_state.update_sync(
                auto_setup_status={"step": "building", "message": f"Building Trie for {req.symbol}...", "percent": 40}
            )
            try:
                from ppmt.engine.ppmt import PPMT as PPMTBuilder
                df = storage.load_ohlcv(req.symbol, req.timeframe)
                if df is not None and not df.empty:
                    builder = PPMTBuilder(symbol=req.symbol, sax_strategy="ohlcv")
                    count = builder.build(df, pattern_length=5)
                    for level_name, trie_obj in [("n1", builder.trie_n1), ("n2", builder.trie_n2), ("n3", builder.trie_n3), ("n4", builder.trie_n4)]:
                        storage.save_trie(req.symbol, level_name, trie_obj)
            except Exception as e:
                storage.close()
                return {"ok": False, "error": f"Build failed: {str(e)}"}

        storage.close()

        # Step 3: Run backtest
        terminal_state.update_sync(
            auto_setup_status={"step": "backtesting", "message": f"Running backtest for {req.symbol}...", "percent": 60}
        )
        from ppmt.engine.realtime import RealtimeTrader, ReplayConfig
        config = ReplayConfig(
            symbol=req.symbol,
            timeframe=req.timeframe,
            initial_capital=req.capital,
            speed=0,
            verbose=False,
        )
        trader = RealtimeTrader(config=config)
        result = trader.run_replay()

        # Compute profit factor
        gross_profit = sum(t.pnl_pct for t in result.trades if t.pnl_pct > 0)
        gross_loss = abs(sum(t.pnl_pct for t in result.trades if t.pnl_pct < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Step 4: Run Monte Carlo
        mc_result = {}
        mc_verdict = ""
        terminal_state.update_sync(
            auto_setup_status={"step": "montecarlo", "message": f"Running Monte Carlo for {req.symbol}...", "percent": 80}
        )
        try:
            from ppmt.risk.monte_carlo import MonteCarloSimulator, MonteCarloConfig
            mc_config = MonteCarloConfig(initial_capital=req.capital)
            mc_sim = MonteCarloSimulator(config=mc_config)
            trades_pnl = [t.pnl_pct / 100.0 for t in result.trades]  # Convert to fraction
            if len(trades_pnl) >= 5:
                mc_out = mc_sim.simulate(trades_pnl)
                # Compute simple verdict from risk_of_ruin
                if mc_out.risk_of_ruin < 0.05:
                    mc_verdict = "LOW RISK"
                elif mc_out.risk_of_ruin < 0.15:
                    mc_verdict = "MODERATE RISK"
                else:
                    mc_verdict = "HIGH RISK"
                mc_result = {
                    "risk_of_ruin": mc_out.risk_of_ruin,
                    "probability_of_profit": mc_out.probability_of_profit,
                    "p95_max_drawdown": mc_out.p95_max_drawdown,
                    "mean_final_equity": mc_out.mean_final_equity,
                    "median_final_equity": mc_out.median_final_equity,
                    "verdict": mc_verdict,
                    "risk_score": mc_out.risk_of_ruin * 100,
                }
        except Exception as e:
            logger.warning(f"Monte Carlo failed: {e}")

        # Step 5: Compute verdict
        checks = {
            "win_rate_pass": result.win_rate > 0.40,
            "profit_factor_pass": profit_factor > 0.8,
            "risk_of_ruin_pass": mc_result.get("risk_of_ruin", 1.0) < 0.20,
            "mc_verdict_pass": mc_verdict not in ("HIGH RISK",),
            "min_trades_pass": result.total_trades >= 5,
        }
        all_pass = all(checks.values())
        verdict = "PASS" if all_pass else "FAIL"

        val_result = {
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "verdict": verdict,
            "win_rate": result.win_rate,
            "profit_factor": profit_factor,
            "risk_of_ruin": mc_result.get("risk_of_ruin", 1.0),
            "p95_drawdown": mc_result.get("p95_max_drawdown", 1.0),
            "total_trades": result.total_trades,
            "backtest_pnl_pct": result.total_pnl_pct,
            "mc_probability_profit": mc_result.get("probability_of_profit", 0),
            "mc_verdict": mc_verdict,
            "checks": checks,
            "details": {
                "backtest": {
                    "total_trades": result.total_trades,
                    "win_rate": result.win_rate,
                    "total_pnl_pct": result.total_pnl_pct,
                    "max_drawdown": result.max_drawdown,
                },
                "monte_carlo": mc_result,
            },
        }

        # Save to DB
        try:
            storage = PPMTStorage()
            storage.save_validation(val_result)
            storage.close()
        except Exception:
            pass

        # Update terminal state
        terminal_state.update_sync(
            validation_result=val_result,
            auto_setup_status={"step": "done", "message": f"Validation: {verdict}", "percent": 100}
        )

        return {"ok": True, **val_result}

    except Exception as e:
        terminal_state.update_sync(
            auto_setup_status={"step": "error", "message": str(e), "percent": 0}
        )
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoint — Auto-Setup (v0.31.0)
# ------------------------------------------------------------------ #


class AutoSetupRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    exchange: str = "mexc"
    capital: float = 10_000.0
    days_ingest: int = 30


@app.post("/api/auto-setup")
async def auto_setup(req: AutoSetupRequest) -> dict:
    """Full auto-setup pipeline: ingest → build → calibrate → validate.

    This is the 1-click "Prepare Token" button.
    Runs the complete pipeline and returns validation results.
    """
    # Simply delegate to validate, which already does the full pipeline
    val_req = ValidateRequest(
        symbol=req.symbol,
        timeframe=req.timeframe,
        exchange=req.exchange,
        capital=req.capital,
    )
    return await validate_token(val_req)


# ------------------------------------------------------------------ #
# REST endpoint — Portfolio Backtest (v0.32.0 Multi-Token)
# ------------------------------------------------------------------


class PortfolioBacktestRequest(BaseModel):
    symbols: list[str] = ["BTC/USDT", "ETH/USDT"]
    timeframe: str = "1h"
    capital: float = 10_000.0
    exchange: str = "mexc"


@app.post("/api/portfolio-backtest")
async def run_portfolio_backtest(req: PortfolioBacktestRequest) -> dict:
    """Run a multi-token portfolio backtest.

    Runs individual backtests for each symbol and combines results
    with portfolio-level statistics including correlation awareness.
    """
    try:
        from ppmt.engine.realtime import RealtimeTrader, ReplayConfig

        results = {}
        all_equity = []
        total_pnl = 0.0
        total_trades = 0
        total_wins = 0

        per_symbol_capital = req.capital / len(req.symbols)

        for symbol in req.symbols:
            config = ReplayConfig(
                symbol=symbol,
                timeframe=req.timeframe,
                initial_capital=per_symbol_capital,
                speed=0,
                verbose=False,
            )
            trader = RealtimeTrader(config=config)
            result = trader.run_replay()

            results[symbol] = {
                "total_trades": result.total_trades,
                "win_rate": result.win_rate,
                "total_pnl_pct": result.total_pnl_pct,
                "max_drawdown": result.max_drawdown,
                "final_capital": result.final_capital,
                "equity_curve": result.equity_curve[-100:] if result.equity_curve else [],
            }

            total_pnl += (result.final_capital - per_symbol_capital)
            total_trades += result.total_trades
            total_wins += result.winning_trades

            if result.equity_curve:
                all_equity.append(result.equity_curve)

        # Portfolio-level stats
        portfolio_pnl_pct = ((req.capital + total_pnl) / req.capital - 1) * 100 if req.capital > 0 else 0
        portfolio_win_rate = total_wins / total_trades if total_trades > 0 else 0

        return {
            "ok": True,
            "symbols": req.symbols,
            "total_capital": req.capital,
            "total_pnl": total_pnl,
            "portfolio_pnl_pct": portfolio_pnl_pct,
            "total_trades": total_trades,
            "portfolio_win_rate": portfolio_win_rate,
            "per_symbol": results,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoint — Multi-Token Auto-Setup (v0.32.0)
# ------------------------------------------------------------------


class MultiSetupRequest(BaseModel):
    symbols: list[str] = ["BTC/USDT"]
    timeframe: str = "1h"
    exchange: str = "mexc"
    capital: float = 10_000.0


@app.post("/api/multi-setup")
async def multi_setup(req: MultiSetupRequest) -> dict:
    """Auto-setup multiple tokens at once. Creates a child node for each."""
    results = []
    pm = _get_parent_manager()

    per_symbol_alloc = 1.0 / len(req.symbols) if req.symbols else 0.20

    for symbol in req.symbols:
        # Run validation for each symbol
        val_req = ValidateRequest(
            symbol=symbol,
            timeframe=req.timeframe,
            exchange=req.exchange,
            capital=req.capital / len(req.symbols),
        )
        val_result = await validate_token(val_req)

        # Auto-create node if validation passes
        node_id = f"{symbol.split('/')[0].lower()}_{req.timeframe}"
        if val_result.get("verdict") == "PASS" and node_id not in pm._children:
            from ppmt.risk.money_manager import ChildNodeConfig
            cfg = ChildNodeConfig(
                node_id=node_id,
                symbol=symbol,
                timeframe=req.timeframe,
                capital_allocation_pct=per_symbol_alloc,
                leverage=1,
                auto_mode=True,
            )
            try:
                pm.register_child(cfg)
            except Exception:
                pass

        results.append({
            "symbol": symbol,
            "verdict": val_result.get("verdict", "UNKNOWN"),
            "win_rate": val_result.get("win_rate", 0),
            "profit_factor": val_result.get("profit_factor", 0),
        })

    # Distribute capital after adding all nodes
    try:
        pm.distribute_capital()
        _save_parent_manager()
    except Exception:
        pass

    return {"ok": True, "results": results}


# ------------------------------------------------------------------ #
# REST endpoint — Multi-Timeframe Analysis (v0.32.0)
# ------------------------------------------------------------------


class MultiTFRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframes: list[str] = ["1h", "5m"]
    exchange: str = "mexc"
    capital: float = 10_000.0


@app.post("/api/multi-tf-analysis")
async def multi_tf_analysis(req: MultiTFRequest) -> dict:
    """Run analysis across multiple timeframes for confluence scoring.

    For each timeframe:
    1. Ensure data exists (auto-ingest if needed)
    2. Run backtest
    3. Collect signals

    Then compute confluence: signals that agree across timeframes
    get a higher confluence score.
    """
    try:
        from ppmt.engine.realtime import RealtimeTrader, ReplayConfig

        tf_results = {}
        signal_map = {}  # timeframe -> list of signals

        for tf in req.timeframes:
            # Ensure data
            storage = PPMTStorage()
            df = storage.load_ohlcv(req.symbol, tf)
            candles = len(df) if df is not None and not df.empty else 0

            if candles < 200:
                try:
                    collector = DataCollector(exchange=req.exchange, storage=storage)
                    days = {"1m": 1, "5m": 3, "15m": 7, "1h": 30, "4h": 60, "1d": 180}.get(tf, 30)
                    collector.fetch_and_save(req.symbol, tf, days=days)
                    if hasattr(collector, 'close'):
                        collector.close()
                except Exception:
                    pass

            # Ensure trie
            all_tries = storage.load_all_tries(req.symbol)
            if all_tries.get("n3") is None:
                try:
                    from ppmt.engine.ppmt import PPMT as PPMTBuilder
                    df = storage.load_ohlcv(req.symbol, tf)
                    if df is not None and not df.empty:
                        builder = PPMTBuilder(symbol=req.symbol, sax_strategy="ohlcv")
                        builder.build(df, pattern_length=5)
                        for level_name, trie_obj in [("n1", builder.trie_n1), ("n2", builder.trie_n2), ("n3", builder.trie_n3), ("n4", builder.trie_n4)]:
                            storage.save_trie(req.symbol, level_name, trie_obj)
                except Exception:
                    pass

            storage.close()

            # Run backtest for this timeframe
            config = ReplayConfig(
                symbol=req.symbol,
                timeframe=tf,
                initial_capital=req.capital / len(req.timeframes),
                speed=0,
                verbose=False,
            )
            trader = RealtimeTrader(config=config)
            result = trader.run_replay()

            # Collect signal directions
            signals = []
            for t in result.trades:
                signals.append({
                    "direction": t.direction,
                    "pnl_pct": t.pnl_pct,
                    "entry_time": t.entry_time,
                })

            tf_results[tf] = {
                "total_trades": result.total_trades,
                "win_rate": result.win_rate,
                "total_pnl_pct": result.total_pnl_pct,
                "max_drawdown": result.max_drawdown,
                "signals": signals[-20:],
            }
            signal_map[tf] = signals

        # Compute confluence: how many TFs agree on direction
        # Simple approach: check if latest signal direction agrees
        latest_directions = {}
        for tf, sigs in signal_map.items():
            if sigs:
                latest_directions[tf] = sigs[-1]["direction"]

        directions = list(latest_directions.values())
        if directions:
            longs = sum(1 for d in directions if d == "LONG")
            shorts = sum(1 for d in directions if d == "SHORT")
            total = len(directions)
            if longs > shorts:
                confluence_direction = "LONG"
                confluence_score = longs / total
            elif shorts > longs:
                confluence_direction = "SHORT"
                confluence_score = shorts / total
            else:
                confluence_direction = "NEUTRAL"
                confluence_score = 0.5
        else:
            confluence_direction = "NEUTRAL"
            confluence_score = 0.0

        return {
            "ok": True,
            "symbol": req.symbol,
            "timeframes": req.timeframes,
            "confluence": {
                "direction": confluence_direction,
                "score": confluence_score,
                "agreement": latest_directions,
            },
            "per_timeframe": tf_results,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# Pre-trade gate (v0.31.0) — integrated into start_trading
# ------------------------------------------------------------------ #


# ------------------------------------------------------------------ #
# WebSocket endpoint
# ------------------------------------------------------------------ #


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Real-time WebSocket endpoint for live state updates."""
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info("WebSocket client connected (total: %d)", len(_ws_clients))
    try:
        while True:
            snapshot = terminal_state.to_dict()
            # Add nodes data to snapshot
            try:
                pm = _get_parent_manager()
                snapshot["nodes"] = {
                    "total_capital": pm.total_capital,
                    "reserve_capital": pm.reserve_capital,
                    "kill_switch_active": pm._global_kill_switch,
                    "children": [
                        {
                            "node_id": cfg.node_id,
                            "symbol": cfg.symbol,
                            "timeframe": cfg.timeframe,
                            "capital_allocation_pct": cfg.capital_allocation_pct,
                            "leverage": cfg.leverage,
                            "auto_mode": cfg.auto_mode,
                            "enabled": cfg.enabled,
                        }
                        for cfg in pm._children.values()
                    ],
                }
            except Exception:
                snapshot["nodes"] = None

            try:
                await websocket.send_json(snapshot)
            except Exception:
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)
        logger.info("WebSocket client disconnected (total: %d)", len(_ws_clients))


async def _broadcast_state() -> None:
    """Broadcast current state to all connected WebSocket clients."""
    if not _ws_clients:
        return
    snapshot = terminal_state.to_dict()
    stale: list[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_json(snapshot)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _ws_clients.discard(ws)


# ------------------------------------------------------------------ #
# Server runner
# ------------------------------------------------------------------ #


def run_server(host: str = "0.0.0.0", port: int = 8420) -> None:
    """Run the terminal dashboard server."""
    import uvicorn

    logger.info("Starting PPMT Terminal Dashboard on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


# ------------------------------------------------------------------ #
# Fallback HTML (minimal, used only if static/index.html is missing)
# ------------------------------------------------------------------ #
_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PPMT Terminal</title>
<style>
body{background:#0d1117;color:#c9d1d9;font-family:monospace;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
h1{color:#58a6ff}
</style>
</head>
<body><h1>PPMT Terminal — Dashboard file not found</h1></body>
</html>"""
