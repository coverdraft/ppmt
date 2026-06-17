'use client';

import { useState, useCallback, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ComposedChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Customized,
} from 'recharts';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  CandlestickChart,
  TrendingUp,
  TrendingDown,
  Activity,
  BarChart3,
  RefreshCw,
  Crosshair,
} from 'lucide-react';

// ============================================================
// TYPES
// ============================================================

interface OHLCVCandle {
  time: string;
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface OHLCVResponse {
  candles: OHLCVCandle[];
  timeframe?: string;
  source?: string;
  count?: number;
  fallback?: boolean;
  requestedTimeframe?: string;
}

export interface TradeMarker {
  entryTime: string | Date;
  exitTime?: string | Date;
  entryPrice: number;
  exitPrice?: number;
  direction: 'LONG' | 'SHORT';
  pnlPct?: number;
  /** Optional label (e.g. token symbol or system name) */
  label?: string;
  /** Optional unique id for the trade */
  id?: string;
}

interface OHLCVChartProps {
  tokenAddress: string;
  chain?: string;
  timeframes?: string[];
  /** Trade markers to overlay on the chart. If omitted, trades are fetched from /api/execution/history */
  trades?: TradeMarker[];
}

// ============================================================
// CONFIG
// ============================================================

const ALL_TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];

const THEME = {
  bg: '#0d1117',
  panelBg: '#0a0e17',
  border: '#1e293b',
  gold: '#d4af37',
  green: '#10b981',
  red: '#ef4444',
  textPrimary: '#e2e8f0',
  textSecondary: '#94a3b8',
  textMuted: '#64748b',
  volumeUp: 'rgba(16, 185, 129, 0.25)',
  volumeDown: 'rgba(239, 68, 68, 0.25)',
  gridLine: '#1a1f2e',
} as const;

// ============================================================
// HELPERS
// ============================================================

function formatPrice(price: number): string {
  if (price >= 1000) return `$${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (price >= 1) return `$${price.toFixed(2)}`;
  if (price >= 0.001) return `$${price.toFixed(4)}`;
  if (price >= 0.00001) return `$${price.toFixed(6)}`;
  return `$${price.toFixed(8)}`;
}

function formatVolume(vol: number): string {
  if (vol >= 1_000_000) return `$${(vol / 1_000_000).toFixed(2)}M`;
  if (vol >= 1_000) return `$${(vol / 1_000).toFixed(1)}K`;
  return `$${vol.toFixed(0)}`;
}

function formatChange(pct: number): string {
  const sign = pct >= 0 ? '+' : '';
  return `${sign}${pct.toFixed(2)}%`;
}

function formatTimeLabel(timestamp: number, timeframe: string): string {
  const d = new Date(timestamp);
  if (['1m', '3m', '5m', '15m', '30m'].includes(timeframe)) {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  }
  if (['1h', '4h'].includes(timeframe)) {
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
      ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/** Find the X pixel position for a given timestamp within the chart's categorical X axis */
function findXForTimestamp(
  targetTimestamp: number,
  chartData: OHLCVCandle[],
  xScale: (value: string) => number,
  bandWidth: number,
): number | null {
  if (chartData.length === 0) return null;

  // Find the two adjacent candles surrounding the target timestamp
  let lo = 0;
  let hi = chartData.length - 1;

  // Before the first candle
  if (targetTimestamp <= chartData[0].timestamp) {
    const x = xScale(chartData[0].time) + bandWidth / 2;
    return x;
  }
  // After the last candle
  if (targetTimestamp >= chartData[chartData.length - 1].timestamp) {
    const x = xScale(chartData[chartData.length - 1].time) + bandWidth / 2;
    return x;
  }

  // Binary search for the interval
  while (lo < hi - 1) {
    const mid = Math.floor((lo + hi) / 2);
    if (chartData[mid].timestamp <= targetTimestamp) {
      lo = mid;
    } else {
      hi = mid;
    }
  }

  const loCandle = chartData[lo];
  const hiCandle = chartData[hi];
  const loX = xScale(loCandle.time) + bandWidth / 2;
  const hiX = xScale(hiCandle.time) + bandWidth / 2;

  const fraction = (targetTimestamp - loCandle.timestamp) / (hiCandle.timestamp - loCandle.timestamp);
  return loX + fraction * (hiX - loX);
}

// ============================================================
// CUSTOM CANDLESTICK SVG RENDERER
// ============================================================

interface CandlestickRenderProps {
  xAxisMap?: Record<string, { scale: (value: string) => number; bandwidth?: () => number }>;
  yAxisMap?: Record<string, { scale: (value: number) => number; yAxisId?: string }>;
  formattedGraphicalItems?: unknown[];
  offset?: { top: number; right: number; bottom: number; left: number };
  data?: OHLCVCandle[];
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CandlestickSeries(props: any) {
  const { xAxisMap, yAxisMap, data } = props as CandlestickRenderProps;

  if (!xAxisMap || !yAxisMap || !data || data.length === 0) return null;

  const xAxis = Object.values(xAxisMap)[0];
  const yAxis = Object.values(yAxisMap).find(
    (y) => y.yAxisId === 'price'
  );

  if (!xAxis || !yAxis) return null;

  const xScale = xAxis.scale;
  const yScale = yAxis.scale;

  // Determine bandwidth for bar-like positioning
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const bandWidth: number =
    typeof (xScale as any).bandwidth === 'function' ? (xScale as any).bandwidth() : 10;
  /* eslint-enable @typescript-eslint/no-explicit-any */

  return (
    <g className="recharts-candlestick-series">
      {data.map((entry: OHLCVCandle, index: number) => {
        const xCenter = (xScale as (value: string) => number)(entry.time) + bandWidth / 2;
        const yHigh = yScale(entry.high);
        const yLow = yScale(entry.low);
        const yOpen = yScale(entry.open);
        const yClose = yScale(entry.close);

        const isGreen = entry.close >= entry.open;
        const color = isGreen ? THEME.green : THEME.red;
        const bodyTop = Math.min(yOpen, yClose);
        const bodyBottom = Math.max(yOpen, yClose);
        const bodyHeight = Math.max(bodyBottom - bodyTop, 1);
        const candleWidth = Math.max(bandWidth * 0.6, 2);

        return (
          <g key={`candle-${index}`}>
            {/* Wick (high-low line) */}
            <line
              x1={xCenter}
              y1={yHigh}
              x2={xCenter}
              y2={yLow}
              stroke={color}
              strokeWidth={1}
            />
            {/* Body (open-close rectangle) */}
            <rect
              x={xCenter - candleWidth / 2}
              y={bodyTop}
              width={candleWidth}
              height={bodyHeight}
              fill={isGreen ? color : color}
              stroke={color}
              strokeWidth={0.5}
              opacity={isGreen ? 0.9 : 0.95}
            />
          </g>
        );
      })}
    </g>
  );
}

// ============================================================
// TRADE MARKERS OVERLAY
// ============================================================

interface TradeMarkersRenderProps extends CandlestickRenderProps {
  trades?: TradeMarker[];
  chartData?: OHLCVCandle[];
  onHoverTrade?: (trade: TradeMarker | null, x?: number, y?: number) => void;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function TradeMarkersOverlay(props: any) {
  const { xAxisMap, yAxisMap, trades, chartData, onHoverTrade } = props as TradeMarkersRenderProps;

  if (!xAxisMap || !yAxisMap || !trades || trades.length === 0 || !chartData || chartData.length === 0) return null;

  const xAxis = Object.values(xAxisMap)[0];
  const yAxis = Object.values(yAxisMap).find(
    (y) => y.yAxisId === 'price'
  );

  if (!xAxis || !yAxis) return null;

  const xScale = xAxis.scale;
  const yScale = yAxis.scale;

  /* eslint-disable @typescript-eslint/no-explicit-any */
  const bandWidth: number =
    typeof (xScale as any).bandwidth === 'function' ? (xScale as any).bandwidth() : 10;
  /* eslint-enable @typescript-eslint/no-explicit-any */

  const MARKER_SIZE = 7;
  const HIT_SIZE = 16;

  // Pre-compute marker positions
  const markers: {
    trade: TradeMarker;
    entryX: number | null;
    entryY: number | null;
    exitX: number | null;
    exitY: number | null;
  }[] = [];

  for (const trade of trades) {
    const entryTs = new Date(trade.entryTime).getTime();
    const entryX = findXForTimestamp(entryTs, chartData, xScale as (v: string) => number, bandWidth);
    const entryY = yScale(trade.entryPrice);

    let exitX: number | null = null;
    let exitY: number | null = null;
    if (trade.exitTime && trade.exitPrice != null) {
      const exitTs = new Date(trade.exitTime).getTime();
      exitX = findXForTimestamp(exitTs, chartData, xScale as (v: string) => number, bandWidth);
      exitY = yScale(trade.exitPrice);
    }

    markers.push({ trade, entryX, entryY, exitX, exitY });
  }

  return (
    <g className="recharts-trade-markers">
      {/* SVG Filters for glow effects */}
      <defs>
        <filter id="tradeEntryGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feFlood floodColor="#10b981" floodOpacity="0.6" result="color" />
          <feComposite in="color" in2="blur" operator="in" result="glow" />
          <feMerge>
            <feMergeNode in="glow" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="tradeExitGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feFlood floodColor="#ef4444" floodOpacity="0.6" result="color" />
          <feComposite in="color" in2="blur" operator="in" result="glow" />
          <feMerge>
            <feMergeNode in="glow" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Connecting lines (entry → exit) */}
      {markers.map((m, i) => {
        if (m.entryX == null || m.entryY == null || m.exitX == null || m.exitY == null) return null;
        const isProfitable = (m.trade.pnlPct ?? 0) >= 0;
        const lineColor = isProfitable ? THEME.green : THEME.red;
        return (
          <line
            key={`trade-line-${i}`}
            x1={m.entryX}
            y1={m.entryY}
            x2={m.exitX}
            y2={m.exitY}
            stroke={lineColor}
            strokeWidth={1}
            strokeDasharray="4 3"
            opacity={0.5}
          />
        );
      })}

      {/* Entry markers — green triangle pointing UP */}
      {markers.map((m, i) => {
        if (m.entryX == null || m.entryY == null) return null;
        const cx = m.entryX;
        const cy = m.entryY;
        const s = MARKER_SIZE;
        // Triangle pointing UP: top vertex, bottom-left, bottom-right
        const points = `${cx},${cy - s * 1.5} ${cx - s},${cy + s * 0.5} ${cx + s},${cy + s * 0.5}`;
        return (
          <g key={`trade-entry-${i}`}>
            {/* Glow */}
            <polygon
              points={points}
              fill={THEME.green}
              opacity={0.3}
              filter="url(#tradeEntryGlow)"
            />
            {/* Solid marker */}
            <polygon
              points={points}
              fill={THEME.green}
              opacity={0.9}
              stroke={THEME.green}
              strokeWidth={0.5}
            />
            {/* Invisible hit area for hover */}
            <rect
              x={cx - HIT_SIZE / 2}
              y={cy - HIT_SIZE / 2}
              width={HIT_SIZE}
              height={HIT_SIZE}
              fill="transparent"
              className="cursor-pointer"
              onMouseEnter={() => onHoverTrade?.(m.trade, cx, cy)}
              onMouseLeave={() => onHoverTrade?.(null)}
            />
          </g>
        );
      })}

      {/* Exit markers — red triangle pointing DOWN */}
      {markers.map((m, i) => {
        if (m.exitX == null || m.exitY == null) return null;
        const cx = m.exitX;
        const cy = m.exitY;
        const s = MARKER_SIZE;
        // Triangle pointing DOWN: bottom vertex, top-left, top-right
        const points = `${cx},${cy + s * 1.5} ${cx - s},${cy - s * 0.5} ${cx + s},${cy - s * 0.5}`;
        return (
          <g key={`trade-exit-${i}`}>
            {/* Glow */}
            <polygon
              points={points}
              fill={THEME.red}
              opacity={0.3}
              filter="url(#tradeExitGlow)"
            />
            {/* Solid marker */}
            <polygon
              points={points}
              fill={THEME.red}
              opacity={0.9}
              stroke={THEME.red}
              strokeWidth={0.5}
            />
            {/* Invisible hit area for hover */}
            <rect
              x={cx - HIT_SIZE / 2}
              y={cy - HIT_SIZE / 2}
              width={HIT_SIZE}
              height={HIT_SIZE}
              fill="transparent"
              className="cursor-pointer"
              onMouseEnter={() => onHoverTrade?.(m.trade, cx, cy)}
              onMouseLeave={() => onHoverTrade?.(null)}
            />
          </g>
        );
      })}
    </g>
  );
}

// ============================================================
// TRADE MARKER TOOLTIP
// ============================================================

function TradeMarkerTooltip({ trade }: { trade: TradeMarker }) {
  const isProfitable = (trade.pnlPct ?? 0) >= 0;
  const pnlColor = trade.pnlPct != null
    ? isProfitable ? 'text-emerald-400' : 'text-red-400'
    : 'text-[#94a3b8]';

  const entryTimeStr = (() => {
    try {
      return new Date(trade.entryTime).toLocaleString('en-US', {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch { return String(trade.entryTime); }
  })();

  const exitTimeStr = trade.exitTime
    ? (() => {
        try {
          return new Date(trade.exitTime).toLocaleString('en-US', {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
          });
        } catch { return String(trade.exitTime); }
      })()
    : '—';

  return (
    <div className="bg-[#111827] border border-[#1e293b] rounded-md px-3 py-2 shadow-xl pointer-events-none">
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`text-[9px] font-mono font-bold uppercase px-1.5 py-0.5 rounded ${
          trade.direction === 'LONG'
            ? 'bg-emerald-500/15 text-emerald-400'
            : 'bg-red-500/15 text-red-400'
        }`}>
          {trade.direction}
        </span>
        {trade.label && (
          <span className="text-[9px] font-mono text-[#94a3b8]">{trade.label}</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
        <span className="text-[10px] font-mono text-[#64748b]">Entry Price</span>
        <span className="mono-data text-[10px] text-emerald-400 text-right">{formatPrice(trade.entryPrice)}</span>
        <span className="text-[10px] font-mono text-[#64748b]">Entry Time</span>
        <span className="mono-data text-[10px] text-[#e2e8f0] text-right">{entryTimeStr}</span>
        {trade.exitPrice != null && (
          <>
            <span className="text-[10px] font-mono text-[#64748b]">Exit Price</span>
            <span className="mono-data text-[10px] text-red-400 text-right">{formatPrice(trade.exitPrice)}</span>
          </>
        )}
        {trade.exitTime && (
          <>
            <span className="text-[10px] font-mono text-[#64748b]">Exit Time</span>
            <span className="mono-data text-[10px] text-[#e2e8f0] text-right">{exitTimeStr}</span>
          </>
        )}
        {trade.pnlPct != null && (
          <>
            <span className="text-[10px] font-mono text-[#64748b]">PnL</span>
            <span className={`mono-data text-[10px] font-bold text-right ${pnlColor}`}>
              {isProfitable ? '+' : ''}{trade.pnlPct.toFixed(2)}%
            </span>
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================
// CUSTOM TOOLTIP
// ============================================================

function CustomTooltipContent({ active, payload }: {
  active?: boolean;
  payload?: Array<{ payload: OHLCVCandle }>;
}) {
  if (!active || !payload || payload.length === 0) return null;

  const candle = payload[0].payload as OHLCVCandle;
  const isGreen = candle.close >= candle.open;

  return (
    <div className="bg-[#111827] border border-[#1e293b] rounded-md px-3 py-2 shadow-xl">
      <div className="text-[9px] font-mono text-[#64748b] mb-1.5">
        {new Date(candle.timestamp).toLocaleString()}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
        <span className="text-[10px] font-mono text-[#64748b]">Open</span>
        <span className="mono-data text-[10px] text-[#e2e8f0] text-right">{formatPrice(candle.open)}</span>
        <span className="text-[10px] font-mono text-[#64748b]">High</span>
        <span className="mono-data text-[10px] text-right" style={{ color: THEME.green }}>{formatPrice(candle.high)}</span>
        <span className="text-[10px] font-mono text-[#64748b]">Low</span>
        <span className="mono-data text-[10px] text-right" style={{ color: THEME.red }}>{formatPrice(candle.low)}</span>
        <span className="text-[10px] font-mono text-[#64748b]">Close</span>
        <span className="mono-data text-[10px] text-right" style={{ color: isGreen ? THEME.green : THEME.red }}>
          {formatPrice(candle.close)}
        </span>
        <span className="text-[10px] font-mono text-[#64748b]">Volume</span>
        <span className="mono-data text-[10px] text-[#d4af37] text-right">{formatVolume(candle.volume)}</span>
      </div>
      <div className="mt-1 pt-1 border-t border-[#1e293b]">
        <span className={`text-[10px] font-mono font-bold ${isGreen ? 'text-emerald-400' : 'text-red-400'}`}>
          {isGreen ? '▲' : '▼'} {formatChange(((candle.close - candle.open) / candle.open) * 100)}
        </span>
      </div>
    </div>
  );
}

// ============================================================
// LOADING SKELETON
// ============================================================

function ChartSkeleton() {
  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header skeleton */}
      <div className="px-3 py-2 border-b border-[#1e293b] bg-[#0a0e17]">
        <div className="flex items-center gap-3">
          <Skeleton className="h-4 w-24 bg-[#1a1f2e]" />
          <Skeleton className="h-4 w-16 bg-[#1a1f2e]" />
          <Skeleton className="h-4 w-20 bg-[#1a1f2e]" />
        </div>
      </div>
      {/* Timeframe skeleton */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-[#1e293b]">
        {Array.from({ length: 9 }).map((_, i) => (
          <Skeleton key={i} className="h-5 w-8 bg-[#1a1f2e]" />
        ))}
      </div>
      {/* Chart skeleton */}
      <div className="flex-1 p-3 space-y-2">
        <Skeleton className="h-full w-full bg-[#1a1f2e] rounded" />
      </div>
    </div>
  );
}

// ============================================================
// DATA SOURCE BADGE
// ============================================================

/** Visual badge showing where chart data comes from */
function DataSourceBadge({ source }: { source: string }) {
  const config: Record<string, { label: string; color: string; bg: string }> = {
    binance:             { label: 'Binance',   color: 'text-emerald-400',  bg: 'bg-emerald-400/10' },
    coingecko:           { label: 'CoinGecko', color: 'text-blue-400',     bg: 'bg-blue-400/10' },
    coingecko_ondemand:  { label: 'CoinGecko', color: 'text-blue-400',     bg: 'bg-blue-400/10' },
    dexpaprika:          { label: 'DexPaprika', color: 'text-purple-400',  bg: 'bg-purple-400/10' },
    database:            { label: 'Cached',    color: 'text-[#94a3b8]',    bg: 'bg-[#94a3b8]/10' },
    database_fallback:   { label: 'Cached',    color: 'text-amber-400',    bg: 'bg-amber-400/10' },
    none:                { label: 'No Source', color: 'text-red-400/60',   bg: 'bg-red-400/10' },
  };
  const c = config[source] || config.none;
  return (
    <span className={`text-[8px] font-mono px-1.5 py-0.5 rounded ${c.color} ${c.bg}`}>
      {c.label}
    </span>
  );
}

// ============================================================
// NO DATA STATE
// ============================================================

function NoDataState({ source }: { source?: string }) {
  const isNoSource = source === 'none' || !source;
  return (
    <div className="flex flex-col items-center justify-center h-48 text-[#64748b] font-mono gap-2">
      <CandlestickChart className="h-8 w-8 opacity-40" />
      <span className="text-xs">No OHLCV data available</span>
      <span className="text-[10px] text-[#475569]">
        {isNoSource
          ? 'This token has no candle data yet — try running a backfill'
          : 'Data will appear when the market is active'}
      </span>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function OHLCVChart({ tokenAddress, chain, timeframes, trades: tradesProp }: OHLCVChartProps) {
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('4h');
  const [showTradeMarkers, setShowTradeMarkers] = useState<boolean>(true);
  const [hoveredTrade, setHoveredTrade] = useState<TradeMarker | null>(null);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const availableTimeframes = timeframes ?? ['5m', '15m', '1h', '4h', '1d'];

  // Fetch OHLCV data — preserves source & fallback metadata
  const {
    data: ohlcvResponse,
    isLoading,
    isError,
    refetch,
    dataUpdatedAt,
  } = useQuery({
    queryKey: ['ohlcv', tokenAddress, selectedTimeframe, chain],
    queryFn: async (): Promise<{ candles: OHLCVCandle[]; meta: OHLCVResponse }> => {
      try {
        const params = new URLSearchParams({
          tokenAddress,
          timeframe: selectedTimeframe,
          limit: '200',
        });
        if (chain) params.set('chain', chain);

        const res = await fetch(`/api/market/ohlcv?${params.toString()}`);
        if (!res.ok) throw new Error('Failed to fetch OHLCV data');
        const json: OHLCVResponse = await res.json();
        // Return both candles AND metadata so we can show source/fallback
        return { candles: json.candles || [], meta: json };
      } catch {
        return { candles: [], meta: { candles: [], source: 'none' } };
      }
    },
    refetchInterval: 30_000,
    staleTime: 10_000,
    enabled: !!tokenAddress,
  });

  // Fetch trade data from API if not provided via props
  const { data: apiTrades } = useQuery({
    queryKey: ['ohlcv-trades', tokenAddress],
    queryFn: async (): Promise<TradeMarker[]> => {
      try {
        const res = await fetch('/api/execution/history?limit=200');
        if (!res.ok) return [];
        const json = await res.json();
        const data = json.data || [];
        // Filter trades for this token and map to TradeMarker format
        return data
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          .filter((t: any) => t.tokenAddress === tokenAddress)
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          .map((t: any): TradeMarker => ({
            entryTime: t.entryTime,
            exitTime: t.exitTime,
            entryPrice: t.entryPrice,
            exitPrice: t.exitPrice,
            direction: t.direction === 'LONG' ? 'LONG' : 'SHORT',
            pnlPct: t.pnlPct,
            label: t.systemName || t.tokenSymbol,
            id: t.id,
          }));
      } catch {
        return [];
      }
    },
    staleTime: 15_000,
    enabled: !!tokenAddress && tradesProp === undefined,
  });

  const candles = ohlcvResponse?.candles ?? [];
  const ohlcvMeta = ohlcvResponse?.meta;
  const dataSource = ohlcvMeta?.source || 'none';
  const isFallback = ohlcvMeta?.fallback === true;
  const actualTimeframe = ohlcvMeta?.timeframe;
  const requestedTimeframe = ohlcvMeta?.requestedTimeframe || selectedTimeframe;

  // Use prop trades if provided, otherwise use API trades
  const trades = tradesProp ?? apiTrades ?? [];

  // Transform data for recharts — add formatted time labels
  const chartData = useMemo(() => {
    return candles.map((c) => ({
      ...c,
      time: formatTimeLabel(c.timestamp, selectedTimeframe),
      volumeColor: c.close >= c.open ? THEME.volumeUp : THEME.volumeDown,
    }));
  }, [candles, selectedTimeframe]);

  // Filter trades to only those within the visible candle range
  const visibleTrades = useMemo(() => {
    if (candles.length === 0 || trades.length === 0) return [];
    const minTs = candles[0].timestamp;
    const maxTs = candles[candles.length - 1].timestamp;
    // Extend the range slightly to catch trades near the edges
    const padding = (maxTs - minTs) * 0.02;
    return trades.filter((t) => {
      const entryTs = new Date(t.entryTime).getTime();
      return entryTs >= minTs - padding && entryTs <= maxTs + padding;
    });
  }, [trades, candles]);

  // Compute stats from the candles
  const stats = useMemo(() => {
    if (candles.length === 0) return null;

    const latest = candles[candles.length - 1];
    const first = candles[0];
    const currentPrice = latest.close;
    const change24h = ((latest.close - first.open) / first.open) * 100;
    const totalVolume = candles.reduce((sum, c) => sum + c.volume, 0);
    const highPrice = Math.max(...candles.map((c) => c.high));
    const lowPrice = Math.min(...candles.map((c) => c.low));

    return { currentPrice, change24h, totalVolume, highPrice, lowPrice };
  }, [candles]);

  // Price domain with padding — expand to include trade prices if needed
  const priceDomain = useMemo(() => {
    if (candles.length === 0) return [0, 1] as [number, number];
    const allLows = candles.map((c) => c.low);
    const allHighs = candles.map((c) => c.high);
    let min = Math.min(...allLows);
    let max = Math.max(...allHighs);

    // Expand domain to include trade prices
    if (showTradeMarkers && visibleTrades.length > 0) {
      const tradePrices = visibleTrades.flatMap((t) =>
        [t.entryPrice, t.exitPrice].filter((p): p is number => p != null)
      );
      if (tradePrices.length > 0) {
        min = Math.min(min, ...tradePrices);
        max = Math.max(max, ...tradePrices);
      }
    }

    const padding = (max - min) * 0.08;
    return [min - padding, max + padding] as [number, number];
  }, [candles, showTradeMarkers, visibleTrades]);

  // Max volume for domain
  const volumeMax = useMemo(() => {
    if (candles.length === 0) return 1;
    return Math.max(...candles.map((c) => c.volume)) * 1.2;
  }, [candles]);

  const handleTimeframeChange = useCallback((tf: string) => {
    setSelectedTimeframe(tf);
  }, []);

  // Handle refetch with animation
  const handleRefresh = useCallback(() => {
    refetch();
  }, [refetch]);

  const handleHoverTrade = useCallback((trade: TradeMarker | null) => {
    setHoveredTrade(trade);
  }, []);

  // -------------------------------------------------------------------
  // LOADING STATE
  // -------------------------------------------------------------------
  if (isLoading) return <ChartSkeleton />;

  // -------------------------------------------------------------------
  // ERROR / NO DATA STATE
  // -------------------------------------------------------------------
  if (isError || candles.length === 0) {
    return (
      <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0a0e17]">
          <CandlestickChart className="h-3.5 w-3.5 text-[#d4af37]" />
          <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">OHLCV Chart</span>
        </div>
        {/* Timeframe selector still visible */}
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-[#1e293b]">
          {ALL_TIMEFRAMES.map((tf) => (
            <Button
              key={tf}
              variant="ghost"
              size="sm"
              onClick={() => handleTimeframeChange(tf)}
              className={`h-5 px-1.5 text-[9px] font-mono ${
                selectedTimeframe === tf
                  ? 'bg-[#d4af37]/20 text-[#d4af37]'
                  : 'text-[#64748b] hover:text-[#e2e8f0]'
              }`}
            >
              {tf}
            </Button>
          ))}
        </div>
        <NoDataState source={dataSource} />
      </div>
    );
  }

  // -------------------------------------------------------------------
  // MAIN RENDER
  // -------------------------------------------------------------------
  const isPositiveChange = stats ? stats.change24h >= 0 : true;

  return (
    <div className="flex flex-col h-full bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* ── Header Bar ────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[#1e293b] bg-[#0a0e17]">
        <CandlestickChart className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">OHLCV</span>
        {/* Data source badge */}
        <DataSourceBadge source={dataSource} />

        {stats && (
          <>
            <span className="mono-data text-sm font-bold text-[#e2e8f0] ml-2">
              {formatPrice(stats.currentPrice)}
            </span>
            <span
              className={`mono-data text-[11px] font-bold flex items-center gap-0.5 ${
                isPositiveChange ? 'text-emerald-400' : 'text-red-400'
              }`}
            >
              {isPositiveChange ? (
                <TrendingUp className="h-3 w-3" />
              ) : (
                <TrendingDown className="h-3 w-3" />
              )}
              {formatChange(stats.change24h)}
            </span>

            <div className="ml-auto flex items-center gap-3">
              {/* Trade markers legend */}
              {visibleTrades.length > 0 && (
                <div className="hidden sm:flex items-center gap-2 text-[8px] font-mono">
                  <span className="flex items-center gap-1">
                    <span
                      className="inline-block w-0 h-0"
                      style={{
                        borderLeft: '3px solid transparent',
                        borderRight: '3px solid transparent',
                        borderBottom: '5px solid #10b981',
                      }}
                    />
                    <span className="text-emerald-400">Entry</span>
                  </span>
                  <span className="flex items-center gap-1">
                    <span
                      className="inline-block w-0 h-0"
                      style={{
                        borderLeft: '3px solid transparent',
                        borderRight: '3px solid transparent',
                        borderTop: '5px solid #ef4444',
                      }}
                    />
                    <span className="text-red-400">Exit</span>
                  </span>
                </div>
              )}
              {/* Volume */}
              <div className="flex items-center gap-1">
                <BarChart3 className="h-3 w-3 text-[#d4af37]" />
                <span className="text-[9px] font-mono text-[#64748b]">Vol</span>
                <span className="mono-data text-[10px] text-[#94a3b8]">{formatVolume(stats.totalVolume)}</span>
              </div>
              {/* High / Low */}
              <div className="hidden sm:flex items-center gap-2">
                <span className="text-[9px] font-mono text-[#64748b]">H</span>
                <span className="mono-data text-[10px] text-emerald-400">{formatPrice(stats.highPrice)}</span>
                <span className="text-[9px] font-mono text-[#64748b]">L</span>
                <span className="mono-data text-[10px] text-red-400">{formatPrice(stats.lowPrice)}</span>
              </div>
              {/* Refresh */}
              <Button
                variant="ghost"
                size="sm"
                onClick={handleRefresh}
                className="h-5 w-5 p-0 text-[#64748b] hover:text-[#e2e8f0]"
              >
                <RefreshCw className="h-3 w-3" />
              </Button>
              {/* Data freshness */}
              <span className="text-[8px] font-mono text-[#475569]">
                {dataUpdatedAt ? `${Math.floor((Date.now() - dataUpdatedAt) / 1000)}s` : '—'}
              </span>
            </div>
          </>
        )}
      </div>

      {/* ── Timeframe Selector ────────────────────────────────── */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-[#1e293b]">
        {ALL_TIMEFRAMES.map((tf) => (
          <Button
            key={tf}
            variant="ghost"
            size="sm"
            onClick={() => handleTimeframeChange(tf)}
            className={`h-5 px-1.5 text-[9px] font-mono ${
              selectedTimeframe === tf
                ? 'bg-[#d4af37]/20 text-[#d4af37]'
                : availableTimeframes.includes(tf)
                  ? 'text-[#94a3b8] hover:text-[#e2e8f0]'
                  : 'text-[#475569] hover:text-[#64748b]'
            }`}
          >
            {tf}
          </Button>
        ))}

        {/* Fallback indicator */}
        {isFallback && actualTimeframe && (
          <span className="text-[8px] font-mono text-amber-400/80 bg-amber-400/10 px-1.5 py-0.5 rounded">
            Showing {actualTimeframe} ({requestedTimeframe} unavailable)
          </span>
        )}
        {/* Toggle trade markers + LIVE indicator */}
        <div className="ml-auto flex items-center gap-2">
          {/* Trade marker toggle */}
          {trades.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowTradeMarkers((v) => !v)}
              className={`h-5 px-1.5 text-[9px] font-mono gap-1 ${
                showTradeMarkers
                  ? 'bg-[#d4af37]/20 text-[#d4af37]'
                  : 'text-[#475569] hover:text-[#94a3b8]'
              }`}
              title={showTradeMarkers ? 'Hide trade markers' : 'Show trade markers'}
            >
              <Crosshair className="h-3 w-3" />
              <span className="hidden sm:inline">Trades</span>
              <span className="inline sm:hidden">{visibleTrades.length}</span>
            </Button>
          )}
          <div className="flex items-center gap-1">
            <Activity className={`h-3 w-3 ${dataSource === 'none' ? 'text-gray-500' : 'text-emerald-400 animate-pulse'}`} />
            <span className={`text-[8px] font-mono ${dataSource === 'none' ? 'text-[#475569]' : 'text-[#64748b]'}`}>
              {dataSource === 'none' ? 'NO DATA' : 'LIVE'}
            </span>
          </div>
        </div>
      </div>

      {/* ── Chart Area ────────────────────────────────────────── */}
      <div className="flex-1 min-h-0 p-1 relative" ref={chartContainerRef}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={chartData}
            margin={{ top: 4, right: 8, bottom: 4, left: 8 }}
          >
            <defs>
              <linearGradient id="volumeGradientUp" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={THEME.green} stopOpacity={0.4} />
                <stop offset="100%" stopColor={THEME.green} stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="volumeGradientDown" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={THEME.red} stopOpacity={0.4} />
                <stop offset="100%" stopColor={THEME.red} stopOpacity={0.05} />
              </linearGradient>
            </defs>

            {/* X Axis — time labels */}
            <XAxis
              dataKey="time"
              axisLine={{ stroke: THEME.border }}
              tickLine={false}
              tick={{ fill: THEME.textMuted, fontSize: 9, fontFamily: 'monospace' }}
              interval="preserveStartEnd"
              minTickGap={40}
            />

            {/* Y Axis — price (left) */}
            <YAxis
              yAxisId="price"
              domain={priceDomain}
              axisLine={false}
              tickLine={false}
              tick={{ fill: THEME.textMuted, fontSize: 9, fontFamily: 'monospace' }}
              tickFormatter={(v: number) => formatPrice(v)}
              width={70}
            />

            {/* Y Axis — volume (right) */}
            <YAxis
              yAxisId="volume"
              orientation="right"
              domain={[0, volumeMax]}
              axisLine={false}
              tickLine={false}
              tick={{ fill: THEME.textMuted, fontSize: 8, fontFamily: 'monospace' }}
              tickFormatter={(v: number) => formatVolume(v)}
              width={50}
            />

            {/* Grid */}
            <Tooltip
              content={<CustomTooltipContent />}
              cursor={{ stroke: THEME.gold, strokeOpacity: 0.3, strokeDasharray: '4 4' }}
            />

            {/* Volume bars */}
            <Bar
              yAxisId="volume"
              dataKey="volume"
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              shape={(props: any) => {
                const { x, y, width, height, payload } = props;
                const isGreen = payload?.close >= payload?.open;
                return (
                  <rect
                    x={x}
                    y={y}
                    width={width}
                    height={height}
                    fill={isGreen ? THEME.volumeUp : THEME.volumeDown}
                    stroke={isGreen ? THEME.green : THEME.red}
                    strokeWidth={0.3}
                    strokeOpacity={0.3}
                  />
                );
              }}
            />

            {/* Candlestick custom renderer */}
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <Customized component={(props: any) => <CandlestickSeries {...props} data={chartData} />} />

            {/* Trade markers overlay */}
            {showTradeMarkers && visibleTrades.length > 0 && (
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              <Customized component={(props: any) => (
                <TradeMarkersOverlay
                  {...props}
                  trades={visibleTrades}
                  chartData={chartData}
                  onHoverTrade={handleHoverTrade}
                />
              )} />
            )}
          </ComposedChart>
        </ResponsiveContainer>

        {/* Trade hover tooltip — positioned absolutely in the chart container */}
        {hoveredTrade && (
          <div className="absolute top-2 right-2 z-20">
            <TradeMarkerTooltip trade={hoveredTrade} />
          </div>
        )}
      </div>

      {/* ── Trade Markers Legend (mobile) ──────────────────── */}
      {showTradeMarkers && visibleTrades.length > 0 && (
        <div className="sm:hidden flex items-center justify-center gap-4 px-3 py-1 border-t border-[#1e293b] bg-[#0a0e17]">
          <span className="flex items-center gap-1 text-[8px] font-mono">
            <span
              className="inline-block w-0 h-0"
              style={{
                borderLeft: '3px solid transparent',
                borderRight: '3px solid transparent',
                borderBottom: '5px solid #10b981',
              }}
            />
            <span className="text-emerald-400">Entry ▲</span>
          </span>
          <span className="flex items-center gap-1 text-[8px] font-mono">
            <span
              className="inline-block w-0 h-0"
              style={{
                borderLeft: '3px solid transparent',
                borderRight: '3px solid transparent',
                borderTop: '5px solid #ef4444',
              }}
            />
            <span className="text-red-400">Exit ▼</span>
          </span>
          <span className="text-[8px] font-mono text-[#64748b]">
            {visibleTrades.length} trade{visibleTrades.length !== 1 ? 's' : ''}
          </span>
        </div>
      )}
    </div>
  );
}
