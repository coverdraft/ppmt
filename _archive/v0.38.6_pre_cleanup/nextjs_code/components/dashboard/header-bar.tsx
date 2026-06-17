'use client';

import { useCryptoStore, type MarketSummary } from '@/store/crypto-store';
import { useMemo } from 'react';
import { ChevronRight } from 'lucide-react';

// ============================================================
// HELPERS
// ============================================================

function formatPrice(price: number) {
  if (price == null || isNaN(price)) return '$0.00';
  if (price >= 1000) return `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (price >= 1) return `$${price.toFixed(2)}`;
  if (price >= 0.001) return `$${price.toFixed(4)}`;
  return `$${price.toFixed(8)}`;
}

// ============================================================
// MINI SPARKLINE SVG (for ticker strip)
// ============================================================

function TickerSparkline({ data, color }: { data: number[]; color: string }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 32;
  const h = 12;
  const points = data.map((v, i) =>
    `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * h}`
  ).join(' ');

  return (
    <svg width={w} height={h} className="inline-block shrink-0">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1}
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ============================================================
// TICKER STRIP COMPONENT
// ============================================================

function TickerStrip() {
  const tokens = useCryptoStore((s) => s.tokens);

  const tickerItems = useMemo(() => {
    if (tokens.length === 0) return [];
    return [...tokens]
      .sort((a, b) => Math.abs(b.priceChange24h) - Math.abs(a.priceChange24h))
      .slice(0, 30)
      .map(t => ({
        symbol: t.symbol,
        price: t.priceUsd,
        change: t.priceChange24h,
        history: t.priceHistory || [],
      }));
  }, [tokens]);

  if (tickerItems.length === 0) return null;

  const doubled = [...tickerItems, ...tickerItems];

  return (
    <div className="h-5 bg-[#060910] border-b border-[#1e293b] overflow-hidden flex items-center">
      <div className="flex items-center px-1.5 shrink-0 border-r border-[#1e293b]">
        <span className="text-[8px] font-mono text-[#3b82f6] font-bold">LIVE</span>
      </div>
      <div className="flex-1 overflow-hidden">
        <div className="ticker-scroll flex items-center gap-4 whitespace-nowrap w-max">
          {doubled.map((item, i) => (
            <span key={`${item.symbol}-${i}`} className="inline-flex items-center gap-1.5">
              <span className="text-[9px] font-mono text-[#94a3b8] font-semibold">{item.symbol}</span>
              <span className="mono-data text-[9px] text-[#e2e8f0]">${formatPrice(item.price)}</span>
              <span className={`mono-data text-[9px] font-bold ${item.change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {item.change >= 0 ? '+' : ''}{(item.change ?? 0).toFixed(1)}%
              </span>
              {item.history.length >= 2 && (
                <TickerSparkline
                  data={item.history}
                  color={item.change >= 0 ? '#10b981' : '#ef4444'}
                />
              )}
              <ChevronRight className="h-2 w-2 text-[#2d3748] shrink-0" />
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// MARKET SUMMARY BAR
// ============================================================

function MarketSummaryBar() {
  const marketSummary = useCryptoStore((s) => s.marketSummary);
  const isConnected = useCryptoStore((s) => s.isConnected);
  const chainFilter = useCryptoStore((s) => s.chainFilter);
  const setChainFilter = useCryptoStore((s) => s.setChainFilter);

  const CHAIN_FILTERS = ['ALL', 'SOL', 'ETH', 'BASE', 'ARB', 'BSC'] as const;

  return (
    <div className="flex items-center justify-between px-3 bg-[#0a0e17] border-b border-[#1e293b] h-7">
      {/* Left: Connection + Market */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-500 live-pulse' : 'bg-red-500'}`} />
          <span className={`font-mono text-[9px] ${isConnected ? 'text-emerald-400' : 'text-red-400'}`}>
            {isConnected ? 'WS' : 'OFF'}
          </span>
        </div>

        {marketSummary && (
          <>
            <div className="h-3 w-px bg-[#1e293b]" />
            <div className="flex items-center gap-1">
              <span className="text-[#f59e0b] font-mono text-[9px] font-bold">BTC</span>
              <span className="mono-data text-[10px] text-[#e2e8f0]">{formatPrice(marketSummary.btcPrice)}</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[#627eea] font-mono text-[9px] font-bold">ETH</span>
              <span className="mono-data text-[10px] text-[#e2e8f0]">{formatPrice(marketSummary.ethPrice)}</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[#64748b] font-mono text-[9px]">F&G</span>
              <span className={`mono-data text-[10px] font-bold ${
                marketSummary.fearGreedIndex > 60 ? 'text-emerald-400' :
                marketSummary.fearGreedIndex > 40 ? 'text-yellow-400' : 'text-red-400'
              }`}>
                {marketSummary.fearGreedIndex}
              </span>
            </div>
          </>
        )}
      </div>

      {/* Right: Chain Filters */}
      <div className="flex items-center gap-0">
        <span className="text-[7px] font-mono text-[#475569] mr-1">CHAIN:</span>
        {CHAIN_FILTERS.map((chain) => {
          const isActive = chainFilter === chain;
          const chainColor = chain === 'SOL' ? '#9945FF' : chain === 'ETH' ? '#627eea' : chain === 'BASE' ? '#0052FF' : chain === 'ARB' ? '#28A0F0' : chain === 'BSC' ? '#F3BA2F' : '#3b82f6';
          return (
            <button
              key={chain}
              onClick={() => setChainFilter(chain)}
              className={`flex items-center gap-0.5 px-1.5 py-0.5 text-[9px] font-mono transition-all rounded ${
                isActive
                  ? 'text-[#f1f5f9] bg-[#1e293b]'
                  : 'text-[#64748b] hover:text-[#94a3b8]'
              }`}
            >
              {chain !== 'ALL' && (
                <span
                  className="w-1 h-1 rounded-full inline-block"
                  style={{ backgroundColor: chainColor }}
                />
              )}
              {chain}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ============================================================
// MAIN HEADER BAR COMPONENT
// ============================================================

export function HeaderBar() {
  return (
    <div className="shrink-0">
      <TickerStrip />
      <MarketSummaryBar />
    </div>
  );
}
