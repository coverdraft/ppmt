"""Tests for MEXC WebSocket feed parsing (v0.32.4).

Verifies that:
  1. _mexc_subscribe_msg includes the `id` field (required by MEXC v3 API).
  2. _parse_mexc_kline handles MEXC v3 kline format (no "x" field).
  3. _parse_mexc_kline infers `closed` from current time vs k["T"] when no "x".
  4. _parse_mexc_kline trusts explicit "x" field if MEXC ever includes one.
"""
import time
import json
from ppmt.data.websocket_feed import (
    _mexc_subscribe_msg,
    _parse_mexc_kline,
    Candle,
)


def test_subscribe_msg_has_id():
    """MEXC v3 API REQUIRES `id` on SUBSCRIPTION requests."""
    msg = _mexc_subscribe_msg("ETH/USDT", "1h", msg_id=42)
    assert msg["method"] == "SUBSCRIPTION"
    assert msg["id"] == 42
    assert "spot@public.kline.v3.api+Min60+ethusdt" in msg["params"]


def test_subscribe_msg_default_id():
    """Default id is 1 when not specified."""
    msg = _mexc_subscribe_msg("BTC/USDT", "5m")
    assert msg["id"] == 1
    assert "spot@public.kline.v3.api+Min5+btcusdt" in msg["params"]


def test_parse_mexc_kline_no_x_field_infers_open():
    """MEXC v3 kline messages have no 'x' field. When the candle period
    hasn't ended yet, `closed` should be False (inferred from wall-clock
    time vs k["T"]).
    """
    now_ms = int(time.time() * 1000)
    msg = {
        "c": "spot@public.kline.v3.api+Min60+ethusdt",
        "d": {
            "e": "spot@public.kline.v3.api",
            "k": {
                "t": now_ms,                  # start = now
                "T": now_ms + 3_600_000,      # end = +1h
                "s": "ETHUSDT",
                "i": "Min60",
                "o": "1777.0",
                "c": "1778.5",
                "h": "1780.0",
                "l": "1776.0",
                "v": "1234.5",
                "a": "2_193_000.0",
            },
        },
        "s": "ETHUSDT",
        "t": now_ms,
    }
    candle = _parse_mexc_kline(msg, "ETH/USDT", "1h")
    assert candle is not None
    assert candle.timestamp == now_ms
    assert candle.open == 1777.0
    assert candle.close == 1778.5
    assert candle.high == 1780.0
    assert candle.low == 1776.0
    assert candle.volume == 1234.5
    assert candle.exchange == "mexc"
    # Candle ends in the future → not closed yet
    assert candle.closed is False


def test_parse_mexc_kline_no_x_field_infers_closed():
    """When current time is past k["T"], the candle is closed."""
    now_ms = int(time.time() * 1000)
    msg = {
        "d": {
            "k": {
                "t": now_ms - 7_200_000,      # started 2h ago
                "T": now_ms - 3_600_000,      # ended 1h ago
                "o": "1777.0",
                "c": "1780.0",
                "h": "1782.0",
                "l": "1775.0",
                "v": "9999.0",
            },
        },
    }
    candle = _parse_mexc_kline(msg, "ETH/USDT", "1h")
    assert candle is not None
    assert candle.closed is True


def test_parse_mexc_kline_with_explicit_x_uses_x():
    """If MEXC ever includes 'x' in the kline, we trust it over time inference."""
    now_ms = int(time.time() * 1000)
    msg = {
        "d": {
            "k": {
                "t": now_ms,                  # started now
                "T": now_ms + 3_600_000,      # ends in 1h
                "o": "100.0", "c": "101.0", "h": "102.0", "l": "99.0",
                "v": "10.0",
                "x": True,                    # explicit closed flag
            },
        },
    }
    candle = _parse_mexc_kline(msg, "BTC/USDT", "1h")
    assert candle is not None
    # Even though T is in the future, explicit x=True wins
    assert candle.closed is True


def test_parse_mexc_kline_missing_k_returns_none():
    """Non-kline messages (e.g., subscription confirmations) return None."""
    msg = {"id": 1, "code": 0, "msg": "ok"}
    candle = _parse_mexc_kline(msg, "ETH/USDT", "1h")
    assert candle is None


def test_parse_mexc_kline_alternative_nesting():
    """Some MEXC messages put k directly at top level instead of nested in d."""
    msg = {
        "k": {
            "t": 1_700_000_000_000,
            "T": 1_700_003_600_000,
            "o": "100.0", "c": "101.0", "h": "102.0", "l": "99.0",
            "v": "10.0",
        },
    }
    candle = _parse_mexc_kline(msg, "BTC/USDT", "1h")
    assert candle is not None
    assert candle.open == 100.0


if __name__ == "__main__":
    test_subscribe_msg_has_id()
    test_subscribe_msg_default_id()
    test_parse_mexc_kline_no_x_field_infers_open()
    test_parse_mexc_kline_no_x_field_infers_closed()
    test_parse_mexc_kline_with_explicit_x_uses_x()
    test_parse_mexc_kline_missing_k_returns_none()
    test_parse_mexc_kline_alternative_nesting()
    print("All MEXC parser tests passed.")
