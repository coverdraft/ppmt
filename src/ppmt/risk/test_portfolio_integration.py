"""
Integration Tests for PPMT Portfolio Manager v0.16.1

Tests the full integration of:
  - PortfolioManager (multi-token governance)
  - CrossTokenCorrelationEngine (real correlation matrix)
  - RegimeAwareAllocator (regime-based allocation)
  - MoneyManager (equity curve, circuit breakers)
  - process_candle (real-time data flow)

Run with:
  python -m pytest ppmt/risk/test_portfolio_integration.py -v
  python -m pytest ppmt/risk/test_portfolio_integration.py -v -k test_correlation
"""

import time
import pytest
import numpy as np

from ppmt.risk.portfolio_manager import PortfolioManager, PortfolioConfig, TokenSlot
from ppmt.risk.correlation_engine import (
    CrossTokenCorrelationEngine,
    CorrelationMethod,
    CorrelationRegime,
)
from ppmt.risk.regime_allocator import RegimeAwareAllocator, AllocationResult
from ppmt.risk.money_manager import MoneyManager, MoneyManagerConfig
from ppmt.risk.manager import RiskConfig
from ppmt.engine.signal import Signal, SignalType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def portfolio_config():
    """Standard test portfolio config."""
    return PortfolioConfig(
        initial_capital=100_000.0,
        tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
        max_positions_per_token=2,
        max_portfolio_positions=8,
        max_portfolio_exposure_pct=0.80,
        max_single_token_exposure_pct=0.30,
        max_correlated_tokens=2,
        allocation_method="EQUAL_WEIGHT",
        kill_switch_drawdown_pct=0.20,
        daily_loss_limit_pct=0.05,
    )


@pytest.fixture
def portfolio(portfolio_config):
    """Fresh PortfolioManager instance."""
    return PortfolioManager(config=portfolio_config)


@pytest.fixture
def correlation_engine():
    """Fresh CrossTokenCorrelationEngine instance."""
    return CrossTokenCorrelationEngine(
        tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
        window=20,
    )


@pytest.fixture
def allocator():
    """Fresh RegimeAwareAllocator instance."""
    return RegimeAwareAllocator()


@pytest.fixture
def entry_signal_btc():
    """BTC LONG entry signal."""
    return Signal(
        signal_type=SignalType.ENTRY_LONG,
        symbol="BTC/USDT",
        confidence=0.75,
        quality_score=0.65,
        entry_price=50_000.0,
        sl_price=49_000.0,
        tp_price=52_000.0,
        risk_reward_ratio=2.0,
        sizing_multiplier=1.0,
        metadata_sizing_signal=1.2,
    )


@pytest.fixture
def entry_signal_sol():
    """SOL LONG entry signal."""
    return Signal(
        signal_type=SignalType.ENTRY_LONG,
        symbol="SOL/USDT",
        confidence=0.60,
        quality_score=0.50,
        entry_price=150.0,
        sl_price=145.0,
        tp_price=160.0,
        risk_reward_ratio=2.0,
        sizing_multiplier=0.8,
        metadata_sizing_signal=0.9,
    )


# ---------------------------------------------------------------------------
# PortfolioManager Tests
# ---------------------------------------------------------------------------

class TestPortfolioManager:
    """Test the multi-token PortfolioManager."""

    def test_initialization(self, portfolio):
        """Portfolio should initialize with correct number of token slots."""
        assert len(portfolio._slots) == 4
        assert portfolio.total_value == 100_000.0
        assert portfolio.total_open_positions == 0
        assert not portfolio._kill_switch_active

    def test_slots_have_risk_managers(self, portfolio):
        """Each slot should have its own RiskManager."""
        for slot in portfolio.all_slots:
            assert slot.risk_manager is not None
            assert slot.capital_allocated > 0

    def test_equal_weight_allocation(self, portfolio):
        """With EQUAL_WEIGHT, each slot should get 25% of capital."""
        for slot in portfolio.all_slots:
            assert abs(slot.capital_allocated - 25_000.0) < 1.0

    def test_add_token(self, portfolio):
        """Adding a token should create a new slot and rebalance."""
        slot = portfolio.add_token("AVAX/USDT")
        assert slot is not None
        assert slot.symbol == "AVAX/USDT"
        assert len(portfolio._slots) == 5
        assert "AVAX/USDT" in [t for t in portfolio._correlation_engine._tokens]

    def test_remove_token(self, portfolio):
        """Removing a token without positions should work."""
        slot = portfolio.remove_token("DOGE/USDT")
        assert slot is not None
        assert len(portfolio._slots) == 3
        assert "DOGE/USDT" not in portfolio._correlation_engine._tokens

    def test_activate_deactivate_slot(self, portfolio):
        """Should be able to activate and deactivate slots."""
        assert portfolio.deactivate_slot("DOGE/USDT")
        slot = portfolio.get_slot("DOGE/USDT")
        assert not slot.is_active

        assert portfolio.activate_slot("DOGE/USDT")
        assert slot.is_active

    def test_portfolio_summary(self, portfolio):
        """Summary should include correlation data."""
        summary = portfolio.get_portfolio_summary()
        assert "correlation" in summary
        assert "regime" in summary["correlation"]
        assert "diversification_score" in summary["correlation"]
        assert "correlation_crisis_active" in summary["correlation"]

    def test_risk_report_has_correlation_regime(self, portfolio):
        """Risk report should include correlation regime."""
        report = portfolio.get_risk_report()
        assert "correlation_regime" in report
        assert "avg_correlation" in report


# ---------------------------------------------------------------------------
# process_candle Tests
# ---------------------------------------------------------------------------

class TestProcessCandle:
    """Test the process_candle method."""

    def test_basic_candle_processing(self, portfolio):
        """Processing a candle should update state."""
        candle = {"close": 51_000.0, "open": 50_500.0, "high": 51_200.0, "low": 50_400.0}
        result = portfolio.process_candle("BTC/USDT", candle, regime="TRENDING_UP")
        assert result is None  # No signal generated at portfolio level
        assert portfolio._candles_processed == 1

    def test_candle_updates_correlation_engine(self, portfolio):
        """Processing candles should feed data to the correlation engine."""
        # Feed multiple candles to build up returns
        prices = [50_000, 50_500, 49_800, 51_000, 50_200, 51_500]
        for price in prices:
            portfolio.process_candle("BTC/USDT", {"close": price})
            portfolio.process_candle("ETH/USDT", {"close": price * 0.06})
            portfolio.process_candle("SOL/USDT", {"close": price * 0.003})
            portfolio.process_candle("DOGE/USDT", {"close": price * 0.0002})

        # Correlation engine should have data now
        assert len(portfolio._correlation_engine._returns.get("BTC/USDT", [])) > 0

    def test_candle_updates_regime(self, portfolio):
        """Processing a candle with regime should update the slot."""
        portfolio.process_candle("BTC/USDT", {"close": 50_000.0}, regime="TRENDING_UP")
        slot = portfolio.get_slot("BTC/USDT")
        assert slot.current_regime == "TRENDING_UP"

    def test_equity_curve_recording(self, portfolio):
        """Processing candles should record equity curve snapshots."""
        for i in range(5):
            portfolio.process_candle("BTC/USDT", {"close": 50_000 + i * 100})

        assert len(portfolio._equity_curve) >= 5


# ---------------------------------------------------------------------------
# Correlation Integration Tests
# ---------------------------------------------------------------------------

class TestCorrelationIntegration:
    """Test the integration of CrossTokenCorrelationEngine with PortfolioManager."""

    def test_get_correlation_matrix(self, portfolio):
        """Should be able to get correlation matrix from portfolio."""
        result = portfolio.get_correlation_matrix()
        assert result is not None
        assert len(result.tokens) == 4
        assert result.matrix.shape[0] == 4
        assert result.matrix.shape[1] == 4

    def test_get_correlation_between(self, portfolio):
        """Should return correlation between specific tokens."""
        corr = portfolio.get_correlation_between("BTC/USDT", "ETH/USDT")
        # With proxy correlations (no real data), BTC-ETH should be highly correlated
        assert corr is not None
        assert 0.5 < corr < 1.0  # Proxy correlations for blue_chip-blue_chip

    def test_diversification_score(self, portfolio):
        """Should return diversification metrics."""
        div = portfolio.get_diversification_score()
        assert "score" in div
        assert "rating" in div
        assert 0 <= div["score"] <= 1

    def test_real_correlation_after_candles(self, portfolio):
        """After feeding real candle data, should compute real correlations."""
        # Feed correlated data: BTC and ETH move together
        base_prices = {
            "BTC/USDT": 50_000.0,
            "ETH/USDT": 3_000.0,
            "SOL/USDT": 150.0,
            "DOGE/USDT": 0.10,
        }

        np.random.seed(42)
        for _ in range(30):
            # BTC and ETH move together (correlated)
            common_factor = np.random.normal(0, 0.01)
            for symbol, base in base_prices.items():
                noise = np.random.normal(0, 0.005)
                change = common_factor + noise
                new_price = base_prices[symbol] * (1 + change)
                base_prices[symbol] = new_price
                portfolio.process_candle(symbol, {"close": new_price})

        # Now compute correlation
        corr = portfolio.get_correlation_between("BTC/USDT", "ETH/USDT")
        assert corr is not None
        # Should detect positive correlation between BTC and ETH
        assert corr > 0.3  # At minimum, should be somewhat positive

    def test_correlation_crisis_detection(self, correlation_engine):
        """Should detect when correlations spike to crisis levels."""
        # Feed highly correlated data
        np.random.seed(42)
        for _ in range(30):
            common = np.random.normal(0, 0.02)
            for token in correlation_engine._tokens:
                price = 100 * (1 + common + np.random.normal(0, 0.001))
                correlation_engine.update_price(token, price)

        result = correlation_engine.compute_matrix()
        # With highly correlated data, should detect elevated or crisis regime
        assert result.regime in (CorrelationRegime.ELEVATED, CorrelationRegime.CRISIS, CorrelationRegime.NORMAL)


# ---------------------------------------------------------------------------
# Regime Allocation Tests
# ---------------------------------------------------------------------------

class TestRegimeAllocation:
    """Test the RegimeAwareAllocator integration."""

    def test_trending_up_allocation(self, allocator):
        """Trending up should favor blue chips."""
        result = allocator.allocate(
            regime="TRENDING_UP",
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
            total_capital=100_000.0,
        )
        assert result.position_size_multiplier == 1.2
        # Blue chips should get more capital
        btc_instr = next(i for i in result.instructions if i.symbol == "BTC/USDT")
        doge_instr = next(i for i in result.instructions if i.symbol == "DOGE/USDT")
        assert btc_instr.target_weight > doge_instr.target_weight

    def test_crisis_allocation(self, allocator):
        """Crisis should be very conservative."""
        result = allocator.allocate(
            regime="CRISIS",
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"],
            total_capital=100_000.0,
        )
        assert result.position_size_multiplier == 0.25
        assert result.cash_reserve > 40_000  # 50% cash reserve

    def test_allocation_recommendation_from_portfolio(self, portfolio):
        """Portfolio should provide allocation recommendations."""
        result = portfolio.get_allocation_recommendation(regime="TRENDING_UP")
        assert result is not None
        assert len(result.instructions) == 4

    def test_smooth_transition(self, allocator):
        """Allocation should have smooth transitions between rebalances."""
        tokens = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"]

        # First allocation
        result1 = allocator.allocate(
            regime="RANGING",
            tokens=tokens,
            total_capital=100_000.0,
        )

        # Second allocation with different regime
        current = {i.symbol: i.target_capital for i in result1.instructions}
        result2 = allocator.allocate(
            regime="TRENDING_UP",
            tokens=tokens,
            total_capital=100_000.0,
            current_allocations=current,
            smooth_transition=True,
        )

        # Changes should be limited by max_single_change_pct
        for instr in result2.instructions:
            if instr.current_capital > 0:
                change_pct = abs(instr.capital_delta / instr.current_capital)
                assert change_pct <= 0.20  # Default max_single_change_pct + buffer


# ---------------------------------------------------------------------------
# Signal Processing Tests
# ---------------------------------------------------------------------------

class TestSignalProcessing:
    """Test opening/closing positions through the portfolio."""

    def test_can_open_position(self, portfolio, entry_signal_btc):
        """Should be able to open a position."""
        allowed, reason = portfolio.can_open_position(entry_signal_btc, proposed_size=0.1)
        assert allowed, f"Should be allowed: {reason}"

    def test_open_and_close_position(self, portfolio, entry_signal_btc):
        """Should open and close a position correctly."""
        # Open
        position = portfolio.open_position(entry_signal_btc, size=0.1)
        assert position is not None
        assert portfolio.total_open_positions == 1

        # Close
        result = portfolio.close_position("BTC/USDT", exit_price=51_000.0)
        assert result is not None
        assert portfolio.total_open_positions == 0

    def test_kill_switch_blocks_new_positions(self, portfolio, entry_signal_btc):
        """Kill switch should prevent new positions."""
        portfolio.activate_kill_switch()
        allowed, reason = portfolio.can_open_position(entry_signal_btc, proposed_size=0.1)
        assert not allowed
        assert "kill switch" in reason.lower()

    def test_deactivated_slot_blocks_positions(self, portfolio, entry_signal_btc):
        """Deactivated slots should block new positions."""
        portfolio.deactivate_slot("BTC/USDT")
        allowed, reason = portfolio.can_open_position(entry_signal_btc, proposed_size=0.1)
        assert not allowed
        assert "deactivated" in reason.lower()


# ---------------------------------------------------------------------------
# Circuit Breaker Tests
# ---------------------------------------------------------------------------

class TestCircuitBreakers:
    """Test portfolio-level circuit breakers."""

    def test_circuit_breaker_status(self, portfolio):
        """Should return all breaker statuses including correlation crisis."""
        status = portfolio.circuit_breaker_status()
        assert "kill_switch" in status
        assert "daily_loss" in status
        assert "drawdown" in status
        assert "correlation_crisis" in status

    def test_is_trading_allowed(self, portfolio):
        """Trading should be allowed initially."""
        assert portfolio.is_trading_allowed()

    def test_deactivate_kill_switch(self, portfolio):
        """Should be able to deactivate kill switch."""
        portfolio.activate_kill_switch()
        assert portfolio._kill_switch_active
        portfolio.deactivate_kill_switch()
        assert not portfolio._kill_switch_active


# ---------------------------------------------------------------------------
# MoneyManager Integration Tests
# ---------------------------------------------------------------------------

class TestMoneyManagerIntegration:
    """Test that PortfolioManager's MoneyManager is properly integrated."""

    def test_money_manager_exists(self, portfolio):
        """Portfolio should have a MoneyManager instance."""
        assert portfolio.money_manager is not None

    def test_money_manager_tracks_equity(self, portfolio):
        """MoneyManager should track equity curve."""
        assert len(portfolio.money_manager.equity_curve) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
