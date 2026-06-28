#!/usr/bin/env python3
"""Fix the typo on the useEffect dependency array line."""
from pathlib import Path

p = Path("/tmp/my-project/src/components/trading/money-manager.tsx")
text = p.read_text()

# The typo: "}, m.positionSizingMethod])" — missing "[" and one "m"
old = "  }, m.positionSizingMethod])"
new = "  }, [mm.positionSizingMethod])"

if old not in text:
    print("[FAIL] typo not found — already fixed?")
    raise SystemExit(1)

p.write_text(text.replace(old, new, 1))
print("[OK] fixed dependency array: m.positionSizingMethod] -> [mm.positionSizingMethod]")
