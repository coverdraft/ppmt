#!/usr/bin/env python3
"""v43a_patch.py — Apply v43a multi-partial + tighter trail to paper-trading-engine.ts

V43a config (12-seed validated):
- Lock profit at +0.5R → move SL to entry+0.2R (unchanged from v38g)
- Partial 1 at +0.5R → close 15% at market (NEW)
- Partial 2 at +1.0R → close 25% at market, enable trailing on remainder (NEW)
- Trailing: 0.4 ATR (was 0.5 in v38g)
- SL: 1.4 ATR (unchanged)
- TP: 1.2 ATR (unchanged)
- ATR floor: 0.58% (unchanged)

Backtest (12 seeds, 4h × 10 tokens):
  v38g (production): WR 61.8%, P&L +11.01, AvgR +0.41, MaxDD 0.31%, PF 1.46
  v43a (new):        WR 72.5%, P&L +13.92, AvgR +0.61, MaxDD 0.29%, PF 1.54
  → +10.7pp WR, +27% P&L, +0.20 AvgR, -0.02pp MaxDD, +0.08 PF
"""
import sys, re, shutil
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
BACKUP = ENGINE.with_suffix('.ts.bak.v38g')


def main():
    # Backup
    if not BACKUP.exists():
        shutil.copy2(ENGINE, BACKUP)
        print(f"Backup created: {BACKUP}")
    else:
        print(f"Backup already exists: {BACKUP}")

    src = ENGINE.read_text()
    original = src

    edits = []

    # ─── EDIT 1: Add partial1_done / partial2_done to PaperPosition interface ───
    old_iface = """  // v37e: lock-profit + partial TP + trailing state
  lock_done?: boolean
  partial_done?: boolean
  trail_active?: boolean
  max_favorable_price?: number
  initial_atr?: number
  initial_sl_distance?: number"""

    new_iface = """  // v43a: lock-profit + multi-partial TP (15% @0.5R + 25% @1.0R) + trailing state
  lock_done?: boolean
  partial_done?: boolean        // legacy (kept for back-compat with old trades)
  partial1_done?: boolean       // v43a: first partial at +0.5R (close 15%)
  partial2_done?: boolean       // v43a: second partial at +1.0R (close 25%, enable trail)
  trail_active?: boolean
  max_favorable_price?: number
  initial_atr?: number
  initial_sl_distance?: number"""

    if old_iface in src:
        edits.append(('PaperPosition interface', old_iface, new_iface))
    else:
        print("⚠️ EDIT 1 anchor not found (interface)")
        sys.exit(1)

    # ─── EDIT 2: Replace v38g block with v43a multi-partial logic ───
    old_v38g_block = """      // ─── v38g: Lock profit + Partial TP + Trailing (before SL/TP check) ───
      // These run on every tick to manage open positions proactively.
      // Lock: move SL to entry+0.2R when +0.5R reached (locks small profit)
      // Partial: close 40% at +0.7R, then enable trailing on remainder
      // Trail: 0.5 ATR trailing stop on remainder (overrides TP)
      // 8-seed validation: WR 66.7%, P&L +30.97, Profitable 88%, MaxDD 0.26%
      if (pos.initial_atr && pos.initial_sl_distance && pos.initial_sl_distance > 0) {
        const rMultiple = isLong
          ? (price - pos.entry_price) / pos.initial_sl_distance
          : (pos.entry_price - price) / pos.initial_sl_distance

        // Track MFE (max favorable price) for trailing
        if (pos.max_favorable_price === undefined) pos.max_favorable_price = pos.entry_price
        if (isLong && price > pos.max_favorable_price) pos.max_favorable_price = price
        if (!isLong && price < pos.max_favorable_price) pos.max_favorable_price = price

        // 1. Lock profit at +0.5R → move SL to entry+0.2R (v38g: 0.4→0.5)
        if (!pos.lock_done && rMultiple >= 0.5) {
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

        // 2. Partial TP at +0.7R → close 40%, enable trailing on remainder (v38g)
        if (!pos.partial_done && rMultiple >= 0.7) {
          // Close 40% of position at market
          const partialPct = 0.40
          const partialQty = pos.qty * partialPct
          if (partialQty > 0.0001) {
            const partialResult = isLong
              ? this.marketSell(sym, partialQty * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty * pos.entry_price, pos.strategy)
            if (partialResult.success) {
              pos.qty -= partialQty
              console.log(`[Paper/v38g] ${sym} PARTIAL_TP 40% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial_done = true
          pos.trail_active = true
          // Set initial trail SL at current price - 0.5 ATR (v38g: 0.6→0.5)
          const trailDist = pos.initial_atr * 0.5
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
          const trailDist = pos.initial_atr * 0.5
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
      }"""

    new_v43a_block = """      // ─── v43a: Lock profit + Multi-Partial TP + Trailing (before SL/TP check) ───
      // These run on every tick to manage open positions proactively.
      // Lock:     move SL to entry+0.2R when +0.5R reached (locks small profit)
      // Partial1: close 15% at +0.5R (small profit-booking)
      // Partial2: close 25% at +1.0R, then enable trailing on remainder (60%)
      // Trail:    0.4 ATR trailing stop on remainder (overrides TP)
      // 12-seed validation: WR 72.5%, P&L +13.92, Profitable 67%, MaxDD 0.29%, AvgR +0.61
      // vs v38g:  WR 61.8%, P&L +11.01, Profitable 67%, MaxDD 0.31%, AvgR +0.41
      // → +10.7pp WR, +27% P&L, +0.20 AvgR, -0.02pp MaxDD
      if (pos.initial_atr && pos.initial_sl_distance && pos.initial_sl_distance > 0) {
        const rMultiple = isLong
          ? (price - pos.entry_price) / pos.initial_sl_distance
          : (pos.entry_price - price) / pos.initial_sl_distance

        // Track MFE (max favorable price) for trailing
        if (pos.max_favorable_price === undefined) pos.max_favorable_price = pos.entry_price
        if (isLong && price > pos.max_favorable_price) pos.max_favorable_price = price
        if (!isLong && price < pos.max_favorable_price) pos.max_favorable_price = price

        // 1. Lock profit at +0.5R → move SL to entry+0.2R (v43a: same as v38g)
        if (!pos.lock_done && rMultiple >= 0.5) {
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

        // 2a. Partial TP1 at +0.5R → close 15% (v43a: NEW — first profit-booking)
        if (!pos.partial1_done && rMultiple >= 0.5) {
          const partialPct1 = 0.15
          const partialQty1 = pos.qty * partialPct1
          if (partialQty1 > 0.0001) {
            const partialResult1 = isLong
              ? this.marketSell(sym, partialQty1 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty1 * pos.entry_price, pos.strategy)
            if (partialResult1.success) {
              pos.qty -= partialQty1
              console.log(`[Paper/v43a] ${sym} PARTIAL_TP1 15% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial1_done = true
        }

        // 2b. Partial TP2 at +1.0R → close 25%, enable trailing on remainder (v43a: NEW)
        if (!pos.partial2_done && rMultiple >= 1.0) {
          const partialPct2 = 0.25
          // Compute 25% of ORIGINAL qty to avoid compounding with partial1
          // (pos.qty already reduced by partial1; we want 25% of original)
          // Use initial_qty if tracked, else approximate via 0.25 / 0.85 * pos.qty
          const remainingPctAfter1 = 1 - 0.15  // 0.85
          const partialQty2 = (pos.qty * partialPct2) / remainingPctAfter1
          if (partialQty2 > 0.0001 && partialQty2 <= pos.qty) {
            const partialResult2 = isLong
              ? this.marketSell(sym, partialQty2 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty2 * pos.entry_price, pos.strategy)
            if (partialResult2.success) {
              pos.qty -= partialQty2
              console.log(`[Paper/v43a] ${sym} PARTIAL_TP2 25% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial2_done = true
          pos.trail_active = true
          // Set initial trail SL at current price - 0.4 ATR (v43a: 0.5→0.4, tighter)
          const trailDist = pos.initial_atr * 0.4
          if (isLong) {
            const newSL = price - trailDist
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
          } else {
            const newSL = price + trailDist
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
          }
        }

        // 3. Update trailing stop if active (v43a: 0.4 ATR, was 0.5)
        if (pos.trail_active && pos.max_favorable_price) {
          const trailDist = pos.initial_atr * 0.4
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
      }"""

    if old_v38g_block in src:
        edits.append(('v38g block → v43a multi-partial', old_v38g_block, new_v43a_block))
    else:
        print("⚠️ EDIT 2 anchor not found (v38g block)")
        sys.exit(1)

    # ─── EDIT 3: Update Strategy A comment (v38g → v43a) ───
    old_a_comment = "          // v38g: SL 1.4 ATR + lock 0.5R + partial 40% at 0.7R + trail 0.5 ATR + ATR floor 0.58%"
    new_a_comment = "          // v43a: SL 1.4 ATR + lock 0.5R + partial1 15% at 0.5R + partial2 25% at 1.0R + trail 0.4 ATR + ATR floor 0.58%"
    if old_a_comment in src:
        edits.append(('Strategy A comment', old_a_comment, new_a_comment))
    else:
        print("⚠️ EDIT 3 anchor not found (Strategy A comment)")
        sys.exit(1)

    # ─── EDIT 4: Update Strategy B comment + state init (v38g → v43a) ───
    old_b_comment = "          // v38g: SL 1.4 ATR + v38g state init (lock 0.5R / partial 40% at 0.7R / trail 0.5 ATR)"
    new_b_comment = "          // v43a: SL 1.4 ATR + v43a state init (lock 0.5R / partial1 15% at 0.5R / partial2 25% at 1.0R / trail 0.4 ATR)"
    if old_b_comment in src:
        edits.append(('Strategy B comment', old_b_comment, new_b_comment))
    else:
        print("⚠️ EDIT 4 anchor not found (Strategy B comment)")
        sys.exit(1)

    # ─── EDIT 5: Update Strategy A state init — add partial1_done / partial2_done ───
    # Find the A block init pattern
    # Looking at line 1043 area — let's find the actual init code
    # The init is typically: lock_done: false, partial_done: false, trail_active: false, ...

    # Actually since v38g already inits lock_done/partial_done/trail_active, we just need to
    # add partial1_done: false, partial2_done: false alongside
    # Let's find all occurrences of "partial_done: false" and add the new fields after

    # This is tricky because we need to add fields. Let's use a regex.
    # The pattern is likely: lock_done: false, partial_done: false, trail_active: false
    # We'll replace with: lock_done: false, partial1_done: false, partial2_done: false, partial_done: false, trail_active: false

    pattern = re.compile(r'(lock_done:\s*false,\s*\n\s*)partial_done:\s*false,\s*\n(\s*)trail_active:\s*false')
    matches = list(pattern.finditer(src))
    if len(matches) >= 1:
        # Replace each occurrence
        new_src = pattern.sub(
            lambda m: f"{m.group(1)}partial1_done: false,\n{m.group(2)}partial2_done: false,\n{m.group(2)}partial_done: false,\n{m.group(2)}trail_active: false",
            src
        )
        if new_src != src:
            src = new_src
            print(f"✅ EDIT 5: Added partial1_done/partial2_done to {len(matches)} position init(s)")
        else:
            print("⚠️ EDIT 5: pattern matched but no change made")
    else:
        # Try alternate pattern — maybe inline
        pattern2 = re.compile(r'lock_done:\s*false,\s*partial_done:\s*false,\s*trail_active:\s*false')
        matches2 = list(pattern2.finditer(src))
        if matches2:
            new_src = pattern2.sub(
                'lock_done: false, partial1_done: false, partial2_done: false, partial_done: false, trail_active: false',
                src
            )
            src = new_src
            print(f"✅ EDIT 5 (inline): Added partial1_done/partial2_done to {len(matches2)} position init(s)")
        else:
            print("⚠️ EDIT 5: no position init pattern found — checking source...")

    # Apply edits 1-4 (the structural ones)
    for label, old, new in edits:
        if old in src:
            src = src.replace(old, new, 1)
            print(f"✅ Applied: {label}")
        else:
            print(f"⚠️ Could not apply (anchor missing after prior edit): {label}")
            sys.exit(1)

    # Verify balance
    braces_open = src.count('{')
    braces_close = src.count('}')
    parens_open = src.count('(')
    parens_close = src.count(')')
    brackets_open = src.count('[')
    brackets_close = src.count(']')
    print(f"\nBraces: {braces_open}/{braces_close} {'OK' if braces_open == braces_close else 'MISMATCH!'}")
    print(f"Parens: {parens_open}/{parens_close} {'OK' if parens_open == parens_close else 'MISMATCH!'}")
    print(f"Brackets: {brackets_open}/{brackets_close} {'OK' if brackets_open == brackets_close else 'MISMATCH!'}")

    # Verify v43a markers
    v43a_count = src.count('v43a')
    v38g_count = src.count('v38g')
    print(f"\nv43a markers: {v43a_count}")
    print(f"v38g markers remaining: {v38g_count} (should be 0 in active code)")

    if src == original:
        print("\n⚠️ No changes made — aborting")
        sys.exit(1)

    ENGINE.write_text(src)
    print(f"\n✅ Engine updated: {ENGINE}")
    print(f"   Size: {len(src)} bytes ({len(src.splitlines())} lines)")


if __name__ == "__main__":
    main()
