# PPMT Terminal v0.31.0 — IMPLEMENTATION PLAN & TRACEABILITY

> Last updated: 2026-06-16
> Version: v0.31.0

---

## IMPLEMENTATION PLAN

### Phase 1: Persistent Trade History + Pre-Trade Validation Gate

| # | Task | File | Status |
|---|------|------|--------|
| 1.1 | Add `trades` table to SQLite schema | `data/storage.py` | DONE |
| 1.2 | Add `save_trade()`, `get_trades()`, `get_trade_summary()` methods | `data/storage.py` | DONE |
| 1.3 | Add `/api/trades` endpoint (GET, returns closed trades) | `terminal/server.py` | DONE |
| 1.4 | Add `/api/validate` endpoint (POST, runs backtest+MC, returns verdict) | `terminal/server.py` | DONE |
| 1.5 | Add `/api/auto-setup` endpoint (POST, full ingest→build→calibrate→backtest→MC pipeline) | `terminal/server.py` | DONE |
| 1.6 | Pre-trade gate in `/api/start-trading`: validate before allowing trade | `terminal/server.py` | DONE |
| 1.7 | Add `trade_history` + `validation_result` to TerminalState | `terminal/state.py` | DONE |

### Phase 2: Professional Dashboard Redesign

| # | Task | File | Status |
|---|------|------|--------|
| 2.1 | Full UI/UX redesign: Bloomberg Terminal style | `terminal/static/index.html` | DONE |
| 2.2 | Trade History panel with all operations | `terminal/static/index.html` | DONE |
| 2.3 | Auto-Setup wizard (1-click prepare token) | `terminal/static/index.html` | DONE |
| 2.4 | Validation panel (backtest + MC results + verdict) | `terminal/static/index.html` | DONE |
| 2.5 | Money Management detail panel | `terminal/static/index.html` | DONE |
| 2.6 | Multi-token node management UI | `terminal/static/index.html` | DONE |

### Phase 3: Multi-Token Live Trading

| # | Task | File | Status |
|---|------|------|--------|
| 3.1 | Multiple concurrent RealtimeTrader tasks | `terminal/server.py` | DONE |
| 3.2 | Per-child-node trading tasks | `terminal/server.py` | DONE |
| 3.3 | Dashboard multi-token view | `terminal/static/index.html` | DONE |

---

## ARCHITECTURE

### Data Flow (Simplified)

```
User clicks "Prepare Token"
  → /api/auto-setup (ingest → build → calibrate → backtest → MC)
  → Returns validation result (PASS/FAIL)

User clicks "Start Trading"
  → /api/start-trading (checks validation first)
  → If PASS: starts RealtimeTrader in background
  → If FAIL: shows why, suggests fixes

RealtimeTrader runs:
  → WebSocket data feed → SAX encoding → Trie matching → Signal → Risk → Trade
  → Each closed trade saved to DB via storage.save_trade()
  → TerminalState updated in real-time
  → WebSocket pushes to dashboard every 1s

Dashboard displays:
  → Chart with entry/exit markers
  → Open positions + Trade history
  → P&L, Money Management details
  → Validation results + MC verdict
  → Multi-token node status
```

### 4 Token Styles (Weight Profiles)

| Profile | Assets | α | W | Cat Loss | Max Pos | Short |
|---------|--------|---|---|----------|---------|-------|
| blue_chip | BTC, ETH | 3 | 10 | 8% | 10% | YES |
| default | Large/Mid/DeFi | 4 | 7 | 10% | 7% | YES |
| meme | DOGE, SHIB, PEPE | 5 | 5 | 15% | 3% | NO |
| new_launch | New tokens | 3 | 5 | 20% | 1% | NO |

### Pre-Trade Validation Criteria

| Check | Pass Threshold |
|-------|---------------|
| Backtest Win Rate | > 40% |
| Backtest Profit Factor | > 0.8 |
| Monte Carlo Risk of Ruin | < 20% |
| Monte Carlo Verdict | != HIGH RISK |
| Min Trades in Backtest | >= 5 |

---

## VERSION HISTORY

| Version | Date | Changes |
|---------|------|---------|
| v0.30.0 | 2026-06-16 | Fix auto-build bug, backtest markers, equity curve, Living Trie 4-level persistence |
| v0.31.0 | 2026-06-16 | Trade history DB, /api/validate, /api/auto-setup, pre-trade gate, professional UI redesign, multi-token |
