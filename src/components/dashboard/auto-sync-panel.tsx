'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { motion } from 'framer-motion';
import {
  RefreshCw,
  Play,
  Square,
  Loader2,
  Database,
  Users,
  BarChart3,
  Brain,
  Activity,
  Zap,
  TrendingUp,
} from 'lucide-react';
import { toast } from 'sonner';

// ============================================================
// TYPES
// ============================================================

interface AutoSyncStatus {
  isRunning: boolean;
  currentCycle: number;
  lastCycleAt: string | null;
  nextCycleAt: string | null;
  lastResult: {
    tokensRefreshed: number;
    tradersDiscovered: number;
    candlesFetched: number;
    dnaComputed: number;
    patternsDetected: number;
    signalsGenerated: number;
    paperPositionsSynced: number;
    errors: string[];
  } | null;
  isCycleRunning: boolean;
  syncIntervalMs: number;
  apiKeysConfigured: {
    etherscan: boolean;
  };
}

// ============================================================
// COMPONENT
// ============================================================

export function AutoSyncPanel() {
  const queryClient = useQueryClient();

  // Fetch auto-sync status
  const { data: syncStatus, isLoading } = useQuery({
    queryKey: ['auto-sync-status'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/auto-sync', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'status' }),
        });
        if (!res.ok) return null;
        return (await res.json()) as AutoSyncStatus | null;
      } catch {
        return null;
      }
    },
    staleTime: 10000,
    refetchInterval: 30000,
  });

  // Start mutation
  const startMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/auto-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start' }),
      });
      if (!res.ok) throw new Error('Failed to start auto-sync');
      return res.json();
    },
    onSuccess: () => {
      toast.success('Auto-Sync started — data will refresh every 15 minutes');
      queryClient.invalidateQueries({ queryKey: ['auto-sync-status'] });
    },
    onError: () => {
      toast.error('Failed to start auto-sync');
    },
  });

  // Stop mutation
  const stopMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/auto-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'stop' }),
      });
      if (!res.ok) throw new Error('Failed to stop auto-sync');
      return res.json();
    },
    onSuccess: () => {
      toast.success('Auto-Sync stopped');
      queryClient.invalidateQueries({ queryKey: ['auto-sync-status'] });
    },
    onError: () => {
      toast.error('Failed to stop auto-sync');
    },
  });

  // Manual run mutation
  const runOnceMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/auto-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'run' }),
      });
      if (!res.ok) throw new Error('Failed to trigger sync');
      return res.json();
    },
    onSuccess: (data) => {
      if (data.triggered) {
        toast.success('Manual sync cycle started');
      } else {
        toast.info(data.message || 'Sync already running');
      }
      queryClient.invalidateQueries({ queryKey: ['auto-sync-status'] });
    },
    onError: () => {
      toast.error('Failed to trigger sync');
    },
  });

  const isRunning = syncStatus?.isRunning ?? false;
  const isCycleRunning = syncStatus?.isCycleRunning ?? false;
  const lastResult = syncStatus?.lastResult;

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-[#0d1117] border border-[#1e293b] rounded-lg">
        <Loader2 className="h-4 w-4 animate-spin text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b]">Checking sync status...</span>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-[#0a0e17] border-b border-[#1e293b]">
        <div className="flex items-center gap-2">
          <RefreshCw className={`h-3.5 w-3.5 ${isRunning ? 'text-emerald-400 animate-spin' : 'text-[#64748b]'}`} style={{ animationDuration: '3s' }} />
          <span className="text-[11px] font-mono font-semibold text-[#e2e8f0]">
            Auto-Sync
          </span>
          <Badge className={`text-[8px] h-4 px-1.5 font-mono ${
            isRunning
              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
              : 'bg-[#1a1f2e] text-[#64748b] border-[#2d3748]'
          }`}>
            {isRunning ? 'ACTIVE' : 'IDLE'}
          </Badge>
          {syncStatus?.currentCycle ? (
            <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#94a3b8] border-[#2d3748]">
              Cycle {syncStatus.currentCycle}
            </Badge>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            className="h-5 text-[9px] font-mono text-[#94a3b8] hover:text-white px-1.5"
            onClick={() => runOnceMutation.mutate()}
            disabled={isCycleRunning || runOnceMutation.isPending}
          >
            {isCycleRunning ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Zap className="h-3 w-3 mr-1" />
            )}
            Run Once
          </Button>
          {isRunning ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-5 text-[9px] font-mono text-red-400 hover:text-white px-1.5"
              onClick={() => stopMutation.mutate()}
              disabled={stopMutation.isPending}
            >
              <Square className="h-3 w-3 mr-1" />
              Stop
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="h-5 text-[9px] font-mono text-emerald-400 hover:text-white px-1.5"
              onClick={() => startMutation.mutate()}
              disabled={startMutation.isPending}
            >
              <Play className="h-3 w-3 mr-1" />
              Start
            </Button>
          )}
        </div>
      </div>

      {/* Last Cycle Results */}
      {lastResult && (
        <div className="grid grid-cols-3 md:grid-cols-7 gap-px bg-[#1e293b]">
          <MetricCell
            icon={<Database className="h-2.5 w-2.5" />}
            label="Tokens"
            value={lastResult.tokensRefreshed}
            color={lastResult.tokensRefreshed > 0 ? '#10b981' : '#475569'}
          />
          <MetricCell
            icon={<Users className="h-2.5 w-2.5" />}
            label="Traders"
            value={lastResult.tradersDiscovered}
            color={lastResult.tradersDiscovered > 0 ? '#10b981' : '#475569'}
          />
          <MetricCell
            icon={<BarChart3 className="h-2.5 w-2.5" />}
            label="Candles"
            value={lastResult.candlesFetched}
            color={lastResult.candlesFetched > 0 ? '#10b981' : '#475569'}
          />
          <MetricCell
            icon={<Brain className="h-2.5 w-2.5" />}
            label="DNA"
            value={lastResult.dnaComputed}
            color={lastResult.dnaComputed > 0 ? '#10b981' : '#475569'}
          />
          <MetricCell
            icon={<Activity className="h-2.5 w-2.5" />}
            label="Patterns"
            value={lastResult.patternsDetected}
            color={lastResult.patternsDetected > 0 ? '#10b981' : '#475569'}
          />
          <MetricCell
            icon={<Zap className="h-2.5 w-2.5" />}
            label="Signals"
            value={lastResult.signalsGenerated}
            color={lastResult.signalsGenerated > 0 ? '#10b981' : '#475569'}
          />
          <MetricCell
            icon={<TrendingUp className="h-2.5 w-2.5" />}
            label="Paper"
            value={lastResult.paperPositionsSynced || 0}
            color={(lastResult.paperPositionsSynced || 0) > 0 ? '#3b82f6' : '#475569'}
          />
        </div>
      )}

      {/* Info */}
      <div className="px-4 py-2 border-t border-[#1e293b]">
        <div className="flex items-center gap-3 text-[9px] font-mono text-[#475569]">
          <span>Interval: {syncStatus?.syncIntervalMs ? `${syncStatus.syncIntervalMs / 60000}min` : '15min'}</span>
          {syncStatus?.lastCycleAt && (
            <span>Last: {new Date(syncStatus.lastCycleAt).toLocaleTimeString()}</span>
          )}
          {syncStatus?.nextCycleAt && isRunning && (
            <span>Next: {new Date(syncStatus.nextCycleAt).toLocaleTimeString()}</span>
          )}
          <span>Etherscan: {syncStatus?.apiKeysConfigured?.etherscan ? '✓' : '✗'}</span>
          {lastResult && lastResult.errors.length > 0 && (
            <span className="text-orange-400">{lastResult.errors.length} error(s)</span>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ============================================================
// SUB-COMPONENTS
// ============================================================

function MetricCell({
  icon,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="bg-[#0d1117] p-2 text-center">
      <div className="flex items-center justify-center gap-1 mb-0.5">
        <span style={{ color }}>{icon}</span>
        <span className="text-[7px] font-mono text-[#64748b] uppercase">{label}</span>
      </div>
      <div className="text-sm font-mono font-bold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}
