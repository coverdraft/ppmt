'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  Cell,
} from 'recharts';
import {
  Trophy,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  ArrowUpDown,
  Zap,
  Target,
  Loader2,
  BarChart3,
  Flame,
  Eye,
  Radio,
  ChevronUp,
  ChevronDown,
} from 'lucide-react';
// ============================================================
// LOCAL TYPES
// ============================================================

interface AlphaRankingData {
  tokenId: string;
  symbol: string;
  alphaScore: number;
  category: string;
  momentum: number;
  signal: string;
  chain: string;
  direction: string;
  confidence: number;
  expectedReturn: number;
  rank: number;
}

// ============================================================
// CONSTANTS & HELPERS
// ============================================================

const CATEGORIES = ['DeFi', 'L1', 'L2', 'Meme', 'AI', 'Gaming', 'RWA', 'Infrastructure'] as const;
const CATEGORY_COLORS: Record<string, string> = {
  DeFi: '#10b981',
  L1: '#3b82f6',
  L2: '#8b5cf6',
  Meme: '#f59e0b',
  AI: '#ec4899',
  Gaming: '#f97316',
  RWA: '#06b6d4',
  Infrastructure: '#64748b',
};

const SIGNAL_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  BULLISH: TrendingUp,
  BEARISH: TrendingDown,
  NEUTRAL: ArrowUpDown,
};

function getAlphaColor(score: number): string {
  if (score > 70) return 'text-emerald-400';
  if (score >= 40) return 'text-yellow-400';
  return 'text-red-400';
}

function getAlphaBg(score: number): string {
  if (score > 70) return 'bg-emerald-500';
  if (score >= 40) return 'bg-yellow-500';
  return 'bg-red-500';
}

function getAlphaCellBg(score: number): string {
  if (score > 80) return '#10b981';
  if (score > 70) return '#22c55e';
  if (score > 60) return '#84cc16';
  if (score > 50) return '#eab308';
  if (score > 40) return '#f59e0b';
  if (score > 30) return '#f97316';
  return '#ef4444';
}

function getCategoryForToken(symbol: string, chain: string): string {
  const s = symbol.toUpperCase();
  if (['UNI', 'AAVE', 'MKR', 'COMP', 'CRV', 'SUSHI', 'DYDX', 'SNX', '1INCH', 'LDO', 'JUP', 'RAY', 'ORCA'].includes(s)) return 'DeFi';
  if (['ETH', 'SOL', 'BTC', 'BNB', 'AVAX', 'MATIC', 'ADA', 'DOT', 'ATOM', 'NEAR', 'APT', 'SUI', 'SEI'].includes(s)) return 'L1';
  if (['ARB', 'OP', 'MATIC', 'STRK', 'MANTA', 'BLAST', 'METIS', 'IMX'].includes(s)) return 'L2';
  if (['DOGE', 'SHIB', 'PEPE', 'FLOKI', 'WIF', 'BONK', 'BOME', 'MEME', 'POPCAT', 'WEN'].includes(s)) return 'Meme';
  if (['FET', 'AGIX', 'RENDER', 'AKT', 'TAO', 'OCEAN', 'RNERO', 'FLOCK'].includes(s)) return 'AI';
  if (['IMX', 'GALA', 'MANA', 'SAND', 'AXS', 'YGG', 'MAGIC', 'PORTAL'].includes(s)) return 'Gaming';
  if (['ONDO', 'MPL', 'POLYX', 'TRU', 'CFG'].includes(s)) return 'RWA';
  return 'Infrastructure';
}

function formatCompactAddress(addr: string): string {
  if (!addr || addr.length < 12) return addr;
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

// Sort indicator — defined outside render to avoid React static-components rule
function SortIndicator({ field, sortField, sortDir }: { field: string; sortField: string; sortDir: 'asc' | 'desc' }) {
  return (
    <span className="inline-flex flex-col ml-0.5">
      {sortField === field ? (
        sortDir === 'desc' ? (
          <ChevronDown className="h-2.5 w-2.5 text-cyan-400" />
        ) : (
          <ChevronUp className="h-2.5 w-2.5 text-cyan-400" />
        )
      ) : (
        <ArrowUpDown className="h-2.5 w-2.5 text-[#475569]" />
      )}
    </span>
  );
}

// ============================================================
// API RESPONSE TYPE (from /api/alpha/ranking)
// ============================================================

interface ApiAlphaRanking {
  tokenAddress: string;
  chain: string;
  direction: string;
  confidence: number;
  strategyName: string;
  expectedReturn: number;
  expectedVol: number;
  operabilityScore: number;
  regimeFit: number;
  tokenPhase?: string;
  regime?: string;
  liquidityUsd?: number;
  volume24h?: number;
  alphaScore: number;
  rank: number;
  scoreBreakdown: {
    signalStrength: number;
    riskAdjustedReturn: number;
    operability: number;
    portfolioFit: number;
    regimeAlignment: number;
    composite: number;
  };
  suggestedAllocationPct: number;
}

// ============================================================
// COMPONENT
// ============================================================

export function AlphaRankingPanel() {
  const [selectedTokenId, setSelectedTokenId] = useState<string | null>(null);
  const [sortField, setSortField] = useState<string>('alphaScore');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Fetch data with 120s auto-refresh
  const { data: apiData, isLoading, refetch } = useQuery({
    queryKey: ['alpha-ranking'],
    queryFn: async () => {
      const res = await fetch('/api/alpha/ranking?n=20');
      if (!res.ok) throw new Error('Failed to fetch alpha ranking');
      const json = await res.json();
      return json.data as ApiAlphaRanking[] | null;
    },
    refetchInterval: 120000,
    staleTime: 60000,
  });

  // Transform API data into store format
  const transformedRankings: AlphaRankingData[] = useMemo(() => {
    if (!apiData || !Array.isArray(apiData)) return [];

    return apiData.map((item) => {
      const symbol = item.tokenAddress
        ? item.tokenAddress.slice(0, 4).toUpperCase()
        : '???';
      const category = getCategoryForToken(symbol, item.chain);
      const momentum = Math.round(item.scoreBreakdown.riskAdjustedReturn * 100);
      const signal =
        item.direction === 'LONG'
          ? 'BULLISH'
          : item.direction === 'SHORT'
            ? 'BEARISH'
            : 'NEUTRAL';

      return {
        tokenId: item.tokenAddress,
        symbol,
        alphaScore: Math.round(item.alphaScore * 100),
        category,
        momentum,
        signal,
        chain: item.chain,
        direction: item.direction,
        confidence: Math.round(item.confidence * 100),
        expectedReturn: Math.round(item.expectedReturn * 10000) / 100,
        rank: item.rank,
      };
    });
  }, [apiData]);

  const rankings = transformedRankings;

  // Sort handler
  const handleSort = useCallback((field: string) => {
    setSortDir((prev) =>
      sortField === field ? (prev === 'asc' ? 'desc' : 'asc') : 'desc'
    );
    setSortField(field);
  }, [sortField]);

  // Sorted rankings
  const sortedRankings = useMemo(() => {
    const sorted = [...rankings].sort((a, b) => {
      const aVal = (a as any)[sortField] ?? 0;
      const bVal = (b as any)[sortField] ?? 0;
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortDir === 'desc' ? bVal - aVal : aVal - bVal;
      }
      return sortDir === 'desc'
        ? String(bVal).localeCompare(String(aVal))
        : String(aVal).localeCompare(String(bVal));
    });
    return sorted;
  }, [rankings, sortField, sortDir]);

  // Selected token detail
  const selectedToken = useMemo(() => {
    if (!selectedTokenId) return null;
    return rankings.find((r) => r.tokenId === selectedTokenId) || null;
  }, [rankings, selectedTokenId]);

  // Heatmap data grouped by category
  const heatmapData = useMemo(() => {
    const grouped: Record<string, AlphaRankingData[]> = {};
    for (const r of rankings) {
      if (!grouped[r.category]) grouped[r.category] = [];
      grouped[r.category].push(r);
    }
    return grouped;
  }, [rankings]);

  // Category distribution bar chart data
  const categoryDistData = useMemo(() => {
    const catScores: Record<string, { total: number; count: number }> = {};
    for (const r of rankings) {
      if (!catScores[r.category]) catScores[r.category] = { total: 0, count: 0 };
      catScores[r.category].total += r.alphaScore;
      catScores[r.category].count++;
    }
    return Object.entries(catScores)
      .map(([cat, { total, count }]) => ({
        category: cat,
        avgAlpha: Math.round(total / count),
        count,
        color: CATEGORY_COLORS[cat] || '#64748b',
      }))
      .sort((a, b) => b.avgAlpha - a.avgAlpha);
  }, [rankings]);

  // Alpha trend for selected token (simulated 7-day)
  const alphaTrendData = useMemo(() => {
    if (!selectedToken) return [];
    const points: Array<{ day: string; score: number }> = [];
    for (let i = 6; i >= 0; i--) {
      const date = new Date();
      date.setDate(date.getDate() - i);
      const label = date.toLocaleDateString('en-US', { weekday: 'short' });
      const jitter = Math.sin(i * 1.3) * 8;
      points.push({
        day: label,
        score: Math.max(0, Math.min(100, selectedToken.alphaScore + jitter)),
      });
    }
    return points;
  }, [selectedToken]);



  // Loading state
  if (isLoading && rankings.length === 0) {
    return (
      <div className="flex items-center justify-center h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg">
        <div className="flex flex-col items-center gap-3">
          <Trophy className="h-8 w-8 text-[#d4af37] animate-pulse" />
          <span className="text-[#64748b] font-mono text-sm">Loading Alpha Rankings...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-[#1e293b] bg-[#0d1117] p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Trophy className="h-4 w-4 text-[#d4af37]" />
            <span className="text-sm font-mono font-bold text-[#f1f5f9]">Alpha Ranking</span>
            <span className="text-[9px] font-mono text-[#64748b]">
              TOP {rankings.length} OPPORTUNITIES
            </span>
            <span className="text-[8px] font-mono text-[#475569]">Auto-refresh: 120s</span>
          </div>
          <button
            onClick={() => refetch()}
            className="flex items-center justify-center w-7 h-7 bg-[#1e293b]/50 border border-[#1e293b] rounded hover:bg-[#1e293b] transition-colors"
          >
            <RefreshCw className="h-3 w-3 text-[#94a3b8]" />
          </button>
        </div>
      </div>

      {/* Scrollable Content */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {/* Top Alpha Table */}
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Target className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[10px] font-mono text-[#d4af37] uppercase tracking-wider font-bold">
              Top Alpha Opportunities
            </span>
          </div>
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-md overflow-hidden">
            <div className="overflow-x-auto max-h-64 overflow-y-auto">
              <table className="w-full text-[10px] font-mono">
                <thead>
                  <tr className="border-b border-[#1e293b] bg-[#0a0e17]">
                    <th className="px-2 py-2 text-left text-[#64748b] uppercase tracking-wider cursor-pointer" onClick={() => handleSort('rank')}>
                      Rank <SortIndicator field="rank" sortField={sortField} sortDir={sortDir} />
                    </th>
                    <th className="px-2 py-2 text-left text-[#64748b] uppercase tracking-wider cursor-pointer" onClick={() => handleSort('symbol')}>
                      Token <SortIndicator field="symbol" sortField={sortField} sortDir={sortDir} />
                    </th>
                    <th className="px-2 py-2 text-left text-[#64748b] uppercase tracking-wider cursor-pointer" onClick={() => handleSort('alphaScore')}>
                      Alpha Score <SortIndicator field="alphaScore" sortField={sortField} sortDir={sortDir} />
                    </th>
                    <th className="px-2 py-2 text-left text-[#64748b] uppercase tracking-wider cursor-pointer" onClick={() => handleSort('category')}>
                      Category <SortIndicator field="category" sortField={sortField} sortDir={sortDir} />
                    </th>
                    <th className="px-2 py-2 text-left text-[#64748b] uppercase tracking-wider cursor-pointer" onClick={() => handleSort('momentum')}>
                      Momentum <SortIndicator field="momentum" sortField={sortField} sortDir={sortDir} />
                    </th>
                    <th className="px-2 py-2 text-left text-[#64748b] uppercase tracking-wider cursor-pointer" onClick={() => handleSort('signal')}>
                      Signal <SortIndicator field="signal" sortField={sortField} sortDir={sortDir} />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedRankings.map((token) => {
                    const isSelected = selectedTokenId === token.tokenId;
                    const SignalIcon = SIGNAL_ICONS[token.signal] || ArrowUpDown;
                    return (
                      <tr
                        key={token.tokenId}
                        onClick={() => setSelectedTokenId(isSelected ? null : token.tokenId)}
                        className={`border-b border-[#1e293b]/50 cursor-pointer transition-colors ${
                          isSelected
                            ? 'bg-cyan-500/10 border-l-2 border-l-cyan-500'
                            : 'hover:bg-[#1e293b]/20 border-l-2 border-l-transparent'
                        }`}
                      >
                        <td className="px-2 py-2">
                          <span className={`font-bold ${
                            token.rank <= 3 ? 'text-[#d4af37]' : 'text-[#94a3b8]'
                          }`}>
                            #{token.rank}
                          </span>
                        </td>
                        <td className="px-2 py-2">
                          <div className="flex items-center gap-1.5">
                            <div
                              className="w-5 h-5 rounded-full flex items-center justify-center text-[7px] font-bold"
                              style={{
                                backgroundColor: `${CATEGORY_COLORS[token.category] || '#64748b'}22`,
                                color: CATEGORY_COLORS[token.category] || '#64748b',
                                border: `1px solid ${CATEGORY_COLORS[token.category] || '#64748b'}44`,
                              }}
                            >
                              {token.symbol.charAt(0)}
                            </div>
                            <span className="text-[#e2e8f0] font-bold">{token.symbol}</span>
                            <span className="text-[8px] text-[#475569]">{formatCompactAddress(token.tokenId)}</span>
                          </div>
                        </td>
                        <td className="px-2 py-2">
                          <div className="flex items-center gap-1.5">
                            <div className="w-12 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                              <div
                                className={`h-full rounded-full ${getAlphaBg(token.alphaScore)}`}
                                style={{ width: `${token.alphaScore}%` }}
                              />
                            </div>
                            <span className={`${getAlphaColor(token.alphaScore)} font-bold`}>
                              {token.alphaScore}
                            </span>
                          </div>
                        </td>
                        <td className="px-2 py-2">
                          <span
                            className="px-1.5 py-0.5 rounded text-[8px] font-bold"
                            style={{
                              backgroundColor: `${CATEGORY_COLORS[token.category] || '#64748b'}15`,
                              color: CATEGORY_COLORS[token.category] || '#64748b',
                              border: `1px solid ${CATEGORY_COLORS[token.category] || '#64748b'}33`,
                            }}
                          >
                            {token.category}
                          </span>
                        </td>
                        <td className="px-2 py-2">
                          <div className="flex items-center gap-1">
                            {token.momentum > 0 ? (
                              <TrendingUp className="h-3 w-3 text-emerald-400" />
                            ) : token.momentum < 0 ? (
                              <TrendingDown className="h-3 w-3 text-red-400" />
                            ) : (
                              <ArrowUpDown className="h-3 w-3 text-gray-400" />
                            )}
                            <span className={token.momentum > 0 ? 'text-emerald-400' : token.momentum < 0 ? 'text-red-400' : 'text-gray-400'}>
                              {token.momentum > 0 ? '+' : ''}{token.momentum}
                            </span>
                          </div>
                        </td>
                        <td className="px-2 py-2">
                          <div className="flex items-center gap-1">
                            <SignalIcon className={`h-3 w-3 ${
                              token.signal === 'BULLISH' ? 'text-emerald-400' :
                              token.signal === 'BEARISH' ? 'text-red-400' : 'text-gray-400'
                            }`} />
                            <span className={`font-bold ${
                              token.signal === 'BULLISH' ? 'text-emerald-400' :
                              token.signal === 'BEARISH' ? 'text-red-400' : 'text-gray-400'
                            }`}>
                              {token.signal}
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {/* Alpha Heatmap */}
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Flame className="h-3 w-3 text-orange-400" />
              <span className="text-[10px] font-mono text-orange-400 uppercase tracking-wider font-bold">
                Alpha Heatmap by Category
              </span>
            </div>
            <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
              <div className="space-y-2">
                {Object.entries(heatmapData).map(([category, tokens]) => (
                  <div key={category}>
                    <div className="flex items-center gap-1.5 mb-1">
                      <div
                        className="w-2 h-2 rounded-full"
                        style={{ backgroundColor: CATEGORY_COLORS[category] || '#64748b' }}
                      />
                      <span className="text-[9px] font-mono font-bold" style={{ color: CATEGORY_COLORS[category] || '#64748b' }}>
                        {category}
                      </span>
                      <span className="text-[8px] font-mono text-[#475569]">({tokens.length})</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {tokens.map((token) => (
                        <div
                          key={token.tokenId}
                          onClick={() => setSelectedTokenId(token.tokenId)}
                          className={`px-2 py-1 rounded cursor-pointer transition-all text-[9px] font-mono font-bold ${
                            selectedTokenId === token.tokenId
                              ? 'ring-1 ring-white/30 scale-105'
                              : 'hover:scale-105'
                          }`}
                          style={{
                            backgroundColor: `${getAlphaCellBg(token.alphaScore)}22`,
                            color: getAlphaCellBg(token.alphaScore),
                            border: `1px solid ${getAlphaCellBg(token.alphaScore)}44`,
                          }}
                          title={`${token.symbol}: ${token.alphaScore}`}
                        >
                          {token.symbol}
                          <span className="ml-1 opacity-70">{token.alphaScore}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                {Object.keys(heatmapData).length === 0 && (
                  <div className="text-center py-6 text-[#475569] font-mono text-[10px]">
                    No ranking data available
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Category Distribution */}
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <BarChart3 className="h-3 w-3 text-cyan-400" />
              <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider font-bold">
                Category Distribution
              </span>
            </div>
            <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
              <div className="h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={categoryDistData} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis
                      type="number"
                      domain={[0, 100]}
                      tick={{ fill: '#64748b', fontSize: 9, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickLine={{ stroke: '#1e293b' }}
                    />
                    <YAxis
                      type="category"
                      dataKey="category"
                      width={80}
                      tick={{ fill: '#94a3b8', fontSize: 9, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickLine={{ stroke: '#1e293b' }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: '#111827',
                        border: '1px solid #1e293b',
                        borderRadius: '8px',
                        fontSize: '10px',
                        fontFamily: 'monospace',
                      }}
                      formatter={(value: number, _name: string, entry: any) => [
                        `Avg Alpha: ${value} (${entry.payload?.count || 0} tokens)`,
                      ]}
                    />
                    <Bar dataKey="avgAlpha" radius={[0, 4, 4, 0]}>
                      {categoryDistData.map((entry, i) => (
                        <Cell key={`cell-${i}`} fill={entry.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>

        {/* Alpha Trend for Selected Token */}
        {selectedToken && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Radio className="h-3 w-3 text-emerald-400" />
              <span className="text-[10px] font-mono text-emerald-400 uppercase tracking-wider font-bold">
                Alpha Trend — {selectedToken.symbol}
              </span>
              <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border ${
                selectedToken.signal === 'BULLISH'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                  : selectedToken.signal === 'BEARISH'
                    ? 'bg-red-500/10 text-red-400 border-red-500/30'
                    : 'bg-gray-500/10 text-gray-400 border-gray-500/30'
              }`}>
                {selectedToken.signal}
              </span>
            </div>
            <div className="bg-[#0d1117] border border-[#1e293b] rounded-md p-3">
              {/* Token details */}
              <div className="flex items-center gap-4 mb-3 pb-2 border-b border-[#1e293b]">
                <div className="text-center">
                  <div className="text-[8px] font-mono text-[#64748b] uppercase">Alpha</div>
                  <div className={`text-lg font-mono font-bold ${getAlphaColor(selectedToken.alphaScore)}`}>
                    {selectedToken.alphaScore}
                  </div>
                </div>
                <div className="text-center">
                  <div className="text-[8px] font-mono text-[#64748b] uppercase">Momentum</div>
                  <div className={`text-lg font-mono font-bold ${selectedToken.momentum > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {selectedToken.momentum > 0 ? '+' : ''}{selectedToken.momentum}
                  </div>
                </div>
                <div className="text-center">
                  <div className="text-[8px] font-mono text-[#64748b] uppercase">Confidence</div>
                  <div className="text-lg font-mono font-bold text-[#e2e8f0]">
                    {selectedToken.confidence}%
                  </div>
                </div>
                <div className="text-center">
                  <div className="text-[8px] font-mono text-[#64748b] uppercase">Chain</div>
                  <div className="text-sm font-mono font-bold text-[#94a3b8]">
                    {selectedToken.chain}
                  </div>
                </div>
                <div className="text-center">
                  <div className="text-[8px] font-mono text-[#64748b] uppercase">Exp. Return</div>
                  <div className="text-lg font-mono font-bold text-emerald-400">
                    +{selectedToken.expectedReturn}%
                  </div>
                </div>
              </div>
              {/* 7-day trend chart */}
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={alphaTrendData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis
                      dataKey="day"
                      tick={{ fill: '#64748b', fontSize: 9, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickLine={{ stroke: '#1e293b' }}
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fill: '#64748b', fontSize: 9, fontFamily: 'monospace' }}
                      axisLine={{ stroke: '#1e293b' }}
                      tickLine={{ stroke: '#1e293b' }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: '#111827',
                        border: '1px solid #1e293b',
                        borderRadius: '8px',
                        fontSize: '10px',
                        fontFamily: 'monospace',
                      }}
                      formatter={(value: number) => [`${value}`, 'Alpha Score']}
                    />
                    <Line
                      type="monotone"
                      dataKey="score"
                      stroke={CATEGORY_COLORS[selectedToken.category] || '#06b6d4'}
                      strokeWidth={2}
                      dot={{ r: 3, fill: CATEGORY_COLORS[selectedToken.category] || '#06b6d4' }}
                      name="Alpha Score"
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        )}

        {/* Empty state when no data */}
        {rankings.length === 0 && !isLoading && (
          <div className="flex flex-col items-center justify-center py-12 text-[#475569]">
            <Eye className="h-8 w-8 mb-2" />
            <span className="font-mono text-sm">No alpha rankings available</span>
            <span className="font-mono text-[10px] mt-1">Start the Brain to generate signals</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default AlphaRankingPanel;
