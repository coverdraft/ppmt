#!/usr/bin/env python3
"""Remove dead handleSymbolChange + selectedSymbol from page.tsx."""
from pathlib import Path

p = Path("/tmp/my-project/src/app/page.tsx")
text = p.read_text()

# Remove selectedSymbol state (line 51)
old1 = "  const [selectedSymbol, setSelectedSymbol] = useState(symbol)\n  const [activeTab, setActiveTab] = useState('dashboard')"
new1 = "  const [activeTab, setActiveTab] = useState('dashboard')"
assert old1 in text, "selectedSymbol state not found"
text = text.replace(old1, new1, 1)

# Remove handleSymbolChange function (lines 83-86)
old2 = "  const handleSymbolChange = (val: string) => {\n    setSelectedSymbol(val)\n    emit('switch-symbol', { symbol: val })\n  }\n\n"
assert old2 in text, "handleSymbolChange not found"
text = text.replace(old2, "", 1)

# Update start-trading to not pass selectedSymbol (engine ignores it)
old3 = "      emit('start-trading', { symbol: selectedSymbol })"
new3 = "      emit('start-trading')"
assert old3 in text, "start-trading emit not found"
text = text.replace(old3, new3, 1)

p.write_text(text)
print("[OK] removed dead handleSymbolChange + selectedSymbol from page.tsx")
