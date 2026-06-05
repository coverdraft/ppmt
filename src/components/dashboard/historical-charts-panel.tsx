'use client';

import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { motion } from 'framer-motion';
import {
  Trophy,
  BarChart3,
  Activity,
  Star,
  Loader2,
  Flame,
  TrendingUp,
  DollarSign,
  Clock,
} from 'lucide-react';
import { useState, useMemo } from 'react';
import {
  ComposedChart,
  Area,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
} from 'recharts';

// ============================================================
// TYPES
// ============================================================

interface EvolutionCycleData {
  id: string;
  cycleNumber: number;
  runId: string;
  currentPhase: string;
  status: string;
  bestScore: number;
  improvedCount: number;
  degradedCount: number;
  totalMutations: number;
  strategiesActivated: string;
  entriesExecuted: string;
  exitsProcessed: string;
  errors: string;
  durationMs: number;
  startedAt: string;
  completedAt: string;
}

interface BacktestListItem {
  id: string;
  systemId: string;
  systemName: string;
  systemCategory: string;
  systemIcon: string;
  mode: string;
  status: string;
  totalPnl: number;
  totalPnlPct: number;
  sharpeRatio: number;
  winRate: number;
  maxDrawdownPct: number;
  totalTrades: number;
  operationCount: number;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
}

interface BestStrategy {
  id: string;
  strategyName: string;
  category: string;
  timeframe: string;
  tokenAgeCategory: string;
  riskTolerance: string;
  capitalAllocation: number;
  pnlPct: number;
  pnlUsd: number;
  sharpeRatio: number;
  winRate: number;
  maxDrawdownPct: number;
  profitFactor: number;
  totalTrades: number;
  avgHoldTimeMin: number;
  score: number;
  backtestId: string | null;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

// ============================================================
// HELPERS
// ============================================================

function formatPct(v: number): string {
  if (v == null || isNaN(v)) return '0.00%';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function formatCurrency(v: number): string {
  if (v == null || isNaN(v)) return '$0.00';
  const sign = v < 0 ? '-' : '';
  const abs = Math.abs(v);
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

function getGrade(score: number): { grade: string; color: string } {
  if (score >= 80) return { grade: 'A+', color: 'text-emerald-400' };
  if (score >= 65) return { grade: 'A', color: 'text-emerald-400' };
  if (score >= 50) return { grade: 'B', color: 'text-cyan-400' };
  if (score >= 35) return { grade: 'C', color: 'text-amber-400' };
  if (score >= 20) return { grade: 'D', color: 'text-orange-400' };
  return { grade: 'F', color: 'text-red-400' };
}

function formatCategory(cat: string): string {
  if (!cat) return '';
  return cat.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function HistoricalChartsPanel() {
  const [selectedChart, setSelectedChart] = useState<'evolution' | 'backtests' | 'paper' | 'hof'>('evolution');

  // Fetch evolution cycle history
  const { data: evoData, isLoading: evoLoading } = useQuery({
    queryKey: ['auto-evolution-history'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/auto-evolution');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as {
          dbCycleHistory: EvolutionCycleData[];
          totalCycles: number;
          isRunning: boolean;
          currentCycle: number;
        } | null;
      } catch {
        return null;
      }
    },
    staleTime: 30000,
    refetchInterval: 60000,
  });

  // Fetch backtest history
  const { data: backtestData, isLoading: btLoading } = useQuery({
    queryKey: ['backtest-history-charts'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/backtest?status=COMPLETED');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as BacktestListItem[] | null;
      } catch {
        return null;
      }
    },
    staleTime: 30000,
    refetchInterval: 60000,
  });

  // Fetch Paper Trading trades from DB
  const { data: paperTradeData, isLoading: ptLoading } = useQuery({
    queryKey: ['paper-trading-db-trades'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/paper-trading/trades');
        if (!res.ok) return null;
        const json = await res.json();
        return (json.data || []) as Array<{
          id: string;
          tokenSymbol: string;
          chain: string;
          direction: string;
          entryPrice: number;
          exitPrice: number;
          sizeUsd: number;
          pnlUsd: number;
          pnlPct: number;
          holdTimeMin: number | null;
          mfe: number;
          mae: number;
          exitReason: string | null;
          strategyName: string | null;
          openedAt: string;
          closedAt: string;
        }>;
      } catch {
        return null;
      }
    },
    staleTime: 15000,
    refetchInterval: 30000,
  });

  // Fetch Paper Trading status for current positions
  const { data: paperStatusData } = useQuery({
    queryKey: ['paper-trading-status-charts'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/paper-trading');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as {
          stats: {
            status: string;
            currentCapital: number;
            initialCapital: number;
            totalPnlUsd: number;
            totalPnlPct: number;
            winRate: number;
            totalTrades: number;
            winningTrades: number;
            maxDrawdownPct: number;
            sharpeRatio: number;
            unrealizedPnlUsd: number;
          };
          openPositions: Array<{
            id: string;
            symbol: string;
            chain: string;
            direction: string;
            entryPrice: number;
            currentPrice: number;
            unrealizedPnlPct: number;
          }>;
        } | null;
      } catch {
        return null;
      }
    },
    staleTime: 10000,
    refetchInterval: 15000,
  });

  // Fetch Hall of Fame
  const { data: hofData, isLoading: hofLoading } = useQuery({
    queryKey: ['hall-of-fame-charts'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/strategy-optimizer/best');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as BestStrategy[] | null;
      } catch {
        return null;
      }
    },
    staleTime: 30000,
    refetchInterval: 60000,
  });

  const isLoading = evoLoading || btLoading || ptLoading || hofLoading;
  const cycles = evoData?.dbCycleHistory ?? [];
  const backtests = backtestData ?? [];
  const paperTrades = paperTradeData ?? [];
  const paperStats = paperStatusData?.stats;
  const paperPositions = paperStatusData?.openPositions ?? [];
  const hofStrategies = hofData ?? [];

  // ---- EVOLUTION CYCLE CHART DATA ----
  const evoChartData = useMemo(() => {
    if (!cycles.length) return [];
    return [...cycles]
      .sort((a, b) => a.cycleNumber - b.cycleNumber)
      .map(c => ({
        cycle: c.cycleNumber,
        score: Math.round(c.bestScore * 100) / 100,
        improvements: c.improvedCount,
        degradations: c.degradedCount,
        mutations: c.totalMutations,
        duration: Math.round(c.durationMs / 1000),
        date: new Date(c.completedAt || c.startedAt).toLocaleDateString(),
      }));
  }, [cycles]);

  // ---- BACKTEST PERFORMANCE CHART DATA ----
  const btChartData = useMemo(() => {
    if (!backtests.length) return [];
    return [...backtests]
      .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime())
      .map((bt, i) => ({
        index: i + 1,
        name: bt.systemName?.substring(0, 15) || `BT ${i + 1}`,
        pnlPct: Math.round(bt.totalPnlPct * 100) / 100,
        sharpe: Math.round(bt.sharpeRatio * 100) / 100,
        winRate: Math.round(bt.winRate * 10000) / 100,
        maxDD: Math.round(bt.maxDrawdownPct * 100) / 100,
        trades: bt.totalTrades,
        date: new Date(bt.createdAt).toLocaleDateString(),
      }));
  }, [backtests]);

  // ---- PAPER TRADING CHART DATA ----
  const ptChartData = useMemo(() => {
    if (!paperTrades.length) return [];
    const sorted = [...paperTrades].sort(
      (a, b) => new Date(a.closedAt).getTime() - new Date(b.closedAt).getTime()
    );
    let cumulativePnl = 0;
    let capital = paperStats?.initialCapital || 10;
    return sorted.map((t, i) => {
      cumulativePnl += t.pnlUsd;
      capital += t.pnlUsd;
      const cumPnlPct = ((capital - (paperStats?.initialCapital || 10)) / (paperStats?.initialCapital || 10)) * 100;
      return {
        trade: i + 1,
        pnlUsd: Math.round(t.pnlUsd * 100) / 100,
        pnlPct: Math.round(t.pnlPct * 100) / 100,
        cumulativePnl: Math.round(cumulativePnl * 100) / 100,
        capital: Math.round(capital * 100) / 100,
        cumPnlPct: Math.round(cumPnlPct * 100) / 100,
        symbol: t.tokenSymbol,
        holdMin: t.holdTimeMin || 0,
        exitReason: t.exitReason || 'unknown',
        date: new Date(t.closedAt).toLocaleDateString(),
        time: new Date(t.closedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
    });
  }, [paperTrades, paperStats]);

  const ptExitReasonStats = useMemo(() => {
    if (!paperTrades.length) return [];
    const counts: Record<string, number> = {};
    for (const t of paperTrades) {
      const reason = t.exitReason || 'unknown';
      counts[reason] = (counts[reason] || 0) + 1;
    }
    return Object.entries(counts)
      .map(([reason, count]) => ({ reason, count }))
      .sort((a, b) => b.count - a.count);
  }, [paperTrades]);

  // ---- HOF CATEGORY BREAKDOWN ----
  const hofByCategory = useMemo(() => {
    if (!hofStrategies.length) return [];
    const grouped: Record<string, { category: string; count: number; avgScore: number; avgPnlPct: number; bestPnlPct: number }> = {};
    for (const s of hofStrategies) {
      const cat = formatCategory(s.category || 'Other');
      if (!grouped[cat]) {
        grouped[cat] = { category: cat, count: 0, avgScore: 0, avgPnlPct: 0, bestPnlPct: -Infinity };
      }
      grouped[cat].count++;
      grouped[cat].avgScore += s.score;
      grouped[cat].avgPnlPct += s.pnlPct;
      if (s.pnlPct > grouped[cat].bestPnlPct) grouped[cat].bestPnlPct = s.pnlPct;
    }
    return Object.values(grouped).map(g => ({
      ...g,
      avgScore: Math.round((g.avgScore / g.count) * 100) / 100,
      avgPnlPct: Math.round((g.avgPnlPct / g.count) * 100) / 100,
      bestPnlPct: Math.round(g.bestPnlPct * 100) / 100,
    })).sort((a, b) => b.avgScore - a.avgScore);
  }, [hofStrategies]);

  // ---- LOADING ----
  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0d1117]">
        <Loader2 className="h-8 w-8 animate-spin text-[#3b82f6] mb-3" />
        <span className="text-sm font-mono text-[#64748b]">Loading analytics...</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#3b82f6]/10 border border-[#3b82f6]/20">
            <BarChart3 className="h-3.5 w-3.5 text-[#3b82f6]" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Strategy Analytics</span>
            <span className="text-[9px] font-mono text-[#475569] ml-2">
              {cycles.length} ciclos · {backtests.length} backtests · {paperTrades.length} paper trades · {hofStrategies.length} HoF
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {(['evolution', 'backtests', 'paper', 'hof'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setSelectedChart(tab)}
              className={`text-[9px] font-mono px-2.5 py-1 rounded transition-colors ${
                selectedChart === tab
                  ? 'bg-[#3b82f6]/10 text-[#3b82f6] border border-[#3b82f6]/30'
                  : 'text-[#64748b] hover:text-[#94a3b8]'
              }`}
            >
              {tab === 'evolution' ? 'Evolution' : tab === 'backtests' ? 'Backtests' : tab === 'paper' ? 'Paper' : 'Hall of Fame'}
            </button>
          ))}
        </div>
      </div>

      {/* ===== CHARTS CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0 p-3 space-y-4">
        {/* ---- EVOLUTION TAB ---- */}
        {selectedChart === 'evolution' && (
          <>
            {evoChartData.length === 0 ? (
              <EmptyState
                icon={<Activity className="h-6 w-6" />}
                title="No Evolution Data"
                desc="Run auto-evolution cycles to see score progression and mutation history over time."
              />
            ) : (
              <>
                {/* Score Progression */}
                <ChartCard title="Score Progression" subtitle="Best score per cycle">
                  <ResponsiveContainer width="100%" height={200}>
                    <ComposedChart data={evoChartData} margin={{ top: 10, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="cycle" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} />
                      <YAxis tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} width={40} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '6px', fontSize: '10px', fontFamily: 'monospace' }} />
                      <ReferenceLine y={50} stroke="#475569" strokeDasharray="3 3" label={{ value: 'B', fill: '#475569', fontSize: 8 }} />
                      <Area type="monotone" dataKey="score" stroke="#10b981" fill="url(#evoScoreGrad)" strokeWidth={2} />
                      <defs>
                        <linearGradient id="evoScoreGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>

                {/* Mutations: Improvements vs Degradations */}
                <ChartCard title="Mutations per Cycle" subtitle="Improved vs Degraded strategies">
                  <ResponsiveContainer width="100%" height={160}>
                    <ComposedChart data={evoChartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="cycle" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} />
                      <YAxis tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} width={30} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '6px', fontSize: '10px', fontFamily: 'monospace' }} />
                      <Bar dataKey="improvements" fill="#10b981" radius={[2, 2, 0, 0]} name="Improved" />
                      <Bar dataKey="degradations" fill="#ef4444" radius={[2, 2, 0, 0]} name="Degraded" />
                      <Legend wrapperStyle={{ fontSize: '9px', fontFamily: 'monospace' }} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>

                {/* Cycle Duration */}
                <ChartCard title="Cycle Duration (seconds)" subtitle="Time per cycle execution">
                  <ResponsiveContainer width="100%" height={120}>
                    <ComposedChart data={evoChartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="cycle" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} width={35} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '4px', fontSize: '9px', fontFamily: 'monospace' }} />
                      <Bar dataKey="duration" fill="#3b82f6" radius={[2, 2, 0, 0]} name="Duration (s)" />
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>

                {/* Cycle summary table */}
                <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
                  <div className="px-3 py-2 border-b border-[#1e293b] bg-[#0a0e17]">
                    <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                      Cycle History (Last {cycles.length})
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-[9px] font-mono">
                      <thead>
                        <tr className="bg-[#0a0e17] text-[#475569]">
                          <th className="px-2 py-1 text-left">#</th>
                          <th className="px-2 py-1 text-right">Score</th>
                          <th className="px-2 py-1 text-right">Impr</th>
                          <th className="px-2 py-1 text-right">Degr</th>
                          <th className="px-2 py-1 text-right">Mutations</th>
                          <th className="px-2 py-1 text-right">Duration</th>
                          <th className="px-2 py-1 text-right">Completed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...cycles].sort((a, b) => b.cycleNumber - a.cycleNumber).slice(0, 15).map(c => {
                          const gradeInfo = getGrade(c.bestScore);
                          return (
                            <tr key={c.id} className="border-t border-[#1e293b]/50 hover:bg-[#0a0e17]/50">
                              <td className="px-2 py-1 text-[#e2e8f0] font-bold">{c.cycleNumber}</td>
                              <td className={`px-2 py-1 text-right font-bold ${gradeInfo.color}`}>
                                {c.bestScore.toFixed(1)} ({gradeInfo.grade})
                              </td>
                              <td className="px-2 py-1 text-right text-emerald-400">{c.improvedCount}</td>
                              <td className="px-2 py-1 text-right text-red-400">{c.degradedCount}</td>
                              <td className="px-2 py-1 text-right text-[#94a3b8]">{c.totalMutations}</td>
                              <td className="px-2 py-1 text-right text-[#64748b]">{(c.durationMs / 1000).toFixed(1)}s</td>
                              <td className="px-2 py-1 text-right text-[#475569]">
                                {c.completedAt ? new Date(c.completedAt).toLocaleString() : '-'}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </>
        )}

        {/* ---- BACKTESTS TAB ---- */}
        {selectedChart === 'backtests' && (
          <>
            {btChartData.length === 0 ? (
              <EmptyState
                icon={<BarChart3 className="h-6 w-6" />}
                title="No Backtest Data"
                desc="Run backtests through the AI Manager or Backtesting Lab to see performance trends."
              />
            ) : (
              <>
                {/* PnL Distribution */}
                <ChartCard title="PnL % per Backtest" subtitle="Profit/loss distribution across all backtests">
                  <ResponsiveContainer width="100%" height={200}>
                    <ComposedChart data={btChartData} margin={{ top: 10, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="index" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} />
                      <YAxis tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} width={50} tickFormatter={(v: number) => `${v}%`} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '6px', fontSize: '10px', fontFamily: 'monospace' }} />
                      <ReferenceLine y={0} stroke="#475569" strokeWidth={1} />
                      <Bar dataKey="pnlPct" name="PnL %" radius={[2, 2, 0, 0]}
                        fill="#10b981"
                        // We can't easily do conditional fills in Recharts Bar, so we use a custom shape
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>

                {/* Sharpe Ratio Trend */}
                <ChartCard title="Sharpe Ratio Trend" subtitle="Risk-adjusted return over backtests">
                  <ResponsiveContainer width="100%" height={160}>
                    <ComposedChart data={btChartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="index" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} width={35} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '4px', fontSize: '9px', fontFamily: 'monospace' }} />
                      <ReferenceLine y={1} stroke="#475569" strokeDasharray="3 3" label={{ value: 'Good', fill: '#475569', fontSize: 8 }} />
                      <Line type="monotone" dataKey="sharpe" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3, fill: '#3b82f6' }} name="Sharpe" />
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>

                {/* Win Rate vs Max Drawdown */}
                <ChartCard title="Win Rate vs Max Drawdown" subtitle="Risk-reward comparison per backtest">
                  <ResponsiveContainer width="100%" height={160}>
                    <ComposedChart data={btChartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="index" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} />
                      <YAxis yAxisId="left" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} width={35} domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
                      <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} width={35} tickFormatter={(v: number) => `${v}%`} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '4px', fontSize: '9px', fontFamily: 'monospace' }} />
                      <Legend wrapperStyle={{ fontSize: '9px', fontFamily: 'monospace' }} />
                      <Line yAxisId="left" type="monotone" dataKey="winRate" stroke="#10b981" strokeWidth={2} dot={false} name="Win Rate %" />
                      <Area yAxisId="right" type="monotone" dataKey="maxDD" stroke="#ef4444" fill="url(#ddGrad)" strokeWidth={1.5} name="Max DD %" />
                      <defs>
                        <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#ef4444" stopOpacity={0.2} />
                          <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>

                {/* Backtests summary table */}
                <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
                  <div className="px-3 py-2 border-b border-[#1e293b] bg-[#0a0e17]">
                    <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                      Backtest History ({backtests.length})
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-[9px] font-mono">
                      <thead>
                        <tr className="bg-[#0a0e17] text-[#475569]">
                          <th className="px-2 py-1 text-left">System</th>
                          <th className="px-2 py-1 text-right">PnL %</th>
                          <th className="px-2 py-1 text-right">Sharpe</th>
                          <th className="px-2 py-1 text-right">Win Rate</th>
                          <th className="px-2 py-1 text-right">Max DD</th>
                          <th className="px-2 py-1 text-right">Trades</th>
                          <th className="px-2 py-1 text-right">Date</th>
                        </tr>
                      </thead>
                      <tbody>
                        {backtests.slice(0, 20).map(bt => (
                          <tr key={bt.id} className="border-t border-[#1e293b]/50 hover:bg-[#0a0e17]/50">
                            <td className="px-2 py-1 text-[#e2e8f0] font-bold max-w-[120px] truncate">
                              {bt.systemIcon} {bt.systemName}
                            </td>
                            <td className={`px-2 py-1 text-right font-bold ${bt.totalPnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {formatPct(bt.totalPnlPct)}
                            </td>
                            <td className={`px-2 py-1 text-right ${bt.sharpeRatio >= 1 ? 'text-emerald-400' : bt.sharpeRatio >= 0 ? 'text-amber-400' : 'text-red-400'}`}>
                              {bt.sharpeRatio.toFixed(2)}
                            </td>
                            <td className={`px-2 py-1 text-right ${bt.winRate >= 0.5 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {(bt.winRate * 100).toFixed(1)}%
                            </td>
                            <td className="px-2 py-1 text-right text-red-400/70">
                              {bt.maxDrawdownPct.toFixed(1)}%
                            </td>
                            <td className="px-2 py-1 text-right text-[#94a3b8]">
                              {bt.totalTrades}
                            </td>
                            <td className="px-2 py-1 text-right text-[#475569]">
                              {new Date(bt.createdAt).toLocaleDateString()}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </>
        )}

        {/* ---- PAPER TRADING TAB ---- */}
        {selectedChart === 'paper' && (
          <>
            {ptChartData.length === 0 && paperPositions.length === 0 ? (
              <EmptyState
                icon={<TrendingUp className="h-6 w-6" />}
                title="Sin datos de Paper Trading"
                desc="Inicia el paper trading desde el panel de control para ver la equity curve y el historial de trades aquí."
              />
            ) : (
              <>
                {/* Stats Row */}
                {paperStats && (
                  <div className="grid grid-cols-4 gap-2">
                    <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg px-3 py-2">
                      <div className="text-[8px] font-mono text-[#475569] uppercase">Capital</div>
                      <div className="text-[12px] font-mono font-bold text-[#f1f5f9]">{formatCurrency(paperStats.currentCapital)}</div>
                    </div>
                    <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg px-3 py-2">
                      <div className="text-[8px] font-mono text-[#475569] uppercase">PnL Total</div>
                      <div className={`text-[12px] font-mono font-bold ${paperStats.totalPnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {formatPct(paperStats.totalPnlPct)}
                      </div>
                    </div>
                    <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg px-3 py-2">
                      <div className="text-[8px] font-mono text-[#475569] uppercase">Win Rate</div>
                      <div className={`text-[12px] font-mono font-bold ${paperStats.winRate >= 0.5 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {(paperStats.winRate * 100).toFixed(1)}%
                      </div>
                    </div>
                    <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg px-3 py-2">
                      <div className="text-[8px] font-mono text-[#475569] uppercase">Max DD</div>
                      <div className="text-[12px] font-mono font-bold text-red-400">
                        {paperStats.maxDrawdownPct.toFixed(1)}%
                      </div>
                    </div>
                  </div>
                )}

                {/* Equity Curve (Cumulative PnL) */}
                <ChartCard title="Equity Curve" subtitle={`Capital acumulado desde ${paperTrades.length} trades`}>
                  <ResponsiveContainer width="100%" height={220}>
                    <ComposedChart data={ptChartData} margin={{ top: 10, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="trade" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} />
                      <YAxis yAxisId="left" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} width={50} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
                      <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} width={45} tickFormatter={(v: number) => `${v}%`} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '6px', fontSize: '10px', fontFamily: 'monospace' }} />
                      <ReferenceLine yAxisId="left" y={paperStats?.initialCapital || 10} stroke="#475569" strokeDasharray="3 3" />
                      <Area yAxisId="left" type="monotone" dataKey="capital" stroke="#10b981" fill="url(#ptEquityGrad)" strokeWidth={2} name="Capital" />
                      <Line yAxisId="right" type="monotone" dataKey="cumPnlPct" stroke="#3b82f6" strokeWidth={1.5} dot={false} name="PnL %" />
                      <defs>
                        <linearGradient id="ptEquityGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <Legend wrapperStyle={{ fontSize: '9px', fontFamily: 'monospace' }} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>

                {/* PnL per Trade */}
                <ChartCard title="PnL por Trade" subtitle="Ganancia/pérdida de cada trade individual">
                  <ResponsiveContainer width="100%" height={160}>
                    <ComposedChart data={ptChartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="trade" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} width={50} tickFormatter={(v: number) => `${v}%`} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '4px', fontSize: '9px', fontFamily: 'monospace' }} />
                      <ReferenceLine y={0} stroke="#475569" strokeWidth={1} />
                      <Bar dataKey="pnlPct" name="PnL %" radius={[2, 2, 0, 0]}
                        fill="#10b981"
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                </ChartCard>

                {/* Exit Reason Distribution */}
                {ptExitReasonStats.length > 0 && (
                  <ChartCard title="Razones de Salida" subtitle="Distribución de cómo se cerraron los trades">
                    <ResponsiveContainer width="100%" height={140}>
                      <ComposedChart data={ptExitReasonStats} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} layout="vertical">
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis type="number" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} />
                        <YAxis dataKey="reason" type="category" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} width={100} />
                        <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '4px', fontSize: '9px', fontFamily: 'monospace' }} />
                        <Bar dataKey="count" fill="#f59e0b" radius={[0, 2, 2, 0]} name="Trades" barSize={14} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </ChartCard>
                )}

                {/* Open Positions */}
                {paperPositions.length > 0 && (
                  <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
                    <div className="px-3 py-2 border-b border-[#1e293b] bg-[#0a0e17] flex items-center gap-2">
                      <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                        Posiciones Abiertas ({paperPositions.length})
                      </span>
                      <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-[9px] font-mono">
                        <thead>
                          <tr className="bg-[#0a0e17] text-[#475569]">
                            <th className="px-2 py-1 text-left">Token</th>
                            <th className="px-2 py-1 text-right">Entry</th>
                            <th className="px-2 py-1 text-right">Current</th>
                            <th className="px-2 py-1 text-right">PnL %</th>
                          </tr>
                        </thead>
                        <tbody>
                          {paperPositions.map(p => (
                            <tr key={p.id} className="border-t border-[#1e293b]/50 hover:bg-[#0a0e17]/50">
                              <td className="px-2 py-1 text-[#e2e8f0] font-bold">{p.symbol}</td>
                              <td className="px-2 py-1 text-right text-[#94a3b8]">${p.entryPrice.toFixed(6)}</td>
                              <td className="px-2 py-1 text-right text-[#94a3b8]">${p.currentPrice.toFixed(6)}</td>
                              <td className={`px-2 py-1 text-right font-bold ${p.unrealizedPnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                {formatPct(p.unrealizedPnlPct)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Trade History Table */}
                {paperTrades.length > 0 && (
                  <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
                    <div className="px-3 py-2 border-b border-[#1e293b] bg-[#0a0e17]">
                      <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                        Historial de Trades ({paperTrades.length})
                      </span>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-[9px] font-mono">
                        <thead>
                          <tr className="bg-[#0a0e17] text-[#475569]">
                            <th className="px-2 py-1 text-left">Token</th>
                            <th className="px-2 py-1 text-right">PnL %</th>
                            <th className="px-2 py-1 text-right">PnL $</th>
                            <th className="px-2 py-1 text-right">Hold</th>
                            <th className="px-2 py-1 text-right">Exit</th>
                            <th className="px-2 py-1 text-right">MFE</th>
                            <th className="px-2 py-1 text-right">Fecha</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...paperTrades].sort((a, b) => new Date(b.closedAt).getTime() - new Date(a.closedAt).getTime()).slice(0, 20).map(t => (
                            <tr key={t.id} className="border-t border-[#1e293b]/50 hover:bg-[#0a0e17]/50">
                              <td className="px-2 py-1 text-[#e2e8f0] font-bold">{t.tokenSymbol}</td>
                              <td className={`px-2 py-1 text-right font-bold ${t.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                {formatPct(t.pnlPct)}
                              </td>
                              <td className={`px-2 py-1 text-right ${t.pnlUsd >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                {formatCurrency(t.pnlUsd)}
                              </td>
                              <td className="px-2 py-1 text-right text-[#94a3b8]">
                                {t.holdTimeMin ? `${t.holdTimeMin.toFixed(0)}m` : '-'}
                              </td>
                              <td className="px-2 py-1 text-right text-[#64748b]">
                                {t.exitReason || '-'}
                              </td>
                              <td className="px-2 py-1 text-right text-emerald-400/70">
                                {t.mfe.toFixed(1)}%
                              </td>
                              <td className="px-2 py-1 text-right text-[#475569]">
                                {new Date(t.closedAt).toLocaleDateString()}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            )}
          </>
        )}

        {/* ---- HALL OF FAME TAB ---- */}
        {selectedChart === 'hof' && (
          <>
            {hofStrategies.length === 0 ? (
              <EmptyState
                icon={<Trophy className="h-6 w-6" />}
                title="No Hall of Fame Entries"
                desc="The best strategies from auto-evolution cycles will appear here when they achieve top scores."
              />
            ) : (
              <>
                {/* Category Breakdown Chart */}
                {hofByCategory.length > 0 && (
                  <ChartCard title="Performance by Category" subtitle="Average score and PnL per strategy category">
                    <ResponsiveContainer width="100%" height={200}>
                      <ComposedChart data={hofByCategory} margin={{ top: 10, right: 10, left: 10, bottom: 5 }} layout="vertical">
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis type="number" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} />
                        <YAxis dataKey="category" type="category" tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }} axisLine={false} tickLine={false} width={100} />
                        <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '6px', fontSize: '10px', fontFamily: 'monospace' }} />
                        <Bar dataKey="avgScore" fill="#3b82f6" radius={[0, 2, 2, 0]} name="Avg Score" barSize={16} />
                        <Legend wrapperStyle={{ fontSize: '9px', fontFamily: 'monospace' }} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </ChartCard>
                )}

                {/* HoF Cards */}
                <div className="space-y-2">
                  {hofStrategies.slice(0, 20).map((s, i) => {
                    const gradeInfo = getGrade(s.score);
                    return (
                      <motion.div
                        key={s.id}
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: i * 0.03 }}
                        className={`flex items-center gap-3 px-3 py-2 bg-[#0d1117] border rounded-lg ${
                          i === 0
                            ? 'border-[#d4af37]/40 bg-[#d4af37]/5'
                            : 'border-[#1e293b]'
                        }`}
                      >
                        {/* Rank */}
                        <div className={`flex items-center justify-center w-7 h-7 rounded-lg font-mono font-bold text-[11px] ${
                          i === 0 ? 'bg-[#d4af37]/20 text-[#d4af37] border border-[#d4af37]/30' :
                          i === 1 ? 'bg-gray-400/10 text-gray-400' :
                          i === 2 ? 'bg-amber-600/10 text-amber-600' :
                          'bg-[#1a1f2e] text-[#64748b]'
                        }`}>
                          {i === 0 ? <Star className="h-3.5 w-3.5" /> : `#${i + 1}`}
                        </div>

                        {/* Strategy info */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-mono font-bold text-[#e2e8f0] truncate">{s.strategyName}</span>
                            {i === 0 && <Flame className="h-3 w-3 text-[#d4af37] shrink-0" />}
                            <Badge className="text-[7px] h-3.5 px-1.5 bg-[#1a1f2e] text-[#94a3b8] border-[#2d3748] shrink-0">
                              {formatCategory(s.category)}
                            </Badge>
                            <Badge className="text-[7px] h-3.5 px-1.5 bg-[#1a1f2e] text-[#64748b] border-[#2d3748] shrink-0">
                              {s.timeframe}
                            </Badge>
                          </div>
                          <div className="flex items-center gap-3 mt-0.5">
                            <span className="text-[8px] font-mono text-[#475569]">
                              {s.totalTrades} trades · {s.avgHoldTimeMin.toFixed(0)}min avg
                            </span>
                          </div>
                        </div>

                        {/* Grade */}
                        <div className={`text-[14px] font-mono font-black ${gradeInfo.color}`}>
                          {gradeInfo.grade}
                        </div>

                        {/* Score bar */}
                        <div className="w-20">
                          <div className="h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${
                                s.score >= 65 ? 'bg-emerald-400' :
                                s.score >= 50 ? 'bg-cyan-400' :
                                s.score >= 35 ? 'bg-amber-400' :
                                'bg-red-400'
                              }`}
                              style={{ width: `${Math.min(100, s.score)}%` }}
                            />
                          </div>
                          <div className="text-[8px] font-mono text-[#64748b] mt-0.5">{s.score.toFixed(1)}</div>
                        </div>

                        {/* Key metrics */}
                        <div className="flex items-center gap-3 shrink-0">
                          <div className="text-right">
                            <div className={`text-[10px] font-mono font-bold ${s.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {formatPct(s.pnlPct)}
                            </div>
                            <div className="text-[8px] font-mono text-[#475569]">PnL</div>
                          </div>
                          <div className="text-right">
                            <div className={`text-[10px] font-mono font-bold ${s.sharpeRatio >= 1 ? 'text-emerald-400' : 'text-[#94a3b8]'}`}>
                              {s.sharpeRatio.toFixed(2)}
                            </div>
                            <div className="text-[8px] font-mono text-[#475569]">Sharpe</div>
                          </div>
                          <div className="text-right">
                            <div className={`text-[10px] font-mono font-bold ${s.winRate >= 0.5 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {(s.winRate * 100).toFixed(0)}%
                            </div>
                            <div className="text-[8px] font-mono text-[#475569]">WR</div>
                          </div>
                        </div>
                      </motion.div>
                    );
                  })}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================
// SUB-COMPONENTS
// ============================================================

function ChartCard({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b border-[#1e293b]">
        <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
          {title}
        </span>
        <span className="text-[9px] font-mono text-[#475569] ml-2">
          {subtitle}
        </span>
      </div>
      <div className="p-3">
        {children}
      </div>
    </div>
  );
}

function EmptyState({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6">
      <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-[#3b82f6]/10 border border-[#3b82f6]/20 mb-4 text-[#3b82f6]">
        {icon}
      </div>
      <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">{title}</h3>
      <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md">{desc}</p>
    </div>
  );
}
