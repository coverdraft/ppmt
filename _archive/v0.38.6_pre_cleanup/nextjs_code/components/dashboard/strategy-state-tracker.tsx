'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Activity,
  Clock,
  Loader2,
  Pause,
  Play,
  ArrowRight,
  Zap,
  FlaskConical,
  Shield,
  AlertTriangle,
  RefreshCw,
  Timer,
  BarChart3,
  History,
  ChevronDown,
  ChevronUp,
  MoreVertical,
  TrendingUp,
  TrendingDown,
} from 'lucide-react';
import { useState, useCallback, useEffect, useRef } from 'react';

// ============================================================
// TYPES
// ============================================================

type StrategyStatus =
  | 'IDLE'
  | 'BACKTESTING'
  | 'PAPER_TRADING'
  | 'LIVE'
  | 'PAUSED'
  | 'ERROR'
  | 'EVOLVED';

interface StateHistoryEntry {
  id: string;
  status: string;
  previousStatus: string | null;
  triggerReason: string;
  totalPnlUsd: number;
  totalPnlPct: number;
  sharpeRatio: number;
  winRate: number;
  totalTrades: number;
  openPositions: number;
  generation: number;
  parentId: string | null;
  improvementPct: number;
  metadata: string;
  createdAt: string;
}

interface StrategyWithState {
  id: string;
  name: string;
  category: string;
  icon: string;
  isActive: boolean;
  isPaperTrading: boolean;
  version: number;
  parentSystemId: string | null;
  totalBacktests: number;
  bestSharpe: number;
  bestWinRate: number;
  bestPnlPct: number;
  createdAt: string;
  updatedAt: string;
  currentState: {
    status: StrategyStatus;
    previousStatus: string | null;
    triggerReason: string;
    totalPnlUsd: number;
    totalPnlPct: number;
    sharpeRatio: number;
    winRate: number;
    totalTrades: number;
    openPositions: number;
    generation: number;
    improvementPct: number;
    recordedAt: string;
  } | null;
  stateHistory: StateHistoryEntry[];
}

interface StateStatistics {
  statusCounts: Record<string, number>;
  totalStrategies: number;
  avgTimeInState: Record<string, number>;
  recentTransitions: Array<{
    systemId: string;
    systemName: string;
    fromStatus: string | null;
    toStatus: string;
    triggerReason: string;
    createdAt: string;
  }>;
  transitionCounts: Record<string, number>;
}

// ============================================================
// STATUS CONFIG
// ============================================================

const STATUS_CONFIG: Record<string, {
  label: string;
  color: string;
  bgColor: string;
  borderColor: string;
  icon: React.ElementType;
  order: number;
}> = {
  LIVE: {
    label: 'LIVE',
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-400/10',
    borderColor: 'border-yellow-400/30',
    icon: Activity,
    order: 0,
  },
  PAPER_TRADING: {
    label: 'PAPER',
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-400/10',
    borderColor: 'border-emerald-400/30',
    icon: Play,
    order: 1,
  },
  BACKTESTING: {
    label: 'BACKTEST',
    color: 'text-purple-400',
    bgColor: 'bg-purple-400/10',
    borderColor: 'border-purple-400/30',
    icon: FlaskConical,
    order: 2,
  },
  EVOLVED: {
    label: 'EVOLVED',
    color: 'text-amber-400',
    bgColor: 'bg-amber-400/10',
    borderColor: 'border-amber-400/30',
    icon: Zap,
    order: 3,
  },
  PAUSED: {
    label: 'PAUSED',
    color: 'text-orange-400',
    bgColor: 'bg-orange-400/10',
    borderColor: 'border-orange-400/30',
    icon: Pause,
    order: 4,
  },
  ERROR: {
    label: 'ERROR',
    color: 'text-red-400',
    bgColor: 'bg-red-400/10',
    borderColor: 'border-red-400/30',
    icon: AlertTriangle,
    order: 5,
  },
  IDLE: {
    label: 'IDLE',
    color: 'text-[#64748b]',
    bgColor: 'bg-[#64748b]/10',
    borderColor: 'border-[#64748b]/30',
    icon: Clock,
    order: 6,
  },
};

const TIMELINE_STATES = ['IDLE', 'BACKTESTING', 'PAPER_TRADING', 'LIVE', 'PAUSED', 'ERROR', 'EVOLVED'];

const TRIGGER_COLORS: Record<string, string> = {
  MANUAL: 'text-[#94a3b8]',
  AUTO_BACKTEST: 'text-purple-400',
  AUTO_EVOLVE: 'text-amber-400',
  RISK_LIMIT: 'text-red-400',
  SCHEDULER: 'text-cyan-400',
};

function getCategoryColor(category: string): string {
  const colors: Record<string, string> = {
    ALPHA_HUNTER: '#f59e0b',
    SMART_MONEY: '#10b981',
    TECHNICAL: '#3b82f6',
    DEFENSIVE: '#06b6d4',
    BOT_AWARE: '#8b5cf6',
    DEEP_ANALYSIS: '#ec4899',
    MICRO_STRUCTURE: '#f97316',
    ADAPTIVE: '#f43f5e',
  };
  return colors[category] || '#64748b';
}

function formatTimeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h`;
  const days = hours / 24;
  return `${Math.round(days)}d`;
}

// ============================================================
// STATE TIMELINE BAR COMPONENT
// ============================================================

function StateTimelineBar({ history }: { history: StateHistoryEntry[] }) {
  if (history.length === 0) {
    return (
      <div className="flex items-center gap-0.5 h-4 w-full">
        {TIMELINE_STATES.map((state) => {
          return (
            <div
              key={state}
              className="flex-1 h-full rounded-sm bg-[#1a1f2e] border border-[#1e293b] flex items-center justify-center"
              title={state}
            >
              <span className="text-[6px] font-mono text-[#475569]">{state.charAt(0)}</span>
            </div>
          );
        })}
      </div>
    );
  }

  // Build timeline segments from history (reversed to get chronological order)
  const reversed = [...history].reverse();
  const segments: Array<{ state: string; width: number }> = [];
  const totalDuration = reversed.length > 1
    ? new Date(reversed[reversed.length - 1].createdAt).getTime() - new Date(reversed[0].createdAt).getTime()
    : 60000; // default 1 minute

  for (let i = 0; i < reversed.length; i++) {
    const entry = reversed[i];
    const nextEntry = reversed[i + 1];
    const start = new Date(entry.createdAt).getTime();
    const end = nextEntry ? new Date(nextEntry.createdAt).getTime() : Date.now();
    const duration = end - start;
    const width = Math.max(5, (duration / Math.max(totalDuration, 1)) * 100);

    segments.push({ state: entry.status, width });
  }

  // Normalize widths to 100%
  const totalWidth = segments.reduce((s, seg) => s + seg.width, 0);

  return (
    <div className="flex items-center gap-0.5 h-4 w-full rounded overflow-hidden">
      {segments.map((seg, i) => {
        const config = STATUS_CONFIG[seg.state] || STATUS_CONFIG.IDLE;
        const pct = (seg.width / totalWidth) * 100;
        return (
          <div
            key={i}
            className={`h-full ${config.bgColor} flex items-center justify-center transition-all`}
            style={{ width: `${pct}%`, minWidth: pct > 3 ? undefined : '4px' }}
            title={`${seg.state} (${pct.toFixed(0)}%)`}
          >
            {pct > 10 && (
              <span className="text-[6px] font-mono text-[#94a3b8] truncate px-0.5">{seg.state}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// STATE LEGEND COMPONENT
// ============================================================

function StateLegend() {
  return (
    <div className="flex flex-wrap gap-2 px-1">
      {TIMELINE_STATES.map((state) => {
        const config = STATUS_CONFIG[state];
        return (
          <div key={state} className="flex items-center gap-1">
            <div className={`w-2 h-2 rounded-sm ${config.bgColor}`} />
            <span className="text-[7px] font-mono text-[#64748b]">{state}</span>
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// STRATEGY CARD COMPONENT
// ============================================================

// ============================================================
// LIVE PNL COUNTER COMPONENT
// ============================================================

function LivePnlCounter({ pnlUsd, pnlPct }: { pnlUsd: number; pnlPct: number }) {
  const isPositive = pnlUsd >= 0;
  const displayRef = useRef<HTMLSpanElement>(null);
  const prevPnlRef = useRef(pnlUsd);

  useEffect(() => {
    prevPnlRef.current = pnlUsd;
  }, [pnlUsd]);

  useEffect(() => {
    const startValue = prevPnlRef.current;
    const endValue = pnlUsd;
    const diff = endValue - startValue;
    if (Math.abs(diff) < 0.01 || !displayRef.current) {
      if (displayRef.current) {
        displayRef.current.textContent = `${endValue >= 0 ? '+' : ''}${endValue.toFixed(2)}$`;
      }
      return;
    }
    const duration = 600;
    const startTime = Date.now();
    let rafId: number;
    const animate = () => {
      const elapsed = Date.now() - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = startValue + diff * eased;
      if (displayRef.current) {
        displayRef.current.textContent = `${current >= 0 ? '+' : ''}${current.toFixed(2)}$`;
      }
      if (progress < 1) {
        rafId = requestAnimationFrame(animate);
      }
    };
    rafId = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafId);
  }, [pnlUsd]);

  return (
    <div className={`flex items-center gap-2 px-2 py-1 rounded-md ${
      isPositive ? 'bg-emerald-500/10 border border-emerald-500/20' : 'bg-red-500/10 border border-red-500/20'
    }`} style={{
      boxShadow: isPositive
        ? '0 0 12px rgba(16, 185, 129, 0.15), 0 0 4px rgba(16, 185, 129, 0.1)'
        : '0 0 12px rgba(239, 68, 68, 0.15), 0 0 4px rgba(239, 68, 68, 0.1)',
    }}>
      {isPositive ? (
        <TrendingUp className="h-3 w-3 text-emerald-400" />
      ) : (
        <TrendingDown className="h-3 w-3 text-red-400" />
      )}
      <span
        ref={displayRef}
        className={`text-[11px] font-mono font-bold ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}
      >
        {pnlUsd >= 0 ? '+' : ''}{pnlUsd.toFixed(2)}$
      </span>
      <span className={`text-[9px] font-mono ${isPositive ? 'text-emerald-400/70' : 'text-red-400/70'}`}>
        ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
      </span>
    </div>
  );
}

// ============================================================
// STRATEGY CARD COMPONENT
// ============================================================

function StrategyCard({
  strategy,
  isExpanded,
  onToggle,
  onStateChange,
}: {
  strategy: StrategyWithState;
  isExpanded: boolean;
  onToggle: () => void;
  onStateChange: (id: string, newStatus: StrategyStatus) => void;
}) {
  const status = strategy.currentState?.status || 'IDLE';
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.IDLE;
  const StatusIcon = config.icon;
  const catColor = getCategoryColor(strategy.category);
  const isLive = status === 'LIVE' || status === 'PAPER_TRADING';
  const [forceStatus, setForceStatus] = useState<string>('');
  const [actionLoading, setActionLoading] = useState(false);

  const handlePause = async () => {
    setActionLoading(true);
    onStateChange(strategy.id, 'PAUSED');
    setTimeout(() => setActionLoading(false), 500);
  };

  const handleResume = async () => {
    setActionLoading(true);
    onStateChange(strategy.id, 'IDLE');
    setTimeout(() => setActionLoading(false), 500);
  };

  const handleForceTransition = () => {
    if (forceStatus && forceStatus !== status) {
      setActionLoading(true);
      onStateChange(strategy.id, forceStatus as StrategyStatus);
      setForceStatus('');
      setTimeout(() => setActionLoading(false), 500);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className={`bg-[#111827] border rounded-lg overflow-hidden transition-all hover:border-[#2d3748] ${
        isLive ? config.borderColor : 'border-[#1e293b]'
      }`}
    >
      <div className="p-3">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="text-sm">{strategy.icon}</span>
            <span className="font-mono text-[11px] font-bold text-[#e2e8f0] max-w-[160px] truncate">
              {strategy.name}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            {isLive && (
              <span className="relative flex h-2 w-2">
                <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${config.bgColor} opacity-75`} />
                <span className={`relative inline-flex rounded-full h-2 w-2 ${config.bgColor}`} />
              </span>
            )}
            <StatusIcon className={`h-3 w-3 ${config.color}`} />
            <span className={`text-[8px] font-mono ${config.color}`}>{config.label}</span>

            {/* Quick Actions Dropdown */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="ml-1 p-0.5 rounded hover:bg-[#1e293b] transition-colors"
                  disabled={actionLoading}
                >
                  <MoreVertical className="h-3 w-3 text-[#64748b]" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent className="bg-[#1a1f2e] border-[#2d3748] w-48" align="end">
                <DropdownMenuLabel className="text-[8px] font-mono text-[#64748b] uppercase">
                  Quick Actions
                </DropdownMenuLabel>
                <DropdownMenuSeparator className="bg-[#2d3748]" />
                {status !== 'PAUSED' && status !== 'IDLE' && (
                  <DropdownMenuItem
                    className="text-[9px] font-mono text-orange-400 focus:bg-[#2d3748] focus:text-orange-400 cursor-pointer"
                    onClick={handlePause}
                  >
                    <Pause className="h-3 w-3 mr-2" />
                    Pause Strategy
                  </DropdownMenuItem>
                )}
                {status === 'PAUSED' && (
                  <DropdownMenuItem
                    className="text-[9px] font-mono text-emerald-400 focus:bg-[#2d3748] focus:text-emerald-400 cursor-pointer"
                    onClick={handleResume}
                  >
                    <Play className="h-3 w-3 mr-2" />
                    Resume Strategy
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator className="bg-[#2d3748]" />
                <DropdownMenuLabel className="text-[8px] font-mono text-[#64748b] uppercase">
                  Force Transition
                </DropdownMenuLabel>
                <div className="px-2 py-1.5 flex items-center gap-1">
                  <Select value={forceStatus} onValueChange={(val) => setForceStatus(val)}>
                    <SelectTrigger className="h-5 text-[8px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#94a3b8] flex-1">
                      <SelectValue placeholder="Select state..." />
                    </SelectTrigger>
                    <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                      {(['IDLE', 'BACKTESTING', 'PAPER_TRADING', 'LIVE', 'PAUSED', 'ERROR'] as StrategyStatus[]).map((s) => (
                        <SelectItem key={s} value={s} className="text-[8px] font-mono">
                          {STATUS_CONFIG[s]?.label || s}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <button
                    onClick={handleForceTransition}
                    disabled={!forceStatus || forceStatus === status}
                    className="h-5 px-1.5 rounded text-[7px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                  >
                    GO
                  </button>
                </div>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {/* Real-time PnL Counter for active strategies */}
        {isLive && strategy.currentState && (
          <div className="mb-2">
            <LivePnlCounter
              pnlUsd={strategy.currentState.totalPnlUsd}
              pnlPct={strategy.currentState.totalPnlPct}
            />
          </div>
        )}

        {/* Category & Version badges */}
        <div className="flex items-center gap-1.5 mb-2">
          <Badge
            className="text-[7px] h-3.5 px-1 font-mono border-0"
            style={{ backgroundColor: `${catColor}20`, color: catColor }}
          >
            {strategy.category.replace(/_/g, ' ')}
          </Badge>
          {strategy.version > 1 && (
            <Badge className="text-[7px] h-3.5 px-1 font-mono bg-[#1a1f2e] text-[#94a3b8] border-0">
              v{strategy.version}
            </Badge>
          )}
          {strategy.parentSystemId && (
            <Badge className="text-[7px] h-3.5 px-1 font-mono bg-[#d4af37]/10 text-[#d4af37] border-0">
              evolved
            </Badge>
          )}
          {strategy.currentState?.generation && strategy.currentState.generation > 1 && (
            <Badge className="text-[7px] h-3.5 px-1 font-mono bg-amber-500/10 text-amber-400 border-0">
              gen {strategy.currentState.generation}
            </Badge>
          )}
        </div>

        {/* State Timeline Bar */}
        <div className="mb-2">
          <StateTimelineBar history={strategy.stateHistory} />
        </div>

        {/* Key metrics */}
        <div className="grid grid-cols-4 gap-2 text-[9px] font-mono mb-2">
          <div>
            <span className="text-[#475569] uppercase block">Backtests</span>
            <span className="text-[#94a3b8]">{strategy.totalBacktests}</span>
          </div>
          <div>
            <span className="text-[#475569] uppercase block">Sharpe</span>
            <span className={strategy.bestSharpe > 1 ? 'text-emerald-400' : 'text-[#94a3b8]'}>
              {strategy.bestSharpe.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="text-[#475569] uppercase block">WR</span>
            <span className="text-[#94a3b8]">{(strategy.bestWinRate * 100).toFixed(0)}%</span>
          </div>
          <div>
            <span className="text-[#475569] uppercase block">PnL</span>
            <span className={strategy.bestPnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
              {strategy.bestPnlPct >= 0 ? '+' : ''}{strategy.bestPnlPct.toFixed(1)}%
            </span>
          </div>
        </div>

        {/* Current state info */}
        {strategy.currentState && (
          <div className="flex items-center justify-between text-[8px] font-mono">
            <div className="flex items-center gap-1.5">
              {strategy.currentState.previousStatus && (
                <>
                  <span className="text-[#475569]">{strategy.currentState.previousStatus}</span>
                  <ArrowRight className="h-2.5 w-2.5 text-[#475569]" />
                </>
              )}
              <span className={config.color}>{strategy.currentState.status}</span>
              <span className={`text-[6px] ${TRIGGER_COLORS[strategy.currentState.triggerReason] || 'text-[#475569]'}`}>
                ({strategy.currentState.triggerReason})
              </span>
            </div>
            <div className="flex items-center gap-1 text-[#475569]">
              <Timer className="h-2.5 w-2.5" />
              <span>{formatTimeAgo(strategy.currentState.recordedAt)}</span>
            </div>
          </div>
        )}

        {/* Expand toggle */}
        {strategy.stateHistory.length > 0 && (
          <button
            onClick={onToggle}
            className="mt-2 flex items-center gap-1 text-[8px] font-mono text-[#475569] hover:text-[#94a3b8] transition-colors w-full justify-center"
          >
            {isExpanded ? (
              <>
                <ChevronUp className="h-3 w-3" />
                <span>HIDE HISTORY ({strategy.stateHistory.length})</span>
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" />
                <span>SHOW HISTORY ({strategy.stateHistory.length})</span>
              </>
            )}
          </button>
        )}
      </div>

      {/* Expanded history */}
      <AnimatePresence>
        {isExpanded && strategy.stateHistory.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="border-t border-[#1e293b] bg-[#0d1117] p-3 max-h-64 overflow-y-auto">
              <div className="space-y-2">
                {strategy.stateHistory.map((entry, idx) => {
                  const entryConfig = STATUS_CONFIG[entry.status] || STATUS_CONFIG.IDLE;
                  return (
                    <div
                      key={entry.id}
                      className="flex items-start gap-2 text-[8px] font-mono"
                    >
                      {/* Timeline dot */}
                      <div className="flex flex-col items-center mt-0.5">
                        <div className={`w-2 h-2 rounded-full ${entryConfig.bgColor} border ${entryConfig.borderColor}`} />
                        {idx < strategy.stateHistory.length - 1 && (
                          <div className="w-px h-4 bg-[#1e293b]" />
                        )}
                      </div>

                      {/* Entry content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1">
                          {entry.previousStatus && (
                            <>
                              <span className="text-[#475569]">{entry.previousStatus}</span>
                              <ArrowRight className="h-2 w-2 text-[#475569]" />
                            </>
                          )}
                          <span className={entryConfig.color}>{entry.status}</span>
                          <span className={`text-[6px] ${TRIGGER_COLORS[entry.triggerReason] || 'text-[#475569]'}`}>
                            ({entry.triggerReason})
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-[#475569] mt-0.5">
                          <span>{formatTimeAgo(entry.createdAt)}</span>
                          {entry.totalTrades > 0 && <span>Trades: {entry.totalTrades}</span>}
                          {entry.sharpeRatio > 0 && <span>Sharpe: {entry.sharpeRatio.toFixed(2)}</span>}
                          {entry.improvementPct > 0 && (
                            <span className="text-emerald-400">
                              +{entry.improvementPct.toFixed(1)}%
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ============================================================
// STATE FLOW DIAGRAM COMPONENT
// ============================================================

function StateFlowDiagram({ statistics }: { statistics: StateStatistics | null }) {
  // Define the main progression flow
  const flowStates = ['IDLE', 'BACKTESTING', 'PAPER_TRADING', 'LIVE'] as const;

  // Count transitions between adjacent states in the flow
  const transitionCounts: Record<string, number> = {};
  if (statistics?.transitionCounts) {
    // Parse transition counts like "IDLE->BACKTESTING": 5
    for (const [key, count] of Object.entries(statistics.transitionCounts)) {
      transitionCounts[key] = count;
    }
  }

  return (
    <div className="bg-[#0d1117] rounded-lg border border-[#1e293b] p-3">
      <div className="flex items-center gap-2 mb-3">
        <ArrowRight className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[10px] font-mono text-[#d4af37] uppercase tracking-wider font-bold">State Flow Diagram</span>
        <span className="text-[8px] font-mono text-[#475569]">Most common transitions</span>
      </div>

      <div className="flex items-center justify-between gap-1 overflow-x-auto">
        {flowStates.map((state, idx) => {
          const config = STATUS_CONFIG[state];
          const count = statistics?.statusCounts?.[state] || 0;
          const transitionKey = idx < flowStates.length - 1
            ? `${flowStates[idx]}->${flowStates[idx + 1]}`
            : null;
          const transitionCount = transitionKey ? (transitionCounts[transitionKey] || 0) : 0;

          return (
            <div key={state} className="flex items-center gap-1 shrink-0">
              {/* State node */}
              <div className={`flex flex-col items-center gap-1 px-2.5 py-2 rounded-lg border ${config.borderColor} ${config.bgColor} min-w-[64px]`}>
                <div className="flex items-center gap-1">
                  {(() => {
                    const Icon = config.icon;
                    return <Icon className={`h-3 w-3 ${config.color}`} />;
                  })()}
                  <span className={`text-[8px] font-mono font-bold ${config.color}`}>{config.label}</span>
                </div>
                <span className="text-[7px] font-mono text-[#64748b]">{count} strategies</span>
              </div>

              {/* Arrow with count */}
              {idx < flowStates.length - 1 && (
                <div className="flex flex-col items-center gap-0.5 mx-1">
                  <span className="text-[7px] font-mono text-[#d4af37] font-bold">
                    {transitionCount > 0 ? transitionCount : '—'}
                  </span>
                  <svg width="24" height="12" viewBox="0 0 24 12">
                    <line x1="0" y1="6" x2="18" y2="6" stroke={transitionCount > 0 ? '#d4af37' : '#2d3748'} strokeWidth="1.5" />
                    <polygon points="24,6 18,2 18,10" fill={transitionCount > 0 ? '#d4af37' : '#2d3748'} />
                  </svg>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Additional common transitions below */}
      {(() => {
        const extraTransitions = Object.entries(transitionCounts)
          .filter(([key]) => {
            // Filter out the main flow transitions already shown above
            const mainFlowKeys = flowStates.slice(0, -1).map((s, i) => `${s}->${flowStates[i + 1]}`);
            return !mainFlowKeys.includes(key);
          })
          .sort(([, a], [, b]) => b - a)
          .slice(0, 4);

        if (extraTransitions.length === 0) return null;
        return (
          <div className="mt-3 pt-2 border-t border-[#1e293b]">
            <span className="text-[8px] font-mono text-[#475569] uppercase block mb-1.5">Other Transitions</span>
            <div className="flex flex-wrap gap-3">
              {extraTransitions.map(([key, count]) => {
                const [from, to] = key.split('->');
                const fromConfig = STATUS_CONFIG[from] || STATUS_CONFIG.IDLE;
                const toConfig = STATUS_CONFIG[to] || STATUS_CONFIG.IDLE;
                return (
                  <div key={key} className="flex items-center gap-1">
                    <span className={`text-[8px] font-mono ${fromConfig.color}`}>{fromConfig.label}</span>
                    <ArrowRight className="h-2.5 w-2.5 text-[#475569]" />
                    <span className={`text-[8px] font-mono ${toConfig.color}`}>{toConfig.label}</span>
                    <Badge className="text-[7px] h-3 px-1 font-mono bg-[#d4af37]/10 text-[#d4af37] border-0 ml-0.5">
                      {count}
                    </Badge>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ============================================================
// STATISTICS PANEL COMPONENT
// ============================================================

function StatisticsPanel({ statistics }: { statistics: StateStatistics | null }) {
  if (!statistics) return null;

  return (
    <div className="space-y-3 p-3 bg-[#0d1117] rounded-lg border border-[#1e293b]">
      {/* Status Distribution */}
      <div>
        <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-2">
          Status Distribution
        </div>
        <div className="flex gap-1 h-3 rounded overflow-hidden">
          {TIMELINE_STATES.map((state) => {
            const count = statistics.statusCounts[state] || 0;
            if (count === 0) return null;
            const config = STATUS_CONFIG[state];
            const pct = (count / Math.max(statistics.totalStrategies, 1)) * 100;
            return (
              <div
                key={state}
                className={`${config.bgColor} h-full transition-all`}
                style={{ width: `${pct}%`, minWidth: count > 0 ? '4px' : undefined }}
                title={`${state}: ${count} (${pct.toFixed(0)}%)`}
              />
            );
          })}
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1.5">
          {TIMELINE_STATES.map((state) => {
            const count = statistics.statusCounts[state] || 0;
            if (count === 0) return null;
            const config = STATUS_CONFIG[state];
            return (
              <div key={state} className="flex items-center gap-1">
                <div className={`w-1.5 h-1.5 rounded-sm ${config.bgColor}`} />
                <span className={`text-[7px] font-mono ${config.color}`}>
                  {state}: {count}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Avg Time in State */}
      {Object.keys(statistics.avgTimeInState).length > 0 && (
        <div>
          <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1">
            Avg Time in State
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
            {Object.entries(statistics.avgTimeInState).map(([state, minutes]) => {
              const config = STATUS_CONFIG[state];
              return (
                <div key={state} className="flex items-center justify-between text-[8px] font-mono">
                  <span className={config?.color || 'text-[#64748b]'}>{state}</span>
                  <span className="text-[#94a3b8]">{formatDuration(minutes)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Recent Transitions */}
      {statistics.recentTransitions.length > 0 && (
        <div>
          <div className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1">
            Recent Transitions
          </div>
          <div className="space-y-1 max-h-32 overflow-y-auto">
            {statistics.recentTransitions.slice(0, 8).map((t, i) => (
              <div key={i} className="flex items-center gap-1 text-[7px] font-mono">
                <span className="text-[#475569] truncate max-w-[100px]">{t.systemName}</span>
                <span className="text-[#475569]">{t.fromStatus || 'INIT'}</span>
                <ArrowRight className="h-2 w-2 text-[#475569] shrink-0" />
                <span className={STATUS_CONFIG[t.toStatus]?.color || 'text-[#94a3b8]'}>{t.toStatus}</span>
                <span className="text-[#475569] ml-auto">{formatTimeAgo(t.createdAt)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// FILTER BAR COMPONENT
// ============================================================

function FilterBar({
  statusFilter,
  onStatusFilterChange,
  categoryFilter,
  onCategoryFilterChange,
  strategies,
}: {
  statusFilter: string;
  onStatusFilterChange: (s: string) => void;
  categoryFilter: string;
  onCategoryFilterChange: (s: string) => void;
  strategies: StrategyWithState[];
}) {
  // Get unique categories
  const categories = Array.from(new Set(strategies.map((s) => s.category)));

  return (
    <div className="flex flex-wrap gap-1.5 mb-3">
      {/* Status filter */}
      <div className="flex gap-0.5 bg-[#0d1117] rounded-md p-0.5 border border-[#1e293b]">
        <button
          onClick={() => onStatusFilterChange('')}
          className={`text-[7px] font-mono px-1.5 py-0.5 rounded transition-colors ${
            !statusFilter ? 'bg-[#1e293b] text-[#e2e8f0]' : 'text-[#475569] hover:text-[#94a3b8]'
          }`}
        >
          ALL
        </button>
        {TIMELINE_STATES.map((state) => {
          const config = STATUS_CONFIG[state];
          return (
            <button
              key={state}
              onClick={() => onStatusFilterChange(state === statusFilter ? '' : state)}
              className={`text-[7px] font-mono px-1.5 py-0.5 rounded transition-colors ${
                statusFilter === state
                  ? `${config.bgColor} ${config.color}`
                  : 'text-[#475569] hover:text-[#94a3b8]'
              }`}
            >
              {state}
            </button>
          );
        })}
      </div>

      {/* Category filter */}
      {categories.length > 1 && (
        <div className="flex gap-0.5 bg-[#0d1117] rounded-md p-0.5 border border-[#1e293b]">
          <button
            onClick={() => onCategoryFilterChange('')}
            className={`text-[7px] font-mono px-1.5 py-0.5 rounded transition-colors ${
              !categoryFilter ? 'bg-[#1e293b] text-[#e2e8f0]' : 'text-[#475569] hover:text-[#94a3b8]'
            }`}
          >
            ALL
          </button>
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => onCategoryFilterChange(cat === categoryFilter ? '' : cat)}
              className={`text-[7px] font-mono px-1.5 py-0.5 rounded transition-colors ${
                categoryFilter === cat
                  ? 'bg-[#1e293b] text-[#e2e8f0]'
                  : 'text-[#475569] hover:text-[#94a3b8]'
              }`}
            >
              {cat.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function StrategyStateTracker() {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [activeTab, setActiveTab] = useState('overview');
  const queryClient = useQueryClient();

  // Fetch strategy states with real-time polling
  const {
    data: response,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['strategy-states', statusFilter, categoryFilter],
    queryFn: async () => {
      try {
        const params = new URLSearchParams();
        if (statusFilter) params.set('status', statusFilter);
        if (categoryFilter) params.set('category', categoryFilter);
        if (activeTab === 'history') params.set('includeStats', 'true');

        const res = await fetch(`/api/strategy-states?${params.toString()}`);
        if (!res.ok) return { data: [], statistics: null };
        const json = await res.json();
        return {
          data: (json.data || []) as StrategyWithState[],
          statistics: (json.statistics || null) as StateStatistics | null,
        };
      } catch {
        return { data: [], statistics: null };
      }
    },
    staleTime: 15000,
    refetchInterval: 30000, // Poll every 30s (reduced from 10s for perf)
  });

  // Mutation for updating strategy state
  const stateMutation = useMutation({
    mutationFn: async ({ id, newStatus }: { id: string; newStatus: StrategyStatus }) => {
      const res = await fetch(`/api/strategy-states/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          status: newStatus,
          triggerReason: 'MANUAL',
        }),
      });
      if (!res.ok) throw new Error('Failed to update strategy state');
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategy-states'] });
    },
  });

  const handleStateChange = useCallback((id: string, newStatus: StrategyStatus) => {
    stateMutation.mutate({ id, newStatus });
  }, [stateMutation]);

  const strategies = response?.data || [];
  const statistics = response?.statistics || null;

  // Group by status for overview
  const activeStrategies = strategies.filter(
    (s) => s.currentState?.status === 'LIVE' || s.currentState?.status === 'PAPER_TRADING',
  );
  const backtestingStrategies = strategies.filter(
    (s) => s.currentState?.status === 'BACKTESTING',
  );
  const evolvedStrategies = strategies.filter(
    (s) => s.currentState?.status === 'EVOLVED',
  );
  const pausedStrategies = strategies.filter(
    (s) => s.currentState?.status === 'PAUSED' || s.currentState?.status === 'ERROR',
  );
  const idleStrategies = strategies.filter(
    (s) => s.currentState?.status === 'IDLE' || !s.currentState,
  );

  const toggleExpand = useCallback((id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  if (isLoading) {
    return (
      <div className="flex flex-col h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117]">
          <Activity className="h-4 w-4 text-[#d4af37]" />
          <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">STRATEGY STATES</span>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 text-[#d4af37] animate-spin" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-[#d4af37]" />
          <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">STRATEGY STATES</span>
        </div>
        <div className="flex items-center gap-2">
          {activeStrategies.length > 0 && (
            <Badge className="text-[7px] h-3.5 px-1 font-mono bg-emerald-500/15 text-emerald-400 border-0">
              <span className="relative flex h-1.5 w-1.5 mr-1">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-400" />
              </span>
              {activeStrategies.length} active
            </Badge>
          )}
          {backtestingStrategies.length > 0 && (
            <Badge className="text-[7px] h-3.5 px-1 font-mono bg-purple-500/15 text-purple-400 border-0">
              {backtestingStrategies.length} testing
            </Badge>
          )}
          <Badge className="text-[7px] h-3.5 px-1 font-mono bg-[#1a1f2e] text-[#64748b] border-0">
            {strategies.length} total
          </Badge>
          <button
            onClick={() => refetch()}
            className="text-[#475569] hover:text-[#94a3b8] transition-colors"
            title="Refresh"
          >
            <RefreshCw className="h-3 w-3" />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
        <div className="px-3 pt-2 shrink-0">
          <TabsList className="h-6 bg-[#0d1117] border border-[#1e293b] w-full">
            <TabsTrigger value="overview" className="text-[8px] font-mono h-5 flex-1">
              <Activity className="h-2.5 w-2.5 mr-1" />
              OVERVIEW
            </TabsTrigger>
            <TabsTrigger value="history" className="text-[8px] font-mono h-5 flex-1">
              <History className="h-2.5 w-2.5 mr-1" />
              HISTORY
            </TabsTrigger>
            <TabsTrigger value="stats" className="text-[8px] font-mono h-5 flex-1">
              <BarChart3 className="h-2.5 w-2.5 mr-1" />
              STATS
            </TabsTrigger>
          </TabsList>
        </div>

        {/* OVERVIEW TAB */}
        <TabsContent value="overview" className="flex-1 min-h-0 mt-0 px-0">
          <ScrollArea className="h-full">
            <div className="p-3 space-y-3">
              {strategies.length === 0 ? (
                <div className="flex flex-col items-center py-12 text-[#64748b]">
                  <Activity className="h-10 w-10 mb-3 text-[#2d3748]" />
                  <span className="font-mono text-sm">No strategies yet</span>
                  <span className="font-mono text-[10px] text-[#475569] mt-1">
                    Generate strategies in the AI Manager to see them here
                  </span>
                </div>
              ) : (
                <>
                  {/* State Legend */}
                  <StateLegend />

                  {/* Active Strategies */}
                  {activeStrategies.length > 0 && (
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <Activity className="h-3 w-3 text-yellow-400" />
                        <span className="text-[10px] font-mono text-yellow-400 uppercase tracking-wider">Active</span>
                        <span className="text-[8px] font-mono text-[#475569]">(LIVE + PAPER)</span>
                      </div>
                      <div className="space-y-1.5">
                        {activeStrategies.map((s) => (
                          <StrategyCard
                            key={s.id}
                            strategy={s}
                            isExpanded={expandedId === s.id}
                            onToggle={() => toggleExpand(s.id)}
                            onStateChange={handleStateChange}
                          />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Backtesting Strategies */}
                  {backtestingStrategies.length > 0 && (
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <FlaskConical className="h-3 w-3 text-purple-400" />
                        <span className="text-[10px] font-mono text-purple-400 uppercase tracking-wider">Testing</span>
                      </div>
                      <div className="space-y-1.5">
                        {backtestingStrategies.map((s) => (
                          <StrategyCard
                            key={s.id}
                            strategy={s}
                            isExpanded={expandedId === s.id}
                            onToggle={() => toggleExpand(s.id)}
                            onStateChange={handleStateChange}
                          />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Evolved Strategies */}
                  {evolvedStrategies.length > 0 && (
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <Zap className="h-3 w-3 text-amber-400" />
                        <span className="text-[10px] font-mono text-amber-400 uppercase tracking-wider">Evolved</span>
                      </div>
                      <div className="space-y-1.5">
                        {evolvedStrategies.map((s) => (
                          <StrategyCard
                            key={s.id}
                            strategy={s}
                            isExpanded={expandedId === s.id}
                            onToggle={() => toggleExpand(s.id)}
                            onStateChange={handleStateChange}
                          />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Paused/Error Strategies */}
                  {pausedStrategies.length > 0 && (
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <Shield className="h-3 w-3 text-orange-400" />
                        <span className="text-[10px] font-mono text-orange-400 uppercase tracking-wider">Paused</span>
                      </div>
                      <div className="space-y-1.5">
                        {pausedStrategies.map((s) => (
                          <StrategyCard
                            key={s.id}
                            strategy={s}
                            isExpanded={expandedId === s.id}
                            onToggle={() => toggleExpand(s.id)}
                            onStateChange={handleStateChange}
                          />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Idle Strategies */}
                  {idleStrategies.length > 0 && (
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <Clock className="h-3 w-3 text-[#64748b]" />
                        <span className="text-[10px] font-mono text-[#64748b] uppercase tracking-wider">Idle</span>
                      </div>
                      <div className="space-y-1.5">
                        {idleStrategies.slice(0, 8).map((s) => (
                          <StrategyCard
                            key={s.id}
                            strategy={s}
                            isExpanded={expandedId === s.id}
                            onToggle={() => toggleExpand(s.id)}
                            onStateChange={handleStateChange}
                          />
                        ))}
                        {idleStrategies.length > 8 && (
                          <div className="text-center text-[8px] font-mono text-[#475569] py-1">
                            +{idleStrategies.length - 8} more idle strategies
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </ScrollArea>
        </TabsContent>

        {/* HISTORY TAB */}
        <TabsContent value="history" className="flex-1 min-h-0 mt-0 px-0">
          <ScrollArea className="h-full">
            <div className="p-3 space-y-3">
              <FilterBar
                statusFilter={statusFilter}
                onStatusFilterChange={setStatusFilter}
                categoryFilter={categoryFilter}
                onCategoryFilterChange={setCategoryFilter}
                strategies={strategies}
              />
              {strategies.length === 0 ? (
                <div className="flex flex-col items-center py-8 text-[#64748b]">
                  <History className="h-8 w-8 mb-2 text-[#2d3748]" />
                  <span className="font-mono text-xs">No state history yet</span>
                </div>
              ) : (
                <div className="space-y-1.5">
                  {strategies.map((s) => (
                    <StrategyCard
                      key={s.id}
                      strategy={s}
                      isExpanded={expandedId === s.id}
                      onToggle={() => toggleExpand(s.id)}
                      onStateChange={handleStateChange}
                    />
                  ))}
                </div>
              )}
            </div>
          </ScrollArea>
        </TabsContent>

        {/* STATS TAB */}
        <TabsContent value="stats" className="flex-1 min-h-0 mt-0 px-0">
          <ScrollArea className="h-full">
            <div className="p-3 space-y-3">
              {/* State Flow Diagram at top */}
              <StateFlowDiagram statistics={statistics} />

              {statistics ? (
                <StatisticsPanel statistics={statistics} />
              ) : (
                <div className="flex flex-col items-center py-8 text-[#64748b]">
                  <BarChart3 className="h-8 w-8 mb-2 text-[#2d3748]" />
                  <span className="font-mono text-xs">Loading statistics...</span>
                </div>
              )}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}
