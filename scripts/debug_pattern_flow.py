#!/usr/bin/env python3
"""
Debug script: verify that the streaming pattern buffer actually accumulates
SAX symbols when process_new_candle is called repeatedly.

Simulates the REST polling flow with fake candles.
"""
import sys
sys.path.insert(0, 'src')

import asyncio
import random
import numpy as np
from datetime import datetime, timedelta

from ppmt.core.sax import SAXEncoder
from ppmt.engine.buffer import StreamingPatternBuffer
from ppmt.engine.realtime import RealtimeTrader, PositionState
from ppmt.data.websocket_feed import Candle


async def main():
    """Simulate 250 fake candles and check if pattern_buffer fills up."""
    print("=" * 70)
    print("DEBUG: Simulating 250 candles to verify pattern_buffer fills up")
    print("=" * 70)

    # Setup SAX encoder like run_live does
    sax_encoder = SAXEncoder(
        alphabet_size=8,
        window_size=10,
        strategy="ohlcv",
    )

    # Setup streaming buffer
    stream_buf = StreamingPatternBuffer(
        pattern_length=5,
        max_buffer_length=15,
        track_history=True,
    )

    # Generate fake candles with realistic price movement
    base_price = 0.05  # Like PHA
    prices = [base_price]
    for _ in range(300):
        change = random.gauss(0, 0.02)  # 2% std dev
        prices.append(prices[-1] * (1 + change))

    # Generate 300 candles
    candles = []
    ts = int(datetime(2025, 1, 1).timestamp() * 1000)
    for i in range(300):
        o = prices[i]
        c = prices[i + 1]
        h = max(o, c) * 1.005
        l = min(o, c) * 0.995
        v = random.uniform(1000, 10000)
        candles.append(Candle(
            timestamp=ts + i * 60000,
            open=o, high=h, low=l, close=c, volume=v,
            closed=True, exchange="binance",
            symbol="TEST/USDT", timeframe="1m",
        ))

    # Simulate what run_live does — call encode_incremental directly
    # (bypassing process_new_candle to isolate SAX behavior)
    sax_buffer = []
    pattern_buffer = []  # Local copy, like in REST polling mode
    paa_mean = None
    paa_std = None

    # First, encode all candles to compute paa stats (like warmup does)
    import pandas as pd
    rows = []
    for c in candles[:200]:
        rows.append({
            "open": c.open, "high": c.high, "low": c.low,
            "close": c.close, "volume": c.volume,
        })
    df_warmup = pd.DataFrame(rows)
    _, paa_mean, paa_std = sax_encoder.encode_with_normalization(df_warmup)
    print(f"Training PAA stats: mean={paa_mean:.6f}, std={paa_std:.6f}")

    # Now simulate streaming: one candle at a time
    print(f"\nStreaming {len(candles)} candles one at a time...")
    print(f"  window_size={sax_encoder.window_size}, alphabet={sax_encoder.alphabet_size}")
    print(f"  pattern_length={stream_buf.pattern_length}")
    print()

    symbols_produced = 0
    for i, candle in enumerate(candles):
        single_df = candle.to_dataframe_row()
        new_symbols, sax_buffer = sax_encoder.encode_incremental(
            single_df, sax_buffer,
            paa_mean=paa_mean, paa_std=paa_std,
        )

        if new_symbols:
            for sym in new_symbols:
                pattern_buffer.append(sym)
                symbols_produced += 1
                if len(pattern_buffer) > 10:  # pattern_length * 2
                    del pattern_buffer[:len(pattern_buffer) - 10]

                # Sync to stream_buf (like run_live does)
                stream_buf._pattern_buffer.append(sym)
                stream_buf._symbol_counts[sym] += 1
                stream_buf._total_symbols += 1
                stream_buf._symbols_produced += 1
            stream_buf._trim()

        if i in (10, 50, 100, 200, 299) or (i % 50 == 0):
            print(f"  Candle {i+1:3d}: new_syms={len(new_symbols)}, "
                  f"symbols_produced={symbols_produced}, "
                  f"local_pattern_buffer={len(pattern_buffer)}, "
                  f"stream_buf._pattern_buffer={len(stream_buf._pattern_buffer)}, "
                  f"stream_buf.has_pattern()={stream_buf.has_pattern()}, "
                  f"stream_buf.entropy={stream_buf.entropy:.2f}b")
            if stream_buf.has_pattern():
                print(f"             pattern={stream_buf.get_pattern()}")

    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print(f"Total candles: {len(candles)}")
    print(f"Total SAX symbols produced: {symbols_produced}")
    print(f"Local pattern_buffer length: {len(pattern_buffer)}")
    print(f"stream_buf._pattern_buffer length: {len(stream_buf._pattern_buffer)}")
    print(f"stream_buf.has_pattern(): {stream_buf.has_pattern()}")
    print(f"stream_buf.get_pattern(): {stream_buf.get_pattern()}")
    print(f"stream_buf.entropy: {stream_buf.entropy:.2f}b")
    print(f"stream_buf.symbol_counts: {dict(stream_buf._symbol_counts)}")
    print()
    if stream_buf.has_pattern():
        print("✅ SUCCESS: stream_buf has a pattern — display should show it")
    else:
        print("❌ FAILURE: stream_buf has no pattern — display will be empty")
        print()
        print("DIAGNOSIS:")
        if symbols_produced == 0:
            print("  - SAX encoder produced 0 symbols. Check window_size and candle count.")
        elif len(stream_buf._pattern_buffer) == 0:
            print("  - stream_buf._pattern_buffer is empty despite symbols_produced > 0.")
            print("  - The sync logic is broken.")
        elif len(stream_buf._pattern_buffer) < stream_buf.pattern_length:
            print(f"  - stream_buf._pattern_buffer has only {len(stream_buf._pattern_buffer)} symbols, "
                  f"need {stream_buf.pattern_length} for a pattern.")


if __name__ == "__main__":
    asyncio.run(main())
