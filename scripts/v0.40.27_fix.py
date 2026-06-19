#!/usr/bin/env python3
"""
v0.40.27 — Comprehensive Trading tab fix (rewritten with cleaner helpers).

Root causes:
  1. CRITICAL: Top-level script crashes at line 3265/3277 because
     `setupSymbol`/`setupTimeframe` were deleted in v0.40.25 but JS still
     calls `.addEventListener` on them. TypeError aborts all top-level
     code after that point (polling timers, DOMContentLoaded setup).
  2. startPaperTrading() calls OLD /api/start-trading with pre-trade
     validation gate that blocks paper trading for tokens that haven't
     passed WR/PF/RoR checks. NEW /api/multi-start bypasses the gate in
     paper mode.
  3. stopTrading() calls OLD /api/stop-trading which doesn't stop
     multi-token sessions.
  4. Chart is static — loadChart() runs once on symbol/TF change but
     nothing live-updates the last candle. WS pushes current_price every
     1s; we hook into updateDashboard() to live-update the chart.
  5. Capital allocation is invisible. User asks "quien decide que %
     por operacion?" — PPMT uses Quarter-Kelly × confidence × regime ×
     vol × drawdown, capped at 25% of equity. We add a visible panel.
  6. Operations feed never populates — WS signal/trade events aren't
     wired to appendOpsFeed().
  7. "Iniciando..." stays forever — script crash kills DOMContentLoaded
     polling kickoff + Start handler doesn't poll for session start.

This rewrite uses safe Python string operations (not giant regexes) —
we find function bodies by walking brace depth, and patch via simple
string replace. Much less fragile than the previous regex approach.
"""
import sys
from pathlib import Path

HTML = Path("/home/z/my-project/ppmt_work/src/ppmt/terminal/static/index.html")
src = HTML.read_text()
orig_len = len(src)


def find_function_body(src, name):
    """Find the body {…} of `function NAME(...)` or `async function NAME(...)`.
    Returns (start, end_inclusive) byte offsets of the OUTER {...} block.
    """
    # Try both `function NAME(` and `async function NAME(`
    for prefix in (f"async function {name}(", f"function {name}("):
        idx = src.find(prefix)
        if idx < 0:
            continue
        # Find opening brace
        depth = 0
        i = idx
        while i < len(src) and src[i] != '{':
            i += 1
        if i >= len(src):
            continue
        start = i
        # Walk to matching close brace
        depth = 0
        while i < len(src):
            c = src[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return (start, i)
            i += 1
    return None


def replace_function(src, name, new_body):
    """Replace `function NAME(...) { ... }` (entire function) with new_body.
    new_body should include `function NAME(...) { ... }` itself.
    """
    spans = find_function_body(src, name)
    if not spans:
        print(f"  ✗ replace_function {name}: function not found")
        sys.exit(1)
    # Find the start of the function declaration (before `function` keyword)
    start, end = spans
    # Walk back to find `async function` or `function` keyword
    pre = src.rfind("function ", 0, start)
    if pre < 0:
        print(f"  ✗ replace_function {name}: 'function ' keyword not found before brace")
        sys.exit(1)
    # Check if preceded by 'async '
    if pre >= 6 and src[pre-6:pre] == "async ":
        pre -= 6
    src = src[:pre] + new_body + src[end+1:]
    print(f"  ✓ replace_function {name}: replaced ({end-pre+1} → {len(new_body)} chars)")
    return src


def patch(label, old, new, count=1):
    """Simple string replace with count check."""
    global src
    n = src.count(old)
    if n != count:
        print(f"  ✗ {label}: expected {count} match(es), found {n}")
        sys.exit(1)
    src = src.replace(old, new)
    print(f"  ✓ {label}: {count} patch(es) applied")


# ---------------------------------------------------------------------------
# P1: Null-guard setupSymbol/setupTimeframe addEventListener calls
# ---------------------------------------------------------------------------
print("\n[P1] Null-guard setupSymbol/setupTimeframe listeners...")
old_block = """document.getElementById('setupSymbol').addEventListener('change', function() {
  const chart = document.getElementById('chartSymbol');
  if (chart.value !== this.value) {
    chart.value = this.value;
  }
  resetValidationUI();
  loadChart();
  loadTradeHistory();
  // v0.32.6: After switching, check if we already have a saved validation
  // for this token in the DB and show it.
  loadExistingValidation(this.value, document.getElementById('setupTimeframe').value);
});
document.getElementById('setupTimeframe').addEventListener('change', function() {
  const chartTf = document.getElementById('chartTimeframe');
  if (chartTf.value !== this.value) {
    chartTf.value = this.value;
  }
  resetValidationUI();
  loadChart();
  loadExistingValidation(document.getElementById('setupSymbol').value, this.value);
});"""
new_block = """// v0.40.27: Null-guard — setupSymbol/setupTimeframe were deleted in
// v0.40.25 (Discovery tab removal). Without this guard, .addEventListener
// on null throws TypeError and aborts ALL top-level code after this point
// (including polling timers + DOMContentLoaded setup). This was the silent
// killer breaking real-time chart updates + post-Start status polling.
(function() {
  const _ss = document.getElementById('setupSymbol');
  if (_ss) _ss.addEventListener('change', function() {
    const chart = document.getElementById('chartSymbol');
    if (chart.value !== this.value) {
      chart.value = this.value;
    }
    resetValidationUI();
    loadChart();
    loadTradeHistory();
    loadExistingValidation(this.value, document.getElementById('setupTimeframe').value);
  });
  const _st = document.getElementById('setupTimeframe');
  if (_st) _st.addEventListener('change', function() {
    const chartTf = document.getElementById('chartTimeframe');
    if (chartTf.value !== this.value) {
      chartTf.value = this.value;
    }
    resetValidationUI();
    loadChart();
    loadExistingValidation(document.getElementById('setupSymbol').value, this.value);
  });
})();"""
patch("null-guard setup listeners", old_block, new_block)

# ---------------------------------------------------------------------------
# P2: Rewrite startPaperTrading() → /api/multi-start
# ---------------------------------------------------------------------------
print("\n[P2] Rewrite startPaperTrading() → /api/multi-start...")
new_start_fn = """async function startPaperTrading() {
  // v0.40.27: Switched from /api/start-trading (which has a pre-trade
  // validation gate that blocks paper trading when WR/PF/RoR checks fail)
  // to /api/multi-start (which auto-validates but in paper mode
  // dry_run=True proceeds anyway). This lets the user see the bot
  // operating immediately, even on tokens that haven't passed strict
  // validation yet. The motor self-tunes via use_token_profile=True +
  // auto_calibrate=True.
  const symbol = (document.getElementById('chartSymbol') || {}).value
              || (document.getElementById('ticketSymbol') || {}).textContent
              || 'BTC/USDT';
  const normSymbol = symbol.includes('/') ? symbol : symbol.toUpperCase() + '/USDT';
  const tfGroup = document.getElementById('ticketTFGroup');
  const timeframe = (tfGroup && tfGroup.querySelector('.tg-btn.active'))
                 ? tfGroup.querySelector('.tg-btn.active').dataset.tf
                 : '5m';
  const exchange = (document.getElementById('chartExchange') || {}).value || 'binance';
  const capital = parseFloat((document.getElementById('ticketCapital') || {}).value) || 1000;

  const btn = document.getElementById('btnStartTrading');
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Iniciando...';

  try {
    // v0.40.27: /api/multi-start expects
    // {tokens: [{symbol, timeframe, exchange}], capital, leverage, auto_mode}
    const res = await fetch(`${API}/api/multi-start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tokens: [{ symbol: normSymbol, timeframe, exchange }],
        capital,
        leverage: currentLeverage,
        auto_mode: autoMode
      })
    });

    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    if (!data.ok) {
      alert('No se pudo iniciar Paper Trading:\\n\\n' + (data.error || 'Error desconocido'));
      btn.disabled = false;
      btn.textContent = originalText;
      return;
    }

    // v0.40.27: multi-start returns immediately with launched sessions.
    // The actual session goes through states: STARTING -> VALIDATING ->
    // INGESTING -> BUILDING -> STARTING_TRADER -> CONNECTING ->
    // WARMING_UP -> RUNNING. We poll /api/multi-status every 2s and flip
    // the button to 'Running' when the session reaches RUNNING (or after
    // 60s timeout).
    isTrading = true;
    document.getElementById('btnStopTrading').disabled = false;
    updateTradingBadge('LIVE');

    // Ensure chart shows the right symbol/TF
    const selC = document.getElementById('chartSymbol');
    if (selC) selC.value = normSymbol;
    const selT = document.getElementById('chartTimeframe');
    if (selT) selT.value = timeframe;
    loadChart();

    showStatusMsg(`Paper trading iniciado: ${normSymbol} ${timeframe} en ${exchange}. ` +
                  `Validando + ingiriendo datos + construyendo trie...`);
    if (typeof appendOpsFeed === 'function') {
      appendOpsFeed('info', `Start ${normSymbol} ${timeframe} ${currentLeverage}x — iniciando motor…`);
    }

    // Kick off status poller (flips button to 'Running' when session live)
    _pollSessionStatus(normSymbol, timeframe);

    // Ensure global multi-status poll is running too
    if (typeof pollMultiStatus === 'function') {
      if (!_multiStatusPollHandle) {
        _multiStatusPollHandle = setInterval(pollMultiStatus, 3000);
      }
      pollMultiStatus();
    }

  } catch (e) {
    console.error('startPaperTrading error:', e);
    alert('Error de red iniciando Paper Trading: ' + e.message +
          '\\n\\n¿Está corriendo el servidor en http://localhost:8420?');
    btn.disabled = false;
    btn.textContent = originalText;
  }
}"""
src = replace_function(src, "startPaperTrading", new_start_fn)

# ---------------------------------------------------------------------------
# P3: Rewrite stopTrading() → /api/multi-stop
# ---------------------------------------------------------------------------
print("\n[P3] Rewrite stopTrading() → /api/multi-stop...")
new_stop_fn = """async function stopTrading() {
  // v0.40.27: Switched from /api/stop-trading (which only stops the
  // singleton trading task) to /api/multi-stop (which cancels ALL
  // multi-token sessions). With no node_id param, it stops everything.
  try {
    const res = await fetch(`${API}/api/multi-stop?node_id=`, { method: 'POST' });
    if (!res.ok) throw new Error('Error deteniendo trading');

    isTrading = false;
    // v0.40.27: enable Start if a token is selected (no validationPassed gate)
    const _selSym = (document.getElementById('ticketSymbol') || {}).textContent;
    document.getElementById('btnStartTrading').disabled = (_selSym === '—' || !_selSym);
    document.getElementById('btnStartTrading').textContent = 'Start Paper';
    document.getElementById('btnStopTrading').disabled = true;
    updateTradingBadge('STOPPED');
    if (typeof appendOpsFeed === 'function') {
      appendOpsFeed('info', 'Stop — motor detenido por el usuario.');
    }

  } catch (e) {
    console.error('stopTrading error:', e);
  }
}"""
src = replace_function(src, "stopTrading", new_stop_fn)

# ---------------------------------------------------------------------------
# P4: Add _updateChartLiveTick() + _pollSessionStatus() helpers before updateDashboard
# ---------------------------------------------------------------------------
print("\n[P4] Insert real-time chart tick + session poll helpers...")

helpers_block = """// ============================================================
// v0.40.27: REAL-TIME CHART TICK UPDATE
// ============================================================
// The WS pushes a snapshot every 1s with `current_price`. We hook into
// updateDashboard() to live-update the chart's last candle's close
// (and high/low if exceeded). When the TF boundary crosses, we push a
// new candle so the chart scrolls naturally. Without this the chart
// is static between manual Reload clicks.
let _lastCandleTime = 0;
function _updateChartLiveTick(price, symbol, timeframe) {
  if (!chart || !candleSeries || !price || price <= 0) return;
  const chartSym = (document.getElementById('chartSymbol') || {}).value;
  if (symbol && chartSym && symbol !== chartSym) return; // not the displayed symbol
  const tfSecs = { '1m': 60, '5m': 300, '10m': 600, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400 };
  const tf = timeframe || (document.getElementById('chartTimeframe') || {}).value || '5m';
  const secs = tfSecs[tf] || 300;
  const now = Math.floor(Date.now() / 1000);
  const bucket = Math.floor(now / secs) * secs;
  try {
    const lastBar = candleSeries.data().slice(-1)[0];
    if (bucket > _lastCandleTime && _lastCandleTime > 0 && lastBar) {
      // New TF bucket started — push a fresh candle. The previous candle's
      // close becomes the new candle's open.
      const newCandle = { time: bucket, open: lastBar.close, high: Math.max(lastBar.close, price), low: Math.min(lastBar.close, price), close: price };
      candleSeries.update(newCandle);
      _lastCandleTime = bucket;
    } else if (lastBar && lastBar.time === bucket) {
      // Same bucket — update last candle's close (and high/low if exceeded)
      candleSeries.update({
        time: lastBar.time,
        open: lastBar.open,
        high: Math.max(lastBar.high, price),
        low: Math.min(lastBar.low, price),
        close: price,
      });
    } else if (!lastBar || lastBar.time < bucket) {
      // No prior candle for this bucket — seed one
      candleSeries.update({ time: bucket, open: price, high: price, low: price, close: price });
      _lastCandleTime = bucket;
    }
    // Update the price display in the ticket
    const ctrlPrice = document.getElementById('ctrlPrice');
    if (ctrlPrice) ctrlPrice.textContent = formatPrice(price);
  } catch (e) {
    // Chart may be mid-reload; non-critical
  }
}

// ============================================================
// v0.40.27: SESSION STATUS POLLER (post-Start)
// ============================================================
// After /api/multi-start returns, the session goes through states:
//   STARTING -> VALIDATING -> INGESTING -> BUILDING -> STARTING_TRADER
//   -> CONNECTING -> WARMING_UP -> RUNNING
// We poll /api/multi-status every 2s for up to 60s and flip the Start
// button from 'Iniciando...' to 'Running' (or back to 'Start Paper' on
// error). This addresses the user's complaint: 'se queda en iniciando
// y no hace nada'.
function _pollSessionStatus(symbol, timeframe, attemptsLeft = 30) {
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
}

"""
patch("insert chart-tick + session-poll helpers before updateDashboard",
      "// ============================================================\n// DASHBOARD UPDATE\n// ============================================================\n\nfunction updateDashboard(s) {",
      helpers_block + "// ============================================================\n// DASHBOARD UPDATE\n// ============================================================\n\nfunction updateDashboard(s) {")

# P4b: Hook _updateChartLiveTick() into updateDashboard — insert after headerPrice update
patch("call _updateChartLiveTick from updateDashboard",
      "  document.getElementById('headerPrice').textContent = s.current_price ? formatPrice(s.current_price) : '--';\n",
      "  document.getElementById('headerPrice').textContent = s.current_price ? formatPrice(s.current_price) : '--';\n"
      "  // v0.40.27: live-update the chart's last candle with the new price tick\n"
      "  if (s.current_price && typeof _updateChartLiveTick === 'function') {\n"
      "    _updateChartLiveTick(s.current_price, s.symbol, s.timeframe);\n"
      "  }\n")

# P4c: Reset _lastCandleTime in loadChart finally block
patch("reset _lastCandleTime in loadChart finally",
      "  } finally {\n    if (reloadBtn) { reloadBtn.textContent = 'Reload'; reloadBtn.disabled = false; }\n  }\n}",
      "  } finally {\n    if (reloadBtn) { reloadBtn.textContent = 'Reload'; reloadBtn.disabled = false; }\n"
      "    // v0.40.27: Track the last candle's timestamp so _updateChartLiveTick\n"
      "    // knows when to push a new candle vs. update the current one.\n"
      "    try {\n"
      "      const lastBar = candleSeries && candleSeries.data ? candleSeries.data().slice(-1)[0] : null;\n"
      "      _lastCandleTime = lastBar ? lastBar.time : 0;\n"
      "    } catch (e) { /* chart may be mid-reload */ }\n"
      "  }\n}")

# ---------------------------------------------------------------------------
# P5: Add Capital Allocation panel + CSS + updateAllocation() function
# ---------------------------------------------------------------------------
print("\n[P5] Add Capital Allocation panel...")

# P5a: Insert the panel HTML between Leverage row and Mode row
old_lev_block = """          <!-- Leverage -->
          <div class="ticket-row">
            <span class="ticket-label">Apalancamiento <span style="color:var(--text3);text-transform:none;font-size:9px;font-weight:500">(default 1x · max 10x)</span></span>
            <div class="ticket-btn-group" id="ticketLevGroup">
              <button class="tg-btn active" data-lev="1" onclick="setLeverage(1)">1x</button>
              <button class="tg-btn" data-lev="2" onclick="setLeverage(2)">2x</button>
              <button class="tg-btn" data-lev="3" onclick="setLeverage(3)">3x</button>
              <button class="tg-btn" data-lev="5" onclick="setLeverage(5)">5x</button>
              <button class="tg-btn" data-lev="10" onclick="setLeverage(10)">10x</button>
            </div>
          </div>
"""
new_lev_block = """          <!-- Leverage -->
          <div class="ticket-row">
            <span class="ticket-label">Apalancamiento <span style="color:var(--text3);text-transform:none;font-size:9px;font-weight:500">(default 1x · max 10x)</span></span>
            <div class="ticket-btn-group" id="ticketLevGroup">
              <button class="tg-btn active" data-lev="1" onclick="setLeverage(1)">1x</button>
              <button class="tg-btn" data-lev="2" onclick="setLeverage(2)">2x</button>
              <button class="tg-btn" data-lev="3" onclick="setLeverage(3)">3x</button>
              <button class="tg-btn" data-lev="5" onclick="setLeverage(5)">5x</button>
              <button class="tg-btn" data-lev="10" onclick="setLeverage(10)">10x</button>
            </div>
          </div>

          <!-- v0.40.27: Capital Allocation breakdown -->
          <div class="ticket-allocation" id="ticketAllocation">
            <div class="ticket-alloc-header">
              <span class="ticket-label">Asignación por Trade</span>
              <span class="ticket-alloc-hint" title="PPMT usa Quarter-Kelly (25%) × confianza × régimen × volatilidad × drawdown, con cap duro de 25% del equity por trade. El leverage se aplica DESPUÉS del sizing.">ⓘ</span>
            </div>
            <div class="ticket-alloc-grid">
              <div class="alloc-cell">
                <span class="alloc-label">Kelly</span>
                <span class="alloc-val">25% <span class="alloc-sub">(Quarter)</span></span>
              </div>
              <div class="alloc-cell">
                <span class="alloc-label">Max/Trade</span>
                <span class="alloc-val" id="allocMaxPct">25% <span class="alloc-sub">equity</span></span>
              </div>
              <div class="alloc-cell">
                <span class="alloc-label">$/Trade</span>
                <span class="alloc-val accent" id="allocPerTrade">$250</span>
              </div>
              <div class="alloc-cell">
                <span class="alloc-label">Notional c/Leverage</span>
                <span class="alloc-val accent2" id="allocNotional">$250</span>
              </div>
            </div>
            <div class="ticket-alloc-foot" id="allocFoot">
              Tamaño dinámico: conf + régimen + vol + drawdown lo ajustan en vivo.
            </div>
          </div>
"""
patch("insert Capital Allocation panel after Leverage row", old_lev_block, new_lev_block)

# P5b: Add CSS for .ticket-allocation — anchor on the actual existing CSS
patch("add .ticket-allocation CSS",
      "/* Action buttons — big, prominent, Apple HIG style */\n.ticket-actions{\n"
      "  display:grid;grid-template-columns:1fr 1fr;gap:8px;\n"
      "  padding-top:14px;border-top:1px solid var(--border);\n}",
      ".ticket-allocation {\n"
      "  background: rgba(15, 23, 42, 0.5);\n"
      "  border: 1px solid rgba(30, 42, 58, 0.6);\n"
      "  border-radius: 6px;\n"
      "  padding: 8px 10px;\n"
      "  margin-top: 2px;\n"
      "}\n"
      ".ticket-alloc-header { display: flex; align-items: center; gap: 4px; margin-bottom: 6px; }\n"
      ".ticket-alloc-hint { color: var(--text3); font-size: 11px; cursor: help; }\n"
      ".ticket-alloc-grid {\n"
      "  display: grid;\n"
      "  grid-template-columns: 1fr 1fr;\n"
      "  gap: 4px 8px;\n"
      "}\n"
      ".alloc-cell { display: flex; flex-direction: column; gap: 1px; padding: 3px 0; }\n"
      ".alloc-label { font-size: 8px; color: var(--text3); text-transform: uppercase; letter-spacing: 0.5px; }\n"
      ".alloc-val { font-family: var(--mono); font-size: 12px; font-weight: 600; color: var(--text); }\n"
      ".alloc-val.accent { color: var(--accent); }\n"
      ".alloc-val.accent2 { color: var(--accent2); }\n"
      ".alloc-sub { font-size: 8px; color: var(--text3); font-weight: 400; }\n"
      ".ticket-alloc-foot {\n"
      "  font-size: 9px;\n"
      "  color: var(--text3);\n"
      "  margin-top: 6px;\n"
      "  line-height: 1.3;\n"
      "  font-style: italic;\n"
      "}\n"
      ".ticket-actions {display:flex;gap:6px;margin-top:10px;}")

# P5c: Add updateAllocation() function — insert before selectToken
patch("insert updateAllocation function before selectToken",
      "function selectToken(symbol) {",
      "// v0.40.27: Update Capital Allocation panel based on capital + leverage.\n"
      "// Called from setLeverage, updateCapital, DOMContentLoaded.\n"
      "function updateAllocation() {\n"
      "  const cap = parseFloat((document.getElementById('ticketCapital') || {}).value) || 1000;\n"
      "  const lev = currentLeverage || 1;\n"
      "  const maxPct = 0.25;  // 25% of equity hard cap (max_position_pct)\n"
      "  const perTrade = cap * maxPct;\n"
      "  const notional = perTrade * lev;\n"
      "  const elMax = document.getElementById('allocMaxPct');\n"
      "  const elPT = document.getElementById('allocPerTrade');\n"
      "  const elN = document.getElementById('allocNotional');\n"
      "  if (elMax) elMax.innerHTML = (maxPct * 100).toFixed(0) + '% <span class=\"alloc-sub\">equity</span>';\n"
      "  if (elPT) elPT.textContent = '$' + perTrade.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });\n"
      "  if (elN) elN.textContent = '$' + notional.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });\n"
      "}\n\n"
      "function selectToken(symbol) {")

# P5d: Call updateAllocation from setLeverage
src = replace_function(src, "setLeverage",
    "function setLeverage(lev) {\n"
    "  currentLeverage = lev;\n"
    "  // v0.40.26: handle both legacy .lev-btn and new .tg-btn[data-lev]\n"
    "  document.querySelectorAll('.lev-btn, #ticketLevGroup .tg-btn').forEach(b => {\n"
    "    b.classList.toggle('active', parseInt(b.dataset.lev) === lev);\n"
    "  });\n"
    "  if (typeof updateAllocation === 'function') updateAllocation();\n"
    "}")

# P5e: Call updateAllocation from updateCapital — replace the whole function
src = replace_function(src, "updateCapital",
    "async function updateCapital() {\n"
    "  const inp = document.getElementById('ticketCapital');\n"
    "  if (!inp) return;\n"
    "  const val = parseFloat(inp.value) || 1000;\n"
    "  // v0.40.26: also update Money Management display\n"
    "  const mm = document.getElementById('mmTotalCapital');\n"
    "  if (mm) mm.textContent = '$' + val.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 2});\n"
    "  // v0.40.27: refresh Capital Allocation panel\n"
    "  if (typeof updateAllocation === 'function') updateAllocation();\n"
    "  // Persist to server (best-effort)\n"
    "  try {\n"
    "    await fetch(`${API}/api/nodes/capital`, {\n"
    "      method: 'POST',\n"
    "      headers: {'Content-Type': 'application/json'},\n"
    "      body: JSON.stringify({total_capital: val})\n"
    "    });\n"
    "  } catch (e) {\n"
    "    // silent — UI still reflects user input\n"
    "  }\n"
    "}")

# ---------------------------------------------------------------------------
# P6: Wire WS signal/trade events → appendOpsFeed
# ---------------------------------------------------------------------------
print("\n[P6] Wire WS events → appendOpsFeed...")

# P6a: In handleTradeEvent, also push to Trading tab Operations feed
old_trade_handler = """  try {
    const ev = msg.event || '';
    const p = msg.payload || {};
    // Log to the activity feed so the user sees a visible trace.
    if (typeof logActivity === 'function') {
      const dir = (p.direction || '').toUpperCase();
      const sym = p.symbol || '?';
      const pnlPct = Number(p.pnl_pct || 0).toFixed(2);
      const reason = p.exit_reason || '';
      const sign = Number(p.pnl_pct || 0) >= 0 ? '+' : '';
      logActivity('info', `Trade closed: ${dir} ${sym} ${sign}${pnlPct}% (${reason})`);
    }"""
new_trade_handler = """  try {
    const ev = msg.event || '';
    const p = msg.payload || {};
    // v0.40.27: Also push to the Trading tab Operations feed so the user
    // sees the closed trade in the right column immediately.
    if (typeof appendOpsFeed === 'function' && ev === 'trade_closed') {
      const dir = (p.direction || '').toUpperCase();
      const sym = p.symbol || '?';
      const pnl = Number(p.pnl_pct || 0);
      const reason = p.exit_reason || '';
      appendOpsFeed('trade', `CLOSE ${dir} ${sym} (${reason})`, pnl);
    }
    if (typeof logActivity === 'function') {
      const dir = (p.direction || '').toUpperCase();
      const sym = p.symbol || '?';
      const pnlPct = Number(p.pnl_pct || 0).toFixed(2);
      const reason = p.exit_reason || '';
      const sign = Number(p.pnl_pct || 0) >= 0 ? '+' : '';
      logActivity('info', `Trade closed: ${dir} ${sym} ${sign}${pnlPct}% (${reason})`);
    }"""
patch("wire trade_event → appendOpsFeed", old_trade_handler, new_trade_handler)

# P6b: Push new signals to the feed from updateDashboard (deduped by timestamp)
patch("wire signals → appendOpsFeed",
      "function updateDashboard(s) {\n  if (!s) return;\n",
      "let _lastSigTs = 0;\n"
      "function updateDashboard(s) {\n"
      "  if (!s) return;\n"
      "  // v0.40.27: Push new signals to the Trading tab Operations feed.\n"
      "  // The WS snapshot may include multi_signals_by_symbol (a map keyed\n"
      "  // by symbol, each value a list of signal dicts). We dedupe by\n"
      "  // timestamp so we don't double-fire on every snapshot.\n"
      "  if (s.multi_signals_by_symbol && typeof appendOpsFeed === 'function') {\n"
      "    try {\n"
      "      for (const sym in s.multi_signals_by_symbol) {\n"
      "        const sigs = s.multi_signals_by_symbol[sym];\n"
      "        if (!Array.isArray(sigs)) continue;\n"
      "        for (const sig of sigs) {\n"
      "          const ts = Number(sig.timestamp || 0);\n"
      "          if (ts > _lastSigTs) {\n"
      "            _lastSigTs = ts;\n"
      "            const dir = (sig.direction || sig.type || 'SIGNAL').toUpperCase();\n"
      "            const conf = Number(sig.confidence || 0).toFixed(2);\n"
      "            const price = sig.entry_price ? ' @ ' + formatPrice(sig.entry_price) : '';\n"
      "            appendOpsFeed('signal', `${dir} ${sym}${price} conf=${conf}`);\n"
      "          }\n"
      "        }\n"
      "      }\n"
      "    } catch (e) { /* non-critical */ }\n"
      "  }")

# ---------------------------------------------------------------------------
# P7: Kick off multi-status polling on DOMContentLoaded
# ---------------------------------------------------------------------------
print("\n[P7] Enhance DOMContentLoaded handler...")
old_dom_handler = """// Initialize on DOMContentLoaded: trigger capital sync + ensure token list is loaded
document.addEventListener('DOMContentLoaded', () => {
  // Set initial capital display
  setTimeout(() => { updateCapital(); }, 500);
});"""
new_dom_handler = """// v0.40.27: Initialize on DOMContentLoaded — kick off capital sync,
// update the allocation panel, and start multi-status polling so any
// sessions running on the server are reflected in the UI immediately.
// Without this, refreshing the page while sessions are running would
// leave the UI in a stale state until the user clicks something.
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    updateCapital();
    if (typeof updateAllocation === 'function') updateAllocation();
  }, 300);
  // Start multi-status polling (covers case where user refreshes page
  // while sessions are running on the server).
  setTimeout(() => {
    if (typeof pollMultiStatus === 'function') {
      if (!_multiStatusPollHandle) {
        _multiStatusPollHandle = setInterval(pollMultiStatus, 3000);
      }
      pollMultiStatus();
    }
  }, 800);
});"""
patch("enhance DOMContentLoaded handler", old_dom_handler, new_dom_handler)

# ---------------------------------------------------------------------------
# P8: Version bump v0.40.26 → v0.40.27
# ---------------------------------------------------------------------------
print("\n[P8] Version bump → v0.40.27...")
n = src.count("v0.40.26")
src = src.replace("v0.40.26", "v0.40.27")
print(f"  ✓ version bumped ({n} occurrences of v0.40.26 → v0.40.27)")

# ---------------------------------------------------------------------------
# Save + sanity-check
# ---------------------------------------------------------------------------
HTML.write_text(src)
print(f"\n✅ Done. {orig_len} → {len(src)} bytes ({len(src) - orig_len:+d})")

# Basic HTML structure sanity-check
import html.parser
class P(html.parser.HTMLParser):
    def __init__(self): super().__init__(); self.errors = []
    def error(self, msg): self.errors.append(msg)
p = P()
try:
    p.feed(src)
    print("✓ HTML parses cleanly")
except Exception as e:
    print(f"⚠ HTML parse error: {e}")
