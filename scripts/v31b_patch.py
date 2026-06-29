#!/usr/bin/env python3
"""
v31b Patch — Aplicar config v31b al engine real
- Strategy A: momentum 0.40, RSI 25-75, TP 1.2, SL 2.0, catSL 4.0, pos_size 2.5%
- Strategy B: RSI 30/70, TP 1.2, SL 2.0, catSL 4.0, pos_size 10%
- Strategy D: bb_width 0.012, TP 1.0, SL 1.5, catSL 3.0, pos_size 5%
- Time stop: 1h (was 2h)
- Backtest: WR 63.5%, P&L +93.26, PF 1.22
"""
import re
import sys
from pathlib import Path

ENGINE_PATH = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
WORKSPACE_PATH = Path('/home/z/my-project/scripts/terminal/paper-trading-engine-v3.ts')

# Backup
for p in [ENGINE_PATH, WORKSPACE_PATH]:
    bak = p.with_suffix(p.suffix + '.bak.v15')
    if not bak.exists():
        bak.write_text(p.read_text())
        print(f"Backup created: {bak}")

def apply_edits(content):
    edits = [
        # 1. Time stop 2h → 1h (60min) - v31b
        {
            'old': """      // ─── Time stop: 2h max hold (FIX v13 BUG P: was 4h — snapshot showed
      //   3 trades held 110-131min that drifted into SL. Momentum is gone
      //   after 2h; close before drift turns into SL hit.) ───
      const entryTime = new Date(pos.entry_time).getTime()
      const holdMs = now - entryTime
      if (holdMs > 2 * 60 * 60 * 1000) {""",
            'new': """      // ─── Time stop: 1h max hold (v31b: tighter time stop improves P&L
      //   by cutting marginal positions before they drift to SL) ───
      const entryTime = new Date(pos.entry_time).getTime()
      const holdMs = now - entryTime
      if (holdMs > 1 * 60 * 60 * 1000) {""",
        },
        # 2. Strategy A momentum 0.15 → 0.40 + add RSI 25-75 filter
        {
            'old': """        // FIX v12: 0.3% threshold was too strict — Strategy A made 0 trades
        // in 3h of running. Most tokens move <0.3% in 45s even in volatile
        // regime. Lower to 0.15% so Strategy A actually fires.
        const recent = hist.slice(-30)
        const recentMomentum = ((recent[recent.length - 1].price - recent[0].price) / recent[0].price) * 100
        if (Math.abs(recentMomentum) < 0.15) return null  // need ≥0.15% move in 45s
        return { ticker: t, recentMomentum }""",
            'new': """        // v31b: momentum 0.40% (was 0.15) + RSI 25-75 filter for quality
        // Backtest: 0.15 gave WR 41%, 0.40 + RSI filter gives WR 63.5%
        const recent = hist.slice(-30)
        const recentMomentum = ((recent[recent.length - 1].price - recent[0].price) / recent[0].price) * 100
        if (Math.abs(recentMomentum) < 0.40) return null  // need ≥0.40% move (strong signals only)
        const rsiA = computeRSI(prices, 14)
        if (rsiA < 25 || rsiA > 75) return null  // skip extreme zones (mean reversion territory)
        return { ticker: t, recentMomentum }""",
        },
        # 3. Strategy A position size 5% → 2.5% (A is loser, halve losses)
        {
            'old': """      // FIX v12 BUG H: position size 3% → 5% (so wins cover fees+slippage)
      const usdtAmount = Math.min(strat.cash * 0.05, strat.cash * 0.10)
      if (usdtAmount < 50) break""",
            'new': """      // v31b: position size 2.5% for Strategy A (A has 61% WR but loses
      // money due to R:R 1:1.67 — halving size makes A's losses manageable)
      const usdtAmount = Math.min(strat.cash * 0.025, strat.cash * 0.10)
      if (usdtAmount < 50) break""",
        },
        # 4. Strategy A TP 2.5 → 1.2, catSL 5 → 4 (tighter TP for higher WR)
        {
            'old': """          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 2.0 : pos.entry_price + atr * 2.0
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 2.5 : pos.entry_price - atr * 2.5
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 5 : pos.entry_price + atr * 5""",
            'new': """          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 2.0 : pos.entry_price + atr * 2.0
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4""",
        },
        # 5. Strategy B RSI 40/60 → 30/70
        {
            'old': """        // FIX v12 BUG E: Widen RSI thresholds 30/70 → 40/60 for more signals.
        //   On 1.5s ticks RSI rarely hits <30 or >70, so strategy B rarely fired.
        //   40/60 is still a meaningful mean-reversion zone but triggers ~5x more.
        const rsi = computeRSI(prices, 14)
        if (rsi >= 40 && rsi <= 60) return null""",
            'new': """        // v31b: RSI 30/70 (was 40/60) — backtest shows 30/70 gives 75% WR
        //   on Strategy B (the winner strategy, +69 P&L in 6h)
        const rsi = computeRSI(prices, 14)
        if (rsi >= 30 && rsi <= 70) return null""",
        },
        # 6. Strategy B position size 5% → 10% (B is winner, double size)
        {
            'old': """      // FIX v13 BUG L: position size 3% → 5% (matches A; covers fees+slippage)
      const usdtAmount = Math.min(strat.cash * 0.05, strat.cash * 0.10)
      if (usdtAmount < 50) break""",
            'new': """      // v31b: position size 10% for Strategy B (B has 75% WR, the winner
      // strategy — doubling size scales profits)
      const usdtAmount = Math.min(strat.cash * 0.10, strat.cash * 0.10)
      if (usdtAmount < 50) break""",
        },
        # 7. Strategy B TP 2.5 → 1.2 (catSL stays 4)
        {
            'old': """          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 2.0 : pos.entry_price + atr * 2.0
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 2.5 : pos.entry_price - atr * 2.5
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4""",
            'new': """          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 2.0 : pos.entry_price + atr * 2.0
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.2 : pos.entry_price - atr * 1.2
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 4 : pos.entry_price + atr * 4""",
        },
        # 8. Strategy D bb_width 0.015 → 0.012
        {
            'old': """        // FIX v13 BUG N: Squeeze threshold 3% → 1.5% (3% captured too much noise,
        //   0 wins in 4 trades). Real squeeze = bb.width < 1.5%. Fewer but cleaner.
        if (bb.width > 0.015) return null // not squeezed""",
            'new': """        // v31b: Squeeze threshold 1.5% → 1.2% (tighter squeeze = cleaner breakouts)
        if (bb.width > 0.012) return null // not squeezed""",
        },
        # 9. Strategy D TP 3.0 → 1.0, catSL 3.5 → 3.0 (tighter TP, tighter catSL)
        {
            'old': """          // v14 NIGHT1 FIX: SL más ancho (1.0 → 1.5 ATR), TP más cercano (4 → 3 ATR).
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.5 : pos.entry_price + atr * 1.5
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 3 : pos.entry_price - atr * 3
          // FIX v13 BUG N: CatSL 2×ATR → 3.5×ATR (was too tight, caused CAT_SL hits)
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 3.5 : pos.entry_price + atr * 3.5""",
            'new': """          // v31b: SL 1.5 ATR (kept), TP 3.0 → 1.0 (tighter for higher WR), catSL 3.5 → 3.0
          pos.current_sl = direction === 'LONG' ? pos.entry_price - atr * 1.5 : pos.entry_price + atr * 1.5
          pos.current_tp = direction === 'LONG' ? pos.entry_price + atr * 1.0 : pos.entry_price - atr * 1.0
          pos.catastrophic_sl = direction === 'LONG' ? pos.entry_price - atr * 3.0 : pos.entry_price + atr * 3.0""",
        },
    ]

    for i, edit in enumerate(edits, 1):
        if edit['old'] not in content:
            print(f"  ⚠️  Edit {i} NOT FOUND — anchor missing")
            print(f"      Looking for: {edit['old'][:100]}...")
            return None
        content = content.replace(edit['old'], edit['new'])
        print(f"  ✅ Edit {i} applied")
    return content


for label, path in [('REPO', ENGINE_PATH), ('WORKSPACE', WORKSPACE_PATH)]:
    print(f"\n{'='*60}\nApplying v31b to {label}: {path}\n{'='*60}")
    content = path.read_text()
    new_content = apply_edits(content)
    if new_content is None:
        print(f"❌ Failed to apply edits to {label}")
        sys.exit(1)
    path.write_text(new_content)
    print(f"✅ {label} updated")

# Verify
print("\n" + "="*60 + "\nVERIFICATION\n" + "="*60)
for label, path in [('REPO', ENGINE_PATH), ('WORKSPACE', WORKSPACE_PATH)]:
    content = path.read_text()
    checks = [
        ('momentum < 0.40', 'momentum < 0.40' in content),
        ('rsiA < 25 || rsiA > 75', 'rsiA < 25 || rsiA > 75' in content),
        ('strat.cash * 0.025', 'strat.cash * 0.025' in content),
        ('atr * 1.2 (A TP)', content.count('atr * 1.2') >= 2),
        ('rsi >= 30 && rsi <= 70', 'rsi >= 30 && rsi <= 70' in content),
        ('strat.cash * 0.10 (B)', 'strat.cash * 0.10' in content),
        ('bb.width > 0.012', 'bb.width > 0.012' in content),
        ('atr * 1.0 (D TP)', 'atr * 1.0' in content),
        ('atr * 3.0 (D catSL)', 'atr * 3.0' in content),
        ('1h time stop', '1 * 60 * 60 * 1000' in content),
    ]
    print(f"\n{label}:")
    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")
    # Balance checks
    opens = content.count('{')
    closes = content.count('}')
    print(f"  Braces: {opens}/{closes} {'✅' if opens == closes else '❌'}")
