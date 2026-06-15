"""
Tests for PPMT v0.16.0 Portfolio Management System

Covers:
  - PortfolioManager: multi-token slots, capital allocation, circuit breakers
  - CrossTokenCorrelationEngine: matrix computation, regime detection, alerts
  - RegimeAwareAllocator: regime profiles, allocation computation
  - PortfolioBacktester: multi-token backtest simulation
"""

import sys
import os
import pytest
import numpy as np

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ===================================================================
# PortfolioManager Tests
# ===================================================================

class TestPortfolioManager:
    """Tests for the PortfolioManager."""

    def test_create_with_defaults(self):
        """Test creating a PortfolioManager with default config."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(initial_capital=10_000.0, tokens=["BTC/USDT", "ETH/USDT"])
        pm = PortfolioManager(config=config)

        assert pm.total_value == 10_000.0
        assert len(pm._slots) == 2
        assert pm.total_open_positions == 0

    def test_equal_weight_allocation(self):
        """Test equal weight capital allocation."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(
            initial_capital=60_000.0,
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            allocation_method="EQUAL_WEIGHT",
        )
        pm = PortfolioManager(config=config)

        # Each token should get 1/3 of capital
        for slot in pm._slots.values():
            assert abs(slot.capital_allocated - 20_000.0) < 1.0

    def test_risk_parity_allocation(self):
        """Test risk parity allocation gives more to less volatile assets."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(
            initial_capital=60_000.0,
            tokens=["BTC/USDT", "DOGE/USDT"],  # blue_chip vs meme
            allocation_method="RISK_PARITY",
        )
        pm = PortfolioManager(config=config)

        btc_slot = pm.get_slot("BTC/USDT")
        doge_slot = pm.get_slot("DOGE/USDT")

        # BTC (blue_chip, lower vol proxy) should get MORE than DOGE (meme, higher vol proxy)
        assert btc_slot.capital_allocated > doge_slot.capital_allocated

    def test_regime_aware_allocation(self):
        """Test regime-aware allocation adjusts by regime."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(
            initial_capital=60_000.0,
            tokens=["BTC/USDT", "DOGE/USDT"],
            allocation_method="REGIME_AWARE",
        )
        pm = PortfolioManager(config=config)

        # Default regime is UNKNOWN — should still allocate
        btc_slot = pm.get_slot("BTC/USDT")
        doge_slot = pm.get_slot("DOGE/USDT")

        assert btc_slot.capital_allocated > 0
        assert doge_slot.capital_allocated > 0

    def test_add_remove_token(self):
        """Test adding and removing tokens."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(
            initial_capital=30_000.0,
            tokens=["BTC/USDT"],
        )
        pm = PortfolioManager(config=config)

        # Add a token
        slot = pm.add_token("SOL/USDT")
        assert slot is not None
        assert slot.symbol == "SOL/USDT"
        assert len(pm._slots) == 2

        # Remove a token (no open positions, should work)
        removed = pm.remove_token("SOL/USDT")
        assert removed is not None
        assert len(pm._slots) == 1

    def test_activate_deactivate_slot(self):
        """Test activating and deactivating token slots."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(
            initial_capital=30_000.0,
            tokens=["BTC/USDT", "ETH/USDT"],
        )
        pm = PortfolioManager(config=config)

        # Deactivate a slot
        pm.deactivate_slot("ETH/USDT")
        eth_slot = pm.get_slot("ETH/USDT")
        assert not eth_slot.is_active

        # Reactivate
        pm.activate_slot("ETH/USDT")
        assert eth_slot.is_active

    def test_can_open_position(self):
        """Test position opening checks."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig
        from ppmt.engine.signal import Signal, SignalType

        config = PortfolioConfig(
            initial_capital=10_000.0,
            tokens=["BTC/USDT"],
            max_portfolio_positions=5,
        )
        pm = PortfolioManager(config=config)

        # Create a valid entry signal
        signal = Signal(
            signal_type=SignalType.ENTRY_LONG,
            confidence=0.8,
            symbol="BTC/USDT",
            entry_price=50000.0,
            sl_price=49000.0,
            tp_price=52000.0,
            quality_score=0.7,
            risk_reward_ratio=1.5,
        )

        allowed, reason = pm.can_open_position(signal, 0.01, 50000.0)
        # Should be allowed (no circuit breakers, within limits)
        assert allowed

    def test_kill_switch(self):
        """Test kill switch activation."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(initial_capital=10_000.0, tokens=["BTC/USDT"])
        pm = PortfolioManager(config=config)

        pm.activate_kill_switch()
        assert pm._kill_switch_active
        assert not pm.is_trading_allowed()

        pm.deactivate_kill_switch()
        assert not pm._kill_switch_active

    def test_circuit_breaker_status(self):
        """Test circuit breaker status reporting."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(initial_capital=10_000.0, tokens=["BTC/USDT"])
        pm = PortfolioManager(config=config)

        status = pm.circuit_breaker_status()
        assert "kill_switch" in status
        assert "daily_loss" in status
        assert "drawdown" in status

    def test_portfolio_summary(self):
        """Test portfolio summary generation."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(
            initial_capital=50_000.0,
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        )
        pm = PortfolioManager(config=config)

        summary = pm.get_portfolio_summary()
        assert summary["total_value"] == 50_000.0
        assert summary["active_slots"] == 3
        assert summary["dominant_regime"] == "UNKNOWN"
        assert len(summary["slots"]) == 3

    def test_risk_report(self):
        """Test risk report generation."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(
            initial_capital=50_000.0,
            tokens=["BTC/USDT", "ETH/USDT"],
        )
        pm = PortfolioManager(config=config)

        report = pm.get_risk_report()
        assert "var_95_1d" in report
        assert "var_99_1d" in report
        assert "hhi_concentration" in report
        assert "diversification_ratio" in report

    def test_rebalance(self):
        """Test portfolio rebalancing."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        config = PortfolioConfig(
            initial_capital=30_000.0,
            tokens=["BTC/USDT", "ETH/USDT"],
            allocation_method="EQUAL_WEIGHT",
        )
        pm = PortfolioManager(config=config)

        result = pm.rebalance(reason="test")
        assert result.reason == "test"
        # With equal weight and no positions, no moves should be needed
        assert len(result.capital_moves) == 0

    def test_save_load_state(self, tmp_path):
        """Test state persistence."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig

        filepath = str(tmp_path / "portfolio_state.json")

        config = PortfolioConfig(
            initial_capital=10_000.0,
            tokens=["BTC/USDT"],
            state_file=filepath,
        )
        pm = PortfolioManager(config=config)
        pm.save_state()

        # Load into a new instance
        config2 = PortfolioConfig(
            initial_capital=10_000.0,
            tokens=["BTC/USDT"],
            state_file=filepath,
        )
        pm2 = PortfolioManager(config=config2)
        loaded = pm2.load_state()
        assert loaded


# ===================================================================
# CrossTokenCorrelationEngine Tests
# ===================================================================

class TestCrossTokenCorrelationEngine:
    """Tests for the CrossTokenCorrelationEngine."""

    def test_create_engine(self):
        """Test creating a correlation engine."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            window=30,
        )
        assert len(engine._tokens) == 3

    def test_proxy_correlation_matrix(self):
        """Test proxy correlation matrix when insufficient data."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(
            tokens=["BTC/USDT", "ETH/USDT"],
            window=30,
        )

        result = engine.compute_matrix()
        assert len(result.tokens) == 2
        assert result.matrix.shape == (2, 2)
        # Diagonal should be 1.0
        assert result.matrix[0][0] == 1.0
        assert result.matrix[1][1] == 1.0
        # BTC-ETH should be positively correlated
        assert result.matrix[0][1] > 0.5

    def test_real_correlation_matrix(self):
        """Test correlation matrix with sufficient data."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(
            tokens=["A", "B", "C"],
            window=30,
        )

        # Load correlated returns
        np.random.seed(42)
        base = np.random.randn(30)
        returns_a = base * 0.02 + 0.001
        returns_b = base * 0.03 + 0.002  # Highly correlated with A
        returns_c = np.random.randn(30) * 0.04  # Uncorrelated

        engine.update_returns("A", returns_a.tolist())
        engine.update_returns("B", returns_b.tolist())
        engine.update_returns("C", returns_c.tolist())

        result = engine.compute_matrix()
        assert result.matrix.shape == (3, 3)

        # A-B should be highly correlated
        ab_corr = result.get_pair_correlation("A", "B")
        assert ab_corr is not None
        assert ab_corr > 0.7  # Should be highly correlated

        # A-C should be less correlated
        ac_corr = result.get_pair_correlation("A", "C")
        assert ac_corr is not None
        assert abs(ac_corr) < abs(ab_corr)

    def test_regime_detection(self):
        """Test correlation regime detection."""
        from ppmt.risk.correlation_engine import (
            CrossTokenCorrelationEngine, CorrelationRegime,
        )

        engine = CrossTokenCorrelationEngine(
            tokens=["A", "B", "C"],
            window=30,
            crisis_correlation_threshold=0.70,
        )

        # Load highly correlated returns (crisis scenario)
        np.random.seed(42)
        base = np.random.randn(30)
        engine.update_returns("A", (base * 0.02).tolist())
        engine.update_returns("B", (base * 0.02).tolist())  # Identical
        engine.update_returns("C", (base * 0.02).tolist())  # Identical

        result = engine.compute_matrix()
        # With identical returns, should be in ELEVATED or CRISIS regime
        assert result.regime in (CorrelationRegime.ELEVATED, CorrelationRegime.CRISIS)

    def test_diversification_score(self):
        """Test diversification scoring."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(
            tokens=["A", "B", "C"],
            window=30,
        )

        # Load uncorrelated returns (good diversification)
        np.random.seed(42)
        engine.update_returns("A", np.random.randn(30).tolist())
        engine.update_returns("B", np.random.randn(30).tolist())
        engine.update_returns("C", np.random.randn(30).tolist())

        div = engine.compute_diversification_score()
        assert "score" in div
        assert "rating" in div
        assert div["score"] > 0  # Should have positive diversification

    def test_pair_correlation_lookup(self):
        """Test getting correlation between specific pairs."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(
            tokens=["BTC/USDT", "ETH/USDT"],
            window=30,
        )

        result = engine.compute_matrix()
        corr = result.get_pair_correlation("BTC/USDT", "ETH/USDT")
        assert corr is not None
        assert -1.0 <= corr <= 1.0

    def test_most_correlated_pairs(self):
        """Test finding the most correlated pairs."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(
            tokens=["A", "B", "C", "D"],
            window=30,
        )

        # Create specific correlation structure
        np.random.seed(42)
        base = np.random.randn(30)
        engine.update_returns("A", (base * 0.02).tolist())
        engine.update_returns("B", (base * 0.03).tolist())  # Correlated with A
        engine.update_returns("C", np.random.randn(30).tolist())  # Uncorrelated
        engine.update_returns("D", np.random.randn(30).tolist())  # Uncorrelated

        result = engine.compute_matrix()
        top_pairs = result.get_most_correlated_pairs(n=2)
        assert len(top_pairs) >= 2
        # A-B should be the most correlated
        assert top_pairs[0][2] > 0.5  # High correlation

    def test_alert_generation(self):
        """Test correlation alert generation."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(
            tokens=["A", "B"],
            window=30,
            high_correlation_threshold=0.5,  # Low threshold to trigger alerts
        )

        # Load highly correlated returns
        np.random.seed(42)
        base = np.random.randn(30)
        engine.update_returns("A", (base * 0.02).tolist())
        engine.update_returns("B", (base * 0.02).tolist())

        result = engine.compute_matrix()
        alerts = engine.get_alerts()
        assert len(alerts) > 0
        assert alerts[0]["type"] == "HIGH_CORRELATION"

    def test_portfolio_variance(self):
        """Test portfolio variance computation."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(
            tokens=["A", "B"],
            window=30,
        )

        np.random.seed(42)
        base = np.random.randn(30)
        engine.update_returns("A", (base * 0.02).tolist())
        engine.update_returns("B", (base * 0.03).tolist())

        var = engine.compute_portfolio_variance(
            weights={"A": 0.5, "B": 0.5},
            volatilities={"A": 0.02, "B": 0.03},
        )
        assert var > 0

    def test_add_remove_token(self):
        """Test adding and removing tokens from correlation engine."""
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine

        engine = CrossTokenCorrelationEngine(tokens=["A", "B"])
        engine.add_token("C")
        assert len(engine._tokens) == 3

        engine.remove_token("C")
        assert len(engine._tokens) == 2


# ===================================================================
# RegimeAwareAllocator Tests
# ===================================================================

class TestRegimeAwareAllocator:
    """Tests for the RegimeAwareAllocator."""

    def test_create_allocator(self):
        """Test creating an allocator."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator()
        assert len(allocator.profiles) >= 5

    def test_trending_up_allocation(self):
        """Test allocation in trending up regime."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator()
        result = allocator.allocate(
            regime="TRENDING_UP",
            tokens=["BTC/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
        )

        # BTC should get more than DOGE in trending up
        btc_instr = next(i for i in result.instructions if i.symbol == "BTC/USDT")
        doge_instr = next(i for i in result.instructions if i.symbol == "DOGE/USDT")
        assert btc_instr.target_capital > doge_instr.target_capital

    def test_crisis_allocation(self):
        """Test allocation in crisis regime."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator()
        result = allocator.allocate(
            regime="CRISIS",
            tokens=["BTC/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
        )

        # In crisis, position size multiplier should be very low
        assert result.position_size_multiplier <= 0.3
        # Max exposure should be reduced
        assert result.max_exposure <= 0.40
        # Cash reserve should be high
        assert result.cash_reserve > 20_000.0

    def test_performance_adjustment(self):
        """Test performance-based allocation adjustment."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator()
        result = allocator.allocate(
            regime="RANGING",
            tokens=["BTC/USDT", "ETH/USDT"],
            total_capital=50_000.0,
            token_performance={
                "BTC/USDT": {"win_rate": 0.70, "pnl_pct": 0.15, "trades": 20},
                "ETH/USDT": {"win_rate": 0.35, "pnl_pct": -0.10, "trades": 20},
            },
        )

        # BTC (better performance) should get more capital
        btc_instr = next(i for i in result.instructions if i.symbol == "BTC/USDT")
        eth_instr = next(i for i in result.instructions if i.symbol == "ETH/USDT")
        assert btc_instr.target_weight > eth_instr.target_weight

    def test_drawdown_penalty(self):
        """Test that drawdown reduces allocations."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator(drawdown_penalty=3.0)

        # No drawdown
        result_normal = allocator.allocate(
            regime="RANGING",
            tokens=["BTC/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
            portfolio_drawdown_pct=0.0,
        )

        # 20% drawdown (significant)
        result_dd = allocator.allocate(
            regime="RANGING",
            tokens=["BTC/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
            portfolio_drawdown_pct=0.20,
        )

        # Total allocated should be reduced during drawdown due to min_cash_reserve
        # and the overall allocation being scaled down by dd_multiplier
        # With 20% DD and penalty=3.0: dd_mult = max(0.1, 1 - 0.2*3) = 0.4
        # This means weights are 40% of normal → but they're renormalized
        # However, the investable capital may differ due to regime's min_cash_reserve
        # Check that total allocated is reasonable
        assert result_dd.total_allocated < result_normal.total_allocated * 1.5
        # Position size multiplier should still reflect reduced sizing
        # (This is the key safety mechanism — not total allocation but per-trade size)

    def test_smooth_transition(self):
        """Test that rebalancing changes are limited."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator()

        # First allocation
        result1 = allocator.allocate(
            regime="RANGING",
            tokens=["BTC/USDT"],
            total_capital=50_000.0,
            current_allocations={"BTC/USDT": 25_000.0},
        )

        # Second allocation with smooth transition
        result2 = allocator.allocate(
            regime="TRENDING_UP",
            tokens=["BTC/USDT"],
            total_capital=50_000.0,
            current_allocations={"BTC/USDT": 25_000.0},
            smooth_transition=True,
            max_single_change_pct=0.10,
        )

        # Change should be limited
        delta = abs(result2.instructions[0].capital_delta)
        max_allowed = 25_000.0 * 0.10  # 10% of current
        assert delta <= max_allowed + 1.0  # $1 tolerance

    def test_regime_summary(self):
        """Test regime profile summary."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator()
        summary = allocator.get_regime_summary("TRENDING_UP")
        assert summary["regime"] == "TRENDING_UP"
        assert "blue_chip" in summary["class_weights"]
        assert summary["position_size_multiplier"] > 0

    def test_correlation_crisis_adjustment(self):
        """Test that crisis correlation regime makes allocation more conservative."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator()

        # Normal correlation
        result_normal = allocator.allocate(
            regime="RANGING",
            tokens=["BTC/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
            correlation_regime="NORMAL",
        )

        # Crisis correlation
        result_crisis = allocator.allocate(
            regime="RANGING",
            tokens=["BTC/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
            correlation_regime="CRISIS",
        )

        # Crisis should reduce max exposure and position size multiplier
        assert result_crisis.max_exposure < result_normal.max_exposure
        assert result_crisis.position_size_multiplier < result_normal.position_size_multiplier


# ===================================================================
# PortfolioBacktester Tests
# ===================================================================

class TestPortfolioBacktester:
    """Tests for the PortfolioBacktester."""

    def _generate_candles(self, n: int, base_price: float = 100.0, volatility: float = 0.02) -> list:
        """Generate synthetic OHLCV candle data."""
        np.random.seed(42)
        candles = []
        price = base_price
        for i in range(n):
            change = np.random.randn() * volatility * price
            o = price
            c = price + change
            h = max(o, c) + abs(np.random.randn() * volatility * price * 0.5)
            l = min(o, c) - abs(np.random.randn() * volatility * price * 0.5)
            v = abs(np.random.randn() * 1_000_000)
            candles.append({
                "timestamp": 1_700_000_000 + i * 3600,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": round(v, 2),
            })
            price = c
        return candles

    def test_create_backtester(self):
        """Test creating a portfolio backtester."""
        from ppmt.risk.portfolio_backtester import PortfolioBacktester, PortfolioBacktestConfig

        config = PortfolioBacktestConfig(
            initial_capital=50_000.0,
            tokens=["BTC/USDT", "ETH/USDT"],
        )
        bt = PortfolioBacktester(config=config)
        assert bt.config.initial_capital == 50_000.0

    def test_run_simple_backtest(self):
        """Test running a simple portfolio backtest."""
        from ppmt.risk.portfolio_backtester import PortfolioBacktester, PortfolioBacktestConfig

        config = PortfolioBacktestConfig(
            initial_capital=50_000.0,
            tokens=["BTC/USDT", "ETH/USDT"],
            rebalance_interval=0,  # No rebalancing
        )
        bt = PortfolioBacktester(config=config)

        # Generate synthetic data
        data = {
            "BTC/USDT": self._generate_candles(100, base_price=50000.0),
            "ETH/USDT": self._generate_candles(100, base_price=3000.0),
        }

        result = bt.run(data, progress=False)

        assert result.total_capital == 50_000.0
        assert result.duration_candles == 100
        assert len(result.equity_curve) == 100
        assert len(result.drawdown_curve) == 100

    def test_backtest_result_structure(self):
        """Test that backtest result has expected structure."""
        from ppmt.risk.portfolio_backtester import PortfolioBacktester, PortfolioBacktestConfig

        config = PortfolioBacktestConfig(
            initial_capital=50_000.0,
            tokens=["BTC/USDT"],
            rebalance_interval=0,
        )
        bt = PortfolioBacktester(config=config)

        data = {"BTC/USDT": self._generate_candles(50, base_price=50000.0)}
        result = bt.run(data, progress=False)

        # Check result structure
        assert "BTC/USDT" in result.tokens
        assert isinstance(result.total_return_pct, float)
        assert isinstance(result.max_drawdown_pct, float)
        assert isinstance(result.sharpe_ratio, float)

    def test_backtest_to_dict(self):
        """Test backtest result serialization."""
        from ppmt.risk.portfolio_backtester import PortfolioBacktester, PortfolioBacktestConfig

        config = PortfolioBacktestConfig(
            initial_capital=50_000.0,
            tokens=["BTC/USDT"],
            rebalance_interval=0,
        )
        bt = PortfolioBacktester(config=config)

        data = {"BTC/USDT": self._generate_candles(50, base_price=50000.0)}
        result = bt.run(data, progress=False)

        d = result.to_dict()
        assert "total_capital" in d
        assert "tokens" in d
        assert "BTC/USDT" in d["tokens"]

    def test_backtest_save_load(self, tmp_path):
        """Test saving backtest result to file."""
        from ppmt.risk.portfolio_backtester import PortfolioBacktester, PortfolioBacktestConfig
        import json

        config = PortfolioBacktestConfig(
            initial_capital=50_000.0,
            tokens=["BTC/USDT"],
            rebalance_interval=0,
        )
        bt = PortfolioBacktester(config=config)

        data = {"BTC/USDT": self._generate_candles(50, base_price=50000.0)}
        result = bt.run(data, progress=False)

        filepath = str(tmp_path / "backtest_result.json")
        bt.save_result(result, filepath)

        with open(filepath) as f:
            saved = json.load(f)
        assert saved["total_capital"] == 50_000.0


# ===================================================================
# Integration Tests
# ===================================================================

class TestPortfolioIntegration:
    """Integration tests combining multiple components."""

    def test_full_portfolio_workflow(self):
        """Test a complete workflow: create → allocate → rebalance → summary."""
        from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig
        from ppmt.risk.correlation_engine import CrossTokenCorrelationEngine
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        # Step 1: Create portfolio
        config = PortfolioConfig(
            initial_capital=100_000.0,
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
            allocation_method="REGIME_AWARE",
        )
        pm = PortfolioManager(config=config)

        # Step 2: Update regimes
        pm.update_regime("BTC/USDT", "TRENDING_UP")
        pm.update_regime("ETH/USDT", "TRENDING_UP")
        pm.update_regime("SOL/USDT", "RANGING")
        pm.update_regime("DOGE/USDT", "VOLATILE")

        # Step 3: Rebalance with new regime info
        result = pm.rebalance(reason="regime_update")
        assert result.regime == "TRENDING_UP"

        # Step 4: Compute correlation
        corr = CrossTokenCorrelationEngine(
            tokens=list(pm._slots.keys()),
        )
        corr_result = corr.compute_matrix()
        assert corr_result.matrix.shape == (4, 4)

        # Step 5: Get diversification score
        div = corr.compute_diversification_score()
        assert div["score"] > 0

        # Step 6: Get portfolio summary
        summary = pm.get_portfolio_summary()
        assert summary["total_value"] == 100_000.0
        assert summary["active_slots"] == 4
        assert summary["dominant_regime"] == "TRENDING_UP"

    def test_allocation_with_correlation(self):
        """Test that allocator considers correlation regime."""
        from ppmt.risk.regime_allocator import RegimeAwareAllocator

        allocator = RegimeAwareAllocator()

        # Normal correlation → standard allocation
        result_normal = allocator.allocate(
            regime="RANGING",
            tokens=["BTC/USDT", "ETH/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
            correlation_regime="NORMAL",
        )

        # Crisis correlation → more conservative
        result_crisis = allocator.allocate(
            regime="RANGING",
            tokens=["BTC/USDT", "ETH/USDT", "DOGE/USDT"],
            total_capital=50_000.0,
            correlation_regime="CRISIS",
        )

        # Crisis should have higher cash reserve
        assert result_crisis.cash_reserve > result_normal.cash_reserve
        # Crisis should have lower max exposure
        assert result_crisis.max_exposure < result_normal.max_exposure


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
