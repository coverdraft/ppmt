'use client';

import { type PPMTStrategy, usePPMTStore } from '@/store/ppmt-strategy-store';
import { EquityCurveChart } from './equity-curve-chart';
import { cn } from '@/lib/utils';
import {
  ArrowLeft,
  TrendingUp,
  TrendingDown,
  Target,
  BarChart3,
  Shield,
  Zap,
  Clock,
  Trash2,
  Play,
  Loader2,
  ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  draft: { label: 'DRAFT', color: 'text-zinc-400', bg: 'bg-zinc-800' },
  backtesting: { label: 'BACKTEST', color: 'text-amber-400', bg: 'bg-amber-500/10' },
  paper_trading: { label: 'PAPER', color: 'text-cyan-400', bg: 'bg-cyan-500/10' },
  forward_testing: { label: 'FORWARD', color: 'text-orange-400', bg: 'bg-orange-500/10' },
  live: { label: 'LIVE', color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
};

const NEXT_STATUS: Record<string, string | null> = {
  draft: 'backtesting',
  backtesting: 'paper_trading',
  paper_trading: 'forward_testing',
  forward_testing: 'live',
  live: null,
};

export function StrategyDetail({ strategy, onBack }: { strategy: PPMTStrategy; onBack: () => void }) {
  const { deleteStrategy, deployStrategy, runStrategy, isRunningStrategy } = usePPMTStore();
  const isRunning = isRunningStrategy === strategy.id;
  const statusCfg = STATUS_CONFIG[strategy.status] || STATUS_CONFIG.draft;
  const nextStatus = NEXT_STATUS[strategy.status];
  const nextLabel = nextStatus ? STATUS_CONFIG[nextStatus]?.label : null;

  // Get latest run data
  const latestRun = strategy.runs?.[0];
  const equityCurve = latestRun?.equityCurve ? JSON.parse(latestRun.equityCurve) : [];
  const trades = latestRun?.tradesJson ? JSON.parse(latestRun.tradesJson) : [];

  const handleDelete = async () => {
    const success = await deleteStrategy(strategy.id);
    if (success) {
      toast.success('Strategy deleted');
      onBack();
    } else {
      toast.error('Failed to delete');
    }
  };

  const handleDeploy = async () => {
    const success = await deployStrategy(strategy.id);
    if (success) {
      toast.success(`Promoted to ${nextLabel}`);
    }
  };

  const handleRun = async () => {
    const result = await runStrategy(strategy.id, 'paper_trading');
    if (result && result.status === 'completed') {
      toast.success(`Run completed: ${result.totalTrades} trades`);
    } else if (result && result.status === 'failed') {
      toast.error('PPMT engine unavailable — check data availability');
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="p-2 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold text-zinc-50">{strategy.symbol}</h2>
              <span className="text-sm text-zinc-500 font-mono">@{strategy.timeframe}</span>
              <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded-md', statusCfg.bg, statusCfg.color)}>
                {statusCfg.label}
              </span>
            </div>
            <span className="text-xs text-zinc-500 capitalize">{strategy.assetClass.replace('_', ' ')} · α={strategy.saxAlpha} W={strategy.saxWindow}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDelete}
            className="text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
          {(strategy.status === 'draft' || strategy.totalTrades === 0) && (
            <Button
              size="sm"
              onClick={handleRun}
              disabled={isRunning}
              variant="outline"
              className="border-zinc-700 text-zinc-300 hover:bg-zinc-800"
            >
              {isRunning ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
              Run Backtest
            </Button>
          )}
          {nextStatus && (
            <Button
              size="sm"
              onClick={handleDeploy}
              disabled={isRunning}
              className="bg-emerald-600 hover:bg-emerald-500 text-white gap-1"
            >
              Deploy to {nextLabel}
              <ChevronRight className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <DetailMetric icon={<TrendingUp className="h-4 w-4" />} label="PnL" value={`${strategy.totalPnlPct >= 0 ? '+' : ''}${strategy.totalPnlPct.toFixed(2)}%`} color={strategy.totalPnlPct >= 0 ? 'text-emerald-400' : 'text-rose-400'} />
        <DetailMetric icon={<Target className="h-4 w-4" />} label="Win Rate" value={`${(strategy.winRate * 100).toFixed(1)}%`} color={strategy.winRate >= 0.5 ? 'text-emerald-400' : 'text-rose-400'} />
        <DetailMetric icon={<BarChart3 className="h-4 w-4" />} label="Sharpe" value={strategy.sharpeRatio.toFixed(2)} color="text-cyan-400" />
        <DetailMetric icon={<Shield className="h-4 w-4" />} label="Max DD" value={`${(strategy.maxDrawdown * 100).toFixed(1)}%`} color="text-rose-400" />
        <DetailMetric icon={<Zap className="h-4 w-4" />} label="Trades" value={String(strategy.totalTrades)} color="text-zinc-300" />
      </div>

      {/* Equity curve */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">Equity Curve</h3>
        <EquityCurveChart equityCurve={equityCurve} capital={strategy.initialCapital} />
      </div>

      {/* Config & Run History */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Configuration */}
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <h3 className="text-sm font-medium text-zinc-300 mb-3">Configuration</h3>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <ConfigRow label="Initial Capital" value={`$${strategy.initialCapital.toLocaleString()}`} />
            <ConfigRow label="SAX α/W" value={`${strategy.saxAlpha} / ${strategy.saxWindow}`} />
            <ConfigRow label="Pattern Length" value={String(strategy.patternLength)} />
            <ConfigRow label="Min Confidence" value={`${(strategy.minConfidence * 100).toFixed(0)}%`} />
            <ConfigRow label="Catastrophic Loss" value={`${strategy.catastrophicLossPct}%`} />
            <ConfigRow label="Fuzzy Threshold" value={strategy.fuzzyThreshold.toFixed(2)} />
            <ConfigRow label="Living Trie" value={strategy.livingTrie ? 'ON' : 'OFF'} />
            <ConfigRow label="Regime Aware" value={strategy.regimeAware ? 'ON' : 'OFF'} />
            <ConfigRow label="Pruning Interval" value={String(strategy.pruningInterval)} />
            <ConfigRow label="Recalibration" value={String(strategy.recalibrationInterval)} />
          </div>
        </div>

        {/* Run History */}
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <h3 className="text-sm font-medium text-zinc-300 mb-3">Run History</h3>
          {strategy.runs.length === 0 ? (
            <p className="text-sm text-zinc-600">No runs yet</p>
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {strategy.runs.map((run) => (
                <div key={run.id} className="flex items-center justify-between py-2 border-b border-zinc-800 last:border-0">
                  <div className="flex items-center gap-2">
                    <span className={cn(
                      'text-[10px] font-bold px-1.5 py-0.5 rounded',
                      run.status === 'completed' ? 'bg-emerald-500/10 text-emerald-400' :
                      run.status === 'failed' ? 'bg-rose-500/10 text-rose-400' :
                      'bg-amber-500/10 text-amber-400'
                    )}>
                      {run.status.toUpperCase()}
                    </span>
                    <span className="text-xs text-zinc-400 capitalize">{run.runType.replace('_', ' ')}</span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-zinc-500">
                    <span>{run.totalTrades} trades</span>
                    <span className={run.totalPnlPct >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                      {run.totalPnlPct >= 0 ? '+' : ''}{run.totalPnlPct.toFixed(1)}%
                    </span>
                    <Clock className="h-3 w-3" />
                    <span>{run.completedAt ? new Date(run.completedAt).toLocaleDateString() : '—'}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Recent trades */}
      {trades.length > 0 && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4">
          <h3 className="text-sm font-medium text-zinc-300 mb-3">Recent Trades ({trades.length})</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-zinc-500 border-b border-zinc-800">
                  <th className="text-left py-2 px-2">#</th>
                  <th className="text-left py-2 px-2">Dir</th>
                  <th className="text-right py-2 px-2">Entry</th>
                  <th className="text-right py-2 px-2">Exit</th>
                  <th className="text-right py-2 px-2">PnL%</th>
                  <th className="text-left py-2 px-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 20).map((t: Record<string, unknown>, i: number) => (
                  <tr key={i} className="border-b border-zinc-800/50">
                    <td className="py-1.5 px-2 text-zinc-500">{String(t.id || i + 1)}</td>
                    <td className={cn('py-1.5 px-2 font-medium', t.dir === 'LONG' ? 'text-emerald-400' : 'text-rose-400')}>
                      {String(t.dir)}
                    </td>
                    <td className="py-1.5 px-2 text-right text-zinc-300 tabular-nums">{Number(t.entry).toFixed(2)}</td>
                    <td className="py-1.5 px-2 text-right text-zinc-300 tabular-nums">{Number(t.exit).toFixed(2)}</td>
                    <td className={cn(
                      'py-1.5 px-2 text-right tabular-nums',
                      Number(t.pnlPct) >= 0 ? 'text-emerald-400' : 'text-rose-400'
                    )}>
                      {Number(t.pnlPct) >= 0 ? '+' : ''}{Number(t.pnlPct).toFixed(2)}%
                    </td>
                    <td className="py-1.5 px-2 text-zinc-500 text-xs">{String(t.exitReason || '—')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function DetailMetric({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: string; color: string }) {
  return (
    <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-3">
      <div className="flex items-center gap-1.5 mb-1">
        <span className="text-zinc-500">{icon}</span>
        <span className="text-[10px] text-zinc-500 uppercase tracking-wider">{label}</span>
      </div>
      <div className={cn('text-lg font-bold tabular-nums', color)}>{value}</div>
    </div>
  );
}

function ConfigRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-zinc-500 text-xs">{label}</span>
      <span className="text-zinc-300 text-xs font-mono">{value}</span>
    </div>
  );
}
