# Task: Validation Suite - API Route + Dashboard Panel

## Summary
Created two production-quality files for the PPMT Validation Suite feature:

### 1. API Route: `/api/validation/run/route.ts`
- Accepts POST with validation config (symbol, trainRatio, mcSimulations, wfWindows, etc.)
- Executes Python PPMT validation engine via `child_process.execFile`
- Runs `ValidationEngine.run_full_validation()` which performs P0 (OOS), P1 (MC), P2 (WF)
- Returns JSON verdict with composite scoring (0-100) and recommendation (ROBUST/MARGINAL/OVERFIT/INSUFFICIENT_DATA)
- 120-second timeout for long-running validations
- Input sanitization and range validation
- Robust error handling (timeout, parse errors, Python engine errors)

### 2. Dashboard Panel: `validation-suite-panel.tsx`
- Full validation suite UI with gold/amber theme
- Collapsible config panel (symbol, train ratio, MC sims, WF windows, pattern length, etc.)
- One-click "Run Full Validation" button
- Verdict banner with recommendation, confidence score, and P0/P1/P2 score breakdown
- Three result tabs: P0 (OOS), P1 (MC), P2 (WF)
- P0: IS vs OOS comparison table, degradation metrics, equity curve SVG
- P1: Key metric cards (Profit Prob, Risk of Ruin, P95 DD, Median Equity), confidence intervals table, DD bar chart
- P2: Aggregate WFE with progress bar, consistency, degradation bar, WFE trend chart, per-window table
- Loading, error, and empty states with framer-motion animations
- Uses shadcn/ui components, sonner toasts, @tanstack/react-query

### 3. Bug Fix: `ppmt/ppmt/src/ppmt/risk/manager.py`
- Added missing `RiskConfig` and `Position` dataclasses that were imported but not defined
- This was blocking the Python `ValidationEngine` import chain

### 4. Integration into main page
- Added 'validation' tab to TabId type
- Added ValidationSuitePanel import
- Added tab button and conditional rendering

## Files Modified
- `/home/z/my-project/ppmt/ppmt/src/ppmt/risk/manager.py` - Added RiskConfig + Position dataclasses
- `/home/z/my-project/ppmt/src/app/page.tsx` - Added validation tab + import

## Files Created
- `/home/z/my-project/ppmt/src/app/api/validation/run/route.ts` - API route
- `/home/z/my-project/ppmt/src/components/dashboard/validation-suite-panel.tsx` - UI component
