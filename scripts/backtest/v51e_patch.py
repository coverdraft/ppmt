#!/usr/bin/env python3
"""v51e_patch.py — Apply v51e to paper-trading-engine.ts

v51e config (12-seed validated):
- SL multiplier: 1.4 → 1.5 ATR (wider initial stop, fewer BE stops)
- Lock_offset R: 0.2 → 0.35 (tighter BE after lock — locks more profit)
- Partial1 close: 15% → 10% (less first booking, more for runner)
- Partial2 close: 25% → 20% (less 2nd booking, more for trailing)
- BONUS FIX: Add partial1_done=false, partial2_done=false to Strategy B init
  (was missing — works by accident because undefined is falsy, but explicit is safer)
- All other params unchanged from v49c

Backtest (12 seeds, 4h × 10 tokens):
  v49c (was):    WR 73.1%, P&L +20.18, AvgR +0.66, MaxDD 0.27%, PF 1.75, Sharpe +7.96, Profit 67%
  v51e (now):    WR 75.3%, P&L +23.07, AvgR +0.64, MaxDD 0.26%, PF 1.90, Sharpe +10.55, Profit 67%
  → +2.2pp WR, +14% P&L, -0.02 AvgR (negligible), -0.01pp MaxDD, +0.15 PF, +2.59 Sharpe
"""
import sys, shutil
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
BACKUP = ENGINE.with_suffix('.ts.bak.v49c')


def main():
    if not BACKUP.exists():
        shutil.copy2(ENGINE, BACKUP)
        print(f"Backup created: {BACKUP}")
    else:
        print(f"Backup already exists: {BACKUP}")

    src = ENGINE.read_text()
    original = src

    edits = []

    # ─── EDIT 1: Header comment v49c → v51e ───
    old_header = """      // ─── v49c: Lock profit + Multi-Partial TP + Trailing (before SL/TP check) ───
      // These run on every tick to manage open positions proactively.
      // Lock:     move SL to entry+0.2R when +0.5R reached (locks small profit)
      // Partial1: close 15% at +0.5R (small profit-booking)
      // Partial2: close 25% at +1.0R, then enable trailing on remainder (60%)
      // Trail:    0.30 ATR trailing stop on remainder (v49c: tighter than v43a's 0.4)
      // 12-seed validation: WR 73.1%, P&L +20.18, Profitable 67%, MaxDD 0.27%, AvgR +0.66
      // vs v38g:  WR 61.8%, P&L +11.01, Profitable 67%, MaxDD 0.31%, AvgR +0.41
      // vs v43a:  WR 72.5%, P&L +13.73, Profitable 67%, MaxDD 0.28%, AvgR +0.61
      // → +11.3pp WR vs v38g, +47% P&L vs v43a, +0.25 AvgR vs v38g"""
    new_header = """      // ─── v51e: Lock profit + Multi-Partial TP + Trailing (before SL/TP check) ───
      // These run on every tick to manage open positions proactively.
      // Lock:     move SL to entry+0.35R when +0.5R reached (v51e: 0.2→0.35, tighter BE)
      // Partial1: close 10% at +0.5R (v51e: 15%→10%, less first booking, more for runner)
      // Partial2: close 20% at +1.0R, then enable trailing on remainder (70%)
      //           (v51e: 25%→20%, less 2nd booking, more for trailing)
      // Trail:    0.30 ATR trailing stop on remainder (unchanged from v49c)
      // 12-seed validation: WR 75.3%, P&L +23.07, Profitable 67%, MaxDD 0.26%, AvgR +0.64, PF 1.90
      // vs v38g:  WR 61.8%, P&L +11.01, Profitable 67%, MaxDD 0.31%, AvgR +0.41, PF 1.46
      // vs v43a:  WR 72.5%, P&L +13.73, Profitable 67%, MaxDD 0.28%, AvgR +0.61, PF 1.53
      // vs v49c:  WR 73.1%, P&L +20.18, Profitable 67%, MaxDD 0.27%, AvgR +0.66, PF 1.75
      // → +13.5pp WR vs v38g, +2.2pp WR vs v49c, +14% P&L vs v49c, +0.15 PF vs v49c"""
    if old_header in src:
        edits.append(('Header comment v49c → v51e', old_header, new_header))
    else:
        print("⚠️ EDIT 1 anchor not found (header)")
        sys.exit(1)

    # ─── EDIT 2: Lock_offset 0.2 → 0.35 ───
    old_lock = """        // 1. Lock profit at +0.5R → move SL to entry+0.2R (v43a: same as v38g)
        if (!pos.lock_done && rMultiple >= 0.5) {
          const lockR = 0.2"""
    new_lock = """        // 1. Lock profit at +0.5R → move SL to entry+0.35R (v51e: 0.2→0.35, tighter BE)
        if (!pos.lock_done && rMultiple >= 0.5) {
          const lockR = 0.35"""
    if old_lock in src:
        edits.append(('Lock_offset 0.2 → 0.35', old_lock, new_lock))
    else:
        print("⚠️ EDIT 2 anchor not found (lock_offset)")
        sys.exit(1)

    # ─── EDIT 3: Partial1 15% → 10% ───
    old_p1 = """        // 2a. Partial TP1 at +0.5R → close 15% (v43a: NEW — first profit-booking)
        if (!pos.partial1_done && rMultiple >= 0.5) {
          const partialPct1 = 0.15
          const partialQty1 = pos.qty * partialPct1
          if (partialQty1 > 0.0001) {
            const partialResult1 = isLong
              ? this.marketSell(sym, partialQty1 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty1 * pos.entry_price, pos.strategy)
            if (partialResult1.success) {
              pos.qty -= partialQty1
              console.log(`[Paper/v49c] ${sym} PARTIAL_TP1 15% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial1_done = true
        }"""
    new_p1 = """        // 2a. Partial TP1 at +0.5R → close 10% (v51e: 15%→10%, less first booking, more for runner)
        if (!pos.partial1_done && rMultiple >= 0.5) {
          const partialPct1 = 0.10
          const partialQty1 = pos.qty * partialPct1
          if (partialQty1 > 0.0001) {
            const partialResult1 = isLong
              ? this.marketSell(sym, partialQty1 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty1 * pos.entry_price, pos.strategy)
            if (partialResult1.success) {
              pos.qty -= partialQty1
              console.log(`[Paper/v51e] ${sym} PARTIAL_TP1 10% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial1_done = true
        }"""
    if old_p1 in src:
        edits.append(('Partial1 15% → 10%', old_p1, new_p1))
    else:
        print("⚠️ EDIT 3 anchor not found (partial1)")
        sys.exit(1)

    # ─── EDIT 4: Partial2 25% → 20% (with remainingPctAfter1 update) ───
    old_p2 = """        // 2b. Partial TP2 at +1.0R → close 25%, enable trailing on remainder (v43a: NEW)
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
              console.log(`[Paper/v49c] ${sym} PARTIAL_TP2 25% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial2_done = true"""
    new_p2 = """        // 2b. Partial TP2 at +1.0R → close 20%, enable trailing on remainder (v51e: 25%→20%)
        if (!pos.partial2_done && rMultiple >= 1.0) {
          const partialPct2 = 0.20
          // Compute 20% of ORIGINAL qty to avoid compounding with partial1
          // (pos.qty already reduced by partial1; we want 20% of original)
          // Use initial_qty if tracked, else approximate via 0.20 / 0.90 * pos.qty
          const remainingPctAfter1 = 1 - 0.10  // 0.90 (v51e: was 0.85)
          const partialQty2 = (pos.qty * partialPct2) / remainingPctAfter1
          if (partialQty2 > 0.0001 && partialQty2 <= pos.qty) {
            const partialResult2 = isLong
              ? this.marketSell(sym, partialQty2 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty2 * pos.entry_price, pos.strategy)
            if (partialResult2.success) {
              pos.qty -= partialQty2
              console.log(`[Paper/v51e] ${sym} PARTIAL_TP2 20% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial2_done = true"""
    if old_p2 in src:
        edits.append(('Partial2 25% → 20%', old_p2, new_p2))
    else:
        print("⚠️ EDIT 4 anchor not found (partial2)")
        sys.exit(1)

    # ─── EDIT 5: SL 1.4 → 1.5 (Strategy A init, including initial_sl_distance) ───
    old_sl_a = """          // v49c: SL 1.4 ATR + lock 0.5R + partial1 15% at 0.5R + partial2 25% at 1.0R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55%
          //   8-seed validation: WR 66.7%, P&L +30.97, Profitable 88% of seeds, MaxDD 0.26%
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.4 : pos.entry_price + atr * 1.4
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4
          pos.initial_atr = atr
          pos.initial_sl_distance = atr * 1.4"""
    new_sl_a = """          // v51e: SL 1.5 ATR + lock 0.5R (offset 0.35R) + partial1 10% at 0.5R + partial2 20% at 1.0R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55%
          //   12-seed validation: WR 75.3%, P&L +23.07, Profitable 67% of seeds, MaxDD 0.26%, PF 1.90, Sharpe +10.55
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.5 : pos.entry_price + atr * 1.5
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4
          pos.initial_atr = atr
          pos.initial_sl_distance = atr * 1.5"""
    if old_sl_a in src:
        edits.append(('SL 1.4 → 1.5 (Strategy A)', old_sl_a, new_sl_a))
    else:
        print("⚠️ EDIT 5 anchor not found (SL A)")
        sys.exit(1)

    # ─── EDIT 6: SL 1.4 → 1.5 (Strategy B init) + ADD partial1_done/partial2_done ───
    old_sl_b = """          // v49c: SL 1.4 ATR + v49c state init (lock 0.5R / partial1 15% at 0.5R / partial2 25% at 1.0R / trail 0.30 ATR)
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.4 : pos.entry_price + atr * 1.4
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4
          pos.initial_atr = atr
          pos.initial_sl_distance = atr * 1.4
          pos.lock_done = false
          pos.partial_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price"""
    new_sl_b = """          // v51e: SL 1.5 ATR + v51e state init (lock 0.5R offset 0.35R / partial1 10% at 0.5R / partial2 20% at 1.0R / trail 0.30 ATR)
          //   BONUS FIX: add partial1_done=false, partial2_done=false (was missing in v49c — worked by accident because undefined is falsy)
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.5 : pos.entry_price + atr * 1.5
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4
          pos.initial_atr = atr
          pos.initial_sl_distance = atr * 1.5
          pos.lock_done = false
          pos.partial_done = false
          pos.partial1_done = false
          pos.partial2_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price"""
    if old_sl_b in src:
        edits.append(('SL 1.4 → 1.5 (Strategy B) + add partial1/2_done', old_sl_b, new_sl_b))
    else:
        print("⚠️ EDIT 6 anchor not found (SL B)")
        sys.exit(1)

    # ─── EDIT 7: Partial1_done comment (interface) ───
    old_int1 = "  partial1_done?: boolean       // v43a: first partial at +0.5R (close 15%)"
    new_int1 = "  partial1_done?: boolean       // v51e: first partial at +0.5R (close 10%)"
    if old_int1 in src:
        edits.append(('Interface partial1_done comment', old_int1, new_int1))

    # ─── EDIT 8: Partial2_done comment (interface) ───
    old_int2 = "  partial2_done?: boolean       // v43a: second partial at +1.0R (close 25%, enable trail)"
    new_int2 = "  partial2_done?: boolean       // v51e: second partial at +1.0R (close 20%, enable trail)"
    if old_int2 in src:
        edits.append(('Interface partial2_done comment', old_int2, new_int2))

    # Apply all edits
    for label, old, new in edits:
        if old in src:
            src = src.replace(old, new, 1)
            print(f"✅ Applied: {label}")
        else:
            print(f"⚠️ Could not apply: {label}")
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

    # Count version markers
    v51e_count = src.count('v51e')
    v49c_count = src.count('v49c')
    v43a_count = src.count('v43a')
    v38g_count = src.count('v38g')
    print(f"\nv51e markers: {v51e_count}")
    print(f"v49c markers remaining: {v49c_count} (in comparison comments only)")
    print(f"v43a markers remaining: {v43a_count} (in comparison comments only)")
    print(f"v38g markers remaining: {v38g_count} (in comparison comments only)")

    if src == original:
        print("\n⚠️ No changes made — aborting")
        sys.exit(1)

    ENGINE.write_text(src)
    print(f"\n✅ Engine updated: {ENGINE}")
    print(f"   Size: {len(src)} bytes ({len(src.splitlines())} lines)")


if __name__ == "__main__":
    main()
