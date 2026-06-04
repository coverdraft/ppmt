'use client';

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Area,
  AreaChart,
  BarChart,
  Bar,
  Cell,
} from 'recharts';
import {
  Brain,
  Play,
  Pause,
  Square,
  RefreshCw,
  Activity,
  Clock,
  AlertTriangle,
  DollarSign,
  Zap,
  Loader2,
  ArrowUpRight,
  ArrowDownRight,
  TrendingUp,
  ChevronRight,
  Radio,
  Eye,
  Timer,
  BarChart3,
  Globe,
  Gauge,
  Cpu,
  Database,
  CheckCircle2,
  XCircle,
  Flame,
  Target,
  Shield,
  ArrowRight,
  RotateCcw,
  Sparkles,
  CircleDot,
  Layers,
  GitBranch,
  Trophy,
  Swords,
  CircleCheck,
  CircleX,
  HardDrive,
} from 'lucide-react';

// ============================================================
// TYPES
// ============================================================

interface TaskStatus {
  name: string;
  intervalMs: number;
  lastRunAt: string | null;
  nextRunAt: string | null;
  runCount: number;
  errorCount: number;
  lastError: string | null;
  lastDurationMs: number;
  isRunning: boolean;
}

interface SchedulerStatusReport {
  status: 'STOPPED' | 'STARTING' | 'RUNNING' | 'PAUSED' | 'ERROR';
  uptime: number;
  config: {
    capitalUsd: number;
    initialCapitalUsd: number;
    chain: string;
    cycleIntervalMs: number;
    [key: string]: any;
  };
  tasks: TaskStatus[];
  brainCycle: {
    status: string;
    config: any;
    lastCycleResult?: any;
    cyclesCompleted: number;
    currentCycleNumber: number;
    capitalSummary: {
      totalCapital: number;
      initialCapital: number;
      currentPnl: number;
      growthPct: number;
    };
    [key: string]: any;
  };
  capitalStrategy: {
    totalCapital: number;
    initialCapital: number;
    currentPnl: number;
    growthPct: number;
    [key: string]: any;
  };
  totalCyclesCompleted: number;
  lastError: string | null;
}

interface GrowthRecord {
  id: string;
  capitalUsd: number;
  initialCapitalUsd: number;
  totalReturnPct: number;
  totalPnlUsd: number;
  periodPnlUsd: number;
  periodReturnPct: number;
  totalFeesPaidUsd: number;
  totalSlippageUsd: number;
  feeAdjustedPnlUsd: number;
  feeAdjustedReturnPct: number;
  dailyCompoundRate: number;
  projectedAnnualReturn: number;
  winRate: number;
  sharpeRatio: number;
  period: string;
  measuredAt: string;
}

interface CycleRun {
  id: string;
  cycleNumber: number;
  status: string;
  tokensScanned: number;
  tokensOperable: number;
  tokensTradeable: number;
  capitalBeforeCycle: number;
  capitalAfterCycle: number;
  cyclePnlUsd: number;
  cumulativeReturnPct: number;
  dominantRegime: string;
  createdAt: string;
  completedAt: string | null;
  cycleDurationMs: number;
  errorLog: string | null;
  operabilitySummary: string;
}

interface ActivityEvent {
  id: string;
  type: 'cycle_completed' | 'signal_generated' | 'error' | 'status_changed';
  timestamp: number;
  description: string;
  meta?: Record<string, any>;
}

interface MarketContextData {
  regime: string;
  volatilityRegime: string;
  fearGreedIndex: number;
  btcPrice: number;
  ethPrice: number;
  btcChange24h: number;
  ethChange24h: number;
  totalMarketCap: number;
  volume24h: number;
  signalBreakdown: Record<string, number>;
  tokenCount: number;
  signalCount: number;
  chains: string[];
  lastUpdated: string | null;
}

// ─── NEW TYPES: Capacity, Loops, Phase Strategy ───

interface DataCategoryMetrics {
  name: string;
  count: number;
  minReady: number;
  minCapable: number;
  minOptimal: number;
  fillPct: number;
  level: string;
  color: string;
  icon: string;
  sizeKB: number;
}

interface StorageMetrics {
  dbFileSizeMB: number;
  rawDataSizeKB: number;
  analyzedDataSizeKB: number;
  processMemoryMB: number;
  rssMemoryMB: number;
  systemTotalMemoryMB: number;
  systemFreeMemoryMB: number;
  systemMemoryUsagePct: number;
  tableSizes: Array<{
    table: string;
    records: number;
    estimatedKB: number;
  }>;
}

interface CapacityReport {
  level: 'DORMANT' | 'GATHERING' | 'READY' | 'CAPABLE' | 'OPTIMAL';
  overallScore: number;
  rawInfoScore: number;
  analyzedInfoScore: number;
  strongAnalysisReadiness: number;
  metrics: {
    rawInfo: DataCategoryMetrics[];
    analyzedInfo: DataCategoryMetrics[];
    totalRawRecords: number;
    totalAnalyzedRecords: number;
  };
  storage: StorageMetrics;
  analysisCapabilities: {
    basicSignals: boolean;
    dnaAnalysis: boolean;
    backtesting: boolean;
    predictiveModeling: boolean;
    phaseStrategy: boolean;
    syntheticGeneration: boolean;
    walkForward: boolean;
    autonomousTrading: boolean;
  };
  collectionRate: {
    tokensPerHour: number;
    candlesPerHour: number;
    signalsPerHour: number;
    analysisPerHour: number;
  };
  nextLevelRequirements: string[];
  estimatedTimeToNextLevel: string;
  capacityHistory: Array<{
    timestamp: string;
    score: number;
    level: string;
    rawScore: number;
    analyzedScore: number;
  }>;
}

type TradingStage = 'EARLY' | 'MID' | 'STABLE';

interface LoopIteration {
  iterationNumber: number;
  status: string;
  systemName: string;
  stage: TradingStage;
  originalSharpe: number;
  refinedSharpe: number;
  improvementPct: number;
  adopted: boolean;
  weakPhases: string[];
  refinementType: string;
  comparisonSummary: string;
}

interface LoopReport {
  loopId: string;
  status: string;
  totalIterations: number;
  iterations: LoopIteration[];
  summary: {
    systemsImproved: number;
    systemsDegraded: number;
    totalSyntheticGenerated: number;
    bestImprovement: number;
    averageImprovement: number;
  };
  stageResults: Record<TradingStage, {
    iterations: number;
    improved: number;
    avgImprovement: number;
    bestSharpe: number;
  }>;
}

interface LoopStatusData {
  isRunning: boolean;
  loopsCompleted: number;
  currentLoop: LoopReport | null;
  config: {
    maxIterations: number;
    minImprovementPct: number;
    autoGenerateSynthetic: boolean;
    autoAdopt: boolean;
    loopIntervalMs: number;
  };
  history: Array<{
    loopId: string;
    completedAt: string;
    totalIterations: number;
    systemsImproved: number;
    bestImprovement: number;
  }>;
}

interface StageStrategy {
  stage: TradingStage;
  name: string;
  description: string;
  riskProfile: string;
  focusAreas: string[];
  parameters: {
    stopLossPct: number;
    takeProfitPct: number;
    trailingStopPct: number;
    maxPositionPct: number;
    maxOpenPositions: number;
    confidenceThreshold: number;
    [key: string]: any;
  };
  entryWeights: {
    momentum: number;
    volume: number;
    smartMoney: number;
    botActivity: number;
    liquidity: number;
    volatility: number;
    dna: number;
    regime: number;
  };
  color: string;
  icon: string;
}

interface StrategyMatchResult {
  symbol: string;
  chain: string;
  stage: TradingStage;
  matchScore: number;
  matchReason: string;
  phase: string;
  phaseConfidence: number;
}

interface PhaseStrategyReport {
  strategies: StageStrategy[];
  tokenDistribution: Record<TradingStage, number>;
  phaseDistribution: Record<string, number>;
  topOpportunities: StrategyMatchResult[];
  recommendations: string[];
  stages: Record<TradingStage, {
    stage: TradingStage;
    tokensInStage: number;
    activeSystems: number;
    totalBacktests: number;
    avgSharpe: number;
    avgWinRate: number;
    bestPnlPct: number;
    totalTrades: number;
    improvementTrend: number;
  }>;
}

// ============================================================
// HELPERS
// ============================================================

function formatUptime(ms: number): string {
  if (ms <= 0) return '0s';
  const seconds = Math.floor(ms / 1000) % 60;
  const minutes = Math.floor(ms / 60000) % 60;
  const hours = Math.floor(ms / 3600000) % 24;
  const days = Math.floor(ms / 86400000);
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatInterval(ms: number): string {
  if (ms < 60000) return `${Math.round(ms / 1000)}s`;
  if (ms < 3600000) {
    const mins = Math.floor(ms / 60000);
    return `${mins}m`;
  }
  const hrs = Math.floor(ms / 3600000);
  const mins = Math.round((ms % 3600000) / 60000);
  return mins > 0 ? `${hrs}h ${mins}m` : `${hrs}h`;
}

function formatCurrency(val: number): string {
  if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(2)}K`;
  return `$${val.toFixed(2)}`;
}

function formatPct(val: number): string {
  if (Math.abs(val) >= 1000) return `${val.toFixed(0)}%`;
  if (Math.abs(val) >= 100) return `${val.toFixed(1)}%`;
  return `${val.toFixed(2)}%`;
}

function timeAgo(iso: string | null): string {
  if (!iso) return 'Never';
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 5000) return 'just now';
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function formatTaskName(name: string): string {
  return name
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (s) => s.toUpperCase())
    .trim();
}

function getTaskStatus(task: TaskStatus): 'running' | 'idle' | 'error' {
  if (task.isRunning) return 'running';
  if (task.errorCount > 0 && task.lastError) return 'error';
  return 'idle';
}

function formatCompactPrice(price: number): string {
  if (price >= 1000) return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  if (price >= 1) return `$${price.toFixed(2)}`;
  return `$${price.toFixed(4)}`;
}

function formatMktCap(val: number): string {
  if (val >= 1e12) return `$${(val / 1e12).toFixed(2)}T`;
  if (val >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  return `$${(val / 1e6).toFixed(0)}M`;
}

function getFgColor(value: number): string {
  if (value <= 25) return 'text-red-400';
  if (value <= 45) return 'text-orange-400';
  if (value <= 55) return 'text-yellow-400';
  if (value <= 75) return 'text-emerald-400';
  return 'text-emerald-300';
}

function getRegimeBadge(regime: string): string {
  switch (regime) {
    case 'BULL': return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
    case 'BEAR': return 'bg-red-500/10 text-red-400 border-red-500/30';
    case 'SIDEWAYS': return 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30';
    case 'TRANSITION': return 'bg-purple-500/10 text-purple-400 border-purple-500/30';
    default: return 'bg-gray-500/10 text-gray-400 border-gray-500/30';
  }
}

function getCapacityLevelBadge(level: string): string {
  switch (level) {
    case 'OPTIMAL': return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40';
    case 'CAPABLE': return 'bg-cyan-500/15 text-cyan-400 border-cyan-500/40';
    case 'READY': return 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40';
    case 'GATHERING': return 'bg-orange-500/15 text-orange-400 border-orange-500/40';
    case 'DORMANT': return 'bg-red-500/15 text-red-400 border-red-500/40';
    default: return 'bg-gray-500/10 text-gray-400 border-gray-500/30';
  }
}

function getCapacityScoreColor(score: number): string {
  if (score >= 75) return '#10b981';
  if (score >= 50) return '#06b6d4';
  if (score >= 25) return '#f59e0b';
  if (score >= 5) return '#f97316';
  return '#ef4444';
}

const PIPELINE_STAGES = [
  { key: 'SCAN', label: 'SCAN', color: '#06b6d4', field: 'tokensScanned' },
  { key: 'FILTER', label: 'FILTER', color: '#f59e0b', field: 'tokensOperable' },
  { key: 'MATCH', label: 'MATCH', color: '#8b5cf6', field: 'tokensTradeable' },
  { key: 'STORE', label: 'STORE', color: '#10b981', field: 'snapshotsStored' },
  { key: 'FEEDBACK', label: 'FEEDBACK', color: '#ec4899', field: null },
  { key: 'GROWTH', label: 'GROWTH', color: '#d4af37', field: null },
] as const;

const CHAIN_COLORS: Record<string, string> = {
  SOL: '#9945FF',
  ETH: '#627EEA',
  BASE: '#0052FF',
  BSC: '#F3BA2F',
  MATIC: '#8247E5',
  ARB: '#28A0F0',
  OP: '#FF0420',
  AVAX: '#E84142',
};

const SIGNAL_COLORS: Record<string, string> = {
  REGIME_CHANGE: '#f59e0b',
  BOT_SWARM: '#ef4444',
  WHALE_MOVEMENT: '#8b5cf6',
  LIQUIDITY_DRAIN: '#06b6d4',
  CORRELATION_BREAK: '#ec4899',
  SMART_MONEY_POSITIONING: '#10b981',
  VOLATILITY_REGIME: '#f97316',
};

const CAPABILITY_LABELS: Record<string, { label: string; icon: string }> = {
  basicSignals: { label: 'Basic Signals', icon: '📡' },
  dnaAnalysis: { label: 'DNA Analysis', icon: '🧬' },
  backtesting: { label: 'Backtesting', icon: '🧪' },
  predictiveModeling: { label: 'Predictive Model', icon: '🔮' },
  phaseStrategy: { label: 'Phase Strategy', icon: '🎯' },
  syntheticGeneration: { label: 'Synthetic Gen', icon: '⚙️' },
  walkForward: { label: 'Walk Forward', icon: '📊' },
  autonomousTrading: { label: 'Autonomous', icon: '🤖' },
};

const STAGE_DISPLAY: Record<TradingStage, { label: string; color: string; icon: any; desc: string }> = {
  EARLY: { label: 'Early Stage', color: '#ef4444', icon: Flame, desc: 'High volatility, bot-dominated' },
  MID: { label: 'Mid Stage', color: '#f59e0b', icon: Target, desc: 'Smart money active, growing' },
  STABLE: { label: 'Stable Stage', color: '#10b981', icon: Shield, desc: 'Established tokens, conservative' },
};

// ============================================================
// BRAIN DASHBOARD COMPONENT
// ============================================================

export default function BrainDashboard() {
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [loopActionLoading, setLoopActionLoading] = useState<string | null>(null);
  const [activityFeed, setActivityFeed] = useState<ActivityEvent[]>([]);
  const [now, setNow] = useState(Date.now());
  const feedEndRef = useRef<HTMLDivElement>(null);

  // Ticker cada segundo
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // ─── API QUERIES ───

  // Scheduler status (5s)
  const { data: schedulerData, isLoading: schedulerLoading } = useQuery({
    queryKey: ['brain-scheduler'],
    queryFn: async () => {
      const res = await fetch('/api/brain/scheduler');
      if (!res.ok) throw new Error('Failed to fetch scheduler');
      const json = await res.json();
      return json.data as SchedulerStatusReport;
    },
    refetchInterval: 15000,
    staleTime: 10000,
  });

  // Growth history (10s)
  const { data: growthData } = useQuery({
    queryKey: ['brain-growth'],
    queryFn: async () => {
      const res = await fetch('/api/brain/growth?limit=50');
      if (!res.ok) throw new Error('Failed to fetch growth data');
      const json = await res.json();
      return json.data as {
        growthHistory: GrowthRecord[];
        recentCycles: CycleRun[];
        operabilityDistribution: Record<string, number>;
      };
    },
    refetchInterval: 30000,
    staleTime: 20000,
  });

  // Market context (30s)
  const { data: marketContext } = useQuery({
    queryKey: ['brain-market-context'],
    queryFn: async () => {
      const res = await fetch('/api/market/context');
      if (!res.ok) throw new Error('Failed to fetch market context');
      const json = await res.json();
      return json.data as MarketContextData | null;
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  // ─── NEW: Brain Capacity (5s) ───
  const { data: capacityData } = useQuery({
    queryKey: ['brain-capacity'],
    queryFn: async () => {
      const res = await fetch('/api/brain/capacity');
      if (!res.ok) throw new Error('Failed to fetch capacity');
      const json = await res.json();
      return json.data as CapacityReport;
    },
    refetchInterval: 15000,
    staleTime: 10000,
  });

  // ─── NEW: Backtest Loops (15s) ───
  const { data: loopData, refetch: refetchLoops } = useQuery({
    queryKey: ['brain-loops'],
    queryFn: async () => {
      const res = await fetch('/api/brain/loops');
      if (!res.ok) throw new Error('Failed to fetch loops');
      const json = await res.json();
      return json.data as LoopStatusData;
    },
    refetchInterval: 15000,
    staleTime: 10000,
  });

  // ─── NEW: Phase Strategy (30s) ───
  const { data: phaseData } = useQuery({
    queryKey: ['brain-phase-strategy'],
    queryFn: async () => {
      const res = await fetch('/api/brain/phase-strategy');
      if (!res.ok) throw new Error('Failed to fetch phase strategy');
      const json = await res.json();
      return json.data as PhaseStrategyReport;
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  // ─── ACTIVITY FEED ───
  useEffect(() => {
    if (!growthData?.recentCycles) return;
    const events: ActivityEvent[] = [];

    for (const cycle of growthData.recentCycles) {
      if (cycle.status === 'COMPLETED') {
        events.push({
          id: `cycle-${cycle.id}`,
          type: 'cycle_completed',
          timestamp: new Date(cycle.completedAt || cycle.createdAt).getTime(),
          description: `Cycle #${cycle.cycleNumber} completed: ${cycle.tokensScanned} scanned, ${cycle.tokensOperable} operable`,
          meta: { pnl: cycle.cyclePnlUsd, regime: cycle.dominantRegime },
        });
      } else if (cycle.status === 'FAILED') {
        events.push({
          id: `error-${cycle.id}`,
          type: 'error',
          timestamp: new Date(cycle.createdAt).getTime(),
          description: `Cycle #${cycle.cycleNumber} failed: ${cycle.errorLog || 'Unknown error'}`,
        });
      }
      if (cycle.tokensTradeable > 0) {
        events.push({
          id: `signal-${cycle.id}`,
          type: 'signal_generated',
          timestamp: new Date(cycle.createdAt).getTime() + 1,
          description: `${cycle.tokensTradeable} tradeable signals generated`,
        });
      }
    }

    if (schedulerData?.status) {
      events.push({
        id: 'scheduler-status',
        type: 'status_changed',
        timestamp: Date.now(),
        description: `Brain scheduler status: ${schedulerData.status}`,
      });
    }

    events.sort((a, b) => b.timestamp - a.timestamp);
    setActivityFeed(events.slice(0, 30));
  }, [growthData?.recentCycles, schedulerData?.status]);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activityFeed]);

  // ─── ACTIONS ───

  const sendAction = useCallback(async (action: 'start' | 'stop' | 'pause' | 'resume') => {
    setActionLoading(action);
    try {
      const res = await fetch('/api/brain/scheduler', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `Action failed: HTTP ${res.status}`);
      }
    } catch (err: any) {
      console.error('Scheduler action error:', err.message);
    } finally {
      setActionLoading(null);
    }
  }, []);

  const sendLoopAction = useCallback(async (action: 'start' | 'stop' | 'run') => {
    setLoopActionLoading(action);
    try {
      const res = await fetch('/api/brain/loops', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `Loop action failed: HTTP ${res.status}`);
      }
      await refetchLoops();
    } catch (err: any) {
      console.error('Loop action error:', err.message);
    } finally {
      setLoopActionLoading(null);
    }
  }, [refetchLoops]);

  // ─── DERIVED DATA ───

  const status = schedulerData?.status || 'STOPPED';
  const isRunning = status === 'RUNNING';
  const lastCycle = schedulerData?.brainCycle?.lastCycleResult;
  const capital = schedulerData?.brainCycle?.capitalSummary || schedulerData?.capitalStrategy;

  const chartData = useMemo(() => {
    if (!growthData?.growthHistory) return [];
    return growthData.growthHistory.map((g) => ({
      time: new Date(g.measuredAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      capital: g.capitalUsd,
      pnl: g.feeAdjustedPnlUsd,
      returnPct: g.totalReturnPct,
    }));
  }, [growthData?.growthHistory]);

  const operDist = growthData?.operabilityDistribution || lastCycle?.operabilityDistribution || { PREMIUM: 0, GOOD: 0, MARGINAL: 0, RISKY: 0, UNOPERABLE: 0 };

  const pipelineCounts: Record<string, number> = {
    SCAN: lastCycle?.tokensScanned ?? 0,
    FILTER: lastCycle?.tokensOperable ?? 0,
    MATCH: lastCycle?.tokensTradeable ?? 0,
    STORE: lastCycle?.snapshotsStored ?? 0,
    FEEDBACK: schedulerData?.brainCycle?.cyclesCompleted ?? 0,
    GROWTH: capital ? 1 : 0,
  };

  const dominantRegime = lastCycle?.dominantRegime || marketContext?.regime || 'UNKNOWN';
  const lastGrowth = growthData?.growthHistory?.[growthData.growthHistory.length - 1];

  const perChainTokenCounts: Record<string, number> = useMemo(() => {
    if (lastCycle?.perChainTokenCounts) return lastCycle.perChainTokenCounts;
    if (growthData?.recentCycles) {
      for (const cycle of growthData.recentCycles) {
        if (cycle.operabilitySummary) {
          try {
            const parsed = JSON.parse(cycle.operabilitySummary);
            if (parsed.perChainTokenCounts) return parsed.perChainTokenCounts;
          } catch { /* skip */ }
        }
      }
    }
    return {};
  }, [lastCycle, growthData?.recentCycles]);

  const signalBreakdown = marketContext?.signalBreakdown || {};

  const nextCycleCountdown = useMemo(() => {
    if (!isRunning || !schedulerData?.brainCycle?.config?.cycleIntervalMs) return null;
    const cycleTask = schedulerData.tasks?.find(t => t.name === 'brain_cycle');
    if (!cycleTask?.nextRunAt) return null;
    const nextRun = new Date(cycleTask.nextRunAt).getTime();
    const remaining = Math.max(0, nextRun - now);
    if (remaining <= 0) return '0s';
    const secs = Math.floor(remaining / 1000) % 60;
    const mins = Math.floor(remaining / 60000);
    return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  }, [isRunning, schedulerData, now]);

  const sharpeDisplay = useMemo(() => {
    if (!lastGrowth) return 'N/A';
    if (lastGrowth.sharpeRatio !== undefined && lastGrowth.sharpeRatio !== 0) {
      return lastGrowth.sharpeRatio.toFixed(2);
    }
    return 'N/A';
  }, [lastGrowth]);

  // Capacity chart data
  const capacityChartData = useMemo(() => {
    if (!capacityData?.capacityHistory?.length) return [];
    return capacityData.capacityHistory.map((h) => ({
      time: new Date(h.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      overall: h.score,
      raw: h.rawScore,
      analyzed: h.analyzedScore,
    }));
  }, [capacityData?.capacityHistory]);

  // Raw info bar chart data
  const rawInfoChartData = useMemo(() => {
    if (!capacityData?.metrics?.rawInfo) return [];
    return capacityData.metrics.rawInfo.map((m) => ({
      name: m.name,
      count: m.count,
      minReady: m.minReady,
      minCapable: m.minCapable,
      minOptimal: m.minOptimal,
      fillPct: m.fillPct,
    }));
  }, [capacityData?.metrics?.rawInfo]);

  // Analyzed info bar chart data
  const analyzedInfoChartData = useMemo(() => {
    if (!capacityData?.metrics?.analyzedInfo) return [];
    return capacityData.metrics.analyzedInfo.map((m) => ({
      name: m.name,
      count: m.count,
      minReady: m.minReady,
      minCapable: m.minCapable,
      minOptimal: m.minOptimal,
      fillPct: m.fillPct,
    }));
  }, [capacityData?.metrics?.analyzedInfo]);

  // Phase token distribution chart data
  const tokenDistChartData = useMemo(() => {
    if (!phaseData?.tokenDistribution) return [];
    return (Object.entries(phaseData.tokenDistribution) as [TradingStage, number][]).map(([stage, count]) => ({
      name: stage,
      count,
      fill: STAGE_DISPLAY[stage]?.color || '#64748b',
    }));
  }, [phaseData?.tokenDistribution]);

  // ─── LOADING STATE ───
  if (schedulerLoading && !schedulerData) {
    return (
      <div className="flex items-center justify-center h-full bg-[#0d1117] border border-[#1e293b] rounded-lg">
        <div className="flex flex-col items-center gap-3">
          <Brain className="h-8 w-8 text-[#d4af37] animate-pulse" />
          <span className="text-[#64748b] font-mono text-sm">Initializing Brain Dashboard...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">

      {/* ═══════════════════════════════════════════════════════════
          SECTION 1: BRAIN STATUS BAR (TOP)
          ═══════════════════════════════════════════════════════════ */}
      <div className="shrink-0 border-b border-[#1e293b] bg-[#0d1117] p-3">
        <div className="flex items-center gap-4 flex-wrap">
          {/* Status Indicator */}
          <div className="flex items-center gap-2.5">
            <div className={`relative flex items-center justify-center w-10 h-10 rounded-full border-2 ${
              isRunning
                ? 'border-emerald-500/60 bg-emerald-500/10'
                : status === 'PAUSED'
                  ? 'border-amber-500/60 bg-amber-500/10'
                  : 'border-gray-500/40 bg-gray-500/10'
            }`}>
              <Brain className={`h-5 w-5 ${
                isRunning ? 'text-emerald-400' : status === 'PAUSED' ? 'text-amber-400' : 'text-gray-400'
              }`} />
              {isRunning && (
                <span className="absolute inset-0 rounded-full border-2 border-emerald-400/40 animate-ping" />
              )}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className={`text-sm font-mono font-bold ${
                  isRunning ? 'text-emerald-400' : status === 'PAUSED' ? 'text-amber-400' : 'text-gray-400'
                }`}>
                  {status}
                </span>
                {isRunning && (
                  <span className="flex items-center gap-1 px-1.5 py-0.5 bg-emerald-500/10 border border-emerald-500/30 rounded text-[9px] font-mono text-emerald-400">
                    <Radio className="h-2 w-2 animate-pulse" /> LIVE
                  </span>
                )}
              </div>
              <span className="text-[10px] font-mono text-[#64748b]">
                Uptime: {formatUptime(schedulerData?.uptime ?? 0)}
              </span>
            </div>
          </div>

          <div className="h-8 w-px bg-[#1e293b]" />

          {/* Cycles & Capital */}
          <div className="flex items-center gap-4">
            <div className="text-center">
              <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Cycles</div>
              <div className="text-lg font-mono font-bold text-cyan-400">
                {schedulerData?.totalCyclesCompleted ?? 0}
              </div>
            </div>
            <div className="text-center">
              <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Capital</div>
              <div className="text-lg font-mono font-bold text-[#e2e8f0]">
                {formatCurrency(capital?.totalCapital ?? schedulerData?.config?.capitalUsd ?? 0)}
                <span className="text-xs text-[#64748b] ml-1">
                  ({formatCurrency(capital?.initialCapital ?? schedulerData?.config?.initialCapitalUsd ?? 0)})
                </span>
              </div>
            </div>
            <div className="text-center">
              <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Return</div>
              <div className={`text-lg font-mono font-bold flex items-center gap-1 ${
                (capital?.growthPct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
              }`}>
                {(capital?.growthPct ?? 0) >= 0 ? (
                  <ArrowUpRight className="h-4 w-4" />
                ) : (
                  <ArrowDownRight className="h-4 w-4" />
                )}
                {formatPct(capital?.growthPct ?? 0)}
              </div>
            </div>
          </div>

          <div className="h-8 w-px bg-[#1e293b]" />

          {/* Regime */}
          <div className="text-center">
            <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Regime</div>
            <span className={`text-xs font-mono font-bold px-2 py-0.5 rounded border ${getRegimeBadge(dominantRegime)}`}>
              {dominantRegime}
            </span>
          </div>

          {/* Next Cycle Countdown */}
          {isRunning && nextCycleCountdown && (
            <>
              <div className="h-8 w-px bg-[#1e293b]" />
              <div className="text-center">
                <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Next Cycle</div>
                <div className="flex items-center gap-1">
                  <Timer className="h-3 w-3 text-cyan-400" />
                  <span className="text-xs font-mono font-bold text-cyan-400">{nextCycleCountdown}</span>
                </div>
              </div>
            </>
          )}

          <div className="flex-1" />

          {/* Market Context Summary in Status Bar */}
          {marketContext && (
            <>
              <div className="hidden md:flex items-center gap-3">
                <div className="flex items-center gap-1">
                  <span className="text-[#f59e0b] font-mono text-[9px] font-bold">BTC</span>
                  <span className="text-[10px] text-[#e2e8f0] font-mono">{formatCompactPrice(marketContext.btcPrice)}</span>
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-[#627eea] font-mono text-[9px] font-bold">ETH</span>
                  <span className="text-[10px] text-[#e2e8f0] font-mono">{formatCompactPrice(marketContext.ethPrice)}</span>
                </div>
                <div className="flex items-center gap-0.5">
                  <span className="text-[#64748b] font-mono text-[8px]">F&G</span>
                  <span className={`text-[9px] font-bold font-mono ${getFgColor(marketContext.fearGreedIndex)}`}>
                    {marketContext.fearGreedIndex}
                  </span>
                </div>
                <div className="flex items-center gap-0.5">
                  <span className="text-[#64748b] font-mono text-[8px]">VOL</span>
                  <span className={`text-[9px] font-mono font-bold ${
                    marketContext.volatilityRegime === 'EXTREME' ? 'text-red-400' :
                    marketContext.volatilityRegime === 'HIGH' ? 'text-orange-400' :
                    marketContext.volatilityRegime === 'NORMAL' ? 'text-yellow-400' : 'text-emerald-400'
                  }`}>
                    {marketContext.volatilityRegime}
                  </span>
                </div>
              </div>
              <div className="h-8 w-px bg-[#1e293b] hidden md:block" />
            </>
          )}

          {/* Control Buttons */}
          <div className="flex items-center gap-1.5">
            {status === 'STOPPED' ? (
              <button
                onClick={() => sendAction('start')}
                disabled={actionLoading !== null}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 rounded text-[10px] font-mono font-bold hover:bg-emerald-600/30 disabled:opacity-40 transition-colors"
              >
                {actionLoading === 'start' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                START
              </button>
            ) : (
              <>
                <button
                  onClick={() => sendAction(status === 'PAUSED' ? 'resume' : 'pause')}
                  disabled={actionLoading !== null}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-[10px] font-mono font-bold disabled:opacity-40 transition-colors ${
                    status === 'PAUSED'
                      ? 'bg-cyan-600/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-600/30'
                      : 'bg-amber-600/20 text-amber-400 border border-amber-500/30 hover:bg-amber-600/30'
                  }`}
                >
                  {actionLoading === 'pause' || actionLoading === 'resume' ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : status === 'PAUSED' ? (
                    <Play className="h-3 w-3" />
                  ) : (
                    <Pause className="h-3 w-3" />
                  )}
                  {status === 'PAUSED' ? 'RESUME' : 'PAUSE'}
                </button>
                <button
                  onClick={() => sendAction('stop')}
                  disabled={actionLoading !== null}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600/20 text-red-400 border border-red-500/30 rounded text-[10px] font-mono font-bold hover:bg-red-600/30 disabled:opacity-40 transition-colors"
                >
                  {actionLoading === 'stop' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />}
                  STOP
                </button>
              </>
            )}
          </div>
        </div>

        {/* Last error */}
        {schedulerData?.lastError && (
          <div className="mt-2 flex items-center gap-2 px-2 py-1 rounded bg-red-500/5 border border-red-500/20">
            <AlertTriangle className="h-3 w-3 text-red-400 shrink-0" />
            <span className="text-[9px] font-mono text-red-400 line-clamp-1">{schedulerData.lastError}</span>
          </div>
        )}
      </div>

      {/* ═══════════════════════════════════════════════════════════
          SCROLLABLE MIDDLE CONTENT
          ═══════════════════════════════════════════════════════════ */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">

        {/* ═══════════════════════════════════════════════════════════
            SECTION 2: BRAIN CAPACITY PANEL (NEW - CRITICAL)
            ═══════════════════════════════════════════════════════════ */}
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Cpu className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[10px] font-mono text-[#d4af37] uppercase tracking-wider font-bold">
              Brain Capacity
            </span>
            {capacityData && (
              <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border ml-1 ${getCapacityLevelBadge(capacityData.level)}`}>
                {capacityData.level}
              </span>
            )}
          </div>
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3 space-y-3">
            {capacityData ? (
              <>
                {/* Top row: Score gauge + Readiness + Level */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {/* Overall Score Gauge */}
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3 flex flex-col items-center">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-2">Overall Capacity Score</span>
                    <div className="relative w-28 h-28">
                      <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
                        <circle cx="50" cy="50" r="42" fill="none" stroke="#1e293b" strokeWidth="8" />
                        <circle
                          cx="50" cy="50" r="42" fill="none"
                          stroke={getCapacityScoreColor(capacityData.overallScore)}
                          strokeWidth="8"
                          strokeDasharray={`${capacityData.overallScore * 2.64} 264`}
                          strokeLinecap="round"
                          className="transition-all duration-1000"
                        />
                      </svg>
                      <div className="absolute inset-0 flex flex-col items-center justify-center">
                        <span className="text-2xl font-mono font-bold" style={{ color: getCapacityScoreColor(capacityData.overallScore) }}>
                          {capacityData.overallScore}
                        </span>
                        <span className="text-[8px] font-mono text-[#64748b]">/ 100</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 mt-2">
                      <div className="text-center">
                        <div className="text-[8px] font-mono text-[#64748b]">RAW</div>
                        <div className="text-[10px] font-mono font-bold text-cyan-400">{capacityData.rawInfoScore}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-[8px] font-mono text-[#64748b]">ANALYZED</div>
                        <div className="text-[10px] font-mono font-bold text-purple-400">{capacityData.analyzedInfoScore}</div>
                      </div>
                    </div>
                  </div>

                  {/* Strong Analysis Readiness */}
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Strong Analysis Readiness</span>
                    <div className="mt-2">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] font-mono text-[#94a3b8]">Readiness</span>
                        <span className="text-[10px] font-mono font-bold" style={{ color: getCapacityScoreColor(capacityData.strongAnalysisReadiness) }}>
                          {capacityData.strongAnalysisReadiness}%
                        </span>
                      </div>
                      <div className="h-3 bg-[#1a1f2e] rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-1000"
                          style={{
                            width: `${capacityData.strongAnalysisReadiness}%`,
                            background: `linear-gradient(90deg, ${getCapacityScoreColor(capacityData.strongAnalysisReadiness)}, ${getCapacityScoreColor(capacityData.strongAnalysisReadiness)}88)`,
                          }}
                        />
                      </div>
                    </div>

                    {/* Collection Rate */}
                    <div className="mt-3 pt-2 border-t border-[#1e293b]">
                      <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Collection Rate</span>
                      <div className="grid grid-cols-2 gap-1.5 mt-1.5">
                        <div className="bg-[#0d1117] rounded p-1.5">
                          <div className="text-[8px] font-mono text-[#475569]">Tokens/hr</div>
                          <div className="text-[10px] font-mono font-bold text-[#e2e8f0]">{capacityData.collectionRate.tokensPerHour}</div>
                        </div>
                        <div className="bg-[#0d1117] rounded p-1.5">
                          <div className="text-[8px] font-mono text-[#475569]">Candles/hr</div>
                          <div className="text-[10px] font-mono font-bold text-[#e2e8f0]">{capacityData.collectionRate.candlesPerHour}</div>
                        </div>
                        <div className="bg-[#0d1117] rounded p-1.5">
                          <div className="text-[8px] font-mono text-[#475569]">Signals/hr</div>
                          <div className="text-[10px] font-mono font-bold text-[#e2e8f0]">{capacityData.collectionRate.signalsPerHour}</div>
                        </div>
                        <div className="bg-[#0d1117] rounded p-1.5">
                          <div className="text-[8px] font-mono text-[#475569]">Analysis/hr</div>
                          <div className="text-[10px] font-mono font-bold text-[#e2e8f0]">{capacityData.collectionRate.analysisPerHour}</div>
                        </div>
                      </div>
                    </div>

                    {/* Estimated Time to Next Level */}
                    <div className="mt-2 pt-2 border-t border-[#1e293b]">
                      <div className="flex items-center gap-1">
                        <Clock className="h-3 w-3 text-[#64748b]" />
                        <span className="text-[8px] font-mono text-[#64748b]">Next Level Est.</span>
                      </div>
                      <div className="text-[10px] font-mono font-bold text-cyan-400 mt-0.5">
                        {capacityData.estimatedTimeToNextLevel}
                      </div>
                    </div>
                  </div>

                  {/* Analysis Capabilities Grid */}
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Analysis Capabilities</span>
                    <div className="grid grid-cols-2 gap-1.5 mt-2">
                      {Object.entries(capacityData.analysisCapabilities).map(([key, enabled]) => {
                        const info = CAPABILITY_LABELS[key] || { label: key, icon: '❓' };
                        return (
                          <div
                            key={key}
                            className={`flex items-center gap-1.5 px-2 py-1.5 rounded border text-[9px] font-mono ${
                              enabled
                                ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-400'
                                : 'border-red-500/20 bg-red-500/5 text-red-400'
                            }`}
                          >
                            {enabled ? (
                              <CircleCheck className="h-3 w-3 shrink-0" />
                            ) : (
                              <CircleX className="h-3 w-3 shrink-0" />
                            )}
                            <span className="truncate">{info.label}</span>
                          </div>
                        );
                      })}
                    </div>

                    {/* Next Level Requirements */}
                    {capacityData.nextLevelRequirements.length > 0 && (
                      <div className="mt-3 pt-2 border-t border-[#1e293b]">
                        <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Next Level Requirements</span>
                        <div className="mt-1 max-h-24 overflow-y-auto space-y-0.5">
                          {capacityData.nextLevelRequirements.map((req, i) => (
                            <div key={i} className="text-[8px] font-mono text-[#94a3b8] flex items-start gap-1">
                              <ArrowRight className="h-2 w-2 mt-0.5 shrink-0 text-[#64748b]" />
                              <span>{req}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Info Pura vs Info Analizada - Side by side bar charts */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                  {/* Info Pura (Raw) */}
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[9px] font-mono text-cyan-400 uppercase tracking-wider font-bold flex items-center gap-1">
                        <Database className="h-3 w-3" /> Info Pura (Raw Data)
                      </span>
                      <span className="text-[9px] font-mono text-[#475569]">
                        {capacityData.metrics.totalRawRecords} records | {capacityData.storage?.rawDataSizeKB ? (capacityData.storage.rawDataSizeKB > 1024 ? `${(capacityData.storage.rawDataSizeKB / 1024).toFixed(1)} MB` : `${capacityData.storage.rawDataSizeKB} KB`) : '...'}
                      </span>
                    </div>
                    <div className="space-y-1.5">
                      {capacityData.metrics.rawInfo.map((cat) => (
                        <div key={cat.name}>
                          <div className="flex items-center justify-between mb-0.5">
                            <span className="text-[8px] font-mono text-[#94a3b8]">{cat.name}</span>
                            <span className="text-[8px] font-mono text-[#e2e8f0]">{cat.count} / {cat.minOptimal} <span className="text-[#475569]">({cat.sizeKB > 1024 ? `${(cat.sizeKB / 1024).toFixed(1)} MB` : `${cat.sizeKB} KB`})</span></span>
                          </div>
                          <div className="flex h-2 bg-[#1a1f2e] rounded-full overflow-hidden">
                            {/* Fill to READY threshold */}
                            <div
                              className="transition-all duration-700"
                              style={{
                                width: `${Math.min(100, (cat.count / cat.minOptimal) * 100)}%`,
                                backgroundColor: cat.color,
                                opacity: 0.8,
                              }}
                            />
                          </div>
                          <div className="flex items-center gap-1 mt-0.5">
                            <span className="text-[7px] font-mono text-[#475569]">
                              READY: {cat.minReady}
                            </span>
                            <span className="text-[7px] font-mono text-[#475569]">|</span>
                            <span className="text-[7px] font-mono text-[#475569]">
                              CAPABLE: {cat.minCapable}
                            </span>
                            <span className="text-[7px] font-mono text-[#475569]">|</span>
                            <span className="text-[7px] font-mono text-[#475569]">
                              OPTIMAL: {cat.minOptimal}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Info Analizada (Analyzed) */}
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[9px] font-mono text-purple-400 uppercase tracking-wider font-bold flex items-center gap-1">
                        <Sparkles className="h-3 w-3" /> Info Analizada (Analyzed)
                      </span>
                      <span className="text-[9px] font-mono text-[#475569]">
                        {capacityData.metrics.totalAnalyzedRecords} records | {capacityData.storage?.analyzedDataSizeKB ? (capacityData.storage.analyzedDataSizeKB > 1024 ? `${(capacityData.storage.analyzedDataSizeKB / 1024).toFixed(1)} MB` : `${capacityData.storage.analyzedDataSizeKB} KB`) : '...'}
                      </span>
                    </div>
                    <div className="space-y-1.5">
                      {capacityData.metrics.analyzedInfo.map((cat) => (
                        <div key={cat.name}>
                          <div className="flex items-center justify-between mb-0.5">
                            <span className="text-[8px] font-mono text-[#94a3b8]">{cat.name}</span>
                            <span className="text-[8px] font-mono text-[#e2e8f0]">{cat.count} / {cat.minOptimal} <span className="text-[#475569]">({cat.sizeKB > 1024 ? `${(cat.sizeKB / 1024).toFixed(1)} MB` : `${cat.sizeKB} KB`})</span></span>
                          </div>
                          <div className="flex h-2 bg-[#1a1f2e] rounded-full overflow-hidden">
                            <div
                              className="transition-all duration-700"
                              style={{
                                width: `${Math.min(100, (cat.count / cat.minOptimal) * 100)}%`,
                                backgroundColor: cat.color,
                                opacity: 0.8,
                              }}
                            />
                          </div>
                          <div className="flex items-center gap-1 mt-0.5">
                            <span className="text-[7px] font-mono text-[#475569]">
                              READY: {cat.minReady}
                            </span>
                            <span className="text-[7px] font-mono text-[#475569]">|</span>
                            <span className="text-[7px] font-mono text-[#475569]">
                              CAPABLE: {cat.minCapable}
                            </span>
                            <span className="text-[7px] font-mono text-[#475569]">|</span>
                            <span className="text-[7px] font-mono text-[#475569]">
                              OPTIMAL: {cat.minOptimal}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Storage & Memory Panel */}
                {capacityData.storage && (
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider font-bold flex items-center gap-1">
                      <HardDrive className="h-3 w-3" /> Storage & Memory
                    </span>
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mt-2">
                      <div className="bg-[#0d1117] rounded p-2 border border-[#1e293b]">
                        <div className="text-[7px] font-mono text-[#64748b] uppercase">DB File</div>
                        <div className="text-[11px] font-mono font-bold text-[#e2e8f0]">{capacityData.storage.dbFileSizeMB} MB</div>
                      </div>
                      <div className="bg-[#0d1117] rounded p-2 border border-[#1e293b]">
                        <div className="text-[7px] font-mono text-[#64748b] uppercase">Total Data</div>
                        <div className="text-[11px] font-mono font-bold text-cyan-400">
                          {((capacityData.storage.rawDataSizeKB + capacityData.storage.analyzedDataSizeKB) > 1024)
                            ? `${((capacityData.storage.rawDataSizeKB + capacityData.storage.analyzedDataSizeKB) / 1024).toFixed(1)} MB`
                            : `${capacityData.storage.rawDataSizeKB + capacityData.storage.analyzedDataSizeKB} KB`}
                        </div>
                      </div>
                      <div className="bg-[#0d1117] rounded p-2 border border-[#1e293b]">
                        <div className="text-[7px] font-mono text-[#64748b] uppercase">Process RAM</div>
                        <div className="text-[11px] font-mono font-bold text-[#f59e0b]">{capacityData.storage.processMemoryMB} MB</div>
                        <div className="text-[7px] font-mono text-[#475569]">RSS: {capacityData.storage.rssMemoryMB} MB</div>
                      </div>
                      <div className="bg-[#0d1117] rounded p-2 border border-[#1e293b]">
                        <div className="text-[7px] font-mono text-[#64748b] uppercase">System RAM</div>
                        <div className="text-[11px] font-mono font-bold text-[#22c55e]">{capacityData.storage.systemMemoryUsagePct}%</div>
                        <div className="text-[7px] font-mono text-[#475569]">{capacityData.storage.systemFreeMemoryMB} / {capacityData.storage.systemTotalMemoryMB} MB free</div>
                      </div>
                    </div>
                    {/* Per-table size breakdown */}
                    <div className="mt-2 flex flex-wrap gap-1">
                      {capacityData.storage.tableSizes.map(ts => (
                        <div key={ts.table} className="bg-[#0d1117] rounded px-2 py-0.5 border border-[#1e293b] flex items-center gap-1">
                          <span className="text-[7px] font-mono text-[#94a3b8]">{ts.table}</span>
                          <span className="text-[7px] font-mono text-[#64748b]">{ts.records}r</span>
                          <span className="text-[7px] font-mono text-cyan-400">{ts.estimatedKB > 1024 ? `${(ts.estimatedKB / 1024).toFixed(1)}MB` : `${ts.estimatedKB}KB`}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Capacity History Chart */}
                {capacityChartData.length > 1 && (
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Capacity History</span>
                    <div className="h-24 mt-1">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={capacityChartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                          <defs>
                            <linearGradient id="capGrad" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%" stopColor="#d4af37" stopOpacity={0.2} />
                              <stop offset="95%" stopColor="#d4af37" stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                          <XAxis dataKey="time" tick={{ fill: '#475569', fontSize: 8, fontFamily: 'monospace' }} tickLine={false} axisLine={{ stroke: '#1e293b' }} />
                          <YAxis domain={[0, 100]} tick={{ fill: '#475569', fontSize: 8, fontFamily: 'monospace' }} tickLine={false} axisLine={{ stroke: '#1e293b' }} width={30} />
                          <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: 6, fontSize: 9, fontFamily: 'monospace' }} />
                          <Area type="monotone" dataKey="overall" stroke="#d4af37" strokeWidth={1.5} fill="url(#capGrad)" name="Overall" />
                          <Line type="monotone" dataKey="raw" stroke="#06b6d4" strokeWidth={1} strokeDasharray="3 2" dot={false} name="Raw" />
                          <Line type="monotone" dataKey="analyzed" stroke="#8b5cf6" strokeWidth={1} strokeDasharray="3 2" dot={false} name="Analyzed" />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="flex items-center justify-center h-20 text-[#475569] font-mono text-[10px]">
                No capacity data yet — start the brain to begin measuring
              </div>
            )}
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════
            SECTION 3: BACKTEST LOOP PANEL (NEW)
            ═══════════════════════════════════════════════════════════ */}
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <GitBranch className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider font-bold">
              Backtest Loop Engine
            </span>
            {loopData && (
              <>
                <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border ml-1 ${
                  loopData.isRunning
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                    : 'bg-gray-500/10 text-gray-400 border-gray-500/30'
                }`}>
                  {loopData.isRunning ? 'RUNNING' : 'STOPPED'}
                </span>
                <span className="text-[9px] font-mono text-[#475569] ml-1">
                  {loopData.loopsCompleted} loops completed
                </span>
              </>
            )}
          </div>
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3 space-y-3">
            {loopData ? (
              <>
                {/* Stage Results - 3 Columns */}
                <div className="grid grid-cols-3 gap-2">
                  {(['EARLY', 'MID', 'STABLE'] as TradingStage[]).map((stage) => {
                    const stageInfo = STAGE_DISPLAY[stage];
                    const StageIcon = stageInfo.icon;
                    const stageResult = loopData.currentLoop?.stageResults?.[stage] || { iterations: 0, improved: 0, avgImprovement: 0, bestSharpe: 0 };
                    return (
                      <div key={stage} className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-2">
                        <div className="flex items-center gap-1 mb-1.5">
                          <StageIcon className="h-3 w-3" style={{ color: stageInfo.color }} />
                          <span className="text-[9px] font-mono font-bold" style={{ color: stageInfo.color }}>
                            {stage}
                          </span>
                        </div>
                        <div className="grid grid-cols-2 gap-1">
                          <div>
                            <div className="text-[7px] font-mono text-[#475569]">Iterations</div>
                            <div className="text-[10px] font-mono font-bold text-[#e2e8f0]">{stageResult.iterations}</div>
                          </div>
                          <div>
                            <div className="text-[7px] font-mono text-[#475569]">Improved</div>
                            <div className="text-[10px] font-mono font-bold text-emerald-400">{stageResult.improved}</div>
                          </div>
                          <div>
                            <div className="text-[7px] font-mono text-[#475569]">Avg Imp%</div>
                            <div className={`text-[10px] font-mono font-bold ${stageResult.avgImprovement >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {stageResult.avgImprovement !== 0 ? `${stageResult.avgImprovement >= 0 ? '+' : ''}${stageResult.avgImprovement.toFixed(1)}%` : '—'}
                            </div>
                          </div>
                          <div>
                            <div className="text-[7px] font-mono text-[#475569]">Best Sharpe</div>
                            <div className="text-[10px] font-mono font-bold text-cyan-400">
                              {stageResult.bestSharpe !== 0 ? stageResult.bestSharpe.toFixed(2) : '—'}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Latest Loop Results */}
                {loopData.currentLoop && loopData.currentLoop.iterations.length > 0 && (
                  <div>
                    <div className="flex items-center gap-1 mb-1.5">
                      <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Latest Loop Iterations</span>
                      <span className="text-[8px] font-mono text-[#475569]">
                        (Loop: {loopData.currentLoop.loopId.slice(0, 12)}...)
                      </span>
                    </div>
                    <div className="max-h-40 overflow-y-auto space-y-1">
                      {loopData.currentLoop.iterations.slice(0, 8).map((iter, i) => (
                        <div
                          key={i}
                          className={`flex items-center gap-2 px-2 py-1.5 rounded border text-[9px] font-mono ${
                            iter.adopted
                              ? 'border-emerald-500/30 bg-emerald-500/5'
                              : iter.improvementPct > 0
                                ? 'border-cyan-500/20 bg-cyan-500/5'
                                : 'border-[#1e293b] bg-[#0a0e17]'
                          }`}
                        >
                          <span className="shrink-0 font-bold text-[#e2e8f0] w-28 truncate" title={iter.systemName}>
                            {iter.systemName}
                          </span>
                          <span className="shrink-0 px-1 py-0.5 rounded text-[8px] font-bold" style={{
                            color: STAGE_DISPLAY[iter.stage as TradingStage]?.color || '#64748b',
                            backgroundColor: `${STAGE_DISPLAY[iter.stage as TradingStage]?.color || '#64748b'}15`,
                          }}>
                            {iter.stage}
                          </span>
                          <span className="text-[#94a3b8]">
                            {iter.originalSharpe.toFixed(2)} → {iter.refinedSharpe.toFixed(2)}
                          </span>
                          <span className={`font-bold ${iter.improvementPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {iter.improvementPct >= 0 ? '+' : ''}{iter.improvementPct.toFixed(1)}%
                          </span>
                          {iter.adopted && (
                            <span className="flex items-center gap-0.5 px-1 py-0.5 bg-emerald-500/10 border border-emerald-500/30 rounded text-[8px] text-emerald-400">
                              <CheckCircle2 className="h-2 w-2" /> Adopted
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Summary + Controls */}
                <div className="flex items-center gap-3 pt-2 border-t border-[#1e293b]">
                  {loopData.currentLoop && (
                    <div className="flex items-center gap-3 flex-1">
                      <div className="flex items-center gap-1">
                        <Trophy className="h-3 w-3 text-[#d4af37]" />
                        <span className="text-[8px] font-mono text-[#64748b]">Improved:</span>
                        <span className="text-[9px] font-mono font-bold text-emerald-400">
                          {loopData.currentLoop.summary.systemsImproved}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-[8px] font-mono text-[#64748b]">Best:</span>
                        <span className="text-[9px] font-mono font-bold text-cyan-400">
                          +{loopData.currentLoop.summary.bestImprovement.toFixed(1)}%
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-[8px] font-mono text-[#64748b]">Avg:</span>
                        <span className="text-[9px] font-mono font-bold text-[#e2e8f0]">
                          +{loopData.currentLoop.summary.averageImprovement.toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  )}

                  <div className="flex items-center gap-1.5">
                    {!loopData.isRunning ? (
                      <button
                        onClick={() => sendLoopAction('start')}
                        disabled={loopActionLoading !== null}
                        className="flex items-center gap-1 px-2.5 py-1 bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 rounded text-[9px] font-mono font-bold hover:bg-emerald-600/30 disabled:opacity-40 transition-colors"
                      >
                        {loopActionLoading === 'start' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                        START LOOP
                      </button>
                    ) : (
                      <button
                        onClick={() => sendLoopAction('stop')}
                        disabled={loopActionLoading !== null}
                        className="flex items-center gap-1 px-2.5 py-1 bg-red-600/20 text-red-400 border border-red-500/30 rounded text-[9px] font-mono font-bold hover:bg-red-600/30 disabled:opacity-40 transition-colors"
                      >
                        {loopActionLoading === 'stop' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />}
                        STOP LOOP
                      </button>
                    )}
                    <button
                      onClick={() => sendLoopAction('run')}
                      disabled={loopActionLoading !== null}
                      className="flex items-center gap-1 px-2.5 py-1 bg-cyan-600/20 text-cyan-400 border border-cyan-500/30 rounded text-[9px] font-mono font-bold hover:bg-cyan-600/30 disabled:opacity-40 transition-colors"
                    >
                      {loopActionLoading === 'run' ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                      RUN ONCE
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <div className="flex items-center justify-center h-20 text-[#475569] font-mono text-[10px]">
                No loop data available
              </div>
            )}
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════
            SECTION 4: PHASE STRATEGY PANEL (NEW)
            ═══════════════════════════════════════════════════════════ */}
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Layers className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider font-bold">
              Phase Strategy
            </span>
            {phaseData && (
              <span className="text-[9px] font-mono text-[#475569] ml-1">
                {phaseData.strategies.length} strategies
              </span>
            )}
          </div>
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3 space-y-3">
            {phaseData ? (
              <>
                {/* 3 Strategy Cards */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                  {phaseData.strategies.map((strategy) => {
                    const stageInfo = STAGE_DISPLAY[strategy.stage];
                    const StageIcon = stageInfo.icon;
                    const stageMetrics = phaseData.stages[strategy.stage];
                    return (
                      <div key={strategy.stage} className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-2.5">
                        <div className="flex items-center gap-1.5 mb-1.5">
                          <StageIcon className="h-4 w-4" style={{ color: stageInfo.color }} />
                          <div>
                            <div className="text-[10px] font-mono font-bold" style={{ color: stageInfo.color }}>
                              {strategy.name}
                            </div>
                            <div className="text-[8px] font-mono text-[#64748b]">{strategy.riskProfile}</div>
                          </div>
                        </div>

                        <div className="text-[8px] font-mono text-[#94a3b8] mb-2 line-clamp-2">
                          {strategy.description}
                        </div>

                        {/* Key Parameters */}
                        <div className="grid grid-cols-2 gap-1 mb-2">
                          <div className="bg-[#0d1117] rounded px-1.5 py-1">
                            <div className="text-[7px] font-mono text-[#475569]">SL</div>
                            <div className="text-[9px] font-mono font-bold text-[#e2e8f0]">{strategy.parameters.stopLossPct}%</div>
                          </div>
                          <div className="bg-[#0d1117] rounded px-1.5 py-1">
                            <div className="text-[7px] font-mono text-[#475569]">TP</div>
                            <div className="text-[9px] font-mono font-bold text-[#e2e8f0]">{strategy.parameters.takeProfitPct}%</div>
                          </div>
                          <div className="bg-[#0d1117] rounded px-1.5 py-1">
                            <div className="text-[7px] font-mono text-[#475569]">Max Pos</div>
                            <div className="text-[9px] font-mono font-bold text-[#e2e8f0]">{strategy.parameters.maxPositionPct}%</div>
                          </div>
                          <div className="bg-[#0d1117] rounded px-1.5 py-1">
                            <div className="text-[7px] font-mono text-[#475569]">Confidence</div>
                            <div className="text-[9px] font-mono font-bold text-[#e2e8f0]">{(strategy.parameters.confidenceThreshold * 100).toFixed(0)}%</div>
                          </div>
                        </div>

                        {/* Focus Areas */}
                        <div className="flex flex-wrap gap-1 mb-2">
                          {strategy.focusAreas.map((area) => (
                            <span
                              key={area}
                              className="text-[7px] font-mono px-1.5 py-0.5 rounded border border-[#1e293b] text-[#94a3b8]"
                            >
                              {area}
                            </span>
                          ))}
                        </div>

                        {/* Stage Metrics */}
                        {stageMetrics && (
                          <div className="pt-1.5 border-t border-[#1e293b] grid grid-cols-3 gap-1">
                            <div>
                              <div className="text-[7px] font-mono text-[#475569]">Tokens</div>
                              <div className="text-[9px] font-mono font-bold text-[#e2e8f0]">{stageMetrics.tokensInStage}</div>
                            </div>
                            <div>
                              <div className="text-[7px] font-mono text-[#475569]">Systems</div>
                              <div className="text-[9px] font-mono font-bold text-[#e2e8f0]">{stageMetrics.activeSystems}</div>
                            </div>
                            <div>
                              <div className="text-[7px] font-mono text-[#475569]">Avg Sharpe</div>
                              <div className="text-[9px] font-mono font-bold text-cyan-400">
                                {stageMetrics.avgSharpe !== 0 ? stageMetrics.avgSharpe.toFixed(2) : '—'}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Token Distribution Stacked Bar */}
                <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Token Distribution by Stage</span>
                    <span className="text-[9px] font-mono text-[#475569]">
                      Total: {Object.values(phaseData.tokenDistribution).reduce((a, b) => a + b, 0)}
                    </span>
                  </div>
                  <div className="flex h-4 rounded overflow-hidden bg-[#1a1f2e]">
                    {(['EARLY', 'MID', 'STABLE'] as TradingStage[]).map((stage) => {
                      const count = phaseData.tokenDistribution[stage] || 0;
                      const total = Object.values(phaseData.tokenDistribution).reduce((a, b) => a + b, 0);
                      const pct = total > 0 ? (count / total) * 100 : 0;
                      return pct > 0 ? (
                        <div
                          key={stage}
                          className="transition-all duration-500 relative group"
                          style={{ width: `${pct}%`, backgroundColor: STAGE_DISPLAY[stage].color, opacity: 0.75 }}
                          title={`${stage}: ${count} tokens (${pct.toFixed(1)}%)`}
                        >
                          <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-1.5 py-0.5 bg-[#0a0e17] border border-[#1e293b] rounded text-[8px] font-mono text-[#e2e8f0] whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                            {stage}: {count} ({pct.toFixed(0)}%)
                          </div>
                        </div>
                      ) : null;
                    })}
                  </div>
                  <div className="flex items-center gap-3 mt-1">
                    {(['EARLY', 'MID', 'STABLE'] as TradingStage[]).map((stage) => (
                      <span key={stage} className="flex items-center gap-1 text-[8px] font-mono text-[#64748b]">
                        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: STAGE_DISPLAY[stage].color }} />
                        {stage}: {phaseData.tokenDistribution[stage] || 0}
                      </span>
                    ))}
                  </div>
                </div>

                {/* Top Opportunities Table */}
                {phaseData.topOpportunities.length > 0 && (
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                    <div className="flex items-center gap-1.5 mb-2">
                      <Swords className="h-3 w-3 text-[#d4af37]" />
                      <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Top Opportunities</span>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-[9px] font-mono">
                        <thead>
                          <tr className="border-b border-[#1e293b]">
                            <th className="text-left py-1 px-1.5 text-[#475569] font-normal">Symbol</th>
                            <th className="text-left py-1 px-1.5 text-[#475569] font-normal">Stage</th>
                            <th className="text-left py-1 px-1.5 text-[#475569] font-normal">Phase</th>
                            <th className="text-right py-1 px-1.5 text-[#475569] font-normal">Score</th>
                            <th className="text-left py-1 px-1.5 text-[#475569] font-normal hidden md:table-cell">Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {phaseData.topOpportunities.slice(0, 8).map((opp, i) => (
                            <tr key={i} className="border-b border-[#1e293b]/50 hover:bg-[#1a1f2e]/20">
                              <td className="py-1 px-1.5 text-[#e2e8f0] font-bold">{opp.symbol}</td>
                              <td className="py-1 px-1.5">
                                <span className="px-1 py-0.5 rounded" style={{
                                  color: STAGE_DISPLAY[opp.stage]?.color,
                                  backgroundColor: `${STAGE_DISPLAY[opp.stage]?.color}15`,
                                }}>
                                  {opp.stage}
                                </span>
                              </td>
                              <td className="py-1 px-1.5 text-[#94a3b8]">{opp.phase}</td>
                              <td className="py-1 px-1.5 text-right font-bold" style={{ color: opp.matchScore >= 70 ? '#10b981' : opp.matchScore >= 40 ? '#f59e0b' : '#ef4444' }}>
                                {opp.matchScore}
                              </td>
                              <td className="py-1 px-1.5 text-[#64748b] truncate max-w-[200px] hidden md:table-cell" title={opp.matchReason}>
                                {opp.matchReason}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Recommendations */}
                {phaseData.recommendations.length > 0 && (
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-3">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Recommendations</span>
                    <div className="mt-1.5 space-y-1">
                      {phaseData.recommendations.map((rec, i) => (
                        <div key={i} className="flex items-start gap-1.5 text-[8px] font-mono text-[#94a3b8]">
                          <CircleDot className="h-2.5 w-2.5 mt-0.5 shrink-0 text-[#d4af37]" />
                          <span>{rec}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="flex items-center justify-center h-20 text-[#475569] font-mono text-[10px]">
                No phase strategy data available
              </div>
            )}
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════
            SECTION 5: TASK MONITOR + PIPELINE (EXISTING)
            ═══════════════════════════════════════════════════════════ */}
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Activity className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider font-bold">
              Task Monitor
            </span>
            <span className="text-[9px] font-mono text-[#475569] ml-1">
              ({schedulerData?.tasks?.length ?? 0} tasks)
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
            {schedulerData?.tasks?.map((task) => {
              const taskState = getTaskStatus(task);
              return (
                <div
                  key={task.name}
                  className={`relative bg-[#0d1117] border rounded-md p-2 transition-colors ${
                    taskState === 'running'
                      ? 'border-emerald-500/30 bg-emerald-500/5'
                      : taskState === 'error'
                        ? 'border-red-500/20 bg-red-500/5'
                        : 'border-[#1e293b] hover:border-[#2a3040]'
                  }`}
                >
                  <div className="absolute top-2 right-2">
                    <span className={`h-2 w-2 rounded-full block ${
                      taskState === 'running' ? 'bg-emerald-500 animate-pulse' : taskState === 'error' ? 'bg-red-500' : 'bg-gray-600'
                    }`} />
                  </div>
                  <div className="text-[10px] font-mono text-[#e2e8f0] font-bold truncate pr-4">
                    {formatTaskName(task.name)}
                  </div>
                  <div className="mt-1 grid grid-cols-2 gap-x-2 gap-y-0.5">
                    <span className="text-[8px] font-mono text-[#475569]">Interval</span>
                    <span className="text-[8px] font-mono text-[#94a3b8] text-right">{formatInterval(task.intervalMs)}</span>
                    <span className="text-[8px] font-mono text-[#475569]">Runs</span>
                    <span className="text-[8px] font-mono text-[#e2e8f0] text-right">{task.runCount}</span>
                    <span className="text-[8px] font-mono text-[#475569]">Errors</span>
                    <span className={`text-[8px] font-mono text-right ${task.errorCount > 0 ? 'text-red-400' : 'text-[#64748b]'}`}>
                      {task.errorCount}
                    </span>
                    <span className="text-[8px] font-mono text-[#475569]">Last</span>
                    <span className="text-[8px] font-mono text-[#94a3b8] text-right">{timeAgo(task.lastRunAt)}</span>
                  </div>
                  {task.isRunning && (
                    <div className="mt-1 h-0.5 bg-[#1e293b] rounded-full overflow-hidden">
                      <div className="h-full bg-emerald-500 rounded-full animate-[progress_2s_ease-in-out_infinite]" style={{ width: '60%' }} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Brain Cycle Pipeline */}
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Zap className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider font-bold">
              Brain Cycle Pipeline
            </span>
            {isRunning && (
              <span className="text-[9px] font-mono text-emerald-400 ml-1 flex items-center gap-1">
                <span className="h-1 w-1 rounded-full bg-emerald-400 animate-pulse" /> Active
              </span>
            )}
          </div>
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
            <div className="flex items-center gap-1 overflow-x-auto pb-1">
              {PIPELINE_STAGES.map((stage, idx) => (
                <div key={stage.key} className="flex items-center shrink-0">
                  <div className={`flex flex-col items-center px-3 py-2 rounded-md border min-w-[80px] ${
                    pipelineCounts[stage.key] > 0
                      ? 'border-[#2a3040] bg-[#0a0e17]'
                      : 'border-[#1e293b]/50 bg-[#0a0e17]/50'
                  }`}>
                    <span className="text-[9px] font-mono font-bold" style={{ color: stage.color }}>
                      {stage.label}
                    </span>
                    <span className={`text-lg font-mono font-bold ${
                      pipelineCounts[stage.key] > 0 ? 'text-[#e2e8f0]' : 'text-[#475569]'
                    }`}>
                      {stage.key === 'FEEDBACK'
                        ? (schedulerData?.brainCycle?.cyclesCompleted ?? 0)
                        : stage.key === 'GROWTH'
                          ? (lastGrowth ? formatPct(lastGrowth.totalReturnPct) : '—')
                          : pipelineCounts[stage.key]
                      }
                    </span>
                  </div>
                  {idx < PIPELINE_STAGES.length - 1 && (
                    <div className="flex items-center mx-0.5">
                      <ChevronRight className="h-3 w-3 text-[#475569]" />
                      {isRunning && (
                        <div className="w-3 h-0.5 bg-gradient-to-r from-[#d4af37]/60 to-[#d4af37]/0 rounded" />
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Operability Distribution Bar */}
            <div className="mt-3 pt-2 border-t border-[#1e293b]">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Operability Distribution</span>
                <span className="text-[9px] font-mono text-[#475569]">
                  Total: {Object.values(operDist as Record<string, number>).reduce((a: number, b: number) => a + b, 0)}
                </span>
              </div>
              <div className="flex h-3 rounded-full overflow-hidden bg-[#1a1f2e]">
                {Object.entries(operDist as Record<string, number>).map(([level, count]) => {
                  const total = Object.values(operDist as Record<string, number>).reduce((a: number, b: number) => a + b, 0);
                  const pct = total > 0 ? (count / total) * 100 : 0;
                  const colorMap: Record<string, string> = {
                    PREMIUM: 'bg-emerald-500', GOOD: 'bg-cyan-500', MARGINAL: 'bg-yellow-500',
                    RISKY: 'bg-orange-500', UNOPERABLE: 'bg-red-500',
                  };
                  return pct > 0 ? (
                    <div key={level} className={`${colorMap[level] || 'bg-gray-500'} transition-all duration-500`} style={{ width: `${pct}%` }} title={`${level}: ${count} (${pct.toFixed(1)}%)`} />
                  ) : null;
                })}
              </div>
              <div className="flex items-center gap-3 mt-1">
                {Object.entries(operDist).map(([level, count]) => {
                  const colorMap: Record<string, string> = {
                    PREMIUM: 'bg-emerald-500', GOOD: 'bg-cyan-500', MARGINAL: 'bg-yellow-500',
                    RISKY: 'bg-orange-500', UNOPERABLE: 'bg-red-500',
                  };
                  return (
                    <span key={level} className="flex items-center gap-1 text-[8px] font-mono text-[#64748b]">
                      <span className={`h-1.5 w-1.5 rounded-full ${colorMap[level]}`} />
                      {level}: {count as number}
                    </span>
                  );
                })}
              </div>
            </div>

            {/* Per-Chain Token Distribution */}
            {Object.keys(perChainTokenCounts).length > 0 && (
              <div className="mt-3 pt-2 border-t border-[#1e293b]">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider flex items-center gap-1">
                    <BarChart3 className="h-2.5 w-2.5" /> Per-Chain Token Distribution
                  </span>
                  <span className="text-[9px] font-mono text-[#475569]">
                    {Object.keys(perChainTokenCounts).length} chains
                  </span>
                </div>
                <div className="flex h-4 rounded overflow-hidden bg-[#1a1f2e]">
                  {Object.entries(perChainTokenCounts).map(([chain, count]) => {
                    const total = Object.values(perChainTokenCounts).reduce((a, b) => a + b, 0);
                    const pct = total > 0 ? (count / total) * 100 : 0;
                    const color = CHAIN_COLORS[chain] || '#64748b';
                    return pct > 0 ? (
                      <div
                        key={chain}
                        className="transition-all duration-500 relative group"
                        style={{ width: `${pct}%`, backgroundColor: color, opacity: 0.7 }}
                        title={`${chain}: ${count} tokens (${pct.toFixed(1)}%)`}
                      >
                        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-1.5 py-0.5 bg-[#0a0e17] border border-[#1e293b] rounded text-[8px] font-mono text-[#e2e8f0] whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                          {chain}: {count} ({pct.toFixed(0)}%)
                        </div>
                      </div>
                    ) : null;
                  })}
                </div>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  {Object.entries(perChainTokenCounts)
                    .sort(([, a], [, b]) => b - a)
                    .map(([chain, count]) => (
                      <span key={chain} className="flex items-center gap-1 text-[8px] font-mono text-[#64748b]">
                        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: CHAIN_COLORS[chain] || '#64748b' }} />
                        {chain}: {count}
                      </span>
                    ))}
                </div>
              </div>
            )}

            {/* Signal Type Breakdown */}
            {Object.keys(signalBreakdown).length > 0 && (
              <div className="mt-3 pt-2 border-t border-[#1e293b]">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Signal Type Breakdown</span>
                  <span className="text-[9px] font-mono text-[#475569]">
                    {Object.values(signalBreakdown).reduce((a, b) => a + b, 0)} total
                  </span>
                </div>
                <div className="flex h-3 rounded overflow-hidden bg-[#1a1f2e]">
                  {Object.entries(signalBreakdown).map(([type, count]) => {
                    const total = Object.values(signalBreakdown).reduce((a, b) => a + b, 0);
                    const pct = total > 0 ? (count / total) * 100 : 0;
                    const color = SIGNAL_COLORS[type] || '#64748b';
                    return pct > 0 ? (
                      <div
                        key={type}
                        className="transition-all duration-500 relative group"
                        style={{ width: `${pct}%`, backgroundColor: color, opacity: 0.8 }}
                        title={`${type}: ${count} (${pct.toFixed(1)}%)`}
                      >
                        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-1.5 py-0.5 bg-[#0a0e17] border border-[#1e293b] rounded text-[8px] font-mono text-[#e2e8f0] whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                          {type.replace(/_/g, ' ')}: {count}
                        </div>
                      </div>
                    ) : null;
                  })}
                </div>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  {Object.entries(signalBreakdown)
                    .sort(([, a], [, b]) => b - a)
                    .map(([type, count]) => (
                      <span key={type} className="flex items-center gap-1 text-[8px] font-mono text-[#64748b]">
                        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: SIGNAL_COLORS[type] || '#64748b' }} />
                        {type.replace(/_/g, ' ')}: {count}
                      </span>
                    ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════
            SECTION 6+7: CAPITAL GROWTH + ACTIVITY FEED (EXISTING)
            ═══════════════════════════════════════════════════════════ */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {/* Capital Evolution Chart */}
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <TrendingUp className="h-3 w-3 text-[#d4af37]" />
              <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider font-bold">
                Capital Evolution
              </span>
            </div>
            <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
              <div className="h-36">
                {chartData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                      <defs>
                        <linearGradient id="capitalGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#d4af37" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#d4af37" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="pnlGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#10b981" stopOpacity={0.2} />
                          <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis dataKey="time" tick={{ fill: '#475569', fontSize: 9, fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} />
                      <YAxis tick={{ fill: '#475569', fontSize: 9, fontFamily: 'monospace' }} axisLine={{ stroke: '#1e293b' }} tickLine={false} width={50} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
                      <Tooltip contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: 6, fontSize: 10, fontFamily: 'monospace' }} labelStyle={{ color: '#94a3b8' }} />
                      <Area type="monotone" dataKey="capital" stroke="#d4af37" strokeWidth={2} fill="url(#capitalGradient)" name="Capital" />
                      <Line type="monotone" dataKey="pnl" stroke="#10b981" strokeWidth={1.5} strokeDasharray="4 2" dot={false} name="Fee-Adj PnL" />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex items-center justify-center h-full text-[#475569] font-mono text-[10px]">
                    No growth data yet — start the brain to begin tracking
                  </div>
                )}
              </div>
              <div className="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div className="bg-[#0a0e17] rounded p-1.5">
                  <div className="text-[8px] font-mono text-[#475569] uppercase">Daily Rate</div>
                  <div className="text-[11px] font-mono font-bold text-[#d4af37]">
                    {lastGrowth ? formatPct(lastGrowth.dailyCompoundRate) : '—'}
                  </div>
                </div>
                <div className="bg-[#0a0e17] rounded p-1.5">
                  <div className="text-[8px] font-mono text-[#475569] uppercase">Proj. Annual</div>
                  <div className={`text-[11px] font-mono font-bold ${
                    (lastGrowth?.projectedAnnualReturn ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
                  }`}>
                    {lastGrowth ? formatPct(lastGrowth.projectedAnnualReturn) : '—'}
                  </div>
                </div>
                <div className="bg-[#0a0e17] rounded p-1.5">
                  <div className="text-[8px] font-mono text-[#475569] uppercase">Win Rate</div>
                  <div className="text-[11px] font-mono font-bold text-cyan-400">
                    {lastGrowth ? formatPct(lastGrowth.winRate * 100) : '—'}
                  </div>
                </div>
                <div className="bg-[#0a0e17] rounded p-1.5">
                  <div className="text-[8px] font-mono text-[#475569] uppercase">Sharpe</div>
                  <div className="text-[11px] font-mono font-bold text-[#e2e8f0]">
                    {sharpeDisplay}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Activity Feed */}
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Eye className="h-3 w-3 text-[#d4af37]" />
              <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider font-bold">
                Activity Feed
              </span>
              <span className="text-[9px] font-mono text-[#475569] ml-1">
                ({activityFeed.length} events)
              </span>
            </div>
            <div className="bg-[#0d1117] border border-[#1e293b] rounded-md max-h-[280px] overflow-y-auto">
              {activityFeed.length > 0 ? (
                <div className="divide-y divide-[#1e293b]/50">
                  {activityFeed.map((event) => {
                    const iconMap = {
                      cycle_completed: <Activity className="h-3 w-3 text-emerald-400" />,
                      signal_generated: <Zap className="h-3 w-3 text-cyan-400" />,
                      error: <AlertTriangle className="h-3 w-3 text-red-400" />,
                      status_changed: <Radio className="h-3 w-3 text-amber-400" />,
                    };
                    const bgColorMap = {
                      cycle_completed: 'bg-emerald-500/5',
                      signal_generated: 'bg-cyan-500/5',
                      error: 'bg-red-500/5',
                      status_changed: 'bg-amber-500/5',
                    };
                    return (
                      <div
                        key={event.id}
                        className={`flex items-start gap-2 px-3 py-1.5 ${bgColorMap[event.type]} hover:bg-[#1a1f2e]/30 transition-colors`}
                      >
                        <span className="shrink-0 mt-0.5">{iconMap[event.type]}</span>
                        <div className="flex-1 min-w-0">
                          <div className="text-[9px] font-mono text-[#e2e8f0] line-clamp-2">
                            {event.description}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[8px] font-mono text-[#475569]">
                              {new Date(event.timestamp).toLocaleTimeString()}
                            </span>
                            {event.meta?.pnl !== undefined && (
                              <span className={`text-[8px] font-mono font-bold ${
                                event.meta.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                              }`}>
                                {event.meta.pnl >= 0 ? '+' : ''}{formatCurrency(event.meta.pnl)}
                              </span>
                            )}
                            {event.meta?.regime && (
                              <span className="text-[8px] font-mono text-[#94a3b8]">
                                {event.meta.regime}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                  <div ref={feedEndRef} />
                </div>
              ) : (
                <div className="flex items-center justify-center h-20 text-[#475569] font-mono text-[10px]">
                  No activity yet — start the brain scheduler
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ─── FOOTER ─── */}
      <div className="shrink-0 flex items-center gap-3 px-3 py-1.5 border-t border-[#1e293b] bg-[#0d1117]">
        <span className="text-[8px] font-mono text-[#475569]">
          <RefreshCw className="h-2.5 w-2.5 inline mr-1" />
          Auto-refresh: 5s
        </span>
        <span className="text-[8px] font-mono text-[#475569]">•</span>
        <span className="text-[8px] font-mono text-[#64748b]">
          Updated: {new Date(now).toLocaleTimeString()}
        </span>
        {actionLoading && (
          <>
            <span className="text-[8px] font-mono text-[#475569]">•</span>
            <span className="text-[8px] font-mono text-amber-400 flex items-center gap-1">
              <Loader2 className="h-2.5 w-2.5 animate-spin" />
              Processing: {actionLoading}
            </span>
          </>
        )}
        {capacityData && (
          <>
            <span className="text-[8px] font-mono text-[#475569]">•</span>
            <span className="text-[8px] font-mono" style={{ color: getCapacityScoreColor(capacityData.overallScore) }}>
              Capacity: {capacityData.level} ({capacityData.overallScore}/100)
            </span>
          </>
        )}
        <div className="flex-1" />
        {Object.keys(perChainTokenCounts).length > 0 && (
          <span className="text-[8px] font-mono text-[#475569]">
            Chains: {Object.keys(perChainTokenCounts).join(', ')}
          </span>
        )}
        <span className="text-[8px] font-mono text-[#475569]">•</span>
        <span className="text-[8px] font-mono text-[#475569]">
          Interval: {formatInterval(schedulerData?.config?.cycleIntervalMs ?? 300000)}
        </span>
      </div>
    </div>
  );
}
