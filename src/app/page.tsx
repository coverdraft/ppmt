'use client';

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  createChart,
  ColorType,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
} from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import {
  Database,
  Brain,
  Minus,
  Plus,
  RefreshCw,
  BarChart3,
  Layers,
  Radio,
  ArrowUpRight,
  ArrowDownRight,
  Zap,
  Target,
  Download,
  ChevronRight,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Loader2,
  Play,
  LineChart,
  CandlestickChart as CandlestickChartIcon,
  GitBranch,
  Gauge,
  ArrowRight,
  Terminal,
  FlaskConical,
  LayoutDashboard,
  Search,
  HardDrive,
  Activity,
  TrendingUp,
  TrendingDown,
  Sigma,
  Shield,
  Clock,
  Server,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

// ============================================================
// TYPES
// ============================================================

interface TimeframeData {
  timeframe: string;
  count: number;
  firstTs: number;
  lastTs: number;
}

interface TrieLevelData {
  patternCount: number;
  maxDepth: number;
  name?: string;
}

interface AssetDetail {
  symbol: string;
  assetClass: string;
  weightProfile: string;
  candleCount: number;
  firstSeen: string | null;
  lastUpdated: string | null;
  timeframes: TimeframeData[];
  tries: Record<string, TrieLevelData>;
  totalPatterns: number;
  engineState: any;
}

interface StatusData {
  assets: AssetDetail[];
  totalAssets: number;
  totalCandles: number;
  totalPatterns: number;
  signalCount: number;
  dbSizeBytes: number;
  dbSizeMB: string;
}

interface SignalData {
  id: number;
  symbol: string;
  signal_type: string;
  confidence: number;
  quality_score: number;
  sizing_multiplier: number;
  entry_price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  expected_move_pct: number;
  win_rate: number;
  remaining_candles: number;
  timestamp: number;
  matchedPattern: string[];
  predictedPath: any[];
}

interface OHLCVCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface BacktestTrade {
  entry_time: number;
  exit_time: number;
  direction: 'LONG' | 'SHORT';
  entry_price: number;
  exit_price: number;
  pnl_pct: number;
  holding_bars: number;
}

interface BacktestStats {
  total_return: number;
  sharpe: number;
  max_dd: number;
  win_rate: number;
  total_trades: number;
  avg_pnl?: number;
  best_trade?: number;
  worst_trade?: number;
  profit_factor?: number;
}

interface BacktestResult {
  trades: BacktestTrade[];
  equity_curve: { time: number; value: number }[];
  stats: BacktestStats;
  candles_used: number;
  trie_levels: number;
  signals_used: number;
  message?: string;
}

interface MCResult {
  var95: number;
  cvar95: number;
  meanReturn: number;
  medianReturn: number;
  bestReturn: number;
  worstReturn: number;
  pctProfitable: number;
  distribution: number[];
  equityPaths: number[][];
}

interface MCResponse {
  symbol: string;
  timeframe: string;
  base_trades: number;
  simulations: number;
  mc: MCResult | null;
  stats: {
    var95: number;
    cvar95: number;
    meanReturn: number;
    medianReturn: number;
    bestReturn: number;
    worstReturn: number;
    pctProfitable: number;
  };
  message?: string;
}

interface MultiAssetResult {
  symbol: string;
  asset_class: string;
  candle_count: number;
  total_return: number;
  sharpe: number;
  max_dd: number;
  win_rate: number;
  total_trades: number;
  avg_pnl: number;
  best_trade: number;
  worst_trade: number;
  profit_factor: number;
}

interface MultiBacktestResponse {
  timeframe: string;
  assets_tested: number;
  results: MultiAssetResult[];
}

// ============================================================
// CONSTANTS
// ============================================================

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'];
const TRIE_LEVELS = ['N1', 'N2', 'N3', 'N4'];
const TRIE_DESCRIPTIONS: Record<string, string> = {
  N1: 'Universal',
  N2: 'Class',
  N3: 'Asset',
  N4: 'Regime',
};

type TabId = 'dashboard' | 'command' | 'backtest';

// ============================================================
// API HOOKS
// ============================================================

function usePPMTStatus() {
  return useQuery({
    queryKey: ['ppmt-status'],
    queryFn: async () => {
      const res = await fetch('/api/ppmt/status');
      if (!res.ok) throw new Error('Failed to fetch PPMT status');
      const json = await res.json();
      return json.data as StatusData;
    },
    refetchInterval: 30000,
  });
}

function usePPMTSignals(symbol?: string) {
  return useQuery({
    queryKey: ['ppmt-signals', symbol],
    queryFn: async () => {
      const url = symbol ? `/api/ppmt/signals?symbol=${symbol}&limit=50` : '/api/ppmt/signals?limit=50';
      const res = await fetch(url);
      if (!res.ok) throw new Error('Failed to fetch signals');
      const json = await res.json();
      return json.data as SignalData[];
    },
    refetchInterval: 15000,
  });
}

function usePPMTPrediction(symbol: string | null) {
  return useQuery({
    queryKey: ['ppmt-prediction', symbol],
    queryFn: async () => {
      if (!symbol) return null;
      const res = await fetch(`/api/ppmt/predict?symbol=${encodeURIComponent(symbol)}`);
      if (!res.ok) throw new Error('Failed to fetch prediction');
      const json = await res.json();
      return json.data as { symbol: string; timeframe: string; output: string };
    },
    enabled: !!symbol,
    refetchInterval: 60000,
  });
}

function useOHLCV(symbol: string | null, timeframe: string) {
  return useQuery({
    queryKey: ['ppmt-ohlcv', symbol, timeframe],
    queryFn: async () => {
      if (!symbol) return null;
      const res = await fetch(`/api/ppmt/ohlcv?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}`);
      if (!res.ok) throw new Error('Failed to fetch OHLCV data');
      const json = await res.json();
      return json.data as { candles: OHLCVCandle[]; symbol: string; timeframe: string; count: number };
    },
    enabled: !!symbol,
    refetchInterval: 60000,
  });
}

function useMonteCarlo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ symbol, timeframe, simulations }: { symbol: string; timeframe: string; simulations: number }) => {
      const res = await fetch('/api/ppmt/monte-carlo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, timeframe, simulations }),
      });
      if (!res.ok) throw new Error('Monte Carlo failed');
      const json = await res.json();
      return json.data as MCResponse;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ppmt-status'] });
    },
  });
}

function useMultiBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ timeframe }: { timeframe: string }) => {
      const res = await fetch('/api/ppmt/multi-backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timeframe }),
      });
      if (!res.ok) throw new Error('Multi-backtest failed');
      const json = await res.json();
      return json.data as MultiBacktestResponse;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ppmt-status'] });
    },
  });
}

function useBuildAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ symbol, timeframe }: { symbol: string; timeframe: string }) => {
      const res = await fetch('/api/ppmt/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, timeframe }),
      });
      if (!res.ok) throw new Error('Build failed');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ppmt-status'] });
    },
  });
}

function useIngestAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ symbol, timeframe, days }: { symbol: string; timeframe: string; days: number }) => {
      const res = await fetch('/api/ppmt/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, timeframe, days }),
      });
      if (!res.ok) throw new Error('Ingest failed');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ppmt-status'] });
    },
  });
}

// ============================================================
// UTILITY FUNCTIONS
// ============================================================

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatDate(ts: number | string | null): string {
  if (!ts) return '—';
  if (typeof ts === 'number') {
    return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }
  return ts;
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function getDataCompleteness(asset: AssetDetail): number {
  const tf = asset.timeframes.length;
  const idealTimeframes = 6;
  const tfScore = Math.min(tf / idealTimeframes, 1);
  const candleScore = Math.min(asset.candleCount / 50000, 1);
  const trieScore = Object.keys(asset.tries).length / 4;
  return ((tfScore * 0.3 + candleScore * 0.4 + trieScore * 0.3) * 100);
}

function getCompletenessColor(pct: number): string {
  if (pct >= 70) return 'text-emerald-400';
  if (pct >= 40) return 'text-amber-400';
  return 'text-red-400';
}

function getCompletenessBg(pct: number): string {
  if (pct >= 70) return 'bg-emerald-500';
  if (pct >= 40) return 'bg-amber-500';
  return 'bg-red-500';
}

function getCompletenessBarBg(pct: number): string {
  if (pct >= 70) return 'bg-emerald-500/20';
  if (pct >= 40) return 'bg-amber-500/20';
  return 'bg-red-500/20';
}

function getDataSufficiency(asset: AssetDetail): 'sufficient' | 'partial' | 'insufficient' {
  const completeness = getDataCompleteness(asset);
  const hasAllTries = TRIE_LEVELS.every(l => asset.tries[l]);
  const hasEnoughCandles = asset.candleCount >= 50000;
  const hasEnoughTF = asset.timeframes.length >= 6;
  if (hasAllTries && hasEnoughCandles && hasEnoughTF) return 'sufficient';
  if (completeness >= 40 || (asset.candleCount >= 10000 && asset.timeframes.length >= 3)) return 'partial';
  return 'insufficient';
}

function getSignalTypeIcon(type: string) {
  if (type.includes('LONG')) return <ArrowUpRight className="h-3.5 w-3.5 text-emerald-400" />;
  if (type.includes('SHORT')) return <ArrowDownRight className="h-3.5 w-3.5 text-red-400" />;
  if (type.includes('EXIT')) return <XCircle className="h-3.5 w-3.5 text-red-400" />;
  if (type.includes('HOLD')) return <Minus className="h-3.5 w-3.5 text-amber-400" />;
  return <Radio className="h-3.5 w-3.5 text-[#64748b]" />;
}

function getSignalTypeColor(type: string): string {
  if (type.includes('LONG')) return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
  if (type.includes('SHORT')) return 'bg-red-500/10 text-red-400 border-red-500/30';
  if (type.includes('EXIT')) return 'bg-red-500/10 text-red-400 border-red-500/30';
  return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
}

// ============================================================
// CANDLESTICK CHART COMPONENT
// ============================================================

function CandlestickChart({ candles, symbol, timeframe, signals }: {
  candles: OHLCVCandle[];
  symbol: string;
  timeframe: string;
  signals?: SignalData[];
}) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const container = chartContainerRef.current;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0e17' },
        textColor: '#64748b',
        fontFamily: 'monospace',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      width: container.clientWidth,
      height: container.clientHeight || 400,
      crosshair: {
        mode: 0,
        vertLine: { color: '#3b82f680', width: 1, style: 2 },
        horzLine: { color: '#3b82f680', width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.05, bottom: 0.25 },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
      borderDownColor: '#ef4444',
      borderUpColor: '#10b981',
      wickDownColor: '#ef4444',
      wickUpColor: '#10b981',
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#3b82f6',
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    // OHLCV crosshair tooltip
    const tooltip = document.createElement('div');
    tooltip.style.cssText = 'position:absolute;display:none;padding:6px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;font-family:monospace;font-size:10px;color:#94a3b8;z-index:10;pointer-events:none;white-space:nowrap;';
    container.appendChild(tooltip);

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point || !param.seriesData) {
        tooltip.style.display = 'none';
        return;
      }
      const candleData = param.seriesData.get(candleSeries) as any;
      if (!candleData) {
        tooltip.style.display = 'none';
        return;
      }
      tooltip.style.display = 'block';
      tooltip.innerHTML = `<span style="color:#64748b">O</span> <span style="color:#f1f5f9">${candleData.open?.toFixed(2)}</span> <span style="color:#64748b">H</span> <span style="color:#f1f5f9">${candleData.high?.toFixed(2)}</span> <span style="color:#64748b">L</span> <span style="color:#f1f5f9">${candleData.low?.toFixed(2)}</span> <span style="color:#64748b">C</span> <span style="color:#f1f5f9">${candleData.close?.toFixed(2)}</span>`;
      tooltip.style.left = `${Math.min(param.point.x + 12, container.clientWidth - 260)}px`;
      tooltip.style.top = `${param.point.y - 28}px`;
    });

    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height: Math.max(height, 200) });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      if (tooltip.parentNode) tooltip.parentNode.removeChild(tooltip);
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  // Update candle + volume data
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !candles.length) return;

    const sorted = [...candles].sort((a, b) => a.time - b.time);
    const seen = new Set<number>();
    const unique = sorted.filter(c => {
      if (seen.has(c.time)) return false;
      seen.add(c.time);
      return true;
    });

    candleSeriesRef.current.setData(
      unique.map(c => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );

    volumeSeriesRef.current.setData(
      unique.map(c => ({
        time: c.time as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? '#10b98140' : '#ef444440',
      }))
    );

    // Add signal markers
    if (signals && signals.length > 0) {
      const markers = signals
        .filter(s => s.entry_price != null)
        .map(s => {
          const candle = unique.find(c => Math.abs(c.time - Math.floor(s.timestamp / 1000)) < 3600);
          if (!candle) return null;
          return {
            time: candle.time as UTCTimestamp,
            position: s.signal_type.includes('LONG') ? 'belowBar' as const : 'aboveBar' as const,
            color: s.signal_type.includes('LONG') ? '#10b981' : '#ef4444',
            shape: s.signal_type.includes('LONG') ? 'arrowUp' as const : 'arrowDown' as const,
            text: `${s.signal_type} ${(s.confidence * 100).toFixed(0)}%`,
          };
        })
        .filter(Boolean) as any[];

      if (markers.length > 0) {
        candleSeriesRef.current.setMarkers(markers);
      }
    }

    chartRef.current?.timeScale().fitContent();
  }, [candles, signals]);

  return (
    <div className="relative w-full h-full">
      <div className="absolute top-2 left-3 z-10 flex items-center gap-2">
        <CandlestickChartIcon className="h-3.5 w-3.5 text-[#3b82f6]" />
        <span className="font-mono text-[10px] text-[#94a3b8] font-bold">{symbol}</span>
        <span className="font-mono text-[9px] text-[#475569]">{timeframe}</span>
        <Badge variant="outline" className="text-[7px] font-mono px-1.5 py-0 h-4 bg-[#1e293b] text-[#64748b] border-[#334155]">
          {formatNumber(candles.length)} candles
        </Badge>
      </div>
      <div ref={chartContainerRef} className="w-full h-full" />
    </div>
  );
}

// ============================================================
// EQUITY CURVE CHART COMPONENT
// ============================================================

function EquityCurveChart({ data, color }: { data: { time: number; value: number }[]; color?: string }) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current || !data.length) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const container = chartContainerRef.current;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0e17' },
        textColor: '#64748b',
        fontFamily: 'monospace',
        fontSize: 9,
      },
      grid: {
        vertLines: { color: '#1e293b40' },
        horzLines: { color: '#1e293b40' },
      },
      width: container.clientWidth,
      height: container.clientHeight || 200,
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
      },
      crosshair: {
        mode: 0,
        vertLine: { color: '#3b82f640', width: 1, style: 2 },
        horzLine: { color: '#3b82f640', width: 1, style: 2 },
      },
    });

    chartRef.current = chart;

    const lineSeries = chart.addSeries(LineSeries, {
      color: color || '#10b981',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    lineSeries.setData(data.map(d => ({ time: d.time as UTCTimestamp, value: d.value })));
    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height: Math.max(height, 100) });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [data, color]);

  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-[200px] bg-[#0a0e17] rounded">
        <p className="text-[10px] font-mono text-[#475569]">No equity data</p>
      </div>
    );
  }

  return <div ref={chartContainerRef} className="w-full h-full" />;
}

// ============================================================
// MC EQUITY PATHS CHART COMPONENT
// ============================================================

function MCEquityPathsChart({ paths }: { paths: number[][] }) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current || !paths.length) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const container = chartContainerRef.current;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0e17' },
        textColor: '#64748b',
        fontFamily: 'monospace',
        fontSize: 9,
      },
      grid: {
        vertLines: { color: '#1e293b40' },
        horzLines: { color: '#1e293b40' },
      },
      width: container.clientWidth,
      height: container.clientHeight || 220,
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.05, bottom: 0.05 },
      },
      timeScale: {
        borderColor: '#1e293b',
      },
      crosshair: {
        mode: 0,
      },
    });

    chartRef.current = chart;

    const samplePaths = paths.slice(0, 20);
    const colors = [
      '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
      '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1',
    ];

    samplePaths.forEach((path, i) => {
      const series = chart.addSeries(LineSeries, {
        color: colors[i % colors.length] + '60',
        lineWidth: 1,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      });
      series.setData(path.map((v, j) => ({ time: j as UTCTimestamp, value: v })));
    });

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height: Math.max(height, 100) });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [paths]);

  if (!paths.length) {
    return (
      <div className="flex items-center justify-center h-[220px] bg-[#0a0e17] rounded">
        <p className="text-[10px] font-mono text-[#475569]">No MC paths data</p>
      </div>
    );
  }

  return <div ref={chartContainerRef} className="w-full h-full" />;
}

// ============================================================
// SHARED COMPONENTS
// ============================================================

function StatCard({ title, value, subtitle, icon: Icon, colorClass }: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ComponentType<{ className?: string }>;
  colorClass: string;
}) {
  return (
    <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider">{title}</p>
          <p className="text-lg font-mono font-bold text-[#f1f5f9] mt-0.5">{value}</p>
          {subtitle && <p className="text-[9px] font-mono text-[#475569] mt-0.5">{subtitle}</p>}
        </div>
        <div className={`flex items-center justify-center w-9 h-9 rounded-lg ${colorClass}`}>
          <Icon className="h-4.5 w-4.5" />
        </div>
      </div>
    </div>
  );
}

function AssetRow({ asset, isSelected, onSelect }: {
  asset: AssetDetail;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const completeness = getDataCompleteness(asset);
  const hasAllTries = TRIE_LEVELS.every(l => asset.tries[l]);

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-3 py-2.5 border-b border-[#1e293b] transition-colors ${
        isSelected ? 'bg-[#3b82f6]/5 border-l-2 border-l-[#3b82f6]' : 'hover:bg-[#1e293b]/30 border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-xs font-bold text-[#f1f5f9]">{asset.symbol}</span>
          <Badge variant="outline" className="text-[7px] font-mono px-1 py-0 h-3.5 bg-[#1e293b] text-[#94a3b8] border-[#334155]">
            {asset.assetClass}
          </Badge>
        </div>
        <div className="flex items-center gap-1.5">
          {hasAllTries ? (
            <CheckCircle2 className="h-3 w-3 text-emerald-400" />
          ) : (
            <AlertTriangle className="h-3 w-3 text-amber-400" />
          )}
          <ChevronRight className={`h-3 w-3 ${isSelected ? 'text-[#3b82f6]' : 'text-[#475569]'}`} />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1">
          <div className="h-1 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${getCompletenessBg(completeness)}`}
              style={{ width: `${completeness}%` }}
            />
          </div>
        </div>
        <span className={`text-[8px] font-mono font-bold ${getCompletenessColor(completeness)}`}>
          {Math.round(completeness)}%
        </span>
      </div>
      <div className="flex items-center gap-1 mt-1 flex-wrap">
        {asset.timeframes.slice(0, 4).map(tf => (
          <Badge key={tf.timeframe} variant="outline" className="text-[6px] font-mono px-1 py-0 h-3 bg-[#0a0e17] text-emerald-400 border-emerald-500/30">
            {tf.timeframe}
          </Badge>
        ))}
        {asset.timeframes.length > 4 && (
          <span className="text-[6px] font-mono text-[#475569]">+{asset.timeframes.length - 4}</span>
        )}
        {!asset.timeframes.length && (
          <span className="text-[7px] font-mono text-red-400">No data</span>
        )}
      </div>
    </button>
  );
}

function PredictionPanel({ asset, prediction }: { asset: AssetDetail; prediction: any }) {
  const queryClient = useQueryClient();

  const predictMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/ppmt/predict?symbol=${encodeURIComponent(asset.symbol)}&timeframe=1h&depth=5`);
      if (!res.ok) throw new Error('Predict failed');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ppmt-prediction', asset.symbol] });
    },
  });

  const parsePrediction = (output: string | undefined) => {
    if (!output) return null;
    const direction = output.includes('LONG') ? 'LONG' : output.includes('SHORT') ? 'SHORT' : output.includes('HOLD') ? 'HOLD' : null;
    const moveMatch = output.match(/move[:\s]+([+-]?\d+\.?\d*)%/i);
    const confMatch = output.match(/confidence[:\s]+(\d+\.?\d*)/i);
    const sizeMatch = output.match(/siz[^:]*[:\s]+(\d+\.?\d*)/i);
    return {
      direction,
      move: moveMatch ? parseFloat(moveMatch[1]) : null,
      confidence: confMatch ? parseFloat(confMatch[1]) : null,
      sizing: sizeMatch ? parseFloat(sizeMatch[1]) : null,
    };
  };

  const parsed = parsePrediction(prediction?.output);
  const weights = asset.engineState?.weights;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Prediction Display */}
      <div className="px-3 py-2.5 border-b border-[#1e293b]">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider flex items-center gap-1">
            <Gauge className="h-3 w-3" /> Prediction
          </h3>
          <Button
            size="sm"
            variant="outline"
            className="h-5 text-[8px] font-mono px-2 bg-[#10b981]/10 border-[#10b981]/30 text-[#10b981] hover:bg-[#10b981]/20"
            onClick={() => predictMutation.mutate()}
            disabled={predictMutation.isPending}
          >
            {predictMutation.isPending ? <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" /> : <Zap className="h-2.5 w-2.5 mr-0.5" />}
            Predict
          </Button>
        </div>

        {parsed?.direction ? (
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-[9px] font-mono text-[#64748b]">Direction:</span>
              <Badge variant="outline" className={`text-[9px] font-mono px-2 py-0 h-5 ${
                parsed.direction === 'LONG' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' :
                parsed.direction === 'SHORT' ? 'bg-red-500/10 text-red-400 border-red-500/30' :
                'bg-amber-500/10 text-amber-400 border-amber-500/30'
              }`}>
                {parsed.direction === 'LONG' ? <ArrowUpRight className="h-2.5 w-2.5 mr-0.5" /> :
                 parsed.direction === 'SHORT' ? <ArrowDownRight className="h-2.5 w-2.5 mr-0.5" /> :
                 <Minus className="h-2.5 w-2.5 mr-0.5" />}
                {parsed.direction}
              </Badge>
            </div>
            {parsed.move !== null && (
              <div className="flex items-center gap-2">
                <span className="text-[9px] font-mono text-[#64748b]">Move:</span>
                <span className={`text-[11px] font-mono font-bold ${parsed.move > 0 ? 'text-emerald-400' : parsed.move < 0 ? 'text-red-400' : 'text-[#94a3b8]'}`}>
                  {parsed.move > 0 ? '+' : ''}{parsed.move}%
                </span>
              </div>
            )}
            {parsed.confidence !== null && (
              <div className="flex items-center gap-2">
                <span className="text-[9px] font-mono text-[#64748b]">Conf:</span>
                <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                  <div className="h-full rounded-full bg-[#3b82f6]" style={{ width: `${parsed.confidence * 100}%` }} />
                </div>
                <span className="text-[9px] font-mono text-[#3b82f6]">{(parsed.confidence * 100).toFixed(0)}%</span>
              </div>
            )}
            {parsed.sizing !== null && (
              <div className="flex items-center gap-2">
                <span className="text-[9px] font-mono text-[#64748b]">Sizing:</span>
                <span className="text-[10px] font-mono font-bold text-[#94a3b8]">{parsed.sizing}x</span>
              </div>
            )}
          </div>
        ) : prediction?.output ? (
          <pre className="text-[8px] font-mono text-[#94a3b8] bg-[#0a0e17] border border-[#1e293b] rounded p-2 whitespace-pre-wrap overflow-x-auto max-h-32 overflow-y-auto">
            {prediction.output}
          </pre>
        ) : (
          <div className="flex flex-col items-center justify-center py-4 bg-[#0a0e17] border border-[#1e293b] rounded">
            <Brain className="h-5 w-5 text-[#475569] mb-1.5" />
            <p className="text-[9px] font-mono text-[#475569]">No prediction</p>
            <p className="text-[7px] font-mono text-[#334155] mt-0.5">Build trie first, then predict</p>
          </div>
        )}
      </div>

      {/* Trie Architecture */}
      <div className="px-3 py-2.5 border-b border-[#1e293b]">
        <h3 className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-2 flex items-center gap-1">
          <GitBranch className="h-3 w-3" /> 4-Level Trie
        </h3>
        <div className="grid grid-cols-4 gap-1.5">
          {TRIE_LEVELS.map(level => {
            const trie = asset.tries[level];
            return (
              <div key={level} className={`p-1.5 rounded border ${
                trie ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-[#0a0e17] border-[#1e293b]'
              }`}>
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-[8px] font-mono font-bold text-[#f1f5f9]">{level}</span>
                  {trie ? <CheckCircle2 className="h-2.5 w-2.5 text-emerald-400" /> : <XCircle className="h-2.5 w-2.5 text-[#475569]" />}
                </div>
                <p className="text-[6px] font-mono text-[#475569]">{TRIE_DESCRIPTIONS[level]}</p>
                {trie && (
                  <p className="text-[8px] font-mono text-[#94a3b8] mt-0.5">{formatNumber(trie.patternCount)} pat</p>
                )}
              </div>
            );
          })}
        </div>
        {weights && (
          <div className="mt-1.5 flex items-center gap-1">
            <span className="text-[7px] font-mono text-[#475569]">W:</span>
            {TRIE_LEVELS.map((level, i) => {
              const w = Object.values(weights)[i] as number | undefined;
              return (
                <span key={level} className="text-[7px] font-mono text-[#3b82f6]">{level}={w ? `${(w * 100).toFixed(0)}%` : '—'}</span>
              );
            })}
          </div>
        )}
      </div>

      {/* Pattern Quality */}
      <div className="px-3 py-2.5">
        <h3 className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-2 flex items-center gap-1">
          <Target className="h-3 w-3" /> Pattern Quality
        </h3>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[8px] font-mono text-[#475569]">Total Patterns</span>
            <span className="text-[10px] font-mono font-bold text-[#f1f5f9]">{formatNumber(asset.totalPatterns)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[8px] font-mono text-[#475569]">Candle Coverage</span>
            <span className="text-[10px] font-mono font-bold text-[#94a3b8]">{formatNumber(asset.candleCount)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[8px] font-mono text-[#475569]">Timeframes</span>
            <span className="text-[10px] font-mono font-bold text-[#94a3b8]">{asset.timeframes.length}/6</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[8px] font-mono text-[#475569]">Engine State</span>
            <span className="text-[10px] font-mono font-bold text-[#94a3b8]">
              {asset.engineState ? 'Active' : 'None'}
            </span>
          </div>
        </div>
        {/* Forward Prediction Chain */}
        <div className="mt-3 pt-2 border-t border-[#1e293b]">
          <p className="text-[8px] font-mono text-[#475569] mb-1.5">Forward Prediction Chain</p>
          <div className="flex items-center gap-0.5">
            {TRIE_LEVELS.map((level, i) => (
              <div key={level} className="flex items-center">
                <div className={`w-8 h-6 rounded border flex items-center justify-center ${
                  asset.tries[level] ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-[#0a0e17] border-[#1e293b]'
                }`}>
                  <span className="text-[7px] font-mono text-[#94a3b8]">{level}</span>
                </div>
                {i < 3 && <ArrowRight className="h-2.5 w-2.5 text-[#475569] mx-0.5" />}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SignalsFeed({ signals }: { signals: SignalData[] | undefined }) {
  return (
    <div className="h-[120px] bg-[#0d1117] border-t border-[#1e293b] shrink-0">
      <div className="px-3 py-1.5 border-b border-[#1e293b] flex items-center justify-between">
        <h3 className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider flex items-center gap-1">
          <Radio className="h-3 w-3" /> Signals Feed
        </h3>
        <Badge variant="outline" className="text-[7px] font-mono px-1.5 py-0 h-3.5 bg-[#1e293b] text-[#94a3b8] border-[#334155]">
          {signals?.length ?? 0} signals
        </Badge>
      </div>
      <div className="overflow-x-auto overflow-y-hidden whitespace-nowrap px-3 py-2 flex gap-2">
        {!signals?.length ? (
          <div className="flex items-center justify-center w-full py-4">
            <p className="text-[9px] font-mono text-[#475569]">No signals yet — signals appear when patterns match</p>
          </div>
        ) : (
          signals.slice(0, 30).map((signal, i) => (
            <div
              key={signal.id || i}
              className="inline-flex flex-col gap-0.5 px-2.5 py-1.5 bg-[#0a0e17] border border-[#1e293b] rounded-md min-w-[140px] shrink-0"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1">
                  {getSignalTypeIcon(signal.signal_type)}
                  <span className="text-[9px] font-mono font-bold text-[#f1f5f9]">{signal.symbol}</span>
                </div>
                <Badge variant="outline" className={`text-[6px] font-mono px-1 py-0 h-3 ${getSignalTypeColor(signal.signal_type)}`}>
                  {signal.signal_type.replace('ENTRY_', '').replace('EXIT', 'EXIT')}
                </Badge>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[7px] font-mono text-[#475569]">Conf {(signal.confidence * 100).toFixed(0)}%</span>
                <span className="text-[7px] font-mono text-[#475569]">Q {(signal.quality_score * 100).toFixed(0)}%</span>
                <span className="text-[7px] font-mono text-[#475569]">Sz {signal.sizing_multiplier.toFixed(1)}x</span>
              </div>
              <span className="text-[6px] font-mono text-[#334155]">{formatTime(signal.timestamp)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ============================================================
// TAB 1: DASHBOARD
// ============================================================

function DashboardTab({
  status, statusLoading, statusError,
  selectedSymbol, setSelectedSymbol,
  selectedTimeframe, setSelectedTimeframe,
  effectiveSymbol, effectiveTimeframe,
  selectedAsset, availableTimeframes,
  prediction, ohlcvData, ohlcvLoading,
  signals, ingestMutation, buildMutation, queryClient,
}: {
  status: StatusData | undefined;
  statusLoading: boolean;
  statusError: Error | null;
  selectedSymbol: string | null;
  setSelectedSymbol: (s: string | null) => void;
  selectedTimeframe: string;
  setSelectedTimeframe: (t: string) => void;
  effectiveSymbol: string | null;
  effectiveTimeframe: string;
  selectedAsset: AssetDetail | undefined;
  availableTimeframes: string[];
  prediction: any;
  ohlcvData: any;
  ohlcvLoading: boolean;
  signals: SignalData[] | undefined;
  ingestMutation: any;
  buildMutation: any;
  queryClient: any;
}) {
  const [addSymbolInput, setAddSymbolInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const hasDataForTimeframe = availableTimeframes.includes(effectiveTimeframe);

  const handleIngestNew = useCallback(async () => {
    if (!addSymbolInput.trim()) return;
    try {
      await ingestMutation.mutateAsync({ symbol: addSymbolInput.trim().toUpperCase(), timeframe: '1h', days: 365 });
      setAddSymbolInput('');
    } catch { /* ignore */ }
  }, [addSymbolInput, ingestMutation]);

  const assets = status?.assets ?? [];
  const filteredAssets = assetSearch.trim()
    ? assets.filter(a =>
        a.symbol.toLowerCase().includes(assetSearch.trim().toLowerCase()) ||
        a.assetClass.toLowerCase().includes(assetSearch.trim().toLowerCase())
      )
    : assets;

  return (
    <>
      {/* Stats Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 px-3 py-2 shrink-0">
        <StatCard title="Assets" value={status?.totalAssets ?? '—'} subtitle="Tracked symbols" icon={Database} colorClass="bg-emerald-500/10 text-emerald-400" />
        <StatCard title="Candles" value={status ? formatNumber(status.totalCandles) : '—'} subtitle="OHLCV data points" icon={BarChart3} colorClass="bg-[#3b82f6]/10 text-[#3b82f6]" />
        <StatCard title="Patterns" value={status ? formatNumber(status.totalPatterns) : '—'} subtitle="Unique trie patterns" icon={Layers} colorClass="bg-amber-500/10 text-amber-400" />
        <StatCard title="Signals" value={status?.signalCount ?? '—'} subtitle="Trading signals" icon={Radio} colorClass="bg-cyan-500/10 text-cyan-400" />
      </div>

      {/* Main Content */}
      <div className="flex-1 flex min-h-0 px-3 pb-2 gap-2">
        {/* LEFT: Asset Panel */}
        <div className="w-[240px] shrink-0 flex flex-col bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-[#1e293b] shrink-0">
            <div className="flex items-center justify-between mb-1.5">
              <h3 className="text-[9px] text-[#64748b] uppercase tracking-wider">Assets</h3>
              <Badge variant="outline" className="text-[7px] px-1.5 py-0 h-3.5 bg-[#1e293b] text-[#94a3b8] border-[#334155]">
                {status?.assets?.length ?? 0}
              </Badge>
            </div>
            {/* Search */}
            <div className="relative mb-1.5">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-[#475569]" />
              <input
                type="text"
                placeholder="Search assets..."
                value={assetSearch}
                onChange={(e) => setAssetSearch(e.target.value)}
                className="w-full h-6 pl-7 pr-2 text-[9px] bg-[#0a0e17] border border-[#1e293b] rounded text-[#94a3b8] placeholder-[#334155] focus:border-[#3b82f6]/50 focus:outline-none"
              />
            </div>
            {/* Add Asset */}
            <div className="flex gap-1">
              <input
                type="text"
                placeholder="e.g. SOL/USDT"
                value={addSymbolInput}
                onChange={(e) => setAddSymbolInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleIngestNew()}
                className="flex-1 h-6 px-2 text-[9px] bg-[#0a0e17] border border-[#1e293b] rounded text-[#94a3b8] placeholder-[#334155] focus:border-[#3b82f6]/50 focus:outline-none"
              />
              <Button
                size="sm"
                onClick={handleIngestNew}
                disabled={!addSymbolInput.trim() || ingestMutation.isPending}
                className="h-6 px-1.5 bg-[#3b82f6]/10 border border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20 text-[8px]"
                variant="outline"
              >
                {ingestMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
              </Button>
            </div>
          </div>

          {/* Quick Actions for Selected Asset */}
          {selectedAsset && (
            <div className="px-3 py-1.5 border-b border-[#1e293b] shrink-0 flex items-center gap-1">
              <Button
                size="sm"
                variant="outline"
                className="h-5 text-[7px] font-mono px-1.5 bg-[#0a0e17] border-[#334155] text-[#94a3b8] hover:text-[#f1f5f9]"
                onClick={() => ingestMutation.mutate({ symbol: selectedAsset.symbol, timeframe: '1h', days: 365 })}
                disabled={ingestMutation.isPending}
              >
                {ingestMutation.isPending ? <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" /> : <Download className="h-2.5 w-2.5 mr-0.5" />}
                Ingest
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-5 text-[7px] font-mono px-1.5 bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20"
                onClick={() => buildMutation.mutate({ symbol: selectedAsset.symbol, timeframe: '1h' })}
                disabled={buildMutation.isPending}
              >
                {buildMutation.isPending ? <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" /> : <Layers className="h-2.5 w-2.5 mr-0.5" />}
                Build
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-5 text-[7px] font-mono px-1.5 bg-[#10b981]/10 border-[#10b981]/30 text-[#10b981] hover:bg-[#10b981]/20"
                onClick={() => queryClient.invalidateQueries({ queryKey: ['ppmt-prediction', selectedAsset.symbol] })}
              >
                <Zap className="h-2.5 w-2.5 mr-0.5" />
                Predict
              </Button>
            </div>
          )}

          {/* Asset List */}
          <div className="flex-1 overflow-y-auto">
            {statusLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-4 w-4 text-[#475569] animate-spin" />
              </div>
            ) : statusError ? (
              <div className="px-3 py-6 text-center">
                <AlertTriangle className="h-5 w-5 text-red-400 mx-auto mb-1.5" />
                <p className="text-[9px] text-red-400">Failed to load assets</p>
              </div>
            ) : !filteredAssets.length ? (
              <div className="px-3 py-6 text-center">
                <Database className="h-5 w-5 text-[#475569] mx-auto mb-1.5" />
                <p className="text-[9px] text-[#475569]">{assetSearch ? 'No matches' : 'No assets tracked yet'}</p>
              </div>
            ) : (
              filteredAssets.map(asset => (
                <AssetRow
                  key={asset.symbol}
                  asset={asset}
                  isSelected={effectiveSymbol === asset.symbol}
                  onSelect={() => setSelectedSymbol(asset.symbol)}
                />
              ))
            )}
          </div>
        </div>

        {/* CENTER: Chart */}
        <div className="flex-1 min-w-0 flex flex-col gap-2">
          <div className="flex-1 bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden flex flex-col min-h-0">
            {/* Timeframe Selector */}
            <div className="px-3 py-1.5 border-b border-[#1e293b] flex items-center justify-between shrink-0">
              <div className="flex items-center gap-0.5">
                {TIMEFRAMES.map(tf => (
                  <button
                    key={tf}
                    onClick={() => setSelectedTimeframe(tf)}
                    className={`px-2 py-0.5 text-[9px] font-mono rounded transition-colors ${
                      effectiveTimeframe === tf
                        ? 'bg-[#3b82f6]/20 text-[#3b82f6] border border-[#3b82f6]/30'
                        : availableTimeframes.includes(tf)
                          ? 'bg-[#0a0e17] text-[#94a3b8] border border-[#1e293b] hover:text-[#f1f5f9]'
                          : 'bg-[#0a0e17] text-[#334155] border border-[#1e293b] cursor-not-allowed'
                    }`}
                    disabled={!availableTimeframes.includes(tf) && effectiveTimeframe !== tf}
                  >
                    {tf}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1.5">
                {selectedAsset && (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-5 text-[7px] font-mono px-1.5 bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20"
                      onClick={() => ingestMutation.mutate({ symbol: selectedAsset.symbol, timeframe: effectiveTimeframe, days: 365 })}
                      disabled={ingestMutation.isPending}
                    >
                      {ingestMutation.isPending ? <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" /> : <Download className="h-2.5 w-2.5 mr-0.5" />}
                      Ingest {effectiveTimeframe}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-5 text-[7px] font-mono px-1.5 bg-[#0a0e17] border-[#334155] text-[#94a3b8] hover:text-[#f1f5f9]"
                      onClick={() => queryClient.invalidateQueries({ queryKey: ['ppmt-ohlcv', effectiveSymbol, effectiveTimeframe] })}
                    >
                      <RefreshCw className="h-2.5 w-2.5 mr-0.5" />
                    </Button>
                  </>
                )}
              </div>
            </div>

            {/* Chart Content */}
            <div className="flex-1 min-h-0">
              {!effectiveSymbol ? (
                <div className="flex flex-col items-center justify-center h-full">
                  <CandlestickChartIcon className="h-8 w-8 text-[#334155] mb-2" />
                  <p className="text-[11px] text-[#475569]">Select an asset to view chart</p>
                </div>
              ) : ohlcvLoading ? (
                <div className="flex items-center justify-center h-full">
                  <Loader2 className="h-5 w-5 text-[#3b82f6] animate-spin" />
                </div>
              ) : !hasDataForTimeframe ? (
                <div className="flex flex-col items-center justify-center h-full">
                  <BarChart3 className="h-8 w-8 text-[#334155] mb-2" />
                  <p className="text-[11px] text-[#475569]">No data for {effectiveTimeframe}</p>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-[8px] font-mono px-3 mt-2 bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20"
                    onClick={() => ingestMutation.mutate({ symbol: effectiveSymbol!, timeframe: effectiveTimeframe, days: 365 })}
                    disabled={ingestMutation.isPending}
                  >
                    {ingestMutation.isPending ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Download className="h-3 w-3 mr-1" />}
                    Ingest {effectiveTimeframe}
                  </Button>
                </div>
              ) : ohlcvData?.candles?.length ? (
                <CandlestickChart
                  candles={ohlcvData.candles}
                  symbol={effectiveSymbol ?? ''}
                  timeframe={effectiveTimeframe}
                  signals={signals?.filter(s => s.symbol === effectiveSymbol)}
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-full">
                  <Database className="h-8 w-8 text-[#334155] mb-2" />
                  <p className="text-[11px] text-[#475569]">No OHLCV data available</p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT: Prediction & Trie */}
        <div className="w-[240px] shrink-0 bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden flex flex-col">
          {selectedAsset ? (
            <PredictionPanel asset={selectedAsset} prediction={prediction} />
          ) : (
            <div className="flex flex-col items-center justify-center h-full">
              <Brain className="h-8 w-8 text-[#334155] mb-2" />
              <p className="text-[11px] text-[#475569]">Select an asset</p>
              <p className="text-[9px] text-[#334155] mt-0.5">View predictions & trie stats</p>
            </div>
          )}
        </div>
      </div>

      {/* Signals Feed */}
      <SignalsFeed signals={signals} />
    </>
  );
}

// ============================================================
// TAB 2: COMMAND CENTER
// ============================================================

function CommandCenterTab({
  status, statusLoading, statusError,
  ingestMutation, buildMutation, queryClient,
}: {
  status: StatusData | undefined;
  statusLoading: boolean;
  statusError: Error | null;
  ingestMutation: any;
  buildMutation: any;
  queryClient: any;
}) {
  const [newSymbol, setNewSymbol] = useState('');
  const [buildAllStatus, setBuildAllStatus] = useState<string>('');

  const handleIngestNew = useCallback(async () => {
    if (!newSymbol.trim()) return;
    try {
      await ingestMutation.mutateAsync({ symbol: newSymbol.trim().toUpperCase(), timeframe: '1h', days: 365 });
      setNewSymbol('');
    } catch { /* ignore */ }
  }, [newSymbol, ingestMutation]);

  const handleBulkIngest = useCallback(async () => {
    if (!status?.assets) return;
    const missingAssets = status.assets.filter(a => a.timeframes.length < 6);
    for (const asset of missingAssets) {
      const missingTfs = TIMEFRAMES.filter(tf => !asset.timeframes.some(atf => atf.timeframe === tf));
      for (const tf of missingTfs) {
        try {
          await ingestMutation.mutateAsync({ symbol: asset.symbol, timeframe: tf, days: 365 });
        } catch { /* continue */ }
      }
    }
  }, [status?.assets, ingestMutation]);

  const handleBuildAll = useCallback(async () => {
    if (!status?.assets) return;
    setBuildAllStatus('Building...');
    for (const asset of status.assets) {
      try {
        await buildMutation.mutateAsync({ symbol: asset.symbol, timeframe: '1h' });
      } catch { /* continue */ }
    }
    setBuildAllStatus('Complete');
    setTimeout(() => setBuildAllStatus(''), 3000);
  }, [status?.assets, buildMutation]);

  // Sufficiency summary
  const sufficiencySummary = useMemo(() => {
    if (!status?.assets) return { sufficient: 0, partial: 0, insufficient: 0 };
    let sufficient = 0, partial = 0, insufficient = 0;
    status.assets.forEach(a => {
      const s = getDataSufficiency(a);
      if (s === 'sufficient') sufficient++;
      else if (s === 'partial') partial++;
      else insufficient++;
    });
    return { sufficient, partial, insufficient };
  }, [status?.assets]);

  if (statusLoading) {
    return (
      <div className="flex items-center justify-center flex-1">
        <Loader2 className="h-6 w-6 text-[#3b82f6] animate-spin" />
      </div>
    );
  }

  if (statusError) {
    return (
      <div className="flex flex-col items-center justify-center flex-1">
        <AlertTriangle className="h-8 w-8 text-red-400 mb-2" />
        <p className="text-[11px] text-red-400">Failed to load system status</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
      {/* Data Sufficiency Summary */}
      <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
        <h3 className="text-[10px] font-mono text-[#f1f5f9] uppercase tracking-wider mb-2 flex items-center gap-1.5">
          <Shield className="h-3.5 w-3.5 text-[#3b82f6]" /> Data Sufficiency Summary
        </h3>
        <p className="text-[8px] font-mono text-[#475569] mb-2">Minimum requirements: 50K+ candles, 6 timeframes, 4 trie levels for reliable signals</p>
        <div className="grid grid-cols-3 gap-2 mb-2">
          <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-2 text-center">
            <p className="text-[8px] font-mono text-emerald-400 uppercase">Sufficient</p>
            <p className="text-lg font-mono font-bold text-emerald-400">{sufficiencySummary.sufficient}</p>
          </div>
          <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-2 text-center">
            <p className="text-[8px] font-mono text-amber-400 uppercase">Partial</p>
            <p className="text-lg font-mono font-bold text-amber-400">{sufficiencySummary.partial}</p>
          </div>
          <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-2 text-center">
            <p className="text-[8px] font-mono text-red-400 uppercase">Insufficient</p>
            <p className="text-lg font-mono font-bold text-red-400">{sufficiencySummary.insufficient}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-2 bg-[#1e293b] rounded-full overflow-hidden flex">
            {sufficiencySummary.sufficient > 0 && (
              <div className="h-full bg-emerald-500" style={{ width: `${(sufficiencySummary.sufficient / (status?.assets.length || 1)) * 100}%` }} />
            )}
            {sufficiencySummary.partial > 0 && (
              <div className="h-full bg-amber-500" style={{ width: `${(sufficiencySummary.partial / (status?.assets.length || 1)) * 100}%` }} />
            )}
            {sufficiencySummary.insufficient > 0 && (
              <div className="h-full bg-red-500" style={{ width: `${(sufficiencySummary.insufficient / (status?.assets.length || 1)) * 100}%` }} />
            )}
          </div>
          <span className="text-[8px] font-mono text-[#475569]">{status?.assets.length || 0} total</span>
        </div>
      </div>

      {/* Data Inventory Grid */}
      <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-[#1e293b] flex items-center justify-between">
          <h3 className="text-[10px] font-mono text-[#f1f5f9] uppercase tracking-wider flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5 text-[#3b82f6]" /> Data Inventory
          </h3>
          <Badge variant="outline" className="text-[7px] font-mono px-1.5 py-0 h-4 bg-[#1e293b] text-[#94a3b8] border-[#334155]">
            {status?.assets.length ?? 0} assets
          </Badge>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead>
              <tr className="border-b border-[#1e293b]">
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-left uppercase">Symbol</th>
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-left uppercase">Class</th>
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-right uppercase">Candles</th>
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-center uppercase">Timeframes</th>
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-right uppercase">Patterns</th>
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-center uppercase">Trie N1-N4</th>
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-center uppercase">Completeness</th>
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-center uppercase">Status</th>
                <th className="text-[8px] font-mono text-[#64748b] px-3 py-2 text-right uppercase">Updated</th>
              </tr>
            </thead>
            <tbody>
              {status?.assets.map(asset => {
                const completeness = getDataCompleteness(asset);
                const sufficiency = getDataSufficiency(asset);
                return (
                  <tr key={asset.symbol} className="border-b border-[#1e293b]/50 hover:bg-[#1e293b]/10 transition-colors">
                    <td className="px-3 py-2">
                      <span className="text-[10px] font-mono font-bold text-[#f1f5f9]">{asset.symbol}</span>
                    </td>
                    <td className="px-3 py-2">
                      <Badge variant="outline" className="text-[7px] font-mono px-1.5 py-0 h-4 bg-[#1e293b] text-[#94a3b8] border-[#334155]">
                        {asset.assetClass}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="text-[10px] font-mono text-[#94a3b8]">{formatNumber(asset.candleCount)}</span>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <div className="flex items-center justify-center gap-0.5 flex-wrap">
                        {TIMEFRAMES.map(tf => {
                          const has = asset.timeframes.some(atf => atf.timeframe === tf);
                          return (
                            <span key={tf} className={`text-[7px] font-mono px-1 py-0.5 rounded ${
                              has ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-[#0a0e17] text-[#334155] border border-[#1e293b]'
                            }`}>
                              {tf}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="text-[10px] font-mono text-[#94a3b8]">{formatNumber(asset.totalPatterns)}</span>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <div className="flex items-center justify-center gap-0.5">
                        {TRIE_LEVELS.map(level => (
                          asset.tries[level] ? (
                            <CheckCircle2 key={level} className="h-3 w-3 text-emerald-400" />
                          ) : (
                            <XCircle key={level} className="h-3 w-3 text-[#334155]" />
                          )
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        <div className="flex-1 h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${getCompletenessBg(completeness)}`} style={{ width: `${completeness}%` }} />
                        </div>
                        <span className={`text-[8px] font-mono font-bold ${getCompletenessColor(completeness)}`}>
                          {Math.round(completeness)}%
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <Badge variant="outline" className={`text-[7px] font-mono px-1.5 py-0 h-4 ${
                        sufficiency === 'sufficient' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' :
                        sufficiency === 'partial' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' :
                        'bg-red-500/10 text-red-400 border-red-500/30'
                      }`}>
                        {sufficiency === 'sufficient' ? <CheckCircle2 className="h-2.5 w-2.5 mr-0.5" /> :
                         sufficiency === 'partial' ? <AlertTriangle className="h-2.5 w-2.5 mr-0.5" /> :
                         <XCircle className="h-2.5 w-2.5 mr-0.5" />}
                        {sufficiency === 'sufficient' ? 'OK' : sufficiency === 'partial' ? 'PARTIAL' : 'LOW'}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="text-[8px] font-mono text-[#475569]">{formatDate(asset.lastUpdated)}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Controls Row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {/* Ingestion Controls */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
          <h3 className="text-[10px] font-mono text-[#f1f5f9] uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Download className="h-3.5 w-3.5 text-[#3b82f6]" /> Ingestion Controls
          </h3>
          <div className="space-y-2">
            <div className="flex gap-1">
              <input
                type="text"
                placeholder="New symbol e.g. AVAX"
                value={newSymbol}
                onChange={(e) => setNewSymbol(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleIngestNew()}
                className="flex-1 h-7 px-2 text-[9px] bg-[#0a0e17] border border-[#1e293b] rounded text-[#94a3b8] placeholder-[#334155] focus:border-[#3b82f6]/50 focus:outline-none"
              />
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-[8px] font-mono px-2 bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20"
                onClick={handleIngestNew}
                disabled={!newSymbol.trim() || ingestMutation.isPending}
              >
                {ingestMutation.isPending ? <Loader2 className="h-3 w-3 mr-0.5 animate-spin" /> : <Plus className="h-3 w-3 mr-0.5" />}
                Ingest
              </Button>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full h-7 text-[8px] font-mono bg-amber-500/10 border-amber-500/30 text-amber-400 hover:bg-amber-500/20"
              onClick={handleBulkIngest}
              disabled={ingestMutation.isPending}
            >
              {ingestMutation.isPending ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Download className="h-3 w-3 mr-1" />}
              Bulk Ingest All Missing
            </Button>
            {ingestMutation.isPending && (
              <div className="flex items-center gap-1.5">
                <Loader2 className="h-3 w-3 text-[#3b82f6] animate-spin" />
                <span className="text-[8px] font-mono text-[#3b82f6]">Ingesting data...</span>
              </div>
            )}
          </div>
        </div>

        {/* Build Controls */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
          <h3 className="text-[10px] font-mono text-[#f1f5f9] uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Layers className="h-3.5 w-3.5 text-[#3b82f6]" /> Build Controls
          </h3>
          <div className="space-y-2">
            <Button
              size="sm"
              variant="outline"
              className="w-full h-7 text-[8px] font-mono bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20"
              onClick={handleBuildAll}
              disabled={buildMutation.isPending}
            >
              {buildMutation.isPending ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Layers className="h-3 w-3 mr-1" />}
              Build Trie for All Assets
            </Button>
            {buildAllStatus && (
              <div className="flex items-center gap-1.5">
                {buildAllStatus === 'Building...' ? (
                  <Loader2 className="h-3 w-3 text-[#3b82f6] animate-spin" />
                ) : (
                  <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                )}
                <span className={`text-[8px] font-mono ${buildAllStatus === 'Building...' ? 'text-[#3b82f6]' : 'text-emerald-400'}`}>
                  {buildAllStatus}
                </span>
              </div>
            )}
            <Button
              size="sm"
              variant="outline"
              className="w-full h-7 text-[8px] font-mono bg-[#0a0e17] border-[#334155] text-[#94a3b8] hover:text-[#f1f5f9]"
              onClick={() => queryClient.invalidateQueries({ queryKey: ['ppmt-status'] })}
            >
              <RefreshCw className="h-3 w-3 mr-1" />
              Refresh Status
            </Button>
          </div>
        </div>

        {/* Database Stats */}
        <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-3">
          <h3 className="text-[10px] font-mono text-[#f1f5f9] uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <HardDrive className="h-3.5 w-3.5 text-[#3b82f6]" /> Database Stats
          </h3>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-[8px] font-mono text-[#475569]">DB Size</span>
              <span className="text-[10px] font-mono font-bold text-[#f1f5f9]">{status?.dbSizeMB ?? '—'} MB</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[8px] font-mono text-[#475569]">Total Records</span>
              <span className="text-[10px] font-mono font-bold text-[#94a3b8]">{status ? formatNumber(status.totalCandles) : '—'}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[8px] font-mono text-[#475569]">Total Patterns</span>
              <span className="text-[10px] font-mono font-bold text-[#94a3b8]">{status ? formatNumber(status.totalPatterns) : '—'}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[8px] font-mono text-[#475569]">Signal Count</span>
              <span className="text-[10px] font-mono font-bold text-[#94a3b8]">{status?.signalCount ?? '—'}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[8px] font-mono text-[#475569]">Assets Tracked</span>
              <span className="text-[10px] font-mono font-bold text-[#94a3b8]">{status?.totalAssets ?? '—'}</span>
            </div>
            <div className="pt-1 border-t border-[#1e293b]">
              <div className="flex items-center gap-1">
                <Server className="h-3 w-3 text-emerald-400" />
                <span className="text-[8px] font-mono text-emerald-400">System Online</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// TAB 3: BACKTESTING LAB
// ============================================================

function BacktestingLabTab({ status }: { status: StatusData | undefined }) {
  // Single Backtest
  const [btSymbol, setBtSymbol] = useState('BTC');
  const [btTimeframe, setBtTimeframe] = useState('1h');
  const [btResult, setBtResult] = useState<BacktestResult | null>(null);
  const [btRunning, setBtRunning] = useState(false);

  // Monte Carlo
  const [mcSymbol, setMcSymbol] = useState('BTC');
  const [mcTimeframe, setMcTimeframe] = useState('1h');
  const [mcSimulations, setMcSimulations] = useState(500);
  const [mcResult, setMcResult] = useState<MCResponse | null>(null);
  const mcMutation = useMonteCarlo();

  // Multi-Asset
  const [multiTimeframe, setMultiTimeframe] = useState('1h');
  const [multiResult, setMultiResult] = useState<MultiBacktestResponse | null>(null);
  const multiMutation = useMultiBacktest();

  const symbols = useMemo(() => status?.assets.map(a => a.symbol) ?? [], [status?.assets]);

  // Auto-set symbol from status
  useEffect(() => {
    if (symbols.length && !symbols.includes(btSymbol)) {
      setBtSymbol(symbols[0]);
      setMcSymbol(symbols[0]);
    }
  }, [symbols]);

  const runBacktest = useCallback(async () => {
    if (!btSymbol) return;
    setBtRunning(true);
    try {
      const res = await fetch('/api/ppmt/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: btSymbol, timeframe: btTimeframe }),
      });
      if (!res.ok) throw new Error('Backtest failed');
      const json = await res.json();
      setBtResult(json.data as BacktestResult);
    } catch (err) {
      console.error('Backtest error:', err);
    } finally {
      setBtRunning(false);
    }
  }, [btSymbol, btTimeframe]);

  const runMonteCarlo = useCallback(async () => {
    if (!mcSymbol) return;
    try {
      const result = await mcMutation.mutateAsync({ symbol: mcSymbol, timeframe: mcTimeframe, simulations: mcSimulations });
      setMcResult(result);
    } catch (err) {
      console.error('MC error:', err);
    }
  }, [mcSymbol, mcTimeframe, mcSimulations, mcMutation]);

  const runMultiBacktest = useCallback(async () => {
    try {
      const result = await multiMutation.mutateAsync({ timeframe: multiTimeframe });
      setMultiResult(result);
    } catch (err) {
      console.error('Multi-backtest error:', err);
    }
  }, [multiTimeframe, multiMutation]);

  const recentTrades = btResult?.trades?.slice(-10).reverse() || [];
  const sortedMultiResults = useMemo(() => {
    if (!multiResult?.results) return [];
    return [...multiResult.results].sort((a, b) => b.total_return - a.total_return);
  }, [multiResult]);

  return (
    <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
      {/* Single Asset Backtest */}
      <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-[#1e293b] flex items-center justify-between">
          <h3 className="text-[10px] font-mono text-[#f1f5f9] uppercase tracking-wider flex items-center gap-1.5">
            <FlaskConical className="h-3.5 w-3.5 text-[#10b981]" /> Single Asset Backtest
          </h3>
          <div className="flex items-center gap-1.5">
            {/* Symbol selector */}
            <select
              value={btSymbol}
              onChange={(e) => setBtSymbol(e.target.value)}
              className="h-6 px-2 text-[9px] font-mono bg-[#0a0e17] border border-[#1e293b] rounded text-[#94a3b8] focus:outline-none focus:border-[#3b82f6]/50"
            >
              {symbols.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            {/* Timeframe selector */}
            <div className="flex items-center gap-0.5">
              {['1h', '4h', '1d'].map(tf => (
                <button
                  key={tf}
                  onClick={() => setBtTimeframe(tf)}
                  className={`px-1.5 py-0.5 text-[8px] font-mono rounded transition-colors ${
                    btTimeframe === tf
                      ? 'bg-[#3b82f6]/20 text-[#3b82f6] border border-[#3b82f6]/30'
                      : 'bg-[#0a0e17] text-[#475569] border border-[#1e293b] hover:text-[#94a3b8]'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-[8px] font-mono px-2 bg-[#10b981]/10 border-[#10b981]/30 text-[#10b981] hover:bg-[#10b981]/20"
              onClick={runBacktest}
              disabled={!btSymbol || btRunning}
            >
              {btRunning ? <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" /> : <Play className="h-2.5 w-2.5 mr-0.5" />}
              Run
            </Button>
          </div>
        </div>

        {btResult && !btRunning ? (
          <div className="p-3">
            {btResult.message && (
              <div className="mb-2 px-2 py-1 bg-amber-500/5 border border-amber-500/20 rounded">
                <p className="text-[8px] font-mono text-amber-400">{btResult.message}</p>
              </div>
            )}
            {/* Stats Grid */}
            <div className="grid grid-cols-3 md:grid-cols-6 gap-1.5 mb-3">
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Return</p>
                <p className={`text-[12px] font-mono font-bold ${btResult.stats.total_return >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {btResult.stats.total_return >= 0 ? '+' : ''}{btResult.stats.total_return}%
                </p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Sharpe</p>
                <p className="text-[12px] font-mono font-bold text-[#3b82f6]">{btResult.stats.sharpe}</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Max DD</p>
                <p className="text-[12px] font-mono font-bold text-red-400">-{btResult.stats.max_dd}%</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Win Rate</p>
                <p className="text-[12px] font-mono font-bold text-[#94a3b8]">{btResult.stats.win_rate}%</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Trades</p>
                <p className="text-[12px] font-mono font-bold text-[#f1f5f9]">{btResult.stats.total_trades}</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">PF</p>
                <p className="text-[12px] font-mono font-bold text-[#94a3b8]">{btResult.stats.profit_factor?.toFixed(2) ?? '—'}</p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Equity Curve */}
              <div className="h-[220px] bg-[#0a0e17] border border-[#1e293b] rounded overflow-hidden">
                <EquityCurveChart data={btResult.equity_curve} />
              </div>

              {/* Recent Trades Table */}
              <div>
                <p className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider mb-1">Recent Trades</p>
                <div className="bg-[#0a0e17] border border-[#1e293b] rounded overflow-hidden max-h-[200px] overflow-y-auto">
                  <table className="w-full">
                    <thead className="sticky top-0 bg-[#0a0e17]">
                      <tr className="border-b border-[#1e293b]">
                        <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-left">Dir</th>
                        <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right">Entry</th>
                        <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right">Exit</th>
                        <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right">PnL%</th>
                        <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right">Bars</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentTrades.map((trade, i) => (
                        <tr key={i} className="border-b border-[#1e293b]/50">
                          <td className="px-2 py-1">
                            <Badge variant="outline" className={`text-[7px] font-mono px-1 py-0 h-3 ${
                              trade.direction === 'LONG' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30'
                            }`}>
                              {trade.direction === 'LONG' ? '▲' : '▼'} {trade.direction}
                            </Badge>
                          </td>
                          <td className="text-[8px] font-mono text-[#94a3b8] px-2 py-1 text-right">{trade.entry_price.toFixed(2)}</td>
                          <td className="text-[8px] font-mono text-[#94a3b8] px-2 py-1 text-right">{trade.exit_price.toFixed(2)}</td>
                          <td className={`text-[8px] font-mono font-bold px-2 py-1 text-right ${trade.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {trade.pnl_pct >= 0 ? '+' : ''}{trade.pnl_pct}%
                          </td>
                          <td className="text-[8px] font-mono text-[#475569] px-2 py-1 text-right">{trade.holding_bars}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        ) : btRunning ? (
          <div className="flex flex-col items-center justify-center py-8">
            <Loader2 className="h-6 w-6 text-[#3b82f6] animate-spin mb-2" />
            <p className="text-[10px] font-mono text-[#94a3b8]">Running backtest for {btSymbol}...</p>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-8">
            <LineChart className="h-8 w-8 text-[#334155] mb-2" />
            <p className="text-[10px] font-mono text-[#475569]">Select symbol and timeframe, then click Run</p>
          </div>
        )}
      </div>

      {/* Monte Carlo Simulation */}
      <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-[#1e293b] flex items-center justify-between flex-wrap gap-2">
          <h3 className="text-[10px] font-mono text-[#f1f5f9] uppercase tracking-wider flex items-center gap-1.5">
            <Sigma className="h-3.5 w-3.5 text-[#f59e0b]" /> Monte Carlo Simulation
          </h3>
          <div className="flex items-center gap-1.5">
            <select
              value={mcSymbol}
              onChange={(e) => setMcSymbol(e.target.value)}
              className="h-6 px-2 text-[9px] font-mono bg-[#0a0e17] border border-[#1e293b] rounded text-[#94a3b8] focus:outline-none focus:border-[#3b82f6]/50"
            >
              {symbols.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <div className="flex items-center gap-0.5">
              {['1h', '4h', '1d'].map(tf => (
                <button
                  key={tf}
                  onClick={() => setMcTimeframe(tf)}
                  className={`px-1.5 py-0.5 text-[8px] font-mono rounded transition-colors ${
                    mcTimeframe === tf
                      ? 'bg-[#f59e0b]/20 text-[#f59e0b] border border-[#f59e0b]/30'
                      : 'bg-[#0a0e17] text-[#475569] border border-[#1e293b] hover:text-[#94a3b8]'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[8px] font-mono text-[#475569]">Sims:</span>
              <input
                type="number"
                value={mcSimulations}
                onChange={(e) => setMcSimulations(parseInt(e.target.value) || 500)}
                className="w-14 h-6 px-1 text-[9px] font-mono bg-[#0a0e17] border border-[#1e293b] rounded text-[#94a3b8] text-center focus:outline-none focus:border-[#3b82f6]/50"
              />
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-[8px] font-mono px-2 bg-[#f59e0b]/10 border-[#f59e0b]/30 text-[#f59e0b] hover:bg-[#f59e0b]/20"
              onClick={runMonteCarlo}
              disabled={!mcSymbol || mcMutation.isPending}
            >
              {mcMutation.isPending ? <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" /> : <Play className="h-2.5 w-2.5 mr-0.5" />}
              Run MC
            </Button>
          </div>
        </div>

        {mcResult?.mc ? (
          <div className="p-3">
            {/* MC Stats */}
            <div className="grid grid-cols-3 md:grid-cols-7 gap-1.5 mb-3">
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">VaR 95%</p>
                <p className="text-[11px] font-mono font-bold text-red-400">{mcResult.stats.var95.toFixed(1)}%</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">CVaR 95%</p>
                <p className="text-[11px] font-mono font-bold text-red-400">{mcResult.stats.cvar95.toFixed(1)}%</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Mean</p>
                <p className={`text-[11px] font-mono font-bold ${mcResult.stats.meanReturn >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {mcResult.stats.meanReturn.toFixed(1)}%
                </p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Median</p>
                <p className="text-[11px] font-mono font-bold text-[#94a3b8]">{mcResult.stats.medianReturn.toFixed(1)}%</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Best</p>
                <p className="text-[11px] font-mono font-bold text-emerald-400">{mcResult.stats.bestReturn.toFixed(1)}%</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">Worst</p>
                <p className="text-[11px] font-mono font-bold text-red-400">{mcResult.stats.worstReturn.toFixed(1)}%</p>
              </div>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2 text-center">
                <p className="text-[7px] font-mono text-[#475569] uppercase">% Profit</p>
                <p className="text-[11px] font-mono font-bold text-[#3b82f6]">{mcResult.stats.pctProfitable.toFixed(0)}%</p>
              </div>
            </div>

            {/* MC Equity Paths Chart */}
            <div className="h-[240px] bg-[#0a0e17] border border-[#1e293b] rounded overflow-hidden mb-3">
              <MCEquityPathsChart paths={mcResult.mc.equityPaths} />
            </div>

            {/* Distribution Histogram */}
            {mcResult.mc.distribution.length > 0 && (
              <div>
                <p className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider mb-1">Return Distribution</p>
                <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-2">
                  <div className="flex items-end gap-px h-[80px]">
                    {mcResult.mc.distribution.map((val, i) => {
                      const max = Math.max(...mcResult.mc.distribution);
                      const height = max > 0 ? (val / max) * 100 : 0;
                      const isLoss = i < mcResult.mc.distribution.length / 2;
                      return (
                        <div
                          key={i}
                          className={`flex-1 min-w-[2px] rounded-t ${isLoss ? 'bg-red-500/60' : 'bg-emerald-500/60'}`}
                          style={{ height: `${height}%` }}
                          title={`${val}`}
                        />
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : mcMutation.isPending ? (
          <div className="flex flex-col items-center justify-center py-8">
            <Loader2 className="h-6 w-6 text-[#f59e0b] animate-spin mb-2" />
            <p className="text-[10px] font-mono text-[#94a3b8]">Running Monte Carlo ({mcSimulations} simulations)...</p>
          </div>
        ) : mcResult?.message ? (
          <div className="p-3">
            <div className="px-2 py-1.5 bg-amber-500/5 border border-amber-500/20 rounded">
              <p className="text-[8px] font-mono text-amber-400">{mcResult.message}</p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-8">
            <Sigma className="h-8 w-8 text-[#334155] mb-2" />
            <p className="text-[10px] font-mono text-[#475569]">Configure and run Monte Carlo simulation</p>
          </div>
        )}
      </div>

      {/* Multi-Asset Comparison */}
      <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-[#1e293b] flex items-center justify-between">
          <h3 className="text-[10px] font-mono text-[#f1f5f9] uppercase tracking-wider flex items-center gap-1.5">
            <BarChart3 className="h-3.5 w-3.5 text-[#8b5cf6]" /> Multi-Asset Comparison
          </h3>
          <div className="flex items-center gap-1.5">
            <div className="flex items-center gap-0.5">
              {['1h', '4h', '1d'].map(tf => (
                <button
                  key={tf}
                  onClick={() => setMultiTimeframe(tf)}
                  className={`px-1.5 py-0.5 text-[8px] font-mono rounded transition-colors ${
                    multiTimeframe === tf
                      ? 'bg-[#8b5cf6]/20 text-[#8b5cf6] border border-[#8b5cf6]/30'
                      : 'bg-[#0a0e17] text-[#475569] border border-[#1e293b] hover:text-[#94a3b8]'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-[8px] font-mono px-2 bg-[#8b5cf6]/10 border-[#8b5cf6]/30 text-[#8b5cf6] hover:bg-[#8b5cf6]/20"
              onClick={runMultiBacktest}
              disabled={multiMutation.isPending}
            >
              {multiMutation.isPending ? <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" /> : <Play className="h-2.5 w-2.5 mr-0.5" />}
              Run All
            </Button>
          </div>
        </div>

        {sortedMultiResults.length > 0 ? (
          <div className="p-3">
            {/* Visual Comparison Bars */}
            <div className="mb-3 space-y-1">
              {sortedMultiResults.map((r, i) => {
                const maxAbs = Math.max(...sortedMultiResults.map(x => Math.abs(x.total_return)), 1);
                const width = Math.abs(r.total_return) / maxAbs * 100;
                return (
                  <div key={r.symbol} className="flex items-center gap-2">
                    <span className="text-[9px] font-mono font-bold text-[#f1f5f9] w-12 text-right">{r.symbol}</span>
                    <div className="flex-1 h-4 bg-[#0a0e17] rounded overflow-hidden relative">
                      <div
                        className={`h-full rounded transition-all ${r.total_return >= 0 ? 'bg-emerald-500/60' : 'bg-red-500/60'}`}
                        style={{ width: `${width}%` }}
                      />
                    </div>
                    <span className={`text-[9px] font-mono font-bold w-16 text-right ${r.total_return >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {r.total_return >= 0 ? '+' : ''}{r.total_return.toFixed(1)}%
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Results Table */}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px]">
                <thead>
                  <tr className="border-b border-[#1e293b]">
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-left uppercase">#</th>
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-left uppercase">Symbol</th>
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-left uppercase">Class</th>
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right uppercase">Return</th>
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right uppercase">Sharpe</th>
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right uppercase">Max DD</th>
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right uppercase">Win%</th>
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right uppercase">Trades</th>
                    <th className="text-[7px] font-mono text-[#475569] px-2 py-1 text-right uppercase">PF</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedMultiResults.map((r, i) => (
                    <tr key={r.symbol} className="border-b border-[#1e293b]/50 hover:bg-[#1e293b]/10 transition-colors">
                      <td className="px-2 py-1">
                        <span className={`text-[8px] font-mono font-bold ${i === 0 ? 'text-emerald-400' : i === 1 ? 'text-[#3b82f6]' : 'text-[#475569]'}`}>
                          {i + 1}
                        </span>
                      </td>
                      <td className="px-2 py-1">
                        <span className="text-[9px] font-mono font-bold text-[#f1f5f9]">{r.symbol}</span>
                      </td>
                      <td className="px-2 py-1">
                        <Badge variant="outline" className="text-[6px] font-mono px-1 py-0 h-3 bg-[#1e293b] text-[#94a3b8] border-[#334155]">
                          {r.asset_class}
                        </Badge>
                      </td>
                      <td className={`px-2 py-1 text-right text-[9px] font-mono font-bold ${r.total_return >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {r.total_return >= 0 ? '+' : ''}{r.total_return.toFixed(1)}%
                      </td>
                      <td className="px-2 py-1 text-right text-[9px] font-mono text-[#3b82f6]">{r.sharpe.toFixed(2)}</td>
                      <td className="px-2 py-1 text-right text-[9px] font-mono text-red-400">-{r.max_dd.toFixed(1)}%</td>
                      <td className="px-2 py-1 text-right text-[9px] font-mono text-[#94a3b8]">{r.win_rate.toFixed(0)}%</td>
                      <td className="px-2 py-1 text-right text-[9px] font-mono text-[#94a3b8]">{r.total_trades}</td>
                      <td className="px-2 py-1 text-right text-[9px] font-mono text-[#94a3b8]">{r.profit_factor.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : multiMutation.isPending ? (
          <div className="flex flex-col items-center justify-center py-8">
            <Loader2 className="h-6 w-6 text-[#8b5cf6] animate-spin mb-2" />
            <p className="text-[10px] font-mono text-[#94a3b8]">Running backtest for all assets...</p>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-8">
            <BarChart3 className="h-8 w-8 text-[#334155] mb-2" />
            <p className="text-[10px] font-mono text-[#475569]">Run all assets to compare performance</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// MAIN DASHBOARD
// ============================================================

export default function PPMTDashboard() {
  const [activeTab, setActiveTab] = useState<TabId>('dashboard');
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [selectedTimeframe, setSelectedTimeframe] = useState('1h');

  const { data: status, isLoading: statusLoading, error: statusError } = usePPMTStatus();
  const { data: signals } = usePPMTSignals();

  const queryClient = useQueryClient();
  const ingestMutation = useIngestAsset();
  const buildMutation = useBuildAsset();

  // Auto-select first asset
  const effectiveSymbol = selectedSymbol ?? (status?.assets?.length ? status.assets[0].symbol : null);
  const selectedAsset = status?.assets.find(a => a.symbol === effectiveSymbol);

  // Compute effective timeframe
  const availableTimeframes = selectedAsset?.timeframes?.map(tf => tf.timeframe) || [];
  const effectiveTimeframe = availableTimeframes.includes(selectedTimeframe)
    ? selectedTimeframe
    : (['1h', '4h', '1d', '15m', '5m', '1m'].find(tf => availableTimeframes.includes(tf)) ?? selectedTimeframe);

  const { data: prediction } = usePPMTPrediction(effectiveSymbol);
  const { data: ohlcvData, isLoading: ohlcvLoading } = useOHLCV(effectiveSymbol, effectiveTimeframe);

  const tabs: { id: TabId; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'command', label: 'Command Center', icon: Terminal },
    { id: 'backtest', label: 'Backtesting Lab', icon: FlaskConical },
  ];

  return (
    <div className="flex flex-col h-screen bg-[#0a0e17] overflow-hidden font-mono">
      {/* ===== TOP BAR ===== */}
      <div className="flex items-center justify-between px-4 h-10 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-3">
          {/* Logo */}
          <div className="flex items-center gap-1.5">
            <Brain className="h-4 w-4 text-[#3b82f6]" />
            <span className="text-[#3b82f6] text-xs font-bold tracking-wider">PPMT</span>
            <span className="text-[#475569] text-[8px]">Command Center</span>
          </div>
          <div className="h-4 w-px bg-[#1e293b]" />
          {/* Status */}
          <div className="flex items-center gap-1">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[9px] text-emerald-400">ACTIVE</span>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex items-center gap-0.5">
          {tabs.map(tab => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[9px] font-mono transition-colors ${
                  activeTab === tab.id
                    ? 'bg-[#3b82f6]/10 text-[#3b82f6] border border-[#3b82f6]/30'
                    : 'text-[#64748b] hover:text-[#94a3b8] hover:bg-[#1e293b]/30 border border-transparent'
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* Right info */}
        <div className="flex items-center gap-3">
          {status && (
            <span className="text-[9px] text-[#475569]">DB: {status.dbSizeMB} MB</span>
          )}
          <div className="flex items-center gap-1">
            <Clock className="h-3 w-3 text-[#475569]" />
            <span className="text-[9px] text-[#475569]">
              {new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        </div>
      </div>

      {/* ===== TAB CONTENT ===== */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {activeTab === 'dashboard' && (
          <DashboardTab
            status={status}
            statusLoading={statusLoading}
            statusError={statusError as Error | null}
            selectedSymbol={selectedSymbol}
            setSelectedSymbol={setSelectedSymbol}
            selectedTimeframe={selectedTimeframe}
            setSelectedTimeframe={setSelectedTimeframe}
            effectiveSymbol={effectiveSymbol}
            effectiveTimeframe={effectiveTimeframe}
            selectedAsset={selectedAsset}
            availableTimeframes={availableTimeframes}
            prediction={prediction}
            ohlcvData={ohlcvData}
            ohlcvLoading={ohlcvLoading}
            signals={signals}
            ingestMutation={ingestMutation}
            buildMutation={buildMutation}
            queryClient={queryClient}
          />
        )}
        {activeTab === 'command' && (
          <CommandCenterTab
            status={status}
            statusLoading={statusLoading}
            statusError={statusError as Error | null}
            ingestMutation={ingestMutation}
            buildMutation={buildMutation}
            queryClient={queryClient}
          />
        )}
        {activeTab === 'backtest' && (
          <BacktestingLabTab status={status} />
        )}
      </div>
    </div>
  );
}
