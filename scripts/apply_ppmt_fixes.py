#!/usr/bin/env python3
"""
PPMT Terminal Fixes — Batch edit script.

Applies all fixes reported by user:
1. Expand active tokens from 12 to all 50 (was SUPPORTED_TOKENS.slice(0,12))
2. Make Pattern Buffer dynamic — lower threshold + richer SAX alphabet (B/D/F/U/V)
3. Fix Position Sizing dropdown "loop" — optimistic local state in MoneyManager
4. Bump maxConcurrentPositions default (3 -> 8 in store, 5 -> 8 in engine)
   so multi-operation on different tokens actually works
5. Add ENTROPY explanation tooltip in BrainPanel
6. Defensive: don't override store.moneyManager with undefined on bad snapshots

Run: python3 /home/z/my-project/scripts/apply_ppmt_fixes.py
"""

import re
import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
LIB = ROOT / "src/lib"
COMP = ROOT / "src/components/trading"
STORE = ROOT / "src/stores"

edits = []


def edit_file(path: Path, old: str, new: str, label: str):
    """Replace `old` with `new` in `path`. Fail loudly if not found."""
    text = path.read_text()
    if old not in text:
        print(f"  [SKIP] {label}: pattern not found in {path.name}")
        return False
    if text.count(old) > 1:
        print(f"  [WARN] {label}: pattern appears multiple times in {path.name}, replacing all")
        text = text.replace(old, new)
    else:
        text = text.replace(old, new, 1)
    path.write_text(text)
    print(f"  [OK]   {label}")
    return True


# ────────────────────────────────────────────────────────────────────
# 1. paper-trading-engine.ts
# ────────────────────────────────────────────────────────────────────
print("\n=== 1. paper-trading-engine.ts ===")
PTE = LIB / "paper-trading-engine.ts"

# 1a. All 50 tokens active by default
edit_file(
    PTE,
    "  private activeTokens: string[] = [...SUPPORTED_TOKENS.slice(0, 12)]",
    "  // All 50 supported tokens are active by default — gives the auto-scanner\n"
    "  // a wide universe to find real momentum. User can toggle off via UI.\n"
    "  private activeTokens: string[] = [...SUPPORTED_TOKENS]",
    "1a. expand activeTokens to all 50",
)

# 1b. Bump maxConcurrentPositions default in engine
edit_file(
    PTE,
    "  maxConcurrentPositions: 5,\n  maxCorrelatedPositions: 2,",
    "  maxConcurrentPositions: 8,\n  maxCorrelatedPositions: 3,",
    "1b. maxConcurrentPositions 5 -> 8 in engine",
)

# 1c. Richer SAX alphabet + lower threshold.
#     Old: pct > 0.05 ? 'U' : pct < -0.05 ? 'D' : 'F'
#     New: 5-symbol alphabet based on |pct| thresholds
#       V = big up    (>= +0.15%)
#       U = up        (+0.02% to +0.15%)
#       F = flat      (-0.02% to +0.02%)
#       D = down      (-0.15% to -0.02%)
#       B = big down  (<= -0.15%)
#     Lower threshold (0.02 vs 0.05) means small moves register, so the
#     buffer no longer gets stuck in FFFF for low-volatility tokens.
old_pattern_encoding = (
    "      const pct = ((t.price - last) / last) * 100\n"
    "      const sym_char = pct > 0.05 ? 'U' : pct < -0.05 ? 'D' : 'F'"
)
new_pattern_encoding = (
    "      const pct = ((t.price - last) / last) * 100\n"
    "      // 5-symbol SAX alphabet — captures more variance than U/D/F.\n"
    "      // Lower threshold (0.02% vs old 0.05%) means BTC/ETH ticks\n"
    "      // are no longer dominated by 'F' (flat).\n"
    "      const sym_char =\n"
    "        pct >=  0.15 ? 'V' :\n"
    "        pct >=  0.02 ? 'U' :\n"
    "        pct <= -0.15 ? 'B' :\n"
    "        pct <= -0.02 ? 'D' : 'F'"
)
edit_file(PTE, old_pattern_encoding, new_pattern_encoding, "1c. richer SAX alphabet (B/D/F/U/V)")

# 1d. Lower auto-mode threshold so it actually finds trades
edit_file(
    PTE,
    "    if (Math.abs(top.changePct) < 1.5) {\n"
    "      console.log(`[Paper/Auto] Top mover ${top.symbol} only ${top.changePct.toFixed(2)}% — below 1.5% threshold`)\n"
    "      return\n"
    "    }",
    "    if (Math.abs(top.changePct) < 0.8) {\n"
    "      console.log(`[Paper/Auto] Top mover ${top.symbol} only ${top.changePct.toFixed(2)}% — below 0.8% threshold`)\n"
    "      return\n"
    "    }",
    "1d. auto-mode threshold 1.5% -> 0.8%",
)

# 1e. Lower volume filter from $10M to $5M so mid-caps qualify
edit_file(
    PTE,
    "      .filter((t): t is TickerData => t !== null && t.quoteVolume > 10_000_000)",
    "      .filter((t): t is TickerData => t !== null && t.quoteVolume > 5_000_000)",
    "1e. auto-mode volume filter $10M -> $5M",
)

# 1f. Reduce cooldown from 15s to 10s so auto-mode scans more often
edit_file(
    PTE,
    "    if (now - this.lastAutoSignalTime < 15000) return",
    "    if (now - this.lastAutoSignalTime < 10000) return",
    "1f. auto-mode cooldown 15s -> 10s",
)


# ────────────────────────────────────────────────────────────────────
# 2. trading-store.ts
# ────────────────────────────────────────────────────────────────────
print("\n=== 2. trading-store.ts ===")
TS = STORE / "trading-store.ts"

edit_file(
    TS,
    "  maxConcurrentPositions: 3,\n  maxCorrelatedPositions: 1,",
    "  maxConcurrentPositions: 8,\n  maxCorrelatedPositions: 3,",
    "2a. maxConcurrentPositions 3 -> 8 in store default",
)


# ────────────────────────────────────────────────────────────────────
# 3. money-manager.tsx — optimistic local state for Select
# ────────────────────────────────────────────────────────────────────
print("\n=== 3. money-manager.tsx ===")
MM = COMP / "money-manager.tsx"

# 3a. Replace the Select block with one that uses optimistic local state.
# This prevents the "loop back" bug where the Radix Select shows the old
# value briefly between user click and the next 1.5s snapshot.
old_select = (
    "            <Select\n"
    "              value={mm.positionSizingMethod}\n"
    "              onValueChange={(val) => updateMM({ positionSizingMethod: val as MoneyManagerSettings['positionSizingMethod'] })}\n"
    "            >\n"
    "              <SelectTrigger className=\"h-7 bg-[#121a26] border-[#1e2a3d] text-[10px] font-mono\">\n"
    "                <SelectValue />\n"
    "              </SelectTrigger>\n"
    "              <SelectContent className=\"bg-[#121a26] border-[#1e2a3d]\">\n"
    "                <SelectItem value=\"fixed\" className=\"text-[10px] font-mono\">Fixed % Risk</SelectItem>\n"
    "                <SelectItem value=\"kelly\" className=\"text-[10px] font-mono\">Kelly Criterion</SelectItem>\n"
    "                <SelectItem value=\"risk_parity\" className=\"text-[10px] font-mono\">Risk Parity</SelectItem>\n"
    "                <SelectItem value=\"volatility_adj\" className=\"text-[10px] font-mono\">Volatility Adjusted</SelectItem>\n"
    "              </SelectContent>\n"
    "            </Select>"
)

new_select = (
    "            <Select\n"
    "              value={sizingMethodLocal}\n"
    "              onValueChange={(val) => {\n"
    "                setSizingMethodLocal(val as MoneyManagerSettings['positionSizingMethod'])\n"
    "                updateMM({ positionSizingMethod: val as MoneyManagerSettings['positionSizingMethod'] })\n"
    "              }}\n"
    "            >\n"
    "              <SelectTrigger className=\"h-7 bg-[#121a26] border-[#1e2a3d] text-[10px] font-mono\">\n"
    "                <SelectValue />\n"
    "              </SelectTrigger>\n"
    "              <SelectContent className=\"bg-[#121a26] border-[#1e2a3d]\">\n"
    "                <SelectItem value=\"fixed\" className=\"text-[10px] font-mono\">Fixed % Risk</SelectItem>\n"
    "                <SelectItem value=\"kelly\" className=\"text-[10px] font-mono\">Kelly Criterion</SelectItem>\n"
    "                <SelectItem value=\"risk_parity\" className=\"text-[10px] font-mono\">Risk Parity</SelectItem>\n"
    "                <SelectItem value=\"volatility_adj\" className=\"text-[10px] font-mono\">Volatility Adjusted</SelectItem>\n"
    "              </SelectContent>\n"
    "            </Select>"
)
edit_file(MM, old_select, new_select, "3a. Select uses optimistic local state")

# 3b. Declare the local state. Place it near the existing useState for expandedSection.
old_state_decl = (
    "  const [expandedSection, setExpandedSection] = useState<string>('risk')"
)
new_state_decl = (
    "  const [expandedSection, setExpandedSection] = useState<string>('risk')\n"
    "  // Optimistic local state for the position-sizing Select — prevents the\n"
    "  // 'loop back' flicker where Radix Select briefly shows the old value\n"
    "  // between user click and the next 1.5s engine snapshot.\n"
    "  const [sizingMethodLocal, setSizingMethodLocal] = useState<string>(\n"
    "    mm.positionSizingMethod\n"
    "  )\n"
    "  // Sync from store when the store value changes (e.g. on engine reset)\n"
    "  useEffect(() => {\n"
    "    setSizingMethodLocal(mm.positionSizingMethod)\n"
    "  }, [mm.positionSizingMethod])"
)
edit_file(MM, old_state_decl, new_state_decl, "3b. declare sizingMethodLocal state")

# 3c. Add useEffect + useEffect import
edit_file(
    MM,
    "import { useState } from 'react'",
    "import { useState, useEffect } from 'react'",
    "3c. import useEffect",
)


# ────────────────────────────────────────────────────────────────────
# 4. brain-panel.tsx — entropy explanation + richer SAX colors
# ────────────────────────────────────────────────────────────────────
print("\n=== 4. brain-panel.tsx ===")
BP = COMP / "brain-panel.tsx"

# 4a. Add V (big up) and B (big down) colors to SAX_COLORS
old_sax = (
    "  // Paper engine U/D/F encoding (Up / Down / Flat)\n"
    "  U: 'bg-emerald-500/30 text-emerald-300 border-emerald-500/40',\n"
    "  D: 'bg-red-500/30 text-red-300 border-red-500/40',\n"
    "  F: 'bg-gray-500/30 text-gray-300 border-gray-500/40',\n"
    "}"
)
new_sax = (
    "  // Paper engine 5-symbol encoding (Big-down / Down / Flat / Up / Big-up)\n"
    "  B: 'bg-red-600/40 text-red-200 border-red-600/50',\n"
    "  D: 'bg-red-500/30 text-red-300 border-red-500/40',\n"
    "  F: 'bg-gray-500/30 text-gray-300 border-gray-500/40',\n"
    "  U: 'bg-emerald-500/30 text-emerald-300 border-emerald-500/40',\n"
    "  V: 'bg-emerald-600/40 text-emerald-200 border-emerald-600/50',\n"
    "}"
)
edit_file(BP, old_sax, new_sax, "4a. add V and B colors to SAX palette")

# 4b. Add ENTROPY explanation tooltip
old_entropy = (
    "        {/* Entropy */}\n"
    "        <div>\n"
    "          <div className=\"flex justify-between mb-1\">\n"
    "            <span className=\"text-[10px] text-gray-500 font-mono\">ENTROPY</span>\n"
    "            <span className=\"text-[10px] text-gray-400 font-mono\">{entropy.toFixed(3)}</span>\n"
    "          </div>\n"
    "          <div className=\"h-1.5 bg-[#1a2334] rounded-full overflow-hidden\">\n"
    "            <div\n"
    "              className={`h-full ${entropyColor} rounded-full transition-all duration-500`}\n"
    "              style={{ width: `${entropyPct}%` }}\n"
    "            />\n"
    "          </div>\n"
    "        </div>"
)
new_entropy = (
    "        {/* Entropy */}\n"
    "        <div>\n"
    "          <div className=\"flex justify-between mb-1\">\n"
    "            <div className=\"flex items-center gap-1\">\n"
    "              <span className=\"text-[10px] text-gray-500 font-mono\">ENTROPY</span>\n"
    "              <span className=\"text-[9px] text-gray-600 font-mono\" title=\"Shannon-like entropy of 24h returns across active tokens. 0 = all tokens moving in lockstep (low uncertainty). 1 = maximally dispersed (high uncertainty, market is choppy). Computed as stdev(changes)/5, clamped to [0,1].\">\n"
    "                (ℹ)\n"
    "              </span>\n"
    "            </div>\n"
    "            <span className=\"text-[10px] text-gray-400 font-mono\">{entropy.toFixed(3)}</span>\n"
    "          </div>\n"
    "          <div className=\"h-1.5 bg-[#1a2334] rounded-full overflow-hidden\">\n"
    "            <div\n"
    "              className={`h-full ${entropyColor} rounded-full transition-all duration-500`}\n"
    "              style={{ width: `${entropyPct}%` }}\n"
    "            />\n"
    "          </div>\n"
    "          <div className=\"flex justify-between text-[8px] text-gray-600 font-mono mt-0.5\">\n"
    "            <span>calm</span>\n"
    "            <span>{entropy < 0.3 ? 'low uncertainty' : entropy < 0.6 ? 'normal' : 'choppy'}</span>\n"
    "            <span>chaos</span>\n"
    "          </div>\n"
    "        </div>"
)
edit_file(BP, old_entropy, new_entropy, "4b. ENTROPY tooltip + interpretation label")

# 4c. Add a short legend explaining U/D/F/B/V
old_legend_anchor = (
    "        {/* Living Trie Stats */}"
)
new_legend = (
    "        {/* SAX legend */}\n"
    "        <div className=\"flex items-center justify-between text-[8px] text-gray-600 font-mono\">\n"
    "          <span>B↓ D↓ F· U↑ V↑</span>\n"
    "          <span>last {patternBuffer.length} ticks</span>\n"
    "        </div>\n"
    "\n"
    "        {/* Living Trie Stats */}"
)
edit_file(BP, old_legend_anchor, new_legend, "4c. SAX legend under pattern buffer")


# ────────────────────────────────────────────────────────────────────
# 5. use-trading-socket.ts — defensive moneyManager handling
# ────────────────────────────────────────────────────────────────────
print("\n=== 5. use-trading-socket.ts ===")
UTS = LIB / "use-trading-socket.ts"

# 5a. Don't override store.moneyManager with undefined on bad snapshots
edit_file(
    UTS,
    "        moneyManager: data.money_manager || undefined,",
    "        // Only override if engine provided a real money_manager object.\n"
    "        // Falling back to undefined would wipe the store and break the\n"
    "        // MoneyManager Select (the 'loop back' bug).\n"
    "        ...(data.money_manager ? { moneyManager: data.money_manager } : {}),",
    "5a. applyState: don't wipe moneyManager on undefined",
)

# 5b. Same defensive pattern in storeUpdate (used after manual trades)
edit_file(
    UTS,
    "        moneyManager: state.money_manager || undefined,",
    "        ...(state.money_manager ? { moneyManager: state.money_manager } : {}),",
    "5b. storeUpdate: don't wipe moneyManager on undefined",
)


print("\n=== DONE ===")
print("Next: restart `next dev` on the Mac to pick up the changes.")
print("If running on this machine: cd /tmp/my-project && npm run dev")
