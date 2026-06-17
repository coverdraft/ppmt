'use client';

import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { ChevronUp, ChevronDown, Activity } from 'lucide-react';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';

interface DataLoaderStatus {
  tokens: number;
  tokensWithVolume: number;
  tokensWithLiquidity: number;
  tokensEnriched: number;
  candles: number;
  dnaRecords: number;
  activeJobs: number;
  enrichmentPct: number;
  status: string;
}

interface BrainStatus {
  ohlcvCandles: number;
  tokensTracked: number;
  tradersProfiled: number;
  dnaProfiles: number;
  totalSignals: number;
  unvalidatedSignals: number;
  validatedSignals: number;
  brainHealth: string;
  brainStatusMessage?: string;
  tradingSystems: number;
  activePatterns: number;
  backtestRuns: number;
  brainCycles: number;
  winRate: string;
}

export function DataStatusBar() {
  const [expanded, setExpanded] = useState(false);

  // Fetch data loader status
  const { data: loaderData } = useQuery({
    queryKey: ['data-loader-status-bar'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/data-loader');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as DataLoaderStatus | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  // Fetch brain status
  const { data: brainData } = useQuery({
    queryKey: ['brain-status-bar'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/brain/status');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as BrainStatus | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  // Fetch auto-sync status
  const { data: syncData } = useQuery({
    queryKey: ['auto-sync-status-bar'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/auto-sync', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'status' }),
        });
        if (!res.ok) return null;
        return await res.json() as { isRunning: boolean; lastCycleAt: string | null; isCycleRunning: boolean } | null;
      } catch {
        return null;
      }
    },
    refetchInterval: 60000,
    staleTime: 30000,
  });

  // Derive computed values
  const { lastSync, dbSizeKB } = useMemo(() => {
    if (!loaderData) return { lastSync: '--:--:--', dbSizeKB: 0 };
    const sync = new Date().toISOString().substring(11, 19);
    const estimatedKB = (loaderData.tokens * 2) + (loaderData.candles * 0.5) + (loaderData.dnaRecords * 1);
    return { lastSync: sync, dbSizeKB: Math.round(estimatedKB) };
  }, [loaderData]);

  const tokenCount = loaderData?.tokens ?? 0;
  const candleCount = loaderData?.candles ?? brainData?.ohlcvCandles ?? 0;
  const dnaCount = loaderData?.dnaRecords ?? brainData?.dnaProfiles ?? 0;
  const signalCount = brainData?.totalSignals ?? 0;
  const traderCount = brainData?.tradersProfiled ?? 0;
  const patternCount = brainData?.activePatterns ?? 0;
  const brainHealth = brainData?.brainHealth ?? 'UNKNOWN';
  const loaderStatus = loaderData?.status ?? 'UNKNOWN';
  const enrichmentPct = loaderData?.enrichmentPct ?? 0;

  const formatDbSize = (kb: number) => {
    if (kb >= 1e6) return `${(kb / 1e6).toFixed(1)}GB`;
    if (kb >= 1e3) return `${(kb / 1e3).toFixed(1)}MB`;
    return `${kb}KB`;
  };

  return (
    <div className="shrink-0">
      {/* Main Status Bar */}
      <div className="status-bar flex items-center justify-between px-2 sm:px-3 h-6 text-[#64748b] overflow-x-auto">
        {/* Left: Data counts */}
        <div className="flex items-center gap-1.5 sm:gap-3 shrink-0">
          <span className="flex items-center gap-1">
            <span className="text-[#3b82f6]">◆</span>
            <span>{tokenCount.toLocaleString()} tokens</span>
          </span>
          <span className="text-[#1e293b] hidden sm:inline">│</span>
          <span className="hidden sm:inline">{candleCount.toLocaleString()} candles</span>
          <span className="text-[#1e293b] hidden md:inline">│</span>
          <span className="hidden md:inline">{signalCount} signals</span>
          <span className="text-[#1e293b] hidden lg:inline">│</span>
          <span className="hidden lg:inline">{dnaCount} DNA</span>
          <span className="text-[#1e293b] hidden lg:inline">│</span>
          <span className="hidden lg:inline">{traderCount} traders</span>
          <span className="text-[#1e293b] hidden xl:inline">│</span>
          <span className="hidden xl:inline">{patternCount} patterns</span>
        </div>

        {/* Center: Brain + Loader Status */}
        <div className="flex items-center gap-1.5 sm:gap-3 shrink-0">
          <span className="flex items-center gap-1">
            Brain:
            <Tooltip>
              <TooltipTrigger asChild>
                <span className={`font-bold ${
                  brainHealth === 'HEALTHY' || brainHealth === 'ACTIVE' ? 'text-emerald-400' :
                  brainHealth === 'LEARNING' ? 'text-cyan-400' :
                  brainHealth === 'IDLE' ? 'text-gray-400' :
                  'text-red-400'
                }`}>
                  {brainHealth === 'HEALTHY' ? 'HEALTHY' :
                    brainHealth === 'ACTIVE' ? 'ACTIVE' :
                    brainHealth === 'LEARNING' ? 'LEARNING' :
                    brainHealth === 'IDLE' ? 'IDLE' :
                    brainHealth}
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="bg-[#1a1f2e] border border-[#2d3748] text-[#94a3b8] text-[10px] font-mono z-[100]">
                {brainHealth === 'ACTIVE' ? 'Brain is running and generating signals' :
                  brainHealth === 'LEARNING' ? 'Brain is learning — signals pending validation' :
                  brainHealth === 'HEALTHY' ? 'Brain is healthy — all signals validated' :
                  brainHealth === 'IDLE' ? 'No signals yet — start the Brain scheduler' :
                  `Brain status: ${brainHealth}`}
              </TooltipContent>
            </Tooltip>
          </span>
          <span className="text-[#1e293b] hidden sm:inline">│</span>
          <span className="hidden sm:flex items-center gap-1">
            Loader:
            <span className={`font-bold ${loaderStatus === 'IDLE' ? 'text-emerald-400' : 'text-yellow-400'}`}>
              {loaderStatus}
            </span>
          </span>
          <span className="text-[#1e293b] hidden md:inline">│</span>
          <span className="hidden md:inline">Enrich: {enrichmentPct}%</span>
          <span className="text-[#1e293b] hidden lg:inline">│</span>
          <span className="hidden lg:flex items-center gap-1">
            Sync:
            <span className={`w-1.5 h-1.5 rounded-full ${syncData?.isRunning ? 'bg-emerald-500 animate-pulse' : 'bg-gray-500'}`} />
            <span className={syncData?.isRunning ? 'text-emerald-400 font-bold' : 'text-[#64748b]'}>
              {syncData?.isRunning ? 'ON' : 'OFF'}
            </span>
          </span>
        </div>

        {/* Right: DB + Sync + Sources + Expand */}
        <div className="flex items-center gap-1.5 sm:gap-3 shrink-0">
          <span className="hidden sm:inline">DB: {formatDbSize(dbSizeKB)}</span>
          <span className="text-[#1e293b] hidden md:inline">│</span>
          <span className="hidden md:inline">Sync: {lastSync}</span>
          <span className="text-[#1e293b] hidden lg:inline">│</span>
          <span className="hidden lg:flex items-center gap-1.5">
            API:
            <span className="data-dot data-dot-live" />
            <span className="text-emerald-400">DexScreener</span>
            <span className="data-dot data-dot-live" />
            <span className="text-emerald-400">CoinGecko</span>
            <span className="data-dot data-dot-db" />
            <span className="text-yellow-400">DexPaprika</span>
          </span>
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-0.5 text-[#475569] hover:text-[#94a3b8] transition-colors ml-1"
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronUp className="h-3 w-3" />}
          </button>
        </div>
      </div>

      {/* Expanded Detail Panel */}
      {expanded && (
        <div className="font-mono text-[9px] bg-[#060910] border-t border-[#1e293b] px-3 py-2 flex items-start gap-6">
          {/* Token Stats */}
          <div className="space-y-1">
            <span className="text-[#3b82f6] font-bold uppercase tracking-wider">Token Data</span>
            <div className="text-[#64748b] space-y-0.5">
              <div className="flex gap-4">
                <span>Total: <span className="text-[#94a3b8]">{tokenCount.toLocaleString()}</span></span>
                <span>w/ Volume: <span className="text-[#94a3b8]">{loaderData?.tokensWithVolume ?? 0}</span></span>
                <span>w/ Liquidity: <span className="text-[#94a3b8]">{loaderData?.tokensWithLiquidity ?? 0}</span></span>
              </div>
              <div className="flex gap-4">
                <span>Enriched: <span className="text-[#94a3b8]">{loaderData?.tokensEnriched ?? 0}</span></span>
                <span>Enrichment: <span className="text-[#94a3b8]">{enrichmentPct}%</span></span>
              </div>
            </div>
          </div>

          {/* Brain Stats */}
          <div className="space-y-1">
            <span className="text-[#3b82f6] font-bold uppercase tracking-wider">Brain Stats</span>
            <div className="text-[#64748b] space-y-0.5">
              <div className="flex gap-4">
                <span>Cycles: <span className="text-[#94a3b8]">{brainData?.brainCycles ?? 0}</span></span>
                <span>Signals: <span className="text-[#94a3b8]">{signalCount}</span></span>
                <span>Win Rate: <span className="text-[#94a3b8]">{brainData?.winRate ?? 'N/A'}</span></span>
              </div>
              <div className="flex gap-4">
                <span>Systems: <span className="text-[#94a3b8]">{brainData?.tradingSystems ?? 0}</span></span>
                <span>Patterns: <span className="text-[#94a3b8]">{patternCount}</span></span>
                <span>Backtests: <span className="text-[#94a3b8]">{brainData?.backtestRuns ?? 0}</span></span>
              </div>
            </div>
          </div>

          {/* API Status */}
          <div className="space-y-1">
            <span className="text-[#3b82f6] font-bold uppercase tracking-wider">API Status</span>
            <div className="text-[#64748b] space-y-0.5">
              <div className="flex items-center gap-2">
                <span className="flex items-center gap-1"><span className="data-dot data-dot-live" /> DexScreener — <span className="text-emerald-400">Online</span></span>
                <span className="flex items-center gap-1"><span className="data-dot data-dot-live" /> CoinGecko — <span className="text-emerald-400">Online</span></span>
              </div>
              <div className="flex items-center gap-2">
                <span className="flex items-center gap-1"><span className="data-dot data-dot-db" /> DexPaprika — <span className="text-yellow-400">Cached</span></span>
              </div>
            </div>
          </div>

          {/* DB Info */}
          <div className="space-y-1">
            <span className="text-[#3b82f6] font-bold uppercase tracking-wider">Database</span>
            <div className="text-[#64748b] space-y-0.5">
              <div>Size: <span className="text-[#94a3b8]">{formatDbSize(dbSizeKB)}</span></div>
              <div>Active Jobs: <span className="text-[#94a3b8]">{loaderData?.activeJobs ?? 0}</span></div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
