"""
Monte Carlo Simulator for PPMT

Monte Carlo simulation engine for assessing the robustness of a trading
system's backtest results. By reshuffling the order of trades many times
and recomputing equity curves, we estimate the distribution of possible
outcomes and derive confidence intervals for key risk metrics.

Core idea:
  The sequence of wins and losses in a backtest is just ONE realisation.
  If the same trades occurred in a different order the equity curve — and
  therefore the max drawdown, Sharpe ratio, etc. — could be very different.
  Monte Carlo resampling reveals how sensitive the results are to trade
  ordering, which is critical for position-sizing and risk-of-ruin analysis.

Usage:
    from ppmt.risk.monte_carlo import MonteCarloSimulator, MonteCarloConfig

    sim = MonteCarloSimulator()
    result = sim.simulate(trades_pnl_pct=[0.05, -0.02, 0.03, ...])
    print(f"Risk of Ruin: {result.risk_of_ruin:.1%}")
    print(f"P95 Max DD: {result.p95_max_drawdown:.1%}")
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple


@dataclass
class MonteCarloConfig:
    """Configuration for a Monte Carlo simulation run."""
    simulations: int = 1000
    """Number of simulations to run. More = tighter confidence intervals."""

    seed: int = 42
    """Seed for the PRNG. Same seed + same trades = same results."""

    initial_capital: float = 10_000.0
    """Starting capital for each simulation."""

    confidence_levels: List[int] = field(default_factory=lambda: [5, 25, 50, 75, 95])
    """Confidence levels to compute, as percentiles."""

    ruin_threshold: float = 0.5
    """Equity fraction below which the system is considered 'ruined'."""


@dataclass
class ConfidenceInterval:
    """Confidence interval for a single metric."""
    level: int  # e.g. 5, 25, 50, 75, 95
    value: float


@dataclass
class SimulationPathMetrics:
    """Metrics computed for a single simulation path."""
    final_equity: float = 0.0
    max_drawdown: float = 0.0  # as fraction 0-1
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    hit_ruin: bool = False


@dataclass
class MonteCarloResult:
    """Full result of a Monte Carlo simulation run."""
    config: MonteCarloConfig = field(default_factory=MonteCarloConfig)
    trade_count: int = 0
    generated_at: str = ""

    # Equity confidence intervals
    equity_percentiles: List[ConfidenceInterval] = field(default_factory=list)
    # Max drawdown confidence intervals (as fraction 0-1)
    drawdown_percentiles: List[ConfidenceInterval] = field(default_factory=list)
    # Sharpe ratio confidence intervals
    sharpe_percentiles: List[ConfidenceInterval] = field(default_factory=list)
    # Win rate confidence intervals
    win_rate_percentiles: List[ConfidenceInterval] = field(default_factory=list)
    # Profit factor confidence intervals
    profit_factor_percentiles: List[ConfidenceInterval] = field(default_factory=list)

    # Key risk metrics
    probability_of_profit: float = 0.0
    """Fraction of sims where finalEquity > initialCapital."""
    risk_of_ruin: float = 0.0
    """Fraction of sims where equity breached the ruin threshold."""
    p95_max_drawdown: float = 0.0
    """Drawdown exceeded only 5% of the time."""
    mean_final_equity: float = 0.0
    median_final_equity: float = 0.0
    std_dev_final_equity: float = 0.0

    # Original (unshuffled) path metrics for comparison
    original_metrics: Optional[SimulationPathMetrics] = None

    # Sample equity curves for visualization (keep a few)
    sample_equity_curves: List[List[float]] = field(default_factory=list)

    # Histogram data for final equity distribution
    equity_distribution: List[float] = field(default_factory=list)
    drawdown_distribution: List[float] = field(default_factory=list)


def _percentile(sorted_arr: List[float], p: float) -> float:
    """Compute the percentile value from a sorted array using linear interpolation."""
    if not sorted_arr:
        return 0.0
    if len(sorted_arr) == 1:
        return sorted_arr[0]

    idx = (p / 100.0) * (len(sorted_arr) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    frac = idx - lo

    if lo == hi:
        return sorted_arr[lo]
    return sorted_arr[lo] * (1.0 - frac) + sorted_arr[hi] * frac


def _build_confidence_intervals(values: List[float], levels: List[int]) -> List[ConfidenceInterval]:
    """Build percentile confidence intervals from a raw array of values."""
    sorted_vals = sorted(values)
    return [ConfidenceInterval(level=level, value=_percentile(sorted_vals, level))
            for level in levels]


def _compute_max_drawdown(equity_curve: List[float]) -> float:
    """Compute max drawdown from an equity curve as fraction 0-1."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for equity in equity_curve[1:]:
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_sharpe_ratio(returns: List[float]) -> float:
    """Compute annualised Sharpe ratio from periodic returns."""
    n = len(returns)
    if n < 2:
        return 0.0

    mean_ret = sum(returns) / n
    variance = sum((r - mean_ret) ** 2 for r in returns) / (n - 1)
    std_dev = math.sqrt(variance) if variance > 0 else 0.0

    if std_dev < 1e-12:
        return 0.0

    raw_sharpe = mean_ret / std_dev
    # Annualize assuming ~252 trading days, typical trade frequency
    annualized_sharpe = raw_sharpe * math.sqrt(252)
    return annualized_sharpe


def _compute_profit_factor(returns: List[float]) -> float:
    """Compute profit factor = grossProfit / grossLoss."""
    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = sum(abs(r) for r in returns if r < 0)

    if gross_loss < 1e-12:
        return float('inf') if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _build_equity_curve(trades_pnl_pct: List[float], initial_capital: float) -> List[float]:
    """Build the full equity curve from a sequence of PnL % values."""
    curve = [initial_capital]
    for pnl_pct in trades_pnl_pct:
        curve.append(max(curve[-1] * (1.0 + pnl_pct), 0.0))
    return curve


def _run_single_path(
    trade_copy: List[float],
    rng: random.Random,
    initial_capital: float,
    ruin_threshold: float,
) -> Tuple[SimulationPathMetrics, List[float]]:
    """
    Run a single simulation path: shuffle trades, compute equity curve,
    and return key metrics plus the equity curve.
    """
    # Fisher-Yates shuffle
    rng.shuffle(trade_copy)

    # Build equity curve
    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0
    hit_ruin = False
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    returns = []
    equity_curve = [initial_capital]
    ruin_level = initial_capital * ruin_threshold

    for pnl_pct in trade_copy:
        returns.append(pnl_pct)
        equity = equity * (1.0 + pnl_pct)
        if equity < 0:
            equity = 0.0

        equity_curve.append(equity)

        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

        if equity <= ruin_level:
            hit_ruin = True
            break

        if pnl_pct > 0:
            wins += 1
            gross_profit += pnl_pct
        elif pnl_pct < 0:
            gross_loss += abs(pnl_pct)

    n = len(trade_copy)
    win_rate = wins / n if n > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)

    metrics = SimulationPathMetrics(
        final_equity=equity,
        max_drawdown=max_dd,
        sharpe_ratio=_compute_sharpe_ratio(returns),
        win_rate=win_rate,
        profit_factor=profit_factor,
        hit_ruin=hit_ruin,
    )
    return metrics, equity_curve


class MonteCarloSimulator:
    """
    Monte Carlo Simulator for PPMT backtest validation.

    Reshuffles the order of trades many times and recomputes equity curves
    to estimate the distribution of possible outcomes and derive confidence
    intervals for key risk metrics.

    Usage:
        sim = MonteCarloSimulator()
        result = sim.simulate(trades_pnl_pct=[0.05, -0.02, 0.03, ...])
        print(f"Risk of Ruin: {result.risk_of_ruin:.1%}")
        print(f"P95 Max DD: {result.p95_max_drawdown:.1%}")
    """

    def simulate(
        self,
        trades_pnl_pct: List[float],
        config: Optional[MonteCarloConfig] = None,
    ) -> MonteCarloResult:
        """
        Run a full Monte Carlo simulation on a set of backtest trades.

        Args:
            trades_pnl_pct: Array of PnL % per trade as fractions
                           (e.g. 0.05 = +5%, -0.03 = -3%)
            config: Simulation configuration (uses defaults if None)

        Returns:
            MonteCarloResult with confidence intervals and risk metrics
        """
        cfg = config or MonteCarloConfig()
        n = len(trades_pnl_pct)

        # Edge case: no trades
        if n == 0:
            empty_metrics = SimulationPathMetrics(
                final_equity=cfg.initial_capital,
            )
            return MonteCarloResult(
                config=cfg,
                trade_count=0,
                generated_at=datetime.now().isoformat(),
                equity_percentiles=[ConfidenceInterval(level=l, value=cfg.initial_capital)
                                   for l in cfg.confidence_levels],
                drawdown_percentiles=[ConfidenceInterval(level=l, value=0.0)
                                     for l in cfg.confidence_levels],
                sharpe_percentiles=[ConfidenceInterval(level=l, value=0.0)
                                   for l in cfg.confidence_levels],
                win_rate_percentiles=[ConfidenceInterval(level=l, value=0.0)
                                     for l in cfg.confidence_levels],
                profit_factor_percentiles=[ConfidenceInterval(level=l, value=0.0)
                                          for l in cfg.confidence_levels],
                original_metrics=empty_metrics,
            )

        # Compute original (unshuffled) path metrics
        original_curve = _build_equity_curve(trades_pnl_pct, cfg.initial_capital)
        original_metrics = SimulationPathMetrics(
            final_equity=original_curve[-1],
            max_drawdown=_compute_max_drawdown(original_curve),
            sharpe_ratio=_compute_sharpe_ratio(list(trades_pnl_pct)),
            win_rate=sum(1 for r in trades_pnl_pct if r > 0) / n,
            profit_factor=_compute_profit_factor(list(trades_pnl_pct)),
            hit_ruin=any(e <= cfg.initial_capital * cfg.ruin_threshold for e in original_curve),
        )

        # Run N simulations
        rng = random.Random(cfg.seed)
        final_equities = []
        max_drawdowns = []
        sharpe_ratios = []
        win_rates = []
        profit_factors = []
        profit_count = 0
        ruin_count = 0
        sample_curves = []

        # Keep every Nth curve for visualization (up to 20 curves)
        curve_sample_interval = max(1, cfg.simulations // 20)

        for sim_idx in range(cfg.simulations):
            trade_copy = list(trades_pnl_pct)
            metrics, curve = _run_single_path(
                trade_copy, rng, cfg.initial_capital, cfg.ruin_threshold,
            )

            final_equities.append(metrics.final_equity)
            max_drawdowns.append(metrics.max_drawdown)
            sharpe_ratios.append(metrics.sharpe_ratio)
            win_rates.append(metrics.win_rate)
            profit_factors.append(metrics.profit_factor)

            if metrics.final_equity > cfg.initial_capital:
                profit_count += 1
            if metrics.hit_ruin:
                ruin_count += 1

            # Keep sample equity curves for visualization
            if sim_idx % curve_sample_interval == 0 and len(sample_curves) < 20:
                sample_curves.append(curve)

        # Store distribution data for histograms
        equity_distributions = sorted(final_equities)
        drawdown_distributions = sorted(max_drawdowns)

        # Compute aggregate statistics
        mean_final_equity = sum(final_equities) / cfg.simulations
        variance = (sum((e - mean_final_equity) ** 2 for e in final_equities)
                    / (cfg.simulations - 1)) if cfg.simulations >= 2 else 0.0
        std_dev_final_equity = math.sqrt(max(variance, 0.0))

        # Build confidence intervals
        equity_percentiles = _build_confidence_intervals(final_equities, cfg.confidence_levels)
        drawdown_percentiles = _build_confidence_intervals(max_drawdowns, cfg.confidence_levels)
        sharpe_percentiles = _build_confidence_intervals(sharpe_ratios, cfg.confidence_levels)
        win_rate_percentiles = _build_confidence_intervals(win_rates, cfg.confidence_levels)

        # Handle Infinity in profit factors
        pf_finite = [pf if pf != float('inf') else 1e6 for pf in profit_factors]
        profit_factor_percentiles = _build_confidence_intervals(pf_finite, cfg.confidence_levels)

        # P95 max drawdown
        p95_max_drawdown = _percentile(sorted(max_drawdowns), 95)
        median_final_equity = _percentile(sorted(final_equities), 50)

        return MonteCarloResult(
            config=cfg,
            trade_count=n,
            generated_at=datetime.now().isoformat(),
            equity_percentiles=equity_percentiles,
            drawdown_percentiles=drawdown_percentiles,
            sharpe_percentiles=sharpe_percentiles,
            win_rate_percentiles=win_rate_percentiles,
            profit_factor_percentiles=profit_factor_percentiles,
            probability_of_profit=profit_count / cfg.simulations,
            risk_of_ruin=ruin_count / cfg.simulations,
            p95_max_drawdown=p95_max_drawdown,
            mean_final_equity=mean_final_equity,
            median_final_equity=median_final_equity,
            std_dev_final_equity=std_dev_final_equity,
            original_metrics=original_metrics,
            sample_equity_curves=sample_curves,
            equity_distribution=equity_distributions,
            drawdown_distribution=drawdown_distributions,
        )

    def generate_summary(self, result: MonteCarloResult) -> str:
        """Generate a human-readable summary of the Monte Carlo simulation results."""
        lines = []
        c = result.config

        lines.append("=" * 64)
        lines.append("       MONTE CARLO SIMULATION REPORT")
        lines.append("=" * 64)
        lines.append(f"  Trades:            {result.trade_count}")
        lines.append(f"  Simulations:       {c.simulations}")
        lines.append(f"  Seed:              {c.seed}")
        lines.append(f"  Initial Capital:   ${c.initial_capital:,.2f}")
        lines.append(f"  Ruin Threshold:    {c.ruin_threshold * 100:.0f}%")
        lines.append("-" * 64)
        lines.append("  EQUITY CONFIDENCE INTERVALS")

        for ci in result.equity_percentiles:
            lines.append(f"    P{ci.level:>2}: ${ci.value:>12,.2f}")

        lines.append("-" * 64)
        lines.append("  DRAWDOWN CONFIDENCE INTERVALS")

        for ci in result.drawdown_percentiles:
            lines.append(f"    P{ci.level:>2}: {ci.value * 100:>8.2f}%")

        lines.append("-" * 64)
        lines.append("  KEY RISK METRICS")
        lines.append(f"    Probability of Profit: {result.probability_of_profit * 100:>7.1f}%")
        lines.append(f"    Risk of Ruin:          {result.risk_of_ruin * 100:>7.1f}%")
        lines.append(f"    P95 Max Drawdown:      {result.p95_max_drawdown * 100:>7.1f}%")
        lines.append(f"    Mean Final Equity:     ${result.mean_final_equity:>12,.2f}")
        lines.append(f"    Median Final Equity:   ${result.median_final_equity:>12,.2f}")
        lines.append(f"    StdDev Final Equity:   ${result.std_dev_final_equity:>12,.2f}")

        if result.original_metrics:
            lines.append("-" * 64)
            lines.append("  ORIGINAL (UNSHUFFLED) PATH")
            om = result.original_metrics
            lines.append(f"    Final Equity:    ${om.final_equity:>12,.2f}")
            lines.append(f"    Max Drawdown:    {om.max_drawdown * 100:>7.1f}%")
            lines.append(f"    Sharpe Ratio:    {om.sharpe_ratio:>10.3f}")
            lines.append(f"    Win Rate:        {om.win_rate * 100:>7.1f}%")
            pf_str = "INF" if om.profit_factor == float('inf') else f"{om.profit_factor:.2f}"
            lines.append(f"    Profit Factor:   {pf_str:>10}")

        lines.append("-" * 64)
        lines.append("  INTERPRETATION GUIDE")
        lines.append("    Risk of Ruin < 1%:  Excellent - system is very safe")
        lines.append("    Risk of Ruin 1-5%:  Good - acceptable for most traders")
        lines.append("    Risk of Ruin 5-10%: Marginal - consider reducing position")
        lines.append("    Risk of Ruin > 10%: Dangerous - high chance of blow-up")
        lines.append("    P95 Drawdown: worst drawdown expected 95% of the time")
        lines.append("    Wide equity CI: results depend on trade sequencing")
        lines.append("=" * 64)

        return "\n".join(lines)
