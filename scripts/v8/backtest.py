"""
backtest.py — v8 Pattern-Informed Realistic Backtest

KEY RULES from pattern analysis:
1. TIME STOP at 30min (6 bars) — the most powerful filter
2. NO averaging down on BREAKOUT_DOWN entries (PF 0.65)
3. Max 3 entries per trade (with EV confirmation only)
4. ATR-adaptive TP/SL matching training labels
5. Conservative "both hit same bar" = SL wins

From real pattern analysis:
  BREAKOUT_UP:  88.2% WR, PF 3.10 → these should be our bread and butter
  BREAKOUT_DOWN: 66.3% WR, PF 0.65 → filter aggressively
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .features import FEATURE_NAMES
from .model import (
    TP_ATR_MULT, SL_ATR_MULT, LOOKAHEAD, ATR_LAG_OFFSET,
    EV_THRESHOLD_LONG, EV_THRESHOLD_SHORT, DEFAULT_COST,
    MAX_HOLD_BARS, MAX_ENTRIES_PER_TRADE, MAX_CONCURRENT_POSITIONS,
)

LOG = logging.getLogger("v8_backtest")


@dataclass
class TradeResult:
    """Result of a single trade."""
    entry_bar: int
    exit_bar: int
    direction: str
    entry_price: float
    exit_price: float
    exit_reason: str       # "TP", "SL", "TIME_STOP"
    pnl_pct: float
    cost_pct: float
    pnl_net_pct: float
    ev_prediction: float
    atr_at_entry: float
    bars_held: int
    symbol: str = ""


@dataclass
class BacktestResult:
    """Aggregated backtest results."""
    n_trades: int = 0
    n_long: int = 0
    n_short: int = 0
    n_tp: int = 0
    n_sl: int = 0
    n_time_stop: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    total_pnl_pct: float = 0.0
    sharpe: float = 0.0
    max_dd_pct: float = 0.0
    profit_factor: float = 0.0
    calmar: float = 0.0
    avg_hold_bars: float = 0.0
    avg_hold_min: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: np.ndarray = field(default_factory=lambda: np.array([]))
    per_token_stats: dict = field(default_factory=dict)


def run_backtest(
    predictions: np.ndarray,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    atr_14: np.ndarray,
    symbols: Optional[np.ndarray] = None,
    ev_threshold_long: float = EV_THRESHOLD_LONG,
    ev_threshold_short: float = EV_THRESHOLD_SHORT,
    tp_atr_mult: float = TP_ATR_MULT,
    sl_atr_mult: float = SL_ATR_MULT,
    max_hold: int = MAX_HOLD_BARS,
    atr_lag_offset: int = ATR_LAG_OFFSET,
    cost_pct: float = DEFAULT_COST,
    max_concurrent: int = MAX_CONCURRENT_POSITIONS,
    position_size_pct: float = 100.0,
) -> BacktestResult:
    """Run realistic backtest with hard rules from pattern analysis."""
    n = len(predictions)
    trades = []
    equity = 100.0
    equity_curve = np.full(n, 100.0)

    positions = {}  # symbol → position dict

    for i in range(atr_lag_offset, n):
        symbol = str(symbols[i]) if symbols is not None else f"bar_{i}"

        # ── Check existing position for this symbol ──
        if symbol in positions:
            pos = positions[symbol]
            should_close = False
            exit_reason = ""
            exit_price = closes[i]
            bars_held = i - pos["entry_bar"]

            # Check TP
            if pos["direction"] == "LONG" and highs[i] >= pos["tp_price"]:
                should_close = True
                exit_reason = "TP"
                exit_price = pos["tp_price"]
            elif pos["direction"] == "SHORT" and lows[i] <= pos["tp_price"]:
                should_close = True
                exit_reason = "TP"
                exit_price = pos["tp_price"]

            # Check SL (conservative: SL wins if both hit same bar)
            if pos["direction"] == "LONG" and lows[i] <= pos["sl_price"]:
                if should_close and exit_reason == "TP":
                    exit_reason = "SL"
                    exit_price = pos["sl_price"]
                elif not should_close:
                    should_close = True
                    exit_reason = "SL"
                    exit_price = pos["sl_price"]
            elif pos["direction"] == "SHORT" and highs[i] >= pos["sl_price"]:
                if should_close and exit_reason == "TP":
                    exit_reason = "SL"
                    exit_price = pos["sl_price"]
                elif not should_close:
                    should_close = True
                    exit_reason = "SL"
                    exit_price = pos["sl_price"]

            # TIME STOP — the most powerful filter
            if bars_held >= max_hold and not should_close:
                should_close = True
                exit_reason = "TIME_STOP"
                exit_price = closes[i]

            if should_close:
                if pos["direction"] == "LONG":
                    pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                else:
                    pnl_pct = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100

                size_mult = pos["size"] / 100.0
                pnl_net = (pnl_pct - cost_pct) * size_mult
                equity += pnl_net

                trades.append(TradeResult(
                    entry_bar=pos["entry_bar"],
                    exit_bar=i,
                    direction=pos["direction"],
                    entry_price=pos["entry_price"],
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl_pct=pnl_pct * size_mult,
                    cost_pct=cost_pct * size_mult,
                    pnl_net_pct=pnl_net,
                    ev_prediction=pos["ev_pred"],
                    atr_at_entry=pos["atr"],
                    bars_held=bars_held,
                    symbol=symbol,
                ))
                del positions[symbol]

        # ── Check for new signal ──
        if symbol in positions or len(positions) >= max_concurrent:
            equity_curve[i] = equity
            continue

        pred = predictions[i]
        lagged_atr = atr_14[i - atr_lag_offset] if i >= atr_lag_offset else 0

        if np.isnan(lagged_atr) or lagged_atr <= 0:
            equity_curve[i] = equity
            continue

        if pred > ev_threshold_long:
            direction = "LONG"
            tp_price = closes[i] + tp_atr_mult * lagged_atr
            sl_price = closes[i] - sl_atr_mult * lagged_atr
            size = min(pred / ev_threshold_long, 3.0) * position_size_pct
        elif pred < -ev_threshold_short:
            direction = "SHORT"
            tp_price = closes[i] - tp_atr_mult * lagged_atr
            sl_price = closes[i] + sl_atr_mult * lagged_atr
            size = min(abs(pred) / ev_threshold_short, 3.0) * position_size_pct
        else:
            equity_curve[i] = equity
            continue

        positions[symbol] = {
            "entry_bar": i,
            "direction": direction,
            "entry_price": closes[i],
            "tp_price": tp_price,
            "sl_price": sl_price,
            "ev_pred": pred,
            "atr": lagged_atr,
            "symbol": symbol,
            "size": size,
        }

        equity_curve[i] = equity

    return _compute_stats(trades, equity_curve, symbols)


def _compute_stats(
    trades: list[TradeResult],
    equity_curve: np.ndarray,
    symbols: Optional[np.ndarray] = None,
) -> BacktestResult:
    """Compute comprehensive backtest statistics."""
    if not trades:
        return BacktestResult(equity_curve=equity_curve)

    pnls = [t.pnl_net_pct for t in trades]
    n_trades = len(trades)
    n_long = sum(1 for t in trades if t.direction == "LONG")
    n_short = sum(1 for t in trades if t.direction == "SHORT")
    n_tp = sum(1 for t in trades if t.exit_reason == "TP")
    n_sl = sum(1 for t in trades if t.exit_reason == "SL")
    n_time_stop = sum(1 for t in trades if t.exit_reason == "TIME_STOP")

    win_rate = sum(1 for p in pnls if p > 0) / n_trades
    avg_pnl = np.mean(pnls)
    total_pnl = sum(pnls)

    sharpe = np.mean(pnls) / max(np.std(pnls), 1e-10) * np.sqrt(288 * 365) if len(pnls) > 1 else 0

    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = cumulative - running_max
    max_dd = float(drawdowns.min()) if len(drawdowns) > 0 else 0

    gains = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    pf = gains / max(losses, 1e-10)

    avg_hold = np.mean([t.bars_held for t in trades])

    per_token = {}
    unique_symbols = set(t.symbol for t in trades if t.symbol)
    for sym in unique_symbols:
        sym_trades = [t for t in trades if t.symbol == sym]
        sym_pnls = [t.pnl_net_pct for t in sym_trades]
        per_token[sym] = {
            "n_trades": len(sym_trades),
            "win_rate": sum(1 for p in sym_pnls if p > 0) / max(len(sym_pnls), 1),
            "total_pnl": sum(sym_pnls),
            "sharpe": np.mean(sym_pnls) / max(np.std(sym_pnls), 1e-10) * np.sqrt(288 * 365) if len(sym_pnls) > 1 else 0,
        }

    return BacktestResult(
        n_trades=n_trades,
        n_long=n_long,
        n_short=n_short,
        n_tp=n_tp,
        n_sl=n_sl,
        n_time_stop=n_time_stop,
        win_rate=win_rate,
        avg_pnl_pct=avg_pnl,
        total_pnl_pct=total_pnl,
        sharpe=sharpe,
        max_dd_pct=max_dd,
        profit_factor=pf,
        calmar=0,
        avg_hold_bars=avg_hold,
        avg_hold_min=avg_hold * 5,
        trades=trades,
        equity_curve=equity_curve,
        per_token_stats=per_token,
    )


def print_backtest_report(result: BacktestResult) -> None:
    """Print comprehensive backtest report."""
    print("\n" + "=" * 80)
    print("V8 PATTERN-BASED BACKTEST REPORT")
    print("=" * 80)

    print(f"\n  Trades:          {result.n_trades}")
    print(f"    LONG:          {result.n_long}  SHORT: {result.n_short}")
    print(f"    TP exits:      {result.n_tp}")
    print(f"    SL exits:      {result.n_sl}")
    print(f"    TIME STOP:     {result.n_time_stop}  (30min max hold)")
    print(f"  Win Rate:        {result.win_rate * 100:.1f}%")
    print(f"  Avg PnL:         {result.avg_pnl_pct:+.4f}%")
    print(f"  Total PnL:       {result.total_pnl_pct:+.2f}%")
    print(f"  Sharpe (ann):    {result.sharpe:.3f}")
    print(f"  Max DD:          {result.max_dd_pct:.2f}%")
    print(f"  Profit Factor:   {result.profit_factor:.2f}")
    print(f"  Avg Hold:        {result.avg_hold_bars:.1f} bars ({result.avg_hold_min:.0f} min)")

    if result.n_trades > 0:
        tp_pct = result.n_tp / result.n_trades * 100
        sl_pct = result.n_sl / result.n_trades * 100
        ts_pct = result.n_time_stop / result.n_trades * 100
        print(f"\n  Exit Analysis:")
        print(f"    TP:        {tp_pct:.1f}%  (target hit)")
        print(f"    SL:        {sl_pct:.1f}%  (stop hit)")
        print(f"    TIME_STOP: {ts_pct:.1f}%  (30min limit)")

    if result.per_token_stats:
        print(f"\n  Per-Token Stats:")
        print(f"  {'Symbol':<15} {'Trades':>7} {'WR%':>6} {'PnL%':>9} {'Sharpe':>8}")
        print(f"  {'-'*50}")
        for sym, stats in sorted(result.per_token_stats.items()):
            print(f"  {sym:<15} {stats['n_trades']:>7} {stats['win_rate']*100:>6.1f} "
                  f"{stats['total_pnl']:>+9.2f} {stats['sharpe']:>8.3f}")

    print("\n  HARD RULES (from pattern analysis):")
    print("    + Time stop at 30min (6 bars)")
    print("    + No averaging down on breakdown entries")
    print("    + Conservative SL-wins tiebreak")
    print("    + Max 3 entries per trade with EV confirmation")
    print("\n" + "=" * 80)
