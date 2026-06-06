'use client';

import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { useCryptoStore, type TraderIntelFilter } from '@/store/crypto-store';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Search,
  Bot,
  Eye,
  Anchor,
  Crosshair,
  Users,
  TrendingUp,
  TrendingDown,
  Activity,
  ArrowUpDown,
  ExternalLink,
  Shield,
  AlertTriangle,
  Link2,
  Clock,
  Zap,
  RefreshCw,
  Loader2,
} from 'lucide-react';
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  LineChart,
  Line,
} from 'recharts';

// ============================================================
// TYPES
// ============================================================

interface TraderRow {
  id: string;
  address: string;
  chain: string;
  ensName?: string | null;
  solName?: string | null;
  primaryLabel: string;
  isBot: boolean;
  botType?: string | null;
  isSmartMoney: boolean;
  isWhale: boolean;
  isSniper: boolean;
  winRate: number;
  totalPnl: number;
  totalVolumeUsd: number;
  smartMoneyScore: number;
  whaleScore: number;
  sniperScore: number;
  totalTrades: number;
  lastActive: string;
  behaviorPatterns?: { pattern: string; confidence: number }[];
}

interface TraderDetail {
  id: string;
  address: string;
  chain: string;
  ensName?: string | null;
  solName?: string | null;
  primaryLabel: string;
  subLabels: string[];
  isBot: boolean;
  botType?: string | null;
  botConfidence: number;
  botDetectionSignals: string[];
  isSmartMoney: boolean;
  isWhale: boolean;
  isSniper: boolean;
  winRate: number;
  totalPnl: number;
  totalVolumeUsd: number;
  totalTrades: number;
  avgHoldTimeMin: number;
  smartMoneyScore: number;
  whaleScore: number;
  sniperScore: number;
  tradingHourPattern: number[];
  washTradeScore: number;
  copyTradeScore: number;
  frontrunCount: number;
  sandwichCount: number;
  mevExtractionUsd: number;
  isActive247: boolean;
  lastActive: string;
  behaviorPatterns: { id: string; pattern: string; confidence: number; dataPoints: number }[];
  crossChainLinks: { linkedChain: string; linkedAddress: string; linkType: string; linkConfidence: number }[];
  transactions: {
    id: string;
    txHash: string;
    chain: string;
    action: string;
    tokenSymbol?: string | null;
    valueUsd: number;
    pnlUsd?: number | null;
    blockTime: string;
    isFrontrun: boolean;
    isSandwich: boolean;
  }[];
  derived: {
    riskLevel: string;
    riskFactors: string[];
    profileSummary: string;
    totalTransactions: number;
    totalHoldings: number;
  };
}

interface BotStats {
  totalBots: number;
  totalMevExtracted: number;
  totalFrontruns: number;
  totalSandwiches: number;
  botTypeBreakdown: { type: string; count: number; avgConfidence: number }[];
  chainBreakdown: { chain: string; count: number; totalVolume: number }[];
}

// All data comes from /api/traders and /api/traders/bots

// ============================================================
// HELPERS
// ============================================================

function shortAddress(addr: string): string {
  if (addr.length <= 12) return addr;
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function formatUsd(val: number): string {
  if (val == null || isNaN(val)) return '$0';
  if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(2)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}

function formatPnl(val: number): string {
  if (val >= 0) return `+$${formatUsd(val).slice(1)}`;
  return `-$${formatUsd(Math.abs(val)).slice(1)}`;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

const LABEL_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  BOT_MEV: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  BOT_SNIPER: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  BOT_COPY: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  BOT_ARBITRAGE: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  BOT_SANDWICH: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  BOT_WASH: { bg: 'bg-red-500/15', text: 'text-red-400', border: 'border-red-500/30' },
  SMART_MONEY: { bg: 'bg-cyan-500/15', text: 'text-cyan-400', border: 'border-cyan-500/30' },
  WHALE: { bg: 'bg-amber-500/15', text: 'text-amber-400', border: 'border-amber-500/30' },
  SNIPER: { bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/30' },
  RETAIL: { bg: 'bg-gray-500/15', text: 'text-gray-400', border: 'border-gray-500/30' },
  UNKNOWN: { bg: 'bg-gray-500/15', text: 'text-gray-500', border: 'border-gray-500/30' },
};

function getLabelColors(label: string) {
  if (label.startsWith('BOT_')) return LABEL_COLORS.BOT_MEV;
  return LABEL_COLORS[label] || LABEL_COLORS.UNKNOWN;
}

const CHAIN_COLORS: Record<string, string> = {
  SOL: '#9945FF',
  ETH: '#627EEA',
  BASE: '#0052FF',
  ARB: '#28A0F0',
};

const BOT_TYPE_COLORS: Record<string, string> = {
  MEV_EXTRACTOR: '#ef4444',
  SNIPER_BOT: '#f97316',
  SANDWICH_BOT: '#eab308',
  COPY_BOT: '#a855f7',
  WASH_TRADING_BOT: '#ec4899',
  ARBITRAGE_BOT: '#06b6d4',
  JUST_IN_TIME_BOT: '#22c55e',
};

// ============================================================
// SUB-COMPONENTS
// ============================================================

function CircularScoreGauge({ value, label, color, size = 80 }: { value: number; label: string; color: string; size?: number }) {
  const radius = (size - 12) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#1a1f2e" strokeWidth="4" />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth="4"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            style={{ filter: `drop-shadow(0 0 4px ${color}60)` }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="mono-data text-sm font-bold" style={{ color }}>{value}</span>
        </div>
      </div>
      <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">{label}</span>
    </div>
  );
}

function ConfidenceBar({ value, maxVal = 1, color }: { value: number; maxVal?: number; color?: string }) {
  const pct = (value / maxVal) * 100;
  const barColor = color || (pct >= 70 ? '#22c55e' : pct >= 40 ? '#eab308' : '#ef4444');

  return (
    <div className="flex items-center gap-2 w-full">
      <div className="flex-1 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: barColor, boxShadow: `0 0 6px ${barColor}40` }}
        />
      </div>
      <span className="mono-data text-[10px] text-[#94a3b8] w-10 text-right">
        {(value * 100).toFixed(0)}%
      </span>
    </div>
  );
}

function StatCard({ icon: Icon, label, value, sub, iconColor }: { icon: React.ElementType; label: string; value: string; sub?: string; iconColor: string }) {
  return (
    <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 flex items-center gap-3 hover:border-[#2d3748] transition-colors">
      <Icon className="h-5 w-5 shrink-0" style={{ color: iconColor }} />
      <div className="min-w-0">
        <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">{label}</div>
        <div className="mono-data text-lg font-bold text-[#e2e8f0]">{value}</div>
        {sub && <div className="text-[10px] font-mono text-[#94a3b8]">{sub}</div>}
      </div>
    </div>
  );
}

function SortIcon({ active, desc }: { active: boolean; desc: boolean }) {
  if (!active) return <ArrowUpDown className="h-3 w-3 text-[#475569]" />;
  return desc ? <TrendingDown className="h-3 w-3 text-[#d4af37]" /> : <TrendingUp className="h-3 w-3 text-[#d4af37]" />;
}

// ============================================================
// CUSTOM TOOLTIP FOR CHARTS
// ============================================================

function DarkTooltip({ active, payload, label: lbl }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-[#1a1f2e] border border-[#2d3748] rounded px-2 py-1.5 shadow-xl">
      <div className="text-[10px] font-mono text-[#94a3b8] mb-1">{lbl}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="mono-data text-xs" style={{ color: p.color }}>{p.name}: {typeof p.value === 'number' ? p.value.toLocaleString() : p.value}</span>
        </div>
      ))}
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function TraderIntelligencePanel() {
  const traderIntelFilter = useCryptoStore((s) => s.traderIntelFilter);
  const setTraderIntelFilter = useCryptoStore((s) => s.setTraderIntelFilter);
  const [chainFilter, setChainFilter] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTraderId, setSelectedTraderId] = useState<string | null>(null);
  const [sortField, setSortField] = useState<string>('totalPnl');
  const [sortDesc, setSortDesc] = useState(true);
  const [syncStatus, setSyncStatus] = useState<'idle' | 'syncing' | 'done' | 'error'>('idle');
  const [syncMessage, setSyncMessage] = useState('');

  // Sync smart money wallets from DexScreener/DexPaprika
  const handleSyncTraders = useCallback(async () => {
    if (syncStatus === 'syncing') return;
    setSyncStatus('syncing');
    setSyncMessage('Scanning wallets from on-chain data...');
    try {
      const res = await fetch('/api/smart-money-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'full' }),
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data.success) {
        setSyncStatus('done');
        setSyncMessage(`Sync complete — ${data.totalTraders || 0} traders discovered`);
      } else {
        setSyncStatus('error');
        setSyncMessage(data.error || 'Sync failed — click retry');
      }
    } catch (err) {
      setSyncStatus('error');
      setSyncMessage(err instanceof Error ? `${err.message} — click retry` : 'Network error — click retry');
    }
    // Keep status visible longer so user can see result
    setTimeout(() => setSyncStatus('idle'), 12000);
  }, [syncStatus]);

  // Auto-sync on mount if no traders exist
  // Uses a retry-capable approach: if the first auto-sync fails, the user can
  // still click the "Sync Traders" button. The autoSyncRef prevents infinite loops
  // but allows manual retry.
  const autoSyncRef = useRef(false);
  const autoSyncRetryCount = useRef(0);
  useEffect(() => {
    if (autoSyncRef.current) return;
    autoSyncRef.current = true;

    // Wait a brief moment for initial query to complete, then auto-sync
    // if the DB has no traders yet
    const timer = setTimeout(async () => {
      try {
        // Quick check if any traders exist in the DB
        const res = await fetch('/api/traders?limit=1');
        if (!res.ok) throw new Error('API error');
        const data = await res.json();
        const hasTraders = data?.traders?.length > 0;
        if (!hasTraders && autoSyncRetryCount.current < 2) {
          autoSyncRetryCount.current++;
          handleSyncTraders();
        }
      } catch {
        // If the check fails, try syncing anyway (up to 2 retries)
        if (autoSyncRetryCount.current < 2) {
          autoSyncRetryCount.current++;
          handleSyncTraders();
        }
      }
    }, 1500);
    return () => clearTimeout(timer);
  }, []);

  // Fetch traders from API
  const { data: tradersData } = useQuery({
    queryKey: ['traders', traderIntelFilter, chainFilter],
    queryFn: async () => {
      try {
        const params = new URLSearchParams();
        params.set('limit', '200');
        if (chainFilter !== 'ALL') params.set('chain', chainFilter);
        if (traderIntelFilter === 'BOTS') params.set('isBot', 'true');
        if (traderIntelFilter === 'SMART_MONEY') params.set('isSmartMoney', 'true');
        if (traderIntelFilter === 'WHALES') params.set('isWhale', 'true');
        if (traderIntelFilter === 'SNIPERS') params.set('isSniper', 'true');
        params.set('sortBy', sortField);
        params.set('sortOrder', sortDesc ? 'desc' : 'asc');
        const res = await fetch(`/api/traders?${params}`);
        if (!res.ok) throw new Error('API error');
        return res.json();
      } catch {
        return null;
      }
    },
    staleTime: 15000,
  });

  // Fetch bot stats
  const { data: botStatsData } = useQuery({
    queryKey: ['bot-stats', chainFilter],
    queryFn: async () => {
      try {
        const params = new URLSearchParams();
        if (chainFilter !== 'ALL') params.set('chain', chainFilter);
        const res = await fetch(`/api/traders/bots?${params}`);
        if (!res.ok) throw new Error('API error');
        return res.json();
      } catch {
        return null;
      }
    },
    staleTime: 30000,
  });

  // Fetch selected trader detail
  const { data: traderDetailData } = useQuery({
    queryKey: ['trader-detail', selectedTraderId],
    queryFn: async () => {
      if (!selectedTraderId) return null;
      try {
        const res = await fetch(`/api/traders/${selectedTraderId}`);
        if (!res.ok) throw new Error('API error');
        return res.json();
      } catch {
        return null;
      }
    },
    enabled: !!selectedTraderId,
    staleTime: 10000,
  });

  // Search
  const { data: searchData } = useQuery({
    queryKey: ['trader-search', searchQuery],
    queryFn: async () => {
      if (searchQuery.length < 2) return null;
      try {
        const res = await fetch(`/api/traders/search?q=${encodeURIComponent(searchQuery)}`);
        if (!res.ok) throw new Error('API error');
        return res.json();
      } catch {
        return null;
      }
    },
    enabled: searchQuery.length >= 2,
    staleTime: 10000,
  });

  // Use API data only
  const traders: TraderRow[] = useMemo(() => {
    if (searchQuery.length >= 2 && searchData?.traders?.length) {
      return searchData.traders;
    }
    if (tradersData?.traders?.length) {
      return tradersData.traders;
    }
    return [];
  }, [tradersData, searchData, searchQuery, chainFilter, traderIntelFilter, sortField, sortDesc]);

  const botStats: BotStats | null = botStatsData?.stats || null;

  // Selected trader detail — API only
  const selectedTrader: TraderDetail | null = useMemo(() => {
    if (traderDetailData?.trader) {
      // API returns { trader: {...}, derived: {...} }
      const trader = traderDetailData.trader;
      const derived = traderDetailData.derived || {
        riskLevel: 'MEDIUM',
        riskFactors: [],
        profileSummary: '',
        totalTransactions: trader._count?.transactions || 0,
        totalHoldings: trader._count?.tokenHoldings || 0,
      };
      // Ensure subLabels is always an array (DB stores as JSON string)
      const safeTrader = {
        ...trader,
        subLabels: typeof trader.subLabels === 'string'
          ? (() => { try { return JSON.parse(trader.subLabels || '[]'); } catch { return []; } })()
          : Array.isArray(trader.subLabels) ? trader.subLabels : [],
        botDetectionSignals: typeof trader.botDetectionSignals === 'string'
          ? (() => { try { return JSON.parse(trader.botDetectionSignals || '[]'); } catch { return []; } })()
          : Array.isArray(trader.botDetectionSignals) ? trader.botDetectionSignals : [],
        tradingHourPattern: typeof trader.tradingHourPattern === 'string'
          ? (() => { try { return JSON.parse(trader.tradingHourPattern || '[]'); } catch { return new Array(24).fill(0); } })()
          : Array.isArray(trader.tradingHourPattern) ? trader.tradingHourPattern : new Array(24).fill(0),
        behaviorPatterns: Array.isArray(trader.behaviorPatterns) ? trader.behaviorPatterns : [],
        crossChainLinks: Array.isArray(trader.crossChainLinks) ? trader.crossChainLinks : [],
        transactions: Array.isArray(trader.transactions) ? trader.transactions : [],
      };
      return { ...safeTrader, derived } as TraderDetail;
    }
    if (!selectedTraderId) return null;
    return null;
  }, [traderDetailData, selectedTraderId]);

  // Stats
  const stats = useMemo(() => {
    const totalTraders = traders.length;
    const bots = traders.filter(t => t.isBot).length;
    const avgSmartScore = traders.filter(t => t.isSmartMoney).length > 0
      ? Math.round(traders.filter(t => t.isSmartMoney).reduce((s, t) => s + t.smartMoneyScore, 0) / traders.filter(t => t.isSmartMoney).length)
      : 0;
    const totalVol = traders.reduce((s, t) => s + t.totalVolumeUsd, 0);
    return { totalTraders, bots, avgSmartScore, totalVol };
  }, [traders]);

  // Sort handler
  const handleSort = useCallback((field: string) => {
    if (sortField === field) {
      setSortDesc(prev => !prev);
    } else {
      setSortField(field);
      setSortDesc(true);
    }
  }, [sortField]);

  // Filter tabs
  const filterTabs: { id: TraderIntelFilter; label: string }[] = [
    { id: 'ALL', label: 'ALL' },
    { id: 'BOTS', label: 'BOTS' },
    { id: 'SMART_MONEY', label: 'SMART MONEY' },
    { id: 'WHALES', label: 'WHALES' },
    { id: 'SNIPERS', label: 'SNIPERS' },
  ];

  const chainTabs = ['ALL', 'SOL', 'ETH', 'BASE', 'ARB'];

  // Bot pie chart data
  const botPieData = botStats?.botTypeBreakdown?.map(b => ({
    name: b.type.replace(/_/g, ' '),
    value: b.count,
    color: BOT_TYPE_COLORS[b.type] || '#94a3b8',
  }));

  // Bot chain bar chart data
  const botBarData = botStats?.chainBreakdown?.map(c => ({
    name: c.chain,
    count: c.count,
    volume: c.totalVolume,
  }));

  // Trading hours chart data for selected trader
  const tradingHoursData = selectedTrader
    ? selectedTrader.tradingHourPattern.map((val, i) => ({ hour: `${i.toString().padStart(2, '0')}`, trades: val }))
    : [];

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ========== HEADER BAR ========== */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0 flex-wrap">
        <div className="flex items-center gap-2">
          <Eye className="h-4 w-4 text-[#d4af37]" />
          <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">TRADER INTELLIGENCE</span>
        </div>

        <div className="h-4 w-px bg-[#1e293b]" />

        {/* Filter Tabs */}
        <div className="flex items-center gap-1">
          {filterTabs.map(tab => (
            <Button
              key={tab.id}
              variant="ghost"
              size="sm"
              onClick={() => setTraderIntelFilter(tab.id)}
              className={`h-6 px-2 text-[10px] font-mono whitespace-nowrap ${
                traderIntelFilter === tab.id
                  ? tab.id === 'BOTS' ? 'bg-red-500/20 text-red-400'
                    : tab.id === 'SMART_MONEY' ? 'bg-cyan-500/20 text-cyan-400'
                    : tab.id === 'WHALES' ? 'bg-amber-500/20 text-amber-400'
                    : tab.id === 'SNIPERS' ? 'bg-orange-500/20 text-orange-400'
                    : 'bg-[#d4af37]/20 text-[#d4af37]'
                  : 'text-[#94a3b8] hover:text-[#e2e8f0]'
              }`}
            >
              {tab.label}
            </Button>
          ))}
        </div>

        <div className="h-4 w-px bg-[#1e293b]" />

        {/* Chain Filter */}
        <div className="flex items-center gap-1">
          {chainTabs.map(chain => (
            <Button
              key={chain}
              variant="ghost"
              size="sm"
              onClick={() => setChainFilter(chain)}
              className={`h-6 px-2 text-[10px] font-mono ${
                chainFilter === chain
                  ? 'bg-[#d4af37]/20 text-[#d4af37]'
                  : 'text-[#94a3b8] hover:text-[#e2e8f0]'
              }`}
            >
              {chain}
            </Button>
          ))}
        </div>

        <div className="h-4 w-px bg-[#1e293b]" />

        {/* Search */}
        <div className="relative w-48">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-[#64748b]" />
          <Input
            placeholder="Search wallet..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="h-6 pl-7 pr-2 text-[10px] font-mono bg-[#1a1f2e] border-[#2d3748] text-[#e2e8f0] placeholder-[#64748b] focus:border-[#d4af37]/50 rounded"
          />
        </div>

        {/* Total Count Badge */}
        <Badge className="text-[10px] h-5 px-2 font-mono bg-[#1a1f2e] text-[#94a3b8] border-[#2d3748] ml-auto">
          {stats.totalTraders} traders
        </Badge>

        {/* Sync Button */}
        <Button
          size="sm"
          onClick={handleSyncTraders}
          disabled={syncStatus === 'syncing'}
          className={`h-6 px-3 text-[10px] font-mono gap-1.5 ${
            syncStatus === 'syncing' ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30' :
            syncStatus === 'done' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' :
            syncStatus === 'error' ? 'bg-red-500/20 text-red-400 border border-red-500/30' :
            'bg-[#d4af37]/20 text-[#d4af37] border border-[#d4af37]/30 hover:bg-[#d4af37]/30'
          }`}
        >
          {syncStatus === 'syncing' ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
          {syncStatus === 'syncing' ? 'Syncing...' : syncStatus === 'done' ? 'Synced!' : 'Sync Traders'}
        </Button>

        {/* Sync Status Message */}
        {syncMessage && syncStatus !== 'idle' && (
          <span className="text-[9px] font-mono text-[#64748b] whitespace-nowrap">{syncMessage}</span>
        )}
      </div>

      {/* ========== STATS BAR ========== */}
      <div className="grid grid-cols-4 gap-2 px-3 py-2 shrink-0">
        <StatCard icon={Users} label="Traders Tracked" value={stats.totalTraders.toString()} sub={`${stats.bots} bots identified`} iconColor="#06b6d4" />
        <StatCard icon={Bot} label="Bots Identified" value={stats.bots.toString()} sub={botStats ? `MEV: ${botStats.botTypeBreakdown?.find(b => b.type === 'MEV_EXTRACTOR')?.count || 0} | Sniper: ${botStats.botTypeBreakdown?.find(b => b.type === 'SNIPER_BOT')?.count || 0}` : 'No bot data'} iconColor="#ef4444" />
        <StatCard icon={Eye} label="Smart Money Avg" value={`${stats.avgSmartScore}`} sub="Composite score" iconColor="#06b6d4" />
        <StatCard icon={Activity} label="Volume Tracked" value={formatUsd(stats.totalVol)} sub="All categories" iconColor="#d4af37" />
      </div>

      {/* ========== MAIN CONTENT: TWO COLUMNS ========== */}
      <div className="flex-1 flex gap-2 px-3 pb-2 min-h-0">
        {/* LEFT: Trader Leaderboard Table */}
        <div className="w-[55%] shrink-0 flex flex-col bg-[#111827] border border-[#1e293b] rounded-lg overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-[#1e293b]">
            <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Leaderboard</span>
            <span className="mono-data text-[10px] text-[#475569]">{traders.length} results</span>
          </div>
          <ScrollArea className="flex-1">
            <table className="w-full">
              <thead className="sticky top-0 bg-[#111827] z-10">
                <tr className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider border-b border-[#1e293b]">
                  <th className="text-left py-2 px-2 w-8">#</th>
                  <th className="text-left py-2 px-2 cursor-pointer select-none" onClick={() => handleSort('address')}>
                    <div className="flex items-center gap-1">Address <SortIcon active={sortField === 'address'} desc={sortDesc} /></div>
                  </th>
                  <th className="text-left py-2 px-2">Label</th>
                  <th className="text-center py-2 px-1">Chain</th>
                  <th className="text-right py-2 px-1 cursor-pointer select-none" onClick={() => handleSort('winRate')}>
                    <div className="flex items-center justify-end gap-1">Win% <SortIcon active={sortField === 'winRate'} desc={sortDesc} /></div>
                  </th>
                  <th className="text-right py-2 px-1 cursor-pointer select-none" onClick={() => handleSort('totalPnl')}>
                    <div className="flex items-center justify-end gap-1">PnL <SortIcon active={sortField === 'totalPnl'} desc={sortDesc} /></div>
                  </th>
                  <th className="text-right py-2 px-1 cursor-pointer select-none" onClick={() => handleSort('totalVolumeUsd')}>
                    <div className="flex items-center justify-end gap-1">Volume <SortIcon active={sortField === 'totalVolumeUsd'} desc={sortDesc} /></div>
                  </th>
                  <th className="text-right py-2 px-1">Score</th>
                  <th className="text-right py-2 px-1 cursor-pointer select-none" onClick={() => handleSort('lastActive')}>
                    <div className="flex items-center justify-end gap-1">Active <SortIcon active={sortField === 'lastActive'} desc={sortDesc} /></div>
                  </th>
                </tr>
              </thead>
              <tbody>
                {traders.length === 0 && (
                  <tr>
                    <td colSpan={9} className="py-12 text-center">
                      <div className="flex flex-col items-center gap-3">
                        <Eye className="h-6 w-6 text-[#2d3748]" />
                        <span className="text-[#64748b] font-mono text-xs">No trader data available</span>
                        <span className="text-[#475569] font-mono text-[10px]">Sync wallets from DexScreener/DexPaprika to populate</span>
                        <Button
                          size="sm"
                          onClick={handleSyncTraders}
                          disabled={syncStatus === 'syncing'}
                          className="h-7 px-4 text-[10px] font-mono gap-1.5 bg-[#d4af37]/20 text-[#d4af37] border border-[#d4af37]/30 hover:bg-[#d4af37]/30"
                        >
                          {syncStatus === 'syncing' ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                          {syncStatus === 'syncing' ? 'Syncing...' : 'Sync Traders Now'}
                        </Button>
                        {syncMessage && (
                          <span className="text-[9px] font-mono text-[#64748b]">{syncMessage}</span>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
                {traders.map((trader, idx) => {
                  const isSelected = selectedTraderId === trader.id;
                  const colors = getLabelColors(trader.primaryLabel);
                  const score = trader.isSmartMoney ? trader.smartMoneyScore
                    : trader.isWhale ? trader.whaleScore
                    : trader.isSniper ? trader.sniperScore
                    : trader.isBot ? Math.round(trader.winRate * 100)
                    : Math.round(trader.winRate * 100);
                  const scoreColor = score >= 80 ? '#22c55e' : score >= 50 ? '#eab308' : '#ef4444';

                  return (
                    <tr
                      key={trader.id}
                      onClick={() => setSelectedTraderId(isSelected ? null : trader.id)}
                      className={`cursor-pointer transition-colors border-b border-[#1e293b]/40 hover:bg-[#1a1f2e] ${
                        isSelected ? 'bg-[#d4af37]/10 border-l-2 border-l-[#d4af37]' : ''
                      }`}
                    >
                      <td className="py-1.5 px-2">
                        <span className="mono-data text-[10px] text-[#475569]">{idx + 1}</span>
                      </td>
                      <td className="py-1.5 px-2">
                        <div className="flex items-center gap-1.5">
                          <span className="mono-data text-xs text-[#e2e8f0]">
                            {trader.ensName || trader.solName || shortAddress(trader.address)}
                          </span>
                          {trader.isBot && <Bot className="h-3 w-3 text-red-400" />}
                        </div>
                      </td>
                      <td className="py-1.5 px-1">
                        <Badge className={`text-[8px] h-4 px-1 font-mono ${colors.bg} ${colors.text} ${colors.border} border`}>
                          {trader.primaryLabel.replace(/BOT_/, '')}
                        </Badge>
                      </td>
                      <td className="py-1.5 px-1 text-center">
                        <span className="mono-data text-[10px]" style={{ color: CHAIN_COLORS[trader.chain] || '#94a3b8' }}>
                          {trader.chain}
                        </span>
                      </td>
                      <td className="py-1.5 px-1 text-right">
                        <span className={`mono-data text-xs ${trader.winRate >= 0.6 ? 'text-emerald-400' : trader.winRate >= 0.4 ? 'text-yellow-400' : 'text-red-400'}`}>
                          {(trader.winRate * 100).toFixed(0)}%
                        </span>
                      </td>
                      <td className="py-1.5 px-1 text-right">
                        <span className={`mono-data text-xs ${trader.totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {formatPnl(trader.totalPnl)}
                        </span>
                      </td>
                      <td className="py-1.5 px-1 text-right">
                        <span className="mono-data text-xs text-[#94a3b8]">{formatUsd(trader.totalVolumeUsd)}</span>
                      </td>
                      <td className="py-1.5 px-1 text-right">
                        <span className="mono-data text-xs font-bold" style={{ color: scoreColor }}>{score}</span>
                      </td>
                      <td className="py-1.5 px-1 text-right">
                        <span className="mono-data text-[10px] text-[#64748b]">{timeAgo(trader.lastActive)}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </ScrollArea>
        </div>

        {/* RIGHT: Selected Trader Detail Panel */}
        <div className="flex-1 flex flex-col bg-[#111827] border border-[#1e293b] rounded-lg overflow-hidden min-w-0">
          {!selectedTrader ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6">
              <Eye className="h-8 w-8 text-[#2d3748]" />
              <div className="text-[#64748b] font-mono text-sm">No Trader Selected</div>
              <div className="text-[#475569] font-mono text-xs text-center">Click a trader from the leaderboard to view their intelligence profile</div>
            </div>
          ) : (
            <ScrollArea className="flex-1">
              <div className="p-3 space-y-3">
                {/* Profile Card */}
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="mono-data text-sm font-bold text-[#e2e8f0]">
                          {selectedTrader.ensName || selectedTrader.solName || shortAddress(selectedTrader.address)}
                        </span>
                        {selectedTrader.isBot && <Bot className="h-3.5 w-3.5 text-red-400" />}
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="mono-data text-[10px] text-[#64748b]">{shortAddress(selectedTrader.address)}</span>
                        <Badge className={`text-[8px] h-4 px-1 font-mono border ${getLabelColors(selectedTrader.primaryLabel).bg} ${getLabelColors(selectedTrader.primaryLabel).text} ${getLabelColors(selectedTrader.primaryLabel).border}`}>
                          {selectedTrader.primaryLabel}
                        </Badge>
                        <span className="mono-data text-[10px]" style={{ color: CHAIN_COLORS[selectedTrader.chain] || '#94a3b8' }}>
                          {selectedTrader.chain}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Badge className={`text-[9px] h-5 px-1.5 font-mono font-bold ${
                        selectedTrader.derived.riskLevel === 'HIGH' ? 'bg-red-500/20 text-red-400 border-red-500/30' :
                        selectedTrader.derived.riskLevel === 'MEDIUM' ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' :
                        'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                      }`}>
                        <AlertTriangle className="h-2.5 w-2.5 mr-0.5" />
                        {selectedTrader.derived.riskLevel}
                      </Badge>
                    </div>
                  </div>
                  {/* Sub labels */}
                  {selectedTrader.subLabels.length > 0 && (
                    <div className="flex items-center gap-1 mt-2 flex-wrap">
                      {selectedTrader.subLabels.map(label => (
                        <Badge key={label} variant="outline" className="text-[8px] h-4 px-1 font-mono border-[#2d3748] text-[#64748b]">
                          {label.replace(/_/g, ' ')}
                        </Badge>
                      ))}
                    </div>
                  )}
                </motion.div>

                {/* Score Gauges */}
                <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
                  <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Score Gauges</span>
                  <div className="flex items-center justify-around mt-2">
                    <CircularScoreGauge value={selectedTrader.smartMoneyScore} label="Smart Money" color="#06b6d4" />
                    <CircularScoreGauge value={selectedTrader.whaleScore} label="Whale" color="#d4af37" />
                    <CircularScoreGauge value={selectedTrader.sniperScore} label="Sniper" color="#f97316" />
                  </div>
                </div>

                {/* Bot Detection (if bot) */}
                <AnimatePresence>
                  {selectedTrader.isBot && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      className="bg-[#0a0e17] border border-red-500/20 rounded-lg p-3"
                    >
                      <div className="flex items-center gap-2 mb-2">
                        <Bot className="h-4 w-4 text-red-400" />
                        <span className="text-[10px] font-mono text-red-400 uppercase tracking-wider font-bold">Bot Detection</span>
                      </div>
                      <div className="grid grid-cols-2 gap-2 mb-2">
                        <div className="bg-[#111827] rounded p-2">
                          <span className="text-[9px] font-mono text-[#64748b]">Type</span>
                          <div className="mono-data text-xs text-[#e2e8f0] mt-0.5">{selectedTrader.botType?.replace(/_/g, ' ') || 'Unknown'}</div>
                        </div>
                        <div className="bg-[#111827] rounded p-2">
                          <span className="text-[9px] font-mono text-[#64748b]">Confidence</span>
                          <div className="mono-data text-xs text-red-400 mt-0.5 font-bold">{(selectedTrader.botConfidence * 100).toFixed(0)}%</div>
                        </div>
                      </div>
                      {/* Detection Signals */}
                      {selectedTrader.botDetectionSignals.length > 0 && (
                        <div className="space-y-1">
                          <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Detection Signals</span>
                          {selectedTrader.botDetectionSignals.map((signal, i) => (
                            <div key={i} className="flex items-center gap-2 bg-[#111827] rounded px-2 py-1">
                              <Zap className="h-3 w-3 text-red-400 shrink-0" />
                              <span className="text-[10px] font-mono text-[#94a3b8]">{signal}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Behavioral Patterns */}
                <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
                  <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Behavioral Patterns</span>
                  <div className="space-y-2 mt-2">
                    {selectedTrader.behaviorPatterns.map(pattern => (
                      <div key={pattern.id} className="bg-[#111827] rounded p-2">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-[10px] font-mono text-[#e2e8f0] font-bold">
                            {pattern.pattern.replace(/_/g, ' ')}
                          </span>
                          <span className="mono-data text-[9px] text-[#64748b]">{pattern.dataPoints} data pts</span>
                        </div>
                        <ConfidenceBar value={pattern.confidence} />
                      </div>
                    ))}
                  </div>
                </div>

                {/* Trading Hours Heatmap */}
                <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
                  <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Trading Hours Activity</span>
                  <div className="h-28 mt-2">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={tradingHoursData}>
                        <XAxis
                          dataKey="hour"
                          tick={{ fontSize: 8, fill: '#475569', fontFamily: 'monospace' }}
                          axisLine={{ stroke: '#1e293b' }}
                          tickLine={{ stroke: '#1e293b' }}
                          interval={2}
                        />
                        <YAxis hide />
                        <Tooltip content={<DarkTooltip />} />
                        <Bar dataKey="trades" fill="#06b6d4" radius={[2, 2, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  {selectedTrader.isActive247 && (
                    <div className="flex items-center gap-1 mt-1">
                      <Clock className="h-3 w-3 text-red-400" />
                      <span className="text-[9px] font-mono text-red-400">24/7 Activity Detected</span>
                    </div>
                  )}
                </div>

                {/* Cross-Chain Links */}
                {selectedTrader.crossChainLinks.length > 0 && (
                  <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
                    <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Cross-Chain Links</span>
                    <div className="space-y-1.5 mt-2">
                      {selectedTrader.crossChainLinks.map((link, i) => (
                        <div key={i} className="flex items-center gap-2 bg-[#111827] rounded px-2 py-1.5">
                          <Link2 className="h-3 w-3 text-[#06b6d4] shrink-0" />
                          <span className="mono-data text-[10px]" style={{ color: CHAIN_COLORS[link.linkedChain] || '#94a3b8' }}>
                            {link.linkedChain}
                          </span>
                          <span className="mono-data text-[10px] text-[#64748b]">{shortAddress(link.linkedAddress)}</span>
                          <Badge variant="outline" className="text-[8px] h-4 px-1 font-mono border-[#2d3748] text-[#94a3b8] ml-auto">
                            {link.linkType.replace(/_/g, ' ')}
                          </Badge>
                          <span className="mono-data text-[9px] text-[#64748b]">{(link.linkConfidence * 100).toFixed(0)}%</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Recent Transactions */}
                <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
                  <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Recent Transactions</span>
                  <div className="mt-2 space-y-1">
                    {selectedTrader.transactions.map(tx => (
                      <div key={tx.id} className="flex items-center gap-2 bg-[#111827] rounded px-2 py-1.5">
                        <Badge className={`text-[8px] h-4 px-1 font-mono font-bold ${
                          tx.action === 'BUY' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' :
                          'bg-red-500/20 text-red-400 border-red-500/30'
                        }`}>
                          {tx.action}
                        </Badge>
                        <span className="mono-data text-[10px] text-[#e2e8f0] font-bold">{tx.tokenSymbol || '???'}</span>
                        <span className="mono-data text-[10px] text-[#94a3b8]">{formatUsd(tx.valueUsd)}</span>
                        {tx.pnlUsd != null && (
                          <span className={`mono-data text-[10px] ${tx.pnlUsd >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {tx.pnlUsd >= 0 ? '+' : ''}{formatUsd(tx.pnlUsd)}
                          </span>
                        )}
                        <div className="ml-auto flex items-center gap-1">
                          {tx.isFrontrun && <Zap className="h-3 w-3 text-red-400" />}
                          {tx.isSandwich && <Shield className="h-3 w-3 text-orange-400" />}
                          <span className="mono-data text-[9px] text-[#475569]">{timeAgo(tx.blockTime)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </ScrollArea>
          )}
        </div>
      </div>

      {/* ========== BOT ACTIVITY SUMMARY (BOTTOM SECTION) ========== */}
      <div className="px-3 pb-2 shrink-0">
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-red-400" />
              <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Bot Activity Summary</span>
            </div>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] font-mono text-[#64748b]">MEV Extracted</span>
                <span className="mono-data text-xs text-red-400 font-bold">{formatUsd(botStats?.totalMevExtracted || 0)}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] font-mono text-[#64748b]">Frontruns</span>
                <span className="mono-data text-xs text-orange-400 font-bold">{(botStats?.totalFrontruns || 0).toLocaleString()}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] font-mono text-[#64748b]">Sandwiches</span>
                <span className="mono-data text-xs text-yellow-400 font-bold">{(botStats?.totalSandwiches || 0).toLocaleString()}</span>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            {/* Bot Type Distribution Pie */}
            <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-2">
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Bot Type Distribution</span>
              <div className="flex items-center gap-3 mt-1">
                <div className="w-28 h-28 shrink-0">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={botPieData ?? []}
                        cx="50%"
                        cy="50%"
                        innerRadius={28}
                        outerRadius={50}
                        dataKey="value"
                        stroke="none"
                      >
                        {(botPieData ?? []).map((entry, index) => (
                          <Cell key={`bot-cell-${index}`} fill={entry.color} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex-1 space-y-0.5 min-w-0">
                  {(botPieData ?? []).map((entry, i) => (
                    <div key={i} className="flex items-center gap-1.5">
                      <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: entry.color }} />
                      <span className="text-[9px] font-mono text-[#94a3b8] truncate">{entry.name}</span>
                      <span className="mono-data text-[9px] ml-auto shrink-0" style={{ color: entry.color }}>{entry.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Bot Activity by Chain Bar */}
            <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-2">
              <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Bot Activity by Chain</span>
              <div className="h-28 mt-1">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={botBarData ?? []}>
                    <XAxis
                      dataKey="name"
                      tick={{ fontSize: 9, fill: '#475569', fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickLine={{ stroke: '#1e293b' }}
                    />
                    <YAxis hide />
                    <Tooltip content={<DarkTooltip />} />
                    <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                      {(botBarData ?? []).map((entry, index) => (
                        <Cell key={`chain-bar-${index}`} fill={CHAIN_COLORS[entry.name] || '#94a3b8'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
