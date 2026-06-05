# Task: Executive Dashboard & Multi-Level Dashboard System

## Summary

Created a multi-level dashboard system for the CryptoQuant Terminal with Executive, Professional, and Engineer views.

## Files Created

### 1. `/home/z/cryptoquant-terminal/src/components/dashboard/dashboard-level-provider.tsx`
- Context provider managing `DashboardLevel` state ('executive' | 'professional' | 'engineer')
- Persists to localStorage via key `cryptoquant-dashboard-level`
- Provides `useDashboardLevel()` hook
- Keyboard shortcuts: Ctrl+1 (executive), Ctrl+2 (professional), Ctrl+3 (engineer)
- Handles SSR hydration safely (defaults to 'engineer' on server, syncs from localStorage on mount)

### 2. `/home/z/cryptoquant-terminal/src/components/dashboard/executive-dashboard.tsx`
- Full Executive Dashboard component with 7 panels:
  1. **Portfolio Health Score** - Circular gauge (0-100), computed from Sharpe (25%), Max DD (25%), Win Rate (20%), Risk Budget (15%), Diversification (15%). Color-coded: green/yellow/orange/red. Shows trend arrow and component breakdown bars.
  2. **Market Regime Badge** - Shows current regime (TRENDING_BULL/BEAR, RANGING, ACCUMULATION, DISTRIBUTION, PANIC) with confidence % and timestamp. Computed from Fear & Greed index.
  3. **Risk Status Panel** - Traffic light system for Kill Switch, Portfolio DD, VaR Utilization, and Concentration Risk (LOW/MEDIUM/HIGH).
  4. **Capital at Risk** - Large display of total capital, at-risk % and USD, available capital, today's PnL, active strategies, open positions.
  5. **Top 3 Opportunities** - From signals API, sorted by confidence. Shows token symbol, direction, alpha score. "View Details" link to Professional dashboard.
  6. **Active Alerts** - Collapsible panel with severity counts (CRITICAL/WARNING/INFO), latest 3 alerts with timestamps, "View All" link.
  7. **Equity Curve** - Recharts line chart with portfolio vs benchmark line, event dots for key events (kill switches, large trades, SDE decisions).

- Data fetched via react-query from existing API endpoints:
  - GET /api/portfolio/stats
  - GET /api/risk/overview?includeHistory=true
  - GET /api/capital-allocation/dashboard
  - GET /api/alerts?limit=10
  - GET /api/signals?minConfidence=60&limit=10
  - GET /api/portfolio/equity-curve
  - Market summary from Zustand store

- Layout: CSS Grid, responsive (3-column on lg, single column on mobile)
- Dark theme matching existing design (#0d1117, #1e293b, emerald/gold/amber accents)

## Files Modified

### 3. `/home/z/cryptoquant-terminal/src/app/page.tsx`
- Added imports: `DashboardLevelProvider`, `useDashboardLevel`, `DashboardLevel`, `LayoutDashboard`, `Briefcase`, `Code2`
- Added dynamic import for `ExecutiveDashboard`
- Replaced static `NAV_GROUPS` constant with `getFilteredNavGroups(level)` function that filters tabs per level
- Professional level hides: brain, dna-scanner, predictive, patterns, export-import
- Engineer level shows all tabs
- Executive level returns empty nav groups (sidebar hidden)
- Added `DashboardLevelSelector` component (3 pill buttons in TopBar matching chain filter design)
- Updated `Sidebar` to use `getFilteredNavGroups(level)` and hide for executive level
- Updated `DashboardContent` to conditionally render `ExecutiveDashboard` when level is 'executive'
- Updated `HomePage` to wrap with `DashboardLevelProvider`
- Added `ALL_NAV_ITEMS_FLAT` for keyboard shortcut mapping (replacing `ALL_NAV_ITEMS` which depended on removed `NAV_GROUPS` constant)

## Verification
- `bun run lint` passes (0 new errors; pre-existing errors in notification-center.tsx, risk-management-panel.tsx, user-heatmap.tsx, data-ingestion.ts are unrelated)
- `npx next build` compiles successfully
- All changes are backward-compatible; engineer view (default) looks identical to the original single-level dashboard
