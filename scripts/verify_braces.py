#!/usr/bin/env python3
"""Verify brace/paren/bracket balance in edited files."""
from pathlib import Path

files = [
    "/tmp/my-project/src/lib/paper-trading-engine.ts",
    "/tmp/my-project/src/lib/use-trading-socket.ts",
    "/tmp/my-project/src/components/trading/money-manager.tsx",
    "/tmp/my-project/src/components/trading/brain-panel.tsx",
    "/tmp/my-project/src/stores/trading-store.ts",
]

for f in files:
    p = Path(f)
    if not p.exists():
        print(f"[MISS] {f}")
        continue
    text = p.read_text()
    # Strip strings/comments crudely to avoid false positives.
    # For a quick sanity check, just count raw chars — TypeScript template
    # literals and JSX make a true balance check hard, but if all three
    # counts match what they were before, we're fine.
    o, c = text.count("{"), text.count("}")
    op, cp = text.count("("), text.count(")")
    ob, cb = text.count("["), text.count("]")
    ok = "OK" if o == c and op == cp and ob == cb else "MISMATCH"
    print(f"{ok:8} {p.name:30} {{}} {o}/{c}  () {op}/{cp}  [] {ob}/{cb}")
