#!/usr/bin/env python3
"""
PPMT Signal Daemon

Runs continuously, generating PPMT predictions for all subscribed tokens
at configurable intervals. Writes results to JSON files that the Next.js
API route reads instantly (no subprocess spawning).

This is the production-ready architecture:
- Python runs on its own process (no subprocess from Node.js)
- Results cached as JSON files (sub-millisecond reads)
- Configurable intervals per timeframe
- Auto-reconnects to data sources

Usage:
    python3 signal_daemon.py                  # Run with defaults
    python3 signal_daemon.py --interval 30    # Check every 30 seconds

Output:
    Writes to /home/z/my-project/ppmt/signals/{SYMBOL}_{TIMEFRAME}.json
"""

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Signal output directory
SIGNAL_DIR = Path(__file__).parent / 'signals'

# Default profiles to monitor
DEFAULT_PROFILES = [
    {'symbol': 'BTC/USDT', 'timeframe': '1h'},
    {'symbol': 'ETH/USDT', 'timeframe': '1h'},
    {'symbol': 'SOL/USDT', 'timeframe': '5m'},
    {'symbol': 'DOGE/USDT', 'timeframe': '5m'},
    {'symbol': 'LINK/USDT', 'timeframe': '1m'},
]

# How often to run predictions (seconds) by timeframe
TIMEFRAME_INTERVALS = {
    '1m': 60,      # Every minute
    '5m': 300,     # Every 5 minutes
    '15m': 900,    # Every 15 minutes
    '1h': 3600,    # Every hour
    '4h': 14400,   # Every 4 hours
}


def run_prediction(symbol: str, timeframe: str) -> dict | None:
    """Run a single prediction and return the result dict."""
    try:
        from ppmt.data.storage import PPMTStorage
        from ppmt.data.classifier import AssetClassifier
        from ppmt.core.sax import SAXEncoder
        from ppmt.core.regime import RegimeDetector
        from ppmt.core.profiles import TokenProfile
        from ppmt.core.matcher import FuzzyMatcher
        from ppmt.engine.prediction import PredictionEngine
        from ppmt.engine.signal import SignalGenerator

        storage = PPMTStorage()
        df = storage.load_ohlcv(symbol, timeframe)
        if df is None or len(df) < 200:
            return {'error': f'Insufficient data: {len(df) if df is not None else 0} candles', 'symbol': symbol, 'timeframe': timeframe}

        classifier = AssetClassifier()
        asset_info = classifier.classify(symbol)
        asset_class = asset_info.asset_class
        profile = TokenProfile.from_timeframe(symbol, asset_class, timeframe)
        current_price = float(df['close'].iloc[-1])

        # Load tries
        tries_loaded = {}
        for level in ['n1', 'n2', 'n3', 'n4']:
            t = storage.load_trie(symbol, level)
            if t is not None:
                tries_loaded[level] = t

        if not tries_loaded:
            storage.close()
            return {'error': f'No tries built for {symbol}', 'symbol': symbol, 'timeframe': timeframe}

        primary_trie = tries_loaded.get('n3', tries_loaded.get('n1'))

        # Encode
        encoder = SAXEncoder(alphabet_size=profile.sax_alphabet_size, window_size=profile.sax_window_size, strategy='ohlcv')
        sax_symbols = encoder.encode(df)
        if not sax_symbols or len(sax_symbols) < 5:
            storage.close()
            return {'error': 'SAX encoding failed', 'symbol': symbol, 'timeframe': timeframe}

        recent_symbols = sax_symbols[-5:]

        # Regime
        regime_detector = RegimeDetector(lookback=50)
        close_prices = df['close'].values.astype(float)
        regime = regime_detector.detect(close_prices)

        # Prediction
        pred_engine = PredictionEngine(primary_trie, prediction_depth=5)
        tf_hours = {'1m': 1/60, '5m': 5/60, '15m': 15/60, '30m': 30/60, '1h': 1.0, '4h': 4.0, '1d': 24.0}.get(timeframe, 1.0)
        prediction = pred_engine.predict(
            current_symbols=recent_symbols,
            entry_price=current_price,
            timeframe_hours=tf_hours,
            symbol=symbol,
        )

        # Signal (regime-adaptive thresholds)
        regime_name = regime.name if hasattr(regime, 'name') else str(regime)
        regime_conf = getattr(regime, 'confidence', 0.5)

        # Regime-adaptive confidence: trending → aggressive, ranging → conservative
        if regime_name == 'trending':
            min_conf, min_rr = 0.08, 0.8
        elif regime_name == 'volatile':
            min_conf, min_rr = 0.12, 1.2
        else:  # ranging
            min_conf, min_rr = 0.20, 1.5

        if regime_conf < 0.4:
            min_conf *= 1.5  # Unsure about regime → be more careful

        signal = None
        if primary_trie:
            fuzzy_matcher = FuzzyMatcher(sax_encoder=encoder, threshold=0.8)
            match_result = fuzzy_matcher.best_match(primary_trie, recent_symbols)
            if match_result.matched and match_result.node is not None:
                confidence = match_result.node.metadata.confidence if match_result.node.metadata else 0.0
                signal_gen = SignalGenerator(min_confidence=min_conf, min_risk_reward=min_rr)
                signal = signal_gen.generate_entry_signal(
                    match_result=match_result,
                    symbol=symbol,
                    current_price=current_price,
                    confidence=confidence,
                    trie_level='n3',
                )

        output = {
            'symbol': symbol,
            'timeframe': timeframe,
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
                'patterns': primary_trie.pattern_count if primary_trie else 0,
                'levels_loaded': list(tries_loaded.keys()),
            },
            'data_candles': len(df),
            'sax_symbols': len(sax_symbols),
        }

        storage.close()
        return output

    except Exception as e:
        return {'error': str(e), 'errorType': type(e).__name__, 'symbol': symbol, 'timeframe': timeframe}


def write_signal(result: dict) -> None:
    """Write signal result to cache file."""
    symbol = result.get('symbol', 'UNKNOWN')
    timeframe = result.get('timeframe', '1h')
    filename = f"{symbol.replace('/', '_')}_{timeframe}.json"
    filepath = SIGNAL_DIR / filename

    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(result, f)


def main():
    parser = argparse.ArgumentParser(description='PPMT Signal Daemon')
    parser.add_argument('--interval', type=int, default=0,
                        help='Override interval in seconds (0 = use per-timeframe defaults)')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    args = parser.parse_args()

    print(f"PPMT Signal Daemon starting...")
    print(f"  Profiles: {len(DEFAULT_PROFILES)}")
    print(f"  Signal dir: {SIGNAL_DIR}")

    # Track last prediction time per profile
    last_run = {}

    while True:
        now = time.time()

        for profile in DEFAULT_PROFILES:
            key = f"{profile['symbol']}@{profile['timeframe']}"
            interval = args.interval or TIMEFRAME_INTERVALS.get(profile['timeframe'], 300)
            last = last_run.get(key, 0)

            if now - last >= interval:
                print(f"  Predicting {key}...", end=' ', flush=True)
                start = time.time()
                result = run_prediction(profile['symbol'], profile['timeframe'])
                elapsed = time.time() - start

                if result and 'error' not in result:
                    pred = result.get('prediction', {})
                    sig = result.get('signal')
                    sig_str = f"{sig['signal_type']} conf={sig['confidence']*100:.0f}%" if sig else "NO_SIGNAL"
                    print(f"{pred.get('direction', '?')} {pred.get('expected_move_pct', 0):+.2f}% | {sig_str} ({elapsed:.1f}s)")
                else:
                    err = result.get('error', 'Unknown') if result else 'No result'
                    print(f"ERROR: {err} ({elapsed:.1f}s)")

                if result:
                    write_signal(result)
                last_run[key] = now

        if args.once:
            break

        # Sleep for 10 seconds before checking again
        time.sleep(10)


if __name__ == '__main__':
    main()
