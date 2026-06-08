'use client';

import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Database,
  Activity,
  Brain,
  TrendingUp,
  TrendingDown,
  Minus,
  Plus,
  RefreshCw,
  BarChart3,
  Layers,
  Radio,
  Search,
  ArrowUpRight,
  ArrowDownRight,
  Clock,
  Zap,
  Target,
  Shield,
  Eye,
  Download,
  Upload,
  ChevronRight,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Loader2,
  Github,
  ExternalLink,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';

// ============================================================
// TYPES
// ============================================================

interface TimeframeData {
  timeframe: string;
  count: number;
  firstTs: number;
  lastTs: number;
}

interface TrieLevelData {
  patternCount: number;
  maxDepth: number;
  name?: string;
}

interface AssetDetail {
  symbol: string;
  assetClass: string;
  weightProfile: string;
  candleCount: number;
  firstSeen: string | null;
  lastUpdated: string | null;
  timeframes: TimeframeData[];
  tries: Record<string, TrieLevelData>;
  totalPatterns: number;
  engineState: any;
}

interface StatusData {
  assets: AssetDetail[];
  totalAssets: number;
  totalCandles: number;
  totalPatterns: number;
  signalCount: number;
  dbSizeBytes: number;
  dbSizeMB: string;
}

interface SignalData {
  id: number;
  symbol: string;
  signal_type: string;
  confidence: number;
  quality_score: number;
  sizing_multiplier: number;
  entry_price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  expected_move_pct: number;
  win_rate: number;
  remaining_candles: number;
  timestamp: number;
  matchedPattern: string[];
  predictedPath: any[];
}

// ============================================================
// API HOOKS
// ============================================================

function usePPMTStatus() {
  return useQuery({
    queryKey: ['ppmt-status'],
    queryFn: async () => {
      const res = await fetch('/api/ppmt/status');
      if (!res.ok) throw new Error('Failed to fetch PPMT status');
      const json = await res.json();
      return json.data as StatusData;
    },
    refetchInterval: 30000,
  });
}

function usePPMTSignals(symbol?: string) {
  return useQuery({
    queryKey: ['ppmt-signals', symbol],
    queryFn: async () => {
      const url = symbol ? `/api/ppmt/signals?symbol=${symbol}&limit=50` : '/api/ppmt/signals?limit=50';
      const res = await fetch(url);
      if (!res.ok) throw new Error('Failed to fetch signals');
      const json = await res.json();
      return json.data as SignalData[];
    },
    refetchInterval: 15000,
  });
}

function usePPMTPrediction(symbol: string | null) {
  return useQuery({
    queryKey: ['ppmt-prediction', symbol],
    queryFn: async () => {
      if (!symbol) return null;
      const res = await fetch(`/api/ppmt/predict?symbol=${encodeURIComponent(symbol)}`);
      if (!res.ok) throw new Error('Failed to fetch prediction');
      const json = await res.json();
      return json.data as { symbol: string; timeframe: string; output: string };
    },
    enabled: !!symbol,
    refetchInterval: 60000,
  });
}

// ============================================================
// UTILITY FUNCTIONS
// ============================================================

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatDate(ts: number | string | null): string {
  if (!ts) return '—';
  if (typeof ts === 'number') {
    return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }
  return ts;
}

function getDataCompleteness(asset: AssetDetail): number {
  // How complete is the data? Based on number of timeframes and candle counts
  const tf = asset.timeframes.length;
  const idealTimeframes = 6; // 1m, 5m, 15m, 1h, 4h, 1d
  const tfScore = Math.min(tf / idealTimeframes, 1);
  const candleScore = Math.min(asset.candleCount / 50000, 1); // 50K candles is "good"
  return (tfScore * 0.4 + candleScore * 0.6) * 100;
}

function getCompletenessColor(pct: number): string {
  if (pct >= 70) return 'text-emerald-400';
  if (pct >= 40) return 'text-amber-400';
  return 'text-red-400';
}

function getCompletenessBadge(pct: number): string {
  if (pct >= 70) return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
  if (pct >= 40) return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
  return 'bg-red-500/10 text-red-400 border-red-500/30';
}

function getSignalTypeIcon(type: string) {
  if (type.includes('LONG')) return <ArrowUpRight className="h-3.5 w-3.5 text-emerald-400" />;
  if (type.includes('SHORT')) return <ArrowDownRight className="h-3.5 w-3.5 text-red-400" />;
  if (type.includes('EXIT')) return <XCircle className="h-3.5 w-3.5 text-red-400" />;
  if (type.includes('HOLD')) return <Minus className="h-3.5 w-3.5 text-amber-400" />;
  return <Radio className="h-3.5 w-3.5 text-[#64748b]" />;
}

function getSignalTypeColor(type: string): string {
  if (type.includes('LONG')) return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
  if (type.includes('SHORT')) return 'bg-red-500/10 text-red-400 border-red-500/30';
  if (type.includes('EXIT')) return 'bg-red-500/10 text-red-400 border-red-500/30';
  return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
}

// ============================================================
// STAT CARD COMPONENT
// ============================================================

function StatCard({ title, value, subtitle, icon: Icon, color }: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
}) {
  return (
    <Card className="bg-[#111827] border-[#1e293b]">
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">{title}</p>
            <p className="text-xl font-mono font-bold text-[#f1f5f9] mt-1">{value}</p>
            {subtitle && <p className="text-[10px] font-mono text-[#475569] mt-0.5">{subtitle}</p>}
          </div>
          <div className={`flex items-center justify-center w-10 h-10 rounded-lg bg-${color}/10 border border-${color}/20`}>
            <Icon className={`h-5 w-5 text-${color}`} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ============================================================
// ASSET ROW COMPONENT
// ============================================================

function AssetRow({ asset, isSelected, onSelect }: {
  asset: AssetDetail;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const completeness = getDataCompleteness(asset);
  const hasAllTries = asset.tries.N1 && asset.tries.N2 && asset.tries.N3 && asset.tries.N4;

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-4 py-3 border-b border-[#1e293b] transition-colors ${
        isSelected ? 'bg-[#3b82f6]/5 border-l-2 border-l-[#3b82f6]' : 'hover:bg-[#1e293b]/30 border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-bold text-[#f1f5f9]">{asset.symbol}</span>
          <Badge variant="outline" className="text-[8px] font-mono px-1.5 py-0 h-4 bg-[#1e293b] text-[#94a3b8] border-[#334155]">
            {asset.assetClass}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          {hasAllTries ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
          ) : (
            <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
          )}
          <ChevronRight className={`h-3.5 w-3.5 ${isSelected ? 'text-[#3b82f6]' : 'text-[#475569]'}`} />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[9px] font-mono text-[#475569]">Data Completeness</span>
            <span className={`text-[9px] font-mono font-bold ${getCompletenessColor(completeness)}`}>
              {Math.round(completeness)}%
            </span>
          </div>
          <div className="h-1 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                completeness >= 70 ? 'bg-emerald-500' : completeness >= 40 ? 'bg-amber-500' : 'bg-red-500'
              }`}
              style={{ width: `${completeness}%` }}
            />
          </div>
        </div>
        <span className="text-[9px] font-mono text-[#475569]">{formatNumber(asset.candleCount)} candles</span>
      </div>
      <div className="flex items-center gap-1.5 mt-1.5">
        {asset.timeframes.map(tf => (
          <Badge key={tf.timeframe} variant="outline" className="text-[7px] font-mono px-1 py-0 h-3.5 bg-[#0a0e17] text-emerald-400 border-emerald-500/30">
            {tf.timeframe}
          </Badge>
        ))}
        {!asset.timeframes.length && (
          <span className="text-[8px] font-mono text-red-400">No data</span>
        )}
      </div>
    </button>
  );
}

// ============================================================
// ASSET DETAIL PANEL
// ============================================================

function AssetDetailPanel({ asset, prediction }: { asset: AssetDetail; prediction: any }) {
  const queryClient = useQueryClient();

  const ingestMutation = useMutation({
    mutationFn: async ({ symbol, timeframe, days }: { symbol: string; timeframe: string; days: number }) => {
      const res = await fetch('/api/ppmt/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, timeframe, days }),
      });
      if (!res.ok) throw new Error('Ingest failed');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ppmt-status'] });
    },
  });

  const buildMutation = useMutation({
    mutationFn: async ({ symbol, timeframe }: { symbol: string; timeframe: string }) => {
      const res = await fetch('/api/ppmt/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, timeframe }),
      });
      if (!res.ok) throw new Error('Build failed');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ppmt-status'] });
    },
  });

  const trieLevels = ['N1', 'N2', 'N3', 'N4'];
  const trieDescriptions: Record<string, string> = {
    N1: 'Universal (all assets)',
    N2: 'Asset Class',
    N3: 'Per-Asset',
    N4: 'Per-Asset + Regime',
  };
  const weights = asset.engineState?.weights;

  return (
    <div className="h-full overflow-y-auto">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[#1e293b]">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-mono text-lg font-bold text-[#f1f5f9]">{asset.symbol}</h2>
            <div className="flex items-center gap-2 mt-1">
              <Badge variant="outline" className="text-[9px] font-mono bg-[#1e293b] text-[#94a3b8] border-[#334155]">
                {asset.assetClass}
              </Badge>
              <Badge variant="outline" className="text-[9px] font-mono bg-[#3b82f6]/10 text-[#3b82f6] border-[#3b82f6]/30">
                {asset.weightProfile}
              </Badge>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[9px] font-mono bg-[#1e293b] border-[#334155] text-[#94a3b8] hover:text-[#f1f5f9]"
              onClick={() => ingestMutation.mutate({ symbol: asset.symbol, timeframe: '1h', days: 365 })}
              disabled={ingestMutation.isPending}
            >
              {ingestMutation.isPending ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Download className="h-3 w-3 mr-1" />}
              Ingest 1h
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[9px] font-mono bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20"
              onClick={() => buildMutation.mutate({ symbol: asset.symbol, timeframe: '1h' })}
              disabled={buildMutation.isPending}
            >
              {buildMutation.isPending ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Layers className="h-3 w-3 mr-1" />}
              Build Trie
            </Button>
          </div>
        </div>
      </div>

      {/* Trie Architecture */}
      <div className="px-4 py-3 border-b border-[#1e293b]">
        <h3 className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider mb-2">4-Level Trie Architecture</h3>
        <div className="grid grid-cols-4 gap-2">
          {trieLevels.map(level => {
            const trie = asset.tries[level];
            const weight = weights ? Object.values(weights).find((_: any, i: number) => i === trieLevels.indexOf(level)) : null;
            return (
              <div key={level} className={`p-2 rounded-lg border ${
                trie ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-[#0a0e17] border-[#1e293b]'
              }`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[9px] font-mono font-bold text-[#f1f5f9]">{level}</span>
                  {trie ? <CheckCircle2 className="h-3 w-3 text-emerald-400" /> : <XCircle className="h-3 w-3 text-[#475569]" />}
                </div>
                <p className="text-[7px] font-mono text-[#475569] mb-1">{trieDescriptions[level]}</p>
                {trie && (
                  <div className="space-y-0.5">
                    <p className="text-[8px] font-mono text-[#94a3b8]">{formatNumber(trie.patternCount)} patterns</p>
                    <p className="text-[8px] font-mono text-[#64748b]">Depth: {trie.maxDepth}</p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {weights && (
          <div className="mt-2 flex items-center gap-2">
            <span className="text-[8px] font-mono text-[#475569]">Weights:</span>
            <div className="flex gap-1">
              {trieLevels.map((level, i) => {
                const w = Object.values(weights)[i];
                return (
                  <span key={level} className="text-[8px] font-mono text-[#3b82f6]">{level}={typeof w === 'number' ? `${(w * 100).toFixed(0)}%` : '—'}</span>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Timeframe Data */}
      <div className="px-4 py-3 border-b border-[#1e293b]">
        <h3 className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider mb-2">Stored Timeframes</h3>
        {asset.timeframes.length === 0 ? (
          <p className="text-[10px] font-mono text-[#475569]">No OHLCV data stored yet. Click &quot;Ingest&quot; to fetch data.</p>
        ) : (
          <div className="space-y-1.5">
            {asset.timeframes.map(tf => (
              <div key={tf.timeframe} className="flex items-center justify-between py-1 px-2 rounded bg-[#0a0e17] border border-[#1e293b]">
                <div className="flex items-center gap-2">
                  <Clock className="h-3 w-3 text-[#3b82f6]" />
                  <span className="text-[10px] font-mono font-bold text-[#f1f5f9]">{tf.timeframe}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-[9px] font-mono text-[#94a3b8]">{formatNumber(tf.count)} candles</span>
                  <span className="text-[8px] font-mono text-[#475569]">
                    {tf.firstTs ? formatDate(tf.firstTs) : '—'} → {tf.lastTs ? formatDate(tf.lastTs) : '—'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="mt-2 flex items-center gap-2">
          <span className="text-[8px] font-mono text-[#475569]">Total candles: {formatNumber(asset.candleCount)}</span>
          <span className="text-[8px] font-mono text-[#475569]">|</span>
          <span className="text-[8px] font-mono text-[#475569]">Total patterns: {formatNumber(asset.totalPatterns)}</span>
        </div>
      </div>

      {/* Prediction */}
      <div className="px-4 py-3">
        <h3 className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider mb-2">Prediction Output</h3>
        {prediction?.output ? (
          <pre className="text-[9px] font-mono text-[#94a3b8] bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3 whitespace-pre-wrap overflow-x-auto max-h-96 overflow-y-auto">
            {prediction.output}
          </pre>
        ) : (
          <div className="flex flex-col items-center justify-center py-6 bg-[#0a0e17] border border-[#1e293b] rounded-lg">
            <Brain className="h-6 w-6 text-[#475569] mb-2" />
            <p className="text-[10px] font-mono text-[#475569]">No prediction available</p>
            <p className="text-[9px] font-mono text-[#334155] mt-1">Build the Trie first, then predict</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// MAIN DASHBOARD
// ============================================================

export default function PPMTDashboard() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [addSymbolInput, setAddSymbolInput] = useState('');

  const { data: status, isLoading: statusLoading, error: statusError } = usePPMTStatus();
  const { data: signals } = usePPMTSignals();
  const { data: prediction } = usePPMTPrediction(selectedSymbol);

  const selectedAsset = status?.assets.find(a => a.symbol === selectedSymbol);

  // Auto-select first asset
  useEffect(() => {
    if (status?.assets?.length && !selectedSymbol) {
      setSelectedSymbol(status.assets[0].symbol);
    }
  }, [status, selectedSymbol]);

  const handleIngestNew = useCallback(async () => {
    if (!addSymbolInput.trim()) return;
    try {
      const res = await fetch('/api/ppmt/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: addSymbolInput.trim().toUpperCase(), timeframe: '1h', days: 365 }),
      });
      if (res.ok) {
        setAddSymbolInput('');
        // Refetch after a delay
        setTimeout(() => {
          window.location.reload();
        }, 2000);
      }
    } catch { /* ignore */ }
  }, [addSymbolInput]);

  return (
    <div className="flex flex-col h-screen bg-[#0a0e17] overflow-hidden">
      {/* Top Bar */}
      <div className="flex items-center justify-between px-4 h-11 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Brain className="h-4 w-4 text-[#3b82f6]" />
            <span className="text-[#3b82f6] font-mono text-xs font-bold tracking-wider">PPMT</span>
            <span className="text-[#475569] font-mono text-[8px]">Dashboard</span>
          </div>
          <div className="h-4 w-px bg-[#1e293b]" />
          <div className="flex items-center gap-1">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="font-mono text-[9px] text-emerald-400">ACTIVE</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {status && (
            <span className="font-mono text-[9px] text-[#475569]">
              DB: {status.dbSizeMB} MB
            </span>
          )}
          <a
            href="https://github.com/coverdraft/ppmt"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-[#475569] hover:text-[#94a3b8] transition-colors"
          >
            <Github className="h-3.5 w-3.5" />
            <span className="font-mono text-[9px] hidden sm:inline">GitHub</span>
          </a>
        </div>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 px-4 py-3 shrink-0">
        <StatCard title="Assets Tracked" value={status?.totalAssets ?? '—'} subtitle="Symbols in database" icon={Database} color="emerald-500" />
        <StatCard title="Total Candles" value={status ? formatNumber(status.totalCandles) : '—'} subtitle="OHLCV data points" icon={BarChart3} color="[#3b82f6]" />
        <StatCard title="Patterns Found" value={status ? formatNumber(status.totalPatterns) : '—'} subtitle="Unique trie patterns" icon={Layers} color="amber-500" />
        <StatCard title="Signals Generated" value={status?.signalCount ?? '—'} subtitle="Trading signals" icon={Radio} color="cyan-500" />
      </div>

      {/* Main Content */}
      <div className="flex-1 flex min-h-0 px-4 pb-4 gap-3">
        {/* Left: Asset List */}
        <div className="w-[320px] shrink-0 flex flex-col bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
          {/* Asset List Header */}
          <div className="px-4 py-2.5 border-b border-[#1e293b] shrink-0">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Tracked Assets</h3>
              <Badge variant="outline" className="text-[8px] font-mono bg-[#1e293b] text-[#94a3b8] border-[#334155] px-1.5 py-0 h-4">
                {status?.assets?.length ?? 0}
              </Badge>
            </div>
            {/* Add Asset */}
            <div className="flex gap-1.5">
              <input
                type="text"
                placeholder="e.g. SOL/USDT"
                value={addSymbolInput}
                onChange={(e) => setAddSymbolInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleIngestNew()}
                className="flex-1 h-7 px-2 text-[10px] font-mono bg-[#0a0e17] border border-[#1e293b] rounded text-[#94a3b8] placeholder-[#334155] focus:border-[#3b82f6]/50 focus:outline-none"
              />
              <Button
                size="sm"
                onClick={handleIngestNew}
                disabled={!addSymbolInput.trim()}
                className="h-7 px-2 bg-[#3b82f6]/10 border border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20 text-[9px] font-mono"
                variant="outline"
              >
                <Plus className="h-3 w-3" />
              </Button>
            </div>
          </div>

          {/* Asset List */}
          <div className="flex-1 overflow-y-auto">
            {statusLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-5 w-5 text-[#475569] animate-spin" />
              </div>
            ) : statusError ? (
              <div className="px-4 py-8 text-center">
                <AlertTriangle className="h-6 w-6 text-red-400 mx-auto mb-2" />
                <p className="text-[10px] font-mono text-red-400">Failed to load assets</p>
                <p className="text-[9px] font-mono text-[#475569] mt-1">Make sure PPMT is initialized</p>
              </div>
            ) : !status?.assets?.length ? (
              <div className="px-4 py-8 text-center">
                <Database className="h-6 w-6 text-[#475569] mx-auto mb-2" />
                <p className="text-[10px] font-mono text-[#475569]">No assets tracked yet</p>
                <p className="text-[9px] font-mono text-[#334155] mt-1">Add a symbol above to get started</p>
              </div>
            ) : (
              status.assets.map(asset => (
                <AssetRow
                  key={asset.symbol}
                  asset={asset}
                  isSelected={selectedSymbol === asset.symbol}
                  onSelect={() => setSelectedSymbol(asset.symbol)}
                />
              ))
            )}
          </div>
        </div>

        {/* Center: Asset Detail */}
        <div className="flex-1 min-w-0 flex flex-col gap-3">
          <div className="flex-1 bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
            {selectedAsset ? (
              <AssetDetailPanel asset={selectedAsset} prediction={prediction} />
            ) : (
              <div className="flex flex-col items-center justify-center h-full">
                <Brain className="h-10 w-10 text-[#334155] mb-3" />
                <p className="text-sm font-mono text-[#475569]">Select an asset to view details</p>
                <p className="text-[10px] font-mono text-[#334155] mt-1">Choose from the list on the left</p>
              </div>
            )}
          </div>
        </div>

        {/* Right: Recent Signals */}
        <div className="w-[280px] shrink-0 bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden flex flex-col">
          <div className="px-4 py-2.5 border-b border-[#1e293b] shrink-0">
            <h3 className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Recent Signals</h3>
          </div>
          <div className="flex-1 overflow-y-auto">
            {!signals?.length ? (
              <div className="px-4 py-8 text-center">
                <Radio className="h-5 w-5 text-[#475569] mx-auto mb-2" />
                <p className="text-[10px] font-mono text-[#475569]">No signals yet</p>
                <p className="text-[9px] font-mono text-[#334155] mt-1">Signals appear when patterns match</p>
              </div>
            ) : (
              signals.map((signal, i) => (
                <div key={signal.id || i} className="px-3 py-2 border-b border-[#1e293b]">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-1.5">
                      {getSignalTypeIcon(signal.signal_type)}
                      <span className="text-[9px] font-mono font-bold text-[#f1f5f9]">{signal.symbol}</span>
                    </div>
                    <Badge variant="outline" className={`text-[7px] font-mono px-1 py-0 h-3.5 ${getSignalTypeColor(signal.signal_type)}`}>
                      {signal.signal_type.replace('ENTRY_', '').replace('EXIT', 'EXIT')}
                    </Badge>
                  </div>
                  <div className="grid grid-cols-3 gap-1">
                    <div>
                      <span className="text-[7px] font-mono text-[#475569]">Conf</span>
                      <p className="text-[9px] font-mono text-[#94a3b8]">{(signal.confidence * 100).toFixed(0)}%</p>
                    </div>
                    <div>
                      <span className="text-[7px] font-mono text-[#475569]">Quality</span>
                      <p className="text-[9px] font-mono text-[#94a3b8]">{(signal.quality_score * 100).toFixed(0)}%</p>
                    </div>
                    <div>
                      <span className="text-[7px] font-mono text-[#475569]">Size</span>
                      <p className="text-[9px] font-mono text-[#94a3b8]">{signal.sizing_multiplier.toFixed(1)}x</p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
