#!/usr/bin/env python3
"""Fix the mm-before-declaration bug introduced by apply_ppmt_fixes.py."""
from pathlib import Path

p = Path("/tmp/my-project/src/components/trading/money-manager.tsx")
text = p.read_text()

old = (
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
    "  }, [mm.positionSizingMethod])\n"
    "\n"
    "  const mm = moneyManager\n"
    "\n"
    "  const updateMM = (updates: Partial<MoneyManagerSettings>) => {"
)

new = (
    "  const [expandedSection, setExpandedSection] = useState<string>('risk')\n"
    "\n"
    "  const mm = moneyManager\n"
    "\n"
    "  // Optimistic local state for the position-sizing Select — prevents the\n"
    "  // 'loop back' flicker where Radix Select briefly shows the old value\n"
    "  // between user click and the next 1.5s engine snapshot.\n"
    "  const [sizingMethodLocal, setSizingMethodLocal] = useState<string>(\n"
    "    mm.positionSizingMethod\n"
    "  )\n"
    "  // Sync from store when the store value changes (e.g. on engine reset)\n"
    "  useEffect(() => {\n"
    "    setSizingMethodLocal(mm.positionSizingMethod)\n"
    "  }, [mm.positionSizingMethod])\n"
    "\n"
    "  const updateMM = (updates: Partial<MoneyManagerSettings>) => {"
)

if old not in text:
    print("[FAIL] pattern not found — already fixed?")
    raise SystemExit(1)

p.write_text(text.replace(old, new, 1))
print("[OK] moved const mm = moneyManager before sizingMethodLocal declaration")
