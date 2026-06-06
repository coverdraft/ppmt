'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Card,
  CardContent,
  CardHeader,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Loader2,
  Layers,
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownRight,
  BarChart3,
  Trophy,
  Zap,
  DollarSign,
  Activity,
  Database,
  RefreshCw,
} from 'lucide-react';
import { formatVolume, formatPrice, formatPct } from '@/lib/format';
import { ChainHeatmap } from './chain-heatmap';

// ============================================================
// TYPES
// ============================================================

interface ChainSummary {
  tokenCount: number;
  totalVolume24h: number;
  avgPriceChange24h: number;
  topGainer: { symbol: string; priceChange24h: number; priceUsd: number } | null;
  topLoser: { symbol: string; priceChange24h: number; priceUsd: number } | null;
}

interface CrossChainToken {
  symbol: string;
  chains: string[];
  priceByChain: Record<string, number>;
  volumeByChain: Record<string, number>;
  priceDeviationPct: number;
}

interface TopTokenEntry {
  symbol: string;
  name: string;
  priceUsd: number;
  volume24h: number;
  priceChange24h: number;
  marketCap: number;
  chain: string;
}

interface ChainRankingEntry {
  chain: string;
  rank: number;
  totalVolume24h: number;
  avgChange24h: number;
  tokenCount: number;
  topTokens: TopTokenEntry[];
}

interface MultiChainData {
  chainSummary: Record<string, ChainSummary>;
  crossChainTokens: CrossChainToken[];
  topTokensByChain: Record<string, TopTokenEntry[]>;
  chainRanking: ChainRankingEntry[];
}

// ============================================================
// CHAIN META
// ============================================================

const CHAIN_META: Record<string, { emoji: string; hex: string }> = {
  SOL:   { emoji: '◎', hex: '#9945FF' },
  ETH:   { emoji: 'Ξ', hex: '#627EEA' },
  BASE:  { emoji: '🔵', hex: '#0052FF' },
  BSC:   { emoji: '🔶', hex: '#F3BA2F' },
  MATIC: { emoji: '🟣', hex: '#8247E5' },
  ARB:   { emoji: '🔷', hex: '#28A0F0' },
  OP:    { emoji: '🔴', hex: '#FF0420' },
  AVAX:  { emoji: '🔺', hex: '#E84142' },
};

type MetricType = 'volume' | 'marketCap' | 'tokenCount' | 'avgChange';

// ============================================================
// SPARKLINE MINI CHART
// ============================================================

function MiniSparkline({ data, color, width = 60, height = 20 }: {
  data: number[];
  color: string;
  width?: number;
  height?: number;
}) {
  if (data.length < 2) return null;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 2) - 1;
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={width} height={height} className="inline-block">
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={points} />
    </svg>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function MultiChainDashboard() {
  const [selectedChains, setSelectedChains] = useState<string[]>([]);
  const [comparisonMetric, setComparisonMetric] = useState<MetricType>('volume');
  const [isSeeding, setIsSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);

  // Fetch multi-chain data
  const { data: apiResponse, isLoading, isError, refetch } = useQuery({
    queryKey: ['multi-chain', selectedChains.join(',')],
    queryFn: async () => {
      const chainsParam = selectedChains.length > 0 ? selectedChains.join(',').toLowerCase() : '';
      const url = `/api/market/multi-chain?chains=${chainsParam}&topN=10&includeCrossChain=true`;
      const res = await fetch(url);
      if (!res.ok) throw new Error('Failed to fetch');
      const json = await res.json();
      return json.data as MultiChainData | null;
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const data = apiResponse;

  // Derived: available chains from data
  const availableChains = useMemo(() => {
    if (!data?.chainSummary) return Object.keys(CHAIN_META);
    return Object.keys(data.chainSummary);
  }, [data]);

  // Derived: filtered data based on selected chains
  const filteredData = useMemo(() => {
    if (!data) return null;
    if (selectedChains.length === 0) return data;

    const chainSummary: Record<string, ChainSummary> = {};
    const topTokensByChain: Record<string, TopTokenEntry[]> = {};
    let crossChainTokens = data.crossChainTokens;
    let chainRanking = data.chainRanking;

    for (const chain of selectedChains) {
      if (data.chainSummary[chain]) chainSummary[chain] = data.chainSummary[chain];
      if (data.topTokensByChain[chain]) topTokensByChain[chain] = data.topTokensByChain[chain];
    }

    // Re-filter cross chain tokens
    crossChainTokens = crossChainTokens.filter(t =>
      t.chains.some(c => selectedChains.includes(c))
    );

    // Re-filter chain ranking
    chainRanking = chainRanking
      .filter(r => selectedChains.includes(r.chain))
      .map((r, i) => ({ ...r, rank: i + 1 }));

    return { chainSummary, crossChainTokens, topTokensByChain, chainRanking };
  }, [data, selectedChains]);

  // Toggle chain filter
  const toggleChain = useCallback((chain: string) => {
    setSelectedChains(prev =>
      prev.includes(chain) ? prev.filter(c => c !== chain) : [...prev, chain]
    );
  }, []);

  const clearFilters = useCallback(() => setSelectedChains([]), []);

  // Seed handler
  const handleSeed = useCallback(async () => {
    setIsSeeding(true);
    setSeedError(null);
    try {
      const res = await fetch('/api/seed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'tokens' }),
      });
      if (!res.ok) {
        const json = await res.json().catch(() => null);
        throw new Error(json?.error || 'Seed failed');
      }
      // Wait a bit for DB writes to settle, then refetch
      await new Promise(r => setTimeout(r, 2000));
      await refetch();
    } catch (err) {
      setSeedError(err instanceof Error ? err.message : 'Seed failed');
    } finally {
      setIsSeeding(false);
    }
  }, [refetch]);

  // ============================================================
  // COMPUTE COMPARISON BAR CHART DATA
  // ============================================================

  const barChartData = useMemo(() => {
    if (!filteredData) return [];
    const entries: { chain: string; value: number; label: string }[] = [];

    for (const [chain, summary] of Object.entries(filteredData.chainSummary)) {
      let value = 0;
      let label = '';
      switch (comparisonMetric) {
        case 'volume':
          value = summary.totalVolume24h;
          label = formatVolume(value);
          break;
        case 'marketCap':
          // Sum top tokens market cap as proxy
          const tokens = filteredData.topTokensByChain[chain] || [];
          value = tokens.reduce((s, t) => s + t.marketCap, 0);
          label = formatVolume(value);
          break;
        case 'tokenCount':
          value = summary.tokenCount;
          label = `${value}`;
          break;
        case 'avgChange':
          value = summary.avgPriceChange24h;
          label = formatPct(value);
          break;
      }
      entries.push({ chain, value, label });
    }

    // Sort by value descending
    entries.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
    return entries;
  }, [filteredData, comparisonMetric]);

  const maxBarValue = useMemo(() => {
    if (barChartData.length === 0) return 1;
    return Math.max(...barChartData.map(d => Math.abs(d.value)), 1);
  }, [barChartData]);

  // ============================================================
  // RENDER
  // ============================================================

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0d1117] border border-[#1e293b] rounded-lg">
        <Loader2 className="h-8 w-8 text-[#d4af37] animate-spin mb-3" />
        <span className="font-mono text-[11px] text-[#64748b]">Loading multi-chain data...</span>
      </div>
    );
  }

  if (isError || !filteredData) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0d1117] border border-[#1e293b] rounded-lg gap-3 px-4">
        <Layers className="h-8 w-8 text-[#475569]" />
        <span className="font-mono text-[11px] text-[#64748b]">Unable to load multi-chain data</span>
        <span className="font-mono text-[9px] text-[#475569]">The database may be empty or the API is unreachable</span>
        <Button
          onClick={handleSeed}
          disabled={isSeeding}
          className="mt-2 bg-[#3b82f6] hover:bg-[#3b82f6]/80 text-white font-mono text-[11px] h-8 px-4"
        >
          {isSeeding ? (
            <><Loader2 className="h-3 w-3 mr-1.5 animate-spin" /> Seeding...</>
          ) : (
            <><Database className="h-3 w-3 mr-1.5" /> Load Data</>
          )}
        </Button>
        {seedError && (
          <span className="font-mono text-[9px] text-red-400">{seedError}</span>
        )}
      </div>
    );
  }

  // Check if data is sparse (fewer than 3 chains with tokens)
  const chainsWithTokens = Object.values(filteredData.chainSummary).filter(s => s.tokenCount > 0).length;
  const totalTokens = Object.values(filteredData.chainSummary).reduce((s, c) => s + c.tokenCount, 0);
  const isDataSparse = chainsWithTokens < 3 || totalTokens < 10;

  return (
    <div className="flex flex-col h-full overflow-y-auto custom-scrollbar">
      {/* ============================================================ */}
      {/* HEADER + CHAIN FILTER PILLS */}
      {/* ============================================================ */}
      <div className="px-4 pt-3 pb-2 border-b border-[#1e293b] bg-[#0a0e17] shrink-0">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-[#d4af37]" />
            <h2 className="text-sm font-mono font-bold text-[#f1f5f9]">Multi-Chain Dashboard</h2>
            <span className="font-mono text-[9px] text-[#475569]">
              {availableChains.length} chains · {totalTokens} tokens
            </span>
          </div>
          <div className="flex items-center gap-2">
            {isDataSparse && (
              <Button
                onClick={handleSeed}
                disabled={isSeeding}
                className="bg-[#3b82f6] hover:bg-[#3b82f6]/80 text-white font-mono text-[9px] h-5 px-2"
              >
                {isSeeding ? (
                  <><Loader2 className="h-2.5 w-2.5 mr-1 animate-spin" /> Seeding...</>
                ) : (
                  <><Database className="h-2.5 w-2.5 mr-1" /> Load Data</>
                )}
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refetch()}
              className="h-5 px-2 text-[9px] font-mono text-[#64748b] hover:text-[#94a3b8]"
            >
              <RefreshCw className="h-2.5 w-2.5 mr-1" /> Refresh
            </Button>
            {selectedChains.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={clearFilters}
                className="h-6 px-2 text-[9px] font-mono text-[#64748b] hover:text-[#94a3b8]"
              >
                Clear filters
              </Button>
            )}
          </div>
        </div>

        {/* Data sparse warning */}
        {isDataSparse && !isSeeding && (
          <div className="mb-2 px-2 py-1 rounded bg-amber-500/10 border border-amber-500/20">
            <span className="font-mono text-[8px] text-amber-400">
              ⚠ Low data — only {chainsWithTokens} chains with {totalTokens} tokens. Click "Load Data" to seed the database.
            </span>
          </div>
        )}
        {seedError && (
          <div className="mb-2 px-2 py-1 rounded bg-red-500/10 border border-red-500/20">
            <span className="font-mono text-[8px] text-red-400">
              Seed error: {seedError}
            </span>
          </div>
        )}

        {/* Chain Filter Pills */}
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={clearFilters}
            className={`px-2.5 py-1 rounded-full text-[9px] font-mono font-medium transition-all border ${
              selectedChains.length === 0
                ? 'bg-[#d4af37]/20 text-[#d4af37] border-[#d4af37]/40'
                : 'bg-[#111827] text-[#64748b] border-[#1e293b] hover:border-[#374151]'
            }`}
          >
            All Chains
          </button>
          {availableChains.map(chain => {
            const meta = CHAIN_META[chain];
            const isActive = selectedChains.includes(chain);
            return (
              <button
                key={chain}
                onClick={() => toggleChain(chain)}
                className={`px-2.5 py-1 rounded-full text-[9px] font-mono font-medium transition-all border flex items-center gap-1 ${
                  isActive
                    ? `border-opacity-60`
                    : 'bg-[#111827] text-[#64748b] border-[#1e293b] hover:border-[#374151]'
                }`}
                style={isActive ? {
                  backgroundColor: `${meta.hex}15`,
                  color: meta.hex,
                  borderColor: `${meta.hex}60`,
                } : undefined}
              >
                <span>{meta.emoji}</span>
                <span>{chain}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* ============================================================ */}
      {/* A. CHAIN OVERVIEW CARDS */}
      {/* ============================================================ */}
      <div className="px-3 pt-3 pb-1">
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
          {availableChains.map(chain => {
            const summary = filteredData.chainSummary[chain];
            if (!summary) return null;
            const meta = CHAIN_META[chain];
            const isPositive = summary.avgPriceChange24h >= 0;

            return (
              <Card
                key={chain}
                className="bg-[#111827] border-[#1e293b] cursor-pointer hover:border-[#374151] transition-all group"
                onClick={() => toggleChain(chain)}
              >
                <CardHeader className="p-2 pb-1">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div
                        className="w-5 h-5 rounded-full flex items-center justify-center text-[10px]"
                        style={{ backgroundColor: `${meta.hex}20`, color: meta.hex }}
                      >
                        {meta.emoji}
                      </div>
                      <span className="font-mono text-[10px] font-bold text-[#e2e8f0]">{chain}</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={`h-4 px-1 text-[7px] font-mono border-0 ${
                        isPositive ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                      }`}
                    >
                      {isPositive ? '+' : ''}{summary.avgPriceChange24h.toFixed(1)}%
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="p-2 pt-0">
                  <div className="space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[7px] text-[#475569]">Tokens</span>
                      <span className="font-mono text-[9px] text-[#94a3b8] font-bold">{summary.tokenCount}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[7px] text-[#475569]">Volume</span>
                      <span className="font-mono text-[9px] text-[#94a3b8] font-bold">{formatVolume(summary.totalVolume24h)}</span>
                    </div>
                    {summary.topGainer && (
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-[7px] text-[#475569]">Top</span>
                        <div className="flex items-center gap-0.5">
                          <TrendingUp className="h-2.5 w-2.5 text-emerald-400" />
                          <span className="font-mono text-[8px] text-emerald-400 font-bold">{summary.topGainer.symbol}</span>
                        </div>
                      </div>
                    )}
                    {summary.topLoser && (
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-[7px] text-[#475569]">Low</span>
                        <div className="flex items-center gap-0.5">
                          <TrendingDown className="h-2.5 w-2.5 text-red-400" />
                          <span className="font-mono text-[8px] text-red-400 font-bold">{summary.topLoser.symbol}</span>
                        </div>
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>

      {/* ============================================================ */}
      {/* A2. CHAIN HEATMAP */}
      {/* ============================================================ */}
      <div className="px-3 pt-2">
        <ChainHeatmap />
      </div>

      {/* ============================================================ */}
      {/* B. CHAIN COMPARISON BAR CHART + C. CROSS-CHAIN TABLE */}
      {/* ============================================================ */}
      <div className="flex-1 flex flex-col lg:flex-row gap-2 px-3 pt-2 min-h-0">
        {/* B. Bar Chart */}
        <div className="lg:w-1/2 flex flex-col">
          <Card className="bg-[#111827] border-[#1e293b] flex-1 flex flex-col">
            <CardHeader className="p-2 pb-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <BarChart3 className="h-3 w-3 text-[#d4af37]" />
                  <span className="font-mono text-[9px] text-[#64748b] uppercase tracking-wider">Chain Comparison</span>
                </div>
                {/* Metric Toggle Pills */}
                <div className="flex gap-1">
                  {([
                    { key: 'volume' as MetricType, label: 'Volume', icon: DollarSign },
                    { key: 'marketCap' as MetricType, label: 'MCap', icon: BarChart3 },
                    { key: 'tokenCount' as MetricType, label: 'Tokens', icon: Layers },
                    { key: 'avgChange' as MetricType, label: 'Avg %', icon: Activity },
                  ]).map(m => (
                    <button
                      key={m.key}
                      onClick={() => setComparisonMetric(m.key)}
                      className={`px-1.5 py-0.5 rounded text-[7px] font-mono transition-all border ${
                        comparisonMetric === m.key
                          ? 'bg-[#d4af37]/20 text-[#d4af37] border-[#d4af37]/40'
                          : 'bg-transparent text-[#475569] border-transparent hover:text-[#64748b]'
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-2 pt-1 flex-1">
              <div className="space-y-2">
                {barChartData.map(item => {
                  const meta = CHAIN_META[item.chain];
                  const pct = maxBarValue > 0 ? (Math.abs(item.value) / maxBarValue) * 100 : 0;
                  const isNeg = item.value < 0;

                  return (
                    <div key={item.chain} className="flex items-center gap-2">
                      <div className="flex items-center gap-1 w-14 shrink-0">
                        <span className="text-[10px]">{meta?.emoji}</span>
                        <span className="font-mono text-[9px] text-[#94a3b8] font-bold">{item.chain}</span>
                      </div>
                      <div className="flex-1 h-5 bg-[#0a0e17] rounded-sm overflow-hidden relative">
                        <div
                          className="h-full rounded-sm transition-all duration-500"
                          style={{
                            width: `${pct}%`,
                            backgroundColor: isNeg ? `${meta?.hex || '#ef4444'}60` : `${meta?.hex || '#22c55e'}80`,
                          }}
                        />
                      </div>
                      <span className={`font-mono text-[9px] font-bold w-16 text-right shrink-0 ${
                        isNeg ? 'text-red-400' : 'text-[#94a3b8]'
                      }`}>
                        {item.label}
                      </span>
                    </div>
                  );
                })}
                {barChartData.length === 0 && (
                  <div className="flex items-center justify-center h-20 text-[#475569] font-mono text-[9px]">
                    No data for comparison
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* C. Cross-Chain Token Table */}
        <div className="lg:w-1/2 flex flex-col">
          <Card className="bg-[#111827] border-[#1e293b] flex-1 flex flex-col min-h-0">
            <CardHeader className="p-2 pb-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <Zap className="h-3 w-3 text-amber-400" />
                  <span className="font-mono text-[9px] text-[#64748b] uppercase tracking-wider">Cross-Chain Tokens</span>
                  <Badge variant="outline" className="h-4 px-1 text-[7px] font-mono border-[#1e293b] text-[#475569]">
                    {filteredData.crossChainTokens.length}
                  </Badge>
                </div>
                <span className="font-mono text-[7px] text-amber-400/60">Arbitrage Opportunities</span>
              </div>
            </CardHeader>
            <CardContent className="p-0 flex-1 min-h-0 overflow-y-auto custom-scrollbar">
              {filteredData.crossChainTokens.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow className="border-[#1e293b] hover:bg-transparent">
                      <TableHead className="font-mono text-[8px] text-[#475569] h-6 py-0 px-2">Symbol</TableHead>
                      <TableHead className="font-mono text-[8px] text-[#475569] h-6 py-0 px-2 text-center"># Chains</TableHead>
                      <TableHead className="font-mono text-[8px] text-[#475569] h-6 py-0 px-2">Price by Chain</TableHead>
                      <TableHead className="font-mono text-[8px] text-[#475569] h-6 py-0 px-2 text-right">Deviation</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredData.crossChainTokens.slice(0, 20).map(token => (
                      <TableRow
                        key={token.symbol}
                        className="border-[#1e293b]/50 hover:bg-[#1e293b]/20 cursor-pointer"
                      >
                        <TableCell className="py-1.5 px-2">
                          <span className="font-mono text-[10px] font-bold text-[#e2e8f0]">{token.symbol}</span>
                        </TableCell>
                        <TableCell className="py-1.5 px-2 text-center">
                          <Badge
                            variant="outline"
                            className="h-4 px-1 text-[8px] font-mono border-[#1e293b] text-[#94a3b8]"
                          >
                            {token.chains.length}
                          </Badge>
                        </TableCell>
                        <TableCell className="py-1.5 px-2">
                          <div className="flex flex-wrap gap-1">
                            {token.chains.slice(0, 4).map(chain => {
                              const meta = CHAIN_META[chain];
                              const price = token.priceByChain[chain];
                              return (
                                <div
                                  key={chain}
                                  className="flex items-center gap-0.5 rounded px-1 py-0.5"
                                  style={{ backgroundColor: `${meta?.hex || '#64748b'}10` }}
                                >
                                  <span className="text-[7px]">{meta?.emoji}</span>
                                  <span className="font-mono text-[8px]" style={{ color: meta?.hex || '#94a3b8' }}>
                                    {formatPrice(price)}
                                  </span>
                                </div>
                              );
                            })}
                            {token.chains.length > 4 && (
                              <span className="font-mono text-[7px] text-[#475569]">+{token.chains.length - 4}</span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="py-1.5 px-2 text-right">
                          <span className={`font-mono text-[9px] font-bold ${
                            token.priceDeviationPct > 5 ? 'text-amber-400' :
                            token.priceDeviationPct > 1 ? 'text-yellow-400' :
                            'text-emerald-400'
                          }`}>
                            {token.priceDeviationPct.toFixed(2)}%
                          </span>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <div className="flex flex-col items-center justify-center h-24 text-[#475569] font-mono text-[9px]">
                  <Layers className="h-4 w-4 mb-1 text-[#475569]" />
                  No cross-chain tokens found
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ============================================================ */}
      {/* D. CHAIN RANKING */}
      {/* ============================================================ */}
      <div className="px-3 pt-2 pb-3">
        <Card className="bg-[#111827] border-[#1e293b]">
          <CardHeader className="p-2 pb-1">
            <div className="flex items-center gap-1.5">
              <Trophy className="h-3 w-3 text-[#d4af37]" />
              <span className="font-mono text-[9px] text-[#64748b] uppercase tracking-wider">Chain Ranking</span>
              <span className="font-mono text-[7px] text-[#475569]">by 24h volume</span>
            </div>
          </CardHeader>
          <CardContent className="p-2 pt-0">
            <div className="space-y-1.5">
              {filteredData.chainRanking.map(entry => {
                const meta = CHAIN_META[entry.chain];
                const isPositive = entry.avgChange24h >= 0;

                // Generate sparkline from top tokens' 24h price changes
                const sparkData = entry.topTokens.length >= 2
                  ? entry.topTokens.map(t => t.priceChange24h)
                  : [];

                return (
                  <div
                    key={entry.chain}
                    className="flex items-center gap-2 bg-[#0a0e17] rounded-lg p-2 border border-[#1e293b]/50 hover:border-[#374151] transition-all"
                  >
                    {/* Rank */}
                    <div className="w-6 shrink-0 text-center">
                      <span className={`font-mono text-[11px] font-bold ${
                        entry.rank === 1 ? 'text-[#d4af37]' :
                        entry.rank === 2 ? 'text-[#c0c0c0]' :
                        entry.rank === 3 ? 'text-[#cd7f32]' :
                        'text-[#475569]'
                      }`}>
                        #{entry.rank}
                      </span>
                    </div>

                    {/* Chain Info */}
                    <div className="flex items-center gap-1.5 w-20 shrink-0">
                      <div
                        className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] shrink-0"
                        style={{ backgroundColor: `${meta?.hex || '#64748b'}20`, color: meta?.hex || '#64748b' }}
                      >
                        {meta?.emoji || '?'}
                      </div>
                      <div className="flex flex-col">
                        <span className="font-mono text-[10px] font-bold text-[#e2e8f0]">{entry.chain}</span>
                        <span className="font-mono text-[7px] text-[#475569]">{entry.tokenCount} tokens</span>
                      </div>
                    </div>

                    {/* Volume */}
                    <div className="flex flex-col w-20 shrink-0">
                      <span className="font-mono text-[7px] text-[#475569]">Volume</span>
                      <span className="font-mono text-[10px] font-bold text-[#e2e8f0]">
                        {formatVolume(entry.totalVolume24h)}
                      </span>
                    </div>

                    {/* Avg Change */}
                    <div className="flex flex-col w-16 shrink-0">
                      <span className="font-mono text-[7px] text-[#475569]">Avg 24h</span>
                      <div className="flex items-center gap-0.5">
                        {isPositive ? (
                          <ArrowUpRight className="h-3 w-3 text-emerald-400" />
                        ) : (
                          <ArrowDownRight className="h-3 w-3 text-red-400" />
                        )}
                        <span className={`font-mono text-[10px] font-bold ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                          {isPositive ? '+' : ''}{entry.avgChange24h.toFixed(1)}%
                        </span>
                      </div>
                    </div>

                    {/* Sparkline */}
                    {sparkData.length >= 2 && (
                      <div className="hidden sm:block w-16 shrink-0">
                        <MiniSparkline
                          data={sparkData}
                          color={isPositive ? '#34d399' : '#f87171'}
                          width={60}
                          height={18}
                        />
                      </div>
                    )}

                    {/* Top 3 Tokens */}
                    <div className="flex-1 flex items-center gap-1 min-w-0 overflow-hidden">
                      {entry.topTokens.slice(0, 3).map(token => (
                        <div
                          key={`${entry.chain}-${token.symbol}`}
                          className="flex items-center gap-0.5 bg-[#111827] rounded px-1.5 py-0.5 shrink-0"
                        >
                          <span className="font-mono text-[8px] text-[#e2e8f0] font-medium">{token.symbol}</span>
                          <span className={`font-mono text-[7px] font-bold ${
                            token.priceChange24h >= 0 ? 'text-emerald-400' : 'text-red-400'
                          }`}>
                            {token.priceChange24h >= 0 ? '+' : ''}{token.priceChange24h.toFixed(1)}%
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
              {filteredData.chainRanking.length === 0 && (
                <div className="flex items-center justify-center h-12 text-[#475569] font-mono text-[9px]">
                  No chain ranking data
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
