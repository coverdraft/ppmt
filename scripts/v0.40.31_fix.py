#!/usr/bin/env python3
"""
v0.40.31 — Skip load_markets() entirely, inject manual market stub.

PROBLEMA (v0.40.30 en Mac del usuario):
- `Failed to connect: mexc GET https://api.mexc.com/api/v3/exchangeInfo`
- MEXC's /api/v3/exchangeInfo endpoint (que retorna TODOS los ~3260 mercados)
  está BLOQUEADO o TIMEOUT desde la Mac del usuario.
- PERO /api/v3/ticker/price (fetch_ticker) y /api/v3/klines (fetch_ohlcv)
  SÍ funcionan — el log muestra 200 OK en /api/market/price?exchange=mexc.
- v0.40.29 auto-fallback Binance→MEXC no sirve si MEXC también falla en load_markets.

DIAGNÓSTICO:
- load_markets() descarga TODOS los mercados del exchange (3260 para MEXC,
  3600 para Binance). Algunas redes/ISPs bloquean o rate-limitean este endpoint
  masivo, pero permiten endpoints individuales como ticker y klines.
- ccxt requiere que `exchange.markets[symbol]` exista antes de llamar
  fetch_ohlcv/fetch_ticker. Normalmente load_markets() popula esto.
- WORKAROUND: inyectar manualmente un market stub para el símbolo específico
  que se está tradeando, sin llamar load_markets().

FIX v0.40.31:
1. Reemplazar load_markets() con un helper `_inject_market_stub()` que:
   - Construye un market dict mínimo para cfg.symbol
   - Lo asigna a exchange.markets[symbol] y exchange.markets_by_id[symbol_id]
   - Verifica con fetch_ticker que el símbolo exista
2. Si fetch_ticker falla → el símbolo no existe o el exchange está caído → return
3. Si fetch_ticker OK → fetch_ohlcv va a funcionar (mismo endpoint pattern)
4. Aplica a cualquier exchange (no solo Binance/MEXC).
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


def patch_realtime():
    """Replace the entire load_markets block with a stub-injection approach."""
    src = REALTIME.read_text()

    # The old block starts at "v0.40.29: Auto-fallback" comment and ends at
    # "last_candle_ts = 0" — we replace the whole thing with a simpler version
    # that injects a market stub instead of calling load_markets.

    # Find the block boundaries
    START_MARKER = "                    # v0.40.29: Auto-fallback Binance → MEXC."
    END_MARKER = "                    last_candle_ts = 0"

    start_idx = src.find(START_MARKER)
    if start_idx == -1:
        raise SystemExit("FAIL: start marker not found in realtime.py")
    end_idx = src.find(END_MARKER, start_idx)
    if end_idx == -1:
        raise SystemExit("FAIL: end marker not found in realtime.py")

    old_block = src[start_idx:end_idx]

    new_block = """                    # v0.40.31: SKIP load_markets() entirely.
                    # load_markets() descarga TODOS los mercados del exchange
                    # (3260 para MEXC, 3600 para Binance). Algunas redes/ISPs
                    # bloquean o timeoutean este endpoint masivo, pero permiten
                    # endpoints individuales como /ticker/price y /klines.
                    # Workaround: inyectar manualmente un market stub para
                    # cfg.symbol. Verificamos con fetch_ticker que el símbolo
                    # exista. Si OK, fetch_ohlcv va a funcionar.
                    _effective_exchange = cfg.exchange
                    try:
                        # Verify symbol exists via fetch_ticker (lightweight,
                        # 1 request instead of 3260)
                        _ticker = await poll_exchange.fetch_ticker(cfg.symbol)
                        _ticker_price = _ticker.get('last') or _ticker.get('close')
                        if _ticker_price is None:
                            raise RuntimeError(f"fetch_ticker returned no price for {cfg.symbol}")
                        # Inject minimal market stub
                        if cfg.symbol not in poll_exchange.markets:
                            _base, _quote = cfg.symbol.split('/')
                            poll_exchange.markets[cfg.symbol] = {
                                'id': cfg.symbol.replace('/', ''),
                                'symbol': cfg.symbol,
                                'base': _base,
                                'quote': _quote,
                                'active': True,
                                'type': 'spot',
                                'spot': True,
                                'future': False,
                                'option': False,
                                'contract': False,
                                'precision': {'amount': 8, 'price': 8},
                                'limits': {
                                    'amount': {'min': 0.00000001, 'max': None},
                                    'price': {'min': 0.00000001, 'max': None},
                                    'cost': {'min': 0.00000001, 'max': None},
                                },
                            }
                            poll_exchange.markets_by_id[cfg.symbol.replace('/', '')] = poll_exchange.markets[cfg.symbol]
                        console.print(f"[green]Connected to {cfg.exchange} (REST polling, symbol-stub mode)[/green]")
                        console.print(f"  {cfg.symbol} last price: ${_ticker_price}")
                    except Exception as e_primary:
                        # If fetch_ticker fails too, try ONE fallback to MEXC
                        # (covers the case where Binance is hardcoded but
                        # symbol ticker itself doesn't work)
                        if cfg.exchange.lower() == 'binance':
                            console.print(f"[yellow]Binance ticker failed ({e_primary}). Falling back to MEXC…[/yellow]")
                            try:
                                await poll_exchange.close()
                            except Exception:
                                pass
                            try:
                                mexc_class = ccxt_async.mexc
                                _mexc_config = {'enableRateLimit': True}
                                if cfg.api_key:
                                    _mexc_config['apiKey'] = cfg.api_key
                                if cfg.api_secret:
                                    _mexc_config['secret'] = cfg.api_secret
                                poll_exchange = mexc_class(_mexc_config)
                                _ticker = await poll_exchange.fetch_ticker(cfg.symbol)
                                _ticker_price = _ticker.get('last') or _ticker.get('close')
                                if _ticker_price is None:
                                    raise RuntimeError(f"MEXC fetch_ticker returned no price for {cfg.symbol}")
                                if cfg.symbol not in poll_exchange.markets:
                                    _base, _quote = cfg.symbol.split('/')
                                    poll_exchange.markets[cfg.symbol] = {
                                        'id': cfg.symbol.replace('/', ''),
                                        'symbol': cfg.symbol,
                                        'base': _base,
                                        'quote': _quote,
                                        'active': True,
                                        'type': 'spot',
                                        'spot': True,
                                        'precision': {'amount': 8, 'price': 8},
                                        'limits': {'amount': {'min': 1e-8}, 'price': {'min': 1e-8}, 'cost': {'min': 1e-8}},
                                    }
                                    poll_exchange.markets_by_id[cfg.symbol.replace('/', '')] = poll_exchange.markets[cfg.symbol]
                                _effective_exchange = 'mexc'
                                console.print(f"[green]Connected to MEXC (fallback from Binance) — REST polling, symbol-stub mode[/green]")
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

"""

    src = src.replace(old_block, new_block, 1)
    REALTIME.write_text(src)
    print(f"  [OK] realtime.py: load_markets() → symbol-stub mode (P1)")


def patch_server_symbols_endpoint():
    """Replace /api/market/symbols implementation to use fetch_tickers instead of load_markets."""
    src = SERVER.read_text()

    # Find the get_market_symbols function and replace load_markets with fetch_tickers
    OLD = """        try:
            markets = exc.load_markets()
            # v0.32.5: Filter out leveraged/derivative tokens
            usdt_pairs = []
            for s in markets.keys():
                if not s.endswith("/USDT"):
                    continue
                if not markets[s].get("active", True):
                    continue

                base = s[:-5]  # strip "/USDT"
                # Skip leveraged tokens (MEXC: 1000X, 3L/3S, 5L/5S; Binance: UP/DOWN, BULL/BEAR)
                if base.startswith(("1000", "10000", "1BULL", "3L", "3S", "5L", "5S")):
                    continue
                if base.endswith(("UP", "DOWN", "BULL", "BEAR")) and len(base) > 4:
                    continue
                usdt_pairs.append(s)

            usdt_pairs.sort()

            # v0.32.5: Return up to `limit` symbols (was hard-coded 100)
            return {"ok": True, "exchange": exchange, "symbols": usdt_pairs[:limit],
                    "total_available": len(usdt_pairs)}
        finally:
            if hasattr(exc, 'close'):
                exc.close()"""

    NEW = """        try:
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

    if OLD not in src:
        raise SystemExit("FAIL: server.py OLD block for /api/market/symbols not found (P2)")
    src = src.replace(OLD, NEW, 1)
    SERVER.write_text(src)
    print(f"  [OK] server.py: /api/market/symbols uses fetch_tickers + fallback (P2)")


def bump_version():
    """Bump v0.40.30 → v0.40.31 across all files."""
    files_and_replacements = [
        (INIT_PY, '"0.40.30"', '"0.40.31"'),
        (PYPROJECT, '"0.40.30"', '"0.40.31"'),
        (PYPROJECT, '= "0.40.30"', '= "0.40.31"'),
        (CLI_MAIN, 'v0.40.30', 'v0.40.31'),
        (SERVER, 'version="0.40.30"', 'version="0.40.31"'),
        (HTML, 'v0.40.30', 'v0.40.31'),
    ]
    for path, old, new in files_and_replacements:
        if not path.exists():
            print(f"  [skip] {path.name}: not found")
            continue
        txt = path.read_text()
        n = txt.count(old)
        if n == 0:
            print(f"  [skip] {path.name}: no '{old}' found")
            continue
        path.write_text(txt.replace(old, new))
        print(f"  [OK] {path.name}: {n}x '{old}'→'{new}' (P3)")


def main():
    print("v0.40.31 — Skip load_markets, inject symbol stub")
    print()
    patch_realtime()
    patch_server_symbols_endpoint()
    bump_version()
    print()
    print("All patches applied. Next: syntax check + smoke test.")


if __name__ == "__main__":
    main()
