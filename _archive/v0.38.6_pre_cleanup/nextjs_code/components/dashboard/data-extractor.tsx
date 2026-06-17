'use client';

import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Activity, Database, Zap, Radio, Search, RefreshCw,
  Square, ChevronRight, Clock, AlertTriangle, CheckCircle2,
  XCircle, Loader2, ArrowUpDown, Wallet, BarChart3,
  Globe, Server, Shield, TrendingUp, Link2, FileDown
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';

// ============================================================
// TYPES
// ============================================================

interface SourceStatus {
  moralis: boolean;
  helius: boolean;
  coingecko: boolean;
  dexscreener: boolean;
  defiLlama: boolean;
  etherscan: boolean;
  cryptoDataDownload: boolean;
}

interface ExtractionJob {
  id: string;
  jobType: string;
  status: string;
  sourcesUsed: string[];
  tokensDiscovered: number;
  candlesStored: number;
  walletsProfiled: number;
  transactionsStored: number;
  signalsGenerated: number;
  protocolsStored: number;
  startedAt: string | null;
  completedAt: string | null;
  durationMs: number;
  errors: string[];
  createdAt: string;
}

interface ExtractorStatus {
  status: 'idle' | 'running' | 'completed' | 'failed';
  isRunning: boolean;
  currentJobId: string | null;
  cacheSize: number;
  activeJobs: number;
  errors: string[];
  sources: SourceStatus;
  config: {
    moralisApiKey: boolean;
    heliusApiKey: boolean;
    coingeckoApiKey: boolean;
    etherscanApiKey: boolean;
  };
  lastResult: unknown;
  lastDuration: number;
  recentJobs: ExtractionJob[];
}

type Phase = 'scan' | 'enrich' | 'ohlcv' | 'traders' | 'wallets' | 'sentiment' | 'protocols' | 'realtime' | 'bulk-backfill' | 'full';

const PHASE_CONFIG: Record<Phase, { label: string; icon: React.ReactNode; description: string; color: string }> = {
  scan: { label: 'SCAN', icon: <Radio className="w-4 h-4" />, description: 'Discover new tokens from DexScreener + CoinGecko + DeFi Llama', color: '#22d3ee' },
  enrich: { label: 'ENRICH', icon: <Search className="w-4 h-4" />, description: 'Get full metadata for discovered tokens', color: '#a78bfa' },
  ohlcv: { label: 'OHLCV', icon: <BarChart3 className="w-4 h-4" />, description: 'Historical candles from CoinGecko + DexScreener', color: '#f59e0b' },
  traders: { label: 'TRADERS', icon: <ArrowUpDown className="w-4 h-4" />, description: 'Find top wallets from token transaction data', color: '#10b981' },
  wallets: { label: 'WALLETS', icon: <Wallet className="w-4 h-4" />, description: 'Full wallet history from Moralis + Helius + Etherscan', color: '#3b82f6' },
  sentiment: { label: 'SENTIMENT', icon: <TrendingUp className="w-4 h-4" />, description: 'Prediction market odds from Polymarket', color: '#ec4899' },
  protocols: { label: 'PROTOCOLS', icon: <Globe className="w-4 h-4" />, description: 'DeFi Llama protocol data, TVL, yields', color: '#8b5cf6' },
  realtime: { label: 'REALTIME', icon: <Zap className="w-4 h-4" />, description: 'Quick realtime sync scan', color: '#ef4444' },
  'bulk-backfill': { label: 'BULK CSV', icon: <FileDown className="w-4 h-4" />, description: 'Bulk OHLCV from CryptoDataDownload CSVs', color: '#14b8a6' },
  full: { label: 'FULL PIPELINE', icon: <Activity className="w-4 h-4" />, description: 'Run all 6 phases: SCAN → ENRICH → OHLCV → TRADERS → WALLETS → PROTOCOLS', color: '#f97316' },
};

const SOURCE_DISPLAY: Record<string, { label: string; rank: string; rate: string }> = {
  moralis: { label: 'Moralis', rank: '🥇', rate: '25 RPS' },
  helius: { label: 'Helius', rank: '🥈', rate: '10 RPS' },
  coingecko: { label: 'CoinGecko', rank: '🥉', rate: '30 RPM' },
  dexscreener: { label: 'DexScreener', rank: '⚡', rate: '300/min' },
  defiLlama: { label: 'DeFi Llama', rank: '🆓', rate: 'Unlimited' },
  etherscan: { label: 'Etherscan V2', rank: '🔒', rate: '3/s' },
  cryptoDataDownload: { label: 'CryptoDataDL', rank: '📦', rate: 'Unlimited' },
};

// ============================================================
// COMPONENT
// ============================================================

export function DataExtractorTerminal() {
  const [status, setStatus] = useState<ExtractorStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [runningPhase, setRunningPhase] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/extractor');
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
        if (data.status === 'running') {
          setRunningPhase(data.lastResult?.phase || 'running');
        } else {
          setRunningPhase(null);
        }
      }
    } catch {
      // Silently fail
    }
  }, []);

  useEffect(() => {
    // Initial fetch on mount
    fetch('/api/extractor')
      .then(res => res.ok ? res.json() : null)
      .then(data => { if (data) setStatus(data); })
      .catch(() => {});

    const interval = setInterval(() => {
      if (autoRefresh || status?.isRunning) {
        fetchStatus();
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [autoRefresh, status?.isRunning]);

  const startExtraction = async (phase: Phase) => {
    setLoading(true);
    setRunningPhase(phase);
    try {
      const res = await fetch('/api/extractor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start', phase }),
      });
      if (res.ok) {
        setTimeout(fetchStatus, 1000);
      }
    } catch (err) {
      console.error('Failed to start extraction:', err);
    }
    setLoading(false);
  };

  const abortExtraction = async () => {
    try {
      await fetch('/api/extractor', { method: 'DELETE' });
      setRunningPhase(null);
      setTimeout(fetchStatus, 500);
    } catch (err) {
      console.error('Failed to abort:', err);
    }
  };

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60000).toFixed(1)}m`;
  };

  const formatTime = (dateStr: string | null) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleTimeString();
  };

  const activeSources = status?.sources ? Object.entries(status.sources).filter(([, v]) => v).length : 0;
  const totalSources = 7;

  return (
    <div className="h-full flex flex-col gap-3 p-1">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Database className="w-5 h-5 text-emerald-400" />
            {status?.isRunning && (
              <motion.div
                className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-emerald-400 rounded-full"
                animate={{ scale: [1, 1.5, 1], opacity: [1, 0.5, 1] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              />
            )}
          </div>
          <div>
            <h2 className="text-sm font-bold text-white tracking-wide">DATA EXTRACTOR</h2>
            <p className="text-[10px] text-slate-400">Universal Data Extraction Engine</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-[10px] border-emerald-500/30 text-emerald-400">
            {activeSources}/{totalSources} Sources
          </Badge>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={fetchStatus}
          >
            <RefreshCw className="w-3 h-3 text-slate-400" />
          </Button>
        </div>
      </div>

      {/* Source Status Grid */}
      <Card className="bg-[#0f1629] border-slate-700/50 p-3">
        <div className="flex items-center gap-2 mb-2">
          <Server className="w-3 h-3 text-cyan-400" />
          <span className="text-[10px] font-semibold text-slate-300 uppercase tracking-wider">Data Sources</span>
        </div>
        <div className="grid grid-cols-4 gap-1.5">
          {Object.entries(SOURCE_DISPLAY).map(([key, display]) => {
            const isActive = status?.sources?.[key as keyof SourceStatus] ?? false;
            return (
              <div
                key={key}
                className={`flex items-center gap-1.5 px-2 py-1.5 rounded text-[10px] ${
                  isActive
                    ? 'bg-emerald-500/10 border border-emerald-500/20'
                    : 'bg-slate-800/50 border border-slate-700/30'
                }`}
              >
                <div className={`w-1.5 h-1.5 rounded-full ${isActive ? 'bg-emerald-400' : 'bg-slate-600'}`} />
                <span className={isActive ? 'text-emerald-300' : 'text-slate-500'}>
                  {display.rank} {display.label}
                </span>
                <span className="text-slate-500 ml-auto">{display.rate}</span>
              </div>
            );
          })}
        </div>
      </Card>

      {/* Phase Control Buttons */}
      <Card className="bg-[#0f1629] border-slate-700/50 p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Zap className="w-3 h-3 text-amber-400" />
            <span className="text-[10px] font-semibold text-slate-300 uppercase tracking-wider">Extraction Phases</span>
          </div>
          {status?.isRunning && (
            <Badge variant="outline" className="text-[10px] border-amber-500/30 text-amber-400 animate-pulse">
              <Loader2 className="w-2.5 h-2.5 mr-1 animate-spin" />
              RUNNING
            </Badge>
          )}
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          {Object.entries(PHASE_CONFIG).map(([phase, config]) => {
            const isPhaseRunning = runningPhase === phase || (status?.isRunning && phase === 'full');
            return (
              <Button
                key={phase}
                variant="ghost"
                size="sm"
                disabled={status?.isRunning || loading}
                onClick={() => startExtraction(phase as Phase)}
                className="h-auto py-2 px-2 justify-start hover:bg-slate-800/80 border border-slate-700/30 group"
              >
                <div className="flex items-center gap-2 w-full">
                  <div style={{ color: config.color }} className="shrink-0">
                    {config.icon}
                  </div>
                  <div className="text-left flex-1 min-w-0">
                    <div className="text-[10px] font-bold text-slate-200 group-hover:text-white">
                      {config.label}
                    </div>
                    <div className="text-[9px] text-slate-500 truncate">
                      {config.description}
                    </div>
                  </div>
                  {isPhaseRunning && (
                    <Loader2 className="w-3 h-3 text-amber-400 animate-spin shrink-0" />
                  )}
                </div>
              </Button>
            );
          })}
        </div>

        {status?.isRunning && (
          <div className="mt-2 flex items-center gap-2">
            <Button
              variant="destructive"
              size="sm"
              className="h-7 text-[10px]"
              onClick={abortExtraction}
            >
              <Square className="w-3 h-3 mr-1" />
              ABORT
            </Button>
            <span className="text-[10px] text-amber-400">
              Running for {formatDuration(status.lastDuration)}
            </span>
          </div>
        )}
      </Card>

      {/* Current Status Summary */}
      <Card className="bg-[#0f1629] border-slate-700/50 p-3">
        <div className="flex items-center gap-2 mb-2">
          <Activity className="w-3 h-3 text-blue-400" />
          <span className="text-[10px] font-semibold text-slate-300 uppercase tracking-wider">Pipeline Metrics</span>
          {status?.cacheSize !== undefined && (
            <Badge variant="outline" className="text-[9px] border-slate-600 text-slate-400 ml-auto">
              Cache: {status.cacheSize} entries
            </Badge>
          )}
        </div>

        <div className="grid grid-cols-6 gap-2">
          {[
            { label: 'Tokens', value: status?.recentJobs?.[0]?.tokensDiscovered || 0, icon: <Link2 className="w-3 h-3" />, color: 'text-cyan-400' },
            { label: 'Candles', value: status?.recentJobs?.[0]?.candlesStored || 0, icon: <BarChart3 className="w-3 h-3" />, color: 'text-amber-400' },
            { label: 'Wallets', value: status?.recentJobs?.[0]?.walletsProfiled || 0, icon: <Wallet className="w-3 h-3" />, color: 'text-blue-400' },
            { label: 'Txs', value: status?.recentJobs?.[0]?.transactionsStored || 0, icon: <ArrowUpDown className="w-3 h-3" />, color: 'text-emerald-400' },
            { label: 'Signals', value: status?.recentJobs?.[0]?.signalsGenerated || 0, icon: <TrendingUp className="w-3 h-3" />, color: 'text-pink-400' },
            { label: 'Protocols', value: status?.recentJobs?.[0]?.protocolsStored || 0, icon: <Globe className="w-3 h-3" />, color: 'text-violet-400' },
          ].map((metric) => (
            <div key={metric.label} className="text-center">
              <div className={`${metric.color} flex items-center justify-center gap-1`}>
                {metric.icon}
                <span className="text-sm font-bold">{metric.value.toLocaleString()}</span>
              </div>
              <div className="text-[9px] text-slate-500">{metric.label}</div>
            </div>
          ))}
        </div>
      </Card>

      {/* Recent Jobs History */}
      <Card className="bg-[#0f1629] border-slate-700/50 p-3 flex-1 min-h-0">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Clock className="w-3 h-3 text-slate-400" />
            <span className="text-[10px] font-semibold text-slate-300 uppercase tracking-wider">Job History</span>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-[10px] text-slate-400 cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="w-3 h-3 rounded border-slate-600"
              />
              Auto-refresh
            </label>
          </div>
        </div>

        <ScrollArea className="h-[calc(100%-24px)]">
          <div className="space-y-1.5">
            <AnimatePresence>
              {status?.recentJobs?.length ? (
                status.recentJobs.map((job) => (
                  <motion.div
                    key={job.id}
                    initial={{ opacity: 0, y: 5 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="bg-slate-800/40 rounded border border-slate-700/30 p-2"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        {job.status === 'COMPLETED' && <CheckCircle2 className="w-3 h-3 text-emerald-400" />}
                        {job.status === 'RUNNING' && <Loader2 className="w-3 h-3 text-amber-400 animate-spin" />}
                        {job.status === 'FAILED' && <XCircle className="w-3 h-3 text-red-400" />}
                        {job.status === 'PENDING' && <Clock className="w-3 h-3 text-slate-400" />}
                        {job.status === 'ABORTED' && <AlertTriangle className="w-3 h-3 text-orange-400" />}
                        <span className="text-[10px] font-bold text-slate-200">{job.jobType}</span>
                        <Badge
                          variant="outline"
                          className={`text-[8px] ${
                            job.status === 'COMPLETED' ? 'border-emerald-500/30 text-emerald-400' :
                            job.status === 'RUNNING' ? 'border-amber-500/30 text-amber-400' :
                            job.status === 'FAILED' ? 'border-red-500/30 text-red-400' :
                            'border-slate-600 text-slate-400'
                          }`}
                        >
                          {job.status}
                        </Badge>
                      </div>
                      <span className="text-[9px] text-slate-500">
                        {formatTime(job.startedAt)} · {job.durationMs ? formatDuration(job.durationMs) : '—'}
                      </span>
                    </div>

                    <div className="flex items-center gap-3 text-[9px]">
                      {job.tokensDiscovered > 0 && (
                        <span className="text-cyan-400">🎯 {job.tokensDiscovered} tokens</span>
                      )}
                      {job.candlesStored > 0 && (
                        <span className="text-amber-400">📊 {job.candlesStored} candles</span>
                      )}
                      {job.walletsProfiled > 0 && (
                        <span className="text-blue-400">👤 {job.walletsProfiled} wallets</span>
                      )}
                      {job.transactionsStored > 0 && (
                        <span className="text-emerald-400">📋 {job.transactionsStored} txs</span>
                      )}
                      {job.protocolsStored > 0 && (
                        <span className="text-violet-400">🌐 {job.protocolsStored} protocols</span>
                      )}
                    </div>

                    {job.sourcesUsed.length > 0 && (
                      <div className="flex items-center gap-1 mt-1 flex-wrap">
                        {job.sourcesUsed.map((source: string) => (
                          <Badge key={source} variant="outline" className="text-[8px] border-slate-600 text-slate-400 px-1 py-0">
                            {source}
                          </Badge>
                        ))}
                      </div>
                    )}

                    {job.errors.length > 0 && (
                      <div className="mt-1 text-[9px] text-red-400/80 truncate">
                        ⚠️ {job.errors[0]}
                        {job.errors.length > 1 && ` (+${job.errors.length - 1} more)`}
                      </div>
                    )}
                  </motion.div>
                ))
              ) : (
                <div className="text-center py-6">
                  <Database className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                  <p className="text-[10px] text-slate-500">No extraction jobs yet</p>
                  <p className="text-[9px] text-slate-600">Start a phase above to begin extracting data</p>
                </div>
              )}
            </AnimatePresence>
          </div>
        </ScrollArea>
      </Card>

      {/* 6-Phase Pipeline Diagram */}
      <Card className="bg-[#0f1629] border-slate-700/50 p-2">
        <div className="flex items-center justify-between gap-1">
          {['SCAN', 'ENRICH', 'OHLCV', 'TRADERS', 'WALLETS', 'PROTOCOLS'].map((phase, i) => (
            <div key={phase} className="flex items-center gap-1">
              <div className="flex flex-col items-center">
                <div className="text-[8px] text-slate-400 font-mono">{phase}</div>
                <div className="w-6 h-1 rounded-full bg-slate-700 mt-0.5">
                  <motion.div
                    className="h-full rounded-full bg-emerald-400"
                    initial={{ width: '0%' }}
                    animate={{ width: status?.recentJobs?.[0]?.status === 'COMPLETED' ? '100%' : '0%' }}
                    transition={{ duration: 0.5, delay: i * 0.2 }}
                  />
                </div>
              </div>
              {i < 5 && <ChevronRight className="w-2 h-2 text-slate-600" />}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
