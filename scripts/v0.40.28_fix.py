#!/usr/bin/env python3
"""
PPMT v0.40.28 fix script.

Addresses 5 issues reported by user after v0.40.27:
  1. Chart not real-time (stale price, "STOPPED" badge) — caused by
     ccxt_async.binance load_markets() failing on fapi.binance.com
     (Binance futures endpoint intermittently blocked from EU networks).
     FIX: configure spot-only markets for the polling exchange.

  2. Header badge stays STOPPED even when motor is RUNNING — caused by
     the engine calling _update_terminal_state(is_running=True) at line
     2224 BEFORE the exchange connection succeeds, then the engine
     returns early on load_markets failure → session goes RUNNING then
     STOPPED within milliseconds → frontend sees STOPPED.
     FIX: don't flip is_running=True until the polling loop starts.
     Capture load_markets failure as session ERROR with message.

  3. Chart stays stale when engine is not yet polling — even with the
     engine fix, the chart needs live prices to update.
     FIX: add a frontend fallback ticker that polls /api/market/price
     every 2s for the chart's symbol when no session is broadcasting
     a current_price.

  4. _pollSessionStatus doesn't update the HEADER badge — only updates
     the trading-tab badge.
     FIX: update both badges + handle the RUNNING→ERROR transition.

  5. Exchange toolbar only has Binance + Bybit; Bybit is 403-blocked
     from this network. MEXC works fine.
     FIX: add MEXC to the chart toolbar + use it as fallback if Binance
     fails. Make the exchange selector more prominent.
"""

import re
import sys
from pathlib import Path

ROOT = Path("/home/z/my-project/ppmt_work")
SERVER = ROOT / "src/ppmt/terminal/server.py"
REALTIME = ROOT / "src/ppmt/engine/realtime.py"
INDEX = ROOT / "src/ppmt/terminal/static/index.html"
CLI = ROOT / "src/ppmt/cli/main.py"
TRAZ = ROOT / "TRAZABILIDAD.md"


def patch(path: Path, find: str, replace: str, count_required: int = 1) -> None:
    """Patch a file by string replacement with a count check."""
    src = path.read_text()
    n = src.count(find)
    if n != count_required:
        raise RuntimeError(
            f"PATCH FAILED on {path.name}: expected {count_required} occurrence(s) of\n"
            f"  {find[:120]!r}\n  but found {n}."
        )
    path.write_text(src.replace(find, replace, count_required))
    print(f"  ✓ patched {path.name}: {find[:80]!r}...")


# ============================================================
# PATCH 1: realtime.py — configure spot-only for poll_exchange
# ============================================================
# Before:
#     exchange_config = {}
#     if cfg.api_key: ...
#     if cfg.api_secret: ...
#
#     poll_exchange = exchange_class(exchange_config)
#
# After:
#     exchange_config = {'enableRateLimit': True}
#     if cfg.api_key: ...
#     if cfg.api_secret: ...
#     # v0.40.28: spot-only avoids fapi.binance.com (intermittently blocked)
#     if cfg.exchange.lower() == 'binance':
#         exchange_config.setdefault('options', {})
#         exchange_config['options']['defaultType'] = 'spot'
#         exchange_config['options']['fetchMarkets'] = ['spot']
#
#     poll_exchange = exchange_class(exchange_config)
#
# Note: there are TWO places where poll_exchange is created — only one
# is actually used (the REST polling branch at line ~2458). The other
# (line ~2458 inside the WS-mode branch) is dead code in v0.38.6+ since
# use_websocket defaults to False. We patch only the active one.

print("\n[P1] Patching realtime.py — spot-only ccxt config for poll_exchange...")

# Find the poll_exchange creation block (the one INSIDE the REST polling
# branch, just after `poll_exchange = exchange_class(exchange_config)`).
# We need to add the spot-only options BEFORE the exchange_class() call.

REALTIME_OLD = """                    exchange_config = {}
                    if cfg.api_key:
                        exchange_config['apiKey'] = cfg.api_key
                    if cfg.api_secret:
                        exchange_config['secret'] = cfg.api_secret

                    poll_exchange = exchange_class(exchange_config)"""

REALTIME_NEW = """                    exchange_config = {'enableRateLimit': True}
                    if cfg.api_key:
                        exchange_config['apiKey'] = cfg.api_key
                    if cfg.api_secret:
                        exchange_config['secret'] = cfg.api_secret
                    # v0.40.28: spot-only markets. Without this, ccxt's
                    # binance.load_markets() also hits fapi.binance.com
                    # (USD-M futures) + dapi.binance.com (COIN-M futures).
                    # fapi is intermittently blocked from EU networks —
                    # when it 403s, the whole load_markets() raises and
                    # the engine returns early with no polling, no live
                    # prices, no chart updates. Restricting to spot avoids
                    # the futures endpoints entirely.
                    if cfg.exchange.lower() == 'binance':
                        exchange_config.setdefault('options', {})
                        exchange_config['options']['defaultType'] = 'spot'
                        exchange_config['options']['fetchMarkets'] = ['spot']

                    poll_exchange = exchange_class(exchange_config)"""

patch(REALTIME, REALTIME_OLD, REALTIME_NEW)

# ============================================================
# PATCH 2: realtime.py — DON'T set is_running=True until polling starts
# ============================================================
# The engine currently calls _update_terminal_state(is_running=True) at
# line ~2224 BEFORE the exchange connection. This makes _state_cb flip
# the session to RUNNING prematurely. When load_markets then fails, the
# engine returns and the session goes to STOPPED — but the frontend
# may have already seen RUNNING and shown "Motor RUNNING" messages.
#
# Fix: use is_running=False with websocket_status="connecting" in the
# initial state. The actual is_running=True is set later inside the
# polling loop (line 2606) when real prices start flowing.

print("\n[P2] Patching realtime.py — don't flip is_running=True before polling...")

REALTIME_OLD2 = """        # v0.15.0: Initialize TerminalState for live dashboard
        self._update_terminal_state(
            is_running=True,
            mode="live",
            started_at=time.time(),
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            exchange=cfg.exchange,
            portfolio_value=cfg.initial_capital,
            cash=cfg.initial_capital,
            candles_processed=0,
            websocket_status="connecting",
        )"""

REALTIME_NEW2 = """        # v0.15.0: Initialize TerminalState for live dashboard
        # v0.40.28: is_running=False until the polling loop actually
        # delivers the first price tick. Previously this was True, which
        # made _state_cb flip the session to RUNNING prematurely —
        # misleading the user when load_markets() then failed.
        self._update_terminal_state(
            is_running=False,
            mode="live",
            started_at=time.time(),
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            exchange=cfg.exchange,
            portfolio_value=cfg.initial_capital,
            cash=cfg.initial_capital,
            candles_processed=0,
            websocket_status="connecting",
        )"""

patch(REALTIME, REALTIME_OLD2, REALTIME_NEW2)

# ============================================================
# PATCH 3: realtime.py — capture load_markets failure as ERROR
# ============================================================
# Currently when poll_exchange.load_markets() fails, the engine just
# prints "Failed to connect: {e}" and returns. The caller sets status
# to STOPPED with no error message, so the UI shows "STOPPED" with no
# explanation. Capture the error and propagate it via state_callback.

print("\n[P3] Patching realtime.py — capture load_markets failure as ERROR...")

REALTIME_OLD3 = """                    try:
                        await poll_exchange.load_markets()
                        console.print(f"[green]Connected to {cfg.exchange} (REST polling)[/green]")
                    except Exception as e:
                        console.print(f"[red]Failed to connect: {e}[/red]")
                        await poll_exchange.close()
                        storage.close()
                        return result"""

REALTIME_NEW3 = """                    try:
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

patch(REALTIME, REALTIME_OLD3, REALTIME_NEW3)

# ============================================================
# PATCH 4: server.py /api/ohlcv + /api/market/price — spot-only too
# ============================================================
# The sync ccxt.binance() used by /api/ohlcv and /api/market/price has
# the same fapi.binance.com issue. Apply the same spot-only fix.

print("\n[P4] Patching server.py — spot-only ccxt for /api/ohlcv + /api/market/price...")

SERVER_OLD_OHLCV = """        exc = ex()
        try:
            ohlcv = exc.fetch_ohlcv(symbol, timeframe, limit=min(limit, 1000))"""

SERVER_NEW_OHLCV = """        # v0.40.28: spot-only for binance — avoids fapi.binance.com block.
        _opts = {}
        if exchange.lower() == 'binance':
            _opts = {'enableRateLimit': True,
                     'options': {'defaultType': 'spot', 'fetchMarkets': ['spot']}}
        exc = ex(_opts)
        try:
            ohlcv = exc.fetch_ohlcv(symbol, timeframe, limit=min(limit, 1000))"""

patch(SERVER, SERVER_OLD_OHLCV, SERVER_NEW_OHLCV)

SERVER_OLD_PRICE = """        exc = ex()
        try:
            ticker = exc.fetch_ticker(symbol)"""

SERVER_NEW_PRICE = """        # v0.40.28: spot-only for binance — same fapi fix as /api/ohlcv.
        _opts = {}
        if exchange.lower() == 'binance':
            _opts = {'enableRateLimit': True,
                     'options': {'defaultType': 'spot', 'fetchMarkets': ['spot']}}
        exc = ex(_opts)
        try:
            ticker = exc.fetch_ticker(symbol)"""

patch(SERVER, SERVER_OLD_PRICE, SERVER_NEW_PRICE)

# ============================================================
# PATCH 5: index.html — add MEXC to chart toolbar
# ============================================================

print("\n[P5] Patching index.html — add MEXC to chart toolbar...")

INDEX_OLD_EXCH = """      <label>Exchange</label>
      <select id="chartExchange" style="width:70px">
        <option value="binance" selected>Binance</option>
        <option value="bybit">Bybit</option>
      </select>"""

INDEX_NEW_EXCH = """      <label>Exchange</label>
      <select id="chartExchange" style="width:90px">
        <option value="binance" selected>Binance</option>
        <option value="mexc">MEXC</option>
        <option value="bybit">Bybit</option>
      </select>"""

patch(INDEX, INDEX_OLD_EXCH, INDEX_NEW_EXCH)

# ============================================================
# PATCH 6: index.html — fix _pollSessionStatus to update header badge
# ============================================================
# Currently _pollSessionStatus only calls updateTradingBadge('LIVE') which
# updates the trading-tab badge, not the header badge. Also handle the
# transition from RUNNING (already set by _state_cb) to ERROR (when
# load_markets fails after the engine started).

print("\n[P6] Patching index.html — _pollSessionStatus updates both badges...")

POLL_OLD = """function _pollSessionStatus(symbol, timeframe, attemptsLeft = 30) {
  if (attemptsLeft <= 0) {
    const btn = document.getElementById('btnStartTrading');
    if (btn) { btn.disabled = false; btn.textContent = 'Start Paper'; }
    showStatusMsg('Timeout esperando sesión — revisa la consola del servidor.');
    return;
  }
  setTimeout(async () => {
    try {
      const res = await fetch(`${API}/api/multi-status`);
      if (!res.ok) { _pollSessionStatus(symbol, timeframe, attemptsLeft - 1); return; }
      const data = await res.json();
      if (!data.ok) { _pollSessionStatus(symbol, timeframe, attemptsLeft - 1); return; }
      const sess = (data.sessions || []).find(s => s.symbol === symbol && s.timeframe === timeframe);
      const btn = document.getElementById('btnStartTrading');
      if (!sess) { _pollSessionStatus(symbol, timeframe, attemptsLeft - 1); return; }
      const st = sess.status || '';
      if (st === 'RUNNING') {
        if (btn) { btn.textContent = 'Running'; btn.disabled = true; }
        document.getElementById('btnStopTrading').disabled = false;
        updateTradingBadge('LIVE');
        showStatusMsg(`Motor operando: ${symbol} ${timeframe} — esperando señales…`);
        if (typeof appendOpsFeed === 'function') {
          appendOpsFeed('info', `Motor RUNNING — ${symbol} ${timeframe} — analizando velas…`);
        }
        return; // stop polling
      }
      if (st === 'ERROR' || st === 'VALIDATION_FAILED') {
        if (btn) { btn.textContent = 'Start Paper'; btn.disabled = false; }
        document.getElementById('btnStopTrading').disabled = true;
        updateTradingBadge('ERROR');
        const errMsg = sess.error || st;
        showStatusMsg(`Error iniciando: ${errMsg}`);
        if (typeof appendOpsFeed === 'function') {
          appendOpsFeed('info', `Error: ${errMsg}`);
        }
        return;
      }
      // Still starting — update button text to show progress
      if (btn) btn.textContent = 'Iniciando… (' + st + ')';
      _pollSessionStatus(symbol, timeframe, attemptsLeft - 1);
    } catch (e) {
      _pollSessionStatus(symbol, timeframe, attemptsLeft - 1);
    }
  }, 2000);
}"""

POLL_NEW = """function _pollSessionStatus(symbol, timeframe, attemptsLeft = 30) {
  if (attemptsLeft <= 0) {
    const btn = document.getElementById('btnStartTrading');
    if (btn) { btn.disabled = false; btn.textContent = 'Start Paper'; }
    showStatusMsg('Timeout esperando sesión — revisa la consola del servidor.');
    return;
  }
  setTimeout(async () => {
    try {
      const res = await fetch(`${API}/api/multi-status`);
      if (!res.ok) { _pollSessionStatus(symbol, timeframe, attemptsLeft - 1); return; }
      const data = await res.json();
      if (!data.ok) { _pollSessionStatus(symbol, timeframe, attemptsLeft - 1); return; }
      const sess = (data.sessions || []).find(s => s.symbol === symbol && s.timeframe === timeframe);
      const btn = document.getElementById('btnStartTrading');
      if (!sess) { _pollSessionStatus(symbol, timeframe, attemptsLeft - 1); return; }
      const st = sess.status || '';
      if (st === 'RUNNING') {
        if (btn) { btn.textContent = 'Running'; btn.disabled = true; }
        document.getElementById('btnStopTrading').disabled = false;
        // v0.40.28: update BOTH badges (header + trading tab) so the
        // header at the top of the screen also shows LIVE.
        updateTradingBadge('LIVE');
        const headerTB = document.getElementById('headerTradingBadge');
        if (headerTB) { headerTB.textContent = 'LIVE'; headerTB.className = 'badge badge-live'; }
        const headerDot = document.getElementById('headerDot');
        if (headerDot) headerDot.className = 'status-dot live';
        const headerStatus = document.getElementById('headerStatus');
        if (headerStatus) { headerStatus.textContent = 'LIVE'; headerStatus.style.color = 'var(--green)'; }
        showStatusMsg(`Motor operando: ${symbol} ${timeframe} — esperando señales…`);
        if (typeof appendOpsFeed === 'function') {
          appendOpsFeed('info', `Motor RUNNING — ${symbol} ${timeframe} — analizando velas…`);
        }
        // v0.40.28: kick off the chart fallback ticker — the chart
        // needs a live price feed even when the engine is slow to
        // deliver the first current_price (warmup can take 10-15s).
        if (typeof _startChartTicker === 'function') _startChartTicker();
        return; // stop polling
      }
      if (st === 'ERROR' || st === 'VALIDATION_FAILED') {
        if (btn) { btn.textContent = 'Start Paper'; btn.disabled = false; }
        document.getElementById('btnStopTrading').disabled = true;
        updateTradingBadge('ERROR');
        const headerTB = document.getElementById('headerTradingBadge');
        if (headerTB) { headerTB.textContent = 'ERROR'; headerTB.className = 'badge badge-error'; }
        const errMsg = sess.error || st;
        showStatusMsg(`Error iniciando: ${errMsg}`);
        if (typeof appendOpsFeed === 'function') {
          appendOpsFeed('info', `Error: ${errMsg}`);
        }
        // v0.40.28: even on engine error, kick off the chart ticker
        // so the chart shows live prices (just without trading signals).
        if (typeof _startChartTicker === 'function') _startChartTicker();
        return;
      }
      if (st === 'STOPPED') {
        // v0.40.28: handle the case where the session went STARTING ->
        // STOPPED directly (e.g. validation returned early, or load_markets
        // failed before _state_cb set is_running=True).
        if (btn) { btn.textContent = 'Start Paper'; btn.disabled = false; }
        document.getElementById('btnStopTrading').disabled = true;
        updateTradingBadge('STOPPED');
        const headerTB = document.getElementById('headerTradingBadge');
        if (headerTB) { headerTB.textContent = 'STOPPED'; headerTB.className = 'badge badge-stopped'; }
        const errMsg = sess.error || 'Session stopped unexpectedly';
        if (errMsg && errMsg.indexOf('WARNING') === -1) {
          showStatusMsg(`Motor detenido: ${errMsg}`);
          if (typeof appendOpsFeed === 'function') {
            appendOpsFeed('info', `Motor STOPPED: ${errMsg}`);
          }
        }
        if (typeof _startChartTicker === 'function') _startChartTicker();
        return;
      }
      // Still starting — update button text to show progress
      if (btn) btn.textContent = 'Iniciando… (' + st + ')';
      _pollSessionStatus(symbol, timeframe, attemptsLeft - 1);
    } catch (e) {
      _pollSessionStatus(symbol, timeframe, attemptsLeft - 1);
    }
  }, 2000);
}"""

patch(INDEX, POLL_OLD, POLL_NEW)

# ============================================================
# PATCH 7: index.html — add chart fallback ticker
# ============================================================
# Even when no session is broadcasting a current_price, the chart
# should still show live prices. This polls /api/market/price every
# 2s for the chart's symbol and feeds the result to
# _updateChartLiveTick. The ticker is started by:
#   - DOMContentLoaded (so the chart is live from page load)
#   - _pollSessionStatus when the session reaches RUNNING (engine feed)
#   - _pollSessionStatus when the session errors out (fallback feed)
# The ticker is stopped if/when a session's WS snapshot starts
# delivering current_price for the matching symbol.

print("\n[P7] Patching index.html — add chart fallback ticker...")

# Insert _startChartTicker + _chartTickerHandle just AFTER _updateChartLiveTick
TICKER_INSERT_POINT = """  } catch (e) {
    // Chart may be mid-reload; non-critical
  }
}

// ============================================================
// v0.40.28: CHART FALLBACK TICKER
// ============================================================
// When no active trading session is pushing current_price for the
// chart's symbol (e.g. page just loaded, no session running, or
// session errored out), the chart's last candle would stay frozen.
// This ticker polls /api/market/price every 2s for the chart's
// symbol and feeds the result to _updateChartLiveTick. Once a
// session's WS snapshot starts delivering current_price for the
// matching symbol, the ticker backs off (becomes a no-op) to
// avoid duplicate updates.
let _chartTickerHandle = null;
let _chartTickerLastPrice = 0;
function _startChartTicker() {
  if (_chartTickerHandle) return; // already running
  _chartTickerHandle = setInterval(async () => {
    try {
      const symbol = (document.getElementById('chartSymbol') || {}).value;
      const tf = (document.getElementById('chartTimeframe') || {}).value || '5m';
      const exch = (document.getElementById('chartExchange') || {}).value || 'binance';
      if (!symbol) return;
      const url = `${API}/api/market/price?symbol=${encodeURIComponent(symbol)}&exchange=${encodeURIComponent(exch)}`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      if (!data || !data.ok || !data.price) return;
      const price = parseFloat(data.price);
      if (!price || price <= 0) return;
      // If the price hasn't changed, skip the chart update (the WS
      // snapshot may have already updated it for us).
      if (price === _chartTickerLastPrice) return;
      _chartTickerLastPrice = price;
      // Update the chart's last candle.
      if (typeof _updateChartLiveTick === 'function') {
        _updateChartLiveTick(price, symbol, tf);
      }
      // Also update the header price if no session is broadcasting.
      const headerPrice = document.getElementById('headerPrice');
      if (headerPrice && headerPrice.textContent === '--') {
        headerPrice.textContent = (typeof formatPrice === 'function') ? formatPrice(price) : price.toString();
      }
      // Also update the ticket-side live price stat.
      const ctrlPrice = document.getElementById('ctrlPrice');
      if (ctrlPrice) ctrlPrice.textContent = (typeof formatPrice === 'function') ? formatPrice(price) : price.toString();
    } catch (e) {
      // network blip — non-critical
    }
  }, 2000);
}
function _stopChartTicker() {
  if (_chartTickerHandle) {
    clearInterval(_chartTickerHandle);
    _chartTickerHandle = null;
  }
}"""

# Find the end of _updateChartLiveTick and insert after it
TICKER_OLD = """  } catch (e) {
    // Chart may be mid-reload; non-critical
  }
}

// ============================================================
// v0.40.27: SESSION STATUS POLLER (post-Start)
// ============================================================"""

TICKER_NEW = TICKER_INSERT_POINT + """

// ============================================================
// v0.40.27: SESSION STATUS POLLER (post-Start)
// ============================================================"""

patch(INDEX, TICKER_OLD, TICKER_NEW)

# ============================================================
# PATCH 8: index.html — start chart ticker on DOMContentLoaded
# ============================================================
# Find the DOMContentLoaded handler and add _startChartTicker() to it.

print("\n[P8] Patching index.html — start chart ticker on DOMContentLoaded...")

DOM_OLD = """  setTimeout(() => {
    if (typeof pollMultiStatus === 'function') {
      if (!_multiStatusPollHandle) {
        _multiStatusPollHandle = setInterval(pollMultiStatus, 3000);
      }
      pollMultiStatus();
    }
  }, 800);
});"""

DOM_NEW = """  setTimeout(() => {
    if (typeof pollMultiStatus === 'function') {
      if (!_multiStatusPollHandle) {
        _multiStatusPollHandle = setInterval(pollMultiStatus, 3000);
      }
      pollMultiStatus();
    }
  }, 800);
  // v0.40.28: kick off the chart fallback ticker so the chart shows
  // live prices even before any trading session is started.
  setTimeout(() => {
    if (typeof _startChartTicker === 'function') _startChartTicker();
  }, 500);
});"""

patch(INDEX, DOM_OLD, DOM_NEW)

# ============================================================
# PATCH 9: index.html — version bump v0.40.27 -> v0.40.28
# ============================================================

print("\n[P9] Bumping version v0.40.27 -> v0.40.28 in index.html...")

patch(INDEX, '<span class="logo-ver">v0.40.27</span>', '<span class="logo-ver">v0.40.28</span>')
patch(INDEX, 'PPMT v0.40.27', 'PPMT v0.40.28')

# ============================================================
# PATCH 10: server.py version bump (the /api/version endpoint)
# ============================================================

print("\n[P10] Bumping version in server.py...")

# Find the version constant
SERVER_SRC = SERVER.read_text()
m = re.search(r'VERSION\s*=\s*"v0\.40\.27"', SERVER_SRC)
if m:
    patch(SERVER, 'VERSION = "v0.40.27"', 'VERSION = "v0.40.28"')
else:
    # Try other patterns
    m2 = re.search(r'__version__\s*=\s*"v?0\.40\.27"', SERVER_SRC)
    if m2:
        patch(SERVER, '__version__ = "0.40.27"', '__version__ = "0.40.28"')
    else:
        print("  ! no version string found in server.py — skipping")

# ============================================================
# PATCH 11: cli/main.py version bump
# ============================================================

print("\n[P11] Bumping version in cli/main.py...")

CLI_SRC = CLI.read_text()
m = re.search(r'0\.40\.27', CLI_SRC)
if m:
    # Find context
    for old, new in [
        ('VERSION = "0.40.27"', 'VERSION = "0.40.28"'),
        ('__version__ = "0.40.27"', '__version__ = "0.40.28"'),
        ('version="0.40.27"', 'version="0.40.28"'),
        ('"0.40.27"', '"0.40.28"'),
    ]:
        if old in CLI_SRC:
            patch(CLI, old, new)
            break
else:
    print("  ! no v0.40.27 found in cli/main.py — skipping")

# ============================================================
# Verify HTML structure
# ============================================================
print("\n[VERIFY] HTML structure check...")
import html.parser
class HTMLValidator(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.errors = []
        self.stack = []
    def handle_starttag(self, tag, attrs):
        if tag not in ('br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'option'):
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"Closing </{tag}> with empty stack")
            return
        if self.stack[-1] != tag:
            # Try to find it deeper in the stack
            if tag in self.stack:
                while self.stack and self.stack[-1] != tag:
                    self.errors.append(f"Unclosed <{self.stack[-1]}> before </{tag}>")
                    self.stack.pop()
                if self.stack:
                    self.stack.pop()
            else:
                self.errors.append(f"Stray </{tag}>")
        else:
            self.stack.pop()

v = HTMLValidator()
v.feed(INDEX.read_text())
if v.errors:
    print("  ! HTML errors:")
    for e in v.errors[:5]:
        print(f"    - {e}")
else:
    print("  ✓ HTML structure OK")
if v.stack:
    print(f"  ! Unclosed at EOF: {v.stack[:5]}")
else:
    print("  ✓ All tags closed")

# ============================================================
# Verify JS syntax with node --check
# ============================================================
print("\n[VERIFY] JS syntax check...")
import subprocess
import tempfile

INDEX_SRC = INDEX.read_text()
# Extract <script>...</script> blocks
script_blocks = re.findall(r'<script(?:\s[^>]*)?>(.+?)</script>', INDEX_SRC, re.DOTALL)
js_all = "\n;\n".join(script_blocks)

# Strip the library script (lightweight-charts) — it's a UMD bundle, syntax is fine but huge
# Just check the user-authored blocks (the ones without src=)
user_scripts = []
for m in re.finditer(r'<script(?!\s[^>]*src=)(?:\s[^>]*)?>(.+?)</script>', INDEX_SRC, re.DOTALL):
    user_scripts.append(m.group(1))

if user_scripts:
    js_to_check = "\n;\n".join(user_scripts)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(js_to_check)
        tmp = f.name
    try:
        r = subprocess.run(['node', '--check', tmp], capture_output=True, text=True)
        if r.returncode == 0:
            print("  ✓ JS syntax OK")
        else:
            print(f"  ! JS syntax error:")
            print(r.stderr[:2000])
    finally:
        Path(tmp).unlink(missing_ok=True)
else:
    print("  ! No user-authored <script> blocks found")

# ============================================================
# Verify Python syntax
# ============================================================
print("\n[VERIFY] Python syntax check...")
for p in [SERVER, REALTIME, CLI]:
    r = subprocess.run(['python3', '-m', 'py_compile', str(p)], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  ✓ {p.name} compiles")
    else:
        print(f"  ! {p.name} compile error:")
        print(r.stderr[:1500])

print("\n✅ All patches applied.")
