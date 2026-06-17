'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Separator } from '@/components/ui/separator';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
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
  ChevronDown,
  ChevronRight,
  Loader2,
  Settings2,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react';
import { AutoSyncPanel } from './auto-sync-panel';

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

interface PersistedState {
  startedAt: string | null;
  stoppedAt: string | null;
  totalCycles: number;
  lastCycleNumber: number;
  capitalUsd: number;
  initialCapitalUsd: number;
  chain: string;
  scanLimit: number;
  lastError: string | null;
  updatedAt: string;
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
  persisted: PersistedState | null;
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
  if (days > 0) return `${days}d ${hours}h ${minutes}m ${seconds}s`;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatInterval(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${Math.round(ms / 1000)}s`;
  if (ms < 3600000) {
    const mins = Math.floor(ms / 60000);
    const secs = Math.round((ms % 60000) / 1000);
    return secs > 0 ? `${mins}m ${secs}s` : `${mins} min`;
  }
  const hrs = Math.floor(ms / 3600000);
  const mins = Math.round((ms % 3600000) / 60000);
  return mins > 0 ? `${hrs}h ${mins}m` : `${hrs}h`;
}

function timeAgo(iso: string | null): string {
  if (!iso) return 'Never';
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return 'just now';
  if (diff < 5000) return 'just now';
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function formatCountdown(iso: string | null): string {
  if (!iso) return '—';
  const diff = new Date(iso).getTime() - Date.now();
  if (diff <= 0) return 'now';
  if (diff < 60000) return `${Math.ceil(diff / 1000)}s`;
  if (diff < 3600000) {
    const mins = Math.floor(diff / 60000);
    const secs = Math.ceil((diff % 60000) / 1000);
    return secs >= 60 ? `${mins + 1}m` : `${mins}m ${secs}s`;
  }
  const hrs = Math.floor(diff / 3600000);
  const mins = Math.ceil((diff % 3600000) / 60000);
  return mins >= 60 ? `${hrs + 1}h` : `${hrs}h ${mins}m`;
}

function formatDuration(ms: number): string {
  if (ms <= 0) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function formatCurrency(val: number | undefined | null): string {
  if (val == null || isNaN(val)) return '$0.00';
  if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
  if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(2)}K`;
  return `$${val.toFixed(2)}`;
}

function formatTaskName(name: string): string {
  return name
    .replace(/([A-Z])/g, ' $1')
    .replace(/[_-]/g, ' ')
    .replace(/^./, (s) => s.toUpperCase())
    .replace(/\s+/g, ' ')
    .trim();
}

// ============================================================
// STATUS CONFIG
// ============================================================

const STATUS_CONFIG: Record<
  string,
  { label: string; color: string; bg: string; border: string; dot: string; animate?: string }
> = {
  RUNNING: {
    label: 'RUNNING',
    color: 'text-emerald-400',
    bg: 'bg-emerald-500/10',
    border: 'border-emerald-500/30',
    dot: 'bg-emerald-500',
    animate: 'animate-pulse',
  },
  STARTING: {
    label: 'STARTING',
    color: 'text-yellow-400',
    bg: 'bg-yellow-500/10',
    border: 'border-yellow-500/30',
    dot: 'bg-yellow-500',
    animate: 'animate-pulse',
  },
  PAUSED: {
    label: 'PAUSED',
    color: 'text-amber-400',
    bg: 'bg-amber-500/10',
    border: 'border-amber-500/30',
    dot: 'bg-amber-500',
  },
  STOPPED: {
    label: 'STOPPED',
    color: 'text-gray-400',
    bg: 'bg-gray-500/10',
    border: 'border-gray-500/30',
    dot: 'bg-gray-500',
  },
  ERROR: {
    label: 'ERROR',
    color: 'text-red-400',
    bg: 'bg-red-500/10',
    border: 'border-red-500/30',
    dot: 'bg-red-500',
    animate: 'animate-pulse',
  },
};

const TASK_STATUS_CONFIG: Record<string, { color: string; dot: string; label: string }> = {
  running: { color: 'text-emerald-400', dot: 'bg-emerald-500', label: 'Running' },
  idle: { color: 'text-gray-400', dot: 'bg-gray-500', label: 'Idle' },
  error: { color: 'text-red-400', dot: 'bg-red-500', label: 'Error' },
};

// ============================================================
// SKELETON LOADER
// ============================================================

function BrainControlSkeleton() {
  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0a0e17]">
        <Brain className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">
          Brain Control Panel
        </span>
      </div>
      <div className="flex-1 p-3 space-y-3 overflow-y-auto">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20 bg-[#1a1f2e] rounded-md" />
          ))}
        </div>
        <Skeleton className="h-8 bg-[#1a1f2e] rounded-md w-48" />
        <div className="space-y-1">
          {[1, 2, 3, 4, 5, 6, 7].map((i) => (
            <Skeleton key={i} className="h-8 bg-[#1a1f2e] rounded" />
          ))}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <Skeleton className="h-28 bg-[#1a1f2e] rounded-md" />
          <Skeleton className="h-28 bg-[#1a1f2e] rounded-md" />
        </div>
      </div>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function BrainControl() {
  const queryClient = useQueryClient();
  const [configOpen, setConfigOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState<string | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Countdown ticker every second
  useEffect(() => {
    countdownRef.current = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
  }, []);

  // Fetch scheduler status via useQuery
  const { data: schedulerData, isLoading: schedulerLoading, refetch: refetchScheduler } = useQuery({
    queryKey: ['brain-scheduler'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/brain/scheduler');
        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          throw new Error(errBody.error || `HTTP ${res.status}`);
        }
        const json = await res.json();
        return json.data as SchedulerStatusReport;
      } catch (err: any) {
        throw new Error(err.message || 'Failed to fetch scheduler status');
      }
    },
    refetchInterval: 15000,
    staleTime: 10000,
  });

  // Fetch brain status via useQuery
  const { data: brainStatusData } = useQuery({
    queryKey: ['brain-status'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/brain/status');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as Record<string, unknown> | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 20000,
  });

  const data = schedulerData;

  // Scheduler action mutation (start, stop, pause, resume)
  const schedulerMutation = useMutation({
    mutationFn: async ({ action, params }: { action: 'start' | 'stop' | 'pause' | 'resume'; params?: Record<string, unknown> }) => {
      const res = await fetch('/api/brain/scheduler', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, params }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `Action failed: HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['brain-scheduler'] });
      queryClient.invalidateQueries({ queryKey: ['brain-status'] });
      setError(null);
    },
    onError: (err: Error) => {
      setError(err.message || 'Scheduler action failed');
    },
  });

  // Run cycle mutation
  const cycleMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/brain/pipeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || 'Cycle failed');
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['brain-scheduler'] });
      queryClient.invalidateQueries({ queryKey: ['brain-status'] });
    },
    onError: (err: Error) => {
      setError(err.message || 'Failed to run cycle');
    },
  });

  // Force sync mutation — trigger a brain pipeline run with current config
  const syncMutation = useMutation({
    mutationFn: async () => {
      const chain = data?.config?.chain || 'SOL';
      const res = await fetch('/api/brain/pipeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chain }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || 'Sync failed');
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['brain-scheduler'] });
      queryClient.invalidateQueries({ queryKey: ['brain-status'] });
    },
    onError: (err: Error) => {
      setError(err.message || 'Failed to force sync');
    },
  });

  // Start all mutation
  const startAllMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/brain/start-all', { method: 'POST' });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || 'Start All failed');
      }
      return res.json();
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['brain-scheduler'] });
      queryClient.invalidateQueries({ queryKey: ['brain-status'] });
      if (!result.success) {
        setError(result.message || 'Start All partially failed');
      } else {
        setError(null);
      }
    },
    onError: (err: Error) => {
      setError(err.message || 'Start All failed');
    },
  });

  // Computed action loading state
  const actionLoading = schedulerMutation.isPending
    ? schedulerMutation.variables?.action ?? 'scheduler'
    : cycleMutation.isPending
      ? 'cycle'
      : syncMutation.isPending
        ? 'sync'
        : startAllMutation.isPending
          ? 'startall'
          : null;

  // Loading state
  if (schedulerLoading && !data) {
    return <BrainControlSkeleton />;
  }

  const status = data?.status || 'STOPPED';
  const statusCfg = STATUS_CONFIG[status] || STATUS_CONFIG.STOPPED;

  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* ─── Header ─── */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0a0e17] shrink-0">
        <Brain className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">
          Brain Control Panel
        </span>
        <div className="ml-auto flex items-center gap-2">
          {/* Status dot in header */}
          <span className="flex items-center gap-1">
            <span
              className={`h-1.5 w-1.5 rounded-full ${statusCfg.dot} ${statusCfg.animate || ''}`}
            />
            <span className={`text-[9px] font-mono font-bold ${statusCfg.color}`}>
              {statusCfg.label}
            </span>
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => refetchScheduler()}
            disabled={schedulerLoading}
            className="h-5 w-5 p-0 text-[#64748b] hover:text-[#e2e8f0]"
          >
            <RefreshCw className={`h-3 w-3 ${schedulerLoading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* ─── Scrollable content ─── */}
      <div className="flex-1 overflow-y-auto max-h-[calc(100vh-160px)] p-3 space-y-3">
        {/* Error banner */}
        {error && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30">
            <AlertTriangle className="h-3.5 w-3.5 text-red-400 shrink-0" />
            <span className="text-[10px] font-mono text-red-400 flex-1 line-clamp-2">{error}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setError(null)}
              className="h-4 w-4 p-0 text-red-400/60 hover:text-red-400"
            >
              ×
            </Button>
          </div>
        )}

        {/* ─── Section 1: Scheduler Status ─── */}
        <Card className="bg-[#0a0e17] border-[#1e293b] py-0 gap-0">
          <CardHeader className="px-3 py-2">
            <CardTitle className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider flex items-center gap-1.5">
              <Activity className="h-3 w-3 text-[#d4af37]" />
              Scheduler Status
            </CardTitle>
          </CardHeader>
          <CardContent className="px-3 pb-2 pt-0">
            <div className="grid grid-cols-3 gap-2">
              {/* Status badge */}
              <div className="flex flex-col items-center justify-center rounded-md border border-[#1e293b] bg-[#0d1117] p-2">
                <span className="text-[9px] font-mono text-[#64748b] mb-1">STATUS</span>
                <Badge
                  className={`${statusCfg.bg} ${statusCfg.color} ${statusCfg.border} text-[10px] font-mono font-bold border`}
                >
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${statusCfg.dot} ${statusCfg.animate || ''} mr-1`}
                  />
                  {statusCfg.label}
                </Badge>
              </div>
              {/* Uptime */}
              <div className="flex flex-col items-center justify-center rounded-md border border-[#1e293b] bg-[#0d1117] p-2">
                <span className="text-[9px] font-mono text-[#64748b] mb-1">UPTIME</span>
                <span className="text-[12px] font-mono font-bold text-[#e2e8f0]">
                  {data ? formatUptime(data.uptime) : '—'}
                </span>
              </div>
              {/* Total Cycles */}
              <div className="flex flex-col items-center justify-center rounded-md border border-[#1e293b] bg-[#0d1117] p-2">
                <span className="text-[9px] font-mono text-[#64748b] mb-1">CYCLES</span>
                <span className="text-[12px] font-mono font-bold text-cyan-400">
                  {data?.totalCyclesCompleted ?? data?.persisted?.totalCycles ?? '—'}
                </span>
              </div>
            </div>
            {/* Persisted state info (shows even when stopped) */}
            {data?.persisted && (
              <div className="mt-2 grid grid-cols-2 gap-1.5">
                {/* First started at */}
                <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-[#0d1117] border border-[#1e293b]">
                  <Clock className="h-2.5 w-2.5 text-[#475569] shrink-0" />
                  <span className="text-[8px] font-mono text-[#64748b]">First Started</span>
                  <span className="text-[9px] font-mono text-[#94a3b8] ml-auto">
                    {data.persisted.startedAt ? timeAgo(data.persisted.startedAt) : 'Never'}
                  </span>
                </div>
                {/* Last stopped at */}
                <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-[#0d1117] border border-[#1e293b]">
                  <Square className="h-2.5 w-2.5 text-[#475569] shrink-0" />
                  <span className="text-[8px] font-mono text-[#64748b]">Last Stopped</span>
                  <span className="text-[9px] font-mono text-[#94a3b8] ml-auto">
                    {data.persisted.stoppedAt ? timeAgo(data.persisted.stoppedAt) : '—'}
                  </span>
                </div>
              </div>
            )}
            {/* Last error */}
            {(data?.lastError || data?.persisted?.lastError) && (
              <div className="mt-2 flex items-center gap-1.5 px-2 py-1 rounded bg-red-500/5 border border-red-500/20">
                <AlertTriangle className="h-3 w-3 text-red-400 shrink-0" />
                <span className="text-[9px] font-mono text-red-400 line-clamp-1">
                  {data?.lastError || data?.persisted?.lastError}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ─── Section 2: Control Buttons ─── */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <Button
            size="sm"
            onClick={() => schedulerMutation.mutate({ action: 'start', params: data?.config ? {
              capitalUsd: data.config.capitalUsd,
              initialCapitalUsd: data.config.initialCapitalUsd,
              chain: data.config.chain,
              scanLimit: data.config.scanLimit,
            } : undefined })}
            disabled={status === 'RUNNING' || actionLoading !== null}
            className="h-7 px-3 text-[10px] font-mono bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-600/30 hover:text-emerald-300 disabled:opacity-40"
            variant="outline"
          >
            {actionLoading === 'start' ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Play className="h-3 w-3 mr-1" />
            )}
            Start
          </Button>
          <Button
            size="sm"
            onClick={() => schedulerMutation.mutate({ action: 'pause' })}
            disabled={status !== 'RUNNING' || actionLoading !== null}
            className="h-7 px-3 text-[10px] font-mono bg-amber-600/20 text-amber-400 border border-amber-500/30 hover:bg-amber-600/30 hover:text-amber-300 disabled:opacity-40"
            variant="outline"
          >
            {actionLoading === 'pause' ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Pause className="h-3 w-3 mr-1" />
            )}
            Pause
          </Button>
          <Button
            size="sm"
            onClick={() => schedulerMutation.mutate({ action: 'resume' })}
            disabled={status !== 'PAUSED' || actionLoading !== null}
            className="h-7 px-3 text-[10px] font-mono bg-cyan-600/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-600/30 hover:text-cyan-300 disabled:opacity-40"
            variant="outline"
          >
            {actionLoading === 'resume' ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Play className="h-3 w-3 mr-1" />
            )}
            Resume
          </Button>
          <Button
            size="sm"
            onClick={() => schedulerMutation.mutate({ action: 'stop' })}
            disabled={status === 'STOPPED' || actionLoading !== null}
            className="h-7 px-3 text-[10px] font-mono bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600/30 hover:text-red-300 disabled:opacity-40"
            variant="outline"
          >
            {actionLoading === 'stop' ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Square className="h-3 w-3 mr-1" />
            )}
            Stop
          </Button>
        </div>

        <Separator className="bg-[#1e293b]" />

        {/* ─── Section 3: Task Monitor ─── */}
        <Card className="bg-[#0a0e17] border-[#1e293b] py-0 gap-0">
          <CardHeader className="px-3 py-2">
            <CardTitle className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider flex items-center gap-1.5">
              <Clock className="h-3 w-3 text-[#d4af37]" />
              Task Monitor
              {data?.tasks && (
                <span className="ml-1 text-[9px] text-[#475569]">
                  ({data.tasks.length} tasks)
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-3 pb-2 pt-0">
            {data?.tasks && data.tasks.length > 0 ? (
              <div className="rounded-md border border-[#1e293b] overflow-hidden">
                {/* Table header */}
                <div className="grid grid-cols-[1fr_70px_60px_70px_40px_40px_20px] sm:grid-cols-[1fr_70px_70px_80px_50px_50px_24px] gap-1 px-2 py-1 bg-[#0d1117] border-b border-[#1e293b]">
                  <span className="text-[8px] font-mono text-[#475569] uppercase">Task</span>
                  <span className="text-[8px] font-mono text-[#475569] uppercase">Interval</span>
                  <span className="text-[8px] font-mono text-[#475569] uppercase">Last Run</span>
                  <span className="text-[8px] font-mono text-[#475569] uppercase">Next Run</span>
                  <span className="text-[8px] font-mono text-[#475569] uppercase text-center">Runs</span>
                  <span className="text-[8px] font-mono text-[#475569] uppercase text-center">Errs</span>
                  <span className="text-[8px] font-mono text-[#475569] uppercase text-center">●</span>
                </div>
                {/* Table rows */}
                <div className="max-h-72 overflow-y-auto">
                  {data.tasks.map((task, i) => {
                    const taskState = task.isRunning
                      ? 'running'
                      : task.errorCount > 0 && task.lastError
                        ? 'error'
                        : 'idle';
                    const taskCfg = TASK_STATUS_CONFIG[taskState];
                    return (
                      <div
                        key={task.name}
                        className={`grid grid-cols-[1fr_70px_60px_70px_40px_40px_20px] sm:grid-cols-[1fr_70px_70px_80px_50px_50px_24px] gap-1 px-2 py-1.5 border-b border-[#1e293b]/50 hover:bg-[#0d1117]/80 transition-colors ${
                          i % 2 === 0 ? 'bg-[#080b12]' : 'bg-[#0a0e17]'
                        }`}
                      >
                        {/* Task name */}
                        <div className="flex items-center gap-1 min-w-0">
                          <span className="text-[10px] font-mono text-[#e2e8f0] truncate">
                            {formatTaskName(task.name)}
                          </span>
                          {task.isRunning && (
                            <Loader2 className="h-2.5 w-2.5 text-emerald-400 animate-spin shrink-0" />
                          )}
                        </div>
                        {/* Interval */}
                        <span className="text-[10px] font-mono text-[#94a3b8]">
                          {formatInterval(task.intervalMs)}
                        </span>
                        {/* Last Run */}
                        <span className="text-[10px] font-mono text-[#94a3b8]">
                          {timeAgo(task.lastRunAt)}
                        </span>
                        {/* Next Run (countdown) */}
                        <span className="text-[10px] font-mono text-cyan-400/80">
                          {status === 'RUNNING' || status === 'STARTING'
                            ? formatCountdown(task.nextRunAt)
                            : '—'}
                        </span>
                        {/* Run count */}
                        <span className="text-[10px] font-mono text-[#e2e8f0] text-center">
                          {task.runCount}
                        </span>
                        {/* Error count */}
                        <span
                          className={`text-[10px] font-mono text-center ${
                            task.errorCount > 0 ? 'text-red-400' : 'text-[#64748b]'
                          }`}
                        >
                          {task.errorCount}
                        </span>
                        {/* Status dot */}
                        <span className="flex items-center justify-center">
                          <span
                            className={`h-2 w-2 rounded-full ${taskCfg.dot} ${
                              taskState === 'running' ? 'animate-pulse' : ''
                            }`}
                            title={taskCfg.label}
                          />
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-center h-16 text-[10px] font-mono text-[#475569]">
                No tasks loaded
              </div>
            )}
          </CardContent>
        </Card>

        {/* ─── Section 4 & 5: Capital Strategy + Brain Cycle Status ─── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {/* Capital Strategy */}
          <Card className="bg-[#0a0e17] border-[#1e293b] py-0 gap-0">
            <CardHeader className="px-3 py-2">
              <CardTitle className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider flex items-center gap-1.5">
                <DollarSign className="h-3 w-3 text-[#d4af37]" />
                Capital Strategy
              </CardTitle>
            </CardHeader>
            <CardContent className="px-3 pb-2 pt-0 space-y-1.5">
              {data?.capitalStrategy ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-[9px] font-mono text-[#64748b]">Current Capital</span>
                    <span className="text-[11px] font-mono font-bold text-[#e2e8f0]">
                      {formatCurrency(data.capitalStrategy.totalCapital)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[9px] font-mono text-[#64748b]">Initial Capital</span>
                    <span className="text-[11px] font-mono text-[#94a3b8]">
                      {formatCurrency(data.capitalStrategy.initialCapital)}
                    </span>
                  </div>
                  <Separator className="bg-[#1e293b]" />
                  <div className="flex items-center justify-between">
                    <span className="text-[9px] font-mono text-[#64748b]">PnL</span>
                    <span
                      className={`text-[11px] font-mono font-bold ${
                        (data.capitalStrategy.currentPnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}
                    >
                      {(data.capitalStrategy.currentPnl ?? 0) >= 0 ? '+' : ''}
                      {formatCurrency(data.capitalStrategy.currentPnl)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[9px] font-mono text-[#64748b]">Growth</span>
                    <span
                      className={`text-[11px] font-mono font-bold ${
                        (data.capitalStrategy.growthPct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}
                    >
                      {(data.capitalStrategy.growthPct ?? 0) >= 0 ? '+' : ''}
                      {(data.capitalStrategy.growthPct ?? 0).toFixed(2)}%
                    </span>
                  </div>
                  {/* Mini growth bar */}
                  <div className="mt-1">
                    <div className="h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-700 ${
                          (data.capitalStrategy.growthPct ?? 0) >= 0 ? 'bg-emerald-500' : 'bg-red-500'
                        }`}
                        style={{
                          width: `${Math.min(Math.abs(data.capitalStrategy.growthPct ?? 0), 100)}%`,
                        }}
                      />
                    </div>
                  </div>
                </>
              ) : (
                <div className="flex items-center justify-center h-12 text-[10px] font-mono text-[#475569]">
                  No capital data
                </div>
              )}
            </CardContent>
          </Card>

          {/* Brain Cycle Status */}
          <Card className="bg-[#0a0e17] border-[#1e293b] py-0 gap-0">
            <CardHeader className="px-3 py-2">
              <CardTitle className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider flex items-center gap-1.5">
                <Brain className="h-3 w-3 text-[#d4af37]" />
                Brain Cycle
              </CardTitle>
            </CardHeader>
            <CardContent className="px-3 pb-2 pt-0 space-y-1.5">
              {data?.brainCycle ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-[9px] font-mono text-[#64748b]">Cycle Status</span>
                    <Badge
                      className={`text-[9px] font-mono font-bold border ${
                        data.brainCycle.status === 'idle'
                          ? 'bg-gray-500/10 text-gray-400 border-gray-500/30'
                          : data.brainCycle.status === 'running'
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                            : data.brainCycle.status === 'error'
                              ? 'bg-red-500/10 text-red-400 border-red-500/30'
                              : 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30'
                      }`}
                    >
                      {data.brainCycle.status?.toUpperCase() || 'UNKNOWN'}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[9px] font-mono text-[#64748b]">Cycles Completed</span>
                    <span className="text-[11px] font-mono font-bold text-cyan-400">
                      {data.brainCycle.cyclesCompleted ?? 0}
                    </span>
                  </div>
                  <Separator className="bg-[#1e293b]" />
                  {/* Last cycle result summary */}
                  {data.brainCycle.lastCycleResult ? (
                    <div className="bg-[#0d1117] rounded-md border border-[#1e293b] p-2 space-y-1">
                      <span className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">
                        Last Cycle Result
                      </span>
                      {typeof data.brainCycle.lastCycleResult === 'object' &&
                        Object.entries(data.brainCycle.lastCycleResult)
                          .filter(
                            ([k, v]) =>
                              v !== null &&
                              v !== undefined &&
                              typeof v !== 'object' &&
                              k !== 'id' &&
                              k !== 'createdAt' &&
                              k !== 'updatedAt'
                          )
                          .slice(0, 5)
                          .map(([key, value]) => (
                            <div key={key} className="flex items-center justify-between">
                              <span className="text-[9px] font-mono text-[#64748b]">
                                {key
                                  .replace(/([A-Z])/g, ' $1')
                                  .replace(/^./, (s) => s.toUpperCase())}
                              </span>
                              <span
                                className={`text-[10px] font-mono font-bold ${
                                  typeof value === 'number'
                                    ? 'text-[#d4af37]'
                                    : 'text-[#e2e8f0]'
                                }`}
                              >
                                {typeof value === 'number'
                                  ? value >= 1000
                                    ? value.toLocaleString()
                                    : value.toFixed ? value.toFixed(2) : String(value)
                                  : String(value)}
                              </span>
                            </div>
                          ))}
                    </div>
                  ) : (
                    <div className="flex items-center justify-center h-8 text-[9px] font-mono text-[#475569]">
                      No cycle results yet
                    </div>
                  )}
                </>
              ) : (
                <div className="flex items-center justify-center h-12 text-[10px] font-mono text-[#475569]">
                  No brain cycle data
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* ─── Section 6: Quick Actions ─── */}
        <Card className="bg-[#0a0e17] border-[#1e293b] py-0 gap-0">
          <CardHeader className="px-3 py-2">
            <CardTitle className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider flex items-center gap-1.5">
              <Zap className="h-3 w-3 text-[#d4af37]" />
              Quick Actions
            </CardTitle>
          </CardHeader>
          <CardContent className="px-3 pb-2 pt-0">
            <div className="flex items-center gap-2 flex-wrap">
              <Button
                size="sm"
                onClick={() => startAllMutation.mutate()}
                disabled={actionLoading !== null}
                className="h-8 px-4 text-[11px] font-mono font-bold bg-gradient-to-r from-emerald-600/30 to-cyan-600/30 text-emerald-300 border border-emerald-500/40 hover:from-emerald-600/40 hover:to-cyan-600/40 hover:text-emerald-200 disabled:opacity-40 shadow-lg shadow-emerald-500/10"
                variant="outline"
              >
                {actionLoading === 'startall' ? (
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                ) : (
                  <Zap className="h-3.5 w-3.5 mr-1.5" />
                )}
                Start All
              </Button>
              <Button
                size="sm"
                onClick={() => cycleMutation.mutate()}
                disabled={actionLoading !== null}
                className="h-7 px-3 text-[10px] font-mono bg-cyan-600/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-600/30 hover:text-cyan-300 disabled:opacity-40"
                variant="outline"
              >
                {actionLoading === 'cycle' ? (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                ) : (
                  <RotateCcw className="h-3 w-3 mr-1" />
                )}
                Run Cycle Now
              </Button>
              <Button
                size="sm"
                onClick={() => syncMutation.mutate()}
                disabled={actionLoading !== null}
                className="h-7 px-3 text-[10px] font-mono bg-violet-600/20 text-violet-400 border border-violet-500/30 hover:bg-violet-600/30 hover:text-violet-300 disabled:opacity-40"
                variant="outline"
              >
                {actionLoading === 'sync' ? (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                ) : (
                  <RefreshCw className="h-3 w-3 mr-1" />
                )}
                Force Sync
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  // Navigate to signals tab to show unvalidated signals
                  try {
                    import('@/store/crypto-store').then(({ useCryptoStore }) => {
                      const state = useCryptoStore.getState?.();
                      if (state?.setActiveTab) state.setActiveTab('signals');
                    }).catch(() => { /* store not available */ });
                  } catch { /* store not available */ }
                }}
                disabled={actionLoading !== null}
                className="h-7 px-3 text-[10px] font-mono bg-yellow-600/20 text-yellow-400 border border-yellow-500/30 hover:bg-yellow-600/30 hover:text-yellow-300"
                variant="outline"
              >
                <ShieldCheck className="h-3 w-3 mr-1" />
                Validate Signals
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* ─── Section 7: Config Panel (Collapsible) ─── */}
        <Collapsible open={configOpen} onOpenChange={setConfigOpen}>
          <Card className="bg-[#0a0e17] border-[#1e293b] py-0 gap-0">
            <CollapsibleTrigger asChild>
              <CardHeader className="px-3 py-2 cursor-pointer hover:bg-[#0d1117]/50 transition-colors rounded-t-xl">
                <CardTitle className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider flex items-center gap-1.5">
                  <Settings2 className="h-3 w-3 text-[#d4af37]" />
                  Configuration
                  {configOpen ? (
                    <ChevronDown className="h-3 w-3 text-[#475569] ml-auto" />
                  ) : (
                    <ChevronRight className="h-3 w-3 text-[#475569] ml-auto" />
                  )}
                </CardTitle>
              </CardHeader>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <CardContent className="px-3 pb-3 pt-0">
                {data?.config ? (
                  <div className="bg-[#0d1117] rounded-md border border-[#1e293b] p-3 space-y-2">
                    {/* Key config values */}
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                      <div className="flex flex-col gap-0.5">
                        <span className="text-[8px] font-mono text-[#475569] uppercase">
                          Capital (USD)
                        </span>
                        <span className="text-[11px] font-mono font-bold text-[#e2e8f0]">
                          {formatCurrency(data.config.capitalUsd)}
                        </span>
                      </div>
                      <div className="flex flex-col gap-0.5">
                        <span className="text-[8px] font-mono text-[#475569] uppercase">
                          Initial Capital
                        </span>
                        <span className="text-[11px] font-mono text-[#94a3b8]">
                          {formatCurrency(data.config.initialCapitalUsd)}
                        </span>
                      </div>
                      <div className="flex flex-col gap-0.5">
                        <span className="text-[8px] font-mono text-[#475569] uppercase">Chain</span>
                        <span className="text-[11px] font-mono text-[#e2e8f0]">
                          {data.config.chain?.toUpperCase() || '—'}
                        </span>
                      </div>
                      <div className="flex flex-col gap-0.5">
                        <span className="text-[8px] font-mono text-[#475569] uppercase">
                          Cycle Interval
                        </span>
                        <span className="text-[11px] font-mono text-[#e2e8f0]">
                          {formatInterval(data.config.cycleIntervalMs)}
                        </span>
                      </div>
                    </div>
                    <Separator className="bg-[#1e293b]" />
                    {/* Raw config JSON (read-only display) */}
                    <div className="max-h-32 overflow-y-auto">
                      <pre className="text-[9px] font-mono text-[#64748b] whitespace-pre-wrap break-all">
                        {JSON.stringify(
                          Object.fromEntries(
                            Object.entries(data.config).filter(
                              ([k]) =>
                                !['capitalUsd', 'initialCapitalUsd', 'chain', 'cycleIntervalMs'].includes(k)
                            )
                          ),
                          null,
                          2
                        )}
                      </pre>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-12 text-[10px] font-mono text-[#475569]">
                    No configuration loaded
                  </div>
                )}
              </CardContent>
            </CollapsibleContent>
          </Card>
        </Collapsible>

        {/* Auto-Sync Panel */}
        <AutoSyncPanel />
      </div>

      {/* ─── Footer status bar ─── */}
      <div className="shrink-0 flex items-center gap-3 px-3 py-1 border-t border-[#1e293b] bg-[#0a0e17]">
        <span className="text-[8px] font-mono text-[#475569]">
          Auto-refresh: 5s
        </span>
        <span className="text-[8px] font-mono text-[#475569]">•</span>
        <span className="text-[8px] font-mono text-[#475569]">
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
      </div>
    </div>
  );
}
