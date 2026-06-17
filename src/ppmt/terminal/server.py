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
import time
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
app = FastAPI(title="PPMT Terminal", version="0.38.0")

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
    exchange: str = "binance",
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
    exchange: str = "binance",
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
async def get_market_symbols(exchange: str = "binance", limit: int = 500) -> dict:
    """Get available trading symbols from exchange.

    v0.32.5: Filters out leveraged/derivative tokens (1000X, 3L, 3S, 5L, 5S,
    UP, DOWN, BULL, BEAR) that aren't suitable for the PPMT pattern engine.
    Returns up to `limit` symbols (default 500, was 100). This makes the
    dropdown actually contain the major tokens the user expects (AAVE, ADA,
    AVAX, DOGE, etc.) instead of being filled with "1000BONK/USDT"-style
    leveraged tokens that sort first alphabetically.
    """
    try:
        import ccxt
        ex = getattr(ccxt, exchange, None)
        if ex is None:
            return {"ok": False, "error": f"Exchange '{exchange}' not found"}
        exc = ex()
        try:
            markets = exc.load_markets()
            # v0.32.5: Filter out leveraged/derivative tokens
            usdt_pairs = []
            for s in markets.keys():
                if not s.endswith("/USDT"):
                    continue
                if not markets[s].get("active", True):
                    continue

                base = s[:-5]  # strip "/USDT"
                # Skip leveraged tokens (MEXC: 1000X, 3L/3S, 5L/5S; Binance: UP/DOWN, BULL/BEAR)
                if base.startswith(("1000", "10000", "1BULL", "3L", "3S", "5L", "5S")):
                    continue
                if base.endswith(("UP", "DOWN", "BULL", "BEAR")) and len(base) > 4:
                    continue
                usdt_pairs.append(s)

            usdt_pairs.sort()

            # v0.32.5: Return up to `limit` symbols (was hard-coded 100)
            return {"ok": True, "exchange": exchange, "symbols": usdt_pairs[:limit],
                    "total_available": len(usdt_pairs)}
        finally:
            if hasattr(exc, 'close'):
                exc.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


class IngestRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1m"
    exchange: str = "binance"
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
    exchange: str = "binance"
    capital: float = 10_000.0
    leverage: int = 1
    auto_mode: bool = True
    max_positions: int = 5
    max_exposure: float = 0.80
    kill_switch_pct: float = 0.95
    daily_loss_pct: float = 0.05
    # v0.32.3: default 90 days (was 30) — same rationale as AutoSetupRequest
    days_ingest: int = 90
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
            v = val_result.get("verdict")
            if v != "PASS":
                if v == "INSUFFICIENT_DATA":
                    return {
                        "ok": False,
                        "error": f"Cannot trade {req.symbol} {req.timeframe}: backtest produced 0 trades. "
                                 f"Ingest more historical data or try a different timeframe.",
                        "validation": val_result,
                        "checks": val_result.get("checks", {}),
                    }
                else:
                    return {
                        "ok": False,
                        "error": f"Pre-trade validation FAILED for {req.symbol} {req.timeframe}. "
                                 f"Token did not pass safety checks (WR/PF/RoR).",
                        "validation": val_result,
                        "checks": val_result.get("checks", {}),
                    }
    except Exception as e:
        logger.warning(f"Pre-trade gate check failed: {e} — proceeding anyway")

    try:
        # v0.34.3: Reset terminal state so the new session doesn't show stale
        # pattern_buffer / signals / equity_curve from a previous session.
        # Without this, the dashboard shows 30 'n' (SAX neutral) symbols from
        # the previous run forever, even after Stop+Start.
        terminal_state.reset()
        terminal_state.update_sync(
            symbol=req.symbol,
            timeframe=req.timeframe,
            exchange=req.exchange,
            is_running=True,
            websocket_status="connecting",
            mode="paper",
            started_at=time.time(),
            capital=req.capital,
            leverage=req.leverage,
            auto_mode=req.auto_mode,
        )

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
                # v0.34.3: collector.close() no longer closes the shared storage.
                if hasattr(collector, 'close'):
                    collector.close()
            except Exception as e:
                logger.warning(f"Auto-ingest failed: {e} — continuing with existing data")

        # v0.34.3: Defensive — re-open storage if it was closed by a child.
        if storage.conn is None:
            try:
                storage._reconnect()
            except Exception:
                storage = PPMTStorage()

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
# REST endpoint — Multi-Token Trading (v0.36.0)
# ------------------------------------------------------------------ #
# v0.36.0: True multi-token server-side trading. Each token gets its own
# background asyncio task running a RealtimeTrader instance. The frontend
# Trading tab polls /api/multi-status to display live per-token state.
#
# Replaces the v0.35.0 hack where _activeTradeTokens was just an in-memory
# JS list and "Start" only ran autoSetup() on the first token.

# Map: node_id -> {task, config, started_at, last_price, pnl_pct, signals,
#                  trades, status, error}
_multi_sessions: dict = {}


class MultiStartRequest(BaseModel):
    """v0.36.0: Start multiple paper-trading sessions in parallel."""
    tokens: list[dict] = []
    """List of {symbol, timeframe, exchange} dicts. Empty list = use parent
    manager's registered child nodes."""
    capital: float = 10_000.0
    """Total capital to split across all tokens."""
    leverage: int = 1
    auto_mode: bool = True
    days_ingest: int = 90


@app.post("/api/multi-start")
async def multi_start(req: MultiStartRequest) -> dict:
    """v0.36.0: Start N concurrent paper-trading sessions (one per token).

    Each token gets its own asyncio task that:
      1. Auto-validates (skip if already PASS in DB)
      2. Auto-ingests data (if < 500 candles)
      3. Auto-builds Trie (if missing)
      4. Starts RealtimeTrader.run_live() in dry_run mode

    Returns immediately with the list of sessions launched.
    """
    tokens = list(req.tokens)
    if not tokens:
        return {"ok": False, "error": "No tokens provided"}

    launched = []
    from ppmt.engine.realtime import RealtimeTrader, LiveConfig
    for t in tokens:
        sym = t.get("symbol", "").strip().upper()
        tf = t.get("timeframe", "1h")
        exch = t.get("exchange", "binance").lower()
        if not sym:
            continue
        if "/" not in sym:
            # Accept "BTCUSDT" or "BTC" → normalize to "BTC/USDT"
            if sym.endswith("USDT"):
                sym = sym[:-4] + "/USDT"
            else:
                sym = sym + "/USDT"

        node_id = f"{sym.split('/')[0].lower()}_{tf}"

        # Skip if already running
        existing = _multi_sessions.get(node_id)
        if existing and not existing.get("task").done():
            launched.append({"node_id": node_id, "symbol": sym, "status": "ALREADY_RUNNING"})
            continue

        # Per-token capital (even split)
        per_capital = req.capital / max(len(tokens), 1)

        config = LiveConfig(
            symbol=sym,
            timeframe=tf,
            initial_capital=per_capital,
            exchange=exch,
            dry_run=True,
            testnet=False,
            leverage=req.leverage,
            auto_mode=req.auto_mode,
            use_token_profile=True,
            auto_calibrate=True,
            regime_aware=True,
            use_multi_level=True,
            living_trie=True,
        )

        session_state = {
            "node_id": node_id,
            "symbol": sym,
            "timeframe": tf,
            "exchange": exch,
            "started_at": time.time(),
            "status": "STARTING",
            "last_price": 0.0,
            "pnl_pct": 0.0,
            "signals": 0,
            "trades": 0,
            "candles_processed": 0,
            "error": "",
            # v0.36.2: extra live state for richer UI
            "regime": "",
            "pattern_buffer": [],
            "entropy": 0.0,
            "websocket_status": "disconnected",
            "is_running": False,
            "portfolio_value": 0.0,
            "win_rate": 0.0,
            "exposure_pct": 0.0,
            "validation_verdict": "",
            "last_update_ts": 0.0,
        }

        async def _run_one_token(_sym=sym, _tf=tf, _exch=exch, _cfg=config, _nid=node_id):
            sess = _multi_sessions[_nid]
            try:
                # Auto-validate first (non-blocking gate)
                storage = PPMTStorage()
                latest_val = storage.get_latest_validation(_sym, _tf)
                storage.close()

                if latest_val is None or latest_val.get("verdict") != "PASS":
                    sess["status"] = "VALIDATING"
                    val_result = await validate_token(ValidateRequest(
                        symbol=_sym, timeframe=_tf, exchange=_exch, capital=_cfg.initial_capital,
                    ))
                    verdict = val_result.get("verdict", "UNKNOWN")
                    sess["validation_verdict"] = verdict
                    if verdict != "PASS":
                        sess["status"] = "VALIDATION_FAILED"
                        sess["error"] = (
                            f"Validation: {verdict} — "
                            f"{val_result.get('reason', val_result.get('error', ''))}"[:200]
                        )
                        return

                # v0.36.2: Per-session state callback — bridges trader updates to
                # this session's dict so /api/multi-status returns real values
                # instead of all zeros. Also drives granular status transitions.
                def _state_cb(_nid=_nid, **kwargs):
                    s = _multi_sessions.get(_nid)
                    if s is None:
                        return
                    if "current_price" in kwargs:
                        s["last_price"] = float(kwargs["current_price"] or 0)
                    if "total_pnl_pct" in kwargs:
                        s["pnl_pct"] = float(kwargs["total_pnl_pct"] or 0)
                    elif "portfolio_value" in kwargs and _cfg.initial_capital > 0:
                        # Fallback: derive pct from portfolio value vs initial capital
                        pv = float(kwargs["portfolio_value"] or 0)
                        s["pnl_pct"] = ((pv - _cfg.initial_capital) / _cfg.initial_capital) * 100.0
                    if "candles_processed" in kwargs:
                        s["candles_processed"] = int(kwargs["candles_processed"] or 0)
                    if "total_trades" in kwargs:
                        s["trades"] = int(kwargs["total_trades"] or 0)
                    if "sax_symbols_produced" in kwargs:
                        s["signals"] = int(kwargs["sax_symbols_produced"] or 0)
                    if "regime" in kwargs and kwargs["regime"]:
                        s["regime"] = kwargs["regime"]
                    if "pattern_buffer" in kwargs:
                        s["pattern_buffer"] = list(kwargs["pattern_buffer"] or [])[-30:]
                    if "entropy" in kwargs:
                        s["entropy"] = float(kwargs["entropy"] or 0)
                    if "websocket_status" in kwargs and kwargs["websocket_status"]:
                        s["websocket_status"] = kwargs["websocket_status"]
                    if "is_running" in kwargs:
                        s["is_running"] = bool(kwargs["is_running"])
                    if "portfolio_value" in kwargs:
                        s["portfolio_value"] = float(kwargs["portfolio_value"] or 0)
                    if "win_rate" in kwargs:
                        s["win_rate"] = float(kwargs["win_rate"] or 0)
                    if "exposure_pct" in kwargs:
                        s["exposure_pct"] = float(kwargs["exposure_pct"] or 0)
                    s["last_update_ts"] = time.time()
                    # Status transitions driven by what we just learned
                    if kwargs.get("is_running") and s["status"] in ("STARTING_TRADER", "STARTING"):
                        s["status"] = "RUNNING"
                    elif (kwargs.get("websocket_status") == "connecting"
                          and s["status"] in ("STARTING_TRADER", "STARTING")):
                        s["status"] = "CONNECTING"
                    elif (kwargs.get("websocket_status") == "warming_up"
                          and s["status"] in ("STARTING_TRADER", "STARTING", "CONNECTING")):
                        s["status"] = "WARMING_UP"

                sess["status"] = "STARTING_TRADER"
                trader = RealtimeTrader(config=_cfg, state_callback=_state_cb)
                # run_live() runs until cancelled
                await trader.run_live()
                sess["status"] = "STOPPED"
                sess["is_running"] = False
            except asyncio.CancelledError:
                sess["status"] = "STOPPED"
                sess["is_running"] = False
                raise
            except Exception as e:
                logger.error(f"Multi-session {_nid} error: {e}", exc_info=True)
                sess["status"] = "ERROR"
                sess["error"] = str(e)[:200]
                sess["is_running"] = False

        task = asyncio.create_task(_run_one_token())
        _multi_sessions[node_id] = {**session_state, "task": task, "config": config}
        launched.append({"node_id": node_id, "symbol": sym, "status": "LAUNCHED"})

    return {
        "ok": True,
        "launched": launched,
        "total_active": sum(1 for s in _multi_sessions.values() if not s["task"].done()),
    }


@app.get("/api/multi-status")
async def multi_status() -> dict:
    """v0.36.0: Live status of all multi-token trading sessions.

    v0.36.2: Now also returns regime, pattern_buffer, entropy, websocket_status,
    portfolio_value, win_rate, exposure_pct, and last_update_ts per session.
    """
    sessions = []
    now = time.time()
    for node_id, sess in _multi_sessions.items():
        is_active = not sess["task"].done()
        # v0.36.2: Stale detection — if a session claims to be RUNNING but
        # hasn't updated state in 60s, mark as STALE so the UI can surface it.
        last_update = sess.get("last_update_ts", 0)
        status = sess["status"]
        if is_active and status == "RUNNING" and last_update > 0 and (now - last_update) > 60:
            status = "STALE"
        sessions.append({
            "node_id": node_id,
            "symbol": sess["symbol"],
            "timeframe": sess["timeframe"],
            "exchange": sess["exchange"],
            "started_at": sess["started_at"],
            "status": status if is_active else (
                "STOPPED" if sess["status"] in ("STARTING_TRADER", "RUNNING", "CONNECTING", "WARMING_UP") else sess["status"]
            ),
            "last_price": sess.get("last_price", 0.0),
            "pnl_pct": sess.get("pnl_pct", 0.0),
            "signals": sess.get("signals", 0),
            "trades": sess.get("trades", 0),
            "candles_processed": sess.get("candles_processed", 0),
            "error": sess.get("error", ""),
            "is_active": is_active,
            "uptime_seconds": (now - sess["started_at"]) if is_active else 0,
            # v0.36.2: new fields
            "regime": sess.get("regime", ""),
            "pattern_buffer": sess.get("pattern_buffer", [])[-30:],
            "entropy": sess.get("entropy", 0.0),
            "websocket_status": sess.get("websocket_status", "disconnected"),
            "portfolio_value": sess.get("portfolio_value", 0.0),
            "win_rate": sess.get("win_rate", 0.0),
            "exposure_pct": sess.get("exposure_pct", 0.0),
            "validation_verdict": sess.get("validation_verdict", ""),
            "last_update_ts": last_update,
            "seconds_since_update": (now - last_update) if last_update > 0 else 0,
        })
    return {
        "ok": True,
        "sessions": sessions,
        "total": len(sessions),
        "active": sum(1 for s in sessions if s["is_active"]),
    }


@app.post("/api/multi-stop")
async def multi_stop(node_id: str = "") -> dict:
    """v0.36.0: Stop one (by node_id) or all multi-token trading sessions."""
    stopped = []
    if node_id:
        sess = _multi_sessions.get(node_id)
        if sess and not sess["task"].done():
            sess["task"].cancel()
            try:
                await asyncio.wait_for(sess["task"], timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            sess["status"] = "STOPPED"
            stopped.append(node_id)
    else:
        # Stop all
        for nid, sess in list(_multi_sessions.items()):
            if not sess["task"].done():
                sess["task"].cancel()
                try:
                    await asyncio.wait_for(sess["task"], timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                sess["status"] = "STOPPED"
                stopped.append(nid)
    return {"ok": True, "stopped": stopped}


@app.delete("/api/multi-remove")
async def multi_remove(node_id: str) -> dict:
    """v0.36.0: Remove a multi-token session from the registry (stops first)."""
    sess = _multi_sessions.get(node_id)
    if sess is None:
        return {"ok": False, "error": f"Session '{node_id}' not found"}
    if not sess["task"].done():
        sess["task"].cancel()
        try:
            await asyncio.wait_for(sess["task"], timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    del _multi_sessions[node_id]
    return {"ok": True, "removed": node_id}


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


class ClearHistoryRequest(BaseModel):
    """v0.37.0: Request body for clearing stale trade/signal history."""
    symbol: Optional[str] = None  # None = clear ALL symbols
    older_than_days: int = 0      # 0 = clear all matching
    clear_trades: bool = True
    clear_signals: bool = True


@app.post("/api/clear-history")
async def clear_history(req: ClearHistoryRequest) -> dict:
    """v0.37.0: Clear stale trade/signal history from SQLite.

    Use case: The dashboard's Trade History panel was showing 434 fake
    BTC trades at $38,452 / $41,940 / $50,919 from a previous backtest,
    even while the user was trading XLM/OP/ICP/INJ at $0.23 / $0.11.
    This endpoint lets the user clear that stale data.

    Args (in body):
        symbol: If provided, only clear this symbol. If None, clear ALL.
        older_than_days: If >0, only clear rows older than N days.
        clear_trades: Whether to clear the trades table (default True).
        clear_signals: Whether to clear the signals table (default True).

    Returns:
        {ok: True, trades_deleted: N, signals_deleted: N}
    """
    try:
        storage = PPMTStorage()
        trades_deleted = 0
        signals_deleted = 0
        if req.clear_trades:
            trades_deleted = storage.clear_trades(
                symbol=req.symbol, older_than_days=req.older_than_days,
            )
        if req.clear_signals:
            signals_deleted = storage.clear_signals(
                symbol=req.symbol, older_than_days=req.older_than_days,
            )
        storage.close()
        return {
            "ok": True,
            "trades_deleted": trades_deleted,
            "signals_deleted": signals_deleted,
            "message": (
                f"Cleared {trades_deleted} trades and {signals_deleted} signals"
                + (f" for {req.symbol}" if req.symbol else " (all symbols)")
                + (f" older than {req.older_than_days} days" if req.older_than_days > 0 else "")
            ),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoint — Validate Token (v0.31.0)
# ------------------------------------------------------------------ #


class ValidateRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    exchange: str = "binance"
    capital: float = 10_000.0


def _days_for_tf(timeframe: str, default: int = 180) -> int:
    """v0.33.0: Crypto 24/7 needs more data than forex. Calibrated so each TF
    produces >=500 candles (the warning threshold) AND captures enough market
    regimes (weekends, macro events, halving cycles) for robust backtests.

    TF   | Days | Candles (approx)  | Justification
    -----|------|--------------------|----------------------------------------
    1m   |  7   | 10,080             | 1 semana captura fines de semana
    5m   | 30   |  8,640             | 1 mes para capturar ciclos
    10m  | 45   |  6,480             | 6 semanas
    15m  | 90   |  8,640             | 3 meses para tendencias medias
    30m  | 120  |  5,760             | 4 meses
    1h   | 180  |  4,320             | 6 meses, captura macro + halving
    4h   | 365  |  2,190             | 1 año, fiable para swing
    1d   | 730  |    730             | 2 años, ciclos crypto
    """
    return {
        "1m": 7, "3m": 14, "5m": 30, "10m": 45, "15m": 90, "30m": 120,
        "1h": 180, "2h": 240, "4h": 365, "6h": 540, "12h": 730,
        "1d": 730, "1w": 1825,
    }.get(timeframe, default)


def _min_candles_for_tf(timeframe: str) -> int:
    """v0.33.0: Minimum candle count for a 'reliable' backtest on this TF.

    Below this threshold, the dashboard shows a warning:
    '⚠️ Muestra insuficiente. Resultados poco fiables.'
    """
    return 500


def _candle_count_warning(candles: int, timeframe: str) -> Optional[str]:
    """Return a warning string if candle count is below the reliability threshold."""
    threshold = _min_candles_for_tf(timeframe)
    if candles < threshold:
        return (
            f"⚠️ Muestra insuficiente ({candles} < {threshold} velas). "
            f"Resultados poco fiables para TF={timeframe}."
        )
    return None


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
    # v0.32.6: Per-token status tagging. The frontend now filters
    # auto_setup_status / validation_result by symbol+timeframe so switching
    # tokens no longer re-renders stale state from a previous token's run.
    _status_token = {"symbol": req.symbol, "timeframe": req.timeframe,
                     "exchange": req.exchange}

    try:
        terminal_state.update_sync(
            auto_setup_status={**_status_token,
                               "step": "validating", "status": "running",
                               "message": f"Validating {req.symbol} {req.timeframe}...",
                               "percent": 10}
        )

        # Step 1: Ensure data exists
        storage = PPMTStorage()
        df = storage.load_ohlcv(req.symbol, req.timeframe)
        candles = len(df) if df is not None and not df.empty else 0

        if candles < 500:
            terminal_state.update_sync(
                auto_setup_status={**_status_token,
                                   "step": "ingesting", "status": "running",
                                   "message": f"Ingesting data for {req.symbol}...",
                                   "percent": 20}
            )
            try:
                collector = DataCollector(exchange=req.exchange, storage=storage)
                # v0.32.6: TF-aware day defaults. 1h needs >=180d to give the
                # trie enough patterns to reach the 5-trade MC threshold.
                # Shorter TFs need less because each day produces more candles.
                days = _days_for_tf(req.timeframe, default=180)
                df = collector.fetch_and_save(req.symbol, req.timeframe, days=days)
                # v0.34.3: collector.close() no longer closes the shared storage
                # (collector now tracks _owns_storage). Safe to call.
                if hasattr(collector, 'close'):
                    collector.close()
            except Exception as e:
                logger.warning(f"Auto-ingest for validation failed: {e}")

        # v0.34.3: Defensive — if storage was somehow closed, re-open it.
        # This guards against any future regression in the collector lifecycle.
        if storage.conn is None:
            try:
                storage._reconnect()
            except Exception:
                storage = PPMTStorage()  # fallback: create a fresh instance

        # Step 2: Ensure trie exists
        all_tries = storage.load_all_tries(req.symbol)
        if all_tries.get("n3") is None:
            terminal_state.update_sync(
                auto_setup_status={**_status_token,
                                   "step": "building", "status": "running",
                                   "message": f"Building Trie for {req.symbol}...",
                                   "percent": 40}
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
            auto_setup_status={**_status_token,
                               "step": "backtesting", "status": "running",
                               "message": f"Running backtest for {req.symbol}...",
                               "percent": 60}
        )
        from ppmt.engine.realtime import RealtimeTrader, ReplayConfig
        config = ReplayConfig(
            symbol=req.symbol,
            timeframe=req.timeframe,
            initial_capital=req.capital,
            speed=0,
            verbose=False,
            # v0.32.3: validation_mode relaxes v0.25.0 strict signal filters so
            # the backtest can produce enough trades to reach the 5-trade MC
            # threshold. Without this, Bayesian shrinkage + regime filters reject
            # almost all signals in short (30-day) backtests → 0-2 trades → FAIL.
            validation_mode=True,
        )
        trader = RealtimeTrader(config=config)
        result = trader.run_replay()

        logger.info(
            f"Backtest {req.symbol} {req.timeframe}: "
            f"trades={result.total_trades}, WR={result.win_rate:.1%}, "
            f"PnL={result.total_pnl_pct:+.2f}%, DD={result.max_drawdown:.2%}"
        )

        # Compute profit factor
        gross_profit = sum(t.pnl_pct for t in result.trades if t.pnl_pct > 0)
        gross_loss = abs(sum(t.pnl_pct for t in result.trades if t.pnl_pct < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Step 4: Run Monte Carlo
        mc_result = {}
        mc_verdict = ""
        terminal_state.update_sync(
            auto_setup_status={**_status_token,
                               "step": "montecarlo", "status": "running",
                               "message": f"Running Monte Carlo for {req.symbol}...",
                               "percent": 80}
        )
        try:
            from ppmt.risk.monte_carlo import MonteCarloSimulator, MonteCarloConfig
            mc_config = MonteCarloConfig(initial_capital=req.capital)
            mc_sim = MonteCarloSimulator()  # No args in constructor (v0.32.0 fix)
            trades_pnl = [t.pnl_pct / 100.0 for t in result.trades]  # Convert to fraction
            if len(trades_pnl) >= 5:
                mc_out = mc_sim.simulate(trades_pnl, config=mc_config)  # config passed here
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
            logger.warning(f"Monte Carlo failed: {e}", exc_info=True)

        # Step 5: Compute verdict
        # v0.32.3: If backtest produced < 5 trades, mark as INSUFFICIENT_DATA
        # (previously only 0 trades → INSUFFICIENT_DATA; 1-4 trades went to FAIL
        # because MC was skipped → risk_of_ruin defaulted to 1.0 → check failed).
        if result.total_trades < 5:
            verdict = "INSUFFICIENT_DATA"
            reason_msg = (
                f"Backtest produced only {result.total_trades} trades (need >= 5). "
                f"Causes: (a) insufficient historical data, (b) overly strict signal "
                f"filters for this regime/market, (c) TokenProfile calibration issue. "
                f"Try: longer history (90+ days), different timeframe, or check trie patterns."
            )
            # v0.32.3: checks dict uses BOTH naming conventions for dashboard compat:
            #   - `win_rate_pass` (server original) — used by Python code/tests
            #   - `win_rate`      (dashboard expects) — used by index.html JS
            checks = {
                "win_rate_pass": False,
                "profit_factor_pass": False,
                "risk_of_ruin_pass": False,
                "mc_verdict_pass": False,
                "min_trades_pass": False,
                # Dashboard-friendly aliases (simple names without `_pass` suffix)
                "win_rate": False,
                "profit_factor": False,
                "risk_of_ruin": False,
                "mc_verdict": False,
                "min_trades": False,
                "reason": reason_msg,
            }
            # v0.32.3: Diagnostic logging — print which check failed and why
            logger.warning(
                f"Validation {req.symbol} {req.timeframe}: INSUFFICIENT_DATA "
                f"(trades={result.total_trades} < 5, signals={result.signals_generated}, "
                f"candles_processed={result.candles_processed})"
            )
        else:
            wr_pass = result.win_rate > 0.40
            pf_pass = profit_factor > 0.8
            ror_pass = mc_result.get("risk_of_ruin", 1.0) < 0.20
            mc_pass = mc_verdict not in ("HIGH RISK",)
            mt_pass = result.total_trades >= 5

            checks = {
                # Server-style keys (with _pass suffix)
                "win_rate_pass": wr_pass,
                "profit_factor_pass": pf_pass,
                "risk_of_ruin_pass": ror_pass,
                "mc_verdict_pass": mc_pass,
                "min_trades_pass": mt_pass,
                # v0.32.3: Dashboard-friendly aliases (simple names).
                # Dashboard's checkNames = ['win_rate','profit_factor','risk_of_ruin','mc_verdict','min_trades']
                "win_rate": wr_pass,
                "profit_factor": pf_pass,
                "risk_of_ruin": ror_pass,
                "mc_verdict": mc_pass,
                "min_trades": mt_pass,
            }
            all_pass = wr_pass and pf_pass and ror_pass and mc_pass and mt_pass
            verdict = "PASS" if all_pass else "FAIL"

            # v0.32.3: Detailed per-check diagnostic logging
            if not all_pass:
                failed = [k for k, v in checks.items() if k.endswith("_pass") and not v]
                logger.warning(
                    f"Validation {req.symbol} {req.timeframe}: FAIL. "
                    f"Failed checks: {failed}. "
                    f"Metrics: WR={result.win_rate:.1%} ({'PASS' if wr_pass else 'FAIL'}), "
                    f"PF={profit_factor:.2f} ({'PASS' if pf_pass else 'FAIL'}), "
                    f"RoR={mc_result.get('risk_of_ruin', 1.0):.2%} ({'PASS' if ror_pass else 'FAIL'}), "
                    f"MC={mc_verdict or 'N/A'} ({'PASS' if mc_pass else 'FAIL'}), "
                    f"Trades={result.total_trades} ({'PASS' if mt_pass else 'FAIL'})"
                )
            else:
                logger.info(
                    f"Validation {req.symbol} {req.timeframe}: PASS. "
                    f"WR={result.win_rate:.1%}, PF={profit_factor:.2f}, "
                    f"RoR={mc_result.get('risk_of_ruin', 0):.2%}, "
                    f"MC={mc_verdict}, Trades={result.total_trades}"
                )

        # v0.32.3: Backtest/MC summary dicts at top level (for dashboard compatibility).
        # Dashboard reads `vr.backtest` and `vr.monte_carlo` (top-level), not `vr.details.backtest`.
        backtest_summary = {
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "total_pnl_pct": result.total_pnl_pct,
            "max_drawdown": result.max_drawdown,
            "signals_generated": result.signals_generated,
            "candles_processed": result.candles_processed,
            "pnl": result.total_pnl_pct,  # alias for dashboard `bt.pnl`
            "net_profit": result.total_pnl,
            "trades": result.total_trades,  # alias for dashboard `bt.trades`
        }
        monte_carlo_summary = {
            **mc_result,
            # v0.32.3: Add aliases for dashboard field names
            "prob_of_profit": mc_result.get("probability_of_profit", 0),
            "p95_drawdown": mc_result.get("p95_max_drawdown", 0),
            "p95_dd": mc_result.get("p95_max_drawdown", 0),
        }

        val_result = {
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "verdict": verdict,
            # v0.32.3: `passed` field — dashboard checks `vr.passed || vr.valid`.
            # Without this, dashboard ALWAYS shows FAIL even when verdict=PASS.
            "passed": verdict == "PASS",
            "valid": verdict == "PASS",
            "win_rate": result.win_rate,  # numeric value (top-level)
            "profit_factor": profit_factor,  # numeric value (top-level)
            "risk_of_ruin": mc_result.get("risk_of_ruin", 1.0) if result.total_trades >= 5 else 1.0,
            "p95_drawdown": mc_result.get("p95_max_drawdown", 1.0) if result.total_trades >= 5 else 1.0,
            "total_trades": result.total_trades,
            "backtest_pnl_pct": result.total_pnl_pct,
            # v0.33.0: Candle-count warning for low-TF samples
            "candles_processed": result.candles_processed,
            "candle_warning": _candle_count_warning(result.candles_processed, req.timeframe),
            "mc_probability_profit": mc_result.get("probability_of_profit", 0),
            "mc_verdict": mc_verdict,
            "checks": checks,
            # v0.32.3: Top-level backtest/monte_carlo (dashboard reads these)
            "backtest": backtest_summary,
            "monte_carlo": monte_carlo_summary,
            "details": {
                "backtest": backtest_summary,
                "monte_carlo": monte_carlo_summary,
            },
        }

        # Save to DB
        try:
            storage = PPMTStorage()
            storage.save_validation(val_result)
            storage.close()
        except Exception:
            pass

        # Update terminal state — v0.32.6: tag with symbol+timeframe so the
        # frontend can filter out stale state from a different token.
        val_result["symbol"] = req.symbol
        val_result["timeframe"] = req.timeframe
        terminal_state.update_sync(
            validation_result=val_result,
            auto_setup_status={**_status_token,
                               "step": "done", "status": "done",
                               "message": f"Validation: {verdict}",
                               "percent": 100,
                               "verdict": verdict}
        )

        return {"ok": True, **val_result}

    except Exception as e:
        # v0.34.2: log full traceback para que el usuario pueda ver en el
        # servidor qué falló en cada token del sweep (antes era silencioso).
        logger.error(
            f"validate_token failed for {req.symbol} {req.timeframe}: {e}",
            exc_info=True,
        )
        terminal_state.update_sync(
            auto_setup_status={**_status_token,
                               "step": "error", "status": "error",
                               "message": str(e), "percent": 0}
        )
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoint — Auto-Setup (v0.31.0)
# ------------------------------------------------------------------ #


class AutoSetupRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    exchange: str = "binance"
    capital: float = 10_000.0
    # v0.32.3: default 90 days (was 30). 30d only gives ~720 1h candles,
    # barely enough to build a meaningful trie and reach the 5-trade threshold.
    days_ingest: int = 90


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
    exchange: str = "binance"


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
    exchange: str = "binance"
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
# REST endpoint — Sweep All Tokens (v0.32.6)
# ------------------------------------------------------------------ #

# Global sweep state
_sweep_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_symbol": "",
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "results": [],  # list of {symbol, verdict, win_rate, profit_factor, total_trades, error?}
    "started_at": 0.0,
    "finished_at": 0.0,
    "error": "",
    "group_id": "",
    "filters": {},
}


# ------------------------------------------------------------------ #
# REST endpoints — Token Groups (v0.33.0)
# ------------------------------------------------------------------ #


@app.get("/api/groups")
async def list_token_groups() -> dict:
    """v0.33.0: List all available token groups (predefined + dynamic + custom).

    Returns:
      { "groups": { group_id: { label, category, description, bases? }, ... } }
    """
    try:
        from ppmt.data.groups import list_groups
        return {"ok": True, "groups": list_groups()}
    except Exception as e:
        logger.warning(f"list_token_groups failed: {e}", exc_info=True)
        return {"ok": False, "error": str(e), "groups": {}}


@app.get("/api/groups/resolve")
async def resolve_token_group(
    group_id: str = "top25_mcap",
    exchange: str = "binance",
    exclude_stablecoins: bool = True,
    only_usdt_pairs: bool = True,
    min_volume_24h_usd: float = 0,
    min_volatility_pct: float = 0,
    min_listed_days: int = 0,
    limit: int = 50,
) -> dict:
    """v0.33.0: Resolve a group ID to the actual list of symbols.

    v0.36.1: Also returns ``raw_count`` (number of bases defined in the group
    before exchange filtering) and ``filtered_count`` (after exchange filter
    but before limit) so the UI can show "X of Y tokens on exchange".

    Returns:
      { "ok": True, "group_id": str, "symbols": [...], "count": int,
        "raw_count": int, "filtered_count": int, "filters_applied": dict }
    """
    try:
        from ppmt.data.groups import (
            resolve_group,
            PREDEFINED_STATIC_GROUPS,
            _load_custom_groups,
        )
        filters = {
            "exclude_stablecoins": exclude_stablecoins,
            "only_usdt_pairs": only_usdt_pairs,
            "min_volume_24h_usd": min_volume_24h_usd,
            "min_volatility_pct": min_volatility_pct,
            "min_listed_days": min_listed_days,
            "limit": limit,
        }

        # Compute raw_count = number of unique bases defined for this group
        raw_count = 0
        if group_id in PREDEFINED_STATIC_GROUPS:
            raw_count = len(set(PREDEFINED_STATIC_GROUPS[group_id].get("bases", [])))
        else:
            custom = _load_custom_groups()
            if group_id in custom:
                raw_count = len(set(custom[group_id].get("bases", [])))

        symbols = resolve_group(group_id, exchange=exchange, filters=filters)

        # filtered_count = how many would be available WITHOUT the user's limit cap
        filtered_count = raw_count
        if limit and limit > 0:
            filtered_count = min(raw_count, max(len(symbols), 0))
        # If we can re-resolve with limit=0 to get true count, do it
        try:
            no_limit_filters = dict(filters)
            no_limit_filters["limit"] = 0
            no_limit_syms = resolve_group(group_id, exchange=exchange, filters=no_limit_filters)
            filtered_count = len(no_limit_syms)
        except Exception:
            pass

        return {
            "ok": True,
            "group_id": group_id,
            "exchange": exchange,
            "symbols": symbols,
            "count": len(symbols),
            "raw_count": raw_count,
            "filtered_count": filtered_count,
            "filters_applied": filters,
        }
    except Exception as e:
        logger.warning(f"resolve_token_group failed: {e}", exc_info=True)
        return {"ok": False, "error": str(e), "symbols": [], "count": 0}


class SaveCustomGroupRequest(BaseModel):
    name: str
    symbols: list[str]
    description: str = ""


@app.post("/api/groups/custom")
async def save_custom_group_endpoint(req: SaveCustomGroupRequest) -> dict:
    """v0.33.0: Save a custom group to ~/.ppmt/groups_config.json."""
    try:
        from ppmt.data.groups import save_custom_group
        ok = save_custom_group(req.name, req.symbols, req.description)
        if not ok:
            return {"ok": False, "error": "Invalid name or symbols (or reserved name)"}
        return {"ok": True, "name": req.name, "count": len(req.symbols)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.delete("/api/groups/custom")
async def delete_custom_group_endpoint(name: str) -> dict:
    """v0.33.0: Delete a custom group."""
    try:
        from ppmt.data.groups import delete_custom_group
        ok = delete_custom_group(name)
        if not ok:
            return {"ok": False, "error": f"Group '{name}' not found"}
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ #
# REST endpoint — Sweep All Tokens (v0.32.6)
# ------------------------------------------------------------------ #

# (Sweep state above)


class SweepRequest(BaseModel):
    """Run validation across many tokens in the background.

    v0.33.0: Now accepts ``group_id`` and ``filters`` so the dashboard can
    sweep a dynamic group (e.g. Top 25 by Volume) instead of the hard-coded
    25-majors list. If both ``symbols`` and ``group_id`` are provided,
    ``symbols`` wins (caller already resolved the group).

    v0.33.1: ``sweep_all_groups`` triggers a sequential sweep across EVERY
    group returned by ``list_groups()``. Useful for weekly reports: lets you
    run a full inventory of the entire universe overnight and collect every
    PASS token in one consolidated results table.
    """
    symbols: list[str] = []
    group_id: str = ""  # e.g. "top25_mcap", "memes", "top_volume_24h", or a custom group name
    filters: dict = {}  # optional: {min_volume_24h_usd, exclude_stablecoins, limit, ...}
    timeframe: str = "1h"
    exchange: str = "binance"
    capital: float = 1_000.0
    skip_if_pass: bool = True
    """v0.32.6: If True, skip tokens that already have a PASS validation in DB."""
    sweep_all_groups: bool = False
    """v0.33.1: If True, ignore symbols/group_id and iterate over ALL groups
    returned by list_groups(). Each token is validated once (deduplicated).
    Useful for weekly consolidation reports."""
    all_groups_categories: list[str] = []
    """v0.33.1: Optional filter — only sweep groups whose `category` is in
    this list. Empty = all categories (market_cap, category, dynamic, custom)."""


@app.post("/api/sweep")
async def sweep_tokens(req: SweepRequest) -> dict:
    """v0.32.6 / v0.33.0: Sweep many tokens — run validation on each, in background.

    The endpoint returns immediately with the planned list; the dashboard
    polls `/api/sweep-status` to see live progress. Each token that PASSES
    is auto-added as a child node so the user can immediately start trading.

    v0.33.0: Resolution priority for the symbol list:
      1. req.symbols (caller already resolved)
      2. req.group_id (resolve now via groups.resolve_group, applying req.filters)
      3. Fall back to curated 25 majors (legacy behaviour)
    """
    global _sweep_state

    if _sweep_state["running"]:
        return {"ok": False, "error": "A sweep is already running. Wait for it to finish."}

    # Resolve symbol list
    # v0.33.1: sweep_all_groups → union of all groups' symbols (deduplicated)
    symbols = list(req.symbols) if req.symbols else []
    resolved_group = ""

    if req.sweep_all_groups:
        try:
            from ppmt.data.groups import list_groups as _lg, resolve_group as _rg
            all_groups = _lg()
            cat_filter = set(req.all_groups_categories) if req.all_groups_categories else None
            seen = set(symbols)  # don't drop user-supplied symbols
            resolved_groups_list = []
            for gid, gdef in all_groups.items():
                if cat_filter and gdef.get("category") not in cat_filter:
                    continue
                try:
                    syms = _rg(gid, exchange=req.exchange, filters=req.filters or None)
                except Exception as e:
                    logger.warning(f"Sweep-all: group '{gid}' failed: {e}")
                    syms = []
                resolved_groups_list.append(gid)
                for s in syms:
                    if s not in seen:
                        seen.add(s)
                        symbols.append(s)
            resolved_group = f"ALL ({len(resolved_groups_list)} groups, {len(symbols)} unique symbols)"
            logger.info(f"Sweep-all: {len(resolved_groups_list)} groups → {len(symbols)} unique symbols")
        except Exception as e:
            logger.warning(f"Sweep-all-groups failed: {e}")

    elif not symbols and req.group_id:
        try:
            from ppmt.data.groups import resolve_group
            symbols = resolve_group(
                req.group_id, exchange=req.exchange, filters=req.filters or None,
            )
            resolved_group = req.group_id
            logger.info(f"Sweep: resolved group '{req.group_id}' -> {len(symbols)} symbols")
        except Exception as e:
            logger.warning(f"Sweep: group resolution failed: {e}")
            symbols = []

    if not symbols:
        # v0.32.6: If user didn't pass a list, use the curated majors list.
        symbols = [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
            "ADA/USDT", "AVAX/USDT", "LINK/USDT", "BNB/USDT", "DOT/USDT",
            "LTC/USDT", "BCH/USDT", "ATOM/USDT", "UNI/USDT", "AAVE/USDT",
            "NEAR/USDT", "APT/USDT", "FIL/USDT", "ARB/USDT", "OP/USDT",
            "INJ/USDT", "SUI/USDT", "TIA/USDT", "SEI/USDT", "RUNE/USDT",
        ]

    # Filter out tokens we can skip (already PASS)
    if req.skip_if_pass:
        try:
            storage = PPMTStorage()
            skipped = []
            to_run = []
            for s in symbols:
                v = storage.get_latest_validation(s, req.timeframe)
                if v and v.get("verdict") == "PASS":
                    skipped.append(s)
                    _sweep_state["results"].append({
                        "symbol": s, "verdict": "PASS",
                        "win_rate": v.get("win_rate", 0),
                        "profit_factor": v.get("profit_factor", 0),
                        "total_trades": v.get("total_trades", 0),
                        "skipped": True,
                    })
                else:
                    to_run.append(s)
            storage.close()
            _sweep_state["skipped"] = len(skipped)
            symbols = to_run
        except Exception as e:
            logger.warning(f"Sweep skip-check failed: {e}")

    # Reset state
    _sweep_state = {
        "running": True,
        "total": len(symbols),
        "done": 0,
        "current_symbol": symbols[0] if symbols else "",
        "passed": 0,
        "failed": 0,
        "skipped": _sweep_state.get("skipped", 0),
        "results": list(_sweep_state.get("results", [])),  # preserve skipped
        "started_at": time.time(),
        "finished_at": 0.0,
        "error": "",
        "group_id": resolved_group,
        "filters": req.filters or {},
    }

    if not symbols:
        _sweep_state["running"] = False
        _sweep_state["finished_at"] = time.time()
        return {"ok": True, "message": "All tokens already validated.", "total": 0}

    # Launch background task
    asyncio.create_task(_sweep_runner(symbols, req.timeframe, req.exchange, req.capital))

    return {
        "ok": True,
        "message": f"Sweeping {len(symbols)} tokens in the background.",
        "total": len(symbols),
        "skipped": _sweep_state["skipped"],
    }


async def _sweep_runner(symbols: list[str], timeframe: str, exchange: str, capital: float) -> None:
    """Background task that validates each token sequentially."""
    global _sweep_state
    pm = _get_parent_manager()
    per_alloc = 0.10  # cap at 10% per token to avoid over-allocation

    for sym in symbols:
        if not _sweep_state["running"]:
            break  # cancelled
        _sweep_state["current_symbol"] = sym
        try:
            val_req = ValidateRequest(
                symbol=sym, timeframe=timeframe, exchange=exchange, capital=capital,
            )
            # validate_token is async — call directly
            val_result = await validate_token(val_req)

            # v0.34.2: Si validate_token devolvió {ok: False, error: ...} sin
            # verdict (excepción capturada dentro), marcar como FAIL con el
            # error visible. Antes se marcaba como UNKNOWN silenciosamente.
            if not val_result.get("ok", True) and "verdict" not in val_result:
                err_msg = val_result.get("error", "Unknown validation error")
                logger.warning(f"Sweep validation for {sym} returned error: {err_msg}")
                _sweep_state["failed"] += 1
                _sweep_state["results"].append({
                    "symbol": sym,
                    "verdict": "FAIL",
                    "win_rate": 0,
                    "profit_factor": 0,
                    "total_trades": 0,
                    "max_drawdown": 0,
                    "risk_of_ruin": 1.0,
                    "error": err_msg[:200],  # truncate to avoid huge UI
                })
                _sweep_state["done"] += 1
                await asyncio.sleep(0.1)
                continue

            verdict = val_result.get("verdict", "FAIL")  # default FAIL, no UNKNOWN
            entry = {
                "symbol": sym,
                "verdict": verdict,
                "win_rate": val_result.get("win_rate", 0),
                "profit_factor": val_result.get("profit_factor", 0),
                "total_trades": val_result.get("total_trades", 0),
                "max_drawdown": (val_result.get("backtest") or {}).get("max_drawdown", 0),
                "risk_of_ruin": val_result.get("risk_of_ruin", 0),
            }
            if verdict == "PASS":
                _sweep_state["passed"] += 1
                # Auto-add as child node if not present
                node_id = f"{sym.split('/')[0].lower()}_{timeframe}"
                if node_id not in pm._children:
                    from ppmt.risk.money_manager import ChildNodeConfig
                    try:
                        cfg = ChildNodeConfig(
                            node_id=node_id, symbol=sym, timeframe=timeframe,
                            capital_allocation_pct=per_alloc, leverage=1, auto_mode=True,
                        )
                        pm.register_child(cfg)
                    except Exception:
                        pass
            elif verdict == "INSUFFICIENT_DATA":
                _sweep_state["skipped"] += 1
                entry["skipped"] = True
            else:
                _sweep_state["failed"] += 1
            _sweep_state["results"].append(entry)
        except Exception as e:
            logger.warning(f"Sweep validation for {sym} raised: {e}", exc_info=True)
            _sweep_state["failed"] += 1
            _sweep_state["results"].append({
                "symbol": sym, "verdict": "FAIL", "error": str(e)[:200],
                "win_rate": 0, "profit_factor": 0, "total_trades": 0,
            })
        _sweep_state["done"] += 1
        # Yield between tokens so the event loop can process WS / other requests
        await asyncio.sleep(0.1)

    try:
        pm.distribute_capital()
        _save_parent_manager()
    except Exception:
        pass

    # v0.36.1: Persist sweep results to SQLite history so the user can review
    # past sweeps from the History tab. This was missing — save_scan() existed
    # but was never called from the sweep runner, so "history" was always empty.
    try:
        from ppmt.terminal.history_manager import save_scan
        resultados_for_db = []
        for r in _sweep_state.get("results", []):
            resultados_for_db.append({
                "symbol": r.get("symbol", ""),
                "resultado": r.get("verdict", "FAIL"),
                "win_rate": r.get("win_rate", 0),
                "profit_factor": r.get("profit_factor", 0),
                "sharpe": 0.0,  # not currently returned by validate_token
                "max_drawdown": r.get("max_drawdown", 0),
                "total_trades": r.get("total_trades", 0),
                "grupo": "sweep",
                "cached": False,
            })
        if resultados_for_db:
            started_at = _sweep_state.get("started_at") or time.time()
            finished_at = _sweep_state.get("finished_at") or time.time()
            save_scan(
                grupo_utilizado=f"sweep ({len(symbols)} tokens)",
                tf_utilizado=timeframe,
                resultados=resultados_for_db,
                filtros_aplicados={"exchange": exchange, "capital": capital},
                dias_data=0,
                tiempo_ejecucion=round(finished_at - started_at, 2),
            )
    except Exception as e:
        logger.warning(f"save_scan failed at end of sweep (non-fatal): {e}")

    _sweep_state["running"] = False
    _sweep_state["finished_at"] = time.time()
    _sweep_state["current_symbol"] = ""
    logger.info(
        f"Sweep complete: {_sweep_state['passed']} PASS, "
        f"{_sweep_state['failed']} FAIL, "
        f"{_sweep_state['skipped']} skipped "
        f"({_sweep_state['done']}/{_sweep_state['total']} run)"
    )


@app.get("/api/sweep-status")
async def sweep_status() -> dict:
    """v0.32.6: Live progress for the background sweep."""
    return dict(_sweep_state)


@app.post("/api/sweep-cancel")
async def sweep_cancel() -> dict:
    """v0.32.6: Cancel a running sweep."""
    global _sweep_state
    if not _sweep_state["running"]:
        return {"ok": False, "error": "No sweep running"}
    _sweep_state["running"] = False
    return {"ok": True, "message": "Sweep will stop after the current token."}


# ------------------------------------------------------------------ #
# REST endpoints — History (v0.36.1)
# ------------------------------------------------------------------ #

@app.get("/api/history/scans")
async def history_list_scans(limit: int = 20) -> dict:
    """List recent sweeps saved in the SQLite history DB."""
    try:
        from ppmt.terminal.history_manager import list_scans
        rows = list_scans(limit=limit)
        return {"ok": True, "scans": rows}
    except Exception as e:
        return {"ok": False, "error": str(e), "scans": []}


@app.get("/api/history/scans/{scan_id}")
async def history_get_scan(scan_id: int) -> dict:
    """Get a full scan with all per-token results."""
    try:
        from ppmt.terminal.history_manager import get_scan
        scan = get_scan(scan_id)
        if scan is None:
            return {"ok": False, "error": "scan_id not found"}
        return {"ok": True, "scan": scan}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/history/symbol/{symbol}")
async def history_by_symbol(symbol: str, limit: int = 20) -> dict:
    """Get validation history for a single symbol across all past scans."""
    try:
        from ppmt.terminal.history_manager import list_by_symbol
        rows = list_by_symbol(symbol, limit=limit)
        return {"ok": True, "rows": rows}
    except Exception as e:
        return {"ok": False, "error": str(e), "rows": []}


@app.get("/api/history/today")
async def history_today() -> dict:
    """Get scans run today."""
    try:
        from ppmt.terminal.history_manager import list_today
        rows = list_today()
        return {"ok": True, "scans": rows}
    except Exception as e:
        return {"ok": False, "error": str(e), "scans": []}


# ------------------------------------------------------------------ #
# REST endpoint — Multi-Timeframe Analysis (v0.32.0)
# ------------------------------------------------------------------


class MultiTFRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframes: list[str] = ["1h", "5m"]
    exchange: str = "binance"
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


# ------------------------------------------------------------------ #
# Module entry point — allows `python -m ppmt.terminal.server`
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PPMT Terminal Dashboard Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default 0.0.0.0)")
    parser.add_argument("--port", "-p", default=8420, type=int, help="Bind port (default 8420)")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
