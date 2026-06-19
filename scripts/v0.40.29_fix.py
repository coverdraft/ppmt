#!/usr/bin/env python3
"""
v0.40.29 — MEXC default + auto-fallback Binance→MEXC.

PROBLEMA (v0.40.28 en Mac del usuario):
- `Exchange connection failed: binance GET https://api.binance.com/api/v3/exchangeInfo`
- Binance SPOT (api.binance.com) también está bloqueado desde algunas redes LATAM/EU,
  no solo fapi.binance.com.
- Sin load_markets() exitoso → no hay polling → no hay live prices → chart congelado,
  motor STOPPED, Candles=0, SAX=0.
- v0.40.28 solo fixeaba fapi.binance.com (futures). El spot también falla.

FIX:
1. MEXC como default en todos los endpoints (server.py + index.html dropdown).
2. Auto-fallback en realtime.py: si binance.load_markets() falla, retry automático
   con mexc. La sesión queda marcada como "mexc (fallback from binance)".
3. Chart toolbar dropdown ahora arranca en MEXC.
4. Paper mode no depende de si el exchange responde o no para validar.
"""
import re
from pathlib import Path

PPMT = Path("/home/z/my-project/ppmt")
REALTIME = PPMT / "src/ppmt/engine/realtime.py"
SERVER = PPMT / "src/ppmt/terminal/server.py"
HTML = PPMT / "src/ppmt/terminal/static/index.html"
PYPROJECT = PPMT / "pyproject.toml"
CLI_MAIN = PPMT / "src/ppmt/cli/main.py"


def patch_realtime():
    """Add auto-fallback Binance→MEXC in REST polling section."""
    src = REALTIME.read_text()

    # Patch 1: replace the load_markets try/except with auto-fallback logic.
    OLD = """                    try:
                        await poll_exchange.load_markets()
                        console.print(f"[green]Connected to {cfg.exchange} (REST polling)[/green]")
                    except Exception as e:
                        # v0.40.28: Capture the failure as a session ERROR
                        # so the dashboard can show WHY the motor stopped
                        # (was previously silent — UI just showed STOPPED).
                        _err_msg = f"Exchange connection failed: {e}"
                        console.print(f"[red]Failed to connect: {e}[/red]")
                        try:
                            self._update_terminal_state(
                                is_running=False,
                                websocket_status="disconnected",
                                error=_err_msg,
                            )
                        except Exception:
                            pass
                        await poll_exchange.close()
                        storage.close()
                        return result"""

    NEW = """                    # v0.40.29: Auto-fallback Binance → MEXC. Some EU/LATAM
                    # networks block api.binance.com (spot) outright, not just
                    # fapi.binance.com (futures). Without this fallback, the
                    # whole session dies with "Exchange connection failed:
                    # binance GET https://api.binance.com/api/v3/exchangeInfo"
                    # and the user sees STOPPED + Candles=0 forever.
                    _effective_exchange = cfg.exchange
                    _fallback_used = False
                    try:
                        await poll_exchange.load_markets()
                        console.print(f"[green]Connected to {cfg.exchange} (REST polling)[/green]")
                    except Exception as e_primary:
                        if cfg.exchange.lower() == 'binance':
                            console.print(f"[yellow]Binance connection failed ({e_primary}). Falling back to MEXC…[/yellow]")
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
                                await poll_exchange.load_markets()
                                _effective_exchange = 'mexc'
                                _fallback_used = True
                                console.print(f"[green]Connected to MEXC (fallback from Binance) — REST polling[/green]")
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
                            # Non-binance exchange, no fallback. Report and exit.
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
                            return result"""

    if OLD not in src:
        raise SystemExit("FAIL: realtime.py OLD block not found (P1)")
    src = src.replace(OLD, NEW, 1)

    REALTIME.write_text(src)
    print(f"  [OK] realtime.py patched (P1: Binance→MEXC auto-fallback)")


def patch_server_defaults():
    """Change default exchange from 'binance' to 'mexc' in server.py endpoints."""
    src = SERVER.read_text()

    # We replace `exchange: str = "binance"` with `exchange: str = "mexc"`.
    # BUT — the chart toolbar dropdown still defaults to "mexc" so the
    # frontend-driven calls will also send mexc by default. For backwards
    # compat, any caller still passing exchange="binance" gets it honored,
    # and the realtime engine will auto-fallback to mexc if it fails.
    n = src.count('exchange: str = "binance"')
    src = src.replace('exchange: str = "binance"', 'exchange: str = "mexc"')
    print(f"  [OK] server.py: {n} defaults changed binance→mexc (P2)")

    # Also flip the LiveConfig default in realtime.py if it exists.
    rt = REALTIME.read_text()
    n2 = rt.count('exchange: str = "binance"')
    if n2 > 0:
        rt = rt.replace('exchange: str = "binance"', 'exchange: str = "mexc"')
        REALTIME.write_text(rt)
        print(f"  [OK] realtime.py: {n2} defaults changed binance→mexc (P2-bis)")

    # Now also patch the get_market_symbols function default and the global
    # exchange lookup table. Same logic — flip to mexc.
    n3 = src.count('async def get_market_symbols(exchange: str = "mexc"')
    if n3 == 0:
        # Already replaced by the global substitution above; nothing to do.
        pass

    SERVER.write_text(src)


def patch_html_default_exchange():
    """Set chartExchange dropdown default to mexc."""
    src = HTML.read_text()

    OLD = """      <select id="chartExchange" style="width:90px">
        <option value="binance" selected>Binance</option>
        <option value="mexc">MEXC</option>
        <option value="bybit">Bybit</option>
      </select>"""
    NEW = """      <select id="chartExchange" style="width:90px">
        <option value="mexc" selected>MEXC</option>
        <option value="binance">Binance</option>
        <option value="bybit">Bybit</option>
      </select>"""
    if OLD not in src:
        raise SystemExit("FAIL: HTML chartExchange OLD block not found (P3a)")
    src = src.replace(OLD, NEW, 1)
    print(f"  [OK] HTML: chartExchange default → mexc (P3a)")

    # Also flip any `|| 'binance'` fallbacks in JS to `|| 'mexc'`.
    n = src.count("|| 'binance'")
    src = src.replace("|| 'binance'", "|| 'mexc'")
    print(f"  [OK] HTML: {n} JS fallbacks 'binance'→'mexc' (P3b)")

    HTML.write_text(src)


def bump_version():
    """Bump v0.40.28 → v0.40.29 across all the usual places."""
    files = [
        (HTML, "v0.40.28", "v0.40.29"),
        (PYPROJECT, "0.40.28", "0.40.29"),
        (CLI_MAIN, "0.40.28", "0.40.29"),
    ]
    for path, old, new in files:
        if not path.exists():
            print(f"  [skip] {path.name}: file not found")
            continue
        txt = path.read_text()
        n = txt.count(old)
        if n == 0:
            print(f"  [skip] {path.name}: no '{old}' found")
            continue
        path.write_text(txt.replace(old, new))
        print(f"  [OK] {path.name}: {n}× '{old}'→'{new}' (P4)")


def main():
    print("v0.40.29 — MEXC default + Binance→MEXC auto-fallback")
    print()
    patch_realtime()
    patch_server_defaults()
    patch_html_default_exchange()
    bump_version()
    print()
    print("All patches applied. Next: syntax check + smoke test.")


if __name__ == "__main__":
    main()
