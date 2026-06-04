'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from '@/components/ui/tooltip';
import { motion, AnimatePresence } from 'framer-motion';
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Loader2,
  RefreshCw,
  TrendingUp,
  BarChart3,
  Activity,
} from 'lucide-react';
import { toast } from 'sonner';

// ============================================================
// TYPES
// ============================================================

interface DataQualityMetrics {
  totalTokens: number;
  tokensWithCandles: number;
  tokensWithEnoughCandles: number;
  totalCandles: number;
  candlesByTimeframe: Array<{ timeframe: string; count: number }>;
  candlesBySource: Array<{ source: string; count: number }>;
  zeroVolumeCandles: number;
  candlesWithVolume: number;
  oldestCandle: string | null;
  newestCandle: string | null;
  dnaRecords: number;
  coverage: {
    candleCoverage: number;
    backtestReadyCoverage: number;
    volumeCoverage: number;
    dnaCoverage: number;
  };
  qualityScore: number;
  qualityLevel: 'critical' | 'poor' | 'fair' | 'good' | 'excellent';
  recommendations: string[];
}

// ============================================================
// QUALITY LEVEL CONFIG
// ============================================================

const QUALITY_CONFIG: Record<string, { color: string; bgColor: string; borderColor: string; icon: React.ReactNode; label: string }> = {
  critical: {
    color: 'text-red-400',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    icon: <AlertTriangle className="h-4 w-4 text-red-400" />,
    label: 'CRITICAL',
  },
  poor: {
    color: 'text-orange-400',
    bgColor: 'bg-orange-500/10',
    borderColor: 'border-orange-500/30',
    icon: <AlertTriangle className="h-4 w-4 text-orange-400" />,
    label: 'POOR',
  },
  fair: {
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/30',
    icon: <Activity className="h-4 w-4 text-yellow-400" />,
    label: 'FAIR',
  },
  good: {
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-500/10',
    borderColor: 'border-emerald-500/30',
    icon: <TrendingUp className="h-4 w-4 text-emerald-400" />,
    label: 'GOOD',
  },
  excellent: {
    color: 'text-cyan-400',
    bgColor: 'bg-cyan-500/10',
    borderColor: 'border-cyan-500/30',
    icon: <CheckCircle2 className="h-4 w-4 text-cyan-400" />,
    label: 'EXCELLENT',
  },
};

// ============================================================
// MAIN COMPONENT
// ============================================================

export function DataQualityGate({ compact = false }: { compact?: boolean }) {
  const queryClient = useQueryClient();

  // Fetch data quality metrics
  const { data: qualityData, isLoading, refetch } = useQuery({
    queryKey: ['data-quality-gate'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/data-quality');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as DataQualityMetrics | null;
      } catch {
        return null;
      }
    },
    staleTime: 15000,
    refetchInterval: 60000,
  });

  // Backfill mutation
  const backfillMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/backfill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: 20, timeframes: ['4h', '1d'] }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Backfill failed');
      }
      return res.json();
    },
    onSuccess: () => {
      toast.success('OHLCV backfill started — candles will be available shortly');
      refetch();
      queryClient.invalidateQueries({ queryKey: ['data-quality-gate'] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Backfill failed');
    },
  });

  const q = qualityData;
  const level = q?.qualityLevel ?? 'critical';
  const config = QUALITY_CONFIG[level];

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-[#0d1117] border border-[#1e293b] rounded-lg">
        <Loader2 className="h-4 w-4 animate-spin text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b]">Checking data quality...</span>
      </div>
    );
  }

  if (!q) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-[#0d1117] border border-red-500/30 rounded-lg">
        <AlertTriangle className="h-4 w-4 text-red-400" />
        <span className="text-[10px] font-mono text-red-400">Data quality check failed</span>
        <Button
          variant="ghost"
          size="sm"
          className="h-5 text-[9px] font-mono text-[#94a3b8] hover:text-white px-1.5"
          onClick={() => refetch()}
        >
          <RefreshCw className="h-3 w-3 mr-1" />
          Retry
        </Button>
      </div>
    );
  }

  // Compact mode — inline status bar
  if (compact) {
    return (
      <div className={`flex items-center gap-2 px-3 py-1.5 ${config.bgColor} border ${config.borderColor} rounded-lg`}>
        {config.icon}
        <span className={`text-[10px] font-mono font-semibold ${config.color}`}>
          Data: {config.label}
        </span>
        <span className="text-[9px] font-mono text-[#64748b]">
          ({q.tokensWithEnoughCandles}/{q.totalTokens} tokens ready)
        </span>
        {level === 'critical' || level === 'poor' ? (
          <Button
            variant="ghost"
            size="sm"
            className="h-5 text-[9px] font-mono text-[#d4af37] hover:text-white px-1.5"
            onClick={() => backfillMutation.mutate()}
            disabled={backfillMutation.isPending}
          >
            {backfillMutation.isPending ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Database className="h-3 w-3 mr-1" />
            )}
            Backfill
          </Button>
        ) : null}
      </div>
    );
  }

  // Full mode — detailed panel
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={`bg-[#0d1117] border ${config.borderColor} rounded-lg overflow-hidden`}
    >
      {/* Header */}
      <div className={`flex items-center justify-between px-4 py-2.5 ${config.bgColor} border-b ${config.borderColor}`}>
        <div className="flex items-center gap-2">
          {config.icon}
          <span className="text-[11px] font-mono font-semibold text-[#e2e8f0]">
            Data Quality Gate
          </span>
          <Badge className={`text-[8px] h-4 px-1.5 font-mono ${config.bgColor} ${config.color} border ${config.borderColor}`}>
            {config.label}
          </Badge>
          <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#94a3b8] border-[#2d3748]">
            Score: {q.qualityScore}/100
          </Badge>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            className="h-5 text-[9px] font-mono text-[#94a3b8] hover:text-white px-1.5"
            onClick={() => refetch()}
          >
            <RefreshCw className="h-3 w-3" />
          </Button>
          {(level === 'critical' || level === 'poor') && (
            <Button
              variant="ghost"
              size="sm"
              className={`h-5 text-[9px] font-mono ${config.color} hover:text-white px-1.5`}
              onClick={() => backfillMutation.mutate()}
              disabled={backfillMutation.isPending}
            >
              {backfillMutation.isPending ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <Database className="h-3 w-3 mr-1" />
              )}
              Backfill Data
            </Button>
          )}
        </div>
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-[#1e293b]">
        {/* Backtest-Ready Tokens */}
        <div className="bg-[#0d1117] p-3 text-center">
          <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">Backtest Ready</div>
          <div className="text-lg font-mono font-bold text-[#e2e8f0]">
            {q.tokensWithEnoughCandles}
            <span className="text-[10px] text-[#475569]">/{q.totalTokens}</span>
          </div>
          <div className="mt-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
            <motion.div
              className="h-full rounded-full"
              initial={{ width: 0 }}
              animate={{ width: `${q.coverage.backtestReadyCoverage}%` }}
              transition={{ duration: 1, ease: 'easeOut' }}
              style={{
                backgroundColor: q.coverage.backtestReadyCoverage >= 50 ? '#10b981' :
                  q.coverage.backtestReadyCoverage >= 25 ? '#f59e0b' : '#ef4444',
              }}
            />
          </div>
          <div className="text-[8px] font-mono text-[#475569] mt-0.5">
            {q.coverage.backtestReadyCoverage}% coverage
          </div>
        </div>

        {/* Total Candles */}
        <div className="bg-[#0d1117] p-3 text-center">
          <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">OHLCV Candles</div>
          <div className="text-lg font-mono font-bold text-[#e2e8f0]">
            {q.totalCandles.toLocaleString()}
          </div>
          <div className="flex items-center justify-center gap-1 mt-1">
            <BarChart3 className="h-2.5 w-2.5 text-[#475569]" />
            <span className="text-[8px] font-mono text-[#475569]">
              {q.candlesByTimeframe.map((t) => `${t.timeframe}: ${t.count}`).join(' · ')}
            </span>
          </div>
        </div>

        {/* Volume Data */}
        <div className="bg-[#0d1117] p-3 text-center">
          <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">Volume Data</div>
          <div className="text-lg font-mono font-bold text-[#e2e8f0]">
            {q.candlesWithVolume.toLocaleString()}
            <span className="text-[10px] text-[#475569]">/{q.totalCandles.toLocaleString()}</span>
          </div>
          <div className="mt-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
            <motion.div
              className="h-full rounded-full"
              initial={{ width: 0 }}
              animate={{ width: `${q.coverage.volumeCoverage}%` }}
              transition={{ duration: 1, ease: 'easeOut' }}
              style={{
                backgroundColor: q.coverage.volumeCoverage >= 50 ? '#10b981' :
                  q.coverage.volumeCoverage >= 25 ? '#f59e0b' : '#ef4444',
              }}
            />
          </div>
          <div className="text-[8px] font-mono text-[#475569] mt-0.5">
            {q.coverage.volumeCoverage}% with volume
          </div>
        </div>

        {/* DNA Coverage */}
        <div className="bg-[#0d1117] p-3 text-center">
          <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">DNA Profiles</div>
          <div className="text-lg font-mono font-bold text-[#e2e8f0]">
            {q.dnaRecords}
            <span className="text-[10px] text-[#475569]">/{q.totalTokens}</span>
          </div>
          <div className="mt-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
            <motion.div
              className="h-full rounded-full"
              initial={{ width: 0 }}
              animate={{ width: `${q.coverage.dnaCoverage}%` }}
              transition={{ duration: 1, ease: 'easeOut' }}
              style={{
                backgroundColor: q.coverage.dnaCoverage >= 50 ? '#10b981' :
                  q.coverage.dnaCoverage >= 25 ? '#f59e0b' : '#ef4444',
              }}
            />
          </div>
          <div className="text-[8px] font-mono text-[#475569] mt-0.5">
            {q.coverage.dnaCoverage}% coverage
          </div>
        </div>
      </div>

      {/* Recommendations */}
      <AnimatePresence>
        {q.recommendations.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="px-4 py-2.5 border-t border-[#1e293b]">
              <div className="text-[9px] font-mono text-[#64748b] uppercase mb-1.5">Recommendations</div>
              {q.recommendations.map((rec, i) => (
                <div key={i} className="flex items-start gap-1.5 mb-1 last:mb-0">
                  <span className="text-[#d4af37] text-[9px] mt-0.5">•</span>
                  <span className="text-[9px] font-mono text-[#94a3b8]">{rec}</span>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
