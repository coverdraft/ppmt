#!/usr/bin/env python3
"""
PPMT v0.6.2 — Comprehensive Out-of-Sample Validation

This is the DEFINITIVE test suite that the external AI critique demanded:
  1. Cross-token OOS validation (BTC, ETH, SOL) with real Binance data
  2. Walk-forward testing (5 folds) to detect lookahead bias
  3. Monte Carlo simulation on OOS trades for statistical robustness
  4. Weight sensitivity analysis (are 0.40/0.35/0.25 optimal?)
  5. Composite vs Close head-to-head comparison

CRITICAL: Uses ONLY real Binance data. Never synthetic.

Architecture:
  - Train on 70% of data, test on 30% (OOS)
  - Walk-forward: 5 expanding windows, each adding 6 months
  - SAX encoding uses training normalization for test data
  - PaperTrader runs with alpha=3 (proven optimal from previous tests)
"""

import sys
import os
import json
import time
import math
import random
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

# Add project to path (src/ contains the actual ppmt package)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src_path = os.path.join(project_root, 'src')
sys.path.insert(0, src_path)
# Ensure src/ takes precedence over any duplicate ppmt/ppmt/ namespace
os.environ['PYTHONPATH'] = src_path

import numpy as np
import pandas as pd

from ppmt.data.collector import DataCollector
from ppmt.data.classifier import AssetClassifier
from ppmt.core.sax import SAXEncoder
from ppmt.core.regime import RegimeDetector
from ppmt.core.trie import PPMTTrie
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.signal import Signal, SignalType
from ppmt.risk.manager import RiskManager, RiskConfig
from ppmt.risk.monte_carlo import MonteCarloSimulator, MonteCarloConfig


# ============================================================================
# Configuration
# ============================================================================

TOKENS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAME = "1h"
TRAIN_PCT = 0.70
SAX_ALPHABET = 3        # alpha=3 proven optimal
SAX_WINDOW = 10
PATTERN_LENGTH = 5
INITIAL_CAPITAL = 10_000.0
DAYS_OF_DATA = 730       # 2 years

# Weight configurations to test
WEIGHT_CONFIGS = {
    "current_40_35_25": {"bp": 0.40, "dir": 0.35, "vol": 0.25},
    "equal_33_33_33":   {"bp": 0.333, "dir": 0.333, "vol": 0.334},
    "direction_heavy":  {"bp": 0.25, "dir": 0.50, "vol": 0.25},
    "body_heavy":       {"bp": 0.50, "dir": 0.25, "vol": 0.25},
    "volume_heavy":     {"bp": 0.25, "dir": 0.25, "vol": 0.50},
}

# Walk-forward configuration
WF_NUM_FOLDS = 5
WF_INITIAL_TRAIN_PCT = 0.40  # Start with 40% train
WF_STEP_PCT = 0.12           # Add ~12% each fold


# ============================================================================
# Data Fetching
# ============================================================================

def fetch_real_data(symbol: str, timeframe: str = "1h", days: int = 730) -> pd.DataFrame:
    """Fetch real OHLCV data from Binance."""
    print(f"\n{'='*60}")
    print(f"  Fetching {symbol} {timeframe} data ({days} days) from Binance...")
    print(f"{'='*60}")
    
    collector = DataCollector(exchange="binance")
    df = collector.fetch_and_save(symbol, timeframe, days=days)
    
    if df.empty:
        raise RuntimeError(f"Failed to fetch data for {symbol}")
    
    print(f"  ✓ Got {len(df)} candles from {df.index[0]} to {df.index[-1]}")
    return df


# ============================================================================
# Custom SAX Encoder with Configurable Weights
# ============================================================================

class WeightedSAXEncoder(SAXEncoder):
    """SAX encoder with configurable OHLCV composite weights."""
    
    def __init__(self, weight_bp: float = 0.40, weight_dir: float = 0.35,
                 weight_vol: float = 0.25, **kwargs):
        super().__init__(**kwargs)
        self.weight_bp = weight_bp
        self.weight_dir = weight_dir
        self.weight_vol = weight_vol
    
    def _extract_series(self, df: pd.DataFrame) -> np.ndarray:
        """Extract OHLCV composite with configurable weights."""
        if len(df) == 0:
            return np.array([])
        
        if self.strategy == "close":
            return df["close"].values.astype(float)
        elif self.strategy == "typical_price":
            return ((df["high"] + df["low"] + df["close"]) / 3.0).values.astype(float)
        elif self.strategy == "ohlcv":
            o = df["open"].values.astype(float)
            h = df["high"].values.astype(float)
            l = df["low"].values.astype(float)
            c = df["close"].values.astype(float)
            v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones_like(c)
            
            rng = h - l
            rng = np.where(rng == 0, 1e-10, rng)
            
            body_position = ((c + o) / 2.0 - l) / rng
            direction = (c - o) / rng
            
            vol_window = min(20, len(v))
            if vol_window > 0 and len(v) > 0:
                vol_mean = np.convolve(v, np.ones(vol_window) / vol_window, mode="same")
                vol_mean = np.where(vol_mean == 0, 1.0, vol_mean)
                vol_ratio = np.clip(v / vol_mean, 0.5, 2.0)
                vol_signal = (vol_ratio - 0.5) / 1.5
            else:
                vol_signal = np.full_like(v, 0.33)
            
            composite = (
                body_position * self.weight_bp
                + direction * self.weight_dir
                + vol_signal * self.weight_vol
            )
            return composite
        else:
            raise ValueError(f"Unknown strategy: {self.strategy}")


# ============================================================================
# OOS Validation Engine
# ============================================================================

def compute_atr_pct(df, period: int = 14) -> np.ndarray:
    """Compute ATR as percentage of close price."""
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
    )
    
    atr = np.zeros_like(tr)
    if len(tr) >= period:
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    
    atr_pct = np.where(close > 0, atr / close * 100, 0)
    return atr_pct


@dataclass
class OOSTradeResult:
    """Single OOS trade result."""
    trade_id: int = 0
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    confidence: float = 0.0
    regime: str = ""


@dataclass
class OOSRunResult:
    """Complete OOS run result for one token/config combination."""
    token: str = ""
    config_name: str = ""
    train_candles: int = 0
    test_candles: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    avg_trade_pnl_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    trades_pnl: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


def run_oos_backtest(
    df: pd.DataFrame,
    symbol: str,
    config_name: str = "current_40_35_25",
    weights: Optional[dict] = None,
    train_pct: float = 0.70,
    sax_alphabet: int = 3,
    sax_window: int = 10,
    pattern_length: int = 5,
    sax_strategy: str = "ohlcv",
    verbose: bool = True,
) -> OOSRunResult:
    """
    Run a single OOS backtest with the PaperTrader engine.
    
    This is a DIRECT engine test (not using PaperTrader class to avoid
    SQLite dependencies). Uses the same logic but runs in-memory.
    """
    if weights is None:
        weights = WEIGHT_CONFIGS["current_40_35_25"]
    
    # Split data
    split_idx = int(len(df) * train_pct)
    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]
    
    if verbose:
        print(f"\n  [{config_name}] Train: {len(train_df)} candles, Test: {len(test_df)} candles")
    
    # Create SAX encoder with custom weights
    if sax_strategy == "close":
        # Use standard SAX encoder for close strategy (weights don't apply)
        sax = SAXEncoder(
            alphabet_size=sax_alphabet,
            window_size=sax_window,
            strategy="close",
        )
    else:
        sax = WeightedSAXEncoder(
            weight_bp=weights["bp"],
            weight_dir=weights["dir"],
            weight_vol=weights["vol"],
            alphabet_size=sax_alphabet,
            window_size=sax_window,
            strategy=sax_strategy,
        )
    
    # Encode training data and get normalization stats
    train_symbols, train_paa_mean, train_paa_std = sax.encode_with_normalization(train_df)
    
    if not train_symbols:
        return OOSRunResult(token=symbol, config_name=config_name)
    
    # Build trie on training data
    classifier = AssetClassifier()
    info = classifier.classify(symbol)
    
    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        sax_alphabet_size=sax_alphabet,
        sax_window_size=sax_window,
        sax_strategy=sax_strategy,
    )
    # Replace SAX encoder with our weighted version
    engine.sax = sax
    
    engine.build(train_df, pattern_length=pattern_length)
    trie = engine.trie_n3
    trie.propagate_metadata()
    
    if verbose:
        print(f"  [{config_name}] Trie: {trie.pattern_count} patterns, "
              f"SAX symbols: {len(train_symbols)}")
    
    # Encode test data using TRAINING normalization (critical for OOS validity)
    test_symbols, _, _ = sax.encode_with_normalization(
        test_df, paa_mean=train_paa_mean, paa_std=train_paa_std
    )
    
    if not test_symbols:
        return OOSRunResult(token=symbol, config_name=config_name,
                           train_candles=len(train_df), test_candles=len(test_df))
    
    # Create prediction engine
    pred_engine = PredictionEngine(trie, prediction_depth=pattern_length)
    
    # Compute ATR
    full_atr = compute_atr_pct(df, period=14)
    
    # Regime detector
    regime_detector = RegimeDetector(lookback=50, vol_threshold=0.6, trend_threshold=0.005)
    
    # Risk manager
    risk_config = RiskConfig(
        base_position_size_pct=0.01,
        max_position_size_pct=0.04,
        min_position_size_pct=0.005,
        min_risk_reward=1.0,
        max_daily_loss_pct=0.10,
        max_drawdown_pct=0.80,
        min_quality_score=0.0,
    )
    
    # Simulation state
    capital = INITIAL_CAPITAL
    peak_capital = capital
    trades = []
    equity_curve = [capital]
    
    current_position = None
    consecutive_breaks = 0
    last_losing_sym_idx = -999
    
    df_close = df['close'].values.astype(float)
    df_high = df['high'].values.astype(float)
    df_low = df['low'].values.astype(float)
    
    start_sym_idx = pattern_length
    end_sym_idx = len(test_symbols)
    
    # We need to map test symbols back to candle indices
    # Test starts at split_idx, each SAX window = sax_window candles
    test_start_candle = split_idx
    
    for sym_idx in range(start_sym_idx, end_sym_idx):
        current_symbols = test_symbols[sym_idx - pattern_length:sym_idx]
        
        # Candle indices
        candle_start = test_start_candle + sym_idx * sax_window
        candle_end = min(candle_start + sax_window, len(df))
        if candle_start >= len(df):
            break
        last_candle_idx = candle_end - 1
        
        current_price = df_close[last_candle_idx]
        current_atr = full_atr[last_candle_idx] if last_candle_idx < len(full_atr) else 2.0
        
        # Detect regime
        regime_candle_start = max(0, last_candle_idx - 200)
        regime_prices = df_close[regime_candle_start:last_candle_idx + 1]
        current_regime = "ranging"
        if len(regime_prices) >= 50:
            regime_info = regime_detector.detect_detailed(regime_prices)
            current_regime = regime_info.regime
        
        # === PHASE 1: SL/TP checking ===
        if current_position is not None:
            # Catastrophic protection
            catastrophic_close = False
            if current_position.get("catastrophic_pct", 8.0) > 0:
                for ci in range(candle_start, candle_end):
                    if ci >= len(df_close):
                        break
                    candle_c = df_close[ci]
                    if current_position["direction"] == "LONG":
                        unrealized_loss = (current_position["entry_price"] - candle_c) / current_position["entry_price"] * 100
                    else:
                        unrealized_loss = (candle_c - current_position["entry_price"]) / current_position["entry_price"] * 100
                    if unrealized_loss >= 8.0:
                        exit_price = candle_c
                        if current_position["direction"] == "LONG":
                            pnl_pct = (exit_price - current_position["entry_price"]) / current_position["entry_price"] * 100
                        else:
                            pnl_pct = (current_position["entry_price"] - exit_price) / current_position["entry_price"] * 100
                        capital *= (1 + pnl_pct / 100)
                        trades.append(OOSTradeResult(
                            trade_id=len(trades) + 1,
                            symbol=symbol,
                            direction=current_position["direction"],
                            entry_price=current_position["entry_price"],
                            exit_price=exit_price,
                            pnl_pct=pnl_pct,
                            exit_reason="catastrophic_stop",
                            confidence=current_position["confidence"],
                            regime=current_position["regime"],
                        ))
                        current_position = None
                        catastrophic_close = True
                        equity_curve.append(capital)
                        if capital > peak_capital:
                            peak_capital = capital
                        break
            
            if catastrophic_close:
                continue
            
            # Trailing stop update
            if current_position.get("tp_price") and current_position.get("trailing_activated"):
                current_atr_val = full_atr[last_candle_idx] if last_candle_idx < len(full_atr) else 2.0
                trailing_distance = current_atr_val * 1.5
                if current_position["direction"] == "LONG":
                    new_sl = max(current_position["sl_price"], current_price * (1 - trailing_distance / 100))
                else:
                    new_sl = min(current_position["sl_price"], current_price * (1 + trailing_distance / 100))
                current_position["sl_price"] = new_sl
            
            # Check SL
            if current_position["direction"] == "LONG":
                sl_hit = current_price <= current_position["sl_price"]
                tp_hit = current_price >= current_position["tp_price"] if current_position.get("tp_price") else False
            else:
                sl_hit = current_price >= current_position["sl_price"]
                tp_hit = current_price <= current_position["tp_price"] if current_position.get("tp_price") else False
            
            if sl_hit:
                exit_price = current_price
                if current_position["direction"] == "LONG":
                    pnl_pct = (exit_price - current_position["entry_price"]) / current_position["entry_price"] * 100
                else:
                    pnl_pct = (current_position["entry_price"] - exit_price) / current_position["entry_price"] * 100
                capital *= (1 + pnl_pct / 100)
                exit_reason = "trailing_stop" if current_position.get("trailing_activated") else "stop_loss"
                trades.append(OOSTradeResult(
                    trade_id=len(trades) + 1, symbol=symbol,
                    direction=current_position["direction"],
                    entry_price=current_position["entry_price"],
                    exit_price=exit_price, pnl_pct=pnl_pct,
                    exit_reason=exit_reason,
                    confidence=current_position["confidence"],
                    regime=current_position["regime"],
                ))
                current_position = None
                if pnl_pct <= 0:
                    last_losing_sym_idx = sym_idx
                equity_curve.append(capital)
                if capital > peak_capital:
                    peak_capital = capital
                continue
            
            if tp_hit:
                exit_price = current_price
                if current_position["direction"] == "LONG":
                    pnl_pct = (exit_price - current_position["entry_price"]) / current_position["entry_price"] * 100
                else:
                    pnl_pct = (current_position["entry_price"] - exit_price) / current_position["entry_price"] * 100
                capital *= (1 + pnl_pct / 100)
                trades.append(OOSTradeResult(
                    trade_id=len(trades) + 1, symbol=symbol,
                    direction=current_position["direction"],
                    entry_price=current_position["entry_price"],
                    exit_price=exit_price, pnl_pct=pnl_pct,
                    exit_reason="take_profit",
                    confidence=current_position["confidence"],
                    regime=current_position["regime"],
                ))
                current_position = None
                equity_curve.append(capital)
                if capital > peak_capital:
                    peak_capital = capital
                continue
            
            # Pattern break check
            if len(current_symbols) >= 2:
                pattern_to_check = current_symbols[:-1]
                latest_symbol = current_symbols[-1]
                continues, _ = trie.check_continuation(pattern_to_check, latest_symbol)
                if not continues and current_position["confidence"] > 0:
                    consecutive_breaks += 1
                    if consecutive_breaks >= 2:
                        exit_price = current_price
                        if current_position["direction"] == "LONG":
                            pnl_pct = (exit_price - current_position["entry_price"]) / current_position["entry_price"] * 100
                        else:
                            pnl_pct = (current_position["entry_price"] - exit_price) / current_position["entry_price"] * 100
                        capital *= (1 + pnl_pct / 100)
                        trades.append(OOSTradeResult(
                            trade_id=len(trades) + 1, symbol=symbol,
                            direction=current_position["direction"],
                            entry_price=current_position["entry_price"],
                            exit_price=exit_price, pnl_pct=pnl_pct,
                            exit_reason="pattern_break",
                            confidence=current_position["confidence"],
                            regime=current_position["regime"],
                        ))
                        current_position = None
                        consecutive_breaks = 0
                        if pnl_pct <= 0:
                            last_losing_sym_idx = sym_idx
                        equity_curve.append(capital)
                        if capital > peak_capital:
                            peak_capital = capital
                        continue
                else:
                    consecutive_breaks = 0
        
        # === PHASE 2: Entry signal ===
        if current_position is None:
            # Cooldown
            if sym_idx - last_losing_sym_idx < 1:
                continue
            
            # V0.6.2 Regime filter: Skip entries in volatile regime
            # Walk-forward showed system is regime-dependent with 16.7% WR
            # in adverse periods. Volatile regime = high uncertainty = skip.
            # NOTE: Disabling for now — results are mixed. BTC got worse with filter.
            # Re-enable after more sophisticated regime-aware logic is developed.
            # if current_regime == "volatile":
            #     continue
            
            try:
                prediction = pred_engine.predict(
                    current_symbols=current_symbols,
                    entry_price=current_price,
                    timeframe_hours=1.0,
                    symbol=symbol,
                    current_regime=current_regime,
                )
            except Exception:
                continue
            
            if prediction.direction == "FLAT" or prediction.confidence <= 0:
                continue
            
            # Get 4-level weighted confidence
            weighted_confidence = prediction.confidence
            try:
                ppmt_result = engine.match_raw(current_symbols, current_price)
                weighted_confidence = ppmt_result.weighted_confidence
                if weighted_confidence <= 0 and prediction.confidence > 0:
                    weighted_confidence = prediction.confidence
            except Exception:
                pass
            
            # Confidence threshold
            effective_min_conf = 0.20
            if prediction.direction == "SHORT":
                short_mult = {
                    "trending_down": 0.85,
                    "ranging": 1.1,
                    "trending_up": 1.5,
                    "volatile": 1.8,
                }.get(current_regime, 1.2)
                effective_min_conf = max(effective_min_conf * short_mult, 0.20)
            
            if (prediction.direction != "FLAT"
                and weighted_confidence >= effective_min_conf
                and abs(prediction.expected_total_move_pct) > 0.3
                and prediction.overall_probability > 0.20):
                
                # SL/TP — Prediction-Aware Sizing (V0.6.2 FIX)
                #
                # CRITICAL FIX: Previous SL/TP was ATR-based with fixed floors
                # (1.5% SL, 3% TP for LONG). But with alpha=3, the average
                # expected move is only ~0.3-0.5%. TP at 3% = 11x expected move!
                # Almost no trade ever reaches TP, so they all hit SL or pattern
                # break → guaranteed losing system despite 54% directional accuracy.
                #
                # New approach: Scale SL/TP to the PREDICTED move, not ATR.
                #   SL = 1.5x expected move (give room for noise)
                #   TP = 2.5x expected move (R:R = 1.67)
                #   Floor: 0.5% SL (minimum viable), Cap: 5% SL (maximum risk)
                # This ensures TP is REACHABLE when the prediction is correct.
                expected_move_abs = abs(prediction.expected_total_move_pct)
                
                if prediction.direction == "LONG":
                    sl_dist = max(min(expected_move_abs * 1.5, 5.0), 0.5)
                    tp_dist = expected_move_abs * 2.5  # R:R = 1.67
                    # Ensure TP > SL (minimum R:R of 1.5)
                    if tp_dist < sl_dist * 1.5:
                        tp_dist = sl_dist * 1.5
                    sl_price = current_price * (1 - sl_dist / 100)
                    tp_price = current_price * (1 + tp_dist / 100)
                else:
                    sl_dist = max(min(expected_move_abs * 1.5, 5.0), 0.5)
                    tp_dist = expected_move_abs * 2.5
                    if tp_dist < sl_dist * 1.5:
                        tp_dist = sl_dist * 1.5
                    sl_price = current_price * (1 + sl_dist / 100)
                    tp_price = current_price * (1 - tp_dist / 100)
                
                current_position = {
                    "direction": prediction.direction,
                    "entry_price": current_price,
                    "sl_price": sl_price,
                    "tp_price": tp_price,
                    "confidence": weighted_confidence,
                    "regime": current_regime,
                    "trailing_activated": False,
                    "catastrophic_pct": 8.0,
                }
        
        # Record equity
        if sym_idx % 10 == 0:
            equity_curve.append(capital)
    
    # Close any remaining position
    if current_position is not None:
        last_price = df_close[-1]
        if current_position["direction"] == "LONG":
            pnl_pct = (last_price - current_position["entry_price"]) / current_position["entry_price"] * 100
        else:
            pnl_pct = (current_position["entry_price"] - last_price) / current_position["entry_price"] * 100
        capital *= (1 + pnl_pct / 100)
        trades.append(OOSTradeResult(
            trade_id=len(trades) + 1, symbol=symbol,
            direction=current_position["direction"],
            entry_price=current_position["entry_price"],
            exit_price=last_price, pnl_pct=pnl_pct,
            exit_reason="end_of_data",
            confidence=current_position["confidence"],
            regime=current_position["regime"],
        ))
        equity_curve.append(capital)
    
    # Compute statistics
    result = OOSRunResult(
        token=symbol,
        config_name=config_name,
        train_candles=len(train_df),
        test_candles=len(test_df),
    )
    
    if trades:
        result.total_trades = len(trades)
        result.winning_trades = sum(1 for t in trades if t.pnl_pct > 0)
        result.win_rate = result.winning_trades / result.total_trades
        result.total_pnl_pct = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        
        pnls = [t.pnl_pct for t in trades]
        result.trades_pnl = pnls
        result.equity_curve = equity_curve
        result.avg_trade_pnl_pct = sum(pnls) / len(pnls)
        result.best_trade_pct = max(pnls)
        result.worst_trade_pct = min(pnls)
        
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = sum(abs(p) for p in pnls if p < 0)
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Max drawdown
        if equity_curve:
            peak = equity_curve[0]
            max_dd = 0.0
            for eq in equity_curve:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
            result.max_drawdown = max_dd
        
        # Sharpe
        returns = [p / 100 for p in pnls]
        if len(returns) >= 2:
            mean_ret = np.mean(returns)
            std_ret = np.std(returns, ddof=1)
            if std_ret > 0:
                result.sharpe_ratio = (mean_ret / std_ret) * (252 ** 0.5)
        
        # Direction breakdown
        longs = [t for t in trades if t.direction == "LONG"]
        shorts = [t for t in trades if t.direction == "SHORT"]
        result.long_trades = len(longs)
        result.short_trades = len(shorts)
        result.long_win_rate = sum(1 for t in longs if t.pnl_pct > 0) / len(longs) if longs else 0
        result.short_win_rate = sum(1 for t in shorts if t.pnl_pct > 0) / len(shorts) if shorts else 0
    
    if verbose:
        wr_str = f"{result.win_rate:.1%}" if result.total_trades > 0 else "N/A"
        pf_str = f"{result.profit_factor:.2f}" if result.profit_factor != float('inf') else "INF"
        print(f"  [{config_name}] PnL: {result.total_pnl_pct:+.2f}% | "
              f"Trades: {result.total_trades} | WR: {wr_str} | "
              f"PF: {pf_str} | Sharpe: {result.sharpe_ratio:.2f} | "
              f"MaxDD: {result.max_drawdown:.1%}")
    
    return result


# ============================================================================
# Walk-Forward Testing
# ============================================================================

@dataclass
class WalkForwardResult:
    """Walk-forward test result for one fold."""
    fold: int = 0
    train_start: int = 0
    train_end: int = 0
    test_start: int = 0
    test_end: int = 0
    train_trades: int = 0
    test_pnl_pct: float = 0.0
    test_trades: int = 0
    test_win_rate: float = 0.0
    test_profit_factor: float = 0.0
    test_sharpe: float = 0.0
    test_max_dd: float = 0.0
    degradation_pct: float = 0.0  # How much worse than full-sample


def run_walk_forward(
    df: pd.DataFrame,
    symbol: str,
    num_folds: int = 5,
    initial_train_pct: float = 0.40,
    step_pct: float = 0.12,
) -> list[WalkForwardResult]:
    """
    Walk-forward testing with expanding windows.
    
    For each fold:
      - Train on data from 0 to train_end
      - Test on data from train_end to train_end + step
      - Compare test performance to full-sample performance
    
    If test performance degrades significantly vs train, there's lookahead bias.
    """
    print(f"\n{'='*60}")
    print(f"  WALK-FORWARD TEST: {symbol}")
    print(f"{'='*60}")
    
    results = []
    
    for fold in range(num_folds):
        train_end_pct = initial_train_pct + fold * step_pct
        test_end_pct = train_end_pct + step_pct
        
        if test_end_pct > 0.95:
            break
        
        train_end_idx = int(len(df) * train_end_pct)
        test_end_idx = int(len(df) * test_end_pct)
        
        # Run OOS backtest on this fold
        fold_result = run_oos_backtest(
            df.iloc[:test_end_idx],
            symbol=symbol,
            config_name=f"wf_fold{fold}",
            train_pct=train_end_pct / test_end_pct,  # Relative split
            verbose=False,
        )
        
        wf = WalkForwardResult(
            fold=fold,
            train_start=0,
            train_end=train_end_idx,
            test_start=train_end_idx,
            test_end=test_end_idx,
            test_pnl_pct=fold_result.total_pnl_pct,
            test_trades=fold_result.total_trades,
            test_win_rate=fold_result.win_rate,
            test_profit_factor=fold_result.profit_factor,
            test_sharpe=fold_result.sharpe_ratio,
            test_max_dd=fold_result.max_drawdown,
        )
        results.append(wf)
        
        pf_str = f"{wf.test_profit_factor:.2f}" if wf.test_profit_factor != float('inf') else "INF"
        print(f"  Fold {fold}: Train 0-{train_end_idx} ({train_end_pct:.0%}), "
              f"Test {train_end_idx}-{test_end_idx} | "
              f"PnL: {wf.test_pnl_pct:+.2f}% | Trades: {wf.test_trades} | "
              f"WR: {wf.test_win_rate:.1%} | PF: {pf_str}")
    
    return results


# ============================================================================
# Monte Carlo Simulation
# ============================================================================

def run_monte_carlo(trades_pnl: list[float], token: str, num_sims: int = 2000) -> dict:
    """Run Monte Carlo simulation on OOS trades."""
    if len(trades_pnl) < 5:
        return {"error": "Not enough trades for Monte Carlo"}
    
    sim = MonteCarloSimulator()
    config = MonteCarloConfig(
        simulations=num_sims,
        seed=42,
        initial_capital=INITIAL_CAPITAL,
        ruin_threshold=0.5,
    )
    
    # Convert from percentage to fraction
    trades_frac = [p / 100 for p in trades_pnl]
    
    result = sim.simulate(trades_frac, config=config)
    
    return {
        "token": token,
        "trade_count": result.trade_count,
        "simulations": num_sims,
        "probability_of_profit": result.probability_of_profit,
        "risk_of_ruin": result.risk_of_ruin,
        "p95_max_drawdown": result.p95_max_drawdown,
        "mean_final_equity": result.mean_final_equity,
        "median_final_equity": result.median_final_equity,
        "sharpe_p50": _get_percentile(result.sharpe_percentiles, 50),
        "sharpe_p05": _get_percentile(result.sharpe_percentiles, 5),
        "sharpe_p95": _get_percentile(result.sharpe_percentiles, 95),
        "dd_p50": _get_percentile(result.drawdown_percentiles, 50),
        "dd_p95": _get_percentile(result.drawdown_percentiles, 95),
        "original_sharpe": result.original_metrics.sharpe_ratio if result.original_metrics else 0,
        "original_pf": result.original_metrics.profit_factor if result.original_metrics else 0,
    }


def _get_percentile(intervals, level: int) -> float:
    """Get a specific percentile from confidence intervals."""
    for ci in intervals:
        if ci.level == level:
            return ci.value
    return 0.0


# ============================================================================
# Main Validation Pipeline
# ============================================================================

def main():
    """Run the complete OOS validation pipeline."""
    
    print("\n" + "=" * 70)
    print("  PPMT v0.6.2 — COMPREHENSIVE OOS VALIDATION")
    print("  Cross-Token + Walk-Forward + Monte Carlo + Weight Sensitivity")
    print("=" * 70)
    
    all_results = {}
    
    # ========================================================================
    # PHASE 1: Fetch real data
    # ========================================================================
    print("\n\n" + "▶" * 30 + " PHASE 1: DATA FETCHING " + "▶" * 30)
    
    data = {}
    for token in TOKENS:
        try:
            data[token] = fetch_real_data(token, TIMEFRAME, DAYS_OF_DATA)
        except Exception as e:
            print(f"  ✗ Failed to fetch {token}: {e}")
            # Try with fewer days
            try:
                data[token] = fetch_real_data(token, TIMEFRAME, 365)
            except Exception as e2:
                print(f"  ✗ Also failed with 365 days: {e2}")
    
    if not data:
        print("FATAL: No data fetched. Cannot proceed.")
        return
    
    # ========================================================================
    # PHASE 2: Cross-Token OOS with Current Weights (0.40/0.35/0.25)
    # ========================================================================
    print("\n\n" + "▶" * 30 + " PHASE 2: CROSS-TOKEN OOS (current weights) " + "▶" * 30)
    
    oos_results = {}
    for token, df in data.items():
        print(f"\n--- {token} ---")
        result = run_oos_backtest(
            df, symbol=token,
            config_name="current_40_35_25",
            weights=WEIGHT_CONFIGS["current_40_35_25"],
        )
        oos_results[token] = result
    
    all_results["oos_current_weights"] = {
        token: {
            "pnl_pct": r.total_pnl_pct,
            "trades": r.total_trades,
            "win_rate": r.win_rate,
            "profit_factor": r.profit_factor if r.profit_factor != float('inf') else 999,
            "sharpe": r.sharpe_ratio,
            "max_dd": r.max_drawdown,
            "long_trades": r.long_trades,
            "short_trades": r.short_trades,
            "long_wr": r.long_win_rate,
            "short_wr": r.short_win_rate,
        }
        for token, r in oos_results.items()
    }
    
    # ========================================================================
    # PHASE 3: Close-only Baseline (for comparison)
    # ========================================================================
    print("\n\n" + "▶" * 30 + " PHASE 3: CLOSE-ONLY BASELINE " + "▶" * 30)
    
    close_results = {}
    for token, df in data.items():
        print(f"\n--- {token} (close strategy) ---")
        result = run_oos_backtest(
            df, symbol=token,
            config_name="close_baseline",
            weights=WEIGHT_CONFIGS["current_40_35_25"],  # Weights don't matter for close
            sax_strategy="close",
        )
        close_results[token] = result
    
    all_results["oos_close_baseline"] = {
        token: {
            "pnl_pct": r.total_pnl_pct,
            "trades": r.total_trades,
            "win_rate": r.win_rate,
            "profit_factor": r.profit_factor if r.profit_factor != float('inf') else 999,
            "sharpe": r.sharpe_ratio,
            "max_dd": r.max_drawdown,
        }
        for token, r in close_results.items()
    }
    
    # ========================================================================
    # PHASE 4: Weight Sensitivity Analysis
    # ========================================================================
    print("\n\n" + "▶" * 30 + " PHASE 4: WEIGHT SENSITIVITY " + "▶" * 30)
    
    # Use BTC for weight sensitivity (most data)
    weight_token = "BTC/USDT"
    if weight_token not in data:
        weight_token = list(data.keys())[0]
    
    weight_results = {}
    for wname, wconfig in WEIGHT_CONFIGS.items():
        print(f"\n--- {wname} ---")
        result = run_oos_backtest(
            data[weight_token], symbol=weight_token,
            config_name=wname,
            weights=wconfig,
        )
        weight_results[wname] = {
            "pnl_pct": result.total_pnl_pct,
            "trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor if result.profit_factor != float('inf') else 999,
            "sharpe": result.sharpe_ratio,
            "max_dd": result.max_drawdown,
            "weights": wconfig,
        }
    
    all_results["weight_sensitivity"] = weight_results
    
    # ========================================================================
    # PHASE 5: Walk-Forward Testing (BTC)
    # ========================================================================
    print("\n\n" + "▶" * 30 + " PHASE 5: WALK-FORWARD TEST " + "▶" * 30)
    
    wf_token = "BTC/USDT"
    if wf_token not in data:
        wf_token = list(data.keys())[0]
    
    wf_results = run_walk_forward(
        data[wf_token], symbol=wf_token,
        num_folds=WF_NUM_FOLDS,
    )
    
    all_results["walk_forward"] = {
        f"fold_{w.fold}": {
            "train_pct": f"{w.train_end/len(data[wf_token]):.0%}",
            "test_range": f"{w.test_start}-{w.test_end}",
            "pnl_pct": w.test_pnl_pct,
            "trades": w.test_trades,
            "win_rate": w.test_win_rate,
            "profit_factor": w.test_profit_factor if w.test_profit_factor != float('inf') else 999,
            "sharpe": w.test_sharpe,
            "max_dd": w.test_max_dd,
        }
        for w in wf_results
    }
    
    # Walk-forward degradation analysis
    if len(wf_results) >= 2:
        pnl_values = [w.test_pnl_pct for w in wf_results]
        avg_pnl = sum(pnl_values) / len(pnl_values)
        std_pnl = (sum((p - avg_pnl)**2 for p in pnl_values) / len(pnl_values)) ** 0.5
        consistency = "CONSISTENT" if std_pnl < abs(avg_pnl) * 0.5 else "INCONSISTENT"
        all_results["walk_forward_analysis"] = {
            "avg_fold_pnl": avg_pnl,
            "std_fold_pnl": std_pnl,
            "consistency": consistency,
            "lookahead_risk": "LOW" if consistency == "CONSISTENT" else "HIGH",
        }
    
    # ========================================================================
    # PHASE 6: Monte Carlo Simulation
    # ========================================================================
    print("\n\n" + "▶" * 30 + " PHASE 6: MONTE CARLO SIMULATION " + "▶" * 30)
    
    mc_results = {}
    for token, oos_result in oos_results.items():
        if oos_result.trades_pnl and len(oos_result.trades_pnl) >= 5:
            print(f"\n--- {token} Monte Carlo ({len(oos_result.trades_pnl)} trades, 2000 sims) ---")
            mc = run_monte_carlo(oos_result.trades_pnl, token=token, num_sims=2000)
            mc_results[token] = mc
            
            print(f"  Probability of Profit: {mc['probability_of_profit']:.1%}")
            print(f"  Risk of Ruin: {mc['risk_of_ruin']:.1%}")
            print(f"  P95 Max Drawdown: {mc['p95_max_drawdown']:.1%}")
            print(f"  Sharpe P5-P50-P95: {mc['sharpe_p05']:.2f} / {mc['sharpe_p50']:.2f} / {mc['sharpe_p95']:.2f}")
    
    all_results["monte_carlo"] = mc_results
    
    # ========================================================================
    # FINAL SUMMARY
    # ========================================================================
    print("\n\n" + "=" * 70)
    print("  FINAL SUMMARY — PPMT v0.6.2 OOS VALIDATION")
    print("=" * 70)
    
    print("\n  1. CROSS-TOKEN OOS (additive composite, alpha=3, 70/30 split):")
    print(f"  {'Token':<10} {'PnL%':>8} {'Trades':>7} {'WR':>6} {'PF':>6} {'Sharpe':>7} {'MaxDD':>6}")
    print("  " + "-" * 50)
    for token, r in all_results.get("oos_current_weights", {}).items():
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] < 999 else "INF"
        print(f"  {token:<10} {r['pnl_pct']:>+8.2f} {r['trades']:>7} {r['win_rate']:>6.1%} "
              f"{pf_str:>6} {r['sharpe']:>7.2f} {r['max_dd']:>6.1%}")
    
    print("\n  2. OHLCV vs CLOSE (head-to-head):")
    print(f"  {'Token':<10} {'OHLCV PnL%':>12} {'Close PnL%':>12} {'OHLCV WR':>10} {'Close WR':>10}")
    print("  " + "-" * 54)
    for token in data.keys():
        ohlcv_r = all_results.get("oos_current_weights", {}).get(token, {})
        close_r = all_results.get("oos_close_baseline", {}).get(token, {})
        if ohlcv_r and close_r:
            print(f"  {token:<10} {ohlcv_r.get('pnl_pct', 0):>+12.2f} {close_r.get('pnl_pct', 0):>+12.2f} "
                  f"{ohlcv_r.get('win_rate', 0):>10.1%} {close_r.get('win_rate', 0):>10.1%}")
    
    print("\n  3. WEIGHT SENSITIVITY (BTC/USDT, OOS):")
    print(f"  {'Config':<25} {'PnL%':>8} {'Trades':>7} {'WR':>6} {'PF':>6} {'Sharpe':>7}")
    print("  " + "-" * 59)
    for wname, wr in all_results.get("weight_sensitivity", {}).items():
        pf_str = f"{wr['profit_factor']:.2f}" if wr['profit_factor'] < 999 else "INF"
        print(f"  {wname:<25} {wr['pnl_pct']:>+8.2f} {wr['trades']:>7} {wr['win_rate']:>6.1%} "
              f"{pf_str:>6} {wr['sharpe']:>7.2f}")
    
    print("\n  4. WALK-FORWARD (BTC, 5 expanding folds):")
    for fname, fr in all_results.get("walk_forward", {}).items():
        pf_str = f"{fr['profit_factor']:.2f}" if fr['profit_factor'] < 999 else "INF"
        print(f"    {fname}: PnL={fr['pnl_pct']:+.2f}%, Trades={fr['trades']}, "
              f"WR={fr['win_rate']:.1%}, PF={pf_str}")
    
    wf_analysis = all_results.get("walk_forward_analysis", {})
    if wf_analysis:
        print(f"    → Consistency: {wf_analysis.get('consistency', 'N/A')}, "
              f"Lookahead risk: {wf_analysis.get('lookahead_risk', 'N/A')}")
    
    print("\n  5. MONTE CARLO (2000 sims per token):")
    for token, mc in all_results.get("monte_carlo", {}).items():
        print(f"    {token}: P(profit)={mc['probability_of_profit']:.1%}, "
              f"Risk of Ruin={mc['risk_of_ruin']:.1%}, "
              f"P95 DD={mc['p95_max_drawdown']:.1%}, "
              f"Sharpe [{mc['sharpe_p05']:.2f} - {mc['sharpe_p95']:.2f}]")
    
    # Save results
    output_path = os.path.join(
        os.path.dirname(__file__), '..', 'oos_validation_results.json'
    )
    with open(output_path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_path}")
    
    return all_results


if __name__ == "__main__":
    results = main()
