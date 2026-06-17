'use client';

import { useCryptoStore, type TokenData } from '@/store/crypto-store';
import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Database, RefreshCw, Loader2, Radio, Download, ArrowUp, ArrowDown, Search } from 'lucide-react';
// ============================================================
// API RESPONSE TYPES
// ============================================================

interface ApiTokenData {
  id: string;
  symbol: string;
  name: string;
  chain: string;
  address?: string;
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

// ============================================================
// HELPERS
// ============================================================

function formatPrice(price: number) {
  if (price == null || isNaN(price)) return '0.00';
  if (price >= 1000) return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (price >= 1) return price.toFixed(2);
  if (price >= 0.001) return price.toFixed(4);
  if (price >= 0.00001) return price.toFixed(6);
  return price.toFixed(8);
}

function formatVolume(vol: number) {
  if (vol == null || isNaN(vol)) return '0';
  if (vol >= 1e9) return `${(vol / 1e9).toFixed(1)}B`;
  if (vol >= 1e6) return `${(vol / 1e6).toFixed(1)}M`;
  if (vol >= 1e3) return `${(vol / 1e3).toFixed(1)}K`;
  return vol.toFixed(0);
}

function getRiskDotColor(riskScore?: number) {
  if (!riskScore) return '#64748b';
  if (riskScore <= 30) return '#10b981';
  if (riskScore <= 60) return '#f59e0b';
  return '#ef4444';
}

function getChainColor(chain: string): string {
  const upper = chain.toUpperCase();
  if (upper === 'SOL' || upper === 'SOLANA') return '#9945FF';
  if (upper === 'ETH' || upper === 'ETHEREUM') return '#627eea';
  if (upper === 'BASE') return '#0052FF';
  if (upper === 'ARB') return '#28A0F0';
  if (upper === 'BSC') return '#F3BA2F';
  return '#64748b';
}

function normalizeChain(chain: string): string {
  const upper = chain.toUpperCase();
  if (upper === 'SOLANA') return 'SOL';
  if (upper === 'ETHEREUM') return 'ETH';
  return upper;
}

// ============================================================
// SVG SPARKLINE (pure SVG, no recharts dependency for perf)
// ============================================================

function SvgSparkline({ data, color, width = 40, height = 14 }: { data: number[]; color: string; width?: number; height?: number }) {
  if (data.length < 2) return <span className="text-[7px] text-[#475569]">—</span>;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 1;

  const points = data.map((v, i) => {
    const x = pad + (i / (data.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (v - min) / range) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  // Generate area fill
  const firstX = pad;
  const lastX = pad + ((data.length - 1) / (data.length - 1)) * (width - pad * 2);
  const areaPoints = `${firstX},${height} ${points} ${lastX},${height}`;

  return (
    <svg width={width} height={height} className="inline-block shrink-0">
      <polygon
        points={areaPoints}
        fill={color}
        fillOpacity={0.08}
      />
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1}
        strokeLinejoin="round"
        strokeLinecap="round"
        className="sparkline-path"
      />
    </svg>
  );
}

// ============================================================
// SORT ICON
// ============================================================

function SortIcon({ active, direction }: { active: boolean; direction: 'asc' | 'desc' }) {
  if (!active) return <span className="text-[#475569] ml-0.5">↕</span>;
  return direction === 'asc'
    ? <ArrowUp className="h-2 w-2 text-[#d4af37] ml-0.5 inline" />
    : <ArrowDown className="h-2 w-2 text-[#d4af37] ml-0.5 inline" />;
}

// ============================================================
// COLUMN SORT CONFIG
// ============================================================

type SortColumn = 'symbol' | 'price' | '5m' | '1h' | '24h' | 'volume' | 'liquidity' | 'risk' | 'mcap';

// ============================================================
// MAIN COMPONENT
// ============================================================

export function TokenFlow() {
  const wsTokens = useCryptoStore((s) => s.tokens);
  const selectedToken = useCryptoStore((s) => s.selectedToken);
  const selectToken = useCryptoStore((s) => s.selectToken);
  const chainFilter = useCryptoStore((s) => s.chainFilter);
  const setChainFilter = useCryptoStore((s) => s.setChainFilter);
  const riskFilter = useCryptoStore((s) => s.riskFilter);
  const setRiskFilter = useCryptoStore((s) => s.setRiskFilter);
  const sortBy = useCryptoStore((s) => s.sortBy);
  const setSortBy = useCryptoStore((s) => s.setSortBy);
  const [search, setSearch] = useState('');
  const [useLiveData, setUseLiveData] = useState(true);
  const [isSyncing, setIsSyncing] = useState(false);
  const [sortColumn, setSortColumn] = useState<SortColumn>('volume');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  const [isSeeding, setIsSeeding] = useState(false);

  // Fetch ALL token data from DB
  const { data: apiTokensData, isLoading: apiLoading, refetch: refetchTokens } = useQuery({
    queryKey: ['market-tokens', chainFilter, riskFilter, sortBy, search],
    queryFn: async () => {
      try {
        const chain = chainFilter;
        const params = new URLSearchParams({
          chain,
          limit: '500',
          offset: '0',
        });
        if (search) params.set('search', search);
        const res = await fetch(`/api/market/tokens?${params}`);
        if (!res.ok) throw new Error('Failed to fetch');
        const json = await res.json();
        return {
          tokens: (json.data || []) as ApiTokenData[],
          source: json.source as string,
          total: json.total || 0,
          hasMore: json.hasMore || false,
        };
      } catch {
        return { tokens: [] as ApiTokenData[], source: 'fallback' as const, total: 0, hasMore: false };
      }
    },
    refetchInterval: 30000,
    staleTime: 15000,
    enabled: useLiveData,
  });

  // Merge API tokens with WS tokens
  const mergedTokens = useMemo(() => {
    if (!useLiveData || !apiTokensData || apiTokensData.tokens.length === 0) {
      return wsTokens.map(t => ({ ...t, _dataSource: 'ws' as const }));
    }

    const apiTokens = apiTokensData.tokens;
    const apiSourceFlag = apiTokensData.source;

    const wsBySymbol = new Map<string, TokenData>();
    for (const t of wsTokens) {
      wsBySymbol.set(t.symbol.toUpperCase(), t);
    }

    const merged: Array<TokenData & { _dataSource: 'api' | 'ws'; _apiSource?: string; _address?: string }> = [];
    const seenSymbols = new Set<string>();

    for (const apiToken of apiTokens) {
      const symbolKey = `${apiToken.symbol.toUpperCase()}-${normalizeChain(apiToken.chain)}`;
      if (seenSymbols.has(symbolKey)) continue;

      const wsToken = wsBySymbol.get(apiToken.symbol.toUpperCase());
      const normalizedChain = normalizeChain(apiToken.chain);

      merged.push({
        id: apiToken.id,
        symbol: apiToken.symbol,
        name: apiToken.name,
        chain: normalizedChain,
        priceUsd: apiToken.priceUsd,
        volume24h: apiToken.volume24h,
        liquidity: apiToken.liquidity,
        marketCap: apiToken.marketCap,
        priceChange5m: apiToken.priceChange5m,
        priceChange15m: apiToken.priceChange15m,
        priceChange1h: apiToken.priceChange1h,
        priceChange24h: apiToken.priceChange24h,
        riskScore: apiToken.riskScore,
        priceHistory: wsToken?.priceHistory,
        _dataSource: 'api',
        _apiSource: apiSourceFlag,
        _address: apiToken.address || apiToken.id,
      } as TokenData & { _dataSource: 'api'; _apiSource: string; _address?: string });

      seenSymbols.add(symbolKey);
    }

    for (const wsToken of wsTokens) {
      const symbolKey = `${wsToken.symbol.toUpperCase()}-${normalizeChain(wsToken.chain)}`;
      if (!seenSymbols.has(symbolKey)) {
        merged.push({
          ...wsToken,
          _dataSource: 'ws' as const,
          _address: (wsToken as any).address || wsToken.id,
        } as TokenData & { _dataSource: 'ws'; _address?: string });
        seenSymbols.add(symbolKey);
      }
    }

    return merged;
  }, [wsTokens, apiTokensData, useLiveData]);

  // Filter + sort (with column header sorting)
  const filteredTokens = useMemo(() => {
    let filtered = [...mergedTokens];

    if (chainFilter !== 'ALL') {
      filtered = filtered.filter((t) => normalizeChain(t.chain) === chainFilter);
    }

    if (riskFilter !== 'ALL') {
      filtered = filtered.filter((t) => {
        if (riskFilter === 'SAFE') return (t.riskScore ?? 50) <= 30;
        if (riskFilter === 'CAUTION') return (t.riskScore ?? 50) > 30 && (t.riskScore ?? 50) <= 60;
        if (riskFilter === 'DANGER') return (t.riskScore ?? 50) > 60;
        return true;
      });
    }

    if (search) {
      const lower = search.toLowerCase();
      filtered = filtered.filter(
        (t) => t.symbol.toLowerCase().includes(lower) || t.name.toLowerCase().includes(lower)
      );
    }

    // Column sorting
    const dir = sortDirection === 'asc' ? 1 : -1;
    switch (sortColumn) {
      case 'symbol':
        filtered.sort((a, b) => a.symbol.localeCompare(b.symbol) * dir);
        break;
      case 'price':
        filtered.sort((a, b) => (a.priceUsd - b.priceUsd) * dir);
        break;
      case '5m':
        filtered.sort((a, b) => (a.priceChange5m - b.priceChange5m) * dir);
        break;
      case '1h':
        filtered.sort((a, b) => (a.priceChange1h - b.priceChange1h) * dir);
        break;
      case '24h':
        filtered.sort((a, b) => (a.priceChange24h - b.priceChange24h) * dir);
        break;
      case 'volume':
        filtered.sort((a, b) => (a.volume24h - b.volume24h) * dir);
        break;
      case 'liquidity':
        filtered.sort((a, b) => (a.liquidity - b.liquidity) * dir);
        break;
      case 'risk':
        filtered.sort((a, b) => ((a.riskScore ?? 50) - (b.riskScore ?? 50)) * dir);
        break;
      case 'mcap':
        filtered.sort((a, b) => (a.marketCap - b.marketCap) * dir);
        break;
    }

    return filtered;
  }, [mergedTokens, chainFilter, riskFilter, search, sortColumn, sortDirection]);

  // Column header click handler
  const handleSort = useCallback((col: SortColumn) => {
    if (sortColumn === col) {
      setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortColumn(col);
      setSortDirection('desc');
    }
  }, [sortColumn]);

  // Seed handler for when DB is empty
  const handleSeed = useCallback(async () => {
    setIsSeeding(true);
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
      // Wait for DB writes to settle, then refetch
      await new Promise(r => setTimeout(r, 2000));
      await refetchTokens();
    } catch (err) {
      console.error('[TokenFlow] Seed failed:', err);
    } finally {
      setIsSeeding(false);
    }
  }, [refetchTokens]);

  // Stats
  const apiTokenCount = mergedTokens.filter(t => (t as TokenData & { _dataSource: string })._dataSource === 'api').length;
  const wsTokenCount = mergedTokens.filter(t => (t as TokenData & { _dataSource: string })._dataSource === 'ws').length;
  const effectiveSource = apiTokensData?.source || 'fallback';
  const totalDbTokens = apiTokensData?.total || 0;

  // Generate pseudo-sparkline data if no priceHistory (7 points from price changes)
  const getSparklineData = (token: TokenData): number[] => {
    if (token.priceHistory && token.priceHistory.length >= 2) return token.priceHistory.slice(-7);
    // Generate from price changes
    const base = token.priceUsd;
    const changes = [0, token.priceChange5m, token.priceChange15m, 0, token.priceChange1h, 0, token.priceChange24h];
    return changes.map((c, i) => base * (1 - (c / 100) * ((changes.length - i) / changes.length)));
  };

  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header with data source info */}
      <div className="flex items-center gap-2 px-2 py-1 border-b border-[#1e293b] bg-[#0a0e17]">
        <Database className="h-3 w-3 text-[#d4af37]" />
        <span className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">Token Flow</span>

        <div className="ml-auto flex items-center gap-2">
          {/* Data source stats */}
          {useLiveData && (
            <span className="text-[8px] font-mono text-[#475569]">
              {apiTokenCount} DB{wsTokenCount > 0 ? ` + ${wsTokenCount} WS` : ''} | Total: {totalDbTokens.toLocaleString()}
            </span>
          )}

          {/* Source indicator */}
          <div className="flex items-center gap-1">
            <span className={`data-dot ${
              useLiveData && effectiveSource === 'live' ? 'data-dot-live' :
              useLiveData && effectiveSource === 'fallback' ? 'data-dot-db' :
              'data-dot-offline'
            }`} />
            <span className={`text-[8px] font-mono ${
              useLiveData && effectiveSource === 'live' ? 'text-emerald-400' :
              useLiveData && effectiveSource === 'fallback' ? 'text-yellow-400' :
              'text-gray-400'
            }`}>
              {useLiveData ? (effectiveSource === 'live' ? 'LIVE' : 'DB') : 'WS'}
            </span>
          </div>

          {/* Toggle live data */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setUseLiveData(!useLiveData)}
            className={`h-4 px-1 text-[8px] font-mono ${
              useLiveData ? 'text-emerald-400 hover:text-emerald-300' : 'text-[#64748b] hover:text-[#94a3b8]'
            }`}
          >
            <Radio className="h-2 w-2 mr-0.5" />
            {useLiveData ? 'Live' : 'WS'}
          </Button>

          {apiLoading && <Loader2 className="h-2.5 w-2.5 text-[#d4af37] animate-spin" />}

          {/* Sync Real Data Button */}
          <Button
            variant="ghost"
            size="sm"
            disabled={isSyncing}
            onClick={async () => {
              setIsSyncing(true);
              try {
                await fetch('/api/brain/init');
                fetch('/api/data-sync?chain=all', { method: 'GET' }).catch(() => {});
              } catch {}
              setTimeout(() => setIsSyncing(false), 5000);
            }}
            className={`h-4 px-1 text-[8px] font-mono ${
              isSyncing ? 'text-[#d4af37]' : 'text-[#64748b] hover:text-[#d4af37]'
            }`}
          >
            {isSyncing ? <Loader2 className="h-2 w-2 mr-0.5 animate-spin" /> : <Download className="h-2 w-2 mr-0.5" />}
            {isSyncing ? 'Sync...' : 'Sync'}
          </Button>
        </div>
      </div>

      {/* Filter Bar - compact */}
      <div className="flex items-center gap-1.5 px-2 py-1 border-b border-[#1e293b] bg-[#0d1117]">
        <div className="relative">
          <Search className="absolute left-1.5 top-1/2 -translate-y-1/2 h-2.5 w-2.5 text-[#475569]" />
          <input
            type="text"
            placeholder="Search tokens..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-[#1a1f2e] border border-[#2d3748] rounded pl-5 pr-2 py-0.5 text-[9px] font-mono text-[#e2e8f0] placeholder-[#475569] w-28 focus:outline-none focus:border-[#3b82f6]/50 transition-colors"
          />
        </div>

        <div className="flex gap-px">
          {['ALL', 'SOL', 'ETH', 'BASE', 'ARB'].map((chain) => (
            <Button
              key={chain}
              variant="ghost"
              size="sm"
              onClick={() => setChainFilter(chain)}
              className={`h-4 px-1 text-[8px] font-mono ${
                chainFilter === chain
                  ? 'bg-[#3b82f6]/15 text-[#3b82f6]'
                  : 'text-[#64748b] hover:text-[#e2e8f0]'
              }`}
            >
              {chain}
            </Button>
          ))}
        </div>

        <div className="h-3 w-px bg-[#1e293b]" />

        <div className="flex gap-px">
          {['ALL', 'SAFE', 'CAUTION', 'DANGER'].map((risk) => (
            <Button
              key={risk}
              variant="ghost"
              size="sm"
              onClick={() => setRiskFilter(risk)}
              className={`h-4 px-1 text-[8px] font-mono ${
                riskFilter === risk
                  ? risk === 'SAFE' ? 'bg-emerald-500/15 text-emerald-400'
                    : risk === 'CAUTION' ? 'bg-yellow-500/15 text-yellow-400'
                    : risk === 'DANGER' ? 'bg-red-500/15 text-red-400'
                    : 'bg-[#d4af37]/15 text-[#d4af37]'
                  : 'text-[#475569] hover:text-[#e2e8f0]'
              }`}
            >
              {risk}
            </Button>
          ))}
        </div>

        <span className="ml-auto text-[8px] font-mono text-[#475569]">
          {filteredTokens.length} tokens
        </span>
      </div>

      {/* Token Table - Compact rows */}
      <div className="flex-1 overflow-y-auto max-h-[calc(100vh-260px)] custom-scrollbar">
        <table className="w-full">
          <thead className="sticky top-0 bg-[#0d1117] z-10">
            <tr className="text-[8px] font-mono text-[#475569] uppercase tracking-wider">
              <th className="text-left py-1 px-2 cursor-pointer hover:text-[#94a3b8] select-none" onClick={() => handleSort('symbol')}>
                Token <SortIcon active={sortColumn === 'symbol'} direction={sortDirection} />
              </th>
              <th className="text-right py-1 px-1 cursor-pointer hover:text-[#94a3b8] select-none" onClick={() => handleSort('price')}>
                Price <SortIcon active={sortColumn === 'price'} direction={sortDirection} />
              </th>
              <th className="text-right py-1 px-1 cursor-pointer hover:text-[#94a3b8] select-none" onClick={() => handleSort('5m')}>
                5m <SortIcon active={sortColumn === '5m'} direction={sortDirection} />
              </th>
              <th className="text-right py-1 px-1 cursor-pointer hover:text-[#94a3b8] select-none" onClick={() => handleSort('1h')}>
                1h <SortIcon active={sortColumn === '1h'} direction={sortDirection} />
              </th>
              <th className="text-right py-1 px-1 cursor-pointer hover:text-[#94a3b8] select-none" onClick={() => handleSort('24h')}>
                24h <SortIcon active={sortColumn === '24h'} direction={sortDirection} />
              </th>
              <th className="text-right py-1 px-1 cursor-pointer hover:text-[#94a3b8] select-none" onClick={() => handleSort('volume')}>
                Vol <SortIcon active={sortColumn === 'volume'} direction={sortDirection} />
              </th>
              <th className="text-right py-1 px-1 cursor-pointer hover:text-[#94a3b8] select-none" onClick={() => handleSort('liquidity')}>
                Liq <SortIcon active={sortColumn === 'liquidity'} direction={sortDirection} />
              </th>
              <th className="text-center py-1 px-1">Chart</th>
              <th className="text-center py-1 px-1 cursor-pointer hover:text-[#94a3b8] select-none" onClick={() => handleSort('risk')}>
                Risk <SortIcon active={sortColumn === 'risk'} direction={sortDirection} />
              </th>
              <th className="text-center py-1 px-0.5">·</th>
            </tr>
          </thead>
          <tbody>
            {filteredTokens.map((token) => {
              const isSelected = selectedToken?.id === token.id;
              const sparkData = getSparklineData(token);
              const sparkColor = token.priceChange24h >= 0 ? '#10b981' : '#ef4444';
              const dataSource = (token as TokenData & { _dataSource?: string })._dataSource || 'ws';
              const chainColor = getChainColor(token.chain);
              const riskDotColor = getRiskDotColor(token.riskScore);

              return (
                <tr
                  key={(token as any)._address || (token as any).id || `${token.symbol}-${token.chain}-${(mergedTokens as any[]).indexOf(token)}`}
                  onClick={() => selectToken(token as TokenData)}
                  className={`terminal-row cursor-pointer transition-colors border-b border-[#1e293b]/30 ${
                    isSelected ? 'terminal-row-selected' : ''
                  }`}
                >
                  <td className="py-0.5 px-2">
                    <div className="flex items-center gap-1">
                      <span
                        className="w-1 h-1 rounded-full shrink-0"
                        style={{ backgroundColor: chainColor }}
                        title={normalizeChain(token.chain)}
                      />
                      <span className="font-mono text-[10px] font-bold text-[#e2e8f0]">{token.symbol}</span>
                      <span className="text-[8px] text-[#475569] truncate max-w-[50px]">{token.name}</span>
                      <span
                        className="text-[7px] font-mono px-0.5 rounded"
                        style={{ color: chainColor, border: `0.5px solid ${chainColor}33` }}
                      >
                        {normalizeChain(token.chain)}
                      </span>
                    </div>
                  </td>
                  <td className="py-0.5 px-1 text-right">
                    <span className="mono-data text-[10px] text-[#e2e8f0]">${formatPrice(token.priceUsd)}</span>
                  </td>
                  <td className="py-0.5 px-1 text-right">
                    <span className={`mono-data text-[10px] ${token.priceChange5m >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {token.priceChange5m >= 0 ? '+' : ''}{(token.priceChange5m ?? 0).toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-0.5 px-1 text-right">
                    <span className={`mono-data text-[10px] ${token.priceChange1h >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {token.priceChange1h >= 0 ? '+' : ''}{(token.priceChange1h ?? 0).toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-0.5 px-1 text-right">
                    <span className={`mono-data text-[10px] font-bold ${token.priceChange24h >= 0 ? 'text-emerald-400 green-glow-text' : 'text-red-400 red-glow-text'}`}>
                      {token.priceChange24h >= 0 ? '+' : ''}{(token.priceChange24h ?? 0).toFixed(1)}%
                    </span>
                  </td>
                  <td className="py-0.5 px-1 text-right">
                    <span className="mono-data text-[10px] text-[#94a3b8]">${formatVolume(token.volume24h)}</span>
                  </td>
                  <td className="py-0.5 px-1 text-right">
                    <span className="mono-data text-[10px] text-[#94a3b8]">${formatVolume(token.liquidity)}</span>
                  </td>
                  <td className="py-0 px-1 text-center">
                    <SvgSparkline data={sparkData} color={sparkColor} width={40} height={14} />
                  </td>
                  <td className="py-0.5 px-1 text-center">
                    <div className="flex items-center justify-center gap-0.5">
                      <span
                        className="w-1.5 h-1.5 rounded-full inline-block shrink-0"
                        style={{
                          backgroundColor: riskDotColor,
                          boxShadow: `0 0 3px ${riskDotColor}60`
                        }}
                        title={`Risk: ${token.riskScore ?? '?'}`}
                      />
                      <span className="mono-data text-[8px] text-[#94a3b8]">{token.riskScore ?? '?'}</span>
                    </div>
                  </td>
                  <td className="py-0.5 px-0.5 text-center">
                    <span className={`data-dot ${dataSource === 'api' ? 'data-dot-live' : 'data-dot-offline'}`} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {filteredTokens.length === 0 && (
          <div className="flex flex-col items-center justify-center h-24 text-[#64748b] font-mono text-[10px] gap-1.5">
            {apiLoading ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin text-[#d4af37]" />
                <span>Loading tokens...</span>
              </>
            ) : mergedTokens.length === 0 && !isSeeding ? (
              <>
                <Database className="h-4 w-4 text-[#475569]" />
                <span>No tokens in database</span>
                <Button
                  onClick={handleSeed}
                  className="mt-1 bg-[#3b82f6] hover:bg-[#3b82f6]/80 text-white font-mono text-[9px] h-6 px-3"
                >
                  <Database className="h-2.5 w-2.5 mr-1" /> Load Data
                </Button>
              </>
            ) : isSeeding ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin text-[#3b82f6]" />
                <span>Seeding tokens...</span>
              </>
            ) : (
              <span>No tokens matching filters</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
