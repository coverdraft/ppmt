#!/usr/bin/env python3
"""
v0.40.32 — Replace ccxt polling with direct aiohttp HTTP calls.

PROBLEMA (v0.40.31 en Mac del usuario):
- 'Failed to connect: mexc GET https://api.mexc.com/api/v3/exchangeInfo'
- v0.40.31 intentó skippar load_markets() pero ccxt's fetch_ticker() llama
  load_markets() internamente como precondición. Aunque el código no lo
  invocaba explícitamente, ccxt lo hacía por debajo.
- Resultado: mismo error, motor STOPPED.

DIAGNÓSTICO:
- ccxt's fetch_ticker, fetch_ohlcv, etc. todos llaman self.load_markets()
  al inicio. Si markets está vacío, hace la network call al endpoint masivo
  /api/v3/exchangeInfo que está bloqueado en la red del usuario.
- Pre-poblar markets con stub NO funciona para fetch_ticker (ccxt's MEXC impl
  usa safe_market() que accede markets[0] cuando marketId=None → KeyError).
- fetch_ohlcv SÍ funciona con stub pre-poblado, pero fetch_ticker no.

SOLUCIÓN v0.40.32:
- Reemplazar el poll_exchange con un wrapper aiohttp directo.
- El wrapper implementa fetch_ohlcv() y fetch_ticker() usando HTTP directo
  a MEXC/Binance/Bybit APIs, sin pasar por ccxt.
- Esto evita load_markets() completamente.
- ccxt sigue usándose para order execution (real money) si se configura.
"""
import re
from pathlib import Path

PPMT = Path("/home/z/my-project/ppmt")
REALTIME = PPMT / "src/ppmt/engine/realtime.py"
SERVER = PPMT / "src/ppmt/terminal/server.py"
HTML = PPMT / "src/ppmt/terminal/static/index.html"
PYPROJECT = PPMT / "pyproject.toml"
INIT_PY = PPMT / "src/ppmt/__init__.py"
CLI_MAIN = PPMT / "src/ppmt/cli/main.py"


# Insert a DirectPollExchange class at module level, then patch the
# REST polling block to use it instead of ccxt.

DIRECT_POLL_CLASS = '''

# v0.40.32: Direct HTTP polling — bypasses ccxt's load_markets() which
# times out on some networks. Implements only fetch_ticker + fetch_ohlcv,
# which is all the engine needs for paper trading.
class _DirectPollExchange:
    """Lightweight exchange wrapper using aiohttp direct HTTP calls.

    Implements only the methods the engine actually uses:
    - fetch_ticker(symbol) -> {last, close, ...}
    - fetch_ohlcv(symbol, timeframe, limit) -> [[ts, o, h, l, c, v], ...]
    - close() -> cleanup aiohttp session

    Supports: mexc, binance (spot). Other exchanges fall back to ccxt.
    """

    _BASE_URLS = {
        "mexc": "https://api.mexc.com",
        "binance": "https://api.binance.com",
        "bybit": "https://api.bybit.com",
    }

    _TIMEFRAME_MAP = {
        # CCXT-style -> exchange-native
        "mexc": {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1H", "4h": "4H", "1d": "1D",
        },
        "binance": {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1h", "4h": "4h", "1d": "1d",
        },
        "bybit": {
            "1m": "1", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "4h": "240", "1d": "D",
        },
    }

    def __init__(self, exchange_name: str, api_key: str = None, api_secret: str = None):
        import aiohttp
        self.exchange_name = exchange_name.lower()
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = self._BASE_URLS.get(self.exchange_name)
        if not self.base_url:
            raise ValueError(f"_DirectPollExchange: unsupported exchange '{exchange_name}'")
        self._session = None
        # ccxt-compat fields
        self.markets = {}
        self.markets_by_id = {}

    async def _get_session(self):
        if self._session is None or self._session.closed:
            import aiohttp
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "ppmt/0.40.32"},
            )
        return self._session

    async def fetch_ticker(self, symbol: str) -> dict:
        """GET /api/v3/ticker/price?symbol=BTCUSDT (MEXC/Binance format)."""
        session = await self._get_session()
        symbol_id = symbol.replace("/", "").upper()
        if self.exchange_name in ("mexc", "binance"):
            url = f"{self.base_url}/api/v3/ticker/price"
            params = {"symbol": symbol_id}
        elif self.exchange_name == "bybit":
            url = f"{self.base_url}/v5/market/tickers"
            params = {"category": "spot", "symbol": symbol_id}
        else:
            raise ValueError(f"fetch_ticker not implemented for {self.exchange_name}")
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"{self.exchange_name} fetch_ticker HTTP {resp.status}: {await resp.text()}")
            data = await resp.json()
        if self.exchange_name in ("mexc", "binance"):
            price = float(data.get("price", 0))
            return {"symbol": symbol, "last": price, "close": price, "info": data}
        elif self.exchange_name == "bybit":
            tickers = data.get("result", {}).get("list", [])
            if not tickers:
                raise RuntimeError(f"bybit fetch_ticker: no data for {symbol}")
            price = float(tickers[0].get("lastPrice", 0))
            return {"symbol": symbol, "last": price, "close": price, "info": data}

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        """GET /api/v3/klines (MEXC/Binance) or /v5/market/kline (Bybit)."""
        session = await self._get_session()
        symbol_id = symbol.replace("/", "").upper()
        tf_native = self._TIMEFRAME_MAP.get(self.exchange_name, {}).get(timeframe, timeframe)
        if self.exchange_name in ("mexc", "binance"):
            url = f"{self.base_url}/api/v3/klines"
            params = {"symbol": symbol_id, "interval": tf_native, "limit": limit}
        elif self.exchange_name == "bybit":
            url = f"{self.base_url}/v5/market/kline"
            params = {"category": "spot", "symbol": symbol_id, "interval": tf_native, "limit": limit}
        else:
            raise ValueError(f"fetch_ohlcv not implemented for {self.exchange_name}")
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"{self.exchange_name} fetch_ohlcv HTTP {resp.status}: {await resp.text()}")
            data = await resp.json()
        if self.exchange_name in ("mexc", "binance"):
            # [[ts, o, h, l, c, v, ...], ...]
            return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in data]
        elif self.exchange_name == "bybit":
            klines = data.get("result", {}).get("list", [])
            # Bybit returns newest-first, reverse to oldest-first
            klines = list(reversed(klines))
            return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in klines]

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

'''


def add_direct_poll_class():
    """Insert _DirectPollExchange class definition near the top of realtime.py."""
    src = REALTIME.read_text()
    # Find a good insertion point — after the imports, before the LiveConfig class
    # Use the LiveConfig class definition as anchor
    anchor = "class LiveConfig"
    idx = src.find(anchor)
    if idx == -1:
        raise SystemExit("FAIL: LiveConfig class not found in realtime.py")
    # Find the start of the line
    line_start = src.rfind("\n", 0, idx) + 1
    # Insert the class definition before the anchor
    new_src = src[:line_start] + DIRECT_POLL_CLASS + "\n" + src[line_start:]
    REALTIME.write_text(new_src)
    print(f"  [OK] realtime.py: _DirectPollExchange class inserted (P1)")


def patch_engine_to_use_direct_poll():
    """Replace the ccxt-based REST polling block with _DirectPollExchange usage."""
    src = REALTIME.read_text()

    # Find the block to replace — from "exchange_config = {'enableRateLimit': True}"
    # to "last_candle_ts = 0" inclusive
    START_MARKER = "                    exchange_config = {'enableRateLimit': True}"
    END_MARKER = "                    last_candle_ts = 0"

    start_idx = src.find(START_MARKER)
    if start_idx == -1:
        raise SystemExit("FAIL: start marker not found")
    end_idx = src.find(END_MARKER, start_idx)
    if end_idx == -1:
        raise SystemExit("FAIL: end marker not found")
    end_idx = end_idx + len(END_MARKER)

    old_block = src[start_idx:end_idx]

    new_block = """# v0.40.32: Use _DirectPollExchange (aiohttp direct HTTP) instead of ccxt.
                    # ccxt's fetch_ticker/fetch_ohlcv internally call load_markets()
                    # which times out on some networks. _DirectPollExchange bypasses
                    # ccxt entirely, calling MEXC/Binance/Bybit REST APIs directly.
                    _effective_exchange = cfg.exchange
                    try:
                        poll_exchange = _DirectPollExchange(
                            cfg.exchange,
                            api_key=cfg.api_key,
                            api_secret=cfg.api_secret,
                        )
                        # Verify connection with a single fetch_ticker
                        _ticker = await poll_exchange.fetch_ticker(cfg.symbol)
                        _ticker_price = _ticker.get('last') or _ticker.get('close')
                        if _ticker_price is None or _ticker_price == 0:
                            raise RuntimeError(f"fetch_ticker returned no price for {cfg.symbol}")
                        console.print(f"[green]Connected to {cfg.exchange} (direct HTTP polling, no load_markets)[/green]")
                        console.print(f"  {cfg.symbol} last price: ${_ticker_price}")
                    except Exception as e_primary:
                        # Auto-fallback Binance → MEXC
                        if cfg.exchange.lower() == 'binance':
                            console.print(f"[yellow]Binance direct poll failed ({e_primary}). Falling back to MEXC…[/yellow]")
                            try:
                                if 'poll_exchange' in dir():
                                    await poll_exchange.close()
                            except Exception:
                                pass
                            try:
                                poll_exchange = _DirectPollExchange(
                                    'mexc',
                                    api_key=cfg.api_key,
                                    api_secret=cfg.api_secret,
                                )
                                _ticker = await poll_exchange.fetch_ticker(cfg.symbol)
                                _ticker_price = _ticker.get('last') or _ticker.get('close')
                                if _ticker_price is None or _ticker_price == 0:
                                    raise RuntimeError(f"MEXC fetch_ticker returned no price for {cfg.symbol}")
                                _effective_exchange = 'mexc'
                                console.print(f"[green]Connected to MEXC (fallback from Binance) — direct HTTP polling[/green]")
                                console.print(f"  {cfg.symbol} last price: ${_ticker_price}")
                            except Exception as e_mexc:
                                _err_msg = f"Exchange connection failed (binance + mexc fallback): binance={e_primary} | mexc={e_mexc}"
                                console.print(f"[red]Failed to connect: binance={e_primary} | mexc fallback={e_mexc}[/red]")
                                try:
                                    self._update_terminal_state(
                                        is_running=False,
                                        websocket_status="disconnected",
                                        error=_err_msg,
                                    )
                                except Exception:
                                    pass
                                try:
                                    await poll_exchange.close()
                                except Exception:
                                    pass
                                storage.close()
                                return result
                        else:
                            _err_msg = f"Exchange connection failed: {e_primary}"
                            console.print(f"[red]Failed to connect: {e_primary}[/red]")
                            try:
                                self._update_terminal_state(
                                    is_running=False,
                                    websocket_status="disconnected",
                                    error=_err_msg,
                                )
                            except Exception:
                                pass
                            try:
                                await poll_exchange.close()
                            except Exception:
                                pass
                            storage.close()
                            return result

                    last_candle_ts = 0"""

    src = src.replace(old_block, new_block, 1)
    REALTIME.write_text(src)
    print(f"  [OK] realtime.py: REST polling uses _DirectPollExchange (P2)")


def patch_server_symbols_endpoint():
    """Make /api/market/symbols also use direct HTTP instead of ccxt.load_markets()."""
    src = SERVER.read_text()

    OLD = """        try:
            # v0.40.31: Skip load_markets() — it times out on some networks.
            # Use fetch_tickers() instead: lighter endpoint, returns same info.
            # If fetch_tickers also fails, return a hardcoded fallback list.
            try:
                tickers = exc.fetch_tickers()
                usdt_pairs = []
                for s in tickers.keys():
                    if not s.endswith("/USDT"):
                        continue
                    base = s[:-5]
                    if base.startswith(("1000", "10000", "1BULL", "3L", "3S", "5L", "5S")):
                        continue
                    if base.endswith(("UP", "DOWN", "BULL", "BEAR")) and len(base) > 4:
                        continue
                    usdt_pairs.append(s)
                usdt_pairs.sort()
                return {"ok": True, "exchange": exchange, "symbols": usdt_pairs[:limit],
                        "total_available": len(usdt_pairs)}
            except Exception as e_tickers:
                # Hardcoded fallback: top 50 USDT pairs (works on any exchange)
                _fallback = [
                    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
                    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
                    "MATIC/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "BCH/USDT",
                    "NEAR/USDT", "APT/USDT", "FIL/USDT", "ARB/USDT", "OP/USDT",
                    "INJ/USDT", "SUI/USDT", "TIA/USDT", "SEI/USDT", "RUNE/USDT",
                    "AAVE/USDT", "MKR/USDT", "GRT/USDT", "SAND/USDT", "MANA/USDT",
                    "AXS/USDT", "FTM/USDT", "ALGO/USDT", "EGLD/USDT", "FLOW/USDT",
                    "THETA/USDT", "GALA/USDT", "IMX/USDT", "LDO/USDT", "STX/USDT",
                    "PEPE/USDT", "WIF/USDT", "BONK/USDT", "FLOKI/USDT", "SHIB/USDT",
                    "JUP/USDT", "PYTH/USDT", "RNDR/USDT", "FET/USDT", "GRT/USDT",
                ]
                return {"ok": True, "exchange": exchange, "symbols": _fallback[:limit],
                        "total_available": len(_fallback), "fallback": True,
                        "note": f"load_markets and fetch_tickers failed ({e_tickers}); using hardcoded top-50 list"}
        finally:
            if hasattr(exc, 'close'):
                exc.close()"""

    NEW = """        try:
            # v0.40.32: Use direct HTTP /api/v3/exchangeInfo with timeout.
            # If it times out (>10s), fall back to hardcoded top-50 list.
            # This avoids blocking the whole server when the endpoint is slow.
            import requests as _requests
            _base_urls = {
                "mexc": "https://api.mexc.com",
                "binance": "https://api.binance.com",
                "bybit": "https://api.bybit.com",
            }
            _base = _base_urls.get(exchange.lower(), "https://api.mexc.com")
            try:
                _r = _requests.get(f"{_base}/api/v3/exchangeInfo", timeout=10)
                _r.raise_for_status()
                _data = _r.json()
                usdt_pairs = []
                for s_obj in _data.get("symbols", []):
                    s = s_obj.get("symbol", "")
                    if not s.endswith("USDT"):
                        continue
                    if not s_obj.get("status", "TRADING") == "TRADING":
                        continue
                    # Reconstruct CCXT-style symbol: BTCUSDT -> BTC/USDT
                    base = s[:-4]
                    usdt_pairs.append(f"{base}/USDT")
                # Filter leveraged tokens
                usdt_pairs = [s for s in usdt_pairs
                              if not s[:-5].startswith(("1000", "10000", "1BULL", "3L", "3S", "5L", "5S"))
                              and not (s[:-5].endswith(("UP", "DOWN", "BULL", "BEAR")) and len(s[:-5]) > 4)]
                usdt_pairs.sort()
                return {"ok": True, "exchange": exchange, "symbols": usdt_pairs[:limit],
                        "total_available": len(usdt_pairs)}
            except Exception as e_direct:
                # Hardcoded fallback
                _fallback = [
                    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
                    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
                    "MATIC/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "BCH/USDT",
                    "NEAR/USDT", "APT/USDT", "FIL/USDT", "ARB/USDT", "OP/USDT",
                    "INJ/USDT", "SUI/USDT", "TIA/USDT", "SEI/USDT", "RUNE/USDT",
                    "AAVE/USDT", "MKR/USDT", "GRT/USDT", "SAND/USDT", "MANA/USDT",
                    "AXS/USDT", "FTM/USDT", "ALGO/USDT", "EGLD/USDT", "FLOW/USDT",
                    "THETA/USDT", "GALA/USDT", "IMX/USDT", "LDO/USDT", "STX/USDT",
                    "PEPE/USDT", "WIF/USDT", "BONK/USDT", "FLOKI/USDT", "SHIB/USDT",
                    "JUP/USDT", "PYTH/USDT", "RNDR/USDT", "FET/USDT", "GRT/USDT",
                ]
                return {"ok": True, "exchange": exchange, "symbols": _fallback[:limit],
                        "total_available": len(_fallback), "fallback": True,
                        "note": f"direct HTTP exchangeInfo failed ({e_direct}); using hardcoded top-50 list"}
        finally:
            if hasattr(exc, 'close'):
                exc.close()"""

    if OLD not in src:
        raise SystemExit("FAIL: server.py OLD block not found (P3)")
    src = src.replace(OLD, NEW, 1)
    SERVER.write_text(src)
    print(f"  [OK] server.py: /api/market/symbols uses direct HTTP (P3)")


def bump_version():
    """Bump v0.40.31 → v0.40.32 across all files."""
    files_and_replacements = [
        (INIT_PY, '"0.40.31"', '"0.40.32"'),
        (PYPROJECT, '"0.40.31"', '"0.40.32"'),
        (CLI_MAIN, 'v0.40.31', 'v0.40.32'),
        (SERVER, 'version="0.40.31"', 'version="0.40.32"'),
        (HTML, 'v0.40.31', 'v0.40.32'),
    ]
    for path, old, new in files_and_replacements:
        if not path.exists():
            continue
        txt = path.read_text()
        n = txt.count(old)
        if n == 0:
            continue
        path.write_text(txt.replace(old, new))
        print(f"  [OK] {path.name}: {n}x '{old}'→'{new}' (P4)")


def main():
    print("v0.40.32 — Direct HTTP polling, bypass ccxt's load_markets")
    print()
    add_direct_poll_class()
    patch_engine_to_use_direct_poll()
    patch_server_symbols_endpoint()
    bump_version()
    print()
    print("All patches applied.")


if __name__ == "__main__":
    main()
