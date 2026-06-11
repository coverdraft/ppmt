'use client';

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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
  ShieldCheck,
  ShieldAlert,
  ShieldX,
  HelpCircle,
  Play,
  ChevronDown,
  ChevronRight,
  Loader2,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Activity,
  Target,
  Dice5,
  Route,
  ArrowDownRight,
  Skull,
  DollarSign,
  Zap,
  Gauge,
} from 'lucide-react';
import { toast } from 'sonner';
import { formatCurrency, formatPct } from '@/lib/format';

// ============================================================
// TYPES
// ============================================================

type Recommendation = 'ROBUST' | 'MARGINAL' | 'OVERFIT' | 'INSUFFICIENT_DATA';

interface OOSSection {
  train_candles: number;
  test_candles: number;
  patterns_trained: number;
  is_total_trades: number;
  is_win_rate: number;
  is_total_pnl_pct: number;
  is_sharpe: number;
  is_max_dd_pct: number;
  is_long_trades: number;
  is_short_trades: number;
  is_long_wr: number;
  is_short_wr: number;
  oos_total_trades: number;
  oos_win_rate: number;
  oos_total_pnl_pct: number;
  oos_sharpe: number;
  oos_max_dd_pct: number;
  oos_long_trades: number;
  oos_short_trades: number;
  oos_long_wr: number;
  oos_short_wr: number;
  pnl_degradation_pct: number;
  wr_degradation_pct: number;
  oos_ratio: number;
  oos_equity_curve: number[];
}

interface MCSection {
  n_simulations: number;
  n_trades_used: number;
  risk_of_ruin_pct: number;
  profit_probability_pct: number;
  p95_max_drawdown_pct: number;
  mean_final_equity: number;
  median_final_equity: number;
  ci_5: number;
  ci_25: number;
  ci_75: number;
  ci_95: number;
  sharpe_ratio: number;
  mean_win_rate_pct: number;
  mean_pnl_pct: number;
}

interface WFWindow {
  window_index: number;
  is_return_pct: number;
  oos_return_pct: number;
  is_trades: number;
  oos_trades: number;
  is_win_rate: number;
  oos_win_rate: number;
  wfe: number;
  degradation_pct: number;
}

interface WFSection {
  aggregate_wfe: number;
  avg_is_return: number;
  avg_oos_return: number;
  overall_degradation: number;
  profitable_windows: number;
  total_windows: number;
  consistency_pct: number;
  windows: WFWindow[];
}

interface ValidationData {
  recommendation: Recommendation;
  confidence_score: number;
  p0_score: number;
  p1_score: number;
  p2_score: number;
  symbol: string;
  total_candles: number;
  elapsed_seconds: number;
  oos: OOSSection;
  mc: MCSection;
  wf: WFSection;
  summary: string;
}

// ============================================================
// HELPERS
// ============================================================

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || isNaN(v)) return '0.00%';
  return formatPct(v, digits);
}

function fmtCur(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '$0.00';
  return formatCurrency(v);
}

function pnlColor(v: number): string {
  if (v > 0) return 'text-emerald-400';
  if (v < 0) return 'text-red-400';
  return 'text-[#94a3b8]';
}

function wfeColor(v: number): string {
  if (v >= 0.7) return 'text-emerald-400';
  if (v >= 0.5) return 'text-amber-400';
  if (v >= 0.3) return 'text-orange-400';
  return 'text-red-400';
}

function wfeBg(v: number): string {
  if (v >= 0.7) return 'bg-emerald-500/10 border-emerald-500/30';
  if (v >= 0.5) return 'bg-amber-500/10 border-amber-500/30';
  if (v >= 0.3) return 'bg-orange-500/10 border-orange-500/30';
  return 'bg-red-500/10 border-red-500/30';
}

// ============================================================
// VERDICT BANNER
// ============================================================

function VerdictBanner({ rec, score }: { rec: Recommendation; score: number }) {
  const config: Record<
    Recommendation,
    { bg: string; border: string; icon: React.ElementType; color: string; label: string }
  > = {
    ROBUST: {
      bg: 'bg-emerald-500/10',
      border: 'border-emerald-500/30',
      icon: ShieldCheck,
      color: 'text-emerald-400',
      label: 'ROBUST',
    },
    MARGINAL: {
      bg: 'bg-amber-500/10',
      border: 'border-amber-500/30',
      icon: ShieldAlert,
      color: 'text-amber-400',
      label: 'MARGINAL',
    },
    OVERFIT: {
      bg: 'bg-red-500/10',
      border: 'border-red-500/30',
      icon: ShieldX,
      color: 'text-red-400',
      label: 'OVERFIT',
    },
    INSUFFICIENT_DATA: {
      bg: 'bg-gray-500/10',
      border: 'border-gray-500/30',
      icon: HelpCircle,
      color: 'text-gray-400',
      label: 'INSUFFICIENT DATA',
    },
  };

  const c = config[rec] || config.INSUFFICIENT_DATA;
  const Icon = c.icon;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`${c.bg} border ${c.border} rounded-lg px-4 py-3 flex items-center gap-3`}
    >
      <div className={`flex items-center justify-center w-10 h-10 rounded-lg ${c.bg} border ${c.border}`}>
        <Icon className={`h-5 w-5 ${c.color}`} />
      </div>
      <div className="flex-1">
        <div className="flex items-center gap-3">
          <span className={`text-[14px] font-mono font-bold ${c.color} tracking-wider`}>{c.label}</span>
          <span className="text-[10px] font-mono text-[#64748b]">
            {score.toFixed(0)}/100 points
          </span>
        </div>
        <div className="mt-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden w-48">
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${Math.min(score, 100)}%` }}
            transition={{ duration: 0.8, ease: 'easeOut' }}
            className={`h-full rounded-full ${
              score >= 70 ? 'bg-emerald-500' : score >= 45 ? 'bg-amber-500' : 'bg-red-500'
            }`}
          />
        </div>
      </div>
    </motion.div>
  );
}

// ============================================================
// SCORE BREAKDOWN
// ============================================================

function ScoreBreakdown({ p0, p1, p2 }: { p0: number; p1: number; p2: number }) {
  const items = [
    { label: 'P0 OOS', value: p0, max: 40, color: '#10b981' },
    { label: 'P1 MC', value: p1, max: 30, color: '#f59e0b' },
    { label: 'P2 WF', value: p2, max: 30, color: '#06b6d4' },
  ];

  return (
    <div className="grid grid-cols-3 gap-2">
      {items.map((item) => (
        <Card key={item.label} className="bg-[#0d1117] border-[#1e293b] p-2">
          <div className="flex items-center gap-1.5 mb-1">
            <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
              {item.label}
            </span>
          </div>
          <div className="text-[16px] font-mono font-bold text-[#e2e8f0]">
            {item.value.toFixed(0)}/{item.max}
          </div>
          <div className="mt-1 h-1 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className="h-full rounded-full"
              style={{
                width: `${(item.value / item.max) * 100}%`,
                backgroundColor: item.color,
              }}
            />
          </div>
        </Card>
      ))}
    </div>
  );
}

// ============================================================
// P0: OOS COMPARISON TABLE
// ============================================================

function OOSComparisonTable({ oos }: { oos: OOSSection }) {
  const rows = [
    { label: 'Trades', is: oos.is_total_trades, oos: oos.oos_total_trades, fmt: (v: number) => String(v) },
    { label: 'Win Rate', is: oos.is_win_rate, oos: oos.oos_win_rate, fmt: (v: number) => fmtPct(v) },
    { label: 'PnL%', is: oos.is_total_pnl_pct, oos: oos.oos_total_pnl_pct, fmt: (v: number) => fmtPct(v) },
    { label: 'Sharpe', is: oos.is_sharpe, oos: oos.oos_sharpe, fmt: (v: number) => v.toFixed(2) },
    { label: 'Max DD%', is: oos.is_max_dd_pct, oos: oos.oos_max_dd_pct, fmt: (v: number) => fmtPct(v) },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px] font-mono">
        <thead>
          <tr className="border-b border-[#1e293b]">
            <th className="text-left py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Metric</th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">In-Sample</th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Out-of-Sample</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-b border-[#1e293b]/50">
              <td className="py-1.5 px-2 text-[#94a3b8]">{r.label}</td>
              <td className={`text-right py-1.5 px-2 font-bold ${pnlColor(r.is)}`}>{r.fmt(r.is)}</td>
              <td className={`text-right py-1.5 px-2 font-bold ${pnlColor(r.oos)}`}>{r.fmt(r.oos)}</td>
            </tr>
          ))}
          <tr className="border-b border-[#1e293b]/50">
            <td className="py-1.5 px-2 text-[#94a3b8]">LONG / SHORT</td>
            <td className="text-right py-1.5 px-2 text-[#94a3b8]">
              {oos.is_long_trades}/{oos.is_short_trades}
              <span className="text-[#475569] ml-1">({fmtPct(oos.is_long_wr)}/{fmtPct(oos.is_short_wr)})</span>
            </td>
            <td className="text-right py-1.5 px-2 text-[#94a3b8]">
              {oos.oos_long_trades}/{oos.oos_short_trades}
              <span className="text-[#475569] ml-1">({fmtPct(oos.oos_long_wr)}/{fmtPct(oos.oos_short_wr)})</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ============================================================
// P0: OOS EQUITY CURVE (SVG)
// ============================================================

function OOSEquityChart({ curve }: { curve: number[] }) {
  if (!curve || curve.length < 2) return null;

  const width = 400;
  const height = 120;
  const padding = { top: 10, right: 10, bottom: 18, left: 50 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const minVal = Math.min(...curve);
  const maxVal = Math.max(...curve);
  const range = maxVal - minVal || 1;

  const scaleX = (i: number) => padding.left + (i / (curve.length - 1)) * chartW;
  const scaleY = (v: number) => padding.top + chartH - ((v - minVal) / range) * chartH;

  const linePath = curve
    .map((v, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(i).toFixed(1)} ${scaleY(v).toFixed(1)}`)
    .join(' ');

  const areaPath = `${linePath} L ${scaleX(curve.length - 1).toFixed(1)} ${padding.top + chartH} L ${padding.left} ${padding.top + chartH} Z`;

  const isPositive = curve[curve.length - 1] >= curve[0];
  const lineColor = isPositive ? '#10b981' : '#ef4444';

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="oosAreaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={lineColor} stopOpacity={0.2} />
          <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
        </linearGradient>
      </defs>
      {[0, 0.5, 1].map((pct) => (
        <line key={pct} x1={padding.left} y1={padding.top + chartH * pct} x2={padding.left + chartW} y2={padding.top + chartH * pct} stroke="#1e293b" strokeDasharray="3 3" />
      ))}
      <path d={areaPath} fill="url(#oosAreaGrad)" />
      <path d={linePath} fill="none" stroke={lineColor} strokeWidth={1.5} />
      <text x={padding.left - 4} y={padding.top + 3} textAnchor="end" fill="#475569" fontSize="7" fontFamily="monospace">
        {fmtCur(maxVal)}
      </text>
      <text x={padding.left - 4} y={padding.top + chartH + 3} textAnchor="end" fill="#475569" fontSize="7" fontFamily="monospace">
        {fmtCur(minVal)}
      </text>
    </svg>
  );
}

// ============================================================
// P1: MC KEY METRICS
// ============================================================

function MCMetricsCards({ mc }: { mc: MCSection }) {
  const probColor = mc.profit_probability_pct >= 70 ? 'text-emerald-400' : mc.profit_probability_pct >= 50 ? 'text-amber-400' : 'text-red-400';
  const ruinColor = mc.risk_of_ruin_pct > 5 ? 'text-red-400' : mc.risk_of_ruin_pct > 1 ? 'text-amber-400' : 'text-emerald-400';

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
      <Card className="bg-[#0d1117] border-[#1e293b] p-3">
        <div className="flex items-center gap-1.5 mb-2">
          <TrendingUp className="h-3 w-3 text-emerald-400" />
          <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Profit Prob</span>
        </div>
        <div className={`text-[18px] font-mono font-bold ${probColor}`}>
          {mc.profit_probability_pct.toFixed(1)}%
        </div>
      </Card>
      <Card className="bg-[#0d1117] border-[#1e293b] p-3">
        <div className="flex items-center gap-1.5 mb-2">
          <Skull className="h-3 w-3 text-red-400" />
          <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Risk of Ruin</span>
        </div>
        <div className={`text-[18px] font-mono font-bold ${ruinColor}`}>
          {mc.risk_of_ruin_pct.toFixed(2)}%
        </div>
      </Card>
      <Card className="bg-[#0d1117] border-[#1e293b] p-3">
        <div className="flex items-center gap-1.5 mb-2">
          <TrendingDown className="h-3 w-3 text-red-400" />
          <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">P95 Max DD</span>
        </div>
        <div className={`text-[18px] font-mono font-bold ${mc.p95_max_drawdown_pct > 30 ? 'text-red-400' : mc.p95_max_drawdown_pct > 15 ? 'text-amber-400' : 'text-emerald-400'}`}>
          {mc.p95_max_drawdown_pct.toFixed(1)}%
        </div>
      </Card>
      <Card className="bg-[#0d1117] border-[#1e293b] p-3">
        <div className="flex items-center gap-1.5 mb-2">
          <DollarSign className="h-3 w-3 text-amber-400" />
          <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Median Equity</span>
        </div>
        <div className={`text-[18px] font-mono font-bold ${pnlColor(mc.median_final_equity)}`}>
          {fmtCur(mc.median_final_equity)}
        </div>
      </Card>
    </div>
  );
}

// ============================================================
// P1: MC CONFIDENCE INTERVALS
// ============================================================

function MCConfidenceTable({ mc }: { mc: MCSection }) {
  const entries = [
    { level: 'P5', value: mc.ci_5 },
    { level: 'P25', value: mc.ci_25 },
    { level: 'P50', value: mc.median_final_equity },
    { level: 'P75', value: mc.ci_75 },
    { level: 'P95', value: mc.ci_95 },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px] font-mono">
        <thead>
          <tr className="border-b border-[#1e293b]">
            <th className="text-left py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Percentile</th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Equity</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.level} className={`border-b border-[#1e293b]/50 ${e.level === 'P50' ? 'bg-amber-500/5' : ''}`}>
              <td className="py-1.5 px-2 text-[#e2e8f0]">
                {e.level === 'P50' && <span className="inline-block w-1 h-1 rounded-full bg-amber-400 mr-1" />}
                {e.level}
              </td>
              <td className={`text-right py-1.5 px-2 font-bold ${pnlColor(e.value)}`}>
                {fmtCur(e.value)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================
// P2: WF TREND CHART (SVG)
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

  const threshold50Y = scaleY(50);

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
      {[0, 0.25, 0.5, 0.75, 1].map((pct) => (
        <line key={pct} x1={padding.left} y1={padding.top + chartH * pct} x2={padding.left + chartW} y2={padding.top + chartH * pct} stroke="#1e293b" strokeDasharray="3 3" />
      ))}
      {threshold50Y >= padding.top && threshold50Y <= padding.top + chartH && (
        <line x1={padding.left} y1={threshold50Y} x2={padding.left + chartW} y2={threshold50Y} stroke="#f59e0b" strokeDasharray="4 4" opacity={0.4} />
      )}
      <defs>
        <linearGradient id="wfAreaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.2} />
          <stop offset="100%" stopColor="#f59e0b" stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#wfAreaGrad)" />
      <path d={linePath} fill="none" stroke="#f59e0b" strokeWidth={1.5} />
      {wfeValues.map((v, i) => {
        const color = v >= 70 ? '#10b981' : v >= 50 ? '#f59e0b' : v >= 30 ? '#f97316' : '#ef4444';
        return (
          <g key={i}>
            <circle cx={scaleX(i)} cy={scaleY(v)} r={3.5} fill={color} stroke="#0a0e17" strokeWidth={1.5} />
            <text x={scaleX(i)} y={scaleY(v) - 7} textAnchor="middle" fill="#e2e8f0" fontSize="7" fontFamily="monospace" fontWeight="bold">
              {v.toFixed(0)}%
            </text>
          </g>
        );
      })}
      {windows.map((w, i) => (
        <text key={i} x={scaleX(i)} y={padding.top + chartH + 14} textAnchor="middle" fill="#475569" fontSize="7" fontFamily="monospace">
          W{w.window_index + 1}
        </text>
      ))}
    </svg>
  );
}

// ============================================================
// P2: WF WINDOW TABLE
// ============================================================

function WFWindowTable({ windows }: { windows: WFWindow[] }) {
  return (
    <div className="overflow-x-auto" style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
      <table className="w-full text-[9px] font-mono">
        <thead>
          <tr className="text-[#475569] uppercase border-b border-[#1e293b]/50">
            <th className="py-1.5 px-2 text-left">Window</th>
            <th className="py-1.5 px-2 text-right">IS PnL%</th>
            <th className="py-1.5 px-2 text-right">OOS PnL%</th>
            <th className="py-1.5 px-2 text-center">WFE</th>
            <th className="py-1.5 px-2 text-right">Degradation</th>
            <th className="py-1.5 px-2 text-center">Trades</th>
          </tr>
        </thead>
        <tbody>
          {windows.map((w) => (
            <motion.tr
              key={w.window_index}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: w.window_index * 0.05 }}
              className="border-b border-[#1e293b]/30 hover:bg-[#0a0e17]/50"
            >
              <td className="py-1.5 px-2 text-[#e2e8f0] font-bold">W{w.window_index + 1}</td>
              <td className={`py-1.5 px-2 text-right font-bold ${pnlColor(w.is_return_pct)}`}>
                {fmtPct(w.is_return_pct)}
              </td>
              <td className={`py-1.5 px-2 text-right font-bold ${pnlColor(w.oos_return_pct)}`}>
                {fmtPct(w.oos_return_pct)}
              </td>
              <td className="py-1.5 px-2 text-center">
                <Badge className={`text-[8px] h-4 px-1.5 font-mono border ${wfeBg(w.wfe)} ${wfeColor(w.wfe)}`}>
                  {(w.wfe * 100).toFixed(0)}%
                </Badge>
              </td>
              <td className="py-1.5 px-2 text-right text-[#94a3b8]">{w.degradation_pct.toFixed(1)}%</td>
              <td className="py-1.5 px-2 text-center text-[#94a3b8]">
                <span className="text-emerald-400">{w.is_trades}</span>
                <span className="text-[#475569]">/</span>
                <span className="text-amber-400">{w.oos_trades}</span>
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

export default function ValidationSuitePanel() {
  // Config state
  const [symbol, setSymbol] = useState('BTC/USDT');
  const [trainRatio, setTrainRatio] = useState(0.7);
  const [mcSimulations, setMcSimulations] = useState(1000);
  const [wfWindows, setWfWindows] = useState(5);
  const [patternLength, setPatternLength] = useState(5);
  const [forwardWindow, setForwardWindow] = useState(5);
  const [seed, setSeed] = useState(42);
  const [configOpen, setConfigOpen] = useState(true);
  const [result, setResult] = useState<ValidationData | null>(null);

  // Run validation mutation
  const mutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/validation/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          trainRatio,
          mcSimulations,
          wfWindows,
          wfTrainRatio: trainRatio,
          patternLength,
          forwardWindow,
          positionSize: 1.0,
          ruinThreshold: 0.5,
          seed,
        }),
      });
      const json = await res.json();
      if (!res.ok || json.error) {
        throw new Error(json.error || 'Validation failed');
      }
      return json.data as ValidationData;
    },
    onSuccess: (data) => {
      setResult(data);
      setConfigOpen(false);
      const recMessages: Record<Recommendation, string> = {
        ROBUST: 'System validated as robust!',
        MARGINAL: 'System shows marginal performance.',
        OVERFIT: 'Warning: System appears overfit!',
        INSUFFICIENT_DATA: 'Insufficient data for validation.',
      };
      toast.success(recMessages[data.recommendation] || 'Validation complete');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Validation failed');
    },
  });

  const hasResult = result !== null;

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <Zap className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Validation Suite</span>
            {hasResult && (
              <span className="text-[9px] font-mono text-[#475569] ml-2">
                {result.symbol} &middot; {result.total_candles.toLocaleString()} candles &middot; {result.elapsed_seconds.toFixed(1)}s
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
              <Gauge className="h-3 w-3 mr-1" />
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
                <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider">Configuration</span>
              </div>
              {configOpen ? <ChevronDown className="h-3 w-3 text-[#475569]" /> : <ChevronRight className="h-3 w-3 text-[#475569]" />}
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="px-4 py-3 border-b border-[#1e293b] bg-[#0d1117]/30">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {/* Symbol */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Symbol</Label>
                  <Select value={symbol} onValueChange={setSymbol}>
                    <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-[#111827] border-[#1e293b]">
                      <SelectItem value="BTC/USDT" className="text-[10px] font-mono">BTC/USDT</SelectItem>
                      <SelectItem value="ETH/USDT" className="text-[10px] font-mono">ETH/USDT</SelectItem>
                      <SelectItem value="SOL/USDT" className="text-[10px] font-mono">SOL/USDT</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {/* MC Simulations */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">MC Simulations</Label>
                  <Input
                    type="number"
                    value={mcSimulations}
                    onChange={(e) => setMcSimulations(Math.max(100, Math.min(100000, Number(e.target.value) || 1000)))}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>
                {/* WF Windows */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">WF Windows</Label>
                  <Input
                    type="number"
                    value={wfWindows}
                    onChange={(e) => setWfWindows(Math.max(2, Math.min(20, Number(e.target.value) || 5)))}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>
                {/* Seed */}
                <div>
                  <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Seed</Label>
                  <Input
                    type="number"
                    value={seed}
                    onChange={(e) => setSeed(Number(e.target.value) || 42)}
                    className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>
              </div>

              {/* Train Ratio Slider */}
              <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Train Ratio</Label>
                    <span className="text-[10px] font-mono text-amber-400 font-bold">{(trainRatio * 100).toFixed(0)}%</span>
                  </div>
                  <Slider
                    value={[trainRatio]}
                    onValueChange={([v]) => setTrainRatio(v)}
                    min={0.5}
                    max={0.9}
                    step={0.05}
                    className="py-1"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex-1">
                    <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Pattern Length</Label>
                    <Input
                      type="number"
                      value={patternLength}
                      onChange={(e) => setPatternLength(Math.max(3, Math.min(10, Number(e.target.value) || 5)))}
                      className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                    />
                  </div>
                  <div className="flex-1">
                    <Label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Forward Window</Label>
                    <Input
                      type="number"
                      value={forwardWindow}
                      onChange={(e) => setForwardWindow(Math.max(2, Math.min(10, Number(e.target.value) || 5)))}
                      className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                    />
                  </div>
                </div>
              </div>

              {/* Run Button */}
              <div className="mt-4 flex items-center gap-3">
                <Button
                  onClick={() => mutation.mutate()}
                  disabled={mutation.isPending}
                  className="h-9 px-6 text-[11px] font-mono font-bold bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 border border-amber-500/30 disabled:opacity-50"
                >
                  {mutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Validating...
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4 mr-2" />
                      Run Full Validation
                    </>
                  )}
                </Button>
                <span className="text-[9px] font-mono text-[#475569]">
                  P0 (OOS) + P1 (Monte Carlo) + P2 (Walk-Forward)
                </span>
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>

        {/* ---- EMPTY STATE ---- */}
        <AnimatePresence mode="wait">
          {!hasResult && !mutation.isPending && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex-1 flex flex-col items-center justify-center py-16 px-6"
            >
              <div className="flex items-center justify-center w-16 h-16 rounded-xl bg-amber-500/10 border border-amber-500/20 mb-4">
                <Zap className="h-7 w-7 text-amber-400" />
              </div>
              <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">One-Click Validation Suite</h3>
              <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md leading-relaxed">
                Run P0 (Out-of-Sample), P1 (Monte Carlo), and P2 (Walk-Forward) validation
                in a single click. Get a composite ROBUST / MARGINAL / OVERFIT verdict.
              </p>
              <div className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-3 max-w-lg">
                <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3 text-center">
                  <Target className="h-5 w-5 text-emerald-400 mx-auto mb-1.5" />
                  <span className="text-[9px] font-mono text-[#94a3b8]">P0: Out-of-Sample</span>
                </div>
                <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3 text-center">
                  <Dice5 className="h-5 w-5 text-amber-400 mx-auto mb-1.5" />
                  <span className="text-[9px] font-mono text-[#94a3b8]">P1: Monte Carlo</span>
                </div>
                <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3 text-center">
                  <Route className="h-5 w-5 text-cyan-400 mx-auto mb-1.5" />
                  <span className="text-[9px] font-mono text-[#94a3b8]">P2: Walk-Forward</span>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ---- LOADING STATE ---- */}
        {mutation.isPending && !hasResult && (
          <div className="flex-1 flex flex-col items-center justify-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-amber-400 mb-3" />
            <span className="text-sm font-mono text-[#94a3b8]">Running Validation Suite...</span>
            <span className="text-[10px] font-mono text-[#475569] mt-1">
              P0 (OOS) + P1 (MC {mcSimulations} sims) + P2 ({wfWindows} windows)
            </span>
            <span className="text-[9px] font-mono text-[#475569] mt-1">
              This may take 30-120 seconds depending on data size
            </span>
          </div>
        )}

        {/* ---- RESULTS SECTION ---- */}
        {hasResult && result && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
          >
            {/* Verdict Banner */}
            <div className="px-4 py-3 border-b border-[#1e293b]">
              <VerdictBanner rec={result.recommendation} score={result.confidence_score} />
            </div>

            {/* Score Breakdown */}
            <div className="px-4 py-3 border-b border-[#1e293b]">
              <ScoreBreakdown p0={result.p0_score} p1={result.p1_score} p2={result.p2_score} />
            </div>

            {/* ──── P0: Out-of-Sample ──── */}
            <div className="px-4 py-3 border-b border-[#1e293b]">
              <div className="flex items-center gap-2 mb-3">
                <Target className="h-3.5 w-3.5 text-emerald-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  P0: Out-of-Sample
                </span>
                <Badge variant="outline" className="text-[8px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b] ml-auto">
                  {result.oos.patterns_trained.toLocaleString()} patterns
                </Badge>
              </div>

              <OOSComparisonTable oos={result.oos} />

              {/* OOS Ratio bar */}
              <div className="mt-3 flex items-center gap-3">
                <span className="text-[9px] font-mono text-[#64748b] w-20">OOS Ratio</span>
                <div className="flex-1 h-2.5 bg-[#1e293b] rounded-full overflow-hidden">
                  <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.min(Math.abs(result.oos.oos_ratio) * 100, 100)}%` }}
                    transition={{ duration: 0.8 }}
                    className={`h-full rounded-full ${result.oos.oos_ratio >= 0.5 ? 'bg-emerald-500' : result.oos.oos_ratio >= 0.3 ? 'bg-amber-500' : 'bg-red-500'}`}
                  />
                </div>
                <span className={`text-[10px] font-mono font-bold w-12 text-right ${wfeColor(result.oos.oos_ratio)}`}>
                  {result.oos.oos_ratio.toFixed(3)}
                </span>
              </div>

              {/* Equity Curve */}
              {result.oos.oos_equity_curve && result.oos.oos_equity_curve.length > 2 && (
                <div className="mt-3">
                  <div className="flex items-center gap-2 mb-1">
                    <Activity className="h-3 w-3 text-[#64748b]" />
                    <span className="text-[9px] font-mono text-[#64748b]">OOS Equity Curve</span>
                  </div>
                  <OOSEquityChart curve={result.oos.oos_equity_curve} />
                </div>
              )}
            </div>

            {/* ──── P1: Monte Carlo ──── */}
            <div className="px-4 py-3 border-b border-[#1e293b]">
              <div className="flex items-center gap-2 mb-3">
                <Dice5 className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  P1: Monte Carlo
                </span>
                <Badge variant="outline" className="text-[8px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b] ml-auto">
                  {result.mc.n_simulations.toLocaleString()} sims
                </Badge>
              </div>

              {result.mc.n_trades_used > 0 ? (
                <>
                  <MCMetricsCards mc={result.mc} />
                  <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                      <div className="flex items-center gap-2 mb-2">
                        <Target className="h-3 w-3 text-amber-400" />
                        <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">Equity Confidence Intervals</span>
                      </div>
                      <MCConfidenceTable mc={result.mc} />
                    </Card>
                    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                      <div className="flex items-center gap-2 mb-2">
                        <Activity className="h-3 w-3 text-red-400" />
                        <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">Key Stats</span>
                      </div>
                      <div className="space-y-2 text-[10px] font-mono">
                        <div className="flex justify-between">
                          <span className="text-[#64748b]">Mean Win Rate</span>
                          <span className="text-[#e2e8f0]">{result.mc.mean_win_rate_pct.toFixed(1)}%</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-[#64748b]">Mean PnL</span>
                          <span className={pnlColor(result.mc.mean_pnl_pct)}>{fmtPct(result.mc.mean_pnl_pct)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-[#64748b]">Sharpe Ratio</span>
                          <span className="text-[#e2e8f0]">{result.mc.sharpe_ratio.toFixed(2)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-[#64748b]">Trades Used</span>
                          <span className="text-[#e2e8f0]">{result.mc.n_trades_used}</span>
                        </div>
                      </div>
                    </Card>
                  </div>
                </>
              ) : (
                <div className="text-[10px] font-mono text-[#475569] py-4 text-center">
                  Insufficient OOS trades for Monte Carlo simulation
                </div>
              )}
            </div>

            {/* ──── P2: Walk-Forward ──── */}
            <div className="px-4 py-3">
              <div className="flex items-center gap-2 mb-3">
                <Route className="h-3.5 w-3.5 text-cyan-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  P2: Walk-Forward
                </span>
                <Badge variant="outline" className="text-[8px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b] ml-auto">
                  {result.wf.total_windows} windows
                </Badge>
              </div>

              {result.wf.total_windows >= 2 ? (
                <>
                  {/* Aggregate metrics */}
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mb-3">
                    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                      <div className="flex items-center gap-1.5 mb-2">
                        <Activity className={`h-3 w-3 ${wfeColor(result.wf.aggregate_wfe)}`} />
                        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Aggregate WFE</span>
                      </div>
                      <div className={`text-[16px] font-mono font-bold ${wfeColor(result.wf.aggregate_wfe)}`}>
                        {(result.wf.aggregate_wfe * 100).toFixed(1)}%
                      </div>
                    </Card>
                    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                      <div className="flex items-center gap-1.5 mb-2">
                        <Target className="h-3 w-3 text-amber-400" />
                        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Consistency</span>
                      </div>
                      <div className="text-[16px] font-mono font-bold text-[#e2e8f0]">
                        {result.wf.consistency_pct.toFixed(0)}%
                      </div>
                    </Card>
                    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                      <div className="flex items-center gap-1.5 mb-2">
                        <TrendingUp className="h-3 w-3 text-cyan-400" />
                        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Avg IS</span>
                      </div>
                      <div className={`text-[16px] font-mono font-bold ${pnlColor(result.wf.avg_is_return)}`}>
                        {fmtPct(result.wf.avg_is_return)}
                      </div>
                    </Card>
                    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                      <div className="flex items-center gap-1.5 mb-2">
                        <BarChart3 className={`h-3 w-3 ${pnlColor(result.wf.avg_oos_return)}`} />
                        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Avg OOS</span>
                      </div>
                      <div className={`text-[16px] font-mono font-bold ${pnlColor(result.wf.avg_oos_return)}`}>
                        {fmtPct(result.wf.avg_oos_return)}
                      </div>
                    </Card>
                  </div>

                  {/* Degradation bar */}
                  <div className="mb-3">
                    <div className="flex items-center gap-2 mb-1">
                      <ArrowDownRight className="h-3 w-3 text-amber-400" />
                      <span className="text-[9px] font-mono text-[#94a3b8]">Overall Degradation</span>
                      <Badge variant="outline" className="text-[8px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b] ml-auto">
                        {result.wf.overall_degradation.toFixed(1)}%
                      </Badge>
                    </div>
                    <div className="h-2 bg-[#1e293b] rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${result.wf.overall_degradation < 30 ? 'bg-emerald-500' : result.wf.overall_degradation < 50 ? 'bg-amber-500' : 'bg-red-500'}`}
                        style={{ width: `${Math.min(result.wf.overall_degradation, 100)}%` }}
                      />
                    </div>
                  </div>

                  {/* WFE Trend Chart */}
                  {result.wf.windows.length >= 2 && (
                    <div className="mb-3">
                      <WFETrendChart windows={result.wf.windows} />
                    </div>
                  )}

                  {/* Window Details Table */}
                  <WFWindowTable windows={result.wf.windows} />
                </>
              ) : (
                <div className="text-[10px] font-mono text-[#475569] py-4 text-center">
                  Insufficient data for walk-forward analysis
                </div>
              )}
            </div>
          </motion.div>
        )}
      </div>
    </div>
  );
}
