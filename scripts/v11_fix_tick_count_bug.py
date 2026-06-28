#!/usr/bin/env python3
"""
PPMT Patch v11 — Fix tickCount / lastTickAt bug + clarity labels.

DIAGNOSIS (2026-06-29):
  User reported in terminal:
    PATTERN BUFFER
    waiting
    F F F F F D F F F F F F
    tick #0 • 12/12 symbols

  Root cause analysis (from worklog Task ID 15):

  A) "tick #0" bug (CRITICAL):
     - brain-panel.tsx line 109 shows:  tick #{tickCount.toLocaleString()} • ...
     - header.tsx v9 EXPORT destructures tickCount + lastTickAt from store
     - use-trading-socket.ts lines 112-113 do:  tickCount: data.tick_count || 0
     - trading-store.ts has tickCount: 0 default
     - BUT paper-trading-engine.ts does NOT have these fields as private members
       and does NOT expose tick_count / last_tick_at in its snapshot()
     - Result: data.tick_count is undefined → 0 → store stays 0 → UI shows "tick #0" forever
     - The v5 brain-logs patch (commit ce23b50) originally added them, but the v7
       multi-strategy rewrite (commit 750e63c) replaced the engine with
       paper-trading-engine-v3.ts and LOST these fields. All subsequent patches
       (v8 export, v9 comprehensive, v10 fixes) used them in UI without restoring
       them in the engine.

  B) "12/12 symbols" confusing label (MINOR):
     - brain-panel.tsx line 109:  {patternBuffer.length}/12 symbols
     - The "12" is the SAX buffer max size, NOT the number of active tokens
     - "X/12 symbols" reads as "X of 12 tokens" which is wrong
     - Should be "X/12 SAX" or "X/12 buffer" for clarity

  C) "waiting" + full buffer contradiction (EXPLAINED, not a bug):
     - "waiting" is the isLive indicator when lastTickAt === 0 (engine never ticked)
     - The 12 SAX letters are stale data from previous session/HMR
     - This is consistent with bug A: lastTickAt=0 because engine never sets it

FIXES:

  FIX 1: Add tickCount + lastTickAt fields to PaperTradingEngine
  FIX 2: Bump them inside updatePatternsAndTrie loop (one tickCount per symbol processed)
  FIX 3: Expose tick_count + last_tick_at in snapshot()
  FIX 4: Rename "X/12 symbols" → "X/12 SAX" in brain-panel.tsx for clarity

Files modified:
  1. src/lib/paper-trading-engine.ts     — FIX 1, 2, 3
  2. src/components/trading/brain-panel.tsx — FIX 4

Run: python3 /home/z/my-project/scripts/v11_fix_tick_count_bug.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
ENGINE = ROOT / "src/lib/paper-trading-engine.ts"
BRAIN = ROOT / "src/components/trading/brain-panel.tsx"

errors = []
applied = []


# ─── FIX 1: Add tickCount + lastTickAt fields to PaperTradingEngine ─────
print("\n=== FIX 1: Add tickCount + lastTickAt fields ===")
src = ENGINE.read_text()

OLD_FIELDS = """  private candlesProcessed: number = 0
  private maxPatternDepthObserved: number = 0
  private lastTriePruneTime: number = 0"""

NEW_FIELDS = """  private candlesProcessed: number = 0
  private maxPatternDepthObserved: number = 0
  private lastTriePruneTime: number = 0
  // FIX v11: tickCount + lastTickAt were lost when v7 rewrote the engine.
  // Without these, the BrainPanel always shows "tick #0" and the EXPORT v9
  // snapshot always reports tick_count=0 / last_tick_at=null — making it
  // look like the WebSocket loop is dead even when it's running fine.
  private tickCount: number = 0
  private lastTickAt: number = 0"""

if OLD_FIELDS not in src:
    errors.append("FIX 1: fields block pattern not found")
else:
    src = src.replace(OLD_FIELDS, NEW_FIELDS, 1)
    applied.append("FIX 1: added tickCount + lastTickAt fields to engine")
    print("  + added tickCount + lastTickAt fields to PaperTradingEngine")


# ─── FIX 2: Bump tickCount + lastTickAt inside updatePatternsAndTrie ───
print("\n=== FIX 2: Bump tickCount + lastTickAt in pattern loop ===")

OLD_BUMP = """      this.candlesProcessed++
    }
    if (now - this.lastTriePruneTime > 60 && this.livingTrie.size > 5000) {"""

NEW_BUMP = """      this.candlesProcessed++
      this.tickCount++          // FIX v11: tick = one symbol's pattern update
    }
    this.lastTickAt = Date.now()  // FIX v11: stamp the moment we finished a tick batch
    if (now - this.lastTriePruneTime > 60 && this.livingTrie.size > 5000) {"""

if OLD_BUMP not in src:
    errors.append("FIX 2: bump block pattern not found")
else:
    src = src.replace(OLD_BUMP, NEW_BUMP, 1)
    applied.append("FIX 2: bump tickCount + lastTickAt inside pattern loop")
    print("  + tickCount++ + lastTickAt = Date.now() inside updatePatternsAndTrie")


# ─── FIX 3: Expose tick_count + last_tick_at in snapshot() ─────────────
print("\n=== FIX 3: Expose tick_count + last_tick_at in snapshot ===")

OLD_SNAP = """      candles_processed: this.candlesProcessed,
      websocket_status: wsConnected ? 'connected' : 'reconnecting',"""

NEW_SNAP = """      candles_processed: this.candlesProcessed,
      tick_count: this.tickCount,            // FIX v11
      last_tick_at: this.lastTickAt,         // FIX v11: ms epoch; 0 = never ticked
      websocket_status: wsConnected ? 'connected' : 'reconnecting',"""

if OLD_SNAP not in src:
    errors.append("FIX 3: snapshot block pattern not found")
else:
    src = src.replace(OLD_SNAP, NEW_SNAP, 1)
    applied.append("FIX 3: expose tick_count + last_tick_at in snapshot")
    print("  + tick_count + last_tick_at now exposed in snapshot()")

ENGINE.write_text(src)


# ─── FIX 4: Rename "X/12 symbols" → "X/12 SAX" in brain-panel.tsx ──────
print("\n=== FIX 4: Clarify SAX buffer label in BrainPanel ===")
bp = BRAIN.read_text()

OLD_LABEL = """            tick #{tickCount.toLocaleString()} • {patternBuffer.length}/12 symbols"""

NEW_LABEL = """            tick #{tickCount.toLocaleString()} • {patternBuffer.length}/12 SAX"""

if OLD_LABEL not in bp:
    errors.append("FIX 4: brain-panel label pattern not found")
else:
    bp = bp.replace(OLD_LABEL, NEW_LABEL, 1)
    applied.append("FIX 4: renamed 'X/12 symbols' → 'X/12 SAX' in BrainPanel")
    print("  + 'X/12 symbols' → 'X/12 SAX' (clarity: this is the SAX buffer, not token count)")

BRAIN.write_text(bp)


# ─── Report ───────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  PPMT v11 — tickCount/lastTickAt restoration + label clarity")
print("=" * 70)
if applied:
    print(f"\nApplied {len(applied)} fixes:")
    for line in applied:
        print(f"  + {line}")
if errors:
    print(f"\n{len(errors)} errors:")
    for line in errors:
        print(f"  - {line}")
    sys.exit(1)

print("\nAll fixes applied.")
print()
print("Expected impact after restart:")
print("  ✓ BrainPanel will show 'tick #N' (N>0) instead of 'tick #0'")
print("  ✓ BrainPanel will show 'X/12 SAX' (clearer than 'symbols')")
print("  ✓ EXPORT v9 snapshot will report real tick_count + last_tick_at")
print("  ✓ 'seconds_since_last_tick' hint will work correctly")
print("  ✓ AI will be able to verify the engine is actually ticking")
print()
print("On your Mac:")
print("  1. git pull origin terminal-web")
print("  2. kill -9 $(lsof -ti :3000) 2>/dev/null; sleep 1; npm run dev")
print("  3. Wait 30s, look at BrainPanel — should show 'tick #N' (N grows)")
print("  4. Click EXPORT → paste in chat — tick_count should be > 0")
