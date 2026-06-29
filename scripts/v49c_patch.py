#!/usr/bin/env python3
"""v49c_patch.py — Apply v49c (momentum 0.55 + trail 0.30) to paper-trading-engine.ts

V49c config (12-seed validated):
- Strategy A momentum_min: 0.40 → 0.55 (stricter — only strong signals)
- Trail distance: 0.4 → 0.30 ATR (tighter — locks more profit)
- All other params unchanged from v43a

Backtest (12 seeds, 4h × 10 tokens):
  v43a (was):    WR 72.5%, P&L +13.73, AvgR +0.61, MaxDD 0.28%, PF 1.53, Profit 67%
  v49c (now):    WR 73.1%, P&L +20.18, AvgR +0.66, MaxDD 0.27%, PF 1.75, Profit 67%
  → +0.6pp WR, +47% P&L, +0.05 AvgR, -0.01pp MaxDD, +0.22 PF
"""
import sys, shutil
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
BACKUP = ENGINE.with_suffix('.ts.bak.v43a')


def main():
    if not BACKUP.exists():
        shutil.copy2(ENGINE, BACKUP)
        print(f"Backup created: {BACKUP}")
    else:
        print(f"Backup already exists: {BACKUP}")

    src = ENGINE.read_text()
    original = src

    edits = []

    # ─── EDIT 1: Strategy A momentum 0.40 → 0.55 ───
    old_mom = """        // v31b: momentum 0.40% (was 0.15) + RSI 25-75 filter for quality
        // Backtest: 0.15 gave WR 41%, 0.40 + RSI filter gives WR 63.5%
        const recent = hist.slice(-30)
        const recentMomentum = ((recent[recent.length - 1].price - recent[0].price) / recent[0].price) * 100
        if (Math.abs(recentMomentum) < 0.40) return null  // need ≥0.40% move (strong signals only)"""
    new_mom = """        // v49c: momentum 0.55% (was 0.40 v31b) — stricter quality filter
        // Backtest: 0.15 gave WR 41%, 0.40 + RSI filter gives WR 63.5%, 0.55 gives WR 73.1% (12-seed)
        const recent = hist.slice(-30)
        const recentMomentum = ((recent[recent.length - 1].price - recent[0].price) / recent[0].price) * 100
        if (Math.abs(recentMomentum) < 0.55) return null  // v49c: need ≥0.55% move (was 0.40)"""
    if old_mom in src:
        edits.append(('Strategy A momentum 0.40 → 0.55', old_mom, new_mom))
    else:
        # Try alternate patterns
        print("⚠️ EDIT 1 anchor not found — searching alternate patterns...")
        # Maybe slight different wording
        import re
        m = re.search(r'// v31b: momentum 0\.40%.*?if \(absMomentum < 0\.40\) return.*?signals', src, re.DOTALL)
        if m:
            print(f"  Found at: {m.start()}-{m.end()}")
            print(f"  Match: {repr(src[m.start():m.end()][:200])}")
        sys.exit(1)

    # ─── EDIT 2: Trail distance 0.4 → 0.30 (both initial set and update) ───
    # In v43a block: "const trailDist = pos.initial_atr * 0.4"
    # This appears twice: once on partial2 enable, once on trail update

    # First occurrence: partial2 trail activation
    old_trail_init = """          pos.partial2_done = true
          pos.trail_active = true
          // Set initial trail SL at current price - 0.4 ATR (v43a: 0.5→0.4, tighter)
          const trailDist = pos.initial_atr * 0.4"""
    new_trail_init = """          pos.partial2_done = true
          pos.trail_active = true
          // Set initial trail SL at current price - 0.30 ATR (v49c: 0.4→0.30, tighter)
          const trailDist = pos.initial_atr * 0.30"""
    if old_trail_init in src:
        edits.append(('Trail init 0.4 → 0.30', old_trail_init, new_trail_init))
    else:
        print("⚠️ EDIT 2 anchor not found (trail init)")
        sys.exit(1)

    # Second occurrence: trail update
    old_trail_update = """        // 3. Update trailing stop if active (v43a: 0.4 ATR, was 0.5)
        if (pos.trail_active && pos.max_favorable_price) {
          const trailDist = pos.initial_atr * 0.4"""
    new_trail_update = """        // 3. Update trailing stop if active (v49c: 0.30 ATR, was 0.4 in v43a)
        if (pos.trail_active && pos.max_favorable_price) {
          const trailDist = pos.initial_atr * 0.30"""
    if old_trail_update in src:
        edits.append(('Trail update 0.4 → 0.30', old_trail_update, new_trail_update))
    else:
        print("⚠️ EDIT 3 anchor not found (trail update)")
        sys.exit(1)

    # ─── EDIT 4: Update version comment header ───
    old_header = """      // ─── v43a: Lock profit + Multi-Partial TP + Trailing (before SL/TP check) ───
      // These run on every tick to manage open positions proactively.
      // Lock:     move SL to entry+0.2R when +0.5R reached (locks small profit)
      // Partial1: close 15% at +0.5R (small profit-booking)
      // Partial2: close 25% at +1.0R, then enable trailing on remainder (60%)
      // Trail:    0.4 ATR trailing stop on remainder (overrides TP)
      // 12-seed validation: WR 72.5%, P&L +13.92, Profitable 67%, MaxDD 0.29%, AvgR +0.61
      // vs v38g:  WR 61.8%, P&L +11.01, Profitable 67%, MaxDD 0.31%, AvgR +0.41
      // → +10.7pp WR, +27% P&L, +0.20 AvgR, -0.02pp MaxDD"""
    new_header = """      // ─── v49c: Lock profit + Multi-Partial TP + Trailing (before SL/TP check) ───
      // These run on every tick to manage open positions proactively.
      // Lock:     move SL to entry+0.2R when +0.5R reached (locks small profit)
      // Partial1: close 15% at +0.5R (small profit-booking)
      // Partial2: close 25% at +1.0R, then enable trailing on remainder (60%)
      // Trail:    0.30 ATR trailing stop on remainder (v49c: tighter than v43a's 0.4)
      // 12-seed validation: WR 73.1%, P&L +20.18, Profitable 67%, MaxDD 0.27%, AvgR +0.66
      // vs v38g:  WR 61.8%, P&L +11.01, Profitable 67%, MaxDD 0.31%, AvgR +0.41
      // vs v43a:  WR 72.5%, P&L +13.73, Profitable 67%, MaxDD 0.28%, AvgR +0.61
      // → +11.3pp WR vs v38g, +47% P&L vs v43a, +0.25 AvgR vs v38g"""
    if old_header in src:
        edits.append(('Header comment v43a → v49c', old_header, new_header))
    else:
        print("⚠️ EDIT 4 anchor not found (header)")
        sys.exit(1)

    # ─── EDIT 5: Update Strategy A comment (v43a → v49c) ───
    old_a_comment = "          // v43a: SL 1.4 ATR + lock 0.5R + partial1 15% at 0.5R + partial2 25% at 1.0R + trail 0.4 ATR + ATR floor 0.58%"
    new_a_comment = "          // v49c: SL 1.4 ATR + lock 0.5R + partial1 15% at 0.5R + partial2 25% at 1.0R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55%"
    if old_a_comment in src:
        edits.append(('Strategy A comment v43a → v49c', old_a_comment, new_a_comment))
    else:
        print("⚠️ EDIT 5 anchor not found (Strategy A comment)")
        sys.exit(1)

    # ─── EDIT 6: Update Strategy B comment (v43a → v49c) ───
    old_b_comment = "          // v43a: SL 1.4 ATR + v43a state init (lock 0.5R / partial1 15% at 0.5R / partial2 25% at 1.0R / trail 0.4 ATR)"
    new_b_comment = "          // v49c: SL 1.4 ATR + v49c state init (lock 0.5R / partial1 15% at 0.5R / partial2 25% at 1.0R / trail 0.30 ATR)"
    if old_b_comment in src:
        edits.append(('Strategy B comment v43a → v49c', old_b_comment, new_b_comment))
    else:
        print("⚠️ EDIT 6 anchor not found (Strategy B comment)")
        sys.exit(1)

    # ─── EDIT 7: Update PARTIAL_TP1/TP2 log messages (v43a → v49c) ───
    old_log1 = "console.log(`[Paper/v43a] ${sym} PARTIAL_TP1 15% @ ${price} (R=${rMultiple.toFixed(2)})`)"
    new_log1 = "console.log(`[Paper/v49c] ${sym} PARTIAL_TP1 15% @ ${price} (R=${rMultiple.toFixed(2)})`)"
    if old_log1 in src:
        edits.append(('PARTIAL_TP1 log v43a → v49c', old_log1, new_log1))

    old_log2 = "console.log(`[Paper/v43a] ${sym} PARTIAL_TP2 25% @ ${price} (R=${rMultiple.toFixed(2)})`)"
    new_log2 = "console.log(`[Paper/v49c] ${sym} PARTIAL_TP2 25% @ ${price} (R=${rMultiple.toFixed(2)})`)"
    if old_log2 in src:
        edits.append(('PARTIAL_TP2 log v43a → v49c', old_log2, new_log2))

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
    v49c_count = src.count('v49c')
    v43a_count = src.count('v43a')
    v38g_count = src.count('v38g')
    print(f"\nv49c markers: {v49c_count}")
    print(f"v43a markers remaining: {v43a_count}")
    print(f"v38g markers remaining: {v38g_count} (in comparison comments only)")

    if src == original:
        print("\n⚠️ No changes made — aborting")
        sys.exit(1)

    ENGINE.write_text(src)
    print(f"\n✅ Engine updated: {ENGINE}")
    print(f"   Size: {len(src)} bytes ({len(src.splitlines())} lines)")


if __name__ == "__main__":
    main()
