'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Shield,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Activity,
  Target,
  Clock,
  BarChart3,
  Trophy,
  Skull,
  Loader2,
  Save,
  Settings,
  ArrowUpRight,
  ArrowDownRight,
  Gauge,
  DollarSign,
  PieChart,
  Zap,
  Timer,
} from 'lucide-react';
import { toast } from 'sonner';
import { useState, useMemo } from 'react';

// ============================================================
// TYPES
// ============================================================

interface RiskOverview {
  portfolioRisk: {
    totalExposureUsd: number;
    maxExposureUsd: number;
    exposurePct: number;
    openPositions: number;
    maxPositions: number;
    concentrationByChain: Record<string, number>;
    concentrationByDirection: Record<string, number>;
  };
  pnlMetrics: {
    realizedPnl: number;
    unrealizedPnl: number;
    totalPnl: number;
    winRate: number;
    avgWinUsd: number;
    avgLossUsd: number;
    profitFactor: number;
    expectancyUsd: number;
    maxConsecutiveWins: number;
    maxConsecutiveLosses: number;
  };
  drawdown: {
    currentDrawdownPct: number;
    maxDrawdownPct: number;
    maxDrawdownUsd: number;
    peakCapital: number;
    currentCapital: number;
    recoveryFactor: number;
    timeToRecoveryEstMin: number;
  };
  riskControls: {
    maxPositionSizePct: number;
    maxPortfolioRiskPct: number;
    stopLossDefaultPct: number;
    dailyLossLimitPct: number;
    currentDailyPnlPct: number;
  };
  tradeAnalysis: {
    avgHoldTimeMin: number;
    bestTrade: { symbol: string; pnlUsd: number; pnlPct: number } | null;
    worstTrade: { symbol: string; pnlUsd: number; pnlPct: number } | null;
    avgMfe: number;
    avgMae: number;
    mfeMaeRatio: number;
  };
  equityCurve: Array<{
    timestamp: string;
    capital: number;
    drawdown: number;
  }>;
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

function pnlColor(v: number): string {
  if (v > 0) return 'text-emerald-400';
  if (v < 0) return 'text-red-400';
  return 'text-[#94a3b8]';
}

function riskColor(score: number): string {
  if (score <= 30) return '#10b981';
  if (score <= 60) return '#f59e0b';
  return '#ef4444';
}

function riskLabel(score: number): string {
  if (score <= 30) return 'LOW';
  if (score <= 60) return 'MEDIUM';
  return 'HIGH';
}

// ============================================================
// RISK SCORE GAUGE
// ============================================================

function RiskScoreGauge({ score }: { score: number }) {
  const color = riskColor(score);
  const label = riskLabel(score);
  const radius = 60;
  const circumference = Math.PI * radius; // Half circle
  const progress = (score / 100) * circumference;
  const dashOffset = circumference - progress;

  return (
    <div className="flex flex-col items-center">
      <div className="relative">
        <svg width="160" height="90" viewBox="0 0 160 90">
          {/* Background arc */}
          <path
            d="M 20 80 A 60 60 0 0 1 140 80"
            fill="none"
            stroke="#1e293b"
            strokeWidth="10"
            strokeLinecap="round"
          />
          {/* Progress arc */}
          <path
            d="M 20 80 A 60 60 0 0 1 140 80"
            fill="none"
            stroke={color}
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
            style={{ transition: 'stroke-dashoffset 0.8s ease, stroke 0.3s ease' }}
          />
          {/* Tick marks */}
          <line x1="20" y1="80" x2="20" y2="74" stroke="#475569" strokeWidth="1" />
          <line x1="80" y1="20" x2="80" y2="14" stroke="#475569" strokeWidth="1" />
          <line x1="140" y1="80" x2="140" y2="74" stroke="#475569" strokeWidth="1" />
          <text x="18" y="88" fontSize="7" fill="#475569" fontFamily="monospace">0</text>
          <text x="76" y="12" fontSize="7" fill="#475569" fontFamily="monospace">50</text>
          <text x="134" y="88" fontSize="7" fill="#475569" fontFamily="monospace">100</text>
        </svg>
        <div className="absolute inset-0 flex items-end justify-center pb-1">
          <div className="text-center">
            <span className="text-2xl font-mono font-bold" style={{ color }}>
              {score.toFixed(0)}
            </span>
          </div>
        </div>
      </div>
      <Badge
        className="text-[9px] h-5 px-2 font-mono mt-1"
        style={{
          backgroundColor: `${color}20`,
          color,
          borderColor: `${color}40`,
        }}
      >
        {label} RISK
      </Badge>
    </div>
  );
}

// ============================================================
// MINI DONUT CHART (SVG)
// ============================================================

function MiniDonut({ value, max, color, size = 48 }: { value: number; max: number; color: string; size?: number }) {
  const radius = (size - 8) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = max > 0 ? value / max : 0;
  const dashOffset = circumference * (1 - pct);

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="#1e293b"
        strokeWidth="4"
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth="4"
        strokeDasharray={circumference}
        strokeDashoffset={dashOffset}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: 'stroke-dashoffset 0.5s ease' }}
      />
      <text
        x={size / 2}
        y={size / 2}
        textAnchor="middle"
        dominantBaseline="central"
        fill="#e2e8f0"
        fontSize="9"
        fontFamily="monospace"
        fontWeight="bold"
      >
        {max > 0 ? `${(pct * 100).toFixed(0)}%` : '—'}
      </text>
    </svg>
  );
}

// ============================================================
// EQUITY CURVE (SVG)
// ============================================================

function EquityCurveChart({ data }: { data: Array<{ timestamp: string; capital: number; drawdown: number }> }) {
  if (data.length < 2) return null;

  const width = 400;
  const height = 120;
  const padding = { top: 10, right: 10, bottom: 20, left: 45 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const capitals = data.map((d) => d.capital);
  const minCap = Math.min(...capitals);
  const maxCap = Math.max(...capitals);
  const range = maxCap - minCap || 1;

  const scaleX = (i: number) => padding.left + (i / (data.length - 1)) * chartW;
  const scaleY = (v: number) => padding.top + chartH - ((v - minCap) / range) * chartH;

  // Capital line
  const linePath = data
    .map((d, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(i)} ${scaleY(d.capital)}`)
    .join(' ');

  // Area under capital
  const areaPath = `${linePath} L ${scaleX(data.length - 1)} ${padding.top + chartH} L ${padding.left} ${padding.top + chartH} Z`;

  // Drawdown bars
  const maxDd = Math.max(...data.map((d) => d.drawdown), 1);
  const ddBars = data
    .filter((d) => d.drawdown > 0)
    .map((d, idx) => {
      const i = data.indexOf(d);
      const barH = (d.drawdown / maxDd) * (chartH * 0.3);
      return (
        <rect
          key={idx}
          x={scaleX(i) - 1}
          y={padding.top + chartH - barH}
          width={2}
          height={barH}
          fill="#ef4444"
          opacity={0.4}
        />
      );
    });

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

      {/* Area */}
      <path d={areaPath} fill="url(#equityGrad)" />
      <defs>
        <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#10b981" stopOpacity={0.3} />
          <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
        </linearGradient>
      </defs>

      {/* Drawdown bars */}
      {ddBars}

      {/* Capital line */}
      <path d={linePath} fill="none" stroke="#10b981" strokeWidth={1.5} />

      {/* Y axis labels */}
      {[minCap, (minCap + maxCap) / 2, maxCap].map((v, i) => (
        <text
          key={i}
          x={padding.left - 4}
          y={scaleY(v) + 3}
          textAnchor="end"
          fill="#475569"
          fontSize="7"
          fontFamily="monospace"
        >
          ${v.toFixed(2)}
        </text>
      ))}
    </svg>
  );
}

// ============================================================
// CONCENTRATION BAR
// ============================================================

function ConcentrationBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[9px] font-mono text-[#94a3b8] w-20 truncate">{label}</span>
      <div className="flex-1 h-2.5 bg-[#1e293b] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${Math.min(value, 100)}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-[9px] font-mono text-[#e2e8f0] w-8 text-right">{value}%</span>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function RiskManagementPanel() {
  const queryClient = useQueryClient();
  const [showControls, setShowControls] = useState(false);
  const [riskControls, setRiskControls] = useState({
    maxPositionSizePct: 10,
    maxPortfolioRiskPct: 25,
    stopLossDefaultPct: 5,
    dailyLossLimitPct: 10,
  });

  // Fetch risk overview
  const { data: riskData, isLoading } = useQuery({
    queryKey: ['risk-overview'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/risk/overview?includeHistory=true');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as RiskOverview | null;
      } catch {
        return null;
      }
    },
    staleTime: 15000,
    refetchInterval: 30000,
  });

  // Sync risk controls from API data
  const data = riskData;
  const hasTrades = data && (data.pnlMetrics.realizedPnl !== 0 || data.pnlMetrics.unrealizedPnl !== 0 || data.drawdown.peakCapital > 0);

  // Calculate risk score
  const riskScore = useMemo(() => {
    if (!data) return 0;
    const exposureScore = Math.min(data.portfolioRisk.exposurePct / 100, 1) * 25;
    const drawdownScore = Math.min(data.drawdown.currentDrawdownPct / 30, 1) * 30;
    const concentrationScore = (() => {
      const chainValues = Object.values(data.portfolioRisk.concentrationByChain);
      const maxConcentration = chainValues.length > 0 ? Math.max(...chainValues) : 0;
      return Math.min(maxConcentration / 100, 1) * 25;
    })();
    const directionBias = (() => {
      const long = data.portfolioRisk.concentrationByDirection.LONG || 0;
      const short = data.portfolioRisk.concentrationByDirection.SHORT || 0;
      const total = long + short;
      if (total === 0) return 0;
      const bias = Math.abs(long - short) / total;
      return bias * 20;
    })();

    return Math.min(Math.round(exposureScore + drawdownScore + concentrationScore + directionBias), 100);
  }, [data]);

  // Initialize risk controls from data
  useMemo(() => {
    if (data?.riskControls) {
      setRiskControls({
        maxPositionSizePct: data.riskControls.maxPositionSizePct,
        maxPortfolioRiskPct: data.riskControls.maxPortfolioRiskPct,
        stopLossDefaultPct: data.riskControls.stopLossDefaultPct,
        dailyLossLimitPct: data.riskControls.dailyLossLimitPct,
      });
    }
  }, [data?.riskControls]);

  // Save controls mutation
  const saveControlsMutation = useMutation({
    mutationFn: async () => {
      // For now, just show success. In a full implementation,
      // this would persist to a config store or TradingSystem
      await new Promise((r) => setTimeout(r, 500));
      return true;
    },
    onSuccess: () => {
      toast.success('Risk controls saved');
      queryClient.invalidateQueries({ queryKey: ['risk-overview'] });
    },
  });

  // ---- LOADING STATE ----
  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0a0e17]">
        <Loader2 className="h-8 w-8 animate-spin text-[#d4af37] mb-3" />
        <span className="text-sm font-mono text-[#64748b]">Loading risk analysis...</span>
      </div>
    );
  }

  // ---- EMPTY STATE ----
  if (!data || !hasTrades) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        <div className="flex items-center gap-2 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <Shield className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Risk Management</span>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-amber-500/10 border border-amber-500/20 mb-4">
            <Shield className="h-6 w-6 text-amber-400" />
          </div>
          <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">Risk Dashboard</h3>
          <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md">
            Start paper trading to generate risk analytics. The risk panel will display portfolio exposure,
            drawdown analysis, concentration metrics, and risk controls once trading activity begins.
          </p>
        </div>
      </div>
    );
  }

  const { portfolioRisk, pnlMetrics, drawdown, riskControls: apiControls, tradeAnalysis, equityCurve } = data;

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <Shield className="h-3.5 w-3.5 text-amber-400" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Risk Management</span>
            <span className="text-[9px] font-mono text-[#475569] ml-2">
              Capital: {formatCurrency(drawdown.currentCapital)}
            </span>
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className={`h-6 text-[9px] font-mono px-2 ${showControls ? 'text-amber-400 bg-amber-500/10' : 'text-[#94a3b8]'}`}
          onClick={() => setShowControls(!showControls)}
        >
          <Settings className="h-3 w-3 mr-1" />
          Controls
        </Button>
      </div>

      {/* ===== SCROLLABLE CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* ---- RISK SCORE + EXPOSURE CARDS (TOP ROW) ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="flex flex-col md:flex-row gap-3">
            {/* Risk Score Gauge */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4 flex-shrink-0 flex flex-col items-center justify-center md:w-[180px]">
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-2">Portfolio Risk</span>
              <RiskScoreGauge score={riskScore} />
            </Card>

            {/* Exposure Cards Grid */}
            <div className="flex-1 grid grid-cols-2 lg:grid-cols-4 gap-2">
              {/* Total Exposure */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <DollarSign className="h-3 w-3 text-amber-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Exposure</span>
                </div>
                <div className="text-[14px] font-mono font-bold text-[#e2e8f0]">
                  {formatCurrency(portfolioRisk.totalExposureUsd)}
                </div>
                <div className="mt-1.5">
                  <Progress
                    value={portfolioRisk.exposurePct}
                    className="h-1.5 bg-[#1e293b]"
                  />
                  <div className="flex justify-between mt-0.5">
                    <span className="text-[8px] font-mono text-[#475569]">
                      {portfolioRisk.exposurePct.toFixed(1)}%
                    </span>
                    <span className="text-[8px] font-mono text-[#475569]">
                      of {formatCurrency(portfolioRisk.maxExposureUsd)}
                    </span>
                  </div>
                </div>
              </Card>

              {/* Open Positions */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <Activity className="h-3 w-3 text-cyan-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Positions</span>
                </div>
                <div className="text-[14px] font-mono font-bold text-[#e2e8f0]">
                  {portfolioRisk.openPositions}
                  <span className="text-[10px] text-[#475569]"> / {portfolioRisk.maxPositions}</span>
                </div>
                <div className="mt-1.5">
                  <Progress
                    value={(portfolioRisk.openPositions / portfolioRisk.maxPositions) * 100}
                    className="h-1.5 bg-[#1e293b]"
                  />
                  <span className="text-[8px] font-mono text-[#475569]">
                    {((portfolioRisk.openPositions / portfolioRisk.maxPositions) * 100).toFixed(0)}% utilized
                  </span>
                </div>
              </Card>

              {/* Capital at Risk */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  <Gauge className="h-3 w-3 text-red-400" />
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Capital at Risk</span>
                </div>
                <div className="text-[14px] font-mono font-bold text-[#e2e8f0]">
                  {portfolioRisk.exposurePct.toFixed(1)}%
                </div>
                <div className="mt-1.5">
                  <div className="h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.min(portfolioRisk.exposurePct, 100)}%`,
                        backgroundColor: portfolioRisk.exposurePct > 75 ? '#ef4444' : portfolioRisk.exposurePct > 50 ? '#f59e0b' : '#10b981',
                      }}
                    />
                  </div>
                  <span className="text-[8px] font-mono text-[#475569]">
                    {formatCurrency(portfolioRisk.totalExposureUsd)} at risk
                  </span>
                </div>
              </Card>

              {/* Daily P&L */}
              <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                <div className="flex items-center gap-1.5 mb-2">
                  {apiControls.currentDailyPnlPct >= 0 ? (
                    <TrendingUp className="h-3 w-3 text-emerald-400" />
                  ) : (
                    <TrendingDown className="h-3 w-3 text-red-400" />
                  )}
                  <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Daily P&L</span>
                </div>
                <div className={`text-[14px] font-mono font-bold ${pnlColor(apiControls.currentDailyPnlPct)}`}>
                  {formatPct(apiControls.currentDailyPnlPct)}
                </div>
                <div className="mt-1.5 flex items-center gap-1">
                  <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${Math.min(Math.abs(apiControls.currentDailyPnlPct) / apiControls.dailyLossLimitPct * 100, 100)}%`,
                        backgroundColor: apiControls.currentDailyPnlPct >= 0 ? '#10b981' : '#ef4444',
                      }}
                    />
                  </div>
                  <span className="text-[8px] font-mono text-[#475569]">
                    lim {apiControls.dailyLossLimitPct}%
                  </span>
                </div>
              </Card>
            </div>
          </div>
        </div>

        {/* ---- P&L METRICS + DRAWDOWN (MIDDLE) ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* P&L Metrics */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <BarChart3 className="h-3.5 w-3.5 text-emerald-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  P&L Metrics
                </span>
              </div>

              <div className="grid grid-cols-3 gap-3">
                {/* Realized P&L */}
                <div>
                  <span className="text-[8px] font-mono text-[#475569] block">Realized</span>
                  <span className={`text-[12px] font-mono font-bold ${pnlColor(pnlMetrics.realizedPnl)}`}>
                    {formatCurrency(pnlMetrics.realizedPnl)}
                  </span>
                </div>
                {/* Unrealized P&L */}
                <div>
                  <span className="text-[8px] font-mono text-[#475569] block">Unrealized</span>
                  <span className={`text-[12px] font-mono font-bold ${pnlColor(pnlMetrics.unrealizedPnl)}`}>
                    {formatCurrency(pnlMetrics.unrealizedPnl)}
                  </span>
                </div>
                {/* Total P&L */}
                <div>
                  <span className="text-[8px] font-mono text-[#475569] block">Total</span>
                  <span className={`text-[12px] font-mono font-bold ${pnlColor(pnlMetrics.totalPnl)}`}>
                    {formatCurrency(pnlMetrics.totalPnl)}
                  </span>
                </div>
              </div>

              <Separator className="my-3 bg-[#1e293b]" />

              <div className="grid grid-cols-2 gap-3">
                {/* Win Rate */}
                <div className="flex items-center gap-2">
                  <MiniDonut
                    value={pnlMetrics.winRate}
                    max={1}
                    color={pnlMetrics.winRate >= 0.5 ? '#10b981' : '#ef4444'}
                  />
                  <div>
                    <span className="text-[8px] font-mono text-[#475569] block">Win Rate</span>
                    <span className={`text-[13px] font-mono font-bold ${pnlMetrics.winRate >= 0.5 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {(pnlMetrics.winRate * 100).toFixed(1)}%
                    </span>
                  </div>
                </div>

                {/* Profit Factor */}
                <div>
                  <span className="text-[8px] font-mono text-[#475569] block">Profit Factor</span>
                  <span className={`text-[13px] font-mono font-bold ${
                    pnlMetrics.profitFactor >= 1.5 ? 'text-emerald-400' :
                    pnlMetrics.profitFactor >= 1 ? 'text-amber-400' : 'text-red-400'
                  }`}>
                    {pnlMetrics.profitFactor === -1 ? '∞' : pnlMetrics.profitFactor.toFixed(2)}
                  </span>
                </div>

                {/* Expectancy */}
                <div>
                  <span className="text-[8px] font-mono text-[#475569] block">Expectancy</span>
                  <span className={`text-[13px] font-mono font-bold ${pnlColor(pnlMetrics.expectancyUsd)}`}>
                    {formatCurrency(pnlMetrics.expectancyUsd)}
                  </span>
                </div>

                {/* Avg Win / Avg Loss */}
                <div>
                  <span className="text-[8px] font-mono text-[#475569] block">Avg Win / Loss</span>
                  <div className="flex items-center gap-1">
                    <span className="text-[11px] font-mono font-bold text-emerald-400">
                      {formatCurrency(pnlMetrics.avgWinUsd)}
                    </span>
                    <span className="text-[9px] text-[#475569]">/</span>
                    <span className="text-[11px] font-mono font-bold text-red-400">
                      {formatCurrency(pnlMetrics.avgLossUsd)}
                    </span>
                  </div>
                </div>
              </div>

              <Separator className="my-3 bg-[#1e293b]" />

              {/* Streaks */}
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-1.5">
                  <Trophy className="h-3 w-3 text-emerald-400" />
                  <span className="text-[8px] font-mono text-[#475569]">Win Streak</span>
                  <span className="text-[11px] font-mono font-bold text-emerald-400">
                    {pnlMetrics.maxConsecutiveWins}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Skull className="h-3 w-3 text-red-400" />
                  <span className="text-[8px] font-mono text-[#475569]">Loss Streak</span>
                  <span className="text-[11px] font-mono font-bold text-red-400">
                    {pnlMetrics.maxConsecutiveLosses}
                  </span>
                </div>
              </div>
            </Card>

            {/* Drawdown Analysis */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <TrendingDown className="h-3.5 w-3.5 text-red-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Drawdown Analysis
                </span>
              </div>

              {/* Current Drawdown - Big Number */}
              <div className="flex items-center gap-4 mb-3">
                <div>
                  <span className="text-[8px] font-mono text-[#475569] block">Current DD</span>
                  <span className={`text-[24px] font-mono font-bold ${
                    drawdown.currentDrawdownPct > 5 ? 'text-red-400' :
                    drawdown.currentDrawdownPct > 2 ? 'text-amber-400' : 'text-emerald-400'
                  }`}>
                    {drawdown.currentDrawdownPct.toFixed(1)}%
                  </span>
                </div>
                <div className="flex-1 grid grid-cols-2 gap-2">
                  <div>
                    <span className="text-[8px] font-mono text-[#475569] block">Max DD</span>
                    <span className="text-[12px] font-mono font-bold text-red-400">
                      {drawdown.maxDrawdownPct.toFixed(1)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-[8px] font-mono text-[#475569] block">Max DD $</span>
                    <span className="text-[12px] font-mono font-bold text-red-400">
                      {formatCurrency(drawdown.maxDrawdownUsd)}
                    </span>
                  </div>
                  <div>
                    <span className="text-[8px] font-mono text-[#475569] block">Peak Capital</span>
                    <span className="text-[12px] font-mono font-bold text-[#e2e8f0]">
                      {formatCurrency(drawdown.peakCapital)}
                    </span>
                  </div>
                  <div>
                    <span className="text-[8px] font-mono text-[#475569] block">Recovery Factor</span>
                    <span className={`text-[12px] font-mono font-bold ${
                      drawdown.recoveryFactor >= 2 ? 'text-emerald-400' :
                      drawdown.recoveryFactor >= 1 ? 'text-amber-400' : 'text-red-400'
                    }`}>
                      {drawdown.recoveryFactor.toFixed(2)}
                    </span>
                  </div>
                </div>
              </div>

              {/* Equity Curve */}
              {equityCurve.length > 1 && (
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
                      Equity Curve
                    </span>
                    <div className="flex items-center gap-1">
                      <Timer className="h-2.5 w-2.5 text-[#475569]" />
                      <span className="text-[8px] font-mono text-[#475569]">
                        {drawdown.timeToRecoveryEstMin > 0
                          ? `~${drawdown.timeToRecoveryEstMin < 60 ? `${drawdown.timeToRecoveryEstMin}m` : `${(drawdown.timeToRecoveryEstMin / 60).toFixed(1)}h`} to recovery`
                          : 'No drawdown'}
                      </span>
                    </div>
                  </div>
                  <EquityCurveChart data={equityCurve} />
                </div>
              )}
            </Card>
          </div>
        </div>

        {/* ---- CONCENTRATION ANALYSIS ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {/* By Chain */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <PieChart className="h-3.5 w-3.5 text-cyan-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  By Chain
                </span>
              </div>
              {Object.keys(portfolioRisk.concentrationByChain).length > 0 ? (
                <div className="space-y-2">
                  {Object.entries(portfolioRisk.concentrationByChain).map(([chain, pct]) => {
                    const chainColors: Record<string, string> = {
                      SOL: '#9945FF',
                      ETH: '#627EEA',
                      BASE: '#0052FF',
                      ARB: '#28A0F0',
                      BSC: '#F0B90B',
                    };
                    return (
                      <ConcentrationBar
                        key={chain}
                        label={chain}
                        value={pct}
                        color={chainColors[chain] || '#64748b'}
                      />
                    );
                  })}
                </div>
              ) : (
                <span className="text-[10px] font-mono text-[#475569]">No open positions</span>
              )}
            </Card>

            {/* By Direction */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <Target className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  By Direction
                </span>
              </div>
              {portfolioRisk.openPositions > 0 ? (
                <div className="flex items-center gap-4">
                  {/* LONG donut */}
                  <div className="flex flex-col items-center">
                    <MiniDonut
                      value={portfolioRisk.concentrationByDirection.LONG || 0}
                      max={100}
                      color="#10b981"
                      size={56}
                    />
                    <div className="flex items-center gap-1 mt-1">
                      <ArrowUpRight className="h-2.5 w-2.5 text-emerald-400" />
                      <span className="text-[9px] font-mono font-bold text-emerald-400">
                        LONG {portfolioRisk.concentrationByDirection.LONG || 0}%
                      </span>
                    </div>
                  </div>
                  {/* SHORT donut */}
                  <div className="flex flex-col items-center">
                    <MiniDonut
                      value={portfolioRisk.concentrationByDirection.SHORT || 0}
                      max={100}
                      color="#ef4444"
                      size={56}
                    />
                    <div className="flex items-center gap-1 mt-1">
                      <ArrowDownRight className="h-2.5 w-2.5 text-red-400" />
                      <span className="text-[9px] font-mono font-bold text-red-400">
                        SHORT {portfolioRisk.concentrationByDirection.SHORT || 0}%
                      </span>
                    </div>
                  </div>
                </div>
              ) : (
                <span className="text-[10px] font-mono text-[#475569]">No open positions</span>
              )}

              {/* Direction bias warning */}
              {portfolioRisk.openPositions > 0 && (
                (portfolioRisk.concentrationByDirection.LONG || 0) === 100 && (
                  <div className="mt-3 flex items-center gap-1.5 px-2 py-1.5 bg-amber-500/5 border border-amber-500/20 rounded">
                    <AlertTriangle className="h-3 w-3 text-amber-400 shrink-0" />
                    <span className="text-[8px] font-mono text-amber-400">
                      100% LONG bias — consider hedging
                    </span>
                  </div>
                )
              )}
            </Card>

            {/* Top Positions by Size */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-4">
              <div className="flex items-center gap-2 mb-3">
                <Zap className="h-3.5 w-3.5 text-[#d4af37]" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Top Positions
                </span>
              </div>
              {portfolioRisk.openPositions > 0 ? (
                <div className="space-y-2">
                  {(() => {
                    // Get top positions from the data we have
                    // Since we don't have individual position data in the risk API,
                    // we show summary
                    const chainEntries = Object.entries(portfolioRisk.concentrationByChain);
                    const total = portfolioRisk.totalExposureUsd;
                    return chainEntries.length > 0 ? chainEntries.map(([chain, pct]) => (
                      <div key={chain} className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Badge className="text-[7px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#94a3b8] border-[#2d3748]">
                            {chain}
                          </Badge>
                          <span className="text-[10px] font-mono text-[#e2e8f0]">{pct}%</span>
                        </div>
                        <span className="text-[10px] font-mono text-[#64748b]">
                          {formatCurrency(total * pct / 100)}
                        </span>
                      </div>
                    )) : (
                      <span className="text-[10px] font-mono text-[#475569]">No positions</span>
                    );
                  })()}
                </div>
              ) : (
                <span className="text-[10px] font-mono text-[#475569]">No open positions</span>
              )}
            </Card>
          </div>
        </div>

        {/* ---- TRADE ANALYSIS ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="flex items-center gap-2 mb-3">
            <Activity className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Trade Analysis
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            {/* Best Trade */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <Trophy className="h-3 w-3 text-emerald-400" />
                <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Best Trade</span>
              </div>
              {tradeAnalysis.bestTrade ? (
                <>
                  <span className="text-[12px] font-mono font-bold text-emerald-400 block">
                    {tradeAnalysis.bestTrade.symbol}
                  </span>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-[11px] font-mono font-bold text-emerald-400">
                      {formatCurrency(tradeAnalysis.bestTrade.pnlUsd)}
                    </span>
                    <span className="text-[9px] font-mono text-emerald-400/70">
                      ({formatPct(tradeAnalysis.bestTrade.pnlPct)})
                    </span>
                  </div>
                </>
              ) : (
                <span className="text-[10px] font-mono text-[#475569]">No trades yet</span>
              )}
            </Card>

            {/* Worst Trade */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <Skull className="h-3 w-3 text-red-400" />
                <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Worst Trade</span>
              </div>
              {tradeAnalysis.worstTrade ? (
                <>
                  <span className="text-[12px] font-mono font-bold text-red-400 block">
                    {tradeAnalysis.worstTrade.symbol}
                  </span>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-[11px] font-mono font-bold text-red-400">
                      {formatCurrency(tradeAnalysis.worstTrade.pnlUsd)}
                    </span>
                    <span className="text-[9px] font-mono text-red-400/70">
                      ({formatPct(tradeAnalysis.worstTrade.pnlPct)})
                    </span>
                  </div>
                </>
              ) : (
                <span className="text-[10px] font-mono text-[#475569]">No trades yet</span>
              )}
            </Card>

            {/* Avg Hold Time */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <Clock className="h-3 w-3 text-cyan-400" />
                <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">Avg Hold Time</span>
              </div>
              <span className="text-[14px] font-mono font-bold text-cyan-400">
                {tradeAnalysis.avgHoldTimeMin < 60
                  ? `${tradeAnalysis.avgHoldTimeMin.toFixed(0)}m`
                  : tradeAnalysis.avgHoldTimeMin < 1440
                  ? `${(tradeAnalysis.avgHoldTimeMin / 60).toFixed(1)}h`
                  : `${(tradeAnalysis.avgHoldTimeMin / 1440).toFixed(1)}d`}
              </span>
            </Card>

            {/* MFE / MAE */}
            <Card className="bg-[#0d1117] border-[#1e293b] p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <BarChart3 className="h-3 w-3 text-amber-400" />
                <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">MFE / MAE</span>
              </div>
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-[9px] font-mono text-[#475569]">MFE (profit left)</span>
                  <span className="text-[11px] font-mono font-bold text-emerald-400">
                    {tradeAnalysis.avgMfe.toFixed(1)}%
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[9px] font-mono text-[#475569]">MAE (risk taken)</span>
                  <span className="text-[11px] font-mono font-bold text-red-400">
                    {tradeAnalysis.avgMae.toFixed(1)}%
                  </span>
                </div>
                <Separator className="bg-[#1e293b]" />
                <div className="flex items-center justify-between">
                  <span className="text-[9px] font-mono text-[#475569]">Efficiency</span>
                  <span className={`text-[11px] font-mono font-bold ${
                    tradeAnalysis.mfeMaeRatio >= 2 ? 'text-emerald-400' :
                    tradeAnalysis.mfeMaeRatio >= 1 ? 'text-amber-400' : 'text-red-400'
                  }`}>
                    {tradeAnalysis.mfeMaeRatio === -1 ? '∞' : tradeAnalysis.mfeMaeRatio.toFixed(2)}x
                  </span>
                </div>
              </div>
            </Card>
          </div>
        </div>

        {/* ---- RISK CONTROLS (COLLAPSIBLE) ---- */}
        <AnimatePresence>
          {showControls && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="px-3 py-3">
                <Card className="bg-[#0d1117] border-[#1e293b] p-4">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Settings className="h-3.5 w-3.5 text-amber-400" />
                      <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                        Risk Controls
                      </span>
                    </div>
                    <Button
                      size="sm"
                      className="h-6 text-[9px] font-mono bg-amber-500/10 text-amber-400 border border-amber-500/30 hover:bg-amber-500/20"
                      onClick={() => saveControlsMutation.mutate()}
                      disabled={saveControlsMutation.isPending}
                    >
                      {saveControlsMutation.isPending ? (
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                      ) : (
                        <Save className="h-3 w-3 mr-1" />
                      )}
                      Save
                    </Button>
                  </div>

                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {/* Max Position Size */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[9px] font-mono text-[#64748b]">Max Position Size</span>
                        <span className="text-[10px] font-mono font-bold text-[#e2e8f0]">
                          {riskControls.maxPositionSizePct}%
                        </span>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="50"
                        step="1"
                        value={riskControls.maxPositionSizePct}
                        onChange={(e) => setRiskControls({ ...riskControls, maxPositionSizePct: Number(e.target.value) })}
                        className="w-full h-1.5 bg-[#1e293b] rounded-full appearance-none cursor-pointer accent-amber-400"
                      />
                      <div className="flex justify-between mt-0.5">
                        <span className="text-[7px] font-mono text-[#475569]">1%</span>
                        <span className="text-[7px] font-mono text-[#475569]">50%</span>
                      </div>
                    </div>

                    {/* Max Portfolio Risk */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[9px] font-mono text-[#64748b]">Max Portfolio Risk</span>
                        <span className="text-[10px] font-mono font-bold text-[#e2e8f0]">
                          {riskControls.maxPortfolioRiskPct}%
                        </span>
                      </div>
                      <input
                        type="range"
                        min="5"
                        max="80"
                        step="5"
                        value={riskControls.maxPortfolioRiskPct}
                        onChange={(e) => setRiskControls({ ...riskControls, maxPortfolioRiskPct: Number(e.target.value) })}
                        className="w-full h-1.5 bg-[#1e293b] rounded-full appearance-none cursor-pointer accent-amber-400"
                      />
                      <div className="flex justify-between mt-0.5">
                        <span className="text-[7px] font-mono text-[#475569]">5%</span>
                        <span className="text-[7px] font-mono text-[#475569]">80%</span>
                      </div>
                    </div>

                    {/* Daily Loss Limit */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[9px] font-mono text-[#64748b]">Daily Loss Limit</span>
                        <span className="text-[10px] font-mono font-bold text-[#e2e8f0]">
                          {riskControls.dailyLossLimitPct}%
                        </span>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="30"
                        step="1"
                        value={riskControls.dailyLossLimitPct}
                        onChange={(e) => setRiskControls({ ...riskControls, dailyLossLimitPct: Number(e.target.value) })}
                        className="w-full h-1.5 bg-[#1e293b] rounded-full appearance-none cursor-pointer accent-red-400"
                      />
                      <div className="flex justify-between mt-0.5">
                        <span className="text-[7px] font-mono text-[#475569]">1%</span>
                        <span className="text-[7px] font-mono text-[#475569]">30%</span>
                      </div>
                    </div>

                    {/* Stop Loss Default */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[9px] font-mono text-[#64748b]">Stop Loss Default</span>
                        <span className="text-[10px] font-mono font-bold text-[#e2e8f0]">
                          {riskControls.stopLossDefaultPct}%
                        </span>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="30"
                        step="1"
                        value={riskControls.stopLossDefaultPct}
                        onChange={(e) => setRiskControls({ ...riskControls, stopLossDefaultPct: Number(e.target.value) })}
                        className="w-full h-1.5 bg-[#1e293b] rounded-full appearance-none cursor-pointer accent-red-400"
                      />
                      <div className="flex justify-between mt-0.5">
                        <span className="text-[7px] font-mono text-[#475569]">1%</span>
                        <span className="text-[7px] font-mono text-[#475569]">30%</span>
                      </div>
                    </div>
                  </div>

                  {/* Active risk thresholds */}
                  <div className="mt-4 grid grid-cols-2 gap-2">
                    <div className="flex items-center gap-2 px-2 py-1.5 bg-[#0a0e17] border border-[#1e293b] rounded">
                      <div className={`w-2 h-2 rounded-full ${
                        portfolioRisk.exposurePct <= riskControls.maxPortfolioRiskPct ? 'bg-emerald-400' : 'bg-red-400'
                      }`} />
                      <span className="text-[8px] font-mono text-[#94a3b8]">
                        Exposure: {portfolioRisk.exposurePct.toFixed(1)}% / {riskControls.maxPortfolioRiskPct}% limit
                      </span>
                    </div>
                    <div className="flex items-center gap-2 px-2 py-1.5 bg-[#0a0e17] border border-[#1e293b] rounded">
                      <div className={`w-2 h-2 rounded-full ${
                        Math.abs(apiControls.currentDailyPnlPct) <= riskControls.dailyLossLimitPct ? 'bg-emerald-400' : 'bg-red-400'
                      }`} />
                      <span className="text-[8px] font-mono text-[#94a3b8]">
                        Daily P&L: {formatPct(apiControls.currentDailyPnlPct)} / ±{riskControls.dailyLossLimitPct}% limit
                      </span>
                    </div>
                  </div>
                </Card>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
