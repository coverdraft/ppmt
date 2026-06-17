'use client';

import { usePPMTStore, type PPMTStrategy, type StrategyStatus } from '@/store/ppmt-strategy-store';
import { cn } from '@/lib/utils';
import { motion } from 'framer-motion';
import {
  TrendingUp,
  TrendingDown,
  Play,
  ChevronRight,
  Loader2,
  Clock,
  BarChart3,
} from 'lucide-react';
import { toast } from 'sonner';

const STATUS_CONFIG: Record<StrategyStatus, { label: string; color: string; bg: string; border: string }> = {
  draft: { label: 'DRAFT', color: 'text-zinc-400', bg: 'bg-zinc-800', border: 'border-zinc-700' },
  backtesting: { label: 'BACKTEST', color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20' },
  paper_trading: { label: 'PAPER', color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20' },
  forward_testing: { label: 'FORWARD', color: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/20' },
  live: { label: 'LIVE', color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20' },
};

const ASSET_CLASS_COLORS: Record<string, string> = {
  blue_chip: 'bg-blue-500/20 text-blue-300',
  large_cap: 'bg-violet-500/20 text-violet-300',
  defi: 'bg-pink-500/20 text-pink-300',
  meme: 'bg-amber-500/20 text-amber-300',
  new_launch: 'bg-rose-500/20 text-rose-300',
};

const NEXT_STATUS: Record<StrategyStatus, StrategyStatus | null> = {
  draft: 'backtesting',
  backtesting: 'paper_trading',
  paper_trading: 'forward_testing',
  forward_testing: 'live',
  live: null,
};

export function StrategyCard({ strategy }: { strategy: PPMTStrategy }) {
  const { selectStrategy, deployStrategy, runStrategy, isRunningStrategy } = usePPMTStore();
  const statusCfg = STATUS_CONFIG[strategy.status];
  const nextStatus = NEXT_STATUS[strategy.status];
  const isRunning = isRunningStrategy === strategy.id;
  const isProfitable = strategy.totalPnlPct > 0;
  const hasResults = strategy.totalTrades > 0;

  const handleDeploy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const success = await deployStrategy(strategy.id);
    if (success) {
      toast.success(`Strategy promoted to ${STATUS_CONFIG[nextStatus!]?.label || 'next stage'}`);
    } else {
      toast.error('Failed to promote strategy');
    }
  };

  const handleRun = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const result = await runStrategy(strategy.id, strategy.status === 'draft' ? 'backtest' : 'paper_trading');
    if (result && result.status === 'completed') {
      toast.success(`Run completed: ${result.totalTrades} trades`);
    } else if (result && result.status === 'failed') {
      toast.error('PPMT engine unavailable — ensure data exists for this token');
    }
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ scale: 1.01 }}
      onClick={() => selectStrategy(strategy.id)}
      className={cn(
        'bg-zinc-900 rounded-xl border p-4 cursor-pointer transition-all duration-200',
        'hover:border-zinc-600 hover:shadow-lg hover:shadow-black/20',
        statusCfg.border
      )}
    >
      {/* Header row */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-base font-bold text-zinc-50">{strategy.symbol}</span>
          <span className="text-xs text-zinc-500 font-mono">@{strategy.timeframe}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded-md', statusCfg.bg, statusCfg.color)}>
            {statusCfg.label}
          </span>
          <span className={cn('text-[10px] px-1.5 py-0.5 rounded', ASSET_CLASS_COLORS[strategy.assetClass] || 'bg-zinc-800 text-zinc-400')}>
            {strategy.assetClass.replace('_', ' ')}
          </span>
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-4 gap-3 mb-3">
        <MetricBlock
          label="PnL"
          value={hasResults ? `${strategy.totalPnlPct >= 0 ? '+' : ''}${strategy.totalPnlPct.toFixed(1)}%` : '—'}
          valueColor={hasResults ? (isProfitable ? 'text-emerald-400' : 'text-rose-400') : 'text-zinc-600'}
          icon={isProfitable ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
        />
        <MetricBlock
          label="Win Rate"
          value={hasResults ? `${(strategy.winRate * 100).toFixed(0)}%` : '—'}
          valueColor={hasResults ? (strategy.winRate >= 0.5 ? 'text-emerald-400' : 'text-rose-400') : 'text-zinc-600'}
        />
        <MetricBlock
          label="Sharpe"
          value={hasResults ? strategy.sharpeRatio.toFixed(2) : '—'}
          valueColor={hasResults ? (strategy.sharpeRatio >= 1 ? 'text-emerald-400' : 'text-zinc-300') : 'text-zinc-600'}
        />
        <MetricBlock
          label="Trades"
          value={hasResults ? String(strategy.totalTrades) : '—'}
          valueColor="text-zinc-300"
        />
      </div>

      {/* Secondary metrics */}
      <div className="flex items-center gap-4 text-[11px] text-zinc-500 mb-3">
        <span>DD: {hasResults ? `${(strategy.maxDrawdown * 100).toFixed(1)}%` : '—'}</span>
        <span>PF: {hasResults ? strategy.profitFactor.toFixed(2) : '—'}</span>
        <span>Patterns: {strategy.patternCount.toLocaleString()}</span>
        <span>α/W: {strategy.saxAlpha}/{strategy.saxWindow}</span>
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-[11px] text-zinc-500">
          <Clock className="h-3 w-3" />
          <span>{strategy.lastRunAt ? new Date(strategy.lastRunAt).toLocaleDateString() : 'Never run'}</span>
        </div>
        <div className="flex items-center gap-2">
          {(strategy.status === 'draft' || strategy.status === 'backtesting') && (
            <button
              onClick={handleRun}
              disabled={isRunning}
              className={cn(
                'flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg transition-colors',
                'bg-zinc-800 text-zinc-300 hover:bg-zinc-700 hover:text-zinc-100',
                'disabled:opacity-50 disabled:cursor-not-allowed'
              )}
            >
              {isRunning ? <Loader2 className="h-3 w-3 animate-spin" /> : <BarChart3 className="h-3 w-3" />}
              Run
            </button>
          )}
          {nextStatus && (
            <button
              onClick={handleDeploy}
              disabled={isRunning}
              className={cn(
                'flex items-center gap-1 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors',
                'bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 hover:text-emerald-300',
                'disabled:opacity-50 disabled:cursor-not-allowed'
              )}
            >
              Deploy
              <ChevronRight className="h-3 w-3" />
            </button>
          )}
          {strategy.status === 'live' && (
            <span className="flex items-center gap-1 text-xs text-emerald-400">
              <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
              Active
            </span>
          )}
        </div>
      </div>
    </motion.div>
  );
}

function MetricBlock({
  label,
  value,
  valueColor,
  icon,
}: {
  label: string;
  value: string;
  valueColor: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider mb-0.5">{label}</span>
      <div className={cn('flex items-center gap-1 text-sm font-semibold tabular-nums', valueColor)}>
        {icon}
        {value}
      </div>
    </div>
  );
}
