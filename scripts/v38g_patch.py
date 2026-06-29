#!/usr/bin/env python3
"""
v38g Patch — Update engine from v37e to v38g combo (8-seed validated winner)
v38g: WR 66.7%, P&L +30.97, Profitable 88% of seeds, MaxDD 0.26%, AvgR +0.60, PF 1.85

CHANGES from v37e:
- Lock trigger: 0.4R → 0.5R (later lock, more room before SL tightening)
- Partial trigger: 0.8R → 0.7R (earlier partial close)
- Partial close %: 30% → 40% (larger partial)
- Trail distance: 0.6 ATR → 0.5 ATR (tighter trailing)
- ATR floor: 0.55% → 0.58% (slightly tighter filter)
"""
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
src = ENGINE.read_text()

def edit(src, old, new, label):
    if old not in src:
        raise SystemExit(f"✗ EDIT FAIL [{label}]: anchor not found")
    if src.count(old) > 1:
        raise SystemExit(f"✗ EDIT FAIL [{label}]: anchor not unique ({src.count(old)} matches)")
    src = src.replace(old, new, 1)
    print(f"✓ EDIT [{label}]")
    return src

# EDIT 1: ATR floor 0.55 → 0.58 (Strategy A and B)
src = edit(src,
    "        const atrPctA = atrA / x.ticker.price * 100\n        return atrPctA >= 0.55",
    "        const atrPctA = atrA / x.ticker.price * 100\n        return atrPctA >= 0.58",
    "Strategy A: ATR floor 0.55 → 0.58")

src = edit(src,
    "        const atrPctB = atrB / x.ticker.price * 100\n        return atrPctB >= 0.55",
    "        const atrPctB = atrB / x.ticker.price * 100\n        return atrPctB >= 0.58",
    "Strategy B: ATR floor 0.55 → 0.58")

# EDIT 2: Lock trigger 0.4 → 0.5
src = edit(src,
    "        // 1. Lock profit at +0.4R → move SL to entry+0.2R\n        if (!pos.lock_done && rMultiple >= 0.4) {",
    "        // 1. Lock profit at +0.5R → move SL to entry+0.2R (v38g: 0.4→0.5)\n        if (!pos.lock_done && rMultiple >= 0.5) {",
    "Lock trigger 0.4R → 0.5R")

# EDIT 3: Partial trigger 0.8 → 0.7 + partial_pct 30% → 40%
src = edit(src,
    "        // 2. Partial TP at +0.8R → close 30%, enable trailing on remainder\n        if (!pos.partial_done && rMultiple >= 0.8) {\n          // Close 30% of position at market\n          const partialPct = 0.30",
    "        // 2. Partial TP at +0.7R → close 40%, enable trailing on remainder (v38g)\n        if (!pos.partial_done && rMultiple >= 0.7) {\n          // Close 40% of position at market\n          const partialPct = 0.40",
    "Partial TP 0.8R/30% → 0.7R/40%")

# EDIT 4: Update partial log message
src = edit(src,
    '              console.log(`[Paper/v37e] ${sym} PARTIAL_TP 30% @ ${price} (R=${rMultiple.toFixed(2)})`)',
    '              console.log(`[Paper/v38g] ${sym} PARTIAL_TP 40% @ ${price} (R=${rMultiple.toFixed(2)})`)',
    "Partial log message v37e → v38g")

# EDIT 5: Trail distance 0.6 → 0.5 (appears 2x: initial set + update)
# First occurrence: initial trail set after partial
src = edit(src,
    "          pos.partial_done = true\n          pos.trail_active = true\n          // Set initial trail SL at current price - 0.6 ATR\n          const trailDist = pos.initial_atr * 0.6",
    "          pos.partial_done = true\n          pos.trail_active = true\n          // Set initial trail SL at current price - 0.5 ATR (v38g: 0.6→0.5)\n          const trailDist = pos.initial_atr * 0.5",
    "Trail initial 0.6 → 0.5")

# Second occurrence: trail update
src = edit(src,
    "        if (pos.trail_active && pos.max_favorable_price) {\n          const trailDist = pos.initial_atr * 0.6",
    "        if (pos.trail_active && pos.max_favorable_price) {\n          const trailDist = pos.initial_atr * 0.5",
    "Trail update 0.6 → 0.5")

# EDIT 6: Update version comment
src = edit(src,
    "      // ─── v37e: Lock profit + Partial TP + Trailing (before SL/TP check) ───\n      // These run on every tick to manage open positions proactively.\n      // Lock: move SL to entry+0.2R when +0.4R reached (locks small profit)\n      // Partial: close 30% at +0.8R, then enable trailing on remainder\n      // Trail: 0.6 ATR trailing stop on remainder (overrides TP)",
    "      // ─── v38g: Lock profit + Partial TP + Trailing (before SL/TP check) ───\n      // These run on every tick to manage open positions proactively.\n      // Lock: move SL to entry+0.2R when +0.5R reached (locks small profit)\n      // Partial: close 40% at +0.7R, then enable trailing on remainder\n      // Trail: 0.5 ATR trailing stop on remainder (overrides TP)\n      // 8-seed validation: WR 66.7%, P&L +30.97, Profitable 88%, MaxDD 0.26%",
    "Version comment v37e → v38g")

# EDIT 7: Update SL comment to mention v38g
src = edit(src,
    "          // v37e: SL 2.0 → 1.4 ATR (tighter risk; backtest 5-seed: P&L -7 → +23, MaxDD 0.38% → 0.30%)\n          //   Lock profit at +0.4R, partial TP 30% at +0.8R, trail 0.6 ATR after partial.",
    "          // v38g: SL 1.4 ATR + lock 0.5R + partial 40% at 0.7R + trail 0.5 ATR + ATR floor 0.58%\n          //   8-seed validation: WR 66.7%, P&L +30.97, Profitable 88% of seeds, MaxDD 0.26%",
    "Strategy A: comment v37e → v38g")

src = edit(src,
    "          // v37e: SL 2.0 → 1.4 ATR + v37e state init (lock/partial/trail/MFE)",
    "          // v38g: SL 1.4 ATR + v38g state init (lock 0.5R / partial 40% at 0.7R / trail 0.5 ATR)",
    "Strategy B: comment v37e → v38g")

ENGINE.write_text(src)

# Verify braces
opens = src.count('{'); closes = src.count('}')
print(f"\n✓ Braces: {opens}/{closes} balanced={opens == closes}")
po = src.count('('); pc = src.count(')')
print(f"✓ Parens: {po}/{pc} balanced={po == pc}")
bo = src.count('['); bc = src.count(']')
print(f"✓ Brackets: {bo}/{bc} balanced={bo == bc}")

# Verify edits
checks = [
    ('v38g ATR floor 0.58 (A)', 'atrPctA >= 0.58'),
    ('v38g ATR floor 0.58 (B)', 'atrPctB >= 0.58'),
    ('v38g lock 0.5R', 'rMultiple >= 0.5'),
    ('v38g partial 0.7R', 'rMultiple >= 0.7'),
    ('v38g partial 40%', 'const partialPct = 0.40'),
    ('v38g trail 0.5 ATR (initial)', 'pos.initial_atr * 0.5'),
    ('v38g trail 0.5 ATR (update)', 'pos.initial_atr * 0.5'),
    ('v38g log message', '[Paper/v38g]'),
]
print("\nVerification:")
all_ok = True
for label, needle in checks:
    if needle in src:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label} MISSING")
        all_ok = False

# Count occurrences of 0.5 trail (should be 2)
trail_count = src.count('pos.initial_atr * 0.5')
print(f"\nTrail 0.5 occurrences: {trail_count} (expected 2)")
# Count occurrences of 0.6 (should be 0 in v38g context)
old_trail_count = src.count('pos.initial_atr * 0.6')
print(f"Old trail 0.6 occurrences: {old_trail_count} (expected 0)")

if all_ok and trail_count == 2 and old_trail_count == 0:
    print("\n✅ All v38g edits verified")
else:
    print("\n⚠️  Some edits missing — review needed")

print(f"\nEngine file: {ENGINE}")
