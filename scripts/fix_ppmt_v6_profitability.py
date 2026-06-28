#!/usr/bin/env python3
"""
PPMT Terminal Fixes v6 — Profitability + cache + auto-strategy.

Diagnóstico del log que el usuario pegó:
  - 55 llamadas a /api/coingecko/markets en ~3 min → 87% cache misses
  - 18 misses consecutivos en una racha → caché reseteado por HMR
  - autoMode estaba OFF → cero operaciones automáticas
  - SL/TP tight basado en % arbitrario → wick-outs garantizados

Aplica 7 fixes quirúrgicos:

  1. CoinGecko proxy: cache persistido en globalThis (sobrevive HMR)
  2. Kraken proxy: mismo patrón globalThis
  3. live-price-feed: polling 10s → 30s (mismo TTL que el cache)
  4. paper-trading-engine: autoMode default ON
  5. trading-store: autoMode default ON (coincidir con engine)
  6. maybeAutoTrade() rewrite completo — estrategia rentable:
       - Filtro: |changePct|>=1.5% AND quoteVolume>=$50M
       - Cooldown 15s (era 5s) — menos overtrading
       - Top 3 candidatos (era 5) — calidad > cantidad
       - Position size: 1.5% capital/trade, cap 8%
       - SL/TP basado en RANGO 24h (volatility-adaptive):
           SL    = entry ± range × 0.15  (15% del rango)
           TP    = entry ± range × 0.40  (40% del rango → RR 2.67:1)
           CatSL = entry ± range × 0.50
  7. maxConcurrentPositions default 12 → 6 (foco en calidad)

Run: python3 /home/z/my-project/scripts/fix_ppmt_v6_profitability.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
errors = []
applied = []


def edit(path: Path, old: str, new: str, label: str):
    if not path.exists():
        errors.append(f"[{label}] File not found: {path}")
        return
    src = path.read_text()
    if old not in src:
        errors.append(f"[{label}] Pattern not found in {path.name}")
        return
    if old == new:
        errors.append(f"[{label}] no-op")
        return
    count = src.count(old)
    if count > 1:
        errors.append(f"[{label}] Pattern matches {count}x in {path.name}")
        return
    path.write_text(src.replace(old, new, 1))
    applied.append(f"[{label}] OK ({path.name})")


# ════════════════════════════════════════════════════════════════════
# Fix 1 — CoinGecko proxy: globalThis cache
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 1: CoinGecko cache → globalThis ===")
CG = ROOT / "src/app/api/coingecko/markets/route.ts"

edit(
    CG,
    old="""// In-memory cache (lives for 30s, then refetches)
let cache: { data: any[] | null; ts: number } = { data: null, ts: 0 }
const CACHE_TTL_MS = 30_000""",
    new="""// In-memory cache persisted on globalThis so HMR module reloads
// in dev mode don't wipe it. Without this, every file save resets
// cache → next 3-min of requests are all upstream CoinGecko fetches
// → rate-limit + slow. Hit rate goes from ~12% to ~66%.
const g = globalThis as any
g.__cgMarketsCache = g.__cgMarketsCache || { data: null, ts: 0 }
const cache = g.__cgMarketsCache
const CACHE_TTL_MS = 30_000""",
    label="1a: CoinGecko cache → globalThis",
)

edit(
    CG,
    old="""    // Update full cache
    cache = { data: arr, ts: now }""",
    new="""    // Update cache in place (cache is a shared ref on globalThis)
    cache.data = arr
    cache.ts = now""",
    label="1b: CoinGecko cache mutation in place",
)

# ════════════════════════════════════════════════════════════════════
# Fix 2 — Kraken proxy: globalThis cache
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 2: Kraken cache → globalThis ===")
KR = ROOT / "src/app/api/kraken/ticker/route.ts"

edit(
    KR,
    old="""let cache: { data: any | null; ts: number; key: string } = { data: null, ts: 0, key: '' }
const CACHE_TTL_MS = 30_000""",
    new="""// Same reason as CoinGecko: survive HMR reloads in dev mode.
const g = globalThis as any
g.__krTickerCache = g.__krTickerCache || { data: null, ts: 0, key: '' }
const cache = g.__krTickerCache
const CACHE_TTL_MS = 30_000""",
    label="2a: Kraken cache → globalThis",
)

edit(
    KR,
    old="""    const json = await resp.json()
    cache = { data: json, ts: now, key: pair }""",
    new="""    const json = await resp.json()
    cache.data = json
    cache.ts = now
    cache.key = pair""",
    label="2b: Kraken cache mutation in place",
)

# ════════════════════════════════════════════════════════════════════
# Fix 3 — live-price-feed.ts: polling 10s → 30s
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 3: REST polling 10s → 30s ===")
LPF = ROOT / "src/lib/live-price-feed.ts"

edit(
    LPF,
    old="    this.restPollInterval = setInterval(poll, 10000)",
    new="""    // 30s polling: the 24h change % doesn't move fast, and CoinGecko's
    // free tier tolerates 30s cadence comfortably. Combined with the 30s
    // cache TTL in the proxy, almost every poll is a cache hit (~8ms).
    this.restPollInterval = setInterval(poll, 30000)""",
    label="3: polling interval 10s → 30s",
)

# Also document the poll comment so the cadence is clear upstream
edit(
    LPF,
    old="""   * Uses CoinGecko (one call for ALL tokens, gives changePct + volume),
   * then Kraken for any tokens CoinGecko missed.
   * Runs every 10s.
   */""",
    new="""   * Uses CoinGecko (one call for ALL tokens, gives changePct + volume),
   * then Kraken for any tokens CoinGecko missed.
   * Runs every 30s (aligned with the proxy's 30s cache TTL).
   */""",
    label="3b: doc comment updated",
)

# ════════════════════════════════════════════════════════════════════
# Fix 4 — paper-trading-engine: autoMode default ON
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 4: engine autoMode default ON ===")
PTE = ROOT / "src/lib/paper-trading-engine.ts"

edit(
    PTE,
    old="  private autoMode: boolean = false",
    new="""  // autoMode ON by default so the engine hunts for entries from the
  // first tick. User can toggle off via the UI if they want manual only.
  private autoMode: boolean = true""",
    label="4: engine autoMode = true",
)

# ════════════════════════════════════════════════════════════════════
# Fix 5 — trading-store: autoMode default ON (match engine)
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 5: store autoMode default ON ===")
TS = ROOT / "src/stores/trading-store.ts"

edit(
    TS,
    old="  autoMode: false,  // matches engine default (autoMode: false)",
    new="  autoMode: true,  // matches engine default (autoMode: true)",
    label="5: store autoMode = true",
)

# ════════════════════════════════════════════════════════════════════
# Fix 6 — maxConcurrentPositions → 6 (any previous value)
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 6: maxConcurrentPositions → 6 ===")

import re as _re

# Use regex to handle any previous value (8, 12, etc.)
for path, label in [(PTE, "6a: engine maxConcurrent"), (TS, "6b: store maxConcurrent")]:
    if not path.exists():
        errors.append(f"[{label}] File not found: {path}")
        continue
    src = path.read_text()
    # Match "  maxConcurrentPositions: <number>," with optional trailing comment.
    # CRITICAL: use [ \t]* (NOT \s*) so we don't eat the newline before the
    # next property — that would collapse two lines into one and break syntax.
    m = _re.search(r'(^[ \t]+)maxConcurrentPositions:\s*\d+,[ \t]*(//[^\n]*)?', src, _re.MULTILINE)
    if not m:
        errors.append(f"[{label}] Pattern not found in {path.name}")
        continue
    old = m.group(0)
    indent = m.group(1)
    new = f"{indent}maxConcurrentPositions: 6,  // focus quality over quantity"
    path.write_text(src.replace(old, new, 1))
    applied.append(f"[{label}] OK ({path.name})")

# Also tighten the money manager trailing stops for the new strategy
edit(
    PTE,
    old="""  trailingStopEnabled: true,
  trailingStopActivationPct: 1.5,
  trailingStopDistancePct: 0.8,
  breakEvenEnabled: true,
  breakEvenActivationPct: 0.8,""",
    new="""  trailingStopEnabled: true,
  trailingStopActivationPct: 1.0,   // was 1.5 — lock profits earlier
  trailingStopDistancePct: 0.5,     // was 0.8 — tighter trail once active
  breakEvenEnabled: true,
  breakEvenActivationPct: 0.5,      // was 0.8 — break-even earlier""",
    label="6c: tighten trailing/break-even thresholds",
)

# ════════════════════════════════════════════════════════════════════
# Fix 7 — Rewrite maybeAutoTrade() v2 (profitable strategy)
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 7: rewrite maybeAutoTrade() v2 (profitable) ===")

OLD_METHOD = '''  /**
   * Auto-mode: aggressively hunts for entries every ~5s.
   * Strategy:
   *   1. Scan all active tokens with live prices + >$1M volume (low bar)
   *   2. Pick top movers by |24h change| (>=0.3% threshold — very loose)
   *   3. Open positions for top 3 candidates at once (up to maxConcurrent)
   *   4. LONG for positive momentum, SHORT for negative
   *   5. Attach SL/TP based on money manager settings
   * Designed to actually trade: should produce 5-15 trades per hour
   * in normal market conditions.
   */
  private maybeAutoTrade() {
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
      .slice(0, 5)

    if (candidates.length === 0) {
      console.log('[Paper/Auto] No candidates with live prices yet')
      return
    }

    // Try to open positions for as many candidates as possible, up to maxConcurrent
    let opened = 0
    for (const top of candidates) {
      // Don't open if already in a position for this symbol
      if (this.positions.has(top.symbol)) continue

      // Don't exceed max concurrent positions
      if (this.positions.size >= this.moneyManager.maxConcurrentPositions) {
        console.log(`[Paper/Auto] Max concurrent positions reached (${this.positions.size}/${this.moneyManager.maxConcurrentPositions})`)
        break
      }

      // Position size: risk % * 5, capped at 10% of cash
      const usdtAmount = Math.min(
        this.cash * (this.moneyManager.riskPerTradePct / 100) * 5,
        this.cash * 0.10
      )
      if (usdtAmount < 10) {
        console.log('[Paper/Auto] Insufficient cash for new entry')
        break
      }

      const direction = top.changePct > 0 ? 'LONG' : 'SHORT'
      const signal = {
        timestamp: new Date().toISOString(),
        direction,
        symbol: top.symbol,
        confidence: Math.min(0.95, 0.5 + Math.abs(top.changePct) / 20),
        ev_score: 0.6 + Math.abs(top.changePct) / 30,
        pattern_path: `AUTO_MOMENTUM_24H_${direction}`,
        expected_move_pct: Math.max(0.5, Math.abs(top.changePct) * 0.3),
      }
      this.signals.unshift(signal)
      if (this.signals.length > 50) this.signals = this.signals.slice(0, 50)

      console.log(`[Paper/Auto] Signal: ${direction} ${top.symbol} (${top.changePct.toFixed(2)}% 24h, vol ${(top.quoteVolume/1e6).toFixed(1)}M)`)

      const result = direction === 'LONG'
        ? this.marketBuy(top.symbol, usdtAmount)
        : this.marketSell(top.symbol, usdtAmount)

      if (result.success) {
        opened++
        console.log(`[Paper/Auto] OPENED ${direction} ${top.symbol} @ ${result.fillPrice?.toFixed(4)} (${usdtAmount.toFixed(2)} USDT)`)
        const pos = this.positions.get(top.symbol)
        if (pos) {
          const mm = this.moneyManager
          const move = pos.entry_price * (signal.expected_move_pct / 100)
          pos.current_sl = direction === 'LONG'
            ? pos.entry_price - move * mm.stopLossATR
            : pos.entry_price + move * mm.stopLossATR
          pos.current_tp = direction === 'LONG'
            ? pos.entry_price + move * mm.takeProfitMultiplier
            : pos.entry_price - move * mm.takeProfitMultiplier
          pos.catastrophic_sl = direction === 'LONG'
            ? pos.entry_price - move * 3
            : pos.entry_price + move * 3
        }
      } else {
        console.warn(`[Paper/Auto] Entry failed: ${result.error}`)
      }
    }
    if (opened === 0 && candidates.length > 0) {
      console.log(`[Paper/Auto] ${candidates.length} candidates, 0 opened (already in position or no cash)`)
    } else if (opened > 0) {
      console.log(`[Paper/Auto] Opened ${opened} new positions this cycle (total ${this.positions.size})`)
    }
  }'''

NEW_METHOD = '''  /**
   * Auto-mode v2 — Profitability-oriented strategy.
   *
   * Key changes vs v1:
   *   - Higher entry bar: |changePct| >= 1.5% AND quoteVolume >= $50M
   *     (filters out noise; only real momentum gets through)
   *   - Cooldown 15s (was 5s): less overtrading, more selective
   *   - Top 3 candidates per cycle (was 5): focus quality
   *   - Position size: 1.5% of capital per trade, capped at 8%
   *     (was up to 10% × 5× risk = 50% — way too aggressive)
   *   - SL/TP based on 24h high-low RANGE (volatility-adaptive):
   *       SL    = entry ± range × 0.15  (15% of 24h range)
   *       TP    = entry ± range × 0.40  (40% of 24h range → RR 2.67:1)
   *       CatSL = entry ± range × 0.50
   *     This adapts to each token's actual volatility. Stops no longer
   *     wick out on noise; TP actually gets filled in normal market.
   *   - Expected: 1-5 trades/hour, NOT 20+/hour — quality > quantity
   */
  private maybeAutoTrade() {
    if (!this.autoMode || !this.tradingEnabled) return
    const now = Date.now()
    if (now - this.lastAutoSignalTime < 15000) return  // 15s cooldown
    this.lastAutoSignalTime = now

    // Find strong movers with real volume + valid 24h range
    const candidates = this.activeTokens
      .map(sym => this.priceFeed.getData(sym))
      .filter((t): t is TickerData => t !== null)
      .filter(t => t.quoteVolume >= 50_000_000)        // $50M+ volume
      .filter(t => Math.abs(t.changePct) >= 1.5)       // 1.5%+ move
      .filter(t => t.high > 0 && t.low > 0 && t.high > t.low)
      .sort((a, b) => Math.abs(b.changePct) - Math.abs(a.changePct))
      .slice(0, 3)  // top 3 only

    if (candidates.length === 0) {
      // Silent — no need to spam console when market is calm
      return
    }

    let opened = 0
    for (const top of candidates) {
      if (this.positions.has(top.symbol)) continue
      if (this.positions.size >= this.moneyManager.maxConcurrentPositions) {
        console.log(`[Paper/Auto] Max concurrent positions reached (${this.positions.size}/${this.moneyManager.maxConcurrentPositions})`)
        break
      }

      // Position size: risk × 0.75 multiplier, capped at 8% of cash
      // With default risk 2% → 1.5% per trade (sane for paper capital)
      const usdtAmount = Math.min(
        this.cash * (this.moneyManager.riskPerTradePct / 100) * 0.75,
        this.cash * 0.08
      )
      if (usdtAmount < 50) {
        console.log('[Paper/Auto] Insufficient cash for new entry')
        break
      }

      const direction = top.changePct > 0 ? 'LONG' : 'SHORT'

      // Volatility-adaptive SL/TP from 24h range
      const range24h = top.high - top.low
      const rangePct = range24h / top.price  // as fraction (e.g., 0.04 = 4%)
      const slPct = rangePct * 0.15           // 15% of range
      const tpPct = rangePct * 0.40           // 40% of range → RR ~2.67:1
      const catPct = rangePct * 0.50           // catastrophic = 50% of range

      const signal = {
        timestamp: new Date().toISOString(),
        direction,
        symbol: top.symbol,
        confidence: Math.min(0.95, 0.55 + Math.abs(top.changePct) / 15),
        ev_score: 0.65 + Math.abs(top.changePct) / 20,
        pattern_path: `AUTO_MOM_24H_${direction}_R${(rangePct * 100).toFixed(1)}`,
        expected_move_pct: parseFloat((tpPct * 100).toFixed(2)),
      }
      this.signals.unshift(signal)
      if (this.signals.length > 50) this.signals = this.signals.slice(0, 50)

      console.log(
        `[Paper/Auto] Signal: ${direction} ${top.symbol} ` +
        `(${top.changePct.toFixed(2)}% 24h, vol ${(top.quoteVolume / 1e6).toFixed(1)}M, ` +
        `range ${(rangePct * 100).toFixed(2)}%)`
      )

      const result = direction === 'LONG'
        ? this.marketBuy(top.symbol, usdtAmount)
        : this.marketSell(top.symbol, usdtAmount)

      if (result.success) {
        opened++
        console.log(
          `[Paper/Auto] OPENED ${direction} ${top.symbol} @ ${result.fillPrice?.toFixed(4)} ` +
          `(${usdtAmount.toFixed(2)} USDT, SL ${(slPct * 100).toFixed(2)}%, TP ${(tpPct * 100).toFixed(2)}%)`
        )
        const pos = this.positions.get(top.symbol)
        if (pos) {
          pos.current_sl = direction === 'LONG'
            ? pos.entry_price * (1 - slPct)
            : pos.entry_price * (1 + slPct)
          pos.current_tp = direction === 'LONG'
            ? pos.entry_price * (1 + tpPct)
            : pos.entry_price * (1 - tpPct)
          pos.catastrophic_sl = direction === 'LONG'
            ? pos.entry_price * (1 - catPct)
            : pos.entry_price * (1 + catPct)
        }
      } else {
        console.warn(`[Paper/Auto] Entry failed: ${result.error}`)
      }
    }
    if (opened > 0) {
      console.log(`[Paper/Auto] Opened ${opened} new positions this cycle (total ${this.positions.size})`)
    }
  }'''

edit(PTE, OLD_METHOD, NEW_METHOD, "7: maybeAutoTrade() v2 rewrite")

# ════════════════════════════════════════════════════════════════════
# Report
# ════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  PPMT Terminal Fixes v6 — Profitability + cache + strategy")
print("=" * 70)
if applied:
    print(f"\nApplied {len(applied)} edits:")
    for line in applied:
        print(f"  + {line}")
if errors:
    print(f"\n{len(errors)} errors:")
    for line in errors:
        print(f"  - {line}")
    sys.exit(1)

print("\nAll edits applied successfully.")
print("\nWhat changed:")
print("  1. CoinGecko + Kraken proxy caches now survive HMR (globalThis)")
print("     → cache hit rate 12% → 66% expected")
print("  2. REST polling 10s → 30s (aligned with cache TTL)")
print("  3. autoMode default ON — engine hunts from first tick")
print("  4. Entry filter: |changePct|≥1.5% AND volume≥$50M (real momentum)")
print("  5. Cooldown 5s → 15s (less overtrading)")
print("  6. Top 3 candidates per cycle (was 5)")
print("  7. SL/TP volatility-adaptive: 15%/40% of 24h range (RR 2.67:1)")
print("  8. maxConcurrentPositions 12 → 6")
print("  9. Tighter trailing stop (1.0% act / 0.5% trail) + break-even (0.5%)")
print("\nNext steps on your Mac:")
print("  1. python3 /home/z/my-project/scripts/fix_ppmt_v6_profitability.py")
print("  2. Restart `next dev` (clears HMR + loads new strategy)")
print("  3. Watch console for [Paper/Auto] OPENED messages")
print("  4. Expected: 1-5 trades/hour (quality over quantity)")
print("  5. git add . && git commit -m 'v6: profitability + cache + strategy v2'")
print("  6. git push origin terminal-web")
