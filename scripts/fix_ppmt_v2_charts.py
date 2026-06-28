#!/usr/bin/env python3
"""
PPMT Terminal Fixes v2 — Part 2: SVG chart defensive guards.

The polygon/polyline NaN errors:
  <polygon> attribute points: Expected number, "NaN,55 300,60 0,…"
  <polyline> attribute points: Expected number, "NaN,55"

…originate in src/components/trading/performance-panel.tsx line 37:
  const x = (i / (recentEquity.length - 1)) * chartW

When `recentEquity.length === 1` (initial store state with just [10000]),
`i / (1 - 1)` = `0 / 0` = NaN, producing the "NaN,55" point string.

After fixing the engine's null-price crash (v2 part 1), the equityCurve
will be populated on every tick and the NaN case won't trigger in normal
operation. But we should also defend the chart components themselves so
they never render invalid SVG geometry.

Run:  python3 /home/z/my-project/scripts/fix_ppmt_v2_charts.py
"""

import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
COMP = ROOT / "src/components/trading"

errors = []
applied = []


def edit_file(path: Path, old: str, new: str, label: str):
    if not path.exists():
        errors.append(f"[{label}] File not found: {path}")
        return
    src = path.read_text()
    if old not in src:
        errors.append(f"[{label}] Pattern not found in {path}")
        return
    if old == new:
        errors.append(f"[{label}] old == new (no-op) in {path}")
        return
    count = src.count(old)
    if count > 1:
        errors.append(f"[{label}] Pattern matches {count} times in {path}")
        return
    path.write_text(src.replace(old, new, 1))
    applied.append(f"[{label}] OK ({path.relative_to(ROOT)})")


# ─── performance-panel.tsx — guard against single-point equity curve ───
edit_file(
    COMP / "performance-panel.tsx",
    old="""  // Equity curve chart (simple SVG)
  // INITIAL_CAPITAL (10000) imported as the baseline reference.
  const recentEquity = equityCurve.slice(-100)
  const eqMin = Math.min(...recentEquity, INITIAL_CAPITAL)
  const eqMax = Math.max(...recentEquity, INITIAL_CAPITAL)
  const eqRange = eqMax - eqMin || 1

  const chartH = 60
  const chartW = 300

  const points = recentEquity.map((v, i) => {
    const x = (i / (recentEquity.length - 1)) * chartW
    const y = chartH - ((v - eqMin) / eqRange) * (chartH - 10) - 5
    return `${x},${y}`
  }).join(' ')

  // Area fill
  const areaPoints = points +
    ` ${chartW},${chartH} 0,${chartH}`""",
    new="""  // Equity curve chart (simple SVG).
  // INITIAL_CAPITAL (10000) imported as the baseline reference.
  // Defensive: when equityCurve has 0 or 1 entries (engine hasn't ticked
  // yet, or snapshot failed), the i/(length-1) division would produce NaN
  // and the SVG would render <polyline points="NaN,55"> which the browser
  // rejects. Fall back to a flat baseline at INITIAL_CAPITAL.
  const recentEquity = equityCurve.slice(-100)
  const safeEquity = recentEquity.length >= 2
    ? recentEquity.filter(v => typeof v === 'number' && isFinite(v))
    : [INITIAL_CAPITAL, INITIAL_CAPITAL]
  const eqMin = Math.min(...safeEquity, INITIAL_CAPITAL)
  const eqMax = Math.max(...safeEquity, INITIAL_CAPITAL)
  const eqRange = (eqMax - eqMin) || 1

  const chartH = 60
  const chartW = 300

  const points = safeEquity.map((v, i) => {
    // Guard against zero-division when length === 1 (shouldn't happen
    // because we forced safeEquity to >= 2 entries, but be paranoid).
    const denom = Math.max(1, safeEquity.length - 1)
    const x = (i / denom) * chartW
    const y = chartH - ((v - eqMin) / eqRange) * (chartH - 10) - 5
    return `${x},${y}`
  }).join(' ')

  // Area fill
  const areaPoints = points +
    ` ${chartW},${chartH} 0,${chartH}`""",
    label="performance-panel.tsx: NaN-safe equity curve",
)


# ─── Report ─────────────────────────────────────────────────────────────
print("\n=== PPMT Terminal Fixes v2 — Part 2 (charts) ===\n")
if applied:
    print(f"Applied {len(applied)} edits:")
    for line in applied:
        print(f"  ✓ {line}")
if errors:
    print(f"\n{len(errors)} errors:")
    for line in errors:
        print(f"  ✗ {line}")
    sys.exit(1)
print("\nAll edits applied successfully.")
