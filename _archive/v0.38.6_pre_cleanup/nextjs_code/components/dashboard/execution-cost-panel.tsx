'use client';

import { useState, useMemo, useCallback } from 'react';
import { useMutation } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  DollarSign,
  Clock,
  Zap,
  AlertTriangle,
  Loader2,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Activity,
  ChevronRight,
  Info,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useCryptoStore,
  type ExecutionCostResult,
  type ExecutionLogEntry,
} from '@/store/crypto-store';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  LineChart,
  Line,
  ReferenceLine,
} from 'recharts';
import { toast } from 'sonner';

// ============================================================
// TYPES
// ============================================================

type OrderSide = 'BUY' | 'SELL';
type Urgency = 'LOW' | 'MEDIUM' | 'HIGH';

interface CostEstimateRequest {
  symbol: string;
  side: OrderSide;
  sizeUsd: number;
  urgency: Urgency;
}

// API response from /api/execution/cost (backend CostEstimate)
interface BackendCostEstimate {
  spreadCostPct: number;
  slippagePct: number;
  marketImpactPct: number;
  gasFeePct: number;
  dexFeePct: number;
  totalCostPct: number;
  totalCostUsd: number;
  estimatedEntryPrice: number;
  breakEvenPct: number;
  recommendation: 'EXECUTE' | 'REDUCE_SIZE' | 'DELAY' | 'REJECT';
}

// ============================================================
// HELPERS
// ============================================================

function formatUsd(v: number): string {
  if (v == null || isNaN(v)) return '$0.00';
  const sign = v < 0 ? '-' : '';
  const abs = Math.abs(v);
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

function costColor(pct: number): string {
  if (pct < 0.5) return 'text-emerald-400';
  if (pct <= 1.0) return 'text-amber-400';
  return 'text-red-400';
}

function costBg(pct: number): string {
  if (pct < 0.5) return 'bg-emerald-500/10 border-emerald-500/20';
  if (pct <= 1.0) return 'bg-amber-500/10 border-amber-500/20';
  return 'bg-red-500/10 border-red-500/20';
}

function urgencyToTimeHorizon(urgency: Urgency): string {
  switch (urgency) {
    case 'LOW': return '30 min';
    case 'MEDIUM': return '10 min';
    case 'HIGH': return 'Immediate';
  }
}

function urgencyToEstimatedTime(urgency: Urgency, baseTimeSec: number): number {
  switch (urgency) {
    case 'LOW': return baseTimeSec * 3;
    case 'MEDIUM': return baseTimeSec * 1.5;
    case 'HIGH': return baseTimeSec;
  }
}

function getRecommendationOrderType(
  totalCostPct: number,
  urgency: Urgency
): { orderType: string; timeHorizon: string; splitCount?: number } {
  if (urgency === 'HIGH' && totalCostPct < 0.5) {
    return { orderType: 'Market', timeHorizon: 'Immediate' };
  }
  if (urgency === 'HIGH' && totalCostPct >= 0.5) {
    return { orderType: 'TWAP', timeHorizon: '5 min', splitCount: 4 };
  }
  if (totalCostPct < 0.3) {
    return { orderType: 'Limit', timeHorizon: '15 min' };
  }
  if (totalCostPct < 1.0) {
    return { orderType: 'TWAP', timeHorizon: '10 min', splitCount: 3 };
  }
  if (totalCostPct < 2.0) {
    return { orderType: 'VWAP', timeHorizon: '30 min', splitCount: 5 };
  }
  return { orderType: 'VWAP', timeHorizon: '60 min', splitCount: 8 };
}

function generateAlmgrenChrissTrajectory(
  totalCostPct: number,
  estimatedTimeSec: number,
  side: OrderSide
): Array<{ t: number; price: number }> {
  const points = 20;
  const impactDirection = side === 'BUY' ? 1 : -1;
  const maxImpact = (totalCostPct / 100) * impactDirection;
  const eta = 0.142;
  const gamma = 0.314;
  const tempFraction = eta / (eta + gamma);

  const data: Array<{ t: number; price: number }> = [];
  for (let i = 0; i <= points; i++) {
    const progress = i / points;
    const sqrtProgress = Math.sqrt(progress);
    const tempImpact = maxImpact * tempFraction * sqrtProgress;
    const permImpact = maxImpact * (1 - tempFraction) * progress;
    const totalImpact = tempImpact + permImpact;
    // Price starts at 100 (normalized), deviates based on impact
    const noise = (Math.random() - 0.5) * maxImpact * 0.15;
    data.push({
      t: progress * estimatedTimeSec,
      price: 100 * (1 + totalImpact + noise),
    });
  }
  return data;
}

// ============================================================
// CUSTOM TOOLTIP FOR BAR CHART
// ============================================================

function CostBreakdownTooltip({ active, payload }: any) {
  if (!active || !payload || !payload.length) return null;
  const data = payload[0]?.payload;
  if (!data) return null;

  return (
    <div className="bg-[#111827] border border-[#1e293b] rounded-lg px-3 py-2 shadow-xl">
      <p className="text-[10px] font-mono text-[#e2e8f0] font-bold mb-1">{data.name}</p>
      <p className="text-[10px] font-mono text-[#94a3b8]">
        {data.value.toFixed(4)}%
      </p>
    </div>
  );
}

// ============================================================
// COST BREAKDOWN BAR CHART
// ============================================================

function CostBreakdownChart({ result }: { result: ExecutionCostResult }) {
  const data = [
    { name: 'Slippage', value: result.slippagePct, color: '#3b82f6' },
    { name: 'Market Impact', value: result.marketImpactPct, color: '#8b5cf6' },
    { name: 'Exchange Fee', value: result.feePct, color: '#f59e0b' },
    { name: 'Network Fee', value: result.networkFeePct, color: '#06b6d4' },
  ];

  return (
    <div className="w-full h-[180px]">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 5, right: 40, left: 5, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
          <XAxis
            type="number"
            tick={{ fontSize: 9, fontFamily: 'monospace', fill: '#64748b' }}
            tickFormatter={(v: number) => `${v.toFixed(2)}%`}
            stroke="#1e293b"
          />
          <YAxis
            type="category"
            dataKey="name"
            tick={{ fontSize: 9, fontFamily: 'monospace', fill: '#94a3b8' }}
            width={90}
            stroke="#1e293b"
          />
          <Tooltip content={<CostBreakdownTooltip />} />
          <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={14}>
            {data.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} fillOpacity={0.85} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ============================================================
// ALMGREN-CHRISS TRAJECTORY CHART
// ============================================================

function TrajectoryChart({
  trajectory,
  side,
}: {
  trajectory: Array<{ t: number; price: number }>;
  side: OrderSide;
}) {
  const lineColor = side === 'BUY' ? '#ef4444' : '#10b981';

  return (
    <div className="w-full h-[160px]">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={trajectory} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis
            dataKey="t"
            tick={{ fontSize: 8, fontFamily: 'monospace', fill: '#475569' }}
            tickFormatter={(v: number) => `${v.toFixed(0)}s`}
            stroke="#1e293b"
          />
          <YAxis
            domain={['auto', 'auto']}
            tick={{ fontSize: 8, fontFamily: 'monospace', fill: '#475569' }}
            tickFormatter={(v: number) => v.toFixed(2)}
            stroke="#1e293b"
            width={45}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#111827',
              border: '1px solid #1e293b',
              borderRadius: '6px',
              fontSize: '9px',
              fontFamily: 'monospace',
            }}
            labelFormatter={(v: number) => `t = ${v.toFixed(1)}s`}
            formatter={(value: number) => [value.toFixed(4), 'Price']}
          />
          <ReferenceLine
            y={100}
            stroke="#475569"
            strokeDasharray="4 4"
            label={{
              value: 'Start',
              position: 'right',
              fill: '#475569',
              fontSize: 8,
              fontFamily: 'monospace',
            }}
          />
          <Line
            type="monotone"
            dataKey="price"
            stroke={lineColor}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3, fill: lineColor }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ============================================================
// EXECUTION LOG TABLE
// ============================================================

function ExecutionLogTable({ entries }: { entries: ExecutionLogEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8">
        <Activity className="h-5 w-5 text-[#475569] mb-2" />
        <span className="text-[10px] font-mono text-[#475569]">
          No recent executions
        </span>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto max-h-48 overflow-y-auto custom-scrollbar">
      <table className="w-full text-[9px] font-mono">
        <thead className="sticky top-0 bg-[#0d1117] z-10">
          <tr className="border-b border-[#1e293b]">
            <th className="text-left py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Time</th>
            <th className="text-left py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Token</th>
            <th className="text-left py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Side</th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Size</th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Est. Cost</th>
            <th className="text-right py-1.5 px-2 text-[#64748b] uppercase tracking-wider">Actual</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, i) => (
            <motion.tr
              key={entry.id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.04 }}
              className="border-b border-[#1e293b]/40 hover:bg-[#1e293b]/20 transition-colors"
            >
              <td className="py-1.5 px-2 text-[#94a3b8]">
                {new Date(entry.time).toLocaleTimeString()}
              </td>
              <td className="py-1.5 px-2 text-[#e2e8f0] font-bold">{entry.token}</td>
              <td className="py-1.5 px-2">
                <Badge
                  className="text-[8px] h-4 px-1.5 font-mono"
                  style={{
                    backgroundColor: entry.side === 'BUY' ? '#10b98120' : '#ef444420',
                    color: entry.side === 'BUY' ? '#10b981' : '#ef4444',
                    borderColor: entry.side === 'BUY' ? '#10b98140' : '#ef444440',
                  }}
                >
                  {entry.side}
                </Badge>
              </td>
              <td className="text-right py-1.5 px-2 text-[#e2e8f0]">
                {formatUsd(entry.size)}
              </td>
              <td className={`text-right py-1.5 px-2 font-bold ${costColor(entry.estimatedCost)}`}>
                {entry.estimatedCost.toFixed(3)}%
              </td>
              <td className="text-right py-1.5 px-2 text-[#64748b]">
                {entry.actualCost != null ? `${entry.actualCost.toFixed(3)}%` : '—'}
              </td>
            </motion.tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export function ExecutionCostPanel() {
  // Form state
  const [symbol, setSymbol] = useState('BTC');
  const [side, setSide] = useState<OrderSide>('BUY');
  const [sizeUsd, setSizeUsd] = useState(10000);
  const [urgency, setUrgency] = useState<Urgency>('MEDIUM');

  // Store
  const executionCost = useCryptoStore((s) => s.executionCost);
  const setExecutionCost = useCryptoStore((s) => s.setExecutionCost);
  const executionLog = useCryptoStore((s) => s.executionLog);
  const addExecutionLog = useCryptoStore((s) => s.addExecutionLog);
  const tokens = useCryptoStore((s) => s.tokens);

  // Estimate cost mutation
  const mutation = useMutation({
    mutationFn: async (req: CostEstimateRequest) => {
      // Find token data from store for richer API params
      const tokenData = tokens.find(
        (t) => t.symbol.toUpperCase() === req.symbol.toUpperCase()
      );

      const tokenAddress = tokenData?.address || tokenData?.id || req.symbol;
      const chain = tokenData?.chain || 'ETH';
      const currentPrice = tokenData?.priceUsd || 50000;
      const liquidity = tokenData?.liquidity || 100000;
      const volume24h = tokenData?.volume24h || 500000;

      const res = await fetch('/api/execution/cost', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tokenAddress,
          chain,
          positionSizeUsd: req.sizeUsd,
          direction: req.side === 'BUY' ? 'BUY' : 'SELL',
          currentPrice,
          liquidity,
          volume24h,
        }),
      });

      const json = await res.json();
      if (!res.ok || json.error) {
        throw new Error(json.error || 'Cost estimation failed');
      }
      return json.data as BackendCostEstimate;
    },
    onSuccess: (backendResult, req) => {
      // Map backend response to our frontend-friendly ExecutionCostResult
      const estimatedTimeSec = urgencyToEstimatedTime(
        req.urgency,
        12 // base ~12s for a typical ETH swap
      );

      const recommendation = getRecommendationOrderType(
        backendResult.totalCostPct,
        req.urgency
      );

      const result: ExecutionCostResult = {
        totalCostPct: backendResult.totalCostPct,
        slippagePct: backendResult.slippagePct,
        marketImpactPct: backendResult.marketImpactPct,
        feePct: backendResult.dexFeePct,
        networkFeePct: backendResult.gasFeePct,
        estimatedTimeSec,
        recommendation,
      };

      setExecutionCost(result);

      // Add to execution log
      const logEntry: ExecutionLogEntry = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        time: new Date().toISOString(),
        token: req.symbol.toUpperCase(),
        side: req.side,
        size: req.sizeUsd,
        estimatedCost: backendResult.totalCostPct,
        actualCost: null,
      };
      addExecutionLog(logEntry);

      toast.success(`Cost estimated: ${backendResult.totalCostPct.toFixed(3)}%`);
    },
    onError: (error: Error) => {
      toast.error(`Estimation failed: ${error.message}`);
    },
  });

  // Derive trajectory data from result
  const trajectory = useMemo(() => {
    if (!executionCost) return [];
    return generateAlmgrenChrissTrajectory(
      executionCost.marketImpactPct,
      executionCost.estimatedTimeSec,
      side
    );
  }, [executionCost, side]);

  const handleEstimate = useCallback(() => {
    if (!symbol.trim()) {
      toast.error('Please enter a token symbol');
      return;
    }
    if (sizeUsd <= 0) {
      toast.error('Order size must be positive');
      return;
    }
    mutation.mutate({ symbol, side, sizeUsd, urgency });
  }, [symbol, side, sizeUsd, urgency, mutation]);

  // ==================== RENDER ====================
  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-cyan-500/10 border border-cyan-500/20">
            <DollarSign className="h-3.5 w-3.5 text-cyan-400" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">
              Execution Cost Engine
            </span>
            <span className="text-[9px] font-mono text-[#475569] ml-2">
              Almgren-Chriss Model
            </span>
          </div>
        </div>
      </div>

      {/* ===== SCROLLABLE CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* ---- COST ESTIMATOR FORM ---- */}
        <div className="px-3 py-3 bg-[#0d1117]/50 border-b border-[#1e293b]">
          <div className="flex items-center gap-1.5 mb-3">
            <Zap className="h-3 w-3 text-cyan-400" />
            <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Cost Estimator
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {/* Token Symbol */}
            <div>
              <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                Token Symbol
              </label>
              <Input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="BTC"
                className="h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0] uppercase"
              />
            </div>

            {/* Order Side */}
            <div>
              <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                Side
              </label>
              <div className="flex gap-1">
                <button
                  onClick={() => setSide('BUY')}
                  className={`flex-1 h-7 rounded text-[10px] font-mono font-bold transition-all border ${
                    side === 'BUY'
                      ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-400'
                      : 'bg-[#0d1117] border-[#1e293b] text-[#64748b] hover:border-emerald-500/20 hover:text-emerald-400/60'
                  }`}
                >
                  BUY
                </button>
                <button
                  onClick={() => setSide('SELL')}
                  className={`flex-1 h-7 rounded text-[10px] font-mono font-bold transition-all border ${
                    side === 'SELL'
                      ? 'bg-red-500/15 border-red-500/30 text-red-400'
                      : 'bg-[#0d1117] border-[#1e293b] text-[#64748b] hover:border-red-500/20 hover:text-red-400/60'
                  }`}
                >
                  SELL
                </button>
              </div>
            </div>

            {/* Order Size */}
            <div>
              <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                Size (USD)
              </label>
              <Input
                type="number"
                value={sizeUsd}
                onChange={(e) => setSizeUsd(Math.max(1, Number(e.target.value) || 0))}
                placeholder="10000"
                min={1}
                className="h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]"
              />
            </div>

            {/* Urgency */}
            <div>
              <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">
                Urgency
              </label>
              <Select value={urgency} onValueChange={(v) => setUrgency(v as Urgency)}>
                <SelectTrigger className="w-full h-7 text-[10px] font-mono bg-[#0d1117] border-[#1e293b] text-[#e2e8f0]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-[#111827] border-[#1e293b]">
                  <SelectItem value="LOW" className="text-[10px] font-mono text-emerald-400 focus:bg-[#1e293b] focus:text-emerald-300">
                    LOW — {urgencyToTimeHorizon('LOW')}
                  </SelectItem>
                  <SelectItem value="MEDIUM" className="text-[10px] font-mono text-amber-400 focus:bg-[#1e293b] focus:text-amber-300">
                    MEDIUM — {urgencyToTimeHorizon('MEDIUM')}
                  </SelectItem>
                  <SelectItem value="HIGH" className="text-[10px] font-mono text-red-400 focus:bg-[#1e293b] focus:text-red-300">
                    HIGH — {urgencyToTimeHorizon('HIGH')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Estimate Button */}
            <div className="flex items-end">
              <Button
                onClick={handleEstimate}
                disabled={mutation.isPending || !symbol.trim()}
                className="w-full h-7 text-[10px] font-mono font-bold bg-cyan-500 hover:bg-cyan-600 text-black px-3 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {mutation.isPending ? (
                  <>
                    <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                    Estimating...
                  </>
                ) : (
                  <>
                    <DollarSign className="h-3 w-3 mr-1.5" />
                    Estimate Cost
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>

        {/* ---- RESULTS AREA ---- */}
        <AnimatePresence mode="wait">
          {executionCost ? (
            <motion.div
              key="results"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
            >
              {/* ---- TOTAL COST + KEY METRICS ---- */}
              <div className="px-3 py-3 border-b border-[#1e293b]">
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                  {/* Total Cost */}
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.05 }}
                  >
                    <Card className={`border p-3 ${costBg(executionCost.totalCostPct)}`}>
                      <div className="flex items-center gap-1.5 mb-1">
                        <DollarSign className="h-3 w-3 text-cyan-400" />
                        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                          Total Cost
                        </span>
                      </div>
                      <div className={`text-[24px] font-mono font-bold ${costColor(executionCost.totalCostPct)}`}>
                        {executionCost.totalCostPct.toFixed(3)}%
                      </div>
                      <div className="mt-1">
                        <div className="h-1.5 bg-[#1e293b] rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all duration-700"
                            style={{
                              width: `${Math.min(executionCost.totalCostPct * 20, 100)}%`,
                              backgroundColor:
                                executionCost.totalCostPct < 0.5
                                  ? '#10b981'
                                  : executionCost.totalCostPct <= 1.0
                                    ? '#f59e0b'
                                    : '#ef4444',
                            }}
                          />
                        </div>
                      </div>
                      <div className="mt-1 text-[8px] font-mono text-[#475569]">
                        {formatUsd(sizeUsd * (executionCost.totalCostPct / 100))} on {formatUsd(sizeUsd)}
                      </div>
                    </Card>
                  </motion.div>

                  {/* Estimated Time */}
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                  >
                    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                      <div className="flex items-center gap-1.5 mb-1">
                        <Clock className="h-3 w-3 text-amber-400" />
                        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                          Est. Exec Time
                        </span>
                      </div>
                      <div className="text-[24px] font-mono font-bold text-[#e2e8f0]">
                        {executionCost.estimatedTimeSec < 60
                          ? `${executionCost.estimatedTimeSec.toFixed(0)}s`
                          : `${(executionCost.estimatedTimeSec / 60).toFixed(1)}m`}
                      </div>
                      <div className="mt-1">
                        <Badge className="text-[8px] h-4 px-1.5 font-mono bg-amber-500/10 text-amber-400 border-amber-500/20">
                          {urgency} urgency
                        </Badge>
                      </div>
                    </Card>
                  </motion.div>

                  {/* Recommendation */}
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.15 }}
                  >
                    <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                      <div className="flex items-center gap-1.5 mb-1">
                        <Zap className="h-3 w-3 text-cyan-400" />
                        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                          Order Type
                        </span>
                      </div>
                      <div className="text-[20px] font-mono font-bold text-cyan-400">
                        {executionCost.recommendation.orderType}
                      </div>
                      <div className="mt-1 flex items-center gap-1.5">
                        <Clock className="h-2.5 w-2.5 text-[#64748b]" />
                        <span className="text-[8px] font-mono text-[#94a3b8]">
                          {executionCost.recommendation.timeHorizon}
                        </span>
                        {executionCost.recommendation.splitCount && (
                          <>
                            <Separator orientation="vertical" className="h-3 bg-[#1e293b]" />
                            <span className="text-[8px] font-mono text-cyan-400">
                              {executionCost.recommendation.splitCount} splits
                            </span>
                          </>
                        )}
                      </div>
                    </Card>
                  </motion.div>

                  {/* Warning / Cost Threshold */}
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.2 }}
                  >
                    <Card
                      className={`border p-3 ${
                        executionCost.totalCostPct > 1.5
                          ? 'bg-red-500/5 border-red-500/20'
                          : executionCost.totalCostPct > 0.5
                            ? 'bg-amber-500/5 border-amber-500/20'
                            : 'bg-[#0d1117] border-[#1e293b]'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <AlertTriangle
                          className={`h-3 w-3 ${
                            executionCost.totalCostPct > 1.5
                              ? 'text-red-400'
                              : executionCost.totalCostPct > 0.5
                                ? 'text-amber-400'
                                : 'text-emerald-400'
                          }`}
                        />
                        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">
                          Risk Assessment
                        </span>
                      </div>
                      <div
                        className={`text-[14px] font-mono font-bold ${
                          executionCost.totalCostPct > 1.5
                            ? 'text-red-400'
                            : executionCost.totalCostPct > 0.5
                              ? 'text-amber-400'
                              : 'text-emerald-400'
                        }`}
                      >
                        {executionCost.totalCostPct > 1.5
                          ? 'HIGH COST'
                          : executionCost.totalCostPct > 0.5
                            ? 'MODERATE'
                            : 'LOW COST'}
                      </div>
                      <div className="mt-1 text-[8px] font-mono text-[#94a3b8]">
                        {executionCost.totalCostPct > 1.5
                          ? 'Cost exceeds 1.5% — reduce size or use TWAP/VWAP'
                          : executionCost.totalCostPct > 0.5
                            ? 'Cost acceptable but monitor slippage'
                            : 'Execution cost within safe threshold'}
                      </div>
                    </Card>
                  </motion.div>
                </div>
              </div>

              {/* ---- COST BREAKDOWN CHART + TRAJECTORY ---- */}
              <div className="px-3 py-3 border-b border-[#1e293b]">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {/* Stacked Breakdown Bar Chart */}
                  <Card className="bg-[#0d1117] border-[#1e293b] p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <BarChart3 className="h-3.5 w-3.5 text-cyan-400" />
                      <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                        Cost Breakdown
                      </span>
                    </div>
                    <CostBreakdownChart result={executionCost} />
                    {/* Legend */}
                    <div className="flex flex-wrap items-center gap-3 mt-2">
                      {[
                        { label: 'Slippage', color: '#3b82f6' },
                        { label: 'Market Impact', color: '#8b5cf6' },
                        { label: 'Exchange Fee', color: '#f59e0b' },
                        { label: 'Network Fee', color: '#06b6d4' },
                      ].map((item) => (
                        <div key={item.label} className="flex items-center gap-1">
                          <div
                            className="w-2 h-2 rounded-sm"
                            style={{ backgroundColor: item.color }}
                          />
                          <span className="text-[8px] font-mono text-[#64748b]">
                            {item.label}
                          </span>
                        </div>
                      ))}
                    </div>
                  </Card>

                  {/* Almgren-Chriss Trajectory */}
                  <Card className="bg-[#0d1117] border-[#1e293b] p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <TrendingUp className="h-3.5 w-3.5 text-cyan-400" />
                      <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                        Almgren-Chriss Optimal Trajectory
                      </span>
                    </div>
                    {trajectory.length > 0 && (
                      <TrajectoryChart trajectory={trajectory} side={side} />
                    )}
                    <div className="flex items-center gap-2 mt-2">
                      <div className="flex items-center gap-1">
                        <div
                          className="w-2 h-2 rounded-sm"
                          style={{
                            backgroundColor: side === 'BUY' ? '#ef4444' : '#10b981',
                          }}
                        />
                        <span className="text-[8px] font-mono text-[#64748b]">
                          {side === 'BUY' ? 'Buy impact' : 'Sell impact'}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        <div className="w-2 h-2 rounded-sm bg-[#475569]" />
                        <span className="text-[8px] font-mono text-[#64748b]">
                          Starting price
                        </span>
                      </div>
                      <span className="text-[8px] font-mono text-[#475569] ml-auto">
                        η=0.142 γ=0.314
                      </span>
                    </div>
                  </Card>
                </div>
              </div>

              {/* ---- EXECUTION RECOMMENDATIONS DETAIL ---- */}
              <div className="px-3 py-3 border-b border-[#1e293b]">
                <div className="flex items-center gap-1.5 mb-3">
                  <Info className="h-3 w-3 text-cyan-400" />
                  <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
                    Execution Recommendations
                  </span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {/* Recommended Order Type */}
                  <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                    <div className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider mb-1">
                      Recommended Order Type
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge
                        className="text-[10px] h-5 px-2 font-mono font-bold"
                        style={{
                          backgroundColor: '#06b6d420',
                          color: '#06b6d4',
                          borderColor: '#06b6d440',
                        }}
                      >
                        {executionCost.recommendation.orderType}
                      </Badge>
                      <ChevronRight className="h-3 w-3 text-[#475569]" />
                    </div>
                    <div className="mt-2 text-[8px] font-mono text-[#94a3b8] leading-relaxed">
                      {executionCost.recommendation.orderType === 'Market' &&
                        'Execute immediately for fastest fill. Best when costs are low.'}
                      {executionCost.recommendation.orderType === 'Limit' &&
                        'Place a limit order to control execution price. Patience required.'}
                      {executionCost.recommendation.orderType === 'TWAP' &&
                        `Split into ${executionCost.recommendation.splitCount || 3} equal orders over ${executionCost.recommendation.timeHorizon} to minimize market impact.`}
                      {executionCost.recommendation.orderType === 'VWAP' &&
                        `Volume-weighted execution across ${executionCost.recommendation.splitCount || 5} orders over ${executionCost.recommendation.timeHorizon} for optimal fill.`}
                    </div>
                  </Card>

                  {/* Suggested Time Horizon */}
                  <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                    <div className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider mb-1">
                      Suggested Time Horizon
                    </div>
                    <div className="text-[16px] font-mono font-bold text-[#e2e8f0]">
                      {executionCost.recommendation.timeHorizon}
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                      <Clock className="h-2.5 w-2.5 text-[#64748b]" />
                      <span className="text-[8px] font-mono text-[#94a3b8]">
                        Est. completion: {executionCost.estimatedTimeSec < 60
                          ? `${executionCost.estimatedTimeSec.toFixed(0)}s`
                          : `${(executionCost.estimatedTimeSec / 60).toFixed(1)}m`}
                      </span>
                    </div>
                    <div className="mt-2">
                      <div className="h-1 bg-[#1e293b] rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full bg-cyan-500"
                          style={{
                            width: `${Math.min(
                              (executionCost.estimatedTimeSec / 1800) * 100,
                              100
                            )}%`,
                          }}
                        />
                      </div>
                    </div>
                  </Card>

                  {/* Optimal Split */}
                  <Card className="bg-[#0d1117] border-[#1e293b] p-3">
                    <div className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider mb-1">
                      Optimal Split
                    </div>
                    {executionCost.recommendation.splitCount ? (
                      <>
                        <div className="text-[16px] font-mono font-bold text-[#e2e8f0]">
                          {executionCost.recommendation.splitCount} orders
                        </div>
                        <div className="mt-1 text-[8px] font-mono text-[#94a3b8]">
                          Each ~{formatUsd(sizeUsd / (executionCost.recommendation.splitCount || 1))} over{' '}
                          {executionCost.recommendation.timeHorizon}
                        </div>
                        <div className="mt-2 flex gap-0.5">
                          {Array.from({
                            length: executionCost.recommendation.splitCount,
                          }).map((_, i) => (
                            <div
                              key={i}
                              className="flex-1 h-3 bg-cyan-500/30 rounded-sm border border-cyan-500/20"
                              style={{
                                opacity: 0.5 + (i / (executionCost.recommendation.splitCount || 1)) * 0.5,
                              }}
                            />
                          ))}
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="text-[16px] font-mono font-bold text-[#e2e8f0]">
                          Single order
                        </div>
                        <div className="mt-1 text-[8px] font-mono text-[#94a3b8]">
                          No split needed — {executionCost.recommendation.orderType} order
                        </div>
                        <div className="mt-2 flex gap-0.5">
                          <div className="flex-1 h-3 bg-cyan-500/30 rounded-sm border border-cyan-500/20" />
                        </div>
                      </>
                    )}
                  </Card>
                </div>

                {/* Warning banner if cost exceeds threshold */}
                {executionCost.totalCostPct > 1.0 && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    className="mt-3"
                  >
                    <div
                      className={`flex items-start gap-2 p-3 rounded-lg border ${
                        executionCost.totalCostPct > 1.5
                          ? 'bg-red-500/10 border-red-500/20'
                          : 'bg-amber-500/10 border-amber-500/20'
                      }`}
                    >
                      <AlertTriangle
                        className={`h-4 w-4 mt-0.5 shrink-0 ${
                          executionCost.totalCostPct > 1.5
                            ? 'text-red-400'
                            : 'text-amber-400'
                        }`}
                      />
                      <div>
                        <p
                          className={`text-[10px] font-mono font-bold ${
                            executionCost.totalCostPct > 1.5
                              ? 'text-red-400'
                              : 'text-amber-400'
                          }`}
                        >
                          {executionCost.totalCostPct > 1.5
                            ? 'HIGH EXECUTION COST WARNING'
                            : 'COST THRESHOLD ALERT'}
                        </p>
                        <p className="text-[9px] font-mono text-[#94a3b8] mt-0.5 leading-relaxed">
                          {executionCost.totalCostPct > 1.5
                            ? `Total cost of ${executionCost.totalCostPct.toFixed(3)}% exceeds the 1.5% safety threshold. Consider reducing position size or using a ${executionCost.recommendation.orderType} strategy with ${executionCost.recommendation.splitCount || 'multiple'} orders over ${executionCost.recommendation.timeHorizon}.`
                            : `Total cost of ${executionCost.totalCostPct.toFixed(3)}% is above the 1.0% caution threshold. A ${executionCost.recommendation.orderType} order strategy is recommended to minimize market impact.`}
                        </p>
                      </div>
                    </div>
                  </motion.div>
                )}
              </div>
            </motion.div>
          ) : (
            /* ---- EMPTY STATE ---- */
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="px-3 py-8"
            >
              <div className="flex flex-col items-center justify-center">
                <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-cyan-500/10 border border-cyan-500/20 mb-4">
                  <DollarSign className="h-6 w-6 text-cyan-400" />
                </div>
                <h3 className="text-sm font-mono font-bold text-[#f1f5f9] mb-2">
                  Execution Cost Estimator
                </h3>
                <p className="text-[11px] font-mono text-[#94a3b8] text-center max-w-md leading-relaxed">
                  Estimate the true cost of executing a trade before it happens.
                  Includes slippage, market impact (Almgren-Chriss), exchange fees,
                  and network costs. Get optimal order type and time horizon recommendations.
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ---- RECENT EXECUTIONS LOG ---- */}
        <div className="px-3 py-3">
          <div className="flex items-center gap-1.5 mb-3">
            <Activity className="h-3 w-3 text-amber-400" />
            <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Recent Executions
            </span>
            <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1e293b] text-[#64748b] border-[#1e293b] ml-1">
              Last 5
            </Badge>
          </div>
          <Card className="bg-[#0d1117] border-[#1e293b] p-3">
            <ExecutionLogTable entries={executionLog} />
          </Card>
        </div>
      </div>
    </div>
  );
}

export default ExecutionCostPanel;
