'use client';

import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Radio, Loader2, Filter, ChevronDown } from 'lucide-react';

// ============================================================
// TYPES
// ============================================================

interface DbSignal {
  id: string;
  type: string;
  confidence: number;
  direction: string;
  description: string;
  priceTarget: number | null;
  tokenId: string | null;
  tokenSymbol: string | null;
  chain: string | null;
  createdAt: string;
  metadata: Record<string, unknown> | null;
}

interface FeedSignal {
  id: string;
  type: string;
  tokenSymbol: string;
  chain: string;
  direction: string;
  confidence: number;
  price: number;
  timestamp: number;
  description: string;
}

// ============================================================
// SIGNAL TYPE COLORS (matching signal-center.tsx)
// ============================================================

const SIGNAL_TYPE_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  RUG_PULL: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  SMART_MONEY_ENTRY: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  LIQUIDITY_TRAP: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  V_SHAPE: { bg: 'bg-cyan-500/15', text: 'text-cyan-400', border: 'border-cyan-500/30' },
  DIVERGENCE: { bg: 'bg-purple-500/15', text: 'text-purple-400', border: 'border-purple-500/30' },
  CUSTOM: { bg: 'bg-gray-500/15', text: 'text-gray-400', border: 'border-gray-500/30' },
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

// ============================================================
// CHAIN DISPLAY NAMES
// ============================================================

const CHAIN_OPTIONS = ['ALL', 'SOL', 'ETH', 'BASE', 'BSC', 'MATIC', 'ARB', 'OP', 'AVAX'];

function normalizeChainName(raw: string | null): string {
  if (!raw) return 'UNKNOWN';
  const map: Record<string, string> = {
    solana: 'SOL', ethereum: 'ETH', base: 'BASE', bsc: 'BSC',
    polygon: 'MATIC', arbitrum: 'ARB', optimism: 'OP', avalanche: 'AVAX',
    sol: 'SOL', eth: 'ETH', matic: 'MATIC', arb: 'ARB', op: 'OP', avax: 'AVAX',
  };
  return map[raw.toLowerCase()] || raw.toUpperCase();
}

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

function getTimeGroup(timestamp: number): string {
  const diff = Date.now() - timestamp;
  if (diff < 5 * 60 * 1000) return 'Last 5m';
  if (diff < 15 * 60 * 1000) return 'Last 15m';
  if (diff < 60 * 60 * 1000) return 'Last 1h';
  return 'Older';
}

function formatCompactPrice(price: number): string {
  if (price >= 1000) return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  if (price >= 1) return `$${price.toFixed(2)}`;
  if (price >= 0.001) return `$${price.toFixed(4)}`;
  if (price > 0) return `$${price.toFixed(8)}`;
  return '--';
}

// ============================================================
// SIGNAL CARD (Compact Bloomberg Style)
// ============================================================

function LiveSignalCard({ signal }: { signal: FeedSignal }) {
  const typeColors = SIGNAL_TYPE_COLORS[signal.type] || SIGNAL_TYPE_COLORS.CUSTOM;

  const directionColor =
    signal.direction === 'LONG' ? 'text-emerald-400' :
    signal.direction === 'SHORT' ? 'text-red-400' :
    'text-yellow-400';

  const directionBg =
    signal.direction === 'LONG' ? 'bg-emerald-500/10' :
    signal.direction === 'SHORT' ? 'bg-red-500/10' :
    'bg-yellow-500/10';

  return (
    <motion.div
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      className={`flex items-center gap-1.5 px-2 py-1.5 rounded border ${typeColors.bg} ${typeColors.border}`}
    >
      {/* Direction badge */}
      <span className={`text-[9px] font-mono font-bold px-1 py-0.5 rounded ${directionBg} ${directionColor} w-12 text-center shrink-0`}>
        {signal.direction}
      </span>

      {/* Token symbol */}
      <span className="font-mono text-[10px] font-bold text-[#e2e8f0] w-12 truncate shrink-0">
        {signal.tokenSymbol || '--'}
      </span>

      {/* Chain */}
      <span className="font-mono text-[8px] text-[#64748b] w-8 shrink-0">
        {signal.chain}
      </span>

      {/* Signal type */}
      <span className={`font-mono text-[8px] ${typeColors.text} truncate max-w-[80px]`} title={signal.type}>
        {signal.type.replace(/_/g, ' ')}
      </span>

      {/* Confidence */}
      <div className="flex items-center gap-1 shrink-0 ml-auto">
        <div className="w-8 h-1 bg-[#1a1f2e] rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full ${
              signal.confidence >= 80 ? 'bg-emerald-500' :
              signal.confidence >= 60 ? 'bg-yellow-500' :
              signal.confidence >= 40 ? 'bg-orange-500' : 'bg-red-500'
            }`}
            style={{ width: `${signal.confidence}%` }}
          />
        </div>
        <span className="font-mono text-[8px] text-[#94a3b8] w-6 text-right">{signal.confidence}%</span>
      </div>

      {/* Price */}
      <span className="font-mono text-[9px] text-[#94a3b8] w-16 text-right shrink-0">
        {formatCompactPrice(signal.price)}
      </span>

      {/* Timestamp */}
      <span className="font-mono text-[8px] text-[#475569] w-12 text-right shrink-0">
        {timeAgo(signal.timestamp)}
      </span>
    </motion.div>
  );
}

// ============================================================
// TIME GROUP HEADER
// ============================================================

function TimeGroupHeader({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-center gap-2 px-2 pt-2 pb-1">
      <span className="font-mono text-[8px] text-[#64748b] uppercase tracking-wider">{label}</span>
      <div className="flex-1 h-px bg-[#1e293b]" />
      <Badge className="text-[7px] h-3 px-1 font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748] border">
        {count}
      </Badge>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function SignalLiveFeed() {
  const [chainFilter, setChainFilter] = useState('ALL');
  const [showChainDropdown, setShowChainDropdown] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Fetch signals every 10s
  const { data: signalsData, isLoading } = useQuery({
    queryKey: ['signals-live-feed'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/signals?limit=100');
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();
        return (json.signals || []) as DbSignal[];
      } catch {
        return [] as DbSignal[];
      }
    },
    refetchInterval: 15_000,
    staleTime: 10_000,
  });

  // Transform DB signals to feed signals
  const feedSignals: FeedSignal[] = useMemo(() => {
    if (!signalsData) return [];

    return signalsData.map((s) => ({
      id: s.id,
      type: s.type,
      tokenSymbol: s.tokenSymbol || '--',
      chain: normalizeChainName(s.chain),
      direction: s.direction || 'NEUTRAL',
      confidence: s.confidence,
      price: s.priceTarget ?? 0,
      timestamp: new Date(s.createdAt).getTime(),
      description: s.description,
    }));
  }, [signalsData]);

  // Filter by chain
  const filteredSignals = useMemo(() => {
    if (chainFilter === 'ALL') return feedSignals;
    return feedSignals.filter((s) => s.chain === chainFilter);
  }, [feedSignals, chainFilter]);

  // Group signals by time
  const groupedSignals = useMemo(() => {
    const groups: Record<string, FeedSignal[]> = {};
    const groupOrder = ['Last 5m', 'Last 15m', 'Last 1h', 'Older'];

    for (const signal of filteredSignals) {
      const group = getTimeGroup(signal.timestamp);
      if (!groups[group]) groups[group] = [];
      groups[group].push(signal);
    }

    // Sort within each group by timestamp descending
    for (const key of Object.keys(groups)) {
      groups[key].sort((a, b) => b.timestamp - a.timestamp);
    }

    // Return in order
    return groupOrder
      .filter((g) => groups[g] && groups[g].length > 0)
      .map((g) => ({ label: g, signals: groups[g] }));
  }, [filteredSignals]);

  // Auto-scroll to bottom on new signals
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filteredSignals, autoScroll]);

  // Track scroll position to toggle auto-scroll
  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(isAtBottom);
  }, []);

  // Count by direction
  const longCount = filteredSignals.filter((s) => s.direction === 'LONG').length;
  const shortCount = filteredSignals.filter((s) => s.direction === 'SHORT').length;
  const neutralCount = filteredSignals.filter((s) => s.direction === 'NEUTRAL').length;

  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0a0e17]">
        {/* LIVE indicator */}
        <div className="flex items-center gap-1.5">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          <span className="font-mono text-[9px] font-bold text-emerald-400">LIVE</span>
        </div>

        <div className="h-3.5 w-px bg-[#1e293b]" />

        <Radio className="h-3 w-3 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Signal Feed</span>

        {/* Signal count badge */}
        <Badge className="text-[8px] h-3.5 px-1.5 font-mono font-bold bg-[#d4af37]/20 text-[#d4af37] border border-[#d4af37]/40">
          {filteredSignals.length}
        </Badge>

        <div className="ml-auto flex items-center gap-2">
          {/* Direction counts */}
          <span className="text-[8px] font-mono text-emerald-400">{longCount}L</span>
          <span className="text-[8px] font-mono text-red-400">{shortCount}S</span>
          <span className="text-[8px] font-mono text-yellow-400">{neutralCount}N</span>

          {/* Chain filter dropdown */}
          <div className="relative">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowChainDropdown(!showChainDropdown)}
              className="h-5 px-1.5 text-[9px] font-mono text-[#64748b] hover:text-[#e2e8f0] gap-1"
            >
              <Filter className="h-2.5 w-2.5" />
              {chainFilter}
              <ChevronDown className="h-2 w-2" />
            </Button>
            {showChainDropdown && (
              <div className="absolute right-0 top-6 z-20 bg-[#0d1117] border border-[#1e293b] rounded shadow-lg py-1 min-w-[60px]">
                {CHAIN_OPTIONS.map((chain) => (
                  <button
                    key={chain}
                    onClick={() => {
                      setChainFilter(chain);
                      setShowChainDropdown(false);
                    }}
                    className={`w-full text-left px-2 py-0.5 text-[9px] font-mono hover:bg-[#1a1f2e] ${
                      chainFilter === chain ? 'text-[#d4af37]' : 'text-[#64748b]'
                    }`}
                  >
                    {chain}
                  </button>
                ))}
              </div>
            )}
          </div>

          {isLoading && <Loader2 className="h-3 w-3 text-[#d4af37] animate-spin" />}
        </div>
      </div>

      {/* Signal Feed */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto max-h-[calc(100vh-200px)] p-1.5 space-y-0.5"
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#1e293b #0a0e17' }}
      >
        <AnimatePresence>
          {groupedSignals.map((group) => (
            <div key={group.label}>
              <TimeGroupHeader label={group.label} count={group.signals.length} />
              <div className="space-y-0.5">
                {group.signals.map((signal) => (
                  <LiveSignalCard key={signal.id} signal={signal} />
                ))}
              </div>
            </div>
          ))}
        </AnimatePresence>

        {filteredSignals.length === 0 && (
          <div className="flex flex-col items-center justify-center h-32 text-[#64748b] font-mono text-[10px] gap-2">
            {isLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin text-[#d4af37]" />
                <span>Loading signals...</span>
              </>
            ) : (
              <span>No signals found</span>
            )}
          </div>
        )}
      </div>

      {/* Auto-scroll indicator */}
      {!autoScroll && (
        <div className="px-2 py-1 border-t border-[#1e293b] bg-[#0a0e17]">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setAutoScroll(true);
              if (scrollRef.current) {
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
              }
            }}
            className="h-4 px-1.5 text-[8px] font-mono text-[#64748b] hover:text-[#d4af37] w-full"
          >
            Auto-scroll paused -- click to resume
          </Button>
        </div>
      )}
    </div>
  );
}
