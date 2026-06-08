'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
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
  Github,
  Play,
  LineChart,
  CandlestickChart as CandlestickChartIcon,
  GitBranch,
  Gauge,
  ArrowRight,
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
  return (tfScore * 0.4 + candleScore * 0.6) * 100;
}

function getCompletenessColor(pct: number): string {
  if (pct >= 70) return 'text-emerald-400';
  if (pct >= 40) return 'text-amber-400';
  return 'text-red-400';
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

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'];

// ============================================================
// CANDLESTICK CHART COMPONENT
// ============================================================

function CandlestickChart({ candles, symbol, timeframe }: {
  candles: OHLCVCandle[];
  symbol: string;
  timeframe: string;
}) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Clean up existing chart
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
      height: 400,
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

    // Add OHLCV crosshair tooltip
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

    // Handle resize
    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width });
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

  // Update data when candles change
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !candles.length) return;

    const sorted = [...candles].sort((a, b) => a.time - b.time);

    // Deduplicate by time
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

    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  return (
    <div className="relative">
      <div className="absolute top-2 left-3 z-10 flex items-center gap-2">
        <CandlestickChartIcon className="h-3.5 w-3.5 text-[#3b82f6]" />
        <span className="font-mono text-[10px] text-[#94a3b8] font-bold">{symbol}</span>
        <span className="font-mono text-[9px] text-[#475569]">{timeframe}</span>
        <Badge variant="outline" className="text-[7px] font-mono px-1.5 py-0 h-4 bg-[#1e293b] text-[#64748b] border-[#334155]">
          {formatNumber(candles.length)} candles
        </Badge>
      </div>
      <div ref={chartContainerRef} className="w-full" />
    </div>
  );
}

// ============================================================
// EQUITY CURVE CHART COMPONENT
// ============================================================

function EquityCurveChart({ data }: { data: { time: number; value: number }[] }) {
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
      height: 200,
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
      color: '#10b981',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    lineSeries.setData(data.map(d => ({ time: d.time as UTCTimestamp, value: d.value })));
    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-[200px] bg-[#0a0e17] rounded">
        <p className="text-[10px] font-mono text-[#475569]">No equity data</p>
      </div>
    );
  }

  return <div ref={chartContainerRef} className="w-full" />;
}

// ============================================================
// STAT CARD COMPONENT
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

// ============================================================
// ASSET ROW COMPONENT
// ============================================================

function AssetRow({ asset, isSelected, onSelect }: {
  asset: AssetDetail;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const completeness = getDataCompleteness(asset);
  const hasAllTries = asset.tries.N1 && asset.tries.N2 && asset.tries.N3 && asset.tries.N4;

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
              className={`h-full rounded-full transition-all ${
                completeness >= 70 ? 'bg-emerald-500' : completeness >= 40 ? 'bg-amber-500' : 'bg-red-500'
              }`}
              style={{ width: `${completeness}%` }}
            />
          </div>
        </div>
        <span className={`text-[8px] font-mono font-bold ${getCompletenessColor(completeness)}`}>
          {Math.round(completeness)}%
        </span>
      </div>
      <div className="flex items-center gap-1 mt-1">
        {asset.timeframes.map(tf => (
          <Badge key={tf.timeframe} variant="outline" className="text-[6px] font-mono px-1 py-0 h-3 bg-[#0a0e17] text-emerald-400 border-emerald-500/30">
            {tf.timeframe}
          </Badge>
        ))}
        {!asset.timeframes.length && (
          <span className="text-[7px] font-mono text-red-400">No data</span>
        )}
      </div>
    </button>
  );
}

// ============================================================
// PREDICTION PANEL COMPONENT
// ============================================================

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

  // Parse prediction output for structured display
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
  const trieLevels = ['N1', 'N2', 'N3', 'N4'];
  const trieDescriptions: Record<string, string> = {
    N1: 'Universal',
    N2: 'Class',
    N3: 'Asset',
    N4: 'Regime',
  };
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
                <span className="text-[9px] font-mono text-[#64748b]">Confidence:</span>
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
          {trieLevels.map(level => {
            const trie = asset.tries[level];
            return (
              <div key={level} className={`p-1.5 rounded border ${
                trie ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-[#0a0e17] border-[#1e293b]'
              }`}>
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-[8px] font-mono font-bold text-[#f1f5f9]">{level}</span>
                  {trie ? <CheckCircle2 className="h-2.5 w-2.5 text-emerald-400" /> : <XCircle className="h-2.5 w-2.5 text-[#475569]" />}
                </div>
                <p className="text-[6px] font-mono text-[#475569]">{trieDescriptions[level]}</p>
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
            {trieLevels.map((level, i) => {
              const w = Object.values(weights)[i] as number | undefined;
              return (
                <span key={level} className="text-[7px] font-mono text-[#3b82f6]">{level}={w ? `${(w * 100).toFixed(0)}%` : '—'}</span>
              );
            })}
          </div>
        )}
      </div>

      {/* Pattern Match Quality */}
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
            {['N1', 'N2', 'N3', 'N4'].map((level, i) => (
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

// ============================================================
// BACKTEST PANEL COMPONENT
// ============================================================

function BacktestPanel({ symbol }: { symbol: string | null }) {
  const [backtestTimeframe, setBacktestTimeframe] = useState('1h');
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  const runBacktest = useCallback(async () => {
    if (!symbol) return;
    setIsRunning(true);
    try {
      const res = await fetch('/api/ppmt/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, timeframe: backtestTimeframe }),
      });
      if (!res.ok) throw new Error('Backtest failed');
      const json = await res.json();
      setResult(json.data as BacktestResult);
    } catch (err) {
      console.error('Backtest error:', err);
    } finally {
      setIsRunning(false);
    }
  }, [symbol, backtestTimeframe]);

  const recentTrades = result?.trades?.slice(-10).reverse() || [];

  return (
    <div className="flex flex-col h-full">
      {/* Backtest Controls */}
      <div className="px-3 py-2 border-b border-[#1e293b] flex items-center justify-between shrink-0">
        <h3 className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider flex items-center gap-1">
          <LineChart className="h-3 w-3" /> Backtest
        </h3>
        <div className="flex items-center gap-1.5">
          <div className="flex items-center gap-0.5">
            {['1h', '4h', '1d'].map(tf => (
              <button
                key={tf}
                onClick={() => setBacktestTimeframe(tf)}
                className={`px-1.5 py-0.5 text-[8px] font-mono rounded transition-colors ${
                  backtestTimeframe === tf
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
            className="h-5 text-[8px] font-mono px-2 bg-[#10b981]/10 border-[#10b981]/30 text-[#10b981] hover:bg-[#10b981]/20"
            onClick={runBacktest}
            disabled={!symbol || isRunning}
          >
            {isRunning ? <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" /> : <Play className="h-2.5 w-2.5 mr-0.5" />}
            Run
          </Button>
        </div>
      </div>

      {!result && !isRunning && (
        <div className="flex-1 flex flex-col items-center justify-center py-4">
          <LineChart className="h-6 w-6 text-[#334155] mb-2" />
          <p className="text-[10px] font-mono text-[#475569]">No backtest run yet</p>
          <p className="text-[8px] font-mono text-[#334155] mt-0.5">Select timeframe and click Run</p>
        </div>
      )}

      {isRunning && (
        <div className="flex-1 flex flex-col items-center justify-center py-4">
          <Loader2 className="h-6 w-6 text-[#3b82f6] animate-spin mb-2" />
          <p className="text-[10px] font-mono text-[#94a3b8]">Running backtest...</p>
        </div>
      )}

      {result && !isRunning && (
        <div className="flex-1 overflow-y-auto">
          {result.message && (
            <div className="px-3 py-1.5 bg-amber-500/5 border-b border-amber-500/20">
              <p className="text-[8px] font-mono text-amber-400">{result.message}</p>
            </div>
          )}

          {/* Stats Cards */}
          <div className="grid grid-cols-5 gap-1.5 px-3 py-2">
            <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-1.5 text-center">
              <p className="text-[7px] font-mono text-[#475569] uppercase">Return</p>
              <p className={`text-[11px] font-mono font-bold ${result.stats.total_return >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {result.stats.total_return >= 0 ? '+' : ''}{result.stats.total_return}%
              </p>
            </div>
            <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-1.5 text-center">
              <p className="text-[7px] font-mono text-[#475569] uppercase">Max DD</p>
              <p className="text-[11px] font-mono font-bold text-red-400">
                -{result.stats.max_dd}%
              </p>
            </div>
            <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-1.5 text-center">
              <p className="text-[7px] font-mono text-[#475569] uppercase">Win Rate</p>
              <p className="text-[11px] font-mono font-bold text-[#94a3b8]">
                {result.stats.win_rate}%
              </p>
            </div>
            <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-1.5 text-center">
              <p className="text-[7px] font-mono text-[#475569] uppercase">Sharpe</p>
              <p className="text-[11px] font-mono font-bold text-[#3b82f6]">
                {result.stats.sharpe}
              </p>
            </div>
            <div className="bg-[#0a0e17] border border-[#1e293b] rounded p-1.5 text-center">
              <p className="text-[7px] font-mono text-[#475569] uppercase">Trades</p>
              <p className="text-[11px] font-mono font-bold text-[#f1f5f9]">
                {result.stats.total_trades}
              </p>
            </div>
          </div>

          {/* Equity Curve */}
          <div className="px-3 pb-2">
            <EquityCurveChart data={result.equity_curve} />
          </div>

          {/* Recent Trades */}
          {recentTrades.length > 0 && (
            <div className="px-3 pb-2">
              <p className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider mb-1">Recent Trades</p>
              <div className="bg-[#0a0e17] border border-[#1e293b] rounded overflow-hidden">
                <table className="w-full">
                  <thead>
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
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================
// SIGNALS FEED COMPONENT
// ============================================================

function SignalsFeed({ signals }: { signals: SignalData[] | undefined }) {
  const scrollRef = useRef<HTMLDivElement>(null);

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
      <div ref={scrollRef} className="overflow-x-auto overflow-y-hidden whitespace-nowrap px-3 py-2 flex gap-2">
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
// MAIN DASHBOARD
// ============================================================

export default function PPMTDashboard() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [selectedTimeframe, setSelectedTimeframe] = useState('1h');
  const [addSymbolInput, setAddSymbolInput] = useState('');

  const { data: status, isLoading: statusLoading, error: statusError } = usePPMTStatus();
  const { data: signals } = usePPMTSignals();

  // Auto-select first asset and timeframe
  const effectiveSymbol = selectedSymbol ?? (status?.assets?.length ? status.assets[0].symbol : null);
  const selectedAsset = status?.assets.find(a => a.symbol === effectiveSymbol);

  // Compute effective timeframe based on available data
  const availableTimeframes = selectedAsset?.timeframes?.map(tf => tf.timeframe) || [];
  const effectiveTimeframe = availableTimeframes.includes(selectedTimeframe)
    ? selectedTimeframe
    : (['1h', '4h', '1d', '15m', '5m', '1m'].find(tf => availableTimeframes.includes(tf)) ?? selectedTimeframe);

  const { data: prediction } = usePPMTPrediction(effectiveSymbol);
  const { data: ohlcvData, isLoading: ohlcvLoading } = useOHLCV(effectiveSymbol, effectiveTimeframe);

  const queryClient = useQueryClient();

  const handleIngestNew = useCallback(async () => {
    if (!addSymbolInput.trim()) return;
    try {
      const res = await fetch('/api/ppmt/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: addSymbolInput.trim().toUpperCase(), timeframe: '1h', days: 365 }),
      });
      if (res.ok) {
        setAddSymbolInput('');
        setTimeout(() => queryClient.invalidateQueries({ queryKey: ['ppmt-status'] }), 2000);
      }
    } catch { /* ignore */ }
  }, [addSymbolInput, queryClient]);

  const ingestMutation = useMutation({
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

  const buildMutation = useMutation({
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

  const hasDataForTimeframe = availableTimeframes.includes(effectiveTimeframe);

  return (
    <div className="flex flex-col h-screen bg-[#0a0e17] overflow-hidden font-mono">
      {/* ===== TOP BAR ===== */}
      <div className="flex items-center justify-between px-4 h-10 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Brain className="h-4 w-4 text-[#3b82f6]" />
            <span className="text-[#3b82f6] text-xs font-bold tracking-wider">PPMT</span>
            <span className="text-[#475569] text-[8px]">Dashboard</span>
          </div>
          <div className="h-4 w-px bg-[#1e293b]" />
          <div className="flex items-center gap-1">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[9px] text-emerald-400">ACTIVE</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {status && (
            <span className="text-[9px] text-[#475569]">DB: {status.dbSizeMB} MB</span>
          )}
          <a
            href="https://github.com/coverdraft/ppmt"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-[#475569] hover:text-[#94a3b8] transition-colors"
          >
            <Github className="h-3.5 w-3.5" />
            <span className="text-[9px] hidden sm:inline">GitHub</span>
          </a>
        </div>
      </div>

      {/* ===== STATS ROW ===== */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 px-3 py-2 shrink-0">
        <StatCard
          title="Assets"
          value={status?.totalAssets ?? '—'}
          subtitle="Tracked symbols"
          icon={Database}
          colorClass="bg-emerald-500/10 text-emerald-400"
        />
        <StatCard
          title="Candles"
          value={status ? formatNumber(status.totalCandles) : '—'}
          subtitle="OHLCV data points"
          icon={BarChart3}
          colorClass="bg-[#3b82f6]/10 text-[#3b82f6]"
        />
        <StatCard
          title="Patterns"
          value={status ? formatNumber(status.totalPatterns) : '—'}
          subtitle="Unique trie patterns"
          icon={Layers}
          colorClass="bg-amber-500/10 text-amber-400"
        />
        <StatCard
          title="Signals"
          value={status?.signalCount ?? '—'}
          subtitle="Trading signals"
          icon={Radio}
          colorClass="bg-cyan-500/10 text-cyan-400"
        />
      </div>

      {/* ===== MAIN CONTENT ===== */}
      <div className="flex-1 flex min-h-0 px-3 pb-2 gap-2">
        {/* LEFT: Asset Management Panel */}
        <div className="w-[240px] shrink-0 flex flex-col bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
          {/* Header */}
          <div className="px-3 py-2 border-b border-[#1e293b] shrink-0">
            <div className="flex items-center justify-between mb-1.5">
              <h3 className="text-[9px] text-[#64748b] uppercase tracking-wider">Assets</h3>
              <Badge variant="outline" className="text-[7px] px-1.5 py-0 h-3.5 bg-[#1e293b] text-[#94a3b8] border-[#334155]">
                {status?.assets?.length ?? 0}
              </Badge>
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
                disabled={!addSymbolInput.trim()}
                className="h-6 px-1.5 bg-[#3b82f6]/10 border border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20 text-[8px]"
                variant="outline"
              >
                <Plus className="h-3 w-3" />
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
                <p className="text-[8px] text-[#475569] mt-0.5">Make sure PPMT is initialized</p>
              </div>
            ) : !status?.assets?.length ? (
              <div className="px-3 py-6 text-center">
                <Database className="h-5 w-5 text-[#475569] mx-auto mb-1.5" />
                <p className="text-[9px] text-[#475569]">No assets tracked yet</p>
                <p className="text-[8px] text-[#334155] mt-0.5">Add a symbol above</p>
              </div>
            ) : (
              status.assets.map(asset => (
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

        {/* CENTER: Chart + Backtest */}
        <div className="flex-1 min-w-0 flex flex-col gap-2">
          {/* Candlestick Chart Area */}
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
                      onClick={() => {
                        queryClient.invalidateQueries({ queryKey: ['ppmt-ohlcv', effectiveSymbol, effectiveTimeframe] });
                      }}
                    >
                      <RefreshCw className="h-2.5 w-2.5 mr-0.5" />
                      Refresh
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
                  <p className="text-[9px] text-[#334155] mt-0.5 mb-2">Ingest {effectiveTimeframe} data to view chart</p>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-[8px] font-mono px-3 bg-[#3b82f6]/10 border-[#3b82f6]/30 text-[#3b82f6] hover:bg-[#3b82f6]/20"
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
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-full">
                  <Database className="h-8 w-8 text-[#334155] mb-2" />
                  <p className="text-[11px] text-[#475569]">No OHLCV data available</p>
                </div>
              )}
            </div>
          </div>

          {/* Backtest Zone */}
          <div className="h-[320px] shrink-0 bg-[#0d1117] border border-[#1e293b] rounded-lg overflow-hidden">
            <BacktestPanel symbol={effectiveSymbol} />
          </div>
        </div>

        {/* RIGHT: Prediction & Trie Stats */}
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

      {/* ===== SIGNALS FEED ===== */}
      <SignalsFeed signals={signals} />
    </div>
  );
}
