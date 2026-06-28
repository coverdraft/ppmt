#!/usr/bin/env python3
"""
PPMT Terminal Fixes v7 — Multi-strategy parallel trading.

MAJOR UPGRADE: Replaces single-strategy engine with 4 parallel strategies,
each with its own capital allocation, P&L tracking, and independent signal
generation. After 3-7 days of running, you can compare which strategy
performs best and reallocate capital accordingly.

Strategies:
  A: Momentum 24h     (3000 USDT) — top movers by |changePct| >= 1.5%
  B: Mean Reversion   (2500 USDT) — RSI < 30 LONG / RSI > 70 SHORT
  C: Range Breakout   (2500 USDT) — breaks rolling 60-tick high/low
  D: Vol Squeeze      (2000 USDT) — Bollinger squeeze + first expansion

Bug fixes applied:
  ✓ Circuit breakers enforced (drawdown > 25% stops new entries)
  ✓ checkStops() runs even when trading paused
  ✓ Trailing stop + break-even work on ALL positions (manual + auto)
  ✓ SL/TP are ATR-based (not stale 24h range)
  ✓ maxCorrelatedPositions enforced via sector grouping
  ✓ Time stop: 4h max hold
  ✓ Cooldown 30min post-stop-out per token
  ✓ Only WS-connected tokens (Coinbase) eligible for auto-trading
  ✓ 5-min console report shows per-strategy performance

Files modified:
  1. src/lib/paper-trading-engine.ts  — COMPLETE REPLACEMENT with v3
  2. src/stores/trading-store.ts      — add `strategy` to Position,
                                        add `strategies_perf` to State
  3. src/lib/use-trading-socket.ts    — pass `strategies_perf` through

Run: python3 /home/z/my-project/scripts/fix_ppmt_v7_multi_strategy.py
"""

import sys
import shutil
from pathlib import Path

ROOT = Path("/tmp/my-project")
errors = []
applied = []

# ─── 1. Replace paper-trading-engine.ts with v3 ──────────────────────
print("\n=== 1. Replace paper-trading-engine.ts with v3 ===")
SRC_ENGINE = Path("/home/z/my-project/scripts/terminal/paper-trading-engine-v3.ts")
DST_ENGINE = ROOT / "src/lib/paper-trading-engine.ts"

if not SRC_ENGINE.exists():
    errors.append(f"Source engine file not found: {SRC_ENGINE}")
else:
    try:
        DST_ENGINE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC_ENGINE, DST_ENGINE)
        applied.append(f"[1: engine v3] OK ({DST_ENGINE.name}, {SRC_ENGINE.stat().st_size} bytes)")
    except Exception as e:
        errors.append(f"[1: engine v3] Failed: {e}")


# ─── 2. trading-store.ts: add strategy field + strategies_perf ───────
print("\n=== 2. trading-store.ts: add strategy + strategies_perf ===")
TS = ROOT / "src/stores/trading-store.ts"

def edit(path: Path, old: str, new: str, label: str):
    if not path.exists():
        errors.append(f"[{label}] File not found: {path}")
        return
    src = path.read_text()
    if old not in src:
        errors.append(f"[{label}] Pattern not found in {path.name}")
        return
    count = src.count(old)
    if count > 1:
        errors.append(f"[{label}] Pattern matches {count}x in {path.name}")
        return
    path.write_text(src.replace(old, new, 1))
    applied.append(f"[{label}] OK ({path.name})")

# 2a. Add `strategy` field to Position interface
edit(
    TS,
    old="""  pnl_pct: number
  pnl_usdt: number
  expected_sequence?: string[][]
  sequence_index?: number
}""",
    new="""  pnl_pct: number
  pnl_usdt: number
  strategy?: string  // 'A' | 'B' | 'C' | 'D' — which strategy opened this
  expected_sequence?: string[][]
  sequence_index?: number
}""",
    label="2a: Position.strategy field",
)

# 2b. Add `strategies_perf` to TradingState interface (after riskRewardRatio)
edit(
    TS,
    old="""  kellyPercent: number           // calculated Kelly %
  suggestedPositionSize: number  // calculated position size in USDT
  riskRewardRatio: number        // current R:R of open position

  // Actions""",
    new="""  kellyPercent: number           // calculated Kelly %
  suggestedPositionSize: number  // calculated position size in USDT
  riskRewardRatio: number        // current R:R of open position

  // ─── Multi-Strategy Performance ─────────────
  strategies_perf: Record<string, {
    name: string
    description: string
    cash: number
    allocated: number
    realized_pnl: number
    unrealized_pnl: number
    total_pnl_pct: number
    total_trades: number
    winning_trades: number
    win_rate: number
    open_positions: number
    last_signal_time: number
    color: string
  }>

  // Actions""",
    label="2b: TradingState.strategies_perf",
)

# 2c. Add default value for strategies_perf in initialState
edit(
    TS,
    old="""  kellyPercent: 0,
  suggestedPositionSize: 0,
  riskRewardRatio: 0,
}""",
    new="""  kellyPercent: 0,
  suggestedPositionSize: 0,
  riskRewardRatio: 0,
  strategies_perf: {} as Record<string, any>,
}""",
    label="2c: initialState.strategies_perf default",
)


# ─── 3. use-trading-socket.ts: pass strategies_perf through ──────────
print("\n=== 3. use-trading-socket.ts: pass strategies_perf ===")
HOOK = ROOT / "src/lib/use-trading-socket.ts"

# 3a. In applyState() — add strategies_perf to the setState call
edit(
    HOOK,
    old="""        kellyPercent: data.kelly_percent || 0,
        suggestedPositionSize: data.suggested_position_size || 0,
        riskRewardRatio: data.risk_reward_ratio || 0,
      })""",
    new="""        kellyPercent: data.kelly_percent || 0,
        suggestedPositionSize: data.suggested_position_size || 0,
        riskRewardRatio: data.risk_reward_ratio || 0,
        strategies_perf: data.strategies_perf || {},
      })""",
    label="3a: applyState strategies_perf",
)

# 3b. In storeUpdate() (inside emit callback) — add strategies_perf
edit(
    HOOK,
    old="""        kellyPercent: state.kelly_percent || 0,
        suggestedPositionSize: state.suggested_position_size || 0,
        riskRewardRatio: state.risk_reward_ratio || 0,
      })""",
    new="""        kellyPercent: state.kelly_percent || 0,
        suggestedPositionSize: state.suggested_position_size || 0,
        riskRewardRatio: state.risk_reward_ratio || 0,
        strategies_perf: state.strategies_perf || {},
      })""",
    label="3b: storeUpdate strategies_perf",
)


# ─── Report ──────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  PPMT Terminal Fixes v7 — Multi-strategy parallel trading")
print("=" * 70)
if applied:
    print(f"\nApplied {len(applied)} edits:")
    for line in applied:
        print(f"  + {line}")
if errors:
    print(f"\n{len(errors)} errors:")
    for line in errors:
        print(f"  - {line}")
    sys.exit(1)

print("\nAll edits applied successfully.")
print("\n" + "=" * 70)
print("  WHAT CHANGED")
print("=" * 70)
print("""
ENGINE (complete rewrite):
  ✓ 4 strategies run in parallel, each with own capital:
      A: Momentum 24h     3000 USDT  (15s cooldown, top 3 movers)
      B: Mean Reversion   2500 USDT  (30s cooldown, RSI < 30 / > 70)
      C: Range Breakout   2500 USDT  (10s cooldown, 60-tick high/low)
      D: Vol Squeeze      2000 USDT  (60s cooldown, Bollinger squeeze)
  ✓ SL/TP now ATR-based (reactive to recent volatility):
      A: SL 1.5×ATR  TP 3×ATR  CatSL 4×ATR   (RR 2:1)
      B: SL 1.5×ATR  TP 2×ATR  CatSL 3×ATR   (RR 1.33:1, contrarian)
      C: SL 1.0×ATR  TP 3×ATR  CatSL 2×ATR   (RR 3:1, tight SL for fakes)
      D: SL 1.0×ATR  TP 4×ATR  CatSL 2×ATR   (RR 4:1, big TP for expansion)
  ✓ Circuit breakers enforced (drawdown > 25% stops new entries)
  ✓ checkStops() runs even when trading is paused
  ✓ Trailing stop + break-even work on manual entries too
  ✓ Time stop: 4h max hold (closes zombie positions)
  ✓ Cooldown 30min after SL/CatSL per token (no re-entry death spiral)
  ✓ Only 52 WS-connected tokens eligible for auto-trading
  ✓ maxCorrelatedPositions enforced (3 per sector: btc/eth/sol/l1/etc.)
  ✓ 5-min console report shows per-strategy P&L for comparison

STORE:
  ✓ Position interface: added `strategy?: string` field
  ✓ TradingState: added `strategies_perf` for per-strategy UI display

HOOK:
  ✓ Passes `strategies_perf` through to store in both applyState + storeUpdate
""")
print("Next steps on your Mac:")
print("  1. python3 /home/z/my-project/scripts/fix_ppmt_v7_multi_strategy.py")
print("  2. Restart `next dev` (Ctrl+C, then npm run dev)")
print("  3. Watch console for 5-min strategy reports")
print("  4. After 3-7 days, compare strategies and reallocate capital")
print("  5. git add . && git commit -m 'v7: multi-strategy parallel + bug fixes'")
print("  6. git push origin terminal-web")
