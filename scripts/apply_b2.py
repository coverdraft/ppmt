#!/usr/bin/env python3
"""Apply B2 fix: bump lastTickAt + tickCount after the for loop."""
from pathlib import Path
p = Path('/tmp/my-project/src/lib/paper-trading-engine.ts')
s = p.read_text()
old = (
    "      this.candlesProcessed++\n"
    "    }\n"
    "\n"
    "    // Prune trie periodically if it gets too large (keep top 5000)"
)
new = (
    "      this.candlesProcessed++\n"
    "      this.tickCount++\n"
    "    }\n"
    "    this.lastTickAt = Date.now()\n"
    "\n"
    "    // Prune trie periodically if it gets too large (keep top 5000)"
)
if old in s:
    s = s.replace(old, new, 1)
    p.write_text(s)
    print('B2 applied')
else:
    print('B2 pattern not found')
