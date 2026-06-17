#!/usr/bin/env python3
"""
v0.38.3: Script de diagnóstico — ¿por qué Trades=0 con Signals>0?

Simula el flujo completo para un token:
  1. Carga trie desde storage
  2. Genera un pattern aleatorio (o usa el último conocido)
  3. Llama a PredictionEngine.predict()
  4. Construye una Signal
  5. Llama a RiskManager.can_open()
  6. Muestra exactamente qué check falla

Uso:
  python scripts/diagnose_signal_flow.py BTC/USDT 1h
  python scripts/diagnose_signal_flow.py PHA/USDT 1h
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ppmt.data.storage import PPMTStorage
from ppmt.core.sax import SAXEncoder
from ppmt.engine.prediction import PredictionEngine
from ppmt.engine.signal import Signal, SignalType
from ppmt.risk.manager import RiskManager, RiskConfig
from ppmt.core.metadata import BlockLifecycleMetadata


def main():
    if len(sys.argv) < 3:
        print("Uso: python diagnose_signal_flow.py SYMBOL TIMEFRAME")
        print("Ej:  python diagnose_signal_flow.py BTC/USDT 1h")
        sys.exit(1)

    symbol = sys.argv[1]
    timeframe = sys.argv[2]

    print(f"\n{'='*70}")
    print(f"  DIAGNÓSTICO DE FLUJO DE SIGNAL — {symbol} {timeframe}")
    print(f"{'='*70}\n")

    storage = PPMTStorage()

    # 1. Cargar trie
    print("[1/6] Cargando trie desde storage...")
    all_tries = storage.load_all_tries(symbol)
    trie = all_tries.get("n3")
    if trie is None:
        print(f"  ❌ No hay trie N3 para {symbol}. Corre 'ppmt ingest -s {symbol}' primero.")
        storage.close()
        sys.exit(1)
    print(f"  ✅ Trie N3 cargado: {trie.pattern_count} patrones")
    print(f"     Total nodes: {getattr(trie, 'total_nodes', 'N/A')}")

    # 2. Cargar datos históricos
    print(f"\n[2/6] Cargando datos históricos...")
    df = storage.load_ohlcv(symbol, timeframe)
    if df is None or df.empty:
        print(f"  ❌ No hay datos para {symbol} {timeframe}")
        storage.close()
        sys.exit(1)
    print(f"  ✅ {len(df)} candles cargadas (rango: {df.index[0]} → {df.index[-1]})")

    # 3. Encode últimos N candles con SAX
    print(f"\n[3/6] Generando SAX pattern reciente...")
    # Buscar TokenProfile guardado
    saved_profile = storage.load_token_profile(symbol, timeframe)
    if saved_profile:
        from ppmt.core.profiles import TokenProfile
        profile = TokenProfile.from_dict(saved_profile)
        alpha = profile.sax_alphabet_size
        window = profile.sax_window_size
        print(f"  TokenProfile: alpha={alpha}, window={window}")
    else:
        alpha, window = 8, 10
        print(f"  No hay TokenProfile. Usando defaults: alpha={alpha}, window={window}")

    encoder = SAXEncoder(alphabet_size=alpha, window_size=window)
    symbols = encoder.encode(df.tail(window * 20))  # últimos 200 candles
    if not symbols:
        print(f"  ❌ SAX no produjo símbolos. Necesitas más datos.")
        storage.close()
        sys.exit(1)

    pattern_length = 5
    current_symbols = symbols[-pattern_length:]
    current_price = float(df["close"].iloc[-1])
    print(f"  ✅ Pattern actual: {''.join(current_symbols)}")
    print(f"  ✅ Precio actual: ${current_price:,.4f}")

    # 4. Prediction
    print(f"\n[4/6] Llamando PredictionEngine.predict()...")
    pred_engine = PredictionEngine(trie, prediction_depth=pattern_length)
    tf_hours = {"1m": 1/60, "5m": 5/60, "15m": 15/60, "1h": 1, "4h": 4, "1d": 24}.get(timeframe, 1)
    prediction = pred_engine.predict(
        current_symbols=current_symbols,
        entry_price=current_price,
        timeframe_hours=tf_hours,
        symbol=symbol,
    )
    print(f"  Direction: {prediction.direction}")
    print(f"  Confidence: {prediction.confidence:.4f}")
    print(f"  Overall probability: {prediction.overall_probability:.4f}")
    print(f"  Expected move: {prediction.expected_total_move_pct:.4f}%")

    # 5. Construir signal
    print(f"\n[5/6] Construyendo Signal...")
    if prediction.direction == "FLAT":
        print(f"  ❌ Prediction es FLAT — no hay signal.")
        storage.close()
        sys.exit(0)

    expected_move_abs = abs(prediction.expected_total_move_pct)
    sl_distance_pct = max(min(expected_move_abs * 1.2, 3.0), 0.5)
    tp_distance_pct = expected_move_abs * 2.0
    if tp_distance_pct < sl_distance_pct * 1.5:
        tp_distance_pct = sl_distance_pct * 1.5

    if prediction.direction == "LONG":
        sl_price = current_price * (1 - sl_distance_pct / 100)
        tp_price = current_price * (1 + tp_distance_pct / 100)
    else:
        sl_price = current_price * (1 + sl_distance_pct / 100)
        tp_price = current_price * (1 - tp_distance_pct / 100)

    risk_reward = tp_distance_pct / sl_distance_pct if sl_distance_pct > 0 else 0

    matched_node = trie.search(current_symbols)
    actual_historical_count = 10
    if matched_node and matched_node.metadata.historical_count > 0:
        actual_historical_count = matched_node.metadata.historical_count
    print(f"  Matched node historical_count: {actual_historical_count}")

    signal_type = SignalType.ENTRY_LONG if prediction.direction == "LONG" else SignalType.ENTRY_SHORT
    signal = Signal(
        signal_type=signal_type,
        confidence=prediction.confidence,
        symbol=symbol,
        entry_price=current_price,
        sl_price=sl_price,
        tp_price=tp_price,
        expected_move_pct=prediction.expected_total_move_pct,
        risk_reward_ratio=risk_reward,
        win_rate=prediction.overall_probability,
        historical_count=actual_historical_count,
        matched_pattern=current_symbols,
        trie_level="n3",
    )
    signal.quality_score = signal.compute_quality_score()
    signal.sizing_multiplier = signal.compute_sizing_multiplier()

    mock_meta = BlockLifecycleMetadata(
        win_rate=signal.win_rate,
        expected_move_pct=signal.expected_move_pct,
        max_drawdown_pct=-sl_distance_pct,
        historical_count=actual_historical_count,
    )
    signal.probability_of_success = mock_meta.probability_of_success
    signal.expected_profit_ahead = mock_meta.expected_profit_ahead
    signal.metadata_sizing_signal = mock_meta.sizing_signal

    print(f"  Signal: {signal.signal_type.value} @ ${current_price:,.4f}")
    print(f"  SL: ${sl_price:,.4f} (-{sl_distance_pct:.2f}%)")
    print(f"  TP: ${tp_price:,.4f} (+{tp_distance_pct:.2f}%)")
    print(f"  R:R: {risk_reward:.2f}")
    print(f"  Quality score: {signal.quality_score:.4f}")
    print(f"  Sizing multiplier: {signal.sizing_multiplier:.4f}")
    print(f"  Metadata sizing signal: {signal.metadata_sizing_signal:.4f}")

    # 6. Risk check
    print(f"\n[6/6] Llamando RiskManager.can_open()...")
    print(f"  RiskConfig defaults (v0.38.1+):")
    print(f"    min_quality_score:  {RiskConfig().min_quality_score}")
    print(f"    min_risk_reward:    {RiskConfig().min_risk_reward}")
    print(f"    min_confidence:     {RiskConfig().min_confidence}")
    print(f"    max_open_positions: {RiskConfig().max_open_positions}")
    print(f"    max_correlated:     {RiskConfig().max_correlated_positions}")
    print(f"    max_daily_loss_pct: {RiskConfig().max_daily_loss_pct}")
    print(f"    max_drawdown_pct:   {RiskConfig().max_drawdown_pct}")

    risk_mgr = RiskManager(capital=10000.0, config=RiskConfig())
    from ppmt.data.classifier import AssetClassifier
    info = AssetClassifier().classify(symbol)
    print(f"  Asset class: {info.asset_class}")

    can_open, reason = risk_mgr.can_open(signal, info.asset_class)
    print(f"\n  Resultado: can_open={can_open}, reason='{reason}'")

    if can_open:
        size = risk_mgr.calculate_position_size(signal)
        print(f"  ✅ Position size calculado: {size:.6f} units")
        print(f"  ✅ Notional: ${size * current_price:,.2f}")
        print(f"\n  🎉 LA SIGNAL DEBERÍA EJECUTARSE.")
        print(f"  Si en el dashboard ves Trades=0, el problema NO es can_open().")
        print(f"  Posibles causas:")
        print(f"    - La signal se genera pero el loop principal no la procesa")
        print(f"    - El risk_mgr ya tiene posición en este símbolo (sesión previa)")
        print(f"    - El patrón actual no coincide con ningún nodo del trie")
    else:
        print(f"\n  ❌ LA SIGNAL ES RECHAZADA.")
        print(f"  Razón: {reason}")
        print(f"\n  Diagnóstico:")
        if "Quality too low" in reason:
            print(f"    → quality_score={signal.quality_score:.4f} < min=0.03")
            print(f"    → confidence={signal.confidence:.4f}, win_rate={signal.win_rate:.4f}")
            print(f"    → Para subir quality: necesitas más confidence o win_rate")
            print(f"    → Esto mejora con tries más grandes (más historical_count)")
        elif "Confidence too low" in reason:
            print(f"    → confidence={signal.confidence:.4f} < min=0.08")
            print(f"    → confidence viene de PredictionEngine basado en trie stats")
            print(f"    → Tries pequeños dan confidence baja (Bayesian shrinkage)")
        elif "R:R too low" in reason:
            print(f"    → risk_reward={signal.risk_reward_ratio:.4f} < min=0.5")
            print(f"    → expected_move={prediction.expected_total_move_pct:.4f}%")
            print(f"    → Move muy bajo → SL/TP apretados → R:R bajo")
        elif "Already in position" in reason:
            print(f"    → risk_mgr ya tiene posición en {symbol}")
            print(f"    → Borra ~/.ppmt/money_mgr_*.json y reinicia")
        elif "Max" in reason:
            print(f"    → Límite de posiciones alcanzado")
        else:
            print(f"    → Otra razón: {reason}")

    # Resumen
    print(f"\n{'='*70}")
    print(f"  RESUMEN")
    print(f"{'='*70}")
    print(f"  Symbol: {symbol} {timeframe}")
    print(f"  Trie patterns: {trie.pattern_count}")
    print(f"  Pattern actual: {''.join(current_symbols)}")
    print(f"  Prediction: dir={prediction.direction}, conf={prediction.confidence:.3f}, prob={prediction.overall_probability:.3f}, move={prediction.expected_total_move_pct:.3f}%")
    print(f"  Signal: quality={signal.quality_score:.3f}, RR={signal.risk_reward_ratio:.3f}")
    print(f"  can_open: {can_open} ({reason})")
    print()

    storage.close()


if __name__ == "__main__":
    main()
