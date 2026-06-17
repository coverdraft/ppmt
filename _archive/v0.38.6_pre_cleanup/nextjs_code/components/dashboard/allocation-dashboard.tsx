'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Skeleton } from '@/components/ui/skeleton';

// ============================================================
// TYPES
// ============================================================

interface DashboardPortfolio {
  totalCapital: number;
  peakCapital: number;
  currentDD: number;
  allocatedPct: number;
  availablePct: number;
  activeStrategies: number;
}

interface StrategyAllocation {
  strategyId: string;
  strategyName: string;
  state: string;
  action: string;
  method: string;
  sizeUsd: number;
  targetPct: number;
  category: string;
}

interface CorrelationSummary {
  strategies: string[];
  matrix: number[][];
  avgCorrelation: number;
  dataPoints: number;
  computedAt: string;
}

interface KillSwitchStatus {
  globalPause: boolean;
  globalPauseReason?: string;
  portfolioDDTriggered: boolean;
  strategyDDTriggered: string[];
}

interface RiskBudget {
  maxPortfolioDrawdownPct: number;
  maxStrategyDrawdownPct: number;
  maxPositionLossPct: number;
  maxConcentrationPct: number;
  maxSectorPct: number;
  maxChainPct: number;
  maxCorrelatedPct: number;
  riskProfile: string;
}

interface DashboardData {
  portfolio: DashboardPortfolio;
  strategyAllocations: StrategyAllocation[];
  correlationSummary: CorrelationSummary;
  killSwitchStatus: KillSwitchStatus;
  riskBudget: RiskBudget;
  methodDistribution: Record<string, number>;
  timestamp: string;
}

// ============================================================
// HELPERS
// ============================================================

function formatUsd(value: number): string {
  if (value >= 1000) return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (value >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(4)}`;
}

function formatPct(value: number): string {
  return `${value.toFixed(1)}%`;
}

function getStateColor(state: string): string {
  switch (state) {
    case 'ACTIVE': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
    case 'CONDITIONAL': return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
    case 'PAUSED': return 'bg-orange-500/20 text-orange-400 border-orange-500/30';
    case 'REJECTED': return 'bg-red-500/20 text-red-400 border-red-500/30';
    default: return 'bg-gray-500/20 text-gray-400 border-gray-500/30';
  }
}

function getActionColor(action: string): string {
  switch (action) {
    case 'INCREASE': return 'bg-emerald-500/20 text-emerald-400';
    case 'MAINTAIN': return 'bg-blue-500/20 text-blue-400';
    case 'REDUCE': return 'bg-yellow-500/20 text-yellow-400';
    case 'EXIT': return 'bg-red-500/20 text-red-400';
    default: return 'bg-gray-500/20 text-gray-400';
  }
}

function getCorrelationColor(value: number): string {
  if (value >= 0.7) return 'bg-red-500/60';
  if (value >= 0.4) return 'bg-yellow-500/60';
  if (value >= 0) return 'bg-emerald-500/60';
  return 'bg-blue-500/60';
}

function getCorrelationTextColor(value: number): string {
  if (value >= 0.7) return 'text-red-100';
  if (value >= 0.4) return 'text-yellow-100';
  return 'text-emerald-100';
}

function getMethodLabel(method: string): string {
  const labels: Record<string, string> = {
    KELLY_MODIFIED: 'Kelly Mod',
    RISK_PARITY: 'Risk Parity',
    VOLATILITY_TARGETING: 'Vol Target',
    MAX_DRAWDOWN_CONTROL: 'DD Control',
    EQUAL_WEIGHT: 'Equal Wt',
    MEAN_VARIANCE: 'Mean-Var',
    MIN_VARIANCE: 'Min Var',
  };
  return labels[method] || method;
}

// ============================================================
// COMPONENT
// ============================================================

export default function AllocationDashboard() {
  const [data, setData] = useState<DashboardData | null>(null);

  // Fetch dashboard data via useQuery
  const { isLoading: loading, error: queryError, refetch: fetchDashboard } = useQuery({
    queryKey: ['capital-allocation-dashboard'],
    queryFn: async () => {
      const res = await fetch('/api/capital-allocation/dashboard');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (json.data) {
        setData(json.data as DashboardData);
        return json.data as DashboardData;
      }
      throw new Error(json.error || 'No data returned');
    },
    refetchInterval: 10000,
    staleTime: 5000,
  });

  const error = queryError ? (queryError instanceof Error ? queryError.message : 'Failed to fetch') : null;

  if (loading) {
    return (
      <div className="p-4 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map(i => (
            <Skeleton key={i} className="h-28 w-full bg-[#1a1f2e]" />
          ))}
        </div>
        <Skeleton className="h-64 w-full bg-[#1a1f2e]" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-4 text-center text-muted-foreground">
        <p className="text-red-400 mb-2">Failed to load allocation dashboard</p>
        <p className="text-xs">{error}</p>
        <button
          onClick={fetchDashboard}
          className="mt-2 px-3 py-1 text-xs bg-[#1a1f2e] rounded hover:bg-[#2a3040] text-[#d4af37]"
        >
          Retry
        </button>
      </div>
    );
  }

  const { portfolio, strategyAllocations, correlationSummary, killSwitchStatus, riskBudget, methodDistribution } = data;

  return (
    <TooltipProvider>
      <div className="p-4 space-y-4 max-h-full overflow-y-auto">
        {/* ---- PORTFOLIO OVERVIEW CARDS ---- */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card className="bg-[#0d1117] border-[#1e293b]">
            <CardHeader className="pb-2 pt-3 px-4">
              <CardTitle className="text-xs font-mono text-muted-foreground">Total Capital</CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className="text-xl font-bold text-[#d4af37] font-mono">
                {formatUsd(portfolio.totalCapital)}
              </div>
              <div className="text-[10px] text-muted-foreground mt-1">
                Peak: {formatUsd(portfolio.peakCapital)}
              </div>
            </CardContent>
          </Card>

          <Card className="bg-[#0d1117] border-[#1e293b]">
            <CardHeader className="pb-2 pt-3 px-4">
              <CardTitle className="text-xs font-mono text-muted-foreground">Allocated</CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className="text-xl font-bold text-emerald-400 font-mono">
                {formatPct(portfolio.allocatedPct)}
              </div>
              <Progress value={portfolio.allocatedPct} className="mt-2 h-1.5 bg-[#1e293b]" />
            </CardContent>
          </Card>

          <Card className="bg-[#0d1117] border-[#1e293b]">
            <CardHeader className="pb-2 pt-3 px-4">
              <CardTitle className="text-xs font-mono text-muted-foreground">Available</CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className="text-xl font-bold text-blue-400 font-mono">
                {formatPct(portfolio.availablePct)}
              </div>
              <div className="text-[10px] text-muted-foreground mt-1">
                {formatUsd(portfolio.totalCapital * portfolio.availablePct / 100)}
              </div>
            </CardContent>
          </Card>

          <Card className="bg-[#0d1117] border-[#1e293b]">
            <CardHeader className="pb-2 pt-3 px-4">
              <CardTitle className="text-xs font-mono text-muted-foreground">Drawdown</CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <div className={`text-xl font-bold font-mono ${portfolio.currentDD > 10 ? 'text-red-400' : portfolio.currentDD > 5 ? 'text-yellow-400' : 'text-emerald-400'}`}>
                {formatPct(portfolio.currentDD)}
              </div>
              <div className="text-[10px] text-muted-foreground mt-1">
                Limit: {formatPct(riskBudget.maxPortfolioDrawdownPct)}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* ---- STRATEGY ALLOCATIONS TABLE ---- */}
        <Card className="bg-[#0d1117] border-[#1e293b]">
          <CardHeader className="pb-2 pt-3 px-4">
            <div className="flex items-center justify-between">
              <CardTitle className="text-xs font-mono text-muted-foreground">
                Strategy Allocations ({strategyAllocations.length} active)
              </CardTitle>
              <Badge variant="outline" className="text-[10px] font-mono border-[#d4af37]/30 text-[#d4af37]">
                {portfolio.activeStrategies} strategies
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            {strategyAllocations.length === 0 ? (
              <div className="text-center text-muted-foreground text-xs py-8">
                No active strategies. Activate strategies from the AI Manager to see allocations.
              </div>
            ) : (
              <div className="max-h-64 overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="border-[#1e293b] hover:bg-transparent">
                      <TableHead className="text-[10px] font-mono text-muted-foreground h-8">Strategy</TableHead>
                      <TableHead className="text-[10px] font-mono text-muted-foreground h-8">State</TableHead>
                      <TableHead className="text-[10px] font-mono text-muted-foreground h-8">Action</TableHead>
                      <TableHead className="text-[10px] font-mono text-muted-foreground h-8">Method</TableHead>
                      <TableHead className="text-[10px] font-mono text-muted-foreground h-8 text-right">Size</TableHead>
                      <TableHead className="text-[10px] font-mono text-muted-foreground h-8 text-right">Target %</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {strategyAllocations.map((alloc) => (
                      <TableRow key={alloc.strategyId} className="border-[#1e293b] hover:bg-[#1a1f2e]/50">
                        <TableCell className="text-xs font-mono py-1.5">
                          <div className="truncate max-w-[140px]" title={alloc.strategyName}>
                            {alloc.strategyName}
                          </div>
                        </TableCell>
                        <TableCell className="py-1.5">
                          <Badge variant="outline" className={`text-[9px] font-mono px-1.5 py-0 ${getStateColor(alloc.state)}`}>
                            {alloc.state}
                          </Badge>
                        </TableCell>
                        <TableCell className="py-1.5">
                          <Badge variant="outline" className={`text-[9px] font-mono px-1.5 py-0 ${getActionColor(alloc.action)}`}>
                            {alloc.action}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-[10px] font-mono text-muted-foreground py-1.5">
                          {getMethodLabel(alloc.method)}
                        </TableCell>
                        <TableCell className="text-[10px] font-mono text-right py-1.5">
                          {formatUsd(alloc.sizeUsd)}
                        </TableCell>
                        <TableCell className="text-[10px] font-mono text-right py-1.5">
                          {formatPct(alloc.targetPct)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ---- CORRELATION HEATMAP + METHOD DISTRIBUTION ---- */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Correlation Heatmap */}
          <Card className="bg-[#0d1117] border-[#1e293b]">
            <CardHeader className="pb-2 pt-3 px-4">
              <div className="flex items-center justify-between">
                <CardTitle className="text-xs font-mono text-muted-foreground">
                  Correlation Heatmap
                </CardTitle>
                <div className="flex items-center gap-1.5">
                  <div className="flex items-center gap-1 text-[9px] text-muted-foreground">
                    <div className="w-2.5 h-2.5 rounded-sm bg-emerald-500/60" /> Low
                  </div>
                  <div className="flex items-center gap-1 text-[9px] text-muted-foreground">
                    <div className="w-2.5 h-2.5 rounded-sm bg-yellow-500/60" /> Med
                  </div>
                  <div className="flex items-center gap-1 text-[9px] text-muted-foreground">
                    <div className="w-2.5 h-2.5 rounded-sm bg-red-500/60" /> High
                  </div>
                </div>
              </div>
              <div className="text-[10px] text-muted-foreground mt-1">
                Avg: {formatPct(correlationSummary.avgCorrelation * 100)} | {correlationSummary.dataPoints} data points
              </div>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              {correlationSummary.strategies.length < 2 ? (
                <div className="text-center text-muted-foreground text-xs py-6">
                  Insufficient strategy return data for correlation analysis.
                  <br />
                  <span className="text-[10px]">Need 2+ strategies with 5+ daily returns each.</span>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr>
                        <th className="text-[9px] font-mono text-muted-foreground p-1"></th>
                        {correlationSummary.strategies.map((s, i) => (
                          <th key={i} className="text-[9px] font-mono text-muted-foreground p-1">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="truncate max-w-[40px] block cursor-default">
                                  {s.slice(0, 6)}
                                </span>
                              </TooltipTrigger>
                              <TooltipContent side="top" className="text-xs">
                                {s}
                              </TooltipContent>
                            </Tooltip>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {correlationSummary.matrix.map((row, i) => (
                        <tr key={i}>
                          <td className="text-[9px] font-mono text-muted-foreground p-1 text-right">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="truncate max-w-[40px] block cursor-default">
                                  {correlationSummary.strategies[i].slice(0, 6)}
                                </span>
                              </TooltipTrigger>
                              <TooltipContent side="right" className="text-xs">
                                {correlationSummary.strategies[i]}
                              </TooltipContent>
                            </Tooltip>
                          </td>
                          {row.map((val, j) => (
                            <td key={j} className="p-0.5">
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <div
                                    className={`w-8 h-8 flex items-center justify-center text-[9px] font-mono rounded-sm cursor-default ${getCorrelationColor(val)} ${getCorrelationTextColor(val)}`}
                                  >
                                    {i === j ? '1.0' : val.toFixed(2)}
                                  </div>
                                </TooltipTrigger>
                                <TooltipContent side="top" className="text-xs">
                                  {correlationSummary.strategies[i]} ↔ {correlationSummary.strategies[j]}: {val.toFixed(4)}
                                </TooltipContent>
                              </Tooltip>
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Method Distribution + Kill Switch Status + Risk Budget */}
          <div className="space-y-4">
            {/* Method Distribution */}
            <Card className="bg-[#0d1117] border-[#1e293b]">
              <CardHeader className="pb-2 pt-3 px-4">
                <CardTitle className="text-xs font-mono text-muted-foreground">
                  Allocation Method Distribution
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4 pb-3">
                {Object.keys(methodDistribution).length === 0 ? (
                  <div className="text-center text-muted-foreground text-xs py-4">
                    No active allocation methods
                  </div>
                ) : (
                  <div className="space-y-2">
                    {Object.entries(methodDistribution).map(([method, count]) => {
                      const total = Object.values(methodDistribution).reduce((s, v) => s + v, 0);
                      const pct = total > 0 ? (count / total) * 100 : 0;
                      return (
                        <div key={method} className="flex items-center gap-2">
                          <div className="w-20 text-[10px] font-mono text-muted-foreground truncate">
                            {getMethodLabel(method)}
                          </div>
                          <div className="flex-1">
                            <Progress value={pct} className="h-2 bg-[#1e293b]" />
                          </div>
                          <div className="text-[10px] font-mono text-muted-foreground w-8 text-right">
                            {count}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Kill Switch Status */}
            <Card className="bg-[#0d1117] border-[#1e293b]">
              <CardHeader className="pb-2 pt-3 px-4">
                <CardTitle className="text-xs font-mono text-muted-foreground">
                  Kill Switch Status
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4 pb-3">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono text-muted-foreground">Global Pause</span>
                    <Badge variant="outline" className={`text-[9px] font-mono px-1.5 py-0 ${killSwitchStatus.globalPause ? 'bg-red-500/20 text-red-400 border-red-500/30' : 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'}`}>
                      {killSwitchStatus.globalPause ? 'ACTIVE' : 'Clear'}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono text-muted-foreground">Portfolio DD Kill</span>
                    <Badge variant="outline" className={`text-[9px] font-mono px-1.5 py-0 ${killSwitchStatus.portfolioDDTriggered ? 'bg-red-500/20 text-red-400 border-red-500/30' : 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'}`}>
                      {killSwitchStatus.portfolioDDTriggered ? 'TRIGGERED' : 'Clear'}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono text-muted-foreground">Strategy DD Kills</span>
                    <span className="text-[10px] font-mono text-muted-foreground">
                      {Array.isArray(killSwitchStatus.strategyDDTriggered) ? killSwitchStatus.strategyDDTriggered.length : 0}
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Risk Budget Summary */}
            <Card className="bg-[#0d1117] border-[#1e293b]">
              <CardHeader className="pb-2 pt-3 px-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xs font-mono text-muted-foreground">
                    Risk Budget
                  </CardTitle>
                  <Badge variant="outline" className="text-[9px] font-mono px-1.5 py-0 border-[#d4af37]/30 text-[#d4af37]">
                    {riskBudget.riskProfile}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="px-4 pb-3">
                <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                  <RiskBudgetItem
                    label="Max Portfolio DD"
                    limit={riskBudget.maxPortfolioDrawdownPct}
                    current={portfolio.currentDD}
                  />
                  <RiskBudgetItem
                    label="Max Concentration"
                    limit={riskBudget.maxConcentrationPct}
                    current={portfolio.allocatedPct}
                  />
                  <RiskBudgetItem
                    label="Max Strategy DD"
                    limit={riskBudget.maxStrategyDrawdownPct}
                    current={0}
                  />
                  <RiskBudgetItem
                    label="Max Correlated"
                    limit={riskBudget.maxCorrelatedPct}
                    current={Math.round(correlationSummary.avgCorrelation * 100 * 100) / 100}
                  />
                  <RiskBudgetItem
                    label="Max Chain Pct"
                    limit={riskBudget.maxChainPct}
                    current={0}
                  />
                  <RiskBudgetItem
                    label="Max Position Loss"
                    limit={riskBudget.maxPositionLossPct}
                    current={0}
                  />
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}

// ============================================================
// SUB-COMPONENT: Risk Budget Item
// ============================================================

function RiskBudgetItem({ label, limit, current }: { label: string; limit: number; current: number }) {
  const usagePct = limit > 0 ? Math.min((current / limit) * 100, 100) : 0;
  const isWarning = usagePct > 70;
  const isCritical = usagePct > 90;

  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-[9px] font-mono text-muted-foreground">{label}</span>
        <span className={`text-[9px] font-mono ${isCritical ? 'text-red-400' : isWarning ? 'text-yellow-400' : 'text-muted-foreground'}`}>
          {formatPct(current)} / {formatPct(limit)}
        </span>
      </div>
      <Progress
        value={usagePct}
        className={`h-1 mt-0.5 bg-[#1e293b] ${isCritical ? '[&>div]:bg-red-500' : isWarning ? '[&>div]:bg-yellow-500' : '[&>div]:bg-emerald-500'}`}
      />
    </div>
  );
}
