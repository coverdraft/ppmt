#!/usr/bin/env python3
"""
PPMT Terminal — Audit fixes (batch 2).

Applies all bugs found in the wiring audit:

  Fix 1 — Position.current_sl/current_tp/catastrophic_sl made nullable in store.
          Guard arithmetic in PositionPanel and OperationsChart.
  Fix 2 — Replace hardcoded 1000 (old demo capital) with INITIAL_CAPITAL.
          Affects: portfolio-manager.tsx, performance-panel.tsx, operations-chart.tsx.
  Fix 3 — Align store defaults with engine: BTC/USDT, autoMode=false, all 50 tokens active.
  Fix 4 — manual-trade-panel.tsx: sync local `symbol` when store `selectedToken` changes.
  Fix 5 — page.tsx footer: "25 Tokens" -> "50 Tokens".
  Fix 6 — page.tsx: replace dead timeframe <Select> with static LIVE badge.
  Fix 7 — trading-store.ts setConnected: fallback 'demo' -> 'paper'.

Run: python3 /home/z/my-project/scripts/apply_audit_fixes.py
"""

from pathlib import Path

ROOT = Path("/tmp/my-project")
LIB = ROOT / "src/lib"
COMP = ROOT / "src/components/trading"
STORE = ROOT / "src/stores"
APP = ROOT / "src/app"


def edit(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text()
    if old not in text:
        print(f"  [SKIP] {label}: pattern not found in {path.name}")
        return False
    if text.count(old) > 1:
        text = text.replace(old, new)
    else:
        text = text.replace(old, new, 1)
    path.write_text(text)
    print(f"  [OK]   {label}")
    return True


# ════════════════════════════════════════════════════════════════════
# Fix 1 — Position type nullable + component guards
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 1: Position nullable SL/TP + guards ===")

TS = STORE / "trading-store.ts"

# 1a. Update Position interface: current_sl, current_tp, catastrophic_sl → nullable
edit(
    TS,
    "  size_usdt: number\n"
    "  current_sl: number\n"
    "  current_tp: number\n"
    "  catastrophic_sl: number\n"
    "  pnl_pct: number\n"
    "  pnl_usdt: number\n"
    "  expected_sequence?: string[][]\n"
    "  sequence_index?: number\n"
    "}",
    "  size_usdt: number\n"
    "  current_sl: number | null   // null for manual entries (no auto SL set)\n"
    "  current_tp: number | null   // null for manual entries\n"
    "  catastrophic_sl: number | null\n"
    "  pnl_pct: number\n"
    "  pnl_usdt: number\n"
    "  expected_sequence?: string[][]\n"
    "  sequence_index?: number\n"
    "}",
    "1a. Position.current_sl/tp/catastrophic_sl nullable",
)

# 1b. PositionPanel — guard distance-to-TP/SL arithmetic
PP = COMP / "position-panel.tsx"

old_pp_dist = (
    "            // Distance to TP/SL\n"
    "            const distToTP = isLong\n"
    "              ? ((pos.current_tp - currentPrice) / currentPrice) * 100\n"
    "              : ((currentPrice - pos.current_tp) / currentPrice) * 100\n"
    "            const distToSL = isLong\n"
    "              ? ((currentPrice - pos.current_sl) / currentPrice) * 100\n"
    "              : ((pos.current_sl - currentPrice) / currentPrice) * 100"
)
new_pp_dist = (
    "            // Distance to TP/SL — null-safe (manual entries have no SL/TP)\n"
    "            const distToTP = pos.current_tp !== null && currentPrice > 0\n"
    "              ? (isLong\n"
    "                  ? ((pos.current_tp - currentPrice) / currentPrice) * 100\n"
    "                  : ((currentPrice - pos.current_tp) / currentPrice) * 100)\n"
    "              : null\n"
    "            const distToSL = pos.current_sl !== null && currentPrice > 0\n"
    "              ? (isLong\n"
    "                  ? ((currentPrice - pos.current_sl) / currentPrice) * 100\n"
    "                  : ((pos.current_sl - currentPrice) / currentPrice) * 100)\n"
    "              : null"
)
edit(PP, old_pp_dist, new_pp_dist, "1b. PositionPanel distToTP/SL null-safe")

# 1c. PositionPanel — render distToTP/distToSL with null guard
old_pp_render = (
    "                    <div>\n"
    "                      <span className=\"text-emerald-500\">TP {distToTP.toFixed(2)}%</span>\n"
    "                      <div className=\"text-emerald-400\">{pos.current_tp?.toFixed(4)}</div>\n"
    "                    </div>\n"
    "                    <div>\n"
    "                      <span className=\"text-red-500\">SL {distToSL.toFixed(2)}%</span>\n"
    "                      <div className=\"text-red-400\">{pos.current_sl?.toFixed(4)}</div>\n"
    "                    </div>"
)
new_pp_render = (
    "                    <div>\n"
    "                      <span className=\"text-emerald-500\">TP {distToTP !== null ? distToTP.toFixed(2) + '%' : '—'}</span>\n"
    "                      <div className=\"text-emerald-400\">{pos.current_tp !== null ? pos.current_tp.toFixed(4) : 'manual'}</div>\n"
    "                    </div>\n"
    "                    <div>\n"
    "                      <span className=\"text-red-500\">SL {distToSL !== null ? distToSL.toFixed(2) + '%' : '—'}</span>\n"
    "                      <div className=\"text-red-400\">{pos.current_sl !== null ? pos.current_sl.toFixed(4) : 'manual'}</div>\n"
    "                    </div>"
)
edit(PP, old_pp_render, new_pp_render, "1c. PositionPanel render TP/SL null-safe")

# 1d. PositionPanel — Cat SL render already uses ?. but let's be explicit
old_pp_cat = (
    "                  Cat SL: {pos.catastrophic_sl?.toFixed(4)}"
)
new_pp_cat = (
    "                  Cat SL: {pos.catastrophic_sl !== null ? pos.catastrophic_sl.toFixed(4) : '—'}"
)
edit(PP, old_pp_cat, new_pp_cat, "1d. PositionPanel cat SL null-safe")

# 1e. OperationsChart — skip ReferenceLine if current_tp/current_sl is null
OC = COMP / "operations-chart.tsx"

old_oc_reflines = (
    "                {/* Position TP/SL lines if active */}\n"
    "                {positions && positions.length > 0 && positions.map((pos, idx) => {\n"
    "                  const tpColor = '#10b981'\n"
    "                  const slColor = '#ef4444'\n"
    "                  return [\n"
    "                    <ReferenceLine key={`tp-${idx}`} y={pos.current_tp} stroke={tpColor} strokeDasharray=\"4 4\" strokeOpacity={0.6} />,\n"
    "                    <ReferenceLine key={`sl-${idx}`} y={pos.current_sl} stroke={slColor} strokeDasharray=\"4 4\" strokeOpacity={0.6} />,\n"
    "                  ]\n"
    "                })}"
)
new_oc_reflines = (
    "                {/* Position TP/SL lines if active (skip if null — manual entries) */}\n"
    "                {positions && positions.length > 0 && positions.map((pos, idx) => {\n"
    "                  const tpColor = '#10b981'\n"
    "                  const slColor = '#ef4444'\n"
    "                  const lines: any[] = []\n"
    "                  if (pos.current_tp !== null && pos.current_tp !== undefined) {\n"
    "                    lines.push(<ReferenceLine key={`tp-${idx}`} y={pos.current_tp} stroke={tpColor} strokeDasharray=\"4 4\" strokeOpacity={0.6} />)\n"
    "                  }\n"
    "                  if (pos.current_sl !== null && pos.current_sl !== undefined) {\n"
    "                    lines.push(<ReferenceLine key={`sl-${idx}`} y={pos.current_sl} stroke={slColor} strokeDasharray=\"4 4\" strokeOpacity={0.6} />)\n"
    "                  }\n"
    "                  return lines\n"
    "                })}"
)
edit(OC, old_oc_reflines, new_oc_reflines, "1e. OperationsChart TP/SL ReferenceLines null-safe")

# 1f. OperationsChart — Position Lifecycle section: guard dist arithmetic
old_oc_lifecycle = (
    "            {positions.map((pos, idx) => {\n"
    "              const isLong = pos.direction === 'LONG'\n"
    "              const isPositive = pos.pnl_pct >= 0\n"
    "              const tpDist = isLong\n"
    "                ? ((pos.current_tp - currentPrice) / currentPrice) * 100\n"
    "                : ((currentPrice - pos.current_tp) / currentPrice) * 100\n"
    "              const slDist = isLong\n"
    "                ? ((currentPrice - pos.current_sl) / currentPrice) * 100\n"
    "                : ((pos.current_sl - currentPrice) / currentPrice) * 100\n"
    "\n"
    "              // Progress bar: how far between SL and TP\n"
    "              const totalRange = Math.abs(tpDist) + Math.abs(slDist)\n"
    "              const progressPct = totalRange > 0 ? (Math.abs(slDist) / totalRange) * 100 : 50"
)
new_oc_lifecycle = (
    "            {positions.map((pos, idx) => {\n"
    "              const isLong = pos.direction === 'LONG'\n"
    "              const isPositive = pos.pnl_pct >= 0\n"
    "              // Null-safe: manual entries have no SL/TP\n"
    "              const tpDist = pos.current_tp !== null && currentPrice > 0\n"
    "                ? (isLong\n"
    "                    ? ((pos.current_tp - currentPrice) / currentPrice) * 100\n"
    "                    : ((currentPrice - pos.current_tp) / currentPrice) * 100)\n"
    "                : null\n"
    "              const slDist = pos.current_sl !== null && currentPrice > 0\n"
    "                ? (isLong\n"
    "                    ? ((currentPrice - pos.current_sl) / currentPrice) * 100\n"
    "                    : ((pos.current_sl - currentPrice) / currentPrice) * 100)\n"
    "                : null\n"
    "\n"
    "              // Progress bar: how far between SL and TP (only if both exist)\n"
    "              const totalRange = (tpDist !== null && slDist !== null) ? Math.abs(tpDist) + Math.abs(slDist) : 0\n"
    "              const progressPct = totalRange > 0 ? (Math.abs(slDist!) / totalRange) * 100 : 50"
)
edit(OC, old_oc_lifecycle, new_oc_lifecycle, "1f. OperationsChart lifecycle dist null-safe")

# 1g. OperationsChart — render the SL/TP distances with null guard
old_oc_render_dist = (
    "                    <div className=\"flex justify-between text-[9px] font-mono mb-0.5\">\n"
    "                      <span className=\"text-red-400\">SL {slDist.toFixed(2)}%</span>\n"
    "                      <span className={isPositive ? 'text-emerald-400' : 'text-red-400'}>\n"
    "                        {isPositive ? '+' : ''}{pos.pnl_pct.toFixed(3)}%\n"
    "                      </span>\n"
    "                      <span className=\"text-emerald-400\">TP {tpDist.toFixed(2)}%</span>\n"
    "                    </div>"
)
new_oc_render_dist = (
    "                    <div className=\"flex justify-between text-[9px] font-mono mb-0.5\">\n"
    "                      <span className=\"text-red-400\">SL {slDist !== null ? slDist.toFixed(2) + '%' : '—'}</span>\n"
    "                      <span className={isPositive ? 'text-emerald-400' : 'text-red-400'}>\n"
    "                        {isPositive ? '+' : ''}{pos.pnl_pct.toFixed(3)}%\n"
    "                      </span>\n"
    "                      <span className=\"text-emerald-400\">TP {tpDist !== null ? tpDist.toFixed(2) + '%' : '—'}</span>\n"
    "                    </div>"
)
edit(OC, old_oc_render_dist, new_oc_render_dist, "1g. OperationsChart render distances null-safe")

# 1h. OperationsChart — progress bar zones guard
old_oc_zones = (
    "                    <div className=\"relative h-3 bg-[#1a2334] rounded-full overflow-hidden\">\n"
    "                      {/* SL zone */}\n"
    "                      <div className=\"absolute left-0 top-0 h-full bg-red-500/20\" style={{ width: `${Math.max(5, Math.min(slDist / (Math.abs(slDist) + Math.abs(tpDist)) * 100, 95))}%` }} />\n"
    "                      {/* TP zone */}\n"
    "                      <div className=\"absolute right-0 top-0 h-full bg-emerald-500/20\" style={{ width: `${Math.max(5, Math.min(tpDist / (Math.abs(slDist) + Math.abs(tpDist)) * 100, 95))}%` }} />\n"
    "                      {/* Current price indicator */}\n"
    "                      <div\n"
    "                        className=\"absolute top-0 h-full w-0.5 bg-white z-10\"\n"
    "                        style={{ left: `${progressPct}%` }}\n"
    "                      />\n"
    "                    </div>"
)
new_oc_zones = (
    "                    <div className=\"relative h-3 bg-[#1a2334] rounded-full overflow-hidden\">\n"
    "                      {/* SL zone (only if SL exists) */}\n"
    "                      {slDist !== null && tpDist !== null && (\n"
    "                        <>\n"
    "                          <div className=\"absolute left-0 top-0 h-full bg-red-500/20\" style={{ width: `${Math.max(5, Math.min(slDist / (Math.abs(slDist) + Math.abs(tpDist)) * 100, 95))}%` }} />\n"
    "                          <div className=\"absolute right-0 top-0 h-full bg-emerald-500/20\" style={{ width: `${Math.max(5, Math.min(tpDist / (Math.abs(slDist) + Math.abs(tpDist)) * 100, 95))}%` }} />\n"
    "                        </>\n"
    "                      )}\n"
    "                      {/* Current price indicator */}\n"
    "                      <div\n"
    "                        className=\"absolute top-0 h-full w-0.5 bg-white z-10\"\n"
    "                        style={{ left: `${progressPct}%` }}\n"
    "                      />\n"
    "                      {(slDist === null || tpDist === null) && (\n"
    "                        <div className=\"absolute inset-0 flex items-center justify-center text-[8px] text-gray-500 font-mono\">\n"
    "                          manual entry — no SL/TP set\n"
    "                        </div>\n"
    "                      )}\n"
    "                    </div>"
)
edit(OC, old_oc_zones, new_oc_zones, "1h. OperationsChart progress bar null-safe")


# ════════════════════════════════════════════════════════════════════
# Fix 2 — Replace hardcoded 1000 with INITIAL_CAPITAL
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 2: Replace 1000 with INITIAL_CAPITAL ===")

PM = COMP / "portfolio-manager.tsx"
PFP = COMP / "performance-panel.tsx"

# 2a. portfolio-manager.tsx — import INITIAL_CAPITAL
edit(
    PM,
    "import { useTradingSocket } from '@/lib/use-trading-socket'",
    "import { useTradingSocket } from '@/lib/use-trading-socket'\n"
    "import { INITIAL_CAPITAL } from '@/lib/paper-trading-engine'",
    "2a. portfolio-manager.tsx: import INITIAL_CAPITAL",
)

# 2b. portfolio-manager.tsx — use store's totalPnlPct instead of recomputing with 1000
edit(
    PM,
    "  const {\n"
    "    portfolioValue, cash, realizedPnl, unrealizedPnl,\n"
    "    tokenStates, activeTokens, selectedToken,\n"
    "    exposurePct, totalTrades, winRate, maxDrawdown,\n"
    "    equityCurve, equityTimestamps,\n"
    "  } = useTradingStore()\n"
    "  const { emit } = useTradingSocket()\n"
    "\n"
    "  const tokens = Object.values(tokenStates)\n"
    "  const activeTokensList = tokens.filter(t => t.isActive)\n"
    "  const totalPnl = realizedPnl + unrealizedPnl\n"
    "  const isPositive = totalPnl >= 0\n"
    "  const totalPnlPct = portfolioValue > 0 ? ((portfolioValue - 1000) / 1000) * 100 : 0",
    "  const {\n"
    "    portfolioValue, cash, realizedPnl, unrealizedPnl,\n"
    "    tokenStates, activeTokens, selectedToken,\n"
    "    exposurePct, totalTrades, winRate, maxDrawdown,\n"
    "    equityCurve, equityTimestamps, totalPnlPct,\n"
    "  } = useTradingStore()\n"
    "  const { emit } = useTradingSocket()\n"
    "\n"
    "  const tokens = Object.values(tokenStates)\n"
    "  const activeTokensList = tokens.filter(t => t.isActive)\n"
    "  const totalPnl = realizedPnl + unrealizedPnl\n"
    "  const isPositive = totalPnl >= 0\n"
    "  // totalPnlPct comes from the store (computed correctly by the engine\n"
    "  // using INITIAL_CAPITAL). Previously hardcoded 1000 which gave wrong %.",
    "2b. portfolio-manager.tsx: use store's totalPnlPct (drop hardcoded 1000)",
)

# 2c. portfolio-manager.tsx — equity baseline 1000 -> INITIAL_CAPITAL
edit(
    PM,
    "  const equityData = equityCurve.slice(-60).map((val, i) => ({\n"
    "    time: equityTimestamps.slice(-60)[i],\n"
    "    value: val,\n"
    "    baseline: 1000,\n"
    "  }))",
    "  const equityData = equityCurve.slice(-60).map((val, i) => ({\n"
    "    time: equityTimestamps.slice(-60)[i],\n"
    "    value: val,\n"
    "    baseline: INITIAL_CAPITAL,\n"
    "  }))",
    "2c. portfolio-manager.tsx: equity baseline 1000 -> INITIAL_CAPITAL",
)

# 2d. performance-panel.tsx — clamp/equity math using 1000
edit(
    PFP,
    "  // Equity curve chart (simple SVG)\n"
    "  const recentEquity = equityCurve.slice(-100)\n"
    "  const eqMin = Math.min(...recentEquity, 1000)\n"
    "  const eqMax = Math.max(...recentEquity, 1000)\n"
    "  const eqRange = eqMax - eqMin || 1",
    "  // Equity curve chart (simple SVG)\n"
    "  // INITIAL_CAPITAL (10000) imported as the baseline reference.\n"
    "  const recentEquity = equityCurve.slice(-100)\n"
    "  const eqMin = Math.min(...recentEquity, INITIAL_CAPITAL)\n"
    "  const eqMax = Math.max(...recentEquity, INITIAL_CAPITAL)\n"
    "  const eqRange = eqMax - eqMin || 1",
    "2d. performance-panel.tsx: eqMin/eqMax 1000 -> INITIAL_CAPITAL",
)

# 2e. performance-panel.tsx — baseline y line at 1000
edit(
    PFP,
    "            {(() => {\n"
    "              const baseY = chartH - ((1000 - eqMin) / eqRange) * (chartH - 10) - 5\n"
    "              return <line x1=\"0\" y1={baseY} x2={chartW} y2={baseY} stroke=\"#1e2a3d\" strokeWidth=\"1\" strokeDasharray=\"4,4\" vectorEffect=\"non-scaling-stroke\" />\n"
    "            })()}",
    "            {(() => {\n"
    "              const baseY = chartH - ((INITIAL_CAPITAL - eqMin) / eqRange) * (chartH - 10) - 5\n"
    "              return <line x1=\"0\" y1={baseY} x2={chartW} y2={baseY} stroke=\"#1e2a3d\" strokeWidth=\"1\" strokeDasharray=\"4,4\" vectorEffect=\"non-scaling-stroke\" />\n"
    "            })()}",
    "2e. performance-panel.tsx: baseline y at INITIAL_CAPITAL",
)

# 2f. performance-panel.tsx — import INITIAL_CAPITAL
edit(
    PFP,
    "import { useTradingStore } from '@/stores/trading-store'",
    "import { useTradingStore } from '@/stores/trading-store'\n"
    "import { INITIAL_CAPITAL } from '@/lib/paper-trading-engine'",
    "2f. performance-panel.tsx: import INITIAL_CAPITAL",
)

# 2g. operations-chart.tsx — import INITIAL_CAPITAL + replace 1000s
edit(
    OC,
    "import { useTradingStore } from '@/stores/trading-store'",
    "import { useTradingStore } from '@/stores/trading-store'\n"
    "import { INITIAL_CAPITAL } from '@/lib/paper-trading-engine'",
    "2g. operations-chart.tsx: import INITIAL_CAPITAL",
)

edit(
    OC,
    "  const equityData = equityCurve.map((val, i) => ({\n"
    "    time: equityTimestamps[i],\n"
    "    value: val,\n"
    "    baseline: 1000,\n"
    "  }))",
    "  const equityData = equityCurve.map((val, i) => ({\n"
    "    time: equityTimestamps[i],\n"
    "    value: val,\n"
    "    baseline: INITIAL_CAPITAL,\n"
    "  }))",
    "2h. operations-chart.tsx: equity baseline 1000 -> INITIAL_CAPITAL",
)

edit(
    OC,
    "                {/* Baseline at 1000 */}\n"
    "                <ReferenceLine y={1000} stroke=\"#1e2a3d\" strokeWidth={1} />",
    "                {/* Baseline at INITIAL_CAPITAL */}\n"
    "                <ReferenceLine y={INITIAL_CAPITAL} stroke=\"#1e2a3d\" strokeWidth={1} />",
    "2i. operations-chart.tsx: baseline ReferenceLine 1000 -> INITIAL_CAPITAL",
)


# ════════════════════════════════════════════════════════════════════
# Fix 3 — Align store defaults with engine
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 3: Align store defaults with engine ===")

# 3a. trading-store.ts: symbol, selectedToken, autoMode defaults
edit(
    TS,
    "  symbol: 'SOL/USDT',\n"
    "  timeframe: '5m',",
    "  // Aligned with PaperTradingEngine defaults (BTC/USDT, 'live')\n"
    "  symbol: 'BTC/USDT',\n"
    "  timeframe: 'live',",
    "3a. store default symbol SOL/USDT -> BTC/USDT, timeframe 5m -> live",
)

edit(
    TS,
    "  autoMode: true,",
    "  autoMode: false,  // matches engine default (autoMode: false)",
    "3b. store default autoMode true -> false",
)

edit(
    TS,
    "  // Multi-token\n"
    "  activeTokens: ['SOL/USDT', 'BTC/USDT', 'ETH/USDT'] as string[],\n"
    "  tokenStates: {} as Record<string, TokenState>,\n"
    "  selectedToken: 'SOL/USDT',",
    "  // Multi-token — all 50 supported tokens active by default (matches engine)\n"
    "  activeTokens: [] as string[],  // engine will populate on first snapshot\n"
    "  tokenStates: {} as Record<string, TokenState>,\n"
    "  selectedToken: 'BTC/USDT',",
    "3c. store default selectedToken SOL -> BTC; activeTokens empty (engine populates)",
)

# 3d. trading-store.ts: portfolioValue default 1000 -> INITIAL_CAPITAL
# (we don't import INITIAL_CAPITAL in the store, just hardcode 10000 to match)
edit(
    TS,
    "  portfolioValue: 1000,\n"
    "  cash: 1000,",
    "  // Match INITIAL_CAPITAL from paper-trading-engine.ts (10000 USDT)\n"
    "  portfolioValue: 10000,\n"
    "  cash: 10000,",
    "3d. store default portfolioValue/cash 1000 -> 10000",
)

edit(
    TS,
    "  equityCurve: [1000],",
    "  equityCurve: [10000],",
    "3e. store default equityCurve [1000] -> [10000]",
)

# 3f. trading-store.ts: setConnected fallback 'demo' -> 'paper'
edit(
    TS,
    "  setConnected: (connected, mode) =>\n"
    "    set({\n"
    "      isConnected: connected,\n"
    "      engineMode: (mode as any) || (connected ? 'demo' : 'disconnected'),\n"
    "    }),",
    "  setConnected: (connected, mode) =>\n"
    "    set({\n"
    "      isConnected: connected,\n"
    "      engineMode: (mode as any) || (connected ? 'paper' : 'disconnected'),\n"
    "    }),",
    "3f. setConnected fallback 'demo' -> 'paper'",
)


# ════════════════════════════════════════════════════════════════════
# Fix 4 — manual-trade-panel.tsx: sync local symbol with store selectedToken
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 4: ManualTradePanel local symbol sync ===")

MTP = COMP / "manual-trade-panel.tsx"

# 4a. Add useEffect import
edit(
    MTP,
    "import { useState } from 'react'",
    "import { useState, useEffect } from 'react'",
    "4a. manual-trade-panel.tsx: import useEffect",
)

# 4b. Add useEffect to sync local symbol with store selectedToken
edit(
    MTP,
    "  const [symbol, setSymbol] = useState(selectedToken || 'BTC/USDT')\n"
    "  const [amount, setAmount] = useState('100')\n"
    "  const [lastResult, setLastResult] = useState<{ ok: boolean; msg: string } | null>(null)",
    "  const [symbol, setSymbol] = useState(selectedToken || 'BTC/USDT')\n"
    "  const [amount, setAmount] = useState('100')\n"
    "  const [lastResult, setLastResult] = useState<{ ok: boolean; msg: string } | null>(null)\n"
    "\n"
    "  // Sync local symbol with store selectedToken (e.g. when user clicks\n"
    "  // a token in TokenSelector or PortfolioManager)\n"
    "  useEffect(() => {\n"
    "    if (selectedToken) setSymbol(selectedToken)\n"
    "  }, [selectedToken])",
    "4b. manual-trade-panel.tsx: sync local symbol with store selectedToken",
)


# ════════════════════════════════════════════════════════════════════
# Fix 5 — page.tsx footer: 25 Tokens -> 50 Tokens
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 5: Footer token count ===")

PAGE = APP / "page.tsx"

edit(
    PAGE,
    "            PPMT v0.70 • PAPER TRADING • Live Binance Prices • 25 Tokens",
    "            PPMT v0.70 • PAPER TRADING • Live Binance Prices • 50 Tokens",
    "5. footer 25 Tokens -> 50 Tokens",
)


# ════════════════════════════════════════════════════════════════════
# Fix 6 — Replace dead timeframe <Select> with static LIVE badge
# ════════════════════════════════════════════════════════════════════
print("\n=== Fix 6: Remove dead timeframe selector ===")

# 6a. Drop the selectedTimeframe state — engine ignores it anyway
edit(
    PAGE,
    "  const [selectedSymbol, setSelectedSymbol] = useState(symbol)\n"
    "  const [selectedTimeframe, setSelectedTimeframe] = useState(timeframe)\n"
    "  const [activeTab, setActiveTab] = useState('dashboard')\n"
    "  const [currentTime, setCurrentTime] = useState('')",
    "  const [selectedSymbol, setSelectedSymbol] = useState(symbol)\n"
    "  const [activeTab, setActiveTab] = useState('dashboard')\n"
    "  const [currentTime, setCurrentTime] = useState('')",
    "6a. page.tsx: drop selectedTimeframe state",
)

# 6b. Drop handleTimeframeChange
edit(
    PAGE,
    "  const handleTimeframeChange = (val: string) => {\n"
    "    setSelectedTimeframe(val)\n"
    "    emit('switch-timeframe', { timeframe: val })\n"
    "  }\n"
    "\n",
    "",
    "6b. page.tsx: drop handleTimeframeChange",
)

# 6c. Drop start-trading payload's stale capital:1000 + timeframe
edit(
    PAGE,
    "      emit('start-trading', { symbol: selectedSymbol, timeframe: selectedTimeframe, capital: 1000 })",
    "      emit('start-trading', { symbol: selectedSymbol })",
    "6c. page.tsx: drop stale capital/timeframe from start-trading payload",
)

# 6d. Replace the timeframe <Select> with a static LIVE badge
edit(
    PAGE,
    "        {/* Timeframe Selector */}\n"
    "        <div className=\"flex items-center gap-2 shrink-0\">\n"
    "          <span className=\"text-[10px] text-gray-500 font-mono\">TF</span>\n"
    "          <Select value={selectedTimeframe} onValueChange={handleTimeframeChange}>\n"
    "            <SelectTrigger className=\"h-7 w-20 bg-[#121a26] border-[#1e2a3d] text-xs font-mono\">\n"
    "              <SelectValue />\n"
    "            </SelectTrigger>\n"
    "            <SelectContent className=\"bg-[#121a26] border-[#1e2a3d]\">\n"
    "              <SelectItem value=\"5m\" className=\"text-xs font-mono\">5m</SelectItem>\n"
    "              <SelectItem value=\"15m\" className=\"text-xs font-mono\">15m</SelectItem>\n"
    "              <SelectItem value=\"1h\" className=\"text-xs font-mono\">1h</SelectItem>\n"
    "            </SelectContent>\n"
    "          </Select>\n"
    "        </div>",
    "        {/* Live price indicator (engine always streams real-time, no timeframe) */}\n"
    "        <div className=\"flex items-center gap-2 shrink-0\">\n"
    "          <span className=\"text-[10px] text-gray-500 font-mono\">TF</span>\n"
    "          <Badge variant=\"outline\" className=\"h-7 bg-[#121a26] border-emerald-500/30 text-emerald-400 text-xs font-mono px-2 flex items-center gap-1\">\n"
    "            <div className=\"w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse\" />\n"
    "            LIVE\n"
    "          </Badge>\n"
    "        </div>",
    "6d. page.tsx: replace timeframe Select with LIVE badge",
)

# 6e. Drop the now-unused Select imports from page.tsx (we still use SelectContent etc.? let's check)
# Actually we still need Select for nothing else in page.tsx now. Drop the imports.
edit(
    PAGE,
    "import { Badge } from '@/components/ui/badge'\n"
    "import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'\n"
    "import {\n"
    "  Select,\n"
    "  SelectContent,\n"
    "  SelectItem,\n"
    "  SelectTrigger,\n"
    "  SelectValue,\n"
    "} from '@/components/ui/select'\n"
    "import { useState, useEffect } from 'react'",
    "import { Badge } from '@/components/ui/badge'\n"
    "import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'\n"
    "import { useState, useEffect } from 'react'",
    "6e. page.tsx: drop unused Select imports",
)


print("\n=== DONE ===")
print("Run verify_braces.py next to sanity-check structure.")
