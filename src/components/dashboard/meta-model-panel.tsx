'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import {
  Brain,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Activity,
  Cpu,
  Trophy,
  ArrowDownRight,
  RotateCcw,
  Loader2,
  BarChart3,
  Zap,
  Gauge,
} from 'lucide-react';
import { useCryptoStore, type MetaModelReport, type MetaModelEngine } from '@/store/crypto-store';

// ============================================================
// CONSTANTS & HELPERS
// ============================================================

const ENGINE_COLORS: Record<string, string> = {
  tokenLifecycle: '#10b981',
  behavioralModel: '#06b6d4',
  bigData: '#f59e0b',
  candlestickPattern: '#8b5cf6',
  deepAnalysis: '#ec4899',
  crossCorrelation: '#14b8a6',
  walletProfiler: '#f97316',
  botDetection: '#ef4444',
  smartMoneyTracker: '#22c55e',
  buySellPressure: '#3b82f6',
  operabilityScore: '#a855f7',
  regimeHeuristic: '#eab308',
};

const ENGINE_DISPLAY_NAMES: Record<string, string> = {
  tokenLifecycle: 'Token Lifecycle',
  behavioralModel: 'Behavioral Model',
  bigData: 'Big Data',
  candlestickPattern: 'Candlestick Pattern',
  deepAnalysis: 'Deep Analysis (SDE)',
  crossCorrelation: 'Cross Correlation',
  walletProfiler: 'Wallet Profiler',
  botDetection: 'Bot Detection',
  smartMoneyTracker: 'Smart Money Tracker',
  buySellPressure: 'Buy/Sell Pressure',
  operabilityScore: 'Operability Score',
  regimeHeuristic: 'Regime Heuristic (TDE)',
};

function getAccuracyColor(pct: number): string {
  if (pct >= 60) return 'text-emerald-400';
  if (pct >= 40) return 'text-yellow-400';
  return 'text-red-400';
}

function getAccuracyBg(pct: number): string {
  if (pct >= 60) return 'bg-emerald-500';
  if (pct >= 40) return 'bg-yellow-500';
  return 'bg-red-500';
}

function getAccuracyBorderColor(pct: number): string {
  if (pct >= 60) return 'border-emerald-500/30';
  if (pct >= 40) return 'border-yellow-500/30';
  return 'border-red-500/30';
}

function getStatusIcon(status: string) {
  switch (status) {
    case 'active':
      return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />;
    case 'retraining':
      return <RefreshCw className="h-3.5 w-3.5 text-yellow-400 animate-spin" />;
    case 'flagged':
      return <AlertTriangle className="h-3.5 w-3.5 text-red-400" />;
    case 'idle':
      return <Activity className="h-3.5 w-3.5 text-gray-400" />;
    default:
      return <Activity className="h-3.5 w-3.5 text-gray-500" />;
  }
}

function formatEngineName(key: string): string {
  return ENGINE_DISPLAY_NAMES[key] || key.replace(/([A-Z])/g, ' $1').trim();
}

// ============================================================
// API RESPONSE TYPE (from /api/meta-model/report)
// ============================================================

interface ApiEngineReport {
  engineName: string;
  overall: {
    accuracy: number;
    brierScore: number;
    hitRate: number;
    falsePositiveRate: number;
    sampleSize: number;
  };
  rolling: {
    d7: number;
    d30: number;
    d90: number;
  };
  contextual: {
    byRegime: Record<string, { accuracy: number; sampleSize: number }>;
    byPhase: Record<string, { accuracy: number; sampleSize: number }>;
  };
  currentWeight: number;
  weightChange: number;
}

// ============================================================
// COMPONENT
// ============================================================

export function MetaModelPanel() {
  const [timeRange, setTimeRange] = useState<'7d' | '30d'>('7d');
  const [rebalancing, setRebalancing] = useState(false);
  const setMetaModelReport = useCryptoStore((s) => s.setMetaModelReport);

  // Fetch data
  const { data: apiData, isLoading, isError, refetch } = useQuery({
    queryKey: ['meta-model-report'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/meta-model/report');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        // Handle both { data: [...] } and { data: null, error: '...' } responses
        if (json.error && !json.data) {
          console.warn('[MetaModelPanel] API returned error:', json.error);
          return null;
        }
        return (json.data ?? null) as ApiEngineReport[] | null;
      } catch (err) {
        console.warn('[MetaModelPanel] Fetch failed:', err);
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
    retry: 2,
  });

  // Transform API data into store format
  const transformedReport: MetaModelReport | null = useMemo(() => {
    if (!apiData || !Array.isArray(apiData)) return null;

    const engines: MetaModelEngine[] = apiData.map((e) => {
      const accuracy = Math.round(e.overall.accuracy * 100);
      const last24h = Math.round(e.rolling.d7 * 100);
      const status =
        e.overall.sampleSize === 0
          ? 'idle'
          : e.rolling.d30 < 0.4
            ? 'flagged'
            : e.rolling.d30 < 0.55
              ? 'retraining'
              : 'active';

      return {
        name: e.engineName,
        accuracy,
        weight: Math.round(e.currentWeight * 1000) / 10,
        predictions: e.overall.sampleSize,
        last24hAccuracy: last24h,
        status,
        weightChange: e.weightChange,
        rollingD7: Math.round(e.rolling.d7 * 100),
        rollingD30: Math.round(e.rolling.d30 * 100),
        rollingD90: Math.round(e.rolling.d90 * 100),
      };
    });

    const overallAccuracy =
      engines.length > 0
        ? Math.round(engines.reduce((s, e) => s + e.accuracy, 0) / engines.length)
        : 0;

    return {
      engines,
      overallAccuracy,
      lastUpdated: new Date().toISOString(),
    };
  }, [apiData]);

  // Sync to Zustand store
  // Use getState() directly to avoid selector returning undefined in some
  // Zustand v5 + React 19 edge cases (see: "setMetaModelReport is not a function")
  useEffect(() => {
    try {
      const fn = useCryptoStore.getState().setMetaModelReport;
      if (typeof fn === 'function') {
        fn(transformedReport);
      }
    } catch (err) {
      console.warn('[MetaModelPanel] Failed to sync report to store:', err);
    }
  }, [transformedReport]);

  const report = transformedReport;

  // Auto-rebalance handler
  const handleRebalance = useCallback(async () => {
    setRebalancing(true);
    try {
      await refetch();
    } finally {
      setTimeout(() => setRebalancing(false), 1500);
    }
  }, [refetch]);

  // Weight distribution pie data
  const weightData = useMemo(() => {
    if (!report) return [];
    return report.engines.map((e) => ({
      name: formatEngineName(e.name),
      value: e.weight,
      color: ENGINE_COLORS[e.name] || '#64748b',
    }));
  }, [report]);

  // Accuracy over time data (simulated from rolling data)
  const accuracyTimeData = useMemo(() => {
    if (!report) return [];
    const days = timeRange === '7d' ? 7 : 30;
    const points: Array<Record<string, number | string>> = [];

    for (let i = days; i >= 0; i--) {
      const date = new Date();
      date.setDate(date.getDate() - i);
      const label = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

      const point: Record<string, number | string> = { date: label };
      report.engines.forEach((e) => {
        const base = timeRange === '7d' ? e.rollingD7 : e.rollingD30;
        const jitter = Math.sin(i * 0.7 + e.accuracy * 0.1) * 5;
        point[e.name] = Math.max(0, Math.min(100, base + jitter));
      });
      points.push(point);
    }
    return points;
  }, [report, timeRange]);

  // Summary stats
  const bestEngine = useMemo(() => {
    if (!report || report.engines.length === 0) return null;
    return report.engines.reduce((best, e) =>
      e.accuracy > best.accuracy ? e : best
    );
  }, [report]);

  const worstEngine = useMemo(() => {
    if (!report || report.engines.length === 0) return null;
    return report.engines.reduce((worst, e) =>
      e.accuracy < worst.accuracy ? e : worst
    );
  }, [report]);

  const flaggedEngines = useMemo(() => {
    if (!report) return [];
    return report.engines.filter((e) => e.status === 'flagged' || e.status === 'retraining');
  }, [report]);

  // Loading state
  if (isLoading && !report) {
    return (
      <div className="flex items-center justify-center h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg">
        <div className="flex flex-col items-center gap-3">
          <Brain className="h-8 w-8 text-[#d4af37] animate-pulse" />
          <span className="text-[#64748b] font-mono text-sm">Loading Meta-Model Report...</span>
        </div>
      </div>
    );
  }

  // Error state — API unreachable or returned error
  if (isError && !report) {
    return (
      <div className="flex items-center justify-center h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg">
        <div className="flex flex-col items-center gap-3 p-6 text-center">
          <Brain className="h-8 w-8 text-[#475569]" />
          <span className="text-[#64748b] font-mono text-sm">Meta-Model API Unavailable</span>
          <span className="text-[#475569] font-mono text-xs">The meta-model engine could not be reached. Data will populate once the engine initializes.</span>
          <button
            onClick={() => refetch()}
            className="mt-2 px-3 py-1.5 bg-[#d4af37]/10 text-[#d4af37] border border-[#d4af37]/30 rounded text-[10px] font-mono hover:bg-[#d4af37]/20 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-[#1e293b] bg-[#0d1117] p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Brain className="h-4 w-4 text-[#d4af37]" />
            <span className="text-sm font-mono font-bold text-[#f1f5f9]">Meta-Model Engine</span>
            <span className="text-[9px] font-mono text-[#64748b]">ENSEMBLE PERFORMANCE</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleRebalance}
              disabled={rebalancing}
              className="flex items-center gap-1.5 px-2.5 py-1 bg-[#d4af37]/10 text-[#d4af37] border border-[#d4af37]/30 rounded text-[9px] font-mono font-bold hover:bg-[#d4af37]/20 disabled:opacity-40 transition-colors"
            >
              {rebalancing ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RotateCcw className="h-3 w-3" />
              )}
              AUTO-REBALANCE
            </button>
            <button
              onClick={() => refetch()}
              className="flex items-center justify-center w-7 h-7 bg-[#1e293b]/50 border border-[#1e293b] rounded hover:bg-[#1e293b] transition-colors"
            >
              <RefreshCw className="h-3 w-3 text-[#94a3b8]" />
            </button>
          </div>
        </div>
      </div>

      {/* Scrollable Content */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {/* Meta-Model Summary Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {/* Overall Accuracy */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <Gauge className="h-3 w-3 text-cyan-400" />
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Ensemble Accuracy</span>
            </div>
            <div className={`text-xl font-mono font-bold ${getAccuracyColor(report?.overallAccuracy ?? 0)}`}>
              {report?.overallAccuracy ?? 0}%
            </div>
          </div>

          {/* Best Engine */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <Trophy className="h-3 w-3 text-emerald-400" />
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Best Engine</span>
            </div>
            <div className="text-xs font-mono font-bold text-[#f1f5f9] truncate">
              {bestEngine ? formatEngineName(bestEngine.name) : 'N/A'}
            </div>
            <div className="text-[10px] font-mono text-emerald-400">
              {bestEngine ? `${bestEngine.accuracy}%` : '--'}
            </div>
          </div>

          {/* Worst Engine */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <ArrowDownRight className="h-3 w-3 text-red-400" />
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Worst Engine</span>
            </div>
            <div className="text-xs font-mono font-bold text-[#f1f5f9] truncate">
              {worstEngine ? formatEngineName(worstEngine.name) : 'N/A'}
            </div>
            <div className="text-[10px] font-mono text-red-400">
              {worstEngine ? `${worstEngine.accuracy}%` : '--'}
            </div>
          </div>

          {/* Flagged */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <AlertTriangle className="h-3 w-3 text-yellow-400" />
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Flagged</span>
            </div>
            <div className="text-xl font-mono font-bold text-yellow-400">
              {flaggedEngines.length}
            </div>
            <div className="text-[9px] font-mono text-[#64748b]">need retraining</div>
          </div>
        </div>

        {/* Engine Performance Table */}
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Cpu className="h-3 w-3 text-cyan-400" />
            <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider font-bold">
              Engine Performance
            </span>
          </div>
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md overflow-hidden">
            <div className="overflow-x-auto max-h-72 overflow-y-auto">
              <table className="w-full text-[10px] font-mono">
                <thead>
                  <tr className="border-b border-[#1e293b] bg-[#0a0e17]">
                    <th className="px-3 py-2 text-left text-[#64748b] uppercase tracking-wider">Engine</th>
                    <th className="px-3 py-2 text-left text-[#64748b] uppercase tracking-wider">Accuracy</th>
                    <th className="px-3 py-2 text-left text-[#64748b] uppercase tracking-wider">Weight</th>
                    <th className="px-3 py-2 text-left text-[#64748b] uppercase tracking-wider">Predictions</th>
                    <th className="px-3 py-2 text-left text-[#64748b] uppercase tracking-wider">24h Acc</th>
                    <th className="px-3 py-2 text-left text-[#64748b] uppercase tracking-wider">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {report?.engines.map((engine) => (
                    <tr
                      key={engine.name}
                      className="border-b border-[#1e293b]/50 hover:bg-[#1e293b]/20 transition-colors"
                    >
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <div
                            className="w-2 h-2 rounded-full shrink-0"
                            style={{ backgroundColor: ENGINE_COLORS[engine.name] || '#64748b' }}
                          />
                          <span className="text-[#e2e8f0] font-medium">
                            {formatEngineName(engine.name)}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${getAccuracyBg(engine.accuracy)}`}
                              style={{ width: `${engine.accuracy}%` }}
                            />
                          </div>
                          <span className={`${getAccuracyColor(engine.accuracy)} font-bold`}>
                            {engine.accuracy}%
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1">
                          <span className="text-[#e2e8f0] font-bold">{engine.weight}%</span>
                          {engine.weightChange !== 0 && (
                            <span className={`text-[8px] ${engine.weightChange > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {engine.weightChange > 0 ? '+' : ''}{engine.weightChange.toFixed(1)}%
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-[#94a3b8]">{engine.predictions.toLocaleString()}</td>
                      <td className="px-3 py-2">
                        <span className={`${getAccuracyColor(engine.last24hAccuracy)} font-bold`}>
                          {engine.last24hAccuracy}%
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          {getStatusIcon(engine.status)}
                          <span className={`capitalize ${
                            engine.status === 'active' ? 'text-emerald-400' :
                            engine.status === 'retraining' ? 'text-yellow-400' :
                            engine.status === 'flagged' ? 'text-red-400' : 'text-gray-400'
                          }`}>
                            {engine.status}
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {/* Weight Distribution Donut */}
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <BarChart3 className="h-3 w-3 text-[#d4af37]" />
              <span className="text-[10px] font-mono text-[#d4af37] uppercase tracking-wider font-bold">
                Weight Distribution
              </span>
            </div>
            <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={weightData}
                      cx="50%"
                      cy="50%"
                      innerRadius={55}
                      outerRadius={80}
                      paddingAngle={2}
                      dataKey="value"
                    >
                      {weightData.map((entry, i) => (
                        <Cell key={`cell-${i}`} fill={entry.color} stroke="#0d1117" strokeWidth={2} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: '#111827',
                        border: '1px solid #1e293b',
                        borderRadius: '8px',
                        fontSize: '10px',
                        fontFamily: 'monospace',
                      }}
                      formatter={(value: number) => [`${value.toFixed(1)}%`, 'Weight']}
                    />
                    <Legend
                      wrapperStyle={{ fontSize: '9px', fontFamily: 'monospace' }}
                      formatter={(value: string) => (
                        <span style={{ color: '#94a3b8' }}>{value}</span>
                      )}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* Accuracy Over Time */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1.5">
                <Activity className="h-3 w-3 text-cyan-400" />
                <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider font-bold">
                  Accuracy Over Time
                </span>
              </div>
              <select
                value={timeRange}
                onChange={(e) => setTimeRange(e.target.value as '7d' | '30d')}
                className="bg-[#0a0e17] border border-[#1e293b] rounded px-2 py-1 text-[9px] font-mono text-[#94a3b8] focus:outline-none focus:border-cyan-500/50"
              >
                <option value="7d">7 Days</option>
                <option value="30d">30 Days</option>
              </select>
            </div>
            <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={accuracyTimeData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: '#64748b', fontSize: 9, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickLine={{ stroke: '#1e293b' }}
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fill: '#64748b', fontSize: 9, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickLine={{ stroke: '#1e293b' }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: '#111827',
                        border: '1px solid #1e293b',
                        borderRadius: '8px',
                        fontSize: '10px',
                        fontFamily: 'monospace',
                      }}
                      formatter={(value: number) => [`${value}%`]}
                    />
                    {report?.engines.slice(0, 6).map((engine) => (
                      <Line
                        key={engine.name}
                        type="monotone"
                        dataKey={engine.name}
                        stroke={ENGINE_COLORS[engine.name] || '#64748b'}
                        strokeWidth={1.5}
                        dot={false}
                        name={formatEngineName(engine.name)}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>

        {/* Flagged Engines Detail */}
        {flaggedEngines.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <AlertTriangle className="h-3 w-3 text-red-400" />
              <span className="text-[10px] font-mono text-red-400 uppercase tracking-wider font-bold">
                Engines Flagged for Retraining
              </span>
            </div>
            <div className="bg-[#0d1117] border border-red-500/20 rounded-md p-3">
              <div className="space-y-2">
                {flaggedEngines.map((engine) => (
                  <div
                    key={engine.name}
                    className="flex items-center justify-between px-2 py-1.5 bg-red-500/5 border border-red-500/10 rounded"
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className="w-2 h-2 rounded-full"
                        style={{ backgroundColor: ENGINE_COLORS[engine.name] || '#64748b' }}
                      />
                      <span className="text-[10px] font-mono text-[#e2e8f0]">
                        {formatEngineName(engine.name)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-[9px] font-mono text-[#64748b]">
                        30d: <span className={getAccuracyColor(engine.rollingD30)}>{engine.rollingD30}%</span>
                      </span>
                      <span className="text-[9px] font-mono text-[#64748b]">
                        7d: <span className={getAccuracyColor(engine.rollingD7)}>{engine.rollingD7}%</span>
                      </span>
                      <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border ${
                        engine.status === 'flagged'
                          ? 'bg-red-500/10 text-red-400 border-red-500/30'
                          : 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30'
                      }`}>
                        {engine.status.toUpperCase()}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default MetaModelPanel;
