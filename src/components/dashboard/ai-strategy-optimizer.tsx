'use client';

import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from '@/components/ui/tooltip';

import { motion, AnimatePresence } from 'framer-motion';
import { toast } from 'sonner';
import {
  Zap,
  Scan,
  Brain,
  Trophy,
  Play,
  Loader2,
  Star,
  BookmarkPlus,
  Rocket,
  Target,
  BarChart3,
  Activity,
  DollarSign,
  Trash2,
  RefreshCw,
  Sparkles,
  Filter,
  Check,
  X,
  ChevronDown,
  ChevronUp,
  Layers,
  Crosshair,
  ArrowRightLeft,
  Dna,
  CircleDot,
  TrendingUp,
  AlertTriangle,
  Clock,
  Timer,
} from 'lucide-react';
import { DataQualityGate } from './data-quality-gate';
import type { TokenCandidate, StrategyConfig, RankResult, BestStrategy } from '@/lib/types/strategy';

// ============================================================
// TYPES (local-only types, shared types imported from @/lib/types/strategy)
// ============================================================

interface ExecutionStatus {
  type: 'idle' | 'executing' | 'success' | 'error';
  message: string;
  tradeId?: string;
  systemId?: string;
  pnlUsd?: number;
  pnlPct?: number;
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
// CONSTANTS
// ============================================================

const RISK_COLORS: Record<string, { bg: string; text: string }> = {
  LOW: { bg: 'bg-emerald-500/15', text: 'text-emerald-400' },
  MEDIUM: { bg: 'bg-yellow-500/15', text: 'text-yellow-400' },
  HIGH: { bg: 'bg-orange-500/15', text: 'text-orange-400' },
  EXTREME: { bg: 'bg-red-500/15', text: 'text-red-400' },
};

const TIMEFRAMES = ['1m', '5m', '10m', '15m', '30m', '1h', '4h'];
const TOKEN_AGES = ['NEW', 'MEDIUM', 'OLD'];
const RISK_LEVELS = ['CONSERVATIVE', 'MODERATE', 'AGGRESSIVE'];

type Step = 'setup' | 'scanning' | 'generating' | 'running' | 'results' | 'activate';

// Pipeline node definitions
const PIPELINE_NODES: { step: Step; emoji: string; label: string }[] = [
  { step: 'setup', emoji: '💰', label: 'Capital' },
  { step: 'scanning', emoji: '🔍', label: 'Scan' },
  { step: 'generating', emoji: '⚡', label: 'Generate' },
  { step: 'running', emoji: '🔄', label: 'Backtest' },
  { step: 'results', emoji: '🏆', label: 'Rank' },
  { step: 'activate', emoji: '🚀', label: 'Activate' },
];

const STEP_ORDER: Step[] = ['setup', 'scanning', 'generating', 'running', 'results', 'activate'];

const CATEGORY_COLORS: Record<string, string> = {
  // AI Strategy Manager categories (from generate_strategies)
  alpha_hunter: '#f59e0b',
  smart_money: '#d4af37',
  technical: '#06b6d4',
  defensive: '#10b981',
  bot_aware: '#8b5cf6',
  adaptive: '#f97316',
  // Legacy categories (for backward compatibility)
  momentum: '#f59e0b',
  mean_reversion: '#06b6d4',
  breakout: '#8b5cf6',
  scalping: '#ef4444',
  trend_following: '#10b981',
  volatility: '#f97316',
  default: '#64748b',
};

function getCategoryColor(category: string): string {
  return CATEGORY_COLORS[category.toLowerCase()] || CATEGORY_COLORS.default;
}

/** Format category key to title case display label */
function formatCategory(category: string): string {
  return category
    .replace(/_/g, ' ')
    .split(' ')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ');
}

/** Grade color mapping */
const GRADE_COLORS: Record<string, string> = {
  'A+': '#10b981',
  'A': '#10b981',
  'B': '#d4af37',
  'C': '#f59e0b',
  'D': '#f97316',
  'F': '#ef4444',
};

// ============================================================
// PIPELINE NODE COMPONENT
// ============================================================

function PipelineNode({
  emoji,
  label,
  status,
  isLast,
}: {
  emoji: string;
  label: string;
  status: 'idle' | 'active' | 'done';
  isLast: boolean;
}) {
  return (
    <div className="flex items-center">
      <motion.div
        className="flex flex-col items-center gap-1"
        initial={false}
        animate={{ scale: status === 'active' ? 1.1 : 1 }}
        transition={{ type: 'spring', stiffness: 300, damping: 20 }}
      >
        <div
          className={`w-10 h-10 rounded-xl flex items-center justify-center text-base border-2 transition-all duration-300 ${
            status === 'done'
              ? 'bg-emerald-500/20 border-emerald-500/50 shadow-lg shadow-emerald-500/10'
              : status === 'active'
              ? 'bg-[#d4af37]/20 border-[#d4af37]/60 shadow-lg shadow-[#d4af37]/10'
              : 'bg-[#111827] border-[#1e293b]'
          }`}
        >
          {status === 'done' ? (
            <Check className="h-4 w-4 text-emerald-400" />
          ) : status === 'active' ? (
            <Loader2 className="h-4 w-4 text-[#d4af37] animate-spin" />
          ) : (
            <span>{emoji}</span>
          )}
        </div>
        <span
          className={`text-[9px] font-mono font-semibold tracking-wider ${
            status === 'done'
              ? 'text-emerald-400'
              : status === 'active'
              ? 'text-[#d4af37]'
              : 'text-[#475569]'
          }`}
        >
          {label.toUpperCase()}
        </span>
      </motion.div>
      {!isLast && (
        <div className="relative w-8 h-px mx-1 flex items-center">
          <div className={`w-full h-px ${status === 'done' ? 'bg-emerald-500/50' : 'bg-[#1e293b]'}`} />
          {status === 'done' && (
            <motion.div
              className="absolute left-0 top-1/2 -translate-y-1/2 h-px bg-emerald-500/50"
              initial={{ width: 0 }}
              animate={{ width: '100%' }}
              transition={{ duration: 0.5 }}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================
// STRATEGY CARD COMPONENT (with inline editing)
// ============================================================

function StrategyCard({
  strategy,
  onEdit,
  onRemove,
  isSelected,
  onToggleSelect,
  status,
}: {
  strategy: StrategyConfig;
  onEdit: (id: string, field: string, value: number) => void;
  onRemove: (id: string) => void;
  isSelected: boolean;
  onToggleSelect: (id: string) => void;
  status: 'pending' | 'testing' | 'done' | 'failed';
}) {
  const [expanded, setExpanded] = useState(false);

  const tp = (strategy.config?.exitSignal as Record<string, unknown>)?.takeProfit as number || 40;
  const sl = (strategy.config?.exitSignal as Record<string, unknown>)?.stopLoss as number || 15;
  const posSize = (strategy.config?.riskManagement as Record<string, unknown>)?.maxPositionSize as number || 5;
  const catColor = getCategoryColor(strategy.category);

  const statusConfig = {
    pending: { icon: <Activity className="h-3 w-3" />, color: 'text-[#64748b]', bg: 'bg-[#1a1f2e]' },
    testing: { icon: <Loader2 className="h-3 w-3 animate-spin" />, color: 'text-amber-400', bg: 'bg-amber-500/10' },
    done: { icon: <Check className="h-3 w-3" />, color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
    failed: { icon: <X className="h-3 w-3" />, color: 'text-red-400', bg: 'bg-red-500/10' },
  };
  const statusInfo = statusConfig[status];

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      className={`bg-[#111827] border rounded-lg transition-all ${
        isSelected ? 'border-[#d4af37]/50 shadow-md shadow-[#d4af37]/5' : 'border-[#1e293b] hover:border-[#2d3748]'
      }`}
    >
      <div className="p-3">
        {/* Header row */}
        <div className="flex items-center gap-2 mb-2">
          {/* Selection checkbox */}
          <button
            onClick={() => onToggleSelect(strategy.id)}
            className={`w-4 h-4 rounded border flex items-center justify-center transition-all shrink-0 ${
              isSelected
                ? 'bg-[#d4af37] border-[#d4af37] text-[#0a0e17]'
                : 'border-[#2d3748] hover:border-[#d4af37]/50'
            }`}
          >
            {isSelected && <Check className="h-2.5 w-2.5" />}
          </button>
          <span className="text-sm">{strategy.icon}</span>
          <span className="font-mono text-[11px] text-[#e2e8f0] flex-1 truncate font-semibold">
            {strategy.name}
          </span>
          {/* Status indicator */}
          <Badge className={`text-[8px] h-4 px-1.5 font-mono ${statusInfo.bg} ${statusInfo.color} border-0`}>
            {statusInfo.icon}
            <span className="ml-0.5">{status.toUpperCase()}</span>
          </Badge>
          {/* Remove */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => onRemove(strategy.id)}
                className="h-5 w-5 flex items-center justify-center text-[#475569] hover:text-red-400 transition-colors"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </TooltipTrigger>
            <TooltipContent>Remove Strategy</TooltipContent>
          </Tooltip>
        </div>

        {/* Tags row */}
        <div className="flex items-center gap-1.5 mb-2 flex-wrap">
          <Badge
            className="text-[8px] h-4 px-1.5 font-mono border-0"
            style={{ backgroundColor: `${catColor}20`, color: catColor }}
          >
            {strategy.category}
          </Badge>
          <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#94a3b8] border-0">
            {strategy.timeframe}
          </Badge>
          <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#94a3b8] border-0">
            {strategy.tokenAgeCategory === 'NEW' ? '<7d' : strategy.tokenAgeCategory === 'MEDIUM' ? '7-30d' : '>30d'}
          </Badge>
          <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#94a3b8] border-0">
            ${strategy.capitalAllocation.toFixed(0)}
          </Badge>
        </div>

        {/* Inline Quick-Edit: TP / SL / Position Size */}
        <div className="grid grid-cols-3 gap-2">
          <div>
            <label className="text-[8px] font-mono text-[#475569] uppercase block mb-0.5">TP%</label>
            <Input
              type="number"
              value={tp}
              onChange={e => onEdit(strategy.id, 'takeProfit', Number(e.target.value))}
              className="h-6 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-emerald-400 px-1.5 py-0"
            />
          </div>
          <div>
            <label className="text-[8px] font-mono text-[#475569] uppercase block mb-0.5">SL%</label>
            <Input
              type="number"
              value={sl}
              onChange={e => onEdit(strategy.id, 'stopLoss', Number(e.target.value))}
              className="h-6 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-red-400 px-1.5 py-0"
            />
          </div>
          <div>
            <label className="text-[8px] font-mono text-[#475569] uppercase block mb-0.5">Size%</label>
            <Input
              type="number"
              value={posSize}
              onChange={e => onEdit(strategy.id, 'positionSize', Number(e.target.value))}
              className="h-6 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#d4af37] px-1.5 py-0"
            />
          </div>
        </div>

        {/* Expand/collapse for more edit options */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center justify-center mt-2 text-[#475569] hover:text-[#94a3b8] transition-colors"
        >
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </button>

        <AnimatePresence>
          {expanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="pt-2 border-t border-[#1e293b] mt-1 space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[8px] font-mono text-[#475569] uppercase block mb-0.5">Name</label>
                    <Input
                      value={strategy.name}
                      onChange={e => onEdit(strategy.id, 'name', e.target.value as unknown as number)}
                      className="h-6 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] px-1.5 py-0"
                    />
                  </div>
                  <div>
                    <label className="text-[8px] font-mono text-[#475569] uppercase block mb-0.5">Allocation $</label>
                    <Input
                      type="number"
                      value={strategy.capitalAllocation}
                      onChange={e => onEdit(strategy.id, 'capitalAllocation', Number(e.target.value))}
                      className="h-6 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#d4af37] px-1.5 py-0"
                    />
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}

// ============================================================
// PORTFOLIO SUMMARY COMPONENT
// ============================================================

function PortfolioSummary({
  results,
  capital,
}: {
  results: RankResult[];
  capital: number;
}) {
  if (results.length === 0) return null;

  const totalPnl = results.reduce((sum, r) => sum + r.pnlUsd, 0);
  const avgSharpe = results.reduce((sum, r) => sum + r.sharpeRatio, 0) / results.length;
  const avgWinRate = results.reduce((sum, r) => sum + r.winRate, 0) / results.length;
  const totalAllocated = results.reduce((sum, r) => sum + r.capitalAllocation, 0);
  const avgDrawdown = results.reduce((sum, r) => sum + r.maxDrawdownPct, 0) / results.length;

  // Group by risk tolerance
  const riskGroups = results.reduce((acc, r) => {
    const key = r.riskTolerance;
    if (!acc[key]) acc[key] = { count: 0, allocation: 0 };
    acc[key].count++;
    acc[key].allocation += r.capitalAllocation;
    return acc;
  }, {} as Record<string, { count: number; allocation: number }>);

  const riskColors: Record<string, string> = {
    CONSERVATIVE: '#10b981',
    MODERATE: '#f59e0b',
    AGGRESSIVE: '#ef4444',
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4"
    >
      <div className="flex items-center gap-2 mb-3">
        <Layers className="h-3.5 w-3.5 text-[#d4af37]" />
        <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Portfolio Summary</span>
        <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
          {results.length} strategies
        </Badge>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 text-center">
          <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">Total PnL</div>
          <div className={`text-sm font-mono font-bold ${totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(0)}
          </div>
        </div>
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 text-center">
          <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">Avg Sharpe</div>
          <div className="text-sm font-mono font-bold text-[#d4af37]">{avgSharpe.toFixed(2)}</div>
        </div>
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 text-center">
          <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">Avg Win Rate</div>
          <div className="text-sm font-mono font-bold text-cyan-400">{(avgWinRate * 100).toFixed(0)}%</div>
        </div>
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 text-center">
          <div className="text-[8px] font-mono text-[#64748b] uppercase mb-1">Avg Drawdown</div>
          <div className="text-sm font-mono font-bold text-red-400">{avgDrawdown.toFixed(1)}%</div>
        </div>
      </div>

      {/* Capital Allocation Breakdown (simulated pie with colored bars) */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-[9px] font-mono text-[#64748b]">
          <span>Capital Allocation</span>
          <span>${totalAllocated.toFixed(0)} / ${capital.toLocaleString()}</span>
        </div>
        <div className="h-4 bg-[#1a1f2e] rounded-full overflow-hidden flex">
          {results.slice(0, 10).map((r, i) => (
            <div
              key={r.id}
              className="h-full transition-all duration-500 relative group"
              style={{
                width: `${Math.max((r.capitalAllocation / capital) * 100, 1)}%`,
                backgroundColor: getCategoryColor(r.category),
                opacity: 0.7 + (0.3 * (1 - i / results.length)),
              }}
              title={`${r.strategyName}: $${r.capitalAllocation.toFixed(0)}`}
            />
          ))}
        </div>

        {/* Risk Distribution */}
        <div className="mt-3">
          <div className="text-[9px] font-mono text-[#64748b] mb-1.5">Risk Distribution</div>
          <div className="space-y-1.5">
            {Object.entries(riskGroups).map(([risk, data]) => (
              <div key={risk} className="flex items-center gap-2">
                <div
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: riskColors[risk] || '#64748b' }}
                />
                <span className="text-[9px] font-mono text-[#94a3b8] w-24">{risk}</span>
                <div className="flex-1 h-2 bg-[#1a1f2e] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${(data.allocation / capital) * 100}%`,
                      backgroundColor: riskColors[risk] || '#64748b',
                      opacity: 0.6,
                    }}
                  />
                </div>
                <span className="text-[8px] font-mono text-[#64748b] w-8 text-right">
                  {data.count}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function AIStrategyOptimizer() {
  const queryClient = useQueryClient();

  // State
  const [capital, setCapital] = useState(10000);
  const [allocationMode, setAllocationMode] = useState<'distribute' | 'focus'>('distribute');
  const [selectedTimeframes, setSelectedTimeframes] = useState<string[]>(['5m', '15m', '1h']);
  const [selectedTokenAges, setSelectedTokenAges] = useState<string[]>(['NEW', 'MEDIUM']);
  const [riskTolerance, setRiskTolerance] = useState('MODERATE');
  const [strategyCount, setStrategyCount] = useState(6);
  const [currentStep, setCurrentStep] = useState<Step>('setup');
  const [generatedStrategies, setGeneratedStrategies] = useState<StrategyConfig[]>([]);
  const [loopResults, setLoopResults] = useState<Array<{ strategyId: string; strategyName: string; backtestId: string | null; status: string; error?: string }>>([]);

  // Selected strategies for grouping
  const [selectedStrategyIds, setSelectedStrategyIds] = useState<Set<string>>(new Set());
  const [groupFilter, setGroupFilter] = useState<string>('all');


  // Results filter state
  const [filterTimeframe, setFilterTimeframe] = useState<string>('ALL');
  const [filterTokenAge, setFilterTokenAge] = useState<string>('ALL');
  const [filterRisk, setFilterRisk] = useState<string>('ALL');
  const [filterRecent, setFilterRecent] = useState<number>(24); // 0 = all time, 24 = last 24h, 168 = last 7d

  // Expanded backtest detail row
  const [expandedBacktestId, setExpandedBacktestId] = useState<string | null>(null);

  // Activate count
  const [activateTopN, setActivateTopN] = useState(3);

  // Auto-running state
  const [autoRunning, setAutoRunning] = useState(false);
  const [autoRunPhase, setAutoRunPhase] = useState<string>('');

  // Hall of Fame collapsible state (visible by default)
  const [hallOfFameCollapsed, setHallOfFameCollapsed] = useState(false);

  // Refs for auto-scrolling to results
  const resultsRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  // Execution state
  const [executionStatuses, setExecutionStatuses] = useState<ExecutionStatus[]>([]);
  const [autoEvolveRunning, setAutoEvolveRunning] = useState(false);
  const [autoEvoCycles, setAutoEvoCycles] = useState(3);
  const [autoEvoInterval, setAutoEvoInterval] = useState(60); // seconds

  // Frontend-driven auto-schedule state (P2-A: discrete cycles)
  const [autoScheduleEnabled, setAutoScheduleEnabled] = useState(false);
  const [autoScheduleInterval, setAutoScheduleInterval] = useState(120); // seconds between cycles
  const [autoScheduleCountdown, setAutoScheduleCountdown] = useState(0); // seconds until next cycle
  const [autoScheduleCyclesRun, setAutoScheduleCyclesRun] = useState(0);
  const [autoScheduleTotalCycles, setAutoScheduleTotalCycles] = useState(0); // 0 = infinite
  // Ref to prevent race conditions: tracks if a cycle is locally in-progress
  // so we don't double-trigger when isPending flips or autoEvolveRunning lags
  const cycleInProgressRef = useRef(false);
  // Tick counter to force auto-schedule useEffect re-evaluation after mutations complete
  const [autoScheduleTick, setAutoScheduleTick] = useState(0);

  // Start Trading mutation (per strategy in Hall of Fame)
  const startTradingMutation = useMutation({
    mutationFn: async (strategy: BestStrategy) => {
      // First ensure the strategy is activated as a trading system
      let systemId: string | null = null;

      // Check if there's already an active trading system for this strategy
      const systemsRes = await fetch('/api/trading-systems');
      if (systemsRes.ok) {
        const systemsData = await systemsRes.json();
        const existing = (systemsData.data || []).find(
          (s: { name: string; isActive: boolean }) => s.name === `[AI] ${strategy.strategyName}` && s.isActive
        );
        if (existing) {
          systemId = existing.id;
        }
      }

      // If no active system exists, create and activate one
      if (!systemId) {
        const createRes = await fetch('/api/trading-systems', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: `[AI] ${strategy.strategyName}`,
            category: strategy.category,
            config: {
              assetFilter: { minLiquidity: 50000, minVolume24h: 10000, maxMarketCap: 10000000, chains: ['SOL', 'ETH', 'BASE'], tokenAge: strategy.tokenAgeCategory === 'NEW' ? '<7D' : strategy.tokenAgeCategory === 'MEDIUM' ? '7-30D' : '>30D' },
              phaseConfig: { genesis: strategy.tokenAgeCategory === 'NEW', early: true, growth: true, maturity: strategy.tokenAgeCategory === 'OLD', decline: false },
              entrySignal: { signalType: 'SMART_MONEY_ENTRY', confidenceThreshold: 70, confirmationRequired: true, timeWindow: 15 },
              execution: { orderType: 'LIMIT', slippageTolerance: 1.5, maxPositionSize: strategy.capitalAllocation > 0 ? Math.round(strategy.capitalAllocation * 100 / 10000) : 5, executionDelay: 0 },
              exitSignal: { takeProfit: strategy.pnlPct > 0 ? Math.round(strategy.pnlPct * 0.6) : 25, stopLoss: strategy.maxDrawdownPct > 0 ? Math.round(strategy.maxDrawdownPct * 0.5) : 10, trailingStop: true, trailingStopPercent: 10, timeBasedExit: 1440 },
              riskManagement: { maxDrawdown: strategy.maxDrawdownPct || 20, maxConcurrentTrades: 3, maxDailyLoss: 5, positionSizing: 'RISK_BASED' },
              capitalAllocation: { method: 'risk_parity', percentage: Math.round(strategy.capitalAllocation / 100), maxAllocation: 2, rebalanceFrequency: 'DAILY' },
              bigDataContext: { whaleTracking: true, smartMoneyMirror: true, botDetection: true, onChainMetrics: true, socialSentiment: false },
            },
            primaryTimeframe: strategy.timeframe || '15m',
            maxPositionPct: strategy.capitalAllocation > 0 ? Math.round(strategy.capitalAllocation * 100 / 10000) : 5,
            stopLossPct: strategy.maxDrawdownPct > 0 ? Math.round(strategy.maxDrawdownPct * 0.5) : 10,
            takeProfitPct: strategy.pnlPct > 0 ? Math.round(strategy.pnlPct * 0.6) : 25,
          }),
        });
        if (!createRes.ok) throw new Error('Failed to create trading system');
        const createData = await createRes.json();
        systemId = createData.data?.id || createData.id;

        // Activate in paper mode
        const activateRes = await fetch(`/api/trading-systems/${systemId}/activate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'paper' }),
        });
        if (!activateRes.ok) throw new Error('Failed to activate trading system');
      }

      // Execute the trade
      const execRes = await fetch('/api/execution/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          systemId,
          direction: 'LONG',
          positionSizeUsd: Math.min(strategy.capitalAllocation || 100, 100),
        }),
      });
      if (!execRes.ok) {
        const err = await execRes.json().catch(() => ({}));
        throw new Error(err.error || 'Execution failed');
      }
      return await execRes.json();
    },
    onSuccess: (data) => {
      const result = data.data;
      if (result?.executed) {
        toast.success(`🎯 Trade executed: ${result.direction} ${result.tokenSymbol} at $${result.entryPrice?.toFixed(6)}`);
        setExecutionStatuses(prev => [...prev, {
          type: 'success',
          message: `Trade executed: ${result.direction} ${result.tokenSymbol} at $${result.entryPrice?.toFixed(6)}`,
          systemId: result.systemId,
          tradeId: result.orderId,
        }]);
      } else {
        toast.info(result?.message || 'Trade request processed');
      }
      queryClient.invalidateQueries({ queryKey: ['open-positions'] });
      queryClient.invalidateQueries({ queryKey: ['trading-systems'] });
    },
    onError: (err) => {
      setExecutionStatuses(prev => [...prev, {
        type: 'error',
        message: err instanceof Error ? err.message : 'Start Trading failed',
      }]);
      toast.error('Failed to start trading');
    },
  });

  // Auto-Evolution control mutation
  const autoEvolutionControlMutation = useMutation({
    mutationFn: async (action: 'start' | 'stop') => {
      if (action === 'start') {
        const res = await fetch('/api/auto-evolution', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            action: 'start',
            totalCycles: autoEvoCycles,
            intervalMs: autoEvoInterval * 1000,
            minSharpeRatio: 0.5,
            minWinRate: 0.4,
            positionSizeUsd: 100,
            enableTrailingStop: true,
            enableTimeBasedExit: true,
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || `Auto-evolution start failed`);
        }
        return await res.json();
      } else {
        const res = await fetch('/api/auto-evolution', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'stop' }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || `Auto-evolution stop failed`);
        }
        return await res.json();
      }
    },
    onSuccess: (data) => {
      const result = data.data;
      if (result.status === 'started') {
        setAutoEvolveRunning(true);
        toast.success(`🧬 Auto-evolution started: cycles every ${result.config?.intervalMs ? result.config.intervalMs / 1000 : 300}s`);
        setExecutionStatuses(prev => [...prev, {
          type: 'success',
          message: 'Auto-evolution loop started — strategies will auto-trade when quality thresholds are met',
        }]);
      } else {
        setAutoEvolveRunning(false);
        toast.info('Auto-evolution loop stopped');
        setExecutionStatuses(prev => [...prev, {
          type: 'idle',
          message: 'Auto-evolution loop stopped',
        }]);
      }
      queryClient.invalidateQueries({ queryKey: ['auto-evolution-status'] });
    },
    onError: (err) => {
      setExecutionStatuses(prev => [...prev, {
        type: 'error',
        message: err instanceof Error ? err.message : 'Auto-evolution control failed',
      }]);
      toast.error('Failed to control auto-evolution');
    },
  });

  // Auto-Evolution status query
  const { data: autoEvoStatus, refetch: refetchAutoEvo } = useQuery({
    queryKey: ['auto-evolution-status'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/auto-evolution');
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as {
          isRunning: boolean;
          currentCycle: number;
          totalCycles: number;
          currentPhase: string;
          progress: string;
          stopRequested: boolean;
          lastError: string | null;
          startedAt: string | null;
          lastCycleAt: string | null;
          config: {
            totalCycles: number;
            intervalMs: number;
            minSharpeRatio: number;
            minWinRate: number;
          } | null;
          activeStrategies: string[];
          totalPaperTrades: number;
          totalExitsProcessed: number;
          totalEvolutions: number;
          runId: string | null;
          bestResultsSoFar: Array<{
            id: string;
            strategyName: string;
            category: string;
            timeframe: string;
            score: number;
            sharpeRatio: number;
            winRate: number;
            pnlPct: number;
            totalTrades: number;
          }>;
          completedCycles: Array<{
            cycleNumber: number;
            status: string;
            currentPhase: string;
            bestScore: number;
            improvedCount: number;
            degradedCount: number;
            totalMutations: number;
            strategiesActivated: string[];
            durationMs: number;
            startedAt: string;
            completedAt: string | null;
          }>;
          dbCycleHistory?: Array<{
            cycleNumber: number;
            runId: string;
            status: string;
            currentPhase: string;
            bestScore: number;
            improvedCount: number;
            degradedCount: number;
            totalMutations: number;
            strategiesActivated: string[];
            entriesExecuted: string[];
            exitsProcessed: Array<{ backtestId: string; exitReason: string; pnlUsd: number }>;
            errors: string[];
            durationMs: number;
            startedAt: string;
            completedAt: string | null;
          }>;
        } | null;
      } catch {
        return null;
      }
    },
    staleTime: 5000,
    refetchInterval: autoEvolveRunning ? 5000 : 30000,
  });

  // Sync autoEvolveRunning with the server status
  useEffect(() => {
    if (autoEvoStatus?.isRunning !== undefined) {
      setAutoEvolveRunning(autoEvoStatus.isRunning);
    }
  }, [autoEvoStatus?.isRunning]);

  // Queries
  const { data: scanData, isLoading: scanLoading, refetch: refetchScan } = useQuery({
    queryKey: ['strategy-optimizer-scan'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/strategy-optimizer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'scan' }),
        });
        if (!res.ok) throw new Error('Scan failed');
        const json = await res.json();
        return json.data as { tokens: TokenCandidate[]; dnaProfiles: number; activeSignals: number; lifecyclePhases: Record<string, number>; behaviorModels: number } | null;
      } catch {
        return null;
      }
    },
    enabled: false,
  });

  const { data: rankData, isLoading: rankLoading, refetch: refetchRank } = useQuery({
    queryKey: ['strategy-optimizer-rank', filterRecent],
    queryFn: async () => {
      try {
        const res = await fetch('/api/strategy-optimizer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'rank_results', sortBy: 'score', recentHours: filterRecent }),
        });
        if (!res.ok) throw new Error('Rank failed');
        const json = await res.json();
        return json.data as { results: RankResult[]; totalRanked: number } | null;
      } catch {
        return null;
      }
    },
    enabled: true,
    staleTime: 15000,
    refetchInterval: 60000, // Auto-refresh every 60s
  });

  const { data: bestData } = useQuery({
    queryKey: ['strategy-optimizer-best'],
    queryFn: async () => {
      try {
        const res = await fetch('/api/strategy-optimizer/best');
        if (!res.ok) return [];
        const json = await res.json();
        return (json.data || []) as BestStrategy[];
      } catch {
        return [];
      }
    },
    staleTime: 10000,
  });

  // Expanded backtest detail query
  const { data: expandedBacktestDetail, isLoading: detailLoading } = useQuery({
    queryKey: ['ai-manager-backtest-detail', expandedBacktestId],
    queryFn: async () => {
      if (!expandedBacktestId) return null;
      try {
        const res = await fetch(`/api/backtest/${expandedBacktestId}`);
        if (!res.ok) return null;
        const json = await res.json();
        return json.data as {
          id: string;
          status: string;
          totalPnlPct: number;
          totalPnl: number;
          sharpeRatio: number;
          sortinoRatio: number;
          calmarRatio: number;
          recoveryFactor: number;
          winRate: number;
          maxDrawdownPct: number;
          profitFactor: number;
          totalTrades: number;
          avgHoldTimeMin: number;
          initialCapital: number;
          finalCapital: number;
          operations?: Array<{
            id: string;
            tokenSymbol: string;
            tokenAddress: string;
            operationType: string;
            entryPrice: number;
            exitPrice: number | null;
            entryTime: string;
            exitTime: string | null;
            pnlUsd: number | null;
            pnlPct: number | null;
            holdTimeMin: number | null;
            exitReason: string | null;
            quantity: number;
            positionSizeUsd: number;
          }>;
        } | null;
      } catch {
        return null;
      }
    },
    enabled: !!expandedBacktestId,
    staleTime: 30000,
  });

  // Polling for running backtests
  const { data: backtestStatus } = useQuery({
    queryKey: ['ai-manager-backtest-status', loopResults.map(r => r.backtestId).join(',')],
    queryFn: async () => {
      const ids = loopResults.filter(r => r.backtestId && r.status !== 'completed' && r.status !== 'failed').map(r => r.backtestId!);
      if (ids.length === 0) return [];

      const results = await Promise.allSettled(
        ids.map(async (id) => {
          const res = await fetch(`/api/backtest/${id}`);
          if (!res.ok) return null;
          const json = await res.json();
          return json.data;
        })
      );
      return results.filter(r => r.status === 'fulfilled' && r.value).map(r => (r as PromiseFulfilledResult<Record<string, unknown>>).value);
    },
    enabled: loopResults.some(r => r.status === 'running' || r.status === 'created'),
    refetchInterval: 5000,
  });

  // When backtest status updates, patch loopResults and auto-trigger ranking
  useEffect(() => {
    if (!backtestStatus || !Array.isArray(backtestStatus) || backtestStatus.length === 0) return;

    let hasNewlyCompleted = false;
    setLoopResults(prev => {
      let updated = prev;
      backtestStatus.forEach((bt: Record<string, unknown>) => {
        const btId = bt.id as string;
        const btStatus = (bt.status as string || '').toLowerCase();
        if (btStatus === 'completed' || btStatus === 'failed') {
          const idx = updated.findIndex(r => r.backtestId === btId);
          if (idx !== -1 && updated[idx].status !== 'completed' && updated[idx].status !== 'failed') {
            hasNewlyCompleted = true;
            updated = updated.map(r =>
              r.backtestId === btId ? { ...r, status: btStatus } : r
            );
          }
        }
      });
      return updated;
    });

    if (hasNewlyCompleted) {
      refetchRank();
    }
  }, [backtestStatus, refetchRank]);

  // Mutations
  const generateMutation = useMutation({
    mutationFn: async (options?: { scannedTokens?: TokenCandidate[] }) => {
      // Extract chains from scanned tokens for better targeting
      const scannedChains = options?.scannedTokens
        ? [...new Set(options.scannedTokens.map(t => t.chain).filter(Boolean))] as string[]
        : undefined;

      const res = await fetch('/api/strategy-optimizer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'generate_strategies',
          capital,
          timeframes: selectedTimeframes,
          tokenAges: selectedTokenAges,
          riskTolerance,
          allocationMode,
          strategyCount,
          scannedChains,
          scannedTokenCount: options?.scannedTokens?.length || 0,
          // B4 FIX: Pass actual scanned tokens so strategies are built around real data
          scannedTokens: options?.scannedTokens || [],
        }),
      });
      if (!res.ok) throw new Error('Generate failed');
      return res.json();
    },
    onSuccess: (data) => {
      const strategies = (data.data?.strategies || []) as StrategyConfig[];
      setGeneratedStrategies(strategies);
      // Auto-select all strategies
      setSelectedStrategyIds(new Set(strategies.map(s => s.id)));
      toast.success(`Generated ${strategies.length} strategies`);
    },
    onError: () => {
      toast.error('Failed to generate strategies');
    },
  });

  const runLoopMutation = useMutation({
    mutationFn: async () => {
      // Only run selected strategies
      const strategiesToRun = generatedStrategies.filter(s => selectedStrategyIds.has(s.id));
      if (strategiesToRun.length === 0) {
        throw new Error('No strategies selected');
      }
      const res = await fetch('/api/strategy-optimizer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'run_loop',
          strategies: strategiesToRun,
          capital,
        }),
      });
      if (!res.ok) throw new Error('Run loop failed');
      return res.json();
    },
    onSuccess: (data) => {
      const results = data.data?.results || [];
      setLoopResults(results);
      toast.success(`Created ${results.filter((r: { backtestId: string | null }) => r.backtestId).length} backtests`);
      setCurrentStep('results');
      refetchRank();
      setAutoRunning(false);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Failed to run optimization loop');
      setCurrentStep('results');
      setAutoRunning(false);
    },
  });

  const saveBestMutation = useMutation({
    mutationFn: async (strategy: RankResult) => {
      const res = await fetch('/api/strategy-optimizer/best', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy }),
      });
      if (!res.ok) throw new Error('Save failed');
      return res.json();
    },
    onSuccess: () => {
      toast.success('Strategy saved to Hall of Fame');
      queryClient.invalidateQueries({ queryKey: ['strategy-optimizer-best'] });
    },
    onError: () => {
      toast.error('Failed to save strategy');
    },
  });

  const deleteBestMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch('/api/strategy-optimizer/best', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      if (!res.ok) throw new Error('Delete failed');
      return res.json();
    },
    onSuccess: () => {
      toast.success('Strategy removed');
      queryClient.invalidateQueries({ queryKey: ['strategy-optimizer-best'] });
    },
  });

  const activateMutation = useMutation({
    mutationFn: async (strategy: BestStrategy) => {
      // Step 1: Create the trading system
      const createRes = await fetch(`/api/trading-systems`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: `[AI] ${strategy.strategyName}`,
          category: strategy.category,
          config: {
            assetFilter: { minLiquidity: 50000, minVolume24h: 10000, maxMarketCap: 10000000, chains: ['SOL', 'ETH', 'BASE'], tokenAge: strategy.tokenAgeCategory === 'NEW' ? '<7D' : strategy.tokenAgeCategory === 'MEDIUM' ? '7-30D' : '>30D' },
            phaseConfig: { genesis: strategy.tokenAgeCategory === 'NEW', early: true, growth: true, maturity: strategy.tokenAgeCategory === 'OLD', decline: false },
            entrySignal: { signalType: 'SMART_MONEY_ENTRY', confidenceThreshold: 70, confirmationRequired: true, timeWindow: 15 },
            execution: { orderType: 'LIMIT', slippageTolerance: 1.5, maxPositionSize: strategy.capitalAllocation > 0 ? Math.round(strategy.capitalAllocation * 100 / 10000) : 5, executionDelay: 0 },
            exitSignal: { takeProfit: strategy.pnlPct > 0 ? Math.round(strategy.pnlPct * 0.6) : 25, stopLoss: strategy.maxDrawdownPct > 0 ? Math.round(strategy.maxDrawdownPct * 0.5) : 10, trailingStop: true, trailingStopPercent: 10, timeBasedExit: 1440 },
            riskManagement: { maxDrawdown: strategy.maxDrawdownPct || 20, maxConcurrentTrades: 3, maxDailyLoss: 5, positionSizing: 'RISK_BASED' },
            capitalAllocation: { method: 'risk_parity', percentage: Math.round(strategy.capitalAllocation / 100), maxAllocation: 2, rebalanceFrequency: 'DAILY' },
            bigDataContext: { whaleTracking: true, smartMoneyMirror: true, botDetection: true, onChainMetrics: true, socialSentiment: false },
          },
          primaryTimeframe: strategy.timeframe || '15m',
          maxPositionPct: strategy.capitalAllocation > 0 ? Math.round(strategy.capitalAllocation * 100 / 10000) : 5,
          stopLossPct: strategy.maxDrawdownPct > 0 ? Math.round(strategy.maxDrawdownPct * 0.5) : 10,
          takeProfitPct: strategy.pnlPct > 0 ? Math.round(strategy.pnlPct * 0.6) : 25,
        }),
      });
      if (!createRes.ok) throw new Error('Failed to create trading system');
      const createData = await createRes.json();
      const systemId = createData.data?.id || createData.id;

      // Step 2: Activate in paper mode
      const activateRes = await fetch(`/api/trading-systems/${systemId}/activate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'paper' }),
      });
      if (!activateRes.ok) throw new Error('Failed to activate trading system');

      // Step 3: Trigger actual trade execution via the autonomous execution engine
      let executionResult = null;
      try {
        const execRes = await fetch('/api/execution/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            systemId,
            direction: 'LONG',
            positionSizeUsd: Math.min(strategy.capitalAllocation || 100, 100),
          }),
        });
        if (execRes.ok) {
          const execData = await execRes.json();
          executionResult = execData.data;
        } else {
          console.warn('[Activate] Execution start failed, system is still activated:', await execRes.text());
        }
      } catch (execErr) {
        console.warn('[Activate] Execution start error, system is still activated:', execErr);
      }

      const activateData = await activateRes.json();
      return { ...activateData, executionResult, systemId };
    },
    onSuccess: (data) => {
      if (data.executionResult?.executed) {
        toast.success(`Strategy activated & trade executed: ${data.executionResult.direction} ${data.executionResult.tokenSymbol} at $${data.executionResult.entryPrice?.toFixed(6)}`);
        setExecutionStatuses(prev => [...prev, {
          type: 'success',
          message: `Activated + executed: ${data.executionResult.direction} ${data.executionResult.tokenSymbol}`,
          systemId: data.systemId,
          tradeId: data.executionResult.orderId,
        }]);
      } else {
        toast.success('Strategy activated in Paper Trading mode (execution pending)');
      }
      queryClient.invalidateQueries({ queryKey: ['trading-systems'] });
      queryClient.invalidateQueries({ queryKey: ['open-positions'] });
    },
    onError: () => {
      toast.error('Failed to activate strategy');
    },
  });

  // Activate top N strategies at once (with auto-execution)
  const activateAllMutation = useMutation({
    mutationFn: async ({ strategies, count }: { strategies: BestStrategy[]; count: number }) => {
      const toActivate = strategies.slice(0, count);
      const results = await Promise.allSettled(
        toActivate.map(async (strategy) => {
          // Step 1: Create the trading system
          const createRes = await fetch(`/api/trading-systems`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              name: `[AI] ${strategy.strategyName}`,
              category: strategy.category,
              config: {
                assetFilter: { minLiquidity: 50000, minVolume24h: 10000, maxMarketCap: 10000000, chains: ['SOL', 'ETH', 'BASE'], tokenAge: strategy.tokenAgeCategory === 'NEW' ? '<7D' : strategy.tokenAgeCategory === 'MEDIUM' ? '7-30D' : '>30D' },
                phaseConfig: { genesis: strategy.tokenAgeCategory === 'NEW', early: true, growth: true, maturity: strategy.tokenAgeCategory === 'OLD', decline: false },
                entrySignal: { signalType: 'SMART_MONEY_ENTRY', confidenceThreshold: 70, confirmationRequired: true, timeWindow: 15 },
                execution: { orderType: 'LIMIT', slippageTolerance: 1.5, maxPositionSize: strategy.capitalAllocation > 0 ? Math.round(strategy.capitalAllocation * 100 / 10000) : 5, executionDelay: 0 },
                exitSignal: { takeProfit: strategy.pnlPct > 0 ? Math.round(strategy.pnlPct * 0.6) : 25, stopLoss: strategy.maxDrawdownPct > 0 ? Math.round(strategy.maxDrawdownPct * 0.5) : 10, trailingStop: true, trailingStopPercent: 10, timeBasedExit: 1440 },
                riskManagement: { maxDrawdown: strategy.maxDrawdownPct || 20, maxConcurrentTrades: 3, maxDailyLoss: 5, positionSizing: 'RISK_BASED' },
                capitalAllocation: { method: 'risk_parity', percentage: Math.round(strategy.capitalAllocation / 100), maxAllocation: 2, rebalanceFrequency: 'DAILY' },
                bigDataContext: { whaleTracking: true, smartMoneyMirror: true, botDetection: true, onChainMetrics: true, socialSentiment: false },
              },
              primaryTimeframe: strategy.timeframe || '15m',
              maxPositionPct: strategy.capitalAllocation > 0 ? Math.round(strategy.capitalAllocation * 100 / 10000) : 5,
              stopLossPct: strategy.maxDrawdownPct > 0 ? Math.round(strategy.maxDrawdownPct * 0.5) : 10,
              takeProfitPct: strategy.pnlPct > 0 ? Math.round(strategy.pnlPct * 0.6) : 25,
            }),
          });
          if (!createRes.ok) throw new Error(`Failed to create ${strategy.strategyName}`);
          const createData = await createRes.json();
          const systemId = createData.data?.id || createData.id;

          // Step 2: Activate in paper mode
          const activateRes = await fetch(`/api/trading-systems/${systemId}/activate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'paper' }),
          });
          if (!activateRes.ok) throw new Error(`Failed to activate ${strategy.strategyName}`);

          // Step 3: Trigger trade execution
          try {
            await fetch('/api/execution/start', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                systemId,
                direction: 'LONG',
                positionSizeUsd: Math.min(strategy.capitalAllocation || 100, 100),
              }),
            });
          } catch {
            // Execution failure doesn't fail the activation
          }

          return await activateRes.json();
        })
      );
      const succeeded = results.filter(r => r.status === 'fulfilled').length;
      return { succeeded, total: toActivate.length };
    },
    onSuccess: ({ succeeded, total }) => {
      toast.success(`🚀 Activated & executed ${succeeded}/${total} strategies!`);
      queryClient.invalidateQueries({ queryKey: ['trading-systems'] });
      queryClient.invalidateQueries({ queryKey: ['open-positions'] });
    },
    onError: () => {
      toast.error('Failed to activate strategies');
    },
  });

  // Handlers
  const handleScan = useCallback(() => {
    setCurrentStep('scanning');
    refetchScan();
  }, [refetchScan]);

  const handleGenerate = useCallback(() => {
    setCurrentStep('generating');
    // B4 FIX: Pass scanned tokens from the scan step so strategies are built around real data
    const scannedTokens = scanData?.tokens || [];
    generateMutation.mutate({ scannedTokens });
  }, [generateMutation, scanData]);

  const handleRunLoop = useCallback(() => {
    if (selectedStrategyIds.size === 0) {
      toast.error('Select at least one strategy to backtest');
      return;
    }
    setCurrentStep('running');
    runLoopMutation.mutate();
  }, [runLoopMutation, selectedStrategyIds]);

  const handleQuickStart = useCallback(() => {
    setCapital(10000);
    setAllocationMode('distribute');
    setSelectedTimeframes(['5m', '15m', '1h']);
    setSelectedTokenAges(['NEW', 'MEDIUM']);
    setRiskTolerance('MODERATE');
    setStrategyCount(6);
    setCurrentStep('scanning');
    // Quick start triggers scan first, then generate will be called with scan results
    refetchScan();
  }, [refetchScan]);

  // Auto-run full pipeline
  const handleAutoRunPipeline = useCallback(async () => {
    setAutoRunning(true);
    setAutoRunPhase('Checking data quality...');

    try {
      // Step 0: Pre-flight data quality check
      try {
        const dqRes = await fetch('/api/data-quality');
        if (dqRes.ok) {
          const dqJson = await dqRes.json();
          const dq = dqJson.data as { qualityLevel: string; tokensWithEnoughCandles: number; totalTokens: number; recommendations: string[] } | null;
          if (dq && (dq.qualityLevel === 'critical' || dq.qualityLevel === 'poor')) {
            const proceed = window.confirm(
              `⚠️ Data quality is ${dq.qualityLevel.toUpperCase()} (${dq.tokensWithEnoughCandles}/${dq.totalTokens} tokens ready).\n` +
              `Backtests may fail or produce unreliable results.\n\n` +
              (dq.recommendations.length > 0 ? dq.recommendations.slice(0, 2).join('\n') + '\n\n' : '') +
              `Proceed anyway?`
            );
            if (!proceed) {
              setAutoRunning(false);
              setAutoRunPhase('');
              toast.info('Pipeline cancelled — backfill data first');
              return;
            }
          }
        }
      } catch {
        // Data quality check is optional — continue pipeline if it fails
      }

      // Step 1: Scan
      setAutoRunPhase('Scanning market...');
      setCurrentStep('scanning');
      const scanResult = await refetchScan();
      setAutoRunPhase('Generating strategies...');

      // Step 2: Generate — pass scanned tokens as context
      setCurrentStep('generating');
      const scannedTokens = (scanResult?.data as { tokens: TokenCandidate[] } | null)?.tokens || [];
      const genResult = await generateMutation.mutateAsync({ scannedTokens });
      const strategies = (genResult.data?.strategies || []) as StrategyConfig[];
      if (strategies.length === 0) {
        toast.error('No strategies generated');
        setAutoRunning(false);
        setAutoRunPhase('');
        return;
      }
      setSelectedStrategyIds(new Set(strategies.map(s => s.id)));
      setAutoRunPhase('Running backtests...');

      // Step 3: Backtest
      setCurrentStep('running');
      await runLoopMutation.mutateAsync();
      setAutoRunPhase('Ranking results...');

      // Step 4: Rank
      setCurrentStep('results');
      await refetchRank();
      setAutoRunPhase('');

      toast.success('🚀 Pipeline complete!');
    } catch (error) {
      const failedStep = currentStep || 'unknown';
      toast.error(`Pipeline failed at ${failedStep}: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setAutoRunning(false);
      setAutoRunPhase('');
    }
  }, [refetchScan, generateMutation, runLoopMutation, refetchRank, currentStep]);

  // Evolve top strategies mutation
  const evolveMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/strategy-optimizer/evolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'evolve',
          maxIterations: 3,
          improvementThreshold: 2,
          mutationRate: 0.3,
          topN: 3,
          capital,
        }),
      });
      if (!res.ok) throw new Error('Evolution failed');
      return res.json();
    },
    onSuccess: (data) => {
      const result = data.data;
      toast.success(`🧬 Evolution complete: ${result.improved} improved, ${result.degraded} degraded out of ${result.totalMutations} mutations`);
      queryClient.invalidateQueries({ queryKey: ['backtests'] });
      queryClient.invalidateQueries({ queryKey: ['strategy-optimizer-rank'] });
      refetchRank();
    },
    onError: () => {
      toast.error('Evolution failed. Run the pipeline first to have strategies to evolve.');
    },
  });

  const handleEvolve = useCallback(() => {
    evolveMutation.mutate();
  }, [evolveMutation]);

  // ---- Auto-Trade Mutation (entry) ----
  const autoTradeMutation = useMutation({
    mutationFn: async (params: { systemId: string; tokenAddress: string; direction: 'LONG' | 'SHORT'; positionSizeUsd: number }) => {
      const res = await fetch('/api/execution/auto-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Auto-trade failed');
      }
      return res.json();
    },
    onSuccess: (data) => {
      const result = data.data;
      setExecutionStatuses(prev => [...prev, {
        type: 'success',
        message: result.message,
        tradeId: result.tradeId,
        systemId: result.systemId,
      }]);
      toast.success(`✅ Entry executed: ${result.direction} ${result.tokenSymbol} at $${result.entryPrice?.toFixed(6)}`);
      queryClient.invalidateQueries({ queryKey: ['open-positions'] });
    },
    onError: (err) => {
      setExecutionStatuses(prev => [...prev, {
        type: 'error',
        message: err instanceof Error ? err.message : 'Auto-trade failed',
      }]);
      toast.error('Auto-trade execution failed');
    },
  });

  // ---- Auto-Exit Mutation ----
  const autoExitMutation = useMutation({
    mutationFn: async (params: { backtestId: string; exitPrice?: number; exitReason?: string }) => {
      const res = await fetch('/api/execution/auto-exit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Auto-exit failed');
      }
      return res.json();
    },
    onSuccess: (data) => {
      const result = data.data;
      setExecutionStatuses(prev => [...prev, {
        type: 'success',
        message: result.message,
        pnlUsd: result.pnlUsd,
        pnlPct: result.pnlPct,
      }]);
      toast.success(`📊 Exit executed: PnL ${result.pnlUsd >= 0 ? '+' : ''}$${result.pnlUsd?.toFixed(2)}`);
      queryClient.invalidateQueries({ queryKey: ['open-positions'] });
    },
    onError: (err) => {
      setExecutionStatuses(prev => [...prev, {
        type: 'error',
        message: err instanceof Error ? err.message : 'Auto-exit failed',
      }]);
      toast.error('Auto-exit failed');
    },
  });

  // ---- Auto-Evolve & Execute Mutation ----
  const autoEvolveExecuteMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/strategy-optimizer/evolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'auto_evolve',
          maxIterations: 3,
          improvementThreshold: 2,
          mutationRate: 0.3,
          topN: 3,
          capital,
          autoActivate: true,
          autoExecute: true,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Auto-evolve failed');
      }
      return res.json();
    },
    onSuccess: (data) => {
      const result = data.data;
      setAutoEvolveRunning(false);
      const status: ExecutionStatus = {
        type: 'success',
        message: result.message,
      };
      if (result.activatedSystemId) status.systemId = result.activatedSystemId;
      if (result.executedTradeId) status.tradeId = result.executedTradeId;
      setExecutionStatuses(prev => [...prev, status]);
      toast.success(`🧬🚀 ${result.message}`);
      queryClient.invalidateQueries({ queryKey: ['backtests'] });
      queryClient.invalidateQueries({ queryKey: ['strategy-optimizer-rank'] });
      queryClient.invalidateQueries({ queryKey: ['open-positions'] });
      queryClient.invalidateQueries({ queryKey: ['trading-systems'] });
      refetchRank();
    },
    onError: (err) => {
      setAutoEvolveRunning(false);
      setExecutionStatuses(prev => [...prev, {
        type: 'error',
        message: err instanceof Error ? err.message : 'Auto-evolve & execute failed',
      }]);
      toast.error('Auto-evolve & execute failed');
    },
  });

  // ---- Auto-Evolution Start/Stop Mutations (dedicated panel controls) ----
  const autoEvoStartMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/auto-evolution', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'start',
          totalCycles: autoEvoCycles,
          intervalMs: autoEvoInterval * 1000,
          minSharpeRatio: 0.5,
          minWinRate: 0.4,
          positionSizeUsd: 100,
          enableTrailingStop: true,
          enableTimeBasedExit: true,
          maxHoldTimeMin: 1440,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to start auto-evolution');
      }
      return res.json();
    },
    onSuccess: () => {
      setAutoEvolveRunning(true);
      toast.success(`🧬 Auto-Evolution started: ${autoEvoCycles} cycles, ${autoEvoInterval}s interval`);
      refetchAutoEvo();
    },
    onError: (err) => {
      toast.error(`Auto-Evolution start failed: ${err instanceof Error ? err.message : 'Unknown'}`);
    },
  });

  const autoEvoStopMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/auto-evolution', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'stop' }),
      });
      if (!res.ok) throw new Error('Failed to stop auto-evolution');
      return res.json();
    },
    onSuccess: () => {
      setAutoEvolveRunning(false);
      toast.success('Auto-Evolution stopping after current cycle...');
      refetchAutoEvo();
    },
    onError: () => {
      toast.error('Failed to stop auto-evolution');
    },
  });

  // ---- Run Single Evolution Cycle Mutation ----
  const autoEvoSingleCycleMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/auto-evolution', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'run_single_cycle',
          minSharpeRatio: 0.5,
          minWinRate: 0.4,
          positionSizeUsd: 100,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to run single cycle');
      }
      return res.json();
    },
    onSuccess: (data) => {
      const result = data?.data?.cycleResult;
      if (result) {
        toast.success(`🧬 Single cycle complete: score=${(result.evolutionResult?.bestScore ?? 0).toFixed(1)}, +${result.evolutionResult?.improved ?? 0} improved, -${result.evolutionResult?.degraded ?? 0} degraded`);
      } else {
        toast.success('🧬 Single cycle completed');
      }
      refetchAutoEvo();
    },
    onError: (err) => {
      toast.error(`Single cycle failed: ${err instanceof Error ? err.message : 'Unknown'}`);
    },
  });

  // ---- Run Full Pipeline Mutation (Scan → Generate → Backtest → Evolve) ----
  const autoEvoFullPipelineMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/auto-evolution', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'run_full_pipeline',
          minSharpeRatio: 0.5,
          minWinRate: 0.4,
          positionSizeUsd: 100,
          evolutionConfig: {
            capital: capital,
          },
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to run full pipeline');
      }
      return res.json();
    },
    onSuccess: (data) => {
      const result = data?.data?.cycleResult;
      if (result) {
        const evo = result.evolutionResult;
        toast.success(`🧬 Full pipeline complete: score=${(evo?.bestScore ?? 0).toFixed(1)}, +${evo?.improved ?? 0} improved, ${result.strategiesActivated?.length ?? 0} activated`);
      } else {
        toast.success('🧬 Full pipeline cycle completed');
      }
      refetchAutoEvo();
      queryClient.invalidateQueries({ queryKey: ['strategy-optimizer-rank'] });
      queryClient.invalidateQueries({ queryKey: ['best-strategies'] });
    },
    onError: (err) => {
      toast.error(`Full pipeline failed: ${err instanceof Error ? err.message : 'Unknown'}`);
    },
  });

  // ---- Run Next Discrete Cycle Mutation (P2-A: frontend-driven) ----
  const runNextCycleMutation = useMutation({
    mutationFn: async () => {
      cycleInProgressRef.current = true;
      const res = await fetch('/api/auto-evolution', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'run_next_cycle',
          minSharpeRatio: 0.5,
          minWinRate: 0.4,
          positionSizeUsd: 100,
          bootstrap: true,
          enableTrailingStop: true,
          enableTimeBasedExit: true,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to run next cycle');
      }
      return res.json();
    },
    onSuccess: (data) => {
      cycleInProgressRef.current = false;
      const result = data?.data;
      if (result?.status === 'already_running') {
        // Cycle was already in progress, just skip — but tick to re-evaluate
        setAutoScheduleTick(t => t + 1);
        return;
      }
      const cycleResult = result?.cycleResult;
      const evo = cycleResult?.evolutionResult;
      if (result?.wasBootstrap) {
        toast.success(`🚀 Bootstrap cycle completed: score=${(evo?.bestScore ?? 0).toFixed(1)}, +${evo?.improved ?? 0} improved`);
      } else if (cycleResult) {
        toast.success(`🧬 Cycle #${autoScheduleCyclesRun + 1} complete: score=${(evo?.bestScore ?? 0).toFixed(1)}, +${evo?.improved ?? 0} improved, -${evo?.degraded ?? 0} degraded`);
      } else {
        toast.success('🧬 Evolution cycle completed');
      }
      setAutoScheduleCyclesRun(prev => prev + 1);
      refetchAutoEvo();
      queryClient.invalidateQueries({ queryKey: ['strategy-optimizer-rank'] });
      queryClient.invalidateQueries({ queryKey: ['best-strategies'] });
    },
    onError: (err) => {
      cycleInProgressRef.current = false;
      setAutoScheduleTick(t => t + 1);
      // Don't toast errors from auto-schedule (they're expected occasionally)
      console.warn('[AutoSchedule] Cycle failed:', err instanceof Error ? err.message : 'Unknown');
    },
  });

  // ---- Frontend-Driven Auto-Schedule (P2-A) ----
  // When autoScheduleEnabled is true, this useEffect drives discrete cycles:
  // 1. After a cycle completes, wait autoScheduleInterval seconds
  // 2. Trigger run_next_cycle
  // 3. Repeat until disabled or totalCycles reached
  //
  // RACE CONDITION FIX: We use cycleInProgressRef instead of depending on
  // runNextCycleMutation.isPending in the dependency array. This prevents
  // the effect from re-running when isPending flips, which could cause
  // a double-trigger if autoEvolveRunning hasn't synced yet from the server.
  useEffect(() => {
    if (!autoScheduleEnabled) {
      setAutoScheduleCountdown(0);
      return;
    }

    // Check if we've hit the cycle limit (0 = infinite)
    if (autoScheduleTotalCycles > 0 && autoScheduleCyclesRun >= autoScheduleTotalCycles) {
      setAutoScheduleEnabled(false);
      toast.success(`🏁 Auto-schedule completed: ${autoScheduleCyclesRun} cycles run`);
      return;
    }

    // If a cycle is currently running (locally or on server), don't start countdown yet
    if (cycleInProgressRef.current || autoEvolveRunning) {
      return;
    }

    // Start countdown
    setAutoScheduleCountdown(autoScheduleInterval);

    const countdownTimer = setInterval(() => {
      setAutoScheduleCountdown(prev => {
        if (prev <= 1) {
          clearInterval(countdownTimer);
          // Trigger next cycle
          runNextCycleMutation.mutate();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(countdownTimer);
  }, [autoScheduleEnabled, autoScheduleInterval, autoScheduleCyclesRun, autoScheduleTotalCycles, autoEvolveRunning, autoScheduleTick]);

  // Reset cyclesRun when auto-schedule is disabled
  useEffect(() => {
    if (!autoScheduleEnabled) {
      // Keep cyclesRun for display but reset countdown
      setAutoScheduleCountdown(0);
    }
  }, [autoScheduleEnabled]);

  // ---- Open Positions Query ----
  const { data: openPositionsData, refetch: refetchOpenPositions } = useQuery({
    queryKey: ['open-positions'],
    queryFn: async () => {
      try {
        // Try the execution positions endpoint first
        const res = await fetch('/api/execution/positions');
        if (res.ok) {
          const json = await res.json();
          if (json.data && Array.isArray(json.data) && json.data.length > 0) {
            return json.data as OpenPosition[];
          }
        }
        // Fallback to the strategy-optimizer/evolve endpoint
        const fallbackRes = await fetch('/api/strategy-optimizer/evolve?type=open_positions');
        if (!fallbackRes.ok) return [];
        const json = await fallbackRes.json();
        return (json.data || []) as OpenPosition[];
      } catch {
        return [];
      }
    },
    staleTime: 15000,
    refetchInterval: 30000,
  });

  const openPositions = openPositionsData || [];

  const tokens = scanData?.tokens || [];
  const rankedResults = rankData?.results || [];
  const bestStrategies = Array.isArray(bestData) ? bestData : [];

  // Filter ranked results (moved before handleExecuteTopStrategies to avoid used-before-declaration error)
  const filteredRankedResults = rankedResults.filter(item => {
    if (filterTimeframe !== 'ALL' && item.timeframe !== filterTimeframe) return false;
    if (filterTokenAge !== 'ALL' && item.tokenAgeCategory !== filterTokenAge) return false;
    if (filterRisk !== 'ALL' && item.riskTolerance !== filterRisk) return false;
    return true;
  });

  // ---- Execute a single ranked strategy ----
  const handleExecuteSingleStrategy = useCallback(async (result: RankResult) => {
    setExecutionStatuses(prev => [...prev, {
      type: 'executing',
      message: `Executing: ${result.strategyName}...`,
    }]);

    try {
      const systemId = result.id || result.backtestId;

      const execRes = await fetch('/api/execution/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          systemId,
          direction: 'LONG',
          positionSizeUsd: Math.min(result.capitalAllocation, capital * 0.1),
        }),
      });

      if (execRes.ok) {
        const execData = await execRes.json();
        if (execData.data?.executed) {
          setExecutionStatuses(prev => [...prev, {
            type: 'success',
            message: `Executed: ${execData.data.direction} ${execData.data.tokenSymbol} at $${execData.data.entryPrice?.toFixed(6)}`,
            tradeId: execData.data.orderId,
            systemId: execData.data.systemId,
          }]);
          toast.success(`Trade executed: ${result.strategyName}`);
        } else {
          setExecutionStatuses(prev => [...prev, {
            type: 'idle',
            message: execData.data?.message || `No token found for ${result.strategyName}`,
          }]);
        }
      } else {
        // Fallback: try auto-trade with discovered token from backtest history
        const btRes = await fetch(`/api/backtest/${result.backtestId}`);
        if (btRes.ok) {
          const btData = await btRes.json();
          const btInfo = btData.data;

          const opsRes = await fetch(`/api/strategy-optimizer/evolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'trade_history', systemId: btInfo?.systemId, limit: 5 }),
          });

          let tokenAddress = '';
          if (opsRes.ok) {
            const opsData = await opsRes.json();
            const ops = opsData.data || [];
            if (ops.length > 0) {
              tokenAddress = ops[0].tokenAddress || '';
            }
          }

          if (!tokenAddress) {
            tokenAddress = '0x0000000000000000000000000000000000000001';
          }

          const fallbackSystemId = btInfo?.systemId || btInfo?.id;
          if (fallbackSystemId) {
            await autoTradeMutation.mutateAsync({
              systemId: fallbackSystemId,
              tokenAddress,
              direction: 'LONG',
              positionSizeUsd: Math.min(result.capitalAllocation, capital * 0.1),
            });
            toast.success(`Trade executed (fallback): ${result.strategyName}`);
          }
        } else {
          setExecutionStatuses(prev => [...prev, {
            type: 'error',
            message: `Failed to execute: ${result.strategyName}`,
          }]);
        }
      }
    } catch {
      setExecutionStatuses(prev => [...prev, {
        type: 'error',
        message: `Error executing: ${result.strategyName}`,
      }]);
    }
    refetchOpenPositions();
  }, [capital, autoTradeMutation, refetchOpenPositions]);

  // ---- Execute Top N Strategies ----
  const handleExecuteTopStrategies = useCallback(async (topN: number) => {
    const topResults = filteredRankedResults.slice(0, topN);
    if (topResults.length === 0) {
      toast.error('No ranked strategies available to execute');
      return;
    }

    setExecutionStatuses(prev => [...prev, {
      type: 'executing',
      message: `Executing top ${topN} strategies...`,
    }]);

    let executedCount = 0;
    for (const result of topResults) {
      try {
        // Use /api/execution/start which auto-selects the best token
        const systemId = result.id || result.backtestId;

        const execRes = await fetch('/api/execution/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            systemId,
            direction: 'LONG',
            positionSizeUsd: Math.min(result.capitalAllocation, capital * 0.1),
          }),
        });

        if (execRes.ok) {
          const execData = await execRes.json();
          if (execData.data?.executed) {
            executedCount++;
            setExecutionStatuses(prev => [...prev, {
              type: 'success',
              message: `Executed: ${execData.data.direction} ${execData.data.tokenSymbol} at $${execData.data.entryPrice?.toFixed(6)}`,
              tradeId: execData.data.orderId,
              systemId: execData.data.systemId,
            }]);
          }
        } else {
          // Fallback: try the auto-trade endpoint with a discovered token
          const btRes = await fetch(`/api/backtest/${result.backtestId}`);
          if (!btRes.ok) continue;
          const btData = await btRes.json();
          const btInfo = btData.data;

          const opsRes = await fetch(`/api/strategy-optimizer/evolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'trade_history', systemId: btInfo?.systemId, limit: 5 }),
          });

          let tokenAddress = '';
          if (opsRes.ok) {
            const opsData = await opsRes.json();
            const ops = opsData.data || [];
            if (ops.length > 0) {
              tokenAddress = ops[0].tokenAddress || '';
            }
          }

          if (!tokenAddress) {
            tokenAddress = '0x0000000000000000000000000000000000000001';
          }

          const fallbackSystemId = btInfo?.systemId || btInfo?.id;
          if (!fallbackSystemId) continue;

          await autoTradeMutation.mutateAsync({
            systemId: fallbackSystemId,
            tokenAddress,
            direction: 'LONG',
            positionSizeUsd: Math.min(result.capitalAllocation, capital * 0.1),
          });
          executedCount++;
        }
      } catch {
        // Skip failed executions
      }
    }

    if (executedCount > 0) {
      toast.success(`Executed entries for ${executedCount}/${topN} strategies`);
    }
    refetchOpenPositions();
  }, [filteredRankedResults, capital, autoTradeMutation, refetchOpenPositions]);

  // ---- Auto-Evolve & Execute handler ----
  const handleAutoEvolveAndExecute = useCallback(() => {
    setAutoEvolveRunning(true);
    setExecutionStatuses(prev => [...prev, {
      type: 'executing',
      message: 'Running auto-evolution + auto-activation + auto-execution...',
    }]);
    autoEvolveExecuteMutation.mutate();
  }, [autoEvolveExecuteMutation]);

  const toggleTimeframe = (tf: string) => {
    setSelectedTimeframes(prev =>
      prev.includes(tf) ? prev.filter(t => t !== tf) : [...prev, tf]
    );
  };

  const toggleTokenAge = (age: string) => {
    setSelectedTokenAges(prev =>
      prev.includes(age) ? prev.filter(a => a !== age) : [...prev, age]
    );
  };

  // Strategy card inline edit handler
  const handleStrategyEdit = useCallback((id: string, field: string, value: number) => {
    setGeneratedStrategies(prev =>
      prev.map(s => {
        if (s.id !== id) return s;
        if (field === 'name') {
          return { ...s, name: String(value) };
        }
        if (field === 'capitalAllocation') {
          return { ...s, capitalAllocation: Number(value) };
        }
        return {
          ...s,
          config: {
            ...s.config,
            exitSignal: {
              ...((s.config?.exitSignal as Record<string, unknown>) || {}),
              ...(field === 'takeProfit' ? { takeProfit: value } : {}),
              ...(field === 'stopLoss' ? { stopLoss: value } : {}),
            },
            riskManagement: {
              ...((s.config?.riskManagement as Record<string, unknown>) || {}),
              ...(field === 'positionSize' ? { maxPositionSize: value } : {}),
            },
          },
        };
      })
    );
  }, []);

  const handleStrategyRemove = useCallback((id: string) => {
    setGeneratedStrategies(prev => prev.filter(s => s.id !== id));
    setSelectedStrategyIds(prev => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const handleToggleSelectStrategy = useCallback((id: string) => {
    setSelectedStrategyIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Select/deselect by category
  const handleSelectByCategory = useCallback((category: string) => {
    if (category === 'all') {
      setSelectedStrategyIds(new Set(generatedStrategies.map(s => s.id)));
    } else {
      setSelectedStrategyIds(new Set(
        generatedStrategies.filter(s => s.category.toLowerCase() === category.toLowerCase()).map(s => s.id)
      ));
    }
  }, [generatedStrategies]);

  // Select/deselect by timeframe
  const handleSelectByTimeframe = useCallback((tf: string) => {
    if (tf === 'all') {
      setSelectedStrategyIds(new Set(generatedStrategies.map(s => s.id)));
    } else {
      setSelectedStrategyIds(new Set(
        generatedStrategies.filter(s => s.timeframe === tf).map(s => s.id)
      ));
    }
  }, [generatedStrategies]);


  const bestBacktestIds = useMemo(
    () => new Set(bestStrategies.map(s => s.backtestId).filter(Boolean)),
    [bestStrategies]
  );

  // rankedResults and filteredRankedResults are defined above (before handleExecuteTopStrategies)

  // Get unique values for filter pills
  const uniqueTimeframes = [...new Set(rankedResults.map(r => r.timeframe))].filter(Boolean);
  const uniqueTokenAges = [...new Set(rankedResults.map(r => r.tokenAgeCategory))].filter(Boolean);
  const uniqueRisks = [...new Set(rankedResults.map(r => r.riskTolerance))].filter(Boolean);

  // Get unique categories and timeframes from generated strategies for grouping
  const uniqueCategories = useMemo(
    () => [...new Set(generatedStrategies.map(s => s.category))].filter(Boolean),
    [generatedStrategies]
  );
  const uniqueStratTimeframes = useMemo(
    () => [...new Set(generatedStrategies.map(s => s.timeframe))].filter(Boolean),
    [generatedStrategies]
  );

  // Determine strategy status based on loopResults
  const getStrategyStatus = (strategyId: string): 'pending' | 'testing' | 'done' | 'failed' => {
    const result = loopResults.find(r => r.strategyId === strategyId);
    if (!result) return 'pending';
    if (result.status === 'completed' || result.status === 'running') return 'done';
    if (result.status === 'failed') return 'failed';
    return 'testing';
  };

  // Pipeline step status
  const getStepStatus = (step: Step): 'idle' | 'active' | 'done' => {
    const currentIdx = STEP_ORDER.indexOf(currentStep);
    const stepIdx = STEP_ORDER.indexOf(step);
    if (stepIdx < currentIdx) return 'done';
    if (stepIdx === currentIdx) {
      // Active if currently loading
      if (step === 'scanning' && scanLoading) return 'active';
      if (step === 'generating' && generateMutation.isPending) return 'active';
      if (step === 'running' && runLoopMutation.isPending) return 'active';
      if (step === 'results' && rankLoading) return 'active';
      if (step === 'activate' && (activateMutation.isPending || activateAllMutation.isPending)) return 'active';
      return 'done';
    }
    return 'idle';
  };

  // Filtered generated strategies by group
  const displayedStrategies = useMemo(() => {
    if (groupFilter === 'all') return generatedStrategies;
    if (TIMEFRAMES.includes(groupFilter)) {
      return generatedStrategies.filter(s => s.timeframe === groupFilter);
    }
    return generatedStrategies.filter(s => s.category.toLowerCase() === groupFilter.toLowerCase());
  }, [generatedStrategies, groupFilter]);

  // Selected count
  const selectedCount = selectedStrategyIds.size;

  // Auto-scroll to results when backtesting completes or results data loads
  useEffect(() => {
    const shouldScroll = (currentStep === 'results' || currentStep === 'activate') && rankedResults.length > 0;
    if (!shouldScroll) return;

    const scrollToResults = () => {
      if (!resultsRef.current) return;

      // The scroll container is the parent div with overflow-y-auto (scrollAreaRef)
      const scrollContainer = scrollAreaRef.current;
      if (scrollContainer && resultsRef.current) {
        const containerRect = scrollContainer.getBoundingClientRect();
        const resultsRect = resultsRef.current.getBoundingClientRect();
        // Only scroll if results are not already visible in the viewport
        if (resultsRect.top > containerRect.bottom - 40 || resultsRect.bottom < containerRect.top + 40) {
          const scrollOffset = resultsRect.top - containerRect.top + scrollContainer.scrollTop;
          scrollContainer.scrollTo({ top: scrollOffset - 16, behavior: 'smooth' });
          return;
        }
      }

      // Fallback: use scrollIntoView
      try {
        resultsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch {
        // Ignore scroll errors
      }
    };

    // Use a timeout to ensure DOM has updated with new data
    const timer = setTimeout(scrollToResults, 600);
    return () => clearTimeout(timer);
  }, [currentStep, rankedResults.length, rankData]);

  // Scroll into view when ranking results are freshly loaded
  const prevRankedCountRef = useRef(0);
  useEffect(() => {
    if (rankedResults.length > 0 && rankedResults.length !== prevRankedCountRef.current) {
      prevRankedCountRef.current = rankedResults.length;
      const timer = setTimeout(() => {
        resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [rankedResults.length]);

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] border border-[#1e293b] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#1e293b] bg-[#0d1117] shrink-0">
        <Brain className="h-4 w-4 text-[#d4af37]" />
        <span className="text-[#d4af37] font-mono text-sm font-bold tracking-wider">AI TRADING MANAGER</span>
        <div className="ml-auto flex items-center gap-2">
          <Button
            onClick={handleAutoRunPipeline}
            disabled={autoRunning || generateMutation.isPending || runLoopMutation.isPending}
            className="h-7 px-3 text-[10px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30"
          >
            {autoRunning ? (
              <>
                <Loader2 className="h-3 w-3 mr-1 animate-spin" /> {autoRunPhase}
              </>
            ) : (
              <>
                <Rocket className="h-3 w-3 mr-1" /> Run Full Pipeline
              </>
            )}
          </Button>
          <Button
            onClick={handleEvolve}
            disabled={evolveMutation.isPending || rankedResults.length === 0}
            className="h-7 px-3 text-[10px] font-mono bg-purple-600/20 text-purple-400 hover:bg-purple-600/30 border border-purple-500/30"
          >
            {evolveMutation.isPending ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Layers className="h-3 w-3 mr-1" />
            )}
            Evolve Top Strategies
          </Button>
          <Button
            onClick={handleAutoEvolveAndExecute}
            disabled={autoEvolveRunning || autoEvolveExecuteMutation.isPending || rankedResults.length === 0}
            className="h-7 px-3 text-[10px] font-mono bg-emerald-600/20 text-emerald-400 hover:bg-emerald-600/30 border border-emerald-500/30"
          >
            {autoEvolveRunning || autoEvolveExecuteMutation.isPending ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <Dna className="h-3 w-3 mr-1" />
            )}
            Auto-Evolve & Execute
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleQuickStart}
            className="h-6 px-2 text-[10px] font-mono text-[#d4af37] hover:text-[#f0d060] hover:bg-[#d4af37]/10"
          >
            <Sparkles className="h-3 w-3 mr-1" /> Quick Start
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setCurrentStep('setup'); refetchScan(); refetchRank(); }}
            className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]"
          >
            <RefreshCw className="h-3 w-3 mr-1" /> Reset
          </Button>
        </div>
      </div>

      {/* Main Content - scrollable area */}
      <div className="flex-1 min-h-0 overflow-y-auto" ref={scrollAreaRef} style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
        <div className="p-4 space-y-4">
          {/* ============================================= */}
          {/* VISUAL PIPELINE FLOW */}
          {/* ============================================= */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4">
            <div className="flex items-center justify-center gap-0 flex-wrap">
              {PIPELINE_NODES.map((node, i) => (
                <PipelineNode
                  key={node.step}
                  emoji={node.emoji}
                  label={node.label}
                  status={getStepStatus(node.step)}
                  isLast={i === PIPELINE_NODES.length - 1}
                />
              ))}
            </div>
          </div>

          {/* ============================================= */}
          {/* STEP 1: Capital Setup */}
          {/* ============================================= */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2 mb-2">
              <DollarSign className="h-3.5 w-3.5 text-[#d4af37]" />
              <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Capital Setup</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {/* Left: Capital & Allocation */}
              <div className="space-y-3">
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Total Capital ($)</label>
                  <Input
                    type="number"
                    value={capital}
                    onChange={e => setCapital(Number(e.target.value))}
                    className="h-8 text-xs font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Allocation Mode</label>
                  <Select value={allocationMode} onValueChange={v => setAllocationMode(v as 'distribute' | 'focus')}>
                    <SelectTrigger className="h-8 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                      <SelectItem value="distribute" className="text-[10px] font-mono text-[#e2e8f0]">Distribute</SelectItem>
                      <SelectItem value="focus" className="text-[10px] font-mono text-[#e2e8f0]">Focus</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Capital Visualization Bar */}
                <div className="mt-2">
                  <div className="flex items-center justify-between text-[9px] font-mono text-[#64748b] mb-1">
                    <span>Capital Distribution</span>
                    <span>${capital.toLocaleString()} total</span>
                  </div>
                  <div className="h-3 bg-[#1a1f2e] rounded-full overflow-hidden flex">
                    {generatedStrategies.length > 0 ? generatedStrategies.map((s, i) => (
                      <div
                        key={s.id}
                        className="h-full transition-all duration-300"
                        style={{
                          width: `${(s.capitalAllocation / capital) * 100}%`,
                          backgroundColor: `hsl(${(i * 360 / generatedStrategies.length) + 40}, 70%, 50%)`,
                        }}
                        title={`${s.name}: $${s.capitalAllocation.toFixed(0)}`}
                      />
                    )) : (
                      <div className="h-full bg-[#2d3748] w-full" />
                    )}
                  </div>
                  {generatedStrategies.length > 0 && (
                    <div className="text-[8px] font-mono text-[#475569] mt-0.5">
                      {allocationMode === 'distribute'
                        ? `${generatedStrategies.length} strategies × $${(capital / Math.max(generatedStrategies.length, 1)).toFixed(0)} each`
                        : `$${capital.toLocaleString()} focused on each strategy`}
                    </div>
                  )}
                </div>
              </div>

              {/* Center: Timeframes & Token Ages */}
              <div className="space-y-3">
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1.5 block">Timeframes</label>
                  <div className="flex flex-wrap gap-1.5">
                    {TIMEFRAMES.map(tf => (
                      <button
                        key={tf}
                        onClick={() => toggleTimeframe(tf)}
                        className={`px-2.5 py-1 rounded-full text-[10px] font-mono border transition-all ${
                          selectedTimeframes.includes(tf)
                            ? 'bg-[#d4af37]/15 text-[#d4af37] border-[#d4af37]/30'
                            : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                        }`}
                      >
                        {tf}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1.5 block">Token Age Filter</label>
                  <div className="flex gap-1.5">
                    {TOKEN_AGES.map(age => (
                      <button
                        key={age}
                        onClick={() => toggleTokenAge(age)}
                        className={`px-3 py-1 rounded-full text-[10px] font-mono border transition-all ${
                          selectedTokenAges.includes(age)
                            ? 'bg-[#d4af37]/15 text-[#d4af37] border-[#d4af37]/30'
                            : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                        }`}
                      >
                        {age === 'NEW' ? '<7d' : age === 'MEDIUM' ? '7-30d' : '>30d'}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Right: Risk & Strategy Count */}
              <div className="space-y-3">
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Risk Tolerance</label>
                  <Select value={riskTolerance} onValueChange={setRiskTolerance}>
                    <SelectTrigger className="h-8 text-[10px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-[#1a1f2e] border-[#2d3748]">
                      {RISK_LEVELS.map(r => (
                        <SelectItem key={r} value={r} className="text-[10px] font-mono text-[#e2e8f0]">{r}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-[9px] font-mono text-[#64748b] uppercase tracking-wider mb-1 block">Max Strategies</label>
                  <Input
                    type="number"
                    value={strategyCount}
                    onChange={e => setStrategyCount(Number(e.target.value))}
                    min={1}
                    max={20}
                    className="h-8 text-xs font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0]"
                  />
                </div>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center gap-2 pt-2">
              <Button
                onClick={handleScan}
                disabled={scanLoading}
                className="h-8 px-4 text-[10px] font-mono bg-[#d4af37]/20 text-[#d4af37] hover:bg-[#d4af37]/30 border border-[#d4af37]/30"
              >
                {scanLoading ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Scan className="h-3 w-3 mr-1" />}
                Scan Opportunities
              </Button>
              <Button
                onClick={handleGenerate}
                disabled={generateMutation.isPending || selectedTimeframes.length === 0}
                className="h-8 px-4 text-[10px] font-mono bg-cyan-600/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-600/30"
              >
                {generateMutation.isPending ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Zap className="h-3 w-3 mr-1" />}
                Generate Strategies
              </Button>
            </div>
          </div>

          {/* ============================================= */}
          {/* STEP 2: AI Scan Results */}
          {/* ============================================= */}
          {/* Data Quality Gate — shows data readiness before backtesting */}
          <DataQualityGate compact={scanData ? true : false} />
          <AnimatePresence>
            {scanData && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4 space-y-3"
              >
                <div className="flex items-center gap-2 mb-2">
                  <Scan className="h-3.5 w-3.5 text-[#d4af37]" />
                  <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Scan Results</span>
                  <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                    {tokens.length} tokens · {scanData.dnaProfiles} DNA · {scanData.activeSignals} signals
                  </Badge>
                </div>

                {tokens.length === 0 ? (
                  <div className="flex flex-col items-center py-8 text-[#64748b]">
                    <Target className="h-8 w-8 mb-2 text-[#2d3748]" />
                    <span className="font-mono text-sm">No opportunities found. Try running the Brain first.</span>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2 max-h-64 overflow-y-auto">
                    {tokens.slice(0, 12).map((token, idx) => {
                      const risk = RISK_COLORS[token.dnaRiskLevel] || RISK_COLORS.MEDIUM;
                      return (
                        <motion.div
                          key={token.id || `scan-${idx}`}
                          initial={{ opacity: 0, scale: 0.95 }}
                          animate={{ opacity: 1, scale: 1 }}
                          transition={{ delay: idx * 0.03 }}
                          className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 hover:border-[#2d3748] transition-all"
                        >
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="font-mono text-xs font-bold text-[#e2e8f0]">{token.symbol}</span>
                            <Badge className={`text-[8px] h-3.5 px-1 font-mono border ${risk.bg} ${risk.text}`}>
                              {token.dnaRiskLevel}
                            </Badge>
                          </div>
                          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[9px] font-mono">
                            <span className="text-[#64748b]">Vol 24h</span>
                            <span className="text-[#94a3b8] text-right">${(token.volume24h || 0).toLocaleString()}</span>
                            <span className="text-[#64748b]">Change</span>
                            <span className={`text-right ${(token.priceChange24h || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {(token.priceChange24h || 0) >= 0 ? '+' : ''}{(token.priceChange24h || 0).toFixed(1)}%
                            </span>
                            <span className="text-[#64748b]">Signals</span>
                            <span className="text-amber-400 text-right">{token.signalCount}</span>
                            <span className="text-[#64748b]">Phase</span>
                            <span className="text-[#94a3b8] text-right">{token.phase || '—'}</span>
                          </div>
                        </motion.div>
                      );
                    })}
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          {/* ============================================= */}
          {/* STEP 3: Strategy Cards with Grouping */}
          {/* ============================================= */}
          <AnimatePresence>
            {generatedStrategies.length > 0 && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4 space-y-3"
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Zap className="h-3.5 w-3.5 text-[#d4af37]" />
                    <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Strategy Cards</span>
                    <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                      {selectedCount}/{generatedStrategies.length} selected
                    </Badge>
                  </div>
                </div>

                {/* Strategy Grouping Controls */}
                <div className="flex flex-wrap items-center gap-2 pb-2 border-b border-[#1e293b]/50">
                  <Layers className="h-3 w-3 text-[#475569]" />
                  <span className="text-[8px] font-mono text-[#475569] uppercase">Select by:</span>
                  <button
                    onClick={() => handleSelectByCategory('all')}
                    className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                      selectedCount === generatedStrategies.length ? 'bg-[#d4af37]/15 text-[#d4af37] border-[#d4af37]/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                    }`}
                  >
                    All
                  </button>
                  {uniqueCategories.map(cat => (
                    <button
                      key={cat}
                      onClick={() => handleSelectByCategory(cat)}
                      className="px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]"
                      style={{ color: getCategoryColor(cat) }}
                    >
                      {cat}
                    </button>
                  ))}
                  <span className="text-[#2d3748]">|</span>
                  {uniqueStratTimeframes.map(tf => (
                    <button
                      key={tf}
                      onClick={() => handleSelectByTimeframe(tf)}
                      className="px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]"
                    >
                      {tf}
                    </button>
                  ))}
                  <button
                    onClick={() => setSelectedStrategyIds(new Set())}
                    className="px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all bg-[#0a0e17] text-red-400/60 border-[#1e293b] hover:border-red-400/30"
                  >
                    None
                  </button>
                </div>

                {/* Filter by category/timeframe (display filter) */}
                <div className="flex items-center gap-2">
                  <Filter className="h-3 w-3 text-[#475569]" />
                  <span className="text-[8px] font-mono text-[#475569] uppercase">View:</span>
                  <button
                    onClick={() => setGroupFilter('all')}
                    className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                      groupFilter === 'all' ? 'bg-cyan-600/15 text-cyan-400 border-cyan-500/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b]'
                    }`}
                  >
                    All
                  </button>
                  {uniqueCategories.map(cat => (
                    <button
                      key={cat}
                      onClick={() => setGroupFilter(cat)}
                      className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                        groupFilter === cat ? 'bg-cyan-600/15 text-cyan-400 border-cyan-500/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b]'
                      }`}
                    >
                      {cat}
                    </button>
                  ))}
                  {uniqueStratTimeframes.filter(tf => !uniqueCategories.includes(tf)).map(tf => (
                    <button
                      key={tf}
                      onClick={() => setGroupFilter(tf)}
                      className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                        groupFilter === tf ? 'bg-cyan-600/15 text-cyan-400 border-cyan-500/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b]'
                      }`}
                    >
                      {tf}
                    </button>
                  ))}
                </div>

                {/* Strategy Cards Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2 max-h-96 overflow-y-auto">
                  <AnimatePresence mode="popLayout">
                    {displayedStrategies.map((strat) => (
                      <StrategyCard
                        key={strat.id}
                        strategy={strat}
                        onEdit={handleStrategyEdit}
                        onRemove={handleStrategyRemove}
                        isSelected={selectedStrategyIds.has(strat.id)}
                        onToggleSelect={handleToggleSelectStrategy}
                        status={getStrategyStatus(strat.id)}
                      />
                    ))}
                  </AnimatePresence>
                </div>

                {/* Run Backtest Button */}
                <div className="flex items-center gap-3 pt-2">
                  <Button
                    onClick={handleRunLoop}
                    disabled={runLoopMutation.isPending || selectedCount === 0}
                    className="h-8 px-4 text-[10px] font-mono bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-600/30"
                  >
                    {runLoopMutation.isPending ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Play className="h-3 w-3 mr-1" />}
                    {runLoopMutation.isPending ? 'Running Backtests...' : `Run Backtest (${selectedCount} strategies)`}
                  </Button>
                  {runLoopMutation.isPending && (
                    <span className="text-[9px] font-mono text-amber-400 flex items-center gap-1">
                      <Activity className="h-3 w-3 animate-pulse" /> Testing {selectedCount} strategies...
                    </span>
                  )}
                </div>

                {/* Loop Progress */}
                {loopResults.length > 0 && (
                  <div className="space-y-1.5 pt-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[9px] font-mono text-[#475569] uppercase">Backtest Progress</span>
                      <span className="text-[9px] font-mono text-[#94a3b8]">
                        {loopResults.filter(r => r.status === 'completed' || r.status === 'failed').length}/{loopResults.length} done
                        {loopResults.filter(r => r.status === 'failed').length > 0 && (
                          <span className="text-red-400 ml-1">({loopResults.filter(r => r.status === 'failed').length} failed)</span>
                        )}
                      </span>
                    </div>
                    {/* Overall progress bar */}
                    <div className="h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{
                          width: `${loopResults.length > 0 ? (loopResults.filter(r => r.status === 'completed' || r.status === 'failed').length / loopResults.length) * 100 : 0}%`,
                          backgroundColor: loopResults.every(r => r.status === 'completed') ? '#10b981' :
                            loopResults.some(r => r.status === 'failed') ? '#f59e0b' : '#3b82f6',
                        }}
                      />
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
                      {loopResults.map((res, idx) => (
                        <div key={res.strategyId || `loop-${idx}`} className="flex items-center gap-2 text-[9px] font-mono bg-[#111827] rounded px-2 py-1">
                          {res.status === 'completed' ? (
                            <Check className="h-3 w-3 text-emerald-400" />
                          ) : res.status === 'failed' ? (
                            <X className="h-3 w-3 text-red-400" />
                          ) : (
                            <Loader2 className="h-3 w-3 text-amber-400 animate-spin" />
                          )}
                          <span className="text-[#94a3b8] truncate flex-1">{res.strategyName}</span>
                          <span className={`${
                            res.status === 'completed' ? 'text-emerald-400' :
                            res.status === 'running' ? 'text-amber-400' :
                            res.status === 'failed' ? 'text-red-400' : 'text-[#64748b]'
                          }`}>
                            {res.status.toUpperCase()}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          {/* ============================================= */}
          {/* PORTFOLIO SUMMARY DASHBOARD */}
          {/* ============================================= */}
          <AnimatePresence>
            {filteredRankedResults.length > 0 && (
              <PortfolioSummary results={filteredRankedResults} capital={capital} />
            )}
          </AnimatePresence>

          {/* ============================================= */}
          {/* STEP 4: Results Ranking */}
          {/* ============================================= */}
          <div ref={resultsRef} className={`bg-[#0d1117] border rounded-lg transition-all duration-300 overflow-hidden ${
            (currentStep === 'results' || currentStep === 'activate' || rankedResults.length > 0)
              ? 'border-[#d4af37]/30 shadow-md shadow-[#d4af37]/5 p-4 space-y-3'
              : 'border-[#1e293b] p-4 space-y-3'
          }`}>
            {/* Results Header - prominent and clear */}
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Trophy className="h-4 w-4 text-[#d4af37]" />
                <span className="text-xs font-mono text-[#d4af37] uppercase tracking-wider font-bold">Results Ranking</span>
                <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                  {filteredRankedResults.length} results
                </Badge>
                {/* Time filter */}
                <div className="flex items-center gap-1 ml-1">
                  <span className="text-[8px] font-mono text-[#475569]">Period:</span>
                  {[
                    { value: 0, label: 'All' },
                    { value: 24, label: '24h' },
                    { value: 168, label: '7d' },
                    { value: 720, label: '30d' },
                  ].map(opt => (
                    <button
                      key={opt.value}
                      onClick={() => setFilterRecent(opt.value)}
                      className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                        filterRecent === opt.value
                          ? 'bg-[#d4af37]/15 text-[#d4af37] border-[#d4af37]/30'
                          : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {/* Execute Top Strategy Button */}
                <Button
                  onClick={() => handleExecuteTopStrategies(1)}
                  disabled={autoTradeMutation.isPending || filteredRankedResults.length === 0}
                  className="h-6 px-3 text-[9px] font-mono bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-600/30"
                >
                  {autoTradeMutation.isPending ? (
                    <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                  ) : (
                    <Crosshair className="h-3 w-3 mr-1" />
                  )}
                  Execute #1
                </Button>
                <Button
                  onClick={() => handleExecuteTopStrategies(3)}
                  disabled={autoTradeMutation.isPending || filteredRankedResults.length === 0}
                  className="h-6 px-3 text-[9px] font-mono bg-amber-600/20 text-amber-400 border border-amber-500/30 hover:bg-amber-600/30"
                >
                  <TrendingUp className="h-3 w-3 mr-1" />
                  Execute Top 3
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => refetchRank()}
                  disabled={rankLoading}
                  className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]"
                >
                  <RefreshCw className={`h-3 w-3 mr-1 ${rankLoading ? 'animate-spin' : ''}`} /> Refresh
                </Button>
              </div>
            </div>

            {/* Execution Status Panel */}
            {executionStatuses.length > 0 && (
              <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-1.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <CircleDot className="h-3 w-3 text-[#d4af37]" />
                    <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">Execution Log</span>
                  </div>
                  <button
                    onClick={() => setExecutionStatuses([])}
                    className="text-[8px] font-mono text-[#475569] hover:text-[#94a3b8]"
                  >
                    Clear
                  </button>
                </div>
                <div className="max-h-32 overflow-y-auto space-y-1">
                  {executionStatuses.slice(-10).reverse().map((status, idx) => (
                    <div key={`exec-${idx}`} className={`flex items-center gap-2 text-[9px] font-mono px-2 py-1 rounded ${
                      status.type === 'success' ? 'bg-emerald-500/5 text-emerald-400' :
                      status.type === 'error' ? 'bg-red-500/5 text-red-400' :
                      status.type === 'executing' ? 'bg-amber-500/5 text-amber-400' :
                      'bg-[#1a1f2e] text-[#94a3b8]'
                    }`}>
                      {status.type === 'success' && <Check className="h-3 w-3 shrink-0" />}
                      {status.type === 'error' && <AlertTriangle className="h-3 w-3 shrink-0" />}
                      {status.type === 'executing' && <Loader2 className="h-3 w-3 shrink-0 animate-spin" />}
                      {status.type === 'idle' && <CircleDot className="h-3 w-3 shrink-0" />}
                      <span className="flex-1 truncate">{status.message}</span>
                      {status.tradeId && <Badge className="text-[7px] h-3 px-1 font-mono bg-[#0a0e17] text-[#64748b] border-0">{status.tradeId.slice(0, 8)}</Badge>}
                      {status.pnlUsd !== undefined && (
                        <span className={status.pnlUsd >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                          {status.pnlUsd >= 0 ? '+' : ''}{status.pnlUsd.toFixed(2)}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Filter Pills */}
            {rankedResults.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 pb-2 border-b border-[#1e293b]/50">
                <Filter className="h-3 w-3 text-[#475569]" />
                {/* Timeframe filter */}
                <div className="flex items-center gap-1">
                  <span className="text-[8px] font-mono text-[#475569]">TF:</span>
                  <button
                    onClick={() => setFilterTimeframe('ALL')}
                    className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                      filterTimeframe === 'ALL' ? 'bg-[#d4af37]/15 text-[#d4af37] border-[#d4af37]/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                    }`}
                  >
                    All
                  </button>
                  {uniqueTimeframes.map(tf => (
                    <button
                      key={tf}
                      onClick={() => setFilterTimeframe(tf)}
                      className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                        filterTimeframe === tf ? 'bg-[#d4af37]/15 text-[#d4af37] border-[#d4af37]/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                      }`}
                    >
                      {tf}
                    </button>
                  ))}
                </div>
                {/* Token age filter */}
                <div className="flex items-center gap-1">
                  <span className="text-[8px] font-mono text-[#475569]">Age:</span>
                  <button
                    onClick={() => setFilterTokenAge('ALL')}
                    className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                      filterTokenAge === 'ALL' ? 'bg-cyan-600/15 text-cyan-400 border-cyan-500/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                    }`}
                  >
                    All
                  </button>
                  {uniqueTokenAges.map(age => (
                    <button
                      key={age}
                      onClick={() => setFilterTokenAge(age)}
                      className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                        filterTokenAge === age ? 'bg-cyan-600/15 text-cyan-400 border-cyan-500/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                      }`}
                    >
                      {age === 'NEW' ? '<7d' : age === 'MEDIUM' ? '7-30d' : '>30d'}
                    </button>
                  ))}
                </div>
                {/* Risk filter */}
                <div className="flex items-center gap-1">
                  <span className="text-[8px] font-mono text-[#475569]">Risk:</span>
                  <button
                    onClick={() => setFilterRisk('ALL')}
                    className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                      filterRisk === 'ALL' ? 'bg-emerald-600/15 text-emerald-400 border-emerald-500/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                    }`}
                  >
                    All
                  </button>
                  {uniqueRisks.map(risk => (
                    <button
                      key={risk}
                      onClick={() => setFilterRisk(risk)}
                      className={`px-1.5 py-0.5 rounded text-[8px] font-mono border transition-all ${
                        filterRisk === risk ? 'bg-emerald-600/15 text-emerald-400 border-emerald-500/30' : 'bg-[#0a0e17] text-[#64748b] border-[#1e293b] hover:border-[#2d3748]'
                      }`}
                    >
                      {risk.slice(0, 4)}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {filteredRankedResults.length === 0 ? (
              <div className="flex flex-col items-center py-8 text-[#64748b]">
                <BarChart3 className="h-8 w-8 mb-2 text-[#2d3748]" />
                <span className="font-mono text-sm">
                  {rankedResults.length === 0
                    ? 'No backtests with trades yet. Run the pipeline to generate strategies and backtest them.'
                    : 'No results match your filters.'}
                </span>
                {rankedResults.length === 0 && (
                  <span className="font-mono text-[10px] text-[#475569] mt-1">
                    Tip: Backtests with 0 trades are automatically excluded from ranking.
                  </span>
                )}
              </div>
            ) : (
              <div className="overflow-auto max-h-[500px] min-h-[200px]" style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
                <table className="w-full text-[9px] font-mono min-w-[800px]">
                  <thead>
                    <tr className="text-[#475569] uppercase border-b border-[#1e293b]">
                      <th className="py-1.5 px-1.5 text-left">#</th>
                      <th className="py-1.5 px-1.5 text-left">Strategy</th>
                      <th className="py-1.5 px-1.5 text-left">Cat</th>
                      <th className="py-1.5 px-1.5 text-left">TF</th>
                      <th className="py-1.5 px-1.5 text-right">Grade</th>
                      <th className="py-1.5 px-1.5 text-right">Score</th>
                      <th className="py-1.5 px-1.5 text-right">Sharpe</th>
                      <th className="py-1.5 px-1.5 text-right">Win%</th>
                      <th className="py-1.5 px-1.5 text-right">PnL%</th>
                      <th className="py-1.5 px-1.5 text-right">PnL$</th>
                      <th className="py-1.5 px-1.5 text-right">DD%</th>
                      <th className="py-1.5 px-1.5 text-right">PF</th>
                      <th className="py-1.5 px-1.5 text-right">Trades</th>
                      <th className="py-1.5 px-1.5 text-center">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRankedResults.slice(0, 50).map((result, idx) => {
                      const rankNum = result.rank || (idx + 1);
                      const medalEmoji = rankNum === 1 ? '🥇' : rankNum === 2 ? '🥈' : rankNum === 3 ? '🥉' : '';
                      const rowBg = rankNum === 1 ? 'bg-[#d4af37]/5' : rankNum === 2 ? 'bg-[#c0c0c0]/5' : rankNum === 3 ? 'bg-[#cd7f32]/5' : '';
                      const catColor = getCategoryColor(result.category);
                      const catLabel = formatCategory(result.category);
                      const isExpanded = expandedBacktestId === result.backtestId;
                      const isInHoF = bestBacktestIds.has(result.backtestId);
                      const gradeColor = GRADE_COLORS[result.grade] || GRADE_COLORS['F'];

                      return (
                        <React.Fragment key={result.id || result.backtestId || `rank-${idx}`}>
                          <tr
                            className={`border-b border-[#1e293b]/50 hover:bg-[#111827] transition-colors cursor-pointer ${rowBg} ${isInHoF ? 'border-l-2 border-l-[#d4af37]' : ''} ${isExpanded ? 'bg-[#111827]' : ''}`}
                            onClick={() => setExpandedBacktestId(isExpanded ? null : result.backtestId)}
                          >
                            <td className="py-1.5 px-1.5 font-bold whitespace-nowrap">
                              {medalEmoji ? (
                                <span className="text-xs">{medalEmoji}</span>
                              ) : (
                                <span className="text-[#64748b]">{rankNum}</span>
                              )}
                            </td>
                            <td className="py-1.5 px-1.5 text-[#e2e8f0] max-w-[180px] truncate" title={result.strategyName}>
                              <div className="flex items-center gap-1">
                                {result.strategyName}
                                {isInHoF && <Star className="h-2.5 w-2.5 text-[#d4af37] shrink-0" />}
                              </div>
                            </td>
                            <td className="py-1.5 px-1.5">
                              <span
                                className="inline-block px-1 py-0.5 rounded text-[7px] font-bold max-w-[80px] truncate"
                                style={{ backgroundColor: `${catColor}20`, color: catColor }}
                                title={catLabel}
                              >
                                {catLabel}
                              </span>
                            </td>
                            <td className="py-1.5 px-1.5 text-[#94a3b8]">{result.timeframe}</td>
                            <td className="py-1.5 px-1.5 text-right">
                              <span
                                className="inline-block px-1.5 py-0.5 rounded text-[8px] font-bold"
                                style={{ backgroundColor: `${gradeColor}20`, color: gradeColor }}
                              >
                                {result.grade || 'F'}
                              </span>
                            </td>
                            <td className="py-1.5 px-1.5 text-right">
                              <div className="flex items-center justify-end gap-1">
                                <div className="w-8 h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                                  <div
                                    className="h-full rounded-full"
                                    style={{
                                      width: `${Math.min(100, Math.max(0, result.score))}%`,
                                      backgroundColor: gradeColor,
                                    }}
                                  />
                                </div>
                                <span className="font-bold" style={{ color: gradeColor }}>{result.score.toFixed(1)}</span>
                              </div>
                            </td>
                            <td className="py-1.5 px-1.5 text-right text-[#94a3b8]">{result.sharpeRatio.toFixed(2)}</td>
                            <td className="py-1.5 px-1.5 text-right text-[#94a3b8]">{(result.winRate * 100).toFixed(0)}%</td>
                            <td className={`py-1.5 px-1.5 text-right font-bold ${result.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {result.pnlPct >= 0 ? '+' : ''}{result.pnlPct.toFixed(1)}%
                            </td>
                            <td className={`py-1.5 px-1.5 text-right ${result.pnlUsd >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {result.pnlUsd >= 0 ? '+' : ''}{result.pnlUsd < 0 ? '-' : ''}${Math.abs(result.pnlUsd).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                            </td>
                            <td className="py-1.5 px-1.5 text-right text-red-400">{result.maxDrawdownPct.toFixed(1)}%</td>
                            <td className="py-1.5 px-1.5 text-right text-[#94a3b8]">{result.profitFactor.toFixed(2)}</td>
                            <td className="py-1.5 px-1.5 text-right text-[#94a3b8]">{result.totalTrades}</td>
                            <td className="py-1.5 px-1.5 text-center" onClick={e => e.stopPropagation()}>
                              <div className="flex items-center justify-center gap-1">
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <button
                                      onClick={() => handleExecuteSingleStrategy(result)}
                                      className="text-emerald-400/60 hover:text-emerald-400 transition-colors"
                                      title="Execute this strategy"
                                    >
                                      <Crosshair className="h-3 w-3" />
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent>Execute Trade</TooltipContent>
                                </Tooltip>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <button
                                      onClick={() => saveBestMutation.mutate(result)}
                                      className={`transition-colors ${isInHoF ? 'text-[#d4af37]' : 'text-[#475569] hover:text-[#d4af37]'}`}
                                      title={isInHoF ? 'Already in Hall of Fame' : 'Save to Hall of Fame'}
                                      disabled={isInHoF}
                                    >
                                      <BookmarkPlus className="h-3 w-3" />
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent>{isInHoF ? 'Saved' : 'Save to HoF'}</TooltipContent>
                                </Tooltip>
                              </div>
                            </td>
                          </tr>
                          {/* Expanded detail row */}
                          {isExpanded && (
                            <tr key={`detail-${result.backtestId}`} className="border-b border-[#1e293b]">
                              <td colSpan={14} className="p-0">
                                <div className="bg-[#0a0e17] p-3 space-y-3">
                                  {detailLoading ? (
                                    <div className="flex items-center justify-center py-4">
                                      <Loader2 className="h-4 w-4 text-purple-400 animate-spin mr-2" />
                                      <span className="text-[9px] font-mono text-[#64748b]">Loading details...</span>
                                    </div>
                                  ) : expandedBacktestDetail ? (
                                    <>
                                      {/* Advanced metrics grid */}
                                      <div className="grid grid-cols-4 md:grid-cols-6 gap-2">
                                        <div className="bg-[#111827] border border-[#1e293b] rounded p-2 text-center">
                                          <div className="text-[7px] font-mono text-[#475569] uppercase">Sortino</div>
                                          <div className="text-[10px] font-mono font-bold text-cyan-400">{(expandedBacktestDetail.sortinoRatio ?? 0).toFixed(2)}</div>
                                        </div>
                                        <div className="bg-[#111827] border border-[#1e293b] rounded p-2 text-center">
                                          <div className="text-[7px] font-mono text-[#475569] uppercase">Calmar</div>
                                          <div className="text-[10px] font-mono font-bold text-cyan-400">{(expandedBacktestDetail.calmarRatio ?? 0).toFixed(2)}</div>
                                        </div>
                                        <div className="bg-[#111827] border border-[#1e293b] rounded p-2 text-center">
                                          <div className="text-[7px] font-mono text-[#475569] uppercase">Recovery</div>
                                          <div className="text-[10px] font-mono font-bold text-cyan-400">{(expandedBacktestDetail.recoveryFactor ?? 0).toFixed(2)}</div>
                                        </div>
                                        <div className="bg-[#111827] border border-[#1e293b] rounded p-2 text-center">
                                          <div className="text-[7px] font-mono text-[#475569] uppercase">Initial $</div>
                                          <div className="text-[10px] font-mono font-bold text-[#94a3b8]">{(expandedBacktestDetail.initialCapital ?? 0).toFixed(0)}</div>
                                        </div>
                                        <div className="bg-[#111827] border border-[#1e293b] rounded p-2 text-center">
                                          <div className="text-[7px] font-mono text-[#475569] uppercase">Final $</div>
                                          <div className={`text-[10px] font-mono font-bold ${(expandedBacktestDetail.finalCapital ?? 0) >= (expandedBacktestDetail.initialCapital ?? 0) ? 'text-emerald-400' : 'text-red-400'}`}>
                                            {(expandedBacktestDetail.finalCapital ?? 0).toFixed(0)}
                                          </div>
                                        </div>
                                        <div className="bg-[#111827] border border-[#1e293b] rounded p-2 text-center">
                                          <div className="text-[7px] font-mono text-[#475569] uppercase">Net P&L</div>
                                          <div className={`text-[10px] font-mono font-bold ${(expandedBacktestDetail.totalPnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                            {(expandedBacktestDetail.totalPnl ?? 0) >= 0 ? '+' : ''}{(expandedBacktestDetail.totalPnl ?? 0).toFixed(2)}
                                          </div>
                                        </div>
                                      </div>
                                      {/* Individual trades table */}
                                      {expandedBacktestDetail.operations && expandedBacktestDetail.operations.length > 0 ? (
                                        <div>
                                          <div className="flex items-center gap-1.5 mb-1.5">
                                            <BarChart3 className="h-3 w-3 text-[#64748b]" />
                                            <span className="text-[8px] font-mono text-[#94a3b8] uppercase tracking-wider">
                                              Trades ({expandedBacktestDetail.operations.length})
                                            </span>
                                          </div>
                                          <div className="max-h-40 overflow-y-auto" style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
                                            <table className="w-full text-[8px] font-mono">
                                              <thead>
                                                <tr className="text-[#475569] uppercase border-b border-[#1e293b]/50">
                                                  <th className="py-1 px-1 text-left">Token</th>
                                                  <th className="py-1 px-1 text-left">Dir</th>
                                                  <th className="py-1 px-1 text-right">Entry</th>
                                                  <th className="py-1 px-1 text-right">Exit</th>
                                                  <th className="py-1 px-1 text-right">PnL%</th>
                                                  <th className="py-1 px-1 text-right">PnL $</th>
                                                  <th className="py-1 px-1 text-right">Hold</th>
                                                  <th className="py-1 px-1 text-left">Exit Reason</th>
                                                </tr>
                                              </thead>
                                              <tbody>
                                                {expandedBacktestDetail.operations.map((op) => (
                                                  <tr key={op.id} className="border-b border-[#1e293b]/30 hover:bg-[#111827]">
                                                    <td className="py-0.5 px-1 text-[#e2e8f0]">{op.tokenSymbol || op.tokenAddress.slice(0, 8)}</td>
                                                    <td className={`py-0.5 px-1 ${op.operationType === 'LONG' || op.operationType === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                                                      {op.operationType.slice(0, 4)}
                                                    </td>
                                                    <td className="py-0.5 px-1 text-right text-[#94a3b8]">{op.entryPrice.toFixed(6)}</td>
                                                    <td className="py-0.5 px-1 text-right text-[#94a3b8]">{op.exitPrice?.toFixed(6) ?? '-'}</td>
                                                    <td className={`py-0.5 px-1 text-right font-bold ${(op.pnlPct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                                      {(op.pnlPct ?? 0) >= 0 ? '+' : ''}{(op.pnlPct ?? 0).toFixed(1)}%
                                                    </td>
                                                    <td className={`py-0.5 px-1 text-right ${(op.pnlUsd ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                                      {(op.pnlUsd ?? 0) >= 0 ? '+' : ''}{(op.pnlUsd ?? 0).toFixed(2)}
                                                    </td>
                                                    <td className="py-0.5 px-1 text-right text-[#94a3b8]">{op.holdTimeMin !== null ? `${op.holdTimeMin}m` : '-'}</td>
                                                    <td className="py-0.5 px-1 text-[#475569] max-w-[80px] truncate">{op.exitReason || '-'}</td>
                                                  </tr>
                                                ))}
                                              </tbody>
                                            </table>
                                          </div>
                                        </div>
                                      ) : (
                                        <div className="text-[8px] font-mono text-[#475569] py-2 text-center">
                                          No trade operations recorded for this backtest
                                        </div>
                                      )}
                                    </>
                                  ) : (
                                    <div className="text-[9px] font-mono text-red-400 py-2 text-center">
                                      Failed to load backtest details
                                    </div>
                                  )}
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ============================================= */}
          {/* OPEN POSITIONS MONITOR */}
          {/* ============================================= */}
          <AnimatePresence>
            {openPositions.length > 0 && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="bg-[#0d1117] border border-emerald-500/20 rounded-lg p-4 space-y-3"
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <ArrowRightLeft className="h-3.5 w-3.5 text-emerald-400" />
                    <span className="text-[11px] font-mono text-emerald-400 uppercase tracking-wider">Open Positions</span>
                    <Badge className="text-[8px] h-4 px-1.5 font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                      {openPositions.length} active
                    </Badge>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => refetchOpenPositions()}
                    className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]"
                  >
                    <RefreshCw className="h-3 w-3 mr-1" /> Refresh
                  </Button>
                </div>

                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {openPositions.map((pos, idx) => (
                    <motion.div
                      key={pos.backtestId || `pos-${idx}`}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: idx * 0.05 }}
                      className="bg-[#111827] border border-[#1e293b] rounded-lg p-3"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <Badge className={`text-[8px] h-4 px-1.5 font-mono border-0 ${
                            pos.direction === 'LONG' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                          }`}>
                            {pos.direction}
                          </Badge>
                          <span className="font-mono text-xs font-bold text-[#e2e8f0]">{pos.tokenSymbol || pos.tokenAddress.slice(0, 8)}</span>
                          <span className="text-[9px] font-mono text-[#64748b]">{pos.systemName}</span>
                        </div>
                        <Button
                          onClick={() => autoExitMutation.mutate({
                            backtestId: pos.backtestId,
                            exitReason: 'manual_close',
                          })}
                          disabled={autoExitMutation.isPending}
                          className="h-5 px-2 text-[8px] font-mono bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600/30"
                        >
                          <ArrowRightLeft className="h-2.5 w-2.5 mr-0.5" />
                          Close Position
                        </Button>
                      </div>
                      <div className="grid grid-cols-4 gap-x-3 gap-y-0.5 text-[9px] font-mono">
                        <div>
                          <span className="text-[#64748b]">Entry</span>
                          <div className="text-[#e2e8f0]">${pos.entryPrice.toFixed(6)}</div>
                        </div>
                        <div>
                          <span className="text-[#64748b]">Size</span>
                          <div className="text-[#d4af37]">${pos.positionSizeUsd.toFixed(2)}</div>
                        </div>
                        <div>
                          <span className="text-[#64748b]">Qty</span>
                          <div className="text-[#94a3b8]">{pos.quantity.toFixed(4)}</div>
                        </div>
                        <div>
                          <span className="text-[#64748b]">Time</span>
                          <div className="text-[#94a3b8]">{new Date(pos.entryTime).toLocaleTimeString()}</div>
                        </div>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* ============================================= */}
          {/* AUTO-EVOLUTION CONTROL PANEL (P2-A: Discrete Cycles) */}
          {/* ============================================= */}
          <div className={`bg-[#0d1117] border rounded-lg transition-all duration-300 overflow-hidden ${
            autoScheduleEnabled || autoEvolveRunning || autoEvoStatus?.isRunning
              ? 'border-purple-500/30 shadow-md shadow-purple-500/5 p-4 space-y-3'
              : 'border-[#1e293b] p-4 space-y-3'
          }`}>
            {/* Header Row */}
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Dna className={`h-4 w-4 ${
                  autoScheduleEnabled ? 'text-purple-400 animate-pulse' :
                  autoEvolveRunning ? 'text-purple-400 animate-pulse' :
                  'text-purple-400'
                }`} />
                <span className="text-xs font-mono text-purple-400 uppercase tracking-wider font-bold">Auto-Evolution</span>
                {autoScheduleEnabled && (
                  <Badge className="text-[8px] h-4 px-1.5 font-mono bg-purple-500/15 text-purple-400 border-purple-500/30 animate-pulse">
                    AUTO C{autoScheduleCyclesRun}{autoScheduleTotalCycles > 0 ? `/${autoScheduleTotalCycles}` : ''}
                  </Badge>
                )}
                {!autoScheduleEnabled && autoEvoStatus?.isRunning && (
                  <Badge className="text-[8px] h-4 px-1.5 font-mono bg-purple-500/15 text-purple-400 border-purple-500/30 animate-pulse">
                    CYCLE {autoEvoStatus.currentCycle}/{autoEvoStatus.totalCycles}
                  </Badge>
                )}
                {!autoScheduleEnabled && !autoEvoStatus?.isRunning && (autoEvoStatus?.totalEvolutions ?? 0) > 0 && (
                  <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#64748b] border-[#2d3748]">
                    {autoEvoStatus?.totalEvolutions} evolutions
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => refetchAutoEvo()}
                  className="h-6 px-2 text-[10px] font-mono text-[#64748b] hover:text-[#e2e8f0]"
                >
                  <RefreshCw className="h-3 w-3" />
                </Button>
              </div>
            </div>

            {/* ============================================= */}
            {/* P2-A: AUTO-SCHEDULE TOGGLE + CONTROLS */}
            {/* ============================================= */}
            <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Clock className={`h-3.5 w-3.5 ${autoScheduleEnabled ? 'text-purple-400' : 'text-[#64748b]'}`} />
                  <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">
                    Discrete Auto-Schedule
                  </span>
                </div>
                <button
                  onClick={() => {
                    if (autoScheduleEnabled) {
                      setAutoScheduleEnabled(false);
                      setAutoScheduleCyclesRun(0);
                      toast.info('Auto-schedule stopped');
                    } else {
                      setAutoScheduleCyclesRun(0);
                      setAutoScheduleEnabled(true);
                      toast.success(`Auto-schedule started: every ${autoScheduleInterval}s`);
                    }
                  }}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                    autoScheduleEnabled ? 'bg-purple-600' : 'bg-[#2d3748]'
                  }`}
                >
                  <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                    autoScheduleEnabled ? 'translate-x-4.5' : 'translate-x-1'
                  }`} />
                </button>
              </div>

              {autoScheduleEnabled && (
                <div className="flex items-center gap-3 mt-1">
                  <div className="flex items-center gap-1.5">
                    <label className="text-[8px] font-mono text-[#475569]">Every</label>
                    <Input
                      type="number"
                      value={autoScheduleInterval}
                      onChange={e => {
                        const v = Math.max(30, Number(e.target.value));
                        setAutoScheduleInterval(v);
                      }}
                      className="h-5 w-14 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] px-1.5"
                      min={30}
                      disabled={autoScheduleEnabled}
                    />
                    <span className="text-[8px] font-mono text-[#475569]">s</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <label className="text-[8px] font-mono text-[#475569]">Cycles</label>
                    <Input
                      type="number"
                      value={autoScheduleTotalCycles}
                      onChange={e => setAutoScheduleTotalCycles(Math.max(0, Number(e.target.value)))}
                      className="h-5 w-12 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] px-1.5"
                      min={0}
                      placeholder="0=∞"
                      disabled={autoScheduleEnabled}
                    />
                    <span className="text-[8px] font-mono text-[#475569]">(0=∞)</span>
                  </div>
                  <div className="ml-auto flex items-center gap-1.5">
                    {autoScheduleCountdown > 0 ? (
                      <>
                        <Timer className="h-3 w-3 text-purple-400" />
                        <span className="text-[9px] font-mono text-purple-300 animate-pulse">
                          Next in {autoScheduleCountdown}s
                        </span>
                      </>
                    ) : runNextCycleMutation.isPending ? (
                      <>
                        <Loader2 className="h-3 w-3 text-purple-400 animate-spin" />
                        <span className="text-[9px] font-mono text-purple-300">
                          Running cycle {autoScheduleCyclesRun + 1}...
                        </span>
                      </>
                    ) : autoEvolveRunning ? (
                      <>
                        <Loader2 className="h-3 w-3 text-emerald-400 animate-spin" />
                        <span className="text-[9px] font-mono text-emerald-300">
                          Server cycle running...
                        </span>
                      </>
                    ) : (
                      <span className="text-[9px] font-mono text-[#475569]">
                        Waiting...
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* Schedule progress bar */}
              {autoScheduleEnabled && autoScheduleInterval > 0 && (
                <div className="h-1 bg-[#1a1f2e] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-1000 bg-purple-500/60"
                    style={{ width: `${autoScheduleCountdown > 0 ? ((autoScheduleInterval - autoScheduleCountdown) / autoScheduleInterval) * 100 : 0}%` }}
                  />
                </div>
              )}

              {autoScheduleEnabled && autoScheduleCyclesRun > 0 && (
                <div className="text-[8px] font-mono text-[#64748b]">
                  Completed: {autoScheduleCyclesRun} cycle{autoScheduleCyclesRun !== 1 ? 's' : ''}
                  {autoScheduleTotalCycles > 0 ? ` / ${autoScheduleTotalCycles}` : ' (continuous)'}
                </div>
              )}
            </div>

            {/* Manual Cycle Triggers */}
            {!autoScheduleEnabled && !autoEvolveRunning && !autoEvoStatus?.isRunning && (
              <div className="flex items-center gap-2 flex-wrap">
                <Button
                  onClick={() => autoEvoSingleCycleMutation.mutate()}
                  disabled={autoEvoSingleCycleMutation.isPending || autoEvoFullPipelineMutation.isPending}
                  className="h-6 px-3 text-[9px] font-mono bg-indigo-600/20 text-indigo-400 border border-indigo-500/30 hover:bg-indigo-600/30"
                >
                  {autoEvoSingleCycleMutation.isPending ? (
                    <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                  ) : (
                    <Zap className="h-3 w-3 mr-1" />
                  )}
                  Run 1 Cycle
                </Button>
                <Button
                  onClick={() => autoEvoFullPipelineMutation.mutate()}
                  disabled={autoEvoFullPipelineMutation.isPending || autoEvoSingleCycleMutation.isPending}
                  className="h-6 px-3 text-[9px] font-mono bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-600/30"
                >
                  {autoEvoFullPipelineMutation.isPending ? (
                    <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                  ) : (
                    <Rocket className="h-3 w-3 mr-1" />
                  )}
                  Full Pipeline
                </Button>
                <div className="h-4 w-px bg-[#1e293b]" />
                <Button
                  onClick={() => autoEvoStartMutation.mutate()}
                  disabled={autoEvoStartMutation.isPending}
                  className="h-6 px-3 text-[9px] font-mono bg-purple-600/20 text-purple-400 border border-purple-500/30 hover:bg-purple-600/30"
                >
                  {autoEvoStartMutation.isPending ? (
                    <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                  ) : (
                    <Play className="h-3 w-3 mr-1" />
                  )}
                  Server Loop
                </Button>
                <div className="flex items-center gap-1.5 ml-1">
                  <label className="text-[8px] font-mono text-[#475569]">Cycles:</label>
                  <Input
                    type="number"
                    value={autoEvoCycles}
                    onChange={e => setAutoEvoCycles(Math.max(1, Math.min(20, Number(e.target.value))))}
                    className="h-5 w-10 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] px-1"
                    min={1}
                    max={20}
                  />
                  <label className="text-[8px] font-mono text-[#475569]">Int(s):</label>
                  <Input
                    type="number"
                    value={autoEvoInterval}
                    onChange={e => setAutoEvoInterval(Math.max(30, Number(e.target.value)))}
                    className="h-5 w-12 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#e2e8f0] px-1"
                    min={30}
                  />
                </div>
              </div>
            )}

            {/* Stop button when running */}
            {(autoEvolveRunning || autoEvoStatus?.isRunning) && !autoScheduleEnabled && (
              <Button
                onClick={() => autoEvoStopMutation.mutate()}
                disabled={autoEvoStopMutation.isPending || autoEvoStatus?.stopRequested}
                className="h-6 px-3 text-[9px] font-mono bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600/30"
              >
                {autoEvoStopMutation.isPending ? (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                ) : (
                  <X className="h-3 w-3 mr-1" />
                )}
                {autoEvoStatus?.stopRequested ? 'Stopping...' : 'Stop Server Loop'}
              </Button>
            )}

            {/* Progress & Phase Display — when any cycle is running */}
            {(autoEvoStatus?.isRunning || runNextCycleMutation.isPending) && (
              <div className="bg-[#111827] border border-purple-500/20 rounded-lg p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[9px] font-mono text-purple-300">
                    Phase: {autoEvoStatus?.currentPhase === 'BOOTSTRAP' ? '🚀 BOOTSTRAP' : autoEvoStatus?.currentPhase || (runNextCycleMutation.isPending ? 'STARTING' : 'IDLE')}
                  </span>
                  <span className="text-[9px] font-mono text-[#64748b]">
                    {autoEvoStatus?.progress || (runNextCycleMutation.isPending ? 'Starting...' : '')}
                  </span>
                </div>
                {/* Phase pipeline indicator */}
                <div className="flex items-center gap-0.5">
                  {['SCAN', 'GENERATE', 'BACKTEST', 'EVALUATE', 'SAVE', 'EVOLVE'].map((phase, idx) => {
                    const allPhases = ['SCAN', 'BOOTSTRAP', 'GENERATE', 'BACKTEST', 'EVALUATE', 'SAVE', 'EVOLVE', 'COMPLETED', 'WAITING'];
                    const currentPhase = autoEvoStatus?.currentPhase || '';
                    const currentIdx = allPhases.indexOf(currentPhase);
                    const phaseIdx = allPhases.indexOf(phase);
                    const isActive = currentPhase === phase;
                    const isDone = currentIdx > phaseIdx || currentPhase === 'COMPLETED';
                    const isBootstrap = currentPhase === 'BOOTSTRAP' && idx <= 2;
                    return (
                      <div key={phase} className="flex items-center gap-0.5 flex-1">
                        <div className="flex flex-col items-center flex-1">
                          <div className={`h-1.5 w-full rounded-full transition-all duration-300 ${
                            isActive || isBootstrap ? 'bg-purple-400 animate-pulse' :
                            isDone ? 'bg-emerald-500/60' : 'bg-[#1a1f2e]'
                          }`} />
                          <span className={`text-[6px] font-mono mt-0.5 ${
                            isActive || isBootstrap ? 'text-purple-400' :
                            isDone ? 'text-emerald-500/60' : 'text-[#2d3748]'
                          }`}>{phase.slice(0, 3)}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="grid grid-cols-4 gap-2 mt-1">
                  <div className="text-center">
                    <div className="text-[10px] font-mono font-bold text-purple-400">{autoEvoStatus?.activeStrategies?.length || 0}</div>
                    <div className="text-[7px] font-mono text-[#475569]">ACTIVE</div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] font-mono font-bold text-emerald-400">{autoEvoStatus?.totalPaperTrades || 0}</div>
                    <div className="text-[7px] font-mono text-[#475569]">TRADES</div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] font-mono font-bold text-amber-400">{autoEvoStatus?.totalExitsProcessed || 0}</div>
                    <div className="text-[7px] font-mono text-[#475569]">EXITS</div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] font-mono font-bold text-[#d4af37]">{autoEvoStatus?.totalEvolutions || 0}</div>
                    <div className="text-[7px] font-mono text-[#475569]">EVOLUTIONS</div>
                  </div>
                </div>
                {autoEvoStatus?.lastError && (
                  <div className="text-[8px] font-mono text-red-400 bg-red-500/5 px-2 py-1 rounded">
                    Error: {autoEvoStatus.lastError}
                  </div>
                )}
              </div>
            )}

            {/* Completed Cycles History — use in-memory when running, DB history when idle */}
            {(() => {
              const cycles = (autoEvoStatus?.completedCycles && autoEvoStatus.completedCycles.length > 0)
                ? autoEvoStatus.completedCycles
                : (autoEvoStatus?.dbCycleHistory || []);
              return cycles.length > 0 ? (
              <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
                <div className="flex items-center gap-1.5">
                  <Activity className="h-3 w-3 text-[#64748b]" />
                  <span className="text-[9px] font-mono text-[#94a3b8] uppercase tracking-wider">
                    {autoEvoStatus?.isRunning ? 'Cycle History' : 'Past Runs'}
                  </span>
                  {cycles.length > 1 && (
                    <Badge className="text-[7px] h-3.5 px-1 font-mono bg-purple-500/10 text-purple-400 border-0">
                      {(() => {
                        const scores = cycles.map((c: { bestScore: number }) => c.bestScore);
                        const first = scores[0] ?? 0;
                        const last = scores[scores.length - 1] ?? 0;
                        const delta = last - first;
                        return delta > 0 ? `+${delta.toFixed(1)}` : delta < 0 ? `${delta.toFixed(1)}` : '=0';
                      })()} trend
                    </Badge>
                  )}
                  <Badge className="text-[7px] h-3.5 px-1 font-mono bg-[#1a1f2e] text-[#64748b] border-0">
                    {cycles.length} cycles
                  </Badge>
                </div>
                <div className="space-y-1 max-h-40 overflow-y-auto" style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
                  {cycles.map((cycle: { cycleNumber: number; bestScore: number; improvedCount: number; degradedCount: number; totalMutations: number; strategiesActivated?: string[]; durationMs?: number; startedAt?: string; runId?: string; status?: string; currentPhase?: string; completedAt?: string | null }, idx: number) => {
                    const prevScore = idx > 0 ? cycles[idx - 1].bestScore : null;
                    const scoreDelta = prevScore !== null ? cycle.bestScore - prevScore : null;
                    const trendIcon = scoreDelta === null ? '' : scoreDelta > 0 ? '↑' : scoreDelta < 0 ? '↓' : '→';
                    const trendColor = scoreDelta === null ? '' : scoreDelta > 0 ? 'text-emerald-400' : scoreDelta < 0 ? 'text-red-400' : 'text-[#64748b]';
                    const runLabel = cycle.runId?.startsWith('single-') ? '⚡' : `C${cycle.cycleNumber}`;

                    return (
                      <div key={`cycle-${cycle.runId}-${cycle.cycleNumber}`} className="flex items-center gap-2 text-[8px] font-mono px-2 py-1 rounded bg-[#0a0e17]">
                        <span className="text-purple-400">{runLabel}</span>
                        <span className="text-[#64748b]">score:{cycle.bestScore.toFixed(1)}</span>
                        {trendIcon && (
                          <span className={trendColor}>{trendIcon}{scoreDelta !== null && scoreDelta !== 0 ? Math.abs(scoreDelta).toFixed(1) : ''}</span>
                        )}
                        <span className="text-emerald-400">+{cycle.improvedCount}</span>
                        <span className="text-red-400">-{cycle.degradedCount}</span>
                        <span className="text-[#475569]">mut:{cycle.totalMutations}</span>
                        {cycle.strategiesActivated && cycle.strategiesActivated.length > 0 && (
                          <span className="text-amber-400">★{cycle.strategiesActivated.length}</span>
                        )}
                        <span className="text-[#475569] ml-auto">{cycle.durationMs ? `${(cycle.durationMs / 1000).toFixed(1)}s` : '-'}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
              ) : null;
            })()}

            {/* Best Results from Auto-Evolution */}
            {autoEvoStatus?.bestResultsSoFar && autoEvoStatus.bestResultsSoFar.length > 0 && (
              <div className="bg-[#111827] border border-[#1e293b] rounded-lg p-3 space-y-2">
                <div className="flex items-center gap-1.5">
                  <Trophy className="h-3 w-3 text-[#d4af37]" />
                  <span className="text-[9px] font-mono text-[#d4af37] uppercase tracking-wider">Best Evolved</span>
                </div>
                <div className="space-y-1">
                  {autoEvoStatus.bestResultsSoFar.slice(0, 5).map((best, idx) => (
                    <div key={best.id} className="flex items-center gap-2 text-[8px] font-mono px-2 py-1 rounded bg-[#0a0e17]">
                      <span className="text-[#d4af37]">#{idx + 1}</span>
                      <span className="text-[#e2e8f0] truncate max-w-[150px]">{best.strategyName}</span>
                      <span className="text-[#d4af37]">{best.score.toFixed(1)}</span>
                      <span className="text-[#94a3b8]">SR:{best.sharpeRatio.toFixed(2)}</span>
                      <span className="text-[#94a3b8]">W:{(best.winRate * 100).toFixed(0)}%</span>
                      <span className={best.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                        {best.pnlPct >= 0 ? '+' : ''}{best.pnlPct.toFixed(1)}%
                      </span>
                      <span className="text-[#475569]">{best.totalTrades}t</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Idle state - no evolution running and no history */}
            {!autoEvoStatus?.isRunning && (!autoEvoStatus?.completedCycles || autoEvoStatus.completedCycles.length === 0) && (!autoEvoStatus?.dbCycleHistory || autoEvoStatus.dbCycleHistory.length === 0) && (
              <div className="flex flex-col items-center py-4 text-[#475569]">
                <Dna className="h-6 w-6 mb-1.5 text-[#2d3748]" />
                <span className="font-mono text-[10px]">Start auto-evolution to iteratively improve strategies</span>
                <span className="font-mono text-[8px] text-[#374151] mt-0.5">Each cycle: Scan → Generate → Backtest → Evaluate → Evolve</span>
                <span className="font-mono text-[8px] text-[#374151] mt-0.5">"Full Pipeline" bootstraps from scratch if no backtests exist</span>
                <span className="font-mono text-[8px] text-[#374151] mt-0.5">"Run 1 Cycle" evolves existing strategies, "Start Evolution" runs N cycles</span>
              </div>
            )}
          </div>

          {/* ============================================= */}
          {/* STEP 5: Hall of Fame with One-Click Activate */}
          {/* ============================================= */}
          <div className="bg-[#0d1117] border border-[#1e293b] rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Trophy className="h-3.5 w-3.5 text-[#d4af37]" />
                <span className="text-[11px] font-mono text-[#94a3b8] uppercase tracking-wider">Hall of Fame</span>
                <Badge className="text-[8px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#d4af37] border-[#d4af37]/30">
                  {bestStrategies.length} saved
                </Badge>
                <button
                  onClick={() => setHallOfFameCollapsed(!hallOfFameCollapsed)}
                  className="h-5 w-5 flex items-center justify-center text-[#64748b] hover:text-[#94a3b8] transition-colors"
                  title={hallOfFameCollapsed ? 'Expand Hall of Fame' : 'Collapse Hall of Fame'}
                >
                  {hallOfFameCollapsed ? <ChevronDown className="h-3 w-3" /> : <ChevronUp className="h-3 w-3" />}
                </button>
              </div>
              {/* One-Click Activate All + Auto-Evolution Controls */}
              {bestStrategies.length > 0 && (
                <div className="flex items-center gap-2">
                  <div className="flex items-center gap-1">
                    <label className="text-[8px] font-mono text-[#64748b]">Top</label>
                    <Input
                      type="number"
                      value={activateTopN}
                      onChange={e => setActivateTopN(Math.max(1, Number(e.target.value)))}
                      min={1}
                      max={bestStrategies.length}
                      className="h-5 w-10 text-[9px] font-mono bg-[#0a0e17] border-[#1e293b] text-[#d4af37] px-1"
                    />
                  </div>
                  <Button
                    onClick={() => activateAllMutation.mutate({ strategies: bestStrategies, count: activateTopN })}
                    disabled={activateAllMutation.isPending || bestStrategies.length === 0}
                    className="h-6 px-3 text-[9px] font-mono bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-600/30"
                  >
                    {activateAllMutation.isPending ? (
                      <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                    ) : (
                      <Rocket className="h-3 w-3 mr-1" />
                    )}
                    Activate & Trade Top {activateTopN}
                  </Button>
                  <Button
                    onClick={() => autoEvolutionControlMutation.mutate(autoEvolveRunning ? 'stop' : 'start')}
                    disabled={autoEvolutionControlMutation.isPending}
                    className={`h-6 px-3 text-[9px] font-mono border hover:opacity-80 ${
                      autoEvolveRunning
                        ? 'bg-red-600/20 text-red-400 border-red-500/30 hover:bg-red-600/30'
                        : 'bg-purple-600/20 text-purple-400 border-purple-500/30 hover:bg-purple-600/30'
                    }`}
                  >
                    {autoEvolutionControlMutation.isPending ? (
                      <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                    ) : autoEvolveRunning ? (
                      <Activity className="h-3 w-3 mr-1" />
                    ) : (
                      <Dna className="h-3 w-3 mr-1" />
                    )}
                    {autoEvolveRunning ? 'Stop Auto-Evo' : 'Start Auto-Evolution'}
                  </Button>
                  {autoEvoStatus && (
                    <Badge className="text-[7px] h-4 px-1.5 font-mono bg-[#1a1f2e] text-[#94a3b8] border-[#2d3748]">
                      {autoEvolveRunning
                        ? `🔄 Cycle ${autoEvoStatus.currentCycle ?? 0}/${autoEvoStatus.totalCycles ?? '?'}`
                        : 'Paused'}
                      {autoEvoStatus.totalPaperTrades > 0 && ` | Trades: ${autoEvoStatus.totalPaperTrades}`}
                    </Badge>
                  )}
                </div>
              )}
            </div>

            {bestStrategies.length === 0 ? (
              <div className="flex flex-col items-center py-6 text-[#64748b]">
                <Star className="h-8 w-8 mb-2 text-[#2d3748]" />
                <span className="font-mono text-sm">No saved strategies yet. Bookmark your best results above.</span>
              </div>
            ) : (
              <AnimatePresence>
                {!hallOfFameCollapsed && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="overflow-hidden"
                  >
                    <div className="max-h-96 overflow-y-auto" style={{ scrollbarWidth: 'thin', scrollbarColor: '#2d3748 #0a0e17' }}>
                      <div className="space-y-2">
                        {bestStrategies.map((strat, idx) => (
                          <motion.div
                            key={strat.id || `best-${idx}`}
                            initial={{ opacity: 0, x: -10 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: idx * 0.05 }}
                            className="bg-[#111827] border border-[#d4af37]/20 rounded-lg p-3"
                          >
                            <div className="flex items-center justify-between mb-2">
                              <div className="flex items-center gap-2">
                                <span className="text-[9px] font-mono text-[#d4af37]">#{idx + 1}</span>
                                <Trophy className="h-3.5 w-3.5 text-[#d4af37]" />
                                <span className="font-mono text-xs font-bold text-[#e2e8f0] max-w-[180px] truncate" title={strat.strategyName}>{strat.strategyName}</span>
                                <span
                                  className="inline-block px-1.5 py-0.5 rounded text-[7px] font-bold"
                                  style={{ backgroundColor: `${getCategoryColor(strat.category)}20`, color: getCategoryColor(strat.category) }}
                                >
                                  {formatCategory(strat.category)}
                                </span>
                                <Badge className="text-[7px] h-3.5 px-1 font-mono bg-[#1a1f2e] text-[#94a3b8] border-0">
                                  {strat.timeframe}
                                </Badge>
                                {strat.isActive && (
                                  <Badge className="text-[7px] h-3.5 px-1 font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">
                                    ACTIVE
                                  </Badge>
                                )}
                              </div>
                              <div className="flex items-center gap-1">
                                <Badge className="text-[8px] h-3.5 px-1.5 font-mono bg-[#d4af37]/15 text-[#d4af37] border border-[#d4af37]/30">
                                  Score: {strat.score.toFixed(1)}
                                </Badge>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <button
                                      onClick={() => activateMutation.mutate(strat)}
                                      disabled={activateMutation.isPending}
                                      className="h-6 w-6 flex items-center justify-center rounded transition-all bg-emerald-600/10 text-emerald-400 hover:bg-emerald-600/20 border border-emerald-500/20"
                                      title="Activate + Execute Paper Trade"
                                    >
                                      <Rocket className="h-3 w-3" />
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent>Activate & Execute Trade (Paper)</TooltipContent>
                                </Tooltip>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <button
                                      onClick={() => startTradingMutation.mutate(strat)}
                                      disabled={startTradingMutation.isPending}
                                      className="h-6 w-6 flex items-center justify-center rounded transition-all bg-cyan-600/10 text-cyan-400 hover:bg-cyan-600/20 border border-cyan-500/20"
                                      title="Start Trading (Paper)"
                                    >
                                      {startTradingMutation.isPending ? (
                                        <Loader2 className="h-3 w-3 animate-spin" />
                                      ) : (
                                        <Crosshair className="h-3 w-3" />
                                      )}
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent>Start Paper Trading</TooltipContent>
                                </Tooltip>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <button
                                      onClick={() => deleteBestMutation.mutate(strat.id)}
                                      className="h-6 w-6 flex items-center justify-center rounded transition-all bg-[#1a1f2e] text-[#64748b] hover:text-red-400 hover:bg-red-500/10 border border-[#1e293b]"
                                      title="Remove"
                                    >
                                      <Trash2 className="h-3 w-3" />
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent>Remove from Hall of Fame</TooltipContent>
                                </Tooltip>
                              </div>
                            </div>
                            <div className="grid grid-cols-4 md:grid-cols-8 gap-x-3 gap-y-0.5 text-[9px] font-mono">
                              <div>
                                <span className="text-[#64748b]">Sharpe</span>
                                <div className="text-[#e2e8f0]">{strat.sharpeRatio.toFixed(2)}</div>
                              </div>
                              <div>
                                <span className="text-[#64748b]">Win Rate</span>
                                <div className="text-[#e2e8f0]">{(strat.winRate * 100).toFixed(0)}%</div>
                              </div>
                              <div>
                                <span className="text-[#64748b]">PnL%</span>
                                <div className={strat.pnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                                  {strat.pnlPct >= 0 ? '+' : ''}{strat.pnlPct.toFixed(1)}%
                                </div>
                              </div>
                              <div>
                                <span className="text-[#64748b]">PnL$</span>
                                <div className={strat.pnlUsd >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                                  {strat.pnlUsd >= 0 ? '+' : ''}{strat.pnlUsd < 0 ? '-' : ''}${Math.abs(strat.pnlUsd).toFixed(0)}
                                </div>
                              </div>
                              <div>
                                <span className="text-[#64748b]">Max DD</span>
                                <div className="text-red-400">{strat.maxDrawdownPct.toFixed(1)}%</div>
                              </div>
                              <div>
                                <span className="text-[#64748b]">Trades</span>
                                <div className="text-[#e2e8f0]">{strat.totalTrades}</div>
                              </div>
                              <div>
                                <span className="text-[#64748b]">Allocation</span>
                                <div className="text-[#d4af37]">${strat.capitalAllocation.toFixed(0)}</div>
                              </div>
                              <div>
                                <span className="text-[#64748b]">Saved</span>
                                <div className="text-[#94a3b8]">{strat.createdAt ? new Date(strat.createdAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</div>
                              </div>
                            </div>
                          </motion.div>
                        ))}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            )}
          </div>
        </div>
      </div>

    </div>
  );
}
