'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Shield,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Clock,
  RefreshCw,
  Loader2,
  BarChart3,
  Target,
  Zap,
  PieChart,
  Eye,
} from 'lucide-react';
import { toast } from 'sonner';

// ============================================================
// TYPES
// ============================================================

interface EquityCurvePoint {
  date: string;
  portfolioValue: number;
  benchmarkValue: number;
  events: Array<{ type: string; description: string }>;
}

interface EquityCurveData {
  curve: EquityCurvePoint[];
  initialCapital: number;
  currentCapital: number;
  totalReturnPct: number;
  totalTrades: number;
  openPositions: number;
}

interface LifecycleState {
  state: string;
  from: string;
  to: string;
  timestamp: string;
  reason: string;
  scores?: { robustness: number; overfitting: number; stability: number };
  capitalAction?: string;
}

interface StrategyLifecycle {
  strategyId: string;
  strategyName: string;
  states: LifecycleState[];
}

interface LifecycleData {
  lifecycles: StrategyLifecycle[];
  totalStrategies: number;
  stateDistribution: Record<string, number>;
}

interface PortfolioStats {
  sharpeRatio: number;
  sortinoRatio: number;
  maxDrawdownPct: number;
  winRate: number;
  totalPnlUsd: number;
  totalPnlPct: number;
  initialCapital: number;
  currentCapital: number;
  currentDrawdownPct: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  rollingCorrelationAvg: number;
  activeStrategies: number;
  stateDistribution: Record<string, number>;
  strategies: Array<{ id: string; name: string; state: string; lastEvaluated: string }>;
  riskBudget: Record<string, unknown>;
  riskBudgetUtilization: {
    drawdownUtilizationPct: number;
    currentDD: number;
    maxDD: number;
  };
  tokenConcentration: Record<string, number>;
  maxTokenConcentration: number;
  maxConcentrationLimit: number;
  killSwitch: Record<string, unknown>;
  openPositions: Array<{
    symbol: string;
    chain: string;
    sizeUsd: number;
    pnlUsd: number;
    pnlPct: number;
    strategy: string;
  }>;
  openPositionCount: number;
}

interface RiskCheck {
  name: string;
  present: boolean;
  location: string;
  description: string;
}

interface RiskVerification {
  allChecksPresent: boolean;
  missingChecks: string[];
  checks: RiskCheck[];
  summary: { total: number; passed: number; failed: number; coveragePct: number };
}

// ============================================================
// HELPERS
// ============================================================

function stateColor(state: string): string {
  switch (state) {
    case 'ACTIVE': return 'bg-emerald-500';
    case 'CONDITIONAL': return 'bg-yellow-500';
    case 'PAUSED': return 'bg-orange-500';
    case 'REJECTED': return 'bg-red-500';
    default: return 'bg-gray-500';
  }
}

function stateTextColor(state: string): string {
  switch (state) {
    case 'ACTIVE': return 'text-emerald-400';
    case 'CONDITIONAL': return 'text-yellow-400';
    case 'PAUSED': return 'text-orange-400';
    case 'REJECTED': return 'text-red-400';
    default: return 'text-gray-400';
  }
}

function stateBadgeBg(state: string): string {
  switch (state) {
    case 'ACTIVE': return 'bg-emerald-500/15 border-emerald-500/30';
    case 'CONDITIONAL': return 'bg-yellow-500/15 border-yellow-500/30';
    case 'PAUSED': return 'bg-orange-500/15 border-orange-500/30';
    case 'REJECTED': return 'bg-red-500/15 border-red-500/30';
    default: return 'bg-gray-500/15 border-gray-500/30';
  }
}

function formatCurrency(value: number): string {
  if (Math.abs(value) >= 1000) return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (Math.abs(value) >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(4)}`;
}

function formatPct(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function formatNumber(value: number, decimals = 2): string {
  return value.toFixed(decimals);
}

// ============================================================
// EQUITY CURVE CHART (SVG-based)
// ============================================================

function EquityCurveChart({ data }: { data: EquityCurvePoint[] }) {
  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center h-48 text-[#475569]">
        <div className="text-center">
          <BarChart3 className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <span className="text-[10px] font-mono">No equity curve data yet. Start paper trading to see the curve.</span>
        </div>
      </div>
    );
  }

  const width = 700;
  const height = 200;
  const padding = { top: 20, right: 20, bottom: 30, left: 60 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const values = data.map(d => d.portfolioValue);
  const benchmarks = data.map(d => d.benchmarkValue);
  const allValues = [...values, ...benchmarks];
  const minVal = Math.min(...allValues) * 0.95;
  const maxVal = Math.max(...allValues) * 1.05;
  const valRange = maxVal - minVal || 1;

  const scaleX = (i: number) => padding.left + (i / (data.length - 1)) * chartW;
  const scaleY = (v: number) => padding.top + chartH - ((v - minVal) / valRange) * chartH;

  // Portfolio line path
  const portfolioPath = data.map((d, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(i)} ${scaleY(d.portfolioValue)}`).join(' ');

  // Benchmark line path
  const benchmarkPath = data.map((d, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(i)} ${scaleY(d.benchmarkValue)}`).join(' ');

  // Area fill under portfolio line
  const areaPath = `${portfolioPath} L ${scaleX(data.length - 1)} ${scaleY(minVal)} L ${scaleX(0)} ${scaleY(minVal)} Z`;

  // Event dots (only for points with events)
  const eventPoints = data.filter(d => d.events.length > 0);

  // Y-axis labels
  const yTicks = 5;
  const yLabels = Array.from({ length: yTicks }, (_, i) => {
    const v = minVal + (valRange * i) / (yTicks - 1);
    return { value: v, y: scaleY(v) };
  });

  // X-axis labels (show first, middle, last)
  const xLabels = [
    { label: new Date(data[0].date).toLocaleDateString('en', { month: 'short', day: 'numeric' }), x: scaleX(0) },
    { label: new Date(data[Math.floor(data.length / 2)].date).toLocaleDateString('en', { month: 'short', day: 'numeric' }), x: scaleX(Math.floor(data.length / 2)) },
    { label: new Date(data[data.length - 1].date).toLocaleDateString('en', { month: 'short', day: 'numeric' }), x: scaleX(data.length - 1) },
  ];

  // Determine if portfolio is above or below benchmark
  const lastValue = values[values.length - 1];
  const lastBenchmark = benchmarks[benchmarks.length - 1];
  const lineColor = lastValue >= lastBenchmark ? '#10b981' : '#ef4444';
  const areaColor = lastValue >= lastBenchmark ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" style={{ maxHeight: '220px' }}>
      {/* Grid lines */}
      {yLabels.map((tick, i) => (
        <line key={i} x1={padding.left} y1={tick.y} x2={width - padding.right} y2={tick.y} stroke="#1e293b" strokeWidth="0.5" />
      ))}

      {/* Area fill */}
      <path d={areaPath} fill={areaColor} />

      {/* Benchmark line (dashed) */}
      <path d={benchmarkPath} fill="none" stroke="#475569" strokeWidth="1" strokeDasharray="4,4" />

      {/* Portfolio line */}
      <path d={portfolioPath} fill="none" stroke={lineColor} strokeWidth="2" strokeLinejoin="round" />

      {/* Event dots */}
      {eventPoints.map((d, i) => {
        const idx = data.indexOf(d);
        const x = scaleX(idx);
        const y = scaleY(d.portfolioValue);
        const eventType = d.events[0]?.type;
        const dotColor = eventType === 'KILL_SWITCH' ? '#ef4444'
          : eventType === 'SDE_DECISION' ? '#d4af37'
          : eventType === 'STOP_LOSS' ? '#f97316'
          : eventType === 'LARGE_TRADE' ? '#3b82f6'
          : '#94a3b8';

        return (
          <g key={i}>
            <circle cx={x} cy={y} r="3" fill={dotColor} stroke="#0a0e17" strokeWidth="1" />
            <title>{d.events.map(e => `[${e.type}] ${e.description}`).join('\n')}</title>
          </g>
        );
      })}

      {/* Y-axis labels */}
      {yLabels.map((tick, i) => (
        <text key={i} x={padding.left - 8} y={tick.y + 3} textAnchor="end" fill="#64748b" fontSize="8" fontFamily="monospace">
          {formatCurrency(tick.value)}
        </text>
      ))}

      {/* X-axis labels */}
      {xLabels.map((label, i) => (
        <text key={i} x={label.x} y={height - 5} textAnchor="middle" fill="#64748b" fontSize="8" fontFamily="monospace">
          {label.label}
        </text>
      ))}

      {/* Legend */}
      <line x1={padding.left + 10} y1={padding.top - 8} x2={padding.left + 30} y2={padding.top - 8} stroke={lineColor} strokeWidth="2" />
      <text x={padding.left + 34} y={padding.top - 5} fill="#94a3b8" fontSize="7" fontFamily="monospace">Portfolio</text>
      <line x1={padding.left + 100} y1={padding.top - 8} x2={padding.left + 120} y2={padding.top - 8} stroke="#475569" strokeWidth="1" strokeDasharray="4,4" />
      <text x={padding.left + 124} y={padding.top - 5} fill="#94a3b8" fontSize="7" fontFamily="monospace">Benchmark (Initial)</text>
    </svg>
  );
}

// ============================================================
// STRATEGY LIFECYCLE LANE VIEW
// ============================================================

function StrategyLifecycleLanes({ lifecycles }: { lifecycles: StrategyLifecycle[] }) {
  if (lifecycles.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-[#475569]">
        <span className="text-[10px] font-mono">No strategy lifecycle data. Run SDE evaluations to see lifecycle history.</span>
      </div>
    );
  }

  return (
    <TooltipProvider>
      <div className="space-y-1.5 max-h-64 overflow-y-auto custom-scrollbar">
        {lifecycles.map((lifecycle) => {
          const currentState = lifecycle.states.length > 0
            ? lifecycle.states[lifecycle.states.length - 1].state
            : 'UNKNOWN';

          return (
            <div key={lifecycle.strategyId} className="flex items-center gap-2">
              {/* Strategy name */}
              <div className="w-28 shrink-0 truncate">
                <span className="text-[9px] font-mono text-[#e2e8f0] truncate block" title={lifecycle.strategyName}>
                  {lifecycle.strategyName}
                </span>
              </div>

              {/* Lifecycle bar */}
              <div className="flex-1 h-5 bg-[#0d1117] rounded-sm overflow-hidden flex relative">
                {lifecycle.states.length > 0 && (() => {
                  const totalDuration = lifecycle.states.length;
                  return lifecycle.states.map((state, i) => {
                    const widthPct = (1 / totalDuration) * 100;
                    return (
                      <Tooltip key={i}>
                        <TooltipTrigger asChild>
                          <div
                            className={`h-full ${stateColor(state.state)} cursor-pointer transition-opacity hover:opacity-80`}
                            style={{ width: `${widthPct}%`, minWidth: '4px' }}
                          />
                        </TooltipTrigger>
                        <TooltipContent side="top" className="bg-[#1a1f2e] border-[#1e293b] text-[#e2e8f0] max-w-xs">
                          <div className="space-y-1 p-1">
                            <div className="flex items-center gap-1.5">
                              <Badge className={`text-[7px] h-3 px-1 font-mono ${stateBadgeBg(state.state)} ${stateTextColor(state.state)} border`}>
                                {state.state}
                              </Badge>
                              {state.capitalAction && (
                                <span className="text-[7px] font-mono text-[#94a3b8]">
                                  {state.capitalAction}
                                </span>
                              )}
                            </div>
                            <p className="text-[8px] font-mono text-[#94a3b8]">
                              {new Date(state.timestamp).toLocaleString('en', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                            </p>
                            <p className="text-[8px] font-mono text-[#64748b]">
                              Reason: {state.reason}
                            </p>
                            {state.scores && (
                              <div className="flex gap-2 text-[7px] font-mono text-[#64748b]">
                                <span>R:{state.scores.robustness.toFixed(0)}</span>
                                <span>O:{state.scores.overfitting.toFixed(0)}</span>
                                <span>S:{state.scores.stability.toFixed(0)}</span>
                              </div>
                            )}
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    );
                  });
                })()}
              </div>

              {/* Current state badge */}
              <Badge className={`text-[7px] h-4 px-1.5 font-mono shrink-0 ${stateBadgeBg(currentState)} ${stateTextColor(currentState)} border`}>
                {currentState}
              </Badge>
            </div>
          );
        })}
      </div>
    </TooltipProvider>
  );
}

// ============================================================
// STATS CARD
// ============================================================

function StatCard({
  label,
  value,
  icon: Icon,
  colorClass,
  borderColor,
  subtitle,
}: {
  label: string;
  value: string;
  icon: React.ElementType;
  colorClass: string;
  borderColor: string;
  subtitle?: string;
}) {
  return (
    <Card className="bg-[#0d1117] border-[#1e293b] p-3 relative overflow-hidden">
      <div className={`absolute top-0 left-0 w-full h-0.5 ${borderColor}`} />
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className={`h-3 w-3 ${colorClass}`} />
        <span className="text-[8px] font-mono text-[#64748b] uppercase tracking-wider">{label}</span>
      </div>
      <div className={`text-[18px] font-mono font-bold ${colorClass}`}>{value}</div>
      {subtitle && (
        <span className="text-[8px] font-mono text-[#475569]">{subtitle}</span>
      )}
    </Card>
  );
}

// ============================================================
// RISK CONTROLS SUMMARY
// ============================================================

function RiskControlsSummary({ stats, verification }: { stats: PortfolioStats | null; verification: RiskVerification | null }) {
  return (
    <div className="space-y-3">
      {/* Concentration limits */}
      {stats && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Target className="h-3 w-3 text-amber-400" />
            <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Concentration Limits
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="px-2 py-1.5 bg-[#0d1117] rounded border border-[#1e293b]">
              <span className="text-[8px] font-mono text-[#475569] block">Token Concentration</span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono font-bold text-[#e2e8f0]">
                  {stats.maxTokenConcentration.toFixed(1)}%
                </span>
                <span className="text-[8px] font-mono text-[#64748b]">
                  / {stats.maxConcentrationLimit}%
                </span>
              </div>
              <Progress
                value={Math.min(100, (stats.maxTokenConcentration / stats.maxConcentrationLimit) * 100)}
                className="h-1 mt-1 bg-[#1e293b]"
              />
            </div>
            <div className="px-2 py-1.5 bg-[#0d1117] rounded border border-[#1e293b]">
              <span className="text-[8px] font-mono text-[#475569] block">Drawdown Utilization</span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono font-bold text-[#e2e8f0]">
                  {stats.riskBudgetUtilization.currentDD.toFixed(1)}%
                </span>
                <span className="text-[8px] font-mono text-[#64748b]">
                  / {stats.riskBudgetUtilization.maxDD}%
                </span>
              </div>
              <Progress
                value={Math.min(100, stats.riskBudgetUtilization.drawdownUtilizationPct)}
                className="h-1 mt-1 bg-[#1e293b]"
              />
            </div>
          </div>
        </div>
      )}

      {/* Kill switch status */}
      {stats && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Shield className="h-3 w-3 text-red-400" />
            <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Kill Switch Status
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <div className="flex items-center gap-1.5 px-2 py-1 bg-[#0d1117] rounded border border-[#1e293b]">
              {stats.killSwitch.globalPause ? (
                <XCircle className="h-3 w-3 text-red-400" />
              ) : (
                <CheckCircle2 className="h-3 w-3 text-emerald-400" />
              )}
              <span className="text-[8px] font-mono text-[#94a3b8]">Global</span>
              <span className={`text-[8px] font-mono ${stats.killSwitch.globalPause ? 'text-red-400' : 'text-emerald-400'}`}>
                {stats.killSwitch.globalPause ? 'PAUSED' : 'ACTIVE'}
              </span>
            </div>
            <div className="flex items-center gap-1.5 px-2 py-1 bg-[#0d1117] rounded border border-[#1e293b]">
              {stats.killSwitch.portfolioDDTriggered ? (
                <XCircle className="h-3 w-3 text-red-400" />
              ) : (
                <CheckCircle2 className="h-3 w-3 text-emerald-400" />
              )}
              <span className="text-[8px] font-mono text-[#94a3b8]">Portfolio DD</span>
              <span className={`text-[8px] font-mono ${stats.killSwitch.portfolioDDTriggered ? 'text-red-400' : 'text-emerald-400'}`}>
                {stats.killSwitch.portfolioDDTriggered ? 'TRIGGERED' : 'OK'}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Risk budget */}
      {stats && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <PieChart className="h-3 w-3 text-cyan-400" />
            <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Risk Budget
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5">
            {[
              { label: 'Max Portfolio DD', value: `${stats.riskBudget.maxPortfolioDrawdownPct}%` },
              { label: 'Max Strategy DD', value: `${stats.riskBudget.maxStrategyDrawdownPct}%` },
              { label: 'Max Position Loss', value: `${stats.riskBudget.maxPositionLossPct}%` },
              { label: 'Max Concentration', value: `${stats.riskBudget.maxConcentrationPct}%` },
              { label: 'Max Chain', value: `${stats.riskBudget.maxChainPct}%` },
              { label: 'Max Correlation', value: `${stats.riskBudget.maxCorrelatedPct}%` },
            ].map((item) => (
              <div key={item.label} className="px-1.5 py-1 bg-[#0d1117] rounded border border-[#1e293b]">
                <span className="text-[7px] font-mono text-[#475569] block">{item.label}</span>
                <span className="text-[10px] font-mono font-bold text-[#e2e8f0]">{item.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Verification checklist */}
      {verification && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Eye className="h-3 w-3 text-[#d4af37]" />
            <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Risk Controls Verification
            </span>
            <Badge className={`text-[7px] h-3.5 px-1.5 font-mono ${
              verification.allChecksPresent
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'bg-red-500/15 text-red-400 border border-red-500/30'
            }`}>
              {verification.summary.coveragePct.toFixed(0)}% Coverage
            </Badge>
          </div>
          <div className="space-y-1 max-h-48 overflow-y-auto custom-scrollbar">
            {verification.checks.map((check) => (
              <div key={check.name} className="flex items-start gap-1.5 px-2 py-1.5 bg-[#0d1117] rounded border border-[#1e293b]">
                {check.present ? (
                  <CheckCircle2 className="h-3 w-3 text-emerald-400 shrink-0 mt-0.5" />
                ) : (
                  <XCircle className="h-3 w-3 text-red-400 shrink-0 mt-0.5" />
                )}
                <div className="min-w-0">
                  <div className="flex items-center gap-1">
                    <span className="text-[8px] font-mono font-semibold text-[#e2e8f0]">{check.name}</span>
                  </div>
                  <p className="text-[7px] font-mono text-[#64748b] leading-tight">{check.description}</p>
                  <p className="text-[7px] font-mono text-[#475569] mt-0.5">{check.location}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Violations / near-violations */}
      {stats && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <AlertTriangle className="h-3 w-3 text-orange-400" />
            <span className="text-[9px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Violations &amp; Near-Violations
            </span>
          </div>
          <div className="space-y-1">
            {stats.riskBudgetUtilization.drawdownUtilizationPct > 70 && (
              <div className="flex items-center gap-1.5 px-2 py-1.5 bg-orange-500/5 border border-orange-500/20 rounded">
                <AlertTriangle className="h-3 w-3 text-orange-400 shrink-0" />
                <span className="text-[8px] font-mono text-orange-400">
                  Drawdown utilization at {stats.riskBudgetUtilization.drawdownUtilizationPct.toFixed(0)}% — approaching limit
                </span>
              </div>
            )}
            {stats.maxTokenConcentration > stats.maxConcentrationLimit * 0.7 && (
              <div className="flex items-center gap-1.5 px-2 py-1.5 bg-yellow-500/5 border border-yellow-500/20 rounded">
                <AlertTriangle className="h-3 w-3 text-yellow-400 shrink-0" />
                <span className="text-[8px] font-mono text-yellow-400">
                  Token concentration at {stats.maxTokenConcentration.toFixed(1)}% — near {stats.maxConcentrationLimit}% limit
                </span>
              </div>
            )}
            {Boolean(stats.killSwitch.globalPause) && (
              <div className="flex items-center gap-1.5 px-2 py-1.5 bg-red-500/5 border border-red-500/20 rounded">
                <XCircle className="h-3 w-3 text-red-400 shrink-0" />
                <span className="text-[8px] font-mono text-red-400">
                  Global kill switch active — all trading paused
                </span>
              </div>
            )}
            {Boolean(stats.killSwitch.portfolioDDTriggered) && (
              <div className="flex items-center gap-1.5 px-2 py-1.5 bg-red-500/5 border border-red-500/20 rounded">
                <XCircle className="h-3 w-3 text-red-400 shrink-0" />
                <span className="text-[8px] font-mono text-red-400">
                  Portfolio DD kill switch triggered — drawdown exceeds limit
                </span>
              </div>
            )}
            {!Boolean(stats.killSwitch.globalPause) && stats.riskBudgetUtilization.drawdownUtilizationPct <= 70 && stats.maxTokenConcentration <= stats.maxConcentrationLimit * 0.7 && (
              <div className="flex items-center gap-1.5 px-2 py-1.5 bg-emerald-500/5 border border-emerald-500/20 rounded">
                <CheckCircle2 className="h-3 w-3 text-emerald-400 shrink-0" />
                <span className="text-[8px] font-mono text-emerald-400">All risk controls within limits</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// MAIN PORTFOLIO VIEW COMPONENT
// ============================================================

export default function PortfolioView() {
  const [equityData, setEquityData] = useState<EquityCurveData | null>(null);
  const [lifecycleData, setLifecycleData] = useState<LifecycleData | null>(null);
  const [stats, setStats] = useState<PortfolioStats | null>(null);
  const [verification, setVerification] = useState<RiskVerification | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** Fetch all portfolio data */
  const fetchAllData = useCallback(async (showRefreshLoader = false) => {
    if (showRefreshLoader) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }
    setError(null);

    try {
      const [equityRes, lifecycleRes, statsRes, verifyRes] = await Promise.allSettled([
        fetch('/api/portfolio/equity-curve'),
        fetch('/api/portfolio/lifecycle'),
        fetch('/api/portfolio/stats'),
        fetch('/api/portfolio/risk-verification'),
      ]);

      if (equityRes.status === 'fulfilled' && equityRes.value.ok) {
        const json = await equityRes.value.json();
        setEquityData(json.data);
      }

      if (lifecycleRes.status === 'fulfilled' && lifecycleRes.value.ok) {
        const json = await lifecycleRes.value.json();
        setLifecycleData(json.data);
      }

      if (statsRes.status === 'fulfilled' && statsRes.value.ok) {
        const json = await statsRes.value.json();
        setStats(json.data);
      }

      if (verifyRes.status === 'fulfilled' && verifyRes.value.ok) {
        const json = await verifyRes.value.json();
        setVerification(json.data);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load portfolio data');
      toast.error('Failed to load portfolio data');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchAllData();
  }, [fetchAllData]);

  // Auto-refresh every 15 seconds
  useEffect(() => {
    refreshTimerRef.current = setInterval(() => {
      fetchAllData(true);
    }, 15000);
    return () => {
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
    };
  }, [fetchAllData]);

  // ---- LOADING STATE ----
  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0a0e17]">
        <Loader2 className="h-8 w-8 animate-spin text-[#d4af37] mb-3" />
        <span className="text-sm font-mono text-[#64748b]">Loading portfolio...</span>
      </div>
    );
  }

  // ---- ERROR STATE ----
  if (error && !stats) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17]">
        <div className="flex items-center gap-2 px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#d4af37]/10 border border-[#d4af37]/20">
            <PieChart className="h-3.5 w-3.5 text-[#d4af37]" />
          </div>
          <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Portfolio View</span>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <AlertTriangle className="h-6 w-6 text-red-400 mb-3" />
          <p className="text-[11px] font-mono text-[#94a3b8] text-center mb-4">{error}</p>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-[10px] font-mono text-[#d4af37] hover:bg-[#d4af37]/10"
            onClick={() => fetchAllData()}
          >
            <RefreshCw className="h-3 w-3 mr-1.5" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] overflow-hidden">
      {/* ===== HEADER ===== */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-[#d4af37]/10 border border-[#d4af37]/20">
            <PieChart className="h-3.5 w-3.5 text-[#d4af37]" />
          </div>
          <div>
            <span className="text-[12px] font-mono font-bold text-[#f1f5f9]">Portfolio View</span>
            {stats && (
              <span className="text-[9px] font-mono text-[#475569] ml-2">
                {formatCurrency(stats.currentCapital)} • {formatPct(stats.totalPnlPct)}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {isRefreshing && <Loader2 className="h-3 w-3 animate-spin text-[#d4af37]" />}
          <span className="text-[8px] font-mono text-[#475569]">Auto-refresh: 15s</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-[9px] font-mono px-2 text-[#94a3b8] hover:text-[#d4af37] hover:bg-[#d4af37]/10"
            onClick={() => fetchAllData(true)}
            disabled={isRefreshing}
          >
            <RefreshCw className={`h-3 w-3 mr-1 ${isRefreshing ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* ===== SCROLLABLE CONTENT ===== */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* ---- STATS CARDS ---- */}
        {stats && (
          <div className="px-3 py-3 border-b border-[#1e293b]">
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
              <StatCard
                label="Sharpe Ratio"
                value={formatNumber(stats.sharpeRatio)}
                icon={Activity}
                colorClass={stats.sharpeRatio >= 1 ? 'text-emerald-400' : stats.sharpeRatio >= 0 ? 'text-yellow-400' : 'text-red-400'}
                borderColor={stats.sharpeRatio >= 1 ? 'bg-emerald-500' : stats.sharpeRatio >= 0 ? 'bg-yellow-500' : 'bg-red-500'}
              />
              <StatCard
                label="Sortino Ratio"
                value={formatNumber(stats.sortinoRatio)}
                icon={Target}
                colorClass={stats.sortinoRatio >= 1.5 ? 'text-emerald-400' : stats.sortinoRatio >= 0 ? 'text-yellow-400' : 'text-red-400'}
                borderColor={stats.sortinoRatio >= 1.5 ? 'bg-emerald-500' : stats.sortinoRatio >= 0 ? 'bg-yellow-500' : 'bg-red-500'}
              />
              <StatCard
                label="Max Drawdown"
                value={`${stats.maxDrawdownPct.toFixed(1)}%`}
                icon={TrendingDown}
                colorClass={stats.maxDrawdownPct <= 10 ? 'text-emerald-400' : stats.maxDrawdownPct <= 20 ? 'text-yellow-400' : 'text-red-400'}
                borderColor={stats.maxDrawdownPct <= 10 ? 'bg-emerald-500' : stats.maxDrawdownPct <= 20 ? 'bg-yellow-500' : 'bg-red-500'}
              />
              <StatCard
                label="7D Correlation"
                value={(stats.rollingCorrelationAvg * 100).toFixed(1) + '%'}
                icon={BarChart3}
                colorClass={stats.rollingCorrelationAvg <= 0.3 ? 'text-emerald-400' : stats.rollingCorrelationAvg <= 0.5 ? 'text-yellow-400' : 'text-red-400'}
                borderColor={stats.rollingCorrelationAvg <= 0.3 ? 'bg-emerald-500' : stats.rollingCorrelationAvg <= 0.5 ? 'bg-yellow-500' : 'bg-red-500'}
                subtitle="avg pairwise"
              />
              <StatCard
                label="Win Rate"
                value={`${(stats.winRate * 100).toFixed(1)}%`}
                icon={stats.winRate >= 0.5 ? TrendingUp : TrendingDown}
                colorClass={stats.winRate >= 0.5 ? 'text-emerald-400' : stats.winRate >= 0.35 ? 'text-yellow-400' : 'text-red-400'}
                borderColor={stats.winRate >= 0.5 ? 'bg-emerald-500' : stats.winRate >= 0.35 ? 'bg-yellow-500' : 'bg-red-500'}
                subtitle={`${stats.winningTrades}W / ${stats.losingTrades}L`}
              />
              <StatCard
                label="Total PnL"
                value={formatCurrency(stats.totalPnlUsd)}
                icon={Zap}
                colorClass={stats.totalPnlUsd >= 0 ? 'text-emerald-400' : 'text-red-400'}
                borderColor={stats.totalPnlUsd >= 0 ? 'bg-emerald-500' : 'bg-red-500'}
                subtitle={formatPct(stats.totalPnlPct)}
              />
            </div>
          </div>
        )}

        {/* ---- EQUITY CURVE ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="flex items-center gap-1.5 mb-2">
            <Activity className="h-3.5 w-3.5 text-[#d4af37]" />
            <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Equity Curve
            </span>
            {equityData && (
              <span className="text-[8px] font-mono text-[#475569] ml-auto">
                {equityData.totalTrades} trades • {equityData.openPositions} open
              </span>
            )}
          </div>
          <div className="bg-[#0d1117] rounded-lg border border-[#1e293b] p-2">
            <EquityCurveChart data={equityData?.curve ?? []} />
          </div>
        </div>

        {/* ---- STRATEGY LIFECYCLE ---- */}
        <div className="px-3 py-3 border-b border-[#1e293b]">
          <div className="flex items-center gap-1.5 mb-2">
            <Shield className="h-3.5 w-3.5 text-cyan-400" />
            <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Strategy Lifecycle
            </span>
            {lifecycleData && (
              <div className="flex items-center gap-1.5 ml-auto">
                {Object.entries(lifecycleData.stateDistribution).map(([state, count]) => (
                  count > 0 && (
                    <Badge key={state} className={`text-[7px] h-3.5 px-1 font-mono ${stateBadgeBg(state)} ${stateTextColor(state)} border`}>
                      {count} {state}
                    </Badge>
                  )
                ))}
              </div>
            )}
          </div>
          {/* Legend */}
          <div className="flex items-center gap-3 mb-2">
            {['ACTIVE', 'CONDITIONAL', 'PAUSED', 'REJECTED'].map(state => (
              <div key={state} className="flex items-center gap-1">
                <div className={`w-3 h-2 rounded-sm ${stateColor(state)}`} />
                <span className="text-[7px] font-mono text-[#64748b]">{state}</span>
              </div>
            ))}
          </div>
          <StrategyLifecycleLanes lifecycles={lifecycleData?.lifecycles ?? []} />
        </div>

        {/* ---- RISK CONTROLS ENFORCEMENT ---- */}
        <div className="px-3 py-3">
          <div className="flex items-center gap-1.5 mb-3">
            <Shield className="h-3.5 w-3.5 text-red-400" />
            <span className="text-[10px] font-mono font-semibold text-[#94a3b8] uppercase tracking-wider">
              Risk Controls Enforcement
            </span>
          </div>
          <RiskControlsSummary stats={stats} verification={verification} />
        </div>
      </div>
    </div>
  );
}
