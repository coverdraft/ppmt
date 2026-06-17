'use client';

import { useCryptoStore } from '@/store/crypto-store';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ChevronDown,
  ChevronUp,
  Database,
  Loader2,
  Radio,
  Target,
  Clock,
  Timer,
  Brain,
  Zap,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Waves,
  Activity,
} from 'lucide-react';

// ============================================================
// TYPES
// ============================================================

type SignalSource = 'ws' | 'db' | 'predictive';

interface DbSignal {
  id: string;
  type: string;
  confidence: number;
  direction: string;
  description: string;
  priceTarget: number | null;
  tokenId: string | null;
  tokenSymbol: string | null;
  tokenName?: string | null;
  chain: string | null;
  createdAt: string;
  metadata: Record<string, unknown> | null;
}

interface PredictiveSignal {
  id: string;
  signalType: string;
  chain: string;
  tokenAddress: string | null;
  sector: string | null;
  prediction: string;
  confidence: number;
  timeframe: string;
  validUntil: string;
  evidence: string;
  historicalHitRate: number;
  dataPointsUsed: number;
  createdAt: string;
}

interface MergedSignal {
  id: string;
  type: string;
  tokenSymbol: string;
  tokenPrice?: number;
  chain?: string;
  confidence: number;
  direction: string;
  description: string;
  priceTarget?: number;
  timestamp: number;
  source: SignalSource;
  // Predictive-specific fields
  signalType?: string;
  timeframe?: string;
  validUntil?: string;
  hitRate?: number;
  dataPoints?: number;
  prediction?: Record<string, unknown>;
  evidence?: string[];
}

// ============================================================
// CONFIG
// ============================================================

const SIGNAL_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  RUG_PULL: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  SMART_MONEY: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  SMART_MONEY_ENTRY: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  LIQUIDITY_TRAP: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  V_SHAPE: { bg: 'bg-cyan-500/15', text: 'text-cyan-400', border: 'border-cyan-500/30' },
  DIVERGENCE: { bg: 'bg-purple-500/15', text: 'text-purple-400', border: 'border-purple-500/30' },
  PATTERN: { bg: 'bg-sky-500/15', text: 'text-sky-400', border: 'border-sky-500/30' },
  CUSTOM: { bg: 'bg-gray-500/15', text: 'text-gray-400', border: 'border-gray-500/30' },
  // Predictive signal types
  REGIME_CHANGE: { bg: 'bg-violet-500/15', text: 'text-violet-400', border: 'border-violet-500/30' },
  BOT_SWARM: { bg: 'bg-rose-500/15', text: 'text-rose-400', border: 'border-rose-500/30' },
  WHALE_MOVEMENT: { bg: 'bg-cyan-500/15', text: 'text-cyan-400', border: 'border-cyan-500/30' },
  LIQUIDITY_DRAIN: { bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/30' },
  CORRELATION_BREAK: { bg: 'bg-indigo-500/15', text: 'text-indigo-400', border: 'border-indigo-500/30' },
  ANOMALY: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  CYCLE_POSITION: { bg: 'bg-teal-500/15', text: 'text-teal-400', border: 'border-teal-500/30' },
  SECTOR_ROTATION: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  MEAN_REVERSION_ZONE: { bg: 'bg-sky-500/15', text: 'text-sky-400', border: 'border-sky-500/30' },
  SMART_MONEY_POSITIONING: { bg: 'bg-amber-500/15', text: 'text-amber-400', border: 'border-amber-500/30' },
  VOLATILITY_REGIME: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
};

const SIGNAL_ICONS: Record<string, React.ElementType> = {
  RUG_PULL: AlertTriangle,
  SMART_MONEY: Brain,
  SMART_MONEY_ENTRY: Brain,
  LIQUIDITY_TRAP: AlertTriangle,
  V_SHAPE: TrendingUp,
  DIVERGENCE: Activity,
  PATTERN: Activity,
  REGIME_CHANGE: Activity,
  BOT_SWARM: Zap,
  WHALE_MOVEMENT: Waves,
  LIQUIDITY_DRAIN: Waves,
  ANOMALY: AlertTriangle,
  SMART_MONEY_POSITIONING: Brain,
  VOLATILITY_REGIME: Activity,
};

const DIRECTION_ICONS: Record<string, string> = {
  LONG: '\u2191',
  SHORT: '\u2193',
  AVOID: '\uD83D\uDEA1',
};

const SOURCE_CONFIG: Record<SignalSource, { label: string; color: string; dot: string }> = {
  ws: { label: 'WS', color: 'text-gray-400', dot: 'bg-gray-500' },
  db: { label: 'DB', color: 'text-yellow-400', dot: 'bg-yellow-500' },
  predictive: { label: 'AI', color: 'text-violet-400', dot: 'bg-violet-500' },
};

const FILTER_OPTIONS = [
  'ALL',
  // Real-time signals (must match DB Signal.type values)
  'RUG_PULL', 'SMART_MONEY', 'SMART_MONEY_ENTRY', 'LIQUIDITY_TRAP', 'V_SHAPE', 'DIVERGENCE', 'PATTERN',
  // Predictive signals (must match PredictiveSignal.signalType values)
  'REGIME_CHANGE', 'BOT_SWARM', 'WHALE_MOVEMENT', 'LIQUIDITY_DRAIN',
  'CORRELATION_BREAK', 'ANOMALY', 'CYCLE_POSITION', 'SECTOR_ROTATION',
  'MEAN_REVERSION_ZONE', 'SMART_MONEY_POSITIONING', 'VOLATILITY_REGIME',
];

// ============================================================
// HELPERS
// ============================================================

function timeAgo(timestamp: number): string {
  const diff = Date.now() - timestamp;
  if (diff < 5000) return 'just now';
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function parsePrediction(str: string): Record<string, unknown> {
  try { return JSON.parse(str); } catch { return {}; }
}

function parseEvidence(str: string): string[] {
  try { return JSON.parse(str); } catch { return []; }
}

function formatValidity(validUntil: string): string {
  const diff = new Date(validUntil).getTime() - Date.now();
  if (diff <= 0) return 'Expired';
  const mins = Math.floor(diff / 60000);
  const hrs = Math.floor(mins / 60);
  if (hrs > 0) return `${hrs}h ${mins % 60}m`;
  return `${mins}m`;
}

// ============================================================
// SIGNAL CARD
// ============================================================

function SignalCard({ signal, isExpanded, onToggle }: {
  signal: MergedSignal;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const colors = SIGNAL_COLORS[signal.type] || SIGNAL_COLORS.CUSTOM;
  const Icon = SIGNAL_ICONS[signal.type] || Activity;
  const srcConfig = SOURCE_CONFIG[signal.source];

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className={`border rounded-lg p-2.5 ${colors.bg} ${colors.border}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Icon className={`h-3.5 w-3.5 ${colors.text}`} />
          <Badge className={`${colors.bg} ${colors.text} ${colors.border} text-[9px] font-mono font-bold border`}>
            {signal.type.replace(/_/g, ' ')}
          </Badge>
          <span className="font-mono text-[11px] font-bold text-[#e2e8f0]">
            {signal.tokenSymbol || '—'}
          </span>
          {signal.chain && (
            <Badge variant="outline" className="text-[8px] h-3.5 px-1 font-mono border-[#2d3748] text-[#64748b]">
              {signal.chain}
            </Badge>
          )}
          {/* Source indicator */}
          <div className="flex items-center gap-1">
            <span className={`h-1.5 w-1.5 rounded-full ${srcConfig.dot}`} />
            <span className={`text-[8px] font-mono ${srcConfig.color}`}>{srcConfig.label}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`font-mono text-[11px] font-bold ${
            signal.direction === 'LONG' ? 'text-emerald-400' :
            signal.direction === 'SHORT' ? 'text-red-400' : 'text-yellow-400'
          }`}>
            {signal.direction} {DIRECTION_ICONS[signal.direction] || ''}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={onToggle}
            className="h-4 w-4 p-0 text-[#64748b] hover:text-[#e2e8f0]"
          >
            {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </Button>
        </div>
      </div>

      {/* Confidence bar */}
      <div className="mt-1.5 flex items-center gap-2">
        <div className="flex-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              signal.confidence >= 80 ? 'bg-emerald-500' :
              signal.confidence >= 60 ? 'bg-yellow-500' :
              signal.confidence >= 40 ? 'bg-orange-500' : 'bg-red-500'
            }`}
            style={{ width: `${signal.confidence}%` }}
          />
        </div>
        <span className="mono-data text-[10px] text-[#94a3b8] w-8 text-right">{signal.confidence}%</span>
      </div>

      {/* Description + timestamp */}
      <div className="mt-1 flex items-center justify-between">
        <span className="text-[10px] text-[#94a3b8] line-clamp-1 flex-1">{signal.description}</span>
        <span className="mono-data text-[9px] text-[#64748b] ml-2 shrink-0">{timeAgo(signal.timestamp)}</span>
      </div>

      {/* Predictive-specific badges */}
      {signal.source === 'predictive' && (
        <div className="mt-1.5 flex items-center gap-1.5">
          {signal.hitRate !== undefined && (
            <Badge className="text-[8px] h-3.5 px-1 font-mono bg-emerald-500/15 text-emerald-400 border-emerald-500/30 border">
              <Target className="h-2 w-2 mr-0.5" />
              {Math.round(signal.hitRate * 100)}% hit
            </Badge>
          )}
          {signal.timeframe && (
            <Badge variant="outline" className="text-[8px] h-3.5 px-1 font-mono border-[#2d3748] text-[#64748b]">
              <Clock className="h-2 w-2 mr-0.5" />
              {signal.timeframe}
            </Badge>
          )}
          {signal.validUntil && (
            <Badge variant="outline" className="text-[8px] h-3.5 px-1 font-mono border-[#2d3748] text-[#64748b]">
              <Timer className="h-2 w-2 mr-0.5" />
              {formatValidity(signal.validUntil)}
            </Badge>
          )}
        </div>
      )}

      {/* Expanded details */}
      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="mt-2.5 pt-2.5 border-t border-[#2d3748] space-y-1.5">
              {/* Price target */}
              {signal.priceTarget && (
                <div className="flex justify-between">
                  <span className="text-[10px] text-[#64748b] font-mono">Price Target</span>
                  <span className="mono-data text-[11px] text-[#d4af37]">${signal.priceTarget.toLocaleString()}</span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-[10px] text-[#64748b] font-mono">Confidence</span>
                <span className="mono-data text-[11px] text-[#e2e8f0]">{signal.confidence}%</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[10px] text-[#64748b] font-mono">Direction</span>
                <span className={`mono-data text-[11px] font-bold ${
                  signal.direction === 'LONG' ? 'text-emerald-400' :
                  signal.direction === 'SHORT' ? 'text-red-400' : 'text-yellow-400'
                }`}>{signal.direction}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[10px] text-[#64748b] font-mono">Source</span>
                <span className={`mono-data text-[11px] ${srcConfig.color}`}>{srcConfig.label}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[10px] text-[#64748b] font-mono">Time</span>
                <span className="mono-data text-[11px] text-[#94a3b8]">{new Date(signal.timestamp).toLocaleTimeString()}</span>
              </div>

              {/* Predictive signal data */}
              {signal.source === 'predictive' && signal.prediction && (
                <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b] mt-1">
                  <div className="flex items-center gap-1 mb-1">
                    <Zap className="h-3 w-3 text-[#d4af37]" />
                    <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">Prediction Data</span>
                  </div>
                  {Object.entries(signal.prediction).slice(0, 6).map(([key, value]) => (
                    <div key={key} className="flex items-center justify-between py-0.5">
                      <span className="text-[9px] font-mono text-[#64748b]">
                        {key.replace(/([A-Z])/g, ' $1').replace(/^./, (s) => s.toUpperCase())}
                      </span>
                      <span className="mono-data text-[10px] font-bold text-[#e2e8f0]">
                        {typeof value === 'number'
                          ? value >= 1000 ? `$${(value / 1000).toFixed(1)}K`
                            : value < 1 && value > 0 ? `${(value * 100).toFixed(1)}%`
                            : value.toFixed(2)
                          : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Evidence */}
              {signal.source === 'predictive' && signal.evidence && signal.evidence.length > 0 && (
                <div className="bg-[#0a0e17] rounded-md p-2 border border-[#1e293b]">
                  <div className="flex items-center gap-1 mb-1">
                    <Activity className="h-3 w-3 text-[#d4af37]" />
                    <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">Evidence</span>
                  </div>
                  <ul className="space-y-0.5">
                    {signal.evidence.slice(0, 4).map((ev, i) => (
                      <li key={i} className="flex items-start gap-1 text-[9px] font-mono text-[#94a3b8]">
                        <span className="text-[#d4af37] mt-0.5 shrink-0">&rsaquo;</span>
                        <span>{ev}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {signal.dataPoints !== undefined && (
                <div className="flex justify-between">
                  <span className="text-[10px] text-[#64748b] font-mono">Data Points</span>
                  <span className="mono-data text-[11px] text-[#94a3b8]">{signal.dataPoints}</span>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function SignalCenter() {
  const wsSignals = useCryptoStore((s) => s.signals);
  const signalFilter = useCryptoStore((s) => s.signalFilter);
  const setSignalFilter = useCryptoStore((s) => s.setSignalFilter);
  const [expandedSignal, setExpandedSignal] = useState<string | null>(null);
  const [usePredictive, setUsePredictive] = useState(true);

  // Fetch DB signals (real-time alerts from DB)
  const { data: dbSignalsData } = useQuery({
    queryKey: ['db-signals'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/signals?limit=30');
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();
        return (json.signals || []) as DbSignal[];
      } catch {
        return [] as DbSignal[];
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
  });

  // Fetch predictive signals from Big Data Engine
  const { data: predictiveData, isLoading: predictiveLoading } = useQuery({
    queryKey: ['predictive-signals-center'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/predictive?limit=30');
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();
        return (json.data || []) as PredictiveSignal[];
      } catch {
        return [] as PredictiveSignal[];
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
    enabled: usePredictive,
  });

  // Merge all signal sources
  const mergedSignals = useMemo(() => {
    const merged: MergedSignal[] = [];
    const seenIds = new Set<string>();

    // 1. Predictive signals (highest priority — from Big Data Engine)
    if (usePredictive && predictiveData) {
      for (const ps of predictiveData) {
        if (seenIds.has(ps.id)) continue;
        seenIds.add(ps.id);

        const prediction = parsePrediction(ps.prediction);
        const evidence = parseEvidence(ps.evidence);

        // Derive direction from prediction
        let direction = 'NEUTRAL';
        if (prediction.direction === 'ACCUMULATING' || prediction.netDirection === 'INFLOW') direction = 'LONG';
        else if (prediction.direction === 'DISTRIBUTING' || prediction.netDirection === 'OUTFLOW') direction = 'SHORT';
        else if (prediction.toRegime === 'BULL') direction = 'LONG';
        else if (prediction.toRegime === 'BEAR') direction = 'SHORT';
        else if (ps.signalType === 'RUG_PULL' || ps.signalType === 'LIQUIDITY_DRAIN') direction = 'AVOID';

        // Derive description from signal type + chain
        const desc = `${ps.signalType.replace(/_/g, ' ')} detected on ${ps.chain}${ps.sector ? ` · ${ps.sector}` : ''}`;

        merged.push({
          id: ps.id,
          type: ps.signalType,
          tokenSymbol: ps.tokenAddress ? ps.tokenAddress.slice(0, 8) + '...' : '—',
          chain: ps.chain,
          confidence: Math.round(ps.confidence * 100),
          direction,
          description: desc,
          timestamp: new Date(ps.createdAt).getTime(),
          source: 'predictive',
          signalType: ps.signalType,
          timeframe: ps.timeframe,
          validUntil: ps.validUntil,
          hitRate: ps.historicalHitRate,
          dataPoints: ps.dataPointsUsed,
          prediction,
          evidence,
        });
      }
    }

    // 2. DB signals (real-time alerts from DB)
    if (dbSignalsData) {
      for (const ds of dbSignalsData) {
        if (seenIds.has(ds.id)) continue;
        seenIds.add(ds.id);

        merged.push({
          id: ds.id,
          type: ds.type,
          tokenSymbol: ds.tokenSymbol || ds.tokenName || '—',
          chain: ds.chain || undefined,
          confidence: ds.confidence,
          direction: ds.direction,
          description: ds.description,
          priceTarget: ds.priceTarget || undefined,
          timestamp: new Date(ds.createdAt).getTime(),
          source: 'db',
        });
      }
    }

    // 3. WS signals (real-time from WebSocket)
    for (const ws of wsSignals) {
      if (seenIds.has(ws.id)) continue;
      seenIds.add(ws.id);

      merged.push({
        id: ws.id,
        type: ws.type,
        tokenSymbol: ws.tokenSymbol || '—',
        tokenPrice: ws.tokenPrice,
        chain: ws.chain || undefined,
        confidence: ws.confidence,
        direction: ws.direction,
        description: ws.description,
        priceTarget: ws.priceTarget || undefined,
        timestamp: ws.timestamp,
        source: 'ws',
      });
    }

    // Sort by timestamp (newest first)
    merged.sort((a, b) => b.timestamp - a.timestamp);

    return merged;
  }, [wsSignals, dbSignalsData, predictiveData, usePredictive]);

  // Filter
  const filteredSignals = signalFilter === 'ALL'
    ? mergedSignals
    : mergedSignals.filter((s) => s.type === signalFilter);

  // Source counts
  const predictiveCount = mergedSignals.filter(s => s.source === 'predictive').length;
  const dbCount = mergedSignals.filter(s => s.source === 'db').length;
  const wsCount = mergedSignals.filter(s => s.source === 'ws').length;

  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0a0e17]">
        <Database className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Signal Center</span>

        <div className="ml-auto flex items-center gap-3">
          {/* Source counts */}
          <span className="text-[9px] font-mono text-[#64748b]">
            {predictiveCount > 0 && <span className="text-violet-400">{predictiveCount} AI</span>}
            {predictiveCount > 0 && dbCount > 0 && <span className="text-[#475569]"> · </span>}
            {dbCount > 0 && <span className="text-yellow-400">{dbCount} DB</span>}
            {(predictiveCount > 0 || dbCount > 0) && wsCount > 0 && <span className="text-[#475569]"> · </span>}
            {wsCount > 0 && <span className="text-gray-400">{wsCount} WS</span>}
          </span>

          {/* Toggle predictive */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setUsePredictive(!usePredictive)}
            className={`h-5 px-1.5 text-[9px] font-mono ${
              usePredictive ? 'text-violet-400 hover:text-violet-300' : 'text-[#64748b] hover:text-[#94a3b8]'
            }`}
          >
            <Brain className="h-2.5 w-2.5 mr-1" />
            {usePredictive ? 'AI On' : 'AI Off'}
          </Button>

          {predictiveLoading && <Loader2 className="h-3 w-3 text-violet-400 animate-spin" />}
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex items-center gap-1 p-2 border-b border-[#1e293b] overflow-x-auto">
        {FILTER_OPTIONS.map((type) => {
          const colors = SIGNAL_COLORS[type] || { text: 'text-[#94a3b8]' };
          return (
            <Button
              key={type}
              variant="ghost"
              size="sm"
              onClick={() => setSignalFilter(type)}
              className={`h-5 px-1.5 text-[9px] font-mono whitespace-nowrap ${
                signalFilter === type
                  ? `${colors.bg || 'bg-[#d4af37]/20'} ${colors.text || 'text-[#d4af37]'}`
                  : 'text-[#64748b] hover:text-[#e2e8f0]'
              }`}
            >
              {type.replace(/_/g, ' ')}
            </Button>
          );
        })}
        <div className="ml-auto">
          <span className="mono-data text-[9px] text-[#475569]">{filteredSignals.length} signals</span>
        </div>
      </div>

      {/* Signal Feed */}
      <div className="flex-1 overflow-y-auto max-h-[calc(100vh-280px)] p-2 space-y-1.5">
        <AnimatePresence>
          {filteredSignals.map((signal) => (
            <SignalCard
              key={signal.id}
              signal={signal}
              isExpanded={expandedSignal === signal.id}
              onToggle={() => setExpandedSignal(expandedSignal === signal.id ? null : signal.id)}
            />
          ))}
        </AnimatePresence>

        {filteredSignals.length === 0 && (
          <div className="flex flex-col items-center justify-center h-32 text-[#64748b] font-mono text-xs gap-2">
            {predictiveLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin text-violet-400" />
                <span>Loading signals...</span>
              </>
            ) : (
              <span>No signals matching filter</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
