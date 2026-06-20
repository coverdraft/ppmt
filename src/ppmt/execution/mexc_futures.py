"""
PPMT MEXC Futures Executor — Real-money executor for MEXC Futures API v2.

v0.44.0: ENTREGABLE 4 — Cero ccxt. Código nuevo, limpio y aislado.
All HTTP requests are made directly with ``aiohttp`` (or ``requests``
via ``asyncio.to_thread``). HMAC-SHA256 signatures are computed
manually. No exchange abstraction library is used.

MEXC Futures API v2 endpoints used:
  - GET  /api/v1/contract/detail          — symbol precision info
  - POST /api/v1/leverage                  — set leverage
  - POST /api/v1/order/place-order        — open market order
  - POST /api/v1/order/stop-order         — place SL/TP conditional orders
  - DELETE /api/v1/order                   — cancel order
  - POST /api/v1/order/close-position     — close position
  - POST /api/v1/order/close-all-positions — kill switch

Architecture:
  - Symbol precision is fetched ONCE per symbol on first use (lazy init).
  - Leverage is set ONCE per symbol before the first position open.
  - After a market fill, two conditional orders (SL + TP) are placed
    immediately. Their order IDs are stored in PositionState.exchange_meta.
  - update_position cancels + replaces SL/TP orders individually.
  - All API calls are wrapped in try/except with rate-limit retry (429).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from ppmt.execution.interfaces import IExecutor
from ppmt.execution.models import PositionState, Direction, PositionStatus

logger = logging.getLogger("ppmt.execution.mexc")


# ─── Helpers ──────────────────────────────────────────────────

def _symbol_to_mexc(symbol: str) -> str:
    """Convert PPMT symbol format to MEXC contract symbol.

    "DOGE/USDT" → "DOGE_USDT"
    """
    return symbol.replace("/", "_")


def _direction_to_side(direction: str) -> int:
    """Convert PPMT direction to MEXC side integer.

    MEXC API: 1 = OPEN_LONG, 2 = CLOSE_LONG,
              3 = OPEN_SHORT, 4 = CLOSE_SHORT
    """
    if direction == "LONG":
        return 1
    elif direction == "SHORT":
        return 3
    raise ValueError(f"Invalid direction: {direction!r}")


def _direction_to_close_side(direction: str) -> int:
    """MEXC close-side integer matching the position direction."""
    if direction == "LONG":
        return 2
    elif direction == "SHORT":
        return 4
    raise ValueError(f"Invalid direction: {direction!r}")


# ─── Rate Limit Exception ────────────────────────────────────

class MexcRateLimitError(Exception):
    """Raised when MEXC returns HTTP 429."""
    pass


class MexcApiError(Exception):
    """Raised when MEXC returns a non-success response body."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MEXC API error {code}: {message}")


# ─── MexcFuturesExecutor ─────────────────────────────────────

class MexcFuturesExecutor(IExecutor):
    """
    Real-money executor for MEXC Futures (USDT-M perpetual contracts).

    Usage:
        executor = MexcFuturesExecutor(api_key="...", secret="...")
        pos = await executor.open_position(
            symbol="DOGE/USDT",
            direction="LONG",
            size_usdt=100.0,
            metadata={"entry_price": 0.080, "expected_move_pct": 2.0},
        )
    """

    BASE_URL = "https://contract.mexc.com"

    def __init__(
        self,
        api_key: str,
        secret: str,
        default_leverage: int = 20,
    ):
        self._api_key = api_key
        self._secret = secret
        self._default_leverage = default_leverage

        # Lazy-loaded precision cache: symbol → {price_precision, quantity_precision, ...}
        self._symbol_info: dict[str, dict] = {}

        # Track which symbols have had leverage set
        self._leverage_set: set[str] = set()

        # Open positions indexed by PPMT symbol
        self._positions: dict[str, PositionState] = {}

        # aiohttp session (created on first use)
        self._session: Optional[aiohttp.ClientSession] = None

    # ─── Session management ──────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=self.BASE_URL,
                headers={"X-MEXC-APIKEY": self._api_key},
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session. Call this when shutting down."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ─── HMAC-SHA256 signing ─────────────────────────────────

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Sign request parameters with HMAC-SHA256.

        MEXC Futures API v2 signature algorithm:
          1. Sort all parameters by key.
          2. URL-encode them into a query string.
          3. Compute HMAC-SHA256 of the query string using the API secret.
          4. Append the hex digest as the ``signature`` parameter.

        The ``api_key`` and ``timestamp`` (in milliseconds) are added
        automatically if not already present.

        Args:
            params: Request parameters (without signature).

        Returns:
            New dict with api_key, timestamp, and signature added.
        """
        signed = dict(params)

        # Add API key if not present
        if "api_key" not in signed:
            signed["api_key"] = self._api_key

        # Add timestamp if not present (millisecond epoch)
        if "timestamp" not in signed:
            signed["timestamp"] = int(time.time() * 1000)

        # Sort by key and build query string
        sorted_params = sorted(signed.items())
        query_string = urlencode(sorted_params)

        # HMAC-SHA256
        signature = hmac.new(
            self._secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        signed["signature"] = signature
        return signed

    def _build_sign_string(self, params: dict[str, Any]) -> str:
        """
        Build the exact string that gets signed (for testing/debugging).

        Returns the query string BEFORE the signature is appended.
        """
        tmp = dict(params)
        if "api_key" not in tmp:
            tmp["api_key"] = self._api_key
        if "timestamp" not in tmp:
            tmp["timestamp"] = int(time.time() * 1000)
        sorted_params = sorted(tmp.items())
        return urlencode(sorted_params)

    # ─── HTTP request with rate-limit handling ───────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        signed: bool = True,
    ) -> dict:
        """
        Make an HTTP request to MEXC with automatic rate-limit retry.

        Args:
            method: HTTP method ("GET", "POST", "DELETE").
            path: API path (e.g. "/api/v1/contract/detail").
            params: Request parameters.
            signed: Whether to sign the request.

        Returns:
            Parsed JSON response body.

        Raises:
            MexcRateLimitError: If 429 persists after one retry.
            MexcApiError: If MEXC returns a non-zero code.
        """
        session = await self._get_session()

        if params is None:
            params = {}

        if signed:
            params = self._sign(params)

        for attempt in range(2):
            try:
                if method == "GET":
                    async with session.get(path, params=params) as resp:
                        return await self._handle_response(resp)
                elif method == "POST":
                    async with session.post(path, params=params) as resp:
                        return await self._handle_response(resp)
                elif method == "DELETE":
                    async with session.delete(path, params=params) as resp:
                        return await self._handle_response(resp)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
            except MexcRateLimitError:
                if attempt == 0:
                    logger.warning("[MEXC] Rate limit hit (429), retrying in 1s...")
                    await asyncio.sleep(1)
                    continue
                raise
            except aiohttp.ClientError as e:
                logger.error(f"[MEXC] Connection error: {e}")
                raise ConnectionError(f"MEXC unreachable: {e}") from e

    async def _handle_response(self, resp: aiohttp.ClientResponse) -> dict:
        """Parse response and handle errors."""
        if resp.status == 429:
            raise MexcRateLimitError("MEXC rate limit (429)")

        body = await resp.json(content_type=None)

        # MEXC Futures API returns {"code": 0, "data": ...} on success
        # and {"code": non-zero, "message": "..."} on error
        code = body.get("code", -1)
        if code != 0:
            msg = body.get("message", body.get("msg", "Unknown error"))
            raise MexcApiError(code=code, message=msg, data=body.get("data"))

        return body

    # ─── Symbol info (lazy) ──────────────────────────────────

    async def _fetch_symbol_info(self, symbol: str) -> dict:
        """
        Fetch contract detail for a single symbol.

        GET /api/v1/contract/detail?symbol=DOGE_USDT

        Returns and caches:
          - price_precision (int): Decimal places for price (e.g. 5)
          - quantity_precision (int): Decimal places for quantity (e.g. 0)
          - min_qty (float): Minimum order quantity
          - contract_size (float): Multiplier per contract
        """
        mexc_sym = _symbol_to_mexc(symbol)

        if mexc_sym in self._symbol_info:
            return self._symbol_info[mexc_sym]

        resp = await self._request(
            "GET",
            "/api/v1/contract/detail",
            params={"symbol": mexc_sym},
            signed=False,
        )

        data = resp.get("data", {})
        info = {
            "symbol": mexc_sym,
            "price_precision": int(data.get("pricePrecision", 8)),
            "quantity_precision": int(data.get("quantityPrecision", 0)),
            "min_qty": float(data.get("minQty", 1)),
            "contract_size": float(data.get("contractSize", 1)),
        }

        self._symbol_info[mexc_sym] = info
        logger.info(
            f"[MEXC] Symbol info cached: {mexc_sym} "
            f"price_prec={info['price_precision']} qty_prec={info['quantity_precision']}"
        )
        return info

    # ─── Leverage (lazy) ─────────────────────────────────────

    async def _ensure_leverage(self, symbol: str, leverage: int) -> None:
        """
        Set leverage for a symbol if not already set.

        POST /api/v1/leverage
          symbol=DOGE_USDT&leverage=20&openType=2

        Called once per symbol before the first position open.
        """
        mexc_sym = _symbol_to_mexc(symbol)
        if mexc_sym in self._leverage_set:
            return

        await self._request(
            "POST",
            "/api/v1/leverage",
            params={
                "symbol": mexc_sym,
                "leverage": leverage,
                "openType": 2,  # Cross margin
            },
        )

        self._leverage_set.add(mexc_sym)
        logger.info(f"[MEXC] Leverage set: {mexc_sym} = {leverage}x (cross)")

    # ─── Quantity calculation ────────────────────────────────

    @staticmethod
    def _calc_quantity(
        size_usdt: float,
        price: float,
        leverage: int,
        quantity_precision: int,
        contract_size: float = 1.0,
    ) -> float:
        """
        Calculate order quantity from USDT size.

        quantity = (size_usdt * leverage) / (price * contract_size)

        Then round down to quantity_precision decimal places.
        """
        raw_qty = (size_usdt * leverage) / (price * contract_size)
        # Round down to precision (floor)
        factor = 10 ** quantity_precision
        rounded = int(raw_qty * factor) / factor
        return rounded

    # ─── IExecutor implementation ────────────────────────────

    async def open_position(
        self,
        symbol: str,
        direction: str,
        size_usdt: float,
        metadata: dict,
    ) -> PositionState:
        """
        Open a real futures position on MEXC.

        Flow:
          1. Fetch symbol precision (lazy, once per symbol).
          2. Set leverage (lazy, once per symbol).
          3. Calculate quantity from size_usdt.
          4. POST /api/v1/order/place-order (market order).
          5. Immediately place SL and TP conditional orders.
          6. Return PositionState with exchange_meta containing order IDs.
        """
        if symbol in self._positions and self._positions[symbol].status in (
            "ACTIVE", "BREAK_EVEN_SECURED", "TP_EXTENDED"
        ):
            raise RuntimeError(f"Already in position: {symbol}")

        entry_price = metadata.get("entry_price", 0.0)
        expected_move_pct = metadata.get("expected_move_pct", 1.0)
        leverage = metadata.get("leverage", self._default_leverage)

        # 1. Fetch symbol info
        info = await self._fetch_symbol_info(symbol)
        mexc_sym = info["symbol"]
        qty_prec = info["quantity_precision"]
        price_prec = info["price_precision"]
        contract_size = info["contract_size"]

        # 2. Set leverage
        await self._ensure_leverage(symbol, leverage)

        # 3. Calculate quantity
        quantity = self._calc_quantity(
            size_usdt=size_usdt,
            price=entry_price,
            leverage=leverage,
            quantity_precision=qty_prec,
            contract_size=contract_size,
        )

        if quantity <= 0:
            raise ValueError(
                f"Calculated quantity is 0 for {symbol}: "
                f"size={size_usdt}, price={entry_price}, leverage={leverage}"
            )

        # 4. Place market order
        side = _direction_to_side(direction)
        order_params = {
            "symbol": mexc_sym,
            "price": round(entry_price, price_prec),
            "vol": quantity,
            "side": side,
            "type": 5,          # 5 = MARKET order
            "openType": 2,      # 2 = CROSS margin
            "positionType": 1,  # 1 = FIXED position (perpetual)
        }

        resp = await self._request(
            "POST",
            "/api/v1/order/place-order",
            params=order_params,
        )

        order_data = resp.get("data", {})
        order_id = order_data.get("orderId") or order_data.get("id")

        logger.info(
            f"[MEXC] Market order placed: {direction} {mexc_sym} "
            f"qty={quantity} @ ~{entry_price} order_id={order_id}"
        )

        # 5. Calculate SL/TP prices
        expected_move = entry_price * (expected_move_pct / 100.0)
        if direction == "LONG":
            sl_price = metadata.get("sl_price", entry_price - (expected_move * 1.2))
            tp_price = metadata.get("tp_price", entry_price + (expected_move * 2.5))
            cat_sl = entry_price - (expected_move * 3.0)
        else:
            sl_price = metadata.get("sl_price", entry_price + (expected_move * 1.2))
            tp_price = metadata.get("tp_price", entry_price - (expected_move * 2.5))
            cat_sl = entry_price + (expected_move * 3.0)

        # Round SL/TP to price precision
        sl_price = round(sl_price, price_prec)
        tp_price = round(tp_price, price_prec)

        # Place SL order (stop market)
        sl_order_id = None
        try:
            sl_resp = await self._request(
                "POST",
                "/api/v1/order/stop-order",
                params={
                    "symbol": mexc_sym,
                    "price": round(sl_price, price_prec),
                    "vol": quantity,
                    "side": _direction_to_close_side(direction),
                    "type": 1,          # 1 = STOP (loss)
                    "triggerPrice": round(sl_price, price_prec),
                    "triggerType": 1,   # 1 = deal price
                    "openType": 2,
                },
            )
            sl_data = sl_resp.get("data", {})
            sl_order_id = sl_data.get("orderId") or sl_data.get("id")
            logger.info(f"[MEXC] SL order placed: {sl_price} id={sl_order_id}")
        except (MexcApiError, Exception) as e:
            logger.warning(f"[MEXC] SL order failed (non-fatal): {e}")

        # Place TP order (take profit market)
        tp_order_id = None
        try:
            tp_resp = await self._request(
                "POST",
                "/api/v1/order/stop-order",
                params={
                    "symbol": mexc_sym,
                    "price": round(tp_price, price_prec),
                    "vol": quantity,
                    "side": _direction_to_close_side(direction),
                    "type": 2,          # 2 = TAKE_PROFIT
                    "triggerPrice": round(tp_price, price_prec),
                    "triggerType": 1,
                    "openType": 2,
                },
            )
            tp_data = tp_resp.get("data", {})
            tp_order_id = tp_data.get("orderId") or tp_data.get("id")
            logger.info(f"[MEXC] TP order placed: {tp_price} id={tp_order_id}")
        except (MexcApiError, Exception) as e:
            logger.warning(f"[MEXC] TP order failed (non-fatal): {e}")

        # 6. Build PositionState
        predicted_path = metadata.get("predicted_path_symbols")
        expected_sequence = [[s] for s in predicted_path] if predicted_path else []

        position = PositionState(
            symbol=symbol,
            direction=direction,
            status="ACTIVE",
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc).isoformat(),
            size_usdt=size_usdt,
            current_sl=sl_price,
            current_tp=tp_price,
            catastrophic_sl=round(cat_sl, price_prec),
            expected_sequence=expected_sequence,
            sequence_index=0,
            exchange_meta={
                "mexc_symbol": mexc_sym,
                "order_id": order_id,
                "sl_order_id": sl_order_id,
                "tp_order_id": tp_order_id,
                "quantity": quantity,
                "leverage": leverage,
                "price_precision": price_prec,
                "quantity_precision": qty_prec,
            },
        )

        self._positions[symbol] = position
        return position

    async def update_position(
        self,
        position: PositionState,
        new_sl: Optional[float] = None,
        new_tp: Optional[float] = None,
    ) -> bool:
        """
        Update SL/TP by cancelling old conditional orders and placing new ones.

        Golden rule: If cancellation fails (order already filled or triggered),
        we log the error and return False — we do NOT crash.
        """
        if not position.exchange_meta:
            logger.warning("[MEXC] update_position: no exchange_meta, cannot update")
            return False

        mexc_sym = position.exchange_meta.get("mexc_symbol", "")
        quantity = position.exchange_meta.get("quantity", 0)
        price_prec = position.exchange_meta.get("price_precision", 8)
        direction = position.direction
        success = True

        # Update SL
        if new_sl is not None and new_sl != position.current_sl:
            old_sl_id = position.exchange_meta.get("sl_order_id")
            if old_sl_id:
                try:
                    await self._request(
                        "DELETE",
                        "/api/v1/order",
                        params={"symbol": mexc_sym, "orderId": old_sl_id},
                    )
                    logger.info(f"[MEXC] Old SL order cancelled: {old_sl_id}")
                except MexcApiError as e:
                    logger.warning(f"[MEXC] SL cancel failed (may be filled): {e}")
                    success = False

            if success:
                try:
                    sl_resp = await self._request(
                        "POST",
                        "/api/v1/order/stop-order",
                        params={
                            "symbol": mexc_sym,
                            "price": round(new_sl, price_prec),
                            "vol": quantity,
                            "side": _direction_to_close_side(direction),
                            "type": 1,
                            "triggerPrice": round(new_sl, price_prec),
                            "triggerType": 1,
                            "openType": 2,
                        },
                    )
                    sl_data = sl_resp.get("data", {})
                    new_sl_id = sl_data.get("orderId") or sl_data.get("id")
                    position.exchange_meta["sl_order_id"] = new_sl_id
                    position.current_sl = round(new_sl, price_prec)
                    logger.info(f"[MEXC] New SL placed: {new_sl} id={new_sl_id}")
                except MexcApiError as e:
                    logger.warning(f"[MEXC] New SL placement failed: {e}")
                    success = False

        # Update TP
        if new_tp is not None and new_tp != position.current_tp:
            old_tp_id = position.exchange_meta.get("tp_order_id")
            if old_tp_id:
                try:
                    await self._request(
                        "DELETE",
                        "/api/v1/order",
                        params={"symbol": mexc_sym, "orderId": old_tp_id},
                    )
                    logger.info(f"[MEXC] Old TP order cancelled: {old_tp_id}")
                except MexcApiError as e:
                    logger.warning(f"[MEXC] TP cancel failed (may be filled): {e}")
                    success = False

            if success:
                try:
                    tp_resp = await self._request(
                        "POST",
                        "/api/v1/order/stop-order",
                        params={
                            "symbol": mexc_sym,
                            "price": round(new_tp, price_prec),
                            "vol": quantity,
                            "side": _direction_to_close_side(direction),
                            "type": 2,
                            "triggerPrice": round(new_tp, price_prec),
                            "triggerType": 1,
                            "openType": 2,
                        },
                    )
                    tp_data = tp_resp.get("data", {})
                    new_tp_id = tp_data.get("orderId") or tp_data.get("id")
                    position.exchange_meta["tp_order_id"] = new_tp_id
                    position.current_tp = round(new_tp, price_prec)
                    logger.info(f"[MEXC] New TP placed: {new_tp} id={new_tp_id}")
                except MexcApiError as e:
                    logger.warning(f"[MEXC] New TP placement failed: {e}")
                    success = False

        return success

    async def close_position(
        self,
        position: PositionState,
        reason: str,
    ) -> PositionState:
        """
        Close a specific position on MEXC.

        1. Cancel pending SL/TP orders.
        2. POST /api/v1/order/close-position with the symbol.
        """
        if not position.exchange_meta:
            raise RuntimeError("No exchange_meta — cannot close position")

        mexc_sym = position.exchange_meta.get("mexc_symbol", "")

        # Cancel SL
        old_sl_id = position.exchange_meta.get("sl_order_id")
        if old_sl_id:
            try:
                await self._request(
                    "DELETE",
                    "/api/v1/order",
                    params={"symbol": mexc_sym, "orderId": old_sl_id},
                )
            except MexcApiError as e:
                logger.warning(f"[MEXC] SL cancel on close (may be filled): {e}")

        # Cancel TP
        old_tp_id = position.exchange_meta.get("tp_order_id")
        if old_tp_id:
            try:
                await self._request(
                    "DELETE",
                    "/api/v1/order",
                    params={"symbol": mexc_sym, "orderId": old_tp_id},
                )
            except MexcApiError as e:
                logger.warning(f"[MEXC] TP cancel on close (may be filled): {e}")

        # Close position
        resp = await self._request(
            "POST",
            "/api/v1/order/close-position",
            params={
                "symbol": mexc_sym,
                "side": _direction_to_close_side(position.direction),
            },
        )

        close_data = resp.get("data", {})
        close_price = float(close_data.get("price", position.entry_price))

        # Update position
        position.close_price = close_price
        position.close_reason = reason
        position.status = reason

        if position.direction == "LONG":
            position.pnl_pct = ((close_price - position.entry_price) / position.entry_price) * 100.0
        else:
            position.pnl_pct = ((position.entry_price - close_price) / position.entry_price) * 100.0

        position.pnl_usdt = position.size_usdt * (position.pnl_pct / 100.0)

        logger.info(
            f"[MEXC] Position closed: {mexc_sym} {reason} @ {close_price} "
            f"PnL={position.pnl_pct:+.2f}%"
        )

        # Remove from active positions
        self._positions.pop(position.symbol, None)
        return position

    async def close_all_positions(self) -> bool:
        """
        Kill switch — close ALL open positions via MEXC bulk endpoint.

        POST /api/v1/order/close-all-positions
        """
        all_success = True
        for symbol, pos in list(self._positions.items()):
            if pos.status in ("ACTIVE", "BREAK_EVEN_SECURED", "TP_EXTENDED"):
                try:
                    await self.close_position(pos, "CLOSED_KILL_SWITCH")
                except Exception as e:
                    logger.error(f"[MEXC] Kill switch failed for {symbol}: {e}")
                    all_success = False
        return all_success

    # ─── Test helpers (expose internals for unit testing) ─────

    def build_open_order_payload(
        self,
        symbol: str,
        direction: str,
        size_usdt: float,
        entry_price: float,
        leverage: int,
        quantity_precision: int,
        price_precision: int,
        contract_size: float = 1.0,
    ) -> dict[str, Any]:
        """
        Build the place-order payload WITHOUT sending it.

        Used for testing to verify correct quantity rounding,
        field naming, and signature generation.
        """
        mexc_sym = _symbol_to_mexc(symbol)
        quantity = self._calc_quantity(
            size_usdt=size_usdt,
            price=entry_price,
            leverage=leverage,
            quantity_precision=quantity_precision,
            contract_size=contract_size,
        )
        side = _direction_to_side(direction)

        payload = {
            "symbol": mexc_sym,
            "price": round(entry_price, price_precision),
            "vol": quantity,
            "side": side,
            "type": 5,          # MARKET
            "openType": 2,      # CROSS margin
            "positionType": 1,  # FIXED (perpetual)
        }

        # Sign it
        signed = self._sign(payload)

        return {
            "payload": payload,
            "signed_params": signed,
            "sign_string": self._build_sign_string(payload),
            "quantity": quantity,
            "mexc_symbol": mexc_sym,
        }
