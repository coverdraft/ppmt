# Worklog - PPMT (Progressive Pattern Matching Trie Engine)

---
Task ID: v0.2.7
Agent: Main
Task: Fix v0.2.7 crash (UnboundLocalError) — ATR-based SL/TP, trailing stop, expected-value path walking

Work Log:
- v0.2.7 introduced ATR-based SL/TP, trailing stop activation at 50% TP distance, and expected-value path walking
- CRASH: `UnboundLocalError: cannot access local variable 'np'` at line 734 in paper_trader.py
- Root cause: `import numpy as np` inside the `run()` method (line 734) shadowed the module-level import (line 36)
- Python determines variable scope at function definition time — an import/assignment inside a function makes it local throughout that function
- Fix: Removed the duplicate `import numpy as np` from inside `run()`, keeping only the module-level import on line 36
- Previous fixes (v0.2.3-v0.2.6): daily loss limit permanent block, R:R too low, max drawdown circuit breaker, SHORT trades losing

Stage Summary:
- v0.2.7 crash fixed but results still poor (0 trades due to crash)
- Key learning: NEVER import inside a function when module-level import exists

---
Task ID: v0.2.8
Agent: Main
Task: Implement Living Trie — feedback loop where Trie learns from each trade outcome

Work Log:
- Designed and implemented `_record_observation()` function (~80 lines) — the core of the Living Trie
  - Finds the Trie node for the matched entry pattern (with prefix fallback)
  - Updates node metadata with actual outcome (move_pct, drawdown_pct, favorable_pct, duration, won, next_symbol)
  - If next_symbol is NOT already a child → creates new child node (Trie GROWS)
  - Returns {"observations": N, "new_nodes": M} for tracking
- Added `PaperTraderConfig.living_trie: bool = True` — enable/disable Living Trie
- Added `PaperTraderConfig.metadata_propagation_interval: int = 200` — how often to re-propagate
- Added `PaperTrade.entry_sym_idx: int = 0` — SAX symbol index at trade entry
- Added `PaperTrade.trie_updated: bool = False` — prevent double-updating same trade
- Living Trie integration at EVERY trade close point:
  - SL hit (stop_loss / trailing_stop)
  - TP hit (take_profit)
  - Pattern break
  - End of data
- Periodic re-propagation every 200 SAX symbol steps (configurable)
- Final metadata propagation at end of paper trading
- Living Trie statistics printed at end: observations, new nodes, propagations, Trie growth
- Trie SAVED after paper trading so Living Trie persists across runs
- Fixed v0.2.7 crash: removed duplicate `import numpy as np` from inside `run()` method
- Updated versions: __init__.py, pyproject.toml, cli/main.py → 0.2.8

Key Architecture — Living Trie Feedback Loop:
```
Trie predicts → Trade executes → Outcome observed → Node updated ↑
    ↓                                                              │
    └────── Next prediction uses improved metadata ────────────────┘
```

- Each node has INDEPENDENT metadata
- Parent metadata is AGGREGATED (weighted average) from children via `propagate_metadata()`
- When a new SAX symbol appears that isn't in the Trie, a new child node is created
- This means the Trie discovers patterns it hasn't seen in the original historical data
- Over time, the Trie becomes more accurate as it accumulates observations

FIRST PROFITABLE RUN (v0.2.8):
- 509 trades, 47.0% WR, +20.11% P&L, Profit Factor 1.11
- Living Trie: 509 observations recorded, 488 new nodes created, Trie grew 2420→2908 patterns
- SHORT trades now appearing and some winning (trades 126, 190, 330, 341, 396, 407, 430, 432, 508)
- Previous best: v0.2.6 had 173 trades, 38.7% WR, -18.85% P&L

Remaining gaps vs target (960+ trades/symbol, 60%+ WR, 1400%+ P&L):
- WR: 47% vs 60%+ target (need +13pp)
- Max DD: 46.1% (very high)
- Catastrophic individual losses: -10.20%, -9.75%
- Avg confidence: 14.9% (low)
- Avg quality: 0.12 (low)

Stage Summary:
- v0.2.8 = FIRST PROFITABLE VERSION of PPMT paper trading
- Living Trie feedback loop working: Trie learns and grows from each trade
- Major architectural milestone: Trie is now "alive" — it improves with every observation
- Files modified: paper_trader.py, __init__.py, pyproject.toml, cli/main.py
