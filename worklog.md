# PPMT Worklog

---
Task ID: 13
Agent: Main Agent
Task: TAREA 13 — Nuclear cleanup and rebuild with corrected compute_outcome_won

Work Log:
- PASO 0: Discovered critical storage design flaw — tries table had PK (symbol, level) without timeframe column. Each timeframe build overwrote previous ones. Added timeframe column to tries table, updated save_trie/load_trie/load_all_tries APIs with backward-compatible timeframe="" parameter. Updated ppmt.py to propagate self.timeframe to all storage calls.
- PASO 1: Dropped old tries table and recreated with new schema (symbol, timeframe, level) PK. All contaminated data removed.
- PASO 2: Rebuilt 10 tokens × 3 timeframes (1m, 5m, 15m). Total: 75 tries in DB. Build script verification updated for per-timeframe checks.
- PASO 3: Verified win_rates across all timeframes. 1m shows highest WRs (47.3% N1, 46.0% N3 DOGE). 5m/15m show lower but consistent WRs.
- PASO 4: OOS validation completed. DOGE 1m: WC=0.4054 (≥0.40 minimum met). SOL 5m: WC=0.3883 (above 0.30 filter).
- Git commit + push completed. TRAZABILIDAD updated to v0.53.0.

Stage Summary:
- Storage now supports per-timeframe tries (v0.53.0)
- 75 tries rebuilt with corrected compute_outcome_won
- DOGE 1m weighted_confidence: 0.4054 (was stuck at ~0.30 before)
- SOL 5m weighted_confidence: 0.3883
- Commit: 2ebdd7c pushed to origin/main
