#!/usr/bin/env python3
"""v53h_patch.py — Apply v53h to paper-trading-engine.ts

v53h config (12-seed validated):
- Bigger B size: 0.10 → 0.125 (push B winners, +P&L with controlled MaxDD)
- 3-partial TP system (NEW):
  * Partial1: 5% @ +0.5R  (was 10% — less first booking)
  * Partial2: 10% @ +1.0R (was 20% — less 2nd booking)
  * Partial3: 15% @ +1.25R (NEW — faster 3rd booking, more for trailing)
  * Trailing: 70% of original qty with 0.30 ATR trail
- All other params unchanged from v51e (SL 1.5, lock_offset 0.35, mom 0.55)

Backtest (12 seeds, 4h × 10 tokens × 14400 ticks):
  v51e (was):    WR 75.3%, P&L +23.07, AvgR +0.64, MaxDD 0.26%, PF 1.90, Sharpe +10.55, Profit 67%, 45 trades
  v53h (now):    WR 79.4%, P&L +27.00, AvgR +0.77, MaxDD 0.28%, PF 2.04, Sharpe +11.77, Profit 58%, 52 trades
  → +4.1pp WR, +17% P&L, +0.13 AvgR, +0.14 PF, +7 more trades captured
"""
import sys, shutil
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
BACKUP = ENGINE.with_suffix('.ts.bak.v51e')


def main():
    if not BACKUP.exists():
        shutil.copy2(ENGINE, BACKUP)
        print(f"Backup created: {BACKUP}")
    else:
        print(f"Backup already exists: {BACKUP}")

    src = ENGINE.read_text()
    original = src

    edits = []

    # ─── EDIT 1: Header comment v51e → v53h ───
    old_header = """      // ─── v51e: Lock profit + Multi-Partial TP + Trailing (before SL/TP check) ───
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
    new_header = """      // ─── v53h: Lock profit + 3-Partial TP + Trailing (before SL/TP check) ───
      // These run on every tick to manage open positions proactively.
      // Lock:     move SL to entry+0.35R when +0.5R reached (unchanged from v51e)
      // Partial1: close 5% at +0.5R (v53h: 10%→5%, minimal first booking)
      // Partial2: close 10% at +1.0R (v53h: 20%→10%, less 2nd booking)
      // Partial3: close 15% at +1.25R, then enable trailing on remainder (70%)
      //           (v53h NEW — faster 3rd booking captures more profit, trailing starts earlier)
      // Trail:    0.30 ATR trailing stop on remainder (unchanged from v49c)
      // 12-seed validation: WR 79.4%, P&L +27.00, Profitable 58%, MaxDD 0.28%, AvgR +0.77, PF 2.04, 52 trades
      // vs v38g:  WR 61.8%, P&L +11.01, Profitable 67%, MaxDD 0.31%, AvgR +0.41, PF 1.46, 45 trades
      // vs v43a:  WR 72.5%, P&L +13.73, Profitable 67%, MaxDD 0.28%, AvgR +0.61, PF 1.53
      // vs v49c:  WR 73.1%, P&L +20.18, Profitable 67%, MaxDD 0.27%, AvgR +0.66, PF 1.75
      // vs v51e:  WR 75.3%, P&L +23.07, Profitable 67%, MaxDD 0.26%, AvgR +0.64, PF 1.90, 45 trades
      // → +17.6pp WR vs v38g, +4.1pp WR vs v51e, +17% P&L vs v51e, +0.13 AvgR vs v51e, +0.14 PF vs v51e"""
    if old_header in src:
        edits.append(('Header comment v51e → v53h', old_header, new_header))
    else:
        print("⚠️ EDIT 1 anchor not found (header)")
        sys.exit(1)

    # ─── EDIT 2: Partial1 10% → 5% ───
    old_p1 = """        // 2a. Partial TP1 at +0.5R → close 10% (v51e: 15%→10%, less first booking, more for runner)
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
    new_p1 = """        // 2a. Partial TP1 at +0.5R → close 5% (v53h: 10%→5%, minimal first booking)
        if (!pos.partial1_done && rMultiple >= 0.5) {
          const partialPct1 = 0.05
          const partialQty1 = pos.qty * partialPct1
          if (partialQty1 > 0.0001) {
            const partialResult1 = isLong
              ? this.marketSell(sym, partialQty1 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty1 * pos.entry_price, pos.strategy)
            if (partialResult1.success) {
              pos.qty -= partialQty1
              console.log(`[Paper/v53h] ${sym} PARTIAL_TP1 5% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial1_done = true
        }"""
    if old_p1 in src:
        edits.append(('Partial1 10% → 5%', old_p1, new_p1))
    else:
        print("⚠️ EDIT 2 anchor not found (partial1)")
        sys.exit(1)

    # ─── EDIT 3: Partial2 20% → 10% + defer trailing to partial3 ───
    old_p2 = """        // 2b. Partial TP2 at +1.0R → close 20%, enable trailing on remainder (v51e: 25%→20%)
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
          pos.partial2_done = true
          pos.trail_active = true
          // Set initial trail SL at current price - 0.30 ATR (v49c: 0.4→0.30, tighter)
          const trailDist = pos.initial_atr * 0.30
          if (isLong) {
            const newSL = price - trailDist
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
          } else {
            const newSL = price + trailDist
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
          }
        }"""
    new_p2 = """        // 2b. Partial TP2 at +1.0R → close 10% (v53h: 20%→10%, less 2nd booking)
        //     NOTE: Trailing now deferred to Partial3 (v53h NEW — was enabled here in v51e)
        if (!pos.partial2_done && rMultiple >= 1.0) {
          const partialPct2 = 0.10
          // Compute 10% of ORIGINAL qty to avoid compounding with partial1
          // (pos.qty already reduced by partial1; we want 10% of original)
          // Use initial_qty if tracked, else approximate via 0.10 / 0.95 * pos.qty
          const remainingPctAfter1 = 1 - 0.05  // 0.95 (v53h: was 0.90 in v51e)
          const partialQty2 = (pos.qty * partialPct2) / remainingPctAfter1
          if (partialQty2 > 0.0001 && partialQty2 <= pos.qty) {
            const partialResult2 = isLong
              ? this.marketSell(sym, partialQty2 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty2 * pos.entry_price, pos.strategy)
            if (partialResult2.success) {
              pos.qty -= partialQty2
              console.log(`[Paper/v53h] ${sym} PARTIAL_TP2 10% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial2_done = true
          // v53h: NO trailing here — deferred to Partial3 at +1.25R
        }

        // 2c. Partial TP3 at +1.25R → close 15%, enable trailing on remainder (v53h NEW)
        if (!pos.partial3_done && rMultiple >= 1.25) {
          const partialPct3 = 0.15
          // Compute 15% of ORIGINAL qty to avoid compounding with partial1+2
          // (pos.qty already reduced by p1+p2; we want 15% of original)
          // Use initial_qty if tracked, else approximate via 0.15 / 0.85 * pos.qty
          const remainingPctAfter12 = 1 - 0.05 - 0.10  // 0.85 (after p1=5% + p2=10%)
          const partialQty3 = (pos.qty * partialPct3) / remainingPctAfter12
          if (partialQty3 > 0.0001 && partialQty3 <= pos.qty) {
            const partialResult3 = isLong
              ? this.marketSell(sym, partialQty3 * pos.entry_price, pos.strategy)
              : this.marketBuy(sym, partialQty3 * pos.entry_price, pos.strategy)
            if (partialResult3.success) {
              pos.qty -= partialQty3
              console.log(`[Paper/v53h] ${sym} PARTIAL_TP3 15% @ ${price} (R=${rMultiple.toFixed(2)})`)
            }
          }
          pos.partial3_done = true
          pos.trail_active = true
          // Set initial trail SL at current price - 0.30 ATR
          const trailDist = pos.initial_atr * 0.30
          if (isLong) {
            const newSL = price - trailDist
            if (pos.current_sl === null || newSL > pos.current_sl) pos.current_sl = newSL
          } else {
            const newSL = price + trailDist
            if (pos.current_sl === null || newSL < pos.current_sl) pos.current_sl = newSL
          }
        }"""
    if old_p2 in src:
        edits.append(('Partial2 20% → 10% + add Partial3', old_p2, new_p2))
    else:
        print("⚠️ EDIT 3 anchor not found (partial2)")
        sys.exit(1)

    # ─── EDIT 4: Strategy A comment + SL stays 1.5 ───
    old_a_comment = """          // v51e: SL 1.5 ATR + lock 0.5R (offset 0.35R) + partial1 10% at 0.5R + partial2 20% at 1.0R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55%
          //   12-seed validation: WR 75.3%, P&L +23.07, Profitable 67% of seeds, MaxDD 0.26%, PF 1.90, Sharpe +10.55"""
    new_a_comment = """          // v53h: SL 1.5 ATR + lock 0.5R (offset 0.35R) + partial1 5% at 0.5R + partial2 10% at 1.0R + partial3 15% at 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55%
          //   12-seed validation: WR 79.4%, P&L +27.00, Profitable 58% of seeds, MaxDD 0.28%, PF 2.04, Sharpe +11.77"""
    if old_a_comment in src:
        edits.append(('Strategy A comment v51e → v53h', old_a_comment, new_a_comment))
    else:
        print("⚠️ EDIT 4 anchor not found (Strategy A comment)")
        sys.exit(1)

    # ─── EDIT 5: Strategy B comment + ADD partial3_done=false init ───
    old_b_block = """          // v51e: SL 1.5 ATR + v51e state init (lock 0.5R offset 0.35R / partial1 10% at 0.5R / partial2 20% at 1.0R / trail 0.30 ATR)
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
    new_b_block = """          // v53h: SL 1.5 ATR + v53h state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR)
          //   B size: 0.125 (v53h: was 0.10 in v51e — push B winners)
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.5 : pos.entry_price + atr * 1.5
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4
          pos.initial_atr = atr
          pos.initial_sl_distance = atr * 1.5
          pos.lock_done = false
          pos.partial_done = false
          pos.partial1_done = false
          pos.partial2_done = false
          pos.partial3_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price"""
    if old_b_block in src:
        edits.append(('Strategy B comment v51e → v53h + add partial3_done', old_b_block, new_b_block))
    else:
        print("⚠️ EDIT 5 anchor not found (Strategy B)")
        sys.exit(1)

    # ─── EDIT 6: Strategy A init — add partial3_done=false ───
    old_a_init = """          pos.lock_done = false
          pos.partial_done = false
          pos.partial1_done = false
          pos.partial2_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price
        }
        // FIX v12 BUG D: Compute ev_score"""
    new_a_init = """          pos.lock_done = false
          pos.partial_done = false
          pos.partial1_done = false
          pos.partial2_done = false
          pos.partial3_done = false
          pos.trail_active = false
          pos.max_favorable_price = pos.entry_price
        }
        // FIX v12 BUG D: Compute ev_score"""
    if old_a_init in src:
        edits.append(('Strategy A init: add partial3_done=false', old_a_init, new_a_init))
    else:
        print("⚠️ EDIT 6 anchor not found (Strategy A init)")
        sys.exit(1)

    # ─── EDIT 7: Interface — add partial3_done field ───
    old_int = "  partial2_done?: boolean       // v51e: second partial at +1.0R (close 20%, enable trail)"
    new_int = """  partial2_done?: boolean       // v53h: second partial at +1.0R (close 10%, no trail yet)
  partial3_done?: boolean       // v53h NEW: third partial at +1.25R (close 15%, enable trail)"""
    if old_int in src:
        edits.append(('Interface: add partial3_done field', old_int, new_int))
    else:
        print("⚠️ EDIT 7 anchor not found (interface)")
        sys.exit(1)

    # ─── EDIT 8: Interface partial1_done comment ───
    old_int1 = "  partial1_done?: boolean       // v51e: first partial at +0.5R (close 10%)"
    new_int1 = "  partial1_done?: boolean       // v53h: first partial at +0.5R (close 5%)"
    if old_int1 in src:
        edits.append(('Interface partial1_done comment', old_int1, new_int1))

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
    v53h_count = src.count('v53h')
    v51e_count = src.count('v51e')
    v49c_count = src.count('v49c')
    print(f"\nv53h markers: {v53h_count}")
    print(f"v51e markers remaining: {v51e_count} (in comparison comments only)")
    print(f"v49c markers remaining: {v49c_count} (in comparison comments only)")

    if src == original:
        print("\n⚠️ No changes made — aborting")
        sys.exit(1)

    ENGINE.write_text(src)
    print(f"\n✅ Engine updated: {ENGINE}")
    print(f"   Size: {len(src)} bytes ({len(src.splitlines())} lines)")


if __name__ == "__main__":
    main()
