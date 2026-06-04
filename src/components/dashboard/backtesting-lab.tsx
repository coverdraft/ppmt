'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FlaskConical,
  Play,
  Clock,
  CheckCircle2,
  XCircle,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Activity,
  Target,
  AlertTriangle,
  ChevronRight,
  Gauge,
  ArrowUpRight,
  ArrowDownRight,
  RefreshCw,
  Zap,
  Trash2,
  Loader2,
  Plus,
  AlertCircle,
  Info,
} from 'lucide-react';
import { toast } from 'sonner';

// ============================================================
// TYPES
// ============================================================

type BacktestStatus = 'COMPLETED' | 'RUNNING' | 'FAILED' | 'PENDING';

/** Shape returned by GET /api/backtest (list) */
interface BacktestListItem {
  id: string;
  systemId: string;
  systemName: string;
  systemCategory: string;
  systemIcon: string;
  mode: string;
  periodStart: string;
  periodEnd: string;
  initialCapital: number;
  allocationMethod: string;
  status: BacktestStatus;
  progress: number;
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

/** Shape returned by GET /api/backtest/[id] (detail) */
interface BacktestDetailData {
  id: string;
  systemId: string;
  mode: string;
  periodStart: string;
  periodEnd: string;
  initialCapital: number;
  finalCapital: number;
  totalPnl: number;
  totalPnlPct: number;
  sharpeRatio: number;
  winRate: number;
  totalTrades: number;
  winTrades: number;
  lossTrades: number;
  maxDrawdownPct: number;
  profitFactor: number;
  avgHoldTimeMin: number;
  marketExposurePct: number;
  sortinoRatio: number | null;
  calmarRatio: number | null;
  recoveryFactor: number | null;
  phaseResults: string;
  timeframeResults: string;
  status: BacktestStatus;
  progress: number;
  startedAt: string | null;
  completedAt: string | null;
  errorLog: string | null;
  autoBackfillAttempted?: boolean;
  system: {
    id: string;
    name: string;
    category: string;
    icon: string;
    primaryTimeframe: string;
    allocationMethod: string;
  };
  operations?: Array<{
    id: string;
    tokenSymbol: string;
    tokenAddress: string;
    operationType: string;
    entryPrice: number;
    exitPrice: number | null;
    entryTime: string;
    exitTime: string | null;
    pnlUsd: number | null;
    pnlPct: number | null;
    holdTimeMin: number | null;
    exitReason: string | null;
    quantity: number;
    positionSizeUsd: number;
  }>;
}

/** Trading system for the dropdown */
interface TradingSystemItem {
  id: string;
  name: string;
  category: string;
  icon: string;
  primaryTimeframe: string;
  allocationMethod: string;
}

/** New backtest form data */
interface NewBacktestForm {
  systemId: string;
  mode: string;
  periodStart: string;
  periodEnd: string;
  initialCapital: number;
  allocationMethod: string;
}

// ============================================================
// CONSTANTS
// ============================================================

const CATEGORY_COLORS: Record<string, { bg: string; text: string }> = {
  ALPHA_HUNTER: { bg: 'bg-orange-500/15', text: 'text-orange-400' },
  SMART_MONEY: { bg: 'bg-emerald-500/15', text: 'text-emerald-400' },
  TECHNICAL: { bg: 'bg-blue-500/15', text: 'text-blue-400' },
  DEFENSIVE: { bg: 'bg-cyan-500/15', text: 'text-cyan-400' },
  BOT_AWARE: { bg: 'bg-purple-500/15', text: 'text-purple-400' },
  DEEP_ANALYSIS: { bg: 'bg-pink-500/15', text: 'text-pink-400' },
  MICRO_STRUCTURE: { bg: 'bg-yellow-500/15', text: 'text-yellow-400' },
  ADAPTIVE: { bg: 'bg-rose-500/15', text: 'text-rose-400' },
};

const ALLOCATION_METHODS = [
  { value: 'KELLY_MODIFIED', label: 'Kelly Modified' },
  { value: 'FIXED', label: 'Fixed %' },
  { value: 'RISK_PARITY', label: 'Risk Parity' },
  { value: 'EQUAL_WEIGHT', label: 'Equal Weight' },
];

const BACKTEST_MODES = [
  { value: 'HISTORICAL', label: 'Historical' },
  { value: 'PAPER', label: 'Paper' },
  { value: 'FORWARD', label: 'Forward' },
];

// ============================================================
// HELPERS
// ============================================================

function formatPnl(pct: number): string {
  const sign = pct >= 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
}

function formatCurrency(val: number): string {
  if (val >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes.toFixed(0)}m`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return dateStr;
  }
}

function safeParseJson<T>(jsonStr: string | null | undefined, fallback: T): T {
  if (!jsonStr) return fallback;
  try {
    return JSON.parse(jsonStr) as T;
  } catch {
    return fallback;
  }
}

// ============================================================
// API FETCH FUNCTIONS
// ============================================================

async function fetchBacktests(status?: string): Promise<BacktestListItem[]> {
  const params = new URLSearchParams();
  if (status && status !== 'ALL') params.set('status', status);
  const res = await fetch(`/api/backtest?${params.toString()}`);
  if (!res.ok) throw new Error('Failed to fetch backtests');
  const json = await res.json();
  return json.data ?? [];
}

async function fetchBacktestDetail(id: string): Promise<BacktestDetailData> {
  const res = await fetch(`/api/backtest/${id}`);
  if (!res.ok) throw new Error('Failed to fetch backtest detail');
  const json = await res.json();
  return json.data;
}

async function fetchTradingSystems(): Promise<TradingSystemItem[]> {
  const res = await fetch('/api/trading-systems');
  if (!res.ok) throw new Error('Failed to fetch trading systems');
  const json = await res.json();
  return json.data ?? [];
}

async function createBacktest(form: NewBacktestForm): Promise<BacktestListItem> {
  const res = await fetch('/api/backtest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(form),
  });
  if (!res.ok) {
    const json = await res.json().catch(() => ({}));
    throw new Error(json.error || 'Failed to create backtest');
  }
  const json = await res.json();
  return json.data;
}

async function runBacktest(id: string): Promise<BacktestDetailData> {
  const res = await fetch(`/api/backtest/${id}/run`, { method: 'POST' });
  if (!res.ok) {
    const json = await res.json().catch(() => ({}));
    throw new Error(json.error || 'Failed to run backtest');
  }
  const json = await res.json();
  return json.data;
}

async function deleteBacktest(id: string): Promise<void> {
  const res = await fetch(`/api/backtest/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const json = await res.json().catch(() => ({}));
    throw new Error(json.error || 'Failed to delete backtest');
  }
}

// ============================================================
// BACKTEST CARD
// ============================================================

function BacktestCard({ run, onClick }: { run: BacktestListItem; onClick: () => void }) {
  const statusIcons: Record<BacktestStatus, React.ElementType> = {
    COMPLETED: CheckCircle2,
    RUNNING: Clock,
    FAILED: XCircle,
    PENDING: Clock,
  };
  const statusColors: Record<BacktestStatus, string> = {
    COMPLETED: 'text-emerald-400',
    RUNNING: 'text-yellow-400',
    FAILED: 'text-red-400',
    PENDING: 'text-gray-400',
  };
  const StatusIcon = statusIcons[run.status];
  const catColor = CATEGORY_COLORS[run.systemCategory] || { bg: 'bg-gray-500/15', text: 'text-gray-400' };
  const isPositive = run.totalPnlPct >= 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ borderColor: '#2d3748' }}
      className="bg-[#111827] border border-[#1e293b] rounded-lg p-4 cursor-pointer hover:border-[#2d3748] transition-all group"
      onClick={onClick}
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">{run.systemIcon}</span>
          <span className="font-mono text-sm font-bold text-[#e2e8f0] group-hover:text-[#d4af37] transition-colors">{run.systemName}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusIcon className={`h-3.5 w-3.5 ${statusColors[run.status]}`} />
          <span className={`text-[10px] font-mono ${statusColors[run.status]}`}>{run.status}</span>
        </div>
      </div>

      {run.status === 'RUNNING' && (
        <div className="mb-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] font-mono text-[#64748b]">Progress</span>
            <span className="text-[10px] font-mono text-[#94a3b8]">{(run.progress * 100).toFixed(0)}%</span>
          </div>
          <div className="h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
            <div className="h-full bg-[#d4af37] rounded-full transition-all" style={{ width: `${run.progress * 100}%` }} />
          </div>
        </div>
      )}

      {run.status === 'PENDING' && (
        <div className="mb-3 flex items-center gap-2 bg-yellow-500/10 border border-yellow-500/20 rounded p-2">
          <Clock className="h-3 w-3 text-yellow-400" />
          <span className="text-[10px] font-mono text-yellow-400">Pending — click to run</span>
        </div>
      )}

      {run.status === 'FAILED' && (
        <div className="mb-3 bg-red-500/10 border border-red-500/20 rounded p-2">
          <div className="flex items-center gap-1 mb-1">
            <AlertTriangle className="h-3 w-3 text-red-400" />
            <span className="text-[10px] font-mono text-red-400">Failed</span>
          </div>
          <p className="text-[9px] font-mono text-red-300/70 line-clamp-2">Click for details</p>
        </div>
      )}

      {run.status === 'COMPLETED' && (
        <>
          <div className="grid grid-cols-3 gap-2 mb-3">
            <div>
              <span className="text-[9px] font-mono text-[#64748b] block">PnL</span>
              <span className={`mono-data text-sm font-bold ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                {formatPnl(run.totalPnlPct)}
              </span>
            </div>
            <div>
              <span className="text-[9px] font-mono text-[#64748b] block">Sharpe</span>
              <span className="mono-data text-sm text-[#e2e8f0]">{run.sharpeRatio.toFixed(2)}</span>
            </div>
            <div>
              <span className="text-[9px] font-mono text-[#64748b] block">Win Rate</span>
              <span className="mono-data text-sm text-[#e2e8f0]">{(run.winRate * 100).toFixed(1)}%</span>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <span className="text-[9px] font-mono text-[#64748b] block">Trades</span>
              {run.totalTrades === 0 ? (
                <span className="mono-data text-xs text-orange-400 flex items-center gap-1">
                  <AlertTriangle className="h-2.5 w-2.5" />0
                </span>
              ) : (
                <span className="mono-data text-xs text-[#94a3b8]">{run.totalTrades}</span>
              )}
            </div>
            <div>
              <span className="text-[9px] font-mono text-[#64748b] block">Max DD</span>
              <span className="mono-data text-xs text-red-400">{run.maxDrawdownPct.toFixed(1)}%</span>
            </div>
            <div>
              <span className="text-[9px] font-mono text-[#64748b] block">Capital</span>
              <span className="mono-data text-xs text-[#94a3b8]">{formatCurrency(run.initialCapital)}</span>
            </div>
          </div>
        </>
      )}

      <div className="flex items-center justify-between mt-3 pt-2 border-t border-[#1e293b]">
        <div className="flex items-center gap-2">
          <Badge className={`text-[8px] h-4 px-1.5 font-mono border ${catColor.bg} ${catColor.text}`}>
            {run.systemCategory.replace(/_/g, ' ')}
          </Badge>
          <Badge variant="outline" className="text-[8px] h-4 px-1.5 font-mono border-[#2d3748] text-[#64748b]">
            {run.mode}
          </Badge>
        </div>
        <span className="text-[9px] font-mono text-[#64748b]">
          {formatDate(run.periodStart)} → {formatDate(run.periodEnd)}
        </span>
      </div>
    </motion.div>
  );
}

// ============================================================
// BACKTEST DETAIL
// ============================================================

function BacktestDetailView({
  backtestId,
  onBack,
}: {
  backtestId: string;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();

  const {
    data: run,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['backtest', backtestId],
    queryFn: () => fetchBacktestDetail(backtestId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'RUNNING' || status === 'PENDING' ? 3000 : false;
    },
  });

  const runMutation = useMutation({
    mutationFn: () => runBacktest(backtestId),
    onSuccess: () => {
      toast.success('Backtest started');
      queryClient.invalidateQueries({ queryKey: ['backtest', backtestId] });
      queryClient.invalidateQueries({ queryKey: ['backtests'] });
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to run backtest');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteBacktest(backtestId),
    onSuccess: () => {
      toast.success('Backtest deleted');
      queryClient.invalidateQueries({ queryKey: ['backtests'] });
      onBack();
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to delete backtest');
    },
  });

  if (isLoading) {
    return (
      <div className="h-full flex flex-col bg-[#0a0e17]">
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
          <Button variant="ghost" size="sm" onClick={onBack} className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]">
            <ChevronRight className="h-3 w-3 mr-1 rotate-180" /> Back
          </Button>
          <Skeleton className="h-4 w-40 bg-[#1e293b]" />
        </div>
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 bg-[#111827] border border-[#1e293b] rounded-lg" />
            ))}
          </div>
          <Skeleton className="h-32 bg-[#111827] border border-[#1e293b] rounded-lg" />
          <Skeleton className="h-32 bg-[#111827] border border-[#1e293b] rounded-lg" />
        </div>
      </div>
    );
  }

  if (isError || !run) {
    return (
      <div className="h-full flex flex-col bg-[#0a0e17]">
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
          <Button variant="ghost" size="sm" onClick={onBack} className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]">
            <ChevronRight className="h-3 w-3 mr-1 rotate-180" /> Back
          </Button>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <AlertCircle className="h-8 w-8 text-red-400 mx-auto mb-3" />
            <p className="font-mono text-sm text-red-400">{(error as Error)?.message || 'Failed to load backtest detail'}</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3 text-[10px] font-mono border-[#2d3748] text-[#94a3b8]"
              onClick={onBack}
            >
              Go Back
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const catColor = CATEGORY_COLORS[run.system.category] || { bg: 'bg-gray-500/15', text: 'text-gray-400' };
  const isPositive = run.totalPnlPct >= 0;
  const parsedPhaseResults = safeParseJson<Record<string, { trades: number; winRate: number; pnlPct: number }>>(
    run.phaseResults,
    {}
  );
  const parsedTimeframeResults = safeParseJson<Record<string, { trades: number; winRate: number; pnlPct?: number }>>(
    run.timeframeResults,
    {}
  );

  return (
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={onBack} className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]">
            <ChevronRight className="h-3 w-3 mr-1 rotate-180" /> Back
          </Button>
          <span className="text-lg">{run.system.icon}</span>
          <span className="font-mono text-sm font-bold text-[#e2e8f0]">{run.system.name}</span>
          <Badge className={`text-[9px] h-5 px-1.5 font-mono border ${catColor.bg} ${catColor.text}`}>
            {run.system.category.replace(/_/g, ' ')}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          {run.status === 'PENDING' && (
            <Button
              size="sm"
              className="h-7 px-3 text-[10px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30"
              onClick={() => runMutation.mutate()}
              disabled={runMutation.isPending}
            >
              {runMutation.isPending ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <Play className="h-3 w-3 mr-1" />
              )}
              Run Backtest
            </Button>
          )}
          {(run.status === 'COMPLETED' || run.status === 'FAILED') && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 px-3 text-[10px] font-mono border-red-500/30 text-red-400 hover:bg-red-500/10 hover:text-red-300"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <Trash2 className="h-3 w-3 mr-1" />
              )}
              Delete
            </Button>
          )}
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {/* Error Log */}
          {run.status === 'FAILED' && run.errorLog && (
            <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-2">
                <AlertTriangle className="h-4 w-4 text-red-400" />
                <h3 className="text-[11px] font-mono text-red-400 uppercase tracking-wider">Error Log</h3>
              </div>
              <p className="text-[10px] font-mono text-red-300/70 whitespace-pre-wrap">{run.errorLog}</p>
            </div>
          )}

          {/* Auto-Backfill Info Banner */}
          {run.autoBackfillAttempted && (
            <div className="bg-cyan-500/10 border border-cyan-500/20 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <Info className="h-4 w-4 text-cyan-400 shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-[11px] font-mono text-cyan-400 uppercase tracking-wider mb-1">Auto-Backfill</h3>
                  <p className="text-[10px] font-mono text-cyan-300/70">
                    Se intentó cargar datos OHLCV automáticamente. Ejecutando backtest con los datos disponibles.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Running Progress — prominent */}
          {run.status === 'RUNNING' && (
            <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-5">
              <div className="flex items-center gap-2 mb-4">
                <Loader2 className="h-5 w-5 text-yellow-400 animate-spin" />
                <h3 className="text-[11px] font-mono text-yellow-400 uppercase tracking-wider">Running Backtest</h3>
              </div>
              {/* Large progress percentage */}
              <div className="text-center mb-4">
                <span className="mono-data text-4xl font-bold text-[#d4af37]">
                  {(run.progress * 100).toFixed(0)}%
                </span>
              </div>
              {/* Animated progress bar */}
              <div className="h-3 bg-[#1e293b] rounded-full overflow-hidden">
                <motion.div
                  className="h-full bg-gradient-to-r from-[#d4af37] to-[#f5d77a] rounded-full"
                  initial={{ width: 0 }}
                  animate={{ width: `${run.progress * 100}%` }}
                  transition={{ duration: 0.8, ease: 'easeOut' }}
                />
              </div>
              <div className="flex justify-between mt-2">
                <span className="text-[9px] font-mono text-[#64748b]">Progress</span>
                <span className="text-[9px] font-mono text-[#94a3b8]">{(run.progress * 100).toFixed(0)}%</span>
              </div>
            </div>
          )}

          {/* No Data Warning — COMPLETED with 0 trades */}
          {run.status === 'COMPLETED' && run.totalTrades === 0 && (
            <div className="bg-orange-500/10 border border-orange-500/20 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="h-5 w-5 text-orange-400 shrink-0 mt-0.5" />
                <div>
                  <h3 className="text-[11px] font-mono text-orange-400 uppercase tracking-wider mb-2">No se ejecutaron operaciones</h3>
                  <p className="text-[10px] font-mono text-orange-300/70 mb-1">
                    Probablemente no hay datos OHLCV disponibles.
                  </p>
                  <p className="text-[10px] font-mono text-orange-300/70 mb-1">
                    <span className="text-orange-400">Solución:</span> El auto-backfill ahora intenta cargar datos automáticamente.
                  </p>
                  <p className="text-[10px] font-mono text-orange-300/70">
                    Intenta ejecutar el backtest de nuevo, o ejecuta <code className="text-orange-400 bg-orange-500/10 px-1 py-0.5 rounded">POST /api/seed</code> para cargar datos iniciales.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Key Metrics */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <span className="text-[9px] font-mono text-[#64748b] block mb-1">Total PnL</span>
              <span className={`mono-data text-xl font-bold ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                {formatPnl(run.totalPnlPct)}
              </span>
              <span className="text-[9px] font-mono text-[#64748b] block mt-0.5">
                {run.status === 'COMPLETED' ? `${formatCurrency(run.finalCapital - run.initialCapital)} profit` : '—'}
              </span>
            </div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <span className="text-[9px] font-mono text-[#64748b] block mb-1">Sharpe Ratio</span>
              <span className="mono-data text-xl font-bold text-[#e2e8f0]">{run.sharpeRatio.toFixed(2)}</span>
              <span className="text-[9px] font-mono text-[#64748b] block mt-0.5">
                {run.sharpeRatio >= 2 ? 'Excellent' : run.sharpeRatio >= 1 ? 'Good' : 'Fair'}
              </span>
            </div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <span className="text-[9px] font-mono text-[#64748b] block mb-1">Win Rate</span>
              <span className="mono-data text-xl font-bold text-[#e2e8f0]">{(run.winRate * 100).toFixed(1)}%</span>
              <span className="text-[9px] font-mono text-[#64748b] block mt-0.5">
                {run.winTrades}W / {run.lossTrades}L
              </span>
            </div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <span className="text-[9px] font-mono text-[#64748b] block mb-1">Max Drawdown</span>
              <span className="mono-data text-xl font-bold text-red-400">{run.maxDrawdownPct.toFixed(1)}%</span>
              <span className="text-[9px] font-mono text-[#64748b] block mt-0.5">Worst peak-to-trough</span>
            </div>
          </div>

          {/* Secondary Metrics */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <span className="text-[9px] font-mono text-[#64748b] block mb-1">Profit Factor</span>
              <span className="mono-data text-sm font-bold text-[#e2e8f0]">{run.profitFactor.toFixed(2)}</span>
            </div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <span className="text-[9px] font-mono text-[#64748b] block mb-1">Total Trades</span>
              <span className="mono-data text-sm font-bold text-[#e2e8f0]">{run.totalTrades}</span>
            </div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <span className="text-[9px] font-mono text-[#64748b] block mb-1">Avg Hold Time</span>
              <span className="mono-data text-sm font-bold text-[#e2e8f0]">{formatDuration(run.avgHoldTimeMin)}</span>
            </div>
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
              <span className="text-[9px] font-mono text-[#64748b] block mb-1">Market Exposure</span>
              <span className="mono-data text-sm font-bold text-[#e2e8f0]">{run.marketExposurePct}%</span>
            </div>
          </div>

          {/* Capital Info */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-4">
            <h3 className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider mb-3 flex items-center gap-2">
              <Gauge className="h-3.5 w-3.5 text-[#d4af37]" /> Capital Overview
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <span className="text-[9px] font-mono text-[#64748b] block">Initial</span>
                <span className="mono-data text-sm text-[#e2e8f0]">{formatCurrency(run.initialCapital)}</span>
              </div>
              <div>
                <span className="text-[9px] font-mono text-[#64748b] block">Final</span>
                <span className={`mono-data text-sm ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                  {run.status === 'COMPLETED' ? formatCurrency(run.finalCapital) : '—'}
                </span>
              </div>
              <div>
                <span className="text-[9px] font-mono text-[#64748b] block">Net P&L</span>
                <span className={`mono-data text-sm ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                  {run.status === 'COMPLETED' ? `${isPositive ? '+' : ''}${formatCurrency(run.finalCapital - run.initialCapital)}` : '—'}
                </span>
              </div>
            </div>
          </div>

          {/* Phase Results */}
          {Object.keys(parsedPhaseResults).length > 0 && (
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-4">
              <h3 className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider mb-3 flex items-center gap-2">
                <Target className="h-3.5 w-3.5 text-[#d4af37]" /> Phase Breakdown
              </h3>
              <div className="space-y-2">
                {Object.entries(parsedPhaseResults).map(([phase, data]) => (
                  <div key={phase} className="flex items-center gap-3 py-1.5 border-b border-[#1e293b] last:border-0">
                    <Badge variant="outline" className="text-[9px] h-5 px-1.5 font-mono border-[#2d3748] text-[#94a3b8] w-24 justify-center">
                      {phase}
                    </Badge>
                    <span className="text-[10px] font-mono text-[#64748b] w-16">{data.trades} trades</span>
                    <span className="text-[10px] font-mono text-[#94a3b8] w-16">WR: {(data.winRate * 100).toFixed(0)}%</span>
                    <span className={`text-[10px] font-mono ${data.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'} w-16`}>
                      {formatPnl(data.pnlPct)}
                    </span>
                    <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${data.pnlPct >= 0 ? 'bg-emerald-500' : 'bg-red-500'}`}
                        style={{ width: `${Math.min(Math.abs(data.pnlPct), 50)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Timeframe Results */}
          {Object.keys(parsedTimeframeResults).length > 0 && (
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-4">
              <h3 className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider mb-3 flex items-center gap-2">
                <BarChart3 className="h-3.5 w-3.5 text-[#d4af37]" /> Timeframe Analysis
              </h3>
              <div className="space-y-2">
                {Object.entries(parsedTimeframeResults).map(([tf, data]) => (
                  <div key={tf} className="flex items-center gap-3 py-1.5 border-b border-[#1e293b] last:border-0">
                    <Badge variant="outline" className="text-[9px] h-5 px-1.5 font-mono border-[#2d3748] text-[#94a3b8] w-16 justify-center">
                      {tf}
                    </Badge>
                    <span className="text-[10px] font-mono text-[#64748b] w-16">{data.trades} trades</span>
                    <span className="text-[10px] font-mono text-[#94a3b8] w-16">WR: {(data.winRate * 100).toFixed(0)}%</span>
                    {data.pnlPct !== undefined && (
                      <span className={`text-[10px] font-mono ${data.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {formatPnl(data.pnlPct)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Individual Trade Operations */}
          {run.operations && run.operations.length > 0 && (
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-4">
              <h3 className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider mb-3 flex items-center gap-2">
                <BarChart3 className="h-3.5 w-3.5 text-[#d4af37]" /> Trade Operations ({run.operations.length})
              </h3>
              <div className="max-h-64 overflow-y-auto" style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
                <table className="w-full text-[9px] font-mono">
                  <thead>
                    <tr className="text-[#475569] uppercase border-b border-[#1e293b]/50">
                      <th className="py-1 px-1 text-left">Token</th>
                      <th className="py-1 px-1 text-left">Dir</th>
                      <th className="py-1 px-1 text-right">Entry</th>
                      <th className="py-1 px-1 text-right">Exit</th>
                      <th className="py-1 px-1 text-right">PnL%</th>
                      <th className="py-1 px-1 text-right">PnL $</th>
                      <th className="py-1 px-1 text-right">Hold</th>
                      <th className="py-1 px-1 text-left">Exit Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.operations.map((op) => (
                      <tr key={op.id} className="border-b border-[#1e293b]/30 hover:bg-[#0a0e17]">
                        <td className="py-1 px-1 text-[#e2e8f0]">{op.tokenSymbol || op.tokenAddress.slice(0, 8)}</td>
                        <td className={`py-1 px-1 ${op.operationType === 'LONG' || op.operationType === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                          {op.operationType.slice(0, 4)}
                        </td>
                        <td className="py-1 px-1 text-right text-[#94a3b8]">{op.entryPrice.toFixed(6)}</td>
                        <td className="py-1 px-1 text-right text-[#94a3b8]">{op.exitPrice?.toFixed(6) ?? '-'}</td>
                        <td className={`py-1 px-1 text-right font-bold ${(op.pnlPct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {(op.pnlPct ?? 0) >= 0 ? '+' : ''}{(op.pnlPct ?? 0).toFixed(1)}%
                        </td>
                        <td className={`py-1 px-1 text-right ${(op.pnlUsd ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {(op.pnlUsd ?? 0) >= 0 ? '+' : ''}{(op.pnlUsd ?? 0).toFixed(2)}
                        </td>
                        <td className="py-1 px-1 text-right text-[#94a3b8]">{op.holdTimeMin !== null ? `${op.holdTimeMin}m` : '-'}</td>
                        <td className="py-1 px-1 text-[#475569] max-w-[80px] truncate">{op.exitReason || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Period & Timing */}
          <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-4">
            <h3 className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider mb-3 flex items-center gap-2">
              <Activity className="h-3.5 w-3.5 text-[#d4af37]" /> Run Info
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <span className="text-[9px] font-mono text-[#64748b] block">Period</span>
                <span className="text-[10px] font-mono text-[#94a3b8]">{formatDate(run.periodStart)} → {formatDate(run.periodEnd)}</span>
              </div>
              <div>
                <span className="text-[9px] font-mono text-[#64748b] block">Mode</span>
                <span className="text-[10px] font-mono text-[#94a3b8]">{run.mode}</span>
              </div>
              <div>
                <span className="text-[9px] font-mono text-[#64748b] block">Started</span>
                <span className="text-[10px] font-mono text-[#94a3b8]">
                  {run.startedAt ? new Date(run.startedAt).toLocaleString() : '—'}
                </span>
              </div>
              <div>
                <span className="text-[9px] font-mono text-[#64748b] block">Completed</span>
                <span className="text-[10px] font-mono text-[#94a3b8]">
                  {run.completedAt ? new Date(run.completedAt).toLocaleString() : '—'}
                </span>
              </div>
            </div>
          </div>
        </div>
      </ScrollArea>
    </motion.div>
  );
}

// ============================================================
// NEW BACKTEST DIALOG
// ============================================================

function NewBacktestDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<NewBacktestForm>({
    systemId: '',
    mode: 'HISTORICAL',
    periodStart: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
    periodEnd: new Date().toISOString().split('T')[0],
    initialCapital: 10000,
    allocationMethod: 'KELLY_MODIFIED',
  });

  const queryClient = useQueryClient();

  const { data: systems, isLoading: systemsLoading } = useQuery({
    queryKey: ['trading-systems'],
    queryFn: fetchTradingSystems,
    enabled: open,
  });

  const createMutation = useMutation({
    mutationFn: createBacktest,
    onSuccess: () => {
      toast.success('Backtest created');
      queryClient.invalidateQueries({ queryKey: ['backtests'] });
      setOpen(false);
      setForm({
        systemId: '',
        mode: 'HISTORICAL',
        periodStart: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
        periodEnd: new Date().toISOString().split('T')[0],
        initialCapital: 10000,
        allocationMethod: 'KELLY_MODIFIED',
      });
      onCreated();
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to create backtest');
    },
  });

  const handleSubmit = useCallback(() => {
    if (!form.systemId) {
      toast.error('Please select a trading system');
      return;
    }
    createMutation.mutate(form);
  }, [form, createMutation]);

  const selectedSystem = systems?.find((s) => s.id === form.systemId);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" className="h-7 px-3 text-[10px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30">
          <Plus className="h-3 w-3 mr-1" /> New Backtest
        </Button>
      </DialogTrigger>
      <DialogContent className="bg-[#0d1117] border-[#1e293b] text-[#e2e8f0] max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-[#d4af37]" />
            New Backtest
          </DialogTitle>
          <DialogDescription className="text-[10px] font-mono text-[#64748b]">
            Configure and create a new backtest run for a trading system.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Trading System */}
          <div className="space-y-1.5">
            <Label className="text-[10px] font-mono text-[#94a3b8]">Trading System *</Label>
            <Select
              value={form.systemId}
              onValueChange={(v) => {
                const sys = systems?.find((s) => s.id === v);
                setForm((prev) => ({
                  ...prev,
                  systemId: v,
                  allocationMethod: sys?.allocationMethod || prev.allocationMethod,
                }));
              }}
            >
              <SelectTrigger className="h-8 text-[10px] font-mono bg-[#111827] border-[#1e293b] text-[#e2e8f0]">
                <SelectValue placeholder={systemsLoading ? 'Loading systems...' : 'Select a system'} />
              </SelectTrigger>
              <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                {systems?.map((sys) => (
                  <SelectItem key={sys.id} value={sys.id} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">
                    {sys.icon} {sys.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedSystem && (
              <div className="flex items-center gap-2 mt-1">
                <Badge className={`text-[8px] h-4 px-1.5 font-mono border ${CATEGORY_COLORS[selectedSystem.category]?.bg || 'bg-gray-500/15'} ${CATEGORY_COLORS[selectedSystem.category]?.text || 'text-gray-400'}`}>
                  {selectedSystem.category.replace(/_/g, ' ')}
                </Badge>
                <span className="text-[9px] font-mono text-[#64748b]">{selectedSystem.primaryTimeframe}</span>
              </div>
            )}
          </div>

          {/* Mode */}
          <div className="space-y-1.5">
            <Label className="text-[10px] font-mono text-[#94a3b8]">Mode</Label>
            <Select value={form.mode} onValueChange={(v) => setForm((prev) => ({ ...prev, mode: v }))}>
              <SelectTrigger className="h-8 text-[10px] font-mono bg-[#111827] border-[#1e293b] text-[#e2e8f0]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                {BACKTEST_MODES.map((m) => (
                  <SelectItem key={m.value} value={m.value} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Period */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-[10px] font-mono text-[#94a3b8]">Period Start</Label>
              <Input
                type="date"
                value={form.periodStart}
                onChange={(e) => setForm((prev) => ({ ...prev, periodStart: e.target.value }))}
                className="h-8 text-[10px] font-mono bg-[#111827] border-[#1e293b] text-[#e2e8f0]"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-[10px] font-mono text-[#94a3b8]">Period End</Label>
              <Input
                type="date"
                value={form.periodEnd}
                onChange={(e) => setForm((prev) => ({ ...prev, periodEnd: e.target.value }))}
                className="h-8 text-[10px] font-mono bg-[#111827] border-[#1e293b] text-[#e2e8f0]"
              />
            </div>
          </div>

          {/* Initial Capital */}
          <div className="space-y-1.5">
            <Label className="text-[10px] font-mono text-[#94a3b8]">Initial Capital ($)</Label>
            <Input
              type="number"
              min={100}
              step={1000}
              value={form.initialCapital}
              onChange={(e) => setForm((prev) => ({ ...prev, initialCapital: Number(e.target.value) || 10000 }))}
              className="h-8 text-[10px] font-mono bg-[#111827] border-[#1e293b] text-[#e2e8f0]"
            />
          </div>

          {/* Allocation Method */}
          <div className="space-y-1.5">
            <Label className="text-[10px] font-mono text-[#94a3b8]">Allocation Method</Label>
            <Select value={form.allocationMethod} onValueChange={(v) => setForm((prev) => ({ ...prev, allocationMethod: v }))}>
              <SelectTrigger className="h-8 text-[10px] font-mono bg-[#111827] border-[#1e293b] text-[#e2e8f0]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                {ALLOCATION_METHODS.map((m) => (
                  <SelectItem key={m.value} value={m.value} className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            size="sm"
            className="text-[10px] font-mono border-[#2d3748] text-[#94a3b8]"
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            className="text-[10px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30"
            onClick={handleSubmit}
            disabled={createMutation.isPending || !form.systemId}
          >
            {createMutation.isPending ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Play className="h-3 w-3 mr-1" />
            )}
            Create Backtest
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// SKELETON CARDS
// ============================================================

function BacktestCardSkeleton() {
  return (
    <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-4">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <Skeleton className="h-5 w-5 rounded bg-[#1e293b]" />
          <Skeleton className="h-4 w-28 bg-[#1e293b]" />
        </div>
        <Skeleton className="h-3 w-16 bg-[#1e293b]" />
      </div>
      <div className="grid grid-cols-3 gap-2 mb-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i}>
            <Skeleton className="h-2.5 w-8 bg-[#1e293b] mb-1" />
            <Skeleton className="h-4 w-12 bg-[#1e293b]" />
          </div>
        ))}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i}>
            <Skeleton className="h-2.5 w-8 bg-[#1e293b] mb-1" />
            <Skeleton className="h-3 w-10 bg-[#1e293b]" />
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between mt-3 pt-2 border-t border-[#1e293b]">
        <div className="flex items-center gap-2">
          <Skeleton className="h-4 w-20 bg-[#1e293b]" />
          <Skeleton className="h-4 w-14 bg-[#1e293b]" />
        </div>
        <Skeleton className="h-3 w-24 bg-[#1e293b]" />
      </div>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function BacktestingLab() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  const queryClient = useQueryClient();

  const {
    data: backtests,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ['backtests', statusFilter],
    queryFn: () => fetchBacktests(statusFilter),
    refetchInterval: (query) => {
      const hasRunning = query.state.data?.some((b) => b.status === 'RUNNING' || b.status === 'PENDING');
      return hasRunning ? 5000 : false;
    },
  });

  const completedCount = backtests?.filter((b) => b.status === 'COMPLETED').length ?? 0;
  const runningCount = backtests?.filter((b) => b.status === 'RUNNING').length ?? 0;
  const failedCount = backtests?.filter((b) => b.status === 'FAILED').length ?? 0;

  const handleCreated = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['backtests'] });
  }, [queryClient]);

  // Detail view
  if (selectedId) {
    return (
      <div className="h-full bg-[#0a0e17]">
        <BacktestDetailView
          backtestId={selectedId}
          onBack={() => setSelectedId(null)}
        />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-[#0a0e17]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <div className="flex items-center gap-3">
          <FlaskConical className="h-4 w-4 text-[#d4af37]" />
          <span className="font-mono text-sm font-bold text-[#e2e8f0]">Backtesting Lab</span>
          <div className="flex items-center gap-2 ml-4">
            <Badge className="text-[9px] h-5 px-1.5 font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
              <CheckCircle2 className="h-2.5 w-2.5 mr-0.5" /> {completedCount} Completed
            </Badge>
            <Badge className="text-[9px] h-5 px-1.5 font-mono bg-yellow-500/15 text-yellow-400 border border-yellow-500/30">
              <Clock className="h-2.5 w-2.5 mr-0.5" /> {runningCount} Running
            </Badge>
            <Badge className="text-[9px] h-5 px-1.5 font-mono bg-red-500/15 text-red-400 border border-red-500/30">
              <XCircle className="h-2.5 w-2.5 mr-0.5" /> {failedCount} Failed
            </Badge>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="h-7 w-32 text-[10px] font-mono bg-[#1a1f2e] border-[#2d3748] text-[#94a3b8]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
              <SelectItem value="ALL" className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">All Status</SelectItem>
              <SelectItem value="COMPLETED" className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">Completed</SelectItem>
              <SelectItem value="RUNNING" className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">Running</SelectItem>
              <SelectItem value="PENDING" className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">Pending</SelectItem>
              <SelectItem value="FAILED" className="text-[10px] font-mono text-[#e2e8f0] focus:bg-[#2d3748]">Failed</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0 text-[#64748b] hover:text-[#e2e8f0]"
            onClick={() => refetch()}
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <NewBacktestDialog onCreated={handleCreated} />
        </div>
      </div>

      {/* Content */}
      <ScrollArea className="flex-1">
        <div className="p-4">
          {/* Error State */}
          {isError && (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <AlertCircle className="h-8 w-8 mb-3 text-red-400" />
              <p className="font-mono text-sm text-red-400 mb-2">{(error as Error)?.message || 'Failed to load backtests'}</p>
              <Button
                variant="outline"
                size="sm"
                className="text-[10px] font-mono border-[#2d3748] text-[#94a3b8]"
                onClick={() => refetch()}
              >
                <RefreshCw className="h-3 w-3 mr-1" /> Retry
              </Button>
            </div>
          )}

          {/* Loading State */}
          {isLoading && (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <BacktestCardSkeleton key={i} />
              ))}
            </div>
          )}

          {/* Loaded State */}
          {!isLoading && !isError && (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              <AnimatePresence mode="popLayout">
                {backtests?.map((run) => (
                  <BacktestCard key={run.id} run={run} onClick={() => setSelectedId(run.id)} />
                ))}
              </AnimatePresence>
            </div>
          )}

          {/* Empty State */}
          {!isLoading && !isError && backtests?.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-[#64748b]">
              <FlaskConical className="h-8 w-8 mb-3 opacity-30" />
              <span className="font-mono text-xs">No backtests found for this filter</span>
              <span className="font-mono text-[10px] mt-1 text-[#4a5568]">Create a new backtest to get started</span>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
