#!/usr/bin/env python3
"""
v15 Volatility Circuit Breaker
==============================

Añade un circuit breaker defensivo que detecta volatilidad extrema del mercado
y pausa nuevas entradas durante 10 minutos. Las posiciones abiertas siguen
gestionándose normalmente (SL/TP/trailing no se ven afectados).

Detección:
  - Para cada token con ≥60 muestras de precio, calcula ATR(60)/precio*100
  - Promedia los top tokens por volumen
  - Si avgAtrPct > 1.5%  → mercado en pánico (típico calmado: 0.2-0.5%)
  - One-shot: cuando se cruza el umbral, fija volPauseUntil = now + 10 min
  - Auto-resume tras 10 min (no requiere acción manual)

Aplica a:
  1. /home/z/my-project/ppmt/src/lib/paper-trading-engine.ts   (repo real)
  2. /home/z/my-project/scripts/terminal/paper-trading-engine-v3.ts  (workspace)

Es idempotente: si v15 ya está aplicado, no hace nada.
"""
from pathlib import Path
import sys
import shutil

REPO_ENGINE = Path("/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts")
WORKSPACE_ENGINE = Path("/home/z/my-project/scripts/terminal/paper-trading-engine-v3.ts")

V15_TAG = "v15 VOL CB"

# ──────────────────────────────────────────────────────────────────────────
# Edit 1: Add volPauseUntil field (after lastCBLogTime)
# ──────────────────────────────────────────────────────────────────────────
EDIT_FIELD = (
    "  private lastCBLogTime: number = 0",
    "  private lastCBLogTime: number = 0\n\n  // v15 VOL CB: timestamp when volatility pause expires (0 = no pause)\n"
    "  private volPauseUntil: number = 0\n  private lastVolCBLogTime: number = 0"
)

# ──────────────────────────────────────────────────────────────────────────
# Edit 2: Insert volatility CB block at top of maybeAutoTrade()
# ──────────────────────────────────────────────────────────────────────────
EDIT_CB_BLOCK = (
    """    // ─── Circuit breaker: stop new entries if drawdown exceeded ───
    const totalValue = this.computeTotalValue()
    const totalPnlPct = ((totalValue - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100""",
    """    // ─── v15 VOL CB: pause new entries when market volatility is extreme ───
    // Detects violent market-wide moves (avg ATR/price > 1.5% across top tokens)
    // and pauses NEW entries for 10 minutes. Open positions keep managing normally.
    // Calm market: avgAtrPct ≈ 0.2-0.5%.  Volatile: 0.8-1.2%.  Extreme: >1.5%.
    if (now < this.volPauseUntil) {
      const minsLeft = Math.ceil((this.volPauseUntil - now) / 60000)
      if (now - this.lastVolCBLogTime > 60000) {
        console.log(`[Paper/VolCB] ⛔ Volatility pause active — ${minsLeft}min left. New entries skipped.`)
        this.lastVolCBLogTime = now
      }
      return
    }
    const volNow = this.computeMarketVolatility()
    if (volNow.extreme) {
      this.volPauseUntil = now + 10 * 60 * 1000  // 10 min
      console.log(
        `[Paper/VolCB] 🌋 Extreme market volatility detected — ` +
        `avgATR/price=${volNow.avgAtrPct.toFixed(3)}% across ${volNow.tokenCount} tokens. ` +
        `Pausing new entries for 10min. Open positions continue managing.`
      )
      this.lastVolCBLogTime = now
      return
    }

    // ─── Circuit breaker: stop new entries if drawdown exceeded ───
    const totalValue = this.computeTotalValue()
    const totalPnlPct = ((totalValue - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100"""
)

# ──────────────────────────────────────────────────────────────────────────
# Edit 3: Add computeMarketVolatility() method (before maybeReport)
# ──────────────────────────────────────────────────────────────────────────
EDIT_METHOD = (
    "  private maybeReport() {",
    """  /**
   * v15 VOL CB: Compute real-time market-wide volatility.
   * Returns avgAtrPct = average of (ATR(60) / price * 100) across top-volume
   * tokens with sufficient price history. This is a TRUE real-time vol measure
   * (last ~90s of price action), unlike the 24h avgChange 'regime' which is stale.
   *
   * Thresholds (calibrated for 1.5s tick / 60-sample ATR ≈ 90s window):
   *   calm     < 0.5%
   *   normal   0.5 - 0.8%
   *   high     0.8 - 1.5%
   *   extreme  > 1.5%   ← triggers circuit breaker
   */
  private computeMarketVolatility(): { avgAtrPct: number; tokenCount: number; extreme: boolean } {
    let sumAtrPct = 0
    let count = 0
    for (const sym of WS_ELIGIBLE_TOKENS) {
      const ticker = this.priceFeed.getData(sym)
      if (!ticker || ticker.quoteVolume < 50_000_000) continue
      const hist = this.priceHistory.get(sym) || []
      if (hist.length < 60) continue
      const prices = hist.slice(-60).map(h => h.price)
      const atr = computeATR(prices, 60)
      if (atr <= 0 || ticker.price <= 0) continue
      sumAtrPct += (atr / ticker.price) * 100
      count++
    }
    const avgAtrPct = count > 0 ? sumAtrPct / count : 0
    return {
      avgAtrPct,
      tokenCount: count,
      extreme: count >= 3 && avgAtrPct > 1.5,  // need ≥3 tokens agreeing to avoid single-token pump false positives
    }
  }

  private maybeReport() {"""
)

# ──────────────────────────────────────────────────────────────────────────
# Edit 4: Expose vol_regime + vol_pause_until in getState() output
# ──────────────────────────────────────────────────────────────────────────
EDIT_STATE = (
    "      regime,\n      latest_signal:",
    "      regime,\n      vol_regime: (() => {\n        const v = this.computeMarketVolatility()\n        return { avg_atr_pct: parseFloat(v.avgAtrPct.toFixed(3)), token_count: v.tokenCount, extreme: v.extreme, paused: Date.now() < this.volPauseUntil, pause_remaining_ms: Math.max(0, this.volPauseUntil - Date.now()) }\n      })(),\n      latest_signal:"
)

EDITS = [
    ("volPauseUntil field", EDIT_FIELD),
    ("CB block in maybeAutoTrade", EDIT_CB_BLOCK),
    ("computeMarketVolatility method", EDIT_METHOD),
    ("vol_regime in state output", EDIT_STATE),
]


def apply_to_file(path: Path) -> bool:
    print(f"\n{'='*70}\n📄 Patching: {path}\n{'='*70}")
    if not path.exists():
        print(f"  ❌ File not found, skipping")
        return False

    src = path.read_text(encoding="utf-8")

    if "v15 VOL CB" in src:
        print(f"  ⏭️  v15 already applied, skipping")
        return True

    # Backup
    bak = path.with_suffix(path.suffix + ".bak.v14")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  💾 Backup: {bak.name}")

    new_src = src
    for label, (old, new) in EDITS:
        if old not in new_src:
            print(f"  ❌ Could not find anchor for: {label}")
            print(f"     Looking for:\n     {old[:120]}...")
            return False
        if new_src.count(old) > 1:
            print(f"  ❌ Anchor not unique for: {label} (found {new_src.count(old)} matches)")
            return False
        new_src = new_src.replace(old, new, 1)
        print(f"  ✅ Applied: {label}")

    # Brace balance check
    open_b = new_src.count("{")
    close_b = new_src.count("}")
    if open_b != close_b:
        print(f"  ❌ Brace imbalance: {open_b} {{ vs {close_b} }} — aborting (file not written)")
        return False
    print(f"  ✓ Braces balanced: {open_b}/{close_b}")

    open_p = new_src.count("(")
    close_p = new_src.count(")")
    if open_p != close_p:
        print(f"  ⚠️  Paren imbalance: {open_p} ( vs {close_p} ) — continuing anyway (may be in comments)")

    path.write_text(new_src, encoding="utf-8")
    print(f"  📝 Written: {path}")
    return True


def main():
    ok1 = apply_to_file(REPO_ENGINE)
    ok2 = apply_to_file(WORKSPACE_ENGINE)
    if not (ok1 and ok2):
        print("\n❌ FAILED — at least one file could not be patched. Aborting.")
        sys.exit(1)
    print("\n" + "="*70)
    print("✅ v15 VOL CB applied successfully to both files")
    print("="*70)
    print("\nNext steps:")
    print("  1. cd /home/z/my-project/ppmt")
    print("  2. git diff --stat")
    print("  3. git add src/lib/paper-trading-engine.ts")
    print("  4. git commit -m 'feat(v15): volatility circuit breaker'")
    print("  5. git push origin terminal-web")


if __name__ == "__main__":
    main()
