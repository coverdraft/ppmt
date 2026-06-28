#!/usr/bin/env python3
"""Verify brace balance for ALL edited files (full list)."""
from pathlib import Path

files = [
    "/tmp/my-project/src/lib/paper-trading-engine.ts",
    "/tmp/my-project/src/lib/use-trading-socket.ts",
    "/tmp/my-project/src/stores/trading-store.ts",
    "/tmp/my-project/src/app/page.tsx",
    "/tmp/my-project/src/components/trading/money-manager.tsx",
    "/tmp/my-project/src/components/trading/brain-panel.tsx",
    "/tmp/my-project/src/components/trading/manual-trade-panel.tsx",
    "/tmp/my-project/src/components/trading/position-panel.tsx",
    "/tmp/my-project/src/components/trading/portfolio-manager.tsx",
    "/tmp/my-project/src/components/trading/portfolio-panel.tsx",
    "/tmp/my-project/src/components/trading/performance-panel.tsx",
    "/tmp/my-project/src/components/trading/operations-chart.tsx",
]

for f in files:
    p = Path(f)
    if not p.exists():
        print(f"[MISS] {f}")
        continue
    text = p.read_text()
    o, c = text.count("{"), text.count("}")
    op, cp = text.count("("), text.count(")")
    ob, cb = text.count("["), text.count("]")
    ok = "OK" if o == c and op == cp and ob == cb else "MISMATCH"
    print(f"{ok:8} {p.name:30} {{}} {o}/{c}  () {op}/{cp}  [] {ob}/{cb}")
