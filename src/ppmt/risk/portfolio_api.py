"""
Portfolio API Bridge - REST API Server for PPMT Portfolio Manager

Provides a FastAPI server that exposes the Python PortfolioManager,
CrossTokenCorrelationEngine, and RegimeAwareAllocator to the
Next.js dashboard via REST endpoints.

The bridge runs as a sidecar process alongside the Next.js app:
  ppmt portfolio-serve --port 8430

The Next.js dashboard calls these endpoints to get real-time
portfolio data, correlation matrices, and allocation recommendations.

Endpoints:
  GET  /api/portfolio/state          - Full portfolio state
  GET  /api/portfolio/summary        - Portfolio summary
  GET  /api/portfolio/risk           - Risk report
  GET  /api/portfolio/positions      - All open positions
  GET  /api/portfolio/correlation    - Correlation matrix
  GET  /api/portfolio/diversification - Diversification score
  GET  /api/portfolio/allocation     - Current allocation
  POST /api/portfolio/rebalance      - Trigger rebalance
  POST /api/portfolio/kill-switch    - Activate kill switch
  DELETE /api/portfolio/kill-switch  - Deactivate kill switch
  GET  /api/portfolio/alerts         - Correlation alerts
  GET  /api/portfolio/backtest       - Run portfolio backtest
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# FastAPI is optional — the bridge only works if fastapi is installed
try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig
from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine, CorrelationMethod
from ppmt.risk.regime_allocator import RegimeAwareAllocator
from ppmt.data.classifier import AssetClassifier


# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------

_portfolio: Optional[PortfolioManager] = None
_correlation: Optional[CrossTokenCorrelationEngine] = None
_allocator: Optional[RegimeAwareAllocator] = None
_classifier = AssetClassifier()

# Config directory for state persistence
CONFIG_DIR = os.path.expanduser("~/.ppmt")
STATE_FILE = os.path.join(CONFIG_DIR, "portfolio_state.json")


def get_portfolio() -> PortfolioManager:
    """Get or create the global PortfolioManager instance."""
    global _portfolio
    if _portfolio is None:
        config = PortfolioConfig()
        # Try to load saved state
        if os.path.exists(STATE_FILE):
            config.state_file = STATE_FILE
        _portfolio = PortfolioManager(config=config)
        if os.path.exists(STATE_FILE):
            _portfolio.load_state()
    return _portfolio


def get_correlation() -> CrossTokenCorrelationEngine:
    """Get or create the global CrossTokenCorrelationEngine instance."""
    global _correlation
    if _correlation is None:
        pm = get_portfolio()
        _correlation = CrossTokenCorrelationEngine(
            tokens=list(pm._slots.keys()),
            window=60,
        )
    return _correlation


def get_allocator() -> RegimeAwareAllocator:
    """Get or create the global RegimeAwareAllocator instance."""
    global _allocator
    if _allocator is None:
        _allocator = RegimeAwareAllocator()
    return _allocator


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

def create_app() -> "FastAPI":
    """Create the FastAPI application."""
    if not HAS_FASTAPI:
        raise ImportError(
            "FastAPI is not installed. Install with: pip install fastapi uvicorn"
        )

    app = FastAPI(
        title="PPMT Portfolio API",
        description="Portfolio management REST API for PPMT Terminal",
        version="0.16.0",
    )

    # CORS for Next.js dashboard
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict to dashboard origin
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------
    # Portfolio State
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/state")
    async def get_state():
        """Get full portfolio state including all slots and positions."""
        pm = get_portfolio()
        return pm.get_portfolio_summary()

    @app.get("/api/portfolio/summary")
    async def get_summary():
        """Get compact portfolio summary."""
        pm = get_portfolio()
        summary = pm.get_portfolio_summary()
        # Compact version — no slot details
        return {
            "total_value": summary["total_value"],
            "initial_capital": summary["initial_capital"],
            "total_pnl": summary["total_pnl"],
            "total_pnl_pct": summary["total_pnl_pct"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "exposure_pct": summary["exposure_pct"],
            "open_positions": summary["open_positions"],
            "dominant_regime": summary["dominant_regime"],
            "kill_switch": summary["kill_switch"],
            "drawdown_pct": summary["drawdown_pct"],
        }

    @app.get("/api/portfolio/risk")
    async def get_risk():
        """Get portfolio risk report."""
        pm = get_portfolio()
        return pm.get_risk_report()

    # -------------------------------------------------------------------
    # Positions
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/positions")
    async def get_positions(symbol: Optional[str] = Query(None)):
        """Get all open positions, optionally filtered by token."""
        pm = get_portfolio()
        positions = []
        for slot in pm.all_slots:
            if symbol and slot.symbol != symbol:
                continue
            if slot.risk_manager:
                for pos in slot.risk_manager.open_positions:
                    positions.append({
                        "symbol": pos.symbol,
                        "direction": pos.direction,
                        "entry_price": pos.entry_price,
                        "size": pos.size,
                        "sl_price": pos.sl_price,
                        "tp_price": pos.tp_price,
                        "quality_score": pos.quality_score,
                        "sizing_multiplier": pos.sizing_multiplier,
                        "unrealized_pnl_pct": round(pos.unrealized_pnl_pct, 2),
                        "signal_confidence": pos.signal_confidence,
                        "asset_class": slot.asset_class,
                    })
        return {"positions": positions, "count": len(positions)}

    # -------------------------------------------------------------------
    # Correlation
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/correlation")
    async def get_correlation_matrix(
        method: str = Query("PEARSON", enum=["PEARSON", "SPEARMAN"]),
    ):
        """Get current cross-token correlation matrix."""
        corr = get_correlation()
        corr._method = CorrelationMethod(method)
        result = corr.compute_matrix()
        return result.to_dict()

    @app.get("/api/portfolio/diversification")
    async def get_diversification():
        """Get portfolio diversification score."""
        corr = get_correlation()
        return corr.compute_diversification_score()

    @app.get("/api/portfolio/alerts")
    async def get_alerts(limit: int = Query(20)):
        """Get recent correlation alerts."""
        corr = get_correlation()
        return {"alerts": corr.get_alerts(limit=limit)}

    # -------------------------------------------------------------------
    # Allocation
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/allocation")
    async def get_allocation():
        """Get current portfolio allocation."""
        pm = get_portfolio()
        slots = pm.all_slots
        total = pm.total_value if pm.total_value > 0 else 1.0

        return {
            "method": pm.config.allocation_method,
            "tokens": [
                {
                    "symbol": s.symbol,
                    "asset_class": s.asset_class,
                    "allocated": s.capital_allocated,
                    "used": s.capital_used,
                    "weight": round(s.capital_allocated / total, 4),
                    "pnl": s.total_pnl,
                    "win_rate": s.win_rate,
                    "regime": s.current_regime,
                    "active": s.is_active,
                }
                for s in slots
            ],
        }

    @app.post("/api/portfolio/allocation/compute")
    async def compute_allocation(
        regime: str = Query("UNKNOWN"),
        correlation_regime: str = Query("NORMAL"),
    ):
        """Compute recommended allocation for a given regime."""
        pm = get_portfolio()
        allocator = get_allocator()

        current_alloc = {
            sym: slot.capital_allocated
            for sym, slot in pm._slots.items()
        }
        perf_data = {
            sym: {"win_rate": slot.win_rate, "pnl_pct": slot.pnl_pct, "trades": slot.trades_completed}
            for sym, slot in pm._slots.items()
        }
        quality_data = {
            sym: 0.5 + slot.win_rate * 0.3
            for sym, slot in pm._slots.items()
        }

        result = allocator.allocate(
            regime=regime,
            tokens=list(pm._slots.keys()),
            total_capital=pm.total_value,
            current_allocations=current_alloc,
            token_performance=perf_data,
            pattern_quality=quality_data,
            correlation_regime=correlation_regime,
            portfolio_drawdown_pct=pm.current_drawdown_pct,
        )

        return {
            "regime": result.regime,
            "total_allocated": result.total_allocated,
            "cash_reserve": result.cash_reserve,
            "position_size_multiplier": result.position_size_multiplier,
            "max_exposure": result.max_exposure,
            "instructions": [
                {
                    "symbol": instr.symbol,
                    "target_weight": instr.target_weight,
                    "target_capital": instr.target_capital,
                    "current_capital": instr.current_capital,
                    "capital_delta": instr.capital_delta,
                    "reasoning": instr.reasoning,
                }
                for instr in result.instructions
            ],
        }

    # -------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------

    @app.post("/api/portfolio/rebalance")
    async def rebalance(reason: str = Query("api_request")):
        """Trigger a portfolio rebalance."""
        pm = get_portfolio()
        result = pm.rebalance(reason=reason)
        pm.save_state()

        return {
            "success": True,
            "regime": result.regime,
            "moves": result.capital_moves,
            "allocations_before": result.allocations_before,
            "allocations_after": result.allocations_after,
            "reason": result.reason,
        }

    @app.post("/api/portfolio/kill-switch")
    async def activate_kill_switch():
        """Activate the portfolio kill switch (emergency)."""
        pm = get_portfolio()
        pm.activate_kill_switch()
        pm.save_state()
        return {"success": True, "message": "Kill switch activated — all positions closed"}

    @app.delete("/api/portfolio/kill-switch")
    async def deactivate_kill_switch():
        """Deactivate the portfolio kill switch (manual recovery)."""
        pm = get_portfolio()
        pm.deactivate_kill_switch()
        return {"success": True, "message": "Kill switch deactivated — trading resumed"}

    # -------------------------------------------------------------------
    # Token Management
    # -------------------------------------------------------------------

    @app.post("/api/portfolio/tokens/{symbol}")
    async def add_token(symbol: str, capital: Optional[float] = Query(None)):
        """Add a token to the portfolio."""
        pm = get_portfolio()
        slot = pm.add_token(symbol, capital)
        pm.save_state()
        return {"success": True, "slot": slot.to_dict()}

    @app.delete("/api/portfolio/tokens/{symbol}")
    async def remove_token(symbol: str):
        """Remove a token from the portfolio."""
        pm = get_portfolio()
        slot = pm.remove_token(symbol)
        if slot is None:
            raise HTTPException(status_code=400, detail="Cannot remove token (has positions or not found)")
        pm.save_state()
        return {"success": True}

    @app.post("/api/portfolio/tokens/{symbol}/activate")
    async def activate_token(symbol: str):
        """Activate a token slot for trading."""
        pm = get_portfolio()
        if pm.activate_slot(symbol):
            return {"success": True}
        raise HTTPException(status_code=404, detail="Token not found")

    @app.post("/api/portfolio/tokens/{symbol}/deactivate")
    async def deactivate_token(symbol: str):
        """Deactivate a token slot (won't open new positions)."""
        pm = get_portfolio()
        if pm.deactivate_slot(symbol):
            return {"success": True}
        raise HTTPException(status_code=404, detail="Token not found")

    # -------------------------------------------------------------------
    # Regime
    # -------------------------------------------------------------------

    @app.post("/api/portfolio/regime/{symbol}")
    async def update_regime(symbol: str, regime: str = Query(...)):
        """Update the market regime for a token."""
        pm = get_portfolio()
        pm.update_regime(symbol, regime)
        return {"success": True, "symbol": symbol, "regime": regime}

    # -------------------------------------------------------------------
    # Health
    # -------------------------------------------------------------------

    @app.get("/api/portfolio/health")
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "ok",
            "version": "0.16.0",
            "tokens": len(get_portfolio()._slots),
            "positions": get_portfolio().total_open_positions,
        }

    return app


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def serve(host: str = "0.0.0.0", port: int = 8430) -> None:
    """Start the Portfolio API server."""
    if not HAS_FASTAPI:
        print("ERROR: FastAPI not installed. Install with: pip install fastapi uvicorn")
        sys.exit(1)

    import uvicorn

    app = create_app()
    print(f"PPMT Portfolio API v0.16.0 starting on {host}:{port}")
    print(f"Dashboard can connect at: http://{host}:{port}/api/portfolio/state")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PPMT Portfolio API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8430, help="Port to bind")
    args = parser.parse_args()
    serve(host=args.host, port=args.port)
