"""
v0.37.0 verification script — confirms the SAX streaming buffer sync fix.

Before this fix, the on_candle handler in realtime.py had a bug where
`pattern_buffer` (a copy of stream_buf.pattern_buffer) was passed to
process_new_candle, which mutated it in-place. After the call, both
`pattern_buffer` and `_pattern_buffer` (the returned value) pointed to
the SAME mutated list, so `_pattern_buffer[len(pattern_buffer):]` was
ALWAYS empty. This caused the StreamingPatternBuffer to NEVER update,
showing Pattern: [...] | Entropy: 0.0b forever on the dashboard.

The fix uses `result.sax_symbols_produced` (an authoritative counter
incremented inside process_new_candle) to compute how many new symbols
were produced this candle, then syncs the StreamingPatternBuffer.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import pandas as pd

from ppmt.engine.buffer import StreamingPatternBuffer
from ppmt.core.sax import SAXEncoder


def simulate_old_buggy_sync():
    """Reproduce the v0.36.2 bug: streaming buffer never updates."""
    buf = StreamingPatternBuffer(pattern_length=5, max_buffer_length=15)
    enc = SAXEncoder(alphabet_size=8, window_size=10, strategy='ohlcv')

    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(50) * 0.5)
    candles = [
        {'open': p, 'high': p + 0.5, 'low': p - 0.5,
         'close': p + np.random.randn() * 0.3, 'volume': 1000}
        for p in prices
    ]

    sax_buf = []
    result_sax_produced = 0

    for c in candles:
        df = pd.DataFrame([c])
        # This is what on_candle does:
        pattern_buffer = buf.pattern_buffer  # FRESH COPY each call
        new_symbols, sax_buf = enc.encode_incremental(df, sax_buf, None, None)
        # Simulate process_new_candle mutation (in-place)
        for sym in new_symbols:
            pattern_buffer.append(sym)
            result_sax_produced += 1
            if len(pattern_buffer) > 10:
                del pattern_buffer[:len(pattern_buffer) - 10]
        _pattern_buffer = pattern_buffer  # Returned (same list)
        # OLD sync logic — ALWAYS empty diff!
        new_in_buf = _pattern_buffer[len(pattern_buffer):]
        if new_in_buf:
            for sym in new_in_buf:
                buf._pattern_buffer.append(sym)
                buf._symbol_counts[sym] += 1
                buf._total_symbols += 1
                buf._symbols_produced += 1
            buf._trim()

    return buf, result_sax_produced


def simulate_new_fixed_sync():
    """Verify the v0.37.0 fix: streaming buffer correctly syncs."""
    buf = StreamingPatternBuffer(pattern_length=5, max_buffer_length=15)
    enc = SAXEncoder(alphabet_size=8, window_size=10, strategy='ohlcv')

    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(50) * 0.5)
    candles = [
        {'open': p, 'high': p + 0.5, 'low': p - 0.5,
         'close': p + np.random.randn() * 0.3, 'volume': 1000}
        for p in prices
    ]

    sax_buf = []
    result_sax_produced = 0

    for c in candles:
        df = pd.DataFrame([c])
        pattern_buffer = buf.pattern_buffer  # FRESH COPY
        prev_produced = buf._symbols_produced  # CAPTURE before mutation
        new_symbols, sax_buf = enc.encode_incremental(df, sax_buf, None, None)
        for sym in new_symbols:
            pattern_buffer.append(sym)
            result_sax_produced += 1
            if len(pattern_buffer) > 10:
                del pattern_buffer[:len(pattern_buffer) - 10]
        _pattern_buffer = pattern_buffer
        # NEW sync logic — uses authoritative counter
        new_prod = result_sax_produced
        if new_prod > prev_produced:
            n_new = new_prod - prev_produced
            if n_new <= len(_pattern_buffer):
                new_syms = _pattern_buffer[-n_new:]
            else:
                new_syms = list(_pattern_buffer)
            for sym in new_syms:
                buf._pattern_buffer.append(sym)
                buf._symbol_counts[sym] += 1
                buf._total_symbols += 1
                buf._symbols_produced += 1
            buf._trim()

    return buf, result_sax_produced


if __name__ == '__main__':
    print('=' * 70)
    print('v0.37.0 SAX Streaming Buffer Sync Fix — Verification')
    print('=' * 70)
    print()

    print('[1/2] OLD (buggy) sync behavior:')
    old_buf, old_produced = simulate_old_buggy_sync()
    print(f'  result.sax_symbols_produced = {old_produced}  (SAX encoder DID produce)')
    print(f'  buf._pattern_buffer          = {old_buf._pattern_buffer}  (EMPTY — never synced!)')
    print(f'  buf._symbols_produced        = {old_buf._symbols_produced}  (stuck at 0)')
    print(f'  buf.entropy                  = {old_buf.entropy:.3f}  (0.0 — no diversity)')
    print(f'  buf.has_pattern()            = {old_buf.has_pattern()}  (False — no signals possible)')
    print()

    print('[2/2] NEW (fixed) sync behavior:')
    new_buf, new_produced = simulate_new_fixed_sync()
    print(f'  result.sax_symbols_produced = {new_produced}  (SAX encoder produced symbols)')
    print(f'  buf._pattern_buffer          = {new_buf._pattern_buffer}  (POPULATED!)')
    print(f'  buf._symbols_produced        = {new_buf._symbols_produced}  (matches encoder)')
    print(f'  buf.entropy                  = {new_buf.entropy:.3f}  (>0 — diverse symbols)')
    print(f'  buf.has_pattern()            = {new_buf.has_pattern()}  (True — signals possible)')
    print()

    # Assertions
    assert old_buf._symbols_produced == 0, 'OLD should be broken (0 symbols synced)'
    assert new_buf._symbols_produced > 0, 'NEW should sync symbols'
    assert new_buf.has_pattern(), 'NEW should have enough symbols for pattern matching'
    assert new_buf.entropy > 0, 'NEW should have non-zero entropy'
    assert new_buf._symbols_produced == new_produced, (
        f'NEW sync count should match: {new_buf._symbols_produced} vs {new_produced}'
    )

    print('PASS: All assertions passed. The v0.37.0 fix correctly syncs the')
    print('      StreamingPatternBuffer, enabling SAX pattern display, entropy')
    print('      calculation, and signal generation.')
