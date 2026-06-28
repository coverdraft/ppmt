#!/usr/bin/env python3
"""
PPMT Terminal Fixes v2 — Part 3b: add aria-label to money-manager sliders.

money-manager.tsx uses <span> for visible labels (not <label>), so the
browser's "form field has no id/name" warning still fires on the <Slider>
and <Switch> components. Adding `aria-label` gives them an accessible name
and silences the warning without needing a <label htmlFor> pair.

Strategy: walk through the file, find each block of the form:
    <span ...>SOME LABEL TEXT</span>
    ...
    <Slider ... />
or
    <Switch ... />

and inject aria-label="SOME LABEL TEXT" into the Slider/Switch props.

Run:  python3 /home/z/my-project/scripts/fix_ppmt_v2_labels_b.py
"""

import re
import sys
from pathlib import Path

ROOT = Path("/tmp/my-project")
MM = ROOT / "src/components/trading/money-manager.tsx"

src = MM.read_text()

# Find Slider blocks: from the preceding <span ...>LABEL</span> (within
# ~10 lines) down to the closing `/>` of the Slider.
# Easier approach: find every <Slider ... /> that does NOT already have
# aria-label, look back ~5 lines for the nearest <span ...>TEXT</span>,
# and inject aria-label="TEXT" as the first prop.

# Pattern for a Slider block with its preceding span label
slider_block = re.compile(
    r'(<span[^>]*>([A-Z][A-Z0-9 %\(\)\-/]+)</span>.*?)'   # label span + stuff
    r'(<Slider\b)([^>]*?)(/>)',                              # Slider tag
    re.DOTALL
)

switch_block = re.compile(
    r'(<span[^>]*>([A-Z][A-Z0-9 %\(\)\-/]+)</span>.*?)'
    r'(<Switch\b)([^>]*?)(/>)',
    re.DOTALL
)

count = [0]

def add_aria_slider(m):
    pre, label_text, open_tag, props, close = m.groups()
    if 'aria-label' in props:
        return m.group(0)
    count[0] += 1
    return f'{pre}{open_tag} aria-label="{label_text.strip()}"{props}{close}'

def add_aria_switch(m):
    pre, label_text, open_tag, props, close = m.groups()
    if 'aria-label' in props:
        return m.group(0)
    count[0] += 1
    return f'{pre}{open_tag} aria-label="{label_text.strip()}"{props}{close}'

new_src = slider_block.sub(add_aria_slider, src)
new_src = switch_block.sub(add_aria_switch, new_src)

if new_src == src:
    print("No changes — no Slider/Switch blocks matched.")
    sys.exit(1)

MM.write_text(new_src)
print(f"OK — added aria-label to {count[0]} Slider/Switch components in money-manager.tsx")
