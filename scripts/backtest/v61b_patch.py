#!/usr/bin/env python3
"""
v61b_patch — Apply v61b champion to engine:
  Adds PYRAMIDING on Strategy B: when trade reaches +1.0R, add 50% more size.
  - pos.qty *= 1.5
  - pos.entry_price = weighted avg
  - Reset partials/lock/trail so they fire again on pyramided position
  - SL moved to new_entry - 1.5 * new_ATR

12-seed validation: WR 79.6%, P&L +46.02 (+3.13 vs v60b), MaxDD 0.29%, PF 2.66, Sharpe +8.69
"""
import sys
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
BAK = ENGINE.with_suffix('.ts.bak.v60b')

src = ENGINE.read_text()
BAK.write_text(src)
print(f"✓ Backup written to {BAK.name}")

edits = [
    # 1. Header comment v60b → v61b
    ("// ─── v60b: Lock profit + 3-Partial TP + Trailing + TIERED Adaptive ATR + A 0.050 + B 0.20 ───",
     "// ─── v61b: Lock + 3-Partial TP + Trailing + TIERED Adaptive + A 0.050 + B 0.20 + PYRAMID B @+1.0R ───"),
    # 2. Header comparison block — add v61b line
    ("// vs v59f:  WR 79.4%, P&L +36.03, Profitable 67%, MaxDD 0.23%, AvgR +0.77, PF 2.63\n      // → +17.6pp WR vs v38g, +295% P&L vs v38g, +33.6% P&L vs v58d, +19% P&L vs v59f",
     "// vs v59f:  WR 79.4%, P&L +36.03, Profitable 67%, MaxDD 0.23%, AvgR +0.77, PF 2.63\n      // vs v60b:  WR 79.4%, P&L +42.89, Profitable 67%, MaxDD 0.28%, AvgR +0.77, PF 2.56\n      // → +17.6pp WR vs v38g, +318% P&L vs v38g, +43% P&L vs v58d, +7.3% P&L vs v60b"),
    # 3. Add pyramid_done to PaperPosition interface
    ("  partial3_done?: boolean       // v53h NEW: third partial at +1.25R (close 15%, enable trail)",
     "  partial3_done?: boolean       // v53h NEW: third partial at +1.25R (close 15%, enable trail)\n  pyramid_done?: boolean         // v61b NEW: pyramid at +1.0R (B only, +50% size, reset partials)"),
    # 4. Add pyramid block BEFORE lock section (after rMultiple/MFE tracking, before lock)
    ("        // 1. Lock profit at +0.5R → move SL to entry+0.35R (v51e: 0.2→0.35, tighter BE)",
     "        // v61b NEW: PYRAMID at +1.0R (Strategy B only) — add 50% more size at current price\n        //   - Increases pos.qty by 50%\n        //   - Recomputes pos.entry_price as weighted average\n        //   - Resets SL to new_entry - 1.5*ATR (gives pyramided position room)\n        //   - Resets partial1/2/3_done, lock_done, trail_active so they re-fire on pyramided pos\n        //   12-seed validation: +3.13 P&L vs v60b, MaxDD +0.01% (negligible risk increase)\n        if (!pos.pyramid_done && pos.strategy === 'B' && rMultiple >= 1.0) {\n          const pyramidPct = 0.50\n          const oldQty = pos.qty\n          const oldEntry = pos.entry_price\n          const addQty = oldQty * pyramidPct\n          const newQty = oldQty + addQty\n          const newEntry = (oldQty * oldEntry + addQty * price) / newQty\n          pos.qty = newQty\n          pos.entry_price = newEntry\n          // Recompute ATR and SL distance for pyramided position\n          const newATR = computeATR(hist.map(h => h.price), 60)\n          if (newATR > 0) {\n            pos.initial_atr = newATR\n            pos.initial_sl_distance = newATR * 1.5\n            pos.current_sl = isLong ? newEntry - newATR * 1.5 : newEntry + newATR * 1.5\n            pos.catastrophic_sl = isLong ? newEntry - newATR * 4 : newEntry + newATR * 4\n            pos.current_tp = null\n          }\n          // Reset partials + lock + trail so they fire again on pyramided position\n          pos.partial1_done = false\n          pos.partial2_done = false\n          pos.partial3_done = false\n          pos.lock_done = false\n          pos.trail_active = false\n          pos.max_favorable_price = price\n          pos.pyramid_done = true\n          console.log(`[Paper/v61b] ${sym} PYRAMID +50% @ ${price} (R was ${rMultiple.toFixed(2)}, new entry ${newEntry.toFixed(4)})`)\n        }\n\n        // Recompute rMultiple after pyramid (entry_price may have changed)\n        const rMultipleEff = isLong\n          ? (price - pos.entry_price) / (pos.initial_sl_distance || 1)\n          : (pos.entry_price - price) / (pos.initial_sl_distance || 1)\n        const rMultiple = rMultipleEff  // shadow so downstream code uses effective R\n\n        // 1. Lock profit at +0.5R → move SL to entry+0.35R (v51e: 0.2→0.35, tighter BE)"),
    # 5. A strategy position init comment v60b → v61b
    ("// v60b: SL 1.5 ATR + lock 0.5R (offset 0.35R) + p1 5% @ 0.5R + p2 10% @ 1.0R + p3 15% @ 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55% + TIERED SIZE (0.4/0.7/1.0 by ATR)\n          //   12-seed validation: WR 79.4%, P&L +42.89, Profitable 67% of seeds, MaxDD 0.28%, PF 2.56, Sharpe +8.30",
     "// v61b: SL 1.5 ATR + lock 0.5R (offset 0.35R) + p1 5% @ 0.5R + p2 10% @ 1.0R + p3 15% @ 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55% + TIERED SIZE (0.4/0.7/1.0 by ATR) + PYRAMID B @+1.0R\n          //   12-seed validation: WR 79.4%, P&L +42.89 (A), Profitable 67% of seeds, MaxDD 0.28%, PF 2.56"),
    # 6. B strategy position init comment v60b → v61b
    ("// v60b: SL 1.5 ATR + v60b state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR)\n          //   B size: 0.20 base, TIERED 0.4/0.7/1.0 by ATR (v60b: was 0.175 in v59f)",
     "// v61b: SL 1.5 ATR + v61b state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR / PYRAMID +50% @ +1.0R)\n          //   B size: 0.20 base, TIERED 0.4/0.7/1.0 by ATR, pyramided +50% at +1.0R (v61b: was 0.175 in v59f)"),
]

n_applied = 0
for old, new in edits:
    if old in src:
        src = src.replace(old, new, 1)
        n_applied += 1
        print(f"✓ Edit #{n_applied}: applied")
    else:
        print(f"✗ Edit FAILED — anchor not found:")
        print(f"   {old[:120]}...")
        sys.exit(1)

# Now add pyramid_done = false to position init blocks (after partial3_done = false)
import re
init_pattern = r"(pos\.partial3_done = false\n)"
matches = list(re.finditer(init_pattern, src))
print(f"\nFound {len(matches)} position init blocks with partial3_done = false")
# Insert pyramid_done = false after each
src = re.sub(init_pattern, r"\1          pos.pyramid_done = false\n", src)
print(f"✓ Added pos.pyramid_done = false to {len(matches)} init blocks")

v60b_count = src.count('v60b')
v61b_count = src.count('v61b')
print(f"\nVersion markers: v60b={v60b_count} (in comparison comments), v61b={v61b_count} (should be >8)")

braces = src.count('{') - src.count('}')
parens = src.count('(') - src.count(')')
brackets = src.count('[') - src.count(']')
print(f"Braces diff: {braces}, parens diff: {parens}, brackets diff: {brackets} (all should be 0)")

if braces != 0 or parens != 0 or brackets != 0:
    print("⚠️ Brace/paren/bracket mismatch — aborting")
    sys.exit(1)

ENGINE.write_text(src)
print(f"\n✓ All {n_applied} edits + {len(matches)} init-block patches applied. Engine updated to v61b.")
