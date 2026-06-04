# Task 6 — Refactor Auto-Evolution Loop to Discrete Cycles

## Summary
Refactored the auto-evolution loop from a continuous, fragile `setInterval` loop to a discrete, cycle-based approach where each cycle is independent and persisted to the database. If Cycle 3 fails, Cycles 1 and 2 results are still in the DB.

## Files Changed
- `prisma/schema.prisma` — Added `EvolutionCycle` model for cycle persistence
- `src/lib/services/auto-evolution-loop.ts` — Complete refactor to cycle-based architecture
- `src/app/api/auto-evolution/route.ts` — Added `status` action, `totalCycles` support

## Key Design Decisions
1. Each cycle has 6 discrete phases: SCAN → GENERATE → BACKTEST → EVALUATE → SAVE → EVOLVE
2. Cycle state is persisted to `EvolutionCycle` DB model after each phase
3. On restart, `tryResume()` checks for interrupted runs and resumes from last completed cycle
4. `stop()` is graceful — waits for current cycle to complete before stopping
5. `runId` groups cycles from the same start command for easy querying

## TypeScript Verification
- Zero errors in modified files after `npx tsc --noEmit`
