'use client';

import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Grid3X3 } from 'lucide-react';
import { normalizeChain, formatVolume, formatMarketCap } from '@/lib/format';

// ============================================================
// TYPES
// ============================================================

interface ApiTokenData {
  id: string;
  symbol: string;
  name: string;
  chain: string;
  priceUsd: number;
  volume24h: number;
  liquidity: number;
  marketCap: number;
  priceChange5m: number;
  priceChange15m: number;
  priceChange1h: number;
  priceChange24h: number;
  riskScore?: number;
}

interface TooltipData {
  symbol: string;
  name: string;
  price: number;
  change24h: number;
  volume: number;
  marketCap: number;
  chain: string;
  x: number;
  y: number;
}

// ============================================================
// CHAIN CONFIG (hex colors for headers)
// ============================================================

const CHAIN_COLUMNS = [
  { key: 'SOL', label: 'SOL', color: '#9945FF' },
  { key: 'ETH', label: 'ETH', color: '#627EEA' },
  { key: 'BASE', label: 'BASE', color: '#0052FF' },
  { key: 'BSC', label: 'BSC', color: '#F3BA2F' },
  { key: 'MATIC', label: 'MATIC', color: '#8247E5' },
  { key: 'ARB', label: 'ARB', color: '#28A0F0' },
  { key: 'OP', label: 'OP', color: '#FF0420' },
  { key: 'AVAX', label: 'AVAX', color: '#E84142' },
] as const;

// ============================================================
// HEAT COLOR HELPERS
// ============================================================

function getHeatBg(change24h: number): string {
  if (change24h > 10) return 'bg-emerald-500';
  if (change24h > 2) return 'bg-emerald-500/60';
  if (change24h >= -2) return 'bg-gray-600/40';
  if (change24h >= -10) return 'bg-red-500/60';
  return 'bg-red-500';
}

function getHeatBorder(change24h: number): string {
  if (change24h > 10) return 'border-emerald-500/60';
  if (change24h > 2) return 'border-emerald-500/40';
  if (change24h >= -2) return 'border-gray-600/30';
  if (change24h >= -10) return 'border-red-500/40';
  return 'border-red-500/60';
}

function getHeatTextColor(change24h: number): string {
  if (change24h > 10) return 'text-emerald-200';
  if (change24h > 2) return 'text-emerald-300';
  if (change24h >= -2) return 'text-gray-400';
  if (change24h >= -10) return 'text-red-300';
  return 'text-red-200';
}

/** Scale cell size based on market cap (bigger = larger cap) */
function getCellScale(marketCap: number): { w: string; h: string } {
  if (marketCap >= 1e9) return { w: 'w-full', h: 'h-10' };
  if (marketCap >= 100e6) return { w: 'w-full', h: 'h-8' };
  if (marketCap >= 10e6) return { w: 'w-full', h: 'h-7' };
  if (marketCap >= 1e6) return { w: 'w-full', h: 'h-6' };
  return { w: 'w-full', h: 'h-5' };
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function ChainHeatmap() {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  // Fetch tokens across all chains every 30s
  const { data: tokensData, isLoading } = useQuery({
    queryKey: ['chain-heatmap-tokens'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/market/tokens?chain=all&limit=100');
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();
        return (json.data || []) as ApiTokenData[];
      } catch {
        return [] as ApiTokenData[];
      }
    },
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  // Group tokens by chain
  const tokensByChain = useMemo(() => {
    const map: Record<string, ApiTokenData[]> = {};
    for (const col of CHAIN_COLUMNS) {
      map[col.key] = [];
    }

    if (tokensData) {
      for (const token of tokensData) {
        const normalized = normalizeChain(token.chain);
        if (map[normalized]) {
          map[normalized].push(token);
        }
      }
    }

    // Sort each chain by market cap descending
    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => b.marketCap - a.marketCap);
    }

    return map;
  }, [tokensData]);

  // Compute aggregate stats per chain
  const chainStats = useMemo(() => {
    const stats: Record<string, { avgChange: number; totalVolume: number; count: number }> = {};

    for (const col of CHAIN_COLUMNS) {
      const tokens = tokensByChain[col.key] || [];
      const totalChange = tokens.reduce((sum, t) => sum + t.priceChange24h, 0);
      const totalVol = tokens.reduce((sum, t) => sum + t.volume24h, 0);

      stats[col.key] = {
        avgChange: tokens.length > 0 ? totalChange / tokens.length : 0,
        totalVolume: totalVol,
        count: tokens.length,
      };
    }

    return stats;
  }, [tokensByChain]);

  // Total signal
  const totalTokens = Object.values(tokensByChain).reduce((s, t) => s + t.length, 0);

  const handleMouseEnter = (token: ApiTokenData, e: React.MouseEvent) => {
    const rect = (e.target as HTMLElement).getBoundingClientRect();
    const parentRect = (e.target as HTMLElement).closest('.heatmap-grid')?.getBoundingClientRect();
    const offsetX = parentRect ? rect.left - parentRect.left : rect.left;
    const offsetY = parentRect ? rect.top - parentRect.top : rect.top;

    setTooltip({
      symbol: token.symbol,
      name: token.name,
      price: token.priceUsd,
      change24h: token.priceChange24h,
      volume: token.volume24h,
      marketCap: token.marketCap,
      chain: normalizeChain(token.chain),
      x: offsetX + rect.width / 2,
      y: offsetY,
    });
  };

  const handleMouseLeave = () => {
    setTooltip(null);
  };

  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0a0e17]">
        <Grid3X3 className="h-3 w-3 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Chain Heatmap</span>
        <span className="font-mono text-[8px] text-[#475569]">{totalTokens} tokens</span>

        {/* Legend */}
        <div className="ml-auto flex items-center gap-1">
          <span className="font-mono text-[7px] text-[#64748b]">24h:</span>
          <div className="flex items-center gap-0.5">
            <div className="w-3 h-2 rounded-sm bg-emerald-500" />
            <span className="font-mono text-[7px] text-[#64748b]">&gt;10%</span>
          </div>
          <div className="flex items-center gap-0.5">
            <div className="w-3 h-2 rounded-sm bg-emerald-500/60" />
            <span className="font-mono text-[7px] text-[#64748b]">2-10%</span>
          </div>
          <div className="flex items-center gap-0.5">
            <div className="w-3 h-2 rounded-sm bg-gray-600/40" />
            <span className="font-mono text-[7px] text-[#64748b]">-2~2%</span>
          </div>
          <div className="flex items-center gap-0.5">
            <div className="w-3 h-2 rounded-sm bg-red-500/60" />
            <span className="font-mono text-[7px] text-[#64748b]">-10~-2%</span>
          </div>
          <div className="flex items-center gap-0.5">
            <div className="w-3 h-2 rounded-sm bg-red-500" />
            <span className="font-mono text-[7px] text-[#64748b]">&lt;-10%</span>
          </div>
        </div>

        {isLoading && <Loader2 className="h-3 w-3 text-[#d4af37] animate-spin" />}
      </div>

      {/* Heatmap Grid */}
      <div className="flex-1 overflow-auto p-2 relative heatmap-grid">
        {tokensData && tokensData.length > 0 ? (
          <div className="grid grid-cols-8 gap-1 min-w-[640px]">
            {CHAIN_COLUMNS.map((col) => {
              const tokens = tokensByChain[col.key] || [];
              const stats = chainStats[col.key];

              return (
                <div key={col.key} className="flex flex-col gap-0.5">
                  {/* Chain Header */}
                  <div
                    className="text-center py-1 rounded-t"
                    style={{ backgroundColor: `${col.color}20`, borderBottom: `2px solid ${col.color}60` }}
                  >
                    <span className="font-mono text-[9px] font-bold" style={{ color: col.color }}>
                      {col.label}
                    </span>
                    <div className="flex justify-center gap-1 mt-0.5">
                      <span className={`font-mono text-[7px] font-bold ${
                        stats.avgChange >= 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}>
                        {stats.avgChange >= 0 ? '+' : ''}{stats.avgChange.toFixed(1)}%
                      </span>
                      <span className="font-mono text-[7px] text-[#475569]">
                        {formatVolume(stats.totalVolume).replace('$', '')}
                      </span>
                    </div>
                  </div>

                  {/* Token cells */}
                  <div className="flex flex-col gap-0.5">
                    {tokens.slice(0, 12).map((token) => {
                      const cellScale = getCellScale(token.marketCap);
                      const heatBg = getHeatBg(token.priceChange24h);
                      const heatBorder = getHeatBorder(token.priceChange24h);
                      const heatText = getHeatTextColor(token.priceChange24h);

                      return (
                        <div
                          key={token.id}
                          className={`rounded-sm border ${heatBg} ${heatBorder} ${cellScale.w} ${cellScale.h} flex items-center justify-between px-1 cursor-pointer transition-all hover:brightness-125 hover:z-10 relative`}
                          onMouseEnter={(e) => handleMouseEnter(token, e)}
                          onMouseLeave={handleMouseLeave}
                        >
                          <span className={`font-mono text-[8px] font-bold ${heatText} truncate`}>
                            {token.symbol}
                          </span>
                          <span className={`font-mono text-[7px] ${heatText} shrink-0 ml-0.5`}>
                            {token.priceChange24h >= 0 ? '+' : ''}{(token.priceChange24h ?? 0).toFixed(1)}%
                          </span>
                        </div>
                      );
                    })}

                    {tokens.length === 0 && (
                      <div className="h-8 flex items-center justify-center">
                        <span className="font-mono text-[7px] text-[#475569]">No data</span>
                      </div>
                    )}

                    {/* Overflow indicator */}
                    {tokens.length > 12 && (
                      <div className="text-center py-0.5">
                        <span className="font-mono text-[7px] text-[#475569]">
                          +{tokens.length - 12} more
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-[#64748b] font-mono text-[10px] gap-2">
            {isLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin text-[#d4af37]" />
                <span>Loading heatmap data...</span>
              </>
            ) : (
              <span>No token data available</span>
            )}
          </div>
        )}

        {/* Tooltip */}
        {tooltip && (
          <div
            className="absolute z-30 bg-[#111827] border border-[#2d3748] rounded shadow-xl p-2 pointer-events-none"
            style={{
              left: Math.min(tooltip.x, 500),
              top: tooltip.y + 20,
              transform: 'translateX(-50%)',
            }}
          >
            <div className="flex items-center gap-1.5 mb-1">
              <span className="font-mono text-[10px] font-bold text-[#e2e8f0]">{tooltip.symbol}</span>
              <span className="font-mono text-[8px] text-[#64748b]">{tooltip.chain}</span>
            </div>
            <div className="space-y-0.5">
              <div className="flex justify-between gap-4">
                <span className="font-mono text-[8px] text-[#64748b]">Price</span>
                <span className="font-mono text-[9px] text-[#e2e8f0]">{formatMarketCap(tooltip.price)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="font-mono text-[8px] text-[#64748b]">24h</span>
                <span className={`font-mono text-[9px] font-bold ${tooltip.change24h >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {tooltip.change24h >= 0 ? '+' : ''}{tooltip.change24h.toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="font-mono text-[8px] text-[#64748b]">Vol</span>
                <span className="font-mono text-[9px] text-[#94a3b8]">{formatVolume(tooltip.volume)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="font-mono text-[8px] text-[#64748b]">MCap</span>
                <span className="font-mono text-[9px] text-[#94a3b8]">{formatMarketCap(tooltip.marketCap)}</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
