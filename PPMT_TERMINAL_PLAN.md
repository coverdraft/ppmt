# PPMT Terminal Strategy Lifecycle Engine - Master Plan

> **MAXIMUM PRIORITY**: This is THE ONLY plan. No other plans or side-quests.
> If you get lost, come back to this document. Everything we do must trace back here.

**Version**: v0.20.0
**Date**: 2026-06-16
**Status**: ACTIVE - IN PROGRESS

---

## Vision

PPMT Terminal runs **standalone** — no Next.js frontend, no Python↔Node bridge, no Portfolio API sidecar.
The Python CLI is the single source of truth. Everything works from the terminal.

```
ppmt scan → ppmt ingest → ppmt build → ppmt run → ppmt backtest → ppmt monte-carlo
```

---

## Architecture Decision

**CHOSEN**: Standalone Python terminal (NO Next.js bridge)
**REJECTED**: Next.js Brain API + FastAPI Portfolio API bridge (too complex, integration nightmare)

**Rationale**:
- PPMT already finds signals easily — the engine works
- 1m/5m timeframes produce enough trades for validation
- ccxt provides both data AND execution for ANY exchange (MEXC, Bybit, Binance, etc.)
- Zero bridge complexity = zero integration bugs
- Monte Carlo validates robustness before real money
- Living Trie adapts in real-time without full rebuilds

---

## Implementation Phases (IN ORDER)

### Phase 1: Fix ppmt scan + Add 1m/5m Support ✅ DONE
**Files**: `src/ppmt/data/collector.py`, `src/ppmt/cli/main.py`, `pyproject.toml`

- [x] Implement `DataCollector.get_markets()` using ccxt
- [x] Implement `DataCollector.get_tickers()` using ccxt
- [x] Make ccxt a REQUIRED dependency (not optional)
- [x] Make fastapi/uvicorn REQUIRED dependencies (not optional)
- [x] Bump version to 0.20.0

### Phase 2: MEXC Exchange Integration via ccxt ✅ DONE
**Files**: `src/ppmt/data/websocket_feed.py`, `src/ppmt/cli/main.py`

- [x] Add MEXC WebSocket support to WebSocketFeed (`_listen_mexc`)
- [x] Add MEXC WS constants and kline parser
- [x] Update `ppmt run` CLI to accept `mexc` as exchange
- [x] Update ExchangeWS enum with MEXC

### Phase 3: Real Order Execution + Emergency Controls ✅ DONE
**Files**: `src/ppmt/engine/realtime.py`, `src/ppmt/cli/main.py`

- [x] Verify existing ccxt order execution code path in run_live()
- [x] Add leverage control via ccxt `set_leverage()`
- [x] Add emergency close position on shutdown (tries exchange close)
- [x] Add `--live` / `--mainnet` flags for real orders (already existed)

### Phase 4: Monte Carlo Integration ✅ DONE
**Files**: `src/ppmt/cli/main.py`

- [x] Fix `ppmt monte-carlo` CLI command to use actual `MonteCarloSimulator.simulate()` API
- [x] Display risk assessment: Risk of Ruin, P95 Drawdown, Profit Probability
- [x] Display equity/drawdown confidence intervals
- [x] Gate live trading: VERDICT display (LOW/MODERATE/HIGH risk)

### Phase 5: Living Trie Updates + Persistence in Live Mode ✅ DONE
**Files**: `src/ppmt/engine/realtime.py`

- [x] Verify `_living_trie_update()` fires correctly in live mode (already working)
- [x] Add periodic Trie persistence (save to SQLite every N candles)
- [x] Add Trie persistence on shutdown
- [x] Add `trie_persist_interval` to LiveConfig

### Phase 6: Money Management + Portfolio Controls ✅ DONE
**Files**: `src/ppmt/engine/realtime.py`, `src/ppmt/cli/main.py`, `src/ppmt/risk/money_manager.py`, `src/ppmt/risk/position_sizing.py`

- [x] Integrate `MoneyManager` into `RealtimeTrader.run_live()` (was only using basic RiskManager)
- [x] Add leverage control (--leverage flag)
- [x] Add auto/manual mode (--auto/--manual)
- [x] Add max positions, max exposure, kill switch, daily loss limit to LiveConfig
- [x] Integrate `AdvancedPositionSizer` (Kelly Criterion)
- [x] Add MoneyManager state persistence on shutdown
- [x] Add MoneyManager portfolio summary on exit
- [x] Add all money management CLI flags (--max-positions, --max-exposure, --kill-switch, etc.)
- [x] TerminalState updates with portfolio data during live mode
- [x] Periodic MoneyManager auto-save

### Phase 7: TerminalState Population + Dashboard ⬜ TODO
**Files**: `src/ppmt/terminal/state.py`, `src/ppmt/terminal/server.py`

- [ ] Add Monte Carlo results to TerminalState
- [ ] Add Living Trie stats to TerminalState
- [ ] Enhance terminal HTML dashboard with portfolio controls
- [ ] WebSocket broadcast of all state changes

### Phase 8: Bug Fixes + Push to GitHub ⬜ TODO
**Files**: Various

- [ ] Fix ATR inconsistency between ppmt.py build() and paper_trader.py
- [ ] End-to-end test: full lifecycle
- [ ] Push everything to GitHub

---

## Money Management Architecture (v0.20.0)

```
Signal → RiskManager (per-trade quality check)
         ↓
      MoneyManager (portfolio-level governance)
         ├─ Exposure cap (max 80% of portfolio)
         ├─ Single position cap (max 25% of portfolio)
         ├─ Correlation limit (max 2 same asset class)
         ├─ Circuit breakers (daily loss, max drawdown)
         ├─ Kill switch (auto-close all at 95% exposure)
         └─ Kelly Criterion sizing (Quarter-Kelly default)
              ↓
           Exchange execution (with leverage if set)
```

### Key Controls Available

| Control | CLI Flag | Default | Description |
|---------|----------|---------|-------------|
| Leverage | `--leverage` / `-l` | 1 (spot) | Exchange leverage (1-125x) |
| Mode | `--auto` / `--manual` | auto | Auto-execute or display-only |
| Max Positions | `--max-positions` | 5 | Max simultaneous positions |
| Max Exposure | `--max-exposure` | 0.80 | Portfolio exposure limit |
| Kill Switch | `--kill-switch` | 0.95 | Auto-close all at this exposure |
| Daily Loss | `--daily-loss` | 0.05 | Stop trading at 5% daily loss |
| Kelly Sizing | `--kelly` / `--no-kelly` | ON | Kelly Criterion position sizing |

### Example Commands

```bash
# Conservative: spot, auto, Kelly sizing, 3 positions max
ppmt run -s BTC/USDT -t 5m -e mexc --max-positions 3 --max-exposure 0.5

# Aggressive: 5x leverage, 8 positions, 90% exposure
ppmt run -s BTC/USDT -t 5m -e mexc -l 5 --max-positions 8 --max-exposure 0.9

# Manual mode: only display signals, don't execute
ppmt run -s BTC/USDT -t 5m -e mexc --manual

# With real money (after Monte Carlo validation)
ppmt run -s BTC/USDT -t 5m -e mexc --live --mainnet -l 2 --kill-switch 0.8
```

---

## Key Commands (Target State)

```bash
# Scan for assets
ppmt scan -e mexc -q USDT --sort-by volume

# Ingest historical data (1m or 5m for more trades)
ppmt ingest -s BTC/USDT -t 5m -e mexc --days 30

# Build Trie
ppmt build -s BTC/USDT -t 5m

# Backtest
ppmt backtest -s BTC/USDT -t 5m

# Monte Carlo validation
ppmt monte-carlo -s BTC/USDT -t 5m

# Live dry-run (paper trading) with full money management
ppmt run -s BTC/USDT -t 5m -e mexc

# Live with real orders (after MC validation passes)
ppmt run -s BTC/USDT -t 5m -e mexc --live --mainnet

# Web dashboard
ppmt terminal
```

---

## Exchange Support Matrix

| Exchange | WebSocket | REST (DataCollector) | ccxt (Data+Orders) | Leverage | Priority |
|----------|-----------|---------------------|-------------------|----------|----------|
| Binance  | ✅ Yes    | ✅ Yes              | ✅ Yes            | ✅ Yes   | Medium   |
| Bybit    | ✅ Yes    | ✅ Yes (primary)    | ✅ Yes            | ✅ Yes   | High     |
| MEXC     | ✅ Yes    | 🔲 Via ccxt         | ✅ Yes            | ✅ Yes   | **HIGHEST** |
| OKX      | 🔲 No    | ✅ Yes              | ✅ Yes            | ✅ Yes   | Low      |
| Kraken   | 🔲 No    | ✅ Yes              | ✅ Yes            | ✅ Yes   | Low      |

---

## What We Are NOT Doing

1. ❌ Next.js frontend / Brain API
2. ❌ Python↔Node.js bridge
3. ❌ Portfolio API sidecar (MoneyManager IS the portfolio)
4. ❌ Multi-service architecture
5. ❌ Complex UI — terminal + simple web dashboard is enough

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| v0.19.1 | Previous | 5 bug fixes (encode_incremental, process_new_candle, max_correlated, Sharpe 252→365) |
| v0.20.0 | 2026-06-16 | Standalone terminal. MEXC integration. MoneyManager integration. Kelly sizing. Leverage control. Living Trie persistence. Kill switch. Circuit breakers. Monte Carlo fix. ccxt required. |

---

## Quick Recovery Guide (If You Get Lost)

1. **What am I doing?** → Read this file
2. **What phase am I in?** → Check the checkboxes above
3. **What's the next step?** → First unchecked item in current phase
4. **Should I build a web UI?** → NO. Terminal only.
5. **Should I integrate Next.js?** → NO. Standalone Python.
6. **What exchange?** → MEXC (via ccxt). Others work too but MEXC is the target.
7. **What timeframe?** → 5m primary, 1m for aggressive testing.
8. **How do I test?** → `ppmt backtest -s BTC/USDT -t 5m` then `ppmt run -s BTC/USDT -t 5m -e mexc`
9. **How does money management work?** → MoneyManager wraps RiskManager. Portfolio-level caps, Kelly sizing, leverage, kill switch.
10. **Should I add more portfolio features?** → MoneyManager already has: exposure caps, correlation limits, circuit breakers, kill switch, auto-save, equity curve, analytics. Just integrate, don't rebuild.
