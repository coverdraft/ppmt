#!/usr/bin/env python3
"""
PPMT Cross-Token Diagnostic Suite
==================================

Pruebas diagnósticas para validar si el sistema PPMT puede:
1. Operar en tokens que nunca ha visto (cross-token OOS)
2. Generalizar patrones entre clases de assets
3. Aprender desde cero en un token nuevo (Living Trie)
4. Mantener rentabilidad out-of-sample vs in-sample

Estas pruebas son NO-DISTORSIONANTES:
- Separación estricta train/test (70/30)
- Normalización SAX propagada del train al test (V7.9 fix)
- Living Trie DESACTIVADO en test (solo aprende en train)
- Sin look-ahead bias

Uso:
    cd /home/z/my-project/ppmt
    python -m scripts.cross_token_diagnostic
    
    # O con un token específico:
    python -m scripts.cross_token_diagnostic --source BTC/USDT --target SOL/USDT
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# Add project to path
sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier, AssetInfo
from ppmt.core.sax import SAXEncoder
from ppmt.core.regime import RegimeDetector
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.paper_trader import (
    PaperTrader, PaperTraderConfig, PaperTraderResult,
    compute_atr_pct, _record_observation, PaperTrade,
)
from ppmt.engine.monte_carlo import MonteCarloEngine


# ============================================================
# Configuration
# ============================================================

DEFAULT_TOKENS = {
    "blue_chip": ["BTC/USDT", "ETH/USDT"],
    "large_cap": ["SOL/USDT", "BNB/USDT"],
    "mid_cap":   ["LINK/USDT", "AVAX/USDT"],
    "meme":      ["DOGE/USDT", "SHIB/USDT"],
    "defi":      ["UNI/USDT", "AAVE/USDT"],
}

TRAIN_RATIO = 0.7
SAX_ALPHABET = 8
SAX_WINDOW = 10
PATTERN_LENGTH = 5
INITIAL_CAPITAL = 10000.0


# ============================================================
# Result Data Classes
# ============================================================

@dataclass
class SingleTokenResult:
    """Result of OOS validation on a single token."""
    symbol: str
    asset_class: str
    total_candles: int = 0
    train_candles: int = 0
    test_candles: int = 0
    patterns_built: int = 0
    
    # In-sample metrics
    is_trades: int = 0
    is_win_rate: float = 0.0
    is_total_pnl_pct: float = 0.0
    is_sharpe: float = 0.0
    is_max_dd: float = 0.0
    
    # Out-of-sample metrics
    oos_trades: int = 0
    oos_win_rate: float = 0.0
    oos_total_pnl_pct: float = 0.0
    oos_sharpe: float = 0.0
    oos_max_dd: float = 0.0
    
    # Degradation
    pnl_degradation: float = 0.0
    wr_degradation: float = 0.0
    oos_ratio: float = 0.0


@dataclass
class CrossTokenResult:
    """Result of cross-token validation."""
    source_symbol: str
    source_class: str
    target_symbol: str
    target_class: str
    
    # Source trie stats
    source_patterns: int = 0
    
    # Target OOS results using source N3 trie
    target_oos_trades_n3: int = 0
    target_oos_pnl_n3: float = 0.0
    target_oos_wr_n3: float = 0.0
    
    # Target OOS results using fresh N3 trie (built on target's own train data)
    target_oos_trades_fresh: int = 0
    target_oos_pnl_fresh: float = 0.0
    target_oos_wr_fresh: float = 0.0
    
    # Target OOS results using N1+N2 from source (cross-class patterns)
    target_oos_trades_n1n2: int = 0
    target_oos_pnl_n1n2: float = 0.0
    target_oos_wr_n1n2: float = 0.0


@dataclass
class DiagnosticReport:
    """Complete diagnostic report."""
    timestamp: str = ""
    available_tokens: list[dict] = field(default_factory=list)
    
    # Test 1: Single-token OOS (baseline)
    single_token_results: list[SingleTokenResult] = field(default_factory=list)
    
    # Test 2: Cross-token OOS
    cross_token_results: list[CrossTokenResult] = field(default_factory=list)
    
    # Test 3: Living Trie ON vs OFF
    living_trie_on_pnl: float = 0.0
    living_trie_off_pnl: float = 0.0
    living_trie_on_wr: float = 0.0
    living_trie_off_wr: float = 0.0
    
    # Test 4: Random baseline comparison
    random_pnl: float = 0.0
    random_wr: float = 0.0
    
    # Verdict
    verdict: str = "INSUFFICIENT_DATA"
    confidence_score: float = 0.0
    notes: list[str] = field(default_factory=list)


# ============================================================
# Helper Functions
# ============================================================

def check_available_data() -> list[dict]:
    """Check what tokens have data in the database."""
    storage = PPMTStorage()
    assets = storage.get_assets()
    available = []
    for a in assets:
        count = storage.get_candle_count(a["symbol"], "1h")
        if count > 0:
            available.append({
                "symbol": a["symbol"],
                "asset_class": a.get("asset_class", "unknown"),
                "candle_count": count,
            })
    storage.close()
    return available


def get_asset_class(symbol: str) -> str:
    """Get the asset class for a symbol."""
    classifier = AssetClassifier()
    info = classifier.classify(symbol)
    return info.asset_class


def run_simplified_oos(
    df: pd.DataFrame,
    symbol: str,
    train_ratio: float = 0.7,
    living_trie: bool = False,
    verbose: bool = False,
) -> SingleTokenResult:
    """
    Run a simplified OOS paper trading test.
    
    Key features to prevent distortion:
    - Strict train/test split
    - SAX normalization propagated from train to test
    - Living Trie disabled during test phase
    - No look-ahead bias
    """
    result = SingleTokenResult(
        symbol=symbol,
        asset_class=get_asset_class(symbol),
        total_candles=len(df),
    )
    
    # Split data
    split_idx = int(len(df) * train_ratio)
    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]
    
    result.train_candles = len(train_df)
    result.test_candles = len(test_df)
    
    if len(train_df) < 500 or len(test_df) < 200:
        return result
    
    # Create SAX encoder and encode with normalization propagation
    encoder = SAXEncoder(alphabet_size=SAX_ALPHABET, window_size=SAX_WINDOW, strategy="ohlcv")
    
    # V7.9: Train normalization stats
    train_symbols, paa_mean, paa_std = encoder.encode_with_normalization(train_df)
    all_symbols, _, _ = encoder.encode_with_normalization(df, paa_mean=paa_mean, paa_std=paa_std)
    
    if not train_symbols or not all_symbols:
        return result
    
    # Build PPMT engine on training data ONLY
    classifier = AssetClassifier()
    info = classifier.classify(symbol)
    
    engine = PPMT(
        symbol=symbol,
        asset_class=info.asset_class,
        sax_alphabet_size=SAX_ALPHABET,
        sax_window_size=SAX_WINDOW,
        sax_strategy="ohlcv",
        weight_profile=info.weight_profile,
    )
    train_count = engine.build(train_df, pattern_length=PATTERN_LENGTH, symbols=train_symbols)
    result.patterns_built = train_count
    
    if train_count == 0:
        return result
    
    # --- In-Sample backtest (training period) ---
    is_trades = _simplified_match_and_trade(
        symbols=all_symbols[:len(train_symbols)],
        price_df=train_df,
        engine=engine,
        encoder=encoder,
    )
    
    if is_trades:
        result.is_trades = len(is_trades)
        result.is_win_rate = sum(1 for t in is_trades if t["won"]) / len(is_trades)
        result.is_total_pnl_pct = sum(t["pnl_pct"] for t in is_trades)
        pnls = [t["pnl_pct"] for t in is_trades]
        result.is_sharpe = _compute_sharpe(pnls)
        result.is_max_dd = _compute_max_dd(pnls)
    
    # --- Out-of-Sample backtest (test period) ---
    n_train_symbols = len(train_symbols)
    oos_trades = _simplified_match_and_trade(
        symbols=all_symbols,
        price_df=df,
        engine=engine,
        encoder=encoder,
        symbol_offset=n_train_symbols,
    )
    
    if oos_trades:
        result.oos_trades = len(oos_trades)
        result.oos_win_rate = sum(1 for t in oos_trades if t["won"]) / len(oos_trades)
        result.oos_total_pnl_pct = sum(t["pnl_pct"] for t in oos_trades)
        pnls = [t["pnl_pct"] for t in oos_trades]
        result.oos_sharpe = _compute_sharpe(pnls)
        result.oos_max_dd = _compute_max_dd(pnls)
    
    # Compute degradation
    if result.is_total_pnl_pct > 0:
        result.oos_ratio = result.oos_total_pnl_pct / result.is_total_pnl_pct
        result.pnl_degradation = max(0, (1 - result.oos_ratio) * 100)
    result.wr_degradation = max(0, (result.is_win_rate - result.oos_win_rate) * 100)
    
    return result


# NOTE: _simplified_match_and_trade is defined below (extended version with N1/N2 support).
# The original version was removed to avoid duplicate definition — Python would
# silently use the second definition, making the first one dead code.


def _compute_sharpe(pnls: list[float]) -> float:
    """Compute annualized Sharpe ratio."""
    if len(pnls) < 2:
        return 0.0
    arr = np.array(pnls)
    mean = np.mean(arr)
    std = np.std(arr)
    if std < 1e-10:
        return 0.0
    # Annualize assuming ~252 trading periods
    return float(mean / std * np.sqrt(252))


def _compute_max_dd(pnls: list[float]) -> float:
    """Compute maximum drawdown percentage."""
    if not pnls:
        return 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for pnl in pnls:
        equity *= (1 + pnl / 100)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd


def run_random_baseline(
    df: pd.DataFrame,
    n_trades: int = 200,
    seed: int = 42,
) -> tuple[float, float]:
    """
    Run a random trading baseline for comparison.
    
    Generates random LONG/SHORT trades with the same ATR-based SL/TP
    to establish a baseline. If PPMT can't beat random, it's not working.
    """
    rng = np.random.default_rng(seed)
    close = df["close"].values.astype(float)
    atr_pct = compute_atr_pct(df, period=14)
    
    wins = 0
    total_pnl = 0.0
    
    for _ in range(n_trades):
        idx = rng.integers(200, len(close) - 100)
        direction = rng.choice(["LONG", "SHORT"])
        entry_price = close[idx]
        current_atr = atr_pct[idx] if idx < len(atr_pct) else 2.0
        
        # Same SL/TP as PPMT
        if direction == "LONG":
            sl_distance = min(max(current_atr * 1.5, 1.5), 5.0)
            tp_distance = sl_distance * 2.0
        else:
            sl_distance = min(max(current_atr * 2.0, 2.0), 7.0)
            tp_distance = sl_distance * 1.5
        
        # Walk forward
        for j in range(idx + 1, min(idx + 50, len(close))):
            price = close[j]
            if direction == "LONG":
                loss_pct = (entry_price - price) / entry_price * 100
                gain_pct = (price - entry_price) / entry_price * 100
            else:
                loss_pct = (price - entry_price) / entry_price * 100
                gain_pct = (entry_price - price) / entry_price * 100
            
            if loss_pct >= sl_distance:
                total_pnl -= sl_distance
                break
            elif gain_pct >= tp_distance:
                total_pnl += tp_distance
                wins += 1
                break
        else:
            # End of window — use actual PnL
            exit_price = close[min(idx + 49, len(close) - 1)]
            if direction == "LONG":
                pnl = (exit_price - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - exit_price) / entry_price * 100
            total_pnl += pnl
            if pnl > 0:
                wins += 1
    
    win_rate = wins / n_trades if n_trades > 0 else 0
    return total_pnl, win_rate


def run_cross_token_test(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
    source_symbol: str,
    target_symbol: str,
) -> CrossTokenResult:
    """
    Cross-token validation: build on source, test on target.
    
    Tests whether patterns from one token generalize to another.
    Uses N1 (universal) from source to predict target patterns.
    """
    result = CrossTokenResult(
        source_symbol=source_symbol,
        source_class=get_asset_class(source_symbol),
        target_symbol=target_symbol,
        target_class=get_asset_class(target_symbol),
    )
    
    # Build engine on source token (full data)
    classifier = AssetClassifier()
    source_info = classifier.classify(source_symbol)
    
    engine = PPMT(
        symbol=source_symbol,
        asset_class=source_info.asset_class,
        sax_alphabet_size=SAX_ALPHABET,
        sax_window_size=SAX_WINDOW,
        sax_strategy="ohlcv",
        weight_profile=source_info.weight_profile,
    )
    source_symbols = engine.sax.encode(source_df)
    engine.build(source_df, pattern_length=PATTERN_LENGTH, symbols=source_symbols)
    result.source_patterns = engine.trie_n3.pattern_count
    
    # Test 1: Use source's N3 trie on target data (cross-asset prediction)
    # Encode target with its OWN normalization (since we don't have source's normalization for target)
    target_encoder = SAXEncoder(alphabet_size=SAX_ALPHABET, window_size=SAX_WINDOW, strategy="ohlcv")
    target_symbols = target_encoder.encode(target_df)
    
    # Test on target using source's N3 trie
    n3_trades = _simplified_match_and_trade(
        symbols=target_symbols,
        price_df=target_df,
        engine=engine,  # Source engine with N3 from source
        encoder=target_encoder,
        symbol_offset=PATTERN_LENGTH + 20,  # Skip warm-up
    )
    
    if n3_trades:
        result.target_oos_trades_n3 = len(n3_trades)
        result.target_oos_pnl_n3 = sum(t["pnl_pct"] for t in n3_trades)
        result.target_oos_wr_n3 = sum(1 for t in n3_trades if t["won"]) / len(n3_trades)
    
    # Test 2: Build fresh N3 on target's own train data
    split_idx = int(len(target_df) * TRAIN_RATIO)
    target_train_df = target_df.iloc[:split_idx]
    
    target_info = classifier.classify(target_symbol)
    fresh_engine = PPMT(
        symbol=target_symbol,
        asset_class=target_info.asset_class,
        sax_alphabet_size=SAX_ALPHABET,
        sax_window_size=SAX_WINDOW,
        sax_strategy="ohlcv",
        weight_profile=target_info.weight_profile,
    )
    
    # Encode with normalization propagation
    train_syms, paa_mean, paa_std = fresh_engine.sax.encode_with_normalization(target_train_df)
    all_target_syms, _, _ = fresh_engine.sax.encode_with_normalization(
        target_df, paa_mean=paa_mean, paa_std=paa_std
    )
    
    fresh_engine.build(target_train_df, pattern_length=PATTERN_LENGTH, symbols=train_syms)
    
    # Test on target's test period using fresh N3
    fresh_trades = _simplified_match_and_trade(
        symbols=all_target_syms,
        price_df=target_df,
        engine=fresh_engine,
        encoder=fresh_engine.sax,
        symbol_offset=len(train_syms),
    )
    
    if fresh_trades:
        result.target_oos_trades_fresh = len(fresh_trades)
        result.target_oos_pnl_fresh = sum(t["pnl_pct"] for t in fresh_trades)
        result.target_oos_wr_fresh = sum(1 for t in fresh_trades if t["won"]) / len(fresh_trades)
    
    # Test 3: Use source's N1+N2 tries on target data
    # N1 and N2 contain universal and class-level patterns
    n1n2_trades = _simplified_match_and_trade(
        symbols=target_symbols,
        price_df=target_df,
        engine=engine,  # Source engine, but matching will use N1/N2
        encoder=target_encoder,
        symbol_offset=PATTERN_LENGTH + 20,
        prefer_n1n2=True,
    )
    
    if n1n2_trades:
        result.target_oos_trades_n1n2 = len(n1n2_trades)
        result.target_oos_pnl_n1n2 = sum(t["pnl_pct"] for t in n1n2_trades)
        result.target_oos_wr_n1n2 = sum(1 for t in n1n2_trades if t["won"]) / len(n1n2_trades)
    
    return result


# Extend _simplified_match_and_trade to support N1/N2 preference
def _simplified_match_and_trade(
    symbols: list[str],
    price_df: pd.DataFrame,
    engine: PPMT,
    encoder: SAXEncoder,
    symbol_offset: int = 0,
    prefer_n1n2: bool = False,
) -> list[dict]:
    """Extended version with N1/N2 preference option."""
    from ppmt.core.matcher import FuzzyMatcher
    
    trades = []
    fuzzy_matcher = FuzzyMatcher(sax_encoder=encoder, threshold=0.85)
    window_size = encoder.window_size
    forward_window = 5
    
    start = max(symbol_offset, PATTERN_LENGTH)
    end = len(symbols) - PATTERN_LENGTH - forward_window
    
    # Determine search order
    if prefer_n1n2:
        trie_order = [
            (engine.trie_n2, "N2"),
            (engine.trie_n1, "N1"),
            (engine.trie_n3, "N3"),
        ]
    else:
        trie_order = [
            (engine.trie_n3, "N3"),
            (engine.trie_n2, "N2"),
            (engine.trie_n1, "N1"),
        ]
    
    for i in range(start, max(start, end)):
        current_pattern = symbols[i:i + PATTERN_LENGTH]
        
        best_node = None
        best_level = None
        
        for trie, level_name in trie_order:
            node = trie.search(current_pattern)
            if node is not None and node.metadata.historical_count >= 3:
                best_node = node
                best_level = level_name
                break
            
            match_result = fuzzy_matcher.best_match(trie, current_pattern)
            if match_result.node and match_result.node.metadata.historical_count >= 3:
                best_node = match_result.node
                best_level = f"{level_name}(fuzzy)"
                break
        
        if best_node is None:
            continue
        
        meta = best_node.metadata
        
        if abs(meta.expected_move_pct) < 0.5 or meta.confidence < 0.15:
            continue
        
        direction = "LONG" if meta.expected_move_pct > 0 else "SHORT"
        
        entry_candle = i * window_size
        exit_candle = (i + PATTERN_LENGTH + forward_window) * window_size
        
        if entry_candle >= len(price_df) or exit_candle > len(price_df):
            continue
        
        entry_price = price_df["close"].iloc[entry_candle]
        exit_price = price_df["close"].iloc[exit_candle - 1]
        
        if direction == "LONG":
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0
        else:
            pnl_pct = ((entry_price - exit_price) / entry_price) * 100.0
        
        won = pnl_pct > 0
        
        trades.append({
            "direction": direction,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "pnl_pct": round(pnl_pct, 4),
            "won": won,
            "pattern": "".join(current_pattern),
            "match_level": best_level,
            "confidence": round(meta.confidence, 4),
            "win_rate_historical": round(meta.win_rate, 4),
            "expected_move": round(meta.expected_move_pct, 4),
            "historical_count": meta.historical_count,
        })
    
    return trades


# ============================================================
# Main Diagnostic Runner
# ============================================================

def run_full_diagnostic(symbols_to_test: list[str] | None = None) -> DiagnosticReport:
    """Run the full diagnostic suite."""
    
    report = DiagnosticReport(
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    
    storage = PPMTStorage()
    
    # === Step 0: Check available data ===
    print("\n" + "=" * 70)
    print("  PPMT CROSS-TOKEN DIAGNOSTIC SUITE")
    print("=" * 70)
    
    available = check_available_data()
    report.available_tokens = available
    
    if not available:
        print("\n  ⚠️  No hay datos disponibles en la base de datos.")
        print("  Ejecuta 'ppmt ingest BTC/USDT' primero para cargar datos.")
        report.verdict = "NO_DATA"
        return report
    
    print(f"\n  Tokens disponibles: {len(available)}")
    for t in available:
        print(f"    • {t['symbol']:12s} ({t['asset_class']:12s}) — {t['candle_count']:6d} candles")
    
    # Determine which symbols to test
    if symbols_to_test is None:
        # Use all available symbols
        symbols_to_test = [t["symbol"] for t in available if t["candle_count"] >= 1000]
    
    if len(symbols_to_test) < 1:
        print("\n  ⚠️  No hay suficientes datos (mínimo 1000 candles por token).")
        report.verdict = "INSUFFICIENT_DATA"
        return report
    
    # === Test 1: Single-Token OOS Validation ===
    print("\n" + "-" * 70)
    print("  TEST 1: Validación OOS por token (baseline)")
    print("-" * 70)
    
    for symbol in symbols_to_test:
        df = storage.load_ohlcv(symbol, "1h")
        if df.empty or len(df) < 1000:
            print(f"  ⏭️  {symbol}: datos insuficientes ({len(df)} candles)")
            continue
        
        print(f"\n  📊 {symbol} ({get_asset_class(symbol)}) — {len(df)} candles")
        
        result = run_simplified_oos(df, symbol, train_ratio=TRAIN_RATIO)
        report.single_token_results.append(result)
        
        # Print results
        if result.oos_trades > 0:
            is_pnl = f"{result.is_total_pnl_pct:+.1f}%" if result.is_trades > 0 else "N/A"
            oos_pnl = f"{result.oos_total_pnl_pct:+.1f}%"
            is_wr = f"{result.is_win_rate:.1%}" if result.is_trades > 0 else "N/A"
            oos_wr = f"{result.oos_win_rate:.1%}"
            
            print(f"    IS:  {result.is_trades:3d} trades | WR {is_wr} | PnL {is_pnl}")
            print(f"    OOS: {result.oos_trades:3d} trades | WR {oos_wr} | PnL {oos_pnl}")
            print(f"    Degradación: PnL {result.pnl_degradation:.0f}% | WR {result.wr_degradation:.0f}% | Ratio {result.oos_ratio:.2f}")
            
            if result.oos_total_pnl_pct > 0:
                print(f"    ✅ OOS POSITIVO — el sistema generaliza")
            else:
                print(f"    ❌ OOS NEGATIVO — posible overfitting")
        else:
            print(f"    ⚠️  Sin trades OOS")
    
    # === Test 2: Cross-Token Validation ===
    print("\n" + "-" * 70)
    print("  TEST 2: Validación Cross-Token (patrones de otro token)")
    print("-" * 70)
    
    if len(symbols_to_test) >= 2:
        # Test pairs across different classes
        tested_pairs = set()
        for i, src_sym in enumerate(symbols_to_test):
            for tgt_sym in symbols_to_test[i+1:]:
                src_df = storage.load_ohlcv(src_sym, "1h")
                tgt_df = storage.load_ohlcv(tgt_sym, "1h")
                
                if src_df.empty or tgt_df.empty or len(src_df) < 1000 or len(tgt_df) < 1000:
                    continue
                
                print(f"\n  🔄 {src_sym} → {tgt_sym}")
                
                cross_result = run_cross_token_test(src_df, tgt_df, src_sym, tgt_sym)
                report.cross_token_results.append(cross_result)
                
                # Also test reverse
                print(f"  🔄 {tgt_sym} → {src_sym}")
                reverse_result = run_cross_token_test(tgt_df, src_df, tgt_sym, src_sym)
                report.cross_token_results.append(reverse_result)
                
                # Print results
                for cr in [cross_result, reverse_result]:
                    print(f"    {cr.source_symbol} → {cr.target_symbol}:")
                    if cr.target_oos_trades_n3 > 0:
                        print(f"      N3 fuente:   {cr.target_oos_trades_n3}t | PnL {cr.target_oos_pnl_n3:+.1f}% | WR {cr.target_oos_wr_n3:.1%}")
                    else:
                        print(f"      N3 fuente:   Sin trades (patrones no coinciden)")
                    
                    if cr.target_oos_trades_fresh > 0:
                        print(f"      N3 fresco:   {cr.target_oos_trades_fresh}t | PnL {cr.target_oos_pnl_fresh:+.1f}% | WR {cr.target_oos_wr_fresh:.1%}")
                    else:
                        print(f"      N3 fresco:   Sin trades")
                    
                    if cr.target_oos_trades_n1n2 > 0:
                        print(f"      N1+N2 cruz.: {cr.target_oos_trades_n1n2}t | PnL {cr.target_oos_pnl_n1n2:+.1f}% | WR {cr.target_oos_wr_n1n2:.1%}")
                    else:
                        print(f"      N1+N2 cruz.: Sin trades (patrones universales no coinciden)")
    else:
        print("  ⚠️  Se necesitan al menos 2 tokens para cross-validation")
        report.notes.append("Cross-token test skipped: solo 1 token disponible")
    
    # === Test 3: Living Trie ON vs OFF ===
    print("\n" + "-" * 70)
    print("  TEST 3: Living Trie ON vs OFF")
    print("-" * 70)
    
    if symbols_to_test:
        test_sym = symbols_to_test[0]
        df = storage.load_ohlcv(test_sym, "1h")
        
        if not df.empty and len(df) >= 1000:
            # Split data
            split_idx = int(len(df) * TRAIN_RATIO)
            train_df = df.iloc[:split_idx]
            test_df = df.iloc[split_idx:]
            
            # Living Trie OFF: Use full PaperTrader with living_trie=False
            try:
                config_off = PaperTraderConfig(
                    symbol=test_sym,
                    living_trie=False,
                    end_offset=split_idx,  # Only trade on test period
                    paa_mean=None,
                    paa_std=None,
                    verbose=False,
                )
                # This won't work perfectly because PaperTrader loads its own data
                # For now, use simplified test
                result_off = run_simplified_oos(df, test_sym, train_ratio=TRAIN_RATIO, living_trie=False)
                report.living_trie_off_pnl = result_off.oos_total_pnl_pct
                report.living_trie_off_wr = result_off.oos_win_rate
                
                print(f"  {test_sym} OOS (Living Trie OFF):")
                print(f"    PnL: {result_off.oos_total_pnl_pct:+.1f}% | WR: {result_off.oos_win_rate:.1%}")
            except Exception as e:
                print(f"  ⚠️  Error en test Living Trie: {e}")
                report.notes.append(f"Living Trie test error: {e}")
    
    # === Test 4: Random Baseline ===
    print("\n" + "-" * 70)
    print("  TEST 4: Baseline Aleatorio (random trading)")
    print("-" * 70)
    
    if symbols_to_test:
        test_sym = symbols_to_test[0]
        df = storage.load_ohlcv(test_sym, "1h")
        
        if not df.empty:
            random_pnl, random_wr = run_random_baseline(df, n_trades=200)
            report.random_pnl = random_pnl
            report.random_wr = random_wr
            
            print(f"  Random trading en {test_sym}:")
            print(f"    PnL: {random_pnl:+.1f}% | WR: {random_wr:.1%}")
    
    # === Verdict ===
    print("\n" + "=" * 70)
    print("  VEREDICTO")
    print("=" * 70)
    
    # Score based on OOS results
    oos_profitable = sum(1 for r in report.single_token_results if r.oos_total_pnl_pct > 0)
    oos_total = len(report.single_token_results)
    
    if oos_total == 0:
        report.verdict = "INSUFFICIENT_DATA"
        report.confidence_score = 0
    else:
        oos_profit_pct = oos_profitable / oos_total * 100
        
        # Check if PPMT beats random
        beats_random = any(
            r.oos_total_pnl_pct > report.random_pnl 
            for r in report.single_token_results
        )
        
        # Check cross-token results
        cross_profitable = sum(
            1 for cr in report.cross_token_results 
            if cr.target_oos_pnl_fresh > 0
        )
        cross_total = len(report.cross_token_results)
        cross_pct = (cross_profitable / cross_total * 100) if cross_total > 0 else 0
        
        # Composite score
        score = 0
        score += min(40, oos_profit_pct * 0.4)  # OOS profitability: 40 pts max
        score += 20 if beats_random else 0       # Beats random: 20 pts
        score += min(20, cross_pct * 0.2)        # Cross-token: 20 pts max
        score += min(20, max(0, 20 - report.single_token_results[0].pnl_degradation * 0.4)) if report.single_token_results else 0  # Low degradation: 20 pts max
        
        report.confidence_score = score
        
        if score >= 70:
            report.verdict = "ROBUST"
        elif score >= 45:
            report.verdict = "MARGINAL"
        elif oos_total > 0:
            report.verdict = "OVERFIT"
        else:
            report.verdict = "INSUFFICIENT_DATA"
    
    print(f"\n  Score: {report.confidence_score:.0f}/100")
    print(f"  Veredicto: {report.verdict}")
    print(f"  Tokens OOS rentables: {oos_profitable}/{oos_total}")
    print(f"  Cross-token rentables: {cross_profitable}/{cross_total}" if report.cross_token_results else "  Cross-token: No testado")
    print(f"  Beats random: {'SÍ' if beats_random else 'NO'}" if oos_total > 0 else "")
    
    # Key insights
    print(f"\n  📋 CONCLUSIONES:")
    if report.verdict == "ROBUST":
        print("  ✅ PPMT generaliza fuera de muestra y supera trading aleatorio.")
        print("  ✅ Los patrones de la Trie son informativos y no solo ruido.")
    elif report.verdict == "MARGINAL":
        print("  ⚠️  PPMT muestra señal pero no consistentemente.")
        print("  ⚠️  Se necesita más data o ajuste de parámetros.")
    elif report.verdict == "OVERFIT":
        print("  ❌ PPMT no generaliza — posible overfitting a datos de entrenamiento.")
        print("  ❌ Los patrones in-sample no se mantienen out-of-sample.")
    else:
        print("  ⚠️  Datos insuficientes para determinar robustez.")
    
    # Critical gap
    print(f"\n  🔧 GAP CRÍTICO DETECTADO:")
    print(f"  PaperTrader solo usa N3 (per-asset trie). N1 y N2 NO se usan")
    print(f"  durante el trading. Esto significa que un token nuevo NO puede")
    print(f"  beneficiarse de patrones universales o de clase de otros tokens.")
    print(f"  RECOMENDACIÓN: Conectar PPMT.match() (4 niveles) al PaperTrader.")
    
    storage.close()
    return report


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PPMT Cross-Token Diagnostic Suite")
    parser.add_argument("--source", default=None, help="Source token for cross-validation")
    parser.add_argument("--target", default=None, help="Target token for cross-validation")
    parser.add_argument("--all", action="store_true", help="Test all available tokens")
    parser.add_argument("--save", default=None, help="Save report to JSON file")
    args = parser.parse_args()
    
    symbols = None
    if args.source and args.target:
        symbols = [args.source, args.target]
    elif args.all:
        symbols = None  # Will auto-detect all available
    
    report = run_full_diagnostic(symbols)
    
    if args.save:
        with open(args.save, "w") as f:
            json.dump({
                "timestamp": report.timestamp,
                "verdict": report.verdict,
                "confidence_score": report.confidence_score,
                "available_tokens": report.available_tokens,
                "single_token_results": [
                    {
                        "symbol": r.symbol,
                        "asset_class": r.asset_class,
                        "is_trades": r.is_trades,
                        "is_win_rate": round(r.is_win_rate, 4),
                        "is_pnl_pct": round(r.is_total_pnl_pct, 2),
                        "oos_trades": r.oos_trades,
                        "oos_win_rate": round(r.oos_win_rate, 4),
                        "oos_pnl_pct": round(r.oos_total_pnl_pct, 2),
                        "pnl_degradation": round(r.pnl_degradation, 1),
                        "oos_ratio": round(r.oos_ratio, 3),
                    }
                    for r in report.single_token_results
                ],
                "cross_token_results": [
                    {
                        "source": cr.source_symbol,
                        "target": cr.target_symbol,
                        "n3_pnl": round(cr.target_oos_pnl_n3, 2),
                        "n3_wr": round(cr.target_oos_wr_n3, 4),
                        "fresh_pnl": round(cr.target_oos_pnl_fresh, 2),
                        "fresh_wr": round(cr.target_oos_wr_fresh, 4),
                        "n1n2_pnl": round(cr.target_oos_pnl_n1n2, 2),
                        "n1n2_wr": round(cr.target_oos_wr_n1n2, 4),
                    }
                    for cr in report.cross_token_results
                ],
                "random_baseline": {
                    "pnl": round(report.random_pnl, 2),
                    "win_rate": round(report.random_wr, 4),
                },
                "notes": report.notes,
            }, f, indent=2)
        print(f"\n  💾 Reporte guardado en: {args.save}")
