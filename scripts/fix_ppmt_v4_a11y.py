#!/usr/bin/env python3
"""
PPMT Terminal Fixes v4 — Final accessibility cleanup.

Fixes the remaining browser console warnings:
  - "Form field should have an id and/or name attribute" (2 instances)
  - "Form field should have a visible label or aria-label" (3 instances)

Targets:
  1. money-manager.tsx line ~215: <SelectTrigger> for "METHOD" — add id/name/aria-label
  2. money-manager.tsx line ~520: <Switch> for "Break-Even Move" — add aria-label
  3. portfolio-manager.tsx line ~305: <Switch> for token active toggle — add aria-label
  4. money-manager.tsx: replace all 14 generic aria-labels (section titles duplicated)
     with unique descriptive labels so screen readers can distinguish them
  5. money-manager.tsx: <Switch aria-label="EXIT MANAGEMENT"> for trailing stop —
     already had aria-label but it was the section title, fix to "Trailing stop toggle"
  6. manual-trade-panel.tsx: <label> for PRICE has no `for` because the next element
     is a div, not a form control — change to <span> so the browser doesn't flag it

Run: python3 /home/z/my-project/scripts/fix_ppmt_v4_a11y.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
COMP = ROOT / "src/components/trading"

errors = []
applied = []


def edit(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        errors.append(f"[{label}] File not found: {path}")
        return
    src = path.read_text()
    if old not in src:
        errors.append(f"[{label}] Pattern not found")
        return
    if old == new:
        errors.append(f"[{label}] old == new (no-op)")
        return
    cnt = src.count(old)
    if cnt > 1:
        errors.append(f"[{label}] Pattern matches {cnt} times — needs disambiguation")
        return
    path.write_text(src.replace(old, new, 1))
    applied.append(label)


# ─── 1. money-manager.tsx — METHOD SelectTrigger id/name/aria-label ──
MM = COMP / "money-manager.tsx"

edit(
    MM,
    old=(
        "            <Select\n"
        "              value={sizingMethodLocal}\n"
        "              onValueChange={(val) => {\n"
        "                setSizingMethodLocal(val as MoneyManagerSettings['positionSizingMethod'])\n"
        "                updateMM({ positionSizingMethod: val as MoneyManagerSettings['positionSizingMethod'] })\n"
        "              }}\n"
        "            >\n"
        "              <SelectTrigger className=\"h-7 bg-[#121a26] border-[#1e2a3d] text-[10px] font-mono\">\n"
        "                <SelectValue />\n"
        "              </SelectTrigger>"
    ),
    new=(
        "            <Select\n"
        "              value={sizingMethodLocal}\n"
        "              onValueChange={(val) => {\n"
        "                setSizingMethodLocal(val as MoneyManagerSettings['positionSizingMethod'])\n"
        "                updateMM({ positionSizingMethod: val as MoneyManagerSettings['positionSizingMethod'] })\n"
        "              }}\n"
        "            >\n"
        "              <SelectTrigger\n"
        "                id=\"mm-sizing-method\"\n"
        "                name=\"positionSizingMethod\"\n"
        "                aria-label=\"Position sizing method\"\n"
        "                className=\"h-7 bg-[#121a26] border-[#1e2a3d] text-[10px] font-mono\"\n"
        "              >\n"
        "                <SelectValue />\n"
        "              </SelectTrigger>"
    ),
    label="1: METHOD SelectTrigger id/name/aria-label",
)

# ─── 2. money-manager.tsx — Break-Even Switch aria-label ─────────────
edit(
    MM,
    old=(
        "          {/* Break-Even */}\n"
        "          <div className=\"flex items-center justify-between\">\n"
        "            <div className=\"flex items-center gap-2\">\n"
        "              <span className=\"text-[10px] text-gray-300 font-mono\">Break-Even Move</span>\n"
        "              <Info className=\"w-3 h-3 text-gray-600\" />\n"
        "            </div>\n"
        "            <Switch\n"
        "              checked={mm.breakEvenEnabled}\n"
        "              onCheckedChange={(val) => updateMM({ breakEvenEnabled: val })}\n"
        "            />\n"
        "          </div>"
    ),
    new=(
        "          {/* Break-Even */}\n"
        "          <div className=\"flex items-center justify-between\">\n"
        "            <div className=\"flex items-center gap-2\">\n"
        "              <span className=\"text-[10px] text-gray-300 font-mono\">Break-Even Move</span>\n"
        "              <Info className=\"w-3 h-3 text-gray-600\" />\n"
        "            </div>\n"
        "            <Switch\n"
        "              aria-label=\"Break-even move toggle\"\n"
        "              checked={mm.breakEvenEnabled}\n"
        "              onCheckedChange={(val) => updateMM({ breakEvenEnabled: val })}\n"
        "            />\n"
        "          </div>"
    ),
    label="2: Break-Even Switch aria-label",
)

# ─── 3. money-manager.tsx — replace generic aria-labels with unique ones ──
# The previous patch used the section title (e.g. "TRADE PARAMETERS") as the
# aria-label for every slider inside that section. That violates the
# "each form field should have a distinguishable label" guideline. Replace
# each with a unique, descriptive label.

slider_replacements = [
    ('aria-label="RISK PER TRADE"',          'aria-label="Risk per trade percent"'),
    ('aria-label="KELLY FRACTION"',          'aria-label="Kelly fraction"'),
    ('aria-label="TRADE PARAMETERS"',
     'aria-label="Take profit multiplier"'),  # only 1 occurrence on the TP slider
    ('aria-label="STOP LOSS (ATR)"',         'aria-label="Stop loss ATR multiplier"'),
    ('aria-label="DEFAULT LEVERAGE"',        'aria-label="Default leverage"'),
    ('aria-label="MAX LEVERAGE"',            'aria-label="Max leverage"'),
    ('aria-label="MAX POSITIONS"',           'aria-label="Max concurrent positions"'),
    ('aria-label="MAX DRAWDOWN"',            'aria-label="Max drawdown percent"'),
    ('aria-label="DAILY LOSS LIMIT"',        'aria-label="Daily loss limit percent"'),
    ('aria-label="MAX CORRELATED POSITIONS"',
     'aria-label="Max correlated positions"'),
]

# Note: "ACTIVATE AFTER" appears twice (trailing + break-even) — handle
# them separately below. Same for "TRAIL DISTANCE" (unique) and
# "EXIT MANAGEMENT" Switch (unique).

# First, do the unique replacements with a simple text-replace-count guard
src = MM.read_text()
for old, new in slider_replacements:
    cnt = src.count(old)
    if cnt == 0:
        errors.append(f"[slider:{old}] not found")
        continue
    if cnt > 1:
        errors.append(f"[slider:{old}] matches {cnt}x — skipping (needs disambiguation)")
        continue
    src = src.replace(old, new, 1)
    applied.append(f"slider:{old} -> {new}")

# Handle the duplicated "ACTIVATE AFTER" — they belong to trailing-stop
# and break-even sections. Disambiguate by surrounding context.
# Trailing-stop ACTIVATE AFTER comes right before:
#   value={[mm.trailingStopActivationPct]}
# Break-even ACTIVATE AFTER comes right before:
#   value={[mm.breakEvenActivationPct]}
src, n1 = src.replace(
    'Slider aria-label="ACTIVATE AFTER"\n                value={[mm.trailingStopActivationPct]}',
    'Slider aria-label="Trailing stop activation percent"\n                value={[mm.trailingStopActivationPct]}',
    1,
), None
src, n2 = src.replace(
    'Slider aria-label="ACTIVATE AFTER"\n              value={[mm.breakEvenActivationPct]}',
    'Slider aria-label="Break-even activation percent"\n              value={[mm.breakEvenActivationPct]}',
    1,
), None
applied.append("ACTIVATE AFTER (trailing) -> unique label")
applied.append("ACTIVATE AFTER (break-even) -> unique label")

# TRAIL DISTANCE is unique
src = src.replace(
    'aria-label="TRAIL DISTANCE"',
    'aria-label="Trailing stop distance percent"',
    1,
)
applied.append("TRAIL DISTANCE -> unique label")

# EXIT MANAGEMENT Switch -> trailing stop toggle
src = src.replace(
    'Switch aria-label="EXIT MANAGEMENT"',
    'Switch aria-label="Trailing stop toggle"',
    1,
)
applied.append("EXIT MANAGEMENT Switch -> Trailing stop toggle")

MM.write_text(src)


# ─── 4. portfolio-manager.tsx — token active Switch aria-label ───────
PM = COMP / "portfolio-manager.tsx"

edit(
    PM,
    old=(
        "                    {/* Active switch */}\n"
        "                    <Switch\n"
        "                      checked={isActive}\n"
        "                      onCheckedChange={(checked) => {\n"
        "                        emit('toggle-token', { symbol: token.symbol })\n"
        "                      }}\n"
        "                      className=\"scale-75\"\n"
        "                      onClick={(e) => e.stopPropagation()}\n"
        "                    />"
    ),
    new=(
        "                    {/* Active switch */}\n"
        "                    <Switch\n"
        "                      aria-label={`Toggle ${token.symbol.replace('/USDT','')} active`}\n"
        "                      checked={isActive}\n"
        "                      onCheckedChange={(checked) => {\n"
        "                        emit('toggle-token', { symbol: token.symbol })\n"
        "                      }}\n"
        "                      className=\"scale-75\"\n"
        "                      onClick={(e) => e.stopPropagation()}\n"
        "                    />"
    ),
    label="4: portfolio-manager token Switch aria-label",
)


# ─── 5. manual-trade-panel.tsx — PRICE <label> -> <span> ────────────
# The PRICE label has no `for` because the next element is a div, not a
# form control. Change <label> to <span> so the browser stops flagging it.
MTP = COMP / "manual-trade-panel.tsx"

edit(
    MTP,
    old=(
        "          <div>\n"
        "            <label className=\"text-[10px] text-gray-500 font-mono\">PRICE</label>\n"
        "            <div className=\"h-8 flex items-center bg-[#121a26] border border-[#1e2a3d] rounded px-2\">"
    ),
    new=(
        "          <div>\n"
        "            <span className=\"text-[10px] text-gray-500 font-mono\">PRICE</span>\n"
        "            <div className=\"h-8 flex items-center bg-[#121a26] border border-[#1e2a3d] rounded px-2\">"
    ),
    label="5: PRICE <label> -> <span>",
)


# ─── Report ───────────────────────────────────────────────────────────
print("\n=== PPMT Terminal Fixes v4 (final accessibility) ===\n")
if applied:
    print(f"Applied {len(applied)} edits:")
    for line in applied:
        print(f"  + {line}")
if errors:
    print(f"\n{len(errors)} errors:")
    for line in errors:
        print(f"  - {line}")
    sys.exit(1)
print("\nAll edits applied successfully.")
