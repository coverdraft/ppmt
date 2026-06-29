#!/usr/bin/env python3
"""
v60b_patch — Apply v60b champion to engine:
  1. A base size: 0.040 → 0.050 (push A harder with tiered protection)
  2. B base size: 0.175 → 0.20 (push B harder with tiered protection)
  3. TIERED adaptive sizing (already in v59f, unchanged)
  4. Update version markers v59f → v60b

12-seed validation: WR 79.4%, P&L +42.89 (+6.86 vs v59f, +19%), MaxDD 0.28%, PF 2.56, Sharpe +8.30
"""
import sys
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
BAK = ENGINE.with_suffix('.ts.bak.v59f')

src = ENGINE.read_text()
BAK.write_text(src)
print(f"✓ Backup written to {BAK.name}")

edits = [
    # 1. Header comment v59f → v60b (line 799)
    ("// ─── v59f: Lock profit + 3-Partial TP + Trailing + TIERED Adaptive ATR + A 0.040 + B 0.175 ───",
     "// ─── v60b: Lock profit + 3-Partial TP + Trailing + TIERED Adaptive ATR + A 0.050 + B 0.20 ───"),
    # 2. Header comparison block — add v60b line
    ("// vs v58d:  WR 79.4%, P&L +32.12, Profitable 67%, MaxDD 0.21%, AvgR +0.77, PF 2.53\n      // → +17.6pp WR vs v38g, +218% P&L vs v38g, +12.2% P&L vs v58d, tiered sizing vs simple",
     "// vs v58d:  WR 79.4%, P&L +32.12, Profitable 67%, MaxDD 0.21%, AvgR +0.77, PF 2.53\n      // vs v59f:  WR 79.4%, P&L +36.03, Profitable 67%, MaxDD 0.23%, AvgR +0.77, PF 2.63\n      // → +17.6pp WR vs v38g, +295% P&L vs v38g, +33.6% P&L vs v58d, +19% P&L vs v59f"),
    # 3. A base size 0.040 → 0.050
    ("// v59f: A base size 0.040 (was 0.030 in v58d) — push A harder with TIERED adaptive protection\n      //   12-seed validation: A 0.040 + B 0.175 + tiered → P&L +36.03, MaxDD 0.23% (vs v58d P&L +32.12, MaxDD 0.21%)\n      const baseUsdtAmountA = Math.min(strat.cash * 0.040, strat.cash * 0.10)",
     "// v60b: A base size 0.050 (was 0.040 in v59f) — push A harder with TIERED adaptive protection\n      //   12-seed validation: A 0.050 + B 0.20 + tiered → P&L +42.89, MaxDD 0.28% (vs v59f P&L +36.03, MaxDD 0.23%)\n      const baseUsdtAmountA = Math.min(strat.cash * 0.050, strat.cash * 0.10)"),
    # 4. A strategy position init comment v59f → v60b
    ("// v59f: SL 1.5 ATR + lock 0.5R (offset 0.35R) + p1 5% @ 0.5R + p2 10% @ 1.0R + p3 15% @ 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55% + TIERED SIZE (0.4/0.7/1.0 by ATR)\n          //   12-seed validation: WR 79.4%, P&L +36.03, Profitable 67% of seeds, MaxDD 0.23%, PF 2.63, Sharpe +8.28",
     "// v60b: SL 1.5 ATR + lock 0.5R (offset 0.35R) + p1 5% @ 0.5R + p2 10% @ 1.0R + p3 15% @ 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55% + TIERED SIZE (0.4/0.7/1.0 by ATR)\n          //   12-seed validation: WR 79.4%, P&L +42.89, Profitable 67% of seeds, MaxDD 0.28%, PF 2.56, Sharpe +8.30"),
    # 5. B base size 0.175 → 0.20
    ("// v59f: B base size 0.175 (was 0.15 in v57i/v58d) — push B harder with TIERED adaptive protection\n      //   12-seed validation: A 0.040 + B 0.175 + tiered → P&L +36.03, MaxDD 0.23% (vs v58d P&L +32.12, MaxDD 0.21%)\n      const baseUsdtAmount = Math.min(strat.cash * 0.175, strat.cash * 0.175)",
     "// v60b: B base size 0.20 (was 0.175 in v59f) — push B harder with TIERED adaptive protection\n      //   12-seed validation: A 0.050 + B 0.20 + tiered → P&L +42.89, MaxDD 0.28% (vs v59f P&L +36.03, MaxDD 0.23%)\n      const baseUsdtAmount = Math.min(strat.cash * 0.20, strat.cash * 0.20)"),
    # 6. B strategy position init comment v59f → v60b
    ("// v59f: SL 1.5 ATR + v59f state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR)\n          //   B size: 0.175 base, TIERED 0.4/0.7/1.0 by ATR (v59f: was 0.15 in v57i/v58d)",
     "// v60b: SL 1.5 ATR + v60b state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR)\n          //   B size: 0.20 base, TIERED 0.4/0.7/1.0 by ATR (v60b: was 0.175 in v59f)"),
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

v59f_count = src.count('v59f')
v60b_count = src.count('v60b')
print(f"\nVersion markers: v59f={v59f_count} (should be 0 in active code), v60b={v60b_count} (should be >6)")

braces = src.count('{') - src.count('}')
parens = src.count('(') - src.count(')')
brackets = src.count('[') - src.count(']')
print(f"Braces diff: {braces}, parens diff: {parens}, brackets diff: {brackets} (all should be 0)")

if braces != 0 or parens != 0 or brackets != 0:
    print("⚠️ Brace/paren/bracket mismatch — aborting")
    sys.exit(1)

ENGINE.write_text(src)
print(f"\n✓ All {n_applied} edits applied. Engine updated to v60b.")
