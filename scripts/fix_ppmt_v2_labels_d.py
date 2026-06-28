#!/usr/bin/env python3
"""
PPMT Terminal Fixes v2 — Part 3d: aria-labels with corrected regex.

Previous attempt failed because `[^>]*?` stopped at the `>` inside `=>`
arrow functions in JSX props like onValueChange={([val]) => ...}.

Fix: use `.*?` with DOTALL flag (lazy match anything across newlines up
to the first `/>`).
"""

import re
import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
MM = ROOT / "src/components/trading/money-manager.tsx"

src = MM.read_text()

# Match <Slider ... /> or <Switch ... /> allowing > inside props (e.g. =>)
pattern = re.compile(r'<(Slider|Switch)\b(.*?)/>', re.DOTALL)

count = 0
out = []
last_end = 0

for m in pattern.finditer(src):
    tag = m.group(1)
    props = m.group(2)
    full = m.group(0)

    if 'aria-label' in props:
        continue

    # Look back 800 chars for the most recent <span ...>TEXT</span>
    start = max(0, m.start() - 800)
    window = src[start:m.start()]
    span_matches = list(re.finditer(r'<span[^>]*>\s*([A-Z][A-Z0-9 %\(\)\-/]+)\s*</span>', window))

    if not span_matches:
        continue

    label_text = span_matches[-1].group(1).strip()
    if len(label_text) > 40:
        label_text = label_text[:40]

    # Inject aria-label as the first prop after the tag name
    new_full = f'<{tag} aria-label="{label_text}"{props}/>'
    out.append(src[last_end:m.start()])
    out.append(new_full)
    last_end = m.end()
    count += 1

out.append(src[last_end:])
new_src = ''.join(out)

if new_src == src:
    print("No changes applied.")
    sys.exit(1)

MM.write_text(new_src)
print(f"OK — added aria-label to {count} Slider/Switch components in money-manager.tsx")
