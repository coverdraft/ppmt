#!/usr/bin/env python3
"""
PPMT Terminal Fixes v2 — Batch edit script.

Fixes for the regression reported by the user:
  "todos estos errores ahora mismo.. no estas haciendo bien las cosas por
   eso no encuentra ni una operacion y solo tiene 25 token pon muchos mas
   asi esta todo el tiempo buscando y operando... y haz que todo todo funcione"

Console errors observed:
  1. paper-trading-engine.ts:751  Cannot read properties of null (reading 'toFixed')
  2. <polygon> attribute points: Expected number, "NaN,55 300,60 0,…"
  3. <polyline> attribute points: Expected number, "NaN,55"
  4. Only 25 tokens visible (should be 89)
  5. No trades executed ("no encuentra ni una operacion")

Root cause analysis:
  - CoinGecko's /coins/markets endpoint returns `current_price: null` for
    some illiquid tokens. The price feed stored those nulls in the prices
    map. Then `PaperTradingEngine.snapshot()` at line 751 called
    `t.price.toFixed(...)` on a null `t.price` — the whole snapshot threw
    and the state-push to zustand never happened. Result:
      * The UI only showed tokens that had been added to the store BEFORE
        the first null-price token (about 25 of them).
      * Auto-trade signals may have fired in the engine, but since the
        snapshot crashed before returning, the store never learned about
        the new positions → "no operations found".
      * The SVG polygon/polyline NaN errors are a cascade — chart
        components received NaN because the store had stale/empty data.
  - Auto-mode was disabled by default in both the engine and the store,
    so even if the snapshot had worked, no trades would happen unless
    the user manually clicked "Auto".

Fixes applied:
  A. live-price-feed.ts:  Skip CoinGecko entries with null/NaN/<=0 price
     so the prices map never stores an invalid TickerData.
  B. paper-trading-engine.ts:  Make snapshot() fully defensive — wrap
     each token's state-building in try/catch + numeric guards, so one
     bad token can never crash the whole snapshot again.
  C. paper-trading-engine.ts:  Enable auto-mode by default
     (`private autoMode: boolean = true`).
  D. paper-trading-engine.ts:  Lower auto-trade thresholds so the engine
     actually finds candidates:
       - cooldown 5000ms → 3500ms
       - volume floor $1M → $200K
       - |change%| floor 0.3% → 0.1%
  E. trading-store.ts:  Initial state `autoMode: true` and `isRunning: true`
     so the UI shows the engine as actively trading from the first render.
  F. use-trading-socket.ts:  On engine init, call setAutoMode(true) +
     setTradingEnabled(true) and push the flags to the store so the
     engine starts scanning & trading immediately on page load.

Run:  python3 /home/z/my-project/scripts/fix_ppmt_v2.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
LIB = ROOT / "src/lib"
STORE = ROOT / "src/stores"

errors = []
applied = []


def edit_file(path: Path, old: str, new: str, label: str):
    """Replace `old` with `new` in `path`. Fail loudly if not found."""
    if not path.exists():
        errors.append(f"[{label}] File not found: {path}")
        return
    src = path.read_text()
    if old not in src:
        errors.append(f"[{label}] Pattern not found in {path}")
        return
    if old == new:
        errors.append(f"[{label}] old == new (no-op) in {path}")
        return
    count = src.count(old)
    if count > 1:
        errors.append(f"[{label}] Pattern matches {count} times in {path} — needs disambiguation")
        return
    path.write_text(src.replace(old, new, 1))
    applied.append(f"[{label}] OK ({path.relative_to(ROOT)})")


# ─── Fix A: live-price-feed.ts — never store null/NaN prices ─────────────
edit_file(
    LIB / "live-price-feed.ts",
    old="""      let updated = 0
      for (const c of arr) {
        const meta = META_BY_COINGECKO.get(c.id)
        if (!meta) continue
        const internal = meta.internal
        const price = c.current_price
        const data: TickerData = {
          symbol: internal,
          rawSymbol: c.id,
          price,
          changePct: c.price_change_percentage_24h ?? 0,
          volume: (c.total_volume ?? 0) / (price || 1), // base vol approx quote/price
          quoteVolume: c.total_volume ?? 0,
          high: c.high_24h ?? price,
          low: c.low_24h ?? price,
          timestamp: Date.now(),
        }""",
    new="""      let updated = 0
      for (const c of arr) {
        const meta = META_BY_COINGECKO.get(c.id)
        if (!meta) continue
        const internal = meta.internal
        const price = c.current_price
        // CoinGecko returns null current_price for illiquid / delisted coins.
        // Storing null here would crash PaperTradingEngine.snapshot() when
        // it calls t.price.toFixed(...). Skip these entries entirely —
        // if no other source provides a price for this token, the engine's
        // snapshot loop will simply skip the token (defensive guard added).
        if (typeof price !== 'number' || !isFinite(price) || price <= 0) continue
        const totalVol = typeof c.total_volume === 'number' && isFinite(c.total_volume)
          ? c.total_volume : 0
        const changePct = typeof c.price_change_percentage_24h === 'number'
          && isFinite(c.price_change_percentage_24h)
          ? c.price_change_percentage_24h : 0
        const data: TickerData = {
          symbol: internal,
          rawSymbol: c.id,
          price,
          changePct,
          volume: totalVol > 0 ? totalVol / price : 0, // base vol approx quote/price
          quoteVolume: totalVol,
          high: (typeof c.high_24h === 'number' && isFinite(c.high_24h)) ? c.high_24h : price,
          low: (typeof c.low_24h === 'number' && isFinite(c.low_24h)) ? c.low_24h : price,
          timestamp: Date.now(),
        }""",
    label="A: CoinGecko null-price guard",
)


# ─── Fix B: paper-trading-engine.ts — bulletproof snapshot token loop ───
edit_file(
    LIB / "paper-trading-engine.ts",
    old="""    // Token states from live price feed
    const tokenStates: Record<string, TokenState> = {}
    for (const sym of this.activeTokens) {
      const t = this.priceFeed.getData(sym)
      if (t) {
        const pos = this.positions.get(sym)
        tokenStates[sym] = {
          symbol: t.symbol,
          name: TOKEN_NAMES[t.symbol] || t.symbol,
          price: parseFloat(t.price.toFixed(t.price < 1 ? 6 : t.price < 100 ? 4 : 2)),
          change24h: parseFloat(t.changePct.toFixed(2)),
          volume24h: parseFloat(t.quoteVolume.toFixed(0)),
          positions: pos ? [pos] : [],
          unrealizedPnl: pos ? parseFloat(pos.pnl_usdt.toFixed(4)) : 0,
          realizedPnl: 0,
          allocationPct: totalValue > 0 && pos
            ? parseFloat(((pos.qty * t.price) / totalValue * 100).toFixed(1))
            : 0,
          isActive: true,
          isTrading: !!pos,
          winRate: this.totalTrades > 0 ? this.winningTrades / this.totalTrades : 0,
          totalTrades: this.trades.filter(tr => tr.symbol === sym).length,
          equity: this.equity,
          color: TOKEN_COLORS[sym] || '#6b7280',
        }
      }
    }""",
    new="""    // Token states from live price feed.
    // IMPORTANT: be fully defensive against null / NaN / undefined prices.
    // CoinGecko can return null current_price for illiquid tokens; if we
    // call .toFixed() on null we crash the whole snapshot, which would
    // freeze the UI and stop all auto-trading. Skip any token without a
    // valid finite numeric price.
    const tokenStates: Record<string, TokenState> = {}
    for (const sym of this.activeTokens) {
      try {
        const t = this.priceFeed.getData(sym)
        if (!t) continue
        if (typeof t.price !== 'number' || !isFinite(t.price) || t.price <= 0) continue
        if (typeof t.changePct !== 'number' || !isFinite(t.changePct)) continue
        if (typeof t.quoteVolume !== 'number' || !isFinite(t.quoteVolume)) continue

        const pos = this.positions.get(sym)
        tokenStates[sym] = {
          symbol: t.symbol,
          name: TOKEN_NAMES[t.symbol] || t.symbol,
          price: parseFloat(t.price.toFixed(t.price < 1 ? 6 : t.price < 100 ? 4 : 2)),
          change24h: parseFloat(t.changePct.toFixed(2)),
          volume24h: parseFloat(t.quoteVolume.toFixed(0)),
          positions: pos ? [pos] : [],
          unrealizedPnl: pos ? parseFloat(pos.pnl_usdt.toFixed(4)) : 0,
          realizedPnl: 0,
          allocationPct: totalValue > 0 && pos
            ? parseFloat(((pos.qty * t.price) / totalValue * 100).toFixed(1))
            : 0,
          isActive: true,
          isTrading: !!pos,
          winRate: this.totalTrades > 0 ? this.winningTrades / this.totalTrades : 0,
          totalTrades: this.trades.filter(tr => tr.symbol === sym).length,
          equity: this.equity,
          color: TOKEN_COLORS[sym] || '#6b7280',
        }
      } catch {
        // Never let one bad token crash the whole snapshot
        continue
      }
    }""",
    label="B: snapshot tokenStates defensive guard",
)


# ─── Fix C: paper-trading-engine.ts — autoMode default ON ───────────────
edit_file(
    LIB / "paper-trading-engine.ts",
    old="""  private running: boolean = false
  private tradingEnabled: boolean = true
  private autoMode: boolean = false""",
    new="""  private running: boolean = false
  private tradingEnabled: boolean = true
  // Auto-mode is ON by default so the engine starts scanning and trading
  // the moment the page loads. User can toggle it off via the header.
  private autoMode: boolean = true""",
    label="C: autoMode default true",
)


# ─── Fix D: paper-trading-engine.ts — more aggressive auto-trade ────────
# D1: cooldown 5000ms → 3500ms
edit_file(
    LIB / "paper-trading-engine.ts",
    old="""  private maybeAutoTrade() {
    if (!this.autoMode || !this.tradingEnabled) return
    const now = Date.now()
    if (now - this.lastAutoSignalTime < 5000) return
    this.lastAutoSignalTime = now

    // Find strongest movers among active tokens with live prices
    const candidates = this.activeTokens
      .map(sym => this.priceFeed.getData(sym))
      .filter((t): t is TickerData => t !== null && t.quoteVolume > 1_000_000)
      .filter(t => Math.abs(t.changePct) >= 0.3)
      .sort((a, b) => Math.abs(b.changePct) - Math.abs(a.changePct))
      .slice(0, 5)""",
    new="""  private maybeAutoTrade() {
    if (!this.autoMode || !this.tradingEnabled) return
    const now = Date.now()
    // 3.5s cooldown — fast enough to keep the engine visibly hunting
    // opportunities, slow enough to not spam orders on every tick.
    if (now - this.lastAutoSignalTime < 3500) return
    this.lastAutoSignalTime = now

    // Find strongest movers among active tokens with live prices.
    // Very loose thresholds (>$200K volume, |change%| >= 0.1%) so the
    // engine finds candidates practically every cycle. Most crypto
    // tokens move >0.1% over 24h — this is a momentum-following scan.
    const candidates = this.activeTokens
      .map(sym => this.priceFeed.getData(sym))
      .filter((t): t is TickerData =>
        t !== null
        && typeof t.price === 'number' && isFinite(t.price) && t.price > 0
        && typeof t.quoteVolume === 'number' && isFinite(t.quoteVolume)
        && t.quoteVolume > 200_000
      )
      .filter(t => typeof t.changePct === 'number' && isFinite(t.changePct) && Math.abs(t.changePct) >= 0.1)
      .sort((a, b) => Math.abs(b.changePct) - Math.abs(a.changePct))
      .slice(0, 5)""",
    label="D: aggressive auto-trade thresholds",
)


# ─── Fix E: trading-store.ts — initial state autoMode + isRunning ON ────
edit_file(
    STORE / "trading-store.ts",
    old="""  isRunning: false,""",
    new="""  isRunning: true,""",
    label="E1: store isRunning true",
)
edit_file(
    STORE / "trading-store.ts",
    old="""  autoMode: false,  // matches engine default (autoMode: false)""",
    new="""  // Auto-mode ON by default — engine starts scanning & trading on page load.
  // User can toggle off via the header.
  autoMode: true,""",
    label="E2: store autoMode true",
)


# ─── Fix F: use-trading-socket.ts — auto-enable engine on init ─────────
edit_file(
    LIB / "use-trading-socket.ts",
    old="""    GLOBAL_REFCOUNT++
    if (!GLOBAL_ENGINE) {
      console.log('[Paper] Starting paper trading engine with live Coinbase + CoinGecko prices')
      console.log(`[Paper] Initial capital: ${INITIAL_CAPITAL} USDT`)
      console.log(`[Paper] Supported tokens: ${SUPPORTED_TOKENS.length}`)

      GLOBAL_FEED = new LivePriceFeed([...SUPPORTED_TOKENS])
      GLOBAL_ENGINE = new PaperTradingEngine(GLOBAL_FEED)
      GLOBAL_LISTENER = (state: any) => applyState(state)
      GLOBAL_ENGINE.startTicking(GLOBAL_LISTENER, 1500)
    } else {""",
    new="""    GLOBAL_REFCOUNT++
    if (!GLOBAL_ENGINE) {
      console.log('[Paper] Starting paper trading engine with live Coinbase + CoinGecko prices')
      console.log(`[Paper] Initial capital: ${INITIAL_CAPITAL} USDT`)
      console.log(`[Paper] Supported tokens: ${SUPPORTED_TOKENS.length}`)

      GLOBAL_FEED = new LivePriceFeed([...SUPPORTED_TOKENS])
      GLOBAL_ENGINE = new PaperTradingEngine(GLOBAL_FEED)
      // Auto-enable trading + auto-mode so the engine starts scanning
      // and opening positions the moment the page loads. The user can
      // still pause via the header buttons.
      GLOBAL_ENGINE.setTradingEnabled(true)
      GLOBAL_ENGINE.setAutoMode(true)
      useTradingStore.getState().setState({
        isRunning: true,
        autoMode: true,
        killSwitchActive: false,
      })
      console.log('[Paper] Auto-mode + trading enabled on init — engine will hunt opportunities continuously')
      GLOBAL_LISTENER = (state: any) => applyState(state)
      GLOBAL_ENGINE.startTicking(GLOBAL_LISTENER, 1500)
    } else {""",
    label="F: auto-enable engine on init",
)


# ─── Report ─────────────────────────────────────────────────────────────
print("\n=== PPMT Terminal Fixes v2 ===\n")
if applied:
    print(f"Applied {len(applied)} edits:")
    for line in applied:
        print(f"  ✓ {line}")
if errors:
    print(f"\n{len(errors)} errors:")
    for line in errors:
        print(f"  ✗ {line}")
    sys.exit(1)
print("\nAll edits applied successfully.")
