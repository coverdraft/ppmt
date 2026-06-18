#!/usr/bin/env python3
"""
PPMT Live Prediction Bridge

Lightweight prediction for real-time trading. Unlike the full PaperTrader
which steps through ALL historical data, this script:

1. Loads the existing trie from storage (already built)
2. Fetches the latest N candles from the exchange
3. Encodes them with SAX
4. Matches against the trie
5. Returns a trading signal (LONG/SHORT/EXIT/HOLD/NEUTRAL)

This runs in <1 second, making it suitable for real-time use.

Usage:
    python3 predict_live.py --result-file /tmp/ppmt_signal.json \
        --symbol BTC/USDT --timeframe 1h

Output:
    Writes JSON signal to --result-file
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))


def main():
    parser = argparse.ArgumentParser(description='PPMT Live Prediction')
    parser.add_argument('--result-file', required=True, help='Path to write JSON signal')
    parser.add_argument('--symbol', required=True, help='Trading pair (e.g. BTC/USDT)')
    parser.add_argument('--timeframe', default='1h', help='Candle timeframe')
    parser.add_argument('--candles', type=int, default=0,
                        help='OHLCV candles JSON (base64 or file path). 0=fetch from exchange')
    parser.add_argument('--price', type=float, default=0.0,
                        help='Current price (for SL/TP calculation)')
    args = parser.parse_args()

    try:
        from ppmt.data.storage import PPMTStorage
        from ppmt.data.classifier import AssetClassifier
        from ppmt.data.collector import DataCollector
        from ppmt.core.sax import SAXEncoder
        from ppmt.core.regime import RegimeDetector
        from ppmt.core.profiles import TokenProfile
        from ppmt.core.matcher import FuzzyMatcher
        from ppmt.engine.ppmt import PPMT
        from ppmt.engine.prediction import PredictionEngine
        from ppmt.engine.signal import SignalGenerator, SignalType
        import numpy as np

        storage = PPMTStorage()

        # 1. Load OHLCV data (from storage or fetch fresh)
        df = storage.load_ohlcv(args.symbol, args.timeframe)
        if df is None or len(df) < 200:
            # Fetch fresh data if not in storage
            collector = DataCollector(exchange='bybit', storage=storage)
            df = collector.fetch_ohlcv(args.symbol, args.timeframe, days=60)
            if df is None or len(df) < 200:
                raise ValueError(f"Insufficient data for {args.symbol} @ {args.timeframe}: {len(df) if df is not None else 0} candles")

        # 2. Classify asset
        classifier = AssetClassifier()
        asset_info = classifier.classify(args.symbol)
        asset_class = asset_info.asset_class
        profile = TokenProfile.from_timeframe(args.symbol, asset_class, args.timeframe)

        # 3. Get current price
        current_price = args.price if args.price > 0 else float(df["close"].iloc[-1])

        # 4. Load existing tries from storage
        tries_loaded = {}
        for level in ['n1', 'n2', 'n3', 'n4']:
            t = storage.load_trie(args.symbol, level)
            if t is not None:
                tries_loaded[level] = t

        # If no tries exist, we need to build them
        if not tries_loaded:
            # Build from data (first time only)
            alpha = profile.sax_alphabet_size
            window = profile.sax_window_size
            ppmt_engine = PPMT(symbol=args.symbol, asset_class=asset_class, sax_alphabet_size=alpha, sax_window_size=window)
            ppmt_engine.build(df)
            for level, t in [('n1', ppmt_engine.trie_n1), ('n2', ppmt_engine.trie_n2), ('n3', ppmt_engine.trie_n3), ('n4', ppmt_engine.trie_n4)]:
                storage.save_trie(args.symbol, level, t)
                tries_loaded[level] = t

        # Use N3 as primary trie (most patterns)
        primary_trie = tries_loaded.get('n3', tries_loaded.get('n1'))
        if primary_trie is None:
            raise ValueError(f"No trie available for {args.symbol}")

        # FIX-14 (v0.40.10): also load N4 as regime_trie for regime-aware lookup
        regime_trie = tries_loaded.get('n4')

        # 5. Encode recent data with SAX
        alpha = profile.sax_alphabet_size
        window = profile.sax_window_size
        encoder = SAXEncoder(alphabet_size=alpha, window_size=window)

        # Encode the full dataset
        sax_symbols = encoder.encode(df)
        if not sax_symbols or len(sax_symbols) < 5:
            raise ValueError(f"SAX encoding produced {len(sax_symbols) if sax_symbols else 0} symbols")

        # Get the last N symbols for pattern matching
        pattern_length = 5
        recent_symbols = sax_symbols[-pattern_length:]

        # 6. Detect current regime
        regime_detector = RegimeDetector(lookback=50)
        close_prices = df["close"].values.astype(float)
        regime = regime_detector.detect(close_prices)

        # 7. Get prediction from trie
        # FIX-14 (v0.40.10): pass regime_trie (N4) + current_regime so the
        # engine routes the lookup through the matching regime sub-trie.
        regime_name_for_pred = regime.name if hasattr(regime, 'name') else str(regime)
        pred_engine = PredictionEngine(
            primary_trie,
            prediction_depth=5,
            regime_trie=regime_trie,
        )
        prediction = pred_engine.predict(
            current_symbols=recent_symbols,
            entry_price=current_price,
            timeframe_hours=_tf_to_hours(args.timeframe),
            symbol=args.symbol,
            current_regime=regime_name_for_pred,
        )

        # 8. Generate signal (regime-adaptive thresholds)
        regime_name = regime_name_for_pred
        regime_conf = getattr(regime, 'confidence', 0.5)

        # Regime-adaptive confidence: trending markets → more aggressive entries
        if regime_name == 'trending':
            min_conf = 0.08   # Very aggressive — patterns are reliable in trends
            min_rr = 0.8
        elif regime_name == 'volatile':
            min_conf = 0.12   # Moderate — high volatility needs some confidence
            min_rr = 1.2
        else:  # ranging
            min_conf = 0.20   # Conservative — ranging markets need strong signals
            min_rr = 1.5

        # Scale by regime confidence (low regime confidence → higher signal threshold)
        if regime_conf < 0.4:
            min_conf *= 1.5  # Unsure about regime → be more careful

        signal_gen = SignalGenerator(
            min_confidence=min_conf,
            min_risk_reward=min_rr,
        )

        # Try to match the current pattern in the trie
        from ppmt.core.matcher import FuzzyMatcher
        fuzzy_matcher = FuzzyMatcher(sax_encoder=encoder, threshold=0.8)
        match_result = fuzzy_matcher.best_match(primary_trie, recent_symbols)

        signal = None
        if match_result.matched and match_result.node is not None:
            confidence = match_result.node.metadata.confidence if match_result.node.metadata else 0.0
            signal = signal_gen.generate_entry_signal(
                match_result=match_result,
                symbol=args.symbol,
                current_price=current_price,
                confidence=confidence,
                trie_level='n3',
            )

        # 9. Build output
        output = {
            'symbol': args.symbol,
            'timeframe': args.timeframe,
            'timestamp': time.time(),
            'current_price': current_price,
            'asset_class': asset_class,
            'regime': {
                'name': regime_name,
                'confidence': regime_conf,
            },
            'signal_thresholds': {
                'min_confidence': min_conf,
                'min_risk_reward': min_rr,
                'regime_adapted': True,
            },
            'current_pattern': recent_symbols,
            'prediction': {
                'direction': prediction.direction,
                'confidence': round(prediction.confidence, 4),
                'expected_move_pct': round(prediction.expected_total_move_pct, 4),
                'probability': round(prediction.overall_probability, 4),
                'pattern_break_prob': round(prediction.pattern_break_probability, 4),
                'estimated_candles': prediction.total_estimated_candles,
                'entry_price': prediction.entry_price,
                'target_price': prediction.predicted_target,
                'sl_price': prediction.predicted_sl,
            },
            'signal': signal.to_dict() if signal else None,
            'trie_stats': {
                'patterns': primary_trie.pattern_count,
                'levels_loaded': list(tries_loaded.keys()),
            },
            'data_candles': len(df),
            'sax_symbols': len(sax_symbols),
        }

        # Write result to file
        result_dir = os.path.dirname(args.result_file)
        if result_dir:
            os.makedirs(result_dir, exist_ok=True)
        with open(args.result_file, 'w') as f:
            json.dump(output, f)

        # Print one-line summary to stdout
        sig_str = f"{signal.signal_type.value} conf={signal.confidence:.0%}" if signal else "NO_SIGNAL"
        print(f"PPMT {args.symbol} @ {args.timeframe}: {prediction.direction} move={prediction.expected_total_move_pct:+.2f}% conf={prediction.confidence:.0%} | {sig_str}")

    except Exception as e:
        error_output = {'error': str(e), 'errorType': type(e).__name__}
        try:
            result_dir = os.path.dirname(args.result_file)
            if result_dir:
                os.makedirs(result_dir, exist_ok=True)
            with open(args.result_file, 'w') as f:
                json.dump(error_output, f)
        except Exception:
            pass
        print(f"ERROR: {e}")


def _tf_to_hours(tf: str) -> float:
    """Convert timeframe string to hours."""
    mapping = {
        '1m': 1/60, '3m': 3/60, '5m': 5/60, '15m': 15/60, '30m': 30/60,
        '1h': 1.0, '2h': 2.0, '4h': 4.0, '6h': 6.0, '12h': 12.0,
        '1d': 24.0, '1w': 168.0,
    }
    return mapping.get(tf, 1.0)


if __name__ == '__main__':
    main()
