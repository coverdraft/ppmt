'use client';

import { useState, useMemo } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
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
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Layers,
  GitBranch,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  HelpCircle,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Activity,
  Target,
  ChevronDown,
  ChevronRight,
  Loader2,
  Play,
  ArrowDownRight,
  Route,
  Gauge,
  ShieldCheck,
  ShieldAlert,
  ShieldX,
} from 'lucide-react';
import { toast } from 'sonner';
import { formatCurrency, formatPct } from '@/lib/format';

// ============================================================
// TYPES
// ============================================================

type Recommendation = 'ROBUST' | 'MARGINAL' | 'OVERFIT' | 'INSUFFICIENT_DATA';

interface WFWindow {
  windowIndex: number;
  trainStart: string;
  trainEnd: string;
  testStart: string;
  testEnd: string;
  degradationPct: number;
  wfe: number;
  inSampleReturn: number | null;
  outOfSampleReturn: number | null;
  inSampleTrades: number;
  outOfSampleTrades: number;
}

interface WFAData {
  id: string;
  systemName: string;
  recommendation: Recommendation;
  isRobust: boolean;
  aggregateWFE: number;
  avgInSampleReturn: number;
  avgOutOfSampleReturn: number;
  performanceConsistency: number;
  overallDegradation: number;
  parameterStability: number;
  windows: WFWindow[];
  summary: string;
  tokensAnalyzed: number;
}

interface TradingSystemItem {
  id: string;
  name: string;
  category: string;
  icon: string;
  primaryTimeframe: string;
}

interface WFAConfig {
  systemName: string;
  startDate: string;
  endDate: string;
  windowCount: number;
  trainRatio: number;
  initialCapital: number;
  minWFE: number;
  anchored: boolean;
  chain: string;
}

// ============================================================
// HELPERS
// ============================================================

function fmtCurrency(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '$0.00';
  return formatCurrency(v);
}

function fmtPct(v: number | null | undefined, digits: number = 2): string {
  if (v == null || isNaN(v)) return '0.00%';
  return formatPct(v, digits);
}

function fmtDate(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return dateStr;
  }
}

function fmtDateRange(start: string, end: string): string {
  return `${fmtDate(start)} - ${fmtDate(end)}`;
}

function wfeColor(wfe: number): string {
  if (wfe >= 0.7) return 'text-emerald-400';
  if (wfe >= 0.5) return 'text-amber-400';
  if (wfe >= 0.3) return 'text-orange-400';
  return 'text-red-400';
}

function wfeBg(wfe: number): string {
  if (wfe >= 0.7) return 'bg-emerald-500/10 border-emerald-500/30';
  if (wfe >= 0.5) return 'bg-amber-500/10 border-amber-500/30';
  if (wfe >= 0.3) return 'bg-orange-500/10 border-orange-500/30';
  return 'bg-red-500/10 border-red-500/30';
}

function degradationColor(pct: number): string {
  if (pct <= 20) return 'text-emerald-400';
  if (pct <= 40) return 'text-amber-400';
  if (pct <= 60) return 'text-orange-400';
  return 'text-red-400';
}

function pnlColor(v: number): string {
  if (v > 0) return 'text-emerald-400';
  if (v < 0) return 'text-red-400';
  return 'text-[#94a3b8]';
}

// ============================================================
// VERDICT BANNER
// ============================================================

function VerdictBanner({ recommendation }: { recommendation: Recommendation }) {
  const config: Record<
    Recommendation,
    { bg: string; border: string; icon: React.ElementType; color: string; label: string; desc: string }
  > = {
    ROBUST: {
      bg: 'bg-emerald-500/10',
      border: 'border-emerald-500/30',
      icon: ShieldCheck,
      color: 'text-emerald-400',
      label: 'ROBUST',
      desc: 'System validates well out-of-sample',
    },
    MARGINAL: {
      bg: 'bg-amber-500/10',
      border: 'border-amber-500/30',
      icon: AlertTriangle,
      color: 'text-amber-400',
      label: 'MARGINAL',
      desc: 'System shows some degradation',
    },
    OVERFIT: {
      bg: 'bg-red-500/10',
      border: 'border-red-500/30',
      icon: ShieldX,
      color: 'text-red-400',
      label: 'OVERFIT',
      desc: 'System fails out-of-sample validation',
    },
    INSUFFICIENT_DATA: {
      bg: 'bg-gray-500/10',
      border: 'border-gray-500/30',
      icon: HelpCircle,
      color: 'text-gray-400',
      label: 'INSUFFICIENT DATA',
      desc: 'Not enough trades to assess',
    },
  };

  const c = config[recommendation] || config.INSUFFICIENT_DATA;
  const Icon = c.icon;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.3 }}
      className={`${c.bg} border ${c.border} rounded-lg px-4 py-3 flex items-center gap-3`}
    >
      <div className={`flex items-center justify-center w-10 h-10 rounded-lg ${c.bg} border ${c.border}`}>
        <Icon className={`h-5 w-5 ${c.color}`} />
      </div>
      <div>
        <span className={`text-[14px] font-mono font-bold ${c.color} tracking-wider`}>{c.label}</span>
        <p className="text-[10px] font-mono text-[#94a3b8] mt-0.5">{c.desc}</p>
      </div>
    </motion.div>
  );
}

// ============================================================
// METRIC CARD
// ============================================================

function MetricCard({
  icon: Icon,
  iconColor,
  label,
  value,
  subValue,
  subColor,
}: {
  icon: React.ElementType;
  iconColor: string;
  label: string;
  value: string;
  subValue?: string;
  subColor?: string;
}) {
  return (
    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
      <div className="flex items-center gap-1.5 mb-2">
        <Icon className={`h-3 w-3 ${iconColor}`} />
        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-[16px] font-mono font-bold text-[#e2e8f0]">{value}</div>
      {subValue && (
        <div className={`text-[10px] font-mono mt-0.5 ${subColor || 'text-[#475569]'}`}>{subValue}</div>
      )}
    </Card>
  );
}

// ============================================================
// DEGRADATION BAR
// ============================================================

function DegradationBar({
  isReturn,
  oosReturn,
  degradationPct,
}: {
  isReturn: number;
  oosReturn: number;
  degradationPct: number;
}) {
  const maxReturn = Math.max(Math.abs(isReturn), Math.abs(oosReturn), 1);
  const isWidth = Math.min(Math.abs(isReturn) / maxReturn, 1) * 100;
  const oosWidth = Math.min(Math.abs(oosReturn) / maxReturn, 1) * 100;

  return (
    <div className="space-y-1.5">
      {/* In-Sample */}
      <div className="flex items-center gap-2">
        <span className="text-[9px] font-mono text-[#64748b] w-16 shrink-0">In-Sample</span>
        <div className="flex-1 h-3 bg-[#1e293b] rounded-full overflow-hidden relative">
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${isWidth}%` }}
            transition={{ duration: 0.6, ease: 'easeOut' }}
            className={`h-full rounded-full ${isReturn >= 0 ? 'bg-emerald-500' : 'bg-red-500'}`}
          />
        </div>
        <span className={`text-[10px] font-mono font-bold w-16 text-right ${pnlColor(isReturn)}`}>
          {fmtPct(isReturn)}
        </span>
      </div>

      {/* Out-of-Sample */}
      <div className="flex items-center gap-2">
        <span className="text-[9px] font-mono text-[#64748b] w-16 shrink-0">Out-of-Sample</span>
        <div className="flex-1 h-3 bg-[#1e293b] rounded-full overflow-hidden relative">
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${oosWidth}%` }}
            transition={{ duration: 0.6, ease: 'easeOut', delay: 0.15 }}
            className={`h-full rounded-full ${oosReturn >= 0 ? 'bg-emerald-500/70' : 'bg-red-500/70'}`}
          />
        </div>
        <span className={`text-[10px] font-mono font-bold w-16 text-right ${pnlColor(oosReturn)}`}>
          {fmtPct(oosReturn)}
        </span>
      </div>

      {/* Degradation indicator */}
      <div className="flex items-center gap-2">
        <span className="text-[9px] font-mono text-[#64748b] w-16 shrink-0">Degradation</span>
        <div className="flex-1 flex items-center gap-1.5">
          <ArrowDownRight className={`h-3 w-3 ${degradationColor(degradationPct)}`} />
          <span className={`text-[10px] font-mono font-bold ${degradationColor(degradationPct)}`}>
            -{degradationPct.toFixed(1)}%
          </span>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// WFE TREND CHART (SVG)
// ============================================================

function WFETrendChart({ windows }: { windows: WFWindow[] }) {
  if (windows.length < 2) return null;

  const width = 360;
  const height = 100;
  const padding = { top: 12, right: 12, bottom: 22, left: 35 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const wfeValues = windows.map((w) => w.wfe * 100);
  const minVal = Math.min(...wfeValues, 0);
  const maxVal = Math.max(...wfeValues, 100);
  const range = maxVal - minVal || 1;

  const scaleX = (i: number) => padding.left + (i / (windows.length - 1)) * chartW;
  const scaleY = (v: number) => padding.top + chartH - ((v - minVal) / range) * chartH;

  const linePath = wfeValues
    .map((v, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(i).toFixed(1)} ${scaleY(v).toFixed(1)}`)
    .join(' ');

  const areaPath = `${linePath} L ${scaleX(windows.length - 1).toFixed(1)} ${padding.top + chartH} L ${padding.left} ${padding.top + chartH} Z`;

  // Threshold line at 50%
  const threshold50Y = scaleY(50);

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
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

      {/* 50% WFE threshold */}
      {threshold50Y >= padding.top && threshold50Y <= padding.top + chartH && (
        <>
          <line
            x1={padding.left}
            y1={threshold50Y}
            x2={padding.left + chartW}
            y2={threshold50Y}
            stroke="#f59e0b"
            strokeDasharray="4 4"
            opacity={0.4}
          />
          <text
            x={padding.left - 4}
            y={threshold50Y + 3}
            textAnchor="end"
            fill="#f59e0b"
            fontSize="7"
            fontFamily="monospace"
            opacity={0.6}
          >
            50%
          </text>
        </>
      )}

      {/* Area fill */}
      <defs>
        <linearGradient id="wfeAreaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.2} />
          <stop offset="100%" stopColor="#f59e0b" stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#wfeAreaGrad)" />

      {/* Line */}
      <path d={linePath} fill="none" stroke="#f59e0b" strokeWidth={1.5} />

      {/* Data points */}
      {wfeValues.map((v, i) => {
        const color = v >= 70 ? '#10b981' : v >= 50 ? '#f59e0b' : v >= 30 ? '#f97316' : '#ef4444';
        return (
          <g key={i}>
            <circle cx={scaleX(i)} cy={scaleY(v)} r={3.5} fill={color} stroke="#0a0e17" strokeWidth={1.5} />
            <text
              x={scaleX(i)}
              y={scaleY(v) - 7}
              textAnchor="middle"
              fill="#e2e8f0"
              fontSize="7"
              fontFamily="monospace"
              fontWeight="bold"
            >
              {v.toFixed(0)}%
            </text>
          </g>
        );
      })}

      {/* X axis labels */}
      {windows.map((w, i) => (
        <text
          key={i}
          x={scaleX(i)}
          y={padding.top + chartH + 14}
          textAnchor="middle"
          fill="#475569"
          fontSize="7"
          fontFamily="monospace"
        >
          W{w.windowIndex + 1}
        </text>
      ))}

      {/* Y axis labels */}
      {[minVal, (minVal + maxVal) / 2, maxVal].map((v, i) => (
        <text
          key={i}
          x={padding.left - 4}
          y={scaleY(v) + 3}
          textAnchor="end"
          fill="#475569"
          fontSize="7"
          fontFamily="monospace"
        >
          {v.toFixed(0)}%
        </text>
      ))}
    </svg>
  );
}

// ============================================================
// WINDOW DETAILS TABLE
// ============================================================

function WindowTable({ windows }: { windows: WFWindow[] }) {
  return (
    <div className="overflow-x-auto" style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
      <table className="w-full text-[9px] font-mono">
        <thead>
          <tr className="text-[#475569] uppercase border-b border-[#1e293b]/50">
            <th className="py-1.5 px-2 text-left">Window</th>
            <th className="py-1.5 px-2 text-left">Train Period</th>
            <th className="py-1.5 px-2 text-left">Test Period</th>
            <th className="py-1.5 px-2 text-right">IS Return</th>
            <th className="py-1.5 px-2 text-right">OOS Return</th>
            <th className="py-1.5 px-2 text-center">WFE</th>
            <th className="py-1.5 px-2 text-right">Degradation</th>
            <th className="py-1.5 px-2 text-center">Trades</th>
          </tr>
        </thead>
        <tbody>
          {windows.map((w) => {
            const isReturn = w.inSampleReturn ?? 0;
            const oosReturn = w.outOfSampleReturn ?? 0;

            return (
              <motion.tr
                key={w.windowIndex}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: w.windowIndex * 0.05 }}
                className="border-b border-[#1e293b]/30 hover:bg-[#0a0e17]/50"
              >
                <td className="py-1.5 px-2 text-[#e2e8f0] font-bold">W{w.windowIndex + 1}</td>
                <td className="py-1.5 px-2 text-[#94a3b8]">{fmtDateRange(w.trainStart, w.trainEnd)}</td>
                <td className="py-1.5 px-2 text-[#94a3b8]">{fmtDateRange(w.testStart, w.testEnd)}</td>
                <td className={`py-1.5 px-2 text-right font-bold ${pnlColor(isReturn)}`}>
                  {fmtPct(isReturn)}
                </td>
                <td className={`py-1.5 px-2 text-right font-bold ${pnlColor(oosReturn)}`}>
                  {fmtPct(oosReturn)}
                </td>
                <td className="py-1.5 px-2 text-center">
                  <Badge
                    className={`text-[8px] h-4 px-1.5 font-mono border ${wfeBg(w.wfe)} ${wfeColor(w.wfe)}`}
                  >
                    {(w.wfe * 100).toFixed(0)}%
                  </Badge>
                </td>
                <td className={`py-1.5 px-2 text-right font-bold ${degradationColor(w.degradationPct)}`}>
                  -{w.degradationPct.toFixed(1)}%
                </td>
                <td className="py-1.5 px-2 text-center text-[#94a3b8]">
                  <span className="text-emerald-400">{w.inSampleTrades}</span>
                  <span className="text-[#475569]">/</span>
                  <span className="text-amber-400">{w.outOfSampleTrades}</span>
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
// MAIN COMPONENT
// ============================================================

export default function WalkForwardPanel() {
  // Config state
  const [config, setConfig] = useState<WFAConfig>({
    systemName: '',
    startDate: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
    endDate: new Date().toISOString().slice(0, 10),
    windowCount: 5,
    trainRatio: 0.7,
    initialCapital: 10,
    minWFE: 0.5,
    anchored: false,
    chain: 'SOL',
  });

  const [configOpen, setConfigOpen] = useState(true);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [result, setResult] = useState<WFAData | null>(null);

  // Fetch trading systems for the system selector
  const { data: tradingSystems = [] } = useQuery({
    queryKey: ['trading-systems-wf'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/trading-systems');
        if (!res.ok) return [];
        const json = await res.json();
        return (json.data ?? []) as TradingSystemItem[];
      } catch {
        return [];
      }
    },
    staleTime: 30000,
  });

  // Run Walk-Forward Analysis mutation
  const runMutation = useMutation({
    mutationFn: async (cfg: WFAConfig) => {
      const res = await fetch('/api/backtest/walk-forward', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      });
      const json = await res.json();
      if (!res.ok) {
        throw new Error(json.error || 'Failed to run Walk-Forward Analysis');
      }
      return json.data as WFAData;
    },
    onSuccess: (data) => {
      setResult(data);
      setConfigOpen(false);
      const recMessages: Record<Recommendation, string> = {
        ROBUST: 'System validated as robust!',
        MARGINAL: 'System shows marginal performance.',
        OVERFIT: 'Warning: System appears overfit!',
        INSUFFICIENT_DATA: 'Insufficient data for analysis.',
      };
      toast.success(recMessages[data.recommendation] || 'Analysis complete');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to run Walk-Forward Analysis');
    },
  });

  // Handlers
  const handleRun = () => {
    if (!config.systemName) {
      toast.error('Please select a trading system');
      return;
    }
    if (config.startDate >= config.endDate) {
      toast.error('Start date must be before end date');
      return;
    }
    runMutation.mutate(config);
  };

  const updateConfig = <K extends keyof WFAConfig>(key: K, value: WFAConfig[K]) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  // Derived values
  const hasResult = result !== null;

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <GitBranch className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Walk-Forward Analysis</span>
            {hasResult && (
              <span className="text-[9px] font-mono text-[#475569] ml-2">
                {result.systemName} &middot; {result.tokensAnalyzed} tokens
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {hasResult && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-[9px] font-mono px-2 text-amber-400 bg-amber-500/10 hover:bg-amber-500/20"
              onClick={() => setConfigOpen(!configOpen)}
            >
              <Route className="h-3 w-3 mr-1" />
              Reconfigure
            </Button>
          )}
        </div>
      </div>

      {/* ===== SCROLLABLE CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* ---- CONFIGURATION PANEL ---- */}
        <Collapsible open={configOpen} onOpenChange={setConfigOpen}>
          <CollapsibleTrigger asChild>
            <button className="w-full flex items-center justify-between px-4 py-1.5 bg-[#0d1117]/50 border-b border-[#1e293b]/50 hover:bg-[#0d1117] transition-colors">
              <div className="flex items-center gap-2">
                <Gauge className="h-3 w-3 text-[#64748b]" />
                <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider">
                  Configuration
                </span>
              </div>
              {configOpen ? (
                <ChevronDown className="h-3 w-3 text-[#475569]" />
              ) : (
                <ChevronRight className="h-3 w-3 text-[#475569]" />
              )}
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="px-4 py-3 border-b border-[#1e293b] bg-[#0d1117]/30">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {/* System Name / Select */}
                <div className="sm:col-span-2 lg:col-span-3">
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Trading System
                  </Label>
                  <Select
                    value={config.systemName}
                    onValueChange={(v) => updateConfig('systemName', v)}
                  >
                    <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue placeholder="Select a system..." />
                    </SelectTrigger>
                    <SelectContent className="bg-[#111827] border-[#1e293b]">
                      {tradingSystems.length > 0 ? (
                        tradingSystems.map((sys) => (
                          <SelectItem
                            key={sys.id}
                            value={sys.name}
                            className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#1e293b] focus:text-[#f1f5f9]"
                          >
                            <span className="mr-1.5">{sys.icon}</span>
                            {sys.name}
                            <span className="text-[#475569] ml-1.5">({sys.primaryTimeframe})</span>
                          </SelectItem>
                        ))
                      ) : (
                        <SelectItem value="__none" disabled className="text-[10px] font-mono text-[#475569]">
                          No systems available
                        </SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                  {/* Or enter manually */}
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="text-[8px] font-mono text-[#475569]">Or enter manually:</span>
                    <Input
                      value={config.systemName}
                      onChange={(e) => updateConfig('systemName', e.target.value)}
                      placeholder="System name..."
                      className="h-6 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] placeholder-[#475569]"
                    />
                  </div>
                </div>

                {/* Start Date */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Start Date
                  </Label>
                  <Input
                    type="date"
                    value={config.startDate}
                    onChange={(e) => updateConfig('startDate', e.target.value)}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>

                {/* End Date */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    End Date
                  </Label>
                  <Input
                    type="date"
                    value={config.endDate}
                    onChange={(e) => updateConfig('endDate', e.target.value)}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>

                {/* Chain */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Chain
                  </Label>
                  <Select
                    value={config.chain}
                    onValueChange={(v) => updateConfig('chain', v)}
                  >
                    <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-[#111827] border-[#1e293b]">
                      <SelectItem value="SOL" className="text-[10px] font-mono text-[#e2e8f0]">SOL</SelectItem>
                      <SelectItem value="ETH" className="text-[10px] font-mono text-[#e2e8f0]">ETH</SelectItem>
                      <SelectItem value="BASE" className="text-[10px] font-mono text-[#e2e8f0]">BASE</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Window Count */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Windows
                  </Label>
                  <Input
                    type="number"
                    min={2}
                    max={20}
                    value={config.windowCount}
                    onChange={(e) => updateConfig('windowCount', Math.max(2, Math.min(20, parseInt(e.target.value) || 5)))}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>

                {/* Initial Capital */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Initial Capital ($)
                  </Label>
                  <Input
                    type="number"
                    min={1}
                    step={1}
                    value={config.initialCapital}
                    onChange={(e) => updateConfig('initialCapital', Math.max(1, parseFloat(e.target.value) || 10))}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>

                {/* Min WFE */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Min WFE
                  </Label>
                  <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={config.minWFE}
                    onChange={(e) => updateConfig('minWFE', Math.max(0, Math.min(1, parseFloat(e.target.value) || 0.5)))}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>

                {/* Train Ratio Slider */}
                <div className="sm:col-span-2">
                  <div className="flex items-center justify-between mb-1">
                    <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
                      Train Ratio
                    </Label>
                    <span className="text-[10px] font-mono text-amber-400 font-bold">
                      {(config.trainRatio * 100).toFixed(0)}%
                    </span>
                  </div>
                  <Slider
                    value={[config.trainRatio]}
                    onValueChange={([v]) => updateConfig('trainRatio', v)}
                    min={0.5}
                    max={0.9}
                    step={0.05}
                    className="py-1"
                  />
                  <div className="flex justify-between mt-0.5">
                    <span className="text-[8px] font-mono text-[#475569]">50% Train</span>
                    <span className="text-[8px] font-mono text-[#475569]">90% Train</span>
                  </div>
                </div>

                {/* Anchored Toggle */}
                <div className="flex items-center gap-3">
                  <Switch
                    checked={config.anchored}
                    onCheckedChange={(v) => updateConfig('anchored', v)}
                    className="data-[state=checked]:bg-amber-500"
                  />
                  <div>
                    <span className="text-[10px] font-mono text-[#e2e8f0]">Anchored WFA</span>
                    <p className="text-[8px] font-mono text-[#475569]">
                      {config.anchored ? 'Training starts from beginning each window' : 'Training window slides forward (rolling)'}
                    </p>
                  </div>
                </div>
              </div>

              {/* Run Button */}
              <div className="mt-4 flex items-center gap-3">
                <Button
                  onClick={handleRun}
                  disabled={runMutation.isPending || !config.systemName}
                  className="h-8 px-4 text-[10px] font-mono bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 border border-amber-500/30 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {runMutation.isPending ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                      Analyzing...
                    </>
                  ) : (
                    <>
                      <Play className="h-3.5 w-3.5 mr-1.5" />
                      Run Analysis
                    </>
                  )}
                </Button>
                {!config.systemName && (
                  <span className="text-[9px] font-mono text-[#475569]">Select a system first</span>
                )}
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>

        {/* ---- EMPTY STATE ---- */}
        <AnimatePresence mode="wait">
          {!hasResult && !runMutation.isPending && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex-1 flex flex-col items-center justify-center py-16 px-6"
            >
              <div className="flex items-center justify-center w-16 h-16 rounded-xl bg-amber-500/10 border border-amber-500/20 mb-4">
                <Layers className="h-7 w-7 text-amber-400" />
              </div>
              <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">Walk-Forward Analysis</h3>
              <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md leading-relaxed">
                Validate your trading system&apos;s robustness by testing it on out-of-sample data
                across multiple time windows. Prevent overfitting before going live.
              </p>
              <div className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-3 max-w-lg">
                <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3 text-center">
                  <ShieldCheck className="h-5 w-5 text-emerald-400 mx-auto mb-1.5" />
                  <span className="text-[9px] font-mono text-[#94a3b8]">Detect Overfitting</span>
                </div>
                <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3 text-center">
                  <Target className="h-5 w-5 text-amber-400 mx-auto mb-1.5" />
                  <span className="text-[9px] font-mono text-[#94a3b8]">Validate Robustness</span>
                </div>
                <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3 text-center">
                  <Route className="h-5 w-5 text-cyan-400 mx-auto mb-1.5" />
                  <span className="text-[9px] font-mono text-[#94a3b8]">Multi-Window Test</span>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ---- LOADING STATE ---- */}
        {runMutation.isPending && !hasResult && (
          <div className="flex-1 flex flex-col items-center justify-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-amber-400 mb-3" />
            <span className="text-sm font-mono text-[#94a3b8]">Running Walk-Forward Analysis...</span>
            <span className="text-[10px] font-mono text-[#475569] mt-1">
              {config.windowCount} windows &middot; {config.trainRatio * 100}% train ratio
            </span>
          </div>
        )}

        {/* ---- RESULTS SECTION ---- */}
        {hasResult && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
          >
            {/* Verdict Banner */}
            <div className="px-4 py-3 border-b border-[#1e293b]">
              <VerdictBanner recommendation={result.recommendation} />
            </div>

            {/* Key Metrics Row */}
            <div className="px-4 py-3 border-b border-[#1e293b]">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                {/* Aggregate WFE */}
                <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                  <div className="flex items-center gap-1.5 mb-2">
                    <Activity className={`h-3 w-3 ${wfeColor(result.aggregateWFE)}`} />
                    <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Aggregate WFE</span>
                  </div>
                  <div className={`text-[16px] font-mono font-bold ${wfeColor(result.aggregateWFE)}`}>
                    {(result.aggregateWFE * 100).toFixed(1)}%
                  </div>
                  <div className="mt-1.5">
                    <div className="h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${Math.min(result.aggregateWFE * 100, 100)}%` }}
                        transition={{ duration: 0.8, ease: 'easeOut' }}
                        className={`h-full rounded-full ${
                          result.aggregateWFE >= 0.7
                            ? 'bg-emerald-500'
                            : result.aggregateWFE >= 0.5
                            ? 'bg-amber-500'
                            : result.aggregateWFE >= 0.3
                            ? 'bg-orange-500'
                            : 'bg-red-500'
                        }`}
                      />
                    </div>
                  </div>
                </Card>

                {/* Avg In-Sample Return */}
                <MetricCard
                  icon={TrendingUp}
                  iconColor="text-cyan-400"
                  label="Avg In-Sample Return"
                  value={fmtPct(result.avgInSampleReturn)}
                  subColor={pnlColor(result.avgInSampleReturn)}
                />

                {/* Avg Out-of-Sample Return */}
                <MetricCard
                  icon={BarChart3}
                  iconColor={pnlColor(result.avgOutOfSampleReturn)}
                  label="Avg Out-of-Sample Return"
                  value={fmtPct(result.avgOutOfSampleReturn)}
                  subColor={pnlColor(result.avgOutOfSampleReturn)}
                />

                {/* Parameter Stability */}
                <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                  <div className="flex items-center gap-1.5 mb-2">
                    <Target className="h-3 w-3 text-amber-400" />
                    <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                      Parameter Stability
                    </span>
                  </div>
                  <div className="text-[16px] font-mono font-bold text-[#e2e8f0]">
                    {(result.parameterStability * 100).toFixed(1)}%
                  </div>
                  <div className="mt-1.5">
                    <Progress
                      value={result.parameterStability * 100}
                      className="h-1.5 bg-[#1e293b]"
                    />
                  </div>
                </Card>
              </div>
            </div>

            {/* Degradation Bar */}
            <div className="px-4 py-3 border-b border-[#1e293b]">
              <div className="flex items-center gap-2 mb-3">
                <ArrowDownRight className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  IS vs OOS Performance
                </span>
                <Badge variant="outline" className="text-[8px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b] ml-auto">
                  Degradation: -{result.overallDegradation.toFixed(1)}%
                </Badge>
              </div>
              <DegradationBar
                isReturn={result.avgInSampleReturn}
                oosReturn={result.avgOutOfSampleReturn}
                degradationPct={result.overallDegradation}
              />
            </div>

            {/* WFE Trend Chart */}
            {result.windows.length >= 2 && (
              <div className="px-4 py-3 border-b border-[#1e293b]">
                <div className="flex items-center gap-2 mb-2">
                  <Activity className="h-3.5 w-3.5 text-amber-400" />
                  <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    WFE Trend Across Windows
                  </span>
                </div>
                <WFETrendChart windows={result.windows} />
              </div>
            )}

            {/* Window Details Table */}
            <div className="px-4 py-3 border-b border-[#1e293b]">
              <div className="flex items-center gap-2 mb-3">
                <Route className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Window Details
                </span>
                <Badge variant="outline" className="text-[8px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b] ml-auto">
                  {result.windows.length} windows
                </Badge>
              </div>
              <WindowTable windows={result.windows} />
            </div>

            {/* Summary Text */}
            <div className="px-4 py-3">
              <Collapsible open={summaryOpen} onOpenChange={setSummaryOpen}>
                <CollapsibleTrigger asChild>
                  <button className="w-full flex items-center justify-between py-1 hover:bg-[#0d1117]/50 transition-colors rounded">
                    <div className="flex items-center gap-2">
                      <BarChart3 className="h-3.5 w-3.5 text-[#64748b]" />
                      <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider">
                        Full Report
                      </span>
                    </div>
                    {summaryOpen ? (
                      <ChevronDown className="h-3 w-3 text-[#475569]" />
                    ) : (
                      <ChevronRight className="h-3 w-3 text-[#475569]" />
                    )}
                  </button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="mt-2 bg-[#0d1117] border border-[#1e293b] rounded-lg p-3 max-h-64 overflow-y-auto" style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
                    <pre className="text-[8px] font-mono text-[#94a3b8] whitespace-pre-wrap leading-relaxed">
                      {result.summary}
                    </pre>
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </div>
          </motion.div>
        )}
      </div>
    </div>
  );
}
