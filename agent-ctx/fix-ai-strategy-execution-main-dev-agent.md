# Task: Fix AI Strategy Optimizer to Execute Trades After Activation

## Task ID: fix-ai-strategy-execution
## Agent: Main Dev Agent

## Summary of Changes

### Problem
The AI Strategy Optimizer's "Activate" button created a TradingSystem and activated it in paper mode, but NEVER actually triggered trade execution. The autonomous execution engine and auto-evolution loop existed but were not connected to the UI activation flow.

### Changes Made

#### 1. Created `/api/execution/start/route.ts` (NEW)
- Accepts POST with `{ systemId, tokenAddress?, direction?, positionSizeUsd? }`
- Validates the trading system exists and is active
- Auto-selects the best token from backtest operations if none provided
- Uses `strategyEvolutionEngine.executeEntry()` for reliable paper trade execution
- Creates a tracking record in BacktestOperation with `backtestId = "paper_trading_autonomous"`
- Records state transition via strategyStateManager
- Includes `ensureAutonomousBacktestRun()` helper to create the singleton autonomous backtest tracking record

#### 2. Verified `/api/execution/auto-trade/route.ts` (EXISTS - working)
- Already uses `strategyEvolutionEngine.executeEntry()` properly
- Validates required fields (systemId, tokenAddress, direction, positionSizeUsd)
- Resolves token symbol and price from DB
- Records state transition to PAPER_TRADING
- Returns proper response with tradeId, status, and execution details

#### 3. Verified `/api/auto-evolution/route.ts` (EXISTS - working)
- POST: Start/stop the auto-evolution loop with configurable parameters
- GET: Returns current status including isRunning, cycleCount, activeStrategies, etc.
- Uses the `autoEvolutionLoop` singleton properly

#### 4. Updated AI Strategy Optimizer (`src/components/dashboard/ai-strategy-optimizer.tsx`)

**A. `activateMutation` - Now triggers trade execution after activation:**
- Step 1: Create trading system (unchanged)
- Step 2: Activate in paper mode (unchanged)
- Step 3 (NEW): Calls `/api/execution/start` to execute a paper trade
- Updated success handler to show trade execution details
- Adds to executionStatuses on success

**B. `activateAllMutation` - Now triggers trade execution for each activated strategy:**
- Added Step 3 to each activation: calls `/api/execution/start`
- Execution failure doesn't fail the activation
- Updated success message to "Activated & executed"
- Invalidates open-positions query on success

**C. New `startTradingMutation` - "Start Trading" button for Hall of Fame:**
- Checks for existing active trading system
- Creates and activates if needed
- Calls `/api/execution/start` to execute the paper trade
- Shows success/error in execution status log
- Proper loading states with Crosshair icon

**D. New `autoEvolutionControlMutation` - "Start Auto-Evolution" button:**
- Sends start/stop to `/api/auto-evolution`
- Configurable: 5-minute intervals, 0.5 min Sharpe, 0.4 min win rate
- Updates `autoEvolveRunning` state
- Shows execution status messages
- Invalidates auto-evolution-status query

**E. New `autoEvolutionStatus` query:**
- Polls `/api/auto-evolution` every 30 seconds
- Syncs `autoEvolveRunning` with server status
- Shows cycle count and trade count in badge

**F. Updated Hall of Fame UI:**
- "Activate & Trade Top N" button (was "Activate Top N")
- "Start Auto-Evolution" / "Stop Auto-Evo" toggle button
- Status badge showing cycle count and trades
- Per-strategy "Start Paper Trading" button (Crosshair icon) alongside Activate
- Per-strategy Activate button tooltip updated to "Activate & Execute Trade (Paper)"

**G. Updated `handleExecuteTopStrategies`:**
- Now uses `/api/execution/start` first (auto-selects best token)
- Falls back to `/api/execution/auto-trade` if execution/start fails
- Shows execution status in the log

**H. Updated Open Positions Query:**
- Tries `/api/execution/positions` first (more reliable)
- Falls back to `/api/strategy-optimizer/evolve?type=open_positions`

### Execution Flow (End-to-End)
1. **AI Strategy → Activate**: Creates TradingSystem, activates in paper mode
2. **Activate → Execute Trade**: Calls `/api/execution/start` which uses `strategyEvolutionEngine.executeEntry()`
3. **Execute Trade → Track Position**: Creates BacktestRun + BacktestOperation records
4. **Track Position → Auto-Exit**: Auto-evolution loop monitors positions and applies SL/TP/trailing stop
5. **Auto-Evolution**: Continuous loop that evolves strategies, auto-activates, auto-executes entries, and monitors exits

All trades are in PAPER mode (no real money).
