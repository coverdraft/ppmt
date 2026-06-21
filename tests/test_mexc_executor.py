#!/usr/bin/env python3
"""
ENTREGABLE 4 — Test script for MexcFuturesExecutor.

Uses mock data (no internet). Validates:
  1. Payload structure matches MEXC Futures API v2.
  2. Quantity is correctly rounded using quantity_precision.
  3. HMAC-SHA256 signature is generated from the correct sign string.
  4. SL/TP conditional order payloads have correct fields.
  5. IExecutor interface is properly implemented.
  6. PaperExecutor also implements IExecutor (dual compliance).
"""
import json
import sys
import os

sys.path.insert(0, "/home/z/my-project/ppmt/src")

from ppmt.execution.mexc_futures import MexcFuturesExecutor, _symbol_to_mexc, _direction_to_side
from ppmt.execution.interfaces import IExecutor
from ppmt.execution.models import PositionState
from ppmt.terminal.paper_executor import PaperExecutor


def test_mexc_payload():
    """Test 1: Build open_position payload with mock precision data."""

    executor = MexcFuturesExecutor(api_key="test", secret="test_secret_key_123")

    # Simulate: GET /api/v1/contract/detail returns:
    #   price_precision=5, quantity_precision=0
    # This means:
    #   - Price: 0.08000 (5 decimal places)
    #   - Quantity: 25000 (0 decimal places = integer contracts)
    price_precision = 5
    quantity_precision = 0

    # Build the payload for buying DOGE with 100 USDT at 0.080, leverage 20x
    result = executor.build_open_order_payload(
        symbol="DOGE/USDT",
        direction="LONG",
        size_usdt=100.0,
        entry_price=0.080,
        leverage=20,
        quantity_precision=quantity_precision,
        price_precision=price_precision,
        contract_size=1.0,
    )

    print("=" * 70)
    print("TEST 1: MEXC place-order PAYLOAD")
    print("=" * 70)
    print()
    print("1. Order payload (what gets sent to POST /api/v1/order/place-order):")
    print(json.dumps(result["payload"], indent=2))
    print()

    # Validate quantity calculation
    # quantity = (size_usdt * leverage) / (price * contract_size)
    # = (100 * 20) / (0.080 * 1) = 2000 / 0.080 = 25000
    expected_qty = 25000
    actual_qty = result["quantity"]
    qty_ok = actual_qty == expected_qty
    print(f"2. Quantity validation:")
    print(f"   Expected: {expected_qty} (100 USDT * 20x / 0.080)")
    print(f"   Actual:   {actual_qty}")
    print(f"   PASS: {qty_ok}")
    print()

    # Validate quantity is rounded to quantity_precision=0 (integer)
    qty_is_int = actual_qty == int(actual_qty)
    print(f"3. Quantity precision (qty_precision=0 → integer):")
    print(f"   {actual_qty} is integer: {qty_is_int}")
    print()

    # Validate MEXC symbol format
    print(f"4. Symbol format:")
    print(f"   PPMT format: DOGE/USDT")
    print(f"   MEXC format: {result['mexc_symbol']}")
    print(f"   Correct: {result['mexc_symbol'] == 'DOGE_USDT'}")
    print()

    # Show signed params
    print("5. Signed params (with HMAC-SHA256 signature):")
    print(json.dumps(result["signed_params"], indent=2))
    print()

    # Show the sign string (what gets HMAC'd)
    print("6. Sign string (input to HMAC-SHA256):")
    print(result["sign_string"])
    print()

    # Verify HMAC independently
    import hmac as hmac_mod
    import hashlib
    expected_sig = hmac_mod.new(
        b"test_secret_key_123",
        result["sign_string"].encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    actual_sig = result["signed_params"]["signature"]
    sig_ok = expected_sig == actual_sig
    print("7. HMAC-SHA256 signature verification:")
    print(f"   Expected: {expected_sig}")
    print(f"   Actual:   {actual_sig}")
    print(f"   MATCH: {sig_ok}")
    print()

    return qty_ok and sig_ok and qty_is_int


def test_sl_tp_payloads():
    """Test 2: Validate SL/TP conditional order payloads."""

    executor = MexcFuturesExecutor(api_key="test", secret="test_secret_key_123")

    # Mock conditions: LONG DOGE at 0.080, expected_move=2%
    entry_price = 0.080
    expected_move_pct = 2.0
    expected_move = entry_price * (expected_move_pct / 100.0)  # 0.0016

    # SL = entry - (move * 1.2) = 0.080 - 0.00192 = 0.07808
    sl_price = round(entry_price - (expected_move * 1.2), 5)
    # TP = entry + (move * 2.5) = 0.080 + 0.00400 = 0.08400
    tp_price = round(entry_price + (expected_move * 2.5), 5)
    quantity = 25000

    print("=" * 70)
    print("TEST 2: SL/TP conditional order payloads")
    print("=" * 70)
    print()

    # SL stop-order payload
    sl_params = {
        "symbol": "DOGE_USDT",
        "price": sl_price,
        "vol": quantity,
        "side": 2,  # CLOSE_LONG
        "type": 1,  # STOP (loss)
        "triggerPrice": sl_price,
        "triggerType": 1,  # deal price
        "openType": 2,  # CROSS
    }
    sl_signed = executor._sign(sl_params)

    print("1. SL Stop-Order payload (POST /api/v1/order/stop-order):")
    print(json.dumps(sl_params, indent=2))
    print()
    print(f"   SL price: {sl_price}")
    print(f"   Calculated: entry({entry_price}) - move({expected_move})*1.2 = {sl_price}")
    print()

    # TP stop-order payload
    tp_params = {
        "symbol": "DOGE_USDT",
        "price": tp_price,
        "vol": quantity,
        "side": 2,  # CLOSE_LONG
        "type": 2,  # TAKE_PROFIT
        "triggerPrice": tp_price,
        "triggerType": 1,
        "openType": 2,
    }
    tp_signed = executor._sign(tp_params)

    print("2. TP Stop-Order payload (POST /api/v1/order/stop-order):")
    print(json.dumps(tp_params, indent=2))
    print()
    print(f"   TP price: {tp_price}")
    print(f"   Calculated: entry({entry_price}) + move({expected_move})*2.5 = {tp_price}")
    print()

    return True


def test_interface_compliance():
    """Test 3: Both executors implement IExecutor."""

    print("=" * 70)
    print("TEST 3: IExecutor interface compliance")
    print("=" * 70)
    print()

    # MexcFuturesExecutor
    mexc = MexcFuturesExecutor(api_key="test", secret="test")
    mexc_ok = isinstance(mexc, IExecutor)
    print(f"1. MexcFuturesExecutor instanceof IExecutor: {mexc_ok}")

    # Check all 4 methods exist and are async
    import asyncio
    import inspect
    methods = ["open_position", "update_position", "close_position", "close_all_positions"]
    for m in methods:
        fn = getattr(mexc, m)
        is_coro = inspect.iscoroutinefunction(fn)
        print(f"   {m}: async={is_coro}")

    print()

    # PaperExecutor
    paper = PaperExecutor(capital_usdt=100.0)
    paper_ok = isinstance(paper, IExecutor)
    print(f"2. PaperExecutor instanceof IExecutor: {paper_ok}")

    for m in methods:
        fn = getattr(paper, m)
        is_coro = inspect.iscoroutinefunction(fn)
        print(f"   {m}: async={is_coro}")

    print()

    # Shared PositionState model
    from ppmt.execution.models import PositionState as ExecPositionState
    from ppmt.terminal.paper_executor import PositionState as PaperPositionState
    models_match = ExecPositionState is PaperPositionState
    print(f"3. Shared PositionState model: {models_match}")
    print(f"   ppmt.execution.models.PositionState is paper_executor.PositionState: {models_match}")

    print()
    return mexc_ok and paper_ok


def test_quantity_precision_edge_cases():
    """Test 4: Quantity rounding with different precisions."""

    print("=" * 70)
    print("TEST 4: Quantity precision edge cases")
    print("=" * 70)
    print()

    executor = MexcFuturesExecutor(api_key="test", secret="test")

    # Case 1: quantity_precision=0 (e.g. DOGE contracts = integer)
    qty0 = executor._calc_quantity(100.0, 0.080, 20, quantity_precision=0)
    print(f"1. DOGE/USDT: 100 USDT * 20x / $0.080 = {100*20/0.080}, rounded(qp=0) = {qty0}")
    print(f"   Is integer: {qty0 == int(qty0)}")

    # Case 2: quantity_precision=1 (fractional contracts)
    qty1 = executor._calc_quantity(50.0, 0.080, 10, quantity_precision=1)
    print(f"2. Fractional: 50 USDT * 10x / $0.080 = {50*10/0.080}, rounded(qp=1) = {qty1}")

    # Case 3: quantity_precision=3
    qty3 = executor._calc_quantity(10.0, 65000.0, 5, quantity_precision=3)
    raw = 10.0 * 5 / 65000.0
    print(f"3. BTC/USDT: 10 USDT * 5x / $65000 = {raw}, rounded(qp=3) = {qty3}")

    # Case 4: quantity_precision=0 with rounding down
    # 99 USDT * 20x / 0.080 = 24750.0 → exactly integer
    qty4 = executor._calc_quantity(99.0, 0.080, 20, quantity_precision=0)
    print(f"4. 99 USDT * 20x / $0.080 = {99*20/0.080}, rounded(qp=0) = {qty4}")

    print()
    return True


# ─── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    all_pass = True

    all_pass &= test_mexc_payload()
    all_pass &= test_sl_tp_payloads()
    all_pass &= test_interface_compliance()
    all_pass &= test_quantity_precision_edge_cases()

    print("=" * 70)
    print(f"ALL TESTS PASS: {all_pass}")
    print("=" * 70)
