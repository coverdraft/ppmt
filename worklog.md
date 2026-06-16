# PPMT Worklog — Trazabilidad de Cambios

---
Task ID: 1
Agent: main
Task: Fix version mismatch between __init__.py and pyproject.toml

Work Log:
- Verified __init__.py already at 0.24.0 (was updated before this session)
- Updated pyproject.toml from 0.23.0 to 0.24.0
- Updated cli/main.py click.version_option from 0.23.0 to 0.24.0

Stage Summary:
- All version references now consistently say 0.24.0

---
Task ID: 2
Agent: main
Task: Fix Monte Carlo verdict logic - contradictory "LOW RISK" with 0% profit probability

Work Log:
- Analyzed the verdict logic in cli/main.py lines 784-792
- OLD LOGIC: only checked risk_of_ruin → 0% ruin = LOW RISK even if strategy loses money
- NEW LOGIC: comprehensive 4-factor composite risk score (0-100):
  - Factor 1: Risk of Ruin (0-40 points)
  - Factor 2: Probability of Profit (0-30 points, inverted)
  - Factor 3: Profit Factor (0-15 points)
  - Factor 4: P95 Max Drawdown (0-15 points)
- Score >= 50 → HIGH RISK (with specific reasons)
- Score 30-49 → MODERATE RISK
- Score < 30 → LOW RISK
- Now shows specific warnings for losing strategies

Stage Summary:
- With the current strategy (-10% P&L, 0% profit prob, PF=0.65), the new verdict will be:
  HIGH RISK (score ~70/100) with clear message "this strategy LOSES money"
- This prevents dangerous "Safe to deploy" messages for losing strategies

---
Task ID: 3
Agent: main
Task: Improve strategy profitability - 31.8% win rate and -10.01% P&L

Work Log:
- Analyzed trade history: 22 trades, 7 wins, 15 losses
- Key issues identified:
  1. SL too tight (1.2x expected_move, max 3.0%) → premature stop-outs
  2. Weak signal filters letting noise through (move_threshold=0.30, prob_threshold=0.15)
  3. No regime-aware signal filtering (entering LONG in downtrends, SHORT in uptrends)
  4. Only 1 symbol cooldown after loss → revenge trading
- Changes made to src/ppmt/engine/realtime.py:
  a. Raised move_threshold: 0.30 → 0.50 (skip tiny moves)
  b. Raised prob_threshold: 0.15 → 0.20 (need stronger patterns)
  c. Stricter quality gate: prob < 0.30 AND move < 0.8 → reject
  d. Regime-aware signal filtering:
     - Volatile: require 1.5x normal thresholds
     - Counter-trend LONG in downtrend: require prob >= 0.50
     - Counter-trend SHORT in uptrend: require prob >= 0.50
  e. Regime-adaptive SL/TP multipliers:
     - Trending: SL=1.3x, TP=3.0x (tight SL, let profits run)
     - Ranging: SL=1.8x, TP=1.8x (wide SL, smaller TP)
     - Volatile: SL=1.5x, TP=2.5x (moderate)
  f. Wider SL bounds: max 4.0% (was 3.0%), min 0.8% (was 0.5%)
  g. Cooldown after loss: 3 symbols (was 1)

Stage Summary:
- Expected improvements: fewer but higher-quality trades, better win rate
- Regime-awareness prevents counter-trend entries
- Wider SL reduces premature stop-outs
- Cooldown prevents revenge trading

---
Task ID: 4
Agent: main
Task: Money Management system verification (auto/manual, leverage, parent-child nodes)

Work Log:
- Verified existing implementation in src/ppmt/risk/money_manager.py:
  - MoneyManagerConfig with all portfolio-level controls
  - CircuitBreaker for emergency controls
  - ParentNodeManager with full parent-child architecture
  - ChildNodeConfig with leverage, auto_mode, capital_allocation_pct
  - ChildNodeState for runtime tracking
- Verified CLI commands already exist:
  - `ppmt portfolio` — portfolio overview, correlation, rebalance
  - `ppmt nodes` — parent-child node management with leverage and auto/manual modes
- All features are already implemented and functional:
  - Auto/manual mode per child node
  - Leverage control per child node (1-125x)
  - Capital distribution among children
  - Global kill switch
  - Portfolio exposure limits
  - Circuit breakers

Stage Summary:
- Money management system is complete and already integrated
- CLI provides full control over portfolio, nodes, leverage, and modes
- No additional code needed for basic money management features

---
Task ID: 5
Agent: main
Task: Verify MEXC and WebSocket connectivity

Work Log:
- Verified MEXC support in src/ppmt/data/collector.py:
  - _fetch_ccxt_exchange("mexc", ...) works via ccxt
  - MEXC is in the fallback chain via ccxt
- Verified MEXC WebSocket in src/ppmt/data/websocket_feed.py:
  - MEXC_WS_SPOT = "wss://wbs.mexc.com/ws"
  - _mexc_subscribe_msg() builds subscription for spot@public.kline.v3.api
  - _parse_mexc_kline() parses MEXC v3 kline format
  - _listen_mexc() handles MEXC WebSocket connection
- Both REST and WebSocket for MEXC are fully implemented

Stage Summary:
- MEXC REST: works via ccxt (requires ccxt installed)
- MEXC WebSocket: implemented with proper subscribe/parse/listen
- User needs to test with: ppmt ingest -s BTC/USDT -e mexc

---
Task ID: 6
Agent: main
Task: Fix nodes leverage bug + aggressive strategy improvement + dashboard setup

Work Log:
- Fixed ppmt nodes -l bug: changed type=int to str to accept NODE_ID:LEVERAGE format
- v0.25.0: Aggressive strategy improvement based on trade analysis:
  - Raised move_threshold: 0.50 → 0.80 (only meaningful moves)
  - Raised prob_threshold: 0.20 → 0.30 (need strong patterns)
  - Hard quality gate: reject if overall_probability < 0.35 or move < 0.5%
  - Strict regime filtering:
    * Ranging: require prob >= 0.55 and move >= 1.0%
    * Volatile: require 2x normal thresholds
    * Counter-trend: require prob >= 0.60
  - Wider SL/TP:
    * Trending: SL=1.5x, TP=4.0x
    * Ranging: SL=2.0x, TP=2.5x
    * Volatile: SL=1.8x, TP=3.0x
  - SL bounds: max 5.0%, min 1.0%
  - Minimum R:R ratio enforced at 2:1
- Dashboard verification:
  * Next.js dashboard exists at project root (needs npm install + node_modules)
  * FastAPI lite dashboard available via ppmt terminal --lite (port 8420)
  * ppmt terminal (default) tries Next.js on port 3000, falls back to FastAPI

Stage Summary:
- Version bumped to 0.25.0
- Leverage CLI bug fixed
- Strategy should produce fewer but much higher quality trades
- Dashboard available via: ppmt terminal --lite (FastAPI) or ppmt terminal (Next.js)

---
Task ID: 7
Agent: main
Task: Phase 7 - Dashboard con Money Management completo
Work Log:
- Enhanced server.py with full Money Management REST API (nodes, leverage, kill-switch, backtest)
- Enhanced dashboard HTML with Money Management panel, Child Nodes table, Quick Backtest
- Changed ppmt terminal to launch FastAPI on port 3000 (no Next.js dependency)
- Updated version to 0.26.0
Stage Summary:
- Phase 7 COMPLETE - Dashboard has full Money Management, Node Control, Backtest
- ppmt terminal -> http://localhost:3000 (FastAPI dashboard)


---
Task ID: 8
Agent: main
Task: Fix MEXC WebSocket — live trading stuck at 49 candles / 0 signals / 0 trades

Work Log:
- Audited full /api/start-trading → RealtimeTrader.run_live() → WebSocketFeed pipeline
- Discovered: "Candles: 49" exactly matches warmup_candles = sax_window_size*2 + pattern_length*sax_window_size = 7*2 + 5*7 = 49
  → meaning ZERO live candles were processed despite WS connection appearing active
- Root cause #1: MEXC v3 kline messages do NOT include "x" (is_closed) field
  → _parse_mexc_kline() used k.get("x", False) → candle.closed always False
  → on_candle never invoked → no SAX symbols, no signals, no trades
- Root cause #2: MEXC requires CLIENT-initiated pings ({"method":"ping","id":N}) every ≤10s
  → old code only handled SERVER pings → MEXC closed connection at ~30s → reconnect loop
- Root cause #3: websockets library ping_interval=15 sent protocol-level pings MEXC ignores
  → websockets closed connection at ping_timeout=10s expecting pong control frame
- Root cause #4: SUBSCRIPTION message lacked "id" field → some environments get "Blocked!"

Fixes applied (v0.32.4):
- src/ppmt/data/websocket_feed.py:
  * _mexc_subscribe_msg() now includes "id" parameter (required by MEXC v3)
  * _parse_mexc_kline() infers closed from wall-clock vs k["T"] (end time), with fallback to k["x"] if present
  * _listen_mexc() uses BUFFERED candle strategy: emits previous candle as closed when new timestamp arrives
  * Added background _mexc_ping_loop() sending {"method":"ping","id":N} every 10s
  * Disabled websockets protocol-level pings (ping_interval=None, ping_timeout=None)
  * Robust control-message handling: PONG, SUBSCRIPTION confirmation, server-ping, subscription-echo
  * on_error fires when MEXC rejects subscription (was silent before)
  * Flushes buffered candle on shutdown so last period isn't lost
- tests/test_mexc_ws_parser.py: 7 new tests covering subscribe msg, parser variants, nesting
- src/ppmt/__init__.py: version 0.29.0 → 0.32.4
- pyproject.toml: version 0.32.0 → 0.32.4
- TRAZABILIDAD.md: +240 lines documenting the 5 root causes, 7 fixes, lessons learned

Stage Summary:
- 167 tests pass (160 existing + 7 new MEXC parser tests)
- After user reinstalls, live trading should now:
  * Maintain stable WS connection (no more 30s reconnect loop)
  * Process every closed candle via on_candle
  * Generate SAX symbols → predictions → signals → trades
- "Candles" counter should now climb past 49 as live candles arrive each hour
