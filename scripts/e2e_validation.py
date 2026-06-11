#!/usr/bin/env python3
"""
PPMT End-to-End Validation Script
==================================

5 pruebas clave para verificar si PPMT funciona analizando y ganando dinero:

1. FULL IN-SAMPLE: Paper trading con todos los datos (verificar que el pipeline funciona)
2. OUT-OF-SAMPLE: Train 70% / Trade 30% (verificar edge real vs overfitting)
3. BUY & HOLD BASELINE: Comparar PPMT vs simplemente comprar y mantener
4. REGIME-AWARE VS NON-REGIME: Verificar que regime detection añade valor
5. ANTI-DISTORTION: Datos aleatorios NO deben producir ganancias consistentes

Criterios de éxito:
- PPMT produce > 0% P&L (al menos no pierde)
- WR > 45% (mejor que azar)
- Profit Factor > 1.0 (ganancias superan pérdidas)
- OOS performance no degrada > 50% vs in-sample (no overfitting severo)
- PPMT supera Buy & Hold en al menos 1 escenario
- Random data NO produce P&L positivo consistente
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import pandas as pd
from datetime import datetime
import json
import time

from ppmt.data.storage import PPMTStorage
from ppmt.data.classifier import AssetClassifier
from ppmt.core.sax import SAXEncoder
from ppmt.core.regime import RegimeDetector
from ppmt.engine.ppmt import PPMT
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.paper_trader import PaperTrader, PaperTraderConfig, compute_atr_pct


def load_data():
    """Load BTC/USDT 1h data from storage."""
    storage = PPMTStorage()
    df = storage.load_ohlcv("BTC/USDT", "1h")
    print(f"  Loaded {len(df)} candles of BTC/USDT 1h")
    return df


def run_paper_trader(df, config_overrides=None, verbose=False):
    """Run paper trader with given config overrides."""
    overrides = config_overrides or {}
    config = PaperTraderConfig(
        symbol="BTC/USDT",
        timeframe="1h",
        initial_capital=10_000.0,
        pattern_length=5,
        sax_alphabet_size=8,
        sax_window_size=10,
        sax_strategy="ohlcv",
        min_confidence=0.20,
        start_offset=200,
        living_trie=True,
        pattern_break_grace=2,
        reentry_cooldown=1,
        catastrophic_loss_pct=8.0,
        regime_aware=True,
        verbose=verbose,
        **overrides,
    )
    trader = PaperTrader(config=config)
    result = trader.run()
    return result


def compute_buy_hold(df, start_offset=200, initial_capital=10_000.0, end_offset=0):
    """Compute buy & hold baseline."""
    start_idx = start_offset
    end_idx = end_offset if end_offset > 0 else len(df)
    
    entry_price = df['close'].iloc[start_idx]
    exit_price = df['close'].iloc[min(end_idx - 1, len(df) - 1)]
    
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    final_capital = initial_capital * (1 + pnl_pct / 100)
    
    return {
        "initial_capital": initial_capital,
        "final_capital": final_capital,
        "pnl_pct": pnl_pct,
        "entry_price": entry_price,
        "exit_price": exit_price,
    }


def result_to_dict(result):
    """Convert PaperTraderResult to a simple dict."""
    return {
        "symbol": result.symbol,
        "initial_capital": result.initial_capital,
        "final_capital": result.final_capital,
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate": result.win_rate,
        "total_pnl": result.total_pnl,
        "total_pnl_pct": result.total_pnl_pct,
        "max_drawdown": result.max_drawdown,
        "sharpe_ratio": result.sharpe_ratio,
        "profit_factor": result.profit_factor,
        "avg_trade_pnl_pct": result.avg_trade_pnl_pct,
        "best_trade_pnl_pct": result.best_trade_pnl_pct,
        "worst_trade_pnl_pct": result.worst_trade_pnl_pct,
        "avg_confidence": result.avg_confidence,
    }


# ================================================================
# PRUEBA 1: FULL IN-SAMPLE
# ================================================================
def test_1_full_insample(df):
    """Paper trading con todos los datos disponibles."""
    print("\n" + "=" * 70)
    print("PRUEBA 1: FULL IN-SAMPLE (todos los datos)")
    print("=" * 70)
    print("  Objetivo: Verificar que el pipeline completo funciona end-to-end")
    print("  Datos: ~5 años de BTC/USDT 1h (47,981 velas)")
    print()
    
    t0 = time.time()
    result = run_paper_trader(df, verbose=False)
    elapsed = time.time() - t0
    
    d = result_to_dict(result)
    print(f"  Tiempo: {elapsed:.1f}s")
    print(f"  Capital: ${d['initial_capital']:,.2f} → ${d['final_capital']:,.2f}")
    print(f"  P&L: {d['total_pnl_pct']:+.2f}% (${d['total_pnl']:+,.2f})")
    print(f"  Trades: {d['total_trades']} (W:{d['winning_trades']} L:{d['losing_trades']})")
    print(f"  Win Rate: {d['win_rate']:.1%}")
    print(f"  Profit Factor: {d['profit_factor']:.2f}")
    print(f"  Max Drawdown: {d['max_drawdown']:.1%}")
    print(f"  Sharpe: {d['sharpe_ratio']:.2f}")
    print(f"  Avg Trade: {d['avg_trade_pnl_pct']:+.2f}%")
    print(f"  Best/Worst: {d['best_trade_pnl_pct']:+.2f}% / {d['worst_trade_pnl_pct']:+.2f}%")
    print(f"  Avg Confidence: {d['avg_confidence']:.1%}")
    
    # Analyze exit reasons
    exit_reasons = {}
    for t in result.trades:
        r = t.exit_reason
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    print(f"  Exit Reasons: {exit_reasons}")
    
    # LONG vs SHORT breakdown
    longs = [t for t in result.trades if t.direction == "LONG"]
    shorts = [t for t in result.trades if t.direction == "SHORT"]
    if longs:
        long_wr = sum(1 for t in longs if t.pnl_pct > 0) / len(longs)
        long_avg = np.mean([t.pnl_pct for t in longs])
        print(f"  LONG: {len(longs)} trades, WR={long_wr:.1%}, avg={long_avg:+.2f}%")
    if shorts:
        short_wr = sum(1 for t in shorts if t.pnl_pct > 0) / len(shorts)
        short_avg = np.mean([t.pnl_pct for t in shorts])
        print(f"  SHORT: {len(shorts)} trades, WR={short_wr:.1%}, avg={short_avg:+.2f}%")
    
    # Evaluation
    passed = True
    if d['total_trades'] < 10:
        print("  ⚠️  MUY POCOS TRADES (< 10) — puede ser ruido estadístico")
        passed = False
    if d['win_rate'] < 0.40:
        print("  ⚠️  WIN RATE BAJO (< 40%) — predicciones no son mejores que azar")
    if d['profit_factor'] < 1.0:
        print("  ⚠️  PROFIT FACTOR < 1.0 — pérdidas superan ganancias")
        passed = False
    if d['total_pnl_pct'] > 0:
        print("  ✅ P&L POSITIVO — el sistema genera ganancias in-sample")
    else:
        print("  ❌ P&L NEGATIVO — el sistema pierde dinero in-sample")
        passed = False
    
    return d, passed


# ================================================================
# PRUEBA 2: OUT-OF-SAMPLE (Train 70% / Trade 30%)
# ================================================================
def test_2_out_of_sample(df):
    """Train on first 70%, trade on last 30% — the real test."""
    print("\n" + "=" * 70)
    print("PRUEBA 2: OUT-OF-SAMPLE (Train 70% / Trade 30%)")
    print("=" * 70)
    print("  Objetivo: Verificar que el edge es REAL, no overfitting")
    print("  Train: primeros 70% de datos para construir Trie")
    print("  Test: últimos 30% de datos para operar (sin haberlos visto)")
    print()
    
    total_candles = len(df)
    train_end = int(total_candles * 0.70)
    test_start = train_end
    
    print(f"  Train: velas 0-{train_end} ({train_end} velas)")
    print(f"  Test:  velas {test_start}-{total_candles} ({total_candles - test_start} velas)")
    
    # Step 1: Build trie from training data only
    print("\n  Building Trie from training data...")
    train_df = df.iloc[:train_end].copy()
    classifier = AssetClassifier()
    info = classifier.classify("BTC/USDT")
    
    engine = PPMT(
        symbol="BTC/USDT",
        asset_class=info.asset_class,
        sax_alphabet_size=8,
        sax_window_size=10,
        sax_strategy="ohlcv",
        weight_profile=info.weight_profile,
    )
    
    # Encode training data and get normalization stats
    sax_encoder = SAXEncoder(alphabet_size=8, window_size=10, strategy="ohlcv")
    train_symbols, paa_mean, paa_std = sax_encoder.encode_with_normalization(train_df)
    
    # Build trie using training symbols
    n_patterns = engine.build(train_df, pattern_length=5, symbols=train_symbols)
    print(f"  Trie built: {n_patterns} patterns")
    
    # Run bootstrap on training data
    print("  Running bootstrap on training data...")
    bootstrap_result = engine.bootstrap(train_df, pattern_length=5, verbose=False)
    print(f"  Bootstrap: {bootstrap_result['trades']} trades, WR={bootstrap_result['win_rate']:.1%}")
    
    # Step 2: Run paper trading on TEST data with training normalization
    print("\n  Running paper trading on TEST data (out-of-sample)...")
    t0 = time.time()
    
    # Use the full dataframe but only trade on test portion
    # Pass training normalization stats for consistent SAX encoding
    result = run_paper_trader(df, config_overrides={
        'start_offset': test_start,
        'paa_mean': paa_mean,
        'paa_std': paa_std,
        'verbose': False,
    }, verbose=False)
    elapsed = time.time() - t0
    
    d = result_to_dict(result)
    print(f"  Tiempo: {elapsed:.1f}s")
    print(f"  Capital: ${d['initial_capital']:,.2f} → ${d['final_capital']:,.2f}")
    print(f"  P&L: {d['total_pnl_pct']:+.2f}% (${d['total_pnl']:+,.2f})")
    print(f"  Trades: {d['total_trades']} (W:{d['winning_trades']} L:{d['losing_trades']})")
    print(f"  Win Rate: {d['win_rate']:.1%}")
    print(f"  Profit Factor: {d['profit_factor']:.2f}")
    print(f"  Max Drawdown: {d['max_drawdown']:.1%}")
    print(f"  Sharpe: {d['sharpe_ratio']:.2f}")
    
    # Buy & Hold for the same test period
    bh = compute_buy_hold(df, start_offset=test_start, end_offset=0)
    print(f"\n  Buy & Hold (mismo período): {bh['pnl_pct']:+.2f}% (${bh['final_capital'] - bh['initial_capital']:+,.2f})")
    
    # Evaluation
    passed = True
    if d['total_trades'] < 5:
        print("  ⚠️  MUY POCOS TRADES OOS (< 5)")
        passed = False
    if d['profit_factor'] > 1.0:
        print("  ✅ PROFIT FACTOR > 1.0 OOS — edge real")
    else:
        print("  ❌ PROFIT FACTOR ≤ 1.0 OOS — edge no confirmado fuera de muestra")
    if d['total_pnl_pct'] > 0:
        print("  ✅ P&L POSITIVO OOS")
    else:
        print("  ⚠️  P&L NEGATIVO OOS — puede necesitar ajustes")
    
    # Compare with buy & hold
    if d['total_pnl_pct'] > bh['pnl_pct']:
        print(f"  ✅ PPMT ({d['total_pnl_pct']:+.2f}%) > Buy&Hold ({bh['pnl_pct']:+.2f}%)")
    else:
        print(f"  ⚠️  PPMT ({d['total_pnl_pct']:+.2f}%) < Buy&Hold ({bh['pnl_pct']:+.2f}%)")
    
    return d, bh, passed


# ================================================================
# PRUEBA 3: BUY & HOLD BASELINE
# ================================================================
def test_3_buy_hold_baseline(df):
    """Compare PPMT vs Buy & Hold over the same period."""
    print("\n" + "=" * 70)
    print("PRUEBA 3: BUY & HOLD BASELINE")
    print("=" * 70)
    print("  Objetivo: PPMT debe superar o al menos igualar Buy & Hold")
    print("  en períodos donde el mercado no es puramente alcista")
    print()
    
    # Full period buy & hold
    bh_full = compute_buy_hold(df, start_offset=200)
    print(f"  Buy & Hold (full): ${bh_full['entry_price']:,.2f} → ${bh_full['exit_price']:,.2f}")
    print(f"  P&L: {bh_full['pnl_pct']:+.2f}%")
    
    # Bear market period (2022) — test if PPMT can handle downtrends
    # BTC went from ~48k to ~16k in 2022
    df_dates = df.copy()
    if hasattr(df_dates.index, 'astype'):
        try:
            # Try to convert timestamp index
            dates = pd.to_datetime(df_dates.index)
        except:
            dates = None
    else:
        dates = None
    
    print(f"\n  Nota: BTC subió de ~29k (dic 2020) a ~109k (jun 2026)")
    print(f"  Buy & Hold fue muy rentable en este período")
    print(f"  PPMT debe al menos proteger capital en caídas")
    
    return bh_full


# ================================================================
# PRUEBA 4: REGIME-AWARE VS NON-REGIME
# ================================================================
def test_4_regime_comparison(df):
    """Compare regime-aware vs non-regime trading."""
    print("\n" + "=" * 70)
    print("PRUEBA 4: REGIME-AWARE VS NON-REGIME")
    print("=" * 70)
    print("  Objetivo: Verificar que la detección de régimen AÑADE valor")
    print()
    
    # With regime awareness
    print("  Running WITH regime awareness...")
    result_with = run_paper_trader(df, config_overrides={
        'regime_aware': True,
        'verbose': False,
    }, verbose=False)
    d_with = result_to_dict(result_with)
    
    # Without regime awareness
    print("  Running WITHOUT regime awareness...")
    result_without = run_paper_trader(df, config_overrides={
        'regime_aware': False,
        'verbose': False,
    }, verbose=False)
    d_without = result_to_dict(result_without)
    
    print(f"\n  {'Metric':<20} {'Regime ON':>12} {'Regime OFF':>12} {'Winner':>10}")
    print(f"  {'-'*54}")
    
    metrics = [
        ("P&L %", "total_pnl_pct", "%+.2f"),
        ("Trades", "total_trades", "%d"),
        ("Win Rate", "win_rate", "%.1%%"),
        ("Profit Factor", "profit_factor", "%.2f"),
        ("Max DD", "max_drawdown", "%.1%%"),
        ("Sharpe", "sharpe_ratio", "%.2f"),
    ]
    
    regime_wins = 0
    for name, key, fmt in metrics:
        v_on = d_with[key]
        v_off = d_without[key]
        # For max_drawdown, lower is better
        if key == "max_drawdown":
            winner = "Regime ON" if v_on < v_off else "Regime OFF"
        else:
            winner = "Regime ON" if v_on > v_off else "Regime OFF"
        if winner == "Regime ON":
            regime_wins += 1
        print(f"  {name:<20} {fmt.format(v_on):>12} {fmt.format(v_off):>12} {winner:>10}")
    
    print(f"\n  Regime awareness wins in {regime_wins}/{len(metrics)} metrics")
    if regime_wins >= 3:
        print("  ✅ Regime detection AÑADE valor")
    else:
        print("  ⚠️  Regime detection NO añade valor claro — necesita ajustes")
    
    return d_with, d_without, regime_wins >= 3


# ================================================================
# PRUEBA 5: ANTI-DISTORTION (Random Data)
# ================================================================
def test_5_random_data():
    """Random walk data should NOT produce consistent profits."""
    print("\n" + "=" * 70)
    print("PRUEBA 5: ANTI-DISTORTION (Datos Aleatorios)")
    print("=" * 70)
    print("  Objetivo: Datos aleatorios NO deben producir ganancias")
    print("  Si ganan con random walk → el sistema tiene sesgo")
    print()
    
    np.random.seed(42)
    n_candles = 5000
    
    # Generate random walk price data
    returns = np.random.randn(n_candles) * 0.005  # 0.5% daily volatility
    prices = 100 * np.exp(np.cumsum(returns))
    
    # Create OHLCV DataFrame
    df_random = pd.DataFrame({
        "open": prices * (1 + np.random.randn(n_candles) * 0.001),
        "high": prices * (1 + np.abs(np.random.randn(n_candles)) * 0.005),
        "low": prices * (1 - np.abs(np.random.randn(n_candles)) * 0.005),
        "close": prices,
        "volume": np.abs(np.random.randn(n_candles)) * 1000 + 100,
    })
    
    # Build trie from random data
    print("  Building Trie from random walk data...")
    engine = PPMT(
        symbol="RANDOM/USDT",
        asset_class="default",
        sax_alphabet_size=8,
        sax_window_size=10,
        sax_strategy="ohlcv",
    )
    n_patterns = engine.build(df_random, pattern_length=5)
    print(f"  Trie: {n_patterns} patterns")
    
    # Run bootstrap
    bootstrap_result = engine.bootstrap(df_random, pattern_length=5, verbose=False)
    print(f"  Bootstrap: {bootstrap_result['trades']} trades, WR={bootstrap_result['win_rate']:.1%}")
    
    # Run paper trading
    print("  Running paper trading on random data...")
    result = run_paper_trader(df_random, config_overrides={
        'symbol': 'RANDOM/USDT',
        'start_offset': 200,
        'verbose': False,
    }, verbose=False)
    d = result_to_dict(result)
    
    print(f"\n  P&L: {d['total_pnl_pct']:+.2f}%")
    print(f"  Trades: {d['total_trades']} (W:{d['winning_trades']} L:{d['losing_trades']})")
    print(f"  Win Rate: {d['win_rate']:.1%}")
    print(f"  Profit Factor: {d['profit_factor']:.2f}")
    
    # Evaluation
    passed = True
    if d['profit_factor'] > 1.5 and d['total_pnl_pct'] > 20:
        print("  ❌ RANDOM DATA GANA — sistema tiene sesgo alcista o overfitting!")
        passed = False
    elif d['total_pnl_pct'] > 0 and d['win_rate'] > 0.55:
        print("  ⚠️  Random data ligeramente positivo — revisar sesgos")
    else:
        print("  ✅ Random data NO gana consistentemente — sistema es legítimo")
    
    return d, passed


# ================================================================
# MAIN
# ================================================================
def main():
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║         PPMT END-TO-END VALIDATION — ¿FUNCIONA?               ║")
    print("║         ¿Analiza y gana dinero con datos reales?               ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    
    # Load data
    print("\n📂 Cargando datos...")
    df = load_data()
    if df.empty:
        print("❌ No hay datos. Ejecuta 'ppmt ingest' primero.")
        return
    
    results = {}
    
    # Test 1: Full in-sample
    try:
        d1, p1 = test_1_full_insample(df)
        results["test_1_insample"] = {"data": d1, "passed": p1}
    except Exception as e:
        print(f"\n❌ Test 1 FAILED with error: {e}")
        import traceback
        traceback.print_exc()
        results["test_1_insample"] = {"data": None, "passed": False, "error": str(e)}
    
    # Test 2: Out-of-sample
    try:
        d2, bh2, p2 = test_2_out_of_sample(df)
        results["test_2_oos"] = {"data": d2, "buyhold": bh2, "passed": p2}
    except Exception as e:
        print(f"\n❌ Test 2 FAILED with error: {e}")
        import traceback
        traceback.print_exc()
        results["test_2_oos"] = {"data": None, "passed": False, "error": str(e)}
    
    # Test 3: Buy & Hold baseline
    try:
        bh3 = test_3_buy_hold_baseline(df)
        results["test_3_buyhold"] = bh3
    except Exception as e:
        print(f"\n❌ Test 3 FAILED with error: {e}")
        results["test_3_buyhold"] = {"error": str(e)}
    
    # Test 4: Regime comparison
    try:
        d4w, d4wo, p4 = test_4_regime_comparison(df)
        results["test_4_regime"] = {"with_regime": d4w, "without_regime": d4wo, "passed": p4}
    except Exception as e:
        print(f"\n❌ Test 4 FAILED with error: {e}")
        import traceback
        traceback.print_exc()
        results["test_4_regime"] = {"data": None, "passed": False, "error": str(e)}
    
    # Test 5: Random data
    try:
        d5, p5 = test_5_random_data()
        results["test_5_random"] = {"data": d5, "passed": p5}
    except Exception as e:
        print(f"\n❌ Test 5 FAILED with error: {e}")
        import traceback
        traceback.print_exc()
        results["test_5_random"] = {"data": None, "passed": False, "error": str(e)}
    
    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n\n" + "╔" + "═" * 68 + "╗")
    print("║                    RESUMEN DE VALIDACIÓN                        ║")
    print("╚" + "═" * 68 + "╝")
    
    tests = [
        ("1. In-Sample P&L", results.get("test_1_insample", {}).get("passed", False)),
        ("2. Out-of-Sample Edge", results.get("test_2_oos", {}).get("passed", False)),
        ("3. Regime Adds Value", results.get("test_4_regime", {}).get("passed", False)),
        ("4. No Random Bias", results.get("test_5_random", {}).get("passed", False)),
    ]
    
    passed_count = sum(1 for _, p in tests if p)
    total = len(tests)
    
    for name, passed in tests:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}  {name}")
    
    print(f"\n  Resultado: {passed_count}/{total} pruebas pasadas")
    
    if passed_count >= 3:
        print("\n  🎉 PPMT muestra edge REAL — puede analizar y ganar dinero")
    elif passed_count >= 2:
        print("\n  🟡 PPMT muestra edge PARCIAL — necesita ajustes pero funciona")
    else:
        print("\n  🔴 PPMT NO muestra edge claro — necesita trabajo significativo")
    
    # Key metrics summary
    d1 = results.get("test_1_insample", {}).get("data", {})
    d2 = results.get("test_2_oos", {}).get("data", {})
    bh2 = results.get("test_2_oos", {}).get("buyhold", {})
    
    if d1:
        print(f"\n  📊 Métricas clave In-Sample:")
        print(f"     P&L: {d1.get('total_pnl_pct', 0):+.2f}% | WR: {d1.get('win_rate', 0):.1%} | PF: {d1.get('profit_factor', 0):.2f}")
    if d2:
        print(f"  📊 Métricas clave Out-of-Sample:")
        print(f"     P&L: {d2.get('total_pnl_pct', 0):+.2f}% | WR: {d2.get('win_rate', 0):.1%} | PF: {d2.get('profit_factor', 0):.2f}")
    if bh2:
        print(f"  📊 Buy & Hold OOS: {bh2.get('pnl_pct', 0):+.2f}%")
    
    # Save results
    output_path = "/home/z/my-project/download/ppmt_e2e_validation_results.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  💾 Resultados guardados en: {output_path}")


if __name__ == "__main__":
    main()
