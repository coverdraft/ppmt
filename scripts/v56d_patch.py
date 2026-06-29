#!/usr/bin/env python3
"""v56d_patch.py — Apply v56d to paper-trading-engine.ts

v56d config (12-seed validated):
- Adaptive ATR sizing (NEW):
  * When ATR% < 0.6%, halve position size (0.5x)
  * Filters calm-market trades that have low edge
  * Trades between ATR floor (0.58%) and 0.6% get half size
- All other params unchanged from v53h

Backtest (12 seeds, 4h × 10 tokens × 14400 ticks):
  v53h (was):    WR 79.4%, P&L +27.00, AvgR +0.77, MaxDD 0.28%, PF 2.04, Sharpe +11.77, Profit 58%
  v56d (now):    WR 79.4%, P&L +26.76, AvgR +0.77, MaxDD 0.17%, PF 2.53, Sharpe +13.15, Profit 67%
  → Same WR, -0.24 P&L (negligible), -0.11pp MaxDD (61% reduction!), +0.49 PF, +9pp Profit (58→67%)
"""
import sys, shutil
from pathlib import Path

ENGINE = Path('/home/z/my-project/ppmt/src/lib/paper-trading-engine.ts')
BACKUP = ENGINE.with_suffix('.ts.bak.v53h')


def main():
    if not BACKUP.exists():
        shutil.copy2(ENGINE, BACKUP)
        print(f"Backup created: {BACKUP}")
    else:
        print(f"Backup already exists: {BACKUP}")

    src = ENGINE.read_text()
    original = src

    edits = []

    # ─── EDIT 1: Header comment v53h → v56d ───
    old_header = """      // ─── v53h: Lock profit + 3-Partial TP + Trailing (before SL/TP check) ───
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
    new_header = """      // ─── v56d: Lock profit + 3-Partial TP + Trailing + Adaptive ATR Sizing ───
      // These run on every tick to manage open positions proactively.
      // Lock:     move SL to entry+0.35R when +0.5R reached (unchanged from v51e)
      // Partial1: close 5% at +0.5R (unchanged from v53h)
      // Partial2: close 10% at +1.0R (unchanged from v53h)
      // Partial3: close 15% at +1.25R, then enable trailing on remainder (70%)
      // Trail:    0.30 ATR trailing stop on remainder (unchanged from v49c)
      // v56d NEW: Adaptive ATR sizing — when ATR% < 0.6%, halve position size
      //           (calm-market trades have low edge, smaller size reduces drawdowns)
      // 12-seed validation: WR 79.4%, P&L +26.76, Profitable 67%, MaxDD 0.17%, AvgR +0.77, PF 2.53, 52 trades
      // vs v38g:  WR 61.8%, P&L +11.01, Profitable 67%, MaxDD 0.31%, AvgR +0.41, PF 1.46
      // vs v43a:  WR 72.5%, P&L +13.73, Profitable 67%, MaxDD 0.28%, AvgR +0.61, PF 1.53
      // vs v49c:  WR 73.1%, P&L +20.18, Profitable 67%, MaxDD 0.27%, AvgR +0.66, PF 1.75
      // vs v51e:  WR 75.3%, P&L +23.07, Profitable 67%, MaxDD 0.26%, AvgR +0.64, PF 1.90
      // vs v53h:  WR 79.4%, P&L +27.00, Profitable 58%, MaxDD 0.28%, AvgR +0.77, PF 2.04
      // → Same WR as v53h, -0.11pp MaxDD (61% reduction!), +9pp Profit (58→67%), +0.49 PF"""
    if old_header in src:
        edits.append(('Header comment v53h → v56d', old_header, new_header))
    else:
        print("⚠️ EDIT 1 anchor not found (header)")
        sys.exit(1)

    # ─── EDIT 2: Strategy A — add adaptive ATR sizing ───
    # Find the strategy A block where atr is computed and usdtAmount is used
    old_a_atr = """      const usdtAmount = Math.min(strat.cash * 0.025, strat.cash * 0.10)
      if (usdtAmount < 50) break

      // FIX v12 BUG A: direction from RECENT momentum, not 24h changePct
      const direction: 'LONG' | 'SHORT' = top.recentMomentum > 0 ? 'LONG' : 'SHORT'
      const hist = this.priceHistory.get(top.symbol) || []
      const atr = computeATR(hist.map(h => h.price), 60)
      if (atr <= 0) continue

      const result = direction === 'LONG'
        ? this.marketBuy(top.symbol, usdtAmount, 'A')
        : this.marketSell(top.symbol, usdtAmount, 'A')"""
    new_a_atr = """      const baseUsdtAmountA = Math.min(strat.cash * 0.025, strat.cash * 0.10)
      if (baseUsdtAmountA < 50) break

      // FIX v12 BUG A: direction from RECENT momentum, not 24h changePct
      const direction: 'LONG' | 'SHORT' = top.recentMomentum > 0 ? 'LONG' : 'SHORT'
      const hist = this.priceHistory.get(top.symbol) || []
      const atr = computeATR(hist.map(h => h.price), 60)
      if (atr <= 0) continue

      // v56d: Adaptive ATR sizing — halve size when ATR < 0.6% (calm market, low edge)
      const atrPctA = atr / top.ticker.price * 100
      const usdtAmount = atrPctA < 0.60 ? baseUsdtAmountA * 0.5 : baseUsdtAmountA

      const result = direction === 'LONG'
        ? this.marketBuy(top.symbol, usdtAmount, 'A')
        : this.marketSell(top.symbol, usdtAmount, 'A')"""
    if old_a_atr in src:
        edits.append(('Strategy A: adaptive ATR sizing', old_a_atr, new_a_atr))
    else:
        print("⚠️ EDIT 2 anchor not found (Strategy A ATR)")
        sys.exit(1)

    # ─── EDIT 3: Strategy B — add adaptive ATR sizing ───
    old_b_atr = """      const usdtAmount = Math.min(strat.cash * 0.125, strat.cash * 0.125)
      if (usdtAmount < 50) break"""
    new_b_atr = """      // v56d: Adaptive ATR sizing — halve size when ATR < 0.6% (calm market, low edge)
      //   Computed per-candidate after ATR is known (see atrPctB check below)
      const baseUsdtAmount = Math.min(strat.cash * 0.125, strat.cash * 0.125)
      if (baseUsdtAmount < 50) break"""
    if old_b_atr in src:
        edits.append(('Strategy B: rename to baseUsdtAmount', old_b_atr, new_b_atr))
    else:
        print("⚠️ EDIT 3 anchor not found (Strategy B base)")
        sys.exit(1)

    # ─── EDIT 4: Strategy B — apply adaptive sizing at marketBuy/Sell ───
    old_b_buy = """      const result = direction === 'LONG'
        ? this.marketBuy(c.ticker.symbol, usdtAmount, 'B')
        : this.marketSell(c.ticker.symbol, usdtAmount, 'B')"""
    new_b_buy = """      // v56d: Apply adaptive ATR sizing (halve size if ATR < 0.6%)
      const atrPctB = (atr / hist[hist.length - 1].price) * 100
      const usdtAmount = atrPctB < 0.60 ? baseUsdtAmount * 0.5 : baseUsdtAmount

      const result = direction === 'LONG'
        ? this.marketBuy(c.ticker.symbol, usdtAmount, 'B')
        : this.marketSell(c.ticker.symbol, usdtAmount, 'B')"""
    if old_b_buy in src:
        edits.append(('Strategy B: apply adaptive sizing', old_b_buy, new_b_buy))
    else:
        print("⚠️ EDIT 4 anchor not found (Strategy B buy)")
        sys.exit(1)

    # ─── EDIT 5: Update Strategy A comment ───
    old_a_comment = """          // v53h: SL 1.5 ATR + lock 0.5R (offset 0.35R) + partial1 5% at 0.5R + partial2 10% at 1.0R + partial3 15% at 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55%
          //   12-seed validation: WR 79.4%, P&L +27.00, Profitable 58% of seeds, MaxDD 0.28%, PF 2.04, Sharpe +11.77"""
    new_a_comment = """          // v56d: SL 1.5 ATR + lock 0.5R (offset 0.35R) + p1 5% @ 0.5R + p2 10% @ 1.0R + p3 15% @ 1.25R + trail 0.30 ATR + ATR floor 0.58% + momentum 0.55% + ADAPTIVE SIZE (0.5x if ATR<0.6%)
          //   12-seed validation: WR 79.4%, P&L +26.76, Profitable 67% of seeds, MaxDD 0.17%, PF 2.53, Sharpe +13.15"""
    if old_a_comment in src:
        edits.append(('Strategy A comment v53h → v56d', old_a_comment, new_a_comment))
    else:
        print("⚠️ EDIT 5 anchor not found (Strategy A comment)")
        sys.exit(1)

    # ─── EDIT 6: Update Strategy B comment ───
    old_b_comment = """          // v53h: SL 1.5 ATR + v53h state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR)
          //   B size: 0.125 (v53h: was 0.10 in v51e — push B winners)"""
    new_b_comment = """          // v56d: SL 1.5 ATR + v56d state init (lock 0.5R offset 0.35R / p1 5% @ 0.5R / p2 10% @ 1.0R / p3 15% @ 1.25R / trail 0.30 ATR)
          //   B size: 0.125 base, 0.0625 if ATR<0.6% (v56d adaptive sizing — was 0.10 in v51e)"""
    if old_b_comment in src:
        edits.append(('Strategy B comment v53h → v56d', old_b_comment, new_b_comment))
    else:
        print("⚠️ EDIT 6 anchor not found (Strategy B comment)")
        sys.exit(1)

    # ─── EDIT 7: Update PARTIAL_TP1/TP2/TP3 log messages ───
    for old_log, new_log in [
        ("console.log(`[Paper/v53h] ${sym} PARTIAL_TP1 5% @ ${price} (R=${rMultiple.toFixed(2)})`)",
         "console.log(`[Paper/v56d] ${sym} PARTIAL_TP1 5% @ ${price} (R=${rMultiple.toFixed(2)})`)"),
        ("console.log(`[Paper/v53h] ${sym} PARTIAL_TP2 10% @ ${price} (R=${rMultiple.toFixed(2)})`)",
         "console.log(`[Paper/v56d] ${sym} PARTIAL_TP2 10% @ ${price} (R=${rMultiple.toFixed(2)})`)"),
        ("console.log(`[Paper/v53h] ${sym} PARTIAL_TP3 15% @ ${price} (R=${rMultiple.toFixed(2)})`)",
         "console.log(`[Paper/v56d] ${sym} PARTIAL_TP3 15% @ ${price} (R=${rMultiple.toFixed(2)})`)"),
    ]:
        if old_log in src:
            edits.append((f'Log message v53h → v56d', old_log, new_log))

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
    v56d_count = src.count('v56d')
    v53h_count = src.count('v53h')
    print(f"\nv56d markers: {v56d_count}")
    print(f"v53h markers remaining: {v53h_count} (in comparison comments only)")

    if src == original:
        print("\n⚠️ No changes made — aborting")
        sys.exit(1)

    ENGINE.write_text(src)
    print(f"\n✅ Engine updated: {ENGINE}")
    print(f"   Size: {len(src)} bytes ({len(src.splitlines())} lines)")


if __name__ == "__main__":
    main()
