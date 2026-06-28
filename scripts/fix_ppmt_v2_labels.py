#!/usr/bin/env python3
"""
PPMT Terminal Fixes v2 — Part 3: form accessibility (label htmlFor + input id).

The browser shows these warnings:
  - "A form field element has neither an id nor a name attribute"
  - "No label associated with a form field"

Cause: <label> elements without `htmlFor`, and form controls (<Input>,
<Slider>, <Switch>, <Select>) without `id`. They're functionally OK
(clicking the label still focuses the control via DOM nesting in most
cases), but Chrome's autofill audit complains.

Fix: add unique `id` to each form control and matching `htmlFor` to
its sibling <label>.

Run:  python3 /home/z/my-project/scripts/fix_ppmt_v2_labels.py
"""

import re
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


# ─── manual-trade-panel.tsx — SYMBOL + AMOUNT labels ────────────────────
edit_file(
    COMP / "manual-trade-panel.tsx",
    old="""          <div>
            <label className="text-[10px] text-gray-500 font-mono">SYMBOL</label>
            <Select value={symbol} onValueChange={handleSymbolChange}>
              <SelectTrigger className="h-8 bg-[#121a26] border-[#1e2a3d] text-xs font-mono">
                <SelectValue />
              </SelectTrigger>""",
    new="""          <div>
            <label htmlFor="mt-symbol" className="text-[10px] text-gray-500 font-mono">SYMBOL</label>
            <Select value={symbol} onValueChange={handleSymbolChange}>
              <SelectTrigger id="mt-symbol" className="h-8 bg-[#121a26] border-[#1e2a3d] text-xs font-mono">
                <SelectValue />
              </SelectTrigger>""",
    label="manual-trade-panel.tsx: SYMBOL label",
)

edit_file(
    COMP / "manual-trade-panel.tsx",
    old="""        <div>
          <label className="text-[10px] text-gray-500 font-mono">AMOUNT (USDT)</label>
          <Input
            type="number"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="h-8 bg-[#121a26] border-[#1e2a3d] text-xs font-mono"
            placeholder="100"
            min={0}
            step={10}
          />""",
    new="""        <div>
          <label htmlFor="mt-amount" className="text-[10px] text-gray-500 font-mono">AMOUNT (USDT)</label>
          <Input
            id="mt-amount"
            name="amount"
            type="number"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="h-8 bg-[#121a26] border-[#1e2a3d] text-xs font-mono"
            placeholder="100"
            min={0}
            step={10}
          />""",
    label="manual-trade-panel.tsx: AMOUNT label",
)


# ─── money-manager.tsx — give every Slider/Switch a unique id ───────────
# Strategy: read the file, find every <Slider ... /> and <Switch ... /> and
# every <label ...>TEXT</label>, and pair them by proximity. Simpler:
# add an `id` prop to every Slider/Switch whose label is the immediately
# preceding <label>, and add `htmlFor` to that label.
mm_path = COMP / "money-manager.tsx"
if mm_path.exists():
    src = mm_path.read_text()
    # Find all label + Slider/Switch pairs.
    # Pattern: <label ...>TEXT</label> ... <Slider (props) />  OR  <Switch (props) />
    # We give each a unique id derived from the label text.
    label_pattern = re.compile(
        r'<label([^>]*?)>([A-Z][A-Z0-9 %\(\)\-]+)</label>\s*'
        r'(<Slider\b|<Switch\b)',
        re.MULTILINE
    )

    counter = [0]
    def add_id_htmlfor(m):
        counter[0] += 1
        label_attrs, label_text, control_tag = m.group(1), m.group(2).strip(), m.group(3)
        # Derive a slug from the label text
        slug = re.sub(r'[^a-z0-9]+', '-', label_text.lower()).strip('-')[:20]
        uid = f"mm-{slug}"
        # If htmlFor already present, don't double-add
        if 'htmlFor' in label_attrs:
            return m.group(0)
        new_label_attrs = label_attrs + f' htmlFor="{uid}"'
        # Insert id as the first prop on the control tag
        new_control_tag = control_tag + f' id="{uid}"'
        return f'<label{new_label_attrs}>{label_text}</label>\n            {new_control_tag}'

    new_src = label_pattern.sub(add_id_htmlfor, src)
    if new_src != src:
        mm_path.write_text(new_src)
        applied.append(f"[money-manager.tsx: {counter[0]} label+control pairs] OK ({mm_path.relative_to(ROOT)})")
    else:
        errors.append("[money-manager.tsx] No label+control pairs matched")


# ─── header.tsx — AUTO switch already has id="auto-mode", label has htmlFor ─
# Already correct, skip.


# ─── portfolio-manager.tsx — Switch without label ───────────────────────
# Let's just add name attributes to the Switches there to silence the
# autofill warning. They're toggles for token activation, not form fields
# in the traditional sense, but Chrome still complains.
pm_path = COMP / "portfolio-manager.tsx"
if pm_path.exists():
    src = pm_path.read_text()
    # Find <Switch ... checked=... onCheckedChange=... /> patterns and add
    # an aria-label so screen readers + autofill audit are happy.
    # We won't add `id` because these are dynamically rendered in a list.
    new_src = re.sub(
        r'<Switch\s+([^>]*?)(?<!\s)',
        lambda m: '<Switch ' + m.group(1),
        src,
        count=0
    )
    # Actually simpler: just leave portfolio-manager alone, its Switches
    # are inside buttons that already have text labels. The browser
    # warning is about manual-trade-panel and money-manager only.
    # Skip this file.


# ─── Report ─────────────────────────────────────────────────────────────
print("\n=== PPMT Terminal Fixes v2 — Part 3 (form labels) ===\n")
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
