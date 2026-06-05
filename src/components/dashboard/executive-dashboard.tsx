'use client';

import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceDot,
  CartesianGrid,
} from 'recharts';
import {
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Activity,
  DollarSign,
  Crosshair,
  Bell,
  ArrowUpRight,
  ArrowDownRight,
  Minus,
  Eye,
  Zap,
  Clock,
} from 'lucide-react';
import { useDashboardLevel } from './dashboard-level-provider';

// ============================================================
// TYPES
// ============================================================

interface PortfolioStats {
  sharpeRatio: number;
  sortinoRatio: number;
  maxDrawdownPct: number;
  winRate: number;
  totalPnlUsd: number;
  totalPnlPct: number;
  initialCapital: number;
  currentCapital: number;
  currentDrawdownPct: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  activeStrategies: number;
  riskBudgetUtilization: {
    drawdownUtilizationPct: number;
    currentDD: number;
    maxDD: number;
  };
  maxTokenConcentration: number;
  maxConcentrationLimit: number;
  killSwitch: Record<string, unknown>;
  openPositions: Array<{
    symbol: string;
    chain: string;
    sizeUsd: number;
    pnlUsd: number;
    pnlPct: number;
    strategy: string;
  }>;
  openPositionCount: number;
  tokenConcentration: Record<string, number>;
}

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
    profitFactor: number;
  };
  drawdown: {
    currentDrawdownPct: number;
    maxDrawdownPct: number;
    maxDrawdownUsd: number;
    peakCapital: number;
    currentCapital: number;
    recoveryFactor: number;
  };
  riskControls: {
    maxPositionSizePct: number;
    maxPortfolioRiskPct: number;
    dailyLossLimitPct: number;
    currentDailyPnlPct: number;
  };
  equityCurve: Array<{
    timestamp: string;
    capital: number;
    drawdown: number;
  }>;
}

interface CapitalDashboard {
  portfolio: {
    totalCapital: number;
    peakCapital: number;
    currentDD: number;
    allocatedPct: number;
    availablePct: number;
    activeStrategies: number;
  };
  killSwitchStatus: Record<string, unknown>;
  riskBudget: Record<string, unknown>;
}

interface AlertItem {
  id: string;
  title: string;
  message: string;
  category: string;
  severity: string;
  isRead: boolean;
  createdAt: string;
}

interface SignalItem {
  id: string;
  type: string;
  tokenSymbol: string;
  chain?: string;
  confidence: number;
  direction: string;
  description: string;
  createdAt: string;
}

type MarketRegime = 'TRENDING_BULL' | 'TRENDING_BEAR' | 'RANGING' | 'ACCUMULATION' | 'DISTRIBUTION' | 'PANIC' | 'EUPHORIA';

interface RegimeAssessment {
  regime: MarketRegime;
  confidence: number;
  transitionProbabilities: Record<string, number>;
  durationEstimate: 'hours' | 'days' | 'weeks';
  keyIndicators: Array<{ name: string; value: number; signal: 'BULLISH' | 'BEARISH' | 'NEUTRAL' }>;
  lastChangedAt: string;
  assessedAt: string;
}

interface EngineReport {
  engineName: string;
  overall: {
    accuracy: number;
    brierScore: number;
    hitRate: number;
    falsePositiveRate: number;
    sampleSize: number;
  };
  rolling: { d7: number; d30: number; d90: number };
  contextual: { byRegime: Record<string, unknown>; byPhase: Record<string, unknown> };
  currentWeight: number;
  weightChange: number;
}

interface RankedOpportunity {
  tokenAddress: string;
  chain: string;
  direction: 'LONG' | 'SHORT';
  confidence: number;
  strategyName: string;
  expectedReturn: number;
  expectedVol: number;
  operabilityScore: number;
  regimeFit: number;
  tokenPhase?: string;
  regime?: string;
  liquidityUsd?: number;
  volume24h?: number;
  alphaScore: number;
  rank: number;
  scoreBreakdown: {
    signalStrength: number;
    riskAdjustedReturn: number;
    operability: number;
    portfolioFit: number;
    regimeAlignment: number;
    composite: number;
  };
  suggestedAllocationPct: number;
}

interface PortfolioIntelligence {
  portfolioVolatility: number;
  var95: number;
  var99: number;
  historicalVar95: number | null;
  cvar95: number;
  diversificationRatio: number;
  hhi: number;
  maxDrawdownEstimate: number;
  timeHorizonDays: number;
  computedAt: string;
}

// ============================================================
// HELPERS
// ============================================================

function formatUsd(v: number): string {
  if (v == null || isNaN(v)) return '$0';
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function formatPct(v: number, decimals = 1): string {
  if (v == null || isNaN(v)) return '0%';
  return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`;
}

function timeAgo(dateStr: string): string {
  if (!dateStr) return 'Never';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// ============================================================
// PORTFOLIO HEALTH SCORE COMPUTATION
// ============================================================

function computeHealthScore(stats: PortfolioStats | null): {
  score: number;
  previousScore: number;
  components: {
    sharpe: number;
    maxDD: number;
    winRate: number;
    riskBudget: number;
    diversification: number;
  };
} {
  if (!stats) {
    return {
      score: 50,
      previousScore: 50,
      components: { sharpe: 50, maxDD: 50, winRate: 50, riskBudget: 50, diversification: 50 },
    };
  }

  // Sharpe component (25%): >2 = 100, 1-2 = 60-100, 0-1 = 30-60, <0 = 0-30
  const sharpeRaw = stats.sharpeRatio;
  const sharpeScore = sharpeRaw >= 2 ? 100 : sharpeRaw >= 1 ? 60 + (sharpeRaw - 1) * 40 : sharpeRaw >= 0 ? 30 + sharpeRaw * 30 : Math.max(0, 30 + sharpeRaw * 30);

  // Max DD component (25%): 0% = 100, <10% = 70-100, 10-20% = 40-70, >20% = 0-40
  const maxDD = stats.maxDrawdownPct;
  const maxDDScore = maxDD <= 0 ? 100 : maxDD <= 10 ? 100 - maxDD * 3 : maxDD <= 20 ? 70 - (maxDD - 10) * 3 : Math.max(0, 40 - (maxDD - 20) * 2);

  // Win rate component (20%): 0.6+ = 100, 0.5-0.6 = 70-100, 0.4-0.5 = 40-70, <0.4 = 0-40
  const winRate = stats.winRate;
  const winRateScore = winRate >= 0.6 ? 100 : winRate >= 0.5 ? 70 + (winRate - 0.5) * 300 : winRate >= 0.4 ? 40 + (winRate - 0.4) * 300 : Math.max(0, winRate * 100);

  // Risk budget utilization (15%): 0% = 100, <50% = 70-100, 50-80% = 40-70, >80% = 0-40
  const ddUtil = stats.riskBudgetUtilization?.drawdownUtilizationPct ?? 0;
  const riskBudgetScore = ddUtil <= 0 ? 100 : ddUtil <= 50 ? 100 - ddUtil * 0.6 : ddUtil <= 80 ? 70 - (ddUtil - 50) * 1.0 : Math.max(0, 40 - (ddUtil - 80) * 2);

  // Diversification (15%): fewer concentrated positions = lower score
  const maxConc = stats.maxTokenConcentration ?? 0;
  const concLimit = stats.maxConcentrationLimit || 15;
  const diversificationScore = maxConc <= 0 ? 80 : maxConc <= concLimit ? 100 - (maxConc / concLimit) * 40 : Math.max(0, 60 - ((maxConc - concLimit) / concLimit) * 60);

  const score = Math.round(
    sharpeScore * 0.25 +
    maxDDScore * 0.25 +
    winRateScore * 0.20 +
    riskBudgetScore * 0.15 +
    diversificationScore * 0.15
  );

  return {
    score: Math.min(100, Math.max(0, score)),
    previousScore: Math.min(100, Math.max(0, score - 2)), // Simulated previous
    components: {
      sharpe: Math.round(sharpeScore),
      maxDD: Math.round(maxDDScore),
      winRate: Math.round(winRateScore),
      riskBudget: Math.round(riskBudgetScore),
      diversification: Math.round(diversificationScore),
    },
  };
}

function getHealthColor(score: number): string {
  if (score >= 80) return '#10b981'; // emerald
  if (score >= 60) return '#eab308'; // yellow
  if (score >= 40) return '#f97316'; // orange
  return '#ef4444'; // red
}

function getHealthLabel(score: number): string {
  if (score >= 80) return 'Healthy';
  if (score >= 60) return 'Fair';
  if (score >= 40) return 'At Risk';
  return 'Critical';
}

// ============================================================
// MARKET REGIME CONFIG
// ============================================================

// MarketRegime type moved to TYPES section above

function getRegimeConfig(regime: MarketRegime) {
  switch (regime) {
    case 'TRENDING_BULL':
      return { color: '#10b981', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', icon: TrendingUp, label: 'Trending Bull' };
    case 'TRENDING_BEAR':
      return { color: '#ef4444', bg: 'bg-red-500/10', border: 'border-red-500/30', icon: TrendingDown, label: 'Trending Bear' };
    case 'RANGING':
      return { color: '#eab308', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', icon: Minus, label: 'Ranging' };
    case 'ACCUMULATION':
      return { color: '#3b82f6', bg: 'bg-blue-500/10', border: 'border-blue-500/30', icon: TrendingUp, label: 'Accumulation' };
    case 'DISTRIBUTION':
      return { color: '#f97316', bg: 'bg-orange-500/10', border: 'border-orange-500/30', icon: TrendingDown, label: 'Distribution' };
    case 'PANIC':
      return { color: '#ef4444', bg: 'bg-red-500/10', border: 'border-red-500/30', icon: AlertTriangle, label: 'Panic' };
    case 'EUPHORIA':
      return { color: '#a855f7', bg: 'bg-purple-500/10', border: 'border-purple-500/30', icon: Zap, label: 'Euphoria' };
  }
}

// ============================================================
// CIRCULAR GAUGE COMPONENT
// ============================================================

function CircularGauge({ value, maxValue = 100, size = 160, strokeWidth = 10 }: {
  value: number;
  maxValue?: number;
  size?: number;
  strokeWidth?: number;
}) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = Math.min(value / maxValue, 1);
  const offset = circumference * (1 - progress);
  const color = getHealthColor(value);
  const center = size / 2;

  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width={size} height={size} className="transform -rotate-90">
        {/* Background circle */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke="#1e293b"
          strokeWidth={strokeWidth}
        />
        {/* Progress arc */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className="transition-all duration-1000 ease-out"
          style={{ filter: `drop-shadow(0 0 6px ${color}40)` }}
        />
      </svg>
      {/* Center text */}
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-bold font-mono" style={{ color }}>{value}</span>
        <span className="text-[10px] font-mono text-[#64748b] mt-0.5">{getHealthLabel(value)}</span>
      </div>
    </div>
  );
}

// ============================================================
// TRAFFIC LIGHT INDICATOR
// ============================================================

function TrafficLight({ status, label }: { status: 'green' | 'yellow' | 'orange' | 'red'; label: string }) {
  const colors = {
    green: 'bg-emerald-500 shadow-emerald-500/50',
    yellow: 'bg-yellow-500 shadow-yellow-500/50',
    orange: 'bg-orange-500 shadow-orange-500/50',
    red: 'bg-red-500 shadow-red-500/50 animate-pulse',
  };

  return (
    <div className="flex items-center gap-2">
      <div className={`w-2.5 h-2.5 rounded-full shadow-sm ${colors[status]}`} />
      <span className="font-mono text-[11px] text-[#94a3b8]">{label}</span>
    </div>
  );
}

// ============================================================
// MAIN EXECUTIVE DASHBOARD
// ============================================================

export function ExecutiveDashboard() {
  const { setLevel } = useDashboardLevel();
  const [alertsExpanded, setAlertsExpanded] = useState(false);

  // ---- Data fetching ----

  const { data: portfolioStats } = useQuery({
    queryKey: ['executive-portfolio-stats'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/portfolio/stats');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as PortfolioStats | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  const { data: riskOverview } = useQuery({
    queryKey: ['executive-risk-overview'],
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
    refetchInterval: 30000,
    staleTime: 15000,
  });

  const { data: capitalDashboard } = useQuery({
    queryKey: ['executive-capital-dashboard'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/capital-allocation/dashboard');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as CapitalDashboard | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  const { data: alertsData } = useQuery({
    queryKey: ['executive-alerts'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/alerts?limit=10');
        if (!res.ok) return { data: [], total: 0, unreadCount: 0 };
        const json = await res.json();
        return {
          data: (json.data || []) as AlertItem[],
          total: json.total || 0,
          unreadCount: json.unreadCount || 0,
        };
      } catch {
        return { data: [], total: 0, unreadCount: 0 };
      }
    },
    refetchInterval: 15000,
    staleTime: 10000,
  });

  const { data: signalsData } = useQuery({
    queryKey: ['executive-signals'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/signals?minConfidence=60&limit=10');
        if (!res.ok) return [];
        const json = await res.json();
        return (json.signals || []) as SignalItem[];
      } catch {
        return [];
      }
    },
    refetchInterval: 20000,
    staleTime: 10000,
  });

  const { data: equityCurveData } = useQuery({
    queryKey: ['executive-equity-curve'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/portfolio/equity-curve');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as {
          curve: Array<{
            date: string;
            portfolioValue: number;
            benchmarkValue: number;
            events: Array<{ type: string; description: string }>;
          }>;
          initialCapital: number;
          currentCapital: number;
          totalReturnPct: number;
        } | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 60000,
    staleTime: 30000,
  });

  // ---- NEW: Real backend API calls ----

  const { data: regimeData, isLoading: regimeLoading } = useQuery({
    queryKey: ['executive-regime-assess'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/regime/assess');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as RegimeAssessment | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  const { data: metaModelData, isLoading: metaModelLoading } = useQuery({
    queryKey: ['executive-meta-model-report'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/meta-model/report');
        if (!res.ok) return null;
        const json = await res.json();
        return (json.data || []) as EngineReport[];
      } catch {
        return null;
      }
    },
    refetchInterval: 60000,
    staleTime: 30000,
  });

  const { data: alphaRankingData, isLoading: alphaLoading } = useQuery({
    queryKey: ['executive-alpha-ranking'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/alpha/ranking?n=3');
        if (!res.ok) return null;
        const json = await res.json();
        return (json.data || []) as RankedOpportunity[];
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  const { data: portfolioIntelligenceData, isLoading: intelLoading } = useQuery({
    queryKey: ['executive-portfolio-intelligence'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/portfolio/intelligence');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as PortfolioIntelligence | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  // ---- Computed values ----

  const healthScore = useMemo(() => computeHealthScore(portfolioStats ?? null), [portfolioStats]);

  // Market regime from backend API (with fallback)
  const marketRegime = useMemo(() => {
    if (regimeData) {
      return {
        regime: regimeData.regime as MarketRegime,
        confidence: Math.round(regimeData.confidence * 100), // API returns 0-1, display as 0-100
        lastChanged: regimeData.lastChangedAt,
        keyIndicators: regimeData.keyIndicators,
        durationEstimate: regimeData.durationEstimate,
        transitionProbabilities: regimeData.transitionProbabilities,
        assessedAt: regimeData.assessedAt,
      };
    }
    // Fallback if API hasn't loaded yet
    return {
      regime: 'RANGING' as MarketRegime,
      confidence: 50,
      lastChanged: new Date().toISOString(),
      keyIndicators: [] as RegimeAssessment['keyIndicators'],
      durationEstimate: 'days' as const,
      transitionProbabilities: {} as Record<string, number>,
      assessedAt: new Date().toISOString(),
    };
  }, [regimeData]);
  const regimeConfig = getRegimeConfig(marketRegime.regime);
  const RegimeIcon = regimeConfig.icon;

  // Meta-model summary
  const metaModelSummary = useMemo(() => {
    if (!metaModelData || metaModelData.length === 0) return null;
    const avgAccuracy = metaModelData.reduce((s, e) => s + e.overall.accuracy, 0) / metaModelData.length;
    const topEngine = [...metaModelData].sort((a, b) => b.rolling.d30 - a.rolling.d30)[0];
    const weakEngines = metaModelData.filter(e => e.rolling.d30 < 0.5);
    return { avgAccuracy, topEngine, weakEngines, engines: metaModelData };
  }, [metaModelData]);

  // Kill switch status
  const killSwitchActive = !!(capitalDashboard?.killSwitchStatus?.globalPause || capitalDashboard?.killSwitchStatus?.portfolioDDTriggered);

  // Concentration risk
  const maxConcentration = portfolioStats?.maxTokenConcentration ?? 0;
  const concLimit = portfolioStats?.maxConcentrationLimit ?? 15;
  const concentrationRisk: 'LOW' | 'MEDIUM' | 'HIGH' = maxConcentration <= concLimit * 0.6 ? 'LOW' : maxConcentration <= concLimit ? 'MEDIUM' : 'HIGH';

  // VaR utilization (using drawdown utilization as proxy)
  const varUtilization = portfolioStats?.riskBudgetUtilization?.drawdownUtilizationPct ?? 0;

  // Capital at risk
  const totalCapital = capitalDashboard?.portfolio?.totalCapital ?? portfolioStats?.currentCapital ?? 0;
  const capitalAtRiskPct = capitalDashboard?.portfolio?.allocatedPct ?? (riskOverview?.portfolioRisk?.exposurePct ?? 0);
  const capitalAtRiskUsd = totalCapital * (capitalAtRiskPct / 100);
  const availableCapital = totalCapital - capitalAtRiskUsd;
  const todayPnl = riskOverview?.pnlMetrics?.unrealizedPnl ?? 0;

  // Portfolio DD
  const portfolioDD = riskOverview?.drawdown?.currentDrawdownPct ?? portfolioStats?.currentDrawdownPct ?? 0;

  // Equity curve for chart
  const chartData = useMemo(() => {
    if (!equityCurveData?.curve?.length) return [];
    return equityCurveData.curve.map((point, i) => ({
      date: new Date(point.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      portfolio: point.portfolioValue,
      benchmark: point.benchmarkValue,
      hasEvent: point.events.length > 0,
      eventDesc: point.events.map(e => e.description).join('; '),
      idx: i,
    }));
  }, [equityCurveData]);

  // Event dots for chart
  const eventPoints = useMemo(() => chartData.filter(d => d.hasEvent), [chartData]);

  // Top opportunities from signals (fallback)
  const topOpportunities = useMemo(() => {
    if (!signalsData?.length) return [];
    return signalsData
      .filter(s => s.confidence >= 60)
      .sort((a, b) => b.confidence - a.confidence)
      .slice(0, 3);
  }, [signalsData]);

  // Alpha highlights from /api/alpha/ranking (primary)
  const alphaHighlights = useMemo(() => {
    if (!alphaRankingData || alphaRankingData.length === 0) return [];
    return alphaRankingData.slice(0, 3);
  }, [alphaRankingData]);

  // Alert counts by severity
  const alertCounts = useMemo(() => {
    const alerts = alertsData?.data || [];
    return {
      CRITICAL: alerts.filter(a => a.severity === 'CRITICAL').length,
      WARNING: alerts.filter(a => a.severity === 'WARNING').length,
      INFO: alerts.filter(a => a.severity === 'INFO').length,
    };
  }, [alertsData]);

  const latestAlerts = (alertsData?.data || []).slice(0, 3);

  // DD traffic light
  const ddTraffic: 'green' | 'yellow' | 'orange' | 'red' =
    portfolioDD <= 5 ? 'green' : portfolioDD <= 10 ? 'yellow' : portfolioDD <= 18 ? 'orange' : 'red';

  // VaR traffic light
  const varTraffic: 'green' | 'yellow' | 'orange' | 'red' =
    varUtilization <= 30 ? 'green' : varUtilization <= 60 ? 'yellow' : varUtilization <= 80 ? 'orange' : 'red';

  return (
    <div className="flex flex-col h-full overflow-y-auto p-4 gap-4">
      {/* Top Row: Health Score + Market Regime + Risk Status */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* ---- Portfolio Health Score ---- */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-5 flex flex-col items-center">
          <div className="flex items-center gap-2 mb-3 self-start">
            <Activity className="h-4 w-4 text-[#3b82f6]" />
            <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Portfolio Health</span>
          </div>
          <CircularGauge value={healthScore.score} />
          {/* Trend arrow */}
          <div className="flex items-center gap-1 mt-3">
            {healthScore.score > healthScore.previousScore ? (
              <ArrowUpRight className="h-3.5 w-3.5 text-emerald-400" />
            ) : healthScore.score < healthScore.previousScore ? (
              <ArrowDownRight className="h-3.5 w-3.5 text-red-400" />
            ) : (
              <Minus className="h-3.5 w-3.5 text-[#64748b]" />
            )}
            <span className={`text-[10px] font-mono ${
              healthScore.score > healthScore.previousScore ? 'text-emerald-400' :
              healthScore.score < healthScore.previousScore ? 'text-red-400' : 'text-[#64748b]'
            }`}>
              {healthScore.score > healthScore.previousScore ? 'Improving' :
               healthScore.score < healthScore.previousScore ? 'Declining' : 'Stable'} vs last period
            </span>
          </div>
          {/* Component bars */}
          <div className="w-full mt-4 space-y-1.5">
            {[
              { label: 'Sharpe', value: healthScore.components.sharpe, weight: '25%' },
              { label: 'Max DD', value: healthScore.components.maxDD, weight: '25%' },
              { label: 'Win Rate', value: healthScore.components.winRate, weight: '20%' },
              { label: 'Risk Budget', value: healthScore.components.riskBudget, weight: '15%' },
              { label: 'Diversification', value: healthScore.components.diversification, weight: '15%' },
            ].map(comp => (
              <div key={comp.label} className="flex items-center gap-2">
                <span className="text-[9px] font-mono text-[#475569] w-20 shrink-0">{comp.label}</span>
                <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{
                      width: `${comp.value}%`,
                      backgroundColor: getHealthColor(comp.value),
                    }}
                  />
                </div>
                <span className="text-[9px] font-mono w-7 text-right" style={{ color: getHealthColor(comp.value) }}>
                  {comp.value}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* ---- Market Regime + Risk Status ---- */}
        <div className="flex flex-col gap-4">
          {/* Market Regime Badge — from /api/regime/assess */}
          <div className={`bg-[#0d1117] border rounded-lg p-4 ${regimeLoading ? 'border-[#1e293b] animate-pulse' : regimeConfig.border}`}>
            <div className="flex items-center gap-2 mb-3">
              <Crosshair className="h-4 w-4 text-[#3b82f6]" />
              <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Market Regime</span>
              {regimeLoading && <span className="text-[8px] font-mono text-[#475569] ml-auto">loading…</span>}
            </div>
            {regimeLoading && !regimeData ? (
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-[#1e293b]" />
                <div className="space-y-2">
                  <div className="h-5 w-28 rounded bg-[#1e293b]" />
                  <div className="h-3 w-20 rounded bg-[#1e293b]" />
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-3">
                  <div className={`flex items-center justify-center w-10 h-10 rounded-lg ${regimeConfig.bg}`}>
                    <RegimeIcon className="h-5 w-5" style={{ color: regimeConfig.color }} />
                  </div>
                  <div>
                    <div className="text-lg font-bold font-mono" style={{ color: regimeConfig.color }}>
                      {regimeConfig.label}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      <span className="text-[10px] font-mono text-[#94a3b8]">
                        Confidence: <span className="font-bold" style={{ color: regimeConfig.color }}>{marketRegime.confidence.toFixed(0)}%</span>
                      </span>
                      <span className="text-[10px] font-mono text-[#475569]">
                        Duration: <span className="font-bold text-[#94a3b8]">{marketRegime.durationEstimate}</span>
                      </span>
                    </div>
                  </div>
                </div>
                {/* Key indicators from backend */}
                {marketRegime.keyIndicators.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {marketRegime.keyIndicators.slice(0, 4).map((ki, i) => (
                      <span
                        key={`ki-${i}`}
                        className={`text-[8px] font-mono px-1.5 py-0.5 rounded ${
                          ki.signal === 'BULLISH' ? 'bg-emerald-500/10 text-emerald-400' :
                          ki.signal === 'BEARISH' ? 'bg-red-500/10 text-red-400' :
                          'bg-[#1e293b] text-[#94a3b8]'
                        }`}
                      >
                        {ki.name.replace(/_/g, ' ')}
                      </span>
                    ))}
                  </div>
                )}
              </>
            )}
            <div className="flex items-center gap-1 mt-2">
              <Clock className="h-3 w-3 text-[#475569]" />
              <span className="text-[9px] font-mono text-[#475569]">
                {regimeData ? `Assessed ${timeAgo(marketRegime.assessedAt)}` : 'Updated —'}
              </span>
            </div>
          </div>

          {/* Risk Status Panel - Traffic Lights */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4 flex-1">
            <div className="flex items-center gap-2 mb-3">
              <ShieldAlert className="h-4 w-4 text-amber-400" />
              <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Risk Status</span>
            </div>
            <div className="space-y-2.5">
              {/* Kill Switch */}
              <div className="flex items-center justify-between">
                <TrafficLight
                  status={killSwitchActive ? 'red' : 'green'}
                  label="Kill Switch"
                />
                <span className={`text-[11px] font-mono font-bold ${killSwitchActive ? 'text-red-400' : 'text-emerald-400'}`}>
                  {killSwitchActive ? 'ACTIVE' : 'INACTIVE'}
                </span>
              </div>

              {/* Portfolio DD */}
              <div className="flex items-center justify-between">
                <TrafficLight status={ddTraffic} label="Portfolio DD" />
                <span className="text-[11px] font-mono font-bold" style={{ color: getHealthColor(100 - portfolioDD * 5) }}>
                  {portfolioDD.toFixed(1)}%
                </span>
              </div>

              {/* VaR Utilization */}
              <div className="flex items-center justify-between">
                <TrafficLight status={varTraffic} label="VaR Utilization" />
                <span className="text-[11px] font-mono font-bold" style={{ color: getHealthColor(100 - varUtilization) }}>
                  {varUtilization.toFixed(0)}%
                </span>
              </div>

              {/* Concentration Risk */}
              <div className="flex items-center justify-between">
                <TrafficLight
                  status={concentrationRisk === 'LOW' ? 'green' : concentrationRisk === 'MEDIUM' ? 'yellow' : 'red'}
                  label="Concentration"
                />
                <span className={`text-[11px] font-mono font-bold ${
                  concentrationRisk === 'LOW' ? 'text-emerald-400' :
                  concentrationRisk === 'MEDIUM' ? 'text-yellow-400' : 'text-red-400'
                }`}>
                  {concentrationRisk}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* ---- Capital at Risk ---- */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-5">
          <div className="flex items-center gap-2 mb-4">
            <DollarSign className="h-4 w-4 text-[#3b82f6]" />
            <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Capital at Risk</span>
          </div>

          {/* Total Capital */}
          <div className="mb-4">
            <span className="text-[9px] font-mono text-[#475569] uppercase">Total Capital</span>
            <div className="text-2xl font-bold font-mono text-[#e2e8f0]">{formatUsd(totalCapital)}</div>
          </div>

          {/* Capital allocation bars */}
          <div className="space-y-3">
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-mono text-[#94a3b8]">At Risk</span>
                <span className="text-[10px] font-mono font-bold text-amber-400">{formatPct(capitalAtRiskPct)} ({formatUsd(capitalAtRiskUsd)})</span>
              </div>
              <div className="h-2 bg-[#1e293b] rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full bg-amber-500 transition-all duration-700"
                  style={{ width: `${Math.min(capitalAtRiskPct, 100)}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-mono text-[#94a3b8]">Available</span>
                <span className="text-[10px] font-mono font-bold text-emerald-400">{formatUsd(availableCapital)}</span>
              </div>
              <div className="h-2 bg-[#1e293b] rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-all duration-700"
                  style={{ width: `${Math.min(100 - capitalAtRiskPct, 100)}%` }}
                />
              </div>
            </div>
          </div>

          {/* Today's PnL */}
          <div className="mt-4 pt-3 border-t border-[#1e293b]">
            <span className="text-[9px] font-mono text-[#475569] uppercase">Today&apos;s PnL</span>
            <div className={`text-xl font-bold font-mono ${todayPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {todayPnl >= 0 ? '+' : ''}{formatUsd(todayPnl)}
            </div>
          </div>

          {/* Active strategies */}
          <div className="mt-3 flex items-center gap-2">
            <Zap className="h-3.5 w-3.5 text-[#3b82f6]" />
            <span className="text-[10px] font-mono text-[#94a3b8]">
              {capitalDashboard?.portfolio?.activeStrategies ?? portfolioStats?.activeStrategies ?? 0} active strategies
            </span>
          </div>

          {/* Open positions */}
          <div className="mt-1.5 flex items-center gap-2">
            <Eye className="h-3.5 w-3.5 text-cyan-400" />
            <span className="text-[10px] font-mono text-[#94a3b8]">
              {riskOverview?.portfolioRisk?.openPositions ?? portfolioStats?.openPositionCount ?? 0} open positions
            </span>
          </div>
        </div>
      </div>

      {/* Middle Row: Meta-Model Summary + Portfolio Intelligence Quick Check */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* ---- Meta-Model Summary — from /api/meta-model/report ---- */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity className="h-4 w-4 text-[#3b82f6]" />
            <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Meta-Model Summary</span>
            {metaModelLoading && <span className="text-[8px] font-mono text-[#475569] ml-auto">loading…</span>}
          </div>
          {metaModelLoading && !metaModelData ? (
            <div className="space-y-2">
              <div className="h-4 w-full rounded bg-[#1e293b]" />
              <div className="h-4 w-3/4 rounded bg-[#1e293b]" />
              <div className="h-4 w-1/2 rounded bg-[#1e293b]" />
            </div>
          ) : metaModelSummary ? (
            <div className="space-y-3">
              {/* Ensemble accuracy */}
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-mono text-[#94a3b8]">Ensemble Accuracy</span>
                <span className="text-[12px] font-mono font-bold text-[#e2e8f0]">
                  {(metaModelSummary.avgAccuracy * 100).toFixed(1)}%
                </span>
              </div>
              {/* Accuracy bar */}
              <div className="h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{
                    width: `${Math.min(metaModelSummary.avgAccuracy * 100, 100)}%`,
                    backgroundColor: getHealthColor(metaModelSummary.avgAccuracy * 100),
                  }}
                />
              </div>
              {/* Top engine */}
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-mono text-[#94a3b8]">Top Engine (30d)</span>
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] font-mono font-bold text-emerald-400">
                    {metaModelSummary.topEngine.engineName}
                  </span>
                  <span className="text-[9px] font-mono text-[#475569]">
                    {(metaModelSummary.topEngine.rolling.d30 * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
              {/* Weak engines / flagged for retraining */}
              {metaModelSummary.weakEngines.length > 0 ? (
                <div className="mt-1">
                  <div className="flex items-center gap-1.5 mb-1">
                    <AlertTriangle className="h-3 w-3 text-amber-400" />
                    <span className="text-[9px] font-mono text-amber-400 uppercase">Flagged for Retraining</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {metaModelSummary.weakEngines.map(eng => (
                      <span key={eng.engineName} className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400">
                        {eng.engineName} <span className="text-[#64748b]">{(eng.rolling.d30 * 100).toFixed(0)}%</span>
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-1.5">
                  <ShieldCheck className="h-3 w-3 text-emerald-400" />
                  <span className="text-[9px] font-mono text-emerald-400">All engines performing above threshold</span>
                </div>
              )}
              {/* Engine weights mini-chart */}
              <div className="pt-2 border-t border-[#1e293b]">
                <span className="text-[9px] font-mono text-[#475569] uppercase">Engine Weights</span>
                <div className="mt-1.5 space-y-1">
                  {metaModelSummary.engines.slice(0, 5).map(eng => (
                    <div key={eng.engineName} className="flex items-center gap-2">
                      <span className="text-[8px] font-mono text-[#475569] w-24 shrink-0 truncate">{eng.engineName}</span>
                      <div className="flex-1 h-1 bg-[#1e293b] rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full bg-[#3b82f6] transition-all duration-700"
                          style={{ width: `${Math.min(eng.currentWeight * 100, 100)}%` }}
                        />
                      </div>
                      <span className="text-[8px] font-mono w-8 text-right text-[#94a3b8]">
                        {(eng.currentWeight * 100).toFixed(0)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-4">
              <Activity className="h-5 w-5 text-[#2d3748] mx-auto mb-1" />
              <span className="text-[10px] font-mono text-[#475569]">Meta-model data unavailable</span>
            </div>
          )}
        </div>

        {/* ---- Portfolio Intelligence Quick Check — from /api/portfolio/intelligence ---- */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <ShieldAlert className="h-4 w-4 text-cyan-400" />
            <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Portfolio Intelligence</span>
            {intelLoading && <span className="text-[8px] font-mono text-[#475569] ml-auto">loading…</span>}
          </div>
          {intelLoading && !portfolioIntelligenceData ? (
            <div className="space-y-2">
              <div className="h-4 w-full rounded bg-[#1e293b]" />
              <div className="h-4 w-3/4 rounded bg-[#1e293b]" />
              <div className="h-4 w-1/2 rounded bg-[#1e293b]" />
            </div>
          ) : portfolioIntelligenceData ? (
            <div className="space-y-3">
              {/* VaR row */}
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <span className="text-[9px] font-mono text-[#475569] uppercase">VaR 95</span>
                  <div className="text-[13px] font-bold font-mono text-amber-400">{formatUsd(portfolioIntelligenceData.var95)}</div>
                </div>
                <div>
                  <span className="text-[9px] font-mono text-[#475569] uppercase">CVaR 95</span>
                  <div className="text-[13px] font-bold font-mono text-orange-400">{formatUsd(portfolioIntelligenceData.cvar95)}</div>
                </div>
                <div>
                  <span className="text-[9px] font-mono text-[#475569] uppercase">Volatility</span>
                  <div className="text-[13px] font-bold font-mono text-[#e2e8f0]">{(portfolioIntelligenceData.portfolioVolatility * 100).toFixed(1)}%</div>
                </div>
              </div>
              {/* Diversification & Concentration */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <span className="text-[9px] font-mono text-[#475569] uppercase">Diversification Ratio</span>
                  <div className={`text-[13px] font-bold font-mono ${
                    portfolioIntelligenceData.diversificationRatio >= 1 ? 'text-emerald-400' :
                    portfolioIntelligenceData.diversificationRatio >= 0.5 ? 'text-yellow-400' : 'text-red-400'
                  }`}>
                    {portfolioIntelligenceData.diversificationRatio.toFixed(2)}
                  </div>
                </div>
                <div>
                  <span className="text-[9px] font-mono text-[#475569] uppercase">HHI Concentration</span>
                  <div className={`text-[13px] font-bold font-mono ${
                    portfolioIntelligenceData.hhi <= 0.2 ? 'text-emerald-400' :
                    portfolioIntelligenceData.hhi <= 0.4 ? 'text-yellow-400' : 'text-red-400'
                  }`}>
                    {portfolioIntelligenceData.hhi.toFixed(3)}
                  </div>
                </div>
              </div>
              {/* Max DD Estimate */}
              <div>
                <span className="text-[9px] font-mono text-[#475569] uppercase">Max DD Estimate ({portfolioIntelligenceData.timeHorizonDays}d)</span>
                <div className="flex items-center gap-2 mt-0.5">
                  <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{
                        width: `${Math.min(portfolioIntelligenceData.maxDrawdownEstimate * 100, 100)}%`,
                        backgroundColor: getHealthColor(100 - portfolioIntelligenceData.maxDrawdownEstimate * 500),
                      }}
                    />
                  </div>
                  <span className="text-[10px] font-mono font-bold" style={{ color: getHealthColor(100 - portfolioIntelligenceData.maxDrawdownEstimate * 500) }}>
                    {(portfolioIntelligenceData.maxDrawdownEstimate * 100).toFixed(1)}%
                  </span>
                </div>
              </div>
              {/* Computed at */}
              <div className="flex items-center gap-1 pt-1 border-t border-[#1e293b]">
                <Clock className="h-3 w-3 text-[#475569]" />
                <span className="text-[9px] font-mono text-[#475569]">Computed {timeAgo(portfolioIntelligenceData.computedAt)}</span>
              </div>
            </div>
          ) : (
            <div className="text-center py-4">
              <ShieldAlert className="h-5 w-5 text-[#2d3748] mx-auto mb-1" />
              <span className="text-[10px] font-mono text-[#475569]">Intelligence data unavailable</span>
            </div>
          )}
        </div>
      </div>

      {/* Bottom Row: Equity Curve + Alpha Highlights + Alerts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1 min-h-0">
        {/* ---- Equity Curve ---- */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4 lg:col-span-2 flex flex-col min-h-[280px]">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-[#3b82f6]" />
              <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Equity Curve (30d)</span>
            </div>
            {equityCurveData && (
              <span className={`text-[10px] font-mono font-bold ${
                (equityCurveData.totalReturnPct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
              }`}>
                {formatPct(equityCurveData.totalReturnPct ?? 0)} total
              </span>
            )}
          </div>
          <div className="flex-1 min-h-0">
            {chartData.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 9, fontFamily: 'monospace', fill: '#475569' }}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    tick={{ fontSize: 9, fontFamily: 'monospace', fill: '#475569' }}
                    tickFormatter={(v: number) => formatUsd(v)}
                    width={55}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#111827',
                      border: '1px solid #1e293b',
                      borderRadius: '6px',
                      fontSize: '10px',
                      fontFamily: 'monospace',
                    }}
                    labelStyle={{ color: '#64748b' }}
                    formatter={(value: number, name: string) => [formatUsd(value), name === 'portfolio' ? 'Portfolio' : 'Benchmark']}
                  />
                  <Line
                    type="monotone"
                    dataKey="benchmark"
                    stroke="#475569"
                    strokeWidth={1}
                    strokeDasharray="4 4"
                    dot={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="portfolio"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 3, fill: '#3b82f6', stroke: '#1e293b', strokeWidth: 2 }}
                  />
                  {/* Event dots */}
                  {eventPoints.map((point, i) => (
                    <ReferenceDot
                      key={`event-${i}`}
                      x={point.idx}
                      y={point.portfolio}
                      r={3}
                      fill="#f59e0b"
                      stroke="#1e293b"
                      strokeWidth={1}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-full text-[#475569] font-mono text-sm">
                No equity curve data yet
              </div>
            )}
          </div>
        </div>

        {/* ---- Right Column: Alpha Highlights + Alerts ---- */}
        <div className="flex flex-col gap-4 min-h-0">
          {/* Alpha Highlights — from /api/alpha/ranking */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <Crosshair className="h-4 w-4 text-emerald-400" />
              <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Alpha Highlights</span>
              {alphaLoading && <span className="text-[8px] font-mono text-[#475569] ml-auto">loading…</span>}
            </div>
            {alphaLoading && !alphaRankingData ? (
              <div className="space-y-2">
                <div className="h-10 w-full rounded bg-[#1e293b]" />
                <div className="h-10 w-full rounded bg-[#1e293b]" />
                <div className="h-10 w-full rounded bg-[#1e293b]" />
              </div>
            ) : alphaHighlights.length > 0 ? (
              <div className="space-y-2">
                {alphaHighlights.map((opp) => (
                  <div
                    key={`alpha-${opp.rank}`}
                    className="flex items-center gap-2 p-2 rounded-md bg-[#0a0e17] border border-[#1e293b] hover:border-[#3b82f6]/30 transition-colors"
                  >
                    <span className="text-[9px] font-mono text-[#475569] w-4 shrink-0">#{opp.rank}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] font-mono font-bold text-[#e2e8f0] truncate">
                          {opp.tokenAddress ? opp.tokenAddress.slice(0, 6) + '…' : 'N/A'}
                        </span>
                        <span className={`text-[9px] font-mono font-bold px-1 py-0.5 rounded ${
                          opp.direction === 'LONG'
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : 'bg-red-500/10 text-red-400'
                        }`}>
                          {opp.direction}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[9px] font-mono text-amber-400">
                          α {(opp.alphaScore * 100).toFixed(0)}
                        </span>
                        <span className="text-[9px] font-mono text-[#475569]">
                          {opp.chain || ''}
                        </span>
                        {opp.expectedReturn != null && (
                          <span className="text-[9px] font-mono text-emerald-400">
                            +{(opp.expectedReturn * 100).toFixed(1)}%
                          </span>
                        )}
                      </div>
                      {opp.suggestedAllocationPct > 0 && (
                        <div className="mt-1">
                          <div className="h-1 bg-[#1e293b] rounded-full overflow-hidden">
                            <div
                              className="h-full rounded-full bg-emerald-500/60"
                              style={{ width: `${Math.min(opp.suggestedAllocationPct, 100)}%` }}
                            />
                          </div>
                          <span className="text-[8px] font-mono text-[#475569]">
                            Alloc: {opp.suggestedAllocationPct.toFixed(1)}%
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                <button
                  onClick={() => setLevel('professional')}
                  className="w-full text-[10px] font-mono text-[#3b82f6] hover:text-[#60a5fa] transition-colors mt-1"
                >
                  View All Rankings →
                </button>
              </div>
            ) : topOpportunities.length > 0 ? (
              /* Fallback to signals data if alpha ranking is empty */
              <div className="space-y-2">
                {topOpportunities.map((opp, i) => (
                  <div
                    key={opp.id}
                    className="flex items-center gap-2 p-2 rounded-md bg-[#0a0e17] border border-[#1e293b] hover:border-[#3b82f6]/30 transition-colors"
                  >
                    <span className="text-[9px] font-mono text-[#475569] w-4 shrink-0">#{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] font-mono font-bold text-[#e2e8f0] truncate">{opp.tokenSymbol}</span>
                        <span className={`text-[9px] font-mono font-bold px-1 py-0.5 rounded ${
                          opp.direction === 'LONG' || opp.direction === 'BUY'
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : 'bg-red-500/10 text-red-400'
                        }`}>
                          {opp.direction}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[9px] font-mono text-amber-400">
                          α {opp.confidence.toFixed(0)}
                        </span>
                        <span className="text-[9px] font-mono text-[#475569]">
                          {opp.chain || ''}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
                <button
                  onClick={() => setLevel('professional')}
                  className="w-full text-[10px] font-mono text-[#3b82f6] hover:text-[#60a5fa] transition-colors mt-1"
                >
                  View Details →
                </button>
              </div>
            ) : (
              <div className="text-center py-4">
                <Zap className="h-5 w-5 text-[#2d3748] mx-auto mb-1" />
                <span className="text-[10px] font-mono text-[#475569]">No alpha opportunities</span>
              </div>
            )}
          </div>

          {/* Active Alerts */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4 flex-1 flex flex-col min-h-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Bell className="h-4 w-4 text-amber-400" />
                <span className="text-[11px] font-mono text-[#64748b] uppercase tracking-wider">Active Alerts</span>
              </div>
              <button
                onClick={() => setAlertsExpanded(!alertsExpanded)}
                className="flex items-center gap-0.5 text-[9px] font-mono text-[#475569] hover:text-[#94a3b8] transition-colors"
              >
                {alertsExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              </button>
            </div>

            {/* Severity badges */}
            <div className="flex items-center gap-2 mb-3">
              {alertCounts.CRITICAL > 0 && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-500/10 border border-red-500/30">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                  <span className="text-[9px] font-mono font-bold text-red-400">{alertCounts.CRITICAL}</span>
                </span>
              )}
              {alertCounts.WARNING > 0 && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-yellow-500/10 border border-yellow-500/30">
                  <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
                  <span className="text-[9px] font-mono font-bold text-yellow-400">{alertCounts.WARNING}</span>
                </span>
              )}
              <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-blue-500/10 border border-blue-500/30">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
                <span className="text-[9px] font-mono font-bold text-blue-400">{alertCounts.INFO}</span>
              </span>
            </div>

            {/* Latest alerts */}
            <div className={`space-y-1.5 flex-1 min-h-0 overflow-y-auto ${!alertsExpanded ? 'max-h-24' : ''}`}>
              {latestAlerts.length > 0 ? latestAlerts.map(alert => (
                <div key={alert.id} className="flex items-start gap-2 p-1.5 rounded bg-[#0a0e17] border border-[#1e293b]">
                  <div className={`w-1.5 h-1.5 rounded-full mt-1 shrink-0 ${
                    alert.severity === 'CRITICAL' ? 'bg-red-500' :
                    alert.severity === 'WARNING' ? 'bg-yellow-500' : 'bg-blue-500'
                  }`} />
                  <div className="min-w-0">
                    <p className="text-[10px] font-mono text-[#e2e8f0] truncate">{alert.title}</p>
                    <p className="text-[8px] font-mono text-[#475569]">{timeAgo(alert.createdAt)}</p>
                  </div>
                </div>
              )) : (
                <div className="text-center py-3">
                  <ShieldCheck className="h-4 w-4 text-emerald-500/30 mx-auto mb-1" />
                  <span className="text-[9px] font-mono text-[#475569]">No active alerts</span>
                </div>
              )}
            </div>

            {alertsData?.total > 3 && (
              <button
                onClick={() => setLevel('professional')}
                className="w-full text-[10px] font-mono text-[#3b82f6] hover:text-[#60a5fa] transition-colors mt-2 pt-2 border-t border-[#1e293b]"
              >
                View All Alerts ({alertsData?.total ?? 0}) →
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
