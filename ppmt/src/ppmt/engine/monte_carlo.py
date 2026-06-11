"""
PPMT Monte Carlo Simulation Engine

Resamples from historical backtest trade results to simulate equity curves,
calculate risk of ruin, confidence intervals, and distribution of outcomes.

This is the CORE feature — the `ppmt monte-carlo` command.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import logging
import json

logger = logging.getLogger(__name__)


@dataclass
class MonteCarloResult:
    """Container for Monte Carlo simulation results."""

    symbol: str = ""
    initial_capital: float = 10000.0
    n_simulations: int = 1000
    n_trades_per_sim: int = 0
    ruin_threshold: float = 0.5
    ruin_definition: str = "fraction"  # "fraction" or "absolute"

    # Per-simulation equity curves
    equity_curves: List[List[float]] = field(default_factory=list)
    # Final equity values
    final_equities: List[float] = field(default_factory=list)
    # Maximum drawdowns per simulation
    max_drawdowns: List[float] = field(default_factory=list)
    # Total PnL per simulation
    total_pnls: List[float] = field(default_factory=list)
    # Win rates per simulation
    win_rates: List[float] = field(default_factory=list)

    # Aggregate statistics
    stats: Dict = field(default_factory=dict)

    def compute_stats(self):
        """Compute aggregate statistics from simulation results."""
        if not self.final_equities:
            self.stats = {}
            return

        finals = np.array(self.final_equities)
        dds = np.array(self.max_drawdowns)
        pnls = np.array(self.total_pnls)
        wrs = np.array(self.win_rates)

        # Risk of ruin
        if self.ruin_definition == "fraction":
            ruin_level = self.initial_capital * self.ruin_threshold
        else:
            ruin_level = self.ruin_threshold
        ruin_count = np.sum(finals < ruin_level)
        risk_of_ruin = ruin_count / len(finals) * 100

        # Profit probability
        profit_prob = np.sum(finals > self.initial_capital) / len(finals) * 100

        # Confidence intervals
        ci_5 = np.percentile(finals, 5)
        ci_25 = np.percentile(finals, 25)
        ci_50 = np.percentile(finals, 50)
        ci_75 = np.percentile(finals, 75)
        ci_95 = np.percentile(finals, 95)

        self.stats = {
            "symbol": self.symbol,
            "initial_capital": self.initial_capital,
            "n_simulations": self.n_simulations,
            "n_trades_per_sim": self.n_trades_per_sim,
            "ruin_threshold": self.ruin_threshold,
            "risk_of_ruin_pct": round(risk_of_ruin, 2),
            "profit_probability_pct": round(profit_prob, 2),
            "mean_final_equity": round(float(np.mean(finals)), 2),
            "median_final_equity": round(float(ci_50), 2),
            "std_final_equity": round(float(np.std(finals)), 2),
            "ci_5": round(float(ci_5), 2),
            "ci_25": round(float(ci_25), 2),
            "ci_50": round(float(ci_50), 2),
            "ci_75": round(float(ci_75), 2),
            "ci_95": round(float(ci_95), 2),
            "mean_max_drawdown_pct": round(float(np.mean(dds)), 2),
            "worst_max_drawdown_pct": round(float(np.min(dds)), 2),
            "best_max_drawdown_pct": round(float(np.max(dds)), 2),
            "mean_pnl_pct": round(float(np.mean(pnls)), 2),
            "mean_win_rate_pct": round(float(np.mean(wrs) * 100), 2),
            "sharpe_ratio": round(float(np.mean(pnls) / (np.std(pnls) + 1e-12) * np.sqrt(252)), 2),
        }

    def to_dict(self) -> Dict:
        """Serialize results to dictionary."""
        return {
            "stats": self.stats,
            "symbol": self.symbol,
            "initial_capital": self.initial_capital,
            "n_simulations": self.n_simulations,
            "n_trades_per_sim": self.n_trades_per_sim,
            "final_equities": self.final_equities,
            "max_drawdowns": self.max_drawdowns,
        }

    def summary_text(self) -> str:
        """Return a formatted text summary for CLI output."""
        s = self.stats
        if not s:
            return "No simulation results available."

        lines = [
            f"\n{'='*60}",
            f"  MONTE CARLO SIMULATION RESULTS — {s['symbol']}",
            f"{'='*60}",
            f"  Simulations:         {s['n_simulations']:,}",
            f"  Trades/sim:          {s['n_trades_per_sim']:,}",
            f"  Initial Capital:     ${s['initial_capital']:,.2f}",
            f"  Ruin Threshold:      {s['ruin_threshold']}",
            f"{'─'*60}",
            f"  RISK OF RUIN:        {s['risk_of_ruin_pct']:.2f}%",
            f"  Profit Probability:  {s['profit_probability_pct']:.2f}%",
            f"{'─'*60}",
            f"  Mean Final Equity:   ${s['mean_final_equity']:,.2f}",
            f"  Median Final Equity: ${s['ci_50']:,.2f}",
            f"  Std Final Equity:    ${s['std_final_equity']:,.2f}",
            f"{'─'*60}",
            f"  Confidence Intervals (Final Equity):",
            f"    5th percentile:    ${s['ci_5']:,.2f}",
            f"    25th percentile:   ${s['ci_25']:,.2f}",
            f"    50th percentile:   ${s['ci_50']:,.2f}",
            f"    75th percentile:   ${s['ci_75']:,.2f}",
            f"    95th percentile:   ${s['ci_95']:,.2f}",
            f"{'─'*60}",
            f"  Mean Max Drawdown:   {s['mean_max_drawdown_pct']:.2f}%",
            f"  Worst Max Drawdown:  {s['worst_max_drawdown_pct']:.2f}%",
            f"  Best Max Drawdown:   {s['best_max_drawdown_pct']:.2f}%",
            f"{'─'*60}",
            f"  Mean PnL:            {s['mean_pnl_pct']:.2f}%",
            f"  Mean Win Rate:       {s['mean_win_rate_pct']:.2f}%",
            f"  Sharpe Ratio:        {s['sharpe_ratio']:.2f}",
            f"{'='*60}",
        ]
        return "\n".join(lines)


class MonteCarloEngine:
    """
    Monte Carlo simulation engine.

    Supports two modes:
    1. From trade results: Resample from historical backtest trade PnLs
    2. From parameters: Use win_rate, avg_win, avg_loss to generate synthetic trades
    """

    def __init__(self, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed)

    def simulate_from_trades(
        self,
        trade_pnls: np.ndarray,
        trade_pnl_pcts: np.ndarray,
        symbol: str = "",
        initial_capital: float = 10000.0,
        n_simulations: int = 1000,
        n_trades: Optional[int] = None,
        ruin_threshold: float = 0.5,
        position_size_pct: float = 0.02,
    ) -> MonteCarloResult:
        """
        Run Monte Carlo by resampling from actual backtest trade results.

        Args:
            trade_pnls: Array of absolute PnL values from backtest
            trade_pnl_pcts: Array of PnL percentages from backtest
            symbol: Trading pair symbol
            initial_capital: Starting capital
            n_simulations: Number of simulation runs
            n_trades: Trades per simulation (default: len(trade_pnls))
            ruin_threshold: Fraction of initial capital considered "ruin"
            position_size_pct: Base position size as fraction of equity
        """
        result = MonteCarloResult(
            symbol=symbol,
            initial_capital=initial_capital,
            n_simulations=n_simulations,
            n_trades_per_sim=n_trades or len(trade_pnls),
            ruin_threshold=ruin_threshold,
        )

        n = len(trade_pnls)
        if n == 0:
            result.compute_stats()
            return result

        sim_trades = result.n_trades_per_sim

        for _ in range(n_simulations):
            # Resample trade PnLs with replacement
            indices = self.rng.integers(0, n, size=sim_trades)
            sampled_pcts = trade_pnl_pcts[indices]

            # Build equity curve
            equity = initial_capital
            equity_curve = [equity]
            max_dd = 0.0
            peak = equity
            wins = 0

            for pnl_pct in sampled_pcts:
                # Position sizing: risk a fraction of current equity
                position = equity * position_size_pct
                pnl = position * (pnl_pct / 100.0)
                equity += pnl
                equity = max(equity, 0.01)  # floor at 1 cent
                equity_curve.append(equity)

                if pnl > 0:
                    wins += 1

                peak = max(peak, equity)
                dd = (equity - peak) / peak * 100 if peak > 0 else 0
                max_dd = min(max_dd, dd)

            result.equity_curves.append(equity_curve)
            result.final_equities.append(equity)
            result.max_drawdowns.append(max_dd)
            result.total_pnls.append((equity - initial_capital) / initial_capital * 100)
            result.win_rates.append(wins / sim_trades if sim_trades > 0 else 0)

        result.compute_stats()
        return result

    def simulate_from_params(
        self,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        symbol: str = "",
        initial_capital: float = 10000.0,
        n_simulations: int = 1000,
        n_trades: int = 500,
        ruin_threshold: float = 0.5,
        position_size_pct: float = 0.02,
    ) -> MonteCarloResult:
        """
        Run Monte Carlo from statistical parameters.

        Args:
            win_rate: Historical win rate (0-1)
            avg_win_pct: Average winning trade percentage
            avg_loss_pct: Average losing trade percentage (positive number)
            symbol: Trading pair symbol
            initial_capital: Starting capital
            n_simulations: Number of simulation runs
            n_trades: Trades per simulation
            ruin_threshold: Fraction of initial capital considered "ruin"
            position_size_pct: Base position size as fraction of equity
        """
        result = MonteCarloResult(
            symbol=symbol,
            initial_capital=initial_capital,
            n_simulations=n_simulations,
            n_trades_per_sim=n_trades,
            ruin_threshold=ruin_threshold,
        )

        for _ in range(n_simulations):
            equity = initial_capital
            equity_curve = [equity]
            max_dd = 0.0
            peak = equity
            wins = 0

            for _ in range(n_trades):
                is_win = self.rng.random() < win_rate
                if is_win:
                    pnl_pct = avg_win_pct * (0.5 + self.rng.random())
                    wins += 1
                else:
                    pnl_pct = -avg_loss_pct * (0.5 + self.rng.random())

                position = equity * position_size_pct
                pnl = position * (pnl_pct / 100.0)
                equity += pnl
                equity = max(equity, 0.01)
                equity_curve.append(equity)

                peak = max(peak, equity)
                dd = (equity - peak) / peak * 100 if peak > 0 else 0
                max_dd = min(max_dd, dd)

            result.equity_curves.append(equity_curve)
            result.final_equities.append(equity)
            result.max_drawdowns.append(max_dd)
            result.total_pnls.append((equity - initial_capital) / initial_capital * 100)
            result.win_rates.append(wins / n_trades if n_trades > 0 else 0)

        result.compute_stats()
        return result


def run_monte_carlo_for_symbol(
    symbol: str,
    db_path: str = "ppmt.db",
    n_simulations: int = 1000,
    initial_capital: float = 10000.0,
    ruin_threshold: float = 0.5,
    position_size_pct: float = 0.02,
    seed: Optional[int] = None,
) -> MonteCarloResult:
    """
    High-level function: load data, run backtest, then Monte Carlo.
    This is what the CLI command calls.
    """
    import sqlite3
    from ppmt.engine.ppmt import run_rolling_backtest

    # Load candle data
    conn = sqlite3.connect(db_path)
    table_name = symbol.replace("/", "_").replace("-", "_")
    try:
        df = pd.read_sql_query(
            f"SELECT timestamp, open, high, low, close, volume FROM {table_name} ORDER BY timestamp",
            conn,
        )
    except Exception:
        # Try with prefix
        try:
            df = pd.read_sql_query(
                f"SELECT timestamp, open, high, low, close, volume FROM candles_{table_name} ORDER BY timestamp",
                conn,
            )
        except Exception:
            # Fallback: list tables and pick the right one
            tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", conn)
            matching = [t for t in tables["name"] if table_name.lower() in t.lower()]
            if matching:
                df = pd.read_sql_query(
                    f"SELECT timestamp, open, high, low, close, volume FROM {matching[0]} ORDER BY timestamp",
                    conn,
                )
            else:
                conn.close()
                raise ValueError(f"No data found for {symbol} in {db_path}")
    finally:
        conn.close()

    if len(df) < 100:
        raise ValueError(f"Insufficient data for {symbol}: only {len(df)} candles")

    logger.info(f"Loaded {len(df)} candles for {symbol}, running backtest...")

    # Run backtest
    bt_result = run_rolling_backtest(df, symbol=symbol, initial_capital=initial_capital)

    if not bt_result.trades:
        raise ValueError(f"Backtest produced no trades for {symbol}")

    logger.info(f"Backtest: {bt_result.total_trades} trades, "
                f"WR={bt_result.win_rate:.1%}, PnL={bt_result.total_pnl_pct:.1f}%")

    # Extract trade PnLs for Monte Carlo
    trade_pnl_pcts = np.array([t["pnl_pct"] for t in bt_result.trades])
    trade_pnls = np.array([t["pnl"] for t in bt_result.trades])

    # Run Monte Carlo
    engine = MonteCarloEngine(seed=seed)
    mc_result = engine.simulate_from_trades(
        trade_pnls=trade_pnls,
        trade_pnl_pcts=trade_pnl_pcts,
        symbol=symbol,
        initial_capital=initial_capital,
        n_simulations=n_simulations,
        ruin_threshold=ruin_threshold,
        position_size_pct=position_size_pct,
    )

    # Attach backtest summary
    mc_result.stats["backtest_total_trades"] = bt_result.total_trades
    mc_result.stats["backtest_win_rate"] = bt_result.win_rate
    mc_result.stats["backtest_pnl_pct"] = bt_result.total_pnl_pct
    mc_result.stats["backtest_max_dd_pct"] = bt_result.max_drawdown_pct

    return mc_result
