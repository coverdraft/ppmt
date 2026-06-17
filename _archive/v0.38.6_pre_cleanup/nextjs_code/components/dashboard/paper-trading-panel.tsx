'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Play,
  Square,
  Pause,
  RefreshCw,
  Radio,
  ArrowUp,
  ArrowDown,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Activity,
  Target,
  Clock,
  BarChart3,
  Zap,
  ChevronDown,
  ChevronUp,
  X,
  Settings,
  Trophy,
  ArrowUpRight,
  ArrowDownRight,
  Loader2,
  Eye,
  Wallet,
} from 'lucide-react';
import { toast } from 'sonner';
import { useState, useMemo } from 'react';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';

// ============================================================
// TYPES
// ============================================================

interface PaperTradingStats {
  status: 'STOPPED' | 'RUNNING' | 'PAUSED';
  startedAt: string | null;
  uptimeMs: number;
  currentCapital: number;
  initialCapital: number;
  totalReturnPct: number;
  openPositions: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
  avgPnlPct: number;
  unrealizedPnl: number;
  maxDrawdownPct: number;
  sharpeRatio: number;
  lastScanAt: string | null;
  lastPriceSyncAt: string | null;
  tokensScanned: number;
  signalsGenerated: number;
}

interface PaperPosition {
  id: string;
  tokenAddress: string;
  symbol: string;
  chain: string;
  direction: 'LONG' | 'SHORT';
  entryTime: string;
  entryPrice: number;
  currentPrice: number;
  positionSizeUsd: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
  highWaterMark: number;
  systemName: string;
  exitConditions: string[];
}

interface PaperTradeRecord {
  id: string;
  tokenAddress: string;
  symbol: string;
  chain: string;
  direction: 'LONG' | 'SHORT';
  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number;
  positionSizeUsd: number;
  pnl: number;
  pnlPct: number;
  holdTimeMin: number;
  mfe: number;
  mae: number;
  exitReason: string;
  systemName: string;
}

interface PaperTradingConfig {
  initialCapital: number;
  chain: string;
  systemName: string;
  scanIntervalMs: number;
  maxOpenPositions: number;
  feesPct: number;
  slippagePct: number;
  minOperabilityScore: number;
  autoFeedback: boolean;
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

function formatTime(ms: number): string {
  if (ms < 60000) return `${Math.floor(ms / 1000)}s`;
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return `${h}h ${m}m`;
}

function timeAgo(date: string | null): string {
  if (!date) return 'Never';
  const diff = Date.now() - new Date(date).getTime();
  if (diff < 60000) return 'Just now';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function exitReasonLabel(reason: string): string {
  if (reason.includes('take_profit')) return 'Take Profit';
  if (reason.includes('stop_loss')) return 'Stop Loss';
  if (reason.includes('trailing_stop')) return 'Trailing Stop';
  if (reason.includes('time_expired')) return 'Time Exit';
  if (reason.includes('brain_signal')) return 'Brain Signal';
  if (reason.includes('ENGINE_STOPPED')) return 'Engine Stop';
  if (reason.includes('manual_close')) return 'Manual Close';
  return reason;
}

function exitReasonColor(reason: string): string {
  if (reason.includes('take_profit') || reason.includes('trailing_stop')) return 'text-emerald-400';
  if (reason.includes('stop_loss')) return 'text-red-400';
  if (reason.includes('time_expired')) return 'text-amber-400';
  if (reason.includes('brain_signal')) return 'text-cyan-400';
  return 'text-[#94a3b8]';
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function PaperTradingPanel() {
  const queryClient = useQueryClient();
  const [showConfig, setShowConfig] = useState(false);
  const [showClosedTrades, setShowClosedTrades] = useState(true);
  const [config, setConfig] = useState<PaperTradingConfig>({
    initialCapital: 10,
    chain: 'SOL',
    systemName: 'Smart Entry Mirror',
    scanIntervalMs: 60000,
    maxOpenPositions: 3,
    feesPct: 0.003,
    slippagePct: 0.5,
    minOperabilityScore: 50,
    autoFeedback: true,
  });

  // Fetch paper trading status
  const { data: paperData, isLoading, isError: isStatusError } = useQuery({
    queryKey: ['paper-trading-status'],
    queryFn: async () => {
      const res = await fetch('/api/paper-trading');
      if (!res.ok) throw new Error(`Status fetch failed (${res.status})`);
      const json = await res.json();
      return json.data as {
        stats: PaperTradingStats;
        openPositions: PaperPosition[];
        recentTrades: PaperTradeRecord[];
      } | null;
    },
    staleTime: 10000,
    refetchInterval: 15000,
    retry: 2,
  });

  // Fetch full trade history
  const { data: tradesData, isError: isTradesError } = useQuery({
    queryKey: ['paper-trading-trades'],
    queryFn: async () => {
      const res = await fetch('/api/paper-trading/trades');
      if (!res.ok) throw new Error(`Trades fetch failed (${res.status})`);
      const json = await res.json();
      return json as { data: PaperTradeRecord[]; total: number } | null;
    },
    staleTime: 10000,
    refetchInterval: 10000,
    retry: 2,
  });

  // Fetch open positions
  const { data: positionsData, isError: isPositionsError } = useQuery({
    queryKey: ['paper-trading-positions'],
    queryFn: async () => {
      const res = await fetch('/api/paper-trading/positions');
      if (!res.ok) throw new Error(`Positions fetch failed (${res.status})`);
      const json = await res.json();
      return json as { data: PaperPosition[] } | null;
    },
    staleTime: 10000,
    refetchInterval: 10000,
    retry: 2,
  });

  const stats = paperData?.stats;
  const positions = positionsData?.data ?? paperData?.openPositions ?? [];
  const trades = tradesData?.data ?? paperData?.recentTrades ?? [];
  const isRunning = stats?.status === 'RUNNING';
  const isPaused = stats?.status === 'PAUSED';
  const isStopped = !stats || stats?.status === 'STOPPED';

  // Build equity curve data from trades
  const equityCurveData = useMemo(() => {
    if (!stats || !trades.length) return [];
    let capital = stats.initialCapital;
    const data: Array<{ trade: number; capital: number; pnl: number; drawdown: number }> = [];
    let peak = stats.initialCapital;

    // Sort trades by exit time
    const sortedTrades = [...trades].sort(
      (a, b) => new Date(a.exitTime).getTime() - new Date(b.exitTime).getTime()
    );

    data.push({ trade: 0, capital, pnl: 0, drawdown: 0 });

    sortedTrades.forEach((t, i) => {
      capital += t.pnl;
      if (capital > peak) peak = capital;
      const drawdown = peak > 0 ? ((peak - capital) / peak) * 100 : 0;
      data.push({
        trade: i + 1,
        capital: Math.round(capital * 100) / 100,
        pnl: Math.round(t.pnlPct * 100) / 100,
        drawdown: Math.round(drawdown * 100) / 100,
      });
    });

    return data;
  }, [stats, trades]);

  // Rolling win rate data
  const winRateData = useMemo(() => {
    if (!trades.length) return [];
    const sorted = [...trades].sort(
      (a, b) => new Date(a.exitTime).getTime() - new Date(b.exitTime).getTime()
    );
    const windowSize = 10;
    const data: Array<{ trade: number; winRate: number; avgPnl: number }> = [];

    sorted.forEach((t, i) => {
      const start = Math.max(0, i - windowSize + 1);
      const window = sorted.slice(start, i + 1);
      const wins = window.filter(w => w.pnl > 0).length;
      const avgPnl = window.reduce((s, w) => s + w.pnlPct, 0) / window.length;
      data.push({
        trade: i + 1,
        winRate: Math.round((wins / window.length) * 10000) / 100,
        avgPnl: Math.round(avgPnl * 100) / 100,
      });
    });

    return data;
  }, [trades]);

  // ---- MUTATIONS ----

  const startMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/paper-trading', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start', config }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || 'Failed to start');
      }
      return res.json();
    },
    onSuccess: (data) => {
      toast.success(data.data?.message || 'Paper trading started');
      queryClient.invalidateQueries({ queryKey: ['paper-trading-status'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-positions'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-trades'] });
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to start paper trading');
    },
  });

  const stopMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/paper-trading', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'stop' }),
      });
      if (!res.ok) throw new Error('Failed to stop');
      return res.json();
    },
    onSuccess: (data) => {
      toast.success(data.data?.message || 'Paper trading stopped');
      queryClient.invalidateQueries({ queryKey: ['paper-trading-status'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-positions'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-trades'] });
    },
    onError: () => {
      toast.error('Failed to stop paper trading');
    },
  });

  const pauseMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/paper-trading', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'pause' }),
      });
      if (!res.ok) throw new Error('Failed to pause');
      return res.json();
    },
    onSuccess: () => {
      toast.info('Paper trading paused');
      queryClient.invalidateQueries({ queryKey: ['paper-trading-status'] });
    },
  });

  const resumeMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/paper-trading', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'resume' }),
      });
      if (!res.ok) throw new Error('Failed to resume');
      return res.json();
    },
    onSuccess: () => {
      toast.success('Paper trading resumed');
      queryClient.invalidateQueries({ queryKey: ['paper-trading-status'] });
    },
  });

  const scanMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/paper-trading', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'scan' }),
      });
      if (!res.ok) throw new Error('Failed to run scan');
      return res.json();
    },
    onSuccess: (data) => {
      const result = data.data;
      toast.success(
        `Scan complete: ${result?.tokensScanned ?? 0} tokens, ${result?.signalsGenerated ?? 0} signals, ${result?.tradesOpened ?? 0} opened`
      );
      queryClient.invalidateQueries({ queryKey: ['paper-trading-status'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-positions'] });
    },
  });

  const forceCloseMutation = useMutation({
    mutationFn: async (positionId: string) => {
      const res = await fetch('/api/paper-trading/positions', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ positionId, reason: 'manual_close' }),
      });
      if (!res.ok) throw new Error('Failed to close position');
      return res.json();
    },
    onSuccess: (data) => {
      const d = data.data;
      toast.success(
        `Closed ${d?.symbol}: ${formatCurrency(d?.pnl ?? 0)} (${formatPct(d?.pnlPct ?? 0)})`
      );
      queryClient.invalidateQueries({ queryKey: ['paper-trading-status'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-positions'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-trades'] });
    },
    onError: () => {
      toast.error('Failed to close position');
    },
  });

  // Sync Prices mutation - Sincronizar precios en vivo desde DexScreener
  const syncPricesMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/paper-trading', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'sync_prices' }),
      });
      if (!res.ok) throw new Error('Failed to sync prices');
      return res.json();
    },
    onSuccess: (data) => {
      const result = data.data;
      toast.success(
        `Prices synced: ${result?.updated ?? 0} updated, ${result?.errors ?? 0} errors`
      );
      queryClient.invalidateQueries({ queryKey: ['paper-trading-status'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-positions'] });
    },
    onError: () => {
      toast.error('Failed to sync prices');
    },
  });

  // Open Position mutation - manually open a paper trading position
  const openPositionMutation = useMutation({
    mutationFn: async ({ tokenAddress, tokenSymbol, chain, direction }: {
      tokenAddress: string; tokenSymbol: string; chain: string; direction: 'LONG' | 'SHORT';
    }) => {
      const res = await fetch('/api/paper-trading', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'activate_strategy',
          tokenAddress,
          tokenSymbol,
          chain,
          strategyName: config.systemName,
          direction,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || 'Failed to open position');
      }
      return res.json();
    },
    onSuccess: (data) => {
      const d = data.data;
      toast.success(`Position opened: ${d?.symbol || 'Token'} ${d?.direction || ''}`);
      queryClient.invalidateQueries({ queryKey: ['paper-trading-status'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-positions'] });
      queryClient.invalidateQueries({ queryKey: ['paper-trading-trades'] });
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to open position');
    },
  });

  // Open position form state
  const [openPosForm, setOpenPosForm] = useState({ tokenAddress: '', symbol: '', chain: 'SOL', direction: 'LONG' as 'LONG' | 'SHORT' });
  const [showOpenPosForm, setShowOpenPosForm] = useState(false);

  // ---- LOADING STATE ----

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0d1117]">
        <Loader2 className="h-8 w-8 animate-spin text-[#d4af37] mb-3" />
        <span className="text-sm font-mono text-[#64748b]">Loading paper trading...</span>
      </div>
    );
  }

  const pnlColor = (stats?.totalReturnPct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400';
  const unrealizedColor = (stats?.unrealizedPnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400';

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#d4af37]/10 border border-[#d4af37]/20">
            <Wallet className="h-3.5 w-3.5 text-[#d4af37]" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Paper Trading</span>
              <Badge className={`text-[8px] h-4 px-1.5 font-mono ${
                isRunning
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                  : isPaused
                  ? 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                  : 'bg-[#1a1f2e] text-[#64748b] border-[#2d3748]'
              }`}>
                {isRunning ? (
                  <span className="flex items-center gap-1">
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
                    </span>
                    LIVE
                  </span>
                ) : isPaused ? 'PAUSED' : 'IDLE'}
              </Badge>
            </div>
            <span className="text-[9px] font-mono text-[#475569]">
              {stats?.startedAt ? `Started ${timeAgo(stats.startedAt)}` : 'Not started'}
              {stats?.uptimeMs ? ` · Uptime ${formatTime(stats.uptimeMs)}` : ''}
              {stats?.lastPriceSyncAt && isRunning ? ` · Prices ${timeAgo(stats.lastPriceSyncAt)}` : ''}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Config Toggle */}
          <Button
            variant="ghost"
            size="sm"
            className={`h-6 text-[9px] font-mono px-2 ${showConfig ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-[#94a3b8]'}`}
            onClick={() => setShowConfig(!showConfig)}
          >
            <Settings className="h-3 w-3 mr-1" />
            Config
          </Button>

          {/* Sync Prices - Sincronizar precios en vivo */}
          {isRunning && positions.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-[9px] font-mono text-emerald-400 px-2"
              onClick={() => syncPricesMutation.mutate()}
              disabled={syncPricesMutation.isPending}
            >
              {syncPricesMutation.isPending ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <Radio className="h-3 w-3 mr-1" />
              )}
              Sync
            </Button>
          )}

          {/* Single Scan */}
          {isRunning && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-[9px] font-mono text-cyan-400 px-2"
              onClick={() => scanMutation.mutate()}
              disabled={scanMutation.isPending}
            >
              {scanMutation.isPending ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <Zap className="h-3 w-3 mr-1" />
              )}
              Scan
            </Button>
          )}

          {/* Pause/Resume */}
          {(isRunning || isPaused) && (
            <Button
              variant="ghost"
              size="sm"
              className={`h-6 text-[9px] font-mono px-2 ${isPaused ? 'text-emerald-400' : 'text-amber-400'}`}
              onClick={() => isPaused ? resumeMutation.mutate() : pauseMutation.mutate()}
              disabled={pauseMutation.isPending || resumeMutation.isPending}
            >
              {isPaused ? (
                <><Play className="h-3 w-3 mr-1" />Resume</>
              ) : (
                <><Pause className="h-3 w-3 mr-1" />Pause</>
              )}
            </Button>
          )}

          {/* Open Position */}
          {isRunning && (
            <Button
              variant="ghost"
              size="sm"
              className={`h-6 text-[9px] font-mono px-2 ${showOpenPosForm ? 'text-[#d4af37] bg-[#d4af37]/10' : 'text-emerald-400'}`}
              onClick={() => setShowOpenPosForm(!showOpenPosForm)}
            >
              <TrendingUp className="h-3 w-3 mr-1" />
              Open
            </Button>
          )}

          {/* Start / Stop */}
          {isStopped ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-[9px] font-mono text-emerald-400 px-2 border border-emerald-500/30 bg-emerald-500/5 hover:bg-emerald-500/10"
              onClick={() => startMutation.mutate()}
              disabled={startMutation.isPending}
            >
              {startMutation.isPending ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <Play className="h-3 w-3 mr-1" />
              )}
              Start
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-[9px] font-mono text-red-400 px-2"
              onClick={() => stopMutation.mutate()}
              disabled={stopMutation.isPending}
            >
              {stopMutation.isPending ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <Square className="h-3 w-3 mr-1" />
              )}
              Stop
            </Button>
          )}
        </div>
      </div>

      {/* ===== OPEN POSITION FORM (collapsible) ===== */}
      <AnimatePresence>
        {showOpenPosForm && isRunning && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-b border-[#1e293b]"
          >
            <div className="px-4 py-3 bg-[#0d1117] flex items-center gap-3 flex-wrap">
              <div className="flex flex-col gap-0.5">
                <span className="text-[8px] font-mono text-[#475569] uppercase">Token Address</span>
                <Input
                  value={openPosForm.tokenAddress}
                  onChange={(e) => setOpenPosForm({ ...openPosForm, tokenAddress: e.target.value })}
                  placeholder="e.g. So11111111111111111111111111111111111111112"
                  className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] w-64"
                />
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[8px] font-mono text-[#475569] uppercase">Symbol</span>
                <Input
                  value={openPosForm.symbol}
                  onChange={(e) => setOpenPosForm({ ...openPosForm, symbol: e.target.value })}
                  placeholder="e.g. SOL"
                  className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] w-24"
                />
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[8px] font-mono text-[#475569] uppercase">Chain</span>
                <Select value={openPosForm.chain} onValueChange={(v) => setOpenPosForm({ ...openPosForm, chain: v })}>
                  <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] w-20">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#111827] border-[#1e293b]">
                    <SelectItem value="SOL" className="text-[10px] font-mono">SOL</SelectItem>
                    <SelectItem value="ETH" className="text-[10px] font-mono">ETH</SelectItem>
                    <SelectItem value="BASE" className="text-[10px] font-mono">BASE</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[8px] font-mono text-[#475569] uppercase">Direction</span>
                <Select value={openPosForm.direction} onValueChange={(v) => setOpenPosForm({ ...openPosForm, direction: v as 'LONG' | 'SHORT' })}>
                  <SelectTrigger className="h-7 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] w-24">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#111827] border-[#1e293b]">
                    <SelectItem value="LONG" className="text-[10px] font-mono text-emerald-400">LONG</SelectItem>
                    <SelectItem value="SHORT" className="text-[10px] font-mono text-red-400">SHORT</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button
                size="sm"
                onClick={() => {
                  if (!openPosForm.tokenAddress.trim() || !openPosForm.symbol.trim()) {
                    toast.error('Token address and symbol are required');
                    return;
                  }
                  openPositionMutation.mutate({
                    tokenAddress: openPosForm.tokenAddress.trim(),
                    tokenSymbol: openPosForm.symbol.trim().toUpperCase(),
                    chain: openPosForm.chain,
                    direction: openPosForm.direction,
                  });
                }}
                disabled={openPositionMutation.isPending || !openPosForm.tokenAddress.trim() || !openPosForm.symbol.trim()}
                className="h-7 px-3 text-[10px] font-mono bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-600/30 hover:text-emerald-300 disabled:opacity-40"
                variant="outline"
              >
                {openPositionMutation.isPending ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <ArrowUpRight className="h-3 w-3 mr-1" />}
                Open Position
              </Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ===== CONFIG PANEL (collapsible) ===== */}
      <AnimatePresence>
        {showConfig && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-b border-[#1e293b]"
          >
            <div className="px-4 py-3 bg-[#0d1117] grid grid-cols-3 gap-3">
              <ConfigField
                label="Initial Capital ($)"
                value={config.initialCapital}
                type="number"
                onChange={(v) => setConfig({ ...config, initialCapital: Number(v) })}
                disabled={!isStopped}
              />
              <ConfigField
                label="Chain"
                value={config.chain}
                type="select"
                options={['SOL', 'ETH', 'BASE', 'ARB']}
                onChange={(v) => setConfig({ ...config, chain: v })}
                disabled={!isStopped}
              />
              <ConfigField
                label="Max Positions"
                value={config.maxOpenPositions}
                type="number"
                onChange={(v) => setConfig({ ...config, maxOpenPositions: Number(v) })}
                disabled={!isStopped}
              />
              <ConfigField
                label="Scan Interval (sec)"
                value={config.scanIntervalMs / 1000}
                type="number"
                onChange={(v) => setConfig({ ...config, scanIntervalMs: Number(v) * 1000 })}
                disabled={!isStopped}
              />
              <ConfigField
                label="Fee %"
                value={config.feesPct * 100}
                type="number"
                onChange={(v) => setConfig({ ...config, feesPct: Number(v) / 100 })}
                disabled={!isStopped}
              />
              <ConfigField
                label="Slippage %"
                value={config.slippagePct}
                type="number"
                onChange={(v) => setConfig({ ...config, slippagePct: Number(v) })}
                disabled={!isStopped}
              />
              <ConfigField
                label="Min Operability"
                value={config.minOperabilityScore}
                type="number"
                onChange={(v) => setConfig({ ...config, minOperabilityScore: Number(v) })}
                disabled={!isStopped}
              />
              <ConfigField
                label="Auto Feedback"
                value={config.autoFeedback ? 'true' : 'false'}
                type="select"
                options={['true', 'false']}
                onChange={(v) => setConfig({ ...config, autoFeedback: v === 'true' })}
                disabled={!isStopped}
              />
              <ConfigField
                label="System"
                value={config.systemName}
                type="select"
                options={['Smart Entry Mirror', 'Momentum Rider', 'Scalper Pro', 'Swing Trader', 'Deep Value']}
                onChange={(v) => setConfig({ ...config, systemName: v })}
                disabled={!isStopped}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ===== MAIN CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* ---- KEY METRICS ROW ---- */}
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-px bg-[#1e293b] shrink-0">
          <MetricCell
            icon={<DollarSign className="h-2.5 w-2.5" />}
            label="Capital"
            value={formatCurrency(stats?.currentCapital ?? 0)}
            color={pnlColor}
          />
          <MetricCell
            icon={<TrendingUp className="h-2.5 w-2.5" />}
            label="Return"
            value={formatPct(stats?.totalReturnPct ?? 0)}
            color={pnlColor}
          />
          <MetricCell
            icon={<Eye className="h-2.5 w-2.5" />}
            label="Unrealized"
            value={formatCurrency(stats?.unrealizedPnl ?? 0)}
            color={unrealizedColor}
          />
          <MetricCell
            icon={<Target className="h-2.5 w-2.5" />}
            label="Win Rate"
            value={`${((stats?.winRate ?? 0) * 100).toFixed(1)}%`}
            color={(stats?.winRate ?? 0) >= 0.5 ? 'text-emerald-400' : 'text-red-400'}
          />
          <MetricCell
            icon={<BarChart3 className="h-2.5 w-2.5" />}
            label="Sharpe"
            value={(stats?.sharpeRatio ?? 0).toFixed(2)}
            color={(stats?.sharpeRatio ?? 0) >= 1 ? 'text-emerald-400' : (stats?.sharpeRatio ?? 0) >= 0 ? 'text-amber-400' : 'text-red-400'}
          />
          <MetricCell
            icon={<TrendingDown className="h-2.5 w-2.5" />}
            label="Max DD"
            value={`${(stats?.maxDrawdownPct ?? 0).toFixed(1)}%`}
            color={(stats?.maxDrawdownPct ?? 0) > 20 ? 'text-red-400' : 'text-amber-400'}
          />
          <MetricCell
            icon={<Activity className="h-2.5 w-2.5" />}
            label="Trades"
            value={`${stats?.totalTrades ?? 0}`}
            subtext={`${stats?.winningTrades ?? 0}W / ${stats?.losingTrades ?? 0}L`}
            color="text-cyan-400"
          />
          <MetricCell
            icon={<Clock className="h-2.5 w-2.5" />}
            label="Last Scan"
            value={timeAgo(stats?.lastScanAt ?? null)}
            color="text-[#94a3b8]"
          />
        </div>

        {/* ---- EQUITY CURVE CHART ---- */}
        {equityCurveData.length > 1 && (
          <div className="px-3 py-3 border-b border-[#1e293b]">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                Equity Curve
              </span>
              <span className="text-[9px] font-mono text-[#475569]">
                {equityCurveData.length - 1} trades
              </span>
            </div>
            <div className="h-[160px]">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={equityCurveData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis
                    dataKey="trade"
                    tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }}
                    axisLine={{ stroke: '#1e293b' }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }}
                    axisLine={{ stroke: '#1e293b' }}
                    tickLine={false}
                    width={50}
                    tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#0d1117',
                      border: '1px solid #1e293b',
                      borderRadius: '6px',
                      fontSize: '10px',
                      fontFamily: 'monospace',
                    }}
                    labelStyle={{ color: '#64748b' }}
                    itemStyle={{ color: '#e2e8f0' }}
                    formatter={(value: number, name: string) => {
                      if (name === 'capital') return [`$${value.toFixed(2)}`, 'Capital'];
                      if (name === 'drawdown') return [`${value.toFixed(2)}%`, 'Drawdown'];
                      return [value, name];
                    }}
                  />
                  <ReferenceLine
                    y={stats?.initialCapital ?? 10}
                    stroke="#475569"
                    strokeDasharray="3 3"
                    strokeWidth={1}
                  />
                  <Area
                    type="monotone"
                    dataKey="capital"
                    stroke="#10b981"
                    fill="url(#capitalGradient)"
                    strokeWidth={2}
                  />
                  <defs>
                    <linearGradient id="capitalGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            {/* Mini charts row: Drawdown + Win Rate */}
            <div className="grid grid-cols-2 gap-3 mt-3">
              {/* Drawdown chart */}
              {equityCurveData.length > 2 && (
                <div>
                  <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Drawdown %
                  </span>
                  <div className="h-[80px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={equityCurveData} margin={{ top: 2, right: 5, left: 5, bottom: 2 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis dataKey="trade" tick={{ fontSize: 7, fill: '#475569' }} tickLine={false} axisLine={false} />
                        <YAxis tick={{ fontSize: 7, fill: '#475569' }} tickLine={false} axisLine={false} width={30} tickFormatter={(v: number) => `${v}%`} />
                        <Tooltip
                          contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '4px', fontSize: '9px', fontFamily: 'monospace' }}
                          formatter={(v: number) => [`${v.toFixed(2)}%`, 'DD']}
                        />
                        <Area type="monotone" dataKey="drawdown" stroke="#ef4444" fill="url(#ddGradient)" strokeWidth={1.5} />
                        <defs>
                          <linearGradient id="ddGradient" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                            <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* Rolling win rate chart */}
              {winRateData.length > 2 && (
                <div>
                  <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                    Win Rate (rolling 10)
                  </span>
                  <div className="h-[80px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={winRateData} margin={{ top: 2, right: 5, left: 5, bottom: 2 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis dataKey="trade" tick={{ fontSize: 7, fill: '#475569' }} tickLine={false} axisLine={false} />
                        <YAxis tick={{ fontSize: 7, fill: '#475569' }} tickLine={false} axisLine={false} width={30} domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
                        <Tooltip
                          contentStyle={{ backgroundColor: '#0d1117', border: '1px solid #1e293b', borderRadius: '4px', fontSize: '9px', fontFamily: 'monospace' }}
                          formatter={(v: number) => [`${v.toFixed(1)}%`, 'WR']}
                        />
                        <ReferenceLine y={50} stroke="#475569" strokeDasharray="3 3" strokeWidth={1} />
                        <Line type="monotone" dataKey="winRate" stroke="#3b82f6" strokeWidth={1.5} dot={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ---- NO DATA PLACEHOLDER ---- */}
        {isStopped && (!stats || stats.totalTrades === 0) && (
          <div className="flex flex-col items-center justify-center py-16 px-6">
            <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-[#d4af37]/10 border border-[#d4af37]/20 mb-4">
              <Wallet className="h-6 w-6 text-[#d4af37]" />
            </div>
            <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">
              Paper Trading Simulator
            </h3>
            <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md mb-4">
              Simulate the Brain&apos;s live decisions with virtual capital. No real money at risk.
              Validates whether the Brain&apos;s signals work in real-time by executing paper trades
              against live market data with realistic slippage and fees.
            </p>
            <Button
              size="sm"
              className="h-7 text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/20"
              onClick={() => startMutation.mutate()}
              disabled={startMutation.isPending}
            >
              {startMutation.isPending ? (
                <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
              ) : (
                <Play className="h-3 w-3 mr-1.5" />
              )}
              Start Paper Trading
            </Button>
          </div>
        )}

        {/* ---- OPEN POSITIONS ---- */}
        {positions.length > 0 && (
          <div className="border-b border-[#1e293b]">
            <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117]">
              <div className="flex items-center gap-2">
                <Activity className="h-3 w-3 text-cyan-400" />
                <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                  Open Positions ({positions.length})
                </span>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-[10px] font-mono">
                <thead>
                  <tr className="bg-[#0a0e17] text-[#475569]">
                    <th className="px-3 py-1.5 text-left">Token</th>
                    <th className="px-3 py-1.5 text-left">Direction</th>
                    <th className="px-3 py-1.5 text-right">Entry</th>
                    <th className="px-3 py-1.5 text-right">Current</th>
                    <th className="px-3 py-1.5 text-right">Size</th>
                    <th className="px-3 py-1.5 text-right">PnL</th>
                    <th className="px-3 py-1.5 text-right">PnL %</th>
                    <th className="px-3 py-1.5 text-right">HWM</th>
                    <th className="px-3 py-1.5 text-center">Exits</th>
                    <th className="px-3 py-1.5 text-right">Hold</th>
                    <th className="px-3 py-1.5 text-center">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((pos) => {
                    const holdMin = pos.entryTime
                      ? (Date.now() - new Date(pos.entryTime).getTime()) / 60000
                      : 0;
                    const isProfit = pos.unrealizedPnl >= 0;
                    return (
                      <tr key={pos.id} className="border-t border-[#1e293b]/50 hover:bg-[#0d1117]/50">
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-[#e2e8f0]">{pos.symbol}</span>
                            <Badge className="text-[7px] h-3 px-1 bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                              {pos.chain}
                            </Badge>
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          <span className={`flex items-center gap-1 ${pos.direction === 'LONG' ? 'text-emerald-400' : 'text-red-400'}`}>
                            {pos.direction === 'LONG' ? <ArrowUpRight className="h-2.5 w-2.5" /> : <ArrowDownRight className="h-2.5 w-2.5" />}
                            {pos.direction}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right text-[#94a3b8]">
                          ${pos.entryPrice < 0.01 ? pos.entryPrice.toExponential(2) : pos.entryPrice.toFixed(pos.entryPrice < 1 ? 6 : 2)}
                        </td>
                        <td className="px-3 py-2 text-right text-[#e2e8f0]">
                          <span className="flex items-center justify-end gap-1">
                            ${pos.currentPrice < 0.01 ? pos.currentPrice.toExponential(2) : pos.currentPrice.toFixed(pos.currentPrice < 1 ? 6 : 2)}
                            {pos.currentPrice > pos.entryPrice ? (
                              <ArrowUp className="h-2.5 w-2.5 text-emerald-400" />
                            ) : pos.currentPrice < pos.entryPrice ? (
                              <ArrowDown className="h-2.5 w-2.5 text-red-400" />
                            ) : null}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right text-[#94a3b8]">
                          {formatCurrency(pos.positionSizeUsd)}
                        </td>
                        <td className={`px-3 py-2 text-right font-bold ${isProfit ? 'text-emerald-400' : 'text-red-400'}`}>
                          {formatCurrency(pos.unrealizedPnl)}
                        </td>
                        <td className={`px-3 py-2 text-right font-bold ${isProfit ? 'text-emerald-400' : 'text-red-400'}`}>
                          {formatPct(pos.unrealizedPnlPct)}
                        </td>
                        <td className="px-3 py-2 text-right text-[#64748b]">
                          ${pos.highWaterMark < 0.01 ? pos.highWaterMark.toExponential(2) : pos.highWaterMark.toFixed(pos.highWaterMark < 1 ? 6 : 2)}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <div className="flex items-center justify-center gap-0.5 flex-wrap">
                            {pos.exitConditions?.slice(0, 3).map((ec, i) => (
                              <Badge key={i} className="text-[6px] h-3 px-1 bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                                {ec.replace(/_/g, ' ')}
                              </Badge>
                            ))}
                          </div>
                        </td>
                        <td className="px-3 py-2 text-right text-[#475569]">
                          {holdMin < 60 ? `${holdMin.toFixed(0)}m` : `${(holdMin / 60).toFixed(1)}h`}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-5 text-[8px] font-mono text-red-400 hover:text-red-300 px-1"
                            onClick={() => forceCloseMutation.mutate(pos.id)}
                            disabled={forceCloseMutation.isPending}
                          >
                            <X className="h-2.5 w-2.5 mr-0.5" />
                            Close
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ---- TRADE HISTORY ---- */}
        {trades.length > 0 && (
          <div>
            <button
              className="flex items-center gap-2 px-4 py-2 bg-[#0d1117] w-full text-left hover:bg-[#0a0e17] transition-colors"
              onClick={() => setShowClosedTrades(!showClosedTrades)}
            >
              <Trophy className="h-3 w-3 text-[#d4af37]" />
              <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                Trade History ({trades.length})
              </span>
              {showClosedTrades ? (
                <ChevronUp className="h-3 w-3 text-[#475569] ml-auto" />
              ) : (
                <ChevronDown className="h-3 w-3 text-[#475569] ml-auto" />
              )}
            </button>

            <AnimatePresence>
              {showClosedTrades && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="overflow-hidden"
                >
                  <div className="overflow-x-auto">
                    <table className="w-full text-[10px] font-mono">
                      <thead>
                        <tr className="bg-[#0a0e17] text-[#475569]">
                          <th className="px-3 py-1.5 text-left">Token</th>
                          <th className="px-3 py-1.5 text-left">Direction</th>
                          <th className="px-3 py-1.5 text-right">Entry</th>
                          <th className="px-3 py-1.5 text-right">Exit</th>
                          <th className="px-3 py-1.5 text-right">PnL</th>
                          <th className="px-3 py-1.5 text-right">PnL %</th>
                          <th className="px-3 py-1.5 text-right">Hold</th>
                          <th className="px-3 py-1.5 text-right">MFE</th>
                          <th className="px-3 py-1.5 text-right">MAE</th>
                          <th className="px-3 py-1.5 text-center">Exit Reason</th>
                          <th className="px-3 py-1.5 text-right">Time</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...trades]
                          .sort((a, b) => new Date(b.exitTime).getTime() - new Date(a.exitTime).getTime())
                          .slice(0, 50)
                          .map((trade) => {
                            const isWin = trade.pnl > 0;
                            return (
                              <tr key={trade.id} className="border-t border-[#1e293b]/50 hover:bg-[#0d1117]/50">
                                <td className="px-3 py-1.5">
                                  <div className="flex items-center gap-1.5">
                                    <span className={`font-bold ${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                                      {trade.symbol}
                                    </span>
                                    <Badge className="text-[7px] h-3 px-1 bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                                      {trade.chain}
                                    </Badge>
                                  </div>
                                </td>
                                <td className="px-3 py-1.5">
                                  <span className={trade.direction === 'LONG' ? 'text-emerald-400' : 'text-red-400'}>
                                    {trade.direction}
                                  </span>
                                </td>
                                <td className="px-3 py-1.5 text-right text-[#94a3b8]">
                                  ${trade.entryPrice < 0.01 ? trade.entryPrice.toExponential(2) : trade.entryPrice.toFixed(trade.entryPrice < 1 ? 6 : 2)}
                                </td>
                                <td className="px-3 py-1.5 text-right text-[#94a3b8]">
                                  ${trade.exitPrice < 0.01 ? trade.exitPrice.toExponential(2) : trade.exitPrice.toFixed(trade.exitPrice < 1 ? 6 : 2)}
                                </td>
                                <td className={`px-3 py-1.5 text-right font-bold ${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                                  {formatCurrency(trade.pnl)}
                                </td>
                                <td className={`px-3 py-1.5 text-right font-bold ${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                                  {formatPct(trade.pnlPct)}
                                </td>
                                <td className="px-3 py-1.5 text-right text-[#475569]">
                                  {trade.holdTimeMin < 60
                                    ? `${trade.holdTimeMin.toFixed(0)}m`
                                    : `${(trade.holdTimeMin / 60).toFixed(1)}h`}
                                </td>
                                <td className="px-3 py-1.5 text-right text-emerald-400/70">
                                  {trade.mfe.toFixed(1)}%
                                </td>
                                <td className="px-3 py-1.5 text-right text-red-400/70">
                                  {trade.mae.toFixed(1)}%
                                </td>
                                <td className="px-3 py-1.5 text-center">
                                  <span className={`text-[9px] ${exitReasonColor(trade.exitReason)}`}>
                                    {exitReasonLabel(trade.exitReason)}
                                  </span>
                                </td>
                                <td className="px-3 py-1.5 text-right text-[#475569]">
                                  {new Date(trade.exitTime).toLocaleTimeString()}
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {/* ---- SCAN INFO ---- */}
        {isRunning && stats && (
          <div className="px-4 py-2 border-t border-[#1e293b] bg-[#0a0e17]">
            <div className="flex items-center gap-4 text-[9px] font-mono text-[#475569]">
              <span className="flex items-center gap-1">
                <RefreshCw className="h-2.5 w-2.5 animate-spin text-emerald-400" style={{ animationDuration: '3s' }} />
                Scanning
              </span>
              <span>Tokens scanned: {stats.tokensScanned.toLocaleString()}</span>
              <span>Signals generated: {stats.signalsGenerated.toLocaleString()}</span>
              <span>Scan interval: {config.scanIntervalMs / 1000}s</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// SUB-COMPONENTS
// ============================================================

function MetricCell({
  icon,
  label,
  value,
  subtext,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  subtext?: string;
  color: string;
}) {
  return (
    <div className="bg-[#0d1117] p-2.5 text-center">
      <div className="flex items-center justify-center gap-1 mb-0.5">
        <span className={color}>{icon}</span>
        <span className="text-[7px] font-mono text-[#64748b] uppercase">{label}</span>
      </div>
      <div className={`text-[12px] font-mono font-bold ${color}`}>{value}</div>
      {subtext && (
        <div className="text-[8px] font-mono text-[#475569]">{subtext}</div>
      )}
    </div>
  );
}

function ConfigField({
  label,
  value,
  type,
  options,
  onChange,
  disabled,
}: {
  label: string;
  value: string | number;
  type: 'number' | 'select';
  options?: string[];
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">
        {label}
      </label>
      {type === 'select' ? (
        <select
          className="h-6 px-2 text-[10px] font-mono bg-[#0a0e17] border border-[#1e293b] rounded text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]/50 disabled:opacity-50"
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
        >
          {options?.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      ) : (
        <input
          type="number"
          className="h-6 px-2 text-[10px] font-mono bg-[#0a0e17] border border-[#1e293b] rounded text-[#e2e8f0] focus:outline-none focus:border-[#3b82f6]/50 disabled:opacity-50 w-full"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          step={typeof value === 'number' && value < 1 ? 0.001 : 1}
        />
      )}
    </div>
  );
}
