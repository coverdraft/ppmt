#!/usr/bin/env python3
"""
v59f_patch — Apply v59f champion to engine:
  1. A base size: 0.030 → 0.040 (push A harder with tiered protection)
  2. B base size: 0.15 → 0.175 (push B harder with tiered protection)
  3. Adaptive ATR: simple 0.6/0.5 → TIERED 0.4 (ATR<0.6) / 0.7 (ATR<0.8) / 1.0
  4. Update version markers v58d → v59f

12-seed validation: WR 79.4%, P&L +36.03 (+3.91 vs v58d), MaxDD 0.23%, PF 2.63
"""
import re, sys
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
BAK = ENGINE.with_suffix('.ts.bak.v58d')

# Backup
src = ENGINE.read_text()
BAK.write_text(src)
print(f"✓ Backup written to {BAK.name}")

# Edits
edits = [
    # 1. Header comment v58d → v59f (line 799)
    ("// ─── v58d: Lock profit + 3-Partial TP + Trailing + Adaptive ATR Sizing + A 0.030 + B 0.15 ───",
     "// ─── v59f: Lock profit + 3-Partial TP + Trailing + TIERED Adaptive ATR + A 0.040 + B 0.175 ───"),
    # 2. Header comment block — add v59f line (line 815-818)
    ("// vs v57i:  WR 79.4%, P&L +28.83, Profitable 67%, MaxDD 0.19%, AvgR +0.77, PF 2.63\n      // → +17.6pp WR vs v38g, +192% P&L vs v38g, +9pp Profit vs v53h, +0.49 PF vs v53h",
     "// vs v57i:  WR 79.4%, P&L +28.83, Profitable 67%, MaxDD 0.19%, AvgR +0.77, PF 2.63\n      // vs v58d:  WR 79.4%, P&L +32.12, Profitable 67%, MaxDD 0.21%, AvgR +0.77, PF 2.53\n      // → +17.6pp WR vs v38g, +218% P&L vs v38g, +12.2% P&L vs v58d, tiered sizing vs simple"),
    # 3. A base size 0.030 → 0.040 (line 1087)
    ("// v58d: A base size 0.030 (was 0.025 in v31b-v57i) — modest A boost with adaptive protection\n      //   12-seed validation: A 0.030 → P&L +32.12, MaxDD 0.21% (vs A 0.025 → P&L +28.83, MaxDD 0.19%)\n      const baseUsdtAmountA = Math.min(strat.cash * 0.030, strat.cash * 0.10)",
     "// v59f: A base size 0.040 (was 0.030 in v58d) — push A harder with TIERED adaptive protection\n      //   12-seed validation: A 0.040 + B 0.175 + tiered → P&L +36.03, MaxDD 0.23% (vs v58d P&L +32.12, MaxDD 0.21%)\n      const baseUsdtAmountA = Math.min(strat.cash * 0.040, strat.cash * 0.10)"),
    # 4. A adaptive sizing — simple → tiered (line 1096-1098)
    ("// v56d: Adaptive ATR sizing — halve size when ATR < 0.6% (calm market, low edge)\n      const atrPctA = atr / top.ticker.price * 100\n      const usdtAmount = atrPctA < 0.60 ? baseUsdtAmountA * 0.5 : baseUsdtAmountA",
     "// v59f: TIERED adaptive ATR sizing — 0.4x if ATR<0.6%, 0.7x if ATR<0.8%, 1.0x otherwise\n      //   Replaces v56d's simple 0.5x halving — finer control reduces MaxDD while keeping P&L up\n      const atrPctA = atr / top.ticker.price * 100\n      const sizeMultA = atrPctA < 0.60 ? 0.4 : (atrPctA < 0.80 ? 0.7 : 1.0)\n      const usdtAmount = baseUsdtAmountA * sizeMultA"),
    # 5. A strategy position init comment v58d → v59f (line 1107)
    ("// v58d: SL 1.5 ATR + lock 0.5R (offset 0.35R) + p1 5% @ 0.5R + p2 10% @ 1.0R + p3 15% @ 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55% + ADAPTIVE SIZE (0.5x if ATR<0.6%)\n          //   12-seed validation: WR 79.4%, P&L +32.12, Profitable 67% of seeds, MaxDD 0.21%, PF 2.53, Sharpe +13.15",
     "// v59f: SL 1.5 ATR + lock 0.5R (offset 0.35R) + p1 5% @ 0.5R + p2 10% @ 1.0R + p3 15% @ 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55% + TIERED SIZE (0.4/0.7/1.0 by ATR)\n          //   12-seed validation: WR 79.4%, P&L +36.03, Profitable 67% of seeds, MaxDD 0.23%, PF 2.63, Sharpe +8.28"),
    # 6. B base size 0.15 → 0.175 (line 1187)
    ("// v57i: B base size 0.15 (was 0.125 in v56d) — push B winners with adaptive protection\n      //   12-seed validation: B 0.15 → P&L +28.83, MaxDD 0.19% (vs B 0.125 → P&L +26.76, MaxDD 0.17%)\n      const baseUsdtAmount = Math.min(strat.cash * 0.15, strat.cash * 0.15)",
     "// v59f: B base size 0.175 (was 0.15 in v57i/v58d) — push B harder with TIERED adaptive protection\n      //   12-seed validation: A 0.040 + B 0.175 + tiered → P&L +36.03, MaxDD 0.23% (vs v58d P&L +32.12, MaxDD 0.21%)\n      const baseUsdtAmount = Math.min(strat.cash * 0.175, strat.cash * 0.175)"),
    # 7. B adaptive sizing — simple → tiered (line 1201-1203)
    ("// v56d: Apply adaptive ATR sizing (halve size if ATR < 0.6%)\n      const atrPctB = (atr / hist[hist.length - 1].price) * 100\n      const usdtAmount = atrPctB < 0.60 ? baseUsdtAmount * 0.5 : baseUsdtAmount",
     "// v59f: Apply TIERED adaptive ATR sizing (0.4x if <0.6%, 0.7x if <0.8%, 1.0x otherwise)\n      const atrPctB = (atr / hist[hist.length - 1].price) * 100\n      const sizeMultB = atrPctB < 0.60 ? 0.4 : (atrPctB < 0.80 ? 0.7 : 1.0)\n      const usdtAmount = baseUsdtAmount * sizeMultB"),
    # 8. B strategy position init comment v57i → v59f (line 1212-1213)
    ("// v57i: SL 1.5 ATR + v57i state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR)\n          //   B size: 0.15 base, 0.075 if ATR<0.6% (v57i: was 0.125 in v56d — push B winners)",
     "// v59f: SL 1.5 ATR + v59f state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR)\n          //   B size: 0.175 base, TIERED 0.4/0.7/1.0 by ATR (v59f: was 0.15 in v57i/v58d)"),
]

# Apply edits
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

# Verify version markers
v58d_count = src.count('v58d')
v59f_count = src.count('v59f')
print(f"\nVersion markers: v58d={v58d_count} (should be 0 in active code, OK in comments), v59f={v59f_count} (should be >8)")

# Braces/parens/brackets check
braces = src.count('{') - src.count('}')
parens = src.count('(') - src.count(')')
brackets = src.count('[') - src.count(']')
print(f"Braces diff: {braces}, parens diff: {parens}, brackets diff: {brackets} (all should be 0)")

if braces != 0 or parens != 0 or brackets != 0:
    print("⚠️ Brace/paren/bracket mismatch — aborting")
    sys.exit(1)

ENGINE.write_text(src)
print(f"\n✓ All {n_applied} edits applied. Engine updated to v59f.")
