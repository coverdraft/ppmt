---
Task ID: 1
Agent: Main Agent
Task: Comprehensive audit and fix of CryptoQuant Terminal - reconnect all decoupled panels

Work Log:
- Cloned repo from GitHub, installed dependencies, generated Prisma client
- Discovered database was empty (0 tokens) — root cause of all broken panels
- Seeded DB with 214 tokens from DexScreener + CoinGecko across 8 chains
- Created 214 TokenDNA records, 81 Signals, 8 Trading Systems
- Fixed Multi-Chain API: added DexScreener fallback enrichment when DB has <5 tokens per chain
- Fixed Multi-Chain Dashboard: added Load Data button, Refresh button, sparse data warnings
- Fixed Token Flow: added seed button when DB is empty
- Fixed Market Summary API: added real Fear & Greed Index from alternative.me API
- Fixed Signal Center: filter types now match DB signal types (SMART_MONEY, PATTERN)
- Fixed Brain Control: converted from manual fetch to useQuery/useMutation, fixed force sync
- Fixed Alpha Ranking: removed broken store imports, added local interface definitions
- Fixed Risk Pre-Filter API: added filterDetails to POST response
- Fixed Risk Pre-Filter Panel: converted to useMutation with proper state handling
- Fixed Market Regime Panel: converted from manual fetch to useQuery with auto-refresh
- Fixed Capital Allocation Dashboard: converted from manual fetch to useQuery with auto-refresh
- Fixed Strategy Lab: status mutation mapping, added Clone and Backtest buttons
- Fixed Paper Trading: added Open Position form with token fields
- Fixed Portfolio Intelligence API: fixed missing correlationWithExisting and varDelta fields
- Fixed Health API: added seedNeeded field for frontend prompt
- Verified all APIs return data: health, market/summary, market/multi-chain, trading-systems, signals, etc.
- Build compiles without errors
- Pushed all changes to GitHub (commit 402bd18)

Stage Summary:
- All 14+ panels now properly connected to their APIs
- Multi-Chain Dashboard works with 8 chains, cross-chain tokens, chain ranking
- Buttons (Load Data, Refresh, Start/Stop Brain, Open Position, Clone System, Run Backtest) all functional
- DB has 214 tokens, 214 DNA, 81 signals, 8 trading systems
- Key remaining issue: no OHLCV candle data yet (affects backtesting)
