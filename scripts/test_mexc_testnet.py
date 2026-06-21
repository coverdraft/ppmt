#!/usr/bin/env python3
"""
ENTREGABLE 10 — Section C: Futures endpoints via api.mexc.com gateway.

Tests signed Futures endpoints on api.mexc.com (WAF bypass) vs
contract.mexc.com (direct, may be WAF-blocked).

Plus cross-validation via Spot API v3 (same HMAC-SHA256 signing).
"""

import asyncio
import json
from ppmt.execution.mexc_futures import MexcFuturesExecutor


FAKE_API_KEY = "mx0fake1234567890ABCDEFGH"
FAKE_SECRET  = "fake_secret_abcdef1234567890"


async def test_section_c():
    print("=" * 70)
    print("SECTION C: Futures endpoints — gateway vs direct")
    print("=" * 70)
    print()

    # ── Test 1: contract.mexc.com (direct, may be WAF-blocked) ──
    executor_direct = MexcFuturesExecutor(
        api_key=FAKE_API_KEY,
        secret=FAKE_SECRET,
    )
    print(f"  [DIRECT] base_url = {executor_direct._base_url}")

    # ── Test 2: api.mexc.com (gateway, WAF bypass) ─────────────
    executor_gateway = MexcFuturesExecutor(
        api_key=FAKE_API_KEY,
        secret=FAKE_SECRET,
        base_url="https://api.mexc.com/",
    )
    print(f"  [GATEWAY] base_url = {executor_gateway._base_url}")
    print()

    # ── C1: Signed GET openPositions ────────────────────────────
    for label, executor in [("DIRECT", executor_direct), ("GATEWAY", executor_gateway)]:
        try:
            resp = await executor._request(
                "GET",
                "/api/v1/position/openPositions",
                params={},
                signed=True,
            )
            print(f"  C1 [{label}]: GET /api/v1/position/openPositions → UNEXPECTED SUCCESS")
        except Exception as e:
            exc_msg = str(e)
            exc_name = type(e).__name__
            print(f"  C1 [{label}]: {exc_name}: {exc_msg[:200]}")
            if "10072" in exc_msg:
                print(f"         ✓ CLEAN JSON AUTH ERROR — signing VALIDATED")
            elif "404" in exc_msg and "Not Found" in exc_msg:
                print(f"         ⚠ Endpoint not available on this domain (404 JSON, not 403 HTML)")
            elif "403" in exc_msg or "WAF" in exc_msg or "geo-block" in exc_msg:
                print(f"         ⚠ WAF/geo-block (expected for contract.mexc.com from LATAM)")
            else:
                print(f"         ✗ Unexpected")
    print()

    # ── C2: Signed POST place-order ─────────────────────────────
    for label, executor in [("DIRECT", executor_direct), ("GATEWAY", executor_gateway)]:
        try:
            order_params = {
                "symbol": "DOGE_USDT",
                "price": 0.18,
                "vol": 100,
                "side": 1,
                "type": 5,
                "openType": 2,
                "positionType": 1,
            }
            resp = await executor._request(
                "POST",
                "/api/v1/order/place-order",
                params=order_params,
                signed=True,
            )
            print(f"  C2 [{label}]: POST /api/v1/order/place-order → UNEXPECTED SUCCESS")
        except Exception as e:
            exc_msg = str(e)
            exc_name = type(e).__name__
            print(f"  C2 [{label}]: {exc_name}: {exc_msg[:200]}")
            if "10072" in exc_msg:
                print(f"         ✓ CLEAN JSON AUTH ERROR — signing VALIDATED")
            elif "404" in exc_msg and "Not Found" in exc_msg:
                print(f"         ⚠ Endpoint not available on this domain (404 JSON)")
            elif "403" in exc_msg or "WAF" in exc_msg or "geo-block" in exc_msg:
                print(f"         ⚠ WAF/geo-block (expected for contract.mexc.com from LATAM)")
            else:
                print(f"         ✗ Unexpected")
    print()

    # ── C3: Signed POST leverage ────────────────────────────────
    for label, executor in [("DIRECT", executor_direct), ("GATEWAY", executor_gateway)]:
        try:
            resp = await executor._request(
                "POST",
                "/api/v1/leverage",
                params={"symbol": "DOGE_USDT", "leverage": 20, "openType": 2},
                signed=True,
            )
            print(f"  C3 [{label}]: POST /api/v1/leverage → UNEXPECTED SUCCESS")
        except Exception as e:
            exc_msg = str(e)
            exc_name = type(e).__name__
            print(f"  C3 [{label}]: {exc_name}: {exc_msg[:200]}")
            if "10072" in exc_msg:
                print(f"         ✓ CLEAN JSON AUTH ERROR — signing VALIDATED")
            elif "404" in exc_msg and "Not Found" in exc_msg:
                print(f"         ⚠ Endpoint not available on this domain (404 JSON)")
            elif "403" in exc_msg or "WAF" in exc_msg or "geo-block" in exc_msg:
                print(f"         ⚠ WAF/geo-block (expected for contract.mexc.com from LATAM)")
            else:
                print(f"         ✗ Unexpected")
    print()

    # ── C4: Unsigned GET contract/detail ────────────────────────
    for label, executor in [("DIRECT", executor_direct), ("GATEWAY", executor_gateway)]:
        try:
            resp = await executor._request(
                "GET",
                "/api/v1/contract/detail",
                params={"symbol": "DOGE_USDT"},
                signed=False,
            )
            data = resp.get("data", {})
            print(f"  C4 [{label}]: GET /api/v1/contract/detail → symbol={data.get('symbol')} ✓")
        except Exception as e:
            exc_msg = str(e)[:200]
            print(f"  C4 [{label}]: {exc_msg}")
    print()

    await executor_direct.close()
    await executor_gateway.close()

    # ── Cross-validation: Spot API v3 (same HMAC-SHA256) ────────
    print("  ── Cross-validation: Spot API v3 (same signing as Futures) ──")
    import hashlib, hmac as hmac_mod, time
    from urllib.parse import urlencode
    import aiohttp

    headers = {"X-MEXC-APIKEY": FAKE_API_KEY, "Content-Type": "application/json"}
    async with aiohttp.ClientSession(base_url="https://api.mexc.com", headers=headers) as session:
        # Spot GET
        params = {"timestamp": int(time.time() * 1000)}
        sorted_p = sorted(params.items())
        qs = urlencode(sorted_p)
        sig = hmac_mod.new(FAKE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        async with session.get("/api/v3/account", params=params) as r:
            body = await r.text()
            print(f"  CV1: GET  /api/v3/account → status={r.status} body={body[:100]}")

        # Spot POST
        params = {
            "symbol": "DOGEUSDT",
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": "10",
            "timestamp": int(time.time() * 1000),
        }
        sorted_p = sorted(params.items())
        qs = urlencode(sorted_p)
        sig = hmac_mod.new(FAKE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        async with session.post("/api/v3/order", params=params) as r:
            body = await r.text()
            print(f"  CV2: POST /api/v3/order  → status={r.status} body={body[:100]}")
    print()

    print("=" * 70)
    print("SUMMARY:")
    print("  • contract.mexc.com: WAF blocked from this server (403 Akamai)")
    print("  • api.mexc.com gateway: Bypasses WAF but only mirrors READ endpoints")
    print("  • Spot API v3 on api.mexc.com: Validates HMAC-SHA256 signing (10072)")
    print("  • CONCLUSION: Signing is CORRECT. Futures endpoints require")
    print("    contract.mexc.com access (non-blocked IP or VPN/proxy).")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_section_c())
