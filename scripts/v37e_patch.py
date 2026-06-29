#!/usr/bin/env python3
"""
v37e Patch — Apply breakthrough config to paper-trading-engine.ts
v37e: WR 62.1%, P&L +23.41, AvgR +0.46, MaxDD 0.30%, Profitable 80% of seeds

CHANGES:
1. Strategy A: SL 2.0 → 1.4 ATR (tighter risk)
2. Strategy B: SL 2.0 → 1.4 ATR
3. ATR floor 0.55%: skip trades when ATR% < 0.55 (filters calm regime where fees dominate)
4. Lock profit: move SL to entry+0.2R when +0.4R reached (locks small profit)
5. Partial TP: close 30% at +0.8R, then trail remainder with 0.6 ATR
6. Track max favorable price (MFE) for trailing
"""
import shutil
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')

# Backup
backup = ENGINE.with_suffix('.ts.bak.v31b')
if not backup.exists():
    shutil.copy2(ENGINE, backup)
    print(f"✓ Backup created: {backup}")
else:
    print(f"✓ Backup exists: {backup}")

src = ENGINE.read_text()
original = src

def edit(src, old, new, label):
    if old not in src:
        raise SystemExit(f"✗ EDIT FAIL [{label}]: anchor not found")
    if src.count(old) > 1:
        raise SystemExit(f"✗ EDIT FAIL [{label}]: anchor not unique ({src.count(old)} matches)")
    src = src.replace(old, new, 1)
    print(f"✓ EDIT [{label}]")
    return src

# ───────────────────────────────────────────────────────────────────
# EDIT 1: Add new Position fields for v37e (lock/partial/trail/MFE)
# ───────────────────────────────────────────────────────────────────
# Find the Position interface or class. Looking for current_sl field.
OLD_1 = """  current_sl: number | null
  current_tp: number | null
  catastrophic_sl: number | null"""
NEW_1 = """  current_sl: number | null
  current_tp: number | null
  catastrophic_sl: number | null
  // v37e: lock-profit + partial TP + trailing state
  lock_done?: boolean
  partial_done?: boolean
  trail_active?: boolean
  max_favorable_price?: number
  initial_atr?: number
  initial_sl_distance?: number"""
src = edit(src, OLD_1, NEW_1, "Add Position fields for v37e")

# ───────────────────────────────────────────────────────────────────
# EDIT 2: Strategy A — reduce SL 2.0 → 1.4 ATR + add ATR floor + init v37e fields
# ───────────────────────────────────────────────────────────────────
# Strategy A: ATR floor filter + initial field set
OLD_2A_FLOOR = """      .filter(x => !this.cooldownUntil.has(x.ticker.symbol) || now > (this.cooldownUntil.get(x.ticker.symbol) || 0))
      .filter(x => !this.positions.has(x.ticker.symbol))
      .filter(x => this.checkCorrelationLimit(x.ticker.symbol))
      .sort((a, b) => Math.abs(b.recentMomentum) - Math.abs(a.recentMomentum))
      .slice(0, 3)"""
NEW_2A_FLOOR = """      .filter(x => !this.cooldownUntil.has(x.ticker.symbol) || now > (this.cooldownUntil.get(x.ticker.symbol) || 0))
      .filter(x => !this.positions.has(x.ticker.symbol))
      .filter(x => this.checkCorrelationLimit(x.ticker.symbol))
      .filter(x => {
        // v37e: ATR floor 0.55% — skip trades in low-vol regimes where fees dominate
        const histA = this.priceHistory.get(x.ticker.symbol) || []
        const pricesA = histA.map(h => h.price)
        const atrA = computeATR(pricesA, 60)
        const atrPctA = atrA / x.ticker.price * 100
        return atrPctA >= 0.55
      })
      .sort((a, b) => Math.abs(b.recentMomentum) - Math.abs(a.recentMomentum))
      .slice(0, 3)"""
src = edit(src, OLD_2A_FLOOR, NEW_2A_FLOOR, "Strategy A: ATR floor 0.55%")

# Strategy A: SL 2.0 → 1.4 + init v37e fields
OLD_2A_SL = """          // v14 NIGHT1 FIX: SL más ancho (1.5 → 2.0 ATR), TP más cercano (3 → 2.5 ATR).
          //   35% de trades A cerraban en <2min en snapshot A.
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 2.0 : pos.entry_price + atr * 2.0
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4"""
NEW_2A_SL = """          // v37e: SL 2.0 → 1.4 ATR (tighter risk; backtest 5-seed: P&L -7 → +23, MaxDD 0.38% → 0.30%)
          //   Lock profit at +0.4R, partial TP 30% at +0.8R, trail 0.6 ATR after partial.
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.4 : pos.entry_price + atr * 1.4
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4
          pos.initial_atr = atr
          pos.initial_sl_distance = atr * 1.4
          pos.lock_done = false
          pos.partial_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price"""
src = edit(src, OLD_2A_SL, NEW_2A_SL, "Strategy A: SL 1.4 ATR + v37e init fields")

# ───────────────────────────────────────────────────────────────────
# EDIT 3: Strategy B — reduce SL 2.0 → 1.4 ATR + add ATR floor + init v37e fields
# ───────────────────────────────────────────────────────────────────
OLD_3B_FLOOR = """      .filter(x => !this.cooldownUntil.has(x.ticker.symbol) || now > (this.cooldownUntil.get(x.ticker.symbol) || 0))
      .filter(x => !this.positions.has(x.ticker.symbol))
      .filter(x => this.checkCorrelationLimit(x.ticker.symbol))
      .sort((a, b) => Math.abs(a.rsi - 50) - Math.abs(b.rsi - 50))
      .slice(0, 2)"""
NEW_3B_FLOOR = """      .filter(x => !this.cooldownUntil.has(x.ticker.symbol) || now > (this.cooldownUntil.get(x.ticker.symbol) || 0))
      .filter(x => !this.positions.has(x.ticker.symbol))
      .filter(x => this.checkCorrelationLimit(x.ticker.symbol))
      .filter(x => {
        // v37e: ATR floor 0.55%
        const histB = this.priceHistory.get(x.ticker.symbol) || []
        const pricesB = histB.map(h => h.price)
        const atrB = computeATR(pricesB, 60)
        const atrPctB = atrB / x.ticker.price * 100
        return atrPctB >= 0.55
      })
      .sort((a, b) => Math.abs(a.rsi - 50) - Math.abs(b.rsi - 50))
      .slice(0, 2)"""
src = edit(src, OLD_3B_FLOOR, NEW_3B_FLOOR, "Strategy B: ATR floor 0.55%")

OLD_3B_SL = """          // v14 NIGHT1 FIX: SL más ancho (1.5 → 2.0 ATR), TP más cercano (2 → 2.5 ATR).
          //   LONG WR era 20% en snapshot B vs SHORT WR 40%. Más aire para que LONG respire.
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 2.0 : pos.entry_price + atr * 2.0
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4"""
NEW_3B_SL = """          // v37e: SL 2.0 → 1.4 ATR + v37e state init (lock/partial/trail/MFE)
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.4 : pos.entry_price + atr * 1.4
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4
          pos.initial_atr = atr
          pos.initial_sl_distance = atr * 1.4
          pos.lock_done = false
          pos.partial_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price"""
src = edit(src, OLD_3B_SL, NEW_3B_SL, "Strategy B: SL 1.4 ATR + v37e init fields")

# ───────────────────────────────────────────────────────────────────
# EDIT 4: checkStops — add v37e lock/partial/trail logic BEFORE SL/TP check
# ───────────────────────────────────────────────────────────────────
# Insert lock/partial/trail logic right after the break-even block (before SL/TP trigger)
OLD_4_CHECK = """      // ─── SL/TP/CatSL trigger (IMMEDIATE — exchange-like behavior) ───"""
NEW_4_CHECK = """      // ─── v37e: Lock profit + Partial TP + Trailing (before SL/TP check) ───
      // These run on every tick to manage open positions proactively.
      // Lock: move SL to entry+0.2R when +0.4R reached (locks small profit)
      // Partial: close 30% at +0.8R, then enable trailing on remainder
      // Trail: 0.6 ATR trailing stop on remainder (overrides TP)
      if (pos.initial_atr && pos.initial_sl_distance && pos.initial_sl_distance > 0) {
        const rMultiple = isLong
          ? (price - pos.entry_price) / pos.initial_sl_distance
          : (pos.entry_price - price) / pos.initial_sl_distance

        // Track MFE (max favorable price) for trailing
        if (pos.max_favorable_price === undefined) pos.max_favorable_price = pos.entry_price
        if (isLong && price > pos.max_favorable_price) pos.max_favorable_price = price
        if (!isLong && price < pos.max_favorable_price) pos.max_favorable_price = price

        // 1. Lock profit at +0.4R → move SL to entry+0.2R
        if (!pos.lock_done && rMultiple >= 0.4) {
          const lockR = 0.2
          if (isLong) {
            const newSL = pos.entry_price + lockR * pos.initial_sl_distance
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
          } else {
            const newSL = pos.entry_price - lockR * pos.initial_sl_distance
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
          }
          pos.lock_done = true
        }

        // 2. Partial TP at +0.8R → close 30%, enable trailing on remainder
        if (!pos.partial_done && rMultiple >= 0.8) {
          // Close 30% of position at market
          const partialPct = 0.30
          const partialQty = pos.qty * partialPct
          if (partialQty > 0.0001) {
            const partialResult = isLong
              ? this.marketSell(sym, partialQty * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty * pos.entry_price, pos.strategy)
            if (partialResult.success) {
              pos.qty -= partialQty
              console.log(`[Paper/v37e] ${sym} PARTIAL_TP 30% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial_done = true
          pos.trail_active = true
          // Set initial trail SL at current price - 0.6 ATR
          const trailDist = pos.initial_atr * 0.6
          if (isLong) {
            const newSL = price - trailDist
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
          } else {
            const newSL = price + trailDist
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
          }
        }

        // 3. Update trailing stop if active
        if (pos.trail_active && pos.max_favorable_price) {
          const trailDist = pos.initial_atr * 0.6
          if (isLong) {
            const newSL = pos.max_favorable_price - trailDist
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
            pos.current_tp = null  // disable TP — let trail do the work
          } else {
            const newSL = pos.max_favorable_price + trailDist
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
            pos.current_tp = null
          }
        }
      }

      // ─── SL/TP/CatSL trigger (IMMEDIATE — exchange-like behavior) ───"""
src = edit(src, OLD_4_CHECK, NEW_4_CHECK, "checkStops: v37e lock/partial/trail logic")

# Write back
ENGINE.write_text(src)

# Verify braces balance
opens = src.count('{')
closes = src.count('}')
print(f"\n✓ Braces: {opens} open / {closes} close / balanced={opens == closes}")

# Verify parens balance
po = src.count('(')
pc = src.count(')')
print(f"✓ Parens: {po} open / {pc} close / balanced={po == pc}")

# Verify brackets balance
bo = src.count('[')
bc = src.count(']')
print(f"✓ Brackets: {bo} open / {bc} close / balanced={bo == bc}")

# Verify edits applied
checks = [
    ('v37e: ATR floor 0.55%', 'atrPctA >= 0.55'),
    ('v37e: SL 1.4 ATR (A)', 'pos.entry_price - atr * 1.4'),
    ('v37e: SL 1.4 ATR (B)', 'pos.entry_price + atr * 1.4'),
    ('v37e: lock_profit logic', 'rMultiple >= 0.4'),
    ('v37e: partial TP logic', 'rMultiple >= 0.8'),
    ('v37e: trailing logic', 'pos.trail_active && pos.max_favorable_price'),
    ('v37e: Position fields', 'initial_sl_distance?: number'),
]
print("\nVerification:")
all_ok = True
for label, needle in checks:
    if needle in src:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label} MISSING")
        all_ok = False

if all_ok:
    print("\n✅ All edits verified")
else:
    print("\n⚠️  Some edits missing — review needed")

print(f"\nEngine file: {ENGINE}")
print(f"Backup: {backup}")
