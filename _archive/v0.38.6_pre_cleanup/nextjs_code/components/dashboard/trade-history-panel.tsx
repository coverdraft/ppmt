'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { motion, AnimatePresence } from 'framer-motion';
import {
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownRight,
  DollarSign,
  BarChart3,
  Activity,
  RefreshCw,
  Zap,
  Target,
  ChevronRight,
  Percent,
  Timer,
  Tag,
  Settings2,
} from 'lucide-react';

// ============================================================
// TYPES
// ============================================================

interface TradeRecord {
  id: string;
  systemId: string;
  systemName: string;
  tokenAddress: string;
  tokenSymbol: string;
  direction: string;
  entryPrice: number;
  exitPrice: number;
  entryTime: string;
  exitTime: string;
  pnlUsd: number | null;
  pnlPct: number | null;
  holdTimeMin: number | null;
  exitReason: string | null;
  mode: string;
}

interface OpenPosition {
  backtestId: string;
  systemId: string;
  systemName: string;
  tokenAddress: string;
  tokenSymbol: string;
  direction: string;
  entryPrice: number;
  entryTime: string;
  positionSizeUsd: number;
  quantity: number;
  unrealizedPnl: number;
}

// ============================================================
// HELPERS
// ============================================================

function formatPnl(value: number): string {
  return value >= 0 ? `+$${value.toFixed(2)}` : `-$${Math.abs(value).toFixed(2)}`;
}

function formatPct(value: number): string {
  return value >= 0 ? `+${value.toFixed(1)}%` : `${value.toFixed(1)}%`;
}

function formatDuration(minutes: number | null): string {
  if (!minutes) return '—';
  if (minutes < 60) return `${minutes.toFixed(0)}m`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}

function formatTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return dateStr;
  }
}

function formatTimeFull(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return dateStr;
  }
}

function getExitReasonLabel(reason: string | null): { label: string; color: string } {
  if (!reason) return { label: 'Unknown', color: 'text-[#64748b]' };
  const r = reason.toLowerCase();
  if (r.includes('take_profit') || r.includes('tp')) return { label: 'Take Profit', color: 'text-emerald-400' };
  if (r.includes('stop_loss') || r.includes('sl')) return { label: 'Stop Loss', color: 'text-red-400' };
  if (r.includes('trailing')) return { label: 'Trailing Stop', color: 'text-yellow-400' };
  if (r.includes('time')) return { label: 'Time Exit', color: 'text-blue-400' };
  if (r.includes('signal')) return { label: 'Signal Exit', color: 'text-purple-400' };
  if (r.includes('manual')) return { label: 'Manual', color: 'text-cyan-400' };
  return { label: reason, color: 'text-[#94a3b8]' };
}

// ============================================================
// ENHANCED EQUITY CURVE + ENTRY/EXIT MARKERS CHART
// ============================================================

function EquityCurveChart({ trades }: { trades: TradeRecord[] }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  const sortedTrades = useMemo(
    () => [...trades].sort((a, b) => new Date(a.exitTime).getTime() - new Date(b.exitTime).getTime()),
    [trades],
  );

  const chartData = useMemo(() => {
    const pnlValues = sortedTrades.map(t => t.pnlUsd || 0);
    // Compute cumulative PnL and peak via reduce to avoid mutation in render
    const cumulatives: number[] = [];
    const peaks: number[] = [];
    let runningPnl = 0;
    let runningPeak = 0;
    for (const pnl of pnlValues) {
      runningPnl += pnl;
      if (runningPnl > runningPeak) runningPeak = runningPnl;
      cumulatives.push(runningPnl);
      peaks.push(runningPeak);
    }
    return sortedTrades.map((trade, i) => ({
      index: i,
      cumulativePnl: cumulatives[i],
      drawdown: peaks[i] - cumulatives[i],
      trade,
      isWin: (trade.pnlPct || 0) > 0,
      entryTime: new Date(trade.entryTime).getTime(),
      exitTime: new Date(trade.exitTime).getTime(),
    }));
  }, [sortedTrades]);

  if (chartData.length === 0) return null;

  const W = 520;
  const H = 160;
  const padX = 5;
  const padY = 5;
  const chartW = W - padX * 2;
  const chartH = H - padY * 2;

  const minY = Math.min(0, ...chartData.map(d => d.cumulativePnl));
  const maxY = Math.max(0, ...chartData.map(d => d.cumulativePnl));
  const rangeY = maxY - minY || 1;

  const toX = (i: number) => padX + (i / Math.max(chartData.length - 1, 1)) * chartW;
  const toY = (v: number) => padY + chartH - ((v - minY) / rangeY) * chartH;

  const zeroY = toY(0);
  const pathD = chartData.map((d, i) => `${i === 0 ? 'M' : 'L'} ${toX(i)} ${toY(d.cumulativePnl)}`).join(' ');

  const hovered = hoveredIndex !== null ? chartData[hoveredIndex] : null;

  return (
    <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5 text-[#d4af37]" />
          <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider">Equity Curve</span>
          <Badge className="text-[7px] h-3.5 px-1 font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
            {trades.length} trades
          </Badge>
        </div>
        <div className="flex items-center gap-3 text-[8px] font-mono">
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 bg-emerald-400" style={{ clipPath: 'polygon(50% 0%, 0% 100%, 100% 100%)' }} /> Entry</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 bg-red-400" style={{ clipPath: 'polygon(50% 100%, 0% 0%, 100% 0%)' }} /> Exit</span>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: '160px' }}>
        {/* Zero line */}
        <line x1={padX} y1={zeroY} x2={W - padX} y2={zeroY} stroke="#2d3748" strokeWidth="0.5" strokeDasharray="2,2" />

        {/* Grid lines */}
        {[0.25, 0.5, 0.75].map(frac => (
          <line key={frac} x1={padX} y1={padY + chartH * frac} x2={W - padX} y2={padY + chartH * frac} stroke="#1a1f2e" strokeWidth="0.3" />
        ))}

        {/* Area fill under curve */}
        {chartData.length > 1 && (
          <path
            d={`${pathD} L ${toX(chartData.length - 1)} ${padY + chartH} L ${padX} ${padY + chartH} Z`}
            fill="url(#equityGrad)"
            opacity="0.25"
          />
        )}

        {/* Main line */}
        {chartData.length > 1 && (
          <path
            d={pathD}
            fill="none"
            stroke={chartData[chartData.length - 1].cumulativePnl >= 0 ? '#10b981' : '#ef4444'}
            strokeWidth="1.2"
          />
        )}

        {/* Entry markers (green triangles pointing up) with glow */}
        {chartData.map((d, i) => {
          const cx = toX(i);
          const cy = toY(d.cumulativePnl);
          const s = 4;
          return (
            <g key={`entry-${i}`}>
              {/* Glow effect */}
              <polygon
                points={`${cx},${cy - s * 1.5} ${cx - s},${cy + s * 0.5} ${cx + s},${cy + s * 0.5}`}
                fill="#10b981"
                opacity={hoveredIndex === i ? 0.5 : 0.25}
                filter="url(#entryGlow)"
              />
              {/* Solid marker */}
              <polygon
                points={`${cx},${cy - s * 1.5} ${cx - s},${cy + s * 0.5} ${cx + s},${cy + s * 0.5}`}
                fill="#10b981"
                opacity={hoveredIndex === i ? 1 : 0.85}
              />
            </g>
          );
        })}

        {/* Exit markers (red triangles pointing down) with glow */}
        {chartData.map((d, i) => {
          const cx = toX(i);
          // Place exit marker slightly below the entry marker
          const cy = toY(d.cumulativePnl) + 8;
          const s = 4;
          return (
            <g key={`exit-${i}`}>
              {/* Glow effect */}
              <polygon
                points={`${cx},${cy + s * 1.5} ${cx - s},${cy - s * 0.5} ${cx + s},${cy - s * 0.5}`}
                fill="#ef4444"
                opacity={hoveredIndex === i ? 0.5 : 0.25}
                filter="url(#exitGlow)"
              />
              {/* Solid marker */}
              <polygon
                points={`${cx},${cy + s * 1.5} ${cx - s},${cy - s * 0.5} ${cx + s},${cy - s * 0.5}`}
                fill="#ef4444"
                opacity={hoveredIndex === i ? 1 : 0.85}
              />
            </g>
          );
        })}

        {/* Hover crosshair */}
        {hovered && (
          <>
            <line x1={toX(hoveredIndex!)} y1={padY} x2={toX(hoveredIndex!)} y2={padY + chartH} stroke="#d4af37" strokeWidth="0.5" strokeDasharray="2,2" opacity="0.5" />
            <circle cx={toX(hoveredIndex!)} cy={toY(hovered.cumulativePnl)} r="3" fill="none" stroke="#d4af37" strokeWidth="1" />
          </>
        )}

        <defs>
          <linearGradient id="equityGrad" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor={chartData[chartData.length - 1].cumulativePnl >= 0 ? '#10b981' : '#ef4444'} stopOpacity="0.4" />
            <stop offset="100%" stopColor={chartData[chartData.length - 1].cumulativePnl >= 0 ? '#10b981' : '#ef4444'} stopOpacity="0" />
          </linearGradient>
          {/* Glow filters for markers */}
          <filter id="entryGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feFlood floodColor="#10b981" floodOpacity="0.6" result="color" />
            <feComposite in="color" in2="blur" operator="in" result="glow" />
            <feMerge>
              <feMergeNode in="glow" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <filter id="exitGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feFlood floodColor="#ef4444" floodOpacity="0.6" result="color" />
            <feComposite in="color" in2="blur" operator="in" result="glow" />
            <feMerge>
              <feMergeNode in="glow" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Invisible hit areas for hover */}
        {chartData.map((d, i) => (
          <rect
            key={`hit-${i}`}
            x={toX(i) - 8}
            y={padY}
            width={16}
            height={chartH}
            fill="transparent"
            onMouseEnter={() => setHoveredIndex(i)}
            onMouseLeave={() => setHoveredIndex(null)}
            className="cursor-pointer"
          />
        ))}
      </svg>

      {/* Hover tooltip */}
      {hovered && (
        <div className="mt-1 bg-[#111827] border border-[#1e293b] rounded px-3 py-2 flex items-center gap-4 text-[9px] font-mono">
          <span className="text-[#94a3b8]">
            {hovered.trade.tokenSymbol || hovered.trade.tokenAddress.slice(0, 8)}
          </span>
          <span className={hovered.isWin ? 'text-emerald-400' : 'text-red-400'}>
            {formatPct(hovered.trade.pnlPct || 0)}
          </span>
          <span className={hovered.isWin ? 'text-emerald-400/70' : 'text-red-400/70'}>
            {formatPnl(hovered.trade.pnlUsd || 0)}
          </span>
          <span className="text-[#64748b]">
            Hold: {formatDuration(hovered.trade.holdTimeMin)}
          </span>
          <span className="text-[#64748b]">
            {formatTime(hovered.trade.exitTime)}
          </span>
          <span className={hovered.isWin ? 'text-emerald-400' : 'text-red-400'}>
            ${hovered.trade.entryPrice.toFixed(4)} → ${hovered.trade.exitPrice.toFixed(4)}
          </span>
        </div>
      )}

      {/* Summary stats */}
      <div className="grid grid-cols-4 gap-2 mt-2">
        {(() => {
          const totalPnl = chartData.reduce((s, d) => s + (d.trade.pnlUsd || 0), 0);
          const wins = chartData.filter(d => d.isWin).length;
          const bestPct = Math.max(...chartData.map(d => d.trade.pnlPct || 0));
          const worstPct = Math.min(...chartData.map(d => d.trade.pnlPct || 0));
          return (
            <>
              <div>
                <span className="text-[8px] font-mono text-[#64748b] uppercase block">Total PnL</span>
                <span className={`text-[10px] font-mono font-bold ${totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {formatPnl(totalPnl)}
                </span>
              </div>
              <div>
                <span className="text-[8px] font-mono text-[#64748b] uppercase block">Win Rate</span>
                <span className="text-[10px] font-mono font-bold text-[#e2e8f0]">
                  {chartData.length > 0 ? ((wins / chartData.length) * 100).toFixed(0) : 0}%
                </span>
              </div>
              <div>
                <span className="text-[8px] font-mono text-[#64748b] uppercase block">Best Trade</span>
                <span className="text-[10px] font-mono font-bold text-emerald-400">{formatPct(bestPct)}</span>
              </div>
              <div>
                <span className="text-[8px] font-mono text-[#64748b] uppercase block">Worst Trade</span>
                <span className="text-[10px] font-mono font-bold text-red-400">{formatPct(worstPct)}</span>
              </div>
            </>
          );
        })()}
      </div>
    </div>
  );
}

// ============================================================
// PRICE OVERLAY CHART (token price with entry/exit markers)
// ============================================================

function PriceOverlayChart({ trades }: { trades: TradeRecord[] }) {
  const sortedTrades = useMemo(
    () => [...trades].sort((a, b) => new Date(a.entryTime).getTime() - new Date(b.entryTime).getTime()),
    [trades],
  );

  // Build price points from entry/exit prices
  const pricePoints = useMemo(() => {
    if (sortedTrades.length === 0) return [];
    const points: { time: number; price: number; type: 'entry' | 'exit'; trade: TradeRecord }[] = [];
    sortedTrades.forEach(trade => {
      points.push({ time: new Date(trade.entryTime).getTime(), price: trade.entryPrice, type: 'entry', trade });
      points.push({ time: new Date(trade.exitTime).getTime(), price: trade.exitPrice, type: 'exit', trade });
    });
    points.sort((a, b) => a.time - b.time);
    return points;
  }, [sortedTrades]);

  if (pricePoints.length === 0) return null;

  const W = 520;
  const H = 120;
  const padX = 5;
  const padY = 8;
  const chartW = W - padX * 2;
  const chartH = H - padY * 2;

  const minPrice = Math.min(...pricePoints.map(p => p.price)) * 0.98;
  const maxPrice = Math.max(...pricePoints.map(p => p.price)) * 1.02;
  const rangePrice = maxPrice - minPrice || 1;
  const minTime = pricePoints[0].time;
  const maxTime = pricePoints[pricePoints.length - 1].time;
  const rangeTime = maxTime - minTime || 1;

  const toX = (t: number) => padX + ((t - minTime) / rangeTime) * chartW;
  const toY = (p: number) => padY + chartH - ((p - minPrice) / rangePrice) * chartH;

  // Create a line path through all price points
  const linePath = pricePoints.map((p, i) =>
    `${i === 0 ? 'M' : 'L'} ${toX(p.time)} ${toY(p.price)}`
  ).join(' ');

  return (
    <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-3.5 w-3.5 text-[#d4af37]" />
          <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider">Price Overlay</span>
        </div>
        <div className="flex items-center gap-3 text-[8px] font-mono">
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full bg-emerald-400" /> Entry</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full bg-red-400" /> Exit</span>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: '120px' }}>
        {/* Price line connecting all points */}
        {pricePoints.length > 1 && (
          <path d={linePath} fill="none" stroke="#d4af37" strokeWidth="0.8" opacity="0.6" />
        )}

        {/* Entry markers */}
        {pricePoints.filter(p => p.type === 'entry').map((p, i) => (
          <g key={`pentry-${i}`}>
            <circle cx={toX(p.time)} cy={toY(p.price)} r="3.5" fill="#10b981" stroke="#0a0e17" strokeWidth="1" />
            <line x1={toX(p.time)} y1={toY(p.price) - 3.5} x2={toX(p.time)} y2={toY(p.price) - 8} stroke="#10b981" strokeWidth="0.5" />
          </g>
        ))}

        {/* Exit markers */}
        {pricePoints.filter(p => p.type === 'exit').map((p, i) => (
          <g key={`pexit-${i}`}>
            <circle cx={toX(p.time)} cy={toY(p.price)} r="3.5" fill="#ef4444" stroke="#0a0e17" strokeWidth="1" />
            <line x1={toX(p.time)} y1={toY(p.price) + 3.5} x2={toX(p.time)} y2={toY(p.price) + 8} stroke="#ef4444" strokeWidth="0.5" />
          </g>
        ))}

        {/* Connecting lines between entry/exit of same trade */}
        {sortedTrades.map((trade, i) => {
          const entryP = pricePoints.find(p => p.trade.id === trade.id && p.type === 'entry');
          const exitP = pricePoints.find(p => p.trade.id === trade.id && p.type === 'exit');
          if (!entryP || !exitP) return null;
          const isWin = (trade.pnlPct || 0) > 0;
          return (
            <line
              key={`conn-${i}`}
              x1={toX(entryP.time)} y1={toY(entryP.price)}
              x2={toX(exitP.time)} y2={toY(exitP.price)}
              stroke={isWin ? '#10b981' : '#ef4444'}
              strokeWidth="0.6"
              strokeDasharray="2,2"
              opacity="0.4"
            />
          );
        })}
      </svg>
    </div>
  );
}

// ============================================================
// WIN/LOSS DISTRIBUTION HISTOGRAM
// ============================================================

function PnlHistogram({ trades }: { trades: TradeRecord[] }) {
  const bins = useMemo(() => {
    if (trades.length === 0) return [];
    const pnls = trades.map(t => t.pnlPct || 0);
    const minVal = Math.min(...pnls);
    const maxVal = Math.max(...pnls);
    const range = maxVal - minVal || 1;
    const binCount = Math.min(12, Math.max(5, trades.length));
    const binWidth = range / binCount;

    const binsArr: { label: string; count: number; isWin: boolean; trades: TradeRecord[] }[] = [];
    for (let i = 0; i < binCount; i++) {
      const low = minVal + i * binWidth;
      const high = low + binWidth;
      const mid = (low + high) / 2;
      const tradesInBin = trades.filter(t => {
        const p = t.pnlPct || 0;
        return i === binCount - 1 ? p >= low && p <= high : p >= low && p < high;
      });
      binsArr.push({
        label: `${mid >= 0 ? '+' : ''}${mid.toFixed(1)}%`,
        count: tradesInBin.length,
        isWin: mid >= 0,
        trades: tradesInBin,
      });
    }
    return binsArr;
  }, [trades]);

  if (bins.length === 0) return null;

  const maxCount = Math.max(...bins.map(b => b.count), 1);
  const barW = 28;
  const chartH = 60;

  return (
    <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
      <div className="flex items-center gap-2 mb-2">
        <BarChart3 className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider">PnL Distribution</span>
      </div>

      <div className="flex items-end justify-center gap-1" style={{ height: `${chartH + 20}px` }}>
        {bins.map((bin, i) => {
          const h = (bin.count / maxCount) * chartH;
          return (
            <Tooltip key={i}>
              <TooltipTrigger asChild>
                <div className="flex flex-col items-center cursor-pointer">
                  <span className="text-[7px] font-mono text-[#64748b] mb-0.5">{bin.count}</span>
                  <div
                    className={`rounded-t-sm transition-all hover:opacity-80 ${
                      bin.isWin ? 'bg-emerald-500/60' : 'bg-red-500/60'
                    }`}
                    style={{ width: `${barW}px`, height: `${Math.max(h, 2)}px` }}
                  />
                  <span className="text-[6px] font-mono text-[#475569] mt-1 max-w-[32px] text-center leading-tight truncate">
                    {bin.label}
                  </span>
                </div>
              </TooltipTrigger>
              <TooltipContent
                className="bg-[#1a1f2e] text-[#e2e8f0] border border-[#2d3748] text-[9px] font-mono"
                side="top"
              >
                <div>{bin.label}: {bin.count} trade{bin.count !== 1 ? 's' : ''}</div>
                {bin.trades.length > 0 && (
                  <div className="mt-1 text-[8px] text-[#94a3b8]">
                    {bin.trades.slice(0, 3).map(t => t.tokenSymbol || t.tokenAddress.slice(0, 6)).join(', ')}
                    {bin.trades.length > 3 ? ` +${bin.trades.length - 3} more` : ''}
                  </div>
                )}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </div>
  );
}

// ============================================================
// DRAWDOWN CURVE
// ============================================================

function DrawdownChart({ trades }: { trades: TradeRecord[] }) {
  const sortedTrades = useMemo(
    () => [...trades].sort((a, b) => new Date(a.exitTime).getTime() - new Date(b.exitTime).getTime()),
    [trades],
  );

  const drawdownData = useMemo(() => {
    const pnlValues = sortedTrades.map(t => t.pnlUsd || 0);
    const cumulatives: number[] = [];
    const peaks: number[] = [];
    let runningPnl = 0;
    let runningPeak = 0;
    for (const pnl of pnlValues) {
      runningPnl += pnl;
      if (runningPnl > runningPeak) runningPeak = runningPnl;
      cumulatives.push(runningPnl);
      peaks.push(runningPeak);
    }
    return sortedTrades.map((trade, i) => {
      const dd = peaks[i] > 0 ? ((peaks[i] - cumulatives[i]) / peaks[i]) * 100 : 0;
      return { drawdown: Math.max(0, dd), trade };
    });
  }, [sortedTrades]);

  if (drawdownData.length === 0) return null;

  const W = 520;
  const H = 70;
  const padX = 5;
  const padY = 5;
  const chartW = W - padX * 2;
  const chartH = H - padY * 2;

  const maxDD = Math.max(...drawdownData.map(d => d.drawdown), 1);

  const toX = (i: number) => padX + (i / Math.max(drawdownData.length - 1, 1)) * chartW;
  const toY = (dd: number) => padY + (dd / maxDD) * chartH;

  const pathD = drawdownData.map((d, i) => `${i === 0 ? 'M' : 'L'} ${toX(i)} ${toY(d.drawdown)}`).join(' ');

  const maxDrawdown = Math.max(...drawdownData.map(d => d.drawdown));

  return (
    <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <TrendingDown className="h-3.5 w-3.5 text-red-400" />
          <span className="text-[10px] font-mono text-[#94a3b8] uppercase tracking-wider">Drawdown</span>
        </div>
        <span className="text-[9px] font-mono text-red-400">Max: {maxDrawdown.toFixed(1)}%</span>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: '70px' }}>
        {/* Area fill */}
        {drawdownData.length > 1 && (
          <path
            d={`${pathD} L ${toX(drawdownData.length - 1)} ${padY} L ${padX} ${padY} Z`}
            fill="url(#ddGrad)"
            opacity="0.4"
          />
        )}

        {/* Drawdown line */}
        {drawdownData.length > 1 && (
          <path d={pathD} fill="none" stroke="#ef4444" strokeWidth="1" opacity="0.8" />
        )}

        {/* Zero line (top) */}
        <line x1={padX} y1={padY} x2={W - padX} y2={padY} stroke="#2d3748" strokeWidth="0.3" strokeDasharray="1,1" />

        <defs>
          <linearGradient id="ddGrad" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#ef4444" stopOpacity="0" />
            <stop offset="100%" stopColor="#ef4444" stopOpacity="0.3" />
          </linearGradient>
        </defs>
      </svg>
    </div>
  );
}

// ============================================================
// MINI TRADE PRICE CHART (for detail modal)
// ============================================================

function MiniTradeChart({ trade }: { trade: TradeRecord }) {
  const entryP = trade.entryPrice;
  const exitP = trade.exitPrice;
  const isWin = (trade.pnlPct || 0) > 0;

  // Simulate a few price points between entry and exit for visual effect
  const points = useMemo(() => {
    const entryTime = new Date(trade.entryTime).getTime();
    const exitTime = new Date(trade.exitTime).getTime();
    const midTime = (entryTime + exitTime) / 2;
    const volatility = Math.abs(exitP - entryP) * 0.5;

    return [
      { time: entryTime, price: entryP },
      { time: midTime - (midTime - entryTime) * 0.3, price: entryP + (Math.random() - 0.4) * volatility },
      { time: midTime, price: (entryP + exitP) / 2 + (Math.random() - 0.5) * volatility * 0.5 },
      { time: midTime + (exitTime - midTime) * 0.3, price: exitP + (Math.random() - 0.6) * volatility },
      { time: exitTime, price: exitP },
    ];
  }, [trade, entryP, exitP]);

  const W = 280;
  const H = 80;
  const pad = 12;
  const chartW = W - pad * 2;
  const chartH = H - pad * 2;

  const minP = Math.min(...points.map(p => p.price)) * 0.99;
  const maxP = Math.max(...points.map(p => p.price)) * 1.01;
  const rangeP = maxP - minP || 1;
  const minT = points[0].time;
  const rangeT = points[points.length - 1].time - minT || 1;

  const toX = (t: number) => pad + ((t - minT) / rangeT) * chartW;
  const toY = (p: number) => pad + chartH - ((p - minP) / rangeP) * chartH;

  const linePath = points.map((p, i) =>
    `${i === 0 ? 'M' : 'L'} ${toX(p.time)} ${toY(p.price)}`
  ).join(' ');

  const entryY = toY(entryP);
  const exitY = toY(exitP);
  const entryX = toX(points[0].time);
  const exitX = toX(points[points.length - 1].time);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: '80px' }}>
      {/* Entry price line */}
      <line x1={pad} y1={entryY} x2={W - pad} y2={entryY} stroke="#10b981" strokeWidth="0.5" strokeDasharray="3,2" opacity="0.4" />

      {/* Exit price line */}
      <line x1={pad} y1={exitY} x2={W - pad} y2={exitY} stroke="#ef4444" strokeWidth="0.5" strokeDasharray="3,2" opacity="0.4" />

      {/* Area */}
      <path
        d={`${linePath} L ${exitX} ${pad + chartH} L ${entryX} ${pad + chartH} Z`}
        fill={isWin ? '#10b981' : '#ef4444'}
        opacity="0.1"
      />

      {/* Line */}
      <path d={linePath} fill="none" stroke={isWin ? '#10b981' : '#ef4444'} strokeWidth="1.5" />

      {/* Entry marker */}
      <circle cx={entryX} cy={entryY} r="4" fill="#10b981" stroke="#0a0e17" strokeWidth="1.5" />
      <text x={entryX} y={entryY - 8} textAnchor="middle" fill="#10b981" fontSize="7" fontFamily="monospace">
        Entry ${entryP.toFixed(4)}
      </text>

      {/* Exit marker */}
      <circle cx={exitX} cy={exitY} r="4" fill="#ef4444" stroke="#0a0e17" strokeWidth="1.5" />
      <text x={exitX} y={exitY + 14} textAnchor="middle" fill="#ef4444" fontSize="7" fontFamily="monospace">
        Exit ${exitP.toFixed(4)}
      </text>
    </svg>
  );
}

// ============================================================
// TRADE DETAIL MODAL
// ============================================================

function TradeDetailModal({
  trade,
  open,
  onOpenChange,
}: {
  trade: TradeRecord | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!trade) return null;

  const isWin = (trade.pnlPct || 0) > 0;
  const exitInfo = getExitReasonLabel(trade.exitReason);
  const pnlUsd = trade.pnlUsd || 0;
  const pnlPct = trade.pnlPct || 0;
  const holdTime = trade.holdTimeMin;

  // Calculate approximate fees
  const entryFee = (trade.entryPrice * 0.003).toFixed(4);
  const exitFee = (trade.exitPrice * 0.003).toFixed(4);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] max-w-lg p-0 overflow-hidden">
        <DialogHeader className="px-5 pt-5 pb-3 border-b border-[#1e293b]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {isWin ? (
                <ArrowUpRight className="h-5 w-5 text-emerald-400" />
              ) : (
                <ArrowDownRight className="h-5 w-5 text-red-400" />
              )}
              <div>
                <DialogTitle className="text-base font-mono font-bold text-[#e2e8f0]">
                  {trade.tokenSymbol || trade.tokenAddress.slice(0, 12)}
                </DialogTitle>
                <DialogDescription className="text-[10px] font-mono text-[#64748b]">
                  Trade Detail • {trade.id.slice(0, 12)}
                </DialogDescription>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Badge className={`text-[9px] h-5 px-2 font-mono border-0 ${
                trade.direction === 'LONG' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
              }`}>
                {trade.direction}
              </Badge>
              <Badge className="text-[8px] h-5 px-2 font-mono bg-[#1a1f2e] text-[#64748b] border-0">
                {trade.mode}
              </Badge>
            </div>
          </div>
        </DialogHeader>

        <div className="px-5 py-4 space-y-4">
          {/* PnL Header */}
          <div className={`rounded-lg p-3 ${isWin ? 'bg-emerald-500/10 border border-emerald-500/20' : 'bg-red-500/10 border border-red-500/20'}`}>
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-mono text-[#94a3b8] uppercase">Profit / Loss</span>
              <div className="flex items-center gap-3">
                <span className={`text-lg font-mono font-bold ${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                  {formatPnl(pnlUsd)}
                </span>
                <span className={`text-sm font-mono ${isWin ? 'text-emerald-400/70' : 'text-red-400/70'}`}>
                  {formatPct(pnlPct)}
                </span>
              </div>
            </div>
          </div>

          {/* Mini chart */}
          <div className="bg-[#0d1117] rounded-lg p-3 border border-[#1a1f2e]">
            <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Price Movement</span>
            <MiniTradeChart trade={trade} />
          </div>

          {/* Entry / Exit Details */}
          <div className="grid grid-cols-2 gap-3">
            {/* Entry */}
            <div className="bg-[#0d1117] rounded-lg p-3 border border-emerald-500/20">
              <div className="flex items-center gap-1.5 mb-2">
                <TrendingUp className="h-3 w-3 text-emerald-400" />
                <span className="text-[9px] font-mono text-emerald-400 uppercase font-bold">Entry</span>
              </div>
              <div className="space-y-1.5">
                <div className="flex justify-between text-[9px] font-mono">
                  <span className="text-[#64748b]">Price</span>
                  <span className="text-[#e2e8f0]">${trade.entryPrice.toFixed(6)}</span>
                </div>
                <div className="flex justify-between text-[9px] font-mono">
                  <span className="text-[#64748b]">Time</span>
                  <span className="text-[#e2e8f0]">{formatTimeFull(trade.entryTime)}</span>
                </div>
              </div>
            </div>

            {/* Exit */}
            <div className="bg-[#0d1117] rounded-lg p-3 border border-red-500/20">
              <div className="flex items-center gap-1.5 mb-2">
                <TrendingDown className="h-3 w-3 text-red-400" />
                <span className="text-[9px] font-mono text-red-400 uppercase font-bold">Exit</span>
              </div>
              <div className="space-y-1.5">
                <div className="flex justify-between text-[9px] font-mono">
                  <span className="text-[#64748b]">Price</span>
                  <span className="text-[#e2e8f0]">${trade.exitPrice.toFixed(6)}</span>
                </div>
                <div className="flex justify-between text-[9px] font-mono">
                  <span className="text-[#64748b]">Time</span>
                  <span className="text-[#e2e8f0]">{formatTimeFull(trade.exitTime)}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Trade Details Grid */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 bg-[#0d1117] rounded-lg p-3 border border-[#1a1f2e]">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <Timer className="h-3 w-3 text-[#64748b]" />
                <span className="text-[9px] font-mono text-[#64748b]">Hold Time</span>
              </div>
              <span className="text-[9px] font-mono text-[#e2e8f0]">{formatDuration(holdTime)}</span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <Tag className="h-3 w-3 text-[#64748b]" />
                <span className="text-[9px] font-mono text-[#64748b]">Exit Reason</span>
              </div>
              <span className={`text-[9px] font-mono ${exitInfo.color}`}>{exitInfo.label}</span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <DollarSign className="h-3 w-3 text-[#64748b]" />
                <span className="text-[9px] font-mono text-[#64748b]">Est. Entry Fee</span>
              </div>
              <span className="text-[9px] font-mono text-[#94a3b8]">${entryFee}</span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <DollarSign className="h-3 w-3 text-[#64748b]" />
                <span className="text-[9px] font-mono text-[#64748b]">Est. Exit Fee</span>
              </div>
              <span className="text-[9px] font-mono text-[#94a3b8]">${exitFee}</span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <Percent className="h-3 w-3 text-[#64748b]" />
                <span className="text-[9px] font-mono text-[#64748b]">Price Change</span>
              </div>
              <span className={`text-[9px] font-mono ${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                {((trade.exitPrice - trade.entryPrice) / trade.entryPrice * 100).toFixed(2)}%
              </span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <Settings2 className="h-3 w-3 text-[#64748b]" />
                <span className="text-[9px] font-mono text-[#64748b]">Strategy</span>
              </div>
              <span className="text-[9px] font-mono text-[#94a3b8] max-w-[120px] truncate">{trade.systemName}</span>
            </div>
          </div>

          {/* Token Info */}
          <div className="bg-[#0d1117] rounded-lg p-3 border border-[#1a1f2e]">
            <div className="flex items-center justify-between text-[9px] font-mono">
              <span className="text-[#64748b]">Token Address</span>
              <span className="text-[#94a3b8] truncate max-w-[280px]">{trade.tokenAddress}</span>
            </div>
            <div className="flex items-center justify-between text-[9px] font-mono mt-1">
              <span className="text-[#64748b]">System ID</span>
              <span className="text-[#94a3b8] truncate max-w-[200px]">{trade.systemId}</span>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function TradeHistoryPanel() {
  const [filterMode, setFilterMode] = useState<string>('ALL');
  const [filterDirection, setFilterDirection] = useState<string>('ALL');
  const [filterStrategy, setFilterStrategy] = useState<string>('ALL');
  const [tab, setTab] = useState<'history' | 'positions'>('history');
  const [selectedTrade, setSelectedTrade] = useState<TradeRecord | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const { data: tradeHistory, refetch: refetchHistory } = useQuery({
    queryKey: ['trade-history'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/execution/history?limit=100');
        if (!res.ok) return [];
        const json = await res.json();
        return (json.data || []) as TradeRecord[];
      } catch {
        return [];
      }
    },
    staleTime: 10000,
  });

  const { data: openPositions, refetch: refetchPositions } = useQuery({
    queryKey: ['open-positions'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/execution/positions');
        if (!res.ok) return [];
        const json = await res.json();
        return (json.data || []) as OpenPosition[];
      } catch {
        return [];
      }
    },
    staleTime: 10000,
  });

  const trades = tradeHistory || [];
  const positions = openPositions || [];

  // Unique strategy names for filter
  const strategyNames = useMemo(
    () => Array.from(new Set(trades.map(t => t.systemName))).sort(),
    [trades],
  );

  // Filter trades
  const filteredTrades = useMemo(() => {
    return trades.filter(t => {
      if (filterMode !== 'ALL' && t.mode !== filterMode) return false;
      if (filterDirection !== 'ALL' && t.direction !== filterDirection) return false;
      if (filterStrategy !== 'ALL' && t.systemName !== filterStrategy) return false;
      return true;
    });
  }, [trades, filterMode, filterDirection, filterStrategy]);

  const handleRefresh = () => {
    refetchHistory();
    refetchPositions();
  };

  const handleTradeClick = useCallback((trade: TradeRecord) => {
    setSelectedTrade(trade);
    setDetailOpen(true);
  }, []);

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <div className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-[#d4af37]" />
          <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">TRADE HISTORY</span>
        </div>
        <div className="flex items-center gap-2">
          {/* Tab switcher */}
          <div className="flex bg-[#111827] border border-[#1e293b] rounded-md overflow-hidden">
            <button
              onClick={() => setTab('history')}
              className={`px-2.5 py-1 text-[9px] font-mono transition-all ${
                tab === 'history' ? 'bg-[#d4af37]/15 text-[#d4af37]' : 'text-[#64748b] hover:text-[#94a3b8]'
              }`}
            >
              History ({trades.length})
            </button>
            <button
              onClick={() => setTab('positions')}
              className={`px-2.5 py-1 text-[9px] font-mono transition-all ${
                tab === 'positions' ? 'bg-[#d4af37]/15 text-[#d4af37]' : 'text-[#64748b] hover:text-[#94a3b8]'
              }`}
            >
              Open ({positions.length})
            </button>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleRefresh}
            className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]"
          >
            <RefreshCw className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {/* Content */}
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          <AnimatePresence mode="wait">
            {tab === 'history' ? (
              <motion.div key="history" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="space-y-4">
                {/* Charts Section */}
                {filteredTrades.length > 0 && (
                  <>
                    {/* Equity Curve with Entry/Exit markers */}
                    <EquityCurveChart trades={filteredTrades} />

                    {/* Price Overlay + Drawdown side by side on larger screens */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                      <PriceOverlayChart trades={filteredTrades} />
                      <DrawdownChart trades={filteredTrades} />
                    </div>

                    {/* PnL Distribution Histogram */}
                    <PnlHistogram trades={filteredTrades} />
                  </>
                )}

                {/* Filters */}
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="flex items-center gap-1">
                    <span className="text-[8px] font-mono text-[#475569]">Mode:</span>
                    <Select value={filterMode} onValueChange={setFilterMode}>
                      <SelectTrigger className="h-5 w-20 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#94a3b8]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                        <SelectItem value="ALL" className="text-[9px] font-mono">All</SelectItem>
                        <SelectItem value="HISTORICAL" className="text-[9px] font-mono">Backtest</SelectItem>
                        <SelectItem value="PAPER" className="text-[9px] font-mono">Paper</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="text-[8px] font-mono text-[#475569]">Dir:</span>
                    <Select value={filterDirection} onValueChange={setFilterDirection}>
                      <SelectTrigger className="h-5 w-16 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#94a3b8]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                        <SelectItem value="ALL" className="text-[9px] font-mono">All</SelectItem>
                        <SelectItem value="LONG" className="text-[9px] font-mono">Long</SelectItem>
                        <SelectItem value="SHORT" className="text-[9px] font-mono">Short</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  {strategyNames.length > 1 && (
                    <div className="flex items-center gap-1">
                      <span className="text-[8px] font-mono text-[#475569]">Strategy:</span>
                      <Select value={filterStrategy} onValueChange={setFilterStrategy}>
                        <SelectTrigger className="h-5 w-28 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#94a3b8]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                          <SelectItem value="ALL" className="text-[9px] font-mono">All Strategies</SelectItem>
                          {strategyNames.map(name => (
                            <SelectItem key={name} value={name} className="text-[9px] font-mono truncate max-w-[200px]">
                              {name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                </div>

                {/* Trade List */}
                {filteredTrades.length === 0 ? (
                  <div className="flex flex-col items-center py-12 text-[#64748b]">
                    <BarChart3 className="h-10 w-10 mb-3 text-[#2d3748]" />
                    <span className="font-mono text-sm">No trade history yet</span>
                    <span className="font-mono text-[10px] text-[#475569] mt-1">Run backtests or activate paper trading to see results</span>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {filteredTrades.map((trade) => {
                      const isWin = (trade.pnlPct || 0) > 0;
                      const exitInfo = getExitReasonLabel(trade.exitReason);

                      return (
                        <motion.div
                          key={trade.id}
                          initial={{ opacity: 0, x: -5 }}
                          animate={{ opacity: 1, x: 0 }}
                          onClick={() => handleTradeClick(trade)}
                          className={`bg-[#111827] border rounded-lg p-3 transition-all hover:border-[#d4af37]/40 cursor-pointer group ${
                            isWin ? 'border-emerald-500/20' : 'border-red-500/20'
                          }`}
                        >
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              {isWin ? (
                                <ArrowUpRight className="h-3.5 w-3.5 text-emerald-400" />
                              ) : (
                                <ArrowDownRight className="h-3.5 w-3.5 text-red-400" />
                              )}
                              <span className="font-mono text-xs font-bold text-[#e2e8f0] max-w-[180px] truncate">
                                {trade.tokenSymbol || trade.tokenAddress.slice(0, 8)}
                              </span>
                              <Badge className={`text-[7px] h-3.5 px-1 font-mono border-0 ${
                                trade.direction === 'LONG' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                              }`}>
                                {trade.direction}
                              </Badge>
                              <Badge className="text-[7px] h-3.5 px-1 font-mono bg-[#1a1f2e] text-[#64748b] border-0">
                                {trade.mode}
                              </Badge>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className={`font-mono text-xs font-bold ${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                                {formatPct(trade.pnlPct || 0)}
                              </span>
                              <span className={`font-mono text-[10px] ${isWin ? 'text-emerald-400/70' : 'text-red-400/70'}`}>
                                {formatPnl(trade.pnlUsd || 0)}
                              </span>
                              <ChevronRight className="h-3 w-3 text-[#475569] group-hover:text-[#d4af37] transition-colors" />
                            </div>
                          </div>

                          <div className="grid grid-cols-5 gap-2 text-[9px] font-mono">
                            <div>
                              <span className="text-[#475569] uppercase block">Entry</span>
                              <span className="text-[#94a3b8]">${trade.entryPrice.toFixed(4)}</span>
                            </div>
                            <div>
                              <span className="text-[#475569] uppercase block">Exit</span>
                              <span className="text-[#94a3b8]">${trade.exitPrice.toFixed(4)}</span>
                            </div>
                            <div>
                              <span className="text-[#475569] uppercase block">Hold</span>
                              <span className="text-[#94a3b8]">{formatDuration(trade.holdTimeMin)}</span>
                            </div>
                            <div>
                              <span className="text-[#475569] uppercase block">Exit</span>
                              <span className={exitInfo.color}>{exitInfo.label}</span>
                            </div>
                            <div>
                              <span className="text-[#475569] uppercase block">Time</span>
                              <span className="text-[#94a3b8]">{formatTime(trade.exitTime)}</span>
                            </div>
                          </div>

                          <div className="mt-1.5 text-[8px] font-mono text-[#475569]">
                            {trade.systemName}
                          </div>
                        </motion.div>
                      );
                    })}
                  </div>
                )}
              </motion.div>
            ) : (
              <motion.div key="positions" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="space-y-4">
                {/* Live Positions Monitor */}
                {positions.length > 0 && (
                  <div className="bg-[#0d1117] border border-[#d4af37]/20 rounded-lg p-3">
                    <div className="flex items-center gap-2 mb-3">
                      <Activity className="h-3.5 w-3.5 text-[#d4af37]" />
                      <span className="text-[10px] font-mono text-[#d4af37] uppercase tracking-wider font-bold">Live Positions Monitor</span>
                      <span className="relative flex h-2 w-2 ml-1">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#d4af37]/50 opacity-75" />
                        <span className="relative inline-flex rounded-full h-2 w-2 bg-[#d4af37]" />
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                      {/* Total Unrealized PnL */}
                      <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-2.5">
                        <span className="text-[8px] font-mono text-[#64748b] uppercase block mb-1">Total Unrealized PnL</span>
                        {(() => {
                          const totalUnrealized = positions.reduce((sum, p) => sum + p.unrealizedPnl, 0);
                          return (
                            <span className={`text-[12px] font-mono font-bold ${totalUnrealized >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {formatPnl(totalUnrealized)}
                            </span>
                          );
                        })()}
                      </div>
                      {/* Average Hold Time */}
                      <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-2.5">
                        <span className="text-[8px] font-mono text-[#64748b] uppercase block mb-1">Avg Hold Time</span>
                        {(() => {
                          const now = Date.now();
                          const avgMs = positions.reduce((sum, p) => sum + (now - new Date(p.entryTime).getTime()), 0) / Math.max(positions.length, 1);
                          const avgMin = avgMs / 60000;
                          return (
                            <span className="text-[12px] font-mono font-bold text-[#e2e8f0]">
                              {formatDuration(avgMin)}
                            </span>
                          );
                        })()}
                      </div>
                      {/* Direction Distribution */}
                      <div className="bg-[#0a0e17] border border-[#1e293b] rounded-md p-2.5">
                        <span className="text-[8px] font-mono text-[#64748b] uppercase block mb-1">Direction</span>
                        <div className="flex items-center gap-2">
                          {(() => {
                            const longCount = positions.filter(p => p.direction === 'LONG').length;
                            const shortCount = positions.filter(p => p.direction === 'SHORT').length;
                            return (
                              <>
                                <span className="text-[10px] font-mono font-bold text-emerald-400">
                                  {longCount} LONG
                                </span>
                                <span className="text-[8px] font-mono text-[#475569]">|</span>
                                <span className="text-[10px] font-mono font-bold text-red-400">
                                  {shortCount} SHORT
                                </span>
                              </>
                            );
                          })()}
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {positions.length === 0 ? (
                  <div className="flex flex-col items-center py-12 text-[#64748b]">
                    <Target className="h-10 w-10 mb-3 text-[#2d3748]" />
                    <span className="font-mono text-sm">No open positions</span>
                    <span className="font-mono text-[10px] text-[#475569] mt-1">Activate strategies to start paper trading</span>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {positions.map((pos) => (
                      <div key={pos.backtestId} className="bg-[#111827] border border-[#d4af37]/20 rounded-lg p-3">
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-2">
                            <DollarSign className="h-3.5 w-3.5 text-[#d4af37]" />
                            <span className="font-mono text-xs font-bold text-[#e2e8f0]">
                              {pos.tokenSymbol || pos.tokenAddress.slice(0, 8)}
                            </span>
                            <Badge className={`text-[7px] h-3.5 px-1 font-mono border-0 ${
                              pos.direction === 'LONG' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                            }`}>
                              {pos.direction}
                            </Badge>
                          </div>
                          <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#d4af37]/15 text-[#d4af37] border border-[#d4af37]/30">
                            OPEN
                          </Badge>
                        </div>
                        <div className="grid grid-cols-4 gap-2 text-[9px] font-mono">
                          <div>
                            <span className="text-[#475569] uppercase block">Entry</span>
                            <span className="text-[#94a3b8]">${pos.entryPrice.toFixed(4)}</span>
                          </div>
                          <div>
                            <span className="text-[#475569] uppercase block">Size</span>
                            <span className="text-[#94a3b8]">${pos.positionSizeUsd.toFixed(0)}</span>
                          </div>
                          <div>
                            <span className="text-[#475569] uppercase block">Qty</span>
                            <span className="text-[#94a3b8]">{pos.quantity.toFixed(2)}</span>
                          </div>
                          <div>
                            <span className="text-[#475569] uppercase block">Since</span>
                            <span className="text-[#94a3b8]">{formatTime(pos.entryTime)}</span>
                          </div>
                        </div>
                        <div className="mt-1.5 text-[8px] font-mono text-[#475569]">
                          {pos.systemName}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </ScrollArea>

      {/* Trade Detail Modal */}
      <TradeDetailModal
        trade={selectedTrade}
        open={detailOpen}
        onOpenChange={setDetailOpen}
      />
    </div>
  );
}
