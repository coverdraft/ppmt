'use client';

import { useQuery, useMutation } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import { Slider } from '@/components/ui/slider';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Dice5,
  BarChart3,
  ChevronDown,
  ChevronRight,
  Loader2,
  TrendingUp,
  TrendingDown,
  Shield,
  AlertTriangle,
  DollarSign,
  Skull,
  Activity,
  Target,
  Settings2,
  ArrowRightLeft,
  Info,
} from 'lucide-react';
import { toast } from 'sonner';
import { useState, useMemo } from 'react';

// ============================================================
// TYPES
// ============================================================

interface PercentileEntry {
  level: number;
  value: number;
}

interface MonteCarloResult {
  tradeCount: number;
  totalOperationsFound: number;
  skippedOperations: number;
  config: {
    simulations: number;
    seed: number;
    initialCapital: number;
    ruinThreshold: number;
    confidenceLevels: number[];
  };
  equityPercentiles: PercentileEntry[];
  drawdownPercentiles: PercentileEntry[];
  sharpePercentiles: PercentileEntry[];
  winRatePercentiles: PercentileEntry[];
  profitFactorPercentiles: PercentileEntry[];
  probabilityOfProfit: number;
  riskOfRuin: number;
  p95MaxDrawdown: number;
  meanFinalEquity: number;
  medianFinalEquity: number;
  stdDevFinalEquity: number;
  originalMetrics: {
    finalEquity: number;
    maxDrawdown: number;
    sharpeRatio: number;
    winRate: number;
    profitFactor: number | null;
    hitRuin: boolean;
  };
  summary: string;
  generatedAt: string;
}

interface TradingSystem {
  id: string;
  name: string;
  category: string;
  isActive: boolean;
  backtestCount: number;
}

// ============================================================
// HELPERS
// ============================================================

function formatCurrency(v: number): string {
  if (v == null || isNaN(v)) return '$0.00';
  const sign = v < 0 ? '-' : '';
  const abs = Math.abs(v);
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

function formatPct(v: number): string {
  if (v == null || isNaN(v)) return '0.00%';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function profitColor(v: number): string {
  if (v > 0) return 'text-emerald-400';
  if (v < 0) return 'text-red-400';
  return 'text-[#94a3b8]';
}

// ============================================================
// DRAWDOWN BAR CHART (SVG)
// ============================================================

function DrawdownBarChart({ data }: { data: PercentileEntry[] }) {
  if (!data || data.length === 0) return null;

  const width = 320;
  const height = 100;
  const padding = { top: 10, right: 10, bottom: 22, left: 40 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const maxVal = Math.max(...data.map((d) => Math.abs(d.value)), 0.01);

  const barWidth = chartW / data.length - 8;

  return (
    <svg
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet"
    >
      {/* Grid lines */}
      {[0, 0.25, 0.5, 0.75, 1].map((pct) => (
        <line
          key={pct}
          x1={padding.left}
          y1={padding.top + chartH * pct}
          x2={padding.left + chartW}
          y2={padding.top + chartH * pct}
          stroke="#1e293b"
          strokeDasharray="3 3"
        />
      ))}

      {/* Y axis labels */}
      {[0, maxVal * 0.5, maxVal].map((v, i) => (
        <text
          key={i}
          x={padding.left - 4}
          y={padding.top + chartH - (v / maxVal) * chartH + 3}
          textAnchor="end"
          fill="#475569"
          fontSize="7"
          fontFamily="monospace"
        >
          {(v * 100).toFixed(0)}%
        </text>
      ))}

      {/* Bars */}
      {data.map((d, i) => {
        const barH = (Math.abs(d.value) / maxVal) * chartH;
        const x =
          padding.left + (i / data.length) * chartW + 4;
        const y = padding.top + chartH - barH;
        const intensity =
          Math.abs(d.value) / maxVal;
        const color =
          intensity > 0.7
            ? '#ef4444'
            : intensity > 0.4
              ? '#f59e0b'
              : '#10b981';

        return (
          <g key={i}>
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={barH}
              fill={color}
              opacity={0.8}
              rx={2}
            />
            {/* Label */}
            <text
              x={x + barWidth / 2}
              y={padding.top + chartH + 12}
              textAnchor="middle"
              fill="#64748b"
              fontSize="7"
              fontFamily="monospace"
            >
              P{d.level}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ============================================================
// EQUITY PERCENTILE TABLE
// ============================================================

function EquityPercentileTable({ data }: { data: PercentileEntry[] }) {
  if (!data || data.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px] font-mono">
        <thead>
          <tr className="border-b border-[#1e293b]">
            <th className="text-left py-1.5 px-2 text-[#64748b] uppercase tracking-wider">
              Percentile
            </th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">
              Equity
            </th>
          </tr>
        </thead>
        <tbody>
          {data.map((entry, i) => {
            const isMedian = entry.level === 50;
            return (
              <motion.tr
                key={entry.level}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.05 }}
                className={`border-b border-[#1e293b]/50 ${
                  isMedian ? 'bg-amber-500/5' : ''
                }`}
              >
                <td className="py-1.5 px-2 text-[#e2e8f0]">
                  <span className="flex items-center gap-1.5">
                    {isMedian && (
                      <span className="inline-block w-1 h-1 rounded-full bg-amber-400" />
                    )}
                    P{entry.level}
                  </span>
                </td>
                <td
                  className={`text-right py-1.5 px-2 font-bold ${profitColor(
                    entry.value
                  )}`}
                >
                  {formatCurrency(entry.value)}
                </td>
              </motion.tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================
// COMPARISON TABLE: ORIGINAL vs SIMULATED
// ============================================================

function ComparisonTable({
  original,
  result,
}: {
  original: MonteCarloResult['originalMetrics'];
  result: MonteCarloResult;
}) {
  // Get median simulated values from percentiles
  const medianSharpe =
    result.sharpePercentiles.find((p) => p.level === 50)?.value ?? 0;
  const medianWinRate =
    result.winRatePercentiles.find((p) => p.level === 50)?.value ?? 0;
  const medianPF =
    result.profitFactorPercentiles.find((p) => p.level === 50)?.value ?? 0;

  const rows = [
    {
      label: 'Final Equity',
      orig: formatCurrency(original.finalEquity),
      sim: formatCurrency(result.medianFinalEquity),
      origColor: profitColor(original.finalEquity),
      simColor: profitColor(result.medianFinalEquity),
    },
    {
      label: 'Max Drawdown',
      orig: `${(original.maxDrawdown * 100).toFixed(1)}%`,
      sim: `${(result.p95MaxDrawdown * 100).toFixed(1)}%`,
      origColor: 'text-red-400',
      simColor: 'text-red-400',
    },
    {
      label: 'Sharpe Ratio',
      orig: original.sharpeRatio.toFixed(3),
      sim: medianSharpe.toFixed(3),
      origColor:
        original.sharpeRatio >= 1
          ? 'text-emerald-400'
          : original.sharpeRatio >= 0
            ? 'text-amber-400'
            : 'text-red-400',
      simColor:
        medianSharpe >= 1
          ? 'text-emerald-400'
          : medianSharpe >= 0
            ? 'text-amber-400'
            : 'text-red-400',
    },
    {
      label: 'Win Rate',
      orig: `${(original.winRate * 100).toFixed(1)}%`,
      sim: `${(medianWinRate * 100).toFixed(1)}%`,
      origColor: original.winRate >= 0.5 ? 'text-emerald-400' : 'text-red-400',
      simColor: medianWinRate >= 0.5 ? 'text-emerald-400' : 'text-red-400',
    },
    {
      label: 'Profit Factor',
      orig:
        original.profitFactor === null
          ? '∞'
          : original.profitFactor.toFixed(2),
      sim: medianPF.toFixed(2),
      origColor:
        (original.profitFactor ?? 999) >= 1.5
          ? 'text-emerald-400'
          : (original.profitFactor ?? 0) >= 1
            ? 'text-amber-400'
            : 'text-red-400',
      simColor:
        medianPF >= 1.5
          ? 'text-emerald-400'
          : medianPF >= 1
            ? 'text-amber-400'
            : 'text-red-400',
    },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px] font-mono">
        <thead>
          <tr className="border-b border-[#1e293b]">
            <th className="text-left py-1.5 px-2 text-[#64748b] uppercase tracking-wider">
              Metric
            </th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">
              Original
            </th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">
              Simulated (P50)
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <motion.tr
              key={row.label}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.06 }}
              className="border-b border-[#1e293b]/50"
            >
              <td className="py-1.5 px-2 text-[#94a3b8]">{row.label}</td>
              <td className={`text-right py-1.5 px-2 font-bold ${row.origColor}`}>
                {row.orig}
              </td>
              <td className={`text-right py-1.5 px-2 font-bold ${row.simColor}`}>
                {row.sim}
              </td>
            </motion.tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function MonteCarloPanel() {
  // Config state
  const [selectedSystemId, setSelectedSystemId] = useState<string>('');
  const [simulations, setSimulations] = useState(1000);
  const [seed, setSeed] = useState(42);
  const [initialCapital, setInitialCapital] = useState(10000);
  const [ruinThreshold, setRuinThreshold] = useState(0.5);
  const [configOpen, setConfigOpen] = useState(true);
  const [summaryOpen, setSummaryOpen] = useState(false);

  // Fetch trading systems for the selector
  const { data: systemsData, isLoading: systemsLoading } = useQuery({
    queryKey: ['trading-systems-mc'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/trading-systems');
        if (!res.ok) return [];
        const json = await res.json();
        return (json.data ?? []) as TradingSystem[];
      } catch {
        return [];
      }
    },
    staleTime: 30000,
  });

  const systems = systemsData ?? [];

  // Monte Carlo mutation
  const mutation = useMutation({
    mutationFn: async () => {
      if (!selectedSystemId) {
        throw new Error('Please select a trading system first');
      }
      const res = await fetch('/api/risk/monte-carlo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          systemId: selectedSystemId,
          simulations,
          seed,
          initialCapital,
          confidenceLevels: [5, 25, 50, 75, 95],
          ruinThreshold,
        }),
      });
      const json = await res.json();
      if (!res.ok || json.error) {
        throw new Error(json.error || 'Simulation failed');
      }
      return json.data as MonteCarloResult;
    },
    onSuccess: () => {
      toast.success('Monte Carlo simulation completed');
    },
    onError: (error: Error) => {
      toast.error(`Simulation failed: ${error.message}`);
    },
  });

  const result = mutation.data ?? null;

  // Derived metric helpers
  const probOfProfitPct = result
    ? (result.probabilityOfProfit * 100).toFixed(1)
    : '0';
  const riskOfRuinPct = result
    ? (result.riskOfRuin * 100).toFixed(2)
    : '0';
  const p95DDPct = result ? (result.p95MaxDrawdown * 100).toFixed(1) : '0';

  // Risk color helpers
  const probColor = result
    ? result.probabilityOfProfit >= 0.7
      ? 'text-emerald-400'
      : result.probabilityOfProfit >= 0.4
        ? 'text-amber-400'
        : 'text-red-400'
    : 'text-[#94a3b8]';

  const ruinColor = result
    ? result.riskOfRuin > 0.05
      ? 'text-red-400'
      : result.riskOfRuin > 0.01
        ? 'text-amber-400'
        : 'text-emerald-400'
    : 'text-[#94a3b8]';

  const ruinBg = result
    ? result.riskOfRuin > 0.05
      ? 'bg-red-500/10 border-red-500/20'
      : result.riskOfRuin > 0.01
        ? 'bg-amber-500/10 border-amber-500/20'
        : 'bg-emerald-500/10 border-emerald-500/20'
    : 'bg-[#0d1117] border-[#1e293b]';

  // ==================== EMPTY STATE ====================
  if (!result && !mutation.isPending) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
              <Dice5 className="h-3.5 w-3.5 text-amber-400" />
            </div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
              Monte Carlo Simulation
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className={`h-6 text-[9px] font-mono px-2 ${configOpen ? 'text-amber-400 bg-amber-500/10' : 'text-[#94a3b8]'}`}
            onClick={() => setConfigOpen(!configOpen)}
          >
            <Settings2 className="h-3 w-3 mr-1" />
            Config
          </Button>
        </div>

        {/* Collapsible Config */}
        <Collapsible open={configOpen} onOpenChange={setConfigOpen}>
          <CollapsibleContent>
            <div className="px-3 py-3 bg-[#0d1117]/50 border-b border-[#1e293b]">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {/* System Selector */}
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Trading System
                  </label>
                  <Select
                    value={selectedSystemId}
                    onValueChange={setSelectedSystemId}
                  >
                    <SelectTrigger className="w-full h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue placeholder="Select system..." />
                    </SelectTrigger>
                    <SelectContent className="bg-[#111827] border-[#1e293b]">
                      {systemsLoading ? (
                        <SelectItem value="__loading" disabled>
                          Loading...
                        </SelectItem>
                      ) : systems.length === 0 ? (
                        <SelectItem value="__empty" disabled>
                          No systems found
                        </SelectItem>
                      ) : (
                        systems.map((s) => (
                          <SelectItem
                            key={s.id}
                            value={s.id}
                            className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#1e293b] focus:text-[#f1f5f9]"
                          >
                            {s.name}
                            <span className="text-[#475569] ml-1.5">
                              ({s.backtestCount} bt)
                            </span>
                          </SelectItem>
                        ))
                      )}
                    </SelectContent>
                  </Select>
                </div>

                {/* Simulations */}
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Simulations
                  </label>
                  <Input
                    type="number"
                    value={simulations}
                    onChange={(e) =>
                      setSimulations(
                        Math.max(1, Math.min(100000, Number(e.target.value) || 1))
                      )
                    }
                    className="h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]"
                    min={1}
                    max={100000}
                  />
                </div>

                {/* Seed */}
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Seed
                  </label>
                  <Input
                    type="number"
                    value={seed}
                    onChange={(e) => setSeed(Number(e.target.value) || 0)}
                    className="h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>

                {/* Initial Capital */}
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Initial Capital ($)
                  </label>
                  <Input
                    type="number"
                    value={initialCapital}
                    onChange={(e) =>
                      setInitialCapital(Math.max(1, Number(e.target.value) || 1))
                    }
                    className="h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]"
                    min={1}
                  />
                </div>

                {/* Ruin Threshold */}
                <div className="sm:col-span-2">
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
                      Ruin Threshold
                    </label>
                    <span className="text-[10px] font-mono text-amber-400 font-bold">
                      {(ruinThreshold * 100).toFixed(0)}%
                    </span>
                  </div>
                  <Slider
                    value={[ruinThreshold]}
                    onValueChange={([v]) => setRuinThreshold(v)}
                    min={0.01}
                    max={0.99}
                    step={0.01}
                    className="py-1"
                  />
                </div>
              </div>

              {/* Run Button */}
              <div className="mt-3 flex justify-end">
                <Button
                  onClick={() => mutation.mutate()}
                  disabled={!selectedSystemId || mutation.isPending}
                  className="h-7 text-[10px] font-mono font-bold bg-amber-500 hover:bg-amber-600 text-black px-4 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {mutation.isPending ? (
                    <>
                      <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                      Simulating...
                    </>
                  ) : (
                    <>
                      <Dice5 className="h-3 w-3 mr-1.5" />
                      Run Simulation
                    </>
                  )}
                </Button>
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>

        {/* Empty State Content */}
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.3 }}
            className="flex items-center justify-center w-14 h-14 rounded-xl bg-amber-500/10 border border-amber-500/20 mb-4"
          >
            <BarChart3 className="h-6 w-6 text-amber-400" />
          </motion.div>
          <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">
            Monte Carlo Simulation
          </h3>
          <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md leading-relaxed">
            Run Monte Carlo simulations to assess the robustness of your trading
            system&apos;s backtest results through trade order randomization.
            Select a trading system above and click Run Simulation to begin.
          </p>
        </div>
      </div>
    );
  }

  // ==================== LOADING STATE ====================
  if (mutation.isPending && !result) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        <div className="flex items-center gap-2.5 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <Dice5 className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
            Monte Carlo Simulation
          </span>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-amber-400 mb-3" />
          <span className="text-sm font-mono text-[#64748b]">
            Running {simulations.toLocaleString()} simulations...
          </span>
          <span className="text-[10px] font-mono text-[#475569] mt-1">
            Randomizing trade order for robustness analysis
          </span>
        </div>
      </div>
    );
  }

  // ==================== ERROR STATE ====================
  if (mutation.isError && !result) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        <div className="flex items-center gap-2.5 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <Dice5 className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
            Monte Carlo Simulation
          </span>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-red-500/10 border border-red-500/20 mb-4">
            <AlertTriangle className="h-6 w-6 text-red-400" />
          </div>
          <h3 className="text-sm font-mono font-bold text-red-400 mb-2">
            Simulation Failed
          </h3>
          <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md">
            {mutation.error?.message || 'An unexpected error occurred. Please try again.'}
          </p>
          <Button
            onClick={() => mutation.mutate()}
            className="mt-4 h-7 text-[10px] font-mono font-bold bg-amber-500 hover:bg-amber-600 text-black px-4"
          >
            <Dice5 className="h-3 w-3 mr-1.5" />
            Retry Simulation
          </Button>
        </div>
      </div>
    );
  }

  if (!result) return null;

  // ==================== RESULTS VIEW ====================
  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <Dice5 className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
              Monte Carlo Simulation
            </span>
            <span className="text-[9px] font-mono text-[#475569] ml-2">
              {result.config.simulations.toLocaleString()} paths ·{' '}
              {result.tradeCount} trades · seed {result.config.seed}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            className={`h-6 text-[9px] font-mono px-2 ${configOpen ? 'text-amber-400 bg-amber-500/10' : 'text-[#94a3b8]'}`}
            onClick={() => setConfigOpen(!configOpen)}
          >
            <Settings2 className="h-3 w-3 mr-1" />
            Config
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!selectedSystemId || mutation.isPending}
            className="h-6 text-[9px] font-mono font-bold bg-amber-500 hover:bg-amber-600 text-black px-3 disabled:opacity-50"
          >
            {mutation.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <>
                <Dice5 className="h-3 w-3 mr-1" />
                Re-run
              </>
            )}
          </Button>
        </div>
      </div>

      {/* ===== SCROLLABLE CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* ---- COLLAPSIBLE CONFIG ---- */}
        <Collapsible open={configOpen} onOpenChange={setConfigOpen}>
          <CollapsibleContent>
            <div className="px-3 py-3 bg-[#0d1117]/50 border-b border-[#1e293b]">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {/* System Selector */}
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Trading System
                  </label>
                  <Select
                    value={selectedSystemId}
                    onValueChange={setSelectedSystemId}
                  >
                    <SelectTrigger className="w-full h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue placeholder="Select system..." />
                    </SelectTrigger>
                    <SelectContent className="bg-[#111827] border-[#1e293b]">
                      {systemsLoading ? (
                        <SelectItem value="__loading" disabled>
                          Loading...
                        </SelectItem>
                      ) : systems.length === 0 ? (
                        <SelectItem value="__empty" disabled>
                          No systems found
                        </SelectItem>
                      ) : (
                        systems.map((s) => (
                          <SelectItem
                            key={s.id}
                            value={s.id}
                            className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#1e293b] focus:text-[#f1f5f9]"
                          >
                            {s.name}
                            <span className="text-[#475569] ml-1.5">
                              ({s.backtestCount} bt)
                            </span>
                          </SelectItem>
                        ))
                      )}
                    </SelectContent>
                  </Select>
                </div>

                {/* Simulations */}
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Simulations
                  </label>
                  <Input
                    type="number"
                    value={simulations}
                    onChange={(e) =>
                      setSimulations(
                        Math.max(
                          1,
                          Math.min(100000, Number(e.target.value) || 1)
                        )
                      )
                    }
                    className="h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]"
                    min={1}
                    max={100000}
                  />
                </div>

                {/* Seed */}
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Seed
                  </label>
                  <Input
                    type="number"
                    value={seed}
                    onChange={(e) => setSeed(Number(e.target.value) || 0)}
                    className="h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>

                {/* Initial Capital */}
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Initial Capital ($)
                  </label>
                  <Input
                    type="number"
                    value={initialCapital}
                    onChange={(e) =>
                      setInitialCapital(Math.max(1, Number(e.target.value) || 1))
                    }
                    className="h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]"
                    min={1}
                  />
                </div>

                {/* Ruin Threshold */}
                <div className="sm:col-span-2">
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
                      Ruin Threshold
                    </label>
                    <span className="text-[10px] font-mono text-amber-400 font-bold">
                      {(ruinThreshold * 100).toFixed(0)}%
                    </span>
                  </div>
                  <Slider
                    value={[ruinThreshold]}
                    onValueChange={([v]) => setRuinThreshold(v)}
                    min={0.01}
                    max={0.99}
                    step={0.01}
                    className="py-1"
                  />
                </div>
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>

        {/* ---- KEY RISK METRICS ROW ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
            {/* Probability of Profit */}
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.05 }}
            >
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <TrendingUp className="h-3 w-3 text-emerald-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    Prob. of Profit
                  </span>
                </div>
                <div className={`text-[18px] font-mono font-bold ${probColor}`}>
                  {probOfProfitPct}%
                </div>
                <div className="mt-1.5">
                  <div className="h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{
                        width: `${result.probabilityOfProfit * 100}%`,
                        backgroundColor:
                          result.probabilityOfProfit >= 0.7
                            ? '#10b981'
                            : result.probabilityOfProfit >= 0.4
                              ? '#f59e0b'
                              : '#ef4444',
                      }}
                    />
                  </div>
                </div>
              </Card>
            </motion.div>

            {/* Risk of Ruin */}
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
            >
              <Card className={`bg-[#0d1117] border-[#1e293b] p-3`}>
                <div className="flex items-center gap-1.5 mb-2">
                  <Skull className="h-3 w-3 text-red-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    Risk of Ruin
                  </span>
                </div>
                <div className={`text-[18px] font-mono font-bold ${ruinColor}`}>
                  {riskOfRuinPct}%
                </div>
                <div className="mt-1.5">
                  <Badge
                    className="text-[8px] h-4 px-1.5 font-mono"
                    style={{
                      backgroundColor:
                        result.riskOfRuin > 0.05
                          ? '#ef444420'
                          : result.riskOfRuin > 0.01
                            ? '#f59e0b20'
                            : '#10b98120',
                      color:
                        result.riskOfRuin > 0.05
                          ? '#ef4444'
                          : result.riskOfRuin > 0.01
                            ? '#f59e0b'
                            : '#10b981',
                      borderColor:
                        result.riskOfRuin > 0.05
                          ? '#ef444440'
                          : result.riskOfRuin > 0.01
                            ? '#f59e0b40'
                            : '#10b98140',
                    }}
                  >
                    {result.riskOfRuin > 0.05
                      ? 'HIGH RISK'
                      : result.riskOfRuin > 0.01
                        ? 'MODERATE'
                        : 'LOW RISK'}
                  </Badge>
                </div>
              </Card>
            </motion.div>

            {/* P95 Max Drawdown */}
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 }}
            >
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <TrendingDown className="h-3 w-3 text-red-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    P95 Max Drawdown
                  </span>
                </div>
                <div
                  className={`text-[18px] font-mono font-bold ${
                    result.p95MaxDrawdown > 0.3
                      ? 'text-red-400'
                      : result.p95MaxDrawdown > 0.15
                        ? 'text-amber-400'
                        : 'text-emerald-400'
                  }`}
                >
                  {p95DDPct}%
                </div>
                <div className="mt-1.5">
                  <div className="h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{
                        width: `${Math.min(result.p95MaxDrawdown * 100, 100)}%`,
                        backgroundColor:
                          result.p95MaxDrawdown > 0.3
                            ? '#ef4444'
                            : result.p95MaxDrawdown > 0.15
                              ? '#f59e0b'
                              : '#10b981',
                      }}
                    />
                  </div>
                </div>
              </Card>
            </motion.div>

            {/* Mean Final Equity */}
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
            >
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <DollarSign className="h-3 w-3 text-amber-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                    Mean Final Equity
                  </span>
                </div>
                <div
                  className={`text-[18px] font-mono font-bold ${profitColor(
                    result.meanFinalEquity - initialCapital
                  )}`}
                >
                  {formatCurrency(result.meanFinalEquity)}
                </div>
                <div className="mt-1.5 flex items-center gap-2">
                  <span className="text-[8px] font-mono text-[#475569]">
                    Median: {formatCurrency(result.medianFinalEquity)}
                  </span>
                  <span className="text-[8px] font-mono text-[#475569]">
                    σ {formatCurrency(result.stdDevFinalEquity)}
                  </span>
                </div>
              </Card>
            </motion.div>
          </div>
        </div>

        {/* ---- EQUITY PERCENTILES + DRAWDOWN DISTRIBUTION ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* Equity Percentiles Table */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <Target className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Equity Confidence Intervals
                </span>
              </div>
              <EquityPercentileTable data={result.equityPercentiles} />
            </Card>

            {/* Drawdown Distribution */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <Activity className="h-3.5 w-3.5 text-red-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Drawdown Distribution
                </span>
              </div>
              <DrawdownBarChart data={result.drawdownPercentiles} />
              <div className="mt-2 flex items-center gap-3">
                {result.drawdownPercentiles.map((d) => (
                  <div key={d.level} className="flex items-center gap-1">
                    <span className="text-[8px] font-mono text-[#475569]">
                      P{d.level}:
                    </span>
                    <span className="text-[9px] font-mono font-bold text-red-400">
                      {(d.value * 100).toFixed(1)}%
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>

        {/* ---- ORIGINAL vs SIMULATED COMPARISON ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <Card className="bg-[#0d1117] border-[#1e293b] p-4">
            <div className="flex items-center gap-2 mb-3">
              <ArrowRightLeft className="h-3.5 w-3.5 text-cyan-400" />
              <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                Original vs Simulated (Median)
              </span>
            </div>
            <ComparisonTable original={result.originalMetrics} result={result} />
            {result.originalMetrics.hitRuin && (
              <div className="mt-3 flex items-center gap-1.5 px-2 py-1.5 bg-red-500/5 border border-red-500/20 rounded">
                <AlertTriangle className="h-3 w-3 text-red-400 shrink-0" />
                <span className="text-[8px] font-mono text-red-400">
                  Original backtest path hit the ruin threshold — results may be
                  unreliable
                </span>
              </div>
            )}
          </Card>
        </div>

        {/* ---- ADDITIONAL METRICS ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* Sharpe Percentiles */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <BarChart3 className="h-3.5 w-3.5 text-emerald-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Sharpe Ratio Distribution
                </span>
              </div>
              <div className="grid grid-cols-5 gap-2">
                {result.sharpePercentiles.map((p) => (
                  <div key={p.level} className="text-center">
                    <span className="text-[8px] font-mono text-[#475569] block">
                      P{p.level}
                    </span>
                    <span
                      className={`text-[11px] font-mono font-bold ${
                        p.value >= 1
                          ? 'text-emerald-400'
                          : p.value >= 0
                            ? 'text-amber-400'
                            : 'text-red-400'
                      }`}
                    >
                      {p.value.toFixed(2)}
                    </span>
                  </div>
                ))}
              </div>
            </Card>

            {/* Win Rate & PF Percentiles */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <Shield className="h-3.5 w-3.5 text-[#d4af37]" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Win Rate / Profit Factor
                </span>
              </div>
              <div className="grid grid-cols-2 gap-4">
                {/* Win Rate */}
                <div>
                  <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider block mb-2">
                    Win Rate
                  </span>
                  {result.winRatePercentiles.map((p) => (
                    <div
                      key={p.level}
                      className="flex items-center justify-between py-0.5"
                    >
                      <span className="text-[9px] font-mono text-[#64748b]">
                        P{p.level}
                      </span>
                      <span
                        className={`text-[10px] font-mono font-bold ${
                          p.value >= 0.5
                            ? 'text-emerald-400'
                            : 'text-red-400'
                        }`}
                      >
                        {(p.value * 100).toFixed(1)}%
                      </span>
                    </div>
                  ))}
                </div>
                {/* Profit Factor */}
                <div>
                  <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider block mb-2">
                    Profit Factor
                  </span>
                  {result.profitFactorPercentiles.map((p) => (
                    <div
                      key={p.level}
                      className="flex items-center justify-between py-0.5"
                    >
                      <span className="text-[9px] font-mono text-[#64748b]">
                        P{p.level}
                      </span>
                      <span
                        className={`text-[10px] font-mono font-bold ${
                          p.value >= 1.5
                            ? 'text-emerald-400'
                            : p.value >= 1
                              ? 'text-amber-400'
                              : 'text-red-400'
                        }`}
                      >
                        {p.value.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </Card>
          </div>
        </div>

        {/* ---- SUMMARY ---- */}
        <div className="px-3 py-3">
          <Collapsible open={summaryOpen} onOpenChange={setSummaryOpen}>
            <CollapsibleTrigger asChild>
              <button className="flex items-center gap-2 w-full text-left">
                {summaryOpen ? (
                  <ChevronDown className="h-3 w-3 text-[#64748b]" />
                ) : (
                  <ChevronRight className="h-3 w-3 text-[#64748b]" />
                )}
                <Info className="h-3 w-3 text-[#64748b]" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Simulation Summary
                </span>
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <Card className="bg-[#0d1117] border-[#1e293b] p-4 mt-2">
                <pre className="text-[10px] font-mono text-[#94a3b8] whitespace-pre-wrap leading-relaxed">
                  {result.summary}
                </pre>
              </Card>
            </CollapsibleContent>
          </Collapsible>

          {/* Data info footer */}
          <div className="mt-3 flex items-center gap-3 text-[8px] font-mono text-[#475569]">
            <span>
              Operations: {result.totalOperationsFound} found ·{' '}
              {result.skippedOperations} skipped
            </span>
            <Separator orientation="vertical" className="h-3 bg-[#1e293b]" />
            <span>
              Ruin threshold: {(result.config.ruinThreshold * 100).toFixed(0)}%
            </span>
            <Separator orientation="vertical" className="h-3 bg-[#1e293b]" />
            <span>
              Capital: {formatCurrency(result.config.initialCapital)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
